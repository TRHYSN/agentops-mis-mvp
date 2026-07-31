import { spawn, spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const FREE_LOCAL_DEPLOYMENT_MODES = new Set(["local", "free_local", "development"]);
const PRODUCTION_DEPLOYMENT_MODES = new Set(["production", "prod", "shared", "hosted"]);
const LOOPBACK_HOSTS = new Set(["127.0.0.1", "::1", "localhost"]);

class StartBoundaryError extends Error {
  constructor(code) {
    super(code);
    this.name = "StartBoundaryError";
    this.code = code;
  }
}

function writeStartFailure(errorCode) {
  console.error(JSON.stringify({
    contract: "nextjs_start_boundary_v2",
    ok: false,
    error_code: errorCode,
    credentials_omitted: true,
    sql_omitted: true,
    row_data_omitted: true,
  }));
}

function normalized(value) {
  return String(value || "").trim().toLowerCase();
}

function productionDeployment() {
  const configured = normalized(process.env.AGENTOPS_DEPLOYMENT_MODE);
  if (PRODUCTION_DEPLOYMENT_MODES.has(configured)) return true;
  if (FREE_LOCAL_DEPLOYMENT_MODES.has(configured)) return false;
  if (configured) {
    throw new StartBoundaryError("deployment_mode_invalid");
  }
  return normalized(process.env.NODE_ENV) === "production";
}

function startConfiguration() {
  const production = productionDeployment();
  const configuredHost = String(process.env.AGENTOPS_NEXT_HOST || "").trim();
  if (production && !configuredHost) {
    throw new StartBoundaryError("production_host_required");
  }
  const host = configuredHost || "127.0.0.1";
  if (!production && !LOOPBACK_HOSTS.has(normalized(host))) {
    throw new StartBoundaryError("free_local_host_not_loopback");
  }

  const portText = String(process.env.AGENTOPS_NEXT_PORT || process.env.PORT || "3001").trim();
  const port = Number(portText);
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new StartBoundaryError("next_port_invalid");
  }
  return { host, port, production };
}

function schemaReadiness() {
  const tsxCli = fileURLToPath(new URL("../node_modules/tsx/dist/cli.mjs", import.meta.url));
  const runner = fileURLToPath(new URL("./migrate-postgres.ts", import.meta.url));
  const result = spawnSync(process.execPath, [tsxCli, runner, "--check"], {
    encoding: "utf8",
    env: process.env,
    maxBuffer: 64 * 1024,
  });
  const output = String(result.stdout || "").trim();
  if (result.status !== 0) {
    if (output) process.stderr.write(`${output}\n`);
    else {
      process.stderr.write(
        `${JSON.stringify({
          contract: "agentops_postgres_schema_readiness_v1",
          ok: false,
          error_code: "schema_readiness_process_failed",
          credentials_omitted: true,
          sql_omitted: true,
          row_data_omitted: true,
        })}\n`,
      );
    }
    process.exit(1);
  }
  try {
    const receipt = JSON.parse(output);
    if (
      receipt?.contract !== "agentops_postgres_schema_readiness_v1"
      || receipt?.ok !== true
      || receipt?.operation !== "check"
      || receipt?.database_role_boundary_verified !== true
    ) {
      throw new Error("invalid schema receipt");
    }
    return receipt;
  } catch {
    process.stderr.write(
      `${JSON.stringify({
        contract: "agentops_postgres_schema_readiness_v1",
        ok: false,
        error_code: "schema_readiness_receipt_invalid",
        credentials_omitted: true,
        sql_omitted: true,
        row_data_omitted: true,
      })}\n`,
    );
    process.exit(1);
  }
}

let configuration;
try {
  configuration = startConfiguration();
} catch (error) {
  writeStartFailure(
    error instanceof StartBoundaryError ? error.code : "start_configuration_failed",
  );
  process.exit(1);
}

const schema = configuration.production ? schemaReadiness() : null;

if (process.argv.includes("--check")) {
  console.log(JSON.stringify({
    contract: "nextjs_start_boundary_v2",
    ok: true,
    deployment: configuration.production ? "production" : "free_local",
    host: configuration.host,
    port: configuration.port,
    free_local_loopback_only: !configuration.production,
    schema_required: configuration.production,
    schema_ready: configuration.production ? schema?.ok === true : null,
    schema_contract: configuration.production ? schema?.schema_contract : null,
    schema_fingerprint_verified: configuration.production
      ? schema?.schema_fingerprint_verified === true
      : null,
    database_role_boundary_verified: configuration.production
      ? schema?.database_role_boundary_verified === true
      : null,
    production_python_fallback: false,
    credentials_omitted: true,
  }));
  process.exit(0);
}

const nextCli = fileURLToPath(new URL("../node_modules/next/dist/bin/next", import.meta.url));
const child = spawn(
  process.execPath,
  [nextCli, "start", "-H", configuration.host, "-p", String(configuration.port)],
  { env: process.env, stdio: "inherit" },
);

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => {
    if (!child.killed) child.kill(signal);
  });
}

child.on("error", (error) => {
  void error;
  writeStartFailure("next_process_spawn_failed");
  process.exitCode = 1;
});
child.on("exit", (code, signal) => {
  process.exitCode = code ?? (signal ? 1 : 0);
});
