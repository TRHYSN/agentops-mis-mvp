import assert from "node:assert/strict";
import { createHash, randomBytes } from "node:crypto";
import { readFile } from "node:fs/promises";

import { Client } from "pg";

import {
  POSTGRES_MIGRATION_MANIFEST,
  SCHEMA_CONTRACT,
} from "../src/server/controlPlane/schemaReadiness";

const baseDsn = String(process.env.AGENTOPS_POSTGRES_DSN || "").trim();
const migrationFilename = "20260731_cost_reservations_v10.sql";
const baseMigrationCount = POSTGRES_MIGRATION_MANIFEST.findIndex(
  (definition) => definition.filename === migrationFilename,
);
const migrationUrl = new URL(
  `../../../migrations/postgres/${migrationFilename}`,
  import.meta.url,
);
let failureStage = "bootstrap";

function hash(value: string) {
  return createHash("sha256").update(value).digest("hex");
}

function schemaName() {
  return `agentops_cost_v10_${randomBytes(6).toString("hex")}`;
}

function quotedSchema(value: string) {
  assert.match(value, /^[a-z][a-z0-9_]+$/);
  return `"${value}"`;
}

function scopedDsn(schema: string) {
  const parsed = new URL(baseDsn);
  parsed.searchParams.set(
    "options",
    `-csearch_path=${schema} -cstatement_timeout=8000 -clock_timeout=6000`,
  );
  return parsed.toString();
}

async function expectDatabaseError(
  action: Promise<unknown>,
  expectedMessage: string,
) {
  await assert.rejects(
    action,
    (error: unknown) => (
      (error as { message?: string }).message === expectedMessage
    ),
  );
}

async function applyBaseMigrations(client: Client) {
  for (const definition of POSTGRES_MIGRATION_MANIFEST) {
    if (definition.filename === migrationFilename) break;
    const url = new URL(
      `../../../migrations/postgres/${definition.filename}`,
      import.meta.url,
    );
    const sql = await readFile(url, "utf8");
    assert.equal(hash(sql), definition.checksum);
    await client.query("BEGIN");
    try {
      await client.query(sql);
      await client.query("COMMIT");
    } catch (error) {
      await client.query("ROLLBACK").catch(() => undefined);
      throw error;
    }
  }
}

async function applyCostMigration(client: Client, migration: string) {
  await client.query("BEGIN");
  try {
    await client.query(migration);
    await client.query("COMMIT");
  } catch (error) {
    await client.query("ROLLBACK").catch(() => undefined);
    throw error;
  }
}

async function entitlement(
  client: Client,
  workspaceId: string,
  input: Readonly<{
    maxConcurrentRuns?: number;
    maxMonthlyRuns?: number;
    maxMonthlyCostUsd?: string;
    status?: string;
    runStart?: boolean;
  }> = {},
) {
  await client.query(
    `INSERT INTO workspace_entitlements(
      workspace_id,edition,status,capabilities_json,max_agents,
      max_active_enrollments,max_active_sessions_per_agent,max_monthly_runs,
      max_monthly_cost_usd,effective_at,expires_at,max_concurrent_runs
    ) VALUES(
      $1,'team_governance',$2,jsonb_build_object('run_start',$3::boolean),
      20,20,5,$4,$5::numeric,clock_timestamp()-interval '1 hour',
      clock_timestamp()+interval '1 year',$6
    )`,
    [
      workspaceId,
      input.status || "active",
      input.runStart ?? true,
      input.maxMonthlyRuns ?? 20,
      input.maxMonthlyCostUsd || "100.000000",
      input.maxConcurrentRuns ?? 5,
    ],
  );
}

type ReservationRow = Readonly<{
  reservation_id: string;
  workspace_id: string;
  run_id: string;
  billing_class: "metered_execution" | "historical_execution";
  billing_month_utc: string | Date;
  state: "reserved" | "settled" | "released" | "expired";
  estimated_cost_usd: string;
  observed_cost_usd: string;
  settled_cost_usd: string | null;
  idempotency_key_hash: string;
  request_hash: string;
  release_reason: string | null;
  expires_at: string | Date;
}>;

async function reserve(
  client: Client,
  workspaceId: string,
  runId: string,
  estimatedCostUsd: string,
  key = `reserve:${workspaceId}:${runId}`,
  request = `request:${workspaceId}:${runId}:${estimatedCostUsd}`,
  ttl = "1 hour",
) {
  const result = await client.query<ReservationRow>(
    `SELECT *
    FROM agentops_reserve_run_cost_v10(
      $1,$2,$3::numeric,$4,$5,$6::interval
    )`,
    [
      workspaceId,
      runId,
      estimatedCostUsd,
      hash(key),
      hash(request),
      ttl,
    ],
  );
  assert.equal(result.rowCount, 1);
  return result.rows[0];
}

async function heartbeat(
  client: Client,
  workspaceId: string,
  runId: string,
  observedCostUsd: string,
  ttl = "1 hour",
) {
  const result = await client.query<ReservationRow>(
    `SELECT *
    FROM agentops_heartbeat_run_cost_v10(
      $1,$2,$3::numeric,$4::interval
    )`,
    [workspaceId, runId, observedCostUsd, ttl],
  );
  assert.equal(result.rowCount, 1);
  return result.rows[0];
}

