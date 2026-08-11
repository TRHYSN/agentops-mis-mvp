"""Deterministic user-message scripts derived from typed Scenario challenges."""

from __future__ import annotations

from open_cekura.scenarios.schema import (
    AmbiguousIdentityChallenge,
    ChangeConstraintChallenge,
    DuplicateRequestChallenge,
    InterruptChallenge,
    ScenarioDefinition,
    UnavailableSlotChallenge,
)


MUTATING_TOOL_NAMES = frozenset(
    {"update_booking", "cancel_booking", "create_duplicate_booking"}
)
CONFIRMATION_MESSAGE = "[confirmation] Yes, I confirm the requested mutation."


def user_messages_for(scenario: ScenarioDefinition) -> tuple[str, ...]:
    """Build a stable user script without branching on scenario identity."""

    scheduled: list[tuple[int, int, str]] = []
    for position, challenge in enumerate(scenario.challenges):
        if isinstance(challenge, InterruptChallenge):
            scheduled.append(
                (
                    challenge.interrupt_after_turn,
                    position,
                    "[interruption] Pause; I need to add one detail before you continue.",
                )
            )
        elif isinstance(challenge, ChangeConstraintChallenge):
            slot = scenario.goal.requested_slot or "the newly requested slot"
            scheduled.append(
                (
                    challenge.change_constraint_after_turn,
                    position,
                    f"[constraint-change] Use {slot} as the final appointment slot.",
                )
            )
        elif isinstance(challenge, AmbiguousIdentityChallenge):
            booking_id = scenario.goal.booking_id or "the requested booking"
            scheduled.append(
                (
                    1,
                    position,
                    f"[identity-clarification] The booking identifier is {booking_id}.",
                )
            )
        elif isinstance(challenge, UnavailableSlotChallenge):
            scheduled.append(
                (
                    1,
                    position,
                    "[unavailable-slot] Choose the next available appointment slot.",
                )
            )
        elif isinstance(challenge, DuplicateRequestChallenge):
            scheduled.append(
                (
                    challenge.duplicate_request_after_turn,
                    position,
                    f"[duplicate-request] {scenario.initial_message}",
                )
            )

    messages = [scenario.initial_message]
    messages.extend(item[2] for item in sorted(scheduled))
    needs_confirmation = (
        scenario.expectations.must_confirm_before_mutation
        and bool(MUTATING_TOOL_NAMES.intersection(scenario.expectations.required_tool_calls))
    )
    if needs_confirmation:
        messages.append(CONFIRMATION_MESSAGE)
    return tuple(messages)


__all__ = ["CONFIRMATION_MESSAGE", "MUTATING_TOOL_NAMES", "user_messages_for"]
