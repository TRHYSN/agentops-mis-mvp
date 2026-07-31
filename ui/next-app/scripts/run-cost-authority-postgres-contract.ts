import assert from "node:assert/strict";
import { createHash, randomBytes } from "node:crypto";

import { Client } from "pg";

import {
  heartbeatAgentGatewayRun,
  startAgentGatewayRun,
} from "../src/server/controlPlane/agentGatewayRuns";
import { closeControlPlanePoolForTests } from "../src/server/controlPlane/db";
import { ControlPlaneHttpError } from "../src/server/controlPlane/http";
import {
  runPostgresSchemaCommand,
  SchemaReadinessError,
} from "../src/server/controlPlane/schemaReadiness";

const CONTRACT = "agentops_run_cost_authority_postgres_contract_v1";
const baseDsn = String(process.env.AGENTOPS_POSTGRES_DSN || "").trim();
const schema = `agentops_run_cost_api_${randomBytes(6).toString("hex")}`;
const applicationName = `agentops-run-cost-api-${randomBytes(5).toString("hex")}`;
const ownerUserId = "usr_run_cost_authority";

const fixtures = Object.freeze({
  main: {
    workspaceId: "ws_run_cost_authority",
    agentId: "agt_run_cost_authority",
    tokenId: "tok_run_cost_authority",
    token: `contract_token_${randomBytes(18).toString("hex")}`,
  },
  race: {
    workspaceId: "ws_run_cost_race",
    agentId: "agt_run_cost_race",
    tokenId: "tok_run_cost_race",
    token: `contract_token_${randomBytes(18).toString("hex")}`,
  },
  expiry: {
    workspaceId: "ws_run_cost_expiry",
    agentId: "agt_run_cost_expiry",
    tokenId: "tok_run_cost_expiry",
    token: `contract_token_${randomBytes(18).toString("hex")}`,
  },
});

type Fixture = (typeof fixtures)[keyof typeof fixtures];
type ApiResult = Readonly<{
  status: number;
  body: Record<string, unknown>;
}>;
type CostSnapshot = Readonly<{
  state: string;
  estimated_cost_usd: string;
  observed_cost_usd: string;
  settled_cost_usd: string | null;
  expires_at: string;
  run_cost_usd: string;
  run_status: string;
}>;

let failureStage = "bootstrap";

function sha256(value: string) {
  return createHash("sha256").update(value, "utf8").digest("hex");
}

function quotedIdentifier(value: string) {
  assert.match(value, /^[a-z][a-z0-9_]{0,62}$/);
  return `"${value}"`;
}

function scopedDsn() {
  const parsed = new URL(baseDsn);
  parsed.searchParams.set(
    "options",
    `-csearch_path=${schema} -cstatement_timeout=12000 -clock_timeout=8000`,
  );
  parsed.searchParams.set("application_name", applicationName);
  return parsed.toString();
}

function request(
  path: string,
  fixture: Fixture,
  body: Record<string, unknown>,
) {
  return new Request(`http://agentops.test${path}`, {
    method: "POST",
    headers: new Headers({
      "authorization": `Bearer ${fixture.token}`,
      "content-type": "application/json",
      "x-agentops-workspace-id": fixture.workspaceId,
      "x-agentops-agent-id": fixture.agentId,
    }),
    body: JSON.stringify(body),
  });
}

async function start(
  fixture: Fixture,
  body: Record<string, unknown>,
) {
  return await startAgentGatewayRun(
    request("/api/mis/agent-gateway/runs/start", fixture, body),
  ) as unknown as ApiResult;
}

async function heartbeat(
  fixture: Fixture,
  runId: string,
  body: Record<string, unknown>,
) {
  return await heartbeatAgentGatewayRun(
    request(
      `/api/mis/agent-gateway/runs/${runId}/heartbeat`,
      fixture,
      body,
    ),
    runId,
  ) as unknown as ApiResult;
}

async function expectHttpError(
  status: number,
  code: string,
  work: () => Promise<unknown>,
) {
  let captured: unknown;
  try {
    await work();
  } catch (error) {
    captured = error;
  }
  assert.ok(captured instanceof ControlPlaneHttpError);
  assert.equal(captured.status, status);
  assert.equal(captured.code, code);
}

function record(value: unknown, label: string) {
  assert.ok(
    value !== null && typeof value === "object" && !Array.isArray(value),
    `${label} must be an object`,
  );
  return value as Record<string, unknown>;
}

function reservation(body: Record<string, unknown>) {
  return record(body.cost_reservation, "cost_reservation");
}

function publicNumber(value: unknown, label: string) {
  if (typeof value === "number") return value;
  const wrapper = record(value, label);
  assert.deepEqual(Object.keys(wrapper), ["__agentops_python_float__"]);
  assert.equal(typeof wrapper.__agentops_python_float__, "number");
  return wrapper.__agentops_python_float__ as number;
}

