"""Async agent adapter boundary and strict wire-neutral observations."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StrictBool,
    StrictStr,
)

from open_cekura.domain.models import AgentVersion
from open_cekura.scenarios.schema import ScenarioDefinition


NonEmptyText = Annotated[StrictStr, Field(min_length=1, max_length=20_000)]
ToolName = Annotated[
    StrictStr,
    Field(min_length=1, max_length=128, pattern=r"^[A-Za-z_][A-Za-z0-9_.-]*$"),
]
DurationMilliseconds = Annotated[int, Field(strict=True, ge=0, le=3_600_000)]


class AdapterError(RuntimeError):
    """Base exception for explicit adapter failures."""


class AdapterStateError(AdapterError):
    """An adapter lifecycle method was called out of order."""


class AdapterContractError(AdapterError):
    """Input or response data violated a typed adapter contract."""


class AdapterTransportError(AdapterError):
    """An adapter transport failed without returning a valid response."""


class AdapterTimeoutError(AdapterTransportError):
    """An adapter transport exceeded its explicit timeout."""


class StrictAdapterContract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class AdapterSession(StrictAdapterContract):
    schema_version: Literal[1] = 1
    initial_state: dict[StrictStr, JsonValue] = Field(default_factory=dict)


class AgentReply(StrictAdapterContract):
    schema_version: Literal[1] = 1
    message: NonEmptyText
    completed: StrictBool
    success_claim: StrictBool | None
    final_state: dict[StrictStr, JsonValue]
    timed_out: StrictBool
    error: NonEmptyText | None


class ToolCallObservation(StrictAdapterContract):
    schema_version: Literal[1] = 1
    name: ToolName
    arguments: dict[StrictStr, JsonValue]
    result: JsonValue | None
    error: NonEmptyText | None
    is_mutation: StrictBool
    duration_ms: DurationMilliseconds


class AgentAdapter(ABC):
    """Frozen asynchronous simulation lifecycle."""

    @abstractmethod
    async def start(
        self,
        scenario: ScenarioDefinition,
        agent_version: AgentVersion,
    ) -> AdapterSession:
        """Start one isolated scenario session."""

    @abstractmethod
    async def send(self, message: str) -> AgentReply:
        """Send one user message and return the corresponding agent reply."""

    @abstractmethod
    async def observe_tool_calls(self) -> tuple[ToolCallObservation, ...]:
        """Drain ordered tool-call observations produced since the last read."""

    @abstractmethod
    async def close(self) -> None:
        """Close the current session and release resources."""


__all__ = [
    "AdapterContractError",
    "AdapterError",
    "AdapterSession",
    "AdapterStateError",
    "AdapterTimeoutError",
    "AdapterTransportError",
    "AgentAdapter",
    "AgentReply",
    "ToolCallObservation",
]
