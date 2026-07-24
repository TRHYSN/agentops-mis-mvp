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
import {
  evaluateWorkspaceEntitlement,
  type WorkspaceEntitlementDecision,
} from "./workspaceEntitlements";

const RUNTIME_TYPES = new Set(["mock", "claude_code", "codex", "openhands", "crewai", "langgraph", "openclaw", "hermes"]);
const RUN_STATUSES = new Set(["running", "completed", "failed", "blocked", "waiting_approval"]);
const STARTABLE_TASK_STATUSES = new Set(["planned", "backlog", "running"]);
export const RUN_WRITE_MAX_BODY_BYTES = 16 * 1024;

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

type AgentRow = {
  agent_id: string;
  runtime_type: string;
  model_provider: string | null;
  model_name: string | null;
};

type RunnablePlanRow = VerifiableAgentPlan & {
  plan_id: string;
  status: string;
  plan_hash: string | null;
  verified_at: string | null;
  verification_result_hash: string | null;
  created_at: string;
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
  cost_usd: number;
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

function choice(value: unknown, allowed: Set<string>, fallback: string) {
  const normalized = String(value ?? "").trim().toLowerCase();
  return allowed.has(normalized) ? normalized : fallback;
}

function optionalIso(value: unknown, field: string) {
  if (value === undefined || value === null || value === "") return null;
  const timestamp = Date.parse(String(value));
  if (!Number.isFinite(timestamp)) {
    throw new ControlPlaneHttpError(400, `${field}_invalid`, `${field} must be an ISO-8601 timestamp.`);
  }
  return new Date(timestamp).toISOString();
}

function nonNegativeInteger(value: unknown, field: string) {
  if (value === undefined || value === null || value === "") return 0;
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed) || parsed < 0) {
    throw new ControlPlaneHttpError(400, `${field}_invalid`, `${field} must be a non-negative integer.`);
  }
  return parsed;
}

function nonNegativeNumber(value: unknown, field: string) {
  if (value === undefined || value === null || value === "") return 0;
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed < 0) {
    throw new ControlPlaneHttpError(400, `${field}_invalid`, `${field} must be a non-negative number.`);
  }
  return parsed;
}

function boolean(value: unknown) {
  return value === true || value === 1 || ["1", "true", "yes", "on"].includes(String(value ?? "").trim().toLowerCase());
}

function collaborators(task: TaskRow) {
  try {
    const parsed = JSON.parse(task.collaborator_agent_ids || "[]");
    return Array.isArray(parsed) ? parsed.map((item) => String(item)) : [];
  } catch {
    return [];
  }
}

function runAuditSnapshot(row: RunRow) {
  return { ...row, cost_usd: pythonFloat(Number(row.cost_usd)) };
}

function taskAuditSnapshot(row: TaskRow) {
  return { ...row, budget_limit_usd: pythonFloat(Number(row.budget_limit_usd)) };
}

function defaultDelegationId(taskId: string, agentId: string) {
  const raw = `agent_gateway::${taskId}::${agentId}`;
  const slug = raw.replace(/[^A-Za-z0-9_]+/g, "_").replace(/^_+|_+$/g, "").toLowerCase();
  return slug && slug.length <= 64 ? `del_${slug}` : `del_${stableHash(raw).slice(0, 16)}`;
}

function response(run: RunRow, outcome: "created" | "unchanged", agentPlanId?: string | null) {
  return {
    status: outcome === "created" ? 201 : 200,
    body: {
      ok: true,
      provider: "agentops-mis",
      control_plane: "typescript_postgres",
      operation: "run_start",
      outcome,
      run,
      run_id: run.run_id,
      workspace_id: run.workspace_id,
      ...(agentPlanId ? { agent_plan_id: agentPlanId } : {}),
      token_omitted: true,
    },
  };
}

