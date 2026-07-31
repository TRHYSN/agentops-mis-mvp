import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";

import { ControlPlaneHttpError } from "../src/server/controlPlane/http";
import { stableHash } from "../src/server/controlPlane/ledger";
import {
  issueWorkspaceEntitlementAdminChallengeWithClient,
  parseWorkspaceEntitlementAdminChallengeRequest,
  WORKSPACE_ENTITLEMENT_ADMIN_DATABASE_FUNCTIONS,
  WORKSPACE_ENTITLEMENT_ADMIN_CHALLENGE_CONTRACT,
  WORKSPACE_ENTITLEMENT_ADMIN_CHALLENGE_TTL_SECONDS,
  WORKSPACE_ENTITLEMENT_ADMINISTRATION_REQUEST_CONTRACT,
} from "../src/server/controlPlane/workspaceEntitlementAdminChallenges";

const WORKSPACE = "ws_challenge_contract";
const OPERATOR = "usr_challenge_operator";
const SESSION = "hsess_challenge_contract";
const TOKEN = "A".repeat(43);
const NOW = new Date("2026-07-31T08:00:00.000Z");
const ISSUED_AT = "2026-07-31T08:00:01.000Z";
const EXPIRES_AT = "2026-07-31T08:01:16.000Z";

function body(overrides: Record<string, unknown> = {}) {
  return {
    contract: WORKSPACE_ENTITLEMENT_ADMINISTRATION_REQUEST_CONTRACT,
    workspace_id: WORKSPACE,
    operator_user_id: OPERATOR,
    mode: "confirm",
    guard: {
      kind: "expected_revision",
      revision: "a".repeat(64),
    },
    configuration: {
      edition: "enterprise_byoc",
      status: "active",
      capabilities: {
        enrollment_issue: true,
        session_issue: true,
        run_start: true,
      },
      max_agents: 100,
      max_active_enrollments: 100,
      max_active_sessions_per_agent: 3,
      max_concurrent_runs: 20,
      max_monthly_runs: 10000,
      max_monthly_cost_usd: "1200.5",
      effective_at: "2026-07-31T00:00:00Z",
      expires_at: null,
    },
    ...overrides,
  };
}

function identity(role = "operator") {
  return {
    mode: "human_session" as const,
    sessionId: SESSION,
    sessionRef: "hsref_omitted",
    userId: OPERATOR,
    userName: "Challenge Operator",
    workspaceId: WORKSPACE,
    membershipRole: role,
  };
}

async function expectCode(code: string, work: () => Promise<unknown>) {
  await assert.rejects(work, (error: unknown) => (
    error instanceof ControlPlaneHttpError && error.code === code
  ));
}

