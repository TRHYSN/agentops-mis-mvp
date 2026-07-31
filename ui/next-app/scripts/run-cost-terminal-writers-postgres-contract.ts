import assert from "node:assert/strict";
import { createHash, randomBytes } from "node:crypto";
import { readFile } from "node:fs/promises";

import { Client, Pool, type PoolClient } from "pg";

import {
  settleExistingTerminalRunCost,
} from "../src/server/controlPlane/terminalRunCost";
import { ControlPlaneHttpError } from "../src/server/controlPlane/http";
import {
  POSTGRES_MIGRATION_MANIFEST,
  runPostgresSchemaCommand,
  SCHEMA_CONTRACT,
} from "../src/server/controlPlane/schemaReadiness";

const baseDsn = String(process.env.AGENTOPS_POSTGRES_DSN || "").trim();
const preparedActionsUrl = new URL(
  "../src/server/controlPlane/preparedActions.ts",
  import.meta.url,
);
const approvalDecisionsUrl = new URL(
  "../src/server/controlPlane/approvalDecisions.ts",
  import.meta.url,
);
const enrollmentApprovalsUrl = new URL(
  "../src/server/controlPlane/agentGatewayEnrollmentApprovals.ts",
  import.meta.url,
);

type TerminalStatus = "completed" | "failed" | "blocked";
let stage = "startup";

function hash(value: string) {
  return createHash("sha256").update(value, "utf8").digest("hex");
}

function schemaName() {
  return `agentops_terminal_cost_${randomBytes(6).toString("hex")}`;
}

function quotedSchema(value: string) {
  assert.match(value, /^[a-z][a-z0-9_]+$/);
  return `"${value}"`;
}

function scopedDsn(schema: string) {
  const parsed = new URL(baseDsn);
  parsed.searchParams.set(
    "options",
    `-csearch_path=${schema} -cstatement_timeout=10000 -clock_timeout=6000`,
  );
  return parsed.toString();
}

function functionSlice(source: string, start: string, end: string) {
  const startIndex = source.indexOf(start);
  const endIndex = source.indexOf(end, startIndex + start.length);
  assert.notEqual(startIndex, -1, `missing source marker: ${start}`);
  assert.notEqual(endIndex, -1, `missing source marker: ${end}`);
  return source.slice(startIndex, endIndex);
}

function assertSettlementBeforeTerminalUpdate(
  source: string,
  updatePattern: RegExp,
  owner: string,
) {
  const settlementIndex = source.indexOf("settleExistingTerminalRunCost");
  const updateMatch = updatePattern.exec(source);
  assert.notEqual(settlementIndex, -1, `${owner} omits terminal cost closure`);
  assert.ok(updateMatch, `${owner} terminal run update is missing`);
  assert.ok(
    settlementIndex < (updateMatch?.index ?? -1),
    `${owner} settles cost after the run is terminal`,
  );
  assert.doesNotMatch(source, /\breserveRunCost\b/);
}

