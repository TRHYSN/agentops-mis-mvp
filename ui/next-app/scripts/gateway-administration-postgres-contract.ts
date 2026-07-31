import assert from "node:assert/strict";
import {
  createHash,
  randomBytes,
  scryptSync,
} from "node:crypto";
import { readFile } from "node:fs/promises";

import { Client } from "pg";

import { closeControlPlanePoolForTests } from "../src/server/controlPlane/db";
import {
  createGatewayEnrollment,
  listGatewayEnrollments,
  listGatewaySessions,
  revokeGatewayEnrollment,
  revokeGatewaySession,
  rotateGatewayEnrollment,
} from "../src/server/controlPlane/gatewayAdministration";
import {
  createGatewaySession,
  getGatewayStatus,
} from "../src/server/controlPlane/gatewayLifecycle";
import {
  establishHumanSession,
} from "../src/server/controlPlane/humanSession";
import { HUMAN_SCRYPT_PARAMS } from "../src/server/controlPlane/humanPasswordPolicy";
import { ControlPlaneHttpError } from "../src/server/controlPlane/http";
import {
  SCHEMA_CONTRACT,
} from "../src/server/controlPlane/schemaReadiness";
import {
  createPostgresRoleBoundaryFixture,
} from "./postgres-role-boundary-test-helper";

const ORIGIN = "https://mis.example.test";
const HOST = "mis.example.test";
const WORKSPACE = "ws_gateway_admin";
const FOREIGN_WORKSPACE = "ws_gateway_admin_foreign";
const PASSWORD = `${randomBytes(24).toString("base64url")}Aa1!`;
const SECRET_CANARY = "gateway-admin-sensitive-canary";
const DSN_CANARY = `post${"gresql"}://admin:contract-db-password@internal/gateway`;
const POSTGRES_APPLICATION_NAME =
  `agentops-admin-${randomBytes(6).toString("hex")}`;

type HumanSession = {
  cookie: string;
  csrf: string;
};

function sha(value: string) {
  return createHash("sha256").update(value, "utf8").digest("hex");
}

function loginHeaders() {
  return new Headers({ origin: ORIGIN, host: HOST });
}

function humanHeaders(
  session: HumanSession,
  input?: {
    csrf?: boolean;
    idempotencyKey?: string;
    workspaceId?: string;
    machineCredential?: boolean;
    origin?: string;
  },
) {
  const headers = new Headers({
    cookie: session.cookie,
    host: HOST,
    origin: input?.origin ?? ORIGIN,
    "x-agentops-workspace-id": input?.workspaceId ?? WORKSPACE,
  });
  if (input?.csrf !== false) headers.set("x-agentops-csrf", session.csrf);
  if (input?.idempotencyKey) {
    headers.set("idempotency-key", input.idempotencyKey);
  }
  if (input?.machineCredential) {
    headers.set("authorization", "Bearer machine-not-human");
  }
  return headers;
}

