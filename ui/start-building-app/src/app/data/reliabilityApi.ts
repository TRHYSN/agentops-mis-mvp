import { apiJson } from "./liveApi";

export type ReliabilityJson =
  | string
  | number
  | boolean
  | null
  | ReliabilityJson[]
  | { [key: string]: ReliabilityJson };

export interface ReliabilitySafety {
  read_only: boolean;
  ledger_mutated: boolean;
  live_execution_performed: boolean;
  token_omitted: boolean;
  raw_hidden_prompts_omitted: boolean;
}

interface ReliabilityEnvelope {
  schema_version: "open_cekura.reliability_api.v1";
  provider: "open-cekura";
  operation: string;
  workspace_id: string;
  safety: ReliabilitySafety;
  token_omitted: true;
}

export interface ReliabilityPage {
  limit: number;
  offset: number;
  returned: number;
  has_more: boolean;
  next_offset: number | null;
}

export interface ReliabilityAgentVersion {
  workspace_id: string;
  agent_version_id: string;
  schema_version: number;
  agent_id: string;
  version: string;
  adapter_kind: "mock" | "http" | string;
  config_sha256: string;
  created_at: string;
}

export interface ReliabilityAgent {
  workspace_id: string;
  agent_id: string;
  schema_version: number;
  name: string;
  description: string;
  created_at: string;
  version_count: number;
  versions?: ReliabilityAgentVersion[];
  returned_version_count?: number;
  versions_truncated?: boolean;
}

export interface ReliabilityScenarioContract {
  schema_version: number;
  id: string;
  name: string;
  persona: Record<string, ReliabilityJson>;
  initial_message: string;
  goal: Record<string, ReliabilityJson>;
  challenges: ReliabilityJson[];
  expectations: Record<string, ReliabilityJson>;
  tags: string[];
}

export interface ReliabilityScenario {
  workspace_id: string;
  scenario_id: string;
  schema_version: number;
  suite_id: string;
  persona_id: string;
  name: string;
  initial_message: string;
  goal_type: string;
  source_sha256: string;
  created_at: string;
  contract: ReliabilityScenarioContract;
}

export interface ReliabilityScenarioSuite {
  workspace_id: string;
  suite_id: string;
  schema_version: number;
  name: string;
  description: string;
  scenario_count: number;
  created_at: string;
  scenarios?: ReliabilityScenario[];
}

export interface ReliabilityCampaign {
  workspace_id: string;
  campaign_id: string;
  schema_version: number;
  agent_version_id: string;
  scenario_suite_id: string;
  status: string;
  mis_task_id: string | null;
  mis_plan_id: string | null;
  run_count: number;
  created_at: string;
}

export interface ReliabilityRun {
  workspace_id: string;
  run_id: string;
  schema_version: number;
  campaign_id: string;
  scenario_id: string;
  agent_version_id: string;
  status: string;
  mis_run_id: string | null;
  created_at: string;
  turn_count?: number;
  tool_call_count?: number;
  evaluation_count?: number;
}

export interface ReliabilityTurn {
  workspace_id: string;
  turn_id: string;
  schema_version: number;
  run_id: string;
  turn_index: number;
  role: "system" | "user" | "assistant" | "tool" | string;
  content: string;
  created_at: string;
}

export interface ReliabilityToolCall {
  workspace_id: string;
  tool_call_id: string;
  schema_version: number;
  run_id: string;
  turn_id: string;
  name: string;
  arguments: ReliabilityJson;
  result: ReliabilityJson;
  error: string | null;
  is_mutation: boolean;
  duration_ms: number;
  mis_tool_call_id: string | null;
  created_at: string;
}

export interface ReliabilityEvaluation {
  workspace_id: string;
  evaluation_id: string;
  schema_version: number;
  run_id: string;
  evaluator_id: string;
  status: string;
  score: number | null;
  threshold: number | null;
  reason_codes: string[];
  evidence_refs: string[];
  metadata: Record<string, ReliabilityJson>;
  mis_evaluation_id: string | null;
  created_at: string;
}

