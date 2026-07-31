#!/usr/bin/env python3
"""Manual-live customer worker acceptance for Hermes/OpenClaw.

This is intentionally not part of deterministic CI. It is the product-level
dogfood check to run when local Hermes/OpenClaw are authorized and available.
Mock is not supported here; use mock-only smokes only as offline fallback.
"""
from __future__ import annotations

import argparse
import datetime as dt
import http.cookiejar
import json
import os
import re
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen


TOKEN_PATTERNS = [
    re.compile(r"Authorization:", re.IGNORECASE),
    re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"\bagtok_[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bagtsess_[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bntn_[A-Za-z0-9_-]{16,}"),
]


def token_leaked(text: str) -> bool:
    return any(pattern.search(text or "") for pattern in TOKEN_PATTERNS)


def http_json(
    method: str,
    base_url: str,
    path: str,
    payload: dict | None,
    timeout: int,
    *,
    opener=None,
    headers: dict | None = None,
) -> tuple[int, dict]:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = Request(
        base_url.rstrip("/") + path,
        data=data,
        headers={"Content-Type": "application/json", **(headers or {})},
        method=method,
    )
    try:
        with (opener.open(req, timeout=timeout) if opener else urlopen(req, timeout=timeout)) as res:
            raw = res.read().decode("utf-8")
            return res.status, json.loads(raw) if raw else {}
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(raw)
        except Exception:
            return exc.code, {"raw": raw}
    except URLError as exc:
        raise RuntimeError(f"Cannot reach {base_url}{path}: {exc.reason}") from exc


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def authenticate_human_session(args: argparse.Namespace, *, include_session_cookie: bool = False):
    password = os.environ.get(args.password_env, "")
    if not password:
        raise RuntimeError(f"Private Host human auth requires password env: {args.password_env}")
    cookie_jar = http.cookiejar.CookieJar()
    opener = build_opener(HTTPCookieProcessor(cookie_jar))
    status, auth_status = http_json("GET", args.base_url, "/api/human-auth/status", None, args.request_timeout, opener=opener)
    if status != 200 or auth_status.get("required") is not True:
        raise RuntimeError("Private Host did not report required human authentication")
    origin = args.origin or args.base_url.rstrip("/")
    if auth_status.get("bootstrap_required"):
        setup_code = os.environ.get(args.setup_code_env, "")
        if not setup_code:
            raise RuntimeError(f"Owner bootstrap requires setup-code env: {args.setup_code_env}")
        path = "/api/human-auth/bootstrap"
        body = {
            "setup_code": setup_code,
            "username": args.username,
            "display_name": "Private Host Acceptance Owner",
            "password": password,
        }
        expected = 201
    else:
        path = "/api/human-auth/login"
        body = {"username": args.username, "password": password}
        expected = 200
    status, authenticated = http_json(
        "POST",
        args.base_url,
        path,
        body,
        args.request_timeout,
        opener=opener,
        headers={"Origin": origin},
    )
    csrf_token = str(authenticated.get("csrf_token") or "")
    if status != expected or not csrf_token or (authenticated.get("user") or {}).get("role") != "owner":
        raise RuntimeError("Private Host Owner authentication failed")
    session_cookie = next(
        (cookie.value for cookie in cookie_jar if cookie.name == "agentops_human_session"),
        "",
    )
    if not session_cookie:
        raise RuntimeError("Private Host Owner Session cookie was not established")
    if include_session_cookie:
        return opener, csrf_token, origin, session_cookie
    return opener, csrf_token, origin


def customer_worker_payload(args: argparse.Namespace, adapter: str) -> dict:
    stamp = time.strftime("%Y%m%d%H%M%S")
    return {
        "adapter": adapter,
        "confirm_run": True,
        "title": f"真实 {adapter} Worker 产品级闭环验收 {stamp}",
        "description": (
            "以客户任务视角审视 AgentOps MIS 工作台，输出三条可执行改进建议。"
            "不要写文件，不要调用外部服务，只通过本地 runtime 返回摘要并写入 MIS 账本。"
        ),
        "acceptance_criteria": (
            "必须返回可读中文摘要，并写入 run/tool/evaluation/runtime/audit/artifact/"
            "memory/approval/plan-evidence 证据。"
        ),
        "priority": "high",
        "risk_level": "low",
        "hermes_timeout": args.hermes_timeout,
        "hermes_max_tokens": args.hermes_max_tokens,
    }


