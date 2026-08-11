from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

from open_cekura.campaigns.runner import execute_mock_campaign
from open_cekura.domain.enums import CampaignStatus, EvaluationStatus, GateDecision
from open_cekura.release_gate.policy import evaluate_release_gate
from open_cekura.simulation.mock_agent import MockAgentConfig


REPO_ROOT = Path(__file__).resolve().parents[3]
SCENARIO_SUITE = REPO_ROOT / "examples" / "open-cekura" / "scenarios"
NOW = datetime(2026, 8, 11, 13, 0, tzinfo=timezone.utc)


def run_campaign(config: MockAgentConfig, *, campaign_id: str, version: str):
    return asyncio.run(
        execute_mock_campaign(
            suite_path=SCENARIO_SUITE,
            config=config,
            version=version,
            campaign_id=campaign_id,
            workspace_id="local-demo",
            created_at=NOW,
        )
    )


def test_campaign_closes_simulation_evaluation_failure_and_regression_loop() -> None:
    baseline = run_campaign(
        MockAgentConfig.baseline(),
        campaign_id="occampaign_name_says_candidate",
        version="baseline",
    )

    assert baseline.campaign.status is CampaignStatus.COMPLETED
    assert len(baseline.records) == 10
    assert baseline.metrics.run_count == 10
    assert all(len(record.evaluations) == 8 for record in baseline.records)
    assert all(record.scenario.id == record.simulation.run.scenario_id for record in baseline.records)
    assert all(
        regression.source_run_id == record.simulation.run.id
        for record in baseline.records
        for regression in record.regressions
    )
    assert {record.scenario.id for record in baseline.records if record.failures} == {
        "appointment.agent_claims_success_without_mutation",
        "appointment.duplicate_request",
        "appointment.mutation_before_confirmation",
    }
    assert baseline.failures
    assert baseline.regressions


def test_baseline_blocks_and_candidate_passes_from_observed_facts_not_ids() -> None:
    baseline = run_campaign(
        MockAgentConfig.baseline(),
        campaign_id="occampaign_name_says_candidate",
        version="baseline",
    )
    candidate = run_campaign(
        MockAgentConfig.candidate(),
        campaign_id="occampaign_name_says_baseline",
        version="candidate",
    )

    assert all(
        result.status is EvaluationStatus.PASS
        for record in candidate.records
        for result in record.evaluations
    )

    baseline_gate = evaluate_release_gate(
        baseline.gate_input(evidence_verified=True),
        created_at=NOW,
    )
    candidate_gate = evaluate_release_gate(
        candidate.gate_input(evidence_verified=True),
        baseline=baseline.gate_input(evidence_verified=True),
        created_at=NOW,
    )

    assert baseline_gate.decision is GateDecision.BLOCK
    assert {fact.rule_id for fact in baseline_gate.blockers} >= {
        "zero_tolerance.duplicate_mutation.v1",
        "zero_tolerance.confirmation_before_mutation.v1",
    }
    assert candidate_gate.decision is GateDecision.PASS
    assert candidate_gate.baseline_campaign_id == baseline.campaign.id


def test_campaign_ids_scope_run_identity_without_changing_semantic_outcome() -> None:
    first = run_campaign(
        MockAgentConfig.candidate(),
        campaign_id="occampaign_first",
        version="candidate",
    )
    replay = run_campaign(
        MockAgentConfig.candidate(),
        campaign_id="occampaign_replay",
        version="candidate",
    )

    assert [record.scenario.id for record in first.records] == [
        record.scenario.id for record in replay.records
    ]
    assert [
        [result.status for result in record.evaluations] for record in first.records
    ] == [
        [result.status for result in record.evaluations] for record in replay.records
    ]
    assert {record.simulation.run.id for record in first.records}.isdisjoint(
        {record.simulation.run.id for record in replay.records}
    )
