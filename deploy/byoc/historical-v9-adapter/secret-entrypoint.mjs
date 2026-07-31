import { spawn } from "node:child_process";
import {
  chmodSync,
  chownSync,
  closeSync,
  constants,
  fchmodSync,
  fchownSync,
  fstatSync,
  mkdtempSync,
  openSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";

const NODE_UID = 1000;
const NODE_GID = 1000;
const MAX_SECRET_BYTES = 16 * 1024;

function fail(code) {
  throw new Error(code);
}

function stageSecret(sourcePath, directory, targetName) {
  const source = openSync(sourcePath, constants.O_RDONLY | constants.O_NOFOLLOW);
  let value;
  try {
    const before = fstatSync(source);
    if (!before.isFile() || before.size < 1 || before.size > MAX_SECRET_BYTES) {
      fail("historical_secret_shape_invalid");
    }
    value = readFileSync(source);
    const after = fstatSync(source);
    if (
      after.dev !== before.dev
      || after.ino !== before.ino
      || after.size !== before.size
      || after.mtimeMs !== before.mtimeMs
      || after.ctimeMs !== before.ctimeMs
    ) {
      value.fill(0);
      fail("historical_secret_identity_changed");
    }
  } finally {
    closeSync(source);
  }

  const targetPath = `${directory}/${targetName}`;
  try {
    const target = openSync(
      targetPath,
      constants.O_CREAT | constants.O_EXCL | constants.O_WRONLY,
      0o400,
    );
    try {
      writeFileSync(target, value);
      fchmodSync(target, 0o400);
      fchownSync(target, NODE_UID, NODE_GID);
    } finally {
      closeSync(target);
    }
  } finally {
    value.fill(0);
  }
  return targetPath;
}

function invocation(arguments_) {
  const separator = arguments_.indexOf("--");
  const flags = new Set(arguments_.slice(0, separator));
  const command = arguments_.slice(separator + 1);
  if (
    separator < 0
    || command.length === 0
    || [...flags].some((flag) => !["--postgres", "--human-session"].includes(flag))
    || !flags.has("--postgres")
  ) {
    fail("historical_secret_invocation_invalid");
  }
  return { command, includeHumanSession: flags.has("--human-session") };
}

function assertDroppedPrivileges() {
  const status = readFileSync("/proc/self/status", "utf8");
  for (const field of ["CapInh", "CapPrm", "CapEff", "CapAmb"]) {
    const value = status.match(new RegExp(`^${field}:\\s+([0-9A-Fa-f]+)$`, "m"));
    if (!value || !/^0+$/.test(value[1])) fail("historical_privilege_drop_failed");
  }
  if (!/^NoNewPrivs:\s+1$/m.test(status)) fail("historical_privilege_drop_failed");
}

async function main() {
  if (process.platform !== "linux" || process.getuid?.() !== 0 || process.getgid?.() !== 0) {
    fail("historical_root_preflight_required");
  }
  if (process.env.AGENTOPS_POSTGRES_PASSWORD || process.env.AGENTOPS_HUMAN_SESSION_HMAC_KEY) {
    fail("historical_direct_secret_forbidden");
  }

  const { command, includeHumanSession } = invocation(process.argv.slice(2));
  const runtimeDirectory = mkdtempSync(
    "/run/agentops-historical-secrets/instance-",
  );
  chmodSync(runtimeDirectory, 0o700);
  try {
    process.env.AGENTOPS_POSTGRES_PASSWORD_FILE = stageSecret(
      process.env.AGENTOPS_POSTGRES_PASSWORD_SOURCE_FILE?.trim()
        || "/run/secrets/postgres_migrator_password",
      runtimeDirectory,
      "postgres_password",
    );
    if (includeHumanSession) {
      process.env.AGENTOPS_HUMAN_SESSION_HMAC_KEY_FILE = stageSecret(
        process.env.AGENTOPS_HUMAN_SESSION_HMAC_KEY_SOURCE_FILE?.trim()
          || "/run/secrets/human_session_hmac_key",
        runtimeDirectory,
        "human_session_hmac_key",
      );
    }
    delete process.env.AGENTOPS_POSTGRES_PASSWORD_SOURCE_FILE;
    delete process.env.AGENTOPS_HUMAN_SESSION_HMAC_KEY_SOURCE_FILE;
    chownSync(runtimeDirectory, NODE_UID, NODE_GID);
    process.setgroups([]);
    process.setgid(NODE_GID);
    process.setuid(NODE_UID);
    assertDroppedPrivileges();

    const child = spawn(command[0], command.slice(1), {
      env: process.env,
      stdio: "inherit",
    });
    for (const signal of ["SIGINT", "SIGTERM", "SIGHUP"]) {
      process.on(signal, () => child.kill(signal));
    }
    const result = await new Promise((resolve, reject) => {
      child.once("error", reject);
      child.once("exit", (code, signal) => resolve({ code, signal }));
    });
    if (result.signal) process.kill(process.pid, result.signal);
    process.exitCode = result.code ?? 1;
  } finally {
    rmSync(runtimeDirectory, { force: true, recursive: true });
  }
}

main().catch((error) => {
  console.error(JSON.stringify({
    contract: "agentops_byoc_historical_secret_adapter_v1",
    ok: false,
    error_code: error instanceof Error ? error.message : "historical_secret_adapter_failed",
    credentials_omitted: true,
  }));
  process.exitCode = 1;
});
