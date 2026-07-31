from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlsplit

from .ledger import ResearchLedger
from .protocol import sha256_json


def _decode_json(value: Any, fallback: Any) -> Any:
    if value is None:
        return fallback
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def build_mis_evidence_bundle(
    ledger: ResearchLedger,
    experiment_id: str,
    *,
    workspace_id: str = "local-demo",
) -> dict[str, Any]:
    """Build a bounded, content-free bundle for the MIS authority ledger."""
    evidence = ledger.experiment_evidence(experiment_id)
    experiment = evidence["experiment"]
    metrics = evidence["metrics"]
    artifacts = evidence["artifacts"]
    if len(metrics) > 5000:
        raise ValueError("MIS evidence bundle supports at most 5000 metric records")
    if len(artifacts) > 256:
        raise ValueError("MIS evidence bundle supports at most 256 artifacts")

    trials = []
    for trial in evidence["trials"]:
        trials.append(
            {
                "trial_id": trial["id"],
                "params": _decode_json(trial.get("params_json"), {}),
                "status": trial["status"],
                "created_at": trial["created_at"],
                "updated_at": trial["updated_at"],
            }
        )

    attempts = []
    for attempt in evidence["attempts"]:
        attempts.append(
            {
                "attempt_id": attempt["id"],
                "trial_id": attempt["trial_id"],
                "attempt_number": attempt["attempt_number"],
                "status": attempt["status"],
                "exit_code": attempt["exit_code"],
                "error_summary": str(attempt.get("error_summary") or "")[:240] or None,
                "stdout_sha256": attempt.get("stdout_sha256"),
                "stderr_sha256": attempt.get("stderr_sha256"),
                "started_at": attempt["started_at"],
                "finished_at": attempt.get("finished_at"),
                "raw_output_omitted": True,
            }
        )

    metric_rows = []
    for metric in metrics:
        metric_rows.append(
            {
                "metric_id": f"rmet_{sha256_json({'experiment_id': experiment_id, 'source_id': metric['id']})[:20]}",
                "trial_id": metric["trial_id"],
                "attempt_id": metric["attempt_id"],
                "name": metric["name"],
                "value": metric["value"],
                "step": metric.get("step"),
                "recorded_at": metric.get("recorded_at"),
            }
        )

    artifact_rows = []
    for artifact in artifacts:
        artifact_rows.append(
            {
                "artifact_id": artifact["id"],
                "trial_id": artifact["trial_id"],
                "attempt_id": artifact["attempt_id"],
                "relative_path": artifact["relative_path"],
                "content_hash": artifact["sha256"],
                "size_bytes": artifact["size_bytes"],
                "artifact_body_omitted": True,
            }
        )

    deviations = []
    for deviation in evidence["deviations"]:
        deviations.append(
            {
                "deviation_id": f"rdev_{sha256_json({'experiment_id': experiment_id, 'source_id': deviation['id']})[:20]}",
                "trial_id": deviation["trial_id"],
                "attempt_id": deviation["attempt_id"],
                "field_path": deviation["field_path"],
                "severity": deviation["severity"],
                "message": str(deviation["message"])[:240],
                "status": deviation["status"],
                "expected_hash": sha256_json(_decode_json(deviation.get("expected_json"), None)),
                "actual_hash": sha256_json(_decode_json(deviation.get("actual_json"), None)),
                "values_omitted": True,
            }
        )

    bundle = {
        "schema_version": "research_lab_mis_evidence_v1",
        "workspace_id": workspace_id,
        "experiment": {
            "experiment_id": experiment["id"],
            "name": experiment["name"],
            "stage": experiment["stage"],
            "status": experiment["status"],
            "protocol_hash": experiment["protocol_hash"],
            "provenance_hash": experiment.get("provenance_hash"),
            "protocol": _decode_json(experiment.get("protocol_json"), {}),
            "claim_eligible": bool(experiment["claim_eligible"]),
            "claim_reasons": _decode_json(experiment.get("claim_reasons_json"), []),
            "created_at": experiment["created_at"],
            "updated_at": experiment["updated_at"],
        },
        "trials": trials,
        "attempts": attempts,
        "metrics": metric_rows,
        "artifacts": artifact_rows,
        "deviations": deviations,
        "omissions": {
            "credentials": True,
            "state_directory": True,
            "raw_stdout": True,
            "raw_stderr": True,
            "artifact_bodies": True,
            "raw_prompts": True,
            "raw_responses": True,
        },
    }
    bundle["evidence_hash"] = sha256_json(bundle)
    return bundle


def sync_mis_evidence(
    bundle: dict[str, Any],
    *,
    base_url: str,
    timeout_seconds: float = 20,
) -> dict[str, Any]:
    parsed = urlsplit(base_url.rstrip("/"))
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("v1 MIS evidence sync only supports a local HTTP Host")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("MIS base URL must omit credentials, query and fragment")
    token = os.environ.get("AGENTOPS_API_KEY")
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        base_url.rstrip("/") + "/api/research/experiments/ingest",
        data=json.dumps(bundle, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
            status = response.status
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = {"error": "mis_sync_http_error"}
        status = exc.code
    if not isinstance(payload, dict):
        payload = {"error": "mis_sync_invalid_response"}
    payload["http_status"] = status
    payload["token_omitted"] = True
    return payload
