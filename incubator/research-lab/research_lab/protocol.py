from __future__ import annotations

import hashlib
import itertools
import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any

from .provenance import ProvenanceError, ProvenanceSpec
from .redaction import is_sensitive_key, redact_command


class SpecError(ValueError):
    """Raised when an experiment specification is invalid."""


class ExperimentStage(StrEnum):
    SMOKE = "smoke"
    PILOT = "pilot"
    SEARCH = "search"
    CONFIRMATORY = "confirmatory"
    ABLATION = "ablation"
    ROBUSTNESS = "robustness"
    REPRODUCTION = "reproduction"

    @property
    def potentially_claim_eligible(self) -> bool:
        return self in {
            self.CONFIRMATORY,
            self.ABLATION,
            self.ROBUSTNESS,
            self.REPRODUCTION,
        }


DEFAULT_REQUIRED_ACTUAL_FIELDS = ("initialization_mode", "training_scope")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _is_json_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def validate_relative_sync_path(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value or "\n" in value:
        raise SpecError("executor_config.sync_paths entries must be non-empty relative paths")
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts or str(path) in {".", ""}:
        raise SpecError(f"unsafe sync path: {value!r}")
    return str(path)


@dataclass(frozen=True, slots=True)
class IntegrityPolicy:
    required_actual_fields: tuple[str, ...]
    strict_actuals: bool
    minimum_completed_trials: int
    minimum_distinct_seeds: int
    allow_warning_deviations: bool
    require_provenance: bool
    minimum_primary_metric: float | None

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None, *, stage: ExperimentStage) -> "IntegrityPolicy":
        raw = raw or {}
        if not isinstance(raw, dict):
            raise SpecError("integrity must be an object")
        required = raw.get("required_actual_fields", list(DEFAULT_REQUIRED_ACTUAL_FIELDS))
        if not isinstance(required, list) or not all(isinstance(item, str) and item for item in required):
            raise SpecError("integrity.required_actual_fields must be a list of strings")
        default_strict = stage in {ExperimentStage.CONFIRMATORY, ExperimentStage.REPRODUCTION, ExperimentStage.ROBUSTNESS}
        strict_actuals = raw.get("strict_actuals", default_strict)
        if not isinstance(strict_actuals, bool):
            raise SpecError("integrity.strict_actuals must be a boolean")
        minimum_completed_trials = raw.get("minimum_completed_trials", 1)
        if not isinstance(minimum_completed_trials, int) or minimum_completed_trials < 1:
            raise SpecError("integrity.minimum_completed_trials must be >= 1")
        default_seed_count = 2 if stage in {ExperimentStage.CONFIRMATORY, ExperimentStage.ROBUSTNESS} else 1
        minimum_distinct_seeds = raw.get("minimum_distinct_seeds", default_seed_count)
        if not isinstance(minimum_distinct_seeds, int) or minimum_distinct_seeds < 1:
            raise SpecError("integrity.minimum_distinct_seeds must be >= 1")
        allow_warning_deviations = raw.get("allow_warning_deviations", True)
        if not isinstance(allow_warning_deviations, bool):
            raise SpecError("integrity.allow_warning_deviations must be a boolean")
        default_require_provenance = stage in {ExperimentStage.CONFIRMATORY, ExperimentStage.ROBUSTNESS, ExperimentStage.REPRODUCTION}
        require_provenance = raw.get("require_provenance", default_require_provenance)
        if not isinstance(require_provenance, bool):
            raise SpecError("integrity.require_provenance must be a boolean")
        minimum_primary_metric = raw.get("minimum_primary_metric")
        if minimum_primary_metric is not None and (
            isinstance(minimum_primary_metric, bool) or not isinstance(minimum_primary_metric, (int, float))
        ):
            raise SpecError("integrity.minimum_primary_metric must be a number")
        return cls(
            tuple(dict.fromkeys(required)),
            strict_actuals,
            minimum_completed_trials,
            minimum_distinct_seeds,
            allow_warning_deviations,
            require_provenance,
            None if minimum_primary_metric is None else float(minimum_primary_metric),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "required_actual_fields": list(self.required_actual_fields),
            "strict_actuals": self.strict_actuals,
            "minimum_completed_trials": self.minimum_completed_trials,
            "minimum_distinct_seeds": self.minimum_distinct_seeds,
            "allow_warning_deviations": self.allow_warning_deviations,
            "require_provenance": self.require_provenance,
            "minimum_primary_metric": self.minimum_primary_metric,
        }


