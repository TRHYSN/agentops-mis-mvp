import { createHash, randomBytes, randomUUID } from "node:crypto";
import type { PoolClient } from "pg";

import { boundedJsonObject } from "./boundedJson";
import { withPostgresTransaction } from "./db";
import { AGENT_GATEWAY_VALID_SCOPES } from "./gatewayLifecycle";
import {
  authenticateHumanMember,
  authenticateHumanReviewer,
  type HumanSessionIdentity,
} from "./humanSession";
import { ControlPlaneHttpError } from "./http";
import { appendAudit, appendRuntimeEvent, stableHash } from "./ledger";
import {
  evaluateWorkspaceEntitlement,
  type WorkspaceEntitlementDecision,
} from "./workspaceEntitlements";

export const GATEWAY_ADMIN_MAX_BODY_BYTES = 16 * 1024;

const ADMIN_ROLES = new Set(["owner", "workspace-admin"]);
const RUNTIME_TYPES = new Set(["mock", "hermes", "openclaw", "codex"]);
const TOKEN_STATUSES = new Set(["active", "revoked", "expired"]);
const VALID_SCOPE_SET = new Set<string>(AGENT_GATEWAY_VALID_SCOPES);
const CREATE_FIELDS = new Set([
  "agent_id",
  "heartbeat_timeout_sec",
  "label",
  "name",
  "role",
  "runtime_type",
  "scopes",
  "ttl_days",
  "workspace_id",
]);
const REVOKE_FIELDS = new Set([
  "agent_id",
  "token_id",
  "token_ref",
  "workspace_id",
]);
const ROTATE_FIELDS = new Set([
  "agent_id",
  "heartbeat_timeout_sec",
  "label",
  "scopes",
  "token_id",
  "token_ref",
  "ttl_days",
  "workspace_id",
]);
const SESSION_REVOKE_FIELDS = new Set([
  "agent_id",
  "session_id",
  "session_ref",
  "workspace_id",
]);

type AdministrationResult = {
  status: number;
  body: Record<string, unknown>;
};

type AgentRow = {
  agent_id: string;
  name: string;
  role: string;
  runtime_type: string;
  status: string;
  owner_user_id: string | null;
};

type TokenRow = {
  token_id: string;
  workspace_id: string;
  agent_id: string;
  scopes_json: string;
  status: string;
  label: string | null;
  heartbeat_timeout_sec: number;
  created_at: string;
  expires_at: string | null;
  revoked_at: string | null;
  last_used_at: string | null;
  last_heartbeat_at: string | null;
};

type SessionRow = {
  session_id: string;
  parent_token_id: string | null;
  workspace_id: string;
  agent_id: string;
  scopes_json: string;
  status: string;
  created_at: string;
  expires_at: string;
  revoked_at: string | null;
  last_used_at: string | null;
};

type IssueInput = {
  agentId: string;
  name: string;
  role: string;
  runtimeType: string;
  scopes: string[];
  ttlDays: number;
  heartbeatTimeoutSec: number;
  label: string;
};

type TokenRequestIdentity = {
  idempotencyKeyHash: string;
  requestBindingHash: string;
  requestHash: string;
  tokenId: string;
};

function rejectUnknownFields(
  body: Record<string, unknown>,
  allowed: ReadonlySet<string>,
  operation: string,
) {
  const unknown = Object.keys(body).find((field) => !allowed.has(field));
  if (unknown) {
    throw new ControlPlaneHttpError(
      400,
      `${operation}_field_unsupported`,
      `${operation} received an unsupported request field.`,
    );
  }
}

function identifier(value: unknown, field: string, optional = false) {
  const normalized = String(value ?? "").trim();
  if (optional && !normalized) return null;
  if (!/^[A-Za-z0-9._:-]{1,128}$/.test(normalized)) {
    throw new ControlPlaneHttpError(
      400,
      `${field}_invalid`,
      `${field} must use 1-128 safe identifier characters.`,
    );
  }
  return normalized;
}

function sanitizedText(
  value: unknown,
  field: string,
  maximum: number,
  fallback: string,
) {
  if (value === undefined || value === null || value === "") return fallback;
  if (typeof value !== "string") {
    throw new ControlPlaneHttpError(
      400,
      `${field}_invalid`,
      `${field} must be text.`,
    );
  }
  const clean = value
    .replace(
      /-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----[\s\S]*?-----END (?:[A-Z0-9 ]+ )?PRIVATE KEY-----/g,
      "[PRIVATE_KEY_REDACTED]",
    )
    .replace(/\b(?:agtok|agtsess)_[A-Za-z0-9_-]+\b/g, "[AGENT_CREDENTIAL_REDACTED]")
    .replace(/\bBearer\s+\S+/gi, "Bearer [REDACTED]")
    .replace(
      /(token|secret|password|api[_-]?key|credential|dsn)\s*[:=]\s*['"]?[^'"\s,;]+/gi,
      "$1=[REDACTED]",
    )
    .replace(/[\u0000-\u001f\u007f]/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, maximum);
  return clean || fallback;
}

function boundedInteger(
  value: unknown,
  field: string,
  fallback: number,
  minimum: number,
  maximum: number,
) {
  if (value === undefined || value === null || value === "") return fallback;
  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed < minimum || parsed > maximum) {
    throw new ControlPlaneHttpError(
      400,
      `${field}_invalid`,
      `${field} is outside the allowed range.`,
    );
  }
  return parsed;
}

function requestedScopes(value: unknown) {
  if (!Array.isArray(value) || value.length === 0) {
    throw new ControlPlaneHttpError(
      400,
      "enrollment_scopes_required",
      "At least one Agent Gateway scope is required.",
    );
  }
  const scopes = value.map((item) => String(item ?? "").trim());
  if (
    scopes.some((scope) => !scope || !VALID_SCOPE_SET.has(scope))
    || new Set(scopes).size !== scopes.length
  ) {
    throw new ControlPlaneHttpError(
      400,
      "enrollment_scope_invalid",
      "Enrollment scopes must be unique recognized Agent Gateway scopes.",
    );
  }
  return scopes.sort();
}

function parseStoredScopes(value: string) {
  try {
    const parsed: unknown = JSON.parse(value);
    return Array.isArray(parsed)
      ? [...new Set(parsed.map((item) => String(item).trim())
        .filter((scope) => VALID_SCOPE_SET.has(scope)))].sort()
      : [];
  } catch {
    return [];
  }
}

