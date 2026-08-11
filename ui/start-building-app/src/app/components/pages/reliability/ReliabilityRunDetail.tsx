import { useMemo } from "react";
import { ArrowLeft, CheckCircle2, Clock3, ExternalLink, GitCommit, ShieldAlert, Wrench } from "lucide-react";
import { Link, useParams } from "react-router";
import { useLiveData } from "../../../data/liveApi";
import {
  loadReliabilityRun,
  selectReliabilityCurrentGate,
  type ReliabilityEvaluation,
  type ReliabilityJson,
  type ReliabilityToolCall,
} from "../../../data/reliabilityApi";
import {
  ReliabilityEmptyState,
  ReliabilityId,
  ReliabilityJsonView,
  ReliabilityKeyValue,
  ReliabilityLoadingState,
  ReliabilityMetric,
  ReliabilityPage,
  ReliabilityPanel,
  ReliabilityRefreshButton,
  ReliabilityStatus,
  ReliabilityUnavailableState,
  formatReliabilityDate,
  formatReliabilityScore,
} from "./ReliabilityUi";

const SAFE_EVALUATION_METADATA = [
  "model",
  "provider",
  "prompt_version",
  "judge_version",
  "temperature",
  "turn_index",
  "tool_call_id",
  "expectation",
  "expected",
  "observed",
] as const;

function visibleEvaluationMetadata(evaluation: ReliabilityEvaluation): Record<string, ReliabilityJson> {
  const result: Record<string, ReliabilityJson> = {};
  for (const key of SAFE_EVALUATION_METADATA) {
    const value = evaluation.metadata[key];
    if (value !== undefined) result[key] = value;
  }
  return result;
}

function ToolTrace({ call }: { call: ReliabilityToolCall }) {
  return (
    <article className="rounded-lg p-3" style={{ background: "var(--mis-bg)", border: "1px solid var(--mis-border)" }}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Wrench size={12} style={{ color: call.error ? "#F87171" : "var(--mis-purple)" }} />
          <span className="font-mono text-[11px] font-semibold" style={{ color: "var(--mis-text)" }}>{call.name}</span>
          {call.is_mutation ? <ReliabilityStatus status="attention" label="MUTATION" /> : null}
        </div>
        <span className="text-[10px] tabular-nums" style={{ color: "var(--mis-muted)" }}>{call.duration_ms} ms</span>
      </div>
      <div className="mt-2 grid grid-cols-1 gap-2 lg:grid-cols-2">
        <div>
          <div className="mb-1 text-[9px] font-medium uppercase tracking-wide" style={{ color: "var(--mis-muted)" }}>Arguments</div>
          <ReliabilityJsonView value={call.arguments} maxLength={800} />
        </div>
        <div>
          <div className="mb-1 text-[9px] font-medium uppercase tracking-wide" style={{ color: call.error ? "#F87171" : "var(--mis-muted)" }}>{call.error ? "Error" : "Result"}</div>
          {call.error ? <div className="rounded p-2 text-[10px]" style={{ color: "#F87171", background: "rgba(248,113,113,0.06)" }}>{call.error}</div> : <ReliabilityJsonView value={call.result} maxLength={800} />}
        </div>
      </div>
      <div className="mt-2 flex flex-wrap gap-3"><ReliabilityId>{call.tool_call_id}</ReliabilityId>{call.mis_tool_call_id ? <ReliabilityId>MIS: {call.mis_tool_call_id}</ReliabilityId> : null}</div>
    </article>
  );
}

