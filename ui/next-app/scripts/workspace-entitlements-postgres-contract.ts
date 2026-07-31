import assert from "node:assert/strict";
import { randomBytes, randomUUID } from "node:crypto";
import { readFile } from "node:fs/promises";

import { Client, type PoolClient } from "pg";

import {
  POSTGRES_MIGRATION_MANIFEST,
  runPostgresSchemaCommand,
  SCHEMA_CONTRACT,
} from "../src/server/controlPlane/schemaReadiness";
import {
  evaluateWorkspaceEntitlement,
  WORKSPACE_ENTITLEMENT_DECISION_CONTRACT,
  type WorkspaceEntitlementDecision,
  type WorkspaceEntitlementEvaluation,
} from "../src/server/controlPlane/workspaceEntitlements";

const CAPABILITIES = Object.freeze({
  enrollment_issue: true,
  session_issue: true,
  run_start: true,
  raw_config_canary: "must-not-appear-in-decisions",
});
const AGENT_A = "agt_entitlement_contract_a";
const AGENT_B = "agt_entitlement_contract_b";
const TOKEN_CANARY = `ag${"tok"}_entitlement_contract_private`;

type EntitlementOverrides = Readonly<{
  edition?: "free_local" | "pro_workspace" | "team_governance" | "enterprise_byoc";
  status?: "active" | "inactive" | "suspended" | "expired";
  capabilities?: Record<string, unknown>;
  maxAgents?: number;
  maxActiveEnrollments?: number;
  maxActiveSessionsPerAgent?: number;
  maxConcurrentRuns?: number;
  maxMonthlyRuns?: number;
  maxMonthlyCostUsd?: number;
  effectiveAt?: Date;
  expiresAt?: Date | null;
}>;

function scopedDsn(baseDsn: string, schema: string) {
  const parsed = new URL(baseDsn);
  parsed.searchParams.set("options", `-csearch_path=${schema}`);
  return parsed.toString();
}

function quotedSchema(value: string) {
  assert.match(value, /^[a-z][a-z0-9_]+$/);
  return `"${value}"`;
}

function hoursFromNow(hours: number) {
  return new Date(Date.now() + hours * 60 * 60 * 1000);
}

async function upsertEntitlement(
  client: Client,
  workspaceId: string,
  overrides: EntitlementOverrides = {},
) {
  const effectiveAt = overrides.effectiveAt || hoursFromNow(-1);
  const expiresAt = overrides.expiresAt === undefined
    ? hoursFromNow(24)
    : overrides.expiresAt;
  await client.query(
    `INSERT INTO workspace_entitlements(
      workspace_id,edition,status,capabilities_json,max_agents,
      max_active_enrollments,max_active_sessions_per_agent,max_monthly_runs,
      max_monthly_cost_usd,max_concurrent_runs,effective_at,expires_at,
      created_at,updated_at,
      updated_by_user_id
    ) VALUES(
      $1,$2,$3,$4::jsonb,$5,$6,$7,$8,$9,$10,$11,$12,clock_timestamp(),
      clock_timestamp(),NULL
    )
    ON CONFLICT(workspace_id) DO UPDATE SET
      edition=EXCLUDED.edition,
      status=EXCLUDED.status,
      capabilities_json=EXCLUDED.capabilities_json,
      max_agents=EXCLUDED.max_agents,
      max_active_enrollments=EXCLUDED.max_active_enrollments,
      max_active_sessions_per_agent=EXCLUDED.max_active_sessions_per_agent,
      max_monthly_runs=EXCLUDED.max_monthly_runs,
      max_monthly_cost_usd=EXCLUDED.max_monthly_cost_usd,
      max_concurrent_runs=EXCLUDED.max_concurrent_runs,
      effective_at=EXCLUDED.effective_at,
      expires_at=EXCLUDED.expires_at,
      updated_at=clock_timestamp(),
      updated_by_user_id=NULL`,
    [
      workspaceId,
      overrides.edition || "team_governance",
      overrides.status || "active",
      JSON.stringify(overrides.capabilities || CAPABILITIES),
      overrides.maxAgents ?? 10,
      overrides.maxActiveEnrollments ?? 10,
      overrides.maxActiveSessionsPerAgent ?? 10,
      overrides.maxMonthlyRuns ?? 100,
      overrides.maxMonthlyCostUsd ?? 100,
      overrides.maxConcurrentRuns ?? 10,
      effectiveAt.toISOString(),
      expiresAt?.toISOString() || null,
    ],
  );
}

