import {
  createHash,
  randomBytes,
} from "node:crypto";
import type { PoolClient, QueryResultRow } from "pg";

import {
  postgresRuntimeApiSchema,
} from "./config";
import { withPostgresTransaction } from "./db";
import {
  authenticateHumanWriteMember,
  rejectMachineCredentials,
  validateWriteOrigin,
  type HumanSessionIdentity,
} from "./humanSession";
import { ControlPlaneHttpError } from "./http";
import { stableHash } from "./ledger";

export const WORKSPACE_ENTITLEMENT_ADMIN_CHALLENGE_CONTRACT =
  "agentops_workspace_entitlement_admin_challenge_v11";
export const WORKSPACE_ENTITLEMENT_ADMINISTRATION_REQUEST_CONTRACT =
  "agentops_workspace_entitlement_administration_v2";
export const WORKSPACE_ENTITLEMENT_ADMIN_CHALLENGE_TTL_SECONDS = 75;
export const WORKSPACE_ENTITLEMENT_ADMIN_DATABASE_FUNCTIONS = Object.freeze({
  issue: "agentops_issue_workspace_entitlement_admin_challenge_v11",
  plan: "agentops_plan_workspace_entitlement_v11",
  apply: "agentops_apply_workspace_entitlement_v11",
});

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
const EDITIONS = new Set([
  "free_local",
  "pro_workspace",
  "team_governance",
  "enterprise_byoc",
]);
const STATUSES = new Set([
  "active",
  "inactive",
  "suspended",
  "expired",
]);
const CAPABILITIES = [
  "enrollment_issue",
  "session_issue",
  "run_start",
] as const;

type ChallengeMode = "plan" | "confirm";
type EntitlementGuard =
  | Readonly<{ kind: "none" }>
  | Readonly<{ kind: "expect_absent" }>
  | Readonly<{ kind: "expected_revision"; revision: string }>;

export type CanonicalWorkspaceEntitlementAdminChallengeRequest = Readonly<{
  contract: typeof WORKSPACE_ENTITLEMENT_ADMINISTRATION_REQUEST_CONTRACT;
  workspace_id: string;
  operator_user_id: string;
  mode: ChallengeMode;
  guard: EntitlementGuard;
  configuration: Readonly<{
    edition: string;
    status: string;
    capabilities: Readonly<Record<(typeof CAPABILITIES)[number], boolean>>;
    max_agents: number;
    max_active_enrollments: number;
    max_active_sessions_per_agent: number;
    max_concurrent_runs: number;
    max_monthly_runs: number;
    max_monthly_cost_usd: string;
    effective_at: string;
    expires_at: string | null;
  }>;
}>;

type ChallengeDatabaseReceipt = {
  contract?: unknown;
  challenge_id?: unknown;
  workspace_id?: unknown;
  operator_user_id?: unknown;
  mode?: unknown;
  request_sha256?: unknown;
  issued_at?: unknown;
  expires_at?: unknown;
  single_use?: unknown;
  session_revoked?: unknown;
};

function challengeError(code: string, message: string, status = 400) {
  return new ControlPlaneHttpError(status, code, message);
}

function objectValue(value: unknown, field: string) {
  if (!value || Array.isArray(value) || typeof value !== "object") {
    throw challengeError(
      `${field}_invalid`,
      `${field} must be a JSON object.`,
    );
  }
  return value as Record<string, unknown>;
}

function exactKeys(
  value: Record<string, unknown>,
  expected: readonly string[],
  field: string,
) {
  const allowed = new Set(expected);
  if (
    Object.keys(value).length !== expected.length
    || Object.keys(value).some((key) => !allowed.has(key))
  ) {
    throw challengeError(
      `${field}_invalid`,
      `${field} must contain exactly the supported fields.`,
    );
  }
}

function identifier(value: unknown, field: string) {
  const normalized = typeof value === "string" ? value.trim() : "";
  if (!IDENTIFIER_PATTERN.test(normalized)) {
    throw challengeError(
      `${field}_invalid`,
      `${field} must use 1-128 safe identifier characters.`,
    );
  }
  return normalized;
}

function integerQuota(value: unknown, field: string) {
  if (
    !Number.isSafeInteger(value)
    || Number(value) < 0
    || Number(value) > MAX_POSTGRES_INTEGER
  ) {
    throw challengeError(
      `${field}_invalid`,
      `${field} must be a non-negative PostgreSQL integer.`,
    );
  }
  return Number(value);
}

