import { useState } from "react";
import { Link } from "react-router";
import {
  Activity,
  AlertTriangle,
  Bot,
  CheckCircle2,
  Copy,
  Play,
  Power,
  RefreshCw,
  RotateCw,
  ShieldCheck,
  Square,
  TerminalSquare,
} from "lucide-react";
import { StatusBadge } from "../shared/StatusBadge";
import {
  dispatchLocalWorkerOnce,
  loadOperatorExecutionMode,
  loadOperatorStartCheck,
  loadLocalReadiness,
  loadWorkerAdapterReadiness,
  loadWorkerFleet,
  loadWorkerFleetHygiene,
  loadWorkerStatus,
  recordOperatorActionControlReadback,
  recordOperatorActionReceipt,
  applyWorkerFleetHygiene,
  restartLocalWorkerDaemon,
  startLocalWorkerDaemon,
  stopLocalWorkerDaemon,
  useLiveData,
  type OperatorExecutionModePayload,
  type OperatorStartCheckPayload,
  type LocalReadinessPayload,
  type LocalRunPathStep,
  type WorkerAdapterName,
  type WorkerAdapterReadinessPayload,
  type WorkerDaemonResult,
  type WorkerDispatchResult,
  type WorkerFleetHygienePayload,
  type WorkerFleetPayload,
  type WorkerStatusPayload,
} from "../../data/liveApi";
import { pick, usePreferences } from "../../context/PreferencesContext";

const WORKER_ADAPTERS: WorkerAdapterName[] = ["mock", "hermes", "openclaw"];

interface WorkerConsoleData {
  workerStatus: WorkerStatusPayload;
  workerFleet: WorkerFleetPayload;
  fleetHygiene: WorkerFleetHygienePayload;
  adapterReadiness: WorkerAdapterReadinessPayload;
  executionMode: OperatorExecutionModePayload;
  startCheck: OperatorStartCheckPayload;
  localReadiness: LocalReadinessPayload;
}

function adapterColor(adapter: string) {
  if (adapter === "hermes") return "#2E86AB";
  if (adapter === "openclaw") return "#2A9D8F";
  return "var(--mis-muted)";
}

function commandLabel(command: string) {
  if (command.length <= 74) return command;
  return `${command.slice(0, 71)}...`;
}

