#!/usr/bin/env python3
"""Verify setup-code authority is handed to the browser without terminal disclosure."""
from __future__ import annotations

import json
import os
import secrets
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agentops_mis_cli import host as host_cli


AUTH_GATE = ROOT / "ui" / "start-building-app" / "src" / "app" / "components" / "auth" / "AuthGate.tsx"


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def request_json(url: str, *, body: dict | None = None, origin: str | None = None) -> tuple[int, dict]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if origin:
        headers["Origin"] = origin
    request = urllib.request.Request(url, data=data, headers=headers, method="POST" if data is not None else "GET")
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8"))


def browser_source_checks(failures: list[str]) -> dict[str, bool]:
    source = AUTH_GATE.read_text(encoding="utf-8")
    checks = {
        "fragment_key_present": 'params.get("agentops-owner-setup")' in source,
        "fragment_scrubbed_immediately": "window.history.replaceState" in source,
        "same_document_handoff_consumed": 'window.addEventListener("hashchange", handleSetupHandoff)' in source,
        "same_document_listener_removed": 'window.removeEventListener("hashchange", handleSetupHandoff)' in source,
        "late_handoff_not_retained": 'gate !== "checking" && gate !== "bootstrap" && gate !== "login"' in source,
        "handoff_bounded": "{16,256}" in source,
        "handoff_charset_bounded": "[A-Za-z0-9_-]" in source,
        "setup_field_hidden_for_handoff": "{isBootstrap && !hasInstallerHandoff && (" in source,
        "handoff_stays_component_state": "useState(initialSetupHandoff.value)" in source,
        "handoff_not_persisted": "localStorage" not in source and "sessionStorage" not in source,
        "terminal_handoff_error_clears_value": 'if (gate === "bootstrap" && !["weak_password", "invalid_username"].includes(code))' in source,
    }
    failures.extend(name for name, passed in checks.items() if not passed)
    return checks


def cli_handoff_checks(setup_code: str, failures: list[str]) -> dict[str, object]:
    config = {"host": "127.0.0.1", "port": 18878}

    def project(auth_payload: dict) -> tuple[int, list[dict], list[tuple[list[str], str]]]:
        emitted: list[dict] = []
        calls: list[tuple[list[str], str]] = []

        def fake_run(argv, **kwargs):
            calls.append((list(argv), str(kwargs.get("input") or "")))
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with (
            mock.patch.object(host_cli, "require_initialized", return_value=(config, {"owner_setup_code": setup_code})),
            mock.patch.object(host_cli, "managed_host_running", return_value=True),
            mock.patch.object(host_cli, "health", return_value={"reachable": True, "status": "ready"}),
            mock.patch.object(host_cli, "local_json_request", return_value=(200, auth_payload)),
            mock.patch.object(host_cli.subprocess, "run", side_effect=fake_run),
            mock.patch.object(host_cli, "emit", side_effect=lambda payload: emitted.append(payload)),
            mock.patch.object(host_cli.sys, "platform", "darwin"),
            mock.patch.object(host_cli.Path, "is_file", return_value=True),
        ):
            code = host_cli.cmd_open_console(SimpleNamespace())
        return code, emitted, calls

    code, emitted, calls = project({"required": True, "bootstrap_required": True})
    login_code, login_emitted, login_calls = project({"required": True, "bootstrap_required": False})
    serialized_output = json.dumps([*emitted, *login_emitted], ensure_ascii=False, sort_keys=True)
    argv = calls[0][0] if calls else []
    script_input = calls[0][1] if calls else ""
    login_script_input = login_calls[0][1] if login_calls else ""
    checks = {
        "return_code": code,
        "login_return_code": login_code,
        "opened": bool(emitted and emitted[-1].get("opened")),
        "setup_code_absent_from_argv": setup_code not in " ".join(argv),
        "setup_code_absent_from_output": setup_code not in serialized_output,
        "handoff_fragment_sent_over_stdin": f"#agentops-owner-setup={setup_code}" in script_input,
        "login_recovery_handoff_sent_over_stdin": f"#agentops-owner-setup={setup_code}" in login_script_input,
        "osascript_reads_stdin": argv == ["/usr/bin/osascript", "-"],
        "handoff_reported_but_omitted": bool(
            emitted
            and emitted[-1].get("bootstrap_handoff_prepared") is True
            and emitted[-1].get("local_authority_handoff_prepared") is True
            and emitted[-1].get("bootstrap_handoff_omitted") is True
            and emitted[-1].get("local_authority_handoff_omitted") is True
            and emitted[-1].get("setup_code_omitted") is True
        ),
        "login_handoff_reported_but_omitted": bool(
            login_emitted
            and login_emitted[-1].get("bootstrap_handoff_prepared") is False
            and login_emitted[-1].get("local_authority_handoff_prepared") is True
            and login_emitted[-1].get("bootstrap_handoff_omitted") is True
            and login_emitted[-1].get("local_authority_handoff_omitted") is True
            and login_emitted[-1].get("setup_code_omitted") is True
        ),
    }
    if code != 0 or login_code != 0 or not all(value is True for key, value in checks.items() if key not in {"return_code", "login_return_code"}):
        failures.append("managed CLI browser handoff did not preserve setup-code secrecy")
    return checks