async function main() {
  const request = parseWorkspaceEntitlementAdminChallengeRequest(
    WORKSPACE,
    body(),
  );
  assert.equal(
    request.contract,
    WORKSPACE_ENTITLEMENT_ADMINISTRATION_REQUEST_CONTRACT,
  );
  assert.equal(request.configuration.max_monthly_cost_usd, "1200.500000");
  assert.equal(
    request.configuration.effective_at,
    "2026-07-31T00:00:00.000Z",
  );

  const queries: Array<{ text: string; values: unknown[] }> = [];
  const requestSha256 = stableHash(request);
  const fakeClient = {
    async query<T>(text: string, values?: unknown[]) {
      queries.push({ text, values: values || [] });
      return {
        command: "SELECT",
        rowCount: 1,
        oid: 0,
        fields: [],
        rows: [{
          challenge: {
            contract: WORKSPACE_ENTITLEMENT_ADMIN_CHALLENGE_CONTRACT,
            challenge_id: "ech_contract_01",
            workspace_id: WORKSPACE,
            operator_user_id: OPERATOR,
            mode: "confirm",
            request_sha256: requestSha256,
            issued_at: ISSUED_AT,
            expires_at: EXPIRES_AT,
            single_use: true,
            session_revoked: true,
          },
        }] as T[],
      };
    },
  };
  const result = await issueWorkspaceEntitlementAdminChallengeWithClient(
    fakeClient,
    identity(),
    request,
    {
      runtimeApiSchema: "challenge_api_fixture",
      challengeToken: TOKEN,
      now: NOW,
    },
  );
  assert.equal(queries.length, 1);
  assert.match(
    queries[0].text,
    /^SELECT "challenge_api_fixture"\."agentops_issue_workspace_entitlement_admin_challenge_v11"\(/,
  );
  assert.deepEqual(queries[0].values.slice(0, 4), [
    SESSION,
    WORKSPACE,
    OPERATOR,
    "confirm",
  ]);
  assert.deepEqual(JSON.parse(String(queries[0].values[4])), request);
  assert.equal(
    queries[0].values[5],
    createHash("sha256").update(TOKEN, "utf8").digest("hex"),
  );
  assert.notEqual(queries[0].values[5], TOKEN);
  assert.equal(
    queries[0].values[6],
    `${WORKSPACE_ENTITLEMENT_ADMIN_CHALLENGE_TTL_SECONDS} seconds`,
  );
  assert.equal(result.status, 201);
  assert.equal(result.body.challenge_token, TOKEN);
  assert.equal(result.body.challenge_token_returned_once, true);
  assert.equal(result.body.long_lived_credentials_omitted, true);
  assert.equal(result.body.human_session_consumed, true);
  assert.equal(result.body.runtime_plan_apply_allowed, false);
  assert.equal(result.body.raw_config_omitted, true);
  const serialized = JSON.stringify(result.body);
  assert.doesNotMatch(serialized, /sessionId|session_id|HMAC|postgresql:|password/i);
  assert.doesNotMatch(serialized, /enterprise_byoc|max_monthly_cost_usd/);

  for (const role of ["operator", "owner", "workspace-admin"]) {
    await issueWorkspaceEntitlementAdminChallengeWithClient(
      fakeClient,
      identity(role),
      request,
      {
        runtimeApiSchema: "challenge_api_fixture",
        challengeToken: TOKEN,
        now: NOW,
      },
    );
  }
  await expectCode(
    "entitlement_challenge_role_forbidden",
    () => issueWorkspaceEntitlementAdminChallengeWithClient(
      fakeClient,
      identity("reviewer"),
      request,
      {
        runtimeApiSchema: "challenge_api_fixture",
        challengeToken: TOKEN,
        now: NOW,
      },
    ),
  );
  await expectCode(
    "entitlement_challenge_identity_mismatch",
    () => issueWorkspaceEntitlementAdminChallengeWithClient(
      fakeClient,
      { ...identity(), userId: "usr_other" },
      request,
      {
        runtimeApiSchema: "challenge_api_fixture",
        challengeToken: TOKEN,
        now: NOW,
      },
    ),
  );

  assert.throws(
    () => parseWorkspaceEntitlementAdminChallengeRequest(
      WORKSPACE,
      body({ unexpected: true }),
    ),
    (error: unknown) => (
      error instanceof ControlPlaneHttpError
      && error.code === "challenge_request_invalid"
    ),
  );
  assert.throws(
    () => parseWorkspaceEntitlementAdminChallengeRequest(
      WORKSPACE,
      body({
        guard: { kind: "none" },
      }),
    ),
    (error: unknown) => (
      error instanceof ControlPlaneHttpError
      && error.code === "confirm_guard_required"
    ),
  );
  const numericCost = body();
  (
    numericCost.configuration as Record<string, unknown>
  ).max_monthly_cost_usd = 1200.5;
  assert.throws(
    () => parseWorkspaceEntitlementAdminChallengeRequest(
      WORKSPACE,
      numericCost,
    ),
    (error: unknown) => (
      error instanceof ControlPlaneHttpError
      && error.code === "max_monthly_cost_usd_invalid"
    ),
  );

  const routeSource = await readFile(
    new URL(
      "../app/api/mis/workspaces/[workspaceId]/entitlement-admin/challenges/route.ts",
      import.meta.url,
    ),
    "utf8",
  );
  assert.match(routeSource, /controlPlaneMode\(\) !== "postgres"/);
  assert.match(routeSource, /maxBytes: 16 \* 1024/);
  assert.match(routeSource, /"Cache-Control": "no-store"/);
  assert.match(routeSource, /"Set-Cookie": result\.setCookie/);
  assert.doesNotMatch(routeSource, /proxyControlPlaneRequest|legacyPython/);

  process.stdout.write(`${JSON.stringify({
    ok: true,
    contract: WORKSPACE_ENTITLEMENT_ADMIN_CHALLENGE_CONTRACT,
    endpoint:
      "POST /api/mis/workspaces/:workspaceId/entitlement-admin/challenges",
    database_wrapper:
      `${WORKSPACE_ENTITLEMENT_ADMIN_DATABASE_FUNCTIONS.issue}(text,text,text,text,jsonb,text,interval)`,
    plan_wrapper: WORKSPACE_ENTITLEMENT_ADMIN_DATABASE_FUNCTIONS.plan,
    apply_wrapper: WORKSPACE_ENTITLEMENT_ADMIN_DATABASE_FUNCTIONS.apply,
    request_contract: WORKSPACE_ENTITLEMENT_ADMINISTRATION_REQUEST_CONTRACT,
    control_plane_url_environment:
      "AGENTOPS_ENTITLEMENT_CONTROL_PLANE_URL",
    control_plane_origin_environment:
      "AGENTOPS_ENTITLEMENT_CONTROL_PLANE_ORIGIN",
    non_loopback_https_required: true,
    loopback_http_local_only: true,
    ttl_seconds: WORKSPACE_ENTITLEMENT_ADMIN_CHALLENGE_TTL_SECONDS,
    trusted_roles: ["operator", "owner", "workspace-admin"],
    raw_token_persisted: false,
    human_session_consumed: true,
    runtime_plan_apply_allowed: false,
  })}\n`);
}

main().catch((error) => {
  process.stderr.write(`${error instanceof Error ? error.stack : String(error)}\n`);
  process.exitCode = 1;
});