function timestampMillis(value: unknown, label: string) {
  const timestamp = value instanceof Date
    ? value.getTime()
    : Date.parse(String(value ?? ""));
  assert.ok(Number.isFinite(timestamp), `${label} must be a timestamp`);
  return timestamp;
}

const FORBIDDEN_RESPONSE_KEYS = new Set([
  "reservation_id",
  "idempotency_key_hash",
  "request_hash",
  "settlement_idempotency_key_hash",
  "settlement_request_hash",
  "release_idempotency_key_hash",
  "release_request_hash",
  "token",
  "token_hash",
  "authorization",
  "raw_prompt",
  "raw_response",
  "raw_payload",
]);

function assertSafeResponse(
  body: Record<string, unknown>,
  secrets: readonly string[],
) {
  const pending: unknown[] = [body];
  while (pending.length > 0) {
    const current = pending.pop();
    if (Array.isArray(current)) {
      pending.push(...current);
      continue;
    }
    if (!current || typeof current !== "object") continue;
    for (const [key, value] of Object.entries(current)) {
      assert.equal(
        FORBIDDEN_RESPONSE_KEYS.has(key),
        false,
        `response exposed private key ${key}`,
      );
      pending.push(value);
    }
  }
  const serialized = JSON.stringify(body);
  for (const secret of secrets) {
    assert.equal(serialized.includes(secret), false);
  }
  assert.equal(serialized.includes("Bearer "), false);
  assert.equal(serialized.includes("raw prompt contract marker"), false);
  assert.equal(serialized.includes("raw response contract marker"), false);
}

function assertAllowedStart(result: ApiResult, expectedStatus: 200 | 201) {
  assert.equal(result.status, expectedStatus);
  assert.equal(result.body.ok, true);
  assert.equal(result.body.control_plane, "typescript_postgres");
  assert.equal(result.body.operation, "run_start");
  assert.equal(result.body.token_omitted, true);
  assertSafeResponse(
    result.body,
    Object.values(fixtures).map((fixture) => fixture.token),
  );
}

function assertAllowedHeartbeat(result: ApiResult) {
  assert.equal(result.status, 200);
  assert.equal(result.body.ok, true);
  assert.equal(result.body.control_plane, "typescript_postgres");
  assert.equal(result.body.operation, "run_heartbeat");
  assert.equal(result.body.token_omitted, true);
  assertSafeResponse(
    result.body,
    Object.values(fixtures).map((fixture) => fixture.token),
  );
}

function assertDeniedStart(result: ApiResult, reasonCode: string) {
  assert.equal(result.status, 403);
  assert.equal(result.body.ok, false);
  assert.equal(result.body.error, "workspace_entitlement_denied");
  assert.equal(result.body.reason_code, reasonCode);
  assert.equal(result.body.reservation_created, false);
  assert.equal(result.body.credentials_omitted, true);
  assert.equal(result.body.raw_config_omitted, true);
  assertSafeResponse(
    result.body,
    Object.values(fixtures).map((fixture) => fixture.token),
  );
}

function startBody(
  fixture: Fixture,
  taskId: string,
  runId: string,
  estimatedCostUsd: string,
) {
  return {
    workspace_id: fixture.workspaceId,
    agent_id: fixture.agentId,
    task_id: taskId,
    run_id: runId,
    runtime_type: "mock",
    estimated_cost_usd: estimatedCostUsd,
    input_summary: "Bounded run cost authority contract.",
  };
}

async function seedEntitlement(
  client: Client,
  fixture: Fixture,
  limits: Readonly<{
    maxConcurrentRuns: number;
    maxMonthlyRuns: number;
    maxMonthlyCostUsd: string;
  }>,
) {
  await client.query(
    `INSERT INTO workspace_entitlements(
      workspace_id,edition,status,capabilities_json,max_agents,
      max_active_enrollments,max_active_sessions_per_agent,max_monthly_runs,
      max_monthly_cost_usd,max_concurrent_runs,effective_at,expires_at,
      created_at,updated_at,updated_by_user_id
    ) VALUES(
      $1,'team_governance','active',
      jsonb_build_object(
        'enrollment_issue',true,
        'session_issue',true,
        'run_start',true
      ),
      20,20,10,$2,$3::numeric,$4,
      clock_timestamp()-interval '1 hour',
      clock_timestamp()+interval '1 year',
      clock_timestamp(),clock_timestamp(),$5
    )`,
    [
      fixture.workspaceId,
      limits.maxMonthlyRuns,
      limits.maxMonthlyCostUsd,
      limits.maxConcurrentRuns,
      ownerUserId,
    ],
  );
}