function safeRef(kind: "token" | "session" | "request", value: string) {
  const digest = createHash("sha256")
    .update(`${kind}:${value}`, "utf8")
    .digest("hex")
    .slice(0, 16);
  return `${kind}_ref_${digest}`;
}

function tokenHash(value: string) {
  return createHash("sha256").update(value, "utf8").digest("hex");
}

function idempotencyKey(headers: Headers) {
  const supplied = String(headers.get("idempotency-key") || "").trim();
  if (!supplied) return `implicit:${randomUUID()}`;
  if (!/^[A-Za-z0-9._:-]{16,128}$/.test(supplied)) {
    throw new ControlPlaneHttpError(
      400,
      "idempotency_key_invalid",
      "Idempotency-Key must use 16-128 safe identifier characters.",
    );
  }
  return supplied;
}

function requireAdministrator(identity: HumanSessionIdentity) {
  if (!ADMIN_ROLES.has(identity.membershipRole.trim().toLowerCase())) {
    throw new ControlPlaneHttpError(
      403,
      "human_admin_role_forbidden",
      "Enrollment administration requires workspace-admin or owner authority.",
    );
  }
}

function queryWorkspace(request: Request) {
  const url = new URL(request.url);
  const allowed = new Set(["workspace_id", "agent_id", "status", "limit"]);
  for (const key of new Set(url.searchParams.keys())) {
    if (!allowed.has(key)) {
      throw new ControlPlaneHttpError(
        400,
        "gateway_administration_query_unsupported",
        "Gateway administration received an unsupported query parameter.",
      );
    }
  }
  if (url.searchParams.getAll("workspace_id").length > 1) {
    throw new ControlPlaneHttpError(
      400,
      "workspace_id_ambiguous",
      "One workspace_id is allowed.",
    );
  }
  return {
    url,
    workspaceId: url.searchParams.get("workspace_id"),
  };
}

function bodyWorkspace(body: Record<string, unknown>) {
  return body.workspace_id === undefined
    ? undefined
    : identifier(body.workspace_id, "workspace_id");
}

function publicToken(row: TokenRow) {
  const timeout = Math.max(Number(row.heartbeat_timeout_sec) || 300, 1);
  let heartbeatState = row.status;
  if (row.status === "active") {
    if (row.expires_at && Date.parse(row.expires_at) <= Date.now()) {
      heartbeatState = "expired";
    } else if (!row.last_heartbeat_at) {
      heartbeatState = "never_seen";
    } else {
      const lastHeartbeat = Date.parse(row.last_heartbeat_at);
      heartbeatState = (
        !Number.isFinite(lastHeartbeat)
        || Date.now() - lastHeartbeat > timeout * 1000
      ) ? "stale" : "fresh";
    }
  }
  return {
    token_ref: safeRef("token", row.token_id),
    token_id_omitted: true,
    workspace_id: row.workspace_id,
    agent_id: row.agent_id,
    scopes: parseStoredScopes(row.scopes_json),
    status: row.status,
    label: row.label || "",
    heartbeat_timeout_sec: timeout,
    created_at: row.created_at,
    expires_at: row.expires_at,
    revoked_at: row.revoked_at,
    last_used_at: row.last_used_at,
    last_heartbeat_at: row.last_heartbeat_at,
    heartbeat_state: heartbeatState,
    token_hash_omitted: true,
    token_omitted: true,
  };
}

function publicSession(row: SessionRow) {
  const effectiveState = row.status === "active"
    && Date.parse(row.expires_at) <= Date.now()
    ? "expired"
    : row.status;
  return {
    session_ref: safeRef("session", row.session_id),
    session_id_omitted: true,
    parent_token_ref: row.parent_token_id
      ? safeRef("token", row.parent_token_id)
      : null,
    parent_token_id_omitted: true,
    workspace_id: row.workspace_id,
    agent_id: row.agent_id,
    scopes: parseStoredScopes(row.scopes_json),
    status: row.status,
    session_state: effectiveState,
    created_at: row.created_at,
    expires_at: row.expires_at,
    revoked_at: row.revoked_at,
    last_used_at: row.last_used_at,
    session_hash_omitted: true,
    token_omitted: true,
  };
}

function issueInput(body: Record<string, unknown>): IssueInput {
  const runtimeType = String(body.runtime_type ?? "").trim().toLowerCase();
  if (!RUNTIME_TYPES.has(runtimeType)) {
    throw new ControlPlaneHttpError(
      400,
      "runtime_type_invalid",
      "Enrollment runtime_type is invalid.",
    );
  }
  const agentId = identifier(body.agent_id, "agent_id") as string;
  return {
    agentId,
    name: sanitizedText(body.name, "name", 120, agentId),
    role: sanitizedText(
      body.role,
      "role",
      120,
      "Remote AI Digital Employee",
    ),
    runtimeType,
    scopes: requestedScopes(body.scopes),
    ttlDays: boundedInteger(body.ttl_days, "ttl_days", 30, 1, 365),
    heartbeatTimeoutSec: boundedInteger(
      body.heartbeat_timeout_sec,
      "heartbeat_timeout_sec",
      300,
      30,
      86_400,
    ),
    label: sanitizedText(
      body.label,
      "label",
      120,
      `${agentId} token`,
    ),
  };
}

