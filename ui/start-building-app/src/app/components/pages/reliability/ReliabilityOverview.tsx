import { Activity, ArrowRight, ShieldCheck } from "lucide-react";
import { Link } from "react-router";
import { useLiveData } from "../../../data/liveApi";
import { loadReliabilityOverview } from "../../../data/reliabilityApi";
import {
  ReliabilityEmptyState,
  ReliabilityId,
  ReliabilityLoadingState,
  ReliabilityMetric,
  ReliabilityPage,
  ReliabilityPanel,
  ReliabilityRefreshButton,
  ReliabilityStatus,
  ReliabilityUnavailableState,
  formatReliabilityDate,
} from "./ReliabilityUi";

export function ReliabilityOverview() {
  const { data, loading, error, refresh } = useLiveData(loadReliabilityOverview, []);
  const overview = data?.overview;

  return (
    <ReliabilityPage
      title="Reliability Lab"
      description="Evidence-first simulation, deterministic evaluation, regression capture and release gating for AI agents."
      actions={<ReliabilityRefreshButton refresh={refresh} />}
    >
      {loading && !overview ? <ReliabilityLoadingState label="Loading Reliability Lab evidence…" /> : null}
      {error && !overview ? <ReliabilityUnavailableState refresh={refresh} /> : null}
      {overview ? (
        <>
          <section className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-7">
            <ReliabilityMetric label="Agents" value={overview.counts.agents} />
            <ReliabilityMetric label="Suites" value={overview.counts.scenario_suites} />
            <ReliabilityMetric label="Campaigns" value={overview.counts.campaigns} />
            <ReliabilityMetric label="Runs" value={overview.counts.runs} />
            <ReliabilityMetric label="Failures" value={overview.counts.failures} status={overview.counts.failures > 0 ? "fail" : "pass"} />
            <ReliabilityMetric label="Regressions" value={overview.counts.regressions} />
            <ReliabilityMetric label="Release gates" value={overview.counts.release_gates} />
          </section>

          <section className="grid grid-cols-1 gap-3 xl:grid-cols-[1.35fr_1fr]">
            <ReliabilityPanel
              title="Recent campaigns"
              description="Campaigns map to governed MIS tasks and plans; each row opens its run and gate chain."
              action={<Link to="campaigns" className="inline-flex items-center gap-1 text-[11px]" style={{ color: "var(--mis-cyan)" }}>All campaigns <ArrowRight size={11} /></Link>}
            >
              {overview.recent_campaigns.length === 0 ? (
                <ReliabilityEmptyState title="No campaigns recorded" detail="Run a deterministic campaign from the OpenCekura CLI, then refresh this workspace." />
              ) : (
                <div className="space-y-2">
                  {overview.recent_campaigns.map((campaign) => (
                    <Link
                      key={campaign.campaign_id}
                      to={`campaigns/${encodeURIComponent(campaign.campaign_id)}`}
                      className="flex items-center justify-between gap-3 rounded px-3 py-2.5 transition-opacity hover:opacity-80"
                      style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}
                    >
                      <div className="min-w-0">
                        <div className="flex items-center gap-2">
                          <Activity size={13} style={{ color: "var(--mis-cyan)" }} />
                          <span className="truncate text-xs font-medium" style={{ color: "var(--mis-text)" }}>{campaign.campaign_id}</span>
                        </div>
                        <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1">
                          <span className="text-[10px]" style={{ color: "var(--mis-muted)" }}>{campaign.run_count} runs</span>
                          <ReliabilityId>{campaign.agent_version_id}</ReliabilityId>
                        </div>
                      </div>
                      <ReliabilityStatus status={campaign.status} />
                    </Link>
                  ))}
                </div>
              )}
            </ReliabilityPanel>

            <ReliabilityPanel title="Latest release decisions" description="Deterministic policy output; no gate result is inferred by the UI.">
              {overview.recent_release_gates.length === 0 ? (
                <ReliabilityEmptyState title="No release decisions" detail="A gate appears after a campaign has enough deterministic evaluation evidence." />
              ) : (
                <div className="space-y-2">
                  {overview.recent_release_gates.map((gate) => (
                    <div key={gate.gate_id} className="rounded px-3 py-2.5" style={{ background: "var(--mis-surface2)", border: "1px solid var(--mis-border)" }}>
                      <div className="flex items-center justify-between gap-2">
                        <Link to={`campaigns/${encodeURIComponent(gate.campaign_id)}`} className="truncate text-xs font-medium hover:opacity-80" style={{ color: "var(--mis-text)" }}>{gate.campaign_id}</Link>
                        <ReliabilityStatus status={gate.decision} />
                      </div>
                      <div className="mt-2 flex flex-wrap gap-3 text-[10px]" style={{ color: "var(--mis-muted)" }}>
                        <span>{gate.blockers.length} blockers</span>
                        <span>{gate.warnings.length} warnings</span>
                        <span>{formatReliabilityDate(gate.created_at)}</span>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </ReliabilityPanel>
          </section>

          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
            <ReliabilityPanel title="Run outcomes">
              <div className="flex flex-wrap gap-2">
                {Object.entries(overview.run_status_counts).length > 0
                  ? Object.entries(overview.run_status_counts).map(([status, count]) => <ReliabilityStatus key={status} status={status} label={`${status.toUpperCase()} · ${count}`} />)
                  : <span className="text-xs" style={{ color: "var(--mis-muted)" }}>No run outcomes yet.</span>}
              </div>
            </ReliabilityPanel>
            <ReliabilityPanel title="Gate outcomes">
              <div className="flex flex-wrap gap-2">
                {Object.entries(overview.gate_decision_counts).length > 0
                  ? Object.entries(overview.gate_decision_counts).map(([status, count]) => <ReliabilityStatus key={status} status={status} label={`${status.toUpperCase()} · ${count}`} />)
                  : <span className="text-xs" style={{ color: "var(--mis-muted)" }}>No gate outcomes yet.</span>}
              </div>
            </ReliabilityPanel>
          </div>

          <div className="flex items-center gap-2 rounded-lg px-3 py-2 text-[10px]" style={{ background: "rgba(42,157,143,0.06)", border: "1px solid rgba(42,157,143,0.18)", color: "var(--mis-dim)" }}>
            <ShieldCheck size={12} style={{ color: "var(--mis-success)" }} />
            Overview counts are calculated from workspace-scoped Reliability tables backed by the existing MIS ledger.
          </div>
        </>
      ) : null}
    </ReliabilityPage>
  );
}