async function settle(
  client: Client,
  workspaceId: string,
  runId: string,
  actualCostUsd: string,
  key = `settle:${workspaceId}:${runId}`,
  request = `settle-request:${workspaceId}:${runId}:${actualCostUsd}`,
) {
  const result = await client.query<ReservationRow>(
    `SELECT *
    FROM agentops_settle_run_cost_v10(
      $1,$2,$3::numeric,$4,$5
    )`,
    [
      workspaceId,
      runId,
      actualCostUsd,
      hash(key),
      hash(request),
    ],
  );
  assert.equal(result.rowCount, 1);
  return result.rows[0];
}

async function settleAndTerminate(
  client: Client,
  workspaceId: string,
  runId: string,
  actualCostUsd: string,
  status: "completed" | "failed" | "blocked" = "completed",
) {
  await client.query("BEGIN");
  try {
    const result = await settle(
      client,
      workspaceId,
      runId,
      actualCostUsd,
    );
    await client.query(
      `UPDATE runs
      SET status=$1,ended_at=$2
      WHERE workspace_id=$3 AND run_id=$4`,
      [status, new Date().toISOString(), workspaceId, runId],
    );
    await client.query("COMMIT");
    return result;
  } catch (error) {
    await client.query("ROLLBACK").catch(() => undefined);
    throw error;
  }
}

async function release(
  client: Client,
  workspaceId: string,
  runId: string,
  reason: string,
  key = `release:${workspaceId}:${runId}`,
  request = `release-request:${workspaceId}:${runId}:${reason}`,
) {
  const result = await client.query<ReservationRow>(
    `SELECT *
    FROM agentops_release_run_cost_v10($1,$2,$3,$4,$5)`,
    [workspaceId, runId, reason, hash(key), hash(request)],
  );
  assert.equal(result.rowCount, 1);
  return result.rows[0];
}

type RunSeed = Readonly<{
  status?: string;
  startedAt?: string;
  costUsd?: string;
  modelProvider?: string;
  modelName?: string;
  approvalRequired?: number;
  inputTokens?: number;
  outputTokens?: number;
  reasoningTokens?: number;
}>;

async function seedAgentTask(
  client: Client,
  workspaceId: string,
  runId: string,
) {
  const agentId = `agt_${runId}`;
  const taskId = `tsk_${runId}`;
  const now = new Date().toISOString();
  await client.query(
    `INSERT INTO agents(
      agent_id,name,role,description,runtime_type,model_provider,model_name,
      status,permission_level,allowed_tools,budget_limit_usd,owner_user_id,
      created_at,updated_at
    ) VALUES(
      $1,'Cost contract agent','worker',NULL,'hermes','hermes','contract',
      'running','standard','[]',0,NULL,$2,$2
    )`,
    [agentId, now],
  );
  await client.query(
    `INSERT INTO tasks(
      task_id,workspace_id,title,description,requester_id,owner_agent_id,
      collaborator_agent_ids,status,priority,due_date,acceptance_criteria,
      risk_level,budget_limit_usd,created_at,updated_at
    ) VALUES(
      $1,$2,'Cost contract task',NULL,NULL,$3,'[]','running','medium',
      NULL,NULL,'low',0,$4,$4
    )`,
    [taskId, workspaceId, agentId, now],
  );
  return { agentId, taskId, now };
}

async function insertRun(
  client: Client,
  workspaceId: string,
  runId: string,
  graph: Awaited<ReturnType<typeof seedAgentTask>>,
  input: RunSeed = {},
) {
  const startedAt = input.startedAt || graph.now;
  await client.query(
    `INSERT INTO runs(
      run_id,workspace_id,task_id,agent_id,runtime_type,status,started_at,
      ended_at,duration_ms,input_summary,output_summary,model_provider,
      model_name,input_tokens,output_tokens,reasoning_tokens,cost_usd,
      error_type,error_message,trace_id,parent_run_id,delegation_id,
      approval_required,agent_plan_id,plan_hash,created_at
    ) VALUES(
      $1,$2,$3,$4,'hermes',$5,$6,NULL,NULL,NULL,NULL,$7,$8,$9,$10,$11,
      $12::numeric,NULL,NULL,NULL,NULL,NULL,$13,NULL,NULL,$14
    )`,
    [
      runId,
      workspaceId,
      graph.taskId,
      graph.agentId,
      input.status || "running",
      startedAt,
      input.modelProvider || "hermes",
      input.modelName || "contract",
      input.inputTokens ?? 0,
      input.outputTokens ?? 0,
      input.reasoningTokens ?? 0,
      input.costUsd || "0.000000",
      input.approvalRequired ?? 0,
      graph.now,
    ],
  );
}

async function seedRun(
  client: Client,
  workspaceId: string,
  runId: string,
  input: RunSeed = {},
) {
  const graph = await seedAgentTask(client, workspaceId, runId);
  await insertRun(client, workspaceId, runId, graph, input);
  return graph;
}

async function insertEnrollmentRequest(
  client: Client,
  workspaceId: string,
  runId: string,
  graph: Awaited<ReturnType<typeof seedAgentTask>>,
) {
  const approvalId = `apr_${runId}`;
  await client.query(
    `INSERT INTO approvals(
      approval_id,approval_kind,task_id,run_id,tool_call_id,
      requested_by_agent_id,approver_user_id,decision,reason,expires_at,
      created_at,decided_at
    ) VALUES(
      $1,'agent_enrollment',$2,$3,NULL,$4,NULL,'pending',
      'bounded enrollment contract',NULL,$5,NULL
    )`,
    [approvalId, graph.taskId, runId, graph.agentId, graph.now],
  );
  await client.query(
    `INSERT INTO agent_gateway_enrollment_requests(
      request_id,approval_id,task_id,run_id,workspace_id,agent_id,name,role,
      runtime_type,scopes_json,reason,status,token_id,created_at,updated_at,
      decided_at
    ) VALUES(
      $1,$2,$3,$4,$5,$6,'Enrollment contract agent','worker','hermes',
      '["tasks:read"]','bounded contract','pending',NULL,$7,$7,NULL
    )`,
    [
      `enr_${runId}`,
      approvalId,
      graph.taskId,
      runId,
      workspaceId,
      graph.agentId,
      graph.now,
    ],
  );
}

