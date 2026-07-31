import assert from "node:assert/strict";
import {
  createHash,
  createHmac,
  randomBytes,
} from "node:crypto";
import { readFile } from "node:fs/promises";

import { Client } from "pg";

import { closeControlPlanePoolForTests } from "../src/server/controlPlane/db";
import {
  listHumanAgents,
  listHumanAudit,
  listHumanEvaluations,
  listHumanToolCalls,
  readHumanAgentPerformance,
  readHumanDashboard,
} from "../src/server/controlPlane/humanReadModels";
import { ControlPlaneHttpError } from "../src/server/controlPlane/http";
import { runPostgresSchemaCommand } from "../src/server/controlPlane/schemaReadiness";

const BASE_DSN = String(process.env.AGENTOPS_POSTGRES_DSN || "").trim();
const WORKSPACE = "ws_human_reads";
const FOREIGN_WORKSPACE = "ws_human_reads_foreign";
const LOCAL_AGENT = "agt_human_reads";
const FOREIGN_AGENT = "agt_human_reads_foreign";
const LOCAL_TASK = "tsk_human_reads";
const FOREIGN_TASK = "tsk_human_reads_foreign";
const LOCAL_RUN = "run_human_reads";
const FOREIGN_RUN = "run_human_reads_foreign";
const FOREIGN_CANARY = "foreign-read-canary-must-not-cross";
const SESSION_TOKEN = randomBytes(32).toString("base64url");
const SESSION_HMAC_KEY = randomBytes(48).toString("base64url");
const SCHEMA = `human_reads_${randomBytes(6).toString("hex")}`;

function quotedIdentifier(value: string) {
  assert.match(value, /^[a-z][a-z0-9_]+$/);
  return `"${value}"`;
}

function scopedDsn() {
  const parsed = new URL(BASE_DSN);
  parsed.searchParams.set("options", `-csearch_path=${SCHEMA}`);
  return parsed.toString();
}

function sessionHash() {
  return createHmac("sha256", SESSION_HMAC_KEY)
    .update(`session:${SESSION_TOKEN}`, "utf8")
    .digest("hex");
}

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

function browserRequest(
  path: string,
  options: {
    workspaceId?: string;
    queryWorkspaceId?: string;
    machineCredential?: boolean;
  } = {},
) {
  const url = new URL(`https://mis.example.test/api/mis${path}`);
  if (options.queryWorkspaceId) {
    url.searchParams.set("workspace_id", options.queryWorkspaceId);
  }
  const headers = new Headers({
    cookie: `agentops_human_session=${SESSION_TOKEN}`,
    "x-agentops-workspace-id": options.workspaceId || WORKSPACE,
  });
  if (options.machineCredential) {
    headers.set("authorization", "Bearer machine-cannot-be-human");
  }
  return new Request(url, { method: "GET", headers });
}

async function expectCode(
  code: string,
  work: () => Promise<unknown>,
) {
  await assert.rejects(work, (error: unknown) => (
    error instanceof ControlPlaneHttpError && error.code === code
  ));
}

