import { createHash } from "node:crypto";

import type { ClientBase } from "pg";

export const SCHEMA_FINGERPRINT_CONTRACT =
  "agentops_postgres_schema_fingerprint_v1" as const;

type CatalogRow = {
  object_kind: string;
  object_identity: string;
  definition: string;
};

export type SchemaFingerprintReceipt = Readonly<{
  contract: typeof SCHEMA_FINGERPRINT_CONTRACT;
  sha256: string;
  object_count: number;
  object_counts: Readonly<Record<string, number>>;
  catalog_only: true;
  row_data_omitted: true;
  credentials_omitted: true;
  sql_omitted: true;
}>;

const SCHEMA_CATALOG_QUERY = `
WITH target_schema AS (
  SELECT oid,nspname
  FROM pg_namespace
  WHERE nspname=current_schema()
),
catalog AS (
  SELECT
    'relation'::text AS object_kind,
    c.relname::text AS object_identity,
    jsonb_build_object(
      'kind',c.relkind,
      'persistence',c.relpersistence,
      'row_security',c.relrowsecurity,
      'force_row_security',c.relforcerowsecurity,
      'partition_key',pg_get_partkeydef(c.oid),
      'partition_bound',
        CASE WHEN c.relispartition
          THEN pg_get_expr(c.relpartbound,c.oid,true)
          ELSE NULL
        END
    )::text AS definition
  FROM pg_class c
  JOIN target_schema n ON n.oid=c.relnamespace
  WHERE c.relkind IN ('r','p','v','m','S')

  UNION ALL

  SELECT
    'column',
    c.relname || '.' || a.attname,
    jsonb_build_object(
      'ordinal',a.attnum,
      'type',format_type(a.atttypid,a.atttypmod),
      'not_null',a.attnotnull,
      'default',pg_get_expr(d.adbin,d.adrelid,true),
      'identity',a.attidentity,
      'generated',a.attgenerated,
      'collation',
        CASE WHEN a.attcollation=0 THEN NULL ELSE coll.collname END
    )::text
  FROM pg_class c
  JOIN target_schema n ON n.oid=c.relnamespace
  JOIN pg_attribute a ON a.attrelid=c.oid
  LEFT JOIN pg_attrdef d
    ON d.adrelid=a.attrelid AND d.adnum=a.attnum
  LEFT JOIN pg_collation coll ON coll.oid=a.attcollation
  WHERE c.relkind IN ('r','p','v','m')
    AND a.attnum>0
    AND NOT a.attisdropped

  UNION ALL

  SELECT
    'constraint',
    COALESCE(c.relname || '.', '') || constraint_row.conname,
    jsonb_build_object(
      'type',constraint_row.contype,
      'deferrable',constraint_row.condeferrable,
      'deferred',constraint_row.condeferred,
      'validated',constraint_row.convalidated,
      'definition',pg_get_constraintdef(constraint_row.oid,true)
    )::text
  FROM pg_constraint constraint_row
  JOIN target_schema n ON n.oid=constraint_row.connamespace
  LEFT JOIN pg_class c ON c.oid=constraint_row.conrelid

  UNION ALL

  SELECT
    'index',
    index_class.relname,
    jsonb_build_object(
      'valid',index_row.indisvalid,
      'ready',index_row.indisready,
      'unique',index_row.indisunique,
      'primary',index_row.indisprimary,
      'definition',pg_get_indexdef(index_row.indexrelid,0,true)
    )::text
  FROM pg_index index_row
  JOIN pg_class index_class ON index_class.oid=index_row.indexrelid
  JOIN target_schema n ON n.oid=index_class.relnamespace

  UNION ALL

  SELECT
    'function',
    procedure_row.proname || '('
      || pg_get_function_identity_arguments(procedure_row.oid) || ')',
    jsonb_build_object(
      'kind',procedure_row.prokind,
      'security_definer',procedure_row.prosecdef,
      'strict',procedure_row.proisstrict,
      'volatility',procedure_row.provolatile,
      'parallel',procedure_row.proparallel,
      'configuration',procedure_row.proconfig,
      'definition',pg_get_functiondef(procedure_row.oid)
    )::text
  FROM pg_proc procedure_row
  JOIN target_schema n ON n.oid=procedure_row.pronamespace

  UNION ALL

  SELECT
    'trigger',
    table_class.relname || '.' || trigger_row.tgname,
    jsonb_build_object(
      'enabled',trigger_row.tgenabled,
      'definition',pg_get_triggerdef(trigger_row.oid,true)
    )::text
  FROM pg_trigger trigger_row
  JOIN pg_class table_class ON table_class.oid=trigger_row.tgrelid
  JOIN target_schema n ON n.oid=table_class.relnamespace
  WHERE NOT trigger_row.tgisinternal

  UNION ALL

  SELECT
    'policy',
    table_class.relname || '.' || policy_row.polname,
    jsonb_build_object(
      'permissive',policy_row.polpermissive,
      'command',policy_row.polcmd,
      'roles',COALESCE((
        SELECT jsonb_agg(
          CASE WHEN role_oid=0 THEN 'public' ELSE role_row.rolname END
          ORDER BY CASE WHEN role_oid=0 THEN 'public' ELSE role_row.rolname END
        )
        FROM unnest(policy_row.polroles) AS role_oid
        LEFT JOIN pg_roles role_row ON role_row.oid=role_oid
      ),'[]'::jsonb),
      'using',pg_get_expr(policy_row.polqual,policy_row.polrelid,true),
      'with_check',pg_get_expr(policy_row.polwithcheck,policy_row.polrelid,true)
    )::text
  FROM pg_policy policy_row
  JOIN pg_class table_class ON table_class.oid=policy_row.polrelid
  JOIN target_schema n ON n.oid=table_class.relnamespace

  UNION ALL

  SELECT
    'type',
    type_row.typname,
    jsonb_build_object(
      'kind',type_row.typtype,
      'base_type',
        CASE WHEN type_row.typbasetype=0
          THEN NULL
          ELSE format_type(type_row.typbasetype,type_row.typtypmod)
        END,
      'not_null',type_row.typnotnull,
      'default',type_row.typdefault,
      'enum_labels',(
        SELECT jsonb_agg(enum_row.enumlabel ORDER BY enum_row.enumsortorder)
        FROM pg_enum enum_row
        WHERE enum_row.enumtypid=type_row.oid
      )
    )::text
  FROM pg_type type_row
  JOIN target_schema n ON n.oid=type_row.typnamespace
  WHERE type_row.typtype IN ('d','e')
)
SELECT object_kind,object_identity,definition
FROM catalog
ORDER BY object_kind,object_identity,definition
`;

