#!/usr/bin/env node

import { spawn } from "node:child_process";
import {
  closeSync,
  constants,
  fchmodSync,
  fstatSync,
  openSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { dirname, join } from "node:path";

const MAX_PASSWORD_BYTES = 4096;

function fail(code) {
  const error = new Error(code);
  error.code = code;
  throw error;
}

function requiredEnvironment(name, pattern) {
  const value = String(process.env[name] || "").trim();
  if (!pattern.test(value)) fail(`${name.toLowerCase()}_invalid`);
  return value;
}

function assertInvocation(arguments_) {
  const passwordStdinCount = arguments_.filter(
    (argument) => argument === "--password-stdin",
  ).length;
  if (
    passwordStdinCount !== 1
    || arguments_.some(
      (argument) => argument.startsWith("--password")
        && argument !== "--password-stdin",
    )
  ) {
    fail("owner_password_stdin_required");
  }
}

function readPasswordFile(path) {
  const noFollow = constants.O_NOFOLLOW;
  if (typeof noFollow !== "number") fail("owner_password_nofollow_unavailable");
  const descriptor = openSync(path, constants.O_RDONLY | noFollow);
  try {
    const metadata = fstatSync(descriptor);
    if (!metadata.isFile() || metadata.size < 1 || metadata.size > MAX_PASSWORD_BYTES) {
      fail("owner_migrator_password_file_invalid");
    }
    const bytes = readFileSync(descriptor);
    const after = fstatSync(descriptor);
    if (
      after.dev !== metadata.dev
      || after.ino !== metadata.ino
      || after.size !== metadata.size
      || after.mtimeMs !== metadata.mtimeMs
      || after.ctimeMs !== metadata.ctimeMs
    ) {
      bytes.fill(0);
      fail("owner_migrator_password_file_changed");
    }
    return bytes;
  } finally {
    closeSync(descriptor);
  }
}

function normalizedPassword(bytes) {
  let end = bytes.length;
  if (end > 0 && bytes[end - 1] === 0x0a) end -= 1;
  if (end > 0 && bytes[end - 1] === 0x0d) end -= 1;
  if (end < 1) fail("owner_migrator_password_empty");
  const value = bytes.subarray(0, end).toString("utf8");
  if (/\u0000|\r|\n/.test(value)) fail("owner_migrator_password_invalid");
  return value;
}

function pgpassEscape(value) {
  return value.replaceAll("\\", "\\\\").replaceAll(":", "\\:");
}

function writePgpass({ sourcePath, host, port, database, user }) {
  const bytes = readPasswordFile(sourcePath);
  let password = "";
  const path = join(dirname(sourcePath), `.owner-bootstrap-pgpass-${process.pid}`);
  let descriptor;
  try {
    password = normalizedPassword(bytes);
    descriptor = openSync(
      path,
      constants.O_CREAT | constants.O_EXCL | constants.O_WRONLY,
      0o600,
    );
    const line = [host, port, database, user, password]
      .map(pgpassEscape)
      .join(":");
    writeFileSync(descriptor, `${line}\n`, "utf8");
    fchmodSync(descriptor, 0o600);
  } finally {
    if (typeof descriptor === "number") closeSync(descriptor);
    bytes.fill(0);
    password = "";
  }
  return path;
}

async function run() {
  if (
    process.env.AGENTOPS_POSTGRES_DSN
    || process.env.DATABASE_URL
    || process.env.PGPASSWORD
    || process.env.PGPASSFILE
  ) {
    fail("owner_direct_database_secret_forbidden");
  }
  const arguments_ = process.argv.slice(2);
  assertInvocation(arguments_);

  const host = requiredEnvironment(
    "AGENTOPS_POSTGRES_MIGRATOR_HOST",
    /^(?:[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?)$/,
  );
  const port = requiredEnvironment(
    "AGENTOPS_POSTGRES_MIGRATOR_PORT",
    /^(?:[1-9][0-9]{0,4})$/,
  );
  if (Number(port) > 65535) fail("agentops_postgres_migrator_port_invalid");
  const database = requiredEnvironment(
    "AGENTOPS_POSTGRES_MIGRATOR_DATABASE",
    /^[A-Za-z_][A-Za-z0-9_]{0,62}$/,
  );
  const user = requiredEnvironment(
    "AGENTOPS_POSTGRES_MIGRATOR_USER",
    /^[A-Za-z_][A-Za-z0-9_]{0,62}$/,
  );
  const passwordFile = requiredEnvironment(
    "AGENTOPS_POSTGRES_MIGRATOR_PASSWORD_FILE",
    /^\/run\/agentops-runtime-secrets\/instance-[A-Za-z0-9._-]+\/postgres_migrator_password$/,
  );
  const pgpassFile = writePgpass({
    sourcePath: passwordFile,
    host,
    port,
    database,
    user,
  });

  const childEnvironment = {
    ...process.env,
    AGENTOPS_POSTGRES_DSN:
      `postgresql://${encodeURIComponent(user)}@${host}:${port}/${encodeURIComponent(database)}`,
    PGPASSFILE: pgpassFile,
  };
  delete childEnvironment.AGENTOPS_POSTGRES_MIGRATOR_PASSWORD_FILE;
  delete childEnvironment.AGENTOPS_POSTGRES_MIGRATOR_PASSWORD_SOURCE_FILE;

  try {
    const child = spawn(
      "npm",
      ["run", "--silent", "bootstrap:owner", "--", ...arguments_],
      { env: childEnvironment, stdio: "inherit" },
    );
    const forwardedSignals = ["SIGINT", "SIGTERM", "SIGHUP"];
    const handlers = new Map();
    for (const signal of forwardedSignals) {
      const handler = () => {
        if (child.exitCode === null && child.signalCode === null) child.kill(signal);
      };
      handlers.set(signal, handler);
      process.on(signal, handler);
    }
    const outcome = await new Promise((resolve, reject) => {
      child.once("error", reject);
      child.once("close", (code, signal) => resolve({ code, signal }));
    });
    for (const [signal, handler] of handlers) process.off(signal, handler);
    if (outcome.signal) {
      process.kill(process.pid, outcome.signal);
      return;
    }
    process.exitCode = outcome.code ?? 1;
  } finally {
    rmSync(pgpassFile, { force: true });
  }
}

run().catch((error) => {
  process.stdout.write(`${JSON.stringify({
    ok: false,
    error: typeof error?.code === "string"
      ? error.code
      : "owner_bootstrap_entrypoint_failed",
    credential_omitted: true,
    password_omitted: true,
    python_started: false,
  })}\n`);
  process.exitCode = 1;
});
