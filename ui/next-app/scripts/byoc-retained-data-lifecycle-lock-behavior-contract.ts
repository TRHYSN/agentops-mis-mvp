import assert from "node:assert/strict";
import { spawn, type ChildProcess } from "node:child_process";
import {
  chmod,
  lstat,
  mkdtemp,
  mkdir,
  readFile,
  readdir,
  rename,
  rm,
  symlink,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

// @ts-expect-error The BYOC lifecycle state helper is intentionally plain ESM.
import * as lifecycleState from "../../../deploy/byoc/retained-data-lifecycle-state.mjs";
// @ts-expect-error The BYOC lifecycle CLI is intentionally plain ESM.
import { runLifecycle } from "../../../deploy/byoc/retained-data-lifecycle.mjs";

const {
  LIFECYCLE_STATE_CONTRACT,
  lifecycleLockStatus,
  recoverStaleLifecycleLock,
  withLifecycleLock,
  writeLifecycleState,
} = lifecycleState;

const OPERATION_ID = "byoc_lifecycle_deadbeefdeadbeefdead";
const WRONG_OPERATION_ID = "byoc_lifecycle_feedfacefeedfacefeed";
const SECRET_CANARY = "owner_token_must_never_be_returned";
const testRoot = await mkdtemp(join(tmpdir(), "agentops-byoc-lock-contract-"));
const modulePath = resolve(
  dirname(fileURLToPath(import.meta.url)),
  "../../../deploy/byoc/retained-data-lifecycle-state.mjs",
);

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : String(error);
}

async function expectError(
  operation: () => Promise<unknown>,
  expected: RegExp,
) {
  await assert.rejects(operation, (error: unknown) => {
    assert.match(errorMessage(error), expected);
    assert.doesNotMatch(errorMessage(error), /owner_token|[0-9a-f]{64}/i);
    return true;
  });
}

async function createStateDirectory(name: string) {
  const stateDirectory = join(testRoot, name);
  await writeLifecycleState(stateDirectory, {
    contract: LIFECYCLE_STATE_CONTRACT,
    generation: 1,
    installation: { image_id: "sha256:fixture" },
    operation: {
      operation_id: OPERATION_ID,
      phase: "backup_ready",
    },
    history: [],
    updated_at: new Date().toISOString(),
  });
  return stateDirectory;
}

async function waitForLine(child: ChildProcess, expected: string) {
  return new Promise<void>((resolvePromise, rejectPromise) => {
    let output = "";
    const timeout = setTimeout(() => {
      rejectPromise(new Error("child_lock_timeout"));
    }, 10_000);
    child.stdout?.setEncoding("utf8");
    child.stdout?.on("data", (chunk: string) => {
      output += chunk;
      if (output.split(/\r?\n/).includes(expected)) {
        clearTimeout(timeout);
        resolvePromise();
      }
    });
    child.once("error", (error) => {
      clearTimeout(timeout);
      rejectPromise(error);
    });
    child.once("exit", (code, signal) => {
      clearTimeout(timeout);
      rejectPromise(new Error(`child_exited_before_lock:${code}:${signal}`));
    });
  });
}

async function waitForPrefixedLine(child: ChildProcess, prefix: string) {
  return new Promise<string>((resolvePromise, rejectPromise) => {
    let output = "";
    const timeout = setTimeout(() => {
      rejectPromise(new Error("child_lock_timeout"));
    }, 10_000);
    child.stdout?.setEncoding("utf8");
    child.stdout?.on("data", (chunk: string) => {
      output += chunk;
      const line = output.split(/\r?\n/).find((candidate) =>
        candidate.startsWith(prefix));
      if (line) {
        clearTimeout(timeout);
        resolvePromise(line);
      }
    });
    child.once("error", (error) => {
      clearTimeout(timeout);
      rejectPromise(error);
    });
    child.once("exit", (code, signal) => {
      clearTimeout(timeout);
      rejectPromise(new Error(`child_exited_before_lock:${code}:${signal}`));
    });
  });
}

async function waitForExit(child: ChildProcess) {
  if (child.exitCode !== null || child.signalCode !== null) return;
  await new Promise<void>((resolvePromise, rejectPromise) => {
    const timeout = setTimeout(() => rejectPromise(new Error("child_exit_timeout")), 10_000);
    child.once("exit", () => {
      clearTimeout(timeout);
      resolvePromise();
    });
    child.once("error", (error) => {
      clearTimeout(timeout);
      rejectPromise(error);
    });
  });
}