async function seed(client: Client) {
  const now = new Date().toISOString();
  const expiresAt = new Date(Date.now() + 60 * 60 * 1000).toISOString();
  await client.query(
    `INSERT INTO users(user_id,name,email,role,created_at)
    VALUES
      ('usr_human_reads','Human Read Member',
        'human-reads@example.invalid','viewer',$1),
      ('usr_human_reads_owner','Human Read Owner',
        'human-reads-owner@example.invalid','owner',$1)`,
    [now],
  );
  await client.query(
    `INSERT INTO workspace_memberships(
      workspace_id,user_id,role,status,created_at,updated_at
    ) VALUES($1,'usr_human_reads','viewer','active',$2,$2)`,
    [WORKSPACE, now],
  );
  await client.query(
    `INSERT INTO human_sessions(
      session_id,user_id,session_hash,status,created_at,expires_at,
      last_seen_at,revoked_at
    ) VALUES(
      'hsess_human_reads','usr_human_reads',$1,'active',$2,$3,$2,NULL
    )`,
    [sessionHash(), now, expiresAt],
  );
  await client.query(
    `INSERT INTO agents(
      agent_id,name,role,description,runtime_type,model_provider,model_name,
      status,permission_level,allowed_tools,budget_limit_usd,owner_user_id,
      created_at,updated_at
    ) VALUES
      ($1,'Workspace Hermes','worker','bounded local agent','hermes',
        'hermes','commercial-hermes','running','standard','["summarize"]',
        10,'usr_human_reads_owner',$3,$3),
      ($2,$4,'worker',$4,'openclaw','openclaw','foreign-openclaw','error',
        'standard','[]',20,'usr_human_reads_owner',$3,$3)`,
    [LOCAL_AGENT, FOREIGN_AGENT, now, FOREIGN_CANARY],
  );
  await client.query(
    `INSERT INTO agent_gateway_tokens(
      token_id,token_hash,workspace_id,agent_id,scopes_json,status,label,
      heartbeat_timeout_sec,created_at,expires_at,revoked_at,last_used_at,
      last_heartbeat_at
    ) VALUES
      ('tok_human_reads',$1,$2,$3,'["tasks:read"]','active',
        'human read local',300,$5,NULL,NULL,NULL,$5),
      ('tok_human_reads_foreign',$4,$6,$7,'["tasks:read"]','active',
        'human read foreign',300,$5,NULL,NULL,NULL,$5)`,
    [
      sha("local-human-read-token"),
      WORKSPACE,
      LOCAL_AGENT,
      sha("foreign-human-read-token"),
      now,
      FOREIGN_WORKSPACE,
      FOREIGN_AGENT,
    ],
  );
  await client.query(
    `INSERT INTO tasks(
      task_id,workspace_id,title,description,requester_id,owner_agent_id,
      collaborator_agent_ids,status,priority,due_date,acceptance_criteria,
      risk_level,budget_limit_usd,created_at,updated_at
    ) VALUES
      ($1,$2,'Workspace task','bounded local task','usr_human_reads_owner',$3,
        '[]','completed','high',NULL,'Verify workspace read isolation','low',
        5,$7,$7),
      ($4,$5,$6,$6,'usr_human_reads_owner',$8,'[]','failed','high',NULL,$6,
        'low',5,$7,$7)`,
    [
      LOCAL_TASK,
      WORKSPACE,
      LOCAL_AGENT,
      FOREIGN_TASK,
      FOREIGN_WORKSPACE,
      FOREIGN_CANARY,
      now,
      FOREIGN_AGENT,
    ],
  );
  await reserveHistoricalRunCost(
    client,
    WORKSPACE,
    LOCAL_RUN,
    "0.250000",
    now,
  );
  await reserveHistoricalRunCost(
    client,
    FOREIGN_WORKSPACE,
    FOREIGN_RUN,
    "9.750000",
    now,
  );
  await client.query(
    `INSERT INTO runs(
      run_id,workspace_id,task_id,agent_id,runtime_type,status,started_at,
      ended_at,duration_ms,input_summary,output_summary,model_provider,
      model_name,input_tokens,output_tokens,reasoning_tokens,cost_usd,
      error_type,error_message,trace_id,parent_run_id,delegation_id,
      approval_required,agent_plan_id,plan_hash,billing_class,created_at
    ) VALUES
      ($1,$2,$3,$4,'hermes','completed',$7,$7,1200,
        'bounded input','bounded output','hermes','commercial-hermes',
        10,12,2,0.25,NULL,NULL,'trace_human_reads',NULL,NULL,0,NULL,NULL,
        'historical_execution',$7),
      ($5,$6,$8,$9,'openclaw','failed',$7,$7,900,
        $10,$10,'openclaw','foreign-openclaw',
        8,4,1,9.75,'foreign_error',$10,'trace_human_reads_foreign',
        NULL,NULL,1,NULL,NULL,'historical_execution',$7)`,
    [
      LOCAL_RUN,
      WORKSPACE,
      LOCAL_TASK,
      LOCAL_AGENT,
      FOREIGN_RUN,
      FOREIGN_WORKSPACE,
      now,
      FOREIGN_TASK,
      FOREIGN_AGENT,
      FOREIGN_CANARY,
    ],
  );
  await settleHistoricalRunCost(
    client,
    WORKSPACE,
    LOCAL_RUN,
    "0.250000",
  );
  await settleHistoricalRunCost(
    client,
    FOREIGN_WORKSPACE,
    FOREIGN_RUN,
    "9.750000",
  );
  await client.query(
    `INSERT INTO tool_calls(
      tool_call_id,run_id,agent_id,tool_name,tool_version,tool_category,
      normalized_args_json,target_resource,risk_level,status,result_summary,
      side_effect_id,started_at,ended_at,created_at
    ) VALUES
      ('tc_human_reads',$1,$2,'summarize','v1','custom','{}',
        'workspace://bounded','low','completed','bounded result',NULL,$5,$5,$5),
      ('tc_human_reads_foreign',$3,$4,'foreign-tool','v1','custom','{}',
        $6,'low','completed',$6,NULL,$5,$5,$5)`,
    [
      LOCAL_RUN,
      LOCAL_AGENT,
      FOREIGN_RUN,
      FOREIGN_AGENT,
      now,
      FOREIGN_CANARY,
    ],
  );
  await client.query(
    `INSERT INTO evaluations(
      evaluation_id,task_id,run_id,agent_id,evaluator_type,score,pass_fail,
      rubric_json,notes,created_at
    ) VALUES
      ('eval_human_reads',$1,$2,$3,'rule',0.95,'pass','{}',
        'bounded evaluation',$7),
      ('eval_human_reads_foreign',$4,$5,$6,'rule',0.1,'fail','{}',$8,$7)`,
    [
      LOCAL_TASK,
      LOCAL_RUN,
      LOCAL_AGENT,
      FOREIGN_TASK,
      FOREIGN_RUN,
      FOREIGN_AGENT,
      now,
      FOREIGN_CANARY,
    ],
  );
  await client.query(
    `INSERT INTO audit_logs(
      audit_id,workspace_id,actor_type,actor_id,action,entity_type,entity_id,
      before_hash,after_hash,metadata_json,tamper_chain_hash,created_at
    ) VALUES
      ('aud_human_reads',$1,'agent',$2,'run.completed','runs',$3,
        NULL,NULL,$4,$5,$7),
      ('aud_human_reads_foreign',$6,'agent',$8,$9,'runs',$10,
        NULL,NULL,$11,$12,$7)`,
    [
      WORKSPACE,
      LOCAL_AGENT,
      LOCAL_RUN,
      JSON.stringify({ workspace_id: WORKSPACE, raw_omitted: true }),
      sha("local-audit-chain"),
      FOREIGN_WORKSPACE,
      now,
      FOREIGN_AGENT,
      FOREIGN_CANARY,
      FOREIGN_RUN,
      JSON.stringify({
        workspace_id: FOREIGN_WORKSPACE,
        marker: FOREIGN_CANARY,
      }),
      sha("foreign-audit-chain"),
    ],
  );
  await client.query(
    `INSERT INTO memories(
      memory_id,workspace_id,scope,memory_type,canonical_text,source_type,
      source_ref,project_id,task_id,run_id,agent_id,confidence,review_status,
      owner_user_id,ttl_review_due_at,supersedes_memory_id,access_tags,
      created_at,updated_at
    ) VALUES(
      'mem_human_reads',$1,'task','risk','bounded stale memory','run_log',
      $2,NULL,$3,$2,$4,0.8,'stale',NULL,$5,NULL,'[]',$6,$6
    )`,
    [
      WORKSPACE,
      LOCAL_RUN,
      LOCAL_TASK,
      LOCAL_AGENT,
      new Date(Date.now() - 60_000).toISOString(),
      now,
    ],
  );
  await client.query(
    `INSERT INTO runtime_connectors(
      runtime_connector_id,provider,connector_type,profile_name,base_url,
      binary_path,status,allow_real_run,require_confirm_run,trust_status,
      trust_note,trust_updated_at,observation_level,capability_manifest_json,
      capability_policy_hash,last_health_at,last_error,created_at,updated_at
    ) VALUES(
      'rtc_human_reads','hermes','http','commercial-read-contract',NULL,NULL,
      'ready',1,1,'trusted','bounded contract',$1,'ledger_summary_only','{}',
      $2,$1,NULL,$1,$1
    )`,
    [now, sha("human-read-capability")],
  );
}

