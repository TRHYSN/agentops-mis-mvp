import assert from "node:assert/strict";
import {
  createHash,
  randomBytes,
  scryptSync,
} from "node:crypto";
import { readFile } from "node:fs/promises";

import { Client } from "pg";

import {
  controlPlaneMode,
  legacyPythonProxyAllowed,
} from "../src/server/controlPlane/config";
import { closeControlPlanePoolForTests } from "../src/server/controlPlane/db";
import { establishHumanSession } from "../src/server/controlPlane/humanSession";
import { HUMAN_SCRYPT_PARAMS } from "../src/server/controlPlane/humanPasswordPolicy";
import { ControlPlaneHttpError } from "../src/server/controlPlane/http";
import {
  POSTGRES_MIGRATION_MANIFEST,
  SCHEMA_CONTRACT,
} from "../src/server/controlPlane/schemaReadiness";
import { readWorkspaceRunEvidenceGraph } from "../src/server/controlPlane/workspaceRunEvidenceGraph";
import {
  createPostgresRoleBoundaryFixture,
} from "./postgres-role-boundary-test-helper";

const ORIGIN = "https://mis.example.test";
const HOST = "mis.example.test";
const WORKSPACE = "ws_graph_contract";
const FOREIGN_WORKSPACE = "ws_graph_contract_foreign";
const PASSWORD = `${randomBytes(24).toString("base64url")}Aa1!`;
const NORMALIZED_ARGS_CANARY = "graph-normalized-args-canary";
const RAW_PROMPT_CANARY = "graph-raw-prompt-canary";
const RAW_RESPONSE_CANARY = "graph-raw-response-canary";
const APPROVAL_REASON_CANARY = "graph-approval-reason-canary";
const RUBRIC_CANARY = "graph-rubric-canary";
const ARTIFACT_URI_CANARY = "https://graph-artifact.example.test/private";
const MEMORY_CONTENT_CANARY = "graph-memory-content-canary";
const MEMORY_SOURCE_CANARY = "graph-memory-source-canary";
const AUDIT_METADATA_CANARY = "graph-audit-metadata-canary";
const CREDENTIAL_CANARY = `ag${"tok"}_graph_credential_canary`;
const DSN_CANARY = `post${"gresql"}://graph:password@db.internal/evidence`;
const SENSITIVE_CANARIES = [
  NORMALIZED_ARGS_CANARY,
  RAW_PROMPT_CANARY,
  RAW_RESPONSE_CANARY,
  APPROVAL_REASON_CANARY,
  RUBRIC_CANARY,
  "graph-artifact.example.test",
  MEMORY_CONTENT_CANARY,
  MEMORY_SOURCE_CANARY,
  AUDIT_METADATA_CANARY,
  CREDENTIAL_CANARY,
  DSN_CANARY,
];
const PRIMARY = {
  agentId: "agt_graph_primary",
  taskId: "tsk_graph_primary",
  runId: "run_graph_primary",
  planId: "plan_graph_primary",
  toolId: "tc_graph_primary",
  runtimeEventId: "rte_graph_primary",
  evaluationId: "eval_graph_primary",
  approvalId: "ap_graph_primary",
  artifactId: "art_graph_primary",
  memoryId: "mem_graph_primary",
  manifestId: "pem_graph_primary",
};
const SIBLING = {
  ...PRIMARY,
  runId: "run_graph_sibling",
  planId: "plan_graph_sibling",
  toolId: "tc_graph_sibling",
  runtimeEventId: "rte_graph_sibling",
  evaluationId: "eval_graph_sibling",
  approvalId: "ap_graph_sibling",
  artifactId: "art_graph_sibling",
  memoryId: "mem_graph_sibling",
  manifestId: "pem_graph_sibling",
};
const FOREIGN = {
  agentId: "agt_graph_foreign",
  taskId: "tsk_graph_foreign",
  runId: "run_graph_foreign",
  planId: "plan_graph_foreign",
  toolId: "tc_graph_foreign",
  runtimeEventId: "rte_graph_foreign",
  evaluationId: "eval_graph_foreign",
  approvalId: "ap_graph_foreign",
  artifactId: "art_graph_foreign",
  memoryId: "mem_graph_foreign",
  manifestId: "pem_graph_foreign",
};
const SPARSE_RUN_ID = "run_graph_sparse";

type BrowserSession = { cookie: string };
type Fixture = typeof PRIMARY;

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

function loginHeaders() {
  return new Headers({ origin: ORIGIN, host: HOST });
}

