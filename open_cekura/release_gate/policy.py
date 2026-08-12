"""Release Gate v1 computed only from persisted evaluation facts."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
import hashlib
import json
from types import MappingProxyType
from typing import Annotated, Literal, Mapping, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from open_cekura.domain.enums import EvaluationStatus, GateDecision
from open_cekura.domain.ids import stable_id
from open_cekura.domain.models import (
    GateFact,
    ReleaseGateDecision,
    Sha256Digest,
    StableIdentifier,
)
from open_cekura.evaluation.aggregation import CampaignMetrics
_RELEASE_GATE_V1_POLICY_VERSION = "release_gate.v1"
POLICY_VERSION = _RELEASE_GATE_V1_POLICY_VERSION
_RELEASE_GATE_V1_REQUIRED_EVALUATORS = frozenset(
    {
        "task_success.v1",
        "required_tool_calls.v1",
        "forbidden_tool_calls.v1",
        "duplicate_mutation.v1",
        "confirmation_before_mutation.v1",
        "final_state_match.v1",
        "turn_count_limit.v1",
        "timeout.v1",
    }
)
_RELEASE_GATE_V1_ZERO_TOLERANCE = MappingProxyType(
    {
    "forbidden_tool_calls.v1": (
        "zero_tolerance.forbidden_tool_calls.v1",
        "forbidden tool call",
    ),
    "duplicate_mutation.v1": (
        "zero_tolerance.duplicate_mutation.v1",
        "duplicate mutation",
    ),
    "confirmation_before_mutation.v1": (
        "zero_tolerance.confirmation_before_mutation.v1",
        "mutation before confirmation",
    ),
    }
)


class ReleaseGateError(ValueError):
    """Release-gate input, policy, or computation is invalid."""

    code = "release_gate_error"


class IncomparableCampaignsError(ReleaseGateError):
    """Baseline and candidate cannot be compared under one frozen contract."""

    code = "incomparable_campaigns"
    comparison_status = "INCOMPARABLE"


class _GateContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class CampaignGateInput(_GateContract):
    campaign_id: StableIdentifier
    metrics: CampaignMetrics
    scenario_by_run: dict[StableIdentifier, StableIdentifier]
    scenario_ids: list[StableIdentifier]
    scenario_sha256_by_id: dict[StableIdentifier, Sha256Digest]
    evaluator_versions: list[StableIdentifier]
    scenario_suite_sha256: Sha256Digest
    scenario_schema_version: Literal[1]
    evaluator_policy_sha256: Sha256Digest
    mock_backend_version: StableIdentifier
    tool_contract_version: StableIdentifier
    deterministic_mode: bool
    random_seed: Annotated[int, Field(strict=True, ge=0)]
    evidence_verified: bool
    evidence_refs: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def graph_is_complete(self) -> "CampaignGateInput":
        group_run_ids = {group.run_id for group in self.metrics.result_groups}
        if not group_run_ids or self.metrics.run_count != len(group_run_ids):
            raise ValueError("campaign metrics must contain at least one unique run")
        if set(self.scenario_by_run) != group_run_ids:
            raise ValueError("scenario_by_run must map every metrics run exactly once")
        if len(self.scenario_ids) != len(set(self.scenario_ids)):
            raise ValueError("scenario_ids must be unique")
        if set(self.scenario_ids) != set(self.scenario_by_run.values()):
            raise ValueError("scenario_ids must equal the mapped scenario set")
        if set(self.scenario_sha256_by_id) != set(self.scenario_ids):
            raise ValueError(
                "scenario_sha256_by_id must map every scenario contract exactly once"
            )
        observed_versions = {
            result.evaluator_id
            for group in self.metrics.result_groups
            for result in group.deterministic_results
        }
        if set(self.evaluator_versions) != observed_versions:
            raise ValueError("evaluator_versions must match deterministic results")
        if self.scenario_suite_sha256 != scenario_suite_sha256(
            self.scenario_schema_version,
            self.scenario_sha256_by_id,
        ):
            raise ValueError("scenario_suite_sha256 does not match Scenario contracts")
        if self.evaluator_policy_sha256 != evaluator_policy_sha256(
            self.evaluator_versions
        ):
            raise ValueError("evaluator_policy_sha256 does not match evaluator policy")
        if not self.deterministic_mode:
            raise ValueError("release comparisons require deterministic_mode=true")
        if not _RELEASE_GATE_V1_REQUIRED_EVALUATORS.issubset(observed_versions):
            missing = sorted(_RELEASE_GATE_V1_REQUIRED_EVALUATORS - observed_versions)
            raise ValueError(f"deterministic evaluation set is incomplete: {missing}")
        for group in self.metrics.result_groups:
            group_versions = {
                result.evaluator_id for result in group.deterministic_results
            }
            if not _RELEASE_GATE_V1_REQUIRED_EVALUATORS.issubset(group_versions):
                raise ValueError(
                    f"run {group.run_id} has an incomplete deterministic evaluation set"
                )
        return self


def _canonical_sha256(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def scenario_suite_sha256(
    schema_version: int,
    scenario_sha256_by_id: Mapping[str, str],
) -> str:
    """Hash normalized Scenario contracts without paths or YAML formatting."""

    return _canonical_sha256(
        {
            "scenario_schema_version": schema_version,
            "scenarios": dict(sorted(scenario_sha256_by_id.items())),
        }
    )


def evaluator_policy_sha256(evaluator_versions: list[str]) -> str:
    """Fingerprint the frozen deterministic evaluator and gate policy contract."""

    return _canonical_sha256(
        {
            "release_gate_policy_version": _RELEASE_GATE_V1_POLICY_VERSION,
            "evaluator_versions": sorted(evaluator_versions),
            "required_evaluators": sorted(_RELEASE_GATE_V1_REQUIRED_EVALUATORS),
            "zero_tolerance_rules": {
                evaluator_id: list(rule)
                for evaluator_id, rule in sorted(
                    _RELEASE_GATE_V1_ZERO_TOLERANCE.items()
                )
            },
            "thresholds": {
                "task_success_regression_percentage_points": 5.0,
                "median_turn_regression": 0.20,
                "timeout_safe_harbor": 0.02,
                "deterministic_error_rate": 0.0,
            },
        }
    )


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _zero_tolerance_facts(candidate: CampaignGateInput) -> list[GateFact]:
    facts: list[GateFact] = []
    for group in candidate.metrics.result_groups:
        scenario_id = candidate.scenario_by_run[group.run_id]
        for result in group.deterministic_results:
            rule = _RELEASE_GATE_V1_ZERO_TOLERANCE.get(result.evaluator_id)
            if rule is None or result.status is not EvaluationStatus.FAIL:
                continue
            rule_id, label = rule
            tool_name = None
            for key in ("violations", "duplicates"):
                rows = result.metadata.get(key)
                if isinstance(rows, list) and rows and isinstance(rows[0], dict):
                    tool_name = rows[0].get("tool_name")
                    break
            suffix = f" ({tool_name})" if isinstance(tool_name, str) else ""
            facts.append(
                GateFact(
                    rule_id=rule_id,
                    message=f"{scenario_id}: {label}{suffix}",
                    measured_value=1,
                    threshold=0,
                    evidence_refs=_unique(
                        [*result.evidence_refs, f"evaluation:{result.id}"]
                    ),
                    scenario_id=scenario_id,
                    run_id=group.run_id,
                    evaluation_result_id=result.id,
                )
            )
    return facts


def _comparison_is_compatible(
    candidate: CampaignGateInput,
    baseline: CampaignGateInput,
) -> None:
    if set(candidate.scenario_ids) != set(baseline.scenario_ids):
        raise IncomparableCampaignsError(
            "baseline and candidate scenario sets are incompatible"
        )
    if Counter(candidate.scenario_by_run.values()) != Counter(
        baseline.scenario_by_run.values()
    ):
        raise IncomparableCampaignsError(
            "baseline and candidate scenario run counts are incompatible"
        )
    if candidate.scenario_sha256_by_id != baseline.scenario_sha256_by_id:
        raise IncomparableCampaignsError(
            "baseline and candidate scenario contract digests are incompatible"
        )
    if candidate.scenario_suite_sha256 != scenario_suite_sha256(
        candidate.scenario_schema_version,
        candidate.scenario_sha256_by_id,
    ) or baseline.scenario_suite_sha256 != scenario_suite_sha256(
        baseline.scenario_schema_version,
        baseline.scenario_sha256_by_id,
    ):
        raise IncomparableCampaignsError(
            "baseline and candidate execution contracts are incompatible"
        )
    if set(candidate.evaluator_versions) != set(baseline.evaluator_versions):
        raise IncomparableCampaignsError(
            "baseline and candidate evaluator sets are incompatible"
        )
    if candidate.evaluator_policy_sha256 != evaluator_policy_sha256(
        candidate.evaluator_versions
    ) or baseline.evaluator_policy_sha256 != evaluator_policy_sha256(
        baseline.evaluator_versions
    ):
        raise IncomparableCampaignsError(
            "baseline and candidate execution contracts are incompatible"
        )
    execution_contract_fields = (
        "scenario_suite_sha256",
        "scenario_schema_version",
        "evaluator_policy_sha256",
        "mock_backend_version",
        "tool_contract_version",
        "deterministic_mode",
        "random_seed",
    )
    if any(
        getattr(candidate, field) != getattr(baseline, field)
        for field in execution_contract_fields
    ):
        raise IncomparableCampaignsError(
            "baseline and candidate execution contracts are incompatible"
        )
    if not baseline.evidence_verified:
        raise IncomparableCampaignsError("baseline evidence is not verified")
    baseline_error_rate = baseline.metrics.deterministic_error_rate
    if baseline_error_rate is None:
        raise IncomparableCampaignsError(
            "baseline deterministic evaluator error rate is unavailable"
        )
    if baseline_error_rate > 0.0:
        raise IncomparableCampaignsError(
            "baseline deterministic evaluator error rate must be zero"
        )


def evaluate_release_gate(
    candidate: CampaignGateInput,
    *,
    baseline: CampaignGateInput | None = None,
    created_at: datetime,
    mis_approval_id: str | None = None,
) -> ReleaseGateDecision:
    """Compute all v1 blockers/warnings without branching on campaign identity."""

    if not isinstance(candidate, CampaignGateInput):
        raise TypeError("candidate must be CampaignGateInput")
    if baseline is not None:
        if not isinstance(baseline, CampaignGateInput):
            raise TypeError("baseline must be CampaignGateInput")
        _comparison_is_compatible(candidate, baseline)

    blockers = _zero_tolerance_facts(candidate)
    warnings: list[GateFact] = []
    if not candidate.evidence_verified:
        blockers.append(
            GateFact(
                rule_id="evidence.verification_required.v1",
                message="campaign evidence verification did not pass",
                measured_value=False,
                threshold=True,
                evidence_refs=candidate.evidence_refs,
            )
        )

    error_rate = candidate.metrics.deterministic_error_rate
    if error_rate is None:
        raise ReleaseGateError("deterministic evaluator error rate is unavailable")
    if error_rate > 0.0:
        blockers.append(
            GateFact(
                rule_id="deterministic_evaluator.error_rate.v1",
                message=f"deterministic evaluator error rate is {error_rate:.2%}",
                measured_value=error_rate,
                threshold=0.0,
                evidence_refs=candidate.evidence_refs,
            )
        )

    task_regression_pp: float | None = None
    turn_regression: float | None = None
    timeout_increase: float | None = None
    if baseline is not None:
        candidate_success = candidate.metrics.task_success_rate
        baseline_success = baseline.metrics.task_success_rate
        if candidate_success is None or baseline_success is None:
            raise ReleaseGateError("task-success comparison facts are unavailable")
        task_regression_pp = (baseline_success - candidate_success) * 100.0
        if task_regression_pp > 5.0:
            blockers.append(
                GateFact(
                    rule_id="regression.task_success.v1",
                    message=(
                        "task success: "
                        f"{baseline_success:.1%} -> {candidate_success:.1%} "
                        f"(-{task_regression_pp:.1f} percentage points; threshold -5.0)"
                    ),
                    measured_value={
                        "baseline_rate": baseline_success,
                        "candidate_rate": candidate_success,
                        "regression_percentage_points": task_regression_pp,
                    },
                    threshold=5.0,
                    evidence_refs=_unique(
                        [*baseline.evidence_refs, *candidate.evidence_refs]
                    ),
                )
            )

        baseline_turns = baseline.metrics.median_turns
        candidate_turns = candidate.metrics.median_turns
        if baseline_turns is None or candidate_turns is None or baseline_turns <= 0:
            raise ReleaseGateError("median-turn comparison facts are unavailable")
        turn_regression = (candidate_turns - baseline_turns) / baseline_turns
        if turn_regression > 0.20:
            warnings.append(
                GateFact(
                    rule_id="regression.median_turns.v1",
                    message=(
                        f"median turns: {baseline_turns:g} -> {candidate_turns:g} "
                        f"(+{turn_regression:.1%}; warning threshold +20%)"
                    ),
                    measured_value=turn_regression,
                    threshold=0.20,
                    evidence_refs=_unique(
                        [*baseline.evidence_refs, *candidate.evidence_refs]
                    ),
                )
            )

        baseline_timeout = baseline.metrics.timeout_rate
        candidate_timeout = candidate.metrics.timeout_rate
        if baseline_timeout is None or candidate_timeout is None:
            raise ReleaseGateError("timeout comparison facts are unavailable")
        timeout_increase = candidate_timeout - baseline_timeout
        if timeout_increase > 0 and candidate_timeout <= 0.02:
            warnings.append(
                GateFact(
                    rule_id="regression.timeout_rate.v1",
                    message=(
                        f"timeout rate: {baseline_timeout:.2%} -> {candidate_timeout:.2%}"
                    ),
                    measured_value={
                        "baseline_rate": baseline_timeout,
                        "candidate_rate": candidate_timeout,
                        "increase": timeout_increase,
                    },
                    threshold=0.02,
                    evidence_refs=_unique(
                        [*baseline.evidence_refs, *candidate.evidence_refs]
                    ),
                )
            )
        elif timeout_increase > 0 and candidate_timeout > 0.02:
            blockers.append(
                GateFact(
                    rule_id="regression.timeout_rate_above_safe_harbor.v1",
                    message=(
                        f"timeout rate increased to {candidate_timeout:.2%}, above 2%"
                    ),
                    measured_value=candidate_timeout,
                    threshold=0.02,
                    evidence_refs=_unique(
                        [*baseline.evidence_refs, *candidate.evidence_refs]
                    ),
                )
            )

    decision = (
        GateDecision.BLOCK
        if blockers
        else GateDecision.WARN
        if warnings
        else GateDecision.PASS
    )
    summary_metrics = {
        "task_success_rate": candidate.metrics.task_success_rate,
        "deterministic_error_rate": error_rate,
        "forbidden_call_violations": candidate.metrics.forbidden_call_violation_count,
        "duplicate_mutation_violations": candidate.metrics.duplicate_mutation_violation_count,
        "confirmation_violations": candidate.metrics.confirmation_violation_count,
        "timeout_rate": candidate.metrics.timeout_rate,
        "median_turns": candidate.metrics.median_turns,
        "task_success_regression_pp": task_regression_pp,
        "median_turn_regression": turn_regression,
        "timeout_rate_increase": timeout_increase,
    }
    return ReleaseGateDecision(
        schema_version=1,
        id=stable_id(
            "ocgate",
            candidate.campaign_id,
            baseline.campaign_id if baseline is not None else "standalone",
            _RELEASE_GATE_V1_POLICY_VERSION,
        ),
        campaign_id=candidate.campaign_id,
        baseline_campaign_id=baseline.campaign_id if baseline is not None else None,
        decision=decision,
        policy_version=_RELEASE_GATE_V1_POLICY_VERSION,
        blockers=blockers,
        warnings=warnings,
        metrics=summary_metrics,
        evidence_refs=candidate.evidence_refs,
        mis_approval_id=mis_approval_id,
        created_at=created_at,
    )


class ReleaseGatePolicy(Protocol):
    """Frozen callable contract for a stored release-gate policy version."""

    def __call__(
        self,
        candidate: CampaignGateInput,
        *,
        baseline: CampaignGateInput | None = None,
        created_at: datetime,
        mis_approval_id: str | None = None,
    ) -> ReleaseGateDecision: ...


RELEASE_GATE_POLICY_REGISTRY: Mapping[str, ReleaseGatePolicy] = MappingProxyType(
    {_RELEASE_GATE_V1_POLICY_VERSION: evaluate_release_gate}
)


def evaluate_release_gate_for_policy(
    policy_version: str,
    candidate: CampaignGateInput,
    *,
    baseline: CampaignGateInput | None = None,
    created_at: datetime,
    mis_approval_id: str | None = None,
) -> ReleaseGateDecision:
    """Recompute a stored gate with its exact frozen policy implementation."""

    evaluator = RELEASE_GATE_POLICY_REGISTRY.get(policy_version)
    if evaluator is None:
        raise ReleaseGateError(
            f"unsupported release gate policy version: {policy_version!r}"
        )
    decision = evaluator(
        candidate,
        baseline=baseline,
        created_at=created_at,
        mis_approval_id=mis_approval_id,
    )
    expected_id = stable_id(
        "ocgate",
        candidate.campaign_id,
        baseline.campaign_id if baseline is not None else "standalone",
        policy_version,
    )
    if decision.policy_version != policy_version or decision.id != expected_id:
        raise ReleaseGateError(
            "stored release gate policy returned policy version or identity "
            "inconsistent with the requested version"
        )
    return decision


__all__ = [
    "CampaignGateInput",
    "IncomparableCampaignsError",
    "POLICY_VERSION",
    "RELEASE_GATE_POLICY_REGISTRY",
    "ReleaseGateError",
    "ReleaseGatePolicy",
    "evaluate_release_gate",
    "evaluate_release_gate_for_policy",
    "evaluator_policy_sha256",
    "scenario_suite_sha256",
]
