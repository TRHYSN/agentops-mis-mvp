import assert from "node:assert/strict";
import {
  mkdtemp,
  rm,
  symlink,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import {
  postgresDsn,
  secretEnvironmentValue,
} from "../src/server/controlPlane/config";

const SECRET_KEYS = [
  "AGENTOPS_CONTRACT_SECRET",
  "AGENTOPS_CONTRACT_SECRET_FILE",
  "AGENTOPS_POSTGRES_DSN",
  "AGENTOPS_POSTGRES_DSN_FILE",
  "AGENTOPS_POSTGRES_HOST",
  "AGENTOPS_POSTGRES_PORT",
  "AGENTOPS_POSTGRES_DATABASE",
  "AGENTOPS_POSTGRES_USER",
  "AGENTOPS_POSTGRES_PASSWORD",
  "AGENTOPS_POSTGRES_PASSWORD_FILE",
] as const;

async function expectFailure(work: () => unknown) {
  assert.throws(work, (error: unknown) => (
    error instanceof Error
    && !error.message.includes("contract-secret-canary")
    && !error.message.includes("postgres-password-canary")
  ));
}

async function run() {
  const original = Object.fromEntries(
    SECRET_KEYS.map((key) => [key, process.env[key]]),
  );
  const directory = await mkdtemp(join(tmpdir(), "agentops-secrets-"));
  const secretPath = join(directory, "secret");
  const symlinkPath = join(directory, "secret-link");
  const oversizedPath = join(directory, "oversized");
  const postgresPasswordPath = join(directory, "postgres-password");
  const dsnPath = join(directory, "postgres-dsn");
  try {
    await writeFile(secretPath, "contract-secret-canary\n", { mode: 0o600 });
    await symlink(secretPath, symlinkPath);
    await writeFile(oversizedPath, "x".repeat(16 * 1024 + 1), {
      mode: 0o600,
    });
    await writeFile(
      postgresPasswordPath,
      "postgres-password-canary:/@?#%\n",
      { mode: 0o600 },
    );
    await writeFile(
      dsnPath,
      "postgresql://contract.invalid/example\n",
      { mode: 0o600 },
    );

    process.env.AGENTOPS_CONTRACT_SECRET_FILE = secretPath;
    assert.equal(
      secretEnvironmentValue("AGENTOPS_CONTRACT_SECRET"),
      "contract-secret-canary",
    );
    process.env.AGENTOPS_CONTRACT_SECRET = "direct-secret";
    await expectFailure(() =>
      secretEnvironmentValue("AGENTOPS_CONTRACT_SECRET"));
    delete process.env.AGENTOPS_CONTRACT_SECRET;

    process.env.AGENTOPS_CONTRACT_SECRET_FILE = "relative-secret";
    await expectFailure(() =>
      secretEnvironmentValue("AGENTOPS_CONTRACT_SECRET"));
    process.env.AGENTOPS_CONTRACT_SECRET_FILE = symlinkPath;
    await expectFailure(() =>
      secretEnvironmentValue("AGENTOPS_CONTRACT_SECRET"));
    process.env.AGENTOPS_CONTRACT_SECRET_FILE = oversizedPath;
    await expectFailure(() =>
      secretEnvironmentValue("AGENTOPS_CONTRACT_SECRET"));

    delete process.env.AGENTOPS_POSTGRES_DSN;
    process.env.AGENTOPS_POSTGRES_DSN_FILE = dsnPath;
    assert.equal(
      postgresDsn(),
      "postgresql://contract.invalid/example",
    );
    process.env.AGENTOPS_POSTGRES_HOST = "postgres";
    await expectFailure(() => postgresDsn());

    delete process.env.AGENTOPS_POSTGRES_DSN_FILE;
    process.env.AGENTOPS_POSTGRES_PORT = "5432";
    process.env.AGENTOPS_POSTGRES_DATABASE = "agentops";
    process.env.AGENTOPS_POSTGRES_USER = "agentops";
    process.env.AGENTOPS_POSTGRES_PASSWORD_FILE = postgresPasswordPath;
    const componentDsn = new URL(postgresDsn());
    assert.equal(componentDsn.protocol, "postgresql:");
    assert.equal(componentDsn.hostname, "postgres");
    assert.equal(componentDsn.port, "5432");
    assert.equal(componentDsn.pathname, "/agentops");
    assert.equal(decodeURIComponent(componentDsn.username), "agentops");
    assert.equal(
      decodeURIComponent(componentDsn.password),
      "postgres-password-canary:/@?#%",
    );

    process.env.AGENTOPS_POSTGRES_PASSWORD =
      "postgres-password-canary-direct";
    await expectFailure(() => postgresDsn());

    const receipt = {
      ok: true,
      contract: "agentops_configuration_secrets_contract_v1",
      bounded_regular_file: true,
      trailing_newline_removed: true,
      symlink_rejected: true,
      relative_path_rejected: true,
      direct_and_file_conflict_rejected: true,
      dsn_file_supported: true,
      postgres_component_secret_supported: true,
      secret_values_omitted: true,
      python_used: false,
      sqlite_used: false,
    };
    const serialized = JSON.stringify(receipt);
    assert.equal(serialized.includes("contract-secret-canary"), false);
    assert.equal(serialized.includes("postgres-password-canary"), false);
    console.log(serialized);
  } finally {
    for (const key of SECRET_KEYS) {
      const value = original[key];
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
    await rm(directory, { recursive: true, force: true });
  }
}

run().catch(() => {
  console.log(JSON.stringify({
    ok: false,
    contract: "agentops_configuration_secrets_contract_v1",
    error_code: "configuration_secrets_contract_failed",
    secret_values_omitted: true,
    python_used: false,
    sqlite_used: false,
  }));
  process.exitCode = 1;
});
