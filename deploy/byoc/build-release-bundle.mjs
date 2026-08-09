#!/usr/bin/env node

import { createHash } from "node:crypto";
import {
  chmodSync,
  copyFileSync,
  existsSync,
  lstatSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  renameSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

const CONTRACT = "agentops_byoc_release_bundle_v1";
const COMMIT_MARKER = `${CONTRACT}:committed`;
const IMAGE = /^[a-z0-9][a-z0-9._:/-]*@sha256:[0-9a-f]{64}$/;
const REVISION = /^[0-9a-f]{40}$/;
const SHA256 = /^[0-9a-f]{64}$/;
const POSTGRES_IMAGE = "postgres:16-alpine@sha256:57c72fd2a128e416c7fcc499958864df5301e940bca0a56f58fddf30ffc07777";
const PLATFORM = "linux/amd64";
const moduleDirectory = dirname(fileURLToPath(import.meta.url));
const repositoryRoot = resolve(moduleDirectory, "../..");

const INPUTS = [
  ["deploy/byoc/compose.release.yaml", "deploy/byoc/compose.yaml", 0o600],
  ["deploy/byoc/.env.example", "deploy/byoc/.env.example", 0o600],
  ["deploy/byoc/RELEASE_BUNDLE.md", "README.md", 0o600],
  ["deploy/byoc/install.sh", "install.sh", 0o700],
  ["deploy/byoc/owner-init.sh", "owner-init.sh", 0o700],
  ["deploy/byoc/backup.sh", "deploy/byoc/backup.sh", 0o700],
  ["deploy/byoc/restore-drill.sh", "deploy/byoc/restore-drill.sh", 0o700],
  ["deploy/byoc/postgres-destructive-database.sh", "deploy/byoc/postgres-destructive-database.sh", 0o700],
  ["deploy/byoc/postgres-restore-guardian.sh", "deploy/byoc/postgres-restore-guardian.sh", 0o700],
  ["deploy/byoc/retained-data-lifecycle.mjs", "deploy/byoc/retained-data-lifecycle.mjs", 0o700],
  ["deploy/byoc/retained-data-lifecycle-state.mjs", "deploy/byoc/retained-data-lifecycle-state.mjs", 0o600],
];
const RELEASE_INPUTS = [
  ...new Set([
    "deploy/byoc/build-release-bundle.mjs",
    "deploy/byoc/Dockerfile",
    "deploy/byoc/owner-bootstrap-contract.mjs",
    "deploy/byoc/owner-bootstrap-entrypoint.mjs",
    "deploy/byoc/release-bundle-contract.mjs",
    ...INPUTS.map(([sourcePath]) => sourcePath),
  ]),
].sort();

function fail(code) {
  const error = new Error(code);
  error.code = code;
  throw error;
}

function sha256(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

function git(arguments_, acceptedStatuses = [0]) {
  const result = spawnSync("git", arguments_, {
    cwd: repositoryRoot,
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
  });
  if (!acceptedStatuses.includes(result.status ?? -1)) {
    fail("release_source_binding_failed");
  }
  return result;
}

function assertInputsBoundToRevision(revision) {
  const head = git(["rev-parse", "HEAD"]).stdout.trim();
  if (head !== revision) fail("release_source_revision_not_head");
  for (const path of RELEASE_INPUTS) {
    git(["ls-files", "--error-unmatch", "--", path]);
  }
  const difference = git(
    ["diff", "--quiet", revision, "--", ...RELEASE_INPUTS],
    [0, 1],
  );
  if (difference.status !== 0) fail("release_inputs_not_committed");
}

function safeFile(root, path) {
  const target = resolve(root, path);
  const prefix = `${resolve(root)}/`;
  if (!target.startsWith(prefix)) fail("release_path_escape");
  const metadata = lstatSync(target);
  if (!metadata.isFile() || metadata.isSymbolicLink()) fail("release_file_invalid");
  return target;
}

function walk(root, directory = root) {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = join(directory, entry.name);
    if (entry.isSymbolicLink()) fail("release_symlink_forbidden");
    if (entry.isDirectory()) return walk(root, path);
    if (!entry.isFile()) fail("release_entry_invalid");
    return [relative(root, path).replaceAll("\\", "/")];
  }).sort();
}

