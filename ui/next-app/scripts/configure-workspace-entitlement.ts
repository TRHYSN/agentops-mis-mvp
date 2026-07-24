import {
  createHash,
  scrypt as scryptCallback,
  timingSafeEqual,
} from "node:crypto";
import { resolve } from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

import { Client, type PoolClient } from "pg";

import {
  postgresDsn,
  postgresSslEnabled,
} from "../src/server/controlPlane/config";
import {
  appendAudit,
  stableHash,
} from "../src/server/controlPlane/ledger";
import {
  runPostgresSchemaCommand,
  SchemaReadinessError,
} from "../src/server/controlPlane/schemaReadiness";
import {
  HUMAN_PASSWORD_MAX_LENGTH,
  HUMAN_PASSWORD_MIN_LENGTH,
  HUMAN_SCRYPT_PARAMS,
} from "../src/server/controlPlane/humanPasswordPolicy";

export const WORKSPACE_ENTITLEMENT_ADMINISTRATION_CONTRACT =
  "agentops_workspace_entitlement_administration_v1";

const EDITIONS = Object.freeze([
  "free_local",
  "pro_workspace",
  "team_governance",
  "enterprise_byoc",
] as const);
const STATUSES = Object.freeze([
  "active",
  "inactive",
  "suspended",
  "expired",
] as const);
const CAPABILITY_NAMES = Object.freeze([
  "enrollment_issue",
  "session_issue",
  "run_start",
] as const);
const VALUE_ARGUMENTS = new Set([
  "--workspace-id",
  "--operator-user-id",
  "--edition",
  "--status",
  "--capabilities",
  "--max-agents",
  "--max-active-enrollments",
  "--max-active-sessions-per-agent",
  "--max-monthly-runs",
  "--max-monthly-cost-usd",
  "--effective-at",
  "--expires-at",
  "--expected-revision",
]);
const BOOLEAN_ARGUMENTS = new Set([
  "--confirm",
  "--expect-absent",
]);
const REQUIRED_VALUE_ARGUMENTS = Object.freeze([
  "--workspace-id",
  "--operator-user-id",
  "--edition",
  "--status",
  "--capabilities",
  "--max-agents",
  "--max-active-enrollments",
  "--max-active-sessions-per-agent",
  "--max-monthly-runs",
  "--max-monthly-cost-usd",
  "--effective-at",
  "--expires-at",
]);
const TRUSTED_OPERATOR_ROLES = new Set([
  "operator",
  "owner",
  "workspace-admin",
]);
const IDENTIFIER_PATTERN = /^[A-Za-z0-9._:-]{1,128}$/;
const REVISION_PATTERN = /^[a-f0-9]{64}$/;
const UTC_TIMESTAMP_PATTERN =
  /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?Z$/;
const MAX_POSTGRES_INTEGER = 2_147_483_647;
const MAX_EXACT_COST_WHOLE_USD = 1_000_000_000;

type Edition = (typeof EDITIONS)[number];
type EntitlementStatus = (typeof STATUSES)[number];
type CapabilityName = (typeof CAPABILITY_NAMES)[number];

export type WorkspaceEntitlementCapabilities = Readonly<
  Record<CapabilityName, boolean>
>;

export type WorkspaceEntitlementConfiguration = Readonly<{
  edition: Edition;
  status: EntitlementStatus;
  capabilities: WorkspaceEntitlementCapabilities;
  maxAgents: number;
  maxActiveEnrollments: number;
  maxActiveSessionsPerAgent: number;
  maxMonthlyRuns: number;
  maxMonthlyCostUsd: string;
  effectiveAt: Date;
  expiresAt: Date | null;
}>;

export type WorkspaceEntitlementGuard =
  | Readonly<{ kind: "none" }>
  | Readonly<{ kind: "expect_absent" }>
  | Readonly<{ kind: "expected_revision"; revision: string }>;

export type WorkspaceEntitlementAdministrationRequest = Readonly<{
  workspaceId: string;
  operatorUserId: string;
  confirm: boolean;
  guard: WorkspaceEntitlementGuard;
  configuration: WorkspaceEntitlementConfiguration;
}>;

export type WorkspaceEntitlementAdministrationReceipt = Readonly<{
  contract: typeof WORKSPACE_ENTITLEMENT_ADMINISTRATION_CONTRACT;
  ok: true;
  mode: "plan" | "confirmed";
  outcome:
    | "would_create"
    | "would_update"
    | "created"
    | "updated"
    | "unchanged";
  workspace_id: string;
  operator_ref: string;
  desired_config_hash: string;
  previous_config_hash: string | null;
  revision: string | null;
  required_guard: "expect_absent" | "expected_revision" | null;
  audit_appended: boolean;
  audit_ref: string | null;
  lock: Readonly<{
    scope: "workspace";
    transaction_scoped: true;
    acquired: true;
  }>;
  credentials_omitted: true;
  dsn_omitted: true;
  raw_config_omitted: true;
  python_started: false;
  sqlite_used: false;
  external_network_used: false;
}>;

type EntitlementRow = {
  workspace_id: string;
  edition: string;
  status: string;
  capabilities_json: unknown;
  max_agents: number;
  max_active_enrollments: number;
  max_active_sessions_per_agent: number;
  max_monthly_runs: number;
  max_monthly_cost_usd: string;
  effective_at: Date | string;
  expires_at: Date | string | null;
  updated_at_value: string;
  updated_by_user_id: string | null;
};

