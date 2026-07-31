import assert from "node:assert/strict";
import {
  createHash,
  randomBytes,
  randomUUID,
  scryptSync,
} from "node:crypto";
import { readFile } from "node:fs/promises";

import { NextRequest } from "next/server";
import { Client } from "pg";

import { POST as issueRoute } from "../app/api/mis/agent-gateway/enrollment/issue-approved/route";
import { POST as requestRoute } from "../app/api/mis/agent-gateway/enrollment/request/route";
import { POST as decisionRoute } from "../app/api/mis/approvals/[approvalId]/[decision]/route";
import { closeControlPlanePoolForTests } from "../src/server/controlPlane/db";
import { establishHumanSession } from "../src/server/controlPlane/humanSession";
import { HUMAN_SCRYPT_PARAMS } from "../src/server/controlPlane/humanPasswordPolicy";
import {
  POSTGRES_MIGRATION_MANIFEST,
  SCHEMA_CONTRACT,
} from "../src/server/controlPlane/schemaReadiness";
import {
  createPostgresRoleBoundaryFixture,
} from "./postgres-role-boundary-test-helper";

const ORIGIN = "https://mis.example.test";
const HOST = "mis.example.test";
const WORKSPACE = "ws_enrollment_approval_contract";
const FOREIGN_WORKSPACE = "ws_enrollment_approval_contract_foreign";
const PASSWORD = `${randomBytes(24).toString("base64url")}Aa1!`;
const RAW_INPUT_CANARY = `raw-enrollment-${randomUUID()}`;
const REQUEST_SCOPE = "approvals:request";

type HumanRole =
  | "owner"
  | "workspace-admin"
  | "reviewer"
  | "approver";

type HumanSession = {
  cookie: string;
  csrf: string;
  workspaceId: string;
};

type AgentCredential = {
  agentId: string;
  name: string;
  token: string;
  workspaceId: string;
};

type EnrollmentHandle = {
  requestId: string;
  approvalId: string;
  taskId: string;
  runId: string;
  agentId: string;
  workspaceId: string;
};

type RouteResult = {
  response: Response;
  body: Record<string, unknown>;
};

function sha(value: string) {
  return createHash("sha256").update(value, "utf8").digest("hex");
}

function quotedIdentifier(value: string) {
  return `"${value.replaceAll("\"", "\"\"")}"`;
}

async function responseBody(response: Response) {
  return await response.json() as Record<string, unknown>;
}

function enrollmentBody(
  credential: AgentCredential,
  overrides: Record<string, unknown> = {},
) {
  return {
    workspace_id: credential.workspaceId,
    agent_id: credential.agentId,
    name: credential.name,
    role: "worker",
    runtime_type: "hermes",
    scopes: ["agents:write", "tasks:read"],
    ttl_days: 30,
    heartbeat_timeout_sec: 300,
    label: `${credential.agentId} approved token`,
    reason: `credential=${RAW_INPUT_CANARY}`,
    ...overrides,
  };
}

function requestHeaders(input: {
  workspaceId: string;
  idempotencyKey: string;
  agent?: AgentCredential;
  human?: HumanSession;
}) {
  const headers = new Headers({
    "content-type": "application/json",
    host: HOST,
    "x-agentops-workspace-id": input.workspaceId,
    "idempotency-key": input.idempotencyKey,
  });
  if (input.agent) {
    headers.set("authorization", `Bearer ${input.agent.token}`);
    headers.set("x-agentops-agent-id", input.agent.agentId);
  }
  if (input.human) {
    headers.set("origin", ORIGIN);
    headers.set("cookie", input.human.cookie);
    headers.set("x-agentops-csrf", input.human.csrf);
  }
  return headers;
}

function routeRequest(
  path: string,
  body: Record<string, unknown>,
  input: {
    workspaceId: string;
    idempotencyKey: string;
    agent?: AgentCredential;
    human?: HumanSession;
  },
) {
  return new NextRequest(`${ORIGIN}${path}`, {
    method: "POST",
    headers: requestHeaders(input),
    body: JSON.stringify(body),
  });
}

async function callRequestRoute(
  credential: AgentCredential,
  idempotencyKey: string,
  body = enrollmentBody(credential),
): Promise<RouteResult> {
  const response = await requestRoute(routeRequest(
    "/api/mis/agent-gateway/enrollment/request",
    body,
    {
      workspaceId: credential.workspaceId,
      idempotencyKey,
      agent: credential,
    },
  ));
  return { response, body: await responseBody(response) };
}