async function seedHistoricalFixtures(client: Client) {
  const month = (await client.query<{ month: string }>(
    `SELECT to_char(
      clock_timestamp() AT TIME ZONE 'UTC',
      'YYYY-MM'
    ) AS month`,
  )).rows[0].month;

  await seedRun(
    client,
    "ws_cost_historical",
    "run_cost_historical",
    {
      status: "completed",
      startedAt: `${month}-15T12:00:00.000Z`,
      costUsd: "100.123456",
    },
  );
  const projectedBefore = (await client.query<{ cost: string }>(
    `SELECT round(cost_usd::numeric,6)::numeric(18,6)::text AS cost
    FROM runs
    WHERE run_id='run_cost_historical'`,
  )).rows[0].cost;

  await seedRun(
    client,
    "ws_cost_historical_utc_boundary",
    "run_cost_historical_utc_boundary",
    {
      status: "completed",
      startedAt: "2026-02-01T00:30:00+14:00",
      costUsd: "0.500000",
    },
  );
  await seedRun(
    client,
    "ws_cost_historical_naive_utc",
    "run_cost_historical_naive_utc",
    {
      status: "completed",
      startedAt: "2026-02-01 00:30:00",
      costUsd: "0.250000",
    },
  );
  await seedRun(
    client,
    "ws_cost_historical_second_offset",
    "run_cost_historical_second_offset",
    {
      status: "completed",
      startedAt: "2026-03-01T00:00:00+00:00:30",
      costUsd: "0.125000",
    },
  );

  const managementGraph = await seedAgentTask(
    client,
    "ws_cost_management_history",
    "run_cost_management_history",
  );
  await insertRun(
    client,
    "ws_cost_management_history",
    "run_cost_management_history",
    managementGraph,
    {
      status: "waiting_approval",
      modelProvider: "agent-gateway",
      modelName: "enrollment-request",
      approvalRequired: 1,
    },
  );
  await client.query("BEGIN");
  try {
    await insertEnrollmentRequest(
      client,
      "ws_cost_management_history",
      "run_cost_management_history",
      managementGraph,
    );
    await client.query("COMMIT");
  } catch (error) {
    await client.query("ROLLBACK").catch(() => undefined);
    throw error;
  }

  return { projectedBefore };
}

async function assertSchema(client: Client, migration: string) {
  const columns = await client.query<{
    table_name: string;
    column_name: string;
    data_type: string;
    numeric_precision: number | null;
    numeric_scale: number | null;
    is_nullable: string;
  }>(
    `SELECT table_name,column_name,data_type,numeric_precision,numeric_scale,
      is_nullable
    FROM information_schema.columns
    WHERE table_schema=current_schema()
      AND (
        (
          table_name='run_cost_reservations'
          AND column_name IN (
            'estimated_cost_usd',
            'observed_cost_usd',
            'settled_cost_usd'
          )
        )
        OR (table_name='runs' AND column_name='cost_usd')
      )
    ORDER BY table_name,column_name`,
  );
  assert.equal(columns.rowCount, 4);
  for (const column of columns.rows) {
    assert.equal(column.data_type, "numeric");
    assert.equal(column.numeric_precision, 18);
    assert.equal(column.numeric_scale, 6);
  }
  assert.equal(
    columns.rows.find(
      (column) => column.table_name === "runs",
    )?.is_nullable,
    "NO",
  );

  const billingColumns = await client.query<{
    table_name: string;
    is_nullable: string;
  }>(
    `SELECT table_name,is_nullable
    FROM information_schema.columns
    WHERE table_schema=current_schema()
      AND column_name='billing_class'
      AND table_name IN ('runs','run_cost_reservations')
    ORDER BY table_name`,
  );
  assert.equal(billingColumns.rowCount, 2);
  assert.deepEqual(
    billingColumns.rows.map((row) => row.is_nullable),
    ["NO", "NO"],
  );

  const states = await client.query<{ definition: string }>(
    `SELECT pg_get_constraintdef(oid) AS definition
    FROM pg_constraint
    WHERE conrelid='run_cost_reservations'::regclass
      AND conname='run_cost_reservations_state_check'`,
  );
  assert.match(
    states.rows[0]?.definition || "",
    /reserved.*settled.*released.*expired/,
  );

  const expectedFunctions = [
    "agentops_classify_enrollment_management_run_v10",
    "agentops_enforce_billable_run_ledger_v10",
    "agentops_expire_run_cost_reservations_v10",
    "agentops_heartbeat_run_cost_v10",
    "agentops_release_run_cost_v10",
    "agentops_reserve_run_cost_v10",
    "agentops_run_billing_class_guard_v10",
    "agentops_run_cost_reservation_guard_v10",
    "agentops_settle_run_cost_v10",
  ];
  const privileges = await client.query<{
    proname: string;
    public_execute: boolean;
  }>(
    `SELECT
      procedure.proname,
      EXISTS(
        SELECT 1
        FROM aclexplode(
          COALESCE(
            procedure.proacl,
            acldefault('f',procedure.proowner)
          )
        ) privilege
        WHERE privilege.grantee=0
          AND privilege.privilege_type='EXECUTE'
      ) AS public_execute
    FROM pg_proc procedure
    JOIN pg_namespace namespace
      ON namespace.oid=procedure.pronamespace
    WHERE namespace.nspname=current_schema()
      AND procedure.proname=ANY($1::text[])
    ORDER BY procedure.proname`,
    [expectedFunctions],
  );
  assert.deepEqual(
    privileges.rows.map((row) => row.proname),
    [...expectedFunctions].sort(),
  );
  assert.equal(
    privileges.rows.some((row) => row.public_execute),
    false,
  );

  assert.match(migration, /SET LOCAL lock_timeout = '5s'/);
  assert.match(migration, /SET LOCAL statement_timeout = '30s'/);
  assert.match(migration, /SET lock_timeout='5s'/);
  assert.match(migration, /SET statement_timeout='30s'/);
  assert.match(migration, /pg_advisory_xact_lock/);
  assert.match(migration, /AT TIME ZONE 'UTC'/);
  assert.match(migration, /observed_cost_usd NUMERIC\(18,6\)/);
  assert.match(migration, /REVOKE EXECUTE ON FUNCTION/);
  assert.doesNotMatch(
    migration.match(
      /CREATE OR REPLACE FUNCTION agentops_reserve_run_cost_v10[\s\S]*?\n\$\$;/,
    )?.[0] || "",
    /started_at/i,
  );
}