async function leaveDeadOwnerLock(stateDirectory: string) {
  const childSource = `
    import { withLifecycleLock } from ${JSON.stringify(pathToFileURL(modulePath).href)};
    await withLifecycleLock(${JSON.stringify(stateDirectory)}, async () => {
      process.stdout.write("locked\\n");
      await new Promise(() => undefined);
    });
  `;
  const child = spawn(process.execPath, ["--input-type=module", "--eval", childSource], {
    stdio: ["ignore", "pipe", "pipe"],
    env: { ...process.env, SECRET_CANARY },
  });
  await waitForLine(child, "locked");
  child.kill("SIGKILL");
  await waitForExit(child);
  assert.equal(await lifecycleLockStatus(stateDirectory), true);
  return JSON.parse(
    await readFile(join(stateDirectory, ".operation.lock", "owner.json"), "utf8"),
  ) as Record<string, unknown>;
}

async function leaveDeadOwnerWithLiveChild(stateDirectory: string) {
  const childSource = `
    import { spawn } from "node:child_process";
    import {
      registerLifecycleChildProcess,
      withLifecycleLock,
    } from ${JSON.stringify(pathToFileURL(modulePath).href)};
    await withLifecycleLock(${JSON.stringify(stateDirectory)}, async () => {
      const child = spawn(process.execPath, [
        "--input-type=module",
        "--eval",
        "setInterval(() => undefined, 1000)",
      ], {
        detached: true,
        stdio: "ignore",
      });
      child.unref();
      await registerLifecycleChildProcess(
        ${JSON.stringify(stateDirectory)},
        child.pid,
        child.pid,
      );
      process.stdout.write("locked-with-child:" + child.pid + "\\n");
      await new Promise(() => undefined);
    });
  `;
  const owner = spawn(
    process.execPath,
    ["--input-type=module", "--eval", childSource],
    { stdio: ["ignore", "pipe", "pipe"] },
  );
  const line = await waitForPrefixedLine(owner, "locked-with-child:");
  const childPid = Number(line.slice("locked-with-child:".length));
  assert.ok(Number.isSafeInteger(childPid) && childPid > 1);
  owner.kill("SIGKILL");
  await waitForExit(owner);
  return childPid;
}

async function waitForProcessGroupExit(processGroupId: number) {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    try {
      process.kill(-processGroupId, 0);
    } catch (error: unknown) {
      if (
        error instanceof Error
        && "code" in error
        && error.code === "ESRCH"
      ) return;
    }
    await new Promise((resolveWait) => setTimeout(resolveWait, 20));
  }
  throw new Error("child_process_group_exit_timeout");
}

