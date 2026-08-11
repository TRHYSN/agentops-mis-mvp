from __future__ import annotations

from datetime import datetime, timezone

import pytest

from open_cekura.domain.enums import EvaluationStatus, GateDecision
from open_cekura.evaluation.aggregation import (
    EvaluationResultGroup,
    RunMetricFacts,
    aggregate_campaign_metrics,
)
from open_cekura.release_gate.gate import render_gate_decision
from open_cekura.release_gate.policy import (
    CampaignGateInput,
    ReleaseGateError,
    evaluate_release_gate,
)
from open_cekura.tests.integration.test_simulation_evaluation import evaluate_profile
from open_cekura.simulation.mock_agent import MockAgentConfig


NOW = datetime(2026, 8, 11, 17, 0, tzinfo=timezone.utc)


def gate_input(config: MockAgentConfig, profile: str) -> CampaignGateInput:
    rows = evaluate_profile(config, profile)
    groups: list[EvaluationResultGroup] = []
    facts: list[RunMetricFacts] = []
    scenario_by_run: dict[str, str] = {}
    evaluator_versions: set[str] = set()
    for index, (scenario_id, results) in enumerate(sorted(rows.items())):
        run_id = results[0].run_id
        groups.append(
            EvaluationResultGroup(
                run_id=run_id,
                deterministic_results=results,
                judge_results=[],
            )
        )
        facts.append(
            RunMetricFacts(
                run_id=run_id,
                turn_count=4 + index % 3,
                latency_ms=100 + index * 10,
            )
        )
        scenario_by_run[run_id] = scenario_id
        evaluator_versions.update(result.evaluator_id for result in results)
    return CampaignGateInput(
        campaign_id=f"occampaign_{profile}",
        metrics=aggregate_campaign_metrics(groups, facts),
        scenario_by_run=scenario_by_run,
        scenario_ids=sorted(rows),
        evaluator_versions=sorted(evaluator_versions),
        evidence_verified=True,
        evidence_refs=[f"artifact:{profile}/campaign_summary.json"],
    )


def test_public_baseline_blocks_and_candidate_passes_from_observed_facts() -> None:
    baseline = gate_input(MockAgentConfig.baseline(), "baseline")
    candidate = gate_input(MockAgentConfig.candidate(), "candidate")

    baseline_decision = evaluate_release_gate(baseline, created_at=NOW)
    candidate_decision = evaluate_release_gate(
        candidate,
        baseline=baseline,
        created_at=NOW,
    )

    assert baseline_decision.decision is GateDecision.BLOCK
    assert {
        blocker.rule_id for blocker in baseline_decision.blockers
    } >= {
        "zero_tolerance.duplicate_mutation.v1",
        "zero_tolerance.confirmation_before_mutation.v1",
    }
    assert candidate_decision.decision is GateDecision.PASS
    assert candidate_decision.blockers == []
    assert candidate_decision.warnings == []
    assert candidate_decision.baseline_campaign_id == baseline.campaign_id
    assert "FAIL\nBlockers:" in render_gate_decision(baseline_decision)
    assert "appointment.duplicate_request" in render_gate_decision(baseline_decision)
    assert render_gate_decision(candidate_decision) == "PASS"


def test_evaluator_error_and_unverified_evidence_can_never_pass() -> None:
    candidate = gate_input(MockAgentConfig.candidate(), "candidate")
    first_group = candidate.metrics.result_groups[0]
    first_result = first_group.deterministic_results[0]
    errored = first_result.model_copy(
        update={
            "status": EvaluationStatus.ERROR,
            "score": None,
            "threshold": None,
            "reason_codes": ["evaluator_error"],
        }
    )
    groups = [
        first_group.model_copy(
            update={
                "deterministic_results": [
                    errored,
                    *first_group.deterministic_results[1:],
                ]
            }
        ),
        *candidate.metrics.result_groups[1:],
    ]
    facts = [
        RunMetricFacts(
            run_id=group.run_id,
            turn_count=4,
            latency_ms=100,
        )
        for group in groups
    ]
    unsafe = candidate.model_copy(
        update={
            "metrics": aggregate_campaign_metrics(groups, facts),
            "evidence_verified": False,
        }
    )

    decision = evaluate_release_gate(unsafe, created_at=NOW)

    assert decision.decision is GateDecision.BLOCK
    assert {fact.rule_id for fact in decision.blockers} >= {
        "deterministic_evaluator.error_rate.v1",
        "evidence.verification_required.v1",
    }


