#!/usr/bin/env python3
"""Verify Host worker reads use machine auth without weakening browser auth."""
from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "agentops"
MACHINE_KEY = "fixture-host-machine-key"
ADMIN_KEY = "fixture-host-admin-key"
OWNER_CODE = "fixture-host-owner-code"


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def http_json(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    body: dict | None = None,
    token: str | None = None,
    headers: dict | None = None,
) -> tuple[int, dict, str]:
    request_headers = {"Content-Type": "application/json", **(headers or {})}
    if token:
        request_headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(base_url.rstrip("/") + path, data=data, method=method, headers=request_headers)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read().decode("utf-8")
            return response.status, json.loads(raw) if raw else {}, raw
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
        except Exception:
            payload = {"error": exc.reason}
        return exc.code, payload, raw


def database_snapshot(db_path: Path, token_id: str, session_id: str) -> dict:
    with sqlite3.connect(db_path) as conn:
        counts = {}
        for table in ("agents", "tasks", "runs", "runtime_events", "tool_calls", "evaluations", "audit_logs"):
            counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        token_row = conn.execute(
            "SELECT last_used_at FROM agent_gateway_tokens WHERE token_id=?",
            (token_id,),
        ).fetchone()
        session_row = conn.execute(
            "SELECT last_used_at FROM agent_gateway_sessions WHERE session_id=?",
            (session_id,),
        ).fetchone()
    return {
        "counts": counts,
        "token_last_used_at": token_row[0] if token_row else None,
        "session_last_used_at": session_row[0] if session_row else None,
    }