function humanRequest(
  method: "GET" | "POST",
  path: string,
  session: HumanSession,
  body?: Record<string, unknown>,
  input?: Parameters<typeof humanHeaders>[1],
) {
  return new Request(`${ORIGIN}${path}`, {
    method,
    headers: humanHeaders(session, input),
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

function agentRequest(
  method: "GET" | "POST",
  path: string,
  token: string,
  body?: Record<string, unknown>,
) {
  return new Request(`${ORIGIN}${path}`, {
    method,
    headers: new Headers({
      authorization: `Bearer ${token}`,
      "content-type": "application/json",
      "x-agentops-workspace-id": WORKSPACE,
      "x-agentops-agent-id": "agt_admin_contract",
    }),
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

async function expectCode(
  code: string,
  work: () => Promise<unknown>,
) {
  await assert.rejects(work, (error: unknown) => (
    error instanceof ControlPlaneHttpError && error.code === code
  ));
}

async function within<T>(work: Promise<T>, timeoutMs: number) {
  let timeout: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([
      work,
      new Promise<never>((_, reject) => {
        timeout = setTimeout(
          () => reject(new Error(`contract_timeout_after_${timeoutMs}ms`)),
          timeoutMs,
        );
      }),
    ]);
  } finally {
    if (timeout) clearTimeout(timeout);
  }
}

async function waitForBlockedByPid(
  client: Client,
  blockerPid: number,
) {
  const deadline = Date.now() + 5_000;
  while (Date.now() < deadline) {
    const result = await client.query<{ pid: number }>(
      `SELECT pid
      FROM pg_stat_activity
      WHERE datname=current_database()
        AND pid<>pg_backend_pid()
        AND application_name=$1
        AND wait_event_type='Lock'
        AND $2::integer=ANY(pg_blocking_pids(pid))
      ORDER BY query_start,pid`,
      [POSTGRES_APPLICATION_NAME, blockerPid],
    );
    if (result.rows[0]?.pid) return result.rows[0].pid;
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
  throw new Error("expected_target_postgres_lock_waiter");
}

async function waitForBlockedAdvisoryLock(
  client: Client,
  lockKey: number,
) {
  const deadline = Date.now() + 5_000;
  while (Date.now() < deadline) {
    const result = await client.query<{ pid: number }>(
      `SELECT lock_row.pid
      FROM pg_locks lock_row
      JOIN pg_stat_activity activity ON activity.pid=lock_row.pid
      WHERE activity.datname=current_database()
        AND lock_row.pid<>pg_backend_pid()
        AND activity.application_name=$1
        AND lock_row.locktype='advisory'
        AND lock_row.granted=false
        AND lock_row.classid=0
        AND lock_row.objid=$2::oid
      ORDER BY activity.query_start,lock_row.pid`,
      [POSTGRES_APPLICATION_NAME, lockKey],
    );
    if (result.rows[0]?.pid) return result.rows[0].pid;
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
  throw new Error("expected_target_postgres_advisory_lock_waiter");
}

async function seedHuman(
  client: Client,
  userId: string,
  username: string,
  role: "owner" | "approver",
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

async function seedEntitlement(
  client: Client,
  workspaceId = WORKSPACE,
  updatedByUserId = "usr_admin_owner",
) {
  const now = new Date();
  await client.query(
    `INSERT INTO workspace_entitlements(
      workspace_id,edition,status,capabilities_json,max_agents,
      max_active_enrollments,max_active_sessions_per_agent,max_monthly_runs,
      max_monthly_cost_usd,effective_at,expires_at,created_at,updated_at,
      updated_by_user_id
    ) VALUES($1,'team_governance','active',$2::jsonb,1,1,1,100,100,
      $3,$4,$3,$3,$5)`,
    [
      workspaceId,
      JSON.stringify({
        enrollment_issue: true,
        session_issue: true,
        run_start: true,
      }),
      new Date(now.getTime() - 60_000).toISOString(),
      new Date(now.getTime() + 24 * 60 * 60 * 1000).toISOString(),
      updatedByUserId,
    ],
  );
}

async function login(username: string) {
  const result = await establishHumanSession(loginHeaders(), {
    username,
    password: PASSWORD,
  });
  assert.equal(result.status, 200);
  assert.match(result.setCookie, /^agentops_human_session=/);
  assert.match(result.setCookie, /; HttpOnly/);
  assert.match(result.setCookie, /; SameSite=Strict/);
  assert.match(result.setCookie, /; Secure/);
  const csrf = String(result.body.csrf_token || "");
  assert.match(csrf, /^[a-f0-9]{64}$/);
  return {
    cookie: result.setCookie.split(";", 1)[0],
    csrf,
  };
}

function createBody() {
  return {
    workspace_id: WORKSPACE,
    agent_id: "agt_admin_contract",
    name: `Admin Contract Bearer ${SECRET_CANARY}`,
    role: `Remote worker using ${DSN_CANARY}`,
    runtime_type: "hermes",
    scopes: ["agents:heartbeat", "tasks:read"],
    ttl_days: 30,
    heartbeat_timeout_sec: 300,
    label: `credential=${SECRET_CANARY}`,
  };
}

function assertSafe(value: unknown, issuedTokens: string[] = []) {
  const serialized = JSON.stringify(value);
  for (const token of issuedTokens.filter(Boolean)) {
    assert.doesNotMatch(serialized, new RegExp(token, "g"));
  }
  assert.doesNotMatch(serialized, new RegExp(SECRET_CANARY, "g"));
  assert.doesNotMatch(serialized, /contract-db-password/g);
  for (const key of [
    "token_hash",
    "session_hash",
    "password_hash",
    "password_salt",
  ]) {
    assert.doesNotMatch(serialized, new RegExp(`"${key}"\\s*:`, "g"));
  }
}

async function assertStaticOwnership() {
  const paths = [
    "../src/server/controlPlane/gatewayAdministration.ts",
    "../app/api/mis/agent-gateway/enrollments/route.ts",
    "../app/api/mis/agent-gateway/sessions/route.ts",
    "../app/api/mis/agent-gateway/enrollment/create/route.ts",
    "../app/api/mis/agent-gateway/enrollment/revoke/route.ts",
    "../app/api/mis/agent-gateway/enrollment/rotate/route.ts",
    "../app/api/mis/agent-gateway/session/revoke/route.ts",
  ];
  const source = (
    await Promise.all(paths.map((path) => readFile(
      new URL(path, import.meta.url),
      "utf8",
    )))
  ).join("\n");
  assert.match(source, /authenticateHumanReviewer/);
  assert.match(source, /typescript_postgres/);
  assert.match(source, /controlPlaneMode\(\) === "proxy"/);
  assert.match(source, /proxyFreeLocal/);
  assert.doesNotMatch(source, /proxyControlPlaneRequest/);
  assert.doesNotMatch(source, /child_process/);
  assert.doesNotMatch(source, /agentops_mis/);
  assert.doesNotMatch(source, /\.py\b/);
  assert.doesNotMatch(source, /\bfetch\s*\(/);
}

async function run() {
  const baseDsn = String(process.env.AGENTOPS_POSTGRES_DSN || "").trim();
  assert.ok(baseDsn, "AGENTOPS_POSTGRES_DSN is required");
  const originalOrigins = process.env.AGENTOPS_ALLOWED_ORIGINS;
  const originalHmac = process.env.AGENTOPS_HUMAN_SESSION_HMAC_KEY;
  const originalApplicationName =
    process.env.AGENTOPS_POSTGRES_APPLICATION_NAME;
  const originalFetch = globalThis.fetch;
  const roleFixture = await createPostgresRoleBoundaryFixture(
    baseDsn,
    "gateway_admin",
  );
  const admin = roleFixture.owner;
  let restoreRuntimeEnvironment: () => void = () => undefined;
  let fetchCalls = 0;

  process.env.AGENTOPS_ALLOWED_ORIGINS = ORIGIN;
  process.env.AGENTOPS_HUMAN_SESSION_HMAC_KEY = randomBytes(48)
    .toString("base64url");
  process.env.AGENTOPS_POSTGRES_APPLICATION_NAME =
    POSTGRES_APPLICATION_NAME;
  globalThis.fetch = async () => {
    fetchCalls += 1;
    throw new Error("Network access is forbidden in the administration contract.");
  };

  try {
    const version = await admin.query<{ server_version: string }>(
      "SHOW server_version",
    );
    assert.match(version.rows[0]?.server_version || "", /^16\./);
    const migration = roleFixture.migration;
    assert.equal(migration.schema_contract, SCHEMA_CONTRACT);
    assert.equal(migration.schema_fingerprint_verified, true);
    const runtimeEntitlementPrivileges = await admin.query<{
      can_insert: boolean;
      can_update: boolean;
      can_delete: boolean;
      can_truncate: boolean;
    }>(
      `SELECT
        has_table_privilege($1,'workspace_entitlements','INSERT') AS can_insert,
        has_table_privilege($1,'workspace_entitlements','UPDATE') AS can_update,
        has_table_privilege($1,'workspace_entitlements','DELETE') AS can_delete,
        has_table_privilege($1,'workspace_entitlements','TRUNCATE') AS can_truncate`,
      [roleFixture.runtimeRole],
    );
    assert.deepEqual(runtimeEntitlementPrivileges.rows[0], {
      can_insert: false,
      can_update: false,
      can_delete: false,
      can_truncate: false,
    });
    await seedHuman(admin, "usr_admin_owner", "admin-owner", "owner");
    await seedHuman(admin, "usr_admin_reviewer", "admin-reviewer", "approver");
    await seedHuman(
      admin,
      "usr_admin_foreign",
      "admin-foreign",
      "owner",
      FOREIGN_WORKSPACE,
    );
    for (let index = 0; index < 8; index += 1) {
      await seedHuman(
        admin,
        `usr_admin_concurrent_${index}`,
        `admin-concurrent-${index}`,
        "owner",
      );
    }
    await seedEntitlement(admin);
    await seedEntitlement(
      admin,
      FOREIGN_WORKSPACE,
      "usr_admin_foreign",
    );
    restoreRuntimeEnvironment = roleFixture.activateRuntimeEnvironment();
    const owner = await login("admin-owner");
    const reviewer = await login("admin-reviewer");
    const foreign = await login("admin-foreign");
    const concurrentOwners: HumanSession[] = [];
    for (let index = 0; index < 8; index += 1) {
      concurrentOwners.push(await login(`admin-concurrent-${index}`));
    }

    await expectCode(
      "csrf_validation_failed",
      () => createGatewayEnrollment(humanRequest(
        "POST",
        "/api/mis/agent-gateway/enrollment/create",
        owner,
        createBody(),
        { csrf: false, idempotencyKey: "admin-create-no-csrf-0001" },
      )),
    );
    await expectCode(
      "machine_credential_not_allowed",
      () => createGatewayEnrollment(humanRequest(
        "POST",
        "/api/mis/agent-gateway/enrollment/create",
        owner,
        createBody(),
        {
          idempotencyKey: "admin-create-machine-0001",
          machineCredential: true,
        },
      )),
    );
    await expectCode(
      "human_admin_role_forbidden",
      () => createGatewayEnrollment(humanRequest(
        "POST",
        "/api/mis/agent-gateway/enrollment/create",
        reviewer,
        createBody(),
        { idempotencyKey: "admin-create-operator-0001" },
      )),
    );
    await expectCode(
      "human_membership_forbidden",
      () => createGatewayEnrollment(humanRequest(
        "POST",
        "/api/mis/agent-gateway/enrollment/create",
        foreign,
        createBody(),
        {
          idempotencyKey: "admin-create-foreign-0001",
          workspaceId: WORKSPACE,
        },
      )),
    );

    const issueAttempts = await Promise.all(
      Array.from({ length: 8 }, () =>
        createGatewayEnrollment(humanRequest(
          "POST",
          "/api/mis/agent-gateway/enrollment/create",
          owner,
          createBody(),
          { idempotencyKey: "admin-create-concurrent-0001" },
        ))),
    );
    const issued = issueAttempts.filter(
      (result) => typeof result.body.token === "string",
    );
    assert.equal(issued.length, 1);
    assert.equal(
      issueAttempts.filter((result) => result.body.replayed === true).length,
      7,
    );
    const firstToken = String(issued[0].body.token);
    const firstTokenId = String(issued[0].body.token_id);
    const tokenRow = await admin.query<{
      token_hash: string;
      status: string;
    }>(
      `SELECT token_hash,status FROM agent_gateway_tokens
      WHERE token_id=$1`,
      [firstTokenId],
    );
    assert.equal(tokenRow.rowCount, 1);
    assert.equal(tokenRow.rows[0].token_hash, sha(firstToken));
    assert.equal(tokenRow.rows[0].status, "active");
    const firstTokenCount = await admin.query<{ count: string }>(
      `SELECT count(*)::text AS count FROM agent_gateway_tokens
      WHERE workspace_id=$1 AND agent_id='agt_admin_contract'`,
      [WORKSPACE],
    );
    assert.equal(firstTokenCount.rows[0].count, "1");
    await expectCode(
      "enrollment_idempotency_conflict",
      () => createGatewayEnrollment(humanRequest(
        "POST",
        "/api/mis/agent-gateway/enrollment/create",
        owner,
        {
          ...createBody(),
          label: "same key with a different binding",
        },
        { idempotencyKey: "admin-create-concurrent-0001" },
      )),
    );

    await expectCode(
      "human_admin_role_forbidden",
      () => listGatewayEnrollments(humanRequest(
        "GET",
        `/api/mis/agent-gateway/enrollments?workspace_id=${WORKSPACE}`,
        reviewer,
        undefined,
        { csrf: false },
      )),
    );
    await expectCode(
      "human_admin_role_forbidden",
      () => listGatewaySessions(humanRequest(
        "GET",
        `/api/mis/agent-gateway/sessions?workspace_id=${WORKSPACE}`,
        reviewer,
        undefined,
        { csrf: false },
      )),
    );
    const enrollmentList = await listGatewayEnrollments(humanRequest(
      "GET",
      `/api/mis/agent-gateway/enrollments?workspace_id=${WORKSPACE}`,
      owner,
      undefined,
      { csrf: false },
    ));
    assert.equal(enrollmentList.status, 200);
    const enrollments = enrollmentList.body.enrollments as Array<
      Record<string, unknown>
    >;
    assert.equal(enrollments.length, 1);
    assert.equal(enrollments[0].agent_id, "agt_admin_contract");
    assert.match(String(enrollments[0].token_ref), /^token_ref_[a-f0-9]{16}$/);
    assert.equal(Object.hasOwn(enrollments[0], "token_id"), false);
    assert.equal(Object.hasOwn(enrollments[0], "token_hash"), false);
    assertSafe(enrollmentList.body, [firstToken]);

    const session = await createGatewaySession(agentRequest(
      "POST",
      "/api/mis/agent-gateway/session/create",
      firstToken,
      {
        workspace_id: WORKSPACE,
        agent_id: "agt_admin_contract",
        scopes: ["tasks:read"],
        ttl_sec: 900,
        request_id: "admin-contract-session-0001",
      },
    ));
    assert.equal(session.status, 201);
    const sessionToken = String(session.body.session_token);
    const sessionList = await listGatewaySessions(humanRequest(
      "GET",
      `/api/mis/agent-gateway/sessions?workspace_id=${WORKSPACE}`,
      owner,
      undefined,
      { csrf: false },
    ));
    const sessions = sessionList.body.sessions as Array<Record<string, unknown>>;
    assert.equal(sessions.length, 1);
    assert.equal(Object.hasOwn(sessions[0], "session_id"), false);
    assert.equal(Object.hasOwn(sessions[0], "parent_token_id"), false);
    assertSafe(sessionList.body, [firstToken, sessionToken]);

    const revoked = await revokeGatewayEnrollment(humanRequest(
      "POST",
      "/api/mis/agent-gateway/enrollment/revoke",
      owner,
      {
        workspace_id: WORKSPACE,
        token_ref: String(enrollments[0].token_ref),
      },
    ));
    assert.equal(revoked.body.revoked, 1);
    assert.equal(revoked.body.sessions_revoked, 1);
    assert.equal(Object.hasOwn(revoked.body, "token_id"), false);
    assertSafe(revoked.body, [firstToken, sessionToken]);
    await expectCode(
      "unauthorized",
      () => getGatewayStatus(agentRequest(
        "GET",
        "/api/mis/agent-gateway/status",
        firstToken,
      )),
    );
    await expectCode(
      "unauthorized",
      () => getGatewayStatus(agentRequest(
        "GET",
        "/api/mis/agent-gateway/status",
        sessionToken,
      )),
    );

    const rotationBase = await createGatewayEnrollment(humanRequest(
      "POST",
      "/api/mis/agent-gateway/enrollment/create",
      owner,
      {
        ...createBody(),
        label: "rotation base",
      },
      { idempotencyKey: "admin-rotation-base-0001" },
    ));
    const rotationBaseToken = String(rotationBase.body.token);
    const rotationBaseRef = String(rotationBase.body.token_ref);
    assert.match(rotationBaseRef, /^token_ref_[a-f0-9]{16}$/);
    const rotationAttempts = await Promise.all(
      Array.from({ length: 8 }, () =>
        rotateGatewayEnrollment(humanRequest(
          "POST",
          "/api/mis/agent-gateway/enrollment/rotate",
          owner,
          {
            workspace_id: WORKSPACE,
            token_ref: rotationBaseRef,
            scopes: ["agents:heartbeat", "tasks:read"],
            label: "rotation replacement",
          },
          { idempotencyKey: "admin-rotate-concurrent-0001" },
        ))),
    );
    const rotated = rotationAttempts.filter(
      (result) => typeof result.body.token === "string",
    );
    assert.equal(rotated.length, 1);
    assert.equal(
      rotationAttempts.filter((result) => result.body.replayed === true).length,
      7,
    );
    const rotatedToken = String(rotated[0].body.token);
    const activeTokens = await admin.query<{
      token_id: string;
      token_hash: string;
    }>(
      `SELECT token_id,token_hash FROM agent_gateway_tokens
      WHERE workspace_id=$1 AND agent_id='agt_admin_contract'
        AND status='active'`,
      [WORKSPACE],
    );
    assert.equal(activeTokens.rowCount, 1);
    assert.equal(activeTokens.rows[0].token_hash, sha(rotatedToken));
    assert.notEqual(rotatedToken, rotationBaseToken);
    await expectCode(
      "enrollment_idempotency_conflict",
      () => rotateGatewayEnrollment(humanRequest(
        "POST",
        "/api/mis/agent-gateway/enrollment/rotate",
        owner,
        {
          workspace_id: WORKSPACE,
          token_ref: rotationBaseRef,
          scopes: ["agents:heartbeat", "tasks:read"],
          heartbeat_timeout_sec: 301,
          label: "rotation replacement",
        },
        { idempotencyKey: "admin-rotate-concurrent-0001" },
      )),
    );

    const directSession = await createGatewaySession(agentRequest(
      "POST",
      "/api/mis/agent-gateway/session/create",
      rotatedToken,
      {
        workspace_id: WORKSPACE,
        agent_id: "agt_admin_contract",
        scopes: ["tasks:read"],
        ttl_sec: 900,
        request_id: "admin-contract-session-0002",
      },
    ));
    const directSessionToken = String(directSession.body.session_token);
    const activeSessionList = await listGatewaySessions(humanRequest(
      "GET",
      `/api/mis/agent-gateway/sessions?workspace_id=${WORKSPACE}&status=active`,
      owner,
      undefined,
      { csrf: false },
    ));
    const activeSessionRows = activeSessionList.body.sessions as Array<
      Record<string, unknown>
    >;
    assert.equal(activeSessionRows.length, 1);
    const directSessionRef = String(activeSessionRows[0].session_ref);
    assert.match(directSessionRef, /^session_ref_[a-f0-9]{16}$/);
    const sessionRevoked = await revokeGatewaySession(humanRequest(
      "POST",
      "/api/mis/agent-gateway/session/revoke",
      owner,
      { workspace_id: WORKSPACE, session_ref: directSessionRef },
    ));
    assert.equal(sessionRevoked.body.revoked, 1);
    assert.equal(Object.hasOwn(sessionRevoked.body, "session_id"), false);
    await expectCode(
      "unauthorized",
      () => getGatewayStatus(agentRequest(
        "GET",
        "/api/mis/agent-gateway/status",
        directSessionToken,
      )),
    );

    const rotatedTokenRef = String(rotated[0].body.token_ref);
    const lockOrderSessionCreated = await createGatewaySession(agentRequest(
      "POST",
      "/api/mis/agent-gateway/session/create",
      rotatedToken,
      {
        workspace_id: WORKSPACE,
        agent_id: "agt_admin_contract",
        scopes: ["tasks:read"],
        ttl_sec: 900,
        request_id: "admin-lock-order-session-0001",
      },
    ));
    const lockOrderSessionToken = String(
      lockOrderSessionCreated.body.session_token,
    );
    const lockOrderSessionList = await listGatewaySessions(humanRequest(
      "GET",
      `/api/mis/agent-gateway/sessions?workspace_id=${WORKSPACE}&status=active`,
      owner,
      undefined,
      { csrf: false },
    ));
    const lockOrderSessionRef = String(
      (
        lockOrderSessionList.body.sessions as Array<Record<string, unknown>>
      ).find((row) => row.agent_id === "agt_admin_contract")?.session_ref || "",
    );
    assert.match(lockOrderSessionRef, /^session_ref_[a-f0-9]{16}$/);
    const lockOrderBlocker = new Client({
      connectionString: roleFixture.ownerDsn,
    });
    const lockOrderObserver = new Client({
      connectionString: roleFixture.ownerDsn,
      application_name: "agentops-admin-lock-observer",
    });
    await lockOrderBlocker.connect();
    await lockOrderObserver.connect();
    let lockOrderBlockerReleased = false;
    let lockOrderRotationPromise:
      | ReturnType<typeof rotateGatewayEnrollment>
      | undefined;
    let lockOrderRevokePromise:
      | ReturnType<typeof revokeGatewaySession>
      | undefined;
    let lockOrderRotatedToken = "";
    let lockOrderRotatedRef = "";
    try {
      await lockOrderBlocker.query("BEGIN");
      await lockOrderBlocker.query(
        "SELECT pg_advisory_xact_lock(1095779668)",
      );
      lockOrderRotationPromise = rotateGatewayEnrollment(humanRequest(
        "POST",
        "/api/mis/agent-gateway/enrollment/rotate",
        owner,
        {
          workspace_id: WORKSPACE,
          token_ref: rotatedTokenRef,
          label: "lock order replacement",
        },
        { idempotencyKey: "admin-lock-order-rotate-0001" },
      ));
      void lockOrderRotationPromise.catch(() => undefined);
      const rotationPid = await waitForBlockedAdvisoryLock(
        lockOrderObserver,
        1095779668,
      );
      lockOrderRevokePromise = revokeGatewaySession(humanRequest(
        "POST",
        "/api/mis/agent-gateway/session/revoke",
        concurrentOwners[0],
        {
          workspace_id: WORKSPACE,
          session_ref: lockOrderSessionRef,
        },
      ));
      void lockOrderRevokePromise.catch(() => undefined);
      const revokePid = await waitForBlockedByPid(
        lockOrderObserver,
        rotationPid,
      );
      assert.notEqual(revokePid, rotationPid);
      await lockOrderBlocker.query("COMMIT");
      lockOrderBlockerReleased = true;
      const lockOrderRace = await within(
        Promise.allSettled([
          lockOrderRotationPromise,
          lockOrderRevokePromise,
        ]),
        10_000,
      );
      const lockOrderRotation = lockOrderRace[0];
      assert.equal(lockOrderRotation.status, "fulfilled");
      assert.equal(lockOrderRotation.value.status, 201);
      lockOrderRotatedToken = String(lockOrderRotation.value.body.token);
      lockOrderRotatedRef = String(
        lockOrderRotation.value.body.token_ref,
      );
      const lockOrderHumanRevoke = lockOrderRace[1];
      assert.equal(lockOrderHumanRevoke.status, "fulfilled");
      assert.equal(lockOrderHumanRevoke.value.status, 200);
      assert(
        lockOrderHumanRevoke.value.body.revoked === 0
        || lockOrderHumanRevoke.value.body.revoked === 1,
      );
    } finally {
      if (!lockOrderBlockerReleased) {
        await lockOrderBlocker.query("ROLLBACK").catch(() => undefined);
      }
      const pendingOperations: Promise<unknown>[] = [];
      if (lockOrderRotationPromise) {
        pendingOperations.push(lockOrderRotationPromise);
      }
      if (lockOrderRevokePromise) {
        pendingOperations.push(lockOrderRevokePromise);
      }
      await Promise.allSettled(pendingOperations);
      await lockOrderBlocker.end().catch(() => undefined);
      await lockOrderObserver.end().catch(() => undefined);
    }
    await expectCode(
      "unauthorized",
      () => getGatewayStatus(agentRequest(
        "GET",
        "/api/mis/agent-gateway/status",
        lockOrderSessionToken,
      )),
    );

    await admin.query(
      `UPDATE workspace_entitlements
      SET max_active_enrollments=2,updated_at=clock_timestamp()
      WHERE workspace_id=$1`,
      [WORKSPACE],
    );
    const siblingEnrollment = await createGatewayEnrollment(humanRequest(
      "POST",
      "/api/mis/agent-gateway/enrollment/create",
      owner,
      {
        ...createBody(),
        label: "sibling enrollment lock order",
      },
      { idempotencyKey: "admin-sibling-enrollment-0001" },
    ));
    const siblingToken = String(siblingEnrollment.body.token);
    const siblingLockRace = await within(
      Promise.allSettled([
        rotateGatewayEnrollment(humanRequest(
          "POST",
          "/api/mis/agent-gateway/enrollment/rotate",
          owner,
          {
            workspace_id: WORKSPACE,
            token_ref: lockOrderRotatedRef,
            label: "sibling lock order replacement",
          },
          { idempotencyKey: "admin-sibling-rotate-0001" },
        )),
        createGatewaySession(agentRequest(
          "POST",
          "/api/mis/agent-gateway/session/create",
          siblingToken,
          {
            workspace_id: WORKSPACE,
            agent_id: "agt_admin_contract",
            scopes: ["tasks:read"],
            ttl_sec: 900,
            request_id: "admin-sibling-session-0001",
          },
        )),
      ]),
      10_000,
    );
    assert.equal(siblingLockRace[0].status, "fulfilled");
    assert.equal(siblingLockRace[1].status, "fulfilled");
    const siblingRotationToken = siblingLockRace[0].status === "fulfilled"
      ? String(siblingLockRace[0].value.body.token)
      : "";
    const siblingSessionToken = siblingLockRace[1].status === "fulfilled"
      ? String(siblingLockRace[1].value.body.session_token)
      : "";
    await admin.query(
      `UPDATE workspace_entitlements
      SET max_active_enrollments=1,updated_at=clock_timestamp()
      WHERE workspace_id=$1`,
      [WORKSPACE],
    );
    const activeRevokeBeforeRace = await revokeGatewayEnrollment(humanRequest(
      "POST",
      "/api/mis/agent-gateway/enrollment/revoke",
      owner,
      {
        workspace_id: WORKSPACE,
        agent_id: "agt_admin_contract",
      },
    ));
    assert.equal(activeRevokeBeforeRace.body.revoked, 2);
    assert.equal(activeRevokeBeforeRace.body.sessions_revoked, 1);
    const multiAdminAttempts = await Promise.all(
      concurrentOwners.map((concurrentOwner) =>
        createGatewayEnrollment(humanRequest(
          "POST",
          "/api/mis/agent-gateway/enrollment/create",
          concurrentOwner,
          {
            ...createBody(),
            agent_id: "agt_admin_multi_owner_race",
            label: "multi-owner quota race",
          },
          { idempotencyKey: "admin-multi-owner-race-0001" },
        ))),
    );
    const multiAdminWinners = multiAdminAttempts.filter(
      (result) => typeof result.body.token === "string",
    );
    const multiAdminDenied = multiAdminAttempts.filter(
      (result) => (
        result.status === 403
        && result.body.error === "workspace_entitlement_denied"
        && (
          result.body.entitlement_decision as Record<string, unknown>
        )?.reason_code === "active_enrollment_quota_exceeded"
      ),
    );
    assert.equal(multiAdminWinners.length, 1);
    assert.equal(multiAdminDenied.length, 7);
    const multiAdminWinnerToken = String(multiAdminWinners[0].body.token);

    await admin.query(
      `UPDATE workspace_entitlements
      SET max_agents=2,max_active_enrollments=2,updated_at=clock_timestamp()
      WHERE workspace_id=$1`,
      [WORKSPACE],
    );
    const crossWorkspaceAttempts = await within(
      Promise.allSettled([
        createGatewayEnrollment(humanRequest(
          "POST",
          "/api/mis/agent-gateway/enrollment/create",
          owner,
          {
            ...createBody(),
            agent_id: "agt_admin_cross_workspace_race",
            label: "cross workspace race",
          },
          { idempotencyKey: "admin-cross-workspace-race-0001" },
        )),
        createGatewayEnrollment(humanRequest(
          "POST",
          "/api/mis/agent-gateway/enrollment/create",
          foreign,
          {
            ...createBody(),
            workspace_id: FOREIGN_WORKSPACE,
            agent_id: "agt_admin_cross_workspace_race",
            label: "cross workspace race",
          },
          {
            idempotencyKey: "admin-cross-workspace-race-0001",
            workspaceId: FOREIGN_WORKSPACE,
          },
        )),
      ]),
      10_000,
    );
    const crossWorkspaceWinners = crossWorkspaceAttempts.filter(
      (result): result is PromiseFulfilledResult<
        Awaited<ReturnType<typeof createGatewayEnrollment>>
      > => result.status === "fulfilled",
    );
    const crossWorkspaceLosers = crossWorkspaceAttempts.filter(
      (result): result is PromiseRejectedResult => result.status === "rejected",
    );
    assert.equal(crossWorkspaceWinners.length, 1);
    assert.equal(crossWorkspaceLosers.length, 1);
    assert(
      crossWorkspaceLosers[0].reason instanceof ControlPlaneHttpError
      && crossWorkspaceLosers[0].reason.code === "agent_workspace_binding_conflict",
    );
    const crossWorkspaceWinner = crossWorkspaceWinners[0].value;
    const crossWorkspaceToken = String(crossWorkspaceWinner.body.token);
    const crossWorkspaceId = String(crossWorkspaceWinner.body.workspace_id);
    const crossWorkspaceOwner = crossWorkspaceId === WORKSPACE ? owner : foreign;
    const crossWorkspaceRevoke = await revokeGatewayEnrollment(humanRequest(
      "POST",
      "/api/mis/agent-gateway/enrollment/revoke",
      crossWorkspaceOwner,
      {
        workspace_id: crossWorkspaceId,
        token_ref: String(crossWorkspaceWinner.body.token_ref),
      },
      { workspaceId: crossWorkspaceId },
    ));
    assert.equal(crossWorkspaceRevoke.body.revoked, 1);
    await admin.query(
      `UPDATE workspace_entitlements
      SET max_agents=1,max_active_enrollments=1,updated_at=clock_timestamp()
      WHERE workspace_id=$1`,
      [WORKSPACE],
    );

    await admin.query(
      `UPDATE workspace_entitlements SET status='suspended',updated_at=clock_timestamp()
      WHERE workspace_id=$1`,
      [WORKSPACE],
    );
    const entitlementDenied = await createGatewayEnrollment(humanRequest(
      "POST",
      "/api/mis/agent-gateway/enrollment/create",
      owner,
      {
        ...createBody(),
        agent_id: "agt_admin_entitlement_denied",
      },
      { idempotencyKey: "admin-entitlement-denied-0001" },
    ));
    assert.equal(entitlementDenied.status, 403);
    assert.equal(entitlementDenied.body.error, "workspace_entitlement_denied");
    assert.equal(
      (entitlementDenied.body.entitlement_decision as Record<string, unknown>)
        .reason_code,
      "entitlement_suspended",
    );
    assert.equal(entitlementDenied.body.credential_generated, false);
    assert.equal(Object.hasOwn(entitlementDenied.body, "token"), false);
    const deniedReplay = await createGatewayEnrollment(humanRequest(
      "POST",
      "/api/mis/agent-gateway/enrollment/create",
      owner,
      {
        ...createBody(),
        agent_id: "agt_admin_entitlement_denied",
      },
      { idempotencyKey: "admin-entitlement-denied-0001" },
    ));
    assert.equal(deniedReplay.status, 403);
    assert.equal(deniedReplay.body.replayed, true);
    assert.equal(
      (deniedReplay.body.entitlement_decision as Record<string, unknown>)
        .reason_code,
      "entitlement_suspended",
    );
    await admin.query(
      `UPDATE workspace_entitlements SET status='active',updated_at=clock_timestamp()
      WHERE workspace_id=$1`,
      [WORKSPACE],
    );
    const deniedAfterRecovery = await createGatewayEnrollment(humanRequest(
      "POST",
      "/api/mis/agent-gateway/enrollment/create",
      owner,
      {
        ...createBody(),
        agent_id: "agt_admin_entitlement_denied",
      },
      { idempotencyKey: "admin-entitlement-denied-0001" },
    ));
    assert.equal(deniedAfterRecovery.status, 403);
    assert.equal(deniedAfterRecovery.body.replayed, true);
    assert.equal(
      (deniedAfterRecovery.body.entitlement_decision as Record<string, unknown>)
        .reason_code,
      "entitlement_suspended",
    );
    await expectCode(
      "enrollment_idempotency_conflict",
      () => createGatewayEnrollment(humanRequest(
        "POST",
        "/api/mis/agent-gateway/enrollment/create",
        owner,
        {
          ...createBody(),
          agent_id: "agt_admin_entitlement_denied",
          label: "changed after the denied request",
        },
        { idempotencyKey: "admin-entitlement-denied-0001" },
      )),
    );
    await admin.query(
      `UPDATE workspace_entitlements SET status='suspended',updated_at=clock_timestamp()
      WHERE workspace_id=$1`,
      [WORKSPACE],
    );
    const revokeWhileSuspended = await revokeGatewayEnrollment(humanRequest(
      "POST",
      "/api/mis/agent-gateway/enrollment/revoke",
      owner,
      {
        workspace_id: WORKSPACE,
        agent_id: "agt_admin_multi_owner_race",
      },
    ));
    assert.equal(revokeWhileSuspended.body.revoked, 1);
    const entitlementAudit = await admin.query<{
      count: string;
      metadata_json: string;
    }>(
      `SELECT count(*)::text AS count,max(metadata_json) AS metadata_json
      FROM audit_logs
      WHERE workspace_id=$1
        AND action='agent_gateway.enrollment_entitlement_denied'
        AND metadata_json::jsonb #>> '{entitlement_decision,reason_code}'
          ='entitlement_suspended'`,
      [WORKSPACE],
    );
    assert.equal(entitlementAudit.rows[0].count, "1");
    assert.match(entitlementAudit.rows[0].metadata_json, /entitlement_suspended/);

    const auditRows = await admin.query<{
      entity_id: string;
      metadata_json: string;
    }>(
      `SELECT entity_id,metadata_json FROM audit_logs
      WHERE workspace_id=ANY($1::text[])`,
      [[WORKSPACE, FOREIGN_WORKSPACE]],
    );
    assertSafe(auditRows.rows, [
      firstToken,
      sessionToken,
      rotationBaseToken,
      rotatedToken,
      directSessionToken,
      lockOrderRotatedToken,
      lockOrderSessionToken,
      siblingToken,
      siblingRotationToken,
      siblingSessionToken,
      multiAdminWinnerToken,
      crossWorkspaceToken,
    ]);
    assert(auditRows.rows.every((row) => !row.entity_id.startsWith("agt_")));
    const runtimeRows = await admin.query<{
      input_summary: string | null;
      output_summary: string | null;
      raw_payload_hash: string | null;
    }>(
      `SELECT input_summary,output_summary,raw_payload_hash
      FROM runtime_events WHERE workspace_id=ANY($1::text[])`,
      [[WORKSPACE, FOREIGN_WORKSPACE]],
    );
    assertSafe(runtimeRows.rows, [
      firstToken,
      sessionToken,
      rotationBaseToken,
      rotatedToken,
      directSessionToken,
      lockOrderRotatedToken,
      lockOrderSessionToken,
      siblingToken,
      siblingRotationToken,
      siblingSessionToken,
      multiAdminWinnerToken,
      crossWorkspaceToken,
    ]);
    assert.equal(fetchCalls, 0);
    await assertStaticOwnership();

    console.log(JSON.stringify({
      ok: true,
      contract: "gateway_administration_postgres_v1",
      postgres_major: 16,
      schema_contract: migration.schema_contract,
      migration_count: migration.applied_count,
      human_owner_authority: true,
      non_admin_denied: true,
      csrf_and_origin_enforced: true,
      machine_credential_denied: true,
      concurrent_issue_attempts: issueAttempts.length,
      independent_human_sessions: concurrentOwners.length,
      one_time_token_responses: issued.length,
      concurrent_rotation_attempts: rotationAttempts.length,
      one_time_rotation_responses: rotated.length,
      rotation_at_enrollment_quota_is_net_zero: true,
      multi_admin_quota_race_single_winner: true,
      cross_workspace_agent_binding_race_single_winner: true,
      token_before_workspace_lock_order: true,
      parent_token_before_session_lock_order: true,
      child_session_before_audit_lock_order: true,
      sibling_token_workspace_before_agent_lock_order: true,
      denial_replay_is_terminal: true,
      full_request_idempotency_binding: true,
      opaque_ref_row_actions: true,
      child_session_revoke_cascade: true,
      direct_session_revoke: true,
      entitlement_fail_closed_and_audited: true,
      runtime_role_boundary_verified: true,
      runtime_entitlement_dml_granted: false,
      revocation_available_while_suspended: true,
      cross_workspace_denied: true,
      token_hash_only_at_rest: true,
      provider_calls: fetchCalls,
      python_proxy_performed: false,
      sqlite_used: false,
      credentials_omitted: true,
    }, null, 2));
  } finally {
    await closeControlPlanePoolForTests();
    restoreRuntimeEnvironment();
    globalThis.fetch = originalFetch;
    const restore = (
      key: string,
      value: string | undefined,
    ) => {
      if (value === undefined) {
        delete process.env[key];
      } else {
        process.env[key] = value;
      }
    };
    restore("AGENTOPS_ALLOWED_ORIGINS", originalOrigins);
    restore("AGENTOPS_HUMAN_SESSION_HMAC_KEY", originalHmac);
    restore(
      "AGENTOPS_POSTGRES_APPLICATION_NAME",
      originalApplicationName,
    );
    await roleFixture.cleanup();
  }
}

run().catch((error) => {
  console.error(error instanceof Error ? error.message : String(error));
  process.exitCode = 1;
});
