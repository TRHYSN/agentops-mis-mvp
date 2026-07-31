import type { PoolClient } from "pg";

export const WORKSPACE_ENTITLEMENT_DECISION_CONTRACT =
  "agentops_workspace_entitlement_decision_v1";

export type WorkspaceEntitlementOperation =
  | "enrollment_issue"
  | "session_issue"
  | "run_start";

export type WorkspaceEntitlementEvaluation =
  | Readonly<{
      workspaceId: string;
      operation: "enrollment_issue";
      agentId: string;
      replacingEnrollmentId?: string;
    }>
  | Readonly<{
      workspaceId: string;
      operation: "session_issue";
      agentId: string;
    }>
  | Readonly<{
      workspaceId: string;
      operation: "run_start";
      agentId?: string;
      estimatedCostUsd?: number;
    }>;

export type WorkspaceEntitlementReason =
  | "allowed"
  | "workspace_binding_required"
  | "agent_binding_required"
  | "estimated_cost_invalid"
  | "entitlement_missing"
  | "entitlement_state_invalid"
  | "entitlement_inactive"
  | "entitlement_suspended"
  | "entitlement_expired"
  | "entitlement_not_effective"
  | "capability_disabled"
  | "usage_state_invalid"
  | "replacement_enrollment_invalid"
  | "agent_quota_exceeded"
  | "active_enrollment_quota_exceeded"
  | "active_session_quota_exceeded"
  | "monthly_run_quota_exceeded"
  | "monthly_cost_quota_exceeded";

export type EntitlementQuotaUsage = Readonly<{
  current: number;
  projected: number;
  limit: number;
  unit: "count" | "usd";
}>;

export type WorkspaceEntitlementUsage = Readonly<{
  active_agents: EntitlementQuotaUsage | null;
  active_enrollments: EntitlementQuotaUsage | null;
  active_sessions_per_agent: EntitlementQuotaUsage | null;
  monthly_runs: EntitlementQuotaUsage | null;
  monthly_cost_usd: EntitlementQuotaUsage | null;
  month_utc: string | null;
}>;

export type WorkspaceEntitlementDecision = Readonly<{
  contract: typeof WORKSPACE_ENTITLEMENT_DECISION_CONTRACT;
  allow: boolean;
  decision: "allow" | "deny";
  reason_code: WorkspaceEntitlementReason;
  operation: WorkspaceEntitlementOperation;
  workspace_id: string;
  agent_id: string | null;
  entitlement: Readonly<{
    authority: "postgres";
    configured: boolean;
    edition: string | null;
    status: string | null;
    capability: WorkspaceEntitlementOperation;
    capability_enabled: boolean;
    raw_config_omitted: true;
  }>;
  usage: WorkspaceEntitlementUsage | null;
  evaluated_at: string;
  lock: Readonly<{
    scope: "workspace";
    transaction_scoped: true;
    acquired: boolean;
  }>;
  credentials_omitted: true;
  raw_config_omitted: true;
}>;

type EntitlementRow = {
  workspace_id: string;
  edition: string;
  status: string;
  capabilities_json: unknown;
  max_agents: number;
  max_active_enrollments: number;
  max_active_sessions_per_agent: number;
  max_concurrent_runs: number;
  max_monthly_runs: number;
  max_monthly_cost_usd: string;
  effective_at: Date | string;
  expires_at: Date | string | null;
};

type EnrollmentUsageRow = {
  active_agents: string;
  active_enrollments: string;
  target_agent_active: boolean;
  invalid_expiries: string;
};

type SessionUsageRow = {
  active_sessions: string;
  invalid_expiries: string;
};

type MonthlyUsageRow = {
  monthly_runs: string;
  monthly_cost_usd: string;
  invalid_started_at: string;
  invalid_costs: string;
};

const EMPTY_USAGE: WorkspaceEntitlementUsage = Object.freeze({
  active_agents: null,
  active_enrollments: null,
  active_sessions_per_agent: null,
  monthly_runs: null,
  monthly_cost_usd: null,
  month_utc: null,
});

function identifier(value: unknown) {
  return typeof value === "string" ? value.trim() : "";
}

function asDate(value: Date | string) {
  const parsed = value instanceof Date ? value : new Date(value);
  return Number.isFinite(parsed.getTime()) ? parsed : null;
}

function nonNegativeInteger(value: unknown) {
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) && parsed >= 0 ? parsed : null;
}

function nonNegativeNumber(value: unknown) {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : null;
}