try {
  const normalDirectory = await createStateDirectory("normal-release");
  let competingRejected = false;
  const operationResult = await withLifecycleLock(normalDirectory, async () => {
    assert.equal(await lifecycleLockStatus(normalDirectory), true);
    const owner = JSON.parse(
      await readFile(join(normalDirectory, ".operation.lock", "owner.json"), "utf8"),
    ) as Record<string, unknown>;
    assert.equal(owner.contract, "agentops_byoc_lifecycle_lock_v2");
    assert.equal(owner.pid, process.pid);
    assert.equal(typeof owner.hostname, "string");
    assert.equal(Object.hasOwn(owner, "boot_id"), true);
    assert.equal(Object.hasOwn(owner, "process_start_identity"), true);
    assert.equal(typeof owner.owner_token, "string");
    assert.match(String(owner.owner_token), /^[0-9a-f]{64}$/);
    assert.equal(typeof owner.integrity_sha256, "string");
    assert.equal((await lstat(join(normalDirectory, ".operation.lock"))).mode & 0o077, 0);
    assert.equal(
      (await lstat(join(normalDirectory, ".operation.lock", "owner.json"))).mode & 0o077,
      0,
    );
    await expectError(
      () => recoverStaleLifecycleLock(normalDirectory, OPERATION_ID),
      /lifecycle_lock_recovery_(owner_alive|identity_unverifiable)/,
    );
    await expectError(
      () => withLifecycleLock(normalDirectory, async () => undefined),
      /lifecycle_operation_locked/,
    );
    competingRejected = true;
    return "normal-operation-result";
  });
  assert.equal(operationResult, "normal-operation-result");
  assert.equal(competingRejected, true);
  assert.equal(await lifecycleLockStatus(normalDirectory), false);
  assert.deepEqual(
    (await readdir(normalDirectory)).filter((name) => name.startsWith(".operation.lock")),
    [],
  );

  const deadDirectory = await createStateDirectory("dead-owner");
  const deadOwner = await leaveDeadOwnerLock(deadDirectory);
  const ownerToken = String(deadOwner.owner_token);
  await expectError(
    () => recoverStaleLifecycleLock(deadDirectory, WRONG_OPERATION_ID),
    /lifecycle_lock_recovery_confirmation_mismatch/,
  );
  await expectError(
    () => recoverStaleLifecycleLock(deadDirectory, ` ${OPERATION_ID}`),
    /lifecycle_lock_recovery_confirmation_mismatch/,
  );
  assert.equal(await lifecycleLockStatus(deadDirectory), true);
  const recovery = await recoverStaleLifecycleLock(deadDirectory, OPERATION_ID);
  assert.deepEqual(recovery, {
    contract: "agentops_byoc_lifecycle_lock_recovery_v1",
    ok: true,
    recovered: true,
    operation_id: OPERATION_ID,
    credentials_omitted: true,
  });
  assert.doesNotMatch(JSON.stringify(recovery), new RegExp(ownerToken, "i"));
  assert.doesNotMatch(JSON.stringify(recovery), new RegExp(SECRET_CANARY, "i"));
  assert.equal(await lifecycleLockStatus(deadDirectory), false);

  const cliDirectory = await createStateDirectory("cli-dead-owner");
  await leaveDeadOwnerLock(cliDirectory);
  const cliRecovery = await runLifecycle([
    "recover-lock",
    "--confirm-operation-id",
    OPERATION_ID,
  ], { stateDirectory: cliDirectory });
  assert.deepEqual(cliRecovery, {
    contract: "agentops_byoc_retained_data_lifecycle_v1",
    ok: true,
    operation: "recover-lock",
    operation_id: OPERATION_ID,
    stale_lock_recovered: true,
    owner_identity_verified_stale: true,
    credentials_omitted: true,
    sql_omitted: true,
    row_data_omitted: true,
  });
  assert.equal(await lifecycleLockStatus(cliDirectory), false);

  const liveChildDirectory = await createStateDirectory("live-child-owner");
  const liveChildPid = await leaveDeadOwnerWithLiveChild(liveChildDirectory);
  await expectError(
    () => recoverStaleLifecycleLock(liveChildDirectory, OPERATION_ID),
    /lifecycle_lock_recovery_child_alive/,
  );
  process.kill(-liveChildPid, "SIGKILL");
  await waitForProcessGroupExit(liveChildPid);
  const childRecovery = await recoverStaleLifecycleLock(
    liveChildDirectory,
    OPERATION_ID,
  );
  assert.equal(childRecovery.recovered, true);

  const isolatedDirectory = await createStateDirectory("isolated-dead-owner");
  await leaveDeadOwnerLock(isolatedDirectory);
  await rename(
    join(isolatedDirectory, ".operation.lock"),
    join(isolatedDirectory, ".operation.lock.isolated.release.fixture"),
  );
  assert.equal(await lifecycleLockStatus(isolatedDirectory), true);
  const isolatedRecovery = await recoverStaleLifecycleLock(
    isolatedDirectory,
    OPERATION_ID,
  );
  assert.equal(isolatedRecovery.recovered, true);
  assert.equal(await lifecycleLockStatus(isolatedDirectory), false);

  const tamperedDirectory = await createStateDirectory("tampered-owner");
  const tamperedOwner = await leaveDeadOwnerLock(tamperedDirectory);
  tamperedOwner.acquired_at = new Date(0).toISOString();
  await writeFile(
    join(tamperedDirectory, ".operation.lock", "owner.json"),
    `${JSON.stringify(tamperedOwner)}\n`,
  );
  await expectError(
    () => recoverStaleLifecycleLock(tamperedDirectory, OPERATION_ID),
    /lifecycle_lock_owner_invalid/,
  );
  assert.equal(await lifecycleLockStatus(tamperedDirectory), true);

  const symlinkDirectory = await createStateDirectory("symlink-lock");
  const symlinkTarget = join(testRoot, "symlink-target");
  await mkdir(symlinkTarget, { mode: 0o700 });
  await symlink(symlinkTarget, join(symlinkDirectory, ".operation.lock"));
  await expectError(
    () => recoverStaleLifecycleLock(symlinkDirectory, OPERATION_ID),
    /lifecycle_lock_path_invalid/,
  );

  const permissionDirectory = await createStateDirectory("permission-invalid");
  await leaveDeadOwnerLock(permissionDirectory);
  await chmod(join(permissionDirectory, ".operation.lock", "owner.json"), 0o644);
  await expectError(
    () => recoverStaleLifecycleLock(permissionDirectory, OPERATION_ID),
    /lifecycle_lock_owner_invalid/,
  );

  process.stdout.write(`${JSON.stringify({
    ok: true,
    contract: "agentops_byoc_retained_data_lifecycle_lock_behavior_v1",
    verifiable_owner_identity: true,
    atomic_private_lock_publication: true,
    live_owner_recovery_refused: true,
    dead_pid_recovery_verified: true,
    operator_cli_recovery_verified: true,
    live_child_process_group_recovery_refused: true,
    interrupted_isolation_recovery_verified: true,
    exact_operation_confirmation_verified: true,
    concurrent_acquisition_refused: true,
    tampered_owner_refused: true,
    symlink_lock_refused: true,
    invalid_permissions_refused: true,
    atomic_release_verified: true,
    credentials_omitted: true,
  })}\n`);
} finally {
  await rm(testRoot, { recursive: true, force: true });
}