async function seedFixture(
  client: Client,
  fixture: Fixture,
  taskIds: readonly string[],
  limits: Readonly<{
    maxConcurrentRuns: number;
    maxMonthlyRuns: number;
    maxMonthlyCostUsd: string;
  }>,
) {
  const now = new Date().toISOString();
  const future = new Date(Date.now() + 60 * 60 * 1000).toISOString();
  await client.query(
    `INSERT INTO agents(
      agent_id,name,role,description,runtime_type,model_provider,model_name,
      status,permission_level,allowed_tools,budget_limit_usd,owner_user_id,
      created_at,updated_at
    ) VALUES(
      $1,'Run cost contract agent','worker',NULL,'mock','external',
      'gateway-contract','idle','standard','[]',0,$2,$3,$3
    )`,
    [fixture.agentId, ownerUserId, now],
  );
  await client.query(
    `INSERT INTO agent_gateway_tokens(
      token_id,token_hash,workspace_id,agent_id,scopes_json,status,label,
      heartbeat_timeout_sec,created_at,expires_at,revoked_at,last_used_at,
      last_heartbeat_at
    ) VALUES(
      $1,$2,$3,$4,$5,'active','run-cost-contract',300,$6,$7,NULL,NULL,NULL
    )`,
    [
      fixture.tokenId,
      sha256(fixture.token),
      fixture.workspaceId,
      fixture.agentId,
      JSON.stringify(["runs:write"]),
      now,
      future,
    ],
  );
  await seedEntitlement(client, fixture, limits);
  for (const taskId of taskIds) {
    await client.query(
      `INSERT INTO tasks(
        task_id,workspace_id,title,description,requester_id,owner_agent_id,
        collaborator_agent_ids,status,priority,due_date,acceptance_criteria,
        risk_level,budget_limit_usd,created_at,updated_at
      ) VALUES(
        $1,$2,'Run cost authority contract',
        'Bounded fixture with raw content omitted.',$3,$4,'[]','planned',
        'high',NULL,'Verify transactional cost authority.','medium',0,$5,$5
      )`,
      [
        taskId,
        fixture.workspaceId,
        ownerUserId,
        fixture.agentId,
        now,
      ],
    );
  }
}

async function seed(client: Client) {
  const now = new Date().toISOString();
  await client.query(
    `INSERT INTO users(user_id,name,email,role,created_at)
    VALUES($1,'Run Cost Contract','run-cost-contract@example.invalid','owner',$2)`,
    [ownerUserId, now],
  );
  await seedFixture(
    client,
    fixtures.main,
    [
      "tsk_run_cost_main",
      "tsk_run_cost_estimate",
      "tsk_run_cost_binding",
    ],
    {
      maxConcurrentRuns: 4,
      maxMonthlyRuns: 10,
      maxMonthlyCostUsd: "100.000000",
    },
  );
  await seedFixture(
    client,
    fixtures.race,
    ["tsk_run_cost_race_a", "tsk_run_cost_race_b"],
    {
      maxConcurrentRuns: 1,
      maxMonthlyRuns: 10,
      maxMonthlyCostUsd: "100.000000",
    },
  );
  await seedFixture(
    client,
    fixtures.expiry,
    ["tsk_run_cost_expiry_a", "tsk_run_cost_expiry_b"],
    {
      maxConcurrentRuns: 2,
      maxMonthlyRuns: 1,
      maxMonthlyCostUsd: "100.000000",
    },
  );
}

async function snapshot(
  client: Client,
  workspaceId: string,
  runId: string,
) {
  const result = await client.query<CostSnapshot>(
    `SELECT
      reservation.state,
      reservation.estimated_cost_usd::text,
      reservation.observed_cost_usd::text,
      reservation.settled_cost_usd::text,
      reservation.expires_at::text,
      run.cost_usd::numeric(18,6)::text AS run_cost_usd,
      run.status AS run_status
    FROM run_cost_reservations reservation
    JOIN runs run
      ON run.workspace_id=reservation.workspace_id
      AND run.run_id=reservation.run_id
    WHERE reservation.workspace_id=$1 AND reservation.run_id=$2`,
    [workspaceId, runId],
  );
  assert.equal(result.rowCount, 1);
  return result.rows[0];
}