def server_authority_checks(root: Path, setup_code: str, password: str, failures: list[str]) -> dict[str, object]:
    port = free_port()
    origin = f"http://127.0.0.1:{port}"
    db_path = root / "agentops_mis.db"
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(root),
        "AGENTOPS_DB_PATH": str(db_path),
        "AGENTOPS_SKIP_SEED_EXPORTS": "1",
        "AGENTOPS_DEPLOYMENT_MODE": "private_host",
        "AGENTOPS_HUMAN_AUTH_REQUIRED": "true",
        "AGENTOPS_COOKIE_SECURE": "false",
        "AGENTOPS_OWNER_SETUP_CODE": setup_code,
        "AGENTOPS_ALLOWED_ORIGINS": origin,
        "AGENTOPS_API_KEY": "temporary-" + secrets.token_urlsafe(24),
        "AGENTOPS_ADMIN_KEY": "temporary-" + secrets.token_urlsafe(24),
        "HERMES_ALLOW_REAL_RUN": "false",
    }
    process = subprocess.Popen(
        [sys.executable, "server.py", "--host", "127.0.0.1", "--port", str(port)],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                if request_json(origin + "/health")[0] == 200:
                    break
            except (ConnectionError, OSError, ValueError):
                time.sleep(0.1)
        else:
            failures.append("temporary Host did not become ready")
            return {}

        body = {"username": "handoff-owner", "display_name": "Handoff Owner", "password": password}
        no_code_status, no_code = request_json(origin + "/api/human-auth/bootstrap", body=body, origin=origin)
        code_status, created = request_json(
            origin + "/api/human-auth/bootstrap",
            body={**body, "setup_code": setup_code},
            origin=origin,
        )
        with sqlite3.connect(db_path) as conn:
            audit_rows = conn.execute(
                "SELECT metadata_json FROM audit_logs WHERE action LIKE 'human_auth.%' ORDER BY created_at"
            ).fetchall()
            owner_count = int(conn.execute("SELECT COUNT(*) FROM human_accounts WHERE role='owner'").fetchone()[0])
        serialized_audit = json.dumps(audit_rows, ensure_ascii=False)
        evidence = {
            "no_code_status": no_code_status,
            "no_code_error": no_code.get("error"),
            "setup_code_status": code_status,
            "owner_role": (created.get("user") or {}).get("role"),
            "owner_count": owner_count,
            "audit_credentials_absent": setup_code not in serialized_audit and password not in serialized_audit,
        }
        if evidence != {
            "no_code_status": 401,
            "no_code_error": "invalid_setup_code",
            "setup_code_status": 201,
            "owner_role": "owner",
            "owner_count": 1,
            "audit_credentials_absent": True,
        }:
            failures.append("server did not preserve setup-code authority for first-Owner bootstrap")
        return evidence
    finally:
        process.terminate()
        try:
            stdout, stderr = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate(timeout=5)
        if setup_code in (stdout + stderr) or password in (stdout + stderr):
            failures.append("temporary Host output exposed pairing credentials")


def main() -> int:
    failures: list[str] = []
    setup_code = "temporary-" + secrets.token_urlsafe(24)
    password = "Temporary-" + secrets.token_urlsafe(24)
    evidence: dict[str, object] = {
        "browser": browser_source_checks(failures),
        "cli": cli_handoff_checks(setup_code, failures),
    }
    with tempfile.TemporaryDirectory(prefix="agentops-owner-handoff-") as temporary:
        evidence["server"] = server_authority_checks(Path(temporary), setup_code, password, failures)
    output = {
        "ok": not failures,
        "operation": "private_host_owner_browser_handoff_smoke",
        "temporary_database": True,
        "temporary_server": True,
        "real_runtime_called": False,
        "credential_values_omitted": True,
        "evidence": evidence,
        "failures": failures,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