type TrustedOperatorRow = {
  user_role: string;
  membership_role: string;
  membership_status: string;
  credential_status: string;
  password_hash: string;
  password_salt: string;
  password_params_json: unknown;
};

type TrustedCredentialRow = Pick<
  TrustedOperatorRow,
  | "credential_status"
  | "password_hash"
  | "password_salt"
  | "password_params_json"
>;

type TrustedUserRow = Pick<TrustedOperatorRow, "user_role">;

type TrustedMembershipRow = Pick<
  TrustedOperatorRow,
  "membership_role" | "membership_status"
>;

type CanonicalConfiguration = Readonly<{
  workspace_id: string;
  edition: string;
  status: string;
  capabilities_json: unknown;
  max_agents: number;
  max_active_enrollments: number;
  max_active_sessions_per_agent: number;
  max_monthly_runs: number;
  max_monthly_cost_usd: string;
  effective_at: string;
  expires_at: string | null;
}>;

export class WorkspaceEntitlementAdministrationError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly exitCode = 2,
  ) {
    super(message);
    this.name = "WorkspaceEntitlementAdministrationError";
  }
}

function administrationError(code: string, message: string, exitCode = 2) {
  return new WorkspaceEntitlementAdministrationError(code, message, exitCode);
}

function sha256(value: string) {
  return createHash("sha256").update(value, "utf8").digest("hex");
}

function deriveOperatorPassword(password: string, salt: Buffer) {
  return new Promise<Buffer>((resolvePromise, rejectPromise) => {
    scryptCallback(
      password,
      salt,
      HUMAN_SCRYPT_PARAMS.keylen,
      {
        N: HUMAN_SCRYPT_PARAMS.n,
        r: HUMAN_SCRYPT_PARAMS.r,
        p: HUMAN_SCRYPT_PARAMS.p,
        maxmem: 128 * 1024 * 1024,
      },
      (error, derived) => {
        if (error) rejectPromise(error);
        else resolvePromise(derived);
      },
    );
  });
}

function identifier(value: unknown, field: string) {
  const normalized = typeof value === "string" ? value.trim() : "";
  if (!IDENTIFIER_PATTERN.test(normalized)) {
    throw administrationError(
      `${field}_invalid`,
      `${field} must use 1-128 safe identifier characters.`,
    );
  }
  return normalized;
}

function parseInteger(value: string, field: string) {
  if (!/^(?:0|[1-9]\d*)$/.test(value)) {
    throw administrationError(
      `${field}_invalid`,
      `${field} must be a finite non-negative integer.`,
    );
  }
  const parsed = Number(value);
  if (
    !Number.isSafeInteger(parsed)
    || parsed < 0
    || parsed > MAX_POSTGRES_INTEGER
  ) {
    throw administrationError(
      `${field}_invalid`,
      `${field} is outside the PostgreSQL integer range.`,
    );
  }
  return parsed;
}

function canonicalCost(value: string) {
  const match = /^(0|[1-9]\d{0,11})(?:\.(\d{1,6}))?$/.exec(value);
  if (!match) {
    throw administrationError(
      "max_monthly_cost_usd_invalid",
      "max_monthly_cost_usd must be a finite non-negative decimal with at most six fractional digits.",
    );
  }
  const whole = match[1];
  if (BigInt(whole) > BigInt(MAX_EXACT_COST_WHOLE_USD)) {
    throw administrationError(
      "max_monthly_cost_usd_precision_unsafe",
      "max_monthly_cost_usd exceeds the exact commercial evaluator range.",
    );
  }
  const fraction = String(match[2] || "").padEnd(6, "0");
  return `${whole}.${fraction}`;
}

function canonicalStoredCost(value: unknown) {
  const normalized = String(value ?? "").trim();
  const match = /^(\d{1,12})(?:\.(\d{1,6}))?$/.exec(normalized);
  if (!match) return normalized;
  const whole = BigInt(match[1]).toString();
  return `${whole}.${String(match[2] || "").padEnd(6, "0")}`;
}

function parseUtcTimestamp(value: string, field: string) {
  if (!UTC_TIMESTAMP_PATTERN.test(value)) {
    throw administrationError(
      `${field}_invalid`,
      `${field} must be an unambiguous UTC RFC3339 timestamp.`,
    );
  }
  const parsed = new Date(value);
  if (!Number.isFinite(parsed.getTime())) {
    throw administrationError(
      `${field}_invalid`,
      `${field} must be a valid UTC timestamp.`,
    );
  }
  const canonicalInput = value.includes(".")
    ? value.replace(
        /\.(\d{1,3})Z$/,
        (_, fraction: string) => `.${fraction.padEnd(3, "0")}Z`,
      )
    : value.replace(/Z$/, ".000Z");
  if (parsed.toISOString() !== canonicalInput) {
    throw administrationError(
      `${field}_invalid`,
      `${field} must be a real calendar timestamp without normalization.`,
    );
  }
  return parsed;
}

function parseCapabilities(value: string): WorkspaceEntitlementCapabilities {
  const enabled = new Set<CapabilityName>();
  if (value !== "none") {
    const parts = value.split(",");
    if (
      parts.length === 0
      || parts.some((part) => !part)
      || new Set(parts).size !== parts.length
      || parts.some((part) => !CAPABILITY_NAMES.includes(part as CapabilityName))
    ) {
      throw administrationError(
        "capabilities_invalid",
        "capabilities must be a unique comma-separated set of recognized capability names, or none.",
      );
    }
    for (const part of parts) enabled.add(part as CapabilityName);
  }
  return Object.freeze({
    enrollment_issue: enabled.has("enrollment_issue"),
    session_issue: enabled.has("session_issue"),
    run_start: enabled.has("run_start"),
  });
}