async function assertProductionWriterBindings() {
  const [preparedActions, approvalDecisions, enrollmentApprovals] =
    await Promise.all([
      readFile(preparedActionsUrl, "utf8"),
      readFile(approvalDecisionsUrl, "utf8"),
      readFile(enrollmentApprovalsUrl, "utf8"),
    ]);
  assert.match(
    preparedActions,
    /from "\.\/terminalRunCost";/,
    "PreparedAction owner must use the shared terminal cost boundary",
  );
  assert.match(
    approvalDecisions,
    /from "\.\/terminalRunCost";/,
    "Approval owner must use the shared terminal cost boundary",
  );
  assert.match(
    enrollmentApprovals,
    /from "\.\/terminalRunCost";/,
    "Enrollment owner must use the shared terminal cost boundary",
  );
  assert.doesNotMatch(
    enrollmentApprovals,
    /from "\.\/preparedActions";/,
    "Enrollment must not depend on the PreparedAction domain",
  );
  assert.match(
    enrollmentApprovals,
    /'nonbillable_management'/,
    "Enrollment approval runs must declare their non-billable class",
  );

  assertSettlementBeforeTerminalUpdate(
    functionSlice(
      approvalDecisions,
      "async function decidePreparedAction(",
      "async function deliveryRequestBinding(",
    ),
    /UPDATE runs SET status='blocked'/,
    "PreparedAction Human rejection",
  );
  assertSettlementBeforeTerminalUpdate(
    functionSlice(
      preparedActions,
      "async function terminalizeExpiredLease(",
      "function identifierList(",
    ),
    /UPDATE runs SET status='blocked'/,
    "PreparedAction lease timeout",
  );
  assertSettlementBeforeTerminalUpdate(
    functionSlice(
      preparedActions,
      "export async function failPreparedActionExecution(",
      "export async function resumePreparedActionExecution(",
    ),
    /UPDATE runs SET status='blocked'/,
    "PreparedAction execution failure",
  );
  assertSettlementBeforeTerminalUpdate(
    functionSlice(
      preparedActions,
      "export async function resumePreparedActionExecution(",
      "\n}",
    ),
    /UPDATE runs SET approval_required=0,status='completed'/,
    "PreparedAction execution success",
  );
  assertSettlementBeforeTerminalUpdate(
    functionSlice(
      enrollmentApprovals,
      "export async function decideGatewayEnrollmentApproval(",
      "async function issuedReplay(",
    ),
    /UPDATE runs[\s\S]*?SET status=\$1/,
    "Enrollment approval management run",
  );
}

async function seedEntitlement(client: PoolClient, workspaceId: string) {
  await client.query(
    `INSERT INTO workspace_entitlements(
      workspace_id,edition,status,capabilities_json,max_agents,
      max_active_enrollments,max_active_sessions_per_agent,max_monthly_runs,
      max_monthly_cost_usd,effective_at,expires_at,max_concurrent_runs
    ) VALUES(
      $1,'team_governance','active',
      jsonb_build_object('run_start',true),
      20,20,5,20,100::numeric,
      clock_timestamp()-interval '1 hour',
      clock_timestamp()+interval '1 year',10
    )`,
    [workspaceId],
  );
}

async function seedRun(
  client: PoolClient,
  input: Readonly<{
    workspaceId: string;
    suffix: string;
    costUsd: string;
    billingClass: "metered_execution" | "historical_execution" | "nonbillable_management";
    reserveEstimateUsd?: string;
  }>,
) {
  const agentId = `agt_terminal_${input.suffix}`;
  const taskId = `tsk_terminal_${input.suffix}`;
  const runId = `run_terminal_${input.suffix}`;
  const now = new Date().toISOString();
  await client.query("BEGIN");
  try {
    await client.query(
      `INSERT INTO agents(
      agent_id,name,role,description,runtime_type,model_provider,model_name,
      status,permission_level,allowed_tools,budget_limit_usd,owner_user_id,
      created_at,updated_at
    ) VALUES(
      $1,'Terminal cost contract agent','worker',NULL,'hermes','hermes',
      'contract','running','standard','[]',0,NULL,$2,$2
    )`,
      [agentId, now],
    );
    await client.query(
      `INSERT INTO tasks(
      task_id,workspace_id,title,description,requester_id,owner_agent_id,
      collaborator_agent_ids,status,priority,due_date,acceptance_criteria,
      risk_level,budget_limit_usd,created_at,updated_at
    ) VALUES(
      $1,$2,'Terminal cost contract task',NULL,NULL,$3,'[]','running',
      'medium',NULL,NULL,'low',0,$4,$4
    )`,
      [taskId, input.workspaceId, agentId, now],
    );
    if (input.reserveEstimateUsd) {
      await reserve(
        client,
        input.workspaceId,
        runId,
        input.reserveEstimateUsd,
      );
    }
    await client.query(
      `INSERT INTO runs(
      run_id,workspace_id,task_id,agent_id,runtime_type,status,billing_class,
      started_at,ended_at,duration_ms,input_summary,output_summary,
      model_provider,model_name,input_tokens,output_tokens,reasoning_tokens,
      cost_usd,error_type,error_message,trace_id,parent_run_id,delegation_id,
      approval_required,agent_plan_id,plan_hash,created_at
    ) VALUES(
      $1,$2,$3,$4,'hermes','running',$5,$6,NULL,NULL,NULL,NULL,'hermes',
      'contract',0,0,0,$7::numeric,NULL,NULL,NULL,NULL,NULL,0,NULL,NULL,$6
    )`,
      [
        runId,
        input.workspaceId,
        taskId,
        agentId,
        input.billingClass,
        now,
        input.costUsd,
      ],
    );
    await client.query("COMMIT");
  } catch (error) {
    await client.query("ROLLBACK");
    throw error;
  }
  return { runId, taskId, agentId };
}