async function assertInvalidStarts(client: Client) {
  const fixture = fixtures.main;
  const taskId = "tsk_run_cost_main";
  const invalidRunIds = [
    "run_cost_missing_estimate",
    "run_cost_zero_estimate",
    "run_cost_client_cost",
    "run_cost_client_started_at",
  ];
  const base = {
    workspace_id: fixture.workspaceId,
    agent_id: fixture.agentId,
    task_id: taskId,
    runtime_type: "mock",
  };

  await expectHttpError(
    400,
    "estimated_cost_usd_invalid",
    () => start(fixture, {
      ...base,
      run_id: invalidRunIds[0],
    }),
  );
  await expectHttpError(
    400,
    "estimated_cost_usd_invalid",
    () => start(fixture, {
      ...base,
      run_id: invalidRunIds[1],
      estimated_cost_usd: "0.000000",
    }),
  );
  await expectHttpError(
    400,
    "run_cost_usd_server_owned",
    () => start(fixture, {
      ...base,
      run_id: invalidRunIds[2],
      estimated_cost_usd: "2.000001",
      cost_usd: "0.000001",
    }),
  );
  await expectHttpError(
    400,
    "run_started_at_server_owned",
    () => start(fixture, {
      ...base,
      run_id: invalidRunIds[3],
      estimated_cost_usd: "2.000001",
      started_at: new Date(0).toISOString(),
    }),
  );

  const writes = await client.query<{ count: number }>(
    `SELECT (
      (SELECT COUNT(*) FROM runs WHERE run_id=ANY($1::text[]))
      + (SELECT COUNT(*) FROM run_cost_reservations
          WHERE run_id=ANY($1::text[]))
    )::int AS count`,
    [invalidRunIds],
  );
  assert.equal(writes.rows[0]?.count, 0);
}

