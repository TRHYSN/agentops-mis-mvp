import type { PoolClient } from "pg";

import {
  agentPlanVerificationHash,
  computeAgentPlanHash,
  verifyAgentPlanRow,
  type VerifiableAgentPlan,
} from "./agentPlanContract";
import { authenticateAgentGateway, enforceWorkspaceBinding } from "./auth";
import { boundedJsonObject } from "./boundedJson";
import { withPostgresTransaction } from "./db";
import { ControlPlaneHttpError } from "./http";
import { appendAudit, appendRuntimeEvent, newLedgerId, pythonFloat, stableHash } from "./ledger";

const TOOL_CATEGORIES = new Set(["browser", "github", "file", "shell", "email", "notion", "discord", "database", "mcp", "custom"]);
const RISK_LEVELS = new Set(["low", "medium", "high", "critical"]);
const TOOL_STATUSES = new Set(["planned", "running", "completed", "failed", "blocked", "waiting_approval"]);
const EVALUATOR_TYPES = new Set(["rule", "llm_mock"]);
const TERMINAL_STATUSES = new Set(["completed", "failed", "blocked"]);
const RISKY_TOOLS = new Set(["shell.exec", "github.push", "email.send", "file.delete", "database.write", "dify.knowledge.upload", "openai.file_search.upload"]);
const HIGH_RISK_CATEGORIES = new Set(["shell", "email", "database"]);
const SENSITIVE_KEY = /(authorization|credential|password|secret|token|api[_-]?key|raw[_-]?(prompt|response|transcript|content))/i;
const SAFE_OMISSION_MARKER =
  /^(?:raw_(?:prompt|response|transcript|content)|token)_omitted$/i;
const SHA256_HEX = /^[a-f0-9]{64}$/;
export const EVIDENCE_MAX_BODY_BYTES = 32 * 1024;

type TaskRow = {
  task_id: string;
  workspace_id: string;
  title: string;
  description: string | null;
  requester_id: string | null;
  owner_agent_id: string | null;
  collaborator_agent_ids: string;
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
  workspace_id: string;
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
  input_tokens: number;
  output_tokens: number;
  reasoning_tokens: number;
  cost_usd: number | string;
  error_type: string | null;
  error_message: string | null;
  trace_id: string | null;
  parent_run_id: string | null;
  delegation_id: string | null;
  approval_required: number;
  agent_plan_id: string | null;
  plan_hash: string | null;
  created_at: string;
};

type EvidencePlanRow = VerifiableAgentPlan & {
  plan_id: string;
  workspace_id: string;
  task_id: string | null;
  agent_id: string;
  status: string;
  plan_hash: string | null;
  verified_at: string | null;
  verification_result_hash: string | null;
};

type ToolCallRow = {
  tool_call_id: string;
  run_id: string;
  agent_id: string;
  tool_name: string;
  tool_version: string;
  tool_category: string;
  normalized_args_json: string;
  target_resource: string | null;
  risk_level: string;
  status: string;
  result_summary: string | null;
  side_effect_id: string | null;
  started_at: string;
  ended_at: string | null;
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
  rubric_json: string;
  notes: string | null;
  created_at: string;
};

type ArtifactRow = {
  artifact_id: string;
  task_id: string;
  run_id: string;
  artifact_type: string;
  title: string;
  uri: string | null;
  summary: string | null;
  content_hash: string | null;
  created_at: string;
};

