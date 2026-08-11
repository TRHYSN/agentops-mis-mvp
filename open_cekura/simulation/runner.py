"""Deterministic adapter orchestration into existing OpenCekura domain rows."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StrictBool,
    StrictStr,
    field_validator,
    model_validator,
)

from open_cekura.domain.enums import AdapterKind, RunFinalState, TurnRole
from open_cekura.domain.ids import stable_id
from open_cekura.domain.models import (
    AgentVersion,
    ConversationRun,
    ConversationTurn,
    ObservedToolCall,
)
from open_cekura.scenarios.schema import ScenarioDefinition

from .agent_adapter import (
    AdapterContractError,
    AdapterTimeoutError,
    AdapterTransportError,
    AgentAdapter,
)
from .personas import user_messages_for


DurationMilliseconds = Annotated[int, Field(strict=True, ge=0, le=3_600_000)]


class SimulationResult(BaseModel):
    """Closed, ordered input for deterministic EvaluationContext construction."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    schema_version: Literal[1] = 1
    scenario: ScenarioDefinition
    agent_version: AgentVersion
    run: ConversationRun
    turns: tuple[ConversationTurn, ...]
    tool_calls: tuple[ObservedToolCall, ...]
    initial_state: dict[StrictStr, JsonValue]
    final_state: dict[StrictStr, JsonValue]
    started_at: datetime
    finished_at: datetime
    duration_ms: DurationMilliseconds
    timed_out: StrictBool
    adapter_error: StrictStr | None
    agent_success_claim: StrictBool | None

    @field_validator("started_at", "finished_at")
    @classmethod
    def timestamps_are_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("simulation timestamps must be UTC-aware")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def graph_is_ordered_and_linked(self) -> "SimulationResult":
        if self.finished_at < self.started_at:
            raise ValueError("finished_at must not precede started_at")
        if tuple(turn.turn_index for turn in self.turns) != tuple(range(len(self.turns))):
            raise ValueError("turns must be ordered with contiguous turn_index values")
        turn_ids = {turn.id for turn in self.turns}
        if any(turn.run_id != self.run.id for turn in self.turns):
            raise ValueError("all turns must reference the result run")
        if any(call.run_id != self.run.id for call in self.tool_calls):
            raise ValueError("all tool calls must reference the result run")
        if any(call.turn_id not in turn_ids for call in self.tool_calls):
            raise ValueError("all tool calls must reference a result turn")
        return self

    def canonical_json_bytes(self) -> bytes:
        return json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")


