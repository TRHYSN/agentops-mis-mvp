import { useMemo } from "react";
import { Link } from "react-router";
import {
  Activity,
  ArrowLeft,
  ArrowUpRight,
  Bot,
  CheckCircle2,
  CircleSlash2,
  ClipboardList,
  Code2,
  Database,
  GitBranch,
  LockKeyhole,
  Network,
  PackageCheck,
  Plug,
  RefreshCw,
  ShieldCheck,
  TerminalSquare,
} from "lucide-react";
import { ConnectorTopology } from "../connectors/ConnectorTopology";
import { StatusBadge } from "../shared/StatusBadge";
import {
  loadAgents,
  loadApprovals,
  loadRuns,
  loadRuntimeConnectors,
  loadToolCalls,
  loadWorkerAdapterReadiness,
  loadWorkerFleet,
  loadWorkerStatus,
  useLiveData,
} from "../../data/liveApi";
import { pick, usePreferences } from "../../context/PreferencesContext";

type SourceResult<T> =
  | { status: "ready"; data: T }
  | { status: "unavailable"; data: null; error: string };

interface CodexConnectionData {
  agents: SourceResult<Awaited<ReturnType<typeof loadAgents>>>;
  runs: SourceResult<Awaited<ReturnType<typeof loadRuns>>>;
  toolCalls: SourceResult<Awaited<ReturnType<typeof loadToolCalls>>>;
  approvals: SourceResult<Awaited<ReturnType<typeof loadApprovals>>>;
  workerStatus: SourceResult<Awaited<ReturnType<typeof loadWorkerStatus>>>;
  workerFleet: SourceResult<Awaited<ReturnType<typeof loadWorkerFleet>>>;
  adapterReadiness: SourceResult<Awaited<ReturnType<typeof loadWorkerAdapterReadiness>>>;
  connectors: SourceResult<Awaited<ReturnType<typeof loadRuntimeConnectors>>>;
}

async function settle<T>(loader: () => Promise<T>): Promise<SourceResult<T>> {
  try {
    return { status: "ready", data: await loader() };
  } catch (error) {
    const message = error instanceof Error ? error.message : "";
    const safeHttpError = message.match(/^(\d{3}\s+[^:]+)/)?.[1];
    return {
      status: "unavailable",
      data: null,
      error: safeHttpError || "request_failed",
    };
  }
}

async function loadCodexConnectionData(): Promise<CodexConnectionData> {
  const [agents, runs, toolCalls, approvals, workerStatus, workerFleet, adapterReadiness, connectors] = await Promise.all([
    settle(loadAgents),
    settle(() => loadRuns("limit=100")),
    settle(loadToolCalls),
    settle(loadApprovals),
    settle(loadWorkerStatus),
    settle(loadWorkerFleet),
    settle(loadWorkerAdapterReadiness),
    settle(loadRuntimeConnectors),
  ]);

  return { agents, runs, toolCalls, approvals, workerStatus, workerFleet, adapterReadiness, connectors };
}

function sourceState(result?: SourceResult<unknown>) {
  if (!result || result.status === "unavailable") return "unavailable";
  const payload = result.data;
  if (
    payload
    && typeof payload === "object"
    && !Array.isArray(payload)
    && String((payload as Record<string, unknown>).status || "").toLowerCase() === "unavailable"
  ) {
    return "unavailable";
  }
  return "ready";
}

