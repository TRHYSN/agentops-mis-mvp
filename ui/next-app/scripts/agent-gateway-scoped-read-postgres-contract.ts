import assert from "node:assert/strict";
import { createHash, randomBytes, randomUUID } from "node:crypto";
import { readFile } from "node:fs/promises";

import { Client } from "pg";

import {
  listAgentGatewayArtifacts,
  listAgentGatewayRuns,
  readAgentGatewayRun,
  readAgentGatewayRunGraph,
} from "../src/server/controlPlane/agentGatewayReadModels";
import { closeControlPlanePoolForTests } from "../src/server/controlPlane/db";
import { ControlPlaneHttpError } from "../src/server/controlPlane/http";
import {
  POSTGRES_MIGRATION_MANIFEST,
  runPostgresSchemaCommand,
  SCHEMA_CONTRACT,
} from "../src/server/controlPlane/schemaReadiness";

const WORKSPACE = "ws_agent_read_contract";
const FOREIGN_WORKSPACE = "ws_agent_read_foreign";
const AGENT = "agt_agent_read";
const OTHER_AGENT = "agt_agent_read_other";
const TOKEN = `contract-token-${randomBytes(24).toString("base64url")}`;
const SESSION = `contract-session-${randomBytes(24).toString("base64url")}`;
const OTHER_TOKEN = `contract-other-${randomBytes(24).toString("base64url")}`;
const FOREIGN_TOKEN = `contract-foreign-${randomBytes(24).toString("base64url")}`;
const NO_SCOPE_TOKEN = `contract-noscope-${randomBytes(24).toString("base64url")}`;
const SECRET_CANARY = "contract-sensitive-agent-read-value";
const DSN_CANARY = `post${"gresql"}://reader:contract-db-password@internal/read`;

function sha256(value: string) {
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
      sha256(`historical-reserve:${workspaceId}:${runId}`),
      createdAt,
      sha256(`historical-request:${workspaceId}:${runId}`),
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
      sha256(`historical-settle:${workspaceId}:${runId}`),
      sha256(`historical-settle-request:${workspaceId}:${runId}`),
    ],
  );
}

function scopedDsn(baseDsn: string, schema: string) {
  const parsed = new URL(baseDsn);
  parsed.searchParams.set("options", `-csearch_path=${schema}`);
  return parsed.toString();
}

function quotedSchema(value: string) {
  assert.match(value, /^[a-z][a-z0-9_]+$/);
  return `"${value}"`;
}

