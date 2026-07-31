import { useState } from "react";
import { Brain, CheckCircle, RefreshCw, XCircle } from "lucide-react";
import { decideMemory, loadMemories, useLiveData } from "../../data/liveApi";
import { pick, usePreferences } from "../../context/PreferencesContext";
import { StatusBadge } from "../shared/StatusBadge";

type MemoryEntry = Awaited<ReturnType<typeof loadMemories>>[number];
type MemoryScope = MemoryEntry["scope"];
type MemoryStatus = MemoryEntry["review_status"];
type FilterScope = "all" | MemoryScope;
type FilterStatus = "all" | MemoryStatus;

const SCOPES: MemoryScope[] = ["task", "project", "org"];
const STATUSES: MemoryStatus[] = ["candidate", "approved", "rejected", "stale", "superseded"];
const SCOPE_COLORS: Record<MemoryScope, string> = {
  task: "var(--mis-primary)",
  project: "var(--mis-cyan)",
  org: "var(--mis-purple)",
};

const TYPE_LABELS: Record<string, { en: string; zh: string }> = {
  policy: { en: "Policy", zh: "策略" },
  sop: { en: "SOP", zh: "标准流程" },
  decision: { en: "Decision", zh: "决策" },
  commitment: { en: "Commitment", zh: "承诺" },
  risk: { en: "Risk", zh: "风险" },
  failure_case: { en: "Failure Case", zh: "失败案例" },
  project_context: { en: "Project Context", zh: "项目上下文" },
  customer_preference: { en: "Customer Preference", zh: "客户偏好" },
  agent_lesson: { en: "Agent Lesson", zh: "代理经验" },
  artifact_summary: { en: "Artifact Summary", zh: "产物摘要" },
};

function formatDate(value: string, locale: "en" | "zh") {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value || "-" : parsed.toLocaleDateString(locale === "zh" ? "zh-CN" : "en-US");
}

