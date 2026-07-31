import assert from "node:assert/strict";
import { randomBytes } from "node:crypto";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";

import {
  POSTGRES_MIGRATION_MANIFEST,
  SCHEMA_CONTRACT,
} from "../src/server/controlPlane/schemaReadiness";
import {
  createPostgresRoleBoundaryFixture,
  type PostgresRoleBoundaryFixture,
} from "./postgres-role-boundary-test-helper";

const baseDsn = String(process.env.AGENTOPS_POSTGRES_DSN || "").trim();
assert.ok(baseDsn, "AGENTOPS_POSTGRES_DSN is required");

const scriptPath = fileURLToPath(
  new URL("./bootstrap-owner.ts", import.meta.url),
);
const appRoot = path.resolve(path.dirname(scriptPath), "..");
const tsxPath = path.join(appRoot, "node_modules", "tsx", "dist", "cli.mjs");

function runBootstrap(
  dsn: string,
  args: string[],
  password = "",
  boundary?: PostgresRoleBoundaryFixture,
): Promise<{
  code: number;
  stdout: string;
  stderr: string;
  payload: Record<string, unknown>;
}> {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [tsxPath, scriptPath, ...args], {
      cwd: appRoot,
      env: {
        ...process.env,
        AGENTOPS_DEPLOYMENT_MODE: "production",
        AGENTOPS_CONTROL_PLANE_MODE: "postgres",
        AGENTOPS_POSTGRES_DSN: dsn,
        ...(boundary
          ? {
              AGENTOPS_POSTGRES_SCHEMA: boundary.applicationSchema,
              AGENTOPS_POSTGRES_RUNTIME_API_SCHEMA:
                boundary.runtimeApiSchema,
              AGENTOPS_POSTGRES_RUNTIME_ROLE: boundary.runtimeRole,
            }
          : {}),
      },
      stdio: ["pipe", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk: string) => {
      stdout += chunk;
    });
    child.stderr.on("data", (chunk: string) => {
      stderr += chunk;
    });
    child.on("error", reject);
    child.on("close", (code) => {
      let payload: Record<string, unknown> = {};
      try {
        payload = JSON.parse(stdout.trim() || "{}");
      } catch {
        // Assertions below report the bounded output without secrets.
      }
      resolve({ code: code ?? -1, stdout, stderr, payload });
    });
    child.stdin.end(password ? `${password}\n` : "");
  });
}

async function main() {
  const roleFixture = await createPostgresRoleBoundaryFixture(
    baseDsn,
    "owner_bootstrap",
  );
  try {
    const dsn = roleFixture.runtimeDsn;
    const migration = roleFixture.migration;
    assert.equal(migration.schema_contract, SCHEMA_CONTRACT);
    assert.equal(migration.applied_count, POSTGRES_MIGRATION_MANIFEST.length);

    const forbiddenArg = await runBootstrap(dsn, [
      "--workspace-id",
      "ws_bootstrap_contract",
      "--username",
      "bootstrap-owner",
      "--password=forbidden-value",
    ], "", roleFixture);
    assert.equal(forbiddenArg.code, 2);
    assert.equal(forbiddenArg.payload.error, "password_argv_forbidden");

    const weakPassword = await runBootstrap(
      dsn,
      [
        "--workspace-id",
        "ws_bootstrap_contract",
        "--username",
        "bootstrap-owner",
        "--password-stdin",
      ],
      "too-short",
      roleFixture,
    );
    assert.equal(weakPassword.code, 2);
    assert.equal(weakPassword.payload.error, "password_length_invalid");

    const password = `Owner-${randomBytes(24).toString("base64url")}Aa1!`;
    const created = await runBootstrap(
      dsn,
      [
        "--workspace-id",
        "ws_bootstrap_contract",
        "--username",
        "bootstrap-owner",
        "--display-name",
        "Bootstrap Owner",
        "--password-stdin",
      ],
      password,
      roleFixture,
    );
    assert.equal(created.code, 0, created.stderr || created.stdout);
    assert.equal(created.payload.ok, true);
    assert.equal(created.payload.operation, "commercial_owner_bootstrap");
    assert.equal(created.payload.schema_contract, SCHEMA_CONTRACT);
    assert.equal(created.payload.password_omitted, true);
    assert.equal(created.payload.python_started, false);

    const rows = await roleFixture.owner.query<{
        user_id: string;
        role: string;
        username: string;
        password_hash: string;
        password_salt: string;
        password_params_json: string;
        audits: string;
      }>(
        `SELECT membership.user_id,membership.role,credential.username,
          credential.password_hash,credential.password_salt,
          credential.password_params_json,
          (SELECT COUNT(*)::text FROM audit_logs
            WHERE workspace_id='ws_bootstrap_contract'
              AND action='human_auth.owner_bootstrap') AS audits
        FROM workspace_memberships membership
        JOIN human_login_credentials credential
          ON credential.user_id=membership.user_id
        WHERE membership.workspace_id='ws_bootstrap_contract'`,
    );
    assert.equal(rows.rowCount, 1);
    assert.equal(rows.rows[0].role, "owner");
    assert.equal(rows.rows[0].username, "bootstrap-owner");
    assert.match(rows.rows[0].password_hash, /^[a-f0-9]{64}$/);
    assert.match(rows.rows[0].password_salt, /^[a-f0-9]{32}$/);
    assert.equal(Number(rows.rows[0].audits), 1);
    assert.equal(JSON.stringify(rows.rows).includes(password), false);

    const duplicate = await runBootstrap(
      dsn,
      [
        "--workspace-id",
        "ws_bootstrap_contract",
        "--username",
        "second-owner",
        "--password-stdin",
      ],
      password,
      roleFixture,
    );
    assert.equal(duplicate.code, 2);
    assert.equal(duplicate.payload.error, "owner_already_initialized");

    const output = JSON.stringify({
      contract: "commercial_owner_bootstrap_postgres_v1",
      ok: true,
      schema_contract: SCHEMA_CONTRACT,
      schema_fresh: true,
      password_argv_rejected: true,
      weak_password_rejected: true,
      owner_created_once: true,
      duplicate_owner_rejected: true,
      scrypt_hash_only: true,
      audit_written: true,
      restricted_runtime_verified: true,
      migrator_runtime_distinct: true,
      credentials_omitted: true,
      python_started: false,
    });
    assert.equal(output.includes(password), false);
    assert.equal(output.includes(baseDsn), false);
    process.stdout.write(`${output}\n`);
  } finally {
    await roleFixture.cleanup();
  }
}

await main();