async function ensureEnrollmentAgent(
  client: PoolClient,
  identity: HumanSessionIdentity,
  input: IssueInput,
) {
  await client.query("SELECT pg_advisory_xact_lock(hashtextextended($1,0))", [
    `gateway-admin-agent:${input.agentId}`,
  ]);
  const foreignBinding = await client.query<{ workspace_id: string }>(
    `SELECT workspace_id FROM agent_gateway_tokens
    WHERE agent_id=$1 AND workspace_id<>$2
    ORDER BY created_at DESC LIMIT 1`,
    [input.agentId, identity.workspaceId],
  );
  if (foreignBinding.rows[0]) {
    throw new ControlPlaneHttpError(
      409,
      "agent_workspace_binding_conflict",
      "The agent id already has enrollment history in another workspace.",
    );
  }
  const result = await client.query<AgentRow>(
    `SELECT agent_id,name,role,runtime_type,status,owner_user_id
    FROM agents WHERE agent_id=$1 FOR UPDATE`,
    [input.agentId],
  );
  const existing = result.rows[0];
  if (existing) {
    if (existing.status === "disabled") {
      throw new ControlPlaneHttpError(
        409,
        "enrollment_agent_disabled",
        "A disabled agent cannot receive a new enrollment.",
      );
    }
    if (existing.runtime_type !== input.runtimeType) {
      throw new ControlPlaneHttpError(
        409,
        "enrollment_runtime_binding_conflict",
        "Enrollment runtime_type must match the existing agent.",
      );
    }
    await client.query(
      `UPDATE agents SET name=$1,role=$2,
        owner_user_id=COALESCE(owner_user_id,$3),updated_at=$4
      WHERE agent_id=$5`,
      [
        input.name,
        input.role,
        identity.userId,
        new Date().toISOString(),
        input.agentId,
      ],
    );
    return;
  }
  const now = new Date().toISOString();
  await client.query(
    `INSERT INTO agents(
      agent_id,name,role,description,runtime_type,model_provider,model_name,
      status,permission_level,allowed_tools,budget_limit_usd,owner_user_id,
      created_at,updated_at
    ) VALUES($1,$2,$3,NULL,$4,$4,NULL,'idle','standard','[]',0,$5,$6,$6)`,
    [
      input.agentId,
      input.name,
      input.role,
      input.runtimeType,
      identity.userId,
      now,
    ],
  );
}

function tokenSnapshot(row: TokenRow) {
  return {
    token_ref: safeRef("token", row.token_id),
    workspace_id: row.workspace_id,
    agent_id: row.agent_id,
    scopes: parseStoredScopes(row.scopes_json),
    status: row.status,
    label: row.label,
    heartbeat_timeout_sec: row.heartbeat_timeout_sec,
    created_at: row.created_at,
    expires_at: row.expires_at,
    revoked_at: row.revoked_at,
    token_hash_omitted: true,
    token_omitted: true,
  };
}

function issueRequestBinding(
  input: IssueInput,
  operation: "create" | "rotate",
  replacingEnrollmentId?: string,
) {
  return {
    operation,
    agent_id: input.agentId,
    name: input.name,
    role: input.role,
    runtime_type: input.runtimeType,
    scopes: input.scopes,
    ttl_days: input.ttlDays,
    heartbeat_timeout_sec: input.heartbeatTimeoutSec,
    label: input.label,
    replacing_token_ref: replacingEnrollmentId
      ? safeRef("token", replacingEnrollmentId)
      : null,
  };
}

function tokenRequestIdentity(
  identity: HumanSessionIdentity,
  requestKey: string,
  operation: "create" | "rotate",
  requestBinding: unknown,
): TokenRequestIdentity {
  const idempotencyKeyHash = stableHash({
    workspace_id: identity.workspaceId,
    user_id: identity.userId,
    request_key: requestKey,
    operation,
  });
  const requestBindingHash = stableHash(requestBinding);
  const requestHash = stableHash({
    idempotency_key_hash: idempotencyKeyHash,
    request_binding_hash: requestBindingHash,
  });
  return {
    idempotencyKeyHash,
    requestBindingHash,
    requestHash,
    tokenId: `agt_${createHash("sha256")
      .update(`gateway-admin-token:${idempotencyKeyHash}`, "utf8")
      .digest("hex")
      .slice(0, 32)}`,
  };
}

function auditMetadata(value: string) {
  try {
    const parsed: unknown = JSON.parse(value);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? parsed as Record<string, unknown>
      : {};
  } catch {
    return {};
  }
}

async function assertRequestBinding(
  client: PoolClient,
  identity: HumanSessionIdentity,
  requestIdentity: TokenRequestIdentity,
) {
  const result = await client.query<{ action: string; metadata_json: string }>(
    `SELECT action,metadata_json
    FROM audit_logs
    WHERE workspace_id=$1
      AND actor_type='user'
      AND actor_id=$2
      AND metadata_json::jsonb ->> 'idempotency_key_hash'=$3
    ORDER BY created_at DESC,audit_id DESC
    LIMIT 1`,
    [
      identity.workspaceId,
      identity.userId,
      requestIdentity.idempotencyKeyHash,
    ],
  );
  const row = result.rows[0];
  if (!row) return false;
  const metadata = auditMetadata(row.metadata_json);
  if (
    metadata.request_binding_hash !== requestIdentity.requestBindingHash
  ) {
    throw new ControlPlaneHttpError(
      409,
      "enrollment_idempotency_conflict",
      "Idempotency-Key is already bound to another enrollment request.",
    );
  }
  return {
    action: row.action,
    metadata,
  };
}

function auditedEntitlementDecision(metadata: Record<string, unknown>) {
  const value = metadata.entitlement_decision;
  if (
    !value
    || typeof value !== "object"
    || Array.isArray(value)
    || (value as Record<string, unknown>).allow !== false
  ) {
    throw new ControlPlaneHttpError(
      409,
      "enrollment_idempotency_conflict",
      "The prior enrollment denial cannot be safely replayed.",
    );
  }
  return value as WorkspaceEntitlementDecision;
}

function entitlementDenialResult(
  identity: HumanSessionIdentity,
  agentId: string,
  entitlementDecision: WorkspaceEntitlementDecision,
  replayed: boolean,
) {
  return {
    entitlementDenied: true as const,
    replayed,
    row: null,
    response: {
      ok: false,
      error: "workspace_entitlement_denied",
      message: "Workspace entitlement does not permit a new Agent Gateway enrollment.",
      created: false,
      replayed,
      agent_id: agentId,
      workspace_id: identity.workspaceId,
      entitlement_decision: entitlementDecision,
      credential_generated: false,
      token_omitted: true,
      raw_config_omitted: true,
    },
  };
}

async function resolveActiveTokenRef(
  client: PoolClient,
  workspaceId: string,
  tokenRef: string,
) {
  const rows = (await client.query<{ token_id: string }>(
    `SELECT token_id FROM agent_gateway_tokens
    WHERE workspace_id=$1 AND status='active'
    ORDER BY token_id`,
    [workspaceId],
  )).rows.filter((row) => safeRef("token", row.token_id) === tokenRef);
  if (rows.length > 1) {
    throw new ControlPlaneHttpError(
      409,
      "enrollment_ref_ambiguous",
      "Enrollment reference is ambiguous.",
    );
  }
  return rows[0]?.token_id || null;
}