async function reserve(
  client: PoolClient,
  workspaceId: string,
  runId: string,
  estimatedCostUsd: string,
) {
  const result = await client.query<{ state: string }>(
    `SELECT state
    FROM agentops_reserve_run_cost_v10(
      $1,$2,$3::numeric,$4,$5,interval '1 hour'
    )`,
    [
      workspaceId,
      runId,
      estimatedCostUsd,
      hash(`reserve:${workspaceId}:${runId}`),
      hash(`reserve-request:${workspaceId}:${runId}:${estimatedCostUsd}`),
    ],
  );
  assert.equal(result.rows[0]?.state, "reserved");
}

async function closeRun(
  client: PoolClient,
  input: Readonly<{
    workspaceId: string;
    runId: string;
    terminalStatus: TerminalStatus;
  }>,
) {
  await client.query("BEGIN");
  try {
    const closure = await settleExistingTerminalRunCost(client, input);
    await client.query(
      `UPDATE runs
      SET status=$1,ended_at=clock_timestamp()
      WHERE workspace_id=$2 AND run_id=$3`,
      [input.terminalStatus, input.workspaceId, input.runId],
    );
    await client.query("COMMIT");
    return closure;
  } catch (error) {
    await client.query("ROLLBACK");
    throw error;
  }
}

async function assertReservedWriter(
  client: PoolClient,
  input: Readonly<{
    workspaceId: string;
    suffix: string;
    costUsd: string;
    billingClass: "metered_execution";
    terminalStatus: TerminalStatus;
  }>,
) {
  const fixture = await seedRun(client, {
    ...input,
    reserveEstimateUsd: "5.000000",
  });
  const closure = await closeRun(client, {
    workspaceId: input.workspaceId,
    runId: fixture.runId,
    terminalStatus: input.terminalStatus,
  });
  assert.equal(closure.mode, "settled_existing_reservation");
  assert.equal(closure.reservation?.state, "settled");

  const row = (await client.query<{
    run_status: string;
    state: string;
    settled_cost_usd: string;
  }>(
    `SELECT run.status AS run_status,reservation.state,
      reservation.settled_cost_usd::text
    FROM runs run
    JOIN run_cost_reservations reservation
      ON reservation.workspace_id=run.workspace_id
      AND reservation.run_id=run.run_id
    WHERE run.workspace_id=$1 AND run.run_id=$2`,
    [input.workspaceId, fixture.runId],
  )).rows[0];
  assert.equal(row?.run_status, input.terminalStatus);
  assert.equal(row?.state, "settled");
  assert.equal(row?.settled_cost_usd, "5.000000");

  await client.query("BEGIN");
  try {
    const replay = await settleExistingTerminalRunCost(client, {
      workspaceId: input.workspaceId,
      runId: fixture.runId,
      terminalStatus: input.terminalStatus,
    });
    assert.equal(replay.mode, "settled_existing_reservation");
    assert.equal(replay.reservation?.settled_cost_usd, "5.000000");
    await client.query("COMMIT");
  } catch (error) {
    await client.query("ROLLBACK");
    throw error;
  }
}

async function assertTransactionRollback(
  client: PoolClient,
  workspaceId: string,
) {
  const fixture = await seedRun(client, {
    workspaceId,
    suffix: "rollback",
    costUsd: "0.500000",
    billingClass: "metered_execution",
    reserveEstimateUsd: "5.000000",
  });
  await client.query("BEGIN");
  await settleExistingTerminalRunCost(client, {
    workspaceId,
    runId: fixture.runId,
    terminalStatus: "blocked",
  });
  await client.query(
    "UPDATE runs SET status='blocked' WHERE run_id=$1",
    [fixture.runId],
  );
  await client.query("ROLLBACK");
  const row = (await client.query<{ run_status: string; state: string }>(
    `SELECT run.status AS run_status,reservation.state
    FROM runs run
    JOIN run_cost_reservations reservation ON reservation.run_id=run.run_id
    WHERE run.run_id=$1`,
    [fixture.runId],
  )).rows[0];
  assert.equal(row?.run_status, "running");
  assert.equal(row?.state, "reserved");
}