def run_cli(base_url: str, command: list[str], config_path: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["AGENTOPS_CONFIG"] = str(config_path)
    env.pop("AGENTOPS_API_KEY", None)
    return subprocess.run(
        [str(CLI), "--base-url", base_url, "--api-key", MACHINE_KEY, *command],
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


def wait_ready(process: subprocess.Popen, base_url: str) -> bool:
    deadline = time.time() + 30
    while time.time() < deadline:
        if process.poll() is not None:
            return False
        try:
            status, _payload, _raw = http_json(base_url, "/health")
            if status == 200:
                return True
        except (OSError, urllib.error.URLError):
            time.sleep(0.2)
    return False


def main() -> int:
    failures: list[str] = []
    evidence: dict[str, object] = {}
    captured: list[str] = []
    with tempfile.TemporaryDirectory(prefix="agentops-host-worker-machine-read-") as tmp:
        tmp_path = Path(tmp)
        db_path = tmp_path / "agentops_mis.db"
        port = free_port()
        base_url = f"http://127.0.0.1:{port}"
        env = os.environ.copy()
        env.update({
            "AGENTOPS_DB_PATH": str(db_path),
            "AGENTOPS_SKIP_SEED_EXPORTS": "1",
            "AGENTOPS_DEPLOYMENT_MODE": "private_host",
            "AGENTOPS_HUMAN_AUTH_REQUIRED": "true",
            "AGENTOPS_COOKIE_SECURE": "false",
            "AGENTOPS_API_KEY": MACHINE_KEY,
            "AGENTOPS_ADMIN_KEY": ADMIN_KEY,
            "AGENTOPS_OWNER_SETUP_CODE": OWNER_CODE,
            "AGENTOPS_ALLOWED_ORIGINS": base_url,
            "HERMES_ALLOW_REAL_RUN": "false",
        })
        process = subprocess.Popen(
            [sys.executable, "server.py", "--host", "127.0.0.1", "--port", str(port)],
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            if not wait_ready(process, base_url):
                failures.append("private Host did not become ready")

            for label, token in (("anonymous", None), ("wrong_key", "fixture-wrong-host-key")):
                status, payload, raw = http_json(
                    base_url,
                    "/api/agent-gateway/host-workers/status",
                    token=token,
                )
                captured.append(raw)
                evidence[label] = {"status": status, "error": payload.get("error")}
                require(status == 401 and payload.get("error") == "unauthorized", f"{label} Host Worker read did not fail closed", failures)

            status, payload, raw = http_json(
                base_url,
                "/api/workers/status",
                token=MACHINE_KEY,
            )
            captured.append(raw)
            evidence["browser_route_with_machine_key"] = {"status": status, "error": payload.get("error")}
            require(status == 401 and payload.get("error") == "human_auth_required", "browser Worker route accepted a machine key", failures)

            status, enrollment, raw = http_json(
                base_url,
                "/api/agent-gateway/enrollment/create",
                method="POST",
                body={
                    "workspace_id": "local-demo",
                    "agent_id": "agt_host_worker_read_fixture",
                    "name": "Host Worker Read Fixture",
                    "runtime_type": "mock",
                    "scopes": ["tasks:read"],
                    "ttl_days": 1,
                },
                headers={"X-AgentOps-Admin-Key": ADMIN_KEY},
            )
            require(status == 201 and bool(enrollment.get("token")), f"scoped enrollment setup failed: {status}", failures)
            agent_token = str(enrollment.get("token") or "")
            token_id = str(enrollment.get("token_id") or "")

            status, session, raw = http_json(
                base_url,
                "/api/agent-gateway/session/create",
                method="POST",
                body={"ttl_sec": 120, "scopes": ["tasks:read"]},
                token=agent_token,
            )
            require(status == 201 and bool(session.get("session_token")), f"scoped session setup failed: {status}", failures)
            session_token = str(session.get("session_token") or "")
            session_id = str(session.get("session_id") or "")

            baseline = database_snapshot(db_path, token_id, session_id)
            evidence["baseline_counts"] = baseline["counts"]

            routes = {
                "status": "/api/agent-gateway/host-workers/status",
                "fleet": "/api/agent-gateway/host-workers/fleet",
                "readiness": "/api/agent-gateway/host-workers/adapter-readiness",
                "stuck": "/api/agent-gateway/host-workers/stuck-tasks?threshold_sec=30&limit=5",
                "runtime_connectors": "/api/agent-gateway/host-runtime-connectors",
            }
            route_evidence = {}
            for name, path in routes.items():
                status, payload, raw = http_json(base_url, path, token=MACHINE_KEY)
                captured.append(raw)
                route_evidence[name] = {
                    "status": status,
                    "provider": payload.get("provider"),
                    "auth_mode": (payload.get("auth") or {}).get("mode"),
                    "read_only": (payload.get("safety") or {}).get("read_only"),
                }
                require(status == 200, f"Host machine route {name} failed: {status}", failures)
                require(payload.get("provider") == "agentops-worker", f"Host machine route {name} returned wrong provider", failures)
                require((payload.get("auth") or {}).get("mode") == "global_api_key", f"Host machine route {name} omitted global auth proof", failures)
                require((payload.get("auth") or {}).get("host_machine_only") is True, f"Host machine route {name} omitted host-only proof", failures)
                require((payload.get("safety") or {}).get("read_only") is True, f"Host machine route {name} omitted read-only proof", failures)
                require((payload.get("safety") or {}).get("ledger_mutated") is False, f"Host machine route {name} claimed ledger mutation", failures)
                if name == "runtime_connectors":
                    connectors = payload.get("connectors") or []
                    require(bool(connectors), "Host runtime connector route returned no connectors", failures)
                    require(
                        all(
                            "base_url" not in connector
                            and "binary_path" not in connector
                            and "trust_note" not in connector
                            and "last_error" not in connector
                            and connector.get("raw_base_url_omitted") is True
                            and connector.get("raw_binary_path_omitted") is True
                            for connector in connectors
                        ),
                        "Host runtime connector route leaked private connector fields",
                        failures,
                    )
            evidence["machine_routes"] = route_evidence

            for label, token in (("agent_token", agent_token), ("agent_session", session_token)):
                denied_routes = {}
                for route_name, route_path in routes.items():
                    status, payload, raw = http_json(base_url, route_path, token=token)
                    captured.append(raw)
                    denied_routes[route_name] = {"status": status, "error": payload.get("error")}
                    require(
                        status == 403 and payload.get("error") == "host_machine_credential_required",
                        f"{label} read Host-wide {route_name} telemetry",
                        failures,
                    )
                evidence[label] = denied_routes

            for label, command in (
                ("status", ["worker", "status"]),
                ("fleet", ["worker", "fleet"]),
                ("readiness", ["worker", "readiness"]),
                ("stuck", ["worker", "stuck", "--threshold-sec", "30", "--limit", "5"]),
                ("runtime_connectors", ["runtime", "connectors"]),
            ):
                proc = run_cli(base_url, command, tmp_path / "cli-config.json")
                captured.extend([proc.stdout, proc.stderr])
                try:
                    cli_payload = json.loads(proc.stdout)
                except Exception:
                    cli_payload = {}
                evidence[f"cli_{label}"] = {
                    "returncode": proc.returncode,
                    "provider": cli_payload.get("provider"),
                    "auth_mode": (cli_payload.get("auth") or {}).get("mode"),
                }
                expected_provider = "agentops-runtime" if label == "runtime_connectors" else "agentops-worker"
                require(proc.returncode == 0, f"agentops {label} failed: rc={proc.returncode}", failures)
                require(cli_payload.get("provider") == expected_provider, f"agentops {label} returned wrong provider", failures)

            after = database_snapshot(db_path, token_id, session_id)
            evidence["after_counts"] = after["counts"]
            evidence["ledger_counts_unchanged"] = baseline["counts"] == after["counts"]
            evidence["bound_last_used_unchanged"] = (
                baseline["token_last_used_at"] == after["token_last_used_at"]
                and baseline["session_last_used_at"] == after["session_last_used_at"]
            )
            require(baseline == after, "Host Worker reads changed ledger counts or bound credential usage state", failures)
        finally:
            process.terminate()
            try:
                stdout, stderr = process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate(timeout=5)
            captured.extend([stdout or "", stderr or ""])

        no_key_port = free_port()
        no_key_url = f"http://127.0.0.1:{no_key_port}"
        no_key_env = dict(env)
        no_key_env.pop("AGENTOPS_API_KEY", None)
        no_key_env["AGENTOPS_DB_PATH"] = str(tmp_path / "no-key-agentops.db")
        no_key_env["AGENTOPS_ALLOWED_ORIGINS"] = no_key_url
        no_key_process = subprocess.Popen(
            [sys.executable, "server.py", "--host", "127.0.0.1", "--port", str(no_key_port)],
            cwd=ROOT,
            env=no_key_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            no_key_ready = wait_ready(no_key_process, no_key_url)
            if no_key_ready:
                status, payload, raw = http_json(no_key_url, "/api/agent-gateway/host-workers/status")
                captured.append(raw)
                evidence["private_host_without_machine_key"] = {
                    "server_started": True,
                    "status": status,
                    "error": payload.get("error"),
                }
                require(
                    status == 503 and payload.get("error") == "host_machine_credential_not_configured",
                    "Private Host without AGENTOPS_API_KEY did not fail closed",
                    failures,
                )
            else:
                evidence["private_host_without_machine_key"] = {
                    "server_started": False,
                    "startup_failed_closed": no_key_process.poll() is not None,
                }
                require(no_key_process.poll() is not None, "credential-missing Private Host neither failed startup nor exposed a guarded route", failures)
        finally:
            no_key_process.terminate()
            try:
                stdout, stderr = no_key_process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                no_key_process.kill()
                stdout, stderr = no_key_process.communicate(timeout=5)
            captured.extend([stdout or "", stderr or ""])

        combined = "\n".join(captured)
        for secret in (MACHINE_KEY, ADMIN_KEY, OWNER_CODE, locals().get("agent_token", ""), locals().get("session_token", "")):
            if secret and secret in combined:
                failures.append("smoke output exposed credential material")
                break

    print(json.dumps({
        "ok": not failures,
        "operation": "private_host_worker_machine_read_smoke",
        "human_and_machine_credentials_separate": True,
        "host_worker_reads_are_machine_only": True,
        "real_runtime_called": False,
        "temporary_database": True,
        "credential_values_omitted": True,
        "evidence": evidence,
        "failures": failures,
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