def run_adapter(args: argparse.Namespace, adapter: str, opener=None, csrf_token: str = "", origin: str = "") -> dict:
    payload = customer_worker_payload(args, adapter)
    headers = {"Origin": origin, "X-AgentOps-CSRF": csrf_token} if opener else None
    status, result = http_json(
        "POST",
        args.base_url,
        "/api/workflows/customer-worker-task",
        payload,
        args.request_timeout,
        opener=opener,
        headers=headers,
    )
    evidence = result.get("evidence") or {}
    worker_state = ((result.get("worker_result") or {}).get("state") or {})
    failure_context = {
        "provider": result.get("provider"),
        "workflow": result.get("workflow"),
        "adapter": result.get("adapter"),
        "ok": result.get("ok"),
        "dry_run": result.get("dry_run"),
        "reason": result.get("reason"),
        "note": result.get("note"),
    }
    failures: list[str] = []
    require(status == 201, f"{adapter}: expected HTTP 201, got {status}: {failure_context}", failures)
    require(result.get("provider") == "agentops-worker", f"{adapter}: wrong provider: {failure_context}", failures)
    require(result.get("workflow") == "customer_worker_task", f"{adapter}: wrong workflow: {failure_context}", failures)
    require(result.get("adapter") == adapter, f"{adapter}: adapter mismatch: {failure_context}", failures)
    require(result.get("dry_run") is False, f"{adapter}: live acceptance remained dry-run: {failure_context}", failures)
    require(result.get("ok") is True, f"{adapter}: live worker task did not complete: {failure_context}", failures)
    require(bool(result.get("task_id")), f"{adapter}: missing task_id", failures)
    require(bool(result.get("run_id")), f"{adapter}: missing run_id", failures)
    require(bool(result.get("artifact_id")), f"{adapter}: missing artifact_id", failures)
    require(worker_state.get("base_url") == args.base_url.rstrip("/"), f"{adapter}: worker used wrong base_url: {worker_state}", failures)
    for key in ["tool_calls", "evaluations", "runtime_events", "audit_logs", "artifacts", "memories", "approvals", "plan_evidence_manifests"]:
        require(evidence.get(key, 0) >= 1, f"{adapter}: missing {key} evidence: {evidence}", failures)
    require(
        result.get("plan_evidence_pass") is True,
        f"{adapter}: plan evidence did not pass: status={result.get('plan_evidence_status')} manifest={result.get('plan_evidence_manifest_id')}",
        failures,
    )
    serialized = json.dumps(result, ensure_ascii=False)
    require(not token_leaked(serialized), f"{adapter}: output leaked token-like material", failures)
    return {
        "adapter": adapter,
        "ok": not failures,
        "status": status,
        "task_id": result.get("task_id"),
        "run_id": result.get("run_id"),
        "artifact_id": result.get("artifact_id"),
        "approval_id": result.get("approval_id"),
        "plan_id": result.get("plan_id"),
        "plan_evidence_manifest_id": result.get("plan_evidence_manifest_id"),
        "worker_base_url": worker_state.get("base_url"),
        "evidence": evidence,
        "failures": failures,
    }