function entitlementDeniedResponse(decision: WorkspaceEntitlementDecision) {
  return {
    status: 403,
    body: {
      ok: false,
      provider: "agentops-mis",
      control_plane: "typescript_postgres",
      operation: "run_start",
      error: "workspace_entitlement_denied",
      message: "Workspace entitlement does not allow a new Agent Gateway run.",
      reason_code: decision.reason_code,
      workspace_id: decision.workspace_id,
      agent_id: decision.agent_id,
      entitlement_decision: decision,
      token_omitted: true,
      credentials_omitted: true,
      raw_config_omitted: true,
    },
  };
}

function heartbeatResponse(run: RunRow, outcome: "updated" | "unchanged") {
  return {
    status: 200,
    body: {
      ok: true,
      provider: "agentops-mis",
      control_plane: "typescript_postgres",
      operation: "run_heartbeat",
      outcome,
      run,
      run_id: run.run_id,
      workspace_id: run.workspace_id,
      token_omitted: true,
    },
  };
}

function sameTimestamp(left: string | null, right: string | null) {
  if (left === right) return true;
  if (!left || !right) return false;
  return Date.parse(left) === Date.parse(right);
}

function sameHeartbeatState(left: RunRow, right: RunRow) {
  return left.status === right.status
    && sameTimestamp(left.ended_at, right.ended_at)
    && (left.duration_ms === null ? null : Number(left.duration_ms)) === right.duration_ms
    && left.output_summary === right.output_summary
    && left.error_type === right.error_type
    && left.error_message === right.error_message
    && Number(left.output_tokens || 0) === right.output_tokens
    && Number(left.cost_usd || 0) === right.cost_usd;
}

