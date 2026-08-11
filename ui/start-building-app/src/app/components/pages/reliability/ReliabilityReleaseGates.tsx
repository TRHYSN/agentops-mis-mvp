import { CheckCircle2, ExternalLink, ShieldAlert } from "lucide-react";
import { Link } from "react-router";
import { useLiveData } from "../../../data/liveApi";
import { loadReliabilityReleaseGates } from "../../../data/reliabilityApi";
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

export function ReliabilityReleaseGates() {
  const { data, loading, error, refresh } = useLiveData(loadReliabilityReleaseGates, []);
  const gates = data?.release_gates;
  return (
    <ReliabilityPage
      title="Release Gates"
      description="Policy-versioned, explainable PASS/WARN/BLOCK decisions mapped to the MIS Approval ledger."
      actions={<ReliabilityRefreshButton refresh={refresh} />}
    >
      {loading && !gates ? <ReliabilityLoadingState label="Loading release-gate evidence…" /> : null}
      {error && !gates ? <ReliabilityUnavailableState refresh={refresh} /> : null}
      {gates?.length === 0 ? <ReliabilityEmptyState title="No release gates" detail="Evaluate a completed campaign to record a release decision." /> : null}
      {gates && gates.length > 0 ? (
        <div className="space-y-3">
          {gates.map((gate) => (
            <ReliabilityPanel
              key={gate.gate_id}
              title={`Gate · ${gate.gate_id}`}
              description={`${gate.policy_version} · ${formatReliabilityDate(gate.created_at)}`}
              action={<ReliabilityStatus status={gate.decision} />}
            >
              <div className="grid grid-cols-1 gap-4 xl:grid-cols-[240px_1.15fr_0.85fr]">
                <div className="space-y-3 text-[10px]">
                  <div className="flex items-center gap-2">
                    {gate.decision === "pass" ? <CheckCircle2 size={16} style={{ color: "var(--mis-success)" }} /> : <ShieldAlert size={16} style={{ color: gate.decision === "warn" ? "#FBBF24" : "#F87171" }} />}
                    <span className="font-medium" style={{ color: "var(--mis-text)" }}>{gate.decision === "block" ? "Release blocked" : gate.decision === "warn" ? "Release needs review" : "Release eligible"}</span>
                  </div>
                  <div><span style={{ color: "var(--mis-muted)" }}>Campaign</span><div className="mt-1"><Link to={`/workspace/reliability/campaigns/${encodeURIComponent(gate.campaign_id)}`} className="inline-flex items-center gap-1 hover:opacity-80" style={{ color: "var(--mis-cyan)" }}>{gate.campaign_id}<ExternalLink size={10} /></Link></div></div>
                  <div><span style={{ color: "var(--mis-muted)" }}>Baseline</span><div className="mt-1"><ReliabilityId>{gate.baseline_campaign_id ?? "none"}</ReliabilityId></div></div>
                  <div><span style={{ color: "var(--mis-muted)" }}>MIS Approval</span><div className="mt-1"><ReliabilityId>{gate.mis_approval_id ?? "unmapped"}</ReliabilityId></div></div>
                  <div><span style={{ color: "var(--mis-muted)" }}>Evidence</span><div className="mt-1 space-y-1">{gate.evidence_refs.map((ref) => <div key={ref}><ReliabilityId>{ref}</ReliabilityId></div>)}</div></div>
                </div>

                <div className="space-y-3">
                  <div>
                    <div className="mb-1.5 text-[10px] font-semibold uppercase tracking-wide" style={{ color: gate.blockers.length > 0 ? "#F87171" : "var(--mis-muted)" }}>Blockers ({gate.blockers.length})</div>
                    {gate.blockers.length === 0 ? <p className="text-[11px]" style={{ color: "var(--mis-success)" }}>No blocking rule fired.</p> : <ul className="space-y-2">{gate.blockers.map((fact, index) => <li key={`${fact.rule_id}-${index}`} className="rounded p-2.5" style={{ background: "rgba(248,113,113,0.06)", border: "1px solid rgba(248,113,113,0.16)" }}><div className="text-[11px] font-medium" style={{ color: "var(--mis-text)" }}>{fact.message}</div><div className="mt-1"><ReliabilityId>{fact.rule_id}{fact.scenario_id ? ` · ${fact.scenario_id}` : ""}</ReliabilityId></div></li>)}</ul>}
                  </div>
                  <div>
                    <div className="mb-1.5 text-[10px] font-semibold uppercase tracking-wide" style={{ color: gate.warnings.length > 0 ? "#FBBF24" : "var(--mis-muted)" }}>Warnings ({gate.warnings.length})</div>
                    {gate.warnings.length === 0 ? <p className="text-[11px]" style={{ color: "var(--mis-muted)" }}>No warning rule fired.</p> : <ul className="space-y-2">{gate.warnings.map((fact, index) => <li key={`${fact.rule_id}-${index}`} className="rounded p-2.5 text-[11px]" style={{ background: "rgba(251,191,36,0.06)", color: "var(--mis-dim)" }}>{fact.message}</li>)}</ul>}
                  </div>
                </div>

                <div>
                  <div className="mb-1.5 text-[10px] font-semibold uppercase tracking-wide" style={{ color: "var(--mis-muted)" }}>Measured metrics</div>
                  <ReliabilityJsonView value={gate.metrics} maxLength={2400} />
                </div>
              </div>
            </ReliabilityPanel>
          ))}
        </div>
      ) : null}
    </ReliabilityPage>
  );
}