function requireKnownValue<T extends string>(
  value: string,
  allowed: readonly T[],
  field: string,
) {
  if (!allowed.includes(value as T)) {
    throw administrationError(
      `${field}_invalid`,
      `${field} is not a recognized value.`,
    );
  }
  return value as T;
}

function validateConfiguration(
  configuration: WorkspaceEntitlementConfiguration,
  now: Date,
) {
  const edition = requireKnownValue(
    String(configuration.edition || ""),
    EDITIONS,
    "edition",
  );
  const status = requireKnownValue(
    String(configuration.status || ""),
    STATUSES,
    "status",
  );
  const capabilities = configuration.capabilities;
  if (
    !capabilities
    || CAPABILITY_NAMES.some(
      (name) => typeof capabilities[name] !== "boolean",
    )
    || Object.keys(capabilities).some(
      (name) => !CAPABILITY_NAMES.includes(name as CapabilityName),
    )
  ) {
    throw administrationError(
      "capabilities_invalid",
      "capabilities must contain only recognized boolean capability values.",
    );
  }
  const integerQuotas = [
    ["max_agents", configuration.maxAgents],
    ["max_active_enrollments", configuration.maxActiveEnrollments],
    [
      "max_active_sessions_per_agent",
      configuration.maxActiveSessionsPerAgent,
    ],
    ["max_monthly_runs", configuration.maxMonthlyRuns],
  ] as const;
  for (const [field, quota] of integerQuotas) {
    if (
      !Number.isSafeInteger(quota)
      || quota < 0
      || quota > MAX_POSTGRES_INTEGER
    ) {
      throw administrationError(
        `${field}_invalid`,
        `${field} must be a finite non-negative PostgreSQL integer.`,
      );
    }
  }
  const maxMonthlyCostUsd = canonicalCost(
    String(configuration.maxMonthlyCostUsd ?? ""),
  );
  if (
    !(configuration.effectiveAt instanceof Date)
    || !Number.isFinite(configuration.effectiveAt.getTime())
  ) {
    throw administrationError(
      "effective_at_invalid",
      "effective_at must be a valid timestamp.",
    );
  }
  if (
    configuration.expiresAt !== null
    && (
      !(configuration.expiresAt instanceof Date)
      || !Number.isFinite(configuration.expiresAt.getTime())
    )
  ) {
    throw administrationError(
      "expires_at_invalid",
      "expires_at must be a valid timestamp or never.",
    );
  }
  if (
    configuration.expiresAt
    && configuration.expiresAt.getTime() <= configuration.effectiveAt.getTime()
  ) {
    throw administrationError(
      "entitlement_window_invalid",
      "expires_at must be later than effective_at.",
    );
  }
  if (edition === "free_local" && status === "active") {
    throw administrationError(
      "free_local_commercial_active_forbidden",
      "The trusted commercial operator CLI cannot activate a free_local entitlement.",
    );
  }
  if (status === "active") {
    if (configuration.effectiveAt.getTime() > now.getTime()) {
      throw administrationError(
        "active_entitlement_not_effective",
        "An active entitlement must already be effective.",
      );
    }
    if (
      configuration.expiresAt
      && configuration.expiresAt.getTime() <= now.getTime()
    ) {
      throw administrationError(
        "active_entitlement_expired",
        "An active entitlement cannot use an expired window.",
      );
    }
    if (!CAPABILITY_NAMES.some((name) => capabilities[name])) {
      throw administrationError(
        "active_entitlement_capability_required",
        "An active commercial entitlement must enable at least one capability.",
      );
    }
  }
  if (status === "expired") {
    if (
      !configuration.expiresAt
      || configuration.expiresAt.getTime() > now.getTime()
    ) {
      throw administrationError(
        "expired_entitlement_window_invalid",
        "An expired entitlement must have a completed expiration window.",
      );
    }
  } else if (
    status !== "active"
    && configuration.expiresAt
    && configuration.expiresAt.getTime() <= now.getTime()
  ) {
    throw administrationError(
      "entitlement_status_window_mismatch",
      "A completed expiration window must use expired status.",
    );
  }
  if (
    capabilities.session_issue
    && !capabilities.enrollment_issue
  ) {
    throw administrationError(
      "session_capability_requires_enrollment",
      "session_issue requires enrollment_issue.",
    );
  }
  if (
    capabilities.enrollment_issue
    && (
      configuration.maxAgents === 0
      || configuration.maxActiveEnrollments === 0
    )
  ) {
    throw administrationError(
      "enrollment_capability_quota_invalid",
      "enrollment_issue requires positive agent and enrollment quotas.",
    );
  }
  if (
    !capabilities.enrollment_issue
    && (
      configuration.maxAgents !== 0
      || configuration.maxActiveEnrollments !== 0
    )
  ) {
    throw administrationError(
      "disabled_enrollment_quota_nonzero",
      "Disabled enrollment_issue requires zero agent and enrollment quotas.",
    );
  }
  if (
    capabilities.session_issue
    && configuration.maxActiveSessionsPerAgent === 0
  ) {
    throw administrationError(
      "session_capability_quota_invalid",
      "session_issue requires a positive active-session quota.",
    );
  }
  if (
    !capabilities.session_issue
    && configuration.maxActiveSessionsPerAgent !== 0
  ) {
    throw administrationError(
      "disabled_session_quota_nonzero",
      "Disabled session_issue requires a zero active-session quota.",
    );
  }
  if (capabilities.run_start && configuration.maxMonthlyRuns === 0) {
    throw administrationError(
      "run_capability_quota_invalid",
      "run_start requires a positive monthly run quota.",
    );
  }
  if (
    !capabilities.run_start
    && (
      configuration.maxMonthlyRuns !== 0
      || maxMonthlyCostUsd !== "0.000000"
    )
  ) {
    throw administrationError(
      "disabled_run_quota_nonzero",
      "Disabled run_start requires zero monthly run and cost quotas.",
    );
  }
  return Object.freeze({
    edition,
    status,
    capabilities: Object.freeze({
      enrollment_issue: capabilities.enrollment_issue,
      session_issue: capabilities.session_issue,
      run_start: capabilities.run_start,
    }),
    maxAgents: configuration.maxAgents,
    maxActiveEnrollments: configuration.maxActiveEnrollments,
    maxActiveSessionsPerAgent: configuration.maxActiveSessionsPerAgent,
    maxMonthlyRuns: configuration.maxMonthlyRuns,
    maxMonthlyCostUsd,
    effectiveAt: new Date(configuration.effectiveAt.getTime()),
    expiresAt: configuration.expiresAt
      ? new Date(configuration.expiresAt.getTime())
      : null,
  });
}