async def run_scenario(
    *,
    scenario: ScenarioDefinition,
    agent_version: AgentVersion,
    adapter: AgentAdapter,
    campaign_id: str,
    created_at: datetime | None = None,
) -> SimulationResult:
    """Execute one deterministic persona script and retain typed ordered evidence."""

    if not isinstance(scenario, ScenarioDefinition):
        raise AdapterContractError("scenario must be a validated ScenarioDefinition")
    if not isinstance(agent_version, AgentVersion):
        raise AdapterContractError("agent_version must be a validated AgentVersion")
    if not isinstance(adapter, AgentAdapter):
        raise AdapterContractError("adapter must implement AgentAdapter")
    if not isinstance(campaign_id, str) or not campaign_id:
        raise AdapterContractError("campaign_id must be a non-empty string")

    started_at = created_at or datetime.now(timezone.utc)
    if started_at.tzinfo is None or started_at.utcoffset() != timedelta(0):
        raise AdapterContractError("created_at must be UTC-aware")
    started_at = started_at.astimezone(timezone.utc)
    run_id = stable_id("ocrun", campaign_id, scenario.id, agent_version.id)
    turns: list[ConversationTurn] = []
    tool_calls: list[ObservedToolCall] = []
    initial_state: dict[str, JsonValue] = {}
    final_state: dict[str, JsonValue] = {}
    adapter_error: str | None = None
    timed_out = False
    success_claim: bool | None = None
    completed = False
    timeout_seconds = scenario.expectations.timeout_ms / 1000.0
    lifecycle_started_ns = time.perf_counter_ns()

    try:
        try:
            session = await asyncio.wait_for(
                adapter.start(scenario, agent_version),
                timeout=timeout_seconds,
            )
            initial_state = dict(session.initial_state)
            final_state = dict(initial_state)
        except asyncio.TimeoutError:
            timed_out = True
            adapter_error = "adapter_timeout:start"
        except AdapterTimeoutError as exc:
            timed_out = True
            adapter_error = f"adapter_timeout:start:{exc}"
        except AdapterTransportError as exc:
            adapter_error = f"adapter_transport:start:{exc}"
        except AdapterContractError as exc:
            adapter_error = f"adapter_contract:start:{exc}"

        if adapter_error is None:
            for message in user_messages_for(scenario):
                if len(turns) + 2 > scenario.expectations.max_turns:
                    adapter_error = "turn_limit_exceeded"
                    break

                user_turn = _turn(
                    run_id=run_id,
                    turn_index=len(turns),
                    role=TurnRole.USER,
                    content=message,
                    created_at=started_at,
                )
                turns.append(user_turn)
                try:
                    reply = await asyncio.wait_for(
                        adapter.send(message),
                        timeout=timeout_seconds,
                    )
                except asyncio.TimeoutError:
                    timed_out = True
                    adapter_error = "adapter_timeout:send"
                    break
                except AdapterTimeoutError as exc:
                    timed_out = True
                    adapter_error = f"adapter_timeout:send:{exc}"
                    break
                except AdapterTransportError as exc:
                    adapter_error = f"adapter_transport:send:{exc}"
                    break
                except AdapterContractError as exc:
                    adapter_error = f"adapter_contract:send:{exc}"
                    break

                assistant_turn = _turn(
                    run_id=run_id,
                    turn_index=len(turns),
                    role=TurnRole.ASSISTANT,
                    content=reply.message,
                    created_at=started_at,
                )
                turns.append(assistant_turn)
                final_state = dict(reply.final_state)
                success_claim = reply.success_claim
                timed_out = timed_out or reply.timed_out
                if reply.error is not None:
                    adapter_error = reply.error

                try:
                    observations = await asyncio.wait_for(
                        adapter.observe_tool_calls(),
                        timeout=timeout_seconds,
                    )
                except asyncio.TimeoutError:
                    timed_out = True
                    adapter_error = "adapter_timeout:observe_tool_calls"
                    break
                except AdapterTimeoutError as exc:
                    timed_out = True
                    adapter_error = f"adapter_timeout:observe_tool_calls:{exc}"
                    break
                except AdapterTransportError as exc:
                    adapter_error = f"adapter_transport:observe_tool_calls:{exc}"
                    break
                except AdapterContractError as exc:
                    adapter_error = f"adapter_contract:observe_tool_calls:{exc}"
                    break

                for observation in observations:
                    tool_calls.append(
                        ObservedToolCall(
                            schema_version=1,
                            id=stable_id(
                                "octool",
                                run_id,
                                assistant_turn.id,
                                str(len(tool_calls)),
                                observation.name,
                            ),
                            run_id=run_id,
                            turn_id=assistant_turn.id,
                            name=observation.name,
                            arguments=observation.arguments,
                            result=observation.result,
                            error=observation.error,
                            is_mutation=observation.is_mutation,
                            duration_ms=observation.duration_ms,
                            mis_tool_call_id=None,
                            created_at=started_at,
                        )
                    )

                if reply.completed:
                    completed = True
                    break

            if not completed and adapter_error is None:
                adapter_error = "adapter_incomplete"
    finally:
        try:
            await asyncio.wait_for(adapter.close(), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            timed_out = True
            adapter_error = adapter_error or "adapter_timeout:close"
        except AdapterTimeoutError as exc:
            timed_out = True
            adapter_error = adapter_error or f"adapter_timeout:close:{exc}"
        except AdapterTransportError as exc:
            adapter_error = adapter_error or f"adapter_transport:close:{exc}"
        except AdapterContractError as exc:
            adapter_error = adapter_error or f"adapter_contract:close:{exc}"

    lifecycle_duration_ms = (time.perf_counter_ns() - lifecycle_started_ns) // 1_000_000
    duration_ms = (
        lifecycle_duration_ms
        if agent_version.adapter_kind is AdapterKind.HTTP
        else sum(call.duration_ms for call in tool_calls)
    )
    finished_at = started_at + timedelta(milliseconds=duration_ms)
    if adapter_error is not None or timed_out or not completed:
        status = RunFinalState.ERROR
    elif success_claim is False:
        status = RunFinalState.FAIL
    else:
        status = RunFinalState.PASS
    run = ConversationRun(
        schema_version=1,
        id=run_id,
        campaign_id=campaign_id,
        scenario_id=scenario.id,
        agent_version_id=agent_version.id,
        status=status,
        mis_run_id=None,
        created_at=started_at,
    )
    return SimulationResult(
        scenario=scenario,
        agent_version=agent_version,
        run=run,
        turns=tuple(turns),
        tool_calls=tuple(tool_calls),
        initial_state=initial_state,
        final_state=final_state,
        started_at=started_at,
        finished_at=finished_at,
        duration_ms=duration_ms,
        timed_out=timed_out,
        adapter_error=adapter_error,
        agent_success_claim=success_claim,
    )


def _turn(
    *,
    run_id: str,
    turn_index: int,
    role: TurnRole,
    content: str,
    created_at: datetime,
) -> ConversationTurn:
    return ConversationTurn(
        schema_version=1,
        id=stable_id("octurn", run_id, str(turn_index), role.value),
        run_id=run_id,
        turn_index=turn_index,
        role=role,
        content=content,
        created_at=created_at,
    )


__all__ = ["SimulationResult", "run_scenario"]