async function requestEnrollment(
  credential: AgentCredential,
  idempotencyKey: string,
): Promise<{ result: RouteResult; handle: EnrollmentHandle }> {
  const result = await callRequestRoute(credential, idempotencyKey);
  assert.equal(result.response.status, 201);
  assert.equal(result.body.token_issued, false);
  assert.equal(Object.hasOwn(result.body, "token"), false);
  const request = result.body.request as Record<string, unknown>;
  const approval = result.body.approval as Record<string, unknown>;
  const handle = {
    requestId: String(request.request_id),
    approvalId: String(approval.approval_id),
    taskId: String(approval.task_id),
    runId: String(approval.run_id),
    agentId: credential.agentId,
    workspaceId: credential.workspaceId,
  };
  for (const value of [
    handle.requestId,
    handle.approvalId,
    handle.taskId,
    handle.runId,
  ]) {
    assert.match(value, /^[A-Za-z0-9._:-]{1,128}$/);
  }
  return { result, handle };
}

async function decide(
  actor: { human?: HumanSession; agent?: AgentCredential },
  handle: EnrollmentHandle,
  decision: "approve" | "reject",
  idempotencyKey: string,
): Promise<RouteResult> {
  const response = await decisionRoute(
    routeRequest(
      `/api/mis/approvals/${handle.approvalId}/${decision}`,
      { workspace_id: handle.workspaceId },
      {
        workspaceId: handle.workspaceId,
        idempotencyKey,
        ...actor,
      },
    ),
    {
      params: Promise.resolve({
        approvalId: handle.approvalId,
        decision,
      }),
    },
  );
  return { response, body: await responseBody(response) };
}

async function issue(
  actor: { human?: HumanSession; agent?: AgentCredential },
  handle: EnrollmentHandle,
  idempotencyKey: string,
  selector: "request" | "approval" = "request",
): Promise<RouteResult> {
  const body = selector === "request"
    ? {
      workspace_id: handle.workspaceId,
      request_id: handle.requestId,
    }
    : {
      workspace_id: handle.workspaceId,
      approval_id: handle.approvalId,
    };
  const response = await issueRoute(routeRequest(
    "/api/mis/agent-gateway/enrollment/issue-approved",
    body,
    {
      workspaceId: handle.workspaceId,
      idempotencyKey,
      ...actor,
    },
  ));
  return { response, body: await responseBody(response) };
}

function assertDenied(
  result: RouteResult,
  statuses: number[],
  errorCodes: string[],
) {
  assert.ok(
    statuses.includes(result.response.status),
    `expected one of ${statuses.join(",")}, got ${result.response.status}`,
  );
  assert.ok(
    errorCodes.includes(String(result.body.error)),
    `unexpected error code: ${String(result.body.error)}`,
  );
  assert.equal(Object.hasOwn(result.body, "token"), false);
}

async function seedHuman(
  client: Client,
  userId: string,
  username: string,
  role: HumanRole,
  workspaceId = WORKSPACE,
) {
  const now = new Date().toISOString();
  const salt = randomBytes(16);
  const passwordHash = scryptSync(
    PASSWORD,
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
    `INSERT INTO users(user_id,name,email,role,created_at)
    VALUES($1,$2,$3,$4,$5)`,
    [userId, username, `${username}@example.test`, role, now],
  );
  await client.query(
    `INSERT INTO workspace_memberships(
      workspace_id,user_id,role,status,created_at,updated_at
    ) VALUES($1,$2,$3,'active',$4,$4)`,
    [workspaceId, userId, role, now],
  );
  await client.query(
    `INSERT INTO human_login_credentials(
      credential_id,user_id,username,password_hash,password_salt,
      password_params_json,status,created_at,updated_at,last_login_at
    ) VALUES($1,$2,$3,$4,$5,$6,'active',$7,$7,NULL)`,
    [
      `cred_${username}`,
      userId,
      username,
      passwordHash,
      salt.toString("hex"),
      JSON.stringify(HUMAN_SCRYPT_PARAMS),
      now,
    ],
  );
}

async function login(username: string, workspaceId = WORKSPACE) {
  const result = await establishHumanSession(
    new Headers({ origin: ORIGIN, host: HOST }),
    { username, password: PASSWORD },
  );
  assert.equal(result.status, 200);
  return {
    cookie: result.setCookie.split(";", 1)[0],
    csrf: String(result.body.csrf_token),
    workspaceId,
  };
}

