import {
  createHash,
  randomBytes,
  randomUUID,
  scryptSync,
} from "node:crypto";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { Client, type PoolClient } from "pg";

import {
  agentPlanVerificationHash,
  computeAgentPlanHash,
  verifyAgentPlanRow,
} from "../src/server/controlPlane/agentPlanContract";
import { decideWorkspaceApproval } from "../src/server/controlPlane/approvalDecisions";
import { closeControlPlanePoolForTests } from "../src/server/controlPlane/db";
import {
  recordAgentGatewayArtifact,
  recordAgentGatewayToolCall,
  submitAgentGatewayEvaluation,
} from "../src/server/controlPlane/agentGatewayEvidence";
import {
  emitAgentAudit,
} from "../src/server/controlPlane/agentGatewayEvidenceSupport";
import {
  createAgentGatewayPlanEvidenceManifest,
  verifyCurrentAgentGatewayPlanEvidenceManifest,
} from "../src/server/controlPlane/agentGatewayPlans";
import { establishHumanSession } from "../src/server/controlPlane/humanSession";
import {
  HUMAN_SCRYPT_PARAMS,
} from "../src/server/controlPlane/humanPasswordPolicy";
import { ControlPlaneHttpError } from "../src/server/controlPlane/http";
import {
  claimPreparedActionExecution,
  failPreparedActionExecution,
  getPreparedAction,
  preparePreparedAction,
  resumePreparedActionExecution,
} from "../src/server/controlPlane/preparedActions";
import {
  POSTGRES_MIGRATION_MANIFEST,
  runPostgresSchemaCommand,
  SCHEMA_CONTRACT,
} from "../src/server/controlPlane/schemaReadiness";

const WORKSPACE_ID = "ws_prepared_action_contract";
const AGENT_ID = "agt_prepared_action_contract";
const OTHER_AGENT_ID = "agt_prepared_action_other";
const USER_ID = "usr_prepared_action_contract";
const APPROVER_ID = "usr_prepared_action_approver";
const OPERATOR_ID = "usr_prepared_action_operator";
const ORIGIN = "https://mis.example.test";
const HOST = "mis.example.test";
const PASSWORD = `${randomBytes(24).toString("base64url")}Aa1!`;
const STEPS = [
  "READ",
  "PLAN",
  "RETRIEVE",
  "COMPARE",
  "EXECUTE",
  "VERIFY",
  "RECORD",
];

type Fixture = {
  suffix: string;
  actionId: string;
  approvalId: string;
  taskId: string;
  runId: string;
  toolCallId: string;
  planId: string;
  planHash: string;
  verificationResultHash: string;
  actionHash: string;
  allowedPaths: string[];
  prepareBody: Record<string, unknown>;
};

type HumanBrowserSession = {
  cookie: string;
  csrf: string;
  userId: string;
};

function require(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(message);
}

function sha(value: string) {
  return createHash("sha256").update(value, "utf8").digest("hex");
}

function request(
  token: string | null,
  method: "GET" | "POST",
  body?: Record<string, unknown>,
  options: { workspaceId?: string; agentId?: string } = {},
) {
  const headers = new Headers({
    "x-agentops-workspace-id": options.workspaceId || WORKSPACE_ID,
    "x-agentops-agent-id": options.agentId || AGENT_ID,
  });
  if (token) headers.set("authorization", `Bearer ${token}`);
  if (body) headers.set("content-type", "application/json");
  return new Request("http://agentops.test/api/agent-gateway/prepared-actions", {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });
}

function browserHeaders(
  session: HumanBrowserSession,
  input: {
    csrf?: string;
    idempotencyKey?: string;
    includeOrigin?: boolean;
    machineCredential?: boolean;
  } = {},
) {
  const headers = new Headers({
    cookie: session.cookie,
    host: HOST,
    "x-agentops-workspace-id": WORKSPACE_ID,
  });
  if (input.includeOrigin !== false) headers.set("origin", ORIGIN);
  if (input.csrf !== undefined) headers.set("x-agentops-csrf", input.csrf);
  if (input.idempotencyKey) {
    headers.set("idempotency-key", input.idempotencyKey);
  }
  if (input.machineCredential) {
    headers.set("authorization", "Bearer machine-not-human");
  }
  return headers;
}

function humanDecisionRequest(
  session: HumanBrowserSession,
  approvalId: string,
  decision: "approve" | "reject",
  idempotencyKey: string,
  options: {
    csrf?: string;
    includeOrigin?: boolean;
    machineCredential?: boolean;
  } = {},
) {
  return new Request(
    `${ORIGIN}/api/mis/approvals/${approvalId}/${decision}`,
    {
      method: "POST",
      headers: browserHeaders(session, {
        csrf: options.csrf ?? session.csrf,
        idempotencyKey,
        includeOrigin: options.includeOrigin,
        machineCredential: options.machineCredential,
      }),
      body: JSON.stringify({ workspace_id: WORKSPACE_ID }),
    },
  );
}

async function login(username: string, userId: string) {
  const result = await establishHumanSession(
    new Headers({ origin: ORIGIN, host: HOST }),
    { username, password: PASSWORD },
  );
  require(result.status === 200, "Human login failed");
  const csrf = String(result.body.csrf_token || "");
  require(/^[a-f0-9]{64}$/.test(csrf), "Human CSRF token missing");
  return {
    cookie: result.setCookie.split(";", 1)[0],
    csrf,
    userId,
  };
}

async function expectCode(
  expected: string,
  work: () => Promise<unknown>,
) {
  try {
    await work();
  } catch (error) {
    require(error instanceof ControlPlaneHttpError, `${expected}: wrong error type`);
    require(
      error.code === expected,
      `${expected}: received ${error.code}: ${error.message}`,
    );
    return;
  }
  throw new Error(`${expected}: request unexpectedly passed`);
}

async function expectDatabaseGuard(
  expectedMessage: string,
  work: () => Promise<unknown>,
) {
  try {
    await work();
  } catch (error) {
    const databaseError = error as { code?: string; message?: string };
    require(
      databaseError.code === "23514"
        && databaseError.message === expectedMessage,
      `${expectedMessage}: unexpected database error`,
    );
    return;
  }
  throw new Error(`${expectedMessage}: database mutation unexpectedly passed`);
}

function scopedDsn(baseDsn: string, schema: string) {
  const url = new URL(baseDsn);
  url.searchParams.set("options", `-csearch_path=${schema}`);
  return url.toString();
}

async function sourceContract() {
  const scriptPath = fileURLToPath(import.meta.url);
  const appRoot = path.resolve(path.dirname(scriptPath), "..");
  const route = await readFile(
    path.join(
      appRoot,
      "app/api/mis/agent-gateway/prepared-actions/[actionId]/[[...operation]]/route.ts",
    ),
    "utf8",
  );
  const collectionRoute = await readFile(
    path.join(
      appRoot,
      "app/api/mis/agent-gateway/prepared-actions/route.ts",
    ),
    "utf8",
  );
  const humanRoute = await readFile(
    path.join(
      appRoot,
      "app/api/mis/approvals/[approvalId]/[decision]/route.ts",
    ),
    "utf8",
  );
  const service = await readFile(
    path.join(appRoot, "src/server/controlPlane/preparedActions.ts"),
    "utf8",
  );
  const nextConfig = await readFile(
    path.join(appRoot, "next.config.mjs"),
    "utf8",
  );
  require(
    route.includes("getPreparedAction")
      && route.includes("claimPreparedActionExecution")
      && route.includes("failPreparedActionExecution")
      && route.includes("resumePreparedActionExecution"),
    "PreparedAction route is not wired to all Worker operations",
  );
  require(
    collectionRoute.includes("preparePreparedAction")
      && collectionRoute.includes("legacyPythonProxyAllowed")
      && collectionRoute.includes("prepared_action_postgres_owner_required"),
    "PreparedAction collection route does not preserve production ownership and Free Local rollback",
  );
  require(
    humanRoute.includes("decideWorkspaceApproval")
      && humanRoute.includes("human_session_direct_route_required"),
    "PreparedAction Human decision must use the existing Human Session route",
  );
  require(
    route.includes("prepared_action_postgres_owner_required")
      && !route.includes("proxyControlPlaneRequest")
      && !route.toLowerCase().includes("python"),
    "PreparedAction route must be a direct TypeScript/Postgres owner",
  );
  require(
    !service.includes("child_process")
      && !service.includes("spawn(")
      && !service.toLowerCase().includes(".py"),
    "PreparedAction service must not import, spawn, or proxy Python",
  );
  require(
    nextConfig.includes('source: "/api/agent-gateway/:path*"')
      && nextConfig.includes('destination: "/api/mis/agent-gateway/:path*"'),
    "Worker-compatible /api/agent-gateway rewrite is missing",
  );
}

