from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable

from .protocol import ExperimentStage, IntegrityPolicy

CRITICAL_FIELDS = {
    "initialization_mode",
    "training_scope",
    "dataset_version",
    "split_hash",
    "metric_code_hash",
    "checkpoint_selection_rule",
    "model_architecture",
    "provenance_hash",
    "resolved_config_hash",
    "code_revision",
}


@dataclass(frozen=True, slots=True)
class Deviation:
    field_path: str
    expected: Any
    actual: Any
    severity: str
    message: str


@dataclass(frozen=True, slots=True)
class ClaimEligibility:
    eligible: bool
    reasons: tuple[str, ...]
    stage: str
    completed_trials: int
    total_trials: int
    distinct_seeds: int
    critical_deviations: int
    warning_deviations: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "eligible": self.eligible,
            "reasons": list(self.reasons),
            "stage": self.stage,
            "completed_trials": self.completed_trials,
            "total_trials": self.total_trials,
            "distinct_seeds": self.distinct_seeds,
            "critical_deviations": self.critical_deviations,
            "warning_deviations": self.warning_deviations,
        }


def expected_actuals(protocol_document: dict[str, Any]) -> dict[str, Any]:
    protocol = protocol_document.get("protocol", {})
    if not isinstance(protocol, dict):
        return {}
    expected = {
        key: value
        for key, value in protocol.items()
        if key in {
            "initialization_mode",
            "training_scope",
            "dataset_version",
            "split_hash",
            "metric_code_hash",
            "checkpoint_selection_rule",
            "model_architecture",
            "code_commit",
            "precision",
            "distributed_strategy",
        }
    }
    provenance = protocol_document.get("provenance")
    if isinstance(provenance, dict) and provenance:
        provenance_hash = protocol_document.get("provenance_hash")
        if provenance_hash:
            expected["provenance_hash"] = provenance_hash
        code = provenance.get("code")
        if isinstance(code, dict) and code.get("revision"):
            expected["code_revision"] = code["revision"]
        if provenance.get("resolved_config_hash"):
            expected["resolved_config_hash"] = provenance["resolved_config_hash"]
    return expected


def compare_actuals(protocol_document: dict[str, Any], actuals: dict[str, Any] | None, integrity: IntegrityPolicy) -> list[Deviation]:
    expected = expected_actuals(protocol_document)
    actuals = actuals or {}
    deviations: list[Deviation] = []
    required_fields = list(integrity.required_actual_fields)
    if integrity.require_provenance:
        required_fields.extend(["provenance_hash", "resolved_config_hash", "code_revision"])
    for field in dict.fromkeys(required_fields):
        if field not in actuals:
            severity = "critical" if integrity.strict_actuals else "warning"
            deviations.append(Deviation(f"protocol.{field}", expected.get(field), None, severity, f"required actual field {field!r} was not recorded"))
    for field, expected_value in expected.items():
        if field not in actuals:
            continue
        actual_value = actuals[field]
        if actual_value != expected_value:
            severity = "critical" if field in CRITICAL_FIELDS else "warning"
            deviations.append(Deviation(f"protocol.{field}", expected_value, actual_value, severity, f"actual {field!r} differs from the frozen protocol"))
    protocol_hash = protocol_document.get("protocol_hash")
    if protocol_hash and actuals.get("protocol_hash") not in {None, protocol_hash}:
        deviations.append(Deviation("protocol_hash", protocol_hash, actuals.get("protocol_hash"), "critical", "runtime actuals reference a different protocol hash"))
    return deviations


def _decode_json(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def evaluate_claim_eligibility(
    experiment: dict[str, Any],
    trials: Iterable[dict[str, Any]],
    deviations: Iterable[dict[str, Any]],
    metric_names_by_trial: dict[str, set[str]] | None = None,
    final_metric_values_by_trial: dict[str, dict[str, float]] | None = None,
) -> ClaimEligibility:
    trial_list = list(trials)
    deviation_list = list(deviations)
    protocol_document = _decode_json(experiment.get("protocol_json")) or {}
    integrity_raw = protocol_document.get("integrity", {})
    try:
        stage = ExperimentStage(str(experiment["stage"]))
    except (KeyError, ValueError):
        stage = ExperimentStage.PILOT
    integrity = IntegrityPolicy.from_dict(integrity_raw, stage=stage)
    terminal_success = {"completed", "completed_with_deviation"}
    completed = [trial for trial in trial_list if trial.get("status") in terminal_success]
    reasons: list[str] = []
    if not stage.potentially_claim_eligible:
        reasons.append(f"stage {stage.value!r} is exploratory and cannot support a final claim")
    provenance = protocol_document.get("provenance")
    provenance_hash = protocol_document.get("provenance_hash")
    if integrity.require_provenance and (not isinstance(provenance, dict) or not provenance or not provenance_hash):
        reasons.append("the frozen protocol lacks required provenance evidence")
    if len(completed) < integrity.minimum_completed_trials:
        reasons.append(f"requires at least {integrity.minimum_completed_trials} completed Trials; observed {len(completed)}")
    if len(completed) != len(trial_list):
        reasons.append("not every Trial completed successfully")
    seeds: set[str] = set()
    for trial in completed:
        params = _decode_json(trial.get("params_json")) or {}
        if isinstance(params, dict) and "seed" in params:
            seeds.add(str(params["seed"]))
    if len(seeds) < integrity.minimum_distinct_seeds:
        reasons.append(f"requires {integrity.minimum_distinct_seeds} distinct seeds; observed {len(seeds)}")
    open_deviations = [item for item in deviation_list if item.get("status") == "open"]
    critical = sum(item.get("severity") == "critical" for item in open_deviations)
    warnings = sum(item.get("severity") == "warning" for item in open_deviations)
    if critical:
        reasons.append(f"{critical} unresolved critical protocol deviation(s)")
    if warnings and not integrity.allow_warning_deviations:
        reasons.append(f"{warnings} unresolved warning protocol deviation(s)")
    primary_metric = protocol_document.get("protocol", {}).get("primary_metric") if isinstance(protocol_document.get("protocol"), dict) else None
    if primary_metric and metric_names_by_trial is not None:
        missing_metric = [str(trial["id"]) for trial in completed if primary_metric not in metric_names_by_trial.get(str(trial["id"]), set())]
        if missing_metric:
            reasons.append(f"primary metric {primary_metric!r} missing from {len(missing_metric)} completed Trial(s)")
    if primary_metric and integrity.minimum_primary_metric is not None and final_metric_values_by_trial is not None:
        below_threshold = []
        for trial in completed:
            trial_id = str(trial["id"])
            value = final_metric_values_by_trial.get(trial_id, {}).get(primary_metric)
            if value is None or value < integrity.minimum_primary_metric:
                below_threshold.append(trial_id)
        if below_threshold:
            reasons.append(
                f"primary metric {primary_metric!r} must be >= {integrity.minimum_primary_metric:g} "
                f"for every completed Trial; {len(below_threshold)} Trial(s) failed"
            )
    return ClaimEligibility(not reasons, tuple(reasons), stage.value, len(completed), len(trial_list), len(seeds), critical, warnings)