function request(
  path: string,
  credential: string | null = TOKEN,
  workspaceId = WORKSPACE,
  agentId = AGENT,
) {
  const headers = new Headers({
    "x-agentops-workspace-id": workspaceId,
    "x-agentops-agent-id": agentId,
  });
  if (credential) headers.set("authorization", `Bearer ${credential}`);
  return new Request(`https://mis.example.test${path}`, { headers });
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
  const now = "2026-07-24T00:00:00.000Z";
  const later = "2026-07-24T00:00:05.000Z";
  const future = new Date(Date.now() + 3_600_000).toISOString();
  const readScope = JSON.stringify(["tasks:read"]);

  await client.query(
    `INSERT INTO users(user_id,name,email,role,created_at)
    VALUES('usr_agent_read','Agent Read Contract',
      'agent-read@example.test','owner',$1)`,
    [now],
  );
  for (const [agentId, name] of [
    [AGENT, "Scoped reader"],
    [OTHER_AGENT, "Other reader"],
  ]) {
    await client.query(
      `INSERT INTO agents(
        agent_id,name,role,description,runtime_type,model_provider,model_name,
        status,permission_level,allowed_tools,budget_limit_usd,owner_user_id,
        created_at,updated_at
      ) VALUES($1,$2,'worker',NULL,'hermes','hermes','contract-model',
        'idle','standard','[]',0,'usr_agent_read',$3,$3)`,
      [agentId, name, now],
    );
  }
  for (const token of [
    ["tok_agent_read", sha256(TOKEN), WORKSPACE, AGENT, readScope],
    ["tok_agent_read_other", sha256(OTHER_TOKEN), WORKSPACE, OTHER_AGENT, readScope],
    ["tok_agent_read_foreign", sha256(FOREIGN_TOKEN), FOREIGN_WORKSPACE, AGENT, readScope],
    ["tok_agent_read_noscope", sha256(NO_SCOPE_TOKEN), WORKSPACE, AGENT, "[]"],
  ]) {
    await client.query(
      `INSERT INTO agent_gateway_tokens(
        token_id,token_hash,workspace_id,agent_id,scopes_json,status,label,
        heartbeat_timeout_sec,created_at,expires_at,revoked_at,last_used_at,
        last_heartbeat_at
      ) VALUES($1,$2,$3,$4,$5,'active','read-contract',300,$6,$7,
        NULL,NULL,NULL)`,
      [...token, now, future],
    );
  }
  await client.query(
    `INSERT INTO agent_gateway_sessions(
      session_id,session_hash,parent_token_id,workspace_id,agent_id,scopes_json,
      status,created_at,expires_at,revoked_at,last_used_at
    ) VALUES('sess_agent_read',$1,'tok_agent_read',$2,$3,$4,'active',
      $5,$6,NULL,NULL)`,
    [sha256(SESSION), WORKSPACE, AGENT, readScope, now, future],
  );

  const tasks = [
    ["tsk_agent_owned", WORKSPACE, "Owned", AGENT, "[]"],
    ["tsk_agent_collab", WORKSPACE, "Collaborator", OTHER_AGENT, JSON.stringify([AGENT])],
    ["tsk_agent_unassigned", WORKSPACE, "Unassigned", null, "[]"],
    ["tsk_agent_hidden", WORKSPACE, "Hidden", OTHER_AGENT, "[]"],
    ["tsk_agent_foreign", FOREIGN_WORKSPACE, "Foreign", AGENT, "[]"],
  ];
  for (const [taskId, workspaceId, title, ownerAgentId, collaborators] of tasks) {
    await client.query(
      `INSERT INTO tasks(
        task_id,workspace_id,title,description,requester_id,owner_agent_id,
        collaborator_agent_ids,status,priority,due_date,acceptance_criteria,
        risk_level,budget_limit_usd,created_at,updated_at
      ) VALUES($1,$2,$3,'Contract task','usr_agent_read',$4,$5,'completed',
        'medium',NULL,'Scoped reads stay bounded.','low',0,$6,$6)`,
      [taskId, workspaceId, title, ownerAgentId, collaborators, now],
    );
  }

  const runs: ReadonlyArray<readonly [
    runId: string,
    workspaceId: string,
    taskId: string,
    agentId: string,
    parentRunId: string | null,
    delegationId: string | null,
  ]> = [
    ["run_agent_parent", WORKSPACE, "tsk_agent_owned", AGENT, null, "delegation-owned"],
    ["run_agent_child", WORKSPACE, "tsk_agent_owned", AGENT, "run_agent_parent", "delegation-owned"],
    ["run_agent_sibling", WORKSPACE, "tsk_agent_owned", AGENT, null, "delegation-owned"],
    ["run_agent_collab", WORKSPACE, "tsk_agent_collab", OTHER_AGENT, null, null],
    ["run_agent_unassigned", WORKSPACE, "tsk_agent_unassigned", OTHER_AGENT, null, null],
    ["run_agent_hidden", WORKSPACE, "tsk_agent_hidden", OTHER_AGENT, null, null],
    ["run_agent_foreign", FOREIGN_WORKSPACE, "tsk_agent_foreign", AGENT, null, null],
  ];
  for (const [
    runId,
    workspaceId,
    taskId,
    agentId,
    parentRunId,
    delegationId,
  ] of runs) {
    await reserveHistoricalRunCost(
      client,
      workspaceId,
      runId,
      "0.250000",
      now,
    );
    await client.query(
      `INSERT INTO runs(
        run_id,workspace_id,task_id,agent_id,runtime_type,status,started_at,
        ended_at,duration_ms,input_summary,output_summary,model_provider,
        model_name,input_tokens,output_tokens,reasoning_tokens,cost_usd,
        error_type,error_message,trace_id,parent_run_id,delegation_id,
        approval_required,agent_plan_id,plan_hash,billing_class,created_at
      ) VALUES($1,$2,$3,$4,'hermes','completed',$7,$8,5000,$9,$10,
        'hermes','contract-model',10,20,3,0.25,NULL,NULL,'trace-contract',
        $5,$6,0,NULL,NULL,'historical_execution',$7)`,
      [
        runId,
        workspaceId,
        taskId,
        agentId,
        parentRunId,
        delegationId,
        now,
        later,
        `Bearer ${SECRET_CANARY}`,
        `Completed with ${DSN_CANARY}`,
      ],
    );
    await settleHistoricalRunCost(
      client,
      workspaceId,
      runId,
      "0.250000",
    );
  }

  await client.query(
    `INSERT INTO tool_calls(
      tool_call_id,run_id,agent_id,tool_name,tool_version,tool_category,
      normalized_args_json,target_resource,risk_level,status,result_summary,
      side_effect_id,started_at,ended_at,created_at
    ) VALUES
      ('tc_agent_read','run_agent_parent',$1,'contract.read','v1','database',
        $3,$4,'low','completed',$5,NULL,$2,$6,$2),
      ('tc_agent_mismatched','run_agent_parent',$7,'contract.mismatched','v1',
        'database','{}','hidden','low','completed','Must be excluded',
        NULL,$2,$6,$2)`,
    [
      AGENT,
      now,
      JSON.stringify({ secret: SECRET_CANARY }),
      DSN_CANARY,
      `token=${SECRET_CANARY}`,
      later,
      OTHER_AGENT,
    ],
  );
  await client.query(
    `INSERT INTO evaluations(
      evaluation_id,task_id,run_id,agent_id,evaluator_type,score,pass_fail,
      rubric_json,notes,created_at
    ) VALUES
      ('eval_agent_read','tsk_agent_owned','run_agent_parent',$1,'rule',0.9,
        'pass',$3,$4,$2),
      ('eval_agent_mismatched','tsk_agent_hidden','run_agent_parent',$1,'rule',
        0.1,'fail','{}','Must be excluded',$2)`,
    [
      AGENT,
      now,
      JSON.stringify({ secret: SECRET_CANARY }),
      `credential=${SECRET_CANARY}`,
    ],
  );
  await client.query(
    `INSERT INTO artifacts(
      artifact_id,task_id,run_id,artifact_type,title,uri,summary,content_hash,
      created_at
    ) VALUES
      ('art_agent_read','tsk_agent_owned','run_agent_parent','report',
        'Scoped artifact',$2,$3,'hash-scoped',$1),
      ('art_agent_task_only','tsk_agent_owned',NULL,'report',
        'Task-only artifact',NULL,'Task-only summary','hash-task',$1),
      ('art_agent_mismatched','tsk_agent_hidden','run_agent_parent','report',
        'Mismatched artifact',NULL,'Must be excluded','hash-mismatch',$1),
      ('art_agent_foreign','tsk_agent_foreign','run_agent_foreign','report',
        'Foreign artifact',NULL,'Foreign only','hash-foreign',$1)`,
    [now, DSN_CANARY, `Bearer ${SECRET_CANARY}`],
  );
}