function browserHeaders(
  session: BrowserSession,
  workspaceId: string,
  machineCredential = false,
) {
  const headers = new Headers({
    cookie: session.cookie,
    host: HOST,
    "x-agentops-workspace-id": workspaceId,
  });
  if (machineCredential) {
    headers.set("authorization", "Bearer machine-fixture-not-a-human");
  }
  return headers;
}

function graphRequest(
  session: BrowserSession,
  runId: string,
  workspaceId: string,
  machineCredential = false,
) {
  return new Request(
    `${ORIGIN}/api/mis/runs/${encodeURIComponent(runId)}/evidence-graph`
      + `?workspace_id=${encodeURIComponent(workspaceId)}`,
    {
      headers: browserHeaders(session, workspaceId, machineCredential),
    },
  );
}

async function expectCode(code: string, work: () => Promise<unknown>) {
  await assert.rejects(work, (error: unknown) => (
    error instanceof ControlPlaneHttpError && error.code === code
  ));
}

async function seedHuman(
  client: Client,
  input: {
    userId: string;
    username: string;
    role: "viewer" | "owner";
    workspaceId: string;
  },
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
    [
      input.userId,
      input.username,
      `${input.username}@example.test`,
      input.role,
      now,
    ],
  );
  await client.query(
    `INSERT INTO workspace_memberships(
      workspace_id,user_id,role,status,created_at,updated_at
    ) VALUES($1,$2,$3,'active',$4,$4)`,
    [input.workspaceId, input.userId, input.role, now],
  );
  await client.query(
    `INSERT INTO human_login_credentials(
      credential_id,user_id,username,password_hash,password_salt,
      password_params_json,status,created_at,updated_at,last_login_at
    ) VALUES($1,$2,$3,$4,$5,$6,'active',$7,$7,NULL)`,
    [
      `cred_${input.username}`,
      input.userId,
      input.username,
      passwordHash,
      salt.toString("hex"),
      JSON.stringify(HUMAN_SCRYPT_PARAMS),
      now,
    ],
  );
}

async function login(username: string) {
  const result = await establishHumanSession(loginHeaders(), {
    username,
    password: PASSWORD,
  });
  assert.equal(result.status, 200);
  assert.match(result.setCookie, /^agentops_human_session=/);
  return { cookie: result.setCookie.split(";", 1)[0] };
}

async function seedPlanAndRun(
  client: Client,
  fixture: Fixture,
  workspaceId: string,
  createdAt: string,
) {
  const planHash = sha(`${fixture.planId}:plan`);
  const verificationHash = sha(`${fixture.planId}:verification`);
  await client.query(
    `INSERT INTO agent_plans(
      plan_id,workspace_id,task_id,run_id,agent_id,task_understanding,
      referenced_specs_json,referenced_memories_json,referenced_bases_json,
      proposed_files_to_change_json,risk_level,approval_required,
      execution_steps_json,verification_plan,rollback_plan,status,plan_version,
      plan_hash,verified_at,verification_result_hash,approval_id,
      approved_by_user_id,approved_at,created_at,updated_at
    ) VALUES(
      $1,$2,$3,NULL,$4,$5,'[]','[]','[]','[]','low',0,'[]',
      'contract verification','contract rollback','submitted',1,$6,$7,$8,
      NULL,NULL,NULL,$7,$7
    )`,
    [
      fixture.planId,
      workspaceId,
      fixture.taskId,
      fixture.agentId,
      fixture === PRIMARY
        ? `Plan omits ${RAW_PROMPT_CANARY} and ${CREDENTIAL_CANARY}`
        : "Bound contract plan",
      planHash,
      createdAt,
      verificationHash,
    ],
  );
  await reserveHistoricalRunCost(
    client,
    workspaceId,
    fixture.runId,
    createdAt,
  );
  await client.query(
    `INSERT INTO runs(
      run_id,workspace_id,task_id,agent_id,runtime_type,status,started_at,
      ended_at,duration_ms,input_summary,output_summary,model_provider,
      model_name,input_tokens,output_tokens,reasoning_tokens,cost_usd,
      error_type,error_message,trace_id,parent_run_id,delegation_id,
      approval_required,agent_plan_id,plan_hash,billing_class,created_at
    ) VALUES(
      $1,$2,$3,$4,'hermes','completed',$5,$5,1000,$6,$7,'hermes',
      'contract-model',1,1,0,0,NULL,NULL,NULL,NULL,NULL,0,$8,$9,
      'historical_execution',$5
    )`,
    [
      fixture.runId,
      workspaceId,
      fixture.taskId,
      fixture.agentId,
      createdAt,
      fixture === PRIMARY ? `${RAW_PROMPT_CANARY} ${DSN_CANARY}` : null,
      fixture === PRIMARY ? RAW_RESPONSE_CANARY : null,
      fixture.planId,
      planHash,
    ],
  );
  await settleHistoricalRunCost(
    client,
    workspaceId,
    fixture.runId,
  );
  await client.query(
    "UPDATE agent_plans SET run_id=$1 WHERE plan_id=$2",
    [fixture.runId, fixture.planId],
  );
  return { planHash, verificationHash };
}

