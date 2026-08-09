#!/usr/bin/env node

import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import {
  chmodSync,
  copyFileSync,
  mkdtempSync,
  mkdirSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const moduleDirectory = dirname(fileURLToPath(import.meta.url));
const repositoryRoot = resolve(moduleDirectory, "../..");
const password = "Owner-contract-only-Aa1!not-a-secret-fixture";
const temporaryRoot = mkdtempSync(join(tmpdir(), "agentops-owner-bootstrap-contract-"));

try {
  const compose = readFileSync(join(moduleDirectory, "compose.release.yaml"), "utf8");
  const dockerfile = readFileSync(join(moduleDirectory, "Dockerfile"), "utf8");
  const helper = readFileSync(
    join(moduleDirectory, "owner-bootstrap-entrypoint.mjs"),
    "utf8",
  );
  const operator = readFileSync(join(moduleDirectory, "owner-init.sh"), "utf8");

  assert.match(compose, /owner-bootstrap:\n[\s\S]*profiles: \[owner-bootstrap\]/);
  const ownerService = compose.match(
    /  owner-bootstrap:\n([\s\S]*?)(?=\n  [a-z][a-z0-9-]+:|\nvolumes:)/,
  )?.[1] || "";
  assert.match(ownerService, /--postgres-migrator/);
  assert.match(ownerService, /postgres_migrator_password/);
  assert.doesNotMatch(ownerService, /postgres_runtime_password/);
  assert.doesNotMatch(ownerService, /postgres_entitlement_admin_password/);
  assert.doesNotMatch(ownerService, /entitlement_operator_password/);
  assert.doesNotMatch(ownerService, /human_session_hmac_key/);
  assert.match(dockerfile, /owner-bootstrap-entrypoint\.mjs/);
  assert.match(helper, /PGPASSFILE/);
  assert.doesNotMatch(helper, /childEnvironment\.PGPASSWORD\s*=/);
  assert.match(helper, /delete childEnvironment\.AGENTOPS_POSTGRES_MIGRATOR_PASSWORD_FILE/);
  assert.match(operator, /set \+x/);
  assert.match(operator, /--password-stdin/);
  assert.doesNotMatch(operator, /openssl|rand\b/);
  assert.doesNotMatch(operator, /export\s+[^\n]*password/i);

  const releaseRoot = join(temporaryRoot, "release");
  const fakeBin = join(temporaryRoot, "bin");
  const captureRoot = join(temporaryRoot, "capture");
  mkdirSync(join(releaseRoot, "deploy/byoc"), { recursive: true });
  mkdirSync(fakeBin);
  mkdirSync(captureRoot);
  copyFileSync(join(moduleDirectory, "owner-init.sh"), join(releaseRoot, "owner-init.sh"));
  chmodSync(join(releaseRoot, "owner-init.sh"), 0o700);
  writeFileSync(join(releaseRoot, "deploy/byoc/compose.yaml"), "services: {}\n");
  writeFileSync(join(releaseRoot, "deploy/byoc/.env"), "AGENTOPS_IMAGE=fixture\n");
  writeFileSync(
    join(fakeBin, "docker"),
    `#!/bin/sh
set -eu
printf '%s\n' "$@" > "$AGENTOPS_OWNER_CAPTURE/argv"
env > "$AGENTOPS_OWNER_CAPTURE/env"
cat > "$AGENTOPS_OWNER_CAPTURE/stdin"
printf '%s\n' '{"ok":true,"operation":"commercial_owner_bootstrap","user":{"user_id":"husr_fixture"},"membership":{"workspace_id":"ws_fixture","role":"owner","status":"active"},"credential_omitted":true,"password_omitted":true}'
`,
    { mode: 0o700 },
  );

  const environment = {
    ...process.env,
    PATH: `${fakeBin}:${process.env.PATH || ""}`,
    AGENTOPS_OWNER_CAPTURE: captureRoot,
  };
  const executed = spawnSync(
    join(releaseRoot, "owner-init.sh"),
    [
      "--workspace-id", "ws_fixture",
      "--username", "fixture-owner",
      "--display-name", "Fixture Owner",
      "--password-stdin",
    ],
    { encoding: "utf8", env: environment, input: `${password}\n` },
  );
  assert.equal(executed.status, 0, executed.stderr);
  const receipt = JSON.parse(executed.stdout);
  assert.equal(receipt.user.user_id, "husr_fixture");
  assert.equal(receipt.membership.workspace_id, "ws_fixture");
  assert.equal(receipt.membership.role, "owner");
  assert.equal(receipt.password_omitted, true);
  const capturedArguments = readFileSync(join(captureRoot, "argv"), "utf8");
  const capturedEnvironment = readFileSync(join(captureRoot, "env"), "utf8");
  const capturedStdin = readFileSync(join(captureRoot, "stdin"), "utf8");
  assert.equal(capturedStdin, `${password}\n`);
  assert.equal(capturedArguments.includes(password), false);
  assert.equal(capturedEnvironment.includes(password), false);
  assert.equal(executed.stdout.includes(password), false);
  assert.equal(executed.stderr.includes(password), false);
  assert.match(capturedArguments, /owner-bootstrap-entrypoint\.mjs/);
  assert.match(capturedArguments, /--password-stdin/);
  assert.doesNotMatch(capturedArguments, /entitlement-admin/);

  rmSync(join(captureRoot, "argv"));
  rmSync(join(captureRoot, "env"));
  rmSync(join(captureRoot, "stdin"));
  const forbidden = spawnSync(
    join(releaseRoot, "owner-init.sh"),
    [
      "--workspace-id", "ws_fixture",
      "--username", "fixture-owner",
      `--password=${password}`,
    ],
    { encoding: "utf8", env: environment },
  );
  assert.notEqual(forbidden.status, 0);
  assert.match(forbidden.stderr, /owner_password_argv_forbidden/);
  assert.equal(forbidden.stderr.includes(password), false);

  process.stdout.write(`${JSON.stringify({
    ok: true,
    contract: "agentops_byoc_owner_bootstrap_packaging_v1",
    source_free_operator_command: true,
    hidden_prompt_default: true,
    password_stdin_only: true,
    password_argv_omitted: true,
    password_environment_omitted: true,
    password_receipt_omitted: true,
    migrator_secret_only: true,
    entitlement_boundary_unchanged: true,
    safe_owner_receipt_fields_preserved: true,
  })}\n`);
} finally {
  rmSync(temporaryRoot, { force: true, recursive: true });
}