export interface ReliabilityFailure {
  workspace_id: string;
  failure_id: string;
  schema_version: number;
  run_id: string;
  scenario_id: string;
  evaluation_result_id: string;
  reason_code: string;
  expected: ReliabilityJson;
  observed: ReliabilityJson;
  evidence_refs: string[];
  created_at: string;
}

export interface ReliabilityRegression {
  workspace_id: string;
  regression_id: string;
  schema_version: number;
  failure_case_id: string;
  scenario_id: string;
  source_run_id: string;
  name: string;
  original_input: Record<string, ReliabilityJson>;
  expected: ReliabilityJson;
  observed: ReliabilityJson;
  reason_code: string;
  evaluator_id: string;
  evidence_refs: string[];
  mis_memory_id: string | null;
  created_at: string;
}

export interface ReliabilityGateFact {
  rule_id: string;
  message: string;
  measured_value: ReliabilityJson;
  threshold: ReliabilityJson;
  evidence_refs: string[];
  scenario_id?: string | null;
  run_id?: string | null;
  evaluation_result_id?: string | null;
}

export interface ReliabilityReleaseGate {
  workspace_id: string;
  gate_id: string;
  schema_version: number;
  campaign_id: string;
  baseline_campaign_id: string | null;
  decision: "pass" | "warn" | "block" | string;
  policy_version: string;
  blockers: ReliabilityGateFact[];
  warnings: ReliabilityGateFact[];
  metrics: Record<string, ReliabilityJson>;
  evidence_refs: string[];
  mis_approval_id: string | null;
  created_at: string;
}

export interface ReliabilityEvidenceManifest {
  workspace_id: string;
  manifest_id: string;
  schema_version: number;
  campaign_id: string;
  run_id: string;
  mis_artifact_id: string | null;
  mis_plan_evidence_manifest_id: string | null;
  git_commit_sha: string;
  environment: {
    os?: string;
    python_version?: string;
    node_version?: string;
  };
  scenario_sha256: string;
  agent_config_sha256: string;
  evaluator_versions: string[];
  artifacts: Record<string, string>;
  started_at: string;
  finished_at: string;
  final_state: string;
  created_at: string;
}

export interface ReliabilityMisLinks {
  task_id: string | null;
  plan_id: string | null;
  run_id: string | null;
  tool_call_ids: string[];
  evaluation_ids: string[];
  artifact_ids: string[];
  approval_ids: string[];
  memory_ids: string[];
}

export interface ReliabilityRunDetail {
  run: ReliabilityRun;
  turns: ReliabilityTurn[];
  tool_calls: ReliabilityToolCall[];
  evaluations: ReliabilityEvaluation[];
  failures: ReliabilityFailure[];
  regressions: ReliabilityRegression[];
  release_gates: ReliabilityReleaseGate[];
  manifests: ReliabilityEvidenceManifest[];
  collection_limits: {
    max_items_per_collection: number;
    truncated: string[];
  };
  mis_links: ReliabilityMisLinks;
}

export interface ReliabilityOverview {
  counts: Record<"agents" | "scenario_suites" | "campaigns" | "runs" | "failures" | "regressions" | "release_gates", number>;
  run_status_counts: Record<string, number>;
  gate_decision_counts: Record<string, number>;
  recent_campaigns: ReliabilityCampaign[];
  recent_release_gates: ReliabilityReleaseGate[];
}

