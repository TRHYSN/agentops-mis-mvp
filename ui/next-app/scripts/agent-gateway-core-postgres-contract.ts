import assert from "node:assert/strict";
import { createHash, randomBytes } from "node:crypto";
import { readFile } from "node:fs/promises";
import http from "node:http";

import { Client } from "pg";

import {
  agentPlanVerificationHash,
  computeAgentPlanHash,
} from "../src/server/controlPlane/agentPlanContract";
import {
  createAgentGatewayPlan,
  createAgentGatewayPlanEvidenceManifest,
  getAgentGatewayPlan,
} from "../src/server/controlPlane/agentGatewayPlans";
import {
  recordAgentGatewayArtifact,
  recordAgentGatewayToolCall,
  submitAgentGatewayEvaluation,
} from "../src/server/controlPlane/agentGatewayEvidence";
import { emitAgentAudit } from "../src/server/controlPlane/agentGatewayEvidenceSupport";
import { costUsdExact } from "../src/server/controlPlane/costProjection";
import {
  heartbeatAgentGatewayRun,
  startAgentGatewayRun,
} from "../src/server/controlPlane/agentGatewayRuns";
import { recordGatewayHeartbeat } from "../src/server/controlPlane/gatewayLifecycle";
import {
  claimAgentGatewayTask,
  getAgentGatewayTask,
  pullAgentGatewayTasks,
} from "../src/server/controlPlane/agentGatewayTasks";
import { closeControlPlanePoolForTests } from "../src/server/controlPlane/db";
import { ControlPlaneHttpError } from "../src/server/controlPlane/http";
import { runPostgresSchemaCommand } from "../src/server/controlPlane/schemaReadiness";

const baseDsn = String(process.env.AGENTOPS_POSTGRES_DSN || "").trim();
const schema = `agentops_gateway_core_${randomBytes(6).toString("hex")}`;
const token = `contract_token_${randomBytes(18).toString("hex")}`;
const siblingToken = `contract_sibling_token_${randomBytes(18).toString("hex")}`;
const otherToken = `contract_other_token_${randomBytes(18).toString("hex")}`;
const foreignToken = `contract_foreign_token_${randomBytes(18).toString("hex")}`;
const session = `contract_session_${randomBytes(18).toString("hex")}`;
const workspaceId = "ws_gateway_core";
const otherWorkspaceId = "ws_gateway_foreign";
const postgresApplicationName =
  `agentops-core-${randomBytes(6).toString("hex")}`;
const EXACT_COST_BOUNDARIES = [
  "999999999999.999999",
  "9999999999.999999",
] as const;
let pythonObserverRequests = 0;

function sha(value: string) {
  return createHash("sha256").update(value, "utf8").digest("hex");
}

function scopedDsn() {
  const parsed = new URL(baseDsn);
  parsed.searchParams.set(
    "options",
    `-csearch_path=${schema} -cstatement_timeout=8000 -clock_timeout=6000`,
  );
  parsed.searchParams.set("application_name", postgresApplicationName);
  return parsed.toString();
}

function roleDsn(role: string, password: string) {
  const parsed = new URL(scopedDsn());
  parsed.username = role;
  parsed.password = password;
  return parsed.toString();
}

function headers(
  credential: string | null,
  workspace = workspaceId,
  agent = "agt_gateway_core",
) {
  const value = new Headers({
    "content-type": "application/json",
    "x-agentops-workspace-id": workspace,
    "x-agentops-agent-id": agent,
  });
  if (credential) value.set("authorization", `Bearer ${credential}`);
  return value;
}

