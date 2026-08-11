import { ArrowLeft, CheckCircle2, ShieldAlert } from "lucide-react";
import { Link, useParams } from "react-router";
import { useLiveData } from "../../../data/liveApi";
import {
  loadReliabilityCampaign,
  loadReliabilityCampaignGates,
  loadReliabilityCampaignRuns,
} from "../../../data/reliabilityApi";
import {
  ReliabilityEmptyState,
  ReliabilityId,
  ReliabilityKeyValue,
  ReliabilityLoadingState,
  ReliabilityMetric,
  ReliabilityPage,
  ReliabilityPanel,
  ReliabilityRefreshButton,
  ReliabilityRow,
  ReliabilityStatus,
  ReliabilityTable,
  ReliabilityUnavailableState,
  formatReliabilityDate,
} from "./ReliabilityUi";

export function ReliabilityCampaignDetail() {
  const { id = "" } = useParams();
  const { data, loading, error, refresh } = useLiveData(async () => {
    if (!id) throw new Error("campaign_id_required");
    const [campaignResponse, runResponse, gateResponse] = await Promise.all([
      loadReliabilityCampaign(id),
      loadReliabilityCampaignRuns(id),
      loadReliabilityCampaignGates(id),
    ]);
    return {
      campaign: campaignResponse.campaign,
      runs: runResponse.runs,
      gates: gateResponse.release_gates,
    };
  }, [id]);

  const campaign = data?.campaign;
  const runs = data?.runs ?? [];
  const gates = data?.gates ?? [];
  const latestGate = gates[0];
  const passed = runs.filter((run) => run.status === "pass").length;
  const failed = runs.filter((run) => run.status === "fail").length;
  const errors = runs.filter((run) => run.status === "error").length;

  return (
    <ReliabilityPage
      title={campaign ? `Campaign · ${campaign.campaign_id}` : "Campaign detail"}
      description="Governed campaign identity, deterministic run outcomes and the derived release-gate decision."
      actions={<ReliabilityRefreshButton refresh={refresh} />}
    >
      <Link to="/workspace/reliability/campaigns" className="inline-flex items-center gap-1 text-[11px] hover:opacity-80" style={{ color: "var(--mis-cyan)" }}>
        <ArrowLeft size={12} /> Back to campaigns
      </Link>
      {loading && !campaign ? <ReliabilityLoadingState label="Loading campaign evidence…" /> : null}
      {error && !campaign ? <ReliabilityUnavailableState refresh={refresh} /> : null}
      {campaign ? (
        <>
          <section className="grid grid-cols-2 gap-3 lg:grid-cols-5">
            <ReliabilityMetric label="Campaign" value={<ReliabilityStatus status={campaign.status} />} />
            <ReliabilityMetric label="Runs" value={runs.length} />
            <ReliabilityMetric label="Passed" value={passed} status={failed + errors === 0 && runs.length > 0 ? "pass" : undefined} />
            <ReliabilityMetric label="Failed" value={failed} status={failed > 0 ? "fail" : "pass"} />
            <ReliabilityMetric label="Release gate" value={latestGate ? <ReliabilityStatus status={latestGate.decision} /> : "Pending"} />
          </section>

          <section className="grid grid-cols-1 gap-3 xl:grid-cols-[1fr_1.4fr]">
            <ReliabilityPanel title="Campaign authority mapping" description="Vertical identifiers remain hard-bound to the existing MIS task and plan ledgers.">
              <dl>
                <ReliabilityKeyValue label="Campaign ID" value={<ReliabilityId>{campaign.campaign_id}</ReliabilityId>} />
                <ReliabilityKeyValue label="Agent version" value={<ReliabilityId>{campaign.agent_version_id}</ReliabilityId>} />
                <ReliabilityKeyValue label="Scenario suite" value={<ReliabilityId>{campaign.scenario_suite_id}</ReliabilityId>} />
                <ReliabilityKeyValue label="MIS Task" value={campaign.mis_task_id ? <Link to={`/admin/tasks/${encodeURIComponent(campaign.mis_task_id)}`} style={{ color: "var(--mis-cyan)" }}><ReliabilityId>{campaign.mis_task_id}</ReliabilityId></Link> : "Unmapped"} />
                <ReliabilityKeyValue label="MIS Plan" value={<ReliabilityId>{campaign.mis_plan_id ?? "unmapped"}</ReliabilityId>} />
                <ReliabilityKeyValue label="Created" value={formatReliabilityDate(campaign.created_at)} />
              </dl>
            </ReliabilityPanel>

            <ReliabilityPanel title="Release decision" description="Blockers and warnings come directly from the deterministic gate record.">
              {!latestGate ? (
                <ReliabilityEmptyState title="No release gate recorded" detail="Complete gate evaluation for this campaign to create a decision." />
              ) : (
                <div className="space-y-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex items-center gap-2">
                      {latestGate.decision === "pass" ? <CheckCircle2 size={16} style={{ color: "var(--mis-success)" }} /> : <ShieldAlert size={16} style={{ color: "#F87171" }} />}
                      <ReliabilityStatus status={latestGate.decision} />
                    </div>
                    <ReliabilityId>{latestGate.policy_version}</ReliabilityId>
                  </div>
                  {latestGate.blockers.length > 0 ? (
                    <div>
                      <div className="mb-1.5 text-[10px] font-semibold uppercase tracking-wide" style={{ color: "#F87171" }}>Blockers</div>
                      <ul className="space-y-1.5">
                        {latestGate.blockers.map((fact, index) => (
                          <li key={`${fact.rule_id}-${index}`} className="rounded px-3 py-2 text-[11px] leading-relaxed" style={{ background: "rgba(248,113,113,0.06)", border: "1px solid rgba(248,113,113,0.16)", color: "var(--mis-dim)" }}>
                            <span className="font-medium" style={{ color: "var(--mis-text)" }}>{fact.message}</span>
                            {fact.scenario_id ? <div className="mt-1"><ReliabilityId>{fact.scenario_id}</ReliabilityId></div> : null}
                          </li>
                        ))}
                      </ul>
                    </div>
                  ) : null}
                  {latestGate.warnings.length > 0 ? (
                    <div>
                      <div className="mb-1.5 text-[10px] font-semibold uppercase tracking-wide" style={{ color: "#FBBF24" }}>Warnings</div>
                      <ul className="space-y-1.5">
                        {latestGate.warnings.map((fact, index) => <li key={`${fact.rule_id}-${index}`} className="rounded px-3 py-2 text-[11px]" style={{ background: "rgba(251,191,36,0.06)", color: "var(--mis-dim)" }}>{fact.message}</li>)}
                      </ul>
                    </div>
                  ) : null}
                  {latestGate.blockers.length === 0 && latestGate.warnings.length === 0 ? <p className="text-xs" style={{ color: "var(--mis-success)" }}>All configured release checks passed.</p> : null}
                </div>
              )}
            </ReliabilityPanel>
          </section>

          {runs.length === 0 ? (
            <ReliabilityEmptyState title="No runs in this campaign" detail="The campaign exists, but no ConversationRun evidence is available." />
          ) : (
            <ReliabilityTable headers={["Run", "Scenario", "Outcome", "Turns", "Tools", "Evaluators", "MIS run", "Created"]} minWidth={1100}>
              {runs.map((run) => (
                <ReliabilityRow key={run.run_id}>
                  <td className="px-4 py-3"><Link to={`/workspace/reliability/runs/${encodeURIComponent(run.run_id)}`} className="font-medium hover:opacity-80" style={{ color: "var(--mis-cyan)" }}>{run.run_id}</Link></td>
                  <td className="px-4 py-3"><ReliabilityId>{run.scenario_id}</ReliabilityId></td>
                  <td className="px-4 py-3"><ReliabilityStatus status={run.status} /></td>
                  <td className="px-4 py-3 tabular-nums">{run.turn_count ?? 0}</td>
                  <td className="px-4 py-3 tabular-nums">{run.tool_call_count ?? 0}</td>
                  <td className="px-4 py-3 tabular-nums">{run.evaluation_count ?? 0}</td>
                  <td className="px-4 py-3"><ReliabilityId>{run.mis_run_id ?? "unmapped"}</ReliabilityId></td>
                  <td className="px-4 py-3 text-[11px]">{formatReliabilityDate(run.created_at)}</td>
                </ReliabilityRow>
              ))}
            </ReliabilityTable>
          )}
        </>
      ) : null}
    </ReliabilityPage>
  );
}