async function assertHistoricalBackfill(
  client: Client,
  projectedBefore: string,
) {
  const historical = await client.query<{
    billing_class: string;
    cost_usd: string;
    state: string;
    estimated_cost_usd: string;
    observed_cost_usd: string;
    settled_cost_usd: string;
  }>(
    `SELECT
      run.billing_class,
      run.cost_usd::text,
      reservation.state,
      reservation.estimated_cost_usd::text,
      reservation.observed_cost_usd::text,
      reservation.settled_cost_usd::text
    FROM runs run
    JOIN run_cost_reservations reservation
      ON reservation.workspace_id=run.workspace_id
      AND reservation.run_id=run.run_id
    WHERE run.run_id='run_cost_historical'`,
  );
  assert.equal(historical.rowCount, 1);
  assert.equal(historical.rows[0].billing_class, "historical_execution");
  assert.equal(historical.rows[0].cost_usd, projectedBefore);
  assert.equal(historical.rows[0].state, "settled");
  assert.equal(historical.rows[0].observed_cost_usd, projectedBefore);
  assert.equal(historical.rows[0].settled_cost_usd, projectedBefore);
  assert.ok(
    Number(historical.rows[0].estimated_cost_usd)
      >= Number(projectedBefore),
  );

  const utcBoundary = await client.query<{ billing_month_utc: string }>(
    `SELECT to_char(
      reservation.billing_month_utc,
      'YYYY-MM-DD'
    ) AS billing_month_utc
    FROM run_cost_reservations reservation
    WHERE reservation.run_id='run_cost_historical_utc_boundary'`,
  );
  assert.equal(utcBoundary.rows[0]?.billing_month_utc, "2026-01-01");

  const naiveUtc = await client.query<{ billing_month_utc: string }>(
    `SELECT to_char(
      reservation.billing_month_utc,
      'YYYY-MM-DD'
    ) AS billing_month_utc
    FROM run_cost_reservations reservation
    WHERE reservation.run_id='run_cost_historical_naive_utc'`,
  );
  assert.equal(naiveUtc.rows[0]?.billing_month_utc, "2026-02-01");

  const secondOffset = await client.query<{ billing_month_utc: string }>(
    `SELECT to_char(
      reservation.billing_month_utc,
      'YYYY-MM-DD'
    ) AS billing_month_utc
    FROM run_cost_reservations reservation
    WHERE reservation.run_id='run_cost_historical_second_offset'`,
  );
  assert.equal(secondOffset.rows[0]?.billing_month_utc, "2026-02-01");

  const management = await client.query<{
    billing_class: string;
    ledger_count: string;
  }>(
    `SELECT
      run.billing_class,
      COUNT(reservation.reservation_id)::text AS ledger_count
    FROM runs run
    LEFT JOIN run_cost_reservations reservation
      ON reservation.workspace_id=run.workspace_id
      AND reservation.run_id=run.run_id
    WHERE run.run_id='run_cost_management_history'
    GROUP BY run.billing_class`,
  );
  assert.deepEqual(management.rows[0], {
    billing_class: "nonbillable_management",
    ledger_count: "0",
  });

  await entitlement(client, "ws_cost_historical", {
    maxConcurrentRuns: 1,
    maxMonthlyRuns: 1,
    maxMonthlyCostUsd: "1000.000000",
  });
  await expectDatabaseError(
    reserve(
      client,
      "ws_cost_historical",
      "run_cost_historical_escape",
      "1.000000",
    ),
    "monthly_run_limit_exceeded",
  );
}

