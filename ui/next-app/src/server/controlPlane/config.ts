import {
  closeSync,
  constants,
  fstatSync,
  openSync,
  readFileSync,
} from "node:fs";
import { isAbsolute } from "node:path";

export type ControlPlaneMode = "proxy" | "postgres";

const FREE_LOCAL_DEPLOYMENT_MODES = new Set(["local", "free_local", "development"]);
const PRODUCTION_DEPLOYMENT_MODES = new Set(["production", "prod", "shared", "hosted"]);
const MAX_SECRET_FILE_BYTES = 16 * 1024;

function normalized(value: string | undefined) {
  return String(value || "").trim().toLowerCase();
}

export function controlPlaneMode(): ControlPlaneMode {
  const configured = normalized(
    process.env.AGENTOPS_CONTROL_PLANE_MODE || process.env.AGENTOPS_TS_CONTROL_PLANE_MODE,
  );
  if (configured === "postgres") return "postgres";
  if (configured === "proxy") return isProductionDeployment() ? "postgres" : "proxy";
  if (configured) {
    throw new Error("AGENTOPS_CONTROL_PLANE_MODE must be postgres or proxy.");
  }
  return isProductionDeployment() ? "postgres" : "proxy";
}

export function isProductionDeployment() {
  const configured = normalized(process.env.AGENTOPS_DEPLOYMENT_MODE);
  if (PRODUCTION_DEPLOYMENT_MODES.has(configured)) return true;
  if (FREE_LOCAL_DEPLOYMENT_MODES.has(configured)) return false;
  if (configured) {
    throw new Error(
      "AGENTOPS_DEPLOYMENT_MODE must be production, prod, shared, hosted, local, free_local, or development.",
    );
  }
  return normalized(process.env.NODE_ENV) === "production";
}

export function legacyPythonProxyAllowed() {
  return !isProductionDeployment() && controlPlaneMode() === "proxy";
}

export function secretEnvironmentValue(name: string) {
  const direct = String(process.env[name] || "");
  const filePath = String(process.env[`${name}_FILE`] || "").trim();
  if (direct && filePath) {
    throw new Error(`${name} and ${name}_FILE are mutually exclusive.`);
  }
  if (!filePath) return direct;
  if (!isAbsolute(filePath)) {
    throw new Error(`${name}_FILE must be an absolute path.`);
  }
  let descriptor: number | undefined;
  try {
    descriptor = openSync(
      filePath,
      constants.O_RDONLY | (constants.O_NOFOLLOW || 0),
    );
    const metadata = fstatSync(descriptor);
    if (
      !metadata.isFile()
      || metadata.size < 1
      || metadata.size > MAX_SECRET_FILE_BYTES
    ) {
      throw new Error("secret_file_shape_invalid");
    }
    const value = readFileSync(descriptor, "utf8").replace(/\r?\n$/, "");
    if (!value || value.includes("\0")) {
      throw new Error("secret_file_content_invalid");
    }
    return value;
  } catch {
    throw new Error(`${name}_FILE could not be read as a bounded regular file.`);
  } finally {
    if (descriptor !== undefined) closeSync(descriptor);
  }
}

export function postgresDsn() {
  const dsnFamilyConfigured = Boolean(
    String(process.env.AGENTOPS_POSTGRES_DSN || "")
    || String(process.env.AGENTOPS_POSTGRES_DSN_FILE || "").trim()
  );
  const componentFamilyConfigured = [
    "AGENTOPS_POSTGRES_HOST",
    "AGENTOPS_POSTGRES_PORT",
    "AGENTOPS_POSTGRES_DATABASE",
    "AGENTOPS_POSTGRES_USER",
    "AGENTOPS_POSTGRES_PASSWORD",
    "AGENTOPS_POSTGRES_PASSWORD_FILE",
  ].some((name) => Boolean(String(process.env[name] || "").trim()));
  if (dsnFamilyConfigured && componentFamilyConfigured) {
    throw new Error(
      "Postgres DSN and component configuration families are mutually exclusive.",
    );
  }
  const configuredDsn = secretEnvironmentValue("AGENTOPS_POSTGRES_DSN").trim();
  if (configuredDsn) return configuredDsn;

  const host = String(process.env.AGENTOPS_POSTGRES_HOST || "").trim();
  const database = String(process.env.AGENTOPS_POSTGRES_DATABASE || "").trim();
  const user = String(process.env.AGENTOPS_POSTGRES_USER || "").trim();
  const password = secretEnvironmentValue("AGENTOPS_POSTGRES_PASSWORD");
  const port = Number(process.env.AGENTOPS_POSTGRES_PORT || 5432);
  if (
    !/^[A-Za-z0-9.-]+$/.test(host)
    || !/^[A-Za-z_][A-Za-z0-9_.-]{0,62}$/.test(database)
    || !/^[A-Za-z_][A-Za-z0-9_.-]{0,62}$/.test(user)
    || !password
    || !Number.isInteger(port)
    || port < 1
    || port > 65535
  ) {
    throw new Error(
      "AGENTOPS_POSTGRES_DSN(_FILE) or valid Postgres host/database/user/password-file settings are required.",
    );
  }
  return `postgresql://${encodeURIComponent(user)}:${encodeURIComponent(password)}`
    + `@${host}:${port}/${encodeURIComponent(database)}`;
}

export function proxyBaseUrl() {
  return String(process.env.AGENTOPS_API_BASE || "http://127.0.0.1:8765/api").replace(/\/$/, "");
}

export function postgresSslEnabled() {
  return ["1", "true", "require", "required", "on"].includes(normalized(process.env.AGENTOPS_POSTGRES_SSL));
}

export function postgresApplicationName() {
  const configured = String(
    process.env.AGENTOPS_POSTGRES_APPLICATION_NAME || "",
  ).trim();
  if (!configured) return "agentops-mis-typescript-control-plane";
  if (!/^[A-Za-z0-9_.:-]{1,63}$/.test(configured)) {
    throw new Error(
      "AGENTOPS_POSTGRES_APPLICATION_NAME must be a safe 1-63 character identifier.",
    );
  }
  return configured;
}