export function parseWorkspaceEntitlementArguments(
  argv: string[],
  now = new Date(),
): WorkspaceEntitlementAdministrationRequest {
  const values = new Map<string, string>();
  const booleans = new Set<string>();
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument.includes("=")) {
      throw administrationError(
        "invalid_arguments",
        "Arguments must use separate flag and value tokens.",
      );
    }
    if (BOOLEAN_ARGUMENTS.has(argument)) {
      if (booleans.has(argument)) {
        throw administrationError(
          "duplicate_argument",
          "Each argument may be supplied only once.",
        );
      }
      booleans.add(argument);
      continue;
    }
    if (!VALUE_ARGUMENTS.has(argument)) {
      throw administrationError(
        "unknown_argument",
        "The entitlement administration command received an unsupported argument.",
      );
    }
    if (values.has(argument)) {
      throw administrationError(
        "duplicate_argument",
        "Each argument may be supplied only once.",
      );
    }
    const value = argv[index + 1];
    if (!value || value.startsWith("--")) {
      throw administrationError(
        "argument_value_required",
        "Entitlement administration value arguments require a value.",
      );
    }
    values.set(argument, value);
    index += 1;
  }
  for (const required of REQUIRED_VALUE_ARGUMENTS) {
    if (!values.has(required)) {
      throw administrationError(
        "required_argument_missing",
        "All entitlement identity, policy, quota, and time-window arguments are required.",
      );
    }
  }
  const confirm = booleans.has("--confirm");
  const expectAbsent = booleans.has("--expect-absent");
  const expectedRevision = values.get("--expected-revision");
  if (expectAbsent && expectedRevision) {
    throw administrationError(
      "optimistic_guard_ambiguous",
      "Use exactly one optimistic concurrency guard.",
    );
  }
  if (confirm && !expectAbsent && !expectedRevision) {
    throw administrationError(
      "confirm_guard_required",
      "Confirmed creation requires expect-absent and confirmed update requires expected-revision.",
    );
  }
  if (expectedRevision && !REVISION_PATTERN.test(expectedRevision)) {
    throw administrationError(
      "expected_revision_invalid",
      "expected-revision must be a lowercase SHA-256 value.",
    );
  }
  const edition = requireKnownValue(
    String(values.get("--edition") || ""),
    EDITIONS,
    "edition",
  );
  const status = requireKnownValue(
    String(values.get("--status") || ""),
    STATUSES,
    "status",
  );
  const expiresValue = String(values.get("--expires-at") || "");
  const configuration = validateConfiguration(
    {
      edition,
      status,
      capabilities: parseCapabilities(
        String(values.get("--capabilities") || ""),
      ),
      maxAgents: parseInteger(
        String(values.get("--max-agents") || ""),
        "max_agents",
      ),
      maxActiveEnrollments: parseInteger(
        String(values.get("--max-active-enrollments") || ""),
        "max_active_enrollments",
      ),
      maxActiveSessionsPerAgent: parseInteger(
        String(values.get("--max-active-sessions-per-agent") || ""),
        "max_active_sessions_per_agent",
      ),
      maxMonthlyRuns: parseInteger(
        String(values.get("--max-monthly-runs") || ""),
        "max_monthly_runs",
      ),
      maxMonthlyCostUsd: canonicalCost(
        String(values.get("--max-monthly-cost-usd") || ""),
      ),
      effectiveAt: parseUtcTimestamp(
        String(values.get("--effective-at") || ""),
        "effective_at",
      ),
      expiresAt: expiresValue === "never"
        ? null
        : parseUtcTimestamp(expiresValue, "expires_at"),
    },
    now,
  );
  return Object.freeze({
    workspaceId: identifier(
      values.get("--workspace-id"),
      "workspace_id",
    ),
    operatorUserId: identifier(
      values.get("--operator-user-id"),
      "operator_user_id",
    ),
    confirm,
    guard: expectAbsent
      ? Object.freeze({ kind: "expect_absent" as const })
      : expectedRevision
        ? Object.freeze({
            kind: "expected_revision" as const,
            revision: expectedRevision,
          })
        : Object.freeze({ kind: "none" as const }),
    configuration,
  });
}

