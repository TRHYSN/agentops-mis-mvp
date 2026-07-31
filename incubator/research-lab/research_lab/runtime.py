from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _required_env(name: str) -> Path:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is not set; run inside Research Lab")
    return Path(value)


def log_metric(name: str, value: float, *, step: int | None = None, recorded_at: str | None = None) -> None:
    if not name:
        raise ValueError("metric name must be non-empty")
    path = _required_env("RESEARCH_LAB_METRICS_PATH")
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"name": name, "value": float(value), "step": step, "recorded_at": recorded_at or datetime.now(timezone.utc).isoformat()}
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        fh.flush()


def record_actuals(**actuals: Any) -> Path:
    path = _required_env("RESEARCH_LAB_ACTUALS_PATH")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(actuals)
    payload.setdefault("recorded_at", datetime.now(timezone.utc).isoformat())
    payload.setdefault("protocol_hash", os.environ.get("RESEARCH_LAB_PROTOCOL_HASH"))
    payload.setdefault("provenance_hash", os.environ.get("RESEARCH_LAB_PROVENANCE_HASH"))
    payload.setdefault("resolved_config_hash", os.environ.get("RESEARCH_LAB_RESOLVED_CONFIG_HASH"))
    payload.setdefault("code_revision", os.environ.get("RESEARCH_LAB_CODE_REVISION"))
    payload.setdefault("experiment_id", os.environ.get("RESEARCH_LAB_EXPERIMENT_ID"))
    payload.setdefault("trial_id", os.environ.get("RESEARCH_LAB_TRIAL_ID"))
    payload.setdefault("attempt_id", os.environ.get("RESEARCH_LAB_ATTEMPT_ID"))
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)
    return path


def artifacts_dir() -> Path:
    path = _required_env("RESEARCH_LAB_ARTIFACTS_DIR")
    path.mkdir(parents=True, exist_ok=True)
    return path
