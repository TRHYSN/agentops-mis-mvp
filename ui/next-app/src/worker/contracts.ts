export const WORKER_METHOD_STEPS = Object.freeze([
  "READ",
  "PLAN",
  "RETRIEVE",
  "COMPARE",
  "EXECUTE",
  "VERIFY",
  "RECORD",
]);

export type CommercialRuntime = "hermes" | "openclaw";
export type RiskLevel = "low" | "medium" | "high" | "critical";

export type GatewayTask = {
  task_id: string;
  title?: string | null;
  description?: string | null;
  acceptance_criteria?: string | null;
  risk_level?: string | null;
  status?: string | null;
  target_resource?: string | null;
  external_action_type?: string | null;
  intake?: Record<string, unknown> | null;
};

export type KnowledgeEvidence = {
  consumed: boolean;
  packetHash: string | null;
  queryHash: string | null;
  status: string;
  retrievalIds: string[];
  paths: string[];
  sourceHashes: string[];
  metrics: Record<string, unknown>;
};

export type PromptProfile = {
  profileId: string;
  version: "worker_prompt_profiles_v1";
  profileHash: string;
  objective: string;
  outputContract: string[];
};

export type PromptBundle = {
  prompt: string;
  promptHash: string;
  profile: PromptProfile;
};

export type RuntimeAdapterResult = {
  ok: boolean;
  runtime: CommercialRuntime;
  modelName: string;
  outputSummary: string;
  rawPayloadHash: string;
  targetResource: string;
  durationMs: number;
  outputTokens: number;
  providerCallPerformed: boolean;
  dryRun: boolean;
  retryable: boolean;
  errorType: string | null;
  errorMessage: string | null;
  attemptCount?: number;
  maxAttempts?: number;
  retryHistory?: Array<{
    attempt: number;
    ok: boolean;
    retryable: boolean;
    errorType: string | null;
  }>;
};

export interface RuntimeAdapter {
  readonly runtime: CommercialRuntime;
  readonly modelName: string;
  execute(bundle: PromptBundle): Promise<RuntimeAdapterResult>;
}

export interface GatewayPort {
  get<T extends Record<string, unknown>>(
    path: string,
    query?: Record<string, string | number | boolean | string[] | undefined>,
  ): Promise<T>;
  post<T extends Record<string, unknown>>(
    path: string,
    body: Record<string, unknown>,
  ): Promise<T>;
}

export type CommercialWorkerConfig = {
  workspaceId: string;
  agentId: string;
  runtime: CommercialRuntime;
  estimatedCostUsd: string;
  taskId?: string;
  statuses?: string[];
  allowHighRisk?: boolean;
  confirmRun: boolean;
  requestCustomerDeliveryApproval?: boolean;
  maxAdapterAttempts?: number;
  retryDelayMs?: number;
};

export type CommercialWorkerReceipt = {
  ok: boolean;
  processed: boolean;
  reason: string;
  runtime: CommercialRuntime;
  task_id?: string;
  run_id?: string;
  plan_id?: string;
  plan_evidence_manifest_id?: string;
  plan_evidence_pass?: boolean;
  customer_delivery_approval_requested?: boolean;
  customer_delivery_approval_id?: string;
  customer_delivery_approval_outcome?: string;
  customer_delivery_approval_control_plane?: string;
  provider_call_performed: boolean;
  dry_run: boolean;
  ledger_evidence_complete?: boolean;
  manual_reconciliation_required?: boolean;
  evidence_failure_stage?: string;
  evidence_failure_code?: string;
  evidence_failure_status?: number;
  output_summary?: string;
  error_type?: string | null;
  attempt_count?: number;
  knowledge_evidence?: {
    consumed: boolean;
    packet_hash: string | null;
    query_hash: string | null;
    status: string;
    result_count: number;
    raw_content_omitted: true;
  };
  external_write_gate?: {
    tool_call_id: string | null;
    approval_id: string | null;
    prepared_action_id: string | null;
    live_execution_performed: false;
  };
  token_omitted: true;
  raw_prompt_omitted: true;
  raw_response_omitted: true;
};