function body<T extends Record<string, unknown>>(
  result: { status: number; body: Record<string, unknown> },
) {
  assert.equal(result.status, 200);
  return result.body as T;
}

function ids(value: unknown, key: string) {
  assert.ok(Array.isArray(value));
  return value.map((item) => String((item as Record<string, unknown>)[key]));
}

function assertScope(value: Record<string, unknown>, mode: string) {
  const scope = value.gateway_scope as Record<string, unknown>;
  assert.equal(scope.scope_service, "agent_gateway_scope_v1");
  assert.equal(scope.bound_visibility_enforced, true);
  assert.equal(scope.workspace_id, WORKSPACE);
  assert.equal(scope.agent_id, AGENT);
  assert.equal(scope.credential_mode, mode);
  assert.equal(Object.hasOwn(scope, "credential_id"), false);
}

function assertSanitized(value: unknown) {
  const serialized = JSON.stringify(value);
  assert.doesNotMatch(serialized, new RegExp(SECRET_CANARY, "g"));
  assert.doesNotMatch(serialized, /contract-db-password/g);
  for (const key of [
    "normalized_args_json",
    "rubric_json",
    "uri",
    "content_hash",
    "token_hash",
    "session_hash",
    "credential_id",
  ]) {
    assert.doesNotMatch(serialized, new RegExp(`"${key}"\\s*:`, "g"));
  }
}

