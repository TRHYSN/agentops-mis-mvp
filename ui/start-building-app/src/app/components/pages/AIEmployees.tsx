import { Link } from "react-router";
import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, Bot, CheckCircle2, Play, RefreshCw, Activity, Power, Square, KeyRound, ShieldCheck, Trash2, RotateCw, Inbox, GripVertical, XCircle, Copy, Terminal } from "lucide-react";
import { StatusBadge } from "../shared/StatusBadge";
import {
  createAgentGatewayEnrollment,
  closeExecutionEvidenceGap,
  decideApproval,
  decideEvaluationCase,
  decideMemory,
  dispatchCommanderWorkPackage,
  dispatchCommanderWorkPackageBatch,
  dispatchLocalWorkerOnce,
  issueApprovedAgentGatewayEnrollment,
  loadApprovals,
  loadAgentGatewayEnrollments,
  loadAgentGatewaySessions,
  loadAgentGatewayStatus,
  loadAgents,
  loadCustomerDeliveryBoard,
  loadDashboard,
  loadDemoReadiness,
  loadCommanderProjectBoard,
  loadCommanderWorkPackages,
  loadHermesOpenClawLoopReadback,
  loadIntegrationInbox,
  loadLocalReadiness,
  loadOperatorActionReceipts,
  loadOperatorAgentLoopHandoff,
  loadOperatorCommandCenter,
  loadOperatorActionPlan,
  loadOperatorEvidenceReport,
  loadOperatorExecutionMode,
  loadOperatorHandoff,
  loadOperatorHealth,
  loadOperatorLoopControl,
  loadOperatorLoopBootstrap,
  loadOperatorLoopDriverPackets,
  loadOperatorLoopLaunchPacket,
  loadOperatorLoopSupervision,
  loadOperatorLoopAudit,
  loadOperatorLoopSelfCheck,
  loadOperatorRuntimeDoctor,
  loadReviewQueue,
  loadSecurityProductionReadiness,
  loadStuckWorkflowJobs,
  loadWorkerAdapterReadiness,
  loadWorkerDaemonLogs,
  loadWorkerFleet,
  loadWorkerFleetHygiene,
  loadWorkerStatus,
  loadWorkflowJobs,
  markWorkflowJobFailed,
  applyWorkerFleetHygiene,
  planCommanderWorkPackages,
  promoteCommanderSynthesis,
  previewAgentGatewayEnrollmentPolicy,
  proposeReceiptFailureMemory,
  recordOperatorActionControlReadback,
  recordOperatorActionReceipt,
  releaseWorkerTask,
  restartLocalWorkerDaemon,
  revokeAgentGatewayEnrollment,
  revokeAgentGatewaySession,
  rotateAgentGatewayEnrollment,
  runCustomerWorkerTaskWorkflow,
  runHermesOpenClawLoopWorkflow,
  requestAgentGatewayEnrollment,
  startLocalWorkerDaemon,
  stopLocalWorkerDaemon,
  submitCustomerWorkerTaskJob,
  synthesizeCommanderWorkPackages,
  type AgentGatewayEnrollmentCreateResult,
  type AgentGatewayEnrollmentPolicyPreview,
  type AgentGatewayEnrollmentRequestResult,
  type CommanderProjectBoardPayload,
  type CommanderWorkPackageDispatchBatchPayload,
  type CommanderWorkPackagePlanPayload,
  type CommanderSynthesisPromotionPayload,
  type CustomerDeliveryBoardPayload,
  type CustomerTaskWorkflowResult,
  type ExecutionEvidenceGapItem,
  type HermesOpenClawLoopReadbackPayload,
  type HermesOpenClawLoopWorkflowResult,
  type OperatorActionPlanPayload,
  type OperatorActionReceiptsPayload,
  type OperatorAgentLoopHandoffPayload,
  type OperatorCommandCenterPayload,
  type OperatorEvidenceReportPayload,
  type OperatorExecutionModePayload,
  type OperatorHandoffPayload,
  type OperatorHealthPayload,
  type OperatorLoopControlPayload,
  type OperatorLoopBootstrapPayload,
  type OperatorLoopDriverPacketsPayload,
  type OperatorLoopLaunchPacketPayload,
  type OperatorLoopSupervisionPayload,
  type OperatorLoopAuditPayload,
  type OperatorLoopSelfCheckPayload,
  type OperatorRuntimeDoctorPayload,
  type ReviewQueuePayload,
  type TaskIntakeChecklistItem,
  type WorkerAdapterName,
  type WorkerDaemonResult,
  type WorkerDaemonLogPayload,
  type WorkerDispatchResult,
  type WorkerFleetHygienePayload,
  type WorkflowJob,
} from "../../data/liveApi";
import { pick, usePreferences } from "../../context/PreferencesContext";

const RUNTIME_COLOR: Record<string, string> = {
  claude_code: "#7A5AF8",
  codex:       "#22D3EE",
  openhands:   "#E76F51",
  mock:        "#6B7280",
  openclaw:    "#2A9D8F",
  hermes:      "#2E86AB",
  crewai:      "#FBBF24",
  langgraph:   "#F87171",
};

const DEFAULT_GATEWAY_SCOPES = [
  "agents:heartbeat",
  "agent_plans:read",
  "agent_plans:write",
  "plan_evidence:read",
  "plan_evidence:write",
  "knowledge:read",
  "knowledge:write",
  "tasks:create",
  "tasks:read",
  "tasks:claim",
  "runs:write",
  "runtime_events:write",
  "toolcalls:write",
  "artifacts:write",
  "memories:propose",
  "evaluations:submit",
  "audit:write",
];

const WORKER_EXECUTION_REQUIRED_SCOPES = [
  "agents:heartbeat",
  "tasks:read",
  "tasks:claim",
  "runs:write",
  "runtime_events:write",
  "toolcalls:write",
  "evaluations:submit",
  "audit:write",
];

const GATEWAY_SCOPE_PRESETS = [
  {
    id: "worker",
    scopes: DEFAULT_GATEWAY_SCOPES,
  },
  {
    id: "observer",
    scopes: ["agents:heartbeat", "knowledge:read", "agent_plans:read", "plan_evidence:read", "tasks:read", "audit:write"],
  },
  {
    id: "approval",
    scopes: ["agents:heartbeat", "tasks:read", "approvals:request", "audit:write"],
  },
  {
    id: "full",
    scopes: ["agents:write", "agents:heartbeat", "agent_plans:read", "agent_plans:write", "plan_evidence:read", "plan_evidence:write", "knowledge:read", "knowledge:write", "tasks:create", "tasks:read", "tasks:claim", "runs:write", "runtime_events:write", "toolcalls:write", "artifacts:write", "approvals:request", "memories:propose", "evaluations:submit", "audit:write"],
  },
];

const WORKER_ADAPTERS = ["mock", "hermes", "openclaw"] as const;
type OperatorLoopBootstrapMode = "fast" | "deep";

type AIEmployeesPanelLoadState = {
  id: string;
  status: "ready" | "unavailable" | "running";
  error?: string;
  last_error?: string;
  attempts?: number;
  updated_at?: string;
  last_action?: "initial_load" | "local_refresh";
};

type AIEmployeesLiveData = {
  [key: string]: unknown;
  commanderProjectBoard?: CommanderProjectBoardPayload;
  operatorCommandCenter?: OperatorCommandCenterPayload;
  operatorExecutionMode?: OperatorExecutionModePayload;
  operatorLoopControl?: OperatorLoopControlPayload;
  operatorLoopBootstrap?: OperatorLoopBootstrapPayload;
  loopBootstrapMode?: OperatorLoopBootstrapMode;
  operatorLoopDriverPackets?: OperatorLoopDriverPacketsPayload;
  operatorLoopSupervision?: OperatorLoopSupervisionPayload;
  operatorAgentLoopHandoff?: OperatorAgentLoopHandoffPayload;
  operatorRuntimeDoctor?: OperatorRuntimeDoctorPayload;
  executionModeAdapter?: WorkerAdapterName;
  executionModeConfirmRun?: boolean;
  activeCommanderProjectId?: string;
  activeCommanderPlanId?: string;
  panelLoadState?: Record<string, AIEmployeesPanelLoadState>;
};

type AIEmployeesPanelLoader = {
  id: string;
  load: (context: AIEmployeesLiveData) => Promise<Partial<AIEmployeesLiveData>>;
};

const panelErrorMessage = (err: unknown) => err instanceof Error ? err.message : String(err);

const panelLoadRecord = (
  id: string,
  status: AIEmployeesPanelLoadState["status"],
  options: Partial<AIEmployeesPanelLoadState> = {},
): AIEmployeesPanelLoadState => ({
  id,
  status,
  attempts: options.attempts ?? 1,
  updated_at: options.updated_at || new Date().toISOString(),
  last_action: options.last_action || "initial_load",
  ...(options.error ? { error: options.error, last_error: options.last_error || options.error } : {}),
  ...(options.last_error && !options.error ? { last_error: options.last_error } : {}),
});

const AI_EMPLOYEES_PANEL_LOADERS: AIEmployeesPanelLoader[] = [
  { id: "dashboard", load: async () => ({ metrics: await loadDashboard() }) },
  { id: "demo_readiness", load: async () => ({ demoReadiness: await loadDemoReadiness() }) },
  { id: "worker_status", load: async () => ({ workerStatus: await loadWorkerStatus() }) },
  { id: "worker_fleet", load: async () => ({ workerFleet: await loadWorkerFleet() }) },
  { id: "worker_hygiene", load: async () => ({ workerHygiene: await loadWorkerFleetHygiene({ limit: 5 }) }) },
  { id: "adapter_readiness", load: async () => ({ adapterReadiness: await loadWorkerAdapterReadiness() }) },
  { id: "local_readiness", load: async () => ({ localReadiness: await loadLocalReadiness() }) },
  { id: "operator_runtime_doctor", load: async () => ({ operatorRuntimeDoctor: await loadOperatorRuntimeDoctor(8) }) },
  { id: "operator_execution_mode", load: async (context) => ({ operatorExecutionMode: await loadOperatorExecutionMode(context.executionModeAdapter || "mock", Boolean(context.executionModeConfirmRun), 8) }) },
  { id: "operator_loop_control", load: async () => ({ operatorLoopControl: await loadOperatorLoopControl(8) }) },
  { id: "operator_loop_bootstrap", load: async (context) => ({ operatorLoopBootstrap: await loadOperatorLoopBootstrap(8, { fast: context.loopBootstrapMode !== "deep" }) }) },
  { id: "operator_agent_loop_handoff", load: async () => ({ operatorAgentLoopHandoff: await loadOperatorAgentLoopHandoff(8) }) },
  { id: "operator_loop_supervision", load: async () => ({ operatorLoopSupervision: await loadOperatorLoopSupervision(8) }) },
  { id: "operator_loop_driver_packets", load: async () => ({ operatorLoopDriverPackets: await loadOperatorLoopDriverPackets(8) }) },
  { id: "operator_command_center", load: async () => ({ operatorCommandCenter: await loadOperatorCommandCenter(12) }) },
  { id: "operator_action_plan", load: async () => ({ operatorActionPlan: await loadOperatorActionPlan(12) }) },
  { id: "operator_action_receipts", load: async () => ({ operatorActionReceipts: await loadOperatorActionReceipts(8) }) },
  { id: "operator_evidence_report", load: async () => ({ operatorEvidenceReport: await loadOperatorEvidenceReport(8) }) },
  { id: "operator_loop_launch_packet", load: async () => ({ operatorLoopLaunchPacket: await loadOperatorLoopLaunchPacket(8) }) },
  { id: "security_readiness", load: async () => ({ securityReadiness: await loadSecurityProductionReadiness() }) },
  { id: "integration_inbox", load: async (context) => ({ integrationInbox: await loadIntegrationInbox({ bucket: String(context.integrationInboxBucket || "all"), limit: 20 }) }) },
  { id: "commander_work_packages", load: async () => ({ commanderWorkPackages: await loadCommanderWorkPackages({ limit: 8 }) }) },
  { id: "commander_project_board", load: async (context) => ({ commanderProjectBoard: await loadCommanderProjectBoard({ project_id: String(context.activeCommanderProjectId || ""), plan_id: String(context.activeCommanderPlanId || ""), limit: 12 }) }) },
  { id: "review_queue", load: async () => ({ reviewQueue: await loadReviewQueue(12) }) },
  { id: "customer_delivery_board", load: async () => ({ customerDeliveryBoard: await loadCustomerDeliveryBoard(8) }) },
  { id: "loop_lane_readback", load: async () => ({ loopLaneReadback: await loadHermesOpenClawLoopReadback("", 6) }) },
  { id: "agent_gateway_enrollments", load: async () => ({ enrollmentPayload: await loadAgentGatewayEnrollments() }) },
  { id: "agent_gateway_sessions", load: async () => ({ sessionPayload: await loadAgentGatewaySessions() }) },
  { id: "agent_gateway_status", load: async () => ({ gatewayStatus: await loadAgentGatewayStatus() }) },
  { id: "approvals", load: async () => ({ approvals: await loadApprovals() }) },
  { id: "workflow_jobs", load: async () => ({ workflowJobs: await loadWorkflowJobs(8) }) },
  { id: "stuck_workflow_jobs", load: async () => ({ stuckWorkflowJobs: await loadStuckWorkflowJobs(30, 8) }) },
];

const AI_EMPLOYEES_CORE_PANEL_IDS = new Set([
  "dashboard",
  "worker_status",
  "worker_fleet",
  "local_readiness",
  "operator_runtime_doctor",
  "operator_execution_mode",
  "operator_loop_control",
  "agent_gateway_status",
]);

const AI_EMPLOYEES_CORE_PANEL_LOADERS = AI_EMPLOYEES_PANEL_LOADERS.filter((loader) => AI_EMPLOYEES_CORE_PANEL_IDS.has(loader.id));
const AI_EMPLOYEES_DEFERRED_PANEL_LOADERS = AI_EMPLOYEES_PANEL_LOADERS.filter((loader) => !AI_EMPLOYEES_CORE_PANEL_IDS.has(loader.id));

const AI_EMPLOYEES_SCOPED_PANEL_LOADERS: AIEmployeesPanelLoader[] = [
  { id: "operator_loop_audit", load: async (context) => ({ operatorLoopAudit: await loadOperatorLoopAudit(12, String(context.scopedLoopId || "")) }) },
  { id: "operator_handoff", load: async (context) => ({ operatorHandoff: await loadOperatorHandoff(12, String(context.scopedLoopId || "")) }) },
  { id: "operator_health", load: async (context) => ({ operatorHealth: await loadOperatorHealth(12, String(context.scopedLoopId || "")) }) },
  { id: "operator_loop_self_check", load: async (context) => ({ operatorLoopSelfCheck: await loadOperatorLoopSelfCheck(12, String(context.scopedLoopId || "")) }) },
];

async function loadAIEmployeesPanelSet(loaders: AIEmployeesPanelLoader[], context: AIEmployeesLiveData = {}): Promise<AIEmployeesLiveData> {
  const settled = await Promise.allSettled(loaders.map(async (loader) => ({
    id: loader.id,
    payload: await loader.load(context),
  })));
  const data: AIEmployeesLiveData = {};
  const panelLoadState: Record<string, AIEmployeesPanelLoadState> = {};
  settled.forEach((result, index) => {
    const id = loaders[index]?.id || `panel_${index}`;
    if (result.status === "fulfilled") {
      Object.assign(data, result.value.payload);
      panelLoadState[result.value.id] = panelLoadRecord(result.value.id, "ready");
    } else {
      panelLoadState[id] = panelLoadRecord(id, "unavailable", { error: panelErrorMessage(result.reason) });
    }
  });
  return { ...data, panelLoadState };
}

function loopIdFromUri(uri: unknown): string | null {
  const value = String(uri || "");
  const match = value.match(/^loop:\/\/([^/]+)/);
  return match?.[1] || null;
}

function latestLoopIdFromReadback(readback?: HermesOpenClawLoopReadbackPayload): string {
  if (readback?.loop_id) return String(readback.loop_id);
  for (const artifact of readback?.artifacts || []) {
    const loopId = loopIdFromUri(artifact.uri);
    if (loopId) return loopId;
  }
  for (const run of readback?.runs || []) {
    const loopId = loopIdFromUri(run.delegation_id);
    if (loopId) return loopId;
  }
  return "";
}

export function AIEmployees() {
  const { locale } = usePreferences();
  const [dispatching, setDispatching] = useState<string | null>(null);
  const [dispatchResult, setDispatchResult] = useState<string | null>(null);
  const [hygieneBusy, setHygieneBusy] = useState(false);
  const [hygieneResult, setHygieneResult] = useState<WorkerFleetHygienePayload | null>(null);
  const [hygieneError, setHygieneError] = useState<string | null>(null);
  const [customerTaskBusy, setCustomerTaskBusy] = useState(false);
  const [customerTaskError, setCustomerTaskError] = useState<string | null>(null);
  const [customerTaskResult, setCustomerTaskResult] = useState<CustomerTaskWorkflowResult | null>(null);
  const [customerTaskJob, setCustomerTaskJob] = useState<WorkflowJob | null>(null);
  const [lastWorkerDispatch, setLastWorkerDispatch] = useState<WorkerDispatchResult | null>(null);
  const [lastDaemonControl, setLastDaemonControl] = useState<WorkerDaemonResult | null>(null);
  const [copiedIntakeCommand, setCopiedIntakeCommand] = useState<string | null>(null);
  const [loopLaneBusy, setLoopLaneBusy] = useState(false);
  const [loopLaneError, setLoopLaneError] = useState<string | null>(null);
  const [loopLaneResult, setLoopLaneResult] = useState<HermesOpenClawLoopWorkflowResult | null>(null);
  const [loopLaneForm, setLoopLaneForm] = useState({
    topic: locale === "zh"
      ? "请 Hermes 和 OpenClaw 审视 AgentOps MIS 下一步最重要的产品闭环。"
      : "Ask Hermes and OpenClaw to review the next most important AgentOps MIS product closure.",
    loop_id: "",
  });
  const [workflowJobAction, setWorkflowJobAction] = useState<string | null>(null);
  const [workflowJobResult, setWorkflowJobResult] = useState<string | null>(null);
  const [reviewAction, setReviewAction] = useState<string | null>(null);
  const [reviewResult, setReviewResult] = useState<string | null>(null);
  const [loopRecordAction, setLoopRecordAction] = useState<string | null>(null);
  const [loopRecordResult, setLoopRecordResult] = useState<string | null>(null);
  const [commanderPlannerBusy, setCommanderPlannerBusy] = useState(false);
  const [commanderPlannerError, setCommanderPlannerError] = useState<string | null>(null);
  const [commanderPlannerResult, setCommanderPlannerResult] = useState<CommanderWorkPackagePlanPayload | null>(null);
  const [activeCommanderProject, setActiveCommanderProject] = useState<{ projectId: string; planId: string } | null>(null);
  const [lastCommanderBatch, setLastCommanderBatch] = useState<CommanderWorkPackageDispatchBatchPayload | null>(null);
  const [lastSynthesis, setLastSynthesis] = useState<{ artifactId: string; approvalId?: string | null } | null>(null);
  const [synthesisPromotion, setSynthesisPromotion] = useState<CommanderSynthesisPromotionPayload | null>(null);
  const [commanderPlannerForm, setCommanderPlannerForm] = useState({
    goal: locale === "zh"
      ? "用 AgentOps MIS 协调一个客户 AI 团队项目：拆分并行工作包、分派 agent、保留证据、准备交付。"
      : "Use AgentOps MIS to coordinate a customer AI-team project: split parallel work packages, assign agents, keep evidence, and prepare delivery.",
    max_packages: "5",
  });
  const [actionQueueOrder, setActionQueueOrder] = useState<string[]>([]);
  const [draggedActionId, setDraggedActionId] = useState<string | null>(null);
  const [receiptAction, setReceiptAction] = useState<string | null>(null);
  const [panelReceiptAction, setPanelReceiptAction] = useState<string | null>(null);
  const [receiptFailureMemoryAction, setReceiptFailureMemoryAction] = useState<string | null>(null);
  const [receiptFailureMemoryResult, setReceiptFailureMemoryResult] = useState<string | null>(null);
  const [customerTaskForm, setCustomerTaskForm] = useState<{
    adapter: (typeof WORKER_ADAPTERS)[number];
    title: string;
    description: string;
  }>({
    adapter: "mock",
    title: locale === "zh" ? "优化 Pixel Office 工作台" : "Improve the Pixel Office workspace",
    description: locale === "zh"
      ? "请 AI 团队从客户视角审视 Pixel Office：让像素风更精致，流程更清楚，同时保持 MIS 账本、审批和运行证据可见。"
      : "Ask the AI team to review Pixel Office from a customer perspective: improve the pixel style, clarify the flow, and keep MIS ledger, approvals, and run evidence visible.",
  });
  const [liveRuntimeConfirmed, setLiveRuntimeConfirmed] = useState(false);
  const [loopBootstrapMode, setLoopBootstrapMode] = useState<OperatorLoopBootstrapMode>("fast");
  const [selectedLogAdapter, setSelectedLogAdapter] = useState<(typeof WORKER_ADAPTERS)[number]>("mock");
  const [daemonLogsOpen, setDaemonLogsOpen] = useState(false);
  const [daemonLogsByAdapter, setDaemonLogsByAdapter] = useState<Partial<Record<(typeof WORKER_ADAPTERS)[number], WorkerDaemonLogPayload>>>({});
  const [daemonLogsLoading, setDaemonLogsLoading] = useState(false);
  const [daemonLogsError, setDaemonLogsError] = useState<string | null>(null);
  const [integrationInboxBucket, setIntegrationInboxBucket] = useState("all");
  const [enrollmentAction, setEnrollmentAction] = useState<string | null>(null);
  const [enrollmentResult, setEnrollmentResult] = useState<string | null>(null);
  const [createdToken, setCreatedToken] = useState<AgentGatewayEnrollmentCreateResult | null>(null);
  const [issuedCredentialCopied, setIssuedCredentialCopied] = useState(false);
  const [createdRequest, setCreatedRequest] = useState<AgentGatewayEnrollmentRequestResult | null>(null);
  const [enrollmentPolicy, setEnrollmentPolicy] = useState<AgentGatewayEnrollmentPolicyPreview | null>(null);
  const [enrollmentPolicyError, setEnrollmentPolicyError] = useState<string | null>(null);
  const [issueApprovalId, setIssueApprovalId] = useState("");
  const [enrollmentForm, setEnrollmentForm] = useState({
    agent_id: "agt_remote_customer_worker",
    name: "Customer Remote Worker",
    runtime_type: "mock",
    workspace_id: "local-demo",
    ttl_days: "30",
    heartbeat_timeout_sec: "300",
    scopes: DEFAULT_GATEWAY_SCOPES.join(", "),
  });
  const [data, setData] = useState<AIEmployeesLiveData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [deferredLoading, setDeferredLoading] = useState(false);
  const [deferredError, setDeferredError] = useState<string | null>(null);
  const [localPanelRefreshing, setLocalPanelRefreshing] = useState<string | null>(null);
  const clearIssuedCredential = useCallback(() => {
    setCreatedToken(null);
    setIssuedCredentialCopied(false);
  }, []);
  const refresh = useCallback(async (options?: { preserveIssuedCredential?: boolean; commanderProject?: { projectId: string; planId: string } | null }) => {
    if (!options?.preserveIssuedCredential) {
      clearIssuedCredential();
    }
    setLoading(true);
    setError(null);
    setDeferredError(null);
    const commanderProject = options?.commanderProject === undefined ? activeCommanderProject : options.commanderProject;
    try {
      const coreContext = await loadAIEmployeesPanelSet(AI_EMPLOYEES_CORE_PANEL_LOADERS, {
        integrationInboxBucket,
        executionModeAdapter: customerTaskForm.adapter,
        executionModeConfirmRun: liveRuntimeConfirmed,
        loopBootstrapMode,
        activeCommanderProjectId: commanderProject?.projectId || "",
        activeCommanderPlanId: commanderProject?.planId || "",
      });
      setData((current) => ({
        ...(current || {}),
        ...coreContext,
        panelLoadState: {
          ...((current || {}).panelLoadState || {}),
          ...(coreContext.panelLoadState || {}),
        },
      }));
      setLoading(false);
      setDeferredLoading(true);
      try {
        const deferredContext = await loadAIEmployeesPanelSet(AI_EMPLOYEES_DEFERRED_PANEL_LOADERS, {
          ...coreContext,
          integrationInboxBucket,
          loopBootstrapMode,
          activeCommanderProjectId: commanderProject?.projectId || "",
          activeCommanderPlanId: commanderProject?.planId || "",
        });
        const scopedLoopId = latestLoopIdFromReadback(deferredContext.loopLaneReadback as HermesOpenClawLoopReadbackPayload | undefined);
        const scopedContext = await loadAIEmployeesPanelSet(AI_EMPLOYEES_SCOPED_PANEL_LOADERS, {
          ...coreContext,
          ...deferredContext,
          scopedLoopId,
        });
        const agentContext = await loadAIEmployeesPanelSet([{
          id: "agents",
          load: async (context) => ({ agents: await loadAgents(context.metrics as Parameters<typeof loadAgents>[0]) }),
        }], coreContext);
        setData((current) => ({
          ...(current || {}),
          ...deferredContext,
          ...scopedContext,
          ...agentContext,
          panelLoadState: {
            ...((current || {}).panelLoadState || {}),
            ...(deferredContext.panelLoadState || {}),
            ...(scopedContext.panelLoadState || {}),
            ...(agentContext.panelLoadState || {}),
          },
        }));
      } catch (err) {
        setDeferredError(err instanceof Error ? err.message : String(err));
      } finally {
        setDeferredLoading(false);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setLoading(false);
      setDeferredLoading(false);
    }
  }, [activeCommanderProject?.planId, activeCommanderProject?.projectId, clearIssuedCredential, customerTaskForm.adapter, integrationInboxBucket, liveRuntimeConfirmed, loopBootstrapMode]);
  useEffect(() => {
    void refresh();
  }, [refresh]);
  const refreshPanel = useCallback(async (panelId: string) => {
    clearIssuedCredential();
    const loader = panelId === "operator_health"
      ? { id: "operator_health", load: async () => ({ operatorHealth: await loadOperatorHealth(12, "") }) }
      : panelId === "agents"
        ? { id: "agents", load: async (context: AIEmployeesLiveData) => ({ agents: await loadAgents(context.metrics as Parameters<typeof loadAgents>[0]) }) }
        : [...AI_EMPLOYEES_PANEL_LOADERS, ...AI_EMPLOYEES_SCOPED_PANEL_LOADERS].find((item) => item.id === panelId);
    if (!loader) return;
    const scopedLoopId = latestLoopIdFromReadback(data?.loopLaneReadback as HermesOpenClawLoopReadbackPayload | undefined);
    const context = {
      ...(data || {}),
      integrationInboxBucket,
      scopedLoopId,
      executionModeAdapter: customerTaskForm.adapter,
      executionModeConfirmRun: liveRuntimeConfirmed,
      loopBootstrapMode,
      activeCommanderProjectId: activeCommanderProject?.projectId || "",
      activeCommanderPlanId: activeCommanderProject?.planId || "",
    };
    setLocalPanelRefreshing(panelId);
    setData((current) => ({
      ...(current || {}),
      panelLoadState: {
        ...((current || {}).panelLoadState || {}),
        [panelId]: panelLoadRecord(panelId, "running", {
          attempts: Number((current || {}).panelLoadState?.[panelId]?.attempts || 0) + 1,
          last_action: "local_refresh",
          last_error: (current || {}).panelLoadState?.[panelId]?.last_error,
        }),
      },
    }));
    try {
      const payload = await loader.load(context);
      setData((current) => ({
        ...(current || {}),
        ...payload,
        panelLoadState: {
          ...((current || {}).panelLoadState || {}),
          [panelId]: panelLoadRecord(panelId, "ready", {
            attempts: Number((current || {}).panelLoadState?.[panelId]?.attempts || 1),
            last_action: "local_refresh",
          }),
        },
      }));
    } catch (err) {
      setData((current) => ({
        ...(current || {}),
        panelLoadState: {
          ...((current || {}).panelLoadState || {}),
          [panelId]: panelLoadRecord(panelId, "unavailable", {
            attempts: Number((current || {}).panelLoadState?.[panelId]?.attempts || 1),
            last_action: "local_refresh",
            error: panelErrorMessage(err),
          }),
        },
      }));
    } finally {
      setLocalPanelRefreshing((current) => current === panelId ? null : current);
    }
  }, [activeCommanderProject?.planId, activeCommanderProject?.projectId, clearIssuedCredential, customerTaskForm.adapter, data, integrationInboxBucket, liveRuntimeConfirmed, loopBootstrapMode]);
  const loadSelectedDaemonLog = async (adapter = selectedLogAdapter) => {
    setDaemonLogsLoading(true);
    setDaemonLogsError(null);
    try {
      const payload = await loadWorkerDaemonLogs(adapter);
      setDaemonLogsByAdapter((current) => ({ ...current, [adapter]: payload }));
    } catch (err) {
      setDaemonLogsError(err instanceof Error ? err.message : String(err));
    } finally {
      setDaemonLogsLoading(false);
    }
  };
  useEffect(() => {
    if (!daemonLogsOpen) return;
    void loadSelectedDaemonLog(selectedLogAdapter);
  }, [daemonLogsOpen, selectedLogAdapter]);
  const agents = data?.agents || [];
  const demoReadiness = data?.demoReadiness;
  const workerStatus = data?.workerStatus;
  const workerFleet = data?.workerFleet;
  const workerHygiene = data?.workerHygiene as WorkerFleetHygienePayload | undefined;
  const activeHygiene = hygieneResult || workerHygiene;
  const adapterReadiness = data?.adapterReadiness;
  const localReadiness = data?.localReadiness;
  const operatorCommandCenter = data?.operatorCommandCenter as OperatorCommandCenterPayload | undefined;
  const operatorActionPlan = data?.operatorActionPlan as OperatorActionPlanPayload | undefined;
  const operatorActionReceipts = data?.operatorActionReceipts as OperatorActionReceiptsPayload | undefined;
  const operatorEvidenceReport = data?.operatorEvidenceReport as OperatorEvidenceReportPayload | undefined;
  const operatorLoopLaunchPacket = data?.operatorLoopLaunchPacket as OperatorLoopLaunchPacketPayload | undefined;
  const operatorRuntimeDoctor = data?.operatorRuntimeDoctor as OperatorRuntimeDoctorPayload | undefined;
  const operatorExecutionMode = data?.operatorExecutionMode as OperatorExecutionModePayload | undefined;
  const operatorLoopAudit = data?.operatorLoopAudit as OperatorLoopAuditPayload | undefined;
  const operatorLoopDriverPackets = data?.operatorLoopDriverPackets as OperatorLoopDriverPacketsPayload | undefined;
  const operatorLoopBootstrap = data?.operatorLoopBootstrap as OperatorLoopBootstrapPayload | undefined;
  const operatorLoopSupervision = data?.operatorLoopSupervision as OperatorLoopSupervisionPayload | undefined;
  const operatorAgentLoopHandoff = data?.operatorAgentLoopHandoff as OperatorAgentLoopHandoffPayload | undefined;
  const operatorHandoff = data?.operatorHandoff as OperatorHandoffPayload | undefined;
  const operatorHealth = data?.operatorHealth as OperatorHealthPayload | undefined;
  const operatorLoopControl = data?.operatorLoopControl as OperatorLoopControlPayload | undefined;
  const operatorLoopSelfCheck = data?.operatorLoopSelfCheck as OperatorLoopSelfCheckPayload | undefined;
  const operatorCommandCenterSummary = operatorCommandCenter?.summary;
  const operatorCommandCenterActions = operatorCommandCenter?.next_actions || [];
  const operatorCommandCenterCommanderSummary = operatorCommandCenter?.commander?.summary || {};
  const operatorCommandCenterCodingEvidence = typeof operatorCommandCenterCommanderSummary.coding_evidence === "object" && operatorCommandCenterCommanderSummary.coding_evidence !== null
    ? operatorCommandCenterCommanderSummary.coding_evidence as Record<string, unknown>
    : {};
  const operatorCommandCenterCodingGapCount = Number(operatorCommandCenterCodingEvidence.missing ?? operatorCommandCenterSummary?.commander_coding_evidence_missing ?? 0) + Number(operatorCommandCenterCodingEvidence.partial ?? operatorCommandCenterSummary?.commander_coding_evidence_partial ?? 0);
  const operatorPlanActions = operatorActionPlan?.actions || [];
  const operatorPlanSummary = operatorActionPlan?.summary;
  const operatorReceiptCoverage = operatorActionPlan?.receipt_coverage;
  const operatorEvidenceGaps = operatorActionPlan?.execution_evidence?.gaps || [];
  const operatorEvidenceSummary = operatorEvidenceReport?.summary;
  const operatorEvidenceRuns = operatorEvidenceReport?.runs || [];
  const operatorEvidenceCommands = operatorEvidenceReport?.recommended_commands || [];
  const runtimeDoctorSummary = operatorRuntimeDoctor?.summary;
  const runtimeDoctorBlockedGates = runtimeDoctorSummary?.blocked_gates || [];
  const runtimeDoctorAttentionGates = runtimeDoctorSummary?.attention_gates || [];
  const runtimeDoctorCommands = operatorRuntimeDoctor?.commands || {};
  const runtimeDoctorPrimaryCommands = [
    runtimeDoctorCommands.operator_runtime_doctor,
    runtimeDoctorCommands.worker_readiness,
    runtimeDoctorCommands.operator_health,
  ].filter(Boolean).slice(0, 3);
  const runtimeDoctorTopGates = operatorRuntimeDoctor?.gates.slice(0, 4) || [];
  const operatorEvidenceTopRuns = operatorEvidenceRuns
    .filter(item => item.status !== "ready")
    .concat(operatorEvidenceRuns.filter(item => item.status === "ready"))
    .slice(0, 3);
  const loopLaunchEvaluationContract = operatorLoopLaunchPacket?.evaluation_contract;
  const loopLaunchAuditContract = operatorLoopLaunchPacket?.audit_contract;
  const loopLaunchExitCriteria = loopLaunchEvaluationContract?.minimum_exit_criteria || [];
  const loopLaunchRequiredLedgers = loopLaunchEvaluationContract?.required_ledgers || [];
  const loopLaunchRequiredCommands = loopLaunchEvaluationContract?.required_commands || [];
  const loopLaunchRecordCommands = loopLaunchAuditContract?.record_commands || [];
  const loopLaunchBoundedRunner = loopLaunchAuditContract?.bounded_runner || {};
  const loopLaunchReceiptEvaluation = loopLaunchEvaluationContract?.receipt_evaluation || {};
  const loopLaunchCommands = operatorLoopLaunchPacket?.commands || [];
  const loopLaunchExecutionChain = operatorLoopLaunchPacket?.execution_chain || [];
  const loopLaunchControlSummary = operatorLoopLaunchPacket?.control_summary;
  const loopLaunchRecommendedStep = loopLaunchControlSummary?.recommended_step || {};
  const loopLaunchRecommendedCommand = String(loopLaunchControlSummary?.next_command || loopLaunchRecommendedStep.command || "");
  const loopLaunchRecommendedVerifyCommand = String(loopLaunchControlSummary?.verify_command || loopLaunchRecommendedStep.verify_command || "");
  const loopLaunchRecommendedReceiptCommand = String(loopLaunchControlSummary?.receipt_command || loopLaunchRecommendedStep.receipt_command || "");
  const loopLaunchMutatingSteps = loopLaunchExecutionChain.filter(step => step.mutating).length;
  const loopLaunchReceiptSteps = loopLaunchExecutionChain.filter(step => step.receipt_required).length;
  const loopLaunchPacketJson = operatorLoopLaunchPacket ? JSON.stringify({
    status: operatorLoopLaunchPacket.status,
    method: operatorLoopLaunchPacket.method,
    task_id: operatorLoopLaunchPacket.task_id,
    agent_id: operatorLoopLaunchPacket.agent_id,
    summary: operatorLoopLaunchPacket.summary,
    agent_plan_draft: operatorLoopLaunchPacket.agent_plan_draft,
    evaluation_contract: operatorLoopLaunchPacket.evaluation_contract,
    audit_contract: operatorLoopLaunchPacket.audit_contract,
    execution_chain: operatorLoopLaunchPacket.execution_chain,
    control_summary: operatorLoopLaunchPacket.control_summary,
    commands: operatorLoopLaunchPacket.commands,
    safety: operatorLoopLaunchPacket.safety,
    token_omitted: operatorLoopLaunchPacket.token_omitted,
  }, null, 2) : "";
  const taskIntakeChecklist = operatorActionPlan?.task_intake;
  const taskIntakeSummary = taskIntakeChecklist?.summary;
  const taskIntakeItems = taskIntakeChecklist?.items || [];
  const securityReadiness = data?.securityReadiness;
  const securityGates = securityReadiness?.gates || [];
  const localWriteGuardGate = securityGates.find(gate => gate.id === "local_ui_write_guard");
  const visibleSecurityGates = securityGates.filter(gate => gate.id !== "local_ui_write_guard").slice(0, 4);
  const productionSecurityStatus = securityReadiness?.status || "unknown";
  const productionSecurityNextAction = localWriteGuardGate?.next_action || securityReadiness?.next_actions?.[0] || "agentops security production-readiness";
  const productionSecurityNeedsAttention = productionSecurityStatus !== "ready" || !securityReadiness?.production_ready || localWriteGuardGate?.status !== "pass";
  const integrationInbox = data?.integrationInbox;
  const commanderWorkPackages = data?.commanderWorkPackages;
  const commanderProjectBoard = data?.commanderProjectBoard as CommanderProjectBoardPayload | undefined;
  const commanderTeamBoard = commanderProjectBoard?.team_board || null;
  const commanderTeamLanes = commanderTeamBoard?.lanes || [];
  const commanderLastQueueBoard = lastCommanderBatch?.team_board_after_queue || null;
  const commanderPackageRows = commanderWorkPackages?.work_packages || [];
  const commanderActionRows = commanderTeamLanes.length ? commanderTeamLanes : commanderPackageRows;
  const commanderPlannedPackageCount = commanderActionRows.filter(pkg => pkg.package_status === "planned" || pkg.status === "planned").length;
  const commanderReadyPackageCount = commanderActionRows.filter(pkg => pkg.package_status === "ready_for_review").length;
  const reviewQueue = data?.reviewQueue as ReviewQueuePayload | undefined;
  const reviewQueueSummary = reviewQueue?.summary;
  const reviewQueueItems = reviewQueue?.review_items || [];
  const reviewQueueSafety = reviewQueue?.safety;
  const customerDeliveryBoard = data?.customerDeliveryBoard as CustomerDeliveryBoardPayload | undefined;
  const customerDeliveries = customerDeliveryBoard?.deliveries || [];
  const customerDeliverySummary = customerDeliveryBoard?.summary;
  const customerDeliverySafety = customerDeliveryBoard?.safety;
  const loopLaneReadback = data?.loopLaneReadback as HermesOpenClawLoopReadbackPayload | undefined;
  const localEvidence = localReadiness?.evidence;
  const localReadinessActions = localReadiness?.next_actions || [];
  const localRunPath = localReadiness?.local_run_path || [];
  const localServiceControlStep = localRunPath.find(step => step.service_control_preview || step.step_id === "preview_worker_service_control");
  const synthesisLifecycle = localReadiness?.commander_synthesis_lifecycle;
  const synthesisLifecycleActions = synthesisLifecycle?.next_actions || [];
  const localReadinessGates = localReadiness?.gates || [];
  const localReadinessReadyGates = localReadinessGates.filter(gate => gate.ok).length;
  const localSafetyOk = Boolean(localReadiness?.token_omitted && localReadiness?.live_execution_performed === false);
  const integrationInboxSummary = integrationInbox?.summary;
  const integrationInboxItems = integrationInbox?.inbox_items || [];
  const integrationInboxActions = integrationInbox?.recommended_next_actions || [];
  const integrationInboxSafety = integrationInbox?.safety;
  const integrationInboxSafe = Boolean(
    integrationInbox?.token_omitted &&
    integrationInbox?.live_execution_performed === false &&
    integrationInboxSafety?.read_only &&
    integrationInboxSafety?.ledger_mutated === false &&
    integrationInboxSafety?.raw_prompt_omitted,
  );
  const fleetHealth = workerStatus?.fleet_health;
  const fleetLanes = workerFleet?.lanes || [];
  const fleetLaneSummary = workerFleet?.summary;
  const fleetGates = fleetHealth?.gates || [];
  const recommendedActions = fleetHealth?.recommended_actions || [];
  const remoteHealth = workerStatus?.remote_worker_health;
  const remoteWorkers = remoteHealth?.remote_workers || [];
  const recentRemoteSessions = remoteHealth?.recent_sessions || [];
  const selectedDaemonLog = daemonLogsByAdapter[selectedLogAdapter]?.daemon;
  const workflowJobs = data?.workflowJobs?.jobs || [];
  const stuckWorkflowJobs = data?.stuckWorkflowJobs?.stuck_jobs || [];
  const stuckWorkflowJobRefs = workerStatus?.stuck_workflow_job_refs || [];
  const stuckWorkflowRecoveryRows = stuckWorkflowJobs.length > 0 ? stuckWorkflowJobs : stuckWorkflowJobRefs;
  const recentEvents = workerStatus?.recent_events || [];
  const stuckTasks = workerStatus?.stuck_tasks || [];
  const hygieneSummary = activeHygiene?.summary;
  const hygieneActionsAvailable = Number(hygieneSummary?.actions_available || 0);
  const enrollments = data?.enrollmentPayload?.enrollments || [];
  const sessions = data?.sessionPayload?.sessions || [];
  const gatewayStatus = data?.gatewayStatus;
  const enrollmentApprovals = (data?.approvals || []).filter(item => item.reason.includes("Approve scoped enrollment"));
  const validScopes = data?.enrollmentPayload?.valid_scopes || DEFAULT_GATEWAY_SCOPES;
  const activeAgents = agents.filter(a => a.status === "running").length;
  const activeEnrollments = enrollments.filter(item => item.status === "active").length;
  const staleEnrollments = enrollments.filter(item => item.heartbeat_state === "stale").length;
  const activeSessions = sessions.filter(item => item.session_state === "active").length;
  const runningDaemons = (workerStatus?.daemons || []).filter(daemon => daemon.running).length;
  const hostManagedAdapters = new Set(
    (workerStatus?.daemons || [])
      .filter(daemon => daemon.running && daemon.management_mode === "host_stack")
      .map(daemon => daemon.adapter),
  );
  const isHostManagedAdapter = (adapter: (typeof WORKER_ADAPTERS)[number]) => hostManagedAdapters.has(adapter);
  const controlBlockedAdapters = new Set(
    (workerStatus?.daemons || [])
      .filter(daemon => daemon.process_claim_active && daemon.control_allowed === false)
      .map(daemon => daemon.adapter),
  );
  const isDaemonControlBlocked = (adapter: (typeof WORKER_ADAPTERS)[number]) => controlBlockedAdapters.has(adapter);
  const lastDaemonAdmissionSummary = lastDaemonControl?.local_loop_admission_summary || lastDaemonControl?.task_intake?.local_loop_admission_summary;
  const lastDaemonAdmissionSafety = lastDaemonAdmissionSummary?.safety || {};
  const lastDaemonAdmissionCommands = lastDaemonAdmissionSummary?.next_safe_commands || [];
  const lastDaemonAdmissionReadOnly = Boolean(lastDaemonAdmissionSafety.read_only);
  const lastDaemonAdmissionLedgerMutated = Boolean(lastDaemonAdmissionSafety.ledger_mutated);
  const lastDaemonAdmissionLiveExecuted = Boolean(lastDaemonAdmissionSafety.live_execution_performed);
  const lastDaemonAdmissionServerShell = Boolean(lastDaemonAdmissionSafety.server_executes_shell);
  const stuckWorkerCount = Number(workerStatus?.stuck_worker_tasks || stuckTasks.length || 0);
  const stuckWorkflowJobCount = Number(workerStatus?.stuck_workflow_jobs || stuckWorkflowJobRefs.length || stuckWorkflowJobs.length || 0);
  const liveReadyAdapters = adapterReadiness?.summary.live_ready_adapters || workerStatus?.adapter_readiness?.live_ready_adapters || [];
  const unavailableAdapters = adapterReadiness?.summary.unavailable_adapters || workerStatus?.adapter_readiness?.unavailable_adapters || [];
  const blockedAdapters = adapterReadiness?.summary.blocked_adapters || workerStatus?.adapter_readiness?.blocked_adapters || [];
  const recommendedAdapter = adapterReadiness?.summary.recommended_adapter || workerStatus?.adapter_readiness?.recommended_adapter || "mock";
  const localRecommendedAdapter = localReadiness?.adapter_readiness?.recommended_adapter || recommendedAdapter;
  const selectedAdapterRoute = adapterReadiness?.adapters?.[customerTaskForm.adapter];
  const selectedAdapterRemediation = selectedAdapterRoute?.remediation;
  const selectedAdapterRemediationCommands = (selectedAdapterRemediation?.commands || []).filter(command => command.command).slice(0, 4);
  const selectedAdapterMissingChecks = selectedAdapterRemediation?.missing || [];
  const selectedAdapterLiveBlocked = customerTaskForm.adapter !== "mock" && ["unavailable", "blocked"].includes(selectedAdapterRoute?.readiness || "");
  const selectedAdapterNeedsLiveConfirm = customerTaskForm.adapter !== "mock";
  const selectedAdapterLiveConfirmMissing = selectedAdapterNeedsLiveConfirm && !liveRuntimeConfirmed;
  const selectedAdapterIsReady = customerTaskForm.adapter === "mock" || selectedAdapterRoute?.readiness === "ready" || selectedAdapterRoute?.readiness === "review_required";
  const liveAdapterConfirmMissing = (adapter: (typeof WORKER_ADAPTERS)[number]) => adapter !== "mock" && !liveRuntimeConfirmed;
  const gatewayReady = Boolean(gatewayStatus?.auth.authenticated || ["ready", "ok", "authenticated"].includes(gatewayStatus?.status || ""));
  const copy = pick(locale, {
    en: {
      title: "AI Employees",
      summary: `${agents.length} registered agents · ${activeAgents} active · live backend`,
      loading: "Loading live agents...",
      deferredLoading: "Loading secondary governance panels...",
      deferredUnavailable: "Some secondary panels are still unavailable",
      backendUnavailable: "Live backend unavailable",
      panelLoadReady: "panel ready",
      panelLoadUnavailable: "panel unavailable",
      panelLoadLoading: "panel loading",
      refreshPanel: "Refresh panel",
      panelRefreshRunning: "Refreshing panel...",
      copyPanelDiagnostics: "Copy diagnostics",
      recordPanelDiagnostics: "Record diagnostics",
      panelReceiptRecorded: "Panel receipt",
      panelAttempts: "attempts",
      panelUpdated: "updated",
      panelLastError: "last error",
      refresh: "Refresh live agents",
      commandCenterTitle: "Worker Fleet Console",
      commandCenterSummary: "Adapter readiness, daemon capacity, remote heartbeat/session health, stuck recovery, and the next safe CLI/API action.",
      operatorCommandCenterTitle: "Operator command center",
      operatorCommandCenterSummary: "Unified supervisor read model for projects, blocked runs, approvals, deliveries, stale workers, coding evidence gates, and next actions.",
      commandCenterActions: "BFF actions",
      commandCenterProjects: "Projects",
      commandCenterCodingGaps: "Coding gaps",
      blockedRuns: "Blocked runs",
      runtimeDoctorTitle: "Runtime doctor",
      runtimeDoctorSummary: "Lightweight first-check for MIS reachability, adapter readiness, worker freshness, confirmation walls, prepared-action walls, and redaction boundaries.",
      runtimeDoctorGates: "Doctor gates",
      runtimeDoctorCommands: "Doctor commands",
      operatorHealthTitle: "Operator health",
      operatorHealthSummary: "Aggregate read-only health across loop handoff, local readiness, security, worker fleet, review queue, and action plan.",
      healthScore: "Health score",
      healthRisks: "Health risks",
      evidenceReportTitle: "Evidence report",
      evidenceReportSummary: "Run-level delivery evidence matrix across Agent Plan, approval, plan_evidence_manifest, memory review, ledger counts, pending approvals, and action receipts.",
      evidenceReportReady: "Ready runs",
      evidenceReportBlocked: "Blocked runs",
      workerKnowledge: "Worker knowledge",
      workerKnowledgeReady: "Knowledge ready",
      workerKnowledgeMissing: "Knowledge missing",
      workerKnowledgeUnavailable: "Knowledge unavailable",
      workerKnowledgePaths: "Knowledge paths",
      workerKnowledgePacket: "Packet",
      workerKnowledgeQuery: "Query",
      workerRuntimeSummary: "Runtime summary",
      workerRuntimeSummaryReady: "Runtime summaries ready",
      workerRuntimeSummaryMissing: "Runtime summaries missing",
      workerRuntimeSummaryEvents: "Summary events",
      workerRuntimeSummaryLinked: "Linked",
      workerRuntimeSummaryEvent: "Event",
      runtimeRawTraceOmitted: "raw trace omitted",
      missingManifests: "Missing manifests",
      verifiedReceipts: "Verified receipts",
      demoReadinessTitle: "Demo readiness",
      demoReadinessSummary: "Canonical v1.5 recording path: readiness, security boundary, fleet lanes, async inbox, customer task loop, and run ledger evidence.",
      productEvidencePacket: "Product evidence packet",
      productEvidenceSummary: "Copyable current-code acceptance route for non-live checks, confirmed Hermes/OpenClaw live proof, live readback, and remote worker fallback.",
      productEvidencePhases: "Evidence phases",
      manualLivePhases: "Manual live",
      isolatedDbPhases: "Isolated DB",
      demoReady: "Demo ready",
      shotsReady: "Shots ready",
      loopAuditTitle: "Loop audit",
      loopAuditSummary: "READ -> PLAN -> RETRIEVE -> COMPARE -> EXECUTE -> VERIFY -> RECORD gates across Agent Plans, evidence manifests, reviews, memory and audit.",
      loopRecordTitle: "Loop RECORD closure",
      loopRecordSummary: "Scoped readback for the latest Hermes/OpenClaw loop: memory candidates, approval blockers, review actions, and audit proof to close RECORD.",
      loopMemoryReview: "Memory review",
      loopApprovalReview: "Approval review",
      loopRecordAuditTrail: "Audit trail",
      gateEvidenceGaps: "Gaps",
      gateEvidenceProof: "Proof",
      loopChainTitle: "Latest loop chain",
      loopWorkOrderTitle: "Loop work order",
      loopWorkOrderSummary: "Copy the next gate action, verify command, and audited receipt commands from the loop action package.",
      loopLaunchContractTitle: "Loop launch contract",
      loopLaunchContractSummary: "Machine-readable launch packet for the next agent: method, evaluation exit criteria, audit contract, ledgers, and safe commands.",
      loopControlTitle: "Loop control",
      loopControlSummary: "Next safe control step derived from execution-chain status, receipt proof, and bounded runner policy.",
      recommendedStep: "Recommended step",
      controlMode: "Control mode",
      controlReadbackSource: "Readback source",
      controlReadback: "Control readback",
      controlBefore: "Before",
      controlAfter: "After",
      controlSelfCheck: "Self-check",
      cacheRefresh: "Cache refresh",
      commandSource: "Command source",
      humanRequired: "Human required",
      evaluationContract: "Evaluation contract",
      auditContract: "Audit contract",
      exitCriteria: "Exit criteria",
      requiredLedgers: "Required ledgers",
      tamperChain: "Tamper chain",
      rawContentPolicy: "Raw-content policy",
      copyLaunchPacketJson: "Copy launch packet",
      executionChain: "Execution chain",
      mutatingSteps: "Mutating steps",
      receiptSteps: "Receipt steps",
      confirmRequired: "Confirm required",
      operatorHandoffTitle: "Operator handoff",
      operatorHandoffSummary: "Read-only handoff package for Hermes, OpenClaw, Codex, or a human operator: loop work order, receipts, review state, and source proof.",
      handoffCommands: "Handoff commands",
      loopSelfCheckTitle: "Pre-advance check",
      loopSelfCheckSummary: "Copy the read-only self-check that verifies policy, receipts, evaluations, audit proof, and handoff health before advancing.",
      loopSelfCheckCopy: "Copy self-check",
      loopSelfCheckGates: "Self-check gates",
      policyContract: "Policy",
      receiptEvaluations: "Receipt eval",
      auditLedger: "Audit ledger",
      advanceLoopTitle: "Bounded advance",
      advanceLoopSummary: "Copy the local CLI runner that advances one allowlisted loop action, verifies it, and records a receipt.",
      previewAdvanceLoop: "Preview advance",
      confirmAdvanceLoop: "Confirm CLI",
      loopDriverTitle: "Hermes/OpenClaw loop driver",
      loopDriverSummary: "Copy the bounded local loop wrapper: preview is read-only; confirm advances allowlisted steps with receipts and control readback.",
      agentLoopHandoffTitle: "Agent loop handoff",
      agentLoopHandoffSummary: "Compact shared handoff for Hermes, OpenClaw, and Codex: current-code proof, fresh live evidence, Method gates, and copyable next commands.",
      loopBootstrapTitle: "Local loop bootstrap",
      loopBootstrapSummary: "Ordered startup packet for local Hermes/OpenClaw services: install preview, service-check, service closure, activation confirm, and bounded loop-driver.",
      loopBootstrapMode: "Bootstrap mode",
      loopBootstrapFast: "Fast",
      loopBootstrapDeep: "Deep",
      loopBootstrapStep: "Bootstrap step",
      serviceClosure: "Service closure",
      serviceActive: "Service active",
      loopSupervisionTitle: "Loop supervision",
      loopSupervisionSummary: "Pre-confirm gate for Hermes/OpenClaw: record pressure, bounded confirm readiness, and the next copy-only command.",
      localDeploymentGate: "Local deployment",
      deploymentRecommendedAdapter: "Recommended adapter",
      serviceManagedAdapter: "Service adapter",
      serverShellBoundary: "Server shell",
      gatewayRunStartGate: "Gateway run_start gate",
      gatewayRunStartSummary: "Agent Gateway consumes this supervision before run creation; blocked gates fail closed with 428 and create no run.",
      wouldAllowRunStart: "Would allow run_start",
      noRunOnBlock: "No run on block",
      hashBinding: "Hash binding",
      recordFirst: "Record first",
      readyToConfirm: "Ready to confirm",
      handoffReady: "Handoff ready",
      boundedConfirmReady: "Bounded confirm",
      liveDispatchReady: "Live dispatch",
      freshLiveAdapters: "Fresh live adapters",
      codexSupervisor: "Codex supervisor",
      loopDriverAgentPacket: "Agent loop packet",
      loopDriverAgentPacketSummary: "Live start-check projection for each adapter: current phase, safety gates, and next copy command.",
      methodGates: "Method gates",
      localLoopAdmission: "Local loop admission",
      daemonLoopAdmissionSummary: "Worker start/restart Method Block readback",
      liveAdapterTasks: "Live adapter tasks",
      passedAdmission: "Passed admission",
      missingAdmission: "Missing admission",
      firstSafeCommands: "First safe commands",
      confirmCommands: "Confirm commands",
      currentPhase: "Current phase",
      readyToConfirmLoop: "Ready to confirm",
      phase: "Phase",
      command: "Command",
      previewLoopDriver: "Preview loop",
      confirmLoopDriver: "Confirm loop",
      advanceLoopPolicyLabel: "Policy",
      advanceLoopPolicy: "Local CLI only; no approvals, live runs, workflow dispatch, or server shell execution.",
      handoffSources: "Sources",
      authBoundary: "Auth boundary",
      loopHealth: "Loop health",
      loopRisks: "Risks",
      copyHandoffJson: "Copy handoff JSON",
      loopRecordState: "Loop record",
      copyFirstGateIssue: "Copy first issue",
      firstGateIssue: "First issue",
      allGatesPassing: "All gates passing",
      verifyAfterAction: "Verify",
      remediationWorkflow: "Remediation workflow",
      blockedReason: "Blocked reason",
      readyReason: "Ready reason",
      nextSafeCommand: "Next safe command",
      prerequisiteStep: "Prerequisite",
      noLoopRecordItems: "No loop-specific review rows. Follow the next gate command to create a loop_record memory.",
      approveCommand: "Approve command",
      rejectCommand: "Reject command",
      loopRecordApproveConfirm: "Approve this loop RECORD review item?",
      loopRecordRejectConfirm: "Reject this loop RECORD review item?",
      scopedLoopId: "Scoped loop",
      methodBlock: "Method block",
      nextGateAction: "Next gate action",
      actionQueueTitle: "Operator action queue",
      actionQueueSummary: "Drag to reorder your next checks. Use arrows as the precise fallback.",
      actionSource: "Source",
      dragToReorder: "Drag to reorder",
      resetOrder: "Reset order",
      moveUp: "Move up",
      moveDown: "Move down",
      closeEvidenceGap: "Close gap",
      closingEvidenceGap: "Closing...",
      recordActionReceipt: "Record",
      recordVerifyReceipt: "Verify receipt",
      receiptEvaluation: "Receipt eval",
      receiptFailureMemoryTitle: "Receipt failure memory",
      receiptFailureMemorySummary: "Repeated failed receipt evaluations become reviewable failure-case memory candidates before the same recovery path is reused.",
      failureCandidates: "Failure candidates",
      failedReceipts: "Failed receipts",
      existingCandidates: "Existing candidates",
      proposeFailureMemory: "Propose failure memory",
      previewFailureMemory: "Preview memory",
      createFailureMemory: "Create candidate",
      memoryCandidateResult: "Memory candidate",
      createFailureMemoryConfirm: "Create a reviewable failure-case memory candidate from repeated failed receipt evaluations?",
      copyReceiptCommand: "Copy receipt CLI",
      copyVerifyReceiptCommand: "Copy verify CLI",
      copyActionCommand: "Copy action",
      recordingReceipt: "Recording...",
      actionReceipts: "Receipts",
      receiptProof: "Receipt",
      noReceiptProof: "No receipt",
      receiptNeeded: "Needs receipt",
      evidenceClosureLedger: "Evidence closure ledger",
      evidenceClosureSummary: "Audit readback for remediated source-run debt: closure-ready, closed, waived and reopened decisions.",
      taskIntakeTitle: "Task intake gates",
      taskIntakeSummary: "Pre-run governance for planned work: assignment, Agent Plan, knowledge retrieval, base reference and risk boundary.",
      activeIntakeGate: "Active intake gate",
      activeIntakeSummary: "Planned work is blocked before worker pull. Resolve the listed Agent Plan / knowledge gates first.",
      workerStartBlockedHint: "Worker daemon start/restart is held until intake gates pass.",
      dispatchEvidenceTitle: "Dispatch evidence proofs",
      dispatchEvidenceSummary: "Verified worker/customer dispatch runs with Agent Plan, plan evidence manifest and ledger counts.",
      copyCommand: "Copy command",
      copiedCommand: "Copied",
      reopenEvidenceGap: "Reopen",
      reopeningEvidenceGap: "Reopening...",
      closureDecision: "Decision",
      remediationState: "Remediation",
      noClosureRows: "No closure decisions yet.",
      noIntakeRows: "No planned or backlog tasks to gate.",
      intakeReady: "Ready",
      intakeBlocked: "Blocked",
      intakeAttention: "Attention",
      assignedAgents: "Assigned",
      planReferences: "Refs",
      agentPlan: "Agent Plan",
      planEvidence: "Plan evidence",
      intakeGate: "Intake gate",
      localReadinessTitle: "Local Readiness",
      localReadinessSummary: "Read-only proof that this local MIS workspace can be operated without leaking tokens or triggering live work.",
      localReadinessOverall: "Overall status",
      localRunPathTitle: "Local run path",
      localRunPathSummary: "Boot MIS, select Hermes/OpenClaw or mock, start a worker, preview service control, dispatch work, and verify ledger evidence.",
      serviceControlPreviewTitle: "Service-control preview",
      serviceControlPreviewSummary: "Preview launchd/systemd control from MIS, then verify with service-check before any confirmed OS mutation.",
      serviceCheckCommand: "Service check",
      servicePreviewCommand: "Preview control",
      evidenceChains: "Evidence chains",
      memoryApprovalCounts: "Memory / approvals",
      safetyProof: "Safety proof",
      tokenOmittedProof: "Token omitted",
      liveExecutionProof: "Live execution not performed",
      integrationInboxTitle: "Async Integration Inbox",
      integrationInboxSummary: "Commander queue for worker results arriving at different speeds: review ready work, watch running jobs, and recover blocked items.",
      commanderPlannerTitle: "Commander Work Package Planner",
      commanderPlannerSummary: "Turn one customer goal into parallel MIS work-package tasks for the AI team. Preview is safe; confirm writes planned tasks into the ledger.",
      commanderGoal: "Project goal",
      commanderMaxPackages: "Packages",
      previewPlan: "Preview plan",
      createWorkPackages: "Create work packages",
      planning: "Planning...",
      plannerResult: "Planner result",
      plannedPackages: "Planned packages",
      createdPackages: "Created tasks",
      plannerSafety: "Preview is safe",
      activeTeamBoard: "Active team board",
      activeTeamBoardSummary: "Project-scoped lanes, owners, dependencies and evidence gates for the current AI-team project.",
      teamLanes: "Team lanes",
      dependencyEdges: "Dependencies",
      missingCodingEvidence: "Missing coding evidence",
      teamReadyForReview: "Ready for review",
      activeWorkflowJobs: "Active jobs",
      failedWorkflowJobs: "Failed jobs",
      completedWorkflowJobs: "Completed jobs",
      latestWorkflowJob: "Latest job",
      queueReadback: "Queue readback",
      jobsCreated: "Jobs created",
      afterQueueActive: "After queue active",
      afterQueueFailed: "After queue failed",
      afterQueueCompleted: "After queue completed",
      retryWorkflowJob: "Retry job",
      retryingWorkflowJob: "Retrying...",
      markLaneJobFailed: "Mark job failed",
      persistedPackages: "Persisted packages",
      packageReadback: "Package readback",
      packageStatus: "Package status",
      dispatchPackage: "Run package",
      dispatchPackageMock: "Run mock",
      dispatchPackageHermes: "Run Hermes",
      dispatchPackageOpenClaw: "Run OpenClaw",
      dispatchBatchMock: "Queue planned mock batch",
      synthesizePackages: "Create synthesis report",
      promoteSynthesis: "Promote approved synthesis",
      synthesisLoop: "Synthesis loop",
      reviewQueueTitle: "Human Review Queue",
      reviewQueueSummary: "One operator queue for approvals, memory candidates and customer deliveries. Handle returned work first without waiting for every worker lane.",
      reviewQueueEmpty: "No review items. Keep dispatching or watch the async inbox.",
      reviewActionResult: "Review action",
      reviewApprove: "Approve",
      reviewReject: "Reject",
      reviewReadbackProof: "Queue readback",
      reviewDecisionAuditProof: "Decisions write audit",
      pendingApprovals: "Pending approvals",
      memoryCandidates: "Memory candidates",
      failedBenchmarks: "Failed benchmarks",
      waitingDeliveries: "Waiting deliveries",
      returnedItems: "Returned items",
      cliAction: "CLI action",
      alternateAction: "Alternative",
      customerDeliveryBoardTitle: "Customer Delivery Board",
      customerDeliveryBoardSummary: "Read-only customer-facing board: delivery artifact, linked task/run, approvals, evaluations, audit evidence, and next action.",
      planEvidenceGate: "Plan evidence gate",
      planEvidenceVerified: "Verified",
      planEvidenceBlocked: "Blocked",
      planEvidenceMissing: "Missing manifest",
      loopLaneTitle: "Hermes/OpenClaw Loop Lane",
      loopLaneSummary: "Run a supervised dry-run review loop, then inspect parent/child runs, plans, artifacts, audit and plan evidence manifests.",
      loopTopic: "Loop topic",
      loopId: "Loop ID",
      runLoopLane: "Run safe loop",
      resumeLoopLane: "Resume loop",
      loopRunning: "Loop running...",
      loopReadback: "Loop readback",
      verifiedManifests: "Verified manifests",
      blockedManifests: "Blocked manifests",
      parentRun: "Parent run",
      deliveriesReady: "Ready",
      deliveriesWaiting: "Waiting approval",
      deliveriesAttention: "Needs attention",
      deliveryEmpty: "No customer deliveries yet. Dispatch a customer worker task first.",
      deliverySafeReadback: "Safe readback",
      openReport: "Open report",
      inboxFilter: "Queue view",
      inboxAll: "All",
      readyForReview: "Ready",
      stillRunning: "Running",
      blockedItems: "Blocked",
      lateOrStale: "Late/stale",
      memoryReview: "Memory review",
      inboxEmpty: "No async integration items yet.",
      readOnlyProof: "Read-only",
      ledgerMutationProof: "Ledger unchanged",
      rawPromptProof: "Raw prompt omitted",
      itemAge: "Age",
      itemOwner: "Owner",
      itemBucket: "Bucket",
      integrationDecision: "Decision",
      integrationReason: "Reason",
      integrationAutoApply: "Auto-apply",
      integrationLedgerDecision: "Ledger decision",
      canAdvanceWithoutWaiting: "Can advance without waiting",
      overallFleetHealth: "Fleet health",
      fleetHygieneTitle: "Fleet hygiene",
      fleetHygieneSummary: "Plan or confirm cleanup for stale running worker tasks, never-seen remote enrollments, and heartbeat-stale enrollments. Cleanup writes audit/runtime evidence and never runs live adapters.",
      hygienePlan: "Plan cleanup",
      hygieneApply: "Confirm cleanup",
      hygieneRunning: "Checking...",
      hygieneActions: "Actions",
      staleNeverSeen: "Never-seen enrollments",
      staleHeartbeat: "Stale heartbeats",
      releasedTasks: "Released",
      revokedEnrollments: "Revoked",
      hygieneNoActions: "No cleanup needed.",
      hygieneSafety: "No live execution",
      healthGates: "Health gates",
      recommendedActions: "Recommended actions",
      noRecommendedActions: "No urgent action. Keep monitoring the worker status.",
      remoteWorkersTitle: "Remote heartbeat/session",
      recentRemoteSessionsTitle: "Recent sessions",
      totalEnrollments: "Total enrollments",
      heartbeatFresh: "Fresh",
      heartbeatStale: "Stale",
      heartbeatNeverSeen: "Never seen",
      workflowRecovery: "Workflow recovery",
      recoveryRefs: "Recovery refs",
      contract: "Contract",
      noRemoteWorkers: "No remote workers enrolled.",
      daemonBackoff: "Backoff",
      noBackoff: "none",
      gatewayTitle: "Agent Gateway",
      gatewaySummary: "Machine-facing API/CLI layer for local and remote agents.",
      authMode: "Auth mode",
      authenticated: "Authenticated",
      productionSecurity: "Production security",
      productionSecurityWarning: "Production security boundary",
      productionSecurityWarningSummary: "Shared/production use must pass admin write guard, scoped Agent Gateway auth, and startup security before live operators rely on this panel.",
      productionReady: "Production ready",
      localDevOnly: "Local demo only",
      securityGate: "Security gate",
      localWriteGuard: "Local write guard",
      localWriteGuardSummary: "Browser/local POST and PATCH writes must use the admin key before shared deployment.",
      deploymentMode: "Deployment mode",
      startupSecurity: "Startup security",
      gatewayWorkspace: "Workspace",
      gatewayScopes: "Allowed scopes",
      activeEnrollments: "Active enrollments",
      staleEnrollments: "Stale heartbeats",
      activeSessions: "Active sessions",
      yes: "Yes",
      no: "No",
      runs: "Runs",
      success: "Success",
      approvals: "Approvals",
      budget: "Budget",
      more: "more",
      workerTitle: "Local Worker Loop",
      workerSummary: "Pulls normal MIS tasks, executes through mock / Hermes / OpenClaw, and writes run · tool · eval · audit.",
      customerTaskTitle: "Customer Task Dispatch",
      customerTaskSummary: "Create a normal MIS task, let an agent worker execute it through the selected adapter, then inspect ledger evidence.",
      taskTitleLabel: "Task title",
      taskDescriptionLabel: "Task description",
      adapterLabel: "Runtime adapter",
      runSafeTask: "Run safe plan",
      confirmLiveTask: "Confirm live run",
      submitAsyncTask: "Submit async job",
      customerTaskRunning: "Running task...",
      executionModeTitle: "Execution mode",
      executionModeSummary: "One read-only strip for the current customer task path: dry-run, live confirmation, adapter readiness, approval wall, and async job state.",
      selectedExecutionPath: "Selected path",
      currentAdapter: "Current adapter",
      dryRunMode: "dry-run / mock",
      liveConfirmedMode: "live confirmed",
      liveConfirmMissingMode: "live confirmation required",
      adapterBlockedMode: "adapter route blocked",
      approvalWaitingMode: "approval waiting",
      asyncJobsMode: "async jobs",
      confirmRunWall: "Confirm-run wall",
      preparedActionWall: "Prepared-action wall",
      selectedRoute: "Selected route",
      confirmLiveHint: "Hermes/OpenClaw require explicit confirmation before live execution. Mock is the safe default.",
      liveRuntimeConfirmLabel: "I understand this will run a real local Hermes/OpenClaw adapter and write ledger evidence.",
      liveRuntimeConfirmRequired: "Live adapter confirmation required",
      liveRuntimeConfirmed: "Live adapter confirmed",
      asyncTaskHint: "Use async jobs for long Hermes/OpenClaw work; the ledger records job status, run, artifact, eval and audit evidence.",
      selectedAdapterReady: "Selected route is ready for this dispatch.",
      selectedAdapterBlocked: "Selected live route is not ready. Use the next action before confirming a real run.",
      taskId: "Task",
      jobId: "Job",
      jobType: "Workflow",
      runId: "Run",
      artifactId: "Artifact",
      evidence: "Evidence",
      openTask: "Open task",
      openRun: "Open run",
      workflowJobsTitle: "Async Workflow Jobs",
      workflowJobsSummary: "Recent customer worker/template jobs submitted through the machine-facing Agent Gateway path.",
      noWorkflowJobs: "No workflow jobs yet.",
      stuckWorkflowJobsTitle: "Stuck Workflow Jobs",
      stuckWorkflowJobsSummary: "Queued or running workflow jobs older than the recovery threshold.",
      noStuckWorkflowJobs: "No stuck workflow jobs.",
      markJobFailed: "Mark failed",
      markingJobFailed: "Marking...",
      outputSummary: "Output",
      workers: "Workers",
      completedRuns: "Completed worker runs",
      pendingTasks: "Pending worker tasks",
      stuckTasks: "Stuck tasks",
      releaseTask: "Release",
      releasingTask: "Releasing...",
      noStuckTasks: "No stuck running tasks.",
      age: "Age",
      linkedRun: "Run",
      dispatchMock: "Run mock once",
      dispatchHermes: "Run Hermes once",
      dispatchOpenClaw: "Run OpenClaw once",
      startMockDaemon: "Start mock daemon",
      startHermesDaemon: "Start Hermes daemon",
      startOpenClawDaemon: "Start OpenClaw daemon",
      restartMockDaemon: "Restart mock",
      restartHermesDaemon: "Restart Hermes",
      restartOpenClawDaemon: "Restart OpenClaw",
      stopDaemons: "Stop daemons",
      hostManaged: "Host managed",
      apiManaged: "Console managed",
      hostManagedHint: "Managed by AgentOps Host. Use the Host lifecycle controls.",
      processVerified: "Process verified",
      processUnverified: "Process identity alert",
      dispatching: "Dispatching...",
      restarting: "Restarting...",
      starting: "Starting...",
      stopping: "Stopping...",
      recentRun: "Recent run",
      daemonStatus: "Daemon status",
      pid: "PID",
      processed: "Processed",
      iterations: "Loops",
      errors: "Errors",
      lastError: "Last error",
      statePath: "State path",
      fleetTitle: "Worker Fleet Telemetry",
      fleetSummary: "Read-only observability for local daemons and Agent Gateway events.",
      fleetLanes: "Fleet lanes",
      laneType: "Type",
      laneHealth: "Health",
      laneHeartbeat: "Heartbeat",
      laneSession: "Session",
      laneLastSeen: "Last seen",
      laneNextAction: "Next action",
      noFleetLanes: "No worker fleet lanes yet.",
      daemonLogs: "Daemon logs",
      openDaemonLogs: "Open logs",
      refreshDaemonLogs: "Refresh logs",
      daemonLogsLoading: "Loading logs...",
      daemonLogsLazyHint: "Logs are loaded only when opened, so this page stays useful even if one daemon log endpoint is slow or unavailable.",
      recentEvents: "Recent gateway events",
      logPath: "Log path",
      noLogs: "No log lines yet.",
      noEvents: "No runtime events yet.",
      eventStatus: "Status",
      eventAgent: "Agent",
      enrollmentTitle: "Remote Agent Enrollment",
      enrollmentSummary: "Issue scoped tokens for agents running on another laptop or server. The token is shown once; MIS stores only a hash.",
      enrollmentPolicyTitle: "Scope policy preview",
      enrollmentPolicySummary: "Read-only preview before token issue: risk, approval path, privileged scopes, and worker viability.",
      enrollmentDeploymentPolicy: "Deployment policy",
      enrollmentDeploymentPolicySummary: "Hosted/shared mode routes remote credentials through approval and admin issue; local mode can direct-create only low-risk observer tokens.",
      riskLevel: "Risk",
      policyType: "Policy",
      approvalPath: "Approval path",
      directCreatePath: "Direct create",
      directCreateAllowed: "Direct create allowed",
      approvalRequestRequired: "Approval required",
      adminKeyConfigured: "Admin key",
      scopeEffectsTitle: "Selected scope effects",
      scopeEffectsSummary: "Agent Gateway enforces these endpoint scopes server-side; missing permissions fail closed with HTTP 403.",
      workerViability: "Worker viability",
      workerViabilityReady: "Ready to run worker loop",
      workerViabilityBlocked: "Missing worker-loop scopes",
      requiredWorkerScopes: "Required worker scopes",
      readScopes: "Read/heartbeat",
      executionScopes: "Execution",
      evidenceWriteScopes: "Evidence writes",
      governanceScopes: "Governance",
      scopeRbacProof: "RBAC proof",
      recommendedPath: "Recommended",
      invalidScopes: "Invalid scopes",
      privilegedScopes: "Privileged",
      workerWriteScopes: "Worker writes",
      missingWorkerScopes: "Missing worker scopes",
      createToken: "Create scoped token",
      creatingToken: "Creating token...",
      requestEnrollment: "Request approval",
      requestingEnrollment: "Requesting...",
      issueApproved: "Issue approved token",
      issuingApproved: "Issuing...",
      approveRequest: "Approve",
      rejectRequest: "Reject",
      approvalRequestTitle: "Approval-gated requests",
      noApprovalRequests: "No enrollment approval requests yet.",
      requestCreated: "Enrollment request created",
      rotateToken: "Rotate",
      rotatingToken: "Rotating...",
      revokeToken: "Revoke",
      revokingToken: "Revoking...",
      scopePresets: "Permission presets",
      presetWorker: "Worker",
      presetObserver: "Observer",
      presetApproval: "Approval",
      presetFull: "Full",
      agentId: "Agent ID",
      agentName: "Display name",
      runtime: "Runtime",
      workspace: "Workspace",
      ttlDays: "TTL days",
      heartbeat: "Heartbeat timeout",
      scopes: "Scopes",
      oneTimeCredentialTitle: "One-time issued credential",
      tokenShownOnce: "Copy this token now. It will not be shown again.",
      credentialCannotBeReadAgain: "After you clear this card or refresh the page, MIS can show only the token id and hash-backed audit trail.",
      copyIssuedCredential: "Copy token",
      copiedIssuedCredential: "Copied",
      clearIssuedCredential: "Clear secret",
      launchPacket: "Remote launch packet",
      envSetup: "Environment",
      installCommand: "Install",
      verifyCommand: "Verify",
      startCheckCommand: "Start check",
      loopLaunchBriefCommand: "Launch brief",
      methodGateContract: "Method gates",
      preflightCommand: "Preflight",
      sessionCommand: "Mint session",
      heartbeatCommand: "Heartbeat",
      runOnceCommand: "Run once",
      runLoopCommand: "Run loop",
      launchdTemplate: "launchd template",
      systemdTemplate: "systemd template",
      fallbackCommand: "Repo fallback",
      recentEnrollments: "Recent enrollments",
      recentSessions: "Recent sessions",
      lastHeartbeat: "Last heartbeat",
      lastUsed: "Last used",
      expires: "Expires",
      tokenId: "Token",
      sessionId: "Session",
      parentToken: "Parent token",
      revokeSession: "Revoke session",
      revokingSession: "Revoking session...",
      noEnrollments: "No remote enrollments yet.",
      noSessions: "No short-lived sessions yet.",
      operatorTitle: "Operator Readiness",
      operatorSummary: "Use this page as the one-person company control tower: assign work, run local AI workers, enroll remote machines, and recover stuck tasks without leaving the MIS ledger.",
      modeLocalTitle: "Local worker loop",
      modeLocalBody: "Mock is safe for dry-runs; Hermes and OpenClaw buttons require explicit live confirmation and write run/tool/eval/audit evidence.",
      modeLiveTitle: "Real runtime dispatch",
      modeLiveBody: "Hermes/OpenClaw adapters execute normal MIS tasks, not isolated demos. Results are summarized, hashed, and recorded in the ledger.",
      modeRemoteTitle: "Remote agent entry",
      modeRemoteBody: "External machines use scoped enrollment tokens, short-lived sessions, heartbeat, and workspace-bound permissions.",
      modeRecoveryTitle: "Recovery queue",
      modeRecoveryBody: "Stale running worker tasks can be released back to planned; linked runs are blocked with audit evidence.",
      adapterRoutesTitle: "Adapter routes",
      adapterRoutesSummary: "Read-only route selection for agent workers before live dispatch.",
      adapterRemediationTitle: "Setup commands",
      adapterRemediationSummary: "Copy-only remediation path from worker readiness.",
      missingChecks: "Missing checks",
      recommendedAdapter: "Recommended",
      trustStatus: "Trust",
      observationLevel: "Observation",
      riskFloor: "Risk floor",
      commercialReadiness: "Commercial",
      targetResource: "Target",
      nextAction: "Next action",
      liveReady: "live ready",
      notLiveReady: "not live ready",
      statusRunning: "running",
      statusReady: "ready",
      statusConfirm: "confirm required",
      statusAttention: "attention",
      statusClear: "clear",
      statusSetup: "setup",
    },
    zh: {
      title: "AI 员工",
      summary: `${agents.length} 个已注册代理 · ${activeAgents} 个运行中 · 连接本地后端`,
      loading: "正在加载实时代理...",
      deferredLoading: "正在加载次级治理面板...",
      deferredUnavailable: "部分次级面板暂不可用",
      backendUnavailable: "本地后端不可用",
      panelLoadReady: "面板就绪",
      panelLoadUnavailable: "面板不可用",
      panelLoadLoading: "面板加载中",
      refreshPanel: "刷新面板",
      panelRefreshRunning: "面板刷新中...",
      copyPanelDiagnostics: "复制诊断",
      recordPanelDiagnostics: "诊断记账",
      panelReceiptRecorded: "面板收据",
      panelAttempts: "尝试",
      panelUpdated: "更新",
      panelLastError: "最近错误",
      refresh: "刷新实时代理",
      commandCenterTitle: "Worker Fleet 控制台",
      commandCenterSummary: "集中查看 adapter 就绪、daemon 容量、远程心跳/session、卡住恢复和下一步安全 CLI/API 动作。",
      operatorCommandCenterTitle: "Operator 指挥中心",
      operatorCommandCenterSummary: "统一汇总项目、阻塞 run、审批、交付、过期 worker、编码证据 Gate 和下一步动作的只读监督视图。",
      commandCenterActions: "BFF 动作",
      commandCenterProjects: "项目",
      commandCenterCodingGaps: "编码缺口",
      blockedRuns: "阻塞 Run",
      runtimeDoctorTitle: "Runtime Doctor",
      runtimeDoctorSummary: "轻量 first-check：检查 MIS 可达性、Adapter 就绪、远程 Worker 新鲜度、确认墙、Prepared Action 墙和脱敏边界。",
      runtimeDoctorGates: "Doctor Gate",
      runtimeDoctorCommands: "Doctor 命令",
      operatorHealthTitle: "Operator 健康",
      operatorHealthSummary: "聚合 Loop 交接、本地就绪、安全边界、Worker Fleet、评审队列和动作计划的只读健康快照。",
      healthScore: "健康分",
      healthRisks: "健康风险",
      evidenceReportTitle: "证据报告",
      evidenceReportSummary: "按 run 聚合 Agent Plan、审批、plan_evidence_manifest、记忆评审、账本计数、待审批和动作收据的交付证据矩阵。",
      evidenceReportReady: "就绪 Run",
      evidenceReportBlocked: "阻塞 Run",
      workerKnowledge: "Worker 知识",
      workerKnowledgeReady: "知识已就绪",
      workerKnowledgeMissing: "知识缺失",
      workerKnowledgeUnavailable: "知识不可用",
      workerKnowledgePaths: "知识路径",
      workerKnowledgePacket: "证据包",
      workerKnowledgeQuery: "查询",
      workerRuntimeSummary: "运行摘要",
      workerRuntimeSummaryReady: "运行摘要已就绪",
      workerRuntimeSummaryMissing: "运行摘要缺失",
      workerRuntimeSummaryEvents: "摘要事件",
      workerRuntimeSummaryLinked: "已关联",
      workerRuntimeSummaryEvent: "事件",
      runtimeRawTraceOmitted: "原始轨迹已省略",
      missingManifests: "缺失清单",
      verifiedReceipts: "已验收收据",
      demoReadinessTitle: "Demo 就绪",
      demoReadinessSummary: "v1.5 录屏主路径：本地就绪、安全边界、Fleet 队伍、异步 Inbox、客户任务闭环、Run 账本证据。",
      productEvidencePacket: "产品证据包",
      productEvidenceSummary: "可复制的 current-code 验收路线：非 live 检查、确认后的 Hermes/OpenClaw 真实证明、live 回读和远程 worker fallback。",
      productEvidencePhases: "证据阶段",
      manualLivePhases: "手动 Live",
      isolatedDbPhases: "隔离 DB",
      demoReady: "可录 Demo",
      shotsReady: "镜头就绪",
      loopAuditTitle: "Loop 审计",
      loopAuditSummary: "围绕 Agent Plan、证据清单、评审、记忆和审计账本检查 READ -> PLAN -> RETRIEVE -> COMPARE -> EXECUTE -> VERIFY -> RECORD。",
      loopRecordTitle: "Loop RECORD 闭环",
      loopRecordSummary: "最近 Hermes/OpenClaw loop 的 scoped 回读：记忆候选、审批阻塞项、评审动作和关闭 RECORD 的审计证明。",
      loopMemoryReview: "记忆评审",
      loopApprovalReview: "审批评审",
      loopRecordAuditTrail: "审计链",
      gateEvidenceGaps: "缺口",
      gateEvidenceProof: "证明",
      loopChainTitle: "最新 Loop 链路",
      loopWorkOrderTitle: "Loop 执行包",
      loopWorkOrderSummary: "从 loop action package 复制下一步 Gate 动作、验收命令和审计收据命令。",
      loopLaunchContractTitle: "Loop 启动契约",
      loopLaunchContractSummary: "给下一位 Agent 的机器可读启动包：方法、评估退出标准、审计契约、账本和安全命令。",
      loopControlTitle: "Loop 控制",
      loopControlSummary: "由执行链状态、收据证明和受限 runner 策略推导出的下一步安全控制动作。",
      recommendedStep: "推荐步骤",
      controlMode: "控制模式",
      controlReadbackSource: "回读来源",
      controlReadback: "控制回执",
      controlBefore: "推进前",
      controlAfter: "推进后",
      controlSelfCheck: "自检后",
      cacheRefresh: "缓存刷新",
      commandSource: "命令来源",
      humanRequired: "需要人工",
      evaluationContract: "评估契约",
      auditContract: "审计契约",
      exitCriteria: "退出标准",
      requiredLedgers: "必需账本",
      tamperChain: "防篡改链",
      rawContentPolicy: "原始内容规则",
      copyLaunchPacketJson: "复制启动包",
      executionChain: "执行链",
      mutatingSteps: "写入步骤",
      receiptSteps: "收据步骤",
      confirmRequired: "需要确认",
      operatorHandoffTitle: "Operator 交接包",
      operatorHandoffSummary: "给 Hermes、OpenClaw、Codex 或人工 operator 的只读交接包：Loop 执行包、收据、评审状态和来源证明。",
      handoffCommands: "交接命令",
      loopSelfCheckTitle: "推进前自检",
      loopSelfCheckSummary: "复制只读自检：推进前检查策略、收据、评估、审计证明和交接健康。",
      loopSelfCheckCopy: "复制自检",
      loopSelfCheckGates: "自检 Gate",
      policyContract: "策略",
      receiptEvaluations: "收据评估",
      auditLedger: "审计账本",
      advanceLoopTitle: "受限推进",
      advanceLoopSummary: "复制本地 CLI runner：只推进一个 allowlist loop 动作、验收并记录收据。",
      previewAdvanceLoop: "预览推进",
      confirmAdvanceLoop: "确认 CLI",
      loopDriverTitle: "Hermes/OpenClaw Loop Driver",
      loopDriverSummary: "复制受限本地 loop wrapper：预览只读；确认后只推进 allowlist 步骤，并写入收据和控制回读。",
      agentLoopHandoffTitle: "Agent Loop 交接矩阵",
      agentLoopHandoffSummary: "Hermes、OpenClaw、Codex 共享的紧凑交接包：current-code 证明、fresh live 证据、Method gate 和下一条可复制命令。",
      loopBootstrapTitle: "本地 Loop 启动包",
      loopBootstrapSummary: "给本地 Hermes/OpenClaw 服务的有序启动包：安装预览、service-check、服务闭环、激活确认和受限 loop-driver。",
      loopBootstrapMode: "启动模式",
      loopBootstrapFast: "快速",
      loopBootstrapDeep: "深度",
      loopBootstrapStep: "启动步骤",
      serviceClosure: "服务闭环",
      serviceActive: "服务活跃",
      loopSupervisionTitle: "Loop 监管",
      loopSupervisionSummary: "Hermes/OpenClaw 确认执行前的 gate：RECORD 压力、bounded confirm 状态和下一条 copy-only 命令。",
      localDeploymentGate: "本地部署 Gate",
      deploymentRecommendedAdapter: "推荐 adapter",
      serviceManagedAdapter: "服务 adapter",
      serverShellBoundary: "Server shell",
      gatewayRunStartGate: "Gateway run_start Gate",
      gatewayRunStartSummary: "Agent Gateway 会在创建 run 之前消费这个监管 gate；阻塞时返回 428，且不创建 run。",
      wouldAllowRunStart: "允许 run_start",
      noRunOnBlock: "阻塞不建 run",
      hashBinding: "Hash 绑定",
      recordFirst: "先 RECORD",
      readyToConfirm: "可确认",
      handoffReady: "交接就绪",
      boundedConfirmReady: "Bounded confirm",
      liveDispatchReady: "Live dispatch",
      freshLiveAdapters: "Fresh live adapters",
      codexSupervisor: "Codex 监督",
      loopDriverAgentPacket: "Agent Loop 机器包",
      loopDriverAgentPacketSummary: "每个 adapter 的 start-check 实时投影：当前阶段、安全闸和下一条可复制命令。",
      methodGates: "方法 Gate",
      localLoopAdmission: "本地 Loop 准入包",
      daemonLoopAdmissionSummary: "Worker 启停 Method Block 回读",
      liveAdapterTasks: "Live adapter 任务",
      passedAdmission: "已通过准入",
      missingAdmission: "缺失准入",
      firstSafeCommands: "先执行命令",
      confirmCommands: "需确认命令",
      currentPhase: "当前阶段",
      readyToConfirmLoop: "可确认推进",
      phase: "阶段",
      command: "命令",
      previewLoopDriver: "预览 Loop",
      confirmLoopDriver: "确认 Loop",
      advanceLoopPolicyLabel: "策略",
      advanceLoopPolicy: "仅本地 CLI；不审批、不 live run、不调度 workflow、不让服务端执行 shell。",
      handoffSources: "来源",
      authBoundary: "认证边界",
      loopHealth: "Loop 健康",
      loopRisks: "风险",
      copyHandoffJson: "复制交接 JSON",
      loopRecordState: "Loop 记录",
      copyFirstGateIssue: "复制首个异常",
      firstGateIssue: "首个异常",
      allGatesPassing: "全部 Gate 通过",
      verifyAfterAction: "验收",
      remediationWorkflow: "修复工作流",
      blockedReason: "阻塞原因",
      readyReason: "就绪原因",
      nextSafeCommand: "下一条安全命令",
      prerequisiteStep: "前置步骤",
      noLoopRecordItems: "暂无 loop 专属评审行；请按下一步 Gate 命令创建 loop_record 记忆。",
      approveCommand: "批准命令",
      rejectCommand: "拒绝命令",
      loopRecordApproveConfirm: "确认批准这条 Loop RECORD 评审项？",
      loopRecordRejectConfirm: "确认拒绝这条 Loop RECORD 评审项？",
      scopedLoopId: "Scoped loop",
      methodBlock: "方法块",
      nextGateAction: "下一步 Gate 动作",
      actionQueueTitle: "Operator 动作队列",
      actionQueueSummary: "拖拽调整下一步检查顺序；也可以用箭头精确移动。",
      actionSource: "来源",
      dragToReorder: "拖拽排序",
      resetOrder: "重置顺序",
      moveUp: "上移",
      moveDown: "下移",
      closeEvidenceGap: "关闭缺口",
      closingEvidenceGap: "关闭中...",
      recordActionReceipt: "记账",
      recordVerifyReceipt: "验收记账",
      receiptEvaluation: "收据评估",
      receiptFailureMemoryTitle: "失败收据记忆",
      receiptFailureMemorySummary: "重复失败的收据评估会先进入可评审 failure-case 记忆候选，再决定是否复用同一恢复路径。",
      failureCandidates: "失败候选",
      failedReceipts: "失败收据",
      existingCandidates: "已有候选",
      proposeFailureMemory: "提出失败记忆",
      previewFailureMemory: "预览记忆",
      createFailureMemory: "创建候选",
      memoryCandidateResult: "记忆候选",
      createFailureMemoryConfirm: "确认根据重复失败的收据评估创建可评审 failure-case 记忆候选？",
      copyReceiptCommand: "复制记账 CLI",
      copyVerifyReceiptCommand: "复制验收 CLI",
      copyActionCommand: "复制动作",
      recordingReceipt: "记账中...",
      actionReceipts: "收据",
      receiptProof: "收据证明",
      noReceiptProof: "暂无收据",
      receiptNeeded: "需验收收据",
      evidenceClosureLedger: "证据关闭账本",
      evidenceClosureSummary: "回读已修复源 run 债务的审计状态：待关闭、已关闭、已豁免和已重开。",
      taskIntakeTitle: "任务接收 Gate",
      taskIntakeSummary: "planned 工作的运行前治理：分派、Agent Plan、知识检索、底座引用和风险边界。",
      activeIntakeGate: "接单 Gate 生效",
      activeIntakeSummary: "已有 planned 工作在 worker pull 前被阻塞；请先处理 Agent Plan / 知识 Gate。",
      workerStartBlockedHint: "Worker 常驻启动/重启会被暂停，直到接单 Gate 通过。",
      dispatchEvidenceTitle: "派发证据证明",
      dispatchEvidenceSummary: "已验证的 worker/customer 派发 run，包含 Agent Plan、plan evidence manifest 和账本计数。",
      copyCommand: "复制命令",
      copiedCommand: "已复制",
      reopenEvidenceGap: "重开",
      reopeningEvidenceGap: "重开中...",
      closureDecision: "决策",
      remediationState: "修复",
      noClosureRows: "暂无关闭决策。",
      noIntakeRows: "暂无 planned/backlog 任务需要 Gate。",
      intakeReady: "就绪",
      intakeBlocked: "阻塞",
      intakeAttention: "需处理",
      assignedAgents: "已分派",
      planReferences: "引用",
      agentPlan: "Agent Plan",
      planEvidence: "计划证据",
      intakeGate: "接单 Gate",
      localReadinessTitle: "本地就绪",
      localReadinessSummary: "只读证明：这个本地 MIS 工作区可运行，同时不泄露 token，也不会触发真实执行。",
      localReadinessOverall: "整体状态",
      localRunPathTitle: "本地运行路径",
      localRunPathSummary: "启动 MIS，选择 Hermes/OpenClaw 或 mock，启动 worker，预览服务控制，分派任务，并验收账本证据。",
      serviceControlPreviewTitle: "服务控制预览",
      serviceControlPreviewSummary: "先从 MIS 复制 launchd/systemd 控制预览，再用 service-check 验证，确认前不改变本机服务状态。",
      serviceCheckCommand: "服务自检",
      servicePreviewCommand: "预览控制",
      evidenceChains: "证据闭环",
      memoryApprovalCounts: "记忆 / 审批",
      safetyProof: "安全证明",
      tokenOmittedProof: "Token 已省略",
      liveExecutionProof: "未执行真实任务",
      integrationInboxTitle: "异步集成 Inbox",
      integrationInboxSummary: "Commander 用来处理不同速度 worker 回报的队列：审阅已完成工作、观察运行中 job、恢复阻塞项。",
      commanderPlannerTitle: "总指挥工作包规划器",
      commanderPlannerSummary: "把一个客户目标拆成多条 MIS 工作包任务，分派给 AI 团队并行推进。预览安全不改账本；确认后才写入 planned tasks。",
      commanderGoal: "项目目标",
      commanderMaxPackages: "工作包数",
      previewPlan: "预览规划",
      createWorkPackages: "创建工作包",
      planning: "规划中...",
      plannerResult: "规划结果",
      plannedPackages: "规划工作包",
      createdPackages: "已创建任务",
      plannerSafety: "预览安全",
      activeTeamBoard: "当前团队项目板",
      activeTeamBoardSummary: "按当前项目展示 lanes、负责人、依赖和证据 Gate，避免混进全局最近任务。",
      teamLanes: "团队 lanes",
      dependencyEdges: "依赖边",
      missingCodingEvidence: "缺失编码证据",
      teamReadyForReview: "待复核",
      activeWorkflowJobs: "活跃 Job",
      failedWorkflowJobs: "失败 Job",
      completedWorkflowJobs: "完成 Job",
      latestWorkflowJob: "最新 Job",
      queueReadback: "排队读回",
      jobsCreated: "已创建 Job",
      afterQueueActive: "排队后活跃",
      afterQueueFailed: "排队后失败",
      afterQueueCompleted: "排队后完成",
      retryWorkflowJob: "重试 Job",
      retryingWorkflowJob: "正在重试...",
      markLaneJobFailed: "标记 Job 失败",
      persistedPackages: "持久化工作包",
      packageReadback: "工作包读回",
      packageStatus: "工作包状态",
      dispatchPackage: "运行工作包",
      dispatchPackageMock: "运行 mock",
      dispatchPackageHermes: "运行 Hermes",
      dispatchPackageOpenClaw: "运行 OpenClaw",
      dispatchBatchMock: "批量排队 planned mock",
      synthesizePackages: "生成合并报告",
      promoteSynthesis: "晋升已批准报告",
      synthesisLoop: "合并闭环",
      reviewQueueTitle: "人工审核队列",
      reviewQueueSummary: "把审批、候选记忆和客户交付聚合成一个 operator 队列；哪个 worker 先回来，就先处理哪个。",
      reviewQueueEmpty: "暂无待审事项。可以继续派发任务，或观察异步 Inbox。",
      reviewActionResult: "审核动作",
      reviewApprove: "批准",
      reviewReject: "拒绝",
      reviewReadbackProof: "队列读取",
      reviewDecisionAuditProof: "决策写入审计",
      pendingApprovals: "待审批",
      memoryCandidates: "候选记忆",
      failedBenchmarks: "失败基准",
      waitingDeliveries: "待交付审批",
      returnedItems: "已返回事项",
      cliAction: "CLI 动作",
      alternateAction: "备用动作",
      customerDeliveryBoardTitle: "客户交付看板",
      customerDeliveryBoardSummary: "只读客户视角：交付 artifact、关联 task/run、审批、评估、审计证据和下一步动作。",
      planEvidenceGate: "计划证据门禁",
      planEvidenceVerified: "已验证",
      planEvidenceBlocked: "未通过",
      planEvidenceMissing: "缺少 manifest",
      loopLaneTitle: "Hermes/OpenClaw 循环 Lane",
      loopLaneSummary: "运行一个受监督的安全 dry-run 评审循环，然后查看父子 run、计划、artifact、审计和计划证据 manifest。",
      loopTopic: "循环主题",
      loopId: "Loop ID",
      runLoopLane: "运行安全循环",
      resumeLoopLane: "继续循环",
      loopRunning: "循环运行中...",
      loopReadback: "循环回读",
      verifiedManifests: "已验证 manifest",
      blockedManifests: "阻塞 manifest",
      parentRun: "父 Run",
      deliveriesReady: "可交付",
      deliveriesWaiting: "待审批",
      deliveriesAttention: "需处理",
      deliveryEmpty: "暂无客户交付。请先派发一个客户 worker 任务。",
      deliverySafeReadback: "安全只读",
      openReport: "打开报告",
      inboxFilter: "队列视角",
      inboxAll: "全部",
      readyForReview: "待审阅",
      stillRunning: "运行中",
      blockedItems: "阻塞",
      lateOrStale: "超时/陈旧",
      memoryReview: "记忆审查",
      inboxEmpty: "暂无异步集成事项。",
      readOnlyProof: "只读",
      ledgerMutationProof: "账本未修改",
      rawPromptProof: "原始 Prompt 已省略",
      itemAge: "耗时",
      itemOwner: "负责人",
      itemBucket: "分组",
      integrationDecision: "集成决策",
      integrationReason: "原因",
      integrationAutoApply: "自动应用",
      integrationLedgerDecision: "账本决策",
      canAdvanceWithoutWaiting: "可不等待推进",
      overallFleetHealth: "Fleet 健康",
      fleetHygieneTitle: "Fleet 清理",
      fleetHygieneSummary: "为卡住的运行中任务、从未心跳的远程接入、心跳过期的远程接入生成清理计划；确认清理会写入审计/runtime 证据，但不会触发真实 adapter 执行。",
      hygienePlan: "只读计划",
      hygieneApply: "确认清理",
      hygieneRunning: "检查中...",
      hygieneActions: "可处理项",
      staleNeverSeen: "未连接接入",
      staleHeartbeat: "心跳过期接入",
      releasedTasks: "已释放",
      revokedEnrollments: "已吊销",
      hygieneNoActions: "暂无需要清理的项目。",
      hygieneSafety: "不执行真实任务",
      healthGates: "健康 Gate",
      recommendedActions: "推荐动作",
      noRecommendedActions: "暂无紧急动作，继续观察 worker status。",
      remoteWorkersTitle: "远程心跳 / Session",
      recentRemoteSessionsTitle: "最近 Session",
      totalEnrollments: "接入总数",
      heartbeatFresh: "新鲜",
      heartbeatStale: "过期",
      heartbeatNeverSeen: "未连接",
      workflowRecovery: "Workflow 恢复",
      recoveryRefs: "恢复引用",
      contract: "执行契约",
      noRemoteWorkers: "暂无远程 worker 接入。",
      daemonBackoff: "退避",
      noBackoff: "无",
      gatewayTitle: "Agent Gateway",
      gatewaySummary: "给本地和远程 agent 使用的 API/CLI 接入层。",
      authMode: "认证模式",
      authenticated: "已认证",
      productionSecurity: "生产安全",
      productionSecurityWarning: "生产安全边界",
      productionSecurityWarningSummary: "共享/生产使用前，Admin 写保护、Agent Gateway 范围认证和启动安全必须通过，操作员才能依赖此面板。",
      productionReady: "生产就绪",
      localDevOnly: "仅本地演示",
      securityGate: "安全 Gate",
      localWriteGuard: "本地写保护",
      localWriteGuardSummary: "共享部署前，浏览器/本地 POST 与 PATCH 写入必须使用 Admin Key。",
      deploymentMode: "部署模式",
      startupSecurity: "启动安全",
      gatewayWorkspace: "工作区",
      gatewayScopes: "权限数量",
      activeEnrollments: "有效接入",
      staleEnrollments: "心跳过期",
      activeSessions: "有效 Session",
      yes: "是",
      no: "否",
      runs: "运行",
      success: "成功率",
      approvals: "审批",
      budget: "预算",
      more: "项更多",
      workerTitle: "本地 Worker 循环",
      workerSummary: "自动拉取普通 MIS 任务，通过 mock / Hermes / OpenClaw 执行，并写回 run · tool · eval · audit。",
      customerTaskTitle: "客户任务派发",
      customerTaskSummary: "创建一个普通 MIS 任务，让 agent worker 通过选定 adapter 执行，再查看账本证据。",
      taskTitleLabel: "任务标题",
      taskDescriptionLabel: "任务描述",
      adapterLabel: "运行 adapter",
      runSafeTask: "安全运行 / dry-run",
      confirmLiveTask: "确认真实运行",
      submitAsyncTask: "异步提交 Job",
      customerTaskRunning: "任务运行中...",
      executionModeTitle: "执行模式",
      executionModeSummary: "只读汇总当前客户任务路径：dry-run、真实运行确认、adapter 就绪、审批墙和异步 Job 状态。",
      selectedExecutionPath: "当前路径",
      currentAdapter: "当前 adapter",
      dryRunMode: "dry-run / mock",
      liveConfirmedMode: "已确认真实运行",
      liveConfirmMissingMode: "需要真实运行确认",
      adapterBlockedMode: "adapter 路由阻塞",
      approvalWaitingMode: "等待审批",
      asyncJobsMode: "异步 Job",
      confirmRunWall: "Confirm-run 墙",
      preparedActionWall: "Prepared-action 墙",
      selectedRoute: "当前路由",
      confirmLiveHint: "Hermes/OpenClaw 真实执行前必须显式确认。mock 是安全默认。",
      liveRuntimeConfirmLabel: "我确认这会运行真实本地 Hermes/OpenClaw adapter，并写入账本证据。",
      liveRuntimeConfirmRequired: "需要确认真实 adapter",
      liveRuntimeConfirmed: "真实 adapter 已确认",
      asyncTaskHint: "长时间 Hermes/OpenClaw 工作建议用异步 Job；账本会记录 job 状态、run、artifact、评估和审计证据。",
      selectedAdapterReady: "当前选中的路由可以用于这次派发。",
      selectedAdapterBlocked: "当前真实运行路由未就绪。请先执行下一步动作，再确认真跑。",
      taskId: "任务",
      jobId: "Job",
      jobType: "工作流",
      runId: "Run",
      artifactId: "Artifact",
      evidence: "证据",
      openTask: "打开任务",
      openRun: "打开 Run",
      workflowJobsTitle: "异步 Workflow Jobs",
      workflowJobsSummary: "最近通过 Agent Gateway 机器接口提交的客户 worker / 模板任务。",
      noWorkflowJobs: "还没有异步任务。",
      stuckWorkflowJobsTitle: "卡住的 Workflow Jobs",
      stuckWorkflowJobsSummary: "超过恢复阈值仍处于 queued/running 的 workflow job。",
      noStuckWorkflowJobs: "暂无卡住的 workflow job。",
      markJobFailed: "标记 failed",
      markingJobFailed: "正在标记...",
      outputSummary: "输出",
      workers: "Worker",
      completedRuns: "已完成 worker run",
      pendingTasks: "待处理 worker 任务",
      stuckTasks: "卡住任务",
      releaseTask: "释放回队列",
      releasingTask: "正在释放...",
      noStuckTasks: "暂无卡住的运行中任务。",
      age: "已运行",
      linkedRun: "Run",
      dispatchMock: "运行 mock 单轮",
      dispatchHermes: "运行 Hermes 单轮",
      dispatchOpenClaw: "运行 OpenClaw 单轮",
      startMockDaemon: "启动 mock 常驻",
      startHermesDaemon: "启动 Hermes 常驻",
      startOpenClawDaemon: "启动 OpenClaw 常驻",
      restartMockDaemon: "重启 mock",
      restartHermesDaemon: "重启 Hermes",
      restartOpenClawDaemon: "重启 OpenClaw",
      stopDaemons: "停止常驻 worker",
      hostManaged: "主机托管",
      apiManaged: "控制台托管",
      hostManagedHint: "由 AgentOps 主机托管，请使用主机生命周期控制。",
      processVerified: "进程已核验",
      processUnverified: "进程身份异常",
      dispatching: "正在派发...",
      restarting: "正在重启...",
      starting: "正在启动...",
      stopping: "正在停止...",
      recentRun: "最近 run",
      daemonStatus: "常驻状态",
      pid: "进程",
      processed: "已处理",
      iterations: "轮询",
      errors: "错误",
      lastError: "最近错误",
      statePath: "状态路径",
      fleetTitle: "Worker Fleet 观测",
      fleetSummary: "只读查看本地 daemon 日志和 Agent Gateway 最近事件。",
      fleetLanes: "Fleet 队伍",
      laneType: "类型",
      laneHealth: "健康",
      laneHeartbeat: "心跳",
      laneSession: "Session",
      laneLastSeen: "最近出现",
      laneNextAction: "下一步动作",
      noFleetLanes: "暂无 worker fleet 队伍。",
      daemonLogs: "Daemon 日志",
      openDaemonLogs: "打开日志",
      refreshDaemonLogs: "刷新日志",
      daemonLogsLoading: "日志加载中...",
      daemonLogsLazyHint: "日志只在打开时加载；即使某个 daemon 日志端点慢或不可用，页面其他部分也能继续使用。",
      recentEvents: "最近网关事件",
      logPath: "日志路径",
      noLogs: "暂无日志行。",
      noEvents: "暂无运行事件。",
      eventStatus: "状态",
      eventAgent: "Agent",
      enrollmentTitle: "远程 Agent 接入",
      enrollmentSummary: "给运行在另一台电脑或服务器上的 agent 发放带权限范围的 token。token 只显示一次，MIS 只保存 hash。",
      enrollmentPolicyTitle: "Scope 策略预览",
      enrollmentPolicySummary: "发 token 前的只读检查：风险、审批路径、高权限 scope 和 worker 可执行性。",
      enrollmentDeploymentPolicy: "部署策略",
      enrollmentDeploymentPolicySummary: "Hosted/共享模式下，远程凭证必须经过审批和管理员发放；本地模式只允许低风险 observer token 直接创建。",
      riskLevel: "风险",
      policyType: "策略",
      approvalPath: "走审批",
      directCreatePath: "直接创建",
      directCreateAllowed: "可直接创建",
      approvalRequestRequired: "需要审批",
      adminKeyConfigured: "Admin key",
      scopeEffectsTitle: "已选 scope 影响",
      scopeEffectsSummary: "Agent Gateway 在服务端执行这些 endpoint scope；缺少权限会以 HTTP 403 失败关闭。",
      workerViability: "Worker 可执行性",
      workerViabilityReady: "可运行 worker loop",
      workerViabilityBlocked: "缺少 worker-loop scope",
      requiredWorkerScopes: "Worker 必需 scope",
      readScopes: "读取 / 心跳",
      executionScopes: "执行",
      evidenceWriteScopes: "证据写入",
      governanceScopes: "治理",
      scopeRbacProof: "RBAC 证明",
      recommendedPath: "推荐路径",
      invalidScopes: "无效 scope",
      privilegedScopes: "高权限",
      workerWriteScopes: "Worker 写权限",
      missingWorkerScopes: "缺少 worker scope",
      createToken: "创建接入 token",
      creatingToken: "正在创建...",
      requestEnrollment: "提交审批申请",
      requestingEnrollment: "正在申请...",
      issueApproved: "审批后发 token",
      issuingApproved: "正在发放...",
      approveRequest: "批准",
      rejectRequest: "拒绝",
      approvalRequestTitle: "审批式接入申请",
      noApprovalRequests: "暂无远程接入审批申请。",
      requestCreated: "已创建接入申请",
      rotateToken: "轮换",
      rotatingToken: "正在轮换...",
      revokeToken: "吊销",
      revokingToken: "正在吊销...",
      scopePresets: "权限预设",
      presetWorker: "Worker",
      presetObserver: "只读观测",
      presetApproval: "审批请求",
      presetFull: "完整权限",
      agentId: "Agent ID",
      agentName: "显示名称",
      runtime: "运行时",
      workspace: "工作区",
      ttlDays: "有效天数",
      heartbeat: "心跳超时",
      scopes: "权限范围",
      oneTimeCredentialTitle: "一次性发放凭证",
      tokenShownOnce: "请现在复制 token。页面不会再次显示原始 token。",
      credentialCannotBeReadAgain: "清除此卡片或刷新页面后，MIS 只能显示 token id 和 hash 证据链，不能再次读取原始 token。",
      copyIssuedCredential: "复制 token",
      copiedIssuedCredential: "已复制",
      clearIssuedCredential: "清除密钥",
      launchPacket: "远程启动指引",
      envSetup: "环境变量",
      installCommand: "安装",
      verifyCommand: "自检",
      startCheckCommand: "启动检查",
      loopLaunchBriefCommand: "启动简报",
      methodGateContract: "方法 Gate",
      preflightCommand: "预检",
      sessionCommand: "换取短期 Session",
      heartbeatCommand: "心跳",
      runOnceCommand: "单轮运行",
      runLoopCommand: "常驻运行",
      launchdTemplate: "launchd 模板",
      systemdTemplate: "systemd 模板",
      fallbackCommand: "仓库内备用",
      recentEnrollments: "最近接入记录",
      recentSessions: "最近短期 Session",
      lastHeartbeat: "最近心跳",
      lastUsed: "最近使用",
      expires: "过期时间",
      tokenId: "Token",
      sessionId: "Session",
      parentToken: "父 token",
      revokeSession: "吊销 session",
      revokingSession: "正在吊销 session...",
      noEnrollments: "还没有远程接入记录。",
      noSessions: "还没有短期 session。",
      operatorTitle: "运营就绪",
      operatorSummary: "把这一页当成一人公司的 AI 团队控制塔：派活、运行本地 worker、接入远程机器、恢复卡住任务，全都进入 MIS 账本。",
      modeLocalTitle: "本地 worker 循环",
      modeLocalBody: "mock 用于安全 dry-run；Hermes 和 OpenClaw 按钮需要显式确认真跑，并写入 run/tool/eval/audit 证据。",
      modeLiveTitle: "真实运行派发",
      modeLiveBody: "Hermes/OpenClaw adapter 执行的是普通 MIS 任务，不是孤立演示；结果会摘要、hash，并记录到账本。",
      modeRemoteTitle: "远程 agent 接入",
      modeRemoteBody: "其他电脑或服务器用带权限范围的接入 token、短期 session、心跳和工作区绑定权限接入。",
      modeRecoveryTitle: "恢复队列",
      modeRecoveryBody: "卡住的运行中 worker 任务可以释放回 planned；关联 run 会标记 blocked 并留下审计证据。",
      adapterRoutesTitle: "Adapter 路由",
      adapterRoutesSummary: "agent worker 真跑前使用的只读选路状态。",
      adapterRemediationTitle: "设置命令",
      adapterRemediationSummary: "来自 worker readiness 的只复制修复路径。",
      missingChecks: "缺失检查",
      recommendedAdapter: "推荐",
      trustStatus: "信任",
      observationLevel: "观测等级",
      riskFloor: "风险底线",
      commercialReadiness: "商业状态",
      targetResource: "目标",
      nextAction: "下一步",
      liveReady: "可真跑",
      notLiveReady: "不可真跑",
      statusRunning: "运行中",
      statusReady: "就绪",
      statusConfirm: "需确认",
      statusAttention: "需处理",
      statusClear: "正常",
      statusSetup: "待配置",
    },
  });
  const copyIntakeCommand = async (command: string) => {
    if (!command) return;
    try {
      await navigator.clipboard?.writeText(command);
      setCopiedIntakeCommand(command);
      window.setTimeout(() => setCopiedIntakeCommand(current => current === command ? null : current), 1800);
    } catch {
      setCopiedIntakeCommand(null);
    }
  };
  const copyIssuedCredential = async () => {
    if (!createdToken?.token) return;
    try {
      await navigator.clipboard?.writeText(createdToken.token);
      setIssuedCredentialCopied(true);
      setCreatedToken(current => current ? { ...current, token: "" } : current);
    } catch {
      setIssuedCredentialCopied(false);
    }
  };
  const panelLoadState = data?.panelLoadState || {};
  const panelStatus = (panelId: string) => {
    if (panelLoadState[panelId]?.status === "unavailable") return "unavailable";
    if (panelLoadState[panelId]?.status === "running") return "running";
    if (panelLoadState[panelId]?.status === "ready") return "ready";
    return loading || deferredLoading ? "running" : "unknown";
  };
  const panelStatusLabel = (panelId: string) => {
    const status = panelStatus(panelId);
    if (status === "ready") return copy.panelLoadReady;
    if (status === "unavailable") return copy.panelLoadUnavailable;
    if (status === "running") return copy.panelLoadLoading;
    return status;
  };
  const panelStatusBadge = (panelId: string) => (
    <StatusBadge status={panelStatus(panelId)} label={panelStatusLabel(panelId)} />
  );
  const panelDiagnosticJson = (panelId: string) => {
    const state = panelLoadState[panelId] || { id: panelId, status: panelStatus(panelId) };
    return JSON.stringify({
      panel_diagnostics_json: true,
      panel_id: panelId,
      panel_status: state.status,
      panel_attempts: state.attempts ?? 0,
      panel_updated_at: state.updated_at || null,
      panel_last_action: state.last_action || null,
      panel_last_error: state.last_error || state.error || null,
      token_omitted: true,
    }, null, 2);
  };
  const panelEvidenceText = (panelId: string) => {
    const state = panelLoadState[panelId];
    if (!state) return "";
    const updatedAt = state.updated_at ? new Date(state.updated_at) : null;
    const updatedLabel = updatedAt && !Number.isNaN(updatedAt.getTime()) ? updatedAt.toLocaleTimeString() : state.updated_at;
    const lastError = state.last_error || state.error;
    const parts = [
      `${copy.panelAttempts}: ${state.attempts ?? 0}`,
      updatedLabel ? `${copy.panelUpdated}: ${updatedLabel}` : "",
      lastError ? `${copy.panelLastError}: ${lastError.slice(0, 140)}` : "",
    ].filter(Boolean);
    return parts.join(" · ");
  };
  const panelEvidenceLine = (panelId: string) => {
    const evidence = panelEvidenceText(panelId);
    if (!evidence) return null;
    return <p className="text-[9px] mt-0.5 max-w-4xl truncate" style={{ color: "var(--mis-muted)" }}>{evidence}</p>;
  };
  const panelDiagnosticReceiptStatus = (panelId: string): "recorded" | "verified" | "failed" => {
    const status = panelStatus(panelId);
    if (status === "ready") return "verified";
    if (status === "unavailable") return "failed";
    return "recorded";
  };
  const panelDiagnosticSummary = (panelId: string) => {
    const state = panelLoadState[panelId];
    const lastError = (state?.last_error || state?.error || "").replace(/Bearer\s+\S+|agtok_[A-Za-z0-9_]+|agtsess_[A-Za-z0-9_]+|sk-[A-Za-z0-9]{8,}|ntn_[A-Za-z0-9]{8,}/g, "[redacted]");
    return [
      `panel=${panelId}`,
      `status=${panelStatus(panelId)}`,
      `attempts=${state?.attempts ?? 0}`,
      state?.updated_at ? `updated_at=${state.updated_at}` : "",
      lastError ? `last_error=${lastError.slice(0, 96)}` : "",
      "token_omitted=true",
    ].filter(Boolean).join("; ");
  };
  const recordPanelDiagnosticReceipt = async (panelId: string) => {
    const actionKey = `panel-diagnostics:${panelId}`;
    setPanelReceiptAction(panelId);
    setDispatchResult(null);
    try {
      const result = await recordOperatorActionReceipt({
        action_command: `ui://workspace/agents/panel/${panelId}:refresh`,
        verify_command: "agentops operator action-receipts --limit 20",
        action_id: `ui_panel_diagnostics:${panelId}`,
        action_signature: `ui_panel_diagnostics:${panelId}`,
        source: "ui.panel_diagnostics",
        status: panelDiagnosticReceiptStatus(panelId),
        result_summary: panelDiagnosticSummary(panelId),
      });
      setDispatchResult(`${copy.panelReceiptRecorded}: ${result.status} · ${result.receipt?.receipt_id || actionKey}`);
      await refreshPanel("operator_action_receipts");
      await refreshPanel("operator_action_plan");
      await refreshPanel("operator_loop_audit");
      await refreshPanel("operator_handoff");
    } catch (err) {
      setDispatchResult(err instanceof Error ? err.message : String(err));
    } finally {
      setPanelReceiptAction((current) => current === panelId ? null : current);
    }
  };
  const panelDiagnosticsButton = (panelId: string) => (
    <button
      onClick={() => void copyIntakeCommand(panelDiagnosticJson(panelId))}
      className="inline-flex items-center justify-center h-5 w-5 rounded"
      style={{ color: "var(--mis-muted)", background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}
      title={copy.copyPanelDiagnostics}
      aria-label={copy.copyPanelDiagnostics}
    >
      <Copy size={9} />
    </button>
  );
  const executionModeSummary = operatorExecutionMode?.summary;
  const executionModeRoute = operatorExecutionMode?.selected_route;
  const executionModeGateById = Object.fromEntries((operatorExecutionMode?.gates || []).map((gate) => [gate.id, gate]));
  const activeWorkflowJobCount = Number(executionModeSummary?.active_workflow_jobs ?? workflowJobs.filter((job) => ["queued", "running", "submitted", "planned"].includes(String(job.status || ""))).length);
  const pendingApprovalCount = Number(executionModeSummary?.pending_approvals ?? reviewQueueSummary?.pending_approvals ?? operatorEvidenceSummary?.pending_approvals ?? customerDeliverySummary?.waiting_approval ?? 0);
  const fallbackSelectedExecutionStatus = selectedAdapterLiveBlocked
    ? "blocked"
    : selectedAdapterLiveConfirmMissing
      ? "attention"
      : customerTaskForm.adapter === "mock"
        ? "planned"
        : "pass";
  const selectedExecutionStatus = operatorExecutionMode?.status || fallbackSelectedExecutionStatus;
  const fallbackSelectedExecutionLabel = selectedAdapterLiveBlocked
    ? copy.adapterBlockedMode
    : selectedAdapterLiveConfirmMissing
      ? copy.liveConfirmMissingMode
      : customerTaskForm.adapter === "mock"
        ? copy.dryRunMode
        : copy.liveConfirmedMode;
  const selectedExecutionLabel = operatorExecutionMode?.mode
    ? `${operatorExecutionMode.mode} · ${operatorExecutionMode.selected_path || executionModeSummary?.selected_path || fallbackSelectedExecutionLabel}`
    : fallbackSelectedExecutionLabel;
  const lastWorkerResultWithRunStartGate = lastWorkerDispatch?.worker_result?.results?.find((result) => result.loop_supervision_gate);
  const lastWorkerRunStartGate = lastWorkerDispatch?.loop_supervision_gate || lastWorkerResultWithRunStartGate?.loop_supervision_gate || null;
  const lastWorkerRunStartGateSafety = lastWorkerRunStartGate?.safety || {};
  const lastWorkerRunStartGateHash = lastWorkerRunStartGate?.supervision_hash ? String(lastWorkerRunStartGate.supervision_hash).slice(0, 12) : "—";
  const lastWorkerRunStartRecommendedNext = String(
    lastWorkerRunStartGate?.recommended_next ||
    lastWorkerRunStartGate?.commands?.recommended_next ||
    lastWorkerRunStartGate?.commands?.record_review ||
    ""
  );
  const lastWorkerRunStartReceiptAction = lastWorkerDispatch && lastWorkerRunStartGate
    ? `run-start-gate-readback:${String(lastWorkerRunStartGate.adapter || lastWorkerRunStartGate.runtime_type || lastWorkerDispatch.adapter || "mock")}:${lastWorkerDispatch.task_id}`
    : "";
  const selectedRouteDetail = executionModeRoute
    ? `${executionModeRoute.readiness || "unknown"} · ${executionModeRoute.trust_status || "trust:unknown"}`
    : customerTaskForm.adapter === "mock"
    ? copy.dryRunMode
    : `${selectedAdapterRoute?.readiness || "unknown"} · ${selectedAdapterRoute?.trust_status || "trust:unknown"}`;
  const executionModeCommand = operatorExecutionMode?.commands?.execution_mode
    || executionModeRoute?.recommended_action
    || selectedAdapterRoute?.recommended_action
    || runtimeDoctorCommands.worker_readiness
    || "agentops worker readiness";
  const executionModeCards = [
    {
      id: "execution-mode-selected-path",
      label: copy.selectedExecutionPath,
      value: operatorExecutionMode?.selected_path || executionModeSummary?.selected_path || selectedExecutionLabel,
      status: selectedExecutionStatus,
    },
    {
      id: "execution-mode-current-adapter",
      label: copy.currentAdapter,
      value: `${operatorExecutionMode?.adapter || customerTaskForm.adapter} · ${selectedRouteDetail}`,
      status: executionModeGateById.selected_adapter_route?.status || (selectedAdapterIsReady ? "pass" : "blocked"),
    },
    {
      id: "execution-mode-confirm-run-wall",
      label: copy.confirmRunWall,
      value: executionModeSummary?.confirm_run_wall || (selectedAdapterNeedsLiveConfirm ? (liveRuntimeConfirmed ? copy.liveRuntimeConfirmed : copy.liveRuntimeConfirmRequired) : copy.dryRunMode),
      status: executionModeGateById.confirm_run_wall?.status || (selectedAdapterNeedsLiveConfirm ? (liveRuntimeConfirmed ? "pass" : "attention") : "planned"),
    },
    {
      id: "execution-mode-prepared-action-wall",
      label: copy.preparedActionWall,
      value: executionModeSummary?.prepared_action_wall || (runtimeDoctorSummary?.requires_prepared_action?.length ? runtimeDoctorSummary.requires_prepared_action.join(", ") : copy.statusClear),
      status: executionModeGateById.prepared_action_wall?.status || (runtimeDoctorSummary?.requires_prepared_action?.length ? "pass" : "planned"),
    },
    {
      id: "execution-mode-approval-waiting",
      label: copy.approvalWaitingMode,
      value: pendingApprovalCount,
      status: executionModeGateById.approval_waiting?.status || (pendingApprovalCount > 0 ? "attention" : "pass"),
    },
    {
      id: "execution-mode-async-jobs",
      label: copy.asyncJobsMode,
      value: activeWorkflowJobCount,
      status: executionModeGateById.async_jobs?.status || (activeWorkflowJobCount > 0 ? "running" : "pass"),
    },
  ];
  const panelReceiptButton = (panelId: string) => {
    const busy = panelReceiptAction === panelId;
    return (
      <button
        onClick={() => void recordPanelDiagnosticReceipt(panelId)}
        disabled={Boolean(panelReceiptAction)}
        className="inline-flex items-center justify-center h-5 w-5 rounded disabled:opacity-50"
        style={{ color: busy ? "var(--mis-cyan)" : "var(--mis-success)", background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}
        title={copy.recordPanelDiagnostics}
        aria-label={copy.recordPanelDiagnostics}
      >
        {busy ? <RefreshCw size={9} /> : <Activity size={9} />}
      </button>
    );
  };
  const panelRefreshButton = (panelId: string) => (
    <button
      onClick={() => void refreshPanel(panelId)}
      disabled={localPanelRefreshing === panelId}
      className="inline-flex items-center gap-1 text-[9px] px-1.5 py-0.5 rounded"
      style={{ color: "var(--mis-cyan)", background: "var(--mis-bg)", border: "1px solid var(--mis-border)", opacity: localPanelRefreshing === panelId ? 0.65 : 1 }}
      title={localPanelRefreshing === panelId ? copy.panelRefreshRunning : copy.refreshPanel}
    >
      <RotateCw size={9} />
      {localPanelRefreshing === panelId ? copy.panelRefreshRunning : copy.refreshPanel}
    </button>
  );
  const operatorReadiness = [
    {
      title: copy.modeLocalTitle,
      body: copy.modeLocalBody,
      status: runningDaemons > 0 ? "running" : "ready",
      label: runningDaemons > 0 ? copy.statusRunning : copy.statusReady,
      attention: false,
      meta: `${runningDaemons}/${workerStatus?.worker_count ?? 0} ${copy.workers}`,
    },
    {
      title: copy.modeLiveTitle,
      body: copy.modeLiveBody,
      status: liveReadyAdapters.length > 0 ? "ready" : "unavailable",
      label: liveReadyAdapters.length > 0 ? copy.statusReady : copy.statusAttention,
      attention: liveReadyAdapters.length === 0 || unavailableAdapters.length > 0 || blockedAdapters.length > 0,
      meta: `${liveReadyAdapters.length} ${copy.liveReady} · ${copy.recommendedAdapter}: ${recommendedAdapter}`,
    },
    {
      title: copy.modeRemoteTitle,
      body: copy.modeRemoteBody,
      status: gatewayReady && activeEnrollments > 0 ? "ready" : "planned",
      label: gatewayReady && activeEnrollments > 0 ? copy.statusReady : copy.statusSetup,
      attention: !gatewayReady || staleEnrollments > 0,
      meta: `${activeEnrollments} ${copy.activeEnrollments} · ${activeSessions} ${copy.activeSessions}`,
    },
    {
      title: copy.modeRecoveryTitle,
      body: copy.modeRecoveryBody,
      status: stuckWorkerCount > 0 || stuckWorkflowJobCount > 0 ? "blocked" : "pass",
      label: stuckWorkerCount > 0 || stuckWorkflowJobCount > 0 ? copy.statusAttention : copy.statusClear,
      attention: stuckWorkerCount > 0 || stuckWorkflowJobCount > 0,
      meta: `${stuckWorkerCount} ${copy.stuckTasks} · ${stuckWorkflowJobCount} ${copy.workflowRecovery}`,
    },
  ];
  const adapterRouteCards = WORKER_ADAPTERS.map((adapter) => {
    const item = adapterReadiness?.adapters?.[adapter] || {
      adapter: adapter as WorkerAdapterName,
      ok: false,
      readiness: "unknown",
      trust_status: "unknown",
      target_resource: "—",
      recommended_action: "agentops worker readiness",
      checks: {},
    };
    const liveReady = item.readiness === "ready" && adapter !== "mock";
    const attention = ["unavailable", "blocked"].includes(item.readiness);
    const checks = item.checks || {};
    const remediation = item.remediation;
    const remediationCommands = (remediation?.commands || []).filter(command => command.command).slice(0, 3);
    const remediationMissing = remediation?.missing || [];
    const checkSummary = adapter === "hermes"
      ? `api=${String(checks.api_listening ?? "—")} · port=${String(checks.api_port ?? "—")}`
      : adapter === "openclaw"
        ? `bin=${String(checks.binary_exists ?? "—")} · agents=${String(checks.agents_count ?? "—")}`
        : "local mock worker";
    return { item, liveReady, attention, checkSummary, remediation, remediationCommands, remediationMissing };
  });
  const integrationInboxFilters = [
    { bucket: "all", label: copy.inboxAll, count: integrationInboxSummary?.total ?? 0 },
    { bucket: "ready_for_review", label: copy.readyForReview, count: integrationInboxSummary?.ready_for_review ?? 0 },
    { bucket: "still_running", label: copy.stillRunning, count: integrationInboxSummary?.still_running ?? 0 },
    { bucket: "blocked", label: copy.blockedItems, count: integrationInboxSummary?.blocked ?? 0 },
    { bucket: "late_or_stale", label: copy.lateOrStale, count: integrationInboxSummary?.late_or_stale ?? 0 },
    { bucket: "needs_memory_review", label: copy.memoryReview, count: integrationInboxSummary?.needs_memory_review ?? 0 },
  ];
  const isCloseEvidenceGapCommand = (action: string) => action.startsWith("agentops operator close-evidence-gap --run-id ");
  const loopAuditSteps = operatorLoopAudit?.steps || [];
  const loopAuditSummary = operatorLoopAudit?.summary;
  const loopActionPackage = operatorLoopAudit?.action_package;
  const loopActionPackageItems = loopActionPackage?.items || [];
  const operatorHandoffSummary = operatorHandoff?.summary;
  const operatorHandoffCommands = operatorHandoff?.work_order?.commands || [];
  const handoffEvidenceWorkOrder = operatorHandoff?.work_order?.evidence_report;
  const handoffEvidenceReceiptState = handoffEvidenceWorkOrder?.receipt_state;
  const handoffEvidenceRemediationSummary = handoffEvidenceWorkOrder?.remediation_chain?.summary || {};
  const handoffEvidenceRemediationItems = Array.isArray(handoffEvidenceWorkOrder?.remediation_chain?.items)
    ? handoffEvidenceWorkOrder.remediation_chain.items
    : [];
  const remediationWorkflowRows = handoffEvidenceRemediationItems.slice(0, 5).map((item, index) => {
    const row = item as Record<string, unknown>;
    const nextStep = typeof row.next_workflow_step === "object" && row.next_workflow_step !== null
      ? row.next_workflow_step as Record<string, unknown>
      : {};
    const receiptState = typeof nextStep.receipt_state === "object" && nextStep.receipt_state !== null
      ? nextStep.receipt_state as Record<string, unknown>
      : {};
    return {
      key: `${String(row.run_id || "run")}-${String(nextStep.id || index)}`,
      runId: String(row.run_id || "—"),
      status: String(nextStep.status || row.status || "unknown"),
      severity: String(row.severity || row.status || "attention"),
      stepId: String(nextStep.id || "—"),
      label: String(nextStep.label || row.next_action || "—"),
      reason: String(nextStep.blocked_reason || nextStep.ready_reason || row.next_action || "—"),
      prerequisite: String(nextStep.prerequisite_step || "—"),
      nextSafeCommand: String(nextStep.next_safe_command || nextStep.command || ""),
      nextSafeCommandKind: String(nextStep.next_safe_command_kind || "—"),
      verifyCommand: String(nextStep.verify_command || row.verify_command || ""),
      receiptNextCommand: String(nextStep.receipt_next_command || nextStep.receipt_verify_record_command || nextStep.receipt_record_command || ""),
      receiptStatus: String(receiptState.status || "missing"),
      mutating: Boolean(nextStep.mutating),
      confirmRequired: Boolean(nextStep.confirm_required),
    };
  });
  const loopSelfCheckCommand = operatorHandoff?.loop_id
    ? `agentops operator loop-self-check --loop-id ${operatorHandoff.loop_id} --limit 12`
    : "agentops operator loop-self-check --limit 12";
  const loopSelfCheckGates = operatorLoopSelfCheck?.gates || {};
  const loopSelfCheckGateStatus = (gateId: string) => String((loopSelfCheckGates[gateId] || {}).status || "unknown");
  const policyContractGate = loopSelfCheckGates.policy_contract || {};
  const receiptEvaluationGate = loopSelfCheckGates.receipt_evaluations || {};
  const remediationWorkflowSelfCheckGate = loopSelfCheckGates.evidence_remediation_workflow || {};
  const auditLedgerGate = loopSelfCheckGates.audit_ledger || {};
  const localWriteGuardSelfCheckGate = loopSelfCheckGates.local_ui_write_guard || {};
  const loopSelfCheckGateSummaries = [
    {
      id: "policy_contract",
      label: copy.policyContract,
      status: loopSelfCheckGateStatus("policy_contract"),
      detail: `${String(policyContractGate.policy_id || "advance_loop_local_bounded_v1")} · shell:${String(policyContractGate.server_executes_shell ?? false)}`,
    },
    {
      id: "receipt_evaluations",
      label: copy.receiptEvaluations,
      status: loopSelfCheckGateStatus("receipt_evaluations"),
      detail: `${Number(receiptEvaluationGate.evaluated ?? 0)}/${Number(receiptEvaluationGate.required ?? 0)} · fail ${Number(receiptEvaluationGate.failed ?? 0)}`,
    },
    {
      id: "evidence_remediation_workflow",
      label: copy.remediationState,
      status: loopSelfCheckGateStatus("evidence_remediation_workflow"),
      detail: `${Number(remediationWorkflowSelfCheckGate.ready_steps ?? 0)}/${Number(remediationWorkflowSelfCheckGate.blocked_steps ?? 0)} · receipt ${Number(remediationWorkflowSelfCheckGate.receipt_missing ?? 0)}`,
    },
    {
      id: "audit_ledger",
      label: copy.auditLedger,
      status: loopSelfCheckGateStatus("audit_ledger"),
      detail: `${Number(auditLedgerGate.receipt_audit_rows ?? 0)} receipts · ${Number(auditLedgerGate.evaluation_audit_rows ?? 0)} evals`,
    },
    {
      id: "local_ui_write_guard",
      label: copy.localWriteGuard,
      status: loopSelfCheckGateStatus("local_ui_write_guard"),
      detail: `${String(localWriteGuardSelfCheckGate.gate_status || "unknown")} · shared:${String(localWriteGuardSelfCheckGate.production_requested ?? false)}`,
    },
  ];
  const advanceLoopRaw = (
    operatorHandoff?.work_order?.advance_loop &&
    typeof operatorHandoff.work_order.advance_loop === "object"
      ? operatorHandoff.work_order.advance_loop as Record<string, unknown>
      : {}
  );
  const advanceLoopSummaryRaw = typeof advanceLoopRaw.summary === "object" && advanceLoopRaw.summary !== null ? advanceLoopRaw.summary as Record<string, unknown> : {};
  const advanceLoopPolicyRaw = typeof advanceLoopRaw.policy === "object" && advanceLoopRaw.policy !== null ? advanceLoopRaw.policy as Record<string, unknown> : {};
  const advanceLoopPreviewCommand = String(advanceLoopRaw.preview_command || "agentops operator advance-loop --limit 12");
  const advanceLoopConfirmCommand = String(advanceLoopRaw.confirm_command || `${advanceLoopPreviewCommand} --confirm-advance`);
  const loopDriverPreviewCommands = [
    "agentops operator loop-driver --adapter hermes --max-steps 3 --limit 8",
    "agentops operator loop-driver --adapter openclaw --max-steps 3 --limit 8",
  ];
  const loopDriverConfirmCommands = loopDriverPreviewCommands.map(command => `${command} --confirm-loop`);
  const loopDriverCommands = [
    { label: `Hermes ${copy.previewLoopDriver}`, command: loopDriverPreviewCommands[0], color: "var(--mis-cyan)" },
    { label: `OpenClaw ${copy.previewLoopDriver}`, command: loopDriverPreviewCommands[1], color: "var(--mis-cyan)" },
    { label: `Hermes ${copy.confirmLoopDriver}`, command: loopDriverConfirmCommands[0], color: "var(--mis-warning)" },
    { label: `OpenClaw ${copy.confirmLoopDriver}`, command: loopDriverConfirmCommands[1], color: "var(--mis-warning)" },
  ];
  const loopDriverPacketItems = operatorLoopDriverPackets?.packets || [];
  const loopDriverPacketCommandItems = loopDriverPacketItems.flatMap((packet) => {
    const adapterLabel = packet.adapter === "openclaw" ? "OpenClaw" : packet.adapter === "hermes" ? "Hermes" : packet.adapter;
    return [
      { label: `${adapterLabel} start-check`, command: packet.commands.start_check || "", color: "var(--mis-success)" },
      { label: `${adapterLabel} ${copy.previewLoopDriver}`, command: packet.commands.preview_loop || "", color: "var(--mis-cyan)" },
      { label: `${adapterLabel} ${copy.confirmLoopDriver}`, command: packet.commands.confirm_loop || "", color: "var(--mis-warning)" },
    ].filter(item => item.command);
  });
  const loopDriverVisibleCommands = loopDriverPacketCommandItems.length ? loopDriverPacketCommandItems : loopDriverCommands;
  const agentLoopHandoffConsumers = operatorAgentLoopHandoff?.consumers || [];
  const agentLoopHandoffCommands = [
    { label: copy.agentLoopHandoffTitle, command: operatorAgentLoopHandoff?.codex_consumer?.commands.read_handoff || "agentops operator agent-loop-handoff --limit 8", color: "var(--mis-cyan)" },
    ...agentLoopHandoffConsumers.flatMap((consumer) => [
      { label: `${consumer.adapter} handoff`, command: consumer.commands.agent_loop_handoff || "", color: "var(--mis-cyan)" },
      { label: `${consumer.adapter} start-check`, command: consumer.commands.start_check || "", color: "var(--mis-success)" },
      { label: `${consumer.adapter} live proof`, command: consumer.commands.live_product_readiness || "", color: "var(--mis-warning)" },
    ]),
  ].filter(item => item.command).slice(0, 7);
  const loopSupervisionItems = operatorLoopSupervision?.items || [];
  const loopSupervisionCommands = [
    ...(operatorLoopSupervision?.next_actions || []).map((command, index) => ({
      label: `${copy.loopSupervisionTitle} ${index + 1}`,
      command,
      color: "var(--mis-cyan)",
    })),
    ...loopSupervisionItems.flatMap((item) => [
      { label: `${item.adapter} ${copy.readyToConfirm}`, command: item.next_commands.confirm_required_commands[0] || "", color: "var(--mis-warning)" },
      { label: `${item.adapter} ${copy.previewLoopDriver}`, command: item.next_commands.preview_commands[0] || "", color: "var(--mis-cyan)" },
      { label: `${item.adapter} ${copy.recordFirst}`, command: item.commands.record_review || "", color: "var(--mis-success)" },
    ]),
  ].filter(item => item.command).slice(0, 7);
  const operatorLoopBootstrapItems = operatorLoopBootstrap?.items || [];
  const loopBootstrapCommands = [
    ...(operatorLoopBootstrap?.next_actions || []).map((command, index) => ({
      label: `${copy.loopBootstrapTitle} ${index + 1}`,
      command,
      color: "var(--mis-cyan)",
    })),
    ...operatorLoopBootstrapItems.flatMap((item) => [
      { label: `${item.adapter} bootstrap`, command: item.commands.loop_bootstrap_cli || "", color: "var(--mis-cyan)" },
      { label: `${item.adapter} service-check`, command: item.commands.loop_bootstrap_cli_with_service_check || item.commands.service_check || "", color: "var(--mis-primary)" },
      { label: `${item.adapter} closure`, command: item.commands.service_closure_record || "", color: "var(--mis-success)" },
      { label: `${item.adapter} loop-driver`, command: item.commands.loop_driver_auto_service_closure || "", color: "var(--mis-warning)" },
    ]),
  ].filter(item => item.command).slice(0, 8);
  const advanceLoopSelectedGate = String(advanceLoopSummaryRaw.selected_gate || "—");
  const advanceLoopSelectedStatus = String(advanceLoopSummaryRaw.selected_status || advanceLoopRaw.status || "unknown");
  const advanceLoopServerShell = Boolean(advanceLoopPolicyRaw.server_executes_shell);
  const advanceLoopPolicyId = String(advanceLoopPolicyRaw.policy_id || "advance_loop_local_bounded_v1");
  const advanceLoopPolicyVersion = String(advanceLoopPolicyRaw.policy_version || "unknown");
  const handoffControlSummary = operatorHandoff?.control_summary;
  const handoffControlStep = handoffControlSummary?.recommended_step || {};
  const handoffControlCommand = String(handoffControlSummary?.next_command || handoffControlStep.command || "");
  const handoffControlVerifyCommand = String(handoffControlSummary?.verify_command || handoffControlStep.verify_command || "");
  const handoffControlReceiptCommand = String(handoffControlSummary?.receipt_command || handoffControlStep.receipt_command || "");
  const directLoopControlSummary = operatorLoopControl?.control_summary;
  const operatorHealthControlSummary = directLoopControlSummary || operatorHealth?.control_summary || handoffControlSummary;
  const directLoopControlStep = directLoopControlSummary?.recommended_step || {};
  const directLoopControlNextCommand = String(directLoopControlSummary?.next_command || directLoopControlStep.command || operatorLoopControl?.next_actions?.[0] || operatorLoopControl?.work_order?.commands?.[0] || "");
  const directLoopControlVerifyCommand = String(directLoopControlSummary?.verify_command || directLoopControlStep.verify_command || "");
  const directLoopControlReceiptCommand = String(directLoopControlSummary?.receipt_command || directLoopControlStep.receipt_command || "");
  const directLoopControlAdvance = (
    operatorLoopControl?.work_order?.advance_loop &&
    typeof operatorLoopControl.work_order.advance_loop === "object"
      ? operatorLoopControl.work_order.advance_loop
      : {}
  ) as Record<string, unknown>;
  const directLoopControlSelectedItem = (
    directLoopControlAdvance.selected_item &&
    typeof directLoopControlAdvance.selected_item === "object"
      ? directLoopControlAdvance.selected_item
      : {}
  ) as Record<string, unknown>;
  const directLoopControlPreviewCommand = String(directLoopControlAdvance.preview_command || directLoopControlNextCommand || "agentops operator loop-control --limit 8");
  const operatorHealthLoopControl = (
    operatorLoopControl ? {
      status: operatorLoopControl.status || directLoopControlSummary?.status || "unknown",
      source: "operator_loop_control",
      mode: directLoopControlSummary?.mode,
      recommended_step: directLoopControlStep.label || directLoopControlStep.step_id,
      recommended_step_status: directLoopControlStep.status || directLoopControlSelectedItem.gate_status,
      selected_gate: directLoopControlSummary?.selected_gate || directLoopControlStep.selected_gate || directLoopControlSelectedItem.gate_id,
      selected_status: directLoopControlSummary?.selected_status || directLoopControlSelectedItem.gate_status,
      next_action: directLoopControlNextCommand,
      verify_command: directLoopControlVerifyCommand,
      receipt_command: directLoopControlReceiptCommand,
      requires_human: directLoopControlSummary?.requires_human,
      requires_receipt: directLoopControlSummary?.requires_receipt,
      copy_only: directLoopControlSummary?.copy_only !== false,
      server_executes_shell: Boolean(directLoopControlSummary?.server_executes_shell || operatorLoopControl.safety.server_executes_shell),
      control_readback_source: "agentops operator advance-loop --fast-control --confirm-advance",
      token_omitted: operatorLoopControl.token_omitted,
    } :
    operatorHealth?.loop_control ||
    operatorHandoff?.loop_health?.gates?.loop_control ||
    operatorLoopSelfCheck?.gates?.loop_control ||
    {}
  ) as Record<string, unknown>;
  const loopControlGateStatus = String(operatorHealthLoopControl.status || operatorHealthControlSummary?.status || "unknown");
  const loopControlGateMode = String(operatorHealthLoopControl.mode || operatorHealthControlSummary?.mode || "unknown");
  const loopControlSelectedGate = String(operatorHealthLoopControl.selected_gate || operatorHealthControlSummary?.selected_gate || "—");
  const loopControlNextAction = String(operatorHealthLoopControl.next_action || operatorHealthControlSummary?.next_command || "");
  const loopControlVerifyAction = String(operatorHealthLoopControl.verify_command || operatorHealthControlSummary?.verify_command || "");
  const loopControlReceiptAction = String(operatorHealthLoopControl.receipt_command || operatorHealthControlSummary?.receipt_command || "");
  const loopControlReadbackSource = String(operatorHealthLoopControl.control_readback_source || "agentops operator advance-loop --confirm-advance");
  const loopControlRefreshRequired = Boolean(operatorHealthLoopControl.refresh_cache_required_after_receipt);
  const loopControlCopyOnly = operatorHealthLoopControl.copy_only === undefined ? Boolean(operatorHealthControlSummary?.copy_only) : Boolean(operatorHealthLoopControl.copy_only);
  const loopControlServerShell = Boolean(operatorHealthLoopControl.server_executes_shell || operatorHealthLoopControl.server_shell_execution || operatorHealthControlSummary?.server_executes_shell);
  const loopControlRequiresHuman = operatorHealthLoopControl.requires_human === undefined ? Boolean(operatorHealthControlSummary?.requires_human) : Boolean(operatorHealthLoopControl.requires_human);
  const loopControlRequiresReceipt = operatorHealthLoopControl.requires_receipt === undefined ? Boolean(operatorHealthControlSummary?.requires_receipt) : Boolean(operatorHealthLoopControl.requires_receipt);
  const operatorHandoffSources = operatorHandoff?.sources || {};
  const operatorHandoffJson = operatorHandoff ? JSON.stringify({
    summary: operatorHandoff.summary,
    work_order: operatorHandoff.work_order,
    control_summary: operatorHandoff.control_summary,
    receipt_state: operatorHandoff.receipt_state,
    review_state: operatorHandoff.review_state,
    loop_health: operatorHandoff.loop_health,
    sources: operatorHandoff.sources,
    contract: operatorHandoff.contract,
    auth: operatorHandoff.auth,
    safety: operatorHandoff.safety,
    token_omitted: operatorHandoff.token_omitted,
  }, null, 2) : "";
  const receiptFailureMemoryRaw = (
    operatorHandoff?.receipt_state.failure_memory ||
    operatorActionPlan?.receipt_failure_memory ||
    {}
  ) as Record<string, unknown>;
  const receiptFailureMemorySummary = (
    typeof receiptFailureMemoryRaw.summary === "object" && receiptFailureMemoryRaw.summary !== null
      ? receiptFailureMemoryRaw.summary
      : {}
  ) as Record<string, unknown>;
  const receiptFailureMemoryCandidates = Array.isArray(receiptFailureMemoryRaw.candidates)
    ? receiptFailureMemoryRaw.candidates as Record<string, unknown>[]
    : [];
  const receiptFailureMemoryNextActions = Array.isArray(receiptFailureMemoryRaw.next_actions)
    ? receiptFailureMemoryRaw.next_actions.map(String).filter(Boolean)
    : [];
  const receiptFailureMemoryCandidateCount = Number(receiptFailureMemorySummary.candidates ?? operatorPlanSummary?.receipt_failure_memory_candidates ?? operatorHandoffSummary?.receipt_failure_memory_candidates ?? 0) || 0;
  const receiptFailureMemoryFailedReceipts = Number(receiptFailureMemorySummary.failed_receipts ?? operatorPlanSummary?.receipt_failure_memory_failed_receipts ?? operatorHandoffSummary?.receipt_failure_memory_failed_receipts ?? 0) || 0;
  const receiptFailureMemoryExistingCandidates = Number(receiptFailureMemorySummary.existing_memory_candidates ?? operatorPlanSummary?.receipt_failure_memory_existing_candidates ?? operatorHandoffSummary?.receipt_failure_memory_existing_candidates ?? 0) || 0;
  const receiptFailureMemoryPrimaryHash = String(receiptFailureMemoryCandidates[0]?.action_hash || "");
  const receiptFailureMemoryNextAction = receiptFailureMemoryNextActions[0] || "agentops operator receipt-failure-memories --min-failures 2 --limit 8";
  const loopAuditNextAction = operatorLoopAudit?.next_actions?.[0] || "agentops operator loop-audit --limit 20";
  const firstLoopIssueStep = loopAuditSteps.find((step) => step.status !== "pass");
  const actionReceiptRows = operatorActionReceipts?.receipts || operatorActionPlan?.action_receipts?.receipts || [];
  const receiptShortHash = (receipt?: { tamper_chain_hash?: string; action_hash?: string | null; verify_hash?: string | null; audit_id?: string }) => (
    (receipt?.tamper_chain_hash || receipt?.verify_hash || receipt?.action_hash || receipt?.audit_id || "").slice(0, 12)
  );
  const latestReceiptForAction = (action: string, actionSignature?: string | null) => {
    const wantedAction = String(action || "").trim();
    const wantedSignature = String(actionSignature || "").trim();
    if (!wantedAction && !wantedSignature) return undefined;
    return actionReceiptRows.find(receipt => (
      (wantedAction && wantedAction === String(receipt.action_command || "").trim()) ||
      (wantedSignature && wantedSignature === String(receipt.action_signature || "").trim())
    ));
  };
  const candidateReceiptVerified = (candidate: { action: string; actionSignature?: string | null; receiptRequired?: boolean; receiptVerified?: boolean }) => (
    candidate.receiptRequired === false ? true :
    typeof candidate.receiptVerified === "boolean" ? candidate.receiptVerified : latestReceiptForAction(candidate.action, candidate.actionSignature)?.status === "verified"
  );
  const actionQueueCandidateScore = (candidate: { id: string; action: string; actionSignature?: string | null; receiptRequired?: boolean; receiptVerified?: boolean; isOperatorCommandCenterAction?: boolean; isReceiptCoverageRecovery?: boolean; isReceiptEvaluationRecovery?: boolean; isReceiptFailureMemoryRecovery?: boolean; isOperatorHealthRisk?: boolean; isEvidenceRemediation?: boolean }) => (
    isCloseEvidenceGapCommand(candidate.action) ? 120 :
    candidate.isOperatorCommandCenterAction ? 119 :
    candidate.isOperatorHealthRisk ? 118 :
    candidate.isReceiptEvaluationRecovery ? 116 :
    candidate.isReceiptCoverageRecovery ? 115 :
    candidate.isReceiptFailureMemoryRecovery ? 114 :
    candidate.isEvidenceRemediation ? 112 :
    candidate.id.startsWith("loop-first-issue:") ? 110 :
    !candidateReceiptVerified(candidate) ? 80 :
    0
  );
  const actionQueueCandidates = [
    ...(firstLoopIssueStep?.command ? [{
      id: `loop-first-issue:${firstLoopIssueStep.id}:${firstLoopIssueStep.command}`,
      action: firstLoopIssueStep.command,
      source: `${copy.loopChainTitle} · ${firstLoopIssueStep.label} · ${firstLoopIssueStep.source || copy.actionSource}`,
      status: firstLoopIssueStep.status,
      verifyAction: loopAuditNextAction,
    }] : []),
    ...operatorCommandCenterActions.map((item, index) => ({
      id: `command-center:${item.action_id || index}:${item.command}`,
      action: item.command,
      source: `${copy.operatorCommandCenterTitle} · ${item.source || "next_action"}`,
      status: operatorCommandCenter?.status || "attention",
      verifyAction: item.verify_command || "agentops operator command-center --limit 12",
      actionSignature: item.action_signature || item.action_id || null,
      receiptRequired: item.receipt_required !== false,
      receiptStatus: item.receipt_status,
      receiptVerified: item.receipt_verified,
      receiptHash: item.receipt_hash,
      receiptRecordCommand: item.receipt_record_command,
      receiptVerifyRecordCommand: item.receipt_verify_record_command,
      controlReadbackRequired: item.control_readback_required,
      controlReadbackAttached: item.control_readback_attached,
      isOperatorCommandCenterAction: true,
    })),
    ...operatorPlanActions.map((item) => {
      const evidence = (item.evidence || {}) as Record<string, unknown>;
      const workflowStepId = String(evidence.workflow_step_id || "");
      return {
        id: `operator:${item.action_id}`,
        action: item.command,
        source: `${item.lane === "operator_health" ? copy.operatorHealthTitle : copy.operatorTitle} · ${workflowStepId ? `${copy.remediationWorkflow} · ${workflowStepId}` : evidence.handoff_remediation_chain ? "evidence remediation" : item.lane}`,
        status: item.severity || operatorActionPlan?.status || "attention",
        operatorAction: item,
        verifyAction: item.verify_command || (isCloseEvidenceGapCommand(item.command) ? "agentops operator action-plan --limit 20" : undefined),
        actionSignature: item.action_signature,
        receiptRequired: item.receipt_required,
        receiptStatus: item.receipt_status,
        receiptVerified: item.receipt_verified,
        receiptHash: item.receipt_hash,
        receiptEvaluation: item.receipt_evaluation,
        receiptId: item.receipt_id,
        receiptRecordCommand: item.receipt_record_command,
        receiptRecordConfirmCommand: item.receipt_record_confirm_command,
        receiptVerifyRecordCommand: item.receipt_verify_record_command,
        controlReadbackRequired: item.control_readback_required,
        controlReadbackAttached: item.control_readback_attached,
        controlReadbackHash: item.control_readback_hash,
        remediationWorkflowStepId: workflowStepId,
        remediationWorkflowKind: String(evidence.next_safe_command_kind || ""),
        remediationWorkflowPrerequisite: String(evidence.prerequisite_step || ""),
        remediationWorkflowMutating: Boolean(evidence.mutating),
        remediationWorkflowConfirmRequired: Boolean(evidence.confirm_required),
        isReceiptCoverageRecovery: item.source === "receipt_coverage",
        isReceiptEvaluationRecovery: item.source === "receipt_evaluation",
        isReceiptFailureMemoryRecovery: item.source === "receipt_failure_memory",
        isOperatorHealthRisk: item.lane === "operator_health" || item.source.startsWith("operator_health:"),
        isEvidenceRemediation: Boolean(evidence.handoff_remediation_chain),
      };
    }),
    ...recommendedActions.map((action, index) => ({
      id: `fleet:${index}:${action}`,
      action,
      source: copy.overallFleetHealth,
      status: fleetHealth?.overall || "attention",
      verifyAction: "agentops worker status",
    })),
    ...integrationInboxActions.map((action, index) => ({
      id: `inbox:${index}:${action}`,
      action,
      source: copy.integrationInboxTitle,
      status: integrationInbox?.status || "attention",
      verifyAction: "agentops commander inbox --limit 5",
    })),
    ...synthesisLifecycleActions.map((action, index) => ({
      id: `synthesis:${index}:${action}`,
      action,
      source: copy.synthesisLoop,
      status: synthesisLifecycle?.status || "attention",
      verifyAction: "agentops commander board --limit 20",
    })),
    ...localReadinessActions.map((action, index) => ({
      id: `local:${index}:${action}`,
      action,
      source: copy.localReadinessTitle,
      status: localReadiness?.status || "attention",
      verifyAction: "agentops local readiness",
    })),
  ].filter((candidate, index, list) => (
    candidate.action &&
    list.findIndex(item => item.action === candidate.action) === index
  )).sort((left, right) => actionQueueCandidateScore(right) - actionQueueCandidateScore(left)).slice(0, 8);
  const actionReceiptKey = actionQueueCandidates.map(item => (
    `${item.id}:${candidateReceiptVerified(item) ? "verified" : "missing"}`
  )).join("|");
  const actionQueueKey = `${actionQueueCandidates.map(item => item.id).join("|")}::${actionReceiptKey}`;
  const dispatchEvidenceActions = operatorPlanActions.filter(item => item.lane === "dispatch_evidence").slice(0, 4);
  const loopRecord = operatorLoopAudit?.loop_record;
  const loopRecordMemories = loopRecord?.memory_reviews || [];
  const loopRecordApprovals = loopRecord?.approval_reviews || [];
  const loopRecordItems = [...loopRecordMemories, ...loopRecordApprovals];
  const recordStep = loopAuditSteps.find((step) => step.id === "record");
  const orderedActionQueue = [
    ...actionQueueOrder.map(id => actionQueueCandidates.find(item => item.id === id)).filter(Boolean),
    ...actionQueueCandidates.filter(item => !actionQueueOrder.includes(item.id)),
  ].slice(0, 8);
  const visibleActionQueue = orderedActionQueue.filter(Boolean) as typeof actionQueueCandidates;

  useEffect(() => {
    const nextIds = actionQueueCandidates.map(item => item.id);
    setActionQueueOrder(prev => {
      const kept = prev.filter(id => nextIds.includes(id));
      const added = nextIds.filter(id => !kept.includes(id));
      return [...kept, ...added].slice(0, 8);
    });
  }, [actionQueueKey]);

  const moveActionQueueItem = (activeId: string, targetId: string) => {
    const ids = orderedActionQueue.map(item => item?.id).filter(Boolean) as string[];
    const activeIndex = ids.indexOf(activeId);
    const targetIndex = ids.indexOf(targetId);
    if (activeIndex < 0 || targetIndex < 0 || activeIndex === targetIndex) return;
    const next = [...ids];
    const [moved] = next.splice(activeIndex, 1);
    next.splice(targetIndex, 0, moved);
    setActionQueueOrder(next);
  };

  const nudgeActionQueueItem = (id: string, direction: -1 | 1) => {
    const ids = orderedActionQueue.map(item => item?.id).filter(Boolean) as string[];
    const index = ids.indexOf(id);
    const targetIndex = index + direction;
    if (index < 0 || targetIndex < 0 || targetIndex >= ids.length) return;
    const next = [...ids];
    const [moved] = next.splice(index, 1);
    next.splice(targetIndex, 0, moved);
    setActionQueueOrder(next);
  };

  const readCommandFlag = (command: string, flag: string) => {
    const parts = command.split(/\s+/).filter(Boolean);
    const index = parts.indexOf(flag);
    if (index < 0 || index + 1 >= parts.length) return "";
    return parts[index + 1] || "";
  };

  const closeGapDetailsForAction = (item: (typeof actionQueueCandidates)[number]) => {
    const command = item.action || "";
    if (!isCloseEvidenceGapCommand(command)) return null;
    const evidence = item.operatorAction?.evidence || {};
    const readEvidence = (key: string) => {
      const value = evidence[key];
      return typeof value === "string" ? value : "";
    };
    const decisionRaw = readCommandFlag(command, "--decision") || "accepted_remediation";
    const decision = decisionRaw === "waived" || decisionRaw === "reopen" ? decisionRaw : "accepted_remediation";
    const runId = readCommandFlag(command, "--run-id") || readEvidence("run_id");
    if (!runId) return null;
    return {
      runId,
      decision,
      synthesisArtifactId: readCommandFlag(command, "--synthesis-artifact-id") || readEvidence("remediation_synthesis_artifact_id"),
      remediationTaskId: readCommandFlag(command, "--remediation-task-id") || readEvidence("remediation_task_id"),
    };
  };

  const evidenceGapDecisionDetails = (gap: ExecutionEvidenceGapItem, decision: "accepted_remediation" | "reopen" = "accepted_remediation") => ({
    runId: gap.run_id,
    decision,
    synthesisArtifactId: gap.remediation_synthesis_artifact_id || undefined,
    remediationTaskId: gap.remediation_task_id || undefined,
  });

  const evidenceClosureRows = operatorEvidenceGaps
    .filter((gap) => gap.remediation_synthesis_status || gap.gap_decision_status || isCloseEvidenceGapCommand(gap.command || ""))
    .sort((left, right) => {
      const score = (gap: ExecutionEvidenceGapItem) => (
        gap.gap_decision_status === "closed" ? 30 :
        isCloseEvidenceGapCommand(gap.command || "") ? 50 :
        gap.remediation_synthesis_status === "promoted" ? 40 :
        0
      );
      return score(right) - score(left);
    })
    .slice(0, 4);

  const taskIntakeRows = [...taskIntakeItems]
    .sort((left, right) => {
      const score = (item: TaskIntakeChecklistItem) => item.severity === "blocked" ? 3 : item.severity === "attention" ? 2 : 1;
      return score(right) - score(left) || right.priority_score - left.priority_score;
    })
    .slice(0, 4);
  const blockedIntakeRows = taskIntakeItems.filter(item => item.severity === "blocked");
  const primaryBlockedIntake = blockedIntakeRows[0];
  const workerStartBlocked = Boolean(primaryBlockedIntake);

  const renderActiveIntakeGate = () => {
    if (!primaryBlockedIntake) return null;
    const taskRoute = primaryBlockedIntake.ui_route || `/admin/tasks/${primaryBlockedIntake.task_id}`;
    const gateIds = primaryBlockedIntake.failed_gate_ids.join(", ") || "gate";
    const commandCopied = copiedIntakeCommand === primaryBlockedIntake.command;

    return (
      <div className="rounded-lg p-3 mt-3" style={{ background: "rgba(248,113,113,0.08)", border: "1px solid rgba(248,113,113,0.22)" }}>
        <div className="flex flex-col md:flex-row md:items-start md:justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-1.5">
              <AlertTriangle size={13} style={{ color: "#F87171" }} />
              <div className="text-[11px] font-semibold truncate" style={{ color: "var(--mis-text)" }}>{copy.activeIntakeGate}</div>
            </div>
            <div className="text-[10px] mt-1" style={{ color: "var(--mis-muted)" }}>{copy.activeIntakeSummary}</div>
            <div className="text-[10px] mt-1" style={{ color: "#F87171" }}>{copy.workerStartBlockedHint}</div>
            <div className="text-[10px] mt-1 truncate" style={{ color: "var(--mis-dim)" }}>{primaryBlockedIntake.title} · {gateIds}</div>
          </div>
          <div className="flex items-center gap-2 shrink-0 flex-wrap">
            <StatusBadge status="blocked" label={String(blockedIntakeRows.length)} />
            <Link to={taskRoute} className="inline-flex items-center gap-1 text-[10px] px-2 py-1 rounded" style={{ background: "rgba(34,211,238,0.10)", color: "var(--mis-cyan)", border: "1px solid rgba(34,211,238,0.18)" }}>
              <Inbox size={11} />
              {copy.openTask}
            </Link>
            {primaryBlockedIntake.command && (
              <button
                onClick={() => void copyIntakeCommand(primaryBlockedIntake.command)}
                className="inline-flex items-center gap-1 text-[10px] px-2 py-1 rounded"
                style={{ background: "rgba(251,191,36,0.10)", color: "var(--mis-warning)", border: "1px solid rgba(251,191,36,0.20)" }}
              >
                <Copy size={11} />
                {commandCopied ? copy.copiedCommand : copy.copyCommand}
              </button>
            )}
          </div>
        </div>
        <div className="text-[10px] mt-2 truncate" style={{ color: "var(--mis-cyan)" }}>{copy.nextAction}: {primaryBlockedIntake.command}</div>
      </div>
    );
  };

  const submitEvidenceGapDecision = async (details: {
    runId: string;
    decision: "accepted_remediation" | "waived" | "reopen";
    synthesisArtifactId?: string;
    remediationTaskId?: string;
  }) => {
    const actionKey = `${details.decision === "reopen" ? "reopen-gap" : "close-gap"}:${details.runId}`;
    setDispatching(actionKey);
    setDispatchResult(null);
    try {
      const result = await closeExecutionEvidenceGap({
        run_id: details.runId,
        decision: details.decision,
        synthesis_artifact_id: details.synthesisArtifactId || undefined,
        remediation_task_id: details.remediationTaskId || undefined,
        confirm_close: true,
      });
      const label = details.decision === "reopen" ? copy.reopenEvidenceGap : copy.closeEvidenceGap;
      setDispatchResult(`${label}: ${result.status} · ${result.run_id || details.runId}`);
      await refresh();
    } catch (err) {
      setDispatchResult(err instanceof Error ? err.message : String(err));
    } finally {
      setDispatching(null);
    }
  };

  const closeEvidenceGapFromQueue = async (item: (typeof actionQueueCandidates)[number]) => {
    const details = closeGapDetailsForAction(item);
    if (!details) return;
    await submitEvidenceGapDecision(details);
  };

  const recordActionQueueReceipt = async (
    item: (typeof actionQueueCandidates)[number],
    status: "recorded" | "verified",
  ) => {
    const verifyAction = "verifyAction" in item ? item.verifyAction : undefined;
    const actionKey = `action-receipt:${status}:${item.id}`;
    setReceiptAction(actionKey);
    setDispatchResult(null);
    try {
      const result = await recordOperatorActionReceipt({
        action_command: item.action,
        verify_command: verifyAction || undefined,
        action_id: item.id,
        action_signature: "actionSignature" in item ? item.actionSignature || undefined : undefined,
        source: item.source,
        status,
        result_summary: status === "verified"
          ? `Operator verified recovery action from ${item.source}.`
          : `Operator recorded recovery action from ${item.source}.`,
      });
      setDispatchResult(`${copy.actionReceipts}: ${result.status} · ${result.receipt?.receipt_id || ""}`);
      await refresh();
    } catch (err) {
      setDispatchResult(err instanceof Error ? err.message : String(err));
    } finally {
      setReceiptAction(null);
    }
  };

  const recordLocalRunPathReceipt = async (
    step: LocalRunPathStep,
    status: "recorded" | "verified",
  ) => {
    const actionKey = `local-run-path-receipt:${status}:${step.step_id}`;
    setReceiptAction(actionKey);
    setDispatchResult(null);
    try {
      const result = await recordOperatorActionReceipt({
        action_command: step.command,
        verify_command: step.verify_command || undefined,
        action_id: step.step_id,
        action_signature: step.action_signature || undefined,
        source: step.source || "ui.local_run_path",
        status,
        result_summary: status === "verified"
          ? `Operator verified local run-path step ${step.step_id}.`
          : `Operator recorded local run-path step ${step.step_id}.`,
      });
      setDispatchResult(`${copy.actionReceipts}: ${result.status} · ${result.receipt?.receipt_id || ""}`);
      await refresh();
    } catch (err) {
      setDispatchResult(err instanceof Error ? err.message : String(err));
    } finally {
      setReceiptAction(null);
    }
  };

  const recordLocalRunPathControlReadback = async (step: LocalRunPathStep) => {
    const actionKey = `local-run-path-readback:${step.step_id}`;
    setReceiptAction(actionKey);
    setDispatchResult(null);
    try {
      const state = step.receipt_state || {};
      let receiptId = String(state.receipt_id || "");
      if (!receiptId || !state.verified) {
        const receiptResult = await recordOperatorActionReceipt({
          action_command: step.command,
          verify_command: step.verify_command || undefined,
          action_id: step.step_id,
          action_signature: step.action_signature || undefined,
          source: step.source || "ui.local_run_path.service_control_preview",
          status: "verified",
          result_summary: `Operator verified local run-path step ${step.step_id} before control readback.`,
        });
        receiptId = receiptResult.receipt?.receipt_id || receiptId;
      }
      if (!receiptId) throw new Error("receipt_id_required");
      const readback = await recordOperatorActionControlReadback({
        receipt_id: receiptId,
        source: `${step.source || "ui.local_run_path.service_control_preview"}.control_readback`,
        control_readback: {
          before: {
            step_id: step.step_id,
            status: step.status,
            adapter: step.adapter,
            service_control_preview: Boolean(step.service_control_preview),
          },
          after: {
            verify_command: step.verify_command || null,
            service_check_expected: true,
            confirmed_os_mutation: false,
          },
          self_check: {
            copy_only: step.copy_only !== false,
            server_executes_shell: false,
            writes_ledger_for_service_control: false,
            live_execution_performed: false,
            token_omitted: true,
          },
          cache: {
            refresh_cache_required_after_receipt: true,
          },
          token_omitted: true,
        },
      });
      setDispatchResult(`${copy.controlReadback}: ${readback.status} · ${receiptId}`);
      await Promise.allSettled([
        refreshPanel("local_readiness"),
        refreshPanel("operator_action_receipts"),
        refreshPanel("operator_action_plan"),
      ]);
    } catch (err) {
      setDispatchResult(err instanceof Error ? err.message : String(err));
    } finally {
      setReceiptAction(null);
    }
  };

  const recordLatestRunStartGateReceipt = async () => {
    if (!lastWorkerDispatch || !lastWorkerRunStartGate) return;
    const adapter = String(lastWorkerRunStartGate.adapter || lastWorkerRunStartGate.runtime_type || lastWorkerDispatch.adapter || "mock");
    const actionKey = `run-start-gate-readback:${adapter}:${lastWorkerDispatch.task_id}`;
    const statusText = String(lastWorkerRunStartGate.status || (lastWorkerRunStartGate.ok ? "pass" : "blocked"));
    const noShell = !(lastWorkerRunStartGateSafety.server_executes_shell || lastWorkerRunStartGate.server_executes_shell);
    const noLive = !(lastWorkerRunStartGateSafety.live_execution_performed || lastWorkerRunStartGate.live_execution_performed);
    const actionCommand = `agentops operator loop-supervision --adapter ${adapter} --limit 8`;
    const verifyCommand = "agentops operator loop-audit --limit 20";
    setReceiptAction(actionKey);
    setDispatchResult(null);
    try {
      const receiptResult = await recordOperatorActionReceipt({
        action_command: actionCommand,
        verify_command: verifyCommand,
        action_id: `run_start_supervision:${adapter}`,
        action_signature: `run_start_supervision:${adapter}:${lastWorkerRunStartGate.supervision_hash || statusText}`,
        source: `ui.run_start_loop_supervision_gate:${adapter}`,
        status: noShell && noLive ? "verified" : "failed",
        result_summary: [
          `${adapter} run_start supervision gate ${statusText}`,
          `hash=${lastWorkerRunStartGateHash}`,
          `run_start_attempted=${String(lastWorkerDispatch.run_start_attempted !== false)}`,
          "server_executes_shell=false",
          "live_execution_performed=false",
        ].join("; "),
      });
      const receiptId = receiptResult.receipt?.receipt_id;
      if (!receiptId) throw new Error("receipt_id_required");
      await recordOperatorActionControlReadback({
        receipt_id: receiptId,
        source: `ui.run_start_loop_supervision_gate:${adapter}.control_readback`,
        control_readback: {
          before: {
            selected_gate: "run_start_loop_supervision",
            adapter,
            task_id: lastWorkerDispatch.task_id,
            run_id: lastWorkerDispatch.run_id || null,
            status: statusText,
            ok: lastWorkerRunStartGate.ok === true,
            run_start_attempted: lastWorkerDispatch.run_start_attempted !== false,
            supervision_hash_short: lastWorkerRunStartGateHash,
          },
          after: {
            selected_gate: "run_start_loop_supervision",
            receipt_recorded: true,
            verify_command: verifyCommand,
            recommended_next: lastWorkerRunStartRecommendedNext || actionCommand,
            no_run_created_on_block: lastWorkerDispatch.run_start_attempted === false,
          },
          after_self_check: {
            selected_gate: "run_start_loop_supervision",
            selected_status: noShell && noLive ? "verified" : "failed",
            server_executes_shell: false,
            live_execution_performed: false,
            raw_prompt_omitted: true,
            raw_response_omitted: true,
            token_omitted: true,
          },
          refresh_cache_requested: true,
          token_omitted: true,
        },
      });
      setDispatchResult(`${copy.controlReadback}: ${receiptResult.status} · ${receiptId}`);
      await Promise.allSettled([
        refreshPanel("operator_loop_supervision"),
        refreshPanel("operator_action_receipts"),
        refreshPanel("operator_action_plan"),
        refreshPanel("operator_loop_audit"),
        refreshPanel("operator_handoff"),
      ]);
    } catch (err) {
      setDispatchResult(err instanceof Error ? err.message : String(err));
    } finally {
      setReceiptAction((current) => current === actionKey ? null : current);
    }
  };

  const updateCustomerTaskText = (field: "title" | "description", value: string) => {
    setCustomerTaskForm(prev => ({ ...prev, [field]: value }));
  };

  const updateCustomerTaskAdapter = (adapter: (typeof WORKER_ADAPTERS)[number]) => {
    setCustomerTaskForm(prev => ({ ...prev, adapter }));
  };

  const updateLoopLaneForm = (field: keyof typeof loopLaneForm, value: string) => {
    setLoopLaneForm(prev => ({ ...prev, [field]: value }));
  };

  const updateCommanderPlannerForm = (field: keyof typeof commanderPlannerForm, value: string) => {
    setCommanderPlannerForm(prev => ({ ...prev, [field]: value }));
  };

  const runCommanderPlanner = async (confirmCreate: boolean) => {
    setCommanderPlannerBusy(true);
    setCommanderPlannerError(null);
    try {
      const goal = commanderPlannerForm.goal.trim() || (locale === "zh"
        ? "用 AgentOps MIS 拆分并行 AI 团队工作包。"
        : "Use AgentOps MIS to split parallel AI-team work packages.");
      const maxPackages = Math.min(Math.max(Number(commanderPlannerForm.max_packages) || 5, 1), 8);
      const result = await planCommanderWorkPackages({
        goal,
        max_packages: maxPackages,
        confirm_create: confirmCreate,
      });
      setCommanderPlannerResult(result);
      const nextProject = result.project_id && result.plan_id ? { projectId: result.project_id, planId: result.plan_id } : null;
      if (nextProject) {
        setActiveCommanderProject(nextProject);
      }
      setDispatchResult(`${result.status}: ${result.created_count || result.planned_count} · ${result.plan_id}`);
      if (confirmCreate) {
        if (nextProject) {
          const [projectBoard, scopedPackages] = await Promise.all([
            loadCommanderProjectBoard({ project_id: nextProject.projectId, plan_id: nextProject.planId, limit: 12 }),
            loadCommanderWorkPackages({ project_id: nextProject.projectId, plan_id: nextProject.planId, limit: 12 }),
          ]);
          setData((current) => ({
            ...(current || {}),
            commanderProjectBoard: projectBoard,
            commanderWorkPackages: scopedPackages,
          }));
        }
        await refresh({ commanderProject: nextProject });
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setCommanderPlannerError(message);
      setDispatchResult(message);
    } finally {
      setCommanderPlannerBusy(false);
    }
  };

  const dispatchCommanderPackage = async (taskId: string, adapter: (typeof WORKER_ADAPTERS)[number], confirmRun = false) => {
    const actionId = `commander-${adapter}-${taskId}`;
    setDispatching(actionId);
    setDispatchResult(null);
    try {
      const result = await dispatchCommanderWorkPackage({
        task_id: taskId,
        adapter,
        confirm_run: confirmRun,
      });
      setDispatchResult(`${copy.dispatchPackage}: ${result.ok ? "ok" : result.reason || "failed"} · ${result.run_id || result.task_id}`);
      await refresh();
    } catch (err) {
      setDispatchResult(err instanceof Error ? err.message : String(err));
    } finally {
      setDispatching(null);
    }
  };

  const dispatchCommanderPlannedBatch = async () => {
    const plannedTaskIds = commanderActionRows
      .filter(pkg => pkg.package_status === "planned" || pkg.status === "planned")
      .slice(0, 5)
      .map(pkg => pkg.task_id)
      .filter(Boolean);
    if (plannedTaskIds.length === 0) {
      setDispatchResult(locale === "zh" ? "没有可排队的 planned 工作包" : "No planned work packages to queue");
      return;
    }
    setDispatching("commander-batch-mock");
    setDispatchResult(null);
    try {
      const result = await dispatchCommanderWorkPackageBatch({
        project_id: commanderTeamBoard?.project_id || activeCommanderProject?.projectId || undefined,
        plan_id: commanderTeamBoard?.plan_id || activeCommanderProject?.planId || undefined,
        task_ids: plannedTaskIds,
        adapter: "mock",
        status: "planned",
        limit: plannedTaskIds.length,
      });
      setLastCommanderBatch(result);
      setDispatchResult(`${copy.dispatchBatchMock}: ${result.ok ? "queued" : result.reason || "failed"} · ${result.job_ids.length} jobs`);
      if (result.team_board_after_queue) {
        setData((current) => current ? {
          ...current,
          commanderProjectBoard: current.commanderProjectBoard ? {
            ...(current.commanderProjectBoard as CommanderProjectBoardPayload),
            team_board: result.team_board_after_queue,
          } : current.commanderProjectBoard,
        } : current);
      }
      await refresh();
    } catch (err) {
      setDispatchResult(err instanceof Error ? err.message : String(err));
    } finally {
      setDispatching(null);
    }
  };

  const synthesizeCommanderReadyPackages = async () => {
    const readyTaskIds = commanderActionRows
      .filter(pkg => pkg.package_status === "ready_for_review")
      .slice(0, 10)
      .map(pkg => pkg.task_id)
      .filter(Boolean);
    if (readyTaskIds.length === 0) {
      setDispatchResult(locale === "zh" ? "没有 ready_for_review 工作包可合并" : "No ready_for_review work packages to synthesize");
      return;
    }
    setDispatching("commander-synthesize");
    setDispatchResult(null);
    try {
      const result = await synthesizeCommanderWorkPackages({
        task_ids: readyTaskIds,
        status: "ready_for_review",
        limit: readyTaskIds.length,
        confirm_create: true,
      });
      const reviewGate = result.approval_id ? ` · approval ${result.approval_id}` : "";
      if (result.artifact_id) {
        setLastSynthesis({ artifactId: result.artifact_id, approvalId: result.approval_id });
      }
      setDispatchResult(`${copy.synthesizePackages}: ${result.ok ? result.artifact_id || "created" : "failed"} · ${result.package_count || readyTaskIds.length} packages${reviewGate}`);
      await refresh();
    } catch (err) {
      setDispatchResult(err instanceof Error ? err.message : String(err));
    } finally {
      setDispatching(null);
    }
  };

  const promoteLastCommanderSynthesis = async () => {
    if (!lastSynthesis?.artifactId) {
      setDispatchResult(locale === "zh" ? "还没有可晋升的合并报告" : "No synthesis report to promote yet");
      return;
    }
    setDispatching("commander-promote-synthesis");
    setDispatchResult(null);
    try {
      const result = await promoteCommanderSynthesis({
        artifact_id: lastSynthesis.artifactId,
        approval_id: lastSynthesis.approvalId || undefined,
        mode: "both",
        confirm_promote: true,
      });
      setSynthesisPromotion(result);
      setDispatchResult(`${copy.promoteSynthesis}: ${result.status} · memory ${result.memory_id || "n/a"} · delivery ${result.delivery_artifact_id || "n/a"}`);
      await refresh();
    } catch (err) {
      setDispatchResult(err instanceof Error ? err.message : String(err));
    } finally {
      setDispatching(null);
    }
  };

  const runLoopLane = async (resume = false) => {
    setLoopLaneBusy(true);
    setLoopLaneError(null);
    setLoopLaneResult(null);
    try {
      const topic = loopLaneForm.topic.trim() || (locale === "zh"
        ? "请审视 AgentOps MIS 下一步产品闭环。"
        : "Review the next AgentOps MIS product closure.");
      const result = await runHermesOpenClawLoopWorkflow({
        topic,
        loop_id: loopLaneForm.loop_id.trim() || undefined,
        rounds: 1,
        mode: "dry-run",
        resume,
        request_timeout: 15,
        max_agent_attempts: 1,
        retry_delay_sec: 0,
        order: ["hermes", "openclaw"],
      });
      setLoopLaneResult(result);
      if (result.loop_id && !loopLaneForm.loop_id.trim()) {
        setLoopLaneForm(prev => ({ ...prev, loop_id: result.loop_id || prev.loop_id }));
      }
      setDispatchResult(`loop: ${result.ok ? "ok" : "blocked"} · ${result.loop_id || "—"}`);
      await refresh();
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setLoopLaneError(message);
      setDispatchResult(message);
    } finally {
      setLoopLaneBusy(false);
    }
  };

  const runCustomerTask = async (confirmRun: boolean) => {
    setCustomerTaskBusy(true);
    setCustomerTaskError(null);
    setCustomerTaskResult(null);
    setCustomerTaskJob(null);
    try {
      const title = customerTaskForm.title.trim() || (locale === "zh" ? "客户 AI 任务" : "Customer AI task");
      const result = await runCustomerWorkerTaskWorkflow({
        adapter: customerTaskForm.adapter,
        confirm_run: confirmRun,
        title,
        description: customerTaskForm.description.trim() || title,
        acceptance_criteria: locale === "zh"
          ? "Worker 必须创建 run/tool/eval/audit 证据，并返回可展示的任务摘要。"
          : "Worker must create run/tool/eval/audit evidence and return a demonstrable task summary.",
        priority: "high",
        risk_level: customerTaskForm.adapter === "mock" ? "low" : "medium",
        selected_agent_ids: [],
        workflow_kind: "ui_agent_workspace_customer_dispatch",
      });
      setCustomerTaskResult(result);
      setDispatchResult(`${customerTaskForm.adapter}: ${result.ok ? "ok" : result.dry_run ? "dry-run" : "failed"} · ${result.run_id || result.task_id}`);
      await refresh();
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setCustomerTaskError(message);
      setDispatchResult(message);
    } finally {
      setCustomerTaskBusy(false);
    }
  };

  const submitCustomerTaskAsync = async () => {
    setCustomerTaskBusy(true);
    setCustomerTaskError(null);
    setCustomerTaskResult(null);
    setCustomerTaskJob(null);
    try {
      const title = customerTaskForm.title.trim() || (locale === "zh" ? "客户 AI 任务" : "Customer AI task");
      const result = await submitCustomerWorkerTaskJob({
        adapter: customerTaskForm.adapter,
        confirm_run: customerTaskForm.adapter !== "mock",
        title,
        description: customerTaskForm.description.trim() || title,
        acceptance_criteria: locale === "zh"
          ? "Worker 必须通过异步 Job 创建 run/tool/eval/audit/artifact/memory/approval 证据。"
          : "Worker must create run/tool/eval/audit/artifact/memory/approval evidence through the async job.",
        priority: "high",
        risk_level: customerTaskForm.adapter === "mock" ? "low" : "medium",
        selected_agent_ids: [],
        workflow_kind: "ui_agent_workspace_customer_dispatch_async",
      });
      setCustomerTaskJob(result.job);
      setDispatchResult(`job: ${result.job_id} · ${result.job.status}`);
      await refresh();
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setCustomerTaskError(message);
      setDispatchResult(message);
    } finally {
      setCustomerTaskBusy(false);
    }
  };

  const recordWorkflowJobRecoveryReceipt = async (input: {
    actionCommand: string;
    verifyCommand: string;
    actionId: string;
    actionSignature: string;
    resultSummary: string;
    status?: "recorded" | "verified" | "failed" | "skipped";
  }) => {
    const receipt = await recordOperatorActionReceipt({
      action_command: input.actionCommand,
      verify_command: input.verifyCommand,
      action_id: input.actionId,
      action_signature: input.actionSignature,
      source: "ui.commander_team_board.workflow_job_recovery",
      status: input.status || "verified",
      result_summary: input.resultSummary,
    });
    await Promise.allSettled([
      refreshPanel("operator_action_receipts"),
      refreshPanel("operator_action_plan"),
      refreshPanel("operator_loop_audit"),
    ]);
    return receipt;
  };

  const markStuckWorkflowJobFailed = async (jobId: string) => {
    setWorkflowJobAction(jobId);
    setWorkflowJobResult(null);
    const reason = locale === "zh" ? "操作台标记卡住 workflow job 为 failed" : "Operator marked stuck workflow job as failed";
    try {
      const result = await markWorkflowJobFailed(
        jobId,
        reason,
      );
      let receiptLabel = "";
      try {
        const receipt = await recordWorkflowJobRecoveryReceipt({
          actionCommand: `agentops workflow job-mark-failed --job-id ${jobId} --reason "${reason}"`,
          verifyCommand: `agentops workflow job-status --job-id ${jobId}`,
          actionId: `commander_workflow_job_mark_failed:${jobId}`,
          actionSignature: `workflow_job:${jobId}:mark_failed`,
          status: result.marked_failed ? "verified" : "failed",
          resultSummary: `${jobId} mark-failed recovery result: ${result.marked_failed ? "failed" : result.reason || "not changed"}.`,
        });
        receiptLabel = ` · receipt ${receipt.receipt?.receipt_id || receipt.status}`;
      } catch (receiptErr) {
        receiptLabel = ` · receipt ${receiptErr instanceof Error ? receiptErr.message : String(receiptErr)}`;
      }
      setWorkflowJobResult(`${jobId}: ${result.marked_failed ? "failed" : result.reason || "not changed"}${receiptLabel}`);
      await refresh();
    } catch (err) {
      setWorkflowJobResult(err instanceof Error ? err.message : String(err));
    } finally {
      setWorkflowJobAction(null);
    }
  };

  const retryCommanderWorkflowJob = async (taskId: string, jobId: string, adapter: WorkerAdapterName = "mock") => {
    const safeAdapter = WORKER_ADAPTERS.includes(adapter as (typeof WORKER_ADAPTERS)[number]) ? adapter : "mock";
    const actionId = `commander-retry-${jobId}`;
    setWorkflowJobAction(actionId);
    setWorkflowJobResult(null);
    try {
      const result = await dispatchCommanderWorkPackageBatch({
        project_id: commanderTeamBoard?.project_id || activeCommanderProject?.projectId || undefined,
        plan_id: commanderTeamBoard?.plan_id || activeCommanderProject?.planId || undefined,
        task_ids: [taskId],
        adapter: safeAdapter,
        status: "all",
        limit: 1,
        confirm_run: safeAdapter !== "mock",
      });
      setLastCommanderBatch(result);
      let receiptLabel = "";
      try {
        const newJobId = result.job_ids[0] || "<queued_job_id>";
        const receipt = await recordWorkflowJobRecoveryReceipt({
          actionCommand: `agentops commander dispatch-batch --task-id ${taskId} --status all --limit 1 --adapter ${safeAdapter}${safeAdapter !== "mock" ? " --confirm-run" : ""}`,
          verifyCommand: newJobId === "<queued_job_id>" ? "agentops workflow jobs --limit 20" : `agentops workflow job-status --job-id ${newJobId} --wait`,
          actionId: `commander_workflow_job_retry:${jobId}`,
          actionSignature: `workflow_job:${jobId}:retry:${taskId}:${safeAdapter}`,
          status: result.ok ? "verified" : "failed",
          resultSummary: `${jobId} retry recovery queued ${result.job_ids.length} replacement job(s) for task ${taskId}.`,
        });
        receiptLabel = ` · receipt ${receipt.receipt?.receipt_id || receipt.status}`;
      } catch (receiptErr) {
        receiptLabel = ` · receipt ${receiptErr instanceof Error ? receiptErr.message : String(receiptErr)}`;
      }
      setWorkflowJobResult(`${jobId}: retry ${result.ok ? "queued" : result.reason || "failed"} · ${result.job_ids.length} jobs${receiptLabel}`);
      if (result.team_board_after_queue) {
        setData((current) => current ? {
          ...current,
          commanderProjectBoard: current.commanderProjectBoard ? {
            ...(current.commanderProjectBoard as CommanderProjectBoardPayload),
            team_board: result.team_board_after_queue,
          } : current.commanderProjectBoard,
        } : current);
      }
      await refresh();
    } catch (err) {
      setWorkflowJobResult(err instanceof Error ? err.message : String(err));
    } finally {
      setWorkflowJobAction(null);
    }
  };

  const runWorkerOnce = async (adapter: "mock" | "hermes" | "openclaw") => {
    setDispatching(adapter);
    setDispatchResult(null);
    try {
      const result = await dispatchLocalWorkerOnce({
        adapter,
        confirm_run: adapter !== "mock",
        title: locale === "zh" ? `${adapter} worker 页面派发任务` : `${adapter} worker UI dispatch task`,
        description: locale === "zh"
          ? "从 AI 员工页面触发一次本地 worker，验证普通任务可以被自动执行并回写 MIS 账本。"
          : "Trigger one local worker run from the AI Employees page and write evidence back to the MIS ledger.",
        acceptance_criteria: "Worker must complete the task and write run/tool/eval/audit.",
      });
      const runId = result.run_id || result.worker_result?.results?.[0]?.run_id || result.task_id;
      setLastWorkerDispatch(result);
      setDispatchResult(`${adapter}: ${result.ok ? "ok" : "failed"} · ${runId}`);
      await refresh();
    } catch (err) {
      setDispatchResult(err instanceof Error ? err.message : String(err));
    } finally {
      setDispatching(null);
    }
  };

  const startDaemon = async (adapter: "mock" | "hermes" | "openclaw") => {
    setDispatching(`start-${adapter}`);
    setDispatchResult(null);
    try {
      const result = await startLocalWorkerDaemon({
        adapter,
        confirm_run: adapter !== "mock",
        poll_interval: 2,
        max_tasks: 0,
      });
      setLastDaemonControl(result);
      if (!result.ok && result.task_intake) {
        const admissionAction = result.local_loop_admission_summary?.next_safe_commands?.[0] || result.task_intake.local_loop_admission_summary?.next_safe_commands?.[0];
        const action = admissionAction || result.recommended_action || result.task_intake.next_actions?.[0] || copy.activeIntakeGate;
        setDispatchResult(`${adapter} daemon: blocked · ${action}`);
        return;
      }
      const pid = result.daemon?.pid ? `pid ${result.daemon.pid}` : result.already_running ? "already running" : "started";
      setDispatchResult(`${adapter} daemon: ${result.ok ? "ok" : "failed"} · ${pid}`);
      await refresh();
    } catch (err) {
      setDispatchResult(err instanceof Error ? err.message : String(err));
    } finally {
      setDispatching(null);
    }
  };

  const restartDaemon = async (adapter: "mock" | "hermes" | "openclaw") => {
    setDispatching(`restart-${adapter}`);
    setDispatchResult(null);
    try {
      const result = await restartLocalWorkerDaemon({
        adapter,
        confirm_run: adapter !== "mock",
        poll_interval: 2,
        max_tasks: 0,
      });
      setLastDaemonControl(result);
      if (!result.ok && result.task_intake) {
        const admissionAction = result.local_loop_admission_summary?.next_safe_commands?.[0] || result.task_intake.local_loop_admission_summary?.next_safe_commands?.[0];
        const action = admissionAction || result.recommended_action || result.task_intake.next_actions?.[0] || copy.activeIntakeGate;
        setDispatchResult(`${adapter} restart: blocked · ${action}`);
        return;
      }
      const pid = result.daemon?.pid ? `pid ${result.daemon.pid}` : "restart requested";
      setDispatchResult(`${adapter} restart: ${result.ok ? "ok" : "failed"} · ${pid}`);
      await refresh();
    } catch (err) {
      setDispatchResult(err instanceof Error ? err.message : String(err));
    } finally {
      setDispatching(null);
    }
  };

  const stopDaemons = async () => {
    setDispatching("stop-daemons");
    setDispatchResult(null);
    try {
      const result = await stopLocalWorkerDaemon("all");
      setDispatchResult(`daemon stop: ${result.ok ? "ok" : "failed"}`);
      await refresh();
    } catch (err) {
      setDispatchResult(err instanceof Error ? err.message : String(err));
    } finally {
      setDispatching(null);
    }
  };

  const releaseStuckTask = async (taskId: string) => {
    setDispatching(`release-${taskId}`);
    setDispatchResult(null);
    try {
      const result = await releaseWorkerTask({
        task_id: taskId,
        reason: locale === "zh" ? "操作台释放卡住 worker 任务" : "Operator released stuck worker task",
      });
      setDispatchResult(`${taskId}: ${result.released ? "released" : "not released"} · runs ${result.released_runs.length}`);
      await refresh();
    } catch (err) {
      setDispatchResult(err instanceof Error ? err.message : String(err));
    } finally {
      setDispatching(null);
    }
  };

  const runFleetHygiene = async (apply: boolean) => {
    setHygieneBusy(true);
    setHygieneError(null);
    try {
      const result = apply
        ? await applyWorkerFleetHygiene({
            threshold_sec: 900,
            enrollment_age_sec: 900,
            limit: 10,
            release_reason: locale === "zh" ? "操作台 Fleet Hygiene 清理" : "Operator fleet hygiene cleanup",
          })
        : await loadWorkerFleetHygiene({ threshold_sec: 900, enrollment_age_sec: 900, limit: 10 });
      setHygieneResult(result);
      await refresh();
    } catch (err) {
      setHygieneError(err instanceof Error ? err.message : String(err));
    } finally {
      setHygieneBusy(false);
    }
  };

  const handleReviewDecision = async (item: ReviewQueuePayload["review_items"][number], decision: "approve" | "reject") => {
    const actionKey = `${item.item_type}-${item.item_id}-${decision}`;
    setReviewAction(actionKey);
    setReviewResult(null);
    try {
      if (item.item_type === "approval") {
        await decideApproval(item.item_id, decision);
      } else if (item.item_type === "memory_candidate") {
        await decideMemory(item.item_id, decision);
      } else if (item.item_type === "evaluation_case_candidate") {
        await decideEvaluationCase(item.item_id, decision);
      } else {
        setReviewResult(`${copy.reviewActionResult}: ${item.item_type} ${item.item_id}`);
        return;
      }
      setReviewResult(`${copy.reviewActionResult}: ${item.item_type} ${item.item_id} -> ${decision}`);
      await refreshPanel("review_queue");
      if (item.item_type === "approval") {
        await refreshPanel("approvals");
      }
    } catch (err) {
      setReviewResult(err instanceof Error ? err.message : String(err));
    } finally {
      setReviewAction(null);
    }
  };

  const handleReceiptFailureMemory = async (confirmCreate: boolean) => {
    if (confirmCreate && !window.confirm(copy.createFailureMemoryConfirm)) return;
    const actionKey = confirmCreate ? "receipt-failure-memory-create" : "receipt-failure-memory-preview";
    setReceiptFailureMemoryAction(actionKey);
    setReceiptFailureMemoryResult(null);
    try {
      const result = await proposeReceiptFailureMemory({
        action_hash: receiptFailureMemoryPrimaryHash || undefined,
        min_failures: 2,
        confirm_create: confirmCreate,
      });
      const status = String(result.status || "unknown");
      const memoryId = String(result.memory_id || ((result.memory as Record<string, unknown> | undefined)?.memory_id) || "");
      setReceiptFailureMemoryResult(`${copy.memoryCandidateResult}: ${status}${memoryId ? ` · ${memoryId}` : ""}`);
      if (confirmCreate) {
        await refresh();
      }
    } catch (err) {
      setReceiptFailureMemoryResult(err instanceof Error ? err.message : String(err));
    } finally {
      setReceiptFailureMemoryAction(null);
    }
  };

  const handleLoopRecordDecision = async (kind: "memory" | "approval", id: string, decision: "approve" | "reject") => {
    if (!id) return;
    const confirmed = window.confirm(decision === "approve" ? copy.loopRecordApproveConfirm : copy.loopRecordRejectConfirm);
    if (!confirmed) return;
    const actionKey = `loop-record-${kind}-${id}-${decision}`;
    setLoopRecordAction(actionKey);
    setLoopRecordResult(null);
    try {
      if (kind === "memory") {
        const result = await decideMemory(id, decision);
        setLoopRecordResult(`${copy.loopMemoryReview}: ${result.memory_id} -> ${result.review_status}`);
      } else {
        const result = await decideApproval(id, decision);
        setLoopRecordResult(`${copy.loopApprovalReview}: ${result.approval_id} -> ${result.decision}`);
      }
      await refreshPanel("operator_loop_audit");
      await refreshPanel("operator_handoff");
      if (kind === "approval") {
        await refreshPanel("approvals");
      }
    } catch (err) {
      setLoopRecordResult(err instanceof Error ? err.message : String(err));
    } finally {
      setLoopRecordAction(null);
    }
  };

  const updateEnrollmentForm = (field: keyof typeof enrollmentForm, value: string) => {
    setEnrollmentForm(prev => ({ ...prev, [field]: value }));
  };

  const scopeList = enrollmentForm.scopes
    .split(",")
    .map(item => item.trim())
    .filter(Boolean);
  const missingSelectedWorkerScopes = WORKER_EXECUTION_REQUIRED_SCOPES.filter(scope => !scopeList.includes(scope));
  const selectedScopeWorkerViable = scopeList.length > 0 && missingSelectedWorkerScopes.length === 0;
  const createEnrollmentBlockedByPolicy = Boolean(enrollmentPolicy && !enrollmentPolicy.direct_create_allowed);
  const scopeEffectRows = [
    {
      label: copy.readScopes,
      value: scopeList.filter(scope => scope === "agents:heartbeat" || scope.endsWith(":read")).length,
      status: scopeList.some(scope => scope === "agents:heartbeat" || scope.endsWith(":read")) ? "pass" : "planned",
    },
    {
      label: copy.executionScopes,
      value: scopeList.filter(scope => ["tasks:claim", "runs:write"].includes(scope)).length,
      status: scopeList.some(scope => ["tasks:claim", "runs:write"].includes(scope)) ? "attention" : "planned",
    },
    {
      label: copy.evidenceWriteScopes,
      value: scopeList.filter(scope => ["toolcalls:write", "runtime_events:write", "artifacts:write", "evaluations:submit", "audit:write"].includes(scope)).length,
      status: scopeList.some(scope => ["toolcalls:write", "runtime_events:write", "artifacts:write", "evaluations:submit", "audit:write"].includes(scope)) ? "attention" : "planned",
    },
    {
      label: copy.governanceScopes,
      value: scopeList.filter(scope => scope.startsWith("agent_plans:") || scope.startsWith("plan_evidence:") || scope.startsWith("approvals:") || scope.startsWith("memories:")).length,
      status: scopeList.some(scope => scope.startsWith("agent_plans:") || scope.startsWith("plan_evidence:") || scope.startsWith("approvals:") || scope.startsWith("memories:")) ? "attention" : "planned",
    },
  ];

  useEffect(() => {
    let cancelled = false;
    const timer = window.setTimeout(async () => {
      if (scopeList.length === 0) {
        setEnrollmentPolicy(null);
        return;
      }
      try {
        const result = await previewAgentGatewayEnrollmentPolicy({
          workspace_id: enrollmentForm.workspace_id,
          runtime_type: enrollmentForm.runtime_type,
          scopes: scopeList,
        });
        if (!cancelled) {
          setEnrollmentPolicy(result);
          setEnrollmentPolicyError(null);
        }
      } catch (err) {
        if (!cancelled) {
          setEnrollmentPolicyError(err instanceof Error ? err.message : String(err));
        }
      }
    }, 250);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [enrollmentForm.workspace_id, enrollmentForm.runtime_type, enrollmentForm.scopes]);

  const presetLabel = (id: string) => {
    if (id === "worker") return copy.presetWorker;
    if (id === "observer") return copy.presetObserver;
    if (id === "approval") return copy.presetApproval;
    return copy.presetFull;
  };

  const createEnrollment = async () => {
    setEnrollmentAction("create");
    setEnrollmentResult(null);
    clearIssuedCredential();
    try {
      const result = await createAgentGatewayEnrollment({
        agent_id: enrollmentForm.agent_id.trim(),
        name: enrollmentForm.name.trim(),
        runtime_type: enrollmentForm.runtime_type,
        workspace_id: enrollmentForm.workspace_id.trim(),
        label: `${enrollmentForm.name.trim()} enrollment`,
        scopes: scopeList,
        ttl_days: Number(enrollmentForm.ttl_days) || 30,
        heartbeat_timeout_sec: Number(enrollmentForm.heartbeat_timeout_sec) || 300,
      });
      setCreatedToken(result);
      setEnrollmentResult(`${result.agent_id}: ${result.token_id}`);
      await refresh({ preserveIssuedCredential: true });
    } catch (err) {
      setEnrollmentResult(err instanceof Error ? err.message : String(err));
    } finally {
      setEnrollmentAction(null);
    }
  };

  const requestEnrollment = async () => {
    setEnrollmentAction("request");
    setEnrollmentResult(null);
    clearIssuedCredential();
    setCreatedRequest(null);
    try {
      const result = await requestAgentGatewayEnrollment({
        agent_id: enrollmentForm.agent_id.trim(),
        name: enrollmentForm.name.trim(),
        runtime_type: enrollmentForm.runtime_type,
        workspace_id: enrollmentForm.workspace_id.trim(),
        label: `${enrollmentForm.name.trim()} enrollment request`,
        scopes: scopeList,
        ttl_days: Number(enrollmentForm.ttl_days) || 30,
        heartbeat_timeout_sec: Number(enrollmentForm.heartbeat_timeout_sec) || 300,
        reason: locale === "zh"
          ? "远程 worker 需要带范围权限来处理已分配的 MIS 任务。"
          : "Remote worker needs scoped access to process assigned MIS tasks.",
      });
      setCreatedRequest(result);
      setIssueApprovalId(result.approval.approval_id);
      setEnrollmentResult(`${copy.requestCreated}: ${result.request.request_id}`);
      await refresh();
    } catch (err) {
      setEnrollmentResult(err instanceof Error ? err.message : String(err));
    } finally {
      setEnrollmentAction(null);
    }
  };

  const issueApprovedEnrollment = async (approvalId = issueApprovalId) => {
    setEnrollmentAction(`issue-${approvalId || "manual"}`);
    setEnrollmentResult(null);
    clearIssuedCredential();
    try {
      const result = await issueApprovedAgentGatewayEnrollment({
        approval_id: approvalId.trim(),
        ttl_days: Number(enrollmentForm.ttl_days) || 30,
        heartbeat_timeout_sec: Number(enrollmentForm.heartbeat_timeout_sec) || 300,
        label: `${enrollmentForm.name.trim()} approved enrollment`,
      });
      setCreatedToken(result);
      setEnrollmentResult(`${result.agent_id}: ${result.token_id}`);
      await refresh({ preserveIssuedCredential: true });
    } catch (err) {
      setEnrollmentResult(err instanceof Error ? err.message : String(err));
    } finally {
      setEnrollmentAction(null);
    }
  };

  const decideEnrollmentApproval = async (approvalId: string, decision: "approve" | "reject") => {
    setEnrollmentAction(`${decision}-${approvalId}`);
    setEnrollmentResult(null);
    try {
      const result = await decideApproval(approvalId, decision);
      setIssueApprovalId(approvalId);
      setEnrollmentResult(`${approvalId}: ${result.decision}`);
      await refreshPanel("approvals");
    } catch (err) {
      setEnrollmentResult(err instanceof Error ? err.message : String(err));
    } finally {
      setEnrollmentAction(null);
    }
  };

  const revokeEnrollment = async (tokenId?: string, agentId?: string) => {
    const actionRef = tokenId || agentId || "enrollment";
    setEnrollmentAction(`revoke-${actionRef}`);
    setEnrollmentResult(null);
    clearIssuedCredential();
    try {
      const result = await revokeAgentGatewayEnrollment(tokenId ? { token_id: tokenId } : { agent_id: agentId });
      const sessionNote = result.sessions_revoked ? ` · sessions ${result.sessions_revoked}` : "";
      setEnrollmentResult(`revoked: ${result.tokens.join(", ") || result.revoked}${sessionNote}`);
      await refresh();
    } catch (err) {
      setEnrollmentResult(err instanceof Error ? err.message : String(err));
    } finally {
      setEnrollmentAction(null);
    }
  };

  const revokeSession = async (sessionId?: string, agentId?: string) => {
    const actionRef = sessionId || agentId || "session";
    setEnrollmentAction(`revoke-session-${actionRef}`);
    setEnrollmentResult(null);
    clearIssuedCredential();
    try {
      const result = await revokeAgentGatewaySession(sessionId ? { session_id: sessionId } : { agent_id: agentId });
      setEnrollmentResult(`session revoked: ${result.sessions.join(", ") || result.revoked}`);
      await refresh();
    } catch (err) {
      setEnrollmentResult(err instanceof Error ? err.message : String(err));
    } finally {
      setEnrollmentAction(null);
    }
  };

  const rotateEnrollment = async (tokenId?: string, agentId?: string) => {
    const actionRef = tokenId || agentId || "enrollment";
    setEnrollmentAction(`rotate-${actionRef}`);
    setEnrollmentResult(null);
    clearIssuedCredential();
    try {
      const result = await rotateAgentGatewayEnrollment({
        token_id: tokenId,
        agent_id: agentId,
        ttl_days: Number(enrollmentForm.ttl_days) || 30,
        heartbeat_timeout_sec: Number(enrollmentForm.heartbeat_timeout_sec) || 300,
      });
      setCreatedToken(result);
      setEnrollmentResult(`${result.agent_id}: ${result.rotated_from_token_id} -> ${result.token_id}`);
      await refresh({ preserveIssuedCredential: true });
    } catch (err) {
      setEnrollmentResult(err instanceof Error ? err.message : String(err));
    } finally {
      setEnrollmentAction(null);
    }
  };

  const eventText = (event: Record<string, unknown>, key: string, fallback = "—") => {
    const value = event[key];
    return value === null || value === undefined || value === "" ? fallback : String(value);
  };

  const formatAge = (ageSec?: number) => {
    const seconds = Number(ageSec || 0);
    if (!Number.isFinite(seconds) || seconds <= 0) return "—";
    if (seconds < 60) return `${Math.round(seconds)}s`;
    if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
    if (seconds < 86400) return `${Math.round(seconds / 3600)}h`;
    return `${Math.round(seconds / 86400)}d`;
  };

  return (
    <div className="space-y-5 w-full">
      {/* Header */}
      <div>
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="text-lg font-semibold" style={{ color: "var(--mis-text)" }}>{copy.title}</h1>
          <StatusBadge status={deferredLoading ? "running" : deferredError ? "unavailable" : loading ? "running" : "ready"} label={deferredLoading ? copy.panelLoadLoading : deferredError ? copy.panelLoadUnavailable : loading ? copy.panelLoadLoading : copy.panelLoadReady} />
        </div>
        <p className="text-xs mt-0.5" style={{ color: "var(--mis-dim)" }}>
          {copy.summary}
        </p>
        {loading && <p className="text-xs mt-2" style={{ color: "var(--mis-muted)" }}>{copy.loading}</p>}
        {!loading && deferredLoading && <p className="text-xs mt-2" style={{ color: "var(--mis-muted)" }}>{copy.deferredLoading}</p>}
        {error && <p className="text-xs mt-2" style={{ color: "#F87171" }}>{copy.backendUnavailable}: {error}</p>}
        {deferredError && <p className="text-xs mt-2" style={{ color: "var(--mis-warning)" }}>{copy.deferredUnavailable}: {deferredError}</p>}
        <button onClick={() => void refresh()} className="mt-3 text-[11px] px-3 py-1.5 rounded" style={{ background: "rgba(34,211,238,0.12)", color: "var(--mis-cyan)", border: "1px solid rgba(34,211,238,0.2)" }}>
          {copy.refresh}
        </button>
      </div>

      <div
        data-testid="execution-mode-strip"
        className="rounded-xl p-4"
        style={{
          background: selectedExecutionStatus === "blocked"
            ? "rgba(248,113,113,0.08)"
            : selectedExecutionStatus === "attention"
              ? "rgba(251,191,36,0.08)"
              : "var(--mis-surface)",
          border: selectedExecutionStatus === "blocked"
            ? "1px solid rgba(248,113,113,0.25)"
            : selectedExecutionStatus === "attention"
              ? "1px solid rgba(251,191,36,0.28)"
              : "1px solid var(--mis-border)",
        }}
      >
        <div className="flex flex-col xl:flex-row xl:items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <ShieldCheck size={15} style={{ color: selectedExecutionStatus === "blocked" ? "#F87171" : selectedExecutionStatus === "attention" ? "var(--mis-warning)" : "var(--mis-cyan)" }} />
              <div className="text-sm font-semibold" style={{ color: "var(--mis-text)" }}>{copy.executionModeTitle}</div>
              <StatusBadge status={selectedExecutionStatus} label={selectedExecutionLabel} />
              <StatusBadge status={operatorExecutionMode?.safety?.read_only ? "pass" : "attention"} label={operatorExecutionMode?.safety?.read_only ? copy.readOnlyProof : copy.statusAttention} />
              <StatusBadge status={operatorRuntimeDoctor?.status || "unknown"} label={`${copy.runtimeDoctorTitle}: ${operatorRuntimeDoctor?.status || "unknown"}`} />
            </div>
            <p className="text-[11px] mt-1 max-w-4xl" style={{ color: "var(--mis-dim)" }}>{copy.executionModeSummary}</p>
            <p className="text-[10px] mt-1 max-w-4xl truncate" style={{ color: "var(--mis-muted)" }}>
              {copy.selectedRoute}: {operatorExecutionMode?.adapter || customerTaskForm.adapter} · {selectedRouteDetail} · {copy.nextAction}: {executionModeCommand}
            </p>
            {operatorExecutionMode?.contract && (
              <p className="text-[10px] mt-1 max-w-4xl truncate" style={{ color: "var(--mis-muted)" }}>{copy.contract}: {operatorExecutionMode.contract}</p>
            )}
          </div>
          <button
            onClick={() => void copyIntakeCommand(executionModeCommand)}
            className="inline-flex items-center justify-center gap-1 text-[11px] px-2.5 py-1.5 rounded shrink-0"
            style={{ background: "rgba(34,211,238,0.12)", color: "var(--mis-cyan)", border: "1px solid rgba(34,211,238,0.22)" }}
          >
            <Copy size={12} />
            {copiedIntakeCommand === executionModeCommand ? copy.copiedCommand : copy.copyCommand}
          </button>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6 gap-2 mt-3">
          {executionModeCards.map((item) => (
            <div key={item.id} className="rounded px-3 py-2" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
              <div className="text-[9px]" style={{ color: "var(--mis-muted)" }}>{item.label}</div>
              <div className="flex items-center justify-between gap-2 mt-1">
                <div className="text-[10px] font-semibold truncate" style={{ color: item.status === "blocked" ? "#F87171" : "var(--mis-text)" }}>{item.value}</div>
                <StatusBadge status={item.status} />
              </div>
            </div>
          ))}
        </div>
      </div>

      <div
        data-testid="production-security-warning-strip"
        className="rounded-xl p-4"
        style={{
          background: productionSecurityNeedsAttention ? "rgba(251,191,36,0.08)" : "rgba(45,212,191,0.08)",
          border: productionSecurityNeedsAttention ? "1px solid rgba(251,191,36,0.28)" : "1px solid rgba(45,212,191,0.24)",
        }}
      >
        <div className="flex flex-col xl:flex-row xl:items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              {productionSecurityNeedsAttention ? <AlertTriangle size={15} style={{ color: "var(--mis-warning)" }} /> : <ShieldCheck size={15} style={{ color: "var(--mis-success)" }} />}
              <div className="text-sm font-semibold" style={{ color: "var(--mis-text)" }}>{copy.productionSecurityWarning}</div>
              <StatusBadge status={productionSecurityStatus} label={securityReadiness?.production_ready ? copy.productionReady : copy.localDevOnly} />
              <StatusBadge status={localWriteGuardGate?.status || "unknown"} label={copy.localWriteGuard} />
            </div>
            <p className="text-[11px] mt-1 max-w-4xl" style={{ color: "var(--mis-dim)" }}>{copy.productionSecurityWarningSummary}</p>
            <p className="text-[10px] mt-1 max-w-4xl line-clamp-2" style={{ color: "var(--mis-muted)" }}>
              {localWriteGuardGate?.detail || securityReadiness?.contract || copy.localWriteGuardSummary}
            </p>
          </div>
          <div className="grid grid-cols-2 gap-2 min-w-[260px]">
            {[
              { label: copy.deploymentMode, value: securityReadiness?.deployment_mode || (securityReadiness?.production_requested ? "shared" : "local") },
              { label: copy.startupSecurity, value: securityReadiness?.startup_security?.status || "unknown" },
            ].map((item) => (
              <div key={item.label} className="rounded px-2 py-1" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                <div className="text-[9px]" style={{ color: "var(--mis-muted)" }}>{item.label}</div>
                <div className="text-[10px] font-semibold truncate mt-0.5" style={{ color: "var(--mis-text)" }}>{item.value}</div>
              </div>
            ))}
          </div>
        </div>
        <div className="mt-3 flex flex-col md:flex-row md:items-center justify-between gap-2 rounded px-3 py-2" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
          <div className="min-w-0 text-[10px] truncate" style={{ color: "var(--mis-dim)" }}>
            {copy.nextAction}: <span className="font-mono" style={{ color: "var(--mis-cyan)" }}>{productionSecurityNextAction}</span>
          </div>
          <button
            onClick={() => void copyIntakeCommand(productionSecurityNextAction)}
            className="inline-flex items-center justify-center gap-1 text-[11px] px-2.5 py-1.5 rounded shrink-0"
            style={{ background: "rgba(34,211,238,0.12)", color: "var(--mis-cyan)", border: "1px solid rgba(34,211,238,0.22)" }}
          >
            <Copy size={12} />
            {copiedIntakeCommand === productionSecurityNextAction ? copy.copiedCommand : copy.copyCommand}
          </button>
        </div>
      </div>

      <div
        data-testid="commander-work-package-planner"
        className="rounded-xl p-4"
        style={{ background: "var(--mis-surface)", border: "1px solid var(--mis-border)" }}
      >
        <div className="flex flex-col xl:flex-row xl:items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <Bot size={14} style={{ color: "var(--mis-cyan)" }} />
              <h2 className="text-sm font-semibold" style={{ color: "var(--mis-text)" }}>{copy.commanderPlannerTitle}</h2>
              <StatusBadge status={commanderPlannerResult?.status || "preview"} />
            </div>
            <p className="text-[11px] mt-1 max-w-3xl" style={{ color: "var(--mis-dim)" }}>{copy.commanderPlannerSummary}</p>
          </div>
          <div className="flex flex-wrap gap-1.5 xl:justify-end">
            <StatusBadge status="pass" label={`${copy.plannerSafety}: ${copy.yes}`} />
            <StatusBadge status={commanderPlannerResult?.safety.ledger_mutated ? "attention" : "pass"} label={`${copy.ledgerMutationProof}: ${commanderPlannerResult?.safety.ledger_mutated ? copy.yes : copy.no}`} />
            <StatusBadge status={commanderPlannerResult?.live_execution_performed ? "fail" : "pass"} label={`${copy.liveExecutionProof}: ${commanderPlannerResult?.live_execution_performed ? copy.no : copy.yes}`} />
          </div>
        </div>

        <div className="grid grid-cols-1 xl:grid-cols-[1fr_160px_auto] gap-3 mt-4">
          <label className="text-[10px] uppercase tracking-wide" style={{ color: "var(--mis-muted)" }}>
            {copy.commanderGoal}
            <textarea
              value={commanderPlannerForm.goal}
              onChange={(event) => updateCommanderPlannerForm("goal", event.target.value)}
              className="mt-1 w-full rounded px-3 py-2 text-[11px] min-h-[76px]"
              style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)", color: "var(--mis-text)" }}
            />
          </label>
          <label className="text-[10px] uppercase tracking-wide" style={{ color: "var(--mis-muted)" }}>
            {copy.commanderMaxPackages}
            <input
              value={commanderPlannerForm.max_packages}
              onChange={(event) => updateCommanderPlannerForm("max_packages", event.target.value)}
              className="mt-1 w-full rounded px-3 py-2 text-[11px]"
              style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)", color: "var(--mis-text)" }}
            />
          </label>
          <div className="flex xl:flex-col gap-2 xl:justify-end">
            <button
              onClick={() => void runCommanderPlanner(false)}
              disabled={commanderPlannerBusy}
              className="inline-flex items-center justify-center gap-1.5 text-[11px] px-3 py-2 rounded disabled:opacity-50"
              style={{ background: "rgba(34,211,238,0.12)", color: "var(--mis-cyan)", border: "1px solid rgba(34,211,238,0.2)" }}
            >
              {commanderPlannerBusy ? <RefreshCw size={12} /> : <Inbox size={12} />}
              {commanderPlannerBusy ? copy.planning : copy.previewPlan}
            </button>
            <button
              onClick={() => void runCommanderPlanner(true)}
              disabled={commanderPlannerBusy}
              className="inline-flex items-center justify-center gap-1.5 text-[11px] px-3 py-2 rounded disabled:opacity-50"
              style={{ background: "rgba(45,212,191,0.12)", color: "var(--mis-success)", border: "1px solid rgba(45,212,191,0.22)" }}
            >
              {commanderPlannerBusy ? <RefreshCw size={12} /> : <CheckCircle2 size={12} />}
              {commanderPlannerBusy ? copy.planning : copy.createWorkPackages}
            </button>
          </div>
        </div>

        {commanderPlannerError && (
          <div className="text-[11px] rounded px-3 py-2 mt-3" style={{ color: "#F87171", background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
            {commanderPlannerError}
          </div>
        )}

        {commanderPlannerResult && (
          <div className="mt-4 rounded-lg p-3" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
            <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-2">
              <div className="min-w-0">
                <div className="text-[11px] font-semibold" style={{ color: "var(--mis-text)" }}>
                  {copy.plannerResult}: {commanderPlannerResult.plan_id}
                </div>
                <div className="text-[10px] mt-1 truncate" style={{ color: "var(--mis-muted)" }}>
                  {copy.plannedPackages}: {commanderPlannerResult.planned_count} · {copy.createdPackages}: {commanderPlannerResult.created_count}
                </div>
              </div>
              <StatusBadge status={commanderPlannerResult.created ? "completed" : "planned"} label={commanderPlannerResult.status} />
            </div>
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-2 mt-3">
              {commanderPlannerResult.work_packages.slice(0, 6).map((pkg) => (
                <div key={pkg.task_id} className="rounded px-3 py-2" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                  <div className="flex items-center justify-between gap-2">
                    <div className="text-[11px] font-semibold truncate" style={{ color: "var(--mis-text)" }}>{pkg.title}</div>
                    <StatusBadge status={pkg.status} label={pkg.lane_id} />
                  </div>
                  <div className="text-[10px] mt-1 truncate" style={{ color: "var(--mis-muted)" }}>
                    {pkg.owner_agent_id} · {pkg.priority} · {pkg.risk_level}
                  </div>
                  <div className="text-[10px] mt-1 line-clamp-2" style={{ color: "var(--mis-dim)" }}>{pkg.scope}</div>
                  {commanderPlannerResult.created_task_ids.includes(pkg.task_id) && (
                    <Link to={`/admin/tasks/${pkg.task_id}`} className="inline-flex mt-2 text-[10px] px-2 py-1 rounded" style={{ background: "rgba(34,211,238,0.10)", color: "var(--mis-cyan)", border: "1px solid rgba(34,211,238,0.18)" }}>
                      {copy.openTask}
                    </Link>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {commanderTeamBoard && (
          <div
            data-testid="commander-team-board"
            className="mt-4 rounded-lg p-3"
            style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}
          >
            <div className="flex flex-col lg:flex-row lg:items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <Activity size={13} style={{ color: "var(--mis-cyan)" }} />
                  <div className="text-[11px] font-semibold" style={{ color: "var(--mis-text)" }}>{copy.activeTeamBoard}</div>
                  <StatusBadge status={commanderTeamBoard.status} />
                </div>
                <p className="text-[10px] mt-1 max-w-2xl" style={{ color: "var(--mis-muted)" }}>{copy.activeTeamBoardSummary}</p>
                <div className="text-[10px] mt-1 truncate" style={{ color: "var(--mis-dim)" }}>
                  {commanderTeamBoard.project_id || activeCommanderProject?.projectId || "project"} · {commanderTeamBoard.plan_id || activeCommanderProject?.planId || "plan"}
                </div>
              </div>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 min-w-[280px]">
                {[
                  { label: copy.teamLanes, value: commanderTeamBoard.summary.total_lanes, status: commanderTeamBoard.summary.total_lanes > 0 ? "pass" : "attention" },
                  { label: copy.teamReadyForReview, value: commanderTeamBoard.summary.ready_for_review, status: commanderTeamBoard.summary.ready_for_review > 0 ? "attention" : "planned" },
                  { label: copy.activeWorkflowJobs, value: commanderTeamBoard.summary.active_workflow_jobs, status: commanderTeamBoard.summary.active_workflow_jobs > 0 ? "running" : "pass" },
                  { label: copy.failedWorkflowJobs, value: commanderTeamBoard.summary.failed_workflow_jobs, status: commanderTeamBoard.summary.failed_workflow_jobs > 0 ? "blocked" : "pass" },
                  { label: copy.completedWorkflowJobs, value: commanderTeamBoard.summary.workflow_job_counts.completed || 0, status: (commanderTeamBoard.summary.workflow_job_counts.completed || 0) > 0 ? "completed" : "planned" },
                  { label: copy.missingCodingEvidence, value: commanderTeamBoard.summary.missing_coding_evidence, status: commanderTeamBoard.summary.missing_coding_evidence > 0 ? "attention" : "pass" },
                  { label: copy.dependencyEdges, value: commanderTeamBoard.summary.dependency_edges, status: commanderTeamBoard.summary.dependency_edges > 0 ? "planned" : "pass" },
                  { label: copy.jobType, value: Object.values(commanderTeamBoard.summary.workflow_job_counts).reduce((total, value) => total + value, 0), status: Object.keys(commanderTeamBoard.summary.workflow_job_counts).length > 0 ? "pass" : "planned" },
                ].map((item) => (
                  <div key={item.label} className="rounded px-2 py-1.5" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                    <div className="text-[9px] uppercase tracking-wide" style={{ color: "var(--mis-muted)" }}>{item.label}</div>
                    <div className="flex items-center justify-between gap-2 mt-1">
                      <span className="text-[13px] font-semibold" style={{ color: "var(--mis-text)" }}>{item.value}</span>
                      <StatusBadge status={item.status} />
                    </div>
                  </div>
                ))}
              </div>
            </div>

            <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-2 mt-3 rounded px-3 py-2" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
              <div className="min-w-0">
                <div className="text-[10px] font-semibold" style={{ color: "var(--mis-text)" }}>{copy.queueReadback}</div>
                <div className="text-[10px] mt-1 truncate" style={{ color: "var(--mis-muted)" }}>
                  {lastCommanderBatch ? `${lastCommanderBatch.status || lastCommanderBatch.reason || "queued"} · ${lastCommanderBatch.job_ids.length} jobs · ${lastCommanderBatch.filter?.project_id || commanderTeamBoard.project_id || "project"}` : commanderTeamBoard.next_actions[0] || copy.dispatchBatchMock}
                </div>
              </div>
              <div className="flex flex-wrap gap-1.5 lg:justify-end">
                {commanderLastQueueBoard && (
                  <>
                    <StatusBadge status={lastCommanderBatch?.safety.ledger_mutated ? "attention" : "pass"} label={`${copy.jobsCreated}: ${lastCommanderBatch?.safety.jobs_created ?? lastCommanderBatch?.job_ids.length ?? 0}`} />
                    <StatusBadge status={commanderLastQueueBoard.summary.active_workflow_jobs > 0 ? "running" : "pass"} label={`${copy.afterQueueActive}: ${commanderLastQueueBoard.summary.active_workflow_jobs}`} />
                    <StatusBadge status={commanderLastQueueBoard.summary.failed_workflow_jobs > 0 ? "blocked" : "pass"} label={`${copy.afterQueueFailed}: ${commanderLastQueueBoard.summary.failed_workflow_jobs}`} />
                    <StatusBadge status={(commanderLastQueueBoard.summary.workflow_job_counts.completed || 0) > 0 ? "completed" : "planned"} label={`${copy.afterQueueCompleted}: ${commanderLastQueueBoard.summary.workflow_job_counts.completed || 0}`} />
                  </>
                )}
                <button
                  data-testid="commander-team-board-dispatch-batch"
                  onClick={() => void dispatchCommanderPlannedBatch()}
                  disabled={Boolean(dispatching) || commanderPlannedPackageCount === 0}
                  className="inline-flex items-center gap-1 text-[10px] px-2 py-1 rounded disabled:opacity-50"
                  style={{ background: "rgba(45,212,191,0.10)", color: "var(--mis-success)", border: "1px solid rgba(45,212,191,0.18)" }}
                >
                  {dispatching === "commander-batch-mock" ? <RefreshCw size={10} /> : <Play size={10} />}
                  {dispatching === "commander-batch-mock" ? copy.dispatching : `${copy.dispatchBatchMock} (${commanderPlannedPackageCount})`}
                </button>
              </div>
            </div>

            <div className="grid grid-cols-1 xl:grid-cols-2 gap-2 mt-3">
              {commanderTeamBoard.lanes.slice(0, 8).map((lane) => (
                <div key={lane.task_id} className="rounded px-3 py-2" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <div className="text-[11px] font-semibold truncate" style={{ color: "var(--mis-text)" }}>{lane.title}</div>
                      <div className="text-[10px] mt-1 truncate" style={{ color: "var(--mis-muted)" }}>
                        {lane.owner_agent_id || "agent"} · {lane.lane_id || "lane"} · deps {lane.dependency_count}
                      </div>
                    </div>
                    <StatusBadge status={lane.package_status || lane.status} />
                  </div>
                  <div className="grid grid-cols-3 gap-1.5 mt-2">
                    {[
                      { label: "tool", value: lane.evidence_counts.tool_calls || 0 },
                      { label: "eval", value: lane.evidence_counts.evaluations || 0 },
                      { label: "artifact", value: lane.evidence_counts.artifacts || 0 },
                    ].map((item) => (
                      <div key={item.label} className="text-[9px] rounded px-2 py-1" style={{ color: "var(--mis-muted)", background: "var(--mis-surface)", border: "1px solid var(--mis-border)" }}>
                        {item.label}: <span style={{ color: "var(--mis-text)" }}>{item.value}</span>
                      </div>
                    ))}
                  </div>
                  <div className="flex flex-wrap items-center gap-1.5 mt-2">
                    {lane.latest_workflow_job && (
                      <StatusBadge status={lane.latest_workflow_job.status || "unknown"} label={`${copy.latestWorkflowJob}: ${lane.latest_workflow_job.status || "unknown"}`} />
                    )}
                    {lane.latest_workflow_job?.adapter && (
                      <StatusBadge status={lane.latest_workflow_job.confirm_run ? "attention" : "pass"} label={`${lane.latest_workflow_job.adapter} · ${lane.latest_workflow_job.confirm_run ? "live" : "safe"}`} />
                    )}
                    <StatusBadge status={String(lane.localization_gate.status || "unknown")} label={`repo ${String(lane.localization_gate.status || "unknown")}`} />
                    <StatusBadge status={String(lane.coding_evidence_gate.status || "unknown")} label={`code ${String(lane.coding_evidence_gate.status || "unknown")}`} />
                    {lane.latest_workflow_job?.job_id && (
                      <span className="text-[10px] px-2 py-1 rounded font-mono" style={{ color: "var(--mis-muted)", background: "var(--mis-surface)", border: "1px solid var(--mis-border)" }}>
                        {lane.latest_workflow_job.job_id}
                      </span>
                    )}
                    {lane.latest_workflow_job?.job_id && ["queued", "running"].includes(lane.latest_workflow_job.status || "") && (
                      <button
                        data-testid="commander-team-board-mark-job-failed"
                        onClick={() => lane.latest_workflow_job?.job_id && void markStuckWorkflowJobFailed(lane.latest_workflow_job.job_id)}
                        disabled={Boolean(workflowJobAction)}
                        className="inline-flex items-center gap-1 text-[10px] px-2 py-1 rounded disabled:opacity-50"
                        style={{ color: "#F87171", background: "rgba(248,113,113,0.10)", border: "1px solid rgba(248,113,113,0.22)" }}
                      >
                        {workflowJobAction === lane.latest_workflow_job.job_id ? <RefreshCw size={10} /> : <Square size={10} />}
                        {workflowJobAction === lane.latest_workflow_job.job_id ? copy.markingJobFailed : copy.markLaneJobFailed}
                      </button>
                    )}
                    {lane.latest_workflow_job?.job_id && lane.latest_workflow_job.status === "failed" && (
                      <button
                        data-testid="commander-team-board-retry-job"
                        onClick={() => void retryCommanderWorkflowJob(lane.task_id, lane.latest_workflow_job?.job_id || lane.task_id, (lane.latest_workflow_job?.adapter || "mock") as WorkerAdapterName)}
                        disabled={Boolean(workflowJobAction) || liveAdapterConfirmMissing((lane.latest_workflow_job?.adapter || "mock") as (typeof WORKER_ADAPTERS)[number])}
                        className="inline-flex items-center gap-1 text-[10px] px-2 py-1 rounded disabled:opacity-50"
                        style={{ color: "var(--mis-warning)", background: "rgba(245,158,11,0.10)", border: "1px solid rgba(245,158,11,0.20)" }}
                      >
                        {workflowJobAction === `commander-retry-${lane.latest_workflow_job.job_id}` ? <RefreshCw size={10} /> : <RotateCw size={10} />}
                        {workflowJobAction === `commander-retry-${lane.latest_workflow_job.job_id}` ? copy.retryingWorkflowJob : copy.retryWorkflowJob}
                      </button>
                    )}
                    {lane.latest_run?.run_id && (
                      <Link to={`/admin/runs/${lane.latest_run.run_id}`} className="text-[10px] px-2 py-1 rounded" style={{ color: "var(--mis-cyan)", background: "rgba(34,211,238,0.10)", border: "1px solid rgba(34,211,238,0.18)" }}>
                        {lane.latest_run.run_id}
                      </Link>
                    )}
                    {lane.latest_workflow_job?.result_run_id && lane.latest_workflow_job.result_run_id !== lane.latest_run?.run_id && (
                      <Link to={`/admin/runs/${lane.latest_workflow_job.result_run_id}`} className="text-[10px] px-2 py-1 rounded" style={{ color: "var(--mis-success)", background: "rgba(45,212,191,0.10)", border: "1px solid rgba(45,212,191,0.18)" }}>
                        {copy.runId}
                      </Link>
                    )}
                    {lane.latest_workflow_job?.result_artifact_id && (
                      <span className="text-[10px] px-2 py-1 rounded font-mono" style={{ color: "var(--mis-warning)", background: "rgba(245,158,11,0.08)", border: "1px solid rgba(245,158,11,0.18)" }}>
                        {copy.artifactId}: {lane.latest_workflow_job.result_artifact_id}
                      </span>
                    )}
                    <Link to={`/admin/tasks/${lane.task_id}`} className="text-[10px] px-2 py-1 rounded" style={{ color: "var(--mis-purple)", background: "rgba(129,140,248,0.10)", border: "1px solid rgba(129,140,248,0.18)" }}>
                      {copy.openTask}
                    </Link>
                  </div>
                  {lane.recommended_action && (
                    <div className="text-[10px] mt-2 truncate" style={{ color: "var(--mis-dim)" }}>{lane.recommended_action}</div>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        <div className="mt-4 rounded-lg p-3" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
          <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-2">
            <div className="min-w-0">
              <div className="text-[11px] font-semibold" style={{ color: "var(--mis-text)" }}>
                {copy.persistedPackages}
              </div>
              <div className="text-[10px] mt-1 truncate" style={{ color: "var(--mis-muted)" }}>
                {copy.packageReadback}: {commanderWorkPackages?.summary.total ?? 0} · {copy.readOnlyProof}: {commanderWorkPackages?.safety.read_only ? copy.yes : copy.no}
              </div>
            </div>
            <div className="flex flex-wrap gap-1.5 lg:justify-end">
              <button
                onClick={() => void dispatchCommanderPlannedBatch()}
                disabled={Boolean(dispatching) || commanderPlannedPackageCount === 0}
                className="inline-flex items-center gap-1 text-[10px] px-2 py-1 rounded disabled:opacity-50"
                style={{ background: "rgba(45,212,191,0.10)", color: "var(--mis-success)", border: "1px solid rgba(45,212,191,0.18)" }}
              >
                {dispatching === "commander-batch-mock" ? <RefreshCw size={10} /> : <Play size={10} />}
                {dispatching === "commander-batch-mock" ? copy.dispatching : `${copy.dispatchBatchMock} (${commanderPlannedPackageCount})`}
              </button>
              <button
                onClick={() => void synthesizeCommanderReadyPackages()}
                disabled={Boolean(dispatching) || commanderReadyPackageCount === 0}
                className="inline-flex items-center gap-1 text-[10px] px-2 py-1 rounded disabled:opacity-50"
                style={{ background: "rgba(34,211,238,0.10)", color: "var(--mis-cyan)", border: "1px solid rgba(34,211,238,0.18)" }}
              >
                {dispatching === "commander-synthesize" ? <RefreshCw size={10} /> : <CheckCircle2 size={10} />}
                {dispatching === "commander-synthesize" ? copy.dispatching : `${copy.synthesizePackages} (${commanderReadyPackageCount})`}
              </button>
              <button
                onClick={() => void promoteLastCommanderSynthesis()}
                disabled={Boolean(dispatching) || !lastSynthesis?.artifactId}
                className="inline-flex items-center gap-1 text-[10px] px-2 py-1 rounded disabled:opacity-50"
                style={{ background: "rgba(245,158,11,0.10)", color: "var(--mis-warning)", border: "1px solid rgba(245,158,11,0.20)" }}
              >
                {dispatching === "commander-promote-synthesis" ? <RefreshCw size={10} /> : <Inbox size={10} />}
                {dispatching === "commander-promote-synthesis" ? copy.dispatching : copy.promoteSynthesis}
              </button>
              <StatusBadge status={commanderWorkPackages?.status || "unknown"} />
            </div>
          </div>
          {synthesisPromotion && (
            <div className="text-[10px] mt-2 rounded px-3 py-2" style={{ color: "var(--mis-muted)", background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
              {copy.promoteSynthesis}: {synthesisPromotion.status} · {synthesisPromotion.safety.ledger_mutated ? copy.yes : copy.no}
            </div>
          )}
          <div className="space-y-2 mt-3">
            {commanderPackageRows.length === 0 && (
              <div className="text-[11px] rounded px-3 py-2" style={{ color: "var(--mis-muted)", background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                {copy.noWorkflowJobs}
              </div>
            )}
            {commanderPackageRows.slice(0, 6).map((pkg) => (
              <div key={pkg.work_package_id || pkg.task_id} className="rounded px-3 py-2" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                <div className="flex flex-col lg:flex-row lg:items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2 min-w-0">
                      <div className="text-[11px] font-semibold truncate" style={{ color: "var(--mis-text)" }}>{pkg.title}</div>
                      <StatusBadge status={pkg.package_status || pkg.status} label={pkg.lane_id || pkg.status} />
                    </div>
                    <div className="text-[10px] mt-1 truncate" style={{ color: "var(--mis-muted)" }}>
                      {pkg.project_id || "project"} · {pkg.owner_agent_id || "agent"} · {copy.packageStatus}: {pkg.package_status}
                    </div>
                    <div className="text-[10px] mt-1 line-clamp-2" style={{ color: "var(--mis-dim)" }}>
                      {pkg.recommended_action || "agentops commander board"}
                    </div>
                  </div>
                  <div className="flex flex-wrap gap-1.5 shrink-0">
                    {([
                      { adapter: "mock" as const, label: copy.dispatchPackageMock, confirm: false },
                      { adapter: "hermes" as const, label: copy.dispatchPackageHermes, confirm: true },
                      { adapter: "openclaw" as const, label: copy.dispatchPackageOpenClaw, confirm: true },
                    ]).map(action => {
                      const actionId = `commander-${action.adapter}-${pkg.task_id}`;
                      return (
                        <button
                          key={action.adapter}
                          onClick={() => void dispatchCommanderPackage(pkg.task_id, action.adapter, action.confirm)}
                          disabled={Boolean(dispatching) || (action.confirm && liveAdapterConfirmMissing(action.adapter))}
                          className="inline-flex items-center gap-1 text-[10px] px-2 py-1 rounded disabled:opacity-50"
                          style={{ background: "rgba(129,140,248,0.10)", color: "var(--mis-purple)", border: "1px solid rgba(129,140,248,0.18)" }}
                        >
                          {dispatching === actionId ? <RefreshCw size={10} /> : <Play size={10} />}
                          {dispatching === actionId ? copy.dispatching : action.label}
                        </button>
                      );
                    })}
                    <Link to={`/admin/tasks/${pkg.task_id}`} className="text-[10px] px-2 py-1 rounded" style={{ background: "rgba(34,211,238,0.10)", color: "var(--mis-cyan)", border: "1px solid rgba(34,211,238,0.18)" }}>{copy.openTask}</Link>
                    {pkg.latest_run?.run_id && (
                      <Link to={`/admin/runs/${pkg.latest_run.run_id}`} className="text-[10px] px-2 py-1 rounded" style={{ background: "rgba(45,212,191,0.10)", color: "var(--mis-success)", border: "1px solid rgba(45,212,191,0.18)" }}>{copy.openRun}</Link>
                    )}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div
        data-testid="review-queue-panel"
        className="rounded-xl p-4"
        style={{ background: "var(--mis-surface)", border: "1px solid var(--mis-border)" }}
      >
        <div className="flex flex-col lg:flex-row lg:items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <Inbox size={14} style={{ color: "var(--mis-warning)" }} />
              <h2 className="text-sm font-semibold" style={{ color: "var(--mis-text)" }}>{copy.reviewQueueTitle}</h2>
              <StatusBadge status={reviewQueue?.status || "unknown"} />
            </div>
            <p className="text-[11px] mt-1 max-w-3xl" style={{ color: "var(--mis-dim)" }}>{copy.reviewQueueSummary}</p>
          </div>
          <div className="flex flex-wrap gap-1.5 lg:justify-end">
            <StatusBadge status={reviewQueueSafety?.read_only ? "pass" : "fail"} label={`${copy.reviewReadbackProof}: ${reviewQueueSafety?.read_only ? copy.yes : copy.no}`} />
            <StatusBadge status={reviewQueueSafety?.ledger_mutated === false ? "pass" : "fail"} label={`${copy.ledgerMutationProof}: ${reviewQueueSafety?.ledger_mutated === false ? copy.yes : copy.no}`} />
            <StatusBadge status="attention" label={copy.reviewDecisionAuditProof} />
          </div>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-6 gap-2 mt-3">
          {[
            { label: copy.pendingApprovals, value: reviewQueueSummary?.pending_approvals ?? 0, status: (reviewQueueSummary?.pending_approvals || 0) > 0 ? "attention" : "pass" },
            { label: copy.memoryCandidates, value: reviewQueueSummary?.memory_candidates ?? 0, status: (reviewQueueSummary?.memory_candidates || 0) > 0 ? "attention" : "pass" },
            { label: copy.failedBenchmarks, value: reviewQueueSummary?.failed_evaluation_case_runs ?? 0, status: (reviewQueueSummary?.failed_evaluation_case_runs || 0) > 0 ? "attention" : "pass" },
            { label: copy.waitingDeliveries, value: reviewQueueSummary?.waiting_deliveries ?? 0, status: (reviewQueueSummary?.waiting_deliveries || 0) > 0 ? "attention" : "pass" },
            { label: copy.synthesisLoop, value: `${reviewQueueSummary?.commander_synthesis_promotion_available ?? 0}/${reviewQueueSummary?.commander_synthesis_memory_reviews ?? 0}`, status: ((reviewQueueSummary?.commander_synthesis_promotion_available || 0) + (reviewQueueSummary?.commander_synthesis_memory_reviews || 0)) > 0 ? "attention" : "pass" },
            { label: copy.returnedItems, value: `${reviewQueueSummary?.returned_items ?? 0}/${reviewQueueSummary?.review_items_total ?? 0}`, status: (reviewQueueSummary?.review_items_total || 0) > 0 ? "ready" : "idle" },
          ].map((item) => (
            <div key={item.label} className="rounded px-3 py-2" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
              <div className="text-[10px]" style={{ color: "var(--mis-muted)" }}>{item.label}</div>
              <div className="flex items-center justify-between gap-2 mt-1">
                <div className="text-sm font-semibold truncate" style={{ color: item.status === "attention" ? "var(--mis-warning)" : "var(--mis-text)" }}>{item.value}</div>
                <StatusBadge status={item.status} />
              </div>
            </div>
          ))}
        </div>

        <div className="space-y-2 mt-3">
          {reviewResult && (
            <div className="text-[11px] rounded px-3 py-2" style={{ color: reviewResult.includes("Error") ? "#F87171" : "var(--mis-cyan)", background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
              {reviewResult}
            </div>
          )}
          {reviewQueueItems.length === 0 && (
            <div className="text-[11px] rounded px-3 py-2" style={{ color: "var(--mis-muted)", background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
              {copy.reviewQueueEmpty}
            </div>
          )}
          {reviewQueueItems.slice(0, 5).map((item) => (
            <div key={`${item.item_type}-${item.item_id}`} data-testid="review-queue-item" className="rounded px-3 py-2" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
              <div className="flex flex-col lg:flex-row lg:items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="flex items-center gap-2 min-w-0">
                    <div className="text-[11px] font-semibold truncate" style={{ color: "var(--mis-text)" }}>{item.title}</div>
                    <StatusBadge status={item.status} />
                  </div>
                  <div className="text-[10px] mt-1 truncate" style={{ color: "var(--mis-muted)" }}>
                    {item.item_type} · {item.kind || "review"} · {item.agent_id || "agent: —"}
                  </div>
                  {item.summary && (
                    <div className="text-[10px] mt-1 line-clamp-2" style={{ color: "var(--mis-dim)" }}>{item.summary}</div>
                  )}
                  {item.next_action && (
                    <div className="text-[10px] mt-1 line-clamp-2" style={{ color: "var(--mis-cyan)" }}>
                      {copy.nextAction}: {item.next_action}
                    </div>
                  )}
                </div>
                <div className="flex flex-wrap lg:justify-end gap-1.5 shrink-0">
                  {item.task_id && (
                    <Link to={`/admin/tasks/${item.task_id}`} className="text-[10px] px-2 py-1 rounded" style={{ background: "rgba(34,211,238,0.10)", color: "var(--mis-cyan)", border: "1px solid rgba(34,211,238,0.18)" }}>{copy.openTask}</Link>
                  )}
                  {item.run_id && (
                    <Link to={`/admin/runs/${item.run_id}`} className="text-[10px] px-2 py-1 rounded" style={{ background: "rgba(45,212,191,0.10)", color: "var(--mis-success)", border: "1px solid rgba(45,212,191,0.18)" }}>{copy.openRun}</Link>
                  )}
                  {item.links?.report_url && (
                    <Link to={item.links.report_url} className="text-[10px] px-2 py-1 rounded" style={{ background: "rgba(251,191,36,0.10)", color: "var(--mis-warning)", border: "1px solid rgba(251,191,36,0.20)" }}>{copy.openReport}</Link>
                  )}
                  {((item.item_type === "approval" && item.status === "pending") || (item.item_type === "memory_candidate" && item.status === "candidate") || (item.item_type === "evaluation_case_candidate" && item.status === "candidate")) && (
                    <>
                      <button
                        onClick={() => void handleReviewDecision(item, "approve")}
                        disabled={Boolean(reviewAction)}
                        className="inline-flex items-center gap-1 text-[10px] px-2 py-1 rounded disabled:opacity-50"
                        style={{ background: "rgba(42,157,143,0.15)", color: "var(--mis-success)", border: "1px solid rgba(42,157,143,0.2)" }}
                      >
                        <CheckCircle2 size={11} /> {reviewAction === `${item.item_type}-${item.item_id}-approve` ? copy.dispatching : copy.reviewApprove}
                      </button>
                      <button
                        onClick={() => void handleReviewDecision(item, "reject")}
                        disabled={Boolean(reviewAction)}
                        className="inline-flex items-center gap-1 text-[10px] px-2 py-1 rounded disabled:opacity-50"
                        style={{ background: "rgba(248,113,113,0.12)", color: "#F87171", border: "1px solid rgba(248,113,113,0.2)" }}
                      >
                        <XCircle size={11} /> {reviewAction === `${item.item_type}-${item.item_id}-reject` ? copy.dispatching : copy.reviewReject}
                      </button>
                    </>
                  )}
                </div>
              </div>
              <div className="mt-2 grid grid-cols-1 lg:grid-cols-2 gap-1.5">
                {item.cli_action && (
                  <div className="text-[9px] truncate px-2 py-1 rounded" style={{ color: "var(--mis-muted)", background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
                    {copy.cliAction}: {item.cli_action}
                  </div>
                )}
                {item.alternate_cli_action && (
                  <div className="text-[9px] truncate px-2 py-1 rounded" style={{ color: "var(--mis-muted)", background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
                    {copy.alternateAction}: {item.alternate_cli_action}
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      </div>

      <div
        className="rounded-xl p-4"
        style={{ background: "var(--mis-surface)", border: "1px solid var(--mis-border)" }}
      >
        <div className="flex flex-col lg:flex-row lg:items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <Bot size={14} style={{ color: "var(--mis-cyan)" }} />
              <h2 className="text-sm font-semibold" style={{ color: "var(--mis-text)" }}>{copy.loopLaneTitle}</h2>
              <StatusBadge status={loopLaneResult?.ok ? "completed" : loopLaneReadback?.status || "ready"} />
            </div>
            <p className="text-[11px] mt-1 max-w-3xl" style={{ color: "var(--mis-dim)" }}>{copy.loopLaneSummary}</p>
          </div>
          <div className="flex flex-wrap gap-1.5 lg:justify-end">
            <StatusBadge status="pass" label={`${copy.readOnlyProof}: ${loopLaneReadback?.token_omitted ? copy.yes : copy.no}`} />
            <StatusBadge status={(loopLaneReadback?.summary?.blocked_plan_evidence_manifests || 0) > 0 ? "blocked" : "pass"} label={`${copy.blockedManifests}: ${loopLaneReadback?.summary?.blocked_plan_evidence_manifests ?? 0}`} />
          </div>
        </div>

        <div className="grid grid-cols-1 xl:grid-cols-[1.2fr_0.8fr] gap-3 mt-3">
          <div className="rounded-lg p-3" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
            <div className="grid grid-cols-1 md:grid-cols-[1fr_0.7fr] gap-2">
              <label className="text-[10px] uppercase tracking-wide" style={{ color: "var(--mis-muted)" }}>
                {copy.loopTopic}
                <textarea
                  value={loopLaneForm.topic}
                  onChange={(event) => updateLoopLaneForm("topic", event.target.value)}
                  className="mt-1 w-full rounded px-3 py-2 text-[11px] min-h-[72px]"
                  style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)", color: "var(--mis-text)" }}
                />
              </label>
              <label className="text-[10px] uppercase tracking-wide" style={{ color: "var(--mis-muted)" }}>
                {copy.loopId}
                <input
                  value={loopLaneForm.loop_id}
                  onChange={(event) => updateLoopLaneForm("loop_id", event.target.value)}
                  placeholder="loop_ui_review"
                  className="mt-1 w-full rounded px-3 py-2 text-[11px]"
                  style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)", color: "var(--mis-text)" }}
                />
                <div className="text-[9px] mt-1 normal-case" style={{ color: "var(--mis-dim)" }}>
                  {loopLaneResult?.loop_id || loopLaneReadback?.loop_id || "dry-run · MIS ledger · no live runtime"}
                </div>
              </label>
            </div>
            <div className="flex flex-wrap gap-2 mt-3">
              <button
                onClick={() => runLoopLane(false)}
                disabled={loopLaneBusy}
                className="inline-flex items-center gap-1.5 rounded px-3 py-2 text-[11px] disabled:opacity-50"
                style={{ background: "rgba(34,211,238,0.12)", color: "var(--mis-cyan)", border: "1px solid rgba(34,211,238,0.24)" }}
              >
                {loopLaneBusy ? <RefreshCw size={12} /> : <Play size={12} />}
                {loopLaneBusy ? copy.loopRunning : copy.runLoopLane}
              </button>
              <button
                onClick={() => runLoopLane(true)}
                disabled={loopLaneBusy || !loopLaneForm.loop_id.trim()}
                className="inline-flex items-center gap-1.5 rounded px-3 py-2 text-[11px] disabled:opacity-50"
                style={{ background: "var(--mis-bg)", color: "var(--mis-text)", border: "1px solid var(--mis-border)" }}
              >
                {loopLaneBusy ? <RefreshCw size={12} /> : <RotateCw size={12} />}
                {loopLaneBusy ? copy.loopRunning : copy.resumeLoopLane}
              </button>
            </div>
            {(loopLaneError || loopLaneResult) && (
              <div className="mt-3 rounded px-3 py-2" style={{ background: "var(--mis-bg)", border: loopLaneError ? "1px solid rgba(248,113,113,0.24)" : "1px solid var(--mis-border)" }}>
                {loopLaneError && <div className="text-[11px]" style={{ color: "#F87171" }}>{loopLaneError}</div>}
                {loopLaneResult && (
                  <div className="space-y-2">
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
                      {[
                        { label: copy.loopId, value: loopLaneResult.loop_id || "—" },
                        { label: copy.parentRun, value: loopLaneResult.mis_ledger?.parent_run_id || "—" },
                        { label: copy.verifiedManifests, value: loopLaneResult.mis_ledger?.verified_plan_evidence_manifest_ids?.length ?? 0 },
                        { label: copy.blockedManifests, value: loopLaneResult.mis_ledger?.blocked_plan_evidence_manifest_ids?.length ?? 0 },
                      ].map(item => (
                        <div key={item.label} className="rounded px-2 py-1" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
                          <div className="text-[9px]" style={{ color: "var(--mis-muted)" }}>{item.label}</div>
                          <div className="text-[10px] font-semibold truncate mt-0.5" style={{ color: "var(--mis-text)" }}>{item.value}</div>
                        </div>
                      ))}
                    </div>
                    {loopLaneResult.mis_ledger?.parent_run_id && (
                      <Link to={`/admin/runs/${loopLaneResult.mis_ledger.parent_run_id}`} className="inline-flex text-[10px] rounded px-2 py-1" style={{ background: "rgba(45,212,191,0.10)", color: "var(--mis-success)", border: "1px solid rgba(45,212,191,0.18)" }}>
                        {copy.openRun}
                      </Link>
                    )}
                  </div>
                )}
              </div>
            )}
          </div>

          <div className="rounded-lg p-3" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
            <div className="flex items-center justify-between gap-2">
              <div className="text-[11px] font-semibold" style={{ color: "var(--mis-text)" }}>{copy.loopReadback}</div>
              <StatusBadge status={loopLaneReadback?.status || "unknown"} />
            </div>
            <div className="grid grid-cols-2 gap-2 mt-3">
              {[
                { label: copy.runs, value: loopLaneReadback?.summary?.runs ?? 0 },
                { label: copy.artifactId, value: loopLaneReadback?.summary?.artifacts ?? 0 },
                { label: copy.verifiedManifests, value: loopLaneReadback?.summary?.verified_plan_evidence_manifests ?? 0 },
                { label: copy.blockedManifests, value: loopLaneReadback?.summary?.blocked_plan_evidence_manifests ?? 0 },
              ].map(item => (
                <div key={item.label} className="rounded px-2 py-1" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                  <div className="text-[9px]" style={{ color: "var(--mis-muted)" }}>{item.label}</div>
                  <div className="text-[10px] font-semibold truncate mt-0.5" style={{ color: "var(--mis-text)" }}>{item.value}</div>
                </div>
              ))}
            </div>
            <div className="mt-3 space-y-1.5">
              {(loopLaneReadback?.runs || []).slice(0, 3).map((run) => (
                <Link key={String(run.run_id)} to={`/admin/runs/${String(run.run_id)}`} className="block rounded px-2 py-1.5" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                  <div className="text-[10px] font-semibold truncate" style={{ color: "var(--mis-text)" }}>{String(run.run_id || "—")}</div>
                  <div className="text-[9px] mt-0.5 truncate" style={{ color: "var(--mis-muted)" }}>{String(run.status || "unknown")} · {String(run.agent_id || "—")}</div>
                </Link>
              ))}
              {(loopLaneReadback?.runs || []).length === 0 && (
                <div className="text-[10px] rounded px-2 py-1.5" style={{ color: "var(--mis-muted)", background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                  {copy.inboxEmpty}
                </div>
              )}
            </div>
          </div>
        </div>
      </div>

      <div
        className="rounded-xl p-4"
        style={{ background: "var(--mis-surface)", border: "1px solid var(--mis-border)" }}
      >
        <div className="flex flex-col lg:flex-row lg:items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <ShieldCheck size={14} style={{ color: "var(--mis-success)" }} />
              <h2 className="text-sm font-semibold" style={{ color: "var(--mis-text)" }}>{copy.customerDeliveryBoardTitle}</h2>
              <StatusBadge status={customerDeliveryBoard?.status || "unknown"} />
            </div>
            <p className="text-[11px] mt-1 max-w-3xl" style={{ color: "var(--mis-dim)" }}>{copy.customerDeliveryBoardSummary}</p>
          </div>
          <div className="flex flex-wrap gap-1.5 lg:justify-end">
            <StatusBadge status={customerDeliverySafety?.read_only ? "pass" : "fail"} label={`${copy.deliverySafeReadback}: ${customerDeliverySafety?.read_only ? copy.yes : copy.no}`} />
            <StatusBadge status={customerDeliverySafety?.ledger_mutated === false ? "pass" : "fail"} label={`${copy.ledgerMutationProof}: ${customerDeliverySafety?.ledger_mutated === false ? copy.yes : copy.no}`} />
            <StatusBadge status={customerDeliverySafety?.live_execution_performed === false ? "pass" : "fail"} label={`${copy.liveExecutionProof}: ${customerDeliverySafety?.live_execution_performed === false ? copy.yes : copy.no}`} />
          </div>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 mt-3">
          {[
            { label: copy.deliveriesReady, value: customerDeliverySummary?.ready ?? 0, status: (customerDeliverySummary?.ready || 0) > 0 ? "ready" : "idle" },
            { label: copy.deliveriesWaiting, value: customerDeliverySummary?.waiting_approval ?? 0, status: (customerDeliverySummary?.waiting_approval || 0) > 0 ? "attention" : "pass" },
            { label: copy.deliveriesAttention, value: customerDeliverySummary?.needs_attention ?? 0, status: (customerDeliverySummary?.needs_attention || 0) > 0 ? "blocked" : "pass" },
          ].map((item) => (
            <div key={item.label} className="rounded px-3 py-2" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
              <div className="text-[10px]" style={{ color: "var(--mis-muted)" }}>{item.label}</div>
              <div className="flex items-center justify-between gap-2 mt-1">
                <div className="text-lg font-semibold" style={{ color: item.status === "blocked" ? "#F87171" : "var(--mis-text)" }}>{item.value}</div>
                <StatusBadge status={item.status} />
              </div>
            </div>
          ))}
        </div>

        <div className="space-y-2 mt-3">
          {customerDeliveries.length === 0 && (
            <div className="text-[11px] rounded px-3 py-2" style={{ color: "var(--mis-muted)", background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
              {copy.deliveryEmpty}
            </div>
          )}
          {customerDeliveries.slice(0, 4).map((delivery) => (
            <div key={delivery.delivery_id} className="rounded px-3 py-2" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
              <div className="flex flex-col md:flex-row md:items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="flex items-center gap-2 min-w-0">
                    <div className="text-[11px] font-semibold truncate" style={{ color: "var(--mis-text)" }}>{delivery.title}</div>
                    <StatusBadge status={delivery.status} />
                  </div>
                  <div className="text-[10px] mt-1 truncate" style={{ color: "var(--mis-muted)" }}>
                    {copy.taskId}: {delivery.task_id || "—"} · {copy.runId}: {delivery.run_id || "—"} · {copy.artifactId}: {delivery.artifact_id || "—"}
                  </div>
                  {delivery.summary && (
                    <div className="text-[10px] mt-1 line-clamp-2" style={{ color: "var(--mis-dim)" }}>{delivery.summary}</div>
                  )}
                  <div className="mt-2 flex flex-wrap items-center gap-1.5">
                    <span
                      className="text-[9px] px-1.5 py-0.5 rounded"
                      style={{
                        color: delivery.delivery_approval_gate?.pass ? "var(--mis-success)" : delivery.delivery_approval_gate?.manifest_id ? "var(--mis-warning)" : "#F87171",
                        background: "var(--mis-surface2)",
                        border: "1px solid var(--mis-border)",
                      }}
                    >
                      {copy.planEvidenceGate}: {delivery.delivery_approval_gate?.pass ? copy.planEvidenceVerified : delivery.delivery_approval_gate?.manifest_id ? copy.planEvidenceBlocked : copy.planEvidenceMissing}
                    </span>
                    <span className="text-[9px] truncate max-w-[18rem]" style={{ color: "var(--mis-muted)" }}>
                      {delivery.delivery_approval_gate?.manifest_id || delivery.delivery_approval_gate?.message || delivery.next_action || "—"}
                    </span>
                  </div>
                </div>
                <div className="flex flex-wrap md:justify-end gap-1.5 shrink-0">
                  {delivery.task_id && (
                    <Link to={`/admin/tasks/${delivery.task_id}`} className="text-[10px] px-2 py-1 rounded" style={{ background: "rgba(34,211,238,0.10)", color: "var(--mis-cyan)", border: "1px solid rgba(34,211,238,0.18)" }}>{copy.openTask}</Link>
                  )}
                  {delivery.run_id && (
                    <Link to={`/admin/runs/${delivery.run_id}`} className="text-[10px] px-2 py-1 rounded" style={{ background: "rgba(45,212,191,0.10)", color: "var(--mis-success)", border: "1px solid rgba(45,212,191,0.18)" }}>{copy.openRun}</Link>
                  )}
                  {delivery.ui_report_url && (
                    <Link to={delivery.ui_report_url} className="text-[10px] px-2 py-1 rounded" style={{ background: "rgba(251,191,36,0.10)", color: "var(--mis-warning)", border: "1px solid rgba(251,191,36,0.20)" }}>{copy.openReport}</Link>
                  )}
                </div>
              </div>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {Object.entries(delivery.evidence || {}).slice(0, 6).map(([key, value]) => (
                  <span key={key} className="text-[9px] px-1.5 py-0.5 rounded" style={{ color: "var(--mis-muted)", background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
                    {key}: {value}
                  </span>
                ))}
                {(delivery.pending_approval_ids?.length || 0) > 0 && (
                  <span className="text-[9px] px-1.5 py-0.5 rounded" style={{ color: "var(--mis-warning)", background: "rgba(251,191,36,0.08)", border: "1px solid rgba(251,191,36,0.22)" }}>
                    {copy.approvals}: {delivery.pending_approval_ids?.length}
                  </span>
                )}
                {delivery.next_action && (
                  <span className="text-[9px] px-1.5 py-0.5 rounded truncate max-w-full" style={{ color: "var(--mis-cyan)", background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
                    {copy.nextAction}: {delivery.next_action}
                  </span>
                )}
              </div>
            </div>
          ))}
        </div>
      </div>

      <div
        className="rounded-xl p-4"
        style={{ background: "var(--mis-surface)", border: "1px solid var(--mis-border)" }}
      >
        <div className="flex flex-col lg:flex-row lg:items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-2">
              <Activity size={14} style={{ color: "var(--mis-cyan)" }} />
              <h2 className="text-sm font-semibold" style={{ color: "var(--mis-text)" }}>{copy.commandCenterTitle}</h2>
              <StatusBadge status={operatorCommandCenter?.status || operatorHealth?.status || fleetHealth?.overall || workerStatus?.status || "unknown"} />
              <StatusBadge status={operatorLoopControl?.status || "unknown"} label={`${copy.loopControlTitle}: ${operatorLoopControl?.status || "unknown"}`} />
              <StatusBadge status={operatorRuntimeDoctor?.status || "unknown"} label={`${copy.runtimeDoctorTitle}: ${operatorRuntimeDoctor?.status || "unknown"}`} />
              {panelStatusBadge("operator_loop_control")}
              {panelRefreshButton("operator_loop_control")}
              {panelDiagnosticsButton("operator_loop_control")}
              {panelReceiptButton("operator_loop_control")}
              {panelStatusBadge("operator_runtime_doctor")}
              {panelRefreshButton("operator_runtime_doctor")}
              {panelDiagnosticsButton("operator_runtime_doctor")}
              {panelReceiptButton("operator_runtime_doctor")}
              {panelStatusBadge("operator_command_center")}
              {panelRefreshButton("operator_command_center")}
              {panelDiagnosticsButton("operator_command_center")}
              {panelReceiptButton("operator_command_center")}
              {panelStatusBadge("worker_status")}
              {panelRefreshButton("worker_status")}
              {panelDiagnosticsButton("worker_status")}
              {panelReceiptButton("worker_status")}
            </div>
            <p className="text-[11px] mt-1 max-w-3xl" style={{ color: "var(--mis-dim)" }}>{copy.commandCenterSummary}</p>
            {operatorCommandCenter && (
              <p className="text-[10px] mt-1 max-w-3xl" style={{ color: "var(--mis-muted)" }}>
                {copy.operatorCommandCenterSummary} · {copy.commandCenterActions}: {operatorCommandCenterSummary?.next_actions ?? operatorCommandCenterActions.length} · {copy.blockedRuns}: {operatorCommandCenterSummary?.blocked_runs ?? 0} · {copy.pendingApprovals}: {operatorCommandCenterSummary?.pending_approvals ?? 0}
              </p>
            )}
            {panelEvidenceLine("operator_command_center")}
            {panelEvidenceLine("operator_loop_control")}
            {panelEvidenceLine("operator_runtime_doctor")}
            {panelEvidenceLine("worker_status")}
            {operatorLoopControl && (
              <p className="text-[10px] mt-1 max-w-3xl" style={{ color: "var(--mis-muted)" }}>
                {copy.loopControlSummary} · {copy.verifiedReceipts}: {Number(operatorLoopControl.summary.verified_receipts ?? 0)} · {copy.nextSafeCommand}: {directLoopControlNextCommand || directLoopControlPreviewCommand}
              </p>
            )}
            {operatorRuntimeDoctor && (
              <p className="text-[10px] mt-1 max-w-3xl" style={{ color: "var(--mis-muted)" }}>
                {copy.runtimeDoctorSummary} · {copy.recommendedAdapter}: {runtimeDoctorSummary?.recommended_adapter || "mock"} · {copy.runtimeDoctorGates}: {runtimeDoctorBlockedGates.length} blocked / {runtimeDoctorAttentionGates.length} attention
              </p>
            )}
            {operatorHealth && (
              <p className="text-[10px] mt-1 max-w-3xl" style={{ color: "var(--mis-muted)" }}>
                {copy.operatorHealthSummary} · {copy.healthScore}: {operatorHealth.score}/100 · {copy.healthRisks}: {operatorHealth.risks.length}
              </p>
            )}
            {operatorHealthControlSummary && (
              <div className="flex flex-wrap gap-1.5 mt-2">
                <StatusBadge status={loopControlGateStatus} label={`${copy.loopControlTitle}: ${loopControlGateStatus}`} />
                <StatusBadge status={loopControlServerShell ? "blocked" : "pass"} label={loopControlCopyOnly ? copy.readOnlyProof : "server shell"} />
                <StatusBadge status={loopControlRequiresHuman ? "attention" : "pass"} label={`${copy.humanRequired}: ${loopControlRequiresHuman ? copy.yes : copy.no}`} />
                <StatusBadge status={loopControlRequiresReceipt ? "attention" : "pass"} label={`${copy.receiptProof}: ${loopControlRequiresReceipt ? copy.receiptNeeded : copy.verifiedReceipts}`} />
                <span className="text-[10px] px-2 py-1 rounded max-w-full truncate" style={{ color: "var(--mis-muted)", background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
                  {copy.controlMode}: {loopControlGateMode} · {copy.recommendedStep}: {loopControlSelectedGate}
                </span>
              </div>
            )}
            {fleetHealth?.contract && (
              <p className="text-[10px] mt-1 max-w-3xl" style={{ color: "var(--mis-muted)" }}>
                {copy.contract}: {fleetHealth.contract}
              </p>
            )}
          </div>
          <div className="text-left lg:text-right shrink-0">
            <div className="text-[10px] uppercase tracking-wide" style={{ color: "var(--mis-muted)" }}>{copy.recommendedAdapter}</div>
            <div className="text-sm font-semibold mt-0.5" style={{ color: "var(--mis-text)" }}>{recommendedAdapter}</div>
          </div>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 xl:grid-cols-9 gap-3 mt-4">
          {[
            { label: copy.operatorCommandCenterTitle, value: operatorCommandCenterSummary?.next_actions ?? operatorCommandCenterActions.length, status: operatorCommandCenter?.status || "unknown" },
            { label: copy.loopControlTitle, value: loopControlSelectedGate, status: operatorLoopControl?.status || loopControlGateStatus },
            { label: copy.runtimeDoctorTitle, value: runtimeDoctorSummary?.evidence_chain_status || operatorRuntimeDoctor?.status || "—", status: operatorRuntimeDoctor?.status || "unknown" },
            { label: copy.operatorHealthTitle, value: `${operatorHealth?.score ?? 0}/100`, status: operatorHealth?.status || "unknown" },
            { label: copy.commandCenterCodingGaps, value: operatorCommandCenterCodingGapCount, status: operatorCommandCenterCodingGapCount > 0 ? "attention" : "pass" },
            { label: copy.overallFleetHealth, value: fleetHealth?.overall || workerStatus?.status || "—", status: fleetHealth?.overall || workerStatus?.status || "unknown" },
            { label: copy.daemonStatus, value: `${runningDaemons}/${workerStatus?.daemons?.length ?? 0}`, status: runningDaemons > 0 ? "running" : "ready" },
            { label: copy.pendingTasks, value: workerStatus?.pending_worker_tasks ?? "—", status: (workerStatus?.pending_worker_tasks || 0) > 0 ? "planned" : "pass" },
            { label: copy.stuckTasks, value: stuckWorkerCount, status: stuckWorkerCount > 0 ? "blocked" : "pass" },
            { label: copy.workflowRecovery, value: stuckWorkflowJobCount, status: stuckWorkflowJobCount > 0 ? "blocked" : "pass" },
          ].map((item) => (
            <div key={item.label} className="rounded-lg px-3 py-2" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
              <div className="text-[10px]" style={{ color: "var(--mis-muted)" }}>{item.label}</div>
              <div className="flex items-center justify-between gap-2 mt-1">
                <div className="text-sm font-semibold truncate" style={{ color: item.status === "blocked" ? "#F87171" : "var(--mis-text)" }}>{item.value}</div>
                <StatusBadge status={item.status} />
              </div>
            </div>
          ))}
        </div>

        {operatorLoopControl && (
          <div className="rounded-lg p-3 mt-4" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
            <div className="flex flex-col xl:flex-row xl:items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <Activity size={13} style={{ color: "var(--mis-cyan)" }} />
                  <div className="text-[11px] font-semibold" style={{ color: "var(--mis-text)" }}>{copy.loopControlTitle}</div>
                  <StatusBadge status={operatorLoopControl.status || "unknown"} />
                  <StatusBadge status={operatorLoopControl.safety.read_only && !operatorLoopControl.safety.ledger_mutated ? "pass" : "attention"} label={operatorLoopControl.safety.read_only ? copy.readOnlyProof : copy.statusAttention} />
                  <StatusBadge status={operatorLoopControl.safety.live_execution_performed ? "blocked" : "pass"} label={operatorLoopControl.safety.live_execution_performed ? "live executed" : copy.liveExecutionProof} />
                  <StatusBadge status={loopControlServerShell ? "blocked" : "pass"} label={loopControlServerShell ? "server shell" : "copy-only"} />
                  {panelStatusBadge("operator_loop_control")}
                  {panelRefreshButton("operator_loop_control")}
                  {panelDiagnosticsButton("operator_loop_control")}
                  {panelReceiptButton("operator_loop_control")}
                </div>
                <p className="text-[10px] mt-1 max-w-4xl" style={{ color: "var(--mis-dim)" }}>{copy.loopControlSummary}</p>
                {operatorLoopControl.contract && (
                  <p className="text-[10px] mt-1 max-w-4xl truncate" style={{ color: "var(--mis-muted)" }}>{copy.contract}: {operatorLoopControl.contract}</p>
                )}
                <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mt-2">
                  {[
                    { label: copy.recommendedStep, value: String(directLoopControlStep.label || directLoopControlStep.step_id || loopControlSelectedGate || "—"), status: loopControlGateStatus },
                    { label: copy.controlMode, value: loopControlGateMode, status: loopControlCopyOnly ? "pass" : "attention" },
                    { label: copy.humanRequired, value: loopControlRequiresHuman ? copy.yes : copy.no, status: loopControlRequiresHuman ? "attention" : "pass" },
                    { label: copy.receiptProof, value: loopControlRequiresReceipt ? copy.receiptNeeded : copy.verifiedReceipts, status: loopControlRequiresReceipt ? "attention" : "pass" },
                  ].map((item) => (
                    <div key={item.label} className="rounded px-2 py-1" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                      <div className="text-[8px]" style={{ color: "var(--mis-muted)" }}>{item.label}</div>
                      <div className="flex items-center justify-between gap-1 mt-0.5">
                        <div className="text-[9px] font-semibold truncate" style={{ color: "var(--mis-text)" }}>{item.value}</div>
                        <StatusBadge status={item.status} />
                      </div>
                    </div>
                  ))}
                </div>
              </div>
              <div className="flex flex-col gap-2 shrink-0 xl:max-w-[520px]">
                <div className="flex flex-wrap xl:justify-end gap-1.5">
                  {[
                    { label: copy.nextSafeCommand, command: directLoopControlNextCommand || directLoopControlPreviewCommand, color: "var(--mis-cyan)" },
                    { label: copy.verifyAfterAction, command: directLoopControlVerifyCommand, color: "var(--mis-success)" },
                    { label: copy.copyReceiptCommand, command: directLoopControlReceiptCommand, color: "var(--mis-warning)" },
                    { label: copy.previewAdvanceLoop, command: directLoopControlPreviewCommand, color: "var(--mis-cyan)" },
                  ].filter(item => item.command).map((item) => (
                    <button
                      key={`${item.label}:${item.command}`}
                      onClick={() => void copyIntakeCommand(item.command)}
                      className="inline-flex items-center gap-1 text-[9px] px-2 py-1 rounded max-w-full"
                      style={{ color: item.color, background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}
                      title={item.command}
                    >
                      <Copy size={10} />
                      <span className="truncate max-w-[132px]">{copiedIntakeCommand === item.command ? copy.copiedCommand : item.label}</span>
                    </button>
                  ))}
                </div>
                <div className="rounded px-2 py-1.5" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                  <div className="flex flex-wrap items-center gap-2">
                    <Terminal size={11} style={{ color: "var(--mis-cyan)" }} />
                    <div className="text-[9px] font-semibold" style={{ color: "var(--mis-text)" }}>{copy.loopDriverTitle}</div>
                    <StatusBadge status={operatorLoopDriverPackets?.status || "pass"} />
                    <StatusBadge status="pass" label="local CLI" />
                    <StatusBadge status={operatorLoopDriverPackets?.safety.server_executes_shell ? "blocked" : "pass"} label={operatorLoopDriverPackets?.safety.server_executes_shell ? "server shell" : "copy-only"} />
                    {panelStatusBadge("operator_loop_driver_packets")}
                    {panelRefreshButton("operator_loop_driver_packets")}
                    {panelDiagnosticsButton("operator_loop_driver_packets")}
                    {panelReceiptButton("operator_loop_driver_packets")}
                  </div>
                  <div className="text-[8px] mt-1 line-clamp-2" style={{ color: "var(--mis-muted)" }}>{copy.loopDriverSummary}</div>
                  <div className="flex flex-wrap gap-1 mt-1.5">
                    {loopDriverVisibleCommands.map((item) => (
                      <button
                        key={`${item.label}:${item.command}`}
                        onClick={() => void copyIntakeCommand(item.command)}
                        className="inline-flex items-center gap-1 text-[8px] px-1.5 py-0.5 rounded max-w-full"
                        style={{ color: item.color, background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}
                        title={item.command}
                      >
                        <Copy size={8} />
                        <span className="truncate max-w-[132px]">{copiedIntakeCommand === item.command ? copy.copiedCommand : item.label}</span>
                      </button>
                    ))}
                  </div>
                  {operatorAgentLoopHandoff && (
                    <div className="mt-2 pt-2" style={{ borderTop: "1px solid var(--mis-border)" }}>
                      <div className="flex flex-wrap items-center gap-1.5">
                        <div className="text-[8px] font-semibold" style={{ color: "var(--mis-text)" }}>{copy.agentLoopHandoffTitle}</div>
                        <StatusBadge status={operatorAgentLoopHandoff.status || "unknown"} />
                        <StatusBadge status={operatorAgentLoopHandoff.summary.ready_for_handoff ? "pass" : "blocked"} label={`${copy.handoffReady}: ${String(operatorAgentLoopHandoff.summary.ready_for_handoff)}`} />
                        <StatusBadge status={operatorAgentLoopHandoff.summary.ready_for_all_bounded_loop_confirm ? "pass" : "attention"} label={`${copy.boundedConfirmReady}: ${String(operatorAgentLoopHandoff.summary.ready_for_all_bounded_loop_confirm)}`} />
                        <StatusBadge status={operatorAgentLoopHandoff.current_code.ok ? "pass" : "blocked"} label={operatorAgentLoopHandoff.current_code.status} />
                        <StatusBadge status={operatorAgentLoopHandoff.safety.server_executes_shell ? "blocked" : "pass"} label={operatorAgentLoopHandoff.safety.server_executes_shell ? "server shell" : "copy-only"} />
                        {panelStatusBadge("operator_agent_loop_handoff")}
                        {panelRefreshButton("operator_agent_loop_handoff")}
                        {panelDiagnosticsButton("operator_agent_loop_handoff")}
                        {panelReceiptButton("operator_agent_loop_handoff")}
                      </div>
                      <div className="text-[8px] mt-1 line-clamp-2" style={{ color: "var(--mis-muted)" }}>{copy.agentLoopHandoffSummary}</div>
                      <div className="grid grid-cols-2 md:grid-cols-4 gap-1 mt-1.5">
                        {[
                          { label: copy.freshLiveAdapters, value: String(operatorAgentLoopHandoff.summary.fresh_live_adapters), status: operatorAgentLoopHandoff.summary.fresh_live_adapters >= 2 ? "pass" : "attention" },
                          { label: copy.codexSupervisor, value: operatorAgentLoopHandoff.codex_consumer?.status || "unknown", status: operatorAgentLoopHandoff.codex_consumer?.status || "unknown" },
                          { label: copy.handoffReady, value: `${operatorAgentLoopHandoff.summary.ready_consumers}/${operatorAgentLoopHandoff.summary.consumers}`, status: operatorAgentLoopHandoff.summary.ready_for_handoff ? "pass" : "blocked" },
                          { label: copy.tokenOmitted, value: String(Boolean(operatorAgentLoopHandoff.token_omitted)), status: operatorAgentLoopHandoff.token_omitted ? "pass" : "attention" },
                        ].map((item) => (
                          <div key={`agent-loop-handoff-summary:${item.label}`} className="rounded px-1.5 py-1" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                            <div className="text-[8px]" style={{ color: "var(--mis-muted)" }}>{item.label}</div>
                            <div className="flex items-center justify-between gap-1 mt-0.5">
                              <span className="text-[9px] font-semibold truncate" style={{ color: "var(--mis-text)" }}>{item.value}</span>
                              <StatusBadge status={item.status} />
                            </div>
                          </div>
                        ))}
                      </div>
                      <div className="flex flex-wrap gap-1 mt-1.5">
                        {agentLoopHandoffCommands.map((item) => (
                          <button
                            key={`agent-loop-handoff-command:${item.label}:${item.command}`}
                            type="button"
                            onClick={() => void copyIntakeCommand(String(item.command))}
                            className="inline-flex items-center gap-1 text-[8px] px-1.5 py-0.5 rounded max-w-full"
                            style={{ color: item.color, background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}
                            title={String(item.command)}
                          >
                            <Copy size={8} />
                            <span className="truncate max-w-[132px]">{copiedIntakeCommand === item.command ? copy.copiedCommand : item.label}</span>
                          </button>
                        ))}
                      </div>
                      <div className="grid grid-cols-1 lg:grid-cols-2 gap-1.5 mt-1.5">
                        {agentLoopHandoffConsumers.map((consumer) => (
                          <div key={`agent-loop-handoff-consumer:${consumer.adapter}`} className="rounded p-1.5 min-w-0" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                            <div className="flex flex-wrap items-center justify-between gap-1">
                              <div className="text-[8px] font-semibold uppercase" style={{ color: "var(--mis-text)" }}>{consumer.adapter}</div>
                              <div className="flex flex-wrap gap-1">
                                <StatusBadge status={consumer.status} />
                                <StatusBadge status={consumer.ready_for_handoff ? "pass" : "blocked"} label={`${copy.handoffReady}: ${String(consumer.ready_for_handoff)}`} />
                                <StatusBadge status={consumer.ready_for_live_dispatch ? "pass" : "attention"} label={`${copy.liveDispatchReady}: ${String(consumer.ready_for_live_dispatch)}`} />
                              </div>
                            </div>
                            <div className="grid grid-cols-2 gap-1 mt-1">
                              {[
                                { label: copy.currentPhase, value: consumer.start_check.current_phase || "unknown", status: consumer.start_check.can_preview_loop ? "pass" : "blocked" },
                                { label: copy.methodGates, value: String(consumer.method.method_gate_ids.length), status: consumer.method.method_gate_ids.length >= 8 ? "pass" : "attention" },
                                { label: copy.freshLiveAdapters, value: consumer.live_product_readiness.status, status: consumer.live_product_readiness.fresh ? "pass" : "attention" },
                                { label: copy.boundedConfirmReady, value: String(consumer.ready_for_bounded_loop_confirm), status: consumer.ready_for_bounded_loop_confirm ? "pass" : "attention" },
                              ].map((item) => (
                                <div key={`${consumer.adapter}:handoff:${item.label}`} className="rounded px-1.5 py-0.5" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
                                  <div className="text-[8px]" style={{ color: "var(--mis-muted)" }}>{item.label}</div>
                                  <div className="flex items-center justify-between gap-1 mt-0.5">
                                    <span className="text-[8px] font-semibold truncate" style={{ color: "var(--mis-text)" }}>{item.value}</span>
                                    <StatusBadge status={item.status} />
                                  </div>
                                </div>
                              ))}
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                  {operatorLoopBootstrap && (
                    <div data-testid="operator-loop-bootstrap-panel" className="mt-2 pt-2" style={{ borderTop: "1px solid var(--mis-border)" }}>
                      <div className="flex flex-wrap items-center gap-1.5">
                        <div className="text-[8px] font-semibold" style={{ color: "var(--mis-text)" }}>{copy.loopBootstrapTitle}</div>
                        <StatusBadge status={operatorLoopBootstrap.status} />
                        <StatusBadge status={operatorLoopBootstrap.safety.read_only ? "pass" : "blocked"} label={operatorLoopBootstrap.safety.read_only ? "read-only" : "mutating"} />
                        <StatusBadge status={operatorLoopBootstrap.safety.server_executes_shell ? "blocked" : "pass"} label={operatorLoopBootstrap.safety.server_executes_shell ? "server shell" : "no server shell"} />
                        <StatusBadge status={operatorLoopBootstrap.safety.local_cli_service_check_performed ? "attention" : "pass"} label={operatorLoopBootstrap.safety.local_cli_service_check_performed ? "service-check ran" : "copy-only"} />
                        {panelStatusBadge("operator_loop_bootstrap")}
                        {panelRefreshButton("operator_loop_bootstrap")}
                        {panelDiagnosticsButton("operator_loop_bootstrap")}
                        {panelReceiptButton("operator_loop_bootstrap")}
                      </div>
                      <div className="text-[8px] mt-1 line-clamp-2" style={{ color: "var(--mis-muted)" }}>{copy.loopBootstrapSummary}</div>
                      <div data-testid="operator-loop-bootstrap-mode" className="inline-flex items-center gap-1 mt-1.5 rounded px-1 py-0.5" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                        <span className="text-[8px]" style={{ color: "var(--mis-muted)" }}>{copy.loopBootstrapMode}</span>
                        {([
                          ["fast", copy.loopBootstrapFast],
                          ["deep", copy.loopBootstrapDeep],
                        ] as const).map(([mode, label]) => {
                          const active = loopBootstrapMode === mode;
                          return (
                            <button
                              key={`loop-bootstrap-mode:${mode}`}
                              data-testid={`operator-loop-bootstrap-mode-${mode}`}
                              type="button"
                              onClick={() => setLoopBootstrapMode(mode)}
                              className="inline-flex items-center gap-1 text-[8px] px-1.5 py-0.5 rounded"
                              style={{
                                color: active ? "var(--mis-bg)" : "var(--mis-text)",
                                background: active ? "var(--mis-cyan)" : "transparent",
                                border: "1px solid var(--mis-border)",
                              }}
                            >
                              {label}
                            </button>
                          );
                        })}
                        <StatusBadge status={operatorLoopBootstrap.mode === "fast" ? "attention" : "pass"} label={operatorLoopBootstrap.mode || loopBootstrapMode} />
                      </div>
                      <div className="flex flex-wrap gap-1 mt-1.5">
                        {loopBootstrapCommands.map((item) => (
                          <button
                            key={`loop-bootstrap-command:${item.label}:${item.command}`}
                            type="button"
                            onClick={() => void copyIntakeCommand(String(item.command))}
                            className="inline-flex items-center gap-1 text-[8px] px-1.5 py-0.5 rounded max-w-full"
                            style={{ color: item.color, background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}
                            title={String(item.command)}
                          >
                            <Copy size={8} />
                            <span className="truncate max-w-[150px]">{copiedIntakeCommand === item.command ? copy.copiedCommand : item.label}</span>
                          </button>
                        ))}
                      </div>
                      <div className="grid grid-cols-1 lg:grid-cols-2 gap-1.5 mt-1.5">
                        {operatorLoopBootstrapItems.map((item) => {
                          const closureRequired = item.summary.service_closure_required;
                          const activeReady = item.summary.service_active_loop_ready;
                          const visibleSteps = item.bootstrap_steps.slice(0, 8);
                          return (
                            <div key={`loop-bootstrap-item:${item.adapter}`} data-testid="operator-loop-bootstrap-item" className="rounded p-1.5 min-w-0" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                              <div className="flex flex-wrap items-center justify-between gap-1">
                                <div className="text-[8px] font-semibold uppercase" style={{ color: "var(--mis-text)" }}>{item.adapter}</div>
                                <div className="flex flex-wrap gap-1">
                                  <StatusBadge status={item.status} />
                                  <StatusBadge status={item.summary.current_code_ok ? "pass" : "blocked"} label="current-code" />
                                  <StatusBadge status={closureRequired ? "attention" : "pass"} label={`${copy.serviceClosure}: ${String(closureRequired)}`} />
                                  <StatusBadge status={activeReady ? "pass" : "attention"} label={`${copy.serviceActive}: ${String(activeReady)}`} />
                                </div>
                              </div>
                              <div className="grid grid-cols-2 gap-1 mt-1">
                                {[
                                  { label: "Start-check", value: item.summary.start_check_status || "unknown", status: item.summary.current_code_ok ? "pass" : "blocked" },
                                  { label: "Supervision", value: item.summary.supervision_status || "unknown", status: item.summary.supervision_status || "unknown" },
                                  { label: "Managed loop", value: String(item.summary.service_managed_loop_ready), status: item.summary.service_managed_loop_ready ? "pass" : "attention" },
                                  { label: "Bounded loop", value: String(item.summary.can_confirm_bounded_loop), status: item.summary.can_confirm_bounded_loop ? "pass" : "attention" },
                                ].map((metric) => (
                                  <div key={`${item.adapter}:bootstrap-metric:${metric.label}`} className="rounded px-1.5 py-0.5 min-w-0" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
                                    <div className="text-[8px]" style={{ color: "var(--mis-muted)" }}>{metric.label}</div>
                                    <div className="flex items-center justify-between gap-1 mt-0.5">
                                      <span className="text-[8px] font-semibold truncate" style={{ color: "var(--mis-text)" }} title={metric.value}>{metric.value}</span>
                                      <StatusBadge status={metric.status} />
                                    </div>
                                  </div>
                                ))}
                              </div>
                              <div data-testid="operator-loop-bootstrap-steps" className="grid grid-cols-1 sm:grid-cols-2 gap-1 mt-1">
                                {visibleSteps.map((step, index) => (
                                  <div key={`${item.adapter}:bootstrap-step:${step.id}`} className="rounded px-1.5 py-1 min-w-0" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
                                    <div className="flex items-center justify-between gap-1">
                                      <span className="text-[8px] font-semibold truncate" style={{ color: "var(--mis-text)" }}>{index + 1}. {step.id}</span>
                                      <StatusBadge status={step.status || "unknown"} />
                                    </div>
                                    <div className="flex flex-wrap gap-1 mt-0.5">
                                      <StatusBadge status={step.confirm_required ? "approval_required" : "pass"} label={step.confirm_required ? copy.confirmRequired : "copy-only"} />
                                      <StatusBadge status={step.server_executes_shell ? "blocked" : "pass"} label={step.server_executes_shell ? "server shell" : "no server shell"} />
                                    </div>
                                    {step.command && (
                                      <button
                                        type="button"
                                        onClick={() => void copyIntakeCommand(String(step.command))}
                                        className="inline-flex items-center gap-1 text-[8px] px-1.5 py-0.5 rounded max-w-full mt-1"
                                        style={{ color: step.confirm_required ? "var(--mis-warning)" : "var(--mis-cyan)", background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}
                                        title={String(step.command)}
                                      >
                                        <Copy size={8} />
                                        <span className="truncate max-w-[170px]">{copiedIntakeCommand === step.command ? copy.copiedCommand : step.command}</span>
                                      </button>
                                    )}
                                  </div>
                                ))}
                              </div>
                              {item.next_action && (
                                <button
                                  type="button"
                                  onClick={() => void copyIntakeCommand(String(item.next_action || ""))}
                                  className="inline-flex items-center gap-1 text-[8px] px-1.5 py-0.5 rounded max-w-full mt-1"
                                  style={{ color: "var(--mis-green)", background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}
                                  title={String(item.next_action)}
                                >
                                  <Copy size={8} />
                                  <span className="truncate max-w-[220px]">{copiedIntakeCommand === item.next_action ? copy.copiedCommand : item.next_action}</span>
                                </button>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  )}
                  {operatorLoopSupervision && (
                    <div className="mt-2 pt-2" style={{ borderTop: "1px solid var(--mis-border)" }}>
                      <div className="flex flex-wrap items-center gap-1.5">
                        <div className="text-[8px] font-semibold" style={{ color: "var(--mis-text)" }}>{copy.loopSupervisionTitle}</div>
                        <StatusBadge status={operatorLoopSupervision.status} />
                        <StatusBadge status={operatorLoopSupervision.summary.can_confirm_all ? "pass" : "attention"} label={`${copy.boundedConfirmReady}: ${String(operatorLoopSupervision.summary.can_confirm_all)}`} />
                        <StatusBadge status={operatorLoopSupervision.summary.record_required ? "attention" : "pass"} label={`${copy.recordFirst}: ${String(operatorLoopSupervision.summary.record_required)}`} />
                        <StatusBadge status={operatorLoopSupervision.safety.server_executes_shell ? "blocked" : "pass"} label={operatorLoopSupervision.safety.server_executes_shell ? "server shell" : "copy-only"} />
                        {panelStatusBadge("operator_loop_supervision")}
                        {panelRefreshButton("operator_loop_supervision")}
                        {panelDiagnosticsButton("operator_loop_supervision")}
                        {panelReceiptButton("operator_loop_supervision")}
                      </div>
                      <div className="text-[8px] mt-1 line-clamp-2" style={{ color: "var(--mis-muted)" }}>{copy.loopSupervisionSummary}</div>
                      <div className="flex flex-wrap gap-1 mt-1.5">
                        {loopSupervisionCommands.map((item) => (
                          <button
                            key={`loop-supervision-command:${item.label}:${item.command}`}
                            type="button"
                            onClick={() => void copyIntakeCommand(String(item.command))}
                            className="inline-flex items-center gap-1 text-[8px] px-1.5 py-0.5 rounded max-w-full"
                            style={{ color: item.color, background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}
                            title={String(item.command)}
                          >
                            <Copy size={8} />
                            <span className="truncate max-w-[132px]">{copiedIntakeCommand === item.command ? copy.copiedCommand : item.label}</span>
                          </button>
                        ))}
                      </div>
                      <div className="grid grid-cols-1 lg:grid-cols-2 gap-1.5 mt-1.5">
                        {loopSupervisionItems.map((item) => {
                          const localDeploymentGate = item.gates.find((gate) => gate.id === "local_deployment");
                          const localRunPath = item.local_deployment?.local_run_path;
                          const serviceManagedLoop = item.local_deployment?.service_managed_loop;
                          const managedExecutionPath = item.local_deployment?.managed_execution_path;
                          const localDeploymentServerShell = localRunPath?.safety?.server_executes_shell === true
                            || localDeploymentGate?.server_executes_shell === true
                            || managedExecutionPath?.safety?.server_executes_shell === true;
                          const recommendedAdapter = localRunPath?.recommended_adapter || localDeploymentGate?.recommended_adapter || "missing";
                          const serviceManagedAdapter = serviceManagedLoop?.adapter || localDeploymentGate?.service_managed_adapter || "missing";
                          const managedExecutionAdapter = managedExecutionPath?.adapter || "missing";
                          const serviceManagedCommands = serviceManagedLoop?.commands || {};
                          const managedExecutionCommands = managedExecutionPath?.commands || {};
                          const managedExecutionGates = managedExecutionPath?.gates || [];
                          const managedExecutionPassCount = managedExecutionGates.filter((gate) => ["pass", "ready", "verified"].includes(String(gate.status || ""))).length;
                          const managedExecutionGateStatus = managedExecutionGates.some((gate) => ["blocked", "failed", "fail"].includes(String(gate.status || "")))
                            ? "blocked"
                            : managedExecutionGates.some((gate) => ["attention", "required", "confirm_required", "review_required"].includes(String(gate.status || "")))
                              ? "attention"
                              : managedExecutionGates.length > 0 ? "pass" : "unknown";
                          const managedExecutionRecommended = managedExecutionPath?.recommended_before_dispatch || "missing";
                          const managedExecutionReady = managedExecutionPath?.service_managed_loop_ready === true;
                          const managedExecutionLivePerformed = managedExecutionPath?.safety?.live_execution_performed === true;
                          const serviceManagedShell = serviceManagedLoop?.safety?.server_executes_shell === true;
                          const serviceManagedLivePerformed = serviceManagedLoop?.live_execution_performed === true || serviceManagedLoop?.safety?.live_execution_performed === true;
                          const serviceActiveReady = serviceManagedLoop?.service_active_loop_ready === true;
                          const serviceLoaded = serviceManagedLoop?.service_loaded === true;
                          const managedExecutionLaneCounts = `${managedExecutionPath?.first_safe_commands?.length || 0}/${managedExecutionPath?.confirm_required_commands?.length || 0}/${managedExecutionPath?.verify_commands?.length || 0}`;
                          const managedExecutionCommandGroups = [
                            { label: "First safe", color: "var(--mis-cyan)", commands: managedExecutionPath?.first_safe_commands || [] },
                            { label: "Confirm", color: "var(--mis-green)", commands: managedExecutionPath?.confirm_required_commands || [] },
                            { label: "Verify", color: "var(--mis-primary)", commands: managedExecutionPath?.verify_commands || [] },
                          ].filter((group) => group.commands.length > 0);
                          const managedExecutionTimeline = [
                            {
                              label: "Service check",
                              status: serviceManagedLoop?.service_check_available ? "pass" : "attention",
                              detail: serviceManagedLoop?.checked_status || "missing",
                              command: serviceManagedCommands.service_check,
                            },
                            {
                              label: "Receipt",
                              status: serviceManagedLoop?.receipt_verified ? "pass" : serviceManagedLoop?.receipt_required ? "attention" : "pass",
                              detail: serviceManagedLoop?.receipt_verified ? (serviceManagedLoop.receipt_id || "verified") : serviceManagedLoop?.receipt_required ? "required" : "not required",
                              command: serviceManagedCommands.record_verified_receipt,
                            },
                            {
                              label: "Control readback",
                              status: serviceManagedLoop?.control_readback_attached ? "pass" : serviceManagedLoop?.control_readback_required ? "attention" : "pass",
                              detail: serviceManagedLoop?.control_readback_attached ? (serviceManagedLoop.control_readback_id || "attached") : serviceManagedLoop?.control_readback_required ? "required" : "not required",
                              command: serviceManagedCommands.record_control_readback,
                            },
                            {
                              label: "Activation",
                              status: serviceActiveReady ? "pass" : serviceManagedLoop?.service_managed_loop_ready ? "attention" : "blocked",
                              detail: serviceLoaded ? "loaded" : (serviceManagedLoop?.active_status || serviceManagedLoop?.active_loop_status || "not loaded"),
                              command: serviceManagedCommands.service_control_load_confirm || managedExecutionCommands.service_control_load_confirm,
                            },
                            {
                              label: "Dispatch",
                              status: managedExecutionReady ? "approval_required" : "attention",
                              detail: managedExecutionReady ? "confirm required" : managedExecutionRecommended,
                              command: managedExecutionCommands.customer_worker_dispatch || managedExecutionPath?.confirm_required_commands?.[0],
                            },
                            {
                              label: "Evidence",
                              status: managedExecutionCommands.evidence_report || managedExecutionPath?.verify_commands?.[0] ? "attention" : "unknown",
                              detail: "evidence-report",
                              command: managedExecutionCommands.evidence_report || managedExecutionPath?.verify_commands?.[0],
                            },
                            {
                              label: "Review",
                              status: item.should_record_before_execute ? "attention" : "pass",
                              detail: item.should_record_before_execute ? "record first" : "ready",
                              command: managedExecutionCommands.review_queue || item.commands.record_review || managedExecutionPath?.verify_commands?.find((command) => command.includes("review queue")),
                            },
                          ];
                          const serviceManagedCommandButtons = [
                            { label: "service-check", command: serviceManagedCommands.service_check },
                            { label: "load-service", command: serviceManagedCommands.service_control_load_confirm || managedExecutionCommands.service_control_load_confirm },
                            { label: "record-receipt", command: serviceManagedCommands.record_verified_receipt },
                            { label: "record-readback", command: serviceManagedCommands.record_control_readback },
                          ].filter((entry) => entry.command);
                          const managedExecutionCommandButtons = [
                            { label: "agent-plan", command: managedExecutionCommands.agent_plan_create },
                            { label: "knowledge-search", command: managedExecutionCommands.knowledge_search },
                            { label: "base-reference", command: managedExecutionCommands.base_reference },
                            { label: "dispatch-task", command: managedExecutionCommands.customer_worker_dispatch },
                            { label: "evidence-report", command: managedExecutionCommands.evidence_report },
                            { label: "live-readiness", command: item.commands.live_product_readiness },
                            { label: "review-queue", command: managedExecutionCommands.review_queue || item.commands.record_review },
                          ].filter((entry) => entry.command);
                          const agentWorkPacket = item.agent_work_packet || {};
                          const primaryNextAction = typeof agentWorkPacket.primary_next_action === "object" && agentWorkPacket.primary_next_action !== null
                            ? agentWorkPacket.primary_next_action as Record<string, unknown>
                            : {};
                          const workPacketSafety = typeof agentWorkPacket.safety === "object" && agentWorkPacket.safety !== null
                            ? agentWorkPacket.safety as Record<string, unknown>
                            : {};
                          const workPacketHash = String(agentWorkPacket.packet_hash || "");
                          const workPacketSchema = String(agentWorkPacket.schema_version || "missing");
                          const workPacketPrimaryPhase = String(primaryNextAction.phase || "missing");
                          const workPacketPrimaryCommand = String(primaryNextAction.command || "");
                          const workPacketJson = Object.keys(agentWorkPacket).length ? JSON.stringify(agentWorkPacket, null, 2) : "";
                          const localDeploymentOk = localDeploymentGate?.ok === true
                            && recommendedAdapter === item.adapter
                            && serviceManagedAdapter === item.adapter
                            && managedExecutionAdapter === item.adapter
                            && !serviceManagedShell
                            && !serviceManagedLivePerformed
                            && !managedExecutionLivePerformed
                            && !localDeploymentServerShell;
                          return (
                          <div key={`loop-supervision-item:${item.adapter}`} className="rounded p-1.5 min-w-0" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                            <div className="flex flex-wrap items-center justify-between gap-1">
                              <div className="text-[8px] font-semibold uppercase" style={{ color: "var(--mis-text)" }}>{item.adapter}</div>
                              <div className="flex flex-wrap gap-1">
                                <StatusBadge status={item.status} />
                                <StatusBadge status={item.can_confirm_bounded_loop ? "pass" : "attention"} label={`${copy.boundedConfirmReady}: ${String(item.can_confirm_bounded_loop)}`} />
                                <StatusBadge status={item.should_record_before_execute ? "attention" : "pass"} label={`${copy.recordFirst}: ${String(item.should_record_before_execute)}`} />
                              </div>
                            </div>
                            <div className="grid grid-cols-2 gap-1 mt-1">
                              {[
                                { label: copy.currentPhase, value: item.status, status: item.status },
                                { label: copy.liveDispatchReady, value: String(item.ready_for_live_dispatch), status: item.ready_for_live_dispatch ? "pass" : "attention" },
                                { label: copy.memoryReview, value: String(item.review_pressure.memory_candidates ?? 0), status: Number(item.review_pressure.memory_candidates ?? 0) > 0 ? "attention" : "pass" },
                                { label: copy.pendingApprovals, value: String(item.review_pressure.pending_approvals ?? 0), status: Number(item.review_pressure.pending_approvals ?? 0) > 0 ? "attention" : "pass" },
                              ].map((metric) => (
                                <div key={`${item.adapter}:supervision:${metric.label}`} className="rounded px-1.5 py-0.5" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
                                  <div className="text-[8px]" style={{ color: "var(--mis-muted)" }}>{metric.label}</div>
                                  <div className="flex items-center justify-between gap-1 mt-0.5">
                                    <span className="text-[8px] font-semibold truncate" style={{ color: "var(--mis-text)" }}>{metric.value}</span>
                                    <StatusBadge status={metric.status} />
                                  </div>
                                </div>
                              ))}
                            </div>
                            {Object.keys(agentWorkPacket).length > 0 && (
                              <div
                                data-testid="operator-loop-supervision-agent-work-packet"
                                className="mt-1.5 rounded p-1.5 min-w-0"
                                style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}
                              >
                                <div className="flex flex-wrap items-center justify-between gap-1">
                                  <div className="text-[8px] font-semibold" style={{ color: "var(--mis-text)" }}>Agent work packet</div>
                                  <div className="flex flex-wrap gap-1">
                                    <StatusBadge status={workPacketSafety.server_executes_shell ? "blocked" : "pass"} label={workPacketSafety.server_executes_shell ? "server shell" : "no server shell"} />
                                    <StatusBadge status={workPacketSafety.live_execution_performed ? "blocked" : "pass"} label={workPacketSafety.live_execution_performed ? "live performed" : "no live execution"} />
                                    <StatusBadge status={primaryNextAction.confirm_required ? "approval_required" : "pass"} label={primaryNextAction.confirm_required ? "confirm" : "copy-only"} />
                                  </div>
                                </div>
                                <div className="grid grid-cols-2 gap-1 mt-1">
                                  {[
                                    { label: "Schema", value: workPacketSchema, status: workPacketSchema === "agent_work_packet_v1" ? "pass" : "attention" },
                                    { label: "Packet hash", value: workPacketHash ? workPacketHash.slice(0, 12) : "missing", status: workPacketHash ? "pass" : "attention" },
                                    { label: "Primary phase", value: workPacketPrimaryPhase, status: workPacketPrimaryPhase === "EXECUTE" ? "approval_required" : "pass" },
                                    { label: "Auto continue", value: String(primaryNextAction.safe_to_auto_continue ?? false), status: primaryNextAction.safe_to_auto_continue ? "pass" : "attention" },
                                  ].map((metric) => (
                                    <div key={`${item.adapter}:agent-work-packet:${metric.label}`} className="rounded px-1.5 py-0.5 min-w-0" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                                      <div className="text-[8px]" style={{ color: "var(--mis-muted)" }}>{metric.label}</div>
                                      <div className="flex items-center justify-between gap-1 mt-0.5">
                                        <span className="text-[8px] font-semibold truncate" style={{ color: "var(--mis-text)" }} title={metric.value}>{metric.value}</span>
                                        <StatusBadge status={metric.status} />
                                      </div>
                                    </div>
                                  ))}
                                </div>
                                <div className="flex flex-wrap gap-1 mt-1">
                                  {workPacketPrimaryCommand && (
                                    <button
                                      type="button"
                                      onClick={() => void copyIntakeCommand(workPacketPrimaryCommand)}
                                      className="inline-flex items-center gap-1 text-[8px] px-1.5 py-0.5 rounded max-w-full"
                                      style={{ color: "var(--mis-green)", background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}
                                      title={workPacketPrimaryCommand}
                                    >
                                      <Copy size={8} />
                                      <span className="truncate max-w-[160px]">{copiedIntakeCommand === workPacketPrimaryCommand ? copy.copiedCommand : "primary-next"}</span>
                                    </button>
                                  )}
                                  {workPacketJson && (
                                    <button
                                      type="button"
                                      onClick={() => void copyIntakeCommand(workPacketJson)}
                                      className="inline-flex items-center gap-1 text-[8px] px-1.5 py-0.5 rounded max-w-full"
                                      style={{ color: "var(--mis-cyan)", background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}
                                      title={workPacketJson}
                                    >
                                      <Copy size={8} />
                                      <span className="truncate max-w-[160px]">{copiedIntakeCommand === workPacketJson ? copy.copiedCommand : "copy packet JSON"}</span>
                                    </button>
                                  )}
                                </div>
                              </div>
                            )}
                            <div
                              data-testid="operator-loop-supervision-local-deployment"
                              className="mt-1.5 rounded p-1.5 min-w-0"
                              style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}
                            >
                              <div className="flex flex-wrap items-center justify-between gap-1">
                                <div className="text-[8px] font-semibold" style={{ color: "var(--mis-text)" }}>{copy.localDeploymentGate}</div>
                                <div className="flex flex-wrap gap-1">
                                  <StatusBadge status={localDeploymentOk ? "pass" : "blocked"} />
                                  <StatusBadge status={localDeploymentServerShell ? "blocked" : "pass"} label={localDeploymentServerShell ? "server shell" : "no server shell"} />
                                </div>
                              </div>
                              <div className="grid grid-cols-2 gap-1 mt-1">
                                {[
                                  { label: copy.deploymentRecommendedAdapter, value: String(recommendedAdapter), status: recommendedAdapter === item.adapter ? "pass" : "blocked" },
                                  { label: copy.serviceManagedAdapter, value: String(serviceManagedAdapter), status: serviceManagedAdapter === item.adapter ? "pass" : "blocked" },
                                  { label: "Managed path", value: managedExecutionPath?.operation || "missing", status: managedExecutionAdapter === item.adapter ? "pass" : "blocked" },
                                  { label: "Managed status", value: managedExecutionPath?.status || "missing", status: managedExecutionReady ? "pass" : "attention" },
                                  { label: "Before dispatch", value: managedExecutionRecommended, status: managedExecutionRecommended.includes("dispatch") ? "pass" : "attention" },
                                  { label: "Gate pass", value: `${managedExecutionPassCount}/${managedExecutionGates.length || 0}`, status: managedExecutionGateStatus },
                                  { label: "Safe/confirm/verify", value: managedExecutionLaneCounts, status: (managedExecutionPath?.confirm_required_commands?.length || 0) > 0 && (managedExecutionPath?.verify_commands?.length || 0) > 0 ? "pass" : "attention" },
                                  { label: copy.serverShellBoundary, value: String(localDeploymentServerShell), status: localDeploymentServerShell ? "blocked" : "pass" },
                                ].map((metric) => (
                                  <div key={`${item.adapter}:local-deployment:${metric.label}`} className="rounded px-1.5 py-0.5 min-w-0" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                                    <div className="text-[8px]" style={{ color: "var(--mis-muted)" }}>{metric.label}</div>
                                    <div className="flex items-center justify-between gap-1 mt-0.5">
                                      <span className="text-[8px] font-semibold truncate" style={{ color: "var(--mis-text)" }} title={metric.value}>{metric.value}</span>
                                      <StatusBadge status={metric.status} />
                                    </div>
                                  </div>
                                ))}
                              </div>
                              {serviceManagedCommandButtons.length > 0 && (
                                <div className="flex flex-wrap gap-1 mt-1">
                                  {serviceManagedCommandButtons.map((commandItem) => (
                                    <button
                                      key={`${item.adapter}:service-managed-command:${commandItem.label}`}
                                      type="button"
                                      onClick={() => void copyIntakeCommand(String(commandItem.command))}
                                      className="inline-flex items-center gap-1 text-[8px] px-1.5 py-0.5 rounded max-w-full"
                                      style={{ color: "var(--mis-cyan)", background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}
                                      title={String(commandItem.command)}
                                    >
                                      <Copy size={8} />
                                      <span className="truncate max-w-[132px]">{copiedIntakeCommand === commandItem.command ? copy.copiedCommand : commandItem.label}</span>
                                    </button>
                                  ))}
                                </div>
                              )}
                              {managedExecutionGates.length > 0 && (
                                <div data-testid="operator-loop-supervision-managed-execution-gates" className="flex flex-wrap gap-1 mt-1">
                                  {managedExecutionGates.map((gate) => (
                                    <span
                                      key={`${item.adapter}:managed-execution-gate:${gate.id}`}
                                      title={gate.proof || gate.id}
                                      className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 max-w-full min-w-0"
                                      style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}
                                    >
                                      <span className="text-[8px] font-semibold truncate max-w-[150px]" style={{ color: "var(--mis-text)" }}>{gate.id}</span>
                                      <StatusBadge status={gate.status || "unknown"} />
                                    </span>
                                  ))}
                                </div>
                              )}
                              <div data-testid="operator-loop-supervision-managed-execution-timeline" className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-1 mt-1">
                                {managedExecutionTimeline.map((step, stepIndex) => (
                                  <div key={`${item.adapter}:managed-execution-timeline:${step.label}`} className="rounded px-1.5 py-1 min-w-0" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                                    <div className="flex items-center justify-between gap-1">
                                      <span className="text-[8px] font-semibold truncate" style={{ color: "var(--mis-text)" }}>{stepIndex + 1}. {step.label}</span>
                                      <StatusBadge status={step.status} />
                                    </div>
                                    <div className="text-[8px] mt-0.5 truncate" style={{ color: "var(--mis-muted)" }} title={step.detail}>{step.detail}</div>
                                    {step.command && (
                                      <button
                                        type="button"
                                        onClick={() => void copyIntakeCommand(String(step.command))}
                                        className="inline-flex items-center gap-1 text-[8px] px-1.5 py-0.5 rounded max-w-full mt-1"
                                        style={{ color: "var(--mis-cyan)", background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}
                                        title={String(step.command)}
                                      >
                                        <Copy size={8} />
                                        <span className="truncate max-w-[150px]">{copiedIntakeCommand === step.command ? copy.copiedCommand : step.command}</span>
                                      </button>
                                    )}
                                  </div>
                                ))}
                              </div>
                              {managedExecutionCommandGroups.length > 0 && (
                                <div data-testid="operator-loop-supervision-managed-execution-command-groups" className="grid grid-cols-1 gap-1 mt-1">
                                  {managedExecutionCommandGroups.map((group) => (
                                    <div key={`${item.adapter}:managed-execution-group:${group.label}`} className="rounded px-1.5 py-1 min-w-0" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                                      <div className="flex items-center justify-between gap-1">
                                        <span className="text-[8px] font-semibold" style={{ color: "var(--mis-muted)" }}>{group.label}</span>
                                        <StatusBadge status={group.label === "Confirm" ? "approval_required" : "pass"} label={`${group.commands.length}`} />
                                      </div>
                                      <div className="flex flex-wrap gap-1 mt-1">
                                        {group.commands.slice(0, 3).map((command, commandIndex) => (
                                          <button
                                            key={`${item.adapter}:managed-execution-group:${group.label}:${commandIndex}`}
                                            type="button"
                                            onClick={() => void copyIntakeCommand(command)}
                                            className="inline-flex items-center gap-1 text-[8px] px-1.5 py-0.5 rounded max-w-full"
                                            style={{ color: group.color, background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}
                                            title={command}
                                          >
                                            <Copy size={8} />
                                            <span className="truncate max-w-[160px]">{copiedIntakeCommand === command ? copy.copiedCommand : command}</span>
                                          </button>
                                        ))}
                                      </div>
                                    </div>
                                  ))}
                                </div>
                              )}
                              {managedExecutionCommandButtons.length > 0 && (
                                <div className="flex flex-wrap gap-1 mt-1">
                                  {managedExecutionCommandButtons.map((commandItem) => (
                                    <button
                                      key={`${item.adapter}:managed-execution-command:${commandItem.label}`}
                                      type="button"
                                      onClick={() => void copyIntakeCommand(String(commandItem.command))}
                                      className="inline-flex items-center gap-1 text-[8px] px-1.5 py-0.5 rounded max-w-full"
                                      style={{ color: "var(--mis-green)", background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}
                                      title={String(commandItem.command)}
                                    >
                                      <Copy size={8} />
                                      <span className="truncate max-w-[132px]">{copiedIntakeCommand === commandItem.command ? copy.copiedCommand : commandItem.label}</span>
                                    </button>
                                  ))}
                                </div>
                              )}
                            </div>
                            {item.run_start_admission && (
                              <div
                                className="mt-1.5 rounded p-1.5 min-w-0"
                                style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}
                              >
                                <div className="flex flex-wrap items-center justify-between gap-1">
                                  <div className="text-[8px] font-semibold" style={{ color: "var(--mis-text)" }}>{copy.gatewayRunStartGate}</div>
                                  <div className="flex flex-wrap gap-1">
                                    <StatusBadge status={item.run_start_admission.status} />
                                    <StatusBadge status={item.run_start_admission.would_allow_run_start ? "pass" : "blocked"} label={`${copy.wouldAllowRunStart}: ${String(item.run_start_admission.would_allow_run_start)}`} />
                                    <StatusBadge status={item.run_start_admission.no_run_created_on_block ? "pass" : "blocked"} label={`${copy.noRunOnBlock}: ${String(item.run_start_admission.no_run_created_on_block)}`} />
                                    <StatusBadge status={item.run_start_admission.safety?.server_executes_shell ? "blocked" : "pass"} label={item.run_start_admission.safety?.server_executes_shell ? "server shell" : "no server shell"} />
                                  </div>
                                </div>
                                <div className="text-[8px] mt-1 line-clamp-2" style={{ color: "var(--mis-muted)" }}>{copy.gatewayRunStartSummary}</div>
                                <div className="grid grid-cols-2 gap-1 mt-1">
                                  {[
                                    { label: "HTTP", value: item.run_start_admission.fail_closed_error, status: item.run_start_admission.would_block_run_start ? "blocked" : "pass" },
                                    { label: copy.hashBinding, value: item.run_start_admission.supervision_hash_state, status: item.run_start_admission.run_metadata_field === "loop_supervision_hash" ? "pass" : "attention" },
                                    { label: "Agent Plan", value: String(item.run_start_admission.agent_plan_required), status: item.run_start_admission.agent_plan_required ? "pass" : "blocked" },
                                    { label: "Endpoint", value: item.run_start_admission.gateway_endpoint, status: item.run_start_admission.governed_runtime ? "pass" : "attention" },
                                  ].map((metric) => (
                                    <div key={`${item.adapter}:run-start-admission:${metric.label}`} className="rounded px-1.5 py-0.5 min-w-0" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                                      <div className="text-[8px]" style={{ color: "var(--mis-muted)" }}>{metric.label}</div>
                                      <div className="flex items-center justify-between gap-1 mt-0.5">
                                        <span className="text-[8px] font-semibold truncate" style={{ color: "var(--mis-text)" }} title={metric.value}>{metric.value}</span>
                                        <StatusBadge status={metric.status} />
                                      </div>
                                    </div>
                                  ))}
                                </div>
                                {item.run_start_admission.receipt_projection && (
                                  <div data-testid="operator-loop-supervision-run-start-receipt-projection" className="grid grid-cols-2 gap-1 mt-1">
                                    {[
                                      { label: "Receipt source", value: item.run_start_admission.receipt_projection?.source || "missing", status: item.run_start_admission.receipt_projection?.source ? "pass" : "attention" },
                                      { label: "Action id", value: item.run_start_admission.receipt_projection?.action_id || "missing", status: item.run_start_admission.receipt_projection?.action_id ? "pass" : "attention" },
                                      { label: "Signature", value: item.run_start_admission.receipt_projection?.action_signature || "missing", status: item.run_start_admission.receipt_projection?.action_signature ? "pass" : "attention" },
                                      { label: "Readback source", value: item.run_start_admission.receipt_projection?.control_readback_source || "missing", status: item.run_start_admission.receipt_projection?.control_readback_required ? "attention" : "pass" },
                                    ].map((metric) => (
                                      <div key={`${item.adapter}:run-start-receipt:${metric.label}`} className="rounded px-1.5 py-0.5 min-w-0" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                                        <div className="text-[8px]" style={{ color: "var(--mis-muted)" }}>{metric.label}</div>
                                        <div className="flex items-center justify-between gap-1 mt-0.5">
                                          <span className="text-[8px] font-semibold truncate" style={{ color: "var(--mis-text)" }} title={metric.value}>{metric.value}</span>
                                          <StatusBadge status={metric.status} />
                                        </div>
                                      </div>
                                    ))}
                                  </div>
                                )}
                                {item.run_start_admission.receipt_projection?.action_command && (
                                  <button
                                    type="button"
                                    onClick={() => void copyIntakeCommand(String(item.run_start_admission?.receipt_projection?.action_command || ""))}
                                    className="inline-flex items-center gap-1 text-[8px] px-1.5 py-0.5 rounded max-w-full mt-1"
                                    style={{ color: "var(--mis-green)", background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}
                                    title={String(item.run_start_admission.receipt_projection.action_command)}
                                  >
                                    <Copy size={8} />
                                    <span className="truncate max-w-[180px]">{copiedIntakeCommand === item.run_start_admission.receipt_projection.action_command ? copy.copiedCommand : item.run_start_admission.receipt_projection.action_command}</span>
                                  </button>
                                )}
                                {item.run_start_admission.receipt_projection?.verify_command && (
                                  <button
                                    type="button"
                                    onClick={() => void copyIntakeCommand(String(item.run_start_admission?.receipt_projection?.verify_command || ""))}
                                    className="inline-flex items-center gap-1 text-[8px] px-1.5 py-0.5 rounded max-w-full mt-1 ml-1"
                                    style={{ color: "var(--mis-cyan)", background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}
                                    title={String(item.run_start_admission.receipt_projection.verify_command)}
                                  >
                                    <Copy size={8} />
                                    <span className="truncate max-w-[180px]">{copiedIntakeCommand === item.run_start_admission.receipt_projection.verify_command ? copy.copiedCommand : item.run_start_admission.receipt_projection.verify_command}</span>
                                  </button>
                                )}
                                {item.run_start_admission.recommended_next && (
                                  <button
                                    type="button"
                                    onClick={() => void copyIntakeCommand(String(item.run_start_admission?.recommended_next || ""))}
                                    className="inline-flex items-center gap-1 text-[8px] px-1.5 py-0.5 rounded max-w-full mt-1"
                                    style={{ color: "var(--mis-cyan)", background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}
                                    title={String(item.run_start_admission.recommended_next)}
                                  >
                                    <Copy size={8} />
                                    <span className="truncate max-w-[180px]">{copiedIntakeCommand === item.run_start_admission.recommended_next ? copy.copiedCommand : item.run_start_admission.recommended_next}</span>
                                  </button>
                                )}
                              </div>
                            )}
                          </div>
                          );
                        })}
                      </div>
                    </div>
                  )}
                  {loopDriverPacketItems.length > 0 && (
                    <div className="mt-2 pt-2" style={{ borderTop: "1px solid var(--mis-border)" }}>
                      <div className="flex flex-wrap items-center gap-1.5">
                        <div className="text-[8px] font-semibold" style={{ color: "var(--mis-text)" }}>{copy.loopDriverAgentPacket}</div>
                        <span className="text-[8px]" style={{ color: "var(--mis-muted)" }}>{copy.loopDriverAgentPacketSummary}</span>
                      </div>
                      <div className="grid grid-cols-1 lg:grid-cols-2 gap-1.5 mt-1.5">
                        {loopDriverPacketItems.map((packet) => {
                          const startCheck = operatorLoopDriverPackets?.start_checks?.[packet.adapter];
                          const admissionPacket = (startCheck?.local_loop_admission_packet || {}) as Record<string, unknown>;
                          const admission = (typeof admissionPacket.admission === "object" && admissionPacket.admission !== null ? admissionPacket.admission : {}) as Record<string, unknown>;
                          const admissionSafety = (typeof admissionPacket.safety === "object" && admissionPacket.safety !== null ? admissionPacket.safety : {}) as Record<string, unknown>;
                          const firstSafeCommands = Array.isArray(admissionPacket.first_safe_commands)
                            ? admissionPacket.first_safe_commands.map(String).filter(Boolean).slice(0, 8)
                            : [];
                          const confirmRequiredCommands = Array.isArray(admissionPacket.confirm_required_commands)
                            ? admissionPacket.confirm_required_commands.map(String).filter(Boolean).slice(0, 6)
                            : [];
                          return (
                          <div key={`loop-driver-packet:${packet.adapter}`} className="rounded p-1.5 min-w-0" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
                            <div className="flex flex-wrap items-center justify-between gap-1">
                              <div className="text-[8px] font-semibold uppercase" style={{ color: "var(--mis-text)" }}>{packet.adapter}</div>
                              <div className="flex flex-wrap gap-1">
                                <StatusBadge status={packet.current_phase === "blocked" ? "blocked" : "pass"} label={`${copy.currentPhase}: ${packet.current_phase}`} />
                                <StatusBadge status={packet.ready_to_confirm_loop ? "pass" : "attention"} label={`${copy.readyToConfirmLoop}: ${String(packet.ready_to_confirm_loop)}`} />
                              </div>
                            </div>
                            <div className="grid grid-cols-2 gap-1 mt-1">
                              {packet.phases.slice(0, 8).map((phase) => (
                                <button
                                  key={`${packet.adapter}:${phase.phase}`}
                                  type="button"
                                  disabled={!phase.command}
                                  onClick={() => phase.command && void copyIntakeCommand(String(phase.command))}
                                  className="flex items-center justify-between gap-1 rounded px-1.5 py-0.5 text-left disabled:opacity-60"
                                  style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)", color: "var(--mis-text)" }}
                                  title={phase.command || `${copy.phase}: ${phase.phase}`}
                                >
                                  <span className="truncate text-[8px]">{phase.phase}</span>
                                  <span className="inline-flex items-center gap-1 shrink-0 text-[8px]" style={{ color: phase.command ? "var(--mis-cyan)" : "var(--mis-muted)" }}>
                                    {phase.command && <Copy size={8} />}
                                    {phase.status}
                                  </span>
                                </button>
                              ))}
                            </div>
                            {admissionPacket.operation === "operator_local_loop_admission_packet" && (
                              <div className="mt-1.5 pt-1.5" style={{ borderTop: "1px solid var(--mis-border)" }}>
                                <div className="flex flex-wrap items-center gap-1.5">
                                  <div className="text-[8px] font-semibold" style={{ color: "var(--mis-muted)" }}>{copy.localLoopAdmission}</div>
                                  <StatusBadge status={admission.can_confirm_bounded_loop ? "pass" : "attention"} label={`${copy.readyToConfirmLoop}: ${String(Boolean(admission.can_confirm_bounded_loop))}`} />
                                  <StatusBadge status={admissionSafety.server_executes_shell ? "blocked" : "pass"} label={admissionSafety.server_executes_shell ? "server shell" : "copy-only"} />
                                </div>
                                <div className="grid grid-cols-1 sm:grid-cols-2 gap-1 mt-1">
                                  <div className="min-w-0">
                                    <div className="text-[8px] mb-0.5" style={{ color: "var(--mis-muted)" }}>{copy.firstSafeCommands}</div>
                                    <div className="flex flex-col gap-1">
                                      {firstSafeCommands.map((command, index) => (
                                        <button
                                          key={`${packet.adapter}:admission:first:${index}:${command}`}
                                          type="button"
                                          onClick={() => void copyIntakeCommand(command)}
                                          className="flex items-center gap-1 rounded px-1.5 py-0.5 text-left"
                                          style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)", color: "var(--mis-text)" }}
                                          title={command}
                                        >
                                          <Copy size={8} />
                                          <span className="truncate text-[8px]">{command}</span>
                                        </button>
                                      ))}
                                    </div>
                                  </div>
                                  <div className="min-w-0">
                                    <div className="text-[8px] mb-0.5" style={{ color: "var(--mis-muted)" }}>{copy.confirmCommands}</div>
                                    <div className="flex flex-col gap-1">
                                      {confirmRequiredCommands.map((command, index) => (
                                        <button
                                          key={`${packet.adapter}:admission:confirm:${index}:${command}`}
                                          type="button"
                                          onClick={() => void copyIntakeCommand(command)}
                                          className="flex items-center gap-1 rounded px-1.5 py-0.5 text-left"
                                          style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)", color: "var(--mis-warning)" }}
                                          title={command}
                                        >
                                          <Copy size={8} />
                                          <span className="truncate text-[8px]">{command}</span>
                                        </button>
                                      ))}
                                    </div>
                                  </div>
                                </div>
                              </div>
                            )}
                            {(packet.method_gates || []).length > 0 && (
                              <div className="mt-1.5 pt-1.5" style={{ borderTop: "1px solid var(--mis-border)" }}>
                                <div className="text-[8px] font-semibold mb-1" style={{ color: "var(--mis-muted)" }}>{copy.methodGates}</div>
                                <div className="grid grid-cols-1 sm:grid-cols-2 gap-1">
                                  {(packet.method_gates || []).slice(0, 8).map((gate) => {
                                    const gateStatus = gate.status === "blocked" || gate.status === "fail"
                                      ? "blocked"
                                      : gate.status === "ready" || gate.status === "pass"
                                        ? "pass"
                                        : gate.required
                                          ? "attention"
                                          : "pass";
                                    return (
                                      <button
                                        key={`${packet.adapter}:method-gate:${gate.id}`}
                                        type="button"
                                        disabled={!gate.command}
                                        onClick={() => gate.command && void copyIntakeCommand(String(gate.command))}
                                        className="flex items-center justify-between gap-1 rounded px-1.5 py-0.5 text-left disabled:opacity-60"
                                        style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)", color: "var(--mis-text)" }}
                                        title={gate.proof || gate.command || gate.id}
                                      >
                                        <span className="truncate text-[8px]">{gate.id}</span>
                                        <span className="inline-flex items-center gap-1 shrink-0 text-[8px]" style={{ color: gate.command ? "var(--mis-cyan)" : "var(--mis-muted)" }}>
                                          {gate.command && <Copy size={8} />}
                                          <StatusBadge status={gateStatus} label={gate.required ? "required" : "optional"} />
                                        </span>
                                      </button>
                                    );
                                  })}
                                </div>
                              </div>
                            )}
                          </div>
                          );
                        })}
                      </div>
                    </div>
                  )}
                </div>
              </div>
            </div>
          </div>
        )}

        <div className="rounded-lg p-3 mt-4" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
          <div className="flex flex-col xl:flex-row xl:items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <Terminal size={13} style={{ color: "var(--mis-cyan)" }} />
                <div className="text-[11px] font-semibold" style={{ color: "var(--mis-text)" }}>{copy.runtimeDoctorTitle}</div>
                <StatusBadge status={operatorRuntimeDoctor?.status || "unknown"} />
                <StatusBadge status={operatorRuntimeDoctor?.safety?.read_only && !operatorRuntimeDoctor?.safety?.ledger_mutated ? "pass" : "attention"} label={operatorRuntimeDoctor?.safety?.read_only ? copy.readOnlyProof : copy.statusAttention} />
                <StatusBadge status={operatorRuntimeDoctor?.safety?.server_executes_shell ? "blocked" : "pass"} label={operatorRuntimeDoctor?.safety?.server_executes_shell ? "server shell" : "copy-only"} />
                {panelStatusBadge("operator_runtime_doctor")}
                {panelRefreshButton("operator_runtime_doctor")}
                {panelDiagnosticsButton("operator_runtime_doctor")}
                {panelReceiptButton("operator_runtime_doctor")}
              </div>
              <p className="text-[10px] mt-1 max-w-4xl" style={{ color: "var(--mis-dim)" }}>{copy.runtimeDoctorSummary}</p>
              {operatorRuntimeDoctor?.contract && (
                <p className="text-[10px] mt-1 max-w-4xl truncate" style={{ color: "var(--mis-muted)" }}>{copy.contract}: {operatorRuntimeDoctor.contract}</p>
              )}
              <div className="flex flex-wrap gap-1.5 mt-2">
                {(runtimeDoctorSummary?.ready_adapters || []).map((adapter) => (
                  <StatusBadge key={adapter} status="pass" label={`${adapter}: ${copy.liveReady}`} />
                ))}
                {(runtimeDoctorSummary?.requires_confirm_run || []).map((adapter) => (
                  <StatusBadge key={`confirm-${adapter}`} status="attention" label={`${adapter}: ${copy.statusConfirm}`} />
                ))}
                {(runtimeDoctorSummary?.requires_prepared_action || []).map((adapter) => (
                  <StatusBadge key={`prepared-${adapter}`} status="pass" label={`${adapter}: prepared action`} />
                ))}
              </div>
            </div>
            <div className="flex flex-wrap xl:justify-end gap-1.5 shrink-0">
              {(runtimeDoctorPrimaryCommands.length ? runtimeDoctorPrimaryCommands : ["agentops operator runtime-doctor --limit 8"]).map((command) => (
                <button
                  key={command}
                  onClick={() => void copyIntakeCommand(command)}
                  className="inline-flex items-center gap-1 text-[10px] px-2 py-1 rounded"
                  style={{ color: "var(--mis-cyan)", background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}
                  title={command}
                >
                  <Copy size={11} />
                  {copiedIntakeCommand === command ? copy.copiedCommand : copy.copyCommand}
                </button>
              ))}
            </div>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-2 mt-3">
            {runtimeDoctorTopGates.map((gate) => (
              <div key={gate.id} className="rounded px-2 py-1" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                <div className="flex items-center justify-between gap-2">
                  <div className="text-[9px] font-semibold truncate" style={{ color: "var(--mis-text)" }}>{gate.label}</div>
                  <StatusBadge status={gate.status} />
                </div>
                <div className="text-[9px] mt-1 line-clamp-2" style={{ color: "var(--mis-muted)" }}>{gate.detail || gate.next_action || "—"}</div>
              </div>
            ))}
            {runtimeDoctorTopGates.length === 0 && (
              <div className="text-[10px] rounded px-3 py-2" style={{ color: "var(--mis-muted)", background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                {copy.backendUnavailable}: agentops operator runtime-doctor --limit 8
              </div>
            )}
          </div>
        </div>

        <div className="rounded-lg p-3 mt-4" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
          <div className="flex flex-col lg:flex-row lg:items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <ShieldCheck size={13} style={{ color: operatorEvidenceReport?.status === "blocked" ? "var(--mis-warning)" : "var(--mis-success)" }} />
                <div className="text-[11px] font-semibold" style={{ color: "var(--mis-text)" }}>{copy.evidenceReportTitle}</div>
                <StatusBadge status={operatorEvidenceReport?.status || "unknown"} />
                {panelStatusBadge("operator_evidence_report")}
                {panelRefreshButton("operator_evidence_report")}
                {panelDiagnosticsButton("operator_evidence_report")}
                {panelReceiptButton("operator_evidence_report")}
                <StatusBadge status={operatorEvidenceReport?.safety?.read_only && !operatorEvidenceReport?.safety?.ledger_mutated ? "pass" : "attention"} label={operatorEvidenceReport?.safety?.read_only ? copy.readOnlyProof : copy.statusAttention} />
              </div>
              <p className="text-[10px] mt-1 max-w-4xl" style={{ color: "var(--mis-dim)" }}>{copy.evidenceReportSummary}</p>
              {panelEvidenceLine("operator_evidence_report")}
              {operatorEvidenceReport?.contract && (
                <p className="text-[10px] mt-1 max-w-4xl truncate" style={{ color: "var(--mis-muted)" }}>{copy.contract}: {operatorEvidenceReport.contract}</p>
              )}
            </div>
            <div className="flex flex-wrap gap-1.5 shrink-0">
              {(operatorEvidenceCommands.slice(0, 2).length ? operatorEvidenceCommands.slice(0, 2) : ["agentops operator evidence-report --limit 8"]).map((command) => (
                <button
                  key={command}
                  onClick={() => void copyIntakeCommand(command)}
                  className="inline-flex items-center gap-1 text-[10px] px-2 py-1 rounded"
                  style={{ color: "var(--mis-cyan)", background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}
                  title={command}
                >
                  <Copy size={11} />
                  {copiedIntakeCommand === command ? copy.copiedCommand : copy.copyCommand}
                </button>
              ))}
            </div>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-6 gap-2 mt-3">
            {[
              { label: copy.evidenceReportReady, value: `${operatorEvidenceSummary?.ready ?? 0}/${operatorEvidenceSummary?.runs ?? 0}`, status: (operatorEvidenceSummary?.ready || 0) > 0 ? "pass" : "attention" },
              { label: copy.evidenceReportBlocked, value: operatorEvidenceSummary?.blocked ?? 0, status: (operatorEvidenceSummary?.blocked || 0) > 0 ? "blocked" : "pass" },
              { label: copy.planEvidence, value: operatorEvidenceSummary?.verified_plan_evidence_manifests ?? 0, status: (operatorEvidenceSummary?.verified_plan_evidence_manifests || 0) > 0 ? "pass" : "attention" },
              { label: copy.missingManifests, value: operatorEvidenceSummary?.missing_plan_evidence_manifests ?? 0, status: (operatorEvidenceSummary?.missing_plan_evidence_manifests || 0) > 0 ? "blocked" : "pass" },
              { label: copy.pendingApprovals, value: operatorEvidenceSummary?.pending_approvals ?? 0, status: (operatorEvidenceSummary?.pending_approvals || 0) > 0 ? "attention" : "pass" },
              { label: copy.memoryReview, value: `${operatorEvidenceSummary?.memory_review_ready ?? 0}/${operatorEvidenceSummary?.memory_reviews ?? 0}`, status: ((operatorEvidenceSummary?.missing_memory_reviews || 0) + (operatorEvidenceSummary?.pending_memory_reviews || 0)) > 0 ? "attention" : "pass" },
              { label: copy.verifiedReceipts, value: `${operatorEvidenceSummary?.verified_action_receipts ?? 0}/${operatorEvidenceSummary?.action_receipts ?? 0}`, status: (operatorEvidenceSummary?.verified_action_receipts || 0) > 0 ? "pass" : "planned" },
              { label: copy.workerKnowledgeReady, value: `${operatorEvidenceSummary?.worker_knowledge_retrieval_ready ?? 0}/${operatorEvidenceSummary?.worker_runs ?? 0}`, status: (operatorEvidenceSummary?.worker_knowledge_retrieval_ready || 0) > 0 ? "pass" : (operatorEvidenceSummary?.worker_runs || 0) > 0 ? "attention" : "planned" },
              { label: copy.workerKnowledgeMissing, value: (operatorEvidenceSummary?.worker_knowledge_retrieval_missing ?? 0) + (operatorEvidenceSummary?.worker_knowledge_retrieval_unavailable ?? 0), status: ((operatorEvidenceSummary?.worker_knowledge_retrieval_missing || 0) + (operatorEvidenceSummary?.worker_knowledge_retrieval_unavailable || 0)) > 0 ? "blocked" : "pass" },
              { label: copy.workerRuntimeSummaryReady, value: `${operatorEvidenceSummary?.worker_runtime_summary_ready ?? 0}/${operatorEvidenceSummary?.worker_runs ?? 0}`, status: (operatorEvidenceSummary?.worker_runtime_summary_ready || 0) > 0 ? "pass" : (operatorEvidenceSummary?.worker_runs || 0) > 0 ? "attention" : "planned" },
              { label: copy.workerRuntimeSummaryMissing, value: operatorEvidenceSummary?.worker_runtime_summary_missing ?? 0, status: (operatorEvidenceSummary?.worker_runtime_summary_missing || 0) > 0 ? "blocked" : "pass" },
            ].map((item) => (
              <div key={item.label} className="rounded px-2 py-1" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                <div className="text-[9px]" style={{ color: "var(--mis-muted)" }}>{item.label}</div>
                <div className="flex items-center justify-between gap-2 mt-0.5">
                  <div className="text-[10px] font-semibold truncate" style={{ color: item.status === "blocked" ? "#F87171" : "var(--mis-text)" }}>{item.value}</div>
                  <StatusBadge status={item.status} />
                </div>
              </div>
            ))}
          </div>
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-2 mt-3">
            {operatorEvidenceTopRuns.length === 0 && (
              <div className="text-[10px] rounded px-3 py-2" style={{ color: "var(--mis-muted)", background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                {copy.noRecommendedActions}
              </div>
            )}
            {operatorEvidenceTopRuns.map((run) => {
              const counts = run.evidence_counts || {};
              const firstCommand = run.recommended_commands?.[0] || "";
              const workerKnowledge = run.worker_knowledge_retrieval;
              const workerKnowledgeStatus = workerKnowledge?.status || "not_applicable";
              const workerKnowledgeBlocked = workerKnowledgeStatus === "missing" || workerKnowledgeStatus === "unavailable";
              const workerRuntimeSummary = run.worker_runtime_summary;
              const workerRuntimeSummaryStatus = workerRuntimeSummary?.status || "not_applicable";
              const workerRuntimeSummaryBlocked = workerRuntimeSummaryStatus === "missing";
              return (
                <div key={run.run_id} className="rounded px-3 py-2" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <div className="text-[10px] font-semibold truncate" style={{ color: "var(--mis-text)" }}>{run.run_id}</div>
                      <div className="text-[9px] mt-0.5 truncate" style={{ color: "var(--mis-muted)" }}>
                        {copy.agentPlan}: {run.agent_plan?.verification_pass ? "pass" : run.agent_plan?.status || "—"} · {copy.planEvidence}: {run.plan_evidence_manifest?.verification_pass ? "pass" : run.plan_evidence_manifest?.status || "—"}
                      </div>
                    </div>
                    <StatusBadge status={run.status} />
                  </div>
                  <div className="flex flex-wrap gap-1.5 mt-2">
                    {["tool_calls", "evaluations", "artifacts", "audit_logs"].map((key) => (
                      <span key={key} className="text-[9px] px-1.5 py-0.5 rounded" style={{ color: "var(--mis-muted)", background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
                        {key}: {counts[key] ?? 0}
                      </span>
                    ))}
                    {(run.approvals?.pending || 0) > 0 && (
                      <span className="text-[9px] px-1.5 py-0.5 rounded" style={{ color: "var(--mis-warning)", background: "rgba(251,191,36,0.08)", border: "1px solid rgba(251,191,36,0.22)" }}>
                        {copy.pendingApprovals}: {run.approvals?.pending}
                      </span>
                    )}
                    {workerKnowledge?.applicable && (
                      <span className="text-[9px] px-1.5 py-0.5 rounded" style={{ color: workerKnowledgeBlocked ? "var(--mis-warning)" : "var(--mis-success)", background: workerKnowledgeBlocked ? "rgba(251,191,36,0.08)" : "rgba(45,212,191,0.08)", border: workerKnowledgeBlocked ? "1px solid rgba(251,191,36,0.22)" : "1px solid rgba(45,212,191,0.20)" }}>
                        {copy.workerKnowledge}: {workerKnowledgeStatus}
                      </span>
                    )}
                    {workerRuntimeSummary?.applicable && (
                      <span className="text-[9px] px-1.5 py-0.5 rounded" style={{ color: workerRuntimeSummaryBlocked ? "var(--mis-warning)" : "var(--mis-success)", background: workerRuntimeSummaryBlocked ? "rgba(251,191,36,0.08)" : "rgba(45,212,191,0.08)", border: workerRuntimeSummaryBlocked ? "1px solid rgba(251,191,36,0.22)" : "1px solid rgba(45,212,191,0.20)" }}>
                        {copy.workerRuntimeSummary}: {workerRuntimeSummaryStatus}
                      </span>
                    )}
                  </div>
                  {workerKnowledge?.applicable && (
                    <div className="mt-2 rounded px-2 py-1" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
                      <div className="flex items-center justify-between gap-2">
                        <div className="text-[9px] font-semibold truncate" style={{ color: "var(--mis-text)" }}>
                          {copy.workerKnowledgePaths}: {(workerKnowledge.paths || []).length}
                        </div>
                        <StatusBadge status={workerKnowledgeBlocked ? "blocked" : workerKnowledgeStatus === "ready" ? "pass" : "attention"} />
                      </div>
                      <div className="text-[9px] mt-0.5 truncate" style={{ color: "var(--mis-muted)" }}>
                        {copy.workerKnowledgePacket}: {(workerKnowledge.packet_hashes?.[0] || "—").slice(0, 10)} · {copy.workerKnowledgeQuery}: {(workerKnowledge.query_hashes?.[0] || "—").slice(0, 10)}
                      </div>
                    </div>
                  )}
                  {workerRuntimeSummary?.applicable && (
                    <div className="mt-2 rounded px-2 py-1" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
                      <div className="flex items-center justify-between gap-2">
                        <div className="text-[9px] font-semibold truncate" style={{ color: "var(--mis-text)" }}>
                          {copy.workerRuntimeSummaryEvents}: {workerRuntimeSummary.summary_events || 0} · {copy.workerRuntimeSummaryLinked}: {workerRuntimeSummary.linked_summary_events || 0}
                        </div>
                        <StatusBadge status={workerRuntimeSummaryBlocked ? "blocked" : workerRuntimeSummaryStatus === "ready" ? "pass" : "attention"} />
                      </div>
                      <div className="text-[9px] mt-0.5 truncate" style={{ color: "var(--mis-muted)" }}>
                        {copy.workerRuntimeSummaryEvent}: {(workerRuntimeSummary.event_ids?.[0] || "—").slice(0, 18)} · {copy.runtimeRawTraceOmitted}
                      </div>
                    </div>
                  )}
                  <div className="flex items-center justify-between gap-2 mt-2">
                    <div className="text-[9px] truncate" style={{ color: run.failed_check_ids.length ? "var(--mis-warning)" : "var(--mis-muted)" }}>
                      {run.failed_check_ids.length ? `${copy.gateEvidenceGaps}: ${run.failed_check_ids.join(", ")}` : copy.allGatesPassing}
                    </div>
                    <div className="flex items-center gap-1.5 shrink-0">
                      <Link to={`/admin/runs/${run.run_id}`} className="text-[9px] px-1.5 py-0.5 rounded" style={{ background: "rgba(45,212,191,0.10)", color: "var(--mis-success)", border: "1px solid rgba(45,212,191,0.18)" }}>
                        {copy.openRun}
                      </Link>
                      {firstCommand && (
                        <button
                          onClick={() => void copyIntakeCommand(firstCommand)}
                          className="inline-flex items-center gap-1 text-[9px] px-1.5 py-0.5 rounded"
                          style={{ color: "var(--mis-cyan)", background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}
                          title={firstCommand}
                        >
                          <Copy size={9} />
                          {copiedIntakeCommand === firstCommand ? copy.copiedCommand : copy.copyCommand}
                        </button>
                      )}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        <div className="rounded-lg p-3 mt-4" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
          <div className="flex flex-col xl:flex-row xl:items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <ShieldCheck size={13} style={{ color: hygieneActionsAvailable > 0 ? "var(--mis-warning)" : "var(--mis-success)" }} />
                <div className="text-[11px] font-semibold" style={{ color: "var(--mis-text)" }}>{copy.fleetHygieneTitle}</div>
                <StatusBadge status={activeHygiene?.status || "unknown"} />
              </div>
              <p className="text-[10px] mt-1 max-w-3xl" style={{ color: "var(--mis-dim)" }}>{copy.fleetHygieneSummary}</p>
              <div className="flex flex-wrap gap-1.5 mt-2">
                <span className="text-[9px] px-1.5 py-0.5 rounded" style={{ color: "var(--mis-success)", background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                  {copy.hygieneSafety}
                </span>
                <span className="text-[9px] px-1.5 py-0.5 rounded" style={{ color: "var(--mis-muted)", background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                  {copy.tokenOmittedProof}
                </span>
                {activeHygiene?.safety?.read_only && (
                  <span className="text-[9px] px-1.5 py-0.5 rounded" style={{ color: "var(--mis-cyan)", background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                    {copy.readOnlyProof}
                  </span>
                )}
              </div>
            </div>
            <div className="flex flex-wrap gap-2 shrink-0">
              <button
                onClick={() => runFleetHygiene(false)}
                disabled={hygieneBusy}
                className="inline-flex items-center gap-1.5 text-[10px] px-2 py-1 rounded"
                style={{ color: "var(--mis-text)", background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}
              >
                {hygieneBusy ? <RefreshCw size={11} /> : <Activity size={11} />}
                {hygieneBusy ? copy.hygieneRunning : copy.hygienePlan}
              </button>
              <button
                onClick={() => runFleetHygiene(true)}
                disabled={hygieneBusy || hygieneActionsAvailable <= 0}
                className="inline-flex items-center gap-1.5 text-[10px] px-2 py-1 rounded disabled:opacity-50"
                style={{ color: "#071014", background: hygieneActionsAvailable > 0 ? "var(--mis-warning)" : "var(--mis-muted)", border: "1px solid var(--mis-border)" }}
              >
                {hygieneBusy ? <RefreshCw size={11} /> : <Trash2 size={11} />}
                {hygieneBusy ? copy.hygieneRunning : copy.hygieneApply}
              </button>
            </div>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-3 2xl:grid-cols-6 gap-2 mt-3">
            {[
              { label: copy.hygieneActions, value: hygieneActionsAvailable, status: hygieneActionsAvailable > 0 ? "attention" : "pass" },
              { label: copy.stuckTasks, value: hygieneSummary?.stuck_tasks ?? 0, status: (hygieneSummary?.stuck_tasks || 0) > 0 ? "blocked" : "pass" },
              { label: copy.staleNeverSeen, value: hygieneSummary?.stale_never_seen_enrollments ?? 0, status: (hygieneSummary?.stale_never_seen_enrollments || 0) > 0 ? "attention" : "pass" },
              { label: copy.staleHeartbeat, value: hygieneSummary?.stale_heartbeat_enrollments ?? 0, status: (hygieneSummary?.stale_heartbeat_enrollments || 0) > 0 ? "attention" : "pass" },
              { label: copy.releasedTasks, value: hygieneSummary?.released_tasks ?? activeHygiene?.released_tasks?.length ?? 0, status: (hygieneSummary?.released_tasks || 0) > 0 ? "completed" : "planned" },
              { label: copy.revokedEnrollments, value: hygieneSummary?.revoked_enrollments ?? activeHygiene?.revoked_enrollments?.length ?? 0, status: (hygieneSummary?.revoked_enrollments || 0) > 0 ? "completed" : "planned" },
            ].map((item) => (
              <div key={item.label} className="rounded px-2 py-1" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                <div className="text-[9px]" style={{ color: "var(--mis-muted)" }}>{item.label}</div>
                <div className="flex items-center justify-between gap-2 mt-0.5">
                  <div className="text-[10px] font-semibold truncate" style={{ color: "var(--mis-text)" }}>{item.value}</div>
                  <StatusBadge status={item.status} />
                </div>
              </div>
            ))}
          </div>
          {hygieneError && (
            <div className="text-[10px] mt-2" style={{ color: "#F87171" }}>{hygieneError}</div>
          )}
          <div className="mt-2 text-[10px] truncate" style={{ color: "var(--mis-muted)" }}>
            {(activeHygiene?.recommended_actions || [copy.hygieneNoActions])[0] || copy.hygieneNoActions}
          </div>
        </div>

        <div className="rounded-lg p-3 mt-4" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
          <div className="flex flex-col lg:flex-row lg:items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <CheckCircle2 size={13} style={{ color: demoReadiness?.demo_ready ? "var(--mis-success)" : "var(--mis-warning)" }} />
                <div className="text-[11px] font-semibold" style={{ color: "var(--mis-text)" }}>{copy.demoReadinessTitle}</div>
                <StatusBadge status={demoReadiness?.status || "unknown"} label={demoReadiness?.demo_ready ? copy.demoReady : copy.statusAttention} />
              </div>
              <p className="text-[10px] mt-1 max-w-3xl" style={{ color: "var(--mis-dim)" }}>{copy.demoReadinessSummary}</p>
              {demoReadiness?.contract && (
                <p className="text-[10px] mt-1 max-w-3xl" style={{ color: "var(--mis-muted)" }}>{copy.contract}: {demoReadiness.contract}</p>
              )}
            </div>
            <StatusBadge status={demoReadiness?.production_ready ? "ready" : "attention"} label={demoReadiness?.production_ready ? copy.productionReady : copy.localDevOnly} />
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mt-3">
            {[
              { label: copy.shotsReady, value: `${demoReadiness?.summary.ready_shots ?? 0}/${demoReadiness?.summary.shot_count ?? 0}`, status: demoReadiness?.demo_ready ? "ready" : "attention" },
              { label: copy.evidenceChains, value: demoReadiness?.summary.closed_loop_runs ?? 0, status: (demoReadiness?.summary.closed_loop_runs || 0) > 0 ? "pass" : "attention" },
              { label: copy.fleetLanes, value: demoReadiness?.summary.fleet_lanes ?? 0, status: (demoReadiness?.summary.fleet_lanes || 0) > 0 ? "pass" : "attention" },
              { label: copy.readyForReview, value: demoReadiness?.summary.ready_inbox_items ?? 0, status: (demoReadiness?.summary.ready_inbox_items || 0) > 0 ? "ready" : "planned" },
            ].map((item) => (
              <div key={item.label} className="rounded px-2 py-1" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                <div className="text-[9px]" style={{ color: "var(--mis-muted)" }}>{item.label}</div>
                <div className="flex items-center justify-between gap-2 mt-0.5">
                  <div className="text-[10px] font-semibold truncate" style={{ color: "var(--mis-text)" }}>{item.value}</div>
                  <StatusBadge status={item.status} />
                </div>
              </div>
            ))}
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2 mt-3">
            {(demoReadiness?.shots || []).slice(0, 6).map((shot) => (
              <div key={shot.id} className="rounded px-3 py-2" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                <div className="flex items-center justify-between gap-2">
                  <div className="text-[10px] font-semibold truncate" style={{ color: "var(--mis-text)" }}>{shot.label}</div>
                  <StatusBadge status={shot.ok ? "pass" : shot.status} />
                </div>
                <div className="text-[10px] mt-1 truncate" style={{ color: "var(--mis-muted)" }}>{shot.route || shot.command || "—"}</div>
              </div>
            ))}
          </div>
          {demoReadiness?.product_evidence_packet && (
            <div className="mt-3 rounded px-3 py-2" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
              <div className="flex flex-col lg:flex-row lg:items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <Terminal size={12} style={{ color: "var(--mis-cyan)" }} />
                    <div className="text-[10px] font-semibold" style={{ color: "var(--mis-text)" }}>{copy.productEvidencePacket}</div>
                    <StatusBadge status={demoReadiness.product_evidence_packet.status || "unknown"} />
                    <StatusBadge status={demoReadiness.product_evidence_packet.safety.read_only && !demoReadiness.product_evidence_packet.safety.ledger_mutated ? "pass" : "attention"} label={copy.safetyProof} />
                  </div>
                  <p className="text-[10px] mt-1 max-w-4xl" style={{ color: "var(--mis-dim)" }}>{copy.productEvidenceSummary}</p>
                  {demoReadiness.product_evidence_packet.contract && (
                    <p className="text-[9px] mt-1 max-w-4xl" style={{ color: "var(--mis-muted)" }}>{copy.contract}: {demoReadiness.product_evidence_packet.contract}</p>
                  )}
                </div>
                <div className="grid grid-cols-3 gap-1 min-w-[220px]">
                  {[
                    { label: copy.productEvidencePhases, value: demoReadiness.product_evidence_packet.summary.phase_count, status: "pass" },
                    { label: copy.manualLivePhases, value: demoReadiness.product_evidence_packet.summary.manual_live_phase_count, status: demoReadiness.product_evidence_packet.safety.requires_confirm_live ? "attention" : "pass" },
                    { label: copy.isolatedDbPhases, value: demoReadiness.product_evidence_packet.summary.isolated_db_phase_count, status: demoReadiness.product_evidence_packet.safety.requires_isolated_db_for_live ? "attention" : "pass" },
                  ].map((item) => (
                    <div key={item.label} className="rounded px-2 py-1" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
                      <div className="text-[8px] truncate" style={{ color: "var(--mis-muted)" }}>{item.label}</div>
                      <div className="flex items-center justify-between gap-1 mt-0.5">
                        <span className="text-[10px] font-semibold" style={{ color: "var(--mis-text)" }}>{item.value}</span>
                        <StatusBadge status={item.status} />
                      </div>
                    </div>
                  ))}
                </div>
              </div>
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-2 mt-3">
                {demoReadiness.product_evidence_packet.phases.slice(0, 6).map((phase) => (
                  <div key={phase.id} className="rounded px-2 py-2" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <div className="flex items-center gap-1.5">
                          <div className="text-[9px] font-semibold truncate" style={{ color: "var(--mis-text)" }}>{phase.label}</div>
                          {phase.requires_confirm_live && <StatusBadge status="attention" label="confirm-live" />}
                          {phase.requires_isolated_db && <StatusBadge status="planned" label="isolated-db" />}
                        </div>
                        <div className="text-[9px] mt-1 line-clamp-2" style={{ color: "var(--mis-muted)" }}>{phase.summary}</div>
                      </div>
                      {phase.command && (
                        <button
                          type="button"
                          className="shrink-0 inline-flex items-center gap-1 rounded px-2 py-1 text-[9px]"
                          style={{ border: "1px solid var(--mis-border)", color: "var(--mis-cyan)", background: "var(--mis-bg)" }}
                          onClick={() => void copyIntakeCommand(phase.command)}
                        >
                          <Copy size={10} />
                          <span className="truncate max-w-[72px]">{copiedIntakeCommand === phase.command ? copy.copiedCommand : copy.copyCommand}</span>
                        </button>
                      )}
                    </div>
                    <div className="text-[8px] mt-1 truncate" style={{ color: copiedIntakeCommand === phase.command ? "var(--mis-success)" : "var(--mis-cyan)" }}>
                      {phase.command}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        <div className="rounded-lg p-3 mt-4" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
          <div className="flex flex-col lg:flex-row lg:items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <Activity size={13} style={{ color: operatorLoopAudit?.status === "blocked" ? "var(--mis-warning)" : "var(--mis-cyan)" }} />
                <div className="text-[11px] font-semibold" style={{ color: "var(--mis-text)" }}>{copy.loopAuditTitle}</div>
                <StatusBadge status={operatorLoopAudit?.status || "unknown"} />
                {panelStatusBadge("operator_loop_audit")}
                {panelRefreshButton("operator_loop_audit")}
                {panelDiagnosticsButton("operator_loop_audit")}
                {panelReceiptButton("operator_loop_audit")}
              </div>
              <p className="text-[10px] mt-1 max-w-4xl" style={{ color: "var(--mis-dim)" }}>
                {copy.loopAuditSummary}
              </p>
              {panelEvidenceLine("operator_loop_audit")}
              <div className="text-[10px] mt-1 truncate" style={{ color: "var(--mis-muted)" }}>
                {copy.methodBlock}: {operatorLoopAudit?.method || "READ -> PLAN -> RETRIEVE -> COMPARE -> EXECUTE -> VERIFY -> RECORD"}
              </div>
            </div>
            <StatusBadge status={operatorLoopAudit?.safety?.read_only && !operatorLoopAudit?.safety?.ledger_mutated ? "pass" : "attention"} label={operatorLoopAudit?.safety?.read_only ? copy.safetyProof : copy.statusAttention} />
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mt-3">
            {[
              { label: copy.loopReadback, value: `${loopAuditSummary?.loop_verified_plan_evidence_manifests ?? 0}/${loopAuditSummary?.loop_runs ?? 0}`, status: (loopAuditSummary?.loop_blocked_plan_evidence_manifests || 0) > 0 ? "blocked" : (loopAuditSummary?.loop_verified_plan_evidence_manifests || 0) > 0 ? "pass" : "attention" },
              { label: copy.agentPlan, value: loopAuditSummary?.verified_agent_plans ?? 0, status: (loopAuditSummary?.verified_agent_plans || 0) > 0 ? "pass" : "attention" },
              { label: copy.planEvidence, value: loopAuditSummary?.verified_plan_evidence_manifests ?? 0, status: (loopAuditSummary?.evidence_gap_runs || 0) > 0 ? "blocked" : (loopAuditSummary?.verified_plan_evidence_manifests || 0) > 0 ? "pass" : "attention" },
              { label: copy.memoryApprovalCounts, value: `${loopAuditSummary?.loop_memory_candidates ?? loopAuditSummary?.memory_candidates ?? 0}/${loopAuditSummary?.loop_pending_approvals ?? loopAuditSummary?.pending_approvals ?? 0}`, status: ((loopAuditSummary?.loop_memory_candidates ?? loopAuditSummary?.memory_candidates ?? 0) + (loopAuditSummary?.loop_pending_approvals ?? loopAuditSummary?.pending_approvals ?? 0)) > 0 ? "attention" : "pass" },
            ].map((item) => (
              <div key={item.label} className="rounded px-2 py-1" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                <div className="text-[9px]" style={{ color: "var(--mis-muted)" }}>{item.label}</div>
                <div className="flex items-center justify-between gap-2 mt-0.5">
                  <div className="text-[10px] font-semibold truncate" style={{ color: "var(--mis-text)" }}>{item.value}</div>
                  <StatusBadge status={item.status} />
                </div>
              </div>
            ))}
          </div>
          {loopAuditSteps.length > 0 && (
            <div className="mt-3 rounded px-3 py-2" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
              <div className="flex items-center justify-between gap-2">
                <div className="min-w-0">
                  <div className="text-[10px] font-semibold" style={{ color: "var(--mis-text)" }}>{copy.loopChainTitle}</div>
                  <div className="text-[8px] mt-0.5 truncate" style={{ color: firstLoopIssueStep ? "var(--mis-warning)" : "var(--mis-muted)" }}>
                    {firstLoopIssueStep ? `${copy.firstGateIssue}: ${firstLoopIssueStep.label} · ${firstLoopIssueStep.source || "—"}` : copy.allGatesPassing}
                  </div>
                </div>
                <div className="flex items-center gap-1.5 shrink-0">
                  {firstLoopIssueStep?.command ? (
                    <button
                      onClick={() => void copyIntakeCommand(firstLoopIssueStep.command)}
                      className="inline-flex items-center gap-1 text-[9px] px-1.5 py-0.5 rounded"
                      style={{ color: "var(--mis-cyan)", background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}
                      title={firstLoopIssueStep.command}
                    >
                      <Copy size={9} />
                      {copiedIntakeCommand === firstLoopIssueStep.command ? copy.copiedCommand : copy.copyFirstGateIssue}
                    </button>
                  ) : (
                    <StatusBadge status="pass" label={copy.allGatesPassing} />
                  )}
                  <StatusBadge status={operatorLoopAudit?.status || "unknown"} />
                </div>
              </div>
              <div className="mt-2 overflow-x-auto">
                <div className="flex items-stretch gap-1 min-w-max">
                  {loopAuditSteps.map((step, index) => {
                    const stepReceipt = latestReceiptForAction(step.command);
                    const stepReceiptHash = receiptShortHash(stepReceipt);
                    return (
                      <div key={step.id} className="flex items-center gap-1">
                        <button
                          onClick={() => void copyIntakeCommand(step.command)}
                          className="min-w-[108px] text-left rounded px-2 py-1.5"
                          style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}
                          title={step.command}
                        >
                          <div className="flex items-center justify-between gap-1">
                            <span className="text-[9px] font-semibold truncate" style={{ color: "var(--mis-text)" }}>{step.label}</span>
                            <StatusBadge status={step.status} />
                          </div>
                          <div className="text-[8px] mt-1 truncate" style={{ color: "var(--mis-muted)" }}>
                            {step.source || copy.actionSource}
                          </div>
                          <div className="text-[8px] mt-0.5 truncate" style={{ color: copiedIntakeCommand === step.command ? "var(--mis-success)" : "var(--mis-cyan)" }}>
                            {copiedIntakeCommand === step.command ? copy.copiedCommand : copy.copyCommand}
                          </div>
                          {stepReceipt && (
                            <div className="text-[8px] mt-0.5 truncate" style={{ color: "var(--mis-success)" }}>
                              {copy.receiptProof}: {stepReceipt.status} · {stepReceiptHash}
                            </div>
                          )}
                        </button>
                        {index < loopAuditSteps.length - 1 && (
                          <div className="text-[10px]" style={{ color: "var(--mis-dim)" }}>-&gt;</div>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>
            </div>
          )}
          {operatorLoopLaunchPacket && (
            <div className="mt-3 rounded px-3 py-2" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
              <div className="flex flex-col xl:flex-row xl:items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <Terminal size={12} style={{ color: "var(--mis-cyan)" }} />
                    <div className="text-[10px] font-semibold" style={{ color: "var(--mis-text)" }}>{copy.loopLaunchContractTitle}</div>
                    <StatusBadge status={operatorLoopLaunchPacket.status || "unknown"} />
                    {panelStatusBadge("operator_loop_launch_packet")}
                    {panelRefreshButton("operator_loop_launch_packet")}
                    {panelDiagnosticsButton("operator_loop_launch_packet")}
                    {panelReceiptButton("operator_loop_launch_packet")}
                    <StatusBadge status={operatorLoopLaunchPacket.safety.read_only && !operatorLoopLaunchPacket.safety.ledger_mutated ? "pass" : "attention"} label={operatorLoopLaunchPacket.safety.read_only ? copy.readOnlyProof : copy.statusAttention} />
                  </div>
                  <div className="text-[9px] mt-0.5 max-w-4xl" style={{ color: "var(--mis-muted)" }}>{copy.loopLaunchContractSummary}</div>
                  {panelEvidenceLine("operator_loop_launch_packet")}
                  <div className="text-[9px] mt-0.5 truncate" style={{ color: "var(--mis-dim)" }}>
                    {copy.methodBlock}: {operatorLoopLaunchPacket.method}
                  </div>
                </div>
                <button
                  onClick={() => void copyIntakeCommand(loopLaunchPacketJson)}
                  className="inline-flex items-center gap-1 text-[9px] px-2 py-1 rounded shrink-0"
                  style={{ color: "var(--mis-cyan)", background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}
                  title={operatorLoopLaunchPacket.contract || copy.loopLaunchContractTitle}
                >
                  <Copy size={10} />
                  {copiedIntakeCommand === loopLaunchPacketJson ? copy.copiedCommand : copy.copyLaunchPacketJson}
                </button>
              </div>
              {loopLaunchControlSummary && (
                <div className="mt-3 rounded px-2 py-1.5" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
                  <div className="flex flex-col xl:flex-row xl:items-start justify-between gap-2">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <Activity size={12} style={{ color: "var(--mis-cyan)" }} />
                        <div className="text-[10px] font-semibold" style={{ color: "var(--mis-text)" }}>{copy.loopControlTitle}</div>
                        <StatusBadge status={loopLaunchControlSummary.status || "unknown"} />
                        <StatusBadge status={loopLaunchControlSummary.server_executes_shell ? "blocked" : "pass"} label={loopLaunchControlSummary.server_executes_shell ? "server shell" : "copy only"} />
                      </div>
                      <div className="text-[9px] mt-0.5 max-w-4xl" style={{ color: "var(--mis-muted)" }}>{copy.loopControlSummary}</div>
                      <div className="grid grid-cols-2 xl:grid-cols-4 gap-1.5 mt-2">
                        {[
                          { label: copy.recommendedStep, value: String(loopLaunchRecommendedStep.label || loopLaunchRecommendedStep.step_id || "—"), status: String(loopLaunchRecommendedStep.status || loopLaunchControlSummary.status || "unknown") },
                          { label: copy.controlMode, value: loopLaunchControlSummary.mode || "unknown", status: loopLaunchControlSummary.requires_human ? "attention" : "pass" },
                          { label: copy.humanRequired, value: loopLaunchControlSummary.requires_human ? copy.confirmRequired : copy.readOnlyProof, status: loopLaunchControlSummary.requires_human ? "attention" : "pass" },
                          { label: copy.receiptProof, value: loopLaunchControlSummary.requires_receipt ? copy.receiptNeeded : copy.verifiedReceipts, status: loopLaunchControlSummary.requires_receipt ? "attention" : "pass" },
                        ].map((item) => (
                          <div key={item.label} className="rounded px-2 py-1" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                            <div className="text-[8px]" style={{ color: "var(--mis-muted)" }}>{item.label}</div>
                            <div className="flex items-center justify-between gap-1 mt-0.5">
                              <div className="text-[9px] font-semibold truncate" style={{ color: "var(--mis-text)" }}>{item.value}</div>
                              <StatusBadge status={item.status} />
                            </div>
                          </div>
                        ))}
                      </div>
                      {(loopLaunchRecommendedStep.reason || loopLaunchRecommendedStep.blocked_reason || loopLaunchRecommendedStep.ready_reason) && (
                        <div className="text-[8px] mt-1 line-clamp-2" style={{ color: loopLaunchControlSummary.status === "blocked" ? "var(--mis-warning)" : "var(--mis-dim)" }}>
                          {loopLaunchControlSummary.status === "blocked" ? copy.blockedReason : copy.readyReason}: {String(loopLaunchRecommendedStep.reason || loopLaunchRecommendedStep.blocked_reason || loopLaunchRecommendedStep.ready_reason)}
                        </div>
                      )}
                    </div>
                    <div className="flex flex-wrap gap-1 shrink-0">
                      {loopLaunchRecommendedCommand && (
                        <button
                          onClick={() => void copyIntakeCommand(loopLaunchRecommendedCommand)}
                          className="inline-flex items-center gap-1 text-[8px] px-1.5 py-0.5 rounded max-w-full"
                          style={{ color: loopLaunchControlSummary.requires_human ? "var(--mis-warning)" : "var(--mis-cyan)", background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}
                          title={loopLaunchRecommendedCommand}
                        >
                          <Copy size={8} />
                          <span className="truncate max-w-[96px]">{copiedIntakeCommand === loopLaunchRecommendedCommand ? copy.copiedCommand : copy.nextSafeCommand}</span>
                        </button>
                      )}
                      {loopLaunchRecommendedVerifyCommand && (
                        <button
                          onClick={() => void copyIntakeCommand(loopLaunchRecommendedVerifyCommand)}
                          className="inline-flex items-center gap-1 text-[8px] px-1.5 py-0.5 rounded max-w-full"
                          style={{ color: "var(--mis-success)", background: "rgba(45,212,191,0.08)", border: "1px solid rgba(45,212,191,0.18)" }}
                          title={loopLaunchRecommendedVerifyCommand}
                        >
                          <Copy size={8} />
                          <span className="truncate max-w-[86px]">{copiedIntakeCommand === loopLaunchRecommendedVerifyCommand ? copy.copiedCommand : copy.verifyAfterAction}</span>
                        </button>
                      )}
                      {loopLaunchRecommendedReceiptCommand && (
                        <button
                          onClick={() => void copyIntakeCommand(loopLaunchRecommendedReceiptCommand)}
                          className="inline-flex items-center gap-1 text-[8px] px-1.5 py-0.5 rounded max-w-full"
                          style={{ color: "var(--mis-warning)", background: "rgba(251,191,36,0.08)", border: "1px solid rgba(251,191,36,0.18)" }}
                          title={loopLaunchRecommendedReceiptCommand}
                        >
                          <Copy size={8} />
                          <span className="truncate max-w-[86px]">{copiedIntakeCommand === loopLaunchRecommendedReceiptCommand ? copy.copiedCommand : copy.copyVerifyReceiptCommand}</span>
                        </button>
                      )}
                    </div>
                  </div>
                </div>
              )}
              <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mt-3">
                {[
                  { label: copy.evaluationContract, value: loopLaunchEvaluationContract?.status || "unknown", status: loopLaunchEvaluationContract?.status || "unknown" },
                  { label: copy.auditContract, value: loopLaunchAuditContract?.operation || "unknown", status: loopLaunchAuditContract?.record_required ? "pass" : "attention" },
                  { label: copy.requiredLedgers, value: loopLaunchRequiredLedgers.length, status: loopLaunchRequiredLedgers.length >= 6 ? "pass" : "attention" },
                  { label: copy.receiptEvaluations, value: `${Number(loopLaunchReceiptEvaluation.evaluated ?? 0)}/${Number(loopLaunchReceiptEvaluation.required ?? 0)}`, status: String(loopLaunchReceiptEvaluation.status || "unknown") },
                  { label: copy.tamperChain, value: loopLaunchAuditContract?.tamper_chain_required ? "required" : "missing", status: loopLaunchAuditContract?.tamper_chain_required ? "pass" : "blocked" },
                  { label: copy.agentPlan, value: operatorLoopLaunchPacket.task_id || "—", status: operatorLoopLaunchPacket.task_id ? "attention" : "unknown" },
                  { label: copy.executionChain, value: `${loopLaunchExecutionChain.length}/${loopLaunchReceiptSteps}`, status: loopLaunchExecutionChain.length > 0 ? "attention" : "unknown" },
                  { label: copy.advanceLoopPolicyLabel, value: String(loopLaunchBoundedRunner.policy_id || "advance_loop_local_bounded_v1"), status: loopLaunchBoundedRunner.server_executes_shell ? "blocked" : "pass" },
                ].map((item) => (
                  <div key={item.label} className="rounded px-2 py-1" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
                    <div className="text-[9px]" style={{ color: "var(--mis-muted)" }}>{item.label}</div>
                    <div className="flex items-center justify-between gap-2 mt-0.5">
                      <div className="text-[10px] font-semibold truncate" style={{ color: "var(--mis-text)" }}>{item.value}</div>
                      <StatusBadge status={item.status} />
                    </div>
                  </div>
                ))}
              </div>
              {loopLaunchExecutionChain.length > 0 && (
                <div className="mt-2 rounded px-2 py-1.5" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
                  <div className="flex items-center justify-between gap-2">
                    <div className="min-w-0">
                      <div className="text-[10px] font-semibold" style={{ color: "var(--mis-text)" }}>{copy.executionChain}</div>
                      <div className="text-[9px] mt-0.5 truncate" style={{ color: "var(--mis-muted)" }}>
                        {copy.mutatingSteps}: {loopLaunchMutatingSteps} · {copy.receiptSteps}: {loopLaunchReceiptSteps}
                      </div>
                    </div>
                    <StatusBadge status={loopLaunchMutatingSteps > 0 ? "attention" : "pass"} label={loopLaunchMutatingSteps > 0 ? copy.confirmRequired : copy.readOnlyProof} />
                  </div>
                  <div className="mt-2 overflow-x-auto">
                    <div className="flex items-stretch gap-1 min-w-max">
                      {loopLaunchExecutionChain.slice(0, 7).map((step, index) => {
                        const stepReceiptState = step.receipt_state || {};
                        const fallbackReceipt = latestReceiptForAction(step.command, step.action_signature);
                        const stepReceiptHash = String(
                          stepReceiptState.receipt_hash ||
                          stepReceiptState.verify_hash ||
                          stepReceiptState.action_hash ||
                          fallbackReceipt?.tamper_chain_hash ||
                          fallbackReceipt?.verify_hash ||
                          fallbackReceipt?.action_hash ||
                          fallbackReceipt?.audit_id ||
                          ""
                        ).slice(0, 12);
                        const stepReceiptStatus = String(stepReceiptState.status || fallbackReceipt?.status || (step.receipt_required ? copy.noReceiptProof : "not_required"));
                        const stepReceiptVerified = stepReceiptState.verified === true || fallbackReceipt?.status === "verified";
                        const stepStatus = step.step_status || (stepReceiptVerified ? "verified" : step.mutating ? "attention" : "pass");
                        const stepReason = step.blocked_reason || step.ready_reason || "";
                        return (
                        <div key={step.step_id || `${step.command}-${index}`} className="flex items-center gap-1">
                          <div className="w-[168px] rounded px-2 py-1.5" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                            <div className="flex items-center justify-between gap-1">
                              <span className="text-[9px] font-semibold truncate" style={{ color: "var(--mis-text)" }}>{step.label || step.step_id}</span>
                              <StatusBadge status={stepStatus} />
                            </div>
                            <div className="text-[8px] mt-0.5 truncate" style={{ color: "var(--mis-muted)" }}>
                              {step.phase} · {step.source || copy.actionSource}
                            </div>
                            {stepReason && (
                              <div className="text-[8px] mt-0.5 line-clamp-2" style={{ color: step.blocked_reason ? "var(--mis-warning)" : "var(--mis-dim)" }}>
                                {step.blocked_reason ? copy.blockedReason : copy.readyReason}: {stepReason}
                              </div>
                            )}
                            <div className="flex flex-wrap gap-1 mt-1">
                              {step.command && (
                                <button
                                  onClick={() => void copyIntakeCommand(step.command)}
                                  className="inline-flex items-center gap-1 text-[8px] px-1 py-0.5 rounded max-w-full"
                                  style={{ color: step.mutating ? "var(--mis-warning)" : "var(--mis-cyan)", background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}
                                  title={step.command}
                                >
                                  <Copy size={8} />
                                  <span className="truncate max-w-[90px]">{copiedIntakeCommand === step.command ? copy.copiedCommand : copy.copyActionCommand}</span>
                                </button>
                              )}
                              {step.verify_command && (
                                <button
                                  onClick={() => void copyIntakeCommand(String(step.verify_command))}
                                  className="inline-flex items-center gap-1 text-[8px] px-1 py-0.5 rounded max-w-full"
                                  style={{ color: "var(--mis-success)", background: "rgba(45,212,191,0.08)", border: "1px solid rgba(45,212,191,0.18)" }}
                                  title={String(step.verify_command)}
                                >
                                  <Copy size={8} />
                                  <span className="truncate max-w-[74px]">{copiedIntakeCommand === step.verify_command ? copy.copiedCommand : copy.verifyAfterAction}</span>
                                </button>
                              )}
                            </div>
                            {(step.confirm_required || step.receipt_required) && (
                              <div className="text-[8px] mt-1 truncate" style={{ color: "var(--mis-warning)" }}>
                                {step.confirm_required ? copy.confirmRequired : copy.receiptProof} · {step.policy_id || step.selected_gate || "—"}
                              </div>
                            )}
                            {step.receipt_state && (
                              <div className="text-[8px] mt-0.5 truncate" style={{ color: stepReceiptVerified ? "var(--mis-success)" : step.receipt_required ? "var(--mis-warning)" : "var(--mis-muted)" }}>
                                {copy.receiptProof}: {stepReceiptStatus}{stepReceiptHash ? ` · ${stepReceiptHash}` : ""}
                              </div>
                            )}
                          </div>
                          {index < Math.min(loopLaunchExecutionChain.length, 7) - 1 && (
                            <div className="text-[10px]" style={{ color: "var(--mis-dim)" }}>-&gt;</div>
                          )}
                        </div>
                      );})}
                    </div>
                  </div>
                </div>
              )}
              <div className="grid grid-cols-1 xl:grid-cols-3 gap-2 mt-2">
                <div className="rounded px-2 py-1.5" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
                  <div className="text-[10px] font-semibold" style={{ color: "var(--mis-text)" }}>{copy.exitCriteria}</div>
                  <div className="space-y-1 mt-1">
                    {loopLaunchExitCriteria.slice(0, 4).map((criterion, index) => (
                      <div key={`${criterion}-${index}`} className="text-[9px] leading-snug" style={{ color: "var(--mis-muted)" }}>
                        {index + 1}. {criterion}
                      </div>
                    ))}
                  </div>
                </div>
                <div className="rounded px-2 py-1.5" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
                  <div className="text-[10px] font-semibold" style={{ color: "var(--mis-text)" }}>{copy.requiredLedgers}</div>
                  <div className="flex flex-wrap gap-1 mt-1">
                    {loopLaunchRequiredLedgers.slice(0, 8).map((ledger) => (
                      <span key={ledger} className="text-[8px] px-1.5 py-0.5 rounded" style={{ color: "var(--mis-cyan)", background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                        {ledger}
                      </span>
                    ))}
                  </div>
                  <div className="text-[9px] mt-2 line-clamp-2" style={{ color: "var(--mis-dim)" }}>
                    {copy.rawContentPolicy}: {loopLaunchAuditContract?.raw_content_policy || "—"}
                  </div>
                </div>
                <div className="rounded px-2 py-1.5" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
                  <div className="text-[10px] font-semibold" style={{ color: "var(--mis-text)" }}>{copy.handoffCommands}</div>
                  <div className="flex flex-wrap gap-1 mt-1">
                    {[...loopLaunchRequiredCommands.slice(0, 2), ...loopLaunchRecordCommands.slice(0, 2)].filter(Boolean).map((command) => (
                      <button
                        key={command}
                        onClick={() => void copyIntakeCommand(command)}
                        className="inline-flex items-center gap-1 text-[9px] px-1.5 py-0.5 rounded max-w-full"
                        style={{ color: "var(--mis-cyan)", background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}
                        title={command}
                      >
                        <Copy size={9} />
                        <span className="truncate max-w-[150px]">{copiedIntakeCommand === command ? copy.copiedCommand : command}</span>
                      </button>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          )}
          {loopActionPackageItems.length > 0 && (
            <div className="mt-3 rounded px-3 py-2" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
              <div className="flex flex-col md:flex-row md:items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="text-[10px] font-semibold" style={{ color: "var(--mis-text)" }}>{copy.loopWorkOrderTitle}</div>
                  <div className="text-[9px] mt-0.5" style={{ color: "var(--mis-muted)" }}>{copy.loopWorkOrderSummary}</div>
                </div>
                <div className="flex items-center gap-1.5 shrink-0 flex-wrap">
                  <StatusBadge status={loopActionPackage?.status || "unknown"} />
                  <StatusBadge status={loopActionPackage?.safety?.read_only && !loopActionPackage?.safety?.ledger_mutated ? "pass" : "attention"} label={loopActionPackage?.safety?.read_only ? copy.readOnlyProof : copy.statusAttention} />
                </div>
              </div>
              <div className="grid grid-cols-1 xl:grid-cols-2 gap-2 mt-2">
                {loopActionPackageItems.slice(0, 4).map((item) => (
                  <div key={item.package_id || `${item.gate_id}:${item.action_command}`} className="rounded px-2 py-1.5" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
                    <div className="flex items-center justify-between gap-2">
                      <div className="min-w-0">
                        <div className="text-[10px] font-semibold truncate" style={{ color: "var(--mis-text)" }}>{item.gate_label || item.gate_id}</div>
                        <div className="text-[9px] truncate mt-0.5" style={{ color: "var(--mis-muted)" }}>{item.source || copy.actionSource}</div>
                      </div>
                      <StatusBadge status={item.gate_status} />
                    </div>
                    {item.message && (
                      <div className="text-[9px] mt-1 line-clamp-2" style={{ color: "var(--mis-dim)" }}>{item.message}</div>
                    )}
                    <div className="flex flex-wrap gap-1.5 mt-2">
                      {item.action_command && (
                        <button
                          onClick={() => void copyIntakeCommand(item.action_command)}
                          className="inline-flex items-center gap-1 text-[9px] px-1.5 py-0.5 rounded"
                          style={{ color: "var(--mis-cyan)", background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}
                          title={item.action_command}
                        >
                          <Copy size={9} />
                          {copiedIntakeCommand === item.action_command ? copy.copiedCommand : copy.copyActionCommand}
                        </button>
                      )}
                      {item.verify_command && (
                        <button
                          onClick={() => void copyIntakeCommand(item.verify_command)}
                          className="inline-flex items-center gap-1 text-[9px] px-1.5 py-0.5 rounded"
                          style={{ color: "var(--mis-cyan)", background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}
                          title={item.verify_command}
                        >
                          <Copy size={9} />
                          {copiedIntakeCommand === item.verify_command ? copy.copiedCommand : copy.verifyAfterAction}
                        </button>
                      )}
                      {item.receipt_record_command && (
                        <button
                          onClick={() => void copyIntakeCommand(item.receipt_record_command)}
                          className="inline-flex items-center gap-1 text-[9px] px-1.5 py-0.5 rounded"
                          style={{ color: "var(--mis-warning)", background: "rgba(251,191,36,0.08)", border: "1px solid rgba(251,191,36,0.18)" }}
                          title={item.receipt_record_command}
                        >
                          <Copy size={9} />
                          {copiedIntakeCommand === item.receipt_record_command ? copy.copiedCommand : copy.copyReceiptCommand}
                        </button>
                      )}
                      {item.receipt_verify_record_command && (
                        <button
                          onClick={() => void copyIntakeCommand(item.receipt_verify_record_command)}
                          className="inline-flex items-center gap-1 text-[9px] px-1.5 py-0.5 rounded"
                          style={{ color: "var(--mis-success)", background: "rgba(45,212,191,0.08)", border: "1px solid rgba(45,212,191,0.18)" }}
                          title={item.receipt_verify_record_command}
                        >
                          <Copy size={9} />
                          {copiedIntakeCommand === item.receipt_verify_record_command ? copy.copiedCommand : copy.copyVerifyReceiptCommand}
                        </button>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
          {operatorHandoff && (
            <div className="mt-3 rounded px-3 py-2" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
              <div className="flex flex-col xl:flex-row xl:items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <ShieldCheck size={12} style={{ color: "var(--mis-cyan)" }} />
                    <div className="text-[10px] font-semibold" style={{ color: "var(--mis-text)" }}>{copy.operatorHandoffTitle}</div>
                    <StatusBadge status={operatorHandoff.status || "unknown"} />
                    {panelStatusBadge("operator_handoff")}
                    {panelRefreshButton("operator_handoff")}
                    {panelDiagnosticsButton("operator_handoff")}
                    {panelReceiptButton("operator_handoff")}
                    <StatusBadge status={operatorHandoff.safety.read_only && !operatorHandoff.safety.ledger_mutated && !operatorHandoff.safety.live_execution_performed ? "pass" : "attention"} label={operatorHandoff.safety.read_only ? copy.readOnlyProof : copy.statusAttention} />
                  </div>
                  <div className="text-[9px] mt-0.5 max-w-4xl" style={{ color: "var(--mis-muted)" }}>{copy.operatorHandoffSummary}</div>
                  {panelEvidenceLine("operator_handoff")}
                  <div className="text-[9px] mt-0.5 truncate" style={{ color: "var(--mis-dim)" }}>
                    {copy.methodBlock}: {operatorHandoff.work_order.method || operatorLoopAudit?.method || "READ -> PLAN -> RETRIEVE -> COMPARE -> EXECUTE -> VERIFY -> RECORD"}
                  </div>
                </div>
                <button
                  onClick={() => void copyIntakeCommand(operatorHandoffJson)}
                  className="inline-flex items-center gap-1 text-[9px] px-2 py-1 rounded shrink-0"
                  style={{ color: "var(--mis-cyan)", background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}
                  title={operatorHandoff.contract || copy.operatorHandoffTitle}
                >
                  <Copy size={10} />
                  {copiedIntakeCommand === operatorHandoffJson ? copy.copiedCommand : copy.copyHandoffJson}
                </button>
              </div>
              {handoffControlSummary && (
                <div className="mt-3 rounded px-2 py-1.5" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
                  <div className="flex flex-col xl:flex-row xl:items-start justify-between gap-2">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <Activity size={12} style={{ color: "var(--mis-cyan)" }} />
                        <div className="text-[10px] font-semibold" style={{ color: "var(--mis-text)" }}>{copy.loopControlTitle}</div>
                        <StatusBadge status={handoffControlSummary.status || "unknown"} />
                        <StatusBadge status={handoffControlSummary.server_executes_shell ? "blocked" : "pass"} label={handoffControlSummary.server_executes_shell ? "server shell" : "copy only"} />
                      </div>
                      <div className="text-[9px] mt-0.5 truncate" style={{ color: "var(--mis-muted)" }}>
                        {copy.recommendedStep}: {String(handoffControlStep.label || handoffControlStep.step_id || handoffControlSummary.selected_gate || "—")} · {copy.controlMode}: {handoffControlSummary.mode || "unknown"}
                      </div>
                      {(handoffControlStep.reason || handoffControlSummary.selected_status) && (
                        <div className="text-[8px] mt-0.5 line-clamp-2" style={{ color: handoffControlSummary.requires_human ? "var(--mis-warning)" : "var(--mis-dim)" }}>
                          {handoffControlSummary.requires_human ? copy.confirmRequired : copy.readyReason}: {String(handoffControlStep.reason || handoffControlSummary.selected_status)}
                        </div>
                      )}
                      <div className="grid grid-cols-2 xl:grid-cols-4 gap-1.5 mt-2">
                        {[
                          { label: copy.controlReadbackSource, value: loopControlReadbackSource, status: loopControlReadbackSource.includes("advance-loop") ? "pass" : "attention" },
                          { label: copy.cacheRefresh, value: loopControlRefreshRequired ? copy.yes : copy.no, status: loopControlRefreshRequired ? "attention" : "pass" },
                          { label: copy.commandSource, value: String(operatorHealthLoopControl.source || "operator_handoff.control_summary"), status: "pass" },
                          { label: copy.receiptProof, value: loopControlRequiresReceipt ? copy.receiptNeeded : copy.verifiedReceipts, status: loopControlRequiresReceipt ? "attention" : "pass" },
                        ].map((item) => (
                          <div key={item.label} className="rounded px-2 py-1" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                            <div className="text-[8px]" style={{ color: "var(--mis-muted)" }}>{item.label}</div>
                            <div className="flex items-center justify-between gap-1 mt-0.5">
                              <div className="text-[9px] font-semibold truncate" style={{ color: "var(--mis-text)" }}>{item.value}</div>
                              <StatusBadge status={item.status} />
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                    <div className="flex flex-wrap gap-1 shrink-0">
                      {handoffControlCommand && (
                        <button
                          onClick={() => void copyIntakeCommand(handoffControlCommand)}
                          className="inline-flex items-center gap-1 text-[8px] px-1.5 py-0.5 rounded max-w-full"
                          style={{ color: handoffControlSummary.requires_human ? "var(--mis-warning)" : "var(--mis-cyan)", background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}
                          title={handoffControlCommand}
                        >
                          <Copy size={8} />
                          <span className="truncate max-w-[96px]">{copiedIntakeCommand === handoffControlCommand ? copy.copiedCommand : copy.nextSafeCommand}</span>
                        </button>
                      )}
                      {handoffControlVerifyCommand && (
                        <button
                          onClick={() => void copyIntakeCommand(handoffControlVerifyCommand)}
                          className="inline-flex items-center gap-1 text-[8px] px-1.5 py-0.5 rounded max-w-full"
                          style={{ color: "var(--mis-success)", background: "rgba(45,212,191,0.08)", border: "1px solid rgba(45,212,191,0.18)" }}
                          title={handoffControlVerifyCommand}
                        >
                          <Copy size={8} />
                          <span className="truncate max-w-[86px]">{copiedIntakeCommand === handoffControlVerifyCommand ? copy.copiedCommand : copy.verifyAfterAction}</span>
                        </button>
                      )}
                      {handoffControlReceiptCommand && (
                        <button
                          onClick={() => void copyIntakeCommand(handoffControlReceiptCommand)}
                          className="inline-flex items-center gap-1 text-[8px] px-1.5 py-0.5 rounded max-w-full"
                          style={{ color: "var(--mis-warning)", background: "rgba(251,191,36,0.08)", border: "1px solid rgba(251,191,36,0.18)" }}
                          title={handoffControlReceiptCommand}
                        >
                          <Copy size={8} />
                          <span className="truncate max-w-[86px]">{copiedIntakeCommand === handoffControlReceiptCommand ? copy.copiedCommand : copy.copyVerifyReceiptCommand}</span>
                        </button>
                      )}
                    </div>
                  </div>
                </div>
              )}
              <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mt-3">
                {[
                  { label: copy.loopAuditTitle, value: operatorHandoffSummary?.loop_status || "unknown", status: operatorHandoffSummary?.loop_status || operatorHandoff.status },
                  { label: copy.actionQueueTitle, value: operatorHandoffSummary?.action_plan_status || "unknown", status: operatorHandoffSummary?.action_plan_status || operatorHandoff.status },
                  { label: copy.evidenceReportTitle, value: `${operatorHandoffSummary?.evidence_report_ready ?? 0}/${operatorHandoffSummary?.evidence_report_runs ?? 0}`, status: operatorHandoffSummary?.evidence_report_status || operatorHandoff.status },
                  { label: copy.remediationState, value: `${handoffEvidenceRemediationSummary.receipt_verified ?? 0}/${handoffEvidenceRemediationSummary.items ?? 0}`, status: (handoffEvidenceRemediationSummary.items || 0) > 0 ? "attention" : "pass" },
                  { label: copy.verifyAfterAction, value: `${handoffEvidenceRemediationSummary.workflow_ready_steps ?? 0}/${handoffEvidenceRemediationSummary.workflow_blocked_steps ?? 0}`, status: (handoffEvidenceRemediationSummary.workflow_blocked_steps || 0) > 0 ? "blocked" : (handoffEvidenceRemediationSummary.workflow_ready_steps || 0) > 0 ? "attention" : "pass" },
                  { label: copy.receiptProof, value: handoffEvidenceReceiptState?.verified ? `${copy.verifiedReceipts}: ${(handoffEvidenceReceiptState.receipt_hash || handoffEvidenceReceiptState.receipt_id || "").slice(0, 10)}` : handoffEvidenceReceiptState?.status || copy.noReceiptProof, status: handoffEvidenceReceiptState?.verified ? "pass" : handoffEvidenceReceiptState?.status || "attention" },
                  { label: copy.loopHealth, value: `${operatorHandoff.loop_health?.score ?? 0}/100`, status: operatorHandoff.loop_health?.status || "unknown" },
                  { label: copy.handoffCommands, value: `${operatorHandoffCommands.length}/${operatorHandoffSummary?.loop_package_items ?? 0}`, status: operatorHandoffCommands.length > 0 ? "attention" : "pass" },
                ].map((item) => (
                  <div key={item.label} className="rounded px-2 py-1" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
                    <div className="text-[9px]" style={{ color: "var(--mis-muted)" }}>{item.label}</div>
                    <div className="flex items-center justify-between gap-2 mt-0.5">
                      <div className="text-[10px] font-semibold truncate" style={{ color: "var(--mis-text)" }}>{item.value}</div>
                      <StatusBadge status={item.status} />
                    </div>
                  </div>
                ))}
              </div>
              {remediationWorkflowRows.length > 0 && (
                <div className="mt-2 rounded px-2 py-1.5" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
                  <div className="flex items-center justify-between gap-2">
                    <div className="text-[10px] font-semibold" style={{ color: "var(--mis-text)" }}>{copy.remediationWorkflow}</div>
                    <StatusBadge status={(handoffEvidenceRemediationSummary.workflow_blocked_steps || 0) > 0 ? "blocked" : "attention"} label={`${handoffEvidenceRemediationSummary.workflow_ready_steps ?? 0}/${handoffEvidenceRemediationSummary.workflow_blocked_steps ?? 0}`} />
                  </div>
                  <div className="space-y-1 mt-1">
                    {remediationWorkflowRows.map((row) => (
                      <div key={row.key} className="rounded px-2 py-1" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                        <div className="flex flex-col xl:flex-row xl:items-center justify-between gap-1">
                          <div className="min-w-0">
                            <div className="flex items-center gap-1 min-w-0">
                              <Link to={`/admin/runs/${row.runId}`} className="text-[9px] font-semibold truncate" style={{ color: "var(--mis-cyan)" }}>{row.runId}</Link>
                              <StatusBadge status={row.status} />
                              {(row.mutating || row.confirmRequired) && <StatusBadge status="attention" label={row.confirmRequired ? "confirm" : "write"} />}
                            </div>
                            <div className="text-[9px] mt-0.5 truncate" style={{ color: "var(--mis-muted)" }}>
                              {row.stepId}: {row.label} · {row.nextSafeCommandKind} · receipt {row.receiptStatus}
                            </div>
                            <div className="text-[9px] mt-0.5 truncate" style={{ color: row.status === "blocked" ? "var(--mis-warning)" : "var(--mis-dim)" }}>
                              {row.status === "blocked" ? copy.blockedReason : copy.readyReason}: {row.reason} · {copy.prerequisiteStep}: {row.prerequisite}
                            </div>
                          </div>
                          <div className="flex flex-wrap gap-1 shrink-0">
                            {row.nextSafeCommand && (
                              <button
                                onClick={() => void copyIntakeCommand(row.nextSafeCommand)}
                                className="inline-flex items-center gap-1 text-[9px] px-1.5 py-0.5 rounded max-w-full"
                                style={{ color: row.mutating || row.confirmRequired ? "var(--mis-warning)" : "var(--mis-cyan)", background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}
                                title={row.nextSafeCommand}
                              >
                                <Copy size={9} />
                                <span className="truncate max-w-[120px]">{copiedIntakeCommand === row.nextSafeCommand ? copy.copiedCommand : copy.nextSafeCommand}</span>
                              </button>
                            )}
                            {row.verifyCommand && (
                              <button
                                onClick={() => void copyIntakeCommand(row.verifyCommand)}
                                className="inline-flex items-center gap-1 text-[9px] px-1.5 py-0.5 rounded max-w-full"
                                style={{ color: "var(--mis-success)", background: "rgba(45,212,191,0.08)", border: "1px solid rgba(45,212,191,0.18)" }}
                                title={row.verifyCommand}
                              >
                                <Copy size={9} />
                                <span className="truncate max-w-[110px]">{copiedIntakeCommand === row.verifyCommand ? copy.copiedCommand : copy.verifyAfterAction}</span>
                              </button>
                            )}
                            {row.receiptNextCommand && (
                              <button
                                onClick={() => void copyIntakeCommand(row.receiptNextCommand)}
                                className="inline-flex items-center gap-1 text-[9px] px-1.5 py-0.5 rounded max-w-full"
                                style={{ color: "var(--mis-warning)", background: "rgba(251,191,36,0.08)", border: "1px solid rgba(251,191,36,0.18)" }}
                                title={row.receiptNextCommand}
                              >
                                <Copy size={9} />
                                <span className="truncate max-w-[120px]">{copiedIntakeCommand === row.receiptNextCommand ? copy.copiedCommand : copy.copyReceiptCommand}</span>
                              </button>
                            )}
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-6 gap-2 mt-2">
                <div className="rounded px-2 py-1.5" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
                  <div className="text-[10px] font-semibold" style={{ color: "var(--mis-text)" }}>{copy.loopHealth}</div>
                  <div className="text-[9px] mt-1" style={{ color: "var(--mis-muted)" }}>
                    {copy.loopRisks}: {operatorHandoff.loop_health?.risks?.length ?? 0} · {copy.authBoundary}: {operatorHandoff.auth?.mode || "unknown"} · {operatorHandoff.auth?.required_scope || "tasks:read"}
                  </div>
                  <div className="text-[9px] mt-0.5 truncate" style={{ color: "var(--mis-cyan)" }}>
                    {copy.nextAction}: {operatorHandoff.loop_health?.next_action || operatorHandoff.work_order.next_actions[0] || loopAuditNextAction}
                  </div>
                </div>
                <div className="rounded px-2 py-1.5" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
                  <div className="flex items-center justify-between gap-2">
                    <div className="text-[10px] font-semibold truncate" style={{ color: "var(--mis-text)" }}>{copy.loopSelfCheckTitle}</div>
                    <StatusBadge status={operatorHandoff.loop_health?.status || "unknown"} />
                  </div>
                  <div className="text-[9px] mt-1 truncate" style={{ color: "var(--mis-muted)" }}>
                    {copy.loopHealth}: {operatorHandoff.loop_health?.score ?? 0}/100 · {copy.advanceLoopPolicyLabel}: {advanceLoopPolicyId}
                  </div>
                  <div className="text-[9px] mt-0.5 truncate" style={{ color: "var(--mis-dim)" }}>
                    {copy.advanceLoopPolicy}
                  </div>
                  <div className="text-[8px] mt-1 font-semibold" style={{ color: "var(--mis-muted)" }}>{copy.loopSelfCheckGates}</div>
                  <div className="space-y-0.5 mt-0.5">
                    {loopSelfCheckGateSummaries.map((gate) => (
                      <div key={gate.id} className="flex items-center justify-between gap-1 min-w-0">
                        <span className="text-[8px] truncate" style={{ color: "var(--mis-muted)" }}>{gate.label}: {gate.detail}</span>
                        <StatusBadge status={gate.status} />
                      </div>
                    ))}
                  </div>
                  <button
                    onClick={() => void copyIntakeCommand(loopSelfCheckCommand)}
                    className="inline-flex items-center gap-1 text-[9px] px-1.5 py-0.5 rounded max-w-full mt-1"
                    style={{ color: "var(--mis-cyan)", background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}
                    title={copy.loopSelfCheckSummary}
                  >
                    <ShieldCheck size={9} />
                    <span className="truncate max-w-[150px]">{copiedIntakeCommand === loopSelfCheckCommand ? copy.copiedCommand : copy.loopSelfCheckCopy}</span>
                  </button>
                </div>
                <div className="rounded px-2 py-1.5" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
                  <div className="text-[10px] font-semibold" style={{ color: "var(--mis-text)" }}>{copy.receiptProof}</div>
                  <div className="text-[9px] mt-1" style={{ color: "var(--mis-muted)" }}>
                    {operatorHandoff.receipt_state.coverage?.verified ?? 0}/{operatorHandoff.receipt_state.coverage?.required ?? 0} · missing {operatorHandoff.receipt_state.coverage?.missing ?? 0} · stale {operatorHandoff.receipt_state.coverage?.stale ?? 0}
                  </div>
                  <div className="text-[9px] mt-0.5" style={{ color: "var(--mis-dim)" }}>
                    {copy.actionReceipts}: {operatorHandoff.receipt_state.summary.receipts ?? operatorHandoff.receipt_state.recent.length}/{operatorHandoff.receipt_state.summary.verified ?? 0}
                  </div>
                </div>
                <div className="rounded px-2 py-1.5" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
                  <div className="flex items-center justify-between gap-2">
                    <div className="text-[10px] font-semibold truncate" style={{ color: "var(--mis-text)" }}>{copy.advanceLoopTitle}</div>
                    <StatusBadge status={advanceLoopRaw.status ? String(advanceLoopRaw.status) : "empty"} />
                  </div>
                  <div className="text-[9px] mt-1 truncate" style={{ color: "var(--mis-muted)" }}>
                    {copy.nextGateAction}: {advanceLoopSelectedGate} · {advanceLoopSelectedStatus}
                  </div>
                  <div className="text-[9px] mt-0.5 truncate" style={{ color: advanceLoopServerShell ? "#F87171" : "var(--mis-dim)" }}>
                    {copy.advanceLoopPolicy}
                  </div>
                  <div className="text-[9px] mt-0.5 truncate" style={{ color: "var(--mis-muted)" }}>
                    {copy.advanceLoopPolicyLabel}: {advanceLoopPolicyId} · {advanceLoopPolicyVersion}
                  </div>
                  <div className="flex flex-wrap gap-1 mt-1">
                    <button
                      onClick={() => void copyIntakeCommand(advanceLoopPreviewCommand)}
                      className="inline-flex items-center gap-1 text-[9px] px-1.5 py-0.5 rounded max-w-full"
                      style={{ color: "var(--mis-cyan)", background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}
                      title={copy.advanceLoopSummary}
                    >
                      <Copy size={9} />
                      <span className="truncate max-w-[130px]">{copiedIntakeCommand === advanceLoopPreviewCommand ? copy.copiedCommand : copy.previewAdvanceLoop}</span>
                    </button>
                    <button
                      onClick={() => void copyIntakeCommand(advanceLoopConfirmCommand)}
                      className="inline-flex items-center gap-1 text-[9px] px-1.5 py-0.5 rounded max-w-full"
                      style={{ color: "var(--mis-success)", background: "rgba(45,212,191,0.08)", border: "1px solid rgba(45,212,191,0.18)" }}
                      title={advanceLoopConfirmCommand}
                    >
                      <Terminal size={9} />
                      <span className="truncate max-w-[120px]">{copiedIntakeCommand === advanceLoopConfirmCommand ? copy.copiedCommand : copy.confirmAdvanceLoop}</span>
                    </button>
                  </div>
                </div>
                <div className="rounded px-2 py-1.5" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
                  <div className="flex items-center justify-between gap-2">
                    <div className="text-[10px] font-semibold truncate" style={{ color: "var(--mis-text)" }}>{copy.receiptFailureMemoryTitle}</div>
                    <StatusBadge status={receiptFailureMemoryCandidateCount > 0 ? "attention" : "pass"} />
                  </div>
                  <div className="text-[9px] mt-1" style={{ color: "var(--mis-muted)" }}>
                    {copy.failureCandidates}: {receiptFailureMemoryCandidateCount} · {copy.failedReceipts}: {receiptFailureMemoryFailedReceipts}
                  </div>
                  <div className="text-[9px] mt-0.5" style={{ color: "var(--mis-dim)" }}>
                    {copy.existingCandidates}: {receiptFailureMemoryExistingCandidates} · {receiptFailureMemoryCandidates[0]?.action_hash_short ? String(receiptFailureMemoryCandidates[0].action_hash_short) : "—"}
                  </div>
                  <div className="flex flex-wrap gap-1 mt-1">
                    <button
                      onClick={() => void handleReceiptFailureMemory(false)}
                      disabled={Boolean(receiptFailureMemoryAction)}
                      className="inline-flex items-center gap-1 text-[9px] px-1.5 py-0.5 rounded disabled:opacity-50"
                      style={{ color: "var(--mis-cyan)", background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}
                      title={copy.receiptFailureMemorySummary}
                    >
                      <ShieldCheck size={9} />
                      {receiptFailureMemoryAction === "receipt-failure-memory-preview" ? copy.dispatching : copy.previewFailureMemory}
                    </button>
                    <button
                      onClick={() => void handleReceiptFailureMemory(true)}
                      disabled={Boolean(receiptFailureMemoryAction) || receiptFailureMemoryCandidateCount === 0}
                      className="inline-flex items-center gap-1 text-[9px] px-1.5 py-0.5 rounded disabled:opacity-50"
                      style={{ color: "var(--mis-success)", background: "rgba(45,212,191,0.08)", border: "1px solid rgba(45,212,191,0.18)" }}
                      title={copy.createFailureMemoryConfirm}
                    >
                      <CheckCircle2 size={9} />
                      {receiptFailureMemoryAction === "receipt-failure-memory-create" ? copy.dispatching : copy.createFailureMemory}
                    </button>
                    <button
                      onClick={() => void copyIntakeCommand(receiptFailureMemoryNextAction)}
                      className="inline-flex items-center gap-1 text-[9px] px-1.5 py-0.5 rounded max-w-full"
                      style={{ color: "var(--mis-cyan)", background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}
                      title={receiptFailureMemoryNextAction}
                    >
                      <Copy size={9} />
                      <span className="truncate max-w-[150px]">{copiedIntakeCommand === receiptFailureMemoryNextAction ? copy.copiedCommand : copy.proposeFailureMemory}</span>
                    </button>
                  </div>
                  {receiptFailureMemoryResult && (
                    <div className="text-[9px] mt-1 truncate" style={{ color: receiptFailureMemoryResult.toLowerCase().includes("error") ? "#F87171" : "var(--mis-cyan)" }}>
                      {receiptFailureMemoryResult}
                    </div>
                  )}
                </div>
                <div className="rounded px-2 py-1.5" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
                  <div className="text-[10px] font-semibold" style={{ color: "var(--mis-text)" }}>{copy.reviewQueueTitle}</div>
                  <div className="text-[9px] mt-1" style={{ color: "var(--mis-muted)" }}>
                    {copy.memoryCandidates}: {operatorHandoff.review_state.loop_record?.candidate_count ?? 0} · {copy.pendingApprovals}: {operatorHandoff.review_state.loop_record?.pending_approval_count ?? 0}
                  </div>
                  <div className="text-[9px] mt-0.5" style={{ color: "var(--mis-dim)" }}>
                    {copy.loopRecordState}: {operatorHandoffSummary?.loop_record_approved ?? 0}/{operatorHandoffSummary?.loop_record_candidates ?? 0}/{operatorHandoffSummary?.loop_record_pending_approvals ?? 0}
                  </div>
                  <div className="text-[9px] mt-0.5 truncate" style={{ color: "var(--mis-cyan)" }}>
                    {copy.nextAction}: {operatorHandoff.review_state.loop_record?.next_action || operatorHandoff.work_order.next_actions[0] || loopAuditNextAction}
                  </div>
                </div>
              </div>
              <div className="rounded px-2 py-1.5 mt-2" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
                <div className="text-[10px] font-semibold" style={{ color: "var(--mis-text)" }}>{copy.handoffSources}</div>
                <div className="grid grid-cols-1 md:grid-cols-3 gap-2 mt-1">
                  {Object.entries(operatorHandoffSources).slice(0, 3).map(([sourceName, sourceValue]) => {
                    const sourceRecord = sourceValue && typeof sourceValue === "object" ? sourceValue as Record<string, unknown> : {};
                    const sourceStatus = String(sourceRecord.status || "unknown");
                    const sourceStatusRaw = typeof sourceRecord.source_status === "object" && sourceRecord.source_status !== null ? sourceRecord.source_status as Record<string, unknown> : {};
                    const sourceStatusText = Object.entries(sourceStatusRaw).slice(0, 3).map(([key, value]) => `${key}:${String(value)}`).join(" · ");
                    return (
                      <div key={sourceName} className="min-w-0">
                        <div className="flex items-center justify-between gap-2">
                          <div className="text-[9px] truncate" style={{ color: "var(--mis-muted)" }}>{sourceName}</div>
                          <StatusBadge status={sourceStatus} />
                        </div>
                        {sourceStatusText && <div className="text-[8px] truncate" style={{ color: "var(--mis-dim)" }}>{sourceStatusText}</div>}
                      </div>
                    );
                  })}
                </div>
              </div>
              <div className="flex flex-wrap gap-1.5 mt-2">
                {operatorHandoffCommands.slice(0, 6).map((handoffCommand) => (
                  <button
                    key={handoffCommand}
                    onClick={() => void copyIntakeCommand(handoffCommand)}
                    className="inline-flex items-center gap-1 text-[9px] px-1.5 py-0.5 rounded max-w-full"
                    style={{ color: "var(--mis-cyan)", background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}
                    title={handoffCommand}
                  >
                    <Copy size={9} />
                    <span className="truncate max-w-[220px]">{copiedIntakeCommand === handoffCommand ? copy.copiedCommand : handoffCommand}</span>
                  </button>
                ))}
              </div>
            </div>
          )}
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-2 mt-3">
            {loopAuditSteps.map((step) => {
              const evidenceEntries = Object.entries(step.evidence || {});
              const isGapEvidence = ([key, value]: [string, unknown]) => {
                const keyText = key.toLowerCase();
                if (!["blocked", "missing", "gap", "pending", "candidate", "failed", "error"].some(marker => keyText.includes(marker))) {
                  return false;
                }
                const numeric = typeof value === "number" ? value : Number(value);
                return Number.isFinite(numeric) ? numeric > 0 : Boolean(value);
              };
              const formatEvidenceValue = (value: unknown) => {
                if (Array.isArray(value)) return value.length;
                if (value && typeof value === "object") return Object.keys(value).length;
                return String(value);
              };
              const gapEvidenceEntries = evidenceEntries.filter(isGapEvidence).slice(0, 3);
              const proofEvidenceEntries = evidenceEntries
                .filter(entry => !isGapEvidence(entry))
                .filter(([, value]) => Array.isArray(value) ? value.length > 0 : value && value !== "0")
                .slice(0, 3);
              const recordAuditEntries = step.id === "record" ? (loopRecord?.audit_trail || []) : [];
              const latestRecordAudit = recordAuditEntries[0];
              const latestRecordAuditHash = latestRecordAudit ? (latestRecordAudit.tamper_chain_hash || latestRecordAudit.after_hash || latestRecordAudit.before_hash || latestRecordAudit.audit_id || "") : "";
              return (
                <div key={step.id} className="rounded px-3 py-2" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                  <div className="flex items-center justify-between gap-2">
                    <div className="text-[10px] font-semibold truncate" style={{ color: "var(--mis-text)" }}>{step.label}</div>
                    <StatusBadge status={step.status} />
                  </div>
                  <div className="text-[9px] mt-1 line-clamp-2" style={{ color: "var(--mis-muted)" }}>{step.message || step.source}</div>
                  {(gapEvidenceEntries.length > 0 || proofEvidenceEntries.length > 0) && (
                    <div className="mt-2 space-y-1">
                      {gapEvidenceEntries.length > 0 && (
                        <div>
                          <div className="text-[8px] uppercase" style={{ color: "var(--mis-warning)" }}>{copy.gateEvidenceGaps}</div>
                          <div className="flex flex-wrap gap-1 mt-0.5">
                            {gapEvidenceEntries.map(([key, value]) => (
                              <span key={key} className="text-[9px] px-1.5 py-0.5 rounded" style={{ color: "var(--mis-warning)", background: "rgba(245,158,11,0.08)", border: "1px solid rgba(245,158,11,0.18)" }}>
                                {key}: {formatEvidenceValue(value)}
                              </span>
                            ))}
                          </div>
                        </div>
                      )}
                      {proofEvidenceEntries.length > 0 && (
                        <div>
                          <div className="text-[8px] uppercase" style={{ color: "var(--mis-success)" }}>{copy.gateEvidenceProof}</div>
                          <div className="flex flex-wrap gap-1 mt-0.5">
                            {proofEvidenceEntries.map(([key, value]) => (
                              <span key={key} className="text-[9px] px-1.5 py-0.5 rounded" style={{ color: "var(--mis-dim)", background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
                                {key}: {formatEvidenceValue(value)}
                              </span>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  )}
                  {step.id === "record" && loopRecord && (
                    <div className="mt-2 rounded px-2 py-1.5" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
                      <div className="flex items-center justify-between gap-2">
                        <div className="text-[9px] font-semibold truncate" style={{ color: "var(--mis-text)" }}>{copy.loopRecordAuditTrail}</div>
                        <StatusBadge status={loopRecord.status} />
                      </div>
                      <div className="flex flex-wrap gap-1 mt-1">
                        <StatusBadge status={(loopRecord.candidate_count || 0) > 0 ? "attention" : "pass"} label={`${copy.loopMemoryReview}: ${loopRecord.candidate_count}/${loopRecord.approved_count}`} />
                        <StatusBadge status={(loopRecord.pending_approval_count || 0) > 0 ? "attention" : "pass"} label={`${copy.loopApprovalReview}: ${loopRecord.pending_approval_count}`} />
                        <StatusBadge status={(loopRecord.audit_count || 0) > 0 ? "pass" : "attention"} label={`${copy.loopRecordAuditTrail}: ${loopRecord.audit_count || 0}`} />
                      </div>
                      {latestRecordAuditHash && (
                        <div className="text-[9px] mt-1 truncate" style={{ color: "var(--mis-dim)" }}>
                          {latestRecordAudit?.action || copy.loopRecordAuditTrail}: {latestRecordAuditHash.slice(0, 12)}
                        </div>
                      )}
                    </div>
                  )}
                  <div className="flex items-center justify-between gap-2 mt-2 pt-2" style={{ borderTop: "1px solid var(--mis-border)" }}>
                    <div className="text-[9px] truncate" style={{ color: "var(--mis-muted)" }}>
                      {copy.actionSource}: {step.source || "—"}
                    </div>
                    {step.command && (
                      <button
                        onClick={() => void copyIntakeCommand(step.command)}
                        className="inline-flex items-center gap-1 text-[9px] px-1.5 py-0.5 rounded shrink-0"
                        style={{ color: "var(--mis-cyan)", background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}
                        title={step.command}
                      >
                        <Copy size={9} />
                        {copiedIntakeCommand === step.command ? copy.copiedCommand : copy.copyCommand}
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
          <div className="mt-3 rounded px-3 py-2 flex flex-col sm:flex-row sm:items-center justify-between gap-2" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
            <div className="min-w-0">
              <div className="text-[9px]" style={{ color: "var(--mis-muted)" }}>{copy.nextGateAction}</div>
              <div className="text-[10px] font-semibold truncate" style={{ color: "var(--mis-cyan)" }}>{loopAuditNextAction}</div>
            </div>
            <StatusBadge status={operatorLoopAudit?.token_omitted && !operatorLoopAudit?.live_execution_performed ? "pass" : "attention"} label={operatorLoopAudit?.token_omitted ? copy.tokenOmittedProof : copy.statusAttention} />
          </div>
          <div className="mt-3 rounded px-3 py-2" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
            <div className="flex flex-col md:flex-row md:items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <CheckCircle2 size={12} style={{ color: loopRecord?.status === "ready" ? "var(--mis-success)" : "var(--mis-warning)" }} />
                  <div className="text-[10px] font-semibold" style={{ color: "var(--mis-text)" }}>{copy.loopRecordTitle}</div>
                  <StatusBadge status={loopRecord?.status || recordStep?.status || "unknown"} />
                </div>
                <div className="text-[10px] mt-1" style={{ color: "var(--mis-muted)" }}>{copy.loopRecordSummary}</div>
                <div className="text-[9px] mt-1 truncate" style={{ color: "var(--mis-dim)" }}>
                  {copy.scopedLoopId}: {loopRecord?.loop_id || operatorLoopAudit?.loop_id || loopLaneReadback?.loop_id || "—"}
                </div>
              </div>
              <div className="flex items-center gap-1.5 flex-wrap shrink-0">
                <StatusBadge status={(loopRecord?.candidate_count || 0) > 0 ? "attention" : "pass"} label={`${copy.loopMemoryReview}: ${loopRecord?.candidate_count ?? 0}/${loopRecord?.approved_count ?? 0}`} />
                <StatusBadge status={(loopRecord?.pending_approval_count || 0) > 0 ? "attention" : "pass"} label={`${copy.loopApprovalReview}: ${loopRecord?.pending_approval_count ?? 0}`} />
              </div>
            </div>
            <div className="mt-2 space-y-1.5">
              {loopRecordItems.length === 0 && (
                <div className="text-[10px]" style={{ color: "var(--mis-muted)" }}>{copy.noLoopRecordItems}</div>
              )}
              {loopRecordItems.slice(0, 4).map((item) => {
                const isMemory = "memory_id" in item;
                const reviewKind = isMemory ? "memory" : "approval";
                const itemId = isMemory ? item.memory_id : item.approval_id;
                const itemStatus = isMemory ? item.review_status : item.decision;
                const title = isMemory ? `${copy.loopMemoryReview}: ${item.memory_type || "memory"}` : `${copy.loopApprovalReview}: ${item.tool_call_id || item.run_id || "approval"}`;
                const summary = isMemory ? item.summary : item.reason;
                const canReview = isMemory ? item.review_status === "candidate" : item.decision === "pending";
                const approveActionKey = `loop-record-${reviewKind}-${itemId}-approve`;
                const rejectActionKey = `loop-record-${reviewKind}-${itemId}-reject`;
                return (
                  <div key={itemId} className="rounded px-2 py-1.5" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
                    <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
                      <div className="min-w-0">
                        <div className="flex items-center gap-1.5">
                          <div className="text-[10px] font-semibold truncate" style={{ color: "var(--mis-text)" }}>{title}</div>
                          <StatusBadge status={itemStatus} />
                        </div>
                        <div className="text-[9px] mt-0.5 truncate" style={{ color: "var(--mis-muted)" }}>{itemId} · {isMemory ? item.source_ref || item.task_id || "loop memory" : item.run_id || item.task_id || "loop approval"}</div>
                        {summary && <div className="text-[9px] mt-0.5 line-clamp-2" style={{ color: "var(--mis-dim)" }}>{summary}</div>}
                      </div>
                      <div className="flex items-center gap-1.5 shrink-0">
                        {canReview && (
                          <>
                            <button
                              onClick={() => void handleLoopRecordDecision(reviewKind, itemId, "approve")}
                              disabled={Boolean(loopRecordAction)}
                              className="inline-flex items-center gap-1 text-[10px] px-2 py-1 rounded disabled:opacity-50"
                              style={{ background: "rgba(42,157,143,0.12)", color: "var(--mis-success)", border: "1px solid rgba(42,157,143,0.22)" }}
                            >
                              <CheckCircle2 size={10} />
                              {loopRecordAction === approveActionKey ? copy.dispatching : copy.reviewApprove}
                            </button>
                            <button
                              onClick={() => void handleLoopRecordDecision(reviewKind, itemId, "reject")}
                              disabled={Boolean(loopRecordAction)}
                              className="inline-flex items-center gap-1 text-[10px] px-2 py-1 rounded disabled:opacity-50"
                              style={{ background: "rgba(248,113,113,0.10)", color: "#F87171", border: "1px solid rgba(248,113,113,0.18)" }}
                            >
                              <XCircle size={10} />
                              {loopRecordAction === rejectActionKey ? copy.dispatching : copy.reviewReject}
                            </button>
                          </>
                        )}
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
            {loopRecordResult && (
              <div className="text-[10px] mt-2 rounded px-2 py-1" style={{ color: loopRecordResult.toLowerCase().includes("error") ? "#F87171" : "var(--mis-cyan)", background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
                {loopRecordResult}
              </div>
            )}
            {(loopRecord?.audit_trail || []).length > 0 && (
              <div className="mt-2 rounded px-2 py-1.5" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
                <div className="flex items-center justify-between gap-2">
                  <div className="text-[10px] font-semibold" style={{ color: "var(--mis-text)" }}>{copy.loopRecordAuditTrail}</div>
                  <StatusBadge status="pass" label={`${loopRecord?.audit_count || loopRecord.audit_trail.length}`} />
                </div>
                <div className="mt-1 space-y-1">
                  {loopRecord.audit_trail.slice(0, 3).map((entry) => (
                    <div key={entry.audit_id} className="flex flex-col sm:flex-row sm:items-center justify-between gap-1 text-[9px]" style={{ color: "var(--mis-muted)" }}>
                      <div className="min-w-0 truncate">
                        <span style={{ color: "var(--mis-cyan)" }}>{entry.action}</span>
                        {" · "}
                        {entry.entity_type}/{entry.entity_id}
                      </div>
                      <div className="shrink-0 truncate" style={{ color: "var(--mis-dim)" }}>
                        {(entry.tamper_chain_hash || entry.after_hash || entry.before_hash || "").slice(0, 12) || entry.audit_id}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
            <div className="text-[10px] mt-2 truncate" style={{ color: "var(--mis-cyan)" }}>{copy.nextAction}: {loopRecord?.next_action || loopAuditNextAction}</div>
          </div>
        </div>

        <div className="rounded-lg p-3 mt-4" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
          <div className="flex items-start justify-between gap-3">
            <div>
              <div className="flex items-center gap-2">
                <GripVertical size={13} style={{ color: "var(--mis-cyan)" }} />
                <div className="text-[11px] font-semibold" style={{ color: "var(--mis-text)" }}>{copy.actionQueueTitle}</div>
                <StatusBadge status={operatorActionPlan?.status || "unknown"} />
                {panelStatusBadge("operator_action_plan")}
                {panelRefreshButton("operator_action_plan")}
                {panelDiagnosticsButton("operator_action_plan")}
                {panelReceiptButton("operator_action_plan")}
              </div>
              <p className="text-[10px] mt-1" style={{ color: "var(--mis-muted)" }}>
                {copy.actionQueueSummary}
                {operatorPlanSummary && ` · blocked ${operatorPlanSummary.blocked} / attention ${operatorPlanSummary.attention} / adapter ${operatorPlanSummary.recommended_adapter}`}
                {operatorPlanSummary && ` · remediation ${operatorPlanSummary.remediation_packages}/${operatorPlanSummary.remediation_pending_reviews}/${operatorPlanSummary.remediation_promoted_deliveries}`}
                {operatorPlanSummary && ` · evidence gaps ${operatorPlanSummary.evidence_gap_runs}/${operatorPlanSummary.blocked_evidence_gap_runs}/${operatorPlanSummary.remediated_evidence_gap_runs}`}
                {operatorPlanSummary && ` · synth ${operatorPlanSummary.evidence_synthesis_ready_runs}/${operatorPlanSummary.evidence_synthesis_pending_runs}/${operatorPlanSummary.evidence_synthesis_promoted_runs}`}
                {operatorPlanSummary && ` · close ${operatorPlanSummary.evidence_gap_closure_ready_runs}/${operatorPlanSummary.closed_evidence_gap_runs}/${operatorPlanSummary.waived_evidence_gap_runs}`}
                {operatorPlanSummary && ` · intake ${operatorPlanSummary.task_intake_ready}/${operatorPlanSummary.task_intake_blocked}/${operatorPlanSummary.task_intake_attention}`}
                {operatorReceiptCoverage && ` · receipt coverage ${operatorReceiptCoverage.verified}/${operatorReceiptCoverage.required} · stale ${operatorReceiptCoverage.stale} · missing ${operatorReceiptCoverage.missing} · ${operatorReceiptCoverage.coverage_percent}%`}
                {operatorReceiptCoverage && ` · receipt eval ${operatorReceiptCoverage.evaluated ?? 0}/${operatorReceiptCoverage.evaluation_required ?? 0} · failed ${operatorReceiptCoverage.evaluation_fail ?? 0} · ${operatorReceiptCoverage.evaluation_coverage_percent ?? 100}%`}
                {operatorPlanSummary && ` · failure memory ${operatorPlanSummary.receipt_failure_memory_candidates}/${operatorPlanSummary.receipt_failure_memory_failed_receipts}/${operatorPlanSummary.receipt_failure_memory_existing_candidates}`}
                {operatorActionReceipts?.summary && ` · ${copy.actionReceipts.toLowerCase()} ${operatorActionReceipts.summary.receipts}/${operatorActionReceipts.summary.verified}`}
                {operatorActionReceipts?.summary && ` · ${copy.controlReadback.toLowerCase()} ${operatorActionReceipts.summary.control_readback_attached}/${operatorActionReceipts.summary.control_readback_required} · missing ${operatorActionReceipts.summary.control_readback_missing} · ${operatorActionReceipts.summary.control_readback_coverage_percent}%`}
                {operatorActionReceipts?.summary?.latest_control_readback_hash && ` · ${copy.tamperChain.toLowerCase()} ${operatorActionReceipts.summary.latest_control_readback_hash.slice(0, 10)}`}
              </p>
              {panelEvidenceLine("operator_action_plan")}
            </div>
            <button
              onClick={() => setActionQueueOrder(actionQueueCandidates.map(item => item.id))}
              className="text-[10px] px-2 py-1 rounded"
              style={{ color: "var(--mis-muted)", background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}
            >
              {copy.resetOrder}
            </button>
          </div>
          <div className="grid grid-cols-1 xl:grid-cols-2 gap-2 mt-3">
            {visibleActionQueue.length === 0 && (
              <div className="text-[11px] rounded px-3 py-2" style={{ color: "var(--mis-muted)", background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                {copy.noRecommendedActions}
              </div>
            )}
            {visibleActionQueue.map((item, index) => {
              const closeGapDetails = closeGapDetailsForAction(item);
              const closeGapBusy = Boolean(closeGapDetails && dispatching === `close-gap:${closeGapDetails.runId}`);
              const verifyAction = "verifyAction" in item ? item.verifyAction : undefined;
              const recordBusy = receiptAction === `action-receipt:recorded:${item.id}`;
              const verifyBusy = receiptAction === `action-receipt:verified:${item.id}`;
              const queueReceipt = latestReceiptForAction(item.action, "actionSignature" in item ? item.actionSignature : undefined);
              const backendReceiptStatus = "receiptStatus" in item ? item.receiptStatus : undefined;
              const backendReceiptHash = "receiptHash" in item ? item.receiptHash : undefined;
              const backendReceiptEvaluation = "receiptEvaluation" in item ? item.receiptEvaluation : undefined;
              const queueReceiptEvaluation = backendReceiptEvaluation || queueReceipt?.evaluation;
              const queueReceiptEvaluationStatus = typeof queueReceiptEvaluation === "object" && queueReceiptEvaluation !== null ? String((queueReceiptEvaluation as Record<string, unknown>).pass_fail || "") : "";
              const queueReceiptStatus = backendReceiptStatus || queueReceipt?.status;
              const queueReceiptHash = receiptShortHash(queueReceipt) || String(backendReceiptHash || "").slice(0, 12);
              const queueControlReadbackRaw = queueReceipt?.control_readback;
              const queueControlReadback = (
                typeof queueControlReadbackRaw === "object" && queueControlReadbackRaw !== null
                  ? queueControlReadbackRaw
                  : {}
              ) as Record<string, unknown>;
              const queueControlBefore = (
                typeof queueControlReadback.before === "object" && queueControlReadback.before !== null
                  ? queueControlReadback.before
                  : {}
              ) as Record<string, unknown>;
              const queueControlAfter = (
                typeof queueControlReadback.after === "object" && queueControlReadback.after !== null
                  ? queueControlReadback.after
                  : {}
              ) as Record<string, unknown>;
              const queueControlSelfCheck = (
                typeof queueControlReadback.after_self_check === "object" && queueControlReadback.after_self_check !== null
                  ? queueControlReadback.after_self_check
                  : {}
              ) as Record<string, unknown>;
              const queueControlReadbackHash = String(queueReceipt?.control_readback_hash || "").slice(0, 12);
              const queueControlReadbackVisible = Boolean(queueControlBefore.selected_gate || queueControlAfter.selected_gate || queueControlSelfCheck.selected_gate);
              const queueControlCacheProof = queueControlReadback.cache_bypassed === true ? "bypass" : queueControlReadback.refresh_cache_requested === true ? "refresh" : "";
              const queueNeedsReceipt = !candidateReceiptVerified(item);
              const receiptRecordCommand = "receiptRecordCommand" in item ? item.receiptRecordCommand : undefined;
              const receiptVerifyRecordCommand = "receiptVerifyRecordCommand" in item ? item.receiptVerifyRecordCommand : undefined;
              const remediationWorkflowStepId = "remediationWorkflowStepId" in item ? item.remediationWorkflowStepId : "";
              const remediationWorkflowKind = "remediationWorkflowKind" in item ? item.remediationWorkflowKind : "";
              const remediationWorkflowPrerequisite = "remediationWorkflowPrerequisite" in item ? item.remediationWorkflowPrerequisite : "";
              const remediationWorkflowMutating = "remediationWorkflowMutating" in item ? item.remediationWorkflowMutating : false;
              const remediationWorkflowConfirmRequired = "remediationWorkflowConfirmRequired" in item ? item.remediationWorkflowConfirmRequired : false;
              return (
                <div
                  key={item.id}
                  draggable
                  onDragStart={(event) => {
                    event.dataTransfer.effectAllowed = "move";
                    event.dataTransfer.setData("text/plain", item.id);
                    setDraggedActionId(item.id);
                  }}
                  onDragOver={(event) => {
                    event.preventDefault();
                    event.dataTransfer.dropEffect = "move";
                  }}
                  onDrop={(event) => {
                    event.preventDefault();
                    const activeId = event.dataTransfer.getData("text/plain") || draggedActionId;
                    if (activeId) moveActionQueueItem(activeId, item.id);
                    setDraggedActionId(null);
                  }}
                  onDragEnd={() => setDraggedActionId(null)}
                  className="grid grid-cols-[auto_1fr] sm:grid-cols-[auto_1fr_auto] items-center gap-2 rounded px-3 py-2 cursor-grab active:cursor-grabbing"
                  style={{
                    background: draggedActionId === item.id ? "rgba(34,211,238,0.08)" : "var(--mis-bg)",
                    border: draggedActionId === item.id ? "1px solid rgba(34,211,238,0.32)" : "1px solid var(--mis-border)",
                  }}
                  title={copy.dragToReorder}
                >
                  <GripVertical size={14} style={{ color: "var(--mis-muted)" }} />
                  <div className="min-w-0">
                    <div className="text-[11px] font-semibold truncate" style={{ color: "var(--mis-text)" }}>{item.action}</div>
                    <div className="text-[10px] truncate mt-0.5" style={{ color: "var(--mis-muted)" }}>
                      {copy.actionSource}: {item.source}
                    </div>
                    {remediationWorkflowStepId && (
                      <div className="flex flex-wrap items-center gap-1.5 mt-0.5 text-[10px]" style={{ color: "var(--mis-dim)" }}>
                        <span className="truncate">
                          {copy.remediationWorkflow}: {remediationWorkflowStepId} · {remediationWorkflowKind || "action"}
                          {remediationWorkflowPrerequisite ? ` · ${copy.prerequisiteStep}: ${remediationWorkflowPrerequisite}` : ""}
                        </span>
                        {(remediationWorkflowMutating || remediationWorkflowConfirmRequired) && (
                          <StatusBadge status="attention" label={remediationWorkflowConfirmRequired ? "confirm" : "write"} />
                        )}
                      </div>
                    )}
                    {"verifyAction" in item && item.verifyAction && (
                      <div className="flex items-center gap-1.5 mt-0.5 min-w-0">
                        <div className="text-[10px] truncate" style={{ color: "var(--mis-cyan)" }}>
                          {copy.verifyAfterAction}: {item.verifyAction}
                        </div>
                        <button
                          onClick={() => void copyIntakeCommand(item.verifyAction)}
                          className="inline-flex items-center gap-1 text-[9px] px-1.5 py-0.5 rounded shrink-0"
                          style={{ color: "var(--mis-cyan)", background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}
                          title={item.verifyAction}
                        >
                          <Copy size={9} />
                          {copiedIntakeCommand === item.verifyAction ? copy.copiedCommand : copy.copyCommand}
                        </button>
                      </div>
                    )}
                    {queueReceiptStatus && queueReceiptStatus !== "missing" && (
                      <div className="text-[10px] mt-0.5 truncate" style={{ color: queueReceiptStatus === "verified" ? "var(--mis-success)" : "var(--mis-warning)" }}>
                        {copy.receiptProof}: {queueReceiptStatus} · {queueReceiptHash}
                      </div>
                    )}
                    {queueReceiptEvaluationStatus && (
                      <div className="text-[10px] mt-0.5 truncate" style={{ color: queueReceiptEvaluationStatus === "pass" ? "var(--mis-success)" : "#F87171" }}>
                        {copy.receiptEvaluation}: {queueReceiptEvaluationStatus}
                      </div>
                    )}
                    {queueControlReadbackVisible && (
                      <div className="flex flex-wrap items-center gap-1.5 mt-0.5 text-[10px]" style={{ color: "var(--mis-muted)" }}>
                        <span className="truncate">
                          {copy.controlReadback}: {copy.controlBefore} {String(queueControlBefore.selected_gate || "—")}/{String(queueControlBefore.selected_status || queueControlBefore.status || "—")}
                        </span>
                        <span className="truncate">
                          {copy.controlAfter} {String(queueControlAfter.selected_gate || "—")}/{String(queueControlAfter.selected_status || queueControlAfter.status || "—")}
                        </span>
                        <span className="truncate">
                          {copy.controlSelfCheck} {String(queueControlSelfCheck.selected_gate || "—")}/{String(queueControlSelfCheck.selected_status || queueControlSelfCheck.status || "—")}
                        </span>
                        {queueControlCacheProof && <StatusBadge status={queueControlCacheProof === "bypass" ? "pass" : "attention"} label={`${copy.cacheRefresh}: ${queueControlCacheProof}`} />}
                        {queueControlReadbackHash && <StatusBadge status="pass" label={`${copy.tamperChain}: ${queueControlReadbackHash}`} />}
                      </div>
                    )}
                    {queueNeedsReceipt && (
                      <div className="text-[10px] mt-0.5 truncate" style={{ color: "var(--mis-warning)" }}>
                        {copy.receiptNeeded}: {verifyAction || item.action}
                      </div>
                    )}
                    {(receiptRecordCommand || receiptVerifyRecordCommand) && (
                      <div className="flex flex-wrap items-center gap-1.5 mt-1">
                        {receiptRecordCommand && (
                          <button
                            onClick={() => void copyIntakeCommand(receiptRecordCommand)}
                            className="inline-flex items-center gap-1 text-[9px] px-1.5 py-0.5 rounded"
                            style={{ color: "var(--mis-cyan)", background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}
                            title={receiptRecordCommand}
                          >
                            <Copy size={9} />
                            {copiedIntakeCommand === receiptRecordCommand ? copy.copiedCommand : copy.copyReceiptCommand}
                          </button>
                        )}
                        {receiptVerifyRecordCommand && (
                          <button
                            onClick={() => void copyIntakeCommand(receiptVerifyRecordCommand)}
                            className="inline-flex items-center gap-1 text-[9px] px-1.5 py-0.5 rounded"
                            style={{ color: "var(--mis-success)", background: "rgba(45,212,191,0.08)", border: "1px solid rgba(45,212,191,0.18)" }}
                            title={receiptVerifyRecordCommand}
                          >
                            <Copy size={9} />
                            {copiedIntakeCommand === receiptVerifyRecordCommand ? copy.copiedCommand : copy.copyVerifyReceiptCommand}
                          </button>
                        )}
                      </div>
                    )}
                  </div>
                  <div className="col-span-2 sm:col-span-1 flex items-center gap-1.5 justify-end">
                    {closeGapDetails && (
                      <button
                        onClick={() => void closeEvidenceGapFromQueue(item)}
                        disabled={Boolean(dispatching)}
                        className="inline-flex items-center gap-1 text-[10px] px-2 h-6 rounded disabled:opacity-50"
                        style={{ background: "rgba(45,212,191,0.10)", color: "var(--mis-success)", border: "1px solid rgba(45,212,191,0.20)" }}
                        title={copy.closeEvidenceGap}
                      >
                        {closeGapBusy ? <RefreshCw size={10} /> : <CheckCircle2 size={10} />}
                        {closeGapBusy ? copy.closingEvidenceGap : copy.closeEvidenceGap}
                      </button>
                    )}
                    <button
                      onClick={() => void recordActionQueueReceipt(item, "recorded")}
                      disabled={Boolean(receiptAction)}
                      className="inline-flex items-center gap-1 text-[10px] px-2 h-6 rounded disabled:opacity-50"
                      style={{ background: "rgba(34,211,238,0.08)", color: "var(--mis-cyan)", border: "1px solid rgba(34,211,238,0.20)" }}
                      title={copy.recordActionReceipt}
                    >
                      {recordBusy ? <RefreshCw size={10} /> : <Activity size={10} />}
                      {recordBusy ? copy.recordingReceipt : copy.recordActionReceipt}
                    </button>
                    {verifyAction && (
                      <button
                        onClick={() => void recordActionQueueReceipt(item, "verified")}
                        disabled={Boolean(receiptAction)}
                        className="inline-flex items-center gap-1 text-[10px] px-2 h-6 rounded disabled:opacity-50"
                        style={{ background: "rgba(45,212,191,0.10)", color: "var(--mis-success)", border: "1px solid rgba(45,212,191,0.20)" }}
                        title={copy.recordVerifyReceipt}
                      >
                        {verifyBusy ? <RefreshCw size={10} /> : <CheckCircle2 size={10} />}
                        {verifyBusy ? copy.recordingReceipt : copy.recordVerifyReceipt}
                      </button>
                    )}
                    <StatusBadge status={item.status} />
                    <button
                      onClick={() => nudgeActionQueueItem(item.id, -1)}
                      disabled={index === 0}
                      className="text-[10px] w-6 h-6 rounded disabled:opacity-30"
                      style={{ color: "var(--mis-dim)", background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}
                      aria-label={copy.moveUp}
                      title={copy.moveUp}
                    >
                      ↑
                    </button>
                    <button
                      onClick={() => nudgeActionQueueItem(item.id, 1)}
                      disabled={index === visibleActionQueue.length - 1}
                      className="text-[10px] w-6 h-6 rounded disabled:opacity-30"
                      style={{ color: "var(--mis-dim)", background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}
                      aria-label={copy.moveDown}
                      title={copy.moveDown}
                    >
                      ↓
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
          <div className="mt-3 pt-3" style={{ borderTop: "1px solid var(--mis-border)" }}>
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="text-[11px] font-semibold" style={{ color: "var(--mis-text)" }}>{copy.dispatchEvidenceTitle}</div>
                <div className="text-[10px] mt-1 max-w-3xl" style={{ color: "var(--mis-dim)" }}>{copy.dispatchEvidenceSummary}</div>
              </div>
              <StatusBadge status={(operatorPlanSummary?.dispatch_evidence_verified_manifests || 0) > 0 ? "pass" : "idle"} label={`${operatorPlanSummary?.dispatch_evidence_ready || 0}/${operatorPlanSummary?.dispatch_evidence_proofs || 0}`} />
            </div>
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-2 mt-2">
              {dispatchEvidenceActions.length === 0 && (
                <div className="text-[10px] rounded px-3 py-2" style={{ color: "var(--mis-muted)", background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                  {copy.noRecommendedActions}
                </div>
              )}
              {dispatchEvidenceActions.map((action) => {
                const evidence = action.evidence || {};
                const counts = (evidence.evidence_counts || {}) as Record<string, unknown>;
                return (
                  <div key={action.action_id} className="rounded px-3 py-2" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                    <div className="flex items-center justify-between gap-2">
                      <div className="min-w-0">
                        <div className="text-[10px] font-semibold truncate" style={{ color: "var(--mis-text)" }}>{action.title}</div>
                        <div className="text-[9px] mt-0.5 truncate" style={{ color: "var(--mis-muted)" }}>
                          {copy.runId}: {String(evidence.run_id || "—")} · {copy.planEvidence}: {String(evidence.manifest_id || "—")}
                        </div>
                      </div>
                      <StatusBadge status={action.severity} />
                    </div>
                    <div className="flex flex-wrap items-center gap-1.5 mt-2">
                      {["tool_calls", "evaluations", "artifacts", "plan_evidence_manifests"].map((key) => (
                        <span key={key} className="text-[9px] px-2 py-0.5 rounded" style={{ color: "var(--mis-muted)", background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
                          {key}: {String(counts[key] ?? 0)}
                        </span>
                      ))}
                      {evidence.run_id && (
                        <Link to={`/admin/runs/${String(evidence.run_id)}`} className="text-[9px] px-2 py-0.5 rounded" style={{ background: "rgba(45,212,191,0.10)", color: "var(--mis-success)", border: "1px solid rgba(45,212,191,0.18)" }}>
                          {copy.openRun}
                        </Link>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
          <div className="mt-3 pt-3" style={{ borderTop: "1px solid var(--mis-border)" }}>
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="text-[11px] font-semibold" style={{ color: "var(--mis-text)" }}>{copy.evidenceClosureLedger}</div>
                <div className="text-[10px] mt-1 max-w-3xl" style={{ color: "var(--mis-dim)" }}>{copy.evidenceClosureSummary}</div>
              </div>
              <StatusBadge status={(operatorPlanSummary?.closed_evidence_gap_runs || 0) > 0 ? "pass" : (operatorPlanSummary?.evidence_gap_closure_ready_runs || 0) > 0 ? "attention" : "ready"} label={`${operatorPlanSummary?.closed_evidence_gap_runs || 0}/${operatorPlanSummary?.evidence_gap_closure_ready_runs || 0}`} />
            </div>
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-2 mt-2">
              {evidenceClosureRows.length === 0 && (
                <div className="text-[10px] rounded px-3 py-2" style={{ color: "var(--mis-muted)", background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                  {copy.noClosureRows}
                </div>
              )}
              {evidenceClosureRows.map((gap) => {
                const canClose = gap.gap_decision_status !== "closed" && isCloseEvidenceGapCommand(gap.command || "");
                const canReopen = gap.gap_decision_status === "closed";
                const closeBusy = dispatching === `close-gap:${gap.run_id}`;
                const reopenBusy = dispatching === `reopen-gap:${gap.run_id}`;
                return (
                  <div key={gap.run_id} className="rounded px-3 py-2" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                    <div className="flex items-center justify-between gap-2">
                      <div className="min-w-0">
                        <div className="text-[11px] font-semibold truncate" style={{ color: "var(--mis-text)" }}>{gap.task_title || gap.run_id}</div>
                        <div className="text-[10px] truncate mt-0.5" style={{ color: "var(--mis-muted)" }}>{gap.run_id}</div>
                      </div>
                      <StatusBadge status={gap.gap_decision_status === "closed" ? "pass" : canClose ? "attention" : gap.severity} />
                    </div>
                    <div className="grid grid-cols-2 gap-2 mt-2">
                      <div className="text-[10px] truncate" style={{ color: "var(--mis-muted)" }}>
                        {copy.remediationState}: <span style={{ color: "var(--mis-text)" }}>{gap.remediation_synthesis_status || gap.remediation_status || "—"}</span>
                      </div>
                      <div className="text-[10px] truncate" style={{ color: "var(--mis-muted)" }}>
                        {copy.closureDecision}: <span style={{ color: "var(--mis-text)" }}>{gap.gap_decision_type || gap.gap_decision_status || "open"}</span>
                      </div>
                    </div>
                    <div className="flex items-center justify-between gap-2 mt-2">
                      <div className="text-[10px] truncate" style={{ color: "var(--mis-dim)" }}>{gap.next_action || gap.command}</div>
                      <div className="flex items-center gap-1.5 shrink-0">
                        {canClose && (
                          <button
                            onClick={() => void submitEvidenceGapDecision(evidenceGapDecisionDetails(gap))}
                            disabled={Boolean(dispatching)}
                            className="inline-flex items-center gap-1 text-[10px] px-2 h-6 rounded disabled:opacity-50"
                            style={{ background: "rgba(45,212,191,0.10)", color: "var(--mis-success)", border: "1px solid rgba(45,212,191,0.20)" }}
                            title={copy.closeEvidenceGap}
                          >
                            {closeBusy ? <RefreshCw size={10} /> : <CheckCircle2 size={10} />}
                            {closeBusy ? copy.closingEvidenceGap : copy.closeEvidenceGap}
                          </button>
                        )}
                        {canReopen && (
                          <button
                            onClick={() => void submitEvidenceGapDecision(evidenceGapDecisionDetails(gap, "reopen"))}
                            disabled={Boolean(dispatching)}
                            className="inline-flex items-center gap-1 text-[10px] px-2 h-6 rounded disabled:opacity-50"
                            style={{ background: "rgba(245,158,11,0.10)", color: "var(--mis-warning)", border: "1px solid rgba(245,158,11,0.20)" }}
                            title={copy.reopenEvidenceGap}
                          >
                            {reopenBusy ? <RefreshCw size={10} /> : <RotateCw size={10} />}
                            {reopenBusy ? copy.reopeningEvidenceGap : copy.reopenEvidenceGap}
                          </button>
                        )}
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>

        <div className="rounded-lg p-3 mt-4" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <ShieldCheck size={13} style={{ color: (taskIntakeSummary?.blocked_for_intake || 0) > 0 ? "var(--mis-warning)" : "var(--mis-success)" }} />
                <div className="text-[11px] font-semibold" style={{ color: "var(--mis-text)" }}>{copy.taskIntakeTitle}</div>
                <StatusBadge status={taskIntakeChecklist?.status || "unknown"} />
              </div>
              <p className="text-[10px] mt-1 max-w-3xl" style={{ color: "var(--mis-dim)" }}>{copy.taskIntakeSummary}</p>
            </div>
            <StatusBadge status={(taskIntakeSummary?.blocked_for_intake || 0) > 0 ? "blocked" : (taskIntakeSummary?.attention_for_intake || 0) > 0 ? "attention" : "pass"} label={`${taskIntakeSummary?.ready_for_intake || 0}/${taskIntakeSummary?.tasks_checked || 0}`} />
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 mt-3">
            {[
              { label: copy.intakeReady, value: taskIntakeSummary?.ready_for_intake ?? 0, status: "pass" },
              { label: copy.intakeBlocked, value: taskIntakeSummary?.blocked_for_intake ?? 0, status: (taskIntakeSummary?.blocked_for_intake || 0) > 0 ? "blocked" : "pass" },
              { label: copy.intakeAttention, value: taskIntakeSummary?.attention_for_intake ?? 0, status: (taskIntakeSummary?.attention_for_intake || 0) > 0 ? "attention" : "pass" },
            ].map((item) => (
              <div key={item.label} className="rounded px-2 py-1" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                <div className="text-[9px]" style={{ color: "var(--mis-muted)" }}>{item.label}</div>
                <div className="flex items-center justify-between gap-2 mt-0.5">
                  <div className="text-[10px] font-semibold truncate" style={{ color: item.status === "blocked" ? "#F87171" : "var(--mis-text)" }}>{item.value}</div>
                  <StatusBadge status={item.status} />
                </div>
              </div>
            ))}
          </div>
          <div className="grid grid-cols-1 xl:grid-cols-2 gap-2 mt-3">
            {taskIntakeRows.length === 0 && (
              <div className="text-[10px] rounded px-3 py-2" style={{ color: "var(--mis-muted)", background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                {copy.noIntakeRows}
              </div>
            )}
            {taskIntakeRows.map((item) => (
              <div key={item.task_id} className="rounded px-3 py-2" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="text-[11px] font-semibold truncate" style={{ color: "var(--mis-text)" }}>{item.title}</div>
                    <div className="text-[10px] truncate mt-0.5" style={{ color: "var(--mis-muted)" }}>{item.task_id}</div>
                  </div>
                  <StatusBadge status={item.severity} />
                </div>
                <div className="grid grid-cols-2 gap-2 mt-2">
                  <div className="text-[10px] truncate" style={{ color: "var(--mis-muted)" }}>
                    {copy.assignedAgents}: <span style={{ color: "var(--mis-text)" }}>{item.assigned_agent_ids.join(", ") || "—"}</span>
                  </div>
                  <div className="text-[10px] truncate" style={{ color: "var(--mis-muted)" }}>
                    Agent Plan: <span style={{ color: "var(--mis-text)" }}>{item.plan_verified ? "verified" : item.plan_id || "missing"}</span>
                  </div>
                  <div className="text-[10px] truncate" style={{ color: "var(--mis-muted)" }}>
                    {copy.planReferences}: <span style={{ color: "var(--mis-text)" }}>{item.referenced_specs}/{item.referenced_memories}/{item.referenced_bases}</span>
                  </div>
                  <div className="text-[10px] truncate" style={{ color: "var(--mis-muted)" }}>
                    Risk: <span style={{ color: "var(--mis-text)" }}>{item.risk_level || "—"}</span>
                  </div>
                </div>
                <div className="flex flex-wrap gap-1.5 mt-2">
                  {item.gates.slice(0, 6).map((gate) => (
                    <StatusBadge key={gate.id} status={gate.ok ? "pass" : gate.status} label={gate.id} />
                  ))}
                </div>
                <div className="text-[10px] truncate mt-2" style={{ color: "var(--mis-dim)" }}>{item.next_action || item.command}</div>
              </div>
            ))}
          </div>
        </div>

        <div className="rounded-lg p-3 mt-4" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <ShieldCheck size={13} style={{ color: localSafetyOk ? "var(--mis-success)" : "var(--mis-warning)" }} />
                <div className="text-[11px] font-semibold" style={{ color: "var(--mis-text)" }}>{copy.localReadinessTitle}</div>
                <StatusBadge status={localReadiness?.status || "unknown"} />
              </div>
              <p className="text-[10px] mt-1 max-w-3xl" style={{ color: "var(--mis-dim)" }}>{copy.localReadinessSummary}</p>
              {localReadiness?.contract && (
                <p className="text-[10px] mt-1 truncate max-w-3xl" style={{ color: "var(--mis-muted)" }}>
                  {copy.contract}: {localReadiness.contract}
                </p>
              )}
            </div>
            <StatusBadge status={localReadiness?.ok ? "pass" : "blocked"} label={`${localReadinessReadyGates}/${localReadinessGates.length || 0}`} />
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-6 gap-2 mt-3">
            {[
              { label: copy.localReadinessOverall, value: localReadiness?.status || "—", status: localReadiness?.status || "unknown" },
              { label: copy.evidenceChains, value: localEvidence?.closed_loop_runs ?? "—", status: localEvidence?.has_task_run_tool_eval_audit_artifact_chain ? "pass" : "attention" },
              { label: copy.memoryApprovalCounts, value: `${localEvidence?.memories ?? 0}/${localEvidence?.approvals ?? 0}`, status: localEvidence?.has_memory_or_knowledge && localEvidence?.has_approval_flow ? "pass" : "attention" },
              { label: copy.synthesisLoop, value: `${localEvidence?.commander_synthesis_promoted_deliveries ?? 0}/${localEvidence?.commander_synthesis_pending_reviews ?? 0}`, status: (localEvidence?.commander_synthesis_promoted_deliveries ?? 0) > 0 ? "pass" : "attention" },
              { label: copy.recommendedAdapter, value: localRecommendedAdapter, status: localRecommendedAdapter ? "ready" : "unknown" },
              { label: copy.safetyProof, value: localSafetyOk ? copy.statusClear : copy.statusAttention, status: localSafetyOk ? "pass" : "blocked" },
            ].map((item) => (
              <div key={item.label} className="rounded px-2 py-1" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                <div className="text-[9px]" style={{ color: "var(--mis-muted)" }}>{item.label}</div>
                <div className="flex items-center justify-between gap-2 mt-0.5">
                  <div className="text-[10px] font-semibold truncate" style={{ color: item.status === "blocked" ? "#F87171" : "var(--mis-text)" }}>{item.value}</div>
                  <StatusBadge status={item.status} />
                </div>
              </div>
            ))}
          </div>

          <div className="grid grid-cols-1 md:grid-cols-[0.9fr_1.1fr] gap-3 mt-3">
            <div className="flex flex-wrap gap-1.5">
              <StatusBadge status={localReadiness?.token_omitted ? "pass" : "fail"} label={`${copy.tokenOmittedProof}: ${localReadiness?.token_omitted ? copy.yes : copy.no}`} />
              <StatusBadge status={localReadiness?.live_execution_performed === false ? "pass" : "fail"} label={`${copy.liveExecutionProof}: ${localReadiness?.live_execution_performed === false ? copy.yes : copy.no}`} />
            </div>
            <div className="flex flex-wrap gap-1.5 md:justify-end">
              {(localReadinessActions.length > 0 ? localReadinessActions : [copy.noRecommendedActions]).slice(0, 3).map((action) => (
                <span key={action} className="text-[10px] px-2 py-1 rounded truncate max-w-full" style={{ color: "var(--mis-cyan)", background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                  {copy.nextAction}: {action}
                </span>
              ))}
            </div>
          </div>

          {localServiceControlStep && (
            <div className="rounded p-2 mt-3" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
              {(() => {
                const receiptState = localServiceControlStep.receipt_state || {};
                const receiptStatus = String(receiptState.status || (localServiceControlStep.receipt_required ? "missing" : "not_required"));
                const receiptHash = String(receiptState.receipt_hash || receiptState.action_hash || receiptState.receipt_id || "").slice(0, 10);
                const serviceVerifyBusy = receiptAction === `local-run-path-receipt:verified:${localServiceControlStep.step_id}`;
                const serviceReadbackBusy = receiptAction === `local-run-path-readback:${localServiceControlStep.step_id}`;
                const serviceReceiptVerified = Boolean(receiptState.verified);
                const serviceReadbackAttached = Boolean(receiptState.control_readback_attached || receiptState.control_readback_id);
                return (
                  <>
              <div className="flex flex-col lg:flex-row lg:items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <Power size={12} style={{ color: "var(--mis-cyan)" }} />
                    <div className="text-[10px] font-semibold" style={{ color: "var(--mis-text)" }}>{copy.serviceControlPreviewTitle}</div>
                    <StatusBadge status={localServiceControlStep.status || "preview"} />
                    <StatusBadge status={localServiceControlStep.server_executes_shell === false ? "pass" : "blocked"} label={localServiceControlStep.server_executes_shell === false ? "no server shell" : "server shell"} />
                    <StatusBadge status={localServiceControlStep.writes_ledger ? "blocked" : "pass"} label={localServiceControlStep.writes_ledger ? "ledger write" : "no ledger write"} />
                    <StatusBadge status={serviceReceiptVerified ? "pass" : "attention"} label={`${copy.receiptProof}: ${receiptStatus}${receiptHash ? ` · ${receiptHash}` : ""}`} />
                    <StatusBadge status={serviceReadbackAttached ? "pass" : "attention"} label={`${copy.controlReadback}: ${serviceReadbackAttached ? copy.yes : copy.no}`} />
                  </div>
                  <div className="text-[9px] mt-1 line-clamp-2" style={{ color: "var(--mis-dim)" }}>
                    {localServiceControlStep.detail || copy.serviceControlPreviewSummary}
                  </div>
                </div>
                <StatusBadge status={localServiceControlStep.confirm_required ? "attention" : "pass"} label={localServiceControlStep.confirm_required ? copy.confirmRequired : "preview-only"} />
              </div>
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-2 mt-2">
                <button
                  type="button"
                  onClick={() => void copyIntakeCommand(localServiceControlStep.command)}
                  className="flex items-center gap-1 rounded px-2 py-1 text-left min-w-0"
                  style={{ color: "var(--mis-text)", background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}
                  title={localServiceControlStep.command}
                >
                  <Copy size={9} />
                  <span className="text-[9px] font-semibold shrink-0">{copy.servicePreviewCommand}</span>
                  <span className="text-[8px] truncate" style={{ color: "var(--mis-muted)" }}>{copiedIntakeCommand === localServiceControlStep.command ? copy.copiedCommand : localServiceControlStep.command}</span>
                </button>
                {localServiceControlStep.verify_command && (
                  <button
                    type="button"
                    onClick={() => void copyIntakeCommand(String(localServiceControlStep.verify_command))}
                    className="flex items-center gap-1 rounded px-2 py-1 text-left min-w-0"
                    style={{ color: "var(--mis-text)", background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}
                    title={String(localServiceControlStep.verify_command)}
                  >
                    <Copy size={9} />
                    <span className="text-[9px] font-semibold shrink-0">{copy.serviceCheckCommand}</span>
                    <span className="text-[8px] truncate" style={{ color: "var(--mis-muted)" }}>{copiedIntakeCommand === localServiceControlStep.verify_command ? copy.copiedCommand : localServiceControlStep.verify_command}</span>
                  </button>
                )}
                {localServiceControlStep.receipt_required && (
                  <>
                    {localServiceControlStep.receipt_verify_record_command && (
                      <button
                        type="button"
                        onClick={() => void copyIntakeCommand(String(localServiceControlStep.receipt_verify_record_command))}
                        className="flex items-center gap-1 rounded px-2 py-1 text-left min-w-0"
                        style={{ color: "var(--mis-warning)", background: "rgba(251,191,36,0.08)", border: "1px solid rgba(251,191,36,0.18)" }}
                        title={String(localServiceControlStep.receipt_verify_record_command)}
                      >
                        <Copy size={9} />
                        <span className="text-[9px] font-semibold shrink-0">{copy.copyVerifyReceiptCommand}</span>
                        <span className="text-[8px] truncate" style={{ color: "var(--mis-muted)" }}>{copiedIntakeCommand === localServiceControlStep.receipt_verify_record_command ? copy.copiedCommand : localServiceControlStep.receipt_verify_record_command}</span>
                      </button>
                    )}
                    <button
                      type="button"
                      onClick={() => void recordLocalRunPathReceipt(localServiceControlStep, "verified")}
                      disabled={Boolean(receiptAction)}
                      className="flex items-center gap-1 rounded px-2 py-1 text-left min-w-0 disabled:opacity-50"
                      style={{ color: "var(--mis-success)", background: "rgba(45,212,191,0.10)", border: "1px solid rgba(45,212,191,0.20)" }}
                      title={copy.recordVerifyReceipt}
                    >
                      {serviceVerifyBusy ? <RefreshCw size={9} /> : <CheckCircle2 size={9} />}
                      <span className="text-[9px] font-semibold shrink-0">{serviceVerifyBusy ? copy.recordingReceipt : copy.recordVerifyReceipt}</span>
                      <span className="text-[8px] truncate" style={{ color: "var(--mis-muted)" }}>{localServiceControlStep.receipt_command || copy.actionReceipts}</span>
                    </button>
                    {localServiceControlStep.control_readback_required && (
                      <button
                        type="button"
                        onClick={() => void recordLocalRunPathControlReadback(localServiceControlStep)}
                        disabled={Boolean(receiptAction)}
                        className="flex items-center gap-1 rounded px-2 py-1 text-left min-w-0 disabled:opacity-50"
                        style={{ color: "var(--mis-cyan)", background: "rgba(34,211,238,0.08)", border: "1px solid rgba(34,211,238,0.20)" }}
                        title={copy.controlReadback}
                      >
                        {serviceReadbackBusy ? <RefreshCw size={9} /> : <Activity size={9} />}
                        <span className="text-[9px] font-semibold shrink-0">{serviceReadbackBusy ? copy.recordingReceipt : copy.controlReadback}</span>
                        <span className="text-[8px] truncate" style={{ color: "var(--mis-muted)" }}>{localServiceControlStep.verify_command || copy.serviceCheckCommand}</span>
                      </button>
                    )}
                  </>
                )}
              </div>
                  </>
                );
              })()}
            </div>
          )}

          {localRunPath.length > 0 && (
            <div className="mt-3 rounded p-2" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <Terminal size={12} style={{ color: "var(--mis-cyan)" }} />
                    <div className="text-[10px] font-semibold" style={{ color: "var(--mis-text)" }}>{copy.localRunPathTitle}</div>
                  </div>
                  <p className="text-[9px] mt-1 max-w-3xl" style={{ color: "var(--mis-dim)" }}>{copy.localRunPathSummary}</p>
                </div>
                <StatusBadge status={localRunPath.some(step => step.status === "blocked" || step.status === "action_required") ? "attention" : "pass"} label={String(localRunPath.length)} />
              </div>
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-2 mt-2">
                {localRunPath.slice(0, 8).map((step) => (
                  <div key={step.step_id} className="rounded px-2 py-1.5 min-w-0" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
                    <div className="flex items-center justify-between gap-2">
                      <div className="min-w-0">
                        <div className="text-[9px] font-semibold truncate" style={{ color: "var(--mis-text)" }}>{step.label}</div>
                        <div className="text-[9px] truncate" style={{ color: "var(--mis-muted)" }}>{step.phase}{step.adapter ? ` · ${step.adapter}` : ""}</div>
                      </div>
                      <StatusBadge status={step.status} />
                    </div>
                    {step.detail && (
                      <div className="text-[9px] mt-1 line-clamp-2" style={{ color: "var(--mis-dim)" }}>{step.detail}</div>
                    )}
                    <div className="flex flex-wrap gap-1 mt-1.5">
                      <StatusBadge status={step.copy_only ? "pass" : "attention"} label="copy-only" />
                      <StatusBadge status={step.server_executes_shell === false ? "pass" : "blocked"} label="no server shell" />
                      {step.receipt_required && (
                        <StatusBadge
                          status={Boolean(step.receipt_state?.verified) ? "pass" : "attention"}
                          label={`${copy.receiptProof}: ${String(step.receipt_state?.status || "missing")}`}
                        />
                      )}
                      {step.control_readback_required && (
                        <StatusBadge
                          status={step.receipt_state?.control_readback_attached ? "pass" : "attention"}
                          label={`${copy.controlReadback}: ${step.receipt_state?.control_readback_attached ? copy.yes : copy.no}`}
                        />
                      )}
                      {step.confirm_required && <StatusBadge status="attention" label={copy.confirmRequired} />}
                      {step.live_execution && <StatusBadge status="attention" label="live" />}
                      {step.writes_ledger && <StatusBadge status="ready" label="ledger" />}
                    </div>
                    <div className="flex flex-wrap gap-1.5 mt-2">
                      <button
                        type="button"
                        onClick={() => void copyIntakeCommand(step.command)}
                        className="rounded px-2 py-1 text-[9px] font-semibold truncate max-w-full"
                        style={{ color: "var(--mis-bg)", background: "var(--mis-cyan)", border: "1px solid var(--mis-cyan)" }}
                      >
                        {copy.copyActionCommand}
                      </button>
                      {step.verify_command && (
                        <button
                          type="button"
                          onClick={() => void copyIntakeCommand(String(step.verify_command))}
                          className="rounded px-2 py-1 text-[9px] font-semibold truncate max-w-full"
                          style={{ color: "var(--mis-text)", background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}
                        >
                          {copy.verifyAfterAction}
                        </button>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        <div className="rounded-lg p-3 mt-4" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <Inbox size={13} style={{ color: integrationInboxSafe ? "var(--mis-success)" : "var(--mis-cyan)" }} />
                <div className="text-[11px] font-semibold" style={{ color: "var(--mis-text)" }}>{copy.integrationInboxTitle}</div>
                <StatusBadge status={integrationInbox?.status || "unknown"} />
              </div>
              <p className="text-[10px] mt-1 max-w-3xl" style={{ color: "var(--mis-dim)" }}>{copy.integrationInboxSummary}</p>
            </div>
            <StatusBadge status={(integrationInboxSummary?.blocked || 0) > 0 ? "blocked" : (integrationInboxSummary?.ready_for_review || 0) > 0 ? "attention" : "pass"} label={String(integrationInboxSummary?.total ?? 0)} />
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-5 gap-2 mt-3">
            {[
              { label: copy.readyForReview, value: integrationInboxSummary?.ready_for_review ?? 0, status: (integrationInboxSummary?.ready_for_review || 0) > 0 ? "ready" : "pass" },
              { label: copy.stillRunning, value: integrationInboxSummary?.still_running ?? 0, status: (integrationInboxSummary?.still_running || 0) > 0 ? "running" : "pass" },
              { label: copy.blockedItems, value: integrationInboxSummary?.blocked ?? 0, status: (integrationInboxSummary?.blocked || 0) > 0 ? "blocked" : "pass" },
              { label: copy.lateOrStale, value: integrationInboxSummary?.late_or_stale ?? 0, status: (integrationInboxSummary?.late_or_stale || 0) > 0 ? "stale" : "pass" },
              { label: copy.memoryReview, value: integrationInboxSummary?.needs_memory_review ?? 0, status: (integrationInboxSummary?.needs_memory_review || 0) > 0 ? "candidate" : "pass" },
            ].map((item) => (
              <div key={item.label} className="rounded px-2 py-1" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                <div className="text-[9px]" style={{ color: "var(--mis-muted)" }}>{item.label}</div>
                <div className="flex items-center justify-between gap-2 mt-0.5">
                  <div className="text-[10px] font-semibold truncate" style={{ color: item.status === "blocked" ? "#F87171" : "var(--mis-text)" }}>{item.value}</div>
                  <StatusBadge status={item.status} />
                </div>
              </div>
            ))}
          </div>

          <div className="mt-3 flex flex-wrap items-center gap-1.5">
            <span className="text-[10px] mr-1" style={{ color: "var(--mis-muted)" }}>{copy.inboxFilter}</span>
            {integrationInboxFilters.map((filter) => {
              const active = integrationInboxBucket === filter.bucket;
              return (
                <button
                  key={filter.bucket}
                  type="button"
                  onClick={() => setIntegrationInboxBucket(filter.bucket)}
                  className="rounded px-2 py-1 text-[10px] font-semibold"
                  style={{
                    color: active ? "var(--mis-bg)" : "var(--mis-text)",
                    background: active ? "var(--mis-cyan)" : "var(--mis-bg)",
                    border: `1px solid ${active ? "var(--mis-cyan)" : "var(--mis-border)"}`,
                  }}
                >
                  {filter.label} · {filter.count}
                </button>
              );
            })}
          </div>

          <div className="grid grid-cols-1 xl:grid-cols-[1.35fr_0.85fr] gap-3 mt-3">
            <div className="space-y-2 min-w-0">
              {integrationInboxItems.length === 0 && (
                <div className="text-[11px] rounded px-3 py-2" style={{ color: "var(--mis-muted)", background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                  {copy.inboxEmpty}
                </div>
              )}
              {integrationInboxItems.slice(0, 5).map((item) => {
                const primaryRef = item.task_id || item.run_id || item.job_id || item.artifact_id || item.item_id;
                const integrationDecision = item.integration_decision;
                return (
                  <div key={item.item_id || primaryRef} className="grid grid-cols-1 sm:grid-cols-[1fr_auto] gap-3 rounded px-3 py-2" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                    <div className="min-w-0">
                      <div className="flex items-center gap-2 min-w-0">
                        <div className="text-[11px] font-semibold truncate" style={{ color: "var(--mis-text)" }}>{item.title}</div>
                        <StatusBadge status={item.status} />
                        {integrationDecision && <StatusBadge status={integrationDecision.status || "attention"} label={integrationDecision.decision} />}
                      </div>
                      <div className="flex flex-wrap gap-x-3 gap-y-1 mt-1 text-[10px]" style={{ color: "var(--mis-muted)" }}>
                        <span>{copy.itemBucket}: {item.bucket || "—"}</span>
                        <span>{copy.itemAge}: {formatAge(item.age_sec)}</span>
                        <span>{copy.itemOwner}: {item.owner_agent_id || item.agent_id || "—"}</span>
                      </div>
                      {integrationDecision && (
                        <div className="mt-1.5 rounded px-2 py-1" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
                          <div className="flex flex-wrap gap-1.5">
                            <StatusBadge status={integrationDecision.safe_to_auto_apply ? "fail" : "pass"} label={`${copy.integrationAutoApply}: ${integrationDecision.safe_to_auto_apply ? copy.yes : copy.no}`} />
                            <StatusBadge status={integrationDecision.ledger_decision_required ? "attention" : "pass"} label={`${copy.integrationLedgerDecision}: ${integrationDecision.ledger_decision_required ? copy.yes : copy.no}`} />
                            <StatusBadge status={integrationDecision.can_advance_without_waiting ? "pass" : "attention"} label={copy.canAdvanceWithoutWaiting} />
                          </div>
                          <div className="text-[10px] line-clamp-2 mt-1" style={{ color: "var(--mis-dim)" }}>
                            {copy.integrationReason}: {integrationDecision.reason || "—"}
                          </div>
                        </div>
                      )}
                      {item.recommended_action && (
                        <div className="text-[10px] truncate mt-1" style={{ color: "var(--mis-cyan)" }}>
                          {copy.nextAction}: {item.recommended_action}
                        </div>
                      )}
                      <div className="flex flex-wrap gap-1.5 mt-1">
                        {item.task_id && <Link className="text-[10px]" style={{ color: "var(--mis-cyan)" }} to={`/admin/tasks/${item.task_id}`}>{copy.taskId}: {item.task_id}</Link>}
                        {item.run_id && <Link className="text-[10px]" style={{ color: "var(--mis-cyan)" }} to={`/admin/runs/${item.run_id}`}>{copy.runId}: {item.run_id}</Link>}
                        {item.job_id && <span className="text-[10px]" style={{ color: "var(--mis-muted)" }}>{copy.jobId}: {item.job_id}</span>}
                        {item.artifact_id && <span className="text-[10px]" style={{ color: "var(--mis-muted)" }}>{copy.artifactId}: {item.artifact_id}</span>}
                      </div>
                    </div>
                    <StatusBadge status={item.bucket || "unknown"} label={item.bucket || "—"} />
                  </div>
                );
              })}
            </div>

            <div className="space-y-2 min-w-0">
              <div className="flex flex-wrap gap-1.5">
                <StatusBadge status={integrationInboxSafety?.read_only ? "pass" : "fail"} label={`${copy.readOnlyProof}: ${integrationInboxSafety?.read_only ? copy.yes : copy.no}`} />
                <StatusBadge status={integrationInboxSafety?.ledger_mutated === false ? "pass" : "fail"} label={`${copy.ledgerMutationProof}: ${integrationInboxSafety?.ledger_mutated === false ? copy.yes : copy.no}`} />
                <StatusBadge status={integrationInboxSafety?.raw_prompt_omitted ? "pass" : "fail"} label={`${copy.rawPromptProof}: ${integrationInboxSafety?.raw_prompt_omitted ? copy.yes : copy.no}`} />
              </div>
              {(integrationInboxActions.length > 0 ? integrationInboxActions : [copy.noRecommendedActions]).slice(0, 4).map((action) => (
                <div key={action} className="text-[11px] rounded px-3 py-2 truncate" style={{ color: "var(--mis-cyan)", background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                  {action}
                </div>
              ))}
            </div>
          </div>
        </div>

        <div className="grid grid-cols-1 xl:grid-cols-[1.35fr_1fr] gap-4 mt-4">
          <div className="rounded-lg p-3 min-w-0" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
            <div className="flex items-center justify-between gap-3">
              <div className="text-[11px] font-semibold" style={{ color: "var(--mis-text)" }}>{copy.healthGates}</div>
              <StatusBadge status={fleetHealth?.overall || "unknown"} label={String(fleetGates.length)} />
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-2 mt-3">
              {fleetGates.slice(0, 6).map((gate) => (
                <div key={`${gate.id}-${gate.status}`} className="rounded px-3 py-2" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                  <div className="flex items-center justify-between gap-2">
                    <div className="text-[10px] font-semibold truncate" style={{ color: "var(--mis-text)" }}>{gate.id}</div>
                    <StatusBadge status={gate.status} />
                  </div>
                  <div className="text-[10px] mt-1 line-clamp-2" style={{ color: "var(--mis-dim)" }}>{gate.summary}</div>
                  {gate.action && (
                    <div className="text-[10px] mt-1 truncate" style={{ color: gate.status === "fail" || gate.status === "warn" ? "var(--mis-warning)" : "var(--mis-muted)" }}>
                      {gate.action}
                    </div>
                  )}
                </div>
              ))}
              {fleetGates.length === 0 && (
                <div className="md:col-span-2 text-[11px] rounded px-3 py-2" style={{ color: "var(--mis-muted)", background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                  {copy.noRecommendedActions}
                </div>
              )}
            </div>
          </div>

          <div className="rounded-lg p-3 min-w-0" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
            <div className="flex items-center justify-between gap-3">
              <div className="text-[11px] font-semibold" style={{ color: "var(--mis-text)" }}>{copy.recommendedActions}</div>
              <StatusBadge status={recommendedActions.length > 0 ? "attention" : "pass"} label={String(recommendedActions.length)} />
            </div>
            <div className="space-y-2 mt-3">
              {recommendedActions.length === 0 && (
                <div className="text-[11px] rounded px-3 py-2" style={{ color: "var(--mis-muted)", background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                  {copy.noRecommendedActions}
                </div>
              )}
              {recommendedActions.slice(0, 5).map((action) => (
                <div key={action} className="text-[11px] rounded px-3 py-2 truncate" style={{ color: "var(--mis-cyan)", background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                  {action}
                </div>
              ))}
            </div>
          </div>
        </div>

        <div className="grid grid-cols-1 xl:grid-cols-[1.1fr_0.9fr] gap-4 mt-4">
          <div className="rounded-lg p-3 min-w-0" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
            <div className="flex items-center justify-between gap-3">
              <div className="text-[11px] font-semibold" style={{ color: "var(--mis-text)" }}>{copy.remoteWorkersTitle}</div>
              <div className="flex flex-wrap gap-1.5 justify-end">
                <StatusBadge status="fresh" label={`${copy.heartbeatFresh}: ${workerStatus?.fresh_remote_enrollments ?? 0}`} />
                <StatusBadge status="stale" label={`${copy.heartbeatStale}: ${workerStatus?.stale_remote_enrollments ?? 0}`} />
                <StatusBadge status="never_seen" label={`${copy.heartbeatNeverSeen}: ${workerStatus?.never_seen_remote_enrollments ?? 0}`} />
              </div>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 gap-2 mt-3">
              {[
                { label: copy.totalEnrollments, value: workerStatus?.total_remote_enrollments ?? 0 },
                { label: copy.activeEnrollments, value: workerStatus?.active_remote_enrollments ?? 0 },
                { label: copy.activeSessions, value: workerStatus?.active_remote_sessions ?? 0 },
                { label: copy.gatewayWorkspace, value: gatewayStatus?.auth.workspace_id || "local-demo" },
              ].map((item) => (
                <div key={item.label} className="rounded px-2 py-1" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                  <div className="text-[9px]" style={{ color: "var(--mis-muted)" }}>{item.label}</div>
                  <div className="text-[10px] font-semibold truncate" style={{ color: "var(--mis-text)" }}>{item.value}</div>
                </div>
              ))}
            </div>
            <div className="space-y-2 mt-3">
              {remoteWorkers.length === 0 && (
                <div className="text-[11px] rounded px-3 py-2" style={{ color: "var(--mis-muted)", background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                  {copy.noRemoteWorkers}
                </div>
              )}
              {remoteWorkers.slice(0, 4).map((worker) => (
                <div key={`${worker.agent_id}-${worker.token_ref}`} className="grid grid-cols-1 md:grid-cols-[1fr_0.55fr_0.55fr_0.7fr] gap-2 items-start md:items-center rounded px-3 py-2" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                  <div className="min-w-0">
                    <div className="text-[11px] font-semibold truncate" style={{ color: "var(--mis-text)" }}>{worker.agent_name || worker.agent_id || "remote worker"}</div>
                    <div className="text-[10px] truncate" style={{ color: "var(--mis-muted)" }}>{worker.agent_id || "—"} · {worker.runtime_type || "external"}</div>
                  </div>
                  <StatusBadge status={worker.heartbeat_state || "unknown"} />
                  <div className="text-[10px] truncate" style={{ color: "var(--mis-dim)" }}>{copy.activeSessions}: {worker.active_session_count ?? 0}</div>
                  <div className="text-[10px] truncate" style={{ color: "var(--mis-dim)" }}>{worker.last_heartbeat_at || worker.last_used_at || "—"}</div>
                </div>
              ))}
            </div>
          </div>

          <div className="rounded-lg p-3 min-w-0" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
            <div className="flex items-center justify-between gap-3">
              <div className="text-[11px] font-semibold" style={{ color: "var(--mis-text)" }}>{copy.recentRemoteSessionsTitle}</div>
              <StatusBadge status={recentRemoteSessions.length > 0 ? "active" : "idle"} label={String(recentRemoteSessions.length)} />
            </div>
            <div className="space-y-2 mt-3">
              {recentRemoteSessions.length === 0 && (
                <div className="text-[11px] rounded px-3 py-2" style={{ color: "var(--mis-muted)", background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                  {copy.noSessions}
                </div>
              )}
              {recentRemoteSessions.slice(0, 5).map((session) => (
                <div key={`${session.agent_id}-${session.session_ref}`} className="rounded px-3 py-2" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                  <div className="flex items-center justify-between gap-2">
                    <div className="text-[11px] font-semibold truncate" style={{ color: "var(--mis-text)" }}>{session.agent_id || "—"}</div>
                    <StatusBadge status={session.session_state || session.status || "unknown"} />
                  </div>
                  <div className="text-[10px] mt-1 truncate" style={{ color: "var(--mis-muted)" }}>
                    {copy.sessionId}: {session.session_ref || "—"} · {copy.expires}: {session.expires_at || "—"}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>

      <div
        className="rounded-xl p-4"
        style={{ background: "var(--mis-surface)", border: "1px solid var(--mis-border)" }}
      >
        <div className="flex flex-col lg:flex-row lg:items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-2">
              <Bot size={14} style={{ color: "var(--mis-cyan)" }} />
              <h2 className="text-sm font-semibold" style={{ color: "var(--mis-text)" }}>{copy.customerTaskTitle}</h2>
              <StatusBadge
                status={customerTaskResult?.ok ? "completed" : customerTaskResult?.dry_run ? "planned" : customerTaskResult ? "failed" : "ready"}
              />
            </div>
            <p className="text-[11px] mt-1 max-w-3xl" style={{ color: "var(--mis-dim)" }}>{copy.customerTaskSummary}</p>
            <p className="text-[10px] mt-1 max-w-3xl" style={{ color: "var(--mis-muted)" }}>{copy.confirmLiveHint}</p>
            <label className="inline-flex items-center gap-2 mt-2 text-[10px] rounded px-2 py-1" style={{ color: liveRuntimeConfirmed ? "var(--mis-success)" : "var(--mis-warning)", background: liveRuntimeConfirmed ? "rgba(45,212,191,0.10)" : "rgba(251,191,36,0.10)", border: liveRuntimeConfirmed ? "1px solid rgba(45,212,191,0.20)" : "1px solid rgba(251,191,36,0.24)" }}>
              <input
                type="checkbox"
                checked={liveRuntimeConfirmed}
                onChange={(event) => setLiveRuntimeConfirmed(event.target.checked)}
              />
              {copy.liveRuntimeConfirmLabel}
              <StatusBadge status={liveRuntimeConfirmed ? "pass" : "attention"} label={liveRuntimeConfirmed ? copy.liveRuntimeConfirmed : copy.liveRuntimeConfirmRequired} />
            </label>
          </div>
          <div className="flex gap-2 shrink-0 flex-wrap justify-start lg:justify-end">
            <button
              onClick={() => runCustomerTask(false)}
              disabled={customerTaskBusy}
              className="flex items-center gap-1.5 text-[11px] px-3 py-1.5 rounded disabled:opacity-50"
              style={{ background: "rgba(34,211,238,0.12)", color: "var(--mis-cyan)", border: "1px solid rgba(34,211,238,0.2)" }}
            >
              {customerTaskBusy ? <RefreshCw size={12} /> : <Play size={12} />}
              {customerTaskBusy ? copy.customerTaskRunning : copy.runSafeTask}
            </button>
            <button
              onClick={() => runCustomerTask(true)}
              disabled={customerTaskBusy || selectedAdapterLiveBlocked || selectedAdapterLiveConfirmMissing}
              className="flex items-center gap-1.5 text-[11px] px-3 py-1.5 rounded disabled:opacity-50"
              style={{ background: "rgba(45,212,191,0.12)", color: "var(--mis-success)", border: "1px solid rgba(45,212,191,0.22)" }}
            >
              {customerTaskBusy ? <RefreshCw size={12} /> : <ShieldCheck size={12} />}
              {customerTaskBusy ? copy.customerTaskRunning : copy.confirmLiveTask}
            </button>
            <button
              onClick={submitCustomerTaskAsync}
              disabled={customerTaskBusy || selectedAdapterLiveBlocked || selectedAdapterLiveConfirmMissing}
              className="flex items-center gap-1.5 text-[11px] px-3 py-1.5 rounded disabled:opacity-50"
              style={{ background: "rgba(251,191,36,0.12)", color: "var(--mis-warning)", border: "1px solid rgba(251,191,36,0.24)" }}
            >
              {customerTaskBusy ? <RefreshCw size={12} /> : <Activity size={12} />}
              {customerTaskBusy ? copy.customerTaskRunning : copy.submitAsyncTask}
            </button>
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-[1fr_180px] gap-3 mt-4">
          <label className="text-[10px] uppercase" style={{ color: "var(--mis-muted)" }}>
            {copy.taskTitleLabel}
            <input
              value={customerTaskForm.title}
              onChange={(event) => updateCustomerTaskText("title", event.target.value)}
              className="mt-1 w-full rounded px-3 py-2 text-xs outline-none"
              style={{ background: "var(--mis-surface2)", color: "var(--mis-text)", border: "1px solid var(--mis-border)" }}
            />
          </label>
          <label className="text-[10px] uppercase" style={{ color: "var(--mis-muted)" }}>
            {copy.adapterLabel}
            <select
              value={customerTaskForm.adapter}
              onChange={(event) => updateCustomerTaskAdapter(event.target.value as (typeof WORKER_ADAPTERS)[number])}
              className="mt-1 w-full rounded px-3 py-2 text-xs outline-none"
              style={{ background: "var(--mis-surface2)", color: "var(--mis-text)", border: "1px solid var(--mis-border)" }}
            >
              {WORKER_ADAPTERS.map(adapter => (
                <option key={adapter} value={adapter}>{adapter}</option>
              ))}
            </select>
          </label>
          <label className="md:col-span-2 text-[10px] uppercase" style={{ color: "var(--mis-muted)" }}>
            {copy.taskDescriptionLabel}
            <textarea
              value={customerTaskForm.description}
              onChange={(event) => updateCustomerTaskText("description", event.target.value)}
              rows={3}
              className="mt-1 w-full rounded px-3 py-2 text-xs outline-none resize-none"
              style={{ background: "var(--mis-surface2)", color: "var(--mis-text)", border: "1px solid var(--mis-border)" }}
            />
          </label>
        </div>

        {renderActiveIntakeGate()}

        <div
          className="rounded-lg p-3 mt-3"
          style={{
            background: selectedAdapterLiveBlocked ? "rgba(248,113,113,0.08)" : "var(--mis-surface2)",
            border: selectedAdapterLiveBlocked ? "1px solid rgba(248,113,113,0.22)" : "1px solid var(--mis-border)",
          }}
        >
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-1.5 min-w-0">
              {selectedAdapterLiveBlocked ? <AlertTriangle size={13} style={{ color: "#F87171" }} /> : <CheckCircle2 size={13} style={{ color: "var(--mis-success)" }} />}
              <div className="text-[11px] font-semibold truncate" style={{ color: "var(--mis-text)" }}>
                {customerTaskForm.adapter} · {selectedAdapterIsReady ? copy.selectedAdapterReady : copy.selectedAdapterBlocked}
              </div>
            </div>
            <StatusBadge status={selectedAdapterRoute?.readiness || "unknown"} />
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-5 gap-2 mt-2">
            <div className="rounded px-2 py-1" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
              <div className="text-[9px]" style={{ color: "var(--mis-muted)" }}>{copy.trustStatus}</div>
              <div className="text-[10px] font-semibold truncate" style={{ color: "var(--mis-text)" }}>{selectedAdapterRoute?.trust_status || "—"}</div>
            </div>
            <div className="rounded px-2 py-1" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
              <div className="text-[9px]" style={{ color: "var(--mis-muted)" }}>{copy.observationLevel}</div>
              <div className="text-[10px] font-semibold truncate" style={{ color: "var(--mis-text)" }}>{selectedAdapterRoute?.observation_level || "—"}</div>
            </div>
            <div className="rounded px-2 py-1" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
              <div className="text-[9px]" style={{ color: "var(--mis-muted)" }}>{copy.riskFloor}</div>
              <div className="text-[10px] font-semibold truncate" style={{ color: "var(--mis-text)" }}>{selectedAdapterRoute?.risk_floor || "—"}</div>
            </div>
            <div className="rounded px-2 py-1" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
              <div className="text-[9px]" style={{ color: "var(--mis-muted)" }}>{copy.targetResource}</div>
              <div className="text-[10px] font-semibold truncate" style={{ color: "var(--mis-text)" }}>{selectedAdapterRoute?.target_resource || "—"}</div>
            </div>
            <div className="rounded px-2 py-1" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
              <div className="text-[9px]" style={{ color: "var(--mis-muted)" }}>{copy.commercialReadiness}</div>
              <div className="text-[10px] font-semibold truncate" style={{ color: "var(--mis-cyan)" }}>{selectedAdapterRoute?.commercial_readiness || "—"}</div>
            </div>
          </div>
          <div className="text-[10px] mt-2 truncate" style={{ color: "var(--mis-cyan)" }}>{copy.nextAction}: {selectedAdapterRemediation?.primary_next_action || selectedAdapterRoute?.recommended_action || "agentops worker readiness"}</div>
          {(selectedAdapterRemediationCommands.length > 0 || selectedAdapterMissingChecks.length > 0) && (
            <div className="rounded px-2 py-1.5 mt-2" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="min-w-0">
                  <div className="text-[9px] font-semibold" style={{ color: "var(--mis-text)" }}>{copy.adapterRemediationTitle}</div>
                  <div className="text-[8px] mt-0.5 truncate" style={{ color: "var(--mis-muted)" }}>{copy.adapterRemediationSummary}</div>
                </div>
                <div className="flex items-center gap-1">
                  <StatusBadge status={selectedAdapterRemediation?.status || "unknown"} />
                  <StatusBadge status={selectedAdapterRemediation?.safety?.server_executes_shell === false ? "pass" : "attention"} label={copy.readOnlyProof} />
                </div>
              </div>
              {selectedAdapterMissingChecks.length > 0 && (
                <div className="text-[8px] mt-1 truncate" style={{ color: "var(--mis-warning)" }}>
                  {copy.missingChecks}: {selectedAdapterMissingChecks.slice(0, 4).join(", ")}
                </div>
              )}
              <div className="flex flex-wrap gap-1 mt-1.5">
                {selectedAdapterRemediationCommands.map((command) => (
                  <button
                    key={`${customerTaskForm.adapter}:${command.phase}:${command.command}`}
                    onClick={() => void copyIntakeCommand(String(command.command || ""))}
                    className="inline-flex items-center gap-1 text-[8px] px-1.5 py-0.5 rounded max-w-full"
                    style={{ color: command.confirm_required ? "var(--mis-warning)" : "var(--mis-cyan)", background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}
                    title={String(command.command || "")}
                  >
                    <Copy size={8} />
                    <span className="truncate max-w-[112px]">{copiedIntakeCommand === command.command ? copy.copiedCommand : command.phase || copy.copyCommand}</span>
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>

        <div className="text-[10px] mt-3" style={{ color: "var(--mis-muted)" }}>{copy.asyncTaskHint}</div>

        {(customerTaskError || customerTaskResult || customerTaskJob) && (
          <div
            className="rounded-lg p-3 mt-4"
            style={{
              background: customerTaskError ? "rgba(248,113,113,0.08)" : "var(--mis-surface2)",
              border: customerTaskError ? "1px solid rgba(248,113,113,0.22)" : "1px solid var(--mis-border)",
            }}
          >
            {customerTaskError && (
              <div className="text-[11px]" style={{ color: "#F87171" }}>{customerTaskError}</div>
            )}
            {customerTaskJob && (
              <div className="space-y-3">
                <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 gap-2">
                  <div className="rounded px-3 py-2" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                    <div className="text-[10px]" style={{ color: "var(--mis-muted)" }}>{copy.jobId}</div>
                    <div className="text-[11px] font-semibold truncate mt-1" style={{ color: "var(--mis-text)" }}>{customerTaskJob.job_id}</div>
                  </div>
                  <div className="rounded px-3 py-2" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                    <div className="text-[10px]" style={{ color: "var(--mis-muted)" }}>{copy.jobType}</div>
                    <div className="text-[11px] font-semibold truncate mt-1" style={{ color: "var(--mis-text)" }}>{customerTaskJob.workflow_type}</div>
                  </div>
                  <div className="rounded px-3 py-2" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                    <div className="text-[10px]" style={{ color: "var(--mis-muted)" }}>{copy.adapterLabel}</div>
                    <div className="text-[11px] font-semibold truncate mt-1" style={{ color: "var(--mis-text)" }}>{customerTaskJob.adapter || customerTaskForm.adapter}</div>
                  </div>
                  <div className="rounded px-3 py-2" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                    <div className="text-[10px]" style={{ color: "var(--mis-muted)" }}>{copy.eventStatus}</div>
                    <div className="mt-1"><StatusBadge status={customerTaskJob.status} /></div>
                  </div>
                </div>
                <div className="text-[11px] leading-relaxed rounded px-3 py-2" style={{ background: "var(--mis-bg)", color: "var(--mis-dim)", border: "1px solid var(--mis-border)" }}>
                  {customerTaskJob.input_summary || customerTaskJob.title}
                </div>
              </div>
            )}
            {customerTaskResult && (
              <div className="space-y-3">
                <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 gap-2">
                  <div className="rounded px-3 py-2" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                    <div className="text-[10px]" style={{ color: "var(--mis-muted)" }}>{copy.taskId}</div>
                    <div className="text-[11px] font-semibold truncate mt-1" style={{ color: "var(--mis-text)" }}>{customerTaskResult.task_id}</div>
                  </div>
                  <div className="rounded px-3 py-2" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                    <div className="text-[10px]" style={{ color: "var(--mis-muted)" }}>{copy.runId}</div>
                    <div className="text-[11px] font-semibold truncate mt-1" style={{ color: "var(--mis-text)" }}>{customerTaskResult.run_id || "—"}</div>
                  </div>
                  <div className="rounded px-3 py-2" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                    <div className="text-[10px]" style={{ color: "var(--mis-muted)" }}>{copy.artifactId}</div>
                    <div className="text-[11px] font-semibold truncate mt-1" style={{ color: "var(--mis-text)" }}>{customerTaskResult.artifact_id || "—"}</div>
                  </div>
                  <div className="rounded px-3 py-2" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                    <div className="text-[10px]" style={{ color: "var(--mis-muted)" }}>{copy.runtime}</div>
                    <div className="text-[11px] font-semibold truncate mt-1" style={{ color: "var(--mis-text)" }}>{customerTaskResult.adapter || customerTaskForm.adapter}</div>
                  </div>
                </div>
                {(customerTaskResult.plan_id || customerTaskResult.plan_evidence_manifest_id) && (
                  <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
                    <div className="rounded px-3 py-2" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                      <div className="text-[10px]" style={{ color: "var(--mis-muted)" }}>{copy.agentPlan}</div>
                      <div className="text-[11px] font-semibold truncate mt-1" style={{ color: "var(--mis-text)" }}>{customerTaskResult.plan_id || "—"}</div>
                    </div>
                    <div className="rounded px-3 py-2" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                      <div className="text-[10px]" style={{ color: "var(--mis-muted)" }}>{copy.planEvidence}</div>
                      <div className="mt-1"><StatusBadge status={customerTaskResult.plan_evidence_pass ? "pass" : customerTaskResult.plan_evidence_status || "attention"} label={customerTaskResult.plan_evidence_manifest_id || "—"} /></div>
                    </div>
                    <div className="rounded px-3 py-2" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                      <div className="text-[10px]" style={{ color: "var(--mis-muted)" }}>{copy.approvals}</div>
                      <div className="text-[11px] font-semibold truncate mt-1" style={{ color: "var(--mis-text)" }}>{customerTaskResult.approval_id || "—"}</div>
                    </div>
                  </div>
                )}
                <div className="flex flex-wrap items-center gap-2">
                  {customerTaskResult.task_id && (
                    <Link
                      to={`/admin/tasks/${customerTaskResult.task_id}`}
                      className="text-[11px] px-3 py-1.5 rounded"
                      style={{ background: "rgba(34,211,238,0.12)", color: "var(--mis-cyan)", border: "1px solid rgba(34,211,238,0.2)" }}
                    >
                      {copy.openTask}
                    </Link>
                  )}
                  {customerTaskResult.run_id && (
                    <Link
                      to={`/admin/runs/${customerTaskResult.run_id}`}
                      className="text-[11px] px-3 py-1.5 rounded"
                      style={{ background: "rgba(45,212,191,0.12)", color: "var(--mis-success)", border: "1px solid rgba(45,212,191,0.22)" }}
                    >
                      {copy.openRun}
                    </Link>
                  )}
                  {Object.entries(customerTaskResult.evidence || {}).map(([key, value]) => (
                    <span key={key} className="text-[10px] px-2 py-1 rounded" style={{ background: "var(--mis-bg)", color: "var(--mis-muted)", border: "1px solid var(--mis-border)" }}>
                      {copy.evidence}: {key} {value ?? 0}
                    </span>
                  ))}
                </div>
                {(customerTaskResult.output_summary || customerTaskResult.reason || customerTaskResult.error) && (
                  <div className="text-[11px] leading-relaxed rounded px-3 py-2" style={{ background: "var(--mis-bg)", color: customerTaskResult.error ? "#F87171" : "var(--mis-dim)", border: "1px solid var(--mis-border)" }}>
                    <span className="font-semibold" style={{ color: "var(--mis-text)" }}>{copy.outputSummary}: </span>
                    {customerTaskResult.output_summary || customerTaskResult.reason || customerTaskResult.error}
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        <div className="rounded-lg p-3 mt-4" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
          <div className="flex items-start justify-between gap-3">
            <div>
              <div className="text-[11px] font-semibold" style={{ color: "var(--mis-text)" }}>{copy.workflowJobsTitle}</div>
              <div className="text-[10px] mt-1" style={{ color: "var(--mis-muted)" }}>{copy.workflowJobsSummary}</div>
              {workflowJobResult && (
                <div className="text-[10px] mt-1" style={{ color: workflowJobResult.includes("failed") ? "var(--mis-success)" : "var(--mis-warning)" }}>
                  {workflowJobResult}
                </div>
              )}
            </div>
            <button
              onClick={() => void refresh()}
              className="flex items-center gap-1.5 text-[11px] px-3 py-1.5 rounded"
              style={{ background: "rgba(34,211,238,0.12)", color: "var(--mis-cyan)", border: "1px solid rgba(34,211,238,0.2)" }}
            >
              <RefreshCw size={12} />
              {copy.refresh}
            </button>
          </div>

          <div className="rounded-lg p-3 mt-3" style={{ background: stuckWorkflowRecoveryRows.length > 0 ? "rgba(251,191,36,0.08)" : "var(--mis-bg)", border: stuckWorkflowRecoveryRows.length > 0 ? "1px solid rgba(251,191,36,0.24)" : "1px solid var(--mis-border)" }}>
            <div className="flex items-start justify-between gap-3">
              <div>
                <div className="flex items-center gap-1.5">
                  <AlertTriangle size={13} style={{ color: stuckWorkflowRecoveryRows.length > 0 ? "var(--mis-warning)" : "var(--mis-muted)" }} />
                  <div className="text-[11px] font-semibold" style={{ color: "var(--mis-text)" }}>{copy.stuckWorkflowJobsTitle}</div>
                </div>
                <div className="text-[10px] mt-1" style={{ color: "var(--mis-muted)" }}>{copy.stuckWorkflowJobsSummary}</div>
              </div>
              <StatusBadge status={stuckWorkflowRecoveryRows.length > 0 ? "blocked" : "pass"} label={String(stuckWorkflowRecoveryRows.length)} />
            </div>
            <div className="space-y-2 mt-3">
              {stuckWorkflowRecoveryRows.length === 0 && (
                <div className="text-[11px] rounded px-3 py-2" style={{ color: "var(--mis-muted)", background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
                  {copy.noStuckWorkflowJobs}
                </div>
              )}
              {stuckWorkflowRecoveryRows.slice(0, 4).map((job) => (
                <div key={job.job_id} className="grid grid-cols-1 lg:grid-cols-[1.1fr_0.7fr_0.7fr_auto] gap-3 items-start lg:items-center rounded px-3 py-2" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                  <div className="min-w-0">
                    <div className="text-[11px] font-semibold truncate" style={{ color: "var(--mis-text)" }}>{"title" in job ? job.title || job.job_id : job.job_id}</div>
                    <div className="text-[10px] truncate" style={{ color: "var(--mis-muted)" }}>{job.job_id} · {job.workflow_type}</div>
                  </div>
                  <div className="text-[10px] truncate" style={{ color: "var(--mis-dim)" }}>
                    {"adapter" in job ? job.adapter || "default" : copy.recoveryRefs} · {job.status}
                  </div>
                  <div className="text-[10px] truncate" style={{ color: "var(--mis-dim)" }}>
                    {copy.age}: {job.age_sec || 0}s
                  </div>
                  <button
                    onClick={() => markStuckWorkflowJobFailed(job.job_id)}
                    disabled={Boolean(workflowJobAction)}
                    className="flex items-center gap-1 text-[11px] px-2.5 py-1.5 rounded disabled:opacity-40"
                    style={{ background: "rgba(248,113,113,0.1)", color: "#F87171", border: "1px solid rgba(248,113,113,0.22)" }}
                  >
                    {workflowJobAction === job.job_id ? <RefreshCw size={12} /> : <Square size={12} />}
                    {workflowJobAction === job.job_id ? copy.markingJobFailed : copy.markJobFailed}
                  </button>
                </div>
              ))}
            </div>
          </div>

          <div className="space-y-2 mt-3">
            {workflowJobs.length === 0 && (
              <div className="text-[11px] rounded px-3 py-2" style={{ color: "var(--mis-muted)", background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                {copy.noWorkflowJobs}
              </div>
            )}
            {workflowJobs.slice(0, 6).map((job) => (
              <div key={job.job_id} className="grid grid-cols-1 lg:grid-cols-[1.1fr_0.8fr_0.8fr_auto] gap-3 items-start lg:items-center rounded px-3 py-2" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                <div className="min-w-0">
                  <div className="text-[11px] font-semibold truncate" style={{ color: "var(--mis-text)" }}>{job.title || job.job_id}</div>
                  <div className="text-[10px] truncate" style={{ color: "var(--mis-muted)" }}>{job.job_id} · {job.workflow_type}</div>
                </div>
                <div className="text-[10px] truncate" style={{ color: "var(--mis-dim)" }}>
                  {job.adapter || "default"} · {job.confirm_run ? "live" : "safe"}
                </div>
                <div className="flex items-center gap-2 min-w-0">
                  <StatusBadge status={job.status} />
                  <span className="text-[10px] truncate" style={{ color: "var(--mis-muted)" }}>{job.updated_at || job.created_at}</span>
                </div>
                <div className="flex gap-1.5 justify-end">
                  {job.result_task_id && (
                    <Link to={`/admin/tasks/${job.result_task_id}`} className="text-[10px] px-2 py-1 rounded" style={{ background: "rgba(34,211,238,0.1)", color: "var(--mis-cyan)", border: "1px solid rgba(34,211,238,0.18)" }}>
                      {copy.taskId}
                    </Link>
                  )}
                  {job.result_run_id && (
                    <Link to={`/admin/runs/${job.result_run_id}`} className="text-[10px] px-2 py-1 rounded" style={{ background: "rgba(45,212,191,0.1)", color: "var(--mis-success)", border: "1px solid rgba(45,212,191,0.18)" }}>
                      {copy.runId}
                    </Link>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div
        className="rounded-xl p-4"
        style={{ background: "var(--mis-surface)", border: "1px solid var(--mis-border)" }}
      >
        <div className="flex flex-col lg:flex-row lg:items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-2">
              <CheckCircle2 size={14} style={{ color: "var(--mis-success)" }} />
              <h2 className="text-sm font-semibold" style={{ color: "var(--mis-text)" }}>{copy.operatorTitle}</h2>
            </div>
            <p className="text-[11px] mt-1 max-w-3xl" style={{ color: "var(--mis-dim)" }}>{copy.operatorSummary}</p>
          </div>
          <StatusBadge status={gatewayReady ? "ready" : "planned"} label={gatewayReady ? copy.statusReady : copy.statusSetup} />
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-3 mt-4">
          {operatorReadiness.map(item => (
            <div
              key={item.title}
              className="rounded-lg px-3 py-3"
              style={{
                background: item.attention ? "rgba(251,191,36,0.08)" : "var(--mis-surface2)",
                border: item.attention ? "1px solid rgba(251,191,36,0.28)" : "1px solid var(--mis-border)",
              }}
            >
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-1.5 min-w-0">
                  {item.attention ? <AlertTriangle size={13} style={{ color: "var(--mis-warning)" }} /> : <CheckCircle2 size={13} style={{ color: "var(--mis-success)" }} />}
                  <div className="text-[11px] font-semibold truncate" style={{ color: "var(--mis-text)" }}>{item.title}</div>
                </div>
                <StatusBadge status={item.status} label={item.label} />
              </div>
              <p className="text-[10px] mt-2 leading-relaxed" style={{ color: "var(--mis-dim)" }}>{item.body}</p>
              <div className="text-[10px] mt-2 truncate" style={{ color: "var(--mis-muted)" }}>{item.meta}</div>
            </div>
          ))}
        </div>
        <div className="mt-4 rounded-lg p-3" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
          <div className="flex items-center justify-between gap-3">
            <div>
              <div className="text-[11px] font-semibold" style={{ color: "var(--mis-text)" }}>{copy.adapterRoutesTitle}</div>
              <div className="text-[10px] mt-0.5" style={{ color: "var(--mis-muted)" }}>{copy.adapterRoutesSummary}</div>
            </div>
            <StatusBadge status={adapterReadiness?.status || "unknown"} label={`${copy.recommendedAdapter}: ${recommendedAdapter}`} />
          </div>
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-3 mt-3">
            {adapterRouteCards.map(({ item, liveReady, attention, checkSummary, remediation, remediationCommands, remediationMissing }) => (
              <div
                key={item.adapter}
                className="rounded px-3 py-2"
                style={{
                  background: attention ? "rgba(248,113,113,0.08)" : "var(--mis-bg)",
                  border: attention ? "1px solid rgba(248,113,113,0.22)" : "1px solid var(--mis-border)",
                }}
              >
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-1.5 min-w-0">
                    {attention ? <AlertTriangle size={13} style={{ color: "#F87171" }} /> : <CheckCircle2 size={13} style={{ color: liveReady || item.adapter === "mock" ? "var(--mis-success)" : "var(--mis-muted)" }} />}
                    <div className="text-[11px] font-semibold truncate" style={{ color: "var(--mis-text)" }}>{item.adapter}</div>
                  </div>
                  <StatusBadge status={item.readiness} />
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 mt-2">
                  <div className="rounded px-2 py-1" style={{ background: "var(--mis-surface)", border: "1px solid var(--mis-border)" }}>
                    <div className="text-[9px]" style={{ color: "var(--mis-muted)" }}>{copy.trustStatus}</div>
                    <div className="text-[10px] font-semibold truncate" style={{ color: "var(--mis-text)" }}>{item.trust_status || "—"}</div>
                  </div>
                  <div className="rounded px-2 py-1" style={{ background: "var(--mis-surface)", border: "1px solid var(--mis-border)" }}>
                    <div className="text-[9px]" style={{ color: "var(--mis-muted)" }}>{copy.liveReady}</div>
                    <div className="text-[10px] font-semibold truncate" style={{ color: liveReady ? "var(--mis-success)" : "var(--mis-dim)" }}>
                      {liveReady ? copy.yes : item.adapter === "mock" ? copy.no : copy.notLiveReady}
                    </div>
                  </div>
                  <div className="rounded px-2 py-1" style={{ background: "var(--mis-surface)", border: "1px solid var(--mis-border)" }}>
                    <div className="text-[9px]" style={{ color: "var(--mis-muted)" }}>{copy.observationLevel}</div>
                    <div className="text-[10px] font-semibold truncate" style={{ color: "var(--mis-text)" }}>{item.observation_level || "—"}</div>
                  </div>
                  <div className="rounded px-2 py-1" style={{ background: "var(--mis-surface)", border: "1px solid var(--mis-border)" }}>
                    <div className="text-[9px]" style={{ color: "var(--mis-muted)" }}>{copy.riskFloor}</div>
                    <div className="text-[10px] font-semibold truncate" style={{ color: "var(--mis-text)" }}>{item.risk_floor || "—"}</div>
                  </div>
                </div>
                <div className="text-[10px] mt-2 truncate" style={{ color: "var(--mis-muted)" }}>
                  {copy.commercialReadiness}: {item.commercial_readiness || "—"}
                </div>
                <div className="text-[10px] mt-2 truncate" style={{ color: "var(--mis-dim)" }}>
                  {copy.targetResource}: {item.target_resource || "—"}
                </div>
                <div className="text-[10px] mt-1 truncate" style={{ color: "var(--mis-muted)" }}>
                  {checkSummary}
                </div>
                <div className="text-[10px] mt-1 truncate" style={{ color: "var(--mis-cyan)" }}>
                  {copy.nextAction}: {remediation?.primary_next_action || item.recommended_action || "agentops worker readiness"}
                </div>
                {(remediationCommands.length > 0 || remediationMissing.length > 0) && (
                  <div className="rounded px-2 py-1 mt-2" style={{ background: "var(--mis-surface)", border: "1px solid var(--mis-border)" }}>
                    <div className="flex items-center justify-between gap-2">
                      <div className="text-[9px] font-semibold truncate" style={{ color: "var(--mis-text)" }}>{copy.adapterRemediationTitle}</div>
                      <div className="flex items-center gap-1">
                        <StatusBadge status={remediation?.status || "unknown"} />
                        <StatusBadge status={remediation?.safety?.server_executes_shell === false ? "pass" : "attention"} label={copy.readOnlyProof} />
                      </div>
                    </div>
                    {remediationMissing.length > 0 && (
                      <div className="text-[8px] mt-1 truncate" style={{ color: "var(--mis-warning)" }}>
                        {copy.missingChecks}: {remediationMissing.slice(0, 3).join(", ")}
                      </div>
                    )}
                    <div className="flex flex-wrap gap-1 mt-1">
                      {remediationCommands.map((command) => (
                        <button
                          key={`${item.adapter}:${command.phase}:${command.command}`}
                          onClick={() => void copyIntakeCommand(String(command.command || ""))}
                          className="inline-flex items-center gap-1 text-[8px] px-1 py-0.5 rounded max-w-full"
                          style={{ color: command.confirm_required ? "var(--mis-warning)" : "var(--mis-cyan)", background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}
                          title={String(command.command || "")}
                        >
                          <Copy size={8} />
                          <span className="truncate max-w-[82px]">{copiedIntakeCommand === command.command ? copy.copiedCommand : command.phase || copy.copyCommand}</span>
                        </button>
                      ))}
                    </div>
                  </div>
                )}
                {item.last_error && (
                  <div className="text-[10px] mt-1 truncate" style={{ color: "#F87171" }}>
                    {item.last_error}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      </div>

      <div
        className="rounded-xl p-4"
        style={{ background: "var(--mis-surface)", border: "1px solid var(--mis-border)" }}
      >
        <div className="flex flex-col lg:flex-row lg:items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-2">
              <ShieldCheck size={14} style={{ color: "var(--mis-cyan)" }} />
              <h2 className="text-sm font-semibold" style={{ color: "var(--mis-text)" }}>{copy.gatewayTitle}</h2>
              <StatusBadge status={gatewayStatus?.status || "unknown"} />
            </div>
            <p className="text-[11px] mt-1 max-w-2xl" style={{ color: "var(--mis-dim)" }}>{copy.gatewaySummary}</p>
          </div>
          <div className="text-left lg:text-right">
            <div className="text-[10px] uppercase tracking-wide" style={{ color: "var(--mis-muted)" }}>{copy.authMode}</div>
            <div className="text-xs font-semibold mt-0.5" style={{ color: "var(--mis-text)" }}>
              {gatewayStatus?.auth.mode || "unknown"}
            </div>
          </div>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 xl:grid-cols-5 gap-3 mt-4">
          {[
            { label: copy.authenticated, value: gatewayStatus?.auth.authenticated ? copy.yes : copy.no },
            { label: copy.gatewayWorkspace, value: gatewayStatus?.auth.workspace_id || "local-demo" },
            { label: copy.gatewayScopes, value: gatewayStatus?.auth.scopes.length ?? "—" },
            { label: copy.activeEnrollments, value: activeEnrollments },
            { label: copy.staleEnrollments, value: staleEnrollments },
          ].map((item) => (
            <div key={item.label} className="rounded-lg px-3 py-2" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
              <div className="text-[10px]" style={{ color: "var(--mis-muted)" }}>{item.label}</div>
              <div className="text-sm font-semibold truncate mt-1" style={{ color: "var(--mis-text)" }}>{item.value}</div>
            </div>
          ))}
        </div>
        <div className="rounded-lg p-3 mt-3" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
          <div className="flex flex-col lg:flex-row lg:items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <div className="text-[11px] font-semibold" style={{ color: "var(--mis-text)" }}>{copy.productionSecurity}</div>
                <StatusBadge status={securityReadiness?.status || "unknown"} label={securityReadiness?.production_ready ? copy.productionReady : copy.localDevOnly} />
              </div>
              <div className="text-[10px] mt-1 line-clamp-2" style={{ color: "var(--mis-muted)" }}>
                {securityReadiness?.contract || "local_dev_no_token is local-only"}
              </div>
            </div>
            <StatusBadge status={securityReadiness?.safety?.read_only ? "pass" : "fail"} label={securityReadiness?.safety?.read_only ? copy.readOnlyProof : copy.statusAttention} />
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2 mt-3">
            <div className="rounded px-3 py-2 md:col-span-2" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
              <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2">
                <div className="min-w-0">
                  <div className="text-[10px] font-semibold" style={{ color: "var(--mis-text)" }}>{copy.localWriteGuard}</div>
                  <div className="text-[10px] mt-1 line-clamp-2" style={{ color: "var(--mis-muted)" }}>
                    {localWriteGuardGate?.detail || copy.localWriteGuardSummary}
                  </div>
                </div>
                <StatusBadge status={localWriteGuardGate?.status || "unknown"} />
              </div>
              <div className="text-[10px] mt-2 line-clamp-2" style={{ color: "var(--mis-dim)" }}>
                {copy.nextAction}: {localWriteGuardGate?.next_action || "agentops security production-readiness"}
              </div>
            </div>
            {visibleSecurityGates.map((gate) => (
              <div key={gate.id} className="rounded px-3 py-2" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                <div className="flex items-center justify-between gap-2">
                  <div className="text-[10px] font-semibold truncate" style={{ color: "var(--mis-text)" }}>{gate.label || copy.securityGate}</div>
                  <StatusBadge status={gate.status} />
                </div>
                <div className="text-[10px] mt-1 line-clamp-2" style={{ color: "var(--mis-muted)" }}>{gate.detail}</div>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div
        className="rounded-xl p-4"
        style={{ background: "var(--mis-surface)", border: "1px solid var(--mis-border)" }}
      >
        <div className="flex flex-col lg:flex-row lg:items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-2">
              <Activity size={14} style={{ color: "var(--mis-success)" }} />
              <h2 className="text-sm font-semibold" style={{ color: "var(--mis-text)" }}>{copy.workerTitle}</h2>
              <StatusBadge status={workerStatus?.status || "unknown"} />
            </div>
            <p className="text-[11px] mt-1 max-w-2xl" style={{ color: "var(--mis-dim)" }}>{copy.workerSummary}</p>
            <label className="inline-flex items-center gap-2 mt-2 text-[10px] rounded px-2 py-1" style={{ color: liveRuntimeConfirmed ? "var(--mis-success)" : "var(--mis-warning)", background: liveRuntimeConfirmed ? "rgba(45,212,191,0.10)" : "rgba(251,191,36,0.10)", border: liveRuntimeConfirmed ? "1px solid rgba(45,212,191,0.20)" : "1px solid rgba(251,191,36,0.24)" }}>
              <input
                type="checkbox"
                checked={liveRuntimeConfirmed}
                onChange={(event) => setLiveRuntimeConfirmed(event.target.checked)}
              />
              {copy.liveRuntimeConfirmLabel}
              <StatusBadge status={liveRuntimeConfirmed ? "pass" : "attention"} label={liveRuntimeConfirmed ? copy.liveRuntimeConfirmed : copy.liveRuntimeConfirmRequired} />
            </label>
            {dispatchResult && (
              <div className="text-[11px] mt-2" style={{ color: dispatchResult.includes("failed") ? "#F87171" : "var(--mis-success)" }}>
                {dispatchResult}
              </div>
            )}
          </div>
          <div className="flex gap-2 flex-wrap justify-end">
            {[
              { adapter: "mock" as const, label: copy.dispatchMock },
              { adapter: "hermes" as const, label: copy.dispatchHermes },
              { adapter: "openclaw" as const, label: copy.dispatchOpenClaw },
            ].map((item) => (
              <button
                key={item.adapter}
                onClick={() => runWorkerOnce(item.adapter)}
                disabled={Boolean(dispatching) || liveAdapterConfirmMissing(item.adapter)}
                className="flex items-center gap-1.5 text-[11px] px-3 py-1.5 rounded disabled:opacity-50"
                style={{ background: "rgba(34,211,238,0.12)", color: "var(--mis-cyan)", border: "1px solid rgba(34,211,238,0.2)" }}
              >
                {dispatching === item.adapter ? <RefreshCw size={12} /> : <Play size={12} />}
                {dispatching === item.adapter ? copy.dispatching : item.label}
              </button>
            ))}
          </div>
        </div>
        {renderActiveIntakeGate()}
        {lastWorkerDispatch && (
          <div className="rounded-lg p-3 mt-3" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
            <div className="flex flex-col md:flex-row md:items-start md:justify-between gap-3">
              <div className="min-w-0">
                <div className="text-[11px] font-semibold truncate" style={{ color: "var(--mis-text)" }}>
                  {lastWorkerDispatch.adapter} · {lastWorkerDispatch.task_id}
                </div>
                <div className="text-[10px] mt-1 truncate" style={{ color: "var(--mis-muted)" }}>
                  {copy.runId}: {lastWorkerDispatch.run_id || "—"} · {copy.agentPlan}: {lastWorkerDispatch.agent_plan_id || "—"}
                </div>
              </div>
              <div className="flex items-center gap-2 flex-wrap">
                <StatusBadge status={lastWorkerDispatch.ok ? "completed" : "failed"} />
                <StatusBadge status={lastWorkerDispatch.evidence?.agent_plan_verified ? "pass" : "attention"} label={copy.agentPlan} />
                <StatusBadge status={lastWorkerDispatch.plan_evidence_pass ? "pass" : "attention"} label={copy.planEvidence} />
                <StatusBadge status={String(lastWorkerDispatch.evidence?.intake?.severity || "unknown")} label={copy.intakeGate} />
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-2 mt-3">
              {lastWorkerDispatch.task_id && (
                <Link to={`/admin/tasks/${lastWorkerDispatch.task_id}`} className="text-[10px] px-2 py-1 rounded" style={{ background: "rgba(34,211,238,0.10)", color: "var(--mis-cyan)", border: "1px solid rgba(34,211,238,0.18)" }}>{copy.openTask}</Link>
              )}
              {lastWorkerDispatch.run_id && (
                <Link to={`/admin/runs/${lastWorkerDispatch.run_id}`} className="text-[10px] px-2 py-1 rounded" style={{ background: "rgba(45,212,191,0.10)", color: "var(--mis-success)", border: "1px solid rgba(45,212,191,0.18)" }}>{copy.openRun}</Link>
              )}
	              {Object.entries(lastWorkerDispatch.evidence?.evidence_counts || {}).slice(0, 6).map(([key, value]) => (
	                <span key={key} className="text-[10px] px-2 py-1 rounded" style={{ background: "var(--mis-bg)", color: "var(--mis-muted)", border: "1px solid var(--mis-border)" }}>
	                  {key}: {value}
	                </span>
	              ))}
	            </div>
	            {lastWorkerRunStartGate && (
	              <div className="rounded p-2 mt-3" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
	                <div className="flex flex-col lg:flex-row lg:items-start justify-between gap-2">
	                  <div className="min-w-0">
	                    <div className="flex flex-wrap items-center gap-1.5">
	                      <div className="text-[10px] font-semibold" style={{ color: "var(--mis-text)" }}>{copy.gatewayRunStartGate}</div>
	                      <StatusBadge status={lastWorkerRunStartGate.ok ? "pass" : String(lastWorkerRunStartGate.status || "blocked")} label={String(lastWorkerRunStartGate.status || "unknown")} />
	                      <StatusBadge status={lastWorkerDispatch.run_start_attempted === false ? "attention" : "pass"} label={`run_start_attempted: ${String(lastWorkerDispatch.run_start_attempted !== false)}`} />
	                      <StatusBadge status={lastWorkerRunStartGateSafety.server_executes_shell || lastWorkerRunStartGate.server_executes_shell ? "blocked" : "pass"} label={lastWorkerRunStartGateSafety.server_executes_shell || lastWorkerRunStartGate.server_executes_shell ? "server shell" : "no server shell"} />
	                      <StatusBadge status={lastWorkerRunStartGateSafety.live_execution_performed || lastWorkerRunStartGate.live_execution_performed ? "blocked" : "pass"} label={lastWorkerRunStartGateSafety.live_execution_performed || lastWorkerRunStartGate.live_execution_performed ? "live executed" : copy.liveExecutionProof} />
	                    </div>
	                    <div className="text-[10px] mt-1 line-clamp-2" style={{ color: "var(--mis-muted)" }}>
	                      {copy.hashBinding}: {lastWorkerRunStartGateHash} · {lastWorkerRunStartGate.operation}
	                    </div>
	                  </div>
                  <div className="flex items-center gap-1.5 flex-wrap justify-end">
                  {lastWorkerRunStartRecommendedNext && (
                    <button
                      type="button"
                      onClick={() => void copyIntakeCommand(lastWorkerRunStartRecommendedNext)}
	                      className="inline-flex items-center gap-1 text-[10px] px-2 py-1 rounded max-w-full"
	                      style={{ color: "var(--mis-cyan)", background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}
	                      title={lastWorkerRunStartRecommendedNext}
	                    >
	                      <Copy size={10} />
                      <span className="truncate max-w-[220px]">{copiedIntakeCommand === lastWorkerRunStartRecommendedNext ? copy.copiedCommand : lastWorkerRunStartRecommendedNext}</span>
                    </button>
                  )}
                    <button
                      type="button"
                      onClick={() => void recordLatestRunStartGateReceipt()}
                      disabled={Boolean(receiptAction)}
                      className="inline-flex items-center gap-1 text-[10px] px-2 py-1 rounded disabled:opacity-50"
                      style={{ color: "var(--mis-success)", background: "rgba(45,212,191,0.08)", border: "1px solid rgba(45,212,191,0.18)" }}
                      title={copy.recordVerifyReceipt}
                    >
                      {receiptAction === lastWorkerRunStartReceiptAction ? <RefreshCw size={10} /> : <CheckCircle2 size={10} />}
                      <span>{receiptAction === lastWorkerRunStartReceiptAction ? copy.recordingReceipt : copy.recordVerifyReceipt}</span>
                    </button>
                  </div>
                </div>
              </div>
            )}
	          </div>
	        )}
        <div className="flex gap-2 flex-wrap mt-4">
          {[
            { adapter: "mock" as const, label: copy.startMockDaemon },
            { adapter: "hermes" as const, label: copy.startHermesDaemon },
            { adapter: "openclaw" as const, label: copy.startOpenClawDaemon },
          ].map((item) => (
            <button
              key={item.adapter}
              onClick={() => startDaemon(item.adapter)}
              disabled={Boolean(dispatching) || workerStartBlocked || liveAdapterConfirmMissing(item.adapter) || isHostManagedAdapter(item.adapter) || isDaemonControlBlocked(item.adapter)}
              title={isHostManagedAdapter(item.adapter) ? copy.hostManagedHint : isDaemonControlBlocked(item.adapter) ? copy.processUnverified : undefined}
              className="flex items-center gap-1.5 text-[11px] px-3 py-1.5 rounded disabled:opacity-50"
              style={{ background: "rgba(45,212,191,0.12)", color: "var(--mis-success)", border: "1px solid rgba(45,212,191,0.22)" }}
            >
              {dispatching === `start-${item.adapter}` ? <RefreshCw size={12} /> : <Power size={12} />}
              {dispatching === `start-${item.adapter}` ? copy.starting : item.label}
            </button>
          ))}
          {[
            { adapter: "mock" as const, label: copy.restartMockDaemon },
            { adapter: "hermes" as const, label: copy.restartHermesDaemon },
            { adapter: "openclaw" as const, label: copy.restartOpenClawDaemon },
          ].map((item) => (
            <button
              key={`restart-${item.adapter}`}
              onClick={() => restartDaemon(item.adapter)}
              disabled={Boolean(dispatching) || workerStartBlocked || liveAdapterConfirmMissing(item.adapter) || isHostManagedAdapter(item.adapter) || isDaemonControlBlocked(item.adapter)}
              title={isHostManagedAdapter(item.adapter) ? copy.hostManagedHint : isDaemonControlBlocked(item.adapter) ? copy.processUnverified : undefined}
              className="flex items-center gap-1.5 text-[11px] px-3 py-1.5 rounded disabled:opacity-50"
              style={{ background: "rgba(122,90,248,0.1)", color: "#A78BFA", border: "1px solid rgba(122,90,248,0.2)" }}
            >
              {dispatching === `restart-${item.adapter}` ? <RefreshCw size={12} /> : <RotateCw size={12} />}
              {dispatching === `restart-${item.adapter}` ? copy.restarting : item.label}
            </button>
          ))}
          <button
            onClick={stopDaemons}
            disabled={Boolean(dispatching) || hostManagedAdapters.size > 0 || controlBlockedAdapters.size > 0}
            title={hostManagedAdapters.size > 0 ? copy.hostManagedHint : controlBlockedAdapters.size > 0 ? copy.processUnverified : undefined}
            className="flex items-center gap-1.5 text-[11px] px-3 py-1.5 rounded disabled:opacity-50"
            style={{ background: "rgba(248,113,113,0.1)", color: "#F87171", border: "1px solid rgba(248,113,113,0.22)" }}
          >
            {dispatching === "stop-daemons" ? <RefreshCw size={12} /> : <Square size={12} />}
            {dispatching === "stop-daemons" ? copy.stopping : copy.stopDaemons}
          </button>
        </div>
        {lastDaemonAdmissionSummary && (
          <div className="rounded-lg p-3 mt-3" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
            <div className="flex flex-col lg:flex-row lg:items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <ShieldCheck size={13} style={{ color: "var(--mis-cyan)" }} />
                  <div className="text-[11px] font-semibold" style={{ color: "var(--mis-text)" }}>{copy.daemonLoopAdmissionSummary}</div>
                  <StatusBadge status={lastDaemonAdmissionSummary.local_loop_admission_ready ? "pass" : "blocked"} label={String(lastDaemonAdmissionSummary.adapter || "adapter")} />
                  <StatusBadge status={lastDaemonAdmissionServerShell ? "blocked" : "pass"} label={lastDaemonAdmissionServerShell ? "server shell" : "copy-only"} />
                  <StatusBadge status={lastDaemonAdmissionLiveExecuted ? "blocked" : "pass"} label={copy.liveExecutionProof} />
                </div>
                <div className="text-[10px] mt-1 truncate" style={{ color: "var(--mis-muted)" }}>
                  {copy.liveAdapterTasks}: {lastDaemonAdmissionSummary.live_adapter_tasks_checked}
                  {" · "}
                  {copy.passedAdmission}: {lastDaemonAdmissionSummary.passed_local_loop_admission}
                  {" · "}
                  {copy.missingAdmission}: {lastDaemonAdmissionSummary.missing_local_loop_admission}
                  {" · "}
                  {copy.methodGates}: {lastDaemonAdmissionSummary.required_method_gates.length}
                </div>
              </div>
              <StatusBadge status={lastDaemonControl?.ok ? "ready" : "blocked"} label={lastDaemonControl?.error || (lastDaemonControl?.ok ? "ok" : "blocked")} />
            </div>
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-2 mt-3">
              <div className="rounded px-2 py-1.5" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                <div className="text-[9px] font-semibold mb-1" style={{ color: "var(--mis-muted)" }}>{copy.firstSafeCommands}</div>
                <div className="flex flex-col gap-1">
                  {lastDaemonAdmissionCommands.slice(0, 4).map((command, index) => (
                    <button
                      key={`daemon-admission:${index}:${command}`}
                      type="button"
                      onClick={() => void copyIntakeCommand(command)}
                      className="flex items-center gap-1 rounded px-1.5 py-0.5 text-left"
                      style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)", color: "var(--mis-text)" }}
                      title={command}
                    >
                      <Copy size={8} />
                      <span className="truncate text-[8px]">{copiedIntakeCommand === command ? copy.copiedCommand : command}</span>
                    </button>
                  ))}
                  {lastDaemonAdmissionCommands.length === 0 && (
                    <div className="text-[9px]" style={{ color: "var(--mis-muted)" }}>{copy.noRecommendedActions}</div>
                  )}
                </div>
              </div>
              <div className="rounded px-2 py-1.5" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                <div className="text-[9px] font-semibold mb-1" style={{ color: "var(--mis-muted)" }}>{copy.activeIntakeGate}</div>
                <div className="text-[10px] line-clamp-2" style={{ color: "var(--mis-dim)" }}>
                  {lastDaemonControl?.recommended_action || lastDaemonControl?.task_intake?.next_actions?.[0] || copy.workerStartBlockedHint}
                </div>
                <div className="flex flex-wrap gap-1 mt-2">
                  <StatusBadge status={lastDaemonAdmissionReadOnly ? "pass" : "attention"} label={copy.readOnlyProof} />
                  <StatusBadge status={lastDaemonAdmissionLedgerMutated ? "blocked" : "pass"} label={lastDaemonAdmissionLedgerMutated ? "ledger mutated" : "no ledger write"} />
                  <StatusBadge status={lastDaemonAdmissionSummary.token_omitted ? "pass" : "attention"} label="token omitted" />
                </div>
              </div>
            </div>
          </div>
        )}
        <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 gap-3 mt-4">
          {[
            { label: copy.workers, value: workerStatus?.worker_count ?? "—" },
            { label: copy.completedRuns, value: workerStatus?.recent_completed_runs ?? "—" },
            { label: copy.pendingTasks, value: workerStatus?.pending_worker_tasks ?? "—" },
            { label: copy.stuckTasks, value: workerStatus?.stuck_worker_tasks ?? "—", danger: (workerStatus?.stuck_worker_tasks || 0) > 0 },
          ].map((item) => (
            <div key={item.label} className="rounded-lg px-3 py-2" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
              <div className="text-[10px]" style={{ color: "var(--mis-muted)" }}>{item.label}</div>
              <div className="text-xs font-semibold truncate mt-1" style={{ color: item.danger ? "#F87171" : "var(--mis-text)" }}>{item.value}</div>
            </div>
          ))}
        </div>
        <div className="rounded-lg p-3 mt-3" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
          <div className="flex items-center justify-between gap-3">
            <div className="text-[11px] font-semibold" style={{ color: "var(--mis-text)" }}>{copy.stuckTasks}</div>
            <div className="text-[10px]" style={{ color: "var(--mis-muted)" }}>{copy.recentRun}: {workerStatus?.recent_runs?.[0]?.run_id || "—"}</div>
          </div>
          <div className="space-y-2 mt-2">
            {stuckTasks.length === 0 && (
              <div className="text-[11px] rounded px-3 py-2" style={{ color: "var(--mis-muted)", background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                {copy.noStuckTasks}
              </div>
            )}
            {stuckTasks.slice(0, 4).map(task => (
              <div key={task.task_id} className="grid grid-cols-1 lg:grid-cols-[1.1fr_0.8fr_0.9fr_auto] gap-3 items-start lg:items-center rounded px-3 py-2" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                <div className="min-w-0">
                  <div className="text-[11px] font-semibold truncate" style={{ color: "var(--mis-text)" }}>{task.title}</div>
                  <div className="text-[10px] truncate" style={{ color: "var(--mis-muted)" }}>{task.task_id} · {task.owner_agent_id || "—"}</div>
                </div>
                <div className="text-[10px] truncate" style={{ color: "var(--mis-dim)" }}>{copy.age}: {task.age_sec || 0}s</div>
                <div className="text-[10px] truncate" style={{ color: "var(--mis-dim)" }}>{copy.linkedRun}: {task.running_run_id || "—"}</div>
                <button
                  onClick={() => releaseStuckTask(task.task_id)}
                  disabled={Boolean(dispatching)}
                  className="flex items-center gap-1 text-[11px] px-2.5 py-1.5 rounded disabled:opacity-40"
                  style={{ background: "rgba(251,191,36,0.1)", color: "var(--mis-warning)", border: "1px solid rgba(251,191,36,0.22)" }}
                >
                  {dispatching === `release-${task.task_id}` ? <RefreshCw size={12} /> : <Square size={12} />}
                  {dispatching === `release-${task.task_id}` ? copy.releasingTask : copy.releaseTask}
                </button>
              </div>
            ))}
          </div>
        </div>
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-3 mt-3">
          {(workerStatus?.daemons || []).map((daemon) => (
            <div key={daemon.adapter} className="rounded-lg px-3 py-2" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
              <div className="flex items-center justify-between gap-2">
                <div className="text-[10px] uppercase" style={{ color: "var(--mis-muted)" }}>{daemon.adapter}</div>
                <div className="flex items-center gap-1.5">
                  {daemon.management_mode && <StatusBadge status={daemon.management_mode === "host_stack" ? "ready" : "planned"} label={daemon.management_mode === "host_stack" ? copy.hostManaged : copy.apiManaged} />}
                  {daemon.process_claim_active && <StatusBadge status={daemon.process_identity_verified ? "pass" : "attention"} label={daemon.process_identity_verified ? copy.processVerified : copy.processUnverified} />}
                  <StatusBadge status={daemon.running ? "running" : daemon.status} />
                </div>
              </div>
              <div className="text-[10px] mt-1 truncate" style={{ color: "var(--mis-dim)" }}>
                {copy.daemonStatus}: {daemon.worker_status || daemon.status}
              </div>
              <div className="text-[10px] mt-0.5 truncate" style={{ color: "var(--mis-muted)" }}>
                {copy.daemonBackoff}: {daemon.last_sleep_reason || copy.noBackoff}{daemon.last_sleep_sec ? ` · ${daemon.last_sleep_sec}s` : ""}
              </div>
              <div className="text-[10px] mt-0.5 truncate" style={{ color: "var(--mis-dim)" }}>
                {copy.pid}: {daemon.pid || "—"} · {daemon.agent_id || "—"}
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-1 mt-2">
                <div className="rounded px-1.5 py-1" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                  <div className="text-[9px]" style={{ color: "var(--mis-muted)" }}>{copy.processed}</div>
                  <div className="text-[10px] font-semibold" style={{ color: "var(--mis-text)" }}>{daemon.processed ?? 0}</div>
                </div>
                <div className="rounded px-1.5 py-1" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                  <div className="text-[9px]" style={{ color: "var(--mis-muted)" }}>{copy.iterations}</div>
                  <div className="text-[10px] font-semibold" style={{ color: "var(--mis-text)" }}>{daemon.iterations ?? 0}</div>
                </div>
                <div className="rounded px-1.5 py-1" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                  <div className="text-[9px]" style={{ color: "var(--mis-muted)" }}>{copy.errors}</div>
                  <div className="text-[10px] font-semibold" style={{ color: (daemon.consecutive_errors || 0) > 0 ? "#F87171" : "var(--mis-text)" }}>
                    {daemon.consecutive_errors ?? 0}/{daemon.total_errors ?? 0}
                  </div>
                </div>
              </div>
              {daemon.last_error && (
                <div className="text-[10px] mt-2 truncate" style={{ color: "#F87171" }}>
                  {copy.lastError}: {String(daemon.last_error.error_message || daemon.last_error.error_type || "error")}
                </div>
              )}
            </div>
          ))}
        </div>
      </div>

      <div
        className="rounded-xl p-4"
        style={{ background: "var(--mis-surface)", border: "1px solid var(--mis-border)" }}
      >
        <div className="flex flex-col lg:flex-row lg:items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-2">
              <Activity size={14} style={{ color: "var(--mis-cyan)" }} />
              <h2 className="text-sm font-semibold" style={{ color: "var(--mis-text)" }}>{copy.fleetTitle}</h2>
            </div>
            <p className="text-[11px] mt-1 max-w-2xl" style={{ color: "var(--mis-dim)" }}>{copy.fleetSummary}</p>
          </div>
          <StatusBadge status={workerFleet?.status || fleetHealth?.overall || "unknown"} label={String(fleetLaneSummary?.lane_count ?? 0)} />
        </div>

        <div className="rounded-lg p-3 mt-4" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
          <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-3">
            <div className="min-w-0">
              <div className="text-[11px] font-semibold" style={{ color: "var(--mis-text)" }}>{copy.fleetLanes}</div>
              <div className="text-[10px] mt-1 truncate" style={{ color: "var(--mis-muted)" }}>
                {workerFleet?.contract || fleetHealth?.contract || "Agent Gateway CLI/API"}
              </div>
            </div>
            <div className="flex flex-wrap gap-1.5">
              <StatusBadge status="ready" label={`${copy.daemonStatus}: ${fleetLaneSummary?.running_local_daemons ?? runningDaemons}/${fleetLaneSummary?.local_daemon_count ?? workerStatus?.daemons?.length ?? 0}`} />
              <StatusBadge status={(fleetLaneSummary?.stale_remote_enrollments || 0) > 0 ? "attention" : "ready"} label={`${copy.remoteWorkersTitle}: ${fleetLaneSummary?.remote_worker_count ?? workerStatus?.remote_worker_count ?? 0}`} />
              <StatusBadge status={(fleetLaneSummary?.active_remote_sessions || 0) > 0 ? "ready" : "planned"} label={`${copy.activeSessions}: ${fleetLaneSummary?.active_remote_sessions ?? workerStatus?.active_remote_sessions ?? 0}`} />
            </div>
          </div>
          <div className="space-y-2 mt-3">
            {fleetLanes.length === 0 && (
              <div className="text-[11px] rounded px-3 py-2" style={{ color: "var(--mis-muted)", background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                {copy.noFleetLanes}
              </div>
            )}
            {fleetLanes.slice(0, 8).map((lane) => {
              const healthStatus = lane.health === "pass" ? "ready" : lane.health === "warn" ? "attention" : lane.health === "fail" ? "blocked" : "planned";
              return (
                <div key={lane.lane_id} className="grid grid-cols-1 xl:grid-cols-[1.1fr_0.75fr_0.75fr_1fr] gap-2 rounded px-3 py-2" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                  <div className="min-w-0">
                    <div className="flex items-center gap-2 min-w-0">
                      <div className="text-[11px] font-semibold truncate" style={{ color: "var(--mis-text)" }}>{lane.agent_name || lane.agent_id || lane.lane_id}</div>
                      <StatusBadge status={healthStatus} label={lane.health} />
                    </div>
                    <div className="text-[10px] truncate mt-1" style={{ color: "var(--mis-muted)" }}>
                      {copy.laneType}: {lane.lane_type} · {lane.adapter || lane.runtime_type || "—"} · {lane.safe_ref || "—"}
                    </div>
                  </div>
                  <div className="text-[10px]" style={{ color: "var(--mis-dim)" }}>
                    <div>{copy.laneHeartbeat}: {lane.heartbeat_state || "—"}</div>
                    <div className="truncate">{copy.laneLastSeen}: {lane.last_seen_at || "—"}</div>
                  </div>
                  <div className="text-[10px]" style={{ color: "var(--mis-dim)" }}>
                    <div>{copy.laneSession}: {lane.session_state || "—"}</div>
                    <div>{copy.activeSessions}: {lane.active_session_count ?? 0}</div>
                  </div>
                  <div className="text-[10px] truncate xl:text-right" style={{ color: "var(--mis-cyan)" }}>
                    {copy.laneNextAction}: {lane.next_action || "agentops worker status"}
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        <div className="mt-4 flex flex-col lg:flex-row lg:items-start justify-between gap-4">
          <div>
            <div className="text-[11px] font-semibold" style={{ color: "var(--mis-text)" }}>{copy.daemonLogs}</div>
            <p className="text-[10px] mt-1 max-w-2xl" style={{ color: "var(--mis-muted)" }}>{copy.fleetSummary}</p>
          </div>
          <div className="flex gap-1.5">
            {WORKER_ADAPTERS.map(adapter => (
              <button
                key={adapter}
                onClick={() => {
                  setSelectedLogAdapter(adapter);
                  setDaemonLogsOpen(true);
                }}
                className="text-[11px] px-3 py-1.5 rounded"
                style={{
                  background: selectedLogAdapter === adapter ? "rgba(34,211,238,0.14)" : "var(--mis-surface2)",
                  color: selectedLogAdapter === adapter ? "var(--mis-cyan)" : "var(--mis-muted)",
                  border: "1px solid var(--mis-border)",
                }}
              >
                {adapter}
              </button>
            ))}
            <button
              onClick={() => {
                if (!daemonLogsOpen) {
                  setDaemonLogsOpen(true);
                } else {
                  void loadSelectedDaemonLog(selectedLogAdapter);
                }
              }}
              disabled={daemonLogsLoading}
              className="inline-flex items-center gap-1.5 text-[11px] px-3 py-1.5 rounded disabled:opacity-50"
              style={{ color: "var(--mis-cyan)", background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}
            >
              {daemonLogsLoading ? <RefreshCw size={11} /> : <Activity size={11} />}
              {daemonLogsLoading ? copy.daemonLogsLoading : daemonLogsOpen ? copy.refreshDaemonLogs : copy.openDaemonLogs}
            </button>
          </div>
        </div>

        <div className="grid grid-cols-1 xl:grid-cols-2 gap-4 mt-4">
          <div className="rounded-lg p-3 min-w-0" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
            <div className="flex items-center justify-between gap-2">
              <div className="text-[11px] font-semibold" style={{ color: "var(--mis-text)" }}>{copy.daemonLogs}</div>
              <StatusBadge status={selectedDaemonLog?.running ? "running" : selectedDaemonLog?.status || "unknown"} />
            </div>
            {!daemonLogsOpen && (
              <div className="text-[10px] mt-2 rounded px-2 py-1.5" style={{ color: "var(--mis-muted)", background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                {copy.daemonLogsLazyHint}
              </div>
            )}
            {daemonLogsError && (
              <div className="text-[10px] mt-2 rounded px-2 py-1.5" style={{ color: "#F87171", background: "rgba(248,113,113,0.08)", border: "1px solid rgba(248,113,113,0.18)" }}>
                {copy.lastError}: {daemonLogsError}
              </div>
            )}
            <div className="text-[10px] mt-1 truncate" style={{ color: "var(--mis-muted)" }}>
              {copy.logPath}: {selectedDaemonLog?.log_path || "—"}
            </div>
            <div className="text-[10px] mt-1 truncate" style={{ color: "var(--mis-muted)" }}>
              {copy.statePath}: {selectedDaemonLog?.state_path || "—"}
            </div>
            <div className="text-[10px] mt-1 truncate" style={{ color: "var(--mis-muted)" }}>
              {copy.daemonBackoff}: {selectedDaemonLog?.last_sleep_reason || copy.noBackoff}{selectedDaemonLog?.last_sleep_sec ? ` · ${selectedDaemonLog.last_sleep_sec}s` : ""}
            </div>
            {selectedDaemonLog?.last_error && (
              <div className="text-[10px] mt-2 rounded px-2 py-1" style={{ color: "#F87171", background: "rgba(248,113,113,0.08)", border: "1px solid rgba(248,113,113,0.18)" }}>
                {copy.lastError}: {String(selectedDaemonLog.last_error.error_message || selectedDaemonLog.last_error.error_type || "error")}
              </div>
            )}
            <pre
              className="mt-3 h-44 overflow-auto rounded p-3 text-[10px] leading-relaxed whitespace-pre-wrap"
              style={{ background: "var(--mis-bg)", color: "var(--mis-dim)", border: "1px solid var(--mis-border)" }}
            >
              {daemonLogsLoading
                ? copy.daemonLogsLoading
                : !daemonLogsOpen
                  ? copy.daemonLogsLazyHint
                  : (selectedDaemonLog?.log_tail || []).length > 0
                    ? selectedDaemonLog?.log_tail?.slice(-28).join("\n")
                    : copy.noLogs}
            </pre>
          </div>

          <div className="rounded-lg p-3 min-w-0" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
            <div className="text-[11px] font-semibold mb-2" style={{ color: "var(--mis-text)" }}>{copy.recentEvents}</div>
            <div className="space-y-2 max-h-60 overflow-auto pr-1">
              {recentEvents.length === 0 && (
                <div className="text-[11px] rounded px-3 py-3" style={{ color: "var(--mis-muted)", background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                  {copy.noEvents}
                </div>
              )}
              {recentEvents.slice(0, 8).map((event, index) => (
                <div key={`${eventText(event, "runtime_event_id", String(index))}-${index}`} className="rounded px-3 py-2" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                  <div className="flex items-center justify-between gap-3">
                    <div className="text-[11px] font-semibold truncate" style={{ color: "var(--mis-text)" }}>
                      {eventText(event, "event_type")}
                    </div>
                    <StatusBadge status={eventText(event, "status", "unknown")} />
                  </div>
                  <div className="text-[10px] mt-1 truncate" style={{ color: "var(--mis-dim)" }}>
                    {copy.eventAgent}: {eventText(event, "agent_id")} · {eventText(event, "created_at")}
                  </div>
                  <div className="text-[10px] mt-1 line-clamp-2" style={{ color: "var(--mis-muted)" }}>
                    {eventText(event, "output_summary", eventText(event, "input_summary"))}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>

      <div
        className="rounded-xl p-4"
        style={{ background: "var(--mis-surface)", border: "1px solid var(--mis-border)" }}
      >
        <div className="flex flex-col lg:flex-row lg:items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-2">
              <KeyRound size={14} style={{ color: "var(--mis-cyan)" }} />
              <h2 className="text-sm font-semibold" style={{ color: "var(--mis-text)" }}>{copy.enrollmentTitle}</h2>
            </div>
            <p className="text-[11px] mt-1 max-w-2xl" style={{ color: "var(--mis-dim)" }}>{copy.enrollmentSummary}</p>
            {enrollmentResult && (
              <div
                className="text-[11px] mt-2"
                style={{ color: enrollmentResult.includes("Error") || enrollmentResult.includes("error") ? "#F87171" : "var(--mis-success)" }}
              >
                {enrollmentResult}
              </div>
            )}
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 shrink-0 w-full sm:w-auto">
            <div className="rounded-lg px-3 py-2 min-w-28" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
              <div className="text-[10px]" style={{ color: "var(--mis-muted)" }}>{copy.activeEnrollments}</div>
              <div className="text-sm font-semibold mt-1" style={{ color: "var(--mis-text)" }}>{activeEnrollments}</div>
            </div>
            <div className="rounded-lg px-3 py-2 min-w-28" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
              <div className="text-[10px]" style={{ color: "var(--mis-muted)" }}>{copy.staleEnrollments}</div>
              <div className="text-sm font-semibold mt-1" style={{ color: staleEnrollments > 0 ? "var(--mis-warning)" : "var(--mis-text)" }}>{staleEnrollments}</div>
            </div>
            <div className="rounded-lg px-3 py-2 min-w-28 sm:col-span-2" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
              <div className="text-[10px]" style={{ color: "var(--mis-muted)" }}>{copy.activeSessions}</div>
              <div className="text-sm font-semibold mt-1" style={{ color: "var(--mis-text)" }}>{activeSessions}</div>
            </div>
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-6 gap-3 mt-4">
          <label className="md:col-span-2 text-[10px] uppercase" style={{ color: "var(--mis-muted)" }}>
            {copy.agentId}
            <input
              value={enrollmentForm.agent_id}
              onChange={(event) => updateEnrollmentForm("agent_id", event.target.value)}
              className="mt-1 w-full rounded px-3 py-2 text-xs outline-none"
              style={{ background: "var(--mis-surface2)", color: "var(--mis-text)", border: "1px solid var(--mis-border)" }}
            />
          </label>
          <label className="md:col-span-2 text-[10px] uppercase" style={{ color: "var(--mis-muted)" }}>
            {copy.agentName}
            <input
              value={enrollmentForm.name}
              onChange={(event) => updateEnrollmentForm("name", event.target.value)}
              className="mt-1 w-full rounded px-3 py-2 text-xs outline-none"
              style={{ background: "var(--mis-surface2)", color: "var(--mis-text)", border: "1px solid var(--mis-border)" }}
            />
          </label>
          <label className="text-[10px] uppercase" style={{ color: "var(--mis-muted)" }}>
            {copy.runtime}
            <select
              value={enrollmentForm.runtime_type}
              onChange={(event) => updateEnrollmentForm("runtime_type", event.target.value)}
              className="mt-1 w-full rounded px-3 py-2 text-xs outline-none"
              style={{ background: "var(--mis-surface2)", color: "var(--mis-text)", border: "1px solid var(--mis-border)" }}
            >
              {["mock", "hermes", "openclaw", "codex", "claude_code", "openhands", "crewai", "langgraph"].map(runtime => (
                <option key={runtime} value={runtime}>{runtime}</option>
              ))}
            </select>
          </label>
          <label className="text-[10px] uppercase" style={{ color: "var(--mis-muted)" }}>
            {copy.workspace}
            <input
              value={enrollmentForm.workspace_id}
              onChange={(event) => updateEnrollmentForm("workspace_id", event.target.value)}
              className="mt-1 w-full rounded px-3 py-2 text-xs outline-none"
              style={{ background: "var(--mis-surface2)", color: "var(--mis-text)", border: "1px solid var(--mis-border)" }}
            />
          </label>
          <label className="text-[10px] uppercase" style={{ color: "var(--mis-muted)" }}>
            {copy.ttlDays}
            <input
              type="number"
              min="1"
              value={enrollmentForm.ttl_days}
              onChange={(event) => updateEnrollmentForm("ttl_days", event.target.value)}
              className="mt-1 w-full rounded px-3 py-2 text-xs outline-none"
              style={{ background: "var(--mis-surface2)", color: "var(--mis-text)", border: "1px solid var(--mis-border)" }}
            />
          </label>
          <label className="text-[10px] uppercase" style={{ color: "var(--mis-muted)" }}>
            {copy.heartbeat}
            <input
              type="number"
              min="30"
              value={enrollmentForm.heartbeat_timeout_sec}
              onChange={(event) => updateEnrollmentForm("heartbeat_timeout_sec", event.target.value)}
              className="mt-1 w-full rounded px-3 py-2 text-xs outline-none"
              style={{ background: "var(--mis-surface2)", color: "var(--mis-text)", border: "1px solid var(--mis-border)" }}
            />
          </label>
          <label className="md:col-span-4 text-[10px] uppercase" style={{ color: "var(--mis-muted)" }}>
            {copy.scopes}
            <input
              value={enrollmentForm.scopes}
              onChange={(event) => updateEnrollmentForm("scopes", event.target.value)}
              className="mt-1 w-full rounded px-3 py-2 text-xs outline-none"
              style={{ background: "var(--mis-surface2)", color: "var(--mis-text)", border: "1px solid var(--mis-border)" }}
            />
          </label>
          <div className="md:col-span-4 rounded-lg p-3" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
            <div className="flex flex-col lg:flex-row lg:items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <ShieldCheck size={13} style={{ color: enrollmentPolicy?.approval_recommended ? "var(--mis-warning)" : "var(--mis-success)" }} />
                  <div className="text-[11px] font-semibold" style={{ color: "var(--mis-text)" }}>{copy.enrollmentPolicyTitle}</div>
                  <StatusBadge status={enrollmentPolicy?.status || "unknown"} />
                </div>
                <p className="text-[10px] mt-1" style={{ color: "var(--mis-muted)" }}>{copy.enrollmentPolicySummary}</p>
              </div>
              <div className="flex flex-wrap gap-1.5 shrink-0">
                <StatusBadge status={enrollmentPolicy?.risk_level || "unknown"} label={`${copy.riskLevel}: ${enrollmentPolicy?.risk_level || "—"}`} />
                <StatusBadge status={enrollmentPolicy?.approval_recommended ? "attention" : "pass"} label={enrollmentPolicy?.approval_recommended ? copy.approvalPath : copy.directCreatePath} />
              </div>
            </div>
            {enrollmentPolicyError && (
              <div className="text-[10px] mt-2" style={{ color: "#F87171" }}>{enrollmentPolicyError}</div>
            )}
            {enrollmentPolicy && (
              <>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mt-3">
                  {[
                    { label: copy.policyType, value: enrollmentPolicy.policy, status: enrollmentPolicy.policy },
                    { label: copy.recommendedPath, value: enrollmentPolicy.recommended_path, status: enrollmentPolicy.approval_recommended ? "attention" : "pass" },
                    { label: copy.workerWriteScopes, value: enrollmentPolicy.worker_write_scopes.length, status: enrollmentPolicy.worker_write_scopes.length > 0 ? "ready" : "planned" },
                    { label: copy.privilegedScopes, value: enrollmentPolicy.privileged_scopes.length, status: enrollmentPolicy.privileged_scopes.length > 0 ? "attention" : "pass" },
                  ].map((item) => (
                    <div key={item.label} className="rounded px-2 py-1" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                      <div className="text-[9px]" style={{ color: "var(--mis-muted)" }}>{item.label}</div>
                      <div className="flex items-center justify-between gap-2 mt-0.5">
                        <div className="text-[10px] font-semibold truncate" style={{ color: "var(--mis-text)" }}>{item.value}</div>
                        <StatusBadge status={item.status} />
                      </div>
                    </div>
                  ))}
                </div>
                <div
                  data-testid="hosted-enrollment-policy-gate"
                  className="rounded px-2 py-2 mt-3"
                  style={{
                    background: enrollmentPolicy.production_security_requested ? "rgba(251,191,36,0.08)" : "var(--mis-bg)",
                    border: enrollmentPolicy.production_security_requested ? "1px solid rgba(251,191,36,0.18)" : "1px solid var(--mis-border)",
                  }}
                >
                  <div className="flex flex-wrap items-center gap-1.5">
                    <div className="text-[9px] uppercase tracking-wide mr-1" style={{ color: "var(--mis-muted)" }}>
                      {copy.enrollmentDeploymentPolicy}
                    </div>
                    <StatusBadge status={enrollmentPolicy.production_security_requested ? "attention" : "pass"} label={`${copy.deploymentMode}: ${enrollmentPolicy.deployment_mode}`} />
                    <StatusBadge status={enrollmentPolicy.direct_create_allowed ? "pass" : "attention"} label={`${copy.directCreateAllowed}: ${enrollmentPolicy.direct_create_allowed ? copy.yes : copy.no}`} />
                    <StatusBadge status={enrollmentPolicy.approval_request_required ? "attention" : "pass"} label={`${copy.approvalRequestRequired}: ${enrollmentPolicy.approval_request_required ? copy.yes : copy.no}`} />
                    <StatusBadge status={enrollmentPolicy.admin_key_configured ? "pass" : enrollmentPolicy.production_security_requested ? "fail" : "planned"} label={`${copy.adminKeyConfigured}: ${enrollmentPolicy.admin_key_configured ? copy.yes : copy.no}`} />
                  </div>
                  <div className="text-[10px] mt-2" style={{ color: "var(--mis-muted)" }}>
                    {enrollmentPolicy.deployment_policy_summary || copy.enrollmentDeploymentPolicySummary}
                  </div>
                </div>
                <div
                  data-testid="agent-gateway-scope-effects"
                  className="rounded px-2 py-2 mt-3"
                  style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}
                >
                  <div className="flex flex-col lg:flex-row lg:items-start justify-between gap-2">
                    <div className="min-w-0">
                      <div className="text-[10px] font-semibold" style={{ color: "var(--mis-text)" }}>{copy.scopeEffectsTitle}</div>
                      <div className="text-[10px] mt-1" style={{ color: "var(--mis-muted)" }}>{copy.scopeEffectsSummary}</div>
                    </div>
                    <div className="flex flex-wrap gap-1.5 lg:justify-end">
                      <StatusBadge status={selectedScopeWorkerViable ? "pass" : "attention"} label={`${copy.workerViability}: ${selectedScopeWorkerViable ? copy.workerViabilityReady : copy.workerViabilityBlocked}`} />
                      <StatusBadge status="pass" label={`${copy.scopeRbacProof}: 403`} />
                    </div>
                  </div>
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mt-3">
                    {scopeEffectRows.map(item => (
                      <div key={item.label} className="rounded px-2 py-1" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
                        <div className="text-[9px]" style={{ color: "var(--mis-muted)" }}>{item.label}</div>
                        <div className="flex items-center justify-between gap-2 mt-0.5">
                          <div className="text-[10px] font-semibold" style={{ color: "var(--mis-text)" }}>{item.value}</div>
                          <StatusBadge status={item.status} />
                        </div>
                      </div>
                    ))}
                  </div>
                  <div
                    data-testid="agent-gateway-worker-scope-readiness"
                    className="rounded px-2 py-1.5 mt-3"
                    style={{
                      background: selectedScopeWorkerViable ? "rgba(42,157,143,0.08)" : "rgba(251,191,36,0.08)",
                      border: selectedScopeWorkerViable ? "1px solid rgba(42,157,143,0.18)" : "1px solid rgba(251,191,36,0.18)",
                    }}
                  >
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span className="text-[9px] uppercase tracking-wide" style={{ color: "var(--mis-muted)" }}>{copy.requiredWorkerScopes}</span>
                      <StatusBadge status={selectedScopeWorkerViable ? "pass" : "attention"} label={selectedScopeWorkerViable ? copy.workerViabilityReady : `${copy.missingWorkerScopes}: ${missingSelectedWorkerScopes.length}`} />
                    </div>
                    {missingSelectedWorkerScopes.length > 0 && (
                      <div className="flex flex-wrap gap-1 mt-1.5">
                        {missingSelectedWorkerScopes.slice(0, 6).map(scope => (
                          <span key={`selected-missing-${scope}`} className="text-[9px] px-1.5 py-0.5 rounded" style={{ background: "var(--mis-bg)", color: "var(--mis-muted)", border: "1px solid var(--mis-border)" }}>
                            {scope}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
                <div className="flex flex-wrap gap-1.5 mt-3">
                  {enrollmentPolicy.invalid_scopes.slice(0, 4).map(scope => (
                    <span key={`invalid-${scope}`} className="text-[9px] px-1.5 py-0.5 rounded" style={{ background: "rgba(248,113,113,0.08)", color: "#F87171", border: "1px solid rgba(248,113,113,0.18)" }}>
                      {copy.invalidScopes}: {scope}
                    </span>
                  ))}
                  {enrollmentPolicy.privileged_scopes.slice(0, 4).map(scope => (
                    <span key={`privileged-${scope}`} className="text-[9px] px-1.5 py-0.5 rounded" style={{ background: "rgba(251,191,36,0.08)", color: "var(--mis-warning)", border: "1px solid rgba(251,191,36,0.18)" }}>
                      {scope}
                    </span>
                  ))}
                  {enrollmentPolicy.missing_worker_scopes.slice(0, 4).map(scope => (
                    <span key={`missing-${scope}`} className="text-[9px] px-1.5 py-0.5 rounded" style={{ background: "var(--mis-bg)", color: "var(--mis-muted)", border: "1px solid var(--mis-border)" }}>
                      {copy.missingWorkerScopes}: {scope}
                    </span>
                  ))}
                </div>
                <div className="text-[10px] mt-2 truncate" style={{ color: "var(--mis-muted)" }}>
                  {(enrollmentPolicy.next_actions || [])[0] || "agentops enrollment policy-preview"}
                </div>
              </>
            )}
          </div>
          <div className="md:col-span-2 flex items-end gap-2">
            <button
              onClick={requestEnrollment}
              disabled={Boolean(enrollmentAction) || !enrollmentForm.agent_id.trim() || !enrollmentForm.name.trim() || scopeList.length === 0}
              className="w-full flex items-center justify-center gap-1.5 text-[11px] px-3 py-2 rounded disabled:opacity-50"
              style={{ background: "rgba(45,212,191,0.12)", color: "var(--mis-success)", border: "1px solid rgba(45,212,191,0.22)" }}
            >
              {enrollmentAction === "request" ? <RefreshCw size={12} /> : <ShieldCheck size={12} />}
              {enrollmentAction === "request" ? copy.requestingEnrollment : copy.requestEnrollment}
            </button>
            <button
              onClick={createEnrollment}
              disabled={Boolean(enrollmentAction) || createEnrollmentBlockedByPolicy || !enrollmentForm.agent_id.trim() || !enrollmentForm.name.trim() || scopeList.length === 0}
              className="w-full flex items-center justify-center gap-1.5 text-[11px] px-3 py-2 rounded disabled:opacity-50"
              style={{ background: "rgba(34,211,238,0.12)", color: "var(--mis-cyan)", border: "1px solid rgba(34,211,238,0.2)" }}
              title={createEnrollmentBlockedByPolicy ? copy.enrollmentDeploymentPolicySummary : undefined}
            >
              {enrollmentAction === "create" ? <RefreshCw size={12} /> : <ShieldCheck size={12} />}
              {enrollmentAction === "create" ? copy.creatingToken : copy.createToken}
            </button>
          </div>
        </div>

        {(createdRequest || enrollmentApprovals.length > 0) && (
          <div className="rounded-lg p-3 mt-4" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
            <div className="flex items-center justify-between gap-3">
              <div>
                <div className="text-[11px] font-semibold" style={{ color: "var(--mis-text)" }}>{copy.approvalRequestTitle}</div>
                {createdRequest && (
                  <div className="text-[10px] mt-1" style={{ color: "var(--mis-dim)" }}>
                    {createdRequest.request.request_id} · {createdRequest.approval.approval_id} · {copy.tokenShownOnce.replace("Copy this token now. It will not be shown again.", "No token is issued until approval.").replace("请现在复制 token。页面不会再次显示原始 token。", "审批前不会发放 token。")}
                  </div>
                )}
              </div>
            <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-2 w-full sm:w-auto">
              <input
                value={issueApprovalId}
                onChange={(event) => setIssueApprovalId(event.target.value)}
                placeholder="approval_id"
                className="w-full sm:w-56 rounded px-3 py-2 text-xs outline-none"
                  style={{ background: "var(--mis-bg)", color: "var(--mis-text)", border: "1px solid var(--mis-border)" }}
                />
                <button
                  onClick={() => issueApprovedEnrollment()}
                  disabled={Boolean(enrollmentAction) || !issueApprovalId.trim()}
                  className="flex items-center justify-center gap-1.5 text-[11px] px-3 py-2 rounded disabled:opacity-50"
                  style={{ background: "rgba(251,191,36,0.1)", color: "var(--mis-warning)", border: "1px solid rgba(251,191,36,0.25)" }}
                >
                  {enrollmentAction?.startsWith("issue-") ? <RefreshCw size={12} /> : <KeyRound size={12} />}
                  {enrollmentAction?.startsWith("issue-") ? copy.issuingApproved : copy.issueApproved}
                </button>
              </div>
            </div>
            <div className="space-y-2 mt-3">
              {enrollmentApprovals.length === 0 && (
                <div className="text-[11px] rounded px-3 py-3" style={{ color: "var(--mis-muted)", background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                  {copy.noApprovalRequests}
                </div>
              )}
              {enrollmentApprovals.slice(0, 5).map((approval) => (
                <div key={approval.approval_id} className="grid grid-cols-1 lg:grid-cols-[1.1fr_1.3fr_0.8fr_auto] items-start lg:items-center gap-3 rounded px-3 py-2" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                  <div className="min-w-0">
                    <div className="text-[11px] font-semibold truncate" style={{ color: "var(--mis-text)" }}>{approval.requested_by_agent_id}</div>
                    <div className="text-[10px] truncate" style={{ color: "var(--mis-muted)" }}>{approval.approval_id}</div>
                  </div>
                  <div className="text-[10px] truncate" style={{ color: "var(--mis-dim)" }}>{approval.reason}</div>
                  <StatusBadge status={approval.decision} />
                  <div className="flex flex-wrap justify-start lg:justify-end gap-1.5">
                    <button
                      onClick={() => decideEnrollmentApproval(approval.approval_id, "approve")}
                      disabled={approval.decision !== "pending" || Boolean(enrollmentAction)}
                      className="text-[11px] px-2.5 py-1.5 rounded disabled:opacity-40"
                      style={{ background: "rgba(45,212,191,0.12)", color: "var(--mis-success)", border: "1px solid rgba(45,212,191,0.2)" }}
                    >
                      {enrollmentAction === `approve-${approval.approval_id}` ? copy.creatingToken : copy.approveRequest}
                    </button>
                    <button
                      onClick={() => decideEnrollmentApproval(approval.approval_id, "reject")}
                      disabled={approval.decision !== "pending" || Boolean(enrollmentAction)}
                      className="text-[11px] px-2.5 py-1.5 rounded disabled:opacity-40"
                      style={{ background: "rgba(248,113,113,0.1)", color: "#F87171", border: "1px solid rgba(248,113,113,0.22)" }}
                    >
                      {enrollmentAction === `reject-${approval.approval_id}` ? copy.revokingToken : copy.rejectRequest}
                    </button>
                    <button
                      onClick={() => issueApprovedEnrollment(approval.approval_id)}
                      disabled={approval.decision !== "approved" || Boolean(enrollmentAction)}
                      className="text-[11px] px-2.5 py-1.5 rounded disabled:opacity-40"
                      style={{ background: "rgba(251,191,36,0.1)", color: "var(--mis-warning)", border: "1px solid rgba(251,191,36,0.22)" }}
                    >
                      {enrollmentAction === `issue-${approval.approval_id}` ? copy.issuingApproved : copy.issueApproved}
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        <div className="flex flex-wrap gap-1.5 mt-3">
          <span className="text-[10px] px-2 py-1" style={{ color: "var(--mis-muted)" }}>{copy.scopePresets}</span>
          {GATEWAY_SCOPE_PRESETS.map(preset => (
            <button
              key={preset.id}
              onClick={() => updateEnrollmentForm("scopes", preset.scopes.join(", "))}
              className="text-[10px] px-2 py-1 rounded"
              style={{ background: "rgba(122,90,248,0.1)", color: "#A78BFA", border: "1px solid rgba(122,90,248,0.2)" }}
            >
              {presetLabel(preset.id)}
            </button>
          ))}
          {validScopes.map(scope => (
            <button
              key={scope}
              onClick={() => {
                if (scopeList.includes(scope)) return;
                updateEnrollmentForm("scopes", [...scopeList, scope].join(", "));
              }}
              className="text-[10px] px-2 py-1 rounded"
              style={{ background: scopeList.includes(scope) ? "rgba(45,212,191,0.12)" : "var(--mis-surface2)", color: scopeList.includes(scope) ? "var(--mis-success)" : "var(--mis-muted)", border: "1px solid var(--mis-border)" }}
            >
              {scope}
            </button>
          ))}
        </div>

        {createdToken && (
          <div data-testid="one-time-issued-credential" className="rounded-lg p-3 mt-4" style={{ background: "rgba(251,191,36,0.08)", border: "1px solid rgba(251,191,36,0.28)" }}>
            <div className="flex flex-col md:flex-row md:items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="text-[11px] font-semibold" style={{ color: "var(--mis-warning)" }}>{copy.oneTimeCredentialTitle}</div>
                <div className="text-[10px] mt-1" style={{ color: "var(--mis-muted)" }}>{copy.tokenShownOnce}</div>
                <div className="text-[10px] mt-1" style={{ color: "var(--mis-dim)" }}>{copy.credentialCannotBeReadAgain}</div>
              </div>
              <div className="flex flex-wrap gap-1.5 shrink-0">
                <button
                  onClick={() => void copyIssuedCredential()}
                  disabled={!createdToken.token}
                  className="flex items-center gap-1 text-[11px] px-2.5 py-1.5 rounded"
                  style={{ background: "rgba(251,191,36,0.12)", color: "var(--mis-warning)", border: "1px solid rgba(251,191,36,0.24)", opacity: createdToken.token ? 1 : 0.72 }}
                >
                  <Copy size={12} />
                  {issuedCredentialCopied ? copy.copiedIssuedCredential : copy.copyIssuedCredential}
                </button>
                <button
                  onClick={clearIssuedCredential}
                  className="flex items-center gap-1 text-[11px] px-2.5 py-1.5 rounded"
                  style={{ background: "rgba(248,113,113,0.1)", color: "#F87171", border: "1px solid rgba(248,113,113,0.22)" }}
                >
                  <Trash2 size={12} />
                  {copy.clearIssuedCredential}
                </button>
              </div>
            </div>
            {createdToken.token && !issuedCredentialCopied && (
              <div data-testid="issued-credential-secret" className="mt-2 text-[11px] font-mono break-all" style={{ color: "var(--mis-text)" }}>{createdToken.token}</div>
            )}
            <div className="mt-2 text-[10px]" style={{ color: "var(--mis-dim)" }}>
              {createdToken.agent_id} · {createdToken.token_id} · {copy.expires}: {createdToken.expires_at}
            </div>
            {createdToken.next_steps && (
              <div className="mt-4 rounded-lg p-3" style={{ background: "var(--mis-bg)", border: "1px solid rgba(251,191,36,0.18)" }}>
                <div className="text-[11px] font-semibold" style={{ color: "var(--mis-text)" }}>{copy.launchPacket}</div>
                <div className="text-[10px] mt-1" style={{ color: "var(--mis-muted)" }}>
                  {createdToken.next_steps.token_policy}
                </div>
                <div className="grid grid-cols-1 xl:grid-cols-2 gap-3 mt-3">
                  <div className="min-w-0">
                    <div className="text-[10px] mb-1" style={{ color: "var(--mis-muted)" }}>{copy.envSetup}</div>
                    <pre className="rounded p-2 text-[10px] whitespace-pre-wrap break-all" style={{ background: "var(--mis-surface2)", color: "var(--mis-dim)", border: "1px solid var(--mis-border)" }}>
                      {createdToken.next_steps.env.join("\n")}
                    </pre>
                  </div>
                  <div className="min-w-0 space-y-2">
                    {[
                      { label: copy.installCommand, value: createdToken.next_steps.install || "" },
                      { label: copy.verifyCommand, value: createdToken.next_steps.verify },
                      { label: copy.startCheckCommand, value: createdToken.next_steps.start_check || "" },
                      { label: copy.loopLaunchBriefCommand, value: createdToken.next_steps.loop_launch_brief || "" },
                      { label: copy.preflightCommand, value: createdToken.next_steps.preflight || "" },
                      { label: copy.sessionCommand, value: createdToken.next_steps.session || "" },
                      { label: copy.heartbeatCommand, value: createdToken.next_steps.heartbeat },
                      { label: copy.runOnceCommand, value: createdToken.next_steps.run_once },
                      { label: copy.runLoopCommand, value: createdToken.next_steps.run_loop },
                      { label: copy.launchdTemplate, value: createdToken.next_steps.launchd_template || "" },
                      { label: copy.systemdTemplate, value: createdToken.next_steps.systemd_template || "" },
                      { label: copy.fallbackCommand, value: createdToken.next_steps.repo_fallback_run_once || "" },
                    ].filter(item => item.value).map(item => (
                      <div key={item.label}>
                        <div className="text-[10px] mb-1" style={{ color: "var(--mis-muted)" }}>{item.label}</div>
                        <code className="block rounded px-2 py-1 text-[10px] break-all" style={{ background: "var(--mis-surface2)", color: "var(--mis-cyan)", border: "1px solid var(--mis-border)" }}>
                          {item.value}
                        </code>
                      </div>
                    ))}
                    {createdToken.next_steps.method_gate_contract && (
                      <div>
                        <div className="text-[10px] mb-1" style={{ color: "var(--mis-muted)" }}>{copy.methodGateContract}</div>
                        <code className="block rounded px-2 py-1 text-[10px] break-all" style={{ background: "var(--mis-surface2)", color: "var(--mis-cyan)", border: "1px solid var(--mis-border)" }}>
                          {(createdToken.next_steps.method_gate_contract.required_gates || []).join(" -> ")}
                        </code>
                      </div>
                    )}
                  </div>
                </div>
              </div>
            )}
          </div>
        )}

        <div className="mt-4">
          <div className="text-[11px] font-semibold mb-2" style={{ color: "var(--mis-text)" }}>{copy.recentEnrollments}</div>
          <div className="space-y-2">
            {enrollments.length === 0 && (
              <div className="text-[11px] rounded-lg px-3 py-3" style={{ color: "var(--mis-muted)", background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
                {copy.noEnrollments}
              </div>
            )}
            {enrollments.slice(0, 6).map((item) => {
              const tokenActionRef = item.token_id || item.agent_id;
              const tokenDisplayRef = item.token_ref || item.token_id || "—";
              return (
              <div key={item.token_ref || item.token_id || `${item.agent_id}-${item.created_at}`} className="grid grid-cols-1 xl:grid-cols-[1.2fr_1.1fr_0.9fr_1.3fr_auto] gap-3 items-start xl:items-center rounded-lg px-3 py-2" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
                <div className="min-w-0">
                  <div className="text-[11px] font-semibold truncate" style={{ color: "var(--mis-text)" }}>{item.agent_id}</div>
                  <div className="text-[10px] truncate" style={{ color: "var(--mis-muted)" }}>{copy.tokenId}: {tokenDisplayRef}</div>
                </div>
                <div className="flex items-center gap-2 min-w-0">
                  <StatusBadge status={item.status} />
                  <StatusBadge status={item.heartbeat_state} />
                </div>
                <div className="text-[10px] truncate" style={{ color: "var(--mis-dim)" }}>
                  {copy.lastHeartbeat}: {item.last_heartbeat_at || "—"}
                </div>
                <div className="flex flex-wrap gap-1 min-w-0">
                  {item.scopes.slice(0, 4).map(scope => (
                    <span key={scope} className="text-[10px] px-1.5 py-0.5 rounded" style={{ background: "rgba(34,211,238,0.08)", color: "var(--mis-cyan)" }}>
                      {scope}
                    </span>
                  ))}
                  {item.scopes.length > 4 && (
                    <span className="text-[10px] px-1.5 py-0.5 rounded" style={{ background: "var(--mis-surface)", color: "var(--mis-muted)" }}>
                      +{item.scopes.length - 4}
                    </span>
                  )}
                </div>
                <div className="flex flex-wrap gap-1.5 justify-start xl:justify-end">
                  <button
                    onClick={() => rotateEnrollment(item.token_id, item.agent_id)}
                    disabled={item.status !== "active" || Boolean(enrollmentAction)}
                    className="flex items-center gap-1 text-[11px] px-2.5 py-1.5 rounded disabled:opacity-40"
                    style={{ background: "rgba(34,211,238,0.1)", color: "var(--mis-cyan)", border: "1px solid rgba(34,211,238,0.22)" }}
                  >
                    {enrollmentAction === `rotate-${tokenActionRef}` ? <RefreshCw size={12} /> : <RotateCw size={12} />}
                    {enrollmentAction === `rotate-${tokenActionRef}` ? copy.rotatingToken : copy.rotateToken}
                  </button>
                  <button
                    onClick={() => revokeEnrollment(item.token_id, item.agent_id)}
                    disabled={item.status !== "active" || Boolean(enrollmentAction)}
                    className="flex items-center gap-1 text-[11px] px-2.5 py-1.5 rounded disabled:opacity-40"
                    style={{ background: "rgba(248,113,113,0.1)", color: "#F87171", border: "1px solid rgba(248,113,113,0.22)" }}
                  >
                    {enrollmentAction === `revoke-${tokenActionRef}` ? <RefreshCw size={12} /> : <Trash2 size={12} />}
                    {enrollmentAction === `revoke-${tokenActionRef}` ? copy.revokingToken : copy.revokeToken}
                  </button>
                </div>
              </div>
              );
            })}
          </div>
        </div>

        <div className="mt-4">
          <div className="text-[11px] font-semibold mb-2" style={{ color: "var(--mis-text)" }}>{copy.recentSessions}</div>
          <div className="space-y-2">
            {sessions.length === 0 && (
              <div className="text-[11px] rounded-lg px-3 py-3" style={{ color: "var(--mis-muted)", background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
                {copy.noSessions}
              </div>
            )}
            {sessions.slice(0, 6).map((item) => {
              const sessionActionRef = item.session_id || item.agent_id;
              const sessionDisplayRef = item.session_ref || item.session_id || "—";
              return (
              <div key={item.session_ref || item.session_id || `${item.agent_id}-${item.created_at}`} className="grid grid-cols-1 xl:grid-cols-[1.1fr_1fr_1.1fr_1.3fr_auto] gap-3 items-start xl:items-center rounded-lg px-3 py-2" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
                <div className="min-w-0">
                  <div className="text-[11px] font-semibold truncate" style={{ color: "var(--mis-text)" }}>{item.agent_id}</div>
                  <div className="text-[10px] truncate" style={{ color: "var(--mis-muted)" }}>{copy.sessionId}: {sessionDisplayRef}</div>
                </div>
                <div className="flex items-center gap-2 min-w-0">
                  <StatusBadge status={item.session_state} />
                  {item.session_state !== item.status && <StatusBadge status={item.status} />}
                </div>
                <div className="text-[10px] truncate" style={{ color: "var(--mis-dim)" }}>
                  {copy.lastUsed}: {item.last_used_at || "—"}
                </div>
                <div className="min-w-0">
                  <div className="text-[10px] truncate" style={{ color: "var(--mis-muted)" }}>
                    {copy.parentToken}: {item.parent_token_ref || item.parent_token_id || "—"}
                  </div>
                  <div className="flex flex-wrap gap-1 mt-1">
                    {item.scopes.slice(0, 3).map(scope => (
                      <span key={scope} className="text-[10px] px-1.5 py-0.5 rounded" style={{ background: "rgba(122,90,248,0.1)", color: "#A78BFA" }}>
                        {scope}
                      </span>
                    ))}
                    {item.scopes.length > 3 && (
                      <span className="text-[10px] px-1.5 py-0.5 rounded" style={{ background: "var(--mis-surface)", color: "var(--mis-muted)" }}>
                        +{item.scopes.length - 3}
                      </span>
                    )}
                  </div>
                </div>
                <div className="flex justify-start xl:justify-end">
                  <button
                    onClick={() => revokeSession(item.session_id, item.agent_id)}
                    disabled={item.session_state !== "active" || Boolean(enrollmentAction)}
                    className="flex items-center gap-1 text-[11px] px-2.5 py-1.5 rounded disabled:opacity-40"
                    style={{ background: "rgba(248,113,113,0.1)", color: "#F87171", border: "1px solid rgba(248,113,113,0.22)" }}
                  >
                    {enrollmentAction === `revoke-session-${sessionActionRef}` ? <RefreshCw size={12} /> : <Trash2 size={12} />}
                    {enrollmentAction === `revoke-session-${sessionActionRef}` ? copy.revokingSession : copy.revokeSession}
                  </button>
                </div>
              </div>
              );
            })}
          </div>
        </div>
      </div>

      {/* Agent cards grid */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {agents.map(agent => {
          const budgetPct = Math.min(100, (agent.budget_used_usd / agent.budget_limit_usd) * 100);
          const rtColor = RUNTIME_COLOR[agent.runtime_type] ?? "var(--mis-muted)";

          return (
            <Link
              key={agent.agent_id}
              to={`/admin/agents/${agent.agent_id}`}
              className="block rounded-xl p-5 hover:opacity-90 transition-opacity"
              style={{ background: "var(--mis-surface)", border: "1px solid var(--mis-border)" }}
            >
              {/* Top row */}
              <div className="flex items-start gap-3 mb-4">
                <div
                  className="w-10 h-10 rounded-xl flex items-center justify-center shrink-0"
                  style={{ background: `${rtColor}15`, color: rtColor }}
                >
                  <Bot size={18} />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-semibold" style={{ color: "var(--mis-text)" }}>{agent.name}</span>
                    <StatusBadge status={agent.status} />
                  </div>
                  <div className="text-[11px] mt-0.5" style={{ color: "var(--mis-muted)" }}>{agent.role}</div>
                </div>
              </div>

              {/* Runtime + model */}
              <div className="flex gap-3 mb-3">
                <span
                  className="text-[10px] px-2 py-0.5 rounded font-medium"
                  style={{ background: `${rtColor}15`, color: rtColor }}
                >
                  {agent.runtime_type}
                </span>
                <span className="text-[10px] px-2 py-0.5 rounded" style={{ background: "var(--mis-surface2)", color: "var(--mis-muted)" }}>
                  {agent.model_name || "—"}
                </span>
              </div>

              {/* Stats row */}
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 mb-3">
                <div>
                  <div className="text-[10px]" style={{ color: "var(--mis-muted)" }}>{copy.runs}</div>
                  <div className="text-xs font-semibold" style={{ color: "var(--mis-text)" }}>{agent.run_count}</div>
                </div>
                <div>
                  <div className="text-[10px]" style={{ color: "var(--mis-muted)" }}>{copy.success}</div>
                  <div
                    className="text-xs font-semibold"
                    style={{ color: agent.success_rate >= 0.8 ? "var(--mis-success)" : "var(--mis-warning)" }}
                  >
                    {Math.round(agent.success_rate * 100)}%
                  </div>
                </div>
                <div>
                  <div className="text-[10px]" style={{ color: "var(--mis-muted)" }}>{copy.approvals}</div>
                  <div className="text-xs font-semibold" style={{ color: agent.approval_count > 5 ? "#FBBF24" : "var(--mis-text)" }}>
                    {agent.approval_count}
                  </div>
                </div>
              </div>

              {/* Budget bar */}
              <div>
                <div className="flex items-center justify-between mb-1">
                  <span className="text-[10px]" style={{ color: "var(--mis-muted)" }}>{copy.budget}</span>
                  <span className="text-[10px]" style={{ color: "var(--mis-dim)" }}>
                    ${agent.budget_used_usd.toFixed(2)} / ${agent.budget_limit_usd}
                  </span>
                </div>
                <div className="h-1.5 rounded-full overflow-hidden" style={{ background: "var(--mis-border)" }}>
                  <div
                    className="h-1.5 rounded-full"
                    style={{
                      width: `${budgetPct}%`,
                      background: budgetPct > 80 ? "var(--mis-warning)" : "var(--mis-success)",
                    }}
                  />
                </div>
              </div>

              {/* Allowed tools preview */}
              <div className="flex flex-wrap gap-1 mt-3">
                {agent.allowed_tools.slice(0, 3).map(t => (
                  <span key={t} className="text-[10px] px-1.5 py-0.5 rounded" style={{ background: "var(--mis-surface2)", color: "var(--mis-muted)" }}>
                    {t}
                  </span>
                ))}
                {agent.allowed_tools.length > 3 && (
                  <span className="text-[10px] px-1.5 py-0.5 rounded" style={{ background: "var(--mis-surface2)", color: "var(--mis-muted)" }}>
                    +{agent.allowed_tools.length - 3} {copy.more}
                  </span>
                )}
              </div>
            </Link>
          );
        })}
      </div>
    </div>
  );
}
