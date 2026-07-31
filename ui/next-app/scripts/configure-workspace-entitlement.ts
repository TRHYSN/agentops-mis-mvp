import { createHash } from "node:crypto";
import { resolve } from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

import { Client } from "pg";

import {
  postgresApplicationSchema,
  postgresEntitlementAdminRole,
  postgresEntitlementAdminDsn,
  postgresRuntimeApiSchema,
  postgresSslEnabled,
  secretEnvironmentValue,
} from "../src/server/controlPlane/config";
import { stableHash } from "../src/server/controlPlane/ledger";
import {
  assertPostgresEntitlementAdminRoleBoundary,
  SchemaReadinessError,
} from "../src/server/controlPlane/schemaReadiness";
import {
  parseWorkspaceEntitlementAdminChallengeRequest,
  WORKSPACE_ENTITLEMENT_ADMINISTRATION_REQUEST_CONTRACT,
  type CanonicalWorkspaceEntitlementAdminChallengeRequest,
} from "../src/server/controlPlane/workspaceEntitlementAdminChallenges";

export const WORKSPACE_ENTITLEMENT_ADMINISTRATION_CONTRACT =
  WORKSPACE_ENTITLEMENT_ADMINISTRATION_REQUEST_CONTRACT;

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
  "--max-concurrent-runs",
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
  "--max-concurrent-runs",
  "--max-monthly-runs",
  "--max-monthly-cost-usd",
  "--effective-at",
  "--expires-at",
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
  maxConcurrentRuns: number;
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
  challenge_consumed: true;
  human_session_consumed: true;
  long_lived_credentials_omitted: true;
  challenge_token_omitted: true;
  control_plane_network_used: true;
  python_started: false;
  sqlite_used: false;
  external_network_used: true;
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
    ["max_concurrent_runs", configuration.maxConcurrentRuns],
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
  if (
    capabilities.run_start
    && (
      configuration.maxMonthlyRuns === 0
      || configuration.maxConcurrentRuns === 0
      || maxMonthlyCostUsd === "0.000000"
    )
  ) {
    throw administrationError(
      "run_capability_quota_invalid",
      "run_start requires positive concurrent, monthly run, and monthly cost quotas.",
    );
  }
  if (
    !capabilities.run_start
    && (
      configuration.maxMonthlyRuns !== 0
      || configuration.maxConcurrentRuns !== 0
      || maxMonthlyCostUsd !== "0.000000"
    )
  ) {
    throw administrationError(
      "disabled_run_quota_nonzero",
      "Disabled run_start requires zero concurrent, monthly run, and cost quotas.",
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
    maxConcurrentRuns: configuration.maxConcurrentRuns,
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
      maxConcurrentRuns: parseInteger(
        String(values.get("--max-concurrent-runs") || ""),
        "max_concurrent_runs",
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

function canonicalChallengeRequest(
  request: WorkspaceEntitlementAdministrationRequest,
): CanonicalWorkspaceEntitlementAdminChallengeRequest {
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

function safeRef(kind: "operator" | "audit", value: string) {
  return `${kind}_ref_${sha256(`${kind}:${value}`).slice(0, 16)}`;
}

type EntitlementChallenge = Readonly<{
  challengeId: string;
  challengeToken: string;
  request: CanonicalWorkspaceEntitlementAdminChallengeRequest;
}>;

type DatabaseAdministrationReceipt = {
  contract?: unknown;
  ok?: unknown;
  mode?: unknown;
  outcome?: unknown;
  workspace_id?: unknown;
  desired_config_hash?: unknown;
  previous_config_hash?: unknown;
  revision?: unknown;
  required_guard?: unknown;
  audit_appended?: unknown;
  audit_ref?: unknown;
  challenge_consumed?: unknown;
  raw_config_omitted?: unknown;
  credentials_omitted?: unknown;
  token_omitted?: unknown;
  error_code?: unknown;
};

function configuredControlPlaneEndpoint() {
  const raw = String(
    process.env.AGENTOPS_ENTITLEMENT_CONTROL_PLANE_URL || "",
  ).trim();
  if (!raw) {
    throw administrationError(
      "entitlement_control_plane_url_required",
      "Set AGENTOPS_ENTITLEMENT_CONTROL_PLANE_URL to the TypeScript control plane.",
      1,
    );
  }
  let endpoint: URL;
  try {
    endpoint = new URL(raw);
  } catch {
    throw administrationError(
      "entitlement_control_plane_url_invalid",
      "The entitlement control-plane URL is invalid.",
      1,
    );
  }
  const loopback = ["127.0.0.1", "::1", "[::1]", "localhost"].includes(
    endpoint.hostname.toLowerCase(),
  );
  if (
    (endpoint.protocol !== "https:" && !(loopback && endpoint.protocol === "http:"))
    || endpoint.username
    || endpoint.password
    || !["", "/"].includes(endpoint.pathname)
    || endpoint.search
    || endpoint.hash
  ) {
    throw administrationError(
      "entitlement_control_plane_transport_forbidden",
      "Entitlement administration requires HTTPS, except for explicit loopback execution.",
      1,
    );
  }
  endpoint.pathname = endpoint.pathname.replace(/\/+$/, "") || "/";
  const configuredOrigin = String(
    process.env.AGENTOPS_ENTITLEMENT_CONTROL_PLANE_ORIGIN || "",
  ).trim();
  let origin = endpoint.origin;
  if (configuredOrigin) {
    try {
      const parsedOrigin = new URL(configuredOrigin);
      if (
        !["https:", ...(loopback ? ["http:"] : [])].includes(
          parsedOrigin.protocol,
        )
        || parsedOrigin.username
        || parsedOrigin.password
        || parsedOrigin.pathname !== "/"
        || parsedOrigin.search
        || parsedOrigin.hash
        || parsedOrigin.host.toLowerCase() !== endpoint.host.toLowerCase()
      ) {
        throw new Error("origin_invalid");
      }
      origin = parsedOrigin.origin;
    } catch {
      throw administrationError(
        "entitlement_control_plane_origin_invalid",
        "The entitlement control-plane Origin must match the request host.",
        1,
      );
    }
  }
  return Object.freeze({ endpoint, origin });
}

async function boundedResponseJson(response: Response) {
  const contentLength = Number(response.headers.get("content-length") || 0);
  if (Number.isFinite(contentLength) && contentLength > 64 * 1024) {
    throw administrationError(
      "entitlement_control_plane_response_invalid",
      "The entitlement control plane returned an oversized response.",
      1,
    );
  }
  const text = await response.text();
  if (Buffer.byteLength(text, "utf8") > 64 * 1024) {
    throw administrationError(
      "entitlement_control_plane_response_invalid",
      "The entitlement control plane returned an oversized response.",
      1,
    );
  }
  try {
    const parsed = JSON.parse(text);
    if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") {
      throw new Error("response_not_object");
    }
    return parsed as Record<string, unknown>;
  } catch {
    throw administrationError(
      "entitlement_control_plane_response_invalid",
      "The entitlement control plane returned an invalid response.",
      1,
    );
  }
}

function sessionCookie(response: Response) {
  const setCookie = String(response.headers.get("set-cookie") || "");
  const match = /(?:^|,\s*)(agentops_human_session=[^;,\r\n]+)/.exec(
    setCookie,
  );
  if (!match) {
    throw administrationError(
      "entitlement_human_session_missing",
      "The control plane did not return the required Human Session.",
      1,
    );
  }
  return match[1];
}

async function issueAdministrationChallenge(
  request: WorkspaceEntitlementAdministrationRequest,
  operatorPassword: string,
): Promise<EntitlementChallenge> {
  const username = String(
    process.env.AGENTOPS_ENTITLEMENT_OPERATOR_USERNAME || "",
  ).trim();
  if (!/^[a-z0-9][a-z0-9._-]{2,63}$/i.test(username)) {
    throw administrationError(
      "entitlement_operator_username_required",
      "Set AGENTOPS_ENTITLEMENT_OPERATOR_USERNAME for the intended Human operator.",
      1,
    );
  }
  const { endpoint, origin } = configuredControlPlaneEndpoint();
  const requestUrl = (path: string) => new URL(
    path,
    `${endpoint.toString().replace(/\/+$/, "")}/`,
  );
  const login = await fetch(requestUrl("/api/mis/human-auth/login"), {
    method: "POST",
    redirect: "error",
    cache: "no-store",
    signal: AbortSignal.timeout(30_000),
    headers: {
      "Content-Type": "application/json",
      Origin: origin,
    },
    body: JSON.stringify({ username, password: operatorPassword }),
  }).catch(() => {
    throw administrationError(
      "entitlement_human_login_failed",
      "The Human operator could not be authenticated.",
      1,
    );
  });
  const loginBody = await boundedResponseJson(login);
  const user = loginBody.user && typeof loginBody.user === "object"
    ? loginBody.user as Record<string, unknown>
    : {};
  const csrf = String(loginBody.csrf_token || "");
  if (
    !login.ok
    || loginBody.ok !== true
    || String(user.user_id || "") !== request.operatorUserId
    || !/^[a-f0-9]{64}$/.test(csrf)
  ) {
    throw administrationError(
      "trusted_operator_required",
      "Entitlement administration requires verified Human operator credentials.",
      1,
    );
  }
  const cookie = sessionCookie(login);
  const canonicalRequest = canonicalChallengeRequest(request);
  const challenge = await fetch(
    requestUrl(
      `/api/mis/workspaces/${
        encodeURIComponent(request.workspaceId)
      }/entitlement-admin/challenges`,
    ),
    {
      method: "POST",
      redirect: "error",
      cache: "no-store",
      signal: AbortSignal.timeout(30_000),
      headers: {
        "Content-Type": "application/json",
        Cookie: cookie,
        Origin: origin,
        "X-AgentOps-CSRF": csrf,
        "X-AgentOps-Workspace-Id": request.workspaceId,
      },
      body: JSON.stringify(canonicalRequest),
    },
  ).catch(() => {
    throw administrationError(
      "entitlement_challenge_issue_failed",
      "The entitlement challenge could not be issued.",
      1,
    );
  });
  const challengeBody = await boundedResponseJson(challenge);
  const challengeId = String(challengeBody.challenge_id || "");
  const challengeToken = String(challengeBody.challenge_token || "");
  if (
    !challenge.ok
    || challengeBody.ok !== true
    || challengeBody.human_session_consumed !== true
    || challengeBody.single_use !== true
    || challengeBody.workspace_id !== request.workspaceId
    || challengeBody.operator_user_id !== request.operatorUserId
    || challengeBody.mode !== canonicalRequest.mode
    || challengeBody.request_sha256 !== stableHash(canonicalRequest)
    || !IDENTIFIER_PATTERN.test(challengeId)
    || !/^[A-Za-z0-9_-]{43}$/.test(challengeToken)
  ) {
    throw administrationError(
      "entitlement_challenge_issue_failed",
      "The control plane did not prove a complete single-use challenge.",
      1,
    );
  }
  return Object.freeze({
    challengeId,
    challengeToken,
    request: canonicalRequest,
  });
}

function finalizedAdministrationReceipt(
  request: WorkspaceEntitlementAdministrationRequest,
  value: unknown,
):
  | Readonly<{
    ok: true;
    receipt: WorkspaceEntitlementAdministrationReceipt;
  }>
  | Readonly<{ ok: false; errorCode: string }> {
  if (!value || Array.isArray(value) || typeof value !== "object") {
    throw administrationError(
      "entitlement_admin_database_receipt_invalid",
      "PostgreSQL returned an invalid entitlement administration receipt.",
      1,
    );
  }
  const database = value as DatabaseAdministrationReceipt;
  const expectedMode = request.confirm ? "confirmed" : "plan";
  if (
    database.contract !== WORKSPACE_ENTITLEMENT_ADMINISTRATION_CONTRACT
    || database.mode !== expectedMode
    || database.workspace_id !== request.workspaceId
    || database.challenge_consumed !== true
    || database.raw_config_omitted !== true
    || database.credentials_omitted !== true
    || database.token_omitted !== true
  ) {
    throw administrationError(
      "entitlement_admin_database_receipt_invalid",
      "PostgreSQL did not prove the bound challenge result.",
      1,
    );
  }
  if (database.ok === false) {
    const code = String(database.error_code || "");
    if (
      ![
        "entitlement_revision_stale",
        "expect_absent_required",
        "entitlement_already_exists",
        "entitlement_absent",
      ].includes(code)
      || database.audit_appended !== false
    ) {
      throw administrationError(
        "entitlement_admin_database_receipt_invalid",
        "PostgreSQL returned an unsupported entitlement rejection.",
        1,
      );
    }
    return Object.freeze({ ok: false, errorCode: code });
  }
  if (database.ok !== true) {
    throw administrationError(
      "entitlement_admin_database_receipt_invalid",
      "PostgreSQL omitted the entitlement result status.",
      1,
    );
  }
  const outcomes = new Set([
    "would_create",
    "would_update",
    "created",
    "updated",
    "unchanged",
  ]);
  const outcome = String(database.outcome || "");
  const previousConfigHash = database.previous_config_hash === null
    ? null
    : String(database.previous_config_hash || "");
  const revision = database.revision === null
    ? null
    : String(database.revision || "");
  const requiredGuard = database.required_guard === null
    ? null
    : String(database.required_guard || "");
  const auditRef = database.audit_ref === undefined
    || database.audit_ref === null
    ? null
    : String(database.audit_ref);
  if (
    !outcomes.has(outcome)
    || !REVISION_PATTERN.test(String(database.desired_config_hash || ""))
    || (
      previousConfigHash !== null
      && !REVISION_PATTERN.test(previousConfigHash)
    )
    || (revision !== null && !REVISION_PATTERN.test(revision))
    || (
      requiredGuard !== null
      && !["expect_absent", "expected_revision"].includes(requiredGuard)
    )
    || typeof database.audit_appended !== "boolean"
    || (
      database.audit_appended
      && !/^audit_ref_[a-f0-9]{16}$/.test(String(auditRef || ""))
    )
    || (!database.audit_appended && auditRef !== null)
  ) {
    throw administrationError(
      "entitlement_admin_database_receipt_invalid",
      "PostgreSQL did not prove the complete entitlement administration result.",
      1,
    );
  }
  return Object.freeze({
    ok: true,
    receipt: Object.freeze({
      contract: WORKSPACE_ENTITLEMENT_ADMINISTRATION_CONTRACT,
      ok: true,
      mode: expectedMode,
      outcome:
        outcome as WorkspaceEntitlementAdministrationReceipt["outcome"],
      workspace_id: request.workspaceId,
      operator_ref: safeRef("operator", request.operatorUserId),
      desired_config_hash: String(database.desired_config_hash),
      previous_config_hash: previousConfigHash,
      revision,
      required_guard:
        requiredGuard as WorkspaceEntitlementAdministrationReceipt["required_guard"],
      audit_appended: database.audit_appended,
      audit_ref: auditRef,
      lock: Object.freeze({
        scope: "workspace" as const,
        transaction_scoped: true as const,
        acquired: true as const,
      }),
      credentials_omitted: true,
      dsn_omitted: true,
      raw_config_omitted: true,
      challenge_consumed: true,
      human_session_consumed: true,
      long_lived_credentials_omitted: true,
      challenge_token_omitted: true,
      control_plane_network_used: true,
      python_started: false,
      sqlite_used: false,
      external_network_used: true,
    }),
  });
}

export async function executeWorkspaceEntitlementAdministration(
  client: Client,
  request: WorkspaceEntitlementAdministrationRequest,
  options: Readonly<{
    challenge: EntitlementChallenge;
    runtimeApiSchema?: string;
  }>,
) {
  const canonicalRequest = canonicalChallengeRequest(request);
  if (
    stableHash(options.challenge.request) !== stableHash(canonicalRequest)
  ) {
    throw administrationError(
      "entitlement_challenge_request_mismatch",
      "The entitlement challenge is not bound to this request.",
    );
  }
  const schema = String(
    options.runtimeApiSchema || postgresRuntimeApiSchema(),
  );
  if (!/^[A-Za-z_][A-Za-z0-9_]{0,62}$/.test(schema)) {
    throw administrationError(
      "postgres_runtime_api_schema_invalid",
      "The entitlement administration API schema is invalid.",
      1,
    );
  }
  const functionName = request.confirm
    ? "agentops_apply_workspace_entitlement_v11"
    : "agentops_plan_workspace_entitlement_v11";
  let transactionStarted = false;
  let finalized:
    | ReturnType<typeof finalizedAdministrationReceipt>
    | undefined;
  try {
    await client.query("BEGIN");
    transactionStarted = true;
    await client.query("SET LOCAL lock_timeout = '5s'");
    await client.query("SET LOCAL statement_timeout = '30s'");
    const result = await client.query<{ receipt: unknown }>(
      `SELECT "${schema}"."${functionName}"(
         $1::text,$2::text,$3::jsonb
       ) AS receipt`,
      [
        options.challenge.challengeId,
        options.challenge.challengeToken,
        JSON.stringify(canonicalRequest),
      ],
    );
    finalized = finalizedAdministrationReceipt(
      request,
      result.rows[0]?.receipt,
    );
    await client.query("COMMIT");
    transactionStarted = false;
  } catch (error) {
    if (transactionStarted) {
      await client.query("ROLLBACK").catch(() => undefined);
    }
    throw error;
  }
  if (!finalized) {
    throw administrationError(
      "entitlement_admin_database_receipt_invalid",
      "PostgreSQL omitted the entitlement administration result.",
      1,
    );
  }
  if (!finalized.ok) {
    throw administrationError(
      finalized.errorCode,
      "The entitlement request was rejected and its challenge was consumed.",
      1,
    );
  }
  return finalized.receipt;
}

export async function runWorkspaceEntitlementCli(argv: string[]) {
  const request = parseWorkspaceEntitlementArguments(argv);
  const connectionString = postgresEntitlementAdminDsn();
  const operatorPassword = secretEnvironmentValue(
    "AGENTOPS_ENTITLEMENT_OPERATOR_PASSWORD",
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
    client = new Client({
      connectionString,
      ssl: postgresSslEnabled() ? { rejectUnauthorized: true } : undefined,
      application_name: "agentops-entitlement-operator-cli",
    });
    await client.connect();
    await assertPostgresEntitlementAdminRoleBoundary(client, {
      applicationSchema: postgresApplicationSchema(),
      runtimeApiSchema: postgresRuntimeApiSchema(),
      entitlementAdminRole: postgresEntitlementAdminRole(true),
    });
    const challenge = await issueAdministrationChallenge(
      request,
      operatorPassword,
    );
    const result = await executeWorkspaceEntitlementAdministration(
      client,
      request,
      { challenge },
    );
    return result;
  } catch (error) {
    if (error instanceof WorkspaceEntitlementAdministrationError) throw error;
    if (error instanceof SchemaReadinessError) {
      throw administrationError(
        error.code,
        "The entitlement administrator database boundary is not ready.",
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