function verify(root) {
  const resolved = resolve(root);
  const marker = readFileSync(safeFile(resolved, "COMMITTED"), "utf8").trim();
  if (marker !== COMMIT_MARKER) fail("release_bundle_uncommitted");
  const manifest = JSON.parse(readFileSync(safeFile(resolved, "release-manifest.json"), "utf8"));
  if (
    manifest.contract !== CONTRACT
    || !IMAGE.test(String(manifest.image || ""))
    || !REVISION.test(String(manifest.source_revision || ""))
    || manifest.platform !== PLATFORM
    || manifest.postgres_image !== POSTGRES_IMAGE
    || manifest.credentials_included !== false
    || manifest.application_source_included !== false
    || manifest.repository_checkout_required !== false
    || manifest.owner_bootstrap_command_included !== true
    || !Array.isArray(manifest.files)
  ) fail("release_manifest_invalid");
  const declared = new Map(manifest.files.map((item) => [item.path, item]));
  if (declared.size !== manifest.files.length) fail("release_manifest_duplicate_path");
  const expectedPayload = new Set([
    ...INPUTS.map(([, destinationPath]) => destinationPath),
    "release-image.env",
  ]);
  if (
    declared.size !== expectedPayload.size
    || [...declared.keys()].some((path) => !expectedPayload.has(path))
  ) fail("release_manifest_file_set_invalid");
  for (const [path, item] of declared) {
    const target = safeFile(resolved, path);
    const mode = (lstatSync(target).mode & 0o777).toString(8);
    const actual = sha256(readFileSync(target));
    if (
      !/^(600|700)$/.test(String(item.mode || ""))
      || mode !== item.mode
      || !SHA256.test(String(item.sha256 || ""))
      || actual !== item.sha256
    ) {
      fail("release_file_checksum_mismatch");
    }
  }
  const checksumLines = readFileSync(safeFile(resolved, "SHA256SUMS"), "utf8")
    .trim().split("\n");
  const expectedChecksumLines = [
    ...manifest.files.map((item) => `${item.sha256}  ${item.path}`),
    `${sha256(readFileSync(safeFile(resolved, "release-manifest.json")))}  release-manifest.json`,
  ].sort();
  if (JSON.stringify(checksumLines.sort()) !== JSON.stringify(expectedChecksumLines)) {
    fail("release_checksum_manifest_mismatch");
  }
  const allowed = new Set([...declared.keys(), "release-manifest.json", "SHA256SUMS", "COMMITTED"]);
  const extra = walk(resolved).filter((path) => !allowed.has(path));
  if (extra.length) fail("release_unmanifested_file");
  for (const forbidden of [".git", "Dockerfile", "package.json", "package-lock.json", "server.py", "migrations/"]) {
    if (walk(resolved).some((path) => path === forbidden || path.startsWith(forbidden))) {
      fail("release_application_source_forbidden");
    }
  }
  return {
    ok: true,
    contract: CONTRACT,
    source_revision: manifest.source_revision,
    image_digest_verified: true,
    platform: PLATFORM,
    file_count: manifest.files.length,
    credentials_omitted: true,
    application_source_omitted: true,
    repository_checkout_required: false,
    owner_bootstrap_command_included: true,
  };
}

function build(output, image, revision) {
  if (!IMAGE.test(image)) fail("release_image_digest_required");
  if (!REVISION.test(revision)) fail("release_source_revision_invalid");
  assertInputsBoundToRevision(revision);
  const target = resolve(output);
  if (existsSync(target)) fail("release_output_exists");
  const parent = dirname(target);
  const parentMetadata = lstatSync(parent);
  if (!parentMetadata.isDirectory() || parentMetadata.isSymbolicLink()) {
    fail("release_output_parent_invalid");
  }
  const staging = `${target}.staging.${process.pid}`;
  if (existsSync(staging)) fail("release_staging_exists");
  mkdirSync(staging, { mode: 0o700 });
  try {
    const files = [];
    for (const [sourcePath, destinationPath, mode] of INPUTS) {
      const source = safeFile(repositoryRoot, sourcePath);
      const destination = resolve(staging, destinationPath);
      if (!destination.startsWith(`${staging}/`)) fail("release_path_escape");
      mkdirSync(dirname(destination), { recursive: true, mode: 0o700 });
      copyFileSync(source, destination);
      chmodSync(destination, mode);
      files.push({ path: destinationPath, mode: mode.toString(8), sha256: sha256(readFileSync(destination)) });
    }
    const imageEnvironment = `AGENTOPS_IMAGE=${image}\n`;
    const imagePath = join(staging, "release-image.env");
    writeFileSync(imagePath, imageEnvironment, { encoding: "utf8", mode: 0o600, flag: "wx" });
    files.push({ path: "release-image.env", mode: "600", sha256: sha256(Buffer.from(imageEnvironment)) });
    files.sort((left, right) => left.path.localeCompare(right.path));
    const manifest = {
      contract: CONTRACT,
      source_revision: revision,
      image,
      platform: PLATFORM,
      postgres_image: POSTGRES_IMAGE,
      credentials_included: false,
      application_source_included: false,
      repository_checkout_required: false,
      owner_bootstrap_command_included: true,
      files,
    };
    const manifestBytes = Buffer.from(`${JSON.stringify(manifest, null, 2)}\n`);
    writeFileSync(join(staging, "release-manifest.json"), manifestBytes, { mode: 0o600, flag: "wx" });
    const checksums = [
      ...files.map((item) => `${item.sha256}  ${item.path}`),
      `${sha256(manifestBytes)}  release-manifest.json`,
    ].sort().join("\n");
    writeFileSync(join(staging, "SHA256SUMS"), `${checksums}\n`, { mode: 0o600, flag: "wx" });
    writeFileSync(join(staging, "COMMITTED"), `${COMMIT_MARKER}\n`, { mode: 0o600, flag: "wx" });
    renameSync(staging, target);
    return verify(target);
  } catch (error) {
    rmSync(staging, { force: true, recursive: true });
    throw error;
  }
}

function option(arguments_, name) {
  const index = arguments_.indexOf(name);
  if (index < 0 || index === arguments_.length - 1) fail(`${name.slice(2).replaceAll("-", "_")}_required`);
  return arguments_[index + 1];
}

try {
  const [operation, ...arguments_] = process.argv.slice(2);
  const result = operation === "build"
    ? build(option(arguments_, "--output"), option(arguments_, "--image"), option(arguments_, "--source-revision"))
    : operation === "verify" && arguments_.length === 1
      ? verify(arguments_[0])
      : fail("release_command_invalid");
  process.stdout.write(`${JSON.stringify(result)}\n`);
} catch (error) {
  process.stderr.write(`${typeof error?.code === "string" ? error.code : "release_bundle_failed"}\n`);
  process.exitCode = 1;
}
