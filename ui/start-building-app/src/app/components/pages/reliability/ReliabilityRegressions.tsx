import { ExternalLink, History, RotateCcw } from "lucide-react";
import { Link } from "react-router";
import { useLiveData } from "../../../data/liveApi";
import { loadReliabilityRegressions } from "../../../data/reliabilityApi";
import {
  ReliabilityEmptyState,
  ReliabilityId,
  ReliabilityJsonView,
  ReliabilityLoadingState,
  ReliabilityPage,
  ReliabilityPanel,
  ReliabilityRefreshButton,
  ReliabilityStatus,
  ReliabilityUnavailableState,
  formatReliabilityDate,
} from "./ReliabilityUi";

export function ReliabilityRegressions() {
  const { data, loading, error, refresh } = useLiveData(loadReliabilityRegressions, []);
  const regressions = data?.regressions;
  return (
    <ReliabilityPage
      title="Regression Suite"
      description="Normalized FailureCase evidence promoted into replayable RegressionCase inputs for the next campaign."
      actions={<ReliabilityRefreshButton refresh={refresh} />}
    >
      {loading && !regressions ? <ReliabilityLoadingState label="Loading regression cases…" /> : null}
      {error && !regressions ? <ReliabilityUnavailableState refresh={refresh} /> : null}
      {regressions?.length === 0 ? <ReliabilityEmptyState title="No regression cases" detail="Regression cases are generated from reviewed deterministic failures." /> : null}
      {regressions && regressions.length > 0 ? (
        <div className="space-y-3">
          {regressions.map((regression) => (
            <ReliabilityPanel
              key={regression.regression_id}
              title={regression.name}
              description={`${regression.reason_code} · ${regression.evaluator_id}`}
              action={<ReliabilityStatus status="ready" label="REPLAY READY" />}
            >
              <div className="grid grid-cols-1 gap-3 xl:grid-cols-[240px_1fr_1fr]">
                <div className="space-y-2 text-[10px]">
                  <div className="flex items-center gap-2"><RotateCcw size={13} style={{ color: "var(--mis-purple)" }} /><span className="font-medium" style={{ color: "var(--mis-text)" }}>Replay provenance</span></div>
                  <div><span style={{ color: "var(--mis-muted)" }}>Regression</span><div className="mt-0.5"><ReliabilityId>{regression.regression_id}</ReliabilityId></div></div>
                  <div><span style={{ color: "var(--mis-muted)" }}>Failure case</span><div className="mt-0.5"><ReliabilityId>{regression.failure_case_id}</ReliabilityId></div></div>
                  <div><span style={{ color: "var(--mis-muted)" }}>Scenario</span><div className="mt-0.5"><ReliabilityId>{regression.scenario_id}</ReliabilityId></div></div>
                  <Link to={`/workspace/reliability/runs/${encodeURIComponent(regression.source_run_id)}`} className="inline-flex items-center gap-1 hover:opacity-80" style={{ color: "var(--mis-cyan)" }}>Source run <ExternalLink size={10} /></Link>
                  <div><span style={{ color: "var(--mis-muted)" }}>MIS Memory</span><div className="mt-0.5"><ReliabilityId>{regression.mis_memory_id ?? "candidate mapping unavailable"}</ReliabilityId></div></div>
                  <div className="flex items-center gap-1" style={{ color: "var(--mis-muted)" }}><History size={10} />{formatReliabilityDate(regression.created_at)}</div>
                </div>
                <div className="space-y-3">
                  <div>
                    <div className="mb-1.5 text-[10px] font-medium uppercase tracking-wide" style={{ color: "var(--mis-muted)" }}>Original failing input</div>
                    <ReliabilityJsonView value={regression.original_input} maxLength={1600} />
                  </div>
                  <div>
                    <div className="mb-1.5 text-[10px] font-medium uppercase tracking-wide" style={{ color: "var(--mis-muted)" }}>Evidence references</div>
                    {regression.evidence_refs.map((ref) => <div key={ref}><ReliabilityId>{ref}</ReliabilityId></div>)}
                  </div>
                </div>
                <div className="grid grid-cols-1 gap-3 lg:grid-cols-2 xl:grid-cols-1">
                  <div>
                    <div className="mb-1.5 text-[10px] font-medium uppercase tracking-wide" style={{ color: "var(--mis-success)" }}>Expected state</div>
                    <ReliabilityJsonView value={regression.expected} maxLength={1000} />
                  </div>
                  <div>
                    <div className="mb-1.5 text-[10px] font-medium uppercase tracking-wide" style={{ color: "#F87171" }}>Observed state</div>
                    <ReliabilityJsonView value={regression.observed} maxLength={1000} />
                  </div>
                </div>
              </div>
            </ReliabilityPanel>
          ))}
        </div>
      ) : null}
    </ReliabilityPage>
  );
}
