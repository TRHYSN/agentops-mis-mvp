import { createHash } from "node:crypto";
import type { PoolClient } from "pg";

import { ControlPlaneHttpError } from "./http";

type CredentialRow = {
  credential_type: "token" | "session";
  credential_id: string;
  parent_token_id: string | null;
  workspace_id: string;
  agent_id: string;
  scopes_json: string;
  status: string;
  expires_at: string | null;
};

type ParentTokenRow = {
  token_id: string;
  workspace_id: string;
  agent_id: string;
  scopes_json: string;
  status: string;
  expires_at: string | null;
};

export type AgentGatewayIdentity = {
  mode: "agent_token" | "agent_session";
  credentialId: string;
  parentTokenId: string | null;
  workspaceId: string;
  agentId: string;
  scopes: string[];
  expiresAt: string | null;
};

function bearerCredential(headers: Headers) {
  const authorization = String(headers.get("authorization") || "").trim();
  if (!/^Bearer\s+\S+$/i.test(authorization)) {
    throw new ControlPlaneHttpError(
      401,
      "unauthorized",
      "A Bearer Agent Gateway token or session is required.",
    );
  }
  const supplied = authorization.replace(/^Bearer\s+/i, "");
  if (supplied.length > 4096) {
    throw new ControlPlaneHttpError(401, "unauthorized", "Agent credential is invalid.");
  }
  return supplied;
}

function parseScopes(value: string) {
  try {
    const parsed: unknown = JSON.parse(value);
    return Array.isArray(parsed)
      ? [...new Set(parsed.map((item) => String(item).trim()).filter(Boolean))]
      : [];
  } catch {
    return [];
  }
}

function expired(value: string | null, allowMissing: boolean) {
  if (value === null) return !allowMissing;
  const expiresAt = Date.parse(value);
  return !Number.isFinite(expiresAt) || expiresAt <= Date.now();
}

function requireScope(scopes: string[], requiredScope: string) {
  if (!scopes.includes(requiredScope)) {
    throw new ControlPlaneHttpError(
      403,
      "forbidden",
      `Agent credential is missing required scope: ${requiredScope}`,
    );
  }
}