function canonicalDesired(
  workspaceId: string,
  configuration: WorkspaceEntitlementConfiguration,
): CanonicalConfiguration {
  return Object.freeze({
    workspace_id: workspaceId,
    edition: configuration.edition,
    status: configuration.status,
    capabilities_json: configuration.capabilities,
    max_agents: configuration.maxAgents,
    max_active_enrollments: configuration.maxActiveEnrollments,
    max_active_sessions_per_agent:
      configuration.maxActiveSessionsPerAgent,
    max_monthly_runs: configuration.maxMonthlyRuns,
    max_monthly_cost_usd: configuration.maxMonthlyCostUsd,
    effective_at: configuration.effectiveAt.toISOString(),
    expires_at: configuration.expiresAt?.toISOString() || null,
  });
}

function canonicalStored(row: EntitlementRow): CanonicalConfiguration {
  const effectiveAt = new Date(row.effective_at);
  const expiresAt = row.expires_at === null
    ? null
    : new Date(row.expires_at);
  if (
    !Number.isFinite(effectiveAt.getTime())
    || (expiresAt && !Number.isFinite(expiresAt.getTime()))
  ) {
    throw administrationError(
      "stored_entitlement_invalid",
      "The stored entitlement cannot be safely administered.",
      1,
    );
  }
  return Object.freeze({
    workspace_id: row.workspace_id,
    edition: row.edition,
    status: row.status,
    capabilities_json: row.capabilities_json,
    max_agents: Number(row.max_agents),
    max_active_enrollments: Number(row.max_active_enrollments),
    max_active_sessions_per_agent:
      Number(row.max_active_sessions_per_agent),
    max_monthly_runs: Number(row.max_monthly_runs),
    max_monthly_cost_usd: canonicalStoredCost(
      row.max_monthly_cost_usd,
    ),
    effective_at: effectiveAt.toISOString(),
    expires_at: expiresAt?.toISOString() || null,
  });
}

function revisionHash(workspaceId: string, updatedAtValue: string) {
  return sha256(
    `${WORKSPACE_ENTITLEMENT_ADMINISTRATION_CONTRACT}:${workspaceId}:${updatedAtValue}`,
  );
}

function safeRef(kind: "operator" | "audit", value: string) {
  return `${kind}_ref_${sha256(`${kind}:${value}`).slice(0, 16)}`;
}

function receipt(
  request: WorkspaceEntitlementAdministrationRequest,
  input: Readonly<{
    outcome: WorkspaceEntitlementAdministrationReceipt["outcome"];
    desiredConfigHash: string;
    previousConfigHash: string | null;
    revision: string | null;
    requiredGuard: "expect_absent" | "expected_revision" | null;
    auditId?: string;
  }>,
): WorkspaceEntitlementAdministrationReceipt {
  return Object.freeze({
    contract: WORKSPACE_ENTITLEMENT_ADMINISTRATION_CONTRACT,
    ok: true,
    mode: request.confirm ? "confirmed" : "plan",
    outcome: input.outcome,
    workspace_id: request.workspaceId,
    operator_ref: safeRef("operator", request.operatorUserId),
    desired_config_hash: input.desiredConfigHash,
    previous_config_hash: input.previousConfigHash,
    revision: input.revision,
    required_guard: input.requiredGuard,
    audit_appended: Boolean(input.auditId),
    audit_ref: input.auditId ? safeRef("audit", input.auditId) : null,
    lock: Object.freeze({
      scope: "workspace",
      transaction_scoped: true,
      acquired: true,
    }),
    credentials_omitted: true,
    dsn_omitted: true,
    raw_config_omitted: true,
    python_started: false,
    sqlite_used: false,
    external_network_used: false,
  });
}

async function requireTrustedOperator(
  client: PoolClient,
  workspaceId: string,
  operatorUserId: string,
  operatorPassword: string,
) {
  if (
    operatorPassword.length < HUMAN_PASSWORD_MIN_LENGTH
    || operatorPassword.length > HUMAN_PASSWORD_MAX_LENGTH
  ) {
    throw administrationError(
      "trusted_operator_required",
      "Entitlement administration requires verified Human operator credentials.",
      1,
    );
  }
  const credential = (await client.query<TrustedCredentialRow>(
    `SELECT credential.status AS credential_status,
      credential.password_hash,
      credential.password_salt,
      credential.password_params_json
    FROM human_login_credentials credential
    WHERE credential.user_id=$1
    ORDER BY credential.created_at DESC
    LIMIT 1
    FOR UPDATE`,
    [operatorUserId],
  )).rows[0];
  const user = (await client.query<TrustedUserRow>(
    `SELECT role AS user_role FROM users
    WHERE user_id=$1 FOR UPDATE`,
    [operatorUserId],
  )).rows[0];
  const membership = (await client.query<TrustedMembershipRow>(
    `SELECT role AS membership_role,status AS membership_status
    FROM workspace_memberships
    WHERE user_id=$1 AND workspace_id=$2 FOR UPDATE`,
    [operatorUserId, workspaceId],
  )).rows[0];
  const operator = credential && user && membership
    ? { ...credential, ...user, ...membership }
    : undefined;
  let params: Record<string, unknown> = {};
  try {
    const parsed = typeof operator?.password_params_json === "string"
      ? JSON.parse(operator.password_params_json)
      : operator?.password_params_json;
    params = parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? parsed as Record<string, unknown>
      : {};
  } catch {
    params = {};
  }
  let passwordMatches = false;
  if (
    operator
    && operator.credential_status === "active"
    && params.name === HUMAN_SCRYPT_PARAMS.name
    && params.n === HUMAN_SCRYPT_PARAMS.n
    && params.r === HUMAN_SCRYPT_PARAMS.r
    && params.p === HUMAN_SCRYPT_PARAMS.p
    && params.keylen === HUMAN_SCRYPT_PARAMS.keylen
    && /^[a-f0-9]{32}$/i.test(operator.password_salt)
    && /^[a-f0-9]{64}$/i.test(operator.password_hash)
  ) {
    const derived = await deriveOperatorPassword(
      operatorPassword,
      Buffer.from(operator.password_salt, "hex"),
    );
    passwordMatches = timingSafeEqual(
      derived,
      Buffer.from(operator.password_hash, "hex"),
    );
  }
  if (
    !operator
    || operator.membership_status !== "active"
    || !TRUSTED_OPERATOR_ROLES.has(operator.user_role.trim().toLowerCase())
    || !TRUSTED_OPERATOR_ROLES.has(
      operator.membership_role.trim().toLowerCase(),
    )
    || !passwordMatches
  ) {
    throw administrationError(
      "trusted_operator_required",
      "Entitlement administration requires verified Human operator credentials.",
      1,
    );
  }
}