async function seedIdentity(
  client: Client,
  token: string,
  otherToken: string,
) {
  const now = new Date();
  const expiresAt = new Date(now.getTime() + 3_600_000).toISOString();
  for (const [userId, username, membershipRole] of [
    [USER_ID, "prepared-owner", "owner"],
    [APPROVER_ID, "prepared-approver", "approver"],
    [OPERATOR_ID, "prepared-operator", "operator"],
  ]) {
    const salt = randomBytes(16);
    const passwordHash = scryptSync(
      PASSWORD,
      salt,
      HUMAN_SCRYPT_PARAMS.keylen,
      {
        N: HUMAN_SCRYPT_PARAMS.n,
        r: HUMAN_SCRYPT_PARAMS.r,
        p: HUMAN_SCRYPT_PARAMS.p,
        maxmem: 128 * 1024 * 1024,
      },
    ).toString("hex");
    await client.query(
      `INSERT INTO users(user_id,name,email,role,created_at)
      VALUES($1,$2,$3,$4,$5)`,
      [
        userId,
        username,
        `${username}@example.invalid`,
        membershipRole,
        now.toISOString(),
      ],
    );
    await client.query(
      `INSERT INTO workspace_memberships(
        workspace_id,user_id,role,status,created_at,updated_at
      ) VALUES($1,$2,$3,'active',$4,$4)`,
      [WORKSPACE_ID, userId, membershipRole, now.toISOString()],
    );
    await client.query(
      `INSERT INTO human_login_credentials(
        credential_id,user_id,username,password_hash,password_salt,
        password_params_json,status,created_at,updated_at,last_login_at
      ) VALUES($1,$2,$3,$4,$5,$6,'active',$7,$7,NULL)`,
      [
        `cred_${username}`,
        userId,
        username,
        passwordHash,
        salt.toString("hex"),
        JSON.stringify(HUMAN_SCRYPT_PARAMS),
        now.toISOString(),
      ],
    );
  }
  for (const [agentId, name] of [
    [AGENT_ID, "PreparedAction Contract Agent"],
    [OTHER_AGENT_ID, "Other Contract Agent"],
  ]) {
    await client.query(
      `INSERT INTO agents(
        agent_id,name,role,description,runtime_type,model_provider,model_name,
        status,permission_level,allowed_tools,budget_limit_usd,owner_user_id,
        created_at,updated_at
      ) VALUES(
        $1,$2,'builder','Contract fixture','codex','codex','contract',
        'idle','high','[]',0,$3,$4,$4
      )`,
      [agentId, name, USER_ID, now.toISOString()],
    );
  }
  await client.query(
    `INSERT INTO workspace_entitlements(
      workspace_id,edition,status,capabilities_json,max_agents,
      max_active_enrollments,max_active_sessions_per_agent,max_monthly_runs,
      max_monthly_cost_usd,effective_at,expires_at,max_concurrent_runs
    ) VALUES(
      $1,'enterprise_byoc','active',jsonb_build_object('run_start',true),
      20,20,5,100,1000::numeric,$2,$3,20
    )`,
    [
      WORKSPACE_ID,
      new Date(now.getTime() - 60_000).toISOString(),
      new Date(now.getTime() + 3_600_000).toISOString(),
    ],
  );
  for (const [tokenId, supplied, agentId] of [
    ["tok_prepared_action_contract", token, AGENT_ID],
    ["tok_prepared_action_other", otherToken, OTHER_AGENT_ID],
  ]) {
    await client.query(
      `INSERT INTO agent_gateway_tokens(
        token_id,token_hash,workspace_id,agent_id,scopes_json,status,label,
        heartbeat_timeout_sec,created_at,expires_at,revoked_at,last_used_at,
        last_heartbeat_at
      ) VALUES(
        $1,$2,$3,$4,$5,'active','prepared-action-contract',300,$6,$7,
        NULL,NULL,NULL
      )`,
      [
        tokenId,
        sha(supplied),
        WORKSPACE_ID,
        agentId,
        JSON.stringify([
          "tasks:read",
          "toolcalls:write",
          "evaluations:submit",
          "artifacts:write",
          "audit:write",
          "plan_evidence:write",
        ]),
        now.toISOString(),
        expiresAt,
      ],
    );
  }
}

