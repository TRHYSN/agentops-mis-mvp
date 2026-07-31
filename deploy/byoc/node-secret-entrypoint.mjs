import { spawn } from "node:child_process";
import {
  chmodSync,
  chownSync,
  closeSync,
  constants,
  fchmodSync,
  fchownSync,
  fstatSync,
  fsyncSync,
  lstatSync,
  mkdtempSync,
  openSync,
  readFileSync,
  renameSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { fileURLToPath } from "node:url";

const NODE_UID = 1000;
const NODE_GID = 1000;
const MAX_SECRET_BYTES = 64 * 1024;

function fail(code) {
  const error = new Error(code);
  error.code = code;
  throw error;
}

function stableSecretBytes(sourcePath) {
  const before = lstatSync(sourcePath);
  if (!before.isFile() || before.isSymbolicLink()) {
    fail("source_not_regular");
  }
  if (before.size < 1 || before.size > MAX_SECRET_BYTES) {
    fail("source_size_invalid");
  }

  const noFollow = constants.O_NOFOLLOW;
  if (typeof noFollow !== "number") {
    fail("nofollow_unavailable");
  }

  const descriptor = openSync(sourcePath, constants.O_RDONLY | noFollow);
  try {
    const opened = fstatSync(descriptor);
    if (
      !opened.isFile() ||
      opened.dev !== before.dev ||
      opened.ino !== before.ino ||
      opened.size !== before.size
    ) {
      fail("source_identity_changed");
    }

    const value = readFileSync(descriptor);
    const after = fstatSync(descriptor);
    if (
      after.dev !== opened.dev ||
      after.ino !== opened.ino ||
      after.size !== opened.size ||
      after.mtimeMs !== opened.mtimeMs ||
      after.ctimeMs !== opened.ctimeMs
    ) {
      fail("source_changed_during_read");
    }
    return value;
  } finally {
    closeSync(descriptor);
  }
}

export function stageSecretFile({
  sourcePath,
  targetDirectory,
  targetName,
  targetUid,
  targetGid,
}) {
  const value = stableSecretBytes(sourcePath);
  const temporaryPath = `${targetDirectory}/.${targetName}.${process.pid}`;
  const finalPath = `${targetDirectory}/${targetName}`;
  const descriptor = openSync(
    temporaryPath,
    constants.O_CREAT | constants.O_EXCL | constants.O_WRONLY,
    0o400,
  );
  try {
    writeFileSync(descriptor, value);
    fchmodSync(descriptor, 0o400);
    fchownSync(descriptor, targetUid, targetGid);
    fsyncSync(descriptor);
  } finally {
    closeSync(descriptor);
    value.fill(0);
  }
  renameSync(temporaryPath, finalPath);
  return finalPath;
}

export function prepareRuntimeSecrets({
  definitions,
  runtimePrefix,
  targetUid,
  targetGid,
}) {
  const runtimeDirectory = mkdtempSync(runtimePrefix);
  chmodSync(runtimeDirectory, 0o700);
  try {
    const prepared = {};
    for (const definition of definitions) {
      prepared[definition.environmentName] = stageSecretFile({
        sourcePath: definition.sourcePath,
        targetDirectory: runtimeDirectory,
        targetName: definition.targetName,
        targetUid,
        targetGid,
      });
    }
    chownSync(runtimeDirectory, targetUid, targetGid);
    return { prepared, runtimeDirectory };
  } catch (error) {
    rmSync(runtimeDirectory, { force: true, recursive: true });
    throw error;
  }
}

function parseInvocation(arguments_) {
  const separator = arguments_.indexOf("--");
  if (separator < 0 || separator === arguments_.length - 1) {
    fail("command_missing");
  }
  const flags = new Set(arguments_.slice(0, separator));
  if (
    [...flags].some(
      (flag) => flag !== "--postgres" && flag !== "--human-session",
    )
  ) {
    fail("flag_invalid");
  }
  if (!flags.has("--postgres")) {
    fail("postgres_secret_required");
  }
  return {
    command: arguments_.slice(separator + 1),
    includeHumanSession: flags.has("--human-session"),
  };
}

function assertDroppedPrivilegeState() {
  if (process.platform !== "linux") {
    fail("linux_runtime_required");
  }
  const status = readFileSync("/proc/self/status", "utf8");
  for (const field of ["CapInh", "CapPrm", "CapEff", "CapAmb"]) {
    const capabilities = status.match(
      new RegExp(`^${field}:\\s+([0-9A-Fa-f]+)$`, "m"),
    );
    if (!capabilities || !/^0+$/.test(capabilities[1])) {
      fail("privilege_state_invalid");
    }
  }
  if (!/^NoNewPrivs:\s+1$/m.test(status)) {
    fail("privilege_state_invalid");
  }
}

export function dropPrivilegesAndAssert() {
  if (process.getuid?.() !== 0 || process.getgid?.() !== 0) {
    fail("root_initialization_required");
  }
  process.setgroups([]);
  process.setgid(NODE_GID);
  process.setuid(NODE_UID);
  if (process.getuid() !== NODE_UID || process.getgid() !== NODE_GID) {
    fail("privilege_drop_failed");
  }
  assertDroppedPrivilegeState();
}

async function main() {
  if (process.getuid?.() !== 0 || process.getgid?.() !== 0) {
    fail("root_initialization_required");
  }
  if (
    process.env.AGENTOPS_POSTGRES_PASSWORD ||
    process.env.AGENTOPS_POSTGRES_DSN ||
    process.env.AGENTOPS_HUMAN_SESSION_HMAC_KEY
  ) {
    fail("direct_secret_forbidden");
  }

  const invocation = parseInvocation(process.argv.slice(2));
  const definitions = [];
  const dsnFile =
    process.env.AGENTOPS_POSTGRES_DSN_SOURCE_FILE?.trim() ||
    process.env.AGENTOPS_POSTGRES_DSN_FILE?.trim() ||
    "";
  const passwordFile =
    process.env.AGENTOPS_POSTGRES_PASSWORD_SOURCE_FILE?.trim() ||
    process.env.AGENTOPS_POSTGRES_PASSWORD_FILE?.trim() ||
    "";
  if (dsnFile && passwordFile) {
    fail("postgres_secret_family_ambiguous");
  }
  if (dsnFile) {
    definitions.push({
      environmentName: "AGENTOPS_POSTGRES_DSN_FILE",
      sourcePath: dsnFile,
      targetName: "postgres_dsn",
    });
  } else {
    definitions.push({
      environmentName: "AGENTOPS_POSTGRES_PASSWORD_FILE",
      sourcePath: passwordFile || "/run/secrets/postgres_password",
      targetName: "postgres_password",
    });
  }
  if (invocation.includeHumanSession) {
    definitions.push({
      environmentName: "AGENTOPS_HUMAN_SESSION_HMAC_KEY_FILE",
      sourcePath:
        process.env.AGENTOPS_HUMAN_SESSION_HMAC_KEY_SOURCE_FILE?.trim() ||
        process.env.AGENTOPS_HUMAN_SESSION_HMAC_KEY_FILE?.trim() ||
        "/run/secrets/human_session_hmac_key",
      targetName: "human_session_hmac_key",
    });
  }

  const { prepared, runtimeDirectory } = prepareRuntimeSecrets({
    definitions,
    runtimePrefix: "/run/agentops-runtime-secrets/instance-",
    targetUid: NODE_UID,
    targetGid: NODE_GID,
  });
  Object.assign(process.env, prepared);
  delete process.env.AGENTOPS_POSTGRES_PASSWORD_SOURCE_FILE;
  delete process.env.AGENTOPS_POSTGRES_DSN_SOURCE_FILE;
  delete process.env.AGENTOPS_HUMAN_SESSION_HMAC_KEY_SOURCE_FILE;

  try {
    dropPrivilegesAndAssert();

    const child = spawn(invocation.command[0], invocation.command.slice(1), {
      env: process.env,
      stdio: "inherit",
    });
    const signalHandlers = new Map();
    const forwardedSignals = ["SIGINT", "SIGTERM", "SIGHUP"];
    for (const signal of forwardedSignals) {
      const handler = () => {
        if (child.exitCode === null && child.signalCode === null) {
          child.kill(signal);
        }
      };
      signalHandlers.set(signal, handler);
      process.on(signal, handler);
    }
    const result = await new Promise((resolve, reject) => {
      child.once("error", reject);
      child.once("exit", (code, signal) => resolve({ code, signal }));
    });
    for (const [signal, handler] of signalHandlers) {
      process.removeListener(signal, handler);
    }
    rmSync(runtimeDirectory, { force: true, recursive: true });
    if (result.signal) {
      process.kill(process.pid, result.signal);
      return;
    }
    process.exitCode = result.code ?? 1;
  } catch (error) {
    rmSync(runtimeDirectory, { force: true, recursive: true });
    throw error;
  }
}

function isMain() {
  return process.argv[1] === fileURLToPath(import.meta.url);
}

if (isMain()) {
  main().catch((error) => {
    const code =
      typeof error?.code === "string" ? error.code : "initialization_failed";
    process.stderr.write(`byoc_secret_preflight_failed:${code}\n`);
    process.exitCode = 78;
  });
}
