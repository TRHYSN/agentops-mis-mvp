import assert from "node:assert/strict";
import {
  createHash,
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
  createPostgresRoleBoundaryFixture,
} from "./postgres-role-boundary-test-helper";
import {
  POSTGRES_MIGRATION_MANIFEST,
  runPostgresSchemaCommand,
  SCHEMA_CONTRACT,
} from "../src/server/controlPlane/schemaReadiness";
import { HUMAN_SCRYPT_PARAMS } from "../src/server/controlPlane/humanPasswordPolicy";
import {
  issueWorkspaceEntitlementAdminChallengeWithClient,
  parseWorkspaceEntitlementAdminChallengeRequest,
  WORKSPACE_ENTITLEMENT_ADMINISTRATION_REQUEST_CONTRACT,
  type CanonicalWorkspaceEntitlementAdminChallengeRequest,
} from "../src/server/controlPlane/workspaceEntitlementAdminChallenges";

const CONTRACT_NOW = new Date();
const EFFECTIVE_AT = new Date(
  CONTRACT_NOW.getTime() - 60 * 60 * 1000,
).toISOString();
const EXPIRES_AT = new Date(
  CONTRACT_NOW.getTime() + 365 * 24 * 60 * 60 * 1000,
).toISOString();
const OPERATOR_ID = "husr_entitlement_operator";
const OPERATOR_PASSWORD = `${randomBytes(24).toString("base64url")}Aa1!`;
let contractStage = "initialize";

type ArgumentOverrides = Readonly<{
  workspaceId?: string;
  operatorUserId?: string;
  edition?: string;
  status?: string;
  capabilities?: string;
  maxAgents?: string;
  maxActiveEnrollments?: string;
  maxActiveSessionsPerAgent?: string;
  maxConcurrentRuns?: string;
  maxMonthlyRuns?: string;
  maxMonthlyCostUsd?: string;
  effectiveAt?: string;
  expiresAt?: string;
  confirm?: boolean;
  expectAbsent?: boolean;
  expectedRevision?: string;
}>;

type AdministrationChallenge = Readonly<{
  challengeId: string;
  challengeToken: string;
  request: CanonicalWorkspaceEntitlementAdminChallengeRequest;
}>;

type ContractContext = Readonly<{
  applicationSchema: string;
  runtimeApiSchema: string;
  runtimeDsn: string;
  entitlementAdminDsn: string;
  owner: Client;
}>;

function quotedIdentifier(value: string) {
  assert.match(value, /^[A-Za-z_][A-Za-z0-9_]{0,62}$/);
  return `"${value}"`;
}

function qualified(schema: string, object: string) {
  return `${quotedIdentifier(schema)}.${quotedIdentifier(object)}`;
}

function sha256(value: string) {
  return createHash("sha256").update(value, "utf8").digest("hex");
}

function entitlementArguments(overrides: ArgumentOverrides = {}) {
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
    "--max-concurrent-runs",
    overrides.maxConcurrentRuns || "4",
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
    CONTRACT_NOW,
  );
}

function canonicalRequest(
  request: WorkspaceEntitlementAdministrationRequest,
) {
  return parseWorkspaceEntitlementAdminChallengeRequest(
    request.workspaceId,
    {
      contract: WORKSPACE_ENTITLEMENT_ADMINISTRATION_REQUEST_CONTRACT,
      workspace_id: request.workspaceId,
      operator_user_id: request.operatorUserId,
      mode: request.confirm ? "confirm" : "plan",
      guard: request.guard,
      configuration: {
        edition: request.configuration.edition,
        status: request.configuration.status,
        capabilities: request.configuration.capabilities,
        max_agents: request.configuration.maxAgents,
        max_active_enrollments:
          request.configuration.maxActiveEnrollments,
        max_active_sessions_per_agent:
          request.configuration.maxActiveSessionsPerAgent,
        max_concurrent_runs: request.configuration.maxConcurrentRuns,
        max_monthly_runs: request.configuration.maxMonthlyRuns,
        max_monthly_cost_usd: request.configuration.maxMonthlyCostUsd,
        effective_at: request.configuration.effectiveAt.toISOString(),
        expires_at:
          request.configuration.expiresAt?.toISOString() || null,
      },
    },
  );
}