async function resolveActiveSessionRef(
  client: PoolClient,
  workspaceId: string,
  sessionRef: string,
) {
  const rows = (await client.query<{ session_id: string }>(
    `SELECT session_id FROM agent_gateway_sessions
    WHERE workspace_id=$1 AND status='active'
    ORDER BY session_id`,
    [workspaceId],
  )).rows.filter((row) => safeRef("session", row.session_id) === sessionRef);
  if (rows.length > 1) {
    throw new ControlPlaneHttpError(
      409,
      "session_ref_ambiguous",
      "Session reference is ambiguous.",
    );
  }
  return rows[0]?.session_id || null;
}

async function issueToken(
  client: PoolClient,
  identity: HumanSessionIdentity,
  input: IssueInput,
  requestKey: string,
  operation: "create" | "rotate",
  options: Readonly<{
    replacingEnrollmentId?: string;
    entitlementDecision?: WorkspaceEntitlementDecision;
    requestBinding?: unknown;
  }> = {},
) {
  const requestIdentity = tokenRequestIdentity(
    identity,
    requestKey,
    operation,
    options.requestBinding ?? issueRequestBinding(
      input,
      operation,
      options.replacingEnrollmentId,
    ),
  );
  const {
    idempotencyKeyHash,
    requestBindingHash,
    requestHash,
    tokenId,
  } = requestIdentity;
  await client.query("SELECT pg_advisory_xact_lock(hashtextextended($1,0))", [
    `gateway-admin-token:${tokenId}`,
  ]);
  const existing = (await client.query<TokenRow>(
    `SELECT token_id,workspace_id,agent_id,scopes_json,status,label,
      heartbeat_timeout_sec,created_at,expires_at,revoked_at,last_used_at,
      last_heartbeat_at
    FROM agent_gateway_tokens WHERE token_id=$1 FOR UPDATE`,
    [tokenId],
  )).rows[0];
  const knownRequestBinding = await assertRequestBinding(
    client,
    identity,
    requestIdentity,
  );
  if (existing) {
    const sameBinding = existing.workspace_id === identity.workspaceId
      && existing.agent_id === input.agentId
      && existing.label === input.label
      && existing.heartbeat_timeout_sec === input.heartbeatTimeoutSec
      && JSON.stringify(parseStoredScopes(existing.scopes_json))
        === JSON.stringify(input.scopes);
    if (!knownRequestBinding || !sameBinding) {
      throw new ControlPlaneHttpError(
        409,
        "enrollment_idempotency_conflict",
        "Idempotency-Key is already bound to another enrollment request.",
      );
    }
    return {
      entitlementDenied: false as const,
      replayed: true as const,
      row: existing,
      response: {
        created: false,
        replayed: true,
        token_id: existing.token_id,
        token_ref: safeRef("token", existing.token_id),
        agent_id: existing.agent_id,
        workspace_id: existing.workspace_id,
        scopes: parseStoredScopes(existing.scopes_json),
        status: existing.status,
        expires_at: existing.expires_at,
        heartbeat_timeout_sec: existing.heartbeat_timeout_sec,
        token_omitted: true,
        note: "The credential was already issued and cannot be shown again.",
      },
    };
  }
  if (knownRequestBinding) {
    if (
      knownRequestBinding.action
      === "agent_gateway.enrollment_entitlement_denied"
    ) {
      return entitlementDenialResult(
        identity,
        input.agentId,
        auditedEntitlementDecision(knownRequestBinding.metadata),
        true,
      );
    }
    throw new ControlPlaneHttpError(
      409,
      "enrollment_idempotency_conflict",
      "The prior enrollment request cannot be safely replayed.",
    );
  }

  const entitlementDecision = options.entitlementDecision
    || await evaluateWorkspaceEntitlement(client, {
      workspaceId: identity.workspaceId,
      operation: "enrollment_issue",
      agentId: input.agentId,
      ...(options.replacingEnrollmentId
        ? { replacingEnrollmentId: options.replacingEnrollmentId }
        : {}),
    });
  if (!entitlementDecision.allow) {
    await appendAudit(client, {
      workspaceId: identity.workspaceId,
      actorType: "user",
      actorId: identity.userId,
      action: "agent_gateway.enrollment_entitlement_denied",
      entityType: "workspace_entitlements",
      entityId: identity.workspaceId,
      after: {
        decision: entitlementDecision.decision,
        reason_code: entitlementDecision.reason_code,
        operation: entitlementDecision.operation,
        usage: entitlementDecision.usage,
      },
      metadata: {
        entitlement_decision: entitlementDecision,
        enrollment_operation: operation,
        agent_id: input.agentId,
        replacement_token_ref: options.replacingEnrollmentId
          ? safeRef("token", options.replacingEnrollmentId)
          : null,
        session_ref: identity.sessionRef,
        membership_role: identity.membershipRole,
        credential_generated: false,
        token_omitted: true,
        raw_config_omitted: true,
        idempotency_key_hash: idempotencyKeyHash,
        request_binding_hash: requestBindingHash,
      },
      requestHash,
    });
    return entitlementDenialResult(
      identity,
      input.agentId,
      entitlementDecision,
      false,
    );
  }

  await ensureEnrollmentAgent(client, identity, input);
  const now = new Date();
  const createdAt = now.toISOString();
  const expiresAt = new Date(
    now.getTime() + input.ttlDays * 24 * 60 * 60 * 1000,
  ).toISOString();
  const token = `agtok_${randomBytes(32).toString("base64url")}`;
  const row: TokenRow = {
    token_id: tokenId,
    workspace_id: identity.workspaceId,
    agent_id: input.agentId,
    scopes_json: JSON.stringify(input.scopes),
    status: "active",
    label: input.label,
    heartbeat_timeout_sec: input.heartbeatTimeoutSec,
    created_at: createdAt,
    expires_at: expiresAt,
    revoked_at: null,
    last_used_at: null,
    last_heartbeat_at: null,
  };
  await client.query(
    `INSERT INTO agent_gateway_tokens(
      token_id,token_hash,workspace_id,agent_id,scopes_json,status,label,
      heartbeat_timeout_sec,created_at,expires_at,revoked_at,last_used_at,
      last_heartbeat_at
    ) VALUES($1,$2,$3,$4,$5,'active',$6,$7,$8,$9,NULL,NULL,NULL)`,
    [
      row.token_id,
      tokenHash(token),
      row.workspace_id,
      row.agent_id,
      row.scopes_json,
      row.label,
      row.heartbeat_timeout_sec,
      row.created_at,
      row.expires_at,
    ],
  );
  await appendAudit(client, {
    workspaceId: identity.workspaceId,
    actorType: "user",
    actorId: identity.userId,
    action: `agent_gateway.enrollment_${operation}`,
    entityType: "agent_gateway_tokens",
    entityId: safeRef("token", row.token_id),
    after: tokenSnapshot(row),
    metadata: {
      session_ref: identity.sessionRef,
      membership_role: identity.membershipRole,
      request_ref: safeRef("request", idempotencyKeyHash),
      idempotency_key_hash: idempotencyKeyHash,
      request_binding_hash: requestBindingHash,
      one_time_credential_response: true,
      token_hash_omitted: true,
      token_omitted: true,
    },
    requestHash,
  });
  await appendRuntimeEvent(client, {
    workspaceId: identity.workspaceId,
    eventType: `agent.enrollment.${operation}`,
    status: "completed",
    agentId: input.agentId,
    outputSummary: `${operation === "rotate" ? "Rotated" : "Issued"} scoped enrollment ${safeRef("token", row.token_id)}.`,
    rawPayloadHash: requestHash,
  });
  return {
    entitlementDenied: false as const,
    replayed: false as const,
    row,
    response: {
      created: true,
      replayed: false,
      token_id: row.token_id,
      token_ref: safeRef("token", row.token_id),
      agent_id: row.agent_id,
      workspace_id: row.workspace_id,
      scopes: input.scopes,
      expires_at: expiresAt,
      heartbeat_timeout_sec: input.heartbeatTimeoutSec,
      token,
      note: "The token is shown once; the control plane stores only its hash.",
      token_omitted: false,
    },
  };
}

