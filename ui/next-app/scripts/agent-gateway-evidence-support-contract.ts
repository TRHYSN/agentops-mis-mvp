import assert from "node:assert/strict";
import { createHash, randomBytes } from "node:crypto";
import { readFile } from "node:fs/promises";

import { NextRequest } from "next/server";
import { Client } from "pg";

import {
  POSTGRES_MIGRATION_MANIFEST,
  runPostgresSchemaCommand,
  SCHEMA_CONTRACT,
} from "../src/server/controlPlane/schemaReadiness";

const baseDsn = String(process.env.AGENTOPS_POSTGRES_DSN || "").trim();
const schema = `agentops_evidence_${randomBytes(6).toString("hex")}`;
const token = `contract_token_${randomBytes(24).toString("hex")}`;
const tokenHash = createHash("sha256").update(token, "utf8").digest("hex");

function quotedIdentifier(value: string) {
  assert.match(value, /^[a-z][a-z0-9_]+$/);
  return `"${value}"`;
}

function scopedDsn() {
  const parsed = new URL(baseDsn);
  parsed.searchParams.set("options", `-csearch_path=${schema}`);
  return parsed.toString();
}

function headers(workspaceId = "ws_evidence", agentId = "agt_evidence") {
  return {
    Authorization: `Bearer ${token}`,
    "Content-Type": "application/json",
    "X-AgentOps-Workspace-Id": workspaceId,
    "X-AgentOps-Agent-Id": agentId,
  };
}

function postRequest(
  path: string,
  body: Record<string, unknown>,
  workspaceId = "ws_evidence",
  agentId = "agt_evidence",
) {
  return new NextRequest(`http://agentops.test${path}`, {
    method: "POST",
    headers: headers(workspaceId, agentId),
    body: JSON.stringify(body),
  });
}

function getRequest(path: string) {
  return new NextRequest(`http://agentops.test${path}`, {
    method: "GET",
    headers: headers(),
  });
}

async function json(response: Response) {
  return await response.json() as Record<string, any>;
}

async function seedFixture(client: Client) {
  const now = "2026-07-24T00:00:00.000Z";
  await client.query(
    `INSERT INTO users(user_id,name,email,role,created_at)
    VALUES('usr_evidence','Evidence Owner','evidence@example.invalid','owner',$1)`,
    [now],
  );
  await client.query(
    `INSERT INTO agents(
      agent_id,name,role,description,runtime_type,model_provider,model_name,
      status,permission_level,allowed_tools,budget_limit_usd,owner_user_id,
      created_at,updated_at
    ) VALUES(
      'agt_evidence','Evidence Worker','worker','contract worker','hermes',
      'local','hermes-agent','running','worker','[]',0,'usr_evidence',$1,$1
    )`,
    [now],
  );
  await client.query(
    `INSERT INTO tasks(
      task_id,workspace_id,title,description,requester_id,owner_agent_id,
      collaborator_agent_ids,status,priority,acceptance_criteria,risk_level,
      budget_limit_usd,created_at,updated_at
    ) VALUES(
      'tsk_evidence','ws_evidence','Governed Hermes evidence task',
      'Run a bounded real runtime and record hashed evidence','usr_evidence',
      'agt_evidence','[]','running','high',
      'Use READ PLAN RETRIEVE COMPARE EXECUTE VERIFY RECORD','high',0,$1,$1
    )`,
    [now],
  );
  await client.query(
    `INSERT INTO agent_plans(
      plan_id,workspace_id,task_id,run_id,agent_id,task_understanding,
      referenced_specs_json,referenced_memories_json,referenced_bases_json,
      proposed_files_to_change_json,risk_level,approval_required,
      execution_steps_json,verification_plan,rollback_plan,status,plan_version,
      plan_hash,verified_at,verification_result_hash,approval_id,
      approved_by_user_id,approved_at,created_at,updated_at
    ) VALUES(
      'plan_evidence','ws_evidence','tsk_evidence',NULL,'agt_evidence',
      'Record governed runtime evidence','[]','[]','[]','[]','high',0,
      '["READ","PLAN","RETRIEVE","COMPARE","EXECUTE","VERIFY","RECORD"]',
      'Verify bounded ledger evidence','No external write','approved',1,
      $1,$2,$3,NULL,'usr_evidence',$2,$2,$2
    )`,
    ["b".repeat(64), now, "c".repeat(64)],
  );
  await client.query(
    `INSERT INTO workspace_entitlements(
      workspace_id,edition,status,capabilities_json,max_agents,
      max_active_enrollments,max_active_sessions_per_agent,max_monthly_runs,
      max_monthly_cost_usd,max_concurrent_runs,effective_at,expires_at
    ) VALUES(
      'ws_evidence','team_governance','active',
      jsonb_build_object('run_start',true),10,10,10,100,100,10,
      clock_timestamp()-interval '1 hour',
      clock_timestamp()+interval '1 year'
    )`,
  );
  await client.query("BEGIN");
  try {
    await client.query(
      `SELECT reservation_id
      FROM agentops_reserve_run_cost_v10(
        'ws_evidence','run_evidence',1.000000,$1,$2,interval '1 hour'
      )`,
      [
        createHash("sha256")
          .update("metered-reserve:ws_evidence:run_evidence")
          .digest("hex"),
        createHash("sha256")
          .update("metered-request:ws_evidence:run_evidence")
          .digest("hex"),
      ],
    );
    await client.query(
      `INSERT INTO runs(
        run_id,workspace_id,task_id,agent_id,runtime_type,status,started_at,
        approval_required,agent_plan_id,plan_hash,billing_class,created_at
      ) VALUES(
        'run_evidence','ws_evidence','tsk_evidence','agt_evidence','hermes',
        'running',$1,0,'plan_evidence',$2,'metered_execution',$1
      )`,
      [now, "b".repeat(64)],
    );
    await client.query(
      `UPDATE agent_plans SET run_id='run_evidence'
      WHERE plan_id='plan_evidence'`,
    );
    await client.query("COMMIT");
  } catch (error) {
    await client.query("ROLLBACK").catch(() => undefined);
    throw error;
  }
  await client.query(
    `INSERT INTO agent_gateway_tokens(
      token_id,token_hash,workspace_id,agent_id,scopes_json,status,label,
      heartbeat_timeout_sec,created_at,expires_at,revoked_at,last_used_at,
      last_heartbeat_at
    ) VALUES(
      'tok_evidence',$1,'ws_evidence','agt_evidence',$2,'active',
      'evidence contract',300,$3,NULL,NULL,NULL,NULL
    )`,
    [
      tokenHash,
      JSON.stringify([
        "knowledge:read",
        "knowledge:write",
        "runtime_events:write",
        "memories:propose",
        "audit:write",
      ]),
      now,
    ],
  );
}

