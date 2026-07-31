import {
  createHash,
  randomBytes,
  timingSafeEqual,
} from "node:crypto";
import { constants as fsConstants } from "node:fs";
import {
  lstat,
  mkdir,
  open,
  readFile,
  readdir,
  rename,
  rm,
} from "node:fs/promises";
import { homedir, hostname } from "node:os";
import { dirname, join, resolve } from "node:path";

export const LIFECYCLE_STATE_CONTRACT =
  "agentops_byoc_retained_data_lifecycle_state_v1";

const LIFECYCLE_LOCK_CONTRACT = "agentops_byoc_lifecycle_lock_v2";
const LIFECYCLE_CHILD_CONTRACT = "agentops_byoc_lifecycle_child_v1";
const LOCK_NAME = ".operation.lock";
const LOCK_OWNER_NAME = "owner.json";
const LOCK_CHILDREN_NAME = "children";
const LOCK_PREPARE_PREFIX = `${LOCK_NAME}.prepare.`;
const LOCK_ISOLATION_PREFIX = `${LOCK_NAME}.isolated.`;
const OWNER_TOKEN_PATTERN = /^[0-9a-f]{64}$/;
const SHA256_PATTERN = /^[0-9a-f]{64}$/;
const BOOT_ID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const PROCESS_START_PATTERN = /^linux_proc_start_time_ticks:[1-9][0-9]*$/;

function statePath(stateDirectory) {
  return join(stateDirectory, "state.json");
}

function lockPath(stateDirectory) {
  return join(stateDirectory, LOCK_NAME);
}

function privatePathMetadata(metadata, type) {
  const expectedType = type === "directory"
    ? metadata.isDirectory()
    : metadata.isFile();
  const currentUid = typeof process.getuid === "function" ? process.getuid() : null;
  return expectedType
    && !metadata.isSymbolicLink()
    && (metadata.mode & 0o077) === 0
    && (currentUid === null || metadata.uid === currentUid);
}

function sameFile(left, right) {
  return left.dev === right.dev && left.ino === right.ino;
}

async function syncDirectory(path) {
  const directory = await open(path, fsConstants.O_RDONLY);
  try {
    await directory.sync();
  } finally {
    await directory.close();
  }
}

async function pathMetadata(path) {
  try {
    return await lstat(path);
  } catch (error) {
    if (error?.code === "ENOENT") return null;
    throw error;
  }
}

async function isolationPaths(stateDirectory) {
  const entries = await readdir(stateDirectory, { withFileTypes: true });
  return entries
    .filter((entry) => entry.name.startsWith(LOCK_ISOLATION_PREFIX))
    .map((entry) => join(stateDirectory, entry.name))
    .sort();
}

