import { ArrowRight, FlaskConical, RefreshCw, ShieldCheck } from "lucide-react";
import { Link } from "react-router";
import { loadResearchExperiments, useLiveData } from "../../data/liveApi";
import { pick, usePreferences } from "../../context/PreferencesContext";
import { StatusBadge } from "../shared/StatusBadge";

function formatMetric(value: number | null) {
  if (value === null) return "—";
  return Math.abs(value) >= 100 ? value.toFixed(1) : value.toFixed(4).replace(/0+$/, "").replace(/\.$/, "");
}

export function Experiments() {
  const { locale } = usePreferences();
  const { data, loading, error, refresh } = useLiveData(loadResearchExperiments, []);
  const experiments = data || [];
  const copy = pick(locale, {
    en: {
      title: "Deep-learning experiments",
      subtitle: "Read-only experiment, trial, metric and claim-gate evidence from the AgentOps MIS research ledger.",
      total: "Experiments",
      running: "Running",
      eligible: "Claim eligible",
      attention: "Needs review",
      refresh: "Refresh",
      loading: "Loading experiment ledger...",
      unavailable: "Experiment ledger unavailable",
      empty: "No experiments have been recorded yet.",
      emptyHint: "Run a confirmed local Research Lab experiment, then refresh this ledger.",
      headers: ["Experiment", "Stage", "Status", "Claim gate", "Trials", "Latest metric", "Updated", ""],
      open: "Open",
      trials: "trials",
      stages: {
        smoke: "Smoke",
        pilot: "Pilot",
        search: "Search",
        confirmatory: "Confirmatory",
        ablation: "Ablation",
        robustness: "Robustness",
        reproduction: "Reproduction",
      },
      claims: { pending: "Pending", eligible: "Eligible", ineligible: "Ineligible" },
    },
    zh: {
      title: "深度学习实验",
      subtitle: "只读查看 AgentOps MIS 研究账本中的实验、Trial、指标与结论门证据。",
      total: "实验总数",
      running: "运行中",
      eligible: "结论可用",
      attention: "需要复核",
      refresh: "刷新",
      loading: "正在加载实验账本...",
      unavailable: "实验账本暂不可用",
      empty: "还没有记录实验。",
      emptyHint: "完成一次显式确认的本地 Research Lab 实验后，刷新此账本。",
      headers: ["实验", "阶段", "状态", "结论门", "Trial", "最新指标", "更新时间", ""],
      open: "打开",
      trials: "个 Trial",
      stages: {
        smoke: "冒烟",
        pilot: "试点",
        search: "搜索",
        confirmatory: "确认性实验",
        ablation: "消融",
        robustness: "稳健性",
        reproduction: "复现",
      },
      claims: { pending: "待评估", eligible: "可形成结论", ineligible: "不可形成结论" },
    },
  });
  const running = experiments.filter((item) => item.status === "running").length;
  const eligible = experiments.filter((item) => item.claim_status === "eligible").length;
  const attention = experiments.filter((item) => item.status === "failed" || item.status === "blocked" || item.claim_status === "ineligible").length;
  const stageLabel = (stage: string) => copy.stages[stage as keyof typeof copy.stages] || stage;
  const claimLabel = (claim: string) => copy.claims[claim as keyof typeof copy.claims] || claim;

  return (
    <div className="space-y-5 w-full">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold" style={{ color: "var(--mis-text)" }}>{copy.title}</h1>
          <p className="mt-1 max-w-3xl text-xs leading-relaxed" style={{ color: "var(--mis-dim)" }}>{copy.subtitle}</p>
        </div>
        <button
          type="button"
          onClick={() => void refresh()}
          className="inline-flex items-center gap-1.5 rounded px-3 py-1.5 text-xs"
          style={{ background: "rgba(34,211,238,0.12)", color: "var(--mis-cyan)", border: "1px solid rgba(34,211,238,0.2)" }}
        >
          <RefreshCw size={13} />
          {copy.refresh}
        </button>
      </div>

      <section className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        {[
          { label: copy.total, value: experiments.length, status: experiments.length > 0 ? "pass" : "planned" },
          { label: copy.running, value: running, status: running > 0 ? "running" : "planned" },
          { label: copy.eligible, value: eligible, status: eligible > 0 ? "pass" : "planned" },
          { label: copy.attention, value: attention, status: attention > 0 ? "attention" : "pass" },
        ].map((item) => (
          <div key={item.label} className="rounded-lg p-4" style={{ background: "var(--mis-surface)", border: "1px solid var(--mis-border)" }}>
            <div className="flex items-center justify-between gap-2">
              <span className="text-[11px]" style={{ color: "var(--mis-muted)" }}>{item.label}</span>
              <StatusBadge status={item.status} />
            </div>
            <div className="mt-2 text-2xl font-semibold" style={{ color: "var(--mis-text)" }}>{item.value}</div>
          </div>
        ))}
      </section>

      {(loading || error) && (
        <div className="rounded-lg px-4 py-3 text-xs" style={{ background: "var(--mis-surface)", border: "1px solid var(--mis-border)", color: error ? "#F87171" : "var(--mis-muted)" }}>
          {error ? `${copy.unavailable}: ${error}` : copy.loading}
        </div>
      )}

      <div className="overflow-x-auto rounded-lg" style={{ background: "var(--mis-surface)", border: "1px solid var(--mis-border)" }}>
        <table className="w-full min-w-[960px] text-xs">
          <thead>
            <tr style={{ background: "var(--mis-surface2)", color: "var(--mis-muted)" }}>
              {copy.headers.map((header) => <th key={header || "actions"} className="px-4 py-3 text-left font-medium">{header}</th>)}
            </tr>
          </thead>
          <tbody>
            {experiments.map((experiment, index) => (
              <tr key={experiment.experiment_id} style={{ color: "var(--mis-dim)", borderTop: index > 0 ? "1px solid var(--mis-border)" : "none" }}>
                <td className="px-4 py-3">
                  <Link to={`/workspace/experiments/${encodeURIComponent(experiment.experiment_id)}`} className="font-medium hover:opacity-80" style={{ color: "var(--mis-text)" }}>
                    {experiment.name}
                  </Link>
                  <div className="mt-1 max-w-[250px] truncate font-mono text-[10px]" style={{ color: "var(--mis-muted)" }}>{experiment.experiment_id}</div>
                </td>
                <td className="px-4 py-3"><StatusBadge status="planned" label={stageLabel(experiment.stage)} /></td>
                <td className="px-4 py-3"><StatusBadge status={experiment.status} /></td>
                <td className="px-4 py-3">
                  <StatusBadge
                    status={experiment.claim_status === "eligible" ? "pass" : experiment.claim_status === "ineligible" ? "fail" : "pending"}
                    label={claimLabel(experiment.claim_status)}
                  />
                </td>
                <td className="px-4 py-3">{experiment.completed_trial_count}/{experiment.trial_count} {copy.trials}</td>
                <td className="px-4 py-3">
                  <div className="font-mono" style={{ color: "var(--mis-cyan)" }}>{formatMetric(experiment.final_metric_value)}</div>
                  <div className="mt-0.5 text-[10px]" style={{ color: "var(--mis-muted)" }}>{experiment.primary_metric || "—"}</div>
                </td>
                <td className="px-4 py-3 text-[11px]" style={{ color: "var(--mis-muted)" }}>
                  {experiment.updated_at ? new Date(experiment.updated_at).toLocaleString(locale === "zh" ? "zh-CN" : "en-US") : "—"}
                </td>
                <td className="px-4 py-3">
                  <Link
                    to={`/workspace/experiments/${encodeURIComponent(experiment.experiment_id)}`}
                    className="inline-flex items-center gap-1 rounded px-2 py-1 hover:opacity-80"
                    style={{ color: "var(--mis-cyan)", border: "1px solid rgba(34,211,238,0.2)" }}
                  >
                    {copy.open}
                    <ArrowRight size={12} />
                  </Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!loading && !error && experiments.length === 0 && (
          <div className="py-14 text-center" style={{ color: "var(--mis-muted)" }}>
            <FlaskConical size={26} className="mx-auto mb-2 opacity-40" />
            <p className="text-sm" style={{ color: "var(--mis-text)" }}>{copy.empty}</p>
            <p className="mt-1 text-xs">{copy.emptyHint}</p>
          </div>
        )}
      </div>

      <div className="flex items-center gap-2 text-[10px]" style={{ color: "var(--mis-muted)" }}>
        <ShieldCheck size={12} style={{ color: "var(--mis-success)" }} />
        {locale === "zh" ? "此页面只读取 MIS 权威账本，不会启动训练或修改实验。" : "This page only reads the MIS authority ledger; it does not start or modify experiments."}
      </div>
    </div>
  );
}
