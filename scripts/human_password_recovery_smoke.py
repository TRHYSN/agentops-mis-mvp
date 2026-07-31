#!/usr/bin/env python3
"""Verify loopback-only Owner password recovery against an isolated Host DB."""
from __future__ import annotations

import http.cookiejar
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SETUP_CODE = "fixture-recovery-setup-code"
OLD_PASSWORD = "fixture-old-passphrase"
NEW_PASSWORD = "fixture-new-passphrase"
WEAK_PASSWORD = "short-pass1"
REPLAY_PASSWORD = "another-valid-passphrase"
MACHINE_KEY = "fixture-recovery-machine-key"
ADMIN_KEY = "fixture-recovery-admin-key"
USERNAME = "owner"


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def browser() -> tuple[urllib.request.OpenerDirector, http.cookiejar.CookieJar]:
    jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar)), jar


def cookie_values(jar: http.cookiejar.CookieJar) -> list[str]:
    return [cookie.value for cookie in jar if cookie.value]


def request_json(opener, url: str, *, method="GET", body=None, headers=None) -> tuple[int, dict, dict]:
    payload = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        method=method,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        with opener.open(request, timeout=5) as response:
            return response.status, dict(response.headers), json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), json.loads(exc.read().decode("utf-8"))


def table_rows(conn: sqlite3.Connection, table: str) -> list[dict]:
    return [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()]


