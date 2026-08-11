import { Bot } from "lucide-react";
import { useLiveData } from "../../../data/liveApi";
import { loadReliabilityAgents } from "../../../data/reliabilityApi";
import {
  ReliabilityEmptyState,
  ReliabilityId,
  ReliabilityLoadingState,
  ReliabilityPage,
  ReliabilityRefreshButton,
  ReliabilityRow,
  ReliabilityTable,
  ReliabilityUnavailableState,
  formatReliabilityDate,
} from "./ReliabilityUi";

export function ReliabilityAgents() {
  const { data, loading, error, refresh } = useLiveData(loadReliabilityAgents, []);
  const agents = data?.agents;
  return (
    <ReliabilityPage
      title="Agents"
      description="Agents under test and their immutable adapter/configuration versions."
      actions={<ReliabilityRefreshButton refresh={refresh} />}
    >
      {loading && !agents ? <ReliabilityLoadingState label="Loading agents under test…" /> : null}
      {error && !agents ? <ReliabilityUnavailableState refresh={refresh} /> : null}
      {agents?.length === 0 ? <ReliabilityEmptyState title="No agents under test" detail="A campaign registers its agent and version before simulation begins." /> : null}
      {agents && agents.length > 0 ? (
        <ReliabilityTable headers={["Agent", "Description", "Versions", "Workspace", "Registered"]} minWidth={820}>
          {agents.map((agent) => (
            <ReliabilityRow key={agent.agent_id}>
              <td className="px-4 py-3">
                <div className="flex items-center gap-2 font-medium" style={{ color: "var(--mis-text)" }}><Bot size={13} style={{ color: "var(--mis-cyan)" }} />{agent.name}</div>
                <div className="mt-1"><ReliabilityId>{agent.agent_id}</ReliabilityId></div>
              </td>
              <td className="max-w-md px-4 py-3 text-[11px] leading-relaxed">{agent.description || "—"}</td>
              <td className="px-4 py-3 tabular-nums">{agent.version_count}</td>
              <td className="px-4 py-3"><ReliabilityId>{agent.workspace_id}</ReliabilityId></td>
              <td className="px-4 py-3 text-[11px]">{formatReliabilityDate(agent.created_at)}</td>
            </ReliabilityRow>
          ))}
        </ReliabilityTable>
      ) : null}
    </ReliabilityPage>
  );
}
