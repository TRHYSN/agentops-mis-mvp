"""Strict campaign-level evaluation aggregation contracts."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from statistics import median
from typing import Annotated, Literal, NamedTuple

from pydantic import BaseModel, ConfigDict, Field, model_validator

from open_cekura.domain.enums import EvaluationStatus
from open_cekura.domain.models import EvaluationResult, StableIdentifier


TASK_SUCCESS_EVALUATOR_ID = "task_success.v1"
FORBIDDEN_CALL_EVALUATOR_ID = "forbidden_tool_calls.v1"
DUPLICATE_MUTATION_EVALUATOR_ID = "duplicate_mutation.v1"
CONFIRMATION_EVALUATOR_ID = "confirmation_before_mutation.v1"
TIMEOUT_EVALUATOR_ID = "timeout.v1"

NonNegativeInt = Annotated[int, Field(strict=True, ge=0)]
Rate = Annotated[float, Field(strict=True, ge=0.0, le=1.0, allow_inf_nan=False)]


class AggregationContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, validate_default=True)


class EvaluationResultGroup(AggregationContract):
    run_id: StableIdentifier
    deterministic_results: list[EvaluationResult]
    judge_results: list[EvaluationResult] = Field(default_factory=list)

    @model_validator(mode="after")
    def results_are_closed_to_one_run(self) -> "EvaluationResultGroup":
        results = [*self.deterministic_results, *self.judge_results]
        if any(result.run_id != self.run_id for result in results):
            raise ValueError("all evaluation results must belong to the group run")
        evaluator_ids = [result.evaluator_id for result in results]
        if len(evaluator_ids) != len(set(evaluator_ids)):
            raise ValueError("evaluator IDs must be unique within a result group")
        if any(
            result.status is EvaluationStatus.SKIPPED
            for result in self.deterministic_results
        ):
            raise ValueError("deterministic evaluation results cannot be skipped")
        task_success_count = sum(
            result.evaluator_id == TASK_SUCCESS_EVALUATOR_ID
            for result in self.deterministic_results
        )
        if task_success_count != 1:
            raise ValueError(
                "each result group requires exactly one task_success.v1 result"
            )
        return self


class RunMetricFacts(AggregationContract):
    run_id: StableIdentifier
    turn_count: NonNegativeInt
    latency_ms: NonNegativeInt


class CampaignMetrics(AggregationContract):
    schema_version: Literal[1]
    result_groups: list[EvaluationResultGroup]
    run_count: NonNegativeInt
    task_success_pass_count: NonNegativeInt
    task_success_result_count: NonNegativeInt
    task_success_rate: Rate | None
    deterministic_result_count: NonNegativeInt
    deterministic_pass_count: NonNegativeInt
    deterministic_fail_count: NonNegativeInt
    deterministic_warn_count: NonNegativeInt
    deterministic_error_count: NonNegativeInt
    deterministic_error_rate: Rate | None
    forbidden_call_violation_count: NonNegativeInt
    forbidden_call_result_count: NonNegativeInt
    forbidden_call_violation_rate: Rate | None
    forbidden_call_error_count: NonNegativeInt
    duplicate_mutation_violation_count: NonNegativeInt
    duplicate_mutation_result_count: NonNegativeInt
    duplicate_mutation_violation_rate: Rate | None
    duplicate_mutation_error_count: NonNegativeInt
    confirmation_violation_count: NonNegativeInt
    confirmation_result_count: NonNegativeInt
    confirmation_violation_rate: Rate | None
    confirmation_error_count: NonNegativeInt
    timeout_count: NonNegativeInt
    timeout_result_count: NonNegativeInt
    timeout_rate: Rate | None
    timeout_error_count: NonNegativeInt
    judge_result_count: NonNegativeInt
    judge_pass_count: NonNegativeInt
    judge_fail_count: NonNegativeInt
    judge_warn_count: NonNegativeInt
    judge_error_count: NonNegativeInt
    skipped_judge_count: NonNegativeInt
    median_turns: float | None
    median_latency_ms: float | None


class _RuleSummary(NamedTuple):
    violation_count: int
    result_count: int
    violation_rate: float | None
    error_count: int


def aggregate_campaign_metrics(
    groups: Sequence[EvaluationResultGroup],
    run_facts: Sequence[RunMetricFacts],
) -> CampaignMetrics:
    group_ids = [group.run_id for group in groups]
    if len(group_ids) != len(set(group_ids)):
        raise ValueError("evaluation result groups must be unique by run_id")

    fact_ids = [fact.run_id for fact in run_facts]
    if len(fact_ids) != len(set(fact_ids)):
        raise ValueError("run metric facts must be unique by run_id")
    if set(group_ids) != set(fact_ids):
        raise ValueError("run facts must match evaluation groups")

    canonical_groups = [_canonical_group(group) for group in sorted(groups, key=_run_id)]
    deterministic = [
        result
        for group in canonical_groups
        for result in group.deterministic_results
    ]
    judges = [result for group in canonical_groups for result in group.judge_results]
    task_results = [
        result
        for result in deterministic
        if result.evaluator_id == TASK_SUCCESS_EVALUATOR_ID
    ]

    forbidden = _rule_summary(deterministic, FORBIDDEN_CALL_EVALUATOR_ID)
    duplicate = _rule_summary(deterministic, DUPLICATE_MUTATION_EVALUATOR_ID)
    confirmation = _rule_summary(deterministic, CONFIRMATION_EVALUATOR_ID)
    timeout = _rule_summary(deterministic, TIMEOUT_EVALUATOR_ID)
    deterministic_count = len(deterministic)
    deterministic_errors = _count_status(deterministic, EvaluationStatus.ERROR)
    task_passes = _count_status(task_results, EvaluationStatus.PASS)
    ordered_facts = sorted(run_facts, key=_run_id)

    return CampaignMetrics(
        schema_version=1,
        result_groups=canonical_groups,
        run_count=len(canonical_groups),
        task_success_pass_count=task_passes,
        task_success_result_count=len(task_results),
        task_success_rate=_rate(task_passes, len(task_results)),
        deterministic_result_count=deterministic_count,
        deterministic_pass_count=_count_status(deterministic, EvaluationStatus.PASS),
        deterministic_fail_count=_count_status(deterministic, EvaluationStatus.FAIL),
        deterministic_warn_count=_count_status(deterministic, EvaluationStatus.WARN),
        deterministic_error_count=deterministic_errors,
        deterministic_error_rate=_rate(deterministic_errors, deterministic_count),
        forbidden_call_violation_count=forbidden.violation_count,
        forbidden_call_result_count=forbidden.result_count,
        forbidden_call_violation_rate=forbidden.violation_rate,
        forbidden_call_error_count=forbidden.error_count,
        duplicate_mutation_violation_count=duplicate.violation_count,
        duplicate_mutation_result_count=duplicate.result_count,
        duplicate_mutation_violation_rate=duplicate.violation_rate,
        duplicate_mutation_error_count=duplicate.error_count,
        confirmation_violation_count=confirmation.violation_count,
        confirmation_result_count=confirmation.result_count,
        confirmation_violation_rate=confirmation.violation_rate,
        confirmation_error_count=confirmation.error_count,
        timeout_count=timeout.violation_count,
        timeout_result_count=timeout.result_count,
        timeout_rate=timeout.violation_rate,
        timeout_error_count=timeout.error_count,
        judge_result_count=len(judges),
        judge_pass_count=_count_status(judges, EvaluationStatus.PASS),
        judge_fail_count=_count_status(judges, EvaluationStatus.FAIL),
        judge_warn_count=_count_status(judges, EvaluationStatus.WARN),
        judge_error_count=_count_status(judges, EvaluationStatus.ERROR),
        skipped_judge_count=_count_status(judges, EvaluationStatus.SKIPPED),
        median_turns=_median(fact.turn_count for fact in ordered_facts),
        median_latency_ms=_median(fact.latency_ms for fact in ordered_facts),
    )


def _run_id(value: EvaluationResultGroup | RunMetricFacts) -> str:
    return value.run_id


def _canonical_group(group: EvaluationResultGroup) -> EvaluationResultGroup:
    return EvaluationResultGroup(
        run_id=group.run_id,
        deterministic_results=sorted(
            group.deterministic_results,
            key=lambda result: (result.evaluator_id, result.id),
        ),
        judge_results=sorted(
            group.judge_results,
            key=lambda result: (result.evaluator_id, result.id),
        ),
    )


def _count_status(
    results: Sequence[EvaluationResult],
    status: EvaluationStatus,
) -> int:
    return sum(result.status is status for result in results)


def _rate(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else numerator / denominator


def _rule_summary(
    results: Sequence[EvaluationResult],
    evaluator_id: str,
) -> _RuleSummary:
    matching = [result for result in results if result.evaluator_id == evaluator_id]
    violations = _count_status(matching, EvaluationStatus.FAIL)
    errors = _count_status(matching, EvaluationStatus.ERROR)
    evaluated = len(matching) - errors
    return _RuleSummary(
        violation_count=violations,
        result_count=len(matching),
        violation_rate=_rate(violations, evaluated),
        error_count=errors,
    )


def _median(values: Iterable[int]) -> float | None:
    rows = list(values)
    return None if not rows else float(median(rows))


__all__ = [
    "CampaignMetrics",
    "EvaluationResultGroup",
    "RunMetricFacts",
    "aggregate_campaign_metrics",
]
