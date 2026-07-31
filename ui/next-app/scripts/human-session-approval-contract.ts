import assert from "node:assert/strict";
import {
  createHash,
  randomBytes,
  scryptSync,
} from "node:crypto";
import { readFile } from "node:fs/promises";

import { Client } from "pg";

import {
  decideWorkspaceApproval,
  readWorkspaceApprovalReceipt,
} from "../src/server/controlPlane/approvalDecisions";
import { listWorkspaceApprovals } from "../src/server/controlPlane/approvalQueue";
import { requestCustomerDeliveryApproval } from "../src/server/controlPlane/agentGatewayApprovals";
import { closeControlPlanePoolForTests } from "../src/server/controlPlane/db";
import {
  establishHumanSession,
  humanRoleCanReview,
  humanSessionStatus,
  logoutHumanSession,
} from "../src/server/controlPlane/humanSession";
import {
  HUMAN_SCRYPT_PARAMS,
} from "../src/server/controlPlane/humanPasswordPolicy";
import {
  ControlPlaneHttpError,
  errorPayload,
} from "../src/server/controlPlane/http";
import { stableHash } from "../src/server/controlPlane/ledger";
import { listWorkspaceMemoryCandidates } from "../src/server/controlPlane/memoryCandidates";
import { reviewWorkspaceMemory } from "../src/server/controlPlane/memoryReviews";
import {
  POSTGRES_MIGRATION_MANIFEST,
  SCHEMA_CONTRACT,
} from "../src/server/controlPlane/schemaReadiness";
import { controlPlaneMode } from "../src/server/controlPlane/config";
import {
  createPostgresRoleBoundaryFixture,
} from "./postgres-role-boundary-test-helper";

const ORIGIN = "https://mis.example.test";
const HOST = "mis.example.test";
const WORKSPACE = "ws_human_contract";
const FOREIGN_WORKSPACE = "ws_foreign_contract";
const REVIEWER_ID = "usr_human_reviewer";
const OWNER_ID = "usr_human_owner";
const OPERATOR_ID = "usr_human_operator";
const AGENT_TOKEN = randomBytes(32).toString("base64url");
const PASSWORD = `${randomBytes(24).toString("base64url")}Aa1!`;

type HumanBrowserSession = {
  cookie: string;
  csrf: string;
  userId: string;
};

type DeliveryFixture = {
  approvalId: string;
  agentId: string;
  taskId: string;
  runId: string;
  planId: string;
  manifestId: string;
  planHash: string;
  verificationResultHash: string;
};

function sha(value: string) {
  return createHash("sha256").update(value, "utf8").digest("hex");
}

async function reserveHistoricalRunCost(
  client: Client,
  workspaceId: string,
  runId: string,
  createdAt: string,
) {
  await client.query(
    `INSERT INTO run_cost_reservations(
      reservation_id,workspace_id,run_id,billing_class,billing_month_utc,
      state,estimated_cost_usd,observed_cost_usd,idempotency_key_hash,
      request_hash,reserved_at,expires_at,updated_at
    ) VALUES(
      $1,$2,$3,'historical_execution',
      date_trunc('month',$4::timestamptz)::date,'reserved',1,0,$5,$6,
      $4::timestamptz,$4::timestamptz+interval '1 hour',$4::timestamptz
    )`,
    [
      `rsv_${runId}`,
      workspaceId,
      runId,
      createdAt,
      sha(`historical-reserve:${workspaceId}:${runId}`),
      sha(`historical-request:${workspaceId}:${runId}`),
    ],
  );
}

async function settleHistoricalRunCost(
  client: Client,
  workspaceId: string,
  runId: string,
) {
  await client.query(
    `SELECT reservation_id
    FROM agentops_settle_run_cost_v10($1,$2,0,$3,$4)`,
    [
      workspaceId,
      runId,
      sha(`historical-settle:${workspaceId}:${runId}`),
      sha(`historical-settle-request:${workspaceId}:${runId}`),
    ],
  );
}

function planVerificationHash(
  planId: string,
  verification: Record<string, unknown>,
) {
  const quality = verification.quality as Record<string, unknown>;
  const failedChecks = verification.failed_checks as Array<Record<string, unknown>>;
  return stableHash({
    plan_id: planId,
    plan_hash: verification.plan_hash,
    pass: verification.pass,
    failed_checks: failedChecks.map((check) => check.id),
    summary: verification.summary || {},
    quality: {
      version: quality.version,
      score: quality.score,
      status: quality.status,
      failed_rubric_ids: quality.failed_rubric_ids || [],
    },
  });
}

async function expectCode(
  code: string,
  work: () => Promise<unknown>,
) {
  await assert.rejects(work, (error: unknown) => (
    error instanceof ControlPlaneHttpError && error.code === code
  ));
}

function loginHeaders() {
  return new Headers({ origin: ORIGIN, host: HOST });
}

function browserHeaders(
  session: HumanBrowserSession,
  input?: {
    csrf?: string;
    idempotencyKey?: string;
    workspaceId?: string;
    machineCredential?: boolean;
    includeOrigin?: boolean;
  },
) {
  const headers = new Headers({
    cookie: session.cookie,
    host: HOST,
  });
  if (input?.includeOrigin !== false) headers.set("origin", ORIGIN);
  if (input?.csrf !== undefined) headers.set("x-agentops-csrf", input.csrf);
  if (input?.idempotencyKey) {
    headers.set("idempotency-key", input.idempotencyKey);
  }
  if (input?.workspaceId) {
    headers.set("x-agentops-workspace-id", input.workspaceId);
  }
  if (input?.machineCredential) {
    headers.set("authorization", "Bearer machine-fixture-not-a-human");
  }
  return headers;
}

async function login(username: string, userId: string) {
  const result = await establishHumanSession(loginHeaders(), {
    username,
    password: PASSWORD,
  });
  assert.equal(result.status, 200);
  assert.match(result.setCookie, /^agentops_human_session=/);
  assert.match(result.setCookie, /; HttpOnly/);
  assert.match(result.setCookie, /; SameSite=Strict/);
  assert.match(result.setCookie, /; Secure/);
  assert.doesNotMatch(result.setCookie, new RegExp(PASSWORD));
  const csrf = String(result.body.csrf_token || "");
  assert.match(csrf, /^[a-f0-9]{64}$/);
  return {
    cookie: result.setCookie.split(";", 1)[0],
    csrf,
    userId,
  };
}

