import assert from "node:assert/strict";
import { createHash, randomBytes } from "node:crypto";
import { readFile } from "node:fs/promises";

import { NextRequest } from "next/server";
import { Client } from "pg";

import { ControlPlaneHttpError } from "../src/server/controlPlane/http";
import {
  createPostgresRoleBoundaryFixture,
} from "./postgres-role-boundary-test-helper";

const baseDsn = String(process.env.AGENTOPS_POSTGRES_DSN || "").trim();
const token = `contract_loop_token_${randomBytes(24).toString("hex")}`;
const tokenHash = createHash("sha256").update(token, "utf8").digest("hex");

function request(agentId = "agt_loop_contract") {
  return new NextRequest(
    `http://agentops.test/api/operator/loop-supervision`
      + `?adapter=hermes&task_id=tsk_loop_contract&agent_id=${agentId}&limit=8`,
    {
      method: "GET",
      headers: {
        Authorization: `Bearer ${token}`,
        "X-AgentOps-Workspace-Id": "ws_loop_contract",
        "X-AgentOps-Agent-Id": "agt_loop_contract",
      },
    },
  );
}

async function seed(client: Client) {
  const now = new Date().toISOString();
  await client.query(
    `INSERT INTO users(user_id,name,email,role,created_at)
    VALUES('usr_loop_contract','Loop Owner','loop-owner@example.invalid','owner',$1)`,
    [now],
  );
  await client.query(
    `INSERT INTO agents(
      agent_id,name,role,description,runtime_type,model_provider,model_name,
      status,permission_level,allowed_tools,budget_limit_usd,owner_user_id,
      created_at,updated_at
    ) VALUES(
      'agt_loop_contract','Loop Hermes','worker','bounded contract worker',
      'hermes','hermes','hermes-agent','idle','standard','[]',0,
      'usr_loop_contract',$1,$1
    )`,
    [now],
  );
  await client.query(
    `INSERT INTO tasks(
      task_id,workspace_id,title,description,requester_id,owner_agent_id,
      collaborator_agent_ids,status,priority,acceptance_criteria,risk_level,
      budget_limit_usd,created_at,updated_at
    ) VALUES(
      'tsk_loop_contract','ws_loop_contract','Bounded loop contract',
      'fixture-marker-must-not-appear','usr_loop_contract','agt_loop_contract',
      '[]','planned','high','Verify a bounded real worker admission','low',0,$1,$1
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
      'plan_loop_contract','ws_loop_contract','tsk_loop_contract',NULL,
      'agt_loop_contract','Verify a bounded worker admission','[]','[]','[]','[]',
      'low',0,$1,'Verify provider evidence','No external write','submitted',1,
      $2,$3,$4,NULL,NULL,NULL,$3,$3
    )`,
    [
      JSON.stringify([
        "READ",
        "PLAN",
        "RETRIEVE",
        "COMPARE",
        "EXECUTE",
        "VERIFY",
        "RECORD",
      ]),
      "a".repeat(64),
      now,
      "b".repeat(64),
    ],
  );
  await client.query(
    `INSERT INTO runtime_connectors(
      runtime_connector_id,provider,connector_type,profile_name,base_url,
      binary_path,status,allow_real_run,require_confirm_run,trust_status,
      trust_note,trust_updated_at,last_health_at,last_error,created_at,updated_at
    ) VALUES(
      'rtc_loop_contract','agent_gateway','local','TS loop contract',NULL,NULL,
      'ready',1,1,'trusted','ephemeral contract',$1,$1,NULL,$1,$1
    )`,
    [now],
  );
  await client.query(
    `INSERT INTO agent_gateway_tokens(
      token_id,token_hash,workspace_id,agent_id,scopes_json,status,label,
      heartbeat_timeout_sec,created_at,expires_at,revoked_at,last_used_at,
      last_heartbeat_at
    ) VALUES(
      'tok_loop_contract',$1,'ws_loop_contract','agt_loop_contract',
      '["tasks:read"]','active','loop contract',300,$2,NULL,NULL,NULL,NULL
    )`,
    [tokenHash, now],
  );
}