export type ReliabilityOverviewResponse = ReliabilityEnvelope & { overview: ReliabilityOverview };
export type ReliabilityAgentsResponse = ReliabilityEnvelope & { agents: ReliabilityAgent[]; page: ReliabilityPage };
export type ReliabilityScenarioSuitesResponse = ReliabilityEnvelope & { scenario_suites: ReliabilityScenarioSuite[]; page: ReliabilityPage };
export type ReliabilityCampaignsResponse = ReliabilityEnvelope & { campaigns: ReliabilityCampaign[]; page: ReliabilityPage };
export type ReliabilityRunsResponse = ReliabilityEnvelope & { runs: ReliabilityRun[]; page: ReliabilityPage };
export type ReliabilityFailuresResponse = ReliabilityEnvelope & { failures: ReliabilityFailure[]; page: ReliabilityPage };
export type ReliabilityRegressionsResponse = ReliabilityEnvelope & { regressions: ReliabilityRegression[]; page: ReliabilityPage };
export type ReliabilityReleaseGatesResponse = ReliabilityEnvelope & { release_gates: ReliabilityReleaseGate[]; page: ReliabilityPage };
export type ReliabilityCampaignResponse = ReliabilityEnvelope & { campaign: ReliabilityCampaign };
export type ReliabilityRunResponse = ReliabilityEnvelope & {
  run_detail: ReliabilityRunDetail;
  summary: {
    status: string;
    turn_count: number;
    latency_ms: number | null;
    tool_call_count: number;
    evaluator_count: number;
  };
};

const DEFAULT_PAGE = "?limit=100&offset=0";

async function reliabilityRead<T extends ReliabilityEnvelope>(path: string): Promise<T> {
  try {
    const payload = await apiJson<T>(path);
    if (payload.provider !== "open-cekura" || payload.schema_version !== "open_cekura.reliability_api.v1") {
      throw new Error("invalid_reliability_response");
    }
    return payload;
  } catch {
    throw new Error("Reliability evidence is unavailable for this workspace.");
  }
}

export function loadReliabilityOverview(): Promise<ReliabilityOverviewResponse> {
  return reliabilityRead<ReliabilityOverviewResponse>("/reliability/overview");
}

export function loadReliabilityAgents(): Promise<ReliabilityAgentsResponse> {
  return reliabilityRead<ReliabilityAgentsResponse>(`/reliability/agents${DEFAULT_PAGE}`);
}

export function loadReliabilityScenarioSuites(): Promise<ReliabilityScenarioSuitesResponse> {
  return reliabilityRead<ReliabilityScenarioSuitesResponse>(`/reliability/scenario-suites${DEFAULT_PAGE}`);
}

export function loadReliabilityCampaigns(): Promise<ReliabilityCampaignsResponse> {
  return reliabilityRead<ReliabilityCampaignsResponse>(`/reliability/campaigns${DEFAULT_PAGE}`);
}

export function loadReliabilityFailures(): Promise<ReliabilityFailuresResponse> {
  return reliabilityRead<ReliabilityFailuresResponse>(`/reliability/failures${DEFAULT_PAGE}`);
}

export function loadReliabilityRegressions(): Promise<ReliabilityRegressionsResponse> {
  return reliabilityRead<ReliabilityRegressionsResponse>(`/reliability/regressions${DEFAULT_PAGE}`);
}

export function loadReliabilityReleaseGates(): Promise<ReliabilityReleaseGatesResponse> {
  return reliabilityRead<ReliabilityReleaseGatesResponse>(`/reliability/release-gates${DEFAULT_PAGE}`);
}

export function loadReliabilityCampaign(campaignId: string): Promise<ReliabilityCampaignResponse> {
  return reliabilityRead<ReliabilityCampaignResponse>(`/reliability/campaigns/${encodeURIComponent(campaignId)}`);
}

export function loadReliabilityCampaignRuns(campaignId: string): Promise<ReliabilityRunsResponse> {
  return reliabilityRead<ReliabilityRunsResponse>(`/reliability/runs?campaign_id=${encodeURIComponent(campaignId)}&limit=100&offset=0`);
}

export function loadReliabilityCampaignGates(campaignId: string): Promise<ReliabilityReleaseGatesResponse> {
  return reliabilityRead<ReliabilityReleaseGatesResponse>(`/reliability/release-gates?campaign_id=${encodeURIComponent(campaignId)}&limit=100&offset=0`);
}

export function loadReliabilityRun(runId: string): Promise<ReliabilityRunResponse> {
  return reliabilityRead<ReliabilityRunResponse>(`/reliability/runs/${encodeURIComponent(runId)}`);
}
