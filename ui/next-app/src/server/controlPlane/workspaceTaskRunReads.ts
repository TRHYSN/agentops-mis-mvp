import type { PoolClient } from "pg";

import { withPostgresTransaction } from "./db";
import {
  authenticateHumanMember,
  type HumanSessionIdentity,
} from "./humanSession";
import { ControlPlaneHttpError } from "./http";

const TASK_STATUSES = new Set([
  "backlog",
  "planned",
  "running",
  "waiting_approval",
  "blocked",
  "completed",
  "failed",
  "canceled",
]);
const RUN_STATUSES = new Set([
  "running",
  "completed",
  "failed",
  "blocked",
  "waiting_approval",
]);
const HUMAN_READ_ROLES = new Set([
  "approver",
  "reviewer",
  "workspace-admin",
  "owner",
  "operator",
  "viewer",
]);
const DEFAULT_LIMIT = 100;
const MAX_LIMIT = 200;
const DETAIL_CHILD_LIMIT = 200;

type TaskRow = {
  task_id: string;
  title: string;
  description: string | null;
  requester_id: string | null;
  owner_agent_id: string | null;
  collaborator_agent_ids: string | null;
  status: string;
  priority: string;
  due_date: string | null;
  acceptance_criteria: string | null;
  risk_level: string;
  budget_limit_usd: number;
  created_at: string;
  updated_at: string;
};

type RunRow = {
  run_id: string;
  task_id: string;
  agent_id: string;
  runtime_type: string;
  status: string;
  started_at: string;
  ended_at: string | null;
  duration_ms: number | null;
  input_summary: string | null;
  output_summary: string | null;
  model_provider: string | null;
  model_name: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  reasoning_tokens: number | null;
  cost_usd: number | string | null;
  error_type: string | null;
  error_message: string | null;
  trace_id: string | null;
  parent_run_id: string | null;
  delegation_id: string | null;
  approval_required: number;
  created_at: string;
};

