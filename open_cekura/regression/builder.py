"""Convert deterministic evaluation failures into reviewed regression input."""

from __future__ import annotations

from datetime import datetime
from typing import Iterable

from open_cekura.domain.enums import EvaluationStatus
from open_cekura.domain.ids import stable_id
from open_cekura.domain.models import (
    AgentVersion,
    EvaluationResult,
    FailureCase,
    RegressionCase,
)
from open_cekura.evaluation.base import EvaluationContext
from open_cekura.scenarios.schema import ScenarioDefinition
from open_cekura.simulation.agent_adapter import AgentAdapter
from open_cekura.simulation.runner import SimulationResult, run_scenario


_FAILURE_STATUSES = {EvaluationStatus.FAIL, EvaluationStatus.ERROR}


def _unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(values))


def regression_input_snapshot(scenario: ScenarioDefinition) -> dict[str, object]:
    """Return the complete Scenario v1 input contract captured by regressions."""

    scenario_json = scenario.model_dump(mode="json")
    return {
        "initial_message": scenario.initial_message,
        "persona": scenario_json["persona"],
        "goal": scenario_json["goal"],
        "challenges": scenario_json["challenges"],
        "expectations": scenario_json["expectations"],
    }


def build_failure_cases(
    context: EvaluationContext,
    evaluations: Iterable[EvaluationResult],
) -> list[FailureCase]:
    """Normalize every failed/error evaluator result without hiding its facts."""

    failures: list[FailureCase] = []
    for evaluation in evaluations:
        if evaluation.run_id != context.run_id:
            raise ValueError("evaluation result belongs to a different run")
        if evaluation.status not in _FAILURE_STATUSES:
            continue
        reason_codes = evaluation.reason_codes or ["evaluation_failed"]
        for reason_code in reason_codes:
            failures.append(
                FailureCase(
                    schema_version=1,
                    id=stable_id(
                        "ocfailure",
                        context.run_id,
                        evaluation.id,
                        reason_code,
                    ),
                    run_id=context.run_id,
                    scenario_id=context.scenario.id,
                    evaluation_result_id=evaluation.id,
                    reason_code=reason_code,
                    expected={
                        "evaluation_status": "pass",
                        "final_state": context.scenario.expectations.final_state,
                    },
                    observed={
                        "evaluation_status": evaluation.status.value,
                        "final_state": context.final_state,
                        "metadata": evaluation.metadata,
                    },
                    evidence_refs=_unique(
                        [*evaluation.evidence_refs, f"evaluation:{evaluation.id}"]
                    ),
                    created_at=evaluation.created_at,
                )
            )
    return failures


def build_regression_case(
    *,
    failure: FailureCase,
    scenario: ScenarioDefinition,
    evaluation: EvaluationResult,
    created_at: datetime,
    mis_memory_id: str | None = None,
) -> RegressionCase:
    """Review one normalized failure into a complete, reference-based replay case."""

    if failure.scenario_id != scenario.id:
        raise ValueError("failure and scenario IDs do not match")
    if failure.evaluation_result_id != evaluation.id:
        raise ValueError("failure and evaluation IDs do not match")
    if failure.run_id != evaluation.run_id:
        raise ValueError("failure and evaluation run IDs do not match")
    if evaluation.status not in _FAILURE_STATUSES:
        raise ValueError("only failed or errored evaluations can become regressions")

    return RegressionCase(
        schema_version=1,
        id=stable_id("ocregression", failure.id, evaluation.evaluator_id),
        failure_case_id=failure.id,
        scenario_id=scenario.id,
        source_run_id=failure.run_id,
        name=f"Regression: {scenario.name} [{failure.reason_code}]",
        original_input=regression_input_snapshot(scenario),
        expected=failure.expected,
        observed=failure.observed,
        reason_code=failure.reason_code,
        evaluator_id=evaluation.evaluator_id,
        evidence_refs=_unique([*failure.evidence_refs, f"evaluation:{evaluation.id}"]),
        mis_memory_id=mis_memory_id,
        created_at=created_at,
    )


async def replay_regression(
    *,
    regression: RegressionCase,
    scenario: ScenarioDefinition,
    agent_version: AgentVersion,
    adapter: AgentAdapter,
    campaign_id: str,
    created_at: datetime | None = None,
) -> SimulationResult:
    """Replay the referenced Scenario after rejecting contract drift."""

    if regression.scenario_id != scenario.id:
        raise ValueError("regression and scenario IDs do not match")
    if regression.original_input != regression_input_snapshot(scenario):
        raise ValueError("scenario no longer matches the regression input snapshot")
    return await run_scenario(
        scenario=scenario,
        agent_version=agent_version,
        adapter=adapter,
        campaign_id=campaign_id,
        created_at=created_at,
    )


__all__ = [
    "build_failure_cases",
    "build_regression_case",
    "regression_input_snapshot",
    "replay_regression",
]