function text(value: unknown, limit: number) {
  return String(value ?? "")
    .replace(/(bearer\s+)[a-z0-9._-]+/gi, "$1[REDACTED]")
    .replace(/(token|secret|password|api[_-]?key)\s*[:=]\s*['\"]?[^'\"\s,;]+/gi, "$1=[REDACTED]")
    .replace(/\b(?:sk-[a-z0-9._-]+|ntn_[a-z0-9._-]+)\b/gi, "[SECRET_REDACTED]")
    .replace(/\b(?:agtok|agtsess)_[A-Za-z0-9_-]+\b/g, "[AGENT_TOKEN_REF_REDACTED]")
    .replace(/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g, "[EMAIL_REDACTED]")
    .replace(/[\u0000-\u001f\u007f]/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, limit);
}

function identifier(value: unknown, field: string) {
  const normalized = String(value ?? "").trim();
  if (!/^[A-Za-z0-9._:-]{1,128}$/.test(normalized)) {
    throw new ControlPlaneHttpError(400, `${field}_invalid`, `${field} must use 1-128 safe identifier characters.`);
  }
  return normalized;
}

function optionalIdentifier(value: unknown, field: string) {
  return value === undefined || value === null || value === "" ? null : identifier(value, field);
}

function choice(value: unknown, allowed: Set<string>, fallback: string) {
  const normalized = String(value ?? "").trim().toLowerCase();
  return allowed.has(normalized) ? normalized : fallback;
}

function iso(value: unknown, field: string) {
  const timestamp = Date.parse(String(value));
  if (!Number.isFinite(timestamp)) {
    throw new ControlPlaneHttpError(400, `${field}_invalid`, `${field} must be an ISO-8601 timestamp.`);
  }
  return new Date(timestamp).toISOString();
}

function score(value: unknown) {
  const parsed = Number(value ?? 1);
  if (!Number.isFinite(parsed)) throw new ControlPlaneHttpError(400, "score_invalid", "score must be a finite number.");
  return Math.max(0, Math.min(1, parsed));
}

function sortedJson(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sortedJson);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>)
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([key, item]) => [key, sortedJson(item)]),
    );
  }
  return value;
}

function safeJson(value: unknown, depth = 0): unknown {
  if (depth >= 8) return "[TRUNCATED]";
  if (Array.isArray(value)) return value.slice(0, 40).map((item) => safeJson(item, depth + 1));
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>)
        .slice(0, 40)
        .map(([rawKey, item]) => {
          const key = text(rawKey, 80);
          const safeOmission = item === true && SAFE_OMISSION_MARKER.test(key);
          return [
            key,
            SENSITIVE_KEY.test(key) && !safeOmission
              ? "[REDACTED]"
              : safeJson(item, depth + 1),
          ];
        }),
    );
  }
  if (typeof value === "number" || typeof value === "boolean" || value === null) return value;
  return text(value, 240);
}

function jsonInput(value: unknown, field: string) {
  if (typeof value !== "string") return value;
  try {
    return JSON.parse(value);
  } catch {
    throw new ControlPlaneHttpError(400, `${field}_invalid`, `${field} must be valid JSON.`);
  }
}

function canonicalSafeJson(value: unknown) {
  return JSON.stringify(sortedJson(safeJson(value)));
}

function stableId(prefix: string, ...parts: unknown[]) {
  return `${prefix}_${stableHash(parts).slice(0, 16)}`;
}

function taskSnapshot(row: TaskRow) {
  return { ...row, budget_limit_usd: pythonFloat(Number(row.budget_limit_usd)) };
}

function runSnapshot(row: RunRow) {
  return { ...row, cost_usd: pythonFloat(Number(row.cost_usd)) };
}

function evaluationSnapshot(row: EvaluationRow) {
  return { ...row, score: pythonFloat(Number(row.score)) };
}

function taskCollaborators(task: TaskRow) {
  try {
    const value: unknown = JSON.parse(task.collaborator_agent_ids || "[]");
    return Array.isArray(value) ? value.map(String) : [];
  } catch {
    return [];
  }
}

async function bodyObject(request: Request) {
  return boundedJsonObject(request, {
    maxBytes: EVIDENCE_MAX_BODY_BYTES,
    label: "Agent Gateway evidence",
  });
}