async function activeChildSessions(
  client: PoolClient,
  tokenIds: string[],
) {
  if (!tokenIds.length) return [] as SessionRow[];
  return (await client.query<SessionRow>(
    `SELECT session_id,parent_token_id,workspace_id,agent_id,scopes_json,
      status,created_at,expires_at,revoked_at,last_used_at
    FROM agent_gateway_sessions
    WHERE parent_token_id=ANY($1::text[]) AND status='active'
    ORDER BY created_at,session_id FOR UPDATE`,
    [tokenIds],
  )).rows;
}

async function auditSessionRevocations(
  client: PoolClient,
  identity: HumanSessionIdentity,
  rows: SessionRow[],
  action: string,
  now: string,
) {
  for (const row of rows) {
    await appendAudit(client, {
      workspaceId: identity.workspaceId,
      actorType: "user",
      actorId: identity.userId,
      action,
      entityType: "agent_gateway_sessions",
      entityId: safeRef("session", row.session_id),
      before: publicSession(row),
      after: {
        ...publicSession(row),
        status: "revoked",
        session_state: "revoked",
        revoked_at: now,
      },
      metadata: {
        session_ref: identity.sessionRef,
        parent_token_ref: row.parent_token_id
          ? safeRef("token", row.parent_token_id)
          : null,
        session_id_omitted: true,
        token_omitted: true,
      },
    });
  }
}

export async function listGatewayEnrollments(
  request: Request,
): Promise<AdministrationResult> {
  const { url, workspaceId } = queryWorkspace(request);
  return withPostgresTransaction(async (client) => {
    const identity = await authenticateHumanMember(
      client,
      request.headers,
      workspaceId,
    );
    requireAdministrator(identity);
    const agentId = identifier(
      url.searchParams.get("agent_id"),
      "agent_id",
      true,
    );
    const status = String(url.searchParams.get("status") || "").trim();
    if (status && !TOKEN_STATUSES.has(status)) {
      throw new ControlPlaneHttpError(
        400,
        "enrollment_status_invalid",
        "Enrollment status filter is invalid.",
      );
    }
    const limit = boundedInteger(
      url.searchParams.get("limit"),
      "limit",
      100,
      1,
      200,
    );
    const rows = await client.query<TokenRow>(
      `SELECT token_id,workspace_id,agent_id,scopes_json,status,label,
        heartbeat_timeout_sec,created_at,expires_at,revoked_at,last_used_at,
        last_heartbeat_at
      FROM agent_gateway_tokens
      WHERE workspace_id=$1
        AND ($2::text IS NULL OR agent_id=$2)
        AND ($3::text IS NULL OR status=$3)
      ORDER BY created_at DESC,token_id
      LIMIT $4`,
      [identity.workspaceId, agentId, status || null, limit],
    );
    return {
      status: 200,
      body: {
        enrollments: rows.rows.map(publicToken),
        valid_scopes: AGENT_GATEWAY_VALID_SCOPES,
        workspace_id: identity.workspaceId,
        control_plane: "typescript_postgres",
        python_proxy_performed: false,
        token_omitted: true,
      },
    };
  });
}

export async function listGatewaySessions(
  request: Request,
): Promise<AdministrationResult> {
  const { url, workspaceId } = queryWorkspace(request);
  return withPostgresTransaction(async (client) => {
    const identity = await authenticateHumanMember(
      client,
      request.headers,
      workspaceId,
    );
    requireAdministrator(identity);
    const agentId = identifier(
      url.searchParams.get("agent_id"),
      "agent_id",
      true,
    );
    const status = String(url.searchParams.get("status") || "").trim();
    if (status && !TOKEN_STATUSES.has(status)) {
      throw new ControlPlaneHttpError(
        400,
        "session_status_invalid",
        "Session status filter is invalid.",
      );
    }
    const limit = boundedInteger(
      url.searchParams.get("limit"),
      "limit",
      100,
      1,
      200,
    );
    const rows = await client.query<SessionRow>(
      `SELECT session_id,parent_token_id,workspace_id,agent_id,scopes_json,
        status,created_at,expires_at,revoked_at,last_used_at
      FROM agent_gateway_sessions
      WHERE workspace_id=$1
        AND ($2::text IS NULL OR agent_id=$2)
        AND ($3::text IS NULL OR status=$3)
      ORDER BY created_at DESC,session_id
      LIMIT $4`,
      [identity.workspaceId, agentId, status || null, limit],
    );
    return {
      status: 200,
      body: {
        sessions: rows.rows.map(publicSession),
        valid_scopes: AGENT_GATEWAY_VALID_SCOPES,
        workspace_id: identity.workspaceId,
        control_plane: "typescript_postgres",
        python_proxy_performed: false,
        token_omitted: true,
      },
    };
  });
}

