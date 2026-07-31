#!/usr/bin/env node

import { createHash } from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";

const [applicationRootArgument, identityPathArgument, sourceRevision] =
  process.argv.slice(2);
if (!applicationRootArgument || !identityPathArgument || !sourceRevision) {
  throw new Error("historical_adapter_arguments_required");
}

const applicationRoot = resolve(applicationRootArgument);
const identityPath = resolve(identityPathArgument);
const identity = JSON.parse(await readFile(identityPath, "utf8"));
if (
  identity.contract !== "agentops_byoc_historical_schema_adapter_v1"
  || identity.source_revision !== sourceRevision
) {
  throw new Error("historical_adapter_revision_mismatch");
}

function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}

const manifestPath = resolve(
  applicationRoot,
  "src/server/controlPlane/schemaManifest.ts",
);
const readinessPath = resolve(
  applicationRoot,
  "src/server/controlPlane/schemaReadiness.ts",
);
const packageLockPath = resolve(applicationRoot, "package-lock.json");
if (
  sha256(await readFile(manifestPath)) !== identity.schema_manifest_file_sha256
  || sha256(await readFile(readinessPath))
    !== identity.schema_readiness_file_sha256
  || sha256(await readFile(packageLockPath)) !== identity.package_lock_sha256
  || identity.build_compatibility_patch
    !== "migration_root_runtime_resolution_v1"
) {
  throw new Error("historical_adapter_source_mismatch");
}

const packagePath = resolve(applicationRoot, "package.json");
const packageDocument = JSON.parse(await readFile(packagePath, "utf8"));
const adapterCommands = {
  "byoc:schema-identity":
    "node scripts/byoc-historical-v9-schema-identity.mjs",
  "byoc:database-identity":
    "node scripts/byoc-historical-database-identity.mjs",
};
for (const [command, expected] of Object.entries(adapterCommands)) {
  if (
    Object.hasOwn(packageDocument.scripts || {}, command)
    && packageDocument.scripts[command] !== expected
  ) {
    throw new Error("historical_adapter_command_collision");
  }
}
packageDocument.scripts = {
  ...packageDocument.scripts,
  ...adapterCommands,
};

const readiness = await readFile(readinessPath, "utf8");
const oldImport = 'import { fileURLToPath } from "node:url";';
const newImport = 'import { resolve } from "node:path";';
const oldRoot = `const MIGRATION_ROOT = fileURLToPath(
  new URL("../../../../../migrations/postgres/", import.meta.url),
);`;
const newRoot = [
  "const MIGRATION_ROOT = `${resolve(",
  "  process.cwd(),",
  '  "../../migrations/postgres",',
  ")}/`;",
].join("\n");
if (
  readiness.split(oldImport).length !== 2
  || readiness.split(oldRoot).length !== 2
  || readiness.includes(newImport)
  || readiness.includes(newRoot)
) {
  throw new Error("historical_adapter_build_patch_mismatch");
}
await writeFile(packagePath, `${JSON.stringify(packageDocument, null, 2)}\n`);
await writeFile(
  readinessPath,
  readiness.replace(oldImport, newImport).replace(oldRoot, newRoot),
);