async function lockEvidenceRun(
  client: PoolClient,
  request: Request,
  body: Record<string, unknown>,
  requiredScope: string,
) {
  const identity = await authenticateAgentGateway(client, request.headers, requiredScope);
  enforceWorkspaceBinding(identity, {
    header: request.headers.get("x-agentops-workspace-id"),
    body: body.workspace_id,
  });
  if (body.agent_id !== undefined && text(body.agent_id, 120) !== identity.agentId) {
    throw new ControlPlaneHttpError(403, "forbidden", "Agent credential cannot write another agent's evidence.");
  }
  const runId = identifier(body.run_id, "run_id");
  const candidateResult = await client.query<Pick<RunRow, "task_id" | "agent_id">>(
    "SELECT task_id,agent_id FROM runs WHERE run_id=$1 AND workspace_id=$2",
    [runId, identity.workspaceId],
  );
  const candidate = candidateResult.rows[0];
  if (!candidate) throw new ControlPlaneHttpError(404, "run_not_found", "Run was not found in the credential workspace.");
  if (candidate.agent_id !== identity.agentId) {
    throw new ControlPlaneHttpError(403, "forbidden", "Run belongs to another agent.");
  }
  if (body.task_id !== undefined && identifier(body.task_id, "task_id") !== candidate.task_id) {
    throw new ControlPlaneHttpError(403, "forbidden", "Evidence task_id must match the target run.");
  }
  const taskResult = await client.query<TaskRow>(
    "SELECT * FROM tasks WHERE task_id=$1 AND workspace_id=$2 FOR UPDATE",
    [candidate.task_id, identity.workspaceId],
  );
  const task = taskResult.rows[0];
  if (!task) throw new ControlPlaneHttpError(404, "task_not_found", "Run task was not found in the credential workspace.");
  if (
    task.owner_agent_id
    && task.owner_agent_id !== identity.agentId
    && !taskCollaborators(task).includes(identity.agentId)
  ) {
    throw new ControlPlaneHttpError(
      403,
      "forbidden",
      "Run task is no longer assigned to the credential agent.",
    );
  }
  await client.query("SELECT pg_advisory_xact_lock(hashtext($1))", [`agentops-run:${runId}`]);
  const runResult = await client.query<RunRow>(
    "SELECT * FROM runs WHERE run_id=$1 AND workspace_id=$2 FOR UPDATE",
    [runId, identity.workspaceId],
  );
  const run = runResult.rows[0];
  if (!run) throw new ControlPlaneHttpError(404, "run_not_found", "Run was not found in the credential workspace.");
  if (run.task_id !== candidate.task_id) {
    throw new ControlPlaneHttpError(409, "run_immutable_binding_conflict", "Run task binding changed while evidence write was waiting.");
  }
  if (run.agent_id !== identity.agentId) throw new ControlPlaneHttpError(403, "forbidden", "Run belongs to another agent.");
  if (run.agent_plan_id) {
    const planResult = await client.query<EvidencePlanRow>(
      "SELECT * FROM agent_plans WHERE plan_id=$1 AND workspace_id=$2 FOR SHARE",
      [run.agent_plan_id, identity.workspaceId],
    );
    const plan = planResult.rows[0];
    const computedPlanHash = plan ? computeAgentPlanHash(plan) : null;
    const verification = plan
      ? verifyAgentPlanRow({ ...plan, plan_hash: computedPlanHash })
      : null;
    if (
      !plan
      || plan.agent_id !== identity.agentId
      || plan.task_id !== run.task_id
      || plan.plan_hash !== computedPlanHash
      || run.plan_hash !== plan.plan_hash
      || !plan.verified_at
      || !plan.verification_result_hash
      || !verification?.pass
      || plan.verification_result_hash
        !== agentPlanVerificationHash(plan.plan_id, verification)
    ) {
      throw new ControlPlaneHttpError(
        409,
        "run_plan_binding_stale",
        "Evidence writes require the run's current verified Agent Plan binding.",
      );
    }
  }
  return { identity, run, task };
}

function response(kind: "tool_call" | "evaluation" | "artifact", row: unknown, outcome: "created" | "updated" | "unchanged") {
  return {
    status: outcome === "created" ? 201 : 200,
    body: {
      ok: true,
      provider: "agentops-mis",
      control_plane: "typescript_postgres",
      operation: `${kind}_record`,
      outcome,
      [kind]: row,
      token_omitted: true,
    },
  };
}

