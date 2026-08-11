from __future__ import annotations

from datetime import datetime, timezone

import pytest

from open_cekura.domain.enums import EvaluationStatus
from open_cekura.domain.ids import stable_id
from open_cekura.domain.models import EvaluationResult
from open_cekura.evaluation.aggregation import (
    EvaluationResultGroup,
    RunMetricFacts,
    aggregate_campaign_metrics,
)


NOW = datetime(2026, 8, 11, 17, 0, tzinfo=timezone.utc)
ZERO_TOLERANCE_IDS = (
    "forbidden_tool_calls.v1",
    "duplicate_mutation.v1",
    "confirmation_before_mutation.v1",
    "timeout.v1",
)


def evaluation_result(
    run_id: str,
    evaluator_id: str,
    status: EvaluationStatus,
) -> EvaluationResult:
    scored = status in {
        EvaluationStatus.PASS,
        EvaluationStatus.FAIL,
        EvaluationStatus.WARN,
    }
    score = 1.0 if status is EvaluationStatus.PASS else 0.0 if scored else None
    return EvaluationResult(
        schema_version=1,
        id=stable_id("evr", run_id, evaluator_id),
        run_id=run_id,
        evaluator_id=evaluator_id,
        status=status,
        score=score,
        threshold=1.0 if scored else None,
        reason_codes=[],
        evidence_refs=[],
        metadata={},
        mis_evaluation_id=None,
        created_at=NOW,
    )


def result_group(
    run_id: str,
    task_status: EvaluationStatus,
    *,
    forbidden: EvaluationStatus = EvaluationStatus.PASS,
    duplicate: EvaluationStatus = EvaluationStatus.PASS,
    confirmation: EvaluationStatus = EvaluationStatus.PASS,
    timeout: EvaluationStatus = EvaluationStatus.PASS,
    judges: tuple[EvaluationResult, ...] = (),
) -> EvaluationResultGroup:
    statuses = (forbidden, duplicate, confirmation, timeout)
    deterministic = [evaluation_result(run_id, "task_success.v1", task_status)]
    deterministic.extend(
        evaluation_result(run_id, evaluator_id, status)
        for evaluator_id, status in zip(ZERO_TOLERANCE_IDS, statuses, strict=True)
    )
    return EvaluationResultGroup(
        run_id=run_id,
        deterministic_results=deterministic,
        judge_results=list(judges),
    )


def test_aggregation_keeps_success_errors_and_zero_tolerance_failures_explicit() -> None:
    groups = [
        result_group(
            "ocrun_1",
            EvaluationStatus.PASS,
            judges=(
                evaluation_result(
                    "ocrun_1", "llm_judge.v1", EvaluationStatus.SKIPPED
                ),
            ),
        ),
        result_group(
            "ocrun_2",
            EvaluationStatus.FAIL,
            forbidden=EvaluationStatus.FAIL,
            confirmation=EvaluationStatus.FAIL,
            timeout=EvaluationStatus.FAIL,
            judges=(
                evaluation_result("ocrun_2", "llm_judge.v1", EvaluationStatus.PASS),
            ),
        ),
        result_group(
            "ocrun_3",
            EvaluationStatus.ERROR,
            forbidden=EvaluationStatus.ERROR,
            judges=(
                evaluation_result("ocrun_3", "llm_judge.v1", EvaluationStatus.ERROR),
            ),
        ),
    ]
    facts = [
        RunMetricFacts(run_id="ocrun_1", turn_count=4, latency_ms=100),
        RunMetricFacts(run_id="ocrun_2", turn_count=10, latency_ms=900),
        RunMetricFacts(run_id="ocrun_3", turn_count=7, latency_ms=500),
    ]

    metrics = aggregate_campaign_metrics(groups, facts)

    assert metrics.run_count == 3
    assert metrics.task_success_pass_count == 1
    assert metrics.task_success_result_count == 3
    assert metrics.task_success_rate == pytest.approx(1 / 3)
    assert metrics.deterministic_result_count == 15
    assert metrics.deterministic_pass_count == 9
    assert metrics.deterministic_fail_count == 4
    assert metrics.deterministic_warn_count == 0
    assert metrics.deterministic_error_count == 2
    assert metrics.deterministic_error_rate == pytest.approx(2 / 15)

    assert metrics.forbidden_call_violation_count == 1
    assert metrics.forbidden_call_result_count == 3
    assert metrics.forbidden_call_violation_rate == pytest.approx(1 / 2)
    assert metrics.forbidden_call_error_count == 1
    assert metrics.duplicate_mutation_violation_count == 0
    assert metrics.duplicate_mutation_result_count == 3
    assert metrics.duplicate_mutation_violation_rate == 0.0
    assert metrics.duplicate_mutation_error_count == 0
    assert metrics.confirmation_violation_count == 1
    assert metrics.confirmation_result_count == 3
    assert metrics.confirmation_violation_rate == pytest.approx(1 / 3)
    assert metrics.confirmation_error_count == 0
    assert metrics.timeout_count == 1
    assert metrics.timeout_result_count == 3
    assert metrics.timeout_rate == pytest.approx(1 / 3)
    assert metrics.timeout_error_count == 0

    assert metrics.judge_result_count == 3
    assert metrics.judge_pass_count == 1
    assert metrics.judge_fail_count == 0
    assert metrics.judge_warn_count == 0
    assert metrics.judge_error_count == 1
    assert metrics.skipped_judge_count == 1
    assert metrics.median_turns == 7.0
    assert metrics.median_latency_ms == 500.0
    retained_ids = {
        result.id
        for group in metrics.result_groups
        for result in (*group.deterministic_results, *group.judge_results)
    }
    assert len(retained_ids) == 18