async function evaluate(
  connectionString: string,
  input: WorkspaceEntitlementEvaluation,
) {
  const client = new Client({ connectionString });
  await client.connect();
  try {
    await client.query("BEGIN");
    const result = await evaluateWorkspaceEntitlement(
      client as unknown as PoolClient,
      input,
    );
    await client.query("ROLLBACK");
    return result;
  } finally {
    await client.end();
  }
}

function assertDenied(
  decision: WorkspaceEntitlementDecision,
  reason: WorkspaceEntitlementDecision["reason_code"],
) {
  assert.equal(decision.contract, WORKSPACE_ENTITLEMENT_DECISION_CONTRACT);
  assert.equal(decision.allow, false);
  assert.equal(decision.decision, "deny");
  assert.equal(decision.reason_code, reason);
  assert.equal(decision.lock.acquired, true);
  assert.equal(decision.lock.transaction_scoped, true);
  assert.equal(decision.credentials_omitted, true);
  assert.equal(decision.raw_config_omitted, true);
}

async function seedAuthorityRows(client: Client) {
  const now = new Date().toISOString();
  await client.query(
    `INSERT INTO agents(
      agent_id,name,role,description,runtime_type,model_provider,model_name,
      status,permission_level,allowed_tools,budget_limit_usd,owner_user_id,
      created_at,updated_at
    ) VALUES
      ($1,'Entitlement Agent A','Worker',NULL,'hermes',NULL,NULL,'idle',
       'standard','[]',0,NULL,$3,$3),
      ($2,'Entitlement Agent B','Worker',NULL,'openclaw',NULL,NULL,'idle',
       'standard','[]',0,NULL,$3,$3)`,
    [AGENT_A, AGENT_B, now],
  );
}

async function seedActiveToken(
  client: Client,
  workspaceId: string,
  agentId: string,
  suffix: string,
) {
  const now = new Date().toISOString();
  const tokenId = `agttok_entitlement_${suffix}`;
  await client.query(
    `INSERT INTO agent_gateway_tokens(
      token_id,token_hash,workspace_id,agent_id,scopes_json,status,label,
      heartbeat_timeout_sec,created_at,expires_at,revoked_at,last_used_at,
      last_heartbeat_at
    ) VALUES($1,$2,$3,$4,'["tasks:read"]','active','contract',300,$5,$6,
      NULL,NULL,NULL)`,
    [
      tokenId,
      `${suffix}${"0".repeat(64)}`.slice(0, 64),
      workspaceId,
      agentId,
      now,
      hoursFromNow(12).toISOString(),
    ],
  );
  return tokenId;
}

async function seedActiveSession(
  client: Client,
  workspaceId: string,
  agentId: string,
  tokenId: string,
  suffix: string,
) {
  const now = new Date().toISOString();
  await client.query(
    `INSERT INTO agent_gateway_sessions(
      session_id,session_hash,parent_token_id,workspace_id,agent_id,
      scopes_json,status,created_at,expires_at,revoked_at,last_used_at
    ) VALUES($1,$2,$3,$4,$5,'["tasks:read"]','active',$6,$7,NULL,NULL)`,
    [
      `ags_entitlement_${suffix}`,
      `${suffix}${"1".repeat(64)}`.slice(0, 64),
      tokenId,
      workspaceId,
      agentId,
      now,
      hoursFromNow(4).toISOString(),
    ],
  );
}

