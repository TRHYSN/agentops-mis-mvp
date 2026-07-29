import { useCallback, useEffect, useState } from "react";
import type {
  Agent,
  Approval,
  AuditLog,
  Evaluation,
  Memory,
  RuntimeConnector,
  Run,
  Task,
  ToolCall,
} from "./mockData";

const API_BASE = import.meta.env.VITE_AGENTOPS_API_BASE || "/mis-api";
const HUMAN_AUTH_CSRF_KEY = "agentops-human-auth-csrf";
export const HUMAN_AUTH_UNAUTHORIZED_EVENT = "agentops:human-auth-unauthorized";
const HUMAN_SESSION_UNAUTHORIZED_ERRORS = new Set([
  "human_auth_required",
  "human_session_invalid",
  "human_session_expired",
]);

export interface HumanAuthUser {
  account_id?: string;
  user_id?: string;
  workspace_id?: string;
  username: string;
  display_name?: string;
  role?: string;
}

export interface HumanAuthStatus {
  required: boolean;
  authenticated: boolean;
  user?: HumanAuthUser;
  bootstrap_required: boolean;
  password_recovery_available?: boolean;
  password_recovery_local_only?: boolean;
  csrf_token?: string;
}

export interface HumanAuthSession {
  user: HumanAuthUser;
  csrf_token: string;
}

export interface HumanPasswordRecoveryStart {
  operation: "password_recovery_start";
  recovery_authority: string;
  expires_at: string;
  local_host_only: true;
  single_use: true;
  recovery_authority_ephemeral: true;
}

export interface HumanBrowserSession {
  session_ref: string;
  status: "active" | "revoked" | "expired";
  current: boolean;
  created_at: string;
  expires_at: string;
  last_seen_at?: string | null;
  revoked_at?: string | null;
}

export interface HumanBrowserSessionsPayload {
  provider: string;
  operation: string;
  sessions: HumanBrowserSession[];
  current_session_ref: string;
  active_count: number;
  session_count: number;
  session_id_omitted: boolean;
  session_hash_omitted: boolean;
  token_omitted: boolean;
}

export interface HumanBrowserSessionRevokePayload {
  provider: string;
  operation: string;
  status: string;
  revoked_count: number;
  revoked_session_refs: string[];
  current_session_ref: string;
  current_session_preserved: boolean;
  session_id_omitted: boolean;
  session_hash_omitted: boolean;
  token_omitted: boolean;
}

export type HostRelayAction = "enable" | "disable";
export type HostRelayDisplayState =
  | "disabled"
  | "enabled"
  | "prepared"
  | "pending"
  | "restart_scheduled"
  | "manual_restart_required"
  | "restart_required"
  | "rolled_back"
  | "rollback_failed"
  | "unavailable";

export interface HostRelayStatusPayload {
  state: HostRelayDisplayState;
  active_enabled: boolean;
  control_available: boolean;
  transition_pending: boolean;
  restart_required: boolean;
  restart_pending?: boolean;
  rollback_armed?: boolean;
}

export interface HostRelayTransitionPayload extends HostRelayStatusPayload {
  action: HostRelayAction;
  transition_ref: string;
}

export type HumanPairingRole = "operator" | "approver" | "viewer";

export interface HumanPairingInvitation {
  invitation_ref: string;
  role: HumanPairingRole;
  label?: string | null;
  status: "active" | "redeemed" | "revoked" | "expired" | "locked";
  created_at: string;
  expires_at: string;
  redeemed_at?: string | null;
  revoked_at?: string | null;
  attempt_count?: number;
  max_attempts?: number;
}

export interface HumanPairingInvitationsPayload {
  invitations: HumanPairingInvitation[];
  active_count?: number;
  invitation_count?: number;
  secret_omitted?: true;
  pairing_secret_omitted?: true;
}

export interface HumanPairingInvitationCreated {
  invitation_ref: string;
  pairing_secret: string;
  secret_omitted?: false;
  pairing_secret_omitted?: false;
  pairing_secret_ephemeral?: true;
  role: HumanPairingRole;
  label?: string | null;
  expires_at: string;
}

export interface HumanPairedDevice {
  device_ref: string;
  username?: string;
  display_name?: string | null;
  role: HumanPairingRole;
  label?: string | null;
  status: "active" | "revoked";
  paired_at?: string;
  created_at?: string;
  last_seen_at?: string | null;
  revoked_at?: string | null;
}

export interface HumanPairedDevicesPayload {
  devices: HumanPairedDevice[];
  active_count?: number;
  device_count?: number;
  device_secret_omitted?: true;
}

export class HumanAuthRequestError extends Error {
  readonly code: string;

  constructor(code: string) {
    super(code);
    this.name = "HumanAuthRequestError";
    this.code = code;
  }
}

export type PrivateHostAcceptanceCheckId =
  | "human_session"
  | "local_readiness"
  | "tasks_readable"
  | "evaluations_readable"
  | "approvals_readable"
  | "memories_readable"
  | "audit_readable"
  | "artifacts_readable"
  | "marker_task_readable";

export interface PrivateHostAcceptanceCheck {
  id: PrivateHostAcceptanceCheckId;
  ok: boolean;
  count?: number;
  status?: string;
  error?: string;
}

export interface PrivateHostAcceptanceSnapshot {
  checked_at: string;
  checks: PrivateHostAcceptanceCheck[];
  counts: Record<string, number>;
  related_ids: { marker_task_id?: string };
  actor: {
    workspace_id?: string;
    user_id?: string;
    role?: string;
  };
}

export interface PrivateHostAcceptanceMarker {
  receipt_id: string;
  task_id: string;
  title: string;
}

export interface PrivateHostAuthorityReceipt {
  receipt_id: string;
  host_version: string;
  git_commit: string;
  run_id: string;
  adapter: string;
  status: "completed";
  evaluation: {
    score: number;
    pass_fail: "pass";
  };
  artifact_id: string;
  plan_manifest_id: string;
  artifact_metadata_sha256: string;
  payload_sha256: string;
}

function readHumanAuthCsrf(): string {
  if (typeof window === "undefined") return "";
  return window.sessionStorage.getItem(HUMAN_AUTH_CSRF_KEY) || "";
}

export function setHumanAuthCsrf(token?: string | null) {
  if (typeof window === "undefined") return;
  if (token) {
    window.sessionStorage.setItem(HUMAN_AUTH_CSRF_KEY, token);
  } else {
    window.sessionStorage.removeItem(HUMAN_AUTH_CSRF_KEY);
  }
}

function isStateChangingRequest(method?: string): boolean {
  const normalized = (method || "GET").toUpperCase();
  return !["GET", "HEAD", "OPTIONS"].includes(normalized);
}

function humanAuthHeaders(init?: RequestInit): Headers {
  const headers = new Headers(init?.headers);
  if (!headers.has("Content-Type") && init?.body !== undefined) {
    headers.set("Content-Type", "application/json");
  }
  if (isStateChangingRequest(init?.method) && !headers.has("X-AgentOps-CSRF")) {
    const csrfToken = readHumanAuthCsrf();
    if (csrfToken) headers.set("X-AgentOps-CSRF", csrfToken);
  }
  return headers;
}

async function isHumanSessionUnauthorized(response: Response): Promise<boolean> {
  if (response.status !== 401) return false;
  try {
    const payload = await response.clone().json() as { error?: unknown };
    return HUMAN_SESSION_UNAUTHORIZED_ERRORS.has(String(payload.error || ""));
  } catch {
    return false;
  }
}

async function humanAwareFetch(path: string, init?: RequestInit): Promise<Response> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    credentials: "include",
    headers: humanAuthHeaders(init),
  });
  if (!path.startsWith("/human-auth/") && await isHumanSessionUnauthorized(response)) {
    setHumanAuthCsrf(null);
    if (typeof window !== "undefined") {
      window.dispatchEvent(new CustomEvent(HUMAN_AUTH_UNAUTHORIZED_EVENT));
    }
  }
  return response;
}

export async function getHumanAuthStatus(): Promise<HumanAuthStatus> {
  const status = await apiJson<HumanAuthStatus>("/human-auth/status");
  if (status.csrf_token) setHumanAuthCsrf(status.csrf_token);
  return status;
}

export async function loginHuman(input: { username: string; password: string }): Promise<HumanAuthSession> {
  const session = await apiJson<HumanAuthSession>("/human-auth/login", {
    method: "POST",
    body: JSON.stringify(input),
  });
  setHumanAuthCsrf(session.csrf_token);
  return session;
}

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

export async function startHumanPasswordRecovery(setupCode: string): Promise<HumanPasswordRecoveryStart> {
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

export async function logoutHuman(): Promise<void> {
  await apiJson<Record<string, unknown>>("/human-auth/logout", { method: "POST", body: "{}" });
  setHumanAuthCsrf(null);
}

export async function loadHumanBrowserSessions(): Promise<HumanBrowserSessionsPayload> {
  return apiJson<HumanBrowserSessionsPayload>("/human-auth/sessions");
}

export async function revokeHumanBrowserSession(input: { session_ref: string } | { all_other: true }): Promise<HumanBrowserSessionRevokePayload> {
  return apiJson<HumanBrowserSessionRevokePayload>("/human-auth/sessions/revoke", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

function hostRelayRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function hostRelayBoolean(value: unknown): boolean {
  return value === true || value === 1 || value === "1" || value === "true";
}

function hostRelayOptionalBoolean(value: unknown): boolean | undefined {
  if (value === true || value === 1 || value === "1" || value === "true") return true;
  if (value === false || value === 0 || value === "0" || value === "false") return false;
  return undefined;
}

function normalizeHostRelayState(value: unknown): HostRelayStatusPayload {
  const root = hostRelayRecord(value);
  const relay = hostRelayRecord(root.relay);
  const status = hostRelayRecord(root.status);
  const transition = hostRelayRecord(root.transition);
  const source = Object.keys(relay).length
    ? relay
    : Object.keys(status).length
      ? status
      : Object.keys(transition).length
        ? transition
        : root;
  const rawState = String(
    source.state
      || source.relay_state
      || source.lifecycle_state
      || root.state
      || root.relay_state
      || "",
  ).toLowerCase().replaceAll("-", "_").replaceAll(" ", "_");
  const activeEnabled = hostRelayBoolean(source.active_enabled ?? source.enabled ?? root.active_enabled ?? root.enabled);
  const transitionPending = hostRelayBoolean(
    source.transition_pending ?? source.pending ?? root.transition_pending ?? root.pending,
  );
  const restartRequired = hostRelayBoolean(
    source.restart_required ?? source.requires_restart ?? root.restart_required ?? root.requires_restart,
  );
  const restartPending = hostRelayBoolean(
    source.restart_pending ?? root.restart_pending,
  );
  const rollbackArmed = hostRelayBoolean(
    source.rollback_armed ?? root.rollback_armed,
  );
  const controlAvailable = hostRelayOptionalBoolean(
    source.control_available ?? source.available ?? root.control_available ?? root.available,
  ) !== false;
  const configured = hostRelayOptionalBoolean(source.configured ?? root.configured);
  const explicitlyPrepared = hostRelayBoolean(source.prepared ?? root.prepared) || rawState === "prepared";
  const explicitlyDisabled = rawState === "disabled" || (!activeEnabled && configured === false);

  let state: HostRelayDisplayState = "unavailable";
  if (rawState === "restart_scheduled") state = "restart_scheduled";
  else if (rawState === "manual_restart_required") state = "manual_restart_required";
  else if (rawState === "rolled_back") state = "rolled_back";
  else if (rawState === "rollback_failed") state = "rollback_failed";
  else if (restartRequired || rawState === "restart_required") state = "restart_required";
  else if (rawState === "enabled") state = "enabled";
  else if (
    transitionPending
    || rawState === "pending"
    || rawState === "connecting"
    || rawState === "connected"
    || rawState === "backoff"
  ) state = "pending";
  else if (explicitlyPrepared) state = "prepared";
  else if (explicitlyDisabled) state = "disabled";

  return {
    state,
    active_enabled: activeEnabled,
    control_available: controlAvailable,
    transition_pending: transitionPending,
    restart_required: restartRequired,
    restart_pending: restartPending,
    rollback_armed: rollbackArmed,
  };
}

function normalizeHostRelayTransition(value: unknown, action: HostRelayAction): HostRelayTransitionPayload {
  const root = hostRelayRecord(value);
  const transition = hostRelayRecord(root.transition);
  const transitionRef = String(
    transition.transition_ref || transition.ref || root.transition_ref || root.ref || "",
  );
  if (!/^[A-Za-z0-9._:-]{1,160}$/.test(transitionRef)) {
    throw new Error("relay_transition_unavailable");
  }
  return {
    ...normalizeHostRelayState(value),
    action,
    transition_ref: transitionRef,
  };
}

export async function loadHostRelay(): Promise<HostRelayStatusPayload> {
  const payload = await apiJson<unknown>("/host/relay");
  return normalizeHostRelayState(payload);
}

export async function prepareHostRelayTransition(action: HostRelayAction): Promise<HostRelayTransitionPayload> {
  const payload = await apiJson<unknown>("/host/relay/transitions", {
    method: "POST",
    body: JSON.stringify({ action }),
  });
  return normalizeHostRelayTransition(payload, action);
}

export async function confirmHostRelayTransition(transitionRef: string, action: HostRelayAction): Promise<HostRelayStatusPayload> {
  if (!/^[A-Za-z0-9._:-]{1,160}$/.test(transitionRef)) {
    throw new Error("relay_transition_unavailable");
  }
  const payload = await apiJson<unknown>(`/host/relay/transitions/${encodeURIComponent(transitionRef)}/confirm`, {
    method: "POST",
    body: JSON.stringify({ action }),
  });
  return normalizeHostRelayState(payload);
}

function boundedHumanAuthErrorCode(value: unknown): string {
  const code = typeof value === "string" ? value : "";
  return /^[a-z0-9_]{1,64}$/.test(code) ? code : "human_auth_request_failed";
}

async function safeHumanAuthJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await humanAwareFetch(path, init);
  if (!response.ok) {
    let code = "human_auth_request_failed";
    try {
      const payload = await response.json() as { error?: unknown };
      code = boundedHumanAuthErrorCode(payload.error);
    } catch {
      // Pairing errors intentionally omit response bodies from the client error.
    }
    throw new HumanAuthRequestError(code);
  }
  return response.json() as Promise<T>;
}

export async function loadHumanPairingInvitations(): Promise<HumanPairingInvitationsPayload> {
  return safeHumanAuthJson<HumanPairingInvitationsPayload>("/human-auth/pairing-invitations");
}

export async function createHumanPairingInvitation(input: {
  role: HumanPairingRole;
  expires_in_seconds: number;
  label?: string;
}): Promise<HumanPairingInvitationCreated> {
  return safeHumanAuthJson<HumanPairingInvitationCreated>("/human-auth/pairing-invitations", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function revokeHumanPairingInvitation(invitationRef: string): Promise<Record<string, unknown>> {
  return safeHumanAuthJson<Record<string, unknown>>(`/human-auth/pairing-invitations/${encodeURIComponent(invitationRef)}/revoke`, {
    method: "POST",
    body: "{}",
  });
}

export async function loadHumanPairedDevices(): Promise<HumanPairedDevicesPayload> {
  return safeHumanAuthJson<HumanPairedDevicesPayload>("/human-auth/devices");
}

export async function revokeHumanPairedDevice(deviceRef: string): Promise<Record<string, unknown>> {
  return safeHumanAuthJson<Record<string, unknown>>(`/human-auth/devices/${encodeURIComponent(deviceRef)}/revoke`, {
    method: "POST",
    body: "{}",
  });
}

export async function pairHuman(input: {
  pairing_secret: string;
  username: string;
  password: string;
  display_name?: string;
  device_label?: string;
}): Promise<HumanAuthSession> {
  const session = await safeHumanAuthJson<HumanAuthSession>("/human-auth/pair", {
    method: "POST",
    body: JSON.stringify(input),
  });
  setHumanAuthCsrf(session.csrf_token);
  return session;
}

export interface DashboardMetrics {
  agents_total: number;
  agents_running: number;
  tasks_completed_total: number;
  total_cost_usd: number;
  avg_task_cost_usd: number;
  failure_rate: number;
  pending_approvals: number;
  stale_or_due_memories: number;
  task_status_distribution: { status: string; count: number }[];
  top_cost_agents: { agent_id: string; name: string; cost_usd: number }[];
  top_failing_agents: { agent_id: string; name: string; failures: number }[];
  runtime_health: Record<string, unknown>[];
  openclaw_import: {
    agents: number;
    cron_tasks: number;
    enabled_cron_tasks: number;
    cron_runs: number;
    failed_runs: number;
    failed_quality_gates: number;
  };
  agent_performance_summary: {
    agent_id: string;
    name: string;
    runtime_type: string;
    total_runs: number;
    success_rate: number;
    avg_duration_ms: number;
    total_cost_usd: number;
    failures: number;
    approval_required_count: number;
  }[];
  recent_runs: Run[];
}

export interface RunDetailPayload {
  run: Run;
  tool_calls: ToolCall[];
  approvals: Approval[];
  evaluations: Evaluation[];
  artifacts?: { artifact_id: string; title: string; artifact_type: string; summary: string; created_at: string }[];
  evaluation_case_runs?: EvaluationCaseRun[];
}

export interface RunEvidenceGraphPayload {
  provider: string;
  operation: string;
  schema_version?: string;
  status?: string;
  run_id: string;
  task_id?: string | null;
  agent_id?: string | null;
  agent_plan_id?: string | null;
  plan_hash?: string | null;
  plan_evidence_manifest_id?: string | null;
  evidence_counts: Record<string, number>;
  nodes?: Record<string, unknown>[];
  edges?: Record<string, unknown>[];
  graph_hash?: string;
  authority?: string;
  safety?: {
    read_only?: boolean;
    ledger_mutated?: boolean;
    live_execution_performed?: boolean;
    raw_prompt_omitted?: boolean;
    raw_response_omitted?: boolean;
    raw_content_omitted?: boolean;
    token_omitted?: boolean;
  };
  token_omitted?: boolean;
}

export interface TaskDetailPayload {
  task: Task;
  runs: Run[];
  approvals: Approval[];
  evaluations: Evaluation[];
  memories: Memory[];
  artifacts?: { artifact_id: string; title: string; artifact_type: string; summary: string; created_at: string }[];
  evaluation_case_runs?: EvaluationCaseRun[];
}

export interface AgentPerformancePayload {
  agent: Agent;
  total_runs: number;
  completed_runs: number;
  failures: number;
  success_rate: number;
  avg_duration_ms: number;
  total_cost_usd: number;
  approval_required_count: number;
  recent_error_types: { error_type: string; count: number }[];
  recent_runs: Run[];
}

export interface CommercialConfigStatusPayload {
  provider: string;
  operation: string;
  status: string;
  configured: boolean;
  sources: {
    entitlements?: Record<string, unknown>;
    retention?: Record<string, unknown>;
  };
  entitlements: {
    schema_version?: string;
    edition?: string;
    billing_provider?: string;
    billing_calls_enabled: boolean;
    enabled_capabilities: string[];
    disabled_capabilities: string[];
  };
  retention: {
    schema_version?: string;
    windows?: {
      free_local_days?: number;
      pro_workspace_days?: number;
      max_retention_days?: number;
    };
    cleanup_approval_required: boolean;
    legal_hold_required_before_cleanup: boolean;
    cleanup_execution_enabled: boolean;
    legal_hold_registry_configured: boolean;
    legal_hold_registry_example_only: boolean;
  };
  safety: {
    read_only: boolean;
    live_execution_performed: boolean;
    billing_call_performed: boolean;
    cleanup_execution_performed: boolean;
    raw_config_omitted: boolean;
    token_omitted: boolean;
  };
  failures: string[];
}

export interface LocalBriefResult {
  provider: string;
  workflow: string;
  dry_run: boolean;
  ok?: boolean;
  run_id?: string;
  task_id?: string;
  artifact_id?: string | null;
  duration_ms?: number;
  output_summary?: string;
  error?: string | null;
  note?: string;
  state_preview?: {
    agents_total?: number;
    pending_approvals?: number;
    openclaw_cron_runs?: number;
    recent_real_runs?: number;
  };
}

export interface CustomerTaskWorkflowInput {
  title: string;
  description: string;
  acceptance_criteria: string;
  priority: string;
  risk_level: string;
  owner_agent_id?: string;
  selected_agent_ids: string[];
  template_id?: string;
  workflow_kind?: string;
  confirm_run?: boolean;
}

export interface CustomerWorkerTaskWorkflowInput extends CustomerTaskWorkflowInput {
  adapter?: "mock" | "hermes" | "openclaw";
}

export interface CustomerTaskWorkflowResult {
  provider: string;
  workflow: string;
  dry_run: boolean;
  ok?: boolean;
  adapter?: string;
  agent_id?: string;
  task_id: string;
  run_id?: string;
  artifact_id?: string | null;
  approval_id?: string | null;
  plan_id?: string | null;
  plan_evidence_manifest_id?: string | null;
  plan_evidence_status?: string | null;
  plan_evidence_pass?: boolean;
  evaluation_case_result?: Record<string, unknown> | null;
  duration_ms?: number;
  output_summary?: string;
  error?: string | null;
  reason?: string;
  readiness?: string;
  recommended_action?: string;
  note?: string;
  requires?: Record<string, unknown>;
  selected_agent_ids?: string[];
  evidence?: {
    tool_calls?: number;
    evaluations?: number;
    runtime_events?: number;
    audit_logs?: number;
    artifacts?: number;
    memories?: number;
    approvals?: number;
  };
}

export interface KbBotProjectWorkflowResult {
  provider: string;
  workflow: string;
  dry_run: boolean;
  ok: boolean;
  project_id?: string;
  task_id?: string;
  run_id?: string;
  artifact_id?: string | null;
  approval_ids?: string[];
  results?: {
    task_id: string;
    run_id: string;
    agent_id: string;
    approval_id?: string | null;
    artifact_id?: string | null;
    evaluation_id?: string;
    memory_id?: string;
  }[];
  safe_defaults?: Record<string, unknown>;
  open_pages?: Record<string, string>;
  report_url?: string;
  error?: string | null;
}

export interface CustomerProjectReportArtifactResult {
  ok: boolean;
  created: boolean;
  project_id: string;
  report_url: string;
  content_hash: string;
  raw_report_omitted: boolean;
  token_omitted: boolean;
  artifact?: {
    artifact_id: string;
    task_id?: string | null;
    run_id?: string | null;
    artifact_type: string;
    title: string;
    uri: string;
    summary: string;
    created_at: string;
  };
  safe_defaults?: Record<string, unknown>;
  error?: string | null;
}

export interface CustomerProjectReportPayload {
  project_id: string;
  status: string;
  markdown: string;
  counts: {
    tasks?: number;
    runs?: number;
    completed_runs?: number;
    failed_runs?: number;
    tool_calls?: number;
    approvals?: number;
    pending_approvals?: number;
    evaluations?: number;
    memories?: number;
    artifacts?: number;
  };
  artifact_id?: string | null;
  report_artifact_id?: string | null;
  approval_ids?: string[];
  internal_evidence?: {
    visibility?: string;
    not_customer_report?: boolean;
    task_ids?: string[];
    run_ids?: string[];
    artifact_ids?: string[];
    delivery_artifact_id?: string | null;
    report_artifact_id?: string | null;
    approval_ids?: string[];
    counts?: Record<string, number>;
    operator_routes?: Record<string, string>;
  };
  report_boundary?: {
    customer_markdown_excludes_internal_evidence?: boolean;
    internal_evidence_separated?: boolean;
    raw_prompts_omitted?: boolean;
    raw_model_responses_omitted?: boolean;
    private_transcripts_omitted?: boolean;
    credentials_omitted?: boolean;
    raw_documents_omitted?: boolean;
    customer_visible_sections?: string[];
    internal_only_sections?: string[];
  };
  safe_defaults?: Record<string, unknown>;
  error?: string | null;
}

export interface CustomerProjectSummary {
  project_id: string;
  title: string;
  status: string;
  task_count: number;
  completed_tasks: number;
  failed_or_blocked_tasks: number;
  run_count: number;
  completed_runs: number;
  pending_approvals: number;
  artifact_count: number;
  last_task_id?: string | null;
  last_run_id?: string | null;
  delivery_artifact_id?: string | null;
  report_artifact_id?: string | null;
  approval_ids?: string[];
  created_at?: string;
  updated_at?: string;
  report_url: string;
  ui_report_url: string;
  safe_defaults?: Record<string, unknown>;
}

export interface CustomerProjectIndexPayload {
  projects: CustomerProjectSummary[];
  total: number;
  limit: number;
  safe_defaults?: Record<string, unknown>;
}

export interface CustomerDeliveryBoardItem {
  delivery_id: string;
  status: string;
  title: string;
  task_id?: string | null;
  run_id?: string | null;
  artifact_id?: string | null;
  artifact_type?: string | null;
  project_id?: string | null;
  owner_agent_id?: string | null;
  run_status?: string | null;
  task_status?: string | null;
  priority?: string | null;
  risk_level?: string | null;
  summary?: string;
  created_at?: string;
  report_url?: string | null;
  ui_report_url?: string | null;
  task_url?: string | null;
  run_url?: string | null;
  artifact_url?: string | null;
  artifact_link?: {
    artifact_id?: string | null;
    artifact_type?: string | null;
    uri?: string | null;
    url?: string | null;
    api_url?: string | null;
  };
  approval_ids?: string[];
  pending_approval_ids?: string[];
  approval_links?: {
    approval_id?: string | null;
    decision?: string | null;
    url?: string | null;
    created_at?: string | null;
  }[];
  tool_call_ids?: string[];
  tool_call_links?: {
    tool_call_id?: string | null;
    run_id?: string | null;
    tool_name?: string | null;
    status?: string | null;
    url?: string | null;
  }[];
  evaluation_ids?: string[];
  evaluation_links?: {
    evaluation_id?: string | null;
    run_id?: string | null;
    pass_fail?: string | null;
    score?: number | null;
    url?: string | null;
  }[];
  audit_ids?: string[];
  audit_links?: {
    audit_id?: string | null;
    action?: string | null;
    entity_type?: string | null;
    entity_id?: string | null;
    url?: string | null;
  }[];
  evaluation_summary?: {
    count?: number;
    failed?: number;
    latest_score?: number | null;
    latest_pass_fail?: string | null;
  };
  delivery_approval_gate?: {
    required?: boolean;
    pass?: boolean;
    status?: string;
    manifest_id?: string | null;
    message?: string;
    evidence_counts?: Record<string, number>;
    failed_checks?: string[];
    token_omitted?: boolean;
    verification?: {
      status?: string;
      failed_checks?: { id?: string; message?: string; ok?: boolean }[];
      evidence_counts?: Record<string, number>;
    };
  };
  evidence?: Record<string, number>;
  next_action?: string;
}

export interface CustomerDeliveryBoardPayload {
  provider: string;
  operation: string;
  status: string;
  summary: {
    deliveries: number;
    ready: number;
    waiting_approval: number;
    in_progress: number;
    needs_attention: number;
    pending_approvals: number;
    artifacts: number;
    verified_plan_evidence_manifests?: number;
    missing_plan_evidence_manifests?: number;
  };
  deliveries: CustomerDeliveryBoardItem[];
  gates: { id: string; label: string; ok: boolean; value?: string | number | boolean }[];
  next_actions: string[];
  safety: {
    read_only: boolean;
    ledger_mutated: boolean;
    live_execution_performed: boolean;
    raw_prompt_omitted: boolean;
    raw_response_omitted: boolean;
    token_omitted: boolean;
  };
  token_omitted?: boolean;
}

export interface HermesOpenClawLoopReadbackPayload {
  provider: string;
  operation: string;
  loop_id?: string | null;
  status: string;
  runs: Record<string, unknown>[];
  tasks: Record<string, unknown>[];
  artifacts: Record<string, unknown>[];
  agent_plans: Record<string, unknown>[];
  plan_evidence_manifests: Record<string, unknown>[];
  audit_logs?: Record<string, unknown>[];
  summary: {
    runs?: number;
    tasks?: number;
    artifacts?: number;
    agent_plans?: number;
    plan_evidence_manifests?: number;
    verified_plan_evidence_manifests?: number;
    blocked_plan_evidence_manifests?: number;
    failed_runs?: number;
  };
  token_omitted?: boolean;
}

export interface HermesOpenClawLoopWorkflowResult {
  provider: string;
  workflow: string;
  ok?: boolean;
  loop_id?: string;
  mode?: string;
  rounds?: number;
  agents?: string[];
  duration_ms?: number;
  log_path?: string;
  audit_path?: string;
  next_action_artifact_path?: string;
  next_action_artifact?: Record<string, unknown>;
  runtime_dir_gitignored?: boolean;
  mis_ledger?: {
    ok?: boolean;
    parent_task_id?: string;
    parent_run_id?: string;
    child_task_ids?: string[];
    child_run_ids?: string[];
    plan_ids?: string[];
    plan_evidence_manifest_ids?: string[];
    verified_plan_evidence_manifest_ids?: string[];
    blocked_plan_evidence_manifest_ids?: string[];
    artifact_id?: string;
    artifact_ids?: string[];
    token_omitted?: boolean;
    raw_omitted?: boolean;
  };
  outputs?: Record<string, unknown>[];
  stderr_summary?: string | null;
  token_omitted?: boolean;
  raw_omitted?: boolean;
}

export interface CustomerTaskTemplate {
  template_id: string;
  name: string;
  name_en?: string;
  workflow: string;
  scenario: string;
  status: string;
  risk_level: string;
  priority: string;
  description: string;
  default_title: string;
  default_description: string;
  default_acceptance: string;
  agent_roles: string[];
  required_approvals: string[];
  safe_defaults: Record<string, unknown>;
  entrypoint: string;
}

export interface CustomerTaskTemplateListPayload {
  templates: CustomerTaskTemplate[];
  safe_defaults: Record<string, unknown>;
}

export interface WorkflowJob {
  job_id: string;
  workspace_id?: string;
  workflow_type: string;
  status: "queued" | "running" | "completed" | "failed" | string;
  template_id?: string | null;
  adapter?: string | null;
  confirm_run?: boolean;
  title?: string | null;
  input_summary?: string | null;
  request_hash?: string | null;
  result_task_id?: string | null;
  result_run_id?: string | null;
  result_artifact_id?: string | null;
  error_message?: string | null;
  created_at?: string;
  started_at?: string | null;
  completed_at?: string | null;
  updated_at?: string;
  result?: Partial<CustomerTaskWorkflowResult & KbBotProjectWorkflowResult>;
  raw_request_omitted?: boolean;
  token_omitted?: boolean;
  age_sec?: number;
  threshold_sec?: number;
  stuck_reason?: string;
}

export interface WorkflowJobSubmitPayload {
  ok: boolean;
  provider: string;
  job_id: string;
  status_url: string;
  job: WorkflowJob;
  raw_request_omitted: boolean;
  token_omitted: boolean;
}

export interface WorkflowJobListPayload {
  jobs: WorkflowJob[];
  token_omitted?: boolean;
}

export interface WorkflowJobStuckPayload {
  provider: string;
  threshold_sec: number;
  stuck_jobs: WorkflowJob[];
  token_omitted?: boolean;
}

export interface WorkflowJobMarkFailedPayload {
  ok: boolean;
  provider?: string;
  job_id: string;
  job?: WorkflowJob;
  marked_failed?: boolean;
  reason?: string;
  token_omitted?: boolean;
}

export interface WorkerStatusPayload {
  provider: string;
  status: string;
  worker_count: number;
  running_workers: number;
  active_service_workers: number;
  stale_service_workers: number;
  execution_capacity_workers: number;
  recent_completed_runs: number;
  pending_worker_tasks: number;
  stuck_worker_tasks: number;
  stuck_workflow_jobs?: number;
  remote_worker_count: number;
  total_remote_enrollments: number;
  active_remote_enrollments: number;
  fresh_remote_enrollments: number;
  stale_remote_enrollments: number;
  never_seen_remote_enrollments: number;
  active_remote_sessions: number;
  remote_worker_health: Record<string, unknown>;
  service_workers: Record<string, unknown>[];
  adapter_readiness?: WorkerAdapterReadinessSummary;
  fleet_health?: WorkerFleetHealth;
  daemons: WorkerDaemonStatus[];
  workers: Agent[];
  recent_runs: Run[];
  recent_tasks: Task[];
  stuck_tasks: StuckWorkerTask[];
  stuck_workflow_job_refs?: {
    job_id: string;
    workflow_type?: string;
    status?: string;
    age_sec?: number;
    stuck_reason?: string;
  }[];
  recent_events: Record<string, unknown>[];
}

export interface WorkerFleetLane {
  lane_id: string;
  lane_type: string;
  adapter?: string | null;
  agent_id?: string | null;
  agent_name?: string | null;
  workspace_id?: string | null;
  runtime_type?: string | null;
  status: string;
  health: string;
  heartbeat_state?: string | null;
  session_state?: string | null;
  active_session_count: number;
  last_seen_at?: string | null;
  expires_at?: string | null;
  scope_count?: number;
  workload?: Record<string, unknown>;
  next_action?: string;
  safe_ref?: string | null;
  management_mode?: string | null;
  control_allowed?: boolean;
  process_state_verified?: boolean;
  token_omitted?: boolean;
  session_id_omitted?: boolean;
  token_id_omitted?: boolean;
}

export interface WorkerFleetPayload {
  provider: string;
  operation: string;
  status: string;
  summary: {
    lane_count: number;
    lane_counts: Record<string, number>;
    health_counts: Record<string, number>;
    local_daemon_count: number;
    running_local_daemons: number;
    active_service_workers: number;
    stale_service_workers: number;
    execution_capacity_workers: number;
    host_managed_workers?: number;
    api_managed_daemons?: number;
    remote_worker_count: number;
    fresh_remote_enrollments: number;
    stale_remote_enrollments: number;
    never_seen_remote_enrollments: number;
    active_remote_sessions: number;
    stuck_worker_tasks: number;
    stuck_workflow_jobs: number;
    recommended_adapter?: string;
  };
  lanes: WorkerFleetLane[];
  next_actions: string[];
  contract?: string;
  safety: {
    read_only: boolean;
    live_execution_performed: boolean;
    token_omitted: boolean;
    session_id_omitted: boolean;
    raw_prompt_omitted: boolean;
  };
  token_omitted: boolean;
  live_execution_performed: boolean;
}

export type WorkerAdapterName = "mock" | "hermes" | "openclaw";
export type WorkerReadinessAdapterName = WorkerAdapterName | "codex";

export interface WorkerAdapterReadinessSummary {
  ready_adapters?: WorkerReadinessAdapterName[];
  live_ready_adapters?: WorkerReadinessAdapterName[];
  review_required_adapters?: WorkerReadinessAdapterName[];
  blocked_adapters?: WorkerReadinessAdapterName[];
  unavailable_adapters?: WorkerReadinessAdapterName[];
  recommended_adapter?: WorkerReadinessAdapterName;
}

export interface WorkerAdapterReadinessItem {
  adapter: WorkerReadinessAdapterName;
  ok: boolean;
  readiness: "ready" | "review_required" | "blocked" | "unavailable" | string;
  connector_id?: string | null;
  trust_status?: string;
  observation_level?: string;
  capability_policy_hash?: string | null;
  capability_manifest?: Record<string, unknown>;
  risk_floor?: string;
  commercial_readiness?: string;
  requires_confirm_run?: boolean;
  workspace_write_ready?: boolean;
  client_plugin?: {
    package_name?: string | null;
    package_version?: string | null;
    packaged?: boolean;
    skill_available?: boolean;
    marketplace_available?: boolean;
    mcp_tools_available?: boolean;
    connection_mode?: string;
    raw_path_omitted?: boolean;
    token_omitted?: boolean;
  };
  target_resource?: string | null;
  checks?: Record<string, unknown>;
  recommended_action?: string;
  last_error?: string | null;
  remediation?: {
    status?: string;
    primary_next_action?: string;
    missing?: string[];
    commands?: Array<{
      phase?: string;
      command?: string;
      mutating?: boolean;
      confirm_required?: boolean;
    }>;
    safety?: {
      read_only?: boolean;
      ledger_mutated?: boolean;
      live_execution_performed?: boolean;
      server_executes_shell?: boolean;
      token_omitted?: boolean;
    };
    token_omitted?: boolean;
  };
  token_omitted?: boolean;
}

export interface WorkerAdapterReadinessPayload {
  provider: string;
  status: "ready" | "degraded" | "blocked" | string;
  summary: WorkerAdapterReadinessSummary;
  adapters: Record<WorkerReadinessAdapterName, WorkerAdapterReadinessItem>;
  contract?: string;
  live_execution_performed: boolean;
  token_omitted?: boolean;
}

export interface WorkerFleetGate {
  id: string;
  status: string;
  summary: string;
  action?: string;
}

export interface WorkerFleetHealth {
  overall: string;
  contract?: string;
  gates: WorkerFleetGate[];
  recommended_actions: string[];
  remote_status?: string;
  token_omitted?: boolean;
}

export interface LocalReadinessGate {
  id: string;
  label: string;
  ok: boolean;
  status: string;
  detail: string;
  next_action: string;
}

export interface LocalReadinessEvidence {
  tasks: number;
  planned_tasks: number;
  completed_tasks: number;
  runs: number;
  completed_runs: number;
  tool_calls: number;
  evaluations: number;
  audit_logs: number;
  artifacts: number;
  memories: number;
  memory_candidates: number;
  approved_memories: number;
  pending_approvals: number;
  approvals: number;
  workflow_jobs: number;
  customer_worker_artifacts: number;
  closed_loop_runs: number;
  commander_synthesis_artifacts: number;
  commander_synthesis_pending_reviews: number;
  commander_synthesis_approved_reviews: number;
  commander_synthesis_promoted_memories: number;
  commander_synthesis_promoted_deliveries: number;
  has_task_run_tool_eval_audit_artifact_chain: boolean;
  has_memory_or_knowledge: boolean;
  has_approval_flow: boolean;
}

export interface LocalHarnessProofAttempt {
  run_id?: string | null;
  task_id?: string | null;
  artifact_id?: string | null;
  plan_evidence_manifest_id?: string | null;
  agent_id?: string | null;
  runtime_type?: string | null;
  run_status?: string | null;
  task_status?: string | null;
  task_title?: string | null;
  checked_at?: string | null;
  age_hours?: number | null;
  evidence_class?: string;
  real_runtime_proof: boolean;
  mock_or_ci_fallback: boolean;
  pass: boolean;
  token_omitted: boolean;
}

export interface LocalHarnessReceiptStatus {
  operation: string;
  adapter: WorkerAdapterName;
  required: boolean;
  status: string;
  underlying_status: string;
  match: string;
  current: boolean;
  recorded: boolean;
  verified: boolean;
  receipt_id?: string | null;
  audit_id?: string | null;
  action_id?: string | null;
  action_signature?: string | null;
  receipt_action_signature?: string | null;
  source?: string | null;
  receipt_hash?: string | null;
  created_at?: string | null;
  evaluation_id?: string | null;
  evaluation_pass_fail?: string | null;
  control_readback_attached: boolean;
  control_readback_id?: string | null;
  control_readback_hash?: string | null;
  readback_command: string;
  receipt_presence_is_runtime_success: boolean;
  live_runtime_success_proven: boolean;
  proof_boundary?: string;
  read_only: boolean;
  ledger_mutated: boolean;
  live_execution_performed: boolean;
  raw_prompt_omitted: boolean;
  raw_response_omitted: boolean;
  token_omitted: boolean;
}

export interface LocalHarnessReceiptSummary {
  operation: string;
  readback_command: string;
  recorded_current: number;
  verified_current: number;
  missing: number;
  stale: number;
  current: number;
  receipt_presence_is_runtime_success: boolean;
  read_only: boolean;
  ledger_mutated: boolean;
  live_execution_performed: boolean;
  token_omitted: boolean;
}

export interface LocalHarnessProofAdapter {
  adapter: WorkerAdapterName;
  status: string;
  ok: boolean;
  evidence_class: string;
  real_runtime_adapter: boolean;
  freshness_hours: number;
  latest_attempt?: LocalHarnessProofAttempt | null;
  latest_passing?: LocalHarnessProofAttempt | null;
  next_action: string;
  governed_launch?: {
    operation: string;
    adapter: WorkerAdapterName;
    preview_command: string;
    confirmed_command: string;
    confirm_required: boolean;
    writes_ledger: boolean;
    live_execution: boolean;
    entrypoint: string;
    evidence_readback_command: string;
    receipt_preview_command?: string;
    receipt_record_command?: string;
    receipt_readback_command?: string;
    action_signature?: string;
    receipt_status?: LocalHarnessReceiptStatus;
    approval_boundary?: string;
    raw_prompt_omitted: boolean;
    raw_response_omitted: boolean;
    token_omitted: boolean;
  };
  token_omitted: boolean;
}

export interface LocalHarnessProofReadiness {
  provider: string;
  operation: string;
  status: string;
  ok: boolean;
  workspace_id?: string;
  freshness_hours: number;
  adapters: Record<WorkerAdapterName, LocalHarnessProofAdapter>;
  summary: {
    fresh: number;
    fresh_real_runtime_adapters: number;
    fresh_mock_fallback: number;
    stale: number;
    missing: number;
    latest_failed: number;
    latest_incomplete: number;
    launch_receipts_recorded_current?: number;
    launch_receipts_missing?: number;
    launch_receipts_stale?: number;
  };
  commands: Record<string, string>;
  governed_launch_packet?: {
    operation: string;
    status: string;
    preferred_entrypoint: string;
    readback_command: string;
    receipt_summary?: LocalHarnessReceiptSummary;
    contract?: string;
    safety: {
      read_only: boolean;
      ledger_mutated: boolean;
      live_execution_performed: boolean;
      raw_prompt_omitted: boolean;
      raw_response_omitted: boolean;
      token_omitted: boolean;
    };
  };
  contract?: string;
  safety: {
    read_only: boolean;
    ledger_mutated: boolean;
    live_execution_performed: boolean;
    raw_prompt_omitted: boolean;
    raw_response_omitted: boolean;
    token_omitted: boolean;
  };
  token_omitted: boolean;
  live_execution_performed: boolean;
}

export interface LocalRunPathStep {
  step_id: string;
  label: string;
  phase: string;
  status: string;
  adapter?: WorkerAdapterName;
  command: string;
  verify_command?: string | null;
  route?: string | null;
  detail?: string;
  mutating: boolean;
  confirm_required: boolean;
  writes_ledger: boolean;
  live_execution: boolean;
  service_control_preview?: boolean;
  copy_only?: boolean;
  server_executes_shell?: boolean;
  receipt_required?: boolean;
  control_readback_required?: boolean;
  receipt_command?: string | null;
  receipt_record_command?: string | null;
  receipt_verify_record_command?: string | null;
  receipt_state?: Record<string, unknown>;
  action_signature?: string | null;
  source?: string | null;
  token_omitted?: boolean;
}

export interface CommanderSynthesisLifecyclePayload {
  status: string;
  summary: {
    synthesis_artifacts: number;
    pending_reviews: number;
    approved_reviews: number;
    rejected_reviews: number;
    promoted_memory_candidates: number;
    promoted_delivery_artifacts: number;
  };
  recent: Record<string, unknown>[];
  next_actions: string[];
  safety: {
    read_only: boolean;
    ledger_mutated: boolean;
    live_execution_performed: boolean;
    token_omitted: boolean;
  };
  token_omitted: boolean;
  live_execution_performed: boolean;
}

export interface LocalReadinessPayload {
  provider: string;
  operation: string;
  status: "ready" | "attention" | "blocked" | string;
  ok: boolean;
  workspace_id?: string;
  gates: LocalReadinessGate[];
  evidence: LocalReadinessEvidence;
  next_actions: string[];
  local_run_path?: LocalRunPathStep[];
  contract?: string;
  adapter_readiness: WorkerAdapterReadinessSummary;
  local_harness_proof_readiness?: LocalHarnessProofReadiness;
  commander_synthesis_lifecycle?: CommanderSynthesisLifecyclePayload;
  token_omitted: boolean;
  live_execution_performed: boolean;
}

export interface IntegrationInboxSummary {
  ready_for_review: number;
  still_running: number;
  blocked: number;
  late_or_stale: number;
  needs_memory_review: number;
  total: number;
}

export interface IntegrationInboxItem {
  item_id: string;
  bucket: string;
  title: string;
  status: string;
  task_id?: string | null;
  run_id?: string | null;
  job_id?: string | null;
  artifact_id?: string | null;
  agent_id?: string | null;
  owner_agent_id?: string | null;
  age_sec: number;
  evidence?: Record<string, unknown>;
  integration_decision?: {
    decision: string;
    status: string;
    reason: string;
    required_review: boolean;
    can_advance_without_waiting: boolean;
    evidence_complete: boolean;
    pending_approval: boolean;
    safe_to_auto_apply: boolean;
    ledger_decision_required: boolean;
    next_command?: string;
  };
  recommended_action?: string;
  created_at?: string;
  updated_at?: string;
}

export interface IntegrationInboxPayload {
  provider: string;
  operation: string;
  status: string;
  filter?: {
    bucket: string;
    limit: number;
    threshold_sec: number;
  };
  token_omitted: boolean;
  live_execution_performed: boolean;
  summary: IntegrationInboxSummary;
  inbox_items: IntegrationInboxItem[];
  recommended_next_actions: string[];
  safety: {
    read_only: boolean;
    ledger_mutated: boolean;
    raw_prompt_omitted: boolean;
  };
}

export interface IntegrationInboxOptions {
  bucket?: string;
  limit?: number;
  threshold_sec?: number;
}

export interface CommanderWorkPackage {
  plan_id: string;
  project_id: string;
  lane_id: string;
  task_id: string;
  title: string;
  description: string;
  owner_agent_id: string;
  collaborator_agent_ids: string[];
  status: string;
  priority: string;
  risk_level: string;
  acceptance_criteria: string;
  dependencies: string[];
  verification_commands: string[];
  scope: string;
  avoid_scope: string;
}

export interface CommanderWorkPackagePlanPayload {
  provider: string;
  operation: string;
  status: string;
  ok: boolean;
  workspace_id: string;
  project_id: string;
  plan_id: string;
  goal_summary: string;
  confirm_create: boolean;
  created: boolean;
  created_count: number;
  planned_count: number;
  work_packages: CommanderWorkPackage[];
  created_task_ids: string[];
  errors: { lane_id?: string; task_id?: string; error?: string; message?: string }[];
  recommended_next_actions: string[];
  safety: {
    live_execution_performed: boolean;
    token_omitted: boolean;
    raw_prompt_omitted: boolean;
    dry_run: boolean;
    ledger_mutated: boolean;
    task_created: boolean;
  };
  token_omitted: boolean;
  live_execution_performed: boolean;
}

export interface CommanderWorkPackageReadbackPayload {
  provider: string;
  operation: string;
  status: string;
  workspace_id: string;
  filter: {
    project_id?: string | null;
    plan_id?: string | null;
    status: string;
    limit: number;
  };
  summary: {
    total: number;
    by_status: Record<string, number>;
    by_project: Record<string, number>;
  };
  work_packages: (CommanderWorkPackage & {
    work_package_id: string;
    package_status: string;
    latest_run?: {
      run_id?: string;
      status?: string;
      agent_id?: string;
      runtime_type?: string;
      created_at?: string;
      ended_at?: string | null;
      error_type?: string | null;
      error_message?: string | null;
    } | null;
    latest_workflow_job?: CommanderTeamBoardLane["latest_workflow_job"];
    evidence_counts?: Record<string, number>;
    recommended_action?: string;
    created_at?: string;
    updated_at?: string;
  })[];
  recommended_next_actions: string[];
  safety: {
    read_only: boolean;
    ledger_mutated: boolean;
    task_created: boolean;
    run_created: boolean;
    live_execution_performed: boolean;
    token_omitted: boolean;
    raw_prompt_omitted: boolean;
  };
  token_omitted: boolean;
  live_execution_performed: boolean;
}

export interface CommanderTeamBoardLane {
  task_id: string;
  lane_id: string;
  title: string;
  owner_agent_id: string;
  collaborator_agent_ids: string[];
  status: string;
  package_status: string;
  priority: string;
  risk_level: string;
  dependencies: string[];
  dependency_count: number;
  latest_run?: {
    run_id?: string;
    status?: string;
    created_at?: string;
  } | null;
  latest_workflow_job?: {
    job_id?: string;
    workflow_type?: string;
    status?: string;
    adapter?: string;
    confirm_run?: boolean;
    result_run_id?: string | null;
    result_artifact_id?: string | null;
    created_at?: string;
    started_at?: string | null;
    completed_at?: string | null;
    updated_at?: string;
  } | null;
  evidence_counts: Record<string, number>;
  localization_gate: Record<string, unknown>;
  coding_evidence_gate: Record<string, unknown>;
  recommended_action?: string;
}

export interface CommanderTeamBoardPayload {
  status: string;
  workspace_id: string;
  project_id?: string | null;
  plan_id?: string | null;
  summary: {
    total_lanes: number;
    status_counts: Record<string, number>;
    owner_counts: Record<string, number>;
    ready_for_review: number;
    blocked: number;
    missing_coding_evidence: number;
    dependency_edges: number;
    workflow_job_counts: Record<string, number>;
    active_workflow_jobs: number;
    failed_workflow_jobs: number;
  };
  lanes: CommanderTeamBoardLane[];
  dependency_edges: { from_task_id: string; to_task_id: string; known_in_board: boolean }[];
  ready_for_review_task_ids: string[];
  blocked_task_ids: string[];
  missing_coding_evidence_task_ids: string[];
  active_workflow_job_task_ids: string[];
  failed_workflow_job_task_ids: string[];
  next_actions: string[];
  safety: {
    read_only: boolean;
    ledger_mutated: boolean;
    live_execution_performed: boolean;
    token_omitted: boolean;
    raw_prompt_omitted: boolean;
    raw_source_omitted: boolean;
  };
  token_omitted: boolean;
  live_execution_performed: boolean;
}

export interface CommanderProjectBoardPayload {
  provider: string;
  operation: string;
  status: string;
  workspace_id: string;
  counts: Record<string, unknown>;
  team_board?: CommanderTeamBoardPayload | null;
  team_board_filter: {
    project_id?: string | null;
    plan_id?: string | null;
    limit: number;
    applied: boolean;
  };
  team_work_packages_summary?: CommanderWorkPackageReadbackPayload["summary"] | null;
  integration_gates: { id: string; status: string; summary?: string; next_action?: string }[];
  recommended_next_actions: string[];
  safety: {
    read_only: boolean;
    ledger_mutated: boolean;
    task_created: boolean;
    run_created: boolean;
    job_created: boolean;
    token_omitted: boolean;
    raw_prompt_omitted: boolean;
  };
  token_omitted: boolean;
  live_execution_performed: boolean;
}

export interface CommanderWorkPackageDispatchPayload {
  provider: string;
  operation: string;
  ok: boolean;
  dry_run: boolean;
  adapter: string;
  task_id: string;
  agent_id?: string | null;
  run_id?: string | null;
  work_package?: CommanderWorkPackageReadbackPayload["work_packages"][number] | null;
  evidence?: Record<string, number>;
  duration_ms?: number | null;
  error?: string | null;
  reason?: string | null;
  requires?: Record<string, boolean>;
  safety: {
    ledger_mutated: boolean;
    run_created: boolean;
    live_execution_performed: boolean;
    token_omitted: boolean;
    raw_prompt_omitted: boolean;
  };
  token_omitted: boolean;
  live_execution_performed: boolean;
}

export interface CommanderWorkPackageDispatchBatchPayload {
  provider: string;
  operation: string;
  ok: boolean;
  status?: string;
  adapter: string;
  confirm_run: boolean;
  jobs: WorkflowJob[];
  job_ids: string[];
  task_ids: string[];
  status_urls: string[];
  reason?: string | null;
  filter?: {
    project_id?: string | null;
    plan_id?: string | null;
    status?: string;
    limit?: number;
  };
  team_board_after_queue?: CommanderTeamBoardPayload | null;
  safety: {
    ledger_mutated: boolean;
    jobs_created: number;
    live_execution_performed: boolean;
    token_omitted: boolean;
    raw_prompt_omitted: boolean;
  };
  token_omitted: boolean;
  live_execution_performed: boolean;
}

export interface CommanderWorkPackageSynthesisPayload {
  provider: string;
  operation: string;
  ok: boolean;
  status: string;
  workspace_id: string;
  project_id?: string;
  plan_id?: string;
  artifact_id?: string | null;
  approval_id?: string | null;
  review_approval?: Record<string, unknown> | null;
  markdown?: string;
  content_hash?: string;
  package_count?: number;
  packages?: CommanderWorkPackageReadbackPayload["work_packages"];
  evidence_totals?: Record<string, number>;
  safety: {
    ledger_mutated: boolean;
    artifact_created: boolean;
    live_execution_performed: boolean;
    token_omitted: boolean;
    raw_prompt_omitted: boolean;
  };
  token_omitted: boolean;
  live_execution_performed: boolean;
}

function parseCommanderTeamBoardPayload(team: unknown, fallbackWorkspaceId = "local-demo"): CommanderTeamBoardPayload | null {
  if (typeof team !== "object" || team === null) return null;
  const teamRaw = team as Record<string, unknown>;
  const summaryRaw = typeof teamRaw.summary === "object" && teamRaw.summary !== null ? teamRaw.summary as Record<string, unknown> : {};
  const teamSafetyRaw = typeof teamRaw.safety === "object" && teamRaw.safety !== null ? teamRaw.safety as Record<string, unknown> : {};
  return {
    status: String(teamRaw.status || "unknown"),
    workspace_id: String(teamRaw.workspace_id || fallbackWorkspaceId || "local-demo"),
    project_id: teamRaw.project_id ? String(teamRaw.project_id) : null,
    plan_id: teamRaw.plan_id ? String(teamRaw.plan_id) : null,
    summary: {
      total_lanes: numberValue(summaryRaw.total_lanes, 0),
      status_counts: numberRecord(summaryRaw.status_counts),
      owner_counts: numberRecord(summaryRaw.owner_counts),
      ready_for_review: numberValue(summaryRaw.ready_for_review, 0),
      blocked: numberValue(summaryRaw.blocked, 0),
      missing_coding_evidence: numberValue(summaryRaw.missing_coding_evidence, 0),
      dependency_edges: numberValue(summaryRaw.dependency_edges, 0),
      workflow_job_counts: numberRecord(summaryRaw.workflow_job_counts),
      active_workflow_jobs: numberValue(summaryRaw.active_workflow_jobs, 0),
      failed_workflow_jobs: numberValue(summaryRaw.failed_workflow_jobs, 0),
    },
    lanes: asArray<Record<string, unknown>>(teamRaw.lanes).map((lane) => {
      const latestRun = typeof lane.latest_run === "object" && lane.latest_run !== null ? lane.latest_run as Record<string, unknown> : null;
      const latestWorkflowJob = typeof lane.latest_workflow_job === "object" && lane.latest_workflow_job !== null ? lane.latest_workflow_job as Record<string, unknown> : null;
      return {
        task_id: String(lane.task_id || ""),
        lane_id: String(lane.lane_id || ""),
        title: String(lane.title || "Untitled lane"),
        owner_agent_id: String(lane.owner_agent_id || ""),
        collaborator_agent_ids: asArray<unknown>(lane.collaborator_agent_ids).map(String),
        status: String(lane.status || "unknown"),
        package_status: String(lane.package_status || lane.status || "unknown"),
        priority: String(lane.priority || "medium"),
        risk_level: String(lane.risk_level || "medium"),
        dependencies: asArray<unknown>(lane.dependencies).map(String),
        dependency_count: numberValue(lane.dependency_count, 0),
        latest_run: latestRun ? {
          run_id: latestRun.run_id ? String(latestRun.run_id) : undefined,
          status: latestRun.status ? String(latestRun.status) : undefined,
          created_at: latestRun.created_at ? String(latestRun.created_at) : undefined,
        } : null,
        latest_workflow_job: latestWorkflowJob ? {
          job_id: latestWorkflowJob.job_id ? String(latestWorkflowJob.job_id) : undefined,
          workflow_type: latestWorkflowJob.workflow_type ? String(latestWorkflowJob.workflow_type) : undefined,
          status: latestWorkflowJob.status ? String(latestWorkflowJob.status) : undefined,
          adapter: latestWorkflowJob.adapter ? String(latestWorkflowJob.adapter) : undefined,
          confirm_run: boolValue(latestWorkflowJob.confirm_run),
          result_run_id: latestWorkflowJob.result_run_id ? String(latestWorkflowJob.result_run_id) : null,
          result_artifact_id: latestWorkflowJob.result_artifact_id ? String(latestWorkflowJob.result_artifact_id) : null,
          created_at: latestWorkflowJob.created_at ? String(latestWorkflowJob.created_at) : undefined,
          started_at: latestWorkflowJob.started_at ? String(latestWorkflowJob.started_at) : null,
          completed_at: latestWorkflowJob.completed_at ? String(latestWorkflowJob.completed_at) : null,
          updated_at: latestWorkflowJob.updated_at ? String(latestWorkflowJob.updated_at) : undefined,
        } : null,
        evidence_counts: numberRecord(lane.evidence_counts),
        localization_gate: typeof lane.localization_gate === "object" && lane.localization_gate !== null ? lane.localization_gate as Record<string, unknown> : {},
        coding_evidence_gate: typeof lane.coding_evidence_gate === "object" && lane.coding_evidence_gate !== null ? lane.coding_evidence_gate as Record<string, unknown> : {},
        recommended_action: lane.recommended_action ? String(lane.recommended_action) : undefined,
      };
    }),
    dependency_edges: asArray<Record<string, unknown>>(teamRaw.dependency_edges).map((edge) => ({
      from_task_id: String(edge.from_task_id || ""),
      to_task_id: String(edge.to_task_id || ""),
      known_in_board: boolValue(edge.known_in_board),
    })),
    ready_for_review_task_ids: asArray<unknown>(teamRaw.ready_for_review_task_ids).map(String),
    blocked_task_ids: asArray<unknown>(teamRaw.blocked_task_ids).map(String),
    missing_coding_evidence_task_ids: asArray<unknown>(teamRaw.missing_coding_evidence_task_ids).map(String),
    active_workflow_job_task_ids: asArray<unknown>(teamRaw.active_workflow_job_task_ids).map(String),
    failed_workflow_job_task_ids: asArray<unknown>(teamRaw.failed_workflow_job_task_ids).map(String),
    next_actions: asArray<unknown>(teamRaw.next_actions).map(String).filter(Boolean),
    safety: {
      read_only: boolValue(teamSafetyRaw.read_only),
      ledger_mutated: boolValue(teamSafetyRaw.ledger_mutated),
      live_execution_performed: boolValue(teamSafetyRaw.live_execution_performed),
      token_omitted: boolValue(teamSafetyRaw.token_omitted),
      raw_prompt_omitted: boolValue(teamSafetyRaw.raw_prompt_omitted),
      raw_source_omitted: boolValue(teamSafetyRaw.raw_source_omitted),
    },
    token_omitted: boolValue(teamRaw.token_omitted),
    live_execution_performed: boolValue(teamRaw.live_execution_performed),
  };
}

export interface CommanderSynthesisPromotionPayload {
  provider: string;
  operation: string;
  ok: boolean;
  status: string;
  workspace_id: string;
  artifact_id: string;
  approval_id?: string | null;
  approval_decision?: string;
  mode: string;
  memory_id?: string | null;
  delivery_artifact_id?: string | null;
  created?: Record<string, unknown>;
  safety: {
    ledger_mutated: boolean;
    live_execution_performed: boolean;
    token_omitted: boolean;
    raw_prompt_omitted: boolean;
    raw_response_omitted: boolean;
    memory_candidate_created?: boolean;
    customer_delivery_artifact_created?: boolean;
  };
  token_omitted: boolean;
  live_execution_performed: boolean;
}

export interface ExecutionEvidenceGapDecisionPayload {
  provider?: string;
  operation?: string;
  ok?: boolean;
  status: string;
  error?: string;
  message?: string;
  closed?: boolean;
  workspace_id?: string;
  run_id?: string;
  decision?: Record<string, unknown>;
  gap?: Record<string, unknown> | null;
  next_actions?: string[];
  recommended_action?: string;
  safety?: {
    read_only?: boolean;
    ledger_mutated?: boolean;
    live_execution_performed?: boolean;
    raw_note_omitted?: boolean;
    raw_prompt_omitted?: boolean;
    raw_response_omitted?: boolean;
    token_omitted?: boolean;
  };
  token_omitted?: boolean;
  live_execution_performed?: boolean;
}

export interface ReviewQueueSummary {
  pending_approvals: number;
  memory_candidates: number;
  evaluation_case_candidates?: number;
  failed_evaluation_case_runs?: number;
  ready_deliveries: number;
  waiting_deliveries: number;
  needs_attention_deliveries: number;
  commander_synthesis_pending_reviews?: number;
  commander_synthesis_promotion_available?: number;
  commander_synthesis_memory_reviews?: number;
  review_items_total: number;
  returned_items: number;
  retrieved_pending_approvals?: number;
  retrieved_memory_candidates?: number;
  retrieved_evaluation_case_candidates?: number;
  retrieved_failed_evaluation_case_runs?: number;
}

export interface ReviewQueueItem {
  item_type: "approval" | "memory_candidate" | "customer_delivery" | string;
  item_id: string;
  status: string;
  review_status?: string;
  kind?: string | null;
  title: string;
  summary?: string;
  task_id?: string | null;
  run_id?: string | null;
  agent_id?: string | null;
  artifact_id?: string | null;
  created_at?: string;
  updated_at?: string;
  priority?: number;
  next_action?: string;
  cli_action?: string;
  alternate_cli_action?: string | null;
  links?: Record<string, string | null>;
}

export interface ReviewQueuePayload {
  provider: string;
  operation: string;
  status: string;
  limit: number;
  summary: ReviewQueueSummary;
  review_items: ReviewQueueItem[];
  gates: { id: string; label: string; ok: boolean; value?: string | number | boolean }[];
  next_actions: string[];
  safety: {
    read_only: boolean;
    ledger_mutated: boolean;
    live_execution_performed: boolean;
    raw_prompt_omitted: boolean;
    raw_response_omitted: boolean;
    token_omitted: boolean;
  };
  token_omitted?: boolean;
}

export interface OperatorActionPlanItem {
  action_id: string;
  action_signature?: string | null;
  lane: string;
  severity: string;
  priority: number;
  title: string;
  summary?: string;
  command: string;
  verify_command?: string | null;
  receipt_record_command?: string | null;
  receipt_record_confirm_command?: string | null;
  receipt_verify_record_command?: string | null;
  ui_route?: string | null;
  source: string;
  evidence?: Record<string, unknown>;
  base_priority?: number;
  receipt_priority_boost?: number;
  receipt_required?: boolean;
  receipt_status?: string;
  receipt_underlying_status?: string;
  receipt_match?: string;
  receipt_current?: boolean;
  receipt_verified?: boolean;
  receipt_id?: string | null;
  receipt_hash?: string | null;
  receipt_evaluation?: Record<string, unknown> | null;
  control_readback_required?: boolean;
  control_readback_attached?: boolean;
  control_readback_hash?: string | null;
  receipt_state?: Record<string, unknown>;
}

export interface ExecutionEvidenceGapItem {
  run_id: string;
  task_id?: string | null;
  agent_id?: string | null;
  task_title?: string;
  run_status?: string;
  task_status?: string | null;
  remediation_task_id?: string | null;
  remediation_status?: string | null;
  remediation_synthesis_status?: string | null;
  remediation_synthesis_artifact_id?: string | null;
  remediation_synthesis_approval_id?: string | null;
  gap_decision_status?: string | null;
  gap_decision_type?: string | null;
  gap_decision?: Record<string, unknown> | null;
  gap_types: string[];
  missing_evidence: string[];
  severity: string;
  priority: number;
  command: string;
  next_action?: string;
  ui_route?: string | null;
  token_omitted?: boolean;
}

export interface ExecutionEvidenceGapsPayload {
  provider?: string;
  operation?: string;
  status?: string;
  workspace_id?: string;
  summary?: Record<string, number>;
  gaps: ExecutionEvidenceGapItem[];
  next_actions?: string[];
  safety?: Record<string, unknown>;
  token_omitted?: boolean;
}

export interface TaskIntakeChecklistItem {
  task_id: string;
  title: string;
  status: string;
  priority?: string;
  risk_level?: string;
  assigned_agent_ids: string[];
  plan_id?: string | null;
  plan_status?: string | null;
  plan_verified: boolean;
  plan_verified_at?: string | null;
  referenced_specs: number;
  referenced_memories: number;
  referenced_bases: number;
  gates: { id: string; ok: boolean; status: string; message?: string }[];
  failed_gate_ids: string[];
  severity: string;
  priority_score: number;
  command: string;
  next_action?: string;
  ui_route?: string | null;
  token_omitted?: boolean;
}

export interface LocalLoopAdmissionSummary {
  operation?: string;
  adapter?: "mock" | "hermes" | "openclaw" | string | null;
  agent_id?: string | null;
  live_adapter_tasks_checked: number;
  live_adapters: string[];
  passed_local_loop_admission: number;
  missing_local_loop_admission: number;
  local_loop_admission_ready: boolean;
  required_method_gates: string[];
  next_safe_commands: string[];
  safety?: Record<string, unknown>;
  token_omitted?: boolean;
}

export interface TaskIntakeChecklistPayload {
  provider?: string;
  operation?: string;
  status?: string;
  workspace_id?: string;
  summary?: Record<string, number>;
  local_loop_admission_summary?: LocalLoopAdmissionSummary;
  items: TaskIntakeChecklistItem[];
  next_actions?: string[];
  safety?: Record<string, unknown>;
  token_omitted?: boolean;
}

export interface OperatorCommandCenterNextAction {
  action_id: string;
  action_signature?: string | null;
  source: string;
  title: string;
  priority: number;
  command: string;
  verify_command?: string | null;
  evidence?: Record<string, unknown>;
  receipt_required?: boolean;
  receipt_status?: string;
  receipt_verified?: boolean;
  receipt_hash?: string | null;
  receipt_record_command?: string | null;
  receipt_verify_record_command?: string | null;
  control_readback_required?: boolean;
  control_readback_attached?: boolean;
  token_omitted?: boolean;
}

export interface OperatorCommandCenterPayload {
  provider: string;
  operation: string;
  status: string;
  workspace_id: string;
  summary: Record<string, number>;
  projects: Record<string, unknown>[];
  commander: {
    summary?: Record<string, unknown>;
    packages?: Record<string, unknown>[];
    coding_evidence_gaps?: Record<string, unknown>[];
    recommended_next_actions?: unknown[];
    raw_source_omitted?: boolean;
    raw_patch_omitted?: boolean;
    token_omitted?: boolean;
  };
  blocked_runs: Record<string, unknown>[];
  approvals: {
    summary?: Record<string, unknown>;
    pending?: Record<string, unknown>[];
    next_actions?: unknown[];
  };
  deliveries: {
    summary?: Record<string, unknown>;
    items?: Record<string, unknown>[];
    next_actions?: unknown[];
  };
  workers: {
    status?: string;
    fleet_health?: Record<string, unknown>;
    running_workers?: number;
    stuck_worker_tasks?: number;
    stuck_workflow_jobs?: number;
    stale_refs?: Record<string, unknown>[];
    next_actions?: string[];
  };
  operator_action_plan?: {
    status?: string;
    summary?: Record<string, unknown>;
    actions?: OperatorActionPlanItem[];
    receipt_coverage?: Record<string, unknown>;
  };
  next_actions: OperatorCommandCenterNextAction[];
  contract?: string;
  safety: {
    read_only: boolean;
    ledger_mutated: boolean;
    task_created?: boolean;
    run_created?: boolean;
    worktree_created?: boolean;
    live_execution_performed: boolean;
    server_shell_execution?: boolean;
    raw_prompt_omitted?: boolean;
    raw_response_omitted?: boolean;
    raw_source_omitted?: boolean;
    raw_patch_omitted?: boolean;
    token_omitted: boolean;
  };
  token_omitted?: boolean;
  live_execution_performed?: boolean;
}

export interface OperatorActionPlanPayload {
  provider: string;
  operation: string;
  status: string;
  workspace_id: string;
  summary: {
    actions: number;
    blocked: number;
    attention: number;
    ready: number;
    review_items_total: number;
    failed_evaluation_case_runs: number;
    waiting_deliveries: number;
    needs_attention_deliveries: number;
    stuck_worker_tasks: number;
    stuck_workflow_jobs: number;
    recommended_adapter: string;
    remediation_packages: number;
    remediation_ready_for_review: number;
    remediation_pending_reviews: number;
    remediation_promoted_memories: number;
    remediation_promoted_deliveries: number;
    evidence_gap_runs: number;
    missing_plan_runs: number;
    missing_plan_evidence_manifests: number;
    unverified_plan_evidence_manifests: number;
    remediated_evidence_gap_runs: number;
    blocked_evidence_gap_runs: number;
    evidence_synthesis_ready_runs: number;
    evidence_synthesis_pending_runs: number;
    evidence_synthesis_promoted_runs: number;
    evidence_gap_closure_ready_runs: number;
    closed_evidence_gap_runs: number;
    waived_evidence_gap_runs: number;
    task_intake_checked: number;
    task_intake_ready: number;
    task_intake_blocked: number;
    task_intake_attention: number;
    task_intake_missing_agent_plan: number;
    dispatch_evidence_proofs: number;
    dispatch_evidence_ready: number;
    dispatch_evidence_waiting_approval: number;
    dispatch_evidence_verified_manifests: number;
    operator_health_risks: number;
    operator_health_blocked: number;
    operator_health_attention: number;
    action_receipts: number;
    action_receipts_recorded: number;
    action_receipts_verified: number;
    action_receipts_failed: number;
    action_receipts_evaluated: number;
    action_receipts_evaluation_pass: number;
    action_receipts_evaluation_fail: number;
    receipt_failure_memory_candidates: number;
    receipt_failure_memory_failed_receipts: number;
    receipt_failure_memory_existing_candidates: number;
    receipt_required_actions: number;
    receipt_verified_actions: number;
    receipt_missing_actions: number;
    receipt_missing_verified_actions: number;
    receipt_stale_actions: number;
    receipt_evaluation_required_actions: number;
    receipt_evaluated_actions: number;
    receipt_evaluation_pass_actions: number;
    receipt_evaluation_fail_actions: number;
    receipt_evaluation_missing_actions: number;
    receipt_evaluation_coverage_percent: number;
    receipt_coverage_percent: number;
    receipt_lookup_window: number;
  };
  actions: OperatorActionPlanItem[];
  top_commands: string[];
  source_status: Record<string, string | undefined>;
  receipt_coverage?: {
    required: number;
    verified: number;
    stale: number;
    missing: number;
    missing_verified: number;
    coverage_percent: number;
    status: string;
    evaluation_required?: number;
    evaluated?: number;
    evaluation_pass?: number;
    evaluation_fail?: number;
    evaluation_missing?: number;
    evaluation_coverage_percent?: number;
    evaluation_status?: string;
    lookup_window: number;
    display_receipts: number;
    token_omitted?: boolean;
  };
  execution_evidence?: ExecutionEvidenceGapsPayload;
  task_intake?: TaskIntakeChecklistPayload;
  dispatch_evidence?: Record<string, unknown>;
  operator_health?: Record<string, unknown>;
  action_receipts?: OperatorActionReceiptsPayload;
  receipt_failure_memory?: Record<string, unknown>;
  safety: {
    read_only: boolean;
    ledger_mutated: boolean;
    live_execution_performed: boolean;
    raw_prompt_omitted: boolean;
    raw_response_omitted: boolean;
    token_omitted: boolean;
  };
  token_omitted?: boolean;
  live_execution_performed?: boolean;
}

export interface OperatorActionReceipt {
  receipt_id: string;
  audit_id?: string;
  actor_id?: string;
  workspace_id: string;
  status: string;
  source: string;
  action_id?: string | null;
  action_signature?: string | null;
  action_command?: string | null;
  action_hash?: string | null;
  verify_command?: string | null;
  verify_hash?: string | null;
  result_summary?: string | null;
  evaluation?: Record<string, unknown> | null;
  evaluation_id?: string | null;
  evaluation_pass_fail?: string | null;
  evaluation_score?: number | null;
  control_readback?: Record<string, unknown> | null;
  control_readback_id?: string | null;
  control_readback_hash?: string | null;
  created_at?: string;
  tamper_chain_hash?: string;
  token_omitted?: boolean;
}

export interface OperatorActionReceiptsPayload {
  provider: string;
  operation: string;
  status: string;
  workspace_id: string;
  summary: {
    receipts: number;
    recorded: number;
    verified: number;
    failed: number;
    skipped: number;
    evaluated: number;
    evaluation_pass: number;
    evaluation_fail: number;
    control_readback_required: number;
    control_readback_attached: number;
    control_readback_missing: number;
    control_readback_coverage_percent: number;
    control_readback_status: string;
    latest_control_readback_hash?: string | null;
  };
  receipts: OperatorActionReceipt[];
  safety: {
    read_only: boolean;
    ledger_mutated: boolean;
    live_execution_performed: boolean;
    raw_prompt_omitted: boolean;
    raw_response_omitted: boolean;
    token_omitted: boolean;
  };
  token_omitted?: boolean;
}

export interface OperatorEvidenceReportRun {
  run_id: string;
  task_id?: string | null;
  agent_id?: string | null;
  run_status?: string | null;
  status: string;
  failed_check_ids: string[];
  checks: { id: string; ok: boolean; message?: string }[];
  evidence_counts: Record<string, number>;
  agent_plan?: {
    plan_id?: string | null;
    status?: string | null;
    risk_level?: string | null;
    approval_required?: boolean;
    approval_id?: string | null;
    approval_decision?: string | null;
    verification_pass?: boolean;
    plan_hash?: string | null;
  };
  plan_evidence_manifest?: {
    manifest_id?: string | null;
    status?: string | null;
    verification_pass?: boolean;
    failed_check_ids?: string[];
  };
  memory_review?: {
    status?: string;
    total?: number;
    pending_review?: number;
    approved?: number;
    status_counts?: Record<string, number>;
    items?: Record<string, unknown>[];
    raw_content_omitted?: boolean;
    token_omitted?: boolean;
  };
  approvals?: {
    count?: number;
    pending?: number;
    approved?: number;
    rejected?: number;
    items?: Record<string, unknown>[];
  };
  worker_knowledge_retrieval?: {
    applicable?: boolean;
    status?: string;
    worker_tool_calls?: number;
    consumed_tool_calls?: number;
    missing_tool_calls?: number;
    packet_hashes?: string[];
    query_hashes?: string[];
    context_packet_hashes?: string[];
    context_block_count?: number;
    approved_memory_ids?: string[];
    retrieval_ids?: string[];
    source_hashes?: string[];
    paths?: string[];
    raw_query_omitted?: boolean;
    snippet_omitted?: boolean;
    raw_content_omitted?: boolean;
    context_body_not_persisted?: boolean;
    raw_transcript_omitted?: boolean;
    raw_prompt_omitted?: boolean;
    raw_response_omitted?: boolean;
    token_omitted?: boolean;
  };
  worker_runtime_summary?: {
    applicable?: boolean;
    status?: string;
    worker_tool_calls?: number;
    summary_events?: number;
    linked_summary_events?: number;
    event_ids?: string[];
    tool_items?: Record<string, unknown>[];
    events?: Record<string, unknown>[];
    event_is_worker_summary_not_raw_trace?: boolean;
    raw_prompt_omitted?: boolean;
    raw_response_omitted?: boolean;
    token_omitted?: boolean;
  };
  gap_decision?: Record<string, unknown> | null;
  recommended_commands: string[];
  token_omitted?: boolean;
}

export interface OperatorEvidenceReportPayload {
  provider: string;
  operation: string;
  status: string;
  workspace_id: string;
  summary: {
    runs: number;
    ready: number;
    attention: number;
    blocked: number;
    verified_plan_evidence_manifests: number;
    missing_plan_evidence_manifests: number;
    pending_approvals: number;
    memory_reviews: number;
    memory_review_ready: number;
    missing_memory_reviews: number;
    pending_memory_reviews: number;
    approval_required_plans: number;
    approved_required_plans: number;
    action_receipts: number;
    verified_action_receipts: number;
    evaluated_action_receipts: number;
    worker_runs: number;
    worker_knowledge_retrieval_ready: number;
    worker_knowledge_retrieval_missing: number;
    worker_knowledge_retrieval_unavailable: number;
    worker_runtime_summary_ready: number;
    worker_runtime_summary_missing: number;
  };
  runs: OperatorEvidenceReportRun[];
  recommended_commands: string[];
  contract?: string;
  safety: {
    read_only: boolean;
    ledger_mutated: boolean;
    live_execution_performed: boolean;
    raw_prompt_omitted: boolean;
    raw_response_omitted: boolean;
    token_omitted: boolean;
  };
  token_omitted?: boolean;
}

export interface OperatorActionReceiptResult {
  provider?: string;
  operation?: string;
  status: string;
  workspace_id?: string;
  receipt?: OperatorActionReceipt;
  evaluation?: Record<string, unknown> | null;
  next_actions?: string[];
  safety?: {
    read_only: boolean;
    ledger_mutated: boolean;
    live_execution_performed: boolean;
    raw_prompt_omitted: boolean;
    raw_response_omitted: boolean;
    token_omitted: boolean;
  };
  token_omitted?: boolean;
}

export interface OperatorLoopAuditStep {
  id: string;
  label: string;
  status: string;
  message?: string;
  evidence: Record<string, unknown>;
  command: string;
  source: string;
  token_omitted?: boolean;
}

export interface OperatorLoopRecordMemoryReview {
  memory_id: string;
  scope?: string;
  memory_type?: string;
  review_status: string;
  source_type?: string;
  source_ref?: string | null;
  task_id?: string | null;
  agent_id?: string | null;
  confidence?: number;
  summary?: string;
  created_at?: string | null;
  updated_at?: string | null;
  approve_command?: string;
  reject_command?: string;
  token_omitted?: boolean;
}

export interface OperatorLoopRecordApprovalReview {
  approval_id: string;
  task_id?: string | null;
  run_id?: string | null;
  tool_call_id?: string | null;
  requested_by_agent_id?: string | null;
  decision: string;
  reason?: string;
  created_at?: string | null;
  decided_at?: string | null;
  approve_command?: string;
  reject_command?: string;
  token_omitted?: boolean;
}

export interface OperatorLoopRecordAuditEntry {
  audit_id: string;
  actor_type?: string | null;
  actor_id?: string | null;
  action: string;
  entity_type: string;
  entity_id: string;
  before_hash?: string | null;
  after_hash?: string | null;
  tamper_chain_hash?: string | null;
  created_at?: string | null;
  token_omitted?: boolean;
}

export interface OperatorLoopRecordPayload {
  status: string;
  loop_id?: string | null;
  memory_reviews: OperatorLoopRecordMemoryReview[];
  approval_reviews: OperatorLoopRecordApprovalReview[];
  candidate_count: number;
  approved_count: number;
  pending_approval_count: number;
  audit_count: number;
  audit_trail: OperatorLoopRecordAuditEntry[];
  next_action?: string;
  review_queue_command?: string;
  token_omitted?: boolean;
}

export interface OperatorLoopAuditPayload {
  provider: string;
  operation: string;
  status: string;
  workspace_id: string;
  loop_id?: string | null;
  method: string;
  summary: {
    steps: number;
    pass: number;
    attention: number;
    blocked: number;
    knowledge_documents: number;
    verified_agent_plans: number;
    plan_bound_runs: number;
    verified_plan_evidence_manifests: number;
    evidence_gap_runs: number;
    loop_runs: number;
    loop_verified_plan_evidence_manifests: number;
    loop_blocked_plan_evidence_manifests: number;
    pending_approvals: number;
    memory_candidates: number;
    loop_memory_candidates: number;
    loop_approved_memories: number;
    loop_pending_approvals: number;
    audit_logs: number;
  };
  steps: OperatorLoopAuditStep[];
  action_package?: OperatorLoopActionPackagePayload;
  next_actions: string[];
  source_status: Record<string, string | undefined>;
  sources?: Record<string, unknown>;
  loop_record?: OperatorLoopRecordPayload;
  loop_readback?: HermesOpenClawLoopReadbackPayload | Record<string, unknown>;
  safety: {
    read_only: boolean;
    ledger_mutated: boolean;
    live_execution_performed: boolean;
    raw_prompt_omitted: boolean;
    raw_response_omitted: boolean;
    token_omitted: boolean;
  };
  token_omitted?: boolean;
  live_execution_performed?: boolean;
}

export interface OperatorLoopActionPackageItem {
  package_id: string;
  loop_id?: string | null;
  gate_id: string;
  gate_label: string;
  gate_status: string;
  source?: string;
  action_id?: string;
  action_signature?: string;
  action_command: string;
  verify_command: string;
  receipt_record_command: string;
  receipt_verify_record_command: string;
  message?: string;
  evidence?: Record<string, unknown>;
  token_omitted?: boolean;
}

export interface OperatorLoopActionPackagePayload {
  operation: string;
  status: string;
  loop_id?: string | null;
  method?: string;
  verify_command?: string;
  items: OperatorLoopActionPackageItem[];
  summary: {
    items: number;
    blocked: number;
    attention: number;
    loop_scoped: boolean;
  };
  contract?: string;
  safety?: {
    read_only?: boolean;
    ledger_mutated?: boolean;
    live_execution_performed?: boolean;
    token_omitted?: boolean;
  };
  token_omitted?: boolean;
}

export interface OperatorHandoffPayload {
  provider: string;
  operation: string;
  status: string;
  workspace_id: string;
  loop_id?: string | null;
  summary: {
    loop_status?: string;
    action_plan_status?: string;
    evidence_report_status?: string;
    evidence_report_runs?: number;
    evidence_report_ready?: number;
    evidence_report_attention?: number;
    evidence_report_blocked?: number;
    evidence_report_missing_plan_evidence_manifests?: number;
    evidence_report_pending_approvals?: number;
    loop_package_items: number;
    operator_actions: number;
    receipt_required: number;
    receipt_verified: number;
    receipt_missing: number;
    receipt_stale: number;
    receipt_evaluation_required: number;
    receipt_evaluated: number;
    receipt_evaluation_fail: number;
    receipt_evaluation_missing: number;
    receipt_failure_memory_candidates: number;
    receipt_failure_memory_failed_receipts: number;
    receipt_failure_memory_existing_candidates: number;
    advance_loop_work_items?: number;
    loop_record_status?: string;
    loop_record_candidates: number;
    loop_record_approved: number;
    loop_record_pending_approvals: number;
  };
  work_order: {
    method?: string;
    action_package?: OperatorLoopActionPackagePayload;
    evidence_report?: {
      operation?: string;
      status?: string;
      action_id?: string;
      action_signature?: string;
      summary?: Record<string, number>;
      runs?: Record<string, unknown>[];
      next_actions?: string[];
      remediation_chain?: {
        operation?: string;
        status?: string;
        summary?: Record<string, number>;
        items?: Record<string, unknown>[];
        next_actions?: string[];
        token_omitted?: boolean;
      };
      receipt_state?: {
        status?: string;
        receipt_id?: string | null;
        receipt_hash?: string | null;
        evaluation_pass_fail?: string | null;
        evaluation_score?: number | null;
        current?: boolean;
        verified?: boolean;
        action_signature?: string;
        token_omitted?: boolean;
      };
      token_omitted?: boolean;
    };
    next_actions: string[];
    top_operator_actions: OperatorActionPlanItem[];
    advance_loop?: Record<string, unknown>;
    commands: string[];
    token_omitted?: boolean;
  };
  control_summary?: {
    operation: string;
    status: string;
    mode?: string;
    loop_id?: string | null;
    recommended_step?: Record<string, unknown>;
    next_command?: string | null;
    verify_command?: string | null;
    receipt_command?: string | null;
    requires_human?: boolean;
    requires_receipt?: boolean;
    server_executes_shell?: boolean;
    copy_only?: boolean;
    step_counts?: Record<string, number>;
    selected_gate?: string | null;
    selected_status?: string | null;
    policy_id?: string;
    token_omitted?: boolean;
  };
  receipt_state: {
    coverage?: OperatorActionPlanPayload["receipt_coverage"];
    recent: OperatorActionReceipt[];
    summary: Record<string, number>;
    failure_memory?: Record<string, unknown>;
    token_omitted?: boolean;
  };
  review_state: {
    loop_record?: OperatorLoopRecordPayload;
    token_omitted?: boolean;
  };
  loop_health?: {
    operation: string;
    status: string;
    score: number;
    score_parts: Record<string, number>;
    gates: Record<string, Record<string, unknown>>;
    risks: { id: string; severity: string; count: number; next_action?: string }[];
    next_action?: string;
    contract?: string;
    token_omitted?: boolean;
  };
  sources?: Record<string, unknown>;
  contract?: string;
  auth?: {
    mode: string;
    scoped: boolean;
    required_scope: string;
    workspace_id: string;
    agent_id?: string | null;
    token_omitted?: boolean;
  };
  safety: {
    read_only: boolean;
    ledger_mutated: boolean;
    live_execution_performed: boolean;
    raw_prompt_omitted: boolean;
    raw_response_omitted: boolean;
    token_omitted: boolean;
  };
  token_omitted?: boolean;
  live_execution_performed?: boolean;
}

export interface OperatorLoopSelfCheckPayload {
  provider: string;
  operation: string;
  status: string;
  workspace_id: string;
  loop_id?: string | null;
  summary: Record<string, number | string | boolean | null | undefined>;
  gates: Record<string, Record<string, unknown>>;
  policy_decisions: Record<string, unknown>[];
  next_actions: string[];
  contract?: string;
  safety: {
    read_only: boolean;
    ledger_mutated: boolean;
    live_execution_performed: boolean;
    server_shell_execution: boolean;
    raw_prompt_omitted: boolean;
    raw_response_omitted: boolean;
    token_omitted: boolean;
  };
  token_omitted?: boolean;
  live_execution_performed?: boolean;
}

export interface OperatorLoopLaunchPacketPayload {
  provider: string;
  operation: string;
  status: string;
  workspace_id: string;
  task_id?: string | null;
  agent_id?: string | null;
  method: string;
  summary: Record<string, number | string | boolean | null | undefined>;
  launch_sequence: Record<string, unknown>[];
  execution_chain: {
    step_id: string;
    label: string;
    phase: string;
    command: string;
    verify_command?: string | null;
    receipt_command?: string | null;
    mutating: boolean;
    confirm_required: boolean;
    receipt_required: boolean;
    source?: string;
    selected_gate?: string | null;
    selected_status?: string | null;
    action_signature?: string | null;
    policy_id?: string | null;
    next_on_pass?: string | null;
    step_status?: string;
    blocked_reason?: string;
    ready_reason?: string;
    next_safe_command?: string | null;
    receipt_state?: Record<string, unknown>;
    token_omitted?: boolean;
  }[];
  control_summary?: {
    operation: string;
    status: string;
    mode?: string;
    recommended_step?: Record<string, unknown>;
    next_command?: string | null;
    verify_command?: string | null;
    receipt_command?: string | null;
    requires_human?: boolean;
    requires_receipt?: boolean;
    server_executes_shell?: boolean;
    copy_only?: boolean;
    step_counts?: Record<string, number>;
    unverified_receipt_steps?: number;
    blocking_steps?: string[];
    attention_steps?: string[];
    verified_steps?: string[];
    policy_id?: string;
    token_omitted?: boolean;
  };
  agent_plan_draft: Record<string, unknown>;
  evaluation_contract: {
    operation: string;
    status: string;
    score?: number | null;
    minimum_exit_criteria: string[];
    required_commands: string[];
    required_ledgers: string[];
    receipt_evaluation: Record<string, unknown>;
    token_omitted?: boolean;
  };
  audit_contract: {
    operation: string;
    method: string;
    tamper_chain_required: boolean;
    raw_content_policy?: string;
    record_required: boolean;
    record_commands: string[];
    evidence_report?: Record<string, unknown>;
    bounded_runner?: Record<string, unknown>;
    token_omitted?: boolean;
  };
  commands: string[];
  sources?: Record<string, unknown>;
  contract?: string;
  safety: {
    read_only: boolean;
    ledger_mutated: boolean;
    live_execution_performed: boolean;
    raw_prompt_omitted: boolean;
    raw_response_omitted: boolean;
    token_omitted: boolean;
  };
  token_omitted?: boolean;
  live_execution_performed?: boolean;
}

export type OperatorStartCheckAdapter = "mock" | "hermes" | "openclaw";

export interface OperatorLoopDriverAgentPacketPayload {
  operation: string;
  adapter: string;
  current_phase: string;
  ready_to_confirm_loop: boolean;
  max_steps?: number;
  steps_advanced?: number;
  stop_reason?: string | null;
  phases: {
    phase: string;
    status: string;
    command?: string | null;
    gate_id?: string;
    description?: string;
    confirm_required?: boolean;
    token_omitted?: boolean;
  }[];
  phase_commands?: Record<string, string | null | undefined>;
  method_gates?: {
    id: string;
    phase: string;
    required: boolean;
    status: string;
    command?: string | null;
    confirm_required?: boolean;
    proof?: string;
    token_omitted?: boolean;
  }[];
  commands: Record<string, string | null | undefined>;
  gates: Record<string, unknown>;
  contract?: string;
  safety: {
    read_only: boolean;
    ledger_mutated: boolean;
    live_execution_performed: boolean;
    server_executes_shell: boolean;
    raw_prompt_omitted: boolean;
    raw_response_omitted: boolean;
    raw_content_omitted?: boolean;
    token_omitted: boolean;
  };
  token_omitted?: boolean;
  live_execution_performed?: boolean;
}

export interface OperatorStartCheckPayload {
  provider: string;
  operation: string;
  status: string;
  adapter: string;
  workspace_id: string;
  summary: Record<string, unknown>;
  loop_driver_entry: Record<string, unknown>;
  acceptance_packet: Record<string, unknown>;
  local_loop_admission_packet: Record<string, unknown>;
  local_run_path?: LocalRunPathStep[];
  agent_loop_packet?: OperatorLoopDriverAgentPacketPayload;
  next_commands: string[];
  safety: {
    read_only: boolean;
    ledger_mutated: boolean;
    live_execution_performed: boolean;
    server_executes_shell?: boolean;
    raw_prompt_omitted: boolean;
    raw_response_omitted: boolean;
    token_omitted: boolean;
  };
  token_omitted?: boolean;
  live_execution_performed?: boolean;
}

export interface OperatorLoopDriverPacketsPayload {
  provider: string;
  operation: string;
  status: string;
  packets: OperatorLoopDriverAgentPacketPayload[];
  start_checks: Record<string, OperatorStartCheckPayload>;
  safety: {
    read_only: boolean;
    ledger_mutated: boolean;
    live_execution_performed: boolean;
    server_executes_shell: boolean;
    token_omitted: boolean;
  };
  token_omitted?: boolean;
  live_execution_performed?: boolean;
}

export interface OperatorAgentLoopHandoffConsumerPayload {
  operation: string;
  adapter: string;
  status: string;
  ready_for_handoff: boolean;
  ready_for_bounded_loop_confirm: boolean;
  ready_for_live_dispatch: boolean;
  blockers: string[];
  attention: string[];
  start_check: {
    status: string;
    command?: string | null;
    current_phase?: string | null;
    can_preview_loop: boolean;
    can_confirm_bounded_loop: boolean;
    live_dispatch_requires_confirm_run: boolean;
    human_review_required: boolean;
    memory_review_required: boolean;
    server_executes_shell: boolean;
    token_omitted?: boolean;
  };
  launch_brief: {
    status: string;
    next_command?: string | null;
    verify_command?: string | null;
    receipt_command?: string | null;
    current_code_ok?: boolean;
    control_mode?: string | null;
    recommended_step?: string | null;
    token_omitted?: boolean;
  };
  live_product_readiness: {
    adapter: string;
    status: string;
    fresh: boolean;
    run_id?: string | null;
    task_id?: string | null;
    artifact_id?: string | null;
    plan_evidence_manifest_id?: string | null;
    command?: string | null;
    token_omitted?: boolean;
  };
  method: {
    phases: string[];
    phase_commands: Record<string, string | null | undefined>;
    method_gate_ids: string[];
    required_gate_ids: string[];
    token_omitted?: boolean;
  };
  commands: Record<string, string | null | undefined>;
  run_start_admission?: {
    operation: string;
    gateway_endpoint: string;
    runtime_type: string;
    governed_runtime: boolean;
    would_allow_run_start: boolean;
    would_block_run_start: boolean;
    fail_closed_error: string;
    no_run_created_on_block: boolean;
    agent_plan_required: boolean;
    supervision_hash_state: string;
    run_metadata_field: string;
    recommended_next?: string | null;
    status: string;
    contract?: string;
    receipt_projection?: {
      source?: string;
      action_id?: string;
      action_signature?: string;
      action_command?: string;
      verify_command?: string;
      control_readback_required?: boolean;
      control_readback_source?: string;
      token_omitted?: boolean;
    };
    safety?: {
      read_only?: boolean;
      ledger_mutated?: boolean;
      live_execution_performed?: boolean;
      server_executes_shell?: boolean;
      raw_prompt_omitted?: boolean;
      raw_response_omitted?: boolean;
      raw_content_omitted?: boolean;
      token_omitted?: boolean;
    };
    token_omitted?: boolean;
  };
  safety: {
    read_only: boolean;
    ledger_mutated: boolean;
    live_execution_performed: boolean;
    server_executes_shell: boolean;
    raw_prompt_omitted?: boolean;
    raw_response_omitted?: boolean;
    raw_content_omitted?: boolean;
    token_omitted: boolean;
  };
  token_omitted?: boolean;
}

export interface OperatorAgentLoopHandoffPayload {
  provider: string;
  operation: string;
  status: string;
  workspace_id: string;
  adapters: string[];
  current_code: {
    ok: boolean;
    status: string;
    git_head_sha?: string | null;
    git_branch?: string | null;
    server_pid?: number | null;
    strict_command?: string | null;
    token_omitted?: boolean;
  };
  summary: {
    consumers: number;
    ready_consumers: number;
    attention_consumers: number;
    blocked_consumers: number;
    ready_for_handoff: boolean;
    ready_for_all_bounded_loop_confirm: boolean;
    fresh_live_adapters: number;
    current_code_ok: boolean;
  };
  consumers: OperatorAgentLoopHandoffConsumerPayload[];
  codex_consumer?: {
    operation: string;
    status: string;
    uses_same_packets: boolean;
    commands: Record<string, string | null | undefined>;
    token_omitted?: boolean;
  };
  safety: {
    read_only: boolean;
    ledger_mutated: boolean;
    live_execution_performed: boolean;
    server_executes_shell: boolean;
    raw_prompt_omitted?: boolean;
    raw_response_omitted?: boolean;
    raw_content_omitted?: boolean;
    token_omitted: boolean;
  };
  token_omitted?: boolean;
  live_execution_performed?: boolean;
}

export interface OperatorLoopSupervisionItemPayload {
  operation: string;
  adapter: string;
  status: string;
  can_preview_loop: boolean;
  can_confirm_bounded_loop: boolean;
  should_record_before_execute: boolean;
  ready_for_live_dispatch: boolean;
  blockers: string[];
  attention: string[];
  review_pressure: Record<string, unknown>;
  gates: {
    id: string;
    ok?: boolean;
    status?: string;
    command?: string | null;
    recommended_adapter?: string | null;
    service_managed_adapter?: string | null;
    server_executes_shell?: boolean;
    token_omitted?: boolean;
  }[];
  local_deployment?: {
    local_run_path?: {
      operation?: string;
      recommended_adapter?: string | null;
      safety?: {
        server_executes_shell?: boolean;
        token_omitted?: boolean;
      };
      token_omitted?: boolean;
    };
    service_managed_loop?: {
      operation?: string;
      adapter?: string | null;
      manager?: string | null;
      service_managed_loop_ready?: boolean;
      service_active_loop_ready?: boolean;
      service_loaded?: boolean;
      active_status?: string;
      active_loop_status?: string;
      status?: string;
      checked_status?: string;
      installed_status?: string;
      service_check_available?: boolean;
      service_control_preview_available?: boolean;
      receipt_required?: boolean;
      receipt_verified?: boolean;
      receipt_id?: string | null;
      control_readback_required?: boolean;
      control_readback_attached?: boolean;
      control_readback_id?: string | null;
      live_execution_performed?: boolean;
      commands?: Record<string, string | null | undefined>;
      safety?: {
        server_executes_shell?: boolean;
        live_execution_performed?: boolean;
        token_omitted?: boolean;
      };
      token_omitted?: boolean;
    };
    managed_execution_path?: {
      operation?: string;
      status?: string;
      adapter?: string | null;
      service_managed_loop_ready?: boolean;
      service_active_loop_ready?: boolean;
      service_loaded?: boolean;
      service_active_status?: string;
      recommended_before_dispatch?: string | null;
      commands?: Record<string, string | null | undefined>;
      first_safe_commands?: string[];
      confirm_required_commands?: string[];
      verify_commands?: string[];
      gates?: {
        id: string;
        status?: string;
        required?: boolean;
        proof?: string;
        token_omitted?: boolean;
      }[];
      safety?: {
        server_executes_shell?: boolean;
        live_execution_performed?: boolean;
        token_omitted?: boolean;
      };
      token_omitted?: boolean;
    };
    token_omitted?: boolean;
  };
  agent_work_packet?: Record<string, unknown>;
  next_commands: {
    safe_read_commands: string[];
    preview_commands: string[];
    confirm_required_commands: string[];
    recommended_next?: string | null;
    token_omitted?: boolean;
  };
  commands: Record<string, string | null | undefined>;
  safety: {
    read_only: boolean;
    ledger_mutated: boolean;
    live_execution_performed: boolean;
    server_executes_shell: boolean;
    raw_prompt_omitted?: boolean;
    raw_response_omitted?: boolean;
    raw_content_omitted?: boolean;
    token_omitted: boolean;
  };
  token_omitted?: boolean;
}

export interface OperatorLoopSupervisionPayload {
  provider: string;
  operation: string;
  status: string;
  workspace_id: string;
  adapters: string[];
  handoff_summary: Record<string, unknown>;
  summary: {
    items: number;
    ready_to_confirm: number;
    record_first: number;
    preview_only: number;
    blocked: number;
    can_confirm_all: boolean;
    record_required: boolean;
    current_code_ok: boolean;
  };
  items: OperatorLoopSupervisionItemPayload[];
  work_packets?: Record<string, unknown>[];
  next_actions: string[];
  safety: {
    read_only: boolean;
    ledger_mutated: boolean;
    live_execution_performed: boolean;
    server_executes_shell: boolean;
    raw_prompt_omitted?: boolean;
    raw_response_omitted?: boolean;
    raw_content_omitted?: boolean;
    token_omitted: boolean;
  };
  token_omitted?: boolean;
  live_execution_performed?: boolean;
}

export interface OperatorLoopBootstrapItemPayload {
  operation: string;
  status: string;
  adapter: string;
  manager: string;
  next_action?: string | null;
  summary: {
    start_check_status?: string | null;
    supervision_status?: string | null;
    current_code_ok: boolean;
    service_closure_required: boolean;
    service_closure_step?: string | null;
    service_managed_loop_ready: boolean;
    service_active_loop_ready: boolean;
    service_loaded: boolean;
    local_cli_service_check_performed: boolean;
    can_confirm_bounded_loop: boolean;
  };
  bootstrap_steps: {
    id: string;
    phase: string;
    status?: string | null;
    command?: string | null;
    confirm_required?: boolean;
    server_executes_shell?: boolean;
    token_omitted?: boolean;
  }[];
  commands: Record<string, string | null | undefined>;
  service_check?: Record<string, unknown>;
  service_closure?: Record<string, unknown>;
  supervision?: {
    status?: string | null;
    primary_next_action?: Record<string, unknown>;
    service_closure?: Record<string, unknown>;
    token_omitted?: boolean;
  };
  safety: {
    read_only: boolean;
    ledger_mutated: boolean;
    live_execution_performed: boolean;
    server_executes_shell: boolean;
    local_cli_service_check_performed?: boolean;
    token_omitted: boolean;
  };
  token_omitted?: boolean;
  live_execution_performed?: boolean;
}

export interface OperatorLoopBootstrapPayload {
  provider: string;
  operation: string;
  status: string;
  mode?: string;
  workspace_id: string;
  adapters: string[];
  summary: {
    items: number;
    ready: number;
    attention: number;
    blocked: number;
    service_closure_required: number;
    service_active_loop_ready: number;
    current_code_ok: boolean;
    local_cli_service_check_performed: boolean;
  };
  items: OperatorLoopBootstrapItemPayload[];
  next_actions: string[];
  supervision_summary?: Record<string, unknown>;
  contract?: string;
  safety: {
    read_only: boolean;
    ledger_mutated: boolean;
    live_execution_performed: boolean;
    server_executes_shell: boolean;
    local_cli_service_check_performed?: boolean;
    raw_prompt_omitted?: boolean;
    raw_response_omitted?: boolean;
    raw_content_omitted?: boolean;
    token_omitted: boolean;
  };
  token_omitted?: boolean;
  live_execution_performed?: boolean;
}

export interface OperatorLoopControlPayload {
  provider: string;
  operation: string;
  status: string;
  workspace_id: string;
  loop_id?: string | null;
  summary: Record<string, number | string | boolean | null | undefined>;
  next_actions: string[];
  work_order: {
    advance_loop?: Record<string, unknown>;
    commands: string[];
    token_omitted?: boolean;
  };
  control_summary?: OperatorHandoffPayload["control_summary"];
  sources?: Record<string, unknown>;
  contract?: string;
  safety: {
    read_only: boolean;
    ledger_mutated: boolean;
    live_execution_performed: boolean;
    server_executes_shell?: boolean;
    raw_prompt_omitted: boolean;
    raw_response_omitted: boolean;
    token_omitted: boolean;
  };
  token_omitted?: boolean;
  live_execution_performed?: boolean;
}

export interface OperatorHealthComponent {
  id: string;
  label: string;
  status: string;
  score: number;
  weight: number;
  summary?: string;
  next_action?: string;
}

export interface OperatorRuntimeDoctorPayload {
  provider: string;
  operation: string;
  status: string;
  workspace_id: string;
  base_url?: string;
  summary: {
    mis_status?: string;
    operator_health_score?: number | null;
    recommended_adapter?: string;
    ready_adapters: string[];
    live_ready_adapters: string[];
    requires_confirm_run: string[];
    requires_prepared_action: string[];
    remote_worker_count: number;
    stale_remote_enrollments: number;
    never_seen_remote_enrollments: number;
    control_status?: string;
    control_mode?: string;
    evidence_chain_status?: string;
    blocked_gates: string[];
    attention_gates: string[];
  };
  gates: {
    id: string;
    label: string;
    status: string;
    ok: boolean;
    detail?: string;
    next_action?: string | null;
    token_omitted?: boolean;
  }[];
  commands: Record<string, string>;
  sources?: Record<string, unknown>;
  contract?: string;
  auth?: OperatorHandoffPayload["auth"];
  safety: {
    read_only: boolean;
    ledger_mutated: boolean;
    live_execution_performed: boolean;
    server_executes_shell?: boolean;
    raw_prompt_omitted: boolean;
    raw_response_omitted: boolean;
    token_omitted: boolean;
  };
  token_omitted?: boolean;
  live_execution_performed?: boolean;
}

export interface OperatorExecutionModePayload {
  provider: string;
  operation: string;
  status: string;
  workspace_id: string;
  adapter: WorkerAdapterName;
  mode: string;
  selected_path: string;
  summary: {
    adapter?: WorkerAdapterName;
    adapter_readiness?: string;
    trust_status?: string;
    selected_path?: string;
    live_confirm_required?: boolean;
    confirm_run?: boolean;
    confirm_run_wall?: string;
    prepared_action_wall?: string;
    pending_approvals?: number;
    active_workflow_jobs?: number;
    runtime_doctor_status?: string;
    blocked_gates?: string[];
    attention_gates?: string[];
    recommended_adapter?: string;
  };
  selected_route?: {
    adapter?: WorkerAdapterName;
    readiness?: string;
    trust_status?: string;
    target_resource?: string | null;
    recommended_action?: string;
    requires_confirm_run?: boolean;
    requires_prepared_action?: boolean;
    token_omitted?: boolean;
  };
  gates: {
    id: string;
    label: string;
    status: string;
    detail?: string;
    next_action?: string | null;
    token_omitted?: boolean;
  }[];
  commands: Record<string, string>;
  sources?: Record<string, unknown>;
  contract?: string;
  safety: {
    read_only: boolean;
    ledger_mutated: boolean;
    live_execution_performed: boolean;
    server_executes_shell?: boolean;
    raw_prompt_omitted: boolean;
    raw_response_omitted: boolean;
    token_omitted: boolean;
  };
  token_omitted?: boolean;
  live_execution_performed?: boolean;
}

export interface OperatorHealthPayload {
  provider: string;
  operation: string;
  status: string;
  score: number;
  workspace_id: string;
  loop_id?: string | null;
  summary: {
    components: number;
    ready: number;
    attention: number;
    blocked: number;
    review_items_total: number;
    operator_actions: number;
    loop_health_score: number;
    worker_fleet_status?: string;
    security_status?: string;
    local_readiness_status?: string;
    control_status?: string;
    control_mode?: string;
    control_selected_gate?: string | null;
    control_requires_human?: boolean;
    control_requires_receipt?: boolean;
  };
  components: OperatorHealthComponent[];
  control_summary?: OperatorHandoffPayload["control_summary"];
  loop_control?: {
    status: string;
    source?: string;
    mode?: string;
    recommended_step?: string;
    recommended_step_status?: string;
    selected_gate?: string | null;
    selected_status?: string | null;
    next_action?: string | null;
    verify_command?: string | null;
    receipt_command?: string | null;
    requires_human?: boolean;
    requires_receipt?: boolean;
    copy_only?: boolean;
    server_executes_shell?: boolean;
    server_shell_execution?: boolean;
    refresh_cache_required_after_receipt?: boolean;
    control_readback_source?: string;
    token_omitted?: boolean;
  };
  risks: {
    id: string;
    severity: string;
    summary?: string;
    next_action?: string;
    action_id?: string;
    action_signature?: string;
    action_command?: string;
    verify_command?: string;
    receipt_record_command?: string;
    receipt_verify_record_command?: string;
    receipt_required?: boolean;
    token_omitted?: boolean;
  }[];
  next_actions: string[];
  sources?: Record<string, unknown>;
  auth?: OperatorHandoffPayload["auth"];
  contract?: string;
  safety: {
    read_only: boolean;
    ledger_mutated: boolean;
    live_execution_performed: boolean;
    raw_prompt_omitted: boolean;
    raw_response_omitted: boolean;
    token_omitted: boolean;
  };
  token_omitted?: boolean;
  live_execution_performed?: boolean;
}

export interface EvaluationCaseCandidate {
  case_id: string;
  workspace_id: string;
  source_type: string;
  source_ref?: string | null;
  task_id?: string | null;
  run_id?: string | null;
  artifact_id?: string | null;
  evaluation_id?: string | null;
  agent_id?: string | null;
  case_type: string;
  title: string;
  input_summary?: string | null;
  expected_output_summary?: string | null;
  failure_mode?: string | null;
  confidence: number;
  review_status: string;
  created_by_agent_id?: string | null;
  owner_user_id?: string | null;
  created_at: string;
  updated_at: string;
  rubric?: Record<string, unknown>;
  token_omitted?: boolean;
}

export interface EvaluationCaseCandidatesPayload {
  provider: string;
  operation: string;
  status: string;
  workspace_id: string;
  limit: number;
  summary: {
    candidate: number;
    approved: number;
    rejected: number;
    returned: number;
  };
  cases: EvaluationCaseCandidate[];
  next_actions: string[];
  safety: {
    read_only: boolean;
    ledger_mutated: boolean;
    live_execution_performed: boolean;
    raw_prompt_omitted: boolean;
    raw_response_omitted: boolean;
    token_omitted: boolean;
  };
  token_omitted: boolean;
}

export interface EvaluationCaseRun {
  case_run_id?: string;
  case_id: string;
  workspace_id?: string;
  run_id?: string;
  evaluation_id?: string;
  artifact_id?: string | null;
  runner_type: string;
  status: string;
  score: number;
  pass_fail: "pass" | "fail";
  review_status?: string;
  reviewed_by_user_id?: string | null;
  review_note?: string | null;
  reviewed_at?: string | null;
  checks?: Record<string, unknown>;
  case_title?: string;
  case_type?: string;
  task_id?: string | null;
  source_type?: string | null;
  source_ref?: string | null;
  created_by_agent_id?: string | null;
  created_at?: string;
  token_omitted?: boolean;
}

export interface EvaluationCaseRunPayload {
  provider: string;
  operation: string;
  status: string;
  created?: boolean;
  workspace_id: string;
  limit?: number;
  summary: {
    selected?: number;
    planned?: number;
    skipped?: number;
    total?: number;
    returned?: number;
    created?: number;
    passed?: number;
    failed?: number;
    min_score?: number;
  };
  planned_runs?: EvaluationCaseRun[];
  case_runs: EvaluationCaseRun[];
  skipped?: EvaluationCaseRun[];
  next_actions?: string[];
  safety: {
    read_only?: boolean;
    ledger_mutated: boolean;
    live_execution_performed: boolean;
    raw_prompt_omitted: boolean;
    raw_response_omitted: boolean;
    token_omitted: boolean;
  };
  token_omitted: boolean;
}

export interface StuckWorkerTask extends Task {
  age_sec?: number;
  threshold_sec?: number;
  running_run_id?: string | null;
  running_run_started_at?: string | null;
  stuck_reason?: string;
}

export interface WorkerDaemonStatus {
  adapter: "mock" | "hermes" | "openclaw";
  status: string;
  running: boolean;
  pid?: number | null;
  agent_id?: string | null;
  started_at?: string | null;
  stopped_at?: string | null;
  poll_interval?: number | null;
  max_tasks?: number | null;
  confirm_run?: boolean;
  management_mode?: "standalone" | "daemon_api" | "host_stack" | string;
  control_allowed?: boolean;
  process_source?: "daemon_metadata" | "worker_state" | "none" | string;
  process_claim_active?: boolean;
  host_management_claim_active?: boolean;
  process_identity_status?: string;
  process_identity_verified?: boolean;
  log_path?: string;
  state_path?: string;
  worker_status?: string | null;
  state_updated_at?: string | null;
  processed?: number;
  iterations?: number;
  total_errors?: number;
  consecutive_errors?: number;
  continue_on_error?: boolean;
  last_error?: Record<string, unknown> | null;
  last_result?: Record<string, unknown> | null;
  log_tail?: string[];
}

export interface AgentGatewayRunStartLoopSupervisionGate {
  operation: string;
  runtime_type?: string;
  adapter?: string;
  task_id?: string | null;
  agent_id?: string | null;
  ok?: boolean;
  status?: string;
  can_preview_loop?: boolean;
  can_confirm_bounded_loop?: boolean;
  should_record_before_execute?: boolean;
  ready_for_live_dispatch?: boolean;
  blockers?: string[];
  attention?: string[];
  reason?: string | null;
  recommended_next?: string | null;
  supervision_hash?: string | null;
  commands?: Record<string, string | null | undefined>;
  safety?: {
    read_only?: boolean;
    ledger_mutated?: boolean;
    live_execution_performed?: boolean;
    server_executes_shell?: boolean;
    raw_prompt_omitted?: boolean;
    raw_response_omitted?: boolean;
    raw_content_omitted?: boolean;
    token_omitted?: boolean;
  };
  token_omitted?: boolean;
  live_execution_performed?: boolean;
  server_executes_shell?: boolean;
}

export interface WorkerDispatchResult {
  provider: string;
  dry_run: boolean;
  ok: boolean;
  adapter: "mock" | "hermes" | "openclaw";
  agent_id: string;
  task_id: string;
  run_id?: string | null;
  run_start_attempted?: boolean;
  loop_supervision_gate?: AgentGatewayRunStartLoopSupervisionGate | null;
  agent_plan_id?: string | null;
  plan_evidence_manifest_id?: string | null;
  plan_evidence_status?: string | null;
  plan_evidence_pass?: boolean;
  evidence?: {
    task_id?: string;
    run_id?: string | null;
    agent_plan_id?: string | null;
    agent_plan_status?: string | null;
    agent_plan_verified?: boolean;
    plan_hash?: string | null;
    plan_evidence_manifest_id?: string | null;
    plan_evidence_status?: string | null;
    plan_evidence_pass?: boolean;
    evidence_counts?: Record<string, number>;
    intake?: Record<string, unknown> | null;
    ready_for_delivery?: boolean;
    token_omitted?: boolean;
  };
  duration_ms: number;
  worker_result?: {
    ok?: boolean;
    processed?: number;
    results?: {
      processed?: boolean;
      task_id?: string;
      run_id?: string;
      adapter?: string;
      ok?: boolean;
      output_summary?: string;
      error_type?: string | null;
      run_start_attempted?: boolean;
      loop_supervision_gate?: AgentGatewayRunStartLoopSupervisionGate | null;
    }[];
  };
  error?: string | null;
}

export interface WorkerDaemonResult {
  provider: string;
  ok: boolean;
  already_running?: boolean;
  daemon?: WorkerDaemonStatus;
  daemons?: WorkerDaemonStatus[];
  error?: string;
  message?: string;
  recommended_action?: string;
  local_loop_admission_summary?: LocalLoopAdmissionSummary;
  task_intake?: TaskIntakeChecklistPayload;
}

export interface WorkerDaemonLogPayload {
  provider: string;
  daemon: WorkerDaemonStatus;
}

export interface WorkerTaskReleaseResult {
  released: boolean;
  task: Task;
  released_runs: string[];
  token_omitted: boolean;
  error?: string;
}

export interface WorkerFleetHygienePayload {
  provider: string;
  operation: string;
  status: string;
  threshold_sec: number;
  enrollment_age_sec: number;
  summary: {
    stuck_tasks: number;
    stale_never_seen_enrollments: number;
    stale_heartbeat_enrollments: number;
    actions_available: number;
    released_tasks?: number;
    revoked_enrollments?: number;
    errors?: number;
  };
  stuck_tasks: StuckWorkerTask[];
  stale_never_seen_enrollments: AgentGatewayEnrollment[];
  stale_heartbeat_enrollments: AgentGatewayEnrollment[];
  recommended_actions: string[];
  safety: {
    read_only: boolean;
    requires_confirm_cleanup: boolean;
    live_execution_performed: boolean;
    token_omitted: boolean;
  };
  applied?: boolean;
  released_tasks?: { task_id: string; released_runs: string[] }[];
  revoked_enrollments?: { token_id: string; agent_id?: string | null; sessions_revoked?: number }[];
  errors?: Record<string, unknown>[];
  error?: string;
  token_omitted: boolean;
  live_execution_performed: boolean;
}

export interface AgentGatewayEnrollment {
  token_id?: string;
  token_ref?: string;
  token_id_omitted?: boolean;
  workspace_id: string;
  agent_id: string;
  scopes: string[];
  status: string;
  label: string;
  heartbeat_timeout_sec: number;
  created_at: string;
  expires_at: string;
  revoked_at?: string | null;
  last_used_at?: string | null;
  last_heartbeat_at?: string | null;
  heartbeat_state: "fresh" | "stale" | "never_seen" | string;
}

export interface AgentGatewayEnrollmentListPayload {
  enrollments: AgentGatewayEnrollment[];
  valid_scopes: string[];
  token_omitted: boolean;
}

export interface AgentGatewaySession {
  session_id?: string;
  session_ref?: string;
  session_id_omitted?: boolean;
  parent_token_id?: string | null;
  parent_token_ref?: string | null;
  workspace_id: string;
  agent_id: string;
  scopes: string[];
  status: string;
  session_state: string;
  created_at: string;
  expires_at: string;
  revoked_at?: string | null;
  last_used_at?: string | null;
}

export interface AgentGatewaySessionListPayload {
  sessions: AgentGatewaySession[];
  valid_scopes: string[];
  token_omitted: boolean;
}

export interface AgentGatewayStatusPayload {
  provider: string;
  status: string;
  auth: {
    mode: string;
    authenticated: boolean;
    agent_id: string;
    workspace_id: string;
    scopes: string[];
    token_id?: string;
    token_status?: string;
    heartbeat_state?: string;
    heartbeat_timeout_sec?: number;
    expires_at?: string;
    last_used_at?: string | null;
    last_heartbeat_at?: string | null;
    session_id?: string;
    parent_token_id?: string;
    session_expires_at?: string;
  };
  valid_scopes: string[];
  token_omitted: boolean;
}

export interface SecurityProductionGate {
  id: string;
  label: string;
  status: string;
  ok: boolean;
  detail: string;
  next_action: string;
}

export interface StartupSecurityAssessment {
  ok: boolean;
  status: string;
  host: string;
  deployment_mode: string;
  non_loopback: boolean;
  production_requested: boolean;
  allow_non_loopback: boolean;
  api_key_configured: boolean;
  admin_key_configured: boolean;
  failures: { id: string; message: string }[];
  warnings: { id: string; message: string }[];
  contract: string;
  token_omitted: boolean;
}

export interface SecurityProductionReadinessPayload {
  provider: string;
  operation: string;
  status: string;
  production_ready: boolean;
  production_requested: boolean;
  deployment_mode: string;
  startup_security?: StartupSecurityAssessment;
  auth_mode: string;
  gateway_status_code: number;
  gates: SecurityProductionGate[];
  next_actions: string[];
  contract?: string;
  safety: {
    read_only: boolean;
    live_execution_performed: boolean;
    token_omitted: boolean;
    raw_prompt_omitted: boolean;
  };
  token_omitted: boolean;
  live_execution_performed: boolean;
}

export interface DemoReadinessShot {
  id: string;
  label: string;
  route?: string;
  command?: string;
  status: string;
  ok: boolean;
  detail: string;
  next_action: string;
}

export interface DemoProductEvidencePhase {
  id: string;
  label: string;
  command: string;
  route?: string;
  manual_only: boolean;
  requires_confirm_live: boolean;
  requires_isolated_db: boolean;
  summary: string;
}

export interface DemoProductEvidencePacket {
  id: string;
  operation: string;
  status: string;
  summary: {
    phase_count: number;
    manual_live_phase_count: number;
    isolated_db_phase_count: number;
    copyable_command_count: number;
  };
  phases: DemoProductEvidencePhase[];
  references?: Record<string, string>;
  contract?: string;
  safety: {
    read_only: boolean;
    ledger_mutated: boolean;
    live_execution_performed: boolean;
    token_omitted: boolean;
    raw_prompt_omitted: boolean;
    requires_confirm_live: boolean;
    requires_isolated_db_for_live: boolean;
  };
  token_omitted: boolean;
  live_execution_performed: boolean;
}

export interface DemoReadinessPayload {
  provider: string;
  operation: string;
  status: string;
  demo_ready: boolean;
  production_ready: boolean;
  summary: {
    shot_count: number;
    ready_shots: number;
    blocker_count: number;
    warning_count: number;
    closed_loop_runs: number;
    customer_worker_artifacts: number;
    fleet_lanes: number;
    ready_inbox_items: number;
  };
  shots: DemoReadinessShot[];
  next_actions: string[];
  references?: Record<string, string>;
  product_evidence_packet?: DemoProductEvidencePacket;
  contract?: string;
  safety: {
    read_only: boolean;
    ledger_mutated: boolean;
    live_execution_performed: boolean;
    token_omitted: boolean;
    raw_prompt_omitted: boolean;
  };
  token_omitted: boolean;
  live_execution_performed: boolean;
}

export interface AgentGatewayEnrollmentCreateInput {
  agent_id: string;
  name: string;
  role?: string;
  runtime_type: string;
  workspace_id?: string;
  label?: string;
  scopes: string[];
  ttl_days?: number;
  heartbeat_timeout_sec?: number;
}

export interface AgentGatewayEnrollmentCreateResult {
  created: boolean;
  token_id: string;
  agent_id: string;
  workspace_id: string;
  scopes: string[];
  expires_at: string;
  heartbeat_timeout_sec: number;
  token: string;
  note: string;
  next_steps?: {
    token_policy: string;
    base_url: string;
    agent_id: string;
    workspace_id: string;
    adapter: string;
	    install?: string;
	    env: string[];
	    verify: string;
	    start_check?: string;
	    loop_launch_brief?: string;
	    method_gate_contract?: {
	      operation?: string;
	      source?: string;
	      adapter?: string;
	      method?: string;
	      first_read?: string;
	      phase_commands?: Record<string, string | null | undefined>;
	      required_gates?: string[];
	      safety?: {
	        copy_only?: boolean;
	        server_executes_shell?: boolean;
	        live_execution_requires_confirm_run?: boolean;
	        token_omitted?: boolean;
	      };
	      token_omitted?: boolean;
	    };
	    preflight?: string;
    session?: string;
    heartbeat: string;
    run_once: string;
    run_loop: string;
    launchd_template?: string;
    systemd_template?: string;
    repo_fallback_run_once?: string;
    repo_fallback_run_loop?: string;
    notes: string[];
    token_omitted: boolean;
  };
}

export interface AgentGatewayEnrollmentRequestResult {
  request: {
    request_id: string;
    approval_id: string;
    task_id: string;
    run_id: string;
    workspace_id: string;
    agent_id: string;
    name: string;
    runtime_type: string;
    status: string;
    scopes: string[];
  };
  approval: Approval;
  token_issued: boolean;
  token_omitted: boolean;
}

export interface AgentGatewayEnrollmentRotateResult extends AgentGatewayEnrollmentCreateResult {
  rotated: boolean;
  rotated_from_token_id: string;
  revoked: number;
}

export interface AgentGatewayEnrollmentRevokeResult {
  revoked: number;
  changed: number;
  tokens: string[];
  sessions_revoked?: number;
  sessions?: string[];
}

export interface AgentGatewayEnrollmentPolicyPreview {
  provider: string;
  operation: string;
  status: string;
  workspace_id: string;
  runtime_type: string;
  deployment_mode: string;
  production_security_requested: boolean;
  admin_key_configured: boolean;
  policy: string;
  risk_level: string;
  approval_recommended: boolean;
  recommended_path: string;
  direct_create_allowed: boolean;
  approval_request_required: boolean;
  deployment_policy_summary: string;
  scope_count: number;
  scopes: string[];
  invalid_scopes: string[];
  privileged_scopes: string[];
  worker_write_scopes: string[];
  missing_worker_scopes: string[];
  gates: { id: string; ok: boolean; status: string; summary: string }[];
  next_actions: string[];
  safety: {
    read_only: boolean;
    ledger_mutated: boolean;
    live_execution_performed: boolean;
    token_omitted: boolean;
    raw_prompt_omitted: boolean;
  };
  token_omitted: boolean;
  live_execution_performed: boolean;
}

export interface AgentGatewaySessionRevokeResult {
  revoked: number;
  sessions: string[];
  token_omitted: boolean;
}

function asArray<T>(value: unknown): T[] {
  return Array.isArray(value) ? (value as T[]) : [];
}

function parseJsonArray(value: unknown): string[] {
  if (Array.isArray(value)) return value.map(String);
  if (typeof value !== "string" || !value) return [];
  try {
    const parsed = JSON.parse(value);
    return Array.isArray(parsed) ? parsed.map(String) : [];
  } catch {
    return value.split(",").map((item) => item.trim()).filter(Boolean);
  }
}

function parseJsonObject(value: unknown): Record<string, unknown> {
  if (typeof value === "object" && value !== null && !Array.isArray(value)) {
    return value as Record<string, unknown>;
  }
  if (typeof value !== "string" || !value.trim()) return {};
  try {
    const parsed = JSON.parse(value);
    return typeof parsed === "object" && parsed !== null && !Array.isArray(parsed) ? parsed as Record<string, unknown> : {};
  } catch {
    return {};
  }
}

function numberValue(value: unknown, fallback = 0): number {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

function boolValue(value: unknown): boolean {
  return value === true || value === 1 || value === "1" || value === "true";
}

export async function apiJson<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await humanAwareFetch(path, init);
  if (!res.ok) {
    throw new Error(`${res.status} ${res.statusText}: ${await res.text()}`);
  }
  return res.json() as Promise<T>;
}

async function optionalApiJson<T>(path: string, fallback: T): Promise<T> {
  try {
    const res = await humanAwareFetch(path);
    if (!res.ok) {
      return fallback;
    }
    return res.json() as Promise<T>;
  } catch (error) {
    if (import.meta.env.DEV) {
      console.warn(`Optional AgentOps endpoint unavailable: ${path}`, error);
    }
    return fallback;
  }
}

async function apiJsonWithStatuses<T>(path: string, init: RequestInit | undefined, acceptedStatuses: number[]): Promise<T> {
  const res = await humanAwareFetch(path, init);
  if (!res.ok && !acceptedStatuses.includes(res.status)) {
    throw new Error(`${res.status} ${res.statusText}: ${await res.text()}`);
  }
  return res.json() as Promise<T>;
}

export function useLiveData<T>(loader: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await loader());
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, deps);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return { data, setData, loading, error, refresh };
}

export function normalizeAgent(row: Record<string, unknown>, perf?: DashboardMetrics["agent_performance_summary"][number]): Agent {
  return {
    agent_id: String(row.agent_id || perf?.agent_id || ""),
    name: String(row.name || perf?.name || "Unnamed Agent"),
    role: String(row.role || "Agent"),
    description: String(row.description || ""),
    runtime_type: String(row.runtime_type || perf?.runtime_type || "mock") as Agent["runtime_type"],
    model_provider: String(row.model_provider || "unknown"),
    model_name: String(row.model_name || "unknown"),
    status: String(row.status || "idle") as Agent["status"],
    permission_level: String(row.permission_level || "standard"),
    allowed_tools: parseJsonArray(row.allowed_tools),
    budget_limit_usd: numberValue(row.budget_limit_usd, 0),
    budget_used_usd: numberValue(perf?.total_cost_usd, 0),
    owner_user_id: String(row.owner_user_id || "usr_founder"),
    success_rate: numberValue(perf?.success_rate, 0),
    run_count: numberValue(perf?.total_runs, 0),
    failure_count: numberValue(perf?.failures, 0),
    approval_count: numberValue(perf?.approval_required_count, 0),
    created_at: String(row.created_at || ""),
    updated_at: String(row.updated_at || row.created_at || ""),
  };
}

export function normalizeTask(row: Record<string, unknown>): Task {
  return {
    task_id: String(row.task_id || ""),
    title: String(row.title || "Untitled task"),
    description: String(row.description || ""),
    requester_id: String(row.requester_id || "usr_founder"),
    owner_agent_id: String(row.owner_agent_id || ""),
    collaborator_agent_ids: parseJsonArray(row.collaborator_agent_ids),
    status: String(row.status || "planned") as Task["status"],
    priority: String(row.priority || "medium") as Task["priority"],
    due_date: String(row.due_date || ""),
    acceptance_criteria: String(row.acceptance_criteria || ""),
    risk_level: String(row.risk_level || "medium") as Task["risk_level"],
    budget_limit_usd: numberValue(row.budget_limit_usd, 0),
    created_at: String(row.created_at || ""),
    updated_at: String(row.updated_at || row.created_at || ""),
  };
}

export function normalizeRun(row: Record<string, unknown>): Run {
  return {
    run_id: String(row.run_id || ""),
    task_id: String(row.task_id || ""),
    agent_id: String(row.agent_id || ""),
    runtime_type: String(row.runtime_type || "mock") as Run["runtime_type"],
    status: String(row.status || "unknown"),
    started_at: String(row.started_at || row.created_at || ""),
    ended_at: row.ended_at ? String(row.ended_at) : null,
    duration_ms: numberValue(row.duration_ms, 0),
    input_summary: String(row.input_summary || ""),
    output_summary: String(row.output_summary || ""),
    model_provider: String(row.model_provider || ""),
    model_name: String(row.model_name || ""),
    input_tokens: numberValue(row.input_tokens, 0),
    output_tokens: numberValue(row.output_tokens, 0),
    reasoning_tokens: numberValue(row.reasoning_tokens, 0),
    cost_usd: numberValue(row.cost_usd, 0),
    error_type: row.error_type ? String(row.error_type) : null,
    error_message: row.error_message ? String(row.error_message) : null,
    trace_id: String(row.trace_id || ""),
    parent_run_id: row.parent_run_id ? String(row.parent_run_id) : null,
    delegation_id: row.delegation_id ? String(row.delegation_id) : null,
    approval_required: boolValue(row.approval_required),
    created_at: String(row.created_at || row.started_at || ""),
  };
}

export function normalizeApproval(row: Record<string, unknown>): Approval {
  return {
    approval_id: String(row.approval_id || ""),
    task_id: String(row.task_id || ""),
    run_id: String(row.run_id || ""),
    tool_call_id: String(row.tool_call_id || ""),
    requested_by_agent_id: String(row.requested_by_agent_id || ""),
    approver_user_id: row.approver_user_id ? String(row.approver_user_id) : null,
    decision: String(row.decision || "pending") as Approval["decision"],
    reason: String(row.reason || ""),
    expires_at: String(row.expires_at || row.created_at || ""),
    created_at: String(row.created_at || ""),
    decided_at: row.decided_at ? String(row.decided_at) : null,
  };
}

export function normalizeMemory(row: Record<string, unknown>): Memory {
  return {
    memory_id: String(row.memory_id || ""),
    scope: String(row.scope || "project") as Memory["scope"],
    memory_type: String(row.memory_type || "note"),
    canonical_text: String(row.canonical_text || ""),
    source_type: String(row.source_type || ""),
    confidence: numberValue(row.confidence, 0),
    review_status: String(row.review_status || "candidate") as Memory["review_status"],
    task_id: row.task_id ? String(row.task_id) : null,
    agent_id: row.agent_id ? String(row.agent_id) : null,
    created_at: String(row.created_at || ""),
    updated_at: String(row.updated_at || row.created_at || ""),
  };
}

export function normalizeEvaluation(row: Record<string, unknown>): Evaluation {
  return {
    evaluation_id: String(row.evaluation_id || ""),
    task_id: String(row.task_id || ""),
    run_id: String(row.run_id || ""),
    agent_id: String(row.agent_id || ""),
    evaluator_type: String(row.evaluator_type || "rule"),
    score: numberValue(row.score, 0),
    pass_fail: String(row.pass_fail || "fail") as Evaluation["pass_fail"],
    notes: String(row.notes || ""),
    created_at: String(row.created_at || ""),
  };
}

export function normalizeEvaluationCaseCandidate(row: Record<string, unknown>): EvaluationCaseCandidate {
  const rubric = typeof row.rubric === "object" && row.rubric !== null ? row.rubric as Record<string, unknown> : {};
  return {
    case_id: String(row.case_id || ""),
    workspace_id: String(row.workspace_id || "local-demo"),
    source_type: String(row.source_type || "manual"),
    source_ref: row.source_ref ? String(row.source_ref) : null,
    task_id: row.task_id ? String(row.task_id) : null,
    run_id: row.run_id ? String(row.run_id) : null,
    artifact_id: row.artifact_id ? String(row.artifact_id) : null,
    evaluation_id: row.evaluation_id ? String(row.evaluation_id) : null,
    agent_id: row.agent_id ? String(row.agent_id) : null,
    case_type: String(row.case_type || "quality"),
    title: String(row.title || row.case_id || "Evaluation case"),
    input_summary: row.input_summary ? String(row.input_summary) : null,
    expected_output_summary: row.expected_output_summary ? String(row.expected_output_summary) : null,
    failure_mode: row.failure_mode ? String(row.failure_mode) : null,
    confidence: numberValue(row.confidence, 0),
    review_status: String(row.review_status || "candidate"),
    created_by_agent_id: row.created_by_agent_id ? String(row.created_by_agent_id) : null,
    owner_user_id: row.owner_user_id ? String(row.owner_user_id) : null,
    created_at: String(row.created_at || ""),
    updated_at: String(row.updated_at || row.created_at || ""),
    rubric,
    token_omitted: boolValue(row.token_omitted),
  };
}

export function normalizeEvaluationCaseRun(row: Record<string, unknown>): EvaluationCaseRun {
  const checks = typeof row.checks === "object" && row.checks !== null ? row.checks as Record<string, unknown> : {};
  return {
    case_run_id: row.case_run_id ? String(row.case_run_id) : undefined,
    case_id: String(row.case_id || ""),
    workspace_id: row.workspace_id ? String(row.workspace_id) : undefined,
    run_id: row.run_id ? String(row.run_id) : undefined,
    evaluation_id: row.evaluation_id ? String(row.evaluation_id) : undefined,
    artifact_id: row.artifact_id ? String(row.artifact_id) : null,
    runner_type: String(row.runner_type || "rule"),
    status: String(row.status || "preview"),
    score: numberValue(row.score, 0),
    pass_fail: String(row.pass_fail || "fail") === "pass" ? "pass" : "fail",
    review_status: row.review_status ? String(row.review_status) : "open",
    reviewed_by_user_id: row.reviewed_by_user_id ? String(row.reviewed_by_user_id) : null,
    review_note: row.review_note ? String(row.review_note) : null,
    reviewed_at: row.reviewed_at ? String(row.reviewed_at) : null,
    checks,
    case_title: row.case_title ? String(row.case_title) : undefined,
    case_type: row.case_type ? String(row.case_type) : undefined,
    task_id: row.task_id ? String(row.task_id) : null,
    source_type: row.source_type ? String(row.source_type) : null,
    source_ref: row.source_ref ? String(row.source_ref) : null,
    created_by_agent_id: row.created_by_agent_id ? String(row.created_by_agent_id) : null,
    created_at: row.created_at ? String(row.created_at) : undefined,
    token_omitted: boolValue(row.token_omitted),
  };
}

export function normalizeToolCall(row: Record<string, unknown>): ToolCall {
  return {
    tool_call_id: String(row.tool_call_id || ""),
    run_id: String(row.run_id || ""),
    agent_id: String(row.agent_id || ""),
    tool_name: String(row.tool_name || ""),
    tool_version: String(row.tool_version || ""),
    tool_category: String(row.tool_category || "custom") as ToolCall["tool_category"],
    normalized_args_json: String(row.normalized_args_json || "{}"),
    target_resource: String(row.target_resource || ""),
    risk_level: String(row.risk_level || "low") as ToolCall["risk_level"],
    status: String(row.status || ""),
    result_summary: String(row.result_summary || ""),
    started_at: String(row.started_at || row.created_at || ""),
    ended_at: String(row.ended_at || ""),
    created_at: String(row.created_at || ""),
  };
}

export function normalizeAudit(row: Record<string, unknown>): AuditLog {
  return {
    audit_id: String(row.audit_id || ""),
    actor_type: String(row.actor_type || "system") as AuditLog["actor_type"],
    actor_id: String(row.actor_id || "system"),
    action: String(row.action || ""),
    entity_type: String(row.entity_type || ""),
    entity_id: String(row.entity_id || ""),
    metadata_json: JSON.stringify(row.metadata_json || row.metadata || {}),
    created_at: String(row.created_at || ""),
  };
}

export function normalizeConnector(row: Record<string, unknown>): RuntimeConnector {
  const capabilityManifest = parseJsonObject(row.capability_manifest || row.capability_manifest_json);
  return {
    connector_id: String(row.runtime_connector_id || row.connector_id || ""),
    provider: String(row.provider || ""),
    mode: String(row.connector_type || row.mode || ""),
    status: String(row.status || "unknown"),
    last_checked: String(row.last_health_at || row.updated_at || row.created_at || new Date().toISOString()),
    real_run_enabled: boolValue(row.allow_real_run),
    confirm_required: boolValue(row.require_confirm_run),
    trust_status: String(row.trust_status || "trusted"),
    trust_note: row.trust_note ? String(row.trust_note) : undefined,
    trust_updated_at: row.trust_updated_at ? String(row.trust_updated_at) : undefined,
    observation_level: String(row.observation_level || capabilityManifest.observation_level || ""),
    risk_floor: String(row.risk_floor || capabilityManifest.risk_floor || ""),
    commercial_readiness: String(row.commercial_readiness || capabilityManifest.commercial_readiness || ""),
    capability_policy_hash: String(row.capability_policy_hash || capabilityManifest.manifest_hash || ""),
    capability_manifest: capabilityManifest,
    endpoint: String(row.base_url || row.binary_path || ""),
    import_count: undefined,
    last_event: row.last_error ? String(row.last_error) : undefined,
  };
}

export async function loadDashboard(): Promise<DashboardMetrics> {
  const raw = await apiJson<Record<string, unknown>>("/dashboard/metrics");
  return {
    ...(raw as unknown as DashboardMetrics),
    recent_runs: asArray<Record<string, unknown>>(raw.recent_runs).map(normalizeRun),
  };
}

export async function loadAgents(metrics?: DashboardMetrics): Promise<Agent[]> {
  const raw = await apiJson<Record<string, unknown>[]>("/agents");
  const perf = new Map((metrics?.agent_performance_summary || []).map((item) => [item.agent_id, item]));
  return raw.map((row) => normalizeAgent(row, perf.get(String(row.agent_id))));
}

export async function loadTasks(): Promise<Task[]> {
  return (await apiJson<Record<string, unknown>[]>("/tasks")).map(normalizeTask);
}

function ledgerListPath(base: string, query = "", defaultLimit = 100): string {
  const params = new URLSearchParams(query.startsWith("?") ? query.slice(1) : query);
  if (!params.has("limit")) params.set("limit", String(defaultLimit));
  return `${base}?${params.toString()}`;
}

export async function loadRuns(query = ""): Promise<Run[]> {
  return (await apiJson<Record<string, unknown>[]>(ledgerListPath("/runs", query, 100))).map(normalizeRun);
}

export async function loadEvaluations(): Promise<Evaluation[]> {
  return (await apiJson<Record<string, unknown>[]>("/evaluations")).map(normalizeEvaluation);
}

export async function loadEvaluationCaseCandidates(input: {
  status?: string;
  limit?: number;
  run_id?: string;
  task_id?: string;
  artifact_id?: string;
} = {}): Promise<EvaluationCaseCandidatesPayload> {
  const params = new URLSearchParams();
  params.set("status", input.status || "candidate");
  params.set("limit", String(input.limit || 25));
  if (input.run_id) params.set("run_id", input.run_id);
  if (input.task_id) params.set("task_id", input.task_id);
  if (input.artifact_id) params.set("artifact_id", input.artifact_id);
  const raw = await optionalApiJson<Record<string, unknown>>(`/evaluation-cases?${params.toString()}`, {
    provider: "agentops-evaluation",
    operation: "evaluation_case_candidates",
    status: "unavailable",
    workspace_id: "local-demo",
    limit: input.limit || 25,
    summary: {},
    cases: [],
    next_actions: [],
    safety: {
      read_only: true,
      ledger_mutated: false,
      live_execution_performed: false,
      raw_prompt_omitted: true,
      raw_response_omitted: true,
      token_omitted: true,
    },
    token_omitted: true,
  });
  const summaryRaw = typeof raw.summary === "object" && raw.summary !== null ? raw.summary as Record<string, unknown> : {};
  const safetyRaw = typeof raw.safety === "object" && raw.safety !== null ? raw.safety as Record<string, unknown> : {};
  return {
    provider: String(raw.provider || "agentops-evaluation"),
    operation: String(raw.operation || "evaluation_case_candidates"),
    status: String(raw.status || "unknown"),
    workspace_id: String(raw.workspace_id || "local-demo"),
    limit: numberValue(raw.limit, input.limit || 25),
    summary: {
      candidate: numberValue(summaryRaw.candidate, 0),
      approved: numberValue(summaryRaw.approved, 0),
      rejected: numberValue(summaryRaw.rejected, 0),
      returned: numberValue(summaryRaw.returned, 0),
    },
    cases: asArray<Record<string, unknown>>(raw.cases).map(normalizeEvaluationCaseCandidate),
    next_actions: asArray(raw.next_actions).map(String),
    safety: {
      read_only: boolValue(safetyRaw.read_only),
      ledger_mutated: boolValue(safetyRaw.ledger_mutated),
      live_execution_performed: boolValue(safetyRaw.live_execution_performed),
      raw_prompt_omitted: boolValue(safetyRaw.raw_prompt_omitted),
      raw_response_omitted: boolValue(safetyRaw.raw_response_omitted),
      token_omitted: boolValue(safetyRaw.token_omitted),
    },
    token_omitted: boolValue(raw.token_omitted),
  };
}

function normalizeEvaluationCaseRunPayload(raw: Record<string, unknown>, fallbackStatus = "unknown"): EvaluationCaseRunPayload {
  const summaryRaw = typeof raw.summary === "object" && raw.summary !== null ? raw.summary as Record<string, unknown> : {};
  const safetyRaw = typeof raw.safety === "object" && raw.safety !== null ? raw.safety as Record<string, unknown> : {};
  return {
    provider: String(raw.provider || "agentops-evaluation"),
    operation: String(raw.operation || "evaluation_case_runs"),
    status: String(raw.status || fallbackStatus),
    created: raw.created === undefined ? undefined : boolValue(raw.created),
    workspace_id: String(raw.workspace_id || "local-demo"),
    limit: raw.limit === undefined ? undefined : numberValue(raw.limit, 0),
    summary: {
      selected: summaryRaw.selected === undefined ? undefined : numberValue(summaryRaw.selected, 0),
      planned: summaryRaw.planned === undefined ? undefined : numberValue(summaryRaw.planned, 0),
      skipped: summaryRaw.skipped === undefined ? undefined : numberValue(summaryRaw.skipped, 0),
      total: summaryRaw.total === undefined ? undefined : numberValue(summaryRaw.total, 0),
      returned: summaryRaw.returned === undefined ? undefined : numberValue(summaryRaw.returned, 0),
      created: summaryRaw.created === undefined ? undefined : numberValue(summaryRaw.created, 0),
      passed: summaryRaw.passed === undefined ? undefined : numberValue(summaryRaw.passed, 0),
      failed: summaryRaw.failed === undefined ? undefined : numberValue(summaryRaw.failed, 0),
      min_score: summaryRaw.min_score === undefined ? undefined : numberValue(summaryRaw.min_score, 0),
    },
    planned_runs: asArray<Record<string, unknown>>(raw.planned_runs).map(normalizeEvaluationCaseRun),
    case_runs: asArray<Record<string, unknown>>(raw.case_runs).map(normalizeEvaluationCaseRun),
    skipped: asArray<Record<string, unknown>>(raw.skipped).map(normalizeEvaluationCaseRun),
    next_actions: asArray(raw.next_actions).map(String),
    safety: {
      read_only: safetyRaw.read_only === undefined ? undefined : boolValue(safetyRaw.read_only),
      ledger_mutated: boolValue(safetyRaw.ledger_mutated),
      live_execution_performed: boolValue(safetyRaw.live_execution_performed),
      raw_prompt_omitted: boolValue(safetyRaw.raw_prompt_omitted),
      raw_response_omitted: boolValue(safetyRaw.raw_response_omitted),
      token_omitted: boolValue(safetyRaw.token_omitted),
    },
    token_omitted: boolValue(raw.token_omitted),
  };
}

export async function loadEvaluationCaseRuns(input: {
  limit?: number;
  case_id?: string;
  run_id?: string;
  task_id?: string;
  pass_fail?: "pass" | "fail";
} = {}): Promise<EvaluationCaseRunPayload> {
  const params = new URLSearchParams();
  params.set("limit", String(input.limit || 25));
  if (input.case_id) params.set("case_id", input.case_id);
  if (input.run_id) params.set("run_id", input.run_id);
  if (input.task_id) params.set("task_id", input.task_id);
  if (input.pass_fail) params.set("pass_fail", input.pass_fail);
  const raw = await optionalApiJson<Record<string, unknown>>(`/evaluation-case-runs?${params.toString()}`, {
    provider: "agentops-evaluation",
    operation: "evaluation_case_runs",
    status: "unavailable",
    workspace_id: "local-demo",
    summary: {},
    case_runs: [],
    safety: {
      read_only: true,
      ledger_mutated: false,
      live_execution_performed: false,
      raw_prompt_omitted: true,
      raw_response_omitted: true,
      token_omitted: true,
    },
    token_omitted: true,
  });
  return normalizeEvaluationCaseRunPayload(raw, "unavailable");
}

export async function runEvaluationCases(input: {
  case_ids?: string[];
  case_type?: string;
  status?: string;
  runner_type?: "rule" | "llm_mock";
  task_id?: string;
  run_id?: string;
  artifact_id?: string;
  limit?: number;
  min_score?: number;
  confirm_run?: boolean;
} = {}): Promise<EvaluationCaseRunPayload> {
  const raw = await apiJson<Record<string, unknown>>("/evaluation-cases/run", {
    method: "POST",
    body: JSON.stringify({
      case_ids: input.case_ids || [],
      case_type: input.case_type,
      status: input.status || "approved",
      runner_type: input.runner_type || "rule",
      task_id: input.task_id,
      run_id: input.run_id,
      artifact_id: input.artifact_id,
      limit: input.limit || 10,
      min_score: input.min_score ?? 0.75,
      confirm_run: Boolean(input.confirm_run),
    }),
  });
  return normalizeEvaluationCaseRunPayload(raw, input.confirm_run ? "completed" : "preview");
}

export async function loadApprovals(): Promise<Approval[]> {
  return (await apiJson<Record<string, unknown>[]>("/approvals")).map(normalizeApproval);
}

export async function loadToolCalls(): Promise<ToolCall[]> {
  return (await apiJson<Record<string, unknown>[]>(ledgerListPath("/tool-calls", "", 150))).map(normalizeToolCall);
}

export async function loadMemories(): Promise<Memory[]> {
  return (await apiJson<Record<string, unknown>[]>("/memories")).map(normalizeMemory);
}

export async function loadRuntimeConnectors(): Promise<RuntimeConnector[]> {
  return (await apiJson<Record<string, unknown>[]>("/runtime-connectors")).map(normalizeConnector);
}

export async function loadCommercialConfigStatus(): Promise<CommercialConfigStatusPayload> {
  const raw = await optionalApiJson<Record<string, unknown>>("/commercial/config-status", {
    provider: "agentops-commercial-config",
    operation: "commercial_config_status",
    status: "unavailable",
    configured: false,
    sources: {},
    entitlements: {},
    retention: {},
    safety: {
      read_only: true,
      live_execution_performed: false,
      billing_call_performed: false,
      cleanup_execution_performed: false,
      raw_config_omitted: true,
      token_omitted: true,
    },
    failures: ["commercial config status endpoint unavailable"],
  });
  const entitlementsRaw = parseJsonObject(raw.entitlements);
  const retentionRaw = parseJsonObject(raw.retention);
  const safetyRaw = parseJsonObject(raw.safety);
  const sourcesRaw = parseJsonObject(raw.sources);
  const windowsRaw = parseJsonObject(retentionRaw.windows);
  return {
    provider: String(raw.provider || "agentops-commercial-config"),
    operation: String(raw.operation || "commercial_config_status"),
    status: String(raw.status || "unknown"),
    configured: boolValue(raw.configured),
    sources: {
      entitlements: parseJsonObject(sourcesRaw.entitlements),
      retention: parseJsonObject(sourcesRaw.retention),
    },
    entitlements: {
      schema_version: entitlementsRaw.schema_version ? String(entitlementsRaw.schema_version) : undefined,
      edition: entitlementsRaw.edition ? String(entitlementsRaw.edition) : undefined,
      billing_provider: entitlementsRaw.billing_provider ? String(entitlementsRaw.billing_provider) : undefined,
      billing_calls_enabled: boolValue(entitlementsRaw.billing_calls_enabled),
      enabled_capabilities: asArray<unknown>(entitlementsRaw.enabled_capabilities).map(String),
      disabled_capabilities: asArray<unknown>(entitlementsRaw.disabled_capabilities).map(String),
    },
    retention: {
      schema_version: retentionRaw.schema_version ? String(retentionRaw.schema_version) : undefined,
      windows: {
        free_local_days: numberValue(windowsRaw.free_local_days, 0),
        pro_workspace_days: numberValue(windowsRaw.pro_workspace_days, 0),
        max_retention_days: numberValue(windowsRaw.max_retention_days, 0),
      },
      cleanup_approval_required: boolValue(retentionRaw.cleanup_approval_required),
      legal_hold_required_before_cleanup: boolValue(retentionRaw.legal_hold_required_before_cleanup),
      cleanup_execution_enabled: boolValue(retentionRaw.cleanup_execution_enabled),
      legal_hold_registry_configured: boolValue(retentionRaw.legal_hold_registry_configured),
      legal_hold_registry_example_only: boolValue(retentionRaw.legal_hold_registry_example_only),
    },
    safety: {
      read_only: boolValue(safetyRaw.read_only),
      live_execution_performed: boolValue(safetyRaw.live_execution_performed),
      billing_call_performed: boolValue(safetyRaw.billing_call_performed),
      cleanup_execution_performed: boolValue(safetyRaw.cleanup_execution_performed),
      raw_config_omitted: boolValue(safetyRaw.raw_config_omitted),
      token_omitted: boolValue(safetyRaw.token_omitted),
    },
    failures: asArray<unknown>(raw.failures).map(String),
  };
}

export async function updateRuntimeConnectorTrust(connectorId: string, input: { trust_status: "trusted" | "review_required" | "blocked"; trust_note?: string }): Promise<{ connector: Record<string, unknown>; token_omitted: boolean }> {
  return apiJson<{ connector: Record<string, unknown>; token_omitted: boolean }>(`/runtime-connectors/${encodeURIComponent(connectorId)}/trust`, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function loadAudit(): Promise<AuditLog[]> {
  return (await apiJson<Record<string, unknown>[]>(ledgerListPath("/audit", "", 150))).map(normalizeAudit);
}

export async function loadRunDetail(id: string): Promise<RunDetailPayload> {
  const raw = await apiJson<Record<string, unknown>>(`/runs/${encodeURIComponent(id)}`);
  return {
    run: normalizeRun(raw.run as Record<string, unknown>),
    tool_calls: asArray<Record<string, unknown>>(raw.tool_calls).map(normalizeToolCall),
    approvals: asArray<Record<string, unknown>>(raw.approvals).map(normalizeApproval),
    evaluations: asArray<Record<string, unknown>>(raw.evaluations).map(normalizeEvaluation),
    artifacts: asArray(raw.artifacts),
    evaluation_case_runs: asArray<Record<string, unknown>>(raw.evaluation_case_runs).map(normalizeEvaluationCaseRun),
  };
}

export async function loadRunEvidenceGraph(id: string): Promise<RunEvidenceGraphPayload> {
  const raw = await optionalApiJson<Record<string, unknown>>(`/runs/${encodeURIComponent(id)}/evidence-graph`, {
    provider: "agentops-run-graph",
    operation: "work_delivery_graph_readback",
    status: "unavailable",
    run_id: id,
    evidence_counts: {},
    nodes: [],
    edges: [],
    safety: {
      read_only: true,
      ledger_mutated: false,
      live_execution_performed: false,
      raw_prompt_omitted: true,
      raw_response_omitted: true,
      raw_content_omitted: true,
      token_omitted: true,
    },
    token_omitted: true,
  });
  const safetyRaw = parseJsonObject(raw.safety);
  const countsRaw = parseJsonObject(raw.evidence_counts);
  return {
    provider: String(raw.provider || "agentops-run-graph"),
    operation: String(raw.operation || "work_delivery_graph_readback"),
    schema_version: raw.schema_version ? String(raw.schema_version) : undefined,
    status: raw.status ? String(raw.status) : undefined,
    run_id: String(raw.run_id || id),
    task_id: raw.task_id ? String(raw.task_id) : null,
    agent_id: raw.agent_id ? String(raw.agent_id) : null,
    agent_plan_id: raw.agent_plan_id ? String(raw.agent_plan_id) : null,
    plan_hash: raw.plan_hash ? String(raw.plan_hash) : null,
    plan_evidence_manifest_id: raw.plan_evidence_manifest_id ? String(raw.plan_evidence_manifest_id) : null,
    evidence_counts: Object.fromEntries(Object.entries(countsRaw).map(([key, value]) => [key, numberValue(value, 0)])),
    nodes: asArray<Record<string, unknown>>(raw.nodes),
    edges: asArray<Record<string, unknown>>(raw.edges),
    graph_hash: raw.graph_hash ? String(raw.graph_hash) : undefined,
    authority: raw.authority ? String(raw.authority) : undefined,
    safety: {
      read_only: boolValue(safetyRaw.read_only),
      ledger_mutated: boolValue(safetyRaw.ledger_mutated),
      live_execution_performed: boolValue(safetyRaw.live_execution_performed),
      raw_prompt_omitted: boolValue(safetyRaw.raw_prompt_omitted),
      raw_response_omitted: boolValue(safetyRaw.raw_response_omitted),
      raw_content_omitted: boolValue(safetyRaw.raw_content_omitted),
      token_omitted: boolValue(safetyRaw.token_omitted),
    },
    token_omitted: boolValue(raw.token_omitted),
  };
}

export async function loadTaskDetail(id: string): Promise<TaskDetailPayload> {
  const raw = await apiJson<Record<string, unknown>>(`/tasks/${encodeURIComponent(id)}`);
  return {
    task: normalizeTask(raw.task as Record<string, unknown>),
    runs: asArray<Record<string, unknown>>(raw.runs).map(normalizeRun),
    approvals: asArray<Record<string, unknown>>(raw.approvals).map(normalizeApproval),
    evaluations: asArray<Record<string, unknown>>(raw.evaluations).map(normalizeEvaluation),
    memories: asArray<Record<string, unknown>>(raw.memories).map(normalizeMemory),
    artifacts: asArray(raw.artifacts),
    evaluation_case_runs: asArray<Record<string, unknown>>(raw.evaluation_case_runs).map(normalizeEvaluationCaseRun),
  };
}

export async function loadAgentPerformance(id: string): Promise<AgentPerformancePayload> {
  const raw = await apiJson<Record<string, unknown>>(`/agents/${encodeURIComponent(id)}/performance`);
  const perf = {
    agent_id: String((raw.agent as Record<string, unknown>)?.agent_id || id),
    name: String((raw.agent as Record<string, unknown>)?.name || ""),
    runtime_type: String((raw.agent as Record<string, unknown>)?.runtime_type || "mock"),
    total_runs: numberValue(raw.total_runs, 0),
    success_rate: numberValue(raw.success_rate, 0),
    avg_duration_ms: numberValue(raw.avg_duration_ms, 0),
    total_cost_usd: numberValue(raw.total_cost_usd, 0),
    failures: numberValue(raw.failures, 0),
    approval_required_count: numberValue(raw.approval_required_count, 0),
  };
  return {
    agent: normalizeAgent((raw.agent || {}) as Record<string, unknown>, perf),
    total_runs: numberValue(raw.total_runs, 0),
    completed_runs: numberValue(raw.completed_runs, 0),
    failures: numberValue(raw.failures, 0),
    success_rate: numberValue(raw.success_rate, 0),
    avg_duration_ms: numberValue(raw.avg_duration_ms, 0),
    total_cost_usd: numberValue(raw.total_cost_usd, 0),
    approval_required_count: numberValue(raw.approval_required_count, 0),
    recent_error_types: asArray(raw.recent_error_types),
    recent_runs: asArray<Record<string, unknown>>(raw.recent_runs).map(normalizeRun),
  };
}

export async function decideApproval(id: string, decision: "approve" | "reject"): Promise<Approval> {
  const raw = await apiJson<Record<string, unknown>>(`/approvals/${encodeURIComponent(id)}/${decision}`, {
    method: "POST",
    body: JSON.stringify({}),
  });
  const approvalRaw = typeof raw.approval === "object" && raw.approval !== null ? raw.approval as Record<string, unknown> : raw;
  return normalizeApproval(approvalRaw);
}

export async function decideMemory(id: string, decision: "approve" | "reject"): Promise<Memory> {
  const raw = await apiJson<Record<string, unknown>>(`/memories/${encodeURIComponent(id)}/${decision}`, {
    method: "POST",
    body: JSON.stringify({}),
  });
  return normalizeMemory(raw);
}

export async function decideEvaluationCase(id: string, decision: "approve" | "reject"): Promise<EvaluationCaseCandidate> {
  const raw = await apiJson<Record<string, unknown>>(`/evaluation-cases/${encodeURIComponent(id)}/${decision}`, {
    method: "POST",
    body: JSON.stringify({}),
  });
  return normalizeEvaluationCaseCandidate(raw);
}

export async function runLocalBrief(confirmRun = false): Promise<LocalBriefResult> {
  return apiJson<LocalBriefResult>("/workflows/local-brief", {
    method: "POST",
    body: JSON.stringify(confirmRun ? { confirm_run: true } : {}),
  });
}

export async function runCustomerTaskWorkflow(input: CustomerTaskWorkflowInput): Promise<CustomerTaskWorkflowResult> {
  return apiJson<CustomerTaskWorkflowResult>("/workflows/customer-task", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function runCustomerWorkerTaskWorkflow(input: CustomerWorkerTaskWorkflowInput): Promise<CustomerTaskWorkflowResult> {
  return apiJson<CustomerTaskWorkflowResult>("/workflows/customer-worker-task", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function submitCustomerWorkerTaskJob(input: CustomerWorkerTaskWorkflowInput): Promise<WorkflowJobSubmitPayload> {
  return apiJsonWithStatuses<WorkflowJobSubmitPayload>("/workflows/customer-worker-task/submit", {
    method: "POST",
    body: JSON.stringify(input),
  }, [409]);
}

export async function runKbBotProjectWorkflow(): Promise<KbBotProjectWorkflowResult> {
  return apiJson<KbBotProjectWorkflowResult>("/workflows/kb-bot-project", {
    method: "POST",
    body: JSON.stringify({}),
  });
}

export async function loadCustomerTaskTemplates(): Promise<CustomerTaskTemplateListPayload> {
  const raw = await apiJson<Record<string, unknown>>("/workflows/customer-task-templates");
  return {
    templates: asArray<Record<string, unknown>>(raw.templates).map((item) => ({
      template_id: String(item.template_id || ""),
      name: String(item.name || item.template_id || ""),
      name_en: item.name_en ? String(item.name_en) : undefined,
      workflow: String(item.workflow || ""),
      scenario: String(item.scenario || ""),
      status: String(item.status || ""),
      risk_level: String(item.risk_level || "medium"),
      priority: String(item.priority || "medium"),
      description: String(item.description || ""),
      default_title: String(item.default_title || item.name || ""),
      default_description: String(item.default_description || item.description || ""),
      default_acceptance: String(item.default_acceptance || ""),
      agent_roles: asArray(item.agent_roles).map(String),
      required_approvals: asArray(item.required_approvals).map(String),
      safe_defaults: (item.safe_defaults || {}) as Record<string, unknown>,
      entrypoint: String(item.entrypoint || ""),
    })),
    safe_defaults: (raw.safe_defaults || {}) as Record<string, unknown>,
  };
}

export async function runCustomerTaskTemplateWorkflow(input: { template_id: string; confirm_run?: boolean; selected_agent_ids?: string[]; owner_agent_id?: string }): Promise<KbBotProjectWorkflowResult & { template?: Record<string, unknown> }> {
  return apiJson<KbBotProjectWorkflowResult & { template?: Record<string, unknown> }>("/workflows/customer-task-templates/run", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function submitCustomerTaskTemplateJob(input: {
  template_id: string;
  adapter?: "mock" | "hermes" | "openclaw";
  confirm_run?: boolean;
  selected_agent_ids?: string[];
  owner_agent_id?: string;
  worker_agent_id?: string;
  title?: string;
  description?: string;
  acceptance_criteria?: string;
  priority?: string;
  risk_level?: string;
}): Promise<WorkflowJobSubmitPayload> {
  return apiJsonWithStatuses<WorkflowJobSubmitPayload>("/workflows/customer-task-templates/submit", {
    method: "POST",
    body: JSON.stringify(input),
  }, [409]);
}

export async function loadWorkflowJobs(limit = 8): Promise<WorkflowJobListPayload> {
  return optionalApiJson<WorkflowJobListPayload>(`/workflows/jobs?limit=${encodeURIComponent(String(limit))}`, {
    jobs: [],
    token_omitted: true,
  });
}

export async function loadStuckWorkflowJobs(thresholdSec = 900, limit = 25): Promise<WorkflowJobStuckPayload> {
  return optionalApiJson<WorkflowJobStuckPayload>(`/workflows/jobs/stuck?threshold_sec=${encodeURIComponent(String(thresholdSec))}&limit=${encodeURIComponent(String(limit))}`, {
    provider: "agentops-mis",
    threshold_sec: thresholdSec,
    stuck_jobs: [],
    token_omitted: true,
  });
}

export async function loadWorkflowJob(jobId: string): Promise<{ job: WorkflowJob; token_omitted?: boolean }> {
  return apiJson<{ job: WorkflowJob; token_omitted?: boolean }>(`/workflows/jobs/${encodeURIComponent(jobId)}`);
}

export async function markWorkflowJobFailed(jobId: string, reason: string): Promise<WorkflowJobMarkFailedPayload> {
  return apiJson<WorkflowJobMarkFailedPayload>(`/workflows/jobs/${encodeURIComponent(jobId)}/mark-failed`, {
    method: "POST",
    body: JSON.stringify({ reason, actor_id: "usr_operator" }),
  });
}

export async function persistCustomerProjectReportArtifact(projectId: string): Promise<CustomerProjectReportArtifactResult> {
  return apiJson<CustomerProjectReportArtifactResult>(`/workflows/customer-projects/${encodeURIComponent(projectId)}/report-artifact`, {
    method: "POST",
    body: JSON.stringify({}),
  });
}

export async function loadCustomerProjectReport(projectId: string): Promise<CustomerProjectReportPayload> {
  return apiJson<CustomerProjectReportPayload>(`/workflows/customer-projects/${encodeURIComponent(projectId)}/report`);
}

export async function loadCustomerProjects(limit = 25): Promise<CustomerProjectIndexPayload> {
  return apiJson<CustomerProjectIndexPayload>(`/workflows/customer-projects?limit=${encodeURIComponent(String(limit))}`);
}

export async function loadCustomerDeliveryBoard(limit = 12): Promise<CustomerDeliveryBoardPayload> {
  return optionalApiJson<CustomerDeliveryBoardPayload>(`/workflows/customer-delivery-board?limit=${encodeURIComponent(String(limit))}`, {
    provider: "agentops-mis",
    operation: "customer_delivery_board",
    status: "unavailable",
    summary: {
      deliveries: 0,
      ready: 0,
      waiting_approval: 0,
      in_progress: 0,
      needs_attention: 0,
      pending_approvals: 0,
      artifacts: 0,
      verified_plan_evidence_manifests: 0,
      missing_plan_evidence_manifests: 0,
    },
    deliveries: [],
    gates: [],
    next_actions: [],
    safety: {
      read_only: true,
      ledger_mutated: false,
      live_execution_performed: false,
      raw_prompt_omitted: true,
      raw_response_omitted: true,
      token_omitted: true,
    },
    token_omitted: true,
  });
}

export async function loadHermesOpenClawLoopReadback(loopId = "", limit = 10): Promise<HermesOpenClawLoopReadbackPayload> {
  const params = new URLSearchParams();
  if (loopId) params.set("loop_id", loopId);
  params.set("limit", String(limit));
  return optionalApiJson<HermesOpenClawLoopReadbackPayload>(`/workflows/hermes-openclaw-loop?${params.toString()}`, {
    provider: "agentops-mis",
    operation: "hermes_openclaw_loop_readback",
    status: "unavailable",
    runs: [],
    tasks: [],
    artifacts: [],
    agent_plans: [],
    plan_evidence_manifests: [],
    audit_logs: [],
    summary: {},
    token_omitted: true,
  });
}

export async function runHermesOpenClawLoopWorkflow(input: {
  topic: string;
  loop_id?: string;
  rounds?: number;
  mode?: "dry-run" | "live-hermes" | "live-openclaw" | "live-both";
  confirm_live?: boolean;
  resume?: boolean;
  order?: ("hermes" | "openclaw")[];
  request_timeout?: number;
  max_agent_attempts?: number;
  retry_delay_sec?: number;
  simulate_failure_agent?: ("hermes" | "openclaw")[];
}): Promise<HermesOpenClawLoopWorkflowResult> {
  return apiJsonWithStatuses<HermesOpenClawLoopWorkflowResult>("/workflows/hermes-openclaw-loop", {
    method: "POST",
    body: JSON.stringify(input),
  }, [201, 409]);
}

export async function loadWorkerStatus(): Promise<WorkerStatusPayload> {
  const raw = await apiJson<Record<string, unknown>>("/workers/status");
  const remoteHealthRaw = typeof raw.remote_worker_health === "object" && raw.remote_worker_health !== null ? raw.remote_worker_health as Record<string, unknown> : {};
  const fleetHealthRaw = typeof raw.fleet_health === "object" && raw.fleet_health !== null ? raw.fleet_health as Record<string, unknown> : {};
  return {
    provider: String(raw.provider || "agentops-worker"),
    status: String(raw.status || "unknown"),
    worker_count: numberValue(raw.worker_count, 0),
    running_workers: numberValue(raw.running_workers, 0),
    active_service_workers: numberValue(raw.active_service_workers, 0),
    stale_service_workers: numberValue(raw.stale_service_workers, 0),
    execution_capacity_workers: numberValue(raw.execution_capacity_workers, 0),
    recent_completed_runs: numberValue(raw.recent_completed_runs, 0),
    pending_worker_tasks: numberValue(raw.pending_worker_tasks, 0),
    stuck_worker_tasks: numberValue(raw.stuck_worker_tasks, 0),
    stuck_workflow_jobs: numberValue(raw.stuck_workflow_jobs, 0),
    remote_worker_count: numberValue(raw.remote_worker_count, 0),
    total_remote_enrollments: numberValue(raw.total_remote_enrollments, 0),
    active_remote_enrollments: numberValue(raw.active_remote_enrollments, 0),
    fresh_remote_enrollments: numberValue(raw.fresh_remote_enrollments, 0),
    stale_remote_enrollments: numberValue(raw.stale_remote_enrollments, 0),
    never_seen_remote_enrollments: numberValue(raw.never_seen_remote_enrollments, 0),
    active_remote_sessions: numberValue(raw.active_remote_sessions, 0),
    remote_worker_health: normalizeWorkerRemoteHealth(remoteHealthRaw),
    service_workers: asArray<Record<string, unknown>>(raw.service_workers),
    adapter_readiness: typeof raw.adapter_readiness === "object" && raw.adapter_readiness !== null ? raw.adapter_readiness as WorkerAdapterReadinessSummary : undefined,
    fleet_health: normalizeWorkerFleetHealth(fleetHealthRaw),
    daemons: asArray<Record<string, unknown>>(raw.daemons).map(normalizeWorkerDaemon),
    workers: asArray<Record<string, unknown>>(raw.workers).map((row) => normalizeAgent(row)),
    recent_runs: asArray<Record<string, unknown>>(raw.recent_runs).map(normalizeRun),
    recent_tasks: asArray<Record<string, unknown>>(raw.recent_tasks).map(normalizeTask),
    stuck_tasks: asArray<Record<string, unknown>>(raw.stuck_tasks).map((row) => ({
      ...normalizeTask(row),
      age_sec: numberValue(row.age_sec, 0),
      threshold_sec: numberValue(row.threshold_sec, 0),
      running_run_id: row.running_run_id ? String(row.running_run_id) : null,
      running_run_started_at: row.running_run_started_at ? String(row.running_run_started_at) : null,
      stuck_reason: row.stuck_reason ? String(row.stuck_reason) : undefined,
    })),
    stuck_workflow_job_refs: asArray<Record<string, unknown>>(raw.stuck_workflow_job_refs).map((row) => ({
      job_id: String(row.job_id || ""),
      workflow_type: row.workflow_type ? String(row.workflow_type) : undefined,
      status: row.status ? String(row.status) : undefined,
      age_sec: numberValue(row.age_sec, 0),
      stuck_reason: row.stuck_reason ? String(row.stuck_reason) : undefined,
    })).filter((row) => row.job_id),
    recent_events: asArray<Record<string, unknown>>(raw.recent_events),
  };
}

export async function loadWorkerFleet(): Promise<WorkerFleetPayload> {
  const raw = await optionalApiJson<Record<string, unknown>>("/workers/fleet", {
    provider: "agentops-worker",
    operation: "fleet_view",
    status: "unavailable",
    summary: {},
    lanes: [],
    next_actions: [],
    safety: {
      read_only: true,
      live_execution_performed: false,
      token_omitted: true,
      session_id_omitted: true,
      raw_prompt_omitted: true,
    },
    token_omitted: true,
    live_execution_performed: false,
    fallback_reason: "endpoint_not_available",
  });
  const summaryRaw = typeof raw.summary === "object" && raw.summary !== null ? raw.summary as Record<string, unknown> : {};
  const safetyRaw = typeof raw.safety === "object" && raw.safety !== null ? raw.safety as Record<string, unknown> : {};
  return {
    provider: String(raw.provider || "agentops-worker"),
    operation: String(raw.operation || "fleet_view"),
    status: String(raw.status || "unknown"),
    summary: {
      lane_count: numberValue(summaryRaw.lane_count, 0),
      lane_counts: numberRecord(summaryRaw.lane_counts),
      health_counts: numberRecord(summaryRaw.health_counts),
      local_daemon_count: numberValue(summaryRaw.local_daemon_count, 0),
      running_local_daemons: numberValue(summaryRaw.running_local_daemons, 0),
      active_service_workers: numberValue(summaryRaw.active_service_workers, 0),
      stale_service_workers: numberValue(summaryRaw.stale_service_workers, 0),
      execution_capacity_workers: numberValue(summaryRaw.execution_capacity_workers, 0),
      host_managed_workers: numberValue(summaryRaw.host_managed_workers, 0),
      api_managed_daemons: numberValue(summaryRaw.api_managed_daemons, 0),
      remote_worker_count: numberValue(summaryRaw.remote_worker_count, 0),
      fresh_remote_enrollments: numberValue(summaryRaw.fresh_remote_enrollments, 0),
      stale_remote_enrollments: numberValue(summaryRaw.stale_remote_enrollments, 0),
      never_seen_remote_enrollments: numberValue(summaryRaw.never_seen_remote_enrollments, 0),
      active_remote_sessions: numberValue(summaryRaw.active_remote_sessions, 0),
      stuck_worker_tasks: numberValue(summaryRaw.stuck_worker_tasks, 0),
      stuck_workflow_jobs: numberValue(summaryRaw.stuck_workflow_jobs, 0),
      recommended_adapter: summaryRaw.recommended_adapter ? String(summaryRaw.recommended_adapter) : undefined,
    },
    lanes: asArray<Record<string, unknown>>(raw.lanes).map((lane) => ({
      lane_id: String(lane.lane_id || ""),
      lane_type: String(lane.lane_type || "unknown"),
      adapter: lane.adapter ? String(lane.adapter) : null,
      agent_id: lane.agent_id ? String(lane.agent_id) : null,
      agent_name: lane.agent_name ? String(lane.agent_name) : null,
      workspace_id: lane.workspace_id ? String(lane.workspace_id) : null,
      runtime_type: lane.runtime_type ? String(lane.runtime_type) : null,
      status: String(lane.status || "unknown"),
      health: String(lane.health || "info"),
      heartbeat_state: lane.heartbeat_state ? String(lane.heartbeat_state) : null,
      session_state: lane.session_state ? String(lane.session_state) : null,
      active_session_count: numberValue(lane.active_session_count, 0),
      last_seen_at: lane.last_seen_at ? String(lane.last_seen_at) : null,
      expires_at: lane.expires_at ? String(lane.expires_at) : null,
      scope_count: numberValue(lane.scope_count, 0),
      workload: typeof lane.workload === "object" && lane.workload !== null ? lane.workload as Record<string, unknown> : undefined,
      next_action: lane.next_action ? String(lane.next_action) : undefined,
      safe_ref: lane.safe_ref ? String(lane.safe_ref) : null,
      management_mode: lane.management_mode ? String(lane.management_mode) : null,
      control_allowed: lane.control_allowed === undefined ? undefined : boolValue(lane.control_allowed),
      process_state_verified: lane.process_state_verified === undefined ? undefined : boolValue(lane.process_state_verified),
      token_omitted: boolValue(lane.token_omitted),
      session_id_omitted: boolValue(lane.session_id_omitted),
      token_id_omitted: boolValue(lane.token_id_omitted),
    })).filter((lane) => lane.lane_id),
    next_actions: asArray<unknown>(raw.next_actions).map(String).filter(Boolean),
    contract: raw.contract ? String(raw.contract) : undefined,
    safety: {
      read_only: boolValue(safetyRaw.read_only),
      live_execution_performed: boolValue(safetyRaw.live_execution_performed),
      token_omitted: boolValue(safetyRaw.token_omitted),
      session_id_omitted: boolValue(safetyRaw.session_id_omitted),
      raw_prompt_omitted: boolValue(safetyRaw.raw_prompt_omitted),
    },
    token_omitted: boolValue(raw.token_omitted),
    live_execution_performed: boolValue(raw.live_execution_performed),
  };
}

function normalizeWorkerFleetHygiene(raw: Record<string, unknown>): WorkerFleetHygienePayload {
  const summaryRaw = typeof raw.summary === "object" && raw.summary !== null ? raw.summary as Record<string, unknown> : {};
  const safetyRaw = typeof raw.safety === "object" && raw.safety !== null ? raw.safety as Record<string, unknown> : {};
  return {
    provider: String(raw.provider || "agentops-worker"),
    operation: String(raw.operation || "fleet_hygiene"),
    status: String(raw.status || "unknown"),
    threshold_sec: numberValue(raw.threshold_sec, 900),
    enrollment_age_sec: numberValue(raw.enrollment_age_sec, 900),
    summary: {
      stuck_tasks: numberValue(summaryRaw.stuck_tasks, 0),
      stale_never_seen_enrollments: numberValue(summaryRaw.stale_never_seen_enrollments, 0),
      stale_heartbeat_enrollments: numberValue(summaryRaw.stale_heartbeat_enrollments, 0),
      actions_available: numberValue(summaryRaw.actions_available, 0),
      released_tasks: summaryRaw.released_tasks === undefined ? undefined : numberValue(summaryRaw.released_tasks, 0),
      revoked_enrollments: summaryRaw.revoked_enrollments === undefined ? undefined : numberValue(summaryRaw.revoked_enrollments, 0),
      errors: summaryRaw.errors === undefined ? undefined : numberValue(summaryRaw.errors, 0),
    },
    stuck_tasks: asArray<Record<string, unknown>>(raw.stuck_tasks).map((row) => ({
      ...normalizeTask(row),
      age_sec: numberValue(row.age_sec, 0),
      threshold_sec: numberValue(row.threshold_sec, 0),
      running_run_id: row.running_run_id ? String(row.running_run_id) : null,
      running_run_started_at: row.running_run_started_at ? String(row.running_run_started_at) : null,
      stuck_reason: row.stuck_reason ? String(row.stuck_reason) : undefined,
    })),
    stale_never_seen_enrollments: asArray<Record<string, unknown>>(raw.stale_never_seen_enrollments).map(normalizeAgentGatewayEnrollment),
    stale_heartbeat_enrollments: asArray<Record<string, unknown>>(raw.stale_heartbeat_enrollments).map(normalizeAgentGatewayEnrollment),
    recommended_actions: asArray(raw.recommended_actions).map(String).filter(Boolean),
    safety: {
      read_only: boolValue(safetyRaw.read_only),
      requires_confirm_cleanup: boolValue(safetyRaw.requires_confirm_cleanup),
      live_execution_performed: boolValue(safetyRaw.live_execution_performed),
      token_omitted: boolValue(safetyRaw.token_omitted),
    },
    applied: raw.applied === undefined ? undefined : boolValue(raw.applied),
    released_tasks: asArray<Record<string, unknown>>(raw.released_tasks).map((item) => ({
      task_id: String(item.task_id || ""),
      released_runs: asArray(item.released_runs).map(String),
    })).filter((item) => item.task_id),
    revoked_enrollments: asArray<Record<string, unknown>>(raw.revoked_enrollments).map((item) => ({
      token_id: String(item.token_id || ""),
      agent_id: item.agent_id ? String(item.agent_id) : null,
      sessions_revoked: numberValue(item.sessions_revoked, 0),
    })).filter((item) => item.token_id),
    errors: asArray<Record<string, unknown>>(raw.errors),
    error: raw.error ? String(raw.error) : undefined,
    token_omitted: boolValue(raw.token_omitted),
    live_execution_performed: boolValue(raw.live_execution_performed),
  };
}

export async function loadWorkerFleetHygiene(options: {
  threshold_sec?: number;
  enrollment_age_sec?: number;
  limit?: number;
} = {}): Promise<WorkerFleetHygienePayload> {
  const params = new URLSearchParams();
  if (options.threshold_sec !== undefined) params.set("threshold_sec", String(options.threshold_sec));
  if (options.enrollment_age_sec !== undefined) params.set("enrollment_age_sec", String(options.enrollment_age_sec));
  if (options.limit !== undefined) params.set("limit", String(options.limit));
  const suffix = params.toString() ? `?${params.toString()}` : "";
  const raw = await optionalApiJson<Record<string, unknown>>(`/workers/fleet/hygiene${suffix}`, {
    provider: "agentops-worker",
    operation: "fleet_hygiene",
    status: "unavailable",
    threshold_sec: options.threshold_sec || 900,
    enrollment_age_sec: options.enrollment_age_sec || 900,
    summary: {
      stuck_tasks: 0,
      stale_never_seen_enrollments: 0,
      stale_heartbeat_enrollments: 0,
      actions_available: 0,
    },
    stuck_tasks: [],
    stale_never_seen_enrollments: [],
    stale_heartbeat_enrollments: [],
    recommended_actions: [],
    safety: {
      read_only: true,
      requires_confirm_cleanup: true,
      live_execution_performed: false,
      token_omitted: true,
    },
    token_omitted: true,
    live_execution_performed: false,
    fallback_reason: "endpoint_not_available",
  });
  return normalizeWorkerFleetHygiene(raw);
}

export async function applyWorkerFleetHygiene(input: {
  threshold_sec?: number;
  enrollment_age_sec?: number;
  limit?: number;
  release_reason?: string;
} = {}): Promise<WorkerFleetHygienePayload> {
  const raw = await apiJsonWithStatuses<Record<string, unknown>>("/workers/fleet/hygiene", {
    method: "POST",
    body: JSON.stringify({
      ...input,
      apply: true,
      confirm_cleanup: true,
    }),
  }, [200, 207, 409]);
  return normalizeWorkerFleetHygiene(raw);
}

export async function loadLocalReadiness(): Promise<LocalReadinessPayload> {
  const raw = await optionalApiJson<Record<string, unknown>>("/local/readiness", {
    provider: "agentops-local",
    operation: "local_readiness",
    status: "unavailable",
    ok: false,
    gates: [],
    evidence: {},
    next_actions: [],
    adapter_readiness: {},
    token_omitted: true,
    live_execution_performed: false,
    fallback_reason: "endpoint_not_available",
  });
  const evidenceRaw = typeof raw.evidence === "object" && raw.evidence !== null ? raw.evidence as Record<string, unknown> : {};
  const lifecycleRaw = typeof raw.commander_synthesis_lifecycle === "object" && raw.commander_synthesis_lifecycle !== null ? raw.commander_synthesis_lifecycle as Record<string, unknown> : {};
  const lifecycleSummaryRaw = typeof lifecycleRaw.summary === "object" && lifecycleRaw.summary !== null ? lifecycleRaw.summary as Record<string, unknown> : {};
  const lifecycleSafetyRaw = typeof lifecycleRaw.safety === "object" && lifecycleRaw.safety !== null ? lifecycleRaw.safety as Record<string, unknown> : {};
  const adapterReadiness = typeof raw.adapter_readiness === "object" && raw.adapter_readiness !== null ? raw.adapter_readiness as WorkerAdapterReadinessSummary : {};
  const harnessRaw = typeof raw.local_harness_proof_readiness === "object" && raw.local_harness_proof_readiness !== null ? raw.local_harness_proof_readiness as Record<string, unknown> : {};
  const harnessSummaryRaw = typeof harnessRaw.summary === "object" && harnessRaw.summary !== null ? harnessRaw.summary as Record<string, unknown> : {};
  const harnessSafetyRaw = typeof harnessRaw.safety === "object" && harnessRaw.safety !== null ? harnessRaw.safety as Record<string, unknown> : {};
  const harnessAdaptersRaw = typeof harnessRaw.adapters === "object" && harnessRaw.adapters !== null ? harnessRaw.adapters as Record<string, unknown> : {};
  const harnessCommandsRaw = typeof harnessRaw.commands === "object" && harnessRaw.commands !== null ? harnessRaw.commands as Record<string, unknown> : {};
  const normalizeHarnessAttempt = (value: unknown): LocalHarnessProofAttempt | null => {
    if (typeof value !== "object" || value === null) return null;
    const item = value as Record<string, unknown>;
    return {
      run_id: item.run_id ? String(item.run_id) : null,
      task_id: item.task_id ? String(item.task_id) : null,
      artifact_id: item.artifact_id ? String(item.artifact_id) : null,
      plan_evidence_manifest_id: item.plan_evidence_manifest_id ? String(item.plan_evidence_manifest_id) : null,
      agent_id: item.agent_id ? String(item.agent_id) : null,
      runtime_type: item.runtime_type ? String(item.runtime_type) : null,
      run_status: item.run_status ? String(item.run_status) : null,
      task_status: item.task_status ? String(item.task_status) : null,
      task_title: item.task_title ? String(item.task_title) : null,
      checked_at: item.checked_at ? String(item.checked_at) : null,
      age_hours: item.age_hours === undefined || item.age_hours === null ? null : numberValue(item.age_hours, 0),
      evidence_class: item.evidence_class ? String(item.evidence_class) : undefined,
      real_runtime_proof: boolValue(item.real_runtime_proof),
      mock_or_ci_fallback: boolValue(item.mock_or_ci_fallback),
      pass: boolValue(item.pass),
      token_omitted: item.token_omitted === undefined ? true : boolValue(item.token_omitted),
    };
  };
  const normalizeHarnessAdapter = (adapter: WorkerAdapterName): LocalHarnessProofAdapter => {
    const item = typeof harnessAdaptersRaw[adapter] === "object" && harnessAdaptersRaw[adapter] !== null ? harnessAdaptersRaw[adapter] as Record<string, unknown> : {};
    const governedRaw = typeof item.governed_launch === "object" && item.governed_launch !== null ? item.governed_launch as Record<string, unknown> : {};
    const receiptRaw = typeof governedRaw.receipt_status === "object" && governedRaw.receipt_status !== null ? governedRaw.receipt_status as Record<string, unknown> : {};
    return {
      adapter,
      status: String(item.status || "missing"),
      ok: boolValue(item.ok),
      evidence_class: String(item.evidence_class || (adapter === "mock" ? "mock_ci_fallback" : "real_runtime_ledger_readback")),
      real_runtime_adapter: item.real_runtime_adapter === undefined ? adapter !== "mock" : boolValue(item.real_runtime_adapter),
      freshness_hours: numberValue(item.freshness_hours, numberValue(harnessRaw.freshness_hours, 72)),
      latest_attempt: normalizeHarnessAttempt(item.latest_attempt),
      latest_passing: normalizeHarnessAttempt(item.latest_passing),
      next_action: String(item.next_action || harnessCommandsRaw[adapter] || (adapter === "mock" ? "python3 scripts/local_task_harness.py --adapter mock --execute" : `python3 scripts/local_task_harness.py --adapter ${adapter} --execute --confirm-run --request-timeout 720`)),
      governed_launch: item.governed_launch ? {
        operation: String(governedRaw.operation || "customer_worker_task"),
        adapter,
        preview_command: String(governedRaw.preview_command || ""),
        confirmed_command: String(governedRaw.confirmed_command || governedRaw.preview_command || ""),
        confirm_required: boolValue(governedRaw.confirm_required),
        writes_ledger: boolValue(governedRaw.writes_ledger),
        live_execution: boolValue(governedRaw.live_execution),
        entrypoint: String(governedRaw.entrypoint || "agentops workflow customer-worker-task"),
        evidence_readback_command: String(governedRaw.evidence_readback_command || "agentops operator local-harness-proof --limit 8"),
        receipt_preview_command: governedRaw.receipt_preview_command ? String(governedRaw.receipt_preview_command) : undefined,
        receipt_record_command: governedRaw.receipt_record_command ? String(governedRaw.receipt_record_command) : undefined,
        receipt_readback_command: governedRaw.receipt_readback_command ? String(governedRaw.receipt_readback_command) : undefined,
        action_signature: governedRaw.action_signature ? String(governedRaw.action_signature) : undefined,
        receipt_status: governedRaw.receipt_status ? {
          operation: String(receiptRaw.operation || "local_harness_proof_launch_receipt_status"),
          adapter,
          required: receiptRaw.required === undefined ? true : boolValue(receiptRaw.required),
          status: String(receiptRaw.status || "missing"),
          underlying_status: String(receiptRaw.underlying_status || receiptRaw.status || "missing"),
          match: String(receiptRaw.match || "missing"),
          current: boolValue(receiptRaw.current),
          recorded: boolValue(receiptRaw.recorded),
          verified: boolValue(receiptRaw.verified),
          receipt_id: receiptRaw.receipt_id ? String(receiptRaw.receipt_id) : null,
          audit_id: receiptRaw.audit_id ? String(receiptRaw.audit_id) : null,
          action_id: receiptRaw.action_id ? String(receiptRaw.action_id) : null,
          action_signature: receiptRaw.action_signature ? String(receiptRaw.action_signature) : null,
          receipt_action_signature: receiptRaw.receipt_action_signature ? String(receiptRaw.receipt_action_signature) : null,
          source: receiptRaw.source ? String(receiptRaw.source) : null,
          receipt_hash: receiptRaw.receipt_hash ? String(receiptRaw.receipt_hash) : null,
          created_at: receiptRaw.created_at ? String(receiptRaw.created_at) : null,
          evaluation_id: receiptRaw.evaluation_id ? String(receiptRaw.evaluation_id) : null,
          evaluation_pass_fail: receiptRaw.evaluation_pass_fail ? String(receiptRaw.evaluation_pass_fail) : null,
          control_readback_attached: boolValue(receiptRaw.control_readback_attached),
          control_readback_id: receiptRaw.control_readback_id ? String(receiptRaw.control_readback_id) : null,
          control_readback_hash: receiptRaw.control_readback_hash ? String(receiptRaw.control_readback_hash) : null,
          readback_command: String(receiptRaw.readback_command || governedRaw.receipt_readback_command || "agentops operator action-receipts --limit 20"),
          receipt_presence_is_runtime_success: boolValue(receiptRaw.receipt_presence_is_runtime_success),
          live_runtime_success_proven: boolValue(receiptRaw.live_runtime_success_proven),
          proof_boundary: receiptRaw.proof_boundary ? String(receiptRaw.proof_boundary) : undefined,
          read_only: receiptRaw.read_only === undefined ? true : boolValue(receiptRaw.read_only),
          ledger_mutated: boolValue(receiptRaw.ledger_mutated),
          live_execution_performed: boolValue(receiptRaw.live_execution_performed),
          raw_prompt_omitted: receiptRaw.raw_prompt_omitted === undefined ? true : boolValue(receiptRaw.raw_prompt_omitted),
          raw_response_omitted: receiptRaw.raw_response_omitted === undefined ? true : boolValue(receiptRaw.raw_response_omitted),
          token_omitted: receiptRaw.token_omitted === undefined ? true : boolValue(receiptRaw.token_omitted),
        } : undefined,
        approval_boundary: governedRaw.approval_boundary ? String(governedRaw.approval_boundary) : undefined,
        raw_prompt_omitted: boolValue(governedRaw.raw_prompt_omitted),
        raw_response_omitted: boolValue(governedRaw.raw_response_omitted),
        token_omitted: governedRaw.token_omitted === undefined ? true : boolValue(governedRaw.token_omitted),
      } : undefined,
      token_omitted: item.token_omitted === undefined ? true : boolValue(item.token_omitted),
    };
  };
  const localRunPath = asArray<Record<string, unknown>>(raw.local_run_path).map((step) => ({
    step_id: String(step.step_id || ""),
    label: String(step.label || step.step_id || ""),
    phase: String(step.phase || ""),
    status: String(step.status || "unknown"),
    adapter: ["mock", "hermes", "openclaw"].includes(String(step.adapter)) ? String(step.adapter) as WorkerAdapterName : undefined,
    command: String(step.command || ""),
    verify_command: step.verify_command ? String(step.verify_command) : null,
    route: step.route ? String(step.route) : null,
    detail: step.detail ? String(step.detail) : undefined,
    mutating: boolValue(step.mutating),
    confirm_required: boolValue(step.confirm_required),
    writes_ledger: boolValue(step.writes_ledger),
    live_execution: boolValue(step.live_execution),
    service_control_preview: step.service_control_preview === undefined ? undefined : boolValue(step.service_control_preview),
    copy_only: step.copy_only === undefined ? undefined : boolValue(step.copy_only),
    server_executes_shell: step.server_executes_shell === undefined ? undefined : boolValue(step.server_executes_shell),
    receipt_required: step.receipt_required === undefined ? undefined : boolValue(step.receipt_required),
    control_readback_required: step.control_readback_required === undefined ? undefined : boolValue(step.control_readback_required),
    receipt_command: step.receipt_command ? String(step.receipt_command) : null,
    receipt_record_command: step.receipt_record_command ? String(step.receipt_record_command) : null,
    receipt_verify_record_command: step.receipt_verify_record_command ? String(step.receipt_verify_record_command) : null,
    receipt_state: typeof step.receipt_state === "object" && step.receipt_state !== null ? step.receipt_state as Record<string, unknown> : undefined,
    action_signature: step.action_signature ? String(step.action_signature) : null,
    source: step.source ? String(step.source) : null,
    token_omitted: step.token_omitted === undefined ? undefined : boolValue(step.token_omitted),
  })).filter((step) => step.step_id && step.command);
  return {
    provider: String(raw.provider || "agentops-local"),
    operation: String(raw.operation || "local_readiness"),
    status: String(raw.status || "unknown"),
    ok: boolValue(raw.ok),
    workspace_id: raw.workspace_id ? String(raw.workspace_id) : undefined,
    gates: asArray<Record<string, unknown>>(raw.gates).map((gate) => ({
      id: String(gate.id || ""),
      label: String(gate.label || gate.id || ""),
      ok: boolValue(gate.ok),
      status: String(gate.status || "unknown"),
      detail: String(gate.detail || ""),
      next_action: String(gate.next_action || ""),
    })).filter((gate) => gate.id || gate.label),
    evidence: {
      tasks: numberValue(evidenceRaw.tasks, 0),
      planned_tasks: numberValue(evidenceRaw.planned_tasks, 0),
      completed_tasks: numberValue(evidenceRaw.completed_tasks, 0),
      runs: numberValue(evidenceRaw.runs, 0),
      completed_runs: numberValue(evidenceRaw.completed_runs, 0),
      tool_calls: numberValue(evidenceRaw.tool_calls, 0),
      evaluations: numberValue(evidenceRaw.evaluations, 0),
      audit_logs: numberValue(evidenceRaw.audit_logs, 0),
      artifacts: numberValue(evidenceRaw.artifacts, 0),
      memories: numberValue(evidenceRaw.memories, 0),
      memory_candidates: numberValue(evidenceRaw.memory_candidates, 0),
      approved_memories: numberValue(evidenceRaw.approved_memories, 0),
      pending_approvals: numberValue(evidenceRaw.pending_approvals, 0),
      approvals: numberValue(evidenceRaw.approvals, 0),
      workflow_jobs: numberValue(evidenceRaw.workflow_jobs, 0),
      customer_worker_artifacts: numberValue(evidenceRaw.customer_worker_artifacts, 0),
      closed_loop_runs: numberValue(evidenceRaw.closed_loop_runs, 0),
      commander_synthesis_artifacts: numberValue(evidenceRaw.commander_synthesis_artifacts, 0),
      commander_synthesis_pending_reviews: numberValue(evidenceRaw.commander_synthesis_pending_reviews, 0),
      commander_synthesis_approved_reviews: numberValue(evidenceRaw.commander_synthesis_approved_reviews, 0),
      commander_synthesis_promoted_memories: numberValue(evidenceRaw.commander_synthesis_promoted_memories, 0),
      commander_synthesis_promoted_deliveries: numberValue(evidenceRaw.commander_synthesis_promoted_deliveries, 0),
      has_task_run_tool_eval_audit_artifact_chain: boolValue(evidenceRaw.has_task_run_tool_eval_audit_artifact_chain),
      has_memory_or_knowledge: boolValue(evidenceRaw.has_memory_or_knowledge),
      has_approval_flow: boolValue(evidenceRaw.has_approval_flow),
    },
    adapter_readiness: adapterReadiness,
    local_harness_proof_readiness: raw.local_harness_proof_readiness ? {
      provider: String(harnessRaw.provider || "agentops-operator"),
      operation: String(harnessRaw.operation || "local_harness_proof_readiness"),
      status: String(harnessRaw.status || "unknown"),
      ok: boolValue(harnessRaw.ok),
      workspace_id: harnessRaw.workspace_id ? String(harnessRaw.workspace_id) : undefined,
      freshness_hours: numberValue(harnessRaw.freshness_hours, 72),
      adapters: {
        mock: normalizeHarnessAdapter("mock"),
        hermes: normalizeHarnessAdapter("hermes"),
        openclaw: normalizeHarnessAdapter("openclaw"),
      },
      summary: {
        fresh: numberValue(harnessSummaryRaw.fresh, 0),
        fresh_real_runtime_adapters: numberValue(harnessSummaryRaw.fresh_real_runtime_adapters, 0),
        fresh_mock_fallback: numberValue(harnessSummaryRaw.fresh_mock_fallback, 0),
        stale: numberValue(harnessSummaryRaw.stale, 0),
        missing: numberValue(harnessSummaryRaw.missing, 0),
        latest_failed: numberValue(harnessSummaryRaw.latest_failed, 0),
        latest_incomplete: numberValue(harnessSummaryRaw.latest_incomplete, 0),
        launch_receipts_recorded_current: numberValue(harnessSummaryRaw.launch_receipts_recorded_current, 0),
        launch_receipts_missing: numberValue(harnessSummaryRaw.launch_receipts_missing, 0),
        launch_receipts_stale: numberValue(harnessSummaryRaw.launch_receipts_stale, 0),
      },
      commands: Object.fromEntries(Object.entries(harnessCommandsRaw).map(([key, value]) => [key, String(value || "")])),
      governed_launch_packet: typeof harnessRaw.governed_launch_packet === "object" && harnessRaw.governed_launch_packet !== null ? (() => {
        const packetRaw = harnessRaw.governed_launch_packet as Record<string, unknown>;
        const packetSafetyRaw = typeof packetRaw.safety === "object" && packetRaw.safety !== null ? packetRaw.safety as Record<string, unknown> : {};
        const receiptSummaryRaw = typeof packetRaw.receipt_summary === "object" && packetRaw.receipt_summary !== null ? packetRaw.receipt_summary as Record<string, unknown> : {};
        return {
          operation: String(packetRaw.operation || "local_harness_proof_governed_launch_packet"),
          status: String(packetRaw.status || "unknown"),
          preferred_entrypoint: String(packetRaw.preferred_entrypoint || "agentops workflow customer-worker-task"),
          readback_command: String(packetRaw.readback_command || "agentops operator local-harness-proof --limit 8"),
          receipt_summary: packetRaw.receipt_summary ? {
            operation: String(receiptSummaryRaw.operation || "local_harness_proof_launch_receipt_summary"),
            readback_command: String(receiptSummaryRaw.readback_command || "agentops operator action-receipts --limit 20"),
            recorded_current: numberValue(receiptSummaryRaw.recorded_current, 0),
            verified_current: numberValue(receiptSummaryRaw.verified_current, 0),
            missing: numberValue(receiptSummaryRaw.missing, 0),
            stale: numberValue(receiptSummaryRaw.stale, 0),
            current: numberValue(receiptSummaryRaw.current, 0),
            receipt_presence_is_runtime_success: boolValue(receiptSummaryRaw.receipt_presence_is_runtime_success),
            read_only: receiptSummaryRaw.read_only === undefined ? true : boolValue(receiptSummaryRaw.read_only),
            ledger_mutated: boolValue(receiptSummaryRaw.ledger_mutated),
            live_execution_performed: boolValue(receiptSummaryRaw.live_execution_performed),
            token_omitted: receiptSummaryRaw.token_omitted === undefined ? true : boolValue(receiptSummaryRaw.token_omitted),
          } : undefined,
          contract: packetRaw.contract ? String(packetRaw.contract) : undefined,
          safety: {
            read_only: boolValue(packetSafetyRaw.read_only),
            ledger_mutated: boolValue(packetSafetyRaw.ledger_mutated),
            live_execution_performed: boolValue(packetSafetyRaw.live_execution_performed),
            raw_prompt_omitted: boolValue(packetSafetyRaw.raw_prompt_omitted),
            raw_response_omitted: boolValue(packetSafetyRaw.raw_response_omitted),
            token_omitted: boolValue(packetSafetyRaw.token_omitted),
          },
        };
      })() : undefined,
      contract: harnessRaw.contract ? String(harnessRaw.contract) : undefined,
      safety: {
        read_only: boolValue(harnessSafetyRaw.read_only),
        ledger_mutated: boolValue(harnessSafetyRaw.ledger_mutated),
        live_execution_performed: boolValue(harnessSafetyRaw.live_execution_performed),
        raw_prompt_omitted: boolValue(harnessSafetyRaw.raw_prompt_omitted),
        raw_response_omitted: boolValue(harnessSafetyRaw.raw_response_omitted),
        token_omitted: boolValue(harnessSafetyRaw.token_omitted),
      },
      token_omitted: harnessRaw.token_omitted === undefined ? true : boolValue(harnessRaw.token_omitted),
      live_execution_performed: boolValue(harnessRaw.live_execution_performed),
    } : undefined,
    commander_synthesis_lifecycle: raw.commander_synthesis_lifecycle ? {
      status: String(lifecycleRaw.status || "unknown"),
      summary: {
        synthesis_artifacts: numberValue(lifecycleSummaryRaw.synthesis_artifacts, 0),
        pending_reviews: numberValue(lifecycleSummaryRaw.pending_reviews, 0),
        approved_reviews: numberValue(lifecycleSummaryRaw.approved_reviews, 0),
        rejected_reviews: numberValue(lifecycleSummaryRaw.rejected_reviews, 0),
        promoted_memory_candidates: numberValue(lifecycleSummaryRaw.promoted_memory_candidates, 0),
        promoted_delivery_artifacts: numberValue(lifecycleSummaryRaw.promoted_delivery_artifacts, 0),
      },
      recent: asArray<Record<string, unknown>>(lifecycleRaw.recent),
      next_actions: asArray(lifecycleRaw.next_actions).map(String),
      safety: {
        read_only: boolValue(lifecycleSafetyRaw.read_only),
        ledger_mutated: boolValue(lifecycleSafetyRaw.ledger_mutated),
        live_execution_performed: boolValue(lifecycleSafetyRaw.live_execution_performed),
        token_omitted: boolValue(lifecycleSafetyRaw.token_omitted),
      },
      token_omitted: boolValue(lifecycleRaw.token_omitted),
      live_execution_performed: boolValue(lifecycleRaw.live_execution_performed),
    } : undefined,
    next_actions: asArray(raw.next_actions).map(String),
    local_run_path: localRunPath,
    contract: raw.contract ? String(raw.contract) : undefined,
    token_omitted: boolValue(raw.token_omitted),
    live_execution_performed: boolValue(raw.live_execution_performed),
  };
}

function acceptanceRows(raw: unknown, preferredKey: string): unknown[] {
  if (Array.isArray(raw)) return raw;
  if (typeof raw !== "object" || raw === null) throw new Error("response is not a readable list");
  const record = raw as Record<string, unknown>;
  if (Array.isArray(record[preferredKey])) return record[preferredKey] as unknown[];
  if (Array.isArray(record.items)) return record.items as unknown[];
  throw new Error("response does not contain a readable list");
}

function acceptanceError(error: unknown): string {
  return (error instanceof Error ? error.message : String(error)).replace(/\s+/g, " ").slice(0, 160);
}

export async function loadPrivateHostAcceptanceSnapshot(marker?: {
  receipt_id: string;
  task_id?: string;
}): Promise<PrivateHostAcceptanceSnapshot> {
  const authPromise = getHumanAuthStatus()
    .then((status) => ({
      check: {
        id: "human_session",
        ok: Boolean(status.required && status.authenticated),
        status: status.authenticated ? "authenticated" : "unauthenticated",
      } as PrivateHostAcceptanceCheck,
      user: status.user,
    }))
    .catch((error) => ({
      check: { id: "human_session", ok: false, error: acceptanceError(error) } as PrivateHostAcceptanceCheck,
      user: undefined,
    }));

  const readinessPromise = apiJson<Record<string, unknown>>("/local/readiness")
    .then((readiness): PrivateHostAcceptanceCheck => ({
      id: "local_readiness",
      ok: true,
      status: String(readiness.status || (readiness.ok ? "ready" : "attention")),
    }))
    .catch((error): PrivateHostAcceptanceCheck => ({ id: "local_readiness", ok: false, error: acceptanceError(error) }));

  const listCheck = async (
    id: PrivateHostAcceptanceCheckId,
    path: string,
    key: string,
  ): Promise<{ check: PrivateHostAcceptanceCheck; rows: unknown[] }> => {
    try {
      const rows = acceptanceRows(await apiJson<unknown>(path), key);
      return { check: { id, ok: true, count: rows.length, status: "readable" }, rows };
    } catch (error) {
      return { check: { id, ok: false, error: acceptanceError(error) }, rows: [] };
    }
  };

  const [auth, readiness, tasks, evaluations, approvals, memories, audit, artifacts] = await Promise.all([
    authPromise,
    readinessPromise,
    listCheck("tasks_readable", "/tasks", "tasks"),
    listCheck("evaluations_readable", "/evaluations", "evaluations"),
    listCheck("approvals_readable", "/approvals", "approvals"),
    listCheck("memories_readable", "/memories", "memories"),
    listCheck("audit_readable", "/audit?limit=150", "audit_logs"),
    listCheck("artifacts_readable", "/artifacts", "artifacts"),
  ]);

  const markerRow = marker?.task_id
    ? tasks.rows.find((row) => {
        if (typeof row !== "object" || row === null) return false;
        const task = row as Record<string, unknown>;
        return String(task.task_id || "") === marker.task_id
          && String(task.title || "").includes(marker.receipt_id);
      }) as Record<string, unknown> | undefined
    : undefined;
  const markerCheck: PrivateHostAcceptanceCheck = {
    id: "marker_task_readable",
    ok: Boolean(markerRow),
    status: marker?.task_id ? (markerRow ? "readable" : "not_found") : "not_created",
  };

  const user = auth.user;
  const checks = [
    auth.check,
    readiness,
    tasks.check,
    evaluations.check,
    approvals.check,
    memories.check,
    audit.check,
    artifacts.check,
    markerCheck,
  ];
  return {
    checked_at: new Date().toISOString(),
    checks,
    counts: {
      tasks: tasks.rows.length,
      evaluations: evaluations.rows.length,
      approvals: approvals.rows.length,
      memories: memories.rows.length,
      audit: audit.rows.length,
      artifacts: artifacts.rows.length,
    },
    related_ids: markerRow ? { marker_task_id: String(markerRow.task_id || marker?.task_id || "") } : {},
    actor: {
      workspace_id: user?.workspace_id,
      user_id: user?.account_id || user?.user_id,
      role: user?.role,
    },
  };
}

export async function createPrivateHostAcceptanceMarker(receiptId: string): Promise<PrivateHostAcceptanceMarker> {
  const title = `Private Host acceptance marker ${receiptId}`;
  const raw = await apiJson<Record<string, unknown>>("/tasks", {
    method: "POST",
    body: JSON.stringify({
      title,
      description: "Low-risk browser acceptance marker. No Runtime or external connector is invoked.",
      acceptance_criteria: "The marker task is readable through the same authenticated human Session.",
      owner_agent_id: "",
      collaborator_agent_ids: [],
      status: "planned",
      priority: "low",
      risk_level: "low",
      budget_limit_usd: 0,
    }),
  });
  return {
    receipt_id: receiptId,
    task_id: String(raw.task_id || ""),
    title: String(raw.title || title),
  };
}

function privateHostAuthorityReceipt(raw: unknown): PrivateHostAuthorityReceipt {
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) {
    throw new Error("Host authority receipt response is invalid");
  }
  const record = raw as Record<string, unknown>;
  const evaluationRaw = record.evaluation;
  if (typeof evaluationRaw !== "object" || evaluationRaw === null || Array.isArray(evaluationRaw)) {
    throw new Error("Host authority receipt evaluation is invalid");
  }
  const evaluation = evaluationRaw as Record<string, unknown>;
  const requiredStrings = [
    "receipt_id", "host_version", "git_commit", "run_id", "adapter", "artifact_id",
    "plan_manifest_id", "artifact_metadata_sha256", "payload_sha256",
  ] as const;
  for (const key of requiredStrings) {
    if (typeof record[key] !== "string" || !String(record[key]).trim()) {
      throw new Error(`Host authority receipt field is invalid: ${key}`);
    }
  }
  if (record.status !== "completed" || evaluation.pass_fail !== "pass" || !Number.isFinite(Number(evaluation.score))) {
    throw new Error("Host authority receipt completion or evaluation gate is invalid");
  }
  const artifactHash = String(record.artifact_metadata_sha256);
  const payloadHash = String(record.payload_sha256);
  if (!/^[a-f0-9]{64}$/.test(artifactHash) || !/^[a-f0-9]{64}$/.test(payloadHash)) {
    throw new Error("Host authority receipt integrity hash is invalid");
  }
  return {
    receipt_id: String(record.receipt_id),
    host_version: String(record.host_version),
    git_commit: String(record.git_commit),
    run_id: String(record.run_id),
    adapter: String(record.adapter),
    status: "completed",
    evaluation: { score: Number(evaluation.score), pass_fail: "pass" },
    artifact_id: String(record.artifact_id),
    plan_manifest_id: String(record.plan_manifest_id),
    artifact_metadata_sha256: artifactHash,
    payload_sha256: payloadHash,
  };
}

export async function createPrivateHostAuthorityReceipt(runId: string): Promise<PrivateHostAuthorityReceipt> {
  const created = privateHostAuthorityReceipt(await apiJson<unknown>("/host/acceptance-receipts", {
    method: "POST",
    body: JSON.stringify({ run_id: runId.trim() }),
  }));
  return loadPrivateHostAuthorityReceipt(created.receipt_id);
}

export async function loadPrivateHostAuthorityReceipt(receiptId: string): Promise<PrivateHostAuthorityReceipt> {
  const raw = await apiJson<unknown>(`/host/acceptance-receipts/${encodeURIComponent(receiptId)}`);
  return privateHostAuthorityReceipt(raw);
}

export async function loadIntegrationInbox(options: IntegrationInboxOptions = {}): Promise<IntegrationInboxPayload> {
  const params = new URLSearchParams();
  if (options.bucket && options.bucket !== "all") params.set("bucket", options.bucket);
  if (options.limit) params.set("limit", String(options.limit));
  if (options.threshold_sec) params.set("threshold_sec", String(options.threshold_sec));
  const path = params.toString() ? `/commander/integration-inbox?${params.toString()}` : "/commander/integration-inbox";
  const raw = await optionalApiJson<Record<string, unknown>>(path, {
    provider: "agentops-commander",
    operation: "integration_inbox",
    status: "unavailable",
    filter: { bucket: options.bucket || "all", limit: options.limit || 20, threshold_sec: options.threshold_sec || 900 },
    token_omitted: true,
    live_execution_performed: false,
    summary: {},
    inbox_items: [],
    recommended_next_actions: [],
    safety: {
      read_only: true,
      ledger_mutated: false,
      raw_prompt_omitted: true,
    },
    fallback_reason: "endpoint_not_available",
  });
  const summaryRaw = typeof raw.summary === "object" && raw.summary !== null ? raw.summary as Record<string, unknown> : {};
  const bucketRaw = typeof summaryRaw.buckets === "object" && summaryRaw.buckets !== null ? summaryRaw.buckets as Record<string, unknown> : summaryRaw;
  const filterRaw = typeof raw.filter === "object" && raw.filter !== null ? raw.filter as Record<string, unknown> : {};
  const safetyRaw = typeof raw.safety === "object" && raw.safety !== null ? raw.safety as Record<string, unknown> : {};
  return {
    provider: String(raw.provider || "agentops-commander"),
    operation: String(raw.operation || "integration_inbox"),
    status: String(raw.status || "unknown"),
    filter: {
      bucket: String(filterRaw.bucket || options.bucket || "all"),
      limit: numberValue(filterRaw.limit, options.limit || 20),
      threshold_sec: numberValue(filterRaw.threshold_sec, options.threshold_sec || 900),
    },
    token_omitted: boolValue(raw.token_omitted),
    live_execution_performed: boolValue(raw.live_execution_performed),
    summary: {
      ready_for_review: numberValue(bucketRaw.ready_for_review, 0),
      still_running: numberValue(bucketRaw.still_running, 0),
      blocked: numberValue(bucketRaw.blocked, 0),
      late_or_stale: numberValue(bucketRaw.late_or_stale, 0),
      needs_memory_review: numberValue(bucketRaw.needs_memory_review, 0),
      total: numberValue(summaryRaw.total, Object.values(bucketRaw).reduce((sum, value) => sum + numberValue(value, 0), 0)),
    },
    inbox_items: asArray<Record<string, unknown>>(raw.inbox_items).map((item) => ({
      item_id: String(item.item_id || item.job_id || item.run_id || item.task_id || ""),
      bucket: String(item.bucket || "unknown"),
      title: String(item.title || item.item_id || "Untitled inbox item"),
      status: String(item.status || "unknown"),
      task_id: item.task_id ? String(item.task_id) : null,
      run_id: item.run_id ? String(item.run_id) : null,
      job_id: item.job_id ? String(item.job_id) : null,
      artifact_id: item.artifact_id ? String(item.artifact_id) : null,
      agent_id: item.agent_id ? String(item.agent_id) : null,
      owner_agent_id: item.owner_agent_id ? String(item.owner_agent_id) : null,
      age_sec: numberValue(item.age_sec, 0),
      evidence: typeof item.evidence === "object" && item.evidence !== null
        ? item.evidence as Record<string, unknown>
        : typeof item.evidence_counts === "object" && item.evidence_counts !== null
          ? item.evidence_counts as Record<string, unknown>
          : undefined,
      integration_decision: typeof item.integration_decision === "object" && item.integration_decision !== null
        ? {
            decision: String((item.integration_decision as Record<string, unknown>).decision || "review_required"),
            status: String((item.integration_decision as Record<string, unknown>).status || "attention"),
            reason: String((item.integration_decision as Record<string, unknown>).reason || ""),
            required_review: boolValue((item.integration_decision as Record<string, unknown>).required_review),
            can_advance_without_waiting: boolValue((item.integration_decision as Record<string, unknown>).can_advance_without_waiting),
            evidence_complete: boolValue((item.integration_decision as Record<string, unknown>).evidence_complete),
            pending_approval: boolValue((item.integration_decision as Record<string, unknown>).pending_approval),
            safe_to_auto_apply: boolValue((item.integration_decision as Record<string, unknown>).safe_to_auto_apply),
            ledger_decision_required: boolValue((item.integration_decision as Record<string, unknown>).ledger_decision_required),
            next_command: (item.integration_decision as Record<string, unknown>).next_command ? String((item.integration_decision as Record<string, unknown>).next_command) : undefined,
          }
        : undefined,
      recommended_action: item.recommended_action ? String(item.recommended_action) : undefined,
      created_at: item.created_at ? String(item.created_at) : undefined,
      updated_at: item.updated_at ? String(item.updated_at) : undefined,
    })).filter((item) => item.item_id || item.title),
    recommended_next_actions: asArray<unknown>(raw.recommended_next_actions).map((item) => String(item)).filter(Boolean),
    safety: {
      read_only: boolValue(safetyRaw.read_only),
      ledger_mutated: boolValue(safetyRaw.ledger_mutated),
      raw_prompt_omitted: boolValue(safetyRaw.raw_prompt_omitted),
    },
  };
}

export async function planCommanderWorkPackages(input: {
  goal: string;
  project_id?: string;
  plan_id?: string;
  max_packages?: number;
  confirm_create?: boolean;
}): Promise<CommanderWorkPackagePlanPayload> {
  const raw = await apiJson<Record<string, unknown>>("/commander/work-packages/plan", {
    method: "POST",
    body: JSON.stringify(input),
  });
  const safetyRaw = typeof raw.safety === "object" && raw.safety !== null ? raw.safety as Record<string, unknown> : {};
  return {
    provider: String(raw.provider || "agentops-commander"),
    operation: String(raw.operation || "work_package_plan"),
    status: String(raw.status || "unknown"),
    ok: boolValue(raw.ok),
    workspace_id: String(raw.workspace_id || "local-demo"),
    project_id: String(raw.project_id || ""),
    plan_id: String(raw.plan_id || ""),
    goal_summary: String(raw.goal_summary || ""),
    confirm_create: boolValue(raw.confirm_create),
    created: boolValue(raw.created),
    created_count: numberValue(raw.created_count, 0),
    planned_count: numberValue(raw.planned_count, 0),
    work_packages: asArray<Record<string, unknown>>(raw.work_packages).map((item) => ({
      plan_id: String(item.plan_id || raw.plan_id || ""),
      project_id: String(item.project_id || raw.project_id || ""),
      lane_id: String(item.lane_id || ""),
      task_id: String(item.task_id || ""),
      title: String(item.title || "Untitled work package"),
      description: String(item.description || ""),
      owner_agent_id: String(item.owner_agent_id || ""),
      collaborator_agent_ids: asArray<unknown>(item.collaborator_agent_ids).map(String),
      status: String(item.status || "planned"),
      priority: String(item.priority || "medium"),
      risk_level: String(item.risk_level || "medium"),
      acceptance_criteria: String(item.acceptance_criteria || ""),
      dependencies: asArray<unknown>(item.dependencies).map(String),
      verification_commands: asArray<unknown>(item.verification_commands).map(String),
      scope: String(item.scope || ""),
      avoid_scope: String(item.avoid_scope || ""),
    })),
    created_task_ids: asArray<unknown>(raw.created_task_ids).map(String),
    errors: asArray<Record<string, unknown>>(raw.errors).map((item) => ({
      lane_id: item.lane_id ? String(item.lane_id) : undefined,
      task_id: item.task_id ? String(item.task_id) : undefined,
      error: item.error ? String(item.error) : undefined,
      message: item.message ? String(item.message) : undefined,
    })),
    recommended_next_actions: asArray<unknown>(raw.recommended_next_actions).map(String).filter(Boolean),
    safety: {
      live_execution_performed: boolValue(safetyRaw.live_execution_performed),
      token_omitted: boolValue(safetyRaw.token_omitted),
      raw_prompt_omitted: boolValue(safetyRaw.raw_prompt_omitted),
      dry_run: boolValue(safetyRaw.dry_run),
      ledger_mutated: boolValue(safetyRaw.ledger_mutated),
      task_created: boolValue(safetyRaw.task_created),
    },
    token_omitted: boolValue(raw.token_omitted),
    live_execution_performed: boolValue(raw.live_execution_performed),
  };
}

export async function loadCommanderWorkPackages(options: {
  project_id?: string;
  plan_id?: string;
  status?: string;
  limit?: number;
} = {}): Promise<CommanderWorkPackageReadbackPayload> {
  const params = new URLSearchParams();
  if (options.project_id) params.set("project_id", options.project_id);
  if (options.plan_id) params.set("plan_id", options.plan_id);
  if (options.status) params.set("status", options.status);
  if (options.limit) params.set("limit", String(options.limit));
  const raw = await optionalApiJson<Record<string, unknown>>(`/commander/work-packages${params.toString() ? `?${params}` : ""}`, {
    provider: "agentops-commander",
    operation: "work_packages_readback",
    status: "unavailable",
    workspace_id: "local-demo",
    filter: { status: options.status || "all", limit: options.limit || 25 },
    summary: {},
    work_packages: [],
    recommended_next_actions: [],
    safety: {
      read_only: true,
      ledger_mutated: false,
      task_created: false,
      run_created: false,
      live_execution_performed: false,
      token_omitted: true,
      raw_prompt_omitted: true,
    },
    token_omitted: true,
    live_execution_performed: false,
  });
  const filterRaw = typeof raw.filter === "object" && raw.filter !== null ? raw.filter as Record<string, unknown> : {};
  const summaryRaw = typeof raw.summary === "object" && raw.summary !== null ? raw.summary as Record<string, unknown> : {};
  const safetyRaw = typeof raw.safety === "object" && raw.safety !== null ? raw.safety as Record<string, unknown> : {};
  return {
    provider: String(raw.provider || "agentops-commander"),
    operation: String(raw.operation || "work_packages_readback"),
    status: String(raw.status || "unknown"),
    workspace_id: String(raw.workspace_id || "local-demo"),
    filter: {
      project_id: filterRaw.project_id ? String(filterRaw.project_id) : null,
      plan_id: filterRaw.plan_id ? String(filterRaw.plan_id) : null,
      status: String(filterRaw.status || options.status || "all"),
      limit: numberValue(filterRaw.limit, options.limit || 25),
    },
    summary: {
      total: numberValue(summaryRaw.total, 0),
      by_status: numberRecord(summaryRaw.by_status),
      by_project: numberRecord(summaryRaw.by_project),
    },
    work_packages: asArray<Record<string, unknown>>(raw.work_packages).map((item) => {
      const latestRun = typeof item.latest_run === "object" && item.latest_run !== null ? item.latest_run as Record<string, unknown> : null;
      return {
        plan_id: String(item.plan_id || ""),
        project_id: String(item.project_id || ""),
        lane_id: String(item.lane_id || ""),
        task_id: String(item.task_id || ""),
        work_package_id: String(item.work_package_id || item.task_id || ""),
        title: String(item.title || "Untitled work package"),
        description: String(item.description || ""),
        owner_agent_id: String(item.owner_agent_id || ""),
        collaborator_agent_ids: asArray<unknown>(item.collaborator_agent_ids).map(String),
        status: String(item.status || "unknown"),
        package_status: String(item.package_status || item.status || "unknown"),
        priority: String(item.priority || "medium"),
        risk_level: String(item.risk_level || "medium"),
        acceptance_criteria: String(item.acceptance_criteria || ""),
        dependencies: asArray<unknown>(item.dependencies).map(String),
        verification_commands: asArray<unknown>(item.verification_commands).map(String),
        scope: String(item.scope || ""),
        avoid_scope: String(item.avoid_scope || ""),
        latest_run: latestRun ? {
          run_id: latestRun.run_id ? String(latestRun.run_id) : undefined,
          status: latestRun.status ? String(latestRun.status) : undefined,
          agent_id: latestRun.agent_id ? String(latestRun.agent_id) : undefined,
          runtime_type: latestRun.runtime_type ? String(latestRun.runtime_type) : undefined,
          created_at: latestRun.created_at ? String(latestRun.created_at) : undefined,
          ended_at: latestRun.ended_at ? String(latestRun.ended_at) : null,
          error_type: latestRun.error_type ? String(latestRun.error_type) : null,
          error_message: latestRun.error_message ? String(latestRun.error_message) : null,
        } : null,
        evidence_counts: numberRecord(item.evidence_counts),
        recommended_action: item.recommended_action ? String(item.recommended_action) : undefined,
        created_at: item.created_at ? String(item.created_at) : undefined,
        updated_at: item.updated_at ? String(item.updated_at) : undefined,
      };
    }),
    recommended_next_actions: asArray<unknown>(raw.recommended_next_actions).map(String).filter(Boolean),
    safety: {
      read_only: boolValue(safetyRaw.read_only),
      ledger_mutated: boolValue(safetyRaw.ledger_mutated),
      task_created: boolValue(safetyRaw.task_created),
      run_created: boolValue(safetyRaw.run_created),
      live_execution_performed: boolValue(safetyRaw.live_execution_performed),
      token_omitted: boolValue(safetyRaw.token_omitted),
      raw_prompt_omitted: boolValue(safetyRaw.raw_prompt_omitted),
    },
    token_omitted: boolValue(raw.token_omitted),
    live_execution_performed: boolValue(raw.live_execution_performed),
  };
}

export async function loadCommanderProjectBoard(options: {
  project_id?: string;
  plan_id?: string;
  limit?: number;
} = {}): Promise<CommanderProjectBoardPayload> {
  const params = new URLSearchParams();
  if (options.project_id) params.set("project_id", options.project_id);
  if (options.plan_id) params.set("plan_id", options.plan_id);
  if (options.limit) params.set("limit", String(options.limit));
  const raw = await optionalApiJson<Record<string, unknown>>(`/commander/project-board${params.toString() ? `?${params}` : ""}`, {
    provider: "agentops-commander",
    operation: "project_board",
    status: "unavailable",
    workspace_id: "local-demo",
    counts: {},
    team_board: null,
    team_board_filter: { project_id: options.project_id || null, plan_id: options.plan_id || null, limit: options.limit || 25, applied: Boolean(options.project_id || options.plan_id) },
    integration_gates: [],
    recommended_next_actions: [],
    safety: {
      read_only: true,
      ledger_mutated: false,
      task_created: false,
      run_created: false,
      job_created: false,
      token_omitted: true,
      raw_prompt_omitted: true,
    },
    token_omitted: true,
    live_execution_performed: false,
  });
  const safetyRaw = typeof raw.safety === "object" && raw.safety !== null ? raw.safety as Record<string, unknown> : {};
  const filterRaw = typeof raw.team_board_filter === "object" && raw.team_board_filter !== null ? raw.team_board_filter as Record<string, unknown> : {};
  const summaryRaw = typeof raw.team_work_packages_summary === "object" && raw.team_work_packages_summary !== null ? raw.team_work_packages_summary as Record<string, unknown> : null;
  return {
    provider: String(raw.provider || "agentops-commander"),
    operation: String(raw.operation || "project_board"),
    status: String(raw.status || "unknown"),
    workspace_id: String(raw.workspace_id || "local-demo"),
    counts: typeof raw.counts === "object" && raw.counts !== null ? raw.counts as Record<string, unknown> : {},
    team_board: parseCommanderTeamBoardPayload(raw.team_board, String(raw.workspace_id || "local-demo")),
    team_board_filter: {
      project_id: filterRaw.project_id ? String(filterRaw.project_id) : null,
      plan_id: filterRaw.plan_id ? String(filterRaw.plan_id) : null,
      limit: numberValue(filterRaw.limit, options.limit || 25),
      applied: boolValue(filterRaw.applied),
    },
    team_work_packages_summary: summaryRaw ? {
      total: numberValue(summaryRaw.total, 0),
      by_status: numberRecord(summaryRaw.by_status),
      by_project: numberRecord(summaryRaw.by_project),
    } : null,
    integration_gates: asArray<Record<string, unknown>>(raw.integration_gates).map((gate) => ({
      id: String(gate.id || ""),
      status: String(gate.status || "unknown"),
      summary: gate.summary ? String(gate.summary) : undefined,
      next_action: gate.next_action ? String(gate.next_action) : undefined,
    })),
    recommended_next_actions: asArray<unknown>(raw.recommended_next_actions).map(String).filter(Boolean),
    safety: {
      read_only: boolValue(safetyRaw.read_only),
      ledger_mutated: boolValue(safetyRaw.ledger_mutated),
      task_created: boolValue(safetyRaw.task_created),
      run_created: boolValue(safetyRaw.run_created),
      job_created: boolValue(safetyRaw.job_created),
      token_omitted: boolValue(safetyRaw.token_omitted),
      raw_prompt_omitted: boolValue(safetyRaw.raw_prompt_omitted),
    },
    token_omitted: boolValue(raw.token_omitted),
    live_execution_performed: boolValue(raw.live_execution_performed),
  };
}

export async function dispatchCommanderWorkPackage(input: {
  task_id: string;
  adapter?: WorkerAdapterName;
  confirm_run?: boolean;
  worker_agent_id?: string;
  hermes_timeout?: number;
}): Promise<CommanderWorkPackageDispatchPayload> {
  const raw = await apiJson<Record<string, unknown>>(`/commander/work-packages/${encodeURIComponent(input.task_id)}/dispatch`, {
    method: "POST",
    body: JSON.stringify({
      adapter: input.adapter || "mock",
      confirm_run: Boolean(input.confirm_run),
      worker_agent_id: input.worker_agent_id,
      hermes_timeout: input.hermes_timeout,
    }),
  });
  const safetyRaw = typeof raw.safety === "object" && raw.safety !== null ? raw.safety as Record<string, unknown> : {};
  const workPackageRaw = typeof raw.work_package === "object" && raw.work_package !== null ? raw.work_package as Record<string, unknown> : null;
  const latestRun = workPackageRaw && typeof workPackageRaw.latest_run === "object" && workPackageRaw.latest_run !== null
    ? workPackageRaw.latest_run as Record<string, unknown>
    : null;
  const workPackage = workPackageRaw ? {
    plan_id: String(workPackageRaw.plan_id || ""),
    project_id: String(workPackageRaw.project_id || ""),
    lane_id: String(workPackageRaw.lane_id || ""),
    task_id: String(workPackageRaw.task_id || raw.task_id || ""),
    work_package_id: String(workPackageRaw.work_package_id || workPackageRaw.task_id || raw.task_id || ""),
    title: String(workPackageRaw.title || "Untitled work package"),
    description: String(workPackageRaw.description || ""),
    owner_agent_id: String(workPackageRaw.owner_agent_id || ""),
    collaborator_agent_ids: asArray<unknown>(workPackageRaw.collaborator_agent_ids).map(String),
    status: String(workPackageRaw.status || "unknown"),
    package_status: String(workPackageRaw.package_status || workPackageRaw.status || "unknown"),
    priority: String(workPackageRaw.priority || "medium"),
    risk_level: String(workPackageRaw.risk_level || "medium"),
    acceptance_criteria: String(workPackageRaw.acceptance_criteria || ""),
    dependencies: asArray<unknown>(workPackageRaw.dependencies).map(String),
    verification_commands: asArray<unknown>(workPackageRaw.verification_commands).map(String),
    scope: String(workPackageRaw.scope || ""),
    avoid_scope: String(workPackageRaw.avoid_scope || ""),
    latest_run: latestRun ? {
      run_id: latestRun.run_id ? String(latestRun.run_id) : undefined,
      status: latestRun.status ? String(latestRun.status) : undefined,
      agent_id: latestRun.agent_id ? String(latestRun.agent_id) : undefined,
      runtime_type: latestRun.runtime_type ? String(latestRun.runtime_type) : undefined,
      created_at: latestRun.created_at ? String(latestRun.created_at) : undefined,
      ended_at: latestRun.ended_at ? String(latestRun.ended_at) : null,
      error_type: latestRun.error_type ? String(latestRun.error_type) : null,
      error_message: latestRun.error_message ? String(latestRun.error_message) : null,
    } : null,
    evidence_counts: numberRecord(workPackageRaw.evidence_counts),
    recommended_action: workPackageRaw.recommended_action ? String(workPackageRaw.recommended_action) : undefined,
    created_at: workPackageRaw.created_at ? String(workPackageRaw.created_at) : undefined,
    updated_at: workPackageRaw.updated_at ? String(workPackageRaw.updated_at) : undefined,
  } : null;
  return {
    provider: String(raw.provider || "agentops-commander"),
    operation: String(raw.operation || "work_package_dispatch"),
    ok: boolValue(raw.ok),
    dry_run: boolValue(raw.dry_run),
    adapter: String(raw.adapter || input.adapter || "mock"),
    task_id: String(raw.task_id || input.task_id),
    agent_id: raw.agent_id ? String(raw.agent_id) : null,
    run_id: raw.run_id ? String(raw.run_id) : null,
    work_package: workPackage,
    evidence: numberRecord(raw.evidence),
    duration_ms: raw.duration_ms === null || raw.duration_ms === undefined ? null : numberValue(raw.duration_ms, 0),
    error: raw.error ? String(raw.error) : null,
    reason: raw.reason ? String(raw.reason) : null,
    requires: typeof raw.requires === "object" && raw.requires !== null ? raw.requires as Record<string, boolean> : undefined,
    safety: {
      ledger_mutated: boolValue(safetyRaw.ledger_mutated),
      run_created: boolValue(safetyRaw.run_created),
      live_execution_performed: boolValue(safetyRaw.live_execution_performed),
      token_omitted: boolValue(safetyRaw.token_omitted),
      raw_prompt_omitted: boolValue(safetyRaw.raw_prompt_omitted),
    },
    token_omitted: boolValue(raw.token_omitted),
    live_execution_performed: boolValue(raw.live_execution_performed),
  };
}

export async function dispatchCommanderWorkPackageBatch(input: {
  project_id?: string;
  plan_id?: string;
  task_ids?: string[];
  status?: string;
  limit?: number;
  adapter?: WorkerAdapterName;
  confirm_run?: boolean;
  hermes_timeout?: number;
}): Promise<CommanderWorkPackageDispatchBatchPayload> {
  const raw = await apiJsonWithStatuses<Record<string, unknown>>("/commander/work-packages/dispatch-batch", {
    method: "POST",
    body: JSON.stringify({
      project_id: input.project_id,
      plan_id: input.plan_id,
      task_ids: input.task_ids || [],
      status: input.status || "planned",
      limit: input.limit || 5,
      adapter: input.adapter || "mock",
      confirm_run: Boolean(input.confirm_run),
      hermes_timeout: input.hermes_timeout,
    }),
  }, [409, 404]);
  const safetyRaw = typeof raw.safety === "object" && raw.safety !== null ? raw.safety as Record<string, unknown> : {};
  const filterRaw = typeof raw.filter === "object" && raw.filter !== null ? raw.filter as Record<string, unknown> : {};
  return {
    provider: String(raw.provider || "agentops-commander"),
    operation: String(raw.operation || "work_package_dispatch_batch"),
    ok: boolValue(raw.ok),
    status: raw.status ? String(raw.status) : undefined,
    adapter: String(raw.adapter || input.adapter || "mock"),
    confirm_run: boolValue(raw.confirm_run),
    jobs: asArray<WorkflowJob>(raw.jobs),
    job_ids: asArray<unknown>(raw.job_ids).map(String),
    task_ids: asArray<unknown>(raw.task_ids).map(String),
    status_urls: asArray<unknown>(raw.status_urls).map(String),
    reason: raw.reason ? String(raw.reason) : null,
    filter: {
      project_id: filterRaw.project_id ? String(filterRaw.project_id) : null,
      plan_id: filterRaw.plan_id ? String(filterRaw.plan_id) : null,
      status: filterRaw.status ? String(filterRaw.status) : undefined,
      limit: filterRaw.limit === undefined ? undefined : numberValue(filterRaw.limit, input.limit || 5),
    },
    team_board_after_queue: parseCommanderTeamBoardPayload(raw.team_board_after_queue, "local-demo"),
    safety: {
      ledger_mutated: boolValue(safetyRaw.ledger_mutated),
      jobs_created: numberValue(safetyRaw.jobs_created, 0),
      live_execution_performed: boolValue(safetyRaw.live_execution_performed),
      token_omitted: boolValue(safetyRaw.token_omitted),
      raw_prompt_omitted: boolValue(safetyRaw.raw_prompt_omitted),
    },
    token_omitted: boolValue(raw.token_omitted),
    live_execution_performed: boolValue(raw.live_execution_performed),
  };
}

export async function synthesizeCommanderWorkPackages(input: {
  project_id?: string;
  plan_id?: string;
  task_ids?: string[];
  status?: string;
  limit?: number;
  confirm_create?: boolean;
  artifact_id?: string;
}): Promise<CommanderWorkPackageSynthesisPayload> {
  const raw = await apiJsonWithStatuses<Record<string, unknown>>("/commander/work-packages/synthesize", {
    method: "POST",
    body: JSON.stringify({
      project_id: input.project_id,
      plan_id: input.plan_id,
      task_ids: input.task_ids || [],
      status: input.status || "ready_for_review",
      limit: input.limit || 10,
      confirm_create: Boolean(input.confirm_create),
      artifact_id: input.artifact_id,
    }),
  }, [404]);
  const safetyRaw = typeof raw.safety === "object" && raw.safety !== null ? raw.safety as Record<string, unknown> : {};
  return {
    provider: String(raw.provider || "agentops-commander"),
    operation: String(raw.operation || "work_package_synthesis"),
    ok: boolValue(raw.ok),
    status: String(raw.status || "unknown"),
    workspace_id: String(raw.workspace_id || "local-demo"),
    project_id: raw.project_id ? String(raw.project_id) : undefined,
    plan_id: raw.plan_id ? String(raw.plan_id) : undefined,
    artifact_id: raw.artifact_id ? String(raw.artifact_id) : null,
    approval_id: raw.approval_id ? String(raw.approval_id) : null,
    review_approval: typeof raw.review_approval === "object" && raw.review_approval !== null ? raw.review_approval as Record<string, unknown> : null,
    markdown: raw.markdown ? String(raw.markdown) : undefined,
    content_hash: raw.content_hash ? String(raw.content_hash) : undefined,
    package_count: raw.package_count === undefined ? undefined : numberValue(raw.package_count, 0),
    packages: asArray<CommanderWorkPackageReadbackPayload["work_packages"][number]>(raw.packages),
    evidence_totals: numberRecord(raw.evidence_totals),
    safety: {
      ledger_mutated: boolValue(safetyRaw.ledger_mutated),
      artifact_created: boolValue(safetyRaw.artifact_created),
      live_execution_performed: boolValue(safetyRaw.live_execution_performed),
      token_omitted: boolValue(safetyRaw.token_omitted),
      raw_prompt_omitted: boolValue(safetyRaw.raw_prompt_omitted),
    },
    token_omitted: boolValue(raw.token_omitted),
    live_execution_performed: boolValue(raw.live_execution_performed),
  };
}

export async function promoteCommanderSynthesis(input: {
  artifact_id: string;
  approval_id?: string;
  mode?: "memory" | "delivery" | "both";
  confirm_promote?: boolean;
  project_id?: string;
  memory_id?: string;
  delivery_artifact_id?: string;
}): Promise<CommanderSynthesisPromotionPayload> {
  const raw = await apiJsonWithStatuses<Record<string, unknown>>("/commander/work-packages/synthesis/promote", {
    method: "POST",
    body: JSON.stringify({
      artifact_id: input.artifact_id,
      approval_id: input.approval_id,
      mode: input.mode || "both",
      confirm_promote: Boolean(input.confirm_promote),
      project_id: input.project_id,
      memory_id: input.memory_id,
      delivery_artifact_id: input.delivery_artifact_id,
    }),
  }, [409]);
  const safetyRaw = typeof raw.safety === "object" && raw.safety !== null ? raw.safety as Record<string, unknown> : {};
  return {
    provider: String(raw.provider || "agentops-commander"),
    operation: String(raw.operation || "work_package_synthesis_promote"),
    ok: boolValue(raw.ok),
    status: String(raw.status || "unknown"),
    workspace_id: String(raw.workspace_id || "local-demo"),
    artifact_id: String(raw.artifact_id || input.artifact_id),
    approval_id: raw.approval_id ? String(raw.approval_id) : null,
    approval_decision: raw.approval_decision ? String(raw.approval_decision) : undefined,
    mode: String(raw.mode || input.mode || "both"),
    memory_id: raw.memory_id ? String(raw.memory_id) : null,
    delivery_artifact_id: raw.delivery_artifact_id ? String(raw.delivery_artifact_id) : null,
    created: typeof raw.created === "object" && raw.created !== null ? raw.created as Record<string, unknown> : undefined,
    safety: {
      ledger_mutated: boolValue(safetyRaw.ledger_mutated),
      live_execution_performed: boolValue(safetyRaw.live_execution_performed),
      token_omitted: boolValue(safetyRaw.token_omitted),
      raw_prompt_omitted: boolValue(safetyRaw.raw_prompt_omitted),
      raw_response_omitted: boolValue(safetyRaw.raw_response_omitted),
      memory_candidate_created: safetyRaw.memory_candidate_created === undefined ? undefined : boolValue(safetyRaw.memory_candidate_created),
      customer_delivery_artifact_created: safetyRaw.customer_delivery_artifact_created === undefined ? undefined : boolValue(safetyRaw.customer_delivery_artifact_created),
    },
    token_omitted: boolValue(raw.token_omitted),
    live_execution_performed: boolValue(raw.live_execution_performed),
  };
}

export async function closeExecutionEvidenceGap(input: {
  run_id: string;
  decision?: "accepted_remediation" | "waived" | "reopen";
  reason?: string;
  note?: string;
  synthesis_artifact_id?: string;
  remediation_task_id?: string;
  confirm_close?: boolean;
}): Promise<ExecutionEvidenceGapDecisionPayload> {
  const raw = await apiJsonWithStatuses<Record<string, unknown>>("/operator/execution-evidence/close-gap", {
    method: "POST",
    body: JSON.stringify({
      run_id: input.run_id,
      decision: input.decision || "accepted_remediation",
      reason: input.reason,
      note: input.note,
      synthesis_artifact_id: input.synthesis_artifact_id,
      remediation_task_id: input.remediation_task_id,
      confirm_close: Boolean(input.confirm_close),
    }),
  }, [400, 409]);
  const safetyRaw = typeof raw.safety === "object" && raw.safety !== null ? raw.safety as Record<string, unknown> : {};
  return {
    provider: raw.provider ? String(raw.provider) : undefined,
    operation: raw.operation ? String(raw.operation) : undefined,
    ok: raw.ok === undefined ? undefined : boolValue(raw.ok),
    status: String(raw.status || raw.error || "unknown"),
    error: raw.error ? String(raw.error) : undefined,
    message: raw.message ? String(raw.message) : undefined,
    closed: raw.closed === undefined ? undefined : boolValue(raw.closed),
    workspace_id: raw.workspace_id ? String(raw.workspace_id) : undefined,
    run_id: raw.run_id ? String(raw.run_id) : input.run_id,
    decision: typeof raw.decision === "object" && raw.decision !== null ? raw.decision as Record<string, unknown> : undefined,
    gap: typeof raw.gap === "object" && raw.gap !== null ? raw.gap as Record<string, unknown> : null,
    next_actions: asArray(raw.next_actions).map(String),
    recommended_action: raw.recommended_action ? String(raw.recommended_action) : undefined,
    safety: {
      read_only: safetyRaw.read_only === undefined ? undefined : boolValue(safetyRaw.read_only),
      ledger_mutated: safetyRaw.ledger_mutated === undefined ? undefined : boolValue(safetyRaw.ledger_mutated),
      live_execution_performed: safetyRaw.live_execution_performed === undefined ? undefined : boolValue(safetyRaw.live_execution_performed),
      raw_note_omitted: safetyRaw.raw_note_omitted === undefined ? undefined : boolValue(safetyRaw.raw_note_omitted),
      raw_prompt_omitted: safetyRaw.raw_prompt_omitted === undefined ? undefined : boolValue(safetyRaw.raw_prompt_omitted),
      raw_response_omitted: safetyRaw.raw_response_omitted === undefined ? undefined : boolValue(safetyRaw.raw_response_omitted),
      token_omitted: safetyRaw.token_omitted === undefined ? undefined : boolValue(safetyRaw.token_omitted),
    },
    token_omitted: raw.token_omitted === undefined ? undefined : boolValue(raw.token_omitted),
    live_execution_performed: raw.live_execution_performed === undefined ? undefined : boolValue(raw.live_execution_performed),
  };
}

export async function loadReviewQueue(limit = 12): Promise<ReviewQueuePayload> {
  const raw = await optionalApiJson<Record<string, unknown>>(`/review/queue?limit=${encodeURIComponent(String(limit))}`, {
    provider: "agentops-review",
    operation: "human_review_queue",
    status: "unavailable",
    limit,
    summary: {},
    review_items: [],
    gates: [],
    next_actions: [],
    safety: {
      read_only: true,
      ledger_mutated: false,
      live_execution_performed: false,
      raw_prompt_omitted: true,
      raw_response_omitted: true,
      token_omitted: true,
    },
    token_omitted: true,
    fallback_reason: "endpoint_not_available",
  });
  const summaryRaw = typeof raw.summary === "object" && raw.summary !== null ? raw.summary as Record<string, unknown> : {};
  const safetyRaw = typeof raw.safety === "object" && raw.safety !== null ? raw.safety as Record<string, unknown> : {};
  return {
    provider: String(raw.provider || "agentops-review"),
    operation: String(raw.operation || "human_review_queue"),
    status: String(raw.status || "unknown"),
    limit: numberValue(raw.limit, limit),
    summary: {
      pending_approvals: numberValue(summaryRaw.pending_approvals, 0),
      memory_candidates: numberValue(summaryRaw.memory_candidates, 0),
      evaluation_case_candidates: numberValue(summaryRaw.evaluation_case_candidates, 0),
      failed_evaluation_case_runs: numberValue(summaryRaw.failed_evaluation_case_runs, 0),
      ready_deliveries: numberValue(summaryRaw.ready_deliveries, 0),
      waiting_deliveries: numberValue(summaryRaw.waiting_deliveries, 0),
      needs_attention_deliveries: numberValue(summaryRaw.needs_attention_deliveries, 0),
      commander_synthesis_pending_reviews: numberValue(summaryRaw.commander_synthesis_pending_reviews, 0),
      commander_synthesis_promotion_available: numberValue(summaryRaw.commander_synthesis_promotion_available, 0),
      commander_synthesis_memory_reviews: numberValue(summaryRaw.commander_synthesis_memory_reviews, 0),
      review_items_total: numberValue(summaryRaw.review_items_total, 0),
      returned_items: numberValue(summaryRaw.returned_items, 0),
      retrieved_pending_approvals: numberValue(summaryRaw.retrieved_pending_approvals, 0),
      retrieved_memory_candidates: numberValue(summaryRaw.retrieved_memory_candidates, 0),
      retrieved_evaluation_case_candidates: numberValue(summaryRaw.retrieved_evaluation_case_candidates, 0),
      retrieved_failed_evaluation_case_runs: numberValue(summaryRaw.retrieved_failed_evaluation_case_runs, 0),
    },
    review_items: asArray<Record<string, unknown>>(raw.review_items).map((item) => ({
      item_type: String(item.item_type || "review_item"),
      item_id: String(item.item_id || item.approval_id || item.memory_id || item.artifact_id || ""),
      status: String(item.status || "unknown"),
      review_status: item.review_status ? String(item.review_status) : undefined,
      kind: item.kind ? String(item.kind) : null,
      title: String(item.title || item.item_id || "Review item"),
      summary: item.summary ? String(item.summary) : undefined,
      task_id: item.task_id ? String(item.task_id) : null,
      run_id: item.run_id ? String(item.run_id) : null,
      agent_id: item.agent_id ? String(item.agent_id) : null,
      artifact_id: item.artifact_id ? String(item.artifact_id) : null,
      created_at: item.created_at ? String(item.created_at) : undefined,
      updated_at: item.updated_at ? String(item.updated_at) : undefined,
      priority: numberValue(item.priority, 0),
      next_action: item.next_action ? String(item.next_action) : undefined,
      cli_action: item.cli_action ? String(item.cli_action) : undefined,
      alternate_cli_action: item.alternate_cli_action ? String(item.alternate_cli_action) : null,
      links: typeof item.links === "object" && item.links !== null ? item.links as Record<string, string | null> : undefined,
    })).filter((item) => item.item_id || item.title),
    gates: asArray<Record<string, unknown>>(raw.gates).map((gate) => ({
      id: String(gate.id || gate.label || ""),
      label: String(gate.label || gate.id || ""),
      ok: boolValue(gate.ok),
      value: typeof gate.value === "string" || typeof gate.value === "number" || typeof gate.value === "boolean" ? gate.value : undefined,
    })),
    next_actions: asArray<unknown>(raw.next_actions).map(String).filter(Boolean),
    safety: {
      read_only: boolValue(safetyRaw.read_only),
      ledger_mutated: boolValue(safetyRaw.ledger_mutated),
      live_execution_performed: boolValue(safetyRaw.live_execution_performed),
      raw_prompt_omitted: boolValue(safetyRaw.raw_prompt_omitted),
      raw_response_omitted: boolValue(safetyRaw.raw_response_omitted),
      token_omitted: boolValue(safetyRaw.token_omitted),
    },
    token_omitted: boolValue(raw.token_omitted),
  };
}

function normalizeExecutionEvidenceGaps(rawValue: unknown): ExecutionEvidenceGapsPayload {
  const raw = typeof rawValue === "object" && rawValue !== null ? rawValue as Record<string, unknown> : {};
  const summaryRaw = typeof raw.summary === "object" && raw.summary !== null ? raw.summary as Record<string, unknown> : {};
  const safetyRaw = typeof raw.safety === "object" && raw.safety !== null ? raw.safety as Record<string, unknown> : {};
  return {
    provider: raw.provider ? String(raw.provider) : undefined,
    operation: raw.operation ? String(raw.operation) : undefined,
    status: raw.status ? String(raw.status) : undefined,
    workspace_id: raw.workspace_id ? String(raw.workspace_id) : undefined,
    summary: numberRecord(summaryRaw),
    gaps: asArray<Record<string, unknown>>(raw.gaps).map((item) => ({
      run_id: String(item.run_id || ""),
      task_id: item.task_id ? String(item.task_id) : null,
      agent_id: item.agent_id ? String(item.agent_id) : null,
      task_title: item.task_title ? String(item.task_title) : undefined,
      run_status: item.run_status ? String(item.run_status) : undefined,
      task_status: item.task_status ? String(item.task_status) : null,
      remediation_task_id: item.remediation_task_id ? String(item.remediation_task_id) : null,
      remediation_status: item.remediation_status ? String(item.remediation_status) : null,
      remediation_synthesis_status: item.remediation_synthesis_status ? String(item.remediation_synthesis_status) : null,
      remediation_synthesis_artifact_id: item.remediation_synthesis_artifact_id ? String(item.remediation_synthesis_artifact_id) : null,
      remediation_synthesis_approval_id: item.remediation_synthesis_approval_id ? String(item.remediation_synthesis_approval_id) : null,
      gap_decision_status: item.gap_decision_status ? String(item.gap_decision_status) : null,
      gap_decision_type: item.gap_decision_type ? String(item.gap_decision_type) : null,
      gap_decision: typeof item.gap_decision === "object" && item.gap_decision !== null ? item.gap_decision as Record<string, unknown> : null,
      gap_types: asArray<unknown>(item.gap_types).map(String),
      missing_evidence: asArray<unknown>(item.missing_evidence).map(String),
      severity: String(item.severity || "attention"),
      priority: numberValue(item.priority, 0),
      command: String(item.command || ""),
      next_action: item.next_action ? String(item.next_action) : undefined,
      ui_route: item.ui_route ? String(item.ui_route) : null,
      token_omitted: item.token_omitted === undefined ? undefined : boolValue(item.token_omitted),
    })).filter((item) => item.run_id),
    next_actions: asArray<unknown>(raw.next_actions).map(String).filter(Boolean),
    safety: {
      read_only: boolValue(safetyRaw.read_only),
      ledger_mutated: boolValue(safetyRaw.ledger_mutated),
      live_execution_performed: boolValue(safetyRaw.live_execution_performed),
      token_omitted: boolValue(safetyRaw.token_omitted),
    },
    token_omitted: raw.token_omitted === undefined ? undefined : boolValue(raw.token_omitted),
  };
}

function normalizeTaskIntakeChecklist(rawValue: unknown): TaskIntakeChecklistPayload {
  const raw = typeof rawValue === "object" && rawValue !== null ? rawValue as Record<string, unknown> : {};
  const summaryRaw = typeof raw.summary === "object" && raw.summary !== null ? raw.summary as Record<string, unknown> : {};
  const safetyRaw = typeof raw.safety === "object" && raw.safety !== null ? raw.safety as Record<string, unknown> : {};
  const localLoopAdmissionRaw = typeof raw.local_loop_admission_summary === "object" && raw.local_loop_admission_summary !== null ? raw.local_loop_admission_summary as Record<string, unknown> : {};
  return {
    provider: raw.provider ? String(raw.provider) : undefined,
    operation: raw.operation ? String(raw.operation) : undefined,
    status: raw.status ? String(raw.status) : undefined,
    workspace_id: raw.workspace_id ? String(raw.workspace_id) : undefined,
    summary: numberRecord(summaryRaw),
    local_loop_admission_summary: normalizeLocalLoopAdmissionSummary(localLoopAdmissionRaw),
    items: asArray<Record<string, unknown>>(raw.items).map((item) => ({
      task_id: String(item.task_id || ""),
      title: String(item.title || item.task_id || "Task intake"),
      status: String(item.status || "planned"),
      priority: item.priority ? String(item.priority) : undefined,
      risk_level: item.risk_level ? String(item.risk_level) : undefined,
      assigned_agent_ids: asArray<unknown>(item.assigned_agent_ids).map(String),
      plan_id: item.plan_id ? String(item.plan_id) : null,
      plan_status: item.plan_status ? String(item.plan_status) : null,
      plan_verified: boolValue(item.plan_verified),
      plan_verified_at: item.plan_verified_at ? String(item.plan_verified_at) : null,
      referenced_specs: numberValue(item.referenced_specs, 0),
      referenced_memories: numberValue(item.referenced_memories, 0),
      referenced_bases: numberValue(item.referenced_bases, 0),
      gates: asArray<Record<string, unknown>>(item.gates).map((gate) => ({
        id: String(gate.id || ""),
        ok: boolValue(gate.ok),
        status: String(gate.status || (boolValue(gate.ok) ? "pass" : "attention")),
        message: gate.message ? String(gate.message) : undefined,
      })).filter((gate) => gate.id),
      failed_gate_ids: asArray<unknown>(item.failed_gate_ids).map(String),
      severity: String(item.severity || "attention"),
      priority_score: numberValue(item.priority_score, 0),
      command: String(item.command || ""),
      next_action: item.next_action ? String(item.next_action) : undefined,
      ui_route: item.ui_route ? String(item.ui_route) : null,
      token_omitted: item.token_omitted === undefined ? undefined : boolValue(item.token_omitted),
    })).filter((item) => item.task_id),
    next_actions: asArray<unknown>(raw.next_actions).map(String).filter(Boolean),
    safety: {
      read_only: boolValue(safetyRaw.read_only),
      ledger_mutated: boolValue(safetyRaw.ledger_mutated),
      live_execution_performed: boolValue(safetyRaw.live_execution_performed),
      token_omitted: boolValue(safetyRaw.token_omitted),
    },
    token_omitted: raw.token_omitted === undefined ? undefined : boolValue(raw.token_omitted),
  };
}

function normalizeLocalLoopAdmissionSummary(rawValue: unknown): LocalLoopAdmissionSummary | undefined {
  const raw = typeof rawValue === "object" && rawValue !== null ? rawValue as Record<string, unknown> : {};
  if (!Object.keys(raw).length) return undefined;
  const safetyRaw = typeof raw.safety === "object" && raw.safety !== null ? raw.safety as Record<string, unknown> : {};
  return {
    operation: raw.operation ? String(raw.operation) : undefined,
    adapter: raw.adapter === null || raw.adapter === undefined ? null : String(raw.adapter),
    agent_id: raw.agent_id === null || raw.agent_id === undefined ? null : String(raw.agent_id),
    live_adapter_tasks_checked: numberValue(raw.live_adapter_tasks_checked, 0),
    live_adapters: asArray<unknown>(raw.live_adapters).map(String).filter(Boolean),
    passed_local_loop_admission: numberValue(raw.passed_local_loop_admission, 0),
    missing_local_loop_admission: numberValue(raw.missing_local_loop_admission, 0),
    local_loop_admission_ready: boolValue(raw.local_loop_admission_ready),
    required_method_gates: asArray<unknown>(raw.required_method_gates).map(String).filter(Boolean),
    next_safe_commands: asArray<unknown>(raw.next_safe_commands).map(String).filter(Boolean),
    safety: {
      read_only: boolValue(safetyRaw.read_only),
      ledger_mutated: boolValue(safetyRaw.ledger_mutated),
      live_execution_performed: boolValue(safetyRaw.live_execution_performed),
      server_executes_shell: boolValue(safetyRaw.server_executes_shell),
      token_omitted: boolValue(safetyRaw.token_omitted),
    },
    token_omitted: raw.token_omitted === undefined ? undefined : boolValue(raw.token_omitted),
  };
}

function normalizeOperatorActionReceipt(raw: Record<string, unknown>): OperatorActionReceipt {
  return {
    receipt_id: String(raw.receipt_id || raw.audit_id || ""),
    audit_id: raw.audit_id ? String(raw.audit_id) : undefined,
    actor_id: raw.actor_id ? String(raw.actor_id) : undefined,
    workspace_id: String(raw.workspace_id || "local-demo"),
    status: String(raw.status || "recorded"),
    source: String(raw.source || "operator_action_queue"),
    action_id: raw.action_id ? String(raw.action_id) : null,
    action_signature: raw.action_signature ? String(raw.action_signature) : null,
    action_command: raw.action_command ? String(raw.action_command) : null,
    action_hash: raw.action_hash ? String(raw.action_hash) : null,
    verify_command: raw.verify_command ? String(raw.verify_command) : null,
    verify_hash: raw.verify_hash ? String(raw.verify_hash) : null,
    result_summary: raw.result_summary ? String(raw.result_summary) : null,
    evaluation: typeof raw.evaluation === "object" && raw.evaluation !== null ? raw.evaluation as Record<string, unknown> : null,
    evaluation_id: raw.evaluation_id ? String(raw.evaluation_id) : null,
    evaluation_pass_fail: raw.evaluation_pass_fail ? String(raw.evaluation_pass_fail) : null,
    evaluation_score: raw.evaluation_score === undefined || raw.evaluation_score === null ? null : numberValue(raw.evaluation_score, 0),
    control_readback: typeof raw.control_readback === "object" && raw.control_readback !== null ? raw.control_readback as Record<string, unknown> : null,
    control_readback_id: raw.control_readback_id ? String(raw.control_readback_id) : null,
    control_readback_hash: raw.control_readback_hash ? String(raw.control_readback_hash) : null,
    created_at: raw.created_at ? String(raw.created_at) : undefined,
    tamper_chain_hash: raw.tamper_chain_hash ? String(raw.tamper_chain_hash) : undefined,
    token_omitted: raw.token_omitted === undefined ? undefined : boolValue(raw.token_omitted),
  };
}

export async function loadOperatorActionReceipts(limit = 8): Promise<OperatorActionReceiptsPayload> {
  const raw = await optionalApiJson<Record<string, unknown>>(`/operator/action-receipts?limit=${encodeURIComponent(String(limit))}`, {
    provider: "agentops-operator",
    operation: "operator_action_receipts",
    status: "unavailable",
    workspace_id: "local-demo",
    summary: {},
    receipts: [],
    safety: {
      read_only: true,
      ledger_mutated: false,
      live_execution_performed: false,
      raw_prompt_omitted: true,
      raw_response_omitted: true,
      token_omitted: true,
    },
    token_omitted: true,
  });
  const summaryRaw = typeof raw.summary === "object" && raw.summary !== null ? raw.summary as Record<string, unknown> : {};
  const safetyRaw = typeof raw.safety === "object" && raw.safety !== null ? raw.safety as Record<string, unknown> : {};
  return {
    provider: String(raw.provider || "agentops-operator"),
    operation: String(raw.operation || "operator_action_receipts"),
    status: String(raw.status || "unknown"),
    workspace_id: String(raw.workspace_id || "local-demo"),
    summary: {
      receipts: numberValue(summaryRaw.receipts, 0),
      recorded: numberValue(summaryRaw.recorded, 0),
      verified: numberValue(summaryRaw.verified, 0),
      failed: numberValue(summaryRaw.failed, 0),
      skipped: numberValue(summaryRaw.skipped, 0),
      evaluated: numberValue(summaryRaw.evaluated, 0),
      evaluation_pass: numberValue(summaryRaw.evaluation_pass, 0),
      evaluation_fail: numberValue(summaryRaw.evaluation_fail, 0),
      control_readback_required: numberValue(summaryRaw.control_readback_required, 0),
      control_readback_attached: numberValue(summaryRaw.control_readback_attached, 0),
      control_readback_missing: numberValue(summaryRaw.control_readback_missing, 0),
      control_readback_coverage_percent: numberValue(summaryRaw.control_readback_coverage_percent, 100),
      control_readback_status: String(summaryRaw.control_readback_status || "ready"),
      latest_control_readback_hash: summaryRaw.latest_control_readback_hash ? String(summaryRaw.latest_control_readback_hash) : null,
    },
    receipts: asArray<Record<string, unknown>>(raw.receipts).map(normalizeOperatorActionReceipt).filter(item => item.receipt_id),
    safety: {
      read_only: boolValue(safetyRaw.read_only),
      ledger_mutated: boolValue(safetyRaw.ledger_mutated),
      live_execution_performed: boolValue(safetyRaw.live_execution_performed),
      raw_prompt_omitted: boolValue(safetyRaw.raw_prompt_omitted),
      raw_response_omitted: boolValue(safetyRaw.raw_response_omitted),
      token_omitted: boolValue(safetyRaw.token_omitted),
    },
    token_omitted: boolValue(raw.token_omitted),
  };
}

export async function loadOperatorEvidenceReport(
  limit = 8,
  filters: { runId?: string; taskId?: string } = {},
): Promise<OperatorEvidenceReportPayload> {
  const query = new URLSearchParams({ limit: String(limit) });
  if (filters.runId) query.set("run_id", filters.runId);
  if (filters.taskId) query.set("task_id", filters.taskId);
  const raw = await optionalApiJson<Record<string, unknown>>(`/operator/evidence-report?${query.toString()}`, {
    provider: "agentops-operator",
    operation: "operator_evidence_report",
    status: "unavailable",
    workspace_id: "local-demo",
    summary: {},
    runs: [],
    recommended_commands: ["agentops operator evidence-report --limit 8"],
    contract: "read-only execution evidence report; does not mutate the ledger",
    safety: {
      read_only: true,
      ledger_mutated: false,
      live_execution_performed: false,
      raw_prompt_omitted: true,
      raw_response_omitted: true,
      token_omitted: true,
    },
    token_omitted: true,
  });
  const summaryRaw = typeof raw.summary === "object" && raw.summary !== null ? raw.summary as Record<string, unknown> : {};
  const safetyRaw = typeof raw.safety === "object" && raw.safety !== null ? raw.safety as Record<string, unknown> : {};
  const normalizeReportRun = (item: Record<string, unknown>): OperatorEvidenceReportRun => {
    const agentPlanRaw = typeof item.agent_plan === "object" && item.agent_plan !== null ? item.agent_plan as Record<string, unknown> : {};
    const manifestRaw = typeof item.plan_evidence_manifest === "object" && item.plan_evidence_manifest !== null ? item.plan_evidence_manifest as Record<string, unknown> : {};
    const memoryReviewRaw = typeof item.memory_review === "object" && item.memory_review !== null ? item.memory_review as Record<string, unknown> : {};
    const approvalsRaw = typeof item.approvals === "object" && item.approvals !== null ? item.approvals as Record<string, unknown> : {};
    const workerKnowledgeRaw = typeof item.worker_knowledge_retrieval === "object" && item.worker_knowledge_retrieval !== null ? item.worker_knowledge_retrieval as Record<string, unknown> : {};
    const workerRuntimeRaw = typeof item.worker_runtime_summary === "object" && item.worker_runtime_summary !== null ? item.worker_runtime_summary as Record<string, unknown> : {};
    return {
      run_id: String(item.run_id || ""),
      task_id: item.task_id ? String(item.task_id) : null,
      agent_id: item.agent_id ? String(item.agent_id) : null,
      run_status: item.run_status ? String(item.run_status) : null,
      status: String(item.status || "unknown"),
      failed_check_ids: asArray<unknown>(item.failed_check_ids).map(String).filter(Boolean),
      checks: asArray<Record<string, unknown>>(item.checks).map(check => ({
        id: String(check.id || ""),
        ok: boolValue(check.ok),
        message: check.message ? String(check.message) : undefined,
      })).filter(check => check.id),
      evidence_counts: Object.fromEntries(
        Object.entries(typeof item.evidence_counts === "object" && item.evidence_counts !== null ? item.evidence_counts as Record<string, unknown> : {})
          .map(([key, value]) => [key, numberValue(value, 0)]),
      ),
      agent_plan: {
        plan_id: agentPlanRaw.plan_id ? String(agentPlanRaw.plan_id) : null,
        status: agentPlanRaw.status ? String(agentPlanRaw.status) : null,
        risk_level: agentPlanRaw.risk_level ? String(agentPlanRaw.risk_level) : null,
        approval_required: boolValue(agentPlanRaw.approval_required),
        approval_id: agentPlanRaw.approval_id ? String(agentPlanRaw.approval_id) : null,
        approval_decision: agentPlanRaw.approval_decision ? String(agentPlanRaw.approval_decision) : null,
        verification_pass: boolValue(agentPlanRaw.verification_pass),
        plan_hash: agentPlanRaw.plan_hash ? String(agentPlanRaw.plan_hash) : null,
      },
      plan_evidence_manifest: {
        manifest_id: manifestRaw.manifest_id ? String(manifestRaw.manifest_id) : null,
        status: manifestRaw.status ? String(manifestRaw.status) : null,
        verification_pass: boolValue(manifestRaw.verification_pass),
        failed_check_ids: asArray<unknown>(manifestRaw.failed_check_ids).map(String).filter(Boolean),
      },
      memory_review: {
        status: String(memoryReviewRaw.status || "unknown"),
        total: numberValue(memoryReviewRaw.total, 0),
        pending_review: numberValue(memoryReviewRaw.pending_review, 0),
        approved: numberValue(memoryReviewRaw.approved, 0),
        status_counts: Object.fromEntries(
          Object.entries(typeof memoryReviewRaw.status_counts === "object" && memoryReviewRaw.status_counts !== null ? memoryReviewRaw.status_counts as Record<string, unknown> : {})
            .map(([key, value]) => [key, numberValue(value, 0)]),
        ),
        items: asArray<Record<string, unknown>>(memoryReviewRaw.items),
        raw_content_omitted: boolValue(memoryReviewRaw.raw_content_omitted),
        token_omitted: boolValue(memoryReviewRaw.token_omitted),
      },
      approvals: {
        count: numberValue(approvalsRaw.count, 0),
        pending: numberValue(approvalsRaw.pending, 0),
        approved: numberValue(approvalsRaw.approved, 0),
        rejected: numberValue(approvalsRaw.rejected, 0),
        items: asArray<Record<string, unknown>>(approvalsRaw.items),
      },
      worker_knowledge_retrieval: {
        applicable: boolValue(workerKnowledgeRaw.applicable),
        status: String(workerKnowledgeRaw.status || "not_applicable"),
        worker_tool_calls: numberValue(workerKnowledgeRaw.worker_tool_calls, 0),
        consumed_tool_calls: numberValue(workerKnowledgeRaw.consumed_tool_calls, 0),
        missing_tool_calls: numberValue(workerKnowledgeRaw.missing_tool_calls, 0),
        packet_hashes: asArray<unknown>(workerKnowledgeRaw.packet_hashes).map(String).filter(Boolean),
        query_hashes: asArray<unknown>(workerKnowledgeRaw.query_hashes).map(String).filter(Boolean),
        context_packet_hashes: asArray<unknown>(workerKnowledgeRaw.context_packet_hashes).map(String).filter(Boolean),
        context_block_count: numberValue(workerKnowledgeRaw.context_block_count, 0),
        approved_memory_ids: asArray<unknown>(workerKnowledgeRaw.approved_memory_ids).map(String).filter(Boolean),
        retrieval_ids: asArray<unknown>(workerKnowledgeRaw.retrieval_ids).map(String).filter(Boolean),
        source_hashes: asArray<unknown>(workerKnowledgeRaw.source_hashes).map(String).filter(Boolean),
        paths: asArray<unknown>(workerKnowledgeRaw.paths).map(String).filter(Boolean),
        raw_query_omitted: boolValue(workerKnowledgeRaw.raw_query_omitted),
        snippet_omitted: boolValue(workerKnowledgeRaw.snippet_omitted),
        raw_content_omitted: boolValue(workerKnowledgeRaw.raw_content_omitted),
        context_body_not_persisted: boolValue(workerKnowledgeRaw.context_body_not_persisted),
        raw_transcript_omitted: boolValue(workerKnowledgeRaw.raw_transcript_omitted),
        raw_prompt_omitted: boolValue(workerKnowledgeRaw.raw_prompt_omitted),
        raw_response_omitted: boolValue(workerKnowledgeRaw.raw_response_omitted),
        token_omitted: boolValue(workerKnowledgeRaw.token_omitted),
      },
      worker_runtime_summary: {
        applicable: boolValue(workerRuntimeRaw.applicable),
        status: String(workerRuntimeRaw.status || "not_applicable"),
        worker_tool_calls: numberValue(workerRuntimeRaw.worker_tool_calls, 0),
        summary_events: numberValue(workerRuntimeRaw.summary_events, 0),
        linked_summary_events: numberValue(workerRuntimeRaw.linked_summary_events, 0),
        event_ids: asArray<unknown>(workerRuntimeRaw.event_ids).map(String).filter(Boolean),
        tool_items: asArray<Record<string, unknown>>(workerRuntimeRaw.tool_items),
        events: asArray<Record<string, unknown>>(workerRuntimeRaw.events),
        event_is_worker_summary_not_raw_trace: boolValue(workerRuntimeRaw.event_is_worker_summary_not_raw_trace),
        raw_prompt_omitted: boolValue(workerRuntimeRaw.raw_prompt_omitted),
        raw_response_omitted: boolValue(workerRuntimeRaw.raw_response_omitted),
        token_omitted: boolValue(workerRuntimeRaw.token_omitted),
      },
      gap_decision: typeof item.gap_decision === "object" && item.gap_decision !== null ? item.gap_decision as Record<string, unknown> : null,
      recommended_commands: asArray<unknown>(item.recommended_commands).map(String).filter(Boolean),
      token_omitted: item.token_omitted === undefined ? undefined : boolValue(item.token_omitted),
    };
  };
  return {
    provider: String(raw.provider || "agentops-operator"),
    operation: String(raw.operation || "operator_evidence_report"),
    status: String(raw.status || "unknown"),
    workspace_id: String(raw.workspace_id || "local-demo"),
    summary: {
      runs: numberValue(summaryRaw.runs, 0),
      ready: numberValue(summaryRaw.ready, 0),
      attention: numberValue(summaryRaw.attention, 0),
      blocked: numberValue(summaryRaw.blocked, 0),
      verified_plan_evidence_manifests: numberValue(summaryRaw.verified_plan_evidence_manifests, 0),
      missing_plan_evidence_manifests: numberValue(summaryRaw.missing_plan_evidence_manifests, 0),
      pending_approvals: numberValue(summaryRaw.pending_approvals, 0),
      memory_reviews: numberValue(summaryRaw.memory_reviews, 0),
      memory_review_ready: numberValue(summaryRaw.memory_review_ready, 0),
      missing_memory_reviews: numberValue(summaryRaw.missing_memory_reviews, 0),
      pending_memory_reviews: numberValue(summaryRaw.pending_memory_reviews, 0),
      approval_required_plans: numberValue(summaryRaw.approval_required_plans, 0),
      approved_required_plans: numberValue(summaryRaw.approved_required_plans, 0),
      action_receipts: numberValue(summaryRaw.action_receipts, 0),
      verified_action_receipts: numberValue(summaryRaw.verified_action_receipts, 0),
      evaluated_action_receipts: numberValue(summaryRaw.evaluated_action_receipts, 0),
      worker_runs: numberValue(summaryRaw.worker_runs, 0),
      worker_knowledge_retrieval_ready: numberValue(summaryRaw.worker_knowledge_retrieval_ready, 0),
      worker_knowledge_retrieval_missing: numberValue(summaryRaw.worker_knowledge_retrieval_missing, 0),
      worker_knowledge_retrieval_unavailable: numberValue(summaryRaw.worker_knowledge_retrieval_unavailable, 0),
      worker_runtime_summary_ready: numberValue(summaryRaw.worker_runtime_summary_ready, 0),
      worker_runtime_summary_missing: numberValue(summaryRaw.worker_runtime_summary_missing, 0),
    },
    runs: asArray<Record<string, unknown>>(raw.runs).map(normalizeReportRun).filter(item => item.run_id),
    recommended_commands: asArray<unknown>(raw.recommended_commands).map(String).filter(Boolean),
    contract: raw.contract ? String(raw.contract) : undefined,
    safety: {
      read_only: boolValue(safetyRaw.read_only),
      ledger_mutated: boolValue(safetyRaw.ledger_mutated),
      live_execution_performed: boolValue(safetyRaw.live_execution_performed),
      raw_prompt_omitted: boolValue(safetyRaw.raw_prompt_omitted),
      raw_response_omitted: boolValue(safetyRaw.raw_response_omitted),
      token_omitted: boolValue(safetyRaw.token_omitted),
    },
    token_omitted: raw.token_omitted === undefined ? undefined : boolValue(raw.token_omitted),
  };
}

export async function recordOperatorActionReceipt(input: {
  action_command: string;
  verify_command?: string;
  action_id?: string;
  action_signature?: string;
  source?: string;
  status?: "recorded" | "verified" | "failed" | "skipped";
  result_summary?: string;
}): Promise<OperatorActionReceiptResult> {
  const raw = await apiJsonWithStatuses<Record<string, unknown>>("/operator/action-receipts", {
    method: "POST",
    body: JSON.stringify({
      action_command: input.action_command,
      verify_command: input.verify_command,
      action_id: input.action_id,
      action_signature: input.action_signature,
      source: input.source,
      status: input.status || "recorded",
      result_summary: input.result_summary,
    }),
  }, [400]);
  const safetyRaw = typeof raw.safety === "object" && raw.safety !== null ? raw.safety as Record<string, unknown> : {};
  const receiptRaw = typeof raw.receipt === "object" && raw.receipt !== null ? raw.receipt as Record<string, unknown> : undefined;
  const evaluationRaw = typeof raw.evaluation === "object" && raw.evaluation !== null ? raw.evaluation as Record<string, unknown> : undefined;
  return {
    provider: raw.provider ? String(raw.provider) : undefined,
    operation: raw.operation ? String(raw.operation) : undefined,
    status: String(raw.status || raw.error || input.status || "recorded"),
    workspace_id: raw.workspace_id ? String(raw.workspace_id) : undefined,
    receipt: receiptRaw ? normalizeOperatorActionReceipt(receiptRaw) : undefined,
    evaluation: evaluationRaw || null,
    next_actions: asArray<unknown>(raw.next_actions).map(String).filter(Boolean),
    safety: {
      read_only: boolValue(safetyRaw.read_only),
      ledger_mutated: boolValue(safetyRaw.ledger_mutated),
      live_execution_performed: boolValue(safetyRaw.live_execution_performed),
      raw_prompt_omitted: boolValue(safetyRaw.raw_prompt_omitted),
      raw_response_omitted: boolValue(safetyRaw.raw_response_omitted),
      token_omitted: boolValue(safetyRaw.token_omitted),
    },
    token_omitted: raw.token_omitted === undefined ? undefined : boolValue(raw.token_omitted),
  };
}

export async function recordOperatorActionControlReadback(input: {
  receipt_id: string;
  source?: string;
  control_readback: Record<string, unknown>;
}): Promise<{
  provider?: string;
  operation?: string;
  status: string;
  workspace_id?: string;
  readback?: Record<string, unknown> | null;
  safety?: {
    read_only: boolean;
    ledger_mutated: boolean;
    live_execution_performed: boolean;
    raw_prompt_omitted: boolean;
    raw_response_omitted: boolean;
    token_omitted: boolean;
  };
  token_omitted?: boolean;
}> {
  const raw = await apiJsonWithStatuses<Record<string, unknown>>("/operator/action-receipts/control-readback", {
    method: "POST",
    body: JSON.stringify({
      receipt_id: input.receipt_id,
      source: input.source || "ui.local_run_path.control_readback",
      control_readback: input.control_readback,
    }),
  }, [400, 404]);
  const safetyRaw = typeof raw.safety === "object" && raw.safety !== null ? raw.safety as Record<string, unknown> : {};
  return {
    provider: raw.provider ? String(raw.provider) : undefined,
    operation: raw.operation ? String(raw.operation) : undefined,
    status: String(raw.status || raw.error || "recorded"),
    workspace_id: raw.workspace_id ? String(raw.workspace_id) : undefined,
    readback: typeof raw.readback === "object" && raw.readback !== null ? raw.readback as Record<string, unknown> : null,
    safety: {
      read_only: boolValue(safetyRaw.read_only),
      ledger_mutated: boolValue(safetyRaw.ledger_mutated),
      live_execution_performed: boolValue(safetyRaw.live_execution_performed),
      raw_prompt_omitted: boolValue(safetyRaw.raw_prompt_omitted),
      raw_response_omitted: boolValue(safetyRaw.raw_response_omitted),
      token_omitted: boolValue(safetyRaw.token_omitted),
    },
    token_omitted: raw.token_omitted === undefined ? undefined : boolValue(raw.token_omitted),
  };
}

export async function proposeReceiptFailureMemory(input: {
  action_hash?: string;
  min_failures?: number;
  confirm_create?: boolean;
  memory_id?: string;
  canonical_text?: string;
}): Promise<Record<string, unknown>> {
  return apiJsonWithStatuses<Record<string, unknown>>("/operator/receipt-failure-memories/propose", {
    method: "POST",
    body: JSON.stringify({
      action_hash: input.action_hash || undefined,
      min_failures: input.min_failures || 2,
      confirm_create: Boolean(input.confirm_create),
      memory_id: input.memory_id || undefined,
      canonical_text: input.canonical_text || undefined,
    }),
  }, [200, 201, 400]);
}

export async function loadOperatorCommandCenter(limit = 12, projectId = ""): Promise<OperatorCommandCenterPayload> {
  const query = new URLSearchParams({ limit: String(limit) });
  if (projectId) {
    query.set("project_id", projectId);
  }
  const raw = await optionalApiJson<Record<string, unknown>>(`/operator/command-center?${query.toString()}`, {
    provider: "agentops-operator",
    operation: "operator_command_center",
    status: "unavailable",
    workspace_id: "local-demo",
    summary: {},
    projects: [],
    commander: {
      summary: {},
      packages: [],
      coding_evidence_gaps: [],
      recommended_next_actions: [],
      raw_source_omitted: true,
      raw_patch_omitted: true,
      token_omitted: true,
    },
    blocked_runs: [],
    approvals: { summary: {}, pending: [], next_actions: [] },
    deliveries: { summary: {}, items: [], next_actions: [] },
    workers: { stale_refs: [], next_actions: [] },
    operator_action_plan: { status: "unavailable", summary: {}, actions: [] },
    next_actions: [],
    contract: "read-only command-center BFF unavailable fallback",
    safety: {
      read_only: true,
      ledger_mutated: false,
      task_created: false,
      run_created: false,
      worktree_created: false,
      live_execution_performed: false,
      server_shell_execution: false,
      raw_prompt_omitted: true,
      raw_response_omitted: true,
      raw_source_omitted: true,
      raw_patch_omitted: true,
      token_omitted: true,
    },
    token_omitted: true,
    live_execution_performed: false,
  });
  const summaryRaw = typeof raw.summary === "object" && raw.summary !== null ? raw.summary as Record<string, unknown> : {};
  const commanderRaw = typeof raw.commander === "object" && raw.commander !== null ? raw.commander as Record<string, unknown> : {};
  const approvalsRaw = typeof raw.approvals === "object" && raw.approvals !== null ? raw.approvals as Record<string, unknown> : {};
  const deliveriesRaw = typeof raw.deliveries === "object" && raw.deliveries !== null ? raw.deliveries as Record<string, unknown> : {};
  const workersRaw = typeof raw.workers === "object" && raw.workers !== null ? raw.workers as Record<string, unknown> : {};
  const operatorPlanRaw = typeof raw.operator_action_plan === "object" && raw.operator_action_plan !== null ? raw.operator_action_plan as Record<string, unknown> : {};
  const safetyRaw = typeof raw.safety === "object" && raw.safety !== null ? raw.safety as Record<string, unknown> : {};
  return {
    provider: String(raw.provider || "agentops-operator"),
    operation: String(raw.operation || "operator_command_center"),
    status: String(raw.status || "unknown"),
    workspace_id: String(raw.workspace_id || "local-demo"),
    summary: Object.fromEntries(Object.entries(summaryRaw).map(([key, value]) => [key, numberValue(value, 0)])),
    projects: asArray<Record<string, unknown>>(raw.projects),
    commander: {
      summary: typeof commanderRaw.summary === "object" && commanderRaw.summary !== null ? commanderRaw.summary as Record<string, unknown> : {},
      packages: asArray<Record<string, unknown>>(commanderRaw.packages),
      coding_evidence_gaps: asArray<Record<string, unknown>>(commanderRaw.coding_evidence_gaps),
      recommended_next_actions: asArray<unknown>(commanderRaw.recommended_next_actions),
      raw_source_omitted: boolValue(commanderRaw.raw_source_omitted),
      raw_patch_omitted: boolValue(commanderRaw.raw_patch_omitted),
      token_omitted: boolValue(commanderRaw.token_omitted),
    },
    blocked_runs: asArray<Record<string, unknown>>(raw.blocked_runs),
    approvals: {
      summary: typeof approvalsRaw.summary === "object" && approvalsRaw.summary !== null ? approvalsRaw.summary as Record<string, unknown> : {},
      pending: asArray<Record<string, unknown>>(approvalsRaw.pending),
      next_actions: asArray<unknown>(approvalsRaw.next_actions),
    },
    deliveries: {
      summary: typeof deliveriesRaw.summary === "object" && deliveriesRaw.summary !== null ? deliveriesRaw.summary as Record<string, unknown> : {},
      items: asArray<Record<string, unknown>>(deliveriesRaw.items),
      next_actions: asArray<unknown>(deliveriesRaw.next_actions),
    },
    workers: {
      status: workersRaw.status ? String(workersRaw.status) : undefined,
      fleet_health: typeof workersRaw.fleet_health === "object" && workersRaw.fleet_health !== null ? workersRaw.fleet_health as Record<string, unknown> : {},
      running_workers: numberValue(workersRaw.running_workers, 0),
      stuck_worker_tasks: numberValue(workersRaw.stuck_worker_tasks, 0),
      stuck_workflow_jobs: numberValue(workersRaw.stuck_workflow_jobs, 0),
      stale_refs: asArray<Record<string, unknown>>(workersRaw.stale_refs),
      next_actions: asArray<unknown>(workersRaw.next_actions).map(String).filter(Boolean),
    },
    operator_action_plan: {
      status: operatorPlanRaw.status ? String(operatorPlanRaw.status) : undefined,
      summary: typeof operatorPlanRaw.summary === "object" && operatorPlanRaw.summary !== null ? operatorPlanRaw.summary as Record<string, unknown> : {},
      actions: asArray<OperatorActionPlanItem>(operatorPlanRaw.actions),
      receipt_coverage: typeof operatorPlanRaw.receipt_coverage === "object" && operatorPlanRaw.receipt_coverage !== null ? operatorPlanRaw.receipt_coverage as Record<string, unknown> : undefined,
    },
    next_actions: asArray<Record<string, unknown>>(raw.next_actions).map((item) => ({
      action_id: String(item.action_id || item.command || ""),
      action_signature: item.action_signature ? String(item.action_signature) : null,
      source: String(item.source || "operator_command_center"),
      title: String(item.title || item.source || "Command center action"),
      priority: numberValue(item.priority, 0),
      command: String(item.command || ""),
      verify_command: item.verify_command ? String(item.verify_command) : null,
      evidence: typeof item.evidence === "object" && item.evidence !== null ? item.evidence as Record<string, unknown> : undefined,
      receipt_required: item.receipt_required === undefined ? true : boolValue(item.receipt_required),
      receipt_status: String(item.receipt_status || "missing"),
      receipt_verified: boolValue(item.receipt_verified),
      receipt_hash: item.receipt_hash ? String(item.receipt_hash) : null,
      receipt_record_command: item.receipt_record_command ? String(item.receipt_record_command) : null,
      receipt_verify_record_command: item.receipt_verify_record_command ? String(item.receipt_verify_record_command) : null,
      control_readback_required: item.control_readback_required === undefined ? undefined : boolValue(item.control_readback_required),
      control_readback_attached: item.control_readback_attached === undefined ? undefined : boolValue(item.control_readback_attached),
      token_omitted: item.token_omitted === undefined ? true : boolValue(item.token_omitted),
    })).filter((item) => item.command),
    contract: raw.contract ? String(raw.contract) : undefined,
    safety: {
      read_only: boolValue(safetyRaw.read_only),
      ledger_mutated: boolValue(safetyRaw.ledger_mutated),
      task_created: boolValue(safetyRaw.task_created),
      run_created: boolValue(safetyRaw.run_created),
      worktree_created: boolValue(safetyRaw.worktree_created),
      live_execution_performed: boolValue(safetyRaw.live_execution_performed),
      server_shell_execution: boolValue(safetyRaw.server_shell_execution),
      raw_prompt_omitted: boolValue(safetyRaw.raw_prompt_omitted),
      raw_response_omitted: boolValue(safetyRaw.raw_response_omitted),
      raw_source_omitted: boolValue(safetyRaw.raw_source_omitted),
      raw_patch_omitted: boolValue(safetyRaw.raw_patch_omitted),
      token_omitted: boolValue(safetyRaw.token_omitted),
    },
    token_omitted: raw.token_omitted === undefined ? undefined : boolValue(raw.token_omitted),
    live_execution_performed: raw.live_execution_performed === undefined ? undefined : boolValue(raw.live_execution_performed),
  };
}

export async function loadOperatorActionPlan(limit = 12): Promise<OperatorActionPlanPayload> {
  const raw = await optionalApiJson<Record<string, unknown>>(`/operator/action-plan?limit=${encodeURIComponent(String(limit))}`, {
    provider: "agentops-operator",
    operation: "action_plan",
    status: "unavailable",
    workspace_id: "local-demo",
    summary: {},
    actions: [],
    top_commands: [],
    source_status: {},
    safety: {
      read_only: true,
      ledger_mutated: false,
      live_execution_performed: false,
      raw_prompt_omitted: true,
      raw_response_omitted: true,
      token_omitted: true,
    },
    token_omitted: true,
    live_execution_performed: false,
  });
  const summaryRaw = typeof raw.summary === "object" && raw.summary !== null ? raw.summary as Record<string, unknown> : {};
  const safetyRaw = typeof raw.safety === "object" && raw.safety !== null ? raw.safety as Record<string, unknown> : {};
  return {
    provider: String(raw.provider || "agentops-operator"),
    operation: String(raw.operation || "action_plan"),
    status: String(raw.status || "unknown"),
    workspace_id: String(raw.workspace_id || "local-demo"),
    summary: {
      actions: numberValue(summaryRaw.actions, 0),
      blocked: numberValue(summaryRaw.blocked, 0),
      attention: numberValue(summaryRaw.attention, 0),
      ready: numberValue(summaryRaw.ready, 0),
      review_items_total: numberValue(summaryRaw.review_items_total, 0),
      failed_evaluation_case_runs: numberValue(summaryRaw.failed_evaluation_case_runs, 0),
      waiting_deliveries: numberValue(summaryRaw.waiting_deliveries, 0),
      needs_attention_deliveries: numberValue(summaryRaw.needs_attention_deliveries, 0),
      stuck_worker_tasks: numberValue(summaryRaw.stuck_worker_tasks, 0),
      stuck_workflow_jobs: numberValue(summaryRaw.stuck_workflow_jobs, 0),
      recommended_adapter: String(summaryRaw.recommended_adapter || "mock"),
      remediation_packages: numberValue(summaryRaw.remediation_packages, 0),
      remediation_ready_for_review: numberValue(summaryRaw.remediation_ready_for_review, 0),
      remediation_pending_reviews: numberValue(summaryRaw.remediation_pending_reviews, 0),
      remediation_promoted_memories: numberValue(summaryRaw.remediation_promoted_memories, 0),
      remediation_promoted_deliveries: numberValue(summaryRaw.remediation_promoted_deliveries, 0),
      evidence_gap_runs: numberValue(summaryRaw.evidence_gap_runs, 0),
      missing_plan_runs: numberValue(summaryRaw.missing_plan_runs, 0),
      missing_plan_evidence_manifests: numberValue(summaryRaw.missing_plan_evidence_manifests, 0),
      unverified_plan_evidence_manifests: numberValue(summaryRaw.unverified_plan_evidence_manifests, 0),
      remediated_evidence_gap_runs: numberValue(summaryRaw.remediated_evidence_gap_runs, 0),
      blocked_evidence_gap_runs: numberValue(summaryRaw.blocked_evidence_gap_runs, 0),
      evidence_synthesis_ready_runs: numberValue(summaryRaw.evidence_synthesis_ready_runs, 0),
      evidence_synthesis_pending_runs: numberValue(summaryRaw.evidence_synthesis_pending_runs, 0),
      evidence_synthesis_promoted_runs: numberValue(summaryRaw.evidence_synthesis_promoted_runs, 0),
      evidence_gap_closure_ready_runs: numberValue(summaryRaw.evidence_gap_closure_ready_runs, 0),
      closed_evidence_gap_runs: numberValue(summaryRaw.closed_evidence_gap_runs, 0),
      waived_evidence_gap_runs: numberValue(summaryRaw.waived_evidence_gap_runs, 0),
      task_intake_checked: numberValue(summaryRaw.task_intake_checked, 0),
      task_intake_ready: numberValue(summaryRaw.task_intake_ready, 0),
      task_intake_blocked: numberValue(summaryRaw.task_intake_blocked, 0),
      task_intake_attention: numberValue(summaryRaw.task_intake_attention, 0),
      task_intake_missing_agent_plan: numberValue(summaryRaw.task_intake_missing_agent_plan, 0),
      dispatch_evidence_proofs: numberValue(summaryRaw.dispatch_evidence_proofs, 0),
      dispatch_evidence_ready: numberValue(summaryRaw.dispatch_evidence_ready, 0),
      dispatch_evidence_waiting_approval: numberValue(summaryRaw.dispatch_evidence_waiting_approval, 0),
      dispatch_evidence_verified_manifests: numberValue(summaryRaw.dispatch_evidence_verified_manifests, 0),
      operator_health_risks: numberValue(summaryRaw.operator_health_risks, 0),
      operator_health_blocked: numberValue(summaryRaw.operator_health_blocked, 0),
      operator_health_attention: numberValue(summaryRaw.operator_health_attention, 0),
      action_receipts: numberValue(summaryRaw.action_receipts, 0),
      action_receipts_recorded: numberValue(summaryRaw.action_receipts_recorded, 0),
      action_receipts_verified: numberValue(summaryRaw.action_receipts_verified, 0),
      action_receipts_failed: numberValue(summaryRaw.action_receipts_failed, 0),
      action_receipts_evaluated: numberValue(summaryRaw.action_receipts_evaluated, 0),
      action_receipts_evaluation_pass: numberValue(summaryRaw.action_receipts_evaluation_pass, 0),
      action_receipts_evaluation_fail: numberValue(summaryRaw.action_receipts_evaluation_fail, 0),
      receipt_failure_memory_candidates: numberValue(summaryRaw.receipt_failure_memory_candidates, 0),
      receipt_failure_memory_failed_receipts: numberValue(summaryRaw.receipt_failure_memory_failed_receipts, 0),
      receipt_failure_memory_existing_candidates: numberValue(summaryRaw.receipt_failure_memory_existing_candidates, 0),
      receipt_required_actions: numberValue(summaryRaw.receipt_required_actions, 0),
      receipt_verified_actions: numberValue(summaryRaw.receipt_verified_actions, 0),
      receipt_missing_actions: numberValue(summaryRaw.receipt_missing_actions, 0),
      receipt_missing_verified_actions: numberValue(summaryRaw.receipt_missing_verified_actions, 0),
      receipt_stale_actions: numberValue(summaryRaw.receipt_stale_actions, 0),
      receipt_evaluation_required_actions: numberValue(summaryRaw.receipt_evaluation_required_actions, 0),
      receipt_evaluated_actions: numberValue(summaryRaw.receipt_evaluated_actions, 0),
      receipt_evaluation_pass_actions: numberValue(summaryRaw.receipt_evaluation_pass_actions, 0),
      receipt_evaluation_fail_actions: numberValue(summaryRaw.receipt_evaluation_fail_actions, 0),
      receipt_evaluation_missing_actions: numberValue(summaryRaw.receipt_evaluation_missing_actions, 0),
      receipt_evaluation_coverage_percent: numberValue(summaryRaw.receipt_evaluation_coverage_percent, 0),
      receipt_coverage_percent: numberValue(summaryRaw.receipt_coverage_percent, 0),
      receipt_lookup_window: numberValue(summaryRaw.receipt_lookup_window, 0),
    },
    actions: asArray<Record<string, unknown>>(raw.actions).map((item) => ({
      action_id: String(item.action_id || item.command || item.title || ""),
      action_signature: item.action_signature ? String(item.action_signature) : null,
      lane: String(item.lane || "operator"),
      severity: String(item.severity || "attention"),
      priority: numberValue(item.priority, 0),
      base_priority: numberValue(item.base_priority, numberValue(item.priority, 0)),
      receipt_priority_boost: numberValue(item.receipt_priority_boost, 0),
      title: String(item.title || item.command || "Operator action"),
      summary: item.summary ? String(item.summary) : undefined,
      command: String(item.command || ""),
      verify_command: item.verify_command ? String(item.verify_command) : null,
      receipt_record_command: item.receipt_record_command ? String(item.receipt_record_command) : null,
      receipt_record_confirm_command: item.receipt_record_confirm_command ? String(item.receipt_record_confirm_command) : null,
      receipt_verify_record_command: item.receipt_verify_record_command ? String(item.receipt_verify_record_command) : null,
      ui_route: item.ui_route ? String(item.ui_route) : null,
      source: String(item.source || "operator"),
      evidence: typeof item.evidence === "object" && item.evidence !== null ? item.evidence as Record<string, unknown> : undefined,
      receipt_required: boolValue(item.receipt_required),
      receipt_status: String(item.receipt_status || "missing"),
      receipt_underlying_status: item.receipt_underlying_status ? String(item.receipt_underlying_status) : undefined,
      receipt_match: item.receipt_match ? String(item.receipt_match) : undefined,
      receipt_current: item.receipt_current === undefined ? undefined : boolValue(item.receipt_current),
      receipt_verified: boolValue(item.receipt_verified),
      receipt_id: item.receipt_id ? String(item.receipt_id) : null,
      receipt_hash: item.receipt_hash ? String(item.receipt_hash) : null,
      receipt_evaluation: typeof item.receipt_evaluation === "object" && item.receipt_evaluation !== null ? item.receipt_evaluation as Record<string, unknown> : null,
      receipt_state: typeof item.receipt_state === "object" && item.receipt_state !== null ? item.receipt_state as Record<string, unknown> : undefined,
    })).filter((item) => item.command),
    top_commands: asArray<unknown>(raw.top_commands).map(String).filter(Boolean),
    source_status: typeof raw.source_status === "object" && raw.source_status !== null ? raw.source_status as Record<string, string | undefined> : {},
    receipt_coverage: typeof raw.receipt_coverage === "object" && raw.receipt_coverage !== null ? {
      required: numberValue((raw.receipt_coverage as Record<string, unknown>).required, 0),
      verified: numberValue((raw.receipt_coverage as Record<string, unknown>).verified, 0),
      stale: numberValue((raw.receipt_coverage as Record<string, unknown>).stale, 0),
      missing: numberValue((raw.receipt_coverage as Record<string, unknown>).missing, 0),
      missing_verified: numberValue((raw.receipt_coverage as Record<string, unknown>).missing_verified, 0),
      coverage_percent: numberValue((raw.receipt_coverage as Record<string, unknown>).coverage_percent, 0),
      status: String((raw.receipt_coverage as Record<string, unknown>).status || "unknown"),
      evaluation_required: numberValue((raw.receipt_coverage as Record<string, unknown>).evaluation_required, 0),
      evaluated: numberValue((raw.receipt_coverage as Record<string, unknown>).evaluated, 0),
      evaluation_pass: numberValue((raw.receipt_coverage as Record<string, unknown>).evaluation_pass, 0),
      evaluation_fail: numberValue((raw.receipt_coverage as Record<string, unknown>).evaluation_fail, 0),
      evaluation_missing: numberValue((raw.receipt_coverage as Record<string, unknown>).evaluation_missing, 0),
      evaluation_coverage_percent: numberValue((raw.receipt_coverage as Record<string, unknown>).evaluation_coverage_percent, 0),
      evaluation_status: String((raw.receipt_coverage as Record<string, unknown>).evaluation_status || "ready"),
      lookup_window: numberValue((raw.receipt_coverage as Record<string, unknown>).lookup_window, 0),
      display_receipts: numberValue((raw.receipt_coverage as Record<string, unknown>).display_receipts, 0),
      token_omitted: boolValue((raw.receipt_coverage as Record<string, unknown>).token_omitted),
    } : undefined,
    execution_evidence: normalizeExecutionEvidenceGaps(raw.execution_evidence),
    task_intake: normalizeTaskIntakeChecklist(raw.task_intake),
    dispatch_evidence: typeof raw.dispatch_evidence === "object" && raw.dispatch_evidence !== null ? raw.dispatch_evidence as Record<string, unknown> : undefined,
    operator_health: typeof raw.operator_health === "object" && raw.operator_health !== null ? raw.operator_health as Record<string, unknown> : undefined,
    action_receipts: typeof raw.action_receipts === "object" && raw.action_receipts !== null ? {
      ...(raw.action_receipts as OperatorActionReceiptsPayload),
      receipts: asArray<Record<string, unknown>>((raw.action_receipts as Record<string, unknown>).receipts).map(normalizeOperatorActionReceipt).filter(item => item.receipt_id),
    } : undefined,
    receipt_failure_memory: typeof raw.receipt_failure_memory === "object" && raw.receipt_failure_memory !== null ? raw.receipt_failure_memory as Record<string, unknown> : undefined,
    safety: {
      read_only: boolValue(safetyRaw.read_only),
      ledger_mutated: boolValue(safetyRaw.ledger_mutated),
      live_execution_performed: boolValue(safetyRaw.live_execution_performed),
      raw_prompt_omitted: boolValue(safetyRaw.raw_prompt_omitted),
      raw_response_omitted: boolValue(safetyRaw.raw_response_omitted),
      token_omitted: boolValue(safetyRaw.token_omitted),
    },
    token_omitted: boolValue(raw.token_omitted),
    live_execution_performed: boolValue(raw.live_execution_performed),
  };
}

function normalizeOperatorLoopActionPackage(rawValue: unknown, loopId = ""): OperatorLoopActionPackagePayload {
  const actionPackageRaw = typeof rawValue === "object" && rawValue !== null ? rawValue as Record<string, unknown> : {};
  const actionPackageSummaryRaw = typeof actionPackageRaw.summary === "object" && actionPackageRaw.summary !== null ? actionPackageRaw.summary as Record<string, unknown> : {};
  const actionPackageSafetyRaw = typeof actionPackageRaw.safety === "object" && actionPackageRaw.safety !== null ? actionPackageRaw.safety as Record<string, unknown> : {};
  return {
    operation: String(actionPackageRaw.operation || "loop_action_package"),
    status: String(actionPackageRaw.status || "empty"),
    loop_id: actionPackageRaw.loop_id ? String(actionPackageRaw.loop_id) : loopId || null,
    method: actionPackageRaw.method ? String(actionPackageRaw.method) : undefined,
    verify_command: actionPackageRaw.verify_command ? String(actionPackageRaw.verify_command) : undefined,
    items: asArray<Record<string, unknown>>(actionPackageRaw.items).map((item) => ({
      package_id: String(item.package_id || ""),
      loop_id: item.loop_id ? String(item.loop_id) : null,
      gate_id: String(item.gate_id || ""),
      gate_label: String(item.gate_label || item.gate_id || ""),
      gate_status: String(item.gate_status || "attention"),
      source: item.source ? String(item.source) : undefined,
      action_id: item.action_id ? String(item.action_id) : undefined,
      action_signature: item.action_signature ? String(item.action_signature) : undefined,
      action_command: String(item.action_command || ""),
      verify_command: String(item.verify_command || ""),
      receipt_record_command: String(item.receipt_record_command || ""),
      receipt_verify_record_command: String(item.receipt_verify_record_command || ""),
      message: item.message ? String(item.message) : undefined,
      evidence: typeof item.evidence === "object" && item.evidence !== null ? item.evidence as Record<string, unknown> : undefined,
      token_omitted: item.token_omitted === undefined ? undefined : boolValue(item.token_omitted),
    })).filter((item) => item.package_id || item.action_command),
    summary: {
      items: numberValue(actionPackageSummaryRaw.items, 0),
      blocked: numberValue(actionPackageSummaryRaw.blocked, 0),
      attention: numberValue(actionPackageSummaryRaw.attention, 0),
      loop_scoped: boolValue(actionPackageSummaryRaw.loop_scoped),
    },
    contract: actionPackageRaw.contract ? String(actionPackageRaw.contract) : undefined,
    safety: {
      read_only: actionPackageSafetyRaw.read_only === undefined ? undefined : boolValue(actionPackageSafetyRaw.read_only),
      ledger_mutated: actionPackageSafetyRaw.ledger_mutated === undefined ? undefined : boolValue(actionPackageSafetyRaw.ledger_mutated),
      live_execution_performed: actionPackageSafetyRaw.live_execution_performed === undefined ? undefined : boolValue(actionPackageSafetyRaw.live_execution_performed),
      token_omitted: actionPackageSafetyRaw.token_omitted === undefined ? undefined : boolValue(actionPackageSafetyRaw.token_omitted),
    },
    token_omitted: actionPackageRaw.token_omitted === undefined ? undefined : boolValue(actionPackageRaw.token_omitted),
  };
}

export async function loadOperatorLoopAudit(limit = 12, loopId = ""): Promise<OperatorLoopAuditPayload> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (loopId) params.set("loop_id", loopId);
  const raw = await optionalApiJson<Record<string, unknown>>(`/operator/loop-audit?${params.toString()}`, {
    provider: "agentops-operator",
    operation: "loop_audit",
    status: "unavailable",
    workspace_id: "local-demo",
    loop_id: loopId || null,
    method: "READ -> PLAN -> RETRIEVE -> COMPARE -> EXECUTE -> VERIFY -> RECORD",
    summary: {},
    steps: [],
    next_actions: [],
    source_status: {},
    safety: {
      read_only: true,
      ledger_mutated: false,
      live_execution_performed: false,
      raw_prompt_omitted: true,
      raw_response_omitted: true,
      token_omitted: true,
    },
    token_omitted: true,
    live_execution_performed: false,
  });
  const summaryRaw = typeof raw.summary === "object" && raw.summary !== null ? raw.summary as Record<string, unknown> : {};
  const safetyRaw = typeof raw.safety === "object" && raw.safety !== null ? raw.safety as Record<string, unknown> : {};
  const loopRecordRaw = typeof raw.loop_record === "object" && raw.loop_record !== null ? raw.loop_record as Record<string, unknown> : {};
  return {
    provider: String(raw.provider || "agentops-operator"),
    operation: String(raw.operation || "loop_audit"),
    status: String(raw.status || "unknown"),
    workspace_id: String(raw.workspace_id || "local-demo"),
    loop_id: raw.loop_id ? String(raw.loop_id) : null,
    method: String(raw.method || "READ -> PLAN -> RETRIEVE -> COMPARE -> EXECUTE -> VERIFY -> RECORD"),
    summary: {
      steps: numberValue(summaryRaw.steps, 0),
      pass: numberValue(summaryRaw.pass, 0),
      attention: numberValue(summaryRaw.attention, 0),
      blocked: numberValue(summaryRaw.blocked, 0),
      knowledge_documents: numberValue(summaryRaw.knowledge_documents, 0),
      verified_agent_plans: numberValue(summaryRaw.verified_agent_plans, 0),
      plan_bound_runs: numberValue(summaryRaw.plan_bound_runs, 0),
      verified_plan_evidence_manifests: numberValue(summaryRaw.verified_plan_evidence_manifests, 0),
      evidence_gap_runs: numberValue(summaryRaw.evidence_gap_runs, 0),
      loop_runs: numberValue(summaryRaw.loop_runs, 0),
      loop_verified_plan_evidence_manifests: numberValue(summaryRaw.loop_verified_plan_evidence_manifests, 0),
      loop_blocked_plan_evidence_manifests: numberValue(summaryRaw.loop_blocked_plan_evidence_manifests, 0),
      pending_approvals: numberValue(summaryRaw.pending_approvals, 0),
      memory_candidates: numberValue(summaryRaw.memory_candidates, 0),
      loop_memory_candidates: numberValue(summaryRaw.loop_memory_candidates, 0),
      loop_approved_memories: numberValue(summaryRaw.loop_approved_memories, 0),
      loop_pending_approvals: numberValue(summaryRaw.loop_pending_approvals, 0),
      audit_logs: numberValue(summaryRaw.audit_logs, 0),
    },
    steps: asArray<Record<string, unknown>>(raw.steps).map((step) => ({
      id: String(step.id || ""),
      label: String(step.label || step.id || ""),
      status: String(step.status || "attention"),
      message: step.message ? String(step.message) : undefined,
      evidence: typeof step.evidence === "object" && step.evidence !== null ? step.evidence as Record<string, unknown> : {},
      command: String(step.command || ""),
      source: String(step.source || ""),
      token_omitted: step.token_omitted === undefined ? undefined : boolValue(step.token_omitted),
    })).filter((step) => step.id),
    action_package: normalizeOperatorLoopActionPackage(raw.action_package, loopId),
    next_actions: asArray<unknown>(raw.next_actions).map(String).filter(Boolean),
    source_status: typeof raw.source_status === "object" && raw.source_status !== null ? raw.source_status as Record<string, string | undefined> : {},
    sources: typeof raw.sources === "object" && raw.sources !== null ? raw.sources as Record<string, unknown> : undefined,
    loop_record: {
      status: String(loopRecordRaw.status || (loopId ? "unknown" : "unscoped")),
      loop_id: loopRecordRaw.loop_id ? String(loopRecordRaw.loop_id) : null,
      memory_reviews: asArray<Record<string, unknown>>(loopRecordRaw.memory_reviews).map((item) => ({
        memory_id: String(item.memory_id || ""),
        scope: item.scope ? String(item.scope) : undefined,
        memory_type: item.memory_type ? String(item.memory_type) : undefined,
        review_status: String(item.review_status || "candidate"),
        source_type: item.source_type ? String(item.source_type) : undefined,
        source_ref: item.source_ref ? String(item.source_ref) : null,
        task_id: item.task_id ? String(item.task_id) : null,
        agent_id: item.agent_id ? String(item.agent_id) : null,
        confidence: item.confidence === undefined || item.confidence === null ? undefined : numberValue(item.confidence, 0),
        summary: item.summary ? String(item.summary) : undefined,
        created_at: item.created_at ? String(item.created_at) : null,
        updated_at: item.updated_at ? String(item.updated_at) : null,
        approve_command: item.approve_command ? String(item.approve_command) : undefined,
        reject_command: item.reject_command ? String(item.reject_command) : undefined,
        token_omitted: item.token_omitted === undefined ? undefined : boolValue(item.token_omitted),
      })).filter((item) => item.memory_id),
      approval_reviews: asArray<Record<string, unknown>>(loopRecordRaw.approval_reviews).map((item) => ({
        approval_id: String(item.approval_id || ""),
        task_id: item.task_id ? String(item.task_id) : null,
        run_id: item.run_id ? String(item.run_id) : null,
        tool_call_id: item.tool_call_id ? String(item.tool_call_id) : null,
        requested_by_agent_id: item.requested_by_agent_id ? String(item.requested_by_agent_id) : null,
        decision: String(item.decision || "pending"),
        reason: item.reason ? String(item.reason) : undefined,
        created_at: item.created_at ? String(item.created_at) : null,
        decided_at: item.decided_at ? String(item.decided_at) : null,
        approve_command: item.approve_command ? String(item.approve_command) : undefined,
        reject_command: item.reject_command ? String(item.reject_command) : undefined,
        token_omitted: item.token_omitted === undefined ? undefined : boolValue(item.token_omitted),
      })).filter((item) => item.approval_id),
      candidate_count: numberValue(loopRecordRaw.candidate_count, 0),
      approved_count: numberValue(loopRecordRaw.approved_count, 0),
      pending_approval_count: numberValue(loopRecordRaw.pending_approval_count, 0),
      audit_count: numberValue(loopRecordRaw.audit_count, 0),
      audit_trail: asArray<Record<string, unknown>>(loopRecordRaw.audit_trail).map((item) => ({
        audit_id: String(item.audit_id || ""),
        actor_type: item.actor_type ? String(item.actor_type) : null,
        actor_id: item.actor_id ? String(item.actor_id) : null,
        action: String(item.action || ""),
        entity_type: String(item.entity_type || ""),
        entity_id: String(item.entity_id || ""),
        before_hash: item.before_hash ? String(item.before_hash) : null,
        after_hash: item.after_hash ? String(item.after_hash) : null,
        tamper_chain_hash: item.tamper_chain_hash ? String(item.tamper_chain_hash) : null,
        created_at: item.created_at ? String(item.created_at) : null,
        token_omitted: item.token_omitted === undefined ? undefined : boolValue(item.token_omitted),
      })).filter((item) => item.audit_id),
      next_action: loopRecordRaw.next_action ? String(loopRecordRaw.next_action) : undefined,
      review_queue_command: loopRecordRaw.review_queue_command ? String(loopRecordRaw.review_queue_command) : undefined,
      token_omitted: loopRecordRaw.token_omitted === undefined ? undefined : boolValue(loopRecordRaw.token_omitted),
    },
    loop_readback: typeof raw.loop_readback === "object" && raw.loop_readback !== null ? raw.loop_readback as Record<string, unknown> : undefined,
    safety: {
      read_only: boolValue(safetyRaw.read_only),
      ledger_mutated: boolValue(safetyRaw.ledger_mutated),
      live_execution_performed: boolValue(safetyRaw.live_execution_performed),
      raw_prompt_omitted: boolValue(safetyRaw.raw_prompt_omitted),
      raw_response_omitted: boolValue(safetyRaw.raw_response_omitted),
      token_omitted: boolValue(safetyRaw.token_omitted),
    },
    token_omitted: boolValue(raw.token_omitted),
    live_execution_performed: boolValue(raw.live_execution_performed),
  };
}

export async function loadOperatorHandoff(limit = 12, loopId = ""): Promise<OperatorHandoffPayload> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (loopId) params.set("loop_id", loopId);
  const raw = await optionalApiJson<Record<string, unknown>>(`/operator/handoff?${params.toString()}`, {
    provider: "agentops-operator",
    operation: "operator_handoff",
    status: "unavailable",
    workspace_id: "local-demo",
    loop_id: loopId || null,
    summary: {},
    work_order: {
      method: "READ -> PLAN -> RETRIEVE -> COMPARE -> EXECUTE -> VERIFY -> RECORD",
      action_package: {},
      evidence_report: {},
      next_actions: [],
      top_operator_actions: [],
      commands: [],
      token_omitted: true,
    },
    receipt_state: {
      coverage: {},
      recent: [],
      summary: {},
      token_omitted: true,
    },
    review_state: {
      loop_record: {},
      token_omitted: true,
    },
    loop_health: {
      operation: "operator_loop_health",
      status: "unknown",
      score: 0,
      score_parts: {},
      gates: {},
      risks: [],
      token_omitted: true,
    },
    sources: {},
    auth: {
      mode: "local_dev_no_token",
      scoped: false,
      required_scope: "tasks:read",
      workspace_id: "local-demo",
      token_omitted: true,
    },
    safety: {
      read_only: true,
      ledger_mutated: false,
      live_execution_performed: false,
      raw_prompt_omitted: true,
      raw_response_omitted: true,
      token_omitted: true,
    },
    token_omitted: true,
    live_execution_performed: false,
  });
  const summaryRaw = typeof raw.summary === "object" && raw.summary !== null ? raw.summary as Record<string, unknown> : {};
  const workOrderRaw = typeof raw.work_order === "object" && raw.work_order !== null ? raw.work_order as Record<string, unknown> : {};
  const receiptStateRaw = typeof raw.receipt_state === "object" && raw.receipt_state !== null ? raw.receipt_state as Record<string, unknown> : {};
  const receiptCoverageRaw = typeof receiptStateRaw.coverage === "object" && receiptStateRaw.coverage !== null ? receiptStateRaw.coverage as Record<string, unknown> : {};
  const receiptSummaryRaw = typeof receiptStateRaw.summary === "object" && receiptStateRaw.summary !== null ? receiptStateRaw.summary as Record<string, unknown> : {};
  const reviewStateRaw = typeof raw.review_state === "object" && raw.review_state !== null ? raw.review_state as Record<string, unknown> : {};
  const loopRecordRaw = typeof reviewStateRaw.loop_record === "object" && reviewStateRaw.loop_record !== null ? reviewStateRaw.loop_record as Record<string, unknown> : {};
  const loopHealthRaw = typeof raw.loop_health === "object" && raw.loop_health !== null ? raw.loop_health as Record<string, unknown> : {};
  const loopHealthScorePartsRaw = typeof loopHealthRaw.score_parts === "object" && loopHealthRaw.score_parts !== null ? loopHealthRaw.score_parts as Record<string, unknown> : {};
  const controlRaw = typeof raw.control_summary === "object" && raw.control_summary !== null ? raw.control_summary as Record<string, unknown> : {};
  const authRaw = typeof raw.auth === "object" && raw.auth !== null ? raw.auth as Record<string, unknown> : {};
  const safetyRaw = typeof raw.safety === "object" && raw.safety !== null ? raw.safety as Record<string, unknown> : {};
  return {
    provider: String(raw.provider || "agentops-operator"),
    operation: String(raw.operation || "operator_handoff"),
    status: String(raw.status || "unknown"),
    workspace_id: String(raw.workspace_id || "local-demo"),
    loop_id: raw.loop_id ? String(raw.loop_id) : null,
    summary: {
      loop_status: summaryRaw.loop_status ? String(summaryRaw.loop_status) : undefined,
      action_plan_status: summaryRaw.action_plan_status ? String(summaryRaw.action_plan_status) : undefined,
      evidence_report_status: summaryRaw.evidence_report_status ? String(summaryRaw.evidence_report_status) : undefined,
      evidence_report_runs: numberValue(summaryRaw.evidence_report_runs, 0),
      evidence_report_ready: numberValue(summaryRaw.evidence_report_ready, 0),
      evidence_report_attention: numberValue(summaryRaw.evidence_report_attention, 0),
      evidence_report_blocked: numberValue(summaryRaw.evidence_report_blocked, 0),
      evidence_report_missing_plan_evidence_manifests: numberValue(summaryRaw.evidence_report_missing_plan_evidence_manifests, 0),
      evidence_report_pending_approvals: numberValue(summaryRaw.evidence_report_pending_approvals, 0),
      loop_package_items: numberValue(summaryRaw.loop_package_items, 0),
      operator_actions: numberValue(summaryRaw.operator_actions, 0),
      receipt_required: numberValue(summaryRaw.receipt_required, 0),
      receipt_verified: numberValue(summaryRaw.receipt_verified, 0),
      receipt_missing: numberValue(summaryRaw.receipt_missing, 0),
      receipt_stale: numberValue(summaryRaw.receipt_stale, 0),
      receipt_evaluation_required: numberValue(summaryRaw.receipt_evaluation_required, 0),
      receipt_evaluated: numberValue(summaryRaw.receipt_evaluated, 0),
      receipt_evaluation_fail: numberValue(summaryRaw.receipt_evaluation_fail, 0),
      receipt_evaluation_missing: numberValue(summaryRaw.receipt_evaluation_missing, 0),
      receipt_failure_memory_candidates: numberValue(summaryRaw.receipt_failure_memory_candidates, 0),
      receipt_failure_memory_failed_receipts: numberValue(summaryRaw.receipt_failure_memory_failed_receipts, 0),
      receipt_failure_memory_existing_candidates: numberValue(summaryRaw.receipt_failure_memory_existing_candidates, 0),
      advance_loop_work_items: numberValue(summaryRaw.advance_loop_work_items, 0),
      loop_record_status: summaryRaw.loop_record_status ? String(summaryRaw.loop_record_status) : undefined,
      loop_record_candidates: numberValue(summaryRaw.loop_record_candidates, 0),
      loop_record_approved: numberValue(summaryRaw.loop_record_approved, 0),
      loop_record_pending_approvals: numberValue(summaryRaw.loop_record_pending_approvals, 0),
    },
    work_order: {
      method: workOrderRaw.method ? String(workOrderRaw.method) : undefined,
      action_package: normalizeOperatorLoopActionPackage(workOrderRaw.action_package, loopId),
      evidence_report: typeof workOrderRaw.evidence_report === "object" && workOrderRaw.evidence_report !== null ? workOrderRaw.evidence_report as OperatorHandoffPayload["work_order"]["evidence_report"] : undefined,
      next_actions: asArray<unknown>(workOrderRaw.next_actions).map(String).filter(Boolean),
      advance_loop: typeof workOrderRaw.advance_loop === "object" && workOrderRaw.advance_loop !== null ? workOrderRaw.advance_loop as Record<string, unknown> : undefined,
      top_operator_actions: asArray<Record<string, unknown>>(workOrderRaw.top_operator_actions).map((item) => ({
        action_id: String(item.action_id || item.command || item.title || ""),
        action_signature: item.action_signature ? String(item.action_signature) : null,
        lane: String(item.lane || "operator"),
        severity: String(item.severity || "attention"),
        priority: numberValue(item.priority, 0),
        base_priority: numberValue(item.base_priority, numberValue(item.priority, 0)),
        receipt_priority_boost: numberValue(item.receipt_priority_boost, 0),
        title: String(item.title || item.command || "Operator action"),
        summary: item.summary ? String(item.summary) : undefined,
        command: String(item.command || ""),
        verify_command: item.verify_command ? String(item.verify_command) : null,
        receipt_record_command: item.receipt_record_command ? String(item.receipt_record_command) : null,
        receipt_record_confirm_command: item.receipt_record_confirm_command ? String(item.receipt_record_confirm_command) : null,
        receipt_verify_record_command: item.receipt_verify_record_command ? String(item.receipt_verify_record_command) : null,
        ui_route: item.ui_route ? String(item.ui_route) : null,
        source: String(item.source || "operator"),
        evidence: typeof item.evidence === "object" && item.evidence !== null ? item.evidence as Record<string, unknown> : undefined,
        receipt_required: boolValue(item.receipt_required),
        receipt_status: String(item.receipt_status || "missing"),
        receipt_underlying_status: item.receipt_underlying_status ? String(item.receipt_underlying_status) : undefined,
        receipt_match: item.receipt_match ? String(item.receipt_match) : undefined,
        receipt_current: item.receipt_current === undefined ? undefined : boolValue(item.receipt_current),
        receipt_verified: boolValue(item.receipt_verified),
        receipt_id: item.receipt_id ? String(item.receipt_id) : null,
        receipt_hash: item.receipt_hash ? String(item.receipt_hash) : null,
        receipt_evaluation: typeof item.receipt_evaluation === "object" && item.receipt_evaluation !== null ? item.receipt_evaluation as Record<string, unknown> : null,
        receipt_state: typeof item.receipt_state === "object" && item.receipt_state !== null ? item.receipt_state as Record<string, unknown> : undefined,
      })).filter((item) => item.command),
      commands: asArray<unknown>(workOrderRaw.commands).map(String).filter(Boolean),
      token_omitted: workOrderRaw.token_omitted === undefined ? undefined : boolValue(workOrderRaw.token_omitted),
    },
    control_summary: {
      operation: String(controlRaw.operation || "operator_loop_control_summary"),
      status: String(controlRaw.status || "unknown"),
      mode: controlRaw.mode ? String(controlRaw.mode) : undefined,
      loop_id: controlRaw.loop_id ? String(controlRaw.loop_id) : null,
      recommended_step: typeof controlRaw.recommended_step === "object" && controlRaw.recommended_step !== null ? controlRaw.recommended_step as Record<string, unknown> : {},
      next_command: controlRaw.next_command ? String(controlRaw.next_command) : null,
      verify_command: controlRaw.verify_command ? String(controlRaw.verify_command) : null,
      receipt_command: controlRaw.receipt_command ? String(controlRaw.receipt_command) : null,
      requires_human: controlRaw.requires_human === undefined ? undefined : boolValue(controlRaw.requires_human),
      requires_receipt: controlRaw.requires_receipt === undefined ? undefined : boolValue(controlRaw.requires_receipt),
      server_executes_shell: controlRaw.server_executes_shell === undefined ? undefined : boolValue(controlRaw.server_executes_shell),
      copy_only: controlRaw.copy_only === undefined ? undefined : boolValue(controlRaw.copy_only),
      step_counts: typeof controlRaw.step_counts === "object" && controlRaw.step_counts !== null ? controlRaw.step_counts as Record<string, number> : {},
      selected_gate: controlRaw.selected_gate ? String(controlRaw.selected_gate) : null,
      selected_status: controlRaw.selected_status ? String(controlRaw.selected_status) : null,
      policy_id: controlRaw.policy_id ? String(controlRaw.policy_id) : undefined,
      token_omitted: controlRaw.token_omitted === undefined ? undefined : boolValue(controlRaw.token_omitted),
    },
    receipt_state: {
      coverage: {
        required: numberValue(receiptCoverageRaw.required, 0),
        verified: numberValue(receiptCoverageRaw.verified, 0),
        stale: numberValue(receiptCoverageRaw.stale, 0),
        missing: numberValue(receiptCoverageRaw.missing, 0),
        missing_verified: numberValue(receiptCoverageRaw.missing_verified, 0),
        coverage_percent: numberValue(receiptCoverageRaw.coverage_percent, 0),
        status: String(receiptCoverageRaw.status || "unknown"),
        lookup_window: numberValue(receiptCoverageRaw.lookup_window, 0),
        display_receipts: numberValue(receiptCoverageRaw.display_receipts, 0),
        token_omitted: receiptCoverageRaw.token_omitted === undefined ? undefined : boolValue(receiptCoverageRaw.token_omitted),
      },
      recent: asArray<Record<string, unknown>>(receiptStateRaw.recent).map(normalizeOperatorActionReceipt).filter(item => item.receipt_id),
      summary: Object.fromEntries(Object.entries(receiptSummaryRaw).map(([key, value]) => [key, numberValue(value, 0)])),
      failure_memory: typeof receiptStateRaw.failure_memory === "object" && receiptStateRaw.failure_memory !== null ? receiptStateRaw.failure_memory as Record<string, unknown> : undefined,
      token_omitted: receiptStateRaw.token_omitted === undefined ? undefined : boolValue(receiptStateRaw.token_omitted),
    },
    review_state: {
      loop_record: {
        status: String(loopRecordRaw.status || (loopId ? "unknown" : "unscoped")),
        loop_id: loopRecordRaw.loop_id ? String(loopRecordRaw.loop_id) : null,
        memory_reviews: asArray<Record<string, unknown>>(loopRecordRaw.memory_reviews).map((item) => ({
          memory_id: String(item.memory_id || ""),
          review_status: String(item.review_status || "candidate"),
        })).filter((item) => item.memory_id),
        approval_reviews: asArray<Record<string, unknown>>(loopRecordRaw.approval_reviews).map((item) => ({
          approval_id: String(item.approval_id || ""),
          decision: String(item.decision || "pending"),
        })).filter((item) => item.approval_id),
        candidate_count: numberValue(loopRecordRaw.candidate_count, 0),
        approved_count: numberValue(loopRecordRaw.approved_count, 0),
        pending_approval_count: numberValue(loopRecordRaw.pending_approval_count, 0),
        audit_count: numberValue(loopRecordRaw.audit_count, 0),
        audit_trail: [],
        next_action: loopRecordRaw.next_action ? String(loopRecordRaw.next_action) : undefined,
        review_queue_command: loopRecordRaw.review_queue_command ? String(loopRecordRaw.review_queue_command) : undefined,
        token_omitted: loopRecordRaw.token_omitted === undefined ? undefined : boolValue(loopRecordRaw.token_omitted),
      },
      token_omitted: reviewStateRaw.token_omitted === undefined ? undefined : boolValue(reviewStateRaw.token_omitted),
    },
    loop_health: {
      operation: String(loopHealthRaw.operation || "operator_loop_health"),
      status: String(loopHealthRaw.status || "unknown"),
      score: numberValue(loopHealthRaw.score, 0),
      score_parts: Object.fromEntries(Object.entries(loopHealthScorePartsRaw).map(([key, value]) => [key, numberValue(value, 0)])),
      gates: typeof loopHealthRaw.gates === "object" && loopHealthRaw.gates !== null ? loopHealthRaw.gates as Record<string, Record<string, unknown>> : {},
      risks: asArray<Record<string, unknown>>(loopHealthRaw.risks).map((item) => ({
        id: String(item.id || ""),
        severity: String(item.severity || "attention"),
        count: numberValue(item.count, 0),
        next_action: item.next_action ? String(item.next_action) : undefined,
      })).filter((item) => item.id),
      next_action: loopHealthRaw.next_action ? String(loopHealthRaw.next_action) : undefined,
      contract: loopHealthRaw.contract ? String(loopHealthRaw.contract) : undefined,
      token_omitted: loopHealthRaw.token_omitted === undefined ? undefined : boolValue(loopHealthRaw.token_omitted),
    },
    sources: typeof raw.sources === "object" && raw.sources !== null ? raw.sources as Record<string, unknown> : undefined,
    contract: raw.contract ? String(raw.contract) : undefined,
    auth: {
      mode: String(authRaw.mode || "unknown"),
      scoped: boolValue(authRaw.scoped),
      required_scope: String(authRaw.required_scope || "tasks:read"),
      workspace_id: String(authRaw.workspace_id || "local-demo"),
      agent_id: authRaw.agent_id ? String(authRaw.agent_id) : null,
      token_omitted: authRaw.token_omitted === undefined ? undefined : boolValue(authRaw.token_omitted),
    },
    safety: {
      read_only: boolValue(safetyRaw.read_only),
      ledger_mutated: boolValue(safetyRaw.ledger_mutated),
      live_execution_performed: boolValue(safetyRaw.live_execution_performed),
      raw_prompt_omitted: boolValue(safetyRaw.raw_prompt_omitted),
      raw_response_omitted: boolValue(safetyRaw.raw_response_omitted),
      token_omitted: boolValue(safetyRaw.token_omitted),
    },
    token_omitted: boolValue(raw.token_omitted),
    live_execution_performed: boolValue(raw.live_execution_performed),
  };
}

export async function loadOperatorLoopControl(limit = 8, loopId = ""): Promise<OperatorLoopControlPayload> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (loopId) params.set("loop_id", loopId);
  const raw = await optionalApiJson<Record<string, unknown>>(`/operator/loop-control?${params.toString()}`, {
    provider: "agentops-operator",
    operation: "operator_loop_control",
    status: "unavailable",
    workspace_id: "local-demo",
    loop_id: loopId || null,
    summary: {},
    next_actions: [`agentops operator loop-control --limit ${limit}`],
    work_order: {
      advance_loop: {
        preview_command: `agentops operator advance-loop --fast-control --limit ${limit}`,
        status: "unavailable",
        token_omitted: true,
      },
      commands: [`agentops operator loop-control --limit ${limit}`],
      token_omitted: true,
    },
    control_summary: {
      operation: "operator_loop_control_summary",
      status: "unavailable",
      mode: "read_only_copy",
      recommended_step: {},
      next_command: `agentops operator loop-control --limit ${limit}`,
      verify_command: `agentops operator loop-control --limit ${limit}`,
      receipt_command: null,
      requires_human: false,
      requires_receipt: false,
      server_executes_shell: false,
      copy_only: true,
      step_counts: {},
      selected_gate: null,
      selected_status: null,
      policy_id: "advance_loop_local_bounded_v1",
      token_omitted: true,
    },
    sources: {},
    contract: "lightweight read-only loop control fallback",
    safety: {
      read_only: true,
      ledger_mutated: false,
      live_execution_performed: false,
      raw_prompt_omitted: true,
      raw_response_omitted: true,
      token_omitted: true,
    },
    token_omitted: true,
    live_execution_performed: false,
  });
  const summaryRaw = typeof raw.summary === "object" && raw.summary !== null ? raw.summary as Record<string, unknown> : {};
  const workOrderRaw = typeof raw.work_order === "object" && raw.work_order !== null ? raw.work_order as Record<string, unknown> : {};
  const controlRaw = typeof raw.control_summary === "object" && raw.control_summary !== null ? raw.control_summary as Record<string, unknown> : {};
  const safetyRaw = typeof raw.safety === "object" && raw.safety !== null ? raw.safety as Record<string, unknown> : {};
  return {
    provider: String(raw.provider || "agentops-operator"),
    operation: String(raw.operation || "operator_loop_control"),
    status: String(raw.status || "unknown"),
    workspace_id: String(raw.workspace_id || "local-demo"),
    loop_id: raw.loop_id ? String(raw.loop_id) : null,
    summary: summaryRaw as Record<string, number | string | boolean | null | undefined>,
    next_actions: asArray<unknown>(raw.next_actions).map(String).filter(Boolean),
    work_order: {
      advance_loop: typeof workOrderRaw.advance_loop === "object" && workOrderRaw.advance_loop !== null ? workOrderRaw.advance_loop as Record<string, unknown> : undefined,
      commands: asArray<unknown>(workOrderRaw.commands).map(String).filter(Boolean),
      token_omitted: workOrderRaw.token_omitted === undefined ? undefined : boolValue(workOrderRaw.token_omitted),
    },
    control_summary: {
      operation: String(controlRaw.operation || "operator_loop_control_summary"),
      status: String(controlRaw.status || "unknown"),
      mode: controlRaw.mode ? String(controlRaw.mode) : undefined,
      loop_id: controlRaw.loop_id ? String(controlRaw.loop_id) : null,
      recommended_step: typeof controlRaw.recommended_step === "object" && controlRaw.recommended_step !== null ? controlRaw.recommended_step as Record<string, unknown> : {},
      next_command: controlRaw.next_command ? String(controlRaw.next_command) : null,
      verify_command: controlRaw.verify_command ? String(controlRaw.verify_command) : null,
      receipt_command: controlRaw.receipt_command ? String(controlRaw.receipt_command) : null,
      requires_human: controlRaw.requires_human === undefined ? undefined : boolValue(controlRaw.requires_human),
      requires_receipt: controlRaw.requires_receipt === undefined ? undefined : boolValue(controlRaw.requires_receipt),
      server_executes_shell: controlRaw.server_executes_shell === undefined ? undefined : boolValue(controlRaw.server_executes_shell),
      copy_only: controlRaw.copy_only === undefined ? undefined : boolValue(controlRaw.copy_only),
      step_counts: typeof controlRaw.step_counts === "object" && controlRaw.step_counts !== null ? controlRaw.step_counts as Record<string, number> : {},
      selected_gate: controlRaw.selected_gate ? String(controlRaw.selected_gate) : null,
      selected_status: controlRaw.selected_status ? String(controlRaw.selected_status) : null,
      policy_id: controlRaw.policy_id ? String(controlRaw.policy_id) : undefined,
      token_omitted: controlRaw.token_omitted === undefined ? undefined : boolValue(controlRaw.token_omitted),
    },
    sources: typeof raw.sources === "object" && raw.sources !== null ? raw.sources as Record<string, unknown> : undefined,
    contract: raw.contract ? String(raw.contract) : undefined,
    safety: {
      read_only: boolValue(safetyRaw.read_only),
      ledger_mutated: boolValue(safetyRaw.ledger_mutated),
      live_execution_performed: boolValue(safetyRaw.live_execution_performed),
      raw_prompt_omitted: boolValue(safetyRaw.raw_prompt_omitted),
      raw_response_omitted: boolValue(safetyRaw.raw_response_omitted),
      token_omitted: boolValue(safetyRaw.token_omitted),
    },
    token_omitted: raw.token_omitted === undefined ? undefined : boolValue(raw.token_omitted),
    live_execution_performed: raw.live_execution_performed === undefined ? undefined : boolValue(raw.live_execution_performed),
  };
}

export async function loadOperatorLoopSelfCheck(limit = 12, loopId = ""): Promise<OperatorLoopSelfCheckPayload> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (loopId) params.set("loop_id", loopId);
  const raw = await optionalApiJson<Record<string, unknown>>(`/operator/loop-self-check?${params.toString()}`, {
    provider: "agentops-operator",
    operation: "operator_loop_self_check",
    status: "unavailable",
    workspace_id: "local-demo",
    loop_id: loopId || null,
    summary: {},
    gates: {},
    policy_decisions: [],
    next_actions: [],
    safety: {
      read_only: true,
      ledger_mutated: false,
      live_execution_performed: false,
      server_shell_execution: false,
      raw_prompt_omitted: true,
      raw_response_omitted: true,
      token_omitted: true,
    },
    token_omitted: true,
    live_execution_performed: false,
  });
  const safetyRaw = typeof raw.safety === "object" && raw.safety !== null ? raw.safety as Record<string, unknown> : {};
  return {
    provider: String(raw.provider || "agentops-operator"),
    operation: String(raw.operation || "operator_loop_self_check"),
    status: String(raw.status || "unknown"),
    workspace_id: String(raw.workspace_id || "local-demo"),
    loop_id: raw.loop_id ? String(raw.loop_id) : null,
    summary: typeof raw.summary === "object" && raw.summary !== null ? raw.summary as Record<string, number | string | boolean | null | undefined> : {},
    gates: typeof raw.gates === "object" && raw.gates !== null ? raw.gates as Record<string, Record<string, unknown>> : {},
    policy_decisions: asArray<Record<string, unknown>>(raw.policy_decisions),
    next_actions: asArray<unknown>(raw.next_actions).map(String).filter(Boolean),
    contract: raw.contract ? String(raw.contract) : undefined,
    safety: {
      read_only: boolValue(safetyRaw.read_only),
      ledger_mutated: boolValue(safetyRaw.ledger_mutated),
      live_execution_performed: boolValue(safetyRaw.live_execution_performed),
      server_shell_execution: boolValue(safetyRaw.server_shell_execution),
      raw_prompt_omitted: boolValue(safetyRaw.raw_prompt_omitted),
      raw_response_omitted: boolValue(safetyRaw.raw_response_omitted),
      token_omitted: boolValue(safetyRaw.token_omitted),
    },
    token_omitted: raw.token_omitted === undefined ? undefined : boolValue(raw.token_omitted),
    live_execution_performed: raw.live_execution_performed === undefined ? undefined : boolValue(raw.live_execution_performed),
  };
}

export async function loadOperatorLoopLaunchPacket(limit = 8, query = "Agent Work Method Block"): Promise<OperatorLoopLaunchPacketPayload> {
  const params = new URLSearchParams({ limit: String(limit), q: query });
  const raw = await optionalApiJson<Record<string, unknown>>(`/operator/loop-launch-packet?${params.toString()}`, {
    provider: "agentops-operator",
    operation: "operator_loop_launch_packet",
    status: "unavailable",
    workspace_id: "local-demo",
    task_id: null,
    agent_id: null,
    method: "READ -> PLAN -> RETRIEVE -> COMPARE -> EXECUTE -> VERIFY -> RECORD",
    summary: {},
    launch_sequence: [],
    execution_chain: [],
    control_summary: {
      operation: "loop_launch_control_summary",
      status: "unavailable",
      mode: "read_only_copy",
      recommended_step: {},
      next_command: null,
      verify_command: null,
      receipt_command: null,
      requires_human: false,
      requires_receipt: false,
      server_executes_shell: false,
      copy_only: true,
      step_counts: {},
      unverified_receipt_steps: 0,
      blocking_steps: [],
      attention_steps: [],
      verified_steps: [],
      token_omitted: true,
    },
    agent_plan_draft: {},
    evaluation_contract: {
      operation: "loop_evaluation_contract",
      status: "unknown",
      minimum_exit_criteria: [],
      required_commands: [],
      required_ledgers: [],
      receipt_evaluation: {},
      token_omitted: true,
    },
    audit_contract: {
      operation: "loop_audit_contract",
      method: "READ -> PLAN -> RETRIEVE -> COMPARE -> EXECUTE -> VERIFY -> RECORD",
      tamper_chain_required: true,
      record_required: true,
      record_commands: [],
      token_omitted: true,
    },
    commands: [],
    sources: {},
    safety: {
      read_only: true,
      ledger_mutated: false,
      live_execution_performed: false,
      raw_prompt_omitted: true,
      raw_response_omitted: true,
      token_omitted: true,
    },
    token_omitted: true,
    live_execution_performed: false,
  });
  const summaryRaw = typeof raw.summary === "object" && raw.summary !== null ? raw.summary as Record<string, unknown> : {};
  const safetyRaw = typeof raw.safety === "object" && raw.safety !== null ? raw.safety as Record<string, unknown> : {};
  const evaluationRaw = typeof raw.evaluation_contract === "object" && raw.evaluation_contract !== null ? raw.evaluation_contract as Record<string, unknown> : {};
  const auditRaw = typeof raw.audit_contract === "object" && raw.audit_contract !== null ? raw.audit_contract as Record<string, unknown> : {};
  const controlRaw = typeof raw.control_summary === "object" && raw.control_summary !== null ? raw.control_summary as Record<string, unknown> : {};
  return {
    provider: String(raw.provider || "agentops-operator"),
    operation: String(raw.operation || "operator_loop_launch_packet"),
    status: String(raw.status || "unknown"),
    workspace_id: String(raw.workspace_id || "local-demo"),
    task_id: raw.task_id ? String(raw.task_id) : null,
    agent_id: raw.agent_id ? String(raw.agent_id) : null,
    method: String(raw.method || "READ -> PLAN -> RETRIEVE -> COMPARE -> EXECUTE -> VERIFY -> RECORD"),
    summary: summaryRaw as Record<string, number | string | boolean | null | undefined>,
    launch_sequence: asArray<Record<string, unknown>>(raw.launch_sequence),
    execution_chain: asArray<Record<string, unknown>>(raw.execution_chain).map((item) => ({
      step_id: String(item.step_id || ""),
      label: String(item.label || item.step_id || ""),
      phase: String(item.phase || ""),
      command: String(item.command || ""),
      verify_command: item.verify_command ? String(item.verify_command) : null,
      receipt_command: item.receipt_command ? String(item.receipt_command) : null,
      mutating: boolValue(item.mutating),
      confirm_required: boolValue(item.confirm_required),
      receipt_required: boolValue(item.receipt_required),
      source: item.source ? String(item.source) : undefined,
      selected_gate: item.selected_gate ? String(item.selected_gate) : null,
      selected_status: item.selected_status ? String(item.selected_status) : null,
      action_signature: item.action_signature ? String(item.action_signature) : null,
      policy_id: item.policy_id ? String(item.policy_id) : null,
      next_on_pass: item.next_on_pass ? String(item.next_on_pass) : null,
      step_status: item.step_status ? String(item.step_status) : undefined,
      blocked_reason: item.blocked_reason ? String(item.blocked_reason) : undefined,
      ready_reason: item.ready_reason ? String(item.ready_reason) : undefined,
      next_safe_command: item.next_safe_command ? String(item.next_safe_command) : null,
      receipt_state: typeof item.receipt_state === "object" && item.receipt_state !== null ? item.receipt_state as Record<string, unknown> : undefined,
      token_omitted: item.token_omitted === undefined ? undefined : boolValue(item.token_omitted),
    })).filter((item) => item.step_id || item.command),
    control_summary: {
      operation: String(controlRaw.operation || "loop_launch_control_summary"),
      status: String(controlRaw.status || "unknown"),
      mode: controlRaw.mode ? String(controlRaw.mode) : undefined,
      recommended_step: typeof controlRaw.recommended_step === "object" && controlRaw.recommended_step !== null ? controlRaw.recommended_step as Record<string, unknown> : {},
      next_command: controlRaw.next_command ? String(controlRaw.next_command) : null,
      verify_command: controlRaw.verify_command ? String(controlRaw.verify_command) : null,
      receipt_command: controlRaw.receipt_command ? String(controlRaw.receipt_command) : null,
      requires_human: controlRaw.requires_human === undefined ? undefined : boolValue(controlRaw.requires_human),
      requires_receipt: controlRaw.requires_receipt === undefined ? undefined : boolValue(controlRaw.requires_receipt),
      server_executes_shell: controlRaw.server_executes_shell === undefined ? undefined : boolValue(controlRaw.server_executes_shell),
      copy_only: controlRaw.copy_only === undefined ? undefined : boolValue(controlRaw.copy_only),
      step_counts: typeof controlRaw.step_counts === "object" && controlRaw.step_counts !== null ? controlRaw.step_counts as Record<string, number> : {},
      unverified_receipt_steps: controlRaw.unverified_receipt_steps === undefined ? undefined : numberValue(controlRaw.unverified_receipt_steps, 0),
      blocking_steps: asArray<unknown>(controlRaw.blocking_steps).map(String).filter(Boolean),
      attention_steps: asArray<unknown>(controlRaw.attention_steps).map(String).filter(Boolean),
      verified_steps: asArray<unknown>(controlRaw.verified_steps).map(String).filter(Boolean),
      policy_id: controlRaw.policy_id ? String(controlRaw.policy_id) : undefined,
      token_omitted: controlRaw.token_omitted === undefined ? undefined : boolValue(controlRaw.token_omitted),
    },
    agent_plan_draft: typeof raw.agent_plan_draft === "object" && raw.agent_plan_draft !== null ? raw.agent_plan_draft as Record<string, unknown> : {},
    evaluation_contract: {
      operation: String(evaluationRaw.operation || "loop_evaluation_contract"),
      status: String(evaluationRaw.status || "unknown"),
      score: evaluationRaw.score === undefined || evaluationRaw.score === null ? null : numberValue(evaluationRaw.score, 0),
      minimum_exit_criteria: asArray<unknown>(evaluationRaw.minimum_exit_criteria).map(String).filter(Boolean),
      required_commands: asArray<unknown>(evaluationRaw.required_commands).map(String).filter(Boolean),
      required_ledgers: asArray<unknown>(evaluationRaw.required_ledgers).map(String).filter(Boolean),
      receipt_evaluation: typeof evaluationRaw.receipt_evaluation === "object" && evaluationRaw.receipt_evaluation !== null ? evaluationRaw.receipt_evaluation as Record<string, unknown> : {},
      token_omitted: evaluationRaw.token_omitted === undefined ? undefined : boolValue(evaluationRaw.token_omitted),
    },
    audit_contract: {
      operation: String(auditRaw.operation || "loop_audit_contract"),
      method: String(auditRaw.method || raw.method || "READ -> PLAN -> RETRIEVE -> COMPARE -> EXECUTE -> VERIFY -> RECORD"),
      tamper_chain_required: boolValue(auditRaw.tamper_chain_required),
      raw_content_policy: auditRaw.raw_content_policy ? String(auditRaw.raw_content_policy) : undefined,
      record_required: boolValue(auditRaw.record_required),
      record_commands: asArray<unknown>(auditRaw.record_commands).map(String).filter(Boolean),
      evidence_report: typeof auditRaw.evidence_report === "object" && auditRaw.evidence_report !== null ? auditRaw.evidence_report as Record<string, unknown> : undefined,
      bounded_runner: typeof auditRaw.bounded_runner === "object" && auditRaw.bounded_runner !== null ? auditRaw.bounded_runner as Record<string, unknown> : undefined,
      token_omitted: auditRaw.token_omitted === undefined ? undefined : boolValue(auditRaw.token_omitted),
    },
    commands: asArray<unknown>(raw.commands).map(String).filter(Boolean),
    sources: typeof raw.sources === "object" && raw.sources !== null ? raw.sources as Record<string, unknown> : undefined,
    contract: raw.contract ? String(raw.contract) : undefined,
    safety: {
      read_only: boolValue(safetyRaw.read_only),
      ledger_mutated: boolValue(safetyRaw.ledger_mutated),
      live_execution_performed: boolValue(safetyRaw.live_execution_performed),
      raw_prompt_omitted: boolValue(safetyRaw.raw_prompt_omitted),
      raw_response_omitted: boolValue(safetyRaw.raw_response_omitted),
      token_omitted: boolValue(safetyRaw.token_omitted),
    },
    token_omitted: raw.token_omitted === undefined ? undefined : boolValue(raw.token_omitted),
    live_execution_performed: raw.live_execution_performed === undefined ? undefined : boolValue(raw.live_execution_performed),
  };
}

function normalizeOperatorLoopDriverAgentPacket(rawValue: unknown, adapter: string, limit = 8): OperatorLoopDriverAgentPacketPayload {
  const raw = typeof rawValue === "object" && rawValue !== null ? rawValue as Record<string, unknown> : {};
  const safetyRaw = typeof raw.safety === "object" && raw.safety !== null ? raw.safety as Record<string, unknown> : {};
  const commandsRaw = typeof raw.commands === "object" && raw.commands !== null ? raw.commands as Record<string, unknown> : {};
  const phaseCommandsRaw = typeof raw.phase_commands === "object" && raw.phase_commands !== null ? raw.phase_commands as Record<string, unknown> : {};
  return {
    operation: String(raw.operation || "operator_loop_driver_agent_loop_packet"),
    adapter: String(raw.adapter || adapter),
    current_phase: String(raw.current_phase || "unknown"),
    ready_to_confirm_loop: boolValue(raw.ready_to_confirm_loop),
    max_steps: raw.max_steps === undefined ? undefined : numberValue(raw.max_steps, 0),
    steps_advanced: raw.steps_advanced === undefined ? undefined : numberValue(raw.steps_advanced, 0),
    stop_reason: raw.stop_reason === undefined || raw.stop_reason === null ? null : String(raw.stop_reason),
    phases: asArray<Record<string, unknown>>(raw.phases).map((phase) => ({
      phase: String(phase.phase || ""),
      status: String(phase.status || "unknown"),
      command: phase.command === undefined || phase.command === null ? null : String(phase.command),
      gate_id: phase.gate_id ? String(phase.gate_id) : undefined,
      description: phase.description ? String(phase.description) : undefined,
      confirm_required: phase.confirm_required === undefined ? undefined : boolValue(phase.confirm_required),
      token_omitted: phase.token_omitted === undefined ? undefined : boolValue(phase.token_omitted),
    })).filter((phase) => phase.phase),
    phase_commands: Object.fromEntries(Object.entries(phaseCommandsRaw).map(([key, value]) => [
      key,
      value === undefined || value === null ? null : String(value),
    ])) as Record<string, string | null | undefined>,
    method_gates: asArray<Record<string, unknown>>(raw.method_gates).map((gate) => ({
      id: String(gate.id || ""),
      phase: String(gate.phase || ""),
      required: boolValue(gate.required),
      status: String(gate.status || "unknown"),
      command: gate.command === undefined || gate.command === null ? null : String(gate.command),
      confirm_required: gate.confirm_required === undefined ? undefined : boolValue(gate.confirm_required),
      proof: gate.proof ? String(gate.proof) : undefined,
      token_omitted: gate.token_omitted === undefined ? undefined : boolValue(gate.token_omitted),
    })).filter((gate) => gate.id && gate.phase),
    commands: Object.fromEntries(Object.entries(commandsRaw).map(([key, value]) => [
      key,
      value === undefined || value === null ? null : String(value),
    ])) as Record<string, string | null | undefined>,
    gates: typeof raw.gates === "object" && raw.gates !== null ? raw.gates as Record<string, unknown> : {},
    contract: raw.contract ? String(raw.contract) : undefined,
    safety: {
      read_only: safetyRaw.read_only === undefined ? true : boolValue(safetyRaw.read_only),
      ledger_mutated: boolValue(safetyRaw.ledger_mutated),
      live_execution_performed: boolValue(safetyRaw.live_execution_performed),
      server_executes_shell: boolValue(safetyRaw.server_executes_shell),
      raw_prompt_omitted: safetyRaw.raw_prompt_omitted === undefined ? true : boolValue(safetyRaw.raw_prompt_omitted),
      raw_response_omitted: safetyRaw.raw_response_omitted === undefined ? true : boolValue(safetyRaw.raw_response_omitted),
      raw_content_omitted: safetyRaw.raw_content_omitted === undefined ? undefined : boolValue(safetyRaw.raw_content_omitted),
      token_omitted: safetyRaw.token_omitted === undefined ? true : boolValue(safetyRaw.token_omitted),
    },
    token_omitted: raw.token_omitted === undefined ? undefined : boolValue(raw.token_omitted),
    live_execution_performed: raw.live_execution_performed === undefined ? undefined : boolValue(raw.live_execution_performed),
  };
}

export async function loadOperatorStartCheck(adapter: OperatorStartCheckAdapter = "mock", limit = 8): Promise<OperatorStartCheckPayload> {
  const params = new URLSearchParams({ adapter, limit: String(limit) });
  const raw = await optionalApiJson<Record<string, unknown>>(`/operator/start-check?${params.toString()}`, {
    provider: "agentops-operator",
    operation: "operator_start_check",
    status: "unavailable",
    adapter,
    workspace_id: "local-demo",
    summary: {},
    loop_driver_entry: {},
    acceptance_packet: {},
    local_loop_admission_packet: {
      operation: "operator_local_loop_admission_packet",
      status: "unavailable",
      adapter,
      admission: {
        can_preview_loop: false,
        can_confirm_bounded_loop: false,
        live_dispatch_requires_confirm_run: adapter === "hermes" || adapter === "openclaw",
        method_gate_count: 0,
      },
      required_method_gates: [],
      phase_commands: {},
      commands: {
        read_start_check: `agentops operator start-check --adapter ${adapter} --limit ${limit}`,
      },
      first_safe_commands: [`agentops operator start-check --adapter ${adapter} --limit ${limit}`],
      confirm_required_commands: [],
      safety: {
        read_only: true,
        ledger_mutated: false,
        live_execution_performed: false,
        server_executes_shell: false,
        token_omitted: true,
      },
      token_omitted: true,
      live_execution_performed: false,
    },
    agent_loop_packet: {
      operation: "operator_loop_driver_agent_loop_packet",
      adapter,
      current_phase: "unavailable",
      ready_to_confirm_loop: false,
      max_steps: 3,
      steps_advanced: 0,
      stop_reason: null,
      phases: [],
      phase_commands: {
        read: `agentops operator start-check --adapter ${adapter} --limit ${limit}`,
        execute: `agentops operator loop-driver --adapter ${adapter} --max-steps 3 --limit ${limit} --confirm-loop`,
      },
      method_gates: [],
      commands: {
        start_check: `agentops operator start-check --adapter ${adapter} --limit ${limit}`,
        agent_plan_create: "agentops agent-plan create --help",
        knowledge_search: "agentops knowledge search --query '<task terms>'",
        base_reference: "agentops commander repo-map --query '<task terms>'",
        preview_loop: `agentops operator loop-driver --adapter ${adapter} --max-steps 3 --limit ${limit}`,
        confirm_loop: null,
      },
      gates: {},
      safety: {
        read_only: true,
        ledger_mutated: false,
        live_execution_performed: false,
        server_executes_shell: false,
        raw_prompt_omitted: true,
        raw_response_omitted: true,
        raw_content_omitted: true,
        token_omitted: true,
      },
      token_omitted: true,
      live_execution_performed: false,
    },
    next_commands: [],
    safety: {
      read_only: true,
      ledger_mutated: false,
      live_execution_performed: false,
      server_executes_shell: false,
      raw_prompt_omitted: true,
      raw_response_omitted: true,
      token_omitted: true,
    },
    token_omitted: true,
    live_execution_performed: false,
  });
  const safetyRaw = typeof raw.safety === "object" && raw.safety !== null ? raw.safety as Record<string, unknown> : {};
  const packet = normalizeOperatorLoopDriverAgentPacket(raw.agent_loop_packet, adapter, limit);
  const localRunPath = asArray<Record<string, unknown>>(raw.local_run_path).map((step) => ({
    step_id: String(step.step_id || ""),
    label: String(step.label || step.step_id || ""),
    phase: String(step.phase || ""),
    status: String(step.status || "unknown"),
    adapter: ["mock", "hermes", "openclaw"].includes(String(step.adapter)) ? String(step.adapter) as WorkerAdapterName : undefined,
    command: String(step.command || ""),
    verify_command: step.verify_command ? String(step.verify_command) : null,
    route: step.route ? String(step.route) : null,
    detail: step.detail ? String(step.detail) : undefined,
    mutating: boolValue(step.mutating),
    confirm_required: boolValue(step.confirm_required),
    writes_ledger: boolValue(step.writes_ledger),
    live_execution: boolValue(step.live_execution),
    service_control_preview: step.service_control_preview === undefined ? undefined : boolValue(step.service_control_preview),
    copy_only: step.copy_only === undefined ? undefined : boolValue(step.copy_only),
    server_executes_shell: step.server_executes_shell === undefined ? undefined : boolValue(step.server_executes_shell),
    receipt_required: step.receipt_required === undefined ? undefined : boolValue(step.receipt_required),
    control_readback_required: step.control_readback_required === undefined ? undefined : boolValue(step.control_readback_required),
    receipt_command: step.receipt_command ? String(step.receipt_command) : null,
    receipt_record_command: step.receipt_record_command ? String(step.receipt_record_command) : null,
    receipt_verify_record_command: step.receipt_verify_record_command ? String(step.receipt_verify_record_command) : null,
    receipt_state: typeof step.receipt_state === "object" && step.receipt_state !== null ? step.receipt_state as Record<string, unknown> : undefined,
    action_signature: step.action_signature ? String(step.action_signature) : null,
    source: step.source ? String(step.source) : null,
    token_omitted: step.token_omitted === undefined ? undefined : boolValue(step.token_omitted),
  })).filter((step) => step.step_id && step.command);
  return {
    provider: String(raw.provider || "agentops-operator"),
    operation: String(raw.operation || "operator_start_check"),
    status: String(raw.status || "unknown"),
    adapter: String(raw.adapter || adapter),
    workspace_id: String(raw.workspace_id || "local-demo"),
    summary: typeof raw.summary === "object" && raw.summary !== null ? raw.summary as Record<string, unknown> : {},
    loop_driver_entry: typeof raw.loop_driver_entry === "object" && raw.loop_driver_entry !== null ? raw.loop_driver_entry as Record<string, unknown> : {},
    acceptance_packet: typeof raw.acceptance_packet === "object" && raw.acceptance_packet !== null ? raw.acceptance_packet as Record<string, unknown> : {},
    local_loop_admission_packet: typeof raw.local_loop_admission_packet === "object" && raw.local_loop_admission_packet !== null ? raw.local_loop_admission_packet as Record<string, unknown> : {},
    local_run_path: localRunPath,
    agent_loop_packet: packet,
    next_commands: asArray<unknown>(raw.next_commands).map(String).filter(Boolean),
    safety: {
      read_only: safetyRaw.read_only === undefined ? true : boolValue(safetyRaw.read_only),
      ledger_mutated: boolValue(safetyRaw.ledger_mutated),
      live_execution_performed: boolValue(safetyRaw.live_execution_performed),
      server_executes_shell: safetyRaw.server_executes_shell === undefined ? undefined : boolValue(safetyRaw.server_executes_shell),
      raw_prompt_omitted: safetyRaw.raw_prompt_omitted === undefined ? true : boolValue(safetyRaw.raw_prompt_omitted),
      raw_response_omitted: safetyRaw.raw_response_omitted === undefined ? true : boolValue(safetyRaw.raw_response_omitted),
      token_omitted: safetyRaw.token_omitted === undefined ? true : boolValue(safetyRaw.token_omitted),
    },
    token_omitted: raw.token_omitted === undefined ? undefined : boolValue(raw.token_omitted),
    live_execution_performed: raw.live_execution_performed === undefined ? undefined : boolValue(raw.live_execution_performed),
  };
}

export async function loadOperatorLoopDriverPackets(limit = 8): Promise<OperatorLoopDriverPacketsPayload> {
  const adapters: OperatorStartCheckAdapter[] = ["hermes", "openclaw"];
  const startChecks = await Promise.all(adapters.map((adapter) => loadOperatorStartCheck(adapter, limit)));
  const packets = startChecks
    .map((check, index) => check.agent_loop_packet || normalizeOperatorLoopDriverAgentPacket(undefined, adapters[index], limit))
    .filter((packet) => packet.operation === "operator_loop_driver_agent_loop_packet");
  const startCheckMap = Object.fromEntries(startChecks.map((check) => [check.adapter, check])) as Record<string, OperatorStartCheckPayload>;
  const hasAttention = startChecks.some((check) => check.status !== "ready" && check.status !== "pass");
  return {
    provider: "agentops-operator",
    operation: "operator_loop_driver_packets",
    status: hasAttention ? "attention" : "ready",
    packets,
    start_checks: startCheckMap,
    safety: {
      read_only: true,
      ledger_mutated: false,
      live_execution_performed: false,
      server_executes_shell: packets.some((packet) => packet.safety.server_executes_shell),
      token_omitted: true,
    },
    token_omitted: true,
    live_execution_performed: false,
  };
}

function liveAcceptanceAdapter(raw: Record<string, unknown>, adapter: string): OperatorAgentLoopHandoffConsumerPayload["live_product_readiness"] {
  const adaptersRaw = typeof raw.adapters === "object" && raw.adapters !== null ? raw.adapters as Record<string, unknown> : {};
  const item = typeof adaptersRaw[adapter] === "object" && adaptersRaw[adapter] !== null ? adaptersRaw[adapter] as Record<string, unknown> : {};
  const latest = typeof item.latest_passing === "object" && item.latest_passing !== null
    ? item.latest_passing as Record<string, unknown>
    : typeof item.latest_attempt === "object" && item.latest_attempt !== null
      ? item.latest_attempt as Record<string, unknown>
      : {};
  return {
    adapter,
    status: String(item.status || (item.ok ? "fresh" : "missing")),
    fresh: boolValue(item.ok) && latest.pass !== false,
    run_id: latest.run_id ? String(latest.run_id) : null,
    task_id: latest.task_id ? String(latest.task_id) : null,
    artifact_id: latest.artifact_id ? String(latest.artifact_id) : null,
    plan_evidence_manifest_id: latest.plan_evidence_manifest_id ? String(latest.plan_evidence_manifest_id) : null,
    command: `agentops operator live-product-readiness --require-adapter ${adapter}`,
    token_omitted: latest.token_omitted === undefined ? true : boolValue(latest.token_omitted),
  };
}

export async function loadOperatorAgentLoopHandoff(limit = 8): Promise<OperatorAgentLoopHandoffPayload> {
  const canonical = await optionalApiJson<Record<string, unknown>>(`/operator/agent-loop-handoff?${new URLSearchParams({ freshness_hours: "72", limit: String(limit) }).toString()}`, {
    operation: "operator_agent_loop_handoff_unavailable",
  });
  if (canonical.operation === "operator_agent_loop_handoff") {
    return canonical as unknown as OperatorAgentLoopHandoffPayload;
  }
  const adapters: OperatorStartCheckAdapter[] = ["hermes", "openclaw"];
  const [localReadiness, liveAcceptance, launchPacket, ...startChecks] = await Promise.all([
    loadLocalReadiness(),
    optionalApiJson<Record<string, unknown>>(`/operator/live-acceptance?${new URLSearchParams({ freshness_hours: "72", limit: String(limit) }).toString()}`, {
      operation: "live_acceptance_readiness",
      status: "unavailable",
      adapters: {},
      token_omitted: true,
    }),
    loadOperatorLoopLaunchPacket(limit, "Agent Work Method Block"),
    ...adapters.map((adapter) => loadOperatorStartCheck(adapter, limit)),
  ]);
  const localRaw = localReadiness as unknown as Record<string, unknown>;
  const runningRaw = typeof localRaw.running_instance === "object" && localRaw.running_instance !== null ? localRaw.running_instance as Record<string, unknown> : {};
  const localCodeRaw = typeof localRaw.local_code_check === "object" && localRaw.local_code_check !== null ? localRaw.local_code_check as Record<string, unknown> : {};
  const currentCodeOk = boolValue(runningRaw.current) || boolValue(localCodeRaw.ok);
  const currentHead = runningRaw.git_head_sha || localCodeRaw.server_head_sha;
  const currentCode = {
    ok: currentCodeOk,
    status: String(runningRaw.status || localCodeRaw.status || (currentCodeOk ? "current" : "unknown")),
    git_head_sha: currentHead ? String(currentHead) : null,
    git_branch: runningRaw.git_branch ? String(runningRaw.git_branch) : null,
    server_pid: runningRaw.server_pid === undefined || runningRaw.server_pid === null ? null : numberValue(runningRaw.server_pid, 0),
    strict_command: `agentops local readiness --require-current-code --expect-head-sha ${currentHead || "<head_sha>"}`,
    token_omitted: true,
  };
  const control = launchPacket.control_summary || {};
  const consumers: OperatorAgentLoopHandoffConsumerPayload[] = startChecks.map((check, index) => {
    const adapter = adapters[index];
    const packet = check.agent_loop_packet || normalizeOperatorLoopDriverAgentPacket(undefined, adapter, limit);
    const acceptance = typeof check.acceptance_packet === "object" && check.acceptance_packet !== null ? check.acceptance_packet as Record<string, unknown> : {};
    const decision = typeof acceptance.decision === "object" && acceptance.decision !== null ? acceptance.decision as Record<string, unknown> : {};
    const live = liveAcceptanceAdapter(liveAcceptance, adapter);
    const methodGateIds = (packet.method_gates || []).map((gate) => gate.id).filter(Boolean);
    const phaseCommands = packet.phase_commands || {};
    const requiredPhases = ["read", "plan", "retrieve", "compare", "preflight", "execute", "verify", "record"];
    const blockers = [
      ...(!currentCode.ok ? ["current_code_not_current"] : []),
      ...(check.safety.server_executes_shell ? ["server_shell_safety_missing"] : []),
      ...(methodGateIds.length === 0 ? ["method_gates_missing"] : []),
      ...(requiredPhases.every((phase) => Object.prototype.hasOwnProperty.call(phaseCommands, phase)) ? [] : ["phase_commands_incomplete"]),
      ...(decision.can_preview_loop === false ? ["preview_loop_not_allowed"] : []),
    ];
    const attention = [
      ...(!packet.ready_to_confirm_loop ? ["bounded_loop_confirm_not_ready"] : []),
      ...(!live.fresh ? ["live_product_evidence_not_fresh"] : []),
      ...(check.status === "attention" ? ["start_check_attention"] : []),
    ];
    return {
      operation: "agent_loop_handoff_consumer",
      adapter,
      status: blockers.length ? "blocked" : attention.length ? "attention" : "ready",
      ready_for_handoff: blockers.length === 0,
      ready_for_bounded_loop_confirm: blockers.length === 0 && packet.ready_to_confirm_loop,
      ready_for_live_dispatch: blockers.length === 0 && live.fresh && decision.live_dispatch_allowed !== false,
      blockers,
      attention,
      start_check: {
        status: check.status,
        command: packet.commands.start_check || `agentops operator start-check --adapter ${adapter} --limit ${limit}`,
        current_phase: packet.current_phase,
        can_preview_loop: decision.can_preview_loop !== false,
        can_confirm_bounded_loop: packet.ready_to_confirm_loop,
        live_dispatch_requires_confirm_run: decision.live_dispatch_requires_confirm_run !== false,
        human_review_required: boolValue(decision.human_review_required),
        memory_review_required: boolValue(decision.memory_review_required),
        server_executes_shell: boolValue(check.safety.server_executes_shell),
        token_omitted: true,
      },
      launch_brief: {
        status: launchPacket.status,
        next_command: control.next_command || null,
        verify_command: control.verify_command || null,
        receipt_command: control.receipt_command || null,
        current_code_ok: currentCode.ok,
        control_mode: control.mode || null,
        recommended_step: typeof control.recommended_step === "object" && control.recommended_step !== null ? String((control.recommended_step as Record<string, unknown>).step_id || "") : null,
        token_omitted: true,
      },
      live_product_readiness: live,
      method: {
        phases: requiredPhases,
        phase_commands: Object.fromEntries(requiredPhases.map((phase) => [phase, phaseCommands[phase] || null])),
        method_gate_ids: methodGateIds,
        required_gate_ids: [
          "read_start_check",
          "read_current_code",
          "plan_agent_plan",
          "retrieve_knowledge",
          "compare_base_reference",
          "preflight_adapter",
          "execute_bounded_loop",
          "verify_loop",
          "record_memory_candidate",
        ],
        token_omitted: true,
      },
      commands: {
        agent_loop_handoff: `agentops operator agent-loop-handoff --adapter ${adapter} --limit ${limit}`,
        local_readiness: currentCode.strict_command,
        start_check: packet.commands.start_check || `agentops operator start-check --adapter ${adapter} --limit ${limit}`,
        launch_brief: `agentops operator loop-launch-packet --brief --adapter ${adapter} --limit ${limit}`,
        loop_driver_preview: packet.commands.preview_loop || `agentops operator loop-driver --adapter ${adapter} --max-steps 3 --limit ${limit}`,
        loop_driver_confirm: packet.commands.confirm_loop || `agentops operator loop-driver --adapter ${adapter} --max-steps 3 --limit ${limit} --confirm-loop`,
        adapter_preflight: packet.commands.adapter_preflight || `agentops worker preflight --adapter ${adapter}`,
        live_product_readiness: live.command,
        review_queue: packet.commands.review_queue || "agentops review queue --limit 20",
      },
      safety: {
        read_only: true,
        ledger_mutated: false,
        live_execution_performed: false,
        server_executes_shell: false,
        raw_prompt_omitted: true,
        raw_response_omitted: true,
        raw_content_omitted: true,
        token_omitted: true,
      },
      token_omitted: true,
    };
  });
  const status = consumers.some((item) => item.status === "blocked")
    ? "blocked"
    : consumers.some((item) => item.status === "attention")
      ? "attention"
      : "ready";
  return {
    provider: "agentops-operator",
    operation: "operator_agent_loop_handoff",
    status,
    workspace_id: launchPacket.workspace_id || localReadiness.workspace_id || "local-demo",
    adapters,
    current_code: currentCode,
    summary: {
      consumers: consumers.length + 1,
      ready_consumers: consumers.filter((item) => item.status === "ready").length + (currentCode.ok ? 1 : 0),
      attention_consumers: consumers.filter((item) => item.status === "attention").length,
      blocked_consumers: consumers.filter((item) => item.status === "blocked").length + (currentCode.ok ? 0 : 1),
      ready_for_handoff: consumers.every((item) => item.ready_for_handoff) && currentCode.ok,
      ready_for_all_bounded_loop_confirm: consumers.every((item) => item.ready_for_bounded_loop_confirm),
      fresh_live_adapters: consumers.filter((item) => item.live_product_readiness.fresh).length,
      current_code_ok: currentCode.ok,
    },
    consumers,
    codex_consumer: {
      operation: "agent_loop_handoff_codex_consumer",
      status: currentCode.ok ? "ready" : "blocked",
      uses_same_packets: true,
      commands: {
        read_handoff: `agentops operator agent-loop-handoff --limit ${limit}`,
        loop_control: `agentops operator loop-control --limit ${limit}`,
        loop_launch_brief: `agentops operator loop-launch-packet --brief --adapter hermes --limit ${limit}`,
        loop_driver_preview: `agentops operator loop-driver --adapter hermes --max-steps 3 --limit ${limit}`,
        review_queue: "agentops review queue --limit 20",
      },
      token_omitted: true,
    },
    safety: {
      read_only: true,
      ledger_mutated: false,
      live_execution_performed: false,
      server_executes_shell: false,
      raw_prompt_omitted: true,
      raw_response_omitted: true,
      raw_content_omitted: true,
      token_omitted: true,
    },
    token_omitted: true,
    live_execution_performed: false,
  };
}

export async function loadOperatorLoopSupervision(limit = 8): Promise<OperatorLoopSupervisionPayload> {
  const params = new URLSearchParams({ freshness_hours: "72", limit: String(limit) });
  const raw = await optionalApiJson<Record<string, unknown>>(`/operator/loop-supervision?${params.toString()}`, {
    provider: "agentops-operator",
    operation: "operator_loop_supervision",
    status: "unavailable",
    workspace_id: "local-demo",
    adapters: ["hermes", "openclaw"],
    handoff_summary: {},
    summary: {
      items: 0,
      ready_to_confirm: 0,
      record_first: 0,
      preview_only: 0,
      blocked: 0,
      can_confirm_all: false,
      record_required: false,
      current_code_ok: false,
    },
    items: [],
    work_packets: [],
    next_actions: [],
    safety: {
      read_only: true,
      ledger_mutated: false,
      live_execution_performed: false,
      server_executes_shell: false,
      raw_prompt_omitted: true,
      raw_response_omitted: true,
      raw_content_omitted: true,
      token_omitted: true,
    },
    token_omitted: true,
    live_execution_performed: false,
  });
  const summaryRaw = typeof raw.summary === "object" && raw.summary !== null ? raw.summary as Record<string, unknown> : {};
  const safetyRaw = typeof raw.safety === "object" && raw.safety !== null ? raw.safety as Record<string, unknown> : {};
  return {
    provider: String(raw.provider || "agentops-operator"),
    operation: String(raw.operation || "operator_loop_supervision"),
    status: String(raw.status || "unknown"),
    workspace_id: String(raw.workspace_id || "local-demo"),
    adapters: asArray<unknown>(raw.adapters).map(String).filter(Boolean),
    handoff_summary: typeof raw.handoff_summary === "object" && raw.handoff_summary !== null ? raw.handoff_summary as Record<string, unknown> : {},
    summary: {
      items: numberValue(summaryRaw.items, 0),
      ready_to_confirm: numberValue(summaryRaw.ready_to_confirm, 0),
      record_first: numberValue(summaryRaw.record_first, 0),
      preview_only: numberValue(summaryRaw.preview_only, 0),
      blocked: numberValue(summaryRaw.blocked, 0),
      can_confirm_all: boolValue(summaryRaw.can_confirm_all),
      record_required: boolValue(summaryRaw.record_required),
      current_code_ok: boolValue(summaryRaw.current_code_ok),
    },
    items: asArray<Record<string, unknown>>(raw.items).map((item) => {
      const itemSafety = typeof item.safety === "object" && item.safety !== null ? item.safety as Record<string, unknown> : {};
      const nextCommands = typeof item.next_commands === "object" && item.next_commands !== null ? item.next_commands as Record<string, unknown> : {};
      const localDeploymentRaw = typeof item.local_deployment === "object" && item.local_deployment !== null ? item.local_deployment as Record<string, unknown> : {};
      const localRunPathRaw = typeof localDeploymentRaw.local_run_path === "object" && localDeploymentRaw.local_run_path !== null ? localDeploymentRaw.local_run_path as Record<string, unknown> : {};
      const localRunSafetyRaw = typeof localRunPathRaw.safety === "object" && localRunPathRaw.safety !== null ? localRunPathRaw.safety as Record<string, unknown> : {};
      const serviceManagedRaw = typeof localDeploymentRaw.service_managed_loop === "object" && localDeploymentRaw.service_managed_loop !== null ? localDeploymentRaw.service_managed_loop as Record<string, unknown> : {};
      const serviceManagedCommandsRaw = typeof serviceManagedRaw.commands === "object" && serviceManagedRaw.commands !== null ? serviceManagedRaw.commands as Record<string, unknown> : {};
      const serviceManagedSafetyRaw = typeof serviceManagedRaw.safety === "object" && serviceManagedRaw.safety !== null ? serviceManagedRaw.safety as Record<string, unknown> : {};
      const managedExecutionRaw = typeof localDeploymentRaw.managed_execution_path === "object" && localDeploymentRaw.managed_execution_path !== null ? localDeploymentRaw.managed_execution_path as Record<string, unknown> : {};
      const managedExecutionCommandsRaw = typeof managedExecutionRaw.commands === "object" && managedExecutionRaw.commands !== null ? managedExecutionRaw.commands as Record<string, unknown> : {};
      const managedExecutionSafetyRaw = typeof managedExecutionRaw.safety === "object" && managedExecutionRaw.safety !== null ? managedExecutionRaw.safety as Record<string, unknown> : {};
      const runStartAdmissionRaw = typeof item.run_start_admission === "object" && item.run_start_admission !== null ? item.run_start_admission as Record<string, unknown> : {};
      const runStartSafetyRaw = typeof runStartAdmissionRaw.safety === "object" && runStartAdmissionRaw.safety !== null ? runStartAdmissionRaw.safety as Record<string, unknown> : {};
      const runStartReceiptProjectionRaw = typeof runStartAdmissionRaw.receipt_projection === "object" && runStartAdmissionRaw.receipt_projection !== null ? runStartAdmissionRaw.receipt_projection as Record<string, unknown> : {};
      const agentWorkPacketRaw = typeof item.agent_work_packet === "object" && item.agent_work_packet !== null ? item.agent_work_packet as Record<string, unknown> : {};
      return {
        operation: String(item.operation || "operator_loop_supervision_item"),
        adapter: String(item.adapter || "mock"),
        status: String(item.status || "unknown"),
        can_preview_loop: boolValue(item.can_preview_loop),
        can_confirm_bounded_loop: boolValue(item.can_confirm_bounded_loop),
        should_record_before_execute: boolValue(item.should_record_before_execute),
        ready_for_live_dispatch: boolValue(item.ready_for_live_dispatch),
        blockers: asArray<unknown>(item.blockers).map(String).filter(Boolean),
        attention: asArray<unknown>(item.attention).map(String).filter(Boolean),
        review_pressure: typeof item.review_pressure === "object" && item.review_pressure !== null ? item.review_pressure as Record<string, unknown> : {},
        gates: asArray<Record<string, unknown>>(item.gates).map((gate) => ({
          id: String(gate.id || ""),
          ok: gate.ok === undefined ? undefined : boolValue(gate.ok),
          status: gate.status ? String(gate.status) : undefined,
          command: gate.command === undefined || gate.command === null ? null : String(gate.command),
          recommended_adapter: gate.recommended_adapter === undefined || gate.recommended_adapter === null ? null : String(gate.recommended_adapter),
          service_managed_adapter: gate.service_managed_adapter === undefined || gate.service_managed_adapter === null ? null : String(gate.service_managed_adapter),
          server_executes_shell: gate.server_executes_shell === undefined ? undefined : boolValue(gate.server_executes_shell),
          token_omitted: gate.token_omitted === undefined ? undefined : boolValue(gate.token_omitted),
        })).filter((gate) => gate.id),
        local_deployment: Object.keys(localDeploymentRaw).length ? {
          local_run_path: Object.keys(localRunPathRaw).length ? {
            operation: localRunPathRaw.operation === undefined || localRunPathRaw.operation === null ? undefined : String(localRunPathRaw.operation),
            recommended_adapter: localRunPathRaw.recommended_adapter === undefined || localRunPathRaw.recommended_adapter === null ? null : String(localRunPathRaw.recommended_adapter),
            safety: {
              server_executes_shell: localRunSafetyRaw.server_executes_shell === undefined ? undefined : boolValue(localRunSafetyRaw.server_executes_shell),
              token_omitted: localRunSafetyRaw.token_omitted === undefined ? undefined : boolValue(localRunSafetyRaw.token_omitted),
            },
            token_omitted: localRunPathRaw.token_omitted === undefined ? undefined : boolValue(localRunPathRaw.token_omitted),
          } : undefined,
          service_managed_loop: Object.keys(serviceManagedRaw).length ? {
            operation: serviceManagedRaw.operation === undefined || serviceManagedRaw.operation === null ? undefined : String(serviceManagedRaw.operation),
            adapter: serviceManagedRaw.adapter === undefined || serviceManagedRaw.adapter === null ? null : String(serviceManagedRaw.adapter),
            manager: serviceManagedRaw.manager === undefined || serviceManagedRaw.manager === null ? null : String(serviceManagedRaw.manager),
            service_managed_loop_ready: serviceManagedRaw.service_managed_loop_ready === undefined ? undefined : boolValue(serviceManagedRaw.service_managed_loop_ready),
            service_active_loop_ready: serviceManagedRaw.service_active_loop_ready === undefined ? undefined : boolValue(serviceManagedRaw.service_active_loop_ready),
            service_loaded: serviceManagedRaw.service_loaded === undefined ? undefined : boolValue(serviceManagedRaw.service_loaded),
            active_status: serviceManagedRaw.active_status === undefined || serviceManagedRaw.active_status === null ? undefined : String(serviceManagedRaw.active_status),
            active_loop_status: serviceManagedRaw.active_loop_status === undefined || serviceManagedRaw.active_loop_status === null ? undefined : String(serviceManagedRaw.active_loop_status),
            status: serviceManagedRaw.status === undefined || serviceManagedRaw.status === null ? undefined : String(serviceManagedRaw.status),
            checked_status: serviceManagedRaw.checked_status === undefined || serviceManagedRaw.checked_status === null ? undefined : String(serviceManagedRaw.checked_status),
            installed_status: serviceManagedRaw.installed_status === undefined || serviceManagedRaw.installed_status === null ? undefined : String(serviceManagedRaw.installed_status),
            service_check_available: serviceManagedRaw.service_check_available === undefined ? undefined : boolValue(serviceManagedRaw.service_check_available),
            service_control_preview_available: serviceManagedRaw.service_control_preview_available === undefined ? undefined : boolValue(serviceManagedRaw.service_control_preview_available),
            receipt_required: serviceManagedRaw.receipt_required === undefined ? undefined : boolValue(serviceManagedRaw.receipt_required),
            receipt_verified: serviceManagedRaw.receipt_verified === undefined ? undefined : boolValue(serviceManagedRaw.receipt_verified),
            receipt_id: serviceManagedRaw.receipt_id === undefined || serviceManagedRaw.receipt_id === null ? null : String(serviceManagedRaw.receipt_id),
            control_readback_required: serviceManagedRaw.control_readback_required === undefined ? undefined : boolValue(serviceManagedRaw.control_readback_required),
            control_readback_attached: serviceManagedRaw.control_readback_attached === undefined ? undefined : boolValue(serviceManagedRaw.control_readback_attached),
            control_readback_id: serviceManagedRaw.control_readback_id === undefined || serviceManagedRaw.control_readback_id === null ? null : String(serviceManagedRaw.control_readback_id),
            live_execution_performed: serviceManagedRaw.live_execution_performed === undefined ? undefined : boolValue(serviceManagedRaw.live_execution_performed),
            commands: Object.fromEntries(Object.entries(serviceManagedCommandsRaw).map(([key, value]) => [key, value === undefined || value === null ? null : String(value)])),
            safety: {
              server_executes_shell: serviceManagedSafetyRaw.server_executes_shell === undefined ? undefined : boolValue(serviceManagedSafetyRaw.server_executes_shell),
              live_execution_performed: serviceManagedSafetyRaw.live_execution_performed === undefined ? undefined : boolValue(serviceManagedSafetyRaw.live_execution_performed),
              token_omitted: serviceManagedSafetyRaw.token_omitted === undefined ? undefined : boolValue(serviceManagedSafetyRaw.token_omitted),
            },
            token_omitted: serviceManagedRaw.token_omitted === undefined ? undefined : boolValue(serviceManagedRaw.token_omitted),
          } : undefined,
          managed_execution_path: Object.keys(managedExecutionRaw).length ? {
            operation: managedExecutionRaw.operation === undefined || managedExecutionRaw.operation === null ? undefined : String(managedExecutionRaw.operation),
            status: managedExecutionRaw.status === undefined || managedExecutionRaw.status === null ? undefined : String(managedExecutionRaw.status),
            adapter: managedExecutionRaw.adapter === undefined || managedExecutionRaw.adapter === null ? null : String(managedExecutionRaw.adapter),
            service_managed_loop_ready: managedExecutionRaw.service_managed_loop_ready === undefined ? undefined : boolValue(managedExecutionRaw.service_managed_loop_ready),
            service_active_loop_ready: managedExecutionRaw.service_active_loop_ready === undefined ? undefined : boolValue(managedExecutionRaw.service_active_loop_ready),
            service_loaded: managedExecutionRaw.service_loaded === undefined ? undefined : boolValue(managedExecutionRaw.service_loaded),
            service_active_status: managedExecutionRaw.service_active_status === undefined || managedExecutionRaw.service_active_status === null ? undefined : String(managedExecutionRaw.service_active_status),
            recommended_before_dispatch: managedExecutionRaw.recommended_before_dispatch === undefined || managedExecutionRaw.recommended_before_dispatch === null ? null : String(managedExecutionRaw.recommended_before_dispatch),
            commands: Object.fromEntries(Object.entries(managedExecutionCommandsRaw).map(([key, value]) => [key, value === undefined || value === null ? null : String(value)])),
            first_safe_commands: asArray<unknown>(managedExecutionRaw.first_safe_commands).map(String).filter(Boolean),
            confirm_required_commands: asArray<unknown>(managedExecutionRaw.confirm_required_commands).map(String).filter(Boolean),
            verify_commands: asArray<unknown>(managedExecutionRaw.verify_commands).map(String).filter(Boolean),
            gates: asArray<Record<string, unknown>>(managedExecutionRaw.gates).map((gate) => ({
              id: String(gate.id || ""),
              status: gate.status === undefined || gate.status === null ? undefined : String(gate.status),
              required: gate.required === undefined ? undefined : boolValue(gate.required),
              proof: gate.proof === undefined || gate.proof === null ? undefined : String(gate.proof),
              token_omitted: gate.token_omitted === undefined ? undefined : boolValue(gate.token_omitted),
            })).filter((gate) => gate.id),
            safety: {
              server_executes_shell: managedExecutionSafetyRaw.server_executes_shell === undefined ? undefined : boolValue(managedExecutionSafetyRaw.server_executes_shell),
              live_execution_performed: managedExecutionSafetyRaw.live_execution_performed === undefined ? undefined : boolValue(managedExecutionSafetyRaw.live_execution_performed),
              token_omitted: managedExecutionSafetyRaw.token_omitted === undefined ? undefined : boolValue(managedExecutionSafetyRaw.token_omitted),
            },
            token_omitted: managedExecutionRaw.token_omitted === undefined ? undefined : boolValue(managedExecutionRaw.token_omitted),
          } : undefined,
          token_omitted: localDeploymentRaw.token_omitted === undefined ? undefined : boolValue(localDeploymentRaw.token_omitted),
        } : undefined,
        agent_work_packet: Object.keys(agentWorkPacketRaw).length ? agentWorkPacketRaw : undefined,
        next_commands: {
          safe_read_commands: asArray<unknown>(nextCommands.safe_read_commands).map(String).filter(Boolean),
          preview_commands: asArray<unknown>(nextCommands.preview_commands).map(String).filter(Boolean),
          confirm_required_commands: asArray<unknown>(nextCommands.confirm_required_commands).map(String).filter(Boolean),
          recommended_next: nextCommands.recommended_next === undefined || nextCommands.recommended_next === null ? null : String(nextCommands.recommended_next),
          token_omitted: nextCommands.token_omitted === undefined ? undefined : boolValue(nextCommands.token_omitted),
        },
        commands: typeof item.commands === "object" && item.commands !== null ? item.commands as Record<string, string | null | undefined> : {},
        run_start_admission: Object.keys(runStartAdmissionRaw).length ? {
          operation: String(runStartAdmissionRaw.operation || "operator_loop_supervision_run_start_admission"),
          gateway_endpoint: String(runStartAdmissionRaw.gateway_endpoint || "POST /api/agent-gateway/runs/start"),
          runtime_type: String(runStartAdmissionRaw.runtime_type || item.adapter || "mock"),
          governed_runtime: boolValue(runStartAdmissionRaw.governed_runtime),
          would_allow_run_start: boolValue(runStartAdmissionRaw.would_allow_run_start),
          would_block_run_start: boolValue(runStartAdmissionRaw.would_block_run_start),
          fail_closed_error: String(runStartAdmissionRaw.fail_closed_error || "run_start_loop_supervision_blocked"),
          no_run_created_on_block: runStartAdmissionRaw.no_run_created_on_block === undefined ? true : boolValue(runStartAdmissionRaw.no_run_created_on_block),
          agent_plan_required: runStartAdmissionRaw.agent_plan_required === undefined ? true : boolValue(runStartAdmissionRaw.agent_plan_required),
          supervision_hash_state: String(runStartAdmissionRaw.supervision_hash_state || "bound_by_agent_gateway_run_start"),
          run_metadata_field: String(runStartAdmissionRaw.run_metadata_field || "loop_supervision_hash"),
          recommended_next: runStartAdmissionRaw.recommended_next === undefined || runStartAdmissionRaw.recommended_next === null ? null : String(runStartAdmissionRaw.recommended_next),
          status: String(runStartAdmissionRaw.status || (runStartAdmissionRaw.would_allow_run_start ? "pass" : "blocked")),
          contract: runStartAdmissionRaw.contract === undefined || runStartAdmissionRaw.contract === null ? undefined : String(runStartAdmissionRaw.contract),
          receipt_projection: Object.keys(runStartReceiptProjectionRaw).length ? {
            source: runStartReceiptProjectionRaw.source === undefined || runStartReceiptProjectionRaw.source === null ? undefined : String(runStartReceiptProjectionRaw.source),
            action_id: runStartReceiptProjectionRaw.action_id === undefined || runStartReceiptProjectionRaw.action_id === null ? undefined : String(runStartReceiptProjectionRaw.action_id),
            action_signature: runStartReceiptProjectionRaw.action_signature === undefined || runStartReceiptProjectionRaw.action_signature === null ? undefined : String(runStartReceiptProjectionRaw.action_signature),
            action_command: runStartReceiptProjectionRaw.action_command === undefined || runStartReceiptProjectionRaw.action_command === null ? undefined : String(runStartReceiptProjectionRaw.action_command),
            verify_command: runStartReceiptProjectionRaw.verify_command === undefined || runStartReceiptProjectionRaw.verify_command === null ? undefined : String(runStartReceiptProjectionRaw.verify_command),
            control_readback_required: runStartReceiptProjectionRaw.control_readback_required === undefined ? undefined : boolValue(runStartReceiptProjectionRaw.control_readback_required),
            control_readback_source: runStartReceiptProjectionRaw.control_readback_source === undefined || runStartReceiptProjectionRaw.control_readback_source === null ? undefined : String(runStartReceiptProjectionRaw.control_readback_source),
            token_omitted: runStartReceiptProjectionRaw.token_omitted === undefined ? undefined : boolValue(runStartReceiptProjectionRaw.token_omitted),
          } : undefined,
          safety: {
            read_only: runStartSafetyRaw.read_only === undefined ? true : boolValue(runStartSafetyRaw.read_only),
            ledger_mutated: boolValue(runStartSafetyRaw.ledger_mutated),
            live_execution_performed: boolValue(runStartSafetyRaw.live_execution_performed),
            server_executes_shell: boolValue(runStartSafetyRaw.server_executes_shell),
            raw_prompt_omitted: runStartSafetyRaw.raw_prompt_omitted === undefined ? true : boolValue(runStartSafetyRaw.raw_prompt_omitted),
            raw_response_omitted: runStartSafetyRaw.raw_response_omitted === undefined ? true : boolValue(runStartSafetyRaw.raw_response_omitted),
            raw_content_omitted: runStartSafetyRaw.raw_content_omitted === undefined ? true : boolValue(runStartSafetyRaw.raw_content_omitted),
            token_omitted: runStartSafetyRaw.token_omitted === undefined ? true : boolValue(runStartSafetyRaw.token_omitted),
          },
          token_omitted: runStartAdmissionRaw.token_omitted === undefined ? undefined : boolValue(runStartAdmissionRaw.token_omitted),
        } : undefined,
        safety: {
          read_only: itemSafety.read_only === undefined ? true : boolValue(itemSafety.read_only),
          ledger_mutated: boolValue(itemSafety.ledger_mutated),
          live_execution_performed: boolValue(itemSafety.live_execution_performed),
          server_executes_shell: boolValue(itemSafety.server_executes_shell),
          raw_prompt_omitted: itemSafety.raw_prompt_omitted === undefined ? true : boolValue(itemSafety.raw_prompt_omitted),
          raw_response_omitted: itemSafety.raw_response_omitted === undefined ? true : boolValue(itemSafety.raw_response_omitted),
          raw_content_omitted: itemSafety.raw_content_omitted === undefined ? true : boolValue(itemSafety.raw_content_omitted),
          token_omitted: itemSafety.token_omitted === undefined ? true : boolValue(itemSafety.token_omitted),
        },
        token_omitted: item.token_omitted === undefined ? undefined : boolValue(item.token_omitted),
      };
    }),
    work_packets: asArray<Record<string, unknown>>(raw.work_packets).filter((item) => typeof item === "object" && item !== null),
    next_actions: asArray<unknown>(raw.next_actions).map(String).filter(Boolean),
    safety: {
      read_only: safetyRaw.read_only === undefined ? true : boolValue(safetyRaw.read_only),
      ledger_mutated: boolValue(safetyRaw.ledger_mutated),
      live_execution_performed: boolValue(safetyRaw.live_execution_performed),
      server_executes_shell: boolValue(safetyRaw.server_executes_shell),
      raw_prompt_omitted: safetyRaw.raw_prompt_omitted === undefined ? true : boolValue(safetyRaw.raw_prompt_omitted),
      raw_response_omitted: safetyRaw.raw_response_omitted === undefined ? true : boolValue(safetyRaw.raw_response_omitted),
      raw_content_omitted: safetyRaw.raw_content_omitted === undefined ? true : boolValue(safetyRaw.raw_content_omitted),
      token_omitted: safetyRaw.token_omitted === undefined ? true : boolValue(safetyRaw.token_omitted),
    },
    token_omitted: raw.token_omitted === undefined ? undefined : boolValue(raw.token_omitted),
    live_execution_performed: raw.live_execution_performed === undefined ? undefined : boolValue(raw.live_execution_performed),
  };
}

export async function loadOperatorLoopBootstrap(limit = 8, options: { fast?: boolean } = {}): Promise<OperatorLoopBootstrapPayload> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (options.fast) params.set("fast", "1");
  const fallbackMode = options.fast ? "fast" : "deep";
  const fallbackModeFlag = options.fast ? " --fast" : "";
  const raw = await optionalApiJson<Record<string, unknown>>(`/operator/loop-bootstrap?${params.toString()}`, {
    provider: "agentops-operator",
    operation: "operator_loop_bootstrap",
    status: "unavailable",
    mode: fallbackMode,
    workspace_id: "local-demo",
    adapters: ["hermes", "openclaw"],
    summary: {
      items: 0,
      ready: 0,
      attention: 0,
      blocked: 0,
      service_closure_required: 0,
      service_active_loop_ready: 0,
      current_code_ok: false,
      local_cli_service_check_performed: false,
    },
    items: [],
    next_actions: [
      `agentops operator loop-bootstrap --adapter hermes --limit ${limit}${fallbackModeFlag}`,
      `agentops operator loop-bootstrap --adapter openclaw --limit ${limit}${fallbackModeFlag}`,
    ],
    contract: "read-only API bootstrap packet fallback",
    safety: {
      read_only: true,
      ledger_mutated: false,
      live_execution_performed: false,
      server_executes_shell: false,
      local_cli_service_check_performed: false,
      raw_prompt_omitted: true,
      raw_response_omitted: true,
      raw_content_omitted: true,
      token_omitted: true,
    },
    token_omitted: true,
    live_execution_performed: false,
  });
  const summaryRaw = typeof raw.summary === "object" && raw.summary !== null ? raw.summary as Record<string, unknown> : {};
  const safetyRaw = typeof raw.safety === "object" && raw.safety !== null ? raw.safety as Record<string, unknown> : {};
  return {
    provider: String(raw.provider || "agentops-operator"),
    operation: String(raw.operation || "operator_loop_bootstrap"),
    status: String(raw.status || "unknown"),
    mode: raw.mode === undefined || raw.mode === null ? fallbackMode : String(raw.mode),
    workspace_id: String(raw.workspace_id || "local-demo"),
    adapters: asArray<unknown>(raw.adapters).map(String).filter(Boolean),
    summary: {
      items: numberValue(summaryRaw.items, 0),
      ready: numberValue(summaryRaw.ready, 0),
      attention: numberValue(summaryRaw.attention, 0),
      blocked: numberValue(summaryRaw.blocked, 0),
      service_closure_required: numberValue(summaryRaw.service_closure_required, 0),
      service_active_loop_ready: numberValue(summaryRaw.service_active_loop_ready, 0),
      current_code_ok: boolValue(summaryRaw.current_code_ok),
      local_cli_service_check_performed: boolValue(summaryRaw.local_cli_service_check_performed),
    },
    items: asArray<Record<string, unknown>>(raw.items).map((item) => {
      const itemSummary = typeof item.summary === "object" && item.summary !== null ? item.summary as Record<string, unknown> : {};
      const itemSafety = typeof item.safety === "object" && item.safety !== null ? item.safety as Record<string, unknown> : {};
      const supervisionRaw = typeof item.supervision === "object" && item.supervision !== null ? item.supervision as Record<string, unknown> : {};
      return {
        operation: String(item.operation || "operator_loop_bootstrap_item"),
        status: String(item.status || "unknown"),
        adapter: String(item.adapter || "unknown"),
        manager: String(item.manager || "launchd"),
        next_action: item.next_action === undefined || item.next_action === null ? null : String(item.next_action),
        summary: {
          start_check_status: itemSummary.start_check_status === undefined || itemSummary.start_check_status === null ? null : String(itemSummary.start_check_status),
          supervision_status: itemSummary.supervision_status === undefined || itemSummary.supervision_status === null ? null : String(itemSummary.supervision_status),
          current_code_ok: boolValue(itemSummary.current_code_ok),
          service_closure_required: boolValue(itemSummary.service_closure_required),
          service_closure_step: itemSummary.service_closure_step === undefined || itemSummary.service_closure_step === null ? null : String(itemSummary.service_closure_step),
          service_managed_loop_ready: boolValue(itemSummary.service_managed_loop_ready),
          service_active_loop_ready: boolValue(itemSummary.service_active_loop_ready),
          service_loaded: boolValue(itemSummary.service_loaded),
          local_cli_service_check_performed: boolValue(itemSummary.local_cli_service_check_performed),
          can_confirm_bounded_loop: boolValue(itemSummary.can_confirm_bounded_loop),
        },
        bootstrap_steps: asArray<Record<string, unknown>>(item.bootstrap_steps).map((step) => ({
          id: String(step.id || ""),
          phase: String(step.phase || ""),
          status: step.status === undefined || step.status === null ? null : String(step.status),
          command: step.command === undefined || step.command === null ? null : String(step.command),
          confirm_required: step.confirm_required === undefined ? undefined : boolValue(step.confirm_required),
          server_executes_shell: step.server_executes_shell === undefined ? undefined : boolValue(step.server_executes_shell),
          token_omitted: step.token_omitted === undefined ? undefined : boolValue(step.token_omitted),
        })).filter((step) => step.id),
        commands: typeof item.commands === "object" && item.commands !== null ? item.commands as Record<string, string | null | undefined> : {},
        service_check: typeof item.service_check === "object" && item.service_check !== null ? item.service_check as Record<string, unknown> : undefined,
        service_closure: typeof item.service_closure === "object" && item.service_closure !== null ? item.service_closure as Record<string, unknown> : undefined,
        supervision: Object.keys(supervisionRaw).length ? {
          status: supervisionRaw.status === undefined || supervisionRaw.status === null ? null : String(supervisionRaw.status),
          primary_next_action: typeof supervisionRaw.primary_next_action === "object" && supervisionRaw.primary_next_action !== null ? supervisionRaw.primary_next_action as Record<string, unknown> : undefined,
          service_closure: typeof supervisionRaw.service_closure === "object" && supervisionRaw.service_closure !== null ? supervisionRaw.service_closure as Record<string, unknown> : undefined,
          token_omitted: supervisionRaw.token_omitted === undefined ? undefined : boolValue(supervisionRaw.token_omitted),
        } : undefined,
        safety: {
          read_only: itemSafety.read_only === undefined ? true : boolValue(itemSafety.read_only),
          ledger_mutated: boolValue(itemSafety.ledger_mutated),
          live_execution_performed: boolValue(itemSafety.live_execution_performed),
          server_executes_shell: boolValue(itemSafety.server_executes_shell),
          local_cli_service_check_performed: itemSafety.local_cli_service_check_performed === undefined ? undefined : boolValue(itemSafety.local_cli_service_check_performed),
          token_omitted: itemSafety.token_omitted === undefined ? true : boolValue(itemSafety.token_omitted),
        },
        token_omitted: item.token_omitted === undefined ? undefined : boolValue(item.token_omitted),
        live_execution_performed: item.live_execution_performed === undefined ? undefined : boolValue(item.live_execution_performed),
      };
    }),
    next_actions: asArray<unknown>(raw.next_actions).map(String).filter(Boolean),
    supervision_summary: typeof raw.supervision_summary === "object" && raw.supervision_summary !== null ? raw.supervision_summary as Record<string, unknown> : undefined,
    contract: raw.contract ? String(raw.contract) : undefined,
    safety: {
      read_only: safetyRaw.read_only === undefined ? true : boolValue(safetyRaw.read_only),
      ledger_mutated: boolValue(safetyRaw.ledger_mutated),
      live_execution_performed: boolValue(safetyRaw.live_execution_performed),
      server_executes_shell: boolValue(safetyRaw.server_executes_shell),
      local_cli_service_check_performed: safetyRaw.local_cli_service_check_performed === undefined ? undefined : boolValue(safetyRaw.local_cli_service_check_performed),
      raw_prompt_omitted: safetyRaw.raw_prompt_omitted === undefined ? true : boolValue(safetyRaw.raw_prompt_omitted),
      raw_response_omitted: safetyRaw.raw_response_omitted === undefined ? true : boolValue(safetyRaw.raw_response_omitted),
      raw_content_omitted: safetyRaw.raw_content_omitted === undefined ? true : boolValue(safetyRaw.raw_content_omitted),
      token_omitted: safetyRaw.token_omitted === undefined ? true : boolValue(safetyRaw.token_omitted),
    },
    token_omitted: raw.token_omitted === undefined ? undefined : boolValue(raw.token_omitted),
    live_execution_performed: raw.live_execution_performed === undefined ? undefined : boolValue(raw.live_execution_performed),
  };
}

export async function loadOperatorRuntimeDoctor(limit = 8): Promise<OperatorRuntimeDoctorPayload> {
  const params = new URLSearchParams({ limit: String(limit) });
  const raw = await optionalApiJson<Record<string, unknown>>(`/operator/runtime-doctor?${params.toString()}`, {
    provider: "agentops-operator",
    operation: "operator_runtime_doctor",
    status: "unavailable",
    workspace_id: "local-demo",
    summary: {
      mis_status: "unavailable",
      operator_health_score: null,
      recommended_adapter: "mock",
      ready_adapters: [],
      live_ready_adapters: [],
      requires_confirm_run: [],
      requires_prepared_action: [],
      remote_worker_count: 0,
      stale_remote_enrollments: 0,
      never_seen_remote_enrollments: 0,
      control_status: "unknown",
      control_mode: "copy_only",
      evidence_chain_status: "unknown",
      blocked_gates: [],
      attention_gates: [],
    },
    gates: [],
    commands: {
      operator_runtime_doctor: "agentops operator runtime-doctor --limit 8",
      operator_health: "agentops operator health --limit 20",
      worker_readiness: "agentops worker readiness",
    },
    safety: {
      read_only: true,
      ledger_mutated: false,
      live_execution_performed: false,
      server_executes_shell: false,
      raw_prompt_omitted: true,
      raw_response_omitted: true,
      token_omitted: true,
    },
    token_omitted: true,
    live_execution_performed: false,
  });
  const summaryRaw = typeof raw.summary === "object" && raw.summary !== null ? raw.summary as Record<string, unknown> : {};
  const safetyRaw = typeof raw.safety === "object" && raw.safety !== null ? raw.safety as Record<string, unknown> : {};
  const authRaw = typeof raw.auth === "object" && raw.auth !== null ? raw.auth as Record<string, unknown> : {};
  const commandsRaw = typeof raw.commands === "object" && raw.commands !== null ? raw.commands as Record<string, unknown> : {};
  const commands = Object.fromEntries(
    Object.entries(commandsRaw)
      .map(([key, value]) => [key, String(value || "")])
      .filter(([, value]) => value),
  );
  return {
    provider: String(raw.provider || "agentops-operator"),
    operation: String(raw.operation || "operator_runtime_doctor"),
    status: String(raw.status || "unknown"),
    workspace_id: String(raw.workspace_id || "local-demo"),
    base_url: raw.base_url ? String(raw.base_url) : undefined,
    summary: {
      mis_status: summaryRaw.mis_status ? String(summaryRaw.mis_status) : undefined,
      operator_health_score: summaryRaw.operator_health_score === undefined || summaryRaw.operator_health_score === null ? null : numberValue(summaryRaw.operator_health_score, 0),
      recommended_adapter: summaryRaw.recommended_adapter ? String(summaryRaw.recommended_adapter) : undefined,
      ready_adapters: asArray<unknown>(summaryRaw.ready_adapters).map(String).filter(Boolean),
      live_ready_adapters: asArray<unknown>(summaryRaw.live_ready_adapters).map(String).filter(Boolean),
      requires_confirm_run: asArray<unknown>(summaryRaw.requires_confirm_run).map(String).filter(Boolean),
      requires_prepared_action: asArray<unknown>(summaryRaw.requires_prepared_action).map(String).filter(Boolean),
      remote_worker_count: numberValue(summaryRaw.remote_worker_count, 0),
      stale_remote_enrollments: numberValue(summaryRaw.stale_remote_enrollments, 0),
      never_seen_remote_enrollments: numberValue(summaryRaw.never_seen_remote_enrollments, 0),
      control_status: summaryRaw.control_status ? String(summaryRaw.control_status) : undefined,
      control_mode: summaryRaw.control_mode ? String(summaryRaw.control_mode) : undefined,
      evidence_chain_status: summaryRaw.evidence_chain_status ? String(summaryRaw.evidence_chain_status) : undefined,
      blocked_gates: asArray<unknown>(summaryRaw.blocked_gates).map(String).filter(Boolean),
      attention_gates: asArray<unknown>(summaryRaw.attention_gates).map(String).filter(Boolean),
    },
    gates: asArray<Record<string, unknown>>(raw.gates).map((item) => ({
      id: String(item.id || ""),
      label: String(item.label || item.id || ""),
      status: String(item.status || "unknown"),
      ok: boolValue(item.ok),
      detail: item.detail ? String(item.detail) : undefined,
      next_action: item.next_action ? String(item.next_action) : null,
      token_omitted: item.token_omitted === undefined ? undefined : boolValue(item.token_omitted),
    })).filter((item) => item.id),
    commands,
    sources: typeof raw.sources === "object" && raw.sources !== null ? raw.sources as Record<string, unknown> : undefined,
    contract: raw.contract ? String(raw.contract) : undefined,
    auth: raw.auth ? {
      mode: String(authRaw.mode || "unknown"),
      scoped: boolValue(authRaw.scoped),
      required_scope: String(authRaw.required_scope || "tasks:read"),
      workspace_id: String(authRaw.workspace_id || "local-demo"),
      agent_id: authRaw.agent_id ? String(authRaw.agent_id) : null,
      token_omitted: authRaw.token_omitted === undefined ? undefined : boolValue(authRaw.token_omitted),
    } : undefined,
    safety: {
      read_only: boolValue(safetyRaw.read_only),
      ledger_mutated: boolValue(safetyRaw.ledger_mutated),
      live_execution_performed: boolValue(safetyRaw.live_execution_performed),
      server_executes_shell: safetyRaw.server_executes_shell === undefined ? undefined : boolValue(safetyRaw.server_executes_shell),
      raw_prompt_omitted: boolValue(safetyRaw.raw_prompt_omitted),
      raw_response_omitted: boolValue(safetyRaw.raw_response_omitted),
      token_omitted: boolValue(safetyRaw.token_omitted),
    },
    token_omitted: raw.token_omitted === undefined ? undefined : boolValue(raw.token_omitted),
    live_execution_performed: raw.live_execution_performed === undefined ? undefined : boolValue(raw.live_execution_performed),
  };
}

export async function loadOperatorExecutionMode(
  adapter: WorkerAdapterName = "mock",
  confirmRun = false,
  limit = 8,
): Promise<OperatorExecutionModePayload> {
  const params = new URLSearchParams({
    adapter,
    confirm_run: confirmRun ? "true" : "false",
    limit: String(limit),
  });
  const raw = await optionalApiJson<Record<string, unknown>>(`/operator/execution-mode?${params.toString()}`, {
    provider: "agentops-operator",
    operation: "operator_execution_mode",
    status: adapter === "mock" ? "planned" : "attention",
    workspace_id: "local-demo",
    adapter,
    mode: adapter === "mock" ? "dry_run_or_mock" : "live_confirmation_required",
    selected_path: adapter === "mock" ? "safe_mock_worker" : "waiting_for_confirm_run",
    summary: {
      adapter,
      adapter_readiness: adapter === "mock" ? "ready" : "unknown",
      trust_status: "unknown",
      selected_path: adapter === "mock" ? "safe_mock_worker" : "waiting_for_confirm_run",
      live_confirm_required: adapter !== "mock",
      confirm_run: confirmRun,
      confirm_run_wall: adapter === "mock" || confirmRun ? "pass" : "attention",
      prepared_action_wall: "planned",
      pending_approvals: 0,
      active_workflow_jobs: 0,
      runtime_doctor_status: "unavailable",
      blocked_gates: [],
      attention_gates: [],
      recommended_adapter: "mock",
    },
    selected_route: {
      adapter,
      readiness: adapter === "mock" ? "ready" : "unknown",
      trust_status: "unknown",
      recommended_action: "agentops worker readiness",
      requires_confirm_run: adapter !== "mock",
      requires_prepared_action: false,
      token_omitted: true,
    },
    gates: [],
    commands: {
      execution_mode: `agentops operator execution-mode --adapter ${adapter}${confirmRun ? " --confirm-run" : ""}`,
      worker_readiness: "agentops worker readiness",
      runtime_doctor: "agentops operator runtime-doctor --limit 8",
      review_queue: "agentops review queue --limit 20",
    },
    safety: {
      read_only: true,
      ledger_mutated: false,
      live_execution_performed: false,
      server_executes_shell: false,
      raw_prompt_omitted: true,
      raw_response_omitted: true,
      token_omitted: true,
    },
    token_omitted: true,
    live_execution_performed: false,
  });
  const rawAdapter = String(raw.adapter || adapter);
  const normalizedAdapter = (["mock", "hermes", "openclaw"].includes(rawAdapter) ? rawAdapter : adapter) as WorkerAdapterName;
  const summaryRaw = typeof raw.summary === "object" && raw.summary !== null ? raw.summary as Record<string, unknown> : {};
  const routeRaw = typeof raw.selected_route === "object" && raw.selected_route !== null ? raw.selected_route as Record<string, unknown> : {};
  const safetyRaw = typeof raw.safety === "object" && raw.safety !== null ? raw.safety as Record<string, unknown> : {};
  const commandsRaw = typeof raw.commands === "object" && raw.commands !== null ? raw.commands as Record<string, unknown> : {};
  const commands = Object.fromEntries(
    Object.entries(commandsRaw)
      .map(([key, value]) => [key, String(value || "")])
      .filter(([, value]) => value),
  );
  const routeAdapterRaw = String(routeRaw.adapter || normalizedAdapter);
  const routeAdapter = (["mock", "hermes", "openclaw"].includes(routeAdapterRaw) ? routeAdapterRaw : normalizedAdapter) as WorkerAdapterName;
  return {
    provider: String(raw.provider || "agentops-operator"),
    operation: String(raw.operation || "operator_execution_mode"),
    status: String(raw.status || "unknown"),
    workspace_id: String(raw.workspace_id || "local-demo"),
    adapter: normalizedAdapter,
    mode: String(raw.mode || ""),
    selected_path: String(raw.selected_path || ""),
    summary: {
      adapter: (["mock", "hermes", "openclaw"].includes(String(summaryRaw.adapter)) ? String(summaryRaw.adapter) : normalizedAdapter) as WorkerAdapterName,
      adapter_readiness: summaryRaw.adapter_readiness ? String(summaryRaw.adapter_readiness) : undefined,
      trust_status: summaryRaw.trust_status ? String(summaryRaw.trust_status) : undefined,
      selected_path: summaryRaw.selected_path ? String(summaryRaw.selected_path) : undefined,
      live_confirm_required: summaryRaw.live_confirm_required === undefined ? undefined : boolValue(summaryRaw.live_confirm_required),
      confirm_run: summaryRaw.confirm_run === undefined ? undefined : boolValue(summaryRaw.confirm_run),
      confirm_run_wall: summaryRaw.confirm_run_wall ? String(summaryRaw.confirm_run_wall) : undefined,
      prepared_action_wall: summaryRaw.prepared_action_wall ? String(summaryRaw.prepared_action_wall) : undefined,
      pending_approvals: numberValue(summaryRaw.pending_approvals, 0),
      active_workflow_jobs: numberValue(summaryRaw.active_workflow_jobs, 0),
      runtime_doctor_status: summaryRaw.runtime_doctor_status ? String(summaryRaw.runtime_doctor_status) : undefined,
      blocked_gates: asArray<unknown>(summaryRaw.blocked_gates).map(String).filter(Boolean),
      attention_gates: asArray<unknown>(summaryRaw.attention_gates).map(String).filter(Boolean),
      recommended_adapter: summaryRaw.recommended_adapter ? String(summaryRaw.recommended_adapter) : undefined,
    },
    selected_route: {
      adapter: routeAdapter,
      readiness: routeRaw.readiness ? String(routeRaw.readiness) : undefined,
      trust_status: routeRaw.trust_status ? String(routeRaw.trust_status) : undefined,
      target_resource: routeRaw.target_resource ? String(routeRaw.target_resource) : null,
      recommended_action: routeRaw.recommended_action ? String(routeRaw.recommended_action) : undefined,
      requires_confirm_run: routeRaw.requires_confirm_run === undefined ? undefined : boolValue(routeRaw.requires_confirm_run),
      requires_prepared_action: routeRaw.requires_prepared_action === undefined ? undefined : boolValue(routeRaw.requires_prepared_action),
      token_omitted: routeRaw.token_omitted === undefined ? undefined : boolValue(routeRaw.token_omitted),
    },
    gates: asArray<Record<string, unknown>>(raw.gates).map((item) => ({
      id: String(item.id || ""),
      label: String(item.label || item.id || ""),
      status: String(item.status || "unknown"),
      detail: item.detail ? String(item.detail) : undefined,
      next_action: item.next_action ? String(item.next_action) : null,
      token_omitted: item.token_omitted === undefined ? undefined : boolValue(item.token_omitted),
    })).filter((item) => item.id),
    commands,
    sources: typeof raw.sources === "object" && raw.sources !== null ? raw.sources as Record<string, unknown> : undefined,
    contract: raw.contract ? String(raw.contract) : undefined,
    safety: {
      read_only: boolValue(safetyRaw.read_only),
      ledger_mutated: boolValue(safetyRaw.ledger_mutated),
      live_execution_performed: boolValue(safetyRaw.live_execution_performed),
      server_executes_shell: safetyRaw.server_executes_shell === undefined ? undefined : boolValue(safetyRaw.server_executes_shell),
      raw_prompt_omitted: boolValue(safetyRaw.raw_prompt_omitted),
      raw_response_omitted: boolValue(safetyRaw.raw_response_omitted),
      token_omitted: boolValue(safetyRaw.token_omitted),
    },
    token_omitted: raw.token_omitted === undefined ? undefined : boolValue(raw.token_omitted),
    live_execution_performed: raw.live_execution_performed === undefined ? undefined : boolValue(raw.live_execution_performed),
  };
}

export async function loadOperatorHealth(limit = 12, loopId = ""): Promise<OperatorHealthPayload> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (loopId) params.set("loop_id", loopId);
  const raw = await optionalApiJson<Record<string, unknown>>(`/operator/health?${params.toString()}`, {
    provider: "agentops-operator",
    operation: "operator_health",
    status: "unavailable",
    score: 0,
    workspace_id: "local-demo",
    loop_id: loopId || null,
    summary: {},
    components: [],
    control_summary: {
      operation: "operator_loop_control_summary",
      status: "unavailable",
      mode: "read_only_copy",
      recommended_step: {},
      next_command: null,
      verify_command: null,
      receipt_command: null,
      requires_human: false,
      requires_receipt: false,
      server_executes_shell: false,
      copy_only: true,
      token_omitted: true,
    },
    loop_control: {
      status: "unknown",
      mode: "read_only_copy",
      next_action: "agentops operator handoff --limit 12",
      copy_only: true,
      server_executes_shell: false,
      control_readback_source: "agentops operator advance-loop --confirm-advance",
      token_omitted: true,
    },
    risks: [],
    next_actions: ["agentops operator health --limit 12"],
    sources: {},
    auth: {
      mode: "local_dev_no_token",
      scoped: false,
      required_scope: "tasks:read",
      workspace_id: "local-demo",
      token_omitted: true,
    },
    safety: {
      read_only: true,
      ledger_mutated: false,
      live_execution_performed: false,
      raw_prompt_omitted: true,
      raw_response_omitted: true,
      token_omitted: true,
    },
    token_omitted: true,
    live_execution_performed: false,
  });
  const summaryRaw = typeof raw.summary === "object" && raw.summary !== null ? raw.summary as Record<string, unknown> : {};
  const safetyRaw = typeof raw.safety === "object" && raw.safety !== null ? raw.safety as Record<string, unknown> : {};
  const authRaw = typeof raw.auth === "object" && raw.auth !== null ? raw.auth as Record<string, unknown> : {};
  const controlRaw = typeof raw.control_summary === "object" && raw.control_summary !== null ? raw.control_summary as Record<string, unknown> : {};
  const loopControlRaw = typeof raw.loop_control === "object" && raw.loop_control !== null ? raw.loop_control as Record<string, unknown> : {};
  return {
    provider: String(raw.provider || "agentops-operator"),
    operation: String(raw.operation || "operator_health"),
    status: String(raw.status || "unknown"),
    score: numberValue(raw.score, 0),
    workspace_id: String(raw.workspace_id || "local-demo"),
    loop_id: raw.loop_id ? String(raw.loop_id) : null,
    summary: {
      components: numberValue(summaryRaw.components, 0),
      ready: numberValue(summaryRaw.ready, 0),
      attention: numberValue(summaryRaw.attention, 0),
      blocked: numberValue(summaryRaw.blocked, 0),
      review_items_total: numberValue(summaryRaw.review_items_total, 0),
      operator_actions: numberValue(summaryRaw.operator_actions, 0),
      loop_health_score: numberValue(summaryRaw.loop_health_score, 0),
      worker_fleet_status: summaryRaw.worker_fleet_status ? String(summaryRaw.worker_fleet_status) : undefined,
      security_status: summaryRaw.security_status ? String(summaryRaw.security_status) : undefined,
      local_readiness_status: summaryRaw.local_readiness_status ? String(summaryRaw.local_readiness_status) : undefined,
      control_status: summaryRaw.control_status ? String(summaryRaw.control_status) : undefined,
      control_mode: summaryRaw.control_mode ? String(summaryRaw.control_mode) : undefined,
      control_selected_gate: summaryRaw.control_selected_gate ? String(summaryRaw.control_selected_gate) : null,
      control_requires_human: summaryRaw.control_requires_human === undefined ? undefined : boolValue(summaryRaw.control_requires_human),
      control_requires_receipt: summaryRaw.control_requires_receipt === undefined ? undefined : boolValue(summaryRaw.control_requires_receipt),
    },
    components: asArray<Record<string, unknown>>(raw.components).map((item) => ({
      id: String(item.id || ""),
      label: String(item.label || item.id || ""),
      status: String(item.status || "unknown"),
      score: numberValue(item.score, 0),
      weight: numberValue(item.weight, 0),
      summary: item.summary ? String(item.summary) : undefined,
      next_action: item.next_action ? String(item.next_action) : undefined,
    })).filter((item) => item.id),
    control_summary: {
      operation: String(controlRaw.operation || "operator_loop_control_summary"),
      status: String(controlRaw.status || "unknown"),
      mode: controlRaw.mode ? String(controlRaw.mode) : undefined,
      loop_id: controlRaw.loop_id ? String(controlRaw.loop_id) : null,
      recommended_step: typeof controlRaw.recommended_step === "object" && controlRaw.recommended_step !== null ? controlRaw.recommended_step as Record<string, unknown> : {},
      next_command: controlRaw.next_command ? String(controlRaw.next_command) : null,
      verify_command: controlRaw.verify_command ? String(controlRaw.verify_command) : null,
      receipt_command: controlRaw.receipt_command ? String(controlRaw.receipt_command) : null,
      requires_human: controlRaw.requires_human === undefined ? undefined : boolValue(controlRaw.requires_human),
      requires_receipt: controlRaw.requires_receipt === undefined ? undefined : boolValue(controlRaw.requires_receipt),
      server_executes_shell: controlRaw.server_executes_shell === undefined ? undefined : boolValue(controlRaw.server_executes_shell),
      copy_only: controlRaw.copy_only === undefined ? undefined : boolValue(controlRaw.copy_only),
      step_counts: typeof controlRaw.step_counts === "object" && controlRaw.step_counts !== null ? controlRaw.step_counts as Record<string, number> : {},
      selected_gate: controlRaw.selected_gate ? String(controlRaw.selected_gate) : null,
      selected_status: controlRaw.selected_status ? String(controlRaw.selected_status) : null,
      policy_id: controlRaw.policy_id ? String(controlRaw.policy_id) : undefined,
      token_omitted: controlRaw.token_omitted === undefined ? undefined : boolValue(controlRaw.token_omitted),
    },
    loop_control: {
      status: String(loopControlRaw.status || "unknown"),
      source: loopControlRaw.source ? String(loopControlRaw.source) : undefined,
      mode: loopControlRaw.mode ? String(loopControlRaw.mode) : undefined,
      recommended_step: loopControlRaw.recommended_step ? String(loopControlRaw.recommended_step) : undefined,
      recommended_step_status: loopControlRaw.recommended_step_status ? String(loopControlRaw.recommended_step_status) : undefined,
      selected_gate: loopControlRaw.selected_gate ? String(loopControlRaw.selected_gate) : null,
      selected_status: loopControlRaw.selected_status ? String(loopControlRaw.selected_status) : null,
      next_action: loopControlRaw.next_action ? String(loopControlRaw.next_action) : null,
      verify_command: loopControlRaw.verify_command ? String(loopControlRaw.verify_command) : null,
      receipt_command: loopControlRaw.receipt_command ? String(loopControlRaw.receipt_command) : null,
      requires_human: loopControlRaw.requires_human === undefined ? undefined : boolValue(loopControlRaw.requires_human),
      requires_receipt: loopControlRaw.requires_receipt === undefined ? undefined : boolValue(loopControlRaw.requires_receipt),
      copy_only: loopControlRaw.copy_only === undefined ? undefined : boolValue(loopControlRaw.copy_only),
      server_executes_shell: loopControlRaw.server_executes_shell === undefined ? undefined : boolValue(loopControlRaw.server_executes_shell),
      server_shell_execution: loopControlRaw.server_shell_execution === undefined ? undefined : boolValue(loopControlRaw.server_shell_execution),
      refresh_cache_required_after_receipt: loopControlRaw.refresh_cache_required_after_receipt === undefined ? undefined : boolValue(loopControlRaw.refresh_cache_required_after_receipt),
      control_readback_source: loopControlRaw.control_readback_source ? String(loopControlRaw.control_readback_source) : undefined,
      token_omitted: loopControlRaw.token_omitted === undefined ? undefined : boolValue(loopControlRaw.token_omitted),
    },
    risks: asArray<Record<string, unknown>>(raw.risks).map((item) => ({
      id: String(item.id || ""),
      severity: String(item.severity || "attention"),
      summary: item.summary ? String(item.summary) : undefined,
      next_action: item.next_action ? String(item.next_action) : undefined,
      action_id: item.action_id ? String(item.action_id) : undefined,
      action_signature: item.action_signature ? String(item.action_signature) : undefined,
      action_command: item.action_command ? String(item.action_command) : undefined,
      verify_command: item.verify_command ? String(item.verify_command) : undefined,
      receipt_record_command: item.receipt_record_command ? String(item.receipt_record_command) : undefined,
      receipt_verify_record_command: item.receipt_verify_record_command ? String(item.receipt_verify_record_command) : undefined,
      receipt_required: item.receipt_required === undefined ? undefined : boolValue(item.receipt_required),
      token_omitted: item.token_omitted === undefined ? undefined : boolValue(item.token_omitted),
    })).filter((item) => item.id),
    next_actions: asArray<unknown>(raw.next_actions).map(String).filter(Boolean),
    sources: typeof raw.sources === "object" && raw.sources !== null ? raw.sources as Record<string, unknown> : undefined,
    auth: {
      mode: String(authRaw.mode || "unknown"),
      scoped: boolValue(authRaw.scoped),
      required_scope: String(authRaw.required_scope || "tasks:read"),
      workspace_id: String(authRaw.workspace_id || "local-demo"),
      agent_id: authRaw.agent_id ? String(authRaw.agent_id) : null,
      token_omitted: authRaw.token_omitted === undefined ? undefined : boolValue(authRaw.token_omitted),
    },
    contract: raw.contract ? String(raw.contract) : undefined,
    safety: {
      read_only: boolValue(safetyRaw.read_only),
      ledger_mutated: boolValue(safetyRaw.ledger_mutated),
      live_execution_performed: boolValue(safetyRaw.live_execution_performed),
      raw_prompt_omitted: boolValue(safetyRaw.raw_prompt_omitted),
      raw_response_omitted: boolValue(safetyRaw.raw_response_omitted),
      token_omitted: boolValue(safetyRaw.token_omitted),
    },
    token_omitted: boolValue(raw.token_omitted),
    live_execution_performed: boolValue(raw.live_execution_performed),
  };
}

export async function loadWorkerAdapterReadiness(): Promise<WorkerAdapterReadinessPayload> {
  const raw = await apiJson<Record<string, unknown>>("/workers/adapter-readiness");
  const adaptersRaw = typeof raw.adapters === "object" && raw.adapters !== null ? raw.adapters as Record<string, unknown> : {};
  const normalizeAdapter = (name: WorkerReadinessAdapterName): WorkerAdapterReadinessItem => {
    const item = typeof adaptersRaw[name] === "object" && adaptersRaw[name] !== null ? adaptersRaw[name] as Record<string, unknown> : {};
    const remediationRaw = typeof item.remediation === "object" && item.remediation !== null ? item.remediation as Record<string, unknown> : {};
    const remediationSafetyRaw = typeof remediationRaw.safety === "object" && remediationRaw.safety !== null ? remediationRaw.safety as Record<string, unknown> : {};
    const remediationCommands = asArray<Record<string, unknown>>(remediationRaw.commands);
    return {
      adapter: name,
      ok: boolValue(item.ok),
      readiness: String(item.readiness || "unavailable"),
      connector_id: item.connector_id ? String(item.connector_id) : null,
      trust_status: item.trust_status ? String(item.trust_status) : undefined,
      observation_level: item.observation_level ? String(item.observation_level) : undefined,
      capability_policy_hash: item.capability_policy_hash ? String(item.capability_policy_hash) : null,
      capability_manifest: typeof item.capability_manifest === "object" && item.capability_manifest !== null ? item.capability_manifest as Record<string, unknown> : {},
      risk_floor: item.risk_floor ? String(item.risk_floor) : undefined,
      commercial_readiness: item.commercial_readiness ? String(item.commercial_readiness) : undefined,
      requires_confirm_run: boolValue(item.requires_confirm_run),
      workspace_write_ready: boolValue(item.workspace_write_ready),
      client_plugin: typeof item.client_plugin === "object" && item.client_plugin !== null
        ? item.client_plugin as WorkerAdapterReadinessItem["client_plugin"]
        : undefined,
      target_resource: item.target_resource ? String(item.target_resource) : null,
      checks: typeof item.checks === "object" && item.checks !== null ? item.checks as Record<string, unknown> : {},
      recommended_action: item.recommended_action ? String(item.recommended_action) : undefined,
      last_error: item.last_error ? String(item.last_error) : null,
      remediation: {
        status: remediationRaw.status ? String(remediationRaw.status) : undefined,
        primary_next_action: remediationRaw.primary_next_action ? String(remediationRaw.primary_next_action) : undefined,
        missing: asArray<unknown>(remediationRaw.missing).map(String).filter(Boolean),
        commands: remediationCommands.map(command => ({
          phase: command.phase ? String(command.phase) : undefined,
          command: command.command ? String(command.command) : undefined,
          mutating: boolValue(command.mutating),
          confirm_required: boolValue(command.confirm_required),
        })).filter(command => command.command),
        safety: {
          read_only: boolValue(remediationSafetyRaw.read_only),
          ledger_mutated: boolValue(remediationSafetyRaw.ledger_mutated),
          live_execution_performed: boolValue(remediationSafetyRaw.live_execution_performed),
          server_executes_shell: boolValue(remediationSafetyRaw.server_executes_shell),
          token_omitted: boolValue(remediationSafetyRaw.token_omitted),
        },
        token_omitted: boolValue(remediationRaw.token_omitted),
      },
      token_omitted: boolValue(item.token_omitted),
    };
  };
  return {
    provider: String(raw.provider || "agentops-worker"),
    status: String(raw.status || "unknown"),
    summary: typeof raw.summary === "object" && raw.summary !== null ? raw.summary as WorkerAdapterReadinessSummary : {},
    adapters: {
      mock: normalizeAdapter("mock"),
      codex: normalizeAdapter("codex"),
      hermes: normalizeAdapter("hermes"),
      openclaw: normalizeAdapter("openclaw"),
    },
    contract: raw.contract ? String(raw.contract) : undefined,
    live_execution_performed: boolValue(raw.live_execution_performed),
    token_omitted: boolValue(raw.token_omitted),
  };
}

export async function dispatchLocalWorkerOnce(input: {
  adapter: "mock" | "hermes" | "openclaw";
  confirm_run?: boolean;
  title?: string;
  description?: string;
  acceptance_criteria?: string;
}): Promise<WorkerDispatchResult> {
  return apiJson<WorkerDispatchResult>("/workers/local/dispatch-once", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

function normalizeWorkerDaemon(row: Record<string, unknown>): WorkerDaemonStatus {
  return {
    adapter: String(row.adapter || "mock") as WorkerDaemonStatus["adapter"],
    status: String(row.status || "not_started"),
    running: boolValue(row.running),
    pid: row.pid ? numberValue(row.pid, 0) : null,
    agent_id: row.agent_id ? String(row.agent_id) : null,
    started_at: row.started_at ? String(row.started_at) : null,
    stopped_at: row.stopped_at ? String(row.stopped_at) : null,
    poll_interval: row.poll_interval ? numberValue(row.poll_interval, 0) : null,
    max_tasks: row.max_tasks === undefined || row.max_tasks === null ? null : numberValue(row.max_tasks, 0),
    confirm_run: boolValue(row.confirm_run),
    management_mode: row.management_mode ? String(row.management_mode) : undefined,
    control_allowed: row.control_allowed === undefined ? undefined : boolValue(row.control_allowed),
    process_source: row.process_source ? String(row.process_source) : undefined,
    process_claim_active: row.process_claim_active === undefined ? undefined : boolValue(row.process_claim_active),
    host_management_claim_active: row.host_management_claim_active === undefined ? undefined : boolValue(row.host_management_claim_active),
    process_identity_status: row.process_identity_status ? String(row.process_identity_status) : undefined,
    process_identity_verified: row.process_identity_verified === undefined ? undefined : boolValue(row.process_identity_verified),
    log_path: row.log_path ? String(row.log_path) : undefined,
    state_path: row.state_path ? String(row.state_path) : undefined,
    worker_status: row.worker_status ? String(row.worker_status) : null,
    state_updated_at: row.state_updated_at ? String(row.state_updated_at) : null,
    processed: numberValue(row.processed, 0),
    iterations: numberValue(row.iterations, 0),
    total_errors: numberValue(row.total_errors, 0),
    consecutive_errors: numberValue(row.consecutive_errors, 0),
    last_sleep_reason: row.last_sleep_reason ? String(row.last_sleep_reason) : null,
    last_sleep_sec: row.last_sleep_sec === undefined || row.last_sleep_sec === null ? null : numberValue(row.last_sleep_sec, 0),
    continue_on_error: boolValue(row.continue_on_error),
    last_error: (row.last_error || null) as Record<string, unknown> | null,
    last_result: (row.last_result || null) as Record<string, unknown> | null,
    log_tail: asArray(row.log_tail).map(String),
  };
}

function numberRecord(value: unknown): Record<string, number> {
  const source = typeof value === "object" && value !== null ? value as Record<string, unknown> : {};
  return Object.fromEntries(Object.entries(source).map(([key, count]) => [key, numberValue(count, 0)]));
}

function normalizeWorkerRemoteHealth(row: Record<string, unknown>): WorkerRemoteHealth {
  return {
    status: row.status ? String(row.status) : undefined,
    remote_worker_count: numberValue(row.remote_worker_count, 0),
    total_remote_enrollments: numberValue(row.total_remote_enrollments, 0),
    active_enrollments: numberValue(row.active_enrollments, 0),
    fresh_enrollments: numberValue(row.fresh_enrollments, 0),
    stale_enrollments: numberValue(row.stale_enrollments, 0),
    never_seen_enrollments: numberValue(row.never_seen_enrollments, 0),
    active_sessions: numberValue(row.active_sessions, 0),
    expired_sessions: numberValue(row.expired_sessions, 0),
    revoked_sessions: numberValue(row.revoked_sessions, 0),
    heartbeat_state_counts: numberRecord(row.heartbeat_state_counts),
    token_status_counts: numberRecord(row.token_status_counts),
    session_state_counts: numberRecord(row.session_state_counts),
    remote_workers: asArray<Record<string, unknown>>(row.remote_workers).map((item) => ({
      token_ref: item.token_ref ? String(item.token_ref) : undefined,
      token_id_omitted: boolValue(item.token_id_omitted),
      workspace_id: item.workspace_id ? String(item.workspace_id) : undefined,
      agent_id: item.agent_id ? String(item.agent_id) : undefined,
      agent_name: item.agent_name ? String(item.agent_name) : undefined,
      runtime_type: item.runtime_type ? String(item.runtime_type) : undefined,
      agent_status: item.agent_status ? String(item.agent_status) : null,
      token_status: item.token_status ? String(item.token_status) : undefined,
      heartbeat_state: item.heartbeat_state ? String(item.heartbeat_state) : undefined,
      heartbeat_timeout_sec: numberValue(item.heartbeat_timeout_sec, 0),
      last_heartbeat_at: item.last_heartbeat_at ? String(item.last_heartbeat_at) : null,
      last_used_at: item.last_used_at ? String(item.last_used_at) : null,
      expires_at: item.expires_at ? String(item.expires_at) : null,
      scope_count: numberValue(item.scope_count, 0),
      active_session_count: numberValue(item.active_session_count, 0),
    })),
    recent_sessions: asArray<Record<string, unknown>>(row.recent_sessions).map((item) => ({
      session_ref: item.session_ref ? String(item.session_ref) : undefined,
      session_id_omitted: boolValue(item.session_id_omitted),
      parent_token_ref: item.parent_token_ref ? String(item.parent_token_ref) : undefined,
      workspace_id: item.workspace_id ? String(item.workspace_id) : undefined,
      agent_id: item.agent_id ? String(item.agent_id) : undefined,
      status: item.status ? String(item.status) : undefined,
      session_state: item.session_state ? String(item.session_state) : undefined,
      created_at: item.created_at ? String(item.created_at) : undefined,
      expires_at: item.expires_at ? String(item.expires_at) : undefined,
      last_used_at: item.last_used_at ? String(item.last_used_at) : null,
      scope_count: numberValue(item.scope_count, 0),
    })),
    token_omitted: boolValue(row.token_omitted),
  };
}

function normalizeWorkerFleetHealth(row: Record<string, unknown>): WorkerFleetHealth | undefined {
  if (Object.keys(row).length === 0) return undefined;
  return {
    overall: String(row.overall || "attention"),
    contract: row.contract ? String(row.contract) : undefined,
    gates: asArray<Record<string, unknown>>(row.gates).map((gate) => ({
      id: String(gate.id || ""),
      status: String(gate.status || "info"),
      summary: String(gate.summary || ""),
      action: gate.action ? String(gate.action) : undefined,
    })).filter((gate) => gate.id || gate.summary),
    recommended_actions: asArray(row.recommended_actions).map(String),
    remote_status: row.remote_status ? String(row.remote_status) : undefined,
    token_omitted: boolValue(row.token_omitted),
  };
}

function normalizeAgentGatewayEnrollment(row: Record<string, unknown>): AgentGatewayEnrollment {
  return {
    token_id: row.token_id ? String(row.token_id) : undefined,
    token_ref: row.token_ref ? String(row.token_ref) : undefined,
    token_id_omitted: row.token_id_omitted === undefined ? undefined : boolValue(row.token_id_omitted),
    workspace_id: String(row.workspace_id || ""),
    agent_id: String(row.agent_id || ""),
    scopes: parseJsonArray(row.scopes),
    status: String(row.status || "unknown"),
    label: String(row.label || ""),
    heartbeat_timeout_sec: numberValue(row.heartbeat_timeout_sec, 0),
    created_at: String(row.created_at || ""),
    expires_at: String(row.expires_at || ""),
    revoked_at: row.revoked_at ? String(row.revoked_at) : null,
    last_used_at: row.last_used_at ? String(row.last_used_at) : null,
    last_heartbeat_at: row.last_heartbeat_at ? String(row.last_heartbeat_at) : null,
    heartbeat_state: String(row.heartbeat_state || "never_seen"),
  };
}

function normalizeAgentGatewaySession(row: Record<string, unknown>): AgentGatewaySession {
  return {
    session_id: row.session_id ? String(row.session_id) : undefined,
    session_ref: row.session_ref ? String(row.session_ref) : undefined,
    session_id_omitted: row.session_id_omitted === undefined ? undefined : boolValue(row.session_id_omitted),
    parent_token_id: row.parent_token_id ? String(row.parent_token_id) : null,
    parent_token_ref: row.parent_token_ref ? String(row.parent_token_ref) : null,
    workspace_id: String(row.workspace_id || ""),
    agent_id: String(row.agent_id || ""),
    scopes: parseJsonArray(row.scopes),
    status: String(row.status || "unknown"),
    session_state: String(row.session_state || row.status || "unknown"),
    created_at: String(row.created_at || ""),
    expires_at: String(row.expires_at || ""),
    revoked_at: row.revoked_at ? String(row.revoked_at) : null,
    last_used_at: row.last_used_at ? String(row.last_used_at) : null,
  };
}

function normalizeAgentGatewayStatus(row: Record<string, unknown>): AgentGatewayStatusPayload {
  const auth = (row.auth || {}) as Record<string, unknown>;
  return {
    provider: String(row.provider || "agent_gateway"),
    status: String(row.status || "unknown"),
    auth: {
      mode: String(auth.mode || "unknown"),
      authenticated: boolValue(auth.authenticated),
      agent_id: String(auth.agent_id || ""),
      workspace_id: String(auth.workspace_id || ""),
      scopes: parseJsonArray(auth.scopes),
      token_id: auth.token_id ? String(auth.token_id) : undefined,
      token_status: auth.token_status ? String(auth.token_status) : undefined,
      heartbeat_state: auth.heartbeat_state ? String(auth.heartbeat_state) : undefined,
      heartbeat_timeout_sec: auth.heartbeat_timeout_sec === undefined ? undefined : numberValue(auth.heartbeat_timeout_sec, 0),
      expires_at: auth.expires_at ? String(auth.expires_at) : undefined,
      last_used_at: auth.last_used_at ? String(auth.last_used_at) : null,
      last_heartbeat_at: auth.last_heartbeat_at ? String(auth.last_heartbeat_at) : null,
      session_id: auth.session_id ? String(auth.session_id) : undefined,
      parent_token_id: auth.parent_token_id ? String(auth.parent_token_id) : undefined,
      session_expires_at: auth.session_expires_at ? String(auth.session_expires_at) : undefined,
    },
    valid_scopes: asArray(row.valid_scopes).map(String),
    token_omitted: boolValue(row.token_omitted),
  };
}

export async function startLocalWorkerDaemon(input: {
  adapter: "mock" | "hermes" | "openclaw";
  confirm_run?: boolean;
  poll_interval?: number;
  max_tasks?: number;
}): Promise<WorkerDaemonResult> {
  return apiJsonWithStatuses<WorkerDaemonResult>("/workers/local/start", {
    method: "POST",
    body: JSON.stringify(input),
  }, [200, 201, 409]);
}

export async function stopLocalWorkerDaemon(adapter?: "mock" | "hermes" | "openclaw" | "all"): Promise<WorkerDaemonResult> {
  return apiJson<WorkerDaemonResult>("/workers/local/stop", {
    method: "POST",
    body: JSON.stringify({ adapter: adapter || "all" }),
  });
}

export async function restartLocalWorkerDaemon(input: {
  adapter: "mock" | "hermes" | "openclaw";
  confirm_run?: boolean;
  poll_interval?: number;
  max_tasks?: number;
}): Promise<WorkerDaemonResult> {
  return apiJsonWithStatuses<WorkerDaemonResult>("/workers/local/restart", {
    method: "POST",
    body: JSON.stringify(input),
  }, [200, 201, 409]);
}

export async function loadWorkerDaemonLogs(adapter: "mock" | "hermes" | "openclaw"): Promise<WorkerDaemonLogPayload> {
  const raw = await optionalApiJson<Record<string, unknown>>(`/workers/local/logs?adapter=${encodeURIComponent(adapter)}`, {
    provider: "agentops-worker",
    daemon: {
      adapter,
      status: "unavailable",
      running: false,
      log_tail: [],
    },
  });
  return {
    provider: String(raw.provider || "agentops-worker"),
    daemon: normalizeWorkerDaemon((raw.daemon || {}) as Record<string, unknown>),
  };
}

export async function releaseWorkerTask(input: { task_id: string; reason?: string; force?: boolean }): Promise<WorkerTaskReleaseResult> {
  const raw = await apiJson<Record<string, unknown>>("/workers/tasks/release", {
    method: "POST",
    body: JSON.stringify(input),
  });
  return {
    released: boolValue(raw.released),
    task: normalizeTask((raw.task || {}) as Record<string, unknown>),
    released_runs: asArray(raw.released_runs).map(String),
    token_omitted: boolValue(raw.token_omitted),
    error: raw.error ? String(raw.error) : undefined,
  };
}

export async function loadAgentGatewayEnrollments(): Promise<AgentGatewayEnrollmentListPayload> {
  const raw = await apiJson<Record<string, unknown>>("/agent-gateway/enrollments");
  return {
    enrollments: asArray<Record<string, unknown>>(raw.enrollments).map(normalizeAgentGatewayEnrollment),
    valid_scopes: asArray(raw.valid_scopes).map(String),
    token_omitted: boolValue(raw.token_omitted),
  };
}

export async function loadAgentGatewaySessions(): Promise<AgentGatewaySessionListPayload> {
  const raw = await apiJson<Record<string, unknown>>("/agent-gateway/sessions");
  return {
    sessions: asArray<Record<string, unknown>>(raw.sessions).map(normalizeAgentGatewaySession),
    valid_scopes: asArray(raw.valid_scopes).map(String),
    token_omitted: boolValue(raw.token_omitted),
  };
}

export async function loadAgentGatewayStatus(): Promise<AgentGatewayStatusPayload> {
  const raw = await apiJson<Record<string, unknown>>("/agent-gateway/status");
  return normalizeAgentGatewayStatus(raw);
}

export async function previewAgentGatewayEnrollmentPolicy(input: {
  workspace_id?: string;
  runtime_type?: string;
  scopes: string[];
}): Promise<AgentGatewayEnrollmentPolicyPreview> {
  const raw = await apiJson<Record<string, unknown>>("/agent-gateway/enrollment/policy-preview", {
    method: "POST",
    body: JSON.stringify(input),
  });
  const safetyRaw = typeof raw.safety === "object" && raw.safety !== null ? raw.safety as Record<string, unknown> : {};
  return {
    provider: String(raw.provider || "agent_gateway"),
    operation: String(raw.operation || "enrollment_policy_preview"),
    status: String(raw.status || "unknown"),
    workspace_id: String(raw.workspace_id || ""),
    runtime_type: String(raw.runtime_type || ""),
    deployment_mode: String(raw.deployment_mode || "unknown"),
    production_security_requested: boolValue(raw.production_security_requested),
    admin_key_configured: boolValue(raw.admin_key_configured),
    policy: String(raw.policy || "custom"),
    risk_level: String(raw.risk_level || "unknown"),
    approval_recommended: boolValue(raw.approval_recommended),
    recommended_path: String(raw.recommended_path || ""),
    direct_create_allowed: boolValue(raw.direct_create_allowed),
    approval_request_required: boolValue(raw.approval_request_required),
    deployment_policy_summary: String(raw.deployment_policy_summary || ""),
    scope_count: numberValue(raw.scope_count, 0),
    scopes: asArray(raw.scopes).map(String),
    invalid_scopes: asArray(raw.invalid_scopes).map(String),
    privileged_scopes: asArray(raw.privileged_scopes).map(String),
    worker_write_scopes: asArray(raw.worker_write_scopes).map(String),
    missing_worker_scopes: asArray(raw.missing_worker_scopes).map(String),
    gates: asArray<Record<string, unknown>>(raw.gates).map((gate) => ({
      id: String(gate.id || ""),
      ok: boolValue(gate.ok),
      status: String(gate.status || "unknown"),
      summary: String(gate.summary || ""),
    })).filter((gate) => gate.id || gate.summary),
    next_actions: asArray(raw.next_actions).map(String).filter(Boolean),
    safety: {
      read_only: boolValue(safetyRaw.read_only),
      ledger_mutated: boolValue(safetyRaw.ledger_mutated),
      live_execution_performed: boolValue(safetyRaw.live_execution_performed),
      token_omitted: boolValue(safetyRaw.token_omitted),
      raw_prompt_omitted: boolValue(safetyRaw.raw_prompt_omitted),
    },
    token_omitted: boolValue(raw.token_omitted),
    live_execution_performed: boolValue(raw.live_execution_performed),
  };
}

export async function loadSecurityProductionReadiness(): Promise<SecurityProductionReadinessPayload> {
  const raw = await optionalApiJson<Record<string, unknown>>("/security/production-readiness", {
    provider: "agentops-security",
    operation: "production_readiness",
    status: "unavailable",
    production_ready: false,
    production_requested: false,
    deployment_mode: "unknown",
    auth_mode: "unknown",
    gateway_status_code: 0,
    gates: [],
    next_actions: ["agentops security production-readiness"],
    safety: {
      read_only: true,
      live_execution_performed: false,
      token_omitted: true,
      raw_prompt_omitted: true,
    },
    token_omitted: true,
    live_execution_performed: false,
  });
  const safetyRaw = typeof raw.safety === "object" && raw.safety !== null ? raw.safety as Record<string, unknown> : {};
  const startupRaw = typeof raw.startup_security === "object" && raw.startup_security !== null ? raw.startup_security as Record<string, unknown> : {};
  return {
    provider: String(raw.provider || "agentops-security"),
    operation: String(raw.operation || "production_readiness"),
    status: String(raw.status || "unknown"),
    production_ready: boolValue(raw.production_ready),
    production_requested: boolValue(raw.production_requested),
    deployment_mode: String(raw.deployment_mode || "unknown"),
    startup_security: {
      ok: boolValue(startupRaw.ok),
      status: String(startupRaw.status || "unknown"),
      host: String(startupRaw.host || ""),
      deployment_mode: String(startupRaw.deployment_mode || raw.deployment_mode || "unknown"),
      non_loopback: boolValue(startupRaw.non_loopback),
      production_requested: boolValue(startupRaw.production_requested),
      allow_non_loopback: boolValue(startupRaw.allow_non_loopback),
      api_key_configured: boolValue(startupRaw.api_key_configured),
      admin_key_configured: boolValue(startupRaw.admin_key_configured),
      failures: asArray<Record<string, unknown>>(startupRaw.failures).map((item) => ({
        id: String(item.id || ""),
        message: String(item.message || ""),
      })).filter((item) => item.id || item.message),
      warnings: asArray<Record<string, unknown>>(startupRaw.warnings).map((item) => ({
        id: String(item.id || ""),
        message: String(item.message || ""),
      })).filter((item) => item.id || item.message),
      contract: String(startupRaw.contract || ""),
      token_omitted: boolValue(startupRaw.token_omitted),
    },
    auth_mode: String(raw.auth_mode || "unknown"),
    gateway_status_code: numberValue(raw.gateway_status_code, 0),
    gates: asArray<Record<string, unknown>>(raw.gates).map((gate) => ({
      id: String(gate.id || ""),
      label: String(gate.label || gate.id || ""),
      status: String(gate.status || "unknown"),
      ok: boolValue(gate.ok),
      detail: String(gate.detail || ""),
      next_action: String(gate.next_action || ""),
    })).filter((gate) => gate.id || gate.label),
    next_actions: asArray<unknown>(raw.next_actions).map(String).filter(Boolean),
    contract: raw.contract ? String(raw.contract) : undefined,
    safety: {
      read_only: boolValue(safetyRaw.read_only),
      live_execution_performed: boolValue(safetyRaw.live_execution_performed),
      token_omitted: boolValue(safetyRaw.token_omitted),
      raw_prompt_omitted: boolValue(safetyRaw.raw_prompt_omitted),
    },
    token_omitted: boolValue(raw.token_omitted),
    live_execution_performed: boolValue(raw.live_execution_performed),
  };
}

export async function loadDemoReadiness(): Promise<DemoReadinessPayload> {
  const raw = await optionalApiJson<Record<string, unknown>>("/demo/readiness", {
    provider: "agentops-demo",
    operation: "v1_5_demo_readiness",
    status: "unavailable",
    demo_ready: false,
    production_ready: false,
    summary: {},
    shots: [],
    next_actions: ["agentops demo readiness"],
    safety: {
      read_only: true,
      ledger_mutated: false,
      live_execution_performed: false,
      token_omitted: true,
      raw_prompt_omitted: true,
    },
    token_omitted: true,
    live_execution_performed: false,
  });
  const summaryRaw = typeof raw.summary === "object" && raw.summary !== null ? raw.summary as Record<string, unknown> : {};
  const safetyRaw = typeof raw.safety === "object" && raw.safety !== null ? raw.safety as Record<string, unknown> : {};
  const productEvidenceRaw = typeof raw.product_evidence_packet === "object" && raw.product_evidence_packet !== null
    ? raw.product_evidence_packet as Record<string, unknown>
    : undefined;
  const productEvidenceSummaryRaw = productEvidenceRaw && typeof productEvidenceRaw.summary === "object" && productEvidenceRaw.summary !== null
    ? productEvidenceRaw.summary as Record<string, unknown>
    : {};
  const productEvidenceSafetyRaw = productEvidenceRaw && typeof productEvidenceRaw.safety === "object" && productEvidenceRaw.safety !== null
    ? productEvidenceRaw.safety as Record<string, unknown>
    : {};
  return {
    provider: String(raw.provider || "agentops-demo"),
    operation: String(raw.operation || "v1_5_demo_readiness"),
    status: String(raw.status || "unknown"),
    demo_ready: boolValue(raw.demo_ready),
    production_ready: boolValue(raw.production_ready),
    summary: {
      shot_count: numberValue(summaryRaw.shot_count, 0),
      ready_shots: numberValue(summaryRaw.ready_shots, 0),
      blocker_count: numberValue(summaryRaw.blocker_count, 0),
      warning_count: numberValue(summaryRaw.warning_count, 0),
      closed_loop_runs: numberValue(summaryRaw.closed_loop_runs, 0),
      customer_worker_artifacts: numberValue(summaryRaw.customer_worker_artifacts, 0),
      fleet_lanes: numberValue(summaryRaw.fleet_lanes, 0),
      ready_inbox_items: numberValue(summaryRaw.ready_inbox_items, 0),
    },
    shots: asArray<Record<string, unknown>>(raw.shots).map((shot) => ({
      id: String(shot.id || ""),
      label: String(shot.label || shot.id || ""),
      route: shot.route ? String(shot.route) : undefined,
      command: shot.command ? String(shot.command) : undefined,
      status: String(shot.status || "unknown"),
      ok: boolValue(shot.ok),
      detail: String(shot.detail || ""),
      next_action: String(shot.next_action || ""),
    })).filter((shot) => shot.id || shot.label),
    next_actions: asArray<unknown>(raw.next_actions).map(String).filter(Boolean),
    references: typeof raw.references === "object" && raw.references !== null ? raw.references as Record<string, string> : undefined,
    product_evidence_packet: productEvidenceRaw ? {
      id: String(productEvidenceRaw.id || ""),
      operation: String(productEvidenceRaw.operation || "product_evidence_packet"),
      status: String(productEvidenceRaw.status || "unknown"),
      summary: {
        phase_count: numberValue(productEvidenceSummaryRaw.phase_count, 0),
        manual_live_phase_count: numberValue(productEvidenceSummaryRaw.manual_live_phase_count, 0),
        isolated_db_phase_count: numberValue(productEvidenceSummaryRaw.isolated_db_phase_count, 0),
        copyable_command_count: numberValue(productEvidenceSummaryRaw.copyable_command_count, 0),
      },
      phases: asArray<Record<string, unknown>>(productEvidenceRaw.phases).map((phase) => ({
        id: String(phase.id || ""),
        label: String(phase.label || phase.id || ""),
        command: String(phase.command || ""),
        route: phase.route ? String(phase.route) : undefined,
        manual_only: boolValue(phase.manual_only),
        requires_confirm_live: boolValue(phase.requires_confirm_live),
        requires_isolated_db: boolValue(phase.requires_isolated_db),
        summary: String(phase.summary || ""),
      })).filter((phase) => phase.id || phase.label || phase.command),
      references: typeof productEvidenceRaw.references === "object" && productEvidenceRaw.references !== null ? productEvidenceRaw.references as Record<string, string> : undefined,
      contract: productEvidenceRaw.contract ? String(productEvidenceRaw.contract) : undefined,
      safety: {
        read_only: boolValue(productEvidenceSafetyRaw.read_only),
        ledger_mutated: boolValue(productEvidenceSafetyRaw.ledger_mutated),
        live_execution_performed: boolValue(productEvidenceSafetyRaw.live_execution_performed),
        token_omitted: boolValue(productEvidenceSafetyRaw.token_omitted),
        raw_prompt_omitted: boolValue(productEvidenceSafetyRaw.raw_prompt_omitted),
        requires_confirm_live: boolValue(productEvidenceSafetyRaw.requires_confirm_live),
        requires_isolated_db_for_live: boolValue(productEvidenceSafetyRaw.requires_isolated_db_for_live),
      },
      token_omitted: boolValue(productEvidenceRaw.token_omitted),
      live_execution_performed: boolValue(productEvidenceRaw.live_execution_performed),
    } : undefined,
    contract: raw.contract ? String(raw.contract) : undefined,
    safety: {
      read_only: boolValue(safetyRaw.read_only),
      ledger_mutated: boolValue(safetyRaw.ledger_mutated),
      live_execution_performed: boolValue(safetyRaw.live_execution_performed),
      token_omitted: boolValue(safetyRaw.token_omitted),
      raw_prompt_omitted: boolValue(safetyRaw.raw_prompt_omitted),
    },
    token_omitted: boolValue(raw.token_omitted),
    live_execution_performed: boolValue(raw.live_execution_performed),
  };
}

export async function createAgentGatewayEnrollment(input: AgentGatewayEnrollmentCreateInput): Promise<AgentGatewayEnrollmentCreateResult> {
  return apiJson<AgentGatewayEnrollmentCreateResult>("/agent-gateway/enrollment/create", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function requestAgentGatewayEnrollment(input: AgentGatewayEnrollmentCreateInput & { reason?: string }): Promise<AgentGatewayEnrollmentRequestResult> {
  return apiJson<AgentGatewayEnrollmentRequestResult>("/agent-gateway/enrollment/request", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function issueApprovedAgentGatewayEnrollment(input: {
  request_id?: string;
  approval_id?: string;
  ttl_days?: number;
  heartbeat_timeout_sec?: number;
  label?: string;
}): Promise<AgentGatewayEnrollmentCreateResult & { issued_from_request_id?: string; approval_id?: string }> {
  return apiJson<AgentGatewayEnrollmentCreateResult & { issued_from_request_id?: string; approval_id?: string }>("/agent-gateway/enrollment/issue-approved", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function revokeAgentGatewayEnrollment(input: { token_id?: string; agent_id?: string }): Promise<AgentGatewayEnrollmentRevokeResult> {
  return apiJson<AgentGatewayEnrollmentRevokeResult>("/agent-gateway/enrollment/revoke", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function revokeAgentGatewaySession(input: { session_id?: string; agent_id?: string }): Promise<AgentGatewaySessionRevokeResult> {
  return apiJson<AgentGatewaySessionRevokeResult>("/agent-gateway/session/revoke", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function rotateAgentGatewayEnrollment(input: {
  token_id?: string;
  agent_id?: string;
  scopes?: string[];
  ttl_days?: number;
  heartbeat_timeout_sec?: number;
  label?: string;
}): Promise<AgentGatewayEnrollmentRotateResult> {
  return apiJson<AgentGatewayEnrollmentRotateResult>("/agent-gateway/enrollment/rotate", {
    method: "POST",
    body: JSON.stringify(input),
  });
}
