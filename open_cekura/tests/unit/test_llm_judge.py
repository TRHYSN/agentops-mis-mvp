from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from open_cekura.domain.enums import EvaluationStatus, TurnRole
from open_cekura.domain.models import ConversationTurn
from open_cekura.evaluation.base import EvaluationContext
from open_cekura.evaluation.llm_judge import (
    LLMJudgeAdapter,
    LLMJudgeConfig,
    LLMJudgeOutcome,
)
from open_cekura.scenarios.schema import ScenarioDefinition


NOW = datetime(2026, 8, 11, 16, 0, tzinfo=timezone.utc)
RUN_ID = "ocrun_llm_judge_fixture"
SECRET = "sk-test-secret-that-must-never-be-retained"


def judge_context() -> EvaluationContext:
    scenario = ScenarioDefinition.model_validate(
        {
            "schema_version": 1,
            "id": "appointment.llm-judge-fixture",
            "name": "LLM judge fixture",
            "persona": {
                "language": "en-US",
                "tone": "calm",
                "verbosity": "short",
            },
            "initial_message": "Look up booking-123.",
            "goal": {"type": "lookup", "booking_id": "booking-123"},
            "challenges": [],
            "expectations": {
                "required_tool_calls": ["lookup_booking"],
                "forbidden_tool_calls": ["update_booking", "cancel_booking"],
                "must_confirm_before_mutation": True,
                "final_state": {
                    "booking_id": "booking-123",
                    "slot": "2026-08-20T10:00:00Z",
                    "status": "confirmed",
                    "booking_updated": False,
                },
                "max_turns": 4,
                "timeout_ms": 5000,
            },
            "tags": ["unit"],
        }
    )
    turns = [
        ConversationTurn(
            schema_version=1,
            id="octurn_judge_0",
            run_id=RUN_ID,
            turn_index=0,
            role=TurnRole.USER,
            content="Look up booking-123.",
            created_at=NOW,
        ),
        ConversationTurn(
            schema_version=1,
            id="octurn_judge_1",
            run_id=RUN_ID,
            turn_index=1,
            role=TurnRole.ASSISTANT,
            content="The booking is confirmed for August 20 at 10 AM.",
            created_at=NOW,
        ),
    ]
    state = {
        "booking_id": "booking-123",
        "slot": "2026-08-20T10:00:00Z",
        "status": "confirmed",
        "booking_updated": False,
    }
    return EvaluationContext(
        scenario=scenario,
        run_id=RUN_ID,
        turns=turns,
        tool_calls=[],
        initial_state=state,
        final_state=state,
        agent_claimed_success=True,
        timeout_observations=[],
        evaluated_at=NOW,
    )


def judge_config() -> LLMJudgeConfig:
    return LLMJudgeConfig(
        provider="test-provider",
        model="test-model-1",
        credential_env_var="TEST_JUDGE_API_KEY",
        prompt_version="appointment-quality.v1",
        judge_version="open-cekura-judge.v1",
        temperature=0.0,
        threshold=0.75,
    )


class MustNotCallClient:
    def judge(self, **_: Any) -> LLMJudgeOutcome:
        raise AssertionError("judge client must not be called without credentials")


class PassingClient:
    def __init__(self) -> None:
        self.calls = 0

    def judge(self, **kwargs: Any) -> LLMJudgeOutcome:
        self.calls += 1
        assert kwargs["credential"] == SECRET
        request = kwargs["request"]
        assert request.provider == "test-provider"
        assert request.model == "test-model-1"
        assert request.prompt_version == "appointment-quality.v1"
        assert request.judge_version == "open-cekura-judge.v1"
        assert request.temperature == 0.0
        assert len(request.request_digest) == 64
        return LLMJudgeOutcome(
            score=0.8,
            reason_codes=[],
            evidence_refs=["turn:octurn_judge_1"],
        )


class FailingClient:
    def judge(self, **kwargs: Any) -> LLMJudgeOutcome:
        assert kwargs["credential"] == SECRET
        raise RuntimeError(f"provider failed while using {SECRET}")


class InvalidResponseClient:
    def judge(self, **_: Any) -> dict[str, object]:
        return {"score": 2.0, "reason_codes": [], "evidence_refs": []}


class LowScoreClient:
    def judge(self, **_: Any) -> LLMJudgeOutcome:
        return LLMJudgeOutcome(score=0.25, reason_codes=[], evidence_refs=[])


def test_missing_credentials_skip_without_calling_provider_or_inventing_score() -> None:
    result = LLMJudgeAdapter(judge_config(), MustNotCallClient()).evaluate(
        judge_context(),
        environ={},
    )

    assert result.status is EvaluationStatus.SKIPPED
    assert result.score is None
    assert result.threshold is None
    assert result.reason_codes == ["judge_credentials_missing"]
    assert result.metadata == {
        "provider": "test-provider",
        "model": "test-model-1",
        "prompt_version": "appointment-quality.v1",
        "judge_version": "open-cekura-judge.v1",
        "temperature": 0.0,
        "config_digest": result.metadata["config_digest"],
        "request_digest": result.metadata["request_digest"],
    }
    assert len(result.metadata["config_digest"]) == 64
    assert len(result.metadata["request_digest"]) == 64


def test_configured_judge_records_reproducible_provenance_without_secret() -> None:
    client = PassingClient()
    adapter = LLMJudgeAdapter(judge_config(), client)

    result = adapter.evaluate(
        judge_context(),
        environ={"TEST_JUDGE_API_KEY": SECRET},
    )

    assert client.calls == 1
    assert result.status is EvaluationStatus.PASS
    assert result.score == 0.8
    assert result.threshold == 0.75
    assert result.evidence_refs == ["turn:octurn_judge_1"]
    assert result.metadata["provider"] == "test-provider"
    assert result.metadata["model"] == "test-model-1"
    assert result.metadata["prompt_version"] == "appointment-quality.v1"
    assert result.metadata["judge_version"] == "open-cekura-judge.v1"
    assert result.metadata["temperature"] == 0.0
    serialized = json.dumps(result.model_dump(mode="json"), sort_keys=True)
    assert SECRET not in serialized
    assert SECRET not in repr(adapter)


def test_provider_error_is_error_not_pass_and_does_not_leak_exception_text() -> None:
    result = LLMJudgeAdapter(judge_config(), FailingClient()).evaluate(
        judge_context(),
        environ={"TEST_JUDGE_API_KEY": SECRET},
    )

    assert result.status is EvaluationStatus.ERROR
    assert result.score is None
    assert result.threshold is None
    assert result.reason_codes == ["judge_provider_error"]
    assert result.metadata["error_category"] == "provider_error"
    assert SECRET not in json.dumps(result.model_dump(mode="json"), sort_keys=True)


def test_invalid_provider_response_is_fail_closed_as_error() -> None:
    result = LLMJudgeAdapter(judge_config(), InvalidResponseClient()).evaluate(
        judge_context(),
        environ={"TEST_JUDGE_API_KEY": SECRET},
    )

    assert result.status is EvaluationStatus.ERROR
    assert result.score is None
    assert result.threshold is None
    assert result.reason_codes == ["judge_response_invalid"]
    assert result.metadata["error_category"] == "invalid_response"


def test_score_below_threshold_is_fail_with_stable_reason() -> None:
    result = LLMJudgeAdapter(judge_config(), LowScoreClient()).evaluate(
        judge_context(),
        environ={"TEST_JUDGE_API_KEY": SECRET},
    )

    assert result.status is EvaluationStatus.FAIL
    assert result.score == 0.25
    assert result.threshold == 0.75
    assert result.reason_codes == ["judge_score_below_threshold"]
