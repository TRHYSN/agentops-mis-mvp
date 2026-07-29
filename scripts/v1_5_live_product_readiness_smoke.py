#!/usr/bin/env python3
"""Read-only product-readiness proof from live Hermes/OpenClaw ledger evidence.

This does not call runtimes. Run customer_worker_real_runtime_acceptance.py first
when local Hermes/OpenClaw are authorized, then use this script to verify the
ledger contains fresh, complete product evidence for the requested adapters.
"""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import re
import sys
import urllib.parse
import urllib.request


TOKEN_PATTERNS = [
    re.compile(r"Authorization:", re.IGNORECASE),
    re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"\bagtok_[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bagtsess_[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bntn_[A-Za-z0-9_-]{16,}"),
]

REQUIRED_EVIDENCE = [
    "completed_adapter_tool_calls",
    "passing_evaluations",
    "runtime_events",
    "audit_logs",
    "customer_worker_artifacts",
    "memories",
    "approvals",
    "verified_plan_evidence_manifests",
]


def token_leaked(text: str) -> bool:
    return any(pattern.search(text or "") for pattern in TOKEN_PATTERNS)


def http_get_json(base_url: str, path: str, timeout: int = 30, *, opener=None) -> tuple[int, dict]:
    req = urllib.request.Request(base_url.rstrip("/") + path, headers={"Accept": "application/json"}, method="GET")
    with (opener.open(req, timeout=timeout) if opener else urllib.request.urlopen(req, timeout=timeout)) as resp:
        raw = resp.read().decode("utf-8")
        return resp.status, json.loads(raw or "{}")