async function assertBillingClassBoundary(client: Client) {
  const workspaceId = "ws_cost_billing_boundary";
  const runId = "run_cost_billing_bypass";
  const graph = await seedAgentTask(client, workspaceId, runId);
  await client.query("BEGIN");
  await insertRun(client, workspaceId, runId, graph);
  await expectDatabaseError(
    client.query("COMMIT"),
    "billable_run_cost_reservation_required",
  );
  await client.query("ROLLBACK").catch(() => undefined);

  const managementRunId = "run_cost_management_new";
  const managementGraph = await seedAgentTask(
    client,
    "ws_cost_management_new",
    managementRunId,
  );
  await client.query("BEGIN");
  try {
    await insertRun(
      client,
      "ws_cost_management_new",
      managementRunId,
      managementGraph,
      {
        status: "waiting_approval",
        modelProvider: "agent-gateway",
        modelName: "enrollment-request",
        approvalRequired: 1,
      },
    );
    await insertEnrollmentRequest(
      client,
      "ws_cost_management_new",
      managementRunId,
      managementGraph,
    );
    await client.query("COMMIT");
  } catch (error) {
    await client.query("ROLLBACK").catch(() => undefined);
    throw error;
  }
  const classified = await client.query<{
    billing_class: string;
    ledger_count: string;
  }>(
    `SELECT
      run.billing_class,
      COUNT(reservation.reservation_id)::text AS ledger_count
    FROM runs run
    LEFT JOIN run_cost_reservations reservation
      ON reservation.workspace_id=run.workspace_id
      AND reservation.run_id=run.run_id
    WHERE run.run_id=$1
    GROUP BY run.billing_class`,
    [managementRunId],
  );
  assert.deepEqual(classified.rows[0], {
    billing_class: "nonbillable_management",
    ledger_count: "0",
  });
}

async function assertFailClosed(client: Client) {
  await expectDatabaseError(
    reserve(
      client,
      "ws_cost_missing",
      "run_cost_missing",
      "1.000000",
    ),
    "cost_reservation_entitlement_missing",
  );

  await entitlement(client, "ws_cost_disabled", { runStart: false });
  await expectDatabaseError(
    reserve(
      client,
      "ws_cost_disabled",
      "run_cost_disabled",
      "1.000000",
    ),
    "cost_reservation_entitlement_denied",
  );

  await entitlement(client, "ws_cost_zero");
  await expectDatabaseError(
    reserve(client, "ws_cost_zero", "run_cost_zero", "0.000000"),
    "estimated_cost_must_be_positive_numeric_18_6",
  );
  await expectDatabaseError(
    reserve(client, "ws_cost_zero", "run_cost_negative", "-1.000000"),
    "estimated_cost_must_be_positive_numeric_18_6",
  );

  await assert.rejects(
    client.query(
      `INSERT INTO run_cost_reservations(
        reservation_id,workspace_id,run_id,billing_month_utc,state,
        estimated_cost_usd,idempotency_key_hash,request_hash,reserved_at,
        expires_at,updated_at
      ) VALUES(
        'rsv_direct_bad','ws_cost_zero','run_direct_bad',current_date,
        'settled',1,$1,$2,clock_timestamp(),
        clock_timestamp()+interval '1 hour',clock_timestamp()
      )`,
      [hash("direct-key"), hash("direct-request")],
    ),
  );
}

async function assertConcurrentSingleWinner(
  connectionString: string,
  fixture: Client,
) {
  const workspaceId = "ws_cost_concurrent";
  await entitlement(fixture, workspaceId, {
    maxConcurrentRuns: 1,
    maxMonthlyRuns: 10,
    maxMonthlyCostUsd: "10.000000",
  });
  const first = new Client({ connectionString });
  const second = new Client({ connectionString });
  await Promise.all([first.connect(), second.connect()]);
  try {
    const attempts = await Promise.allSettled([
      reserve(first, workspaceId, "run_cost_concurrent_a", "1.000000"),
      reserve(second, workspaceId, "run_cost_concurrent_b", "1.000000"),
    ]);
    const winners = attempts.filter((attempt) => attempt.status === "fulfilled");
    const losers = attempts.filter((attempt) => attempt.status === "rejected");
    assert.equal(winners.length, 1);
    assert.equal(losers.length, 1);
    assert.equal(
      (losers[0] as PromiseRejectedResult).reason.message,
      "run_concurrency_limit_exceeded",
    );
  } finally {
    await Promise.all([
      first.end().catch(() => undefined),
      second.end().catch(() => undefined),
    ]);
  }
}

async function assertConcurrentBudgetSingleWinner(
  connectionString: string,
  fixture: Client,
) {
  const workspaceId = "ws_cost_budget_race";
  await entitlement(fixture, workspaceId, {
    maxConcurrentRuns: 2,
    maxMonthlyRuns: 10,
    maxMonthlyCostUsd: "5.000000",
  });
  const first = new Client({ connectionString });
  const second = new Client({ connectionString });
  await Promise.all([first.connect(), second.connect()]);
  try {
    const attempts = await Promise.allSettled([
      reserve(first, workspaceId, "run_cost_budget_a", "4.000000"),
      reserve(second, workspaceId, "run_cost_budget_b", "4.000000"),
    ]);
    const winners = attempts.filter((attempt) => attempt.status === "fulfilled");
    const losers = attempts.filter((attempt) => attempt.status === "rejected");
    assert.equal(winners.length, 1);
    assert.equal(losers.length, 1);
    assert.equal(
      (losers[0] as PromiseRejectedResult).reason.message,
      "monthly_cost_reservation_limit_exceeded",
    );
  } finally {
    await Promise.all([
      first.end().catch(() => undefined),
      second.end().catch(() => undefined),
    ]);
  }
}

