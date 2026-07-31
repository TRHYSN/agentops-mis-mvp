import type { PoolClient } from "pg";

import { decideGatewayEnrollmentApproval } from "./agentGatewayEnrollmentApprovals";
import { verifyCurrentCustomerDeliveryPlanEvidence } from "./customerDeliveryPlanEvidence";
import { withPostgresTransaction } from "./db";
import {
  authenticateHumanMember,
  authenticateHumanReviewer,
  rejectMachineCredentials,
  validateWriteOrigin,
  type HumanSessionIdentity,
} from "./humanSession";
import { ControlPlaneHttpError } from "./http";
import { appendAudit, appendRuntimeEvent, stableHash } from "./ledger";
import { preparedActionHash } from "./preparedActions";
import { settleExistingTerminalRunCost } from "./terminalRunCost";

type ApprovalDecision = "approved" | "rejected";

type TaskRow = {
  task_id: string;
  workspace_id: string;
  owner_agent_id: string | null;
  status: string;
  updated_at: string;
};

type RunRow = {
  run_id: string;
  workspace_id: string;
  task_id: string;
  agent_id: string;
  runtime_type: string;
  model_provider: string | null;
  status: string;
  approval_required: number;
  agent_plan_id: string | null;
  plan_hash: string | null;
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
  expires_at: string | null;
  created_at: string;
  decided_at: string | null;
};

type DecisionRequestRow = {
  workspace_id: string;
  user_id: string;
  idempotency_key_hash: string;
  request_hash: string;
  approval_id: string;
  decision: string;
  status: string;
  created_at: string;
  completed_at: string | null;
};

type LockedDeliveryGraph = {
  task: TaskRow;
  run: RunRow;
  approval: ApprovalRow;
};

type DeliveryRequestBinding = {
  manifestId: string;
  planId: string;
  planVersion: number;
  planHash: string;
  verificationResultHash: string;
  bindingHash: string;
};