async function readEntitlement(
  client: PoolClient,
  workspaceId: string,
  lockRow: boolean,
) {
  const result = await client.query<EntitlementRow>(
    `SELECT workspace_id,edition,status,capabilities_json,max_agents,
      max_active_enrollments,max_active_sessions_per_agent,max_monthly_runs,
      max_monthly_cost_usd::text AS max_monthly_cost_usd,
      effective_at,expires_at,updated_by_user_id,
      to_char(
        updated_at AT TIME ZONE 'UTC',
        'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
      ) AS updated_at_value
    FROM workspace_entitlements
    WHERE workspace_id=$1
    ${lockRow ? "FOR UPDATE" : ""}`,
    [workspaceId],
  );
  return result.rows[0] || null;
}

async function insertEntitlement(
  client: PoolClient,
  request: WorkspaceEntitlementAdministrationRequest,
  configuration: WorkspaceEntitlementConfiguration,
) {
  try {
    const result = await client.query<{ updated_at_value: string }>(
      `INSERT INTO workspace_entitlements(
        workspace_id,edition,status,capabilities_json,max_agents,
        max_active_enrollments,max_active_sessions_per_agent,max_monthly_runs,
        max_monthly_cost_usd,effective_at,expires_at,created_at,updated_at,
        updated_by_user_id
      ) VALUES(
        $1,$2,$3,$4::jsonb,$5,$6,$7,$8,$9::numeric,$10::timestamptz,
        $11::timestamptz,clock_timestamp(),clock_timestamp(),$12
      )
      RETURNING to_char(
        updated_at AT TIME ZONE 'UTC',
        'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
      ) AS updated_at_value`,
      [
        request.workspaceId,
        configuration.edition,
        configuration.status,
        JSON.stringify(configuration.capabilities),
        configuration.maxAgents,
        configuration.maxActiveEnrollments,
        configuration.maxActiveSessionsPerAgent,
        configuration.maxMonthlyRuns,
        configuration.maxMonthlyCostUsd,
        configuration.effectiveAt.toISOString(),
        configuration.expiresAt?.toISOString() || null,
        request.operatorUserId,
      ],
    );
    const updatedAt = result.rows[0]?.updated_at_value;
    if (!updatedAt) {
      throw administrationError(
        "entitlement_create_failed",
        "The entitlement create did not return a revision.",
        1,
      );
    }
    return updatedAt;
  } catch (error) {
    if (
      error
      && typeof error === "object"
      && "code" in error
      && error.code === "23505"
    ) {
      throw administrationError(
        "entitlement_create_conflict",
        "The entitlement was created concurrently; no configuration was overwritten.",
        1,
      );
    }
    throw error;
  }
}

async function updateEntitlement(
  client: PoolClient,
  request: WorkspaceEntitlementAdministrationRequest,
  configuration: WorkspaceEntitlementConfiguration,
  currentUpdatedAt: string,
) {
  const result = await client.query<{ updated_at_value: string }>(
    `UPDATE workspace_entitlements SET
      edition=$2,
      status=$3,
      capabilities_json=$4::jsonb,
      max_agents=$5,
      max_active_enrollments=$6,
      max_active_sessions_per_agent=$7,
      max_monthly_runs=$8,
      max_monthly_cost_usd=$9::numeric,
      effective_at=$10::timestamptz,
      expires_at=$11::timestamptz,
      updated_at=GREATEST(
        clock_timestamp(),
        updated_at + interval '1 microsecond'
      ),
      updated_by_user_id=$12
    WHERE workspace_id=$1
      AND updated_at=$13::timestamptz
    RETURNING to_char(
      updated_at AT TIME ZONE 'UTC',
      'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
    ) AS updated_at_value`,
    [
      request.workspaceId,
      configuration.edition,
      configuration.status,
      JSON.stringify(configuration.capabilities),
      configuration.maxAgents,
      configuration.maxActiveEnrollments,
      configuration.maxActiveSessionsPerAgent,
      configuration.maxMonthlyRuns,
      configuration.maxMonthlyCostUsd,
      configuration.effectiveAt.toISOString(),
      configuration.expiresAt?.toISOString() || null,
      request.operatorUserId,
      currentUpdatedAt,
    ],
  );
  const updatedAt = result.rows[0]?.updated_at_value;
  if (result.rowCount !== 1 || !updatedAt) {
    throw administrationError(
      "entitlement_revision_stale",
      "The entitlement changed concurrently; no configuration was overwritten.",
      1,
    );
  }
  return updatedAt;
}