async function assertPreciseHeartbeatAndSettlement(client: Client) {
  const workspaceId = "ws_cost_precision";
  const runId = "run_cost_precision";
  await entitlement(client, workspaceId, {
    maxConcurrentRuns: 2,
    maxMonthlyRuns: 1,
    maxMonthlyCostUsd: "20.000000",
  });
  const reserved = await reserve(
    client,
    workspaceId,
    runId,
    "10.123456",
  );
  assert.equal(reserved.observed_cost_usd, "0.000000");
  const serverMonth = await client.query<{ billing_month: string | Date }>(
    `SELECT date_trunc(
      'month',clock_timestamp() AT TIME ZONE 'UTC'
    )::date AS billing_month`,
  );
  assert.equal(
    new Date(reserved.billing_month_utc).toISOString().slice(0, 10),
    new Date(serverMonth.rows[0].billing_month).toISOString().slice(0, 10),
  );
  await seedRun(client, workspaceId, runId);

  const first = await heartbeat(
    client,
    workspaceId,
    runId,
    "1.000001",
  );
  assert.equal(first.observed_cost_usd, "1.000001");
  const second = await heartbeat(
    client,
    workspaceId,
    runId,
    "1.000002",
  );
  assert.equal(second.observed_cost_usd, "1.000002");
  await expectDatabaseError(
    heartbeat(client, workspaceId, runId, "1.000001"),
    "observed_cost_decrease_forbidden",
  );
  await expectDatabaseError(
    heartbeat(client, workspaceId, runId, "10.123457"),
    "observed_cost_exceeds_reservation",
  );
  await expectDatabaseError(
    heartbeat(client, workspaceId, runId, "1.000002", "25 hours"),
    "cost_reservation_ttl_invalid",
  );
  await expectDatabaseError(
    settle(client, workspaceId, runId, "1.000001"),
    "cost_settlement_below_observed",
  );

  const settled = await settleAndTerminate(
    client,
    workspaceId,
    runId,
    "10.123456",
  );
  assert.equal(settled.state, "settled");
  assert.equal(settled.observed_cost_usd, "10.123456");
  assert.equal(settled.settled_cost_usd, "10.123456");
  const projection = await client.query<{ cost_usd: string }>(
    "SELECT cost_usd::text FROM runs WHERE run_id=$1",
    [runId],
  );
  assert.equal(projection.rows[0].cost_usd, "10.123456");

  const settlementReplay = await settle(
    client,
    workspaceId,
    runId,
    "10.123456",
  );
  assert.equal(settlementReplay.reservation_id, reserved.reservation_id);
  await expectDatabaseError(
    settle(
      client,
      workspaceId,
      runId,
      "10.000000",
      `settle:${workspaceId}:${runId}`,
      `settle-request:${workspaceId}:${runId}:10.000000`,
    ),
    "cost_settlement_idempotency_conflict",
  );

  const reserveReplay = await reserve(
    client,
    workspaceId,
    runId,
    "10.123456",
  );
  assert.equal(reserveReplay.state, "settled");
  assert.equal(reserveReplay.reservation_id, reserved.reservation_id);
}

async function assertReleaseAndReplay(client: Client) {
  const workspaceId = "ws_cost_release";
  await entitlement(client, workspaceId, {
    maxConcurrentRuns: 1,
    maxMonthlyRuns: 1,
    maxMonthlyCostUsd: "5.000000",
  });
  const first = await reserve(
    client,
    workspaceId,
    "run_cost_release_a",
    "4.000000",
  );
  await expectDatabaseError(
    release(
      client,
      workspaceId,
      first.run_id,
      "free form reason",
    ),
    "cost_release_binding_invalid",
  );
  const released = await release(
    client,
    workspaceId,
    first.run_id,
    "run_insert_failed",
  );
  assert.equal(released.state, "released");
  const replay = await release(
    client,
    workspaceId,
    first.run_id,
    "run_insert_failed",
  );
  assert.equal(replay.reservation_id, first.reservation_id);
  await expectDatabaseError(
    reserve(
      client,
      workspaceId,
      first.run_id,
      "4.000000",
    ),
    "cost_reservation_replay_released",
  );
  await expectDatabaseError(
    release(
      client,
      workspaceId,
      first.run_id,
      "run_cancelled_before_execution",
      `release:${workspaceId}:${first.run_id}`,
      `release-request:${workspaceId}:${first.run_id}:changed`,
    ),
    "cost_release_idempotency_conflict",
  );

  const replacement = await reserve(
    client,
    workspaceId,
    "run_cost_release_b",
    "5.000000",
  );
  assert.equal(replacement.state, "reserved");

  const activeWorkspace = "ws_cost_release_active";
  const activeRunId = "run_cost_release_active";
  await entitlement(client, activeWorkspace);
  await reserve(client, activeWorkspace, activeRunId, "2.000000");
  await seedRun(client, activeWorkspace, activeRunId);
  await expectDatabaseError(
    release(
      client,
      activeWorkspace,
      activeRunId,
      "run_insert_failed",
    ),
    "cost_release_run_state_invalid",
  );
  await expectDatabaseError(
    release(
      client,
      activeWorkspace,
      activeRunId,
      "run_cancelled_before_execution",
    ),
    "cost_release_run_state_invalid",
  );

  const rejectedWorkspace = "ws_cost_release_rejected";
  const rejectedRunId = "run_cost_release_rejected";
  await entitlement(client, rejectedWorkspace);
  await reserve(client, rejectedWorkspace, rejectedRunId, "2.000000");
  await seedRun(client, rejectedWorkspace, rejectedRunId);
  await client.query("BEGIN");
  try {
    await client.query(
      `UPDATE runs
      SET status='blocked',ended_at=clock_timestamp()::text
      WHERE workspace_id=$1 AND run_id=$2`,
      [rejectedWorkspace, rejectedRunId],
    );
    await release(
      client,
      rejectedWorkspace,
      rejectedRunId,
      "run_rejected_before_execution",
    );
    await client.query("COMMIT");
  } catch (error) {
    await client.query("ROLLBACK").catch(() => undefined);
    throw error;
  }
  await client.query("BEGIN");
  await client.query(
    `UPDATE runs
    SET status='completed'
    WHERE workspace_id=$1 AND run_id=$2`,
    [rejectedWorkspace, rejectedRunId],
  );
  await expectDatabaseError(
    client.query("COMMIT"),
    "terminal_run_released_state_invalid",
  );
  await client.query("ROLLBACK").catch(() => undefined);
}