def run_adapter_async_disconnect(args: argparse.Namespace, adapter: str) -> dict:
    failures: list[str] = []
    try:
        first_opener, csrf_token, origin, first_session = authenticate_human_session(
            args,
            include_session_cookie=True,
        )
    except RuntimeError as exc:
        return {
            "adapter": adapter,
            "ok": False,
            "failures": [f"{adapter}: initial Owner authentication failed: {exc}"],
        }

    async_payload = customer_worker_payload(args, adapter)
    async_payload["idempotency_key"] = f"runtime-disconnect-{adapter}-{time.time_ns()}"
    status, submitted = http_json(
        "POST",
        args.base_url,
        "/api/workflows/customer-worker-task/submit",
        async_payload,
        args.request_timeout,
        opener=first_opener,
        headers={"Origin": origin, "X-AgentOps-CSRF": csrf_token},
    )
    job_id = str(submitted.get("job_id") or "")
    initial_job = submitted.get("job") or {}
    require(status == 202, f"{adapter}: async submit expected HTTP 202, got {status}", failures)
    require(bool(job_id), f"{adapter}: async submit did not return job_id", failures)
    require(initial_job.get("status") == "queued", f"{adapter}: async job was not initially queued", failures)
    require(initial_job.get("adapter") == adapter, f"{adapter}: queued job adapter mismatch", failures)
    require(initial_job.get("confirm_run") is True, f"{adapter}: queued job lost explicit confirmation", failures)
    request_hash = str(initial_job.get("request_hash") or "")
    replay_status, replayed = http_json(
        "POST",
        args.base_url,
        "/api/workflows/customer-worker-task/submit",
        async_payload,
        args.request_timeout,
        opener=first_opener,
        headers={"Origin": origin, "X-AgentOps-CSRF": csrf_token},
    )
    require(replay_status == 202, f"{adapter}: job reached a terminal state before the browser client was discarded", failures)
    require(replayed.get("idempotent_replay") is True, f"{adapter}: repeated submit was not identified as a replay", failures)
    require(replayed.get("job_id") == job_id, f"{adapter}: repeated submit returned a different job", failures)

    # Dropping the opener and CookieJar models closing the browser without
    # logging out. The server-side job must remain independent of that client.
    disconnected_at = dt.datetime.now(dt.timezone.utc)
    del first_opener
    anonymous_status, anonymous_payload = http_json(
        "GET",
        args.base_url,
        f"/api/workflows/jobs/{job_id}",
        None,
        args.request_timeout,
    )
    require(
        anonymous_status == 401 and anonymous_payload.get("error") == "human_auth_required",
        f"{adapter}: disconnected anonymous client retained workflow access",
        failures,
    )
    time.sleep(max(args.disconnect_delay_sec, 0.0))

    try:
        second_opener, second_csrf, _second_origin, second_session = authenticate_human_session(
            args,
            include_session_cookie=True,
        )
    except RuntimeError as exc:
        return {
            "adapter": adapter,
            "ok": False,
            "job_id": job_id,
            "failures": [*failures, f"{adapter}: reconnect Owner authentication failed: {exc}"],
        }
    session_rotated = bool(second_session and second_session != first_session)
    require(session_rotated, f"{adapter}: reconnect did not establish a distinct Owner Session", failures)

    deadline = time.time() + args.request_timeout
    job = {}
    while time.time() < deadline:
        poll_status, payload = http_json(
            "GET",
            args.base_url,
            f"/api/workflows/jobs/{job_id}",
            None,
            min(args.request_timeout, 30),
            opener=second_opener,
        )
        require(poll_status == 200, f"{adapter}: reconnect job read returned HTTP {poll_status}", failures)
        job = payload.get("job") or {}
        if job.get("status") in {"completed", "failed"}:
            break
        time.sleep(0.5)

    result = job.get("result") or {}
    evidence = result.get("evidence") or {}
    task_id = str(job.get("result_task_id") or result.get("task_id") or "")
    run_id = str(job.get("result_run_id") or result.get("run_id") or "")
    require(job.get("status") == "completed", f"{adapter}: async job did not complete after reconnect", failures)
    require(result.get("ok") is True and result.get("dry_run") is False, f"{adapter}: async live result did not pass", failures)
    require(result.get("adapter") == adapter, f"{adapter}: completed result adapter mismatch", failures)
    require(bool(task_id) and bool(run_id), f"{adapter}: completed job lacks task/run linkage", failures)
    require(job.get("result_task_id") == result.get("task_id"), f"{adapter}: job/task linkage mismatch", failures)
    require(job.get("result_run_id") == result.get("run_id"), f"{adapter}: job/run linkage mismatch", failures)
    try:
        completed_at = dt.datetime.fromisoformat(str(job.get("completed_at") or ""))
        if completed_at.tzinfo is None:
            completed_at = completed_at.replace(tzinfo=dt.timezone.utc)
    except ValueError:
        completed_at = None
    completion_after_disconnect = bool(completed_at and completed_at > disconnected_at)
    require(completion_after_disconnect, f"{adapter}: job did not complete after the browser client was discarded", failures)
    for key in ["tool_calls", "evaluations", "runtime_events", "audit_logs", "artifacts", "memories", "approvals", "plan_evidence_manifests"]:
        require(evidence.get(key, 0) >= 1, f"{adapter}: reconnect readback missing {key} evidence", failures)
    require(result.get("plan_evidence_pass") is True, f"{adapter}: reconnect plan evidence did not pass", failures)

    list_status, listed = http_json(
        "GET",
        args.base_url,
        "/api/workflows/jobs?workflow_type=customer_worker_task&limit=200",
        None,
        args.request_timeout,
        opener=second_opener,
    )
    matching_jobs = [
        item for item in (listed.get("jobs") or [])
        if request_hash and item.get("request_hash") == request_hash
    ]
    require(list_status == 200, f"{adapter}: reconnect job list returned HTTP {list_status}", failures)
    require(len(matching_jobs) == 1, f"{adapter}: disconnect caused duplicate workflow jobs", failures)
    require(not token_leaked(json.dumps(job, ensure_ascii=False)), f"{adapter}: workflow job readback leaked token-like material", failures)
    sensitive_values = [
        first_session,
        second_session,
        csrf_token,
        second_csrf,
        os.environ.get(args.password_env, ""),
        os.environ.get(args.setup_code_env, ""),
    ]
    job_serialized = json.dumps(job, ensure_ascii=False)
    require(
        not any(value and value in job_serialized for value in sensitive_values),
        f"{adapter}: workflow readback exposed Session, CSRF, or acceptance credential material",
        failures,
    )

    public_result = {
        "adapter": adapter,
        "ok": False,
        "submit_status": status,
        "replay_status": replay_status,
        "idempotent_replay_verified": replayed.get("idempotent_replay") is True,
        "job_non_terminal_at_disconnect": replay_status == 202,
        "completion_after_disconnect": completion_after_disconnect,
        "job_id": job_id,
        "job_status": job.get("status"),
        "task_id": task_id,
        "run_id": run_id,
        "artifact_id": job.get("result_artifact_id") or result.get("artifact_id"),
        "approval_id": result.get("approval_id"),
        "plan_id": result.get("plan_id"),
        "plan_evidence_manifest_id": result.get("plan_evidence_manifest_id"),
        "anonymous_after_disconnect_status": anonymous_status,
        "fresh_session_after_reconnect": session_rotated,
        "matching_job_count": len(matching_jobs),
        "disconnect_delay_sec": args.disconnect_delay_sec,
        "evidence": evidence,
        "failures": failures,
    }
    public_serialized = json.dumps(public_result, ensure_ascii=False)
    require(
        not any(value and value in public_serialized for value in sensitive_values),
        f"{adapter}: acceptance output exposed Session, CSRF, or credential material",
        failures,
    )
    public_result["ok"] = not failures
    return public_result


