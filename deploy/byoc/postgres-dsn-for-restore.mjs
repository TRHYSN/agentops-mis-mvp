import {
  closeSync,
  constants,
  fchmodSync,
  fstatSync,
  fsyncSync,
  lstatSync,
  openSync,
  readFileSync,
  unlinkSync,
  writeFileSync,
} from "node:fs";
import { dirname } from "node:path";
import { fileURLToPath } from "node:url";

const MAX_DSN_BYTES = 64 * 1024;
const POSTGRES_PROFILES = Object.freeze({
  runtime: "AGENTOPS_POSTGRES",
  migrator: "AGENTOPS_POSTGRES_MIGRATOR",
  "entitlement-admin": "AGENTOPS_POSTGRES_ENTITLEMENT_ADMIN",
});
const POSTGRES_COMPONENT_SUFFIXES = Object.freeze([
  "HOST",
  "PORT",
  "DATABASE",
  "USER",
  "PASSWORD",
  "PASSWORD_FILE",
]);

function fail() {
  throw new Error("restore_dsn_invalid");
}

function readStableRegularFile(path) {
  const before = lstatSync(path);
  if (!before.isFile() || before.isSymbolicLink()) {
    fail();
  }
  if (before.size < 1 || before.size > MAX_DSN_BYTES) {
    fail();
  }

  const noFollow = constants.O_NOFOLLOW;
  if (typeof noFollow !== "number") {
    fail();
  }

  const descriptor = openSync(path, constants.O_RDONLY | noFollow);
  try {
    const opened = fstatSync(descriptor);
    if (
      !opened.isFile() ||
      opened.dev !== before.dev ||
      opened.ino !== before.ino ||
      opened.size !== before.size
    ) {
      fail();
    }

    const value = readFileSync(descriptor, "utf8");
    const after = fstatSync(descriptor);
    if (
      after.dev !== opened.dev ||
      after.ino !== opened.ino ||
      after.size !== opened.size ||
      after.mtimeMs !== opened.mtimeMs ||
      after.ctimeMs !== opened.ctimeMs
    ) {
      fail();
    }
    return value;
  } finally {
    closeSync(descriptor);
  }
}

function componentPostgresDsn(targetDatabase, environment, prefix) {
  const host = environment[`${prefix}_HOST`]?.trim() ?? "";
  const portValue = environment[`${prefix}_PORT`]?.trim() || "5432";
  const database = environment[`${prefix}_DATABASE`]?.trim() ?? "";
  const user = environment[`${prefix}_USER`]?.trim() ?? "";
  const directPassword = environment[`${prefix}_PASSWORD`] ?? "";
  const passwordFile =
    environment[`${prefix}_PASSWORD_FILE`]?.trim() ?? "";
  const port = Number(portValue);
  if (
    !/^[A-Za-z0-9.-]+$/.test(host) ||
    !/^[A-Za-z_][A-Za-z0-9_.-]{0,62}$/.test(database) ||
    !/^[A-Za-z_][A-Za-z0-9_.-]{0,62}$/.test(user) ||
    directPassword ||
    !passwordFile ||
    !Number.isInteger(port) ||
    port < 1 ||
    port > 65535
  ) {
    fail();
  }

  const password = readStableRegularFile(passwordFile).replace(/\r?\n$/, "");
  if (!password || password.includes("\0")) {
    fail();
  }

  const parsed = new URL("postgresql://localhost");
  parsed.hostname = host;
  parsed.port = String(port);
  parsed.username = user;
  parsed.password = password;
  parsed.pathname = `/${targetDatabase}`;
  return parsed.toString();
}