async function run() {
  assert.ok(baseDsn, "AGENTOPS_POSTGRES_DSN is required");
  stage = "production_writer_bindings";
  await assertProductionWriterBindings();

  stage = "admin_connect";
  const admin = new Client({
    connectionString: baseDsn,
    application_name: "agentops-run-cost-terminal-contract-admin",
  });
  await admin.connect();
  const schema = schemaName();
  let schemaCreated = false;
  let pool: Pool | undefined;
  try {
    stage = "schema_create";
    await admin.query(`CREATE SCHEMA ${quotedSchema(schema)}`);
    schemaCreated = true;
    const connectionString = scopedDsn(schema);
    stage = "schema_migrate";
    await runPostgresSchemaCommand("migrate", { connectionString });
    pool = new Pool({
      connectionString,
      max: 2,
      application_name: "agentops-run-cost-terminal-contract",
    });
    const client = await pool.connect();
    try {
      const workspaceId = "ws_terminal_cost_contract";
      stage = "entitlement_seed";
      await seedEntitlement(client, workspaceId);
      stage = "approval_rejection";
      await assertReservedWriter(client, {
        workspaceId,
        suffix: "approval_rejected",
        costUsd: "0.125000",
        billingClass: "metered_execution",
        terminalStatus: "blocked",
      });
      stage = "prepared_action_success";
      await assertReservedWriter(client, {
        workspaceId,
        suffix: "prepared_success",
        costUsd: "1.250000",
        billingClass: "metered_execution",
        terminalStatus: "completed",
      });
      stage = "prepared_action_failure";
      await assertReservedWriter(client, {
        workspaceId,
        suffix: "prepared_failure",
        costUsd: "2.500000",
        billingClass: "metered_execution",
        terminalStatus: "blocked",
      });
      stage = "transaction_rollback";
      await assertTransactionRollback(client, workspaceId);

      stage = "output";
      const output = JSON.stringify({
        contract: "run_cost_terminal_writers_postgres_v1",
        ok: true,
        control_plane: "typescript_postgres",
        schema_contract: SCHEMA_CONTRACT,
        migration_count: POSTGRES_MIGRATION_MANIFEST.length,
        production_terminal_writers_bound: true,
        approval_rejection_settled: true,
        prepared_action_success_settled: true,
        prepared_action_failure_settled: true,
        untrusted_zero_refund_forbidden: true,
        settlement_replay_idempotent: true,
        billing_class_compatibility_covered_by_schema_contract: true,
        settlement_and_terminal_update_atomic: true,
        credentials_omitted: true,
        row_data_omitted: true,
      });
      assert.equal(output.includes(baseDsn), false);
      assert.equal(output.includes(connectionString), false);
      console.log(output);
    } finally {
      client.release();
    }
  } finally {
    await pool?.end().catch(() => undefined);
    if (schemaCreated) {
      await admin.query(`DROP SCHEMA IF EXISTS ${quotedSchema(schema)} CASCADE`);
    }
    await admin.end().catch(() => undefined);
  }
}

run().catch((error: unknown) => {
  const failureCode = (
    error instanceof ControlPlaneHttpError
      ? error.code
      : String((error as { code?: unknown })?.code || "contract_failed")
  ).slice(0, 80);
  const constraint = String(
    (error as { constraint?: unknown })?.constraint || "",
  ).slice(0, 120);
  const databaseSymbol = String(
    (error as { message?: unknown })?.message || "",
  );
  console.log(JSON.stringify({
    contract: "run_cost_terminal_writers_postgres_v1",
    ok: false,
    error_code: failureCode,
    constraint: constraint || null,
    database_error_symbol: /^[a-z0-9_]{1,120}$/.test(databaseSymbol)
      ? databaseSymbol
      : null,
    stage,
    credentials_omitted: true,
    row_data_omitted: true,
  }));
  process.exitCode = 1;
});