def main() -> int:
    parser = argparse.ArgumentParser(description="Run manual-live Hermes/OpenClaw customer worker acceptance.")
    parser.add_argument("--base-url", default=os.environ.get("AGENTOPS_BASE_URL", "http://127.0.0.1:8787"))
    parser.add_argument("--adapter", action="append", choices=["hermes", "openclaw"], default=None)
    parser.add_argument("--request-timeout", type=int, default=720)
    parser.add_argument("--hermes-timeout", type=int, default=420)
    parser.add_argument("--hermes-max-tokens", type=int, default=int(os.environ.get("HERMES_MAX_TOKENS", "512")))
    parser.add_argument("--confirm-live", action="store_true", help="Required: this calls real local Hermes/OpenClaw runtimes.")
    parser.add_argument("--human-auth", action="store_true", help="Authenticate through the Private Host human Session/CSRF boundary.")
    parser.add_argument("--async-disconnect", action="store_true", help="Submit an async live job, discard the first browser client, then verify it from a fresh Owner Session.")
    parser.add_argument("--disconnect-delay-sec", type=float, default=2.0, help="Seconds to leave the async job without a connected browser client before reconnecting.")
    parser.add_argument("--origin", default=os.environ.get("AGENTOPS_ACCEPTANCE_ORIGIN", ""))
    parser.add_argument("--username", default=os.environ.get("AGENTOPS_ACCEPTANCE_USERNAME", "owner"))
    parser.add_argument("--password-env", default="AGENTOPS_ACCEPTANCE_PASSWORD")
    parser.add_argument("--setup-code-env", default="AGENTOPS_OWNER_SETUP_CODE")
    args = parser.parse_args()
    adapters = args.adapter or ["hermes", "openclaw"]
    if not args.confirm_live:
        print(json.dumps({
            "ok": False,
            "error": "confirm_live_required",
            "message": "Pass --confirm-live to run real Hermes/OpenClaw product acceptance. Mock is not supported by this script.",
            "adapters": adapters,
            "token_omitted": True,
        }, ensure_ascii=False, indent=2, sort_keys=True))
        return 2
    if args.async_disconnect and not args.human_auth:
        print(json.dumps({
            "ok": False,
            "error": "human_auth_required_for_async_disconnect",
            "message": "Pass --human-auth with --async-disconnect so the first and reconnecting browser Sessions can be verified.",
            "token_omitted": True,
        }, ensure_ascii=False, indent=2, sort_keys=True))
        return 2
    opener = None
    csrf_token = ""
    origin = ""
    if args.human_auth:
        try:
            if not args.async_disconnect:
                opener, csrf_token, origin = authenticate_human_session(args)
        except RuntimeError as exc:
            print(json.dumps({
                "ok": False,
                "operation": "customer_worker_real_runtime_acceptance",
                "error": "human_authentication_failed",
                "message": str(exc),
                "credential_values_omitted": True,
                "token_omitted": True,
            }, ensure_ascii=False, indent=2, sort_keys=True))
            return 2
    if args.async_disconnect:
        results = [run_adapter_async_disconnect(args, adapter) for adapter in adapters]
    else:
        results = [run_adapter(args, adapter, opener=opener, csrf_token=csrf_token, origin=origin) for adapter in adapters]
    failures = [failure for result in results for failure in result.get("failures", [])]
    output = {
        "ok": not failures,
        "operation": "customer_worker_real_runtime_acceptance",
        "base_url": args.base_url.rstrip("/"),
        "adapters": adapters,
        "results": results,
        "failures": failures,
        "mock_supported": False,
        "human_session_used": bool(args.human_auth),
        "async_disconnect_verified": bool(args.async_disconnect and not failures),
        "credential_values_omitted": True,
        "token_omitted": True,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
