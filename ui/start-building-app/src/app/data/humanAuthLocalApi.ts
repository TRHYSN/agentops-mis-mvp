import {
  apiJson,
  humanAuthJson,
  setHumanAuthCsrf,
  type HumanAuthSession,
  type HumanBrowserSessionRevokePayload,
  type HumanBrowserSessionsPayload,
  type HumanPairedDevicesPayload,
  type HumanPairingInvitationCreated,
  type HumanPairingInvitationsPayload,
  type HumanPairingRole,
  type HumanPasswordRecoveryStart,
} from "./liveApi";

export async function bootstrapHuman(input: {
  setup_code: string;
  username: string;
  password: string;
  display_name?: string;
}): Promise<HumanAuthSession> {
  const session = await apiJson<HumanAuthSession>("/human-auth/bootstrap", {
    method: "POST",
    body: JSON.stringify(input),
  });
  setHumanAuthCsrf(session.csrf_token);
  return session;
}

export async function startHumanPasswordRecovery(
  setupCode: string,
): Promise<HumanPasswordRecoveryStart> {
  return apiJson<HumanPasswordRecoveryStart>("/human-auth/password-recovery/start", {
    method: "POST",
    body: JSON.stringify({ setup_code: setupCode }),
  });
}

export async function completeHumanPasswordRecovery(input: {
  recovery_authority: string;
  username: string;
  password: string;
}): Promise<HumanAuthSession> {
  const session = await apiJson<HumanAuthSession>("/human-auth/password-recovery/complete", {
    method: "POST",
    body: JSON.stringify(input),
  });
  setHumanAuthCsrf(session.csrf_token);
  return session;
}

export async function loadHumanBrowserSessions(): Promise<HumanBrowserSessionsPayload> {
  return apiJson<HumanBrowserSessionsPayload>("/human-auth/sessions");
}

export async function revokeHumanBrowserSession(
  input: { session_ref: string } | { all_other: true },
): Promise<HumanBrowserSessionRevokePayload> {
  return apiJson<HumanBrowserSessionRevokePayload>("/human-auth/sessions/revoke", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function loadHumanPairingInvitations(): Promise<HumanPairingInvitationsPayload> {
  return humanAuthJson<HumanPairingInvitationsPayload>("/human-auth/pairing-invitations");
}

export async function createHumanPairingInvitation(input: {
  role: HumanPairingRole;
  expires_in_seconds: number;
  label?: string;
}): Promise<HumanPairingInvitationCreated> {
  return humanAuthJson<HumanPairingInvitationCreated>("/human-auth/pairing-invitations", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function revokeHumanPairingInvitation(
  invitationRef: string,
): Promise<Record<string, unknown>> {
  return humanAuthJson<Record<string, unknown>>(
    `/human-auth/pairing-invitations/${encodeURIComponent(invitationRef)}/revoke`,
    { method: "POST", body: "{}" },
  );
}

export async function loadHumanPairedDevices(): Promise<HumanPairedDevicesPayload> {
  return humanAuthJson<HumanPairedDevicesPayload>("/human-auth/devices");
}

export async function revokeHumanPairedDevice(
  deviceRef: string,
): Promise<Record<string, unknown>> {
  return humanAuthJson<Record<string, unknown>>(
    `/human-auth/devices/${encodeURIComponent(deviceRef)}/revoke`,
    { method: "POST", body: "{}" },
  );
}

export async function pairHuman(input: {
  pairing_secret: string;
  username: string;
  password: string;
  display_name?: string;
  device_label?: string;
}): Promise<HumanAuthSession> {
  const session = await humanAuthJson<HumanAuthSession>("/human-auth/pair", {
    method: "POST",
    body: JSON.stringify(input),
  });
  setHumanAuthCsrf(session.csrf_token);
  return session;
}