def test_aggregation_is_deterministic_for_input_order() -> None:
    groups = [
        result_group("ocrun_a", EvaluationStatus.PASS),
        result_group("ocrun_b", EvaluationStatus.FAIL, duplicate=EvaluationStatus.FAIL),
    ]
    facts = [
        RunMetricFacts(run_id="ocrun_a", turn_count=3, latency_ms=200),
        RunMetricFacts(run_id="ocrun_b", turn_count=8, latency_ms=800),
    ]

    forward = aggregate_campaign_metrics(groups, facts)
    reverse = aggregate_campaign_metrics(list(reversed(groups)), list(reversed(facts)))

    assert forward.model_dump(mode="json") == reverse.model_dump(mode="json")


def test_aggregation_rejects_mismatched_or_duplicate_run_facts() -> None:
    groups = [result_group("ocrun_a", EvaluationStatus.PASS)]

    with pytest.raises(ValueError, match="run facts must match evaluation groups"):
        aggregate_campaign_metrics(
            groups,
            [RunMetricFacts(run_id="ocrun_other", turn_count=3, latency_ms=100)],
        )

    with pytest.raises(ValueError, match="run metric facts must be unique"):
        aggregate_campaign_metrics(
            groups,
            [
                RunMetricFacts(run_id="ocrun_a", turn_count=3, latency_ms=100),
                RunMetricFacts(run_id="ocrun_a", turn_count=4, latency_ms=200),
            ],
        )


def test_result_group_rejects_cross_run_or_repeated_evaluator_results() -> None:
    result = evaluation_result("ocrun_a", "task_success.v1", EvaluationStatus.PASS)

    with pytest.raises(ValueError, match="must belong to the group run"):
        EvaluationResultGroup(
            run_id="ocrun_other",
            deterministic_results=[result],
            judge_results=[],
        )

    with pytest.raises(ValueError, match="evaluator IDs must be unique"):
        EvaluationResultGroup(
            run_id="ocrun_a",
            deterministic_results=[result, result.model_copy(update={"id": "evr_repeat"})],
            judge_results=[],
        )


def test_result_group_requires_one_task_success_result_for_scenario_denominator() -> None:
    forbidden = evaluation_result(
        "ocrun_a",
        "forbidden_tool_calls.v1",
        EvaluationStatus.PASS,
    )

    with pytest.raises(ValueError, match="exactly one task_success.v1"):
        EvaluationResultGroup(
            run_id="ocrun_a",
            deterministic_results=[forbidden],
            judge_results=[],
        )
