import { Activity, Bot, Database, Plug, Radio, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";
import { ConnectorTopology } from "../connectors/ConnectorTopology";
import { ConnectorCard } from "../shared/ConnectorCard";
import { StatusBadge } from "../shared/StatusBadge";
import {
  loadAudit,
  loadCommercialConfigStatus,
  loadRuntimeConnectors,
  loadWorkerAdapterReadiness,
  loadWorkerFleet,
  updateRuntimeConnectorTrust,
  useLiveData,
} from "../../data/liveApi";
import { pick, usePreferences } from "../../context/PreferencesContext";

export function RuntimeConnectors() {
  const { locale } = usePreferences();
  const [trustAction, setTrustAction] = useState<string | null>(null);
  const [trustMessage, setTrustMessage] = useState<string | null>(null);
  const { data, loading, error, refresh } = useLiveData(async () => {
    const [runtimeConnectors, auditLogs, commercialConfigStatus, workerFleet, adapterReadiness] = await Promise.all([
      loadRuntimeConnectors(),
      loadAudit(),
      loadCommercialConfigStatus(),
      loadWorkerFleet(),
      loadWorkerAdapterReadiness(),
    ]);
    const connectorAuditLogs = auditLogs.filter(a =>
      a.entity_type === "runtime_connectors" || a.entity_type === "runtime_connector" || a.entity_type === "connector"
    );
    return { runtimeConnectors, connectorAuditLogs, commercialConfigStatus, workerFleet, adapterReadiness };
  }, []);
  const runtimeConnectors = data?.runtimeConnectors || [];
  const connectorAuditLogs = data?.connectorAuditLogs || [];
  const commercialConfigStatus = data?.commercialConfigStatus;
  const workerFleet = data?.workerFleet;
  const adapterReadiness = data?.adapterReadiness;
  const copy = pick(locale, {
    en: {
      title: "Runtime Connectors",
      subtitle: "Vendor-neutral control plane · live AgentOps MIS backend",
      loading: "Loading live connectors...",
      backendUnavailable: "Live backend unavailable",
      refresh: "Refresh live",
      ready: "Ready",
      live: "Live",
      dryRun: "Dry-run",
      unavailable: "Unavailable",
      trusted: "Trusted",
      reviewRequired: "Review",
      blocked: "Blocked",
      trustRegistry: "Runtime Trust Registry",
      trustSummary: "Controls whether live adapters are trusted, require review, or are blocked before customer worker execution.",
      updatingTrust: "Updating...",
      trustUpdated: "Trust policy updated",
      trustImpact: "Trust impact",
      liveWorkerGate: "Live worker gate",
      operatorReadback: "Operator readback",
      auditRefs: "Audit refs",
      trustedImpact: "Confirmed live worker runs may proceed after the normal confirm/prepared-action gates pass.",
      reviewImpact: "Operators should review this connector before confirmed live worker runs; use dry-run or approval-gated paths until reviewed.",
      blockedImpact: "Confirmed live customer-worker execution is blocked before adapter invocation. No live run should be created while blocked.",
      capabilityManifest: "Capability Manifest",
      policyHash: "Policy hash",
      observation: "Observation",
      riskFloor: "Risk",
      externalWrite: "External write",
      confirm: "Confirm",
      trustPolicy: "Trust policy",
      commercial: "Commercial",
      commercialConfig: "Commercial config",
      commercialConfigSummary: "Read-only entitlement and retention controls. This panel never calls billing, cleanup, live runtimes, or exposes raw config.",
      edition: "Edition",
      billingProvider: "Billing provider",
      billingCalls: "Billing calls",
      cleanupExecution: "Cleanup execution",
      approvalGate: "Approval gate",
      legalHoldGate: "Legal hold gate",
      enabledCapabilities: "Enabled capabilities",
      disabledCapabilities: "Disabled capabilities",
      rawConfigOmitted: "Raw config omitted",
      tokenOmitted: "Token omitted",
      source: "Source",
      notConfigured: "Not configured",
      operatorNextAction: "Operator next action",
      operatorNextActionSummary: "Verify the same read-only state from CLI before promoting hosted, billing or cleanup work.",
      verifyFromCli: "Verify from CLI",
      evidenceDoc: "Evidence doc",
      productionBoundary: "Production boundary",
      noBillingCleanup: "No billing, cleanup, hosted-readiness or live-runtime action is performed from this panel.",
      plannedConnectors: "Planned Connectors",
      recentRuntimeEvents: "Recent Runtime Events",
      topology: "Runtime connection topology",
      topologySummary: "Human intent flows through MIS policy and connector trust before a Worker reaches a runtime; bounded evidence returns to the ledger.",
      controlPlane: "MIS control plane",
      controlPlaneDetail: "Task, plan, approval and workspace authority",
      trustNode: "Trust registry",
      trustNodeDetail: "Connector policy and live-run gate",
      adapterNode: "Runtime adapters",
      adapterNodeDetail: "Provider-neutral execution boundary",
      workerNode: "Worker instances",
      workerNodeDetail: "Scoped identities and heartbeat state",
      ledgerNode: "Evidence ledger",
      ledgerNodeDetail: "Run, tool, evaluation and audit readback",
      codexDetail: "Open Codex instances and bidirectional path",
    },
    zh: {
      title: "运行时连接器",
      subtitle: "供应商中立的控制平面 · 连接本地 AgentOps MIS 后端",
      loading: "正在加载实时连接器...",
      backendUnavailable: "本地后端不可用",
      refresh: "刷新实时状态",
      ready: "就绪",
      live: "实时",
      dryRun: "安全预演",
      unavailable: "不可用",
      trusted: "已信任",
      reviewRequired: "需复核",
      blocked: "已阻止",
      trustRegistry: "运行时信任登记",
      trustSummary: "控制 live adapter 在客户 worker 执行前是可信、需复核，还是被阻止。",
      updatingTrust: "正在更新...",
      trustUpdated: "信任策略已更新",
      trustImpact: "信任影响",
      liveWorkerGate: "Live worker Gate",
      operatorReadback: "操作员回读",
      auditRefs: "审计引用",
      trustedImpact: "确认后的真实 worker run 可以在常规确认 / prepared-action gate 通过后继续执行。",
      reviewImpact: "操作员应先复核此连接器；复核前使用 dry-run 或审批式路径。",
      blockedImpact: "确认后的客户 worker 真实执行会在调用 adapter 前被阻断；blocked 状态下不应创建 live run。",
      capabilityManifest: "能力清单",
      policyHash: "策略哈希",
      observation: "观测级别",
      riskFloor: "风险下限",
      externalWrite: "外部写入",
      confirm: "确认要求",
      trustPolicy: "信任策略",
      commercial: "商业状态",
      commercialConfig: "商业配置",
      commercialConfigSummary: "只读 entitlement 与 retention 控制面板；不会调用 billing、cleanup、真实运行时，也不会暴露原始配置。",
      edition: "版本",
      billingProvider: "计费提供方",
      billingCalls: "计费调用",
      cleanupExecution: "清理执行",
      approvalGate: "审批 Gate",
      legalHoldGate: "Legal hold Gate",
      enabledCapabilities: "已启用能力",
      disabledCapabilities: "已禁用能力",
      rawConfigOmitted: "原始配置已省略",
      tokenOmitted: "Token 已省略",
      source: "来源",
      notConfigured: "未配置",
      operatorNextAction: "操作员下一步",
      operatorNextActionSummary: "在推进 hosted、billing 或 cleanup 工作前，用 CLI 回读同一份只读状态。",
      verifyFromCli: "CLI 验证",
      evidenceDoc: "证据文档",
      productionBoundary: "生产边界",
      noBillingCleanup: "此面板不会执行 billing、cleanup、hosted-readiness 或真实运行时动作。",
      plannedConnectors: "计划接入的连接器",
      recentRuntimeEvents: "最近运行时事件",
      topology: "运行时连接拓扑",
      topologySummary: "人的任务先经过 MIS 策略与连接器信任门，再由 Worker 访问运行时；受限证据沿反向链路回到账本。",
      controlPlane: "MIS 控制面",
      controlPlaneDetail: "任务、计划、审批和工作区权威",
      trustNode: "信任登记",
      trustNodeDetail: "连接器策略与真实运行门",
      adapterNode: "运行时适配器",
      adapterNodeDetail: "供应商中立的执行边界",
      workerNode: "Worker 实例",
      workerNodeDetail: "受限身份与心跳状态",
      ledgerNode: "证据账本",
      ledgerNodeDetail: "Run、工具、评估与审计回读",
      codexDetail: "查看 Codex 实例与双向链路",
    },
  });

  const capabilitySummary = (connector: (typeof runtimeConnectors)[number]) => {
    const manifest = connector.capability_manifest || {};
    const capabilities = typeof manifest.capabilities === "object" && manifest.capabilities !== null
      ? manifest.capabilities as Record<string, unknown>
      : {};
    return [
      { label: copy.observation, value: connector.observation_level || String(manifest.observation_level || "—") },
      { label: copy.riskFloor, value: connector.risk_floor || String(manifest.risk_floor || "—") },
      { label: copy.externalWrite, value: String(capabilities.external_write || "—") },
      { label: copy.confirm, value: String(capabilities.confirmation || "—") },
      { label: copy.trustPolicy, value: String(capabilities.trust_policy || connector.trust_status || "—") },
      { label: copy.commercial, value: connector.commercial_readiness || String(manifest.commercial_readiness || "—") },
    ];
  };

  const trustImpact = (status?: string) => {
    if (status === "blocked") return copy.blockedImpact;
    if (status === "review_required") return copy.reviewImpact;
    return copy.trustedImpact;
  };
  const blockedConnectors = runtimeConnectors.filter(connector => connector.trust_status === "blocked").length;
  const reviewConnectors = runtimeConnectors.filter(connector => connector.trust_status === "review_required").length;
  const readyConnectors = runtimeConnectors.filter(connector => ["ready", "live", "available"].includes(connector.status)).length;
  const workerLaneCount = workerFleet?.lanes.length || 0;
  const adapterCount = adapterReadiness ? Object.keys(adapterReadiness.adapters).length : 0;
  const readyAdapterCount = adapterReadiness?.summary.ready_adapters?.length || 0;
  const topologyNodes = [
    {
      id: "control-plane",
      label: copy.controlPlane,
      value: "AgentOps MIS",
      detail: copy.controlPlaneDetail,
      status: error ? "unavailable" : "ready",
      icon: <ShieldCheck size={15} />,
    },
    {
      id: "trust-registry",
      label: copy.trustNode,
      value: blockedConnectors
        ? `${blockedConnectors} ${copy.blocked}`
        : reviewConnectors
          ? `${reviewConnectors} ${copy.reviewRequired}`
          : copy.trusted,
      detail: copy.trustNodeDetail,
      status: blockedConnectors ? "blocked" : reviewConnectors ? "attention" : "pass",
      icon: <ShieldCheck size={15} />,
    },
    {
      id: "runtime-adapters",
      label: copy.adapterNode,
      value: adapterCount ? `${readyAdapterCount}/${adapterCount}` : `${readyConnectors}/${runtimeConnectors.length || 0}`,
      detail: copy.adapterNodeDetail,
      status: adapterCount
        ? (readyAdapterCount ? "ready" : adapterReadiness?.status || "unavailable")
        : readyConnectors ? "ready" : "unavailable",
      icon: <Plug size={15} />,
    },
    {
      id: "worker-fleet",
      label: copy.workerNode,
      value: String(workerLaneCount),
      detail: copy.workerNodeDetail,
      status: workerLaneCount ? "ready" : "unknown",
      icon: <Bot size={15} />,
      to: "/workspace/workers",
    },
    {
      id: "evidence-ledger",
      label: copy.ledgerNode,
      value: String(connectorAuditLogs.length),
      detail: copy.ledgerNodeDetail,
      status: connectorAuditLogs.length ? "ready" : "planned",
      icon: <Database size={15} />,
      to: "/admin/runs",
    },
  ];

  const changeTrust = async (connectorId: string, trustStatus: "trusted" | "review_required" | "blocked") => {
    setTrustAction(`${connectorId}:${trustStatus}`);
    setTrustMessage(null);
    try {
      await updateRuntimeConnectorTrust(connectorId, {
        trust_status: trustStatus,
        trust_note: locale === "zh"
          ? `管理员将 ${connectorId} 标记为 ${trustStatus}。`
          : `Operator marked ${connectorId} as ${trustStatus}.`,
      });
      setTrustMessage(`${copy.trustUpdated}: ${connectorId} -> ${trustStatus}`);
      await refresh();
    } catch (err) {
      setTrustMessage(err instanceof Error ? err.message : String(err));
    } finally {
      setTrustAction(null);
    }
  };

  return (
    <div className="space-y-6 w-full">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold" style={{ color: "var(--mis-text)" }}>
            {copy.title}
          </h1>
          <p className="text-xs mt-0.5" style={{ color: "var(--mis-dim)" }}>
            {copy.subtitle}
          </p>
          {loading && <p className="text-xs mt-2" style={{ color: "var(--mis-muted)" }}>{copy.loading}</p>}
          {error && <p className="text-xs mt-2" style={{ color: "#F87171" }}>{copy.backendUnavailable}: {error}</p>}
        </div>
        <button onClick={refresh} className="flex items-center gap-2 text-xs px-3 py-1.5 rounded" style={{ background: "var(--mis-surface)", border: "1px solid var(--mis-border)", color: "var(--mis-dim)" }}>
          <Radio size={12} style={{ color: "var(--mis-success)" }} />
          {copy.refresh}
        </button>
      </div>

      {/* Status summary */}
      <div className="flex gap-4 flex-wrap">
        {[
          { label: copy.ready, count: runtimeConnectors.filter(c => c.status === "ready").length, status: "ready" },
          { label: copy.live, count: runtimeConnectors.filter(c => c.status === "live").length, status: "live" },
          { label: copy.dryRun, count: runtimeConnectors.filter(c => c.status === "dry_run").length, status: "dry_run" },
          { label: copy.unavailable, count: runtimeConnectors.filter(c => c.status === "unavailable").length, status: "unavailable" },
        ].map(({ label, count, status }) => (
          <div
            key={label}
            className="flex items-center gap-2 px-3 py-2 rounded-lg text-xs"
            style={{ background: "var(--mis-surface)", border: "1px solid var(--mis-border)" }}
          >
            <span style={{ color: "var(--mis-dim)" }}>{label}:</span>
            <span className="font-semibold" style={{ color: "var(--mis-text)" }}>{count}</span>
            <StatusBadge status={status} />
          </div>
        ))}
      </div>

      <ConnectorTopology
        label={copy.topology}
        description={copy.topologySummary}
        nodes={topologyNodes}
      />

      {commercialConfigStatus && (
        <div
          data-testid="commercial-config-status-panel"
          className="rounded-xl p-4"
          style={{ background: "var(--mis-surface)", border: "1px solid var(--mis-border)" }}
        >
          <div className="flex flex-col lg:flex-row lg:items-start lg:justify-between gap-4">
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <div className="text-xs font-semibold" style={{ color: "var(--mis-text)" }}>{copy.commercialConfig}</div>
                <StatusBadge status={commercialConfigStatus.status} label={commercialConfigStatus.status} />
                {!commercialConfigStatus.configured && <StatusBadge status="attention" label={copy.notConfigured} />}
              </div>
              <p className="text-[11px] mt-1 max-w-3xl" style={{ color: "var(--mis-muted)" }}>{copy.commercialConfigSummary}</p>
            </div>
            <div className="flex flex-wrap gap-1.5">
              <StatusBadge status={commercialConfigStatus.safety.read_only ? "pass" : "blocked"} label="read_only" />
              <StatusBadge status={commercialConfigStatus.safety.billing_call_performed ? "blocked" : "pass"} label={copy.billingCalls} />
              <StatusBadge status={commercialConfigStatus.safety.cleanup_execution_performed ? "blocked" : "pass"} label={copy.cleanupExecution} />
              <StatusBadge status={commercialConfigStatus.safety.raw_config_omitted ? "pass" : "attention"} label={copy.rawConfigOmitted} />
              <StatusBadge status={commercialConfigStatus.safety.token_omitted ? "pass" : "attention"} label={copy.tokenOmitted} />
            </div>
          </div>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-2 mt-4">
            {[
              { label: copy.edition, value: commercialConfigStatus.entitlements.edition || "—" },
              { label: copy.billingProvider, value: commercialConfigStatus.entitlements.billing_provider || "—" },
              { label: copy.approvalGate, value: commercialConfigStatus.retention.cleanup_approval_required ? "required" : "missing" },
              { label: copy.legalHoldGate, value: commercialConfigStatus.retention.legal_hold_required_before_cleanup ? "required" : "missing" },
            ].map(item => (
              <div key={item.label} className="rounded px-3 py-2 min-w-0" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
                <div className="text-[9px]" style={{ color: "var(--mis-muted)" }}>{item.label}</div>
                <div className="text-[11px] font-semibold truncate" style={{ color: "var(--mis-text)" }}>{item.value}</div>
              </div>
            ))}
          </div>
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-3 mt-3">
            <div className="rounded p-3" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
              <div className="text-[10px] font-semibold mb-2" style={{ color: "var(--mis-text)" }}>{copy.enabledCapabilities}</div>
              <div className="flex flex-wrap gap-1.5">
                {commercialConfigStatus.entitlements.enabled_capabilities.map(capability => (
                  <span key={capability} className="text-[10px] px-2 py-1 rounded" style={{ background: "rgba(45,212,191,0.10)", color: "var(--mis-success)", border: "1px solid rgba(45,212,191,0.18)" }}>
                    {capability}
                  </span>
                ))}
              </div>
            </div>
            <div className="rounded p-3" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
              <div className="text-[10px] font-semibold mb-2" style={{ color: "var(--mis-text)" }}>{copy.disabledCapabilities}</div>
              <div className="flex flex-wrap gap-1.5">
                {commercialConfigStatus.entitlements.disabled_capabilities.map(capability => (
                  <span key={capability} className="text-[10px] px-2 py-1 rounded" style={{ background: "rgba(148,163,184,0.08)", color: "var(--mis-muted)", border: "1px solid var(--mis-border)" }}>
                    {capability}
                  </span>
                ))}
              </div>
            </div>
          </div>
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-2 mt-3 text-[10px]" style={{ color: "var(--mis-muted)" }}>
            <div>{copy.source}: {String(commercialConfigStatus.sources.entitlements?.source || "—")} · {String(commercialConfigStatus.sources.entitlements?.path || "—")}</div>
            <div>{copy.source}: {String(commercialConfigStatus.sources.retention?.source || "—")} · {String(commercialConfigStatus.sources.retention?.path || "—")}</div>
          </div>
          <div
            data-testid="commercial-config-operator-action"
            className="rounded p-3 mt-3"
            style={{ background: "rgba(34,211,238,0.06)", border: "1px solid rgba(34,211,238,0.16)" }}
          >
            <div className="flex flex-col lg:flex-row lg:items-start lg:justify-between gap-3">
              <div className="min-w-0">
                <div className="text-[10px] font-semibold" style={{ color: "var(--mis-text)" }}>{copy.operatorNextAction}</div>
                <p className="mt-1 text-[11px] leading-relaxed" style={{ color: "var(--mis-dim)" }}>{copy.operatorNextActionSummary}</p>
              </div>
              <StatusBadge status="pass" label="read_only_cli" />
            </div>
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-2 mt-3 text-[10px]">
              {[
                { label: copy.verifyFromCli, value: "agentops commercial config-status" },
                { label: copy.evidenceDoc, value: "docs/COMMERCIAL_CONFIG_STATUS_UI_ACCEPTANCE.md" },
                { label: copy.productionBoundary, value: copy.noBillingCleanup },
              ].map(item => (
                <div key={item.label} className="rounded p-2 min-w-0" style={{ background: "rgba(2,6,23,0.28)", border: "1px solid rgba(148,163,184,0.10)" }}>
                  <div style={{ color: "var(--mis-muted)" }}>{item.label}</div>
                  <div className="mt-0.5 font-medium break-words" style={{ color: "var(--mis-text)" }}>{item.value}</div>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* Connector cards grid */}
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        {runtimeConnectors.map(connector => (
          <div key={connector.connector_id} className="rounded-xl overflow-hidden" style={{ border: "1px solid var(--mis-border)" }}>
            <ConnectorCard connector={connector} />
            <div className="px-5 pb-5 -mt-1" style={{ background: "var(--mis-surface)" }}>
              <div className="rounded-lg p-3" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <div className="text-[11px] font-semibold" style={{ color: "var(--mis-text)" }}>{copy.trustRegistry}</div>
                    <div className="text-[10px] mt-0.5" style={{ color: "var(--mis-muted)" }}>{copy.trustSummary}</div>
                    {connector.trust_note && (
                      <div className="text-[10px] mt-1 truncate" style={{ color: "var(--mis-dim)" }}>{connector.trust_note}</div>
                    )}
                  </div>
                  <StatusBadge status={connector.trust_status || "trusted"} />
                </div>
                <div className="flex flex-wrap gap-2 mt-3">
                  {[
                    { status: "trusted" as const, label: copy.trusted },
                    { status: "review_required" as const, label: copy.reviewRequired },
                    { status: "blocked" as const, label: copy.blocked },
                  ].map(item => (
                    <button
                      key={item.status}
                      onClick={() => changeTrust(connector.connector_id, item.status)}
                      disabled={Boolean(trustAction)}
                      className="text-[11px] px-3 py-1.5 rounded disabled:opacity-50"
                      style={{
                        background: connector.trust_status === item.status ? "rgba(34,211,238,0.14)" : "var(--mis-bg)",
                        color: item.status === "blocked" ? "#F87171" : connector.trust_status === item.status ? "var(--mis-cyan)" : "var(--mis-dim)",
                        border: "1px solid var(--mis-border)",
                      }}
                    >
                      {trustAction === `${connector.connector_id}:${item.status}` ? copy.updatingTrust : item.label}
                    </button>
                  ))}
                </div>
                <div
                  data-testid="runtime-connector-trust-impact"
                  className="rounded px-3 py-2 mt-3"
                  style={{
                    background: connector.trust_status === "blocked" ? "rgba(248,113,113,0.08)" : connector.trust_status === "review_required" ? "rgba(251,191,36,0.08)" : "var(--mis-bg)",
                    border: connector.trust_status === "blocked" ? "1px solid rgba(248,113,113,0.18)" : connector.trust_status === "review_required" ? "1px solid rgba(251,191,36,0.18)" : "1px solid var(--mis-border)",
                  }}
                >
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="text-[9px] uppercase" style={{ color: "var(--mis-muted)" }}>{copy.trustImpact}</span>
                    <StatusBadge status={connector.trust_status === "blocked" ? "blocked" : connector.trust_status === "review_required" ? "attention" : "pass"} label={`${copy.liveWorkerGate}: ${connector.trust_status || "trusted"}`} />
                    <StatusBadge status="pass" label={`${copy.operatorReadback}: ${connector.connector_id}`} />
                    <StatusBadge status={connectorAuditLogs.some(log => log.entity_id === connector.connector_id) ? "ready" : "planned"} label={`${copy.auditRefs}: ${connectorAuditLogs.filter(log => log.entity_id === connector.connector_id).length}`} />
                  </div>
                  <div className="text-[10px] mt-2" style={{ color: "var(--mis-muted)" }}>
                    {trustImpact(connector.trust_status)}
                  </div>
                </div>
              </div>
              <div className="rounded-lg p-3 mt-3" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
                <div className="flex items-center justify-between gap-3">
                  <div className="text-[11px] font-semibold" style={{ color: "var(--mis-text)" }}>{copy.capabilityManifest}</div>
                  {connector.capability_policy_hash && (
                    <div className="text-[10px] truncate max-w-[180px]" style={{ color: "var(--mis-muted)" }}>
                      {copy.policyHash}: {connector.capability_policy_hash.slice(0, 12)}
                    </div>
                  )}
                </div>
                <div className="grid grid-cols-2 gap-2 mt-3">
                  {capabilitySummary(connector).map(item => (
                    <div key={item.label} className="min-w-0 rounded px-2 py-1.5" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
                      <div className="text-[9px]" style={{ color: "var(--mis-muted)" }}>{item.label}</div>
                      <div className="text-[10px] font-semibold truncate" title={item.value} style={{ color: "var(--mis-text)" }}>{item.value}</div>
                    </div>
                  ))}
                </div>
              </div>
              {(`${connector.connector_id} ${connector.provider}`.toLowerCase().includes("codex")) && (
                <Link
                  to="/admin/connectors/codex"
                  className="mt-3 flex min-h-9 items-center justify-between rounded px-3 text-[11px] font-medium"
                  style={{ color: "var(--mis-cyan)", background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}
                >
                  <span className="inline-flex items-center gap-2"><Activity size={13} />{copy.codexDetail}</span>
                  <span aria-hidden="true">→</span>
                </Link>
              )}
            </div>
          </div>
        ))}
      </div>

      {trustMessage && (
        <div className="text-xs rounded px-3 py-2" style={{ background: "var(--mis-surface)", border: "1px solid var(--mis-border)", color: trustMessage.includes("Error") || trustMessage.includes("error") ? "#F87171" : "var(--mis-success)" }}>
          {trustMessage}
        </div>
      )}

      {/* Integration architecture note */}
      <div
        className="rounded-xl p-4 text-xs"
        style={{ background: "var(--mis-surface)", border: "1px solid var(--mis-border)" }}
      >
        <div className="font-semibold mb-2" style={{ color: "var(--mis-text)" }}>
          <Plug size={13} className="inline mr-1.5" style={{ color: "var(--mis-primary)" }} />
          {copy.plannedConnectors}
        </div>
        <div className="flex gap-3 flex-wrap">
          {["OpenAI-compatible APIs", "Claude Direct", "OpenHands", "CrewAI", "LangGraph"].map(name => (
            <span
              key={name}
              className="px-2 py-1 rounded"
              style={{ background: "var(--mis-surface2)", color: "var(--mis-muted)" }}
            >
              {name}
            </span>
          ))}
        </div>
      </div>

      {/* Recent runtime events */}
      {connectorAuditLogs.length > 0 && (
        <div
          className="rounded-xl p-4"
          style={{ background: "var(--mis-surface)", border: "1px solid var(--mis-border)" }}
        >
          <div className="text-xs font-semibold mb-3" style={{ color: "var(--mis-text)" }}>{copy.recentRuntimeEvents}</div>
          <div className="space-y-2">
            {connectorAuditLogs.map(log => (
              <div key={log.audit_id} className="flex items-center justify-between py-2" style={{ borderBottom: "1px solid var(--mis-border)" }}>
                <div>
                  <span className="text-xs font-medium" style={{ color: "var(--mis-text)" }}>{log.action}</span>
                  <span className="text-[11px] ml-2" style={{ color: "var(--mis-muted)" }}>{log.entity_id}</span>
                </div>
                <span className="text-[11px]" style={{ color: "var(--mis-muted)" }}>
                  {new Date(log.created_at).toLocaleString(locale === "zh" ? "zh-CN" : "en-US")}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
