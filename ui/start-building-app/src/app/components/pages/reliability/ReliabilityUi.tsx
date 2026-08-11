import type { ReactNode } from "react";
import { AlertTriangle, ArrowRight, Inbox, RefreshCw, ShieldCheck } from "lucide-react";
import { Link, NavLink } from "react-router";
import { StatusBadge } from "../../shared/StatusBadge";
import type { ReliabilityJson } from "../../../data/reliabilityApi";

const featureLinks = [
  { label: "Overview", to: "/workspace/reliability", end: true },
  { label: "Agents", to: "/workspace/reliability/agents" },
  { label: "Scenario Suites", to: "/workspace/reliability/scenario-suites" },
  { label: "Campaigns", to: "/workspace/reliability/campaigns" },
  { label: "Failures", to: "/workspace/reliability/failures" },
  { label: "Regression Suite", to: "/workspace/reliability/regressions" },
  { label: "Release Gates", to: "/workspace/reliability/release-gates" },
];

export function ReliabilityPage({
  title,
  description,
  actions,
  children,
}: {
  title: string;
  description: string;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="mx-auto w-full max-w-[1680px] space-y-4">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="break-all text-lg font-semibold" style={{ color: "var(--mis-text)" }}>{title}</h1>
          <p className="mt-1 max-w-4xl text-xs leading-relaxed" style={{ color: "var(--mis-dim)" }}>{description}</p>
        </div>
        {actions ? <div className="flex items-center gap-2">{actions}</div> : null}
      </header>

      <nav
        aria-label="Reliability Lab"
        className="flex gap-1 overflow-x-auto rounded-lg p-1"
        style={{ background: "var(--mis-surface)", border: "1px solid var(--mis-border)" }}
      >
        {featureLinks.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.end}
            className="shrink-0 rounded px-3 py-1.5 text-[11px] font-medium transition-colors"
            style={({ isActive }) => ({
              color: isActive ? "var(--mis-cyan)" : "var(--mis-dim)",
              background: isActive ? "color-mix(in srgb, var(--mis-cyan) 10%, transparent)" : "transparent",
            })}
          >
            {item.label}
          </NavLink>
        ))}
      </nav>

      {children}

      <div className="flex items-center gap-2 pb-1 text-[10px]" style={{ color: "var(--mis-muted)" }}>
        <ShieldCheck size={12} style={{ color: "var(--mis-success)" }} />
        Read-only evidence from the AgentOps MIS authority ledger. Hidden prompts and credentials are omitted.
      </div>
    </div>
  );
}

export function ReliabilityRefreshButton({ refresh, label = "Refresh" }: { refresh: () => Promise<void>; label?: string }) {
  return (
    <button
      type="button"
      onClick={() => void refresh()}
      className="inline-flex items-center gap-1.5 rounded px-3 py-1.5 text-xs transition-opacity hover:opacity-80"
      style={{ background: "rgba(34,211,238,0.10)", color: "var(--mis-cyan)", border: "1px solid rgba(34,211,238,0.22)" }}
    >
      <RefreshCw size={13} />
      {label}
    </button>
  );
}

export function ReliabilityLoadingState({ label = "Loading Reliability evidence…" }: { label?: string }) {
  return (
    <div
      data-testid="reliability-loading-state"
      className="rounded-lg px-4 py-10 text-center text-xs"
      style={{ background: "var(--mis-surface)", border: "1px solid var(--mis-border)", color: "var(--mis-muted)" }}
    >
      <RefreshCw size={20} className="mx-auto mb-2 animate-spin opacity-60" />
      {label}
    </div>
  );
}

export function ReliabilityEmptyState({
  title,
  detail,
}: {
  title: string;
  detail: string;
}) {
  return (
    <div
      data-testid="reliability-empty-state"
      className="rounded-lg px-4 py-12 text-center"
      style={{ background: "var(--mis-surface)", border: "1px solid var(--mis-border)" }}
    >
      <Inbox size={24} className="mx-auto mb-2 opacity-40" style={{ color: "var(--mis-muted)" }} />
      <p className="text-sm font-medium" style={{ color: "var(--mis-text)" }}>{title}</p>
      <p className="mx-auto mt-1 max-w-lg text-xs leading-relaxed" style={{ color: "var(--mis-muted)" }}>{detail}</p>
    </div>
  );
}

export function ReliabilityUnavailableState({ refresh }: { refresh?: () => Promise<void> }) {
  return (
    <div
      data-testid="reliability-unavailable-state"
      role="alert"
      className="rounded-lg px-4 py-8 text-center"
      style={{ background: "rgba(248,113,113,0.05)", border: "1px solid rgba(248,113,113,0.20)" }}
    >
      <AlertTriangle size={22} className="mx-auto mb-2" style={{ color: "#F87171" }} />
      <p className="text-sm font-medium" style={{ color: "var(--mis-text)" }}>Reliability evidence unavailable</p>
      <p className="mx-auto mt-1 max-w-lg text-xs" style={{ color: "var(--mis-dim)" }}>
        The current workspace could not read the Reliability ledger. No result has been inferred.
      </p>
      {refresh ? <div className="mt-3"><ReliabilityRefreshButton refresh={refresh} label="Retry" /></div> : null}
    </div>
  );
}

