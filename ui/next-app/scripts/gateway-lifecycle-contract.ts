import assert from "node:assert/strict";
import { createHash, randomUUID } from "node:crypto";
import { readFile } from "node:fs/promises";

import { Client } from "pg";

import { closeControlPlanePoolForTests } from "../src/server/controlPlane/db";
import {
  createGatewaySession,
  getGatewayStatus,
  recordGatewayHeartbeat,
  registerGatewayAgent,
} from "../src/server/controlPlane/gatewayLifecycle";
import { ControlPlaneHttpError } from "../src/server/controlPlane/http";
import {
  POSTGRES_MIGRATION_MANIFEST,
  SCHEMA_CONTRACT,
} from "../src/server/controlPlane/schemaReadiness";
import {
  createPostgresRoleBoundaryFixture,
} from "./postgres-role-boundary-test-helper";

const baseDsn = String(process.env.AGENTOPS_POSTGRES_DSN || "").trim();

function sha(value: string) {
  return createHash("sha256").update(value, "utf8").digest("hex");
}

function gatewayRequest(
  method: "GET" | "POST",
  path: string,
  token: string,
  body?: Record<string, unknown>,
  workspaceId = "ws_gateway_a",
  agentId = "agt_gateway_a",
) {
  const headers = new Headers({
    authorization: `Bearer ${token}`,
    "content-type": "application/json",
    "x-agentops-agent-id": agentId,
    "x-agentops-workspace-id": workspaceId,
  });
  return new Request(`http://agentops.test/api/mis/agent-gateway/${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

async function expectCode(
  expected: string,
  work: () => Promise<unknown>,
) {
  try {
    await work();
  } catch (error) {
    assert(error instanceof ControlPlaneHttpError, `${expected}: wrong error type`);
    assert.equal(error.code, expected);
    return;
  }
  assert.fail(`${expected}: request unexpectedly passed`);
}

function assertSafePayload(
  payload: unknown,
  secrets: string[],
  forbiddenFragments: string[] = [],
) {
  const serialized = JSON.stringify(payload);
  for (const secret of secrets) {
    assert(!serialized.includes(secret), "response leaked a credential");
  }
  for (const fragment of forbiddenFragments) {
    assert(!serialized.includes(fragment), `response leaked ${fragment}`);
  }
}

async function assertSourceOwnership() {
  const routeUrls = [
    new URL("../app/api/mis/agent-gateway/status/route.ts", import.meta.url),
    new URL("../app/api/mis/agent-gateway/register/route.ts", import.meta.url),
    new URL("../app/api/mis/agent-gateway/heartbeat/route.ts", import.meta.url),
    new URL("../app/api/mis/agent-gateway/session/create/route.ts", import.meta.url),
  ];
  const lifecycleUrl = new URL(
    "../src/server/controlPlane/gatewayLifecycle.ts",
    import.meta.url,
  );
  const sources = await Promise.all(
    [...routeUrls, lifecycleUrl].map((url) => readFile(url, "utf8")),
  );
  for (const source of sources) {
    assert(!source.includes("proxyControlPlaneRequest"));
    assert(!source.includes("AGENTOPS_API_BASE"));
    assert(!source.includes("server.py"));
    assert(!/\bpython\b/i.test(source));
  }
  for (const routeSource of sources.slice(0, -1)) {
    assert(routeSource.includes('controlPlaneMode() === "proxy"'));
    assert.match(routeSource, /proxyFreeLocal(?:Read|Mutation)/);
  }
  const lifecycleSource = sources.at(-1);
  assert(lifecycleSource?.includes('controlPlaneMode() !== "postgres"'));
  assert(!lifecycleSource?.includes("proxyFreeLocal"));
}

async function seedFixture(
  admin: Client,
  parentToken: string,
  otherToken: string,
  expiredToken: string,
) {
  const now = new Date();
  const createdAt = new Date(now.getTime() - 60_000).toISOString();
  const future = new Date(now.getTime() + 3_600_000).toISOString();
  const past = new Date(now.getTime() - 1_000).toISOString();
  const tools = [
    "agent_gateway.audit",
    "agent_gateway.task",
    "hermes.execute",
  ];
  const scopes = ["agents:heartbeat", "agents:write", "tasks:read"];
  await admin.query(
    `INSERT INTO users(user_id,name,email,role,created_at)
    VALUES('usr_gateway_owner','Gateway Owner','gateway-owner@example.invalid','owner',$1)`,
    [createdAt],
  );
  await admin.query(
    `INSERT INTO agents(
      agent_id,name,role,description,runtime_type,model_provider,model_name,status,
      permission_level,allowed_tools,budget_limit_usd,owner_user_id,created_at,updated_at
    ) VALUES
      ('agt_gateway_a','Enrolled Agent A','Worker','Commercial enrollment','hermes',
       'hermes','hermes','idle','standard',$1,5,'usr_gateway_owner',$2,$2),
      ('agt_gateway_b','Enrolled Agent B','Worker','Commercial enrollment','openclaw',
       'openclaw','openclaw','idle','standard',$3,5,'usr_gateway_owner',$2,$2)`,
    [JSON.stringify(tools), createdAt, JSON.stringify([
      "agent_gateway.audit",
      "agent_gateway.task",
      "openclaw.execute",
    ])],
  );
  await admin.query(
    `INSERT INTO agent_gateway_tokens(
      token_id,token_hash,workspace_id,agent_id,scopes_json,status,label,
      heartbeat_timeout_sec,created_at,expires_at,revoked_at,last_used_at,last_heartbeat_at
    ) VALUES
      ('tok_gateway_a',$1,'ws_gateway_a','agt_gateway_a',$2,'active','A',300,$3,$4,NULL,NULL,NULL),
      ('tok_gateway_b',$5,'ws_gateway_b','agt_gateway_b',$2,'active','B',300,$3,$4,NULL,NULL,NULL),
      ('tok_gateway_expired',$6,'ws_gateway_b','agt_gateway_b',$2,'active','expired',300,$3,$7,NULL,NULL,NULL)`,
    [
      sha(parentToken),
      JSON.stringify(scopes),
      createdAt,
      future,
      sha(otherToken),
      sha(expiredToken),
      past,
    ],
  );
  await admin.query(
    `INSERT INTO workspace_entitlements(
      workspace_id,edition,status,capabilities_json,max_agents,
      max_active_enrollments,max_active_sessions_per_agent,max_monthly_runs,
      max_monthly_cost_usd,effective_at,expires_at,created_at,updated_at,
      updated_by_user_id
    ) VALUES
      ('ws_gateway_a','team_governance','active',$1::jsonb,10,10,10,100,100,
       $2,$3,$2,$2,'usr_gateway_owner'),
      ('ws_gateway_b','team_governance','active',$1::jsonb,10,10,10,100,100,
       $2,$3,$2,$2,'usr_gateway_owner')`,
    [
      JSON.stringify({
        enrollment_issue: true,
        session_issue: true,
        run_start: true,
      }),
      createdAt,
      future,
    ],
  );
}

async function runContract() {
  assert(baseDsn, "AGENTOPS_POSTGRES_DSN is required");
  await assertSourceOwnership();
  const roleFixture = await createPostgresRoleBoundaryFixture(
    baseDsn,
    "gateway_lifecycle",
  );
  const admin = roleFixture.owner;
  const restoreRuntimeEnvironment =
    roleFixture.activateRuntimeEnvironment();
  const parentToken = `contract_parent_token_${randomUUID()}`;
  const otherToken = `contract_other_token_${randomUUID()}`;
  const expiredToken = `contract_expired_token_${randomUUID()}`;
  const rawCanary = `raw_prompt_${randomUUID()}`;
  try {
    const migration = roleFixture.migration;
    assert.equal(migration.schema_contract, SCHEMA_CONTRACT);
    assert.equal(migration.applied_count, POSTGRES_MIGRATION_MANIFEST.length);
    assert.equal(migration.manifest_count, POSTGRES_MIGRATION_MANIFEST.length);
    await seedFixture(admin, parentToken, otherToken, expiredToken);

    process.env.AGENTOPS_POSTGRES_POOL_MAX = "16";

    const tokenStatus = await getGatewayStatus(
      gatewayRequest("GET", "status", parentToken),
    );
    assert.equal(tokenStatus.status, 200);
    assert.equal(tokenStatus.body.status, "ready");
    assert.equal(
      (tokenStatus.body.auth as Record<string, unknown>).mode,
      "agent_token",
    );
    assert.equal(
      (tokenStatus.body.auth as Record<string, unknown>).heartbeat_state,
      "never_seen",
    );
    assertSafePayload(tokenStatus.body, [parentToken, baseDsn], [
      "tok_gateway_a",
      "token_hash",
      "session_hash",
    ]);

    await expectCode("forbidden", () =>
      getGatewayStatus(
        gatewayRequest(
          "GET",
          "status",
          parentToken,
          undefined,
          "ws_gateway_b",
        ),
      ));
    await expectCode("forbidden", () =>
      getGatewayStatus(
        gatewayRequest(
          "GET",
          "status",
          parentToken,
          undefined,
          "ws_gateway_a",
          "agt_gateway_b",
        ),
      ));

    const sessionBody = {
      workspace_id: "ws_gateway_a",
      agent_id: "agt_gateway_a",
      ttl_sec: 900,
      scopes: ["agents:heartbeat", "agents:write"],
      request_id: "session_contract_concurrent",
    };
    const concurrentSessions = await Promise.all(
      Array.from({ length: 8 }, () =>
        createGatewaySession(
          gatewayRequest("POST", "session/create", parentToken, sessionBody),
        )),
    );
    const issuedSessions = concurrentSessions.filter(
      (result) => typeof result.body.session_token === "string",
    );
    assert.equal(issuedSessions.length, 1, "session token must be visible once");
    assert.equal(
      concurrentSessions.filter((result) => result.body.replayed === true).length,
      7,
    );
    const sessionToken = String(issuedSessions[0].body.session_token);
    const sessionId = String(issuedSessions[0].body.session_id);
    const sessionRow = await admin.query<{
      session_hash: string;
      parent_token_id: string;
      workspace_id: string;
      agent_id: string;
      scopes_json: string;
    }>(
      `SELECT session_hash,parent_token_id,workspace_id,agent_id,scopes_json
      FROM agent_gateway_sessions WHERE session_id=$1`,
      [sessionId],
    );
    assert.equal(sessionRow.rowCount, 1);
    assert.equal(sessionRow.rows[0].session_hash, sha(sessionToken));
    assert.equal(sessionRow.rows[0].parent_token_id, "tok_gateway_a");
    assert.equal(sessionRow.rows[0].workspace_id, "ws_gateway_a");
    assert.equal(sessionRow.rows[0].agent_id, "agt_gateway_a");
    assert(!JSON.stringify(sessionRow.rows[0]).includes(sessionToken));
    const replay = await createGatewaySession(
      gatewayRequest("POST", "session/create", parentToken, sessionBody),
    );
    assert.equal(replay.body.replayed, true);
    assert(!Object.prototype.hasOwnProperty.call(replay.body, "session_token"));
    assert.equal(replay.body.token_omitted, true);
    await admin.query(
      `UPDATE workspace_entitlements SET status='suspended',updated_at=clock_timestamp()
      WHERE workspace_id='ws_gateway_a'`,
    );
    const replayWhileSuspended = await createGatewaySession(
      gatewayRequest("POST", "session/create", parentToken, sessionBody),
    );
    assert.equal(replayWhileSuspended.status, 200);
    assert.equal(replayWhileSuspended.body.replayed, true);
    const entitlementDenied = await createGatewaySession(
      gatewayRequest("POST", "session/create", parentToken, {
        ...sessionBody,
        request_id: "session_contract_entitlement_denied",
      }),
    );
    assert.equal(entitlementDenied.status, 403);
    assert.equal(entitlementDenied.body.error, "workspace_entitlement_denied");
    assert.equal(
      (entitlementDenied.body.entitlement_decision as Record<string, unknown>)
        .reason_code,
      "entitlement_suspended",
    );
    assert.equal(entitlementDenied.body.session_token_generated, false);
    assert.equal(Object.hasOwn(entitlementDenied.body, "session_token"), false);
    const entitlementDenialAudit = await admin.query<{
      count: string;
      metadata_json: string;
    }>(
      `SELECT count(*)::text AS count,max(metadata_json) AS metadata_json
      FROM audit_logs
      WHERE workspace_id='ws_gateway_a'
        AND action='agent_gateway.session_entitlement_denied'`,
    );
    assert.equal(entitlementDenialAudit.rows[0].count, "1");
    assert.match(
      entitlementDenialAudit.rows[0].metadata_json,
      /entitlement_suspended/,
    );
    await admin.query(
      `UPDATE workspace_entitlements SET status='active',updated_at=clock_timestamp()
      WHERE workspace_id='ws_gateway_a'`,
    );

    const sessionStatus = await getGatewayStatus(
      gatewayRequest("GET", "status", sessionToken),
    );
    assert.equal(
      (sessionStatus.body.auth as Record<string, unknown>).mode,
      "agent_session",
    );
    assertSafePayload(sessionStatus.body, [parentToken, sessionToken], [
      sessionId,
      "tok_gateway_a",
      "session_hash",
      "token_hash",
    ]);
    await expectCode("session_parent_token_required", () =>
      createGatewaySession(
        gatewayRequest("POST", "session/create", sessionToken, {
          workspace_id: "ws_gateway_a",
          agent_id: "agt_gateway_a",
          ttl_sec: 60,
        }),
      ));
    await expectCode("session_scope_escalation", () =>
      createGatewaySession(
        gatewayRequest("POST", "session/create", parentToken, {
          workspace_id: "ws_gateway_a",
          agent_id: "agt_gateway_a",
          ttl_sec: 60,
          scopes: ["agents:write", "runs:write"],
        }),
      ));
    await expectCode("session_scope_invalid", () =>
      createGatewaySession(
        gatewayRequest("POST", "session/create", parentToken, {
          workspace_id: "ws_gateway_a",
          agent_id: "agt_gateway_a",
          ttl_sec: 60,
          scopes: ["unsupported:scope"],
        }),
      ));
    await expectCode("session_request_binding_conflict", () =>
      createGatewaySession(
        gatewayRequest("POST", "session/create", parentToken, {
          ...sessionBody,
          ttl_sec: 300,
        }),
      ));

    const registration = {
      workspace_id: "ws_gateway_a",
      agent_id: "agt_gateway_a",
      name: "Local Agent Worker",
      role: "Local hermes Adapter Worker",
      runtime_type: "hermes",
      model_provider: "hermes",
      model_name: "hermes",
      permission_level: "standard",
      allowed_tools: [
        "agent_gateway.task",
        "hermes.execute",
        "agent_gateway.audit",
      ],
      budget_limit_usd: 5,
      description: "Installable commercial worker daemon.",
      request_id: "register_contract",
    };
    const registrations = await Promise.all(
      Array.from({ length: 8 }, () =>
        registerGatewayAgent(
          gatewayRequest("POST", "register", sessionToken, registration),
        )),
    );
    assert.equal(
      registrations.filter((result) => result.body.outcome === "updated").length,
      1,
    );
    assert.equal(
      registrations.filter((result) => result.body.outcome === "unchanged").length,
      7,
    );
    await expectCode("agent_permission_escalation", () =>
      registerGatewayAgent(
        gatewayRequest("POST", "register", sessionToken, {
          ...registration,
          permission_level: "admin",
        }),
      ));
    await expectCode("agent_tool_escalation", () =>
      registerGatewayAgent(
        gatewayRequest("POST", "register", sessionToken, {
          ...registration,
          allowed_tools: [...registration.allowed_tools, "shell.root"],
        }),
      ));
    await expectCode("registration_runtime_mismatch", () =>
      registerGatewayAgent(
        gatewayRequest("POST", "register", sessionToken, {
          ...registration,
          runtime_type: "codex",
        }),
      ));
    await expectCode("forbidden", () =>
      registerGatewayAgent(
        gatewayRequest(
          "POST",
          "register",
          sessionToken,
          { ...registration, workspace_id: "ws_gateway_b" },
          "ws_gateway_a",
        ),
      ));

    const heartbeatBody = {
      workspace_id: "ws_gateway_a",
      agent_id: "agt_gateway_a",
      status: "running",
      summary: `Bearer contract_session_should_not_persist ${rawCanary}`,
      runtime_type: "hermes",
    };
    const heartbeats = await Promise.all(
      Array.from({ length: 8 }, () =>
        recordGatewayHeartbeat(
          gatewayRequest("POST", "heartbeat", sessionToken, heartbeatBody),
        )),
    );
    assert(heartbeats.every((result) => result.body.status === "running"));
    const heartbeatToken = await admin.query<{
      last_heartbeat_at: string | null;
    }>(
      "SELECT last_heartbeat_at FROM agent_gateway_tokens WHERE token_id='tok_gateway_a'",
    );
    assert(heartbeatToken.rows[0].last_heartbeat_at);
    const onlineStatus = await getGatewayStatus(
      gatewayRequest("GET", "status", parentToken),
    );
    assert.equal(
      (onlineStatus.body.auth as Record<string, unknown>).heartbeat_state,
      "online",
    );
    await admin.query(
      "UPDATE agent_gateway_tokens SET last_heartbeat_at=$1 WHERE token_id='tok_gateway_a'",
      [new Date(Date.now() - 600_000).toISOString()],
    );
    const staleStatus = await getGatewayStatus(
      gatewayRequest("GET", "status", parentToken),
    );
    assert.equal(
      (staleStatus.body.auth as Record<string, unknown>).heartbeat_state,
      "stale",
    );
    const heartbeatAudits = await admin.query<{ count: string }>(
      `SELECT count(*)::text AS count FROM audit_logs
      WHERE action='agent_gateway.heartbeat_state_change'
      AND entity_id='agt_gateway_a'`,
    );
    assert.equal(heartbeatAudits.rows[0].count, "1");
    const allAuditMetadata = await admin.query<{ metadata_json: string }>(
      "SELECT metadata_json FROM audit_logs",
    );
    assert(!JSON.stringify(allAuditMetadata.rows).includes(rawCanary));
    assert(!JSON.stringify(allAuditMetadata.rows).includes("contract_session_should_not_persist"));
    await expectCode("forbidden", () =>
      recordGatewayHeartbeat(
        gatewayRequest(
          "POST",
          "heartbeat",
          sessionToken,
          { ...heartbeatBody, workspace_id: "ws_gateway_b" },
          "ws_gateway_a",
        ),
      ));
    await expectCode("heartbeat_runtime_mismatch", () =>
      recordGatewayHeartbeat(
        gatewayRequest("POST", "heartbeat", sessionToken, {
          ...heartbeatBody,
          runtime_type: "openclaw",
        }),
      ));

    const expiring = await createGatewaySession(
      gatewayRequest("POST", "session/create", parentToken, {
        workspace_id: "ws_gateway_a",
        agent_id: "agt_gateway_a",
        ttl_sec: 1,
        scopes: ["agents:heartbeat"],
        request_id: "session_contract_expiry",
      }),
    );
    const expiringToken = String(expiring.body.session_token);
    await admin.query(
      "UPDATE agent_gateway_sessions SET expires_at=$1 WHERE session_id=$2",
      [new Date(Date.now() - 1_000).toISOString(), expiring.body.session_id],
    );
    await expectCode("unauthorized", () =>
      getGatewayStatus(gatewayRequest("GET", "status", expiringToken)));
    const expiredSession = await admin.query<{ status: string }>(
      "SELECT status FROM agent_gateway_sessions WHERE session_id=$1",
      [expiring.body.session_id],
    );
    assert.equal(expiredSession.rows[0].status, "expired");

    await expectCode("unauthorized", () =>
      getGatewayStatus(
        gatewayRequest(
          "GET",
          "status",
          expiredToken,
          undefined,
          "ws_gateway_b",
          "agt_gateway_b",
        ),
      ));
    const expiredParent = await admin.query<{ status: string }>(
      "SELECT status FROM agent_gateway_tokens WHERE token_id='tok_gateway_expired'",
    );
    assert.equal(expiredParent.rows[0].status, "expired");

    const revokedSession = await createGatewaySession(
      gatewayRequest("POST", "session/create", parentToken, {
        workspace_id: "ws_gateway_a",
        agent_id: "agt_gateway_a",
        ttl_sec: 300,
        scopes: ["agents:heartbeat"],
        request_id: "session_contract_parent_revoke",
      }),
    );
    const revokedSessionToken = String(revokedSession.body.session_token);
    await admin.query(
      `UPDATE agent_gateway_tokens
      SET status='revoked',revoked_at=$1 WHERE token_id='tok_gateway_a'`,
      [new Date().toISOString()],
    );
    await expectCode("unauthorized", () =>
      getGatewayStatus(gatewayRequest("GET", "status", revokedSessionToken)));
    const revokedSessionRow = await admin.query<{ status: string }>(
      "SELECT status FROM agent_gateway_sessions WHERE session_id=$1",
      [revokedSession.body.session_id],
    );
    assert.equal(revokedSessionRow.rows[0].status, "revoked");

    const version = await admin.query<{ server_version: string }>(
      "SHOW server_version",
    );
    const sessionCount = await admin.query<{ count: string }>(
      "SELECT count(*)::text AS count FROM agent_gateway_sessions",
    );
    const registrationAudits = await admin.query<{ count: string }>(
      `SELECT count(*)::text AS count FROM audit_logs
      WHERE action='agent_gateway.register' AND entity_id='agt_gateway_a'`,
    );
    assert.equal(registrationAudits.rows[0].count, "1");
    console.log(JSON.stringify({
      contract: "gateway_lifecycle_postgres_v1",
      postgres_version: version.rows[0].server_version,
      fresh_schema: true,
      lifecycle_routes: 4,
      concurrent_session_attempts: concurrentSessions.length,
      one_time_session_token_responses: issuedSessions.length,
      concurrent_registration_attempts: registrations.length,
      concurrent_heartbeat_attempts: heartbeats.length,
      session_rows: Number(sessionCount.rows[0].count),
      cross_workspace_blocked: true,
      expiry_enforced: true,
      parent_revocation_enforced: true,
      entitlement_fail_closed_and_audited: true,
      idempotent_replay_precedes_entitlement_check: true,
      raw_content_persisted: false,
      python_started: false,
      token_omitted: true,
    }, null, 2));
  } finally {
    await closeControlPlanePoolForTests();
    restoreRuntimeEnvironment();
    await roleFixture.cleanup();
  }
}

await runContract();