async function seedPreparedAction(
  client: Client,
  token: string,
  suffix: string,
): Promise<Fixture> {
  const now = new Date();
  const createdAt = new Date(now.getTime() - 20_000).toISOString();
  const verifiedAt = new Date(now.getTime() - 15_000).toISOString();
  const decidedAt = new Date(now.getTime() - 10_000).toISOString();
  const taskId = `tsk_pa_${suffix}`;
  const runId = `run_pa_${suffix}`;
  const planId = `plan_pa_${suffix}`;
  const toolCallId = `tc_pa_${suffix}`;
  const allowedPaths = [`src/${suffix}.ts`];
  const planContract = {
    workspace_id: WORKSPACE_ID,
    task_id: taskId,
    run_id: null,
    agent_id: AGENT_ID,
    task_understanding: `Produce a bounded workspace diff for ${suffix}.`,
    referenced_specs: ["PROJECT_SPEC.md"],
    referenced_memories: ["project-memory:prepared-action"],
    referenced_bases: ["base_local_tasks"],
    proposed_files_to_change: allowedPaths,
    risk_level: "high",
    approval_required: true,
    execution_steps: STEPS,
    verification_plan: "Verify the exact changed paths and diff evidence hash.",
    rollback_plan: "Retain the isolated worktree and block automatic retry.",
    plan_version: 1,
  };
  const verifiablePlan = {
    workspace_id: WORKSPACE_ID,
    task_id: taskId,
    run_id: null,
    agent_id: AGENT_ID,
    task_understanding: planContract.task_understanding,
    referenced_specs_json: JSON.stringify(planContract.referenced_specs),
    referenced_memories_json: JSON.stringify(
      planContract.referenced_memories,
    ),
    referenced_bases_json: JSON.stringify(planContract.referenced_bases),
    proposed_files_to_change_json: JSON.stringify(
      planContract.proposed_files_to_change,
    ),
    risk_level: planContract.risk_level,
    approval_required: 1,
    execution_steps_json: JSON.stringify(STEPS),
    verification_plan: planContract.verification_plan,
    rollback_plan: planContract.rollback_plan,
    plan_version: 1,
  };
  const planHash = computeAgentPlanHash(verifiablePlan);
  const planVerification = verifyAgentPlanRow({
    ...verifiablePlan,
    plan_hash: planHash,
  });
  const verificationResultHash = agentPlanVerificationHash(
    planId,
    planVerification,
  );
  const sourceRepoHash = sha(`source:${suffix}`);
  const baselineHead = sha(`head:${suffix}`).slice(0, 40);
  const targetResource =
    `git+local://sha256/${sourceRepoHash}@${baselineHead}`;
  const runtimeAttestation = {
    attested: true,
    binary_sha256: sha("codex-binary"),
    version_summary: "codex contract",
  };
  const normalizedArgs = {
    task_id: taskId,
    run_id: runId,
    adapter: "codex",
    external_write_intent: true,
    execution_mode: "workspace-write",
    target_resource: targetResource,
    requires_prepared_action_for_external_write: true,
    agent_plan_id: planId,
    agent_plan_hash: planHash,
    agent_plan_verification_result_hash: verificationResultHash,
    source_repo_hash: sourceRepoHash,
    baseline_head: baselineHead,
    allowed_paths: allowedPaths,
    source_repo_clean: true,
    workspace_isolation: "managed_detached_git_worktree",
    rollback_strategy: "remove_managed_worktree_before_promotion",
    runtime_attestation: runtimeAttestation,
    raw_prompt_omitted: true,
    raw_response_omitted: true,
    token_omitted: true,
  };
  const normalizedArgsJson = JSON.stringify(normalizedArgs);
  const checkpoint = {
    checkpoint: "before_codex_workspace_write_execution",
    task_id: taskId,
    run_id: runId,
    adapter: "codex",
    agent_plan_id: planId,
    baseline_head: baselineHead,
    allowed_paths: allowedPaths,
    runtime_attestation: runtimeAttestation,
  };

  await client.query("BEGIN");
  try {
    await client.query(
      `INSERT INTO tasks(
        task_id,workspace_id,title,description,requester_id,owner_agent_id,
        collaborator_agent_ids,status,priority,due_date,acceptance_criteria,
        risk_level,budget_limit_usd,created_at,updated_at
      ) VALUES(
        $1,$2,$3,'Contract PreparedAction task',$4,$5,'[]',
        'waiting_approval','high',NULL,'Verified bounded diff','high',0,$6,$6
      )`,
      [
        taskId,
        WORKSPACE_ID,
        `PreparedAction ${suffix}`,
        USER_ID,
        AGENT_ID,
        createdAt,
      ],
    );
    await client.query(
      `INSERT INTO agent_plans(
        plan_id,workspace_id,task_id,run_id,agent_id,task_understanding,
        referenced_specs_json,referenced_memories_json,referenced_bases_json,
        proposed_files_to_change_json,risk_level,approval_required,
        execution_steps_json,verification_plan,rollback_plan,status,
        plan_version,plan_hash,verified_at,verification_result_hash,
        approval_id,approved_by_user_id,approved_at,created_at,updated_at
      ) VALUES(
        $1,$2,$3,NULL,$4,$5,$6,$7,$8,$9,'high',1,$10,$11,$12,
        'approved',1,$13,$14,$15,NULL,$16,$17,$18,$18
      )`,
      [
        planId,
        WORKSPACE_ID,
        taskId,
        AGENT_ID,
        planContract.task_understanding,
        JSON.stringify(planContract.referenced_specs),
        JSON.stringify(planContract.referenced_memories),
        JSON.stringify(planContract.referenced_bases),
        JSON.stringify(planContract.proposed_files_to_change),
        JSON.stringify(STEPS),
        planContract.verification_plan,
        planContract.rollback_plan,
        planHash,
        verifiedAt,
        verificationResultHash,
        USER_ID,
        decidedAt,
        createdAt,
      ],
    );
    await client.query(
      `SELECT reservation_id
      FROM agentops_reserve_run_cost_v10(
        $1,$2,5.000000::numeric,$3,$4,interval '1 hour'
      )`,
      [
        WORKSPACE_ID,
        runId,
        sha(`prepared-reservation:${runId}`),
        sha(`prepared-reservation-request:${runId}`),
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
        $1,$2,$3,$4,'codex','waiting_approval',$5,NULL,NULL,
        'Bounded workspace write',NULL,'codex','contract',0,0,0,0,
        NULL,NULL,$6,NULL,NULL,1,$7,$8,$5
      )`,
      [
        runId,
        WORKSPACE_ID,
        taskId,
        AGENT_ID,
        createdAt,
        `trace_${suffix}`,
        planId,
        planHash,
      ],
    );
    await client.query(
      `INSERT INTO tool_calls(
        tool_call_id,run_id,agent_id,tool_name,tool_version,tool_category,
        normalized_args_json,target_resource,risk_level,status,result_summary,
        side_effect_id,started_at,ended_at,created_at
      ) VALUES(
        $1,$2,$3,'agent_worker.codex.workspace_write','v1','custom',$4,$5,
        'high','waiting_approval',NULL,NULL,$6,NULL,$6
      )`,
      [
        toolCallId,
        runId,
        AGENT_ID,
        normalizedArgsJson,
        targetResource,
        createdAt,
      ],
    );
    await client.query("COMMIT");
  } catch (error) {
    await client.query("ROLLBACK");
    throw error;
  }
  const prepareBody = {
    workspace_id: WORKSPACE_ID,
    agent_id: AGENT_ID,
    task_id: taskId,
    run_id: runId,
    tool_call_id: toolCallId,
    action_type: "agent_worker.codex.workspace_write",
    normalized_args: normalizedArgs,
    target_resource: targetResource,
    risk_level: "high",
    policy_version: "approval-wall-codex-workspace-write-v2",
    checkpoint,
    idempotency_key: `prepared-action-${suffix}-0001`,
    expires_in_seconds: 3_600,
    reason: "Contract PreparedAction requires Human approval.",
  };
  const prepared = await preparePreparedAction(
    request(token, "POST", prepareBody),
    prepareBody,
  );
  require(
    prepared.status === 201 && prepared.body.outcome === "created",
    `PreparedAction ${suffix} was not created through the owner`,
  );
  const action = prepared.body.prepared_action as Record<string, unknown>;
  const approval = prepared.body.approval as Record<string, unknown>;
  const actionId = String(action.action_id || "");
  const approvalId = String(approval.approval_id || "");
  const actionHash = String(action.action_hash || "");
  require(actionId && approvalId, "PreparedAction owner omitted authority ids");
  require(
    action.status === "prepared" && approval.decision === "pending",
    "PreparedAction owner did not create pending Human authority",
  );
  return {
    suffix,
    actionId,
    approvalId,
    taskId,
    runId,
    toolCallId,
    planId,
    planHash,
    verificationResultHash,
    actionHash,
    allowedPaths,
    prepareBody,
  };
}

async function seedSuccessEvidence(
  token: string,
  fixture: Fixture,
  leaseId: string,
  options: {
    manifestId?: string;
    declaredToolIds?: string[];
    expectVerified?: boolean;
  } = {},
) {
  const now = new Date().toISOString();
  const verifierToolId = `tc_verify_${fixture.suffix}`;
  const evaluationId = `eval_${fixture.suffix}`;
  const artifactId = `art_${fixture.suffix}`;
  const manifestId = options.manifestId || `pem_${fixture.suffix}`;
  const diffEvidenceHash = sha(`diff:${fixture.suffix}`);
  const providerSideEffectId = `codex-diff-${diffEvidenceHash.slice(0, 24)}`;
  const verifier = await recordAgentGatewayToolCall(
    request(token, "POST", {
      workspace_id: WORKSPACE_ID,
      agent_id: AGENT_ID,
      task_id: fixture.taskId,
      run_id: fixture.runId,
      tool_call_id: verifierToolId,
      tool_name: "agent_worker.codex.workspace_diff_verify",
      tool_version: "v1",
      tool_category: "custom",
      args: {
        task_id: fixture.taskId,
        agent_plan_id: fixture.planId,
        prepared_action_id: fixture.actionId,
        execution_lease_id: leaseId,
        diff_evidence_hash: diffEvidenceHash,
        changed_paths: fixture.allowedPaths,
        allowed_paths: fixture.allowedPaths,
        head_unchanged: true,
        raw_diff_omitted: true,
        raw_content_omitted: true,
        raw_prompt_omitted: true,
        raw_response_omitted: true,
        token_omitted: true,
      },
      target_resource: `worktree://${fixture.actionId}/diff-evidence`,
      risk_level: "medium",
      status: "completed",
      result_summary: "Verified bounded diff.",
      started_at: now,
      ended_at: now,
    }),
  );
  require(
    verifier.status === 201
      && (verifier.body.tool_call as Record<string, unknown>).status
        === "completed",
    "Verifier tool was not recorded through the TypeScript/Postgres owner",
  );
  const evaluation = await submitAgentGatewayEvaluation(
    request(token, "POST", {
      workspace_id: WORKSPACE_ID,
      agent_id: AGENT_ID,
      task_id: fixture.taskId,
      run_id: fixture.runId,
      evaluation_id: evaluationId,
      evaluator_type: "rule",
      score: 1,
      pass_fail: "pass",
      rubric: {
        gate: "codex_governed_workspace_write",
        prepared_action_id: fixture.actionId,
        execution_lease_id: leaseId,
        diff_evidence_hash: diffEvidenceHash,
        quality_gate_pass: true,
        raw_diff_omitted: true,
        raw_prompt_omitted: true,
        raw_response_omitted: true,
        token_omitted: true,
      },
      notes: "Verified governed diff.",
    }),
  );
  require(
    evaluation.status === 201
      && (evaluation.body.evaluation as Record<string, unknown>).pass_fail
        === "pass",
    "Evaluation was not recorded through the TypeScript/Postgres owner",
  );
  const artifact = await recordAgentGatewayArtifact(
    request(token, "POST", {
      workspace_id: WORKSPACE_ID,
      agent_id: AGENT_ID,
      task_id: fixture.taskId,
      run_id: fixture.runId,
      artifact_id: artifactId,
      artifact_type: "codex_workspace_diff_evidence",
      title: "Contract diff",
      uri: `worktree://${fixture.actionId}`,
      summary: "Hashed bounded diff; raw diff omitted.",
      content_hash: diffEvidenceHash,
      created_at: now,
    }),
  );
  require(
    artifact.status === 201,
    "Artifact was not recorded through the TypeScript/Postgres owner",
  );
  const audit = await emitAgentAudit(
    request(token, "POST", {
      workspace_id: WORKSPACE_ID,
      agent_id: AGENT_ID,
      task_id: fixture.taskId,
      run_id: fixture.runId,
      action: "agent_worker.codex_workspace_write_completed",
      entity_type: "runs",
      entity_id: fixture.runId,
      metadata: {
        agent_plan_id: fixture.planId,
        prepared_action_id: fixture.actionId,
        approval_id: fixture.approvalId,
        execution_lease_id: leaseId,
        provider_side_effect_id: providerSideEffectId,
        diff_evidence: {
          evidence_hash: diffEvidenceHash,
          changed_paths: fixture.allowedPaths,
        },
        raw_diff_omitted: true,
      },
      after: {
        status: "execution_evidence_recorded",
        provider_side_effect_id: providerSideEffectId,
      },
    }),
  );
  require(
    audit.status === 201 && audit.body.emitted === true,
    "Worker audit was not emitted through the TypeScript/Postgres owner",
  );
  const manifest = await createAgentGatewayPlanEvidenceManifest(
    request(token, "POST", {
      workspace_id: WORKSPACE_ID,
      agent_id: AGENT_ID,
      manifest_id: manifestId,
      plan_id: fixture.planId,
      task_id: fixture.taskId,
      run_id: fixture.runId,
      mismatch_policy: "block",
      expected_steps: STEPS,
      tool_call_ids: options.declaredToolIds
        || [fixture.toolCallId, verifierToolId],
      evaluation_ids: [evaluationId],
      artifact_ids: [artifactId],
      audit_ids: [],
      plan_hash: fixture.planHash,
      verification_result_hash: fixture.verificationResultHash,
      verify_now: true,
    }),
  );
  const manifestBody = manifest.body.manifest as Record<string, unknown>;
  const expectedVerified = options.expectVerified !== false;
  require(
    manifest.status === 201
      && manifestBody.status === (expectedVerified ? "verified" : "blocked"),
    "Manifest did not pass through the expected owner verification state",
  );
  return {
    manifestId,
    manifestStatus: String(manifestBody.status),
    verifierToolId,
    evaluationId,
    artifactId,
    diffEvidenceHash,
    providerSideEffectId,
  };
}