export async function createGatewayEnrollment(
  request: Request,
): Promise<AdministrationResult> {
  const body = await boundedJsonObject(request, {
    maxBytes: GATEWAY_ADMIN_MAX_BODY_BYTES,
    label: "Gateway enrollment creation",
  });
  rejectUnknownFields(body, CREATE_FIELDS, "gateway_enrollment_create");
  const input = issueInput(body);
  const requestKey = idempotencyKey(request.headers);
  return withPostgresTransaction(async (client) => {
    const identity = await authenticateHumanReviewer(
      client,
      request.headers,
      bodyWorkspace(body),
    );
    requireAdministrator(identity);
    const issued = await issueToken(
      client,
      identity,
      input,
      requestKey,
      "create",
    );
    if (issued.entitlementDenied) {
      return {
        status: 403,
        body: issued.response,
      };
    }
    return {
      status: issued.replayed ? 200 : 201,
      body: issued.response,
    };
  });
}

export async function revokeGatewayEnrollment(
  request: Request,
): Promise<AdministrationResult> {
  const body = await boundedJsonObject(request, {
    maxBytes: GATEWAY_ADMIN_MAX_BODY_BYTES,
    label: "Gateway enrollment revocation",
  });
  rejectUnknownFields(body, REVOKE_FIELDS, "gateway_enrollment_revoke");
  const tokenId = identifier(body.token_id, "token_id", true);
  const tokenRef = identifier(body.token_ref, "token_ref", true);
  const agentId = identifier(body.agent_id, "agent_id", true);
  if ([tokenId, tokenRef, agentId].filter(Boolean).length !== 1) {
    throw new ControlPlaneHttpError(
      400,
      "enrollment_revoke_selector_required",
      "Provide exactly one token_ref, token_id, or agent_id.",
    );
  }
  return withPostgresTransaction(async (client) => {
    const identity = await authenticateHumanReviewer(
      client,
      request.headers,
      bodyWorkspace(body),
    );
    requireAdministrator(identity);
    const resolvedTokenId = tokenId || (
      tokenRef
        ? await resolveActiveTokenRef(client, identity.workspaceId, tokenRef)
        : null
    );
    const exactTokenId = tokenRef && !resolvedTokenId
      ? "__agentops_no_matching_token__"
      : resolvedTokenId;
    await client.query("SELECT pg_advisory_xact_lock(hashtextextended($1,0))", [
      `gateway-admin-revoke:${identity.workspaceId}:${tokenRef || tokenId || agentId}`,
    ]);
    const tokens = (await client.query<TokenRow>(
      `SELECT token_id,workspace_id,agent_id,scopes_json,status,label,
        heartbeat_timeout_sec,created_at,expires_at,revoked_at,last_used_at,
        last_heartbeat_at
      FROM agent_gateway_tokens
      WHERE workspace_id=$1 AND status='active'
        AND ($2::text IS NULL OR token_id=$2)
        AND ($3::text IS NULL OR agent_id=$3)
      ORDER BY created_at,token_id FOR UPDATE`,
      [identity.workspaceId, exactTokenId, agentId],
    )).rows;
    const sessions = await activeChildSessions(
      client,
      tokens.map((row) => row.token_id),
    );
    const now = new Date().toISOString();
    if (tokens.length) {
      await client.query(
        `UPDATE agent_gateway_tokens SET status='revoked',revoked_at=$1
        WHERE token_id=ANY($2::text[]) AND workspace_id=$3 AND status='active'`,
        [now, tokens.map((row) => row.token_id), identity.workspaceId],
      );
    }
    if (sessions.length) {
      await client.query(
        `UPDATE agent_gateway_sessions SET status='revoked',revoked_at=$1
        WHERE session_id=ANY($2::text[]) AND workspace_id=$3
          AND status='active'`,
        [now, sessions.map((row) => row.session_id), identity.workspaceId],
      );
    }
    await auditSessionRevocations(
      client,
      identity,
      sessions,
      "agent_gateway.session_revoke_cascade",
      now,
    );
    for (const row of tokens) {
      await appendAudit(client, {
        workspaceId: identity.workspaceId,
        actorType: "user",
        actorId: identity.userId,
        action: "agent_gateway.enrollment_revoke",
        entityType: "agent_gateway_tokens",
        entityId: safeRef("token", row.token_id),
        before: tokenSnapshot(row),
        after: {
          ...tokenSnapshot(row),
          status: "revoked",
          revoked_at: now,
        },
        metadata: {
          session_ref: identity.sessionRef,
          cascaded_sessions: sessions.filter(
            (session) => session.parent_token_id === row.token_id,
          ).length,
          token_id_omitted: true,
          token_omitted: true,
        },
      });
      await appendRuntimeEvent(client, {
        workspaceId: identity.workspaceId,
        eventType: "agent.enrollment.revoke",
        status: "completed",
        agentId: row.agent_id,
        outputSummary: `Revoked scoped enrollment ${safeRef("token", row.token_id)}.`,
      });
    }
    const tokenRefs = tokens.map((row) => safeRef("token", row.token_id));
    const sessionRefs = sessions.map((row) =>
      safeRef("session", row.session_id));
    return {
      status: 200,
      body: {
        revoked: tokens.length,
        changed: tokens.length + sessions.length,
        token_refs: tokenRefs,
        tokens: tokenRefs,
        token_id_omitted: true,
        sessions_revoked: sessions.length,
        session_refs: sessionRefs,
        sessions: sessionRefs,
        session_id_omitted: true,
        workspace_id: identity.workspaceId,
        entitlement_expansion_performed: false,
        token_omitted: true,
      },
    };
  });
}

