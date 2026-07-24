import { createHash, randomBytes, randomUUID } from "node:crypto";
import type { PoolClient } from "pg";

import {
  authenticateAgentGateway,
  enforceWorkspaceBinding,
  type AgentGatewayIdentity,
} from "./auth";
import { boundedJsonObject } from "./boundedJson";
import { controlPlaneMode } from "./config";
import { withPostgresTransaction } from "./db";
import { ControlPlaneHttpError } from "./http";
import { appendAudit, stableHash } from "./ledger";
import { evaluateWorkspaceEntitlement } from "./workspaceEntitlements";

export const GATEWAY_LIFECYCLE_MAX_BODY_BYTES = 16 * 1024;

export const AGENT_GATEWAY_VALID_SCOPES = [
  "agent_plans:read",
  "agent_plans:write",
  "agents:heartbeat",
  "agents:write",
  "approvals:request",
  "artifacts:write",
  "audit:write",
  "evaluations:submit",
  "knowledge:read",
  "knowledge:write",
  "memories:propose",
  "plan_evidence:read",
  "plan_evidence:write",
  "runs:write",
  "runtime_events:write",
  "tasks:claim",
  "tasks:create",
  "tasks:read",
  "toolcalls:write",
] as const;
const VALID_SCOPES = AGENT_GATEWAY_VALID_SCOPES;
const VALID_RUNTIME_TYPES = new Set(["mock", "hermes", "openclaw", "codex"]);
const HEARTBEAT_STATUSES = new Set(["idle", "running", "paused", "error"]);
const REGISTER_FIELDS = new Set([
  "agent_id",
  "allowed_tools",
  "budget_limit_usd",
  "description",
  "model_name",
  "model_provider",
  "name",
  "permission_level",
  "request_id",
  "role",
  "runtime_type",
  "workspace_id",
]);
const HEARTBEAT_FIELDS = new Set([
  "agent_id",
  "request_id",
  "runtime_type",
  "status",
  "summary",
  "workspace_id",
]);
const SESSION_FIELDS = new Set([
  "agent_id",
  "request_id",
  "runtime_type",
  "scopes",
  "ttl_sec",
  "ttl_seconds",
  "workspace_id",
]);

type LifecycleResult = {
  status: number;
  body: Record<string, unknown>;
};

type AgentRow = {
  agent_id: string;
  name: string;
  role: string;
  description: string | null;
  runtime_type: string;
  model_provider: string | null;
  model_name: string | null;
  status: string;
  permission_level: string;
  allowed_tools: string;
  budget_limit_usd: number;
  created_at: string;
  updated_at: string;
};

type TokenStatusRow = {
  token_id: string;
  status: string;
  heartbeat_timeout_sec: number;
  expires_at: string | null;
  last_used_at: string | null;
  last_heartbeat_at: string | null;
};

type SessionReplayRow = {
  session_id: string;
  workspace_id: string;
  agent_id: string;
  scopes_json: string;
  status: string;
  created_at: string;
  expires_at: string;
};

function requirePostgresOwner() {
  if (controlPlaneMode() !== "postgres") {
    throw new ControlPlaneHttpError(
      503,
      "gateway_lifecycle_postgres_required",
      "Commercial Gateway lifecycle routes require the TypeScript PostgreSQL control plane.",
    );
  }
}

function rejectUnknownFields(
  body: Record<string, unknown>,
  allowed: Set<string>,
  owner: string,
) {
  const unknown = Object.keys(body).find((field) => !allowed.has(field));
  if (unknown) {
    throw new ControlPlaneHttpError(
      400,
      `${owner}_field_unsupported`,
      `${owner} received an unsupported request field.`,
    );
  }
}

function identifier(value: unknown, field: string) {
  const normalized = String(value ?? "").trim();
  if (!/^[A-Za-z0-9._:-]{1,128}$/.test(normalized)) {
    throw new ControlPlaneHttpError(
      400,
      `${field}_invalid`,
      `${field} must use 1-128 safe identifier characters.`,
    );
  }
  return normalized;
}

function optionalIdentifier(value: unknown, field: string) {
  return value === undefined || value === null || value === ""
    ? null
    : identifier(value, field);
}