function decisionRequest(
  session: HumanBrowserSession,
  approvalId: string,
  decision: "approve" | "reject",
  key: string,
  body: Record<string, unknown> = { workspace_id: WORKSPACE },
) {
  return new Request(`${ORIGIN}/api/mis/approvals/${approvalId}/${decision}`, {
    method: "POST",
    headers: browserHeaders(session, {
      csrf: session.csrf,
      idempotencyKey: key,
      workspaceId: String(body.workspace_id || WORKSPACE),
    }),
    body: JSON.stringify(body),
  });
}

function memoryDecisionRequest(
  session: HumanBrowserSession,
  memoryId: string,
  decision: "approve" | "reject",
  key: string,
  workspaceId = WORKSPACE,
) {
  return new Request(
    `${ORIGIN}/api/mis/memories/${memoryId}/${decision}`,
    {
      method: "POST",
      headers: browserHeaders(session, {
        csrf: session.csrf,
        idempotencyKey: key,
        workspaceId,
      }),
      body: JSON.stringify({ workspace_id: workspaceId }),
    },
  );
}

async function seedHuman(
  client: Client,
  userId: string,
  username: string,
  role: "approver" | "owner" | "operator",
  workspaceId = WORKSPACE,
) {
  const now = new Date().toISOString();
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
    [userId, username, `${username}@example.test`, role, now],
  );
  await client.query(
    `INSERT INTO workspace_memberships(
      workspace_id,user_id,role,status,created_at,updated_at
    ) VALUES($1,$2,$3,'active',$4,$4)`,
    [workspaceId, userId, role, now],
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
      now,
    ],
  );
}

async function seedDeliveryEvidence(
  client: Client,
  suffix: string,
  runtimeType: "hermes" | "openclaw",
) {
  const agentId = `agt_delivery_${suffix}`;
  const taskId = `tsk_delivery_${suffix}`;
  const runId = `run_delivery_${suffix}`;
  const planId = `plan_delivery_${suffix}`;
  const manifestId = `pem_delivery_${suffix}`;
  const toolId = `tc_delivery_${suffix}`;
  const evaluationId = `eval_delivery_${suffix}`;
  const artifactId = `art_delivery_${suffix}`;
  const now = Date.now();
  const createdAt = new Date(now - 10_000).toISOString();
  const verifiedAt = new Date(now - 5_000).toISOString();
  const steps = [
    "READ",
    "PLAN",
    "RETRIEVE",
    "COMPARE",
    "EXECUTE",
    "VERIFY",
    "RECORD",
  ];
  const planContract = {
    workspace_id: WORKSPACE,
    task_id: taskId,
    run_id: runId,
    agent_id: agentId,
    task_understanding: `Prepare verified ${runtimeType} customer delivery ${suffix}.`,
    referenced_specs: ["PROJECT_SPEC.md"],
    referenced_memories: [`project-memory:${suffix}`],
    referenced_bases: ["base_local_tasks"],
    proposed_files_to_change: [],
    risk_level: "medium",
    approval_required: false,
    execution_steps: steps,
    verification_plan: "Verify bounded evidence.",
    rollback_plan: "Keep delivery blocked.",
    plan_version: 1,
  };
  const planHash = stableHash(planContract);
  const planVerification = {
    pass: true,
    plan_hash: planHash,
    failed_checks: [],
    summary: { quality_score: 100 },
    quality: {
      version: "agent_plan_quality_v1",
      score: 100,
      status: "ready",
      failed_rubric_ids: [],
    },
  };
  const verificationResultHash = planVerificationHash(
    planId,
    planVerification,
  );

  await client.query(
    `INSERT INTO agents(
      agent_id,name,role,description,runtime_type,model_provider,model_name,
      status,permission_level,allowed_tools,budget_limit_usd,owner_user_id,
      created_at,updated_at
    ) VALUES($1,$2,'worker','contract fixture',$3,$3,'contract-model',
      'idle','operator','[]',0,NULL,$4,$4)`,
    [agentId, `Delivery ${suffix}`, runtimeType, createdAt],
  );
  await client.query(
    `INSERT INTO tasks(
      task_id,workspace_id,title,description,requester_id,owner_agent_id,
      collaborator_agent_ids,status,priority,due_date,acceptance_criteria,
      risk_level,budget_limit_usd,created_at,updated_at
    ) VALUES($1,$2,$3,NULL,NULL,$4,'[]','completed','medium',NULL,NULL,
      'medium',0,$5,$5)`,
    [taskId, WORKSPACE, `Delivery ${suffix}`, agentId, createdAt],
  );
  await client.query(
    `INSERT INTO agent_plans(
      plan_id,workspace_id,task_id,run_id,agent_id,task_understanding,
      referenced_specs_json,referenced_memories_json,referenced_bases_json,
      proposed_files_to_change_json,risk_level,approval_required,
      execution_steps_json,verification_plan,rollback_plan,status,plan_version,
      plan_hash,verified_at,verification_result_hash,approval_id,
      approved_by_user_id,approved_at,created_at,updated_at
    ) VALUES($1,$2,$3,NULLIF($4::text,$4::text),$5,$6,$7,$8,$9,'[]','medium',0,$10,$11,$12,
      'submitted',1,$13,$14,$15,NULL,NULL,NULL,$16,$14)`,
    [
      planId,
      WORKSPACE,
      taskId,
      runId,
      agentId,
      planContract.task_understanding,
      JSON.stringify(planContract.referenced_specs),
      JSON.stringify(planContract.referenced_memories),
      JSON.stringify(planContract.referenced_bases),
      JSON.stringify(steps),
      planContract.verification_plan,
      planContract.rollback_plan,
      planHash,
      verifiedAt,
      verificationResultHash,
      createdAt,
    ],
  );
  await reserveHistoricalRunCost(
    client,
    WORKSPACE,
    runId,
    createdAt,
  );
  await client.query(
    `INSERT INTO runs(
      run_id,workspace_id,task_id,agent_id,runtime_type,status,started_at,
      ended_at,duration_ms,input_summary,output_summary,model_provider,
      model_name,input_tokens,output_tokens,reasoning_tokens,cost_usd,
      error_type,error_message,trace_id,parent_run_id,delegation_id,
      approval_required,agent_plan_id,plan_hash,billing_class,created_at
    ) VALUES($1,$2,$3,$4,$5,'completed',$6,$7,1000,NULL,NULL,$5,
      'contract-model',0,0,0,0,NULL,NULL,NULL,NULL,NULL,0,$8,$9,
      'historical_execution',$6)`,
    [
      runId,
      WORKSPACE,
      taskId,
      agentId,
      runtimeType,
      createdAt,
      verifiedAt,
      planId,
      planHash,
    ],
  );
  await settleHistoricalRunCost(
    client,
    WORKSPACE,
    runId,
  );
  await client.query(
    "UPDATE agent_plans SET run_id=$1 WHERE plan_id=$2",
    [runId, planId],
  );
  await client.query(
    `INSERT INTO tool_calls(
      tool_call_id,run_id,agent_id,tool_name,tool_version,tool_category,
      normalized_args_json,target_resource,risk_level,status,result_summary,
      side_effect_id,started_at,ended_at,created_at
    ) VALUES($1,$2,$3,$4,'v1','custom',$5,NULL,'low','completed',NULL,
      NULL,$6,$7,$6)`,
    [
      toolId,
      runId,
      agentId,
      `agent_worker.${runtimeType}`,
      JSON.stringify({
        adapter: runtimeType,
        provider_call_performed: true,
        dry_run: false,
      }),
      createdAt,
      verifiedAt,
    ],
  );
  await client.query(
    `INSERT INTO evaluations(
      evaluation_id,task_id,run_id,agent_id,evaluator_type,score,pass_fail,
      rubric_json,notes,created_at
    ) VALUES($1,$2,$3,$4,'rule',1,'pass',$5,NULL,$6)`,
    [
      evaluationId,
      taskId,
      runId,
      agentId,
      JSON.stringify({
        adapter: runtimeType,
        provider_call_performed: true,
        dry_run: false,
      }),
      createdAt,
    ],
  );
  const artifactHash = sha(`artifact:${suffix}`);
  await client.query(
    `INSERT INTO artifacts(
      artifact_id,task_id,run_id,artifact_type,title,uri,summary,
      content_hash,created_at
    ) VALUES($1,$2,$3,'report',$4,NULL,NULL,$5,$6)`,
    [
      artifactId,
      taskId,
      runId,
      `Delivery artifact ${suffix}`,
      artifactHash,
      createdAt,
    ],
  );
  const auditRows = [
    [`aud_plan_${suffix}`, "agent_gateway.agent_plan_create", "agent_plans", planId, {}],
    [`aud_tool_${suffix}`, "tool_call.create", "tool_calls", toolId, {}],
    [`aud_eval_${suffix}`, "evaluation.create", "evaluations", evaluationId, {}],
    [`aud_art_${suffix}`, "agent_gateway.artifact_record", "artifacts", artifactId, {
      content_hash: artifactHash,
    }],
    [`aud_worker_${suffix}`, "agent_worker.task_processed", "runs", runId, {
      adapter: runtimeType,
      provider_call_performed: true,
      dry_run: false,
    }],
  ] as const;
  for (const [auditId, action, entityType, entityId, metadata] of auditRows) {
    await client.query(
      `INSERT INTO audit_logs(
        audit_id,workspace_id,actor_type,actor_id,action,entity_type,entity_id,
        before_hash,after_hash,metadata_json,tamper_chain_hash,created_at
      ) VALUES($1,$2,'agent',$3,$4,$5,$6,NULL,NULL,$7,$8,$9)`,
      [
        auditId,
        WORKSPACE,
        agentId,
        action,
        entityType,
        entityId,
        JSON.stringify({ ...metadata, workspace_id: WORKSPACE }),
        sha(`chain:${auditId}`),
        createdAt,
      ],
    );
  }
  await client.query(
    `INSERT INTO plan_evidence_manifests(
      manifest_id,workspace_id,plan_id,task_id,run_id,agent_id,
      mismatch_policy,expected_steps_json,tool_call_ids_json,
      evaluation_ids_json,artifact_ids_json,audit_ids_json,plan_hash,
      verification_result_hash,status,verification_json,created_at,updated_at
    ) VALUES($1,$2,$3,$4,$5,$6,'block',$7,$8,$9,$10,'[]',$11,$12,
      'verified',$13,$14,$15)`,
    [
      manifestId,
      WORKSPACE,
      planId,
      taskId,
      runId,
      agentId,
      JSON.stringify(steps),
      JSON.stringify([toolId]),
      JSON.stringify([evaluationId]),
      JSON.stringify([artifactId]),
      planHash,
      verificationResultHash,
      JSON.stringify({
        pass: true,
        status: "verified",
        failed_checks: [],
        plan_verification: planVerification,
      }),
      createdAt,
      verifiedAt,
    ],
  );
  return {
    approvalId: `ap_delivery_${suffix}`,
    agentId,
    taskId,
    runId,
    planId,
    manifestId,
    planHash,
    verificationResultHash,
  };
}

