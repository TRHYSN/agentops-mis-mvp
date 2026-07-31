"""Research experiment evidence contracts for the AgentOps MIS authority ledger.

Local Research Lab execution may produce multiple Trials and retained Attempts.
This module validates and projects only bounded experiment metadata, scalar
metrics, content hashes and review evidence into MIS Task, Run, Artifact,
Evaluation and Audit records. Training execution and raw outputs stay outside
the server.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import math
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable
from urllib.parse import urlsplit


RESEARCH_STAGES = {
    "smoke",
    "pilot",
    "search",
    "confirmatory",
    "ablation",
    "robustness",
    "reproduction",
}
EXPERIMENT_STATUSES = {
    "planned", "running", "completed", "completed_with_deviation", "failed", "blocked",
}
TRIAL_STATUSES = {
    "queued", "running", "completed", "completed_with_deviation", "failed", "blocked",
}
ATTEMPT_STATUSES = {
    "queued", "running", "completed", "completed_with_deviation", "failed", "blocked", "timed_out",
}
CLAIM_STATUSES = {"pending", "eligible", "ineligible"}
_SENSITIVE_KEY = re.compile(
    r"(?:^|[_-])(api[_-]?key|authorization|cookie|credential|password|private[_-]?key|secret|token)(?:$|[_-])",
    re.IGNORECASE,
)
_SAFE_NAME = re.compile(r"^[A-Za-z0-9._:/+-]+$")
_DIGEST = re.compile(r"^[A-Za-z0-9._-]+:[A-Fa-f0-9]{8,}$")
_HEX_SHA256 = re.compile(r"^[A-Fa-f0-9]{64}$")
_PUBLIC_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")
_FORBIDDEN_EVIDENCE_KEY = re.compile(
    r"(?:^|[_-])(?:"
    r"command|environment|local[_-]?path|model[_-]?(?:body|content|weights)|"
    r"prompt|raw|response|state[_-]?dir|stderr|stdout"
    r")(?:$|[_-])",
    re.IGNORECASE,
)
_FORBIDDEN_PROTOCOL_KEY = re.compile(
    r"(?:^|[_-])(?:local[_-]?path|model[_-]?(?:body|content|weights)|prompt|raw|response|state[_-]?dir|stderr|stdout)(?:$|[_-])",
    re.IGNORECASE,
)

RESEARCH_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS research_experiments (
    experiment_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    name TEXT NOT NULL,
    stage TEXT NOT NULL,
    status TEXT NOT NULL,
    protocol_hash TEXT NOT NULL,
    provenance_hash TEXT,
    primary_metric TEXT NOT NULL,
    metric_goal TEXT NOT NULL,
    acceptance_threshold REAL,
    claim_status TEXT NOT NULL,
    claim_reasons_json TEXT NOT NULL DEFAULT '[]',
    evidence_hash TEXT NOT NULL,
    task_id TEXT NOT NULL UNIQUE,
    started_at TEXT,
    ended_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(task_id) REFERENCES tasks(task_id)
);

CREATE TABLE IF NOT EXISTS research_trials (
    trial_id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    status TEXT NOT NULL,
    params_json TEXT NOT NULL DEFAULT '{}',
    params_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(experiment_id) REFERENCES research_experiments(experiment_id)
);

CREATE TABLE IF NOT EXISTS research_attempts (
    attempt_id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL,
    trial_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    attempt_number INTEGER NOT NULL,
    status TEXT NOT NULL,
    exit_code INTEGER,
    error_summary TEXT,
    stdout_sha256 TEXT,
    stderr_sha256 TEXT,
    run_id TEXT NOT NULL UNIQUE,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(experiment_id) REFERENCES research_experiments(experiment_id),
    FOREIGN KEY(trial_id) REFERENCES research_trials(trial_id),
    FOREIGN KEY(run_id) REFERENCES runs(run_id)
);

CREATE TABLE IF NOT EXISTS research_metrics (
    metric_id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL,
    trial_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    name TEXT NOT NULL,
    value REAL NOT NULL,
    step INTEGER,
    recorded_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(experiment_id) REFERENCES research_experiments(experiment_id),
    FOREIGN KEY(trial_id) REFERENCES research_trials(trial_id),
    FOREIGN KEY(attempt_id) REFERENCES research_attempts(attempt_id)
);

CREATE TABLE IF NOT EXISTS research_deviations (
    deviation_id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL,
    trial_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    field_path TEXT NOT NULL,
    severity TEXT NOT NULL,
    message TEXT NOT NULL,
    status TEXT NOT NULL,
    expected_hash TEXT NOT NULL,
    actual_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(experiment_id) REFERENCES research_experiments(experiment_id),
    FOREIGN KEY(trial_id) REFERENCES research_trials(trial_id),
    FOREIGN KEY(attempt_id) REFERENCES research_attempts(attempt_id)
);

CREATE INDEX IF NOT EXISTS idx_research_experiments_workspace
ON research_experiments(workspace_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_research_trials_experiment
ON research_trials(experiment_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_research_attempts_trial
ON research_attempts(trial_id, attempt_number);
CREATE INDEX IF NOT EXISTS idx_research_metrics_trial
ON research_metrics(trial_id, name, step);
CREATE INDEX IF NOT EXISTS idx_research_deviations_trial
ON research_deviations(trial_id, status, severity);
"""