export async function authenticateAgentGateway(
  client: PoolClient,
  headers: Headers,
  requiredScope?: string,
): Promise<AgentGatewayIdentity> {
  const supplied = bearerCredential(headers);
  const hash = createHash("sha256").update(supplied, "utf8").digest("hex");
  const tokenResult = await client.query<CredentialRow>(
    `SELECT 'token'::text AS credential_type,token_id AS credential_id,
      NULL::text AS parent_token_id,workspace_id,agent_id,scopes_json,status,expires_at
    FROM agent_gateway_tokens WHERE token_hash=$1 FOR UPDATE`,
    [hash],
  );
  let credential = tokenResult.rows[0];
  let lockedParent: ParentTokenRow | undefined;
  if (!credential) {
    const sessionCandidate = (await client.query<CredentialRow>(
      `SELECT 'session'::text AS credential_type,session_id AS credential_id,
        parent_token_id,workspace_id,agent_id,scopes_json,status,expires_at
      FROM agent_gateway_sessions WHERE session_hash=$1`,
      [hash],
    )).rows[0];
    if (sessionCandidate?.parent_token_id) {
      lockedParent = (await client.query<ParentTokenRow>(
        `SELECT token_id,workspace_id,agent_id,scopes_json,status,expires_at
        FROM agent_gateway_tokens WHERE token_id=$1 FOR UPDATE`,
        [sessionCandidate.parent_token_id],
      )).rows[0];
    }
    credential = (await client.query<CredentialRow>(
      `SELECT 'session'::text AS credential_type,session_id AS credential_id,
        parent_token_id,workspace_id,agent_id,scopes_json,status,expires_at
      FROM agent_gateway_sessions WHERE session_hash=$1 FOR UPDATE`,
      [hash],
    )).rows[0];
    if (
      credential
      && credential.parent_token_id !== sessionCandidate?.parent_token_id
    ) {
      throw new ControlPlaneHttpError(
        401,
        "unauthorized",
        "Agent session parent binding changed during authentication.",
      );
    }
  }
  if (!credential || credential.status !== "active") {
    throw new ControlPlaneHttpError(401, "unauthorized", "Agent credential is not active.");
  }

  const credentialExpired = expired(
    credential.expires_at,
    credential.credential_type === "token",
  );
  if (credentialExpired) {
    const table = credential.credential_type === "token"
      ? "agent_gateway_tokens"
      : "agent_gateway_sessions";
    const idColumn = credential.credential_type === "token" ? "token_id" : "session_id";
    await client.query(
      `UPDATE ${table} SET status='expired' WHERE ${idColumn}=$1 AND status='active'`,
      [credential.credential_id],
    );
    throw new ControlPlaneHttpError(
      401,
      "unauthorized",
      "Agent credential is expired.",
      true,
    );
  }

  const granted = parseScopes(credential.scopes_json);
  if (requiredScope) requireScope(granted, requiredScope);
  if (credential.credential_type === "session") {
    if (!credential.parent_token_id) {
      await client.query(
        `UPDATE agent_gateway_sessions
        SET status='revoked',revoked_at=$1
        WHERE session_id=$2 AND status='active'`,
        [new Date().toISOString(), credential.credential_id],
      );
      throw new ControlPlaneHttpError(
        401,
        "unauthorized",
        "Agent session parent is missing.",
        true,
      );
    }
    const parent = lockedParent;
    if (
      !parent
      || parent.status !== "active"
      || expired(parent.expires_at, true)
      || parent.workspace_id !== credential.workspace_id
      || parent.agent_id !== credential.agent_id
    ) {
      await client.query(
        `UPDATE agent_gateway_sessions
        SET status='revoked',revoked_at=$1
        WHERE session_id=$2 AND status='active'`,
        [new Date().toISOString(), credential.credential_id],
      );
      throw new ControlPlaneHttpError(
        401,
        "unauthorized",
        "Agent session parent binding is invalid.",
        true,
      );
    }
    const parentScopes = parseScopes(parent.scopes_json);
    if (granted.some((scope) => !parentScopes.includes(scope))) {
      await client.query(
        `UPDATE agent_gateway_sessions
        SET status='revoked',revoked_at=$1
        WHERE session_id=$2 AND status='active'`,
        [new Date().toISOString(), credential.credential_id],
      );
      throw new ControlPlaneHttpError(
        401,
        "unauthorized",
        "Agent session scopes are not bound to its parent token.",
        true,
      );
    }
    if (requiredScope) requireScope(parentScopes, requiredScope);
  }

  const table = credential.credential_type === "token"
    ? "agent_gateway_tokens"
    : "agent_gateway_sessions";
  const idColumn = credential.credential_type === "token" ? "token_id" : "session_id";
  await client.query(
    `UPDATE ${table} SET last_used_at=$1 WHERE ${idColumn}=$2`,
    [new Date().toISOString(), credential.credential_id],
  );
  return {
    mode: credential.credential_type === "token" ? "agent_token" : "agent_session",
    credentialId: credential.credential_id,
    parentTokenId: credential.parent_token_id,
    workspaceId: credential.workspace_id,
    agentId: credential.agent_id,
    scopes: granted,
    expiresAt: credential.expires_at,
  };
}

export function enforceWorkspaceBinding(
  identity: AgentGatewayIdentity,
  input: { header?: string | null; query?: string | null; body?: unknown },
) {
  for (const requested of [
    input.header,
    input.query,
    typeof input.body === "string" ? input.body : null,
  ]) {
    if (requested && requested.trim() !== identity.workspaceId) {
      throw new ControlPlaneHttpError(
        403,
        "forbidden",
        "Agent credential cannot act in another workspace.",
      );
    }
  }
}
