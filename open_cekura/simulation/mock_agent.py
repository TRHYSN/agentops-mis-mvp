"""Deterministic, network-free appointment simulation adapter."""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, StrictBool

from open_cekura.domain.enums import AdapterKind
from open_cekura.domain.models import AgentVersion
from open_cekura.scenarios.schema import (
    BackendTimeoutChallenge,
    DuplicateRequestChallenge,
    FalseSuccessClaimChallenge,
    GoalType,
    MutationBeforeConfirmationChallenge,
    ScenarioDefinition,
    ToolSuccessClaimFailureChallenge,
)

from .agent_adapter import (
    AdapterContractError,
    AdapterSession,
    AdapterStateError,
    AgentAdapter,
    AgentReply,
    ToolCallObservation,
)
from .personas import MUTATING_TOOL_NAMES, user_messages_for


DEFAULT_BOOKING_ID = "booking-001"
DEFAULT_SLOT = "2026-08-20T09:00:00Z"
MOCK_BACKEND_VERSION = "appointment_mock_backend.v1"
TOOL_CONTRACT_VERSION = "appointment_tool_contract.v1"
DETERMINISTIC_RANDOM_SEED = 0


class MockAgentConfig(BaseModel):
    """Explicit defect profile; scenario and campaign IDs never influence behavior."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    schema_version: Literal[1] = 1
    duplicate_mutations: StrictBool = False
    mutate_before_confirmation: StrictBool = False
    claim_failure_after_success: StrictBool = False
    claim_success_without_mutation: StrictBool = False
    call_forbidden_tools: StrictBool = False

    @classmethod
    def candidate(cls) -> "MockAgentConfig":
        return cls()

    @classmethod
    def baseline(cls) -> "MockAgentConfig":
        return cls(
            duplicate_mutations=True,
            mutate_before_confirmation=True,
            claim_failure_after_success=False,
            claim_success_without_mutation=True,
            call_forbidden_tools=False,
        )

    def canonical_json_bytes(self) -> bytes:
        return json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    def canonical_sha256(self) -> str:
        return hashlib.sha256(self.canonical_json_bytes()).hexdigest()


class MockAgentAdapter(AgentAdapter):
    def __init__(self, config: MockAgentConfig):
        if not isinstance(config, MockAgentConfig):
            raise TypeError("config must be MockAgentConfig")
        self.config = config
        self._scenario: ScenarioDefinition | None = None
        self._agent_version: AgentVersion | None = None
        self._script: tuple[str, ...] = ()
        self._message_count = 0
        self._unobserved: list[ToolCallObservation] = []
        self._executed_names: list[str] = []
        self._state: dict[str, object] = {}
        self._initial_state: dict[str, object] = {}
        self._timed_out = False
        self._closed = False

    async def start(
        self,
        scenario: ScenarioDefinition,
        agent_version: AgentVersion,
    ) -> AdapterSession:
        if not isinstance(scenario, ScenarioDefinition):
            raise AdapterContractError("scenario must be a validated ScenarioDefinition")
        if not isinstance(agent_version, AgentVersion):
            raise AdapterContractError("agent_version must be a validated AgentVersion")
        if agent_version.adapter_kind is not AdapterKind.MOCK:
            raise AdapterContractError("MockAgentAdapter requires adapter_kind=mock")
        if agent_version.config_sha256 != self.config.canonical_sha256():
            raise AdapterContractError(
                "AgentVersion.config_sha256 does not match MockAgentConfig"
            )
        if self._scenario is not None and not self._closed:
            raise AdapterStateError("adapter session is already started")

        booking_id = scenario.goal.booking_id or DEFAULT_BOOKING_ID
        initial_slot = (
            scenario.goal.requested_slot
            if scenario.goal.type is GoalType.LOOKUP and scenario.goal.requested_slot
            else DEFAULT_SLOT
        )
        self._initial_state = {
            "booking_id": booking_id,
            "slot": initial_slot,
            "status": "confirmed",
            "booking_updated": False,
        }
        self._state = dict(self._initial_state)
        self._scenario = scenario
        self._agent_version = agent_version
        self._script = user_messages_for(scenario)
        self._message_count = 0
        self._unobserved = []
        self._executed_names = []
        self._timed_out = False
        self._closed = False
        return AdapterSession(initial_state=dict(self._initial_state))

    async def send(self, message: str) -> AgentReply:
        scenario = self._require_active()
        if not isinstance(message, str) or not message:
            raise AdapterContractError("message must be a non-empty string")

        self._message_count += 1
        if self._message_count == 1:
            self._execute_required_tools(mutations=False)
            if (
                self.config.mutate_before_confirmation
                and self._has_challenge(MutationBeforeConfirmationChallenge)
            ):
                self._execute_required_tools(mutations=True)

        is_last_script_message = self._message_count >= len(self._script)
        if not is_last_script_message:
            return AgentReply(
                message="I have recorded that update and will continue safely.",
                completed=False,
                success_claim=None,
                final_state=dict(self._state),
                timed_out=False,
                error=None,
            )

        skip_mutation = (
            self.config.claim_success_without_mutation
            and self._has_challenge(FalseSuccessClaimChallenge)
        )
        if not skip_mutation:
            self._execute_required_tools(mutations=True)

        if (
            self.config.duplicate_mutations
            and self._has_challenge(DuplicateRequestChallenge)
            and not skip_mutation
        ):
            mutation = next(
                (
                    name
                    for name in scenario.expectations.required_tool_calls
                    if name in MUTATING_TOOL_NAMES
                ),
                None,
            )
            if mutation is not None:
                self._execute_tool(mutation)

        if self.config.call_forbidden_tools:
            for name in scenario.expectations.forbidden_tool_calls:
                self._execute_tool(name)

        claim_failure = (
            self.config.claim_failure_after_success
            and self._has_challenge(ToolSuccessClaimFailureChallenge)
            and not self._timed_out
        )
        if self._timed_out:
            success_claim = False
            message_text = "The appointment operation timed out."
            error = self._timeout_error()
        elif claim_failure:
            success_claim = False
            message_text = "I could not update the appointment."
            error = None
        elif skip_mutation:
            success_claim = True
            message_text = "The appointment was updated successfully."
            error = None
        else:
            success_claim = not any(call.error for call in self._unobserved)
            message_text = (
                "The appointment was updated successfully."
                if success_claim
                else "The appointment operation failed."
            )
            error = None

        return AgentReply(
            message=message_text,
            completed=True,
            success_claim=success_claim,
            final_state=dict(self._state),
            timed_out=self._timed_out,
            error=error,
        )

    async def observe_tool_calls(self) -> tuple[ToolCallObservation, ...]:
        self._require_active()
        observations = tuple(self._unobserved)
        self._unobserved.clear()
        return observations

    async def close(self) -> None:
        self._closed = True

    def _require_active(self) -> ScenarioDefinition:
        if self._scenario is None:
            raise AdapterStateError("adapter session has not been started")
        if self._closed:
            raise AdapterStateError("adapter session is closed")
        return self._scenario

    def _has_challenge(self, challenge_type: type[object]) -> bool:
        scenario = self._require_active()
        return any(isinstance(challenge, challenge_type) for challenge in scenario.challenges)

    def _execute_required_tools(self, *, mutations: bool) -> None:
        scenario = self._require_active()
        for name in scenario.expectations.required_tool_calls:
            is_mutation = name in MUTATING_TOOL_NAMES
            if is_mutation != mutations or name in self._executed_names:
                continue
            self._execute_tool(name)

    def _execute_tool(self, name: str) -> None:
        scenario = self._require_active()
        booking_id = str(self._state["booking_id"])
        is_mutation = name in MUTATING_TOOL_NAMES
        timeout = next(
            (
                challenge
                for challenge in scenario.challenges
                if isinstance(challenge, BackendTimeoutChallenge)
                and challenge.backend_timeout_on_tool == name
            ),
            None,
        )
        if timeout is not None:
            duration_ms = timeout.timeout_ms or scenario.expectations.timeout_ms
            observation = ToolCallObservation(
                name=name,
                arguments=self._tool_arguments(name, booking_id),
                result=None,
                error="timeout",
                is_mutation=is_mutation,
                duration_ms=duration_ms,
            )
            self._timed_out = True
        elif name == "lookup_booking":
            observation = ToolCallObservation(
                name=name,
                arguments={"booking_id": booking_id},
                result={"booking": dict(self._state), "matches": 1},
                error=None,
                is_mutation=False,
                duration_ms=5,
            )
        elif name == "list_available_slots":
            requested = scenario.goal.requested_slot
            slots = [requested] if requested else []
            observation = ToolCallObservation(
                name=name,
                arguments={"booking_id": booking_id},
                result={"slots": slots},
                error=None,
                is_mutation=False,
                duration_ms=7,
            )
        elif name == "update_booking":
            slot = scenario.goal.requested_slot or str(self._state["slot"])
            self._state.update(
                {
                    "slot": slot,
                    "status": "confirmed",
                    "booking_updated": True,
                }
            )
            observation = ToolCallObservation(
                name=name,
                arguments={"booking_id": booking_id, "slot": slot},
                result=dict(self._state),
                error=None,
                is_mutation=True,
                duration_ms=10,
            )
        elif name == "cancel_booking":
            self._state.update({"status": "cancelled", "booking_updated": True})
            observation = ToolCallObservation(
                name=name,
                arguments={"booking_id": booking_id},
                result=dict(self._state),
                error=None,
                is_mutation=True,
                duration_ms=10,
            )
        elif name == "create_duplicate_booking":
            observation = ToolCallObservation(
                name=name,
                arguments={"booking_id": booking_id},
                result={"duplicate_created": True},
                error=None,
                is_mutation=True,
                duration_ms=10,
            )
        else:
            observation = ToolCallObservation(
                name=name,
                arguments={"booking_id": booking_id},
                result=None,
                error="unsupported_tool",
                is_mutation=is_mutation,
                duration_ms=1,
            )
        self._executed_names.append(name)
        self._unobserved.append(observation)

    def _tool_arguments(self, name: str, booking_id: str) -> dict[str, object]:
        scenario = self._require_active()
        arguments: dict[str, object] = {"booking_id": booking_id}
        if name == "update_booking":
            arguments["slot"] = scenario.goal.requested_slot or self._state["slot"]
        return arguments

    def _timeout_error(self) -> str:
        scenario = self._require_active()
        challenge = next(
            (
                item
                for item in scenario.challenges
                if isinstance(item, BackendTimeoutChallenge)
            ),
            None,
        )
        tool_name = challenge.backend_timeout_on_tool if challenge else "unknown"
        return f"tool_timeout:{tool_name}"


__all__ = [
    "DEFAULT_BOOKING_ID",
    "DEFAULT_SLOT",
    "DETERMINISTIC_RANDOM_SEED",
    "MOCK_BACKEND_VERSION",
    "MockAgentAdapter",
    "MockAgentConfig",
    "TOOL_CONTRACT_VERSION",
]
