import { AlertTriangle, ExternalLink } from "lucide-react";
import { Link } from "react-router";
import { useLiveData } from "../../../data/liveApi";
import { loadReliabilityFailures } from "../../../data/reliabilityApi";
import {
  ReliabilityEmptyState,
  ReliabilityId,
  ReliabilityJsonView,
  ReliabilityLoadingState,
  ReliabilityPage,
  ReliabilityPanel,
  ReliabilityRefreshButton,
  ReliabilityUnavailableState,
  formatReliabilityDate,
} from "./ReliabilityUi";

export function ReliabilityFailures() {
  const { data, loading, error, refresh } = useLiveData(loadReliabilityFailures, []);
  const failures = data?.failures;
  return (
    <ReliabilityPage
      title="Failures"
      description="Explainable deterministic failures with expected/observed state and source-run evidence."
      actions={<ReliabilityRefreshButton refresh={refresh} />}
    >
      {loading && !failures ? <ReliabilityLoadingState label="Loading failure evidence…" /> : null}
      {error && !failures ? <ReliabilityUnavailableState refresh={refresh} /> : null}
      {failures?.length === 0 ? <ReliabilityEmptyState title="No failures recorded" detail="This workspace currently has no deterministic evaluator failures." /> : null}
      {failures && failures.length > 0 ? (
        <div className="space-y-3">
          {failures.map((failure) => (
            <ReliabilityPanel
              key={failure.failure_id}
              title={failure.reason_code}
              description={`Scenario ${failure.scenario_id}`}
              action={(
                <Link to={`/workspace/reliability/runs/${encodeURIComponent(failure.run_id)}`} className="inline-flex items-center gap-1 text-[11px] hover:opacity-80" style={{ color: "var(--mis-cyan)" }}>
                  Open run <ExternalLink size={11} />
                </Link>
              )}
            >
              <div className="grid grid-cols-1 gap-3 xl:grid-cols-[220px_1fr_1fr]">
                <div className="space-y-2 text-[10px]">
                  <div className="flex items-center gap-2"><AlertTriangle size={13} style={{ color: "#F87171" }} /><span className="font-medium" style={{ color: "var(--mis-text)" }}>Deterministic failure</span></div>
                  <div><span style={{ color: "var(--mis-muted)" }}>Failure</span><div className="mt-0.5"><ReliabilityId>{failure.failure_id}</ReliabilityId></div></div>
                  <div><span style={{ color: "var(--mis-muted)" }}>Evaluation</span><div className="mt-0.5"><ReliabilityId>{failure.evaluation_result_id}</ReliabilityId></div></div>
                  <div><span style={{ color: "var(--mis-muted)" }}>Recorded</span><div className="mt-0.5" style={{ color: "var(--mis-dim)" }}>{formatReliabilityDate(failure.created_at)}</div></div>
                  {failure.evidence_refs.map((ref) => <div key={ref}><ReliabilityId>{ref}</ReliabilityId></div>)}
                </div>
                <div>
                  <div className="mb-1.5 text-[10px] font-medium uppercase tracking-wide" style={{ color: "var(--mis-muted)" }}>Expected state</div>
                  <ReliabilityJsonView value={failure.expected} maxLength={1800} />
                </div>
                <div>
                  <div className="mb-1.5 text-[10px] font-medium uppercase tracking-wide" style={{ color: "#F87171" }}>Observed state</div>
                  <ReliabilityJsonView value={failure.observed} maxLength={1800} />
                </div>
              </div>
            </ReliabilityPanel>
          ))}
        </div>
      ) : null}
    </ReliabilityPage>
  );
}