def main() -> int:
    failures: list[str] = []
    evidence: dict[str, object] = {}
    process: subprocess.Popen[str] | None = None
    stdout = ""
    stderr = ""

    with tempfile.TemporaryDirectory(prefix="agentops-password-recovery-") as tmp:
        db_path = Path(tmp) / "agentops_mis.db"
        port = free_port()
        base_url = f"http://127.0.0.1:{port}"
        env = os.environ.copy()
        env.update(
            {
                "AGENTOPS_DB_PATH": str(db_path),
                "AGENTOPS_SKIP_SEED_EXPORTS": "1",
                "AGENTOPS_DEPLOYMENT_MODE": "private_host",
                "AGENTOPS_HUMAN_AUTH_REQUIRED": "true",
                "AGENTOPS_COOKIE_SECURE": "false",
                "AGENTOPS_OWNER_SETUP_CODE": SETUP_CODE,
                "AGENTOPS_ALLOWED_ORIGINS": base_url,
                "AGENTOPS_API_KEY": MACHINE_KEY,
                "AGENTOPS_ADMIN_KEY": ADMIN_KEY,
                "HERMES_ALLOW_REAL_RUN": "false",
            }
        )
        anonymous = urllib.request.build_opener()
        owner_browser, owner_jar = browser()
        second_browser, second_jar = browser()
        recovery_browser, recovery_jar = browser()
        old_login_browser, old_login_jar = browser()
        new_login_browser, new_login_jar = browser()

        try:
            process = subprocess.Popen(
                [sys.executable, "server.py", "--host", "127.0.0.1", "--port", str(port)],
                cwd=ROOT,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            ready = False
            deadline = time.time() + 30
            while time.time() < deadline:
                if process.poll() is not None:
                    break
                try:
                    status, _headers, _payload = request_json(anonymous, base_url + "/health")
                    if status == 200:
                        ready = True
                        break
                except (OSError, ValueError, urllib.error.URLError):
                    time.sleep(0.2)
            if not ready:
                raise RuntimeError("temporary private Host did not become ready")

            status, _headers, payload = request_json(
                owner_browser,
                base_url + "/api/human-auth/bootstrap",
                method="POST",
                body={
                    "setup_code": SETUP_CODE,
                    "username": USERNAME,
                    "display_name": "Recovery Fixture Owner",
                    "password": OLD_PASSWORD,
                },
                headers={"Origin": base_url},
            )
            evidence["owner_bootstrap"] = {"status": status, "authenticated": payload.get("authenticated")}
            if status != 201 or payload.get("authenticated") is not True:
                failures.append("owner bootstrap failed")

            status, _headers, payload = request_json(
                second_browser,
                base_url + "/api/human-auth/login",
                method="POST",
                body={"username": USERNAME, "password": OLD_PASSWORD},
                headers={"Origin": base_url},
            )
            evidence["second_session"] = {"status": status, "authenticated": payload.get("authenticated")}
            if status != 200 or payload.get("authenticated") is not True:
                failures.append("second pre-recovery session could not be created")

            status, _headers, payload = request_json(
                recovery_browser,
                base_url + "/api/human-auth/password-recovery/start",
                method="POST",
                body={"setup_code": SETUP_CODE},
                headers={"Origin": "https://100.64.0.10"},
            )
            evidence["remote_start_blocked"] = {"status": status, "error": payload.get("error")}
            if status != 403 or payload.get("error") != "local_recovery_required" or payload.get("recovery_authority"):
                failures.append("remote Origin could start password recovery or received authority")

            status, _headers, payload = request_json(
                recovery_browser,
                base_url + "/api/human-auth/password-recovery/start",
                method="POST",
                body={},
                headers={"Origin": base_url},
            )
            evidence["missing_local_authority_blocked"] = {"status": status, "error": payload.get("error")}
            if status != 403 or payload.get("error") != "local_recovery_authority_required" or payload.get("recovery_authority"):
                failures.append("loopback Origin alone could start recovery without application handoff authority")

            status, _headers, payload = request_json(
                recovery_browser,
                base_url + "/api/human-auth/password-recovery/start",
                method="POST",
                body={"setup_code": SETUP_CODE},
                headers={"Origin": base_url},
            )
            authority = str(payload.get("recovery_authority") or "")
            evidence["local_start"] = {
                "status": status,
                "operation": payload.get("operation"),
                "authority_present_in_local_response": bool(authority),
                "local_host_only": payload.get("local_host_only"),
                "single_use": payload.get("single_use"),
            }
            if status != 201 or not authority or not payload.get("local_host_only") or not payload.get("single_use"):
                failures.append("loopback recovery start did not issue a bounded authority")

            status, _headers, payload = request_json(
                recovery_browser,
                base_url + "/api/human-auth/password-recovery/complete",
                method="POST",
                body={"recovery_authority": authority, "username": USERNAME, "password": NEW_PASSWORD},
                headers={"Origin": "https://100.64.0.10"},
            )
            evidence["remote_complete_blocked"] = {"status": status, "error": payload.get("error")}
            if status != 403 or payload.get("error") != "local_recovery_required":
                failures.append("remote Origin could complete password recovery")

            status, _headers, payload = request_json(
                recovery_browser,
                base_url + "/api/human-auth/password-recovery/complete",
                method="POST",
                body={"recovery_authority": authority, "username": USERNAME, "password": WEAK_PASSWORD},
                headers={"Origin": base_url},
            )
            evidence["weak_password_blocked"] = {"status": status, "error": payload.get("error")}
            if status != 400 or payload.get("error") != "weak_password":
                failures.append("recovery accepted a password shorter than 12 characters")

            status, _headers, payload = request_json(
                recovery_browser,
                base_url + "/api/human-auth/password-recovery/complete",
                method="POST",
                body={"recovery_authority": authority, "username": "wrong-owner", "password": NEW_PASSWORD},
                headers={"Origin": base_url},
            )
            evidence["wrong_username_blocked"] = {"status": status, "error": payload.get("error")}
            if status != 401 or payload.get("error") != "invalid_recovery_authority":
                failures.append("wrong recovery username was not rejected with the generic error")

            status, response_headers, payload = request_json(
                recovery_browser,
                base_url + "/api/human-auth/password-recovery/complete",
                method="POST",
                body={"recovery_authority": authority, "username": USERNAME, "password": NEW_PASSWORD},
                headers={"Origin": base_url},
            )
            evidence["recovery_complete"] = {
                "status": status,
                "operation": payload.get("operation"),
                "authenticated": payload.get("authenticated"),
                "previous_sessions_revoked": payload.get("previous_sessions_revoked"),
                "authority_omitted": payload.get("recovery_authority_omitted"),
                "http_only_cookie": "HttpOnly" in response_headers.get("Set-Cookie", ""),
            }
            if (
                status != 200
                or payload.get("operation") != "password_recovery_complete"
                or payload.get("authenticated") is not True
                or int(payload.get("previous_sessions_revoked") or 0) < 2
                or payload.get("recovery_authority_omitted") is not True
                or "HttpOnly" not in response_headers.get("Set-Cookie", "")
            ):
                failures.append("successful recovery did not revoke old sessions and issue a new browser session")

            status, _headers, payload = request_json(owner_browser, base_url + "/api/tasks")
            evidence["old_owner_session_revoked"] = {"status": status, "error": payload.get("error")}
            if status != 401:
                failures.append("bootstrap browser retained access after password recovery")
            status, _headers, payload = request_json(second_browser, base_url + "/api/tasks")
            if status != 401:
                failures.append("second browser retained access after password recovery")
            status, _headers, payload = request_json(recovery_browser, base_url + "/api/tasks")
            evidence["recovery_session_active"] = {"status": status}
            if status != 200:
                failures.append("new recovery browser session could not access the Workspace")

            status, _headers, payload = request_json(
                recovery_browser,
                base_url + "/api/human-auth/password-recovery/complete",
                method="POST",
                body={"recovery_authority": authority, "username": USERNAME, "password": REPLAY_PASSWORD},
                headers={"Origin": base_url},
            )
            evidence["challenge_replay_blocked"] = {"status": status, "error": payload.get("error")}
            if status != 401 or payload.get("error") != "invalid_recovery_authority":
                failures.append("used recovery challenge could be replayed")

            status, _headers, payload = request_json(
                old_login_browser,
                base_url + "/api/human-auth/login",
                method="POST",
                body={"username": USERNAME, "password": OLD_PASSWORD},
                headers={"Origin": base_url},
            )
            evidence["old_password_blocked"] = {"status": status, "error": payload.get("error")}
            if status != 401 or payload.get("error") != "invalid_credentials":
                failures.append("old password remained valid after recovery")

            status, _headers, payload = request_json(
                new_login_browser,
                base_url + "/api/human-auth/login",
                method="POST",
                body={"username": USERNAME, "password": NEW_PASSWORD},
                headers={"Origin": base_url},
            )
            evidence["new_password_login"] = {"status": status, "authenticated": payload.get("authenticated")}
            if status != 200 or payload.get("authenticated") is not True:
                failures.append("new password could not create a browser session")

            raw_session_values = (
                cookie_values(owner_jar)
                + cookie_values(second_jar)
                + cookie_values(recovery_jar)
                + cookie_values(old_login_jar)
                + cookie_values(new_login_jar)
            )
            with sqlite3.connect(db_path) as conn:
                conn.row_factory = sqlite3.Row
                challenges = table_rows(conn, "human_recovery_challenges")
                sessions = table_rows(conn, "human_sessions")
                accounts = table_rows(conn, "human_accounts")
                audit_rows = [
                    dict(row)
                    for row in conn.execute(
                        "SELECT action,entity_type,entity_id,before_hash,after_hash,metadata_json FROM audit_logs "
                        "WHERE action LIKE 'human_auth.%password_recovery%' ORDER BY created_at"
                    ).fetchall()
                ]
            persisted_text = json.dumps(
                {"accounts": accounts, "sessions": sessions, "challenges": challenges, "audit": audit_rows},
                sort_keys=True,
            )
            secret_values = [
                SETUP_CODE,
                OLD_PASSWORD,
                NEW_PASSWORD,
                WEAK_PASSWORD,
                REPLAY_PASSWORD,
                MACHINE_KEY,
                ADMIN_KEY,
                authority,
                *raw_session_values,
            ]
            if any(value and value in persisted_text for value in secret_values):
                failures.append("database or recovery audit retained raw credential, authority, or session material")
            if len(challenges) != 1 or challenges[0].get("status") != "used" or challenges[0].get("challenge_hash") == authority:
                failures.append("recovery challenge was not stored as a consumed hash-only record")
            status_counts: dict[str, int] = {}
            for row in sessions:
                key = str(row.get("status") or "unknown")
                status_counts[key] = status_counts.get(key, 0) + 1
            if status_counts.get("revoked", 0) < 2 or status_counts.get("active", 0) < 2:
                failures.append("session ledger did not retain revoked old sessions plus active replacement sessions")
            actions = {row["action"] for row in audit_rows}
            expected_actions = {
                "human_auth.password_recovery_blocked",
                "human_auth.password_recovery_started",
                "human_auth.password_recovery_failed",
                "human_auth.password_recovery_completed",
            }
            if not expected_actions.issubset(actions):
                failures.append("recovery audit is missing blocked, started, failed, or completed evidence")
            metadata = [json.loads(row["metadata_json"] or "{}") for row in audit_rows]
            if not metadata or any(item.get("credentials_omitted") is not True for item in metadata):
                failures.append("recovery audit omission metadata is incomplete")
            evidence["persistence"] = {
                "challenge_rows": len(challenges),
                "challenge_status": challenges[0].get("status") if challenges else None,
                "session_status_counts": status_counts,
                "recovery_audit_rows": len(audit_rows),
                "recovery_audit_actions": sorted(actions),
                "raw_values_omitted": True,
            }
        except Exception as exc:  # keep one machine-readable result for CI diagnostics
            failures.append(f"unexpected smoke error: {type(exc).__name__}: {exc}")
        finally:
            if process is not None:
                process.terminate()
                try:
                    stdout, stderr = process.communicate(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    stdout, stderr = process.communicate(timeout=5)

        output_text = (stdout or "") + (stderr or "")
        output_secrets = [SETUP_CODE, OLD_PASSWORD, NEW_PASSWORD, WEAK_PASSWORD, REPLAY_PASSWORD, MACHINE_KEY, ADMIN_KEY]
        if any(value in output_text for value in output_secrets):
            failures.append("temporary Host output exposed fixture credential material")

    print(
        json.dumps(
            {
                "ok": not failures,
                "operation": "human_password_recovery_smoke",
                "temporary_database": True,
                "real_user_database_used": False,
                "real_runtime_called": False,
                "loopback_recovery_only": True,
                "application_handoff_required": True,
                "raw_credentials_omitted": True,
                "evidence": evidence,
                "failures": failures,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
