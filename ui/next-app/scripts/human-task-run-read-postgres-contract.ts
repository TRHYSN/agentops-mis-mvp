import assert from "node:assert/strict";
import {
  createHash,
  randomBytes,
  randomUUID,
  scryptSync,
} from "node:crypto";
import { readFile } from "node:fs/promises";

import { Client } from "pg";

import { closeControlPlanePoolForTests } from "../src/server/controlPlane/db";
import { establishHumanSession } from "../src/server/controlPlane/humanSession";
import { HUMAN_SCRYPT_PARAMS } from "../src/server/controlPlane/humanPasswordPolicy";
import { ControlPlaneHttpError } from "../src/server/controlPlane/http";
import {
  POSTGRES_MIGRATION_MANIFEST,
  runPostgresSchemaCommand,
  SCHEMA_CONTRACT,
} from "../src/server/controlPlane/schemaReadiness";
import {
  listWorkspaceRuns,
  listWorkspaceTasks,
  readWorkspaceRunDetail,
  readWorkspaceTaskDetail,
} from "../src/server/controlPlane/workspaceTaskRunReads";

const ORIGIN = "https://mis.example.test";
const HOST = "mis.example.test";
const WORKSPACE = "ws_read_contract";
const FOREIGN_WORKSPACE = "ws_read_contract_foreign";
const PASSWORD = `${randomBytes(24).toString("base64url")}Aa1!`;
const SECRET_KEY_CANARY = `s${"k"}-contract-sensitive-value`;
const AGENT_TOKEN_CANARY = `ag${"tok"}_contract_sensitive_value`;
const DB_PASSWORD_CANARY = ["contract", "db", "password"].join("-");
const DSN_CANARY = `post${"gresql"}://reader:${DB_PASSWORD_CANARY}@db.internal/workspace`;
const APPROVAL_REASON_CANARY = "contract-approval-reason";
const NORMALIZED_ARGUMENT_CANARY = "contract-normalized-argument";
const RUBRIC_CANARY = "contract-rubric-secret";
const ARTIFACT_URI_CANARY = "https://contract-artifact-uri.example.test/private";
const MEMORY_SOURCE_CANARY = "contract-memory-source-ref";
const UPPER_EXACT_COST_USD = "999999999999.999999";
const LARGE_EXACT_COST_USD = "9999999999.999999";
const SENSITIVE_CANARIES = [
  SECRET_KEY_CANARY,
  AGENT_TOKEN_CANARY,
  DB_PASSWORD_CANARY,
  APPROVAL_REASON_CANARY,
  NORMALIZED_ARGUMENT_CANARY,
  RUBRIC_CANARY,
  "contract-artifact-uri",
  MEMORY_SOURCE_CANARY,
];

type BrowserSession = {
  cookie: string;
};

function sha(value: string) {
  return createHash("sha256").update(value, "utf8").digest("hex");
}

async function reserveHistoricalRunCost(
  client: Client,
  workspaceId: string,
  runId: string,
  estimatedCostUsd: string,
  createdAt: string,
) {
  await client.query(
    `INSERT INTO run_cost_reservations(
      reservation_id,workspace_id,run_id,billing_class,billing_month_utc,
      state,estimated_cost_usd,observed_cost_usd,idempotency_key_hash,
      request_hash,reserved_at,expires_at,updated_at
    ) VALUES(
      $1,$2,$3,'historical_execution',
      date_trunc('month',$6::timestamptz)::date,'reserved',$4::numeric,0,
      $5,$7,$6::timestamptz,$6::timestamptz+interval '1 hour',
      $6::timestamptz
    )`,
    [
      `rsv_${runId}`,
      workspaceId,
      runId,
      estimatedCostUsd,
      sha(`historical-reserve:${workspaceId}:${runId}`),
      createdAt,
      sha(`historical-request:${workspaceId}:${runId}`),
    ],
  );
}