async function assertNoIsolatedLock(stateDirectory) {
  if ((await isolationPaths(stateDirectory)).length !== 0) {
    throw new Error("lifecycle_operation_locked");
  }
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
  const currentUid = typeof process.getuid === "function" ? process.getuid() : null;
  if (
    (metadata.mode & 0o077) !== 0
    || (currentUid !== null && metadata.uid !== currentUid)
  ) {
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
  const path = statePath(stateDirectory);
  const metadataBefore = await pathMetadata(path);
  if (metadataBefore === null) return null;
  if (
    !privatePathMetadata(metadataBefore, "file")
    || metadataBefore.size < 1
    || metadataBefore.size > 16 * 1024 * 1024
  ) {
    throw new Error("lifecycle_state_file_invalid");
  }
  let handle;
  try {
    handle = await open(
      path,
      fsConstants.O_RDONLY | (fsConstants.O_NOFOLLOW || 0),
    );
    const metadataOpened = await handle.stat();
    if (!sameFile(metadataBefore, metadataOpened)) {
      throw new Error("lifecycle_state_file_changed");
    }
    const raw = await handle.readFile("utf8");
    const metadataAfter = await lstat(path);
    if (!sameFile(metadataBefore, metadataAfter)) {
      throw new Error("lifecycle_state_file_changed");
    }
    return validateState(JSON.parse(raw));
  } catch (error) {
    if (error instanceof SyntaxError) {
      throw new Error("lifecycle_state_invalid");
    }
    throw error;
  } finally {
    await handle?.close().catch(() => undefined);
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
    await syncDirectory(dirname(target));
  } finally {
    await handle?.close().catch(() => undefined);
    await rm(temporary, { force: true }).catch(() => undefined);
  }
}

async function linuxBootId() {
  if (process.platform !== "linux") return null;
  try {
    const value = (await readFile("/proc/sys/kernel/random/boot_id", "utf8"))
      .trim()
      .toLowerCase();
    return BOOT_ID_PATTERN.test(value) ? value : null;
  } catch {
    return null;
  }
}

async function linuxProcessStartIdentity(pid) {
  if (process.platform !== "linux") return null;
  try {
    const value = await readFile(`/proc/${pid}/stat`, "utf8");
    const commandEnd = value.lastIndexOf(")");
    if (commandEnd < 0) return null;
    const fieldsAfterCommand = value.slice(commandEnd + 1).trim().split(/\s+/);
    const startTimeTicks = fieldsAfterCommand[19];
    if (!/^[1-9][0-9]*$/.test(startTimeTicks || "")) return null;
    return `linux_proc_start_time_ticks:${startTimeTicks}`;
  } catch {
    return null;
  }
}

function ownerPayload(owner) {
  return {
    contract: owner.contract,
    pid: owner.pid,
    hostname: owner.hostname,
    boot_id: owner.boot_id,
    process_start_identity: owner.process_start_identity,
    owner_token: owner.owner_token,
    acquired_at: owner.acquired_at,
  };
}

function ownerIntegrity(owner) {
  return sha256(JSON.stringify(ownerPayload(owner)));
}

function childPayload(child) {
  return {
    contract: child.contract,
    pid: child.pid,
    process_group_id: child.process_group_id,
    hostname: child.hostname,
    boot_id: child.boot_id,
    process_start_identity: child.process_start_identity,
    parent_owner_token: child.parent_owner_token,
    child_token: child.child_token,
    started_at: child.started_at,
  };
}

function childIntegrity(child) {
  return sha256(JSON.stringify(childPayload(child)));
}

function validateChild(child, expectedParentOwnerToken) {
  const keys = Object.keys(child || {}).sort();
  const expectedKeys = [
    "boot_id",
    "child_token",
    "contract",
    "hostname",
    "integrity_sha256",
    "parent_owner_token",
    "pid",
    "process_group_id",
    "process_start_identity",
    "started_at",
  ].sort();
  const startedAt = typeof child?.started_at === "string"
    ? new Date(child.started_at)
    : null;
  if (
    !child
    || typeof child !== "object"
    || keys.length !== expectedKeys.length
    || keys.some((key, index) => key !== expectedKeys[index])
    || child.contract !== LIFECYCLE_CHILD_CONTRACT
    || !Number.isSafeInteger(child.pid)
    || child.pid < 1
    || child.pid > 2_147_483_647
    || !Number.isSafeInteger(child.process_group_id)
    || child.process_group_id < 1
    || child.process_group_id > 2_147_483_647
    || child.hostname !== hostname()
    || (child.boot_id !== null && !BOOT_ID_PATTERN.test(child.boot_id))
    || (
      child.process_start_identity !== null
      && !PROCESS_START_PATTERN.test(child.process_start_identity)
    )
    || child.parent_owner_token !== expectedParentOwnerToken
    || !OWNER_TOKEN_PATTERN.test(child.parent_owner_token || "")
    || !OWNER_TOKEN_PATTERN.test(child.child_token || "")
    || !SHA256_PATTERN.test(child.integrity_sha256 || "")
    || !startedAt
    || Number.isNaN(startedAt.valueOf())
    || startedAt.toISOString() !== child.started_at
  ) {
    throw new Error("lifecycle_lock_child_invalid");
  }
  const actual = Buffer.from(child.integrity_sha256, "hex");
  const expected = Buffer.from(childIntegrity(child), "hex");
  if (actual.length !== expected.length || !timingSafeEqual(actual, expected)) {
    throw new Error("lifecycle_lock_child_invalid");
  }
  return child;
}

function validateOwner(owner) {
  const keys = Object.keys(owner || {}).sort();
  const expectedKeys = [
    "acquired_at",
    "boot_id",
    "contract",
    "hostname",
    "integrity_sha256",
    "owner_token",
    "pid",
    "process_start_identity",
  ].sort();
  const acquiredAt = typeof owner?.acquired_at === "string"
    ? new Date(owner.acquired_at)
    : null;
  if (
    !owner
    || typeof owner !== "object"
    || keys.length !== expectedKeys.length
    || keys.some((key, index) => key !== expectedKeys[index])
    || owner.contract !== LIFECYCLE_LOCK_CONTRACT
    || !Number.isSafeInteger(owner.pid)
    || owner.pid < 1
    || owner.pid > 2_147_483_647
    || typeof owner.hostname !== "string"
    || owner.hostname.length < 1
    || owner.hostname.length > 255
    || /[\u0000-\u001f\u007f]/.test(owner.hostname)
    || (owner.boot_id !== null && !BOOT_ID_PATTERN.test(owner.boot_id))
    || (
      owner.process_start_identity !== null
      && !PROCESS_START_PATTERN.test(owner.process_start_identity)
    )
    || !OWNER_TOKEN_PATTERN.test(owner.owner_token || "")
    || !SHA256_PATTERN.test(owner.integrity_sha256 || "")
    || !acquiredAt
    || Number.isNaN(acquiredAt.valueOf())
    || acquiredAt.toISOString() !== owner.acquired_at
  ) {
    throw new Error("lifecycle_lock_owner_invalid");
  }
  const actual = Buffer.from(owner.integrity_sha256, "hex");
  const expected = Buffer.from(ownerIntegrity(owner), "hex");
  if (actual.length !== expected.length || !timingSafeEqual(actual, expected)) {
    throw new Error("lifecycle_lock_owner_invalid");
  }
  return owner;
}

async function readLockOwner(directory) {
  const directoryBefore = await lstat(directory);
  if (!privatePathMetadata(directoryBefore, "directory")) {
    throw new Error("lifecycle_lock_path_invalid");
  }
  const ownerPath = join(directory, LOCK_OWNER_NAME);
  const ownerBefore = await lstat(ownerPath);
  if (
    !privatePathMetadata(ownerBefore, "file")
    || ownerBefore.nlink !== 1
    || ownerBefore.size < 1
    || ownerBefore.size > 4096
  ) {
    throw new Error("lifecycle_lock_owner_invalid");
  }
  let handle;
  try {
    handle = await open(
      ownerPath,
      fsConstants.O_RDONLY | (fsConstants.O_NOFOLLOW || 0),
    );
    const ownerOpened = await handle.stat();
    if (!sameFile(ownerBefore, ownerOpened)) {
      throw new Error("lifecycle_lock_owner_changed");
    }
    const raw = await handle.readFile("utf8");
    const ownerAfter = await lstat(ownerPath);
    const directoryAfter = await lstat(directory);
    if (
      !sameFile(ownerBefore, ownerAfter)
      || !sameFile(directoryBefore, directoryAfter)
    ) {
      throw new Error("lifecycle_lock_owner_changed");
    }
    try {
      return {
        owner: validateOwner(JSON.parse(raw)),
        directoryMetadata: directoryBefore,
      };
    } catch (error) {
      if (error instanceof SyntaxError) {
        throw new Error("lifecycle_lock_owner_invalid");
      }
      throw error;
    }
  } finally {
    await handle?.close().catch(() => undefined);
  }
}

async function createPreparedLock(stateDirectory) {
  const ownerToken = randomBytes(32).toString("hex");
  const lock = join(
    stateDirectory,
    `${LOCK_PREPARE_PREFIX}${process.pid}.${randomBytes(16).toString("hex")}`,
  );
  let preparedCreated = false;
  try {
    await mkdir(lock, { mode: 0o700 });
    preparedCreated = true;
    await assertPrivateDirectory(lock);
    const payload = {
      contract: LIFECYCLE_LOCK_CONTRACT,
      pid: process.pid,
      hostname: hostname(),
      boot_id: await linuxBootId(),
      process_start_identity: await linuxProcessStartIdentity(process.pid),
      owner_token: ownerToken,
      acquired_at: new Date().toISOString(),
    };
    const owner = {
      ...payload,
      integrity_sha256: ownerIntegrity(payload),
    };
    const ownerHandle = await open(join(lock, "owner.json"), "wx", 0o600);
    try {
      await ownerHandle.writeFile(`${JSON.stringify(owner)}\n`, "utf8");
      await ownerHandle.sync();
    } finally {
      await ownerHandle.close();
    }
    await mkdir(join(lock, LOCK_CHILDREN_NAME), { mode: 0o700 });
    await assertPrivateDirectory(join(lock, LOCK_CHILDREN_NAME));
    await syncDirectory(lock);
    return { owner, prepared: lock };
  } catch (error) {
    if (preparedCreated) {
      await rm(lock, { recursive: true, force: true }).catch(() => undefined);
    }
    throw error;
  }
}

async function publishPreparedLock(stateDirectory, prepared, expectedOwner) {
  await assertNoIsolatedLock(stateDirectory);
  const lock = lockPath(stateDirectory);
  if (await pathMetadata(lock)) {
    throw new Error("lifecycle_operation_locked");
  }
  try {
    await rename(prepared, lock);
  } catch (error) {
    if (["EEXIST", "ENOTEMPTY", "EPERM"].includes(error?.code)) {
      throw new Error("lifecycle_operation_locked");
    }
    throw error;
  }
  await syncDirectory(stateDirectory);
  const published = await readLockOwner(lock);
  if (published.owner.owner_token !== expectedOwner.owner_token) {
    throw new Error("lifecycle_lock_owner_changed");
  }
  try {
    await assertNoIsolatedLock(stateDirectory);
  } catch (error) {
    await releaseOwnedLock(stateDirectory, expectedOwner).catch(() => undefined);
    throw error;
  }
}

async function isolateLock(stateDirectory, source, expectedMetadata, purpose) {
  const isolated = join(
    stateDirectory,
    `${LOCK_ISOLATION_PREFIX}${purpose}.${randomBytes(16).toString("hex")}`,
  );
  try {
    await rename(source, isolated);
  } catch (error) {
    if (["ENOENT", "EEXIST", "ENOTEMPTY"].includes(error?.code)) {
      throw new Error("lifecycle_lock_changed");
    }
    throw error;
  }
  await syncDirectory(stateDirectory);
  const isolatedMetadata = await lstat(isolated);
  if (!sameFile(expectedMetadata, isolatedMetadata)) {
    throw new Error("lifecycle_lock_changed");
  }
  return isolated;
}

async function removeIsolatedLock(stateDirectory, isolated, expectedOwner) {
  const verified = await readLockOwner(isolated);
  if (verified.owner.owner_token !== expectedOwner.owner_token) {
    throw new Error("lifecycle_lock_owner_changed");
  }
  await rm(isolated, { recursive: true });
  await syncDirectory(stateDirectory);
}

async function releaseOwnedLock(stateDirectory, expectedOwner) {
  const lock = lockPath(stateDirectory);
  const verified = await readLockOwner(lock);
  if (verified.owner.owner_token !== expectedOwner.owner_token) {
    throw new Error("lifecycle_lock_owner_changed");
  }
  if (
    (await readLifecycleChildren(lock, expectedOwner.owner_token)).length !== 0
  ) {
    throw new Error("lifecycle_lock_child_active");
  }
  const isolated = await isolateLock(
    stateDirectory,
    lock,
    verified.directoryMetadata,
    "release",
  );
  await removeIsolatedLock(stateDirectory, isolated, expectedOwner);
}

async function authoritativeLockPaths(stateDirectory) {
  const paths = await isolationPaths(stateDirectory);
  if (await pathMetadata(lockPath(stateDirectory))) {
    paths.unshift(lockPath(stateDirectory));
  }
  return paths;
}

async function readLifecycleChildren(lockDirectory, expectedOwnerToken) {
  const childrenDirectory = join(lockDirectory, LOCK_CHILDREN_NAME);
  await assertPrivateDirectory(childrenDirectory);
  const entries = await readdir(childrenDirectory, { withFileTypes: true });
  const children = [];
  for (const entry of entries.sort((left, right) =>
    left.name.localeCompare(right.name))) {
    if (!entry.isFile() || entry.isSymbolicLink() || !entry.name.endsWith(".json")) {
      throw new Error("lifecycle_lock_child_invalid");
    }
    const path = join(childrenDirectory, entry.name);
    const metadata = await lstat(path);
    if (
      !privatePathMetadata(metadata, "file")
      || metadata.nlink !== 1
      || metadata.size < 1
      || metadata.size > 4096
    ) {
      throw new Error("lifecycle_lock_child_invalid");
    }
    let parsed;
    try {
      parsed = JSON.parse(await readFile(path, "utf8"));
    } catch {
      throw new Error("lifecycle_lock_child_invalid");
    }
    children.push({
      child: validateChild(parsed, expectedOwnerToken),
      file_name: entry.name,
    });
  }
  return children;
}

function processTargetExists(target) {
  try {
    process.kill(target, 0);
    return true;
  } catch (error) {
    if (error?.code === "ESRCH") return false;
    if (error?.code === "EPERM") return true;
    throw new Error("lifecycle_lock_recovery_identity_unverifiable");
  }
}

async function assertNoLiveLifecycleChildren(lockDirectory, owner) {
  const children = await readLifecycleChildren(
    lockDirectory,
    owner.owner_token,
  );
  for (const { child } of children) {
    const currentBootId = await linuxBootId();
    if (
      child.boot_id !== null
      && currentBootId !== null
      && child.boot_id !== currentBootId
    ) {
      continue;
    }
    const groupTarget = process.platform === "win32"
      ? child.pid
      : -child.process_group_id;
    if (processTargetExists(groupTarget) || processTargetExists(child.pid)) {
      throw new Error("lifecycle_lock_recovery_child_alive");
    }
  }
}

async function assertOwnerIsStale(owner) {
  const currentHostname = hostname();
  if (owner.hostname !== currentHostname) {
    throw new Error("lifecycle_lock_recovery_cross_host_refused");
  }
  const currentBootId = await linuxBootId();
  if (owner.boot_id !== null && currentBootId !== null && owner.boot_id !== currentBootId) {
    return;
  }
  let processExists;
  try {
    process.kill(owner.pid, 0);
    processExists = true;
  } catch (error) {
    if (error?.code === "ESRCH") return;
    if (error?.code === "EPERM") processExists = true;
    else throw new Error("lifecycle_lock_recovery_identity_unverifiable");
  }
  if (!processExists) return;
  const currentStartIdentity = await linuxProcessStartIdentity(owner.pid);
  if (
    owner.process_start_identity !== null
    && currentStartIdentity !== null
    && owner.process_start_identity !== currentStartIdentity
  ) {
    return;
  }
  if (
    owner.process_start_identity === null
    || currentStartIdentity === null
  ) {
    throw new Error("lifecycle_lock_recovery_identity_unverifiable");
  }
  throw new Error("lifecycle_lock_recovery_owner_alive");
}

export async function lifecycleLockStatus(stateDirectory) {
  const lock = await pathMetadata(lockPath(stateDirectory));
  if (lock) {
    if (!privatePathMetadata(lock, "directory")) {
      throw new Error("lifecycle_lock_path_invalid");
    }
    return true;
  }
  return (await isolationPaths(stateDirectory)).length !== 0;
}

export async function recoverStaleLifecycleLock(
  stateDirectory,
  confirmationOperationId,
) {
  await assertPrivateDirectory(stateDirectory);
  const state = await readLifecycleState(stateDirectory);
  if (
    !state?.operation
    || typeof state.operation.operation_id !== "string"
    || typeof confirmationOperationId !== "string"
    || confirmationOperationId !== state.operation.operation_id
  ) {
    throw new Error("lifecycle_lock_recovery_confirmation_mismatch");
  }
  const confirmation = confirmationOperationId;
  const candidates = await authoritativeLockPaths(stateDirectory);
  if (candidates.length === 0) {
    throw new Error("lifecycle_operation_lock_missing");
  }
  if (candidates.length !== 1) {
    throw new Error("lifecycle_lock_recovery_concurrent_state");
  }
  const candidate = candidates[0];
  const verified = await readLockOwner(candidate);
  await assertOwnerIsStale(verified.owner);
  await assertNoLiveLifecycleChildren(candidate, verified.owner);
  const isolated = await isolateLock(
    stateDirectory,
    candidate,
    verified.directoryMetadata,
    "recovery",
  );
  await removeIsolatedLock(stateDirectory, isolated, verified.owner);
  return {
    contract: "agentops_byoc_lifecycle_lock_recovery_v1",
    ok: true,
    recovered: true,
    operation_id: confirmation,
    credentials_omitted: true,
  };
}

export async function registerLifecycleChildProcess(
  stateDirectory,
  pid,
  processGroupId,
) {
  if (
    !Number.isSafeInteger(pid)
    || pid < 1
    || !Number.isSafeInteger(processGroupId)
    || processGroupId < 1
  ) {
    throw new Error("lifecycle_lock_child_invalid");
  }
  const lock = lockPath(stateDirectory);
  const verifiedOwner = await readLockOwner(lock);
  if (verifiedOwner.owner.pid !== process.pid) {
    throw new Error("lifecycle_lock_owner_changed");
  }
  const childToken = randomBytes(32).toString("hex");
  const payload = {
    contract: LIFECYCLE_CHILD_CONTRACT,
    pid,
    process_group_id: processGroupId,
    hostname: hostname(),
    boot_id: await linuxBootId(),
    process_start_identity: await linuxProcessStartIdentity(pid),
    parent_owner_token: verifiedOwner.owner.owner_token,
    child_token: childToken,
    started_at: new Date().toISOString(),
  };
  const child = {
    ...payload,
    integrity_sha256: childIntegrity(payload),
  };
  const fileName = `${pid}.${randomBytes(16).toString("hex")}.json`;
  const childrenDirectory = join(lock, LOCK_CHILDREN_NAME);
  await assertPrivateDirectory(childrenDirectory);
  const handle = await open(join(childrenDirectory, fileName), "wx", 0o600);
  try {
    await handle.writeFile(`${JSON.stringify(child)}\n`, "utf8");
    await handle.sync();
  } finally {
    await handle.close();
  }
  await syncDirectory(childrenDirectory);
  return { child_token: childToken, file_name: fileName };
}

export async function releaseLifecycleChildProcess(stateDirectory, lease) {
  if (
    !lease
    || !OWNER_TOKEN_PATTERN.test(String(lease.child_token || ""))
    || !/^[1-9][0-9]*\.[0-9a-f]{32}\.json$/.test(
      String(lease.file_name || ""),
    )
  ) {
    throw new Error("lifecycle_lock_child_invalid");
  }
  const lock = lockPath(stateDirectory);
  const owner = await readLockOwner(lock);
  if (owner.owner.pid !== process.pid) {
    throw new Error("lifecycle_lock_owner_changed");
  }
  const children = await readLifecycleChildren(lock, owner.owner.owner_token);
  const registered = children.find(({ file_name: fileName }) =>
    fileName === lease.file_name);
  if (
    !registered
    || registered.child.child_token !== lease.child_token
  ) {
    throw new Error("lifecycle_lock_child_changed");
  }
  const groupTarget = process.platform === "win32"
    ? registered.child.pid
    : -registered.child.process_group_id;
  if (processTargetExists(groupTarget)) {
    throw new Error("lifecycle_lock_child_active");
  }
  const childrenDirectory = join(lock, LOCK_CHILDREN_NAME);
  await rm(join(childrenDirectory, lease.file_name));
  await syncDirectory(childrenDirectory);
}

export async function withLifecycleLock(stateDirectory, operation) {
  await ensureLifecycleStateDirectory(stateDirectory);
  const prepared = await createPreparedLock(stateDirectory);
  try {
    await publishPreparedLock(
      stateDirectory,
      prepared.prepared,
      prepared.owner,
    );
  } catch (error) {
    await rm(prepared.prepared, { recursive: true, force: true })
      .catch(() => undefined);
    throw error;
  }
  try {
    return await operation();
  } finally {
    await releaseOwnedLock(stateDirectory, prepared.owner);
  }
}
