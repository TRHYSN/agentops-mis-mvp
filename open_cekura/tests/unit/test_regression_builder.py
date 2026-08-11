from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from open_cekura.domain.enums import AdapterKind, EvaluationStatus
from open_cekura.domain.models import AgentVersion, EvaluationResult, FailureCase
from open_cekura.evaluation.base import EvaluationContext
from open_cekura.regression.builder import (
    build_failure_cases,
    build_regression_case,
    replay_regression,
)
from open_cekura.regression.clustering import cluster_failures
from open_cekura.scenarios.schema import ScenarioDefinition
from open_cekura.simulation.mock_agent import MockAgentAdapter, MockAgentConfig


NOW = datetime(2026, 8, 11, 15, 0, tzinfo=timezone.utc)
RUN_ID = "ocrun_regression_source"


def scenario() -> ScenarioDefinition:
    return ScenarioDefinition.model_validate(
        {
            "schema_version": 1,
            "id": "appointment.false-success-regression",
            "name": "False success regression",
            "persona": {"language": "en-US", "tone": "direct", "verbosity": "short"},
            "initial_message": "Move booking-123 to August 20 at 10 AM.",
            "goal": {
                "type": "reschedule",
                "booking_id": "booking-123",
                "requested_slot": "2026-08-20T10:00:00Z",
            },
            "challenges": [{"agent_claims_success_without_mutation": True}],
            "expectations": {
                "required_tool_calls": ["lookup_booking", "update_booking"],
                "forbidden_tool_calls": [],
                "must_confirm_before_mutation": True,
                "final_state": {
                    "booking_id": "booking-123",
                    "slot": "2026-08-20T10:00:00Z",
                    "booking_updated": True,
                },
            },
        }
    )


def evaluation(
    evaluation_id: str,
    evaluator_id: str,
    status: EvaluationStatus,
    reason_codes: list[str],
) -> EvaluationResult:
    scored = status in {
        EvaluationStatus.PASS,
        EvaluationStatus.FAIL,
        EvaluationStatus.WARN,
    }
    return EvaluationResult(
        schema_version=1,
        id=evaluation_id,
        run_id=RUN_ID,
        evaluator_id=evaluator_id,
        status=status,
        score=0.0 if scored and status is not EvaluationStatus.PASS else (1.0 if scored else None),
        threshold=1.0 if scored else None,
        reason_codes=reason_codes,
        evidence_refs=["final_state:/booking_updated"],
        metadata={"mismatches": [{"path": "/booking_updated", "expected": True, "observed": False}]},
        mis_evaluation_id=None,
        created_at=NOW,
    )


def context() -> EvaluationContext:
    return EvaluationContext(
        scenario=scenario(),
        run_id=RUN_ID,
        turns=[],
        tool_calls=[],
        initial_state={"booking_id": "booking-123", "booking_updated": False},
        final_state={
            "booking_id": "booking-123",
            "slot": "old",
            "booking_updated": False,
        },
        agent_claimed_success=True,
        timeout_observations=[],
        evaluated_at=NOW,
    )


def test_failed_and_error_evaluations_become_explainable_failure_cases() -> None:
    failed = evaluation(
        "evr_false_success",
        "task_success.v1",
        EvaluationStatus.FAIL,
        ["agent_claims_success_without_state"],
    )
    errored = evaluation(
        "evr_internal_error",
        "custom_rule.v1",
        EvaluationStatus.ERROR,
        ["evaluator_error"],
    )
    passed = evaluation(
        "evr_passed",
        "required_tool_calls.v1",
        EvaluationStatus.PASS,
        [],
    )

    cases = build_failure_cases(context(), [failed, errored, passed])

    assert [case.reason_code for case in cases] == [
        "agent_claims_success_without_state",
        "evaluator_error",
    ]
    first = cases[0]
    assert first.run_id == RUN_ID
    assert first.scenario_id == scenario().id
    assert first.evaluation_result_id == failed.id
    assert first.expected == {
        "evaluation_status": "pass",
        "final_state": scenario().expectations.final_state,
    }
    assert first.observed["evaluation_status"] == "fail"
    assert first.observed["final_state"] == context().final_state
    assert f"evaluation:{failed.id}" in first.evidence_refs
    assert [case.canonical_json_bytes() for case in cases] == [
        case.canonical_json_bytes()
        for case in build_failure_cases(context(), [failed, errored, passed])
    ]


