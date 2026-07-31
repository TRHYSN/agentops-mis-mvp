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

export function postgresDsnForRestore(
  targetDatabase,
  environment = process.env,
) {
  if (!/^[A-Za-z0-9_]+$/.test(targetDatabase)) {
    fail();
  }

  const direct = environment.AGENTOPS_POSTGRES_DSN?.trim() ?? "";
  const file = environment.AGENTOPS_POSTGRES_DSN_FILE?.trim() ?? "";
  if ((direct && file) || (!direct && !file)) {
    fail();
  }

  const source = direct || readStableRegularFile(file).trim();
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
  return parsed.toString();
}

export function writePostgresDsnForRestore(
  targetDatabase,
  outputPath,
  environment = process.env,
) {
  const sourcePath = environment.AGENTOPS_POSTGRES_DSN_FILE?.trim() ?? "";
  if (
    !sourcePath ||
    environment.AGENTOPS_POSTGRES_DSN?.trim() ||
    !outputPath ||
    dirname(outputPath) !== dirname(sourcePath)
  ) {
    fail();
  }

  const value = postgresDsnForRestore(targetDatabase, environment);
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
    );
  } catch {
    process.stderr.write("restore_dsn_invalid\n");
    process.exitCode = 65;
  }
}