async function runContract() {
  assert.ok(baseDsn, "AGENTOPS_POSTGRES_DSN is required");
  const roleFixture = await createPostgresRoleBoundaryFixture(
    baseDsn,
    "loop_supervision",
  );
  const restoreRuntimeEnvironment =
    roleFixture.activateRuntimeEnvironment();
  try {
    const migration = roleFixture.migration;
    process.env.AGENTOPS_TS_CONTROL_PLANE_MODE = "postgres";
    process.env.AGENTOPS_POSTGRES_SSL = "0";

    const client = new Client({
      connectionString: roleFixture.ownerDsn,
    });
    await client.connect();
    try {
      await seed(client);
      const dbModule = await import("../src/server/controlPlane/db");
      const { getOperatorLoopSupervision } = await import(
        "../src/server/controlPlane/loopSupervision"
      );
      const ready = await getOperatorLoopSupervision(request());
      const readyItem = (ready.body.items as Array<Record<string, any>>)[0];
      assert.equal(ready.status, 200);
      assert.equal(ready.body.provider, "agentops-typescript-postgres");
      assert.equal(readyItem.status, "ready_to_confirm");
      assert.equal(readyItem.can_confirm_bounded_loop, true);
      assert.deepEqual(readyItem.blockers, []);
      assert.equal(JSON.stringify(ready.body).includes("fixture-marker"), false);

      await assert.rejects(
        getOperatorLoopSupervision(request("agt_other")),
        (error: unknown) => (
          error instanceof ControlPlaneHttpError
          && error.status === 403
          && error.code === "forbidden"
        ),
      );

      await client.query(
        `UPDATE agent_plans SET verified_at=NULL
        WHERE plan_id='plan_loop_contract'`,
      );
      const unverified = await getOperatorLoopSupervision(request());
      const unverifiedItem = (
        unverified.body.items as Array<Record<string, any>>
      )[0];
      assert.equal(unverifiedItem.status, "blocked");
      assert.equal(unverifiedItem.blockers.includes("verified_agent_plan"), true);

      await client.query(
        `UPDATE agent_plans SET verified_at=$1
        WHERE plan_id='plan_loop_contract'`,
        [new Date().toISOString()],
      );
      await client.query(
        `UPDATE runtime_connectors SET trust_status='untrusted'
        WHERE runtime_connector_id='rtc_loop_contract'`,
      );
      const untrusted = await getOperatorLoopSupervision(request());
      const untrustedItem = (
        untrusted.body.items as Array<Record<string, any>>
      )[0];
      assert.equal(untrustedItem.status, "blocked");
      assert.equal(
        untrustedItem.blockers.includes("trusted_runtime_connector"),
        true,
      );

      const [ownerSource, routeSource, nextConfig] = await Promise.all([
        readFile(
          new URL("../src/server/controlPlane/loopSupervision.ts", import.meta.url),
          "utf8",
        ),
        readFile(
          new URL(
            "../app/api/mis/operator/loop-supervision/route.ts",
            import.meta.url,
          ),
          "utf8",
        ),
        readFile(new URL("../next.config.mjs", import.meta.url), "utf8"),
      ]);
      assert.equal(
        /server\.py|spawn\s*\(|python_api/.test(`${ownerSource}\n${routeSource}`),
        false,
      );
      assert.match(routeSource, /getOperatorLoopSupervision/);
      assert.match(nextConfig, /\/api\/operator\/loop-supervision/);

      console.log(JSON.stringify({
        contract: "nextjs_postgres_worker_loop_supervision_v1",
        ok: true,
        schema_contract: migration.schema_contract,
        direct_typescript_postgres_restricted_runtime: true,
        database_role_boundary_verified: true,
        bearer_tasks_read_scope: true,
        workspace_agent_task_assignment: true,
        verified_plan_method_gate: true,
        trusted_fresh_connector_gate: true,
        unverified_plan_blocked: true,
        untrusted_connector_blocked: true,
        raw_task_content_omitted: true,
        read_only: true,
        production_python_dependency: false,
        token_omitted: true,
      }));
      await dbModule.closeControlPlanePoolForTests();
    } finally {
      await client.end();
    }
  } finally {
    restoreRuntimeEnvironment();
    await roleFixture.cleanup();
  }
}

runContract().catch(() => {
  console.log(JSON.stringify({
    contract: "nextjs_postgres_worker_loop_supervision_v1",
    ok: false,
    error_code: "contract_failed",
    credentials_omitted: true,
    row_data_omitted: true,
    token_omitted: true,
  }));
  process.exitCode = 1;
});
