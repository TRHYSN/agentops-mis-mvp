#!/usr/bin/env python3
"""Verify Private Host ledger and human Session survive a managed restart."""
from __future__ import annotations

import http.cookiejar
import json
import os
import socket
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def run_host(env: dict, *args: str, expected=(0,)) -> tuple[dict, str]:
    process = subprocess.run(
        [sys.executable, "-m", "agentops_mis_cli.cli", "host", *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if process.returncode not in expected:
        raise RuntimeError(f"host {' '.join(args)} exited {process.returncode}")
    return json.loads(process.stdout), (process.stdout or "") + (process.stderr or "")


def request_json(opener, url: str, *, method="GET", body=None, headers=None) -> tuple[int, dict]:
    request = urllib.request.Request(
        url,
        data=None if body is None else json.dumps(body).encode("utf-8"),
        method=method,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        with opener.open(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def main() -> int:
    failures: list[str] = []
    evidence: dict[str, object] = {}
    outputs: list[str] = []
    with tempfile.TemporaryDirectory(prefix="agentops-host-restart-") as temporary:
        temp = Path(temporary)
        host_home = temp / "host"
        ui = temp / "ui"
        ui.mkdir()
        (ui / "index.html").write_text("<!doctype html><title>restart smoke</title>\n", encoding="utf-8")
        port = free_port()
        base_url = f"http://127.0.0.1:{port}"
        env = {**os.environ, "AGENTOPS_HOST_HOME": str(host_home)}
        started = False
        try:
            initialized, text = run_host(env, "init", "--port", str(port), "--ui-dist", str(ui))
            outputs.append(text)
            setup_code = str(initialized.get("owner_setup_code") or "")
            secrets_path = host_home / "secrets.json"
            secret_values = list(json.loads(secrets_path.read_text(encoding="utf-8")).values())
            _started, text = run_host(env, "start", "--no-workers")
            outputs.append(text)
            started = True

            browser = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
            status, auth = request_json(
                browser,
                base_url + "/api/human-auth/bootstrap",
                method="POST",
                body={
                    "setup_code": setup_code,
                    "username": "restart-owner",
                    "display_name": "Restart Owner",
                    "password": "restart-persistence-fixture-password",
                },
                headers={"Origin": base_url},
            )
            csrf = str(auth.get("csrf_token") or "")
            status, task = request_json(
                browser,
                base_url + "/api/tasks",
                method="POST",
                body={"title": "Restart persistence task", "description": "Bounded persistence evidence."},
                headers={"Origin": base_url, "X-AgentOps-CSRF": csrf},
            )
            task_id = str(task.get("task_id") or "")
            if status not in {200, 201} or not task_id:
                failures.append("authenticated task creation failed before restart")
            status, indexed = request_json(
                browser,
                base_url + "/api/knowledge/index",
                method="POST",
                body={"rebuild": True},
                headers={"Origin": base_url, "X-AgentOps-CSRF": csrf},
            )
            indexed_count = int(indexed.get("indexed") or 0)
            if status != 200 or indexed_count < 1:
                failures.append("knowledge index was not populated before restart")

            _stopped, text = run_host(env, "stop")
            outputs.append(text)
            started = False
            _restarted, text = run_host(env, "start", "--no-workers")
            outputs.append(text)
            started = True
            status, tasks = request_json(browser, base_url + "/api/tasks")
            found = status == 200 and any(row.get("task_id") == task_id for row in tasks if isinstance(row, dict))
            query = urllib.parse.urlencode({"q": "AgentOps", "limit": 3})
            knowledge_status, knowledge = request_json(browser, base_url + f"/api/knowledge/search?{query}")
            knowledge_results = knowledge.get("results") or []
            knowledge_survived = knowledge_status == 200 and bool(knowledge_results)
            evidence = {
                "owner_bootstrap": bool(csrf),
                "task_created": bool(task_id),
                "session_survived_restart": status == 200,
                "task_survived_restart": found,
                "knowledge_documents_indexed": indexed_count,
                "knowledge_search_survived_restart": knowledge_survived,
                "knowledge_result_ids": [str(row.get("doc_id") or row.get("chunk_id") or "") for row in knowledge_results[:3]],
                "managed_restart": True,
                "real_runtime_called": False,
                "temporary_database": True,
            }
            if not evidence["session_survived_restart"] or not found or not knowledge_survived:
                failures.append("human Session, task ledger, or knowledge index did not survive managed restart")
            if any(str(value) and str(value) in "\n".join(outputs[1:]) for value in secret_values):
                failures.append("Host restart output exposed credential material")
        except (OSError, RuntimeError, ValueError) as exc:
            failures.append(f"restart persistence exception: {type(exc).__name__}: {str(exc)[:180]}")
        finally:
            if started:
                try:
                    run_host(env, "stop")
                except Exception:
                    failures.append("managed Host cleanup failed")

    print(json.dumps({
        "ok": not failures,
        "operation": "private_host_restart_persistence_smoke",
        "credential_values_omitted": True,
        "evidence": evidence,
        "failures": failures,
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
