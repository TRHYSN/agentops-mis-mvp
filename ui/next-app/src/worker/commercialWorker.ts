import type {
  CommercialWorkerConfig,
  CommercialWorkerReceipt,
  GatewayPort,
  GatewayTask,
  KnowledgeEvidence,
  RiskLevel,
  RuntimeAdapter,
  RuntimeAdapterResult,
} from "./contracts";
import { WORKER_METHOD_STEPS } from "./contracts";
import { GatewayHttpError } from "./gatewayClient";
import { buildWorkerPrompt, taskRequestsExternalWrite } from "./prompt";
import {
  boundedInteger,
  redactText,
  safeIdentifier,
  stableHash,
} from "./redaction";

const API = "/api/mis/agent-gateway";
const RISK_ORDER: Record<RiskLevel, number> = {
  low: 0,
  medium: 1,
  high: 2,
  critical: 3,
};

function objectValue(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function arrayValue(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function stringValue(value: unknown) {
  return typeof value === "string" ? value : "";
}

function stringList(value: unknown, maximum = 8) {
  return arrayValue(value)
    .filter((item): item is string => typeof item === "string")
    .map((item) => redactText(item, 160))
    .filter(Boolean)
    .slice(0, maximum);
}

function normalizedRisk(value: unknown): RiskLevel {
  const risk = String(value || "medium").toLowerCase();
  return risk in RISK_ORDER ? risk as RiskLevel : "medium";
}

function maximumRisk(...values: unknown[]): RiskLevel {
  return values
    .map(normalizedRisk)
    .sort((left, right) => RISK_ORDER[right] - RISK_ORDER[left])[0] || "medium";
}

function taskFrom(value: unknown): GatewayTask {
  const row = objectValue(value);
  return {
    task_id: safeIdentifier(row.task_id, "task_id"),
    title: stringValue(row.title),
    description: stringValue(row.description),
    acceptance_criteria: stringValue(row.acceptance_criteria),
    risk_level: stringValue(row.risk_level || "medium"),
    status: stringValue(row.status),
    target_resource: stringValue(row.target_resource),
    external_action_type: stringValue(row.external_action_type),
    intake: objectValue(row.intake),
  };
}

function knowledgeFrom(payload: Record<string, unknown>): KnowledgeEvidence {
  const primary = objectValue(payload.primary_search);
  const results = arrayValue(primary.results);
  const rows = results
    .map(objectValue)
    .filter((row) => stringValue(row.retrieval_id));
  return {
    consumed: rows.length > 0,
    packetHash: stableHash({
      operation: payload.operation,
      status: payload.status,
      task_context: objectValue(payload.task_context),
      query_hash: payload.query_hash,
      metrics: objectValue(payload.metrics),
      results: rows.map((row) => ({
        retrieval_id: row.retrieval_id,
        path: row.path,
        source_hash: row.source_hash,
        rank: row.rank,
      })),
    }),
    queryHash: stringValue(payload.query_hash) || null,
    status: redactText(payload.status || "attention", 60),
    retrievalIds: stringList(rows.map((row) => row.retrieval_id)),
    paths: stringList(rows.map((row) => row.path)),
    sourceHashes: stringList(rows.map((row) => row.source_hash)),
    metrics: objectValue(payload.metrics),
  };
}

function unavailableKnowledge(reason: string): KnowledgeEvidence {
  return {
    consumed: false,
    packetHash: stableHash({
      status: "unavailable",
      reason,
      raw_content_omitted: true,
    }),
    queryHash: null,
    status: "unavailable",
    retrievalIds: [],
    paths: [],
    sourceHashes: [],
    metrics: {},
  };
}

function secretBoundary() {
  return {
    secret_boundary: "trusted_worker_client_v1",
    credential_transport: "trusted_worker_client_only",
    model_visible_credentials: false,
    secrets_in_prompt: false,
    secrets_in_output: false,
    raw_prompt_omitted: true,
    raw_response_omitted: true,
    token_omitted: true,
  };
}

function receiptBase(
  config: CommercialWorkerConfig,
): Pick<
  CommercialWorkerReceipt,
  "runtime" | "token_omitted" | "raw_prompt_omitted" | "raw_response_omitted"
> {
  return {
    runtime: config.runtime,
    token_omitted: true,
    raw_prompt_omitted: true,
    raw_response_omitted: true,
  };
}

function normalizeAdapterResult(
  result: RuntimeAdapterResult,
  runtime: CommercialWorkerConfig["runtime"],
): RuntimeAdapterResult {
  const hash = /^[a-f0-9]{64}$/.test(result.rawPayloadHash)
    ? result.rawPayloadHash
    : stableHash({
      runtime,
      output_summary: redactText(result.outputSummary, 720),
      error_type: redactText(result.errorType, 120),
    });
  const normalized = {
    ...result,
    runtime,
    modelName: redactText(result.modelName, 120),
    outputSummary: redactText(result.outputSummary, 720),
    rawPayloadHash: hash,
    targetResource: redactText(result.targetResource, 240),
    durationMs: boundedInteger(result.durationMs, 0, 0, 86_400_000),
    outputTokens: boundedInteger(result.outputTokens, 0, 0, 10_000_000),
    errorType: result.errorType ? redactText(result.errorType, 120) : null,
    errorMessage: result.errorMessage
      ? redactText(result.errorMessage, 300)
      : null,
  };
  if (
    normalized.ok
    && (
      normalized.providerCallPerformed !== true
      || normalized.dryRun !== false
    )
  ) {
    return {
      ...normalized,
      ok: false,
      retryable: false,
      errorType: "ProviderAttestationInvalid",
      errorMessage: "Successful runtime result lacks provider-call attestation.",
      outputSummary: "Runtime result was rejected because provider attestation is invalid.",
    };
  }
  return normalized;
}

export class CommercialWorker {
  readonly #gateway: GatewayPort;
  readonly #adapter: RuntimeAdapter;
  readonly #config: CommercialWorkerConfig;

  constructor(
    gateway: GatewayPort,
    adapter: RuntimeAdapter,
    config: CommercialWorkerConfig,
  ) {
    this.#gateway = gateway;
    this.#adapter = adapter;
    this.#config = {
      ...config,
      workspaceId: safeIdentifier(config.workspaceId, "workspace_id"),
      agentId: safeIdentifier(config.agentId, "agent_id"),
      taskId: config.taskId
        ? safeIdentifier(config.taskId, "task_id")
        : undefined,
      statuses: (config.statuses || ["planned"]).slice(0, 4),
      maxAdapterAttempts: boundedInteger(
        config.maxAdapterAttempts,
        2,
        1,
        5,
      ),
      retryDelayMs: boundedInteger(config.retryDelayMs, 250, 0, 30_000),
    };
    if (adapter.runtime !== config.runtime) {
      throw new Error("worker_adapter_runtime_mismatch");
    }
  }

  async #safeHeartbeat(status: string, summary: string) {
    try {
      await this.#gateway.post(`${API}/heartbeat`, {
        workspace_id: this.#config.workspaceId,
        agent_id: this.#config.agentId,
        status,
        summary: redactText(summary, 200),
        runtime_type: this.#config.runtime,
      });
    } catch {
      // The primary operation remains authoritative; heartbeat is best effort.
    }
  }

  async #knowledge(task: GatewayTask) {
    const query = {
      workspace_id: this.#config.workspaceId,
      task_id: task.task_id,
      adapter: this.#config.runtime,
      limit: 5,
    };
    try {
      let payload = await this.#gateway.get<Record<string, unknown>>(
        `${API}/knowledge/evidence-packet`,
        query,
      );
      let evidence = knowledgeFrom(payload);
      if (evidence.consumed) return evidence;
      try {
        await this.#gateway.post(`${API}/knowledge/index`, { rebuild: false });
        payload = await this.#gateway.get<Record<string, unknown>>(
          `${API}/knowledge/evidence-packet`,
          query,
        );
        evidence = knowledgeFrom(payload);
      } catch {
        return evidence;
      }
      return evidence;
    } catch {
      return unavailableKnowledge("knowledge_evidence_unavailable");
    }
  }

  async #verifiedPlan(task: GatewayTask, knowledge: KnowledgeEvidence) {
    const intake = objectValue(task.intake);
    const intakePlanId = stringValue(intake.plan_id);
    if (intakePlanId && intake.plan_verified === true) {
      const verified = await this.#gateway.get<Record<string, unknown>>(
        `${API}/agent-plans/${encodeURIComponent(intakePlanId)}/verify`,
      );
      if (objectValue(verified.verification).pass === true) {
        return {
          planId: safeIdentifier(intakePlanId, "plan_id"),
          payload: verified,
          reused: true,
        };
      }
    }
    const risk = normalizedRisk(task.risk_level);
    const created = await this.#gateway.post<Record<string, unknown>>(
      `${API}/agent-plans`,
      {
        workspace_id: this.#config.workspaceId,
        agent_id: this.#config.agentId,
        task_id: task.task_id,
        task_understanding:
          `Process task '${redactText(task.title, 120)}' through the `
          + `${this.#config.runtime} TypeScript worker and bind governed evidence.`,
        referenced_specs: ["PROJECT_SPEC.md", "AGENT_WORKFLOW.md"],
        referenced_memories: knowledge.paths.slice(0, 8),
        referenced_bases: ["base_local_tasks", "base_local_memory"],
        proposed_files_to_change: [
          "agentops-commercial-worker-runtime",
          `adapter:${this.#config.runtime}`,
        ],
        risk_level: risk,
        approval_required: ["high", "critical"].includes(risk),
        execution_steps: [...WORKER_METHOD_STEPS],
        verification_plan:
          "Consume bounded knowledge evidence and record runtime, tool, evaluation, artifact, audit, and manifest evidence.",
        rollback_plan:
          "Fail the run and withhold customer delivery when runtime or evidence verification is incomplete.",
        status: "submitted",
      },
    );
    const planId = safeIdentifier(
      objectValue(created.agent_plan).plan_id,
      "plan_id",
    );
    const verified = await this.#gateway.get<Record<string, unknown>>(
      `${API}/agent-plans/${encodeURIComponent(planId)}/verify`,
    );
    if (objectValue(verified.verification).pass !== true) {
      throw new Error("agent_plan_verification_failed");
    }
    return { planId, payload: verified, reused: false };
  }

  async #executeWithRetries(bundle: ReturnType<typeof buildWorkerPrompt>) {
    const maximum = this.#config.maxAdapterAttempts || 1;
    const history: NonNullable<RuntimeAdapterResult["retryHistory"]> = [];
    let final: RuntimeAdapterResult | null = null;
    for (let attempt = 1; attempt <= maximum; attempt += 1) {
      const result = normalizeAdapterResult(
        await this.#adapter.execute(bundle),
        this.#config.runtime,
      );
      history.push({
        attempt,
        ok: result.ok,
        retryable: result.retryable,
        errorType: result.errorType,
      });
      final = {
        ...result,
        attemptCount: attempt,
        maxAttempts: maximum,
        retryHistory: [...history],
      };
      if (result.ok || !result.retryable || attempt === maximum) break;
      const delay = (this.#config.retryDelayMs || 0) * attempt;
      if (delay > 0) {
        await new Promise((resolve) => setTimeout(resolve, delay));
      }
    }
    if (!final) throw new Error("runtime_adapter_did_not_execute");
    return final;
  }

  async #blockExternalWrite(
    task: GatewayTask,
    runId: string,
    planId: string,
    planHash: string,
  ): Promise<CommercialWorkerReceipt> {
    const targetResource = `${this.#config.runtime}://external-write/${stableHash({
      workspace_id: this.#config.workspaceId,
      task_id: task.task_id,
    }).slice(0, 20)}`;
    const tool = await this.#gateway.post<Record<string, unknown>>(
      `${API}/tool-calls`,
      {
        workspace_id: this.#config.workspaceId,
        run_id: runId,
        agent_id: this.#config.agentId,
        tool_name: `agent_worker.${this.#config.runtime}.external_write_blocked`,
        tool_category: "custom",
        risk_level: "high",
        status: "waiting_approval",
        target_resource: targetResource,
        args: {
          task_id: task.task_id,
          run_id: runId,
          agent_plan_id: planId,
          agent_plan_hash: planHash,
          adapter: this.#config.runtime,
          external_write_intent: true,
          external_write_runtime_execution_supported: false,
          requires_governed_prepared_action: true,
          raw_omitted: true,
          ...secretBoundary(),
        },
        result_summary:
          "TypeScript worker blocked the external write before provider execution.",
      },
    );
    const toolCallId = stringValue(objectValue(tool.tool_call).tool_call_id);
    await this.#gateway.post(`${API}/audit`, {
      workspace_id: this.#config.workspaceId,
      agent_id: this.#config.agentId,
      action: "agent_worker.external_write_blocked",
      entity_type: "runs",
      entity_id: runId,
      task_id: task.task_id,
      run_id: runId,
      metadata: {
        adapter: this.#config.runtime,
        agent_plan_id: planId,
        tool_call_id: toolCallId || null,
        provider_call_performed: false,
        dry_run: true,
        live_execution_performed: false,
        prepared_action_supported_for_runtime: false,
        ...secretBoundary(),
      },
    });
    await this.#safeHeartbeat(
      "paused",
      "External write requires a governed runtime-specific PreparedAction owner.",
    );
    return {
      ...receiptBase(this.#config),
      ok: true,
      processed: false,
      reason: "external_write_prepared_action_owner_required",
      task_id: task.task_id,
      run_id: runId,
      plan_id: planId,
      provider_call_performed: false,
      dry_run: true,
      output_summary:
        "External write was blocked before Hermes/OpenClaw provider execution.",
      external_write_gate: {
        tool_call_id: toolCallId || null,
        approval_id: null,
        prepared_action_id: null,
        live_execution_performed: false,
      },
    };
  }

  async runOnce(): Promise<CommercialWorkerReceipt> {
    if (!this.#config.confirmRun) {
      throw new Error("confirm_run_required_before_gateway_pull");
    }
    const pullQuery: Record<
      string,
      string | number | boolean | string[] | undefined
    > = {
      workspace_id: this.#config.workspaceId,
      agent_id: this.#config.agentId,
      status: this.#config.statuses || ["planned"],
      limit: 1,
      enforce_intake: false,
      task_id: this.#config.taskId,
    };
    const pulled = await this.#gateway.get<Record<string, unknown>>(
      `${API}/tasks/pull`,
      pullQuery,
    );
    const candidates = arrayValue(pulled.tasks);
    if (candidates.length === 0) {
      await this.#safeHeartbeat("idle", "TypeScript worker found no eligible task.");
      return {
        ...receiptBase(this.#config),
        ok: true,
        processed: false,
        reason: "no_task",
        provider_call_performed: false,
        dry_run: false,
      };
    }
    const task = taskFrom(candidates[0]);
    const risk = normalizedRisk(task.risk_level);
    if (["high", "critical"].includes(risk) && !this.#config.allowHighRisk) {
      await this.#safeHeartbeat("paused", "Task risk requires explicit high-risk authorization.");
      return {
        ...receiptBase(this.#config),
        ok: false,
        processed: false,
        reason: "risk_not_allowed",
        task_id: task.task_id,
        provider_call_performed: false,
        dry_run: true,
      };
    }

    await this.#gateway.post(
      `${API}/tasks/${encodeURIComponent(task.task_id)}/claim`,
      {
        workspace_id: this.#config.workspaceId,
        agent_id: this.#config.agentId,
        runtime_type: this.#config.runtime,
      },
    );
    const knowledge = await this.#knowledge(task);
    const plan = await this.#verifiedPlan(task, knowledge);
    const verifiedPlan = objectValue(plan.payload.agent_plan);
    const planHash = stringValue(
      verifiedPlan.plan_hash
      || objectValue(plan.payload.verification).plan_hash,
    );
    if (!/^[a-f0-9]{64}$/.test(planHash)) {
      throw new Error("verified_agent_plan_hash_required");
    }
    const runPayload = await this.#gateway.post<Record<string, unknown>>(
      `${API}/runs/start`,
      {
        workspace_id: this.#config.workspaceId,
        agent_id: this.#config.agentId,
        task_id: task.task_id,
        agent_plan_id: plan.planId,
        plan_hash: planHash,
        runtime_type: this.#config.runtime,
        estimated_cost_usd: this.#config.estimatedCostUsd,
        input_summary:
          `TypeScript worker adapter=${this.#config.runtime} `
          + `task=${redactText(task.title, 120)}`,
        delegation_id:
          `commercial-ts:${this.#config.runtime}:${task.task_id}`,
      },
    );
    const runId = safeIdentifier(objectValue(runPayload.run).run_id, "run_id");
    if (taskRequestsExternalWrite(task)) {
      return this.#blockExternalWrite(
        task,
        runId,
        plan.planId,
        planHash,
      );
    }

    const prompt = buildWorkerPrompt(task, this.#config.runtime, knowledge);
    const result = await this.#executeWithRetries(prompt);
    let evidenceFailureStage = "runtime_event";
    try {
    const capability = {
      observation_level: "ledger_summary_only",
      risk_floor: "medium",
      effective_risk_level: maximumRisk(risk, "medium"),
      commercial_readiness: "governed_summary_worker",
      requires_prepared_action_for_external_write: true,
      runtime_internal_tools_remain_opaque: true,
      external_writes_supported: false,
    };
    const runtimeEvent = await this.#gateway.post<Record<string, unknown>>(
      `${API}/runtime-events`,
      {
        workspace_id: this.#config.workspaceId,
        agent_id: this.#config.agentId,
        run_id: runId,
        task_id: task.task_id,
        adapter: this.#config.runtime,
        event_type: "agent_worker.adapter_execution_summary",
        status: result.ok ? "completed" : "failed",
        input_summary:
          `TypeScript worker adapter=${this.#config.runtime} `
          + `task=${task.task_id} observation=ledger_summary_only`,
        output_summary: result.outputSummary,
        error_message: result.errorMessage,
        latency_ms: result.durationMs,
        model_name: result.modelName,
        prompt_hash: prompt.promptHash,
        raw_payload_hash: result.rawPayloadHash,
        metadata: {
          task_id: task.task_id,
          adapter: this.#config.runtime,
          ok: result.ok,
          provider_call_performed: result.providerCallPerformed,
          dry_run: result.dryRun,
          error_type: result.errorType,
          attempt_count: result.attemptCount,
          max_attempts: result.maxAttempts,
          prompt_profile_id: prompt.profile.profileId,
          prompt_profile_version: prompt.profile.version,
          prompt_profile_hash: prompt.profile.profileHash,
          ...capability,
          event_is_worker_summary_not_raw_trace: true,
          ...secretBoundary(),
        },
        source: "agentops-commercial-worker-ts.adapter-execution-summary",
      },
    );
    const runtimeEventId = stringValue(
      objectValue(runtimeEvent.runtime_event).runtime_event_id,
    );
    evidenceFailureStage = "tool_call";
    const tool = await this.#gateway.post<Record<string, unknown>>(
      `${API}/tool-calls`,
      {
        workspace_id: this.#config.workspaceId,
        run_id: runId,
        agent_id: this.#config.agentId,
        tool_name: `agent_worker.${this.#config.runtime}`,
        tool_category: "custom",
        risk_level: capability.effective_risk_level,
        status: result.ok ? "completed" : "failed",
        target_resource: result.targetResource,
        args: {
          task_id: task.task_id,
          adapter: this.#config.runtime,
          provider_call_performed: result.providerCallPerformed,
          dry_run: result.dryRun,
          prompt_hash: prompt.promptHash,
          prompt_profile_id: prompt.profile.profileId,
          prompt_profile_version: prompt.profile.version,
          prompt_profile_hash: prompt.profile.profileHash,
          attempt_count: result.attemptCount,
          max_attempts: result.maxAttempts,
          retry_history: result.retryHistory || [],
          worker_runtime_event_id: runtimeEventId,
          worker_runtime_event_summary_recorded: Boolean(runtimeEventId),
          knowledge_retrieval_evidence_consumed: knowledge.consumed,
          knowledge_retrieval_packet_hash: knowledge.packetHash,
          knowledge_retrieval_query_hash: knowledge.queryHash,
          knowledge_retrieval_status: knowledge.status,
          knowledge_retrieval_ids: knowledge.retrievalIds,
          knowledge_retrieval_paths: knowledge.paths,
          knowledge_retrieval_source_hashes: knowledge.sourceHashes,
          knowledge_retrieval_metrics: knowledge.metrics,
          ...capability,
          raw_omitted: true,
          ...secretBoundary(),
        },
        result_summary: result.outputSummary,
      },
    );
    const toolCallId = safeIdentifier(
      objectValue(tool.tool_call).tool_call_id,
      "tool_call_id",
    );
    evidenceFailureStage = "run_heartbeat";
    await this.#gateway.post(
      `${API}/runs/${encodeURIComponent(runId)}/heartbeat`,
      {
        workspace_id: this.#config.workspaceId,
        status: result.ok ? "completed" : "failed",
        output_summary: result.outputSummary,
        duration_ms: result.durationMs,
        output_tokens: result.outputTokens,
        cost_usd: this.#config.estimatedCostUsd,
        error_type: result.errorType,
        error_message: result.errorMessage,
      },
    );
    const evaluationPass = result.ok && knowledge.consumed;
    evidenceFailureStage = "evaluation";
    const evaluation = await this.#gateway.post<Record<string, unknown>>(
      `${API}/evaluations/submit`,
      {
        workspace_id: this.#config.workspaceId,
        run_id: runId,
        task_id: task.task_id,
        agent_id: this.#config.agentId,
        evaluator_type: "rule",
        score: evaluationPass ? 1 : 0,
        pass_fail: evaluationPass ? "pass" : "fail",
        rubric: {
          gate: "commercial_typescript_worker_adapter_loop",
          adapter: this.#config.runtime,
          provider_call_performed: result.providerCallPerformed,
          dry_run: result.dryRun,
          requires_completed_run: true,
          requires_knowledge_retrieval_evidence: true,
          prompt_profile_id: prompt.profile.profileId,
          prompt_profile_version: prompt.profile.version,
          prompt_profile_hash: prompt.profile.profileHash,
          knowledge_retrieval_gate_pass: knowledge.consumed,
          quality_gate_pass: evaluationPass,
          attempt_count: result.attemptCount,
          max_attempts: result.maxAttempts,
          ...capability,
          ...secretBoundary(),
        },
        notes: evaluationPass
          ? "Commercial TypeScript worker completed with governed knowledge evidence."
          : result.ok
            ? "Runtime completed but governed knowledge evidence was unavailable."
            : `Runtime failed: ${result.errorType || "unknown"}`,
      },
    );
    const evaluationId = safeIdentifier(
      objectValue(evaluation.evaluation).evaluation_id,
      "evaluation_id",
    );
    const artifactHash = stableHash({
      run_id: runId,
      task_id: task.task_id,
      adapter: this.#config.runtime,
      summary: result.outputSummary,
      ok: result.ok,
      knowledge_packet_hash: knowledge.packetHash,
    });
    evidenceFailureStage = "artifact";
    const artifact = await this.#gateway.post<Record<string, unknown>>(
      `${API}/artifacts`,
      {
        workspace_id: this.#config.workspaceId,
        run_id: runId,
        task_id: task.task_id,
        agent_id: this.#config.agentId,
        artifact_type: "agent_worker_result",
        title: `TypeScript worker result: ${redactText(task.title, 120)}`,
        uri: `run://${runId}`,
        summary: result.outputSummary,
        content_hash: artifactHash,
      },
    );
    const artifactId = safeIdentifier(
      objectValue(artifact.artifact).artifact_id,
      "artifact_id",
    );
    let memoryId: string | null = null;
    if (result.ok) {
      evidenceFailureStage = "memory";
      const memory = await this.#gateway.post<Record<string, unknown>>(
        `${API}/memories/propose`,
        {
          workspace_id: this.#config.workspaceId,
          agent_id: this.#config.agentId,
          task_id: task.task_id,
          run_id: runId,
          scope: "project",
          memory_type: "artifact_summary",
          canonical_text:
            `TypeScript worker ${this.#config.agentId} completed task `
            + `'${redactText(task.title, 80)}' via ${this.#config.runtime}.`,
          source_ref: runId,
          access_tags: [
            "commercial-typescript-worker",
            this.#config.runtime,
            "review",
          ],
          confidence: 0.72,
        },
      );
      memoryId = stringValue(objectValue(memory.memory).memory_id) || null;
    }
    evidenceFailureStage = "audit";
    const audit = await this.#gateway.post<Record<string, unknown>>(
      `${API}/audit`,
      {
        workspace_id: this.#config.workspaceId,
        agent_id: this.#config.agentId,
        action: "agent_worker.task_processed",
        entity_type: "runs",
        entity_id: runId,
        task_id: task.task_id,
        run_id: runId,
        metadata: {
          implementation: "typescript",
          adapter: this.#config.runtime,
          ok: result.ok,
          provider_call_performed: result.providerCallPerformed,
          dry_run: result.dryRun,
          prompt_hash: prompt.promptHash,
          prompt_profile_id: prompt.profile.profileId,
          prompt_profile_version: prompt.profile.version,
          prompt_profile_hash: prompt.profile.profileHash,
          raw_payload_hash: result.rawPayloadHash,
          attempt_count: result.attemptCount,
          max_attempts: result.maxAttempts,
          retryable_final: result.retryable,
          worker_runtime_event_id: runtimeEventId,
          knowledge_retrieval_evidence_consumed: knowledge.consumed,
          knowledge_retrieval_packet_hash: knowledge.packetHash,
          knowledge_retrieval_query_hash: knowledge.queryHash,
          knowledge_retrieval_status: knowledge.status,
          knowledge_retrieval_ids: knowledge.retrievalIds,
          knowledge_retrieval_paths: knowledge.paths,
          knowledge_retrieval_source_hashes: knowledge.sourceHashes,
          memory_candidate_id: memoryId,
          ...capability,
          ...secretBoundary(),
        },
      },
    );

    let manifestId: string | undefined;
    let manifestPass = false;
    let approvalRequested = false;
    let approvalId: string | undefined;
    let approvalOutcome: string | undefined;
    let approvalControlPlane: string | undefined;
    if (evaluationPass) {
      evidenceFailureStage = "manifest";
      const manifest = await this.#gateway.post<Record<string, unknown>>(
        `${API}/plan-evidence-manifests`,
        {
          workspace_id: this.#config.workspaceId,
          agent_id: this.#config.agentId,
          plan_id: plan.planId,
          run_id: runId,
          mismatch_policy: "block",
          expected_steps: [...WORKER_METHOD_STEPS],
          tool_call_ids: [toolCallId],
          evaluation_ids: [evaluationId],
          artifact_ids: [artifactId],
          audit_ids: [],
        },
      );
      manifestId = stringValue(objectValue(manifest.manifest).manifest_id)
        || undefined;
      manifestPass = objectValue(manifest.verification).pass === true;
      if (
        manifestPass
        && (this.#config.requestCustomerDeliveryApproval ?? true)
      ) {
        evidenceFailureStage = "approval";
        const approval = await this.#gateway.post<Record<string, unknown>>(
          `${API}/approvals/request`,
          {
            workspace_id: this.#config.workspaceId,
            agent_id: this.#config.agentId,
            requested_by_agent_id: this.#config.agentId,
            task_id: task.task_id,
            run_id: runId,
            approval_kind: "customer_delivery",
            decision: "pending",
            reason: "Customer delivery requires Human Owner review.",
          },
        );
        approvalRequested = true;
        approvalId = stringValue(objectValue(approval.approval).approval_id)
          || undefined;
        approvalOutcome = stringValue(approval.outcome) || undefined;
        approvalControlPlane = stringValue(approval.control_plane) || undefined;
      }
    }
    await this.#safeHeartbeat(
      result.ok ? "idle" : "error",
      result.outputSummary,
    );
    return {
      ...receiptBase(this.#config),
      ok: evaluationPass && manifestPass,
      processed: true,
      reason: evaluationPass && manifestPass
        ? "completed"
        : result.ok
          ? "knowledge_evidence_required"
          : "runtime_failed",
      task_id: task.task_id,
      run_id: runId,
      plan_id: plan.planId,
      plan_evidence_manifest_id: manifestId,
      plan_evidence_pass: manifestPass,
      customer_delivery_approval_requested: approvalRequested,
      customer_delivery_approval_id: approvalId,
      customer_delivery_approval_outcome: approvalOutcome,
      customer_delivery_approval_control_plane: approvalControlPlane,
      provider_call_performed: result.providerCallPerformed,
      dry_run: result.dryRun,
      ledger_evidence_complete: true,
      manual_reconciliation_required: false,
      output_summary: result.outputSummary,
      error_type: result.errorType,
      attempt_count: result.attemptCount,
      knowledge_evidence: {
        consumed: knowledge.consumed,
        packet_hash: knowledge.packetHash,
        query_hash: knowledge.queryHash,
        status: knowledge.status,
        result_count: knowledge.retrievalIds.length,
        raw_content_omitted: true,
      },
    };
    } catch (error) {
      const gatewayError = error instanceof GatewayHttpError ? error : null;
      await this.#safeHeartbeat(
        "error",
        "Provider result requires manual ledger reconciliation.",
      );
      return {
        ...receiptBase(this.#config),
        ok: false,
        processed: true,
        reason: "post_provider_evidence_persistence_failed",
        task_id: task.task_id,
        run_id: runId,
        plan_id: plan.planId,
        provider_call_performed: result.providerCallPerformed,
        dry_run: result.dryRun,
        ledger_evidence_complete: false,
        manual_reconciliation_required: true,
        evidence_failure_stage: evidenceFailureStage,
        evidence_failure_code:
          gatewayError?.code || "unexpected_evidence_persistence_error",
        evidence_failure_status: gatewayError?.status,
        output_summary:
          "Provider execution finished, but governed ledger evidence is incomplete.",
        error_type: "PostProviderEvidencePersistenceFailed",
        attempt_count: result.attemptCount,
        knowledge_evidence: {
          consumed: knowledge.consumed,
          packet_hash: knowledge.packetHash,
          query_hash: knowledge.queryHash,
          status: knowledge.status,
          result_count: knowledge.retrievalIds.length,
          raw_content_omitted: true,
        },
      };
    }
  }
}
