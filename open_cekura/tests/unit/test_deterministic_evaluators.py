from __future__ import annotations

from datetime import datetime, timezone

from open_cekura.domain.enums import EvaluationStatus, TurnRole
from open_cekura.domain.models import ConversationTurn, ObservedToolCall
from open_cekura.evaluation.base import EvaluationContext, TimeoutObservation
from open_cekura.evaluation.rules import EvaluatorSpec, evaluate_deterministic
from open_cekura.scenarios.schema import ScenarioDefinition


NOW = datetime(2026, 8, 11, 14, 0, tzinfo=timezone.utc)
RUN_ID = "ocrun_evaluator_fixture"


def scenario(**expectation_updates: object) -> ScenarioDefinition:
    expectations = {
        "required_tool_calls": ["lookup_booking", "update_booking"],
        "forbidden_tool_calls": ["create_duplicate_booking"],
        "must_confirm_before_mutation": True,
        "final_state": {"booking_updated": True, "booking": {"slot": "2026-08-20T10:00:00Z"}},
        "max_turns": 8,
        "timeout_ms": 5000,
    }
    expectations.update(expectation_updates)
    return ScenarioDefinition.model_validate(
        {
            "schema_version": 1,
            "id": "appointment.evaluator-fixture",
            "name": "Evaluator fixture",
            "persona": {"language": "en-US", "tone": "patient", "verbosity": "short"},
            "initial_message": "Move booking booking-123 to August 20 at 10 AM.",
            "goal": {
                "type": "reschedule",
                "booking_id": "booking-123",
                "requested_slot": "2026-08-20T10:00:00Z",
            },
            "challenges": [],
            "expectations": expectations,
            "tags": ["unit"],
        }
    )


def turn(index: int, role: TurnRole, content: str) -> ConversationTurn:
    return ConversationTurn(
        schema_version=1,
        id=f"octurn_{index}",
        run_id=RUN_ID,
        turn_index=index,
        role=role,
        content=content,
        created_at=NOW,
    )


def tool_call(
    call_id: str,
    turn_id: str,
    name: str,
    *,
    arguments: dict[str, object],
    result: object,
    is_mutation: bool,
) -> ObservedToolCall:
    return ObservedToolCall(
        schema_version=1,
        id=call_id,
        run_id=RUN_ID,
        turn_id=turn_id,
        name=name,
        arguments=arguments,
        result=result,
        error=None,
        is_mutation=is_mutation,
        duration_ms=10,
        mis_tool_call_id=None,
        created_at=NOW,
    )


def passing_context() -> EvaluationContext:
    turns = [
        turn(0, TurnRole.USER, "Move booking booking-123 to August 20 at 10 AM."),
        turn(1, TurnRole.ASSISTANT, "I found the booking. Shall I confirm this change?"),
        turn(2, TurnRole.USER, "Yes, confirm the change."),
        turn(3, TurnRole.ASSISTANT, "Done. Your booking is updated."),
    ]
    calls = [
        tool_call(
            "octool_lookup",
            "octurn_1",
            "lookup_booking",
            arguments={"booking_id": "booking-123"},
            result={"found": True},
            is_mutation=False,
        ),
        tool_call(
            "octool_update",
            "octurn_3",
            "update_booking",
            arguments={"booking_id": "booking-123", "slot": "2026-08-20T10:00:00Z"},
            result={"updated": True},
            is_mutation=True,
        ),
    ]
    return EvaluationContext(
        scenario=scenario(),
        run_id=RUN_ID,
        turns=turns,
        tool_calls=calls,
        initial_state={"booking_updated": False, "booking": {"slot": "2026-08-15T09:00:00Z"}},
        final_state={"booking_updated": True, "booking": {"slot": "2026-08-20T10:00:00Z"}},
        agent_claimed_success=True,
        timeout_observations=[],
        evaluated_at=NOW,
    )


def results_by_id(context: EvaluationContext):
    results = evaluate_deterministic(context)
    assert [item.evaluator_id for item in results] == [
        "task_success.v1",
        "required_tool_calls.v1",
        "forbidden_tool_calls.v1",
        "duplicate_mutation.v1",
        "confirmation_before_mutation.v1",
        "final_state_match.v1",
        "turn_count_limit.v1",
        "timeout.v1",
    ]
    return {item.evaluator_id: item for item in results}


def test_all_required_deterministic_evaluators_pass_with_explainable_results() -> None:
    results = results_by_id(passing_context())

    assert all(item.status is EvaluationStatus.PASS for item in results.values())
    assert all(item.score == 1.0 and item.threshold == 1.0 for item in results.values())
    assert all(item.run_id == RUN_ID for item in results.values())
    assert len({item.id for item in results.values()}) == 8


def test_required_and_forbidden_tool_failures_point_to_expectation_and_call() -> None:
    context = passing_context()
    forbidden = tool_call(
        "octool_forbidden",
        "octurn_3",
        "create_duplicate_booking",
        arguments={"booking_id": "booking-123"},
        result={"created": True},
        is_mutation=True,
    )
    context = context.model_copy(
        update={"tool_calls": [context.tool_calls[0], forbidden]}
    )

    results = results_by_id(context)
    required = results["required_tool_calls.v1"]
    forbidden_result = results["forbidden_tool_calls.v1"]

    assert required.status is EvaluationStatus.FAIL
    assert required.reason_codes == ["required_tool_call_missing"]
    assert "expectation:required_tool_calls[1]" in required.evidence_refs
    assert required.metadata["missing_tools"] == ["update_booking"]
    assert forbidden_result.status is EvaluationStatus.FAIL
    assert forbidden_result.reason_codes == ["forbidden_tool_call_observed"]
    assert "tool_call:octool_forbidden" in forbidden_result.evidence_refs
    assert "expectation:forbidden_tool_calls[0]" in forbidden_result.evidence_refs