type PreparedActionDecisionRow = {
  action_id: string;
  workspace_id: string;
  task_id: string;
  run_id: string;
  tool_call_id: string;
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

type PreparedActionToolRow = {
  tool_call_id: string;
  run_id: string;
  agent_id: string;
  tool_name: string;
  status: string;
  side_effect_id: string | null;
  ended_at: string | null;
};

type LockedPreparedActionGraph = {
  task: TaskRow;
  run: RunRow;
  approval: ApprovalRow;
  action: PreparedActionDecisionRow;
  tool: PreparedActionToolRow;
};

const HUMAN_OWNED_FIELDS = new Set([
  "actor_id",
  "actor_type",
  "approver_id",
  "approver_user_id",
  "decided_at",
  "decision",
  "human_user_id",
  "session_id",
]);

function identifier(value: unknown, field: string) {
  const normalized = String(value ?? "").trim();
  if (!/^[A-Za-z0-9._:-]{1,128}$/.test(normalized)) {
    throw new ControlPlaneHttpError(
      400,
      `${field}_invalid`,
      `${field} must use 1-128 safe identifier characters.`,
    );
  }
  return normalized;
}

function requestedDecision(value: unknown): ApprovalDecision {
  if (value === "approve") return "approved";
  if (value === "reject") return "rejected";
  throw new ControlPlaneHttpError(
    404,
    "approval_decision_not_found",
    "Approval decision route was not found.",
  );
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

function validateDecisionBody(body: Record<string, unknown>) {
  for (const field of Object.keys(body)) {
    if (HUMAN_OWNED_FIELDS.has(field)) {
      throw new ControlPlaneHttpError(
        403,
        "human_actor_server_owned",
        "Human decision actor and terminal state are derived from the authenticated session.",
      );
    }
    if (field !== "workspace_id") {
      throw new ControlPlaneHttpError(
        400,
        "approval_decision_field_unsupported",
        "Approval decisions accept only workspace_id.",
      );
    }
  }
}

function taskSnapshot(row: TaskRow) {
  return {
    task_id: row.task_id,
    workspace_id: row.workspace_id,
    owner_agent_id: row.owner_agent_id,
    status: row.status,
    updated_at: row.updated_at,
  };
}

function runSnapshot(row: RunRow) {
  return {
    run_id: row.run_id,
    workspace_id: row.workspace_id,
    task_id: row.task_id,
    agent_id: row.agent_id,
    runtime_type: row.runtime_type,
    model_provider: row.model_provider,
    status: row.status,
    approval_required: Boolean(row.approval_required),
    agent_plan_id: row.agent_plan_id,
    plan_hash: row.plan_hash,
  };
}

function approvalSnapshot(row: ApprovalRow) {
  return {
    approval_id: row.approval_id,
    approval_kind: row.approval_kind,
    task_id: row.task_id,
    run_id: row.run_id,
    tool_call_id: row.tool_call_id,
    requested_by_agent_id: row.requested_by_agent_id,
    approver_user_id: row.approver_user_id,
    decision: row.decision,
    expires_at: row.expires_at,
    created_at: row.created_at,
    decided_at: row.decided_at,
  };
}

function publicApproval(row: ApprovalRow) {
  return {
    approval_id: row.approval_id,
    approval_kind: row.approval_kind,
    task_id: row.task_id,
    run_id: row.run_id,
    requested_by_agent_id: row.requested_by_agent_id,
    approver_user_id: row.approver_user_id,
    decision: row.decision,
    expires_at: row.expires_at,
    created_at: row.created_at,
    decided_at: row.decided_at,
  };
}

function hiddenApproval(): never {
  throw new ControlPlaneHttpError(
    404,
    "approval_not_found",
    "Approval was not found in this workspace.",
  );
}

function publicPreparedAction(row: PreparedActionDecisionRow) {
  return {
    action_id: row.action_id,
    workspace_id: row.workspace_id,
    task_id: row.task_id,
    run_id: row.run_id,
    tool_call_id: row.tool_call_id,
    approval_id: row.approval_id,
    requested_by_agent_id: row.requested_by_agent_id,
    action_type: row.action_type,
    target_resource: row.target_resource,
    risk_level: row.risk_level,
    policy_version: row.policy_version,
    action_hash: row.action_hash,
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

function approvalExpired(row: ApprovalRow) {
  if (!row.expires_at) return false;
  const expiresAt = Date.parse(row.expires_at);
  if (!Number.isFinite(expiresAt)) {
    throw new ControlPlaneHttpError(
      503,
      "approval_state_invalid",
      "Approval expiry state is invalid.",
    );
  }
  return expiresAt <= Date.now();
}

function jsonObject(value: string) {
  try {
    const parsed: unknown = JSON.parse(value);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? parsed as Record<string, unknown>
      : {};
  } catch {
    return {};
  }
}

async function lockCustomerDeliveryGraph(
  client: PoolClient,
  workspaceId: string,
  approvalId: string,
): Promise<LockedDeliveryGraph> {
  const hint = (await client.query<{ task_id: string; run_id: string }>(
    `SELECT approval.task_id,approval.run_id
    FROM approvals approval
    JOIN tasks task ON task.task_id=approval.task_id
      AND task.workspace_id=$2
    JOIN runs run ON run.run_id=approval.run_id
      AND run.task_id=task.task_id
      AND run.workspace_id=$2
    WHERE approval.approval_id=$1`,
    [approvalId, workspaceId],
  )).rows[0];
  if (!hint) return hiddenApproval();

  await client.query("SELECT pg_advisory_xact_lock(hashtext($1))", [
    `agentops-task:${hint.task_id}`,
  ]);
  const task = (await client.query<TaskRow>(
    `SELECT task_id,workspace_id,owner_agent_id,status,updated_at
    FROM tasks WHERE task_id=$1 AND workspace_id=$2 FOR UPDATE`,
    [hint.task_id, workspaceId],
  )).rows[0];
  if (!task) return hiddenApproval();

  await client.query("SELECT pg_advisory_xact_lock(hashtext($1))", [
    `agentops-run:${hint.run_id}`,
  ]);
  const run = (await client.query<RunRow>(
    `SELECT run_id,workspace_id,task_id,agent_id,runtime_type,model_provider,
      status,approval_required,agent_plan_id,plan_hash
    FROM runs
    WHERE run_id=$1 AND task_id=$2 AND workspace_id=$3 FOR UPDATE`,
    [hint.run_id, hint.task_id, workspaceId],
  )).rows[0];
  if (!run) return hiddenApproval();

  await client.query("SELECT pg_advisory_xact_lock(hashtext($1))", [
    `agentops-approval:${workspaceId}:${approvalId}`,
  ]);
  const approval = (await client.query<ApprovalRow>(
    `SELECT approval_id,approval_kind,task_id,run_id,tool_call_id,
      requested_by_agent_id,approver_user_id,decision,expires_at,created_at,
      decided_at
    FROM approvals WHERE approval_id=$1 FOR UPDATE`,
    [approvalId],
  )).rows[0];
  if (!approval) return hiddenApproval();
  if (
    approval.approval_kind !== "customer_delivery"
    || approval.task_id !== task.task_id
    || approval.run_id !== run.run_id
    || approval.tool_call_id !== null
    || approval.requested_by_agent_id !== run.agent_id
  ) {
    throw new ControlPlaneHttpError(
      409,
      "approval_binding_invalid",
      "Customer-delivery approval binding is invalid.",
    );
  }
  return { task, run, approval };
}

async function approvalKindInWorkspace(
  client: PoolClient,
  workspaceId: string,
  approvalId: string,
) {
  const row = (await client.query<{ approval_kind: string }>(
    `SELECT approval.approval_kind
    FROM approvals approval
    JOIN tasks task ON task.task_id=approval.task_id
      AND task.workspace_id=$2
    WHERE approval.approval_id=$1`,
    [approvalId, workspaceId],
  )).rows[0];
  if (!row) return hiddenApproval();
  return row.approval_kind;
}

async function lockPreparedActionGraph(
  client: PoolClient,
  workspaceId: string,
  approvalId: string,
): Promise<LockedPreparedActionGraph> {
  const hint = (await client.query<{
    action_id: string;
    task_id: string;
    run_id: string;
    tool_call_id: string;
  }>(
    `SELECT action.action_id,action.task_id,action.run_id,action.tool_call_id
    FROM prepared_actions action
    JOIN approvals approval ON approval.approval_id=action.approval_id
      AND approval.approval_kind='prepared_action'
    WHERE approval.approval_id=$1 AND action.workspace_id=$2`,
    [approvalId, workspaceId],
  )).rows[0];
  if (!hint) return hiddenApproval();
  await client.query("SELECT pg_advisory_xact_lock(hashtext($1))", [
    `agentops-prepared-action-decision:${workspaceId}:${hint.action_id}`,
  ]);
  const task = (await client.query<TaskRow>(
    `SELECT task_id,workspace_id,owner_agent_id,status,updated_at
    FROM tasks WHERE task_id=$1 AND workspace_id=$2 FOR UPDATE`,
    [hint.task_id, workspaceId],
  )).rows[0];
  const run = (await client.query<RunRow>(
    `SELECT run_id,workspace_id,task_id,agent_id,runtime_type,model_provider,
      status,approval_required,agent_plan_id,plan_hash
    FROM runs WHERE run_id=$1 AND workspace_id=$2 FOR UPDATE`,
    [hint.run_id, workspaceId],
  )).rows[0];
  const approval = (await client.query<ApprovalRow>(
    `SELECT approval_id,approval_kind,task_id,run_id,tool_call_id,
      requested_by_agent_id,approver_user_id,decision,expires_at,created_at,
      decided_at
    FROM approvals WHERE approval_id=$1 FOR UPDATE`,
    [approvalId],
  )).rows[0];
  const action = (await client.query<PreparedActionDecisionRow>(
    `SELECT action_id,workspace_id,task_id,run_id,tool_call_id,approval_id,
      requested_by_agent_id,action_type,normalized_args_json,target_resource,
      risk_level,policy_version,checkpoint_json,action_hash,idempotency_key,
      status,provider_side_effect_id,result_summary,created_at,approved_at,
      consumed_at,expires_at
    FROM prepared_actions WHERE action_id=$1 FOR UPDATE`,
    [hint.action_id],
  )).rows[0];
  const tool = (await client.query<PreparedActionToolRow>(
    `SELECT tool_call_id,run_id,agent_id,tool_name,status,side_effect_id,ended_at
    FROM tool_calls WHERE tool_call_id=$1 FOR UPDATE`,
    [hint.tool_call_id],
  )).rows[0];
  if (!task || !run || !approval || !action || !tool) {
    throw new ControlPlaneHttpError(
      409,
      "prepared_action_binding_invalid",
      "PreparedAction authority graph is incomplete.",
    );
  }
  if (
    approval.approval_kind !== "prepared_action"
    || approval.task_id !== task.task_id
    || approval.run_id !== run.run_id
    || approval.tool_call_id !== tool.tool_call_id
    || approval.requested_by_agent_id !== run.agent_id
    || action.workspace_id !== workspaceId
    || action.task_id !== task.task_id
    || action.run_id !== run.run_id
    || action.tool_call_id !== tool.tool_call_id
    || action.approval_id !== approval.approval_id
    || action.requested_by_agent_id !== run.agent_id
    || run.task_id !== task.task_id
    || task.owner_agent_id !== run.agent_id
    || tool.run_id !== run.run_id
    || tool.agent_id !== run.agent_id
    || tool.tool_name !== action.action_type
  ) {
    throw new ControlPlaneHttpError(
      409,
      "prepared_action_binding_invalid",
      "PreparedAction workspace, task, run, tool, Agent, or approval binding is invalid.",
    );
  }
  return { task, run, approval, action, tool };
}

function preparedActionDecisionResponse(
  graph: LockedPreparedActionGraph,
  outcome: "updated" | "unchanged",
) {
  return {
    status: 200,
    body: {
      ok: true,
      provider: "agentops-human-approval-decision",
      control_plane: "typescript_postgres",
      operation: "prepared_action_approval_decision",
      outcome,
      decision: graph.approval.decision,
      approval: publicApproval(graph.approval),
      prepared_action: publicPreparedAction(graph.action),
      hash_verification: {
        stored_action_hash: graph.action.action_hash,
        current_action_hash: preparedActionHash(graph.action),
        match: graph.action.action_hash === preparedActionHash(graph.action),
      },
      side_effect_performed: false,
      resume_required: graph.approval.decision === "approved",
      credentials_omitted: true,
      raw_body_omitted: true,
      token_omitted: true,
    },
  };
}

async function decidePreparedAction(
  client: PoolClient,
  identity: HumanSessionIdentity,
  approvalId: string,
  decision: ApprovalDecision,
  idempotencyHash: string,
) {
  const graph = await lockPreparedActionGraph(
    client,
    identity.workspaceId,
    approvalId,
  );
  const currentActionHash = preparedActionHash(graph.action);
  const requestHash = stableHash({
    contract: "prepared_action_human_decision_v1",
    workspace_id: identity.workspaceId,
    user_id: identity.userId,
    approval_id: approvalId,
    action_id: graph.action.action_id,
    action_hash: graph.action.action_hash,
    decision,
  });
  const existing = (await client.query<DecisionRequestRow>(
    `SELECT workspace_id,user_id,idempotency_key_hash,request_hash,
      approval_id,decision,status,created_at,completed_at
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
      graph.approval.decision !== decision
      || graph.approval.approver_user_id !== identity.userId
      || graph.action.status !== (
        decision === "approved" ? "approved" : "rejected"
      )
    ) {
      throw new ControlPlaneHttpError(
        409,
        "approval_replay_state_conflict",
        "PreparedAction approval replay state is unavailable.",
      );
    }
    return preparedActionDecisionResponse(graph, "unchanged");
  }
  if (
    graph.approval.decision !== "pending"
    || graph.action.status !== "prepared"
  ) {
    throw new ControlPlaneHttpError(
      409,
      "approval_decision_conflict",
      "PreparedAction approval already has a terminal decision.",
    );
  }
  if (
    graph.action.action_hash !== currentActionHash
    || graph.action.provider_side_effect_id
    || graph.action.consumed_at
  ) {
    throw new ControlPlaneHttpError(
      409,
      "action_hash_mismatch",
      "PreparedAction changed after preparation; create a new action.",
    );
  }
  if (
    !["waiting_approval", "running"].includes(graph.task.status)
    || !["waiting_approval", "running"].includes(graph.run.status)
    || graph.tool.status !== "waiting_approval"
    || graph.tool.side_effect_id
    || graph.tool.ended_at
  ) {
    throw new ControlPlaneHttpError(
      409,
      "prepared_action_parent_state_invalid",
      "PreparedAction parent state is no longer awaiting Human review.",
    );
  }
  if (
    decision === "approved"
    && (
      approvalExpired(graph.approval)
      || approvalExpired({
        ...graph.approval,
        expires_at: graph.action.expires_at,
      })
    )
  ) {
    throw new ControlPlaneHttpError(
      409,
      "approval_expired",
      "Expired PreparedAction approval cannot authorize execution.",
    );
  }

  const now = new Date().toISOString();
  const approval = (await client.query<ApprovalRow>(
    `UPDATE approvals SET decision=$1,approver_user_id=$2,decided_at=$3
    WHERE approval_id=$4 AND approval_kind='prepared_action'
      AND decision='pending'
    RETURNING approval_id,approval_kind,task_id,run_id,tool_call_id,
      requested_by_agent_id,approver_user_id,decision,expires_at,created_at,
      decided_at`,
    [decision, identity.userId, now, approvalId],
  )).rows[0];
  const action = (await client.query<PreparedActionDecisionRow>(
    `UPDATE prepared_actions
    SET status=$1,approved_at=$2,result_summary=$3
    WHERE action_id=$4 AND approval_id=$5 AND status='prepared'
    RETURNING action_id,workspace_id,task_id,run_id,tool_call_id,approval_id,
      requested_by_agent_id,action_type,normalized_args_json,target_resource,
      risk_level,policy_version,checkpoint_json,action_hash,idempotency_key,
      status,provider_side_effect_id,result_summary,created_at,approved_at,
      consumed_at,expires_at`,
    [
      decision === "approved" ? "approved" : "rejected",
      decision === "approved" ? now : null,
      decision === "rejected"
        ? "Human reviewer rejected PreparedAction execution."
        : null,
      graph.action.action_id,
      approvalId,
    ],
  )).rows[0];
  if (!approval || !action) {
    throw new ControlPlaneHttpError(
      409,
      "approval_decision_conflict",
      "PreparedAction approval lost its single-winner transition.",
    );
  }
  let task = graph.task;
  let run = graph.run;
  let tool = graph.tool;
  const terminalCost = decision === "rejected"
    ? await settleExistingTerminalRunCost(client, {
      workspaceId: identity.workspaceId,
      runId: graph.run.run_id,
      terminalStatus: "blocked",
    })
    : null;
  if (decision === "rejected") {
    task = (await client.query<TaskRow>(
      `UPDATE tasks SET status='blocked',updated_at=$1
      WHERE task_id=$2 AND workspace_id=$3
        AND status IN ('waiting_approval','running')
      RETURNING task_id,workspace_id,owner_agent_id,status,updated_at`,
      [now, graph.task.task_id, identity.workspaceId],
    )).rows[0];
    run = (await client.query<RunRow>(
      `UPDATE runs SET status='blocked',approval_required=0
      WHERE run_id=$1 AND workspace_id=$2
        AND status IN ('waiting_approval','running')
      RETURNING run_id,workspace_id,task_id,agent_id,runtime_type,model_provider,
        status,approval_required,agent_plan_id,plan_hash`,
      [graph.run.run_id, identity.workspaceId],
    )).rows[0];
    tool = (await client.query<PreparedActionToolRow>(
      `UPDATE tool_calls SET status='blocked',
        result_summary='Human reviewer rejected PreparedAction execution.',
        ended_at=$1
      WHERE tool_call_id=$2 AND status='waiting_approval'
        AND side_effect_id IS NULL
      RETURNING tool_call_id,run_id,agent_id,tool_name,status,side_effect_id,
        ended_at`,
      [now, graph.tool.tool_call_id],
    )).rows[0];
    if (!task || !run || !tool) {
      throw new ControlPlaneHttpError(
        409,
        "approval_linked_state_conflict",
        "PreparedAction parent state changed before rejection completed.",
      );
    }
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
  await appendAudit(client, {
    workspaceId: identity.workspaceId,
    actorType: "user",
    actorId: identity.userId,
    action: `approval.prepared_action.${decision}`,
    entityType: "approvals",
    entityId: approvalId,
    before: approvalSnapshot(graph.approval),
    after: approvalSnapshot(approval),
    metadata: {
      session_ref: identity.sessionRef,
      membership_role: identity.membershipRole,
      action_id: action.action_id,
      action_hash: action.action_hash,
      request_hash: requestHash,
      idempotency_key_hash: idempotencyHash,
      run_cost_closure: terminalCost?.mode || null,
      run_cost_reservation_state: terminalCost?.reservation?.state || null,
      side_effect_performed: false,
      raw_body_omitted: true,
      token_omitted: true,
    },
  });
  await appendAudit(client, {
    workspaceId: identity.workspaceId,
    actorType: "user",
    actorId: identity.userId,
    action: `approval_wall.prepared_action_${decision}`,
    entityType: "prepared_actions",
    entityId: action.action_id,
    before: publicPreparedAction(graph.action),
    after: publicPreparedAction(action),
    metadata: {
      approval_id: approvalId,
      task_id: task.task_id,
      run_id: run.run_id,
      tool_call_id: tool.tool_call_id,
      request_hash: requestHash,
      run_cost_closure: terminalCost?.mode || null,
      run_cost_reservation_state: terminalCost?.reservation?.state || null,
      side_effect_performed: false,
      token_omitted: true,
    },
  });
  await appendRuntimeEvent(client, {
    workspaceId: identity.workspaceId,
    eventType: `approval.prepared_action.${decision}`,
    status: decision === "approved" ? "waiting_approval" : "blocked",
    runId: run.run_id,
    taskId: task.task_id,
    agentId: run.agent_id,
    outputSummary:
      `Human reviewer marked PreparedAction ${decision}; no side effect executed.`,
    rawPayloadHash: requestHash,
  });
  return preparedActionDecisionResponse(
    { approval, action, task, run, tool },
    "updated",
  );
}

async function deliveryRequestBinding(
  client: PoolClient,
  graph: LockedDeliveryGraph,
): Promise<DeliveryRequestBinding> {
  const rows = (await client.query<{
    metadata_json: string;
    tamper_chain_hash: string | null;
  }>(
    `SELECT metadata_json,tamper_chain_hash
    FROM audit_logs
    WHERE workspace_id=$1
      AND actor_type='agent'
      AND actor_id=$2
      AND action='agent_gateway.customer_delivery_approval_request'
      AND entity_type='approvals'
      AND entity_id=$3
    ORDER BY created_at,audit_id
    LIMIT 2 FOR SHARE`,
    [
      graph.task.workspace_id,
      graph.run.agent_id,
      graph.approval.approval_id,
    ],
  )).rows;
  if (rows.length !== 1 || !/^[a-f0-9]{64}$/.test(
    String(rows[0].tamper_chain_hash || ""),
  )) {
    throw new ControlPlaneHttpError(
      409,
      "customer_delivery_request_evidence_binding_invalid",
      "Customer-delivery request evidence binding is missing or ambiguous.",
    );
  }
  const metadata = jsonObject(rows[0].metadata_json);
  const manifestId = String(metadata.manifest_id || "");
  const planId = String(metadata.plan_id || "");
  const planVersion = Number(metadata.plan_version);
  const planHash = String(metadata.plan_hash || "");
  const verificationResultHash = String(
    metadata.verification_result_hash || "",
  );
  if (
    metadata.workspace_id !== graph.task.workspace_id
    || metadata.task_id !== graph.task.task_id
    || metadata.run_id !== graph.run.run_id
    || !/^[A-Za-z0-9._:-]{1,128}$/.test(manifestId)
    || !/^[A-Za-z0-9._:-]{1,128}$/.test(planId)
    || !Number.isInteger(planVersion)
    || planVersion < 1
    || !/^[a-f0-9]{64}$/.test(planHash)
    || !/^[a-f0-9]{64}$/.test(verificationResultHash)
  ) {
    throw new ControlPlaneHttpError(
      409,
      "customer_delivery_request_evidence_binding_invalid",
      "Customer-delivery request evidence binding is invalid.",
    );
  }
  return {
    manifestId,
    planId,
    planVersion,
    planHash,
    verificationResultHash,
    bindingHash: stableHash({
      approval_id: graph.approval.approval_id,
      workspace_id: graph.task.workspace_id,
      task_id: graph.task.task_id,
      run_id: graph.run.run_id,
      agent_id: graph.run.agent_id,
      manifest_id: manifestId,
      plan_id: planId,
      plan_version: planVersion,
      plan_hash: planHash,
      verification_result_hash: verificationResultHash,
      request_audit_chain_hash: rows[0].tamper_chain_hash,
    }),
  };
}

async function currentEvidence(
  client: PoolClient,
  identity: HumanSessionIdentity,
  graph: LockedDeliveryGraph,
) {
  return verifyCurrentCustomerDeliveryPlanEvidence(
    client,
    identity.workspaceId,
    graph.task.task_id,
    graph.run,
    graph.run.agent_id,
  );
}

function response(
  graph: LockedDeliveryGraph,
  evidence: Awaited<ReturnType<typeof currentEvidence>>,
  outcome: "updated" | "unchanged",
) {
  return {
    status: 200,
    body: {
      ok: true,
      provider: "agentops-human-approval-decision",
      control_plane: "typescript_postgres",
      operation: "customer_delivery_approval_decision",
      outcome,
      decision: graph.approval.decision,
      approval: publicApproval(graph.approval),
      linked_state: {
        workspace_id: graph.task.workspace_id,
        task_id: graph.task.task_id,
        task_status: graph.task.status,
        run_id: graph.run.run_id,
        run_status: graph.run.status,
        agent_plan_id: graph.run.agent_plan_id,
        run_plan_hash: graph.run.plan_hash,
      },
      plan_evidence: evidence,
      credentials_omitted: true,
      raw_body_omitted: true,
      token_omitted: true,
    },
  };
}

async function transitionPendingDecision(
  client: PoolClient,
  identity: HumanSessionIdentity,
  graph: LockedDeliveryGraph,
  decision: ApprovalDecision,
  evidence: Awaited<ReturnType<typeof currentEvidence>>,
  requestBinding: DeliveryRequestBinding,
  requestHash: string,
  idempotencyHash: string,
) {
  if (graph.approval.decision !== "pending") {
    throw new ControlPlaneHttpError(
      409,
      "approval_decision_conflict",
      "A terminal approval cannot be changed or replayed with another request identity.",
    );
  }
  if (
    identity.userId === graph.run.agent_id
    || identity.userId === graph.approval.requested_by_agent_id
  ) {
    throw new ControlPlaneHttpError(
      403,
      "agent_self_approval_forbidden",
      "An Agent cannot approve its own customer delivery.",
    );
  }
  if (
    graph.run.status !== "completed"
    || graph.task.status !== "waiting_approval"
  ) {
    throw new ControlPlaneHttpError(
      409,
      "customer_delivery_run_incomplete",
      "Customer delivery requires a completed run and a task waiting for review.",
    );
  }
  if (decision === "approved" && approvalExpired(graph.approval)) {
    throw new ControlPlaneHttpError(
      409,
      "approval_expired",
      "Expired approval cannot authorize customer delivery.",
    );
  }
  if (decision === "approved" && (!evidence.pass || !evidence.verification_pass)) {
    throw new ControlPlaneHttpError(
      409,
      "verified_plan_evidence_manifest_required",
      "Customer delivery requires current verified Hermes or OpenClaw plan evidence.",
    );
  }
  if (
    decision === "approved"
    && (
      graph.run.agent_plan_id !== requestBinding.planId
      || graph.run.plan_hash !== requestBinding.planHash
      || evidence.manifest_id !== requestBinding.manifestId
      || evidence.plan_id !== requestBinding.planId
      || evidence.plan_version !== requestBinding.planVersion
      || evidence.plan_hash !== requestBinding.planHash
      || evidence.verification_result_hash
        !== requestBinding.verificationResultHash
    )
  ) {
    throw new ControlPlaneHttpError(
      409,
      "customer_delivery_request_evidence_changed",
      "Customer-delivery evidence changed after the Agent requested Human review.",
    );
  }

  const now = new Date().toISOString();
  const approval = (await client.query<ApprovalRow>(
    `UPDATE approvals
    SET decision=$1,approver_user_id=$2,decided_at=$3
    WHERE approval_id=$4 AND task_id=$5 AND run_id=$6
      AND approval_kind='customer_delivery' AND decision='pending'
    RETURNING approval_id,approval_kind,task_id,run_id,tool_call_id,
      requested_by_agent_id,approver_user_id,decision,expires_at,created_at,
      decided_at`,
    [
      decision,
      identity.userId,
      now,
      graph.approval.approval_id,
      graph.task.task_id,
      graph.run.run_id,
    ],
  )).rows[0];
  if (!approval) {
    throw new ControlPlaneHttpError(
      409,
      "approval_decision_conflict",
      "Approval lost its single-winner transition.",
    );
  }

  const task = (await client.query<TaskRow>(
    `UPDATE tasks SET status=$1,updated_at=$2
    WHERE task_id=$3 AND workspace_id=$4 AND status='waiting_approval'
    RETURNING task_id,workspace_id,owner_agent_id,status,updated_at`,
    [
      decision === "approved" ? "completed" : "blocked",
      now,
      graph.task.task_id,
      identity.workspaceId,
    ],
  )).rows[0];
  if (!task) {
    throw new ControlPlaneHttpError(
      409,
      "approval_linked_state_conflict",
      "The customer-delivery task is no longer waiting for this decision.",
    );
  }
  const run = (await client.query<RunRow>(
    `UPDATE runs SET approval_required=0
    WHERE run_id=$1 AND task_id=$2 AND workspace_id=$3 AND status='completed'
    RETURNING run_id,workspace_id,task_id,agent_id,runtime_type,model_provider,
      status,approval_required,agent_plan_id,plan_hash`,
    [graph.run.run_id, graph.task.task_id, identity.workspaceId],
  )).rows[0];
  if (!run) {
    throw new ControlPlaneHttpError(
      409,
      "approval_linked_state_conflict",
      "The customer-delivery run is no longer completed.",
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
      approval.approval_id,
      decision,
      now,
    ],
  );
  const evidenceBindingHash = stableHash({
    manifest_id: evidence.manifest_id,
    plan_id: evidence.plan_id,
    plan_version: evidence.plan_version,
    plan_hash: evidence.plan_hash,
    verification_result_hash: evidence.verification_result_hash,
    verification_pass: evidence.verification_pass,
    failed_checks: evidence.failed_checks,
  });
  await appendAudit(client, {
    workspaceId: identity.workspaceId,
    actorType: "user",
    actorId: identity.userId,
    action: `approval.customer_delivery.${decision}`,
    entityType: "approvals",
    entityId: approval.approval_id,
    before: approvalSnapshot(graph.approval),
    after: approvalSnapshot(approval),
    metadata: {
      session_ref: identity.sessionRef,
      membership_role: identity.membershipRole,
      task_id: task.task_id,
      run_id: run.run_id,
      agent_id: run.agent_id,
      agent_plan_id: run.agent_plan_id,
      run_plan_hash: run.plan_hash,
      manifest_id: evidence.manifest_id,
      plan_id: evidence.plan_id,
      plan_version: evidence.plan_version,
      plan_hash: evidence.plan_hash,
      verification_result_hash: evidence.verification_result_hash,
      evidence_binding_hash: evidenceBindingHash,
      request_evidence_binding_hash: requestBinding.bindingHash,
      request_manifest_id: requestBinding.manifestId,
      request_plan_id: requestBinding.planId,
      request_plan_version: requestBinding.planVersion,
      request_plan_hash: requestBinding.planHash,
      request_verification_result_hash: requestBinding.verificationResultHash,
      request_hash: requestHash,
      idempotency_key_hash: idempotencyHash,
      credentials_omitted: true,
      raw_body_omitted: true,
      token_omitted: true,
    },
  });
  await appendAudit(client, {
    workspaceId: identity.workspaceId,
    actorType: "user",
    actorId: identity.userId,
    action: `task.customer_delivery.${decision}`,
    entityType: "tasks",
    entityId: task.task_id,
    before: taskSnapshot(graph.task),
    after: taskSnapshot(task),
    metadata: {
      approval_id: approval.approval_id,
      run_id: run.run_id,
      evidence_binding_hash: evidenceBindingHash,
      request_evidence_binding_hash: requestBinding.bindingHash,
      token_omitted: true,
    },
  });
  await appendRuntimeEvent(client, {
    eventType: `approval.customer_delivery.${decision}`,
    status: "completed",
    runId: run.run_id,
    taskId: task.task_id,
    agentId: run.agent_id,
    outputSummary: `Human reviewer marked customer delivery ${decision}.`,
    rawPayloadHash: requestHash,
  });
  return response({ approval, task, run }, evidence, "updated");
}

export async function decideWorkspaceApproval(
  request: Request,
  body: Record<string, unknown>,
  rawApprovalId: unknown,
  rawDecision: unknown,
) {
  const approvalId = identifier(rawApprovalId, "approval_id");
  const decision = requestedDecision(rawDecision);
  const replayKey = idempotencyKey(request.headers);
  rejectMachineCredentials(request.headers);
  validateWriteOrigin(request.headers);
  validateDecisionBody(body);
  return withPostgresTransaction(async (client) => {
    const identity = await authenticateHumanReviewer(
      client,
      request.headers,
      body.workspace_id,
    );
    const idempotencyHash = stableHash({
      workspace_id: identity.workspaceId,
      user_id: identity.userId,
      idempotency_key: replayKey,
    });
    await client.query("SELECT pg_advisory_xact_lock(hashtext($1))", [
      `agentops-human-approval-idempotency:${identity.workspaceId}:${identity.userId}:${idempotencyHash}`,
    ]);
    const approvalKind = await approvalKindInWorkspace(
      client,
      identity.workspaceId,
      approvalId,
    );
    if (approvalKind === "prepared_action") {
      return decidePreparedAction(
        client,
        identity,
        approvalId,
        decision,
        idempotencyHash,
      );
    }
    if (approvalKind === "agent_enrollment") {
      return decideGatewayEnrollmentApproval(
        client,
        identity,
        approvalId,
        decision,
        idempotencyHash,
      );
    }
    const graph = await lockCustomerDeliveryGraph(
      client,
      identity.workspaceId,
      approvalId,
    );
    const evidence = await currentEvidence(client, identity, graph);
    const requestBinding = await deliveryRequestBinding(client, graph);
    const requestHash = stableHash({
      workspace_id: identity.workspaceId,
      user_id: identity.userId,
      approval_id: approvalId,
      decision,
      request_evidence_binding_hash: requestBinding.bindingHash,
      current_evidence_binding_hash: stableHash({
        manifest_id: evidence.manifest_id,
        plan_id: evidence.plan_id,
        plan_version: evidence.plan_version,
        plan_hash: evidence.plan_hash,
        verification_result_hash: evidence.verification_result_hash,
        verification_pass: evidence.verification_pass,
        failed_checks: evidence.failed_checks,
      }),
    });
    const existing = (await client.query<DecisionRequestRow>(
      `SELECT workspace_id,user_id,idempotency_key_hash,request_hash,
        approval_id,decision,status,created_at,completed_at
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
        graph.approval.decision !== decision
        || graph.approval.approver_user_id !== identity.userId
      ) {
        throw new ControlPlaneHttpError(
          409,
          "approval_replay_state_conflict",
          "Approval replay state is unavailable.",
        );
      }
      return response(graph, evidence, "unchanged");
    }
    return transitionPendingDecision(
      client,
      identity,
      graph,
      decision,
      evidence,
      requestBinding,
      requestHash,
      idempotencyHash,
    );
  });
}

export async function readWorkspaceApprovalReceipt(
  request: Request,
  rawApprovalId: unknown,
) {
  const approvalId = identifier(rawApprovalId, "approval_id");
  rejectMachineCredentials(request.headers);
  const requestedWorkspace = new URL(request.url).searchParams.get("workspace_id");
  return withPostgresTransaction(async (client) => {
    const identity = await authenticateHumanMember(
      client,
      request.headers,
      requestedWorkspace,
    );
    const graph = await lockCustomerDeliveryGraph(
      client,
      identity.workspaceId,
      approvalId,
    );
    const evidence = await currentEvidence(client, identity, graph);
    const receipt = graph.approval.approver_user_id
      ? (await client.query<DecisionRequestRow>(
        `SELECT workspace_id,user_id,idempotency_key_hash,request_hash,
          approval_id,decision,status,created_at,completed_at
        FROM human_approval_decision_requests
        WHERE workspace_id=$1 AND approval_id=$2
          AND user_id=$3 AND status='completed'
        ORDER BY completed_at DESC,idempotency_key_hash DESC LIMIT 1`,
        [
          identity.workspaceId,
          approvalId,
          graph.approval.approver_user_id,
        ],
      )).rows[0]
      : undefined;
    return {
      status: 200,
      body: {
        ...response(graph, evidence, "unchanged").body,
        operation: "customer_delivery_approval_receipt_read",
        decision_receipt: receipt
          ? {
            user_id: receipt.user_id,
            decision: receipt.decision,
            status: receipt.status,
            request_hash: receipt.request_hash,
            idempotency_key_hash: receipt.idempotency_key_hash,
            completed_at: receipt.completed_at,
          }
          : null,
      },
    };
  });
}
