import { Link, useParams } from "react-router";
import { CheckCircle, Download, FileText, ShieldCheck, Users } from "lucide-react";
import { StatusBadge } from "../shared/StatusBadge";
import { RiskBadge } from "../shared/RiskBadge";
import { loadAgents, loadDashboard, loadTaskDetail, useLiveData } from "../../data/liveApi";
import { pick, usePreferences } from "../../context/PreferencesContext";

export function TaskDetail() {
  const { id } = useParams<{ id: string }>();
  const { locale } = usePreferences();
  const { data, loading, error } = useLiveData(async () => {
    const metrics = await loadDashboard();
    const [detail, agents] = await Promise.all([loadTaskDetail(id || ""), loadAgents(metrics)]);
    return { detail, agents };
  }, [id]);

  if (loading) {
    return <p className="text-xs" style={{ color: "var(--mis-muted)" }}>Loading live task detail...</p>;
  }
  if (error || !data?.detail?.task) {
    return <p className="text-xs" style={{ color: "#F87171" }}>Live task detail unavailable: {error || "not found"}</p>;
  }

  const task = data.detail.task;
  const taskRuns = data.detail.runs;
  const taskApprovals = data.detail.approvals;
  const taskMemories = data.detail.memories;
  const taskEvals = data.detail.evaluations;
  const taskArtifacts = data.detail.artifacts || [];
  const taskCaseRuns = data.detail.evaluation_case_runs || [];
  const latestRun = taskRuns[taskRuns.length - 1];
  const pendingApprovals = taskApprovals.filter(ap => ap.decision === "pending");
  const hasApprovedDelivery = taskApprovals.some(ap => ap.decision === "approved");
  const passedEvals = taskEvals.filter(ev => ev.pass_fail === "pass").length;
  const failedEvals = taskEvals.filter(ev => ev.pass_fail === "fail" || ev.pass_fail === "failed").length;
  const evidenceStatus = pendingApprovals.length > 0 ? "attention" : taskArtifacts.length > 0 && taskRuns.length > 0 ? "pass" : "planned";
  const latestEval = taskEvals[taskEvals.length - 1];
  const latestScore = latestEval ? (latestEval.score <= 1 ? Math.round(latestEval.score * 100) : Math.round(latestEval.score)) : 0;
  const liveRuntimeRuns = taskRuns.filter(run => run.runtime_type === "hermes" || run.runtime_type === "openclaw");
  const mockRuntimeRuns = taskRuns.filter(run => run.runtime_type === "mock");
  const failedOrBlockedRuns = taskRuns.filter(run => ["failed", "fail", "error", "blocked", "timeout"].includes(run.status));
  const runningRuns = taskRuns.filter(run => ["running", "queued", "started"].includes(run.status));
  const deliveryGateStatus = failedEvals > 0 || failedOrBlockedRuns.length > 0
    ? "fail"
    : pendingApprovals.length > 0 || latestRun?.approval_required || task.status === "waiting_approval"
      ? "attention"
      : taskArtifacts.length > 0 && taskRuns.length > 0 && taskEvals.length > 0
        ? "pass"
        : runningRuns.length > 0
          ? "running"
          : "planned";
  const runtimePostureStatus = liveRuntimeRuns.length > 0 ? "pass" : mockRuntimeRuns.length > 0 ? "attention" : "planned";

  const ownerAgent = data.agents.find(a => a.agent_id === task.owner_agent_id);
  const collabAgents = data.agents.filter(a => task.collaborator_agent_ids.includes(a.agent_id));

  const priorityColor: Record<string, string> = {
    low: "var(--mis-success)", medium: "var(--mis-primary)", high: "var(--mis-warning)", critical: "#F87171",
  };
  const copy = pick(locale, {
    en: {
      priority: "priority",
      taskId: "Task ID",
      owner: "Owner Agent",
      budget: "Budget",
      due: "Due",
      acceptance: "Acceptance Criteria",
      noCriteria: "No criteria specified.",
      qualityGate: "Quality Gate - Latest Eval",
      collaborators: "Collaborating Agents",
      approvals: "Approvals",
      deliverables: "Deliverables",
      noDeliverables: "No delivery artifacts recorded yet.",
      downloadApproved: "Download approved artifact",
      relatedRuns: "Related Runs",
      noRuns: "No runs yet.",
      memory: "Memory Candidates",
      benchmarkEvidence: "Benchmark Evidence",
      noBenchmarkEvidence: "No approved evaluation case runs recorded yet.",
      evidenceSummary: "Delivery Evidence Summary",
      executionPosture: "Execution Posture",
      runtimeMode: "Runtime mode",
      approvalWall: "Approval wall",
      deliveryGate: "Delivery gate",
      liveRuntimeEvidence: "Hermes/OpenClaw live evidence",
      mixedRuntimeEvidence: "Mixed live + mock evidence",
      mockRuntimeEvidence: "Mock/offline evidence only",
      noRuntimeEvidence: "No worker run evidence yet",
      approvalPending: "Pending human approval",
      approvalClear: "No pending approval",
      gateReady: "Ready for delivery review",
      gateWaiting: "Waiting for evidence",
      gateBlocked: "Failed or blocked evidence",
      gateRunning: "Worker is still running",
      ledgerState: "Ledger state",
      latestRun: "Latest run",
      openRun: "Open run",
      reviewApproval: "Review approval",
      evidenceCounts: {
        runs: "Runs",
        artifacts: "Artifacts",
        approvals: "Approvals",
        evaluations: "Evaluations",
        memories: "Memories",
        benchmark: "Benchmarks",
      },
      headers: ["Run ID", "Agent", "Runtime", "Status", "Cost", "Tokens"],
    },
    zh: {
      priority: "优先级",
      taskId: "任务 ID",
      owner: "负责代理",
      budget: "预算",
      due: "截止时间",
      acceptance: "验收标准",
      noCriteria: "未填写验收标准。",
      qualityGate: "质量门 - 最新评估",
      collaborators: "协作代理",
      approvals: "审批",
      deliverables: "交付物",
      noDeliverables: "暂未记录交付物。",
      downloadApproved: "下载已批准交付物",
      relatedRuns: "相关运行",
      noRuns: "暂无运行记录。",
      memory: "记忆候选",
      benchmarkEvidence: "基准证据",
      noBenchmarkEvidence: "暂无已批准评估用例执行记录。",
      evidenceSummary: "交付证据摘要",
      executionPosture: "执行状态",
      runtimeMode: "运行模式",
      approvalWall: "审批墙",
      deliveryGate: "交付门",
      liveRuntimeEvidence: "Hermes/OpenClaw 真实运行证据",
      mixedRuntimeEvidence: "真实运行 + mock 混合证据",
      mockRuntimeEvidence: "仅 mock / 离线证据",
      noRuntimeEvidence: "暂无 worker 运行证据",
      approvalPending: "等待人工审批",
      approvalClear: "暂无待处理审批",
      gateReady: "可进入交付审查",
      gateWaiting: "等待证据补齐",
      gateBlocked: "存在失败或阻塞证据",
      gateRunning: "Worker 仍在运行",
      ledgerState: "账本状态",
      latestRun: "最新运行",
      openRun: "打开运行",
      reviewApproval: "处理审批",
      evidenceCounts: {
        runs: "运行",
        artifacts: "交付物",
        approvals: "审批",
        evaluations: "评估",
        memories: "记忆",
        benchmark: "基准",
      },
      headers: ["运行 ID", "代理", "运行时", "状态", "成本", "Token"],
    },
  });

  return (
    <div className="space-y-5 w-full">
      {/* Header */}
      <div
        className="rounded-xl p-5"
        style={{ background: "var(--mis-surface)", border: "1px solid var(--mis-border)" }}
      >
        <div className="flex items-start gap-3">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-3 flex-wrap mb-2">
              <h1 className="text-base font-semibold" style={{ color: "var(--mis-text)" }}>{task.title}</h1>
              <StatusBadge status={task.status} size="md" />
              <RiskBadge risk={task.risk_level} />
              <span
                className="text-[11px] px-2 py-0.5 rounded font-medium capitalize"
                style={{ color: priorityColor[task.priority], background: `${priorityColor[task.priority]}18` }}
              >
                {task.priority} {copy.priority}
              </span>
            </div>
            <p className="text-xs" style={{ color: "var(--mis-dim)" }}>{task.description}</p>
          </div>
        </div>

        <div className="grid grid-cols-4 gap-4 mt-4 pt-4" style={{ borderTop: "1px solid var(--mis-border)" }}>
          <div>
            <div className="text-[10px] uppercase tracking-wide" style={{ color: "var(--mis-muted)" }}>{copy.taskId}</div>
            <div className="text-xs font-mono mt-0.5" style={{ color: "var(--mis-dim)" }}>{task.task_id}</div>
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wide" style={{ color: "var(--mis-muted)" }}>{copy.owner}</div>
            <div className="text-xs mt-0.5" style={{ color: "var(--mis-dim)" }}>{ownerAgent?.name ?? task.owner_agent_id}</div>
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wide" style={{ color: "var(--mis-muted)" }}>{copy.budget}</div>
            <div className="text-xs mt-0.5" style={{ color: "var(--mis-dim)" }}>${task.budget_limit_usd}</div>
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wide" style={{ color: "var(--mis-muted)" }}>{copy.due}</div>
            <div className="text-xs mt-0.5" style={{ color: "var(--mis-dim)" }}>
              {task.due_date ? new Date(task.due_date).toLocaleDateString() : "—"}
            </div>
          </div>
        </div>
      </div>

      <div
        className="rounded-xl p-4"
        style={{ background: "var(--mis-surface)", border: "1px solid var(--mis-border)" }}
      >
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="text-xs font-semibold flex items-center gap-1.5" style={{ color: "var(--mis-text)" }}>
              <ShieldCheck size={13} style={{ color: evidenceStatus === "attention" ? "#FBBF24" : "var(--mis-success)" }} />
              {copy.evidenceSummary}
            </div>
            <p className="mt-1 text-[11px] leading-relaxed" style={{ color: "var(--mis-muted)" }}>
              {copy.ledgerState}: {evidenceStatus === "pass"
                ? (locale === "zh" ? "已有 run / artifact / evaluation 证据，可进入交付审查。" : "Run / artifact / evaluation evidence is present for delivery review.")
                : evidenceStatus === "attention"
                  ? (locale === "zh" ? "存在待处理审批，交付前需要人工确认。" : "Pending approval exists and needs human review before delivery.")
                  : (locale === "zh" ? "任务还没有完整运行证据。" : "The task does not have a complete run evidence chain yet.")}
            </p>
          </div>
          <div className="flex flex-wrap gap-2 text-[10px]">
            {latestRun && (
              <Link
                to={`/admin/runs/${latestRun.run_id}`}
                className="rounded px-2.5 py-1.5"
                style={{ background: "rgba(34,211,238,0.10)", color: "var(--mis-cyan)", border: "1px solid rgba(34,211,238,0.22)" }}
              >
                {copy.openRun}: {latestRun.run_id}
              </Link>
            )}
            {pendingApprovals.length > 0 && (
              <Link
                to="/workspace/approvals"
                className="rounded px-2.5 py-1.5"
                style={{ background: "rgba(251,191,36,0.12)", color: "#FBBF24", border: "1px solid rgba(251,191,36,0.24)" }}
              >
                {copy.reviewApproval}: {pendingApprovals.length}
              </Link>
            )}
          </div>
        </div>
        <div className="mt-3 grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-2">
          {[
            { label: copy.evidenceCounts.runs, value: taskRuns.length, status: taskRuns.length > 0 ? "pass" : "planned" },
            { label: copy.evidenceCounts.artifacts, value: taskArtifacts.length, status: taskArtifacts.length > 0 ? "pass" : "planned" },
            { label: copy.evidenceCounts.approvals, value: `${pendingApprovals.length}/${taskApprovals.length}`, status: pendingApprovals.length > 0 ? "attention" : taskApprovals.length > 0 ? "pass" : "planned" },
            { label: copy.evidenceCounts.evaluations, value: `${passedEvals}/${taskEvals.length}`, status: failedEvals > 0 ? "fail" : taskEvals.length > 0 ? "pass" : "planned" },
            { label: copy.evidenceCounts.memories, value: taskMemories.length, status: taskMemories.length > 0 ? "attention" : "planned" },
            { label: copy.evidenceCounts.benchmark, value: taskCaseRuns.length, status: taskCaseRuns.length > 0 ? "pass" : "planned" },
          ].map(item => (
            <div key={item.label} className="rounded px-3 py-2" style={{ background: "var(--mis-surface2)", border: "1px solid rgba(148,163,184,0.14)" }}>
              <div className="flex items-center justify-between gap-2">
                <span className="text-[10px]" style={{ color: "var(--mis-muted)" }}>{item.label}</span>
                <StatusBadge status={item.status} />
              </div>
              <div className="mt-1 text-sm font-semibold" style={{ color: "var(--mis-text)" }}>{item.value}</div>
            </div>
          ))}
        </div>
        <div
          data-testid="task-detail-execution-posture"
          className="mt-3 rounded-lg p-3"
          style={{ background: "var(--mis-bg)", border: "1px solid rgba(148,163,184,0.14)" }}
        >
          <div className="text-[10px] uppercase tracking-wide mb-2" style={{ color: "var(--mis-muted)" }}>
            {copy.executionPosture}
          </div>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
            <div className="rounded px-3 py-2" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
              <div className="flex items-center justify-between gap-2">
                <span className="text-[10px]" style={{ color: "var(--mis-muted)" }}>{copy.runtimeMode}</span>
                <StatusBadge status={runtimePostureStatus} />
              </div>
              <div className="mt-1 text-[11px] font-semibold" style={{ color: "var(--mis-text)" }}>
                {liveRuntimeRuns.length > 0 && mockRuntimeRuns.length > 0
                  ? copy.mixedRuntimeEvidence
                  : liveRuntimeRuns.length > 0
                    ? copy.liveRuntimeEvidence
                    : mockRuntimeRuns.length > 0
                      ? copy.mockRuntimeEvidence
                      : copy.noRuntimeEvidence}
              </div>
              <div className="mt-1 text-[10px]" style={{ color: "var(--mis-muted)" }}>
                hermes/openclaw {liveRuntimeRuns.length} · mock {mockRuntimeRuns.length}
              </div>
            </div>
            <div className="rounded px-3 py-2" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
              <div className="flex items-center justify-between gap-2">
                <span className="text-[10px]" style={{ color: "var(--mis-muted)" }}>{copy.approvalWall}</span>
                <StatusBadge status={pendingApprovals.length > 0 || latestRun?.approval_required ? "attention" : "pass"} />
              </div>
              <div className="mt-1 text-[11px] font-semibold" style={{ color: "var(--mis-text)" }}>
                {pendingApprovals.length > 0 || latestRun?.approval_required ? copy.approvalPending : copy.approvalClear}
              </div>
              <div className="mt-1 text-[10px]" style={{ color: "var(--mis-muted)" }}>
                pending {pendingApprovals.length} / total {taskApprovals.length}
              </div>
            </div>
            <div className="rounded px-3 py-2" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
              <div className="flex items-center justify-between gap-2">
                <span className="text-[10px]" style={{ color: "var(--mis-muted)" }}>{copy.deliveryGate}</span>
                <StatusBadge status={deliveryGateStatus} />
              </div>
              <div className="mt-1 text-[11px] font-semibold" style={{ color: "var(--mis-text)" }}>
                {deliveryGateStatus === "pass"
                  ? copy.gateReady
                  : deliveryGateStatus === "fail"
                    ? copy.gateBlocked
                    : deliveryGateStatus === "running"
                      ? copy.gateRunning
                      : copy.gateWaiting}
              </div>
              <div className="mt-1 text-[10px]" style={{ color: "var(--mis-muted)" }}>
                eval pass/fail {passedEvals}/{failedEvals} · artifacts {taskArtifacts.length}
              </div>
            </div>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-4">
        {/* Acceptance Criteria */}
        <div
          className="rounded-xl p-4"
          style={{ background: "var(--mis-surface)", border: "1px solid var(--mis-border)" }}
        >
          <div className="text-xs font-semibold flex items-center gap-1.5 mb-3" style={{ color: "var(--mis-text)" }}>
            <CheckCircle size={13} style={{ color: "var(--mis-success)" }} />
            {copy.acceptance}
          </div>
          <p className="text-xs leading-relaxed" style={{ color: "var(--mis-dim)" }}>
            {task.acceptance_criteria || copy.noCriteria}
          </p>

          {latestEval && (
            <div className="mt-4">
              <div className="text-[10px] uppercase tracking-wide mb-1.5" style={{ color: "var(--mis-muted)" }}>
                {copy.qualityGate}
              </div>
              <div className="flex items-center gap-3">
                <div className="flex-1 rounded-full h-2 overflow-hidden" style={{ background: "var(--mis-border)" }}>
                  <div
                    className="h-2 rounded-full"
                    style={{
                      width: `${latestScore}%`,
                      background: latestScore >= 80 ? "var(--mis-success)" : "var(--mis-warning)",
                    }}
                  />
                </div>
                <span className="text-xs font-semibold shrink-0" style={{ color: latestScore >= 80 ? "var(--mis-success)" : "var(--mis-warning)" }}>
                  {latestScore}/100
                </span>
                <StatusBadge status={latestEval.pass_fail} />
              </div>
              <p className="text-[11px] mt-2" style={{ color: "var(--mis-muted)" }}>{latestEval.notes}</p>
            </div>
          )}
        </div>

        {/* Collaborators + Approvals */}
        <div className="space-y-3">
          {collabAgents.length > 0 && (
            <div
              className="rounded-xl p-4"
              style={{ background: "var(--mis-surface)", border: "1px solid var(--mis-border)" }}
            >
              <div className="text-xs font-semibold flex items-center gap-1.5 mb-3" style={{ color: "var(--mis-text)" }}>
                <Users size={13} style={{ color: "var(--mis-primary)" }} />
                {copy.collaborators}
              </div>
              <div className="space-y-1.5">
                {collabAgents.map(a => (
                  <div key={a.agent_id} className="flex items-center justify-between text-xs">
                    <span style={{ color: "var(--mis-dim)" }}>{a.name}</span>
                    <StatusBadge status={a.status} />
                  </div>
                ))}
              </div>
            </div>
          )}

          {taskApprovals.length > 0 && (
            <div
              className="rounded-xl p-4"
              style={{ background: "var(--mis-surface)", border: "1px solid var(--mis-border)" }}
            >
              <div className="text-xs font-semibold flex items-center gap-1.5 mb-3" style={{ color: "var(--mis-text)" }}>
                <ShieldCheck size={13} style={{ color: "#FBBF24" }} />
                {copy.approvals}
              </div>
              <div className="space-y-2">
                {taskApprovals.map(ap => (
                  <div key={ap.approval_id} className="p-2.5 rounded-lg" style={{ background: "var(--mis-surface2)" }}>
                    <div className="flex items-center justify-between">
                      <span className="text-[11px] truncate" style={{ color: "var(--mis-dim)" }}>{ap.reason}</span>
                      <StatusBadge status={ap.decision} />
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>

      <div
        className="rounded-xl p-4"
        style={{ background: "var(--mis-surface)", border: "1px solid var(--mis-border)" }}
      >
        <div className="text-xs font-semibold flex items-center gap-1.5 mb-3" style={{ color: "var(--mis-text)" }}>
          <FileText size={13} style={{ color: "var(--mis-cyan)" }} />
          {copy.deliverables}
        </div>
        {taskArtifacts.length === 0 ? (
          <p className="text-xs" style={{ color: "var(--mis-muted)" }}>{copy.noDeliverables}</p>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {taskArtifacts.map(artifact => (
              <div key={artifact.artifact_id} className="rounded-lg p-3" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
                <div className="flex items-center justify-between gap-2">
                  <div className="text-xs font-medium truncate" style={{ color: "var(--mis-text)" }}>{artifact.title}</div>
                  <span className="text-[10px] rounded px-1.5 py-0.5" style={{ color: "var(--mis-cyan)", background: "rgba(34,211,238,0.10)" }}>{artifact.artifact_type}</span>
                </div>
                <p className="text-[11px] leading-relaxed mt-2" style={{ color: "var(--mis-dim)" }}>{artifact.summary}</p>
                <div className="mt-2 flex items-center justify-between gap-2">
                  <div className="text-[10px]" style={{ color: "var(--mis-muted)" }}>{new Date(artifact.created_at).toLocaleString(locale === "zh" ? "zh-CN" : "en-US")}</div>
                  {hasApprovedDelivery && (
                    <a
                      data-testid="approved-artifact-download"
                      href={`/mis-api/artifacts/${encodeURIComponent(artifact.artifact_id)}/download`}
                      download
                      title={copy.downloadApproved}
                      aria-label={copy.downloadApproved}
                      className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded"
                      style={{ color: "var(--mis-cyan)", border: "1px solid rgba(34,211,238,0.24)", background: "rgba(34,211,238,0.08)" }}
                    >
                      <Download size={13} />
                    </a>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      <div
        className="rounded-xl p-4"
        style={{ background: "var(--mis-surface)", border: "1px solid var(--mis-border)" }}
      >
        <div className="text-xs font-semibold flex items-center gap-1.5 mb-3" style={{ color: "var(--mis-text)" }}>
          <ShieldCheck size={13} style={{ color: "var(--mis-success)" }} />
          {copy.benchmarkEvidence}
        </div>
        {taskCaseRuns.length === 0 ? (
          <p className="text-xs" style={{ color: "var(--mis-muted)" }}>{copy.noBenchmarkEvidence}</p>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {taskCaseRuns.map(caseRun => (
              <div key={caseRun.case_run_id || `${caseRun.case_id}-${caseRun.run_id}`} className="rounded-lg p-3" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
                <div className="flex items-center justify-between gap-2">
                  <div className="text-xs font-medium truncate" style={{ color: "var(--mis-text)" }}>{caseRun.case_title || caseRun.case_id}</div>
                  <StatusBadge status={caseRun.pass_fail} />
                </div>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  <span className="text-[10px] rounded px-1.5 py-0.5" style={{ color: "var(--mis-cyan)", background: "rgba(34,211,238,0.10)" }}>{caseRun.case_type || caseRun.runner_type}</span>
                  <span className="text-[10px] rounded px-1.5 py-0.5" style={{ color: "var(--mis-success)", background: "rgba(45,212,191,0.10)" }}>{Math.round((caseRun.score <= 1 ? caseRun.score * 100 : caseRun.score))}/100</span>
                  <span className="text-[10px] rounded px-1.5 py-0.5" style={{ color: "var(--mis-muted)", background: "var(--mis-bg)" }}>{caseRun.review_status || "open"}</span>
                </div>
                <div className="mt-2 text-[10px] font-mono truncate" style={{ color: "var(--mis-muted)" }}>
                  {caseRun.run_id ? <Link to={`/admin/runs/${caseRun.run_id}`} style={{ color: "var(--mis-cyan)" }}>{caseRun.run_id}</Link> : caseRun.case_id}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Runs */}
      <div
        className="rounded-xl p-4"
        style={{ background: "var(--mis-surface)", border: "1px solid var(--mis-border)" }}
      >
        <div className="text-xs font-semibold mb-3" style={{ color: "var(--mis-text)" }}>{copy.relatedRuns}</div>
        {taskRuns.length === 0 ? (
          <p className="text-xs" style={{ color: "var(--mis-muted)" }}>{copy.noRuns}</p>
        ) : (
          <table className="w-full text-xs">
            <thead>
              <tr style={{ color: "var(--mis-muted)" }}>
                {copy.headers.map(h => (
                  <th key={h} className="text-left pb-2 font-medium pr-4">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {taskRuns.map(run => (
                <tr key={run.run_id} style={{ color: "var(--mis-dim)" }}>
                  <td className="py-2 pr-4 font-mono text-[11px]">
                    <Link to={`/admin/runs/${run.run_id}`} className="hover:opacity-80" style={{ color: "var(--mis-cyan)" }}>{run.run_id}</Link>
                  </td>
                  <td className="py-2 pr-4">{run.agent_id}</td>
                  <td className="py-2 pr-4">{run.runtime_type}</td>
                  <td className="py-2 pr-4"><StatusBadge status={run.status} /></td>
                  <td className="py-2 pr-4">${run.cost_usd.toFixed(3)}</td>
                  <td className="py-2 pr-4">{run.input_tokens + run.output_tokens}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Memory Candidates */}
      {taskMemories.length > 0 && (
        <div
          className="rounded-xl p-4"
          style={{ background: "var(--mis-surface)", border: "1px solid var(--mis-border)" }}
        >
          <div className="text-xs font-semibold mb-3" style={{ color: "var(--mis-text)" }}>{copy.memory}</div>
          <div className="space-y-2">
            {taskMemories.map(m => (
              <div key={m.memory_id} className="p-2.5 rounded-lg flex items-start justify-between gap-3" style={{ background: "var(--mis-surface2)" }}>
                <p className="text-[11px] leading-relaxed" style={{ color: "var(--mis-dim)" }}>{m.canonical_text}</p>
                <StatusBadge status={m.review_status} />
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