function sanitizedText(
  value: unknown,
  field: string,
  maxLength: number,
  fallback: string,
) {
  if (value === undefined || value === null || value === "") return fallback;
  if (typeof value !== "string") {
    throw new ControlPlaneHttpError(400, `${field}_invalid`, `${field} must be text.`);
  }
  const sanitized = value
    .replace(
      /-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----[\s\S]*?-----END (?:[A-Z0-9 ]+ )?PRIVATE KEY-----/g,
      "[PRIVATE_KEY_REDACTED]",
    )
    .replace(/\b(?:agtok|agtsess)_[A-Za-z0-9_-]+\b/g, "[AGENT_CREDENTIAL_REDACTED]")
    .replace(/\bBearer\s+\S+/gi, "Bearer [REDACTED]")
    .replace(
      /(token|secret|password|api[_-]?key)\s*[:=]\s*['"]?[^'"\s,;]+/gi,
      "$1=[REDACTED]",
    )
    .replace(/[\u0000-\u001f\u007f]/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, maxLength);
  return sanitized || fallback;
}

function normalizedStringArray(value: unknown, field: string) {
  if (!Array.isArray(value)) {
    throw new ControlPlaneHttpError(400, `${field}_invalid`, `${field} must be an array.`);
  }
  const normalized = value.map((item) => {
    if (typeof item !== "string") {
      throw new ControlPlaneHttpError(
        400,
        `${field}_invalid`,
        `${field} values must be text.`,
      );
    }
    const clean = item.trim();
    if (!/^[A-Za-z0-9._:-]{1,128}$/.test(clean)) {
      throw new ControlPlaneHttpError(
        400,
        `${field}_invalid`,
        `${field} contains an invalid value.`,
      );
    }
    return clean;
  });
  return [...new Set(normalized)].sort();
}

function parseStoredArray(value: string) {
  try {
    const parsed: unknown = JSON.parse(value);
    return Array.isArray(parsed)
      ? [...new Set(parsed.map((item) => String(item).trim()).filter(Boolean))].sort()
      : [];
  } catch {
    return [];
  }
}

function parseStoredScopes(value: string) {
  return parseStoredArray(value).filter((scope) =>
    (VALID_SCOPES as readonly string[]).includes(scope));
}

function safeRef(kind: "token" | "session" | "request", value: string) {
  const digest = createHash("sha256").update(value, "utf8").digest("hex").slice(0, 16);
  return `${kind}_ref_${digest}`;
}

function enforceAgentBinding(
  identity: AgentGatewayIdentity,
  request: Request,
  bodyAgentId?: unknown,
) {
  for (const requested of [
    request.headers.get("x-agentops-agent-id"),
    bodyAgentId === undefined || bodyAgentId === null ? null : String(bodyAgentId).trim(),
  ]) {
    if (requested && requested !== identity.agentId) {
      throw new ControlPlaneHttpError(
        403,
        "forbidden",
        "Agent credential cannot act as another agent.",
      );
    }
  }
}

function enforceBindings(
  identity: AgentGatewayIdentity,
  request: Request,
  body: Record<string, unknown>,
) {
  const bodyWorkspace = optionalIdentifier(body.workspace_id, "workspace_id");
  if (body.agent_id !== undefined) identifier(body.agent_id, "agent_id");
  enforceWorkspaceBinding(identity, {
    header: request.headers.get("x-agentops-workspace-id"),
    body: bodyWorkspace,
  });
  enforceAgentBinding(identity, request, body.agent_id);
}

async function lockedAgent(client: PoolClient, agentId: string) {
  const result = await client.query<AgentRow>(
    `SELECT agent_id,name,role,description,runtime_type,model_provider,model_name,
      status,permission_level,allowed_tools,budget_limit_usd,created_at,updated_at
    FROM agents WHERE agent_id=$1 FOR UPDATE`,
    [agentId],
  );
  const agent = result.rows[0];
  if (!agent) {
    throw new ControlPlaneHttpError(
      409,
      "gateway_agent_missing",
      "The credential-bound agent must be enrolled before using Gateway lifecycle routes.",
    );
  }
  return agent;
}

function publicAgent(agent: AgentRow) {
  return {
    agent_id: agent.agent_id,
    name: agent.name,
    role: agent.role,
    description: agent.description,
    runtime_type: agent.runtime_type,
    model_provider: agent.model_provider,
    model_name: agent.model_name,
    status: agent.status,
    permission_level: agent.permission_level,
    allowed_tools: parseStoredArray(agent.allowed_tools),
    budget_limit_usd: Number(agent.budget_limit_usd),
    created_at: agent.created_at,
    updated_at: agent.updated_at,
  };
}

function requireEnabledAgent(agent: AgentRow) {
  if (agent.status === "disabled") {
    throw new ControlPlaneHttpError(
      403,
      "gateway_agent_disabled",
      "The credential-bound agent is disabled.",
    );
  }
}

function ttlSeconds(body: Record<string, unknown>) {
  const supplied = body.ttl_sec ?? body.ttl_seconds ?? 900;
  const ttl = Number(supplied);
  if (!Number.isInteger(ttl) || ttl < 1 || ttl > 3600) {
    throw new ControlPlaneHttpError(
      400,
      "session_ttl_invalid",
      "Session TTL must be an integer from 1 to 3600 seconds.",
    );
  }
  return ttl;
}

function requestedSessionScopes(body: Record<string, unknown>, parentScopes: string[]) {
  const validParentScopes = parentScopes
    .filter((scope) => (VALID_SCOPES as readonly string[]).includes(scope))
    .sort();
  const requested = body.scopes === undefined
    ? validParentScopes
    : normalizedStringArray(body.scopes, "scopes");
  if (requested.length === 0) {
    throw new ControlPlaneHttpError(
      400,
      "session_scopes_required",
      "At least one session scope is required.",
    );
  }
  if (requested.some((scope) => !(VALID_SCOPES as readonly string[]).includes(scope))) {
    throw new ControlPlaneHttpError(
      400,
      "session_scope_invalid",
      "Requested session scopes contain an unsupported scope.",
    );
  }
  if (requested.some((scope) => !validParentScopes.includes(scope))) {
    throw new ControlPlaneHttpError(
      403,
      "session_scope_escalation",
      "Requested session scopes must be a subset of the parent token scopes.",
    );
  }
  return requested;
}

function replayTtl(row: SessionReplayRow) {
  const created = Date.parse(row.created_at);
  const expires = Date.parse(row.expires_at);
  return Number.isFinite(created) && Number.isFinite(expires)
    ? Math.max(1, Math.round((expires - created) / 1000))
    : 0;
}

export async function getGatewayStatus(request: Request): Promise<LifecycleResult> {
  requirePostgresOwner();
  return withPostgresTransaction(async (client) => {
    const identity = await authenticateAgentGateway(client, request.headers);
    enforceWorkspaceBinding(identity, {
      header: request.headers.get("x-agentops-workspace-id"),
    });
    enforceAgentBinding(identity, request);
    const agent = await lockedAgent(client, identity.agentId);
    const auth: Record<string, unknown> = {
      mode: identity.mode,
      authenticated: true,
      agent_id: identity.agentId,
      workspace_id: identity.workspaceId,
      scopes: identity.scopes,
      agent_status: agent.status,
      runtime_type: agent.runtime_type,
    };

    if (identity.mode === "agent_token") {
      const result = await client.query<TokenStatusRow>(
        `SELECT token_id,status,heartbeat_timeout_sec,expires_at,last_used_at,last_heartbeat_at
        FROM agent_gateway_tokens WHERE token_id=$1`,
        [identity.credentialId],
      );
      const token = result.rows[0];
      if (!token) {
        throw new ControlPlaneHttpError(401, "unauthorized", "Agent token is unavailable.");
      }
      auth.token_ref = safeRef("token", token.token_id);
      auth.token_id_omitted = true;
      auth.token_status = token.status;
      auth.heartbeat_timeout_sec = token.heartbeat_timeout_sec;
      if (!token.last_heartbeat_at) {
        auth.heartbeat_state = "never_seen";
      } else {
        const heartbeatAt = Date.parse(token.last_heartbeat_at);
        const timeoutMs = Math.max(Number(token.heartbeat_timeout_sec) || 300, 1) * 1000;
        auth.heartbeat_state = (
          !Number.isFinite(heartbeatAt)
          || Date.now() - heartbeatAt > timeoutMs
        ) ? "stale" : "online";
      }
      auth.expires_at = token.expires_at;
      auth.last_used_at = token.last_used_at;
      auth.last_heartbeat_at = token.last_heartbeat_at;
    } else {
      auth.session_ref = safeRef("session", identity.credentialId);
      auth.parent_token_ref = safeRef("token", identity.parentTokenId || "");
      auth.session_id_omitted = true;
      auth.parent_token_id_omitted = true;
      auth.session_expires_at = identity.expiresAt;
    }

    return {
      status: 200,
      body: {
        provider: "agent_gateway",
        status: "ready",
        auth,
        valid_scopes: VALID_SCOPES,
        token_omitted: true,
      },
    };
  });
}

export async function registerGatewayAgent(request: Request): Promise<LifecycleResult> {
  requirePostgresOwner();
  const body = await boundedJsonObject(request, {
    maxBytes: GATEWAY_LIFECYCLE_MAX_BODY_BYTES,
    label: "Gateway agent registration",
  });
  rejectUnknownFields(body, REGISTER_FIELDS, "gateway_register");
  const requestId = optionalIdentifier(body.request_id, "request_id");
  return withPostgresTransaction(async (client) => {
    const identity = await authenticateAgentGateway(client, request.headers, "agents:write");
    enforceBindings(identity, request, body);
    const before = await lockedAgent(client, identity.agentId);
    requireEnabledAgent(before);

    const requestedPermission = body.permission_level === undefined
      ? before.permission_level
      : sanitizedText(body.permission_level, "permission_level", 80, before.permission_level);
    if (requestedPermission !== before.permission_level) {
      throw new ControlPlaneHttpError(
        403,
        "agent_permission_escalation",
        "Gateway registration cannot change the enrolled permission level.",
      );
    }

    const currentTools = parseStoredArray(before.allowed_tools);
    const requestedTools = body.allowed_tools === undefined
      ? currentTools
      : normalizedStringArray(body.allowed_tools, "allowed_tools");
    if (requestedTools.some((tool) => !currentTools.includes(tool))) {
      throw new ControlPlaneHttpError(
        403,
        "agent_tool_escalation",
        "Gateway registration cannot add tools beyond the enrolled allowlist.",
      );
    }

    const requestedBudget = body.budget_limit_usd === undefined
      ? Number(before.budget_limit_usd)
      : Number(body.budget_limit_usd);
    if (
      !Number.isFinite(requestedBudget)
      || requestedBudget < 0
      || requestedBudget > Number(before.budget_limit_usd)
    ) {
      throw new ControlPlaneHttpError(
        403,
        "agent_budget_escalation",
        "Gateway registration cannot increase the enrolled budget.",
      );
    }

    const runtimeType = body.runtime_type === undefined
      ? before.runtime_type
      : String(body.runtime_type).trim();
    if (!VALID_RUNTIME_TYPES.has(runtimeType) || runtimeType !== before.runtime_type) {
      throw new ControlPlaneHttpError(
        409,
        "registration_runtime_mismatch",
        "Gateway registration runtime_type must match the enrolled agent.",
      );
    }
    const next = {
      name: sanitizedText(body.name, "name", 120, before.name),
      role: sanitizedText(body.role, "role", 120, before.role),
      description: sanitizedText(
        body.description,
        "description",
        360,
        before.description || "Agent registered through the commercial Gateway.",
      ),
      runtimeType,
      modelProvider: sanitizedText(
        body.model_provider,
        "model_provider",
        80,
        before.model_provider || "external",
      ),
      modelName: sanitizedText(
        body.model_name,
        "model_name",
        120,
        before.model_name || "gateway-client",
      ),
      allowedTools: requestedTools,
      budgetLimitUsd: requestedBudget,
    };
    const changed = (
      next.name !== before.name
      || next.role !== before.role
      || next.description !== before.description
      || next.runtimeType !== before.runtime_type
      || next.modelProvider !== before.model_provider
      || next.modelName !== before.model_name
      || JSON.stringify(next.allowedTools) !== JSON.stringify(currentTools)
      || next.budgetLimitUsd !== Number(before.budget_limit_usd)
    );

    let agent = before;
    if (changed) {
      const now = new Date().toISOString();
      const result = await client.query<AgentRow>(
        `UPDATE agents SET
          name=$1,role=$2,description=$3,runtime_type=$4,model_provider=$5,
          model_name=$6,allowed_tools=$7,budget_limit_usd=$8,updated_at=$9
        WHERE agent_id=$10
        RETURNING agent_id,name,role,description,runtime_type,model_provider,model_name,
          status,permission_level,allowed_tools,budget_limit_usd,created_at,updated_at`,
        [
          next.name,
          next.role,
          next.description,
          next.runtimeType,
          next.modelProvider,
          next.modelName,
          JSON.stringify(next.allowedTools),
          next.budgetLimitUsd,
          now,
          identity.agentId,
        ],
      );
      agent = result.rows[0];
      await appendAudit(client, {
        workspaceId: identity.workspaceId,
        actorType: "agent",
        actorId: identity.agentId,
        action: "agent_gateway.register",
        entityType: "agents",
        entityId: identity.agentId,
        before: publicAgent(before),
        after: publicAgent(agent),
        metadata: {
          request_ref: requestId ? safeRef("request", requestId) : null,
          credential_mode: identity.mode,
          privilege_escalation_allowed: false,
          raw_content_omitted: true,
          token_omitted: true,
        },
      });
    }

    return {
      status: 200,
      body: {
        agent: publicAgent(agent),
        outcome: changed ? "updated" : "unchanged",
        workspace_id: identity.workspaceId,
        token_omitted: true,
      },
    };
  });
}

export async function recordGatewayHeartbeat(request: Request): Promise<LifecycleResult> {
  requirePostgresOwner();
  const body = await boundedJsonObject(request, {
    maxBytes: GATEWAY_LIFECYCLE_MAX_BODY_BYTES,
    label: "Gateway heartbeat",
  });
  rejectUnknownFields(body, HEARTBEAT_FIELDS, "gateway_heartbeat");
  const requestId = optionalIdentifier(body.request_id, "request_id");
  return withPostgresTransaction(async (client) => {
    const identity = await authenticateAgentGateway(
      client,
      request.headers,
      "agents:heartbeat",
    );
    enforceBindings(identity, request, body);
    const before = await lockedAgent(client, identity.agentId);
    requireEnabledAgent(before);
    const status = String(body.status ?? "idle").trim().toLowerCase();
    if (!HEARTBEAT_STATUSES.has(status)) {
      throw new ControlPlaneHttpError(
        400,
        "heartbeat_status_invalid",
        "Gateway heartbeat status is invalid.",
      );
    }
    if (body.runtime_type !== undefined) {
      const runtimeType = String(body.runtime_type).trim();
      if (!VALID_RUNTIME_TYPES.has(runtimeType) || runtimeType !== before.runtime_type) {
        throw new ControlPlaneHttpError(
          409,
          "heartbeat_runtime_mismatch",
          "Gateway heartbeat runtime_type must match the registered agent.",
        );
      }
    }
    if (body.summary !== undefined) {
      sanitizedText(body.summary, "summary", 200, "Heartbeat recorded.");
    }
    const now = new Date().toISOString();
    const result = await client.query<AgentRow>(
      `UPDATE agents SET status=$1,updated_at=$2 WHERE agent_id=$3
      RETURNING agent_id,name,role,description,runtime_type,model_provider,model_name,
        status,permission_level,allowed_tools,budget_limit_usd,created_at,updated_at`,
      [status, now, identity.agentId],
    );
    const agent = result.rows[0];
    const parentTokenId = identity.mode === "agent_token"
      ? identity.credentialId
      : identity.parentTokenId;
    if (!parentTokenId) {
      throw new ControlPlaneHttpError(
        401,
        "unauthorized",
        "Gateway heartbeat parent token binding is missing.",
      );
    }
    const heartbeat = await client.query(
      `UPDATE agent_gateway_tokens SET last_heartbeat_at=$1,last_used_at=$1
      WHERE token_id=$2 AND workspace_id=$3 AND agent_id=$4 AND status='active'
      RETURNING token_id`,
      [now, parentTokenId, identity.workspaceId, identity.agentId],
    );
    if (heartbeat.rowCount !== 1) {
      throw new ControlPlaneHttpError(
        401,
        "unauthorized",
        "Gateway heartbeat parent token binding is no longer active.",
      );
    }
    if (before.status !== agent.status) {
      await appendAudit(client, {
        workspaceId: identity.workspaceId,
        actorType: "agent",
        actorId: identity.agentId,
        action: "agent_gateway.heartbeat_state_change",
        entityType: "agents",
        entityId: identity.agentId,
        before: { status: before.status },
        after: { status: agent.status },
        metadata: {
          credential_mode: identity.mode,
          request_ref: requestId ? safeRef("request", requestId) : null,
          runtime_type: agent.runtime_type,
          summary_omitted: true,
          raw_content_omitted: true,
          token_omitted: true,
        },
      });
    }
    return {
      status: 200,
      body: {
        agent_id: identity.agentId,
        workspace_id: identity.workspaceId,
        status: agent.status,
        recorded_at: now,
        token_omitted: true,
      },
    };
  });
}

export async function createGatewaySession(request: Request): Promise<LifecycleResult> {
  requirePostgresOwner();
  const body = await boundedJsonObject(request, {
    maxBytes: GATEWAY_LIFECYCLE_MAX_BODY_BYTES,
    label: "Gateway session creation",
  });
  rejectUnknownFields(body, SESSION_FIELDS, "gateway_session_create");
  return withPostgresTransaction(async (client) => {
    const identity = await authenticateAgentGateway(client, request.headers);
    if (identity.mode !== "agent_token") {
      throw new ControlPlaneHttpError(
        401,
        "session_parent_token_required",
        "A parent Agent Gateway token is required to create a session.",
      );
    }
    enforceBindings(identity, request, body);
    const scopes = requestedSessionScopes(body, identity.scopes);
    const ttl = ttlSeconds(body);
    const suppliedRequestId = optionalIdentifier(body.request_id, "request_id");
    const requestKey = suppliedRequestId || `implicit:${randomUUID()}`;
    const lockKey = [
      "gateway_session_create",
      identity.credentialId,
      identity.workspaceId,
      identity.agentId,
      requestKey,
    ].join(":");
    await client.query("SELECT pg_advisory_xact_lock(hashtextextended($1,0))", [lockKey]);
    const sessionId = `ags_${createHash("sha256")
      .update(lockKey, "utf8")
      .digest("hex")
      .slice(0, 32)}`;
    const existingResult = await client.query<SessionReplayRow>(
      `SELECT session_id,workspace_id,agent_id,scopes_json,status,created_at,expires_at
      FROM agent_gateway_sessions WHERE session_id=$1 FOR UPDATE`,
      [sessionId],
    );
    const existing = existingResult.rows[0];
    if (existing) {
      const sameBinding = existing.workspace_id === identity.workspaceId
        && existing.agent_id === identity.agentId
        && JSON.stringify(parseStoredScopes(existing.scopes_json)) === JSON.stringify(scopes)
        && replayTtl(existing) === ttl;
      if (!sameBinding) {
        throw new ControlPlaneHttpError(
          409,
          "session_request_binding_conflict",
          "Session request_id is already bound to different session parameters.",
        );
      }
      const replayAgent = await lockedAgent(client, identity.agentId);
      requireEnabledAgent(replayAgent);
      if (
        body.runtime_type !== undefined
        && String(body.runtime_type).trim() !== replayAgent.runtime_type
      ) {
        throw new ControlPlaneHttpError(
          409,
          "session_runtime_mismatch",
          "Gateway session runtime_type must match the registered agent.",
        );
      }
      return {
        status: 200,
        body: {
          created: false,
          replayed: true,
          session_id: existing.session_id,
          agent_id: existing.agent_id,
          workspace_id: existing.workspace_id,
          scopes,
          status: existing.status,
          expires_at: existing.expires_at,
          ttl_sec: replayTtl(existing),
          session_token_omitted: true,
          token_omitted: true,
        },
      };
    }

    const entitlementDecision = await evaluateWorkspaceEntitlement(client, {
      workspaceId: identity.workspaceId,
      operation: "session_issue",
      agentId: identity.agentId,
    });
    if (!entitlementDecision.allow) {
      const denialRequestHash = stableHash({
        workspace_id: identity.workspaceId,
        agent_id: identity.agentId,
        operation: "session_issue",
        parent_token_ref: safeRef("token", identity.credentialId),
        request_key: requestKey,
      });
      await appendAudit(client, {
        workspaceId: identity.workspaceId,
        actorType: "agent",
        actorId: identity.agentId,
        action: "agent_gateway.session_entitlement_denied",
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
          credential_mode: identity.mode,
          parent_token_ref: safeRef("token", identity.credentialId),
          request_ref: suppliedRequestId
            ? safeRef("request", suppliedRequestId)
            : null,
          session_token_generated: false,
          token_omitted: true,
          raw_config_omitted: true,
        },
        requestHash: denialRequestHash,
      });
      return {
        status: 403,
        body: {
          ok: false,
          error: "workspace_entitlement_denied",
          message: "Workspace entitlement does not permit a new Agent Gateway session.",
          created: false,
          replayed: false,
          workspace_id: identity.workspaceId,
          agent_id: identity.agentId,
          entitlement_decision: entitlementDecision,
          session_token_generated: false,
          token_omitted: true,
          raw_config_omitted: true,
        },
      };
    }

    const agent = await lockedAgent(client, identity.agentId);
    requireEnabledAgent(agent);
    if (
      body.runtime_type !== undefined
      && String(body.runtime_type).trim() !== agent.runtime_type
    ) {
      throw new ControlPlaneHttpError(
        409,
        "session_runtime_mismatch",
        "Gateway session runtime_type must match the registered agent.",
      );
    }
    const now = new Date();
    const createdAt = now.toISOString();
    const expiresAt = new Date(now.getTime() + ttl * 1000).toISOString();
    const sessionToken = `agtsess_${randomBytes(32).toString("base64url")}`;
    const sessionHash = createHash("sha256")
      .update(sessionToken, "utf8")
      .digest("hex");
    await client.query(
      `INSERT INTO agent_gateway_sessions(
        session_id,session_hash,parent_token_id,workspace_id,agent_id,scopes_json,
        status,created_at,expires_at,revoked_at,last_used_at
      ) VALUES($1,$2,$3,$4,$5,$6,'active',$7,$8,NULL,NULL)`,
      [
        sessionId,
        sessionHash,
        identity.credentialId,
        identity.workspaceId,
        identity.agentId,
        JSON.stringify(scopes),
        createdAt,
        expiresAt,
      ],
    );
    await appendAudit(client, {
      workspaceId: identity.workspaceId,
      actorType: "agent",
      actorId: identity.agentId,
      action: "agent_gateway.session_create",
      entityType: "agent_gateway_sessions",
      entityId: sessionId,
      after: {
        session_ref: safeRef("session", sessionId),
        workspace_id: identity.workspaceId,
        agent_id: identity.agentId,
        scopes,
        status: "active",
        created_at: createdAt,
        expires_at: expiresAt,
      },
      metadata: {
        parent_token_ref: safeRef("token", identity.credentialId),
        request_ref: suppliedRequestId
          ? safeRef("request", suppliedRequestId)
          : null,
        session_hash_omitted: true,
        token_omitted: true,
        raw_content_omitted: true,
      },
    });
    return {
      status: 201,
      body: {
        created: true,
        replayed: false,
        session_id: sessionId,
        agent_id: identity.agentId,
        workspace_id: identity.workspaceId,
        scopes,
        expires_at: expiresAt,
        ttl_sec: ttl,
        session_token: sessionToken,
        note: "Session token is shown once; the control plane stores only its hash.",
        token_omitted: false,
      },
    };
  });
}
