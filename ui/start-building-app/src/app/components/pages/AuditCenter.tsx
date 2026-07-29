import { useState } from "react";
import { Bot, ClipboardList, Monitor, RefreshCw, User } from "lucide-react";
import { loadAudit, useLiveData } from "../../data/liveApi";
import { pick, usePreferences } from "../../context/PreferencesContext";

type AuditEntry = Awaited<ReturnType<typeof loadAudit>>[number];
type ActorType = AuditEntry["actor_type"];
type ActorFilter = "all" | ActorType;

const ACTOR_TYPES: ActorType[] = ["user", "agent", "system"];
const ACTOR_COLOR: Record<ActorType, string> = {
  user: "var(--mis-purple)",
  agent: "var(--mis-cyan)",
  system: "var(--mis-muted)",
};

function ActorIcon({ type }: { type: ActorType }) {
  const color = ACTOR_COLOR[type] || "var(--mis-muted)";
  const Icon = type === "user" ? User : type === "agent" ? Bot : Monitor;
  return (
    <span
      className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full"
      style={{ background: `${color}18`, color }}
    >
      <Icon size={11} />
    </span>
  );
}

function formatTimestamp(value: string, locale: "en" | "zh") {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value || "-" : parsed.toLocaleString(locale === "zh" ? "zh-CN" : "en-US");
}

export function AuditCenter() {
  const { locale } = usePreferences();
  const [actorFilter, setActorFilter] = useState<ActorFilter>("all");
  const { data, loading, error, refresh } = useLiveData(loadAudit, []);
  const auditLogs = data || [];
  const filtered = actorFilter === "all" ? auditLogs : auditLogs.filter((log) => log.actor_type === actorFilter);
  const copy = pick(locale, {
    en: {
      title: "Audit Center",
      summary: `${auditLogs.length} events from the live MIS ledger`,
      boundary: "Read-only view of bounded audit records. This page does not infer chain verification or expose raw prompts, responses, or credentials.",
      liveLedger: "Live ledger",
      loading: "Loading live audit ledger...",
      error: "Audit API error",
      refresh: "Refresh",
      empty: "No audit events match this filter.",
      all: "All",
      user: "User",
      agent: "Agent",
      system: "System",
      headers: ["Actor", "Action", "Entity", "Entity ID", "Timestamp"],
    },
    zh: {
      title: "审计中心",
      summary: `实时 MIS 账本中的 ${auditLogs.length} 条事件`,
      boundary: "这里只读展示经过边界化处理的审计记录，不推断链完整性，也不暴露原始提示词、响应或凭据。",
      liveLedger: "实时账本",
      loading: "正在加载实时审计账本...",
      error: "审计 API 错误",
      refresh: "刷新",
      empty: "当前筛选条件下没有审计事件。",
      all: "全部",
      user: "用户",
      agent: "代理",
      system: "系统",
      headers: ["执行者", "动作", "实体", "实体 ID", "时间"],
    },
  });
  const actorLabels: Record<ActorFilter, string> = {
    all: copy.all,
    user: copy.user,
    agent: copy.agent,
    system: copy.system,
  };

  return (
    <div className="w-full space-y-5" data-testid="live-audit-center">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold" style={{ color: "var(--mis-text)" }}>{copy.title}</h1>
          <p className="mt-0.5 text-xs" style={{ color: "var(--mis-dim)" }}>{copy.summary}</p>
          <p className="mt-1 max-w-3xl text-[11px] leading-relaxed" style={{ color: "var(--mis-muted)" }}>{copy.boundary}</p>
          {loading && <p className="mt-2 text-xs" style={{ color: "var(--mis-muted)" }}>{copy.loading}</p>}
          {error && <p className="mt-2 text-xs" role="alert" style={{ color: "#F87171" }}>{copy.error}: {error}</p>}
        </div>
        <div className="flex items-center gap-2">
          <div
            className="flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-[11px]"
            style={{ background: "rgba(42,157,143,0.1)", color: "var(--mis-success)", border: "1px solid rgba(42,157,143,0.2)" }}
          >
            <ClipboardList size={12} />
            {copy.liveLedger}
          </div>
          <button
            type="button"
            onClick={() => void refresh()}
            disabled={loading}
            className="flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-[11px] disabled:opacity-50"
            style={{ background: "var(--mis-surface2)", color: "var(--mis-dim)", border: "1px solid var(--mis-border)" }}
          >
            <RefreshCw size={12} className={loading ? "animate-spin" : ""} />
            {copy.refresh}
          </button>
        </div>
      </div>

      <div className="flex flex-wrap gap-2" data-testid="audit-actor-filter">
        {(["all", ...ACTOR_TYPES] as ActorFilter[]).map((filter) => {
          const color = filter === "all" ? "var(--mis-dim)" : ACTOR_COLOR[filter];
          const count = filter === "all" ? auditLogs.length : auditLogs.filter((log) => log.actor_type === filter).length;
          return (
            <button
              type="button"
              key={filter}
              onClick={() => setActorFilter(filter)}
              className="rounded-lg px-3 py-1.5 text-[11px] transition-all"
              style={{
                background: actorFilter === filter ? `${color}18` : "var(--mis-surface)",
                color: actorFilter === filter ? color : "var(--mis-dim)",
                border: `1px solid ${actorFilter === filter ? `${color}30` : "var(--mis-border)"}`,
              }}
            >
              {actorLabels[filter]} <span className="opacity-60">({count})</span>
            </button>
          );
        })}
      </div>

      <div className="overflow-x-auto rounded-xl" style={{ background: "var(--mis-surface)", border: "1px solid var(--mis-border)" }}>
        <table className="w-full min-w-[760px] text-xs">
          <thead>
            <tr style={{ background: "var(--mis-surface2)", color: "var(--mis-muted)" }}>
              {copy.headers.map((header) => <th key={header} className="px-4 py-3 text-left font-medium">{header}</th>)}
            </tr>
          </thead>
          <tbody>
            {filtered.map((log, index) => (
              <tr key={log.audit_id} style={{ color: "var(--mis-dim)", borderTop: index > 0 ? "1px solid var(--mis-border)" : "none" }}>
                <td className="px-4 py-3">
                  <div className="flex items-center gap-2">
                    <ActorIcon type={log.actor_type} />
                    <div>
                      <div className="text-[11px] font-medium" style={{ color: "var(--mis-text)" }}>{log.actor_id}</div>
                      <div className="text-[10px]" style={{ color: ACTOR_COLOR[log.actor_type] }}>{actorLabels[log.actor_type]}</div>
                    </div>
                  </div>
                </td>
                <td className="px-4 py-3"><span className="font-medium" style={{ color: "var(--mis-text)" }}>{log.action}</span></td>
                <td className="px-4 py-3">{log.entity_type}</td>
                <td className="px-4 py-3 font-mono text-[11px]">{log.entity_id}</td>
                <td className="px-4 py-3 text-[11px]" style={{ color: "var(--mis-muted)" }}>{formatTimestamp(log.created_at, locale)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {!loading && filtered.length === 0 && (
          <div className="px-4 py-12 text-center text-sm" style={{ color: "var(--mis-muted)" }}>{copy.empty}</div>
        )}
      </div>
    </div>
  );
}