export function ReliabilityRunDetail() {
  const { id = "" } = useParams();
  const { data, loading, error, refresh } = useLiveData(async () => {
    if (!id) throw new Error("run_id_required");
    return loadReliabilityRun(id);
  }, [id]);
  const detail = data?.run_detail;
  const summary = data?.summary;
  const callsByTurn = useMemo(() => {
    const grouped = new Map<string, ReliabilityToolCall[]>();
    for (const call of detail?.tool_calls ?? []) {
      const current = grouped.get(call.turn_id) ?? [];
      current.push(call);
      grouped.set(call.turn_id, current);
    }
    return grouped;
  }, [detail?.tool_calls]);

  const run = detail?.run;
  const campaign_id = run?.campaign_id ?? "";
  const mis_run_id = detail?.mis_links.run_id ?? run?.mis_run_id ?? null;
  const currentGate = selectReliabilityCurrentGate(detail?.campaign, detail?.release_gates);

  return (
    <ReliabilityPage
      title={run ? `Run · ${run.run_id}` : "Run detail"}
      description="Conversation trace, observed tools, evaluator verdicts and cryptographic evidence in one read-only chain."
      actions={<ReliabilityRefreshButton refresh={refresh} />}
    >
      <div data-testid="reliability-run-detail" className="space-y-4">
        <Link to={campaign_id ? `/workspace/reliability/campaigns/${encodeURIComponent(campaign_id)}` : "/workspace/reliability/campaigns"} className="inline-flex items-center gap-1 text-[11px] hover:opacity-80" style={{ color: "var(--mis-cyan)" }}>
          <ArrowLeft size={12} /> Back to campaign
        </Link>
        {loading && !detail ? <ReliabilityLoadingState label="Loading run evidence chain…" /> : null}
        {error && !detail ? <ReliabilityUnavailableState refresh={refresh} /> : null}
        {detail && run && summary ? (
          <>
            <section data-testid="reliability-run-evidence-chain" className="rounded-lg p-4" style={{ background: "var(--mis-surface)", border: "1px solid var(--mis-border)" }}>
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <div className="flex items-center gap-2">
                    {run.status === "pass" ? <CheckCircle2 size={18} style={{ color: "var(--mis-success)" }} /> : <ShieldAlert size={18} style={{ color: "#F87171" }} />}
                    <ReliabilityStatus status={run.status} />
                    {currentGate ? <ReliabilityStatus status={currentGate.decision} label={`GATE ${currentGate.decision.toUpperCase()}`} /> : <ReliabilityStatus status="unknown" label="GATE UNAVAILABLE" />}
                  </div>
                  <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[10px]" style={{ color: "var(--mis-muted)" }}>
                    <span>Scenario <ReliabilityId>{run.scenario_id}</ReliabilityId></span>
                    <span>Agent <ReliabilityId>{run.agent_version_id}</ReliabilityId></span>
                    <span>{formatReliabilityDate(run.created_at)}</span>
                  </div>
                </div>
                <div className="flex flex-wrap gap-2">
                  <Link
                    data-testid="reliability-run-campaign-link"
                    to={`/workspace/reliability/campaigns/${encodeURIComponent(run.campaign_id)}`}
                    className="inline-flex items-center gap-1 rounded px-2.5 py-1.5 text-[11px]"
                    style={{ color: "var(--mis-cyan)", border: "1px solid rgba(34,211,238,0.22)" }}
                  >
                    Campaign <ExternalLink size={11} />
                  </Link>
                  {mis_run_id ? (
                    <Link
                      data-testid="reliability-run-mis-evidence-link"
                      to={`/admin/runs/${encodeURIComponent(mis_run_id)}`}
                      className="inline-flex items-center gap-1 rounded px-2.5 py-1.5 text-[11px]"
                      style={{ color: "var(--mis-purple)", border: "1px solid color-mix(in srgb, var(--mis-purple) 30%, transparent)" }}
                    >
                      MIS Run <ExternalLink size={11} />
                    </Link>
                  ) : null}
                </div>
              </div>
            </section>

            <section className="grid grid-cols-2 gap-3 lg:grid-cols-5">
              <ReliabilityMetric label="Outcome" value={<ReliabilityStatus status={summary.status} />} />
              <ReliabilityMetric label="Turns" value={summary.turn_count} />
              <ReliabilityMetric label="Latency" value={summary.latency_ms === null ? "—" : `${summary.latency_ms} ms`} />
              <ReliabilityMetric label="Tool calls" value={summary.tool_call_count} />
              <ReliabilityMetric label="Evaluators" value={summary.evaluator_count} />
            </section>

            <section className="grid grid-cols-1 gap-3 xl:grid-cols-[minmax(190px,0.72fr)_minmax(360px,1.45fr)_minmax(260px,1fr)]">
              <div data-testid="reliability-run-context-column" className="min-w-0 space-y-3">
                <ReliabilityPanel title="Conversation context" description="Stable provenance and turn navigation.">
                  <dl>
                    <ReliabilityKeyValue label="Campaign" value={<ReliabilityId>{run.campaign_id}</ReliabilityId>} />
                    <ReliabilityKeyValue label="Scenario" value={<ReliabilityId>{run.scenario_id}</ReliabilityId>} />
                    <ReliabilityKeyValue label="Agent version" value={<ReliabilityId>{run.agent_version_id}</ReliabilityId>} />
                    <ReliabilityKeyValue label="MIS Task" value={<ReliabilityId>{detail.mis_links.task_id ?? "unmapped"}</ReliabilityId>} />
                    <ReliabilityKeyValue label="MIS Plan" value={<ReliabilityId>{detail.mis_links.plan_id ?? "unmapped"}</ReliabilityId>} />
                    <ReliabilityKeyValue label="MIS Run" value={<ReliabilityId>{mis_run_id ?? "unmapped"}</ReliabilityId>} />
                  </dl>
                </ReliabilityPanel>
                <ReliabilityPanel title="Timeline" description={`${detail.turns.length} recorded turns`}>
                  <ol className="space-y-1">
                    {detail.turns.map((turn) => (
                      <li key={turn.turn_id}>
                        <a href={`#turn-${turn.turn_index}`} className="flex items-center justify-between gap-2 rounded px-2 py-1.5 text-[10px] hover:opacity-80" style={{ background: "var(--mis-surface2)", color: "var(--mis-dim)" }}>
                          <span className="truncate">{turn.turn_index + 1}. {turn.role}</span>
                          <span className="tabular-nums" style={{ color: "var(--mis-muted)" }}>{callsByTurn.get(turn.turn_id)?.length ?? 0} tools</span>
                        </a>
                      </li>
                    ))}
                  </ol>
                </ReliabilityPanel>
              </div>

              <div data-testid="reliability-run-trace-column" className="min-w-0">
                <ReliabilityPanel title="Transcript & tool trace" description="Observed user/agent turns with tool evidence anchored to the originating turn.">
                  {detail.turns.length === 0 ? (
                    <ReliabilityEmptyState title="No turns recorded" detail="This run contains no conversation trace." />
                  ) : (
                    <div className="space-y-3">
                      {detail.turns.map((turn) => {
                        const calls = callsByTurn.get(turn.turn_id) ?? [];
                        const hideContent = turn.role === "system";
                        return (
                          <article key={turn.turn_id} id={`turn-${turn.turn_index}`} className="scroll-mt-4 space-y-2">
                            <div className={`flex ${turn.role === "assistant" ? "justify-end" : "justify-start"}`}>
                              <div className="max-w-[92%] rounded-lg px-3 py-2.5" style={{ background: turn.role === "assistant" ? "rgba(34,211,238,0.08)" : "var(--mis-surface2)", border: `1px solid ${turn.role === "assistant" ? "rgba(34,211,238,0.18)" : "var(--mis-border)"}` }}>
                                <div className="flex items-center justify-between gap-4 text-[9px] font-medium uppercase tracking-wide" style={{ color: "var(--mis-muted)" }}>
                                  <span>{turn.role}</span><span>Turn {turn.turn_index + 1}</span>
                                </div>
                                <p className="mt-1.5 whitespace-pre-wrap break-words text-xs leading-relaxed" style={{ color: "var(--mis-text)" }}>
                                  {hideContent ? "System context is intentionally omitted from this view." : turn.content}
                                </p>
                              </div>
                            </div>
                            {calls.map((call) => <ToolTrace key={call.tool_call_id} call={call} />)}
                          </article>
                        );
                      })}
                    </div>
                  )}
                </ReliabilityPanel>
              </div>

              <div data-testid="reliability-run-verdict-column" className="min-w-0 space-y-3">
                <ReliabilityPanel title="Evaluator verdicts" description="Scores remain explainable through reason codes and evidence references.">
                  <div className="space-y-2">
                    {detail.evaluations.map((evaluation) => {
                      const visibleMetadata = visibleEvaluationMetadata(evaluation);
                      return (
                        <article key={evaluation.evaluation_id} className="rounded-lg p-3" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
                          <div className="flex items-center justify-between gap-2">
                            <span className="break-all font-mono text-[10px] font-medium" style={{ color: "var(--mis-text)" }}>{evaluation.evaluator_id}</span>
                            <ReliabilityStatus status={evaluation.status} />
                          </div>
                          <div className="mt-2 flex gap-4 text-[10px]" style={{ color: "var(--mis-dim)" }}>
                            <span>Score {formatReliabilityScore(evaluation.score)}</span>
                            <span>Threshold {formatReliabilityScore(evaluation.threshold)}</span>
                          </div>
                          {evaluation.reason_codes.length > 0 ? <div className="mt-2 flex flex-wrap gap-1">{evaluation.reason_codes.map((code) => <span key={code} className="rounded px-1.5 py-0.5 font-mono text-[9px]" style={{ background: "var(--mis-bg)", color: "#FBBF24" }}>{code}</span>)}</div> : null}
                          {evaluation.evidence_refs.length > 0 ? <div className="mt-2 space-y-1">{evaluation.evidence_refs.map((ref) => <div key={ref}><ReliabilityId>{ref}</ReliabilityId></div>)}</div> : null}
                          {Object.keys(visibleMetadata).length > 0 ? <div className="mt-2"><ReliabilityJsonView value={visibleMetadata} maxLength={500} /></div> : null}
                        </article>
                      );
                    })}
                  </div>
                </ReliabilityPanel>

                <ReliabilityPanel title="Evidence manifest" description="Artifact hashes bind this run to scenario, agent config and evaluator versions.">
                  {detail.manifests.length === 0 ? <p className="text-xs" style={{ color: "var(--mis-muted)" }}>No manifest recorded.</p> : detail.manifests.map((manifest) => (
                    <article key={manifest.manifest_id} className="space-y-2">
                      <div className="flex items-center gap-2"><GitCommit size={12} style={{ color: "var(--mis-purple)" }} /><ReliabilityId>{manifest.git_commit_sha}</ReliabilityId></div>
                      <dl>
                        <ReliabilityKeyValue label="Manifest" value={<ReliabilityId>{manifest.manifest_id}</ReliabilityId>} />
                        <ReliabilityKeyValue label="MIS Artifact" value={<ReliabilityId>{manifest.mis_artifact_id ?? "unmapped"}</ReliabilityId>} />
                        <ReliabilityKeyValue label="Plan evidence" value={<ReliabilityId>{manifest.mis_plan_evidence_manifest_id ?? "unmapped"}</ReliabilityId>} />
                        <ReliabilityKeyValue label="Environment" value={`${manifest.environment.os ?? "unknown"} · Python ${manifest.environment.python_version ?? "unknown"}`} />
                        <ReliabilityKeyValue label="Duration" value={<span className="inline-flex items-center gap-1"><Clock3 size={10} />{formatReliabilityDate(manifest.started_at)} → {formatReliabilityDate(manifest.finished_at)}</span>} />
                      </dl>
                      <div className="space-y-1">
                        {Object.entries(manifest.artifacts).map(([name, sha]) => <div key={name} className="rounded px-2 py-1.5" style={{ background: "var(--mis-bg)" }}><div className="text-[9px]" style={{ color: "var(--mis-dim)" }}>{name}</div><ReliabilityId>{sha}</ReliabilityId></div>)}
                      </div>
                    </article>
                  ))}
                </ReliabilityPanel>

                <ReliabilityPanel title="Release gate" action={currentGate ? <ReliabilityStatus status={currentGate.decision} /> : undefined}>
                  {currentGate ? (
                    <div className="space-y-2">
                      {[...currentGate.blockers, ...currentGate.warnings].map((fact, index) => <div key={`${fact.rule_id}-${index}`} className="rounded px-2.5 py-2 text-[10px] leading-relaxed" style={{ background: "var(--mis-surface2)", color: "var(--mis-dim)" }}>{fact.message}</div>)}
                      {currentGate.blockers.length === 0 && currentGate.warnings.length === 0 ? <p className="text-xs" style={{ color: "var(--mis-success)" }}>No blockers or warnings.</p> : null}
                    </div>
                  ) : <ReliabilityEmptyState title="Current release gate unavailable" detail="This run's campaign does not identify a current gate in the returned release-gate evidence." />}
                </ReliabilityPanel>
              </div>
            </section>

            {detail.collection_limits.truncated.length > 0 ? (
              <div className="rounded-lg px-3 py-2 text-[10px]" style={{ background: "rgba(251,191,36,0.06)", border: "1px solid rgba(251,191,36,0.18)", color: "#FBBF24" }}>
                Bounded readback: {detail.collection_limits.truncated.join(", ")} reached the {detail.collection_limits.max_items_per_collection}-item display limit.
              </div>
            ) : null}
          </>
        ) : null}
      </div>
    </ReliabilityPage>
  );
}