async function appendConfigurationAudit(
  client: PoolClient,
  request: WorkspaceEntitlementAdministrationRequest,
  operation: "created" | "updated",
  desired: CanonicalConfiguration,
  desiredConfigHash: string,
  previous: CanonicalConfiguration | null,
  previousConfigHash: string | null,
  previousRevision: string | null,
) {
  const operatorRef = safeRef("operator", request.operatorUserId);
  const requestHash = stableHash({
    contract: WORKSPACE_ENTITLEMENT_ADMINISTRATION_CONTRACT,
    workspace_id: request.workspaceId,
    operator_ref: operatorRef,
    operation,
    desired_config_hash: desiredConfigHash,
    previous_revision: previousRevision,
  });
  return appendAudit(client, {
    workspaceId: request.workspaceId,
    actorType: "user",
    actorId: request.operatorUserId,
    action: `workspace_entitlement.${operation}`,
    entityType: "workspace_entitlements",
    entityId: request.workspaceId,
    before: previous === null ? undefined : previous,
    after: desired,
    requestHash,
    metadata: {
      administration_contract:
        WORKSPACE_ENTITLEMENT_ADMINISTRATION_CONTRACT,
      operation,
      operator_ref: operatorRef,
      desired_config_hash: desiredConfigHash,
      previous_config_hash: previousConfigHash,
      previous_revision: previousRevision,
      optimistic_guard: request.guard.kind,
      credentials_omitted: true,
      dsn_omitted: true,
      raw_config_omitted: true,
    },
  });
}

async function administerInsideTransaction(
  client: PoolClient,
  request: WorkspaceEntitlementAdministrationRequest,
  now: Date,
  operatorPassword: string,
) {
  const workspaceId = identifier(request.workspaceId, "workspace_id");
  const operatorUserId = identifier(
    request.operatorUserId,
    "operator_user_id",
  );
  if (typeof request.confirm !== "boolean") {
    throw administrationError(
      "confirm_invalid",
      "confirm must be an explicit boolean.",
    );
  }
  if (
    !request.guard
    || !["none", "expect_absent", "expected_revision"].includes(
      request.guard.kind,
    )
  ) {
    throw administrationError(
      "optimistic_guard_invalid",
      "The optimistic concurrency guard is invalid.",
    );
  }
  if (
    request.guard.kind === "expected_revision"
    && !REVISION_PATTERN.test(request.guard.revision)
  ) {
    throw administrationError(
      "expected_revision_invalid",
      "expected-revision must be a lowercase SHA-256 value.",
    );
  }
  if (request.confirm && request.guard.kind === "none") {
    throw administrationError(
      "confirm_guard_required",
      "Confirmed changes require an explicit optimistic concurrency guard.",
    );
  }
  const configuration = validateConfiguration(request.configuration, now);
  const normalizedRequest = Object.freeze({
    workspaceId,
    operatorUserId,
    confirm: request.confirm,
    guard: request.guard,
    configuration,
  });
  await client.query(
    "SELECT pg_advisory_xact_lock(hashtextextended($1,0))",
    [`agentops:workspace-entitlement:${workspaceId}`],
  );
  await requireTrustedOperator(
    client,
    workspaceId,
    operatorUserId,
    operatorPassword,
  );
  const existing = await readEntitlement(
    client,
    workspaceId,
    request.confirm,
  );
  const desired = canonicalDesired(workspaceId, configuration);
  const desiredConfigHash = stableHash(desired);
  const previous = existing ? canonicalStored(existing) : null;
  const previousConfigHash = previous ? stableHash(previous) : null;
  const currentRevision = existing
    ? revisionHash(workspaceId, existing.updated_at_value)
    : null;

  if (existing && previousConfigHash === desiredConfigHash) {
    return receipt(normalizedRequest, {
      outcome: "unchanged",
      desiredConfigHash,
      previousConfigHash,
      revision: currentRevision,
      requiredGuard: null,
    });
  }

  if (!existing) {
    if (request.guard.kind === "expected_revision") {
      throw administrationError(
        "entitlement_absent",
        "The expected entitlement does not exist; no configuration was written.",
        1,
      );
    }
    if (!request.confirm) {
      return receipt(normalizedRequest, {
        outcome: "would_create",
        desiredConfigHash,
        previousConfigHash: null,
        revision: null,
        requiredGuard: "expect_absent",
      });
    }
    if (request.guard.kind !== "expect_absent") {
      throw administrationError(
        "expect_absent_required",
        "Confirmed creation requires expect-absent.",
      );
    }
    const updatedAt = await insertEntitlement(
      client,
      normalizedRequest,
      configuration,
    );
    const audit = await appendConfigurationAudit(
      client,
      normalizedRequest,
      "created",
      desired,
      desiredConfigHash,
      null,
      null,
      null,
    );
    return receipt(normalizedRequest, {
      outcome: "created",
      desiredConfigHash,
      previousConfigHash: null,
      revision: revisionHash(workspaceId, updatedAt),
      requiredGuard: null,
      auditId: audit.auditId,
    });
  }

  if (request.guard.kind === "expect_absent") {
    throw administrationError(
      "entitlement_already_exists",
      "An entitlement already exists; no configuration was overwritten.",
      1,
    );
  }
  if (
    request.guard.kind === "expected_revision"
    && request.guard.revision !== currentRevision
  ) {
    throw administrationError(
      "entitlement_revision_stale",
      "The entitlement revision is stale; no configuration was overwritten.",
      1,
    );
  }
  if (!request.confirm) {
    return receipt(normalizedRequest, {
      outcome: "would_update",
      desiredConfigHash,
      previousConfigHash,
      revision: currentRevision,
      requiredGuard: "expected_revision",
    });
  }
  if (request.guard.kind !== "expected_revision") {
    throw administrationError(
      "expected_revision_required",
      "Confirmed update requires expected-revision.",
    );
  }
  const updatedAt = await updateEntitlement(
    client,
    normalizedRequest,
    configuration,
    existing.updated_at_value,
  );
  const audit = await appendConfigurationAudit(
    client,
    normalizedRequest,
    "updated",
    desired,
    desiredConfigHash,
    previous,
    previousConfigHash,
    currentRevision,
  );
  return receipt(normalizedRequest, {
    outcome: "updated",
    desiredConfigHash,
    previousConfigHash,
    revision: revisionHash(workspaceId, updatedAt),
    requiredGuard: null,
    auditId: audit.auditId,
  });
}

