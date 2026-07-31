import assert from "node:assert/strict";
import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import { readdir, readFile } from "node:fs/promises";
import path from "node:path";

import type {
  PromptBundle,
  RuntimeAdapter,
  RuntimeAdapterResult,
} from "../src/worker/contracts";
import { CommercialWorker } from "../src/worker/commercialWorker";
import {
  GatewayHttpError,
  HttpGatewayClient,
  validateGatewayBaseUrl,
} from "../src/worker/gatewayClient";
import {
  containsProtectedMaterial,
  stableHash,
} from "../src/worker/redaction";
import { HermesAdapter } from "../src/worker/adapters";

const TOKEN = "contract-bearer-fixture-0123456789abcdef";
const TASK_CANARY = "credential_canary_abcdefghijklmnop";
const OUTPUT_CANARY = "provider_visible_output_qrstuvwxyz123456";
const ERROR_CANARY = "provider_visible_error_abcdefghijkl987654";
const OMITTED_SUMMARY =
  "Provider response omitted; execution completed and bounded metadata was recorded.";
const WORKSPACE_ID = "ws_commercial_ts_contract";
const AGENT_ID = "agt_commercial_ts_contract";
const PLAN_HASH = "a".repeat(64);

type RecordedRequest = {
  method: string;
  path: string;
  query: Record<string, string[]>;
  body: Record<string, unknown>;
};

type Scenario =
  | "happy"
  | "external"
  | "invalid_attestation"
  | "ledger_failure";

const state: {
  scenario: Scenario;
  requests: RecordedRequest[];
  redirectTargetRequests: number;
  hermesTargetRequests: number;
} = {
  scenario: "happy",
  requests: [],
  redirectTargetRequests: 0,
  hermesTargetRequests: 0,
};

function send(
  response: ServerResponse,
  status: number,
  body: Record<string, unknown>,
  headers: Record<string, string> = {},
) {
  response.writeHead(status, {
    "Content-Type": "application/json",
    ...headers,
  });
  response.end(JSON.stringify(body));
}

async function requestBody(request: IncomingMessage) {
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of request) {
    const buffer = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    size += buffer.byteLength;
    assert.ok(size <= 64 * 1024, "contract request exceeded bounded body");
    chunks.push(buffer);
  }
  if (chunks.length === 0) return {};
  const value: unknown = JSON.parse(Buffer.concat(chunks).toString("utf8"));
  assert.ok(value && typeof value === "object" && !Array.isArray(value));
  return value as Record<string, unknown>;
}

function taskIdForScenario() {
  if (state.scenario === "external") return "tsk_ts_external";
  if (state.scenario === "invalid_attestation") return "tsk_ts_invalid";
  if (state.scenario === "ledger_failure") return "tsk_ts_ledger_failure";
  return "tsk_ts_happy";
}

function taskForScenario() {
  const taskId = taskIdForScenario();
  if (state.scenario === "external") {
    return {
      task_id: taskId,
      title: "Deploy customer portal",
      description: "Publish the result to an external customer portal.",
      acceptance_criteria: "The external write must remain approval gated.",
      risk_level: "medium",
      status: "planned",
      intake: {},
    };
  }
  return {
    task_id: taskId,
    title: "Review commercial migration evidence",
    description:
      `Summarize TypeScript migration evidence without exposing ${TASK_CANARY}.`,
    acceptance_criteria: "Record verified provider and ledger evidence.",
    risk_level: "low",
    status: "planned",
    intake: {},
  };
}