async function seedOperator(
  context: ContractContext,
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
  const app = context.applicationSchema;
  await context.owner.query(
    `INSERT INTO ${qualified(app, "users")}(
       user_id,name,email,role,created_at
     ) VALUES($1,$2,$3,$4,$5)
     ON CONFLICT(user_id) DO UPDATE SET role=EXCLUDED.role`,
    [
      userId,
      "Trusted Entitlement Operator",
      `${userId}@operator.invalid`,
      userRole,
      CONTRACT_NOW.toISOString(),
    ],
  );
  await context.owner.query(
    `INSERT INTO ${qualified(app, "workspace_memberships")}(
       workspace_id,user_id,role,status,created_at,updated_at
     ) VALUES($1,$2,$3,$4,$5,$5)
     ON CONFLICT(workspace_id,user_id) DO UPDATE SET
       role=EXCLUDED.role,
       status=EXCLUDED.status,
       updated_at=EXCLUDED.updated_at`,
    [
      workspaceId,
      userId,
      membershipRole,
      membershipStatus,
      CONTRACT_NOW.toISOString(),
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
  await context.owner.query(
    `INSERT INTO ${qualified(app, "human_login_credentials")}(
       credential_id,user_id,username,password_hash,password_salt,
       password_params_json,status,created_at,updated_at,last_login_at
     ) VALUES($1,$2,$3,$4,$5,$6,'active',$7,$7,NULL)
     ON CONFLICT(credential_id) DO UPDATE SET
       status='active',
       updated_at=EXCLUDED.updated_at`,
    [
      `credential_${userId}`,
      userId,
      `entitlement-${userId}`,
      passwordHash,
      salt.toString("hex"),
      JSON.stringify(HUMAN_SCRYPT_PARAMS),
      CONTRACT_NOW.toISOString(),
    ],
  );
}

async function createControlledHumanSession(
  context: ContractContext,
  request: WorkspaceEntitlementAdministrationRequest,
) {
  const sessionId = `hsess_ent_${randomUUID().replaceAll("-", "")}`;
  const createdAt = new Date();
  const expiresAt = new Date(createdAt.getTime() + 10 * 60 * 1000);
  await context.owner.query(
    `INSERT INTO ${qualified(context.applicationSchema, "human_sessions")}(
       session_id,user_id,session_hash,status,created_at,expires_at,
       last_seen_at,revoked_at
     ) VALUES($1,$2,$3,'active',$4,$5,$4,NULL)`,
    [
      sessionId,
      request.operatorUserId,
      sha256(`${sessionId}:${randomBytes(32).toString("hex")}`),
      createdAt.toISOString(),
      expiresAt.toISOString(),
    ],
  );
  return {
    mode: "human_session" as const,
    sessionId,
    sessionRef: `session_ref_${sha256(sessionId).slice(0, 16)}`,
    userId: request.operatorUserId,
    userName: "Trusted Entitlement Operator",
    workspaceId: request.workspaceId,
    membershipRole: "operator",
  };
}

async function issueChallenge(
  context: ContractContext,
  request: WorkspaceEntitlementAdministrationRequest,
) {
  const canonical = canonicalRequest(request);
  const identity = await createControlledHumanSession(context, request);
  const runtime = new Client({
    connectionString: context.runtimeDsn,
    application_name: "agentops-entitlement-challenge-runtime-contract",
  });
  await runtime.connect();
  try {
    const issued = await issueWorkspaceEntitlementAdminChallengeWithClient(
      runtime,
      identity,
      canonical,
      { runtimeApiSchema: context.runtimeApiSchema },
    );
    assert.equal(issued.status, 201);
    assert.equal(issued.body.single_use, true);
    assert.equal(issued.body.human_session_consumed, true);
    assert.equal(issued.body.runtime_plan_apply_allowed, false);
    return Object.freeze({
      challengeId: issued.body.challenge_id,
      challengeToken: issued.body.challenge_token,
      request: canonical,
    });
  } finally {
    await runtime.end();
  }
}

async function executeWithChallenge(
  context: ContractContext,
  request: WorkspaceEntitlementAdministrationRequest,
  challenge: AdministrationChallenge,
) {
  const admin = new Client({
    connectionString: context.entitlementAdminDsn,
    application_name: "agentops-entitlement-admin-contract",
  });
  await admin.connect();
  try {
    return await executeWorkspaceEntitlementAdministration(
      admin,
      request,
      {
        challenge,
        runtimeApiSchema: context.runtimeApiSchema,
      },
    );
  } finally {
    await admin.end();
  }
}

async function executeWithFreshChallenge(
  context: ContractContext,
  request: WorkspaceEntitlementAdministrationRequest,
) {
  const challenge = await issueChallenge(context, request);
  const receipt = await executeWithChallenge(context, request, challenge);
  return { challenge, receipt };
}

async function expectSqlState(
  work: () => Promise<unknown>,
  code: string,
) {
  await assert.rejects(
    work,
    (error: unknown) => (error as { code?: string }).code === code,
  );
}

async function expectAdministrationCode(
  work: () => Promise<unknown>,
  code: string,
) {
  await assert.rejects(
    work,
    (error: unknown) => (
      error instanceof WorkspaceEntitlementAdministrationError
      && error.code === code
    ),
  );
}

function expectParserError(argumentsList: string[], code: string) {
  assert.throws(
    () => parseWorkspaceEntitlementArguments(argumentsList, CONTRACT_NOW),
    (error: unknown) => (
      error instanceof WorkspaceEntitlementAdministrationError
      && error.code === code
    ),
  );
}

async function rowCount(
  context: ContractContext,
  table: "workspace_entitlements" | "audit_logs",
  workspaceId: string,
) {
  const result = await context.owner.query<{ count: string }>(
    `SELECT COUNT(*)::text AS count
     FROM ${qualified(context.applicationSchema, table)}
     WHERE workspace_id=$1`,
    [workspaceId],
  );
  return Number(result.rows[0]?.count || 0);
}

function assertSafeReceipt(
  receipt: WorkspaceEntitlementAdministrationReceipt,
  context: ContractContext,
) {
  assert.equal(
    receipt.contract,
    WORKSPACE_ENTITLEMENT_ADMINISTRATION_CONTRACT,
  );
  assert.equal(receipt.ok, true);
  assert.equal(receipt.challenge_consumed, true);
  assert.equal(receipt.human_session_consumed, true);
  assert.equal(receipt.challenge_token_omitted, true);
  assert.equal(receipt.credentials_omitted, true);
  assert.equal(receipt.dsn_omitted, true);
  assert.equal(receipt.raw_config_omitted, true);
  assert.equal(receipt.python_started, false);
  assert.equal(receipt.sqlite_used, false);
  assert.match(receipt.desired_config_hash, /^[a-f0-9]{64}$/);
  const serialized = JSON.stringify(receipt);
  assert.equal(serialized.includes(context.entitlementAdminDsn), false);
  assert.equal(serialized.includes(context.runtimeDsn), false);
  assert.equal(serialized.includes("postgresql://"), false);
  assert.equal(serialized.includes(OPERATOR_ID), false);
  assert.equal(serialized.includes("enrollment_issue"), false);
  assert.equal(serialized.includes("max_monthly_cost_usd"), false);
}

async function challengeConsumption(
  context: ContractContext,
  challengeId: string,
) {
  const result = await context.owner.query<{
    consumed: boolean;
    consumed_action: string | null;
  }>(
    `SELECT
       consumed_at IS NOT NULL AS consumed,
       consumed_action
     FROM ${qualified(
       context.applicationSchema,
       "entitlement_admin_challenges",
     )}
     WHERE challenge_id=$1`,
    [challengeId],
  );
  assert.equal(result.rowCount, 1);
  return result.rows[0];
}

async function assertPlanCommitsAndConsumes(
  context: ContractContext,
) {
  const workspaceId = "ws_entitlement_plan";
  await seedOperator(context, workspaceId);
  const request = parse({ workspaceId });
  const challenge = await issueChallenge(context, request);
  assert.deepEqual(await challengeConsumption(context, challenge.challengeId), {
    consumed: false,
    consumed_action: null,
  });
  const planned = await executeWithChallenge(context, request, challenge);
  assertSafeReceipt(planned, context);
  assert.equal(planned.mode, "plan");
  assert.equal(planned.outcome, "would_create");
  assert.equal(planned.required_guard, "expect_absent");
  assert.equal(planned.audit_appended, false);
  assert.deepEqual(await challengeConsumption(context, challenge.challengeId), {
    consumed: true,
    consumed_action: "plan",
  });
  assert.equal(await rowCount(context, "workspace_entitlements", workspaceId), 0);
  assert.equal(await rowCount(context, "audit_logs", workspaceId), 0);
  await expectSqlState(
    () => executeWithChallenge(context, request, challenge),
    "55000",
  );
}

async function assertExpiredAndTamperedChallenges(
  context: ContractContext,
) {
  const workspaceId = "ws_entitlement_challenge_guards";
  await seedOperator(context, workspaceId);

  const expiredRequest = parse({ workspaceId });
  const expired = await issueChallenge(context, expiredRequest);
  await context.owner.query(
    `UPDATE ${qualified(
      context.applicationSchema,
      "entitlement_admin_challenges",
    )}
     SET issued_at=clock_timestamp()-INTERVAL '2 seconds',
         expires_at=clock_timestamp()-INTERVAL '1 second'
     WHERE challenge_id=$1`,
    [expired.challengeId],
  );
  await expectSqlState(
    () => executeWithChallenge(context, expiredRequest, expired),
    "55000",
  );

  const originalRequest = parse({ workspaceId });
  const original = await issueChallenge(context, originalRequest);
  const tamperedRequest = parse({ workspaceId, maxAgents: "11" });
  const forgedBinding = Object.freeze({
    ...original,
    request: canonicalRequest(tamperedRequest),
  });
  await expectSqlState(
    () => executeWithChallenge(
      context,
      tamperedRequest,
      forgedBinding,
    ),
    "42501",
  );
  const untampered = await executeWithChallenge(
    context,
    originalRequest,
    original,
  );
  assert.equal(untampered.mode, "plan");
}

async function assertWrongDatabaseRoles(
  context: ContractContext,
) {
  const workspaceId = "ws_entitlement_wrong_role";
  await seedOperator(context, workspaceId);
  const request = parse({ workspaceId });
  const challenge = await issueChallenge(context, request);
  const args = [
    challenge.challengeId,
    challenge.challengeToken,
    JSON.stringify(challenge.request),
  ];
  const plan = qualified(
    context.runtimeApiSchema,
    "agentops_plan_workspace_entitlement_v11",
  );
  const runtime = new Client({
    connectionString: context.runtimeDsn,
    application_name: "agentops-entitlement-wrong-runtime-role-contract",
  });
  await runtime.connect();
  try {
    await expectSqlState(
      () => runtime.query(
        `SELECT ${plan}($1::text,$2::text,$3::jsonb)`,
        args,
      ),
      "42501",
    );
  } finally {
    await runtime.end();
  }

  await expectSqlState(
    () => context.owner.query(
      `SELECT ${plan}($1::text,$2::text,$3::jsonb)`,
      args,
    ),
    "42501",
  );
  const administered = await executeWithChallenge(
    context,
    request,
    challenge,
  );
  assert.equal(administered.mode, "plan");
}

async function assertCreateUpdateAuditAndFreshReplay(
  context: ContractContext,
) {
  const workspaceId = "ws_entitlement_create_update";
  await seedOperator(context, workspaceId);
  const createRequest = parse({
    workspaceId,
    confirm: true,
    expectAbsent: true,
  });
  const created = (
    await executeWithFreshChallenge(context, createRequest)
  ).receipt;
  assertSafeReceipt(created, context);
  assert.equal(created.mode, "confirmed");
  assert.equal(created.outcome, "created");
  assert.equal(created.audit_appended, true);
  assert.match(created.revision || "", /^[a-f0-9]{64}$/);

  const duplicateCreateChallenge = await issueChallenge(context, createRequest);
  await expectAdministrationCode(
    () => executeWithChallenge(
      context,
      createRequest,
      duplicateCreateChallenge,
    ),
    "entitlement_already_exists",
  );
  assert.deepEqual(
    await challengeConsumption(
      context,
      duplicateCreateChallenge.challengeId,
    ),
    { consumed: true, consumed_action: "apply" },
  );

  const unchangedRequest = parse({
    workspaceId,
    confirm: true,
    expectedRevision: created.revision || "",
  });
  const unchanged = (
    await executeWithFreshChallenge(context, unchangedRequest)
  ).receipt;
  assert.equal(unchanged.outcome, "unchanged");
  assert.equal(unchanged.audit_appended, false);
  assert.equal(unchanged.revision, created.revision);

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
  const updated = (
    await executeWithFreshChallenge(context, updateRequest)
  ).receipt;
  assertSafeReceipt(updated, context);
  assert.equal(updated.outcome, "updated");
  assert.equal(updated.audit_appended, true);
  assert.notEqual(updated.revision, created.revision);
  assert.equal(await rowCount(context, "workspace_entitlements", workspaceId), 1);
  assert.equal(await rowCount(context, "audit_logs", workspaceId), 2);

  const staleRequest = parse({
    workspaceId,
    edition: "pro_workspace",
    maxAgents: "12",
    maxActiveEnrollments: "18",
    maxActiveSessionsPerAgent: "4",
    maxMonthlyRuns: "1200",
    maxMonthlyCostUsd: "6000",
    confirm: true,
    expectedRevision: created.revision || "",
  });
  const staleChallenge = await issueChallenge(context, staleRequest);
  await expectAdministrationCode(
    () => executeWithChallenge(context, staleRequest, staleChallenge),
    "entitlement_revision_stale",
  );
  assert.deepEqual(
    await challengeConsumption(context, staleChallenge.challengeId),
    { consumed: true, consumed_action: "apply" },
  );
  assert.equal(await rowCount(context, "audit_logs", workspaceId), 2);

  const stored = await context.owner.query<{
    edition: string;
    max_agents: number;
    max_monthly_cost_usd: string;
    updated_by_user_id: string;
  }>(
    `SELECT
       edition,
       max_agents,
       max_monthly_cost_usd::text AS max_monthly_cost_usd,
       updated_by_user_id
     FROM ${qualified(context.applicationSchema, "workspace_entitlements")}
     WHERE workspace_id=$1`,
    [workspaceId],
  );
  assert.equal(stored.rows[0]?.edition, "enterprise_byoc");
  assert.equal(stored.rows[0]?.max_agents, 25);
  assert.equal(stored.rows[0]?.max_monthly_cost_usd, "9000.500000");
  assert.equal(stored.rows[0]?.updated_by_user_id, OPERATOR_ID);

  const audits = await context.owner.query<{
    action: string;
    actor_id: string;
    before_hash: string | null;
    after_hash: string | null;
    tamper_chain_hash: string;
    metadata_json: string;
  }>(
    `SELECT
       action,actor_id,before_hash,after_hash,tamper_chain_hash,metadata_json
     FROM ${qualified(context.applicationSchema, "audit_logs")}
     WHERE workspace_id=$1
     ORDER BY created_at,audit_id`,
    [workspaceId],
  );
  assert.equal(audits.rowCount, 2);
  assert.equal(audits.rows[0]?.action, "workspace_entitlement.created");
  assert.equal(audits.rows[1]?.action, "workspace_entitlement.updated");
  assert.equal(audits.rows[0]?.actor_id, OPERATOR_ID);
  assert.equal(audits.rows[0]?.before_hash, null);
  for (const audit of audits.rows) {
    assert.match(audit.after_hash || "", /^[a-f0-9]{64}$/);
    assert.match(audit.tamper_chain_hash, /^[a-f0-9]{64}$/);
    const metadata = JSON.parse(audit.metadata_json) as Record<string, unknown>;
    assert.equal(metadata.raw_config_omitted, true);
    assert.equal(metadata.credentials_omitted, true);
    assert.equal(metadata.dsn_omitted, true);
    assert.equal(
      JSON.stringify(metadata).includes("max_monthly_cost_usd"),
      false,
    );
  }
}

async function assertConcurrentRevisionSingleWinner(
  context: ContractContext,
) {
  const workspaceId = "ws_entitlement_concurrent";
  await seedOperator(context, workspaceId);
  const created = (
    await executeWithFreshChallenge(
      context,
      parse({ workspaceId, confirm: true, expectAbsent: true }),
    )
  ).receipt;
  const revision = created.revision || "";
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
  const [firstChallenge, secondChallenge] = await Promise.all([
    issueChallenge(context, first),
    issueChallenge(context, second),
  ]);
  const results = await Promise.allSettled([
    executeWithChallenge(context, first, firstChallenge),
    executeWithChallenge(context, second, secondChallenge),
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
  assert.equal(await rowCount(context, "workspace_entitlements", workspaceId), 1);
  assert.equal(await rowCount(context, "audit_logs", workspaceId), 2);
  assert.equal(
    (await challengeConsumption(context, firstChallenge.challengeId)).consumed,
    true,
  );
  assert.equal(
    (await challengeConsumption(context, secondChallenge.challengeId)).consumed,
    true,
  );
}

async function assertRoleCapabilitySplit(
  context: ContractContext,
) {
  const workspaceId = "ws_entitlement_role_split";
  await seedOperator(context, workspaceId);
  const request = parse({ workspaceId });
  const canonical = canonicalRequest(request);
  const issueFunction = qualified(
    context.runtimeApiSchema,
    "agentops_issue_workspace_entitlement_admin_challenge_v11",
  );
  const planFunction = qualified(
    context.runtimeApiSchema,
    "agentops_plan_workspace_entitlement_v11",
  );
  const applyFunction = qualified(
    context.runtimeApiSchema,
    "agentops_apply_workspace_entitlement_v11",
  );
  const admin = new Client({
    connectionString: context.entitlementAdminDsn,
    application_name: "agentops-entitlement-admin-capability-contract",
  });
  const runtime = new Client({
    connectionString: context.runtimeDsn,
    application_name: "agentops-entitlement-runtime-capability-contract",
  });
  await Promise.all([admin.connect(), runtime.connect()]);
  try {
    await expectSqlState(
      () => admin.query(
        `SELECT ${issueFunction}(
           $1::text,$2::text,$3::text,$4::text,$5::jsonb,$6::text,$7::interval
         )`,
        [
          "hsess_forbidden",
          workspaceId,
          OPERATOR_ID,
          "plan",
          JSON.stringify(canonical),
          "a".repeat(64),
          "75 seconds",
        ],
      ),
      "42501",
    );
    for (const functionName of [planFunction, applyFunction]) {
      await expectSqlState(
        () => runtime.query(
          `SELECT ${functionName}($1::text,$2::text,$3::jsonb)`,
          ["entc_forbidden", "forbidden-token", JSON.stringify(canonical)],
        ),
        "42501",
      );
    }
  } finally {
    await Promise.all([
      admin.end().catch(() => undefined),
      runtime.end().catch(() => undefined),
    ]);
  }
}

async function assertAdminApplicationRelationMatrix(
  context: ContractContext,
) {
  const tables = await context.owner.query<{
    table_name: string;
    first_column: string;
  }>(
    `SELECT
       table_row.tablename AS table_name,
       (
         SELECT attribute.attname
         FROM pg_catalog.pg_class relation
         JOIN pg_catalog.pg_namespace namespace_row
           ON namespace_row.oid=relation.relnamespace
         JOIN pg_catalog.pg_attribute attribute
           ON attribute.attrelid=relation.oid
          AND attribute.attnum>0
          AND NOT attribute.attisdropped
         WHERE namespace_row.nspname=$1
           AND relation.relname=table_row.tablename
         ORDER BY attribute.attnum
         LIMIT 1
       ) AS first_column
     FROM pg_catalog.pg_tables table_row
     WHERE table_row.schemaname=$1
     ORDER BY table_row.tablename`,
    [context.applicationSchema],
  );
  assert.ok(tables.rowCount && tables.rowCount > 0);
  assert.ok(tables.rows.some((row) => (
    row.table_name === "human_login_credentials"
  )));
  assert.ok(tables.rows.some((row) => (
    row.table_name === "workspace_entitlements"
  )));
  assert.ok(tables.rows.some((row) => row.table_name === "audit_logs"));
  assert.ok(tables.rows.some((row) => (
    row.table_name === "agentops_schema_migrations"
  )));

  const admin = new Client({
    connectionString: context.entitlementAdminDsn,
    application_name: "agentops-entitlement-admin-relation-matrix-contract",
  });
  await admin.connect();
  try {
    for (const table of tables.rows) {
      const relation = qualified(context.applicationSchema, table.table_name);
      const column = quotedIdentifier(table.first_column);
      for (const statement of [
        `SELECT * FROM ${relation} LIMIT 0`,
        `INSERT INTO ${relation} DEFAULT VALUES`,
        `UPDATE ${relation} SET ${column}=${column} WHERE false`,
        `DELETE FROM ${relation} WHERE false`,
        `TRUNCATE TABLE ${relation}`,
        `ALTER TABLE ${relation} ADD COLUMN __agentops_forbidden_probe TEXT`,
      ]) {
        await expectSqlState(() => admin.query(statement), "42501");
      }
    }
    await expectSqlState(
      () => admin.query(
        `CREATE TABLE ${qualified(
          context.applicationSchema,
          "__agentops_forbidden_table",
        )}(id INTEGER)`,
      ),
      "42501",
    );
  } finally {
    await admin.end();
  }
}

function assertParserValidation() {
  const valid = entitlementArguments();
  expectParserError([...valid, "--unsupported", "private"], "unknown_argument");
  expectParserError([...valid, "--edition", "enterprise_byoc"], "duplicate_argument");
  expectParserError(
    replaceArgument(valid, "--max-agents", "-1"),
    "max_agents_invalid",
  );
  expectParserError(
    replaceArgument(valid, "--max-monthly-cost-usd", "NaN"),
    "max_monthly_cost_usd_invalid",
  );
  expectParserError(
    replaceArgument(valid, "--edition", "free_local"),
    "free_local_commercial_active_forbidden",
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
    removeArgument(valid, "--max-monthly-runs"),
    "required_argument_missing",
  );
}

async function assertStaticV11Boundary() {
  const source = await readFile(
    new URL("./configure-workspace-entitlement.ts", import.meta.url),
    "utf8",
  );
  assert.match(source, /issueAdministrationChallenge/);
  assert.match(source, /agentops_plan_workspace_entitlement_v11/);
  assert.match(source, /agentops_apply_workspace_entitlement_v11/);
  assert.match(source, /await client\.query\("COMMIT"\)/);
  assert.doesNotMatch(
    source,
    /\b(?:FROM|INTO|UPDATE|DELETE\s+FROM)\s+(?:"[^"]+"\.)?"?(?:human_login_credentials|workspace_entitlements|audit_logs|agentops_schema_migrations)"?/i,
  );
  assert.doesNotMatch(source, /from\s+["'][^"']*sqlite[^"']*["']/i);
  assert.doesNotMatch(source, /\.py\b/i);
}

async function run() {
  const baseDsn = String(process.env.AGENTOPS_POSTGRES_DSN || "").trim();
  assert.ok(baseDsn, "AGENTOPS_POSTGRES_DSN is required");
  contractStage = "postgres_fixture_migrate";
  const fixture = await createPostgresRoleBoundaryFixture(
    baseDsn,
    "entitlement_admin_v11",
  );
  const context: ContractContext = {
    applicationSchema: fixture.applicationSchema,
    runtimeApiSchema: fixture.runtimeApiSchema,
    runtimeDsn: fixture.runtimeDsn,
    entitlementAdminDsn: fixture.entitlementAdminDsn,
    owner: fixture.owner,
  };
  try {
    contractStage = "parser_validation";
    assertParserValidation();
    contractStage = "static_v11_boundary";
    await assertStaticV11Boundary();
    contractStage = "postgres_bootstrap";
    const version = await context.owner.query<{ server_version: string }>(
      "SHOW server_version",
    );
    assert.match(version.rows[0]?.server_version || "", /^16\./);
    assert.equal(fixture.migration.schema_contract, SCHEMA_CONTRACT);
    assert.equal(
      fixture.migration.applied_count,
      POSTGRES_MIGRATION_MANIFEST.length,
    );
    await runPostgresSchemaCommand("check", {
      connectionString: fixture.ownerDsn,
      applicationSchema: fixture.applicationSchema,
      runtimeApiSchema: fixture.runtimeApiSchema,
      enforceRuntimeBoundary: false,
    });

    contractStage = "admin_application_relation_matrix";
    await assertAdminApplicationRelationMatrix(context);
    contractStage = "role_capability_split";
    await assertRoleCapabilitySplit(context);
    contractStage = "plan_commit_and_consume";
    await assertPlanCommitsAndConsumes(context);
    contractStage = "expired_and_tampered_challenges";
    await assertExpiredAndTamperedChallenges(context);
    contractStage = "wrong_database_roles";
    await assertWrongDatabaseRoles(context);
    contractStage = "create_update_audit";
    await assertCreateUpdateAuditAndFreshReplay(context);
    contractStage = "concurrent_revision_single_winner";
    await assertConcurrentRevisionSingleWinner(context);

    const safeResult = {
      contract:
        "agentops_workspace_entitlement_administration_postgres_contract_v11",
      ok: true,
      postgres_major: 16,
      schema_contract: SCHEMA_CONTRACT,
      migration_count: POSTGRES_MIGRATION_MANIFEST.length,
      runtime_issues_single_use_challenges: true,
      admin_plan_apply_only: true,
      admin_issue_forbidden: true,
      runtime_plan_apply_forbidden: true,
      admin_all_application_relation_access_forbidden: true,
      admin_application_ddl_forbidden: true,
      plan_commits_and_consumes_challenge: true,
      replay_rejected: true,
      expired_challenge_rejected: true,
      tampered_request_rejected: true,
      wrong_database_role_rejected: true,
      concurrent_revision_single_winner: true,
      audit_append_verified: true,
      http_end_to_end_delegated_to_independent_challenge_contract: true,
      mock_product_claimed: false,
      python_used: false,
      sqlite_used: false,
      credentials_omitted: true,
      dsn_omitted: true,
      raw_config_omitted: true,
    };
    const serialized = JSON.stringify(safeResult);
    assert.equal(serialized.includes(baseDsn), false);
    assert.equal(serialized.includes("postgresql://"), false);
    assert.equal(serialized.includes(OPERATOR_PASSWORD), false);
    console.log(serialized);
  } finally {
    await fixture.cleanup();
  }
}

run().catch((error: unknown) => {
  const errorCode = error instanceof WorkspaceEntitlementAdministrationError
    ? error.code
    : error instanceof Error && /^[a-z0-9_]{1,96}$/.test(error.message)
      ? error.message
      : "contract_failed";
  console.log(JSON.stringify({
    contract:
      "agentops_workspace_entitlement_administration_postgres_contract_v11",
    ok: false,
    error_code: errorCode,
    stage: contractStage,
    credentials_omitted: true,
    dsn_omitted: true,
    raw_config_omitted: true,
    python_used: false,
    sqlite_used: false,
  }));
  process.exitCode = 1;
});