async function seedBoundEvidence(
  client: Client,
  fixture: Fixture,
  workspaceId: string,
  createdAt: string,
  hashes: { planHash: string; verificationHash: string },
) {
  const sensitive = fixture === PRIMARY;
  await client.query(
    `INSERT INTO tool_calls(
      tool_call_id,run_id,agent_id,tool_name,tool_version,tool_category,
      normalized_args_json,target_resource,risk_level,status,result_summary,
      side_effect_id,started_at,ended_at,created_at
    ) VALUES($1,$2,$3,'contract.graph','v1','database',$4,$5,'low',
      'completed',$6,NULL,$7,$7,$7)`,
    [
      fixture.toolId,
      fixture.runId,
      fixture.agentId,
      JSON.stringify({
        value: sensitive ? NORMALIZED_ARGS_CANARY : "bounded",
        credential: sensitive ? CREDENTIAL_CANARY : "omitted",
      }),
      sensitive ? DSN_CANARY : null,
      sensitive ? RAW_RESPONSE_CANARY : "bounded",
      createdAt,
    ],
  );
  await client.query(
    `INSERT INTO runtime_events(
      runtime_event_id,runtime_connector_id,event_type,status,run_id,task_id,
      agent_id,model_name,latency_ms,prompt_hash,input_summary,output_summary,
      error_message,raw_payload_hash,created_at,workspace_id
    ) VALUES($1,NULL,'worker.completed','completed',$2,$3,$4,
      'contract-model',10,$5,$6,$7,NULL,$8,$9,$10)`,
    [
      fixture.runtimeEventId,
      fixture.runId,
      fixture.taskId,
      fixture.agentId,
      sha(`${fixture.runId}:prompt`),
      sensitive ? RAW_PROMPT_CANARY : null,
      sensitive ? RAW_RESPONSE_CANARY : null,
      sha(`${fixture.runId}:payload`),
      createdAt,
      workspaceId,
    ],
  );
  await client.query(
    `INSERT INTO evaluations(
      evaluation_id,task_id,run_id,agent_id,evaluator_type,score,pass_fail,
      rubric_json,notes,created_at
    ) VALUES($1,$2,$3,$4,'rule',1,'pass',$5,$6,$7)`,
    [
      fixture.evaluationId,
      fixture.taskId,
      fixture.runId,
      fixture.agentId,
      JSON.stringify({
        canary: sensitive ? RUBRIC_CANARY : "bounded",
      }),
      sensitive ? CREDENTIAL_CANARY : null,
      createdAt,
    ],
  );
  await client.query(
    `INSERT INTO approvals(
      approval_id,task_id,run_id,tool_call_id,requested_by_agent_id,
      approver_user_id,decision,reason,expires_at,created_at,decided_at,
      approval_kind
    ) VALUES($1,$2,$3,NULL,$4,NULL,'pending',$5,NULL,$6,NULL,
      'run_execution')`,
    [
      fixture.approvalId,
      fixture.taskId,
      fixture.runId,
      fixture.agentId,
      sensitive ? APPROVAL_REASON_CANARY : "bounded",
      createdAt,
    ],
  );
  await client.query(
    `INSERT INTO artifacts(
      artifact_id,task_id,run_id,artifact_type,title,uri,summary,
      content_hash,created_at
    ) VALUES($1,$2,$3,'report','Contract artifact',$4,$5,$6,$7)`,
    [
      fixture.artifactId,
      fixture.taskId,
      fixture.runId,
      sensitive ? ARTIFACT_URI_CANARY : null,
      sensitive ? RAW_RESPONSE_CANARY : "bounded",
      sha(`${fixture.artifactId}:content`),
      createdAt,
    ],
  );
  await client.query(
    `INSERT INTO memories(
      memory_id,workspace_id,scope,memory_type,canonical_text,source_type,
      source_ref,project_id,task_id,run_id,agent_id,confidence,review_status,
      owner_user_id,ttl_review_due_at,supersedes_memory_id,access_tags,
      created_at,updated_at
    ) VALUES($1,$2,'task','agent_lesson',$3,'run_log',$4,NULL,$5,$6,$7,
      0.9,'candidate',NULL,NULL,NULL,'[]',$8,$8)`,
    [
      fixture.memoryId,
      workspaceId,
      sensitive ? MEMORY_CONTENT_CANARY : "bounded",
      sensitive ? MEMORY_SOURCE_CANARY : fixture.runId,
      fixture.taskId,
      fixture.runId,
      fixture.agentId,
      createdAt,
    ],
  );
  for (const [suffix, entityType, entityId] of [
    ["run", "runs", fixture.runId],
    ["tool", "tool_calls", fixture.toolId],
  ] as const) {
    await client.query(
      `INSERT INTO audit_logs(
        audit_id,workspace_id,actor_type,actor_id,action,entity_type,entity_id,
        before_hash,after_hash,metadata_json,tamper_chain_hash,created_at
      ) VALUES($1,$2,'agent',$3,'contract.graph.read',$4,$5,NULL,NULL,$6,$7,$8)`,
      [
        `aud_${fixture.runId}_${suffix}`,
        workspaceId,
        fixture.agentId,
        entityType,
        entityId,
        JSON.stringify({
          workspace_id: workspaceId,
          canary: sensitive ? AUDIT_METADATA_CANARY : "bounded",
        }),
        sha(`${fixture.runId}:${suffix}:chain`),
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
    ) VALUES($1,$2,$3,$4,$5,$6,'block','[]',$7,$8,$9,$10,$11,$12,
      'verified',$13,$14,$14)`,
    [
      fixture.manifestId,
      workspaceId,
      fixture.planId,
      fixture.taskId,
      fixture.runId,
      fixture.agentId,
      JSON.stringify([fixture.toolId]),
      JSON.stringify([fixture.evaluationId]),
      JSON.stringify([fixture.artifactId]),
      JSON.stringify([
        `aud_${fixture.runId}_run`,
        `aud_${fixture.runId}_tool`,
      ]),
      hashes.planHash,
      hashes.verificationHash,
      JSON.stringify({
        status: "verified",
        sensitive_canary: sensitive ? CREDENTIAL_CANARY : "omitted",
      }),
      createdAt,
    ],
  );
}

async function seedGraph(client: Client) {
  const createdAt = "2026-07-24T00:00:00.000Z";
  await client.query(
    `INSERT INTO agents(
      agent_id,name,role,description,runtime_type,model_provider,model_name,
      status,permission_level,allowed_tools,budget_limit_usd,owner_user_id,
      created_at,updated_at
    ) VALUES
      ($1,'Graph primary','worker',NULL,'hermes','hermes','contract-model',
        'idle','viewer','[]',0,NULL,$3,$3),
      ($2,'Graph foreign','worker',NULL,'hermes','hermes','contract-model',
        'idle','viewer','[]',0,NULL,$3,$3)`,
    [PRIMARY.agentId, FOREIGN.agentId, createdAt],
  );
  await client.query(
    `INSERT INTO tasks(
      task_id,workspace_id,title,description,requester_id,owner_agent_id,
      collaborator_agent_ids,status,priority,due_date,acceptance_criteria,
      risk_level,budget_limit_usd,created_at,updated_at
    ) VALUES
      ($1,$2,'Graph primary',$3,NULL,$4,'[]','completed','high',NULL,$5,
        'medium',0,$8,$8),
      ($6,$7,'Graph foreign','Foreign workspace',NULL,$9,'[]','completed',
        'medium',NULL,NULL,'low',0,$8,$8)`,
    [
      PRIMARY.taskId,
      WORKSPACE,
      `${RAW_PROMPT_CANARY} ${CREDENTIAL_CANARY}`,
      PRIMARY.agentId,
      DSN_CANARY,
      FOREIGN.taskId,
      FOREIGN_WORKSPACE,
      createdAt,
      FOREIGN.agentId,
    ],
  );
  const primaryHashes = await seedPlanAndRun(
    client,
    PRIMARY,
    WORKSPACE,
    createdAt,
  );
  const siblingHashes = await seedPlanAndRun(
    client,
    SIBLING,
    WORKSPACE,
    createdAt,
  );
  const foreignHashes = await seedPlanAndRun(
    client,
    FOREIGN,
    FOREIGN_WORKSPACE,
    createdAt,
  );
  await reserveHistoricalRunCost(
    client,
    WORKSPACE,
    SPARSE_RUN_ID,
    createdAt,
  );
  await client.query(
    `INSERT INTO runs(
      run_id,workspace_id,task_id,agent_id,runtime_type,status,started_at,
      ended_at,duration_ms,input_summary,output_summary,model_provider,
      model_name,input_tokens,output_tokens,reasoning_tokens,cost_usd,
      error_type,error_message,trace_id,parent_run_id,delegation_id,
      approval_required,agent_plan_id,plan_hash,billing_class,created_at
    ) VALUES($1,$2,$3,$4,'hermes','completed',$5,$5,1,NULL,NULL,'hermes',
      'contract-model',0,0,0,0,NULL,NULL,NULL,NULL,NULL,0,NULL,NULL,
      'historical_execution',$5)`,
    [
      SPARSE_RUN_ID,
      WORKSPACE,
      PRIMARY.taskId,
      PRIMARY.agentId,
      createdAt,
    ],
  );
  await settleHistoricalRunCost(
    client,
    WORKSPACE,
    SPARSE_RUN_ID,
  );
  await seedBoundEvidence(
    client,
    PRIMARY,
    WORKSPACE,
    createdAt,
    primaryHashes,
  );
  await seedBoundEvidence(
    client,
    SIBLING,
    WORKSPACE,
    createdAt,
    siblingHashes,
  );
  await seedBoundEvidence(
    client,
    FOREIGN,
    FOREIGN_WORKSPACE,
    createdAt,
    foreignHashes,
  );
  await client.query(
    `INSERT INTO artifacts(
      artifact_id,task_id,run_id,artifact_type,title,uri,summary,
      content_hash,created_at
    ) VALUES('art_graph_task_only',$1,NULL,'report','Task only',NULL,
      'Must not enter a run graph',$2,$3)`,
    [PRIMARY.taskId, sha("task-only-artifact"), createdAt],
  );
  await client.query(
    `INSERT INTO memories(
      memory_id,workspace_id,scope,memory_type,canonical_text,source_type,
      source_ref,project_id,task_id,run_id,agent_id,confidence,review_status,
      owner_user_id,ttl_review_due_at,supersedes_memory_id,access_tags,
      created_at,updated_at
    ) VALUES('mem_graph_task_only',$1,'task','agent_lesson',
      'Must not enter a run graph','manual',NULL,NULL,$2,NULL,$3,0.5,
      'candidate',NULL,NULL,NULL,'[]',$4,$4)`,
    [WORKSPACE, PRIMARY.taskId, PRIMARY.agentId, createdAt],
  );
  await client.query(
    `INSERT INTO runtime_events(
      runtime_event_id,runtime_connector_id,event_type,status,run_id,task_id,
      agent_id,model_name,latency_ms,prompt_hash,input_summary,output_summary,
      error_message,raw_payload_hash,created_at,workspace_id
    ) VALUES('rte_graph_task_only',NULL,'task.observed','completed',NULL,$1,
      NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,$2,$3)`,
    [PRIMARY.taskId, createdAt, WORKSPACE],
  );
  await client.query(
    `INSERT INTO audit_logs(
      audit_id,workspace_id,actor_type,actor_id,action,entity_type,entity_id,
      before_hash,after_hash,metadata_json,tamper_chain_hash,created_at
    ) VALUES('aud_graph_task_only',$1,'agent',$2,'task.observed','tasks',$3,
      NULL,NULL,$4,$5,$6)`,
    [
      WORKSPACE,
      PRIMARY.agentId,
      PRIMARY.taskId,
      JSON.stringify({ workspace_id: WORKSPACE }),
      sha("task-only-audit-chain"),
      createdAt,
    ],
  );
}

function objectBody(result: Awaited<ReturnType<
  typeof readWorkspaceRunEvidenceGraph
>>) {
  assert.equal(Array.isArray(result.body), false);
  return result.body as Record<string, unknown>;
}

function counts(body: Record<string, unknown>) {
  return body.evidence_counts as Record<string, number>;
}

function assertSensitiveDataOmitted(value: unknown) {
  const serialized = JSON.stringify(value);
  for (const canary of SENSITIVE_CANARIES) {
    assert.doesNotMatch(serialized, new RegExp(canary, "g"));
  }
  const forbiddenKeys = new Set([
    "task_understanding",
    "input_summary",
    "output_summary",
    "normalized_args_json",
    "target_resource",
    "result_summary",
    "prompt_hash",
    "raw_payload_hash",
    "reason",
    "rubric_json",
    "notes",
    "uri",
    "summary",
    "canonical_text",
    "source_ref",
    "metadata_json",
    "verification_json",
    "credential",
    "credentials",
    "password",
    "token",
    "dsn",
  ]);
  const visit = (item: unknown) => {
    if (Array.isArray(item)) {
      item.forEach(visit);
      return;
    }
    if (!item || typeof item !== "object") return;
    for (const [key, nested] of Object.entries(
      item as Record<string, unknown>,
    )) {
      assert.equal(forbiddenKeys.has(key), false, `unsafe key returned: ${key}`);
      visit(nested);
    }
  };
  visit(value);
}

async function evidenceLedgerCounts(client: Client) {
  const result = await client.query<{ relation: string; count: string }>(
    `SELECT 'tool_calls' AS relation,COUNT(*)::text AS count FROM tool_calls
    UNION ALL SELECT 'runtime_events',COUNT(*)::text FROM runtime_events
    UNION ALL SELECT 'evaluations',COUNT(*)::text FROM evaluations
    UNION ALL SELECT 'approvals',COUNT(*)::text FROM approvals
    UNION ALL SELECT 'artifacts',COUNT(*)::text FROM artifacts
    UNION ALL SELECT 'memories',COUNT(*)::text FROM memories
    UNION ALL SELECT 'audit_logs',COUNT(*)::text FROM audit_logs
    UNION ALL SELECT 'plan_evidence_manifests',COUNT(*)::text
      FROM plan_evidence_manifests
    ORDER BY relation`,
  );
  return Object.fromEntries(result.rows.map((row) => [
    row.relation,
    Number(row.count),
  ]));
}

async function assertStaticProductionBoundary() {
  const [owner, route, helper, config, packageJson] = await Promise.all([
    readFile(
      new URL(
        "../src/server/controlPlane/workspaceRunEvidenceGraph.ts",
        import.meta.url,
      ),
      "utf8",
    ),
    readFile(
      new URL(
        "../app/api/mis/runs/[runId]/evidence-graph/route.ts",
        import.meta.url,
      ),
      "utf8",
    ),
    readFile(
      new URL("../src/server/controlPlane/humanReadRoute.ts", import.meta.url),
      "utf8",
    ),
    readFile(
      new URL("../src/server/controlPlane/config.ts", import.meta.url),
      "utf8",
    ),
    readFile(new URL("../package.json", import.meta.url), "utf8"),
  ]);
  assert.match(owner, /authenticateHumanMember/);
  assert.match(owner, /withPostgresTransaction/);
  assert.match(owner, /stableHash/);
  assert.match(owner, /task\.workspace_id=\$1 AND run\.run_id=\$2/);
  assert.match(owner, /audit\.workspace_id=root\.workspace_id/);
  assert.match(owner, /event\.run_id=root\.run_id/);
  assert.match(owner, /memory\.run_id=root\.run_id/);
  assert.doesNotMatch(owner, /\bOR\s+(?:[A-Za-z_]+\.)?task_id\b/);
  assert.doesNotMatch(
    owner,
    /proxyControlPlaneRequest|legacyPythonProxyAllowed|child_process|sqlite|\.py\b|\bfetch\s*\(/,
  );
  assert.match(route, /ownHumanReadGet/);
  assert.match(
    route,
    /upstreamPath:\s*`\/runs\/\$\{encodeURIComponent\(runId\)\}\/evidence-graph`/,
  );
  assert.doesNotMatch(
    route,
    /workspaceTaskRunReads|proxyControlPlaneRequest|controlPlaneMode/,
  );
  assert.match(helper, /controlPlaneMode\(\) === "proxy"/);
  assert.match(helper, /!legacyPythonProxyAllowed\(\)/);
  assert.match(config, /isProductionDeployment\(\) \? "postgres" : "proxy"/);
  const scripts = (
    JSON.parse(packageJson) as { scripts: Record<string, string> }
  ).scripts;
  assert.equal(
    scripts["test:human-run-evidence-graph-postgres-contract"],
    "tsx scripts/human-run-evidence-graph-postgres-contract.ts",
  );
}

async function run() {
  const baseDsn = String(process.env.AGENTOPS_POSTGRES_DSN || "").trim();
  assert.ok(baseDsn, "AGENTOPS_POSTGRES_DSN is required");
  const originalFetch = globalThis.fetch;
  const environmentKeys = [
    "AGENTOPS_DEPLOYMENT_MODE",
    "AGENTOPS_CONTROL_PLANE_MODE",
    "AGENTOPS_POSTGRES_DSN",
    "AGENTOPS_POSTGRES_DSN_FILE",
    "AGENTOPS_POSTGRES_SCHEMA",
    "AGENTOPS_POSTGRES_RUNTIME_API_SCHEMA",
    "AGENTOPS_POSTGRES_RUNTIME_ROLE",
    "AGENTOPS_ALLOWED_ORIGINS",
    "AGENTOPS_HUMAN_SESSION_HMAC_KEY",
  ] as const;
  const originalEnvironment = Object.fromEntries(
    environmentKeys.map((key) => [key, process.env[key]]),
  );
  let roleFixture: Awaited<
    ReturnType<typeof createPostgresRoleBoundaryFixture>
  > | undefined;
  let restoreRuntimeEnvironment: () => void = () => undefined;
  let fetchCalls = 0;

  try {
    process.env.AGENTOPS_DEPLOYMENT_MODE = "free_local";
    process.env.AGENTOPS_CONTROL_PLANE_MODE = "proxy";
    assert.equal(controlPlaneMode(), "proxy");
    assert.equal(legacyPythonProxyAllowed(), true);
    process.env.AGENTOPS_DEPLOYMENT_MODE = "production";
    process.env.AGENTOPS_CONTROL_PLANE_MODE = "proxy";
    assert.equal(controlPlaneMode(), "postgres");
    assert.equal(legacyPythonProxyAllowed(), false);
    globalThis.fetch = async () => {
      fetchCalls += 1;
      throw new Error("Network access is forbidden in the graph contract.");
    };

    roleFixture = await createPostgresRoleBoundaryFixture(
      baseDsn,
      "human_run_graph",
    );
    const admin = roleFixture.owner;
    const version = await admin.query<{ server_version: string }>(
      "SHOW server_version",
    );
    assert.match(version.rows[0]?.server_version || "", /^16\./);
    const migration = roleFixture.migration;
    assert.equal(migration.schema_contract, SCHEMA_CONTRACT);
    assert.equal(
      migration.applied_count,
      POSTGRES_MIGRATION_MANIFEST.length,
    );

    await seedHuman(admin, {
      userId: "usr_graph_owner",
      username: "graph-owner",
      role: "owner",
      workspaceId: WORKSPACE,
    });
    await seedHuman(admin, {
      userId: "usr_graph_viewer",
      username: "graph-viewer",
      role: "viewer",
      workspaceId: WORKSPACE,
    });
    await seedHuman(admin, {
      userId: "usr_graph_foreign",
      username: "graph-foreign",
      role: "owner",
      workspaceId: FOREIGN_WORKSPACE,
    });
    await seedGraph(admin);
    restoreRuntimeEnvironment = roleFixture.activateRuntimeEnvironment();
    process.env.AGENTOPS_ALLOWED_ORIGINS = ORIGIN;
    process.env.AGENTOPS_HUMAN_SESSION_HMAC_KEY = randomBytes(48)
      .toString("base64url");

    const owner = await login("graph-owner");
    const viewer = await login("graph-viewer");
    const foreign = await login("graph-foreign");
    const before = await evidenceLedgerCounts(admin);

    const ownerGraph = objectBody(await readWorkspaceRunEvidenceGraph(
      graphRequest(owner, PRIMARY.runId, WORKSPACE),
      PRIMARY.runId,
    ));
    const viewerGraph = objectBody(await readWorkspaceRunEvidenceGraph(
      graphRequest(viewer, PRIMARY.runId, WORKSPACE),
      PRIMARY.runId,
    ));
    const repeatedGraph = objectBody(await readWorkspaceRunEvidenceGraph(
      graphRequest(owner, PRIMARY.runId, WORKSPACE),
      PRIMARY.runId,
    ));
    assert.equal(ownerGraph.workspace_id, WORKSPACE);
    assert.equal(ownerGraph.run_id, PRIMARY.runId);
    assert.equal(ownerGraph.task_id, PRIMARY.taskId);
    assert.equal(ownerGraph.agent_id, PRIMARY.agentId);
    assert.equal(ownerGraph.agent_plan_id, PRIMARY.planId);
    assert.equal(ownerGraph.plan_evidence_manifest_id, PRIMARY.manifestId);
    assert.deepEqual(counts(ownerGraph), {
      tool_calls: 1,
      runtime_events: 1,
      evaluations: 1,
      approvals: 1,
      artifacts: 1,
      memories: 1,
      audit_logs: 2,
      plan_evidence_manifests: 1,
    });
    assert.match(String(ownerGraph.graph_hash), /^[a-f0-9]{64}$/);
    assert.equal(viewerGraph.graph_hash, ownerGraph.graph_hash);
    assert.equal(repeatedGraph.graph_hash, ownerGraph.graph_hash);
    assert.deepEqual(viewerGraph.evidence_counts, ownerGraph.evidence_counts);

    const sparseGraph = objectBody(await readWorkspaceRunEvidenceGraph(
      graphRequest(viewer, SPARSE_RUN_ID, WORKSPACE),
      SPARSE_RUN_ID,
    ));
    assert.deepEqual(counts(sparseGraph), {
      tool_calls: 0,
      runtime_events: 0,
      evaluations: 0,
      approvals: 0,
      artifacts: 0,
      memories: 0,
      audit_logs: 0,
      plan_evidence_manifests: 0,
    });
    assert.equal(sparseGraph.agent_plan_id, null);
    assert.equal(sparseGraph.plan_evidence_manifest_id, null);

    const foreignGraph = objectBody(await readWorkspaceRunEvidenceGraph(
      graphRequest(foreign, FOREIGN.runId, FOREIGN_WORKSPACE),
      FOREIGN.runId,
    ));
    assert.equal(foreignGraph.workspace_id, FOREIGN_WORKSPACE);
    assert.equal(foreignGraph.run_id, FOREIGN.runId);
    assert.deepEqual(counts(foreignGraph), counts(ownerGraph));
    assert.notEqual(foreignGraph.graph_hash, ownerGraph.graph_hash);
    await expectCode("run_not_found", () =>
      readWorkspaceRunEvidenceGraph(
        graphRequest(owner, FOREIGN.runId, WORKSPACE),
        FOREIGN.runId,
      ));
    await expectCode("machine_credential_not_allowed", () =>
      readWorkspaceRunEvidenceGraph(
        graphRequest(owner, PRIMARY.runId, WORKSPACE, true),
        PRIMARY.runId,
      ));
    await expectCode("human_membership_forbidden", () =>
      readWorkspaceRunEvidenceGraph(
        graphRequest(owner, FOREIGN.runId, FOREIGN_WORKSPACE),
        FOREIGN.runId,
      ));

    assertSensitiveDataOmitted(ownerGraph);
    assertSensitiveDataOmitted(viewerGraph);
    assertSensitiveDataOmitted(sparseGraph);
    assertSensitiveDataOmitted(foreignGraph);
    assert.equal(
      (ownerGraph.safety as Record<string, unknown>).workspace_bound,
      true,
    );
    assert.equal(
      (ownerGraph.safety as Record<string, unknown>).audit_workspace_bound,
      true,
    );
    assert.equal(ownerGraph.python_proxy_performed, false);
    assert.equal(ownerGraph.provider_call_performed, false);
    assert.equal(fetchCalls, 0);
    assert.deepEqual(await evidenceLedgerCounts(admin), before);
    await assertStaticProductionBoundary();

    process.stdout.write(`${JSON.stringify({
      ok: true,
      contract: "human_run_evidence_graph_postgres_v1",
      postgres_major: 16,
      schema_contract: SCHEMA_CONTRACT,
      migration_count: POSTGRES_MIGRATION_MANIFEST.length,
      workspaces_verified: 2,
      human_roles_verified: ["viewer", "owner"],
      route_verified: "GET /api/mis/runs/:runId/evidence-graph",
      sparse_run_evidence_counts: 0,
      graph_hash_stable: true,
      direct_typescript_postgres_restricted_runtime: true,
      runtime_role_boundary_verified: true,
      runtime_relation_owner_excluded: true,
      provider_calls: fetchCalls,
      python_proxy_performed: false,
      sensitive_canaries_omitted: SENSITIVE_CANARIES.length,
    })}\n`);
  } finally {
    globalThis.fetch = originalFetch;
    await closeControlPlanePoolForTests();
    restoreRuntimeEnvironment();
    for (const key of environmentKeys) {
      const value = originalEnvironment[key];
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
    await roleFixture?.cleanup();
  }
}

await run();
