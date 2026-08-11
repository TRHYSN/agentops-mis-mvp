"""Closed, deterministic inputs shared by OpenCekura evaluators."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from open_cekura.domain.models import ConversationTurn, ObservedToolCall, StableIdentifier
from open_cekura.scenarios.schema import BackendTimeoutChallenge, ScenarioDefinition


class EvaluationContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class TimeoutObservation(EvaluationContract):
    """One measured adapter or tool timeout, with no inferred timing facts."""

    operation: Literal["adapter", "tool"]
    tool_name: StableIdentifier | None = None
    turn_id: StableIdentifier | None = None
    configured_ms: int = Field(strict=True, ge=1)
    measured_ms: int = Field(strict=True, ge=0)

    @model_validator(mode="after")
    def tool_timeout_has_tool_identity(self) -> "TimeoutObservation":
        if self.operation == "tool" and not self.tool_name:
            raise ValueError("tool timeout requires tool_name")
        return self


class EvaluationContext(EvaluationContract):
    """All facts a deterministic evaluator may inspect."""

    scenario: ScenarioDefinition
    run_id: StableIdentifier
    turns: list[ConversationTurn]
    tool_calls: list[ObservedToolCall]
    initial_state: dict[str, JsonValue]
    final_state: dict[str, JsonValue]
    agent_claimed_success: bool | None
    timeout_observations: list[TimeoutObservation]
    evaluated_at: datetime

    @model_validator(mode="after")
    def context_graph_is_consistent(self) -> "EvaluationContext":
        if self.evaluated_at.tzinfo is None or self.evaluated_at.utcoffset() != timedelta(0):
            raise ValueError("evaluated_at must be UTC-aware")
        object.__setattr__(self, "evaluated_at", self.evaluated_at.astimezone(timezone.utc))

        turn_ids: set[str] = set()
        turn_indexes: set[int] = set()
        previous_index = -1
        for turn in self.turns:
            if turn.run_id != self.run_id:
                raise ValueError("every turn must belong to the evaluation run")
            if turn.id in turn_ids or turn.turn_index in turn_indexes:
                raise ValueError("turn IDs and indexes must be unique")
            if turn.turn_index <= previous_index:
                raise ValueError("turns must be ordered by increasing turn_index")
            turn_ids.add(turn.id)
            turn_indexes.add(turn.turn_index)
            previous_index = turn.turn_index

        call_ids: set[str] = set()
        for call in self.tool_calls:
            if call.run_id != self.run_id:
                raise ValueError("every tool call must belong to the evaluation run")
            if call.turn_id not in turn_ids:
                raise ValueError("every tool call must reference a context turn")
            if call.id in call_ids:
                raise ValueError("tool-call IDs must be unique")
            call_ids.add(call.id)

        for timeout in self.timeout_observations:
            if timeout.turn_id is not None and timeout.turn_id not in turn_ids:
                raise ValueError("timeout turn_id must reference a context turn")
        return self


def context_from_simulation(
    simulation,
    *,
    evaluated_at: datetime,
) -> EvaluationContext:
    """Project typed simulation evidence into the closed evaluator context."""

    from open_cekura.simulation.runner import SimulationResult

    if not isinstance(simulation, SimulationResult):
        raise TypeError("simulation must be a SimulationResult")
    observations: list[TimeoutObservation] = []
    for call in simulation.tool_calls:
        if call.error != "timeout":
            continue
        challenge = next(
            (
                item
                for item in simulation.scenario.challenges
                if isinstance(item, BackendTimeoutChallenge)
                and item.backend_timeout_on_tool == call.name
            ),
            None,
        )
        configured_ms = (
            challenge.timeout_ms
            if challenge is not None and challenge.timeout_ms is not None
            else simulation.scenario.expectations.timeout_ms
        )
        observations.append(
            TimeoutObservation(
                operation="tool",
                tool_name=call.name,
                turn_id=call.turn_id,
                configured_ms=configured_ms,
                measured_ms=call.duration_ms,
            )
        )
    if simulation.timed_out and not observations:
        configured_ms = simulation.scenario.expectations.timeout_ms
        observations.append(
            TimeoutObservation(
                operation="adapter",
                configured_ms=configured_ms,
                measured_ms=max(configured_ms, simulation.duration_ms),
            )
        )
    return EvaluationContext(
        scenario=simulation.scenario,
        run_id=simulation.run.id,
        turns=list(simulation.turns),
        tool_calls=list(simulation.tool_calls),
        initial_state=simulation.initial_state,
        final_state=simulation.final_state,
        agent_claimed_success=simulation.agent_success_claim,
        timeout_observations=observations,
        evaluated_at=evaluated_at,
    )


__all__ = ["EvaluationContext", "TimeoutObservation", "context_from_simulation"]