export function ReliabilityPanel({
  title,
  description,
  action,
  children,
  className = "",
}: {
  title: string;
  description?: string;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section
      className={`min-w-0 rounded-lg ${className}`}
      style={{ background: "var(--mis-surface)", border: "1px solid var(--mis-border)" }}
    >
      <div className="flex flex-wrap items-start justify-between gap-2 border-b px-4 py-3" style={{ borderColor: "var(--mis-border)" }}>
        <div>
          <h2 className="text-xs font-semibold" style={{ color: "var(--mis-text)" }}>{title}</h2>
          {description ? <p className="mt-0.5 text-[10px] leading-relaxed" style={{ color: "var(--mis-muted)" }}>{description}</p> : null}
        </div>
        {action}
      </div>
      <div className="p-4">{children}</div>
    </section>
  );
}

export function ReliabilityMetric({ label, value, status }: { label: string; value: ReactNode; status?: string }) {
  return (
    <div className="rounded-lg p-3" style={{ background: "var(--mis-surface)", border: "1px solid var(--mis-border)" }}>
      <div className="flex items-center justify-between gap-2">
        <span className="text-[10px] uppercase tracking-wide" style={{ color: "var(--mis-muted)" }}>{label}</span>
        {status ? <StatusBadge status={status === "block" ? "fail" : status} label={status.toUpperCase()} /> : null}
      </div>
      <div className="mt-2 text-xl font-semibold tabular-nums" style={{ color: "var(--mis-text)" }}>{value}</div>
    </div>
  );
}

export function ReliabilityId({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <span className={`break-all font-mono text-[10px] ${className}`} style={{ color: "var(--mis-muted)" }}>{children}</span>;
}

export function ReliabilityStatus({ status, label }: { status: string; label?: string }) {
  return <StatusBadge status={status === "block" ? "fail" : status} label={label ?? status.toUpperCase()} size="md" />;
}

export function ReliabilityKeyValue({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex min-w-0 items-start justify-between gap-3 border-b py-2 last:border-0" style={{ borderColor: "var(--mis-border)" }}>
      <dt className="shrink-0 text-[10px]" style={{ color: "var(--mis-muted)" }}>{label}</dt>
      <dd className="min-w-0 text-right text-[11px]" style={{ color: "var(--mis-dim)" }}>{value}</dd>
    </div>
  );
}

export function ReliabilityJsonView({ value, maxLength = 1200 }: { value: ReliabilityJson | undefined; maxLength?: number }) {
  const serialized = formatReliabilityJson(value, maxLength);
  return (
    <pre className="max-h-56 overflow-auto whitespace-pre-wrap break-words rounded p-2 font-mono text-[10px] leading-relaxed" style={{ background: "var(--mis-bg)", color: "var(--mis-dim)", border: "1px solid var(--mis-border)" }}>
      {serialized}
    </pre>
  );
}

export function ReliabilityArrowLink({ to, children }: { to: string; children: ReactNode }) {
  return (
    <Link to={to} className="inline-flex items-center gap-1 text-[11px] hover:opacity-80" style={{ color: "var(--mis-cyan)" }}>
      {children}
      <ArrowRight size={11} />
    </Link>
  );
}

export function ReliabilityTable({ headers, children, minWidth = 900 }: { headers: string[]; children: ReactNode; minWidth?: number }) {
  return (
    <div className="overflow-x-auto rounded-lg" style={{ background: "var(--mis-surface)", border: "1px solid var(--mis-border)" }}>
      <table className="w-full text-xs" style={{ minWidth }}>
        <thead>
          <tr style={{ background: "var(--mis-surface2)", color: "var(--mis-muted)" }}>
            {headers.map((header, index) => <th key={`${header}-${index}`} className="px-4 py-3 text-left text-[10px] font-medium uppercase tracking-wide">{header}</th>)}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

export function ReliabilityRow({ children }: { children: ReactNode }) {
  return <tr className="border-t first:border-t-0" style={{ borderColor: "var(--mis-border)", color: "var(--mis-dim)" }}>{children}</tr>;
}

export function formatReliabilityDate(value?: string | null): string {
  if (!value) return "—";
  const timestamp = new Date(value);
  return Number.isNaN(timestamp.getTime()) ? "—" : timestamp.toLocaleString();
}

export function formatReliabilityJson(value: ReliabilityJson | undefined, maxLength = 240): string {
  if (value === undefined) return "—";
  let serialized = "—";
  try {
    serialized = JSON.stringify(value, null, 2);
  } catch {
    return "Unrenderable evidence";
  }
  if (serialized.length <= maxLength) return serialized;
  return `${serialized.slice(0, maxLength)}…`;
}

export function formatReliabilityScore(value: number | null): string {
  return value === null ? "—" : `${Math.round(value * 100)}%`;
}

export function reliabilityDecisionStatus(value: string): string {
  return value === "block" ? "fail" : value;
}