function toolTransitionAllowed(before: string, after: string) {
  if (before === after) return true;
  if (TERMINAL_STATUSES.has(before) || before === "waiting_approval") return false;
  if (before === "planned") return TOOL_STATUSES.has(after);
  return before === "running" && new Set(["completed", "failed", "blocked", "waiting_approval"]).has(after);
}

export async function recordAgentGatewayToolCall(request: Request) {
  const body = await bodyObject(request);
  return withPostgresTransaction(async (client) => {
    const { identity, run, task } = await lockEvidenceRun(client, request, body, "toolcalls:write");
    const toolCallId = body.tool_call_id ? identifier(body.tool_call_id, "tool_call_id") : newLedgerId("tc_gw");
    await client.query("SELECT pg_advisory_xact_lock(hashtext($1))", [`agentops-tool-call:${toolCallId}`]);
    const existingResult = await client.query<ToolCallRow>("SELECT * FROM tool_calls WHERE tool_call_id=$1 FOR UPDATE", [toolCallId]);
    const existing = existingResult.rows[0];

    const toolName = body.tool_name === undefined && existing ? existing.tool_name : text(body.tool_name || "agent_gateway.note", 120);
    const toolVersion = body.tool_version === undefined && existing ? existing.tool_version : text(body.tool_version || "v1", 40);
    const category = body.tool_category === undefined && existing
      ? existing.tool_category
      : choice(body.tool_category, TOOL_CATEGORIES, "custom");
    let risk = body.risk_level === undefined && existing
      ? existing.risk_level
      : choice(body.risk_level, RISK_LEVELS, RISKY_TOOLS.has(toolName) ? "high" : "low");
    if ((RISKY_TOOLS.has(toolName) || HIGH_RISK_CATEGORIES.has(category)) && ["low", "medium"].includes(risk)) risk = "high";
    const rawArgs = body.normalized_args_json !== undefined
      ? jsonInput(body.normalized_args_json, "normalized_args_json")
      : body.args !== undefined
        ? body.args
        : body.args_summary !== undefined
          ? { summary: body.args_summary }
          : undefined;
    const normalizedArgsJson = rawArgs === undefined && existing
      ? existing.normalized_args_json
      : canonicalSafeJson(rawArgs ?? { summary: "redacted" });
    const targetResource = body.target_resource === undefined && existing
      ? existing.target_resource
      : text(body.target_resource, 200) || null;
    const sideEffectId = body.side_effect_id === undefined && existing
      ? existing.side_effect_id
      : optionalIdentifier(body.side_effect_id, "side_effect_id");
    const startedAt = body.started_at === undefined && existing
      ? existing.started_at
      : body.started_at === undefined || body.started_at === null || body.started_at === ""
        ? new Date().toISOString()
        : iso(body.started_at, "started_at");

    if (existing) {
      const immutableConflict = existing.run_id !== run.run_id
        || existing.agent_id !== identity.agentId
        || existing.tool_name !== toolName
        || existing.tool_version !== toolVersion
        || existing.tool_category !== category
        || existing.normalized_args_json !== normalizedArgsJson
        || existing.target_resource !== targetResource
        || existing.risk_level !== risk
        || existing.side_effect_id !== sideEffectId
        || Date.parse(existing.started_at) !== Date.parse(startedAt);
      if (immutableConflict) {
        throw new ControlPlaneHttpError(409, "tool_call_immutable_binding_conflict", "tool_call_id is already bound to different execution evidence.");
      }
    }

    let status = body.status === undefined && existing
      ? existing.status
      : choice(body.status, TOOL_STATUSES, ["low", "medium"].includes(risk) ? "completed" : "waiting_approval");
    if (["high", "critical"].includes(risk) && (!existing || existing.status === "waiting_approval")) status = "waiting_approval";
    if (existing && !toolTransitionAllowed(existing.status, status)) {
      const error = existing.status === "waiting_approval" ? "tool_call_approval_required" : "tool_call_terminal_conflict";
      throw new ControlPlaneHttpError(409, error, `Tool call cannot move from ${existing.status} to ${status} through evidence writeback.`);
    }
    if (status === "waiting_approval" && (TERMINAL_STATUSES.has(run.status) || TERMINAL_STATUSES.has(task.status))) {
      throw new ControlPlaneHttpError(409, "terminal_execution_approval_conflict", "Terminal run/task cannot be moved to waiting approval by tool evidence.");
    }
    const resultSummary = body.result_summary === undefined && existing
      ? existing.result_summary
      : text(body.result_summary, 200) || null;
    let endedAt = body.ended_at === undefined && existing ? existing.ended_at : null;
    if (body.ended_at !== undefined && body.ended_at !== null && body.ended_at !== "") endedAt = iso(body.ended_at, "ended_at");
    if (TERMINAL_STATUSES.has(status) && !endedAt) endedAt = new Date().toISOString();
    const row: ToolCallRow = {
      tool_call_id: toolCallId,
      run_id: run.run_id,
      agent_id: identity.agentId,
      tool_name: toolName,
      tool_version: toolVersion,
      tool_category: category,
      normalized_args_json: normalizedArgsJson,
      target_resource: targetResource,
      risk_level: risk,
      status,
      result_summary: resultSummary,
      side_effect_id: sideEffectId,
      started_at: startedAt,
      ended_at: endedAt,
      created_at: existing?.created_at || new Date().toISOString(),
    };
    const unchanged = Boolean(existing
      && existing.status === row.status
      && existing.result_summary === row.result_summary
      && ((!existing.ended_at && !row.ended_at) || Date.parse(existing.ended_at || "") === Date.parse(row.ended_at || "")));
    if (unchanged) return response("tool_call", existing, "unchanged");

    if (existing) {
      await client.query(
        "UPDATE tool_calls SET status=$1,result_summary=$2,ended_at=$3 WHERE tool_call_id=$4",
        [row.status, row.result_summary, row.ended_at, toolCallId],
      );
    } else {
      await client.query(
        `INSERT INTO tool_calls(tool_call_id,run_id,agent_id,tool_name,tool_version,tool_category,normalized_args_json,target_resource,
          risk_level,status,result_summary,side_effect_id,started_at,ended_at,created_at)
        VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)`,
        [row.tool_call_id, row.run_id, row.agent_id, row.tool_name, row.tool_version, row.tool_category, row.normalized_args_json,
          row.target_resource, row.risk_level, row.status, row.result_summary, row.side_effect_id, row.started_at, row.ended_at, row.created_at],
      );
    }
    await appendAudit(client, {
      actorType: "system",
      actorId: "agent-gateway",
      action: existing ? "tool_call.update" : "tool_call.create",
      entityType: "tool_calls",
      entityId: toolCallId,
      before: existing || undefined,
      after: row,
      metadata: { args_hash: stableHash(JSON.parse(normalizedArgsJson)), workspace_id: identity.workspaceId, raw_omitted: true },
    });
    if (status === "waiting_approval") {
      const now = new Date().toISOString();
      if (run.status !== "waiting_approval" || !run.approval_required) {
        const updatedRunResult = await client.query<RunRow>(
          "UPDATE runs SET approval_required=1,status='waiting_approval' WHERE run_id=$1 AND workspace_id=$2 RETURNING *",
          [run.run_id, identity.workspaceId],
        );
        const updatedRun = updatedRunResult.rows[0];
        await appendAudit(client, {
          actorType: "agent",
          actorId: identity.agentId,
          action: "agent_gateway.tool_call_run_waiting_approval",
          entityType: "runs",
          entityId: run.run_id,
          before: runSnapshot(run),
          after: runSnapshot(updatedRun),
          metadata: { workspace_id: identity.workspaceId, tool_call_id: toolCallId },
        });
      }
      if (task.status !== "waiting_approval") {
        const updatedTaskResult = await client.query<TaskRow>(
          "UPDATE tasks SET status='waiting_approval',updated_at=$1 WHERE task_id=$2 AND workspace_id=$3 RETURNING *",
          [now, task.task_id, identity.workspaceId],
        );
        const updatedTask = updatedTaskResult.rows[0];
        await appendAudit(client, {
          actorType: "agent",
          actorId: identity.agentId,
          action: "agent_gateway.tool_call_task_waiting_approval",
          entityType: "tasks",
          entityId: task.task_id,
          before: taskSnapshot(task),
          after: taskSnapshot(updatedTask),
          metadata: { workspace_id: identity.workspaceId, run_id: run.run_id, tool_call_id: toolCallId },
        });
      }
    }
    await appendRuntimeEvent(client, {
      eventType: "tool_call.record",
      status,
      runId: run.run_id,
      taskId: run.task_id,
      agentId: identity.agentId,
      outputSummary: text(`${toolName}: ${resultSummary || status}`, 200),
      rawPayloadHash: stableHash(JSON.parse(normalizedArgsJson)),
    });
    return response("tool_call", row, existing ? "updated" : "created");
  });
}

