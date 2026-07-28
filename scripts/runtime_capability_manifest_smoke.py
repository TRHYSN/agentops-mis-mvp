#!/usr/bin/env python3
"""Verify runtime connector capability manifests are a hard, public contract."""
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

REQUIRED_CONNECTORS = {
    "rtc_agent_gateway_local",
    "rtc_codex_local",
    "rtc_openclaw_local",
    "rtc_hermes_default_gateway",
    "rtc_agnesfallback_cli",
    "rtc_agnesfallback_openai_api",
}
REQUIRED_CAPABILITIES = {
    "filesystem",
    "shell",
    "network",
    "git",
    "external_write",
    "confirmation",
    "trust_policy",
    "secrets",
    "tool_event_ingestion",
}
REQUIRED_GOVERNANCE = {
    "requires_confirm_run",
    "requires_prepared_action_for_external_write",
    "trust_status_source",
    "live_execution_blocked_when_trust_status_blocked",
    "shared_commercial_policy",
}
SECRET_MARKERS = ("AGENTOPS_API_KEY", "Authorization:", "Bearer ", "agtok_", "agtsess_", "sk-", "ntn_")


def http_json(base_url: str, path: str) -> tuple[int, object]:
    req = urllib.request.Request(base_url.rstrip("/") + path, headers={"Accept": "application/json"}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(raw)
        except Exception:
            return exc.code, {"raw": raw}


def run_cli(base_url: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.pop("AGENTOPS_API_KEY", None)
    return subprocess.run(
        [str(CLI), "--base-url", base_url, "runtime", "connectors"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def leaked_secret(text: str) -> bool:
    return any(marker in text for marker in SECRET_MARKERS)


def manifest_for(row: dict) -> dict:
    manifest = row.get("capability_manifest")
    if isinstance(manifest, dict):
        return manifest
    raw = row.get("capability_manifest_json")
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return {}
    return {}


def connector_id(row: dict) -> str:
    return str(row.get("runtime_connector_id") or row.get("connector_id") or "")


def validate_connectors(connectors: list[dict], failures: list[str], source: str) -> None:
    observed = {connector_id(row) for row in connectors}
    require(REQUIRED_CONNECTORS.issubset(observed), f"{source} missing connectors: {sorted(REQUIRED_CONNECTORS - observed)}", failures)

    for row in connectors:
        cid = connector_id(row)
        if cid not in REQUIRED_CONNECTORS:
            continue
        manifest = manifest_for(row)
        capabilities = manifest.get("capabilities") or {}
        governance = manifest.get("governance") or {}
        require(manifest.get("schema_version") == "runtime-capability-manifest-v1", f"{source} {cid} missing manifest schema", failures)
        require(manifest.get("connector_id") == cid, f"{source} {cid} manifest connector mismatch", failures)
        require(manifest.get("manifest_hash") == row.get("capability_policy_hash"), f"{source} {cid} hash mismatch", failures)
        require(row.get("token_omitted") is True, f"{source} {cid} token omission proof missing", failures)
        require(manifest.get("token_omitted") is True, f"{source} {cid} manifest token omission proof missing", failures)
        require(manifest.get("raw_prompt_omitted") is True, f"{source} {cid} prompt omission proof missing", failures)
        require(manifest.get("raw_response_omitted") is True, f"{source} {cid} response omission proof missing", failures)
        require(REQUIRED_CAPABILITIES.issubset(capabilities), f"{source} {cid} missing capabilities: {sorted(REQUIRED_CAPABILITIES - set(capabilities))}", failures)
        require(REQUIRED_GOVERNANCE.issubset(governance), f"{source} {cid} missing governance: {sorted(REQUIRED_GOVERNANCE - set(governance))}", failures)
        require(row.get("trust_status") in {"trusted", "review_required", "blocked"}, f"{source} {cid} bad trust status", failures)
        require(row.get("observation_level") == manifest.get("observation_level"), f"{source} {cid} observation mismatch", failures)
        if cid in {"rtc_openclaw_local", "rtc_hermes_default_gateway"}:
            require(manifest.get("risk_floor") == "medium", f"{source} {cid} live runtime risk floor must be medium", failures)
            require(manifest.get("observation_level") == "ledger_summary_only", f"{source} {cid} must disclose summary-only observation", failures)
            require(governance.get("requires_confirm_run") is True, f"{source} {cid} live runtime must require confirmation", failures)
            require(governance.get("requires_prepared_action_for_external_write") is True, f"{source} {cid} external write governance missing", failures)
        if cid == "rtc_codex_local":
            require(manifest.get("risk_floor") == "medium", f"{source} {cid} risk floor must be medium", failures)
            require(manifest.get("observation_level") == "structured_runtime_events", f"{source} {cid} structured observation missing", failures)
            require(manifest.get("commercial_readiness") == "read_only_governed_worker", f"{source} {cid} read-only product boundary missing", failures)
            require(governance.get("requires_confirm_run") is True, f"{source} {cid} must require confirmed execution", failures)
            require(governance.get("requires_prepared_action_for_external_write") is True, f"{source} {cid} workspace-write governance missing", failures)
            require(row.get("binary_path") in {None, ""}, f"{source} {cid} must omit local binary path", failures)
        if cid.startswith("rtc_agnesfallback"):
            require(manifest.get("observation_level") == "fixed_probe_summary_only", f"{source} {cid} fixed-probe boundary missing", failures)


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify runtime capability manifest API and CLI.")
    parser.add_argument("--base-url", default=os.environ.get("AGENTOPS_BASE_URL", "http://127.0.0.1:8787"))
    args = parser.parse_args()
    failures: list[str] = []

    status, api_payload = http_json(args.base_url, "/api/runtime-connectors")
    require(status == 200 and isinstance(api_payload, list), f"runtime connectors API failed: {status} {api_payload}", failures)
    if isinstance(api_payload, list):
        validate_connectors([row for row in api_payload if isinstance(row, dict)], failures, "api")

    proc = run_cli(args.base_url)
    require(proc.returncode == 0, f"runtime connector CLI failed: {proc.stderr or proc.stdout}", failures)
    require(not leaked_secret(proc.stdout + proc.stderr), "runtime connector CLI leaked token-like material", failures)
    cli_payload = {}
    if proc.stdout.strip():
        try:
            cli_payload = json.loads(proc.stdout)
        except Exception as exc:
            failures.append(f"runtime connector CLI returned invalid JSON: {exc}")
    require(cli_payload.get("live_execution_performed") is False, "runtime connector CLI must not execute live work", failures)
    require(cli_payload.get("token_omitted") is True, "runtime connector CLI token omission proof missing", failures)
    cli_connectors = cli_payload.get("connectors") if isinstance(cli_payload, dict) else None
    require(isinstance(cli_connectors, list), f"runtime connector CLI missing connectors: {cli_payload}", failures)
    if isinstance(cli_connectors, list):
        validate_connectors([row for row in cli_connectors if isinstance(row, dict)], failures, "cli")

    output = {
        "ok": not failures,
        "operation": "runtime_capability_manifest_smoke",
        "connector_count": len(api_payload) if isinstance(api_payload, list) else 0,
        "required_connectors": sorted(REQUIRED_CONNECTORS),
        "live_execution_performed": False,
        "secret_leaked": False,
        "failures": failures,
    }
    serialized = json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True)
    if leaked_secret(serialized):
        output["ok"] = False
        output["secret_leaked"] = True
        output["failures"] = [*failures, "runtime capability smoke output leaked token-like material"]
        serialized = json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True)
    print(serialized)
    return 0 if output["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
