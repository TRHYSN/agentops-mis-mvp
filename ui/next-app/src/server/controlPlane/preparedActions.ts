import { randomUUID } from "node:crypto";
import type { PoolClient } from "pg";

import {
  authenticateAgentGateway,
  enforceWorkspaceBinding,
  type AgentGatewayIdentity,
} from "./auth";
import {
  agentPlanVerificationHash,
  computeAgentPlanHash,
  verifyAgentPlanRow,
} from "./agentPlanContract";
import {
  verifyCurrentAgentGatewayPlanEvidenceManifest,
} from "./agentGatewayPlans";
import { boundedJsonObject } from "./boundedJson";
import { withPostgresTransaction } from "./db";
import { ControlPlaneHttpError } from "./http";
import { appendAudit, appendRuntimeEvent, stableHash } from "./ledger";
import { settleExistingTerminalRunCost } from "./terminalRunCost";

export const PREPARED_ACTION_MAX_BODY_BYTES = 16 * 1024;

const ACTION_TYPE = "agent_worker.codex.workspace_write";
const SHA256_HEX = /^[a-f0-9]{64}$/;
const SAFE_IDENTIFIER = /^[A-Za-z0-9._:-]{1,180}$/;
const REQUIRED_V6_TRIGGERS = [
  "prepared_actions_identity_v6",
  "prepared_actions_execution_binding_v6",
  "prepared_action_execution_leases_guard_v6",
  "prepared_action_execution_leases_binding_v6",
  "prepared_action_execution_receipts_guard_v6",
  "prepared_action_execution_receipts_append_only_v6",
] as const;
const REQUIRED_V6_COLUMNS: Record<string, string[]> = {
  prepared_action_execution_leases: [
    "claim_request_hash",
    "claim_idempotency_hash",
    "claim_identity_source",
  ],
  prepared_action_execution_receipts: [
    "receipt_id",
    "lease_id",
    "action_id",
    "workspace_id",
    "requested_by_agent_id",
    "action_hash",
    "claim_request_hash",
    "claim_idempotency_hash",
    "receipt_request_hash",
    "outcome",
    "provider_call_performed",
    "provider_call_may_have_completed",
    "terminal_evidence_hash",
    "terminal_evidence_source",
    "terminal_evidence_verified",
    "automatic_retry_allowed",
    "retry_requires_new_action",
    "raw_provider_output_omitted",
    "raw_prompt_omitted",
    "raw_response_omitted",
    "token_omitted",
    "terminal_at",
  ],
};
const FORBIDDEN_STORED_KEYS = new Set([
  "authorization",
  "credential",
  "credentials",
  "api_key",
  "access_token",
  "refresh_token",
  "password",
  "secret",
  "raw_prompt",
  "raw_response",
  "raw_transcript",
  "raw_content",
  "provider_output",
  "provider_response",
]);

type PreparedActionRow = {
  action_id: string;
  workspace_id: string;
  task_id: string;
  run_id: string;
  tool_call_id: string | null;
  approval_id: string;
  requested_by_agent_id: string;
  action_type: string;
  normalized_args_json: string;
  target_resource: string | null;
  risk_level: string;
  policy_version: string;
  checkpoint_json: string;
  action_hash: string;
  idempotency_key: string;
  status: string;
  provider_side_effect_id: string | null;
  result_summary: string | null;
  created_at: string;
  approved_at: string | null;
  consumed_at: string | null;
  expires_at: string | null;
};

type ApprovalRow = {
  approval_id: string;
  approval_kind: string;
  task_id: string;
  run_id: string;
  tool_call_id: string | null;
  requested_by_agent_id: string | null;
  approver_user_id: string | null;
  decision: string;
  reason: string | null;
  expires_at: string | null;
  created_at: string;
  decided_at: string | null;
};

type RunRow = {
  run_id: string;
  workspace_id: string;
  task_id: string;
  agent_id: string;
  runtime_type: string;
  status: string;
  started_at: string;
  ended_at: string | null;
  approval_required: number;
  agent_plan_id: string | null;
  plan_hash: string | null;
};

type TaskRow = {
  task_id: string;
  workspace_id: string;
  owner_agent_id: string | null;
  status: string;
  updated_at: string;
};

type ToolCallRow = {
  tool_call_id: string;
  run_id: string;
  agent_id: string;
  tool_name: string;
  normalized_args_json: string;
  target_resource: string | null;
  risk_level: string;
  status: string;
  side_effect_id: string | null;
  ended_at: string | null;
};

type ExecutionLeaseRow = {
  lease_id: string;
  action_id: string;
  workspace_id: string;
  requested_by_agent_id: string;
  action_hash: string;
  status: string;
  started_at: string;
  expires_at: string;
  completed_at: string | null;
  failure_reason: string | null;
  claim_request_hash: string;
  claim_idempotency_hash: string;
  claim_identity_source: string;
};

type ExecutionReceiptRow = {
  receipt_id: string;
  lease_id: string;
  action_id: string;
  workspace_id: string;
  requested_by_agent_id: string;
  action_hash: string;
  claim_request_hash: string;
  claim_idempotency_hash: string;
  receipt_request_hash: string;
  outcome: "succeeded" | "failed" | "unknown";
  provider_call_performed: boolean;
  provider_call_may_have_completed: boolean;
  terminal_evidence_hash: string | null;
  terminal_evidence_source: string;
  terminal_evidence_verified: boolean;
  automatic_retry_allowed: boolean;
  retry_requires_new_action: boolean;
  raw_provider_output_omitted: boolean;
  raw_prompt_omitted: boolean;
  raw_response_omitted: boolean;
  token_omitted: boolean;
  terminal_at: string;
};

type AgentPlanRow = {
  plan_id: string;
  workspace_id: string;
  task_id: string | null;
  run_id: string | null;
  agent_id: string;
  task_understanding: string;
  referenced_specs_json: string;
  referenced_memories_json: string;
  referenced_bases_json: string;
  proposed_files_to_change_json: string;
  risk_level: string;
  approval_required: number;
  execution_steps_json: string;
  verification_plan: string | null;
  rollback_plan: string | null;
  status: string;
  plan_version: number;
  plan_hash: string | null;
  verified_at: string | null;
  verification_result_hash: string | null;
  created_at: string;
};

type EvidenceToolRow = {
  tool_call_id: string;
  run_id: string;
  agent_id: string;
  tool_name: string;
  normalized_args_json: string;
  status: string;
};

type EvidenceEvaluationRow = {
  evaluation_id: string;
  task_id: string;
  run_id: string;
  agent_id: string;
  evaluator_type: string;
  pass_fail: string;
  rubric_json: string;
};

type EvidenceArtifactRow = {
  artifact_id: string;
  task_id: string | null;
  run_id: string | null;
  artifact_type: string;
  content_hash: string | null;
};

type EvidenceAuditRow = {
  audit_id: string;
  workspace_id: string | null;
  actor_type: string;
  actor_id: string | null;
  action: string;
  entity_type: string;
  entity_id: string;
  metadata_json: string;
  tamper_chain_hash: string | null;
};

type BoundAction = {
  action: PreparedActionRow;
  approval: ApprovalRow;
  run: RunRow;
  task: TaskRow;
  tool: ToolCallRow;
  plan: AgentPlanRow;
  normalizedArgs: Record<string, unknown>;
  checkpoint: Record<string, unknown>;
  currentActionHash: string;
};

type PreparedActionResult = {
  status: number;
  body: Record<string, unknown>;
};

type PreparedActionAuthoringToolRow = {
  tool_call_id: string;
  run_id: string;
  agent_id: string;
  tool_name: string;
  normalized_args_json: string;
  target_resource: string | null;
  risk_level: string;
  status: string;
  side_effect_id: string | null;
  ended_at: string | null;
};

function identifier(value: unknown, field: string) {
  const normalized = String(value ?? "").trim();
  if (!SAFE_IDENTIFIER.test(normalized)) {
    throw new ControlPlaneHttpError(
      400,
      `${field}_invalid`,
      `${field} must use 1-180 safe identifier characters.`,
    );
  }
  return normalized;
}

function canonicalJsonValue(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonicalJsonValue);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>)
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([key, item]) => [key, canonicalJsonValue(item)]),
    );
  }
  return value;
}

function requestJsonObject(value: unknown, field: string) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new ControlPlaneHttpError(
      400,
      `${field}_invalid`,
      `${field} must be a JSON object.`,
    );
  }
  const normalized = canonicalJsonValue(value) as Record<string, unknown>;
  return {
    value: normalized,
    json: JSON.stringify(normalized),
  };
}

function preparedActionIdempotencyKey(request: Request, body: Record<string, unknown>) {
  const headerValue = String(request.headers.get("idempotency-key") || "").trim();
  const bodyValue = String(body.idempotency_key || "").trim();
  if (headerValue && bodyValue && headerValue !== bodyValue) {
    throw new ControlPlaneHttpError(
      409,
      "prepared_action_idempotency_conflict",
      "Header and body idempotency keys do not match.",
    );
  }
  const value = headerValue || bodyValue;
  if (!/^[A-Za-z0-9._:-]{16,120}$/.test(value)) {
    throw new ControlPlaneHttpError(
      400,
      "idempotency_key_required",
      "PreparedAction idempotency key must use 16-120 safe identifier characters.",
    );
  }
  return value;
}

function assertAllowedFields(
  body: Record<string, unknown>,
  allowed: ReadonlySet<string>,
  operation: string,
) {
  const unsupported = Object.keys(body).find((field) => !allowed.has(field));
  if (unsupported) {
    throw new ControlPlaneHttpError(
      400,
      "prepared_action_request_field_unsupported",
      `${operation} received an unsupported request field.`,
    );
  }
}

function boundedInteger(
  value: unknown,
  fallback: number,
  minimum: number,
  maximum: number,
) {
  if (value === undefined || value === null || value === "") return fallback;
  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed < minimum || parsed > maximum) {
    throw new ControlPlaneHttpError(
      400,
      "prepared_action_numeric_field_invalid",
      `Numeric input must be an integer from ${minimum} to ${maximum}.`,
    );
  }
  return parsed;
}