def authenticated_human_opener(args: argparse.Namespace):
    password = os.environ.get(args.password_env, "")
    if not password:
        raise RuntimeError(f"Private Host human auth requires password env: {args.password_env}")
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    status, auth_status = http_get_json(args.base_url, "/api/human-auth/status", args.timeout, opener=opener)
    if status != 200 or auth_status.get("required") is not True:
        raise RuntimeError("Private Host did not report required human authentication")
    if auth_status.get("bootstrap_required"):
        raise RuntimeError("Private Host Owner must be bootstrapped before read-only readiness")
    body = json.dumps({"username": args.username, "password": password}).encode("utf-8")
    request = urllib.request.Request(
        args.base_url.rstrip("/") + "/api/human-auth/login",
        data=body,
        headers={"Content-Type": "application/json", "Origin": args.origin or args.base_url.rstrip("/")},
        method="POST",
    )
    with opener.open(request, timeout=args.timeout) as response:
        payload = json.loads(response.read().decode("utf-8") or "{}")
    if response.status != 200 or (payload.get("user") or {}).get("role") != "owner":
        raise RuntimeError("Private Host Owner authentication failed")
    return opener


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def adapter_summary(adapter: str, item: dict, failures: list[str]) -> dict:
    latest = item.get("latest_passing") or item.get("latest_attempt") or {}
    evidence = latest.get("evidence") or {}
    checks = latest.get("checks") or []
    require(item.get("status") == "fresh", f"{adapter}: expected fresh status, got {item.get('status')}", failures)
    require(item.get("ok") is True, f"{adapter}: ok flag not true", failures)
    require(latest.get("pass") is True, f"{adapter}: latest passing evidence missing", failures)
    require(latest.get("run_status") == "completed", f"{adapter}: latest run not completed: {latest}", failures)
    for key in REQUIRED_EVIDENCE:
        require(int(evidence.get(key) or 0) >= 1, f"{adapter}: missing evidence {key}: {evidence}", failures)
    failed_checks = [check.get("id") for check in checks if not check.get("ok")]
    require(not failed_checks, f"{adapter}: failed acceptance checks: {failed_checks}", failures)
    return {
        "adapter": adapter,
        "status": item.get("status"),
        "run_id": latest.get("run_id"),
        "task_id": latest.get("task_id"),
        "artifact_id": latest.get("artifact_id"),
        "plan_evidence_manifest_id": latest.get("plan_evidence_manifest_id"),
        "age_hours": latest.get("age_hours"),
        "evidence": {key: int(evidence.get(key) or 0) for key in REQUIRED_EVIDENCE},
        "token_omitted": item.get("token_omitted") is True and latest.get("token_omitted") is True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify live AgentOps MIS product readiness from ledger evidence only.")
    parser.add_argument("--base-url", default=os.environ.get("AGENTOPS_BASE_URL", "http://127.0.0.1:8787"))
    parser.add_argument("--freshness-hours", type=int, default=72)
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--require-adapter", action="append", choices=["hermes", "openclaw"], default=None)
    parser.add_argument("--human-auth", action="store_true", help="Authenticate through a Private Host human Session.")
    parser.add_argument("--origin", default=os.environ.get("AGENTOPS_ACCEPTANCE_ORIGIN", ""))
    parser.add_argument("--username", default=os.environ.get("AGENTOPS_ACCEPTANCE_USERNAME", "owner"))
    parser.add_argument("--password-env", default="AGENTOPS_ACCEPTANCE_PASSWORD")
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args()
    required_adapters = args.require_adapter or ["hermes", "openclaw"]
    failures: list[str] = []
    try:
        opener = authenticated_human_opener(args) if args.human_auth else None
        query = urllib.parse.urlencode({"freshness_hours": args.freshness_hours, "limit": args.limit})
        live_status, live = http_get_json(args.base_url, f"/api/operator/live-acceptance?{query}", args.timeout, opener=opener)
        local_status, local = http_get_json(args.base_url, "/api/local/readiness", args.timeout, opener=opener)
        require(live_status == 200, f"live-acceptance HTTP status {live_status}", failures)
        require(local_status == 200, f"local-readiness HTTP status {local_status}", failures)
        require(live.get("operation") == "live_acceptance_readiness", f"wrong live operation: {live}", failures)
        require(live.get("live_execution_performed") is False, "live readback must not execute runtimes", failures)
        require((live.get("safety") or {}).get("read_only") is True, f"live safety missing: {live}", failures)
        require(local.get("operation") == "local_readiness", f"wrong local operation: {local}", failures)
        require(local.get("live_execution_performed") is False, "local readiness must not execute runtimes", failures)
        adapters = live.get("adapters") or {}
        summaries = []
        for adapter in required_adapters:
            require(adapter in adapters, f"missing adapter {adapter} in live acceptance", failures)
            if adapter in adapters:
                summaries.append(adapter_summary(adapter, adapters[adapter], failures))
        live_summary = live.get("summary") or {}
        local_evidence = local.get("evidence") or {}
        require(int(live_summary.get("fresh") or 0) >= len(required_adapters), f"not enough fresh adapters: {live_summary}", failures)
        require(int(local_evidence.get("live_acceptance_fresh_adapters") or 0) >= len(required_adapters), f"local readiness missing fresh live adapters: {local_evidence}", failures)
        gates = {gate.get("id"): gate for gate in (local.get("gates") or [])}
        live_gate = gates.get("live_acceptance_freshness") or {}
        require(live_gate.get("ok") is True, f"local live acceptance gate not green: {live_gate}", failures)
        output = {
            "operation": "v1_5_live_product_readiness",
            "ok": not failures,
            "product_readiness_proof": not failures,
            "evidence_class": "manual_live_ledger_readback",
            "base_url": args.base_url.rstrip("/"),
            "freshness_hours": args.freshness_hours,
            "required_adapters": required_adapters,
            "human_session_used": bool(args.human_auth),
            "adapters": summaries,
            "live_acceptance_status": live.get("status"),
            "local_readiness_status": local.get("status"),
            "failures": failures,
            "safety": {
                "read_only": True,
                "ledger_mutated": False,
                "live_execution_performed": False,
                "raw_prompt_omitted": True,
                "raw_response_omitted": True,
                "token_omitted": True,
            },
        }
        serialized = json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True)
        if token_leaked(serialized):
            output["ok"] = False
            output["product_readiness_proof"] = False
            output["failures"].append("output leaked token-like material")
            serialized = json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True)
        print(serialized)
        return 0 if output["ok"] else 1
    except Exception as exc:
        print(json.dumps({
            "operation": "v1_5_live_product_readiness",
            "ok": False,
            "product_readiness_proof": False,
            "error": str(exc),
            "failures": failures or [str(exc)],
            "safety": {
                "read_only": True,
                "ledger_mutated": False,
                "live_execution_performed": False,
                "token_omitted": True,
            },
        }, ensure_ascii=False, indent=2, sort_keys=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
