import type { PoolClient } from "pg";

import {
  authenticateAgentGateway,
  enforceWorkspaceBinding,
  type AgentGatewayIdentity,
} from "./auth";
import { costUsdExact } from "./costProjection";
import { withPostgresTransaction } from "./db";
import { ControlPlaneHttpError } from "./http";

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
  cost_usd: string | null;
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

type ArtifactRow = {
  artifact_id: string;
  task_id: string | null;
  run_id: string | null;
  artifact_type: string;
  title: string;
  summary: string | null;
  created_at: string;
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

type ReadContext = {
  client: PoolClient;
  identity: AgentGatewayIdentity;
  url: URL;
};

const RUN_STATUSES = new Set([
  "running",
  "completed",
  "failed",
  "blocked",
  "waiting_approval",
]);

const TASK_VISIBILITY = `(
  task.owner_agent_id IS NULL
  OR task.owner_agent_id=''
  OR task.owner_agent_id=$2
  OR COALESCE(task.collaborator_agent_ids,'[]')::jsonb ? $2
)`;

const RUN_COLUMNS = `run.run_id,run.task_id,run.agent_id,run.runtime_type,
  run.status,run.started_at,run.ended_at,run.duration_ms,run.input_summary,
  run.output_summary,run.model_provider,run.model_name,run.input_tokens,
  run.output_tokens,run.reasoning_tokens,run.cost_usd,run.error_type,
  run.error_message,run.trace_id,run.parent_run_id,run.delegation_id,
  run.approval_required,run.created_at`;

function safeText(value: string | null, limit = 2_000) {
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
    .replace(
      /\b(?:github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9._-]+|ntn_[A-Za-z0-9._-]+|agtok_[A-Za-z0-9_-]+|agtsess_[A-Za-z0-9_-]+)\b/g,
      "[SECRET_REDACTED]",
    )
    .replace(/[\u0000-\u001f\u007f]/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, limit);
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

function boundedInteger(
  value: unknown,
  field: "limit" | "offset",
  fallback: number,
) {
  const normalized = String(value ?? "").trim();
  if (!normalized) return fallback;
  if (!/^[0-9]{1,4}$/.test(normalized)) {
    throw new ControlPlaneHttpError(
      400,
      `agent_gateway_${field}_invalid`,
      `${field} is outside the allowed range.`,
    );
  }
  const parsed = Number(normalized);
  const maximum = field === "limit" ? 200 : 5000;
  const minimum = field === "limit" ? 1 : 0;
  if (parsed < minimum || parsed > maximum) {
    throw new ControlPlaneHttpError(
      400,
      `agent_gateway_${field}_invalid`,
      `${field} is outside the allowed range.`,
    );
  }
  return parsed;
}

function statuses(url: URL) {
  const requested = url.searchParams
    .getAll("status")
    .flatMap((item) => item.split(","))
    .map((item) => item.trim().toLowerCase())
    .filter(Boolean);
  if (requested.some((status) => !RUN_STATUSES.has(status))) {
    throw new ControlPlaneHttpError(
      400,
      "run_status_filter_invalid",
      "One or more run status filters are invalid.",
    );
  }
  return [...new Set(requested)];
}

function rejectUnknown(url: URL, allowed: readonly string[]) {
  const allowlist = new Set(allowed);
  for (const key of new Set(url.searchParams.keys())) {
    if (!allowlist.has(key)) {
      throw new ControlPlaneHttpError(
        400,
        "agent_gateway_read_query_unsupported",
        "The Agent Gateway read received an unsupported query parameter.",
      );
    }
  }
}

function enforceAgentBinding(identity: AgentGatewayIdentity, url: URL, headers: Headers) {
  for (const requested of [
    headers.get("x-agentops-agent-id"),
    url.searchParams.get("agent_id"),
  ]) {
    if (requested && requested.trim() !== identity.agentId) {
      throw new ControlPlaneHttpError(
        403,
        "forbidden",
        "Agent credential cannot read as another agent.",
      );
    }
  }
}

function gatewayScope(identity: AgentGatewayIdentity) {
  return {
    scope_service: "agent_gateway_scope_v1",
    credential_mode: identity.mode,
    workspace_id: identity.workspaceId,
    agent_id: identity.agentId,
    bound_visibility_enforced: true,
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
    cost_usd_exact: costUsdExact(row.cost_usd),
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
    tool_name: safeText(row.tool_name, 500),
    tool_version: safeText(row.tool_version, 200),
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

function publicArtifact(row: ArtifactRow) {
  return {
    artifact_id: row.artifact_id,
    task_id: row.task_id,
    run_id: row.run_id,
    artifact_type: row.artifact_type,
    title: safeText(row.title, 500),
    summary: safeText(row.summary),
    created_at: row.created_at,
    uri_omitted: true,
    raw_content_omitted: true,
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

async function agentRead(
  request: Request,
  allowedParameters: readonly string[],
  work: (context: ReadContext) => Promise<{
    status: number;
    body: Record<string, unknown>;
  }>,
) {
  const url = new URL(request.url);
  rejectUnknown(url, allowedParameters);
  return withPostgresTransaction(async (client) => {
    const identity = await authenticateAgentGateway(
      client,
      request.headers,
      "tasks:read",
    );
    enforceWorkspaceBinding(identity, {
      header: request.headers.get("x-agentops-workspace-id"),
      query: url.searchParams.get("workspace_id"),
    });
    enforceAgentBinding(identity, url, request.headers);
    return work({ client, identity, url });
  });
}

async function visibleRun(
  client: PoolClient,
  identity: AgentGatewayIdentity,
  runId: string,
) {
  const result = await client.query<RunRow>(
    `SELECT ${RUN_COLUMNS}
    FROM runs run
    JOIN tasks task
      ON task.task_id=run.task_id
      AND task.workspace_id=run.workspace_id
    WHERE run.run_id=$3
      AND run.workspace_id=$1
      AND ${TASK_VISIBILITY}`,
    [identity.workspaceId, identity.agentId, runId],
  );
  const row = result.rows[0];
  if (!row) {
    throw new ControlPlaneHttpError(
      404,
      "run_not_found",
      "Run was not found in the Agent credential scope.",
    );
  }
  return row;
}

export async function listAgentGatewayRuns(request: Request) {
  return agentRead(
    request,
    ["workspace_id", "agent_id", "task_id", "status", "limit", "offset"],
    async ({ client, identity, url }) => {
      const taskId = identifier(
        url.searchParams.get("task_id"),
        "task_id",
        true,
      );
      const runStatuses = statuses(url);
      const limit = boundedInteger(url.searchParams.get("limit"), "limit", 25);
      const offset = boundedInteger(
        url.searchParams.get("offset"),
        "offset",
        0,
      );
      const result = await client.query<RunRow>(
        `SELECT ${RUN_COLUMNS}
        FROM runs run
        JOIN tasks task
          ON task.task_id=run.task_id
          AND task.workspace_id=run.workspace_id
        WHERE run.workspace_id=$1
          AND ${TASK_VISIBILITY}
          AND ($3::text IS NULL OR run.task_id=$3)
          AND (cardinality($4::text[])=0 OR run.status=ANY($4::text[]))
        ORDER BY run.created_at DESC,run.run_id
        LIMIT $5 OFFSET $6`,
        [
          identity.workspaceId,
          identity.agentId,
          taskId,
          runStatuses,
          limit,
          offset,
        ],
      );
      return {
        status: 200,
        body: {
          provider: "agent_gateway",
          control_plane: "typescript_postgres",
          operation: "run_list",
          runs: result.rows.map(publicRun),
          count: result.rows.length,
          workspace_id: identity.workspaceId,
          gateway_scope: gatewayScope(identity),
          token_omitted: true,
        },
      };
    },
  );
}

export async function readAgentGatewayRun(
  request: Request,
  rawRunId: unknown,
) {
  const runId = identifier(rawRunId, "run_id") as string;
  return agentRead(
    request,
    ["workspace_id", "agent_id"],
    async ({ client, identity }) => {
      const run = await visibleRun(client, identity, runId);
      const toolCalls = await client.query<ToolCallRow>(
        `SELECT tool.tool_call_id,tool.run_id,tool.agent_id,tool.tool_name,
          tool.tool_version,tool.tool_category,tool.target_resource,
          tool.risk_level,tool.status,tool.result_summary,tool.started_at,
          tool.ended_at,tool.created_at
        FROM tool_calls tool
        JOIN runs bound_run
          ON bound_run.run_id=tool.run_id
          AND bound_run.agent_id=tool.agent_id
        JOIN tasks bound_task
          ON bound_task.task_id=bound_run.task_id
          AND bound_task.workspace_id=$1
          AND bound_run.workspace_id=bound_task.workspace_id
        WHERE tool.run_id=$2
        ORDER BY tool.created_at,tool.tool_call_id
        LIMIT 200`,
        [identity.workspaceId, runId],
      );
      const artifacts = await client.query<ArtifactRow>(
        `SELECT artifact.artifact_id,artifact.task_id,artifact.run_id,
          artifact.artifact_type,artifact.title,artifact.summary,
          artifact.created_at
        FROM artifacts artifact
        JOIN runs bound_run
          ON bound_run.run_id=artifact.run_id
        JOIN tasks bound_task
          ON bound_task.task_id=artifact.task_id
          AND bound_task.task_id=bound_run.task_id
          AND bound_task.workspace_id=$1
          AND bound_run.workspace_id=bound_task.workspace_id
        WHERE artifact.run_id=$2
        ORDER BY artifact.created_at,artifact.artifact_id
        LIMIT 200`,
        [identity.workspaceId, runId],
      );
      const evaluations = await client.query<EvaluationRow>(
        `SELECT evaluation.evaluation_id,evaluation.task_id,evaluation.run_id,
          evaluation.agent_id,evaluation.evaluator_type,evaluation.score,
          evaluation.pass_fail,evaluation.notes,evaluation.created_at
        FROM evaluations evaluation
        JOIN runs bound_run
          ON bound_run.run_id=evaluation.run_id
          AND bound_run.agent_id=evaluation.agent_id
        JOIN tasks bound_task
          ON bound_task.task_id=evaluation.task_id
          AND bound_task.task_id=bound_run.task_id
          AND bound_task.workspace_id=$1
          AND bound_run.workspace_id=bound_task.workspace_id
        WHERE evaluation.run_id=$2
        ORDER BY evaluation.created_at,evaluation.evaluation_id
        LIMIT 200`,
        [identity.workspaceId, runId],
      );
      return {
        status: 200,
        body: {
          provider: "agent_gateway",
          control_plane: "typescript_postgres",
          operation: "run_get",
          run: publicRun(run),
          tool_calls: toolCalls.rows.map(publicToolCall),
          artifacts: artifacts.rows.map(publicArtifact),
          evaluations: evaluations.rows.map(publicEvaluation),
          workspace_id: identity.workspaceId,
          gateway_scope: gatewayScope(identity),
          token_omitted: true,
        },
      };
    },
  );
}

export async function listAgentGatewayArtifacts(request: Request) {
  return agentRead(
    request,
    [
      "workspace_id",
      "agent_id",
      "task_id",
      "run_id",
      "type",
      "limit",
      "offset",
    ],
    async ({ client, identity, url }) => {
      const taskId = identifier(
        url.searchParams.get("task_id"),
        "task_id",
        true,
      );
      const runId = identifier(
        url.searchParams.get("run_id"),
        "run_id",
        true,
      );
      const artifactType = safeText(
        url.searchParams.get("type"),
        120,
      ) || null;
      const limit = boundedInteger(url.searchParams.get("limit"), "limit", 25);
      const offset = boundedInteger(
        url.searchParams.get("offset"),
        "offset",
        0,
      );
      const result = await client.query<ArtifactRow>(
        `SELECT artifact.artifact_id,artifact.task_id,artifact.run_id,
          artifact.artifact_type,artifact.title,artifact.summary,
          artifact.created_at
        FROM artifacts artifact
        LEFT JOIN runs run ON run.run_id=artifact.run_id
        JOIN tasks task
          ON task.task_id=COALESCE(artifact.task_id,run.task_id)
        WHERE task.workspace_id=$1
          AND (run.run_id IS NULL OR run.workspace_id=task.workspace_id)
          AND ${TASK_VISIBILITY}
          AND ($3::text IS NULL OR task.task_id=$3)
          AND ($4::text IS NULL OR run.run_id=$4)
          AND ($5::text IS NULL OR artifact.artifact_type=$5)
        ORDER BY artifact.created_at DESC,artifact.artifact_id
        LIMIT $6 OFFSET $7`,
        [
          identity.workspaceId,
          identity.agentId,
          taskId,
          runId,
          artifactType,
          limit,
          offset,
        ],
      );
      return {
        status: 200,
        body: {
          provider: "agent_gateway",
          control_plane: "typescript_postgres",
          operation: "artifact_list",
          artifacts: result.rows.map(publicArtifact),
          count: result.rows.length,
          workspace_id: identity.workspaceId,
          gateway_scope: gatewayScope(identity),
          token_omitted: true,
        },
      };
    },
  );
}

async function relatedRuns(
  client: PoolClient,
  identity: AgentGatewayIdentity,
  where: string,
  value: string,
) {
  return (await client.query<RunRow>(
    `SELECT ${RUN_COLUMNS}
    FROM runs run
    JOIN tasks task
      ON task.task_id=run.task_id
      AND task.workspace_id=run.workspace_id
    WHERE run.workspace_id=$1
      AND ${TASK_VISIBILITY}
      AND ${where}=$3
    ORDER BY run.created_at,run.run_id
    LIMIT 200`,
    [identity.workspaceId, identity.agentId, value],
  )).rows.map(publicRun);
}

export async function readAgentGatewayRunGraph(
  request: Request,
  rawRunId: unknown,
) {
  const runId = identifier(rawRunId, "run_id") as string;
  return agentRead(
    request,
    ["workspace_id", "agent_id"],
    async ({ client, identity }) => {
      const run = await visibleRun(client, identity, runId);
      const parent = run.parent_run_id
        ? await relatedRuns(
          client,
          identity,
          "run.run_id",
          run.parent_run_id,
        )
        : [];
      const children = await relatedRuns(
        client,
        identity,
        "run.parent_run_id",
        runId,
      );
      const siblings = run.delegation_id
        ? (await relatedRuns(
          client,
          identity,
          "run.delegation_id",
          run.delegation_id,
        )).filter((item) => item.run_id !== runId)
        : [];
      return {
        status: 200,
        body: {
          provider: "agent_gateway",
          control_plane: "typescript_postgres",
          operation: "run_graph",
          run: publicRun(run),
          parent: parent[0] || null,
          children,
          siblings_by_delegation: siblings,
          workspace_id: identity.workspaceId,
          gateway_scope: gatewayScope(identity),
          raw_content_omitted: true,
          token_omitted: true,
        },
      };
    },
  );
}
