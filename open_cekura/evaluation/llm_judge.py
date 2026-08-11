"""Optional, injected-client LLM judge contracts."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from typing import Annotated, Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from open_cekura.domain.enums import EvaluationStatus
from open_cekura.domain.ids import stable_id
from open_cekura.domain.models import EvaluationResult, StableIdentifier

from .base import EvaluationContext


LLM_JUDGE_EVALUATOR_ID = "llm_judge.v1"
NonEmptyString = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=1000),
]
EnvironmentVariableName = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=1,
        max_length=200,
        pattern=r"^[A-Za-z_][A-Za-z0-9_]*$",
    ),
]
Sha256Digest = Annotated[
    str,
    StringConstraints(strict=True, pattern=r"^[0-9a-f]{64}$"),
]


class JudgeContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, validate_default=True)


class LLMJudgeConfig(JudgeContract):
    provider: NonEmptyString
    model: NonEmptyString
    credential_env_var: EnvironmentVariableName
    prompt_version: NonEmptyString
    judge_version: NonEmptyString
    temperature: float = Field(strict=True, ge=0.0, le=2.0)
    threshold: float = Field(strict=True, ge=0.0, le=1.0)


class LLMJudgeOutcome(JudgeContract):
    score: float = Field(strict=True, ge=0.0, le=1.0)
    reason_codes: list[StableIdentifier] = Field(default_factory=list)
    evidence_refs: list[NonEmptyString] = Field(default_factory=list)


class LLMJudgeRequest(JudgeContract):
    provider: NonEmptyString
    model: NonEmptyString
    prompt_version: NonEmptyString
    judge_version: NonEmptyString
    temperature: float = Field(strict=True, ge=0.0, le=2.0)
    request_digest: Sha256Digest


class LLMJudgeClient(Protocol):
    def judge(
        self,
        *,
        context: EvaluationContext,
        request: LLMJudgeRequest,
        credential: str,
    ) -> LLMJudgeOutcome | Mapping[str, Any]: ...


class LLMJudgeAdapter:
    """Fail-closed optional judge that performs I/O only through its client."""

    __slots__ = ("_client", "_config")

    def __init__(self, config: LLMJudgeConfig, client: LLMJudgeClient) -> None:
        self._config = config
        self._client = client

    def evaluate(
        self,
        context: EvaluationContext,
        *,
        environ: Mapping[str, str] | None = None,
    ) -> EvaluationResult:
        config_digest = _canonical_digest(self._config.model_dump(mode="json"))
        request_digest = _canonical_digest(
            {
                "config_digest": config_digest,
                "context": context.model_dump(mode="json"),
                "evaluator_id": LLM_JUDGE_EVALUATOR_ID,
            }
        )
        request = LLMJudgeRequest(
            provider=self._config.provider,
            model=self._config.model,
            prompt_version=self._config.prompt_version,
            judge_version=self._config.judge_version,
            temperature=self._config.temperature,
            request_digest=request_digest,
        )
        provenance: dict[str, Any] = {
            "provider": self._config.provider,
            "model": self._config.model,
            "prompt_version": self._config.prompt_version,
            "judge_version": self._config.judge_version,
            "temperature": self._config.temperature,
            "config_digest": config_digest,
            "request_digest": request_digest,
        }

        environment = os.environ if environ is None else environ
        credential = environment.get(self._config.credential_env_var)
        if not isinstance(credential, str) or not credential.strip():
            return self._result(
                context,
                status=EvaluationStatus.SKIPPED,
                score=None,
                threshold=None,
                reason_codes=["judge_credentials_missing"],
                evidence_refs=[],
                metadata=provenance,
            )

        try:
            raw_outcome = self._client.judge(
                context=context,
                request=request,
                credential=credential,
            )
        except TimeoutError:
            return self._error_result(context, provenance, "provider_timeout")
        except Exception:
            return self._error_result(context, provenance, "provider_error")

        try:
            outcome = LLMJudgeOutcome.model_validate(raw_outcome)
        except (TypeError, ValidationError):
            return self._error_result(context, provenance, "invalid_response")

        passed = outcome.score >= self._config.threshold
        reason_codes = list(outcome.reason_codes)
        if not passed and not reason_codes:
            reason_codes.append("judge_score_below_threshold")
        return self._result(
            context,
            status=EvaluationStatus.PASS if passed else EvaluationStatus.FAIL,
            score=outcome.score,
            threshold=self._config.threshold,
            reason_codes=reason_codes,
            evidence_refs=list(outcome.evidence_refs),
            metadata=provenance,
        )

    def _error_result(
        self,
        context: EvaluationContext,
        provenance: Mapping[str, Any],
        category: str,
    ) -> EvaluationResult:
        metadata = dict(provenance)
        metadata["error_category"] = category
        reason = {
            "invalid_response": "judge_response_invalid",
            "provider_error": "judge_provider_error",
            "provider_timeout": "judge_provider_timeout",
        }[category]
        return self._result(
            context,
            status=EvaluationStatus.ERROR,
            score=None,
            threshold=None,
            reason_codes=[reason],
            evidence_refs=[],
            metadata=metadata,
        )

    @staticmethod
    def _result(
        context: EvaluationContext,
        *,
        status: EvaluationStatus,
        score: float | None,
        threshold: float | None,
        reason_codes: list[str],
        evidence_refs: list[str],
        metadata: dict[str, Any],
    ) -> EvaluationResult:
        return EvaluationResult(
            schema_version=1,
            id=stable_id("evr", context.run_id, LLM_JUDGE_EVALUATOR_ID),
            run_id=context.run_id,
            evaluator_id=LLM_JUDGE_EVALUATOR_ID,
            status=status,
            score=score,
            threshold=threshold,
            reason_codes=reason_codes,
            evidence_refs=evidence_refs,
            metadata=metadata,
            mis_evaluation_id=None,
            created_at=context.evaluated_at,
        )


def _canonical_digest(payload: Any) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


__all__ = [
    "LLMJudgeAdapter",
    "LLMJudgeClient",
    "LLMJudgeConfig",
    "LLMJudgeOutcome",
    "LLMJudgeRequest",
    "LLM_JUDGE_EVALUATOR_ID",
]