async function requestApproval(fixture: DeliveryFixture) {
  const request = new Request(
    `${ORIGIN}/api/mis/agent-gateway/approvals/request`,
    {
      method: "POST",
      headers: {
        authorization: `Bearer ${AGENT_TOKEN}`,
        "content-type": "application/json",
        "x-agentops-agent-id": fixture.agentId,
        "x-agentops-workspace-id": WORKSPACE,
      },
      body: JSON.stringify({
        approval_id: fixture.approvalId,
        approval_kind: "customer_delivery",
        decision: "pending",
        workspace_id: WORKSPACE,
        task_id: fixture.taskId,
        run_id: fixture.runId,
        agent_id: fixture.agentId,
        requested_by_agent_id: fixture.agentId,
        reason: "Bounded customer delivery review fixture.",
      }),
    },
  );
  const result = await requestCustomerDeliveryApproval(request);
  assert.equal(result.status, 201);
  assert.equal(result.body.control_plane, "typescript_postgres");
  const evidence = result.body.plan_evidence as Record<string, unknown>;
  assert.equal(evidence.pass, true);
  assert.equal(evidence.plan_hash, fixture.planHash);
  assert.equal(
    evidence.verification_result_hash,
    fixture.verificationResultHash,
  );
}

async function sourceBoundaryContract() {
  const [
    route,
    approvalListRoute,
    approvalQueueSource,
    memoryListRoute,
    memoryDecisionRoute,
    memoryReviewSource,
  ] =
    await Promise.all([
      readFile(
        new URL(
          "../app/api/mis/approvals/[approvalId]/[decision]/route.ts",
          import.meta.url,
        ),
        "utf8",
      ),
      readFile(
        new URL("../app/api/mis/approvals/route.ts", import.meta.url),
        "utf8",
      ),
      readFile(
        new URL("../src/server/controlPlane/approvalQueue.ts", import.meta.url),
        "utf8",
      ),
      readFile(
        new URL("../app/api/mis/memories/route.ts", import.meta.url),
        "utf8",
      ),
      readFile(
        new URL(
          "../app/api/mis/memories/[memoryId]/[decision]/route.ts",
          import.meta.url,
        ),
        "utf8",
      ),
      readFile(
        new URL("../src/server/controlPlane/memoryReviews.ts", import.meta.url),
        "utf8",
      ),
    ]);
  const humanSource = await readFile(
    new URL("../src/server/controlPlane/humanSession.ts", import.meta.url),
    "utf8",
  );
  assert.match(route, /legacyPythonProxyAllowed/);
  assert.match(route, /proxyControlPlaneRequest/);
  assert.match(route, /human_session_direct_route_required/);
  assert.match(approvalListRoute, /controlPlaneMode\(\) === "proxy"/);
  assert.match(approvalListRoute, /proxyControlPlaneRequest/);
  assert.match(approvalListRoute, /human_session_direct_route_required/);
  assert.doesNotMatch(approvalQueueSource, /proxyControlPlaneRequest/);
  assert.doesNotMatch(approvalQueueSource, /\bpython\b/i);
  assert.match(approvalQueueSource, /authenticateHumanMember/);
  assert.match(approvalQueueSource, /JOIN tasks task/);
  assert.match(approvalQueueSource, /JOIN runs run/);
  assert.match(approvalQueueSource, /LEFT JOIN prepared_actions action/);
  assert.match(approvalQueueSource, /raw_prompt_omitted: true/);
  assert.match(approvalQueueSource, /raw_response_omitted: true/);
  for (const memoryRoute of [memoryListRoute, memoryDecisionRoute]) {
    assert.match(memoryRoute, /controlPlaneMode\(\) === "proxy"/);
    assert.match(memoryRoute, /proxyControlPlaneRequest/);
    assert.match(memoryRoute, /human_session_direct_route_required/);
  }
  assert.doesNotMatch(memoryReviewSource, /proxyControlPlaneRequest/);
  assert.doesNotMatch(memoryReviewSource, /\bpython\b/i);
  assert.match(humanSource, /SameSite=Strict/);
  assert.match(humanSource, /HttpOnly/);
  assert.match(humanSource, /isProductionDeployment\(\).*Secure/s);
  assert.equal(humanRoleCanReview("reviewer"), true);
  assert.equal(humanRoleCanReview("approver"), true);
  assert.equal(humanRoleCanReview("workspace-admin"), true);
  assert.equal(humanRoleCanReview("owner"), true);
  assert.equal(humanRoleCanReview("operator"), false);
  assert.equal(controlPlaneMode(), "postgres");
}