export async function revokeGatewaySession(
  request: Request,
): Promise<AdministrationResult> {
  const body = await boundedJsonObject(request, {
    maxBytes: GATEWAY_ADMIN_MAX_BODY_BYTES,
    label: "Gateway session revocation",
  });
  rejectUnknownFields(body, SESSION_REVOKE_FIELDS, "gateway_session_revoke");
  const sessionId = identifier(body.session_id, "session_id", true);
  const sessionRef = identifier(body.session_ref, "session_ref", true);
  const agentId = identifier(body.agent_id, "agent_id", true);
  if ([sessionId, sessionRef, agentId].filter(Boolean).length !== 1) {
    throw new ControlPlaneHttpError(
      400,
      "session_revoke_selector_required",
      "Provide exactly one session_ref, session_id, or agent_id.",
    );
  }
  return withPostgresTransaction(async (client) => {
    const identity = await authenticateHumanReviewer(
      client,
      request.headers,
      bodyWorkspace(body),
    );
    requireAdministrator(identity);
    const resolvedSessionId = sessionId || (
      sessionRef
        ? await resolveActiveSessionRef(client, identity.workspaceId, sessionRef)
        : null
    );
    const exactSessionId = sessionRef && !resolvedSessionId
      ? "__agentops_no_matching_session__"
      : resolvedSessionId;
    await client.query("SELECT pg_advisory_xact_lock(hashtextextended($1,0))", [
      `gateway-admin-session-revoke:${identity.workspaceId}:${sessionRef || sessionId || agentId}`,
    ]);
    const rows = (await client.query<SessionRow>(
      `SELECT session_id,parent_token_id,workspace_id,agent_id,scopes_json,
        status,created_at,expires_at,revoked_at,last_used_at
      FROM agent_gateway_sessions
      WHERE workspace_id=$1 AND status='active'
        AND ($2::text IS NULL OR session_id=$2)
        AND ($3::text IS NULL OR agent_id=$3)
      ORDER BY created_at,session_id FOR UPDATE`,
      [identity.workspaceId, exactSessionId, agentId],
    )).rows;
    const now = new Date().toISOString();
    if (rows.length) {
      await client.query(
        `UPDATE agent_gateway_sessions SET status='revoked',revoked_at=$1
        WHERE session_id=ANY($2::text[]) AND workspace_id=$3
          AND status='active'`,
        [now, rows.map((row) => row.session_id), identity.workspaceId],
      );
    }
    await auditSessionRevocations(
      client,
      identity,
      rows,
      "agent_gateway.session_revoke",
      now,
    );
    for (const row of rows) {
      await appendRuntimeEvent(client, {
        workspaceId: identity.workspaceId,
        eventType: "agent.session.revoke",
        status: "completed",
        agentId: row.agent_id,
        outputSummary: `Revoked short-lived session ${safeRef("session", row.session_id)}.`,
      });
    }
    const refs = rows.map((row) => safeRef("session", row.session_id));
    return {
      status: 200,
      body: {
        revoked: rows.length,
        session_refs: refs,
        sessions: refs,
        session_id_omitted: true,
        workspace_id: identity.workspaceId,
        entitlement_expansion_performed: false,
        token_omitted: true,
      },
    };
  });
}

