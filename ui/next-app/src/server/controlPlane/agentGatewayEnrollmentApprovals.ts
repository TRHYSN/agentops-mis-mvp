import type { PoolClient } from "pg";

import { boundedJsonObject } from "./boundedJson";
import {
  authenticateAgentGateway,
  enforceWorkspaceBinding,
  type AgentGatewayIdentity,
} from "./auth";
import { withPostgresTransaction } from "./db";
import {
  approvedGatewayEnrollmentIdempotencyKeyHash,
  gatewayOpaqueReference,
  issueApprovedGatewayEnrollmentToken,
  parseGatewayEnrollmentIssueInput,
  parseGatewayTokenScopes,
  requireGatewayAdministrator,
  type GatewayEnrollmentIssueInput,
} from "./gatewayAdministration";
import {
  authenticateHumanReviewer,
  type HumanSessionIdentity,
} from "./humanSession";
import { ControlPlaneHttpError } from "./http";
import { appendAudit, appendRuntimeEvent, stableHash } from "./ledger";

export const ENROLLMENT_APPROVAL_MAX_BODY_BYTES = 16 * 1024;

const REQUEST_FIELDS = new Set([
  "agent_id",
  "heartbeat_timeout_sec",
  "label",
  "name",
  "reason",
  "role",
  "runtime_type",
  "scopes",
  "ttl_days",
  "workspace_id",
]);
const ISSUE_FIELDS = new Set([
  "approval_id",
  "request_id",
  "workspace_id",
]);
const CONFIG_FIELDS = new Set([
  "contract",
  "heartbeat_timeout_sec",
  "label",
  "scopes",
  "ttl_days",
]);
const REQUEST_CONFIG_CONTRACT = "agent_gateway_enrollment_request_v1";
const APPROVAL_TTL_MS = 48 * 60 * 60 * 1000;

type OwnerResult = {
  status: number;
  body: Record<string, unknown>;
};

type EnrollmentConfig = {
  contract: typeof REQUEST_CONFIG_CONTRACT;
  scopes: string[];
  ttl_days: number;
  heartbeat_timeout_sec: number;
  label: string;
};

type EnrollmentGraphRow = {
  request_id: string;
  approval_id: string;
  task_id: string;
  run_id: string;
  workspace_id: string;
  agent_id: string;
  name: string;
  role: string | null;
  enrollment_runtime_type: string;
  scopes_json: string;
  reason: string | null;
  enrollment_status: string;
  token_id: string | null;
  request_created_at: string;
  request_updated_at: string;
  request_decided_at: string | null;
  approval_kind: string;
  requested_by_agent_id: string | null;
  approver_user_id: string | null;
  approval_decision: string;
  approval_reason: string | null;
  approval_expires_at: string | null;
  approval_created_at: string;
  approval_decided_at: string | null;
  requester_user_id: string | null;
  task_owner_agent_id: string | null;
  task_workspace_id: string;
  task_status: string;
  task_updated_at: string;
  run_agent_id: string;
  run_workspace_id: string;
  run_task_id: string;
  run_runtime_type: string;
  run_status: string;
  approval_required: number;
  run_ended_at: string | null;
  agent_name: string;
  agent_role: string;
  agent_runtime_type: string;
  agent_status: string;
};

type LockedEnrollmentGraph = {
  row: EnrollmentGraphRow;
  config: EnrollmentConfig;
  requestBindingHash: string;
  requestIdempotencyKeyHash: string;
};

type DecisionRequestRow = {
  request_hash: string;
  approval_id: string;
  decision: string;
  status: string;
};

type StoredTokenRow = {
  token_id: string;
  token_hash: string;
  workspace_id: string;
  agent_id: string;
  scopes_json: string;
  status: string;
  label: string | null;
  heartbeat_timeout_sec: number;
  created_at: string;
  expires_at: string | null;
  revoked_at: string | null;
};

function rejectUnknownFields(
  body: Record<string, unknown>,
  allowed: ReadonlySet<string>,
  operation: string,
) {
  const unknown = Object.keys(body).find((field) => !allowed.has(field));
  if (unknown) {
    throw new ControlPlaneHttpError(
      400,
      `${operation}_field_unsupported`,
      `${operation} received an unsupported request field.`,
    );
  }
}

function identifier(value: unknown, field: string, optional = false) {
  const normalized = String(value ?? "").trim();
  if (optional && !normalized) return null;
  if (!/^[A-Za-z0-9._:-]{1,128}$/.test(normalized)) {
    throw new ControlPlaneHttpError(
      400,
      `${field}_invalid`,
      `${field} must use 1-128 safe identifier characters.`,
    );
  }
  return normalized;
}

function idempotencyKey(headers: Headers) {
  const value = String(headers.get("idempotency-key") || "").trim();
  if (!/^[A-Za-z0-9._:-]{16,128}$/.test(value)) {
    throw new ControlPlaneHttpError(
      400,
      "idempotency_key_required",
      "Idempotency-Key must use 16-128 safe identifier characters.",
    );
  }
  return value;
}