async function handle(
  request: IncomingMessage,
  response: ServerResponse,
) {
  try {
    const url = new URL(request.url || "/", "http://127.0.0.1");
    if (url.pathname === "/api/mis/agent-gateway/redirect-target") {
      state.redirectTargetRequests += 1;
      send(response, 200, { ok: true });
      return;
    }
    if (
      request.method === "POST"
      && url.pathname === "/v1/chat/completions"
    ) {
      const body = await requestBody(request);
      assert.equal(body.model, "hermes-agent");
      assert.ok(Array.isArray(body.messages));
      state.hermesTargetRequests += 1;
      send(response, 200, {
        choices: [{ message: { content: "Bounded contract response." } }],
        usage: { completion_tokens: 3 },
      });
      return;
    }
    assert.equal(request.headers.authorization, `Bearer ${TOKEN}`);
    assert.equal(request.headers["x-agentops-workspace-id"], WORKSPACE_ID);
    assert.equal(request.headers["x-agentops-agent-id"], AGENT_ID);
    if (url.pathname === "/api/mis/agent-gateway/redirect") {
      response.writeHead(302, {
        Location: "/api/mis/agent-gateway/redirect-target",
        "Content-Type": "application/json",
      });
      response.end(JSON.stringify({ error: "redirect" }));
      return;
    }
    if (url.pathname === "/api/mis/agent-gateway/forbidden") {
      send(response, 401, {
        error: "unauthorized",
        message: `Never expose ${TOKEN}`,
      });
      return;
    }
    const body = await requestBody(request);
    const query: Record<string, string[]> = {};
    for (const key of new Set(url.searchParams.keys())) {
      query[key] = url.searchParams.getAll(key);
    }
    state.requests.push({
      method: request.method || "GET",
      path: url.pathname,
      query,
      body,
    });
    const taskId = taskIdForScenario();
    const runId = `run_${taskId}`;
    if (
      request.method === "GET"
      && url.pathname === "/api/mis/agent-gateway/tasks/pull"
    ) {
      send(response, 200, {
        tasks: [taskForScenario()],
        intake: { blocked: 0 },
        token_omitted: true,
      });
      return;
    }
    if (
      request.method === "POST"
      && url.pathname === `/api/mis/agent-gateway/tasks/${taskId}/claim`
    ) {
      send(response, 200, {
        task: { ...taskForScenario(), status: "running" },
        outcome: "claimed",
        token_omitted: true,
      });
      return;
    }
    if (
      request.method === "GET"
      && url.pathname === "/api/mis/agent-gateway/knowledge/evidence-packet"
    ) {
      send(response, 200, {
        operation: "knowledge_retrieval_evidence_packet",
        status: "ready",
        query_hash: stableHash({ task_id: taskId }),
        task_context: {
          task_id: taskId,
          task_found: true,
          task_text_omitted: true,
        },
        metrics: { recall_at_5: 1, mrr: 1, token_omitted: true },
        primary_search: {
          results: [{
            retrieval_id: `krv_${taskId}`,
            path: "docs/COMMERCIAL_MIGRATION.md",
            source_hash: "b".repeat(64),
            rank: 1,
            snippet_omitted: true,
            raw_content_omitted: true,
          }],
        },
        token_omitted: true,
      });
      return;
    }
    if (
      request.method === "POST"
      && url.pathname === "/api/mis/agent-gateway/agent-plans"
    ) {
      send(response, 201, {
        operation: "agent_plan_create",
        agent_plan: {
          plan_id: `plan_${taskId}`,
          task_id: taskId,
          agent_id: AGENT_ID,
          plan_hash: PLAN_HASH,
          status: "submitted",
        },
        verification: { pass: true, plan_hash: PLAN_HASH },
        token_omitted: true,
      });
      return;
    }
    if (
      request.method === "GET"
      && url.pathname === `/api/mis/agent-gateway/agent-plans/plan_${taskId}/verify`
    ) {
      send(response, 200, {
        agent_plan: {
          plan_id: `plan_${taskId}`,
          task_id: taskId,
          agent_id: AGENT_ID,
          plan_hash: PLAN_HASH,
          status: "submitted",
        },
        verification: { pass: true, plan_hash: PLAN_HASH },
        token_omitted: true,
      });
      return;
    }
    if (
      request.method === "POST"
      && url.pathname === "/api/mis/agent-gateway/runs/start"
    ) {
      send(response, 201, {
        run: {
          run_id: runId,
          task_id: taskId,
          agent_id: AGENT_ID,
          status: "running",
        },
        token_omitted: true,
      });
      return;
    }
    if (
      request.method === "POST"
      && url.pathname === "/api/mis/agent-gateway/runtime-events"
    ) {
      if (state.scenario === "ledger_failure") {
        send(response, 503, { error: "runtime_event_unavailable" });
        return;
      }
      send(response, 201, {
        runtime_event: {
          runtime_event_id: `rte_${taskId}`,
          run_id: runId,
        },
        outcome: "created",
        token_omitted: true,
      });
      return;
    }
    if (
      request.method === "POST"
      && url.pathname === "/api/mis/agent-gateway/tool-calls"
    ) {
      send(response, 201, {
        tool_call: {
          tool_call_id: `tc_${taskId}`,
          run_id: runId,
          status: body.status,
        },
        outcome: "created",
        token_omitted: true,
      });
      return;
    }
    if (
      request.method === "POST"
      && url.pathname === `/api/mis/agent-gateway/runs/${runId}/heartbeat`
    ) {
      send(response, 200, {
        run: { run_id: runId, status: body.status },
        outcome: "updated",
        token_omitted: true,
      });
      return;
    }
    if (
      request.method === "POST"
      && url.pathname === "/api/mis/agent-gateway/evaluations/submit"
    ) {
      send(response, 201, {
        evaluation: {
          evaluation_id: `eval_${taskId}`,
          run_id: runId,
          pass_fail: body.pass_fail,
        },
        outcome: "created",
        token_omitted: true,
      });
      return;
    }
    if (
      request.method === "POST"
      && url.pathname === "/api/mis/agent-gateway/artifacts"
    ) {
      send(response, 201, {
        artifact: {
          artifact_id: `art_${taskId}`,
          run_id: runId,
        },
        outcome: "created",
        token_omitted: true,
      });
      return;
    }
    if (
      request.method === "POST"
      && url.pathname === "/api/mis/agent-gateway/memories/propose"
    ) {
      send(response, 201, {
        memory: {
          memory_id: `mem_${taskId}`,
          run_id: runId,
          review_status: "candidate",
        },
        outcome: "created",
        token_omitted: true,
      });
      return;
    }
    if (
      request.method === "POST"
      && url.pathname === "/api/mis/agent-gateway/audit"
    ) {
      send(response, 201, {
        emitted: true,
        audit_id: `aud_${taskId}_${state.requests.length}`,
        outcome: "created",
        token_omitted: true,
      });
      return;
    }
    if (
      request.method === "POST"
      && url.pathname === "/api/mis/agent-gateway/plan-evidence-manifests"
    ) {
      send(response, 201, {
        manifest: {
          manifest_id: `pem_${taskId}`,
          status: "verified",
        },
        verification: { pass: true, failed_checks: [] },
        outcome: "created",
        token_omitted: true,
      });
      return;
    }
    if (
      request.method === "POST"
      && url.pathname === "/api/mis/agent-gateway/approvals/request"
    ) {
      send(response, 201, {
        operation: "customer_delivery_approval_request",
        control_plane: "typescript_postgres",
        outcome: "created",
        approval: {
          approval_id: `ap_${taskId}`,
          approval_kind: "customer_delivery",
          task_id: taskId,
          run_id: runId,
          requested_by_agent_id: AGENT_ID,
          approver_user_id: null,
          decision: "pending",
        },
        plan_evidence: { pass: true },
        token_omitted: true,
      });
      return;
    }
    if (
      request.method === "POST"
      && url.pathname === "/api/mis/agent-gateway/heartbeat"
    ) {
      send(response, 200, {
        agent_id: AGENT_ID,
        status: body.status,
        token_omitted: true,
      });
      return;
    }
    send(response, 404, { error: "route_not_found" });
  } catch (error) {
    send(response, 500, {
      error: "contract_server_failure",
      error_type: error instanceof Error ? error.name : "Error",
    });
  }
}

