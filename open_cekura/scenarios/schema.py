"""Pydantic contract for OpenCekura Scenario YAML schema version 1."""
from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StrictBool, StrictStr, field_validator, model_validator


NonEmptyText = Annotated[StrictStr, Field(min_length=1, max_length=512)]
ScenarioId = Annotated[
    StrictStr,
    Field(min_length=3, max_length=160, pattern=r"^[a-z0-9][a-z0-9._-]*$"),
]
ToolName = Annotated[
    StrictStr,
    Field(min_length=1, max_length=128, pattern=r"^[A-Za-z_][A-Za-z0-9_.-]*$"),
]
TurnNumber = Annotated[int, Field(strict=True, ge=1, le=1000)]
TimeoutMilliseconds = Annotated[int, Field(strict=True, ge=1, le=3_600_000)]


class StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class Verbosity(str, Enum):
    SHORT = "short"
    MEDIUM = "medium"
    LONG = "long"


class GoalType(str, Enum):
    BOOK = "book"
    LOOKUP = "lookup"
    RESCHEDULE = "reschedule"
    CANCEL = "cancel"


class PersonaSpec(StrictContract):
    language: Annotated[
        StrictStr,
        Field(min_length=2, max_length=35, pattern=r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$"),
    ]
    tone: Annotated[StrictStr, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z][A-Za-z0-9 _-]*$")]
    verbosity: Verbosity


class GoalSpec(StrictContract):
    type: GoalType
    booking_id: Annotated[StrictStr, Field(min_length=1, max_length=128)] | None = None
    requested_slot: Annotated[StrictStr, Field(min_length=1, max_length=128)] | None = None
    requested_date: Annotated[StrictStr, Field(min_length=1, max_length=32)] | None = None
    requested_time: Annotated[StrictStr, Field(min_length=1, max_length=32)] | None = None


class InterruptChallenge(StrictContract):
    interrupt_after_turn: TurnNumber


class ChangeConstraintChallenge(StrictContract):
    change_constraint_after_turn: TurnNumber


class AmbiguousIdentityChallenge(StrictContract):
    ambiguous_identity: Literal[True]


class UnavailableSlotChallenge(StrictContract):
    unavailable_slot: Literal[True]


class DuplicateRequestChallenge(StrictContract):
    duplicate_request_after_turn: TurnNumber


class MutationBeforeConfirmationChallenge(StrictContract):
    mutation_before_confirmation: Literal[True]


class BackendTimeoutChallenge(StrictContract):
    backend_timeout_on_tool: ToolName
    timeout_ms: TimeoutMilliseconds | None = None


class ToolSuccessClaimFailureChallenge(StrictContract):
    tool_succeeds_agent_claims_failure: ToolName


class FalseSuccessClaimChallenge(StrictContract):
    agent_claims_success_without_mutation: Literal[True]


Challenge = (
    InterruptChallenge
    | ChangeConstraintChallenge
    | AmbiguousIdentityChallenge
    | UnavailableSlotChallenge
    | DuplicateRequestChallenge
    | MutationBeforeConfirmationChallenge
    | BackendTimeoutChallenge
    | ToolSuccessClaimFailureChallenge
    | FalseSuccessClaimChallenge
)


class ScenarioExpectations(StrictContract):
    required_tool_calls: list[ToolName]
    forbidden_tool_calls: list[ToolName]
    must_confirm_before_mutation: StrictBool
    final_state: dict[NonEmptyText, JsonValue]
    max_turns: Annotated[int, Field(strict=True, ge=1, le=1000)] = 12
    timeout_ms: TimeoutMilliseconds = 5000

    @field_validator("required_tool_calls", "forbidden_tool_calls")
    @classmethod
    def tool_names_are_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("tool call expectations must not contain duplicates")
        return value
    @field_validator("final_state")
    @classmethod
    def final_state_is_explicit(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        if not value:
            raise ValueError("final_state must contain at least one expected field")
        return value

    @model_validator(mode="after")
    def tool_expectations_do_not_conflict(self) -> "ScenarioExpectations":
        conflict = set(self.required_tool_calls).intersection(self.forbidden_tool_calls)
        if conflict:
            raise ValueError(f"tool calls cannot be both required and forbidden: {sorted(conflict)}")
        return self


class ScenarioDefinition(StrictContract):
    schema_version: Literal[1]
    id: ScenarioId
    name: NonEmptyText
    persona: PersonaSpec
    initial_message: Annotated[StrictStr, Field(min_length=1, max_length=16_000)]
    goal: GoalSpec
    challenges: list[Challenge]
    expectations: ScenarioExpectations
    tags: list[Annotated[StrictStr, Field(min_length=1, max_length=64)]] = Field(default_factory=list)

    @field_validator("tags")
    @classmethod
    def tags_are_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("tags must not contain duplicates")
        return value

    def canonical_json_bytes(self) -> bytes:
        """Return the semantic Scenario contract independent of YAML encoding."""

        return json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    def canonical_sha256(self) -> str:
        return hashlib.sha256(self.canonical_json_bytes()).hexdigest()