async function assertExpiryMonthlyAndRecovery(client: Client) {
  const workspaceId = "ws_cost_expiry";
  const runId = "run_cost_expiry_a";
  await entitlement(client, workspaceId, {
    maxConcurrentRuns: 1,
    maxMonthlyRuns: 1,
    maxMonthlyCostUsd: "5.000000",
  });
  const first = await reserve(
    client,
    workspaceId,
    runId,
    "2.000000",
    undefined,
    undefined,
    "1 second",
  );
  await seedRun(client, workspaceId, runId);
  await heartbeat(client, workspaceId, runId, "1.000001", "1 second");
  await client.query("SELECT pg_sleep(1.05)");
  const expired = await client.query<{ count: string }>(
    "SELECT agentops_expire_run_cost_reservations_v10($1)::text AS count",
    [workspaceId],
  );
  assert.equal(expired.rows[0].count, "1");
  const row = await client.query<ReservationRow>(
    "SELECT * FROM run_cost_reservations WHERE reservation_id=$1",
    [first.reservation_id],
  );
  assert.equal(row.rows[0].state, "expired");
  assert.equal(row.rows[0].observed_cost_usd, "1.000001");

  await expectDatabaseError(
    reserve(
      client,
      workspaceId,
      "run_cost_expiry_b",
      "1.000000",
    ),
    "monthly_run_limit_exceeded",
  );
  await expectDatabaseError(
    reserve(client, workspaceId, runId, "2.000000"),
    "cost_reservation_replay_expired",
  );

  const renewed = await heartbeat(
    client,
    workspaceId,
    runId,
    "1.000002",
    "2 seconds",
  );
  assert.equal(renewed.state, "reserved");
  assert.equal(renewed.observed_cost_usd, "1.000002");
  assert.ok(
    new Date(renewed.expires_at).getTime()
      <= Date.now() + (24 * 60 * 60 * 1000) + 2_000,
  );

  await client.query("SELECT pg_sleep(2.05)");
  await client.query(
    "SELECT agentops_expire_run_cost_reservations_v10($1)",
    [workspaceId],
  );
  const settled = await settleAndTerminate(
    client,
    workspaceId,
    runId,
    "1.500000",
  );
  assert.equal(settled.state, "settled");
  assert.equal(settled.settled_cost_usd, "1.500000");
}

async function assertTerminalSettlementGate(client: Client) {
  const workspaceId = "ws_cost_terminal";
  const runId = "run_cost_terminal";
  await entitlement(client, workspaceId);
  await reserve(client, workspaceId, runId, "2.000000");
  await seedRun(client, workspaceId, runId);
  await heartbeat(client, workspaceId, runId, "1.234567");
  await expectDatabaseError(
    settle(client, workspaceId, runId, "1.234567"),
    "active_run_cost_reservation_state_invalid",
  );
  const runningReservation = await client.query<{ state: string }>(
    `SELECT state
    FROM run_cost_reservations
    WHERE workspace_id=$1 AND run_id=$2`,
    [workspaceId, runId],
  );
  assert.equal(runningReservation.rows[0]?.state, "reserved");

  const waitingWorkspaceId = "ws_cost_terminal_waiting";
  const waitingRunId = "run_cost_terminal_waiting";
  await entitlement(client, waitingWorkspaceId);
  await reserve(client, waitingWorkspaceId, waitingRunId, "2.000000");
  await seedRun(client, waitingWorkspaceId, waitingRunId, {
    status: "waiting_approval",
    approvalRequired: 1,
  });
  await expectDatabaseError(
    settle(client, waitingWorkspaceId, waitingRunId, "0.000000"),
    "active_run_cost_reservation_state_invalid",
  );

  await client.query("BEGIN");
  await client.query(
    `UPDATE runs
    SET status='blocked',ended_at=$1
    WHERE run_id=$2`,
    [new Date().toISOString(), runId],
  );
  await expectDatabaseError(
    client.query("COMMIT"),
    "terminal_run_cost_settlement_required",
  );
  await client.query("ROLLBACK").catch(() => undefined);

  await settleAndTerminate(
    client,
    workspaceId,
    runId,
    "1.234567",
    "blocked",
  );
  const row = await client.query<ReservationRow>(
    `SELECT *
    FROM run_cost_reservations
    WHERE workspace_id=$1 AND run_id=$2`,
    [workspaceId, runId],
  );
  assert.equal(row.rows[0].state, "settled");
  assert.equal(row.rows[0].settled_cost_usd, "1.234567");
}