export function MemoryLibrary() {
  const { locale } = usePreferences();
  const [scopeFilter, setScopeFilter] = useState<FilterScope>("all");
  const [statusFilter, setStatusFilter] = useState<FilterStatus>("all");
  const [busyId, setBusyId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const { data, setData, loading, error, refresh } = useLiveData(loadMemories, []);
  const memories = data || [];
  const copy = pick(locale, {
    en: {
      title: "Memory Library",
      summary: `${memories.length} total · ${memories.filter((memory) => memory.review_status === "candidate").length} candidates pending review`,
      boundary: "Only reviewed memory enters the host authority store. Approve or reject bounded candidates without exposing raw conversations or transcripts.",
      loading: "Loading live memory ledger...",
      error: "Memory API error",
      refresh: "Refresh",
      noMatch: "No memories match this filter.",
      approve: "Approve",
      reject: "Reject",
      confidence: "Confidence",
      agent: "Agent",
      task: "Task",
      source: "Source",
      all: "All",
      scope: { task: "Task", project: "Project", org: "Organization" },
      status: { candidate: "Candidate", approved: "Approved", rejected: "Rejected", stale: "Stale", superseded: "Superseded" },
    },
    zh: {
      title: "记忆库",
      summary: `共 ${memories.length} 条 · ${memories.filter((memory) => memory.review_status === "candidate").length} 条候选记忆待审核`,
      boundary: "只有经过审核的记忆才能进入主机权威存储。这里审批的是边界化候选内容，不展示原始对话或完整记录。",
      loading: "正在加载实时记忆账本...",
      error: "记忆 API 错误",
      refresh: "刷新",
      noMatch: "当前筛选条件下没有记忆。",
      approve: "批准",
      reject: "拒绝",
      confidence: "置信度",
      agent: "代理",
      task: "任务",
      source: "来源",
      all: "全部",
      scope: { task: "任务", project: "项目", org: "组织" },
      status: { candidate: "候选", approved: "已批准", rejected: "已拒绝", stale: "已过期", superseded: "已替代" },
    },
  });

  const filtered = memories.filter((memory) => {
    if (scopeFilter !== "all" && memory.scope !== scopeFilter) return false;
    if (statusFilter !== "all" && memory.review_status !== statusFilter) return false;
    return true;
  });

  const handleDecision = async (memoryId: string, decision: "approve" | "reject") => {
    setBusyId(memoryId);
    setActionError(null);
    try {
      const updated = await decideMemory(memoryId, decision);
      setData((current) => current?.map((memory) => memory.memory_id === updated.memory_id ? updated : memory) || current);
    } catch (nextError) {
      setActionError(nextError instanceof Error ? nextError.message : String(nextError));
    } finally {
      setBusyId(null);
    }
  };

  const handleRefresh = () => {
    setActionError(null);
    void refresh();
  };

  const typeLabel = (type: string) => TYPE_LABELS[type]?.[locale] || type;
  const scopeLabel = (scope: MemoryScope) => copy.scope[scope] || scope;
  const statusLabel = (status: MemoryStatus) => copy.status[status] || status;

  return (
    <div className="w-full space-y-5" data-testid="live-memory-library">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold" style={{ color: "var(--mis-text)" }}>{copy.title}</h1>
          <p className="mt-0.5 text-xs" style={{ color: "var(--mis-dim)" }}>{copy.summary}</p>
          <p className="mt-1 max-w-3xl text-[11px] leading-relaxed" style={{ color: "var(--mis-muted)" }}>{copy.boundary}</p>
          {loading && <p className="mt-2 text-xs" style={{ color: "var(--mis-muted)" }}>{copy.loading}</p>}
          {(error || actionError) && <p className="mt-2 text-xs" role="alert" style={{ color: "#F87171" }}>{copy.error}: {actionError || error}</p>}
        </div>
        <button
          type="button"
          onClick={handleRefresh}
          disabled={loading || busyId !== null}
          className="flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-[11px] disabled:opacity-50"
          style={{ background: "var(--mis-surface2)", color: "var(--mis-dim)", border: "1px solid var(--mis-border)" }}
        >
          <RefreshCw size={12} className={loading ? "animate-spin" : ""} />
          {copy.refresh}
        </button>
      </div>

      <div className="flex flex-wrap gap-4" data-testid="memory-review-filters">
        <div className="flex flex-wrap gap-1">
          {(["all", ...SCOPES] as FilterScope[]).map((scope) => (
            <button
              type="button"
              key={scope}
              onClick={() => setScopeFilter(scope)}
              className="rounded px-2.5 py-1 text-[11px] transition-all"
              style={{
                background: scopeFilter === scope ? "rgba(34,211,238,0.12)" : "var(--mis-surface)",
                color: scopeFilter === scope ? "var(--mis-cyan)" : "var(--mis-dim)",
                border: `1px solid ${scopeFilter === scope ? "rgba(34,211,238,0.25)" : "var(--mis-border)"}`,
              }}
            >
              {scope === "all" ? copy.all : scopeLabel(scope)}
            </button>
          ))}
        </div>
        <div className="flex flex-wrap gap-1">
          {(["all", ...STATUSES] as FilterStatus[]).map((status) => (
            <button
              type="button"
              key={status}
              onClick={() => setStatusFilter(status)}
              className="rounded px-2.5 py-1 text-[11px] transition-all"
              style={{
                background: statusFilter === status ? "rgba(122,90,248,0.12)" : "var(--mis-surface)",
                color: statusFilter === status ? "var(--mis-purple)" : "var(--mis-dim)",
                border: `1px solid ${statusFilter === status ? "rgba(122,90,248,0.25)" : "var(--mis-border)"}`,
              }}
            >
              {status === "all" ? copy.all : statusLabel(status)}
            </button>
          ))}
        </div>
      </div>

      <div className="space-y-2">
        {!loading && filtered.length === 0 && (
          <div className="py-12 text-center" style={{ color: "var(--mis-muted)" }}>
            <Brain size={24} className="mx-auto mb-2 opacity-40" />
            <p className="text-sm">{copy.noMatch}</p>
          </div>
        )}
        {filtered.map((memory) => {
          const scopeColor = SCOPE_COLORS[memory.scope] || "var(--mis-muted)";
          const isBusy = busyId === memory.memory_id;
          return (
            <div key={memory.memory_id} className="rounded-xl p-4" style={{ background: "var(--mis-surface)", border: "1px solid var(--mis-border)" }}>
              <div className="flex flex-col gap-3 md:flex-row md:items-start">
                <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg" style={{ background: `${scopeColor}18`, color: scopeColor }}>
                  <Brain size={13} />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="mb-1.5 flex flex-wrap items-center gap-2">
                    <span className="rounded px-1.5 py-0.5 text-[10px] font-medium" style={{ background: `${scopeColor}15`, color: scopeColor }}>{scopeLabel(memory.scope)}</span>
                    <span className="rounded px-1.5 py-0.5 text-[10px]" style={{ background: "var(--mis-surface2)", color: "var(--mis-muted)" }}>{typeLabel(memory.memory_type)}</span>
                    <StatusBadge status={memory.review_status} label={statusLabel(memory.review_status)} />
                    <span className="text-[10px]" style={{ color: "var(--mis-muted)" }}>{copy.confidence}: {Math.round(memory.confidence * 100)}%</span>
                  </div>
                  <p className="mb-2 text-xs leading-relaxed" style={{ color: "var(--mis-text)" }}>{memory.canonical_text}</p>
                  <div className="flex flex-wrap gap-3 text-[10px]" style={{ color: "var(--mis-muted)" }}>
                    {memory.agent_id && <span>{copy.agent}: <span className="font-mono" style={{ color: "var(--mis-dim)" }}>{memory.agent_id}</span></span>}
                    {memory.task_id && <span>{copy.task}: <span className="font-mono" style={{ color: "var(--mis-dim)" }}>{memory.task_id}</span></span>}
                    <span>{copy.source}: <span style={{ color: "var(--mis-dim)" }}>{memory.source_type || "-"}</span></span>
                    <span>{formatDate(memory.created_at, locale)}</span>
                  </div>
                </div>

                {memory.review_status === "candidate" && (
                  <div className="flex shrink-0 gap-1.5" data-testid={`memory-review-actions-${memory.memory_id}`}>
                    <button
                      type="button"
                      onClick={() => void handleDecision(memory.memory_id, "approve")}
                      disabled={busyId !== null}
                      className="flex items-center gap-1 rounded px-2 py-1 text-[11px] disabled:opacity-50"
                      style={{ background: "rgba(42,157,143,0.15)", color: "var(--mis-success)" }}
                    >
                      <CheckCircle size={11} /> {isBusy ? "..." : copy.approve}
                    </button>
                    <button
                      type="button"
                      onClick={() => void handleDecision(memory.memory_id, "reject")}
                      disabled={busyId !== null}
                      className="flex items-center gap-1 rounded px-2 py-1 text-[11px] disabled:opacity-50"
                      style={{ background: "rgba(248,113,113,0.12)", color: "#F87171" }}
                    >
                      <XCircle size={11} /> {isBusy ? "..." : copy.reject}
                    </button>
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