@dataclass(frozen=True, slots=True)
class ExperimentSpec:
    name: str
    stage: ExperimentStage
    command: tuple[str, ...]
    matrix: dict[str, tuple[Any, ...]]
    protocol: dict[str, Any]
    integrity: IntegrityPolicy
    max_concurrency: int = 1
    timeout_seconds: float = 3600.0
    retries: int = 0
    workdir: str = "."
    environment: dict[str, str] | None = None
    executor: str = "local"
    executor_config: dict[str, Any] | None = None
    provenance: ProvenanceSpec | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ExperimentSpec":
        if not isinstance(raw, dict):
            raise SpecError("spec must be a JSON object")
        name = raw.get("name")
        if not isinstance(name, str) or not name.strip():
            raise SpecError("name must be a non-empty string")
        try:
            stage = ExperimentStage(raw.get("stage"))
        except (TypeError, ValueError) as exc:
            raise SpecError("stage must be one of: " + ", ".join(item.value for item in ExperimentStage)) from exc
        command = raw.get("command")
        if not isinstance(command, list) or not command or not all(isinstance(item, str) and item for item in command):
            raise SpecError("command must be a non-empty list of strings")
        if redact_command(command) != command:
            raise SpecError("command contains a sensitive literal; use approved configuration")

        raw_matrix = raw.get("matrix", {})
        if not isinstance(raw_matrix, dict):
            raise SpecError("matrix must be an object")
        matrix: dict[str, tuple[Any, ...]] = {}
        for key, values in raw_matrix.items():
            if not isinstance(key, str) or not key:
                raise SpecError("matrix keys must be non-empty strings")
            if is_sensitive_key(key):
                raise SpecError(f"matrix key {key!r} is not allowed")
            if not isinstance(values, list) or not values or not all(_is_json_scalar(value) for value in values):
                raise SpecError(f"matrix[{key!r}] must be a non-empty list of JSON scalar values")
            matrix[key] = tuple(values)

        protocol = raw.get("protocol")
        if not isinstance(protocol, dict):
            raise SpecError("protocol must be an object")
        required_protocol = {"research_question", "primary_metric", "initialization_mode", "training_scope"}
        missing = sorted(required_protocol - protocol.keys())
        if missing:
            raise SpecError("protocol is missing required fields: " + ", ".join(missing))
        for key in required_protocol:
            if not isinstance(protocol[key], str) or not protocol[key].strip():
                raise SpecError(f"protocol.{key} must be a non-empty string")

        integrity = IntegrityPolicy.from_dict(raw.get("integrity"), stage=stage)
        try:
            provenance = ProvenanceSpec.from_dict(raw.get("provenance"))
            provenance.validate_for_protocol(protocol, require_provenance=integrity.require_provenance)
        except ProvenanceError as exc:
            raise SpecError(str(exc)) from exc
        max_concurrency = raw.get("max_concurrency", 1)
        if not isinstance(max_concurrency, int) or max_concurrency < 1:
            raise SpecError("max_concurrency must be an integer >= 1")
        timeout_seconds = raw.get("timeout_seconds", 3600)
        if not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
            raise SpecError("timeout_seconds must be > 0")
        retries = raw.get("retries", 0)
        if not isinstance(retries, int) or retries < 0:
            raise SpecError("retries must be an integer >= 0")
        workdir = raw.get("workdir", ".")
        if not isinstance(workdir, str) or not workdir:
            raise SpecError("workdir must be a non-empty string")
        environment = raw.get("environment", {})
        if not isinstance(environment, dict) or not all(isinstance(key, str) and isinstance(value, str) for key, value in environment.items()):
            raise SpecError("environment must map strings to strings")
        sensitive_environment_keys = sorted(key for key in environment if is_sensitive_key(key))
        if sensitive_environment_keys:
            raise SpecError("environment contains sensitive keys; use an approved runtime secret provider")

        executor = raw.get("executor", "local")
        if executor not in {"local", "ssh"}:
            raise SpecError("executor must be 'local' or 'ssh'")
        raw_config = raw.get("executor_config", {})
        if not isinstance(raw_config, dict):
            raise SpecError("executor_config must be an object")
        if executor == "local":
            if raw_config:
                raise SpecError("local executor does not accept executor_config")
            executor_config: dict[str, Any] = {}
        else:
            profile = raw_config.get("profile")
            sync_paths = raw_config.get("sync_paths")
            if not isinstance(profile, str) or not profile.strip():
                raise SpecError("ssh executor requires executor_config.profile")
            if not isinstance(sync_paths, list) or not sync_paths:
                raise SpecError("ssh executor requires a non-empty executor_config.sync_paths list")
            safe_paths = [validate_relative_sync_path(item) for item in sync_paths]
            if len(safe_paths) != len(set(safe_paths)):
                raise SpecError("executor_config.sync_paths contains duplicates")
            if environment:
                raise SpecError("SSH execution uses server-side approved configuration")
            for part in command:
                normalized = part.replace("\\", "/")
                if Path(part).is_absolute() or ".." in PurePosixPath(normalized).parts:
                    raise SpecError("SSH command arguments must not reference absolute or parent-relative local paths")
            executor_config = {"profile": profile, "sync_paths": safe_paths}

        spec = cls(name=name.strip(), stage=stage, command=tuple(command), matrix=matrix, protocol=protocol, integrity=integrity, max_concurrency=max_concurrency, timeout_seconds=float(timeout_seconds), retries=retries, workdir=workdir, environment=environment, executor=executor, executor_config=executor_config, provenance=provenance)
        for params in spec.expand_trials():
            spec.render_command(params)
        return spec

    @property
    def protocol_document(self) -> dict[str, Any]:
        document = {
            "name": self.name,
            "stage": self.stage.value,
            "command_template": redact_command(self.command),
            "matrix": {key: list(value) for key, value in self.matrix.items()},
            "protocol": self.protocol,
            "integrity": self.integrity.to_dict(),
            "timeout_seconds": self.timeout_seconds,
            "retries": self.retries,
            "workdir": self.workdir,
            "executor": self.executor,
            "executor_config": dict(self.executor_config or {}),
            "environment_keys": sorted((self.environment or {}).keys()),
        }
        provenance = self.provenance or ProvenanceSpec.from_dict(None)
        if provenance.present:
            document["provenance"] = provenance.to_dict()
            document["provenance_hash"] = provenance.provenance_hash
        return document

    @property
    def provenance_hash(self) -> str | None:
        provenance = self.provenance or ProvenanceSpec.from_dict(None)
        return provenance.provenance_hash

    @property
    def protocol_hash(self) -> str:
        return sha256_json(self.protocol_document)

    def effective_protocol_document(self, executor_snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
        document = dict(self.protocol_document)
        document["executor_snapshot"] = executor_snapshot or {}
        document["spec_protocol_hash"] = self.protocol_hash
        return document

    def effective_protocol_hash(self, executor_snapshot: dict[str, Any] | None = None) -> str:
        return sha256_json(self.effective_protocol_document(executor_snapshot))

    def expand_trials(self) -> list[dict[str, Any]]:
        if not self.matrix:
            return [{}]
        keys = sorted(self.matrix)
        return [dict(zip(keys, values, strict=True)) for values in itertools.product(*(self.matrix[key] for key in keys))]

    def render_command(self, params: dict[str, Any]) -> list[str]:
        safe_params = {key: str(value) for key, value in params.items()}
        try:
            return [item.format_map(safe_params) for item in self.command]
        except KeyError as exc:
            raise SpecError(f"command references unknown matrix parameter: {exc.args[0]}") from exc