async function assertPrecisionReplayAndTerminal(client: Client) {
  const fixture = fixtures.main;
  const runId = "run_cost_authority_main";
  const body = startBody(
    fixture,
    "tsk_run_cost_main",
    runId,
    "2.000001",
  );
  failureStage = "precision_start_response";
  const created = await start(fixture, body);
  failureStage = "precision_start_allowed";
  assertAllowedStart(created, 201);
  failureStage = "precision_start_public_values";
  assert.equal(created.body.outcome, "created");
  const createdRun = record(created.body.run, "run");
  const createdReservation = reservation(created.body);
  assert.equal(publicNumber(createdRun.cost_usd, "run.cost_usd"), 0);
  assert.equal(createdReservation.state, "reserved");
  assert.equal(createdReservation.estimated_cost_usd, "2.000001");
  assert.equal(createdReservation.observed_cost_usd, "0.000000");
  assert.equal(createdReservation.settled_cost_usd, null);

  failureStage = "precision_start_database";
  const firstSnapshot = await snapshot(client, fixture.workspaceId, runId);
  assert.equal(firstSnapshot.state, "reserved");
  assert.equal(firstSnapshot.estimated_cost_usd, "2.000001");
  assert.equal(firstSnapshot.observed_cost_usd, "0.000000");
  assert.equal(firstSnapshot.run_cost_usd, "0.000000");

  const authority = await client.query<{
    reservation_id: string;
    idempotency_key_hash: string;
    request_hash: string;
  }>(
    `SELECT reservation_id,idempotency_key_hash,request_hash
    FROM run_cost_reservations
    WHERE workspace_id=$1 AND run_id=$2`,
    [fixture.workspaceId, runId],
  );
  assert.equal(authority.rowCount, 1);
  const serializedCreated = JSON.stringify(created.body);
  assert.equal(
    serializedCreated.includes(authority.rows[0].reservation_id),
    false,
  );
  assert.equal(
    serializedCreated.includes(authority.rows[0].idempotency_key_hash),
    false,
  );
  assert.equal(
    serializedCreated.includes(authority.rows[0].request_hash),
    false,
  );

  failureStage = "precision_start_replay";
  const replay = await start(fixture, body);
  assertAllowedStart(replay, 200);
  assert.equal(replay.body.outcome, "unchanged");
  assert.equal(
    reservation(replay.body).reservation_ref,
    createdReservation.reservation_ref,
  );
  const counts = await client.query<{
    run_count: number;
    reservation_count: number;
  }>(
    `SELECT
      (SELECT COUNT(*) FROM runs
        WHERE workspace_id=$1 AND run_id=$2)::int AS run_count,
      (SELECT COUNT(*) FROM run_cost_reservations
        WHERE workspace_id=$1 AND run_id=$2)::int AS reservation_count`,
    [fixture.workspaceId, runId],
  );
  assert.equal(counts.rows[0]?.run_count, 1);
  assert.equal(counts.rows[0]?.reservation_count, 1);

  failureStage = "precision_binding_conflict";
  await expectHttpError(
    409,
    "cost_reservation_idempotency_conflict",
    () => start(fixture, {
      ...body,
      task_id: "tsk_run_cost_binding",
    }),
  );
  assert.deepEqual(
    await snapshot(client, fixture.workspaceId, runId),
    firstSnapshot,
  );

  failureStage = "precision_first_heartbeat";
  await new Promise((resolve) => setTimeout(resolve, 10));
  const firstHeartbeat = await heartbeat(fixture, runId, {
    workspace_id: fixture.workspaceId,
    agent_id: fixture.agentId,
    task_id: "tsk_run_cost_main",
    status: "running",
    cost_usd: "0.100001",
  });
  failureStage = "precision_first_heartbeat_response";
  assertAllowedHeartbeat(firstHeartbeat);
  assert.equal(firstHeartbeat.body.outcome, "updated");
  const firstHeartbeatReservation = reservation(firstHeartbeat.body);
  failureStage = "precision_first_heartbeat_observed";
  assert.equal(firstHeartbeatReservation.state, "reserved");
  assert.equal(firstHeartbeatReservation.observed_cost_usd, "0.100001");
  assert.match(
    String(firstHeartbeatReservation.observed_cost_usd),
    /^\d+\.\d{6}$/,
  );
  failureStage = "precision_first_heartbeat_lease";
  assert.ok(
    timestampMillis(
      firstHeartbeatReservation.expires_at,
      "first heartbeat expires_at",
    ) > timestampMillis(firstSnapshot.expires_at, "initial expires_at"),
  );

  failureStage = "precision_second_heartbeat";
  await new Promise((resolve) => setTimeout(resolve, 10));
  const secondHeartbeat = await heartbeat(fixture, runId, {
    workspace_id: fixture.workspaceId,
    agent_id: fixture.agentId,
    status: "running",
    cost_usd: "0.100002",
  });
  assertAllowedHeartbeat(secondHeartbeat);
  assert.equal(reservation(secondHeartbeat.body).observed_cost_usd, "0.100002");
  const stableSnapshot = await snapshot(client, fixture.workspaceId, runId);
  assert.equal(stableSnapshot.observed_cost_usd, "0.100002");
  assert.equal(stableSnapshot.run_cost_usd, "0.100002");
  assert.ok(
    timestampMillis(stableSnapshot.expires_at, "second heartbeat expires_at")
      > timestampMillis(
        firstHeartbeatReservation.expires_at,
        "first heartbeat expires_at",
      ),
  );

  failureStage = "precision_atomic_rejection";
  await expectHttpError(
    409,
    "run_cost_decrease_forbidden",
    () => heartbeat(fixture, runId, {
      workspace_id: fixture.workspaceId,
      status: "running",
      cost_usd: "0.100001",
    }),
  );
  assert.deepEqual(
    await snapshot(client, fixture.workspaceId, runId),
    stableSnapshot,
  );
  await expectHttpError(
    409,
    "run_cost_reservation_exceeded",
    () => heartbeat(fixture, runId, {
      workspace_id: fixture.workspaceId,
      status: "running",
      cost_usd: "2.000002",
    }),
  );
  assert.deepEqual(
    await snapshot(client, fixture.workspaceId, runId),
    stableSnapshot,
  );

  failureStage = "precision_actual_terminal";
  const terminalBody = {
    workspace_id: fixture.workspaceId,
    agent_id: fixture.agentId,
    task_id: "tsk_run_cost_main",
    status: "completed",
    output_summary: "Bounded terminal cost receipt.",
  };
  const terminal = await heartbeat(fixture, runId, terminalBody);
  assertAllowedHeartbeat(terminal);
  assert.equal(terminal.body.outcome, "updated");
  assert.equal(
    publicNumber(record(terminal.body.run, "run").cost_usd, "run.cost_usd"),
    0.100002,
  );
  assert.equal(reservation(terminal.body).state, "settled");
  assert.equal(
    reservation(terminal.body).settled_cost_usd,
    "0.100002",
  );
  const settled = await snapshot(client, fixture.workspaceId, runId);
  assert.equal(settled.state, "settled");
  assert.equal(settled.observed_cost_usd, "0.100002");
  assert.equal(settled.settled_cost_usd, "0.100002");
  assert.equal(settled.run_cost_usd, "0.100002");
  assert.equal(settled.run_status, "completed");

  failureStage = "precision_terminal_replay";
  const terminalReplay = await heartbeat(fixture, runId, terminalBody);
  assertAllowedHeartbeat(terminalReplay);
  assert.equal(terminalReplay.body.outcome, "unchanged");
  assert.equal(
    reservation(terminalReplay.body).settled_cost_usd,
    "0.100002",
  );
  assert.deepEqual(
    await snapshot(client, fixture.workspaceId, runId),
    settled,
  );

  failureStage = "precision_estimate_terminal";
  const estimateRunId = "run_cost_authority_estimate";
  const estimateBody = startBody(
    fixture,
    "tsk_run_cost_estimate",
    estimateRunId,
    "1.234567",
  );
  const estimateStart = await start(fixture, estimateBody);
  assertAllowedStart(estimateStart, 201);
  const estimateTerminal = await heartbeat(fixture, estimateRunId, {
    workspace_id: fixture.workspaceId,
    status: "failed",
    cost_usd: "1.234567",
    error_type: "ContractTerminal",
    error_message: "Bounded terminal estimate receipt.",
  });
  assertAllowedHeartbeat(estimateTerminal);
  assert.equal(reservation(estimateTerminal.body).state, "settled");
  assert.equal(
    reservation(estimateTerminal.body).settled_cost_usd,
    "1.234567",
  );
  const estimateSettled = await snapshot(
    client,
    fixture.workspaceId,
    estimateRunId,
  );
  assert.equal(estimateSettled.state, "settled");
  assert.equal(estimateSettled.settled_cost_usd, "1.234567");
  assert.equal(estimateSettled.run_cost_usd, "1.234567");
  assert.equal(estimateSettled.run_status, "failed");
}