function safeSummary(value: unknown, fallback: string, maximum = 260) {
  const sanitized = String(value ?? "")
    .replace(
      /-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----[\s\S]*?-----END (?:[A-Z0-9 ]+ )?PRIVATE KEY-----/g,
      "[PRIVATE_KEY_REDACTED]",
    )
    .replace(/(bearer\s+)[a-z0-9._-]+/gi, "$1[REDACTED]")
    .replace(
      /(token|secret|password|api[_-]?key|credential)\s*[:=]\s*['"]?[^'"\s,;]+/gi,
      "$1=[REDACTED]",
    )
    .replace(
      /raw[_-]?(?:prompt|response|transcript|content)\s*[:=]\s*['"]?[^'"\s,;]+/gi,
      "[RAW_FIELD_REDACTED]",
    )
    .replace(
      /(?<![A-Za-z0-9])(?:sk|gh[pousr])[-_][A-Za-z0-9_-]{16,}/g,
      "[SECRET_REDACTED]",
    )
    .replace(/github_pat_[A-Za-z0-9_]{20,}/g, "[SECRET_REDACTED]")
    .replace(
      /\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b/g,
      "[JWT_REDACTED]",
    )
    .replace(/\b(?:agtok|agtsess)_[A-Za-z0-9_-]+\b/g, "[AGENT_TOKEN_REF_REDACTED]")
    .replace(/[\u0000-\u001f\u007f]/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, maximum);
  return sanitized || fallback;
}

function parseJsonObject(value: string, label: string, maximumBytes = 64 * 1024) {
  if (Buffer.byteLength(value, "utf8") > maximumBytes) {
    throw new ControlPlaneHttpError(
      409,
      "prepared_action_stored_metadata_invalid",
      `${label} exceeds the governed metadata bound.`,
    );
  }
  try {
    const parsed: unknown = JSON.parse(value || "{}");
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      return parsed as Record<string, unknown>;
    }
  } catch {
    // Fall through to the fail-closed error.
  }
  throw new ControlPlaneHttpError(
    409,
    "prepared_action_stored_metadata_invalid",
    `${label} must be a JSON object.`,
  );
}

function jsonList(value: string) {
  try {
    const parsed: unknown = JSON.parse(value || "[]");
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function hasForbiddenStoredKey(value: unknown): boolean {
  if (Array.isArray(value)) return value.some(hasForbiddenStoredKey);
  if (!value || typeof value !== "object") return false;
  return Object.entries(value as Record<string, unknown>).some(([key, item]) => {
    const normalized = key.trim().toLowerCase();
    if (
      !normalized.endsWith("_omitted")
      && FORBIDDEN_STORED_KEYS.has(normalized)
    ) {
      return true;
    }
    return hasForbiddenStoredKey(item);
  });
}

function assertNoRawStoredPayload(
  normalizedArgs: Record<string, unknown>,
  checkpoint: Record<string, unknown>,
) {
  if (
    hasForbiddenStoredKey(normalizedArgs)
    || hasForbiddenStoredKey(checkpoint)
  ) {
    throw new ControlPlaneHttpError(
      409,
      "prepared_action_raw_payload_forbidden",
      "PreparedAction metadata contains a forbidden raw or credential field.",
    );
  }
}

export function preparedActionHash(row: {
  workspace_id: string;
  task_id: string;
  run_id: string;
  tool_call_id: string | null;
  requested_by_agent_id: string;
  action_type: string;
  normalized_args_json: string;
  target_resource: string | null;
  risk_level: string;
  policy_version: string;
  checkpoint_json: string;
  idempotency_key: string;
  expires_at: string | null;
}) {
  return stableHash({
    workspace_id: row.workspace_id || "local-demo",
    task_id: row.task_id,
    run_id: row.run_id,
    tool_call_id: row.tool_call_id,
    requested_by_agent_id: row.requested_by_agent_id,
    action_type: row.action_type,
    normalized_args_json: row.normalized_args_json || "{}",
    target_resource: row.target_resource,
    risk_level: row.risk_level,
    policy_version: row.policy_version || "approval-wall-v1",
    checkpoint_json: row.checkpoint_json || "{}",
    idempotency_key: row.idempotency_key,
    expires_at: row.expires_at,
  });
}

function publicPreparedAction(
  row: PreparedActionRow,
  normalizedArgs: Record<string, unknown>,
  checkpoint: Record<string, unknown>,
) {
  return {
    action_id: row.action_id,
    workspace_id: row.workspace_id,
    task_id: row.task_id,
    run_id: row.run_id,
    tool_call_id: row.tool_call_id,
    approval_id: row.approval_id,
    requested_by_agent_id: row.requested_by_agent_id,
    action_type: row.action_type,
    normalized_args: normalizedArgs,
    target_resource: row.target_resource,
    risk_level: row.risk_level,
    policy_version: row.policy_version,
    checkpoint,
    action_hash: row.action_hash,
    idempotency_key: row.idempotency_key,
    status: row.status,
    provider_side_effect_id: row.provider_side_effect_id,
    result_summary: row.result_summary,
    created_at: row.created_at,
    approved_at: row.approved_at,
    consumed_at: row.consumed_at,
    expires_at: row.expires_at,
    raw_provider_output_omitted: true,
    raw_prompt_omitted: true,
    raw_response_omitted: true,
    token_omitted: true,
  };
}

function publicApproval(row: ApprovalRow) {
  return {
    approval_id: row.approval_id,
    approval_kind: row.approval_kind,
    task_id: row.task_id,
    run_id: row.run_id,
    tool_call_id: row.tool_call_id,
    requested_by_agent_id: row.requested_by_agent_id,
    approver_user_id: row.approver_user_id,
    decision: row.decision,
    reason: row.reason,
    expires_at: row.expires_at,
    created_at: row.created_at,
    decided_at: row.decided_at,
  };
}

function publicExecutionLease(row: ExecutionLeaseRow | undefined) {
  if (!row) return null;
  return {
    lease_id: row.lease_id,
    action_id: row.action_id,
    workspace_id: row.workspace_id,
    requested_by_agent_id: row.requested_by_agent_id,
    action_hash: row.action_hash,
    status: row.status,
    started_at: row.started_at,
    expires_at: row.expires_at,
    completed_at: row.completed_at,
    failure_reason: row.failure_reason,
    claim_request_hash: row.claim_request_hash,
    claim_idempotency_hash: row.claim_idempotency_hash,
    execute_once: true,
    token_omitted: true,
  };
}

function publicExecutionReceipt(row: ExecutionReceiptRow | undefined) {
  if (!row) return null;
  return {
    receipt_id: row.receipt_id,
    lease_id: row.lease_id,
    action_id: row.action_id,
    workspace_id: row.workspace_id,
    requested_by_agent_id: row.requested_by_agent_id,
    action_hash: row.action_hash,
    receipt_request_hash: row.receipt_request_hash,
    outcome: row.outcome,
    provider_call_performed: row.provider_call_performed,
    provider_call_may_have_completed: row.provider_call_may_have_completed,
    terminal_evidence_hash: row.terminal_evidence_hash,
    terminal_evidence_source: row.terminal_evidence_source,
    terminal_evidence_verified: row.terminal_evidence_verified,
    automatic_retry_allowed: row.automatic_retry_allowed,
    retry_requires_new_action: row.retry_requires_new_action,
    raw_provider_output_omitted: row.raw_provider_output_omitted,
    raw_prompt_omitted: row.raw_prompt_omitted,
    raw_response_omitted: row.raw_response_omitted,
    token_omitted: true,
    terminal_at: row.terminal_at,
  };
}

async function assertPreparedActionSchemaReady(client: PoolClient) {
  const tables = Object.keys(REQUIRED_V6_COLUMNS);
  const columns = await client.query<{ table_name: string; column_name: string }>(
    `SELECT table_name,column_name
    FROM information_schema.columns
    WHERE table_schema=current_schema() AND table_name=ANY($1::text[])`,
    [tables],
  ).catch(() => ({ rows: [] as Array<{ table_name: string; column_name: string }> }));
  const presentColumns = new Set(
    columns.rows.map((row) => `${row.table_name}.${row.column_name}`),
  );
  const missingColumn = Object.entries(REQUIRED_V6_COLUMNS).some(
    ([table, required]) =>
      required.some((column) => !presentColumns.has(`${table}.${column}`)),
  );
  const triggers = await client.query<{ trigger_name: string }>(
    `SELECT trigger_record.tgname AS trigger_name
    FROM pg_trigger trigger_record
    JOIN pg_class relation ON relation.oid=trigger_record.tgrelid
    JOIN pg_namespace namespace ON namespace.oid=relation.relnamespace
    WHERE namespace.nspname=current_schema()
      AND NOT trigger_record.tgisinternal
      AND trigger_record.tgenabled='O'
      AND trigger_record.tgname=ANY($1::text[])`,
    [[...REQUIRED_V6_TRIGGERS]],
  ).catch(() => ({ rows: [] as Array<{ trigger_name: string }> }));
  const presentTriggers = new Set(triggers.rows.map((row) => row.trigger_name));
  const indexRows = await client.query<{ relation: string | null }>(
    `SELECT to_regclass(
      'idx_prepared_action_lease_claim_idempotency_v6'
    )::text AS relation`,
  ).catch(() => ({ rows: [{ relation: null }] }));
  if (
    missingColumn
    || REQUIRED_V6_TRIGGERS.some((trigger) => !presentTriggers.has(trigger))
    || !indexRows.rows[0]?.relation
  ) {
    throw new ControlPlaneHttpError(
      503,
      "prepared_action_schema_not_ready",
      "PreparedAction execution schema v6 is not ready.",
    );
  }
}

function enforceAgentBinding(
  identity: AgentGatewayIdentity,
  request: Request,
  body?: Record<string, unknown>,
) {
  for (const value of [
    request.headers.get("x-agentops-agent-id"),
    body?.agent_id,
  ]) {
    if (value !== undefined && value !== null && value !== "") {
      if (identifier(value, "agent_id") !== identity.agentId) {
        throw new ControlPlaneHttpError(
          403,
          "forbidden",
          "Agent credential cannot act for another agent.",
        );
      }
    }
  }
}

async function authorize(
  client: PoolClient,
  request: Request,
  scope: string,
  body?: Record<string, unknown>,
) {
  const identity = await authenticateAgentGateway(client, request.headers, scope);
  const requestedWorkspace = body
    && Object.prototype.hasOwnProperty.call(body, "workspace_id")
    ? identifier(body.workspace_id, "workspace_id")
    : undefined;
  enforceWorkspaceBinding(identity, {
    header: request.headers.get("x-agentops-workspace-id"),
    body: requestedWorkspace,
  });
  enforceAgentBinding(identity, request, body);
  return identity;
}

async function loadBoundAction(
  client: PoolClient,
  identity: AgentGatewayIdentity,
  actionId: string,
): Promise<BoundAction> {
  const actionResult = await client.query<PreparedActionRow>(
    `SELECT action_id,workspace_id,task_id,run_id,tool_call_id,approval_id,
      requested_by_agent_id,action_type,normalized_args_json,target_resource,
      risk_level,policy_version,checkpoint_json,action_hash,idempotency_key,
      status,provider_side_effect_id,result_summary,created_at,approved_at,
      consumed_at,expires_at
    FROM prepared_actions WHERE action_id=$1 FOR UPDATE`,
    [actionId],
  );
  const action = actionResult.rows[0];
  if (!action) {
    throw new ControlPlaneHttpError(
      404,
      "prepared_action_not_found",
      "PreparedAction was not found.",
    );
  }
  if (action.workspace_id !== identity.workspaceId) {
    throw new ControlPlaneHttpError(
      403,
      "forbidden",
      "PreparedAction belongs to another workspace.",
    );
  }
  if (action.requested_by_agent_id !== identity.agentId) {
    throw new ControlPlaneHttpError(
      403,
      "forbidden",
      "PreparedAction belongs to another agent.",
    );
  }

  const approvalResult = await client.query<ApprovalRow>(
    `SELECT approval_id,approval_kind,task_id,run_id,tool_call_id,
      requested_by_agent_id,approver_user_id,decision,reason,expires_at,
      created_at,decided_at
    FROM approvals WHERE approval_id=$1 FOR UPDATE`,
    [action.approval_id],
  );
  const runResult = await client.query<RunRow>(
    `SELECT run_id,workspace_id,task_id,agent_id,runtime_type,status,
      started_at,ended_at,approval_required,agent_plan_id,plan_hash
    FROM runs WHERE run_id=$1 FOR UPDATE`,
    [action.run_id],
  );
  const taskResult = await client.query<TaskRow>(
    `SELECT task_id,workspace_id,owner_agent_id,status,updated_at
    FROM tasks WHERE task_id=$1 FOR UPDATE`,
    [action.task_id],
  );
  const toolResult = action.tool_call_id
    ? await client.query<ToolCallRow>(
      `SELECT tool_call_id,run_id,agent_id,tool_name,normalized_args_json,
        target_resource,risk_level,status,side_effect_id,ended_at
      FROM tool_calls WHERE tool_call_id=$1 FOR UPDATE`,
      [action.tool_call_id],
    )
    : { rows: [] as ToolCallRow[] };
  const planResult = runResult.rows[0]?.agent_plan_id
    ? await client.query<AgentPlanRow>(
      `SELECT plan_id,workspace_id,task_id,run_id,agent_id,task_understanding,
        referenced_specs_json,referenced_memories_json,referenced_bases_json,
        proposed_files_to_change_json,risk_level,approval_required,
        execution_steps_json,verification_plan,rollback_plan,status,plan_version,
        plan_hash,verified_at,verification_result_hash,created_at
      FROM agent_plans WHERE plan_id=$1 FOR UPDATE`,
      [runResult.rows[0].agent_plan_id],
    )
    : { rows: [] as AgentPlanRow[] };
  const approval = approvalResult.rows[0];
  const run = runResult.rows[0];
  const task = taskResult.rows[0];
  const tool = toolResult.rows[0];
  const plan = planResult.rows[0];
  if (!approval || !run || !task || !tool || !plan) {
    throw new ControlPlaneHttpError(
      409,
      "prepared_action_binding_invalid",
      "PreparedAction authority graph is incomplete.",
    );
  }
  if (
    action.action_type !== ACTION_TYPE
    || action.policy_version !== "approval-wall-codex-workspace-write-v2"
    || !["high", "critical"].includes(action.risk_level)
    || approval.approval_kind !== "prepared_action"
    || approval.approval_id !== action.approval_id
    || approval.task_id !== action.task_id
    || approval.run_id !== action.run_id
    || approval.tool_call_id !== action.tool_call_id
    || approval.requested_by_agent_id !== action.requested_by_agent_id
    || run.workspace_id !== action.workspace_id
    || run.task_id !== action.task_id
    || run.agent_id !== action.requested_by_agent_id
    || run.runtime_type !== "codex"
    || task.workspace_id !== action.workspace_id
    || tool.run_id !== action.run_id
    || tool.agent_id !== action.requested_by_agent_id
    || tool.tool_name !== ACTION_TYPE
    || tool.target_resource !== action.target_resource
    || tool.risk_level !== action.risk_level
    || plan.plan_id !== run.agent_plan_id
    || plan.workspace_id !== action.workspace_id
    || plan.task_id !== action.task_id
    || (plan.run_id !== null && plan.run_id !== action.run_id)
    || plan.agent_id !== action.requested_by_agent_id
  ) {
    throw new ControlPlaneHttpError(
      409,
      "prepared_action_binding_invalid",
      "PreparedAction workspace, agent, task, run, approval, or tool binding is invalid.",
    );
  }

  const normalizedArgs = parseJsonObject(
    action.normalized_args_json,
    "normalized_args_json",
  );
  const checkpoint = parseJsonObject(
    action.checkpoint_json,
    "checkpoint_json",
    16 * 1024,
  );
  assertNoRawStoredPayload(normalizedArgs, checkpoint);
  const toolArgs = parseJsonObject(
    tool.normalized_args_json,
    "tool normalized args",
  );
  const sourceRepoHash = String(normalizedArgs.source_repo_hash || "");
  const baselineHead = String(normalizedArgs.baseline_head || "");
  const allowedPaths = Array.isArray(normalizedArgs.allowed_paths)
    ? normalizedArgs.allowed_paths.map(String)
    : [];
  const runtimeAttestation = normalizedArgs.runtime_attestation
    && typeof normalizedArgs.runtime_attestation === "object"
    && !Array.isArray(normalizedArgs.runtime_attestation)
    ? normalizedArgs.runtime_attestation as Record<string, unknown>
    : {};
  const checkpointAttestation = checkpoint.runtime_attestation
    && typeof checkpoint.runtime_attestation === "object"
    && !Array.isArray(checkpoint.runtime_attestation)
    ? checkpoint.runtime_attestation as Record<string, unknown>
    : {};
  if (
    normalizedArgs.task_id !== action.task_id
    || normalizedArgs.run_id !== action.run_id
    || normalizedArgs.adapter !== "codex"
    || normalizedArgs.execution_mode !== "workspace-write"
    || normalizedArgs.external_write_intent !== true
    || normalizedArgs.requires_prepared_action_for_external_write !== true
    || normalizedArgs.agent_plan_id !== run.agent_plan_id
    || normalizedArgs.agent_plan_hash !== run.plan_hash
    || normalizedArgs.agent_plan_verification_result_hash === undefined
    || stableHash(toolArgs) !== stableHash(normalizedArgs)
    || normalizedArgs.target_resource !== action.target_resource
    || normalizedArgs.source_repo_clean !== true
    || normalizedArgs.workspace_isolation !== "managed_detached_git_worktree"
    || normalizedArgs.rollback_strategy
      !== "remove_managed_worktree_before_promotion"
    || !SHA256_HEX.test(sourceRepoHash)
    || !/^[a-f0-9]{40}(?:[a-f0-9]{24})?$/.test(baselineHead)
    || action.target_resource
      !== `git+local://sha256/${sourceRepoHash}@${baselineHead}`
    || !allowedPaths.length
    || allowedPaths.some((path) => !safeRelativePath(path))
    || runtimeAttestation.attested !== true
    || !SHA256_HEX.test(String(runtimeAttestation.binary_sha256 || ""))
    || !String(runtimeAttestation.version_summary || "").trim()
    || checkpoint.checkpoint !== "before_codex_workspace_write_execution"
    || checkpoint.task_id !== action.task_id
    || checkpoint.run_id !== action.run_id
    || checkpoint.adapter !== "codex"
    || checkpoint.agent_plan_id !== run.agent_plan_id
    || checkpoint.baseline_head !== baselineHead
    || !exactStringList(checkpoint.allowed_paths, allowedPaths)
    || stableHash(checkpointAttestation) !== stableHash(runtimeAttestation)
    || !SHA256_HEX.test(String(normalizedArgs.agent_plan_hash || ""))
    || !SHA256_HEX.test(
      String(normalizedArgs.agent_plan_verification_result_hash || ""),
    )
  ) {
    throw new ControlPlaneHttpError(
      409,
      "prepared_action_plan_binding_invalid",
      "PreparedAction plan, task, or run binding is invalid.",
    );
  }

  return {
    action,
    approval,
    run,
    task,
    tool,
    plan,
    normalizedArgs,
    checkpoint,
    currentActionHash: preparedActionHash(action),
  };
}

function hashVerification(graph: BoundAction) {
  return {
    stored_action_hash: graph.action.action_hash,
    current_action_hash: graph.currentActionHash,
    match: graph.action.action_hash === graph.currentActionHash,
  };
}

function parseExpiry(value: string | null) {
  const parsed = Date.parse(String(value || ""));
  return Number.isFinite(parsed) ? parsed : null;
}

async function lockPreparedActionMutation(
  client: PoolClient,
  workspaceId: string,
  actionId: string,
) {
  await client.query("SELECT pg_advisory_xact_lock(hashtext($1))", [
    `agentops-prepared-action-decision:${workspaceId}:${actionId}`,
  ]);
}

function assertApprovedActionAuthority(graph: BoundAction) {
  if (!SHA256_HEX.test(graph.action.action_hash)) {
    throw new ControlPlaneHttpError(
      409,
      "action_hash_invalid",
      "PreparedAction hash is not a SHA-256 digest.",
    );
  }
  if (graph.currentActionHash !== graph.action.action_hash) {
    throw new ControlPlaneHttpError(
      409,
      "action_hash_mismatch",
      "PreparedAction changed after approval; create a new action.",
    );
  }
  if (
    graph.action.status !== "approved"
    || graph.approval.decision !== "approved"
    || !graph.action.approved_at
    || !graph.approval.decided_at
    || !graph.approval.approver_user_id
    || graph.action.consumed_at
    || graph.action.provider_side_effect_id
  ) {
    throw new ControlPlaneHttpError(
      409,
      "prepared_action_not_approved",
      "PreparedAction is not in an approved executable state.",
    );
  }
  const actionExpiry = parseExpiry(graph.action.expires_at);
  const approvalExpiry = parseExpiry(graph.approval.expires_at);
  if (
    actionExpiry === null
    || approvalExpiry === null
    || actionExpiry <= Date.now()
    || approvalExpiry <= Date.now()
  ) {
    throw new ControlPlaneHttpError(
      409,
      "prepared_action_expired",
      "PreparedAction or approval has expired.",
    );
  }
}

function currentAssignmentAndPlanValid(graph: BoundAction) {
  const currentPlanHash = computeAgentPlanHash(graph.plan);
  const planVerification = verifyAgentPlanRow(graph.plan);
  const verifiedAt = Date.parse(String(graph.plan.verified_at || ""));
  const createdAt = Date.parse(graph.plan.created_at);
  return !(
    graph.task.owner_agent_id !== graph.action.requested_by_agent_id
    || graph.run.agent_id !== graph.action.requested_by_agent_id
    || graph.run.agent_plan_id !== graph.plan.plan_id
    || graph.run.plan_hash !== graph.plan.plan_hash
    || graph.plan.status !== "approved"
    || graph.plan.plan_hash !== currentPlanHash
    || graph.plan.plan_hash !== graph.normalizedArgs.agent_plan_hash
    || graph.plan.verification_result_hash
      !== graph.normalizedArgs.agent_plan_verification_result_hash
    || !planVerification.pass
    || graph.plan.verification_result_hash
      !== agentPlanVerificationHash(graph.plan.plan_id, planVerification)
    || !SHA256_HEX.test(String(graph.plan.plan_hash || ""))
    || !SHA256_HEX.test(
      String(graph.plan.verification_result_hash || ""),
    )
    || !Number.isFinite(verifiedAt)
    || !Number.isFinite(createdAt)
    || verifiedAt < createdAt
    || verifiedAt > Date.now() + 60_000
  );
}

function assertCurrentAssignmentAndPlan(graph: BoundAction) {
  if (!currentAssignmentAndPlanValid(graph)) {
    throw new ControlPlaneHttpError(
      409,
      "prepared_action_current_assignment_invalid",
      "PreparedAction no longer binds the current task owner, run Agent, or verified Agent Plan.",
    );
  }
}

function assertExecutionState(
  graph: BoundAction,
  phase: "claim" | "executing",
) {
  assertApprovedActionAuthority(graph);
  assertCurrentAssignmentAndPlan(graph);
  const expectedStatus = phase === "claim" ? "waiting_approval" : "running";
  if (
    graph.run.status !== expectedStatus
    || graph.run.ended_at !== null
    || graph.task.status !== expectedStatus
    || graph.tool.status !== expectedStatus
    || graph.tool.side_effect_id !== null
    || graph.tool.ended_at !== null
  ) {
    throw new ControlPlaneHttpError(
      409,
      "prepared_action_parent_state_invalid",
      `PreparedAction run, task, and exact tool are not at the ${phase} checkpoint.`,
    );
  }
}

function assertTerminalExecutionState(
  graph: BoundAction,
  providerSideEffectId: string,
) {
  assertCurrentAssignmentAndPlan(graph);
  if (
    graph.action.status !== "consumed"
    || graph.approval.decision !== "approved"
    || graph.action.provider_side_effect_id !== providerSideEffectId
    || graph.action.consumed_at === null
    || graph.run.status !== "completed"
    || graph.run.ended_at === null
    || graph.task.status !== "completed"
    || graph.tool.status !== "completed"
    || graph.tool.side_effect_id !== providerSideEffectId
    || graph.tool.ended_at === null
  ) {
    throw new ControlPlaneHttpError(
      409,
      "prepared_action_terminal_state_invalid",
      "PreparedAction terminal state no longer matches its current assignment and side effect.",
    );
  }
}

async function loadLease(client: PoolClient, actionId: string) {
  const result = await client.query<ExecutionLeaseRow>(
    `SELECT lease_id,action_id,workspace_id,requested_by_agent_id,action_hash,
      status,started_at,expires_at,completed_at,failure_reason,
      claim_request_hash,claim_idempotency_hash,claim_identity_source
    FROM prepared_action_execution_leases WHERE action_id=$1 FOR UPDATE`,
    [actionId],
  );
  return result.rows[0];
}

async function loadReceipt(client: PoolClient, actionId: string) {
  const result = await client.query<ExecutionReceiptRow>(
    `SELECT receipt_id,lease_id,action_id,workspace_id,requested_by_agent_id,
      action_hash,claim_request_hash,claim_idempotency_hash,
      receipt_request_hash,outcome,provider_call_performed,
      provider_call_may_have_completed,terminal_evidence_hash,
      terminal_evidence_source,terminal_evidence_verified,
      automatic_retry_allowed,retry_requires_new_action,
      raw_provider_output_omitted,raw_prompt_omitted,raw_response_omitted,
      token_omitted,terminal_at
    FROM prepared_action_execution_receipts WHERE action_id=$1 FOR UPDATE`,
    [actionId],
  );
  return result.rows[0];
}

function assertLeaseBinding(graph: BoundAction, lease: ExecutionLeaseRow) {
  if (
    lease.action_id !== graph.action.action_id
    || lease.workspace_id !== graph.action.workspace_id
    || lease.requested_by_agent_id !== graph.action.requested_by_agent_id
    || lease.action_hash !== graph.action.action_hash
    || !SHA256_HEX.test(lease.claim_request_hash)
    || !SHA256_HEX.test(lease.claim_idempotency_hash)
    || lease.claim_identity_source !== "request_hash_v1"
  ) {
    throw new ControlPlaneHttpError(
      409,
      "prepared_action_execution_lease_binding_invalid",
      "PreparedAction execution lease binding is invalid.",
    );
  }
}

function assertReceiptBinding(
  graph: BoundAction,
  lease: ExecutionLeaseRow,
  receipt: ExecutionReceiptRow,
) {
  if (
    receipt.lease_id !== lease.lease_id
    || receipt.action_id !== graph.action.action_id
    || receipt.workspace_id !== graph.action.workspace_id
    || receipt.requested_by_agent_id !== graph.action.requested_by_agent_id
    || receipt.action_hash !== graph.action.action_hash
    || receipt.claim_request_hash !== lease.claim_request_hash
    || receipt.claim_idempotency_hash !== lease.claim_idempotency_hash
    || receipt.automatic_retry_allowed !== false
    || receipt.retry_requires_new_action !== true
    || receipt.raw_provider_output_omitted !== true
    || receipt.raw_prompt_omitted !== true
    || receipt.raw_response_omitted !== true
    || receipt.token_omitted !== true
  ) {
    throw new ControlPlaneHttpError(
      409,
      "prepared_action_terminal_receipt_binding_invalid",
      "PreparedAction terminal receipt binding is invalid.",
    );
  }
}

function receiptId(requestHash: string) {
  return `pa_receipt_${requestHash.slice(0, 24)}`;
}

async function insertReceipt(
  client: PoolClient,
  input: {
    graph: BoundAction;
    lease: ExecutionLeaseRow;
    receiptRequestHash: string;
    outcome: "succeeded" | "failed" | "unknown";
    providerCallPerformed: boolean;
    providerCallMayHaveCompleted: boolean;
    terminalEvidenceHash: string | null;
    terminalEvidenceSource:
      | "worker_verified_v1"
      | "control_plane_failure_v1"
      | "control_plane_timeout_v1";
    terminalEvidenceVerified: boolean;
    terminalAt: string;
  },
) {
  const result = await client.query<ExecutionReceiptRow>(
    `INSERT INTO prepared_action_execution_receipts(
      receipt_id,lease_id,action_id,workspace_id,requested_by_agent_id,
      action_hash,claim_request_hash,claim_idempotency_hash,
      receipt_request_hash,outcome,provider_call_performed,
      provider_call_may_have_completed,terminal_evidence_hash,
      terminal_evidence_source,terminal_evidence_verified,
      automatic_retry_allowed,retry_requires_new_action,
      raw_provider_output_omitted,raw_prompt_omitted,raw_response_omitted,
      token_omitted,terminal_at
    ) VALUES(
      $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,
      FALSE,TRUE,TRUE,TRUE,TRUE,TRUE,$16
    )
    RETURNING receipt_id,lease_id,action_id,workspace_id,
      requested_by_agent_id,action_hash,claim_request_hash,
      claim_idempotency_hash,receipt_request_hash,outcome,
      provider_call_performed,provider_call_may_have_completed,
      terminal_evidence_hash,terminal_evidence_source,
      terminal_evidence_verified,automatic_retry_allowed,
      retry_requires_new_action,raw_provider_output_omitted,
      raw_prompt_omitted,raw_response_omitted,token_omitted,terminal_at`,
    [
      receiptId(input.receiptRequestHash),
      input.lease.lease_id,
      input.graph.action.action_id,
      input.graph.action.workspace_id,
      input.graph.action.requested_by_agent_id,
      input.graph.action.action_hash,
      input.lease.claim_request_hash,
      input.lease.claim_idempotency_hash,
      input.receiptRequestHash,
      input.outcome,
      input.providerCallPerformed,
      input.providerCallMayHaveCompleted,
      input.terminalEvidenceHash,
      input.terminalEvidenceSource,
      input.terminalEvidenceVerified,
      input.terminalAt,
    ],
  );
  const receipt = result.rows[0];
  if (!receipt) {
    throw new ControlPlaneHttpError(
      409,
      "prepared_action_terminal_receipt_missing",
      "PreparedAction terminal receipt was not created.",
    );
  }
  return receipt;
}

async function readCurrentAction(client: PoolClient, actionId: string) {
  const result = await client.query<PreparedActionRow>(
    `SELECT action_id,workspace_id,task_id,run_id,tool_call_id,approval_id,
      requested_by_agent_id,action_type,normalized_args_json,target_resource,
      risk_level,policy_version,checkpoint_json,action_hash,idempotency_key,
      status,provider_side_effect_id,result_summary,created_at,approved_at,
      consumed_at,expires_at
    FROM prepared_actions WHERE action_id=$1`,
    [actionId],
  );
  return result.rows[0];
}

async function readCurrentLease(client: PoolClient, leaseId: string) {
  const result = await client.query<ExecutionLeaseRow>(
    `SELECT lease_id,action_id,workspace_id,requested_by_agent_id,action_hash,
      status,started_at,expires_at,completed_at,failure_reason,
      claim_request_hash,claim_idempotency_hash,claim_identity_source
    FROM prepared_action_execution_leases WHERE lease_id=$1`,
    [leaseId],
  );
  return result.rows[0];
}

async function terminalizeExpiredLease(
  client: PoolClient,
  graph: BoundAction,
  lease: ExecutionLeaseRow,
): Promise<PreparedActionResult> {
  const reason =
    "Execution lease expired before verified evidence closure; automatic retry is forbidden.";
  const terminalAt = new Date().toISOString();
  const receiptRequestHash = stableHash({
    operation: "prepared_action_execution_timeout_v1",
    action_id: graph.action.action_id,
    action_hash: graph.action.action_hash,
    lease_id: lease.lease_id,
    workspace_id: graph.action.workspace_id,
    agent_id: graph.action.requested_by_agent_id,
    lease_expires_at: lease.expires_at,
  });
  const terminalCost = await settleExistingTerminalRunCost(client, {
    workspaceId: graph.action.workspace_id,
    runId: graph.run.run_id,
    terminalStatus: "blocked",
  });
  const actionUpdate = await client.query(
    `UPDATE prepared_actions
    SET status='expired',result_summary=$1
    WHERE action_id=$2 AND status='approved'`,
    [reason, graph.action.action_id],
  );
  const leaseUpdate = await client.query(
    `UPDATE prepared_action_execution_leases
    SET status='failed',completed_at=$1,failure_reason=$2
    WHERE lease_id=$3 AND status='executing'`,
    [terminalAt, reason, lease.lease_id],
  );
  if (actionUpdate.rowCount !== 1 || leaseUpdate.rowCount !== 1) {
    throw new ControlPlaneHttpError(
      409,
      "prepared_action_execution_timeout_conflict",
      "Expired execution lease could not be closed atomically.",
    );
  }
  await client.query(
    `UPDATE runs SET status='blocked',approval_required=0,
      error_type='CodexWorkspaceWriteLeaseExpired',error_message=$1,
      ended_at=$2 WHERE run_id=$3`,
    [reason, terminalAt, graph.run.run_id],
  );
  await client.query(
    "UPDATE tasks SET status='blocked',updated_at=$1 WHERE task_id=$2",
    [terminalAt, graph.task.task_id],
  );
  await client.query(
    `UPDATE tool_calls SET status='blocked',result_summary=$1,ended_at=$2
    WHERE tool_call_id=$3`,
    [reason, terminalAt, graph.tool.tool_call_id],
  );
  await client.query(
    "UPDATE agents SET status='idle',updated_at=$1 WHERE agent_id=$2",
    [terminalAt, graph.action.requested_by_agent_id],
  );
  const terminalLease = (await readCurrentLease(client, lease.lease_id)) || lease;
  const receipt = await insertReceipt(client, {
    graph,
    lease: terminalLease,
    receiptRequestHash,
    outcome: "unknown",
    providerCallPerformed: false,
    providerCallMayHaveCompleted: true,
    terminalEvidenceHash: null,
    terminalEvidenceSource: "control_plane_timeout_v1",
    terminalEvidenceVerified: false,
    terminalAt,
  });
  await appendRuntimeEvent(client, {
    eventType: "prepared_action.execution_timeout",
    status: "blocked",
    runId: graph.run.run_id,
    taskId: graph.task.task_id,
    agentId: graph.action.requested_by_agent_id,
    outputSummary: reason,
    rawPayloadHash: graph.action.action_hash,
  });
  await appendAudit(client, {
    workspaceId: graph.action.workspace_id,
    actorType: "system",
    actorId: null,
    action: "approval_wall.prepared_action_execution_timeout",
    entityType: "prepared_actions",
    entityId: graph.action.action_id,
    before: publicPreparedAction(
      graph.action,
      graph.normalizedArgs,
      graph.checkpoint,
    ),
    after: {
      action_id: graph.action.action_id,
      status: "expired",
      result_summary: reason,
    },
    metadata: {
      lease_id: lease.lease_id,
      receipt_id: receipt.receipt_id,
      receipt_outcome: "unknown",
      run_cost_closure: terminalCost.mode,
      run_cost_reservation_state: terminalCost.reservation?.state || null,
      provider_call_may_have_completed: true,
      automatic_retry_allowed: false,
      retry_requires_new_action: true,
      raw_provider_output_omitted: true,
      raw_prompt_omitted: true,
      raw_response_omitted: true,
      token_omitted: true,
    },
  });
  return {
    status: 409,
    body: {
      ok: false,
      error: "prepared_action_execution_lease_expired",
      message: reason,
      execution_lease: publicExecutionLease(terminalLease),
      execution_receipt: publicExecutionReceipt(receipt),
      automatic_retry_allowed: false,
      retry_requires_new_action: true,
      token_omitted: true,
    },
  };
}

function identifierList(value: string, field: string) {
  const parsed = jsonList(value).map((item) => identifier(item, field));
  if (!parsed.length || new Set(parsed).size !== parsed.length) {
    throw new ControlPlaneHttpError(
      428,
      "verified_plan_evidence_manifest_required",
      `${field} must contain unique evidence identifiers.`,
    );
  }
  return parsed;
}

function exactStringList(left: unknown, right: unknown) {
  if (!Array.isArray(left) || !Array.isArray(right)) return false;
  const normalizedLeft = left.map(String);
  const normalizedRight = right.map(String);
  return normalizedLeft.length === normalizedRight.length
    && normalizedLeft.every((item, index) => item === normalizedRight[index]);
}

function exactIdentifierSet(left: string[], right: string[]) {
  if (left.length !== right.length) return false;
  const expected = new Set(right);
  return expected.size === right.length
    && left.every((item) => expected.has(item));
}

function safeRelativePath(value: unknown) {
  const path = String(value || "").trim();
  return Boolean(path)
    && !path.startsWith("/")
    && !path.startsWith("~")
    && !path.includes("\\")
    && !path.split("/").includes("..");
}

function pathInScope(path: string, scopes: string[]) {
  return scopes.some(
    (scope) => path === scope || path.startsWith(`${scope.replace(/\/+$/, "")}/`),
  );
}

async function verifyWorkspacePlanEvidence(
  client: PoolClient,
  graph: BoundAction,
  lease: ExecutionLeaseRow,
  manifestId: string,
  providerSideEffectId: string,
) {
  const current = await verifyCurrentAgentGatewayPlanEvidenceManifest(
    client,
    {
      manifestId,
      workspaceId: graph.action.workspace_id,
      taskId: graph.action.task_id,
      runId: graph.action.run_id,
      agentId: graph.action.requested_by_agent_id,
      requireCommercialRuntime: false,
    },
  );
  if (!current) {
    throw new ControlPlaneHttpError(
      428,
      "verified_plan_evidence_manifest_required",
      "A current verified plan evidence manifest with exact bindings is required.",
    );
  }
  const { manifest, plan, verification } = current;
  const storedVerification = parseJsonObject(
    manifest.verification_json,
    "manifest.verification_json",
  );
  const storedCheckpoint = storedVerification.execution_checkpoint
    && typeof storedVerification.execution_checkpoint === "object"
    && !Array.isArray(storedVerification.execution_checkpoint)
    ? storedVerification.execution_checkpoint as Record<string, unknown>
    : {};
  const currentCheckpoint = verification.execution_checkpoint;
  const currentCheckpointBound = currentCheckpoint.kind === "terminal_run_v1"
    ? graph.action.status === "consumed" && lease.status === "completed"
    : currentCheckpoint.kind === "prepared_action_execution_v1"
      && graph.action.status === "approved"
      && lease.status === "executing"
      && currentCheckpoint.prepared_action_id === graph.action.action_id
      && currentCheckpoint.execution_lease_id === lease.lease_id
      && currentCheckpoint.active_tool_call_id === graph.tool.tool_call_id
      && currentCheckpoint.action_hash === graph.action.action_hash;
  if (
    manifest.plan_id !== graph.plan.plan_id
    || manifest.status !== "verified"
    || manifest.mismatch_policy !== "block"
    || manifest.plan_hash !== graph.plan.plan_hash
    || manifest.verification_result_hash
      !== graph.plan.verification_result_hash
    || plan.plan_id !== graph.plan.plan_id
    || plan.plan_hash !== graph.plan.plan_hash
    || plan.verification_result_hash
      !== graph.plan.verification_result_hash
    || verification.pass !== true
    || verification.status !== "verified"
    || verification.failed_checks.length !== 0
    || !currentCheckpointBound
    || storedVerification.pass !== true
    || storedVerification.status !== "verified"
    || !Array.isArray(storedVerification.failed_checks)
    || storedVerification.failed_checks.length !== 0
    || storedCheckpoint.kind !== "prepared_action_execution_v1"
    || storedCheckpoint.valid !== true
    || storedCheckpoint.prepared_action_id !== graph.action.action_id
    || storedCheckpoint.execution_lease_id !== lease.lease_id
    || storedCheckpoint.active_tool_call_id !== graph.tool.tool_call_id
    || storedCheckpoint.action_hash !== graph.action.action_hash
    || manifest.audit_ids_json !== "[]"
    || !exactStringList(
      jsonList(manifest.expected_steps_json),
      jsonList(plan.execution_steps_json),
    )
  ) {
    throw new ControlPlaneHttpError(
      428,
      "verified_plan_evidence_manifest_required",
      "Plan evidence is stale, selective, or no longer verifies at the current execution checkpoint.",
    );
  }

  const toolIds = identifierList(manifest.tool_call_ids_json, "tool_call_id");
  const evaluationIds = identifierList(
    manifest.evaluation_ids_json,
    "evaluation_id",
  );
  const artifactIds = identifierList(manifest.artifact_ids_json, "artifact_id");
  const toolRows = await client.query<EvidenceToolRow>(
    `SELECT tool_call_id,run_id,agent_id,tool_name,normalized_args_json,status
    FROM tool_calls
    WHERE run_id=$1 AND agent_id=$2
    ORDER BY created_at,tool_call_id
    FOR SHARE`,
    [graph.run.run_id, graph.run.agent_id],
  );
  const evaluationRows = await client.query<EvidenceEvaluationRow>(
    `SELECT evaluation_id,task_id,run_id,agent_id,evaluator_type,pass_fail,
      rubric_json
    FROM evaluations
    WHERE run_id=$1 AND agent_id=$2
    ORDER BY created_at,evaluation_id
    FOR SHARE`,
    [graph.run.run_id, graph.run.agent_id],
  );
  const artifactRows = await client.query<EvidenceArtifactRow>(
    `SELECT artifact.artifact_id,artifact.task_id,artifact.run_id,
      artifact.artifact_type,artifact.content_hash
    FROM artifacts artifact
    LEFT JOIN runs run ON run.run_id=artifact.run_id
    LEFT JOIN tasks task ON task.task_id=artifact.task_id
    WHERE (
        artifact.run_id=$1
        OR (artifact.run_id IS NULL AND artifact.task_id=$2)
      )
      AND (artifact.run_id IS NULL OR run.workspace_id=$3)
      AND (artifact.task_id IS NULL OR task.workspace_id=$3)
      AND (run.workspace_id=$3 OR task.workspace_id=$3)
    ORDER BY artifact.artifact_id
    FOR SHARE OF artifact`,
    [
      graph.run.run_id,
      graph.task.task_id,
      graph.action.workspace_id,
    ],
  );
  const chainEntityIds = [
    manifest.manifest_id,
    plan.plan_id,
    graph.run.run_id,
    graph.task.task_id,
    ...toolRows.rows.map((row) => row.tool_call_id),
    ...evaluationRows.rows.map((row) => row.evaluation_id),
    ...artifactRows.rows.map((row) => row.artifact_id),
  ];
  const auditRows = await client.query<EvidenceAuditRow>(
    `SELECT audit_id,workspace_id,actor_type,actor_id,action,entity_type,
      entity_id,metadata_json,tamper_chain_hash
    FROM audit_logs
    WHERE workspace_id=$1
      AND entity_id=ANY($2::text[])
      AND metadata_json::jsonb ->> 'workspace_id'=$1
    ORDER BY created_at,audit_id
    FOR SHARE`,
    [graph.action.workspace_id, chainEntityIds],
  );
  if (
    !exactIdentifierSet(
      toolIds,
      toolRows.rows.map((row) => row.tool_call_id),
    )
    || !exactIdentifierSet(
      evaluationIds,
      evaluationRows.rows.map((row) => row.evaluation_id),
    )
    || !exactIdentifierSet(
      artifactIds,
      artifactRows.rows.map((row) => row.artifact_id),
    )
    || auditRows.rows.length === 0
    || toolRows.rows.some((row) =>
      row.run_id !== graph.run.run_id
      || row.agent_id !== graph.run.agent_id)
    || evaluationRows.rows.some((row) =>
      row.task_id !== graph.task.task_id
      || row.run_id !== graph.run.run_id
      || row.agent_id !== graph.run.agent_id
      || row.evaluator_type !== "rule"
      || row.pass_fail !== "pass")
    || artifactRows.rows.some((row) =>
      row.run_id !== graph.run.run_id
      || row.task_id !== graph.task.task_id
      || !SHA256_HEX.test(String(row.content_hash || "")))
    || auditRows.rows.some((row) =>
      row.workspace_id !== graph.action.workspace_id
      || !chainEntityIds.includes(row.entity_id)
      || !SHA256_HEX.test(String(row.tamper_chain_hash || "")))
  ) {
    throw new ControlPlaneHttpError(
      428,
      "verified_plan_evidence_manifest_required",
      "Declared plan evidence is missing or not bound to the run.",
    );
  }
  const manifestOwnerAuditBound = auditRows.rows.some(
    (row) =>
      row.actor_type === "agent"
      && row.actor_id === graph.action.requested_by_agent_id
      && row.action === "agent_gateway.plan_evidence_manifest_create"
      && row.entity_type === "plan_evidence_manifests"
      && row.entity_id === manifest.manifest_id,
  );
  if (!manifestOwnerAuditBound) {
    throw new ControlPlaneHttpError(
      428,
      "verified_plan_evidence_manifest_required",
      "Plan evidence manifest does not have a chained TypeScript/Postgres owner audit.",
    );
  }

  const verifierTools = toolRows.rows.filter(
    (row) => row.tool_name === "agent_worker.codex.workspace_diff_verify",
  );
  if (verifierTools.length !== 1) {
    throw new ControlPlaneHttpError(
      428,
      "verified_plan_evidence_manifest_required",
      "Exactly one governed workspace diff verifier is required.",
    );
  }
  const verifierArgs = parseJsonObject(
    verifierTools[0].normalized_args_json,
    "workspace diff verifier args",
  );
  const diffEvidenceHash = String(verifierArgs.diff_evidence_hash || "");
  const changedPaths = Array.isArray(verifierArgs.changed_paths)
    ? verifierArgs.changed_paths.map(String)
    : [];
  const allowedPaths = Array.isArray(verifierArgs.allowed_paths)
    ? verifierArgs.allowed_paths.map(String)
    : [];
  const actionAllowedPaths = Array.isArray(graph.normalizedArgs.allowed_paths)
    ? graph.normalizedArgs.allowed_paths.map(String)
    : [];
  const plannedPaths = jsonList(plan.proposed_files_to_change_json).map(String);
  if (
    verifierArgs.prepared_action_id !== graph.action.action_id
    || verifierArgs.execution_lease_id !== lease.lease_id
    || verifierArgs.agent_plan_id !== plan.plan_id
    || verifierArgs.head_unchanged !== true
    || verifierArgs.raw_diff_omitted !== true
    || verifierArgs.raw_content_omitted !== true
    || !SHA256_HEX.test(diffEvidenceHash)
    || !changedPaths.length
    || !allowedPaths.length
    || !exactStringList(allowedPaths, actionAllowedPaths)
    || !exactStringList(allowedPaths, plannedPaths)
    || allowedPaths.some((path) => !safeRelativePath(path))
    || changedPaths.some(
      (path) => !safeRelativePath(path) || !pathInScope(path, allowedPaths),
    )
  ) {
    throw new ControlPlaneHttpError(
      428,
      "verified_plan_evidence_manifest_required",
      "Workspace diff evidence is not bound to the approved action and path scope.",
    );
  }
  if (providerSideEffectId !== `codex-diff-${diffEvidenceHash.slice(0, 24)}`) {
    throw new ControlPlaneHttpError(
      409,
      "provider_side_effect_id_invalid",
      "provider_side_effect_id does not bind the verified workspace diff.",
    );
  }

  const evaluationBound = evaluationRows.rows.some((row) => {
    const rubric = parseJsonObject(row.rubric_json, "evaluation rubric");
    return rubric.prepared_action_id === graph.action.action_id
      && rubric.execution_lease_id === lease.lease_id
      && rubric.diff_evidence_hash === diffEvidenceHash
      && rubric.quality_gate_pass === true
      && rubric.raw_diff_omitted === true;
  });
  const artifactBound = artifactRows.rows.some(
    (row) =>
      row.artifact_type === "codex_workspace_diff_evidence"
      && row.content_hash === diffEvidenceHash,
  );
  const auditBound = auditRows.rows.some((row) => {
    const metadata = parseJsonObject(row.metadata_json, "audit metadata");
    const diffEvidence = metadata.diff_evidence
      && typeof metadata.diff_evidence === "object"
      && !Array.isArray(metadata.diff_evidence)
      ? metadata.diff_evidence as Record<string, unknown>
      : {};
    return row.actor_type === "agent"
      && row.actor_id === graph.action.requested_by_agent_id
      && row.action === "agent_worker.codex_workspace_write_completed"
      && row.entity_type === "runs"
      && row.entity_id === graph.run.run_id
      && metadata.prepared_action_id === graph.action.action_id
      && metadata.execution_lease_id === lease.lease_id
      && metadata.provider_side_effect_id === providerSideEffectId
      && diffEvidence.evidence_hash === diffEvidenceHash;
  });
  if (!evaluationBound || !artifactBound || !auditBound) {
    throw new ControlPlaneHttpError(
      428,
      "verified_plan_evidence_manifest_required",
      "Workspace diff evaluation, artifact, and audit evidence must agree.",
    );
  }

  const evidenceSetHash = stableHash({
    tool_call_ids: [...toolIds].sort(),
    evaluation_ids: [...evaluationIds].sort(),
    artifact_ids: [...artifactIds].sort(),
    audit_ids: auditRows.rows.map((row) => row.audit_id).sort(),
  });
  const terminalEvidenceHash = stableHash({
    contract: "prepared_action_workspace_write_terminal_evidence_v2",
    workspace_id: graph.action.workspace_id,
    action_id: graph.action.action_id,
    action_hash: graph.action.action_hash,
    lease_id: lease.lease_id,
    plan_evidence_manifest_id: manifest.manifest_id,
    plan_hash: manifest.plan_hash,
    verification_result_hash: manifest.verification_result_hash,
    provider_side_effect_id: providerSideEffectId,
    diff_evidence_hash: diffEvidenceHash,
    stored_execution_checkpoint_hash: stableHash(storedCheckpoint),
    evidence_set_hash: evidenceSetHash,
  });
  return {
    manifest,
    plan,
    diffEvidenceHash,
    evidenceSetHash,
    terminalEvidenceHash,
  };
}

function baseResponse(graph: BoundAction) {
  return {
    provider: "agentops-approval-wall",
    control_plane: "typescript_postgres",
    prepared_action: publicPreparedAction(
      graph.action,
      graph.normalizedArgs,
      graph.checkpoint,
    ),
    approval: publicApproval(graph.approval),
    hash_verification: hashVerification(graph),
    raw_provider_output_omitted: true,
    raw_prompt_omitted: true,
    raw_response_omitted: true,
    token_omitted: true,
  };
}

export async function preparePreparedAction(
  request: Request,
  body: Record<string, unknown>,
): Promise<PreparedActionResult> {
  assertAllowedFields(
    body,
    new Set([
      "workspace_id",
      "agent_id",
      "task_id",
      "run_id",
      "tool_call_id",
      "action_type",
      "normalized_args",
      "target_resource",
      "risk_level",
      "policy_version",
      "checkpoint",
      "idempotency_key",
      "expires_in_seconds",
      "reason",
    ]),
    "PreparedAction prepare",
  );
  return withPostgresTransaction(async (client) => {
    const identity = await authorize(client, request, "toolcalls:write", body);
    await assertPreparedActionSchemaReady(client);
    const taskId = identifier(body.task_id, "task_id");
    const runId = identifier(body.run_id, "run_id");
    const toolCallId = identifier(body.tool_call_id, "tool_call_id");
    const idempotencyKey = preparedActionIdempotencyKey(request, body);
    const actionType = identifier(body.action_type, "action_type");
    const policyVersion = identifier(body.policy_version, "policy_version");
    const riskLevel = identifier(body.risk_level, "risk_level");
    if (
      actionType !== ACTION_TYPE
      || policyVersion !== "approval-wall-codex-workspace-write-v2"
      || !["high", "critical"].includes(riskLevel)
    ) {
      throw new ControlPlaneHttpError(
        409,
        "prepared_action_authoring_contract_invalid",
        "Only the governed Codex workspace-write PreparedAction contract is supported.",
      );
    }
    const normalizedArgs = requestJsonObject(
      body.normalized_args,
      "normalized_args",
    );
    const checkpoint = requestJsonObject(body.checkpoint, "checkpoint");
    assertNoRawStoredPayload(normalizedArgs.value, checkpoint.value);
    const targetResource = String(body.target_resource || "").trim();
    if (
      !targetResource
      || targetResource.length > 512
      || /[\u0000-\u001f\u007f]/.test(targetResource)
    ) {
      throw new ControlPlaneHttpError(
        400,
        "target_resource_invalid",
        "PreparedAction target_resource is required and must be bounded.",
      );
    }
    const expiresInSeconds = boundedInteger(
      body.expires_in_seconds,
      7_200,
      60,
      172_800,
    );

    await client.query("SELECT pg_advisory_xact_lock(hashtext($1))", [
      `agentops-prepared-action:${identity.workspaceId}:${runId}:${idempotencyKey}`,
    ]);
    const task = (await client.query<TaskRow>(
      `SELECT task_id,workspace_id,owner_agent_id,status,updated_at
      FROM tasks WHERE task_id=$1 FOR UPDATE`,
      [taskId],
    )).rows[0];
    const run = (await client.query<RunRow>(
      `SELECT run_id,workspace_id,task_id,agent_id,runtime_type,status,
        started_at,ended_at,approval_required,agent_plan_id,plan_hash
      FROM runs WHERE run_id=$1 FOR UPDATE`,
      [runId],
    )).rows[0];
    const tool = (await client.query<PreparedActionAuthoringToolRow>(
      `SELECT tool_call_id,run_id,agent_id,tool_name,normalized_args_json,
        target_resource,risk_level,status,side_effect_id,ended_at
      FROM tool_calls WHERE tool_call_id=$1 FOR UPDATE`,
      [toolCallId],
    )).rows[0];
    const plan = run?.agent_plan_id
      ? (await client.query<AgentPlanRow>(
        `SELECT plan_id,workspace_id,task_id,run_id,agent_id,task_understanding,
          referenced_specs_json,referenced_memories_json,referenced_bases_json,
          proposed_files_to_change_json,risk_level,approval_required,
          execution_steps_json,verification_plan,rollback_plan,status,
          plan_version,plan_hash,verified_at,verification_result_hash,created_at
        FROM agent_plans WHERE plan_id=$1 FOR UPDATE`,
        [run.agent_plan_id],
      )).rows[0]
      : undefined;
    if (!task || !run || !tool || !plan) {
      throw new ControlPlaneHttpError(
        404,
        "prepared_action_parent_not_found",
        "PreparedAction task, run, tool call, or verified plan was not found.",
      );
    }
    const toolArgs = parseJsonObject(tool.normalized_args_json, "tool normalized args");
    const currentPlanHash = computeAgentPlanHash(plan);
    const planVerification = verifyAgentPlanRow(plan);
    const planVerifiedAt = Date.parse(String(plan.verified_at || ""));
    const planCreatedAt = Date.parse(plan.created_at);
    if (
      task.workspace_id !== identity.workspaceId
      || task.owner_agent_id !== identity.agentId
      || !["running", "waiting_approval"].includes(task.status)
      || run.workspace_id !== identity.workspaceId
      || run.task_id !== task.task_id
      || run.agent_id !== identity.agentId
      || run.runtime_type !== "codex"
      || !["running", "waiting_approval"].includes(run.status)
      || run.ended_at !== null
      || tool.run_id !== run.run_id
      || tool.agent_id !== identity.agentId
      || tool.tool_name !== ACTION_TYPE
      || !["running", "waiting_approval"].includes(tool.status)
      || tool.side_effect_id !== null
      || tool.ended_at !== null
      || tool.target_resource !== targetResource
      || tool.risk_level !== riskLevel
      || stableHash(toolArgs) !== stableHash(normalizedArgs.value)
      || plan.workspace_id !== identity.workspaceId
      || plan.task_id !== task.task_id
      || (plan.run_id !== null && plan.run_id !== run.run_id)
      || plan.agent_id !== identity.agentId
      || plan.status !== "approved"
      || !plan.verified_at
      || plan.plan_hash !== run.plan_hash
      || plan.plan_hash !== currentPlanHash
      || !planVerification.pass
      || plan.verification_result_hash
        !== agentPlanVerificationHash(plan.plan_id, planVerification)
      || !SHA256_HEX.test(String(plan.plan_hash || ""))
      || !SHA256_HEX.test(String(plan.verification_result_hash || ""))
      || !Number.isFinite(planVerifiedAt)
      || !Number.isFinite(planCreatedAt)
      || planVerifiedAt < planCreatedAt
      || planVerifiedAt > Date.now() + 60_000
      || normalizedArgs.value.task_id !== task.task_id
      || normalizedArgs.value.run_id !== run.run_id
      || normalizedArgs.value.agent_plan_id !== plan.plan_id
      || normalizedArgs.value.agent_plan_hash !== plan.plan_hash
      || normalizedArgs.value.agent_plan_verification_result_hash
        !== plan.verification_result_hash
    ) {
      throw new ControlPlaneHttpError(
        409,
        "prepared_action_current_assignment_invalid",
        "PreparedAction is not bound to the Agent's current task, run, tool call, and verified plan.",
      );
    }

    const existing = (await client.query<PreparedActionRow & {
      approval_decision: string;
    }>(
      `SELECT action.*,approval.decision AS approval_decision
      FROM prepared_actions action
      JOIN approvals approval ON approval.approval_id=action.approval_id
      WHERE action.workspace_id=$1 AND action.run_id=$2
        AND action.idempotency_key=$3
      FOR UPDATE OF action,approval`,
      [identity.workspaceId, runId, idempotencyKey],
    )).rows[0];
    if (existing) {
      const storedTtlSeconds = (
        Date.parse(existing.expires_at || "")
        - Date.parse(existing.created_at)
      ) / 1_000;
      if (
        existing.task_id !== taskId
        || existing.tool_call_id !== toolCallId
        || existing.requested_by_agent_id !== identity.agentId
        || existing.action_type !== actionType
        || existing.normalized_args_json !== normalizedArgs.json
        || existing.target_resource !== targetResource
        || existing.risk_level !== riskLevel
        || existing.policy_version !== policyVersion
        || existing.checkpoint_json !== checkpoint.json
        || storedTtlSeconds !== expiresInSeconds
      ) {
        throw new ControlPlaneHttpError(
          409,
          "prepared_action_idempotency_conflict",
          "PreparedAction idempotency key is already bound to another request.",
        );
      }
      return {
        status: 200,
        body: {
          ok: true,
          provider: "agentops-approval-wall",
          control_plane: "typescript_postgres",
          operation: "prepared_action_prepare",
          outcome: "unchanged",
          prepared_action: publicPreparedAction(
            existing,
            normalizedArgs.value,
            checkpoint.value,
          ),
          approval: {
            approval_id: existing.approval_id,
            decision: existing.approval_decision,
          },
          side_effect_performed: false,
          token_omitted: true,
        },
      };
    }

    const created = new Date();
    const createdAt = created.toISOString();
    const expiresAt = new Date(
      created.getTime() + expiresInSeconds * 1_000,
    ).toISOString();
    const actionId = `pa_${randomUUID().replaceAll("-", "").slice(0, 24)}`;
    const approvalId =
      `ap_prepared_action_${randomUUID().replaceAll("-", "").slice(0, 20)}`;
    const actionBase = {
      action_id: actionId,
      workspace_id: identity.workspaceId,
      task_id: task.task_id,
      run_id: run.run_id,
      tool_call_id: tool.tool_call_id,
      approval_id: approvalId,
      requested_by_agent_id: identity.agentId,
      action_type: actionType,
      normalized_args_json: normalizedArgs.json,
      target_resource: targetResource,
      risk_level: riskLevel,
      policy_version: policyVersion,
      checkpoint_json: checkpoint.json,
      idempotency_key: idempotencyKey,
      expires_at: expiresAt,
    };
    const actionHash = preparedActionHash(actionBase);
    const reason = safeSummary(
      body.reason,
      "Prepared action requires Human approval before exact execution.",
    );
    await client.query(
      `INSERT INTO approvals(
        approval_id,approval_kind,task_id,run_id,tool_call_id,
        requested_by_agent_id,approver_user_id,decision,reason,expires_at,
        created_at,decided_at
      ) VALUES($1,'prepared_action',$2,$3,$4,$5,NULL,'pending',$6,$7,$8,NULL)`,
      [
        approvalId,
        task.task_id,
        run.run_id,
        tool.tool_call_id,
        identity.agentId,
        reason,
        expiresAt,
        createdAt,
      ],
    );
    await client.query(
      `INSERT INTO prepared_actions(
        action_id,workspace_id,task_id,run_id,tool_call_id,approval_id,
        requested_by_agent_id,action_type,normalized_args_json,target_resource,
        risk_level,policy_version,checkpoint_json,action_hash,idempotency_key,
        status,provider_side_effect_id,result_summary,created_at,approved_at,
        consumed_at,expires_at
      ) VALUES(
        $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,
        'prepared',NULL,NULL,$16,NULL,NULL,$17
      )`,
      [
        actionId,
        identity.workspaceId,
        task.task_id,
        run.run_id,
        tool.tool_call_id,
        approvalId,
        identity.agentId,
        actionType,
        normalizedArgs.json,
        targetResource,
        riskLevel,
        policyVersion,
        checkpoint.json,
        actionHash,
        idempotencyKey,
        createdAt,
        expiresAt,
      ],
    );
    const runUpdate = await client.query(
      `UPDATE runs SET status='waiting_approval',approval_required=1
      WHERE run_id=$1 AND workspace_id=$2 AND agent_id=$3
        AND status IN ('running','waiting_approval') AND ended_at IS NULL`,
      [run.run_id, identity.workspaceId, identity.agentId],
    );
    const taskUpdate = await client.query(
      `UPDATE tasks SET status='waiting_approval',updated_at=$1
      WHERE task_id=$2 AND workspace_id=$3 AND owner_agent_id=$4
        AND status IN ('running','waiting_approval')`,
      [createdAt, task.task_id, identity.workspaceId, identity.agentId],
    );
    const toolUpdate = await client.query(
      `UPDATE tool_calls SET status='waiting_approval'
      WHERE tool_call_id=$1 AND run_id=$2 AND agent_id=$3
        AND status IN ('running','waiting_approval')
        AND side_effect_id IS NULL AND ended_at IS NULL`,
      [tool.tool_call_id, run.run_id, identity.agentId],
    );
    if (
      runUpdate.rowCount !== 1
      || taskUpdate.rowCount !== 1
      || toolUpdate.rowCount !== 1
    ) {
      throw new ControlPlaneHttpError(
        409,
        "prepared_action_current_assignment_invalid",
        "PreparedAction parent state changed before authoring completed.",
      );
    }
    await appendRuntimeEvent(client, {
      workspaceId: identity.workspaceId,
      eventType: "prepared_action.prepare",
      status: "waiting_approval",
      runId: run.run_id,
      taskId: task.task_id,
      agentId: identity.agentId,
      inputSummary: actionType,
      outputSummary: reason,
      rawPayloadHash: actionHash,
    });
    await appendAudit(client, {
      workspaceId: identity.workspaceId,
      actorType: "agent",
      actorId: identity.agentId,
      action: "approval_wall.prepared_action_created",
      entityType: "prepared_actions",
      entityId: actionId,
      after: {
        action_id: actionId,
        approval_id: approvalId,
        action_hash: actionHash,
        status: "prepared",
      },
      metadata: {
        task_id: task.task_id,
        run_id: run.run_id,
        tool_call_id: tool.tool_call_id,
        idempotency_key_hash: stableHash(idempotencyKey),
        normalized_args_hash: stableHash(normalizedArgs.value),
        checkpoint_hash: stableHash(checkpoint.value),
        raw_body_omitted: true,
        token_omitted: true,
      },
    });
    const graph = await loadBoundAction(client, identity, actionId);
    return {
      status: 201,
      body: {
        ok: true,
        ...baseResponse(graph),
        operation: "prepared_action_prepare",
        outcome: "created",
        status: "waiting_approval",
        resume_required_after_approval: true,
        side_effect_performed: false,
      },
    };
  });
}

export async function getPreparedAction(
  request: Request,
  actionIdInput: string,
): Promise<PreparedActionResult> {
  const actionId = identifier(actionIdInput, "action_id");
  return withPostgresTransaction(async (client) => {
    const identity = await authorize(client, request, "tasks:read");
    await assertPreparedActionSchemaReady(client);
    await lockPreparedActionMutation(
      client,
      identity.workspaceId,
      actionId,
    );
    let graph = await loadBoundAction(client, identity, actionId);
    let lease = await loadLease(client, actionId);
    let receipt = await loadReceipt(client, actionId);
    if (lease) assertLeaseBinding(graph, lease);
    if (lease && receipt) assertReceiptBinding(graph, lease, receipt);
    let reconciledExpiredLease = false;
    if (
      lease
      && lease.status === "executing"
      && !receipt
      && (parseExpiry(lease.expires_at) ?? 0) <= Date.now()
      && identity.scopes.includes("toolcalls:write")
    ) {
      await terminalizeExpiredLease(client, graph, lease);
      const currentAction = await readCurrentAction(client, actionId);
      if (currentAction) graph = { ...graph, action: currentAction };
      lease = await loadLease(client, actionId);
      receipt = await loadReceipt(client, actionId);
      if (lease && receipt) assertReceiptBinding(graph, lease, receipt);
      reconciledExpiredLease = true;
    }
    const verification = hashVerification(graph);
    const actionExpiry = parseExpiry(graph.action.expires_at);
    const approvalExpiry = parseExpiry(graph.approval.expires_at);
    const authorityReady = graph.action.status === "approved"
      && graph.approval.decision === "approved"
      && Boolean(graph.action.approved_at)
      && Boolean(graph.approval.decided_at)
      && Boolean(graph.approval.approver_user_id)
      && actionExpiry !== null
      && approvalExpiry !== null
      && actionExpiry > Date.now()
      && approvalExpiry > Date.now()
      && currentAssignmentAndPlanValid(graph);
    const leaseUnexpired = !lease
      || (
        lease.status === "executing"
        && (parseExpiry(lease.expires_at) ?? 0) > Date.now()
      );
    const claimReady = authorityReady
      && !lease
      && graph.run.status === "waiting_approval"
      && graph.run.ended_at === null
      && graph.task.status === "waiting_approval"
      && graph.tool.status === "waiting_approval"
      && graph.tool.side_effect_id === null
      && graph.tool.ended_at === null;
    const executionReady = authorityReady
      && lease?.status === "executing"
      && leaseUnexpired
      && graph.run.status === "running"
      && graph.run.ended_at === null
      && graph.task.status === "running"
      && graph.tool.status === "running"
      && graph.tool.side_effect_id === null
      && graph.tool.ended_at === null;
    let state = "blocked";
    if (
      verification.match
      && graph.action.status === "consumed"
      && lease?.status === "completed"
      && receipt?.outcome === "succeeded"
      && currentAssignmentAndPlanValid(graph)
      && graph.run.status === "completed"
      && graph.run.ended_at !== null
      && graph.task.status === "completed"
      && graph.tool.status === "completed"
      && graph.tool.side_effect_id === graph.action.provider_side_effect_id
      && graph.tool.ended_at !== null
    ) {
      state = "completed";
    } else if (
      verification.match
      && !receipt
      && (claimReady || executionReady)
    ) {
      state = executionReady ? "executing" : "ready";
    }
    return {
      status: 200,
      body: {
        ...baseResponse(graph),
        operation: "prepared_action_get",
        status: state,
        execution_lease: publicExecutionLease(lease),
        execution_receipt: publicExecutionReceipt(receipt),
        reconciled_expired_lease: reconciledExpiredLease,
        safety: {
          read_only: !reconciledExpiredLease,
          reconciliation_only: reconciledExpiredLease,
          domain_ledger_mutated: reconciledExpiredLease,
          raw_provider_output_omitted: true,
          raw_prompt_omitted: true,
          raw_response_omitted: true,
          token_omitted: true,
        },
      },
    };
  });
}

export async function claimPreparedActionExecution(
  request: Request,
  actionIdInput: string,
): Promise<PreparedActionResult> {
  const actionId = identifier(actionIdInput, "action_id");
  const body = await boundedJsonObject(request, {
    maxBytes: PREPARED_ACTION_MAX_BODY_BYTES,
    label: "PreparedAction execution claim",
  });
  assertAllowedFields(
    body,
    new Set(["workspace_id", "agent_id", "lease_ttl_seconds"]),
    "PreparedAction execution claim",
  );
  return withPostgresTransaction(async (client) => {
    const identity = await authorize(
      client,
      request,
      "toolcalls:write",
      body,
    );
    await assertPreparedActionSchemaReady(client);
    await lockPreparedActionMutation(
      client,
      identity.workspaceId,
      actionId,
    );
    const graph = await loadBoundAction(client, identity, actionId);
    const ttlSeconds = boundedInteger(
      body.lease_ttl_seconds,
      900,
      1,
      7200,
    );
    const claimRequestHash = stableHash({
      contract: "prepared_action_execution_claim_request_v1",
      workspace_id: identity.workspaceId,
      agent_id: identity.agentId,
      action_id: actionId,
      action_hash: graph.action.action_hash,
      lease_ttl_seconds: ttlSeconds,
    });
    const claimIdempotencyHash = stableHash({
      contract: "prepared_action_execution_claim_idempotency_v1",
      workspace_id: identity.workspaceId,
      agent_id: identity.agentId,
      action_id: actionId,
      action_hash: graph.action.action_hash,
    });
    const existing = await loadLease(client, actionId);
    if (existing) {
      assertLeaseBinding(graph, existing);
      if (
        existing.status === "executing"
        && (parseExpiry(existing.expires_at) ?? 0) <= Date.now()
      ) {
        return terminalizeExpiredLease(client, graph, existing);
      }
      const exactReplay = existing.claim_request_hash === claimRequestHash
        && existing.claim_idempotency_hash === claimIdempotencyHash;
      const existingReceipt = await loadReceipt(client, actionId);
      if (existingReceipt) {
        assertReceiptBinding(graph, existing, existingReceipt);
      } else if (existing.status === "executing") {
        assertExecutionState(graph, "executing");
      }
      return {
        status: 409,
        body: {
          ok: false,
          error: exactReplay
            ? "prepared_action_execution_already_claimed"
            : "prepared_action_execution_claim_conflict",
          message: exactReplay
            ? "The exact claim already owns the exclusive execution lease."
            : "PreparedAction already has a different immutable execution claim.",
          execution_lease: publicExecutionLease(existing),
          execution_receipt: publicExecutionReceipt(existingReceipt),
          exact_claim_replay: exactReplay,
          execute_once: true,
          automatic_retry_allowed: false,
          token_omitted: true,
        },
      };
    }
    assertExecutionState(graph, "claim");

    const startedAt = new Date();
    const actionExpiry = parseExpiry(graph.action.expires_at);
    const approvalExpiry = parseExpiry(graph.approval.expires_at);
    if (actionExpiry === null || approvalExpiry === null) {
      throw new ControlPlaneHttpError(
        409,
        "prepared_action_expiry_invalid",
        "PreparedAction or approval expiry is invalid.",
      );
    }
    const leaseExpiresAt = Math.min(
      startedAt.getTime() + ttlSeconds * 1000,
      actionExpiry,
      approvalExpiry,
    );
    if (leaseExpiresAt <= startedAt.getTime()) {
      throw new ControlPlaneHttpError(
        409,
        "prepared_action_expired",
        "PreparedAction authority expired before the lease could be created.",
      );
    }
    const expiresAt = new Date(leaseExpiresAt).toISOString();
    const leaseId = `pa_lease_${randomUUID().replaceAll("-", "")}`;
    const leaseResult = await client.query<ExecutionLeaseRow>(
      `INSERT INTO prepared_action_execution_leases(
        lease_id,action_id,workspace_id,requested_by_agent_id,action_hash,
        status,started_at,expires_at,completed_at,failure_reason,
        claim_request_hash,claim_idempotency_hash,claim_identity_source
      ) VALUES(
        $1,$2,$3,$4,$5,'executing',$6,$7,NULL,NULL,$8,$9,'request_hash_v1'
      )
      RETURNING lease_id,action_id,workspace_id,requested_by_agent_id,
        action_hash,status,started_at,expires_at,completed_at,failure_reason,
        claim_request_hash,claim_idempotency_hash,claim_identity_source`,
      [
        leaseId,
        actionId,
        identity.workspaceId,
        identity.agentId,
        graph.action.action_hash,
        startedAt.toISOString(),
        expiresAt,
        claimRequestHash,
        claimIdempotencyHash,
      ],
    );
    const lease = leaseResult.rows[0];
    if (!lease) {
      throw new ControlPlaneHttpError(
        409,
        "prepared_action_execution_claim_conflict",
        "PreparedAction execution lease was not created.",
      );
    }
    const runUpdate = await client.query(
      `UPDATE runs SET status='running',approval_required=0
      WHERE run_id=$1 AND workspace_id=$2 AND task_id=$3 AND agent_id=$4
        AND status='waiting_approval' AND ended_at IS NULL
        AND agent_plan_id=$5 AND plan_hash=$6`,
      [
        graph.run.run_id,
        graph.action.workspace_id,
        graph.task.task_id,
        graph.action.requested_by_agent_id,
        graph.plan.plan_id,
        graph.plan.plan_hash,
      ],
    );
    const taskUpdate = await client.query(
      `UPDATE tasks SET status='running',updated_at=$1
      WHERE task_id=$2 AND workspace_id=$3 AND owner_agent_id=$4
        AND status='waiting_approval'`,
      [
        startedAt.toISOString(),
        graph.task.task_id,
        graph.action.workspace_id,
        graph.action.requested_by_agent_id,
      ],
    );
    const toolUpdate = await client.query(
      `UPDATE tool_calls SET status='running'
      WHERE tool_call_id=$1 AND run_id=$2 AND agent_id=$3
        AND tool_name=$4 AND status='waiting_approval'
        AND side_effect_id IS NULL AND ended_at IS NULL`,
      [
        graph.tool.tool_call_id,
        graph.run.run_id,
        graph.action.requested_by_agent_id,
        ACTION_TYPE,
      ],
    );
    if (
      runUpdate.rowCount !== 1
      || taskUpdate.rowCount !== 1
      || toolUpdate.rowCount !== 1
    ) {
      throw new ControlPlaneHttpError(
        409,
        "prepared_action_execution_checkpoint_conflict",
        "PreparedAction claim could not establish the exact running checkpoint.",
      );
    }
    await appendRuntimeEvent(client, {
      eventType: "prepared_action.execution_claimed",
      status: "running",
      runId: graph.run.run_id,
      taskId: graph.task.task_id,
      agentId: identity.agentId,
      outputSummary:
        "Exclusive PreparedAction execution lease acquired before provider execution.",
      rawPayloadHash: graph.action.action_hash,
    });
    await appendAudit(client, {
      workspaceId: identity.workspaceId,
      actorType: "agent",
      actorId: identity.agentId,
      action: "approval_wall.prepared_action_execution_claimed",
      entityType: "prepared_actions",
      entityId: actionId,
      before: publicPreparedAction(
        graph.action,
        graph.normalizedArgs,
        graph.checkpoint,
      ),
      after: {
        action_id: actionId,
        action_hash: graph.action.action_hash,
        execution_lease: publicExecutionLease(lease),
      },
      metadata: {
        lease_id: lease.lease_id,
        claim_request_hash: claimRequestHash,
        claim_idempotency_hash: claimIdempotencyHash,
        execution_checkpoint: "prepared_action_execution_v1",
        execute_once: true,
        raw_provider_output_omitted: true,
        raw_prompt_omitted: true,
        raw_response_omitted: true,
        token_omitted: true,
      },
    });
    return {
      status: 201,
      body: {
        ...baseResponse(graph),
        ok: true,
        operation: "prepared_action_execution_claim",
        outcome: "created",
        status: "executing",
        execution_lease: publicExecutionLease(lease),
        execute_once: true,
      },
    };
  });
}

export async function failPreparedActionExecution(
  request: Request,
  actionIdInput: string,
): Promise<PreparedActionResult> {
  const actionId = identifier(actionIdInput, "action_id");
  const body = await boundedJsonObject(request, {
    maxBytes: PREPARED_ACTION_MAX_BODY_BYTES,
    label: "PreparedAction execution failure",
  });
  assertAllowedFields(
    body,
    new Set([
      "workspace_id",
      "agent_id",
      "lease_id",
      "failure_reason",
      "rollback_performed",
    ]),
    "PreparedAction execution failure",
  );
  return withPostgresTransaction(async (client) => {
    const identity = await authorize(
      client,
      request,
      "toolcalls:write",
      body,
    );
    await assertPreparedActionSchemaReady(client);
    await lockPreparedActionMutation(
      client,
      identity.workspaceId,
      actionId,
    );
    const graph = await loadBoundAction(client, identity, actionId);
    const leaseId = identifier(body.lease_id, "lease_id");
    const lease = await loadLease(client, actionId);
    if (!lease || lease.lease_id !== leaseId) {
      throw new ControlPlaneHttpError(
        409,
        "prepared_action_execution_lease_mismatch",
        "PreparedAction execution lease does not match.",
      );
    }
    assertLeaseBinding(graph, lease);
    const reason = safeSummary(
      body.failure_reason,
      "Codex workspace-write failed closed.",
      240,
    );
    if (
      body.rollback_performed !== undefined
      && typeof body.rollback_performed !== "boolean"
    ) {
      throw new ControlPlaneHttpError(
        400,
        "rollback_performed_invalid",
        "rollback_performed must be a boolean.",
      );
    }
    const failureDetailHash = stableHash({
      failure_summary: reason,
      raw_provider_output_omitted: true,
    });
    const persistedReason =
      "Codex workspace-write failed closed; worker failure detail omitted.";
    const rollbackPerformed = body.rollback_performed === true;
    const receiptRequestHash = stableHash({
      contract: "prepared_action_execution_failure_request_v1",
      workspace_id: identity.workspaceId,
      agent_id: identity.agentId,
      action_id: actionId,
      action_hash: graph.action.action_hash,
      lease_id: leaseId,
      failure_detail_hash: failureDetailHash,
      rollback_performed: rollbackPerformed,
    });
    const existingReceipt = await loadReceipt(client, actionId);
    if (existingReceipt) {
      assertReceiptBinding(graph, lease, existingReceipt);
      const exactReplay = existingReceipt.lease_id === leaseId
        && existingReceipt.outcome === "failed"
        && existingReceipt.receipt_request_hash === receiptRequestHash;
      if (!exactReplay) {
        throw new ControlPlaneHttpError(
          409,
          "prepared_action_terminal_receipt_conflict",
          "PreparedAction already has a different immutable terminal receipt.",
        );
      }
      return {
        status: 200,
        body: {
          ok: true,
          provider: "agentops-approval-wall",
          control_plane: "typescript_postgres",
          operation: "prepared_action_execution_fail",
          outcome: "unchanged",
          status: "blocked",
          execution_lease: publicExecutionLease(lease),
          execution_receipt: publicExecutionReceipt(existingReceipt),
          automatic_retry_allowed: false,
          retry_requires_new_action: true,
          raw_provider_output_omitted: true,
          raw_prompt_omitted: true,
          raw_response_omitted: true,
          token_omitted: true,
        },
      };
    }
    if (lease.status !== "executing") {
      throw new ControlPlaneHttpError(
        409,
        "prepared_action_execution_lease_terminal",
        "PreparedAction execution lease is already terminal.",
      );
    }
    if ((parseExpiry(lease.expires_at) ?? 0) <= Date.now()) {
      return terminalizeExpiredLease(client, graph, lease);
    }
    assertExecutionState(graph, "executing");
    const terminalAt = new Date().toISOString();
    const terminalEvidenceHash = stableHash({
      contract: "prepared_action_execution_failure_evidence_v1",
      action_id: actionId,
      action_hash: graph.action.action_hash,
      lease_id: leaseId,
      failure_detail_hash: failureDetailHash,
      rollback_performed: rollbackPerformed,
    });
    const terminalCost = await settleExistingTerminalRunCost(client, {
      workspaceId: graph.action.workspace_id,
      runId: graph.run.run_id,
      terminalStatus: "blocked",
    });
    const actionUpdate = await client.query(
      `UPDATE prepared_actions SET status='expired',result_summary=$1
      WHERE action_id=$2 AND status='approved'`,
      [persistedReason, actionId],
    );
    const leaseUpdate = await client.query(
      `UPDATE prepared_action_execution_leases
      SET status='failed',completed_at=$1,failure_reason=$2
      WHERE lease_id=$3 AND status='executing'`,
      [terminalAt, persistedReason, leaseId],
    );
    if (actionUpdate.rowCount !== 1 || leaseUpdate.rowCount !== 1) {
      throw new ControlPlaneHttpError(
        409,
        "prepared_action_execution_failure_conflict",
        "PreparedAction failure could not be closed atomically.",
      );
    }
    const runUpdate = await client.query(
      `UPDATE runs SET status='blocked',approval_required=0,
        error_type='CodexWorkspaceWriteFailed',error_message=$1,ended_at=$2
      WHERE run_id=$3 AND workspace_id=$4 AND task_id=$5 AND agent_id=$6
        AND status='running' AND ended_at IS NULL
        AND agent_plan_id=$7 AND plan_hash=$8`,
      [
        persistedReason,
        terminalAt,
        graph.run.run_id,
        graph.action.workspace_id,
        graph.task.task_id,
        graph.action.requested_by_agent_id,
        graph.plan.plan_id,
        graph.plan.plan_hash,
      ],
    );
    const taskUpdate = await client.query(
      `UPDATE tasks SET status='blocked',updated_at=$1
      WHERE task_id=$2 AND workspace_id=$3 AND owner_agent_id=$4
        AND status='running'`,
      [
        terminalAt,
        graph.task.task_id,
        graph.action.workspace_id,
        graph.action.requested_by_agent_id,
      ],
    );
    const toolUpdate = await client.query(
      `UPDATE tool_calls SET status='blocked',result_summary=$1,ended_at=$2
      WHERE tool_call_id=$3 AND run_id=$4 AND agent_id=$5
        AND status='running' AND side_effect_id IS NULL AND ended_at IS NULL`,
      [
        persistedReason,
        terminalAt,
        graph.tool.tool_call_id,
        graph.run.run_id,
        graph.action.requested_by_agent_id,
      ],
    );
    if (
      runUpdate.rowCount !== 1
      || taskUpdate.rowCount !== 1
      || toolUpdate.rowCount !== 1
    ) {
      throw new ControlPlaneHttpError(
        409,
        "prepared_action_execution_failure_conflict",
        "PreparedAction failure parent state changed before closure.",
      );
    }
    await client.query(
      "UPDATE agents SET status='idle',updated_at=$1 WHERE agent_id=$2",
      [terminalAt, identity.agentId],
    );
    const terminalLease = (await readCurrentLease(client, leaseId)) || lease;
    const receipt = await insertReceipt(client, {
      graph,
      lease: terminalLease,
      receiptRequestHash,
      outcome: "failed",
      providerCallPerformed: true,
      providerCallMayHaveCompleted: false,
      terminalEvidenceHash,
      terminalEvidenceSource: "control_plane_failure_v1",
      terminalEvidenceVerified: true,
      terminalAt,
    });
    await appendRuntimeEvent(client, {
      eventType: "prepared_action.execution_failed",
      status: "blocked",
      runId: graph.run.run_id,
      taskId: graph.task.task_id,
      agentId: identity.agentId,
      outputSummary: persistedReason,
      rawPayloadHash: graph.action.action_hash,
    });
    await appendAudit(client, {
      workspaceId: identity.workspaceId,
      actorType: "agent",
      actorId: identity.agentId,
      action: "approval_wall.prepared_action_execution_failed",
      entityType: "prepared_actions",
      entityId: actionId,
      before: publicPreparedAction(
        graph.action,
        graph.normalizedArgs,
        graph.checkpoint,
      ),
      after: {
        action_id: actionId,
        status: "expired",
        result_summary: persistedReason,
      },
      metadata: {
        lease_id: leaseId,
        receipt_id: receipt.receipt_id,
        terminal_evidence_hash: terminalEvidenceHash,
        run_cost_closure: terminalCost.mode,
        run_cost_reservation_state: terminalCost.reservation?.state || null,
        failure_detail_hash: failureDetailHash,
        rollback_performed: rollbackPerformed,
        automatic_retry_allowed: false,
        retry_requires_new_action: true,
        raw_provider_output_omitted: true,
        raw_prompt_omitted: true,
        raw_response_omitted: true,
        token_omitted: true,
      },
    });
    return {
      status: 200,
      body: {
        ok: true,
        provider: "agentops-approval-wall",
        control_plane: "typescript_postgres",
        operation: "prepared_action_execution_fail",
        outcome: "created",
        status: "blocked",
        execution_lease: publicExecutionLease(terminalLease),
        execution_receipt: publicExecutionReceipt(receipt),
        automatic_retry_allowed: false,
        retry_requires_new_action: true,
        raw_provider_output_omitted: true,
        raw_prompt_omitted: true,
        raw_response_omitted: true,
        token_omitted: true,
      },
    };
  });
}

export async function resumePreparedActionExecution(
  request: Request,
  actionIdInput: string,
): Promise<PreparedActionResult> {
  const actionId = identifier(actionIdInput, "action_id");
  const body = await boundedJsonObject(request, {
    maxBytes: PREPARED_ACTION_MAX_BODY_BYTES,
    label: "PreparedAction execution resume",
  });
  assertAllowedFields(
    body,
    new Set([
      "workspace_id",
      "agent_id",
      "lease_id",
      "plan_evidence_manifest_id",
      "provider_side_effect_id",
      "output_summary",
      "duration_ms",
      "output_tokens",
      "result_summary",
    ]),
    "PreparedAction execution resume",
  );
  return withPostgresTransaction(async (client) => {
    const identity = await authorize(
      client,
      request,
      "toolcalls:write",
      body,
    );
    await assertPreparedActionSchemaReady(client);
    await lockPreparedActionMutation(
      client,
      identity.workspaceId,
      actionId,
    );
    const graph = await loadBoundAction(client, identity, actionId);
    const leaseId = identifier(body.lease_id, "lease_id");
    const manifestId = identifier(
      body.plan_evidence_manifest_id,
      "plan_evidence_manifest_id",
    );
    const providerSideEffectId = identifier(
      body.provider_side_effect_id,
      "provider_side_effect_id",
    );
    const lease = await loadLease(client, actionId);
    if (!lease || lease.lease_id !== leaseId) {
      throw new ControlPlaneHttpError(
        409,
        "prepared_action_execution_lease_mismatch",
        "PreparedAction execution lease does not match.",
      );
    }
    assertLeaseBinding(graph, lease);
    const existingReceipt = await loadReceipt(client, actionId);
    if (existingReceipt) {
      assertReceiptBinding(graph, lease, existingReceipt);
      if (existingReceipt.outcome !== "succeeded") {
        throw new ControlPlaneHttpError(
          409,
          "prepared_action_terminal_receipt_conflict",
          "PreparedAction already has a non-success terminal receipt.",
        );
      }
      assertTerminalExecutionState(graph, providerSideEffectId);
      const replayEvidence = await verifyWorkspacePlanEvidence(
        client,
        graph,
        lease,
        manifestId,
        providerSideEffectId,
      );
      const auditResult = await client.query<EvidenceAuditRow>(
        `SELECT audit_id,workspace_id,actor_type,actor_id,action,entity_type,
          entity_id,metadata_json,tamper_chain_hash
        FROM audit_logs
        WHERE workspace_id=$1
          AND action='approval_wall.prepared_action_resumed'
          AND entity_type='prepared_actions'
          AND entity_id=$2
        ORDER BY created_at,audit_id
        FOR SHARE`,
        [identity.workspaceId, actionId],
      );
      if (auditResult.rows.length !== 1) {
        throw new ControlPlaneHttpError(
          409,
          "prepared_action_terminal_audit_invalid",
          "PreparedAction success receipt does not have one terminal audit.",
        );
      }
      const terminalAudit = auditResult.rows[0];
      const terminalMetadata = parseJsonObject(
        terminalAudit.metadata_json,
        "PreparedAction terminal audit metadata",
      );
      const terminalEvidenceHash = replayEvidence.terminalEvidenceHash;
      const receiptRequestHash = stableHash({
        contract: "prepared_action_execution_resume_request_v1",
        workspace_id: identity.workspaceId,
        agent_id: identity.agentId,
        action_id: actionId,
        action_hash: graph.action.action_hash,
        lease_id: leaseId,
        plan_evidence_manifest_id: manifestId,
        provider_side_effect_id: providerSideEffectId,
        terminal_evidence_hash: terminalEvidenceHash,
      });
      const exactReplay = terminalAudit.actor_type === "agent"
        && terminalAudit.actor_id === identity.agentId
        && SHA256_HEX.test(String(terminalAudit.tamper_chain_hash || ""))
        && terminalMetadata.lease_id === leaseId
        && terminalMetadata.approval_id === graph.action.approval_id
        && terminalMetadata.action_hash === graph.action.action_hash
        && terminalMetadata.plan_evidence_manifest_id === manifestId
        && terminalMetadata.provider_side_effect_id === providerSideEffectId
        && terminalMetadata.evidence_set_hash
          === replayEvidence.evidenceSetHash
        && terminalMetadata.terminal_evidence_hash === terminalEvidenceHash
        && terminalEvidenceHash === existingReceipt.terminal_evidence_hash
        && receiptRequestHash === existingReceipt.receipt_request_hash
        && graph.action.status === "consumed"
        && graph.action.provider_side_effect_id === providerSideEffectId
        && lease.status === "completed"
        && lease.completed_at !== null;
      if (!exactReplay) {
        throw new ControlPlaneHttpError(
          409,
          "prepared_action_terminal_receipt_conflict",
          "PreparedAction resume does not match the immutable terminal evidence.",
        );
      }
      return {
        status: 200,
        body: {
          ...baseResponse(graph),
          ok: true,
          operation: "prepared_action_resume",
          outcome: "unchanged",
          status: "completed",
          provider_side_effect_id: providerSideEffectId,
          execution_lease: publicExecutionLease(lease),
          execution_receipt: publicExecutionReceipt(existingReceipt),
          plan_evidence_manifest_id: manifestId,
          plan_evidence_pass: true,
          run_completed_in_resume: true,
          execute_once: true,
        },
      };
    }
    if (
      !existingReceipt
      && lease.status === "executing"
      && (parseExpiry(lease.expires_at) ?? 0) <= Date.now()
    ) {
      return terminalizeExpiredLease(client, graph, lease);
    }
    if (
      !existingReceipt
      && (lease.status !== "executing" || graph.action.status !== "approved")
    ) {
      throw new ControlPlaneHttpError(
        409,
        "prepared_action_execution_lease_terminal",
        "PreparedAction execution state is terminal or inconsistent.",
      );
    }
    if (!existingReceipt) assertExecutionState(graph, "executing");

    const evidence = await verifyWorkspacePlanEvidence(
      client,
      graph,
      lease,
      manifestId,
      providerSideEffectId,
    );
    const receiptRequestHash = stableHash({
      contract: "prepared_action_execution_resume_request_v1",
      workspace_id: identity.workspaceId,
      agent_id: identity.agentId,
      action_id: actionId,
      action_hash: graph.action.action_hash,
      lease_id: leaseId,
      plan_evidence_manifest_id: manifestId,
      provider_side_effect_id: providerSideEffectId,
      terminal_evidence_hash: evidence.terminalEvidenceHash,
    });
    const terminalAt = new Date().toISOString();
    if ((parseExpiry(lease.expires_at) ?? 0) <= Date.parse(terminalAt)) {
      return terminalizeExpiredLease(client, graph, lease);
    }
    const terminalCost = await settleExistingTerminalRunCost(client, {
      workspaceId: graph.action.workspace_id,
      runId: graph.run.run_id,
      terminalStatus: "completed",
    });
    const resultSummary =
      `Codex workspace-write completed with verified bounded diff evidence `
      + `${evidence.diffEvidenceHash.slice(0, 16)}; raw provider output omitted.`;
    const outputSummary = resultSummary;
    const durationMs = boundedInteger(body.duration_ms, 0, 0, 86_400_000);
    const outputTokens = boundedInteger(body.output_tokens, 0, 0, 10_000_000);
    const actionUpdate = await client.query(
      `UPDATE prepared_actions
      SET status='consumed',consumed_at=$1,provider_side_effect_id=$2,
        result_summary=$3
      WHERE action_id=$4 AND status='approved' AND consumed_at IS NULL`,
      [terminalAt, providerSideEffectId, resultSummary, actionId],
    );
    const leaseUpdate = await client.query(
      `UPDATE prepared_action_execution_leases
      SET status='completed',completed_at=$1
      WHERE lease_id=$2 AND status='executing'`,
      [terminalAt, leaseId],
    );
    if (actionUpdate.rowCount !== 1 || leaseUpdate.rowCount !== 1) {
      throw new ControlPlaneHttpError(
        409,
        "prepared_action_execution_resume_conflict",
        "PreparedAction completion could not be committed atomically.",
      );
    }
    const toolUpdate = await client.query(
      `UPDATE tool_calls SET status='completed',side_effect_id=$1,
        result_summary=$2,ended_at=$3
      WHERE tool_call_id=$4 AND run_id=$5 AND agent_id=$6
        AND status='running' AND side_effect_id IS NULL AND ended_at IS NULL`,
      [
        providerSideEffectId,
        resultSummary,
        terminalAt,
        graph.tool.tool_call_id,
        graph.run.run_id,
        graph.action.requested_by_agent_id,
      ],
    );
    const runUpdate = await client.query(
      `UPDATE runs SET approval_required=0,status='completed',ended_at=$1,
        duration_ms=$2,output_summary=$3,output_tokens=$4,
        error_type=NULL,error_message=NULL
      WHERE run_id=$5 AND workspace_id=$6 AND task_id=$7 AND agent_id=$8
        AND status='running' AND ended_at IS NULL
        AND agent_plan_id=$9 AND plan_hash=$10`,
      [
        terminalAt,
        durationMs,
        outputSummary,
        outputTokens,
        graph.run.run_id,
        graph.action.workspace_id,
        graph.task.task_id,
        graph.action.requested_by_agent_id,
        graph.plan.plan_id,
        graph.plan.plan_hash,
      ],
    );
    const taskUpdate = await client.query(
      `UPDATE tasks SET status='completed',updated_at=$1
      WHERE task_id=$2 AND workspace_id=$3 AND owner_agent_id=$4
        AND status='running'`,
      [
        terminalAt,
        graph.task.task_id,
        graph.action.workspace_id,
        graph.action.requested_by_agent_id,
      ],
    );
    if (
      toolUpdate.rowCount !== 1
      || runUpdate.rowCount !== 1
      || taskUpdate.rowCount !== 1
    ) {
      throw new ControlPlaneHttpError(
        409,
        "prepared_action_execution_resume_conflict",
        "PreparedAction parent state changed before completion.",
      );
    }
    await client.query(
      "UPDATE agents SET status='idle',updated_at=$1 WHERE agent_id=$2",
      [terminalAt, identity.agentId],
    );
    const terminalLease = (await readCurrentLease(client, leaseId)) || lease;
    const receipt = await insertReceipt(client, {
      graph,
      lease: terminalLease,
      receiptRequestHash,
      outcome: "succeeded",
      providerCallPerformed: true,
      providerCallMayHaveCompleted: false,
      terminalEvidenceHash: evidence.terminalEvidenceHash,
      terminalEvidenceSource: "worker_verified_v1",
      terminalEvidenceVerified: true,
      terminalAt,
    });
    await appendRuntimeEvent(client, {
      eventType: "prepared_action.resume",
      status: "completed",
      runId: graph.run.run_id,
      taskId: graph.task.task_id,
      agentId: identity.agentId,
      outputSummary: resultSummary,
      rawPayloadHash: graph.action.action_hash,
    });
    await appendAudit(client, {
      workspaceId: identity.workspaceId,
      actorType: "agent",
      actorId: identity.agentId,
      action: "approval_wall.prepared_action_resumed",
      entityType: "prepared_actions",
      entityId: actionId,
      before: publicPreparedAction(
        graph.action,
        graph.normalizedArgs,
        graph.checkpoint,
      ),
      after: {
        action_id: actionId,
        status: "consumed",
        provider_side_effect_id: providerSideEffectId,
        result_summary: resultSummary,
      },
      metadata: {
        approval_id: graph.action.approval_id,
        lease_id: leaseId,
        receipt_id: receipt.receipt_id,
        action_hash: graph.action.action_hash,
        plan_evidence_manifest_id: manifestId,
        plan_hash: evidence.manifest.plan_hash,
        verification_result_hash:
          evidence.manifest.verification_result_hash,
        diff_evidence_hash: evidence.diffEvidenceHash,
        evidence_set_hash: evidence.evidenceSetHash,
        terminal_evidence_hash: evidence.terminalEvidenceHash,
        run_cost_closure: terminalCost.mode,
        run_cost_reservation_state: terminalCost.reservation?.state || null,
        provider_side_effect_id: providerSideEffectId,
        run_completed_in_resume: true,
        execution_checkpoint: "terminal_run_v1",
        execute_once: true,
        raw_provider_output_omitted: true,
        raw_prompt_omitted: true,
        raw_response_omitted: true,
        token_omitted: true,
      },
    });
    const completedAction = (await readCurrentAction(client, actionId))
      || graph.action;
    const completedGraph = { ...graph, action: completedAction };
    return {
      status: 200,
      body: {
        ...baseResponse(completedGraph),
        ok: true,
        operation: "prepared_action_resume",
        outcome: "created",
        status: "completed",
        provider_side_effect_id: providerSideEffectId,
        execution_lease: publicExecutionLease(terminalLease),
        execution_receipt: publicExecutionReceipt(receipt),
        plan_evidence_manifest_id: manifestId,
        plan_evidence_pass: true,
        run_completed_in_resume: true,
        execute_once: true,
      },
    };
  });
}