async function main() {
  const baseDsn = String(
    process.env.AGENTOPS_TEST_POSTGRES_DSN
      || process.env.AGENTOPS_POSTGRES_DSN
      || "",
  ).trim();
  assert.ok(baseDsn, "postgres_dsn_required");
  process.env.AGENTOPS_DEPLOYMENT_MODE = "production";
  process.env.AGENTOPS_CONTROL_PLANE_MODE = "proxy";
  process.env.AGENTOPS_ALLOWED_ORIGINS = ORIGIN;
  process.env.AGENTOPS_HUMAN_SESSION_HMAC_KEY = randomBytes(48).toString("base64url");
  process.env.AGENTOPS_HUMAN_SESSION_TTL_SECONDS = "3600";
  process.env.AGENTOPS_HUMAN_SESSION_IDLE_TTL_SECONDS = "900";
  process.env.AGENTOPS_POSTGRES_POOL_MAX = "24";
  const roleFixture = await createPostgresRoleBoundaryFixture(
    baseDsn,
    "human_session",
  );
  const admin = roleFixture.owner;
  const restoreRuntimeEnvironment =
    roleFixture.activateRuntimeEnvironment();
  try {
    await sourceBoundaryContract();
    const version = await admin.query<{ server_version_num: string }>(
      "SHOW server_version_num",
    );
    assert.equal(
      Math.floor(Number(version.rows[0].server_version_num) / 10_000),
      16,
    );
    const migration = roleFixture.migration;
    assert.equal(migration.schema_contract, SCHEMA_CONTRACT);
    assert.equal(migration.applied_count, POSTGRES_MIGRATION_MANIFEST.length);
    assert.equal(migration.manifest_count, POSTGRES_MIGRATION_MANIFEST.length);

    await seedHuman(admin, REVIEWER_ID, "reviewer", "approver");
    await seedHuman(admin, OWNER_ID, "owner", "owner");
    await seedHuman(admin, OPERATOR_ID, "operator", "operator");
    await seedHuman(
      admin,
      "usr_foreign",
      "foreign",
      "owner",
      FOREIGN_WORKSPACE,
    );
    const fixtures: DeliveryFixture[] = [];
    fixtures.push(await seedDeliveryEvidence(admin, "replay", "hermes"));
    fixtures.push(await seedDeliveryEvidence(admin, "owner", "openclaw"));
    fixtures.push(await seedDeliveryEvidence(admin, "race", "hermes"));
    fixtures.push(await seedDeliveryEvidence(admin, "self", "openclaw"));
    const memoryIds = [
      "mem_human_approve",
      "mem_human_reject",
      "mem_human_race",
    ];
    const memoryCreatedAt = new Date().toISOString();
    for (const [index, memoryId] of memoryIds.entries()) {
      const fixture = fixtures[index];
      await admin.query(
        `INSERT INTO memories(
          memory_id,workspace_id,scope,memory_type,canonical_text,source_type,
          source_ref,project_id,task_id,run_id,agent_id,confidence,
          review_status,owner_user_id,ttl_review_due_at,supersedes_memory_id,
          access_tags,created_at,updated_at
        ) VALUES(
          $1,$2,'project','artifact_summary',$3,'run_log',$4,'agentops-mis',
          $5,$6,$7,0.9,'candidate',NULL,$8,NULL,$9,$10,$10
        )`,
        [
          memoryId,
          WORKSPACE,
          `Bounded review candidate ${index + 1}.`,
          fixture.runId,
          fixture.taskId,
          fixture.runId,
          fixture.agentId,
          new Date(Date.now() + 30 * 24 * 60 * 60 * 1000).toISOString(),
          JSON.stringify(["human-review-contract"]),
          memoryCreatedAt,
        ],
      );
    }
    for (const fixture of fixtures) {
      await admin.query(
        `INSERT INTO agent_gateway_tokens(
          token_id,token_hash,workspace_id,agent_id,scopes_json,status,label,
          heartbeat_timeout_sec,created_at,expires_at,revoked_at,last_used_at
        ) VALUES($1,$2,$3,$4,$5,'active','contract',300,$6,$7,NULL,NULL)`,
        [
          `tok_${fixture.agentId}`,
          sha(AGENT_TOKEN),
          WORKSPACE,
          fixture.agentId,
          JSON.stringify(["approvals:request"]),
          new Date().toISOString(),
          new Date(Date.now() + 3_600_000).toISOString(),
        ],
      );
      await requestApproval(fixture);
      await admin.query(
        "DELETE FROM agent_gateway_tokens WHERE token_id=$1",
        [`tok_${fixture.agentId}`],
      );
    }
    await admin.query(
      `INSERT INTO users(user_id,name,email,role,created_at)
      VALUES($1,'Self Agent','self-agent@example.test','approver',$2)`,
      [fixtures[3].agentId, new Date().toISOString()],
    );
    await admin.query(
      `INSERT INTO workspace_memberships(
        workspace_id,user_id,role,status,created_at,updated_at
      ) VALUES($1,$2,'approver','active',$3,$3)`,
      [WORKSPACE, fixtures[3].agentId, new Date().toISOString()],
    );
    const selfSalt = randomBytes(16);
    const selfHash = scryptSync(
      PASSWORD,
      selfSalt,
      HUMAN_SCRYPT_PARAMS.keylen,
      {
        N: HUMAN_SCRYPT_PARAMS.n,
        r: HUMAN_SCRYPT_PARAMS.r,
        p: HUMAN_SCRYPT_PARAMS.p,
        maxmem: 128 * 1024 * 1024,
      },
    ).toString("hex");
    await admin.query(
      `INSERT INTO human_login_credentials(
        credential_id,user_id,username,password_hash,password_salt,
        password_params_json,status,created_at,updated_at,last_login_at
      ) VALUES('cred_self_agent',$1,'self-agent',$2,$3,$4,'active',$5,$5,NULL)`,
      [
        fixtures[3].agentId,
        selfHash,
        selfSalt.toString("hex"),
        JSON.stringify(HUMAN_SCRYPT_PARAMS),
        new Date().toISOString(),
      ],
    );

    for (let attempt = 0; attempt < 8; attempt += 1) {
      await expectCode("invalid_credentials", () =>
        establishHumanSession(loginHeaders(), {
          username: "unknown-reviewer",
          password: PASSWORD,
        }));
    }
    await expectCode("human_login_throttled", () =>
      establishHumanSession(loginHeaders(), {
        username: "unknown-reviewer",
        password: PASSWORD,
      }));

    const firstReviewer = await login("reviewer", REVIEWER_ID);
    const reviewer = await login("reviewer", REVIEWER_ID);
    await expectCode("human_session_invalid", () =>
      humanSessionStatus(browserHeaders(firstReviewer, {
        includeOrigin: false,
      })));
    const activeReviewerSessions = await admin.query<{ count: string }>(
      `SELECT COUNT(*)::text AS count FROM human_sessions
      WHERE user_id=$1 AND status='active'`,
      [REVIEWER_ID],
    );
    assert.equal(Number(activeReviewerSessions.rows[0].count), 1);

    let operator = await login("operator", OPERATOR_ID);
    const operatorSessionHash = await admin.query<{ session_id: string }>(
      `SELECT session_id FROM human_sessions
      WHERE user_id=$1 AND status='active'`,
      [OPERATOR_ID],
    );
    await admin.query(
      `UPDATE human_sessions SET expires_at=$1
      WHERE session_id=$2`,
      [
        new Date(Date.now() - 1000).toISOString(),
        operatorSessionHash.rows[0].session_id,
      ],
    );
    await expectCode("human_session_expired", () =>
      humanSessionStatus(browserHeaders(operator, {
        includeOrigin: false,
      })));
    operator = await login("operator", OPERATOR_ID);

    let self = await login("self-agent", fixtures[3].agentId);
    const selfSession = await admin.query<{ session_id: string }>(
      `SELECT session_id FROM human_sessions
      WHERE user_id=$1 AND status='active'`,
      [fixtures[3].agentId],
    );
    await admin.query(
      `UPDATE human_sessions SET last_seen_at=$1
      WHERE session_id=$2`,
      [
        new Date(Date.now() - 16 * 60 * 1000).toISOString(),
        selfSession.rows[0].session_id,
      ],
    );
    await expectCode("human_session_idle_expired", () =>
      humanSessionStatus(browserHeaders(self, {
        includeOrigin: false,
      })));
    self = await login("self-agent", fixtures[3].agentId);
    const owner = await login("owner", OWNER_ID);

    const approvalQueue = await listWorkspaceApprovals(
      browserHeaders(reviewer, {
        workspaceId: WORKSPACE,
        includeOrigin: false,
      }),
      WORKSPACE,
    );
    assert.deepEqual(
      new Set(approvalQueue.body.map((row) => row.approval_id)),
      new Set(fixtures.map((fixture) => fixture.approvalId)),
    );
    for (const row of approvalQueue.body) {
      assert.equal(row.approval_kind, "customer_delivery");
      assert.equal(row.decision, "pending");
      assert.equal(row.prepared_action, null);
      assert.equal(row.review_supported, true);
      assert.equal(row.normalized_args_omitted, true);
      assert.equal(row.checkpoint_omitted, true);
      assert.equal(row.raw_prompt_omitted, true);
      assert.equal(row.raw_response_omitted, true);
      assert.equal(row.token_omitted, true);
    }
    const onePendingApproval = await listWorkspaceApprovals(
      browserHeaders(reviewer, {
        workspaceId: WORKSPACE,
        includeOrigin: false,
      }),
      WORKSPACE,
      "pending",
      "1",
    );
    assert.equal(onePendingApproval.body.length, 1);
    await expectCode("approval_decision_filter_invalid", () =>
      listWorkspaceApprovals(
        browserHeaders(reviewer, {
          workspaceId: WORKSPACE,
          includeOrigin: false,
        }),
        WORKSPACE,
        "unknown",
      ));
    await expectCode("approval_limit_invalid", () =>
      listWorkspaceApprovals(
        browserHeaders(reviewer, {
          workspaceId: WORKSPACE,
          includeOrigin: false,
        }),
        WORKSPACE,
        "pending",
        "201",
      ));
    await expectCode("human_membership_forbidden", () =>
      listWorkspaceApprovals(
        browserHeaders(reviewer, {
          workspaceId: FOREIGN_WORKSPACE,
          includeOrigin: false,
        }),
        FOREIGN_WORKSPACE,
      ));
    await expectCode("machine_credential_not_allowed", () =>
      listWorkspaceApprovals(
        browserHeaders(reviewer, {
          workspaceId: WORKSPACE,
          machineCredential: true,
          includeOrigin: false,
        }),
        WORKSPACE,
      ));

    const memoryCandidates = await listWorkspaceMemoryCandidates(
      browserHeaders(reviewer, {
        workspaceId: WORKSPACE,
        includeOrigin: false,
      }),
      WORKSPACE,
    );
    assert.deepEqual(
      new Set(memoryCandidates.body.map((row) => row.memory_id)),
      new Set(memoryIds),
    );
    await expectCode("human_membership_forbidden", () =>
      listWorkspaceMemoryCandidates(
        browserHeaders(reviewer, {
          workspaceId: FOREIGN_WORKSPACE,
          includeOrigin: false,
        }),
        FOREIGN_WORKSPACE,
      ));
    await expectCode("machine_credential_not_allowed", () =>
      reviewWorkspaceMemory(
        new Request(
          `${ORIGIN}/api/mis/memories/${memoryIds[0]}/approve`,
          {
            method: "POST",
            headers: browserHeaders(reviewer, {
              csrf: reviewer.csrf,
              idempotencyKey: "memory-machine-boundary-0001",
              workspaceId: WORKSPACE,
              machineCredential: true,
            }),
          },
        ),
        { workspace_id: WORKSPACE },
        memoryIds[0],
        "approve",
      ));
    await expectCode("csrf_validation_failed", () =>
      reviewWorkspaceMemory(
        new Request(
          `${ORIGIN}/api/mis/memories/${memoryIds[0]}/approve`,
          {
            method: "POST",
            headers: browserHeaders(reviewer, {
              csrf: "0".repeat(64),
              idempotencyKey: "memory-csrf-boundary-0001",
              workspaceId: WORKSPACE,
            }),
          },
        ),
        { workspace_id: WORKSPACE },
        memoryIds[0],
        "approve",
      ));
    const memoryApproveKey = "memory-human-approve-0001";
    const memoryApproved = await reviewWorkspaceMemory(
      memoryDecisionRequest(
        reviewer,
        memoryIds[0],
        "approve",
        memoryApproveKey,
      ),
      { workspace_id: WORKSPACE },
      memoryIds[0],
      "approve",
    );
    assert.equal(memoryApproved.body.outcome, "updated");
    assert.equal(memoryApproved.body.review_status, "approved");
    const memoryReplay = await reviewWorkspaceMemory(
      memoryDecisionRequest(
        reviewer,
        memoryIds[0],
        "approve",
        memoryApproveKey,
      ),
      { workspace_id: WORKSPACE },
      memoryIds[0],
      "approve",
    );
    assert.equal(memoryReplay.body.outcome, "unchanged");
    await expectCode("memory_review_conflict", () =>
      reviewWorkspaceMemory(
        memoryDecisionRequest(
          reviewer,
          memoryIds[0],
          "approve",
          "memory-human-approve-new-key",
        ),
        { workspace_id: WORKSPACE },
        memoryIds[0],
        "approve",
      ));
    const memoryRejected = await reviewWorkspaceMemory(
      memoryDecisionRequest(
        owner,
        memoryIds[1],
        "reject",
        "memory-human-reject-0001",
      ),
      { workspace_id: WORKSPACE },
      memoryIds[1],
      "reject",
    );
    assert.equal(memoryRejected.body.review_status, "rejected");
    const memoryRace = await Promise.allSettled([
      reviewWorkspaceMemory(
        memoryDecisionRequest(
          reviewer,
          memoryIds[2],
          "approve",
          "memory-human-race-approve",
        ),
        { workspace_id: WORKSPACE },
        memoryIds[2],
        "approve",
      ),
      reviewWorkspaceMemory(
        memoryDecisionRequest(
          owner,
          memoryIds[2],
          "reject",
          "memory-human-race-reject",
        ),
        { workspace_id: WORKSPACE },
        memoryIds[2],
        "reject",
      ),
    ]);
    assert.equal(
      memoryRace.filter((result) => result.status === "fulfilled").length,
      1,
    );
    assert.equal(
      memoryRace.filter((result) => result.status === "rejected").length,
      1,
    );
    const memoryRaceRow = await admin.query<{
      review_status: string;
      owner_user_id: string;
      decisions: string;
    }>(
      `SELECT memory.review_status,memory.owner_user_id,
        (SELECT COUNT(*)::text FROM human_memory_review_requests request
          WHERE request.memory_id=memory.memory_id) AS decisions
      FROM memories memory WHERE memory.memory_id=$1`,
      [memoryIds[2]],
    );
    assert.ok(
      ["approved", "rejected"].includes(memoryRaceRow.rows[0].review_status),
    );
    assert.ok(
      [REVIEWER_ID, OWNER_ID].includes(memoryRaceRow.rows[0].owner_user_id),
    );
    assert.equal(Number(memoryRaceRow.rows[0].decisions), 1);

    await expectCode("machine_credential_not_allowed", () =>
      decideWorkspaceApproval(
        new Request(`${ORIGIN}/api/mis/approvals/x/approve`, {
          method: "POST",
          headers: browserHeaders(reviewer, {
            csrf: reviewer.csrf,
            idempotencyKey: "machine-human-boundary-0001",
            workspaceId: WORKSPACE,
            machineCredential: true,
          }),
        }),
        { workspace_id: WORKSPACE },
        fixtures[0].approvalId,
        "approve",
      ));
    await expectCode("csrf_validation_failed", () =>
      decideWorkspaceApproval(
        new Request(`${ORIGIN}/api/mis/approvals/x/approve`, {
          method: "POST",
          headers: browserHeaders(reviewer, {
            csrf: "0".repeat(64),
            idempotencyKey: "wrong-csrf-boundary-0001",
            workspaceId: WORKSPACE,
          }),
        }),
        { workspace_id: WORKSPACE },
        fixtures[0].approvalId,
        "approve",
      ));
    await expectCode("human_membership_forbidden", () =>
      decideWorkspaceApproval(
        new Request(`${ORIGIN}/api/mis/approvals/x/approve`, {
          method: "POST",
          headers: browserHeaders(reviewer, {
            csrf: reviewer.csrf,
            idempotencyKey: "cross-workspace-boundary-0001",
            workspaceId: FOREIGN_WORKSPACE,
          }),
        }),
        { workspace_id: FOREIGN_WORKSPACE },
        fixtures[0].approvalId,
        "approve",
      ));
    await expectCode("human_actor_server_owned", () =>
      decideWorkspaceApproval(
        decisionRequest(
          reviewer,
          fixtures[0].approvalId,
          "approve",
          "spoofed-human-actor-0001",
          {
            workspace_id: WORKSPACE,
            approver_user_id: OWNER_ID,
          },
        ),
        {
          workspace_id: WORKSPACE,
          approver_user_id: OWNER_ID,
        },
        fixtures[0].approvalId,
        "approve",
      ));
    await expectCode("human_role_forbidden", () =>
      decideWorkspaceApproval(
        decisionRequest(
          operator,
          fixtures[0].approvalId,
          "approve",
          "operator-role-boundary-0001",
        ),
        { workspace_id: WORKSPACE },
        fixtures[0].approvalId,
        "approve",
      ));
    await expectCode("agent_self_approval_forbidden", () =>
      decideWorkspaceApproval(
        decisionRequest(
          self,
          fixtures[3].approvalId,
          "approve",
          "agent-self-approval-0001",
        ),
        { workspace_id: WORKSPACE },
        fixtures[3].approvalId,
        "approve",
      ));

    const replayKey = "customer-delivery-replay-0001";
    const approved = await decideWorkspaceApproval(
      decisionRequest(
        reviewer,
        fixtures[0].approvalId,
        "approve",
        replayKey,
      ),
      { workspace_id: WORKSPACE },
      fixtures[0].approvalId,
      "approve",
    );
    assert.equal(approved.body.outcome, "updated");
    assert.equal(approved.body.decision, "approved");
    const replay = await decideWorkspaceApproval(
      decisionRequest(
        reviewer,
        fixtures[0].approvalId,
        "approve",
        replayKey,
      ),
      { workspace_id: WORKSPACE },
      fixtures[0].approvalId,
      "approve",
    );
    assert.equal(replay.body.outcome, "unchanged");
    await expectCode("approval_decision_conflict", () =>
      decideWorkspaceApproval(
        decisionRequest(
          reviewer,
          fixtures[0].approvalId,
          "approve",
          "customer-delivery-new-key-0002",
        ),
        { workspace_id: WORKSPACE },
        fixtures[0].approvalId,
        "approve",
      ));

    const ownerDecision = await decideWorkspaceApproval(
      decisionRequest(
        owner,
        fixtures[1].approvalId,
        "approve",
        "customer-delivery-owner-0001",
      ),
      { workspace_id: WORKSPACE },
      fixtures[1].approvalId,
      "approve",
    );
    assert.equal(ownerDecision.body.decision, "approved");

    const raceResults = await Promise.allSettled([
      decideWorkspaceApproval(
        decisionRequest(
          reviewer,
          fixtures[2].approvalId,
          "approve",
          "customer-delivery-race-approve",
        ),
        { workspace_id: WORKSPACE },
        fixtures[2].approvalId,
        "approve",
      ),
      decideWorkspaceApproval(
        decisionRequest(
          owner,
          fixtures[2].approvalId,
          "reject",
          "customer-delivery-race-reject",
        ),
        { workspace_id: WORKSPACE },
        fixtures[2].approvalId,
        "reject",
      ),
    ]);
    assert.equal(
      raceResults.filter((result) => result.status === "fulfilled").length,
      1,
    );
    assert.equal(
      raceResults.filter((result) => result.status === "rejected").length,
      1,
    );
    const raceRow = await admin.query<{
      decision: string;
      approver_user_id: string;
      decisions: string;
    }>(
      `SELECT approval.decision,approval.approver_user_id,
        (SELECT COUNT(*)::text FROM human_approval_decision_requests request
          WHERE request.approval_id=approval.approval_id) AS decisions
      FROM approvals approval WHERE approval.approval_id=$1`,
      [fixtures[2].approvalId],
    );
    assert.ok(["approved", "rejected"].includes(raceRow.rows[0].decision));
    assert.equal(Number(raceRow.rows[0].decisions), 1);

    await decideWorkspaceApproval(
      decisionRequest(
        owner,
        fixtures[3].approvalId,
        "reject",
        "customer-delivery-self-cleanup",
      ),
      { workspace_id: WORKSPACE },
      fixtures[3].approvalId,
      "reject",
    );

    const readback = await readWorkspaceApprovalReceipt(
      new Request(
        `${ORIGIN}/api/mis/approvals/${fixtures[0].approvalId}?workspace_id=${WORKSPACE}`,
        {
          headers: browserHeaders(reviewer, {
            workspaceId: WORKSPACE,
            includeOrigin: false,
          }),
        },
      ),
      fixtures[0].approvalId,
    );
    assert.equal(readback.body.operation, "customer_delivery_approval_receipt_read");
    assert.equal(readback.body.approval.decision, "approved");
    assert.equal(readback.body.linked_state.task_status, "completed");
    assert.equal(readback.body.linked_state.run_status, "completed");
    assert.equal(readback.body.plan_evidence.manifest_id, fixtures[0].manifestId);
    assert.equal(readback.body.plan_evidence.plan_id, fixtures[0].planId);
    assert.equal(readback.body.plan_evidence.plan_hash, fixtures[0].planHash);
    assert.equal(
      readback.body.plan_evidence.verification_result_hash,
      fixtures[0].verificationResultHash,
    );
    const decisionReceipt = readback.body.decision_receipt;
    assert.ok(decisionReceipt);
    assert.equal(decisionReceipt.user_id, REVIEWER_ID);
    assert.match(
      String(decisionReceipt.request_hash),
      /^[a-f0-9]{64}$/,
    );
    await assert.rejects(
      admin.query(
        "UPDATE approvals SET decision='rejected' WHERE approval_id=$1",
        [fixtures[0].approvalId],
      ),
      (error: unknown) => {
        const candidate = error as { code?: string; message?: string };
        return candidate.code === "23514"
          && String(candidate.message || "").includes(
            "approval_terminal_immutable",
          );
      },
    );
    await assert.rejects(
      admin.query(
        `UPDATE plan_evidence_manifests SET verification_json='{}'
        WHERE manifest_id=$1`,
        [fixtures[0].manifestId],
      ),
      (error: unknown) => {
        const candidate = error as { code?: string; message?: string };
        return candidate.code === "23514"
          && String(candidate.message || "").includes(
            "customer_delivery_evidence_sealed",
        );
      },
    );
    const sealedHttpFailure = errorPayload({
      code: "23514",
      message: "customer_delivery_evidence_sealed",
    });
    assert.equal(sealedHttpFailure.status, 409);
    assert.equal(
      sealedHttpFailure.body.error,
      "customer_delivery_evidence_sealed",
    );
    const unknownDatabaseFailure = errorPayload({
      code: "23514",
      message: "unmapped_database_constraint",
    });
    assert.equal(unknownDatabaseFailure.status, 503);
    assert.equal(
      unknownDatabaseFailure.body.error,
      "typescript_control_plane_unavailable",
    );

    const humanStorage = await admin.query<{
      password_hash: string;
      password_salt: string;
      session_hash: string;
    }>(
      `SELECT credential.password_hash,credential.password_salt,
        session.session_hash
      FROM human_login_credentials credential
      JOIN human_sessions session ON session.user_id=credential.user_id
      WHERE credential.user_id=$1 AND session.status='active'`,
      [REVIEWER_ID],
    );
    assert.match(humanStorage.rows[0].password_hash, /^[a-f0-9]{64}$/);
    assert.match(humanStorage.rows[0].password_salt, /^[a-f0-9]{32}$/);
    assert.match(humanStorage.rows[0].session_hash, /^[a-f0-9]{64}$/);
    assert.equal(
      JSON.stringify(humanStorage.rows).includes(PASSWORD),
      false,
    );
    assert.equal(
      JSON.stringify(humanStorage.rows).includes(reviewer.cookie.split("=")[1]),
      false,
    );
    assert.equal(
      JSON.stringify(humanStorage.rows).includes(reviewer.csrf),
      false,
    );

    const loggedOut = await logoutHumanSession(
      browserHeaders(reviewer, {
        csrf: reviewer.csrf,
      }),
    );
    assert.match(loggedOut.setCookie, /Max-Age=0/);
    assert.match(loggedOut.setCookie, /; Secure/);
    await expectCode("human_session_invalid", () =>
      humanSessionStatus(browserHeaders(reviewer, {
        includeOrigin: false,
      })));

    const counts = await admin.query<{
      decisions: string;
      approval_audits: string;
      runtime_events: string;
      memory_decisions: string;
      memory_audits: string;
      memory_runtime_events: string;
    }>(
      `SELECT
        (SELECT COUNT(*)::text FROM human_approval_decision_requests) AS decisions,
        (SELECT COUNT(*)::text FROM audit_logs
          WHERE action LIKE 'approval.customer_delivery.%') AS approval_audits,
        (SELECT COUNT(*)::text FROM runtime_events
          WHERE event_type LIKE 'approval.customer_delivery.%') AS runtime_events,
        (SELECT COUNT(*)::text FROM human_memory_review_requests) AS memory_decisions,
        (SELECT COUNT(*)::text FROM audit_logs
          WHERE action IN ('memory.approved','memory.rejected')) AS memory_audits,
        (SELECT COUNT(*)::text FROM runtime_events
          WHERE workspace_id=$1
            AND event_type IN ('memory.approved','memory.rejected')) AS memory_runtime_events`,
      [WORKSPACE],
    );
    assert.equal(Number(counts.rows[0].decisions), 4);
    assert.equal(Number(counts.rows[0].approval_audits), 4);
    assert.ok(Number(counts.rows[0].runtime_events) >= 8);
    assert.equal(Number(counts.rows[0].memory_decisions), 3);
    assert.equal(Number(counts.rows[0].memory_audits), 3);
    assert.equal(Number(counts.rows[0].memory_runtime_events), 3);

    const receipt = {
      ok: true,
      contract: "nextjs_postgres_human_session_review_v2",
      postgres_major: 16,
      schema_contract: SCHEMA_CONTRACT,
      migrations_applied: POSTGRES_MIGRATION_MANIFEST.length,
      login_session_logout: true,
      fixed_and_idle_expiry: true,
      login_throttle: true,
      one_active_session_per_user_workspace: true,
      secure_http_only_same_site_cookie: true,
      production_secure_transport: true,
      origin_and_csrf_mutations: true,
      machine_credentials_rejected: true,
      reviewer_owner_allowed_operator_denied: true,
      workspace_isolation: true,
      human_actor_session_bound: true,
      agent_self_approval_rejected: true,
      pending_terminal_single_winner: true,
      exact_replay_unchanged: true,
      approval_queue_workspace_bound: true,
      approval_queue_machine_credentials_rejected: true,
      approval_queue_sensitive_fields_omitted: true,
      memory_candidate_list: true,
      memory_review_idempotent: true,
      memory_review_single_winner: true,
      memory_review_workspace_bound: true,
      terminal_overwrite_rejected: true,
      plan_manifest_evidence_hash_bound: true,
      evidence_seal_v4_compatible: true,
      customer_approval_task_run_readback: true,
      free_local_proxy_explicit: true,
      production_python_observer_requests: 0,
      credentials_omitted: true,
      raw_password_omitted: true,
      raw_session_omitted: true,
      raw_csrf_omitted: true,
      raw_prompt_response_omitted: true,
      token_omitted: true,
    };
    const output = JSON.stringify(receipt);
    assert.equal(output.includes(PASSWORD), false);
    assert.equal(output.includes(AGENT_TOKEN), false);
    process.stdout.write(`${JSON.stringify(receipt, null, 2)}\n`);
  } finally {
    await closeControlPlanePoolForTests().catch(() => undefined);
    restoreRuntimeEnvironment();
    await roleFixture.cleanup();
  }
}

main().catch((error: unknown) => {
  const candidate = error && typeof error === "object"
    ? error as { code?: unknown }
    : {};
  const errorCode = typeof candidate.code === "string"
    && /^[A-Za-z0-9_]{2,64}$/.test(candidate.code)
    ? candidate.code
    : "human_session_approval_contract_failed";
  process.stdout.write(`${JSON.stringify({
    ok: false,
    contract: "nextjs_postgres_human_session_customer_delivery_v1",
    error_code: errorCode,
    credentials_omitted: true,
    dsn_omitted: true,
    sql_omitted: true,
    row_data_omitted: true,
    raw_password_omitted: true,
    raw_session_omitted: true,
    raw_csrf_omitted: true,
    raw_prompt_response_omitted: true,
    token_omitted: true,
  })}\n`);
  process.exitCode = 1;
});