async function assertConcurrentSingleWinner(client: Client) {
  const fixture = fixtures.race;
  const runIds = ["run_cost_race_a", "run_cost_race_b"] as const;
  const attempts = await Promise.all([
    start(
      fixture,
      startBody(
        fixture,
        "tsk_run_cost_race_a",
        runIds[0],
        "1.000000",
      ),
    ),
    start(
      fixture,
      startBody(
        fixture,
        "tsk_run_cost_race_b",
        runIds[1],
        "1.000000",
      ),
    ),
  ]);
  const winners = attempts
    .map((result, index) => ({ result, index }))
    .filter(({ result }) => result.status === 201);
  const losers = attempts
    .map((result, index) => ({ result, index }))
    .filter(({ result }) => result.status === 403);
  assert.equal(winners.length, 1);
  assert.equal(losers.length, 1);
  assertAllowedStart(winners[0].result, 201);
  assertDeniedStart(
    losers[0].result,
    "run_concurrency_limit_exceeded",
  );
  const winnerRunId = runIds[winners[0].index];
  const loserRunId = runIds[losers[0].index];

  const authority = await client.query<{
    run_count: number;
    reservation_count: number;
    loser_run_count: number;
    loser_reservation_count: number;
    loser_audit_count: number;
  }>(
    `SELECT
      (SELECT COUNT(*) FROM runs
        WHERE workspace_id=$1 AND run_id=ANY($2::text[]))::int AS run_count,
      (SELECT COUNT(*) FROM run_cost_reservations
        WHERE workspace_id=$1
          AND run_id=ANY($2::text[]))::int AS reservation_count,
      (SELECT COUNT(*) FROM runs
        WHERE workspace_id=$1 AND run_id=$3)::int AS loser_run_count,
      (SELECT COUNT(*) FROM run_cost_reservations
        WHERE workspace_id=$1 AND run_id=$3)::int AS loser_reservation_count,
      (SELECT COUNT(*) FROM audit_logs
        WHERE workspace_id=$1
          AND entity_id=$3
          AND action='agent_gateway.run_start.entitlement_denied'
      )::int AS loser_audit_count`,
    [fixture.workspaceId, runIds, loserRunId],
  );
  assert.equal(authority.rows[0]?.run_count, 1);
  assert.equal(authority.rows[0]?.reservation_count, 1);
  assert.equal(authority.rows[0]?.loser_run_count, 0);
  assert.equal(authority.rows[0]?.loser_reservation_count, 0);
  assert.equal(authority.rows[0]?.loser_audit_count, 1);

  const denialAudit = await client.query<{ metadata_json: unknown }>(
    `SELECT metadata_json
    FROM audit_logs
    WHERE workspace_id=$1
      AND entity_id=$2
      AND action='agent_gateway.run_start.entitlement_denied'`,
    [fixture.workspaceId, loserRunId],
  );
  assert.equal(denialAudit.rowCount, 1);
  const metadata = typeof denialAudit.rows[0].metadata_json === "string"
    ? JSON.parse(denialAudit.rows[0].metadata_json)
    : denialAudit.rows[0].metadata_json;
  const safeMetadata = record(metadata, "denial audit metadata");
  assert.equal(
    safeMetadata.reason_code,
    "run_concurrency_limit_exceeded",
  );
  assert.equal(safeMetadata.credentials_omitted, true);
  assert.equal(safeMetadata.raw_config_omitted, true);

  const winnerTerminal = await heartbeat(fixture, winnerRunId, {
    workspace_id: fixture.workspaceId,
    status: "completed",
    cost_usd: "1.000000",
  });
  assertAllowedHeartbeat(winnerTerminal);
  assert.equal(reservation(winnerTerminal.body).state, "settled");
}