async function count(client: Client, table: string) {
  assert.match(table, /^[a-z_]+$/);
  const result = await client.query<{ count: string }>(
    `SELECT COUNT(*)::text AS count FROM ${table}`,
  );
  return Number(result.rows[0]?.count || 0);
}

async function runContract() {
  assert.ok(baseDsn);
  const admin = new Client({ connectionString: baseDsn });
  await admin.connect();
  try {
    await admin.query(`CREATE SCHEMA ${quotedIdentifier(schema)}`);
    const connectionString = scopedDsn();
    const migration = await runPostgresSchemaCommand(
      "migrate",
      { connectionString },
    );
    assert.equal(migration.schema_contract, SCHEMA_CONTRACT);
    assert.equal(migration.applied_count, POSTGRES_MIGRATION_MANIFEST.length);

    process.env.AGENTOPS_POSTGRES_DSN = connectionString;
    process.env.AGENTOPS_DEPLOYMENT_MODE = "production";
    process.env.AGENTOPS_CONTROL_PLANE_MODE = "postgres";
    const [
      knowledgeIndexRoute,
      knowledgePacketRoute,
      runtimeEventRoute,
      memoryRoute,
      auditRoute,
      dbModule,
    ] = await Promise.all([
      import("../app/api/mis/agent-gateway/knowledge/index/route"),
      import("../app/api/mis/agent-gateway/knowledge/evidence-packet/route"),
      import("../app/api/mis/agent-gateway/runtime-events/route"),
      import("../app/api/mis/agent-gateway/memories/propose/route"),
      import("../app/api/mis/agent-gateway/audit/route"),
      import("../src/server/controlPlane/db"),
    ]);

    const client = new Client({ connectionString });
    await client.connect();
    try {
      await seedFixture(client);

      const indexedResponse = await knowledgeIndexRoute.POST(postRequest(
        "/api/mis/agent-gateway/knowledge/index",
        { rebuild: false, workspace_id: "ws_evidence", agent_id: "agt_evidence" },
      ));
      const indexed = await json(indexedResponse);
      assert.equal(indexedResponse.status, 200);
      assert.equal(indexed.indexed, 5);
      assert.equal(indexed.raw_content_omitted, true);

      const packetResponse = await knowledgePacketRoute.GET(getRequest(
        "/api/mis/agent-gateway/knowledge/evidence-packet"
          + "?task_id=tsk_evidence&adapter=hermes&limit=5",
      ));
      const packet = await json(packetResponse);
      assert.equal(packetResponse.status, 200);
      assert.equal(packet.status, "ready");
      assert.ok(packet.primary_search.results.length > 0);
      assert.equal(packet.task_context.task_found, true);
      assert.equal(packet.primary_search.raw_content_omitted, true);
      assert.equal(JSON.stringify(packet).includes("Run a bounded real runtime"), false);

      const runtimeBody = {
        workspace_id: "ws_evidence",
        agent_id: "agt_evidence",
        run_id: "run_evidence",
        task_id: "tsk_evidence",
        adapter: "hermes",
        event_type: "agent_worker.adapter_execution_summary",
        status: "completed",
        input_summary: "Bearer should-not-persist",
        output_summary: "Bounded provider summary",
        prompt_hash: "d".repeat(64),
        raw_payload_hash: "e".repeat(64),
        metadata: {
          event_is_worker_summary_not_raw_trace: true,
          raw_prompt_omitted: true,
          raw_response_omitted: true,
          token_omitted: true,
        },
      };
      const firstRuntimeResponse = await runtimeEventRoute.POST(postRequest(
        "/api/mis/agent-gateway/runtime-events",
        runtimeBody,
      ));
      const firstRuntime = await json(firstRuntimeResponse);
      const replayRuntimeResponse = await runtimeEventRoute.POST(postRequest(
        "/api/mis/agent-gateway/runtime-events",
        runtimeBody,
      ));
      const replayRuntime = await json(replayRuntimeResponse);
      assert.equal(firstRuntimeResponse.status, 201);
      assert.equal(replayRuntimeResponse.status, 200);
      assert.equal(firstRuntime.outcome, "created");
      assert.equal(replayRuntime.outcome, "unchanged");
      assert.equal(
        firstRuntime.runtime_event.runtime_event_id,
        replayRuntime.runtime_event.runtime_event_id,
      );
      assert.equal(firstRuntime.runtime_event.input_summary.includes("should-not-persist"), false);
      assert.equal(await count(client, "runtime_events"), 1);

      const rawRuntimeResponse = await runtimeEventRoute.POST(postRequest(
        "/api/mis/agent-gateway/runtime-events",
        { ...runtimeBody, raw_response: "forbidden raw response" },
      ));
      assert.equal(rawRuntimeResponse.status, 400);
      assert.equal((await json(rawRuntimeResponse)).error, "raw_evidence_forbidden");

      const forgedRuntimeClaimResponse = await runtimeEventRoute.POST(postRequest(
        "/api/mis/agent-gateway/runtime-events",
        {
          ...runtimeBody,
          metadata: {
            ...runtimeBody.metadata,
            event_is_worker_summary_not_raw_trace: false,
          },
        },
      ));
      assert.equal(forgedRuntimeClaimResponse.status, 400);
      assert.equal(
        (await json(forgedRuntimeClaimResponse)).error,
        "raw_evidence_forbidden",
      );

      const memoryBody = {
        workspace_id: "ws_evidence",
        agent_id: "agt_evidence",
        run_id: "run_evidence",
        task_id: "tsk_evidence",
        scope: "project",
        memory_type: "artifact_summary",
        canonical_text: "Hermes produced bounded evidence; human review remains required.",
        source_ref: "run_evidence",
        access_tags: ["worker-loop", "hermes", "review"],
        confidence: 0.72,
      };
      const memoryCreatedResponse = await memoryRoute.POST(postRequest(
        "/api/mis/agent-gateway/memories/propose",
        memoryBody,
      ));
      const memoryCreated = await json(memoryCreatedResponse);
      const memoryReplayResponse = await memoryRoute.POST(postRequest(
        "/api/mis/agent-gateway/memories/propose",
        memoryBody,
      ));
      const memoryReplay = await json(memoryReplayResponse);
      assert.equal(memoryCreatedResponse.status, 201);
      assert.equal(memoryReplayResponse.status, 200);
      assert.equal(memoryCreated.memory.memory_id, memoryReplay.memory.memory_id);
      assert.equal(memoryCreated.memory.review_status, "candidate");
      assert.equal(memoryCreated.memory.workspace_id, "ws_evidence");
      assert.equal(await count(client, "memories"), 1);

      const auditBody = {
        workspace_id: "ws_evidence",
        agent_id: "agt_evidence",
        run_id: "run_evidence",
        task_id: "tsk_evidence",
        action: "agent_worker.task_processed",
        entity_type: "runs",
        entity_id: "run_evidence",
        metadata: {
          adapter: "hermes",
          prompt_profile_version: "worker_prompt_profiles_v1",
          secret_boundary: "trusted_worker_client_v1",
          credential_transport: "trusted_worker_client_only",
          model_visible_credentials: false,
          secrets_in_prompt: false,
          secrets_in_output: false,
          raw_prompt_omitted: true,
          raw_response_omitted: true,
          token_omitted: true,
        },
      };
      const auditCreatedResponse = await auditRoute.POST(postRequest(
        "/api/mis/agent-gateway/audit",
        auditBody,
      ));
      const auditCreated = await json(auditCreatedResponse);
      const auditReplayResponse = await auditRoute.POST(postRequest(
        "/api/mis/agent-gateway/audit",
        auditBody,
      ));
      const auditReplay = await json(auditReplayResponse);
      assert.equal(auditCreatedResponse.status, 201);
      assert.equal(auditReplayResponse.status, 200);
      assert.equal(auditCreated.audit_id, auditReplay.audit_id);
      assert.equal(auditReplay.outcome, "unchanged");

      const forgedProfileVersionResponse = await auditRoute.POST(postRequest(
        "/api/mis/agent-gateway/audit",
        {
          ...auditBody,
          metadata: {
            ...auditBody.metadata,
            prompt_profile_version: "unexpected-profile-version",
          },
        },
      ));
      assert.equal(forgedProfileVersionResponse.status, 400);
      assert.equal(
        (await json(forgedProfileVersionResponse)).error,
        "raw_evidence_forbidden",
      );

      const forgedBoundaryResponse = await auditRoute.POST(postRequest(
        "/api/mis/agent-gateway/audit",
        {
          ...auditBody,
          metadata: {
            ...auditBody.metadata,
            secret_boundary: "untrusted_boundary",
          },
        },
      ));
      assert.equal(forgedBoundaryResponse.status, 400);
      assert.equal((await json(forgedBoundaryResponse)).error, "raw_evidence_forbidden");

      const crossWorkspaceResponse = await memoryRoute.POST(postRequest(
        "/api/mis/agent-gateway/memories/propose",
        { ...memoryBody, workspace_id: "ws_other" },
        "ws_other",
      ));
      assert.equal(crossWorkspaceResponse.status, 403);

      await assert.rejects(
        client.query(
          `UPDATE runtime_events SET status='failed'
          WHERE runtime_event_id=$1`,
          [firstRuntime.runtime_event.runtime_event_id],
        ),
        (error: unknown) => (error as { code?: string }).code === "55000",
      );
      await assert.rejects(
        client.query(
          `INSERT INTO runtime_events(
            runtime_event_id,workspace_id,event_type,status,run_id,task_id,
            agent_id,created_at
          ) VALUES(
            'rte_cross_workspace','ws_other','agent_worker.invalid','completed',
            'run_evidence','tsk_evidence','agt_evidence',$1
          )`,
          [new Date().toISOString()],
        ),
        (error: unknown) => (error as { code?: string }).code === "23514",
      );

      const storedLeak = await client.query<{ count: string }>(
        `SELECT COUNT(*)::text AS count
        FROM runtime_events
        WHERE COALESCE(input_summary,'') LIKE '%should-not-persist%'
          OR COALESCE(output_summary,'') LIKE '%should-not-persist%'
          OR COALESCE(error_message,'') LIKE '%should-not-persist%'`,
      );
      assert.equal(storedLeak.rows[0]?.count, "0");

      const sourceFiles = await Promise.all([
        readFile(
          new URL("../src/server/controlPlane/agentGatewayEvidenceSupport.ts", import.meta.url),
          "utf8",
        ),
        readFile(
          new URL("../src/server/controlPlane/evidenceRouteOwner.ts", import.meta.url),
          "utf8",
        ),
      ]);
      assert.equal(
        sourceFiles.some((source) => (
          /spawn\s*\(/.test(source)
          || /server\.py/.test(source)
          || /python_api/.test(source)
        )),
        false,
      );

      console.log(JSON.stringify({
        contract: "agent_gateway_evidence_support_postgres_v1",
        ok: true,
        schema_contract: migration.schema_contract,
        knowledge_indexed: indexed.indexed,
        knowledge_packet_ready: true,
        runtime_event_idempotent: true,
        runtime_event_append_only: true,
        memory_candidate_idempotent: true,
        audit_idempotent: true,
        workspace_isolation: true,
        raw_evidence_rejected: true,
        credential_fragment_not_persisted: true,
        production_python_dependency: false,
        token_omitted: true,
      }));
    } finally {
      await client.end();
      await dbModule.closeControlPlanePoolForTests();
    }
  } finally {
    await admin.query(`DROP SCHEMA IF EXISTS ${quotedIdentifier(schema)} CASCADE`);
    await admin.end();
  }
}

runContract().catch(() => {
  console.log(JSON.stringify({
    contract: "agent_gateway_evidence_support_postgres_v1",
    ok: false,
    error_code: "contract_failed",
    credentials_omitted: true,
    row_data_omitted: true,
    token_omitted: true,
  }));
  process.exitCode = 1;
});