async function seedMonthlyRun(
  client: Client,
  workspaceId: string,
  suffix: string,
  costUsd: number,
) {
  const now = new Date().toISOString();
  const taskId = `tsk_entitlement_${suffix}`;
  const runId = `run_entitlement_${suffix}`;
  await client.query("BEGIN");
  try {
    await client.query(
      `INSERT INTO tasks(
      task_id,workspace_id,title,description,requester_id,owner_agent_id,
      collaborator_agent_ids,status,priority,due_date,acceptance_criteria,
      risk_level,budget_limit_usd,created_at,updated_at
    ) VALUES($1,$2,'Entitlement quota task',NULL,NULL,$3,'[]','running',
      'medium',NULL,NULL,'low',0,$4,$4)`,
      [taskId, workspaceId, AGENT_A, now],
    );
    await client.query(
      `SELECT reservation_id
      FROM agentops_reserve_run_cost_v10(
        $1,$2,$3::numeric,$4,$5,interval '1 hour'
      )`,
      [
        workspaceId,
        runId,
        costUsd,
        randomBytes(32).toString("hex"),
        randomBytes(32).toString("hex"),
      ],
    );
    await client.query(
      `INSERT INTO runs(
      run_id,workspace_id,task_id,agent_id,runtime_type,status,started_at,
      ended_at,duration_ms,input_summary,output_summary,model_provider,
      model_name,input_tokens,output_tokens,reasoning_tokens,cost_usd,
      error_type,error_message,trace_id,parent_run_id,delegation_id,
      approval_required,agent_plan_id,plan_hash,created_at
    ) VALUES($1,$2,$3,$4,'hermes','running',$5,NULL,NULL,NULL,NULL,NULL,NULL,
      0,0,0,0,NULL,NULL,NULL,NULL,NULL,0,NULL,NULL,$5)`,
      [runId, workspaceId, taskId, AGENT_A, now],
    );
    await client.query(
      `SELECT reservation_id
      FROM agentops_heartbeat_run_cost_v10(
        $1,$2,$3::numeric,interval '1 hour'
      )`,
      [workspaceId, runId, costUsd],
    );
    await client.query("COMMIT");
  } catch (error) {
    await client.query("ROLLBACK");
    throw error;
  }
}

async function assertSchemaShape(client: Client) {
  const columns = await client.query<{
    column_name: string;
    data_type: string;
    is_nullable: "YES" | "NO";
  }>(
    `SELECT column_name,data_type,is_nullable
    FROM information_schema.columns
    WHERE table_schema=current_schema()
      AND table_name='workspace_entitlements'
    ORDER BY ordinal_position`,
  );
  assert.deepEqual(
    columns.rows.map((row) => row.column_name),
    [
      "workspace_id",
      "edition",
      "status",
      "capabilities_json",
      "max_agents",
      "max_active_enrollments",
      "max_active_sessions_per_agent",
      "max_monthly_runs",
      "max_monthly_cost_usd",
      "effective_at",
      "expires_at",
      "created_at",
      "updated_at",
      "updated_by_user_id",
      "max_concurrent_runs",
    ],
  );
  assert.equal(
    columns.rows.find((row) => row.column_name === "capabilities_json")?.data_type,
    "jsonb",
  );
  assert.equal(
    columns.rows.find((row) => row.column_name === "expires_at")?.is_nullable,
    "YES",
  );
  await assert.rejects(
    client.query(
      `INSERT INTO workspace_entitlements(
        workspace_id,edition,status,capabilities_json,max_agents,
        max_active_enrollments,max_active_sessions_per_agent,max_monthly_runs,
        max_monthly_cost_usd,effective_at,expires_at
      ) VALUES(
        'ws_bad_capabilities','team_governance','active','[]'::jsonb,
        1,1,1,1,1,clock_timestamp(),NULL
      )`,
    ),
    (error: unknown) => (error as { code?: string }).code === "23514",
  );
}

