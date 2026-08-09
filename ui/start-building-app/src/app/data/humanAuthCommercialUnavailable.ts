function unavailable(..._arguments: unknown[]): Promise<never> {
  return Promise.reject(new Error("local_human_auth_unavailable"));
}

export const bootstrapHuman = unavailable;
export const startHumanPasswordRecovery = unavailable;
export const completeHumanPasswordRecovery = unavailable;
export const loadHumanBrowserSessions = unavailable;
export const revokeHumanBrowserSession = unavailable;
export const loadHumanPairingInvitations = unavailable;
export const createHumanPairingInvitation = unavailable;
export const revokeHumanPairingInvitation = unavailable;
export const loadHumanPairedDevices = unavailable;
export const revokeHumanPairedDevice = unavailable;
export const pairHuman = unavailable;