async function seedAgentCredential(
  client: Client,
  label: string,
  options: {
    scopes?: string[];
    workspaceId?: string;
  } = {},
): Promise<AgentCredential> {
  const workspaceId = options.workspaceId || WORKSPACE;
  const agentId = `agt_enrollment_${label}`;
  const token = `agt_fixture_${randomBytes(24).toString("base64url")}`;
  const now = new Date().toISOString();
  const expiresAt = new Date(Date.now() + 60 * 60 * 1000).toISOString();
  const name = `Enrollment ${label}`;
  await client.query(
    `INSERT INTO agents(
      agent_id,name,role,description,runtime_type,model_provider,model_name,
      status,permission_level,allowed_tools,budget_limit_usd,owner_user_id,
      created_at,updated_at
    ) VALUES(
      $1,$2,'worker',NULL,'hermes','hermes','contract-model','idle',
      'standard','[]',0,'usr_enrollment_owner',$3,$3
    )`,
    [agentId, name, now],
  );
  await client.query(
    `INSERT INTO agent_gateway_tokens(
      token_id,token_hash,workspace_id,agent_id,scopes_json,status,label,
      heartbeat_timeout_sec,created_at,expires_at,revoked_at,last_used_at,
      last_heartbeat_at
    ) VALUES($1,$2,$3,$4,$5,'active','request fixture',300,$6,$7,NULL,NULL,NULL)`,
    [
      `tok_enrollment_${label}`,
      sha(token),
      workspaceId,
      agentId,
      JSON.stringify(options.scopes ?? [REQUEST_SCOPE]),
      now,
      expiresAt,
    ],
  );
  return { agentId, name, token, workspaceId };
}

async function seedEntitlement(client: Client, workspaceId = WORKSPACE) {
  const now = new Date();
  await client.query(
    `INSERT INTO workspace_entitlements(
      workspace_id,edition,status,capabilities_json,max_agents,
      max_active_enrollments,max_active_sessions_per_agent,max_monthly_runs,
      max_monthly_cost_usd,effective_at,expires_at,created_at,updated_at,
      updated_by_user_id
    ) VALUES(
      $1,'team_governance','active',$2::jsonb,100,100,10,1000,1000,
      $3,$4,$3,$3,'usr_enrollment_owner'
    )
    ON CONFLICT(workspace_id) DO UPDATE SET
      status=EXCLUDED.status,
      capabilities_json=EXCLUDED.capabilities_json,
      max_agents=EXCLUDED.max_agents,
      max_active_enrollments=EXCLUDED.max_active_enrollments,
      updated_at=EXCLUDED.updated_at`,
    [
      workspaceId,
      JSON.stringify({
        enrollment_issue: true,
        session_issue: true,
        run_start: true,
      }),
      new Date(now.getTime() - 60_000).toISOString(),
      new Date(now.getTime() + 24 * 60 * 60 * 1000).toISOString(),
    ],
  );
}

async function countIssuedTokens(client: Client, agentId: string) {
  const result = await client.query<{ count: string }>(
    `SELECT count(*)::text AS count
    FROM agent_gateway_tokens
    WHERE workspace_id=$1 AND agent_id=$2 AND label<>'request fixture'`,
    [WORKSPACE, agentId],
  );
  return Number(result.rows[0]?.count || "0");
}

async function assertNoDatabaseValue(
  client: Client,
  forbiddenValues: string[],
) {
  const columns = await client.query<{
    table_name: string;
    column_name: string;
    data_type: string;
  }>(
    `SELECT table_name,column_name,data_type
    FROM information_schema.columns
    WHERE table_schema=current_schema()
      AND data_type IN (
        'text','character varying','character','json','jsonb','bytea'
      )
    ORDER BY table_name,column_name`,
  );
  for (const value of forbiddenValues) {
    for (const column of columns.rows) {
      const tableName = quotedIdentifier(column.table_name);
      const columnName = quotedIdentifier(column.column_name);
      const projection = column.data_type === "bytea"
        ? `encode(${columnName},'escape')`
        : `${columnName}::text`;
      const leaked = await client.query<{ leaked: boolean }>(
        `SELECT EXISTS(
          SELECT 1 FROM ${tableName}
          WHERE ${projection} LIKE $1
        ) AS leaked`,
        [`%${value}%`],
      );
      assert.equal(
        leaked.rows[0]?.leaked,
        false,
        `${column.table_name}.${column.column_name} retained a forbidden raw value`,
      );
    }
  }
}

