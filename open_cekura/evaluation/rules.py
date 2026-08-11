"""Versioned deterministic evaluators for OpenCekura v0."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from open_cekura.domain.enums import EvaluationStatus, TurnRole
from open_cekura.domain.ids import stable_id
from open_cekura.domain.models import EvaluationResult, ObservedToolCall
from open_cekura.scenarios.schema import BackendTimeoutChallenge

from .base import EvaluationContext


Evaluator = Callable[[EvaluationContext], EvaluationResult]
_CONFIRMATION = re.compile(
    r"\b(?:yes|confirm(?:ed)?|go\s+ahead|please\s+do|do\s+it|proceed)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class EvaluatorSpec:
    evaluator_id: str
    evaluator: Evaluator

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}", self.evaluator_id):
            raise ValueError("evaluator_id must be a stable identifier")
        if not callable(self.evaluator):
            raise TypeError("evaluator must be callable")


def _result(
    context: EvaluationContext,
    evaluator_id: str,
    *,
    passed: bool,
    reason_codes: list[str] | None = None,
    evidence_refs: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> EvaluationResult:
    return EvaluationResult(
        schema_version=1,
        id=stable_id("evr", context.run_id, evaluator_id),
        run_id=context.run_id,
        evaluator_id=evaluator_id,
        status=EvaluationStatus.PASS if passed else EvaluationStatus.FAIL,
        score=1.0 if passed else 0.0,
        threshold=1.0,
        reason_codes=reason_codes or [],
        evidence_refs=evidence_refs or [],
        metadata=metadata or {},
        mis_evaluation_id=None,
        created_at=context.evaluated_at,
    )


def _join_path(path: str, key: str) -> str:
    escaped = key.replace("~", "~0").replace("/", "~1")
    return f"{path}/{escaped}"


def _state_mismatches(
    expected: Any,
    observed: Any,
    path: str = "",
) -> list[dict[str, Any]]:
    if isinstance(expected, dict):
        if not isinstance(observed, dict):
            return [{"path": path or "/", "expected": expected, "observed": observed}]
        mismatches: list[dict[str, Any]] = []
        for key in sorted(expected):
            child_path = _join_path(path, key)
            if key not in observed:
                mismatches.append(
                    {
                        "path": child_path,
                        "expected": expected[key],
                        "observed": None,
                        "missing": True,
                    }
                )
            else:
                mismatches.extend(
                    _state_mismatches(expected[key], observed[key], child_path)
                )
        return mismatches
    if isinstance(expected, list):
        if not isinstance(observed, list) or len(expected) != len(observed):
            return [{"path": path or "/", "expected": expected, "observed": observed}]
        mismatches: list[dict[str, Any]] = []
        for index, child in enumerate(expected):
            mismatches.extend(_state_mismatches(child, observed[index], f"{path}/{index}"))
        return mismatches
    if expected != observed:
        return [{"path": path or "/", "expected": expected, "observed": observed}]
    return []


def _final_assistant_turn_ref(context: EvaluationContext) -> list[str]:
    for turn in reversed(context.turns):
        if turn.role is TurnRole.ASSISTANT:
            return [f"turn:{turn.id}"]
    return []


def evaluate_task_success(context: EvaluationContext) -> EvaluationResult:
    evaluator_id = "task_success.v1"
    mismatches = _state_mismatches(
        context.scenario.expectations.final_state,
        context.final_state,
    )
    reasons: list[str] = []
    state_matches = not mismatches
    expected_success_claim = not any(
        isinstance(challenge, BackendTimeoutChallenge)
        for challenge in context.scenario.challenges
    )
    if context.agent_claimed_success is None:
        reasons.append("agent_outcome_missing")
    elif state_matches and context.agent_claimed_success != expected_success_claim:
        reasons.append("agent_claims_failure_after_success")
    elif not state_matches and context.agent_claimed_success:
        reasons.append("agent_claims_success_without_state")
    elif not state_matches:
        reasons.append("task_unsuccessful")
    evidence = _final_assistant_turn_ref(context)
    evidence.extend(f"final_state:{item['path']}" for item in mismatches)
    return _result(
        context,
        evaluator_id,
        passed=state_matches and context.agent_claimed_success is expected_success_claim,
        reason_codes=reasons,
        evidence_refs=evidence,
        metadata={
            "agent_claimed_success": context.agent_claimed_success,
            "expected_success_claim": expected_success_claim,
            "final_state_matches": state_matches,
            "mismatches": mismatches,
        },
    )


def evaluate_required_tool_calls(context: EvaluationContext) -> EvaluationResult:
    evaluator_id = "required_tool_calls.v1"
    observed = {call.name for call in context.tool_calls}
    missing = [
        {"index": index, "tool_name": name}
        for index, name in enumerate(context.scenario.expectations.required_tool_calls)
        if name not in observed
    ]
    return _result(
        context,
        evaluator_id,
        passed=not missing,
        reason_codes=[] if not missing else ["required_tool_call_missing"],
        evidence_refs=[
            f"expectation:required_tool_calls[{item['index']}]" for item in missing
        ],
        metadata={"missing_tools": [item["tool_name"] for item in missing]},
    )


def evaluate_forbidden_tool_calls(context: EvaluationContext) -> EvaluationResult:
    evaluator_id = "forbidden_tool_calls.v1"
    forbidden_indexes = {
        name: index
        for index, name in enumerate(context.scenario.expectations.forbidden_tool_calls)
    }
    violations = [
        {
            "tool_call_id": call.id,
            "turn_id": call.turn_id,
            "tool_name": call.name,
            "expectation_index": forbidden_indexes[call.name],
        }
        for call in context.tool_calls
        if call.name in forbidden_indexes
    ]
    evidence: list[str] = []
    for violation in violations:
        evidence.extend(
            [
                f"tool_call:{violation['tool_call_id']}",
                f"turn:{violation['turn_id']}",
                f"expectation:forbidden_tool_calls[{violation['expectation_index']}]",
            ]
        )
    return _result(
        context,
        evaluator_id,
        passed=not violations,
        reason_codes=[] if not violations else ["forbidden_tool_call_observed"],
        evidence_refs=evidence,
        metadata={"violations": violations},
    )


def _mutation_signature(call: ObservedToolCall) -> str:
    normalized_arguments = json.dumps(
        call.arguments,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"{call.name.casefold()}:{normalized_arguments}"


def evaluate_duplicate_mutation(context: EvaluationContext) -> EvaluationResult:
    evaluator_id = "duplicate_mutation.v1"
    first_by_signature: dict[str, ObservedToolCall] = {}
    duplicates: list[dict[str, Any]] = []
    for call in context.tool_calls:
        if not call.is_mutation or call.error is not None:
            continue
        signature = _mutation_signature(call)
        first = first_by_signature.get(signature)
        if first is None:
            first_by_signature[signature] = call
            continue
        duplicates.append(
            {
                "signature": signature,
                "tool_name": call.name,
                "first_tool_call_id": first.id,
                "first_turn_id": first.turn_id,
                "duplicate_tool_call_id": call.id,
                "duplicate_turn_id": call.turn_id,
            }
        )
    evidence: list[str] = []
    for duplicate in duplicates:
        evidence.extend(
            [
                f"tool_call:{duplicate['first_tool_call_id']}",
                f"turn:{duplicate['first_turn_id']}",
                f"tool_call:{duplicate['duplicate_tool_call_id']}",
                f"turn:{duplicate['duplicate_turn_id']}",
            ]
        )
    return _result(
        context,
        evaluator_id,
        passed=not duplicates,
        reason_codes=[] if not duplicates else ["duplicate_mutation"],
        evidence_refs=evidence,
        metadata={"duplicates": duplicates, "signature_version": evaluator_id},
    )


def _explicit_confirmation_turns(context: EvaluationContext) -> list[int]:
    return [
        turn.turn_index
        for turn in context.turns
        if turn.role is TurnRole.USER
        and turn.turn_index > 0
        and _CONFIRMATION.search(turn.content)
    ]


def evaluate_confirmation_before_mutation(context: EvaluationContext) -> EvaluationResult:
    evaluator_id = "confirmation_before_mutation.v1"
    if not context.scenario.expectations.must_confirm_before_mutation:
        return _result(
            context,
            evaluator_id,
            passed=True,
            metadata={"required": False, "violations": []},
        )
    turn_index_by_id = {turn.id: turn.turn_index for turn in context.turns}
    confirmation_indexes = _explicit_confirmation_turns(context)
    violations: list[dict[str, Any]] = []
    for call in context.tool_calls:
        if not call.is_mutation or call.error is not None:
            continue
        mutation_index = turn_index_by_id[call.turn_id]
        if not any(index < mutation_index for index in confirmation_indexes):
            violations.append(
                {
                    "tool_call_id": call.id,
                    "turn_id": call.turn_id,
                    "turn_index": mutation_index,
                    "tool_name": call.name,
                }
            )
    evidence: list[str] = []
    for violation in violations:
        evidence.extend(
            [
                f"tool_call:{violation['tool_call_id']}",
                f"turn:{violation['turn_id']}",
                "expectation:must_confirm_before_mutation",
            ]
        )
    return _result(
        context,
        evaluator_id,
        passed=not violations,
        reason_codes=[] if not violations else ["confirmation_missing_before_mutation"],
        evidence_refs=evidence,
        metadata={"required": True, "violations": violations},
    )


def evaluate_final_state_match(context: EvaluationContext) -> EvaluationResult:
    evaluator_id = "final_state_match.v1"
    mismatches = _state_mismatches(
        context.scenario.expectations.final_state,
        context.final_state,
    )
    return _result(
        context,
        evaluator_id,
        passed=not mismatches,
        reason_codes=[] if not mismatches else ["final_state_mismatch"],
        evidence_refs=[f"final_state:{item['path']}" for item in mismatches],
        metadata={"mismatches": mismatches},
    )


def evaluate_turn_count_limit(context: EvaluationContext) -> EvaluationResult:
    evaluator_id = "turn_count_limit.v1"
    observed = len(context.turns)
    limit = context.scenario.expectations.max_turns
    passed = observed <= limit
    return _result(
        context,
        evaluator_id,
        passed=passed,
        reason_codes=[] if passed else ["turn_count_limit_exceeded"],
        evidence_refs=[] if passed else ["expectation:max_turns", "artifact:transcript.json"],
        metadata={"limit": limit, "observed": observed},
    )


def evaluate_timeout(context: EvaluationContext) -> EvaluationResult:
    evaluator_id = "timeout.v1"
    timeout_rows = [item.model_dump(mode="json") for item in context.timeout_observations]
    expected_tools = {
        challenge.backend_timeout_on_tool
        for challenge in context.scenario.challenges
        if isinstance(challenge, BackendTimeoutChallenge)
    }
    observed_tools = {
        observation.tool_name
        for observation in context.timeout_observations
        if observation.operation == "tool" and observation.tool_name is not None
    }
    unexpected = [
        row
        for row in timeout_rows
        if row["operation"] != "tool" or row.get("tool_name") not in expected_tools
    ]
    missing_expected = sorted(expected_tools - observed_tools)
    evidence: list[str] = []
    for observation in context.timeout_observations:
        if observation.turn_id:
            evidence.append(f"turn:{observation.turn_id}")
        if observation.operation == "tool" and observation.tool_name:
            matching_calls = [
                call
                for call in context.tool_calls
                if call.name == observation.tool_name
                and (
                    observation.turn_id is None
                    or call.turn_id == observation.turn_id
                )
            ]
            evidence.extend(f"tool_call:{call.id}" for call in matching_calls)
        else:
            evidence.append("artifact:timing.json")
        evidence.append("expectation:timeout_ms")
    evidence = list(dict.fromkeys(evidence))
    passed = not unexpected and not missing_expected
    reasons: list[str] = []
    if unexpected:
        reasons.append("timeout_observed")
    if missing_expected:
        reasons.append("expected_timeout_missing")
    return _result(
        context,
        evaluator_id,
        passed=passed,
        reason_codes=reasons,
        evidence_refs=evidence,
        metadata={
            "timeouts": timeout_rows,
            "expected_timeout_tools": sorted(expected_tools),
            "unexpected_timeouts": unexpected,
            "missing_expected_timeouts": missing_expected,
        },
    )


DETERMINISTIC_EVALUATORS: tuple[EvaluatorSpec, ...] = (
    EvaluatorSpec("task_success.v1", evaluate_task_success),
    EvaluatorSpec("required_tool_calls.v1", evaluate_required_tool_calls),
    EvaluatorSpec("forbidden_tool_calls.v1", evaluate_forbidden_tool_calls),
    EvaluatorSpec("duplicate_mutation.v1", evaluate_duplicate_mutation),
    EvaluatorSpec(
        "confirmation_before_mutation.v1",
        evaluate_confirmation_before_mutation,
    ),
    EvaluatorSpec("final_state_match.v1", evaluate_final_state_match),
    EvaluatorSpec("turn_count_limit.v1", evaluate_turn_count_limit),
    EvaluatorSpec("timeout.v1", evaluate_timeout),
)


def evaluate_deterministic(
    context: EvaluationContext,
    *,
    evaluators: tuple[EvaluatorSpec, ...] = DETERMINISTIC_EVALUATORS,
) -> list[EvaluationResult]:
    """Evaluate all v0 rules in a stable, documented order."""

    results: list[EvaluationResult] = []
    for spec in evaluators:
        try:
            results.append(spec.evaluator(context))
        except Exception as exc:
            results.append(
                EvaluationResult(
                    schema_version=1,
                    id=stable_id("evr", context.run_id, spec.evaluator_id),
                    run_id=context.run_id,
                    evaluator_id=spec.evaluator_id,
                    status=EvaluationStatus.ERROR,
                    score=None,
                    threshold=None,
                    reason_codes=["evaluator_error"],
                    evidence_refs=[],
                    metadata={"error_type": type(exc).__name__},
                    mis_evaluation_id=None,
                    created_at=context.evaluated_at,
                )
            )
    return results


__all__ = ["DETERMINISTIC_EVALUATORS", "EvaluatorSpec", "evaluate_deterministic"]
