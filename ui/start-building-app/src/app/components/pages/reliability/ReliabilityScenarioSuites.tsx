import { FileCheck2 } from "lucide-react";
import { useLiveData } from "../../../data/liveApi";
import { loadReliabilityScenarioSuites } from "../../../data/reliabilityApi";
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

export function ReliabilityScenarioSuites() {
  const { data, loading, error, refresh } = useLiveData(loadReliabilityScenarioSuites, []);
  const suites = data?.scenario_suites;
  return (
    <ReliabilityPage
      title="Scenario Suites"
      description="Versioned YAML scenario contracts grouped into replayable deterministic suites."
      actions={<ReliabilityRefreshButton refresh={refresh} />}
    >
      {loading && !suites ? <ReliabilityLoadingState label="Loading scenario suites…" /> : null}
      {error && !suites ? <ReliabilityUnavailableState refresh={refresh} /> : null}
      {suites?.length === 0 ? <ReliabilityEmptyState title="No scenario suites" detail="Validate and run a Scenario v1 suite to record it in this workspace." /> : null}
      {suites && suites.length > 0 ? (
        <ReliabilityTable headers={["Suite", "Description", "Scenarios", "Schema", "Registered"]} minWidth={820}>
          {suites.map((suite) => (
            <ReliabilityRow key={suite.suite_id}>
              <td className="px-4 py-3">
                <div className="flex items-center gap-2 font-medium" style={{ color: "var(--mis-text)" }}><FileCheck2 size={13} style={{ color: "var(--mis-purple)" }} />{suite.name}</div>
                <div className="mt-1"><ReliabilityId>{suite.suite_id}</ReliabilityId></div>
              </td>
              <td className="max-w-lg px-4 py-3 text-[11px] leading-relaxed">{suite.description || "—"}</td>
              <td className="px-4 py-3 tabular-nums">{suite.scenario_count}</td>
              <td className="px-4 py-3">v{suite.schema_version}</td>
              <td className="px-4 py-3 text-[11px]">{formatReliabilityDate(suite.created_at)}</td>
            </ReliabilityRow>
          ))}
        </ReliabilityTable>
      ) : null}
    </ReliabilityPage>
  );
}