async function assertTokenHashOnly(
  client: Client,
  handle: EnrollmentHandle,
  rawToken: string,
) {
  const stored = await client.query<{
    token_id: string;
    token_hash: string;
    scopes_json: string;
  }>(
    `SELECT token_id,token_hash,scopes_json
    FROM agent_gateway_tokens
    WHERE workspace_id=$1 AND agent_id=$2 AND label<>'request fixture'`,
    [handle.workspaceId, handle.agentId],
  );
  assert.equal(stored.rowCount, 1);
  assert.equal(stored.rows[0].token_hash, sha(rawToken));
  assert.notEqual(stored.rows[0].token_id, rawToken);
  assert.equal(stored.rows[0].scopes_json.includes(rawToken), false);
  await assertNoDatabaseValue(client, [rawToken]);
}

async function assertEntitlementDenialEvidence(
  client: Client,
  handle: EnrollmentHandle,
) {
  const audit = await client.query<{
    actor_type: string;
    entity_id: string;
    metadata_json: string;
  }>(
    `SELECT actor_type,entity_id,metadata_json
    FROM audit_logs
    WHERE workspace_id=$1
      AND action='agent_gateway.enrollment_entitlement_denied'
      AND entity_type='agent_gateway_enrollment_requests'
      AND entity_id=$2
    ORDER BY created_at,audit_id`,
    [handle.workspaceId, handle.requestId],
  );
  assert.equal(audit.rowCount, 1);
  const auditMetadata = JSON.parse(
    audit.rows[0].metadata_json,
  ) as Record<string, unknown>;
  assert.equal(audit.rows[0].actor_type, "user");
  assert.equal(auditMetadata.approval_id, handle.approvalId);
  assert.equal(auditMetadata.task_id, handle.taskId);
  assert.equal(auditMetadata.run_id, handle.runId);
  assert.equal(auditMetadata.agent_id, handle.agentId);
  assert.equal(
    (auditMetadata.entitlement_decision as Record<string, unknown>).decision,
    "deny",
  );

  const runtime = await client.query<{
    run_id: string;
    task_id: string;
    agent_id: string;
    status: string;
    raw_payload_hash: string;
  }>(
    `SELECT run_id,task_id,agent_id,status,raw_payload_hash
    FROM runtime_events
    WHERE workspace_id=$1
      AND event_type='agent.enrollment.issue_denied'
      AND run_id=$2 AND task_id=$3 AND agent_id=$4
    ORDER BY created_at,runtime_event_id`,
    [
      handle.workspaceId,
      handle.runId,
      handle.taskId,
      handle.agentId,
    ],
  );
  assert.equal(runtime.rowCount, 1);
  assert.equal(runtime.rows[0].status, "blocked");
  assert.match(runtime.rows[0].raw_payload_hash, /^[a-f0-9]{64}$/);
}

async function attemptBindingDrift(
  client: Client,
  label: string,
  mutate: () => Promise<void>,
  issueAfterMutation: () => Promise<RouteResult>,
  agentId: string,
) {
  const before = await countIssuedTokens(client, agentId);
  let databaseRejected = false;
  await client.query("BEGIN");
  try {
    await mutate();
    await client.query("SET CONSTRAINTS ALL IMMEDIATE");
    await client.query("COMMIT");
  } catch {
    databaseRejected = true;
    await client.query("ROLLBACK").catch(() => undefined);
  }
  if (!databaseRejected) {
    const denied = await issueAfterMutation();
    assertDenied(
      denied,
      [404, 409],
      [
        "enrollment_approval_binding_invalid",
        "enrollment_request_binding_evidence_invalid",
        "enrollment_request_not_found",
      ],
    );
  }
  assert.equal(
    await countIssuedTokens(client, agentId),
    before,
    `${label} drift issued a credential`,
  );
}

async function assertStaticAuthorityBoundary() {
  const requestSource = await readFile(
    new URL(
      "../src/server/controlPlane/agentGatewayEnrollmentApprovals.ts",
      import.meta.url,
    ),
    "utf8",
  );
  const requestOwner = requestSource.match(
    /export async function requestGatewayEnrollment[\s\S]*?(?=\nexport async function)/,
  )?.[0] || "";
  const decisionOwner = requestSource.match(
    /export async function decideGatewayEnrollmentApproval[\s\S]*?(?=\nexport async function)/,
  )?.[0] || "";
  const issueOwner = requestSource.match(
    /export async function issueApprovedGatewayEnrollment[\s\S]*?(?=\nexport async function|$)/,
  )?.[0] || "";
  assert.match(requestOwner, /authenticateAgentGateway/);
  assert.match(requestOwner, /approvals:request/);
  assert.match(requestOwner, /identity\.agentId/);
  assert.doesNotMatch(requestOwner, /authenticateHuman/);
  assert.match(decisionOwner, /requireGatewayAdministrator/);
  assert.match(issueOwner, /requireGatewayAdministrator/);
  assert.doesNotMatch(requestSource, /proxyControlPlaneRequest/);
  assert.doesNotMatch(requestSource, /child_process/);
  assert.doesNotMatch(requestSource, /\.py\b/);
  assert.doesNotMatch(requestSource, /\bsqlite\b/i);
}