async function assertConcurrency(
  connectionString: string,
  fixture: Client,
) {
  const workspaceId = "ws_entitlement_concurrency";
  await upsertEntitlement(fixture, workspaceId, {
    maxActiveSessionsPerAgent: 1,
  });
  const tokenId = await seedActiveToken(
    fixture,
    workspaceId,
    AGENT_A,
    "concurrency_parent",
  );
  const first = new Client({ connectionString });
  const second = new Client({ connectionString });
  await Promise.all([first.connect(), second.connect()]);
  try {
    await Promise.all([first.query("BEGIN"), second.query("BEGIN")]);
    const firstDecision = await evaluateWorkspaceEntitlement(
      first as unknown as PoolClient,
      { workspaceId, operation: "session_issue", agentId: AGENT_A },
    );
    assert.equal(firstDecision.allow, true);
    await seedActiveSession(
      first,
      workspaceId,
      AGENT_A,
      tokenId,
      "concurrency_first",
    );

    let secondResolved = false;
    const secondDecisionPromise = evaluateWorkspaceEntitlement(
      second as unknown as PoolClient,
      { workspaceId, operation: "session_issue", agentId: AGENT_A },
    ).then((value) => {
      secondResolved = true;
      return value;
    });
    await new Promise((resolve) => setTimeout(resolve, 150));
    assert.equal(secondResolved, false);
    await first.query("COMMIT");
    const secondDecision = await secondDecisionPromise;
    assertDenied(secondDecision, "active_session_quota_exceeded");
    await second.query("ROLLBACK");
  } finally {
    await Promise.allSettled([
      first.query("ROLLBACK"),
      second.query("ROLLBACK"),
    ]);
    await Promise.all([first.end(), second.end()]);
  }
}

