import { createHash } from "node:crypto";

import {
  EXPECTED_POSTGRES_SCHEMA_FINGERPRINT,
  POSTGRES_MIGRATION_MANIFEST,
  SCHEMA_CONTRACT,
} from "../src/server/controlPlane/schemaManifest";

const migrationManifestSha256 = createHash("sha256")
  .update(JSON.stringify(POSTGRES_MIGRATION_MANIFEST))
  .digest("hex");

console.log(JSON.stringify({
  contract: "agentops_byoc_schema_identity_v1",
  ok: true,
  schema_contract: SCHEMA_CONTRACT,
  schema_fingerprint_contract: EXPECTED_POSTGRES_SCHEMA_FINGERPRINT.contract,
  schema_fingerprint_sha256: EXPECTED_POSTGRES_SCHEMA_FINGERPRINT.sha256,
  schema_object_count: EXPECTED_POSTGRES_SCHEMA_FINGERPRINT.objectCount,
  migration_manifest_sha256: migrationManifestSha256,
  migration_count: POSTGRES_MIGRATION_MANIFEST.length,
  static_manifest_only: true,
  database_contacted: false,
  credentials_omitted: true,
  sql_omitted: true,
  row_data_omitted: true,
}));
