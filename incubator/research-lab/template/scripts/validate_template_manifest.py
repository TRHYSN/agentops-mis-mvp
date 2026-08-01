#!/usr/bin/env python3
"""Deterministic, zero-dependency validator for the Research Lab template contract."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

CORE_REQUIRED = {
    "workspaces",
    "projects",
    "tasks",
    "agent_plans",
    "runs",
    "approvals",
    "artifacts",
    "evaluations",
    "audit",
    "external_bases",
}

RESEARCH_REQUIRED = {
    "research_project",
    "experiment_protocol",
    "trial",
    "job_attempt",
    "dataset_version",
    "environment_snapshot",
    "metric_snapshot",
    "checkpoint",
    "model_version",
    "scientific_review",
    "paper_evidence",
    "server_profile",
}

FORBIDDEN_INSTANCE_KEYS = {
    "password",
    "passwd",
    "api_key",
    "token",
    "secret",
    "private_key",
}


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"missing file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {path}: {exc}") from exc


def require(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def walk_keys(value: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            keys.add(str(key).lower())
            keys.update(walk_keys(child))
    elif isinstance(value, list):
        for child in value:
            keys.update(walk_keys(child))
    return keys


def validate(manifest: dict[str, Any], instance: dict[str, Any] | None = None) -> list[str]:
    errors: list[str] = []

    require(manifest.get("schema_version") == "1.0", "schema_version must be 1.0", errors)
    require(manifest.get("template_id") == "research-lab", "template_id must be research-lab", errors)
    require(manifest.get("template_kind") == "embedded_solution", "template_kind must be embedded_solution", errors)
    require(manifest.get("canonical") is False, "foundation manifest must remain canonical=false", errors)

    core = set(manifest.get("requires_core_capabilities", []))
    require(CORE_REQUIRED.issubset(core), f"missing core capabilities: {sorted(CORE_REQUIRED - core)}", errors)

    domain_objects = manifest.get("domain_objects", [])
    object_ids = {item.get("id") for item in domain_objects if isinstance(item, dict)}
    require(RESEARCH_REQUIRED.issubset(object_ids), f"missing research objects: {sorted(RESEARCH_REQUIRED - object_ids)}", errors)
    for item in domain_objects:
        if not isinstance(item, dict):
            errors.append("every domain object must be an object")
            continue
        require(bool(item.get("maps_to")), f"domain object {item.get('id')} must map to Core MIS", errors)

    routes = [item.get("route", "") for item in manifest.get("navigation", []) if isinstance(item, dict)]
    require(bool(routes), "navigation must not be empty", errors)
    for route in routes:
        require(route.startswith("/workspaces/:workspaceId/research"), f"route is not embedded in MIS workspace: {route}", errors)

    connectors = manifest.get("connectors", [])
    require(bool(connectors), "connectors must not be empty", errors)
    for connector in connectors:
        if not isinstance(connector, dict):
            errors.append("every connector must be an object")
            continue
        connector_id = connector.get("id", "<unknown>")
        require(connector.get("summary_projection") is True, f"connector {connector_id} needs in-app summary projection", errors)
        require(connector.get("side_effects") in {"disabled_by_default", "approval_required"}, f"connector {connector_id} has unsafe side_effects policy", errors)

    privacy = manifest.get("privacy", {})
    require(privacy.get("store_credentials") is False, "template cannot store credentials", errors)
    require(privacy.get("store_raw_prompts_by_default") is False, "raw prompts must be off by default", errors)
    require(privacy.get("store_raw_training_logs_in_ledger") is False, "raw training logs cannot be copied into ledger", errors)

    lifecycle = manifest.get("lifecycle", {})
    require(lifecycle.get("disable") == "hide_projections_keep_authority_records", "disable must preserve Core authority records", errors)

    if instance is not None:
        require(instance.get("template_id") == manifest.get("template_id"), "instance template_id mismatch", errors)
        require(instance.get("template_version") == manifest.get("template_version"), "instance template_version mismatch", errors)
        require(instance.get("workspace", {}).get("mode") == "embedded_mis", "instance must use embedded_mis mode", errors)
        keys = walk_keys(instance)
        present_forbidden = sorted(keys & FORBIDDEN_INSTANCE_KEYS)
        require(not present_forbidden, f"instance includes forbidden credential keys: {present_forbidden}", errors)
        require(instance.get("security", {}).get("contains_credentials") is False, "instance must declare contains_credentials=false", errors)

    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--instance", type=Path)
    args = parser.parse_args()

    manifest = load_json(args.manifest)
    instance = load_json(args.instance) if args.instance else None
    errors = validate(manifest, instance)
    result = {
        "ok": not errors,
        "template_id": manifest.get("template_id"),
        "template_version": manifest.get("template_version"),
        "domain_object_count": len(manifest.get("domain_objects", [])),
        "navigation_count": len(manifest.get("navigation", [])),
        "connector_count": len(manifest.get("connectors", [])),
        "errors": errors,
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
