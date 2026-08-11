import { Activity } from "lucide-react";
import { Link } from "react-router";
import { useLiveData } from "../../../data/liveApi";
import { loadReliabilityCampaigns } from "../../../data/reliabilityApi";
import {
  ReliabilityEmptyState,
  ReliabilityId,
  ReliabilityLoadingState,
  ReliabilityPage,
  ReliabilityRefreshButton,
  ReliabilityRow,
  ReliabilityStatus,
  ReliabilityTable,
  ReliabilityUnavailableState,
  formatReliabilityDate,
} from "./ReliabilityUi";

export function ReliabilityCampaigns() {
  const { data, loading, error, refresh } = useLiveData(loadReliabilityCampaigns, []);
  const campaigns = data?.campaigns;
  return (
    <ReliabilityPage
      title="Campaigns"
      description="Scenario-suite executions mapped to governed MIS Task, Plan and Run evidence."
      actions={<ReliabilityRefreshButton refresh={refresh} />}
    >
      {loading && !campaigns ? <ReliabilityLoadingState label="Loading campaigns…" /> : null}
      {error && !campaigns ? <ReliabilityUnavailableState refresh={refresh} /> : null}
      {campaigns?.length === 0 ? <ReliabilityEmptyState title="No campaigns recorded" detail="Run an appointment-agent baseline or candidate campaign, then refresh this ledger." /> : null}
      {campaigns && campaigns.length > 0 ? (
        <ReliabilityTable headers={["Campaign", "Status", "Runs", "Agent version", "Scenario suite", "MIS task", "Created"]} minWidth={1080}>
          {campaigns.map((campaign) => (
            <ReliabilityRow key={campaign.campaign_id}>
              <td className="px-4 py-3">
                <Link to={encodeURIComponent(campaign.campaign_id)} className="flex items-center gap-2 font-medium hover:opacity-80" style={{ color: "var(--mis-text)" }}>
                  <Activity size={13} style={{ color: "var(--mis-cyan)" }} />
                  {campaign.campaign_id}
                </Link>
              </td>
              <td className="px-4 py-3"><ReliabilityStatus status={campaign.status} /></td>
              <td className="px-4 py-3 tabular-nums">{campaign.run_count}</td>
              <td className="px-4 py-3"><ReliabilityId>{campaign.agent_version_id}</ReliabilityId></td>
              <td className="px-4 py-3"><ReliabilityId>{campaign.scenario_suite_id}</ReliabilityId></td>
              <td className="px-4 py-3"><ReliabilityId>{campaign.mis_task_id ?? "unmapped"}</ReliabilityId></td>
              <td className="px-4 py-3 text-[11px]">{formatReliabilityDate(campaign.created_at)}</td>
            </ReliabilityRow>
          ))}
        </ReliabilityTable>
      ) : null}
    </ReliabilityPage>
  );
}