function canonicalCost(value: unknown) {
  if (typeof value !== "string") {
    throw challengeError(
      "max_monthly_cost_usd_invalid",
      "max_monthly_cost_usd must be an exact decimal string.",
    );
  }
  const match = /^(0|[1-9]\d{0,11})(?:\.(\d{1,6}))?$/.exec(value);
  if (!match) {
    throw challengeError(
      "max_monthly_cost_usd_invalid",
      "max_monthly_cost_usd must use at most six fractional digits.",
    );
  }
  if (BigInt(match[1]) > BigInt(MAX_EXACT_COST_WHOLE_USD)) {
    throw challengeError(
      "max_monthly_cost_usd_precision_unsafe",
      "max_monthly_cost_usd exceeds the exact evaluator range.",
    );
  }
  return `${match[1]}.${String(match[2] || "").padEnd(6, "0")}`;
}

function canonicalTimestamp(
  value: unknown,
  field: string,
  nullable?: false,
): string;
function canonicalTimestamp(
  value: unknown,
  field: string,
  nullable: true,
): string | null;
function canonicalTimestamp(
  value: unknown,
  field: string,
  nullable = false,
) {
  if (nullable && value === null) return null;
  if (typeof value !== "string" || !UTC_TIMESTAMP_PATTERN.test(value)) {
    throw challengeError(
      `${field}_invalid`,
      `${field} must be an unambiguous UTC RFC3339 timestamp${nullable ? " or null" : ""}.`,
    );
  }
  const parsed = new Date(value);
  const canonicalInput = value.includes(".")
    ? value.replace(
        /\.(\d{1,3})Z$/,
        (_, fraction: string) => `.${fraction.padEnd(3, "0")}Z`,
      )
    : value.replace(/Z$/, ".000Z");
  if (
    !Number.isFinite(parsed.getTime())
    || parsed.toISOString() !== canonicalInput
  ) {
    throw challengeError(
      `${field}_invalid`,
      `${field} must be a real UTC calendar timestamp.`,
    );
  }
  return parsed.toISOString();
}

type ChallengeQueryClient = {
  query<T extends QueryResultRow>(
    text: string,
    values?: unknown[],
  ): Promise<{ rows: T[] }>;
};

function canonicalGuard(value: unknown, mode: ChallengeMode): EntitlementGuard {
  const guard = objectValue(value, "guard");
  const kind = String(guard.kind || "");
  if (kind === "none" || kind === "expect_absent") {
    exactKeys(guard, ["kind"], "guard");
    if (mode === "confirm" && kind === "none") {
      throw challengeError(
        "confirm_guard_required",
        "Confirmed changes require an explicit optimistic guard.",
      );
    }
    return Object.freeze({ kind });
  }
  if (kind === "expected_revision") {
    exactKeys(guard, ["kind", "revision"], "guard");
    const revision = String(guard.revision || "");
    if (!REVISION_PATTERN.test(revision)) {
      throw challengeError(
        "expected_revision_invalid",
        "revision must be a lowercase SHA-256 value.",
      );
    }
    return Object.freeze({ kind, revision });
  }
  throw challengeError(
    "optimistic_guard_invalid",
    "The optimistic concurrency guard is invalid.",
  );
}