async function settleHistoricalRunCost(
  client: Client,
  workspaceId: string,
  runId: string,
  actualCostUsd: string,
) {
  await client.query(
    `SELECT reservation_id
    FROM agentops_settle_run_cost_v10($1,$2,$3::numeric,$4,$5)`,
    [
      workspaceId,
      runId,
      actualCostUsd,
      sha(`historical-settle:${workspaceId}:${runId}`),
      sha(`historical-settle-request:${workspaceId}:${runId}`),
    ],
  );
}

function scopedDsn(baseDsn: string, schema: string) {
  const parsed = new URL(baseDsn);
  parsed.searchParams.set("options", `-csearch_path=${schema}`);
  return parsed.toString();
}

function roleDsn(
  baseDsn: string,
  schema: string,
  role: string,
  password: string,
) {
  const parsed = new URL(scopedDsn(baseDsn, schema));
  parsed.username = role;
  parsed.password = password;
  return parsed.toString();
}

function quotedSchema(value: string) {
  assert.match(value, /^[a-z][a-z0-9_]+$/);
  return `"${value}"`;
}

function loginHeaders() {
  return new Headers({ origin: ORIGIN, host: HOST });
}

function browserHeaders(
  session: BrowserSession,
  workspaceId = WORKSPACE,
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

function sessionHeaders(session: BrowserSession) {
  return new Headers({
    cookie: session.cookie,
    host: HOST,
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

async function seedHuman(
  client: Client,
  userId: string,
  username: string,
  role: "owner" | "approver" | "operator" | "viewer",
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

async function login(username: string) {
  const result = await establishHumanSession(loginHeaders(), {
    username,
    password: PASSWORD,
  });
  assert.equal(result.status, 200);
  assert.match(result.setCookie, /^agentops_human_session=/);
  assert.match(result.setCookie, /; HttpOnly/);
  assert.match(result.setCookie, /; SameSite=Strict/);
  assert.match(result.setCookie, /; Secure/);
  return {
    cookie: result.setCookie.split(";", 1)[0],
  };
}

async function seedWorkspaceEvidence(client: Client) {
  const createdAt = "2026-07-24T00:00:00.000Z";
  const endedAt = "2026-07-24T00:00:10.000Z";
  await client.query(
    `INSERT INTO agents(
      agent_id,name,role,description,runtime_type,model_provider,model_name,
      status,permission_level,allowed_tools,budget_limit_usd,owner_user_id,
      created_at,updated_at
    ) VALUES
      ('agt_read_primary','Primary','worker',NULL,'hermes','hermes',
        'contract-model','idle','operator','[]',0,NULL,$1,$1),
      ('agt_read_active','Active','worker',NULL,'openclaw','openclaw',
        'contract-model','running','operator','[]',0,NULL,$1,$1),
      ('agt_read_foreign','Foreign','worker',NULL,'hermes','hermes',
        'contract-model','idle','operator','[]',0,NULL,$1,$1)`,
    [createdAt],
  );
  await client.query(
    `INSERT INTO tasks(
      task_id,workspace_id,title,description,requester_id,owner_agent_id,
      collaborator_agent_ids,status,priority,due_date,acceptance_criteria,
      risk_level,budget_limit_usd,created_at,updated_at
    ) VALUES
      ('tsk_read_primary',$1,'Primary task',
        $4,NULL,'agt_read_primary','[]','completed','high',NULL,
        $5,'medium',25,$3,$3),
      ('tsk_read_active',$1,'Active task',NULL,NULL,'agt_read_active','[]',
        'running','medium',NULL,NULL,'low',5,$3,$3),
      ('tsk_read_foreign',$2,'Foreign task','Foreign workspace only',NULL,
        'agt_read_foreign','[]','completed','medium',NULL,NULL,'low',5,$3,$3)`,
    [
      WORKSPACE,
      FOREIGN_WORKSPACE,
      createdAt,
      `Use ${DSN_CANARY}`,
      `Never expose ${SECRET_KEY_CANARY}`,
    ],
  );
  await client.query(
    `INSERT INTO workspace_entitlements(
      workspace_id,edition,status,capabilities_json,max_agents,
      max_active_enrollments,max_active_sessions_per_agent,max_monthly_runs,
      max_monthly_cost_usd,max_concurrent_runs,effective_at,expires_at
    ) VALUES(
      $1,'team_governance','active',jsonb_build_object('run_start',true),
      10,10,10,100,$2::numeric,10,clock_timestamp()-interval '1 hour',
      clock_timestamp()+interval '1 year'
    )`,
    [WORKSPACE, UPPER_EXACT_COST_USD],
  );
  await client.query("BEGIN");
  try {
    await reserveHistoricalRunCost(
      client,
      WORKSPACE,
      "run_read_primary",
      LARGE_EXACT_COST_USD,
      createdAt,
    );
    await reserveHistoricalRunCost(
      client,
      FOREIGN_WORKSPACE,
      "run_read_foreign",
      UPPER_EXACT_COST_USD,
      createdAt,
    );
    await client.query(
      `SELECT reservation_id
      FROM agentops_reserve_run_cost_v10(
        $1,'run_read_active',1.000000,$2,$3,interval '1 hour'
      )`,
      [
        WORKSPACE,
        sha("metered-reserve:run_read_active"),
        sha("metered-request:run_read_active"),
      ],
    );
    await client.query(
      `INSERT INTO runs(
        run_id,workspace_id,task_id,agent_id,runtime_type,status,started_at,
        ended_at,duration_ms,input_summary,output_summary,model_provider,
        model_name,input_tokens,output_tokens,reasoning_tokens,cost_usd,
        error_type,error_message,trace_id,parent_run_id,delegation_id,
        approval_required,agent_plan_id,plan_hash,billing_class,created_at
      ) VALUES
        ('run_read_primary',$1,'tsk_read_primary','agt_read_primary','hermes',
          'completed',$3,$4,10000,$5,$6,'hermes','contract-model',
          10,20,3,$7::numeric,NULL,NULL,'trace-primary',NULL,NULL,0,NULL,NULL,
          'historical_execution',$3),
        ('run_read_active',$1,'tsk_read_active','agt_read_active','openclaw',
          'running',$3,NULL,NULL,NULL,NULL,'openclaw','contract-model',
          0,0,0,0,NULL,NULL,'trace-active',NULL,NULL,0,NULL,NULL,
          'metered_execution',$3),
        ('run_read_foreign',$2,'tsk_read_foreign','agt_read_foreign','hermes',
          'completed',$3,$4,10000,'Foreign input','Foreign output','hermes',
          'contract-model',1,1,0,$8::numeric,NULL,NULL,'trace-foreign',NULL,NULL,0,
          NULL,NULL,'historical_execution',$3)`,
      [
        WORKSPACE,
        FOREIGN_WORKSPACE,
        createdAt,
        endedAt,
        `Bearer ${AGENT_TOKEN_CANARY}`,
        `Result ${SECRET_KEY_CANARY}`,
        LARGE_EXACT_COST_USD,
        UPPER_EXACT_COST_USD,
      ],
    );
    await settleHistoricalRunCost(
      client,
      WORKSPACE,
      "run_read_primary",
      LARGE_EXACT_COST_USD,
    );
    await settleHistoricalRunCost(
      client,
      FOREIGN_WORKSPACE,
      "run_read_foreign",
      UPPER_EXACT_COST_USD,
    );
    await client.query("COMMIT");
  } catch (error) {
    await client.query("ROLLBACK").catch(() => undefined);
    throw error;
  }
  await client.query(
    `INSERT INTO tool_calls(
      tool_call_id,run_id,agent_id,tool_name,tool_version,tool_category,
      normalized_args_json,target_resource,risk_level,status,result_summary,
      side_effect_id,started_at,ended_at,created_at
    ) VALUES
      ('tc_read_primary','run_read_primary','agt_read_primary','contract.read',
        'v1','database',$1,$4,
        'low','completed',$5,
        NULL,$2,$3,$2),
      ('tc_read_foreign','run_read_foreign','agt_read_foreign','contract.read',
        'v1','database','{}','foreign-resource','low','completed','Foreign',
        NULL,$2,$3,$2)`,
    [
      JSON.stringify({
        value: NORMALIZED_ARGUMENT_CANARY,
        credential: SECRET_KEY_CANARY,
      }),
      createdAt,
      endedAt,
      DSN_CANARY,
      `Safe result; token=${SECRET_KEY_CANARY}`,
    ],
  );
  await client.query(
    `INSERT INTO approvals(
      approval_id,task_id,run_id,tool_call_id,requested_by_agent_id,
      approver_user_id,decision,reason,expires_at,created_at,decided_at,
      approval_kind
    ) VALUES
      ('ap_read_primary','tsk_read_primary','run_read_primary',NULL,
        'agt_read_primary',NULL,'pending',$2,NULL,$1,NULL,
        'run_execution'),
      ('ap_read_foreign','tsk_read_foreign','run_read_foreign',NULL,
        'agt_read_foreign',NULL,'pending','Foreign reason',NULL,$1,NULL,
        'run_execution')`,
    [createdAt, APPROVAL_REASON_CANARY],
  );
  await client.query(
    `INSERT INTO evaluations(
      evaluation_id,task_id,run_id,agent_id,evaluator_type,score,pass_fail,
      rubric_json,notes,created_at
    ) VALUES
      ('eval_read_primary','tsk_read_primary','run_read_primary',
        'agt_read_primary','rule',0.95,'pass',$1,
        $3,$2),
      ('eval_read_foreign','tsk_read_foreign','run_read_foreign',
        'agt_read_foreign','rule',0.8,'pass','{}','Foreign notes',$2),
      ('eval_read_mismatched','tsk_read_primary','run_read_foreign',
        'agt_read_foreign','rule',0.1,'fail','{}','Must be excluded',$2)`,
    [
      JSON.stringify({ secret: RUBRIC_CANARY }),
      createdAt,
      `credential=${SECRET_KEY_CANARY}`,
    ],
  );
  await client.query(
    `INSERT INTO memories(
      memory_id,workspace_id,scope,memory_type,canonical_text,source_type,
      source_ref,project_id,task_id,run_id,agent_id,confidence,review_status,
      owner_user_id,ttl_review_due_at,supersedes_memory_id,access_tags,
      created_at,updated_at
    ) VALUES
      ('mem_read_primary',$1,'task','agent_lesson',
        $4,'run_log',$5,NULL,'tsk_read_primary',
        'run_read_primary','agt_read_primary',0.9,'candidate',NULL,NULL,NULL,
        '[]',$3,$3),
      ('mem_read_foreign',$2,'task','agent_lesson','Foreign memory','run_log',
        'run_read_foreign',NULL,'tsk_read_foreign','run_read_foreign',
        'agt_read_foreign',0.8,'candidate',NULL,NULL,NULL,'[]',$3,$3)`,
    [
      WORKSPACE,
      FOREIGN_WORKSPACE,
      createdAt,
      `Password=${DB_PASSWORD_CANARY}`,
      MEMORY_SOURCE_CANARY,
    ],
  );
  await client.query(
    `INSERT INTO artifacts(
      artifact_id,task_id,run_id,artifact_type,title,uri,summary,
      content_hash,created_at
    ) VALUES
      ('art_read_primary','tsk_read_primary','run_read_primary','report',
        'Primary artifact',$2,$3,'hash-primary',$1),
      ('art_read_task_only','tsk_read_primary',NULL,'report',
        'Task-only artifact',NULL,'Task-level summary','hash-task-only',$1),
      ('art_read_foreign','tsk_read_foreign','run_read_foreign','report',
        'Foreign artifact',NULL,'Foreign summary','hash-foreign',$1),
      ('art_read_mismatched','tsk_read_primary','run_read_foreign','report',
        'Mismatched artifact',NULL,'Must be excluded','hash-mismatched',$1)`,
    [createdAt, ARTIFACT_URI_CANARY, `Bearer ${AGENT_TOKEN_CANARY}`],
  );
}

function assertNoSensitiveKeys(value: unknown) {
  if (Array.isArray(value)) {
    value.forEach(assertNoSensitiveKeys);
    return;
  }
  if (!value || typeof value !== "object") return;
  const record = value as Record<string, unknown>;
  for (const key of [
    "normalized_args_json",
    "reason",
    "rubric_json",
    "source_ref",
    "uri",
    "content_hash",
    "password",
    "credential",
    "dsn",
  ]) {
    assert.equal(
      Object.prototype.hasOwnProperty.call(record, key),
      false,
      `sensitive key leaked: ${key}`,
    );
  }
  Object.values(record).forEach(assertNoSensitiveKeys);
}

function assertSanitized(value: unknown) {
  const serialized = JSON.stringify(value);
  for (const canary of SENSITIVE_CANARIES) {
    assert.doesNotMatch(serialized, new RegExp(canary, "g"));
  }
  assertNoSensitiveKeys(value);
}

async function assertStaticProductionBoundary() {
  const paths = [
    "../src/server/controlPlane/workspaceTaskRunReads.ts",
    "../app/api/mis/tasks/route.ts",
    "../app/api/mis/tasks/[taskId]/route.ts",
    "../app/api/mis/runs/route.ts",
    "../app/api/mis/runs/[runId]/route.ts",
  ];
  const source = (
    await Promise.all(paths.map((path) => readFile(
      new URL(path, import.meta.url),
      "utf8",
    )))
  ).join("\n");
  assert.match(source, /authenticateHumanMember/);
  assert.match(source, /typescript_postgres/);
  assert.match(source, /python_proxy_performed:\s*false/);
  assert.match(source, /provider_call_performed:\s*false/);
  assert.match(source, /JOIN tasks task/);
  assert.match(source, /JOIN runs run/);
  assert.doesNotMatch(source, /proxyControlPlaneRequest/);
  assert.doesNotMatch(source, /proxyFreeLocalRead/);
  assert.doesNotMatch(source, /child_process/);
  assert.doesNotMatch(source, /agentops_mis/);
  assert.doesNotMatch(source, /\.py\b/);
  assert.doesNotMatch(source, /\bfetch\s*\(/);
}

async function run() {
  const baseDsn = String(process.env.AGENTOPS_POSTGRES_DSN || "").trim();
  assert.ok(baseDsn, "AGENTOPS_POSTGRES_DSN is required");
  const schema = `human_task_run_read_${randomUUID().replaceAll("-", "")}`;
  const runtimeApiSchema = `api_human_${randomBytes(8).toString("hex")}`;
  const runtimeRole = `rt_human_${randomBytes(8).toString("hex")}`;
  const runtimePassword = randomBytes(24).toString("base64url");
  const entitlementAdminRole = `ea_human_${randomBytes(8).toString("hex")}`;
  const entitlementAdminPassword = randomBytes(24).toString("base64url");
  const admin = new Client({ connectionString: baseDsn });
  const originalFetch = globalThis.fetch;
  let fetchCalls = 0;
  let schemaCreated = false;
  process.env.AGENTOPS_DEPLOYMENT_MODE = "production";
  process.env.AGENTOPS_CONTROL_PLANE_MODE = "postgres";
  process.env.AGENTOPS_ALLOWED_ORIGINS = ORIGIN;
  process.env.AGENTOPS_HUMAN_SESSION_HMAC_KEY = randomBytes(48)
    .toString("base64url");
  globalThis.fetch = async () => {
    fetchCalls += 1;
    throw new Error("Network access is forbidden in the read contract.");
  };

  try {
    await admin.connect();
    const version = await admin.query<{ server_version: string }>(
      "SHOW server_version",
    );
    assert.match(version.rows[0]?.server_version || "", /^16\./);
    await admin.query(`CREATE SCHEMA ${quotedSchema(schema)}`);
    schemaCreated = true;
    const contractDsn = scopedDsn(baseDsn, schema);
    const migration = await runPostgresSchemaCommand(
      "migrate",
      {
        connectionString: contractDsn,
        applicationSchema: schema,
        runtimeApiSchema,
        runtimeRole,
        runtimePassword,
        entitlementAdminRole,
        entitlementAdminPassword,
        provisionRoleBoundary: true,
      },
    );
    assert.equal(migration.schema_contract, SCHEMA_CONTRACT);
    assert.equal(
      migration.applied_count,
      POSTGRES_MIGRATION_MANIFEST.length,
    );
    await admin.query(`SET search_path TO ${quotedSchema(schema)}`);

    await seedHuman(admin, "usr_read_owner", "read-owner", "owner");
    await seedHuman(admin, "usr_read_reviewer", "read-reviewer", "approver");
    await seedHuman(admin, "usr_read_operator", "read-operator", "operator");
    await seedHuman(admin, "usr_read_viewer", "read-viewer", "viewer");
    await seedHuman(
      admin,
      "usr_read_foreign",
      "read-foreign",
      "owner",
      FOREIGN_WORKSPACE,
    );
    await seedWorkspaceEvidence(admin);
    process.env.AGENTOPS_POSTGRES_DSN = roleDsn(
      baseDsn,
      schema,
      runtimeRole,
      runtimePassword,
    );
    process.env.AGENTOPS_POSTGRES_SCHEMA = schema;
    process.env.AGENTOPS_POSTGRES_RUNTIME_API_SCHEMA = runtimeApiSchema;
    process.env.AGENTOPS_POSTGRES_RUNTIME_ROLE = runtimeRole;

    const owner = await login("read-owner");
    const reviewer = await login("read-reviewer");
    const operator = await login("read-operator");
    const viewer = await login("read-viewer");
    const foreign = await login("read-foreign");

    for (const session of [owner, reviewer, operator, viewer]) {
      const tasks = await listWorkspaceTasks(
        browserHeaders(session),
        WORKSPACE,
        [],
        "100",
      );
      assert.equal(tasks.status, 200);
      assert.deepEqual(
        tasks.body.map((task) => task.task_id).sort(),
        ["tsk_read_active", "tsk_read_primary"],
      );
      const runs = await listWorkspaceRuns(
        browserHeaders(session),
        WORKSPACE,
        [],
        "100",
      );
      assert.equal(runs.status, 200);
      assert.deepEqual(
        runs.body.map((run) => run.run_id).sort(),
        ["run_read_active", "run_read_primary"],
      );
      const primaryRun = runs.body.find(
        (run) => run.run_id === "run_read_primary",
      );
      assert.equal(primaryRun?.cost_usd, Number(LARGE_EXACT_COST_USD));
      assert.equal(primaryRun?.cost_usd_exact, LARGE_EXACT_COST_USD);
    }

    const limitedTasks = await listWorkspaceTasks(
      browserHeaders(owner),
      WORKSPACE,
      ["completed"],
      "1",
    );
    assert.equal(limitedTasks.body.length, 1);
    assert.equal(limitedTasks.body[0]?.task_id, "tsk_read_primary");
    const limitedRuns = await listWorkspaceRuns(
      browserHeaders(owner),
      WORKSPACE,
      ["completed"],
      "1",
    );
    assert.equal(limitedRuns.body.length, 1);
    assert.equal(limitedRuns.body[0]?.run_id, "run_read_primary");
    const filteredRuns = await listWorkspaceRuns(
      browserHeaders(owner),
      WORKSPACE,
      [],
      "100",
      "tsk_read_primary",
      "agt_read_primary",
      "0",
    );
    assert.deepEqual(filteredRuns.body.map((run) => run.run_id), [
      "run_read_primary",
    ]);
    const offsetRuns = await listWorkspaceRuns(
      browserHeaders(owner),
      WORKSPACE,
      [],
      "1",
      undefined,
      undefined,
      "1",
    );
    assert.deepEqual(offsetRuns.body.map((run) => run.run_id), [
      "run_read_primary",
    ]);
    const headerBoundTasks = await listWorkspaceTasks(
      browserHeaders(owner),
      undefined,
      [],
      "10",
    );
    assert.equal(headerBoundTasks.body.length, 2);
    const queryBoundTasks = await listWorkspaceTasks(
      sessionHeaders(owner),
      WORKSPACE,
      [],
      "10",
    );
    assert.equal(queryBoundTasks.body.length, 2);
    const sessionBoundTasks = await listWorkspaceTasks(
      sessionHeaders(owner),
      undefined,
      [],
      "10",
    );
    assert.equal(sessionBoundTasks.body.length, 2);

    const taskDetail = await readWorkspaceTaskDetail(
      browserHeaders(owner),
      WORKSPACE,
      "tsk_read_primary",
    );
    assert.equal(taskDetail.body.task.task_id, "tsk_read_primary");
    assert.deepEqual(taskDetail.body.runs.map((run) => run.run_id), [
      "run_read_primary",
    ]);
    assert.deepEqual(
      taskDetail.body.approvals.map((approval) => approval.approval_id),
      ["ap_read_primary"],
    );
    assert.deepEqual(
      taskDetail.body.evaluations.map((evaluation) =>
        evaluation.evaluation_id),
      ["eval_read_primary"],
    );
    assert.deepEqual(
      taskDetail.body.memories.map((memory) => memory.memory_id),
      ["mem_read_primary"],
    );
    assert.deepEqual(
      taskDetail.body.artifacts.map((artifact) => artifact.artifact_id),
      ["art_read_primary", "art_read_task_only"],
    );
    assert.equal(taskDetail.body.provider_call_performed, false);
    assert.equal(taskDetail.body.python_proxy_performed, false);

    const runDetail = await readWorkspaceRunDetail(
      browserHeaders(reviewer),
      WORKSPACE,
      "run_read_primary",
    );
    assert.equal(runDetail.body.run.run_id, "run_read_primary");
    assert.equal(
      runDetail.body.run.cost_usd,
      Number(LARGE_EXACT_COST_USD),
    );
    assert.equal(
      runDetail.body.run.cost_usd_exact,
      LARGE_EXACT_COST_USD,
    );
    assert.deepEqual(
      runDetail.body.tool_calls.map((tool) => tool.tool_call_id),
      ["tc_read_primary"],
    );
    assert.deepEqual(
      runDetail.body.approvals.map((approval) => approval.approval_id),
      ["ap_read_primary"],
    );
    assert.deepEqual(
      runDetail.body.evaluations.map((evaluation) =>
        evaluation.evaluation_id),
      ["eval_read_primary"],
    );
    assert.deepEqual(
      runDetail.body.artifacts.map((artifact) => artifact.artifact_id),
      ["art_read_primary"],
    );
    assert.equal(runDetail.body.provider_call_performed, false);
    assert.equal(runDetail.body.python_proxy_performed, false);

    const foreignTasks = await listWorkspaceTasks(
      browserHeaders(foreign, FOREIGN_WORKSPACE),
      FOREIGN_WORKSPACE,
      [],
      "100",
    );
    assert.deepEqual(foreignTasks.body.map((task) => task.task_id), [
      "tsk_read_foreign",
    ]);
    const foreignRunDetail = await readWorkspaceRunDetail(
      browserHeaders(foreign, FOREIGN_WORKSPACE),
      FOREIGN_WORKSPACE,
      "run_read_foreign",
    );
    assert.equal(
      foreignRunDetail.body.run.cost_usd,
      Number(UPPER_EXACT_COST_USD),
    );
    assert.equal(
      foreignRunDetail.body.run.cost_usd_exact,
      UPPER_EXACT_COST_USD,
    );
    await expectCode("task_not_found", () => readWorkspaceTaskDetail(
      browserHeaders(owner),
      WORKSPACE,
      "tsk_read_foreign",
    ));
    await expectCode("run_not_found", () => readWorkspaceRunDetail(
      browserHeaders(owner),
      WORKSPACE,
      "run_read_foreign",
    ));
    await expectCode("human_membership_forbidden", () => listWorkspaceTasks(
      browserHeaders(owner, FOREIGN_WORKSPACE),
      FOREIGN_WORKSPACE,
      [],
      "10",
    ));
    await expectCode("forbidden", () => listWorkspaceTasks(
      browserHeaders(owner),
      FOREIGN_WORKSPACE,
      [],
      "10",
    ));
    await expectCode("machine_credential_not_allowed", () =>
      listWorkspaceTasks(
        browserHeaders(owner, WORKSPACE, true),
        WORKSPACE,
        [],
        "10",
      ));
    await expectCode("human_auth_required", () =>
      listWorkspaceTasks(new Headers(), WORKSPACE, [], "10"));
    await expectCode("task_status_filter_invalid", () =>
      listWorkspaceTasks(
        browserHeaders(owner),
        WORKSPACE,
        ["not-a-status"],
        "10",
      ));
    await expectCode("run_status_filter_invalid", () =>
      listWorkspaceRuns(
        browserHeaders(owner),
        WORKSPACE,
        ["not-a-status"],
        "10",
      ));
    await expectCode("read_limit_invalid", () =>
      listWorkspaceTasks(
        browserHeaders(owner),
        WORKSPACE,
        [],
        "201",
      ));
    await expectCode("read_offset_invalid", () =>
      listWorkspaceRuns(
        browserHeaders(owner),
        WORKSPACE,
        [],
        "10",
        undefined,
        undefined,
        "5001",
      ));
    await expectCode("agent_id_invalid", () =>
      listWorkspaceRuns(
        browserHeaders(owner),
        WORKSPACE,
        [],
        "10",
        undefined,
        "invalid agent id",
      ));

    assertSanitized(limitedTasks.body);
    assertSanitized(limitedRuns.body);
    assertSanitized(taskDetail.body);
    assertSanitized(runDetail.body);
    assert.equal(fetchCalls, 0);
    await assertStaticProductionBoundary();

    process.stdout.write(`${JSON.stringify({
      ok: true,
      contract: "human_task_run_read_postgres_v1",
      postgres_major: 16,
      schema_contract: SCHEMA_CONTRACT,
      migration_count: POSTGRES_MIGRATION_MANIFEST.length,
      workspaces_verified: 2,
      human_roles_verified: ["owner", "reviewer", "operator", "viewer"],
      run_task_agent_offset_filters: true,
      legacy_numeric_cost_projection_compatible: true,
      exact_cost_projection_numeric_18_6: true,
      routes_verified: [
        "GET /api/mis/tasks",
        "GET /api/mis/tasks/:taskId",
        "GET /api/mis/runs",
        "GET /api/mis/runs/:runId",
      ],
      provider_calls: fetchCalls,
      python_proxy_performed: false,
      sensitive_canaries_omitted: SENSITIVE_CANARIES.length,
    })}\n`);
  } finally {
    globalThis.fetch = originalFetch;
    await closeControlPlanePoolForTests();
    if (schemaCreated) {
      await admin.query(`DROP SCHEMA IF EXISTS ${quotedSchema(schema)} CASCADE`);
      await admin.query(
        `DROP SCHEMA IF EXISTS ${quotedSchema(runtimeApiSchema)} CASCADE`,
      );
      await admin.query(`DROP ROLE IF EXISTS ${quotedSchema(runtimeRole)}`);
      await admin.query(
        `DROP ROLE IF EXISTS ${quotedSchema(entitlementAdminRole)}`,
      );
    }
    await admin.end().catch(() => undefined);
  }
}

await run();