export function WorkerConsole() {
  const { locale } = usePreferences();
  const [selectedAdapter, setSelectedAdapter] = useState<WorkerAdapterName>("mock");
  const [confirmRun, setConfirmRun] = useState(false);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [copiedCommand, setCopiedCommand] = useState<string | null>(null);
  const [lastDispatch, setLastDispatch] = useState<WorkerDispatchResult | null>(null);
  const [lastDaemonResult, setLastDaemonResult] = useState<WorkerDaemonResult | null>(null);
  const [lastHygieneResult, setLastHygieneResult] = useState<WorkerFleetHygienePayload | null>(null);
  const [confirmCleanup, setConfirmCleanup] = useState(false);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [receiptAction, setReceiptAction] = useState<string | null>(null);

  const copy = pick(locale, {
    en: {
      title: "Worker Control Console",
      subtitle: "Operate real AgentOps workers from one focused surface: mode, readiness, dispatch, daemon control, recovery signals and ledger links.",
      backToTeam: "Full AI Employees board",
      refresh: "Refresh",
      loading: "Loading live worker state...",
      unavailable: "Worker backend unavailable",
      selectedAdapter: "Selected adapter",
      confirmLive: "Confirm live run",
      confirmHint: "Hermes/OpenClaw stay disabled until this is explicit.",
      executionMode: "Execution Mode",
      adapterReadiness: "Adapter readiness",
      harnessProof: "Local harness proof",
      harnessProofSummary: "Read-only ledger proof for local task harness runs. Mock is CI/offline fallback; Hermes/OpenClaw proof applies only to returned run IDs.",
      realRuntimeProof: "Real runtime proof",
      mockFallback: "Mock fallback",
      latestPassing: "Latest passing",
      latestAttempt: "Latest attempt",
      proofClass: "Proof class",
      proofCommand: "Proof command",
      governedLaunch: "Governed launch",
      governedLaunchSummary: "Starts through Agent Gateway customer-worker dispatch when copied and run; this panel itself stays read-only.",
      readbackCommand: "Readback command",
      receiptCommand: "Receipt command",
      receiptStatus: "Receipt status",
      receiptCurrent: "Current receipts",
      receiptMissing: "Missing receipts",
      receiptStale: "Stale receipts",
      receiptNotRuntime: "receipt is not runtime success",
      freshnessHours: "Freshness",
      rawPromptOmitted: "raw prompt omitted",
      workerFleet: "Worker fleet",
      fleetHygiene: "Fleet hygiene",
      hygieneSummary: "Plan and apply cleanup for stuck worker tasks plus never-seen or stale remote enrollments. Apply requires explicit confirmation and never executes live runtimes.",
      previewHygiene: "Preview hygiene",
      applyHygiene: "Apply confirmed cleanup",
      confirmCleanup: "Confirm cleanup",
      confirmCleanupHint: "Releases stale running tasks, blocks linked stale runs, and revokes stale enrollments/sessions.",
      actionsAvailable: "Actions available",
      staleNeverSeen: "Never seen",
      staleHeartbeat: "Heartbeat stale",
      releasedTasks: "Released tasks",
      revokedEnrollments: "Revoked enrollments",
      hygieneReadOnly: "read-only plan",
      hygieneApplied: "cleanup applied",
      remoteLanes: "Remote lanes",
      localLanes: "Local lanes",
      daemonControl: "Daemon control",
      hostManaged: "Host managed",
      apiManaged: "Console managed",
      hostManagedHint: "Managed by AgentOps Host. Use the Host lifecycle controls.",
      controlBlockedHint: "Control is disabled until the claimed process identity is verified.",
      processVerified: "Process verified",
      processUnverified: "Process identity alert",
      dispatchOnce: "Dispatch once",
      startDaemon: "Start daemon",
      restartDaemon: "Restart daemon",
      stopDaemons: "Stop all daemons",
      runningDaemons: "Running daemons",
      executionCapacity: "Execution capacity",
      serviceWorkers: "Service workers",
      pendingTasks: "Pending tasks",
      stuckTasks: "Stuck tasks",
      completedRuns: "Completed runs",
      remoteWorkers: "Remote workers",
      activeSessions: "Active sessions",
      activeJobs: "Active jobs",
      approvals: "Approvals",
      confirmWall: "Confirm wall",
      approvalWall: "Approval wall",
      selectedPath: "Selected path",
      recommendedAction: "Recommended action",
      trust: "Trust",
      liveReady: "Live ready",
      target: "Target",
      remediation: "Remediation",
      copyCommand: "Copy command",
      copied: "Copied",
      noCommand: "No command",
      localInstallPacket: "Local install packet",
      installPacketSummary: "Copy-only start-check deployment packet for this adapter.",
      serviceInstallPreview: "Service install preview",
      confirmInstall: "Confirm install file",
      serviceCheck: "Service check",
      serviceControlAudit: "Service control audit",
      serviceControlAuditSummary: "Preview load/restart separately, then record the verified receipt and readback before treating the local loop as service-managed.",
      serviceControlPreview: "Control preview",
      verifiedReceipt: "Verified receipt",
      recordVerifiedReceipt: "Record verified receipt",
      recordControlReadback: "Record control readback",
      serviceManagedLoop: "Service-managed loop",
      managedLoopReady: "managed loop ready",
      activeLoopReady: "active loop ready",
      serviceLoaded: "service loaded",
      managedExecutionPath: "Managed execution path",
      managedExecutionSummary: "Service-managed readiness, Agent Plan, knowledge retrieval, dispatch, evidence report and review queue in one copy-only packet.",
      nextManagedStep: "Next managed step",
      dispatchCommand: "Dispatch command",
      evidenceReport: "Evidence report",
      reviewQueue: "Review queue",
      installedStatus: "installed",
      checkedStatus: "checked",
      receiptProof: "receipt",
      controlReadback: "readback",
      previewFirst: "preview first",
      noServiceLoad: "does not load service",
      noLedgerWrite: "no ledger write",
      receiptRequired: "receipt required",
      receiptAttached: "receipt attached",
      readbackAttached: "readback attached",
      firstSafeIncludesInstall: "install in first-safe",
      readOnlyProof: "read-only proof",
      noServerShell: "no server shell",
      noLiveExecution: "no live execution",
      tokenOmitted: "token omitted",
      openTask: "Open task",
      openRun: "Open run",
      runId: "Run",
      planEvidence: "Plan evidence",
      daemon: "Daemon",
      pid: "PID",
      processed: "Processed",
      errors: "Errors",
      lastSleep: "Backoff",
      recentTasks: "Recent worker tasks",
      recentRuns: "Recent worker runs",
      liveBlocked: "Live adapter needs explicit confirmation.",
      noStuckTasks: "No stuck worker tasks.",
    },
    zh: {
      title: "Worker 控制台",
      subtitle: "一个聚焦的真实 Worker 操作面：运行模式、adapter 就绪、一次性派发、常驻进程控制、恢复信号和账本链接。",
      backToTeam: "完整 AI 员工面板",
      refresh: "刷新",
      loading: "正在加载真实 worker 状态...",
      unavailable: "Worker 后端不可用",
      selectedAdapter: "当前 adapter",
      confirmLive: "确认真实运行",
      confirmHint: "Hermes/OpenClaw 必须显式确认后才可执行。",
      executionMode: "执行模式",
      adapterReadiness: "Adapter 就绪",
      harnessProof: "本地 Harness 证明",
      harnessProofSummary: "只读账本证明本地 task harness 运行。Mock 只是 CI/离线兜底；Hermes/OpenClaw 证明只针对返回的 run id。",
      realRuntimeProof: "真实运行证明",
      mockFallback: "Mock 兜底",
      latestPassing: "最近通过",
      latestAttempt: "最近尝试",
      proofClass: "证明类型",
      proofCommand: "证明命令",
      governedLaunch: "治理启动",
      governedLaunchSummary: "复制执行后会走 Agent Gateway customer-worker 派发；这个面板本身仍是只读。",
      readbackCommand: "回读命令",
      receiptCommand: "回执命令",
      receiptStatus: "回执状态",
      receiptCurrent: "当前回执",
      receiptMissing: "缺失回执",
      receiptStale: "陈旧回执",
      receiptNotRuntime: "回执不等于运行成功",
      freshnessHours: "新鲜度",
      rawPromptOmitted: "raw prompt 已省略",
      workerFleet: "Worker Fleet",
      fleetHygiene: "Fleet 清理",
      hygieneSummary: "规划并执行卡住 worker 任务、从未 heartbeat 或 heartbeat 过期远程 enrollment 的清理。应用清理必须显式确认，且不会执行真实 runtime。",
      previewHygiene: "预览清理计划",
      applyHygiene: "确认执行清理",
      confirmCleanup: "确认清理",
      confirmCleanupHint: "会释放 stale running 任务、阻断关联 stale run，并吊销 stale enrollment/session。",
      actionsAvailable: "可用动作",
      staleNeverSeen: "从未 heartbeat",
      staleHeartbeat: "Heartbeat 过期",
      releasedTasks: "已释放任务",
      revokedEnrollments: "已吊销 enrollment",
      hygieneReadOnly: "只读计划",
      hygieneApplied: "已执行清理",
      remoteLanes: "远程 lane",
      localLanes: "本地 lane",
      daemonControl: "常驻控制",
      hostManaged: "主机托管",
      apiManaged: "控制台托管",
      hostManagedHint: "由 AgentOps 主机托管，请使用主机生命周期控制。",
      controlBlockedHint: "当前进程身份尚未核验，控制操作已禁用。",
      processVerified: "进程已核验",
      processUnverified: "进程身份异常",
      dispatchOnce: "派发一次",
      startDaemon: "启动常驻",
      restartDaemon: "重启常驻",
      stopDaemons: "停止全部常驻",
      runningDaemons: "运行中的常驻",
      executionCapacity: "可用执行通道",
      serviceWorkers: "服务 Worker",
      pendingTasks: "待处理任务",
      stuckTasks: "卡住任务",
      completedRuns: "已完成运行",
      remoteWorkers: "远程 Worker",
      activeSessions: "活跃 session",
      activeJobs: "活跃 Job",
      approvals: "审批",
      confirmWall: "确认墙",
      approvalWall: "审批墙",
      selectedPath: "执行路径",
      recommendedAction: "建议动作",
      trust: "信任状态",
      liveReady: "真实可跑",
      target: "目标资源",
      remediation: "修复/准备命令",
      copyCommand: "复制命令",
      copied: "已复制",
      noCommand: "暂无命令",
      localInstallPacket: "本地安装包",
      installPacketSummary: "当前 adapter 的 start-check 只读部署包。",
      serviceInstallPreview: "安装预览",
      confirmInstall: "确认写服务文件",
      serviceCheck: "服务自检",
      serviceControlAudit: "服务控制审计",
      serviceControlAuditSummary: "加载/重启只给预览命令；真正把本地 loop 视为服务托管前，必须记录 verified receipt 和控制回读。",
      serviceControlPreview: "控制预览",
      verifiedReceipt: "验证回执",
      recordVerifiedReceipt: "记录验证回执",
      recordControlReadback: "记录控制回读",
      serviceManagedLoop: "服务托管 Loop",
      managedLoopReady: "托管 loop 就绪",
      activeLoopReady: "常驻 loop 已激活",
      serviceLoaded: "服务已加载",
      managedExecutionPath: "托管执行路径",
      managedExecutionSummary: "把服务就绪、Agent Plan、知识检索、派发、证据报告和 review queue 收进同一个只读 packet。",
      nextManagedStep: "下一步托管动作",
      dispatchCommand: "派发命令",
      evidenceReport: "证据报告",
      reviewQueue: "Review Queue",
      installedStatus: "安装状态",
      checkedStatus: "检查状态",
      receiptProof: "回执",
      controlReadback: "回读",
      previewFirst: "先预览",
      noServiceLoad: "不加载服务",
      noLedgerWrite: "不写控制账本",
      receiptRequired: "需要回执",
      receiptAttached: "回执已挂接",
      readbackAttached: "回读已挂接",
      firstSafeIncludesInstall: "first-safe 含安装",
      readOnlyProof: "只读证明",
      noServerShell: "服务端不执行 shell",
      noLiveExecution: "未触发真实运行",
      tokenOmitted: "token 已省略",
      openTask: "打开任务",
      openRun: "打开运行",
      runId: "运行",
      planEvidence: "计划证据",
      daemon: "常驻",
      pid: "PID",
      processed: "已处理",
      errors: "错误",
      lastSleep: "退避",
      recentTasks: "最近 Worker 任务",
      recentRuns: "最近 Worker 运行",
      liveBlocked: "真实 adapter 需要显式确认。",
      noStuckTasks: "没有卡住的 worker 任务。",
    },
  });

  const { data, loading, error, refresh } = useLiveData<WorkerConsoleData>(async () => {
    const [workerStatus, workerFleet, fleetHygiene, adapterReadiness, executionMode, startCheck, localReadiness] = await Promise.all([
      loadWorkerStatus(),
      loadWorkerFleet(),
      loadWorkerFleetHygiene({ limit: 8 }),
      loadWorkerAdapterReadiness(),
      loadOperatorExecutionMode(selectedAdapter, confirmRun, 8),
      loadOperatorStartCheck(selectedAdapter, 8),
      loadLocalReadiness(),
    ]);
    return { workerStatus, workerFleet, fleetHygiene, adapterReadiness, executionMode, startCheck, localReadiness };
  }, [selectedAdapter, confirmRun]);

  const workerStatus = data?.workerStatus;
  const workerFleet = data?.workerFleet;
  const fleetHygiene = lastHygieneResult || data?.fleetHygiene;
  const adapterReadiness = data?.adapterReadiness;
  const executionMode = data?.executionMode;
  const startCheck = data?.startCheck;
  const localReadiness = data?.localReadiness;
  const localHarnessProof = localReadiness?.local_harness_proof_readiness;
  const selectedRoute = executionMode?.selected_route;
  const selectedReadiness = adapterReadiness?.adapters?.[selectedAdapter];
  const liveBlocked = selectedAdapter !== "mock" && !confirmRun;
  const selectedDaemon = (workerStatus?.daemons || []).find((daemon) => daemon.adapter === selectedAdapter);
  const selectedHostManaged = Boolean(selectedDaemon?.running && selectedDaemon.management_mode === "host_stack");
  const hostManagedRunning = (workerStatus?.daemons || []).some((daemon) => daemon.running && daemon.management_mode === "host_stack");
  const selectedControlBlocked = Boolean(selectedDaemon?.process_claim_active && selectedDaemon.control_allowed === false);
  const anyControlBlocked = (workerStatus?.daemons || []).some((daemon) => daemon.process_claim_active && daemon.control_allowed === false);
  const primaryCommand = executionMode?.commands?.execution_mode
    || selectedRoute?.recommended_action
    || selectedReadiness?.remediation?.primary_next_action
    || "agentops operator execution-mode";

  const copyCommand = async (command?: string | null) => {
    const text = String(command || "");
    if (!text) return;
    await navigator.clipboard?.writeText(text);
    setCopiedCommand(text);
  };

  const runAction = async (action: string, handler: () => Promise<void>) => {
    setBusyAction(action);
    setActionMessage(null);
    try {
      await handler();
      await refresh();
    } catch (err) {
      setActionMessage(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyAction(null);
    }
  };

  const dispatchOnce = () => runAction(`dispatch:${selectedAdapter}`, async () => {
    const result = await dispatchLocalWorkerOnce({
      adapter: selectedAdapter,
      confirm_run: selectedAdapter !== "mock" && confirmRun,
      title: locale === "zh" ? `${selectedAdapter} worker 控制台任务` : `${selectedAdapter} worker console task`,
      description: locale === "zh"
        ? "从 Worker 控制台派发一次任务，验证 worker 能通过 Agent Gateway 执行并回写账本。"
        : "Dispatch one task from the Worker Console and verify Gateway writeback.",
      acceptance_criteria: "Worker writes run/tool/evaluation/audit/artifact evidence.",
    });
    setLastDispatch(result);
    setActionMessage(`${selectedAdapter}: ${result.ok ? "ok" : result.error || "failed"} · ${result.run_id || result.task_id}`);
  });

  const startDaemon = () => runAction(`start:${selectedAdapter}`, async () => {
    const result = await startLocalWorkerDaemon({
      adapter: selectedAdapter,
      confirm_run: selectedAdapter !== "mock" && confirmRun,
      poll_interval: 2,
      max_tasks: 0,
    });
    setLastDaemonResult(result);
    setActionMessage(`${selectedAdapter}: ${result.ok ? "ok" : result.error || "blocked"} · ${result.daemon?.pid || result.recommended_action || "daemon"}`);
  });

  const restartDaemon = () => runAction(`restart:${selectedAdapter}`, async () => {
    const result = await restartLocalWorkerDaemon({
      adapter: selectedAdapter,
      confirm_run: selectedAdapter !== "mock" && confirmRun,
      poll_interval: 2,
      max_tasks: 0,
    });
    setLastDaemonResult(result);
    setActionMessage(`${selectedAdapter}: ${result.ok ? "ok" : result.error || "blocked"} · ${result.daemon?.pid || result.recommended_action || "daemon"}`);
  });

  const stopAll = () => runAction("stop:all", async () => {
    const result = await stopLocalWorkerDaemon("all");
    setLastDaemonResult(result);
    setActionMessage(result.ok ? "stopped" : result.error || "stop failed");
  });

  const previewHygiene = () => runAction("hygiene:preview", async () => {
    const result = await loadWorkerFleetHygiene({ limit: 8 });
    setLastHygieneResult(result);
    setActionMessage(`${copy.fleetHygiene}: ${result.status} · ${copy.actionsAvailable}: ${result.summary.actions_available}`);
  });

  const applyHygiene = () => runAction("hygiene:apply", async () => {
    const result = await applyWorkerFleetHygiene({
      limit: 8,
      release_reason: "worker_console_confirmed_cleanup",
    });
    setLastHygieneResult(result);
    setConfirmCleanup(false);
    setActionMessage(`${copy.fleetHygiene}: ${result.status} · ${copy.releasedTasks}: ${result.summary.released_tasks ?? 0} · ${copy.revokedEnrollments}: ${result.summary.revoked_enrollments ?? 0}`);
  });

  const hygieneActionsAvailable = fleetHygiene?.summary.actions_available || 0;
  const remoteLaneCount = workerFleet?.summary.remote_worker_count ?? workerStatus?.remote_worker_count ?? 0;
  const localLaneCount = workerFleet?.summary.local_daemon_count ?? workerStatus?.worker_count ?? 0;
  const serviceWorkerLanes = (workerFleet?.lanes || []).filter((lane) => lane.lane_type === "gateway_service_worker");
  const localAdmissionPacket = (startCheck?.local_loop_admission_packet || {}) as Record<string, unknown>;
  const localDeployment = (typeof localAdmissionPacket.local_deployment === "object" && localAdmissionPacket.local_deployment !== null
    ? localAdmissionPacket.local_deployment
    : {}) as Record<string, unknown>;
  const serviceInstall = (typeof localDeployment.service_install === "object" && localDeployment.service_install !== null
    ? localDeployment.service_install
    : {}) as Record<string, unknown>;
  const serviceManagedLoop = (typeof localDeployment.service_managed_loop === "object" && localDeployment.service_managed_loop !== null
    ? localDeployment.service_managed_loop
    : {}) as Record<string, unknown>;
  const managedExecutionPath = (typeof localDeployment.managed_execution_path === "object" && localDeployment.managed_execution_path !== null
    ? localDeployment.managed_execution_path
    : {}) as Record<string, unknown>;
  const managedExecutionCommands = (typeof managedExecutionPath.commands === "object" && managedExecutionPath.commands !== null
    ? managedExecutionPath.commands
    : {}) as Record<string, unknown>;
  const managedExecutionFirstSafe = Array.isArray(managedExecutionPath.first_safe_commands)
    ? managedExecutionPath.first_safe_commands.map(String).filter(Boolean)
    : [];
  const managedExecutionVerify = Array.isArray(managedExecutionPath.verify_commands)
    ? managedExecutionPath.verify_commands.map(String).filter(Boolean)
    : [];
  const managedExecutionGates = Array.isArray(managedExecutionPath.gates)
    ? managedExecutionPath.gates.filter((gate): gate is Record<string, unknown> => typeof gate === "object" && gate !== null)
    : [];
  const managedExecutionStatus = String(managedExecutionPath.status || "attention");
  const managedExecutionReady = managedExecutionPath.service_managed_loop_ready === true;
  const serviceInstallCommands = [
    { label: copy.serviceInstallPreview, command: serviceInstall.preview_command, status: serviceInstall.preview_command ? "pass" : "attention", confirm: false },
    { label: copy.confirmInstall, command: serviceInstall.confirm_command, status: serviceInstall.confirm_command ? "attention" : "blocked", confirm: true },
    { label: copy.serviceCheck, command: serviceInstall.verify_command, status: serviceInstall.verify_command ? "pass" : "attention", confirm: false },
  ];
  const firstSafeCommands = Array.isArray(localAdmissionPacket.first_safe_commands)
    ? localAdmissionPacket.first_safe_commands.map(String).filter(Boolean)
    : [];
  const localRunPath = startCheck?.local_run_path || [];
  const serviceControlStep = localRunPath.find((step) => step.service_control_preview || step.step_id === "preview_worker_service_control");
  const serviceReceiptState = (serviceControlStep?.receipt_state || {}) as Record<string, unknown>;
  const serviceReceiptVerified = Boolean(serviceReceiptState.verified);
  const serviceReadbackAttached = Boolean(serviceReceiptState.control_readback_attached || serviceReceiptState.control_readback_id);
  const serviceReceiptStatus = String(serviceReceiptState.status || (serviceControlStep?.receipt_required ? "missing" : "not_required"));
  const serviceReceiptHash = String(serviceReceiptState.receipt_hash || serviceReceiptState.action_hash || serviceReceiptState.receipt_id || "").slice(0, 10);
  const firstSafeHasInstall = firstSafeCommands.slice(0, 8).some((command) => command.includes("service-install"));
  const serviceInstallSafety = [
    { label: copy.previewFirst, status: serviceInstall.preview_only_by_default === false ? "attention" : "pass" },
    { label: copy.noServiceLoad, status: serviceInstall.loads_service ? "attention" : "pass" },
    { label: copy.noServerShell, status: serviceInstall.server_executes_shell === false ? "pass" : "attention" },
    { label: copy.firstSafeIncludesInstall, status: firstSafeHasInstall ? "pass" : "attention" },
    { label: copy.tokenOmitted, status: serviceInstall.token_omitted === false ? "attention" : "pass" },
  ];
  const serviceControlSafety = [
    { label: copy.previewFirst, status: serviceControlStep?.service_control_preview ? "pass" : "attention" },
    { label: copy.noServerShell, status: serviceControlStep?.server_executes_shell === false ? "pass" : "attention" },
    { label: copy.noLedgerWrite, status: serviceControlStep?.writes_ledger ? "attention" : "pass" },
    { label: copy.receiptRequired, status: serviceControlStep?.receipt_required ? "attention" : "pass" },
    { label: copy.receiptAttached, status: serviceReceiptVerified ? "pass" : "attention" },
    { label: copy.readbackAttached, status: serviceReadbackAttached ? "pass" : "attention" },
  ];
  const serviceManagedReady = serviceManagedLoop.service_managed_loop_ready === true;
  const serviceActiveReady = serviceManagedLoop.service_active_loop_ready === true;
  const serviceLoaded = serviceManagedLoop.service_loaded === true;
  const serviceManagedStatus = String(serviceManagedLoop.status || (serviceManagedReady ? "ready" : "attention"));
  const serviceManagedFacts = [
    { label: copy.managedLoopReady, value: serviceManagedReady ? "yes" : "no", status: serviceManagedReady ? "pass" : "attention" },
    { label: copy.activeLoopReady, value: serviceActiveReady ? "yes" : "no", status: serviceActiveReady ? "pass" : "attention" },
    { label: copy.serviceLoaded, value: serviceLoaded ? "yes" : String(serviceManagedLoop.active_status || serviceManagedLoop.active_loop_status || "unknown"), status: serviceLoaded ? "pass" : "attention" },
    { label: copy.installedStatus, value: String(serviceManagedLoop.installed_status || "unverified"), status: serviceManagedReady ? "pass" : "attention" },
    { label: copy.checkedStatus, value: String(serviceManagedLoop.checked_status || "missing_readback"), status: serviceReadbackAttached ? "pass" : "attention" },
  ];
  const managedExecutionNextCommand = managedExecutionReady
    ? managedExecutionCommands.customer_worker_dispatch
    : serviceReceiptVerified
      ? managedExecutionCommands.service_control_readback
      : managedExecutionCommands.service_control_receipt;

  const recordServiceControlReceipt = (step: LocalRunPathStep) => runAction(`service-receipt:${step.step_id}`, async () => {
    setReceiptAction(`service-receipt:${step.step_id}`);
    try {
      const result = await recordOperatorActionReceipt({
        action_command: step.command,
        verify_command: step.verify_command || undefined,
        action_id: step.step_id,
        action_signature: step.action_signature || undefined,
        source: step.source || "ui.worker_console.service_control_preview",
        status: "verified",
        result_summary: `Worker Console verified local service-control preview ${step.step_id}.`,
      });
      setActionMessage(`${copy.verifiedReceipt}: ${result.status} · ${result.receipt?.receipt_id || ""}`);
    } finally {
      setReceiptAction(null);
    }
  });

  const recordServiceControlReadback = (step: LocalRunPathStep) => runAction(`service-readback:${step.step_id}`, async () => {
    setReceiptAction(`service-readback:${step.step_id}`);
    try {
      const state = step.receipt_state || {};
      let receiptId = String(state.receipt_id || "");
      if (!receiptId || !state.verified) {
        const receiptResult = await recordOperatorActionReceipt({
          action_command: step.command,
          verify_command: step.verify_command || undefined,
          action_id: step.step_id,
          action_signature: step.action_signature || undefined,
          source: step.source || "ui.worker_console.service_control_preview",
          status: "verified",
          result_summary: `Worker Console verified local service-control preview ${step.step_id} before control readback.`,
        });
        receiptId = receiptResult.receipt?.receipt_id || receiptId;
      }
      if (!receiptId) throw new Error("receipt_id_required");
      const result = await recordOperatorActionControlReadback({
        receipt_id: receiptId,
        source: `${step.source || "ui.worker_console.service_control_preview"}.control_readback`,
        control_readback: {
          before: {
            step_id: step.step_id,
            status: step.status,
            adapter: step.adapter || selectedAdapter,
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
          token_omitted: true,
        },
      });
      setActionMessage(`${copy.controlReadback}: ${result.status}`);
    } finally {
      setReceiptAction(null);
    }
  });

  const statCards = [
    { label: copy.executionCapacity, value: workerStatus?.execution_capacity_workers ?? workerFleet?.summary.execution_capacity_workers ?? "—", status: (workerStatus?.execution_capacity_workers || 0) > 0 ? "running" : "attention" },
    { label: copy.pendingTasks, value: workerStatus?.pending_worker_tasks ?? "—", status: (workerStatus?.pending_worker_tasks || 0) > 0 ? "planned" : "pass" },
    { label: copy.stuckTasks, value: workerStatus?.stuck_worker_tasks ?? "—", status: (workerStatus?.stuck_worker_tasks || 0) > 0 ? "attention" : "pass" },
    { label: copy.completedRuns, value: workerStatus?.recent_completed_runs ?? "—", status: "completed" },
    { label: copy.serviceWorkers, value: workerStatus?.active_service_workers ?? workerFleet?.summary.active_service_workers ?? "—", status: (workerStatus?.stale_service_workers || 0) > 0 ? "attention" : (workerStatus?.active_service_workers || 0) > 0 ? "running" : "planned" },
    { label: copy.activeSessions, value: workerStatus?.active_remote_sessions ?? "—", status: (workerStatus?.active_remote_sessions || 0) > 0 ? "ready" : "planned" },
    { label: copy.activeJobs, value: executionMode?.summary.active_workflow_jobs ?? workerStatus?.stuck_workflow_jobs ?? "—", status: (executionMode?.summary.active_workflow_jobs || 0) > 0 ? "running" : "pass" },
    { label: copy.approvals, value: executionMode?.summary.pending_approvals ?? "—", status: (executionMode?.summary.pending_approvals || 0) > 0 ? "attention" : "pass" },
  ];

  return (
    <div className="space-y-4 max-w-none">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <TerminalSquare size={18} style={{ color: "var(--mis-cyan)" }} />
            <h1 className="text-lg font-semibold" style={{ color: "var(--mis-text)" }}>{copy.title}</h1>
            <StatusBadge status={workerStatus?.status || "unknown"} />
            <StatusBadge status={executionMode?.status || "unknown"} label={executionMode?.mode || executionMode?.status || "mode"} />
          </div>
          <p className="text-xs mt-1 max-w-4xl" style={{ color: "var(--mis-dim)" }}>{copy.subtitle}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Link to="/workspace/agents" className="inline-flex items-center gap-1.5 text-xs px-3 py-1.5 rounded" style={{ background: "var(--mis-surface2)", color: "var(--mis-dim)", border: "1px solid var(--mis-border)" }}>
            <Bot size={13} />
            {copy.backToTeam}
          </Link>
          <button onClick={refresh} className="inline-flex items-center gap-1.5 text-xs px-3 py-1.5 rounded" style={{ background: "rgba(34,211,238,0.12)", color: "var(--mis-cyan)", border: "1px solid rgba(34,211,238,0.2)" }}>
            <RefreshCw size={13} className={loading ? "animate-spin" : ""} />
            {copy.refresh}
          </button>
        </div>
      </div>

      {loading && <p className="text-xs" style={{ color: "var(--mis-muted)" }}>{copy.loading}</p>}
      {error && <p className="text-xs" style={{ color: "#F87171" }}>{copy.unavailable}: {error}</p>}

      <section className="rounded-lg p-4" style={{ background: "var(--mis-surface)", border: "1px solid var(--mis-border)" }}>
        <div className="flex flex-col xl:flex-row xl:items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <Activity size={14} style={{ color: "var(--mis-success)" }} />
              <h2 className="text-sm font-semibold" style={{ color: "var(--mis-text)" }}>{copy.executionMode}</h2>
              <StatusBadge status={selectedRoute?.readiness || selectedReadiness?.readiness || "unknown"} />
              <StatusBadge status={executionMode?.summary.confirm_run_wall || (liveBlocked ? "attention" : "pass")} label={copy.confirmWall} />
              <StatusBadge status={executionMode?.summary.prepared_action_wall || "planned"} label={copy.approvalWall} />
            </div>
            <div className="text-[11px] mt-1 truncate" style={{ color: "var(--mis-dim)" }}>
              {copy.selectedPath}: {executionMode?.selected_path || selectedAdapter} · {copy.recommendedAction}: {primaryCommand}
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <label className="text-[10px] uppercase tracking-wide" style={{ color: "var(--mis-muted)" }}>{copy.selectedAdapter}</label>
            <select
              value={selectedAdapter}
              onChange={(event) => setSelectedAdapter(event.target.value as WorkerAdapterName)}
              className="rounded px-2 py-1 text-xs"
              style={{ background: "var(--mis-surface2)", color: "var(--mis-text)", border: "1px solid var(--mis-border)" }}
            >
              {WORKER_ADAPTERS.map((adapter) => <option key={adapter} value={adapter}>{adapter}</option>)}
            </select>
            <label className="inline-flex items-center gap-2 text-[11px] rounded px-2 py-1" style={{ color: confirmRun ? "var(--mis-success)" : "var(--mis-warning)", background: confirmRun ? "rgba(45,212,191,0.10)" : "rgba(251,191,36,0.10)", border: confirmRun ? "1px solid rgba(45,212,191,0.20)" : "1px solid rgba(251,191,36,0.24)" }}>
              <input type="checkbox" checked={confirmRun} onChange={(event) => setConfirmRun(event.target.checked)} />
              {copy.confirmLive}
            </label>
            <button onClick={() => void copyCommand(primaryCommand)} className="inline-flex items-center gap-1.5 rounded px-2.5 py-1 text-[11px]" style={{ background: "var(--mis-surface2)", color: "var(--mis-cyan)", border: "1px solid var(--mis-border)" }}>
              <Copy size={11} />
              {copiedCommand === primaryCommand ? copy.copied : copy.copyCommand}
            </button>
          </div>
        </div>
        {liveBlocked && (
          <div className="mt-3 flex items-center gap-2 rounded px-3 py-2 text-[11px]" style={{ background: "rgba(251,191,36,0.10)", border: "1px solid rgba(251,191,36,0.22)", color: "var(--mis-warning)" }}>
            <AlertTriangle size={13} />
            {copy.liveBlocked} {copy.confirmHint}
          </div>
        )}
        <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-8 gap-2 mt-4">
          {statCards.map((item) => (
            <div key={item.label} className="rounded px-3 py-2" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
              <div className="text-[10px]" style={{ color: "var(--mis-muted)" }}>{item.label}</div>
              <div className="flex items-center justify-between gap-2 mt-1">
                <div className="text-sm font-semibold truncate" style={{ color: "var(--mis-text)" }}>{item.value}</div>
                <StatusBadge status={item.status} />
              </div>
            </div>
          ))}
        </div>
      </section>

      <div className="grid grid-cols-1 xl:grid-cols-[1.15fr_0.85fr] gap-4">
        <section className="rounded-lg p-4" style={{ background: "var(--mis-surface)", border: "1px solid var(--mis-border)" }}>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <div className="flex items-center gap-2">
                <Power size={14} style={{ color: "var(--mis-cyan)" }} />
                <h2 className="text-sm font-semibold" style={{ color: "var(--mis-text)" }}>{copy.daemonControl}</h2>
              </div>
              <p className="text-[11px] mt-1" style={{ color: "var(--mis-dim)" }}>{copy.confirmHint}</p>
            </div>
            <div className="flex flex-wrap gap-2">
              <button onClick={dispatchOnce} disabled={Boolean(busyAction) || liveBlocked} className="inline-flex items-center gap-1.5 rounded px-3 py-1.5 text-[11px] disabled:opacity-45" style={{ background: "rgba(34,211,238,0.12)", color: "var(--mis-cyan)", border: "1px solid rgba(34,211,238,0.2)" }}>
                {busyAction === `dispatch:${selectedAdapter}` ? <RefreshCw size={12} /> : <Play size={12} />}
                {copy.dispatchOnce}
              </button>
              <button onClick={startDaemon} disabled={Boolean(busyAction) || liveBlocked || selectedHostManaged || selectedControlBlocked} title={selectedHostManaged ? copy.hostManagedHint : selectedControlBlocked ? copy.controlBlockedHint : undefined} className="inline-flex items-center gap-1.5 rounded px-3 py-1.5 text-[11px] disabled:opacity-45" style={{ background: "rgba(45,212,191,0.12)", color: "var(--mis-success)", border: "1px solid rgba(45,212,191,0.22)" }}>
                {busyAction === `start:${selectedAdapter}` ? <RefreshCw size={12} /> : <Power size={12} />}
                {copy.startDaemon}
              </button>
              <button onClick={restartDaemon} disabled={Boolean(busyAction) || liveBlocked || selectedHostManaged || selectedControlBlocked} title={selectedHostManaged ? copy.hostManagedHint : selectedControlBlocked ? copy.controlBlockedHint : undefined} className="inline-flex items-center gap-1.5 rounded px-3 py-1.5 text-[11px] disabled:opacity-45" style={{ background: "rgba(122,90,248,0.10)", color: "#A78BFA", border: "1px solid rgba(122,90,248,0.2)" }}>
                {busyAction === `restart:${selectedAdapter}` ? <RefreshCw size={12} /> : <RotateCw size={12} />}
                {copy.restartDaemon}
              </button>
              <button onClick={stopAll} disabled={Boolean(busyAction) || hostManagedRunning || anyControlBlocked} title={hostManagedRunning ? copy.hostManagedHint : anyControlBlocked ? copy.controlBlockedHint : undefined} className="inline-flex items-center gap-1.5 rounded px-3 py-1.5 text-[11px] disabled:opacity-45" style={{ background: "rgba(248,113,113,0.10)", color: "#F87171", border: "1px solid rgba(248,113,113,0.22)" }}>
                {busyAction === "stop:all" ? <RefreshCw size={12} /> : <Square size={12} />}
                {copy.stopDaemons}
              </button>
            </div>
          </div>

          {actionMessage && (
            <div className="mt-3 text-[11px] rounded px-3 py-2" style={{ color: actionMessage.includes("failed") || actionMessage.includes("blocked") ? "#F87171" : "var(--mis-success)", background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
              {actionMessage}
            </div>
          )}

          <div data-testid="worker-local-install-packet" className="rounded p-3 mt-4" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <ShieldCheck size={13} style={{ color: "var(--mis-cyan)" }} />
                  <div className="text-[11px] font-semibold" style={{ color: "var(--mis-text)" }}>{copy.localInstallPacket}</div>
                  <StatusBadge status={startCheck?.status || "unknown"} label={selectedAdapter} />
                </div>
                <p className="text-[10px] mt-1 truncate" style={{ color: "var(--mis-dim)" }}>{copy.installPacketSummary}</p>
              </div>
              <div className="flex flex-wrap gap-1.5">
                {serviceInstallSafety.map((item) => (
                  <StatusBadge key={item.label} status={item.status} label={item.label} />
                ))}
              </div>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-2 mt-3">
              {serviceInstallCommands.map((item) => (
                <button
                  key={item.label}
                  type="button"
                  disabled={!item.command}
                  onClick={() => void copyCommand(String(item.command || ""))}
                  className="flex items-center justify-between gap-2 rounded px-2 py-1.5 text-left disabled:opacity-45"
                  style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)", color: item.confirm ? "var(--mis-warning)" : "var(--mis-cyan)" }}
                  title={String(item.command || copy.noCommand)}
                >
                  <span className="min-w-0">
                    <span className="block text-[10px] font-semibold truncate" style={{ color: "var(--mis-text)" }}>{item.label}</span>
                    <span className="block text-[9px] truncate" style={{ color: item.confirm ? "var(--mis-warning)" : "var(--mis-cyan)" }}>
                      {item.command ? (copiedCommand === item.command ? copy.copied : commandLabel(String(item.command))) : copy.noCommand}
                    </span>
                  </span>
                  <Copy size={10} className="shrink-0" />
                </button>
              ))}
            </div>
            {serviceControlStep && (
              <div data-testid="worker-service-control-audit" className="rounded p-3 mt-3" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                <div className="flex flex-col lg:flex-row lg:items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <Power size={12} style={{ color: "var(--mis-cyan)" }} />
                      <div className="text-[10px] font-semibold" style={{ color: "var(--mis-text)" }}>{copy.serviceControlAudit}</div>
                      <StatusBadge status={serviceControlStep.status || "preview"} />
                      <StatusBadge status={serviceReceiptVerified ? "pass" : "attention"} label={`${copy.receiptProof}: ${serviceReceiptStatus}${serviceReceiptHash ? ` · ${serviceReceiptHash}` : ""}`} />
                      <StatusBadge status={serviceReadbackAttached ? "pass" : "attention"} label={`${copy.controlReadback}: ${serviceReadbackAttached ? "yes" : "no"}`} />
                    </div>
                    <p className="text-[9px] mt-1 line-clamp-2" style={{ color: "var(--mis-dim)" }}>{serviceControlStep.detail || copy.serviceControlAuditSummary}</p>
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {serviceControlSafety.map((item) => (
                      <StatusBadge key={item.label} status={item.status} label={item.label} />
                    ))}
                  </div>
                </div>
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-2 mt-3">
                  <button
                    type="button"
                    onClick={() => void copyCommand(serviceControlStep.command)}
                    className="flex items-center gap-1 rounded px-2 py-1 text-left min-w-0"
                    style={{ color: "var(--mis-text)", background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}
                    title={serviceControlStep.command}
                  >
                    <Copy size={9} />
                    <span className="text-[9px] font-semibold shrink-0">{copy.serviceControlPreview}</span>
                    <span className="text-[8px] truncate" style={{ color: "var(--mis-muted)" }}>{copiedCommand === serviceControlStep.command ? copy.copied : serviceControlStep.command}</span>
                  </button>
                  {serviceControlStep.verify_command && (
                    <button
                      type="button"
                      onClick={() => void copyCommand(String(serviceControlStep.verify_command))}
                      className="flex items-center gap-1 rounded px-2 py-1 text-left min-w-0"
                      style={{ color: "var(--mis-text)", background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}
                      title={String(serviceControlStep.verify_command)}
                    >
                      <Copy size={9} />
                      <span className="text-[9px] font-semibold shrink-0">{copy.serviceCheck}</span>
                      <span className="text-[8px] truncate" style={{ color: "var(--mis-muted)" }}>{copiedCommand === serviceControlStep.verify_command ? copy.copied : serviceControlStep.verify_command}</span>
                    </button>
                  )}
                  {serviceControlStep.receipt_verify_record_command && (
                    <button
                      type="button"
                      onClick={() => void copyCommand(String(serviceControlStep.receipt_verify_record_command))}
                      className="flex items-center gap-1 rounded px-2 py-1 text-left min-w-0"
                      style={{ color: "var(--mis-warning)", background: "rgba(251,191,36,0.08)", border: "1px solid rgba(251,191,36,0.18)" }}
                      title={String(serviceControlStep.receipt_verify_record_command)}
                    >
                      <Copy size={9} />
                      <span className="text-[9px] font-semibold shrink-0">{copy.verifiedReceipt}</span>
                      <span className="text-[8px] truncate" style={{ color: "var(--mis-muted)" }}>{copiedCommand === serviceControlStep.receipt_verify_record_command ? copy.copied : serviceControlStep.receipt_verify_record_command}</span>
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={() => void recordServiceControlReceipt(serviceControlStep)}
                    disabled={Boolean(busyAction) || Boolean(receiptAction)}
                    className="flex items-center gap-1 rounded px-2 py-1 text-left min-w-0 disabled:opacity-50"
                    style={{ color: "var(--mis-success)", background: "rgba(45,212,191,0.10)", border: "1px solid rgba(45,212,191,0.20)" }}
                    title={copy.recordVerifiedReceipt}
                  >
                    {receiptAction === `service-receipt:${serviceControlStep.step_id}` ? <RefreshCw size={9} /> : <CheckCircle2 size={9} />}
                    <span className="text-[9px] font-semibold shrink-0">{copy.recordVerifiedReceipt}</span>
                    <span className="text-[8px] truncate" style={{ color: "var(--mis-muted)" }}>{serviceControlStep.receipt_command || "agentops operator action-receipts --limit 20"}</span>
                  </button>
                  {serviceControlStep.control_readback_required && (
                    <button
                      type="button"
                      onClick={() => void recordServiceControlReadback(serviceControlStep)}
                      disabled={Boolean(busyAction) || Boolean(receiptAction)}
                      className="flex items-center gap-1 rounded px-2 py-1 text-left min-w-0 disabled:opacity-50"
                      style={{ color: "var(--mis-cyan)", background: "rgba(34,211,238,0.08)", border: "1px solid rgba(34,211,238,0.20)" }}
                      title={copy.recordControlReadback}
                    >
                      {receiptAction === `service-readback:${serviceControlStep.step_id}` ? <RefreshCw size={9} /> : <Activity size={9} />}
                      <span className="text-[9px] font-semibold shrink-0">{copy.recordControlReadback}</span>
                      <span className="text-[8px] truncate" style={{ color: "var(--mis-muted)" }}>{serviceControlStep.verify_command || copy.serviceCheck}</span>
                    </button>
                  )}
                </div>
                <div data-testid="worker-service-managed-loop" className="grid grid-cols-1 md:grid-cols-3 gap-2 mt-3">
                  {serviceManagedFacts.map((item) => (
                    <div key={item.label} className="rounded px-2 py-1.5" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-[9px] font-semibold truncate" style={{ color: "var(--mis-text)" }}>{item.label}</span>
                        <StatusBadge status={item.status} />
                      </div>
                      <div className="text-[8px] truncate mt-1" style={{ color: item.status === "pass" ? "var(--mis-success)" : "var(--mis-warning)" }}>{item.value}</div>
                    </div>
                  ))}
                </div>
                <div className="mt-2 flex flex-wrap items-center gap-1.5">
                  <StatusBadge status={serviceManagedStatus} label={copy.serviceManagedLoop} />
                  <StatusBadge status={serviceManagedLoop.loads_service ? "attention" : "pass"} label={copy.noServiceLoad} />
                  <StatusBadge status={(serviceManagedLoop.safety as Record<string, unknown> | undefined)?.server_executes_shell === false ? "pass" : "attention"} label={copy.noServerShell} />
                  <StatusBadge status={serviceManagedLoop.token_omitted === false ? "attention" : "pass"} label={copy.tokenOmitted} />
                </div>
                <div data-testid="worker-managed-execution-path" className="rounded p-3 mt-3" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
                  <div className="flex flex-col lg:flex-row lg:items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <ShieldCheck size={12} style={{ color: managedExecutionReady ? "var(--mis-success)" : "var(--mis-warning)" }} />
                        <div className="text-[10px] font-semibold" style={{ color: "var(--mis-text)" }}>{copy.managedExecutionPath}</div>
                        <StatusBadge status={managedExecutionStatus} />
                        <StatusBadge status={managedExecutionReady ? "pass" : "attention"} label={copy.managedLoopReady} />
                      </div>
                      <p className="text-[9px] mt-1 line-clamp-2" style={{ color: "var(--mis-dim)" }}>{copy.managedExecutionSummary}</p>
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                      {managedExecutionGates.slice(0, 8).map((gate) => (
                        <StatusBadge key={String(gate.id || gate.proof)} status={String(gate.status || "required")} label={String(gate.id || "gate")} />
                      ))}
                    </div>
                  </div>
                  <div className="grid grid-cols-1 lg:grid-cols-2 gap-2 mt-3">
                    {[
                      { label: copy.nextManagedStep, command: managedExecutionNextCommand || managedExecutionFirstSafe[0] || managedExecutionCommands.service_control_receipt },
                      { label: copy.dispatchCommand, command: managedExecutionCommands.customer_worker_dispatch },
                      { label: copy.evidenceReport, command: managedExecutionCommands.evidence_report || managedExecutionVerify[0] },
                      { label: copy.reviewQueue, command: managedExecutionCommands.review_queue || managedExecutionVerify[1] },
                    ].map((item) => (
                      <button
                        key={item.label}
                        type="button"
                        onClick={() => void copyCommand(String(item.command || ""))}
                        disabled={!item.command}
                        className="flex items-center gap-1 rounded px-2 py-1 text-left min-w-0 disabled:opacity-40"
                        style={{ color: "var(--mis-text)", background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}
                        title={String(item.command || "")}
                      >
                        <Copy size={9} />
                        <span className="text-[9px] font-semibold shrink-0">{item.label}</span>
                        <span className="text-[8px] truncate" style={{ color: "var(--mis-muted)" }}>{item.command ? (copiedCommand === item.command ? copy.copied : String(item.command)) : copy.noCommand}</span>
                      </button>
                    ))}
                  </div>
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    <StatusBadge status={(managedExecutionPath.safety as Record<string, unknown> | undefined)?.server_executes_shell === false ? "pass" : "attention"} label={copy.noServerShell} />
                    <StatusBadge status={(managedExecutionPath.safety as Record<string, unknown> | undefined)?.live_execution_performed ? "attention" : "pass"} label={copy.noLiveExecution} />
                    <StatusBadge status={(managedExecutionPath.safety as Record<string, unknown> | undefined)?.token_omitted === false ? "attention" : "pass"} label={copy.tokenOmitted} />
                  </div>
                </div>
              </div>
            )}
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mt-4">
            {serviceWorkerLanes.map((worker) => (
              <div key={worker.lane_id} data-testid="gateway-service-worker" className="rounded px-3 py-2" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
                <div className="flex items-center justify-between gap-2">
                  <div className="text-[11px] font-semibold" style={{ color: adapterColor(worker.adapter || "external") }}>
                    {worker.adapter === "openclaw" ? "OpenClaw Worker" : worker.adapter === "hermes" ? "Hermes Worker" : worker.agent_name || worker.agent_id}
                  </div>
                  <div className="flex items-center gap-1.5">
                    <StatusBadge status={worker.heartbeat_state === "fresh" ? "running" : "attention"} label={worker.heartbeat_state || "unknown"} />
                    <StatusBadge status="ready" label={copy.serviceWorkers} />
                  </div>
                </div>
                <div className="grid grid-cols-2 gap-2 mt-2">
                  <div className="text-[10px] truncate" style={{ color: "var(--mis-muted)" }}>{copy.selectedAdapter}: <span style={{ color: "var(--mis-text)" }}>{worker.adapter || "—"}</span></div>
                  <div className="text-[10px] truncate" style={{ color: "var(--mis-muted)" }}>{copy.activeSessions}: <span style={{ color: "var(--mis-text)" }}>{worker.active_session_count}</span></div>
                </div>
                <div className="text-[10px] mt-2 truncate" style={{ color: "var(--mis-dim)" }}>{worker.agent_id || "—"}</div>
              </div>
            ))}
            {(workerStatus?.daemons || []).map((daemon) => (
              <div key={daemon.adapter} className="rounded px-3 py-2" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
                <div className="flex items-center justify-between gap-2">
                  <div className="text-[11px] font-semibold" style={{ color: adapterColor(daemon.adapter) }}>{daemon.adapter}</div>
                  <div className="flex items-center gap-1.5">
                    {daemon.management_mode && <StatusBadge status={daemon.management_mode === "host_stack" ? "ready" : "planned"} label={daemon.management_mode === "host_stack" ? copy.hostManaged : copy.apiManaged} />}
                    {daemon.process_claim_active && <StatusBadge status={daemon.process_identity_verified ? "pass" : "attention"} label={daemon.process_identity_verified ? copy.processVerified : copy.processUnverified} />}
                    <StatusBadge status={daemon.running ? "running" : daemon.status} />
                  </div>
                </div>
                <div className="grid grid-cols-2 gap-2 mt-2">
                  <div className="text-[10px] truncate" style={{ color: "var(--mis-muted)" }}>{copy.pid}: <span style={{ color: "var(--mis-text)" }}>{daemon.pid || "—"}</span></div>
                  <div className="text-[10px] truncate" style={{ color: "var(--mis-muted)" }}>{copy.processed}: <span style={{ color: "var(--mis-text)" }}>{daemon.processed ?? 0}</span></div>
                  <div className="text-[10px] truncate" style={{ color: "var(--mis-muted)" }}>{copy.errors}: <span style={{ color: daemon.total_errors ? "#F87171" : "var(--mis-text)" }}>{daemon.total_errors ?? 0}</span></div>
                  <div className="text-[10px] truncate" style={{ color: "var(--mis-muted)" }}>{copy.lastSleep}: <span style={{ color: "var(--mis-text)" }}>{daemon.last_sleep_reason || "—"}</span></div>
                </div>
                <div className="text-[10px] mt-2 truncate" style={{ color: "var(--mis-dim)" }}>{daemon.agent_id || daemon.worker_status || "—"}</div>
              </div>
            ))}
          </div>

          {(lastDispatch || lastDaemonResult) && (
            <div className="rounded p-3 mt-4" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
              <div className="flex flex-wrap items-center gap-2">
                <ShieldCheck size={13} style={{ color: "var(--mis-cyan)" }} />
                <div className="text-[11px] font-semibold" style={{ color: "var(--mis-text)" }}>{lastDispatch ? copy.dispatchOnce : copy.daemonControl}</div>
                {lastDispatch && <StatusBadge status={lastDispatch.ok ? "completed" : "failed"} />}
                {lastDispatch?.plan_evidence_status && <StatusBadge status={lastDispatch.plan_evidence_pass ? "pass" : "attention"} label={copy.planEvidence} />}
                {lastDaemonResult && <StatusBadge status={lastDaemonResult.ok ? "ready" : "blocked"} />}
              </div>
              {lastDispatch && (
                <div className="flex flex-wrap gap-2 mt-3">
                  {lastDispatch.task_id && <Link to={`/admin/tasks/${lastDispatch.task_id}`} className="text-[10px] px-2 py-1 rounded" style={{ background: "rgba(34,211,238,0.10)", color: "var(--mis-cyan)", border: "1px solid rgba(34,211,238,0.18)" }}>{copy.openTask}</Link>}
                  {lastDispatch.run_id && <Link to={`/admin/runs/${lastDispatch.run_id}`} className="text-[10px] px-2 py-1 rounded" style={{ background: "rgba(45,212,191,0.10)", color: "var(--mis-success)", border: "1px solid rgba(45,212,191,0.18)" }}>{copy.openRun}</Link>}
                  <span className="text-[10px] px-2 py-1 rounded" style={{ background: "var(--mis-bg)", color: "var(--mis-muted)", border: "1px solid var(--mis-border)" }}>{copy.runId}: {lastDispatch.run_id || "—"}</span>
                </div>
              )}
              {lastDaemonResult?.recommended_action && (
                <button onClick={() => void copyCommand(lastDaemonResult.recommended_action)} className="mt-3 inline-flex items-center gap-1.5 rounded px-2 py-1 text-[10px]" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)", color: "var(--mis-cyan)" }}>
                  <Copy size={10} />
                  {copiedCommand === lastDaemonResult.recommended_action ? copy.copied : commandLabel(lastDaemonResult.recommended_action)}
                </button>
              )}
            </div>
          )}
        </section>

        <section className="rounded-lg p-4" style={{ background: "var(--mis-surface)", border: "1px solid var(--mis-border)" }}>
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <CheckCircle2 size={14} style={{ color: "var(--mis-success)" }} />
              <h2 className="text-sm font-semibold" style={{ color: "var(--mis-text)" }}>{copy.adapterReadiness}</h2>
            </div>
            <StatusBadge status={adapterReadiness?.status || "unknown"} />
          </div>
          <div className="space-y-3 mt-3">
            {WORKER_ADAPTERS.map((adapter) => {
              const item = adapterReadiness?.adapters?.[adapter];
              const commands = item?.remediation?.commands || [];
              return (
                <div key={adapter} className="rounded px-3 py-2" style={{ background: adapter === selectedAdapter ? "rgba(34,211,238,0.07)" : "var(--mis-surface2)", border: adapter === selectedAdapter ? "1px solid rgba(34,211,238,0.22)" : "1px solid var(--mis-border)" }}>
                  <div className="flex items-center justify-between gap-2">
                    <div className="text-[11px] font-semibold" style={{ color: adapterColor(adapter) }}>{adapter}</div>
                    <div className="flex items-center gap-1.5">
                      <StatusBadge status={item?.readiness || "unknown"} />
                      <StatusBadge status={item?.trust_status || "unknown"} label={`${copy.trust}: ${item?.trust_status || "—"}`} />
                    </div>
                  </div>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 mt-2">
                    <div className="text-[10px] truncate" style={{ color: "var(--mis-muted)" }}>{copy.liveReady}: <span style={{ color: item?.ok ? "var(--mis-success)" : "var(--mis-dim)" }}>{item?.ok ? "yes" : "no"}</span></div>
                    <div className="text-[10px] truncate" style={{ color: "var(--mis-muted)" }}>{copy.target}: <span style={{ color: "var(--mis-text)" }}>{item?.target_resource || "—"}</span></div>
                  </div>
                  <div className="text-[10px] mt-2 truncate" style={{ color: "var(--mis-cyan)" }}>{copy.recommendedAction}: {item?.remediation?.primary_next_action || item?.recommended_action || "agentops worker readiness"}</div>
                  <div className="flex flex-wrap gap-1.5 mt-2">
                    {(commands.length ? commands.slice(0, 4) : [{ command: item?.remediation?.primary_next_action || item?.recommended_action || "" }]).map((command, index) => (
                      <button key={`${adapter}:${index}:${command.command}`} onClick={() => void copyCommand(command.command)} disabled={!command.command} className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[9px] disabled:opacity-40" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)", color: command.confirm_required ? "var(--mis-warning)" : "var(--mis-cyan)" }}>
                        <Copy size={8} />
                        {command.command ? (copiedCommand === command.command ? copy.copied : command.phase || copy.copyCommand) : copy.noCommand}
                      </button>
                    ))}
                  </div>
                </div>
              );
            })}
          </div>
          <div
            data-testid="worker-local-harness-proof"
            className="rounded p-3 mt-4"
            style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}
          >
            <div className="flex flex-col gap-2 lg:flex-row lg:items-start lg:justify-between">
              <div>
                <div className="flex flex-wrap items-center gap-2">
                  <ShieldCheck size={13} style={{ color: "var(--mis-cyan)" }} />
                  <h3 className="text-[11px] font-semibold" style={{ color: "var(--mis-text)" }}>{copy.harnessProof}</h3>
                  <StatusBadge status={localHarnessProof?.status || "unknown"} />
                  <StatusBadge status={(localHarnessProof?.summary.fresh_real_runtime_adapters || 0) > 0 ? "pass" : "attention"} label={`${copy.realRuntimeProof}: ${localHarnessProof?.summary.fresh_real_runtime_adapters || 0}`} />
                  <StatusBadge status={(localHarnessProof?.summary.fresh_mock_fallback || 0) > 0 ? "pass" : "unknown"} label={`${copy.mockFallback}: ${localHarnessProof?.summary.fresh_mock_fallback || 0}`} />
                  <StatusBadge status={localHarnessProof?.governed_launch_packet?.status || "unknown"} label={copy.governedLaunch} />
                  <StatusBadge status={(localHarnessProof?.governed_launch_packet?.receipt_summary?.recorded_current || 0) > 0 ? "pass" : "attention"} label={`${copy.receiptCurrent}: ${localHarnessProof?.governed_launch_packet?.receipt_summary?.recorded_current || localHarnessProof?.summary.launch_receipts_recorded_current || 0}`} />
                </div>
                <p className="text-[10px] mt-1 max-w-3xl" style={{ color: "var(--mis-muted)" }}>{copy.harnessProofSummary}</p>
                <p className="text-[10px] mt-1 max-w-3xl" style={{ color: "var(--mis-dim)" }}>{copy.governedLaunchSummary}</p>
                <div className="flex flex-wrap gap-1.5 mt-2" data-testid="worker-local-harness-receipt-summary">
                  <StatusBadge status="pass" label={`${copy.receiptCurrent}: ${localHarnessProof?.governed_launch_packet?.receipt_summary?.recorded_current || localHarnessProof?.summary.launch_receipts_recorded_current || 0}`} />
                  <StatusBadge status={(localHarnessProof?.governed_launch_packet?.receipt_summary?.missing || localHarnessProof?.summary.launch_receipts_missing || 0) > 0 ? "attention" : "pass"} label={`${copy.receiptMissing}: ${localHarnessProof?.governed_launch_packet?.receipt_summary?.missing || localHarnessProof?.summary.launch_receipts_missing || 0}`} />
                  <StatusBadge status={(localHarnessProof?.governed_launch_packet?.receipt_summary?.stale || localHarnessProof?.summary.launch_receipts_stale || 0) > 0 ? "stale" : "pass"} label={`${copy.receiptStale}: ${localHarnessProof?.governed_launch_packet?.receipt_summary?.stale || localHarnessProof?.summary.launch_receipts_stale || 0}`} />
                  <StatusBadge status={localHarnessProof?.governed_launch_packet?.receipt_summary?.receipt_presence_is_runtime_success ? "fail" : "pass"} label={copy.receiptNotRuntime} />
                </div>
              </div>
              <button
                onClick={() => void copyCommand(localHarnessProof?.governed_launch_packet?.readback_command || "agentops operator local-harness-proof --limit 8")}
                className="inline-flex items-center gap-1.5 rounded px-2 py-1 text-[10px]"
                style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)", color: "var(--mis-cyan)" }}
              >
                <Copy size={10} />
                {copiedCommand === (localHarnessProof?.governed_launch_packet?.readback_command || "agentops operator local-harness-proof --limit 8") ? copy.copied : copy.readbackCommand}
              </button>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-2 mt-3">
              {WORKER_ADAPTERS.map((adapter) => {
                const proof = localHarnessProof?.adapters?.[adapter];
                const latest = proof?.latest_passing || proof?.latest_attempt;
                const governedCommand = proof?.governed_launch?.confirm_required ? proof?.governed_launch?.confirmed_command : proof?.governed_launch?.preview_command;
                const receiptStatus = proof?.governed_launch?.receipt_status;
                return (
                  <div key={`harness-proof:${adapter}`} className="rounded px-3 py-2" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                    <div className="flex items-center justify-between gap-2">
                      <div className="text-[11px] font-semibold" style={{ color: adapterColor(adapter) }}>{adapter}</div>
                      <StatusBadge status={proof?.status || "missing"} />
                    </div>
                    <div className="grid grid-cols-1 gap-1 mt-2">
                      <div className="text-[10px] truncate" style={{ color: "var(--mis-muted)" }}>{copy.proofClass}: <span style={{ color: "var(--mis-text)" }}>{proof?.evidence_class || "—"}</span></div>
                      <div className="text-[10px] truncate" style={{ color: "var(--mis-muted)" }}>{copy.latestPassing}: <span style={{ color: latest?.pass ? "var(--mis-success)" : "var(--mis-dim)" }}>{latest?.run_id || "—"}</span></div>
                      <div className="text-[10px] truncate" style={{ color: "var(--mis-muted)" }}>{copy.latestAttempt}: <span style={{ color: "var(--mis-text)" }}>{latest?.run_status || "—"}</span></div>
                      <div className="text-[10px] truncate" style={{ color: "var(--mis-muted)" }}>{copy.freshnessHours}: <span style={{ color: "var(--mis-text)" }}>{proof?.freshness_hours || localHarnessProof?.freshness_hours || 72}h</span></div>
                      <div className="flex min-w-0 items-center gap-1.5 text-[10px]" data-testid="worker-local-harness-receipt-status" style={{ color: "var(--mis-muted)" }}>
                        <span className="shrink-0">{copy.receiptStatus}:</span>
                        <StatusBadge status={receiptStatus?.match === "current" ? (receiptStatus?.underlying_status || "recorded") : (receiptStatus?.match || "missing")} label={`${receiptStatus?.match || "missing"} / ${receiptStatus?.underlying_status || "missing"}`} />
                      </div>
                      <div className="text-[10px] truncate" style={{ color: receiptStatus?.receipt_presence_is_runtime_success ? "var(--mis-warning)" : "var(--mis-dim)" }}>{copy.receiptNotRuntime}</div>
                    </div>
                    <div className="flex flex-wrap gap-1.5 mt-2">
                      <button
                        onClick={() => void copyCommand(governedCommand || proof?.next_action)}
                        disabled={!governedCommand && !proof?.next_action}
                        className="inline-flex max-w-full items-center gap-1 rounded px-1.5 py-0.5 text-[9px] disabled:opacity-40"
                        style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)", color: proof?.governed_launch?.confirm_required ? "var(--mis-warning)" : "var(--mis-cyan)" }}
                      >
                        <Copy size={8} />
                        <span className="truncate">{copiedCommand === (governedCommand || proof?.next_action) ? copy.copied : copy.governedLaunch}</span>
                      </button>
                      <button
                        onClick={() => void copyCommand(proof?.governed_launch?.receipt_record_command)}
                        disabled={!proof?.governed_launch?.receipt_record_command}
                        className="inline-flex max-w-full items-center gap-1 rounded px-1.5 py-0.5 text-[9px] disabled:opacity-40"
                        style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)", color: "var(--mis-cyan)" }}
                      >
                        <Copy size={8} />
                        <span className="truncate">{copiedCommand === proof?.governed_launch?.receipt_record_command ? copy.copied : copy.receiptCommand}</span>
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mt-3">
              <StatusBadge status={localHarnessProof?.safety.read_only ? "pass" : "attention"} label={copy.readOnlyProof} />
              <StatusBadge status={localHarnessProof?.safety.live_execution_performed ? "attention" : "pass"} label={copy.noLiveExecution} />
              <StatusBadge status={localHarnessProof?.safety.raw_prompt_omitted ? "pass" : "attention"} label={copy.rawPromptOmitted} />
              <StatusBadge status={localHarnessProof?.safety.token_omitted ? "pass" : "attention"} label={copy.tokenOmitted} />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-2 mt-4">
            <StatusBadge status={executionMode?.safety.read_only ? "pass" : "attention"} label={copy.readOnlyProof} />
            <StatusBadge status={executionMode?.safety.server_executes_shell === false ? "pass" : "attention"} label={copy.noServerShell} />
            <StatusBadge status={executionMode?.safety.live_execution_performed ? "attention" : "pass"} label={copy.noLiveExecution} />
            <StatusBadge status={executionMode?.safety.token_omitted ? "pass" : "attention"} label={copy.tokenOmitted} />
          </div>
        </section>
      </div>

      <section
        data-testid="worker-fleet-hygiene-panel"
        className="rounded-lg p-4"
        style={{ background: "var(--mis-surface)", border: "1px solid var(--mis-border)" }}
      >
        <div className="flex flex-col gap-3 xl:flex-row xl:items-start xl:justify-between">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <AlertTriangle size={14} style={{ color: hygieneActionsAvailable > 0 ? "#FBBF24" : "var(--mis-success)" }} />
              <h2 className="text-sm font-semibold" style={{ color: "var(--mis-text)" }}>{copy.fleetHygiene}</h2>
              <StatusBadge status={fleetHygiene?.status || "unknown"} />
              <StatusBadge status={fleetHygiene?.safety.read_only ? "pass" : "attention"} label={copy.hygieneReadOnly} />
              <StatusBadge status={fleetHygiene?.applied ? "completed" : "planned"} label={fleetHygiene?.applied ? copy.hygieneApplied : copy.actionsAvailable} />
            </div>
            <p className="mt-1 max-w-4xl text-[11px] leading-relaxed" style={{ color: "var(--mis-dim)" }}>{copy.hygieneSummary}</p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <label className="inline-flex items-center gap-2 rounded px-2 py-1 text-[11px]" style={{ background: confirmCleanup ? "rgba(251,191,36,0.12)" : "var(--mis-surface2)", color: confirmCleanup ? "#FBBF24" : "var(--mis-muted)", border: confirmCleanup ? "1px solid rgba(251,191,36,0.24)" : "1px solid var(--mis-border)" }}>
              <input type="checkbox" checked={confirmCleanup} onChange={(event) => setConfirmCleanup(event.target.checked)} />
              {copy.confirmCleanup}
            </label>
            <button onClick={previewHygiene} disabled={Boolean(busyAction)} className="inline-flex items-center gap-1.5 rounded px-3 py-1.5 text-[11px] disabled:opacity-45" style={{ background: "rgba(34,211,238,0.12)", color: "var(--mis-cyan)", border: "1px solid rgba(34,211,238,0.2)" }}>
              {busyAction === "hygiene:preview" ? <RefreshCw size={12} /> : <Activity size={12} />}
              {copy.previewHygiene}
            </button>
            <button onClick={applyHygiene} disabled={Boolean(busyAction) || !confirmCleanup || hygieneActionsAvailable === 0} className="inline-flex items-center gap-1.5 rounded px-3 py-1.5 text-[11px] disabled:opacity-45" style={{ background: "rgba(251,191,36,0.12)", color: "#FBBF24", border: "1px solid rgba(251,191,36,0.24)" }}>
              {busyAction === "hygiene:apply" ? <RefreshCw size={12} /> : <ShieldCheck size={12} />}
              {copy.applyHygiene}
            </button>
          </div>
        </div>

        <div className="mt-3 rounded px-3 py-2 text-[11px]" style={{ background: "rgba(251,191,36,0.08)", border: "1px solid rgba(251,191,36,0.18)", color: "var(--mis-dim)" }}>
          {copy.confirmCleanupHint} {copy.noLiveExecution}: {fleetHygiene?.live_execution_performed ? "false" : "true"} · {copy.tokenOmitted}: {fleetHygiene?.token_omitted ? "true" : "unknown"}
        </div>

        <div className="mt-4 grid grid-cols-2 md:grid-cols-4 xl:grid-cols-8 gap-2">
          {[
            { label: copy.actionsAvailable, value: hygieneActionsAvailable, status: hygieneActionsAvailable > 0 ? "attention" : "pass" },
            { label: copy.stuckTasks, value: fleetHygiene?.summary.stuck_tasks ?? 0, status: (fleetHygiene?.summary.stuck_tasks || 0) > 0 ? "attention" : "pass" },
            { label: copy.staleNeverSeen, value: fleetHygiene?.summary.stale_never_seen_enrollments ?? 0, status: (fleetHygiene?.summary.stale_never_seen_enrollments || 0) > 0 ? "attention" : "pass" },
            { label: copy.staleHeartbeat, value: fleetHygiene?.summary.stale_heartbeat_enrollments ?? 0, status: (fleetHygiene?.summary.stale_heartbeat_enrollments || 0) > 0 ? "attention" : "pass" },
            { label: copy.releasedTasks, value: fleetHygiene?.summary.released_tasks ?? 0, status: fleetHygiene?.applied ? "completed" : "planned" },
            { label: copy.revokedEnrollments, value: fleetHygiene?.summary.revoked_enrollments ?? 0, status: fleetHygiene?.applied ? "completed" : "planned" },
            { label: copy.remoteLanes, value: remoteLaneCount, status: remoteLaneCount > 0 ? "ready" : "planned" },
            { label: copy.localLanes, value: localLaneCount, status: localLaneCount > 0 ? "ready" : "planned" },
          ].map((item) => (
            <div key={item.label} className="rounded px-3 py-2" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
              <div className="text-[10px]" style={{ color: "var(--mis-muted)" }}>{item.label}</div>
              <div className="flex items-center justify-between gap-2 mt-1">
                <div className="text-sm font-semibold truncate" style={{ color: "var(--mis-text)" }}>{item.value}</div>
                <StatusBadge status={item.status} />
              </div>
            </div>
          ))}
        </div>

        <div className="mt-4 grid grid-cols-1 xl:grid-cols-3 gap-3">
          <div className="rounded p-3" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
            <div className="text-[11px] font-semibold" style={{ color: "var(--mis-text)" }}>{copy.recommendedAction}</div>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {(fleetHygiene?.recommended_actions || ["agentops worker hygiene"]).slice(0, 4).map((command) => (
                <button key={command} onClick={() => void copyCommand(command)} className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[9px]" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)", color: command.includes("--apply") ? "#FBBF24" : "var(--mis-cyan)" }}>
                  <Copy size={8} />
                  {copiedCommand === command ? copy.copied : commandLabel(command)}
                </button>
              ))}
            </div>
          </div>
          <div className="rounded p-3" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
            <div className="text-[11px] font-semibold" style={{ color: "var(--mis-text)" }}>{copy.staleNeverSeen}</div>
            <div className="mt-2 space-y-1">
              {(fleetHygiene?.stale_never_seen_enrollments || []).slice(0, 4).map((enrollment) => (
                <div key={enrollment.token_ref || enrollment.agent_id} className="text-[10px] truncate" style={{ color: "var(--mis-muted)" }}>
                  {enrollment.agent_id} · {enrollment.token_ref || "token_ref"} · {enrollment.heartbeat_state}
                </div>
              ))}
              {(fleetHygiene?.stale_never_seen_enrollments || []).length === 0 && <div className="text-[10px]" style={{ color: "var(--mis-dim)" }}>—</div>}
            </div>
          </div>
          <div className="rounded p-3" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
            <div className="text-[11px] font-semibold" style={{ color: "var(--mis-text)" }}>{copy.staleHeartbeat}</div>
            <div className="mt-2 space-y-1">
              {(fleetHygiene?.stale_heartbeat_enrollments || []).slice(0, 4).map((enrollment) => (
                <div key={enrollment.token_ref || enrollment.agent_id} className="text-[10px] truncate" style={{ color: "var(--mis-muted)" }}>
                  {enrollment.agent_id} · {enrollment.token_ref || "token_ref"} · {enrollment.heartbeat_state}
                </div>
              ))}
              {(fleetHygiene?.stale_heartbeat_enrollments || []).length === 0 && <div className="text-[10px]" style={{ color: "var(--mis-dim)" }}>—</div>}
            </div>
          </div>
        </div>
      </section>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <section className="rounded-lg p-4" style={{ background: "var(--mis-surface)", border: "1px solid var(--mis-border)" }}>
          <div className="flex items-center justify-between gap-3">
            <h2 className="text-sm font-semibold" style={{ color: "var(--mis-text)" }}>{copy.recentTasks}</h2>
            <StatusBadge status={(workerStatus?.stuck_tasks?.length || 0) > 0 ? "attention" : "pass"} label={(workerStatus?.stuck_tasks?.length || 0) > 0 ? `${copy.stuckTasks}: ${workerStatus?.stuck_tasks?.length}` : copy.noStuckTasks} />
          </div>
          <div className="space-y-2 mt-3">
            {(workerStatus?.recent_tasks || []).slice(0, 5).map((task) => (
              <Link key={task.task_id} to={`/admin/tasks/${task.task_id}`} className="block rounded px-3 py-2 hover:opacity-85" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
                <div className="flex items-center justify-between gap-2">
                  <div className="text-[11px] font-semibold truncate" style={{ color: "var(--mis-text)" }}>{task.title}</div>
                  <StatusBadge status={task.status} />
                </div>
                <div className="text-[10px] mt-1 truncate" style={{ color: "var(--mis-muted)" }}>{task.task_id} · {task.owner_agent_id || "—"}</div>
              </Link>
            ))}
          </div>
        </section>

        <section className="rounded-lg p-4" style={{ background: "var(--mis-surface)", border: "1px solid var(--mis-border)" }}>
          <div className="flex items-center justify-between gap-3">
            <h2 className="text-sm font-semibold" style={{ color: "var(--mis-text)" }}>{copy.recentRuns}</h2>
            <StatusBadge status={workerFleet?.status || "unknown"} label={copy.workerFleet} />
          </div>
          <div className="space-y-2 mt-3">
            {(workerStatus?.recent_runs || []).slice(0, 5).map((run) => (
              <Link key={run.run_id} to={`/admin/runs/${run.run_id}`} className="block rounded px-3 py-2 hover:opacity-85" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
                <div className="flex items-center justify-between gap-2">
                  <div className="text-[11px] font-semibold truncate" style={{ color: "var(--mis-text)" }}>{run.run_id}</div>
                  <StatusBadge status={run.status} />
                </div>
                <div className="text-[10px] mt-1 truncate" style={{ color: "var(--mis-muted)" }}>{run.agent_id || "—"} · {run.runtime_type || "worker"}</div>
              </Link>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}
