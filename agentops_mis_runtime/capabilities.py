"""Pure runtime connector capability manifest helpers.

This module is intentionally free of SQLite, HTTP server, subprocess, and
provider-call dependencies. Server routes may persist and expose these manifests,
but the policy shape lives here so it can be tested without booting the MIS app.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "runtime-capability-manifest-v1"


def stable_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def runtime_connector_adapter(connector_id: str, provider: str) -> str:
    if connector_id == "rtc_agent_gateway_local" or provider == "agent-gateway":
        return "agent_gateway"
    if connector_id == "rtc_codex_local" or provider == "codex":
        return "codex"
    if connector_id == "rtc_hermes_default_gateway" or provider == "hermes":
        return "hermes"
    if connector_id == "rtc_openclaw_local" or provider == "openclaw":
        return "openclaw"
    if provider == "agnesfallback":
        return "agnesfallback"
    return "mock"


def runtime_connector_capability_manifest(
    connector_id: str,
    provider: str,
    connector_type: str,
    *,
    repo_root: str | Path | None = None,
) -> dict:
    adapter = runtime_connector_adapter(connector_id, provider)
    root = str(repo_root) if repo_root is not None else "local://agentops-mis"
    base = {
        "schema_version": SCHEMA_VERSION,
        "connector_id": connector_id,
        "provider": provider,
        "connector_type": connector_type,
        "adapter": adapter,
        "token_omitted": True,
        "raw_prompt_omitted": True,
        "raw_response_omitted": True,
    }
    manifests = {
        "agent_gateway": {
            "observation_level": "structured_ledger",
            "risk_floor": "low",
            "commercial_readiness": "local_demo_ready",
            "capabilities": {
                "filesystem": "none",
                "shell": "repo_local_cli_wrapper",
                "network": "loopback_http_api",
                "git": "none",
                "external_write": "none_without_worker_or_prepared_action",
                "confirmation": "not_required_for_read_only_gateway_calls",
                "trust_policy": "runtime_connector_trust_registry",
                "secrets": "scoped_tokens_env_or_config_not_ledger",
                "tool_event_ingestion": "structured",
            },
            "boundaries": {
                "workdir": "local://agentops-mis",
                "network": "127.0.0.1 agent-gateway API",
                "external_side_effects": "disabled_without_explicit_worker_or_prepared_action",
            },
            "governance": {
                "requires_confirm_run": False,
                "requires_prepared_action_for_external_write": True,
                "trust_status_source": "runtime_connectors.trust_status",
                "live_execution_blocked_when_trust_status_blocked": True,
                "shared_commercial_policy": "scoped_token_required_outside_loopback_local_dev",
            },
        },
        "mock": {
            "observation_level": "structured_ledger",
            "risk_floor": "low",
            "commercial_readiness": "local_demo_ready",
            "capabilities": {
                "filesystem": "none",
                "shell": "none",
                "network": "none",
                "git": "none",
                "external_write": "none",
                "confirmation": "not_required_for_mock_execution",
                "trust_policy": "runtime_connector_trust_registry",
                "secrets": "none",
                "tool_event_ingestion": "structured",
            },
            "boundaries": {
                "workdir": "local://agentops/mock-worker",
                "network": "disabled",
                "external_side_effects": "disabled",
            },
            "governance": {
                "requires_confirm_run": False,
                "requires_prepared_action_for_external_write": False,
                "trust_status_source": "runtime_connectors.trust_status",
                "live_execution_blocked_when_trust_status_blocked": True,
                "shared_commercial_policy": "allowed_for_tests_only",
            },
        },
        "codex": {
            "observation_level": "structured_runtime_events",
            "risk_floor": "medium",
            "commercial_readiness": "read_only_governed_worker",
            "capabilities": {
                "filesystem": "read_only_default; managed_worktree_only_after_approval",
                "shell": "disabled_in_read_only",
                "network": "disabled",
                "git": "read_only_default; bounded_diff_in_managed_worktree_after_approval",
                "external_write": "workspace_write_prepared_action_only",
                "confirmation": "confirm_run_required; workspace_write_requires_separate_confirmation",
                "trust_policy": "runtime_connector_trust_registry",
                "secrets": "allowlisted_child_env_without_agentops_credentials",
                "tool_event_ingestion": "structured_codex_jsonl_summary",
            },
            "boundaries": {
                "workdir": root,
                "network": "disabled_by_worker_policy",
                "external_side_effects": "prepared_action_and_execution_lease_required",
            },
            "governance": {
                "requires_confirm_run": True,
                "requires_prepared_action_for_external_write": True,
                "trust_status_source": "runtime_connectors.trust_status",
                "live_execution_blocked_when_trust_status_blocked": True,
                "shared_commercial_policy": "read_only_allowed; workspace_write_requires_attested_official_bundle_and_human_approval",
            },
        },
        "hermes": {
            "observation_level": "ledger_summary_only",
            "risk_floor": "medium",
            "commercial_readiness": "restricted_until_runtime_tool_events",
            "capabilities": {
                "filesystem": "runtime_internal_opaque",
                "shell": "runtime_internal_opaque",
                "network": "runtime_internal_opaque",
                "git": "runtime_internal_opaque",
                "external_write": "must_route_through_mis_guarded_tools",
                "confirmation": "confirm_run_required_for_live_execution",
                "trust_policy": "runtime_connector_trust_registry",
                "secrets": "runtime_env_only_not_ledger",
                "tool_event_ingestion": "summary_hash_only",
            },
            "boundaries": {
                "workdir": "runtime_owned",
                "network": "runtime_policy_required",
                "external_side_effects": "prepared_action_required",
            },
            "governance": {
                "requires_confirm_run": True,
                "requires_prepared_action_for_external_write": True,
                "trust_status_source": "runtime_connectors.trust_status",
                "live_execution_blocked_when_trust_status_blocked": True,
                "shared_commercial_policy": "restricted_when_tool_events_unavailable",
            },
        },
        "openclaw": {
            "observation_level": "ledger_summary_only",
            "risk_floor": "medium",
            "commercial_readiness": "restricted_until_runtime_tool_events",
            "capabilities": {
                "filesystem": "runtime_internal_opaque",
                "shell": "runtime_internal_opaque",
                "network": "runtime_internal_opaque",
                "git": "runtime_internal_opaque",
                "external_write": "must_route_through_mis_guarded_tools",
                "confirmation": "confirm_run_required_for_live_execution",
                "trust_policy": "runtime_connector_trust_registry",
                "secrets": "runtime_env_only_not_ledger",
                "tool_event_ingestion": "summary_hash_only",
            },
            "boundaries": {
                "workdir": root,
                "config": "~/.openclaw",
                "network": "runtime_policy_required",
                "external_side_effects": "prepared_action_required",
            },
            "governance": {
                "requires_confirm_run": True,
                "requires_prepared_action_for_external_write": True,
                "trust_status_source": "runtime_connectors.trust_status",
                "live_execution_blocked_when_trust_status_blocked": True,
                "shared_commercial_policy": "restricted_when_tool_events_unavailable",
            },
        },
        "agnesfallback": {
            "observation_level": "fixed_probe_summary_only",
            "risk_floor": "low",
            "commercial_readiness": "local_recording_only",
            "capabilities": {
                "filesystem": "none_declared",
                "shell": "cli_or_openai_compatible_gateway",
                "network": "local_loopback_gateway_optional",
                "git": "none_declared",
                "external_write": "none_for_fixed_probe",
                "confirmation": "confirm_run_required_for_fixed_probe",
                "trust_policy": "runtime_connector_trust_registry",
                "secrets": "runtime_env_only_not_ledger",
                "tool_event_ingestion": "summary_hash_only",
            },
            "boundaries": {
                "workdir": "runtime_owned",
                "network": "127.0.0.1 only for gateway profile",
                "external_side_effects": "disabled_for_fixed_probe",
            },
            "governance": {
                "requires_confirm_run": True,
                "requires_prepared_action_for_external_write": True,
                "trust_status_source": "runtime_connectors.trust_status",
                "live_execution_blocked_when_trust_status_blocked": True,
                "shared_commercial_policy": "not_a_general_worker_adapter",
            },
        },
    }
    manifest = {**base, **manifests.get(adapter, manifests["mock"])}
    manifest["manifest_hash"] = stable_hash({k: v for k, v in manifest.items() if k != "manifest_hash"})
    return manifest


def runtime_connector_for_adapter(adapter: str) -> str | None:
    if adapter == "codex":
        return "rtc_codex_local"
    if adapter == "hermes":
        return "rtc_hermes_default_gateway"
    if adapter == "openclaw":
        return "rtc_openclaw_local"
    if adapter == "mock":
        return "rtc_agent_gateway_local"
    return None


def runtime_connector_public_row(row) -> dict:
    item = dict(row)
    manifest = {}
    if item.get("capability_manifest_json"):
        try:
            manifest = json.loads(item.get("capability_manifest_json") or "{}")
        except Exception:
            manifest = {}
    item["capability_manifest"] = manifest
    item["capability_policy_hash"] = item.get("capability_policy_hash") or manifest.get("manifest_hash")
    item["token_omitted"] = True
    item["raw_prompt_omitted"] = True
    item["raw_response_omitted"] = True
    return item