async function assertStaticBoundary() {
  const [helper, migration] = await Promise.all([
    readFile(
      new URL(
        "../src/server/controlPlane/workspaceEntitlements.ts",
        import.meta.url,
      ),
      "utf8",
    ),
    readFile(
      new URL(
        "../../../migrations/postgres/20260724_workspace_entitlements_v9.sql",
        import.meta.url,
      ),
      "utf8",
    ),
  ]);
  assert.match(helper, /pg_advisory_xact_lock/);
  assert.match(helper, /agent_gateway_tokens/);
  assert.match(helper, /agent_gateway_sessions/);
  assert.match(helper, /month_utc/);
  assert.doesNotMatch(
    `${helper}\n${migration}`,
    /child_process|\bfetch\s*\(|node:(?:http|https|net|tls)|sqlite|\.py\b/i,
  );
}

async function run() {
  const baseDsn = String(process.env.AGENTOPS_POSTGRES_DSN || "").trim();
  assert.ok(baseDsn, "AGENTOPS_POSTGRES_DSN is required");
  const schema = `workspace_entitlements_${randomUUID().replaceAll("-", "")}`;
  const admin = new Client({ connectionString: baseDsn });
  let schemaCreated = false;
  const originalFetch = globalThis.fetch;
  let externalNetworkCalls = 0;

  globalThis.fetch = async () => {
    externalNetworkCalls += 1;
    throw new Error("External network access is forbidden in this contract.");
  };
  try {
    await admin.connect();
    const version = await admin.query<{ server_version: string }>(
      "SHOW server_version",
    );
    assert.match(version.rows[0]?.server_version || "", /^16\./);
    await admin.query(`CREATE SCHEMA ${quotedSchema(schema)}`);
    schemaCreated = true;
    const connectionString = scopedDsn(baseDsn, schema);
    const migration = await runPostgresSchemaCommand(
      "migrate",
      { connectionString },
    );
    assert.equal(migration.schema_contract, SCHEMA_CONTRACT);
    assert.equal(migration.applied_count, POSTGRES_MIGRATION_MANIFEST.length);
    await runPostgresSchemaCommand("check", { connectionString });

    const fixture = new Client({ connectionString });
    await fixture.connect();
    try {
      await seedAuthorityRows(fixture);
      await assertSchemaShape(fixture);

      assertDenied(
        await evaluate(connectionString, {
          workspaceId: "ws_entitlement_missing",
          operation: "run_start",
        }),
        "entitlement_missing",
      );

      await upsertEntitlement(fixture, "ws_entitlement_inactive", {
        status: "inactive",
      });
      assertDenied(
        await evaluate(connectionString, {
          workspaceId: "ws_entitlement_inactive",
          operation: "run_start",
        }),
        "entitlement_inactive",
      );

      await upsertEntitlement(fixture, "ws_entitlement_suspended", {
        status: "suspended",
      });
      assertDenied(
        await evaluate(connectionString, {
          workspaceId: "ws_entitlement_suspended",
          operation: "session_issue",
          agentId: AGENT_A,
        }),
        "entitlement_suspended",
      );

      await upsertEntitlement(fixture, "ws_entitlement_expired_status", {
        status: "expired",
      });
      assertDenied(
        await evaluate(connectionString, {
          workspaceId: "ws_entitlement_expired_status",
          operation: "run_start",
        }),
        "entitlement_expired",
      );

      await upsertEntitlement(fixture, "ws_entitlement_expired_time", {
        effectiveAt: hoursFromNow(-4),
        expiresAt: hoursFromNow(-2),
      });
      assertDenied(
        await evaluate(connectionString, {
          workspaceId: "ws_entitlement_expired_time",
          operation: "run_start",
        }),
        "entitlement_expired",
      );

      await upsertEntitlement(fixture, "ws_entitlement_future", {
        effectiveAt: hoursFromNow(2),
        expiresAt: hoursFromNow(4),
      });
      assertDenied(
        await evaluate(connectionString, {
          workspaceId: "ws_entitlement_future",
          operation: "run_start",
        }),
        "entitlement_not_effective",
      );

      await upsertEntitlement(fixture, "ws_entitlement_capability", {
        capabilities: {
          enrollment_issue: true,
          session_issue: true,
          run_start: false,
        },
      });
      assertDenied(
        await evaluate(connectionString, {
          workspaceId: "ws_entitlement_capability",
          operation: "run_start",
        }),
        "capability_disabled",
      );

      await upsertEntitlement(fixture, "ws_entitlement_agent_quota", {
        maxAgents: 1,
      });
      await seedActiveToken(
        fixture,
        "ws_entitlement_agent_quota",
        AGENT_A,
        "agent_quota",
      );
      assertDenied(
        await evaluate(connectionString, {
          workspaceId: "ws_entitlement_agent_quota",
          operation: "enrollment_issue",
          agentId: AGENT_B,
        }),
        "agent_quota_exceeded",
      );

      await upsertEntitlement(fixture, "ws_entitlement_enrollment_quota", {
        maxAgents: 2,
        maxActiveEnrollments: 1,
      });
      const enrollmentQuotaToken = await seedActiveToken(
        fixture,
        "ws_entitlement_enrollment_quota",
        AGENT_A,
        "enrollment_quota",
      );
      assertDenied(
        await evaluate(connectionString, {
          workspaceId: "ws_entitlement_enrollment_quota",
          operation: "enrollment_issue",
          agentId: AGENT_A,
        }),
        "active_enrollment_quota_exceeded",
      );
      const replacementAllowed = await evaluate(connectionString, {
        workspaceId: "ws_entitlement_enrollment_quota",
        operation: "enrollment_issue",
        agentId: AGENT_A,
        replacingEnrollmentId: enrollmentQuotaToken,
      });
      assert.equal(replacementAllowed.allow, true);
      assert.equal(replacementAllowed.usage?.active_agents?.projected, 1);
      assert.equal(replacementAllowed.usage?.active_enrollments?.projected, 1);
      assertDenied(
        await evaluate(connectionString, {
          workspaceId: "ws_entitlement_enrollment_quota",
          operation: "enrollment_issue",
          agentId: AGENT_B,
          replacingEnrollmentId: enrollmentQuotaToken,
        }),
        "replacement_enrollment_invalid",
      );

      await upsertEntitlement(fixture, "ws_entitlement_session_quota", {
        maxActiveSessionsPerAgent: 1,
      });
      const sessionToken = await seedActiveToken(
        fixture,
        "ws_entitlement_session_quota",
        AGENT_A,
        "session_quota_parent",
      );
      await seedActiveSession(
        fixture,
        "ws_entitlement_session_quota",
        AGENT_A,
        sessionToken,
        "session_quota",
      );
      assertDenied(
        await evaluate(connectionString, {
          workspaceId: "ws_entitlement_session_quota",
          operation: "session_issue",
          agentId: AGENT_A,
        }),
        "active_session_quota_exceeded",
      );

      await upsertEntitlement(fixture, "ws_entitlement_run_quota", {
        maxMonthlyRuns: 1,
      });
      await seedMonthlyRun(
        fixture,
        "ws_entitlement_run_quota",
        "run_quota",
        1,
      );
      assertDenied(
        await evaluate(connectionString, {
          workspaceId: "ws_entitlement_run_quota",
          operation: "run_start",
        }),
        "monthly_run_quota_exceeded",
      );

      await upsertEntitlement(fixture, "ws_entitlement_cost_quota", {
        maxMonthlyRuns: 10,
        maxMonthlyCostUsd: 5,
      });
      await seedMonthlyRun(
        fixture,
        "ws_entitlement_cost_quota",
        "cost_quota",
        5,
      );
      assertDenied(
        await evaluate(connectionString, {
          workspaceId: "ws_entitlement_cost_quota",
          operation: "run_start",
        }),
        "monthly_cost_quota_exceeded",
      );

      const allowedWorkspace = "ws_entitlement_allowed";
      await upsertEntitlement(fixture, allowedWorkspace);
      const allowed = await Promise.all([
        evaluate(connectionString, {
          workspaceId: allowedWorkspace,
          operation: "enrollment_issue",
          agentId: AGENT_A,
        }),
        evaluate(connectionString, {
          workspaceId: allowedWorkspace,
          operation: "session_issue",
          agentId: AGENT_A,
        }),
        evaluate(connectionString, {
          workspaceId: allowedWorkspace,
          operation: "run_start",
          agentId: AGENT_A,
          estimatedCostUsd: 1,
        }),
      ]);
      for (const item of allowed) {
        assert.equal(item.allow, true);
        assert.equal(item.reason_code, "allowed");
        assert.equal(item.entitlement.authority, "postgres");
        assert.equal(item.entitlement.capability_enabled, true);
      }

      await assertConcurrency(connectionString, fixture);
      const serialized = JSON.stringify([...allowed]);
      assert.equal(serialized.includes(baseDsn), false);
      assert.equal(serialized.includes("postgresql://"), false);
      assert.equal(serialized.includes(TOKEN_CANARY), false);
      assert.equal(serialized.includes(CAPABILITIES.raw_config_canary), false);
      assert.equal(externalNetworkCalls, 0);
      await assertStaticBoundary();
    } finally {
      await fixture.end();
    }

    console.log(JSON.stringify({
      contract: "agentops_workspace_entitlements_postgres_contract_v1",
      ok: true,
      postgres_major: 16,
      schema_contract: SCHEMA_CONTRACT,
      migration_count: POSTGRES_MIGRATION_MANIFEST.length,
      missing_fail_closed: true,
      status_fail_closed: true,
      time_window_fail_closed: true,
      capability_fail_closed: true,
      enrollment_quotas_enforced: true,
      enrollment_rotation_is_quota_neutral: true,
      session_quota_enforced: true,
      utc_monthly_run_and_cost_quotas_enforced: true,
      allowed_decisions_structured: true,
      workspace_lock_serialized: true,
      external_network_calls: externalNetworkCalls,
      python_used: false,
      sqlite_used: false,
      credentials_omitted: true,
      raw_config_omitted: true,
    }));
  } finally {
    globalThis.fetch = originalFetch;
    if (schemaCreated) {
      await admin.query(`DROP SCHEMA IF EXISTS ${quotedSchema(schema)} CASCADE`);
    }
    await admin.end().catch(() => undefined);
  }
}

run().catch(() => {
  console.log(JSON.stringify({
    contract: "agentops_workspace_entitlements_postgres_contract_v1",
    ok: false,
    error_code: "contract_failed",
    credentials_omitted: true,
    raw_config_omitted: true,
  }));
  process.exitCode = 1;
});