export function postgresDsnForRestore(
  targetDatabase,
  environment = process.env,
  sourceProfile = "runtime",
  targetProfile = sourceProfile,
) {
  if (!/^[A-Za-z0-9_]+$/.test(targetDatabase)) {
    fail();
  }

  const sourcePrefix = POSTGRES_PROFILES[sourceProfile];
  const targetPrefix = POSTGRES_PROFILES[targetProfile];
  if (!sourcePrefix || !targetPrefix) {
    fail();
  }
  const direct = environment[`${sourcePrefix}_DSN`]?.trim() ?? "";
  const file = environment[`${sourcePrefix}_DSN_FILE`]?.trim() ?? "";
  const componentConfigured = POSTGRES_COMPONENT_SUFFIXES.some(
    (suffix) => Boolean(environment[`${sourcePrefix}_${suffix}`]?.trim()),
  );
  const configuredFamilies =
    Number(Boolean(direct)) + Number(Boolean(file)) + Number(componentConfigured);
  if (configuredFamilies !== 1) {
    fail();
  }

  const source = direct
    || (file
      ? readStableRegularFile(file).trim()
      : componentPostgresDsn(targetDatabase, environment, sourcePrefix));
  if (!source || source.includes("\0")) {
    fail();
  }

  let parsed;
  try {
    parsed = new URL(source);
  } catch {
    fail();
  }
  if (parsed.protocol !== "postgres:" && parsed.protocol !== "postgresql:") {
    fail();
  }

  parsed.pathname = `/${targetDatabase}`;
  if (targetPrefix !== sourcePrefix) {
    const targetUser = environment[`${targetPrefix}_USER`]?.trim() ?? "";
    const targetPasswordFile =
      environment[`${targetPrefix}_PASSWORD_FILE`]?.trim() ?? "";
    if (
      !/^[A-Za-z_][A-Za-z0-9_]{0,62}$/.test(targetUser) ||
      !targetPasswordFile ||
      environment[`${targetPrefix}_PASSWORD`]
    ) {
      fail();
    }
    const targetPassword = readStableRegularFile(targetPasswordFile)
      .replace(/\r?\n$/, "");
    if (!targetPassword || targetPassword.includes("\0")) {
      fail();
    }
    parsed.username = targetUser;
    parsed.password = targetPassword;
  }
  return parsed.toString();
}

export function writePostgresDsnForRestore(
  targetDatabase,
  outputPath,
  environment = process.env,
  sourceProfile = "runtime",
  targetProfile = sourceProfile,
) {
  const sourcePrefix = POSTGRES_PROFILES[sourceProfile];
  if (!sourcePrefix || !POSTGRES_PROFILES[targetProfile]) {
    fail();
  }
  const sourceDsnPath =
    environment[`${sourcePrefix}_DSN_FILE`]?.trim() ?? "";
  const sourcePasswordPath =
    environment[`${sourcePrefix}_PASSWORD_FILE`]?.trim() ?? "";
  const sourcePath = sourceDsnPath || sourcePasswordPath;
  if (
    !sourcePath ||
    environment[`${sourcePrefix}_DSN`]?.trim() ||
    !outputPath ||
    dirname(outputPath) !== dirname(sourcePath)
  ) {
    fail();
  }

  const value = postgresDsnForRestore(
    targetDatabase,
    environment,
    sourceProfile,
    targetProfile,
  );
  const noFollow = constants.O_NOFOLLOW;
  if (typeof noFollow !== "number") {
    fail();
  }

  let descriptor;
  let outputCreated = false;
  try {
    descriptor = openSync(
      outputPath,
      constants.O_CREAT |
        constants.O_EXCL |
        constants.O_WRONLY |
        noFollow,
      0o400,
    );
    outputCreated = true;
    writeFileSync(descriptor, value);
    fchmodSync(descriptor, 0o400);
    fsyncSync(descriptor);
    closeSync(descriptor);
    descriptor = undefined;
  } catch {
    if (descriptor !== undefined) {
      try {
        closeSync(descriptor);
      } catch {
        // Continue with path cleanup and a bounded failure.
      }
    }
    if (outputCreated) {
      try {
        unlinkSync(outputPath);
      } catch {
        // The fail-closed caller only needs its partial output to be removed.
      }
    }
    fail();
  }
}

function isMain() {
  return process.argv[1] === fileURLToPath(import.meta.url);
}

if (isMain()) {
  try {
    writePostgresDsnForRestore(
      process.argv[2] ?? "",
      process.argv[3] ?? "",
      process.env,
      process.argv[4] ?? "runtime",
      process.argv[5] ?? process.argv[4] ?? "runtime",
    );
  } catch {
    process.stderr.write("restore_dsn_invalid\n");
    process.exitCode = 65;
  }
}