export async function startAgentGatewayRun(request: Request) {
  const body = await boundedJsonObject(request, {
    maxBytes: RUN_WRITE_MAX_BODY_BYTES,
    label: "Run start",
  });

  return withPostgresTransaction(async (client) => {
    const identity = await authenticateAgentGateway(client, request.headers, "runs:write");
    enforceWorkspaceBinding(identity, {
      header: request.headers.get("x-agentops-workspace-id"),
      body: body.workspace_id,
    });
    const requestedAgent = text(body.agent_id || identity.agentId, 120);
    if (requestedAgent !== identity.agentId) {
      throw new ControlPlaneHttpError(403, "forbidden", "Agent credential cannot start another agent's run.");
    }
    const taskId = identifier(body.task_id, "task_id");
    const runId = body.run_id ? identifier(body.run_id, "run_id") : newLedgerId("run_gw");
    const taskResult = await client.query<TaskRow>(
      "SELECT * FROM tasks WHERE task_id=$1 AND workspace_id=$2 FOR UPDATE",
      [taskId, identity.workspaceId],
    );
    const task = taskResult.rows[0];
    if (!task) throw new ControlPlaneHttpError(404, "task_not_found", "Task was not found in the credential workspace.");
    const canAccess = !task.owner_agent_id
      || task.owner_agent_id === identity.agentId
      || collaborators(task).includes(identity.agentId);
    if (!canAccess) {
      throw new ControlPlaneHttpError(403, "forbidden", "Task is assigned to another agent.");
    }
    if (task.status === "running" && task.owner_agent_id && task.owner_agent_id !== identity.agentId) {
      throw new ControlPlaneHttpError(409, "task_running_conflict", "Task is already running for another agent.");
    }

    const agentResult = await client.query<AgentRow>(
      "SELECT agent_id,runtime_type,model_provider,model_name FROM agents WHERE agent_id=$1",
      [identity.agentId],
    );
    const agent = agentResult.rows[0];
    if (!agent) throw new ControlPlaneHttpError(400, "agent_not_found", "Credential agent does not exist.");
    const runtimeType = choice(body.runtime_type || agent.runtime_type, RUNTIME_TYPES, "mock");
    const parentRunId = body.parent_run_id ? identifier(body.parent_run_id, "parent_run_id") : null;
    if (parentRunId === runId) {
      throw new ControlPlaneHttpError(400, "parent_run_id_invalid", "A run cannot be its own parent.");
    }
    if (parentRunId) {
      const parent = await client.query(
        "SELECT 1 FROM runs WHERE run_id=$1 AND workspace_id=$2 FOR SHARE",
        [parentRunId, identity.workspaceId],
      );
      if (!parent.rowCount) {
        throw new ControlPlaneHttpError(404, "parent_run_not_found", "Parent run was not found in the credential workspace.");
      }
    }

    const delegationId = body.delegation_id
      ? identifier(body.delegation_id, "delegation_id")
      : defaultDelegationId(taskId, identity.agentId);
    const requestedStartStatus = choice(body.status, RUN_STATUSES, "running");
    if (requestedStartStatus !== "running") {
      throw new ControlPlaneHttpError(
        409,
        "run_start_status_invalid",
        "A new run must start in running status.",
      );
    }
    await client.query("SELECT pg_advisory_xact_lock(hashtext($1))", [`agentops-run:${runId}`]);
    const existingResult = await client.query<RunRow>("SELECT * FROM runs WHERE run_id=$1 FOR UPDATE", [runId]);
    const existing = existingResult.rows[0];
    if (existing) {
      const immutableConflict = existing.workspace_id !== identity.workspaceId
        || existing.task_id !== taskId
        || existing.agent_id !== identity.agentId
        || (body.runtime_type !== undefined && existing.runtime_type !== runtimeType)
        || (body.parent_run_id !== undefined && existing.parent_run_id !== parentRunId)
        || (body.delegation_id !== undefined && existing.delegation_id !== delegationId)
        || (body.agent_plan_id !== undefined && existing.agent_plan_id !== String(body.agent_plan_id))
        || (body.plan_hash !== undefined && existing.plan_hash !== String(body.plan_hash))
        || (body.approval_required !== undefined && Boolean(existing.approval_required) !== boolean(body.approval_required));
      if (immutableConflict) {
        throw new ControlPlaneHttpError(409, "run_immutable_binding_conflict", "run_id is already bound to another execution identity.");
      }
      if (existing.agent_plan_id) {
        const planResult = await client.query<RunnablePlanRow>(
          "SELECT * FROM agent_plans WHERE plan_id=$1 AND workspace_id=$2 FOR SHARE",
          [existing.agent_plan_id, identity.workspaceId],
        );
        const plan = planResult.rows[0];
        const computedPlanHash = plan ? computeAgentPlanHash(plan) : null;
        const verification = plan
          ? verifyAgentPlanRow({ ...plan, plan_hash: computedPlanHash })
          : null;
        if (
          !plan
          || plan.agent_id !== identity.agentId
          || plan.task_id !== taskId
          || plan.plan_hash !== computedPlanHash
          || existing.plan_hash !== plan.plan_hash
          || !plan.verified_at
          || !plan.verification_result_hash
          || !verification?.pass
          || plan.verification_result_hash
            !== agentPlanVerificationHash(plan.plan_id, verification)
        ) {
          throw new ControlPlaneHttpError(
            409,
            "run_plan_binding_stale",
            "Run replay requires its current verified Agent Plan binding.",
          );
        }
      }
      return response(existing, "unchanged");
    }
    const costUsd = nonNegativeNumber(body.cost_usd, "cost_usd");
    const entitlementDecision = await evaluateWorkspaceEntitlement(client, {
      workspaceId: identity.workspaceId,
      operation: "run_start",
      agentId: identity.agentId,
      estimatedCostUsd: costUsd,
    });
    if (!entitlementDecision.allow) {
      await appendAudit(client, {
        workspaceId: identity.workspaceId,
        actorType: "agent",
        actorId: identity.agentId,
        action: "agent_gateway.run_start.entitlement_denied",
        entityType: "runs",
        entityId: runId,
        after: entitlementDecision,
        metadata: {
          workspace_id: identity.workspaceId,
          task_id: taskId,
          operation: "run_start",
          reason_code: entitlementDecision.reason_code,
          entitlement_decision: entitlementDecision,
          credentials_omitted: true,
          raw_config_omitted: true,
        },
      });
      return entitlementDeniedResponse(entitlementDecision);
    }
    let agentPlanId: string | null = null;
    let planHash: string | null = null;
    if (runtimeType !== "mock") {
      const requestedPlanId = body.agent_plan_id ? identifier(body.agent_plan_id, "agent_plan_id") : null;
      const planResult = await client.query<RunnablePlanRow>(
        `SELECT * FROM agent_plans
        WHERE workspace_id=$1 AND task_id=$2 AND agent_id=$3
          AND (run_id IS NULL OR run_id=$4) AND status IN ('submitted','approved')
          AND ($5::text IS NULL OR plan_id=$5)
        ORDER BY updated_at DESC,created_at DESC LIMIT 1 FOR SHARE`,
        [identity.workspaceId, taskId, identity.agentId, runId, requestedPlanId],
      );
      const plan = planResult.rows[0];
      const computedPlanHash = plan ? computeAgentPlanHash(plan) : null;
      const verification = plan
        ? verifyAgentPlanRow({ ...plan, plan_hash: computedPlanHash })
        : null;
      if (!plan || !verification?.pass) {
        throw new ControlPlaneHttpError(
          409,
          "verified_agent_plan_required",
          "Non-mock run start requires a verifiable Agent Plan bound to this workspace, task, and agent.",
        );
      }
      if (!plan.plan_hash || plan.plan_hash !== computedPlanHash) {
        throw new ControlPlaneHttpError(
          409,
          "agent_plan_hash_mismatch",
          "Agent Plan content no longer matches its stored immutable hash.",
        );
      }
      if (
        !plan.verified_at
        || !plan.verification_result_hash
        || plan.verification_result_hash
          !== agentPlanVerificationHash(plan.plan_id, verification)
      ) {
        throw new ControlPlaneHttpError(
          409,
          "current_agent_plan_verification_required",
          "Non-mock run start requires the current persisted Agent Plan verification.",
        );
      }
      if (
        body.plan_hash !== undefined
        && String(body.plan_hash) !== plan.plan_hash
      ) {
        throw new ControlPlaneHttpError(
          409,
          "agent_plan_hash_mismatch",
          "Requested plan_hash does not match the verified Agent Plan.",
        );
      }
      if (plan.approval_required && plan.status !== "approved") {
        throw new ControlPlaneHttpError(
          409,
          "agent_plan_approval_required",
          "Approval-required Agent Plan must be human-approved before non-mock run start.",
        );
      }
      agentPlanId = plan.plan_id;
      planHash = plan.plan_hash;
    }
    if (!STARTABLE_TASK_STATUSES.has(task.status)) {
      throw new ControlPlaneHttpError(409, "task_status_conflict", `Task cannot start a run from status ${task.status}.`);
    }

    const now = new Date().toISOString();
    const startedAt = optionalIso(body.started_at, "started_at") || now;
    const run: RunRow = {
      run_id: runId,
      workspace_id: identity.workspaceId,
      task_id: taskId,
      agent_id: identity.agentId,
      runtime_type: runtimeType,
      status: requestedStartStatus,
      started_at: startedAt,
      ended_at: optionalIso(body.ended_at, "ended_at"),
      duration_ms: body.duration_ms === undefined || body.duration_ms === null || body.duration_ms === ""
        ? null
        : nonNegativeInteger(body.duration_ms, "duration_ms"),
      input_summary: text(body.input_summary || task.title, 200),
      output_summary: text(body.output_summary, 200) || null,
      model_provider: ["hermes", "openclaw"].includes(runtimeType)
        ? runtimeType
        : text(body.model_provider || agent.model_provider || "external", 80),
      model_name: text(body.model_name || agent.model_name || "gateway-client", 120),
      input_tokens: nonNegativeInteger(body.input_tokens, "input_tokens"),
      output_tokens: nonNegativeInteger(body.output_tokens, "output_tokens"),
      reasoning_tokens: nonNegativeInteger(body.reasoning_tokens, "reasoning_tokens"),
      cost_usd: costUsd,
      error_type: text(body.error_type, 80) || null,
      error_message: text(body.error_message, 200) || null,
      trace_id: body.trace_id ? identifier(body.trace_id, "trace_id") : newLedgerId("trace"),
      parent_run_id: parentRunId,
      delegation_id: delegationId,
      approval_required: boolean(body.approval_required) ? 1 : 0,
      agent_plan_id: agentPlanId,
      plan_hash: planHash,
      created_at: now,
    };
    await client.query(
      `INSERT INTO runs(
        run_id,workspace_id,task_id,agent_id,runtime_type,status,started_at,ended_at,duration_ms,input_summary,output_summary,
        model_provider,model_name,input_tokens,output_tokens,reasoning_tokens,cost_usd,error_type,error_message,trace_id,
        parent_run_id,delegation_id,approval_required,agent_plan_id,plan_hash,created_at
      ) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22,$23,$24,$25,$26)`,
      [
        run.run_id,
        run.workspace_id,
        run.task_id,
        run.agent_id,
        run.runtime_type,
        run.status,
        run.started_at,
        run.ended_at,
        run.duration_ms,
        run.input_summary,
        run.output_summary,
        run.model_provider,
        run.model_name,
        run.input_tokens,
        run.output_tokens,
        run.reasoning_tokens,
        run.cost_usd,
        run.error_type,
        run.error_message,
        run.trace_id,
        run.parent_run_id,
        run.delegation_id,
        run.approval_required,
        run.agent_plan_id,
        run.plan_hash,
        run.created_at,
      ],
    );
    await client.query(
      "UPDATE agents SET status='running',updated_at=$1 WHERE agent_id=$2",
      [now, identity.agentId],
    );
    await appendAudit(client, {
      workspaceId: identity.workspaceId,
      actorType: "system",
      actorId: "agent-gateway",
      action: "run.create",
      entityType: "runs",
      entityId: runId,
      after: runAuditSnapshot(run),
      metadata: {
        workspace_id: identity.workspaceId,
        input_hash: stableHash(run.input_summary || task.title),
        agent_plan_id: agentPlanId,
        plan_hash: planHash,
      },
    });
    if (task.status !== "running" || !task.owner_agent_id) {
      const taskUpdate = await client.query<TaskRow>(
        `UPDATE tasks SET status='running',owner_agent_id=COALESCE(NULLIF(owner_agent_id,''),$1),updated_at=$2
        WHERE task_id=$3 AND workspace_id=$4 RETURNING *`,
        [identity.agentId, now, taskId, identity.workspaceId],
      );
      const updatedTask = taskUpdate.rows[0];
      if (!updatedTask) throw new Error("typescript_control_plane_task_transition_missing");
      await appendAudit(client, {
        workspaceId: identity.workspaceId,
        actorType: "agent",
        actorId: identity.agentId,
        action: "agent_gateway.task_run_start",
        entityType: "tasks",
        entityId: taskId,
        before: taskAuditSnapshot(task),
        after: taskAuditSnapshot(updatedTask),
        metadata: { workspace_id: identity.workspaceId, run_id: runId, raw_payload_omitted: true },
      });
    }
    await appendRuntimeEvent(client, {
      eventType: "run.start",
      status: "running",
      runId,
      taskId,
      agentId: identity.agentId,
      inputSummary: run.input_summary,
    });
    return response(run, "created", agentPlanId);
  });
}