async function main() {
  process.env.AGENTOPS_DEPLOYMENT_MODE = "production";
  process.env.AGENTOPS_CONTROL_PLANE_MODE = "postgres";
  process.env.AGENTOPS_ALLOWED_ORIGINS = ORIGIN;
  process.env.AGENTOPS_HUMAN_SESSION_HMAC_KEY =
    randomBytes(48).toString("base64url");
  await sourceContract();
  const baseDsn = String(process.env.AGENTOPS_POSTGRES_DSN || "").trim();
  require(baseDsn, "AGENTOPS_POSTGRES_DSN is required");
  const schema = `prepared_action_owner_${randomUUID().replaceAll("-", "")}`;
  const baseAdmin = new Client({ connectionString: baseDsn });
  const token = `prepared_action_token_${randomUUID()}`;
  const otherToken = `prepared_action_other_${randomUUID()}`;
  let scopedAdmin: Client | null = null;
  await baseAdmin.connect();
  try {
    await baseAdmin.query(`CREATE SCHEMA "${schema}"`);
    const contractDsn = scopedDsn(baseDsn, schema);
    const migration = await runPostgresSchemaCommand("migrate", {
      connectionString: contractDsn,
    });
    require(
      migration.schema_contract === SCHEMA_CONTRACT
        && migration.applied_count === POSTGRES_MIGRATION_MANIFEST.length
        && migration.manifest_count === POSTGRES_MIGRATION_MANIFEST.length,
      "fresh schema did not apply the current complete manifest",
    );
    process.env.AGENTOPS_POSTGRES_DSN = contractDsn;
    process.env.AGENTOPS_POSTGRES_POOL_MAX = "16";
    scopedAdmin = new Client({ connectionString: contractDsn });
    await scopedAdmin.connect();
    await seedIdentity(scopedAdmin, token, otherToken);
    const owner = await login("prepared-owner", USER_ID);
    const approver = await login("prepared-approver", APPROVER_ID);
    const operator = await login("prepared-operator", OPERATOR_ID);
    const success = await seedPreparedAction(scopedAdmin, token, "success");
    const failure = await seedPreparedAction(scopedAdmin, token, "failure");
    const expiry = await seedPreparedAction(scopedAdmin, token, "expiry");
    const rejectedFixture = await seedPreparedAction(
      scopedAdmin,
      token,
      "human_reject",
    );
    const raceFixture = await seedPreparedAction(
      scopedAdmin,
      token,
      "human_race",
    );
    const claimOwnerDrift = await seedPreparedAction(
      scopedAdmin,
      token,
      "claim_owner_drift",
    );
    const claimRunStateDrift = await seedPreparedAction(
      scopedAdmin,
      token,
      "claim_run_state_drift",
    );
    const claimRunAgentDrift = await seedPreparedAction(
      scopedAdmin,
      token,
      "claim_run_agent_drift",
    );
    const claimToolStatusDrift = await seedPreparedAction(
      scopedAdmin,
      token,
      "claim_tool_status_drift",
    );
    const claimToolEffectDrift = await seedPreparedAction(
      scopedAdmin,
      token,
      "claim_tool_effect_drift",
    );
    const claimPlanDrift = await seedPreparedAction(
      scopedAdmin,
      token,
      "claim_plan_drift",
    );
    const resumeAssignmentDrift = await seedPreparedAction(
      scopedAdmin,
      token,
      "resume_assignment_drift",
    );
    const selectiveEvidence = await seedPreparedAction(
      scopedAdmin,
      token,
      "selective_evidence",
    );
    const crossEvidence = await seedPreparedAction(
      scopedAdmin,
      token,
      "cross_evidence",
    );
    const allFixtures = [
      success,
      failure,
      expiry,
      rejectedFixture,
      raceFixture,
      claimOwnerDrift,
      claimRunStateDrift,
      claimRunAgentDrift,
      claimToolStatusDrift,
      claimToolEffectDrift,
      claimPlanDrift,
      resumeAssignmentDrift,
      selectiveEvidence,
      crossEvidence,
    ];

    const createReplay = await preparePreparedAction(
      request(token, "POST", success.prepareBody),
      success.prepareBody,
    );
    require(
      createReplay.status === 200
        && createReplay.body.outcome === "unchanged"
        && String(
          (createReplay.body.prepared_action as Record<string, unknown>).action_id,
        ) === success.actionId,
      "PreparedAction create replay was not idempotent",
    );
    await expectCode("prepared_action_idempotency_conflict", () =>
      preparePreparedAction(
        request(token, "POST", {
          ...success.prepareBody,
          expires_in_seconds: 3_601,
        }),
        {
          ...success.prepareBody,
          expires_in_seconds: 3_601,
        },
      ));
    await expectCode("prepared_action_current_assignment_invalid", () =>
      preparePreparedAction(
        request(otherToken, "POST", {
          ...success.prepareBody,
          agent_id: OTHER_AGENT_ID,
          idempotency_key: "prepared-other-assignment-0001",
        }, { agentId: OTHER_AGENT_ID }),
        {
          ...success.prepareBody,
          agent_id: OTHER_AGENT_ID,
          idempotency_key: "prepared-other-assignment-0001",
        },
      ));

    await expectCode("machine_credential_not_allowed", () =>
      decideWorkspaceApproval(
        humanDecisionRequest(
          owner,
          success.approvalId,
          "approve",
          "prepared-machine-boundary-0001",
          { machineCredential: true },
        ),
        { workspace_id: WORKSPACE_ID },
        success.approvalId,
        "approve",
      ));
    await expectCode("origin_validation_failed", () =>
      decideWorkspaceApproval(
        humanDecisionRequest(
          owner,
          success.approvalId,
          "approve",
          "prepared-origin-boundary-0001",
          { includeOrigin: false },
        ),
        { workspace_id: WORKSPACE_ID },
        success.approvalId,
        "approve",
      ));
    await expectCode("csrf_validation_failed", () =>
      decideWorkspaceApproval(
        humanDecisionRequest(
          owner,
          success.approvalId,
          "approve",
          "prepared-csrf-boundary-0001",
          { csrf: "0".repeat(64) },
        ),
        { workspace_id: WORKSPACE_ID },
        success.approvalId,
        "approve",
      ));
    await expectCode("human_role_forbidden", () =>
      decideWorkspaceApproval(
        humanDecisionRequest(
          operator,
          success.approvalId,
          "approve",
          "prepared-operator-boundary-0001",
        ),
        { workspace_id: WORKSPACE_ID },
        success.approvalId,
        "approve",
      ));

    const successDecisionKey = "prepared-owner-approve-success-0001";
    const approved = await decideWorkspaceApproval(
      humanDecisionRequest(
        owner,
        success.approvalId,
        "approve",
        successDecisionKey,
      ),
      { workspace_id: WORKSPACE_ID },
      success.approvalId,
      "approve",
    );
    const approvedBody = approved.body as Record<string, unknown>;
    require(
      approvedBody.operation === "prepared_action_approval_decision"
        && approvedBody.outcome === "updated"
        && approvedBody.side_effect_performed === false
        && (approvedBody.prepared_action as Record<string, unknown>).status
          === "approved",
      "Human owner approval did not authorize PreparedAction",
    );
    const approvedReplay = await decideWorkspaceApproval(
      humanDecisionRequest(
        owner,
        success.approvalId,
        "approve",
        successDecisionKey,
      ),
      { workspace_id: WORKSPACE_ID },
      success.approvalId,
      "approve",
    );
    require(
      approvedReplay.body.outcome === "unchanged",
      "Human PreparedAction decision replay was not idempotent",
    );
    const additionallyApproved = [
      failure,
      expiry,
      claimOwnerDrift,
      claimRunStateDrift,
      claimRunAgentDrift,
      claimToolStatusDrift,
      claimToolEffectDrift,
      claimPlanDrift,
      resumeAssignmentDrift,
      selectiveEvidence,
      crossEvidence,
    ];
    for (const [index, fixture] of additionallyApproved.entries()) {
      const session = index % 2 === 0 ? owner : approver;
      const key = `prepared-approve-${fixture.suffix}-0001`;
      const result = await decideWorkspaceApproval(
        humanDecisionRequest(session, fixture.approvalId, "approve", key),
        { workspace_id: WORKSPACE_ID },
        fixture.approvalId,
        "approve",
      );
      const resultBody = result.body as Record<string, unknown>;
      require(
        (resultBody.prepared_action as Record<string, unknown>).status
          === "approved",
        "Human reviewer did not approve PreparedAction fixture",
      );
    }
    const rejected = await decideWorkspaceApproval(
      humanDecisionRequest(
        owner,
        rejectedFixture.approvalId,
        "reject",
        "prepared-owner-reject-0001",
      ),
      { workspace_id: WORKSPACE_ID },
      rejectedFixture.approvalId,
      "reject",
    );
    const rejectedBody = rejected.body as Record<string, unknown>;
    require(
      (rejectedBody.prepared_action as Record<string, unknown>).status
          === "rejected"
        && rejectedBody.side_effect_performed === false,
      "Human PreparedAction rejection did not fail closed",
    );
    const race = await Promise.allSettled([
      decideWorkspaceApproval(
        humanDecisionRequest(
          owner,
          raceFixture.approvalId,
          "approve",
          "prepared-race-owner-approve-0001",
        ),
        { workspace_id: WORKSPACE_ID },
        raceFixture.approvalId,
        "approve",
      ),
      decideWorkspaceApproval(
        humanDecisionRequest(
          approver,
          raceFixture.approvalId,
          "reject",
          "prepared-race-approver-reject-0001",
        ),
        { workspace_id: WORKSPACE_ID },
        raceFixture.approvalId,
        "reject",
      ),
    ]);
    require(
      race.filter((result) => result.status === "fulfilled").length === 1
        && race.filter((result) => result.status === "rejected").length === 1,
      "PreparedAction Human decision race did not have one winner",
    );
    const raceLoser = race.find((result) => result.status === "rejected");
    require(
      raceLoser?.status === "rejected"
        && raceLoser.reason instanceof ControlPlaneHttpError
        && raceLoser.reason.code === "approval_decision_conflict",
      "PreparedAction Human decision race did not fail closed",
    );

    await expectCode("unauthorized", () =>
      getPreparedAction(request(null, "GET"), success.actionId));
    await expectCode("forbidden", () =>
      getPreparedAction(
        request(token, "GET", undefined, { workspaceId: "ws_other" }),
        success.actionId,
      ));
    await expectCode("forbidden", () =>
      getPreparedAction(
        request(otherToken, "GET", undefined, {
          agentId: OTHER_AGENT_ID,
        }),
        success.actionId,
      ));

    const inspect = await getPreparedAction(
      request(token, "GET"),
      success.actionId,
    );
    require(inspect.status === 200, "GET status mismatch");
    require(inspect.body.status === "ready", "approved action is not ready");
    require(
      (inspect.body.hash_verification as Record<string, unknown>).match === true,
      "GET did not verify the current action hash",
    );

    const claimBody = {
      workspace_id: WORKSPACE_ID,
      agent_id: AGENT_ID,
      lease_ttl_seconds: 120,
    };
    await scopedAdmin.query(
      "UPDATE tasks SET owner_agent_id=$1 WHERE task_id=$2",
      [OTHER_AGENT_ID, claimOwnerDrift.taskId],
    );
    await expectCode("prepared_action_current_assignment_invalid", () =>
      claimPreparedActionExecution(
        request(token, "POST", claimBody),
        claimOwnerDrift.actionId,
      ));
    await scopedAdmin.query(
      "UPDATE tasks SET owner_agent_id=$1 WHERE task_id=$2",
      [AGENT_ID, claimOwnerDrift.taskId],
    );

    await scopedAdmin.query(
      "UPDATE runs SET status='running' WHERE run_id=$1",
      [claimRunStateDrift.runId],
    );
    await expectCode("prepared_action_parent_state_invalid", () =>
      claimPreparedActionExecution(
        request(token, "POST", claimBody),
        claimRunStateDrift.actionId,
      ));
    await scopedAdmin.query(
      "UPDATE runs SET status='waiting_approval' WHERE run_id=$1",
      [claimRunStateDrift.runId],
    );

    await expectDatabaseGuard(
      "approval_parent_binding_immutable",
      () => scopedAdmin!.query(
        "UPDATE runs SET agent_id=$1 WHERE run_id=$2",
        [OTHER_AGENT_ID, claimRunAgentDrift.runId],
      ),
    );
    await expectCode("forbidden", () =>
      claimPreparedActionExecution(
        request(otherToken, "POST", {
          ...claimBody,
          agent_id: OTHER_AGENT_ID,
        }, { agentId: OTHER_AGENT_ID }),
        claimRunAgentDrift.actionId,
      ));

    await scopedAdmin.query(
      "UPDATE tool_calls SET status='blocked' WHERE tool_call_id=$1",
      [claimToolStatusDrift.toolCallId],
    );
    await expectCode("prepared_action_parent_state_invalid", () =>
      claimPreparedActionExecution(
        request(token, "POST", claimBody),
        claimToolStatusDrift.actionId,
      ));
    await scopedAdmin.query(
      "UPDATE tool_calls SET status='waiting_approval' WHERE tool_call_id=$1",
      [claimToolStatusDrift.toolCallId],
    );

    await scopedAdmin.query(
      "UPDATE tool_calls SET side_effect_id=$1 WHERE tool_call_id=$2",
      ["unexpected-effect", claimToolEffectDrift.toolCallId],
    );
    await expectCode("prepared_action_parent_state_invalid", () =>
      claimPreparedActionExecution(
        request(token, "POST", claimBody),
        claimToolEffectDrift.actionId,
      ));
    await scopedAdmin.query(
      "UPDATE tool_calls SET side_effect_id=NULL WHERE tool_call_id=$1",
      [claimToolEffectDrift.toolCallId],
    );

    await scopedAdmin.query(
      "UPDATE agent_plans SET verification_result_hash=$1 WHERE plan_id=$2",
      [sha("stale-plan-verification"), claimPlanDrift.planId],
    );
    await expectCode("prepared_action_current_assignment_invalid", () =>
      claimPreparedActionExecution(
        request(token, "POST", claimBody),
        claimPlanDrift.actionId,
      ));
    await scopedAdmin.query(
      "UPDATE agent_plans SET verification_result_hash=$1 WHERE plan_id=$2",
      [claimPlanDrift.verificationResultHash, claimPlanDrift.planId],
    );

    const claimAttempts = Array.from({ length: 8 }, () =>
      claimPreparedActionExecution(
        request(token, "POST", claimBody),
        success.actionId,
      ));
    const claims = await Promise.all(claimAttempts);
    require(
      claims.filter((result) => result.status === 201).length === 1,
      "concurrent claim must have exactly one winner",
    );
    require(
      claims.filter((result) => result.status === 409).length === 7,
      "concurrent claim replays must fail closed",
    );
    require(
      claims.filter((result) => result.status === 409).every(
        (result) => result.body.error
          === "prepared_action_execution_already_claimed",
      ),
      "exact claim replay was not explicit",
    );
    const winningClaim = claims.find((result) => result.status === 201);
    const successLease = winningClaim?.body.execution_lease as
      | Record<string, unknown>
      | undefined;
    const successLeaseId = String(successLease?.lease_id || "");
    require(successLeaseId, "winning claim did not return a lease");
    const claimCount = await scopedAdmin.query<{ count: string }>(
      `SELECT COUNT(*)::text AS count
      FROM prepared_action_execution_leases WHERE action_id=$1`,
      [success.actionId],
    );
    require(Number(claimCount.rows[0].count) === 1, "claim created duplicate leases");

    await expectCode("prepared_action_execution_lease_mismatch", () =>
      failPreparedActionExecution(
        request(token, "POST", {
          workspace_id: WORKSPACE_ID,
          agent_id: AGENT_ID,
          lease_id: "pa_lease_wrong",
          failure_reason: "must not write",
          rollback_performed: true,
        }),
        success.actionId,
      ));
    const successEvidence = await seedSuccessEvidence(
      token,
      success,
      successLeaseId,
    );
    const currentSuccessManifest =
      await verifyCurrentAgentGatewayPlanEvidenceManifest(
        scopedAdmin as unknown as PoolClient,
        {
          manifestId: successEvidence.manifestId,
          workspaceId: WORKSPACE_ID,
          taskId: success.taskId,
          runId: success.runId,
          agentId: AGENT_ID,
          requireCommercialRuntime: false,
        },
      );
    require(
      currentSuccessManifest?.verification.pass === true,
      `Current success manifest did not reverify: ${
        JSON.stringify(
          currentSuccessManifest?.verification.failed_checks
            .map((check) => check.id),
        )
      } checkpoint=${
        JSON.stringify(currentSuccessManifest?.verification.execution_checkpoint)
      }`,
    );
    await expectCode("verified_plan_evidence_manifest_required", () =>
      resumePreparedActionExecution(
        request(token, "POST", {
          workspace_id: WORKSPACE_ID,
          agent_id: AGENT_ID,
          lease_id: successLeaseId,
          plan_evidence_manifest_id: "pem_missing",
          provider_side_effect_id: successEvidence.providerSideEffectId,
        }),
        success.actionId,
      ));
    await expectCode("provider_side_effect_id_invalid", () =>
      resumePreparedActionExecution(
        request(token, "POST", {
          workspace_id: WORKSPACE_ID,
          agent_id: AGENT_ID,
          lease_id: successLeaseId,
          plan_evidence_manifest_id: successEvidence.manifestId,
          provider_side_effect_id: "codex-diff-wrong",
        }),
        success.actionId,
      ));

    const assignmentClaim = await claimPreparedActionExecution(
      request(token, "POST", claimBody),
      resumeAssignmentDrift.actionId,
    );
    const assignmentLeaseId = String(
      (assignmentClaim.body.execution_lease as Record<string, unknown>).lease_id,
    );
    const runningCheckpoint = await scopedAdmin.query<{
      run_status: string;
      task_status: string;
      tool_status: string;
      tool_side_effect_id: string | null;
    }>(
      `SELECT
        (SELECT status FROM runs WHERE run_id=$1) run_status,
        (SELECT status FROM tasks WHERE task_id=$2) task_status,
        (SELECT status FROM tool_calls WHERE tool_call_id=$3) tool_status,
        (SELECT side_effect_id FROM tool_calls
          WHERE tool_call_id=$3) tool_side_effect_id`,
      [
        resumeAssignmentDrift.runId,
        resumeAssignmentDrift.taskId,
        resumeAssignmentDrift.toolCallId,
      ],
    );
    require(
      runningCheckpoint.rows[0].run_status === "running"
        && runningCheckpoint.rows[0].task_status === "running"
        && runningCheckpoint.rows[0].tool_status === "running"
        && runningCheckpoint.rows[0].tool_side_effect_id === null,
      "claim did not establish the exact side-effect-free running checkpoint",
    );
    await scopedAdmin.query(
      "UPDATE tasks SET owner_agent_id=$1 WHERE task_id=$2",
      [OTHER_AGENT_ID, resumeAssignmentDrift.taskId],
    );
    await expectCode("prepared_action_current_assignment_invalid", () =>
      resumePreparedActionExecution(
        request(token, "POST", {
          workspace_id: WORKSPACE_ID,
          agent_id: AGENT_ID,
          lease_id: assignmentLeaseId,
          plan_evidence_manifest_id: "pem_assignment_drift",
          provider_side_effect_id: "codex-diff-assignment-drift",
        }),
        resumeAssignmentDrift.actionId,
      ));
    await scopedAdmin.query(
      "UPDATE tasks SET owner_agent_id=$1 WHERE task_id=$2",
      [AGENT_ID, resumeAssignmentDrift.taskId],
    );
    await failPreparedActionExecution(
      request(token, "POST", {
        workspace_id: WORKSPACE_ID,
        agent_id: AGENT_ID,
        lease_id: assignmentLeaseId,
        failure_reason: "Assignment drift test closed without external write.",
        rollback_performed: true,
      }),
      resumeAssignmentDrift.actionId,
    );

    const selectiveClaim = await claimPreparedActionExecution(
      request(token, "POST", claimBody),
      selectiveEvidence.actionId,
    );
    const selectiveLeaseId = String(
      (selectiveClaim.body.execution_lease as Record<string, unknown>).lease_id,
    );
    const selectiveManifest = await seedSuccessEvidence(
      token,
      selectiveEvidence,
      selectiveLeaseId,
      {
        declaredToolIds: [selectiveEvidence.toolCallId],
        expectVerified: false,
      },
    );
    await expectCode("verified_plan_evidence_manifest_required", () =>
      resumePreparedActionExecution(
        request(token, "POST", {
          workspace_id: WORKSPACE_ID,
          agent_id: AGENT_ID,
          lease_id: selectiveLeaseId,
          plan_evidence_manifest_id: selectiveManifest.manifestId,
          provider_side_effect_id: selectiveManifest.providerSideEffectId,
        }),
        selectiveEvidence.actionId,
      ));
    await failPreparedActionExecution(
      request(token, "POST", {
        workspace_id: WORKSPACE_ID,
        agent_id: AGENT_ID,
        lease_id: selectiveLeaseId,
        failure_reason: "Selective evidence test failed closed and rolled back.",
        rollback_performed: true,
      }),
      selectiveEvidence.actionId,
    );

    const crossClaim = await claimPreparedActionExecution(
      request(token, "POST", claimBody),
      crossEvidence.actionId,
    );
    const crossLeaseId = String(
      (crossClaim.body.execution_lease as Record<string, unknown>).lease_id,
    );
    const crossManifest = await seedSuccessEvidence(
      token,
      crossEvidence,
      crossLeaseId,
    );
    await expectCode("verified_plan_evidence_manifest_required", () =>
      resumePreparedActionExecution(
        request(token, "POST", {
          workspace_id: WORKSPACE_ID,
          agent_id: AGENT_ID,
          lease_id: successLeaseId,
          plan_evidence_manifest_id: crossManifest.manifestId,
          provider_side_effect_id: crossManifest.providerSideEffectId,
        }),
        success.actionId,
      ));
    await failPreparedActionExecution(
      request(token, "POST", {
        workspace_id: WORKSPACE_ID,
        agent_id: AGENT_ID,
        lease_id: crossLeaseId,
        failure_reason: "Cross-action manifest test failed closed and rolled back.",
        rollback_performed: true,
      }),
      crossEvidence.actionId,
    );

    const resumeBody = {
      workspace_id: WORKSPACE_ID,
      agent_id: AGENT_ID,
      lease_id: successLeaseId,
      plan_evidence_manifest_id: successEvidence.manifestId,
      provider_side_effect_id: successEvidence.providerSideEffectId,
      output_summary: "Verified bounded workspace diff.",
      duration_ms: 1200,
      output_tokens: 32,
      result_summary: "Codex workspace-write completed with verified evidence.",
    };
    const resumed = await resumePreparedActionExecution(
      request(token, "POST", resumeBody),
      success.actionId,
    );
    require(
      resumed.status === 200
        && resumed.body.outcome === "created"
        && (resumed.body.prepared_action as Record<string, unknown>).status
          === "consumed",
      "resume did not atomically consume the action",
    );
    const replayedResume = await resumePreparedActionExecution(
      request(token, "POST", resumeBody),
      success.actionId,
    );
    require(
      replayedResume.status === 200
        && replayedResume.body.outcome === "unchanged",
      "lost-response resume replay did not return existing completion",
    );
    const successState = await scopedAdmin.query<{
      action_status: string;
      lease_status: string;
      run_status: string;
      task_status: string;
      receipts: string;
      resume_events: string;
      resume_audits: string;
    }>(
      `SELECT
        (SELECT status FROM prepared_actions WHERE action_id=$1) action_status,
        (SELECT status FROM prepared_action_execution_leases
          WHERE action_id=$1) lease_status,
        (SELECT status FROM runs WHERE run_id=$2) run_status,
        (SELECT status FROM tasks WHERE task_id=$3) task_status,
        (SELECT COUNT(*)::text FROM prepared_action_execution_receipts
          WHERE action_id=$1) receipts,
        (SELECT COUNT(*)::text FROM runtime_events
          WHERE run_id=$2 AND event_type='prepared_action.resume') resume_events,
        (SELECT COUNT(*)::text FROM audit_logs
          WHERE entity_id=$1 AND action=
            'approval_wall.prepared_action_resumed') resume_audits`,
      [success.actionId, success.runId, success.taskId],
    );
    require(
      successState.rows[0].action_status === "consumed"
        && successState.rows[0].lease_status === "completed"
        && successState.rows[0].run_status === "completed"
        && successState.rows[0].task_status === "completed"
        && Number(successState.rows[0].receipts) === 1
        && Number(successState.rows[0].resume_events) === 1
        && Number(successState.rows[0].resume_audits) === 1,
      "resume terminal state or exactly-once evidence is invalid",
    );

    const failureClaim = await claimPreparedActionExecution(
      request(token, "POST", claimBody),
      failure.actionId,
    );
    const failureLeaseId = String(
      (failureClaim.body.execution_lease as Record<string, unknown>).lease_id,
    );
    const failureBody = {
      workspace_id: WORKSPACE_ID,
      agent_id: AGENT_ID,
      lease_id: failureLeaseId,
      failure_reason: "Codex verification failed and rollback completed.",
      rollback_performed: true,
    };
    const failed = await failPreparedActionExecution(
      request(token, "POST", failureBody),
      failure.actionId,
    );
    require(
      failed.status === 200
        && failed.body.outcome === "created"
        && (failed.body.execution_receipt as Record<string, unknown>).outcome
          === "failed",
      "failure did not create an append-only terminal receipt",
    );
    const failureReplay = await failPreparedActionExecution(
      request(token, "POST", failureBody),
      failure.actionId,
    );
    require(failureReplay.body.outcome === "unchanged", "failure replay duplicated closure");
    await expectCode("prepared_action_terminal_receipt_conflict", () =>
      failPreparedActionExecution(
        request(token, "POST", {
          ...failureBody,
          failure_reason: "different terminal claim",
        }),
        failure.actionId,
      ));
    const failureState = await scopedAdmin.query<{
      action_status: string;
      lease_status: string;
      receipt_outcome: string;
      automatic_retry_allowed: boolean;
      retry_requires_new_action: boolean;
    }>(
      `SELECT
        action.status action_status,
        lease.status lease_status,
        receipt.outcome receipt_outcome,
        receipt.automatic_retry_allowed,
        receipt.retry_requires_new_action
      FROM prepared_actions action
      JOIN prepared_action_execution_leases lease
        ON lease.action_id=action.action_id
      JOIN prepared_action_execution_receipts receipt
        ON receipt.action_id=action.action_id
      WHERE action.action_id=$1`,
      [failure.actionId],
    );
    require(
      failureState.rows[0].action_status === "expired"
        && failureState.rows[0].lease_status === "failed"
        && failureState.rows[0].receipt_outcome === "failed"
        && failureState.rows[0].automatic_retry_allowed === false
        && failureState.rows[0].retry_requires_new_action === true,
      "failure terminal state permits unsafe retry",
    );

    const expiryClaim = await claimPreparedActionExecution(
      request(token, "POST", {
        ...claimBody,
        lease_ttl_seconds: 1,
      }),
      expiry.actionId,
    );
    const expiryLeaseId = String(
      (expiryClaim.body.execution_lease as Record<string, unknown>).lease_id,
    );
    await new Promise((resolve) => setTimeout(resolve, 1200));
    const expired = await getPreparedAction(
      request(token, "GET"),
      expiry.actionId,
    );
    require(
      expired.status === 200
        && expired.body.status === "blocked"
        && expired.body.reconciled_expired_lease === true
        && (expired.body.execution_receipt as Record<string, unknown>).outcome
          === "unknown",
      "GET did not reconcile an expired lease to an unknown terminal receipt",
    );

    const rawColumns = await scopedAdmin.query<{ column_name: string }>(
      `SELECT column_name FROM information_schema.columns
      WHERE table_schema=current_schema()
        AND table_name IN(
          'prepared_action_execution_leases',
          'prepared_action_execution_receipts'
        )
        AND column_name IN(
          'raw_prompt','raw_response','provider_output','provider_response',
          'credential','credentials'
        )`,
    );
    require(rawColumns.rows.length === 0, "v6 execution tables contain raw payload columns");
    const terminalCounts = await scopedAdmin.query<{
      leases: string;
      receipts: string;
      succeeded: string;
      failed: string;
      unknown: string;
    }>(
      `SELECT
        (SELECT COUNT(*)::text FROM prepared_action_execution_leases) leases,
        (SELECT COUNT(*)::text FROM prepared_action_execution_receipts) receipts,
        (SELECT COUNT(*)::text FROM prepared_action_execution_receipts
          WHERE outcome='succeeded') succeeded,
        (SELECT COUNT(*)::text FROM prepared_action_execution_receipts
          WHERE outcome='failed') failed,
        (SELECT COUNT(*)::text FROM prepared_action_execution_receipts
          WHERE outcome='unknown') unknown`,
    );
    require(
      Number(terminalCounts.rows[0].leases) === 6
        && Number(terminalCounts.rows[0].receipts) === 6
        && Number(terminalCounts.rows[0].succeeded) === 1
        && Number(terminalCounts.rows[0].failed) === 4
        && Number(terminalCounts.rows[0].unknown) === 1,
      "terminal receipt outcomes are incomplete",
    );
    const ownerEvidence = await scopedAdmin.query<{
      created_audits: string;
      decision_audits: string;
      decision_events: string;
      decision_requests: string;
    }>(
      `SELECT
        (SELECT COUNT(*)::text FROM audit_logs
          WHERE action='approval_wall.prepared_action_created') created_audits,
        (SELECT COUNT(*)::text FROM audit_logs
          WHERE action LIKE 'approval.prepared_action.%') decision_audits,
        (SELECT COUNT(*)::text FROM runtime_events
          WHERE event_type LIKE 'approval.prepared_action.%') decision_events,
        (SELECT COUNT(*)::text FROM human_approval_decision_requests)
          decision_requests`,
    );
    require(
      Number(ownerEvidence.rows[0].created_audits) === allFixtures.length
        && Number(ownerEvidence.rows[0].decision_audits)
          === allFixtures.length
        && Number(ownerEvidence.rows[0].decision_events)
          === allFixtures.length
        && Number(ownerEvidence.rows[0].decision_requests)
          === allFixtures.length,
      "PreparedAction create or Human decision evidence is incomplete",
    );

    console.log(JSON.stringify({
      contract: "prepared_action_typescript_postgres_owner_v3",
      ok: true,
      control_plane: "typescript_postgres",
      schema_contract: migration.schema_contract,
      fresh_schema: true,
      worker_routes: {
        prepare: true,
        get: true,
        claim_execution: true,
        fail_execution: true,
        resume: true,
        api_rewrite_compatible: true,
      },
      bearer_auth: true,
      workspace_agent_binding: true,
      current_assignment_binding: true,
      create_idempotency_replay: true,
      create_idempotency_conflict: true,
      human_session_decisions: {
        owner_approve: true,
        collaborator_approve: true,
        reject: true,
        replay: true,
        single_winner_race: true,
        origin_csrf: true,
        machine_token_rejected: true,
      },
      action_hash_verified: true,
      concurrent_claim_attempts: claimAttempts.length,
      exclusive_claim_winners: 1,
      exact_claim_replays_blocked: claimAttempts.length - 1,
      current_state_negative_gates: {
        task_owner_drift: true,
        run_status_drift: true,
        run_agent_drift: true,
        tool_status_drift: true,
        tool_side_effect_drift: true,
        plan_verification_drift: true,
        resume_assignment_drift: true,
        cross_action_manifest: true,
        selective_manifest_evidence: true,
      },
      claim_establishes_running_checkpoint: true,
      manifest_verified_by_real_owner: true,
      manifest_audit_ids_server_derived: true,
      resume_response_loss_replay: true,
      terminal_receipts: {
        succeeded: 1,
        failed: 4,
        unknown: 1,
        append_only: true,
      },
      automatic_retry_allowed: false,
      python_api_started: false,
      raw_provider_output_omitted: true,
      raw_prompt_omitted: true,
      raw_response_omitted: true,
      token_omitted: true,
    }, null, 2));
  } finally {
    await closeControlPlanePoolForTests();
    if (scopedAdmin) await scopedAdmin.end().catch(() => undefined);
    await baseAdmin.query(`DROP SCHEMA IF EXISTS "${schema}" CASCADE`)
      .catch(() => undefined);
    await baseAdmin.end();
  }
}

await main();
