"""Pure projection and integrity helpers for Private Host acceptance receipts."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping


RECEIPT_SCHEMA_VERSION = "private_host_acceptance_receipt_v1"
RECEIPT_ID_RE = re.compile(r"phr_[a-f0-9]{24}")
SHA256_RE = re.compile(r"[a-f0-9]{64}")
OMISSION_FLAGS = {
    "row_contents_omitted",
    "filesystem_paths_omitted",
    "url_query_omitted",
    "tokens_omitted",
    "raw_prompt_omitted",
    "raw_response_omitted",
    "artifact_file_content_omitted",
}
RECEIPT_KEYS = {
    "receipt_id",
    "schema_version",
    "host_version",
    "git_commit",
    "workspace_id",
    "task_id",
    "run_id",
    "adapter",
    "status",
    "evaluation",
    "approval_id",
    "artifact_id",
    "plan_manifest_id",
    "evidence_counts",
    "artifact_metadata_sha256",
    "generated_at",
    "generated_by_user_id",
    "omission_flags",
    "payload_sha256",
}


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_hex(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def stable_receipt_id(workspace_id: str, run_id: str) -> str:
    digest = hashlib.sha256(f"{workspace_id}\0{run_id}".encode("utf-8")).hexdigest()
    return f"phr_{digest[:24]}"


def artifact_metadata_sha256(artifact: Mapping[str, Any]) -> str:
    """Hash bounded artifact metadata without reading its URI or filesystem target."""
    projection = {
        "artifact_id": str(artifact.get("artifact_id") or ""),
        "artifact_type": str(artifact.get("artifact_type") or ""),
        "title": str(artifact.get("title") or ""),
        "summary": str(artifact.get("summary") or ""),
        "created_at": str(artifact.get("created_at") or ""),
    }
    return sha256_hex(projection)


def receipt_payload_sha256(receipt: Mapping[str, Any]) -> str:
    projection = {key: value for key, value in receipt.items() if key != "payload_sha256"}
    return sha256_hex(projection)


def build_acceptance_receipt(
    *,
    host_version: str,
    git_commit: str,
    workspace_id: str,
    task_id: str,
    run_id: str,
    adapter: str,
    status: str,
    evaluation: Mapping[str, Any],
    approval_id: str,
    artifact_id: str,
    plan_manifest_id: str,
    evidence_counts: Mapping[str, int],
    artifact_metadata_sha256: str,
    generated_at: str,
    generated_by_user_id: str,
) -> dict[str, Any]:
    receipt: dict[str, Any] = {
        "receipt_id": stable_receipt_id(workspace_id, run_id),
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "host_version": str(host_version or "development"),
        "git_commit": str(git_commit or "unknown"),
        "workspace_id": str(workspace_id),
        "task_id": str(task_id),
        "run_id": str(run_id),
        "adapter": str(adapter),
        "status": str(status),
        "evaluation": {
            "evaluation_id": str(evaluation.get("evaluation_id") or ""),
            "evaluator_type": str(evaluation.get("evaluator_type") or ""),
            "score": float(evaluation.get("score") or 0),
            "pass_fail": str(evaluation.get("pass_fail") or ""),
        },
        "approval_id": str(approval_id),
        "artifact_id": str(artifact_id),
        "plan_manifest_id": str(plan_manifest_id),
        "evidence_counts": {
            str(key): int(value)
            for key, value in sorted(evidence_counts.items())
        },
        "artifact_metadata_sha256": str(artifact_metadata_sha256),
        "generated_at": str(generated_at),
        "generated_by_user_id": str(generated_by_user_id),
        "omission_flags": {
            "row_contents_omitted": True,
            "filesystem_paths_omitted": True,
            "url_query_omitted": True,
            "tokens_omitted": True,
            "raw_prompt_omitted": True,
            "raw_response_omitted": True,
            "artifact_file_content_omitted": True,
        },
    }
    receipt["payload_sha256"] = receipt_payload_sha256(receipt)
    return receipt


def verify_acceptance_receipt(receipt: Mapping[str, Any]) -> bool:
    if set(receipt) != RECEIPT_KEYS:
        return False
    receipt_id = str(receipt.get("receipt_id") or "")
    if not RECEIPT_ID_RE.fullmatch(receipt_id):
        return False
    expected_id = stable_receipt_id(str(receipt.get("workspace_id") or ""), str(receipt.get("run_id") or ""))
    supplied_hash = str(receipt.get("payload_sha256") or "")
    artifact_hash = str(receipt.get("artifact_metadata_sha256") or "")
    omissions = receipt.get("omission_flags")
    evaluation = receipt.get("evaluation")
    return (
        receipt_id == expected_id
        and bool(SHA256_RE.fullmatch(supplied_hash))
        and bool(SHA256_RE.fullmatch(artifact_hash))
        and supplied_hash == receipt_payload_sha256(receipt)
        and receipt.get("status") == "completed"
        and isinstance(evaluation, Mapping)
        and set(evaluation) == {"evaluation_id", "evaluator_type", "score", "pass_fail"}
        and evaluation.get("pass_fail") == "pass"
        and isinstance(omissions, Mapping)
        and set(omissions) == OMISSION_FLAGS
        and all(value is True for value in omissions.values())
    )


def receipt_json_bytes(receipt: Mapping[str, Any]) -> bytes:
    return (json.dumps(dict(receipt), ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def receipt_download_filename(receipt_id: str) -> str:
    value = str(receipt_id or "")
    if not RECEIPT_ID_RE.fullmatch(value):
        value = "phr_invalid"
    return f"host-acceptance-receipt-{value}.json"