function quota(current: number, projected: number, limit: number, unit: "count" | "usd") {
  return Object.freeze({ current, projected, limit, unit });
}

function decision(
  input: WorkspaceEntitlementEvaluation,
  options: Readonly<{
    allow: boolean;
    reason: WorkspaceEntitlementReason;
    evaluatedAt: Date;
    lockAcquired: boolean;
    entitlement?: EntitlementRow;
    capabilityEnabled?: boolean;
    usage?: WorkspaceEntitlementUsage;
  }>,
): WorkspaceEntitlementDecision {
  const workspaceId = identifier(input.workspaceId);
  const agentId = "agentId" in input ? identifier(input.agentId) : "";
  return Object.freeze({
    contract: WORKSPACE_ENTITLEMENT_DECISION_CONTRACT,
    allow: options.allow,
    decision: options.allow ? "allow" : "deny",
    reason_code: options.reason,
    operation: input.operation,
    workspace_id: workspaceId,
    agent_id: agentId || null,
    entitlement: Object.freeze({
      authority: "postgres",
      configured: Boolean(options.entitlement),
      edition: options.entitlement?.edition || null,
      status: options.entitlement?.status || null,
      capability: input.operation,
      capability_enabled: Boolean(options.capabilityEnabled),
      raw_config_omitted: true,
    }),
    usage: options.usage || null,
    evaluated_at: options.evaluatedAt.toISOString(),
    lock: Object.freeze({
      scope: "workspace",
      transaction_scoped: true,
      acquired: options.lockAcquired,
    }),
    credentials_omitted: true,
    raw_config_omitted: true,
  });
}

function entitlementState(
  row: EntitlementRow,
  now: Date,
): Readonly<{
  valid: boolean;
  reason: WorkspaceEntitlementReason;
}> {
  const effectiveAt = asDate(row.effective_at);
  const expiresAt = row.expires_at === null ? null : asDate(row.expires_at);
  const capabilities = row.capabilities_json;
  const quotasValid = [
    row.max_agents,
    row.max_active_enrollments,
    row.max_active_sessions_per_agent,
    row.max_concurrent_runs,
    row.max_monthly_runs,
  ].every((value) => nonNegativeInteger(value) !== null)
    && nonNegativeNumber(row.max_monthly_cost_usd) !== null;
  const capabilityObject = (
    capabilities !== null
    && typeof capabilities === "object"
    && !Array.isArray(capabilities)
  ) ? capabilities as Record<string, unknown> : null;
  if (!effectiveAt || (row.expires_at !== null && !expiresAt) || !quotasValid || !capabilityObject) {
    return { valid: false, reason: "entitlement_state_invalid" };
  }
  if (row.status === "inactive") {
    return { valid: false, reason: "entitlement_inactive" };
  }
  if (row.status === "suspended") {
    return { valid: false, reason: "entitlement_suspended" };
  }
  if (row.status === "expired") {
    return { valid: false, reason: "entitlement_expired" };
  }
  if (row.status !== "active") {
    return { valid: false, reason: "entitlement_state_invalid" };
  }
  if (effectiveAt.getTime() > now.getTime()) {
    return { valid: false, reason: "entitlement_not_effective" };
  }
  if (expiresAt && expiresAt.getTime() <= now.getTime()) {
    return { valid: false, reason: "entitlement_expired" };
  }
  return { valid: true, reason: "allowed" };
}

async function readEnrollmentUsage(
  client: PoolClient,
  workspaceId: string,
  agentId: string,
  evaluatedAt: Date,
  replacingEnrollmentId: string | null,
) {
  const result = await client.query<EnrollmentUsageRow>(
    `WITH scoped AS (
       SELECT agent_id,
         status='active' AND (
           expires_at IS NULL
           OR CASE
             WHEN pg_input_is_valid(expires_at,'timestamp with time zone')
             THEN expires_at::timestamptz>$2::timestamptz
             ELSE FALSE
           END
         ) AS active_now,
         status='active' AND expires_at IS NOT NULL
           AND NOT pg_input_is_valid(expires_at,'timestamp with time zone')
           AS invalid_expiry
       FROM agent_gateway_tokens
       WHERE workspace_id=$1
         AND ($4::text IS NULL OR token_id<>$4)
     )
     SELECT
       COUNT(DISTINCT agent_id) FILTER(WHERE active_now)::text AS active_agents,
       COUNT(*) FILTER(WHERE active_now)::text AS active_enrollments,
       COALESCE(BOOL_OR(agent_id=$3 AND active_now),FALSE) AS target_agent_active,
       COUNT(*) FILTER(WHERE invalid_expiry)::text AS invalid_expiries
     FROM scoped`,
    [workspaceId, evaluatedAt.toISOString(), agentId, replacingEnrollmentId],
  );
  return result.rows[0];
}

