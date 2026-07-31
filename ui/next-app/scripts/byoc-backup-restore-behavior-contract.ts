import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import {
  access,
  appendFile,
  chmod,
  cp,
  lstat,
  mkdir,
  mkdtemp,
  readFile,
  readdir,
  rename,
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
const restoreProvisionScript = join(
  repositoryRoot,
  "deploy/byoc/restore-provision.sh",
);
const restoreDsnScript = join(
  repositoryRoot,
  "deploy/byoc/postgres-dsn-for-restore.mjs",
);
const secretEntrypoint = join(
  repositoryRoot,
  "deploy/byoc/node-secret-entrypoint.mjs",
);
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

async function runNode(
  args: string[],
  environment: NodeJS.ProcessEnv,
) {
  const child = spawn(process.execPath, args, {
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
  return new Promise<ShellResult>((resolveResult, reject) => {
    child.once("error", reject);
    child.once("close", (code, signal) => {
      resolveResult({ code, signal, stdout, stderr });
    });
  });
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
    const provisionLog = join(fixtureRoot, "provision.log");
    const databaseState = join(fixtureRoot, "databases");
    const fakeNextApp = join(fixtureRoot, "next-app");
    const fakeNextAppBin = join(fakeNextApp, "node_modules/.bin");
    await mkdir(fixtureBin, { mode: 0o700 });
    await mkdir(databaseState, { mode: 0o700 });
    await mkdir(fakeNextAppBin, { mode: 0o700, recursive: true });
    await writeFile(dockerLog, "", { mode: 0o600 });
    await writeFile(provisionLog, "", { mode: 0o600 });

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
      '    if [ -n "${FAKE_CREATEDB_GATE:-}" ]; then',
      '      : > "${FAKE_CREATEDB_GATE}.started"',
      "      attempts=0",
      '      while [ ! -e "${FAKE_CREATEDB_GATE}.release" ]; do',
      "        attempts=$((attempts + 1))",
      '        [ "$attempts" -lt 200 ] || exit 71',
      "        sleep 0.05",
      "      done",
      "    fi",
      "    ;;",
      "  *pg_restore*)",
      '    if [ -n "${FAKE_RESTORE_INPUT:-}" ]; then',
      '      cat > "$FAKE_RESTORE_INPUT"',
      "    else",
      "      cat >/dev/null",
      "    fi",
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
      "  *restore-provision.sh*)",
      '    [ "${AGENTOPS_POSTGRES_MIGRATOR_HOST:-}" = "postgres" ] || exit 65',
      '    [ -n "${AGENTOPS_POSTGRES_MIGRATOR_PASSWORD_FILE:-}" ] || exit 65',
      '    [ -n "${AGENTOPS_POSTGRES_RUNTIME_PASSWORD_FILE:-}" ] || exit 65',
      '    [ -n "${AGENTOPS_POSTGRES_ENTITLEMENT_ADMIN_PASSWORD_FILE:-}" ] || exit 65',
      '    log "provision_source:migrator_components:$last"',
      '    log "migrate:$last"',
      '    if [ "${FAKE_MIGRATE_FAIL:-false}" = true ]; then',
      "      exit 71",
      "    fi",
      '    [ -d "$FAKE_DB_STATE/$last" ] || exit 45',
      '    log "schema_check:$last"',
      '    if [ "${FAKE_SCHEMA_FAIL:-false}" = true ]; then',
      '      printf "%s\\n" "$POSTGRES_PASSWORD $AGENTOPS_POSTGRES_DSN" >&2',
      "      exit 72",
      "    fi",
      '    log "runtime_boundary:$last"',
      '    if [ "${FAKE_RUNTIME_BOUNDARY_FAIL:-false}" = true ]; then',
      "      exit 73",
      "    fi",
      '    log "entitlement_admin_boundary:$last"',
      '    if [ "${FAKE_ADMIN_BOUNDARY_FAIL:-false}" = true ]; then',
      "      exit 74",
      "    fi",
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

    const fakeNpm = [
      "#!/bin/sh",
      "set -eu",
      ': "${FAKE_PROVISION_LOG:?}"',
      ': "${FAKE_EXPECTED_RESTORE_DB:?}"',
      'log() { printf "%s\\n" "$1" >> "$FAKE_PROVISION_LOG"; }',
      'case "$*" in',
      "  *migrate:postgres*)",
      '    [ -n "${AGENTOPS_POSTGRES_RUNTIME_PASSWORD_FILE:-}" ] || exit 81',
      '    [ -f "${AGENTOPS_POSTGRES_RUNTIME_PASSWORD_FILE:-}" ] || exit 82',
      '    [ -n "${AGENTOPS_POSTGRES_ENTITLEMENT_ADMIN_PASSWORD_FILE:-}" ] || exit 83',
      '    [ -f "${AGENTOPS_POSTGRES_ENTITLEMENT_ADMIN_PASSWORD_FILE:-}" ] || exit 84',
      '    [ -f "${AGENTOPS_POSTGRES_MIGRATOR_DSN_FILE:-}" ] || exit 85',
      '    [ -z "${AGENTOPS_POSTGRES_DSN_FILE:-}" ] || exit 86',
      '    [ -z "${AGENTOPS_POSTGRES_ENTITLEMENT_ADMIN_DSN_FILE:-}" ] || exit 87',
      '    [ -z "${AGENTOPS_POSTGRES_MIGRATOR_HOST:-}" ] || exit 88',
      '    [ -z "${AGENTOPS_POSTGRES_MIGRATOR_PASSWORD_FILE:-}" ] || exit 89',
      '    node -e \'const {readFileSync}=require("fs");const u=new URL(readFileSync(process.env.AGENTOPS_POSTGRES_MIGRATOR_DSN_FILE,"utf8"));if(u.pathname!==`/${process.env.FAKE_EXPECTED_RESTORE_DB}`)process.exit(90);if(process.env.FAKE_EXPECT_COMPONENT_DSN==="true"&&(u.hostname!=="postgres"||u.port!=="5432"||u.username!=="agentops_migrator"))process.exit(91)\'',
      '    log "migrate:derived_migrator_dsn_only"',
      '    if [ -n "${FAKE_PROVISION_GATE:-}" ]; then',
      '      : > "${FAKE_PROVISION_GATE}.started"',
      '      attempts=0',
      '      while [ ! -e "${FAKE_PROVISION_GATE}.release" ]; do',
      "        attempts=$((attempts + 1))",
      '        [ "$attempts" -lt 200 ] || exit 92',
      "        sleep 0.05",
      "      done",
      "    fi",
      '    [ "${FAKE_MIGRATE_FAIL:-false}" != true ] || exit 1',
      "    ;;",
      "  *check:postgres-schema*)",
      '    [ -f "${AGENTOPS_POSTGRES_DSN_FILE:-}" ] || exit 93',
      '    [ -f "${AGENTOPS_POSTGRES_ENTITLEMENT_ADMIN_DSN_FILE:-}" ] || exit 94',
      '    [ -z "${AGENTOPS_POSTGRES_ENTITLEMENT_ADMIN_PASSWORD_FILE:-}" ] || exit 95',
      '    [ -z "${AGENTOPS_POSTGRES_RUNTIME_PASSWORD_FILE:-}" ] || exit 96',
      '    [ -z "${AGENTOPS_POSTGRES_DSN:-}" ] || exit 97',
      '    [ -z "${AGENTOPS_POSTGRES_ENTITLEMENT_ADMIN_DSN:-}" ] || exit 98',
      '    log "schema_check:derived_runtime_dsn_only"',
      '    [ "${FAKE_SCHEMA_FAIL:-false}" != true ] || exit 1',
      "    ;;",
      "  *) exit 99 ;;",
      "esac",
      "",
    ].join("\n");
    const fakeNpmPath = join(fixtureBin, "npm");
    await writeFile(fakeNpmPath, fakeNpm, { mode: 0o700 });
    await chmod(fakeNpmPath, 0o700);

    const fakeTsx = [
      "#!/bin/sh",
      "set -eu",
      ': "${FAKE_PROVISION_LOG:?}"',
      "boundary=",
      'for value in "$@"; do boundary=$value; done',
      'case "$boundary" in runtime|entitlement-admin) ;; *) exit 99 ;; esac',
      '[ -z "${AGENTOPS_POSTGRES_DSN:-}" ] || exit 100',
      '[ -z "${AGENTOPS_POSTGRES_ENTITLEMENT_ADMIN_DSN:-}" ] || exit 101',
      '[ -z "${AGENTOPS_POSTGRES_PASSWORD:-}" ] || exit 102',
      '[ -z "${AGENTOPS_POSTGRES_RUNTIME_PASSWORD:-}" ] || exit 103',
      '[ -z "${AGENTOPS_POSTGRES_ENTITLEMENT_ADMIN_PASSWORD:-}" ] || exit 104',
      '[ -z "${AGENTOPS_POSTGRES_ENTITLEMENT_ADMIN_PASSWORD_FILE:-}" ] || exit 105',
      '[ -f "${AGENTOPS_POSTGRES_DSN_FILE:-}" ] || exit 106',
      '[ -f "${AGENTOPS_POSTGRES_ENTITLEMENT_ADMIN_DSN_FILE:-}" ] || exit 107',
      '[ -z "${AGENTOPS_POSTGRES_MIGRATOR_DSN_FILE:-}" ] || exit 108',
      '[ -z "${AGENTOPS_POSTGRES_MIGRATOR_PASSWORD_FILE:-}" ] || exit 109',
      'if [ "${FAKE_FUNCTION_OWNER_EVIDENCE_MISSING:-false}" = true ]; then',
      "  exit 1",
      "fi",
      'if [ "$boundary" = runtime ] && [ "${FAKE_RUNTIME_BOUNDARY_FAIL:-false}" = true ]; then exit 1; fi',
      'if [ "$boundary" = entitlement-admin ] && [ "${FAKE_ADMIN_BOUNDARY_FAIL:-false}" = true ]; then exit 1; fi',
      'printf "boundary:%s:function_owner_restricted\\n" "$boundary" >> "$FAKE_PROVISION_LOG"',
      "",
    ].join("\n");
    const fakeTsxPath = join(fakeNextAppBin, "tsx");
    await writeFile(fakeTsxPath, fakeTsx, { mode: 0o700 });
    await chmod(fakeTsxPath, 0o700);

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
      AGENTOPS_POSTGRES_MIGRATOR_HOST: "postgres",
      AGENTOPS_POSTGRES_MIGRATOR_PORT: "5432",
      AGENTOPS_POSTGRES_MIGRATOR_USER: "agentops_migrator",
      AGENTOPS_POSTGRES_MIGRATOR_PASSWORD_FILE:
        "/run/secrets/postgres_migrator_password",
      AGENTOPS_POSTGRES_RUNTIME_ROLE: "agentops_runtime",
      AGENTOPS_POSTGRES_RUNTIME_PASSWORD_FILE:
        "/run/secrets/postgres_runtime_password",
      AGENTOPS_POSTGRES_ENTITLEMENT_ADMIN_ROLE:
        "agentops_entitlement_admin",
      AGENTOPS_POSTGRES_ENTITLEMENT_ADMIN_PASSWORD_FILE:
        "/run/secrets/postgres_entitlement_admin_password",
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

    const stableBundle = join(fixtureRoot, "stable.bundle");
    const replacedDump = join(fixtureRoot, "replaced-after-validation.dump");
    const restoreInput = join(fixtureRoot, "stable-restore-input.dump");
    const createdbGate = join(fixtureRoot, "createdb-gate");
    const stableDatabase = "restore_stable_object";
    activeCheck = "stable_restore_object";
    await cp(validBundle, stableBundle, { recursive: true });
    const expectedRestoreInput = await readFile(
      join(stableBundle, "database.dump"),
    );
    const stableRestore = startShell(restoreScript, [stableBundle], {
      ...baseEnvironment,
      AGENTOPS_RESTORE_DATABASE: stableDatabase,
      FAKE_CREATEDB_GATE: createdbGate,
      FAKE_RESTORE_INPUT: restoreInput,
    });
    await waitForPath(`${createdbGate}.started`);
    await rename(join(stableBundle, "database.dump"), replacedDump);
    await writeFile(
      join(stableBundle, "database.dump"),
      "replacement after checksum validation\n",
      { mode: 0o600 },
    );
    await writeFile(`${createdbGate}.release`, "", { mode: 0o600 });
    const stableRestoreResult = await stableRestore.result;
    assertSucceeded(stableRestoreResult);
    assert.match(
      stableRestoreResult.stdout,
      /"staged_object_verified":true/,
    );
    assert.match(
      stableRestoreResult.stdout,
      /"staged_object_read_only":true/,
    );
    assert.deepEqual(await readFile(restoreInput), expectedRestoreInput);
    assert.equal(await pathExists(join(databaseState, stableDatabase)), false);

    activeCheck = "restore_dsn_rewrite";
    const directDsnEnvironment = { ...process.env };
    delete directDsnEnvironment.AGENTOPS_POSTGRES_DSN_FILE;
    directDsnEnvironment.AGENTOPS_POSTGRES_DSN =
      `${dsnSentinel}?sslmode=verify-full&application_name=restore-contract`;
    const directDsnProgram = [
      `import { postgresDsnForRestore } from ${JSON.stringify(
        `file://${restoreDsnScript}`,
      )};`,
      'const parsed = new URL(postgresDsnForRestore("restore_query_preserved"));',
      "process.stdout.write(JSON.stringify({",
      "  pathname: parsed.pathname,",
      '  sslmode: parsed.searchParams.get("sslmode"),',
      '  applicationName: parsed.searchParams.get("application_name"),',
      "}));",
    ].join("\n");
    const directDsnResult = await runNode(
      ["--input-type=module", "--eval", directDsnProgram],
      directDsnEnvironment,
    );
    assert.equal(directDsnResult.code, 0);
    const directDsn = JSON.parse(directDsnResult.stdout);
    assert.equal(directDsn.pathname, "/restore_query_preserved");
    assert.equal(directDsn.sslmode, "verify-full");
    assert.equal(directDsn.applicationName, "restore-contract");

    const dsnFile = join(fixtureRoot, "postgres-dsn");
    await writeFile(
      dsnFile,
      `${dsnSentinel}?sslmode=require&connect_timeout=9\n`,
      { mode: 0o600 },
    );
    const fileDsnEnvironment = { ...process.env };
    delete fileDsnEnvironment.AGENTOPS_POSTGRES_DSN;
    fileDsnEnvironment.AGENTOPS_POSTGRES_DSN_FILE = dsnFile;
    const restoreDsnFile = join(fixtureRoot, "postgres-restore-dsn");
    const fileDsnResult = await runNode(
      [restoreDsnScript, "restore_from_file", restoreDsnFile],
      fileDsnEnvironment,
    );
    assert.equal(fileDsnResult.code, 0);
    assert.equal(fileDsnResult.stdout, "");
    const restoreDsnState = await lstat(restoreDsnFile);
    assert.equal(restoreDsnState.mode & 0o777, 0o400);
    const fileDsn = new URL(await readFile(restoreDsnFile, "utf8"));
    assert.equal(fileDsn.pathname, "/restore_from_file");
    assert.equal(fileDsn.searchParams.get("sslmode"), "require");
    assert.equal(fileDsn.searchParams.get("connect_timeout"), "9");

    const migratorDsnFile = join(fixtureRoot, "postgres-migrator-dsn");
    const runtimePasswordFile = join(fixtureRoot, "postgres-runtime-password");
    const adminPasswordFile = join(fixtureRoot, "postgres-admin-password");
    await writeFile(
      migratorDsnFile,
      `${dsnSentinel}?sslmode=verify-full&application_name=byoc-restore\n`,
      { mode: 0o400 },
    );
    await writeFile(runtimePasswordFile, `${secretSentinel}-runtime\n`, {
      mode: 0o400,
    });
    await writeFile(adminPasswordFile, `${secretSentinel}-admin\n`, {
      mode: 0o400,
    });
    const roleDsnEnvironment: NodeJS.ProcessEnv = {
      ...process.env,
      AGENTOPS_POSTGRES_MIGRATOR_DSN_FILE: migratorDsnFile,
      AGENTOPS_POSTGRES_USER: "agentops_runtime_restore",
      AGENTOPS_POSTGRES_PASSWORD_FILE: runtimePasswordFile,
      AGENTOPS_POSTGRES_ENTITLEMENT_ADMIN_USER:
        "agentops_entitlement_admin_restore",
      AGENTOPS_POSTGRES_ENTITLEMENT_ADMIN_PASSWORD_FILE: adminPasswordFile,
    };
    const runtimeRestoreDsnFile = join(
      fixtureRoot,
      "postgres-runtime-restore-dsn",
    );
    const runtimeRoleDsnResult = await runNode(
      [
        restoreDsnScript,
        "restore_role_profiles",
        runtimeRestoreDsnFile,
        "migrator",
        "runtime",
      ],
      roleDsnEnvironment,
    );
    assert.equal(runtimeRoleDsnResult.code, 0);
    assertNoSecretOutput(runtimeRoleDsnResult);
    const runtimeRoleDsn = new URL(
      await readFile(runtimeRestoreDsnFile, "utf8"),
    );
    assert.equal(runtimeRoleDsn.pathname, "/restore_role_profiles");
    assert.equal(runtimeRoleDsn.username, "agentops_runtime_restore");
    assert.equal(runtimeRoleDsn.password, `${secretSentinel}-runtime`);
    assert.equal(runtimeRoleDsn.searchParams.get("sslmode"), "verify-full");
    assert.equal(
      runtimeRoleDsn.searchParams.get("application_name"),
      "byoc-restore",
    );
    assert.equal((await lstat(runtimeRestoreDsnFile)).mode & 0o777, 0o400);

    const adminRestoreDsnFile = join(
      fixtureRoot,
      "postgres-admin-restore-dsn",
    );
    const adminRoleDsnResult = await runNode(
      [
        restoreDsnScript,
        "restore_role_profiles",
        adminRestoreDsnFile,
        "migrator",
        "entitlement-admin",
      ],
      roleDsnEnvironment,
    );
    assert.equal(adminRoleDsnResult.code, 0);
    assertNoSecretOutput(adminRoleDsnResult);
    const adminRoleDsn = new URL(
      await readFile(adminRestoreDsnFile, "utf8"),
    );
    assert.equal(adminRoleDsn.pathname, "/restore_role_profiles");
    assert.equal(
      adminRoleDsn.username,
      "agentops_entitlement_admin_restore",
    );
    assert.equal(adminRoleDsn.password, `${secretSentinel}-admin`);
    assert.equal(adminRoleDsn.searchParams.get("sslmode"), "verify-full");
    assert.equal((await lstat(adminRestoreDsnFile)).mode & 0o777, 0o400);

    const migratorPasswordFile = join(
      fixtureRoot,
      "postgres-migrator-password",
    );
    await writeFile(migratorPasswordFile, `${secretSentinel}-migrator\n`, {
      mode: 0o400,
    });
    const cleanProvisionEnvironment: NodeJS.ProcessEnv = { ...process.env };
    for (const name of [
      "AGENTOPS_POSTGRES_MIGRATOR_DSN",
      "AGENTOPS_POSTGRES_MIGRATOR_DSN_FILE",
      "AGENTOPS_POSTGRES_DSN",
      "AGENTOPS_POSTGRES_DSN_FILE",
      "AGENTOPS_POSTGRES_ENTITLEMENT_ADMIN_DSN",
      "AGENTOPS_POSTGRES_ENTITLEMENT_ADMIN_DSN_FILE",
      "AGENTOPS_POSTGRES_MIGRATOR_PASSWORD",
      "AGENTOPS_POSTGRES_PASSWORD",
      "AGENTOPS_POSTGRES_RUNTIME_PASSWORD",
      "AGENTOPS_POSTGRES_ENTITLEMENT_ADMIN_PASSWORD",
      "AGENTOPS_POSTGRES_MIGRATOR_HOST",
      "AGENTOPS_POSTGRES_MIGRATOR_PORT",
      "AGENTOPS_POSTGRES_MIGRATOR_DATABASE",
      "AGENTOPS_POSTGRES_MIGRATOR_USER",
      "AGENTOPS_POSTGRES_MIGRATOR_PASSWORD_FILE",
      "AGENTOPS_POSTGRES_HOST",
      "AGENTOPS_POSTGRES_PORT",
      "AGENTOPS_POSTGRES_DATABASE",
      "AGENTOPS_POSTGRES_USER",
      "AGENTOPS_POSTGRES_PASSWORD_FILE",
      "AGENTOPS_POSTGRES_ENTITLEMENT_ADMIN_HOST",
      "AGENTOPS_POSTGRES_ENTITLEMENT_ADMIN_PORT",
      "AGENTOPS_POSTGRES_ENTITLEMENT_ADMIN_DATABASE",
      "AGENTOPS_POSTGRES_ENTITLEMENT_ADMIN_USER",
      "AGENTOPS_POSTGRES_RUNTIME_PASSWORD_FILE",
      "AGENTOPS_POSTGRES_ENTITLEMENT_ADMIN_PASSWORD_FILE",
    ]) {
      delete cleanProvisionEnvironment[name];
    }
    Object.assign(cleanProvisionEnvironment, {
      PATH: `${fixtureBin}:${process.env.PATH ?? ""}`,
      AGENTOPS_BYOC_LIB_DIR: join(repositoryRoot, "deploy/byoc"),
      AGENTOPS_BYOC_NEXT_APP_ROOT: fakeNextApp,
      AGENTOPS_POSTGRES_RUNTIME_ROLE: "agentops_runtime",
      AGENTOPS_POSTGRES_ENTITLEMENT_ADMIN_ROLE:
        "agentops_entitlement_admin",
      AGENTOPS_POSTGRES_RUNTIME_PASSWORD_FILE: runtimePasswordFile,
      AGENTOPS_POSTGRES_ENTITLEMENT_ADMIN_PASSWORD_FILE: adminPasswordFile,
      FAKE_PROVISION_LOG: provisionLog,
    });
    const derivedProvisionFiles = async () => (await readdir(fixtureRoot))
      .filter((name) => name.includes(".restore-"));

    activeCheck = "default_compose_migrator_components";
    const componentRestoreDatabase = "restore_component_provision";
    const componentProvision = await runShell(
      restoreProvisionScript,
      [componentRestoreDatabase],
      {
        ...cleanProvisionEnvironment,
        AGENTOPS_POSTGRES_MIGRATOR_HOST: "postgres",
        AGENTOPS_POSTGRES_MIGRATOR_PORT: "5432",
        AGENTOPS_POSTGRES_MIGRATOR_DATABASE: "agentops_production",
        AGENTOPS_POSTGRES_MIGRATOR_USER: "agentops_migrator",
        AGENTOPS_POSTGRES_MIGRATOR_PASSWORD_FILE: migratorPasswordFile,
        FAKE_EXPECTED_RESTORE_DB: componentRestoreDatabase,
        FAKE_EXPECT_COMPONENT_DSN: "true",
      },
    );
    assert.equal(componentProvision.code, 0);
    assertNoSecretOutput(componentProvision);
    assert.deepEqual(await derivedProvisionFiles(), []);
    const componentProvisionLog = await logLines(provisionLog);
    assert.equal(
      componentProvisionLog.includes(
        "migrate:derived_migrator_dsn_only",
      ),
      true,
    );
    assert.equal(
      componentProvisionLog.includes(
        "schema_check:derived_runtime_dsn_only",
      ),
      true,
    );
    assert.equal(
      componentProvisionLog.includes(
        "boundary:runtime:function_owner_restricted",
      ),
      true,
    );
    assert.equal(
      componentProvisionLog.includes(
        "boundary:entitlement-admin:function_owner_restricted",
      ),
      true,
    );

    const signalGate = join(fixtureRoot, "restore-provision-signal-gate");
    activeCheck = "component_dsn_signal_cleanup";
    const signaledComponentProvision = startShell(
      restoreProvisionScript,
      ["restore_component_signal"],
      {
        ...cleanProvisionEnvironment,
        AGENTOPS_POSTGRES_MIGRATOR_HOST: "postgres",
        AGENTOPS_POSTGRES_MIGRATOR_PORT: "5432",
        AGENTOPS_POSTGRES_MIGRATOR_DATABASE: "agentops_production",
        AGENTOPS_POSTGRES_MIGRATOR_USER: "agentops_migrator",
        AGENTOPS_POSTGRES_MIGRATOR_PASSWORD_FILE: migratorPasswordFile,
        FAKE_EXPECTED_RESTORE_DB: "restore_component_signal",
        FAKE_EXPECT_COMPONENT_DSN: "true",
        FAKE_PROVISION_GATE: signalGate,
      },
    );
    await waitForPath(`${signalGate}.started`);
    const filesBeforeSignal = await derivedProvisionFiles();
    assert.equal(filesBeforeSignal.length, 3);
    for (const name of filesBeforeSignal) {
      assert.equal((await lstat(join(fixtureRoot, name))).mode & 0o777, 0o400);
    }
    signaledComponentProvision.child.kill("SIGTERM");
    await writeFile(`${signalGate}.release`, "", { mode: 0o600 });
    const signaledComponentResult = await signaledComponentProvision.result;
    assert.equal(signaledComponentResult.code, 143);
    assertNoSecretOutput(signaledComponentResult);
    assert.deepEqual(await derivedProvisionFiles(), []);

    const provisionMigratorDsnFile = join(
      fixtureRoot,
      "postgres-provision-migrator-dsn",
    );
    await writeFile(
      provisionMigratorDsnFile,
      `${dsnSentinel}?sslmode=verify-full&connect_timeout=11\n`,
      { mode: 0o400 },
    );
    const fileProvisionEnvironment: NodeJS.ProcessEnv = {
      ...cleanProvisionEnvironment,
      AGENTOPS_POSTGRES_MIGRATOR_DSN_FILE: provisionMigratorDsnFile,
    };

    activeCheck = "file_backed_provision_sequence";
    const fileRestoreDatabase = "restore_file_provision";
    const fileProvision = await runShell(
      restoreProvisionScript,
      [fileRestoreDatabase],
      {
        ...fileProvisionEnvironment,
        FAKE_EXPECTED_RESTORE_DB: fileRestoreDatabase,
      },
    );
    assert.equal(fileProvision.code, 0);
    assertNoSecretOutput(fileProvision);
    assert.deepEqual(await derivedProvisionFiles(), []);
    const fileProvisionLog = await logLines(provisionLog);
    assert.equal(
      fileProvisionLog.includes("migrate:derived_migrator_dsn_only"),
      true,
    );
    assert.equal(
      fileProvisionLog.includes("schema_check:derived_runtime_dsn_only"),
      true,
    );

    activeCheck = "function_owner_evidence_required";
    const missingFunctionOwnerEvidence = await runShell(
      restoreProvisionScript,
      ["restore_missing_function_owner"],
      {
        ...fileProvisionEnvironment,
        FAKE_EXPECTED_RESTORE_DB: "restore_missing_function_owner",
        FAKE_FUNCTION_OWNER_EVIDENCE_MISSING: "true",
      },
    );
    assert.equal(missingFunctionOwnerEvidence.code, 73);
    assertNoSecretOutput(missingFunctionOwnerEvidence);
    assert.deepEqual(await derivedProvisionFiles(), []);

    activeCheck = "direct_dsn_rejected_before_boundary";
    const directDsnConflict = await runShell(
      restoreProvisionScript,
      ["restore_direct_dsn_conflict"],
      {
        ...fileProvisionEnvironment,
        AGENTOPS_POSTGRES_DSN: dsnSentinel,
        FAKE_EXPECTED_RESTORE_DB: "restore_direct_dsn_conflict",
      },
    );
    assert.equal(directDsnConflict.code, 65);
    assertNoSecretOutput(directDsnConflict);
    assert.deepEqual(await derivedProvisionFiles(), []);

    const existingDsnOutput = join(fixtureRoot, "postgres-existing-dsn");
    const existingDsnContent = "existing output must remain unchanged\n";
    await writeFile(existingDsnOutput, existingDsnContent, { mode: 0o600 });
    const existingDsnResult = await runNode(
      [restoreDsnScript, "restore_existing", existingDsnOutput],
      fileDsnEnvironment,
    );
    assertFailed(existingDsnResult, /restore_dsn_invalid/);
    assert.equal(
      await readFile(existingDsnOutput, "utf8"),
      existingDsnContent,
    );

    const ambiguousDsnResult = await runNode(
      [
        restoreDsnScript,
        "restore_ambiguous",
        join(fixtureRoot, "postgres-ambiguous-dsn"),
      ],
      {
        ...fileDsnEnvironment,
        AGENTOPS_POSTGRES_DSN: dsnSentinel,
      },
    );
    assertFailed(ambiguousDsnResult, /restore_dsn_invalid/);

    activeCheck = "node_secret_staging";
    const hostSecret = join(fixtureRoot, "host-secret-0600");
    const stagedSecretDirectory = join(fixtureRoot, "staged-secret");
    await writeFile(hostSecret, `${secretSentinel}\n`, { mode: 0o600 });
    await mkdir(stagedSecretDirectory, { mode: 0o700 });
    const stageProgram = [
      `import { stageSecretFile } from ${JSON.stringify(
        `file://${secretEntrypoint}`,
      )};`,
      'import { lstatSync } from "node:fs";',
      "const target = stageSecretFile({",
      "  sourcePath: process.env.SOURCE_SECRET,",
      "  targetDirectory: process.env.TARGET_DIRECTORY,",
      '  targetName: "prepared",',
      "  targetUid: process.getuid(),",
      "  targetGid: process.getgid(),",
      "});",
      "const state = lstatSync(target);",
      "process.stdout.write(JSON.stringify({",
      "  regular: state.isFile(),",
      "  mode: state.mode & 0o777,",
      "  uid: state.uid,",
      "  gid: state.gid,",
      "  expectedUid: process.getuid(),",
      "  expectedGid: process.getgid(),",
      "}));",
    ].join("\n");
    const stagedSecret = await runNode(
      ["--input-type=module", "--eval", stageProgram],
      {
        ...process.env,
        SOURCE_SECRET: hostSecret,
        TARGET_DIRECTORY: stagedSecretDirectory,
      },
    );
    assert.equal(stagedSecret.code, 0);
    const stagedState = JSON.parse(stagedSecret.stdout) as {
      regular: boolean;
      mode: number;
      uid: number;
      gid: number;
      expectedUid: number;
      expectedGid: number;
    };
    assert.deepEqual(stagedState, {
      regular: true,
      mode: 0o400,
      uid: stagedState.expectedUid,
      gid: stagedState.expectedGid,
      expectedUid: stagedState.expectedUid,
      expectedGid: stagedState.expectedGid,
    });
    assert.equal(
      await readFile(join(stagedSecretDirectory, "prepared"), "utf8"),
      `${secretSentinel}\n`,
    );

    const symlinkSecret = join(fixtureRoot, "host-secret-symlink");
    const symlinkSecretTarget = join(fixtureRoot, "host-secret-target");
    await writeFile(symlinkSecretTarget, `${secretSentinel}\n`, {
      mode: 0o600,
    });
    await symlink(symlinkSecretTarget, symlinkSecret);
    const rejectedSecretDirectory = join(
      fixtureRoot,
      "rejected-secret-staging",
    );
    await mkdir(rejectedSecretDirectory, { mode: 0o700 });
    const rejectedSymlinkSecret = await runNode(
      ["--input-type=module", "--eval", stageProgram],
      {
        ...process.env,
        SOURCE_SECRET: symlinkSecret,
        TARGET_DIRECTORY: rejectedSecretDirectory,
      },
    );
    assert.notEqual(rejectedSymlinkSecret.code, 0);
    assert.equal(
      `${rejectedSymlinkSecret.stdout}\n${rejectedSymlinkSecret.stderr}`.includes(
        secretSentinel,
      ),
      false,
    );
    assert.deepEqual(await readdir(rejectedSecretDirectory), []);

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

    const failedProvisionDatabase = "provision_failure_cleanup";
    activeCheck = "provision_failure_cleanup";
    const failedProvision = await runShell(restoreScript, [validBundle], {
      ...baseEnvironment,
      AGENTOPS_RESTORE_DATABASE: failedProvisionDatabase,
      AGENTOPS_RESTORE_KEEP: "true",
      FAKE_MIGRATE_FAIL: "true",
    });
    assertFailed(failedProvision, /restore_provisioning_failed/);
    assert.equal(
      await pathExists(join(databaseState, failedProvisionDatabase)),
      false,
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

    const failedRuntimeBoundaryDatabase = "runtime_boundary_failure_cleanup";
    activeCheck = "runtime_boundary_failure_cleanup";
    const failedRuntimeBoundary = await runShell(
      restoreScript,
      [validBundle],
      {
        ...baseEnvironment,
        AGENTOPS_RESTORE_DATABASE: failedRuntimeBoundaryDatabase,
        AGENTOPS_RESTORE_KEEP: "true",
        FAKE_RUNTIME_BOUNDARY_FAIL: "true",
      },
    );
    assertFailed(
      failedRuntimeBoundary,
      /restore_runtime_role_boundary_failed/,
    );
    assert.equal(
      await pathExists(join(databaseState, failedRuntimeBoundaryDatabase)),
      false,
    );

    const failedAdminBoundaryDatabase = "admin_boundary_failure_cleanup";
    activeCheck = "admin_boundary_failure_cleanup";
    const failedAdminBoundary = await runShell(
      restoreScript,
      [validBundle],
      {
        ...baseEnvironment,
        AGENTOPS_RESTORE_DATABASE: failedAdminBoundaryDatabase,
        AGENTOPS_RESTORE_KEEP: "true",
        FAKE_ADMIN_BOUNDARY_FAIL: "true",
      },
    );
    assertFailed(
      failedAdminBoundary,
      /restore_entitlement_admin_role_boundary_failed/,
    );
    assert.equal(
      await pathExists(join(databaseState, failedAdminBoundaryDatabase)),
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
      /"restore_provisioning_completed":true/,
    );
    assert.match(
      successfulCleanup.stdout,
      /"runtime_role_boundary_verified":true/,
    );
    assert.match(
      successfulCleanup.stdout,
      /"entitlement_admin_role_boundary_verified":true/,
    );
    assert.match(
      successfulCleanup.stdout,
      /"function_owner_boundary_verified":true/,
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
    assert.equal(
      finalLog.includes(
        `provision_source:migrator_components:${cleanupDatabase}`,
      ),
      true,
    );
    assert.equal(finalLog.includes(`migrate:${cleanupDatabase}`), true);
    assert.equal(finalLog.includes(`schema_check:${cleanupDatabase}`), true);
    assert.equal(
      finalLog.includes(`runtime_boundary:${cleanupDatabase}`),
      true,
    );
    assert.equal(
      finalLog.includes(`entitlement_admin_boundary:${cleanupDatabase}`),
      true,
    );

    console.log(JSON.stringify({
      ok: true,
      contract: "agentops_byoc_backup_restore_behavior_v1",
      concurrent_backup_fail_closed: true,
      backup_failure_unpublished: true,
      existing_output_fail_closed: true,
      symlink_output_fail_closed: true,
      internal_symlink_fail_closed: true,
      stable_restore_object: true,
      stable_restore_object_read_only: true,
      checksum_tamper_rejected_before_restore: true,
      dsn_file_supported: true,
      dsn_file_remains_file_backed: true,
      dsn_existing_output_preserved: true,
      dsn_query_parameters_preserved: true,
      migrator_dsn_used_for_restore: true,
      isolated_runtime_dsn_file_backed: true,
      isolated_entitlement_admin_dsn_file_backed: true,
      default_compose_migrator_components_retargeted: true,
      default_compose_role_dsns_file_backed: true,
      provisioning_passwords_present_during_migration: true,
      boundary_checks_use_only_derived_role_dsns: true,
      direct_role_dsns_absent_from_boundary_checker: true,
      migrator_credentials_absent_from_boundary_checker: true,
      derived_role_dsns_removed_on_success_failure_and_signal: true,
      node_secret_0600_staged_as_0400: true,
      node_secret_symlink_rejected: true,
      restore_failure_cleanup: true,
      provision_failure_cleanup: true,
      schema_failure_cleanup: true,
      schema_fingerprint_verified: true,
      runtime_boundary_failure_cleanup: true,
      entitlement_admin_boundary_failure_cleanup: true,
      restore_provisioning_completed: true,
      runtime_role_boundary_verified: true,
      entitlement_admin_role_boundary_verified: true,
      function_owner_boundary_verified: true,
      function_owner_evidence_required: true,
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