async function expireReservationForContract(
  client: Client,
  workspaceId: string,
  runId: string,
) {
  await new Promise((resolve) => setTimeout(resolve, 10));
  await client.query("BEGIN");
  try {
    await client.query(
      `ALTER TABLE run_cost_reservations
      DISABLE TRIGGER run_cost_reservations_guard_v10`,
    );
    await client.query(
      `UPDATE run_cost_reservations
      SET expires_at=reserved_at+interval '1 microsecond'
      WHERE workspace_id=$1 AND run_id=$2 AND state='reserved'`,
      [workspaceId, runId],
    );
    await client.query(
      `ALTER TABLE run_cost_reservations
      ENABLE TRIGGER run_cost_reservations_guard_v10`,
    );
    await client.query("COMMIT");
  } catch (error) {
    await client.query("ROLLBACK").catch(() => undefined);
    throw error;
  }
  const expired = await client.query<{ count: string }>(
    `SELECT agentops_expire_run_cost_reservations_v10($1)::text AS count`,
    [workspaceId],
  );
  assert.equal(expired.rows[0]?.count, "1");
}

async function assertExpiredRecoveryAndMonthlyAuthority(client: Client) {
  const fixture = fixtures.expiry;
  const runId = "run_cost_expiry_a";
  const created = await start(
    fixture,
    startBody(
      fixture,
      "tsk_run_cost_expiry_a",
      runId,
      "4.000000",
    ),
  );
  assertAllowedStart(created, 201);
  await expireReservationForContract(
    client,
    fixture.workspaceId,
    runId,
  );
  const expired = await snapshot(client, fixture.workspaceId, runId);
  assert.equal(expired.state, "expired");
  assert.equal(expired.observed_cost_usd, "0.000000");

  const monthlyDeniedRunId = "run_cost_expiry_b";
  const monthlyDenied = await start(
    fixture,
    startBody(
      fixture,
      "tsk_run_cost_expiry_b",
      monthlyDeniedRunId,
      "1.000000",
    ),
  );
  assertDeniedStart(monthlyDenied, "monthly_run_quota_exceeded");
  const deniedWrites = await client.query<{
    run_count: number;
    reservation_count: number;
    audit_count: number;
  }>(
    `SELECT
      (SELECT COUNT(*) FROM runs
        WHERE workspace_id=$1 AND run_id=$2)::int AS run_count,
      (SELECT COUNT(*) FROM run_cost_reservations
        WHERE workspace_id=$1 AND run_id=$2)::int AS reservation_count,
      (SELECT COUNT(*) FROM audit_logs
        WHERE workspace_id=$1
          AND entity_id=$2
          AND action='agent_gateway.run_start.entitlement_denied'
      )::int AS audit_count`,
    [fixture.workspaceId, monthlyDeniedRunId],
  );
  assert.equal(deniedWrites.rows[0]?.run_count, 0);
  assert.equal(deniedWrites.rows[0]?.reservation_count, 0);
  assert.equal(deniedWrites.rows[0]?.audit_count, 1);

  const renewed = await heartbeat(fixture, runId, {
    workspace_id: fixture.workspaceId,
    status: "running",
    cost_usd: "1.000001",
  });
  assertAllowedHeartbeat(renewed);
  const renewedReservation = reservation(renewed.body);
  assert.equal(renewedReservation.state, "reserved");
  assert.equal(renewedReservation.observed_cost_usd, "1.000001");
  assert.equal(renewedReservation.expired_at, null);
  assert.ok(
    timestampMillis(renewedReservation.expires_at, "renewed expires_at")
      > Date.now(),
  );
  const renewedSnapshot = await snapshot(
    client,
    fixture.workspaceId,
    runId,
  );
  assert.equal(renewedSnapshot.state, "reserved");
  assert.equal(renewedSnapshot.observed_cost_usd, "1.000001");
  assert.equal(renewedSnapshot.run_cost_usd, "1.000001");

  const terminal = await heartbeat(fixture, runId, {
    workspace_id: fixture.workspaceId,
    status: "completed",
    cost_usd: "4.000000",
  });
  assertAllowedHeartbeat(terminal);
  assert.equal(reservation(terminal.body).state, "settled");
  assert.equal(
    reservation(terminal.body).settled_cost_usd,
    "4.000000",
  );
}

function restoreEnvironment(
  original: Readonly<Record<string, string | undefined>>,
) {
  for (const [name, value] of Object.entries(original)) {
    if (value === undefined) delete process.env[name];
    else process.env[name] = value;
  }
}

