#!/usr/bin/env node

import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

const identityPath = fileURLToPath(
  new URL("./byoc-historical-v9-identity.json", import.meta.url),
);

try {
  const identity = JSON.parse(await readFile(identityPath, "utf8"));
  if (
    identity.contract !== "agentops_byoc_historical_schema_adapter_v1"
    || identity.source_revision
      !== "f55def1233403a503a39d9af92371a71770c23f7"
    || identity.schema_contract !== "agentops_commercial_postgres_v9"
    || identity.schema_fingerprint_contract
      !== "agentops_postgres_schema_fingerprint_v1"
    || !/^[a-f0-9]{64}$/.test(identity.schema_fingerprint_sha256)
    || !/^[a-f0-9]{64}$/.test(identity.migration_manifest_sha256)
    || identity.schema_object_count !== 745
    || identity.migration_count !== 10
  ) {
    throw new Error("historical_schema_identity_invalid");
  }
  console.log(JSON.stringify({
    contract: "agentops_byoc_schema_identity_v1",
    ok: true,
    historical_adapter_contract: identity.contract,
    historical_build_compatibility_patch: identity.build_compatibility_patch,
    historical_source_revision: identity.source_revision,
    schema_contract: identity.schema_contract,
    schema_fingerprint_contract: identity.schema_fingerprint_contract,
    schema_fingerprint_sha256: identity.schema_fingerprint_sha256,
    schema_object_count: identity.schema_object_count,
    migration_manifest_sha256: identity.migration_manifest_sha256,
    migration_count: identity.migration_count,
    static_manifest_only: true,
    database_contacted: false,
    credentials_omitted: true,
    sql_omitted: true,
    row_data_omitted: true,
  }));
} catch {
  console.error(JSON.stringify({
    contract: "agentops_byoc_schema_identity_v1",
    ok: false,
    error_code: "historical_schema_identity_unavailable",
    credentials_omitted: true,
    sql_omitted: true,
    row_data_omitted: true,
  }));
  process.exitCode = 1;
}