class ContractAdapter implements RuntimeAdapter {
  readonly runtime = "hermes" as const;
  readonly modelName = "contract-hermes";
  calls = 0;
  prompts: string[] = [];
  readonly #attested: boolean;

  constructor(attested = true) {
    this.#attested = attested;
  }

  async execute(bundle: PromptBundle): Promise<RuntimeAdapterResult> {
    this.calls += 1;
    this.prompts.push(bundle.prompt);
    assert.equal(bundle.prompt.includes(TASK_CANARY), false);
    assert.equal(containsProtectedMaterial(bundle.prompt), false);
    return {
      ok: true,
      runtime: "hermes",
      modelName: this.modelName,
      outputSummary: `Untrusted provider-visible output: ${OUTPUT_CANARY}.`,
      rawPayloadHash: stableHash({ contract: "runtime", call: this.calls }),
      targetResource: "hermes://contract/runtime",
      durationMs: 12,
      outputTokens: 16,
      providerCallPerformed: this.#attested,
      dryRun: false,
      retryable: false,
      errorType: null,
      errorMessage: `Untrusted provider-visible error: ${ERROR_CANARY}.`,
    };
  }
}

async function workerSources() {
  const root = path.resolve("src/worker");
  const names = (await readdir(root)).filter((name) => name.endsWith(".ts"));
  return Promise.all(
    names.map(async (name) => ({
      name,
      source: await readFile(path.join(root, name), "utf8"),
    })),
  );
}