export async function heartbeatAgentGatewayRun(request: Request, requestedRunId: string) {
  const body = await boundedJsonObject(request, {
    maxBytes: RUN_WRITE_MAX_BODY_BYTES,
    label: "Run heartbeat",
  });
  const runId = identifier(requestedRunId, "run_id");

  return withPostgresTransaction(async (client) => {
    const identity = await authenticateAgentGateway(client, request.headers, "runs:write");
    enforceWorkspaceBinding(identity, {
      header: request.headers.get("x-agentops-workspace-id"),
      body: body.workspace_id,
    });
    const requestedAgent = text(body.agent_id || identity.agentId, 120);
    if (requestedAgent !== identity.agentId) {
      throw new ControlPlaneHttpError(403, "forbidden", "Agent credential cannot heartbeat another agent's run.");
    }

    // Discover the task without a lock, then take locks in the same task -> run order as run-start.
    const candidateResult = await client.query<Pick<RunRow, "task_id" | "agent_id">>(
      "SELECT task_id,agent_id FROM runs WHERE run_id=$1 AND workspace_id=$2",
      [runId, identity.workspaceId],
    );
    const candidate = candidateResult.rows[0];
    if (!candidate) {
      throw new ControlPlaneHttpError(404, "run_not_found", "Run was not found in the credential workspace.");
    }
    if (candidate.agent_id !== identity.agentId) {
      throw new ControlPlaneHttpError(403, "forbidden", "Run belongs to another agent.");
    }
    if (body.task_id !== undefined && identifier(body.task_id, "task_id") !== candidate.task_id) {
      throw new ControlPlaneHttpError(403, "forbidden", "Run heartbeat task_id must match the target run.");
    }

    const taskResult = await client.query<TaskRow>(
      "SELECT * FROM tasks WHERE task_id=$1 AND workspace_id=$2 FOR UPDATE",
      [candidate.task_id, identity.workspaceId],
    );
    const task = taskResult.rows[0];
    if (!task) {
      throw new ControlPlaneHttpError(404, "task_not_found", "Run task was not found in the credential workspace.");
    }
    await client.query("SELECT pg_advisory_xact_lock(hashtext($1))", [`agentops-run:${runId}`]);
    const runResult = await client.query<RunRow>(
      "SELECT * FROM runs WHERE run_id=$1 AND workspace_id=$2 FOR UPDATE",
      [runId, identity.workspaceId],
    );
    const before = runResult.rows[0];
    if (!before) {
      throw new ControlPlaneHttpError(404, "run_not_found", "Run was not found in the credential workspace.");
    }
    if (before.task_id !== candidate.task_id) {
      throw new ControlPlaneHttpError(409, "run_immutable_binding_conflict", "Run task binding changed while heartbeat was waiting.");
    }
    if (before.agent_id !== identity.agentId) {
      throw new ControlPlaneHttpError(403, "forbidden", "Run belongs to another agent.");
    }
    if (before.agent_plan_id) {
      const planResult = await client.query<RunnablePlanRow>(
        "SELECT * FROM agent_plans WHERE plan_id=$1 AND workspace_id=$2 FOR SHARE",
        [before.agent_plan_id, identity.workspaceId],
      );
      const plan = planResult.rows[0];
      const computedPlanHash = plan ? computeAgentPlanHash(plan) : null;
      const verification = plan
        ? verifyAgentPlanRow({ ...plan, plan_hash: computedPlanHash })
        : null;
      if (
        !plan
        || plan.agent_id !== identity.agentId
        || plan.task_id !== before.task_id
        || plan.plan_hash !== computedPlanHash
        || before.plan_hash !== plan.plan_hash
        || !plan.verified_at
        || !plan.verification_result_hash
        || !verification?.pass
        || plan.verification_result_hash
          !== agentPlanVerificationHash(plan.plan_id, verification)
      ) {
        throw new ControlPlaneHttpError(
          409,
          "run_plan_binding_stale",
          "Run heartbeat requires its current verified Agent Plan binding.",
        );
      }
    }

    const status = choice(body.status, RUN_STATUSES, before.status);
    if (["completed", "failed", "blocked"].includes(before.status) && status !== before.status) {
      throw new ControlPlaneHttpError(
        409,
        "run_terminal_conflict",
        `Run ${runId} is terminal and cannot move from ${before.status} to ${status} by heartbeat.`,
      );
    }
    const now = new Date().toISOString();
    const terminal = ["completed", "failed", "blocked"].includes(status);
    let endedAt = before.ended_at;
    if (body.ended_at !== undefined && body.ended_at !== null && body.ended_at !== "") {
      endedAt = optionalIso(body.ended_at, "ended_at");
    } else if (terminal && !endedAt) {
      endedAt = now;
    }
    const durationMs = body.duration_ms === undefined || body.duration_ms === null || body.duration_ms === ""
      ? (before.duration_ms === null ? null : Number(before.duration_ms))
      : nonNegativeInteger(body.duration_ms, "duration_ms");
    const outputSummary = body.output_summary === undefined || body.output_summary === null || body.output_summary === ""
      ? before.output_summary
      : text(body.output_summary, 200) || null;
    const errorType = body.error_type === undefined || body.error_type === null || body.error_type === ""
      ? before.error_type
      : text(body.error_type, 80) || null;
    const errorMessage = body.error_message === undefined || body.error_message === null || body.error_message === ""
      ? before.error_message
      : text(body.error_message, 200) || null;
    const outputTokens = body.output_tokens === undefined || body.output_tokens === null || body.output_tokens === ""
      ? Number(before.output_tokens || 0)
      : nonNegativeInteger(body.output_tokens, "output_tokens");
    const costUsd = body.cost_usd === undefined || body.cost_usd === null || body.cost_usd === ""
      ? Number(before.cost_usd || 0)
      : nonNegativeNumber(body.cost_usd, "cost_usd");
    const candidateAfter: RunRow = {
      ...before,
      status,
      ended_at: endedAt,
      duration_ms: durationMs,
      output_summary: outputSummary,
      error_type: errorType,
      error_message: errorMessage,
      output_tokens: outputTokens,
      cost_usd: costUsd,
    };
    if (
      ["completed", "failed", "blocked"].includes(before.status)
      && !sameHeartbeatState(before, candidateAfter)
    ) {
      throw new ControlPlaneHttpError(
        409,
        "run_terminal_conflict",
        `Run ${runId} is terminal and its receipt is immutable.`,
      );
    }
    if (sameHeartbeatState(before, candidateAfter)) {
      return heartbeatResponse(before, "unchanged");
    }

    const updateResult = await client.query<RunRow>(
      `UPDATE runs SET status=$1,ended_at=$2,duration_ms=$3,output_summary=$4,error_type=$5,error_message=$6,
        output_tokens=$7,cost_usd=$8 WHERE run_id=$9 AND workspace_id=$10 RETURNING *`,
      [
        status,
        endedAt,
        durationMs,
        outputSummary,
        errorType,
        errorMessage,
        outputTokens,
        costUsd,
        runId,
        identity.workspaceId,
      ],
    );
    const after = updateResult.rows[0];
    if (!after) throw new Error("typescript_control_plane_run_heartbeat_missing");
    await appendAudit(client, {
      workspaceId: identity.workspaceId,
      actorType: "agent",
      actorId: identity.agentId,
      action: "agent_gateway.run_heartbeat",
      entityType: "runs",
      entityId: runId,
      before: runAuditSnapshot(before),
      after: runAuditSnapshot(after),
      metadata: { workspace_id: identity.workspaceId, status, raw_payload_omitted: true },
    });

    if (terminal) {
      const taskStatus = status === "completed" ? "completed" : status === "blocked" ? "blocked" : "failed";
      if (task.status !== taskStatus) {
        const taskUpdate = await client.query<TaskRow>(
          "UPDATE tasks SET status=$1,updated_at=$2 WHERE task_id=$3 AND workspace_id=$4 RETURNING *",
          [taskStatus, now, task.task_id, identity.workspaceId],
        );
        const updatedTask = taskUpdate.rows[0];
        if (!updatedTask) throw new Error("typescript_control_plane_task_heartbeat_transition_missing");
        await appendAudit(client, {
          workspaceId: identity.workspaceId,
          actorType: "agent",
          actorId: identity.agentId,
          action: "agent_gateway.task_run_heartbeat",
          entityType: "tasks",
          entityId: task.task_id,
          before: taskAuditSnapshot(task),
          after: taskAuditSnapshot(updatedTask),
          metadata: { workspace_id: identity.workspaceId, run_id: runId, run_status: status, raw_payload_omitted: true },
        });
      }
      await client.query("UPDATE agents SET status='idle',updated_at=$1 WHERE agent_id=$2", [now, identity.agentId]);
    }
    await appendRuntimeEvent(client, {
      eventType: "run.heartbeat",
      status,
      runId,
      taskId: before.task_id,
      agentId: identity.agentId,
      outputSummary,
      errorMessage,
    });
    return heartbeatResponse(after, "updated");
  });
}
