import { ArrowLeft, Box, CheckCircle2, FileCheck2, FlaskConical, Hash, LineChart } from "lucide-react";
import type { ReactNode } from "react";
import { Link, useParams } from "react-router";
import { loadResearchExperiment, useLiveData } from "../../data/liveApi";
import { pick, usePreferences } from "../../context/PreferencesContext";
import { StatusBadge } from "../shared/StatusBadge";

function displayNumber(value: number | null) {
  if (value === null) return "—";
  return Math.abs(value) >= 100 ? value.toFixed(1) : value.toFixed(5).replace(/0+$/, "").replace(/\.$/, "");
}

function compactHash(value: string) {
  return value.length > 20 ? `${value.slice(0, 10)}…${value.slice(-8)}` : value || "—";
}

export function ExperimentDetail() {
  const { id = "" } = useParams<{ id: string }>();
  const { locale } = usePreferences();
  const { data, loading, error, refresh } = useLiveData(() => loadResearchExperiment(id), [id]);
  const copy = pick(locale, {
    en: {
      back: "Experiments",
      loading: "Loading experiment readback...",
      unavailable: "Experiment readback unavailable",
      notFound: "not found",
      refresh: "Refresh",
      experimentId: "Experiment ID",
      task: "MIS task",
      protocolHash: "Protocol hash",
      provenanceHash: "Provenance hash",
      trials: "Trials",
      metrics: "Latest metrics",
      artifacts: "Artifacts",
      evaluations: "Evaluations",
      noTrials: "No trials recorded.",
      noMetrics: "No metric evidence recorded.",
      noArtifacts: "No artifact evidence recorded.",
      noEvaluations: "No evaluation evidence recorded.",
      trialHeaders: ["Trial", "Status", "Parameters", "Primary metric", "Run", "Finished"],
      metricHeaders: ["Metric", "Value", "Split", "Step", "Trial", "Recorded"],
      artifactHeaders: ["Artifact", "Type", "Run", "Content hash", "Created"],
      evaluationHeaders: ["Evaluation", "Gate", "Score", "Result", "Run", "Created"],
      stages: {
        smoke: "Smoke",
        pilot: "Pilot",
        search: "Search",
        confirmatory: "Confirmatory",
        ablation: "Ablation",
        robustness: "Robustness",
        reproduction: "Reproduction",
      },
      claims: { pending: "Claim pending", eligible: "Claim eligible", ineligible: "Claim ineligible" },
      readOnly: "Read-only MIS evidence. Raw training output and credentials are not shown.",
    },
    zh: {
      back: "实验",
      loading: "正在加载实验回读...",
      unavailable: "实验回读暂不可用",
      notFound: "未找到实验",
      refresh: "刷新",
      experimentId: "实验 ID",
      task: "MIS 任务",
      protocolHash: "协议哈希",
      provenanceHash: "来源哈希",
      trials: "Trial",
      metrics: "最新指标",
      artifacts: "Artifact",
      evaluations: "评估",
      noTrials: "暂无 Trial 记录。",
      noMetrics: "暂无指标证据。",
      noArtifacts: "暂无 Artifact 证据。",
      noEvaluations: "暂无评估证据。",
      trialHeaders: ["Trial", "状态", "参数", "主指标", "运行", "完成时间"],
      metricHeaders: ["指标", "值", "数据分区", "步数", "Trial", "记录时间"],
      artifactHeaders: ["Artifact", "类型", "运行", "内容哈希", "创建时间"],
      evaluationHeaders: ["评估", "质量门", "分数", "结果", "运行", "创建时间"],
      stages: {
        smoke: "冒烟",
        pilot: "试点",
        search: "搜索",
        confirmatory: "确认性实验",
        ablation: "消融",
        robustness: "稳健性",
        reproduction: "复现",
      },
      claims: { pending: "结论待评估", eligible: "可形成结论", ineligible: "不可形成结论" },
      readOnly: "此处只读展示 MIS 证据，不显示训练原始输出或凭据。",
    },
  });
  const formatTime = (value: string | null) => value ? new Date(value).toLocaleString(locale === "zh" ? "zh-CN" : "en-US") : "—";

  if (loading) {
    return <p className="text-xs" style={{ color: "var(--mis-muted)" }}>{copy.loading}</p>;
  }
  if (error || !data?.experiment?.experiment_id) {
    return (
      <div className="space-y-3">
        <Link to="/workspace/experiments" className="inline-flex items-center gap-1 text-xs" style={{ color: "var(--mis-cyan)" }}>
          <ArrowLeft size={13} />
          {copy.back}
        </Link>
        <p className="text-xs" style={{ color: "#F87171" }}>{copy.unavailable}: {error || copy.notFound}</p>
      </div>
    );
  }

  const { experiment, trials, latest_metrics: metrics, artifacts, evaluations } = data;
  const stageLabel = copy.stages[experiment.stage as keyof typeof copy.stages] || experiment.stage;
  const claimLabel = copy.claims[experiment.claim_status as keyof typeof copy.claims] || experiment.claim_status;
  const claimStatus = experiment.claim_status === "eligible" ? "pass" : experiment.claim_status === "ineligible" ? "fail" : "pending";

  return (
    <div className="space-y-5 w-full">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Link to="/workspace/experiments" className="inline-flex items-center gap-1 text-xs hover:opacity-80" style={{ color: "var(--mis-cyan)" }}>
          <ArrowLeft size={13} />
          {copy.back}
        </Link>
        <button type="button" onClick={() => void refresh()} className="rounded px-3 py-1.5 text-xs" style={{ color: "var(--mis-cyan)", border: "1px solid rgba(34,211,238,0.2)" }}>
          {copy.refresh}
        </button>
      </div>

      <section className="rounded-lg p-5" style={{ background: "var(--mis-surface)", border: "1px solid var(--mis-border)" }}>
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <FlaskConical size={17} style={{ color: "var(--mis-cyan)" }} />
              <h1 className="text-lg font-semibold" style={{ color: "var(--mis-text)" }}>{experiment.name}</h1>
              <StatusBadge status={experiment.status} size="md" />
              <StatusBadge status="planned" size="md" label={stageLabel} />
              <StatusBadge status={claimStatus} size="md" label={claimLabel} />
            </div>
            <p className="mt-2 text-xs" style={{ color: "var(--mis-muted)" }}>{copy.readOnly}</p>
          </div>
        </div>
        <div className="mt-4 grid grid-cols-1 gap-3 border-t pt-4 sm:grid-cols-2 xl:grid-cols-4" style={{ borderColor: "var(--mis-border)" }}>
          {[
            { label: copy.experimentId, value: experiment.experiment_id, link: "" },
            { label: copy.task, value: experiment.task_id || "—", link: experiment.task_id ? `/admin/tasks/${experiment.task_id}` : "" },
            { label: copy.protocolHash, value: compactHash(experiment.protocol_hash), link: "" },
            { label: copy.provenanceHash, value: compactHash(experiment.provenance_hash), link: "" },
          ].map((item) => (
            <div key={item.label} className="min-w-0">
              <div className="text-[10px] uppercase tracking-wide" style={{ color: "var(--mis-muted)" }}>{item.label}</div>
              {item.link ? (
                <Link to={item.link} className="mt-1 block truncate font-mono text-xs hover:opacity-80" style={{ color: "var(--mis-cyan)" }}>{item.value}</Link>
              ) : (
                <div className="mt-1 truncate font-mono text-xs" title={item.label.includes("hash") || item.label.includes("哈希") ? (item.label === copy.protocolHash ? experiment.protocol_hash : experiment.provenance_hash) : item.value} style={{ color: "var(--mis-dim)" }}>{item.value}</div>
              )}
            </div>
          ))}
        </div>
      </section>

      <section className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {[
          { label: copy.trials, value: `${experiment.completed_trial_count}/${experiment.trial_count}`, icon: <FlaskConical size={14} />, status: experiment.completed_trial_count === experiment.trial_count && experiment.trial_count > 0 ? "pass" : experiment.status },
          { label: copy.metrics, value: experiment.metric_count || metrics.length, icon: <LineChart size={14} />, status: metrics.length > 0 ? "pass" : "planned" },
          { label: copy.artifacts, value: experiment.artifact_count || artifacts.length, icon: <Box size={14} />, status: artifacts.length > 0 ? "pass" : "planned" },
          { label: copy.evaluations, value: experiment.evaluation_count || evaluations.length, icon: <CheckCircle2 size={14} />, status: evaluations.some((item) => item.pass_fail === "fail") ? "fail" : evaluations.length > 0 ? "pass" : "planned" },
        ].map((item) => (
          <div key={item.label} className="rounded-lg p-4" style={{ background: "var(--mis-surface)", border: "1px solid var(--mis-border)" }}>
            <div className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-1.5 text-[11px]" style={{ color: "var(--mis-muted)" }}>{item.icon}{item.label}</div>
              <StatusBadge status={item.status} />
            </div>
            <div className="mt-2 text-xl font-semibold" style={{ color: "var(--mis-text)" }}>{item.value}</div>
          </div>
        ))}
      </section>

      <ReadbackTable title={copy.trials} icon={<FlaskConical size={14} />} headers={copy.trialHeaders} empty={copy.noTrials} rows={trials.map((trial) => [
        <span className="font-mono" style={{ color: "var(--mis-cyan)" }}>{trial.trial_id}</span>,
        <StatusBadge status={trial.status} />,
        <span className="block max-w-[280px] truncate font-mono text-[10px]" title={JSON.stringify(trial.params)}>{Object.keys(trial.params).length ? JSON.stringify(trial.params) : "—"}</span>,
        <span><span className="font-mono" style={{ color: "var(--mis-text)" }}>{displayNumber(trial.final_metric_value)}</span><span className="ml-1 text-[10px]">{trial.primary_metric}</span></span>,
        trial.run_id ? <Link to={`/admin/runs/${trial.run_id}`} className="font-mono" style={{ color: "var(--mis-cyan)" }}>{trial.run_id}</Link> : "—",
        formatTime(trial.ended_at),
      ])} />

      <ReadbackTable title={copy.metrics} icon={<LineChart size={14} />} headers={copy.metricHeaders} empty={copy.noMetrics} rows={metrics.map((metric) => [
        <span style={{ color: "var(--mis-text)" }}>{metric.name}</span>,
        <span className="font-mono" style={{ color: "var(--mis-cyan)" }}>{displayNumber(metric.value)}</span>,
        metric.split,
        metric.step ?? "—",
        <span className="font-mono text-[10px]">{metric.trial_id}</span>,
        formatTime(metric.recorded_at),
      ])} />

      <ReadbackTable title={copy.artifacts} icon={<Box size={14} />} headers={copy.artifactHeaders} empty={copy.noArtifacts} rows={artifacts.map((artifact) => [
        <span title={artifact.summary}><span style={{ color: "var(--mis-text)" }}>{artifact.title}</span><span className="mt-0.5 block font-mono text-[10px]">{artifact.artifact_id}</span></span>,
        artifact.artifact_type,
        artifact.run_id ? <Link to={`/admin/runs/${artifact.run_id}`} className="font-mono" style={{ color: "var(--mis-cyan)" }}>{artifact.run_id}</Link> : "—",
        <span className="inline-flex items-center gap-1 font-mono text-[10px]" title={artifact.content_hash}><Hash size={11} />{compactHash(artifact.content_hash)}</span>,
        formatTime(artifact.created_at),
      ])} />

      <ReadbackTable title={copy.evaluations} icon={<FileCheck2 size={14} />} headers={copy.evaluationHeaders} empty={copy.noEvaluations} rows={evaluations.map((evaluation) => [
        <span title={evaluation.notes} className="font-mono text-[10px]">{evaluation.evaluation_id}</span>,
        evaluation.evaluator_type,
        displayNumber(evaluation.score),
        <StatusBadge status={evaluation.pass_fail} />,
        evaluation.run_id ? <Link to={`/admin/runs/${evaluation.run_id}`} className="font-mono" style={{ color: "var(--mis-cyan)" }}>{evaluation.run_id}</Link> : "—",
        formatTime(evaluation.created_at),
      ])} />
    </div>
  );
}

