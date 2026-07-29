#!/usr/bin/env python3
"""Prove private Host work continues after the human browser disconnects."""
from __future__ import annotations

import http.cookiejar
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def browser_client() -> tuple[urllib.request.OpenerDirector, http.cookiejar.CookieJar]:
    jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar)), jar


def request_json(opener, url: str, *, method="GET", body=None, headers=None, timeout=5) -> tuple[int, dict, dict]:
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        with opener.open(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return response.status, dict(response.headers), json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
        except ValueError:
            payload = {"error": "non_json_error"}
        return exc.code, dict(exc.headers), payload


def session_value(jar: http.cookiejar.CookieJar) -> str:
    return next((cookie.value for cookie in jar if cookie.name == "agentops_human_session"), "")


def wait_ready(opener, base_url: str, process: subprocess.Popen[str]) -> bool:
    deadline = time.time() + 30
    while time.time() < deadline:
        if process.poll() is not None:
            return False
        try:
            status, _headers, payload = request_json(opener, base_url + "/health")
            if status == 200 and payload.get("status") in {"ready", "ok"}:
                return True
        except (OSError, ValueError, urllib.error.URLError):
            pass
        time.sleep(0.2)
    return False


def main() -> int:
    failures: list[str] = []
    evidence: dict[str, object] = {}
    captured_output: list[str] = []
    ephemeral_values = {
        "machine_key": "fixture-" + secrets.token_urlsafe(24),
        "admin_key": "fixture-" + secrets.token_urlsafe(24),
        "setup_code": "fixture-" + secrets.token_urlsafe(18),
        "password": "Fixture-" + secrets.token_urlsafe(18),
    }

    with tempfile.TemporaryDirectory(prefix="agentops-console-disconnect-") as temporary:
        temp = Path(temporary)
        port = free_port()
        base_url = f"http://127.0.0.1:{port}"
        db_path = temp / "agentops_mis.db"
        env = os.environ.copy()
        env.update({
            "AGENTOPS_DB_PATH": str(db_path),
            "AGENTOPS_SKIP_SEED_EXPORTS": "1",
            "AGENTOPS_DEPLOYMENT_MODE": "private_host",
            "AGENTOPS_HUMAN_AUTH_REQUIRED": "true",
            "AGENTOPS_COOKIE_SECURE": "false",
            "AGENTOPS_API_KEY": ephemeral_values["machine_key"],
            "AGENTOPS_ADMIN_KEY": ephemeral_values["admin_key"],
            "AGENTOPS_OWNER_SETUP_CODE": ephemeral_values["setup_code"],
            "AGENTOPS_ALLOWED_ORIGINS": base_url,
            "AGENTOPS_WORKSPACE_ID": "local-demo",
            "HERMES_ALLOW_REAL_RUN": "false",
        })
        host = subprocess.Popen(
            [sys.executable, "server.py", "--host", "127.0.0.1", "--port", str(port)],
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        anonymous = urllib.request.build_opener()
        first_browser, first_jar = browser_client()
        try:
            if not wait_ready(anonymous, base_url, host):
                failures.append("private Host did not become ready")

            status, _headers, owner = request_json(
                first_browser,
                base_url + "/api/human-auth/bootstrap",
                method="POST",
                body={
                    "setup_code": ephemeral_values["setup_code"],
                    "username": "disconnect-owner",
                    "display_name": "Disconnect Smoke Owner",
                    "password": ephemeral_values["password"],
                },
                headers={"Origin": base_url},
            )
            csrf = str(owner.get("csrf_token") or "")
            first_session = session_value(first_jar)
            if status != 201 or not csrf or not first_session:
                failures.append("Owner bootstrap did not establish a browser session")

            worker_agent_id = "agt_console_disconnect_mock"
            status, _headers, agent = request_json(
                first_browser,
                base_url + "/api/agents",
                method="POST",
                body={
                    "agent_id": worker_agent_id,
                    "name": "Console Disconnect Mock Worker",
                    "role": "Background Worker",
                    "runtime_type": "mock",
                    "model_provider": "mock",
                    "model_name": "disconnect-smoke",
                },
                headers={"Origin": base_url, "X-AgentOps-CSRF": csrf},
            )
            if status != 201 or agent.get("agent_id") != worker_agent_id:
                failures.append("Owner could not create the isolated mock worker agent")

            status, _headers, created = request_json(
                first_browser,
                base_url + "/api/tasks",
                method="POST",
                body={
                    "title": "Private Host console disconnect task",
                    "description": "Complete through the independent mock Worker after the browser session is discarded.",
                    "acceptance_criteria": "Task and run complete while no human browser session is connected.",
                    "owner_agent_id": worker_agent_id,
                    "status": "planned",
                    "priority": "high",
                    "risk_level": "low",
                },
                headers={"Origin": base_url, "X-AgentOps-CSRF": csrf},
            )
            task_id = str(created.get("task_id") or "")
            if status != 201 or not task_id:
                failures.append("Owner could not create the worker task")

            status, _headers, logged_out = request_json(
                first_browser,
                base_url + "/api/human-auth/logout",
                method="POST",
                body={},
                headers={"Origin": base_url, "X-AgentOps-CSRF": csrf},
            )
            if status != 200 or logged_out.get("authenticated") is not False:
                failures.append("Owner session was not revoked before Worker execution")
            first_jar.clear()
            del first_browser

            disconnected_status, _headers, disconnected_read = request_json(
                anonymous, base_url + f"/api/tasks/{task_id}"
            )
            if disconnected_status != 401 or disconnected_read.get("error") != "human_auth_required":
                failures.append("discarded browser session retained workspace access")

            host_pid_before = host.pid
            worker = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "agentops_mis_cli.worker",
                    "--base-url",
                    base_url,
                    "--workspace-id",
                    "local-demo",
                    "--agent-id",
                    worker_agent_id,
                    "--adapter",
                    "mock",
                    "--task-id",
                    task_id,
                    "--once",
                    "--no-enforce-intake",
                    "--adapter-max-attempts",
                    "2",
                    "--mock-failures-before-success",
                    "1",
                    "--adapter-retry-delay-sec",
                    "0.2",
                ],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=90,
                check=False,
            )
            worker_output = (worker.stdout or "") + (worker.stderr or "")
            captured_output.append(worker_output)
            try:
                worker_payload = json.loads(worker.stdout or "{}")
            except ValueError:
                worker_payload = {}
            worker_item = ((worker_payload.get("results") or [{}])[0] or {})
            run_id = str(worker_item.get("run_id") or "")
            attempt_count = int(worker_item.get("attempt_count") or 0)
            if worker.returncode != 0 or not run_id or attempt_count != 2:
                failures.append("mock Worker did not complete the controlled retry progression")
            if host.poll() is not None or host.pid != host_pid_before:
                failures.append("Host lifecycle changed after the browser client was discarded")

            second_browser, second_jar = browser_client()
            status, _headers, login = request_json(
                second_browser,
                base_url + "/api/human-auth/login",
                method="POST",
                body={"username": "disconnect-owner", "password": ephemeral_values["password"]},
                headers={"Origin": base_url},
            )
            second_session = session_value(second_jar)
            session_rotated = bool(second_session and second_session != first_session)
            if status != 200 or not session_rotated:
                failures.append("fresh browser login did not establish a distinct session")

            status, _headers, task_detail = request_json(second_browser, base_url + f"/api/tasks/{task_id}")
            task = task_detail.get("task") or {}
            task_runs = task_detail.get("runs") or []
            if status != 200 or task.get("status") != "completed" or not any(row.get("run_id") == run_id for row in task_runs):
                failures.append("reconnected Owner could not read the completed task/run linkage")

            status, _headers, run_detail = request_json(second_browser, base_url + f"/api/runs/{run_id}")
            run = run_detail.get("run") or {}
            tool_calls = run_detail.get("tool_calls") or []
            evaluations = run_detail.get("evaluations") or []
            runtime_events = run_detail.get("runtime_events") or []
            if status != 200 or run.get("status") != "completed":
                failures.append("reconnected Owner could not read the completed run")
            if not tool_calls or not any(row.get("pass_fail") == "pass" for row in evaluations) or not runtime_events:
                failures.append("completed run lacks Worker tool/evaluation/runtime evidence")

            evidence = {
                "owner_session_established": bool(first_session),
                "browser_session_revoked": logged_out.get("authenticated") is False,
                "anonymous_after_disconnect_status": disconnected_status,
                "host_pid_stable": host.poll() is None and host.pid == host_pid_before,
                "worker_adapter": "mock",
                "worker_attempt_count": attempt_count,
                "task_id": task_id,
                "task_status": task.get("status"),
                "run_id": run_id,
                "run_status": run.get("status"),
                "fresh_session_after_reconnect": session_rotated,
                "tool_calls": len(tool_calls),
                "evaluations": len(evaluations),
                "runtime_events": len(runtime_events),
            }
        except (OSError, ValueError, RuntimeError, urllib.error.URLError) as exc:
            failures.append(f"disconnect smoke exception: {type(exc).__name__}: {str(exc)[:180]}")
        finally:
            host.terminate()
            try:
                stdout, stderr = host.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                host.kill()
                stdout, stderr = host.communicate(timeout=5)
            captured_output.extend([stdout or "", stderr or ""])

    combined = "\n".join(captured_output)
    if any(value and value in combined for value in ephemeral_values.values()):
        failures.append("Host or Worker output exposed ephemeral credential material")

    print(json.dumps({
        "ok": not failures,
        "operation": "private_host_console_disconnect_smoke",
        "temporary_database": True,
        "isolated_private_host": True,
        "real_runtime_called": False,
        "browser_connection_required_for_worker": False,
        "credential_values_omitted": True,
        "evidence": evidence,
        "failures": failures,
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
