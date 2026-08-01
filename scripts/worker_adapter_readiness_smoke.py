#!/usr/bin/env python3
"""Verify worker adapter readiness is available through API and CLI."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "agentops"


def http_json(base_url: str, path: str) -> tuple[int, dict]:
    req = urllib.request.Request(base_url.rstrip("/") + path, headers={"Accept": "application/json"}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8"))
        except Exception:
            body = {"error": exc.reason}
        return exc.code, body


def run_cli(base_url: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.pop("AGENTOPS_API_KEY", None)
    return subprocess.run(
        [str(CLI), "--base-url", base_url, "worker", "readiness"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )


def leaked_secret(text: str) -> bool:
    markers = ["AGENTOPS_API_KEY", "Authorization:", "Bearer ", "agtok_", "agtsess_", "sk-", "ntn_"]
    return any(marker in text for marker in markers)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def validate_readiness(payload: dict) -> None:
    require(payload.get("provider") == "agentops-worker", f"wrong provider: {payload}")
    require(payload.get("status") in {"ready", "degraded", "blocked"}, f"bad readiness status: {payload}")
    require(payload.get("live_execution_performed") is False, "readiness must not execute live work")
    require(payload.get("token_omitted") is True, "token omission proof missing")
    connection_policy = payload.get("worker_connection_policy") or {}
    require(connection_policy.get("schema") == "agentops-worker-connection-policy-v1", f"connection policy missing: {payload}")
    require((connection_policy.get("safety") or {}).get("read_only") is True, f"connection policy must be read-only: {connection_policy}")
    require((connection_policy.get("safety") or {}).get("live_execution_performed") is False, f"connection policy executed live work: {connection_policy}")
    require((connection_policy.get("safety") or {}).get("token_omitted") is True, f"connection policy token proof missing: {connection_policy}")
    session = connection_policy.get("session") or {}
    require(session.get("use_session_recommended") is True, f"session policy should recommend short-lived sessions: {connection_policy}")
    require(session.get("ttl_sec") == 900 and session.get("refresh_margin_sec") == 60, f"session defaults missing: {connection_policy}")
    require(session.get("parent_enrollment_token_storage") == "process_memory_only", f"parent token storage boundary missing: {connection_policy}")
    recommended_loop = str(connection_policy.get("recommended_remote_loop") or "")
    for flag in ("--session-refresh-margin-sec 60", "--idle-backoff-max 30", "--error-backoff-max 30", "--backoff-factor 2", "--adapter-max-attempts 1", "--adapter-retry-delay-sec 1", "--max-errors 5"):
        require(flag in recommended_loop, f"recommended remote loop missing {flag}: {connection_policy}")
    loop_backoff = connection_policy.get("loop_backoff") or {}
    require(loop_backoff.get("idle_reason") == "idle_backoff" and loop_backoff.get("error_reason") == "error_backoff", f"backoff reasons missing: {connection_policy}")
    require(loop_backoff.get("idle_backoff_max_sec") == 30 and loop_backoff.get("error_backoff_max_sec") == 30, f"backoff caps missing: {connection_policy}")
    adapter_retry = connection_policy.get("adapter_retry") or {}
    require(adapter_retry.get("retryable_failures_can_retry") is True, f"retryable failure policy missing: {connection_policy}")
    require(adapter_retry.get("non_retryable_safety_gates_retry") is False, f"safety gates should not retry: {connection_policy}")
    daemon_resilience = connection_policy.get("daemon_resilience") or {}
    require(daemon_resilience.get("continue_on_error") is True and daemon_resilience.get("max_errors") == 5, f"daemon resilience policy missing: {connection_policy}")
    adapters = payload.get("adapters") or {}
    for adapter in ("mock", "codex", "hermes", "openclaw"):
        item = adapters.get(adapter) or {}
        require(item.get("adapter") == adapter, f"missing adapter {adapter}: {payload}")
        require(item.get("readiness") in {"ready", "review_required", "blocked", "unavailable"}, f"bad {adapter} readiness: {item}")
        require((item.get("checks") or {}).get("live_execution_performed") is False, f"{adapter} readiness executed live work")
        require(item.get("token_omitted") is True, f"{adapter} token omission proof missing")
        manifest = item.get("capability_manifest") or {}
        require(manifest.get("schema_version") == "runtime-capability-manifest-v1", f"{adapter} manifest missing schema: {item}")
        require(bool(item.get("capability_policy_hash")), f"{adapter} capability hash missing: {item}")
        require(item.get("observation_level") in {"structured_ledger", "structured_runtime_events", "ledger_summary_only"}, f"{adapter} observation level missing: {item}")
        require(item.get("risk_floor") in {"low", "medium"}, f"{adapter} risk floor missing: {item}")
        require(manifest.get("token_omitted") is True, f"{adapter} manifest token omission proof missing: {manifest}")
        remediation = item.get("remediation") or {}
        require(remediation.get("status") in {"ready", "action_required"}, f"{adapter} remediation status missing: {item}")
        require(bool(remediation.get("primary_next_action")), f"{adapter} remediation primary action missing: {item}")
        require((remediation.get("safety") or {}).get("read_only") is True, f"{adapter} remediation must be read-only: {item}")
        require((remediation.get("safety") or {}).get("live_execution_performed") is False, f"{adapter} remediation executed live work: {item}")
        commands = remediation.get("commands") or []
        require(any(command.get("phase") == "preflight" for command in commands), f"{adapter} remediation preflight missing: {item}")
        require(all(command.get("command") for command in commands), f"{adapter} remediation command missing: {item}")
    codex = adapters.get("codex") or {}
    require(codex.get("connector_id") == "rtc_codex_local", f"Codex connector mapping missing: {codex}")
    require(codex.get("target_resource") == "local://codex/read-only", f"Codex target must omit local path: {codex}")
    require(codex.get("observation_level") == "structured_runtime_events", f"Codex structured observation missing: {codex}")
    require(codex.get("commercial_readiness") == "read_only_governed_worker", f"Codex read-only commercial boundary missing: {codex}")
    require(isinstance(codex.get("workspace_write_ready"), bool), f"Codex workspace-write readiness missing: {codex}")
    codex_checks = codex.get("checks") or {}
    require(codex_checks.get("raw_binary_path_omitted") is True, f"Codex path omission proof missing: {codex}")
    require("binary_path" not in codex_checks, f"Codex checks leaked binary path: {codex}")
    require("/Applications/ChatGPT.app" not in json.dumps(codex, ensure_ascii=False), f"Codex readiness leaked raw application path: {codex}")
    codex_plugin = codex.get("client_plugin") or {}
    require(codex_plugin.get("package_name") == "agentops-mis", f"Codex client plugin package missing: {codex}")
    require(codex_plugin.get("packaged") is True, f"Codex client plugin not packaged: {codex}")
    require(codex_plugin.get("skill_available") is True, f"Codex client skill missing: {codex}")
    require(codex_plugin.get("marketplace_available") is True, f"Codex marketplace entry missing: {codex}")
    require(codex_plugin.get("mcp_tools_available") is False, f"Codex MCP tools must not be claimed yet: {codex}")
    require(codex_plugin.get("raw_path_omitted") is True, f"Codex plugin path omission proof missing: {codex}")
    codex_governance = ((codex.get("capability_manifest") or {}).get("governance") or {})
    require(codex_governance.get("requires_prepared_action_for_external_write") is True, f"Codex workspace-write governance missing: {codex}")
    codex_commands = (codex.get("remediation") or {}).get("commands") or []
    local_codex_command = next((command for command in codex_commands if command.get("phase") == "run_read_only"), {})
    remote_codex_command = next((command for command in codex_commands if command.get("phase") == "run_remote_scoped"), {})
    require(local_codex_command.get("confirm_required") is True, f"Codex confirmed read-only command missing: {codex}")
    require("--use-session" not in str(local_codex_command.get("command") or ""), f"Local Codex loop must not require enrollment session: {codex}")
    require("--use-session" in str(remote_codex_command.get("command") or ""), f"Remote Codex session command missing: {codex}")
    require(any(command.get("phase") == "prepare_workspace_write" and command.get("confirm_required") is True for command in codex_commands), f"Codex workspace-write preparation missing: {codex}")
    for adapter in ("hermes", "openclaw"):
        item = adapters.get(adapter) or {}
        require(item.get("observation_level") == "ledger_summary_only", f"{adapter} must disclose summary-only observation: {item}")
        require(item.get("commercial_readiness") == "restricted_until_runtime_tool_events", f"{adapter} commercial restriction missing: {item}")
        governance = ((item.get("capability_manifest") or {}).get("governance") or {})
        require(governance.get("requires_prepared_action_for_external_write") is True, f"{adapter} external write governance missing: {item}")
        remediation_commands = (item.get("remediation") or {}).get("commands") or []
        require(any(command.get("confirm_required") is True for command in remediation_commands), f"{adapter} live remediation commands should require confirmation: {item}")
        require(any("live-product-readiness" in str(command.get("command") or "") for command in remediation_commands), f"{adapter} live proof command missing: {item}")
    summary = payload.get("summary") or {}
    require(summary.get("recommended_adapter") in {"mock", "hermes", "openclaw"}, f"missing recommended adapter: {summary}")
    require("opaque_runtime_adapters" in summary, f"opaque adapter list missing: {summary}")
    policy = payload.get("capability_policy") or {}
    require(policy.get("manifest_schema") == "runtime-capability-manifest-v1", f"capability policy missing: {policy}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify worker adapter readiness.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8787")
    args = parser.parse_args()
    try:
        status_code, api_payload = http_json(args.base_url, "/api/workers/adapter-readiness")
        require(status_code == 200, f"adapter readiness API failed: {status_code} {api_payload}")
        validate_readiness(api_payload)

        status_code, worker_status = http_json(args.base_url, "/api/workers/status")
        require(status_code == 200, f"worker status API failed: {status_code} {worker_status}")
        status_summary = worker_status.get("adapter_readiness") or {}
        require(status_summary.get("recommended_adapter") in {"mock", "hermes", "openclaw"}, f"worker status lacks readiness summary: {worker_status}")

        proc = run_cli(args.base_url)
        require(proc.returncode == 0, f"CLI readiness failed: {proc.stderr or proc.stdout}")
        require(not leaked_secret(proc.stdout + proc.stderr), "CLI readiness leaked token-like material")
        cli_payload = json.loads(proc.stdout)
        validate_readiness(cli_payload)

        result = {
            "ok": True,
            "api_status": api_payload.get("status"),
            "connection_policy_schema": (api_payload.get("worker_connection_policy") or {}).get("schema"),
            "recommended_adapter": (api_payload.get("summary") or {}).get("recommended_adapter"),
            "ready_adapters": (api_payload.get("summary") or {}).get("ready_adapters"),
            "live_execution_performed": False,
            "secret_leaked": False,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2, sort_keys=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
