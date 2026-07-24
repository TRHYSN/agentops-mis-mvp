import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import {
  access,
  appendFile,
  chmod,
  cp,
  mkdir,
  mkdtemp,
  readFile,
  readdir,
  rm,
  symlink,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { setTimeout as delay } from "node:timers/promises";

type ShellResult = {
  code: number | null;
  signal: NodeJS.Signals | null;
  stdout: string;
  stderr: string;
};

const repositoryRoot = resolve(fileURLToPath(new URL("../../../", import.meta.url)));
const backupScript = join(repositoryRoot, "deploy/byoc/backup.sh");
const restoreScript = join(repositoryRoot, "deploy/byoc/restore-drill.sh");
const secretSentinel = "byoc-fixture-password-must-not-leak";
const dsnSentinel = [
  "postgresql",
  "://fixture:",
  secretSentinel,
  "@database.invalid/authority",
].join("");
let activeCheck = "fixture_setup";

function startShell(
  script: string,
  args: string[],
  environment: NodeJS.ProcessEnv,
) {
  const child = spawn("sh", [script, ...args], {
    cwd: repositoryRoot,
    env: environment,
    stdio: ["ignore", "pipe", "pipe"],
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

  const result = new Promise<ShellResult>((resolveResult, reject) => {
    const timeout = setTimeout(() => {
      child.kill("SIGKILL");
    }, 15_000);
    child.once("error", (error) => {
      clearTimeout(timeout);
      reject(error);
    });
    child.once("close", (code, signal) => {
      clearTimeout(timeout);
      resolveResult({ code, signal, stdout, stderr });
    });
  });

  return { child, result };
}

async function runShell(
  script: string,
  args: string[],
  environment: NodeJS.ProcessEnv,
) {
  return startShell(script, args, environment).result;
}

async function pathExists(path: string) {
  try {
    await access(path);
    return true;
  } catch {
    return false;
  }
}

async function waitForPath(path: string) {
  for (let attempt = 0; attempt < 200; attempt += 1) {
    if (await pathExists(path)) {
      return;
    }
    await delay(25);
  }
  assert.fail("fixture_gate_timeout");
}

function assertNoSecretOutput(result: ShellResult) {
  const output = `${result.stdout}\n${result.stderr}`;
  assert.doesNotMatch(output, /postgresql:\/\//);
  assert.equal(output.includes(secretSentinel), false);
  assert.equal(output.includes(dsnSentinel), false);
}

function assertFailed(result: ShellResult, expectedError?: RegExp) {
  assert.notEqual(result.code, 0);
  if (expectedError) {
    assert.match(result.stderr, expectedError);
  }
  assert.doesNotMatch(result.stdout, /"ok":true/);
  assertNoSecretOutput(result);
}

function assertSucceeded(result: ShellResult) {
  assert.equal(result.code, 0);
  assert.equal(result.signal, null);
  assert.match(result.stdout, /"ok":true/);
  assertNoSecretOutput(result);
}

async function logLines(path: string) {
  const value = await readFile(path, "utf8");
  return value.split("\n").filter(Boolean);
}

async function run() {
  const fixtureRoot = await mkdtemp(join(tmpdir(), "agentops-byoc-contract-"));
  try {
    const fixtureBin = join(fixtureRoot, "bin");
    const dockerLog = join(fixtureRoot, "docker.log");
    const databaseState = join(fixtureRoot, "databases");
    await mkdir(fixtureBin, { mode: 0o700 });
    await mkdir(databaseState, { mode: 0o700 });
    await writeFile(dockerLog, "", { mode: 0o600 });

    const fakeDocker = [
      "#!/bin/sh",
      "set -eu",
      ': "${FAKE_DOCKER_LOG:?}"',
      ': "${FAKE_DB_STATE:?}"',
      "log() {",
      '  printf "%s\\n" "$1" >> "$FAKE_DOCKER_LOG"',
      "}",
      "last=",
      'for value in "$@"; do',
      "  last=$value",
      "done",
      "command_line=$*",
      'case "$command_line" in',
      "  *pg_dump*)",
      '    log "pg_dump"',
      '    if [ "${FAKE_PG_DUMP_FAIL:-false}" = true ]; then',
      "      printf 'partial dump bytes\\n'",
      "      exit 40",
      "    fi",
      '    if [ -n "${FAKE_BACKUP_GATE:-}" ]; then',
      '      : > "${FAKE_BACKUP_GATE}.started"',
      "      attempts=0",
      '      while [ ! -e "${FAKE_BACKUP_GATE}.release" ]; do',
      "        attempts=$((attempts + 1))",
      '        [ "$attempts" -lt 200 ] || exit 70',
      "        sleep 0.05",
      "      done",
      "    fi",
      "    printf 'fixture custom dump payload\\n'",
      "    ;;",
      '  *\'printf "%s" "$POSTGRES_DB"\'*)',
      '    printf "%s" "${FAKE_PRODUCTION_DB:-agentops_production}"',
      "    ;;",
      "  *createdb*)",
      '    log "createdb:$last"',
      '    mkdir "$FAKE_DB_STATE/$last"',
      "    ;;",
      "  *pg_restore*)",
      "    cat >/dev/null",
      '    log "pg_restore:$last"',
      '    [ "${FAKE_PG_RESTORE_FAIL:-false}" != true ] || exit 41',
      '    [ -d "$FAKE_DB_STATE/$last" ] || exit 42',
      "    ;;",
      "  *dropdb*)",
      '    log "dropdb:$last"',
      '    if [ "${FAKE_DROP_FAIL:-false}" = true ]; then',
      '      printf "%s\\n" "$POSTGRES_PASSWORD $AGENTOPS_POSTGRES_DSN" >&2',
      "      exit 43",
      "    fi",
      '    rmdir "$FAKE_DB_STATE/$last"',
      "    ;;",
      "  *'npm run check:postgres-schema'*)",
      '    log "schema_check:$last"',
      '    if [ "${FAKE_SCHEMA_FAIL:-false}" = true ]; then',
      '      printf "%s\\n" "$POSTGRES_PASSWORD $AGENTOPS_POSTGRES_DSN" >&2',
      "      exit 44",
      "    fi",
      '    [ -d "$FAKE_DB_STATE/$last" ] || exit 45',
      "    ;;",
      "  *)",
      "    printf '%s\\n' fake_docker_unexpected >&2",
      "    exit 64",
      "    ;;",
      "esac",
      "",
    ].join("\n");
    const fakeDockerPath = join(fixtureBin, "docker");
    await writeFile(fakeDockerPath, fakeDocker, { mode: 0o700 });
    await chmod(fakeDockerPath, 0o700);

    const baseEnvironment: NodeJS.ProcessEnv = {
      ...process.env,
      PATH: `${fixtureBin}:${process.env.PATH ?? ""}`,
      AGENTOPS_BYOC_COMPOSE_FILE: "fixture-compose.yaml",
      AGENTOPS_BYOC_ENV_FILE: "fixture.env",
      FAKE_DOCKER_LOG: dockerLog,
      FAKE_DB_STATE: databaseState,
      FAKE_PRODUCTION_DB: "agentops_production",
      POSTGRES_PASSWORD: secretSentinel,
      AGENTOPS_POSTGRES_DSN: dsnSentinel,
      AGENTOPS_POSTGRES_HOST: "postgres",
      AGENTOPS_POSTGRES_PASSWORD_FILE:
        "/run/secrets/postgres_password",
    };

    const concurrentBundle = join(fixtureRoot, "concurrent.bundle");
    const backupGate = join(fixtureRoot, "backup-gate");
    activeCheck = "concurrent_backup";
    const firstBackup = startShell(backupScript, [concurrentBundle], {
      ...baseEnvironment,
      FAKE_BACKUP_GATE: backupGate,
    });
    await waitForPath(`${backupGate}.started`);
    assert.equal(await pathExists(join(concurrentBundle, "COMMITTED")), false);
    const incompleteRestore = await runShell(
      restoreScript,
      [concurrentBundle],
      {
        ...baseEnvironment,
        AGENTOPS_RESTORE_DATABASE: "restore_incomplete_bundle",
      },
    );
    assertFailed(incompleteRestore, /restore_bundle_incomplete/);

    const competingBackup = await runShell(
      backupScript,
      [concurrentBundle],
      baseEnvironment,
    );
    assertFailed(competingBackup, /backup_output_exists/);
    await writeFile(`${backupGate}.release`, "", { mode: 0o600 });

    const firstBackupResult = await firstBackup.result;
    assertSucceeded(firstBackupResult);
    assert.deepEqual(
      (await readdir(concurrentBundle)).sort(),
      ["COMMITTED", "SHA256SUMS", "database.dump"],
    );

    const failedBackupBundle = join(fixtureRoot, "failed.bundle");
    activeCheck = "backup_failure_unpublished";
    const failedBackup = await runShell(
      backupScript,
      [failedBackupBundle],
      {
        ...baseEnvironment,
        FAKE_PG_DUMP_FAIL: "true",
      },
    );
    assertFailed(failedBackup);
    assert.equal(await pathExists(failedBackupBundle), false);

    const existingOutput = join(fixtureRoot, "existing.bundle");
    activeCheck = "existing_output";
    await mkdir(existingOutput, { mode: 0o700 });
    await writeFile(join(existingOutput, "sentinel"), "unchanged", {
      mode: 0o600,
    });
    const existingResult = await runShell(
      backupScript,
      [existingOutput],
      baseEnvironment,
    );
    assertFailed(existingResult, /backup_output_exists/);
    assert.equal(
      await readFile(join(existingOutput, "sentinel"), "utf8"),
      "unchanged",
    );

    const symlinkTarget = join(fixtureRoot, "symlink-target");
    const symlinkOutput = join(fixtureRoot, "symlink.bundle");
    activeCheck = "symlink_output";
    await mkdir(symlinkTarget, { mode: 0o700 });
    await symlink(symlinkTarget, symlinkOutput, "dir");
    const symlinkResult = await runShell(
      backupScript,
      [symlinkOutput],
      baseEnvironment,
    );
    assertFailed(symlinkResult, /backup_output_exists/);
    assert.deepEqual(await readdir(symlinkTarget), []);

    const validBundle = join(fixtureRoot, "valid.bundle");
    activeCheck = "valid_backup";
    const validBackup = await runShell(
      backupScript,
      [validBundle],
      baseEnvironment,
    );
    assertSucceeded(validBackup);

    const internalSymlinkBundle = join(
      fixtureRoot,
      "internal-symlink.bundle",
    );
    activeCheck = "internal_symlink";
    await cp(validBundle, internalSymlinkBundle, { recursive: true });
    await rm(join(internalSymlinkBundle, "database.dump"));
    await symlink(
      join(validBundle, "database.dump"),
      join(internalSymlinkBundle, "database.dump"),
    );
    const internalSymlinkRestore = await runShell(
      restoreScript,
      [internalSymlinkBundle],
      {
        ...baseEnvironment,
        AGENTOPS_RESTORE_DATABASE: "restore_internal_symlink",
      },
    );
    assertFailed(internalSymlinkRestore, /restore_bundle_incomplete/);

    const tamperedBundle = join(fixtureRoot, "tampered.bundle");
    activeCheck = "checksum_tamper";
    await cp(validBundle, tamperedBundle, { recursive: true });
    await appendFile(
      join(tamperedBundle, "database.dump"),
      "tampered",
      "utf8",
    );
    const logBeforeTamper = await readFile(dockerLog, "utf8");
    const tamperedRestore = await runShell(
      restoreScript,
      [tamperedBundle],
      {
        ...baseEnvironment,
        AGENTOPS_RESTORE_DATABASE: "restore_tampered",
      },
    );
    assertFailed(tamperedRestore, /restore_checksum_mismatch/);
    assert.equal(await readFile(dockerLog, "utf8"), logBeforeTamper);

    const failedRestoreDatabase = "restore_failure_cleanup";
    activeCheck = "restore_failure_cleanup";
    const failedRestore = await runShell(restoreScript, [validBundle], {
      ...baseEnvironment,
      AGENTOPS_RESTORE_DATABASE: failedRestoreDatabase,
      AGENTOPS_RESTORE_KEEP: "true",
      FAKE_PG_RESTORE_FAIL: "true",
    });
    assertFailed(failedRestore);
    assert.equal(
      await pathExists(join(databaseState, failedRestoreDatabase)),
      false,
    );
    const failedRestoreLog = await logLines(dockerLog);
    assert.equal(
      failedRestoreLog.includes(`createdb:${failedRestoreDatabase}`),
      true,
    );
    assert.equal(
      failedRestoreLog.includes(`dropdb:${failedRestoreDatabase}`),
      true,
    );

    const failedSchemaDatabase = "schema_failure_cleanup";
    activeCheck = "schema_failure_cleanup";
    const failedSchema = await runShell(restoreScript, [validBundle], {
      ...baseEnvironment,
      AGENTOPS_RESTORE_DATABASE: failedSchemaDatabase,
      AGENTOPS_RESTORE_KEEP: "true",
      FAKE_SCHEMA_FAIL: "true",
    });
    assertFailed(failedSchema);
    assert.equal(
      await pathExists(join(databaseState, failedSchemaDatabase)),
      false,
    );

    const dropFailureDatabase = "restore_drop_failure";
    activeCheck = "drop_failure_fail_closed";
    const dropFailure = await runShell(restoreScript, [validBundle], {
      ...baseEnvironment,
      AGENTOPS_RESTORE_DATABASE: dropFailureDatabase,
      AGENTOPS_RESTORE_KEEP: "false",
      FAKE_DROP_FAIL: "true",
    });
    assertFailed(dropFailure, /restore_cleanup_failed/);
    assert.equal(
      await pathExists(join(databaseState, dropFailureDatabase)),
      true,
    );

    const cleanupDatabase = "restore_success_cleanup";
    activeCheck = "success_cleanup";
    const successfulCleanup = await runShell(restoreScript, [validBundle], {
      ...baseEnvironment,
      AGENTOPS_RESTORE_DATABASE: cleanupDatabase,
      AGENTOPS_RESTORE_KEEP: "false",
    });
    assertSucceeded(successfulCleanup);
    assert.match(successfulCleanup.stdout, /"cleanup_confirmed":true/);
    assert.match(
      successfulCleanup.stdout,
      /"schema_fingerprint_verified":true/,
    );
    assert.match(
      successfulCleanup.stdout,
      /"restore_database_kept":false/,
    );
    assert.equal(await pathExists(join(databaseState, cleanupDatabase)), false);

    const keptDatabase = "restore_success_keep";
    activeCheck = "success_keep";
    const keptRestore = await runShell(restoreScript, [validBundle], {
      ...baseEnvironment,
      AGENTOPS_RESTORE_DATABASE: keptDatabase,
      AGENTOPS_RESTORE_KEEP: "true",
    });
    assertSucceeded(keptRestore);
    assert.match(keptRestore.stdout, /"restore_database_kept":true/);
    assert.match(keptRestore.stdout, /"cleanup_confirmed":false/);
    assert.match(
      keptRestore.stdout,
      /"restore_disposition_confirmed":true/,
    );
    assert.equal(await pathExists(join(databaseState, keptDatabase)), true);
    const finalLog = await logLines(dockerLog);
    assert.equal(finalLog.includes(`dropdb:${keptDatabase}`), false);

    console.log(JSON.stringify({
      ok: true,
      contract: "agentops_byoc_backup_restore_behavior_v1",
      concurrent_backup_fail_closed: true,
      backup_failure_unpublished: true,
      existing_output_fail_closed: true,
      symlink_output_fail_closed: true,
      internal_symlink_fail_closed: true,
      checksum_tamper_rejected_before_restore: true,
      restore_failure_cleanup: true,
      schema_failure_cleanup: true,
      schema_fingerprint_verified: true,
      drop_failure_fail_closed: true,
      success_cleanup_confirmed: true,
      keep_requires_success_and_explicit_true: true,
      credentials_omitted: true,
    }));
  } finally {
    await rm(fixtureRoot, { recursive: true, force: true });
  }
}

run().catch(() => {
  console.error(JSON.stringify({
    ok: false,
    contract: "agentops_byoc_backup_restore_behavior_v1",
    error: "byoc_backup_restore_behavior_contract_failed",
    failed_check: activeCheck,
    credentials_omitted: true,
  }));
  process.exitCode = 1;
});