export async function rotateGatewayEnrollment(
  request: Request,
): Promise<AdministrationResult> {
  const body = await boundedJsonObject(request, {
    maxBytes: GATEWAY_ADMIN_MAX_BODY_BYTES,
    label: "Gateway enrollment rotation",
  });
  rejectUnknownFields(body, ROTATE_FIELDS, "gateway_enrollment_rotate");
  const tokenId = identifier(body.token_id, "token_id", true);
  const tokenRef = identifier(body.token_ref, "token_ref", true);
  const agentId = identifier(body.agent_id, "agent_id", true);
  if ([tokenId, tokenRef, agentId].filter(Boolean).length !== 1) {
    throw new ControlPlaneHttpError(
      400,
      "enrollment_rotate_selector_required",
      "Provide exactly one token_ref, token_id, or agent_id.",
    );
  }
  const scopeOverride = body.scopes === undefined
    ? null
    : requestedScopes(body.scopes);
  const ttlDaysOverride = body.ttl_days === undefined
    ? null
    : boundedInteger(body.ttl_days, "ttl_days", 30, 1, 365);
  const heartbeatOverride = body.heartbeat_timeout_sec === undefined
    ? null
    : boundedInteger(
      body.heartbeat_timeout_sec,
      "heartbeat_timeout_sec",
      300,
      30,
      86_400,
    );
  const labelOverride = body.label === undefined
    ? null
    : sanitizedText(body.label, "label", 120, "");
  const requestKey = idempotencyKey(request.headers);
  return withPostgresTransaction(async (client) => {
    const identity = await authenticateHumanReviewer(
      client,
      request.headers,
      bodyWorkspace(body),
    );
    requireAdministrator(identity);
    const resolvedTokenId = tokenId || (
      tokenRef
        ? await resolveActiveTokenRef(client, identity.workspaceId, tokenRef)
        : null
    );
    const exactTokenId = tokenRef && !resolvedTokenId
      ? "__agentops_no_matching_token__"
      : resolvedTokenId;
    const requestBinding = {
      operation: "rotate",
      selector: tokenRef
        ? { token_ref: tokenRef }
        : tokenId
          ? { token_ref: safeRef("token", tokenId) }
          : { agent_id: agentId },
      scopes: scopeOverride,
      ttl_days: ttlDaysOverride,
      heartbeat_timeout_sec: heartbeatOverride,
      label: labelOverride,
    };
    const replacementIdentity = tokenRequestIdentity(
      identity,
      requestKey,
      "rotate",
      requestBinding,
    );
    await client.query("SELECT pg_advisory_xact_lock(hashtextextended($1,0))", [
      `gateway-admin-rotate:${replacementIdentity.tokenId}`,
    ]);
    const knownRequestBinding = await assertRequestBinding(
      client,
      identity,
      replacementIdentity,
    );
    const replay = (await client.query<TokenRow>(
      `SELECT token_id,workspace_id,agent_id,scopes_json,status,label,
        heartbeat_timeout_sec,created_at,expires_at,revoked_at,last_used_at,
        last_heartbeat_at
      FROM agent_gateway_tokens
      WHERE token_id=$1 AND workspace_id=$2
      FOR UPDATE`,
      [replacementIdentity.tokenId, identity.workspaceId],
    )).rows[0];
    if (replay) {
      if (!knownRequestBinding) {
        throw new ControlPlaneHttpError(
          409,
          "enrollment_idempotency_conflict",
          "Idempotency-Key replay binding is unavailable.",
        );
      }
      return {
        status: 200,
        body: {
          created: false,
          replayed: true,
          rotated: true,
          revoked: 0,
          token_id: replay.token_id,
          token_ref: safeRef("token", replay.token_id),
          agent_id: replay.agent_id,
          workspace_id: replay.workspace_id,
          scopes: parseStoredScopes(replay.scopes_json),
          status: replay.status,
          expires_at: replay.expires_at,
          heartbeat_timeout_sec: replay.heartbeat_timeout_sec,
          rotated_from_token_ref: tokenRef
            || (tokenId ? safeRef("token", tokenId) : null),
          rotated_from_token_id_omitted: true,
          token_omitted: true,
          note: "The rotated credential was already issued and cannot be shown again.",
        },
      };
    }
    if (knownRequestBinding) {
      if (
        knownRequestBinding.action
        === "agent_gateway.enrollment_entitlement_denied"
      ) {
        const denial = entitlementDenialResult(
          identity,
          String(knownRequestBinding.metadata.agent_id || ""),
          auditedEntitlementDecision(knownRequestBinding.metadata),
          true,
        );
        return {
          status: 403,
          body: {
            ...denial.response,
            rotated: false,
            revoked: 0,
            rotated_from_token_ref: tokenRef
              || (tokenId ? safeRef("token", tokenId) : null),
            rotated_from_token_id_omitted: true,
          },
        };
      }
      throw new ControlPlaneHttpError(
        409,
        "enrollment_idempotency_conflict",
        "The prior rotation request cannot be safely replayed.",
      );
    }
    const old = (await client.query<TokenRow>(
      `SELECT token_id,workspace_id,agent_id,scopes_json,status,label,
        heartbeat_timeout_sec,created_at,expires_at,revoked_at,last_used_at,
        last_heartbeat_at
      FROM agent_gateway_tokens
      WHERE workspace_id=$1 AND status='active'
        AND ($2::text IS NULL OR token_id=$2)
        AND ($3::text IS NULL OR agent_id=$3)
      ORDER BY created_at DESC,token_id DESC
      LIMIT 1
      FOR UPDATE`,
      [identity.workspaceId, exactTokenId, agentId],
    )).rows[0];
    if (!old) {
      throw new ControlPlaneHttpError(
        404,
        "enrollment_not_found",
        "No active enrollment matched the rotation request.",
      );
    }
    const entitlementDecision = await evaluateWorkspaceEntitlement(client, {
      workspaceId: identity.workspaceId,
      operation: "enrollment_issue",
      agentId: old.agent_id,
      replacingEnrollmentId: old.token_id,
    });
    const agent = (await client.query<AgentRow>(
      `SELECT agent_id,name,role,runtime_type,status,owner_user_id
      FROM agents WHERE agent_id=$1 FOR UPDATE`,
      [old.agent_id],
    )).rows[0];
    if (!agent || agent.status === "disabled") {
      throw new ControlPlaneHttpError(
        409,
        "enrollment_agent_unavailable",
        "The enrolled agent is unavailable for rotation.",
      );
    }
    const input: IssueInput = {
      agentId: old.agent_id,
      name: agent.name,
      role: agent.role,
      runtimeType: agent.runtime_type,
      scopes: scopeOverride === null
        ? parseStoredScopes(old.scopes_json)
        : scopeOverride,
      ttlDays: ttlDaysOverride ?? 30,
      heartbeatTimeoutSec: heartbeatOverride ?? old.heartbeat_timeout_sec,
      label: labelOverride || `${old.agent_id} rotated token`,
    };
    const issued = await issueToken(
      client,
      identity,
      input,
      requestKey,
      "rotate",
      {
        replacingEnrollmentId: old.token_id,
        entitlementDecision,
        requestBinding,
      },
    );
    if (issued.entitlementDenied) {
      return {
        status: 403,
        body: {
          ...issued.response,
          rotated: false,
          revoked: 0,
          rotated_from_token_ref: safeRef("token", old.token_id),
          rotated_from_token_id_omitted: true,
        },
      };
    }
    if (issued.replayed) {
      return {
        status: 200,
        body: {
          ...issued.response,
          rotated: true,
          revoked: 0,
          rotated_from_token_ref: safeRef("token", old.token_id),
          rotated_from_token_id_omitted: true,
        },
      };
    }
    const sessions = await activeChildSessions(client, [old.token_id]);
    const now = new Date().toISOString();
    await client.query(
      `UPDATE agent_gateway_tokens SET status='revoked',revoked_at=$1
      WHERE token_id=$2 AND workspace_id=$3 AND status='active'`,
      [now, old.token_id, identity.workspaceId],
    );
    if (sessions.length) {
      await client.query(
        `UPDATE agent_gateway_sessions SET status='revoked',revoked_at=$1
        WHERE session_id=ANY($2::text[]) AND workspace_id=$3
          AND status='active'`,
        [now, sessions.map((row) => row.session_id), identity.workspaceId],
      );
    }
    await auditSessionRevocations(
      client,
      identity,
      sessions,
      "agent_gateway.session_revoke_rotation",
      now,
    );
    await appendAudit(client, {
      workspaceId: identity.workspaceId,
      actorType: "user",
      actorId: identity.userId,
      action: "agent_gateway.enrollment_rotate_revoke",
      entityType: "agent_gateway_tokens",
      entityId: safeRef("token", old.token_id),
      before: tokenSnapshot(old),
      after: {
        ...tokenSnapshot(old),
        status: "revoked",
        revoked_at: now,
      },
      metadata: {
        session_ref: identity.sessionRef,
        replacement_token_ref: safeRef("token", issued.row.token_id),
        cascaded_sessions: sessions.length,
        token_id_omitted: true,
        token_omitted: true,
      },
    });
    return {
      status: 201,
      body: {
        ...issued.response,
        rotated: true,
        revoked: 1,
        rotated_from_token_id: old.token_id,
        rotated_from_token_ref: safeRef("token", old.token_id),
        sessions_revoked: sessions.length,
      },
    };
  });
}