export async function submitAgentGatewayEvaluation(request: Request) {
  const body = await bodyObject(request);
  return withPostgresTransaction(async (client) => {
    const { identity, run } = await lockEvidenceRun(client, request, body, "evaluations:submit");
    if (String(body.evaluator_type || "").trim().toLowerCase() === "human") {
      throw new ControlPlaneHttpError(
        403,
        "human_evaluator_forbidden",
        "Agent credentials cannot submit Human evaluation evidence.",
      );
    }
    const evaluatorType = choice(body.evaluator_type, EVALUATOR_TYPES, "rule");
    const evaluationId = body.evaluation_id
      ? identifier(body.evaluation_id, "evaluation_id")
      : stableId("eval_gw", run.run_id, evaluatorType);
    await client.query("SELECT pg_advisory_xact_lock(hashtext($1))", [`agentops-evaluation:${evaluationId}`]);
    const existingResult = await client.query<EvaluationRow>("SELECT * FROM evaluations WHERE evaluation_id=$1 FOR UPDATE", [evaluationId]);
    const existing = existingResult.rows[0];
    const value = score(body.score);
    const passFail = body.pass_fail === "pass" && value >= 0.5 ? "pass" : "fail";
    const rubric = canonicalSafeJson(jsonInput(body.rubric ?? body.rubric_json ?? { submitted_by: "agent_gateway" }, "rubric_json"));
    const row: EvaluationRow = {
      evaluation_id: evaluationId,
      task_id: run.task_id,
      run_id: run.run_id,
      agent_id: identity.agentId,
      evaluator_type: evaluatorType,
      score: value,
      pass_fail: passFail,
      rubric_json: rubric,
      notes: text(body.notes || "Submitted through Agent Gateway.", 260) || null,
      created_at: existing?.created_at || new Date().toISOString(),
    };
    if (existing) {
      const unchanged = existing.task_id === row.task_id
        && existing.run_id === row.run_id
        && existing.agent_id === row.agent_id
        && existing.evaluator_type === row.evaluator_type
        && Number(existing.score) === row.score
        && existing.pass_fail === row.pass_fail
        && existing.rubric_json === row.rubric_json
        && existing.notes === row.notes;
      if (unchanged) return response("evaluation", existing, "unchanged");
      throw new ControlPlaneHttpError(409, "evaluation_immutable_conflict", "evaluation_id is immutable; submit a new ID for revised evidence.");
    }
    await client.query(
      `INSERT INTO evaluations(evaluation_id,task_id,run_id,agent_id,evaluator_type,score,pass_fail,rubric_json,notes,created_at)
      VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)`,
      [row.evaluation_id, row.task_id, row.run_id, row.agent_id, row.evaluator_type, row.score, row.pass_fail, row.rubric_json, row.notes, row.created_at],
    );
    await appendAudit(client, {
      actorType: "system",
      actorId: "agent-gateway",
      action: "evaluation.create",
      entityType: "evaluations",
      entityId: evaluationId,
      after: evaluationSnapshot(row),
      metadata: { workspace_id: identity.workspaceId, raw_payload_omitted: true },
    });
    await appendRuntimeEvent(client, {
      eventType: "evaluation.submit",
      status: passFail,
      runId: run.run_id,
      taskId: run.task_id,
      agentId: identity.agentId,
      outputSummary: row.notes,
    });
    return response("evaluation", row, "created");
  });
}

