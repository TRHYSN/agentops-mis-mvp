import { Link, useParams } from "react-router";
import { Cpu, DollarSign, Clock, Download, GitBranch, AlertTriangle, ShieldCheck, Network, Brain } from "lucide-react";
import { StatusBadge } from "../shared/StatusBadge";
import { RiskBadge } from "../shared/RiskBadge";
import { AuditTimeline } from "../shared/AuditTimeline";
import { loadAudit, loadOperatorEvidenceReport, loadRunDetail, loadRunEvidenceGraph, loadRuns, useLiveData } from "../../data/liveApi";
import { usePreferences } from "../../context/PreferencesContext";

export function RunDetail() {
  const { id } = useParams<{ id: string }>();
  const { locale } = usePreferences();
  const zh = locale === "zh";
  const { data, loading, error } = useLiveData(async () => {
    const [detail, allRuns, auditLogs, evidenceGraph, evidenceReport] = await Promise.all([
      loadRunDetail(id || ""),
      loadRuns(),
      loadAudit(),
      loadRunEvidenceGraph(id || ""),
      loadOperatorEvidenceReport(1, { runId: id || "" }),
    ]);
    return { detail, allRuns, auditLogs, evidenceGraph, evidenceReport };
  }, [id]);

  if (loading) {
    return <p className="text-xs" style={{ color: "var(--mis-muted)" }}>Loading live run detail...</p>;
  }
  if (error || !data?.detail?.run) {
    return <p className="text-xs" style={{ color: "#F87171" }}>Live run detail unavailable: {error || "not found"}</p>;
  }

  const run = data.detail.run;
  const runTools = data.detail.tool_calls;
  const runApprovals = data.detail.approvals;
  const runArtifacts = data.detail.artifacts || [];
  const runEval = data.detail.evaluations[0];
  const runEvaluations = data.detail.evaluations;
  const caseRuns = data.detail.evaluation_case_runs || [];
  const runAudit = data.auditLogs.filter(a => a.entity_id === run.run_id || runTools.some(tc => a.entity_id === tc.tool_call_id)).slice(0, 5);
  const childRuns = data.allRuns.filter(r => r.parent_run_id === run.run_id);
  const parentRun = run.parent_run_id ? data.allRuns.find(r => r.run_id === run.parent_run_id) : null;
  const score = runEval ? (runEval.score <= 1 ? Math.round(runEval.score * 100) : Math.round(runEval.score)) : 0;
  const pendingApprovals = runApprovals.filter(approval => approval.decision === "pending");
  const hasApprovedDelivery = runApprovals.some(approval => approval.decision === "approved");
  const failedTools = runTools.filter(tool => ["failed", "error", "blocked"].includes(tool.status));
  const failedEvals = runEvaluations.filter(ev => ev.pass_fail === "fail" || ev.pass_fail === "failed");
  const evidenceGraph = data.evidenceGraph;
  const graphCounts = evidenceGraph.evidence_counts || {};
  const graphNodeCount = evidenceGraph.nodes?.length || 0;
  const graphEdgeCount = evidenceGraph.edges?.length || 0;
  const graphAvailable = evidenceGraph.status !== "unavailable" && evidenceGraph.operation === "work_delivery_graph_readback";
  const graphSafety = evidenceGraph.safety || {};
  const reportRun = data.evidenceReport.runs.find(item => item.run_id === run.run_id);
  const knowledgeReceipt = reportRun?.worker_knowledge_retrieval;
  const contextReportAvailable = data.evidenceReport.status !== "unavailable" && Boolean(reportRun);
  const contextApplicable = knowledgeReceipt?.applicable === true;
  const contextStatus = contextReportAvailable ? knowledgeReceipt?.status || "not_applicable" : "unavailable";
  const contextReceiptStatus = !contextReportAvailable
    ? "attention"
    : !contextApplicable
      ? "planned"
      : contextStatus === "ready"
        ? "pass"
        : contextStatus === "unavailable"
          ? "attention"
          : "fail";
  const contextBlockCount = knowledgeReceipt?.context_block_count || 0;
  const approvedMemoryIds = knowledgeReceipt?.approved_memory_ids || [];
  const contextPacketHashes = knowledgeReceipt?.context_packet_hashes || [];
  const knowledgePaths = knowledgeReceipt?.paths || [];
  const pendingMemoryReviews = reportRun?.memory_review?.pending_review || 0;
  const runMemoryItems = (reportRun?.memory_review?.items || []).map(item => ({
    memoryId: typeof item.memory_id === "string" ? item.memory_id : "",
    memoryType: typeof item.memory_type === "string" ? item.memory_type : "unknown",
    reviewStatus: typeof item.review_status === "string" ? item.review_status : "unknown",
    sourceRef: typeof item.source_ref === "string" ? item.source_ref : "",
  })).filter(item => item.memoryId && item.sourceRef === run.run_id);
  const contextCopy = zh ? {
    title: "项目上下文收据",
    ready: "该 Worker 在执行适配器前读取了受治理的 Context Packet。收据会区分版本化项目知识与实际使用的已审核记忆，只展示 ID、哈希、计数和脱敏证明。",
    missing: "该 Run 尚没有完整的项目上下文消费证据；请查看证据报告中的缺失项。",
    notApplicable: "该 Run 不是 Agent Worker 执行，当前没有项目上下文消费收据。",
    unavailable: "连接的服务暂时无法返回该 Run 的项目上下文收据。",
    contextBlocks: "上下文块",
    knowledgeSources: "知识来源",
    approvedMemories: "已用审核记忆",
    consumedCalls: "消费工具调用",
    packetHash: "Context Packet 哈希",
    memoryIds: "已使用的记忆 ID",
    sourcePaths: "项目知识来源",
    resultingMemories: "本次 Run 产生的记忆证据",
    contextBodyOmitted: "上下文正文未落库",
    queryOmitted: "检索问题未保存",
    snippetOmitted: "检索片段未保存",
    rawContentOmitted: "原始内容未保存",
    transcriptOmitted: "完整对话未保存",
    promptOmitted: "原始提示词未保存",
    responseOmitted: "原始回复未保存",
    tokenOmitted: "密钥未保存",
    openMemory: "打开记忆审核",
    pendingCandidates: "条记忆待审核",
  } : {
    title: "Project Context Receipt",
    ready: "This Worker consumed a governed Context Packet before adapter execution. The receipt distinguishes versioned project Knowledge from any approved Memory IDs actually used and shows only safe evidence.",
    missing: "This run does not yet have complete project-context consumption evidence; inspect the evidence report gaps.",
    notApplicable: "This is not an Agent Worker run, so no project-context consumption receipt applies.",
    unavailable: "The connected service cannot currently return a project-context receipt for this run.",
    contextBlocks: "Context blocks",
    knowledgeSources: "Knowledge sources",
    approvedMemories: "Approved memories used",
    consumedCalls: "Consumed tool calls",
    packetHash: "Context Packet hash",
    memoryIds: "Consumed Memory IDs",
    sourcePaths: "Project Knowledge sources",
    resultingMemories: "Memory evidence produced by this run",
    contextBodyOmitted: "Context body not persisted",
    queryOmitted: "Raw query omitted",
    snippetOmitted: "Retrieval snippets omitted",
    rawContentOmitted: "Raw content omitted",
    transcriptOmitted: "Full transcript omitted",
    promptOmitted: "Raw prompt omitted",
    responseOmitted: "Raw response omitted",
    tokenOmitted: "Credential omitted",
    openMemory: "Open memory review",
    pendingCandidates: "memory reviews pending",
  };
  const liveRuntime = run.runtime_type === "hermes" || run.runtime_type === "openclaw";
  const evidenceChainStatus = run.status === "failed" || run.status === "blocked" || failedTools.length > 0 || failedEvals.length > 0
    ? "fail"
    : pendingApprovals.length > 0 || run.approval_required
      ? "attention"
      : runTools.length > 0 && runEvaluations.length > 0 && runArtifacts.length > 0 && runAudit.length > 0
        ? "pass"
        : run.status === "running"
          ? "running"
          : "planned";
  const runtimeEvidenceStatus = liveRuntime ? "live" : run.runtime_type === "mock" ? "dry_run" : "ready";

  return (
    <div className="space-y-5 w-full">
      {/* Header */}
      <div
        className="rounded-xl p-5"
        style={{ background: "var(--mis-surface)", border: "1px solid var(--mis-border)" }}
      >
        <div className="flex items-center gap-3 mb-3">
          <h1 className="text-base font-semibold font-mono" style={{ color: "var(--mis-text)" }}>{run.run_id}</h1>
          <StatusBadge status={run.status} size="md" />
        </div>

        <div className="grid grid-cols-4 gap-4">
          {[
            { label: "Task", value: run.task_id },
            { label: "Agent", value: run.agent_id },
            { label: "Runtime", value: run.runtime_type },
            { label: "Model", value: run.model_name || "—" },
            { label: "Started", value: new Date(run.started_at).toLocaleTimeString() },
            { label: "Duration", value: run.duration_ms > 0 ? `${(run.duration_ms / 1000).toFixed(0)}s` : "In progress" },
            { label: "Trace ID", value: run.trace_id || "—" },
            { label: "Approval Required", value: run.approval_required ? "Yes" : "No" },
          ].map(({ label, value }) => (
            <div key={label}>
              <div className="text-[10px] uppercase tracking-wide" style={{ color: "var(--mis-muted)" }}>{label}</div>
              <div className="text-xs mt-0.5 font-mono" style={{ color: "var(--mis-dim)" }}>{value}</div>
            </div>
          ))}
        </div>
      </div>

      <div
        data-testid="run-detail-evidence-chain"
        className="rounded-xl p-4"
        style={{ background: "var(--mis-surface)", border: "1px solid var(--mis-border)" }}
      >
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="text-xs font-semibold flex items-center gap-1.5" style={{ color: "var(--mis-text)" }}>
              <ShieldCheck size={13} style={{ color: evidenceChainStatus === "fail" ? "#F87171" : evidenceChainStatus === "attention" ? "#FBBF24" : "var(--mis-success)" }} />
              Run Evidence Chain
            </div>
            <p className="mt-1 text-[11px] leading-relaxed" style={{ color: "var(--mis-muted)" }}>
              {evidenceChainStatus === "pass"
                ? "Tool, evaluation, artifact and audit evidence are present for delivery review."
                : evidenceChainStatus === "attention"
                  ? "This run is waiting on human approval before it should be treated as accepted delivery."
                  : evidenceChainStatus === "fail"
                    ? "This run has failed or blocked evidence and needs operator review."
                    : "The run evidence chain is still incomplete."}
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <StatusBadge status={evidenceChainStatus} size="md" label={`Chain: ${evidenceChainStatus}`} />
            <StatusBadge status={runtimeEvidenceStatus} size="md" label={liveRuntime ? "Hermes/OpenClaw live" : run.runtime_type === "mock" ? "Mock/offline" : run.runtime_type} />
          </div>
        </div>
        <div className="mt-3 grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-2">
          {[
            { label: "Tool calls", value: runTools.length, status: failedTools.length > 0 ? "fail" : runTools.length > 0 ? "pass" : "planned" },
            { label: "Evaluations", value: runEvaluations.length, status: failedEvals.length > 0 ? "fail" : runEvaluations.length > 0 ? "pass" : "planned" },
            { label: "Artifacts", value: runArtifacts.length, status: runArtifacts.length > 0 ? "pass" : "planned" },
            { label: "Approvals", value: `${pendingApprovals.length}/${runApprovals.length}`, status: pendingApprovals.length > 0 || run.approval_required ? "attention" : runApprovals.length > 0 ? "pass" : "planned" },
            { label: "Audit refs", value: runAudit.length, status: runAudit.length > 0 ? "pass" : "planned" },
            { label: "Benchmarks", value: caseRuns.length, status: caseRuns.length > 0 ? "pass" : "planned" },
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
        <div className="mt-3 flex flex-wrap gap-2 text-[10px]">
          <Link
            to={`/admin/tasks/${run.task_id}`}
            className="rounded px-2.5 py-1.5"
            style={{ background: "rgba(34,211,238,0.10)", color: "var(--mis-cyan)", border: "1px solid rgba(34,211,238,0.22)" }}
          >
            Open task: {run.task_id}
          </Link>
          {pendingApprovals.length > 0 && (
            <Link
              to="/workspace/approvals"
              className="rounded px-2.5 py-1.5"
              style={{ background: "rgba(251,191,36,0.12)", color: "#FBBF24", border: "1px solid rgba(251,191,36,0.24)" }}
            >
              Review approvals: {pendingApprovals.length}
            </Link>
          )}
        </div>
      </div>

      <div
        data-testid="run-detail-project-context-receipt"
        className="rounded-xl p-4"
        style={{ background: "var(--mis-surface)", border: "1px solid var(--mis-border)" }}
      >
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="max-w-4xl">
            <div className="text-xs font-semibold flex items-center gap-1.5" style={{ color: "var(--mis-text)" }}>
              <Brain size={13} style={{ color: contextReceiptStatus === "pass" ? "var(--mis-success)" : "var(--mis-purple)" }} />
              {contextCopy.title}
            </div>
            <p className="mt-1 text-[11px] leading-relaxed" style={{ color: "var(--mis-muted)" }}>
              {!contextReportAvailable
                ? contextCopy.unavailable
                : contextReceiptStatus === "pass"
                ? contextCopy.ready
                : contextApplicable
                  ? contextCopy.missing
                  : contextCopy.notApplicable}
            </p>
          </div>
          <StatusBadge status={contextReceiptStatus} size="md" label={`Context: ${contextStatus}`} />
        </div>

        <div className="mt-3 grid grid-cols-2 md:grid-cols-4 gap-2">
          {[
            { label: contextCopy.contextBlocks, value: contextBlockCount },
            { label: contextCopy.knowledgeSources, value: knowledgePaths.length },
            { label: contextCopy.approvedMemories, value: approvedMemoryIds.length },
            { label: contextCopy.consumedCalls, value: knowledgeReceipt?.consumed_tool_calls || 0 },
          ].map(item => (
            <div key={item.label} className="rounded px-3 py-2" style={{ background: "var(--mis-surface2)", border: "1px solid rgba(148,163,184,0.14)" }}>
              <span className="text-[10px]" style={{ color: "var(--mis-muted)" }}>{item.label}</span>
              <div className="mt-1 text-sm font-semibold" style={{ color: "var(--mis-text)" }}>{item.value}</div>
            </div>
          ))}
        </div>

        {contextApplicable && (
          <div className="mt-3 space-y-2 text-[10px]">
            <div className="rounded px-3 py-2" style={{ background: "var(--mis-surface2)" }}>
              <div style={{ color: "var(--mis-muted)" }}>{contextCopy.packetHash}</div>
              <div className="mt-1 break-all font-mono" style={{ color: "var(--mis-dim)" }}>
                {contextPacketHashes.length > 0 ? contextPacketHashes.join(" · ") : "—"}
              </div>
            </div>
            {approvedMemoryIds.length > 0 && (
              <div className="rounded px-3 py-2" style={{ background: "var(--mis-surface2)" }}>
                <div style={{ color: "var(--mis-muted)" }}>{contextCopy.memoryIds}</div>
                <div className="mt-1 flex flex-wrap gap-1.5">
                  {approvedMemoryIds.map(memoryId => (
                    <span key={memoryId} className="rounded px-1.5 py-0.5 font-mono" style={{ color: "var(--mis-cyan)", border: "1px solid rgba(34,211,238,0.20)" }}>{memoryId}</span>
                  ))}
                </div>
              </div>
            )}
            {knowledgePaths.length > 0 && (
              <div className="rounded px-3 py-2" style={{ background: "var(--mis-surface2)" }}>
                <div style={{ color: "var(--mis-muted)" }}>{contextCopy.sourcePaths}</div>
                <div className="mt-1 flex flex-wrap gap-1.5">
                  {knowledgePaths.slice(0, 6).map((path, index) => (
                    <span key={`${path}-${index}`} className="rounded px-1.5 py-0.5 font-mono" style={{ color: "var(--mis-dim)", border: "1px solid var(--mis-border)" }}>{path}</span>
                  ))}
                </div>
              </div>
            )}
            {runMemoryItems.length > 0 && (
              <div className="rounded px-3 py-2" style={{ background: "var(--mis-surface2)" }}>
                <div style={{ color: "var(--mis-muted)" }}>{contextCopy.resultingMemories}</div>
                <div className="mt-1 space-y-1.5">
                  {runMemoryItems.slice(0, 6).map(item => (
                    <div key={item.memoryId} className="flex flex-wrap items-center gap-1.5">
                      <span className="font-mono" style={{ color: "var(--mis-cyan)" }}>{item.memoryId}</span>
                      <span style={{ color: "var(--mis-dim)" }}>{item.memoryType}</span>
                      <StatusBadge status={item.reviewStatus} />
                      <span className="font-mono" style={{ color: "var(--mis-muted)" }}>source: {item.sourceRef}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
            <div className="flex flex-wrap gap-1.5">
              {[
                { label: contextCopy.contextBodyOmitted, ok: knowledgeReceipt?.context_body_not_persisted === true },
                { label: contextCopy.queryOmitted, ok: knowledgeReceipt?.raw_query_omitted === true },
                { label: contextCopy.snippetOmitted, ok: knowledgeReceipt?.snippet_omitted === true },
                { label: contextCopy.rawContentOmitted, ok: knowledgeReceipt?.raw_content_omitted === true },
                { label: contextCopy.transcriptOmitted, ok: knowledgeReceipt?.raw_transcript_omitted === true },
                { label: contextCopy.promptOmitted, ok: knowledgeReceipt?.raw_prompt_omitted === true },
                { label: contextCopy.responseOmitted, ok: knowledgeReceipt?.raw_response_omitted === true },
                { label: contextCopy.tokenOmitted, ok: knowledgeReceipt?.token_omitted === true },
              ].map(item => <StatusBadge key={item.label} status={item.ok ? "pass" : "fail"} label={item.label} />)}
            </div>
          </div>
        )}

        <div className="mt-3 text-[10px]">
          <Link to="/workspace/memory" style={{ color: "var(--mis-cyan)" }}>
            {contextCopy.openMemory} · {pendingMemoryReviews} {contextCopy.pendingCandidates}
          </Link>
        </div>
      </div>

      <div
        id="work-delivery-graph"
        data-testid="run-detail-work-delivery-graph"
        className="rounded-xl p-4"
        style={{ background: "var(--mis-surface)", border: "1px solid var(--mis-border)" }}
      >
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="text-xs font-semibold flex items-center gap-1.5" style={{ color: "var(--mis-text)" }}>
              <Network size={13} style={{ color: graphAvailable ? "var(--mis-cyan)" : "var(--mis-muted)" }} />
              Work Delivery Evidence Graph
            </div>
            <p className="mt-1 text-[11px] leading-relaxed" style={{ color: "var(--mis-muted)" }}>
              {graphAvailable
                ? "Backend readback over MIS ledgers: task, plan, run, tools, runtime events, evaluations, approvals, artifacts, memories and audit evidence."
                : "Evidence graph readback is unavailable on the connected server; detail-derived evidence is still shown below."}
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <StatusBadge status={graphAvailable ? "pass" : "planned"} size="md" label={graphAvailable ? "Graph: ready" : "Graph: unavailable"} />
            <StatusBadge status={graphSafety.token_omitted !== false ? "pass" : "fail"} size="md" label="Token omitted" />
            <StatusBadge status={graphSafety.read_only === false ? "fail" : "pass"} size="md" label="Read-only" />
          </div>
        </div>
        <div className="mt-3 grid grid-cols-2 md:grid-cols-4 xl:grid-cols-8 gap-2">
          {[
            { label: "Plan manifests", value: graphCounts.plan_evidence_manifests || 0 },
            { label: "Tool calls", value: graphCounts.tool_calls || 0 },
            { label: "Runtime events", value: graphCounts.runtime_events || 0 },
            { label: "Evaluations", value: graphCounts.evaluations || 0 },
            { label: "Approvals", value: graphCounts.approvals || 0 },
            { label: "Artifacts", value: graphCounts.artifacts || 0 },
            { label: "Memories", value: graphCounts.memories || 0 },
            { label: "Audit logs", value: graphCounts.audit_logs || 0 },
          ].map(item => (
            <div key={item.label} className="rounded px-3 py-2" style={{ background: "var(--mis-surface2)", border: "1px solid rgba(148,163,184,0.14)" }}>
              <span className="text-[10px]" style={{ color: "var(--mis-muted)" }}>{item.label}</span>
              <div className="mt-1 text-sm font-semibold" style={{ color: "var(--mis-text)" }}>{item.value}</div>
            </div>
          ))}
        </div>
        <div className="mt-3 grid grid-cols-1 md:grid-cols-3 gap-2 text-[10px]">
          <div className="rounded px-3 py-2 font-mono truncate" style={{ background: "var(--mis-surface2)", color: "var(--mis-dim)" }}>
            graph_hash: {evidenceGraph.graph_hash || "unavailable"}
          </div>
          <div className="rounded px-3 py-2 font-mono truncate" style={{ background: "var(--mis-surface2)", color: "var(--mis-dim)" }}>
            plan: {evidenceGraph.agent_plan_id || "unbound"}
          </div>
          <div className="rounded px-3 py-2 font-mono truncate" style={{ background: "var(--mis-surface2)", color: "var(--mis-dim)" }}>
            graph: {graphNodeCount} nodes / {graphEdgeCount} edges
          </div>
        </div>
        <div className="mt-2 text-[10px]" style={{ color: "var(--mis-muted)" }}>
          Authority: {evidenceGraph.authority || "read_model_over_mis_ledgers"}
        </div>
      </div>

      {/* Cost + Tokens */}
      <div className="grid grid-cols-4 gap-3">
        {[
          { icon: <DollarSign size={14} />, label: "Cost", value: `$${run.cost_usd.toFixed(4)}`, color: "var(--mis-success)" },
          { icon: <Cpu size={14} />, label: "Input Tokens", value: run.input_tokens.toLocaleString(), color: "var(--mis-primary)" },
          { icon: <Cpu size={14} />, label: "Output Tokens", value: run.output_tokens.toLocaleString(), color: "var(--mis-cyan)" },
          { icon: <Clock size={14} />, label: "Reasoning Tokens", value: run.reasoning_tokens.toLocaleString(), color: "var(--mis-purple)" },
        ].map(({ icon, label, value, color }) => (
          <div
            key={label}
            className="rounded-xl p-4"
            style={{ background: "var(--mis-surface)", border: "1px solid var(--mis-border)" }}
          >
            <div className="flex items-center gap-1.5 mb-1" style={{ color }}>
              {icon}
              <span className="text-[11px] uppercase tracking-wide" style={{ color: "var(--mis-muted)" }}>{label}</span>
            </div>
            <div className="text-xl font-semibold" style={{ color: "var(--mis-text)" }}>{value}</div>
          </div>
        ))}
      </div>

      {/* Delegation Graph */}
      {(parentRun || childRuns.length > 0) && (
        <div
          className="rounded-xl p-4"
          style={{ background: "var(--mis-surface)", border: "1px solid var(--mis-border)" }}
        >
          <div className="text-xs font-semibold flex items-center gap-1.5 mb-4" style={{ color: "var(--mis-text)" }}>
            <GitBranch size={13} style={{ color: "var(--mis-cyan)" }} />
            Run Delegation Graph
          </div>
          <div className="flex flex-col items-center gap-2">
            {parentRun && (
              <>
                <div
                  className="px-4 py-2 rounded-lg text-xs text-center"
                  style={{ background: "var(--mis-surface2)", color: "var(--mis-dim)", border: "1px solid var(--mis-border)" }}
                >
                  <div className="font-mono font-medium" style={{ color: "var(--mis-text)" }}>{parentRun.run_id}</div>
                  <div style={{ color: "var(--mis-muted)" }}>{parentRun.agent_id} · parent</div>
                </div>
                <div className="w-px h-4" style={{ background: "var(--mis-border)" }} />
              </>
            )}
            <div
              className="px-5 py-2.5 rounded-lg text-xs text-center"
              style={{ background: "rgba(34,211,238,0.08)", color: "var(--mis-cyan)", border: "1px solid rgba(34,211,238,0.2)" }}
            >
              <div className="font-mono font-semibold">{run.run_id}</div>
              <div style={{ color: "var(--mis-dim)" }}>{run.agent_id} · current</div>
              {run.delegation_id && (
                <div className="text-[10px] mt-0.5" style={{ color: "var(--mis-muted)" }}>del: {run.delegation_id}</div>
              )}
            </div>
            {childRuns.map((child, i) => (
              <div key={child.run_id} className="flex flex-col items-center gap-2">
                <div className="w-px h-4" style={{ background: "var(--mis-border)" }} />
                <div
                  className="px-4 py-2 rounded-lg text-xs text-center"
                  style={{ background: "var(--mis-surface2)", color: "var(--mis-dim)", border: "1px solid var(--mis-border)" }}
                >
                  <div className="font-mono font-medium" style={{ color: "var(--mis-text)" }}>{child.run_id}</div>
                  <div style={{ color: "var(--mis-muted)" }}>{child.agent_id} · child {i + 1}</div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Tool Calls */}
      <div
        className="rounded-xl p-4"
        style={{ background: "var(--mis-surface)", border: "1px solid var(--mis-border)" }}
      >
        <div className="text-xs font-semibold mb-3" style={{ color: "var(--mis-text)" }}>Tool Calls ({runTools.length})</div>
        {runTools.length === 0 ? (
          <p className="text-xs" style={{ color: "var(--mis-muted)" }}>No tool calls recorded.</p>
        ) : (
          <table className="w-full text-xs">
            <thead>
              <tr style={{ color: "var(--mis-muted)" }}>
                {["Tool", "Category", "Target", "Risk", "Status", "Duration"].map(h => (
                  <th key={h} className="text-left pb-2 font-medium pr-3">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {runTools.map(tc => (
                <tr key={tc.tool_call_id} style={{ color: "var(--mis-dim)" }}>
                  <td className="py-2 pr-3 font-medium" style={{ color: "var(--mis-text)" }}>{tc.tool_name}</td>
                  <td className="py-2 pr-3">{tc.tool_category}</td>
                  <td className="py-2 pr-3 text-[11px] max-w-32 truncate">{tc.target_resource}</td>
                  <td className="py-2 pr-3"><RiskBadge risk={tc.risk_level} /></td>
                  <td className="py-2 pr-3"><StatusBadge status={tc.status} /></td>
                  <td className="py-2 pr-3">
                    {tc.ended_at ? `${((new Date(tc.ended_at).getTime() - new Date(tc.started_at).getTime()) / 1000).toFixed(1)}s` : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Error Panel */}
      {run.status === "failed" && run.error_message && (
        <div
          className="rounded-xl p-4"
          style={{ background: "rgba(248,113,113,0.06)", border: "1px solid rgba(248,113,113,0.2)" }}
        >
          <div className="flex items-center gap-2 mb-2">
            <AlertTriangle size={14} style={{ color: "#F87171" }} />
            <span className="text-xs font-semibold" style={{ color: "#F87171" }}>{run.error_type}</span>
          </div>
          <p className="text-xs" style={{ color: "var(--mis-dim)" }}>{run.error_message}</p>
        </div>
      )}

      {caseRuns.length > 0 && (
        <div
          className="rounded-xl p-4"
          style={{ background: "var(--mis-surface)", border: "1px solid var(--mis-border)" }}
        >
          <div className="text-xs font-semibold flex items-center gap-1.5 mb-3" style={{ color: "var(--mis-text)" }}>
            <ShieldCheck size={13} style={{ color: "var(--mis-success)" }} />
            Evaluation Case Evidence
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {caseRuns.map(caseRun => (
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
                  {caseRun.run_id && caseRun.run_id !== run.run_id ? <Link to={`/admin/runs/${caseRun.run_id}`} style={{ color: "var(--mis-cyan)" }}>{caseRun.run_id}</Link> : caseRun.case_id}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Evaluation */}
      {runEval && (
        <div
          className="rounded-xl p-4"
          style={{ background: "var(--mis-surface)", border: "1px solid var(--mis-border)" }}
        >
          <div className="flex items-center justify-between mb-3">
            <div className="text-xs font-semibold" style={{ color: "var(--mis-text)" }}>Evaluation Result</div>
            <div className="flex items-center gap-2">
              <span className="text-sm font-semibold" style={{ color: runEval.score >= 80 ? "var(--mis-success)" : "var(--mis-warning)" }}>
                {score}/100
              </span>
              <StatusBadge status={runEval.pass_fail} size="md" />
            </div>
          </div>
          <p className="text-xs" style={{ color: "var(--mis-dim)" }}>{runEval.notes}</p>
          <div className="text-[11px] mt-1" style={{ color: "var(--mis-muted)" }}>Evaluator: {runEval.evaluator_type}</div>
        </div>
      )}

      {runArtifacts.length > 0 && (
        <div
          className="rounded-xl p-4"
          style={{ background: "var(--mis-surface)", border: "1px solid var(--mis-border)" }}
        >
          <div className="text-xs font-semibold mb-3" style={{ color: "var(--mis-text)" }}>Artifacts</div>
          <div className="space-y-2">
            {runArtifacts.map((artifact) => (
              <div key={artifact.artifact_id} className="p-3 rounded-lg" style={{ background: "var(--mis-surface2)" }}>
                <div className="flex items-center justify-between gap-2">
                  <div className="text-xs font-medium" style={{ color: "var(--mis-text)" }}>{artifact.title}</div>
                  {hasApprovedDelivery && (
                    <a
                      data-testid="approved-artifact-download"
                      href={`/mis-api/artifacts/${encodeURIComponent(artifact.artifact_id)}/download`}
                      download
                      title="Download approved artifact"
                      aria-label="Download approved artifact"
                      className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded"
                      style={{ color: "var(--mis-cyan)", border: "1px solid rgba(34,211,238,0.24)", background: "rgba(34,211,238,0.08)" }}
                    >
                      <Download size={13} />
                    </a>
                  )}
                </div>
                <div className="text-[11px] mt-1" style={{ color: "var(--mis-dim)" }}>{artifact.summary}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Audit Timeline */}
      {runAudit.length > 0 && (
        <div
          className="rounded-xl p-4"
          style={{ background: "var(--mis-surface)", border: "1px solid var(--mis-border)" }}
        >
          <div className="text-xs font-semibold mb-4" style={{ color: "var(--mis-text)" }}>Audit Timeline</div>
          <AuditTimeline logs={runAudit} />
        </div>
      )}
    </div>
  );
}
