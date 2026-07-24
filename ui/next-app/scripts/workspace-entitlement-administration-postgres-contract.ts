import assert from "node:assert/strict";
import {
  randomBytes,
  randomUUID,
  scryptSync,
} from "node:crypto";
import { readFile } from "node:fs/promises";

import { Client } from "pg";

import {
  executeWorkspaceEntitlementAdministration,
  parseWorkspaceEntitlementArguments,
  WORKSPACE_ENTITLEMENT_ADMINISTRATION_CONTRACT,
  WorkspaceEntitlementAdministrationError,
  type WorkspaceEntitlementAdministrationReceipt,
  type WorkspaceEntitlementAdministrationRequest,
} from "./configure-workspace-entitlement";
import {
  POSTGRES_MIGRATION_MANIFEST,
  runPostgresSchemaCommand,
  SCHEMA_CONTRACT,
} from "../src/server/controlPlane/schemaReadiness";
import { HUMAN_SCRYPT_PARAMS } from "../src/server/controlPlane/humanPasswordPolicy";

const NOW = new Date("2026-07-24T12:00:00.000Z");
const EFFECTIVE_AT = "2026-07-24T11:00:00.000Z";
const EXPIRES_AT = "2027-07-24T12:00:00.000Z";
const OPERATOR_ID = "husr_entitlement_operator";
const OPERATOR_CANARY = "operator-private-canary";
const OPERATOR_PASSWORD = `${randomBytes(24).toString("base64url")}Aa1!`;