def test_failure_review_builds_a_complete_replayable_regression_case() -> None:
    result = evaluation(
        "evr_false_success",
        "task_success.v1",
        EvaluationStatus.FAIL,
        ["agent_claims_success_without_state"],
    )
    failure = build_failure_cases(context(), [result])[0]

    regression = build_regression_case(
        failure=failure,
        scenario=scenario(),
        evaluation=result,
        created_at=NOW,
        mis_memory_id="mem_false_success",
    )

    assert regression.failure_case_id == failure.id
    assert regression.scenario_id == scenario().id
    assert regression.source_run_id == RUN_ID
    assert regression.evaluator_id == "task_success.v1"
    assert regression.reason_code == "agent_claims_success_without_state"
    assert regression.original_input == {
        "challenges": [{"agent_claims_success_without_mutation": True}],
        "goal": scenario().goal.model_dump(mode="json"),
        "initial_message": scenario().initial_message,
        "persona": scenario().persona.model_dump(mode="json"),
    }
    assert regression.expected == failure.expected
    assert regression.observed == failure.observed
    assert regression.mis_memory_id == "mem_false_success"


def test_failure_clusters_are_stable_and_group_by_normalized_reason() -> None:
    base = FailureCase(
        schema_version=1,
        id="ocfailure_one",
        run_id=RUN_ID,
        scenario_id=scenario().id,
        evaluation_result_id="evr_one",
        reason_code="duplicate_mutation",
        expected={"count": 1},
        observed={"count": 2},
        evidence_refs=[],
        created_at=NOW,
    )
    failures = [
        base,
        base.model_copy(
            update={
                "id": "ocfailure_two",
                "run_id": "ocrun_other",
                "evaluation_result_id": "evr_two",
            }
        ),
        base.model_copy(
            update={
                "id": "ocfailure_three",
                "reason_code": "confirmation_missing_before_mutation",
                "evaluation_result_id": "evr_three",
            }
        ),
    ]

    clusters = cluster_failures("occampaign_regression", failures, created_at=NOW)

    assert [cluster.signature for cluster in clusters] == [
        "confirmation_missing_before_mutation",
        "duplicate_mutation",
    ]
    assert clusters[1].failure_case_ids == ["ocfailure_one", "ocfailure_two"]
    assert [cluster.canonical_json_bytes() for cluster in clusters] == [
        cluster.canonical_json_bytes()
        for cluster in cluster_failures("occampaign_regression", failures, created_at=NOW)
    ]


def test_regression_case_replays_the_referenced_scenario_and_rejects_drift() -> None:
    result = evaluation(
        "evr_false_success",
        "task_success.v1",
        EvaluationStatus.FAIL,
        ["agent_claims_success_without_state"],
    )
    failure = build_failure_cases(context(), [result])[0]
    regression = build_regression_case(
        failure=failure,
        scenario=scenario(),
        evaluation=result,
        created_at=NOW,
    )
    config = MockAgentConfig.candidate()
    version = AgentVersion(
        schema_version=1,
        id="ocagentv_regression_candidate",
        agent_id="ocagent_appointment",
        version="candidate",
        adapter_kind=AdapterKind.MOCK,
        config_sha256=config.canonical_sha256(),
        created_at=NOW,
    )

    replay = asyncio.run(
        replay_regression(
            regression=regression,
            scenario=scenario(),
            agent_version=version,
            adapter=MockAgentAdapter(config),
            campaign_id="occampaign_regression_replay",
            created_at=NOW,
        )
    )

    assert replay.scenario.id == regression.scenario_id
    expected_state = scenario().expectations.final_state
    assert {key: replay.final_state[key] for key in expected_state} == expected_state
    drifted = scenario().model_copy(update={"initial_message": "silently changed"})
    with pytest.raises(ValueError, match="snapshot"):
        asyncio.run(
            replay_regression(
                regression=regression,
                scenario=drifted,
                agent_version=version,
                adapter=MockAgentAdapter(config),
                campaign_id="occampaign_drifted",
                created_at=NOW,
            )
        )
