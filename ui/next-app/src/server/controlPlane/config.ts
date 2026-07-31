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

type PostgresDsnFamily = Readonly<{
  dsn: string;
  host: string;
  port: string;
  database: string;
  user: string;
  password: string;
}>;

function postgresDsnFromFamily(family: PostgresDsnFamily) {
  const dsnFamilyConfigured = Boolean(
    String(process.env[family.dsn] || "")
    || String(process.env[`${family.dsn}_FILE`] || "").trim()
  );
  const componentFamilyConfigured = [
    family.host,
    family.port,
    family.database,
    family.user,
    family.password,
    `${family.password}_FILE`,
  ].some((name) => Boolean(String(process.env[name] || "").trim()));
  if (dsnFamilyConfigured && componentFamilyConfigured) {
    throw new Error(
      `${family.dsn} and its component configuration families are mutually exclusive.`,
    );
  }
  const configuredDsn = secretEnvironmentValue(family.dsn).trim();
  if (configuredDsn) return configuredDsn;

  const host = String(process.env[family.host] || "").trim();
  const database = String(process.env[family.database] || "").trim();
  const user = String(process.env[family.user] || "").trim();
  const password = secretEnvironmentValue(family.password);
  const port = Number(process.env[family.port] || 5432);
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
      `${family.dsn}(_FILE) or valid Postgres host/database/user/password-file settings are required.`,
    );
  }
  return `postgresql://${encodeURIComponent(user)}:${encodeURIComponent(password)}`
    + `@${host}:${port}/${encodeURIComponent(database)}`;
}

const RUNTIME_POSTGRES_FAMILY = Object.freeze({
  dsn: "AGENTOPS_POSTGRES_DSN",
  host: "AGENTOPS_POSTGRES_HOST",
  port: "AGENTOPS_POSTGRES_PORT",
  database: "AGENTOPS_POSTGRES_DATABASE",
  user: "AGENTOPS_POSTGRES_USER",
  password: "AGENTOPS_POSTGRES_PASSWORD",
});

const MIGRATOR_POSTGRES_FAMILY = Object.freeze({
  dsn: "AGENTOPS_POSTGRES_MIGRATOR_DSN",
  host: "AGENTOPS_POSTGRES_MIGRATOR_HOST",
  port: "AGENTOPS_POSTGRES_MIGRATOR_PORT",
  database: "AGENTOPS_POSTGRES_MIGRATOR_DATABASE",
  user: "AGENTOPS_POSTGRES_MIGRATOR_USER",
  password: "AGENTOPS_POSTGRES_MIGRATOR_PASSWORD",
});

const ENTITLEMENT_ADMIN_POSTGRES_FAMILY = Object.freeze({
  dsn: "AGENTOPS_POSTGRES_ENTITLEMENT_ADMIN_DSN",
  host: "AGENTOPS_POSTGRES_ENTITLEMENT_ADMIN_HOST",
  port: "AGENTOPS_POSTGRES_ENTITLEMENT_ADMIN_PORT",
  database: "AGENTOPS_POSTGRES_ENTITLEMENT_ADMIN_DATABASE",
  user: "AGENTOPS_POSTGRES_ENTITLEMENT_ADMIN_USER",
  password: "AGENTOPS_POSTGRES_ENTITLEMENT_ADMIN_PASSWORD",
});

export function postgresDsn() {
  return postgresDsnFromFamily(RUNTIME_POSTGRES_FAMILY);
}

export function postgresMigratorDsn() {
  try {
    return postgresDsnFromFamily(MIGRATOR_POSTGRES_FAMILY);
  } catch (error) {
    if (!isProductionDeployment()) return postgresDsn();
    throw error;
  }
}

export function postgresEntitlementAdminDsn() {
  return postgresDsnFromFamily(ENTITLEMENT_ADMIN_POSTGRES_FAMILY);
}

function safePostgresIdentifier(name: string, fallback: string) {
  const value = String(process.env[name] || fallback).trim();
  if (!/^[A-Za-z_][A-Za-z0-9_]{0,62}$/.test(value)) {
    throw new Error(`${name} must be a safe PostgreSQL identifier.`);
  }
  return value;
}

export function postgresApplicationSchema() {
  return safePostgresIdentifier("AGENTOPS_POSTGRES_SCHEMA", "public");
}

export function postgresRuntimeApiSchema() {
  return safePostgresIdentifier(
    "AGENTOPS_POSTGRES_RUNTIME_API_SCHEMA",
    "agentops_runtime_api",
  );
}

export function postgresRuntimeRole(required = isProductionDeployment()) {
  const configured = String(
    process.env.AGENTOPS_POSTGRES_RUNTIME_ROLE || "",
  ).trim();
  if (!configured && !required) return "";
  if (!/^[A-Za-z_][A-Za-z0-9_]{0,62}$/.test(configured)) {
    throw new Error(
      "AGENTOPS_POSTGRES_RUNTIME_ROLE must be a safe PostgreSQL identifier.",
    );
  }
  return configured;
}

export function postgresEntitlementAdminRole(
  required = isProductionDeployment(),
) {
  const configured = String(
    process.env.AGENTOPS_POSTGRES_ENTITLEMENT_ADMIN_ROLE || "",
  ).trim();
  if (!configured && !required) return "";
  if (!/^[A-Za-z_][A-Za-z0-9_]{0,62}$/.test(configured)) {
    throw new Error(
      "AGENTOPS_POSTGRES_ENTITLEMENT_ADMIN_ROLE must be a safe PostgreSQL identifier.",
    );
  }
  return configured;
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