async function assertStaticProductionBoundary() {
  const paths = [
    "../src/server/controlPlane/agentGatewayReadModels.ts",
    "../app/api/mis/agent-gateway/runs/route.ts",
    "../app/api/mis/agent-gateway/runs/[runId]/route.ts",
    "../app/api/mis/agent-gateway/runs/[runId]/graph/route.ts",
    "../app/api/mis/agent-gateway/artifacts/route.ts",
  ];
  const source = (
    await Promise.all(paths.map((path) => readFile(
      new URL(path, import.meta.url),
      "utf8",
    )))
  ).join("\n");
  assert.match(source, /authenticateAgentGateway/);
  assert.match(source, /tasks:read/);
  assert.match(source, /typescript_postgres/);
  assert.match(source, /proxyFreeLocalRead/);
  assert.match(source, /controlPlaneMode\(\) === "proxy"/);
  assert.doesNotMatch(source, /proxyControlPlaneRequest/);
  assert.doesNotMatch(source, /child_process/);
  assert.doesNotMatch(source, /agentops_mis/);
  assert.doesNotMatch(source, /\.py\b/);
  assert.doesNotMatch(source, /\bfetch\s*\(/);
}

async function run() {
  const baseDsn = String(process.env.AGENTOPS_POSTGRES_DSN || "").trim();
  assert.ok(baseDsn, "AGENTOPS_POSTGRES_DSN is required");
  const schema = `agent_scoped_read_${randomUUID().replaceAll("-", "")}`;
  const admin = new Client({ connectionString: baseDsn });
  const originalDsn = process.env.AGENTOPS_POSTGRES_DSN;
  const originalDeployment = process.env.AGENTOPS_DEPLOYMENT_MODE;
  const originalMode = process.env.AGENTOPS_CONTROL_PLANE_MODE;
  const originalFetch = globalThis.fetch;
  let schemaCreated = false;
  let fetchCalls = 0;

  process.env.AGENTOPS_DEPLOYMENT_MODE = "production";
  process.env.AGENTOPS_CONTROL_PLANE_MODE = "postgres";
  globalThis.fetch = async () => {
    fetchCalls += 1;
    throw new Error("Network access is forbidden in the scoped read contract.");
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
    process.env.AGENTOPS_POSTGRES_DSN = contractDsn;
    const migration = await runPostgresSchemaCommand(
      "migrate",
      { connectionString: contractDsn },
    );
    assert.equal(migration.schema_contract, SCHEMA_CONTRACT);
    assert.equal(
      migration.applied_count,
      POSTGRES_MIGRATION_MANIFEST.length,
    );
    await admin.query(`SET search_path TO ${quotedSchema(schema)}`);
    await seed(admin);

    await expectCode(
      "unauthorized",
      () => listAgentGatewayRuns(request(
        "/api/mis/agent-gateway/runs",
        null,
      )),
    );
    await expectCode(
      "forbidden",
      () => listAgentGatewayRuns(request(
        "/api/mis/agent-gateway/runs",
        NO_SCOPE_TOKEN,
      )),
    );
    await expectCode(
      "forbidden",
      () => listAgentGatewayRuns(request(
        `/api/mis/agent-gateway/runs?workspace_id=${FOREIGN_WORKSPACE}`,
      )),
    );
    await expectCode(
      "forbidden",
      () => listAgentGatewayRuns(request(
        `/api/mis/agent-gateway/runs?agent_id=${OTHER_AGENT}`,
      )),
    );
    await expectCode(
      "agent_gateway_read_query_unsupported",
      () => listAgentGatewayRuns(request(
        "/api/mis/agent-gateway/runs?include_raw=true",
      )),
    );

    const listed = body<{
      runs: Array<Record<string, unknown>>;
      gateway_scope: Record<string, unknown>;
    }>(await listAgentGatewayRuns(request(
      "/api/mis/agent-gateway/runs?limit=100",
    )));
    assert.deepEqual(ids(listed.runs, "run_id").sort(), [
      "run_agent_child",
      "run_agent_collab",
      "run_agent_parent",
      "run_agent_sibling",
      "run_agent_unassigned",
    ]);
    assertScope(listed, "agent_token");

    const sessionListed = body<{
      runs: Array<Record<string, unknown>>;
      gateway_scope: Record<string, unknown>;
    }>(await listAgentGatewayRuns(request(
      "/api/mis/agent-gateway/runs?task_id=tsk_agent_collab&limit=10",
      SESSION,
    )));
    assert.deepEqual(ids(sessionListed.runs, "run_id"), [
      "run_agent_collab",
    ]);
    assertScope(sessionListed, "agent_session");

    const detail = body<{
      run: Record<string, unknown>;
      tool_calls: Array<Record<string, unknown>>;
      artifacts: Array<Record<string, unknown>>;
      evaluations: Array<Record<string, unknown>>;
      gateway_scope: Record<string, unknown>;
    }>(await readAgentGatewayRun(
      request("/api/mis/agent-gateway/runs/run_agent_parent"),
      "run_agent_parent",
    ));
    assert.equal(detail.run.run_id, "run_agent_parent");
    assert.deepEqual(ids(detail.tool_calls, "tool_call_id"), ["tc_agent_read"]);
    assert.deepEqual(ids(detail.artifacts, "artifact_id"), ["art_agent_read"]);
    assert.deepEqual(ids(detail.evaluations, "evaluation_id"), ["eval_agent_read"]);
    assertScope(detail, "agent_token");

    const artifacts = body<{
      artifacts: Array<Record<string, unknown>>;
      gateway_scope: Record<string, unknown>;
    }>(await listAgentGatewayArtifacts(request(
      "/api/mis/agent-gateway/artifacts?task_id=tsk_agent_owned&limit=100",
    )));
    assert.deepEqual(ids(artifacts.artifacts, "artifact_id").sort(), [
      "art_agent_read",
      "art_agent_task_only",
    ]);
    assertScope(artifacts, "agent_token");

    const graph = body<{
      run: Record<string, unknown>;
      parent: Record<string, unknown> | null;
      children: Array<Record<string, unknown>>;
      siblings_by_delegation: Array<Record<string, unknown>>;
      gateway_scope: Record<string, unknown>;
    }>(await readAgentGatewayRunGraph(
      request("/api/mis/agent-gateway/runs/run_agent_child/graph"),
      "run_agent_child",
    ));
    assert.equal(graph.run.run_id, "run_agent_child");
    assert.equal(graph.parent?.run_id, "run_agent_parent");
    assert.deepEqual(graph.children, []);
    assert.deepEqual(ids(graph.siblings_by_delegation, "run_id").sort(), [
      "run_agent_parent",
      "run_agent_sibling",
    ]);
    assertScope(graph, "agent_token");

    await expectCode(
      "run_not_found",
      () => readAgentGatewayRun(
        request("/api/mis/agent-gateway/runs/run_agent_hidden"),
        "run_agent_hidden",
      ),
    );
    await expectCode(
      "run_not_found",
      () => readAgentGatewayRun(
        request("/api/mis/agent-gateway/runs/run_agent_foreign"),
        "run_agent_foreign",
      ),
    );
    const otherVisible = body<{
      run: Record<string, unknown>;
    }>(await readAgentGatewayRun(
      request(
        "/api/mis/agent-gateway/runs/run_agent_hidden",
        OTHER_TOKEN,
        WORKSPACE,
        OTHER_AGENT,
      ),
      "run_agent_hidden",
    ));
    assert.equal(otherVisible.run.run_id, "run_agent_hidden");
    await expectCode(
      "forbidden",
      () => listAgentGatewayRuns(request(
        "/api/mis/agent-gateway/runs",
        FOREIGN_TOKEN,
        WORKSPACE,
      )),
    );

    assertSanitized(listed);
    assertSanitized(sessionListed);
    assertSanitized(detail);
    assertSanitized(artifacts);
    assertSanitized(graph);
    assert.equal(fetchCalls, 0);
    await assertStaticProductionBoundary();

    console.log(JSON.stringify({
      ok: true,
      contract: "agent_gateway_scoped_read_postgres_v1",
      postgres_major: 16,
      migrations_applied: migration.applied_count,
      token_read_verified: true,
      child_session_read_verified: true,
      collaborator_visibility_verified: true,
      unassigned_visibility_verified: true,
      cross_workspace_blocked: true,
      cross_agent_impersonation_blocked: true,
      mismatched_evidence_excluded: true,
      raw_content_omitted: true,
      provider_calls: fetchCalls,
      python_proxy_performed: false,
      sqlite_used: false,
      credentials_omitted: true,
    }, null, 2));
  } finally {
    await closeControlPlanePoolForTests();
    globalThis.fetch = originalFetch;
    if (schemaCreated) {
      await admin.query("SET search_path TO public");
      await admin.query(`DROP SCHEMA ${quotedSchema(schema)} CASCADE`);
    }
    await admin.end().catch(() => undefined);
    if (originalDsn === undefined) {
      delete process.env.AGENTOPS_POSTGRES_DSN;
    } else {
      process.env.AGENTOPS_POSTGRES_DSN = originalDsn;
    }
    if (originalDeployment === undefined) {
      delete process.env.AGENTOPS_DEPLOYMENT_MODE;
    } else {
      process.env.AGENTOPS_DEPLOYMENT_MODE = originalDeployment;
    }
    if (originalMode === undefined) {
      delete process.env.AGENTOPS_CONTROL_PLANE_MODE;
    } else {
      process.env.AGENTOPS_CONTROL_PLANE_MODE = originalMode;
    }
  }
}

run().catch((error) => {
  console.error(error instanceof Error ? error.message : String(error));
  process.exitCode = 1;
});