def test_duplicate_mutation_identifies_first_and_duplicate_tool_calls() -> None:
    context = passing_context()
    duplicate = context.tool_calls[1].model_copy(update={"id": "octool_update_duplicate"})
    context = context.model_copy(update={"tool_calls": [*context.tool_calls, duplicate]})

    result = results_by_id(context)["duplicate_mutation.v1"]

    assert result.status is EvaluationStatus.FAIL
    assert result.reason_codes == ["duplicate_mutation"]
    assert "tool_call:octool_update" in result.evidence_refs
    assert "tool_call:octool_update_duplicate" in result.evidence_refs
    assert result.metadata["duplicates"][0]["tool_name"] == "update_booking"


def test_confirmation_must_be_a_user_turn_before_each_mutation() -> None:
    context = passing_context()
    early_mutation = context.tool_calls[1].model_copy(
        update={"id": "octool_early_update", "turn_id": "octurn_1"}
    )
    context = context.model_copy(
        update={"turns": context.turns[:2], "tool_calls": [context.tool_calls[0], early_mutation]}
    )

    result = results_by_id(context)["confirmation_before_mutation.v1"]

    assert result.status is EvaluationStatus.FAIL
    assert result.reason_codes == ["confirmation_missing_before_mutation"]
    assert "tool_call:octool_early_update" in result.evidence_refs
    assert "turn:octurn_1" in result.evidence_refs
    assert result.metadata["violations"][0]["tool_name"] == "update_booking"


def test_final_state_reports_each_mismatched_json_path() -> None:
    context = passing_context().model_copy(
        update={"final_state": {"booking_updated": False, "booking": {"slot": "old"}}}
    )

    result = results_by_id(context)["final_state_match.v1"]

    assert result.status is EvaluationStatus.FAIL
    assert result.reason_codes == ["final_state_mismatch"]
    assert {item["path"] for item in result.metadata["mismatches"]} == {
        "/booking_updated",
        "/booking/slot",
    }
    assert "final_state:/booking_updated" in result.evidence_refs
    assert "final_state:/booking/slot" in result.evidence_refs


def test_task_success_rejects_both_claim_state_mismatch_directions() -> None:
    false_failure = passing_context().model_copy(update={"agent_claimed_success": False})
    false_success = passing_context().model_copy(
        update={
            "agent_claimed_success": True,
            "final_state": {"booking_updated": False, "booking": {"slot": "old"}},
        }
    )

    false_failure_result = results_by_id(false_failure)["task_success.v1"]
    false_success_result = results_by_id(false_success)["task_success.v1"]

    assert false_failure_result.status is EvaluationStatus.FAIL
    assert "agent_claims_failure_after_success" in false_failure_result.reason_codes
    assert false_success_result.status is EvaluationStatus.FAIL
    assert "agent_claims_success_without_state" in false_success_result.reason_codes
    assert "turn:octurn_3" in false_failure_result.evidence_refs


def test_turn_limit_and_timeout_keep_measured_facts() -> None:
    context = passing_context()
    extra_turns = [turn(index, TurnRole.USER, f"extra {index}") for index in range(4, 10)]
    timeout = TimeoutObservation(
        operation="tool",
        tool_name="lookup_booking",
        turn_id="octurn_1",
        configured_ms=5000,
        measured_ms=5001,
    )
    context = context.model_copy(
        update={"turns": [*context.turns, *extra_turns], "timeout_observations": [timeout]}
    )

    results = results_by_id(context)
    turn_result = results["turn_count_limit.v1"]
    timeout_result = results["timeout.v1"]

    assert turn_result.status is EvaluationStatus.FAIL
    assert turn_result.metadata == {"limit": 8, "observed": 10}
    assert timeout_result.status is EvaluationStatus.FAIL
    assert timeout_result.reason_codes == ["timeout_observed"]
    assert timeout_result.metadata["timeouts"][0]["measured_ms"] == 5001
    assert "tool:lookup_booking" in timeout_result.evidence_refs


def test_deterministic_results_are_byte_identical_for_identical_context() -> None:
    first = evaluate_deterministic(passing_context())
    second = evaluate_deterministic(passing_context())

    assert [item.canonical_json_bytes() for item in first] == [
        item.canonical_json_bytes() for item in second
    ]


def test_evaluator_exception_is_error_and_never_passes() -> None:
    def broken_evaluator(_context: EvaluationContext):
        raise RuntimeError("sensitive implementation detail")

    results = evaluate_deterministic(
        passing_context(),
        evaluators=(EvaluatorSpec("broken_rule.v1", broken_evaluator),),
    )

    assert len(results) == 1
    result = results[0]
    assert result.evaluator_id == "broken_rule.v1"
    assert result.status is EvaluationStatus.ERROR
    assert result.score is None
    assert result.threshold is None
    assert result.reason_codes == ["evaluator_error"]
    assert result.metadata == {"error_type": "RuntimeError"}
    assert "sensitive implementation detail" not in result.canonical_json_bytes().decode("utf-8")