async function run() {
  const baseDsn = String(process.env.AGENTOPS_POSTGRES_DSN || "").trim();
  assert.ok(baseDsn, "AGENTOPS_POSTGRES_DSN is required");
  const original = {
    dsn: process.env.AGENTOPS_POSTGRES_DSN,
    deployment: process.env.AGENTOPS_DEPLOYMENT_MODE,
    mode: process.env.AGENTOPS_CONTROL_PLANE_MODE,
    origins: process.env.AGENTOPS_ALLOWED_ORIGINS,
    hmac: process.env.AGENTOPS_HUMAN_SESSION_HMAC_KEY,
    fetch: globalThis.fetch,
  };
  const roleFixture = await createPostgresRoleBoundaryFixture(
    baseDsn,
    "enrollment_approval",
  );
  const admin = roleFixture.owner;
  const restoreRuntimeEnvironment =
    roleFixture.activateRuntimeEnvironment();
  let providerCalls = 0;
  process.env.AGENTOPS_ALLOWED_ORIGINS = ORIGIN;
  process.env.AGENTOPS_HUMAN_SESSION_HMAC_KEY = randomBytes(48)
    .toString("base64url");
  globalThis.fetch = async () => {
    providerCalls += 1;
    throw new Error("Network access is forbidden in this contract.");
  };

  try {
    const version = await admin.query<{ server_version: string }>(
      "SHOW server_version",
    );
    assert.match(version.rows[0]?.server_version || "", /^16\./);
    const migration = roleFixture.migration;
    assert.equal(migration.schema_contract, SCHEMA_CONTRACT);
    assert.equal(migration.applied_count, POSTGRES_MIGRATION_MANIFEST.length);

    await seedHuman(
      admin,
      "usr_enrollment_owner",
      "enrollment-owner",
      "owner",
    );
    await seedHuman(
      admin,
      "usr_enrollment_admin",
      "enrollment-admin",
      "workspace-admin",
    );
    await seedHuman(
      admin,
      "usr_enrollment_reviewer",
      "enrollment-reviewer",
      "reviewer",
    );
    await seedHuman(
      admin,
      "usr_enrollment_approver",
      "enrollment-approver",
      "approver",
    );
    await seedHuman(
      admin,
      "usr_enrollment_foreign_owner",
      "enrollment-foreign-owner",
      "owner",
      FOREIGN_WORKSPACE,
    );

    const owner = await login("enrollment-owner");
    const workspaceAdmin = await login("enrollment-admin");
    const reviewer = await login("enrollment-reviewer");
    const approver = await login("enrollment-approver");
    const foreignOwner = await login(
      "enrollment-foreign-owner",
      FOREIGN_WORKSPACE,
    );

    const validAgents = new Map<string, AgentCredential>();
    for (const label of [
      "auth_self",
      "rbac",
      "owner_authority",
      "admin_authority",
      "decision_race",
      "issue_race",
      "entitlement_deny",
      "cross_workspace",
      "drift_task",
      "drift_run",
      "drift_agent",
      "drift_binding",
    ]) {
      validAgents.set(label, await seedAgentCredential(admin, label));
    }
    const missingScope = await seedAgentCredential(
      admin,
      "missing_scope",
      { scopes: ["tasks:read"] },
    );
    const foreignAgent = await seedAgentCredential(
      admin,
      "foreign",
      { workspaceId: FOREIGN_WORKSPACE },
    );

    const selfAgent = validAgents.get("auth_self") as AgentCredential;
    const selfRequest = await requestEnrollment(
      selfAgent,
      "enrollment-agent-self-0001",
    );
    assert.equal(
      (selfRequest.result.body.approval as Record<string, unknown>)
        .requested_by_agent_id,
      selfAgent.agentId,
    );

    const humanRequestResponse = await requestRoute(routeRequest(
      "/api/mis/agent-gateway/enrollment/request",
      enrollmentBody(selfAgent),
      {
        workspaceId: WORKSPACE,
        idempotencyKey: "enrollment-human-request-forbidden-0001",
        human: owner,
      },
    ));
    assertDenied(
      {
        response: humanRequestResponse,
        body: await responseBody(humanRequestResponse),
      },
      [401],
      ["agent_auth_required", "unauthorized"],
    );

    const missingScopeResult = await callRequestRoute(
      missingScope,
      "enrollment-missing-request-scope-0001",
    );
    assertDenied(missingScopeResult, [403], ["forbidden"]);

    const wrongSelfResult = await callRequestRoute(
      selfAgent,
      "enrollment-agent-other-forbidden-0001",
      enrollmentBody(validAgents.get("rbac") as AgentCredential),
    );
    assertDenied(
      wrongSelfResult,
      [403],
      ["agent_identity_mismatch", "forbidden"],
    );

    const foreignBindingResult = await callRequestRoute(
      foreignAgent,
      "enrollment-agent-workspace-forbidden-0001",
      enrollmentBody(foreignAgent, { workspace_id: WORKSPACE }),
    );
    assertDenied(
      foreignBindingResult,
      [403],
      ["workspace_binding_mismatch", "forbidden"],
    );

    const rbacAgent = validAgents.get("rbac") as AgentCredential;
    const rbacRequest = await requestEnrollment(
      rbacAgent,
      "enrollment-rbac-request-0001",
    );
    for (const [role, session] of [
      ["reviewer", reviewer],
      ["approver", approver],
    ] as const) {
      assertDenied(
        await decide(
          { human: session },
          rbacRequest.handle,
          "approve",
          `enrollment-${role}-decision-forbidden-0001`,
        ),
        [403],
        ["human_admin_role_forbidden"],
      );
      assertDenied(
        await issue(
          { human: session },
          rbacRequest.handle,
          `enrollment-${role}-issue-forbidden-0001`,
        ),
        [403],
        ["human_admin_role_forbidden"],
      );
    }
    assertDenied(
      await decide(
        { agent: rbacAgent },
        rbacRequest.handle,
        "approve",
        "enrollment-agent-decision-forbidden-0001",
      ),
      [401, 403],
      ["human_auth_required", "machine_credential_not_allowed", "unauthorized"],
    );
    assertDenied(
      await issue(
        { agent: rbacAgent },
        rbacRequest.handle,
        "enrollment-agent-issue-forbidden-0001",
      ),
      [401, 403],
      ["human_auth_required", "machine_credential_not_allowed", "unauthorized"],
    );
    assertDenied(
      await decide(
        { human: foreignOwner },
        rbacRequest.handle,
        "approve",
        "enrollment-foreign-decision-forbidden-0001",
      ),
      [403, 404],
      [
        "human_membership_forbidden",
        "workspace_binding_mismatch",
        "enrollment_request_not_found",
      ],
    );
    assertDenied(
      await issue(
        { human: foreignOwner },
        rbacRequest.handle,
        "enrollment-foreign-issue-forbidden-0001",
      ),
      [403, 404],
      [
        "human_membership_forbidden",
        "workspace_binding_mismatch",
        "enrollment_request_not_found",
      ],
    );

    const ownerAgent = validAgents.get("owner_authority") as AgentCredential;
    const ownerRequest = await requestEnrollment(
      ownerAgent,
      "enrollment-owner-authority-request-0001",
    );
    const ownerDecision = await decide(
      { human: owner },
      ownerRequest.handle,
      "reject",
      "enrollment-owner-authority-decision-0001",
    );
    assert.equal(ownerDecision.response.status, 200);
    assert.equal(ownerDecision.body.decision, "rejected");

    const adminAgent = validAgents.get("admin_authority") as AgentCredential;
    const adminRequest = await requestEnrollment(
      adminAgent,
      "enrollment-admin-authority-request-0001",
    );
    const adminDecision = await decide(
      { human: workspaceAdmin },
      adminRequest.handle,
      "approve",
      "enrollment-admin-authority-decision-0001",
    );
    assert.equal(adminDecision.response.status, 200);
    assert.equal(adminDecision.body.decision, "approved");

    const decisionRaceAgent = validAgents.get(
      "decision_race",
    ) as AgentCredential;
    const decisionRaceRequest = await requestEnrollment(
      decisionRaceAgent,
      "enrollment-decision-race-request-0001",
    );
    const decisionRace = await Promise.all(
      Array.from({ length: 12 }, (_, index) => decide(
        {
          human: index % 2 === 0 ? owner : workspaceAdmin,
        },
        decisionRaceRequest.handle,
        "approve",
        `enrollment-decision-race-${String(index).padStart(4, "0")}`,
      )),
    );
    assert.equal(
      decisionRace.filter((result) =>
        result.response.status === 200 && result.body.outcome === "updated"
      ).length,
      1,
    );
    assert.equal(
      decisionRace.filter((result) =>
        result.response.status === 200
        && !["updated", "unchanged"].includes(String(result.body.outcome))
      ).length,
      0,
    );
    assert.equal(
      decisionRace.some((result) => Object.hasOwn(result.body, "token")),
      false,
    );

    const entitlementAgent = validAgents.get(
      "entitlement_deny",
    ) as AgentCredential;
    const entitlementRequest = await requestEnrollment(
      entitlementAgent,
      "enrollment-entitlement-request-0001",
    );
    assert.equal((await decide(
      { human: owner },
      entitlementRequest.handle,
      "approve",
      "enrollment-entitlement-approve-0001",
    )).response.status, 200);
    const deniedIssue = await issue(
      { human: owner },
      entitlementRequest.handle,
      "enrollment-entitlement-denied-issue-0001",
    );
    assertDenied(
      deniedIssue,
      [403],
      ["workspace_entitlement_denied"],
    );
    assert.equal(await countIssuedTokens(admin, entitlementAgent.agentId), 0);
    await assertEntitlementDenialEvidence(admin, entitlementRequest.handle);

    await seedEntitlement(admin);

    const adminIssue = await issue(
      { human: workspaceAdmin },
      adminRequest.handle,
      "enrollment-admin-authority-issue-0001",
    );
    assert.equal(adminIssue.response.status, 201);
    const adminRawToken = String(adminIssue.body.token);
    assert.match(adminRawToken, /^agtok_[A-Za-z0-9_-]+$/);
    await assertTokenHashOnly(admin, adminRequest.handle, adminRawToken);
    const adminReplay = await issue(
      { human: workspaceAdmin },
      adminRequest.handle,
      "enrollment-admin-authority-issue-0001",
    );
    assert.equal(adminReplay.response.status, 200);
    assert.equal(Object.hasOwn(adminReplay.body, "token"), false);
    assert.equal(JSON.stringify(adminReplay.body).includes(adminRawToken), false);

    const ownerIssueAgent = await seedAgentCredential(admin, "owner_issue");
    const ownerIssueRequest = await requestEnrollment(
      ownerIssueAgent,
      "enrollment-owner-issue-request-0001",
    );
    assert.equal((await decide(
      { human: owner },
      ownerIssueRequest.handle,
      "approve",
      "enrollment-owner-issue-decision-0001",
    )).response.status, 200);
    const ownerIssue = await issue(
      { human: owner },
      ownerIssueRequest.handle,
      "enrollment-owner-issue-0001",
    );
    assert.equal(ownerIssue.response.status, 201);
    const ownerRawToken = String(ownerIssue.body.token);
    assert.match(ownerRawToken, /^agtok_[A-Za-z0-9_-]+$/);
    await assertTokenHashOnly(admin, ownerIssueRequest.handle, ownerRawToken);

    const issueRaceAgent = validAgents.get("issue_race") as AgentCredential;
    const issueRaceRequest = await requestEnrollment(
      issueRaceAgent,
      "enrollment-issue-race-request-0001",
    );
    assert.equal((await decide(
      { human: workspaceAdmin },
      issueRaceRequest.handle,
      "approve",
      "enrollment-issue-race-decision-0001",
    )).response.status, 200);
    const issueRace = await Promise.all(
      Array.from({ length: 12 }, () => issue(
        { human: workspaceAdmin },
        issueRaceRequest.handle,
        "enrollment-issue-race-single-key-0001",
      )),
    );
    const firstDeliveries = issueRace.filter((result) =>
      result.response.status === 201
      && typeof result.body.token === "string"
    );
    assert.equal(firstDeliveries.length, 1);
    assert.equal(
      issueRace.filter((result) =>
        typeof result.body.token === "string"
      ).length,
      1,
    );
    assert.equal(
      issueRace.filter((result) =>
        ![200, 201].includes(result.response.status)
      ).length,
      0,
    );
    const raceRawToken = String(firstDeliveries[0].body.token);
    await assertTokenHashOnly(admin, issueRaceRequest.handle, raceRawToken);
    const issueRaceReplay = await issue(
      { human: workspaceAdmin },
      issueRaceRequest.handle,
      "enrollment-issue-race-single-key-0001",
    );
    assert.equal(issueRaceReplay.response.status, 200);
    assert.equal(Object.hasOwn(issueRaceReplay.body, "token"), false);

    const driftSpecs: Array<{
      label: "task" | "run" | "agent" | "binding";
      mutate: (
        handle: EnrollmentHandle,
        otherAgentId: string,
      ) => Promise<void>;
    }> = [
      {
        label: "task",
        mutate: async (handle) => {
          await admin.query(
            "UPDATE tasks SET workspace_id=$1 WHERE task_id=$2",
            [FOREIGN_WORKSPACE, handle.taskId],
          );
        },
      },
      {
        label: "run",
        mutate: async (handle) => {
          await admin.query(
            "UPDATE runs SET workspace_id=$1 WHERE run_id=$2",
            [FOREIGN_WORKSPACE, handle.runId],
          );
        },
      },
      {
        label: "agent",
        mutate: async (handle, otherAgentId) => {
          await admin.query(
            "UPDATE runs SET agent_id=$1 WHERE run_id=$2",
            [otherAgentId, handle.runId],
          );
        },
      },
      {
        label: "binding",
        mutate: async (handle) => {
          await admin.query(
            `UPDATE agent_gateway_enrollment_requests
            SET scopes_json=jsonb_set(
              scopes_json::jsonb,'{ttl_days}','31'::jsonb
            )::text
            WHERE request_id=$1`,
            [handle.requestId],
          );
        },
      },
    ];
    const otherAgentId = (validAgents.get("cross_workspace") as AgentCredential)
      .agentId;
    for (const spec of driftSpecs) {
      const driftAgent = validAgents.get(
        `drift_${spec.label}`,
      ) as AgentCredential;
      const driftRequest = await requestEnrollment(
        driftAgent,
        `enrollment-drift-${spec.label}-request-0001`,
      );
      assert.equal((await decide(
        { human: owner },
        driftRequest.handle,
        "approve",
        `enrollment-drift-${spec.label}-decision-0001`,
      )).response.status, 200);
      await attemptBindingDrift(
        admin,
        spec.label,
        () => spec.mutate(driftRequest.handle, otherAgentId),
        () => issue(
          { human: owner },
          driftRequest.handle,
          `enrollment-drift-${spec.label}-issue-0001`,
        ),
        driftAgent.agentId,
      );
    }

    await assertNoDatabaseValue(admin, [
      RAW_INPUT_CANARY,
      adminRawToken,
      ownerRawToken,
      raceRawToken,
    ]);
    await assertStaticAuthorityBoundary();
    assert.equal(providerCalls, 0);

    console.log(JSON.stringify({
      ok: true,
      contract: "approval_gated_agent_gateway_enrollment_postgres_v2",
      postgres_major: 16,
      schema_contract: migration.schema_contract,
      migration_count: migration.applied_count,
      agent_request_scope_required: true,
      agent_self_request_only: true,
      human_session_request_denied: true,
      reviewer_and_approver_decision_issue_denied: true,
      workspace_admin_and_owner_authority: true,
      agent_decision_issue_denied: true,
      concurrent_decision_single_winner: true,
      concurrent_issue_single_raw_token_winner: true,
      cross_workspace_fail_closed: true,
      task_run_agent_binding_drift_fail_closed: true,
      immutable_request_binding_revalidated: true,
      entitlement_denial_bound_audit_runtime_evidence: true,
      one_time_raw_token_delivery: true,
      replay_omits_raw_token: true,
      all_textual_database_columns_scanned: true,
      token_hash_only_at_rest: true,
      production_python_proxy_calls: 0,
      provider_calls: providerCalls,
      raw_prompt_response_omitted: true,
    }, null, 2));
  } finally {
    await closeControlPlanePoolForTests();
    globalThis.fetch = original.fetch;
    restoreRuntimeEnvironment();
    await roleFixture.cleanup();
    const restore = (key: string, value: string | undefined) => {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    };
    restore("AGENTOPS_POSTGRES_DSN", original.dsn);
    restore("AGENTOPS_DEPLOYMENT_MODE", original.deployment);
    restore("AGENTOPS_CONTROL_PLANE_MODE", original.mode);
    restore("AGENTOPS_ALLOWED_ORIGINS", original.origins);
    restore("AGENTOPS_HUMAN_SESSION_HMAC_KEY", original.hmac);
  }
}

run().catch((error) => {
  console.error(error instanceof Error ? error.stack : String(error));
  process.exitCode = 1;
});