function canonicalConfiguration(value: unknown) {
  const configuration = objectValue(value, "configuration");
  exactKeys(configuration, [
    "edition",
    "status",
    "capabilities",
    "max_agents",
    "max_active_enrollments",
    "max_active_sessions_per_agent",
    "max_concurrent_runs",
    "max_monthly_runs",
    "max_monthly_cost_usd",
    "effective_at",
    "expires_at",
  ], "configuration");
  const edition = String(configuration.edition || "");
  const status = String(configuration.status || "");
  if (!EDITIONS.has(edition)) {
    throw challengeError("edition_invalid", "edition is not recognized.");
  }
  if (!STATUSES.has(status)) {
    throw challengeError("status_invalid", "status is not recognized.");
  }
  const rawCapabilities = objectValue(
    configuration.capabilities,
    "capabilities",
  );
  exactKeys(rawCapabilities, CAPABILITIES, "capabilities");
  const capabilities = Object.fromEntries(
    CAPABILITIES.map((name) => {
      if (typeof rawCapabilities[name] !== "boolean") {
        throw challengeError(
          "capabilities_invalid",
          "Each supported capability must be an explicit boolean.",
        );
      }
      return [name, rawCapabilities[name]];
    }),
  ) as Record<(typeof CAPABILITIES)[number], boolean>;
  const effectiveAt = canonicalTimestamp(
    configuration.effective_at,
    "effective_at",
  );
  const expiresAt = canonicalTimestamp(
    configuration.expires_at,
    "expires_at",
    true,
  );
  if (
    expiresAt
    && Date.parse(expiresAt) <= Date.parse(String(effectiveAt))
  ) {
    throw challengeError(
      "entitlement_window_invalid",
      "expires_at must be later than effective_at.",
    );
  }
  return Object.freeze({
    edition,
    status,
    capabilities: Object.freeze(capabilities),
    max_agents: integerQuota(configuration.max_agents, "max_agents"),
    max_active_enrollments: integerQuota(
      configuration.max_active_enrollments,
      "max_active_enrollments",
    ),
    max_active_sessions_per_agent: integerQuota(
      configuration.max_active_sessions_per_agent,
      "max_active_sessions_per_agent",
    ),
    max_concurrent_runs: integerQuota(
      configuration.max_concurrent_runs,
      "max_concurrent_runs",
    ),
    max_monthly_runs: integerQuota(
      configuration.max_monthly_runs,
      "max_monthly_runs",
    ),
    max_monthly_cost_usd: canonicalCost(
      configuration.max_monthly_cost_usd,
    ),
    effective_at: effectiveAt,
    expires_at: expiresAt,
  });
}

export function parseWorkspaceEntitlementAdminChallengeRequest(
  requestedWorkspaceId: unknown,
  value: unknown,
): CanonicalWorkspaceEntitlementAdminChallengeRequest {
  const body = objectValue(value, "challenge_request");
  exactKeys(
    body,
    [
      "contract",
      "workspace_id",
      "operator_user_id",
      "mode",
      "guard",
      "configuration",
    ],
    "challenge_request",
  );
  const workspaceId = identifier(requestedWorkspaceId, "workspace_id");
  const bodyWorkspaceId = identifier(body.workspace_id, "workspace_id");
  if (workspaceId !== bodyWorkspaceId) {
    throw challengeError(
      "entitlement_challenge_workspace_mismatch",
      "The path and body workspace bindings do not match.",
      403,
    );
  }
  if (body.contract !== WORKSPACE_ENTITLEMENT_ADMINISTRATION_REQUEST_CONTRACT) {
    throw challengeError(
      "entitlement_administration_contract_invalid",
      "The entitlement administration request contract is not supported.",
    );
  }
  const operatorUserId = identifier(
    body.operator_user_id,
    "operator_user_id",
  );
  const mode = String(body.mode || "");
  if (mode !== "plan" && mode !== "confirm") {
    throw challengeError(
      "challenge_mode_invalid",
      "mode must be plan or confirm.",
    );
  }
  return Object.freeze({
    contract: WORKSPACE_ENTITLEMENT_ADMINISTRATION_REQUEST_CONTRACT,
    workspace_id: workspaceId,
    operator_user_id: operatorUserId,
    mode,
    guard: canonicalGuard(body.guard, mode),
    configuration: canonicalConfiguration(body.configuration),
  });
}

function quotedIdentifier(value: string) {
  return `"${value.replaceAll("\"", "\"\"")}"`;
}

function clearSessionCookie(origin: URL) {
  const parts = [
    "agentops_human_session=",
    "Path=/",
    "HttpOnly",
    "SameSite=Strict",
  ];
  if (origin.protocol === "https:") parts.push("Secure");
  parts.push("Max-Age=0", "Expires=Thu, 01 Jan 1970 00:00:00 GMT");
  return parts.join("; ");
}

function challengeDatabaseReceipt(value: unknown) {
  if (!value || Array.isArray(value) || typeof value !== "object") {
    throw challengeError(
      "entitlement_challenge_database_receipt_invalid",
      "PostgreSQL returned an invalid challenge receipt.",
      503,
    );
  }
  return value as ChallengeDatabaseReceipt;
}

