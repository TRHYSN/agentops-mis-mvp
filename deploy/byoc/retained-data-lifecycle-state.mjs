import { createHash, randomBytes } from "node:crypto";
import {
  lstat,
  mkdir,
  open,
  readFile,
  rename,
  rm,
} from "node:fs/promises";
import { homedir } from "node:os";
import { dirname, join, resolve } from "node:path";

export const LIFECYCLE_STATE_CONTRACT =
  "agentops_byoc_retained_data_lifecycle_state_v1";

function statePath(stateDirectory) {
  return join(stateDirectory, "state.json");
}

function lockPath(stateDirectory) {
  return join(stateDirectory, ".operation.lock");
}

export function lifecycleStateDirectory(environment = process.env) {
  const configured = String(
    environment.AGENTOPS_BYOC_LIFECYCLE_STATE_DIR || "",
  ).trim();
  if (configured) return resolve(configured);
  const stateHome = String(environment.XDG_STATE_HOME || "").trim()
    || join(homedir(), ".local", "state");
  return resolve(stateHome, "agentops-mis", "byoc-lifecycle");
}

export function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}

export async function sha256File(path) {
  return sha256(await readFile(path));
}

async function assertPrivateDirectory(path) {
  const metadata = await lstat(path);
  if (!metadata.isDirectory() || metadata.isSymbolicLink()) {
    throw new Error("lifecycle_state_directory_invalid");
  }
  if ((metadata.mode & 0o077) !== 0) {
    throw new Error("lifecycle_state_directory_permissions_invalid");
  }
}

export async function ensureLifecycleStateDirectory(path) {
  await mkdir(path, { recursive: true, mode: 0o700 });
  await assertPrivateDirectory(path);
  await mkdir(join(path, "backups"), { mode: 0o700 }).catch((error) => {
    if (error?.code !== "EEXIST") throw error;
  });
  await assertPrivateDirectory(join(path, "backups"));
}

function validateState(state) {
  if (
    !state
    || typeof state !== "object"
    || state.contract !== LIFECYCLE_STATE_CONTRACT
    || !Number.isSafeInteger(state.generation)
    || state.generation < 1
    || !state.installation
    || typeof state.installation !== "object"
    || !Array.isArray(state.history)
  ) {
    throw new Error("lifecycle_state_invalid");
  }
  if (state.operation !== null && typeof state.operation !== "object") {
    throw new Error("lifecycle_state_invalid");
  }
  return state;
}

export async function readLifecycleState(stateDirectory) {
  try {
    const path = statePath(stateDirectory);
    const metadata = await lstat(path);
    if (
      !metadata.isFile()
      || metadata.isSymbolicLink()
      || (metadata.mode & 0o077) !== 0
    ) {
      throw new Error("lifecycle_state_file_invalid");
    }
    const raw = await readFile(path, "utf8");
    return validateState(JSON.parse(raw));
  } catch (error) {
    if (error?.code === "ENOENT") return null;
    if (error instanceof SyntaxError) {
      throw new Error("lifecycle_state_invalid");
    }
    throw error;
  }
}

export async function writeLifecycleState(stateDirectory, state) {
  await ensureLifecycleStateDirectory(stateDirectory);
  const next = validateState(state);
  const target = statePath(stateDirectory);
  const temporary = join(
    stateDirectory,
    `.state.${process.pid}.${randomBytes(8).toString("hex")}.tmp`,
  );
  let handle;
  try {
    handle = await open(temporary, "wx", 0o600);
    await handle.writeFile(`${JSON.stringify(next, null, 2)}\n`, "utf8");
    await handle.sync();
    await handle.close();
    handle = undefined;
    await rename(temporary, target);
    const directory = await open(dirname(target), "r");
    try {
      await directory.sync();
    } finally {
      await directory.close();
    }
  } finally {
    await handle?.close().catch(() => undefined);
    await rm(temporary, { force: true }).catch(() => undefined);
  }
}

export async function lifecycleLockStatus(stateDirectory) {
  try {
    const metadata = await lstat(lockPath(stateDirectory));
    return metadata.isDirectory() && !metadata.isSymbolicLink();
  } catch (error) {
    if (error?.code === "ENOENT") return false;
    throw error;
  }
}

export async function withLifecycleLock(stateDirectory, operation) {
  await ensureLifecycleStateDirectory(stateDirectory);
  const lock = lockPath(stateDirectory);
  try {
    await mkdir(lock, { mode: 0o700 });
  } catch (error) {
    if (error?.code === "EEXIST") {
      throw new Error("lifecycle_operation_locked");
    }
    throw error;
  }
  try {
    const owner = await open(join(lock, "owner.json"), "wx", 0o600);
    try {
      await owner.writeFile(JSON.stringify({
        contract: "agentops_byoc_lifecycle_lock_v1",
        pid: process.pid,
        acquired_at: new Date().toISOString(),
      }));
      await owner.sync();
    } finally {
      await owner.close();
    }
    return await operation();
  } finally {
    await rm(lock, { recursive: true, force: true });
  }
}