async function runContract() {
  assert.ok(baseDsn, "AGENTOPS_POSTGRES_DSN is required");
  const admin = new Client({
    connectionString: baseDsn,
    application_name: `${applicationName}-admin`,
  });
  const originalEnvironment = {
    AGENTOPS_POSTGRES_DSN: process.env.AGENTOPS_POSTGRES_DSN,
    AGENTOPS_POSTGRES_SSL: process.env.AGENTOPS_POSTGRES_SSL,
    AGENTOPS_DEPLOYMENT_MODE: process.env.AGENTOPS_DEPLOYMENT_MODE,
    AGENTOPS_CONTROL_PLANE_MODE: process.env.AGENTOPS_CONTROL_PLANE_MODE,
    AGENTOPS_POSTGRES_APPLICATION_NAME:
      process.env.AGENTOPS_POSTGRES_APPLICATION_NAME,
    AGENTOPS_POSTGRES_POOL_MAX: process.env.AGENTOPS_POSTGRES_POOL_MAX,
  };
  let schemaCreated = false;
  await admin.connect();
  try {
    failureStage = "postgres_16";
    const version = await admin.query<{ server_version_num: string }>(
      "SHOW server_version_num",
    );
    const serverVersion = Number(version.rows[0]?.server_version_num || 0);
    assert.ok(serverVersion >= 160000 && serverVersion < 170000);

    failureStage = "schema_create";
    await admin.query(`CREATE SCHEMA ${quotedIdentifier(schema)}`);
    schemaCreated = true;
    const connectionString = scopedDsn();
    process.env.AGENTOPS_POSTGRES_DSN = connectionString;
    process.env.AGENTOPS_POSTGRES_SSL = "0";
    process.env.AGENTOPS_DEPLOYMENT_MODE = "production";
    process.env.AGENTOPS_CONTROL_PLANE_MODE = "postgres";
    process.env.AGENTOPS_POSTGRES_APPLICATION_NAME = applicationName;
    process.env.AGENTOPS_POSTGRES_POOL_MAX = "12";

    failureStage = "schema_migrate";
    const migration = await runPostgresSchemaCommand(
      "migrate",
      { connectionString },
    );
    assert.equal(migration.ok, true);
    assert.equal(migration.schema_fingerprint_verified, true);

    const client = new Client({
      connectionString,
      application_name: `${applicationName}-fixture`,
    });
    await client.connect();
    try {
      failureStage = "fixture_seed";
      await seed(client);
      failureStage = "invalid_start_fields";
      await assertInvalidStarts(client);
      failureStage = "precision_replay_terminal";
      await assertPrecisionReplayAndTerminal(client);
      failureStage = "concurrent_single_winner";
      await assertConcurrentSingleWinner(client);
      failureStage = "expired_recovery";
      await assertExpiredRecoveryAndMonthlyAuthority(client);

      const output = JSON.stringify({
        contract: CONTRACT,
        ok: true,
        postgres_major: 16,
        isolated_schema: true,
        schema_runner_verified: true,
        typescript_postgres_api_direct: true,
        estimated_cost_required: true,
        server_owned_start_fields_rejected: true,
        reserved_cost_starts_at_zero: true,
        exact_numeric_18_6: true,
        exact_start_replay: true,
        replay_binding_conflict: true,
        concurrent_start_single_winner: true,
        committed_denial_audit: true,
        denied_run_and_reservation_absent: true,
        running_heartbeat_monotonic: true,
        running_heartbeat_renews_lease: true,
        decrease_and_overestimate_atomic: true,
        actual_and_estimate_terminal_settlement: true,
        exact_terminal_replay: true,
        expired_monthly_usage_retained: true,
        expired_reservation_heartbeat_recovered: true,
        authority_hashes_omitted: true,
        credentials_omitted: true,
        raw_prompt_response_omitted: true,
        dsn_omitted: true,
        sql_omitted: true,
        row_data_omitted: true,
      });
      assert.equal(output.includes(baseDsn), false);
      assert.equal(output.includes(connectionString), false);
      for (const fixture of Object.values(fixtures)) {
        assert.equal(output.includes(fixture.token), false);
      }
      console.log(output);
    } finally {
      await client.end().catch(() => undefined);
    }
  } finally {
    await closeControlPlanePoolForTests().catch(() => undefined);
    restoreEnvironment(originalEnvironment);
    if (schemaCreated) {
      await admin.query(
        `DROP SCHEMA IF EXISTS ${quotedIdentifier(schema)} CASCADE`,
      ).catch(() => undefined);
    }
    await admin.end().catch(() => undefined);
  }
}

await runContract().catch((error: unknown) => {
  const errorCode = error instanceof ControlPlaneHttpError
    ? error.code
    : error instanceof SchemaReadinessError
      ? error.code
      : (error as { code?: unknown })?.code === "ERR_ASSERTION"
        ? "assertion_failed"
        : "unexpected_contract_failure";
  console.log(JSON.stringify({
    contract: CONTRACT,
    ok: false,
    error_code: errorCode,
    failure_stage: failureStage,
    credentials_omitted: true,
    raw_prompt_response_omitted: true,
    dsn_omitted: true,
    sql_omitted: true,
    row_data_omitted: true,
  }));
  process.exitCode = 1;
});