function formatDate(value: string | null | undefined, locale: "en" | "zh") {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return new Intl.DateTimeFormat(locale === "zh" ? "zh-CN" : "en-US", {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(parsed);
}

function compactId(value: string) {
  if (value.length <= 20) return value;
  return `${value.slice(0, 10)}…${value.slice(-7)}`;
}

export function CodexConnection() {
  const { locale } = usePreferences();
  const { data, loading, refresh } = useLiveData(loadCodexConnectionData, []);

  const copy = pick(locale, {
    en: {
      backToConnectors: "Back to connectors",
      title: "Codex Connector",
      subtitle: "Bidirectional control between AgentOps MIS and Codex, with tasks, approvals and evidence kept in the MIS ledger.",
      refresh: "Refresh live data",
      loading: "Reading current AgentOps MIS state...",
      unavailableBanner: "The AgentOps MIS backend is unavailable. No connection or readiness is inferred.",
      degradedBanner: "Some live sources are unavailable. Available evidence is shown without filling the gaps.",
      readOnlySurface: "This browser surface is read-only",
      noCredentials: "Credentials are never shown",
      liveSources: "Live sources",
      codexWorkers: "Codex workers",
      codexRuns: "Codex runs",
      pendingApprovals: "Pending approvals",
      running: "running",
      observed: "observed",
      noEvidence: "no evidence",
      capabilityBoundary: "Capability boundary",
      capabilityBoundaryHint: "Implemented paths and current observations are deliberately separated.",
      inboundPath: "MIS → Codex execution path",
      inboundPathHint: "A governed MIS task reaches a scoped Codex worker; bounded run evidence returns to the authoritative ledger.",
      outboundPath: "Codex → MIS client path",
      outboundPathHint: "The installed AgentOps MIS plugin gives Codex a safe CLI/API workflow for pulling work and writing evidence back.",
      taskPlanNode: "Task + Agent Plan",
      taskPlanNodeDetail: "Human intent, acceptance criteria and immutable plan",
      connectorNode: "Codex connector",
      connectorNodeDetail: "Trust policy and adapter boundary",
      workerInstanceNode: "Worker instance",
      workerInstanceNodeDetail: "Scoped identity, heartbeat and runtime lane",
      codexRuntimeNode: "Codex runtime",
      codexRuntimeNodeDetail: "Real local or enrolled execution runtime",
      ledgerNode: "Run + evidence ledger",
      ledgerNodeDetail: "Tool, evaluation, artifact and audit readback",
      pluginNode: "MIS plugin / Skill",
      pluginNodeDetail: "Codex-side governed workflow package",
      cliApiNode: "AgentOps CLI / API",
      cliApiNodeDetail: "Authenticated bounded context and writeback",
      taskContextNode: "Task context",
      taskContextNodeDetail: "Pull, claim and bounded knowledge packet",
      approvalNode: "Approval wall",
      approvalNodeDetail: "Prepared action must be approved before sensitive writes",
      reviewNode: "Ledger + review",
      reviewNodeDetail: "Run evidence remains visible to human operators",
      readOnlyWorker: "Read-only Codex worker",
      readOnlyWorkerBody: "Can pull governed tasks, run Codex without workspace mutation, and write bounded ledger evidence.",
      workspaceWrite: "Governed workspace-write",
      workspaceWriteBody: "Uses an approved Agent Plan and exact PreparedAction in a managed detached worktree. Commit, merge, push and deploy are outside this authorization.",
      plugin: "AgentOps MIS plugin / Skill",
      pluginBody: "A Codex-installable package now drives the existing AgentOps CLI/API workflow. It never embeds credentials or lets an agent approve itself.",
      mcp: "Native MCP tools",
      mcpBody: "Native AgentOps MIS MCP tools are not implemented yet; the product plugin uses the real CLI/API contract.",
      implemented: "Implemented",
      approvalGated: "Approval-gated",
      notImplemented: "Not implemented",
      executionEvidence: "execution evidence",
      noExecutionEvidence: "No execution evidence observed",
      cliOnly: "CLI only",
      pluginPackaged: "Plugin packaged",
      pluginMissing: "Plugin unavailable",
      mcpPending: "MCP pending",
      observedState: "Observed Codex state",
      observedStateHint: "Rows come from the current Agent, Worker Fleet and Run Ledger APIs.",
      noCodexWorkers: "No Codex agent or fleet lane was returned.",
      worker: "Worker",
      latestRun: "Latest run",
      source: "Source",
      runtime: "Runtime",
      lastSeen: "Last seen",
      recentRuns: "Recent Codex runs",
      recentRunsHint: "Workspace-write is identified only when its verifier tool evidence exists.",
      noCodexRuns: "No Codex run was returned in the current ledger window.",
      run: "Run",
      task: "Task",
      status: "Status",
      modeEvidence: "Mode evidence",
      started: "Started",
      workspaceDiff: "workspace-write verified",
      codexRuntime: "Codex runtime",
      approval: "approval",
      open: "Open",
      readiness: "Adapter readiness",
      readinessHint: "Only adapters.codex from the live readiness response is shown here; no other adapter status is used as a substitute.",
      dedicatedReadiness: "Codex adapter readiness",
      unavailable: "Unavailable",
      cliPreflight: "CLI preflight exists",
      cliPreflightBody: "The backend attests the Codex binary without returning its raw path. This page remains observational and never launches the check itself.",
      binary: "Binary",
      version: "Version",
      officialBundle: "Official ChatGPT bundle",
      workspaceAttested: "Workspace-write attested",
      workspaceReady: "Workspace-write ready",
      confirmRun: "Confirm-run required",
      rawPathOmitted: "Raw binary path omitted",
      commercialReadiness: "Commercial boundary",
      localLoopback: "Local loopback worker",
      localLoopbackBody: "Uses the local Host authority directly. Do not add --use-session.",
      remoteEnrolled: "Remote enrolled worker",
      remoteEnrolledBody: "Uses a scoped enrollment and should refresh a short-lived session with --use-session.",
      commandUnavailable: "Command unavailable until the readiness endpoint returns it.",
      connectors: "Connector inventory",
      noConnector: "No Codex connector inventory row was returned.",
      connector: "Connector",
      trust: "Trust",
      observation: "Observation",
      dataSources: "Data-source truth",
      dataSourcesHint: "Each request fails closed independently; unavailable sources are not replaced with demo data.",
      agentsApi: "Agents",
      runsApi: "Run Ledger",
      toolsApi: "Tool Calls",
      approvalsApi: "Approvals",
      workerApi: "Worker Status",
      fleetApi: "Worker Fleet",
      readinessApi: "Codex Readiness",
      connectorsApi: "Connectors",
      workerConsole: "Worker console",
      approvalsInbox: "Approvals inbox",
      runLedger: "Run ledger",
      connectorRegistry: "Connector registry",
    },
    zh: {
      backToConnectors: "返回连接器",
      title: "Codex 连接器",
      subtitle: "AgentOps MIS 与 Codex 的双向控制入口；任务、审批与运行证据始终回到 MIS 权威账本。",
      refresh: "刷新真实数据",
      loading: "正在读取当前 AgentOps MIS 状态...",
      unavailableBanner: "AgentOps MIS 后端不可用。页面不会据此推断已连接或已就绪。",
      degradedBanner: "部分实时数据源不可用。页面只展示已取得的证据，不补造缺失状态。",
      readOnlySurface: "浏览器页面只读",
      noCredentials: "不展示任何凭据",
      liveSources: "实时数据源",
      codexWorkers: "Codex Worker",
      codexRuns: "Codex Run",
      pendingApprovals: "待审批",
      running: "运行中",
      observed: "已观测",
      noEvidence: "无证据",
      capabilityBoundary: "能力边界",
      capabilityBoundaryHint: "“代码已实现”和“当前实际观测”在这里严格分开。",
      inboundPath: "MIS → Codex 执行链路",
      inboundPathHint: "MIS 受治理任务进入受限 Codex Worker；运行证据沿反向链路回到权威账本。",
      outboundPath: "Codex → MIS 客户端链路",
      outboundPathHint: "安装 AgentOps MIS 插件后，Codex 可通过安全 CLI/API 流程领取任务并回写证据。",
      taskPlanNode: "任务 + Agent Plan",
      taskPlanNodeDetail: "人的目标、验收标准与不可变计划",
      connectorNode: "Codex 连接器",
      connectorNodeDetail: "信任策略与适配器边界",
      workerInstanceNode: "Worker 实例",
      workerInstanceNodeDetail: "受限身份、心跳与运行 lane",
      codexRuntimeNode: "Codex 运行时",
      codexRuntimeNodeDetail: "本机或已注册远程真实运行时",
      ledgerNode: "Run + 证据账本",
      ledgerNodeDetail: "工具、评估、产物与审计回读",
      pluginNode: "MIS 插件 / Skill",
      pluginNodeDetail: "Codex 侧受治理工作流包",
      cliApiNode: "AgentOps CLI / API",
      cliApiNodeDetail: "鉴权后的受限上下文与回写通道",
      taskContextNode: "任务上下文",
      taskContextNodeDetail: "拉取、领取和受限知识包",
      approvalNode: "审批墙",
      approvalNodeDetail: "敏感写入前必须批准精确 prepared action",
      reviewNode: "账本 + 人工复核",
      reviewNodeDetail: "Run 证据持续对操作员可见",
      readOnlyWorker: "只读 Codex Worker",
      readOnlyWorkerBody: "可以领取受治理任务、以只读方式运行 Codex，并向 MIS 写入受限账本证据。",
      workspaceWrite: "受审批 workspace-write",
      workspaceWriteBody: "必须绑定已批准 Agent Plan 和精确 PreparedAction，只能在托管的 detached worktree 内写入。Commit、merge、push 和 deploy 不在本次授权内。",
      plugin: "AgentOps MIS 插件 / Skill",
      pluginBody: "现已提供可安装到 Codex 的产品插件，并复用真实 AgentOps CLI/API 流程；插件不内置凭据，也不允许 Agent 自批。",
      mcp: "原生 MCP 工具",
      mcpBody: "AgentOps MIS 原生 MCP 工具尚未实现；当前产品插件走真实 CLI/API 合同。",
      implemented: "已实现",
      approvalGated: "受审批控制",
      notImplemented: "未实现",
      executionEvidence: "条执行证据",
      noExecutionEvidence: "未观测到执行证据",
      cliOnly: "仅 CLI",
      pluginPackaged: "插件已打包",
      pluginMissing: "插件不可用",
      mcpPending: "MCP 待实现",
      observedState: "Codex 实际状态",
      observedStateHint: "数据来自当前 Agents、Worker Fleet 与 Run Ledger API。",
      noCodexWorkers: "当前 API 没有返回 Codex Agent 或 Fleet lane。",
      worker: "Worker",
      latestRun: "最近 Run",
      source: "来源",
      runtime: "运行时",
      lastSeen: "最近观测",
      recentRuns: "最近 Codex Run",
      recentRunsHint: "只有存在 workspace diff verifier 工具证据时，页面才将 Run 标记为 workspace-write。",
      noCodexRuns: "当前账本窗口没有返回 Codex Run。",
      run: "Run",
      task: "任务",
      status: "状态",
      modeEvidence: "模式证据",
      started: "开始时间",
      workspaceDiff: "workspace-write 已验证",
      codexRuntime: "Codex runtime",
      approval: "审批",
      open: "打开",
      readiness: "Adapter 就绪",
      readinessHint: "这里只展示实时响应中的 adapters.codex；绝不使用其他 adapter 状态代替 Codex。",
      dedicatedReadiness: "Codex Adapter 就绪",
      unavailable: "不可用",
      cliPreflight: "CLI preflight 已存在",
      cliPreflightBody: "后端会校验 Codex binary，但不返回原始路径；该页面只做观测，不会自行启动检查。",
      binary: "Binary",
      version: "版本",
      officialBundle: "ChatGPT 官方 bundle",
      workspaceAttested: "Workspace-write 已证明",
      workspaceReady: "Workspace-write 就绪",
      confirmRun: "需要 confirm-run",
      rawPathOmitted: "原始 binary 路径已省略",
      commercialReadiness: "商业边界",
      localLoopback: "本机 loopback worker",
      localLoopbackBody: "直接使用本机 Host 权威，不得添加 --use-session。",
      remoteEnrolled: "远程已 enrollment worker",
      remoteEnrolledBody: "使用受限 enrollment，应通过 --use-session 刷新短期 session。",
      commandUnavailable: "就绪接口返回命令前不可用。",
      connectors: "连接器清单",
      noConnector: "当前连接器 API 没有返回 Codex 记录。",
      connector: "连接器",
      trust: "信任",
      observation: "观测级别",
      dataSources: "数据源实况",
      dataSourcesHint: "每个请求独立 fail-closed；不可用的数据源不会被演示数据替代。",
      agentsApi: "Agents",
      runsApi: "Run Ledger",
      toolsApi: "Tool Calls",
      approvalsApi: "Approvals",
      workerApi: "Worker Status",
      fleetApi: "Worker Fleet",
      readinessApi: "Codex Readiness",
      connectorsApi: "Connectors",
      workerConsole: "Worker 控制台",
      approvalsInbox: "审批箱",
      runLedger: "运行账本",
      connectorRegistry: "连接器登记",
    },
  });

  const derived = useMemo(() => {
    const agents = data?.agents.status === "ready"
      ? data.agents.data.filter((agent) => agent.runtime_type === "codex")
      : [];
    const runs = data?.runs.status === "ready"
      ? data.runs.data.filter((run) => run.runtime_type === "codex")
      : [];
    const codexRunIds = new Set(runs.map((run) => run.run_id));
    const toolCalls = data?.toolCalls.status === "ready"
      ? data.toolCalls.data.filter((tool) => codexRunIds.has(tool.run_id))
      : [];
    const workspaceWriteRunIds = new Set(
      toolCalls
        .filter((tool) => tool.tool_name === "agent_worker.codex.workspace_diff_verify")
        .map((tool) => tool.run_id),
    );
    const approvals = data?.approvals.status === "ready"
      ? data.approvals.data.filter((approval) => codexRunIds.has(approval.run_id))
      : [];
    const pendingApprovals = approvals.filter((approval) => approval.decision === "pending");
    const fleetLanes = data?.workerFleet.status === "ready"
      ? data.workerFleet.data.lanes.filter((lane) => lane.adapter === "codex" || lane.runtime_type === "codex")
      : [];
    const statusWorkers = data?.workerStatus.status === "ready"
      ? data.workerStatus.data.workers.filter((worker) => worker.runtime_type === "codex")
      : [];
    const workerMap = new Map(agents.map((agent) => [agent.agent_id, agent]));
    statusWorkers.forEach((worker) => workerMap.set(worker.agent_id, worker));
    const connectors = data?.connectors.status === "ready"
      ? data.connectors.data.filter((connector) => (
        `${connector.connector_id} ${connector.provider} ${connector.mode}`.toLowerCase().includes("codex")
      ))
      : [];
    const codexReadiness = data?.adapterReadiness.status === "ready"
      ? data.adapterReadiness.data.adapters.codex
      : null;
    const workers = [...workerMap.values()];
    const runningWorkers = workers.filter((worker) => worker.status === "running").length
      + fleetLanes.filter((lane) => lane.status === "running").length;

    return {
      workers,
      runningWorkers,
      runs,
      workspaceWriteRunIds,
      pendingApprovals,
      fleetLanes,
      connectors,
      codexReadiness,
    };
  }, [data]);

  const sourceRows = [
    { id: "agents", label: copy.agentsApi, result: data?.agents },
    { id: "runs", label: copy.runsApi, result: data?.runs },
    { id: "tools", label: copy.toolsApi, result: data?.toolCalls },
    { id: "approvals", label: copy.approvalsApi, result: data?.approvals },
    { id: "worker", label: copy.workerApi, result: data?.workerStatus },
    { id: "fleet", label: copy.fleetApi, result: data?.workerFleet },
    { id: "readiness", label: copy.readinessApi, result: data?.adapterReadiness },
    { id: "connectors", label: copy.connectorsApi, result: data?.connectors },
  ].map((item) => ({ ...item, state: sourceState(item.result) }));
  const readySources = sourceRows.filter((item) => item.state === "ready").length;
  const overallState = readySources === sourceRows.length
    ? "ready"
    : readySources === 0
      ? "unavailable"
      : "degraded";

  const capabilityRows = [
    {
      id: "read-only",
      icon: <Code2 size={17} />,
      title: copy.readOnlyWorker,
      body: copy.readOnlyWorkerBody,
      contractStatus: "implemented",
      contractLabel: copy.implemented,
      evidenceStatus: derived.codexReadiness?.readiness || "unavailable",
      evidenceLabel: derived.codexReadiness?.readiness || copy.noEvidence,
    },
    {
      id: "workspace-write",
      icon: <GitBranch size={17} />,
      title: copy.workspaceWrite,
      body: copy.workspaceWriteBody,
      contractStatus: "approval_required",
      contractLabel: copy.approvalGated,
      evidenceStatus: derived.workspaceWriteRunIds.size ? "verified" : "unknown",
      evidenceLabel: derived.workspaceWriteRunIds.size
        ? `${derived.workspaceWriteRunIds.size} ${copy.executionEvidence}`
        : copy.noExecutionEvidence,
    },
    {
      id: "plugin",
      icon: <PackageCheck size={17} />,
      title: copy.plugin,
      body: copy.pluginBody,
      contractStatus: derived.codexReadiness?.client_plugin?.packaged ? "implemented" : "unavailable",
      contractLabel: derived.codexReadiness?.client_plugin?.packaged ? copy.implemented : copy.notImplemented,
      evidenceStatus: derived.codexReadiness?.client_plugin?.skill_available ? "pass" : "unavailable",
      evidenceLabel: derived.codexReadiness?.client_plugin?.skill_available ? copy.pluginPackaged : copy.pluginMissing,
    },
    {
      id: "mcp",
      icon: <Network size={17} />,
      title: copy.mcp,
      body: copy.mcpBody,
      contractStatus: "unavailable",
      contractLabel: copy.notImplemented,
      evidenceStatus: "planned",
      evidenceLabel: copy.mcpPending,
    },
  ];
  const codexChecks = derived.codexReadiness?.checks || {};
  const remediationCommands = derived.codexReadiness?.remediation?.commands || [];
  const localLoopbackCandidate = remediationCommands.find((command) => command.phase === "run_read_only")?.command;
  const remoteEnrolledCandidate = remediationCommands.find((command) => command.phase === "run_remote_scoped")?.command;
  const localLoopbackCommand = localLoopbackCandidate && !localLoopbackCandidate.includes("--use-session")
    ? localLoopbackCandidate
    : undefined;
  const remoteEnrolledCommand = remoteEnrolledCandidate?.includes("--use-session")
    ? remoteEnrolledCandidate
    : undefined;
  const checkRows = [
    { label: copy.binary, value: codexChecks.binary_executable },
    { label: copy.version, value: codexChecks.version_ok },
    { label: copy.officialBundle, value: codexChecks.official_chatgpt_bundle },
    { label: copy.workspaceAttested, value: codexChecks.workspace_write_attested },
    { label: copy.workspaceReady, value: derived.codexReadiness?.workspace_write_ready },
    { label: copy.confirmRun, value: derived.codexReadiness?.requires_confirm_run },
    { label: copy.rawPathOmitted, value: codexChecks.raw_binary_path_omitted },
  ];
  const connector = derived.connectors[0];
  const primaryWorker = derived.workers[0];
  const primaryLane = derived.fleetLanes[0];
  const latestRun = derived.runs[0];
  const clientPlugin = derived.codexReadiness?.client_plugin;
  const runtimeVersion = typeof codexChecks.version_summary === "string" && codexChecks.version_summary
    ? codexChecks.version_summary
    : "codex";
  const inboundNodes = [
    {
      id: "task-plan",
      label: copy.taskPlanNode,
      value: latestRun?.task_id ? compactId(latestRun.task_id) : "governed task",
      detail: copy.taskPlanNodeDetail,
      status: latestRun ? "ready" : "unknown",
      icon: <ClipboardList size={15} />,
    },
    {
      id: "connector",
      label: copy.connectorNode,
      value: connector?.connector_id || derived.codexReadiness?.connector_id || "rtc_codex_local",
      detail: copy.connectorNodeDetail,
      status: connector?.status || derived.codexReadiness?.readiness || "unavailable",
      icon: <Plug size={15} />,
    },
    {
      id: "worker",
      label: copy.workerInstanceNode,
      value: primaryWorker?.name || primaryLane?.agent_name || primaryLane?.lane_id || "—",
      detail: copy.workerInstanceNodeDetail,
      status: primaryLane?.status || primaryWorker?.status || "unavailable",
      icon: <Bot size={15} />,
      to: "/workspace/workers",
    },
    {
      id: "runtime",
      label: copy.codexRuntimeNode,
      value: runtimeVersion,
      detail: copy.codexRuntimeNodeDetail,
      status: codexChecks.binary_executable === true ? "pass" : derived.codexReadiness?.readiness || "unavailable",
      icon: <TerminalSquare size={15} />,
    },
    {
      id: "ledger",
      label: copy.ledgerNode,
      value: latestRun ? compactId(latestRun.run_id) : "—",
      detail: copy.ledgerNodeDetail,
      status: latestRun?.status || "unknown",
      icon: <Database size={15} />,
      to: latestRun ? `/admin/runs/${encodeURIComponent(latestRun.run_id)}` : "/admin/runs",
    },
  ];
  const outboundNodes = [
    {
      id: "plugin",
      label: copy.pluginNode,
      value: clientPlugin?.package_name
        ? `${clientPlugin.package_name}@${clientPlugin.package_version || "0.1.0"}`
        : "agentops-mis",
      detail: copy.pluginNodeDetail,
      status: clientPlugin?.skill_available ? "pass" : "unavailable",
      icon: <PackageCheck size={15} />,
    },
    {
      id: "cli-api",
      label: copy.cliApiNode,
      value: clientPlugin?.connection_mode || "agentops_cli_api",
      detail: copy.cliApiNodeDetail,
      status: clientPlugin?.packaged ? "ready" : "unknown",
      icon: <Code2 size={15} />,
    },
    {
      id: "task-context",
      label: copy.taskContextNode,
      value: latestRun?.task_id ? compactId(latestRun.task_id) : "bounded packet",
      detail: copy.taskContextNodeDetail,
      status: clientPlugin?.skill_available ? "ready" : "unknown",
      icon: <ClipboardList size={15} />,
    },
    {
      id: "approval",
      label: copy.approvalNode,
      value: derived.pendingApprovals.length ? String(derived.pendingApprovals.length) : "0",
      detail: copy.approvalNodeDetail,
      status: derived.pendingApprovals.length ? "approval_required" : "pass",
      icon: <ShieldCheck size={15} />,
      to: "/workspace/approvals",
    },
    {
      id: "review",
      label: copy.reviewNode,
      value: latestRun ? compactId(latestRun.run_id) : "—",
      detail: copy.reviewNodeDetail,
      status: latestRun ? "ready" : "unknown",
      icon: <Database size={15} />,
      to: "/admin/runs",
    },
  ];

  return (
    <div className="w-full space-y-5">
      <header className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0">
          <Link
            to="/admin/connectors"
            className="mb-2 inline-flex items-center gap-1 text-[11px]"
            style={{ color: "var(--mis-muted)" }}
          >
            <ArrowLeft size={12} />
            {copy.backToConnectors}
          </Link>
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-lg font-semibold" style={{ color: "var(--mis-text)" }}>{copy.title}</h1>
            <StatusBadge status={overallState} />
            <StatusBadge status="pass" label={copy.readOnlySurface} />
          </div>
          <p className="mt-1 max-w-3xl text-xs leading-relaxed" style={{ color: "var(--mis-dim)" }}>
            {copy.subtitle}
          </p>
          <div className="mt-2 flex flex-wrap gap-1.5">
            <span className="inline-flex items-center gap-1 text-[10px]" style={{ color: "var(--mis-muted)" }}>
              <LockKeyhole size={11} /> {copy.noCredentials}
            </span>
            <span className="inline-flex items-center gap-1 text-[10px]" style={{ color: "var(--mis-muted)" }}>
              <ShieldCheck size={11} /> Agent Plan + PreparedAction
            </span>
          </div>
        </div>
        <button
          type="button"
          onClick={refresh}
          disabled={loading}
          className="inline-flex h-8 shrink-0 items-center justify-center gap-2 rounded px-3 text-xs disabled:opacity-50"
          style={{
            color: "var(--mis-text)",
            background: "var(--mis-surface)",
            border: "1px solid var(--mis-border)",
          }}
        >
          <RefreshCw size={13} className={loading ? "animate-spin" : ""} />
          {copy.refresh}
        </button>
      </header>

      {loading && !data && (
        <div className="rounded p-3 text-xs" style={{ color: "var(--mis-dim)", border: "1px solid var(--mis-border)" }}>
          {copy.loading}
        </div>
      )}

      {!loading && overallState !== "ready" && (
        <div
          className="flex items-start gap-2 rounded p-3 text-xs"
          style={{
            color: overallState === "unavailable" ? "#F87171" : "#FBBF24",
            background: overallState === "unavailable" ? "rgba(248,113,113,0.07)" : "rgba(251,191,36,0.07)",
            border: overallState === "unavailable" ? "1px solid rgba(248,113,113,0.18)" : "1px solid rgba(251,191,36,0.18)",
          }}
        >
          <CircleSlash2 size={14} className="mt-0.5 shrink-0" />
          {overallState === "unavailable" ? copy.unavailableBanner : copy.degradedBanner}
        </div>
      )}

      <div
        data-testid="codex-bidirectional-topology"
        className="space-y-5 border-y py-4"
        style={{ borderColor: "var(--mis-border)" }}
      >
        <ConnectorTopology
          label={copy.inboundPath}
          description={copy.inboundPathHint}
          nodes={inboundNodes}
        />
        <ConnectorTopology
          label={copy.outboundPath}
          description={copy.outboundPathHint}
          nodes={outboundNodes}
        />
      </div>

      <section className="grid grid-cols-2 gap-2 lg:grid-cols-4">
        {[
          { label: copy.liveSources, value: `${readySources}/${sourceRows.length}`, icon: <Activity size={15} /> },
          { label: copy.codexWorkers, value: derived.workers.length, icon: <Bot size={15} /> },
          { label: copy.codexRuns, value: derived.runs.length, icon: <TerminalSquare size={15} /> },
          { label: copy.pendingApprovals, value: derived.pendingApprovals.length, icon: <ShieldCheck size={15} /> },
        ].map((metric) => (
          <div
            key={metric.label}
            className="flex min-h-20 items-center gap-3 rounded p-3"
            style={{ background: "var(--mis-surface)", border: "1px solid var(--mis-border)" }}
          >
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded" style={{ color: "var(--mis-cyan)", background: "rgba(34,211,238,0.08)" }}>
              {metric.icon}
            </span>
            <div className="min-w-0">
              <div className="text-lg font-semibold" style={{ color: "var(--mis-text)" }}>{metric.value}</div>
              <div className="truncate text-[10px]" style={{ color: "var(--mis-muted)" }}>{metric.label}</div>
            </div>
          </div>
        ))}
      </section>

      <section>
        <div className="mb-3">
          <h2 className="text-sm font-semibold" style={{ color: "var(--mis-text)" }}>{copy.capabilityBoundary}</h2>
          <p className="mt-0.5 text-[11px]" style={{ color: "var(--mis-muted)" }}>{copy.capabilityBoundaryHint}</p>
        </div>
        <div className="grid grid-cols-1 gap-2 xl:grid-cols-2">
          {capabilityRows.map((row) => (
            <article
              key={row.id}
              className="grid min-h-32 grid-cols-[34px_minmax(0,1fr)] gap-3 rounded p-4"
              style={{ background: "var(--mis-surface)", border: "1px solid var(--mis-border)" }}
            >
              <span className="flex h-8 w-8 items-center justify-center rounded" style={{ color: "var(--mis-cyan)", background: "var(--mis-surface2)" }}>
                {row.icon}
              </span>
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-1.5">
                  <h3 className="text-xs font-semibold" style={{ color: "var(--mis-text)" }}>{row.title}</h3>
                  <StatusBadge status={row.contractStatus} label={row.contractLabel} />
                  <StatusBadge status={row.evidenceStatus} label={row.evidenceLabel} />
                </div>
                <p className="mt-2 text-[11px] leading-relaxed" style={{ color: "var(--mis-dim)" }}>{row.body}</p>
              </div>
            </article>
          ))}
        </div>
      </section>

      <section className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1.15fr)_minmax(320px,0.85fr)]">
        <div className="min-w-0">
          <div className="mb-3">
            <h2 className="text-sm font-semibold" style={{ color: "var(--mis-text)" }}>{copy.observedState}</h2>
            <p className="mt-0.5 text-[11px]" style={{ color: "var(--mis-muted)" }}>{copy.observedStateHint}</p>
          </div>
          <div className="overflow-x-auto rounded" style={{ border: "1px solid var(--mis-border)" }}>
            {derived.workers.length === 0 && derived.fleetLanes.length === 0 ? (
              <div className="p-4 text-xs" style={{ color: "var(--mis-muted)", background: "var(--mis-surface)" }}>
                {copy.noCodexWorkers}
              </div>
            ) : (
              <div className="min-w-[660px] divide-y" style={{ background: "var(--mis-surface)", borderColor: "var(--mis-border)" }}>
                <div
                  className="grid grid-cols-[minmax(0,1.25fr)_90px_minmax(120px,0.9fr)_minmax(120px,0.8fr)] gap-3 px-3 py-2 text-[10px] font-medium"
                  style={{ color: "var(--mis-muted)" }}
                >
                  <span>{copy.worker}</span>
                  <span>{copy.status}</span>
                  <span className="text-right">{copy.runtime}</span>
                  <span className="text-right">{copy.latestRun}</span>
                </div>
                {derived.workers.map((worker) => {
                  const lane = derived.fleetLanes.find((item) => item.agent_id === worker.agent_id);
                  const workerRun = derived.runs.find((run) => run.agent_id === worker.agent_id);
                  return (
                    <div key={worker.agent_id} className="grid grid-cols-[minmax(0,1.25fr)_90px_minmax(120px,0.9fr)_minmax(120px,0.8fr)] items-center gap-3 p-3 text-[11px]">
                      <div className="min-w-0">
                        <div className="truncate font-semibold" style={{ color: "var(--mis-text)" }}>{worker.name}</div>
                        <div className="truncate font-mono text-[10px]" style={{ color: "var(--mis-muted)" }}>{worker.agent_id}</div>
                      </div>
                      <div>
                        <StatusBadge status={lane?.status || worker.status} />
                      </div>
                      <div className="min-w-0 text-right" style={{ color: "var(--mis-dim)" }}>
                        <div className="truncate">{worker.model_provider}/{worker.model_name}</div>
                        <div className="truncate text-[10px]" style={{ color: "var(--mis-muted)" }}>
                          {lane ? formatDate(lane.last_seen_at, locale) : "/api/agents"}
                        </div>
                      </div>
                      <div className="min-w-0 text-right">
                        {workerRun ? (
                          <Link
                            to={`/admin/runs/${encodeURIComponent(workerRun.run_id)}`}
                            className="inline-flex max-w-full items-center gap-1 font-mono text-[10px]"
                            style={{ color: "var(--mis-cyan)" }}
                            title={workerRun.run_id}
                          >
                            <span className="truncate">{compactId(workerRun.run_id)}</span>
                            <ArrowUpRight size={10} className="shrink-0" />
                          </Link>
                        ) : (
                          <span style={{ color: "var(--mis-muted)" }}>—</span>
                        )}
                      </div>
                    </div>
                  );
                })}
                {derived.fleetLanes
                  .filter((lane) => !derived.workers.some((worker) => worker.agent_id === lane.agent_id))
                  .map((lane) => {
                    const laneRun = derived.runs.find((run) => run.agent_id === lane.agent_id);
                    return (
                      <div key={lane.lane_id} className="grid grid-cols-[minmax(0,1.25fr)_90px_minmax(120px,0.9fr)_minmax(120px,0.8fr)] items-center gap-3 p-3 text-[11px]">
                        <div className="min-w-0">
                          <div className="truncate font-semibold" style={{ color: "var(--mis-text)" }}>{lane.agent_name || lane.agent_id || lane.lane_id}</div>
                          <div className="truncate font-mono text-[10px]" style={{ color: "var(--mis-muted)" }}>{lane.lane_type}</div>
                        </div>
                        <div><StatusBadge status={lane.status} /></div>
                        <div className="min-w-0 text-right" style={{ color: "var(--mis-dim)" }}>
                          <div className="truncate">{lane.runtime_type || lane.adapter || "codex"}</div>
                          <div className="truncate text-[10px]" style={{ color: "var(--mis-muted)" }}>{formatDate(lane.last_seen_at, locale)}</div>
                        </div>
                        <div className="min-w-0 text-right">
                          {laneRun ? (
                            <Link
                              to={`/admin/runs/${encodeURIComponent(laneRun.run_id)}`}
                              className="inline-flex max-w-full items-center gap-1 font-mono text-[10px]"
                              style={{ color: "var(--mis-cyan)" }}
                              title={laneRun.run_id}
                            >
                              <span className="truncate">{compactId(laneRun.run_id)}</span>
                              <ArrowUpRight size={10} className="shrink-0" />
                            </Link>
                          ) : (
                            <span style={{ color: "var(--mis-muted)" }}>—</span>
                          )}
                        </div>
                      </div>
                    );
                  })}
              </div>
            )}
          </div>
        </div>

        <div className="min-w-0">
          <div className="mb-3">
            <h2 className="text-sm font-semibold" style={{ color: "var(--mis-text)" }}>{copy.readiness}</h2>
            <p className="mt-0.5 text-[11px]" style={{ color: "var(--mis-muted)" }}>{copy.readinessHint}</p>
          </div>
          <div className="space-y-2">
            <div className="rounded p-3" style={{ background: "var(--mis-surface)", border: "1px solid var(--mis-border)" }}>
              <div className="flex items-center justify-between gap-3">
                <span className="text-xs font-medium" style={{ color: "var(--mis-text)" }}>{copy.dedicatedReadiness}</span>
                <StatusBadge status={derived.codexReadiness?.readiness || "unavailable"} />
              </div>
              <div className="mt-2 flex flex-wrap gap-1.5">
                <StatusBadge status={derived.codexReadiness?.trust_status || "unknown"} label={`${copy.trust}: ${derived.codexReadiness?.trust_status || "—"}`} />
                <StatusBadge status="unknown" label={`${copy.observation}: ${derived.codexReadiness?.observation_level || "—"}`} />
                <StatusBadge status="unknown" label={`${copy.commercialReadiness}: ${derived.codexReadiness?.commercial_readiness || "—"}`} />
              </div>
              <p className="mt-2 text-[11px] leading-relaxed" style={{ color: "var(--mis-dim)" }}>{copy.cliPreflightBody}</p>
              <div className="mt-3 grid grid-cols-2 gap-1.5">
                {checkRows.map((check) => (
                  <div key={check.label} className="flex min-h-8 items-center justify-between gap-2 rounded px-2 py-1.5" style={{ background: "var(--mis-surface2)" }}>
                    <span className="truncate text-[10px]" style={{ color: "var(--mis-dim)" }}>{check.label}</span>
                    <StatusBadge status={check.value === true ? "pass" : check.value === false ? "fail" : "unknown"} />
                  </div>
                ))}
              </div>
              {typeof codexChecks.version_summary === "string" && codexChecks.version_summary && (
                <div className="mt-2 truncate font-mono text-[10px]" style={{ color: "var(--mis-muted)" }}>
                  {codexChecks.version_summary}
                </div>
              )}
              <div className="mt-3 flex items-center gap-2 border-t pt-3" style={{ borderColor: "var(--mis-border)" }}>
                <CheckCircle2 size={14} style={{ color: "var(--mis-success)" }} />
                <span className="text-xs font-medium" style={{ color: "var(--mis-text)" }}>{copy.cliPreflight}</span>
              </div>
              <code className="mt-2 block rounded px-2 py-1.5 text-[10px]" style={{ color: "var(--mis-cyan)", background: "var(--mis-bg)" }}>
                {remediationCommands.find((command) => command.phase === "preflight")?.command || "agentops worker preflight --adapter codex"}
              </code>
            </div>
            <div className="grid grid-cols-1 gap-2 lg:grid-cols-2">
              <div className="rounded p-3" style={{ background: "var(--mis-surface)", border: "1px solid var(--mis-border)" }}>
                <div className="flex items-center justify-between gap-2">
                  <span className="text-xs font-medium" style={{ color: "var(--mis-text)" }}>{copy.localLoopback}</span>
                  <StatusBadge status="unknown" label="no --use-session" />
                </div>
                <p className="mt-2 text-[10px] leading-relaxed" style={{ color: "var(--mis-dim)" }}>{copy.localLoopbackBody}</p>
                <code className="mt-2 block break-words rounded px-2 py-1.5 text-[9px]" style={{ color: "var(--mis-cyan)", background: "var(--mis-bg)" }}>
                  {localLoopbackCommand || copy.commandUnavailable}
                </code>
              </div>
              <div className="rounded p-3" style={{ background: "var(--mis-surface)", border: "1px solid var(--mis-border)" }}>
                <div className="flex items-center justify-between gap-2">
                  <span className="text-xs font-medium" style={{ color: "var(--mis-text)" }}>{copy.remoteEnrolled}</span>
                  <StatusBadge status="approval_required" label="--use-session" />
                </div>
                <p className="mt-2 text-[10px] leading-relaxed" style={{ color: "var(--mis-dim)" }}>{copy.remoteEnrolledBody}</p>
                <code className="mt-2 block break-words rounded px-2 py-1.5 text-[9px]" style={{ color: "var(--mis-cyan)", background: "var(--mis-bg)" }}>
                  {remoteEnrolledCommand || copy.commandUnavailable}
                </code>
              </div>
            </div>
            <div className="rounded p-3" style={{ background: "var(--mis-surface)", border: "1px solid var(--mis-border)" }}>
              <div className="flex items-center justify-between gap-3">
                <span className="text-xs font-medium" style={{ color: "var(--mis-text)" }}>{copy.connectors}</span>
                <StatusBadge status={derived.connectors.length ? "available" : "unavailable"} />
              </div>
              {derived.connectors.length ? (
                <div className="mt-2 space-y-2">
                  {derived.connectors.map((connector) => (
                    <div key={connector.connector_id} className="rounded p-2 text-[10px]" style={{ background: "var(--mis-bg)" }}>
                      <div className="flex items-center justify-between gap-2">
                        <span className="truncate font-medium" style={{ color: "var(--mis-text)" }}>{connector.provider || connector.connector_id}</span>
                        <StatusBadge status={connector.status} />
                      </div>
                      <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1" style={{ color: "var(--mis-muted)" }}>
                        <span>{copy.trust}: {connector.trust_status || "—"}</span>
                        <span>{copy.observation}: {connector.observation_level || "—"}</span>
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="mt-2 text-[11px]" style={{ color: "var(--mis-muted)" }}>{copy.noConnector}</p>
              )}
            </div>
          </div>
        </div>
      </section>

      <section>
        <div className="mb-3">
          <h2 className="text-sm font-semibold" style={{ color: "var(--mis-text)" }}>{copy.recentRuns}</h2>
          <p className="mt-0.5 text-[11px]" style={{ color: "var(--mis-muted)" }}>{copy.recentRunsHint}</p>
        </div>
        <div className="overflow-x-auto rounded" style={{ border: "1px solid var(--mis-border)" }}>
          {derived.runs.length === 0 ? (
            <div className="p-4 text-xs" style={{ color: "var(--mis-muted)", background: "var(--mis-surface)" }}>
              {copy.noCodexRuns}
            </div>
          ) : (
            <table className="w-full min-w-[760px] border-collapse text-left text-[11px]" style={{ background: "var(--mis-surface)" }}>
              <thead>
                <tr style={{ color: "var(--mis-muted)", borderBottom: "1px solid var(--mis-border)" }}>
                  {[copy.run, copy.task, copy.status, copy.modeEvidence, copy.started, ""].map((label, index) => (
                    <th key={`${label}-${index}`} className="px-3 py-2 font-medium">{label}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {derived.runs.slice(0, 8).map((run) => (
                  <tr key={run.run_id} style={{ borderBottom: "1px solid var(--mis-border)" }}>
                    <td className="px-3 py-2 font-mono" style={{ color: "var(--mis-text)" }} title={run.run_id}>{compactId(run.run_id)}</td>
                    <td className="px-3 py-2 font-mono" style={{ color: "var(--mis-dim)" }} title={run.task_id}>{compactId(run.task_id)}</td>
                    <td className="px-3 py-2"><StatusBadge status={run.status} /></td>
                    <td className="px-3 py-2">
                      <StatusBadge
                        status={derived.workspaceWriteRunIds.has(run.run_id) ? "verified" : "unknown"}
                        label={derived.workspaceWriteRunIds.has(run.run_id) ? copy.workspaceDiff : copy.codexRuntime}
                      />
                    </td>
                    <td className="px-3 py-2 whitespace-nowrap" style={{ color: "var(--mis-dim)" }}>{formatDate(run.started_at, locale)}</td>
                    <td className="px-3 py-2 text-right">
                      <Link to={`/admin/runs/${encodeURIComponent(run.run_id)}`} className="inline-flex items-center gap-1" style={{ color: "var(--mis-cyan)" }}>
                        {copy.open} <ArrowUpRight size={11} />
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </section>

      <section className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(320px,0.55fr)]">
        <div>
          <div className="mb-3">
            <h2 className="text-sm font-semibold" style={{ color: "var(--mis-text)" }}>{copy.dataSources}</h2>
            <p className="mt-0.5 text-[11px]" style={{ color: "var(--mis-muted)" }}>{copy.dataSourcesHint}</p>
          </div>
          <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
            {sourceRows.map((source) => (
              <div key={source.id} className="rounded p-3" style={{ background: "var(--mis-surface)", border: "1px solid var(--mis-border)" }}>
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate text-[10px]" style={{ color: "var(--mis-dim)" }}>{source.label}</span>
                  <StatusBadge status={source.state} />
                </div>
                {source.result?.status === "unavailable" && (
                  <div className="mt-2 truncate font-mono text-[9px]" style={{ color: "var(--mis-muted)" }}>{source.result.error}</div>
                )}
              </div>
            ))}
          </div>
        </div>
        <nav className="grid grid-cols-2 gap-2 content-start">
          {[
            { label: copy.workerConsole, to: "/workspace/workers" },
            { label: copy.approvalsInbox, to: "/workspace/approvals" },
            { label: copy.runLedger, to: "/admin/runs" },
            { label: copy.connectorRegistry, to: "/admin/connectors" },
          ].map((item) => (
            <Link
              key={item.to}
              to={item.to}
              className="flex min-h-10 items-center justify-between gap-2 rounded px-3 text-xs"
              style={{ color: "var(--mis-text)", background: "var(--mis-surface)", border: "1px solid var(--mis-border)" }}
            >
              {item.label}
              <ArrowUpRight size={12} style={{ color: "var(--mis-muted)" }} />
            </Link>
          ))}
        </nav>
      </section>
    </div>
  );
}