export async function issueWorkspaceEntitlementAdminChallengeWithClient(
  client: ChallengeQueryClient,
  identity: HumanSessionIdentity,
  request: CanonicalWorkspaceEntitlementAdminChallengeRequest,
  options: Readonly<{
    runtimeApiSchema?: string;
    challengeToken?: string;
    now?: Date;
  }> = {},
) {
  if (
    identity.workspaceId !== request.workspace_id
    || identity.userId !== request.operator_user_id
  ) {
    throw challengeError(
      "entitlement_challenge_identity_mismatch",
      "The Human Session identity does not match the bound request.",
      403,
    );
  }
  const role = String(identity.membershipRole || "").trim().toLowerCase();
  if (!TRUSTED_OPERATOR_ROLES.has(role)) {
    throw challengeError(
      "entitlement_challenge_role_forbidden",
      "Entitlement challenges require operator, workspace-admin, or owner authority.",
      403,
    );
  }
  const token = options.challengeToken
    || randomBytes(32).toString("base64url");
  if (!/^[A-Za-z0-9_-]{43}$/.test(token)) {
    throw new Error("entitlement_challenge_token_generation_failed");
  }
  const tokenSha256 = createHash("sha256")
    .update(token, "utf8")
    .digest("hex");
  const requestSha256 = stableHash(request);
  const schema = options.runtimeApiSchema || postgresRuntimeApiSchema();
  const result = await client.query<{ challenge: unknown }>(
    `SELECT ${quotedIdentifier(schema)}.${quotedIdentifier(
      WORKSPACE_ENTITLEMENT_ADMIN_DATABASE_FUNCTIONS.issue,
    )}(
      $1::text,$2::text,$3::text,$4::text,$5::jsonb,$6::text,$7::interval
    ) AS challenge`,
    [
      identity.sessionId,
      request.workspace_id,
      request.operator_user_id,
      request.mode,
      JSON.stringify(request),
      tokenSha256,
      `${WORKSPACE_ENTITLEMENT_ADMIN_CHALLENGE_TTL_SECONDS} seconds`,
    ],
  );
  const receipt = challengeDatabaseReceipt(result.rows[0]?.challenge);
  const issuedAt = new Date(String(receipt.issued_at || ""));
  const expiresAt = new Date(String(receipt.expires_at || ""));
  const ttlSeconds = (
    expiresAt.getTime() - issuedAt.getTime()
  ) / 1000;
  const now = options.now || new Date();
  const receiptValid = (
    receipt.contract === WORKSPACE_ENTITLEMENT_ADMIN_CHALLENGE_CONTRACT
    && IDENTIFIER_PATTERN.test(String(receipt.challenge_id || ""))
    && receipt.workspace_id === request.workspace_id
    && receipt.operator_user_id === request.operator_user_id
    && receipt.mode === request.mode
    && receipt.request_sha256 === requestSha256
    && Number.isFinite(issuedAt.getTime())
    && Number.isFinite(expiresAt.getTime())
    && expiresAt.getTime() > now.getTime()
    && ttlSeconds >= 60
    && ttlSeconds <= 90
    && receipt.single_use === true
    && receipt.session_revoked === true
  );
  if (!receiptValid) {
    throw challengeError(
      "entitlement_challenge_database_receipt_invalid",
      "PostgreSQL did not prove the complete challenge binding and Session revocation.",
      503,
    );
  }
  return {
    status: 201,
    body: {
      ok: true,
      contract: WORKSPACE_ENTITLEMENT_ADMIN_CHALLENGE_CONTRACT,
      control_plane: "typescript_postgres",
      challenge_id: String(receipt.challenge_id),
      challenge_token: token,
      workspace_id: request.workspace_id,
      operator_user_id: request.operator_user_id,
      mode: request.mode,
      request_sha256: requestSha256,
      issued_at: issuedAt.toISOString(),
      expires_at: expiresAt.toISOString(),
      ttl_seconds: ttlSeconds,
      single_use: true,
      human_session_consumed: true,
      human_session_omitted: true,
      long_lived_credentials_omitted: true,
      challenge_token_returned_once: true,
      dsn_omitted: true,
      raw_config_omitted: true,
      runtime_plan_apply_allowed: false,
      python_started: false,
      sqlite_used: false,
    },
  } as const;
}

export async function issueWorkspaceEntitlementAdminChallenge(
  headers: Headers,
  requestedWorkspaceId: unknown,
  value: unknown,
) {
  rejectMachineCredentials(headers);
  const origin = validateWriteOrigin(headers);
  const request = parseWorkspaceEntitlementAdminChallengeRequest(
    requestedWorkspaceId,
    value,
  );
  const result = await withPostgresTransaction(async (client) => {
    const identity = await authenticateHumanWriteMember(
      client,
      headers,
      request.workspace_id,
    );
    return issueWorkspaceEntitlementAdminChallengeWithClient(
      client,
      identity,
      request,
    );
  });
  return {
    ...result,
    setCookie: clearSessionCookie(origin),
  };
}