export async function executeWorkspaceEntitlementAdministration(
  client: Client,
  request: WorkspaceEntitlementAdministrationRequest,
  options: Readonly<{
    now?: Date;
    operatorPassword: string;
  }>,
) {
  if (options.now && !Number.isFinite(options.now.getTime())) {
    throw administrationError(
      "administration_time_invalid",
      "The administration evaluation time is invalid.",
    );
  }
  let transactionStarted = false;
  try {
    await client.query("BEGIN");
    transactionStarted = true;
    await client.query("SET LOCAL lock_timeout = '5s'");
    await client.query("SET LOCAL statement_timeout = '30s'");
    const clock = options.now
      ? options.now
      : (await client.query<{ now: Date | string }>(
        "SELECT clock_timestamp() AS now",
      )).rows[0]?.now;
    const now = clock instanceof Date ? clock : new Date(String(clock || ""));
    if (!Number.isFinite(now.getTime())) {
      throw administrationError(
        "administration_time_invalid",
        "The database administration clock is invalid.",
      );
    }
    const result = await administerInsideTransaction(
      client as unknown as PoolClient,
      request,
      now,
      options.operatorPassword,
    );
    if (request.confirm) await client.query("COMMIT");
    else await client.query("ROLLBACK");
    transactionStarted = false;
    return result;
  } catch (error) {
    if (transactionStarted) {
      await client.query("ROLLBACK").catch(() => undefined);
    }
    throw error;
  }
}

export async function runWorkspaceEntitlementCli(argv: string[]) {
  const request = parseWorkspaceEntitlementArguments(argv);
  const connectionString = postgresDsn();
  const operatorPassword = String(
    process.env.AGENTOPS_ENTITLEMENT_OPERATOR_PASSWORD || "",
  );
  if (!operatorPassword) {
    throw administrationError(
      "operator_password_required",
      "Set AGENTOPS_ENTITLEMENT_OPERATOR_PASSWORD for the intended Human operator.",
      1,
    );
  }
  let client: Client | undefined;
  try {
    const readiness = await runPostgresSchemaCommand("check");
    client = new Client({
      connectionString,
      ssl: postgresSslEnabled() ? { rejectUnauthorized: true } : undefined,
      application_name: "agentops-entitlement-operator-cli",
    });
    await client.connect();
    const result = await executeWorkspaceEntitlementAdministration(
      client,
      request,
      { operatorPassword },
    );
    return Object.freeze({
      ...result,
      schema_contract: readiness.schema_contract,
    });
  } catch (error) {
    if (error instanceof WorkspaceEntitlementAdministrationError) throw error;
    if (error instanceof SchemaReadinessError) {
      throw administrationError(
        error.code,
        "Workspace entitlement administration requires the current commercial PostgreSQL schema.",
        1,
      );
    }
    throw administrationError(
      "workspace_entitlement_administration_failed",
      "Workspace entitlement administration failed closed; no unsafe detail was emitted.",
      1,
    );
  } finally {
    await client?.end().catch(() => undefined);
  }
}

function output(payload: Record<string, unknown>) {
  process.stdout.write(`${JSON.stringify(payload)}\n`);
}

function isMainModule() {
  const invoked = process.argv[1] ? resolve(process.argv[1]) : "";
  return Boolean(invoked) && invoked === fileURLToPath(import.meta.url);
}

if (isMainModule()) {
  runWorkspaceEntitlementCli(process.argv.slice(2))
    .then((result) => output(result))
    .catch((error: unknown) => {
      const failure = error instanceof WorkspaceEntitlementAdministrationError
        ? error
        : administrationError(
            "workspace_entitlement_administration_failed",
            "Workspace entitlement administration failed closed.",
            1,
          );
      output({
        contract: WORKSPACE_ENTITLEMENT_ADMINISTRATION_CONTRACT,
        ok: false,
        error: failure.code,
        message: failure.message,
        credentials_omitted: true,
        dsn_omitted: true,
        raw_config_omitted: true,
        python_started: false,
        sqlite_used: false,
        external_network_used: false,
      });
      process.exitCode = failure.exitCode;
    });
}