async function lockReplacementEnrollment(
  client: PoolClient,
  workspaceId: string,
  agentId: string,
  replacingEnrollmentId: string,
  evaluatedAt: Date,
) {
  const result = await client.query<{
    status: string;
    expires_at: string | null;
  }>(
    `SELECT status,expires_at
    FROM agent_gateway_tokens
    WHERE token_id=$1 AND workspace_id=$2 AND agent_id=$3
    FOR UPDATE`,
    [replacingEnrollmentId, workspaceId, agentId],
  );
  const row = result.rows[0];
  if (!row || row.status !== "active") return false;
  if (row.expires_at === null) return true;
  const expiresAt = asDate(row.expires_at);
  return Boolean(expiresAt && expiresAt.getTime() > evaluatedAt.getTime());
}

async function readSessionUsage(
  client: PoolClient,
  workspaceId: string,
  agentId: string,
  evaluatedAt: Date,
) {
  const result = await client.query<SessionUsageRow>(
    `WITH scoped AS (
       SELECT
         status='active' AND CASE
           WHEN pg_input_is_valid(expires_at,'timestamp with time zone')
           THEN expires_at::timestamptz>$3::timestamptz
           ELSE FALSE
         END AS active_now,
         status='active'
           AND NOT pg_input_is_valid(expires_at,'timestamp with time zone')
           AS invalid_expiry
       FROM agent_gateway_sessions
       WHERE workspace_id=$1 AND agent_id=$2
     )
     SELECT
       COUNT(*) FILTER(WHERE active_now)::text AS active_sessions,
       COUNT(*) FILTER(WHERE invalid_expiry)::text AS invalid_expiries
     FROM scoped`,
    [workspaceId, agentId, evaluatedAt.toISOString()],
  );
  return result.rows[0];
}

async function readMonthlyUsage(
  client: PoolClient,
  workspaceId: string,
  monthStart: Date,
  monthEnd: Date,
) {
  const result = await client.query<MonthlyUsageRow>(
    `WITH normalized AS (
       SELECT cost_usd,
         CASE
           WHEN pg_input_is_valid(started_at,'timestamp with time zone')
           THEN started_at::timestamptz
           ELSE NULL
         END AS started_at_utc,
         NOT pg_input_is_valid(started_at,'timestamp with time zone')
           AS invalid_started
       FROM runs
       WHERE workspace_id=$1
     ),
     monthly AS (
       SELECT cost_usd
       FROM normalized
       WHERE started_at_utc >= $2::timestamptz
         AND started_at_utc < $3::timestamptz
     )
     SELECT
       (SELECT COUNT(*)::text FROM monthly) AS monthly_runs,
       (SELECT COALESCE(SUM(
          CASE
            WHEN cost_usd IS NOT NULL
              AND cost_usd>=0
              AND lower(cost_usd::text) NOT IN ('nan','infinity','-infinity')
            THEN cost_usd::numeric
            ELSE 0::numeric
          END
        ),0)::text FROM monthly) AS monthly_cost_usd,
       (SELECT COUNT(*)::text FROM normalized WHERE invalid_started)
         AS invalid_started_at,
       (SELECT COUNT(*)::text FROM monthly
         WHERE cost_usd IS NULL
           OR cost_usd<0
           OR lower(cost_usd::text) IN ('nan','infinity','-infinity'))
         AS invalid_costs`,
    [workspaceId, monthStart.toISOString(), monthEnd.toISOString()],
  );
  return result.rows[0];
}

function utcMonth(now: Date) {
  const start = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1));
  const end = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() + 1, 1));
  return {
    start,
    end,
    label: start.toISOString().slice(0, 7),
  };
}