async function runContract() {
  assert.ok(BASE_DSN, "AGENTOPS_POSTGRES_DSN is required");
  const admin = new Client({ connectionString: BASE_DSN });
  await admin.connect();
  try {
    await admin.query(`CREATE SCHEMA ${quotedIdentifier(SCHEMA)}`);
    const dsn = scopedDsn();
    const migration = await runPostgresSchemaCommand("migrate", {
      connectionString: dsn,
    });
    process.env.AGENTOPS_DEPLOYMENT_MODE = "production";
    process.env.AGENTOPS_CONTROL_PLANE_MODE = "postgres";
    process.env.AGENTOPS_TS_CONTROL_PLANE_MODE = "postgres";
    process.env.AGENTOPS_POSTGRES_DSN = dsn;
    process.env.AGENTOPS_POSTGRES_SSL = "0";
    process.env.AGENTOPS_HUMAN_SESSION_HMAC_KEY = SESSION_HMAC_KEY;

    const client = new Client({ connectionString: dsn });
    await client.connect();
    try {
      await seed(client);

      const agents = await listHumanAgents(browserRequest("/agents"));
      assert.equal(agents.status, 200);
      assert.deepEqual(
        (agents.body as Array<Record<string, unknown>>).map(
          (row) => row.agent_id,
        ),
        [LOCAL_AGENT],
      );
      assert.equal(
        (agents.body as Array<Record<string, unknown>>)[0]?.owner_user_id,
        null,
      );

      const performance = await readHumanAgentPerformance(
        browserRequest(`/agents/${LOCAL_AGENT}/performance`),
        LOCAL_AGENT,
      );
      const performanceBody = performance.body as Record<string, unknown>;
      assert.equal(performanceBody.total_runs, 1);
      assert.equal(performanceBody.completed_runs, 1);
      assert.equal(performanceBody.total_cost_usd, 0.25);

      const evaluations = await listHumanEvaluations(
        browserRequest("/evaluations"),
      );
      assert.deepEqual(
        (evaluations.body as Array<Record<string, unknown>>).map(
          (row) => row.evaluation_id,
        ),
        ["eval_human_reads"],
      );

      const toolCalls = await listHumanToolCalls(
        browserRequest("/tool-calls?limit=1"),
      );
      assert.deepEqual(
        (toolCalls.body as Array<Record<string, unknown>>).map(
          (row) => row.tool_call_id,
        ),
        ["tc_human_reads"],
      );

      const audit = await listHumanAudit(
        browserRequest("/audit?limit=20"),
      );
      assert.deepEqual(
        (audit.body as Array<Record<string, unknown>>).map(
          (row) => row.audit_id,
        ),
        ["aud_human_reads"],
      );

      const dashboard = await readHumanDashboard(
        browserRequest("/dashboard/metrics"),
      );
      const dashboardBody = dashboard.body as Record<string, unknown>;
      assert.equal(dashboardBody.workspace_id, WORKSPACE);
      assert.equal(dashboardBody.agents_total, 1);
      assert.equal(dashboardBody.agents_running, 1);
      assert.equal(dashboardBody.tasks_completed_total, 1);
      assert.equal(dashboardBody.total_cost_usd, 0.25);
      assert.equal(dashboardBody.failure_rate, 0);
      assert.equal(dashboardBody.stale_or_due_memories, 1);
      assert.equal(dashboardBody.control_plane, "typescript_postgres");

      const combined = JSON.stringify({
        agents: agents.body,
        performance: performance.body,
        evaluations: evaluations.body,
        tool_calls: toolCalls.body,
        audit: audit.body,
        dashboard: dashboard.body,
      });
      assert.equal(combined.includes(FOREIGN_CANARY), false);
      assert.equal(combined.includes(FOREIGN_AGENT), false);
      assert.equal(combined.includes(FOREIGN_RUN), false);
      assert.equal(combined.includes("usr_human_reads_owner"), false);
      assert.equal(combined.includes(SESSION_TOKEN), false);

      await expectCode("forbidden", () =>
        listHumanAgents(
          browserRequest("/agents", {
            queryWorkspaceId: FOREIGN_WORKSPACE,
          }),
        ));
      await expectCode("machine_credential_not_allowed", () =>
        readHumanDashboard(
          browserRequest("/dashboard/metrics", {
            machineCredential: true,
          }),
        ));
      await expectCode("agent_not_found", () =>
        readHumanAgentPerformance(
          browserRequest(`/agents/${FOREIGN_AGENT}/performance`),
          FOREIGN_AGENT,
        ));
      await expectCode("human_read_pagination_invalid", () =>
        listHumanToolCalls(browserRequest("/tool-calls?limit=999999")));
      await expectCode("human_read_query_unsupported", () =>
        listHumanAudit(browserRequest("/audit?raw=true")));

      const routeFiles = [
        "../app/api/mis/dashboard/metrics/route.ts",
        "../app/api/mis/agents/route.ts",
        "../app/api/mis/agents/[agentId]/performance/route.ts",
        "../app/api/mis/evaluations/route.ts",
        "../app/api/mis/tool-calls/route.ts",
        "../app/api/mis/audit/route.ts",
        "../src/server/controlPlane/humanReadModels.ts",
        "../src/server/controlPlane/humanReadRoute.ts",
      ];
      const routeSources = await Promise.all(
        routeFiles.map((path) =>
          readFile(new URL(path, import.meta.url), "utf8")),
      );
      const source = routeSources.join("\n");
      assert.equal(
        /server\.py|child_process|spawn\s*\(|sqlite/i.test(source),
        false,
      );
      assert.equal(
        routeSources.slice(0, 6).every((item) =>
          item.includes("ownHumanReadGet")),
        true,
      );
      assert.match(routeSources[6], /authenticateHumanMember/);
      assert.match(routeSources[6], /workspace_id=\$1/);
      assert.match(routeSources[7], /legacyPythonProxyAllowed/);

      console.log(JSON.stringify({
        ok: true,
        contract: "human_workspace_read_models_postgres_v1",
        schema_contract: migration.schema_contract,
        direct_typescript_postgres_owner: true,
        workspace_agent_authority: true,
        cross_workspace_owner_identity_omitted: true,
        workspace_evaluation_tool_audit_isolation: true,
        dashboard_workspace_isolation: true,
        human_session_required: true,
        machine_credential_rejected: true,
        cross_workspace_rejected: true,
        bounded_pagination: true,
        foreign_canary_omitted: true,
        python_api_started: false,
        sqlite_opened: false,
        token_omitted: true,
      }));
      await closeControlPlanePoolForTests();
    } finally {
      await client.end();
    }
  } finally {
    await admin.query(
      `DROP SCHEMA IF EXISTS ${quotedIdentifier(SCHEMA)} CASCADE`,
    );
    await admin.end();
  }
}

runContract().catch(() => {
  console.log(JSON.stringify({
    ok: false,
    contract: "human_workspace_read_models_postgres_v1",
    error_code: "contract_failed",
    credentials_omitted: true,
    row_data_omitted: true,
    token_omitted: true,
  }));
  process.exitCode = 1;
});