async function run() {
  assert.ok(baseDsn, "AGENTOPS_POSTGRES_DSN is required");
  const admin = new Client({
    connectionString: baseDsn,
    application_name: "agentops-cost-reservation-contract-admin",
  });
  await admin.connect();
  const schema = schemaName();
  let schemaCreated = false;
  try {
    await admin.query(`CREATE SCHEMA ${quotedSchema(schema)}`);
    schemaCreated = true;
    const connectionString = scopedDsn(schema);
    const fixture = new Client({
      connectionString,
      application_name: "agentops-cost-reservation-contract",
    });
    await fixture.connect();
    try {
      failureStage = "base_migrations";
      await applyBaseMigrations(fixture);
      failureStage = "historical_fixtures";
      const historical = await seedHistoricalFixtures(fixture);
      const migration = await readFile(migrationUrl, "utf8");
      failureStage = "active_run_upgrade_preflight";
      const activeUpgrade = await seedRun(
        fixture,
        "ws_cost_active_upgrade",
        "run_cost_active_upgrade",
      );
      await expectDatabaseError(
        applyCostMigration(fixture, migration),
        "cost_authority_active_runs_must_be_drained",
      );
      const preflightRollback = await fixture.query<{ present: boolean }>(
        `SELECT EXISTS(
          SELECT 1
          FROM information_schema.columns
          WHERE table_schema=current_schema()
            AND table_name='runs'
            AND column_name='billing_class'
        ) AS present`,
      );
      assert.equal(preflightRollback.rows[0]?.present, false);
      await fixture.query(
        "DELETE FROM runs WHERE run_id='run_cost_active_upgrade'",
      );
      await fixture.query(
        "DELETE FROM tasks WHERE task_id=$1",
        [activeUpgrade.taskId],
      );
      await fixture.query(
        "DELETE FROM agents WHERE agent_id=$1",
        [activeUpgrade.agentId],
      );
      failureStage = "cost_migration_first_apply";
      await fixture.query("SET TIME ZONE 'Pacific/Kiritimati'");
      await applyCostMigration(fixture, migration);
      await fixture.query("SET TIME ZONE 'UTC'");
      failureStage = "cost_migration_reapply";
      await applyCostMigration(fixture, migration);

      failureStage = "schema_contract";
      await assertSchema(fixture, migration);
      failureStage = "historical_backfill";
      await assertHistoricalBackfill(
        fixture,
        historical.projectedBefore,
      );
      failureStage = "billing_class_boundary";
      await assertBillingClassBoundary(fixture);
      failureStage = "fail_closed";
      await assertFailClosed(fixture);
      failureStage = "concurrency";
      await assertConcurrentSingleWinner(connectionString, fixture);
      failureStage = "budget_concurrency";
      await assertConcurrentBudgetSingleWinner(connectionString, fixture);
      failureStage = "precision";
      await assertPreciseHeartbeatAndSettlement(fixture);
      failureStage = "release";
      await assertReleaseAndReplay(fixture);
      failureStage = "expiry";
      await assertExpiryMonthlyAndRecovery(fixture);
      failureStage = "terminal_gate";
      await assertTerminalSettlementGate(fixture);

      const output = JSON.stringify({
        contract: "agentops_cost_reservation_postgres_contract_v10",
        ok: true,
        target_schema_contract: SCHEMA_CONTRACT,
        base_migration_count: baseMigrationCount,
        migration_reapply_idempotent: true,
        historical_execution_backfilled: true,
        historical_utc_month_boundary: true,
        historical_second_offset_utc_boundary: true,
        historical_naive_timestamp_utc_deterministic: true,
        active_run_upgrade_fail_closed: true,
        nonbillable_management_strict: true,
        billable_run_bypass_closed: true,
        authoritative_cost_numeric_18_6: true,
        observed_cost_monotonic: true,
        heartbeat_lease_max_24h: true,
        expired_monthly_usage_retained: true,
        expired_renew_and_settle: true,
        reserve_replay_state_matrix: true,
        release_reason_machine_bounded: true,
        released_terminal_state_bound: true,
        terminal_settlement_gate: true,
        active_run_standalone_settlement_rejected: true,
        settlement_and_terminal_update_atomic: true,
        cost_function_public_execute_revoked: true,
        concurrent_single_winner: true,
        concurrent_budget_single_winner: true,
        server_utc_billing_month: true,
        client_started_at_ignored: true,
        credentials_omitted: true,
        row_data_omitted: true,
      });
      assert.equal(output.includes(baseDsn), false);
      assert.equal(output.includes(connectionString), false);
      console.log(output);
    } finally {
      await fixture.end().catch(() => undefined);
    }
  } finally {
    if (schemaCreated) {
      await admin.query(`DROP SCHEMA IF EXISTS ${quotedSchema(schema)} CASCADE`);
    }
    await admin.end().catch(() => undefined);
  }
}

run().catch((error: unknown) => {
  const message = String((error as { message?: string }).message || "unknown")
    .replace(/postgres(?:ql)?:\/\/\S+/gi, "[redacted-dsn]")
    .slice(0, 160);
  console.log(JSON.stringify({
    contract: "agentops_cost_reservation_postgres_contract_v10",
    ok: false,
    error_code: "contract_failed",
    failure_stage: failureStage,
    diagnostic: message,
    credentials_omitted: true,
    row_data_omitted: true,
  }));
  process.exitCode = 1;
});