function ReadbackTable({
  title,
  icon,
  headers,
  rows,
  empty,
}: {
  title: string;
  icon: ReactNode;
  headers: string[];
  rows: ReactNode[][];
  empty: string;
}) {
  return (
    <section className="overflow-hidden rounded-lg" style={{ background: "var(--mis-surface)", border: "1px solid var(--mis-border)" }}>
      <div className="flex items-center gap-2 border-b px-4 py-3 text-sm font-semibold" style={{ color: "var(--mis-text)", borderColor: "var(--mis-border)" }}>
        {icon}
        {title}
        <span className="text-[10px] font-normal" style={{ color: "var(--mis-muted)" }}>{rows.length}</span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[780px] text-xs">
          <thead>
            <tr style={{ background: "var(--mis-surface2)", color: "var(--mis-muted)" }}>
              {headers.map((header) => <th key={header} className="px-4 py-2.5 text-left font-medium">{header}</th>)}
            </tr>
          </thead>
          <tbody>
            {rows.map((cells, rowIndex) => (
              <tr key={`${title}-${rowIndex}`} style={{ color: "var(--mis-dim)", borderTop: rowIndex > 0 ? "1px solid var(--mis-border)" : "none" }}>
                {cells.map((cell, cellIndex) => <td key={`${rowIndex}-${cellIndex}`} className="px-4 py-3">{cell}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
        {rows.length === 0 && <div className="px-4 py-8 text-center text-xs" style={{ color: "var(--mis-muted)" }}>{empty}</div>}
      </div>
    </section>
  );
}