type ArgumentOverrides = Readonly<{
  workspaceId?: string;
  operatorUserId?: string;
  edition?: string;
  status?: string;
  capabilities?: string;
  maxAgents?: string;
  maxActiveEnrollments?: string;
  maxActiveSessionsPerAgent?: string;
  maxMonthlyRuns?: string;
  maxMonthlyCostUsd?: string;
  effectiveAt?: string;
  expiresAt?: string;
  confirm?: boolean;
  expectAbsent?: boolean;
  expectedRevision?: string;
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

function entitlementArguments(
  overrides: ArgumentOverrides = {},
) {
  const argumentsList = [
    "--workspace-id",
    overrides.workspaceId || "ws_entitlement_plan",
    "--operator-user-id",
    overrides.operatorUserId || OPERATOR_ID,
    "--edition",
    overrides.edition || "team_governance",
    "--status",
    overrides.status || "active",
    "--capabilities",
    overrides.capabilities
      || "enrollment_issue,session_issue,run_start",
    "--max-agents",
    overrides.maxAgents || "10",
    "--max-active-enrollments",
    overrides.maxActiveEnrollments || "20",
    "--max-active-sessions-per-agent",
    overrides.maxActiveSessionsPerAgent || "5",
    "--max-monthly-runs",
    overrides.maxMonthlyRuns || "1000",
    "--max-monthly-cost-usd",
    overrides.maxMonthlyCostUsd || "5000.25",
    "--effective-at",
    overrides.effectiveAt || EFFECTIVE_AT,
    "--expires-at",
    overrides.expiresAt || EXPIRES_AT,
  ];
  if (overrides.expectedRevision) {
    argumentsList.push("--expected-revision", overrides.expectedRevision);
  }
  if (overrides.expectAbsent) argumentsList.push("--expect-absent");
  if (overrides.confirm) argumentsList.push("--confirm");
  return argumentsList;
}

function replaceArgument(
  argumentsList: string[],
  flag: string,
  value: string,
) {
  const updated = [...argumentsList];
  const index = updated.indexOf(flag);
  assert.notEqual(index, -1);
  updated[index + 1] = value;
  return updated;
}

function removeArgument(argumentsList: string[], flag: string) {
  const updated = [...argumentsList];
  const index = updated.indexOf(flag);
  assert.notEqual(index, -1);
  updated.splice(index, 2);
  return updated;
}

function parse(overrides: ArgumentOverrides = {}) {
  return parseWorkspaceEntitlementArguments(
    entitlementArguments(overrides),
    NOW,
  );
}

async function execute(
  connectionString: string,
  request: WorkspaceEntitlementAdministrationRequest,
  operatorPassword = OPERATOR_PASSWORD,
) {
  const client = new Client({
    connectionString,
    application_name: "agentops-entitlement-administration-contract",
  });
  await client.connect();
  try {
    return await executeWorkspaceEntitlementAdministration(
      client,
      request,
      {
        now: NOW,
        operatorPassword,
      },
    );
  } finally {
    await client.end();
  }
}

async function expectAdministrationError(
  work: () => Promise<unknown>,
  code: string,
) {
  await assert.rejects(work, (error: unknown) => (
    error instanceof WorkspaceEntitlementAdministrationError
    && error.code === code
  ));
}

function expectParserError(argumentsList: string[], code: string) {
  assert.throws(
    () => parseWorkspaceEntitlementArguments(argumentsList, NOW),
    (error: unknown) => (
      error instanceof WorkspaceEntitlementAdministrationError
      && error.code === code
    ),
  );
}

async function seedOperator(
  client: Client,
  workspaceId: string,
  input: Readonly<{
    userId?: string;
    userRole?: string;
    membershipRole?: string;
    membershipStatus?: string;
  }> = {},
) {
  const userId = input.userId || OPERATOR_ID;
  const userRole = input.userRole || "operator";
  const membershipRole = input.membershipRole || "operator";
  const membershipStatus = input.membershipStatus || "active";
  await client.query(
    `INSERT INTO users(user_id,name,email,role,created_at)
    VALUES($1,$2,$3,$4,$5)
    ON CONFLICT(user_id) DO NOTHING`,
    [
      userId,
      "Trusted Entitlement Operator",
      `${userId}@operator.invalid`,
      userRole,
      NOW.toISOString(),
    ],
  );
  await client.query(
    `INSERT INTO workspace_memberships(
      workspace_id,user_id,role,status,created_at,updated_at
    ) VALUES($1,$2,$3,$4,$5,$5)`,
    [
      workspaceId,
      userId,
      membershipRole,
      membershipStatus,
      NOW.toISOString(),
    ],
  );
  const salt = randomBytes(16);
  const passwordHash = scryptSync(
    OPERATOR_PASSWORD,
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
    `INSERT INTO human_login_credentials(
      credential_id,user_id,username,password_hash,password_salt,
      password_params_json,status,created_at,updated_at,last_login_at
    ) VALUES($1,$2,$3,$4,$5,$6,'active',$7,$7,NULL)
    ON CONFLICT(credential_id) DO NOTHING`,
    [
      `credential_${userId}`,
      userId,
      `entitlement-${userId}`,
      passwordHash,
      salt.toString("hex"),
      JSON.stringify(HUMAN_SCRYPT_PARAMS),
      NOW.toISOString(),
    ],
  );
}

async function rowCount(
  client: Client,
  table: "workspace_entitlements" | "audit_logs",
  workspaceId: string,
) {
  const result = await client.query<{ count: string }>(
    `SELECT COUNT(*)::text AS count FROM ${table} WHERE workspace_id=$1`,
    [workspaceId],
  );
  return Number(result.rows[0]?.count || 0);
}

function assertSafeReceipt(
  receipt: WorkspaceEntitlementAdministrationReceipt,
  connectionString: string,
) {
  assert.equal(
    receipt.contract,
    WORKSPACE_ENTITLEMENT_ADMINISTRATION_CONTRACT,
  );
  assert.equal(receipt.ok, true);
  assert.equal(receipt.lock.scope, "workspace");
  assert.equal(receipt.lock.transaction_scoped, true);
  assert.equal(receipt.lock.acquired, true);
  assert.equal(receipt.credentials_omitted, true);
  assert.equal(receipt.dsn_omitted, true);
  assert.equal(receipt.raw_config_omitted, true);
  assert.equal(receipt.python_started, false);
  assert.equal(receipt.sqlite_used, false);
  assert.equal(receipt.external_network_used, false);
  assert.match(receipt.desired_config_hash, /^[a-f0-9]{64}$/);
  const serialized = JSON.stringify(receipt);
  assert.equal(serialized.includes(connectionString), false);
  assert.equal(serialized.includes("postgresql://"), false);
  assert.equal(serialized.includes(OPERATOR_ID), false);
  assert.equal(serialized.includes(OPERATOR_CANARY), false);
  assert.equal(serialized.includes("enrollment_issue"), false);
  assert.equal(serialized.includes("max_monthly_cost_usd"), false);
}

async function assertPlanOnlyZeroWrite(
  connectionString: string,
  fixture: Client,
) {
  const workspaceId = "ws_entitlement_plan";
  await seedOperator(fixture, workspaceId);
  const planned = await execute(connectionString, parse({ workspaceId }));
  assertSafeReceipt(planned, connectionString);
  assert.equal(planned.mode, "plan");
  assert.equal(planned.outcome, "would_create");
  assert.equal(planned.required_guard, "expect_absent");
  assert.equal(planned.revision, null);
  assert.equal(planned.audit_appended, false);
  assert.equal(await rowCount(fixture, "workspace_entitlements", workspaceId), 0);
  assert.equal(await rowCount(fixture, "audit_logs", workspaceId), 0);
}

async function assertCreateUpdateAndReplay(
  connectionString: string,
  fixture: Client,
) {
  const workspaceId = "ws_entitlement_create_update";
  await seedOperator(fixture, workspaceId);
  const createRequest = parse({
    workspaceId,
    confirm: true,
    expectAbsent: true,
  });
  const created = await execute(connectionString, createRequest);
  assertSafeReceipt(created, connectionString);
  assert.equal(created.mode, "confirmed");
  assert.equal(created.outcome, "created");
  assert.equal(created.audit_appended, true);
  assert.match(created.revision || "", /^[a-f0-9]{64}$/);
  assert.equal(await rowCount(fixture, "workspace_entitlements", workspaceId), 1);
  assert.equal(await rowCount(fixture, "audit_logs", workspaceId), 1);

  const storedCreate = await fixture.query<{
    edition: string;
    status: string;
    capabilities_json: Record<string, boolean>;
    max_agents: number;
    max_active_enrollments: number;
    max_active_sessions_per_agent: number;
    max_monthly_runs: number;
    max_monthly_cost_usd: string;
    updated_by_user_id: string;
  }>(
    `SELECT edition,status,capabilities_json,max_agents,
      max_active_enrollments,max_active_sessions_per_agent,max_monthly_runs,
      max_monthly_cost_usd::text AS max_monthly_cost_usd,
      updated_by_user_id
    FROM workspace_entitlements WHERE workspace_id=$1`,
    [workspaceId],
  );
  const createRow = storedCreate.rows[0];
  assert.equal(createRow.edition, "team_governance");
  assert.equal(createRow.status, "active");
  assert.deepEqual(createRow.capabilities_json, {
    enrollment_issue: true,
    run_start: true,
    session_issue: true,
  });
  assert.equal(createRow.max_agents, 10);
  assert.equal(createRow.max_active_enrollments, 20);
  assert.equal(createRow.max_active_sessions_per_agent, 5);
  assert.equal(createRow.max_monthly_runs, 1000);
  assert.equal(createRow.max_monthly_cost_usd, "5000.250000");
  assert.equal(createRow.updated_by_user_id, OPERATOR_ID);

  const createAudit = await fixture.query<{
    actor_type: string;
    actor_id: string;
    action: string;
    entity_type: string;
    entity_id: string;
    before_hash: string | null;
    after_hash: string | null;
    metadata_json: string;
    tamper_chain_hash: string;
  }>(
    `SELECT actor_type,actor_id,action,entity_type,entity_id,before_hash,
      after_hash,metadata_json,tamper_chain_hash
    FROM audit_logs
    WHERE workspace_id=$1
    ORDER BY created_at,audit_id`,
    [workspaceId],
  );
  assert.equal(createAudit.rowCount, 1);
  assert.equal(createAudit.rows[0]?.actor_type, "user");
  assert.equal(createAudit.rows[0]?.actor_id, OPERATOR_ID);
  assert.equal(createAudit.rows[0]?.action, "workspace_entitlement.created");
  assert.equal(
    createAudit.rows[0]?.entity_type,
    "workspace_entitlements",
  );
  assert.equal(createAudit.rows[0]?.entity_id, workspaceId);
  assert.equal(createAudit.rows[0]?.before_hash, null);
  assert.match(createAudit.rows[0]?.after_hash || "", /^[a-f0-9]{64}$/);
  assert.match(
    createAudit.rows[0]?.tamper_chain_hash || "",
    /^[a-f0-9]{64}$/,
  );
  const createMetadata = JSON.parse(
    createAudit.rows[0]?.metadata_json || "{}",
  ) as Record<string, unknown>;
  assert.equal(createMetadata.raw_config_omitted, true);
  assert.equal(createMetadata.dsn_omitted, true);
  assert.equal(createMetadata.credentials_omitted, true);
  assert.equal(
    JSON.stringify(createMetadata).includes("enrollment_issue"),
    false,
  );
  assert.equal(
    JSON.stringify(createMetadata).includes("max_monthly_cost_usd"),
    false,
  );

  const createReplay = await execute(connectionString, createRequest);
  assertSafeReceipt(createReplay, connectionString);
  assert.equal(createReplay.outcome, "unchanged");
  assert.equal(createReplay.revision, created.revision);
  assert.equal(createReplay.audit_appended, false);
  assert.equal(await rowCount(fixture, "workspace_entitlements", workspaceId), 1);
  assert.equal(await rowCount(fixture, "audit_logs", workspaceId), 1);

  const updateRequest = parse({
    workspaceId,
    edition: "enterprise_byoc",
    maxAgents: "25",
    maxActiveEnrollments: "40",
    maxActiveSessionsPerAgent: "8",
    maxMonthlyRuns: "2500",
    maxMonthlyCostUsd: "9000.5",
    confirm: true,
    expectedRevision: created.revision || "",
  });
  const updated = await execute(connectionString, updateRequest);
  assertSafeReceipt(updated, connectionString);
  assert.equal(updated.outcome, "updated");
  assert.equal(updated.audit_appended, true);
  assert.notEqual(updated.revision, created.revision);
  assert.equal(updated.previous_config_hash, created.desired_config_hash);
  assert.equal(await rowCount(fixture, "workspace_entitlements", workspaceId), 1);
  assert.equal(await rowCount(fixture, "audit_logs", workspaceId), 2);

  const updateReplay = await execute(connectionString, updateRequest);
  assertSafeReceipt(updateReplay, connectionString);
  assert.equal(updateReplay.outcome, "unchanged");
  assert.equal(updateReplay.revision, updated.revision);
    assert.equal(updateReplay.audit_appended, false);
    assert.equal(await rowCount(fixture, "audit_logs", workspaceId), 2);

    const reverted = await execute(
      connectionString,
      parse({
        workspaceId,
        confirm: true,
        expectedRevision: updated.revision || "",
      }),
    );
    assert.equal(reverted.outcome, "updated");
    assert.equal(await rowCount(fixture, "audit_logs", workspaceId), 3);
    const cycledBack = await execute(
      connectionString,
      parse({
        workspaceId,
        edition: "enterprise_byoc",
        maxAgents: "25",
        maxActiveEnrollments: "40",
        maxActiveSessionsPerAgent: "8",
        maxMonthlyRuns: "2500",
        maxMonthlyCostUsd: "9000.5",
        confirm: true,
        expectedRevision: reverted.revision || "",
      }),
    );
    assert.equal(cycledBack.outcome, "updated");
    assert.equal(await rowCount(fixture, "audit_logs", workspaceId), 4);

    const planDrift = await execute(
    connectionString,
    parse({
      workspaceId,
      edition: "enterprise_byoc",
      maxAgents: "30",
      maxActiveEnrollments: "45",
      maxActiveSessionsPerAgent: "9",
      maxMonthlyRuns: "3000",
      maxMonthlyCostUsd: "10000",
    }),
  );
    assertSafeReceipt(planDrift, connectionString);
    assert.equal(planDrift.outcome, "would_update");
    assert.equal(planDrift.required_guard, "expected_revision");
    assert.equal(planDrift.revision, cycledBack.revision);
    assert.equal(await rowCount(fixture, "audit_logs", workspaceId), 4);

  await expectAdministrationError(
    () => execute(
      connectionString,
      parse({
        workspaceId,
        edition: "pro_workspace",
        maxAgents: "12",
        maxActiveEnrollments: "18",
        maxActiveSessionsPerAgent: "4",
        maxMonthlyRuns: "1200",
        maxMonthlyCostUsd: "6000",
        confirm: true,
        expectedRevision: created.revision || "",
      }),
    ),
    "entitlement_revision_stale",
  );
  await expectAdministrationError(
    () => execute(
      connectionString,
      parse({
        workspaceId,
        edition: "pro_workspace",
        maxAgents: "12",
        maxActiveEnrollments: "18",
        maxActiveSessionsPerAgent: "4",
        maxMonthlyRuns: "1200",
        maxMonthlyCostUsd: "6000",
        confirm: true,
        expectAbsent: true,
      }),
    ),
    "entitlement_already_exists",
  );
    assert.equal(await rowCount(fixture, "audit_logs", workspaceId), 4);
  const finalRow = await fixture.query<{
    edition: string;
    max_agents: number;
  }>(
    `SELECT edition,max_agents
    FROM workspace_entitlements WHERE workspace_id=$1`,
    [workspaceId],
  );
  assert.equal(finalRow.rows[0]?.edition, "enterprise_byoc");
  assert.equal(finalRow.rows[0]?.max_agents, 25);
}

async function assertConcurrentSingleWinner(
  connectionString: string,
  fixture: Client,
) {
  const workspaceId = "ws_entitlement_concurrent";
  await seedOperator(fixture, workspaceId);
  const created = await execute(
    connectionString,
    parse({
      workspaceId,
      confirm: true,
      expectAbsent: true,
    }),
  );
  const revision = created.revision || "";
  assert.match(revision, /^[a-f0-9]{64}$/);
  const first = parse({
    workspaceId,
    edition: "enterprise_byoc",
    maxAgents: "31",
    maxActiveEnrollments: "41",
    maxActiveSessionsPerAgent: "7",
    maxMonthlyRuns: "3100",
    maxMonthlyCostUsd: "8100",
    confirm: true,
    expectedRevision: revision,
  });
  const second = parse({
    workspaceId,
    edition: "pro_workspace",
    maxAgents: "32",
    maxActiveEnrollments: "42",
    maxActiveSessionsPerAgent: "8",
    maxMonthlyRuns: "3200",
    maxMonthlyCostUsd: "8200",
    confirm: true,
    expectedRevision: revision,
  });
  const results = await Promise.allSettled([
    execute(connectionString, first),
    execute(connectionString, second),
  ]);
  const winners = results.filter(
    (result): result is PromiseFulfilledResult<
      WorkspaceEntitlementAdministrationReceipt
    > => result.status === "fulfilled",
  );
  const losers = results.filter(
    (result): result is PromiseRejectedResult => result.status === "rejected",
  );
  assert.equal(winners.length, 1);
  assert.equal(losers.length, 1);
  assert.equal(winners[0]?.value.outcome, "updated");
  assert.ok(
    losers[0]?.reason instanceof WorkspaceEntitlementAdministrationError,
  );
  assert.equal(
    (losers[0]?.reason as WorkspaceEntitlementAdministrationError).code,
    "entitlement_revision_stale",
  );
  assert.equal(await rowCount(fixture, "workspace_entitlements", workspaceId), 1);
  assert.equal(await rowCount(fixture, "audit_logs", workspaceId), 2);
  const stored = await fixture.query<{
    edition: string;
    max_agents: number;
  }>(
    `SELECT edition,max_agents
    FROM workspace_entitlements WHERE workspace_id=$1`,
    [workspaceId],
  );
  const winnerConfig = stored.rows[0];
  assert.ok(
    (
      winnerConfig.edition === "enterprise_byoc"
      && winnerConfig.max_agents === 31
    )
    || (
      winnerConfig.edition === "pro_workspace"
      && winnerConfig.max_agents === 32
    ),
  );
}

async function assertOperatorAndAbsentGuards(
  connectionString: string,
  fixture: Client,
) {
  const untrustedWorkspace = "ws_entitlement_untrusted";
  await seedOperator(fixture, untrustedWorkspace, {
    userId: "husr_entitlement_viewer",
    userRole: "viewer",
    membershipRole: "viewer",
  });
  await expectAdministrationError(
    () => execute(
      connectionString,
      parse({
        workspaceId: untrustedWorkspace,
        operatorUserId: "husr_entitlement_viewer",
      }),
    ),
    "trusted_operator_required",
  );
  assert.equal(
    await rowCount(fixture, "workspace_entitlements", untrustedWorkspace),
    0,
  );
  assert.equal(await rowCount(fixture, "audit_logs", untrustedWorkspace), 0);

  const absentWorkspace = "ws_entitlement_absent_revision";
  await seedOperator(fixture, absentWorkspace);
  await expectAdministrationError(
    () => execute(
      connectionString,
      parse({ workspaceId: absentWorkspace }),
      "wrong-operator-password-Aa1!",
    ),
    "trusted_operator_required",
  );
  await expectAdministrationError(
    () => execute(
      connectionString,
      parse({
        workspaceId: absentWorkspace,
        confirm: true,
        expectedRevision: "a".repeat(64),
      }),
    ),
    "entitlement_absent",
  );
  assert.equal(
    await rowCount(fixture, "workspace_entitlements", absentWorkspace),
    0,
  );
  assert.equal(await rowCount(fixture, "audit_logs", absentWorkspace), 0);
}

async function assertDirectCoreValidation(
  connectionString: string,
  fixture: Client,
) {
  const workspaceId = "ws_entitlement_direct_invalid";
  await seedOperator(fixture, workspaceId);
  const valid = parse({ workspaceId });
  const invalid = {
    ...valid,
    configuration: {
      ...valid.configuration,
      maxAgents: Number.NaN,
    },
  } as WorkspaceEntitlementAdministrationRequest;
  await expectAdministrationError(
    () => execute(connectionString, invalid),
    "max_agents_invalid",
  );
  assert.equal(await rowCount(fixture, "workspace_entitlements", workspaceId), 0);
  assert.equal(await rowCount(fixture, "audit_logs", workspaceId), 0);
}

function assertParserValidation() {
  const valid = entitlementArguments();
  expectParserError([...valid, "--unsupported", OPERATOR_CANARY], "unknown_argument");
  expectParserError([...valid, "--edition", "enterprise_byoc"], "duplicate_argument");
  expectParserError(
    replaceArgument(valid, "--max-agents", "-1"),
    "max_agents_invalid",
  );
  expectParserError(
    replaceArgument(valid, "--max-agents", "NaN"),
    "max_agents_invalid",
  );
  expectParserError(
    replaceArgument(valid, "--max-agents", "Infinity"),
    "max_agents_invalid",
  );
  expectParserError(
    replaceArgument(valid, "--max-monthly-cost-usd", "-1"),
    "max_monthly_cost_usd_invalid",
  );
  expectParserError(
    replaceArgument(valid, "--max-monthly-cost-usd", "NaN"),
    "max_monthly_cost_usd_invalid",
  );
  expectParserError(
    replaceArgument(valid, "--max-monthly-cost-usd", "Infinity"),
    "max_monthly_cost_usd_invalid",
  );
  expectParserError(
    replaceArgument(
      valid,
      "--max-monthly-cost-usd",
      "999999999999.999999",
    ),
    "max_monthly_cost_usd_precision_unsafe",
  );
  expectParserError(
    replaceArgument(valid, "--expires-at", "2026-07-24T10:00:00.000Z"),
    "entitlement_window_invalid",
  );
  expectParserError(
    replaceArgument(valid, "--expires-at", "2026-07-24T11:30:00.000Z"),
    "active_entitlement_expired",
  );
  expectParserError(
    replaceArgument(valid, "--effective-at", "2026-07-25T11:00:00.000Z"),
    "active_entitlement_not_effective",
  );
  expectParserError(
    replaceArgument(valid, "--edition", "free_local"),
    "free_local_commercial_active_forbidden",
  );

  let activeNone = replaceArgument(valid, "--capabilities", "none");
  activeNone = replaceArgument(activeNone, "--max-agents", "0");
  activeNone = replaceArgument(
    activeNone,
    "--max-active-enrollments",
    "0",
  );
  activeNone = replaceArgument(
    activeNone,
    "--max-active-sessions-per-agent",
    "0",
  );
  activeNone = replaceArgument(activeNone, "--max-monthly-runs", "0");
  activeNone = replaceArgument(
    activeNone,
    "--max-monthly-cost-usd",
    "0",
  );
  expectParserError(
    activeNone,
    "active_entitlement_capability_required",
  );

  let sessionOnly = replaceArgument(
    valid,
    "--capabilities",
    "session_issue,run_start",
  );
  sessionOnly = replaceArgument(sessionOnly, "--max-agents", "0");
  sessionOnly = replaceArgument(
    sessionOnly,
    "--max-active-enrollments",
    "0",
  );
  expectParserError(
    sessionOnly,
    "session_capability_requires_enrollment",
  );

  expectParserError(
    replaceArgument(
      valid,
      "--capabilities",
      "enrollment_issue,session_issue",
    ),
    "disabled_run_quota_nonzero",
  );
  expectParserError(
    replaceArgument(
      valid,
      "--capabilities",
      "enrollment_issue,run_start",
    ),
    "disabled_session_quota_nonzero",
  );
  expectParserError(
    replaceArgument(valid, "--capabilities", "run_start"),
    "disabled_enrollment_quota_nonzero",
  );
  expectParserError(
    [...valid, "--confirm"],
    "confirm_guard_required",
  );
  expectParserError(
    [
      ...valid,
      "--confirm",
      "--expect-absent",
      "--expected-revision",
      "a".repeat(64),
    ],
    "optimistic_guard_ambiguous",
  );
  expectParserError(
    [...valid, "--expected-revision", "not-a-revision"],
    "expected_revision_invalid",
  );
  expectParserError(
    removeArgument(valid, "--max-monthly-runs"),
    "required_argument_missing",
  );
  expectParserError(
    replaceArgument(valid, "--expires-at", "not-a-time"),
    "expires_at_invalid",
  );
  expectParserError(
    replaceArgument(valid, "--expires-at", "2027-02-30T12:00:00Z"),
    "expires_at_invalid",
  );
  expectParserError(
    replaceArgument(
      replaceArgument(valid, "--status", "suspended"),
      "--expires-at",
      "2026-07-24T11:30:00.000Z",
    ),
    "entitlement_status_window_mismatch",
  );
}

async function assertStaticBoundary() {
  const source = await readFile(
    new URL("./configure-workspace-entitlement.ts", import.meta.url),
    "utf8",
  );
  assert.match(source, /postgresDsn\(\)/);
  assert.match(source, /runPostgresSchemaCommand\("check"\)/);
  assert.match(source, /else await client\.query\("ROLLBACK"\)/);
  assert.match(source, /pg_advisory_xact_lock/);
  assert.match(source, /FOR UPDATE OF m,credential/);
  assert.match(source, /AGENTOPS_ENTITLEMENT_OPERATOR_PASSWORD/);
  assert.match(source, /appendAudit/);
  assert.match(source, /updated_by_user_id/);
  assert.match(source, /expected_revision/);
  assert.match(source, /expect_absent/);
  assert.doesNotMatch(source, /DATABASE_URL/);
  assert.doesNotMatch(source, /UPDATE\s+audit_logs|DELETE\s+FROM\s+audit_logs/i);
  assert.doesNotMatch(
    source,
    /from\s+["']node:(?:child_process|http|https|net|tls)["']/,
  );
  assert.doesNotMatch(source, /\bfetch\s*\(/);
  assert.doesNotMatch(source, /from\s+["'][^"']*sqlite[^"']*["']/i);
  assert.doesNotMatch(source, /\.py\b/i);
}

async function run() {
  const baseDsn = String(process.env.AGENTOPS_POSTGRES_DSN || "").trim();
  assert.ok(baseDsn, "AGENTOPS_POSTGRES_DSN is required");
  const schema =
    `workspace_entitlement_admin_${randomUUID().replaceAll("-", "")}`;
  const admin = new Client({
    connectionString: baseDsn,
    application_name: "agentops-entitlement-admin-contract-schema",
  });
  let schemaCreated = false;
  const originalFetch = globalThis.fetch;
  let externalNetworkCalls = 0;
  globalThis.fetch = async () => {
    externalNetworkCalls += 1;
    throw new Error("External network access is forbidden in this contract.");
  };

  try {
    assertParserValidation();
    await assertStaticBoundary();
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
    assert.equal(
      migration.applied_count,
      POSTGRES_MIGRATION_MANIFEST.length,
    );
    assert.equal(POSTGRES_MIGRATION_MANIFEST.length, 10);
    await runPostgresSchemaCommand("check", { connectionString });

    const fixture = new Client({
      connectionString,
      application_name: "agentops-entitlement-admin-contract-fixture",
    });
    await fixture.connect();
    try {
      await assertPlanOnlyZeroWrite(connectionString, fixture);
      await assertCreateUpdateAndReplay(connectionString, fixture);
      await assertConcurrentSingleWinner(connectionString, fixture);
      await assertOperatorAndAbsentGuards(connectionString, fixture);
      await assertDirectCoreValidation(connectionString, fixture);
      assert.equal(externalNetworkCalls, 0);
    } finally {
      await fixture.end();
    }

    const safeResult = {
      contract:
        "agentops_workspace_entitlement_administration_postgres_contract_v1",
      ok: true,
      postgres_major: 16,
      schema_contract: SCHEMA_CONTRACT,
      migration_count: POSTGRES_MIGRATION_MANIFEST.length,
      plan_only_zero_write: true,
      confirm_create_update: true,
      idempotent_replay_unchanged: true,
      concurrent_single_winner: true,
      stale_expected_rejected: true,
      invalid_combinations_rejected: true,
      trusted_operator_enforced: true,
      append_only_audit_verified: true,
      secret_omission_verified: true,
      external_network_calls: externalNetworkCalls,
      python_used: false,
      sqlite_used: false,
      credentials_omitted: true,
      dsn_omitted: true,
      raw_config_omitted: true,
    };
    const serialized = JSON.stringify(safeResult);
    assert.equal(serialized.includes(baseDsn), false);
    assert.equal(serialized.includes("postgresql://"), false);
    assert.equal(serialized.includes(OPERATOR_CANARY), false);
    console.log(serialized);
  } finally {
    globalThis.fetch = originalFetch;
    if (schemaCreated) {
      await admin.query(
        `DROP SCHEMA IF EXISTS ${quotedSchema(schema)} CASCADE`,
      );
    }
    await admin.end().catch(() => undefined);
  }
}

run().catch((error: unknown) => {
  console.log(JSON.stringify({
    contract:
      "agentops_workspace_entitlement_administration_postgres_contract_v1",
    ok: false,
    error_code: error instanceof WorkspaceEntitlementAdministrationError
      ? error.code
      : "contract_failed",
    credentials_omitted: true,
    dsn_omitted: true,
    raw_config_omitted: true,
    python_used: false,
    sqlite_used: false,
  }));
  process.exitCode = 1;
});