def test_comparison_warns_on_turn_and_small_timeout_regressions() -> None:
    baseline = gate_input(MockAgentConfig.candidate(), "baseline_clean")
    candidate = gate_input(MockAgentConfig.candidate(), "candidate_slow")
    candidate_metrics = candidate.metrics.model_copy(
        update={
            "median_turns": baseline.metrics.median_turns * 1.25,
            "timeout_rate": 0.02,
        }
    )
    baseline_metrics = baseline.metrics.model_copy(update={"timeout_rate": 0.0})

    decision = evaluate_release_gate(
        candidate.model_copy(update={"metrics": candidate_metrics}),
        baseline=baseline.model_copy(update={"metrics": baseline_metrics}),
        created_at=NOW,
    )

    assert decision.decision is GateDecision.WARN
    assert {warning.rule_id for warning in decision.warnings} == {
        "regression.median_turns.v1",
        "regression.timeout_rate.v1",
    }


def test_incompatible_comparison_contracts_fail_closed() -> None:
    baseline = gate_input(MockAgentConfig.candidate(), "baseline_clean")
    candidate = gate_input(MockAgentConfig.candidate(), "candidate_clean")
    incompatible = candidate.model_copy(
        update={"scenario_ids": [*candidate.scenario_ids, "appointment.unreviewed"]}
    )

    with pytest.raises(ReleaseGateError, match="scenario sets"):
        evaluate_release_gate(incompatible, baseline=baseline, created_at=NOW)


def test_forbidden_call_and_more_than_five_point_success_regression_block() -> None:
    baseline = gate_input(MockAgentConfig.candidate(), "baseline_clean")
    candidate = gate_input(MockAgentConfig.candidate(), "candidate_regressed")
    first_group = candidate.metrics.result_groups[0]
    changed_results = []
    for result in first_group.deterministic_results:
        if result.evaluator_id == "task_success.v1":
            changed_results.append(
                result.model_copy(
                    update={
                        "status": EvaluationStatus.FAIL,
                        "score": 0.0,
                        "reason_codes": ["task_unsuccessful"],
                    }
                )
            )
        elif result.evaluator_id == "forbidden_tool_calls.v1":
            changed_results.append(
                result.model_copy(
                    update={
                        "status": EvaluationStatus.FAIL,
                        "score": 0.0,
                        "reason_codes": ["forbidden_tool_call_observed"],
                        "metadata": {
                            "violations": [
                                {
                                    "tool_name": "cancel_booking",
                                    "tool_call_id": "octool_forbidden",
                                }
                            ]
                        },
                    }
                )
            )
        else:
            changed_results.append(result)
    groups = [
        first_group.model_copy(update={"deterministic_results": changed_results}),
        *candidate.metrics.result_groups[1:],
    ]
    facts = [
        RunMetricFacts(run_id=group.run_id, turn_count=4, latency_ms=100)
        for group in groups
    ]
    regressed = candidate.model_copy(
        update={"metrics": aggregate_campaign_metrics(groups, facts)}
    )

    decision = evaluate_release_gate(
        regressed,
        baseline=baseline,
        created_at=NOW,
    )

    assert decision.decision is GateDecision.BLOCK
    assert {blocker.rule_id for blocker in decision.blockers} >= {
        "zero_tolerance.forbidden_tool_calls.v1",
        "regression.task_success.v1",
    }
    rendered = render_gate_decision(decision)
    assert "forbidden tool call (cancel_booking)" in rendered
    assert "task success: 100.0% -> 90.0%" in rendered