type ToolCallRow = {
  tool_call_id: string;
  run_id: string;
  agent_id: string;
  tool_name: string;
  tool_version: string;
  tool_category: string;
  target_resource: string | null;
  risk_level: string;
  status: string;
  result_summary: string | null;
  started_at: string;
  ended_at: string | null;
  created_at: string;
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

type EvaluationRow = {
  evaluation_id: string;
  task_id: string;
  run_id: string;
  agent_id: string;
  evaluator_type: string;
  score: number;
  pass_fail: string;
  notes: string | null;
  created_at: string;
};

type MemoryRow = {
  memory_id: string;
  scope: string;
  memory_type: string;
  canonical_text: string;
  source_type: string;
  confidence: number;
  review_status: string;
  task_id: string | null;
  agent_id: string | null;
  created_at: string;
  updated_at: string;
};

type ArtifactRow = {
  artifact_id: string;
  artifact_type: string;
  title: string;
  summary: string | null;
  created_at: string;
};

function assertHumanReadRole(identity: HumanSessionIdentity) {
  if (!HUMAN_READ_ROLES.has(identity.membershipRole.trim().toLowerCase())) {
    throw new ControlPlaneHttpError(
      403,
      "human_role_forbidden",
      "The Human Session role cannot read workspace task or run evidence.",
    );
  }
}

function boundedLimit(value: unknown) {
  const normalized = String(value ?? "").trim();
  if (!normalized) return DEFAULT_LIMIT;
  if (!/^[1-9][0-9]{0,2}$/.test(normalized)) {
    throw new ControlPlaneHttpError(
      400,
      "read_limit_invalid",
      `limit must be an integer between 1 and ${MAX_LIMIT}.`,
    );
  }
  const parsed = Number(normalized);
  if (parsed > MAX_LIMIT) {
    throw new ControlPlaneHttpError(
      400,
      "read_limit_invalid",
      `limit must be an integer between 1 and ${MAX_LIMIT}.`,
    );
  }
  return parsed;
}

function boundedOffset(value: unknown) {
  const normalized = String(value ?? "").trim();
  if (!normalized) return 0;
  if (!/^[0-9]{1,4}$/.test(normalized)) {
    throw new ControlPlaneHttpError(
      400,
      "read_offset_invalid",
      "offset must be an integer between 0 and 5000.",
    );
  }
  const parsed = Number(normalized);
  if (parsed > 5000) {
    throw new ControlPlaneHttpError(
      400,
      "read_offset_invalid",
      "offset must be an integer between 0 and 5000.",
    );
  }
  return parsed;
}

function statusFilter(
  values: readonly unknown[],
  allowed: ReadonlySet<string>,
  code: string,
) {
  const statuses = values
    .flatMap((value) => String(value ?? "").split(","))
    .map((value) => value.trim().toLowerCase())
    .filter(Boolean);
  if (statuses.some((status) => !allowed.has(status))) {
    throw new ControlPlaneHttpError(
      400,
      code,
      "One or more status filters are invalid.",
    );
  }
  return [...new Set(statuses)];
}

function ledgerIdentifier(value: unknown, field: "task_id" | "run_id") {
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

function optionalLedgerIdentifier(
  value: unknown,
  field: "task_id" | "agent_id",
) {
  const normalized = String(value ?? "").trim();
  if (!normalized) return null;
  if (!/^[A-Za-z0-9._:-]{1,128}$/.test(normalized)) {
    throw new ControlPlaneHttpError(
      400,
      `${field}_invalid`,
      `${field} must use 1-128 safe identifier characters.`,
    );
  }
  return normalized;
}

function safeText(value: string | null, limit = 4_000) {
  if (value === null) return null;
  return value
    .replace(
      /\b(?:postgres(?:ql)?|mysql|mariadb|mongodb(?:\+srv)?|redis|amqp|mssql):\/\/[^\s"'<>]+/gi,
      "[DSN_REDACTED]",
    )
    .replace(/(bearer\s+)[A-Za-z0-9._~+/-]+=*/gi, "$1[REDACTED]")
    .replace(
      /(token|secret|password|credential|api[_-]?key|dsn)\s*[:=]\s*['"]?[^'"\s,;]+/gi,
      "$1=[REDACTED]",
    )
    .replace(/github_pat_[A-Za-z0-9_]{20,}/g, "[SECRET_REDACTED]")
    .replace(
      /\b(?:sk-[A-Za-z0-9._-]+|ntn_[A-Za-z0-9._-]+|agtok_[A-Za-z0-9_-]+|agtsess_[A-Za-z0-9_-]+)\b/g,
      "[SECRET_REDACTED]",
    )
    .replace(/[\u0000-\u001f\u007f]/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, limit);
}

function collaborators(raw: string | null) {
  try {
    const parsed = JSON.parse(raw || "[]");
    if (!Array.isArray(parsed)) return [];
    return parsed.map((value) => String(value)).filter(Boolean).slice(0, 32);
  } catch {
    return [];
  }
}

function publicTask(row: TaskRow) {
  return {
    task_id: row.task_id,
    title: safeText(row.title, 500) || "",
    description: safeText(row.description),
    requester_id: row.requester_id,
    owner_agent_id: row.owner_agent_id,
    collaborator_agent_ids: collaborators(row.collaborator_agent_ids),
    status: row.status,
    priority: row.priority,
    due_date: row.due_date,
    acceptance_criteria: safeText(row.acceptance_criteria),
    risk_level: row.risk_level,
    budget_limit_usd: Number(row.budget_limit_usd || 0),
    created_at: row.created_at,
    updated_at: row.updated_at,
    raw_content_omitted: true,
    credentials_omitted: true,
    token_omitted: true,
    dsn_omitted: true,
  };
}

function publicRun(row: RunRow) {
  return {
    run_id: row.run_id,
    task_id: row.task_id,
    agent_id: row.agent_id,
    runtime_type: row.runtime_type,
    status: row.status,
    started_at: row.started_at,
    ended_at: row.ended_at,
    duration_ms: Number(row.duration_ms || 0),
    input_summary: safeText(row.input_summary),
    output_summary: safeText(row.output_summary),
    model_provider: safeText(row.model_provider, 200),
    model_name: safeText(row.model_name, 200),
    input_tokens: Number(row.input_tokens || 0),
    output_tokens: Number(row.output_tokens || 0),
    reasoning_tokens: Number(row.reasoning_tokens || 0),
    cost_usd: Number(row.cost_usd || 0),
    error_type: safeText(row.error_type, 200),
    error_message: safeText(row.error_message, 1_000),
    trace_id: safeText(row.trace_id, 500),
    parent_run_id: row.parent_run_id,
    delegation_id: safeText(row.delegation_id, 500),
    approval_required: Boolean(row.approval_required),
    created_at: row.created_at,
    raw_prompt_omitted: true,
    raw_response_omitted: true,
    raw_provider_output_omitted: true,
    credentials_omitted: true,
    token_omitted: true,
    dsn_omitted: true,
  };
}

function publicToolCall(row: ToolCallRow) {
  return {
    tool_call_id: row.tool_call_id,
    run_id: row.run_id,
    agent_id: row.agent_id,
    tool_name: safeText(row.tool_name, 500) || "",
    tool_version: safeText(row.tool_version, 200) || "",
    tool_category: row.tool_category,
    target_resource: safeText(row.target_resource, 1_000),
    risk_level: row.risk_level,
    status: row.status,
    result_summary: safeText(row.result_summary),
    started_at: row.started_at,
    ended_at: row.ended_at,
    created_at: row.created_at,
    normalized_args_omitted: true,
    raw_result_omitted: true,
    credentials_omitted: true,
    token_omitted: true,
    dsn_omitted: true,
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
    expires_at: row.expires_at,
    created_at: row.created_at,
    decided_at: row.decided_at,
    reason_omitted: true,
    normalized_args_omitted: true,
    credentials_omitted: true,
    token_omitted: true,
    dsn_omitted: true,
  };
}

function publicEvaluation(row: EvaluationRow) {
  return {
    evaluation_id: row.evaluation_id,
    task_id: row.task_id,
    run_id: row.run_id,
    agent_id: row.agent_id,
    evaluator_type: row.evaluator_type,
    score: Number(row.score || 0),
    pass_fail: row.pass_fail,
    notes: safeText(row.notes),
    created_at: row.created_at,
    rubric_omitted: true,
    raw_content_omitted: true,
    credentials_omitted: true,
    token_omitted: true,
    dsn_omitted: true,
  };
}

function publicMemory(row: MemoryRow) {
  return {
    memory_id: row.memory_id,
    scope: row.scope,
    memory_type: row.memory_type,
    canonical_text: safeText(row.canonical_text),
    source_type: row.source_type,
    confidence: Number(row.confidence || 0),
    review_status: row.review_status,
    task_id: row.task_id,
    agent_id: row.agent_id,
    created_at: row.created_at,
    updated_at: row.updated_at,
    source_ref_omitted: true,
    raw_content_omitted: true,
    credentials_omitted: true,
    token_omitted: true,
    dsn_omitted: true,
  };
}

function publicArtifact(row: ArtifactRow) {
  return {
    artifact_id: row.artifact_id,
    artifact_type: row.artifact_type,
    title: safeText(row.title, 500) || "",
    summary: safeText(row.summary),
    created_at: row.created_at,
    uri_omitted: true,
    content_hash_omitted: true,
    raw_content_omitted: true,
    credentials_omitted: true,
    token_omitted: true,
    dsn_omitted: true,
  };
}

async function authenticateWorkspaceReader(
  client: PoolClient,
  headers: Headers,
  workspaceId: unknown,
) {
  const identity = await authenticateHumanMember(client, headers, workspaceId);
  assertHumanReadRole(identity);
  return identity;
}

const TASK_COLUMNS = `task.task_id,task.title,task.description,
  CASE WHEN requester_membership.user_id IS NULL
    THEN NULL ELSE task.requester_id END AS requester_id,
  task.owner_agent_id,task.collaborator_agent_ids,task.status,task.priority,
  task.due_date,task.acceptance_criteria,task.risk_level,task.budget_limit_usd,
  task.created_at,task.updated_at`;

const RUN_COLUMNS = `run.run_id,run.task_id,run.agent_id,run.runtime_type,
  run.status,run.started_at,run.ended_at,run.duration_ms,run.input_summary,
  run.output_summary,run.model_provider,run.model_name,run.input_tokens,
  run.output_tokens,run.reasoning_tokens,run.cost_usd,run.error_type,
  run.error_message,run.trace_id,run.parent_run_id,run.delegation_id,
  run.approval_required,run.created_at`;

export async function listWorkspaceTasks(
  headers: Headers,
  workspaceId: unknown,
  rawStatuses: readonly unknown[] = [],
  rawLimit?: unknown,
) {
  return withPostgresTransaction(async (client) => {
    const identity = await authenticateWorkspaceReader(
      client,
      headers,
      workspaceId,
    );
    const statuses = statusFilter(
      rawStatuses,
      TASK_STATUSES,
      "task_status_filter_invalid",
    );
    const limit = boundedLimit(rawLimit);
    const result = await client.query<TaskRow>(
      `SELECT ${TASK_COLUMNS}
      FROM tasks task
      LEFT JOIN workspace_memberships requester_membership
        ON requester_membership.workspace_id=task.workspace_id
        AND requester_membership.user_id=task.requester_id
        AND requester_membership.status='active'
      WHERE task.workspace_id=$1
        AND (cardinality($2::text[])=0 OR task.status=ANY($2::text[]))
      ORDER BY task.updated_at DESC,task.task_id
      LIMIT $3`,
      [identity.workspaceId, statuses, limit],
    );
    return {
      status: 200,
      body: result.rows.map(publicTask),
    };
  });
}

export async function listWorkspaceRuns(
  headers: Headers,
  workspaceId: unknown,
  rawStatuses: readonly unknown[] = [],
  rawLimit?: unknown,
  rawTaskId?: unknown,
  rawAgentId?: unknown,
  rawOffset?: unknown,
) {
  return withPostgresTransaction(async (client) => {
    const identity = await authenticateWorkspaceReader(
      client,
      headers,
      workspaceId,
    );
    const statuses = statusFilter(
      rawStatuses,
      RUN_STATUSES,
      "run_status_filter_invalid",
    );
    const limit = boundedLimit(rawLimit);
    const taskId = optionalLedgerIdentifier(rawTaskId, "task_id");
    const agentId = optionalLedgerIdentifier(rawAgentId, "agent_id");
    const offset = boundedOffset(rawOffset);
    const result = await client.query<RunRow>(
      `SELECT ${RUN_COLUMNS}
      FROM runs run
      JOIN tasks task
        ON task.task_id=run.task_id
        AND task.workspace_id=$1
        AND run.workspace_id=task.workspace_id
      WHERE (cardinality($2::text[])=0 OR run.status=ANY($2::text[]))
        AND ($4::text IS NULL OR run.task_id=$4)
        AND ($5::text IS NULL OR run.agent_id=$5)
      ORDER BY run.created_at DESC,run.run_id
      LIMIT $3 OFFSET $6`,
      [identity.workspaceId, statuses, limit, taskId, agentId, offset],
    );
    return {
      status: 200,
      body: result.rows.map(publicRun),
    };
  });
}

async function taskOrNotFound(
  client: PoolClient,
  workspaceId: string,
  taskId: string,
) {
  const result = await client.query<TaskRow>(
    `SELECT ${TASK_COLUMNS}
    FROM tasks task
    LEFT JOIN workspace_memberships requester_membership
      ON requester_membership.workspace_id=task.workspace_id
      AND requester_membership.user_id=task.requester_id
      AND requester_membership.status='active'
    WHERE task.workspace_id=$1 AND task.task_id=$2`,
    [workspaceId, taskId],
  );
  const task = result.rows[0];
  if (!task) {
    throw new ControlPlaneHttpError(
      404,
      "task_not_found",
      "Task was not found in the Human Session workspace.",
    );
  }
  return task;
}

async function runOrNotFound(
  client: PoolClient,
  workspaceId: string,
  runId: string,
) {
  const result = await client.query<RunRow>(
    `SELECT ${RUN_COLUMNS}
    FROM runs run
    JOIN tasks task
      ON task.task_id=run.task_id
      AND task.workspace_id=$1
      AND run.workspace_id=task.workspace_id
    WHERE run.run_id=$2`,
    [workspaceId, runId],
  );
  const run = result.rows[0];
  if (!run) {
    throw new ControlPlaneHttpError(
      404,
      "run_not_found",
      "Run was not found in the Human Session workspace.",
    );
  }
  return run;
}

async function taskRuns(
  client: PoolClient,
  workspaceId: string,
  taskId: string,
) {
  return (await client.query<RunRow>(
    `SELECT ${RUN_COLUMNS}
    FROM runs run
    JOIN tasks task
      ON task.task_id=run.task_id
      AND task.workspace_id=$1
      AND run.workspace_id=task.workspace_id
    WHERE task.task_id=$2
    ORDER BY run.created_at DESC,run.run_id
    LIMIT $3`,
    [workspaceId, taskId, DETAIL_CHILD_LIMIT],
  )).rows.map(publicRun);
}

async function boundApprovals(
  client: PoolClient,
  workspaceId: string,
  binding: { taskId: string; runId?: string },
) {
  return (await client.query<ApprovalRow>(
    `SELECT approval.approval_id,approval.approval_kind,approval.task_id,
      approval.run_id,approval.tool_call_id,approval.requested_by_agent_id,
      approval.approver_user_id,approval.decision,approval.expires_at,
      approval.created_at,approval.decided_at
    FROM approvals approval
    JOIN tasks task
      ON task.task_id=approval.task_id
      AND task.workspace_id=$1
    JOIN runs run
      ON run.run_id=approval.run_id
      AND run.task_id=task.task_id
      AND run.workspace_id=task.workspace_id
    LEFT JOIN tool_calls tool
      ON tool.tool_call_id=approval.tool_call_id
      AND tool.run_id=run.run_id
      AND tool.agent_id=run.agent_id
    WHERE task.task_id=$2
      AND ($3::text IS NULL OR run.run_id=$3)
      AND (approval.tool_call_id IS NULL OR tool.tool_call_id IS NOT NULL)
    ORDER BY approval.created_at DESC,approval.approval_id
    LIMIT $4`,
    [
      workspaceId,
      binding.taskId,
      binding.runId || null,
      DETAIL_CHILD_LIMIT,
    ],
  )).rows.map(publicApproval);
}

async function boundEvaluations(
  client: PoolClient,
  workspaceId: string,
  binding: { taskId: string; runId?: string },
) {
  return (await client.query<EvaluationRow>(
    `SELECT evaluation.evaluation_id,evaluation.task_id,evaluation.run_id,
      evaluation.agent_id,evaluation.evaluator_type,evaluation.score,
      evaluation.pass_fail,evaluation.notes,evaluation.created_at
    FROM evaluations evaluation
    JOIN tasks task
      ON task.task_id=evaluation.task_id
      AND task.workspace_id=$1
    JOIN runs run
      ON run.run_id=evaluation.run_id
      AND run.task_id=task.task_id
      AND run.workspace_id=task.workspace_id
      AND run.agent_id=evaluation.agent_id
    WHERE task.task_id=$2
      AND ($3::text IS NULL OR run.run_id=$3)
    ORDER BY evaluation.created_at DESC,evaluation.evaluation_id
    LIMIT $4`,
    [
      workspaceId,
      binding.taskId,
      binding.runId || null,
      DETAIL_CHILD_LIMIT,
    ],
  )).rows.map(publicEvaluation);
}

async function taskMemories(
  client: PoolClient,
  workspaceId: string,
  taskId: string,
) {
  return (await client.query<MemoryRow>(
    `SELECT memory.memory_id,memory.scope,memory.memory_type,
      memory.canonical_text,memory.source_type,memory.confidence,
      memory.review_status,memory.task_id,memory.agent_id,memory.created_at,
      memory.updated_at
    FROM memories memory
    JOIN tasks task
      ON task.task_id=memory.task_id
      AND task.workspace_id=$1
      AND memory.workspace_id=task.workspace_id
    LEFT JOIN runs run
      ON run.run_id=memory.run_id
      AND run.task_id=task.task_id
      AND run.workspace_id=task.workspace_id
      AND run.agent_id=memory.agent_id
    WHERE task.task_id=$2
      AND (memory.run_id IS NULL OR run.run_id IS NOT NULL)
    ORDER BY memory.updated_at DESC,memory.memory_id
    LIMIT $3`,
    [workspaceId, taskId, DETAIL_CHILD_LIMIT],
  )).rows.map(publicMemory);
}

async function boundArtifacts(
  client: PoolClient,
  workspaceId: string,
  binding: { taskId: string; runId?: string },
) {
  return (await client.query<ArtifactRow>(
    `SELECT artifact.artifact_id,artifact.artifact_type,artifact.title,
      artifact.summary,artifact.created_at
    FROM artifacts artifact
    JOIN tasks task
      ON task.task_id=artifact.task_id
      AND task.workspace_id=$1
    LEFT JOIN runs run
      ON run.run_id=artifact.run_id
      AND run.task_id=task.task_id
      AND run.workspace_id=task.workspace_id
    WHERE task.task_id=$2
      AND (artifact.run_id IS NULL OR run.run_id IS NOT NULL)
      AND ($3::text IS NULL OR run.run_id=$3)
    ORDER BY artifact.created_at DESC,artifact.artifact_id
    LIMIT $4`,
    [
      workspaceId,
      binding.taskId,
      binding.runId || null,
      DETAIL_CHILD_LIMIT,
    ],
  )).rows.map(publicArtifact);
}

export async function readWorkspaceTaskDetail(
  headers: Headers,
  workspaceId: unknown,
  rawTaskId: unknown,
) {
  return withPostgresTransaction(async (client) => {
    const identity = await authenticateWorkspaceReader(
      client,
      headers,
      workspaceId,
    );
    const taskId = ledgerIdentifier(rawTaskId, "task_id");
    const task = await taskOrNotFound(client, identity.workspaceId, taskId);
    const runs = await taskRuns(client, identity.workspaceId, taskId);
    const binding = { taskId };
    const approvals = await boundApprovals(
      client,
      identity.workspaceId,
      binding,
    );
    const evaluations = await boundEvaluations(
      client,
      identity.workspaceId,
      binding,
    );
    const memories = await taskMemories(
      client,
      identity.workspaceId,
      taskId,
    );
    const artifacts = await boundArtifacts(
      client,
      identity.workspaceId,
      binding,
    );
    return {
      status: 200,
      body: {
        task: publicTask(task),
        runs,
        approvals,
        evaluations,
        memories,
        artifacts,
        evaluation_case_runs: [],
        control_plane: "typescript_postgres",
        provider_call_performed: false,
        python_proxy_performed: false,
        child_limit: DETAIL_CHILD_LIMIT,
        raw_content_omitted: true,
        normalized_args_omitted: true,
        reason_omitted: true,
        credentials_omitted: true,
        token_omitted: true,
        dsn_omitted: true,
      },
    };
  });
}

async function runToolCalls(
  client: PoolClient,
  workspaceId: string,
  runId: string,
) {
  return (await client.query<ToolCallRow>(
    `SELECT tool.tool_call_id,tool.run_id,tool.agent_id,tool.tool_name,
      tool.tool_version,tool.tool_category,tool.target_resource,tool.risk_level,
      tool.status,tool.result_summary,tool.started_at,tool.ended_at,
      tool.created_at
    FROM tool_calls tool
    JOIN runs run
      ON run.run_id=tool.run_id
      AND run.agent_id=tool.agent_id
    JOIN tasks task
      ON task.task_id=run.task_id
      AND task.workspace_id=$1
      AND run.workspace_id=task.workspace_id
    WHERE run.run_id=$2
    ORDER BY tool.created_at,tool.tool_call_id
    LIMIT $3`,
    [workspaceId, runId, DETAIL_CHILD_LIMIT],
  )).rows.map(publicToolCall);
}

export async function readWorkspaceRunDetail(
  headers: Headers,
  workspaceId: unknown,
  rawRunId: unknown,
) {
  return withPostgresTransaction(async (client) => {
    const identity = await authenticateWorkspaceReader(
      client,
      headers,
      workspaceId,
    );
    const runId = ledgerIdentifier(rawRunId, "run_id");
    const run = await runOrNotFound(client, identity.workspaceId, runId);
    const binding = { taskId: run.task_id, runId };
    const toolCalls = await runToolCalls(
      client,
      identity.workspaceId,
      runId,
    );
    const approvals = await boundApprovals(
      client,
      identity.workspaceId,
      binding,
    );
    const evaluations = await boundEvaluations(
      client,
      identity.workspaceId,
      binding,
    );
    const artifacts = await boundArtifacts(
      client,
      identity.workspaceId,
      binding,
    );
    return {
      status: 200,
      body: {
        run: publicRun(run),
        tool_calls: toolCalls,
        approvals,
        evaluations,
        artifacts,
        evaluation_case_runs: [],
        control_plane: "typescript_postgres",
        provider_call_performed: false,
        python_proxy_performed: false,
        child_limit: DETAIL_CHILD_LIMIT,
        raw_content_omitted: true,
        normalized_args_omitted: true,
        reason_omitted: true,
        credentials_omitted: true,
        token_omitted: true,
        dsn_omitted: true,
      },
    };
  });
}
