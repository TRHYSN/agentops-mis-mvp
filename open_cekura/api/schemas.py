"""Stable response helpers for the Reliability Lab HTTP projection."""

from __future__ import annotations

from typing import Any


API_SCHEMA_VERSION = "open_cekura.reliability_api.v1"


def safety_contract() -> dict[str, bool]:
    return {
        "read_only": True,
        "ledger_mutated": False,
        "live_execution_performed": False,
        "token_omitted": True,
        "raw_hidden_prompts_omitted": True,
    }


def success_envelope(
    *,
    operation: str,
    workspace_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": API_SCHEMA_VERSION,
        "provider": "open-cekura",
        "operation": operation,
        "workspace_id": workspace_id,
        **payload,
        "safety": safety_contract(),
        "token_omitted": True,
    }


def error_envelope(error: str, message: str, **extra: Any) -> dict[str, Any]:
    return {
        "schema_version": API_SCHEMA_VERSION,
        "provider": "open-cekura",
        "error": error,
        "message": message,
        **extra,
        "token_omitted": True,
    }


__all__ = [
    "API_SCHEMA_VERSION",
    "error_envelope",
    "safety_contract",
    "success_envelope",
]