async function sourceBoundaryContract() {
  const sources = await workerSources();
  const combined = sources.map((item) => item.source).join("\n");
  const [cliSource, realAcceptanceSource] = await Promise.all([
    readFile(
      new URL("./commercial-worker.ts", import.meta.url),
      "utf8",
    ),
    readFile(
      new URL(
        "../../../scripts/nextjs_postgres_real_worker_human_review_smoke.py",
        import.meta.url,
      ),
      "utf8",
    ),
  ]);
  assert.doesNotMatch(
    combined,
    /\b(?:python3?|sqlite3?|agentops_mis_cli)\b/i,
    "commercial worker source depends on the legacy Python/SQLite stack",
  );
  const orchestrator = sources.find(
    (item) => item.name === "commercialWorker.ts",
  )?.source || "";
  assert.doesNotMatch(orchestrator, /from\s+["']pg["']|SELECT\s|INSERT\s|UPDATE\s/i);
  assert.match(orchestrator, /\/api\/mis\/agent-gateway/);
  assert.match(orchestrator, /provider_call_performed/);
  assert.match(orchestrator, /PROVIDER_RESPONSE_OMITTED|PROVIDER_SUCCESS_SUMMARY/);
  assert.doesNotMatch(
    orchestrator,
    /output_summary:\s*redactText\(result\.outputSummary/,
  );
  assert.match(orchestrator, /plan-evidence-manifests/);
  assert.match(cliSource, /gateway_credentials_must_come_from_environment/);
  assert.match(cliSource, /process\.env\.AGENTOPS_AGENT_TOKEN/);
  assert.doesNotMatch(cliSource, /values\.get\("--(?:api-key|token)/);
  assert.match(realAcceptanceSource, /default="typescript"/);
  assert.match(realAcceptanceSource, /commercial-worker\.ts/);
  assert.doesNotMatch(realAcceptanceSource, /agent_worker\.py/);
  assert.match(
    realAcceptanceSource,
    /nextjs_postgres_real_worker_human_review_v4/,
  );
  assert.match(realAcceptanceSource, /"--estimated-cost-usd"/);
  assert.match(realAcceptanceSource, /configure:workspace-entitlement/);
  assert.match(realAcceptanceSource, /"--max-concurrent-runs"/);
  assert.match(
    realAcceptanceSource,
    /entitlement_admin_forbidden_dml_verified/,
  );
  assert.match(
    realAcceptanceSource,
    /subprocess_environment_scrub_verified/,
  );
  assert.match(realAcceptanceSource, /assert_harness_safety_helper_contracts\(\)/);
  assert.match(realAcceptanceSource, /name\.startswith\("POSTGRES_"\)/);
  assert.match(realAcceptanceSource, /sqlstate.*42501|42501.*sqlstate/s);
  assert.doesNotMatch(realAcceptanceSource, /prompt_secret/);
  assert.match(
    realAcceptanceSource,
    /"insert"[\s\S]*"update"[\s\S]*"delete"[\s\S]*"truncate"[\s\S]*"ddl"/,
  );
  assert.match(realAcceptanceSource, /process_stop_receipt = stop_process/);
  assert.match(
    realAcceptanceSource,
    /fixture_cleanup_verified_before_success/,
  );
  assert.match(realAcceptanceSource, /next_artifact_identity_verified/);
  assert.match(realAcceptanceSource, /candidate_source_worktree_not_clean/);
  assert.match(realAcceptanceSource, /"source_commit"/);
  assert.match(realAcceptanceSource, /"python_worker_started"/);
  assert.match(realAcceptanceSource, /"typescript_worker_started"/);
  return { files: sources.length, python_dependency: false, sqlite_dependency: false };
}

function bodyFor(pathname: string, requests: RecordedRequest[]) {
  const match = requests.find((item) => item.path === pathname);
  assert.ok(match, `request missing: ${pathname}`);
  return match.body;
}

async function main() {
  const sourceBoundary = await sourceBoundaryContract();
  assert.throws(
    () => validateGatewayBaseUrl("http://example.com"),
    /agent_gateway_https_required/,
  );
  assert.throws(
    () => validateGatewayBaseUrl("http://127.0.0.1:3001"),
    /agent_gateway_https_required/,
  );
  assert.equal(
    validateGatewayBaseUrl("http://127.0.0.1:3001", true).protocol,
    "http:",
  );

  const server = createServer((request, response) => {
    void handle(request, response);
  });
  await new Promise<void>((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => resolve());
  });
  const address = server.address();
  assert.ok(address && typeof address === "object");
  const baseUrl = `http://127.0.0.1:${address.port}`;
  try {
    const hermes = new HermesAdapter({
      gatewayUrl: baseUrl,
      model: "hermes-agent",
      timeoutMs: 5_000,
      maxTokens: 128,
    });
    const hermesResult = await hermes.execute({
      prompt: "Return one bounded contract response.",
      promptHash: stableHash("Return one bounded contract response."),
      profile: {
        profileId: "contract",
        version: "worker_prompt_profiles_v1",
        profileHash: stableHash("contract"),
        objective: "Verify the Hermes endpoint remains on the configured origin.",
        outputContract: ["bounded_response"],
      },
    });
    assert.equal(hermesResult.ok, true);
    assert.equal(hermesResult.providerCallPerformed, true);
    assert.equal(hermesResult.dryRun, false);
    assert.equal(hermesResult.outputSummary.includes("Bounded contract response"), false);
    assert.match(hermesResult.outputSummary, /Provider response omitted/);
    assert.equal(state.hermesTargetRequests, 1);
    assert.match(hermesResult.targetResource, /\/v1\/chat\/completions$/);

    const gateway = new HttpGatewayClient({
      baseUrl,
      workspaceId: WORKSPACE_ID,
      agentId: AGENT_ID,
      token: TOKEN,
      allowInsecureLoopback: true,
    });
    const beforeConfirmGuard = state.requests.length;
    const guardedAdapter = new ContractAdapter();
    const guardedWorker = new CommercialWorker(gateway, guardedAdapter, {
      workspaceId: WORKSPACE_ID,
      agentId: AGENT_ID,
      runtime: "hermes",
      estimatedCostUsd: "1.000000",
      confirmRun: false,
    });
    await assert.rejects(
      () => guardedWorker.runOnce(),
      /confirm_run_required_before_gateway_pull/,
    );
    assert.equal(state.requests.length, beforeConfirmGuard);
    assert.equal(guardedAdapter.calls, 0);

    await assert.rejects(
      () => gateway.get("/api/mis/agent-gateway/forbidden"),
      (error: unknown) => {
        assert.ok(error instanceof GatewayHttpError);
        assert.equal(error.code, "unauthorized");
        assert.equal(error.message.includes(TOKEN), false);
        return true;
      },
    );
    await assert.rejects(
      () => gateway.get("/api/mis/agent-gateway/redirect"),
      (error: unknown) => {
        assert.ok(error instanceof GatewayHttpError);
        assert.equal(error.code, "redirect_forbidden");
        return true;
      },
    );
    assert.equal(state.redirectTargetRequests, 0);

    state.scenario = "happy";
    state.requests = [];
    const adapter = new ContractAdapter();
    const worker = new CommercialWorker(gateway, adapter, {
      workspaceId: WORKSPACE_ID,
      agentId: AGENT_ID,
      runtime: "hermes",
      estimatedCostUsd: "1.000000",
      confirmRun: true,
      requestCustomerDeliveryApproval: true,
      maxAdapterAttempts: 2,
      retryDelayMs: 0,
    });
    const happy = await worker.runOnce();
    const happyRequests = [...state.requests];
    assert.equal(happy.ok, true);
    assert.equal(happy.processed, true);
    assert.equal(happy.reason, "completed");
    assert.equal(happy.provider_call_performed, true);
    assert.equal(happy.dry_run, false);
    assert.equal(happy.ledger_evidence_complete, true);
    assert.equal(happy.manual_reconciliation_required, false);
    assert.equal(happy.plan_evidence_pass, true);
    assert.equal(happy.customer_delivery_approval_requested, true);
    assert.equal(happy.customer_delivery_approval_outcome, "created");
    assert.equal(
      happy.customer_delivery_approval_control_plane,
      "typescript_postgres",
    );
    assert.equal(adapter.calls, 1);
    assert.equal(JSON.stringify(happy).includes(TOKEN), false);
    assert.equal(JSON.stringify(happy).includes(TASK_CANARY), false);
    assert.equal(JSON.stringify(happy).includes(OUTPUT_CANARY), false);
    assert.equal(JSON.stringify(happy).includes(ERROR_CANARY), false);
    assert.equal(happy.output_summary, OMITTED_SUMMARY);
    assert.equal(JSON.stringify(happyRequests).includes(TOKEN), false);
    assert.equal(JSON.stringify(happyRequests).includes(OUTPUT_CANARY), false);
    assert.equal(JSON.stringify(happyRequests).includes(ERROR_CANARY), false);
    const runStartBody = bodyFor(
      "/api/mis/agent-gateway/runs/start",
      happyRequests,
    );
    assert.equal(runStartBody.estimated_cost_usd, "1.000000");
    const runHeartbeat = happyRequests.find((item) => (
      item.path.startsWith("/api/mis/agent-gateway/runs/")
      && item.path.endsWith("/heartbeat")
    ));
    assert.ok(runHeartbeat);
    assert.equal(runHeartbeat.body.cost_usd, "1.000000");

    const runtimeBody = bodyFor(
      "/api/mis/agent-gateway/runtime-events",
      happyRequests,
    );
    const runtimeMetadata = runtimeBody.metadata as Record<string, unknown>;
    assert.equal(runtimeMetadata.provider_call_performed, true);
    assert.equal(runtimeMetadata.dry_run, false);
    assert.equal(
      runtimeMetadata.secret_boundary,
      "trusted_worker_client_v1",
    );
    assert.equal(
      runtimeMetadata.credential_transport,
      "trusted_worker_client_only",
    );
    assert.equal(
      runtimeMetadata.prompt_profile_version,
      "worker_prompt_profiles_v1",
    );
    assert.equal(runtimeBody.output_summary, OMITTED_SUMMARY);
    assert.equal(runtimeBody.error_message, null);
    const toolBody = bodyFor("/api/mis/agent-gateway/tool-calls", happyRequests);
    const toolArgs = toolBody.args as Record<string, unknown>;
    assert.equal(toolArgs.provider_call_performed, true);
    assert.equal(toolArgs.dry_run, false);
    const evaluationBody = bodyFor(
      "/api/mis/agent-gateway/evaluations/submit",
      happyRequests,
    );
    const rubric = evaluationBody.rubric as Record<string, unknown>;
    assert.equal(rubric.provider_call_performed, true);
    assert.equal(rubric.dry_run, false);
    assert.equal(evaluationBody.pass_fail, "pass");
    const auditBody = bodyFor("/api/mis/agent-gateway/audit", happyRequests);
    const auditMetadata = auditBody.metadata as Record<string, unknown>;
    assert.equal(auditMetadata.implementation, "typescript");
    assert.equal(auditMetadata.provider_call_performed, true);
    assert.equal(auditMetadata.dry_run, false);
    const manifestBody = bodyFor(
      "/api/mis/agent-gateway/plan-evidence-manifests",
      happyRequests,
    );
    assert.deepEqual(manifestBody.audit_ids, []);
    const approvalBody = bodyFor(
      "/api/mis/agent-gateway/approvals/request",
      happyRequests,
    );
    assert.equal(approvalBody.approval_kind, "customer_delivery");
    assert.equal(approvalBody.decision, "pending");

    state.scenario = "external";
    state.requests = [];
    const external = await worker.runOnce();
    const externalRequests = [...state.requests];
    assert.equal(external.ok, true);
    assert.equal(external.processed, false);
    assert.equal(
      external.reason,
      "external_write_prepared_action_owner_required",
    );
    assert.equal(external.provider_call_performed, false);
    assert.equal(external.dry_run, true);
    assert.equal(adapter.calls, 1);
    assert.equal(
      externalRequests.some(
        (item) => item.path === "/api/mis/agent-gateway/runtime-events",
      ),
      false,
    );
    const externalTool = bodyFor(
      "/api/mis/agent-gateway/tool-calls",
      externalRequests,
    );
    assert.equal(externalTool.status, "waiting_approval");
    assert.equal(
      (externalTool.args as Record<string, unknown>)
        .external_write_runtime_execution_supported,
      false,
    );

    state.scenario = "invalid_attestation";
    state.requests = [];
    const invalidAdapter = new ContractAdapter(false);
    const invalidWorker = new CommercialWorker(gateway, invalidAdapter, {
      workspaceId: WORKSPACE_ID,
      agentId: AGENT_ID,
      runtime: "hermes",
      estimatedCostUsd: "1.000000",
      confirmRun: true,
      maxAdapterAttempts: 1,
      retryDelayMs: 0,
    });
    const invalid = await invalidWorker.runOnce();
    const invalidRequests = [...state.requests];
    assert.equal(invalid.ok, false);
    assert.equal(invalid.reason, "runtime_failed");
    assert.equal(invalid.error_type, "ProviderAttestationInvalid");
    assert.equal(JSON.stringify(invalid).includes(OUTPUT_CANARY), false);
    assert.equal(JSON.stringify(invalid).includes(ERROR_CANARY), false);
    assert.equal(JSON.stringify(invalidRequests).includes(OUTPUT_CANARY), false);
    assert.equal(JSON.stringify(invalidRequests).includes(ERROR_CANARY), false);
    assert.equal(
      invalidRequests.some(
        (item) =>
          item.path === "/api/mis/agent-gateway/plan-evidence-manifests"
          || item.path === "/api/mis/agent-gateway/approvals/request",
      ),
      false,
    );
    const invalidEvaluation = bodyFor(
      "/api/mis/agent-gateway/evaluations/submit",
      invalidRequests,
    );
    assert.equal(invalidEvaluation.pass_fail, "fail");

    state.scenario = "ledger_failure";
    state.requests = [];
    const reconciliationAdapter = new ContractAdapter();
    const reconciliationWorker = new CommercialWorker(
      gateway,
      reconciliationAdapter,
      {
        workspaceId: WORKSPACE_ID,
        agentId: AGENT_ID,
        runtime: "hermes",
        estimatedCostUsd: "1.000000",
        confirmRun: true,
        maxAdapterAttempts: 1,
        retryDelayMs: 0,
      },
    );
    const reconciliation = await reconciliationWorker.runOnce();
    const reconciliationRequests = [...state.requests];
    assert.equal(reconciliation.ok, false);
    assert.equal(reconciliation.processed, true);
    assert.equal(
      reconciliation.reason,
      "post_provider_evidence_persistence_failed",
    );
    assert.equal(reconciliation.provider_call_performed, true);
    assert.equal(reconciliation.dry_run, false);
    assert.equal(reconciliation.ledger_evidence_complete, false);
    assert.equal(reconciliation.manual_reconciliation_required, true);
    assert.equal(reconciliation.evidence_failure_stage, "runtime_event");
    assert.equal(
      reconciliation.evidence_failure_code,
      "runtime_event_unavailable",
    );
    assert.equal(reconciliation.evidence_failure_status, 503);
    assert.equal(
      reconciliation.error_type,
      "PostProviderEvidencePersistenceFailed",
    );
    assert.equal(reconciliationAdapter.calls, 1);
    assert.equal(
      reconciliationRequests.some(
        (item) =>
          item.path === "/api/mis/agent-gateway/plan-evidence-manifests"
          || item.path === "/api/mis/agent-gateway/approvals/request",
      ),
      false,
    );

    process.stdout.write(`${JSON.stringify({
      ok: true,
      contract: "commercial_typescript_worker_v1",
      implementation_language: "typescript",
      source_boundary: sourceBoundary,
      happy_path: {
        gateway_request_count: happyRequests.length,
        provider_call_performed: happy.provider_call_performed,
        dry_run: happy.dry_run,
        plan_evidence_pass: happy.plan_evidence_pass,
        customer_delivery_approval_requested:
          happy.customer_delivery_approval_requested,
      },
      external_write_guard: {
        provider_call_performed: external.provider_call_performed,
        reason: external.reason,
      },
      invalid_attestation_guard: {
        manifest_created: false,
        approval_requested: false,
        error_type: invalid.error_type,
      },
      post_provider_reconciliation_guard: {
        provider_call_performed: reconciliation.provider_call_performed,
        ledger_evidence_complete: reconciliation.ledger_evidence_complete,
        manual_reconciliation_required:
          reconciliation.manual_reconciliation_required,
        manifest_created: false,
        approval_requested: false,
      },
      redirect_followed: false,
      credentials_omitted: true,
      raw_prompt_omitted: true,
      raw_response_omitted: true,
      untrusted_adapter_output_omitted: true,
    }, null, 2)}\n`);
  } finally {
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }
}

main().catch((error) => {
  process.stderr.write(`${JSON.stringify({
    ok: false,
    contract: "commercial_typescript_worker_v1",
    error_type: error instanceof Error ? error.name : "Error",
    error: error instanceof Error ? error.message : String(error),
    credentials_omitted: true,
  })}\n`);
  process.exitCode = 1;
});