class ResearchExperimentError(ValueError):
    """Raised when a research protocol or runtime evidence is unsafe."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def stable_hash(value: Any) -> str:
    text = value if isinstance(value, str) else canonical_json(value)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _safe_scalar_tree(value: Any, path: str) -> None:
    if value is None or isinstance(value, (str, int, float, bool)):
        if isinstance(value, float) and not math.isfinite(value):
            raise ResearchExperimentError(f"{path} must be finite")
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _safe_scalar_tree(child, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str) or not key:
                raise ResearchExperimentError(f"{path} keys must be non-empty strings")
            if _SENSITIVE_KEY.search(key):
                raise ResearchExperimentError(f"{path}.{key} is not allowed")
            _safe_scalar_tree(child, f"{path}.{key}")
        return
    raise ResearchExperimentError(f"{path} must contain JSON-compatible values")


def _safe_reference(raw: Any, *, kind: str, index: int) -> dict[str, Any]:
    path = f"provenance.{kind}[{index}]"
    if not isinstance(raw, dict):
        raise ResearchExperimentError(f"{path} must be an object")
    name = str(raw.get("name") or "").strip()
    uri = str(raw.get("uri") or "").strip()
    version = str(raw.get("version") or "").strip()
    if not name or not _SAFE_NAME.fullmatch(name):
        raise ResearchExperimentError(f"{path}.name contains unsupported characters")
    if not uri or any(char in uri for char in ("\x00", "\n", "\r")):
        raise ResearchExperimentError(f"{path}.uri must be a stable non-empty reference")
    parsed = urlsplit(uri)
    if parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
        raise ResearchExperimentError(f"{path}.uri must omit credentials, query and fragment")
    if not parsed.scheme:
        relative = PurePosixPath(uri.replace("\\", "/"))
        if relative.is_absolute() or ".." in relative.parts:
            raise ResearchExperimentError(f"{path}.uri must be a safe relative path or URI")
    if not version:
        raise ResearchExperimentError(f"{path}.version is required")
    digest = raw.get("digest")
    if digest is not None and (not isinstance(digest, str) or not _DIGEST.fullmatch(digest)):
        raise ResearchExperimentError(f"{path}.digest must look like '<algorithm>:<hex>'")
    metadata = raw.get("metadata") or {}
    _safe_scalar_tree(metadata, f"{path}.metadata")
    result = {"name": name, "kind": kind.rstrip("s"), "uri": uri, "version": version}
    if digest:
        result["digest"] = digest
    if metadata:
        result["metadata"] = metadata
    return result


def normalize_experiment_spec(raw: Any) -> dict[str, Any]:
    """Validate and normalize the single-Trial local experiment contract."""
    if not isinstance(raw, dict):
        raise ResearchExperimentError("experiment spec must be a JSON object")
    _safe_scalar_tree(raw, "spec")
    name = str(raw.get("name") or "").strip()
    if not name:
        raise ResearchExperimentError("name is required")
    stage = str(raw.get("stage") or "").strip()
    if stage not in RESEARCH_STAGES:
        raise ResearchExperimentError("stage must be one of: " + ", ".join(sorted(RESEARCH_STAGES)))
    command = raw.get("command")
    if not isinstance(command, list) or not command or not all(isinstance(item, str) and item for item in command):
        raise ResearchExperimentError("command must be a non-empty string list")
    if command[0] not in {"python", "python3"}:
        raise ResearchExperimentError("v0 local experiments must use python or python3")
    if any("\x00" in item or "\n" in item or "\r" in item for item in command):
        raise ResearchExperimentError("command contains control characters")
    if any(item in {"-c", "-m"} for item in command[1:]):
        raise ResearchExperimentError("v0 requires a training script path, not inline/module execution")

    workdir = str(raw.get("workdir") or ".").strip()
    workdir_path = PurePosixPath(workdir.replace("\\", "/"))
    if not workdir or workdir_path.is_absolute() or ".." in workdir_path.parts:
        raise ResearchExperimentError("workdir must be a safe relative path")

    retries = raw.get("retries", 0)
    if retries != 0:
        raise ResearchExperimentError("v0 supports retries=0 only")
    timeout_seconds = raw.get("timeout_seconds", 300)
    if not isinstance(timeout_seconds, (int, float)) or not 1 <= float(timeout_seconds) <= 86400:
        raise ResearchExperimentError("timeout_seconds must be between 1 and 86400")

    matrix = raw.get("matrix") or {}
    if not isinstance(matrix, dict):
        raise ResearchExperimentError("matrix must be an object")
    normalized_matrix: dict[str, list[Any]] = {}
    for key, values in matrix.items():
        if not isinstance(key, str) or not key or _SENSITIVE_KEY.search(key):
            raise ResearchExperimentError("matrix keys must be safe non-empty strings")
        if not isinstance(values, list) or not values:
            raise ResearchExperimentError(f"matrix.{key} must be a non-empty list")
        for value in values:
            _safe_scalar_tree(value, f"matrix.{key}")
        normalized_matrix[key] = values
    trial_count = math.prod(len(values) for values in normalized_matrix.values()) if normalized_matrix else 1
    if trial_count != 1:
        raise ResearchExperimentError("v0 supports exactly one Trial; use one value per matrix key")
    params = {
        key: values[0]
        for key, values in sorted(normalized_matrix.items())
    }

    protocol = raw.get("protocol")
    if not isinstance(protocol, dict):
        raise ResearchExperimentError("protocol must be an object")
    required_protocol = {"research_question", "primary_metric", "initialization_mode", "training_scope"}
    missing = sorted(required_protocol - protocol.keys())
    if missing:
        raise ResearchExperimentError("protocol is missing required fields: " + ", ".join(missing))
    for key in required_protocol:
        if not isinstance(protocol[key], str) or not protocol[key].strip():
            raise ResearchExperimentError(f"protocol.{key} must be a non-empty string")
    goal = str(protocol.get("metric_goal") or "maximize").strip().lower()
    if goal not in {"maximize", "minimize"}:
        raise ResearchExperimentError("protocol.metric_goal must be maximize or minimize")
    threshold = protocol.get("acceptance_threshold")
    if not isinstance(threshold, (int, float)) or not math.isfinite(float(threshold)):
        raise ResearchExperimentError("protocol.acceptance_threshold must be a finite number")

    provenance_raw = raw.get("provenance") or {}
    if not isinstance(provenance_raw, dict):
        raise ResearchExperimentError("provenance must be an object")
    datasets_raw = provenance_raw.get("datasets") or []
    models_raw = provenance_raw.get("models") or []
    if not isinstance(datasets_raw, list) or not isinstance(models_raw, list):
        raise ResearchExperimentError("provenance datasets/models must be lists")
    datasets = [_safe_reference(item, kind="datasets", index=index) for index, item in enumerate(datasets_raw)]
    models = [_safe_reference(item, kind="models", index=index) for index, item in enumerate(models_raw)]
    environment_raw = provenance_raw.get("environment")
    environment = None if environment_raw is None else _safe_reference(environment_raw, kind="environment", index=0)
    resolved_config = provenance_raw.get("resolved_config") or {}
    if not isinstance(resolved_config, dict):
        raise ResearchExperimentError("provenance.resolved_config must be an object")
    _safe_scalar_tree(resolved_config, "provenance.resolved_config")
    code = provenance_raw.get("code") or {}
    if code and not isinstance(code, dict):
        raise ResearchExperimentError("provenance.code must be an object")
    if code:
        _safe_scalar_tree(code, "provenance.code")
    provenance = {
        "code": code,
        "datasets": datasets,
        "models": models,
        "environment": environment,
        "resolved_config": resolved_config,
    }

    normalized = {
        "schema_version": "research_experiment_v0",
        "name": name[:160],
        "stage": stage,
        "command": list(command),
        "matrix": normalized_matrix,
        "params": params,
        "protocol": protocol,
        "provenance": provenance,
        "workdir": workdir,
        "timeout_seconds": float(timeout_seconds),
        "retries": 0,
    }
    normalized["protocol_hash"] = stable_hash({
        key: normalized[key]
        for key in ("schema_version", "name", "stage", "command", "matrix", "protocol", "provenance", "workdir", "timeout_seconds", "retries")
    })
    normalized["provenance_hash"] = stable_hash(provenance)
    normalized["params_hash"] = stable_hash(params)
    return normalized


def experiment_public(row: dict[str, Any]) -> dict[str, Any]:
    result = dict(row)
    for key in ("protocol_json", "provenance_json"):
        raw = result.pop(key, None)
        if raw:
            try:
                result[key.removesuffix("_json")] = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                result[key.removesuffix("_json")] = {}
    result["token_omitted"] = True
    return result


def trial_public(row: dict[str, Any]) -> dict[str, Any]:
    result = dict(row)
    raw = result.pop("params_json", None)
    try:
        result["params"] = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        result["params"] = {}
    result["token_omitted"] = True
    return result


def ensure_research_schema(conn: sqlite3.Connection) -> None:
    """Initialize the additive Research Lab schema on an existing MIS ledger."""
    conn.executescript(RESEARCH_SCHEMA_SQL)


def _bounded_text(value: Any, path: str, limit: int, *, required: bool = True) -> str | None:
    if value is None and not required:
        return None
    text = str(value or "").strip()
    if required and not text:
        raise ResearchExperimentError(f"{path} is required")
    if len(text) > limit or any(char in text for char in ("\x00", "\n", "\r")):
        raise ResearchExperimentError(f"{path} exceeds its safe text limit")
    return text or None


def _public_id(value: Any, path: str) -> str:
    text = _bounded_text(value, path, 160)
    if not text or not _PUBLIC_ID.fullmatch(text):
        raise ResearchExperimentError(f"{path} must be an opaque public identifier")
    return text


def _sha256(value: Any, path: str) -> str:
    text = _bounded_text(value, path, 64)
    if not text or not _HEX_SHA256.fullmatch(text):
        raise ResearchExperimentError(f"{path} must be a 64-character SHA-256 hex digest")
    return text.lower()


def _finite_number(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ResearchExperimentError(f"{path} must be a finite number")
    return float(value)


def _optional_timestamp(value: Any, path: str) -> str | None:
    text = _bounded_text(value, path, 80, required=False)
    if text is None:
        return None
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ResearchExperimentError(f"{path} must be an ISO-8601 timestamp") from exc
    return text


def _reject_unknown_fields(value: dict[str, Any], allowed: set[str], path: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ResearchExperimentError(f"{path} contains unsupported fields: {', '.join(unknown)}")


def _reject_forbidden_evidence_fields(value: Any, path: str = "bundle") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if _SENSITIVE_KEY.search(key) or _FORBIDDEN_EVIDENCE_KEY.search(key):
                raise ResearchExperimentError(f"{path}.{key} is not accepted by the MIS evidence API")
            _reject_forbidden_evidence_fields(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_forbidden_evidence_fields(child, f"{path}[{index}]")


def _reject_unsafe_protocol_payload(value: Any, path: str) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if _FORBIDDEN_PROTOCOL_KEY.search(key):
                safe_omission = isinstance(child, bool) and (
                    (key.lower().endswith("_omitted") and child)
                    or (key.lower().endswith("_persisted") and not child)
                )
                if not safe_omission:
                    raise ResearchExperimentError(f"{path}.{key} is not accepted by the MIS evidence API")
            _reject_unsafe_protocol_payload(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_unsafe_protocol_payload(child, f"{path}[{index}]")


def _stable_entity_id(prefix: str, *parts: Any) -> str:
    digest = hashlib.sha256("::".join(str(part) for part in parts).encode("utf-8")).hexdigest()[:20]
    return f"{prefix}_{digest}"


def normalize_evidence_bundle(raw: Any) -> dict[str, Any]:
    """Validate the exact bounded multi-Trial Research Lab evidence contract."""
    if not isinstance(raw, dict):
        raise ResearchExperimentError("evidence bundle must be a JSON object")
    _reject_unknown_fields(
        raw,
        {
            "schema_version", "workspace_id", "experiment", "trials", "attempts",
            "metrics", "artifacts", "deviations", "omissions", "evidence_hash",
        },
        "bundle",
    )
    if raw.get("schema_version") != "research_lab_mis_evidence_v1":
        raise ResearchExperimentError("schema_version must be research_lab_mis_evidence_v1")
    supplied_evidence_hash = _sha256(raw.get("evidence_hash"), "evidence_hash")
    unsigned_bundle = {key: value for key, value in raw.items() if key != "evidence_hash"}
    if stable_hash(unsigned_bundle) != supplied_evidence_hash:
        raise ResearchExperimentError("evidence_hash does not match the canonical bundle")
    workspace_id = _bounded_text(raw.get("workspace_id") or "local-demo", "workspace_id", 120)
    if not workspace_id or not _PUBLIC_ID.fullmatch(workspace_id):
        raise ResearchExperimentError("workspace_id contains unsupported characters")

    omissions = raw.get("omissions")
    required_omissions = {
        "credentials", "state_directory", "raw_stdout", "raw_stderr",
        "artifact_bodies", "raw_prompts", "raw_responses",
    }
    if not isinstance(omissions, dict):
        raise ResearchExperimentError("omissions must be an object")
    _reject_unknown_fields(omissions, required_omissions, "omissions")
    if set(omissions) != required_omissions or not all(omissions.get(key) is True for key in required_omissions):
        raise ResearchExperimentError("all required omission flags must be true")

    experiment_raw = raw.get("experiment")
    if not isinstance(experiment_raw, dict):
        raise ResearchExperimentError("experiment must be an object")
    _reject_unknown_fields(
        experiment_raw,
        {
            "experiment_id", "name", "stage", "status", "protocol_hash",
            "provenance_hash", "protocol", "claim_eligible", "claim_reasons",
            "created_at", "updated_at",
        },
        "experiment",
    )
    stage = str(experiment_raw.get("stage") or "")
    status = str(experiment_raw.get("status") or "")
    if stage not in RESEARCH_STAGES:
        raise ResearchExperimentError("experiment.stage is unsupported")
    if status not in EXPERIMENT_STATUSES:
        raise ResearchExperimentError("experiment.status is unsupported")
    if not isinstance(experiment_raw.get("claim_eligible"), bool):
        raise ResearchExperimentError("experiment.claim_eligible must be boolean")
    claim_reasons = experiment_raw.get("claim_reasons") or []
    if (
        not isinstance(claim_reasons, list)
        or len(claim_reasons) > 64
        or not all(isinstance(item, str) and 0 < len(item) <= 240 for item in claim_reasons)
    ):
        raise ResearchExperimentError("experiment.claim_reasons must be a bounded string list")
    protocol = experiment_raw.get("protocol")
    if not isinstance(protocol, dict):
        raise ResearchExperimentError("experiment.protocol must be an object")
    _safe_scalar_tree(protocol, "experiment.protocol")
    _reject_unsafe_protocol_payload(protocol, "experiment.protocol")
    if len(canonical_json(protocol).encode("utf-8")) > 64 * 1024:
        raise ResearchExperimentError("experiment.protocol exceeds 64 KiB")
    protocol_fields = protocol.get("protocol") or {}
    integrity_fields = protocol.get("integrity") or {}
    if not isinstance(protocol_fields, dict) or not isinstance(integrity_fields, dict):
        raise ResearchExperimentError("experiment.protocol protocol/integrity sections must be objects")
    primary_metric = _bounded_text(
        protocol_fields.get("primary_metric"),
        "experiment.protocol.protocol.primary_metric",
        120,
    )
    metric_goal = str(protocol_fields.get("metric_goal") or "maximize")
    if metric_goal not in {"maximize", "minimize"}:
        raise ResearchExperimentError("experiment protocol metric_goal is unsupported")
    acceptance_threshold = integrity_fields.get("minimum_primary_metric")
    if acceptance_threshold is not None:
        acceptance_threshold = _finite_number(
            acceptance_threshold,
            "experiment.protocol.integrity.minimum_primary_metric",
        )
    provenance_hash = experiment_raw.get("provenance_hash")
    if provenance_hash is not None:
        provenance_hash = _sha256(provenance_hash, "experiment.provenance_hash")
    experiment = {
        "experiment_id": _public_id(experiment_raw.get("experiment_id"), "experiment.experiment_id"),
        "name": _bounded_text(experiment_raw.get("name"), "experiment.name", 160),
        "stage": stage,
        "status": status,
        "protocol_hash": _sha256(experiment_raw.get("protocol_hash"), "experiment.protocol_hash"),
        "provenance_hash": provenance_hash,
        "primary_metric": primary_metric,
        "metric_goal": metric_goal,
        "acceptance_threshold": acceptance_threshold,
        "claim_status": "eligible" if experiment_raw["claim_eligible"] else "ineligible",
        "claim_reasons": list(claim_reasons),
        "created_at": _optional_timestamp(experiment_raw.get("created_at"), "experiment.created_at"),
        "updated_at": _optional_timestamp(experiment_raw.get("updated_at"), "experiment.updated_at"),
    }

    trials_raw = raw.get("trials")
    if not isinstance(trials_raw, list) or not 1 <= len(trials_raw) <= 256:
        raise ResearchExperimentError("trials must contain between 1 and 256 records")
    trials: list[dict[str, Any]] = []
    trial_ids: set[str] = set()
    for index, trial_raw in enumerate(trials_raw):
        path = f"trials[{index}]"
        if not isinstance(trial_raw, dict):
            raise ResearchExperimentError(f"{path} must be an object")
        _reject_unknown_fields(trial_raw, {"trial_id", "params", "status", "created_at", "updated_at"}, path)
        trial_id = _public_id(trial_raw.get("trial_id"), f"{path}.trial_id")
        if trial_id in trial_ids:
            raise ResearchExperimentError("trials contain duplicate trial_id values")
        trial_ids.add(trial_id)
        trial_status = str(trial_raw.get("status") or "")
        if trial_status not in TRIAL_STATUSES:
            raise ResearchExperimentError(f"{path}.status is unsupported")
        params = trial_raw.get("params") or {}
        if not isinstance(params, dict) or len(params) > 64:
            raise ResearchExperimentError(f"{path}.params must contain at most 64 fields")
        _safe_scalar_tree(params, f"{path}.params")
        if len(canonical_json(params).encode("utf-8")) > 16 * 1024:
            raise ResearchExperimentError(f"{path}.params exceeds 16 KiB")
        trials.append({
            "trial_id": trial_id,
            "params": params,
            "params_hash": stable_hash(params),
            "status": trial_status,
            "created_at": _optional_timestamp(trial_raw.get("created_at"), f"{path}.created_at"),
            "updated_at": _optional_timestamp(trial_raw.get("updated_at"), f"{path}.updated_at"),
        })

    attempts_raw = raw.get("attempts")
    if not isinstance(attempts_raw, list) or not 1 <= len(attempts_raw) <= 1024:
        raise ResearchExperimentError("attempts must contain between 1 and 1024 records")
    attempts: list[dict[str, Any]] = []
    attempt_ids: set[str] = set()
    for index, attempt_raw in enumerate(attempts_raw):
        path = f"attempts[{index}]"
        if not isinstance(attempt_raw, dict):
            raise ResearchExperimentError(f"{path} must be an object")
        _reject_unknown_fields(
            attempt_raw,
            {
                "attempt_id", "trial_id", "attempt_number", "status", "exit_code",
                "error_summary", "stdout_sha256", "stderr_sha256", "started_at",
                "finished_at", "raw_output_omitted",
            },
            path,
        )
        attempt_id = _public_id(attempt_raw.get("attempt_id"), f"{path}.attempt_id")
        trial_id = _public_id(attempt_raw.get("trial_id"), f"{path}.trial_id")
        if attempt_id in attempt_ids:
            raise ResearchExperimentError("attempts contain duplicate attempt_id values")
        if trial_id not in trial_ids:
            raise ResearchExperimentError(f"{path}.trial_id is not declared by trials")
        attempt_ids.add(attempt_id)
        attempt_number = attempt_raw.get("attempt_number")
        if isinstance(attempt_number, bool) or not isinstance(attempt_number, int) or attempt_number < 1:
            raise ResearchExperimentError(f"{path}.attempt_number must be a positive integer")
        attempt_status = str(attempt_raw.get("status") or "")
        if attempt_status not in ATTEMPT_STATUSES:
            raise ResearchExperimentError(f"{path}.status is unsupported")
        exit_code = attempt_raw.get("exit_code")
        if exit_code is not None and (isinstance(exit_code, bool) or not isinstance(exit_code, int)):
            raise ResearchExperimentError(f"{path}.exit_code must be an integer or null")
        if attempt_raw.get("raw_output_omitted") is not True:
            raise ResearchExperimentError(f"{path}.raw_output_omitted must be true")
        stdout_sha256 = attempt_raw.get("stdout_sha256")
        stderr_sha256 = attempt_raw.get("stderr_sha256")
        attempts.append({
            "attempt_id": attempt_id,
            "trial_id": trial_id,
            "attempt_number": attempt_number,
            "status": attempt_status,
            "exit_code": exit_code,
            "error_summary": _bounded_text(
                attempt_raw.get("error_summary"),
                f"{path}.error_summary",
                240,
                required=False,
            ),
            "stdout_sha256": None if stdout_sha256 is None else _sha256(stdout_sha256, f"{path}.stdout_sha256"),
            "stderr_sha256": None if stderr_sha256 is None else _sha256(stderr_sha256, f"{path}.stderr_sha256"),
            "started_at": _optional_timestamp(attempt_raw.get("started_at"), f"{path}.started_at"),
            "finished_at": _optional_timestamp(attempt_raw.get("finished_at"), f"{path}.finished_at"),
        })

    metrics_raw = raw.get("metrics")
    if not isinstance(metrics_raw, list) or len(metrics_raw) > 5000:
        raise ResearchExperimentError("metrics must contain at most 5000 records")
    metrics: list[dict[str, Any]] = []
    metric_ids: set[str] = set()
    for index, metric_raw in enumerate(metrics_raw):
        path = f"metrics[{index}]"
        if not isinstance(metric_raw, dict):
            raise ResearchExperimentError(f"{path} must be an object")
        _reject_unknown_fields(
            metric_raw,
            {"metric_id", "trial_id", "attempt_id", "name", "value", "step", "recorded_at"},
            path,
        )
        metric_id = _public_id(metric_raw.get("metric_id"), f"{path}.metric_id")
        trial_id = _public_id(metric_raw.get("trial_id"), f"{path}.trial_id")
        attempt_id = _public_id(metric_raw.get("attempt_id"), f"{path}.attempt_id")
        if metric_id in metric_ids:
            raise ResearchExperimentError("metrics contain duplicate metric_id values")
        if trial_id not in trial_ids or attempt_id not in attempt_ids:
            raise ResearchExperimentError(f"{path} references an undeclared Trial or Attempt")
        metric_ids.add(metric_id)
        step = metric_raw.get("step")
        if step is not None and (isinstance(step, bool) or not isinstance(step, int) or step < 0):
            raise ResearchExperimentError(f"{path}.step must be a non-negative integer or null")
        metrics.append({
            "metric_id": metric_id,
            "trial_id": trial_id,
            "attempt_id": attempt_id,
            "name": _bounded_text(metric_raw.get("name"), f"{path}.name", 120),
            "value": _finite_number(metric_raw.get("value"), f"{path}.value"),
            "step": step,
            "recorded_at": _bounded_text(
                metric_raw.get("recorded_at"),
                f"{path}.recorded_at",
                80,
                required=False,
            ),
        })

    artifacts_raw = raw.get("artifacts")
    if not isinstance(artifacts_raw, list) or len(artifacts_raw) > 256:
        raise ResearchExperimentError("artifacts must contain at most 256 records")
    artifacts: list[dict[str, Any]] = []
    artifact_ids: set[str] = set()
    for index, artifact_raw in enumerate(artifacts_raw):
        path = f"artifacts[{index}]"
        if not isinstance(artifact_raw, dict):
            raise ResearchExperimentError(f"{path} must be an object")
        _reject_unknown_fields(
            artifact_raw,
            {
                "artifact_id", "trial_id", "attempt_id", "relative_path",
                "content_hash", "size_bytes", "artifact_body_omitted",
            },
            path,
        )
        artifact_id = _public_id(artifact_raw.get("artifact_id"), f"{path}.artifact_id")
        trial_id = _public_id(artifact_raw.get("trial_id"), f"{path}.trial_id")
        attempt_id = _public_id(artifact_raw.get("attempt_id"), f"{path}.attempt_id")
        if artifact_id in artifact_ids:
            raise ResearchExperimentError("artifacts contain duplicate artifact_id values")
        if trial_id not in trial_ids or attempt_id not in attempt_ids:
            raise ResearchExperimentError(f"{path} references an undeclared Trial or Attempt")
        if artifact_raw.get("artifact_body_omitted") is not True:
            raise ResearchExperimentError(f"{path}.artifact_body_omitted must be true")
        relative_path = _bounded_text(artifact_raw.get("relative_path"), f"{path}.relative_path", 240)
        relative = PurePosixPath(str(relative_path).replace("\\", "/"))
        if relative.is_absolute() or ".." in relative.parts:
            raise ResearchExperimentError(f"{path}.relative_path must be a safe relative path")
        size_bytes = artifact_raw.get("size_bytes")
        if (
            isinstance(size_bytes, bool)
            or not isinstance(size_bytes, int)
            or size_bytes < 0
            or size_bytes > 50 * 1024 * 1024
        ):
            raise ResearchExperimentError(f"{path}.size_bytes is outside the supported range")
        artifact_ids.add(artifact_id)
        artifacts.append({
            "artifact_id": artifact_id,
            "trial_id": trial_id,
            "attempt_id": attempt_id,
            "relative_path": relative.as_posix(),
            "content_hash": _sha256(artifact_raw.get("content_hash"), f"{path}.content_hash"),
            "size_bytes": size_bytes,
        })

    deviations_raw = raw.get("deviations")
    if not isinstance(deviations_raw, list) or len(deviations_raw) > 1024:
        raise ResearchExperimentError("deviations must contain at most 1024 records")
    deviations: list[dict[str, Any]] = []
    deviation_ids: set[str] = set()
    for index, deviation_raw in enumerate(deviations_raw):
        path = f"deviations[{index}]"
        if not isinstance(deviation_raw, dict):
            raise ResearchExperimentError(f"{path} must be an object")
        _reject_unknown_fields(
            deviation_raw,
            {
                "deviation_id", "trial_id", "attempt_id", "field_path", "severity",
                "message", "status", "expected_hash", "actual_hash", "values_omitted",
            },
            path,
        )
        deviation_id = _public_id(deviation_raw.get("deviation_id"), f"{path}.deviation_id")
        trial_id = _public_id(deviation_raw.get("trial_id"), f"{path}.trial_id")
        attempt_id = _public_id(deviation_raw.get("attempt_id"), f"{path}.attempt_id")
        if deviation_id in deviation_ids:
            raise ResearchExperimentError("deviations contain duplicate deviation_id values")
        if trial_id not in trial_ids or attempt_id not in attempt_ids:
            raise ResearchExperimentError(f"{path} references an undeclared Trial or Attempt")
        if deviation_raw.get("values_omitted") is not True:
            raise ResearchExperimentError(f"{path}.values_omitted must be true")
        severity = str(deviation_raw.get("severity") or "")
        if severity not in {"warning", "critical"}:
            raise ResearchExperimentError(f"{path}.severity is unsupported")
        deviation_status = str(deviation_raw.get("status") or "")
        if deviation_status not in {"open", "accepted", "resolved", "superseded"}:
            raise ResearchExperimentError(f"{path}.status is unsupported")
        deviation_ids.add(deviation_id)
        deviations.append({
            "deviation_id": deviation_id,
            "trial_id": trial_id,
            "attempt_id": attempt_id,
            "field_path": _bounded_text(deviation_raw.get("field_path"), f"{path}.field_path", 160),
            "severity": severity,
            "message": _bounded_text(deviation_raw.get("message"), f"{path}.message", 240),
            "status": deviation_status,
            "expected_hash": _sha256(deviation_raw.get("expected_hash"), f"{path}.expected_hash"),
            "actual_hash": _sha256(deviation_raw.get("actual_hash"), f"{path}.actual_hash"),
        })

    return {
        "schema_version": "research_lab_mis_evidence_v1",
        "workspace_id": workspace_id,
        "experiment": experiment,
        "trials": trials,
        "attempts": attempts,
        "metrics": metrics,
        "artifacts": artifacts,
        "deviations": deviations,
        "omissions": dict(omissions),
        "evidence_hash": supplied_evidence_hash,
    }


def _upsert_row(
    conn: sqlite3.Connection,
    table: str,
    id_column: str,
    row: dict[str, Any],
    update_columns: Iterable[str],
) -> str:
    existing = conn.execute(
        f"SELECT * FROM {table} WHERE {id_column}=?",
        (row[id_column],),
    ).fetchone()
    comparison_columns = set(update_columns) - {"updated_at"}
    if existing and all(existing[column] == row[column] for column in comparison_columns):
        return "unchanged"
    if existing:
        assignments = ",".join(f"{column}=:{column}" for column in update_columns)
        conn.execute(
            f"UPDATE {table} SET {assignments} WHERE {id_column}=:{id_column}",
            row,
        )
        return "updated"
    columns = list(row)
    conn.execute(
        f"INSERT INTO {table}({','.join(columns)}) VALUES({','.join(':' + column for column in columns)})",
        row,
    )
    return "created"


def ingest_research_evidence(
    conn: sqlite3.Connection,
    raw: Any,
    *,
    audit_fn: Callable[..., Any],
    now: str | None = None,
) -> tuple[dict[str, Any], int]:
    """Idempotently map one bounded multi-Trial experiment into the MIS ledger."""
    bundle = normalize_evidence_bundle(raw)
    ensure_research_schema(conn)
    timestamp = now or datetime.now(timezone.utc).isoformat()
    workspace_id = bundle["workspace_id"]
    experiment = bundle["experiment"]
    experiment_id = experiment["experiment_id"]
    task_id = _stable_entity_id("tsk_research", workspace_id, experiment_id)
    agent_id = "agt_research_lab_local"

    existing_experiment = conn.execute(
        "SELECT * FROM research_experiments WHERE experiment_id=?",
        (experiment_id,),
    ).fetchone()
    if existing_experiment and (
        existing_experiment["workspace_id"] != workspace_id
        or existing_experiment["protocol_hash"] != experiment["protocol_hash"]
        or existing_experiment["provenance_hash"] != experiment["provenance_hash"]
        or existing_experiment["task_id"] != task_id
    ):
        return {
            "error": "research_experiment_conflict",
            "message": "experiment_id is already bound to another workspace or immutable protocol/provenance hash",
            "token_omitted": True,
        }, 409

    counts = {"created": 0, "updated": 0, "unchanged": 0}

    def count(outcome: str) -> None:
        counts[outcome] += 1

    count(_upsert_row(
        conn,
        "agents",
        "agent_id",
        {
            "agent_id": agent_id,
            "name": "Research Lab Local Runner",
            "role": "Research Experiment Runner",
            "description": "Records bounded local experiment evidence; execution occurs outside the MIS server.",
            "runtime_type": "codex",
            "model_provider": "local",
            "model_name": "python-training-process",
            "status": "idle",
            "permission_level": "standard",
            "allowed_tools": '["research.evidence.record"]',
            "budget_limit_usd": 0.0,
            "owner_user_id": None,
            "created_at": timestamp,
            "updated_at": timestamp,
        },
        {
            "name", "role", "description", "runtime_type", "model_provider", "model_name",
            "status", "permission_level", "allowed_tools", "budget_limit_usd", "owner_user_id",
            "updated_at",
        },
    ))
    membership_insert = conn.execute(
        """INSERT OR IGNORE INTO workspace_agent_memberships(workspace_id,agent_id,source,created_at)
        VALUES(?,?,?,?)""",
        (workspace_id, agent_id, "research-lab-ingest", timestamp),
    )
    count("created" if membership_insert.rowcount == 1 else "unchanged")

    task_status = {
        "planned": "planned",
        "running": "running",
        "completed": "completed",
        "completed_with_deviation": "completed",
        "failed": "failed",
        "blocked": "blocked",
    }[experiment["status"]]
    count(_upsert_row(
        conn,
        "tasks",
        "task_id",
        {
            "task_id": task_id,
            "workspace_id": workspace_id,
            "title": f"Research experiment: {experiment['name']}"[:200],
            "description": (
                f"Research Lab {experiment['stage']} experiment; protocol/provenance are represented by hashes only."
            ),
            "requester_id": None,
            "owner_agent_id": agent_id,
            "collaborator_agent_ids": "[]",
            "status": task_status,
            "priority": "medium",
            "due_date": None,
            "acceptance_criteria": (
                f"{experiment['primary_metric']} {experiment['metric_goal']} "
                f"threshold {experiment['acceptance_threshold'] if experiment['acceptance_threshold'] is not None else 'protocol-defined'}"
            ),
            "risk_level": "low",
            "budget_limit_usd": 0.0,
            "created_at": existing_experiment["created_at"] if existing_experiment else timestamp,
            "updated_at": timestamp,
        },
        {
            "workspace_id", "title", "description", "requester_id", "owner_agent_id",
            "collaborator_agent_ids", "status", "priority", "due_date",
            "acceptance_criteria", "risk_level", "budget_limit_usd", "updated_at",
        },
    ))

    experiment_row = {
        "experiment_id": experiment_id,
        "workspace_id": workspace_id,
        "name": experiment["name"],
        "stage": experiment["stage"],
        "status": experiment["status"],
        "protocol_hash": experiment["protocol_hash"],
        "provenance_hash": experiment["provenance_hash"],
        "primary_metric": experiment["primary_metric"],
        "metric_goal": experiment["metric_goal"],
        "acceptance_threshold": experiment["acceptance_threshold"],
        "claim_status": experiment["claim_status"],
        "task_id": task_id,
        "claim_reasons_json": canonical_json(experiment["claim_reasons"]),
        "evidence_hash": bundle["evidence_hash"],
        "created_at": experiment["created_at"] or (
            existing_experiment["created_at"] if existing_experiment else timestamp
        ),
        "updated_at": experiment["updated_at"] or timestamp,
    }
    count(_upsert_row(
        conn,
        "research_experiments",
        "experiment_id",
        experiment_row,
        {
            "name", "stage", "status", "primary_metric", "metric_goal",
            "acceptance_threshold", "claim_status", "claim_reasons_json",
            "evidence_hash", "updated_at",
        },
    ))

    trial_by_id: dict[str, dict[str, Any]] = {}
    for trial in bundle["trials"]:
        trial_id = trial["trial_id"]
        trial_by_id[trial_id] = trial
        existing_trial = conn.execute(
            "SELECT * FROM research_trials WHERE trial_id=?",
            (trial_id,),
        ).fetchone()
        if existing_trial and (
            existing_trial["workspace_id"] != workspace_id
            or existing_trial["experiment_id"] != experiment_id
            or existing_trial["params_hash"] != trial["params_hash"]
        ):
            return {
                "error": "research_trial_conflict",
                "message": "trial_id is already bound to another experiment, workspace or params hash",
                "token_omitted": True,
            }, 409
        count(_upsert_row(
            conn,
            "research_trials",
            "trial_id",
            {
                "trial_id": trial_id,
                "experiment_id": experiment_id,
                "workspace_id": workspace_id,
                "status": trial["status"],
                "params_json": canonical_json(trial["params"]),
                "params_hash": trial["params_hash"],
                "created_at": trial["created_at"] or (
                    existing_trial["created_at"] if existing_trial else timestamp
                ),
                "updated_at": trial["updated_at"] or timestamp,
            },
            {"status", "params_json", "updated_at"},
        ))

    def duration_ms(started_at: str | None, finished_at: str | None) -> int | None:
        if not started_at or not finished_at:
            return None
        try:
            started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            finished = datetime.fromisoformat(finished_at.replace("Z", "+00:00"))
        except ValueError:
            return None
        return max(0, int((finished - started).total_seconds() * 1000))

    run_ids: list[str] = []
    run_by_attempt: dict[str, str] = {}
    for attempt in bundle["attempts"]:
        attempt_id = attempt["attempt_id"]
        trial_id = attempt["trial_id"]
        run_id = _stable_entity_id("run_research", workspace_id, attempt_id)
        run_ids.append(run_id)
        run_by_attempt[attempt_id] = run_id
        existing_attempt = conn.execute(
            "SELECT * FROM research_attempts WHERE attempt_id=?",
            (attempt_id,),
        ).fetchone()
        if existing_attempt and (
            existing_attempt["workspace_id"] != workspace_id
            or existing_attempt["experiment_id"] != experiment_id
            or existing_attempt["trial_id"] != trial_id
            or existing_attempt["run_id"] != run_id
        ):
            return {
                "error": "research_attempt_conflict",
                "message": "attempt_id is already bound to another Trial, Experiment or workspace",
                "token_omitted": True,
            }, 409
        generic_run_status = {
            "completed_with_deviation": "completed",
            "timed_out": "failed",
        }.get(attempt["status"], attempt["status"])
        count(_upsert_row(
            conn,
            "runs",
            "run_id",
            {
                "run_id": run_id,
                "workspace_id": workspace_id,
                "task_id": task_id,
                "agent_id": agent_id,
                "runtime_type": "research_lab_local",
                "status": generic_run_status,
                "started_at": attempt["started_at"] or timestamp,
                "ended_at": attempt["finished_at"],
                "duration_ms": duration_ms(attempt["started_at"], attempt["finished_at"]),
                "input_summary": (
                    f"Bounded Research Lab Trial {trial_id}; "
                    f"params_hash={trial_by_id[trial_id]['params_hash'][:16]}."
                ),
                "output_summary": (
                    f"Research Attempt {attempt_id} reported {attempt['status']}; raw process output omitted."
                ),
                "model_provider": "local",
                "model_name": "python-training-process",
                "input_tokens": 0,
                "output_tokens": 0,
                "reasoning_tokens": 0,
                "cost_usd": 0.0,
                "error_type": (
                    None
                    if attempt["status"] in {"running", "completed", "completed_with_deviation"}
                    else f"research_{attempt['status']}"
                ),
                "error_message": attempt["error_summary"],
                "trace_id": experiment_id,
                "parent_run_id": None,
                "delegation_id": trial_id,
                "approval_required": 0,
                "agent_plan_id": None,
                "plan_hash": experiment["protocol_hash"],
                "created_at": existing_attempt["created_at"] if existing_attempt else timestamp,
            },
            {
                "workspace_id", "task_id", "agent_id", "runtime_type", "status",
                "started_at", "ended_at", "duration_ms", "input_summary", "output_summary",
                "model_provider", "model_name", "input_tokens", "output_tokens",
                "reasoning_tokens", "cost_usd", "error_type", "error_message",
                "trace_id", "parent_run_id", "delegation_id", "approval_required",
                "agent_plan_id", "plan_hash",
            },
        ))
        count(_upsert_row(
            conn,
            "research_attempts",
            "attempt_id",
            {
                "attempt_id": attempt_id,
                "experiment_id": experiment_id,
                "trial_id": trial_id,
                "workspace_id": workspace_id,
                "attempt_number": attempt["attempt_number"],
                "status": attempt["status"],
                "exit_code": attempt["exit_code"],
                "error_summary": attempt["error_summary"],
                "stdout_sha256": attempt["stdout_sha256"],
                "stderr_sha256": attempt["stderr_sha256"],
                "run_id": run_id,
                "started_at": attempt["started_at"] or timestamp,
                "finished_at": attempt["finished_at"],
                "created_at": existing_attempt["created_at"] if existing_attempt else timestamp,
                "updated_at": timestamp,
            },
            {
                "attempt_number", "status", "exit_code", "error_summary",
                "stdout_sha256", "stderr_sha256", "started_at", "finished_at", "updated_at",
            },
        ))

    metric_ids: list[str] = []
    for metric in bundle["metrics"]:
        metric_id = metric["metric_id"]
        metric_ids.append(metric_id)
        existing_metric = conn.execute(
            "SELECT created_at FROM research_metrics WHERE metric_id=?",
            (metric_id,),
        ).fetchone()
        count(_upsert_row(
            conn,
            "research_metrics",
            "metric_id",
            {
                "metric_id": metric_id,
                "experiment_id": experiment_id,
                "trial_id": metric["trial_id"],
                "attempt_id": metric["attempt_id"],
                "workspace_id": workspace_id,
                "name": metric["name"],
                "value": metric["value"],
                "step": metric["step"],
                "recorded_at": metric["recorded_at"],
                "created_at": existing_metric["created_at"] if existing_metric else timestamp,
                "updated_at": timestamp,
            },
            {"value", "updated_at"},
        ))

    deviation_ids: list[str] = []
    for deviation in bundle["deviations"]:
        deviation_id = deviation["deviation_id"]
        deviation_ids.append(deviation_id)
        existing_deviation = conn.execute(
            "SELECT created_at FROM research_deviations WHERE deviation_id=?",
            (deviation_id,),
        ).fetchone()
        count(_upsert_row(
            conn,
            "research_deviations",
            "deviation_id",
            {
                "deviation_id": deviation_id,
                "experiment_id": experiment_id,
                "trial_id": deviation["trial_id"],
                "attempt_id": deviation["attempt_id"],
                "workspace_id": workspace_id,
                "field_path": deviation["field_path"],
                "severity": deviation["severity"],
                "message": deviation["message"],
                "status": deviation["status"],
                "expected_hash": deviation["expected_hash"],
                "actual_hash": deviation["actual_hash"],
                "created_at": existing_deviation["created_at"] if existing_deviation else timestamp,
                "updated_at": timestamp,
            },
            {
                "field_path", "severity", "message", "status", "expected_hash",
                "actual_hash", "updated_at",
            },
        ))

    evaluation_run_id = run_ids[-1]
    evaluation_id = _stable_entity_id("eval_research", experiment_id, "claim_gate")
    evaluation_row = {
        "evaluation_id": evaluation_id,
        "task_id": task_id,
        "run_id": evaluation_run_id,
        "agent_id": agent_id,
        "evaluator_type": "rule",
        "score": 1.0 if experiment["claim_status"] == "eligible" else 0.0,
        "pass_fail": "pass" if experiment["claim_status"] == "eligible" else "fail",
        "rubric_json": canonical_json({
            "schema_version": "research_claim_gate_v1",
            "primary_metric": experiment["primary_metric"],
            "metric_goal": experiment["metric_goal"],
            "acceptance_threshold": experiment["acceptance_threshold"],
            "claim_status": experiment["claim_status"],
            "claim_reasons": experiment["claim_reasons"],
            "trial_count": len(bundle["trials"]),
            "attempt_count": len(bundle["attempts"]),
            "metric_count": len(bundle["metrics"]),
            "open_deviation_count": sum(
                deviation["status"] == "open" for deviation in bundle["deviations"]
            ),
            "protocol_hash": experiment["protocol_hash"],
            "provenance_hash": experiment["provenance_hash"],
            "raw_output_omitted": True,
        }),
        "notes": "Research Claim Gate recorded from bounded Research Lab evidence.",
        "created_at": timestamp,
    }
    count(_upsert_row(
        conn,
        "evaluations",
        "evaluation_id",
        evaluation_row,
        {"score", "pass_fail", "rubric_json", "notes"},
    ))

    artifact_ids: list[str] = []
    for artifact in bundle["artifacts"]:
        artifact_id = _stable_entity_id(
            "art_research",
            workspace_id,
            artifact["artifact_id"],
            artifact["content_hash"],
        )
        artifact_ids.append(artifact_id)
        run_id = run_by_attempt[artifact["attempt_id"]]
        count(_upsert_row(
            conn,
            "artifacts",
            "artifact_id",
            {
                "artifact_id": artifact_id,
                "task_id": task_id,
                "run_id": run_id,
                "artifact_type": "research_artifact",
                "title": PurePosixPath(artifact["relative_path"]).name[:160],
                "uri": (
                    f"research://{experiment_id}/{artifact['trial_id']}/"
                    f"{artifact['attempt_id']}/{artifact['artifact_id']}"
                ),
                "summary": (
                    f"Content-addressed Research Lab artifact; "
                    f"{artifact['size_bytes']} bytes; body omitted."
                ),
                "content_hash": artifact["content_hash"],
                "created_at": timestamp,
            },
            {"artifact_type", "title", "uri", "summary", "content_hash"},
        ))

    audit_id = _stable_entity_id("aud_research", experiment_id, bundle["evidence_hash"])
    audit_fn(
        conn,
        "system",
        "research-lab-ingest",
        "research.evidence_ingest",
        "research_experiments",
        experiment_id,
        None,
        {
            "experiment_id": experiment_id,
            "run_ids": run_ids,
            "evaluation_id": evaluation_id,
            "evidence_hash": bundle["evidence_hash"],
        },
        {
            "workspace_id": workspace_id,
            "metric_count": len(metric_ids),
            "artifact_count": len(artifact_ids),
            "trial_count": len(bundle["trials"]),
            "attempt_count": len(bundle["attempts"]),
            "deviation_count": len(deviation_ids),
            "raw_output_omitted": True,
            "model_body_omitted": True,
            "local_paths_omitted": True,
            "credentials_omitted": True,
            "token_omitted": True,
        },
        audit_id=audit_id,
        ignore_duplicate=True,
    )
    idempotent_replay = bool(
        existing_experiment
        and existing_experiment["evidence_hash"] == bundle["evidence_hash"]
        and counts["created"] == 0
        and counts["updated"] == 0
    )
    payload = {
        "provider": "agentops-research",
        "operation": "research_experiment_ingest",
        "schema_version": "research_ingest_result_v1",
        "ok": True,
        "experiment_id": experiment_id,
        "task_id": task_id,
        "trial_ids": [trial["trial_id"] for trial in bundle["trials"]],
        "attempt_ids": [attempt["attempt_id"] for attempt in bundle["attempts"]],
        "run_ids": run_ids,
        "evaluation_id": evaluation_id,
        "artifact_ids": artifact_ids,
        "metric_ids": metric_ids,
        "evidence_hash": bundle["evidence_hash"],
        "counts": counts,
        "idempotent_replay": idempotent_replay,
        "server_executes_shell": False,
        "raw_output_omitted": True,
        "model_body_omitted": True,
        "local_paths_omitted": True,
        "credentials_omitted": True,
        "token_omitted": True,
    }
    return payload, 200 if existing_experiment else 201


def list_research_experiments(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    status: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    ensure_research_schema(conn)
    workspace_id = _bounded_text(workspace_id or "local-demo", "workspace_id", 120) or "local-demo"
    if status and status not in EXPERIMENT_STATUSES:
        raise ResearchExperimentError("status filter is unsupported")
    try:
        limit = max(1, min(int(limit), 200))
    except (TypeError, ValueError) as exc:
        raise ResearchExperimentError("limit must be an integer") from exc
    params: list[Any] = [workspace_id]
    where = "WHERE e.workspace_id=?"
    if status:
        where += " AND e.status=?"
        params.append(status)
    params.append(limit)
    rows = conn.execute(
        f"""SELECT e.*,
                   (SELECT COUNT(*) FROM research_trials t WHERE t.experiment_id=e.experiment_id) AS trial_count,
                   (SELECT COUNT(*) FROM research_trials t WHERE t.experiment_id=e.experiment_id AND t.status IN ('completed','completed_with_deviation')) AS completed_trial_count,
                   (SELECT COUNT(*) FROM research_metrics m WHERE m.experiment_id=e.experiment_id) AS metric_count,
                   (SELECT COUNT(*) FROM artifacts a WHERE a.task_id=e.task_id) AS artifact_count,
                   (SELECT COUNT(*) FROM evaluations v WHERE v.task_id=e.task_id) AS evaluation_count,
                   (SELECT m.value FROM research_metrics m
                    WHERE m.experiment_id=e.experiment_id AND m.name=e.primary_metric
                    ORDER BY COALESCE(m.recorded_at,'' ) DESC,COALESCE(m.step,-1) DESC,m.metric_id DESC LIMIT 1) AS final_metric_value
            FROM research_experiments e
            {where}
            ORDER BY e.updated_at DESC,e.experiment_id
            LIMIT ?""",
        params,
    ).fetchall()
    experiments: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        try:
            item["claim_reasons"] = json.loads(item.pop("claim_reasons_json", "[]"))
        except json.JSONDecodeError:
            item["claim_reasons"] = []
        experiments.append(item)
    return {
        "provider": "agentops-research",
        "operation": "research_experiment_list",
        "schema_version": "research_readback_v1",
        "workspace_id": workspace_id,
        "experiments": experiments,
        "count": len(rows),
        "read_only": True,
        "server_executes_shell": False,
        "raw_output_omitted": True,
        "token_omitted": True,
    }


def get_research_experiment(
    conn: sqlite3.Connection,
    experiment_id: str,
    *,
    workspace_id: str,
) -> tuple[dict[str, Any], int]:
    ensure_research_schema(conn)
    experiment_id = _public_id(experiment_id, "experiment_id")
    workspace_id = _bounded_text(workspace_id or "local-demo", "workspace_id", 120) or "local-demo"
    experiment = conn.execute(
        "SELECT * FROM research_experiments WHERE experiment_id=? AND workspace_id=?",
        (experiment_id, workspace_id),
    ).fetchone()
    if not experiment:
        return {
            "error": "research_experiment_not_found",
            "experiment_id": experiment_id,
            "token_omitted": True,
        }, 404
    trials = [
        trial_public(dict(row))
        for row in conn.execute(
            """SELECT t.*,e.primary_metric,
                      (SELECT m.value FROM research_metrics m
                       WHERE m.trial_id=t.trial_id AND m.name=e.primary_metric
                       ORDER BY COALESCE(m.recorded_at,'') DESC,COALESCE(m.step,-1) DESC,m.metric_id DESC LIMIT 1) AS final_metric_value,
                      (SELECT MIN(a.started_at) FROM research_attempts a WHERE a.trial_id=t.trial_id) AS started_at,
                      (SELECT MAX(a.finished_at) FROM research_attempts a WHERE a.trial_id=t.trial_id) AS ended_at
                      ,(SELECT a.run_id FROM research_attempts a WHERE a.trial_id=t.trial_id
                        ORDER BY a.attempt_number DESC,a.attempt_id DESC LIMIT 1) AS run_id
               FROM research_trials t
               JOIN research_experiments e ON e.experiment_id=t.experiment_id
               WHERE t.experiment_id=? AND t.workspace_id=?
               ORDER BY t.created_at,t.trial_id""",
            (experiment_id, workspace_id),
        ).fetchall()
    ]
    trial_ids = [row["trial_id"] for row in trials]
    attempts = [
        dict(row)
        for row in conn.execute(
            """SELECT * FROM research_attempts
            WHERE experiment_id=? AND workspace_id=?
            ORDER BY trial_id,attempt_number""",
            (experiment_id, workspace_id),
        ).fetchall()
    ]
    attempt_ids = [row["attempt_id"] for row in attempts]
    metrics: list[dict[str, Any]] = []
    if trial_ids:
        placeholders = ",".join("?" for _ in trial_ids)
        metrics = [
            dict(row)
            for row in conn.execute(
                f"""SELECT * FROM research_metrics
                WHERE trial_id IN ({placeholders})
                ORDER BY recorded_at,metric_id""",
                trial_ids,
            ).fetchall()
        ]
    latest_metric_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for metric in metrics:
        latest_metric_by_key[(str(metric["trial_id"]), str(metric["name"]))] = metric
    latest_metrics = [
        latest_metric_by_key[key]
        for key in sorted(latest_metric_by_key)
    ]
    task_id = experiment["task_id"]
    run_ids = [row["run_id"] for row in attempts]
    evaluations: list[dict[str, Any]] = []
    artifacts: list[dict[str, Any]] = []
    audits: list[dict[str, Any]] = []
    if run_ids:
        placeholders = ",".join("?" for _ in run_ids)
        evaluations = [
            dict(row)
            for row in conn.execute(
                f"""SELECT evaluation_id,task_id,run_id,agent_id,evaluator_type,score,pass_fail,notes,created_at
                FROM evaluations WHERE run_id IN ({placeholders}) ORDER BY created_at""",
                run_ids,
            ).fetchall()
        ]
        artifacts = [
            dict(row)
            for row in conn.execute(
                f"""SELECT artifact_id,task_id,run_id,artifact_type,title,uri,summary,content_hash,created_at
                FROM artifacts WHERE run_id IN ({placeholders}) ORDER BY created_at""",
                run_ids,
            ).fetchall()
        ]
    deviations = [
        dict(row)
        for row in conn.execute(
            """SELECT * FROM research_deviations
            WHERE experiment_id=? AND workspace_id=?
            ORDER BY trial_id,attempt_id,deviation_id""",
            (experiment_id, workspace_id),
        ).fetchall()
    ]
    audit_entity_ids = [experiment_id, task_id, *trial_ids, *attempt_ids, *run_ids]
    placeholders = ",".join("?" for _ in audit_entity_ids)
    audits = [
        dict(row)
        for row in conn.execute(
            f"""SELECT audit_id,actor_type,actor_id,action,entity_type,entity_id,created_at
            FROM audit_logs WHERE entity_id IN ({placeholders}) ORDER BY created_at""",
            audit_entity_ids,
        ).fetchall()
    ]
    experiment_public_row = dict(experiment)
    try:
        experiment_public_row["claim_reasons"] = json.loads(
            experiment_public_row.pop("claim_reasons_json", "[]")
        )
    except json.JSONDecodeError:
        experiment_public_row["claim_reasons"] = []
    payload = {
        "provider": "agentops-research",
        "operation": "research_experiment_detail",
        "schema_version": "research_readback_v1",
        "workspace_id": workspace_id,
        "experiment": experiment_public_row,
        "trials": trials,
        "attempts": attempts,
        "metrics": metrics,
        "latest_metrics": latest_metrics,
        "deviations": deviations,
        "evaluations": evaluations,
        "artifacts": artifacts,
        "audit_logs": audits,
        "mis_links": {
            "task_id": task_id,
            "run_ids": run_ids,
            "evaluation_ids": [row["evaluation_id"] for row in evaluations],
            "artifact_ids": [row["artifact_id"] for row in artifacts],
        },
        "evidence_counts": {
            "trials": len(trials),
            "attempts": len(attempts),
            "metrics": len(metrics),
            "deviations": len(deviations),
            "evaluations": len(evaluations),
            "artifacts": len(artifacts),
            "audit_logs": len(audits),
        },
        "read_only": True,
        "server_executes_shell": False,
        "raw_output_omitted": True,
        "model_body_omitted": True,
        "local_paths_omitted": True,
        "token_omitted": True,
    }
    return payload, 200


def parse_metric_records(path: Path, *, limit: int = 5000) -> list[dict[str, Any]]:
    if not path.is_file() or path.is_symlink():
        raise ResearchExperimentError("training did not produce a regular metrics JSONL file")
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if len(records) >= limit:
                raise ResearchExperimentError(f"metrics exceed the {limit} record limit")
            if len(line.encode("utf-8")) > 8192:
                raise ResearchExperimentError(f"metric line {line_number} is too large")
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ResearchExperimentError(f"metric line {line_number} is invalid JSON") from exc
            if not isinstance(raw, dict):
                raise ResearchExperimentError(f"metric line {line_number} must be an object")
            name = str(raw.get("name") or "").strip()
            if not name or len(name) > 120 or _SENSITIVE_KEY.search(name):
                raise ResearchExperimentError(f"metric line {line_number} has an invalid name")
            value = raw.get("value")
            if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                raise ResearchExperimentError(f"metric line {line_number} has a non-finite value")
            step = raw.get("step")
            if step is not None and (not isinstance(step, int) or step < 0):
                raise ResearchExperimentError(f"metric line {line_number} has an invalid step")
            split = str(raw.get("split") or "train").strip()[:40] or "train"
            records.append({
                "name": name,
                "value": float(value),
                "step": step,
                "split": split,
                "recorded_at": str(raw.get("recorded_at") or datetime.now(timezone.utc).isoformat())[:80],
            })
    if not records:
        raise ResearchExperimentError("training produced no metrics")
    return records


def load_actuals(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink() or path.stat().st_size > 256 * 1024:
        raise ResearchExperimentError("training did not produce bounded actuals JSON")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ResearchExperimentError("training actuals are invalid JSON") from exc
    if not isinstance(payload, dict):
        raise ResearchExperimentError("training actuals must be an object")
    _safe_scalar_tree(payload, "actuals")
    return payload


def evaluate_claim(
    spec: dict[str, Any],
    metrics: Iterable[dict[str, Any]],
    actuals: dict[str, Any],
    *,
    process_ok: bool,
) -> dict[str, Any]:
    protocol = spec["protocol"]
    primary_metric = str(protocol["primary_metric"])
    values = [float(item["value"]) for item in metrics if item["name"] == primary_metric]
    final_value = values[-1] if values else None
    threshold = float(protocol["acceptance_threshold"])
    goal = str(protocol.get("metric_goal") or "maximize")
    threshold_pass = final_value is not None and (
        final_value >= threshold if goal == "maximize" else final_value <= threshold
    )
    deviations: list[str] = []
    for field in ("protocol_hash", "provenance_hash", "initialization_mode", "training_scope"):
        expected = (
            spec[field]
            if field in {"protocol_hash", "provenance_hash"}
            else protocol.get(field)
        )
        if actuals.get(field) != expected:
            deviations.append(field)
    passed = bool(process_ok and threshold_pass and not deviations)
    return {
        "schema_version": "research_claim_gate_v0",
        "pass": passed,
        "score": 1.0 if passed else 0.0,
        "primary_metric": primary_metric,
        "metric_goal": goal,
        "acceptance_threshold": threshold,
        "final_metric_value": final_value,
        "process_ok": process_ok,
        "threshold_pass": threshold_pass,
        "protocol_deviations": deviations,
        "protocol_hash": spec["protocol_hash"],
        "provenance_hash": spec["provenance_hash"],
        "raw_output_omitted": True,
        "token_omitted": True,
    }


def _required_runtime_path(name: str) -> Path:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is not set; run inside agentops experiment run-local")
    return Path(value)


def log_metric(name: str, value: float, *, step: int | None = None, split: str = "train") -> None:
    """Write one bounded metric from a training process."""
    record = {
        "name": name,
        "value": float(value),
        "step": step,
        "split": split,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    _safe_scalar_tree(record, "metric")
    path = _required_runtime_path("AGENTOPS_RESEARCH_METRICS_PATH")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(canonical_json(record) + "\n")
        handle.flush()


def record_actuals(**values: Any) -> Path:
    """Write the runtime condition snapshot without exposing it to the server."""
    payload = dict(values)
    payload.setdefault("recorded_at", datetime.now(timezone.utc).isoformat())
    payload.setdefault("protocol_hash", os.environ.get("AGENTOPS_RESEARCH_PROTOCOL_HASH"))
    payload.setdefault("provenance_hash", os.environ.get("AGENTOPS_RESEARCH_PROVENANCE_HASH"))
    payload.setdefault("experiment_id", os.environ.get("AGENTOPS_RESEARCH_EXPERIMENT_ID"))
    payload.setdefault("trial_id", os.environ.get("AGENTOPS_RESEARCH_TRIAL_ID"))
    _safe_scalar_tree(payload, "actuals")
    path = _required_runtime_path("AGENTOPS_RESEARCH_ACTUALS_PATH")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)
    return path


def artifacts_dir() -> Path:
    path = _required_runtime_path("AGENTOPS_RESEARCH_ARTIFACTS_DIR")
    path.mkdir(parents=True, exist_ok=True)
    return path


def artifact_manifest(root: Path, *, max_files: int = 32, max_total_bytes: int = 50 * 1024 * 1024) -> list[dict[str, Any]]:
    if not root.is_dir() or root.is_symlink():
        raise ResearchExperimentError("artifact root is missing or unsafe")
    rows: list[dict[str, Any]] = []
    total_bytes = 0
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ResearchExperimentError("artifact symlinks are not allowed")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ResearchExperimentError("artifact entry is not a regular file")
        relative = path.relative_to(root).as_posix()
        if len(rows) >= max_files:
            raise ResearchExperimentError("artifact file count exceeds limit")
        size = path.stat().st_size
        total_bytes += size
        if total_bytes > max_total_bytes:
            raise ResearchExperimentError("artifact bytes exceed limit")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        rows.append({
            "path": relative,
            "size_bytes": size,
            "content_hash": digest,
        })
    return rows