// Callers must keep the returned decision and any corresponding write in the
// same transaction so this workspace lock protects the quota projection.
export async function evaluateWorkspaceEntitlement(
  client: PoolClient,
  input: WorkspaceEntitlementEvaluation,
): Promise<WorkspaceEntitlementDecision> {
  const workspaceId = identifier(input.workspaceId);
  const localNow = new Date();
  if (!workspaceId) {
    return decision(input, {
      allow: false,
      reason: "workspace_binding_required",
      evaluatedAt: localNow,
      lockAcquired: false,
    });
  }

  await client.query(
    "SELECT pg_advisory_xact_lock(hashtextextended($1,0))",
    [`agentops:workspace-entitlement:${workspaceId}`],
  );
  const clock = await client.query<{ evaluated_at: Date | string }>(
    "SELECT clock_timestamp() AS evaluated_at",
  );
  const evaluatedAt = asDate(clock.rows[0]?.evaluated_at || "");
  if (!evaluatedAt) {
    return decision(input, {
      allow: false,
      reason: "entitlement_state_invalid",
      evaluatedAt: localNow,
      lockAcquired: true,
    });
  }

  const entitlementResult = await client.query<EntitlementRow>(
    `SELECT workspace_id,edition,status,capabilities_json,max_agents,
      max_active_enrollments,max_active_sessions_per_agent,max_monthly_runs,
      max_monthly_cost_usd,max_concurrent_runs,effective_at,expires_at
    FROM workspace_entitlements
    WHERE workspace_id=$1
    FOR SHARE`,
    [workspaceId],
  );
  const entitlement = entitlementResult.rows[0];
  if (!entitlement) {
    return decision(input, {
      allow: false,
      reason: "entitlement_missing",
      evaluatedAt,
      lockAcquired: true,
    });
  }

  const state = entitlementState(entitlement, evaluatedAt);
  const capabilityObject = entitlement.capabilities_json as Record<string, unknown>;
  const capabilityEnabled = state.valid && capabilityObject[input.operation] === true;
  if (!state.valid) {
    return decision(input, {
      allow: false,
      reason: state.reason,
      evaluatedAt,
      lockAcquired: true,
      entitlement,
    });
  }
  if (!capabilityEnabled) {
    return decision(input, {
      allow: false,
      reason: "capability_disabled",
      evaluatedAt,
      lockAcquired: true,
      entitlement,
    });
  }

  if (input.operation === "enrollment_issue") {
    const agentId = identifier(input.agentId);
    if (!agentId) {
      return decision(input, {
        allow: false,
        reason: "agent_binding_required",
        evaluatedAt,
        lockAcquired: true,
        entitlement,
        capabilityEnabled,
      });
    }
    const replacingEnrollmentId = identifier(input.replacingEnrollmentId);
    if (
      input.replacingEnrollmentId !== undefined
      && (
        !replacingEnrollmentId
        || !await lockReplacementEnrollment(
          client,
          workspaceId,
          agentId,
          replacingEnrollmentId,
          evaluatedAt,
        )
      )
    ) {
      return decision(input, {
        allow: false,
        reason: "replacement_enrollment_invalid",
        evaluatedAt,
        lockAcquired: true,
        entitlement,
        capabilityEnabled,
      });
    }
    const row = await readEnrollmentUsage(
      client,
      workspaceId,
      agentId,
      evaluatedAt,
      replacingEnrollmentId || null,
    );
    const activeAgents = nonNegativeInteger(row?.active_agents);
    const activeEnrollments = nonNegativeInteger(row?.active_enrollments);
    const invalidExpiries = nonNegativeInteger(row?.invalid_expiries);
    const agentLimit = nonNegativeInteger(entitlement.max_agents);
    const enrollmentLimit = nonNegativeInteger(entitlement.max_active_enrollments);
    if (
      activeAgents === null
      || activeEnrollments === null
      || invalidExpiries === null
      || agentLimit === null
      || enrollmentLimit === null
      || invalidExpiries > 0
    ) {
      return decision(input, {
        allow: false,
        reason: "usage_state_invalid",
        evaluatedAt,
        lockAcquired: true,
        entitlement,
        capabilityEnabled,
      });
    }
    const projectedAgents = activeAgents + (row.target_agent_active ? 0 : 1);
    const projectedEnrollments = activeEnrollments + 1;
    const usage: WorkspaceEntitlementUsage = {
      ...EMPTY_USAGE,
      active_agents: quota(activeAgents, projectedAgents, agentLimit, "count"),
      active_enrollments: quota(
        activeEnrollments,
        projectedEnrollments,
        enrollmentLimit,
        "count",
      ),
    };
    if (projectedAgents > agentLimit) {
      return decision(input, {
        allow: false,
        reason: "agent_quota_exceeded",
        evaluatedAt,
        lockAcquired: true,
        entitlement,
        capabilityEnabled,
        usage,
      });
    }
    if (projectedEnrollments > enrollmentLimit) {
      return decision(input, {
        allow: false,
        reason: "active_enrollment_quota_exceeded",
        evaluatedAt,
        lockAcquired: true,
        entitlement,
        capabilityEnabled,
        usage,
      });
    }
    return decision(input, {
      allow: true,
      reason: "allowed",
      evaluatedAt,
      lockAcquired: true,
      entitlement,
      capabilityEnabled,
      usage,
    });
  }

  if (input.operation === "session_issue") {
    const agentId = identifier(input.agentId);
    if (!agentId) {
      return decision(input, {
        allow: false,
        reason: "agent_binding_required",
        evaluatedAt,
        lockAcquired: true,
        entitlement,
        capabilityEnabled,
      });
    }
    const row = await readSessionUsage(client, workspaceId, agentId, evaluatedAt);
    const current = nonNegativeInteger(row?.active_sessions);
    const invalidExpiries = nonNegativeInteger(row?.invalid_expiries);
    const limit = nonNegativeInteger(entitlement.max_active_sessions_per_agent);
    if (
      current === null
      || invalidExpiries === null
      || limit === null
      || invalidExpiries > 0
    ) {
      return decision(input, {
        allow: false,
        reason: "usage_state_invalid",
        evaluatedAt,
        lockAcquired: true,
        entitlement,
        capabilityEnabled,
      });
    }
    const usage: WorkspaceEntitlementUsage = {
      ...EMPTY_USAGE,
      active_sessions_per_agent: quota(current, current + 1, limit, "count"),
    };
    if (current + 1 > limit) {
      return decision(input, {
        allow: false,
        reason: "active_session_quota_exceeded",
        evaluatedAt,
        lockAcquired: true,
        entitlement,
        capabilityEnabled,
        usage,
      });
    }
    return decision(input, {
      allow: true,
      reason: "allowed",
      evaluatedAt,
      lockAcquired: true,
      entitlement,
      capabilityEnabled,
      usage,
    });
  }

  const estimatedCost = input.estimatedCostUsd === undefined
    ? 0
    : nonNegativeNumber(input.estimatedCostUsd);
  if (estimatedCost === null) {
    return decision(input, {
      allow: false,
      reason: "estimated_cost_invalid",
      evaluatedAt,
      lockAcquired: true,
      entitlement,
      capabilityEnabled,
    });
  }
  const month = utcMonth(evaluatedAt);
  const row = await readMonthlyUsage(client, workspaceId, month.start, month.end);
  const currentRuns = nonNegativeInteger(row?.monthly_runs);
  const currentCost = nonNegativeNumber(row?.monthly_cost_usd);
  const invalidStartedAt = nonNegativeInteger(row?.invalid_started_at);
  const invalidCosts = nonNegativeInteger(row?.invalid_costs);
  const runLimit = nonNegativeInteger(entitlement.max_monthly_runs);
  const costLimit = nonNegativeNumber(entitlement.max_monthly_cost_usd);
  if (
    currentRuns === null
    || currentCost === null
    || invalidStartedAt === null
    || invalidCosts === null
    || runLimit === null
    || costLimit === null
    || invalidStartedAt > 0
    || invalidCosts > 0
  ) {
    return decision(input, {
      allow: false,
      reason: "usage_state_invalid",
      evaluatedAt,
      lockAcquired: true,
      entitlement,
      capabilityEnabled,
    });
  }
  const usage: WorkspaceEntitlementUsage = {
    ...EMPTY_USAGE,
    monthly_runs: quota(currentRuns, currentRuns + 1, runLimit, "count"),
    monthly_cost_usd: quota(
      currentCost,
      currentCost + estimatedCost,
      costLimit,
      "usd",
    ),
    month_utc: month.label,
  };
  if (currentRuns + 1 > runLimit) {
    return decision(input, {
      allow: false,
      reason: "monthly_run_quota_exceeded",
      evaluatedAt,
      lockAcquired: true,
      entitlement,
      capabilityEnabled,
      usage,
    });
  }
  if (currentCost >= costLimit || currentCost + estimatedCost > costLimit) {
    return decision(input, {
      allow: false,
      reason: "monthly_cost_quota_exceeded",
      evaluatedAt,
      lockAcquired: true,
      entitlement,
      capabilityEnabled,
      usage,
    });
  }
  return decision(input, {
    allow: true,
    reason: "allowed",
    evaluatedAt,
    lockAcquired: true,
    entitlement,
    capabilityEnabled,
    usage,
  });
}
