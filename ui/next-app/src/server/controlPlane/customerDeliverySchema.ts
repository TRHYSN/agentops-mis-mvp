import type { PoolClient } from "pg";

import { postgresApplicationSchema } from "./config";
import { ControlPlaneHttpError } from "./http";

export const CUSTOMER_DELIVERY_SCHEMA_ASSUMPTIONS = {
  baseline: "20260724_current_main_commercial_baseline.sql",
  workspaceAudit: "20260719_workspace_read_models_v2.sql",
  approvalBinding: "20260719_approval_kind_bindings_v4.sql",
  runUniqueness: "20260724_customer_delivery_run_unique_v5.sql",
  uniqueIndex: "idx_approvals_customer_delivery_run_unique",
  requiredTriggers: [
    "approvals_kind_binding_enforced",
    "tool_calls_customer_delivery_evidence_sealed",
    "evaluations_customer_delivery_evidence_sealed",
    "artifacts_customer_delivery_evidence_sealed",
    "manifests_customer_delivery_evidence_sealed",
    "agent_plans_customer_delivery_evidence_sealed",
  ],
} as const;

const REQUIRED_COLUMNS: Record<string, string[]> = {
  agent_gateway_tokens: [
    "token_id",
    "token_hash",
    "workspace_id",
    "agent_id",
    "scopes_json",
    "status",
    "expires_at",
    "last_used_at",
  ],
  agent_gateway_sessions: [
    "session_id",
    "session_hash",
    "parent_token_id",
    "workspace_id",
    "agent_id",
    "scopes_json",
    "status",
    "expires_at",
    "revoked_at",
    "last_used_at",
  ],
  tasks: [
    "task_id",
    "workspace_id",
    "owner_agent_id",
    "collaborator_agent_ids",
    "status",
    "updated_at",
  ],
  runs: [
    "run_id",
    "workspace_id",
    "task_id",
    "agent_id",
    "runtime_type",
    "model_provider",
    "status",
    "agent_plan_id",
    "plan_hash",
  ],
  agent_plans: [
    "plan_id",
    "workspace_id",
    "task_id",
    "run_id",
    "agent_id",
    "status",
    "plan_version",
    "plan_hash",
    "verified_at",
    "verification_result_hash",
    "execution_steps_json",
    "created_at",
  ],
  plan_evidence_manifests: [
    "manifest_id",
    "workspace_id",
    "plan_id",
    "task_id",
    "run_id",
    "agent_id",
    "mismatch_policy",
    "expected_steps_json",
    "tool_call_ids_json",
    "evaluation_ids_json",
    "artifact_ids_json",
    "audit_ids_json",
    "plan_hash",
    "verification_result_hash",
    "status",
    "verification_json",
    "created_at",
    "updated_at",
  ],
  approvals: [
    "approval_id",
    "approval_kind",
    "task_id",
    "run_id",
    "tool_call_id",
    "requested_by_agent_id",
    "approver_user_id",
    "decision",
    "reason",
    "expires_at",
    "created_at",
    "decided_at",
  ],
  tool_calls: [
    "tool_call_id",
    "run_id",
    "agent_id",
    "tool_name",
    "normalized_args_json",
    "status",
  ],
  evaluations: [
    "evaluation_id",
    "run_id",
    "agent_id",
    "evaluator_type",
    "rubric_json",
    "pass_fail",
  ],
  artifacts: [
    "artifact_id",
    "task_id",
    "run_id",
    "content_hash",
  ],
  audit_logs: [
    "audit_id",
    "workspace_id",
    "actor_type",
    "actor_id",
    "action",
    "entity_type",
    "entity_id",
    "metadata_json",
    "tamper_chain_hash",
  ],
  runtime_events: [
    "runtime_event_id",
    "event_type",
    "status",
    "run_id",
    "task_id",
    "agent_id",
    "raw_payload_hash",
  ],
};

function schemaUnavailable() {
  throw new ControlPlaneHttpError(
    503,
    "customer_delivery_schema_not_ready",
    "Customer-delivery approval schema v5 is not ready.",
  );
}

export async function assertCustomerDeliverySchemaReady(client: PoolClient) {
  const applicationSchema = postgresApplicationSchema();
  const tableNames = Object.keys(REQUIRED_COLUMNS);
  let columnRows: Array<{ table_name: string; column_name: string }>;
  try {
    const result = await client.query<{ table_name: string; column_name: string }>(
      `SELECT table_name,column_name
      FROM information_schema.columns
      WHERE table_schema=$1 AND table_name=ANY($2::text[])`,
      [applicationSchema, tableNames],
    );
    columnRows = result.rows;
  } catch {
    return schemaUnavailable();
  }
  const present = new Set(
    columnRows.map((row) => `${row.table_name}.${row.column_name}`),
  );
  if (
    Object.entries(REQUIRED_COLUMNS).some(([table, columns]) =>
      columns.some((column) => !present.has(`${table}.${column}`)))
  ) {
    return schemaUnavailable();
  }

  const indexResult = await client.query<{
    table_name: string;
    is_unique: boolean;
    keys: string[];
    predicate: string | null;
  }>(
    `SELECT table_relation.relname AS table_name,
      index_record.indisunique AS is_unique,
      ARRAY(
        SELECT pg_get_indexdef(index_record.indexrelid,position,true)
        FROM generate_series(1,index_record.indnkeyatts) AS position
        ORDER BY position
      ) AS keys,
      pg_get_expr(index_record.indpred,index_record.indrelid,true) AS predicate
    FROM pg_index index_record
    JOIN pg_class index_relation ON index_relation.oid=index_record.indexrelid
    JOIN pg_class table_relation ON table_relation.oid=index_record.indrelid
    JOIN pg_namespace namespace ON namespace.oid=index_relation.relnamespace
    WHERE namespace.nspname=$1 AND index_relation.relname=$2`,
    [
      applicationSchema,
      CUSTOMER_DELIVERY_SCHEMA_ASSUMPTIONS.uniqueIndex,
    ],
  );
  const index = indexResult.rows[0];
  const predicate = String(index?.predicate || "").toLowerCase();
  if (
    indexResult.rows.length !== 1
    || index.table_name !== "approvals"
    || !index.is_unique
    || index.keys.length !== 1
    || index.keys[0] !== "run_id"
    || !predicate.includes("approval_kind")
    || !predicate.includes("customer_delivery")
  ) {
    return schemaUnavailable();
  }

  const triggerResult = await client.query<{ trigger_name: string }>(
    `SELECT trigger_record.tgname AS trigger_name
    FROM pg_trigger trigger_record
    JOIN pg_class relation ON relation.oid=trigger_record.tgrelid
    JOIN pg_namespace namespace ON namespace.oid=relation.relnamespace
    WHERE namespace.nspname=$1
      AND NOT trigger_record.tgisinternal
      AND trigger_record.tgenabled='O'
      AND trigger_record.tgname=ANY($2::text[])`,
    [
      applicationSchema,
      [...CUSTOMER_DELIVERY_SCHEMA_ASSUMPTIONS.requiredTriggers],
    ],
  );
  const triggers = new Set(triggerResult.rows.map((row) => row.trigger_name));
  if (
    CUSTOMER_DELIVERY_SCHEMA_ASSUMPTIONS.requiredTriggers.some(
      (trigger) => !triggers.has(trigger),
    )
  ) {
    return schemaUnavailable();
  }
}