function safeReason(value: unknown) {
  const clean = String(value ?? "")
    .replace(
      /-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----[\s\S]*?-----END (?:[A-Z0-9 ]+ )?PRIVATE KEY-----/g,
      "[PRIVATE_KEY_REDACTED]",
    )
    .replace(/\b(?:agtok|agtsess)_[A-Za-z0-9_-]+\b/g, "[CREDENTIAL_REDACTED]")
    .replace(/\bBearer\s+\S+/gi, "Bearer [REDACTED]")
    .replace(
      /(token|secret|password|api[_-]?key|credential|dsn)\s*[:=]\s*['"]?[^'"\s,;]+/gi,
      "$1=[REDACTED]",
    )
    .replace(
      /raw[_-]?(?:prompt|response|transcript|content)\s*[:=]\s*['"]?[^'"\s,;]+/gi,
      "[RAW_FIELD_REDACTED]",
    )
    .replace(/[\u0000-\u001f\u007f]/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 360);
  return clean || "Remote Agent Gateway enrollment requires Human approval.";
}

function enforceRequestingAgent(
  identity: AgentGatewayIdentity,
  request: Request,
  body: Record<string, unknown>,
) {
  enforceWorkspaceBinding(identity, {
    header: request.headers.get("x-agentops-workspace-id"),
    body: body.workspace_id,
  });
  for (const value of [
    request.headers.get("x-agentops-agent-id"),
    body.agent_id,
  ]) {
    if (
      value !== undefined
      && value !== null
      && value !== ""
      && identifier(value, "agent_id") !== identity.agentId
    ) {
      throw new ControlPlaneHttpError(
        403,
        "forbidden",
        "Agent credential cannot request enrollment for another Agent.",
      );
    }
  }
}

async function assertRequestingAgentBinding(
  client: PoolClient,
  identity: AgentGatewayIdentity,
  input: GatewayEnrollmentIssueInput,
) {
  const row = (await client.query<{
    agent_id: string;
    name: string;
    role: string;
    runtime_type: string;
    status: string;
  }>(
    `SELECT agent_id,name,role,runtime_type,status
    FROM agents WHERE agent_id=$1 FOR UPDATE`,
    [identity.agentId],
  )).rows[0];
  if (
    !row
    || row.status === "disabled"
    || row.agent_id !== input.agentId
    || row.name !== input.name
    || row.role !== input.role
    || row.runtime_type !== input.runtimeType
  ) {
    throw new ControlPlaneHttpError(
      409,
      "enrollment_request_agent_binding_invalid",
      "Enrollment request must exactly match the authenticated active Agent.",
    );
  }
  const foreign = await client.query<{ workspace_id: string }>(
    `SELECT workspace_id
    FROM (
      SELECT workspace_id,created_at
      FROM agent_gateway_tokens
      WHERE agent_id=$1 AND workspace_id<>$2
      UNION ALL
      SELECT workspace_id,created_at
      FROM agent_gateway_enrollment_requests
      WHERE agent_id=$1 AND workspace_id<>$2
    ) binding
    ORDER BY created_at DESC,workspace_id
    LIMIT 1`,
    [identity.agentId, identity.workspaceId],
  );
  if (foreign.rows[0]) {
    throw new ControlPlaneHttpError(
      409,
      "agent_workspace_binding_conflict",
      "The Agent has enrollment history in another workspace.",
    );
  }
}

function parseMetadata(value: string) {
  try {
    const parsed: unknown = JSON.parse(value);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? parsed as Record<string, unknown>
      : null;
  } catch {
    return null;
  }
}

function enrollmentConfig(input: GatewayEnrollmentIssueInput): EnrollmentConfig {
  return {
    contract: REQUEST_CONFIG_CONTRACT,
    scopes: [...input.scopes],
    ttl_days: input.ttlDays,
    heartbeat_timeout_sec: input.heartbeatTimeoutSec,
    label: input.label,
  };
}

function parseEnrollmentConfig(value: string): EnrollmentConfig {
  let parsed: unknown;
  try {
    parsed = JSON.parse(value);
  } catch {
    parsed = null;
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new ControlPlaneHttpError(
      409,
      "enrollment_request_config_invalid",
      "Enrollment request policy binding is invalid.",
    );
  }
  const object = parsed as Record<string, unknown>;
  if (
    Object.keys(object).some((field) => !CONFIG_FIELDS.has(field))
    || Object.keys(object).length !== CONFIG_FIELDS.size
    || object.contract !== REQUEST_CONFIG_CONTRACT
  ) {
    throw new ControlPlaneHttpError(
      409,
      "enrollment_request_config_invalid",
      "Enrollment request policy binding is invalid.",
    );
  }
  const normalized = parseGatewayEnrollmentIssueInput({
    agent_id: "binding-validation",
    name: "binding-validation",
    role: "binding-validation",
    runtime_type: "hermes",
    scopes: object.scopes,
    ttl_days: object.ttl_days,
    heartbeat_timeout_sec: object.heartbeat_timeout_sec,
    label: object.label,
  });
  return enrollmentConfig(normalized);
}

function requestIds(
  workspaceId: string,
  agentId: string,
  requestKey: string,
) {
  const idempotencyKeyHash = stableHash({
    contract: "agent_gateway_enrollment_request_idempotency_v1",
    workspace_id: workspaceId,
    requester_agent_id: agentId,
    idempotency_key: requestKey,
  });
  const suffix = idempotencyKeyHash.slice(0, 24);
  return {
    idempotencyKeyHash,
    requestId: `enr_${suffix}`,
    taskId: `tsk_enr_${suffix}`,
    runId: `run_enr_${suffix}`,
    approvalId: `ap_enr_${suffix}`,
  };
}

function immutableRequestBinding(input: {
  requestId: string;
  approvalId: string;
  taskId: string;
  runId: string;
  workspaceId: string;
  requesterAgentId: string;
  agentId: string;
  name: string;
  role: string;
  runtimeType: string;
  config: EnrollmentConfig;
  reason: string;
  approvalReason: string;
  approvalExpiresAt: string;
}) {
  return stableHash({
    contract: "agent_gateway_enrollment_immutable_binding_v1",
    request_id: input.requestId,
    approval_id: input.approvalId,
    approval_kind: "agent_enrollment",
    task_id: input.taskId,
    run_id: input.runId,
    workspace_id: input.workspaceId,
    requester_user_id: null,
    requester_agent_id: input.requesterAgentId,
    requested_by_agent_id: input.requesterAgentId,
    agent_id: input.agentId,
    name: input.name,
    role: input.role,
    runtime_type: input.runtimeType,
    request_policy: input.config,
    reason: input.reason,
    approval_reason: input.approvalReason,
    approval_expires_at: input.approvalExpiresAt,
  });
}

function requestSnapshot(
  row: EnrollmentGraphRow,
  config: EnrollmentConfig,
) {
  return {
    request_id: row.request_id,
    approval_id: row.approval_id,
    task_id: row.task_id,
    run_id: row.run_id,
    workspace_id: row.workspace_id,
    requester_user_id: null,
    requester_agent_id: row.agent_id,
    agent_id: row.agent_id,
    name: row.name,
    role: row.role,
    runtime_type: row.enrollment_runtime_type,
    scopes: config.scopes,
    ttl_days: config.ttl_days,
    heartbeat_timeout_sec: config.heartbeat_timeout_sec,
    label: config.label,
    status: row.enrollment_status,
    created_at: row.request_created_at,
    updated_at: row.request_updated_at,
    decided_at: row.request_decided_at,
    token_ref: row.token_id
      ? gatewayOpaqueReference("token", row.token_id)
      : null,
    token_id_omitted: true,
    token_hash_omitted: true,
    token_omitted: true,
  };
}

function approvalSnapshot(row: EnrollmentGraphRow) {
  return {
    approval_id: row.approval_id,
    approval_kind: row.approval_kind,
    task_id: row.task_id,
    run_id: row.run_id,
    requested_by_agent_id: row.requested_by_agent_id,
    approver_user_id: row.approver_user_id,
    decision: row.approval_decision,
    reason: row.approval_reason,
    expires_at: row.approval_expires_at,
    created_at: row.approval_created_at,
    decided_at: row.approval_decided_at,
  };
}

function requestResponse(
  graph: LockedEnrollmentGraph,
  outcome: "created" | "unchanged",
): OwnerResult {
  return {
    status: outcome === "created" ? 201 : 200,
    body: {
      ok: true,
      provider: "agentops-agent-gateway-enrollment",
      control_plane: "typescript_postgres",
      operation: "agent_gateway_enrollment_request",
      outcome,
      request: requestSnapshot(graph.row, graph.config),
      approval: approvalSnapshot(graph.row),
      token_issued: false,
      credential_generated: false,
      credentials_omitted: true,
      raw_config_omitted: true,
      raw_prompt_omitted: true,
      raw_response_omitted: true,
      token_omitted: true,
    },
  };
}

async function lockEnrollmentGraph(
  client: PoolClient,
  workspaceId: string,
  selector: { requestId?: string | null; approvalId?: string | null },
): Promise<LockedEnrollmentGraph> {
  const rows = await client.query<EnrollmentGraphRow>(
    `SELECT
      enrollment.request_id,enrollment.approval_id,enrollment.task_id,
      enrollment.run_id,enrollment.workspace_id,enrollment.agent_id,
      enrollment.name,enrollment.role,
      enrollment.runtime_type AS enrollment_runtime_type,
      enrollment.scopes_json,enrollment.reason,
      enrollment.status AS enrollment_status,enrollment.token_id,
      enrollment.created_at AS request_created_at,
      enrollment.updated_at AS request_updated_at,
      enrollment.decided_at AS request_decided_at,
      approval.approval_kind,approval.requested_by_agent_id,
      approval.approver_user_id,approval.decision AS approval_decision,
      approval.reason AS approval_reason,
      approval.expires_at AS approval_expires_at,
      approval.created_at AS approval_created_at,
      approval.decided_at AS approval_decided_at,
      task.requester_id AS requester_user_id,
      task.owner_agent_id AS task_owner_agent_id,
      task.workspace_id AS task_workspace_id,
      task.status AS task_status,task.updated_at AS task_updated_at,
      run.agent_id AS run_agent_id,run.workspace_id AS run_workspace_id,
      run.task_id AS run_task_id,run.runtime_type AS run_runtime_type,
      run.status AS run_status,run.approval_required,run.ended_at AS run_ended_at,
      agent.name AS agent_name,agent.role AS agent_role,
      agent.runtime_type AS agent_runtime_type,agent.status AS agent_status
    FROM agent_gateway_enrollment_requests enrollment
    JOIN approvals approval ON approval.approval_id=enrollment.approval_id
    JOIN tasks task ON task.task_id=enrollment.task_id
    JOIN runs run ON run.run_id=enrollment.run_id
    JOIN agents agent ON agent.agent_id=enrollment.agent_id
    WHERE enrollment.workspace_id=$1
      AND ($2::text IS NULL OR enrollment.request_id=$2)
      AND ($3::text IS NULL OR enrollment.approval_id=$3)
    ORDER BY enrollment.request_id
    LIMIT 2
    FOR UPDATE OF enrollment,approval,task,run,agent`,
    [
      workspaceId,
      selector.requestId || null,
      selector.approvalId || null,
    ],
  );
  if (rows.rows.length !== 1) {
    throw new ControlPlaneHttpError(
      404,
      "enrollment_request_not_found",
      "Enrollment request was not found in this workspace.",
    );
  }
  const row = rows.rows[0];
  if (
    row.approval_kind !== "agent_enrollment"
    || row.task_id === ""
    || row.run_id === ""
    || row.requested_by_agent_id !== row.agent_id
    || row.task_owner_agent_id !== row.agent_id
    || row.requester_user_id !== null
    || row.run_agent_id !== row.agent_id
    || row.workspace_id !== workspaceId
    || row.task_workspace_id !== workspaceId
    || row.run_workspace_id !== workspaceId
    || row.run_task_id !== row.task_id
    || row.agent_name !== row.name
    || row.agent_role !== (row.role || "")
    || row.run_runtime_type !== row.enrollment_runtime_type
    || row.agent_runtime_type !== row.enrollment_runtime_type
    || row.agent_status === "disabled"
  ) {
    throw new ControlPlaneHttpError(
      409,
      "enrollment_approval_binding_invalid",
      "Enrollment workspace, requester, Agent, task, run, or approval binding is invalid.",
    );
  }
  const config = parseEnrollmentConfig(row.scopes_json);
  const requestBindingHash = immutableRequestBinding({
    requestId: row.request_id,
    approvalId: row.approval_id,
    taskId: row.task_id,
    runId: row.run_id,
    workspaceId: row.workspace_id,
    requesterAgentId: row.agent_id,
    agentId: row.agent_id,
    name: row.name,
    role: row.role || "",
    runtimeType: row.enrollment_runtime_type,
    config,
    reason: row.reason || "",
    approvalReason: row.approval_reason || "",
    approvalExpiresAt: row.approval_expires_at || "",
  });
  const auditRows = await client.query<{
    actor_id: string | null;
    metadata_json: string;
  }>(
    `SELECT actor_id,metadata_json
    FROM audit_logs
    WHERE workspace_id=$1
      AND action='agent_gateway.enrollment_request'
      AND entity_type='agent_gateway_enrollment_requests'
      AND entity_id=$2
    ORDER BY created_at,audit_id
    LIMIT 2`,
    [workspaceId, row.request_id],
  );
  if (auditRows.rows.length !== 1) {
    throw new ControlPlaneHttpError(
      409,
      "enrollment_request_binding_evidence_invalid",
      "Enrollment request binding evidence is missing or ambiguous.",
    );
  }
  const metadata = parseMetadata(auditRows.rows[0].metadata_json);
  const idempotencyKeyHash = String(
    metadata?.idempotency_key_hash || "",
  );
  if (
    auditRows.rows[0].actor_id !== row.agent_id
    || metadata?.request_binding_hash !== requestBindingHash
    || !/^[a-f0-9]{64}$/.test(idempotencyKeyHash)
  ) {
    throw new ControlPlaneHttpError(
      409,
      "enrollment_request_binding_evidence_invalid",
      "Enrollment request binding evidence does not match current state.",
    );
  }
  return {
    row,
    config,
    requestBindingHash,
    requestIdempotencyKeyHash: idempotencyKeyHash,
  };
}

function decisionResponse(
  graph: LockedEnrollmentGraph,
  outcome: "updated" | "unchanged",
): OwnerResult {
  return {
    status: 200,
    body: {
      ok: true,
      provider: "agentops-human-approval-decision",
      control_plane: "typescript_postgres",
      operation: "agent_enrollment_approval_decision",
      outcome,
      decision: graph.row.approval_decision,
      approval: approvalSnapshot(graph.row),
      linked_state: {
        request_id: graph.row.request_id,
        enrollment_status: graph.row.enrollment_status,
        task_status: graph.row.task_status,
        run_status: graph.row.run_status,
      },
      credentials_omitted: true,
      raw_body_omitted: true,
      raw_config_omitted: true,
      token_omitted: true,
    },
  };
}

export async function requestGatewayEnrollment(
  request: Request,
): Promise<OwnerResult> {
  const body = await boundedJsonObject(request, {
    maxBytes: ENROLLMENT_APPROVAL_MAX_BODY_BYTES,
    label: "Gateway enrollment request",
  });
  rejectUnknownFields(body, REQUEST_FIELDS, "gateway_enrollment_request");
  const input = parseGatewayEnrollmentIssueInput(body);
  const reason = safeReason(body.reason);
  const requestKey = idempotencyKey(request.headers);
  return withPostgresTransaction(async (client) => {
    const identity = await authenticateAgentGateway(
      client,
      request.headers,
      "approvals:request",
    );
    enforceRequestingAgent(identity, request, body);
    await assertRequestingAgentBinding(client, identity, input);
    const ids = requestIds(
      identity.workspaceId,
      identity.agentId,
      requestKey,
    );
    await client.query(
      "SELECT pg_advisory_xact_lock(hashtextextended($1,0))",
      [`gateway-enrollment-request:${identity.workspaceId}:${identity.agentId}:${ids.idempotencyKeyHash}`],
    );
    const existing = await client.query<{ request_id: string }>(
      `SELECT request_id
      FROM agent_gateway_enrollment_requests
      WHERE request_id=$1 AND workspace_id=$2`,
      [ids.requestId, identity.workspaceId],
    );
    if (existing.rows[0]) {
      const graph = await lockEnrollmentGraph(client, identity.workspaceId, {
        requestId: ids.requestId,
      });
      const expectedBindingHash = immutableRequestBinding({
        requestId: ids.requestId,
        approvalId: ids.approvalId,
        taskId: ids.taskId,
        runId: ids.runId,
        workspaceId: identity.workspaceId,
        requesterAgentId: identity.agentId,
        agentId: input.agentId,
        name: input.name,
        role: input.role,
        runtimeType: input.runtimeType,
        config: enrollmentConfig(input),
        reason,
        approvalReason: `Approve scoped Agent Gateway enrollment for ${input.agentId}.`,
        approvalExpiresAt: graph.row.approval_expires_at || "",
      });
      if (
        graph.requestIdempotencyKeyHash !== ids.idempotencyKeyHash
        || graph.requestBindingHash !== expectedBindingHash
      ) {
        throw new ControlPlaneHttpError(
          409,
          "enrollment_request_idempotency_conflict",
          "Idempotency-Key is already bound to another normalized enrollment request.",
        );
      }
      return requestResponse(graph, "unchanged");
    }

    const conflicting = await client.query<{ request_id: string }>(
      `SELECT request_id
      FROM agent_gateway_enrollment_requests
      WHERE workspace_id=$1 AND agent_id=$2
        AND status IN ('pending','approved','issued')
      ORDER BY created_at,request_id
      LIMIT 1`,
      [identity.workspaceId, input.agentId],
    );
    if (conflicting.rows[0]) {
      throw new ControlPlaneHttpError(
        409,
        "enrollment_request_agent_conflict",
        "The Agent already has an active enrollment request or issued enrollment.",
      );
    }
    const now = new Date();
    const createdAt = now.toISOString();
    const expiresAt = new Date(now.getTime() + APPROVAL_TTL_MS).toISOString();
    const approvalReason =
      `Approve scoped Agent Gateway enrollment for ${input.agentId}.`;
    const config = enrollmentConfig(input);
    const requestBindingHash = immutableRequestBinding({
      requestId: ids.requestId,
      approvalId: ids.approvalId,
      taskId: ids.taskId,
      runId: ids.runId,
      workspaceId: identity.workspaceId,
      requesterAgentId: identity.agentId,
      agentId: input.agentId,
      name: input.name,
      role: input.role,
      runtimeType: input.runtimeType,
      config,
      reason,
      approvalReason,
      approvalExpiresAt: expiresAt,
    });
    await client.query(
      `INSERT INTO tasks(
        task_id,workspace_id,title,description,requester_id,owner_agent_id,
        collaborator_agent_ids,status,priority,due_date,acceptance_criteria,
        risk_level,budget_limit_usd,created_at,updated_at
      ) VALUES(
        $1,$2,$3,$4,$5,$6,'[]','waiting_approval','high',NULL,
        'Human approval is required before any enrollment credential is issued.',
        'high',0,$7,$7
      )`,
      [
        ids.taskId,
        identity.workspaceId,
        `Agent Gateway enrollment request: ${input.name}`,
        reason,
        null,
        input.agentId,
        createdAt,
      ],
    );
    await client.query(
      `INSERT INTO runs(
        run_id,workspace_id,task_id,agent_id,runtime_type,status,started_at,
        ended_at,duration_ms,input_summary,output_summary,model_provider,
        model_name,input_tokens,output_tokens,reasoning_tokens,cost_usd,
        error_type,error_message,trace_id,parent_run_id,delegation_id,
        approval_required,agent_plan_id,plan_hash,created_at
      ) VALUES(
        $1,$2,$3,$4,$5,'waiting_approval',$6,NULL,NULL,$7,NULL,
        'agent-gateway','enrollment-request',0,0,0,0,NULL,NULL,$8,NULL,$9,
        1,NULL,NULL,$6
      )`,
      [
        ids.runId,
        identity.workspaceId,
        ids.taskId,
        input.agentId,
        input.runtimeType,
        createdAt,
        `Enrollment request with ${input.scopes.length} approved-scope candidate(s).`,
        `trace_${ids.idempotencyKeyHash.slice(0, 20)}`,
        `delegation_${ids.idempotencyKeyHash.slice(0, 20)}`,
      ],
    );
    await client.query(
      `INSERT INTO approvals(
        approval_id,approval_kind,task_id,run_id,tool_call_id,
        requested_by_agent_id,approver_user_id,decision,reason,expires_at,
        created_at,decided_at
      ) VALUES($1,'agent_enrollment',$2,$3,NULL,$4,NULL,'pending',$5,$6,$7,NULL)`,
      [
        ids.approvalId,
        ids.taskId,
        ids.runId,
        input.agentId,
        approvalReason,
        expiresAt,
        createdAt,
      ],
    );
    await client.query(
      `INSERT INTO agent_gateway_enrollment_requests(
        request_id,approval_id,task_id,run_id,workspace_id,agent_id,name,role,
        runtime_type,scopes_json,reason,status,token_id,created_at,updated_at,
        decided_at
      ) VALUES(
        $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,'pending',NULL,$12,$12,NULL
      )`,
      [
        ids.requestId,
        ids.approvalId,
        ids.taskId,
        ids.runId,
        identity.workspaceId,
        input.agentId,
        input.name,
        input.role,
        input.runtimeType,
        JSON.stringify(config),
        reason,
        createdAt,
      ],
    );
    await appendAudit(client, {
      workspaceId: identity.workspaceId,
      actorType: "agent",
      actorId: identity.agentId,
      action: "agent_gateway.enrollment_request",
      entityType: "agent_gateway_enrollment_requests",
      entityId: ids.requestId,
      after: {
        request_id: ids.requestId,
        approval_id: ids.approvalId,
        task_id: ids.taskId,
        run_id: ids.runId,
        workspace_id: identity.workspaceId,
        requester_user_id: null,
        requester_agent_id: identity.agentId,
        agent_id: input.agentId,
        runtime_type: input.runtimeType,
        status: "pending",
        scopes_count: input.scopes.length,
        token_id_omitted: true,
        token_omitted: true,
      },
      metadata: {
        credential_ref: gatewayOpaqueReference(
          identity.mode === "agent_token" ? "token" : "session",
          identity.credentialId,
        ),
        credential_mode: identity.mode,
        idempotency_key_hash: ids.idempotencyKeyHash,
        request_binding_hash: requestBindingHash,
        request_policy_hash: stableHash(config),
        credential_generated: false,
        raw_config_omitted: true,
        raw_prompt_omitted: true,
        raw_response_omitted: true,
        token_omitted: true,
      },
      requestHash: requestBindingHash,
    });
    await appendRuntimeEvent(client, {
      workspaceId: identity.workspaceId,
      eventType: "agent.enrollment.request",
      status: "waiting_approval",
      runId: ids.runId,
      taskId: ids.taskId,
      agentId: input.agentId,
      outputSummary: `Enrollment request ${gatewayOpaqueReference("request", ids.requestId)} awaits Human approval.`,
      rawPayloadHash: requestBindingHash,
    });
    return requestResponse(
      await lockEnrollmentGraph(client, identity.workspaceId, {
        requestId: ids.requestId,
      }),
      "created",
    );
  });
}

export async function decideGatewayEnrollmentApproval(
  client: PoolClient,
  identity: HumanSessionIdentity,
  approvalId: string,
  decision: "approved" | "rejected",
  idempotencyHash: string,
): Promise<OwnerResult> {
  requireGatewayAdministrator(identity);
  const graph = await lockEnrollmentGraph(client, identity.workspaceId, {
    approvalId,
  });
  const requestHash = stableHash({
    contract: "agent_gateway_enrollment_human_decision_v1",
    workspace_id: identity.workspaceId,
    user_id: identity.userId,
    approval_id: approvalId,
    decision,
    enrollment_request_binding_hash: graph.requestBindingHash,
  });
  const existing = (await client.query<DecisionRequestRow>(
    `SELECT request_hash,approval_id,decision,status
    FROM human_approval_decision_requests
    WHERE workspace_id=$1 AND user_id=$2 AND idempotency_key_hash=$3
    FOR UPDATE`,
    [identity.workspaceId, identity.userId, idempotencyHash],
  )).rows[0];
  if (
    existing
    && (
      existing.request_hash !== requestHash
      || existing.approval_id !== approvalId
      || existing.decision !== decision
      || existing.status !== "completed"
    )
  ) {
    throw new ControlPlaneHttpError(
      409,
      "approval_idempotency_conflict",
      "Idempotency-Key is already bound to another approval decision.",
    );
  }
  if (existing) {
    if (
      graph.row.approval_decision !== decision
      || graph.row.approver_user_id !== identity.userId
      || (
        graph.row.enrollment_status !== decision
        && !(
          decision === "approved"
          && graph.row.enrollment_status === "issued"
        )
      )
    ) {
      throw new ControlPlaneHttpError(
        409,
        "approval_replay_state_conflict",
        "Enrollment approval replay state is unavailable.",
      );
    }
    return decisionResponse(graph, "unchanged");
  }
  if (
    graph.row.approval_decision !== "pending"
    || graph.row.enrollment_status !== "pending"
    || graph.row.task_status !== "waiting_approval"
    || graph.row.run_status !== "waiting_approval"
    || graph.row.approver_user_id
    || graph.row.approval_decided_at
    || graph.row.request_decided_at
  ) {
    throw new ControlPlaneHttpError(
      409,
      "approval_decision_conflict",
      "Enrollment approval is no longer pending.",
    );
  }
  if (
    decision === "approved"
    && (
      !graph.row.approval_expires_at
      || !Number.isFinite(Date.parse(graph.row.approval_expires_at))
      || Date.parse(graph.row.approval_expires_at) <= Date.now()
    )
  ) {
    throw new ControlPlaneHttpError(
      409,
      "approval_expired",
      "Expired enrollment approval cannot authorize credential issue.",
    );
  }
  const now = new Date().toISOString();
  const approval = await client.query(
    `UPDATE approvals
    SET decision=$1,approver_user_id=$2,decided_at=$3
    WHERE approval_id=$4 AND approval_kind='agent_enrollment'
      AND decision='pending'
    RETURNING approval_id`,
    [decision, identity.userId, now, approvalId],
  );
  const enrollment = await client.query(
    `UPDATE agent_gateway_enrollment_requests
    SET status=$1,decided_at=$2,updated_at=$2
    WHERE request_id=$3 AND workspace_id=$4 AND approval_id=$5
      AND status='pending'
    RETURNING request_id`,
    [
      decision,
      now,
      graph.row.request_id,
      identity.workspaceId,
      approvalId,
    ],
  );
  const taskStatus = decision === "approved" ? "completed" : "blocked";
  const runStatus = decision === "approved" ? "completed" : "blocked";
  const task = await client.query(
    `UPDATE tasks SET status=$1,updated_at=$2
    WHERE task_id=$3 AND workspace_id=$4 AND status='waiting_approval'
    RETURNING task_id`,
    [taskStatus, now, graph.row.task_id, identity.workspaceId],
  );
  const run = await client.query(
    `UPDATE runs
    SET status=$1,approval_required=0,ended_at=COALESCE(ended_at,$2),
      output_summary=CASE WHEN $1='completed'
        THEN 'Enrollment request approved by a Human reviewer.'
        ELSE output_summary END,
      error_type=CASE WHEN $1='blocked' THEN 'ApprovalRejected' ELSE NULL END,
      error_message=CASE WHEN $1='blocked'
        THEN 'The enrollment request was rejected by a Human reviewer.'
        ELSE NULL END
    WHERE run_id=$3 AND task_id=$4 AND workspace_id=$5
      AND status='waiting_approval'
    RETURNING run_id`,
    [
      runStatus,
      now,
      graph.row.run_id,
      graph.row.task_id,
      identity.workspaceId,
    ],
  );
  if (
    approval.rowCount !== 1
    || enrollment.rowCount !== 1
    || task.rowCount !== 1
    || run.rowCount !== 1
  ) {
    throw new ControlPlaneHttpError(
      409,
      "approval_decision_conflict",
      "Enrollment approval lost its single-winner transition.",
    );
  }
  await client.query(
    `INSERT INTO human_approval_decision_requests(
      workspace_id,user_id,idempotency_key_hash,request_hash,approval_id,
      decision,status,created_at,completed_at
    ) VALUES($1,$2,$3,$4,$5,$6,'completed',$7,$7)`,
    [
      identity.workspaceId,
      identity.userId,
      idempotencyHash,
      requestHash,
      approvalId,
      decision,
      now,
    ],
  );
  const metadata = {
    session_ref: identity.sessionRef,
    membership_role: identity.membershipRole,
    approval_id: approvalId,
    request_binding_hash: graph.requestBindingHash,
    idempotency_key_hash: idempotencyHash,
    entitlement_evaluated: false,
    credential_generated: false,
    raw_config_omitted: true,
    token_omitted: true,
  };
  await appendAudit(client, {
    workspaceId: identity.workspaceId,
    actorType: "user",
    actorId: identity.userId,
    action: `agent_gateway.enrollment_request_${decision}`,
    entityType: "agent_gateway_enrollment_requests",
    entityId: graph.row.request_id,
    before: requestSnapshot(graph.row, graph.config),
    after: {
      ...requestSnapshot(graph.row, graph.config),
      status: decision,
      updated_at: now,
      decided_at: now,
    },
    metadata,
    requestHash,
  });
  await appendAudit(client, {
    workspaceId: identity.workspaceId,
    actorType: "user",
    actorId: identity.userId,
    action: `approval.agent_enrollment.${decision}`,
    entityType: "approvals",
    entityId: approvalId,
    before: approvalSnapshot(graph.row),
    after: {
      ...approvalSnapshot(graph.row),
      approver_user_id: identity.userId,
      decision,
      decided_at: now,
    },
    metadata,
    requestHash,
  });
  await appendRuntimeEvent(client, {
    workspaceId: identity.workspaceId,
    eventType: `approval.agent_enrollment.${decision}`,
    status: "completed",
    runId: graph.row.run_id,
    taskId: graph.row.task_id,
    agentId: graph.row.agent_id,
    outputSummary: `Human reviewer marked the enrollment request ${decision}.`,
    rawPayloadHash: requestHash,
  });
  return decisionResponse(
    await lockEnrollmentGraph(client, identity.workspaceId, { approvalId }),
    "updated",
  );
}

function assertIssueReady(graph: LockedEnrollmentGraph) {
  const approvalTime = Date.parse(graph.row.approval_decided_at || "");
  const requestTime = Date.parse(graph.row.request_decided_at || "");
  const expiresAt = Date.parse(graph.row.approval_expires_at || "");
  if (
    graph.row.approval_decision !== "approved"
    || graph.row.enrollment_status !== "approved"
    || !graph.row.approver_user_id
    || !Number.isFinite(approvalTime)
    || approvalTime !== requestTime
    || !Number.isFinite(expiresAt)
    || expiresAt <= Date.now()
    || graph.row.task_status !== "completed"
    || graph.row.run_status !== "completed"
    || graph.row.approval_required !== 0
    || graph.row.agent_status === "disabled"
    || graph.row.token_id
  ) {
    throw new ControlPlaneHttpError(
      409,
      "enrollment_approval_required",
      "Enrollment request must retain an unexpired Human approval and immutable binding before credential issue.",
    );
  }
}

function assertStoredToken(
  graph: LockedEnrollmentGraph,
  token: StoredTokenRow,
) {
  const createdAt = Date.parse(token.created_at);
  const expiresAt = Date.parse(token.expires_at || "");
  const expectedTtl = graph.config.ttl_days * 24 * 60 * 60 * 1000;
  if (
    token.token_id !== graph.row.token_id
    || token.workspace_id !== graph.row.workspace_id
    || token.agent_id !== graph.row.agent_id
    || token.label !== graph.config.label
    || token.heartbeat_timeout_sec !== graph.config.heartbeat_timeout_sec
    || JSON.stringify(parseGatewayTokenScopes(token.scopes_json))
      !== JSON.stringify(graph.config.scopes)
    || !/^[a-f0-9]{64}$/.test(token.token_hash)
    || !Number.isFinite(createdAt)
    || !Number.isFinite(expiresAt)
    || Math.abs(expiresAt - createdAt - expectedTtl) > 1_000
  ) {
    throw new ControlPlaneHttpError(
      409,
      "enrollment_issued_state_invalid",
      "Issued enrollment credential does not match its approved immutable request.",
    );
  }
}

async function issuedReplay(
  client: PoolClient,
  identity: HumanSessionIdentity,
  graph: LockedEnrollmentGraph,
  requestKey: string,
  issueBindingHash: string,
): Promise<OwnerResult> {
  if (graph.row.enrollment_status !== "issued" || !graph.row.token_id) {
    throw new ControlPlaneHttpError(
      409,
      "enrollment_issued_state_invalid",
      "Issued enrollment request is missing its credential binding.",
    );
  }
  const token = (await client.query<StoredTokenRow>(
    `SELECT token_id,token_hash,workspace_id,agent_id,scopes_json,status,label,
      heartbeat_timeout_sec,created_at,expires_at,revoked_at
    FROM agent_gateway_tokens
    WHERE token_id=$1 AND workspace_id=$2
    FOR UPDATE`,
    [graph.row.token_id, graph.row.workspace_id],
  )).rows[0];
  if (!token) {
    throw new ControlPlaneHttpError(
      409,
      "enrollment_issued_state_invalid",
      "Issued enrollment credential state is unavailable.",
    );
  }
  assertStoredToken(graph, token);
  const idempotencyKeyHash = approvedGatewayEnrollmentIdempotencyKeyHash(
    identity,
    requestKey,
  );
  const issueAudits = await client.query<{
    actor_id: string | null;
    metadata_json: string;
  }>(
    `SELECT actor_id,metadata_json
    FROM audit_logs
    WHERE workspace_id=$1
      AND action='agent_gateway.enrollment_issue_approved'
      AND entity_type='agent_gateway_tokens'
      AND entity_id=$2
    ORDER BY created_at,audit_id
    LIMIT 2`,
    [
      graph.row.workspace_id,
      gatewayOpaqueReference("token", token.token_id),
    ],
  );
  const metadata = issueAudits.rows.length === 1
    ? parseMetadata(issueAudits.rows[0].metadata_json)
    : null;
  if (
    !metadata
    || issueAudits.rows[0].actor_id !== identity.userId
    || metadata.idempotency_owner !== "gateway_admin_token_issue"
    || metadata.enrollment_operation !== "issue_approved"
    || metadata.idempotency_key_hash !== idempotencyKeyHash
    || metadata.request_binding_hash !== issueBindingHash
  ) {
    throw new ControlPlaneHttpError(
      409,
      "enrollment_issue_idempotency_conflict",
      "Idempotency-Key or selector does not match the issued enrollment request.",
    );
  }
  return {
    status: 200,
    body: {
      ok: true,
      provider: "agentops-agent-gateway-enrollment",
      control_plane: "typescript_postgres",
      operation: "enrollment_issue_approved",
      created: false,
      replayed: true,
      issued_from_request_id: graph.row.request_id,
      approval_id: graph.row.approval_id,
      token_ref: gatewayOpaqueReference("token", token.token_id),
      token_id_omitted: true,
      agent_id: token.agent_id,
      workspace_id: token.workspace_id,
      scopes: graph.config.scopes,
      status: token.status,
      expires_at: token.expires_at,
      heartbeat_timeout_sec: token.heartbeat_timeout_sec,
      credential_generated: false,
      credentials_omitted: true,
      raw_config_omitted: true,
      token_omitted: true,
      note: "The one-time credential was already delivered and cannot be shown again.",
    },
  };
}

export async function issueApprovedGatewayEnrollment(
  request: Request,
): Promise<OwnerResult> {
  const body = await boundedJsonObject(request, {
    maxBytes: ENROLLMENT_APPROVAL_MAX_BODY_BYTES,
    label: "Approved Gateway enrollment issue",
  });
  rejectUnknownFields(body, ISSUE_FIELDS, "gateway_enrollment_issue_approved");
  const requestId = identifier(body.request_id, "request_id", true);
  const approvalId = identifier(body.approval_id, "approval_id", true);
  if ([requestId, approvalId].filter(Boolean).length !== 1) {
    throw new ControlPlaneHttpError(
      400,
      "enrollment_issue_selector_required",
      "Provide exactly one request_id or approval_id.",
    );
  }
  const requestKey = idempotencyKey(request.headers);
  return withPostgresTransaction(async (client) => {
    const identity = await authenticateHumanReviewer(
      client,
      request.headers,
      body.workspace_id,
    );
    requireGatewayAdministrator(identity);
    await client.query(
      "SELECT pg_advisory_xact_lock(hashtextextended($1,0))",
      [`gateway-enrollment-issue:${identity.workspaceId}:${requestId || approvalId}`],
    );
    const graph = await lockEnrollmentGraph(client, identity.workspaceId, {
      requestId,
      approvalId,
    });
    const issueRequestBinding = {
      contract: "agent_gateway_approved_issue_binding_v1",
      workspace_id: identity.workspaceId,
      enrollment_request_binding_hash: graph.requestBindingHash,
      selector: requestId
        ? { request_id: requestId }
        : { approval_id: approvalId },
    };
    const issueBindingHash = stableHash(issueRequestBinding);
    if (graph.row.enrollment_status === "issued") {
      return issuedReplay(
        client,
        identity,
        graph,
        requestKey,
        issueBindingHash,
      );
    }
    assertIssueReady(graph);
    const issueIdempotencyKeyHash =
      approvedGatewayEnrollmentIdempotencyKeyHash(identity, requestKey);
    const input: GatewayEnrollmentIssueInput = {
      agentId: graph.row.agent_id,
      name: graph.row.name,
      role: graph.row.role || "Remote AI Digital Employee",
      runtimeType: graph.row.enrollment_runtime_type,
      scopes: graph.config.scopes,
      ttlDays: graph.config.ttl_days,
      heartbeatTimeoutSec: graph.config.heartbeat_timeout_sec,
      label: graph.config.label,
    };
    const issued = await issueApprovedGatewayEnrollmentToken(
      client,
      identity,
      input,
      requestKey,
      {
        requestId: graph.row.request_id,
        approvalId: graph.row.approval_id,
        taskId: graph.row.task_id,
        runId: graph.row.run_id,
      },
      issueRequestBinding,
    );
    if (issued.entitlementDenied) {
      return {
        status: 403,
        body: {
          ...issued.response,
          operation: "enrollment_issue_approved",
          issued_from_request_id: graph.row.request_id,
          approval_id: graph.row.approval_id,
        },
      };
    }
    if (issued.replayed) {
      throw new ControlPlaneHttpError(
        409,
        "enrollment_issued_state_invalid",
        "Credential idempotency evidence exists without its enrollment request transition.",
      );
    }
    const now = new Date().toISOString();
    const updated = await client.query(
      `UPDATE agent_gateway_enrollment_requests
      SET status='issued',token_id=$1,updated_at=$2
      WHERE request_id=$3 AND workspace_id=$4 AND approval_id=$5
        AND status='approved' AND token_id IS NULL
      RETURNING request_id`,
      [
        issued.row.token_id,
        now,
        graph.row.request_id,
        identity.workspaceId,
        graph.row.approval_id,
      ],
    );
    if (updated.rowCount !== 1) {
      throw new ControlPlaneHttpError(
        409,
        "enrollment_issue_state_conflict",
        "Enrollment credential issue lost its single-winner transition.",
      );
    }
    await appendAudit(client, {
      workspaceId: identity.workspaceId,
      actorType: "user",
      actorId: identity.userId,
      action: "agent_gateway.enrollment_issue_approved_request",
      entityType: "agent_gateway_enrollment_requests",
      entityId: graph.row.request_id,
      before: requestSnapshot(graph.row, graph.config),
      after: {
        ...requestSnapshot(graph.row, graph.config),
        status: "issued",
        updated_at: now,
        token_ref: gatewayOpaqueReference("token", issued.row.token_id),
      },
      metadata: {
        session_ref: identity.sessionRef,
        membership_role: identity.membershipRole,
        approval_id: graph.row.approval_id,
        idempotency_key_hash: issueIdempotencyKeyHash,
        request_binding_hash: graph.requestBindingHash,
        issue_binding_hash: issueBindingHash,
        token_ref: gatewayOpaqueReference("token", issued.row.token_id),
        one_time_credential_response: true,
        raw_config_omitted: true,
        token_id_omitted: true,
        token_hash_omitted: true,
        token_omitted: true,
      },
      requestHash: issueBindingHash,
    });
    const {
      token_id: omittedTokenId,
      ...publicIssueResponse
    } = issued.response;
    void omittedTokenId;
    return {
      status: 201,
      body: {
        ...publicIssueResponse,
        operation: "enrollment_issue_approved",
        issued_from_request_id: graph.row.request_id,
        approval_id: graph.row.approval_id,
        token_id_omitted: true,
        credential_generated: true,
        raw_config_omitted: true,
      },
    };
  });
}