function canonicalRows(
  rows: readonly CatalogRow[],
  schemaName: string,
) {
  const quotedSchemaPrefix =
    `"${schemaName.replaceAll('"', '""')}".`;
  const schemaPrefix = `${schemaName}.`;
  const quotedLiteralSchema = schemaName.replaceAll("'", "''");
  const searchPathPatterns = [
    `search_path=pg_catalog, ${schemaName}, pg_temp`,
    `search_path=pg_catalog,${schemaName},pg_temp`,
    `SET search_path TO 'pg_catalog', '${quotedLiteralSchema}', 'pg_temp'`,
    `SET search_path TO pg_catalog, ${schemaName}, pg_temp`,
  ];
  const normalize = (value: string) => {
    let normalized = value
      .replaceAll("\r\n", "\n")
      .replaceAll(quotedSchemaPrefix, "__agentops_schema__.")
      .replaceAll(schemaPrefix, "__agentops_schema__.");
    for (const pattern of searchPathPatterns) {
      normalized = normalized.replaceAll(
        pattern,
        pattern
          .replace(schemaName, "__agentops_schema__")
          .replace(quotedLiteralSchema, "__agentops_schema__"),
      );
    }
    return normalized.trim();
  };
  return rows.map((row) => [
    row.object_kind,
    normalize(row.object_identity),
    normalize(row.definition),
  ]);
}

export async function computeSchemaFingerprint(
  client: ClientBase,
): Promise<SchemaFingerprintReceipt> {
  const schemaResult = await client.query<{ schema_name: string }>(
    "SELECT current_schema() AS schema_name",
  );
  const schemaName = String(schemaResult.rows[0]?.schema_name || "").trim();
  if (!schemaName) throw new Error("postgres_current_schema_required");
  const result = await client.query<CatalogRow>(SCHEMA_CATALOG_QUERY);
  const canonical = canonicalRows(result.rows, schemaName);
  const objectCounts: Record<string, number> = {};
  for (const [kind] of canonical) {
    objectCounts[kind] = (objectCounts[kind] || 0) + 1;
  }
  const sha256 = createHash("sha256")
    .update(JSON.stringify(canonical))
    .digest("hex");
  return Object.freeze({
    contract: SCHEMA_FINGERPRINT_CONTRACT,
    sha256,
    object_count: canonical.length,
    object_counts: Object.freeze(
      Object.fromEntries(Object.entries(objectCounts).sort(([left], [right]) =>
        left.localeCompare(right))),
    ),
    catalog_only: true,
    row_data_omitted: true,
    credentials_omitted: true,
    sql_omitted: true,
  });
}