function request(
  method: "GET" | "POST",
  path: string,
  credential: string | null,
  body?: Record<string, unknown>,
  workspace = workspaceId,
  agent = "agt_gateway_core",
) {
  return new Request(`http://agentops.test${path}`, {
    method,
    headers: headers(credential, workspace, agent),
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
      [postgresApplicationName, blockerPid],
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
      [postgresApplicationName, lockKey],
    );
    if (result.rows[0]?.pid) return result.rows[0].pid;
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
  throw new Error("expected_target_postgres_advisory_lock_waiter");
}

type EntitlementDeniedResult = {
  status: number;
  body: {
    ok: boolean;
    error: string;
    reason_code: string;
    raw_config_omitted: boolean;
    entitlement_decision: {
      decision: string;
      reason_code: string;
      raw_config_omitted: boolean;
      credentials_omitted: boolean;
      entitlement: {
        raw_config_omitted: boolean;
      };
    };
  };
};

type RunStartAllowedResult = {
  status: number;
  body: {
    ok: true;
    outcome: "created" | "unchanged";
    run: {
      agent_plan_id: string | null;
      plan_hash: string | null;
      model_provider: string | null;
    };
  };
};

const RAW_ENTITLEMENT_CONFIG_KEYS = [
  "capabilities_json",
  "max_agents",
  "max_active_enrollments",
  "max_active_sessions_per_agent",
  "max_monthly_runs",
  "max_monthly_cost_usd",
  "effective_at",
  "expires_at",
];

function assertEntitlementDenied(
  result: Awaited<ReturnType<typeof startAgentGatewayRun>>,
  expectedReason: string,
) {
  const denied = result as EntitlementDeniedResult;
  assert.equal(denied.status, 403);
  assert.equal(denied.body.ok, false);
  assert.equal(denied.body.error, "workspace_entitlement_denied");
  assert.equal(denied.body.reason_code, expectedReason);
  assert.equal(denied.body.raw_config_omitted, true);
  assert.equal(denied.body.entitlement_decision.decision, "deny");
  assert.equal(denied.body.entitlement_decision.reason_code, expectedReason);
  assert.equal(denied.body.entitlement_decision.raw_config_omitted, true);
  assert.equal(denied.body.entitlement_decision.credentials_omitted, true);
  assert.equal(denied.body.entitlement_decision.entitlement.raw_config_omitted, true);
  const serialized = JSON.stringify(denied.body);
  for (const key of RAW_ENTITLEMENT_CONFIG_KEYS) {
    assert.equal(serialized.includes(key), false, `response leaked raw entitlement key ${key}`);
  }
}

function assertRunStartAllowed(
  result: Awaited<ReturnType<typeof startAgentGatewayRun>>,
  expectedStatus: number,
): asserts result is Awaited<ReturnType<typeof startAgentGatewayRun>> & RunStartAllowedResult {
  const allowed = result as RunStartAllowedResult;
  assert.equal(allowed.status, expectedStatus);
  assert.equal(allowed.body.ok, true);
}

async function setRunEntitlement(
  client: Client,
  options: {
    status?: "active" | "inactive" | "suspended" | "expired";
    runStart?: boolean;
    maxMonthlyRuns?: number;
    maxConcurrentRuns?: number;
  } = {},
) {
  const now = new Date().toISOString();
  const effectiveAt = new Date(Date.now() - 60_000).toISOString();
  await client.query(
    `INSERT INTO workspace_entitlements(
      workspace_id,edition,status,capabilities_json,max_agents,
      max_active_enrollments,max_active_sessions_per_agent,max_monthly_runs,
      max_monthly_cost_usd,effective_at,expires_at,created_at,updated_at,
      updated_by_user_id,max_concurrent_runs
    ) VALUES($1,'team_governance',$2,$3,10,10,10,$4,100,$5,NULL,$6,$6,
      'usr_gateway_core',$7)
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
      expires_at=NULL,
      updated_at=EXCLUDED.updated_at,
      updated_by_user_id=EXCLUDED.updated_by_user_id`,
    [
      workspaceId,
      options.status || "active",
      JSON.stringify({
        enrollment_issue: true,
        session_issue: true,
        run_start: options.runStart ?? true,
      }),
      options.maxMonthlyRuns ?? 10,
      effectiveAt,
      now,
      options.maxConcurrentRuns ?? 10,
    ],
  );
}

async function seed(client: Client) {
  const now = new Date().toISOString();
  const future = new Date(Date.now() + 3_600_000).toISOString();
  const scopes = JSON.stringify([
    "tasks:read",
    "tasks:claim",
    "agent_plans:read",
    "agent_plans:write",
    "runs:write",
    "toolcalls:write",
    "evaluations:submit",
    "artifacts:write",
    "plan_evidence:write",
    "audit:write",
    "agents:heartbeat",
  ]);
  await client.query(
    `INSERT INTO users(user_id,name,email,role,created_at)
    VALUES('usr_gateway_core','Gateway Contract','contract@example.invalid','owner',$1)`,
    [now],
  );
  for (const [agentId, name] of [
    ["agt_gateway_core", "Gateway Core"],
    ["agt_gateway_other", "Gateway Other"],
  ]) {
    await client.query(
      `INSERT INTO agents(
        agent_id,name,role,description,runtime_type,model_provider,model_name,
        status,permission_level,allowed_tools,budget_limit_usd,owner_user_id,
        created_at,updated_at
      ) VALUES($1,$2,'worker',NULL,'hermes','hermes','contract-model',
        'idle','standard','[]',0,'usr_gateway_core',$3,$3)`,
      [agentId, name, now],
    );
  }
  for (const row of [
    ["tok_gateway_core", sha(token), workspaceId, "agt_gateway_core"],
    ["tok_gateway_sibling", sha(siblingToken), workspaceId, "agt_gateway_core"],
    ["tok_gateway_other", sha(otherToken), workspaceId, "agt_gateway_other"],
    ["tok_gateway_foreign", sha(foreignToken), otherWorkspaceId, "agt_gateway_core"],
  ]) {
    await client.query(
      `INSERT INTO agent_gateway_tokens(
        token_id,token_hash,workspace_id,agent_id,scopes_json,status,label,
        heartbeat_timeout_sec,created_at,expires_at,revoked_at,last_used_at,
        last_heartbeat_at
      ) VALUES($1,$2,$3,$4,$5,'active','contract',300,$6,$7,NULL,NULL,NULL)`,
      [...row, scopes, now, future],
    );
  }
  await client.query(
    `INSERT INTO agent_gateway_sessions(
      session_id,session_hash,parent_token_id,workspace_id,agent_id,scopes_json,
      status,created_at,expires_at,revoked_at,last_used_at
    ) VALUES(
      'sess_gateway_core',$1,'tok_gateway_core',$2,'agt_gateway_core',$3,
      'active',$4,$5,NULL,NULL
    )`,
    [sha(session), workspaceId, scopes, now, future],
  );
  await setRunEntitlement(client);
  for (const [taskId, title] of [
    ["tsk_gateway_race", "Concurrent claim"],
    ["tsk_gateway_lock_order", "Run and heartbeat lock order"],
    ["tsk_gateway_core", "Production gateway core"],
  ]) {
    await client.query(
      `INSERT INTO tasks(
        task_id,workspace_id,title,description,requester_id,owner_agent_id,
        collaborator_agent_ids,status,priority,due_date,acceptance_criteria,
        risk_level,budget_limit_usd,created_at,updated_at
      ) VALUES($1,$2,$3,'Contract task','usr_gateway_core',NULL,'[]',
        'planned','high',NULL,'Write bounded immutable evidence.','medium',0,$4,$4)`,
      [taskId, workspaceId, title, now],
    );
  }
  await client.query(
    `INSERT INTO tasks(
      task_id,workspace_id,title,description,requester_id,owner_agent_id,
      collaborator_agent_ids,status,priority,due_date,acceptance_criteria,
      risk_level,budget_limit_usd,created_at,updated_at
    ) VALUES(
      'tsk_gateway_entitlement_missing',$1,'Missing entitlement',
      'Fail closed before creating a run.','usr_gateway_core',
      'agt_gateway_core','[]','planned','high',NULL,
      'Reject a run without a workspace entitlement.','medium',0,$2,$2
    )`,
    [otherWorkspaceId, now],
  );
}

async function runContract() {
  assert.ok(baseDsn, "AGENTOPS_POSTGRES_DSN is required");
  const runtimeApiSchema = `api_core_${randomBytes(8).toString("hex")}`;
  const runtimeRole = `rt_core_${randomBytes(8).toString("hex")}`;
  const runtimePassword = randomBytes(24).toString("base64url");
  const entitlementAdminRole = `ea_core_${randomBytes(8).toString("hex")}`;
  const entitlementAdminPassword = randomBytes(24).toString("base64url");
  const admin = new Client({ connectionString: baseDsn });
  await admin.connect();
  const observer = http.createServer((_request, response) => {
    pythonObserverRequests += 1;
    response.writeHead(500).end();
  });
  await new Promise<void>((resolve) => observer.listen(0, "127.0.0.1", resolve));
  const address = observer.address();
  assert.ok(address && typeof address === "object");
  const originalEnv = {
    dsn: process.env.AGENTOPS_POSTGRES_DSN,
    deployment: process.env.AGENTOPS_DEPLOYMENT_MODE,
    mode: process.env.AGENTOPS_CONTROL_PLANE_MODE,
    upstream: process.env.AGENTOPS_API_BASE,
    applicationName: process.env.AGENTOPS_POSTGRES_APPLICATION_NAME,
    schema: process.env.AGENTOPS_POSTGRES_SCHEMA,
    runtimeApiSchema: process.env.AGENTOPS_POSTGRES_RUNTIME_API_SCHEMA,
    runtimeRole: process.env.AGENTOPS_POSTGRES_RUNTIME_ROLE,
  };
  try {
    await admin.query(`CREATE SCHEMA "${schema}"`);
    process.env.AGENTOPS_DEPLOYMENT_MODE = "production";
    process.env.AGENTOPS_CONTROL_PLANE_MODE = "postgres";
    process.env.AGENTOPS_API_BASE = `http://127.0.0.1:${address.port}/api`;
    process.env.AGENTOPS_POSTGRES_APPLICATION_NAME =
      postgresApplicationName;
    await runPostgresSchemaCommand(
      "migrate",
      {
        connectionString: scopedDsn(),
        applicationSchema: schema,
        runtimeApiSchema,
        runtimeRole,
        runtimePassword,
        entitlementAdminRole,
        entitlementAdminPassword,
        provisionRoleBoundary: true,
      },
    );
    const client = new Client({ connectionString: scopedDsn() });
    await client.connect();
    try {
      const exactCosts = await client.query<{ cost_usd: string }>(
        `SELECT cost_usd::numeric(18,6)::text AS cost_usd
        FROM unnest($1::text[]) WITH ORDINALITY AS value(cost_usd, ordinal)
        ORDER BY ordinal`,
        [[...EXACT_COST_BOUNDARIES]],
      );
      assert.deepEqual(
        exactCosts.rows.map((row) => costUsdExact(row.cost_usd)),
        [...EXACT_COST_BOUNDARIES],
      );
      assert.deepEqual(
        exactCosts.rows.map((row) => Number(row.cost_usd)),
        EXACT_COST_BOUNDARIES.map((value) => Number(value)),
      );
      const evidenceOwnerSource = await readFile(
        new URL(
          "../src/server/controlPlane/agentGatewayEvidence.ts",
          import.meta.url,
        ),
        "utf8",
      );
      assert.match(
        evidenceOwnerSource,
        /function runSnapshot[\s\S]*?cost_usd_exact:\s*costUsdExact\(row\.cost_usd\)/,
      );
      await seed(client);
    } finally {
      await client.end();
    }
    process.env.AGENTOPS_POSTGRES_DSN = roleDsn(
      runtimeRole,
      runtimePassword,
    );
    process.env.AGENTOPS_POSTGRES_SCHEMA = schema;
    process.env.AGENTOPS_POSTGRES_RUNTIME_API_SCHEMA = runtimeApiSchema;
    process.env.AGENTOPS_POSTGRES_RUNTIME_ROLE = runtimeRole;
    const runtimeAclClient = new Client({
      connectionString: roleDsn(runtimeRole, runtimePassword),
    });
    await runtimeAclClient.connect();
    try {
      const entitlementAcl = await runtimeAclClient.query<{
        select_allowed: boolean;
        insert_allowed: boolean;
        update_allowed: boolean;
        delete_allowed: boolean;
        truncate_allowed: boolean;
      }>(
        `SELECT
          has_table_privilege(current_user,relation_row.oid,'SELECT')
            AS select_allowed,
          has_table_privilege(current_user,relation_row.oid,'INSERT')
            AS insert_allowed,
          has_table_privilege(current_user,relation_row.oid,'UPDATE')
            AS update_allowed,
          has_table_privilege(current_user,relation_row.oid,'DELETE')
            AS delete_allowed,
          has_table_privilege(current_user,relation_row.oid,'TRUNCATE')
            AS truncate_allowed
        FROM pg_class relation_row
        JOIN pg_namespace namespace_row
          ON namespace_row.oid=relation_row.relnamespace
        WHERE namespace_row.nspname=$1
          AND relation_row.relname='workspace_entitlements'`,
        [schema],
      );
      assert.equal(entitlementAcl.rows[0]?.select_allowed, true);
      assert.equal(entitlementAcl.rows[0]?.insert_allowed, false);
      assert.equal(entitlementAcl.rows[0]?.update_allowed, false);
      assert.equal(entitlementAcl.rows[0]?.delete_allowed, false);
      assert.equal(entitlementAcl.rows[0]?.truncate_allowed, false);
      const entitlementRead = await runtimeAclClient.query<{ count: string }>(
        "SELECT COUNT(*)::text AS count FROM workspace_entitlements",
      );
      assert.equal(Number(entitlementRead.rows[0]?.count || 0) > 0, true);
    } finally {
      await runtimeAclClient.end();
    }

    await expectCode(
      "unauthorized",
      () => getAgentGatewayTask(
        request("GET", "/api/mis/agent-gateway/tasks/tsk_gateway_core", null),
        "tsk_gateway_core",
      ),
    );
    const sessionRead = await getAgentGatewayTask(
      request(
        "GET",
        "/api/mis/agent-gateway/tasks/tsk_gateway_core",
        session,
      ),
      "tsk_gateway_core",
    );
    assert.equal(sessionRead.task.task_id, "tsk_gateway_core");
    await expectCode(
      "forbidden",
      () => getAgentGatewayTask(
        request(
          "GET",
          "/api/mis/agent-gateway/tasks/tsk_gateway_core",
          foreignToken,
          undefined,
          workspaceId,
        ),
        "tsk_gateway_core",
      ),
    );

    const pulled = await pullAgentGatewayTasks(
      request(
        "GET",
        "/api/mis/agent-gateway/tasks/pull?limit=5&status=planned&task_id=tsk_gateway_core",
        token,
      ),
    );
    assert.deepEqual(
      pulled.tasks.map((task) => task.task_id),
      ["tsk_gateway_core"],
    );

    const race = await Promise.allSettled([
      claimAgentGatewayTask(
        request(
          "POST",
          "/api/mis/agent-gateway/tasks/tsk_gateway_race/claim",
          token,
          { workspace_id: workspaceId, agent_id: "agt_gateway_core" },
        ),
        "tsk_gateway_race",
      ),
      claimAgentGatewayTask(
        request(
          "POST",
          "/api/mis/agent-gateway/tasks/tsk_gateway_race/claim",
          otherToken,
          { workspace_id: workspaceId, agent_id: "agt_gateway_other" },
          workspaceId,
          "agt_gateway_other",
        ),
        "tsk_gateway_race",
      ),
    ]);
    assert.equal(race.filter((result) => result.status === "fulfilled").length, 1);

    const claim = await claimAgentGatewayTask(
      request(
        "POST",
        "/api/mis/agent-gateway/tasks/tsk_gateway_core/claim",
        token,
        { workspace_id: workspaceId, agent_id: "agt_gateway_core" },
      ),
      "tsk_gateway_core",
    );
    assert.equal(claim.body.outcome, "claimed");
    const claimReplay = await claimAgentGatewayTask(
      request(
        "POST",
        "/api/mis/agent-gateway/tasks/tsk_gateway_core/claim",
        session,
        { workspace_id: workspaceId, agent_id: "agt_gateway_core" },
      ),
      "tsk_gateway_core",
    );
    assert.equal(claimReplay.body.outcome, "unchanged");

    const planBody = {
      workspace_id: workspaceId,
      agent_id: "agt_gateway_core",
      plan_id: "plan_gateway_core",
      task_id: "tsk_gateway_core",
      task_understanding: "Execute the production gateway contract.",
      referenced_specs: ["docs/AGENT_GATEWAY_CLI_SPEC.md"],
      referenced_memories: ["knowledge/shared/common_failures.md"],
      referenced_bases: ["base_local_tasks"],
      proposed_files_to_change: ["ui/next-app"],
      risk_level: "medium",
      approval_required: false,
      execution_steps: [
        "READ",
        "PLAN",
        "RETRIEVE",
        "COMPARE",
        "EXECUTE",
        "VERIFY",
        "RECORD",
      ],
      verification_plan: "Verify immutable Postgres evidence and bindings.",
      rollback_plan: "Leave the task blocked and preserve prior evidence.",
      status: "submitted",
      plan_version: 3,
    };
    const planCreate = await createAgentGatewayPlan(
      request(
        "POST",
        "/api/mis/agent-gateway/agent-plans",
        token,
        planBody,
      ),
    );
    assert.equal(planCreate.body.agent_plan.plan_version, 3);
    assert.match(String(planCreate.body.agent_plan.plan_hash), /^[a-f0-9]{64}$/);
    const verified = await getAgentGatewayPlan(
      request(
        "GET",
        "/api/mis/agent-gateway/agent-plans/plan_gateway_core/verify",
        token,
      ),
      "plan_gateway_core",
      true,
    );
    assert.ok(verified.verification);
    assert.equal(verified.verification.pass, true);
    assert.equal(
      computeAgentPlanHash({
        workspace_id: "ws_contract",
        task_id: "tsk_contract",
        run_id: "run_contract",
        agent_id: "agt_contract",
        task_understanding: "Prepare a verified Hermes customer delivery.",
        referenced_specs_json: JSON.stringify(["PROJECT_SPEC.md"]),
        referenced_memories_json: JSON.stringify(["project-memory:928"]),
        referenced_bases_json: JSON.stringify(["base_local_tasks"]),
        proposed_files_to_change_json: "[]",
        risk_level: "medium",
        approval_required: 0,
        execution_steps_json: JSON.stringify([
          "READ",
          "PLAN",
          "RETRIEVE",
          "COMPARE",
          "EXECUTE",
          "VERIFY",
          "RECORD",
        ]),
        verification_plan: "Verify evidence.",
        rollback_plan: "Keep delivery blocked.",
        plan_version: 3,
      }),
      "64cd02cfc2b263deb29a9030930609603a11f50282ac529ef5af8e373e29882f",
    );
    assert.equal(
      agentPlanVerificationHash("plan_contract", {
        plan_hash: "64cd02cfc2b263deb29a9030930609603a11f50282ac529ef5af8e373e29882f",
        pass: true,
        failed_checks: [],
        summary: { quality_score: 100 },
        quality: {
          version: "agent_plan_quality_v1",
          score: 100,
          status: "ready",
          failed_rubric_ids: [],
        },
      }),
      "7cc45901c03a0caf4cf3ea75a7f889f23dea06a553d605833fc577dde0dabdd3",
    );

    const driftClient = new Client({ connectionString: scopedDsn() });
    await driftClient.connect();
    const originalUnderstanding = planCreate.body.agent_plan.task_understanding;
    try {
      await driftClient.query(
        "UPDATE agent_plans SET task_understanding='drifted' WHERE plan_id='plan_gateway_core'",
      );
      const drifted = await getAgentGatewayPlan(
        request(
          "GET",
          "/api/mis/agent-gateway/agent-plans/plan_gateway_core/verify",
          token,
        ),
        "plan_gateway_core",
        true,
      );
      assert.ok(drifted.verification);
      assert.equal(drifted.verification.pass, false);
      await driftClient.query(
        `UPDATE agent_plans SET task_understanding=$1,verified_at=NULL,
          verification_result_hash=NULL WHERE plan_id='plan_gateway_core'`,
        [originalUnderstanding],
      );
    } finally {
      await driftClient.end();
    }
    const reverified = await getAgentGatewayPlan(
      request(
        "GET",
        "/api/mis/agent-gateway/agent-plans/plan_gateway_core/verify",
        token,
      ),
      "plan_gateway_core",
      true,
    );
    assert.ok(reverified.verification);
    assert.equal(reverified.verification.pass, true);
    const planHash = reverified.agent_plan.plan_hash as string;
    const verificationHash = reverified.verification_result_hash as string;

    const runBody = {
      workspace_id: workspaceId,
      agent_id: "agt_gateway_core",
      task_id: "tsk_gateway_core",
      run_id: "run_gateway_core",
      agent_plan_id: "plan_gateway_core",
      plan_hash: planHash,
      runtime_type: "hermes",
      estimated_cost_usd: "1.000000",
      input_summary: "Bounded contract execution.",
    };
    const runStart = await startAgentGatewayRun(
      request(
        "POST",
        "/api/mis/agent-gateway/runs/start",
        token,
        runBody,
      ),
    );
    assertRunStartAllowed(runStart, 201);
    assert.equal(runStart.body.run.agent_plan_id, "plan_gateway_core");
    assert.equal(runStart.body.run.plan_hash, planHash);
    assert.equal(runStart.body.run.model_provider, "hermes");

    const entitlementClient = new Client({ connectionString: scopedDsn() });
    await entitlementClient.connect();
    try {
      const usageBeforeReplay = await entitlementClient.query<{ run_count: number }>(
        "SELECT COUNT(*)::int AS run_count FROM runs WHERE workspace_id=$1",
        [workspaceId],
      );
      assert.equal(usageBeforeReplay.rows[0]?.run_count, 1);

      await entitlementClient.query(
        "UPDATE workspace_entitlements SET status='suspended' WHERE workspace_id=$1",
        [workspaceId],
      );
      const replayWhileSuspended = await startAgentGatewayRun(
        request(
          "POST",
          "/api/mis/agent-gateway/runs/start",
          session,
          runBody,
        ),
      );
      assertRunStartAllowed(replayWhileSuspended, 200);
      assert.equal(replayWhileSuspended.body.outcome, "unchanged");
      const usageAfterReplay = await entitlementClient.query<{ run_count: number }>(
        "SELECT COUNT(*)::int AS run_count FROM runs WHERE workspace_id=$1",
        [workspaceId],
      );
      assert.equal(usageAfterReplay.rows[0]?.run_count, 1);

      const deniedRunIds = [
        "run_gateway_entitlement_capability",
        "run_gateway_entitlement_suspended",
        "run_gateway_entitlement_quota",
      ];
      const missingDenied = await startAgentGatewayRun(
        request(
          "POST",
          "/api/mis/agent-gateway/runs/start",
          foreignToken,
          {
            workspace_id: otherWorkspaceId,
            agent_id: "agt_gateway_core",
            task_id: "tsk_gateway_entitlement_missing",
            run_id: "run_gateway_entitlement_missing",
            runtime_type: "mock",
            estimated_cost_usd: "1.000000",
          },
          otherWorkspaceId,
        ),
      );
      assertEntitlementDenied(missingDenied, "entitlement_missing");
      const missingAudit = await entitlementClient.query<{
        action: string;
        metadata_json: string;
      }>(
        `SELECT action,metadata_json
        FROM audit_logs
        WHERE workspace_id=$1
          AND entity_id='run_gateway_entitlement_missing'
          AND action='agent_gateway.run_start.entitlement_denied'`,
        [otherWorkspaceId],
      );
      assert.equal(missingAudit.rowCount, 1);
      assert.equal(
        JSON.parse(missingAudit.rows[0]?.metadata_json || "{}").reason_code,
        "entitlement_missing",
      );
      const missingWrites = await entitlementClient.query<{ count: number }>(
        `SELECT (
          (SELECT COUNT(*) FROM runs
            WHERE workspace_id=$1 AND run_id='run_gateway_entitlement_missing')
          + (SELECT COUNT(*) FROM run_cost_reservations
            WHERE workspace_id=$1 AND run_id='run_gateway_entitlement_missing')
        )::int AS count`,
        [otherWorkspaceId],
      );
      assert.equal(missingWrites.rows[0]?.count, 0);

      await setRunEntitlement(entitlementClient, { runStart: false });
      const capabilityDenied = await startAgentGatewayRun(
        request(
          "POST",
          "/api/mis/agent-gateway/runs/start",
          token,
          { ...runBody, run_id: deniedRunIds[0] },
        ),
      );
      assertEntitlementDenied(capabilityDenied, "capability_disabled");

      await setRunEntitlement(entitlementClient, { status: "suspended" });
      const suspendedDenied = await startAgentGatewayRun(
        request(
          "POST",
          "/api/mis/agent-gateway/runs/start",
          token,
          { ...runBody, run_id: deniedRunIds[1] },
        ),
      );
      assertEntitlementDenied(suspendedDenied, "entitlement_suspended");

      await setRunEntitlement(entitlementClient, { maxMonthlyRuns: 1 });
      const quotaDenied = await startAgentGatewayRun(
        request(
          "POST",
          "/api/mis/agent-gateway/runs/start",
          token,
          { ...runBody, run_id: deniedRunIds[2] },
        ),
      );
      assertEntitlementDenied(quotaDenied, "monthly_run_quota_exceeded");

      const deniedRuns = await entitlementClient.query<{ run_id: string }>(
        "SELECT run_id FROM runs WHERE run_id=ANY($1::text[])",
        [deniedRunIds],
      );
      assert.equal(deniedRuns.rowCount, 0);
      const denialAudits = await entitlementClient.query<{
        actor_type: string;
        actor_id: string;
        entity_id: string;
        metadata_json: string;
      }>(
        `SELECT actor_type,actor_id,entity_id,metadata_json
        FROM audit_logs
        WHERE workspace_id=$1
          AND action='agent_gateway.run_start.entitlement_denied'
          AND entity_id=ANY($2::text[])
        ORDER BY entity_id`,
        [workspaceId, deniedRunIds],
      );
      assert.equal(denialAudits.rowCount, deniedRunIds.length);
      const expectedReasons = new Map([
        [deniedRunIds[0], "capability_disabled"],
        [deniedRunIds[1], "entitlement_suspended"],
        [deniedRunIds[2], "monthly_run_quota_exceeded"],
      ]);
      for (const audit of denialAudits.rows) {
        assert.equal(audit.actor_type, "agent");
        assert.equal(audit.actor_id, "agt_gateway_core");
        const metadata = typeof audit.metadata_json === "string"
          ? JSON.parse(audit.metadata_json) as Record<string, unknown>
          : audit.metadata_json as unknown as Record<string, unknown>;
        assert.equal(metadata.reason_code, expectedReasons.get(audit.entity_id));
        assert.equal(metadata.raw_config_omitted, true);
        assert.equal(metadata.credentials_omitted, true);
        const serialized = JSON.stringify(metadata);
        for (const key of RAW_ENTITLEMENT_CONFIG_KEYS) {
          assert.equal(serialized.includes(key), false, `audit leaked raw entitlement key ${key}`);
        }
      }
    } finally {
      await entitlementClient.end();
    }
    const auditBlocker = new Client({ connectionString: scopedDsn() });
    const lockObserver = new Client({
      connectionString: scopedDsn(),
      application_name: "agentops-core-lock-observer",
    });
    await auditBlocker.connect();
    await lockObserver.connect();
    let blockerReleased = false;
    let lockOrderRunStart:
      | ReturnType<typeof startAgentGatewayRun>
      | undefined;
    let lockOrderHeartbeat:
      | ReturnType<typeof recordGatewayHeartbeat>
      | undefined;
    try {
      await setRunEntitlement(auditBlocker);
      await auditBlocker.query("BEGIN");
      await auditBlocker.query("SELECT pg_advisory_xact_lock(1095779668)");
      lockOrderRunStart = startAgentGatewayRun(
        request(
          "POST",
          "/api/mis/agent-gateway/runs/start",
          token,
          {
            workspace_id: workspaceId,
            agent_id: "agt_gateway_core",
            task_id: "tsk_gateway_lock_order",
            run_id: "run_gateway_lock_order",
            runtime_type: "mock",
            estimated_cost_usd: "1.000000",
            input_summary: "Exercise business-row-before-audit lock order.",
          },
        ),
      );
      void lockOrderRunStart.catch(() => undefined);
      const runStartPid = await waitForBlockedAdvisoryLock(
        lockObserver,
        1095779668,
      );
      lockOrderHeartbeat = recordGatewayHeartbeat(
        request(
          "POST",
          "/api/mis/agent-gateway/heartbeat",
          siblingToken,
          {
            workspace_id: workspaceId,
            agent_id: "agt_gateway_core",
            runtime_type: "hermes",
            status: "paused",
            request_id: "gateway-lock-order-heartbeat-0001",
          },
        ),
      );
      void lockOrderHeartbeat.catch(() => undefined);
      const heartbeatPid = await waitForBlockedByPid(
        lockObserver,
        runStartPid,
      );
      assert.notEqual(heartbeatPid, runStartPid);
      await auditBlocker.query("COMMIT");
      blockerReleased = true;
      const runHeartbeatLockOrder = await within(
        Promise.allSettled([lockOrderRunStart, lockOrderHeartbeat]),
        10_000,
      );
      assert.equal(runHeartbeatLockOrder[0].status, "fulfilled");
      assert.equal(runHeartbeatLockOrder[0].value.status, 201);
      assert.equal(runHeartbeatLockOrder[1].status, "fulfilled");
      assert.equal(runHeartbeatLockOrder[1].value.status, 200);
    } finally {
      if (!blockerReleased) {
        await auditBlocker.query("ROLLBACK").catch(() => undefined);
      }
      const pendingOperations: Promise<unknown>[] = [];
      if (lockOrderRunStart) pendingOperations.push(lockOrderRunStart);
      if (lockOrderHeartbeat) pendingOperations.push(lockOrderHeartbeat);
      await Promise.allSettled(pendingOperations);
      await auditBlocker.end().catch(() => undefined);
      await lockObserver.end().catch(() => undefined);
    }
    await expectCode(
      "forbidden",
      () => heartbeatAgentGatewayRun(
        request(
          "POST",
          "/api/mis/agent-gateway/runs/run_gateway_core/heartbeat",
          otherToken,
          { workspace_id: workspaceId, status: "running" },
          workspaceId,
          "agt_gateway_other",
        ),
        "run_gateway_core",
      ),
    );

    const toolBody = {
      workspace_id: workspaceId,
      task_id: "tsk_gateway_core",
      run_id: "run_gateway_core",
      agent_id: "agt_gateway_core",
      tool_call_id: "tc_gateway_core",
      tool_name: "agent_worker.hermes",
      tool_category: "custom",
      risk_level: "low",
      status: "completed",
      args: {
        adapter: "hermes",
        provider_call_performed: true,
        dry_run: false,
        raw_prompt: "must-not-persist",
        token: "must-not-persist",
      },
      result_summary: "Hermes provider call completed; raw response omitted.",
    };
    const tool = await recordAgentGatewayToolCall(
      request(
        "POST",
        "/api/mis/agent-gateway/tool-calls",
        token,
        toolBody,
      ),
    );
    assert.equal(tool.body.outcome, "created");
    assert.equal(
      JSON.stringify(tool.body.tool_call).includes("must-not-persist"),
      false,
    );
    const toolReplay = await recordAgentGatewayToolCall(
      request(
        "POST",
        "/api/mis/agent-gateway/tool-calls",
        session,
        toolBody,
      ),
    );
    assert.equal(toolReplay.body.outcome, "unchanged");

    await heartbeatAgentGatewayRun(
      request(
        "POST",
        "/api/mis/agent-gateway/runs/run_gateway_core/heartbeat",
        token,
        {
          workspace_id: workspaceId,
          task_id: "tsk_gateway_core",
          status: "completed",
          output_summary: "Provider result summarized.",
          duration_ms: 12,
        },
      ),
      "run_gateway_core",
    );
    const runReplay = await startAgentGatewayRun(
      request(
        "POST",
        "/api/mis/agent-gateway/runs/start",
        session,
        runBody,
      ),
    );
    assertRunStartAllowed(runReplay, 200);
    assert.equal(runReplay.body.outcome, "unchanged");
    await expectCode(
      "run_terminal_conflict",
      () => heartbeatAgentGatewayRun(
        request(
          "POST",
          "/api/mis/agent-gateway/runs/run_gateway_core/heartbeat",
          token,
          {
            workspace_id: workspaceId,
            status: "completed",
            output_summary: "attempted terminal rewrite",
          },
        ),
        "run_gateway_core",
      ),
    );
    const evaluationBody = {
      workspace_id: workspaceId,
      task_id: "tsk_gateway_core",
      run_id: "run_gateway_core",
      agent_id: "agt_gateway_core",
      evaluation_id: "eval_gateway_core",
      evaluator_type: "rule",
      score: 1,
      pass_fail: "pass",
      rubric: {
        adapter: "hermes",
        provider_call_performed: true,
        dry_run: false,
        raw_response: "must-not-persist",
      },
      notes: "Rule evidence passed.",
    };
    await expectCode(
      "human_evaluator_forbidden",
      () => submitAgentGatewayEvaluation(
        request(
          "POST",
          "/api/mis/agent-gateway/evaluations/submit",
          token,
          { ...evaluationBody, evaluation_id: "eval_human", evaluator_type: "human" },
        ),
      ),
    );
    const evaluation = await submitAgentGatewayEvaluation(
      request(
        "POST",
        "/api/mis/agent-gateway/evaluations/submit",
        token,
        evaluationBody,
      ),
    );
    assert.equal(evaluation.body.outcome, "created");
    const evaluationReplay = await submitAgentGatewayEvaluation(
      request(
        "POST",
        "/api/mis/agent-gateway/evaluations/submit",
        token,
        evaluationBody,
      ),
    );
    assert.equal(evaluationReplay.body.outcome, "unchanged");

    const artifactHash = sha("bounded-artifact");
    const artifactBody = {
      workspace_id: workspaceId,
      task_id: "tsk_gateway_core",
      run_id: "run_gateway_core",
      agent_id: "agt_gateway_core",
      artifact_id: "art_gateway_core",
      artifact_type: "agent_worker_result",
      title: "Bounded worker result",
      uri: "run://run_gateway_core",
      summary: "Hashed result summary.",
      content_hash: artifactHash,
    };
    const artifact = await recordAgentGatewayArtifact(
      request(
        "POST",
        "/api/mis/agent-gateway/artifacts",
        token,
        artifactBody,
      ),
    );
    assert.equal(
      (artifact.body.artifact as { content_hash: string }).content_hash,
      artifactHash,
    );
    const artifactReplay = await recordAgentGatewayArtifact(
      request(
        "POST",
        "/api/mis/agent-gateway/artifacts",
        token,
        artifactBody,
      ),
    );
    assert.equal(artifactReplay.body.outcome, "unchanged");

    const manifestBody = {
      workspace_id: workspaceId,
      task_id: "tsk_gateway_core",
      run_id: "run_gateway_core",
      agent_id: "agt_gateway_core",
      manifest_id: "pem_gateway_core",
      plan_id: "plan_gateway_core",
      plan_hash: planHash,
      verification_result_hash: verificationHash,
      mismatch_policy: "block",
      expected_steps: planBody.execution_steps,
      tool_call_ids: ["tc_gateway_core"],
      evaluation_ids: ["eval_gateway_core"],
      artifact_ids: ["art_gateway_core"],
      audit_ids: ["aud_caller_supplied"],
    };
    const missingWorkerAuditManifest = await createAgentGatewayPlanEvidenceManifest(
      request(
        "POST",
        "/api/mis/agent-gateway/plan-evidence-manifests",
        token,
        {
          ...manifestBody,
          manifest_id: "pem_gateway_core_missing_worker_audit",
        },
      ),
    );
    const missingWorkerAuditVerification = missingWorkerAuditManifest.body.verification as {
      pass: boolean;
      failed_checks: Array<{ id: string }>;
    };
    assert.equal(missingWorkerAuditVerification.pass, false);
    assert.equal(
      missingWorkerAuditVerification.failed_checks.some(
        (check) => check.id === "commercial_worker_audit_provenance",
      ),
      true,
    );
    await emitAgentAudit(
      request(
        "POST",
        "/api/mis/agent-gateway/audit",
        token,
        {
          workspace_id: workspaceId,
          agent_id: "agt_gateway_core",
          task_id: "tsk_gateway_core",
          run_id: "run_gateway_core",
          action: "agent_worker.task_processed",
          entity_type: "runs",
          entity_id: "run_gateway_core",
          metadata: {
            adapter: "hermes",
            provider_call_performed: true,
            dry_run: false,
          },
        },
      ),
    );
    const manifest = await createAgentGatewayPlanEvidenceManifest(
      request(
        "POST",
        "/api/mis/agent-gateway/plan-evidence-manifests",
        token,
        manifestBody,
      ),
    );
    assert.equal(
      (manifest.body.verification as { pass: boolean }).pass,
      true,
    );
    assert.equal(manifest.body.manifest.plan_hash, planHash);
    assert.equal(
      manifest.body.manifest.verification_result_hash,
      verificationHash,
    );
    assert.equal(manifest.body.manifest.audit_ids_json, "[]");
    const manifestReplay = await createAgentGatewayPlanEvidenceManifest(
      request(
        "POST",
        "/api/mis/agent-gateway/plan-evidence-manifests",
        session,
        manifestBody,
      ),
    );
    assert.equal(manifestReplay.body.outcome, "unchanged");
    await expectCode(
      "manifest_stale_plan_hash",
      () => createAgentGatewayPlanEvidenceManifest(
        request(
          "POST",
          "/api/mis/agent-gateway/plan-evidence-manifests",
          token,
          {
            ...manifestBody,
            manifest_id: "pem_gateway_stale",
            plan_hash: "0".repeat(64),
          },
        ),
      ),
    );
    assert.equal(pythonObserverRequests, 0);
    console.log(JSON.stringify({
      contract: "agent_gateway_core_postgres_v1",
      ok: true,
      postgres_migrations_applied: true,
      token_and_session: true,
      cross_workspace_and_agent_denied: true,
      concurrent_claim_single_winner: true,
      plan_hash_and_verification_bound: true,
      run_plan_binding: true,
      run_start_entitlement_fail_closed: true,
      runtime_entitlement_select_only: true,
      entitlement_missing_controlled_403: true,
      entitlement_denied_controlled_403: true,
      entitlement_allowed_with_restricted_runtime: true,
      run_start_before_global_audit_lock_order: true,
      entitlement_denial_audit_persisted: true,
      run_replay_bypasses_entitlement_usage: true,
      immutable_evidence_replay: true,
      evidence_exact_cost_projection_numeric_18_6: true,
      evidence_run_snapshot_exact_field_bound: true,
      legacy_numeric_cost_projection_compatible: true,
      commercial_manifest_provenance_fail_closed: true,
      stale_manifest_hash_denied: true,
      raw_prompt_response_token_omitted: true,
      python_observer_requests: pythonObserverRequests,
    }));
  } finally {
    await closeControlPlanePoolForTests();
    await new Promise<void>((resolve, reject) =>
      observer.close((error) => error ? reject(error) : resolve()));
    if (originalEnv.dsn === undefined) delete process.env.AGENTOPS_POSTGRES_DSN;
    else process.env.AGENTOPS_POSTGRES_DSN = originalEnv.dsn;
    if (originalEnv.deployment === undefined) delete process.env.AGENTOPS_DEPLOYMENT_MODE;
    else process.env.AGENTOPS_DEPLOYMENT_MODE = originalEnv.deployment;
    if (originalEnv.mode === undefined) delete process.env.AGENTOPS_CONTROL_PLANE_MODE;
    else process.env.AGENTOPS_CONTROL_PLANE_MODE = originalEnv.mode;
    if (originalEnv.upstream === undefined) delete process.env.AGENTOPS_API_BASE;
    else process.env.AGENTOPS_API_BASE = originalEnv.upstream;
    if (originalEnv.applicationName === undefined) {
      delete process.env.AGENTOPS_POSTGRES_APPLICATION_NAME;
    } else {
      process.env.AGENTOPS_POSTGRES_APPLICATION_NAME =
        originalEnv.applicationName;
    }
    if (originalEnv.schema === undefined) {
      delete process.env.AGENTOPS_POSTGRES_SCHEMA;
    } else {
      process.env.AGENTOPS_POSTGRES_SCHEMA = originalEnv.schema;
    }
    if (originalEnv.runtimeApiSchema === undefined) {
      delete process.env.AGENTOPS_POSTGRES_RUNTIME_API_SCHEMA;
    } else {
      process.env.AGENTOPS_POSTGRES_RUNTIME_API_SCHEMA =
        originalEnv.runtimeApiSchema;
    }
    if (originalEnv.runtimeRole === undefined) {
      delete process.env.AGENTOPS_POSTGRES_RUNTIME_ROLE;
    } else {
      process.env.AGENTOPS_POSTGRES_RUNTIME_ROLE = originalEnv.runtimeRole;
    }
    await admin.query(`DROP SCHEMA IF EXISTS "${schema}" CASCADE`);
    await admin.query(`DROP SCHEMA IF EXISTS "${runtimeApiSchema}" CASCADE`);
    await admin.query(`DROP ROLE IF EXISTS "${runtimeRole}"`);
    await admin.query(`DROP ROLE IF EXISTS "${entitlementAdminRole}"`);
    await admin.end();
  }
}

await runContract();