export async function recordAgentGatewayArtifact(request: Request) {
  const body = await bodyObject(request);
  return withPostgresTransaction(async (client) => {
    const { identity, run } = await lockEvidenceRun(client, request, body, "artifacts:write");
    const artifactType = text(body.artifact_type || "report", 80);
    const title = text(body.title || "Agent Gateway Artifact", 160);
    const artifactId = body.artifact_id
      ? identifier(body.artifact_id, "artifact_id")
      : stableId("art_gw", run.run_id, title || artifactType);
    const contentHash = body.content_hash
      ? String(body.content_hash).trim().toLowerCase()
      : null;
    if (contentHash && !SHA256_HEX.test(contentHash)) {
      throw new ControlPlaneHttpError(
        400,
        "content_hash_invalid",
        "content_hash must be a lowercase SHA-256 digest.",
      );
    }
    await client.query("SELECT pg_advisory_xact_lock(hashtext($1))", [`agentops-artifact:${artifactId}`]);
    const existingResult = await client.query<ArtifactRow>("SELECT * FROM artifacts WHERE artifact_id=$1 FOR UPDATE", [artifactId]);
    const existing = existingResult.rows[0];
    const row: ArtifactRow = {
      artifact_id: artifactId,
      task_id: run.task_id,
      run_id: run.run_id,
      artifact_type: artifactType,
      title,
      uri: text(body.uri || `run://${run.run_id}`, 240) || null,
      summary: text(body.summary || body.content_summary || "Artifact summary recorded through Agent Gateway.", 360) || null,
      content_hash: contentHash,
      created_at: existing?.created_at || (body.created_at ? iso(body.created_at, "created_at") : new Date().toISOString()),
    };
    if (existing) {
      const auditResult = await client.query<{ metadata_json: string }>(
        "SELECT metadata_json FROM audit_logs WHERE entity_type='artifacts' AND entity_id=$1 AND action='agent_gateway.artifact_record' ORDER BY created_at DESC LIMIT 1",
        [artifactId],
      );
      let existingContentHash: string | null = null;
      try {
        existingContentHash = JSON.parse(auditResult.rows[0]?.metadata_json || "{}").content_hash || null;
      } catch {
        existingContentHash = null;
      }
      const unchanged = existing.task_id === row.task_id
        && existing.run_id === row.run_id
        && existing.artifact_type === row.artifact_type
        && existing.title === row.title
        && existing.uri === row.uri
        && existing.summary === row.summary
        && existing.content_hash === row.content_hash
        && (contentHash === null || existingContentHash === contentHash);
      if (unchanged) return response("artifact", existing, "unchanged");
      throw new ControlPlaneHttpError(409, "artifact_immutable_conflict", "artifact_id is immutable; record a new ID for revised evidence.");
    }
    await client.query(
      `INSERT INTO artifacts(
        artifact_id,task_id,run_id,artifact_type,title,uri,summary,content_hash,created_at
      ) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9)`,
      [
        row.artifact_id,
        row.task_id,
        row.run_id,
        row.artifact_type,
        row.title,
        row.uri,
        row.summary,
        row.content_hash,
        row.created_at,
      ],
    );
    await appendRuntimeEvent(client, {
      eventType: "artifact.record",
      status: "completed",
      runId: run.run_id,
      taskId: run.task_id,
      agentId: identity.agentId,
      outputSummary: row.summary,
      rawPayloadHash: contentHash,
    });
    await appendAudit(client, {
      actorType: "agent",
      actorId: identity.agentId,
      action: "agent_gateway.artifact_record",
      entityType: "artifacts",
      entityId: artifactId,
      after: row,
      metadata: { workspace_id: identity.workspaceId, content_hash: contentHash, raw_content_omitted: true },
    });
    return response("artifact", row, "created");
  });
}
