#!/usr/bin/env python3
"""Verify owner-controlled human browser session listing and revocation."""
from __future__ import annotations

import datetime as dt
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
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agentops_mis_core import human_auth  # noqa: E402


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


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


def browser() -> tuple[urllib.request.OpenerDirector, http.cookiejar.CookieJar]:
    jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar)), jar


def cookie_values(jar: http.cookiejar.CookieJar) -> list[str]:
    return [cookie.value for cookie in jar]


def main() -> int:
    failures: list[str] = []
    evidence: dict[str, object] = {}
    fixture_values = {
        "fixture-owner-setup-code",
        "fixture-password-value",
        "fixture-machine-key",
        "fixture-admin-key",
        "fixture-cross-session-token",
        "fixture-expired-token",
    }

    with tempfile.TemporaryDirectory(prefix="agentops-human-sessions-") as tmp:
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
            "AGENTOPS_OWNER_SETUP_CODE": "fixture-owner-setup-code",
            "AGENTOPS_ALLOWED_ORIGINS": base_url,
            "AGENTOPS_API_KEY": "fixture-machine-key",
            "AGENTOPS_ADMIN_KEY": "fixture-admin-key",
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
        anonymous = urllib.request.build_opener()
        owner_browser, owner_jar = browser()
        second_browser, second_jar = browser()
        third_browser, third_jar = browser()
        viewer_browser, viewer_jar = browser()
        try:
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
                failures.append("temporary private host did not become ready")

            status, _headers, payload = request_json(anonymous, base_url + "/api/human-auth/sessions")
            evidence["anonymous_list"] = {"status": status, "error": payload.get("error")}
            if status != 401 or payload.get("error") != "human_auth_required":
                failures.append("anonymous session list did not fail closed")

            status, _headers, payload = request_json(
                owner_browser,
                base_url + "/api/human-auth/bootstrap",
                method="POST",
                body={
                    "setup_code": "fixture-owner-setup-code",
                    "username": "owner",
                    "display_name": "Fixture Owner",
                    "password": "fixture-password-value",
                },
                headers={"Origin": base_url},
            )
            owner_csrf = str(payload.get("csrf_token") or "")
            if status != 201 or not owner_csrf:
                failures.append("owner bootstrap failed")

            status, _headers, payload = request_json(
                second_browser,
                base_url + "/api/human-auth/login",
                method="POST",
                body={"username": "owner", "password": "fixture-password-value"},
                headers={"Origin": base_url},
            )
            second_csrf = str(payload.get("csrf_token") or "")
            if status != 200 or not second_csrf:
                failures.append("second owner browser login failed")

            status, _headers, sessions_payload = request_json(owner_browser, base_url + "/api/human-auth/sessions")
            sessions = sessions_payload.get("sessions") or []
            current_sessions = [row for row in sessions if row.get("current")]
            other_sessions = [row for row in sessions if not row.get("current") and row.get("status") == "active"]
            evidence["initial_list"] = {
                "status": status,
                "session_count": sessions_payload.get("session_count"),
                "active_count": sessions_payload.get("active_count"),
                "current_count": len(current_sessions),
                "safe_fields_only": all("session_ref" in row and "session_id" not in row and "session_hash" not in row for row in sessions),
            }
            if status != 200 or len(sessions) != 2 or len(current_sessions) != 1 or len(other_sessions) != 1:
                failures.append("owner session list did not identify current and other active sessions")
            if not sessions_payload.get("session_id_omitted") or not sessions_payload.get("session_hash_omitted") or not sessions_payload.get("token_omitted"):
                failures.append("session list omission contract is incomplete")

            current_ref = str((current_sessions[0] if current_sessions else {}).get("session_ref") or "")
            other_ref = str((other_sessions[0] if other_sessions else {}).get("session_ref") or "")
            status, _headers, payload = request_json(
                owner_browser,
                base_url + "/api/human-auth/sessions/revoke",
                method="POST",
                body={"session_ref": other_ref, "all_other": False},
                headers={"Origin": base_url, "X-AgentOps-CSRF": owner_csrf},
            )
            evidence["ambiguous_request_rejected"] = {"status": status, "error": payload.get("error")}
            if status != 400 or payload.get("error") != "invalid_session_revoke_request":
                failures.append("ambiguous Session revoke body was not rejected")

            status, _headers, payload = request_json(
                owner_browser,
                base_url + "/api/human-auth/sessions/revoke",
                method="POST",
                body={"session_ref": current_ref},
                headers={"Origin": base_url, "X-AgentOps-CSRF": owner_csrf},
            )
            evidence["current_revoke_blocked"] = {"status": status, "error": payload.get("error")}
            if status != 409 or payload.get("error") != "current_session_requires_logout":
                failures.append("current browser session was not protected from indirect revocation")

            status, _headers, payload = request_json(
                owner_browser,
                base_url + "/api/human-auth/sessions/revoke",
                method="POST",
                body={"session_ref": other_ref},
                headers={"Origin": base_url},
            )
            if status != 403 or payload.get("error") != "csrf_validation_failed":
                failures.append("session revoke did not require CSRF")

            status, _headers, payload = request_json(
                owner_browser,
                base_url + "/api/human-auth/sessions/revoke",
                method="POST",
                body={"session_ref": other_ref},
                headers={"Origin": base_url, "X-AgentOps-CSRF": owner_csrf},
            )
            evidence["single_revoke"] = {
                "status": status,
                "revoked_count": payload.get("revoked_count"),
                "current_session_preserved": payload.get("current_session_preserved"),
            }
            if status != 200 or payload.get("revoked_count") != 1 or not payload.get("current_session_preserved"):
                failures.append("owner could not revoke the other browser session")
            status, _headers, payload = request_json(second_browser, base_url + "/api/tasks")
            if status != 401 or payload.get("error") != "human_session_invalid":
                failures.append("revoked second browser retained workspace access")

            now = dt.datetime.now(dt.timezone.utc)
            with sqlite3.connect(db_path) as conn:
                conn.row_factory = sqlite3.Row
                owner = conn.execute("SELECT * FROM human_accounts WHERE username='owner'").fetchone()
                conn.execute(
                    """
                    INSERT INTO human_accounts(
                        account_id,workspace_id,username,display_name,role,password_hash,password_salt,
                        password_params_json,status,created_at,updated_at,last_login_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "husr_viewer_fixture", "local-demo", "viewer", "Fixture Viewer", "viewer",
                        owner["password_hash"], owner["password_salt"], owner["password_params_json"],
                        "active", now.isoformat(), now.isoformat(), None,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO human_accounts(
                        account_id,workspace_id,username,display_name,role,password_hash,password_salt,
                        password_params_json,status,created_at,updated_at,last_login_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "husr_cross_fixture", "other-workspace", "cross-owner", "Cross Owner", "owner",
                        owner["password_hash"], owner["password_salt"], owner["password_params_json"],
                        "active", now.isoformat(), now.isoformat(), None,
                    ),
                )
                conn.execute(
                    "INSERT INTO human_sessions(session_id,account_id,session_hash,status,created_at,expires_at,last_seen_at,revoked_at) VALUES(?,?,?,?,?,?,?,?)",
                    (
                        "hsess_cross_account_fixture", "husr_cross_fixture",
                        human_auth.token_hash("fixture-cross-session-token"), "active", now.isoformat(),
                        (now + dt.timedelta(hours=1)).isoformat(), now.isoformat(), None,
                    ),
                )
                conn.execute(
                    "INSERT INTO human_sessions(session_id,account_id,session_hash,status,created_at,expires_at,last_seen_at,revoked_at) VALUES(?,?,?,?,?,?,?,?)",
                    (
                        "hsess_expired_fixture", owner["account_id"], human_auth.token_hash("fixture-expired-token"),
                        "active", (now - dt.timedelta(hours=2)).isoformat(), (now - dt.timedelta(hours=1)).isoformat(),
                        (now - dt.timedelta(hours=2)).isoformat(), None,
                    ),
                )
                conn.commit()

            status, _headers, payload = request_json(
                viewer_browser,
                base_url + "/api/human-auth/login",
                method="POST",
                body={"username": "viewer", "password": "fixture-password-value"},
                headers={"Origin": base_url},
            )
            if status != 200:
                failures.append("viewer fixture login failed")
            status, _headers, payload = request_json(viewer_browser, base_url + "/api/human-auth/sessions")
            evidence["viewer_forbidden"] = {"status": status, "error": payload.get("error")}
            if status != 403 or payload.get("error") != "human_role_forbidden":
                failures.append("non-owner could list browser sessions")

            cross_ref = human_auth.session_reference("hsess_cross_account_fixture")
            status, _headers, payload = request_json(
                owner_browser,
                base_url + "/api/human-auth/sessions/revoke",
                method="POST",
                body={"session_ref": cross_ref},
                headers={"Origin": base_url, "X-AgentOps-CSRF": owner_csrf},
            )
            evidence["cross_account_hidden"] = {"status": status, "error": payload.get("error")}
            if status != 404 or payload.get("error") != "human_session_not_found":
                failures.append("owner could address another account's session reference")

            status, _headers, payload = request_json(
                third_browser,
                base_url + "/api/human-auth/login",
                method="POST",
                body={"username": "owner", "password": "fixture-password-value"},
                headers={"Origin": base_url},
            )
            if status != 200:
                failures.append("third owner browser login failed")
            status, _headers, payload = request_json(
                owner_browser,
                base_url + "/api/human-auth/sessions/revoke",
                method="POST",
                body={"all_other": True},
                headers={"Origin": base_url, "X-AgentOps-CSRF": owner_csrf},
            )
            evidence["all_other_revoke"] = {
                "status": status,
                "revoked_count": payload.get("revoked_count"),
                "current_session_preserved": payload.get("current_session_preserved"),
            }
            if status != 200 or payload.get("revoked_count") != 1 or not payload.get("current_session_preserved"):
                failures.append("all-other revoke did not preserve exactly the current owner session")
            status, _headers, payload = request_json(third_browser, base_url + "/api/tasks")
            if status != 401:
                failures.append("all-other revoke left the third owner browser active")
            status, _headers, payload = request_json(owner_browser, base_url + "/api/tasks")
            if status != 200:
                failures.append("all-other revoke invalidated the current owner browser")

            status, _headers, final_payload = request_json(owner_browser, base_url + "/api/human-auth/sessions")
            expired_rows = [row for row in final_payload.get("sessions") or [] if row.get("status") == "expired"]
            evidence["final_list"] = {
                "status": status,
                "active_count": final_payload.get("active_count"),
                "expired_count": len(expired_rows),
                "session_count": final_payload.get("session_count"),
            }
            if status != 200 or final_payload.get("active_count") != 1 or not expired_rows:
                failures.append("final session list did not preserve current and expire stale sessions")

            with sqlite3.connect(db_path) as conn:
                conn.row_factory = sqlite3.Row
                raw_sessions = conn.execute("SELECT session_id,session_hash FROM human_sessions").fetchall()
                audit_rows = conn.execute(
                    """
                    SELECT action,entity_id,metadata_json FROM audit_logs
                    WHERE action LIKE 'human_auth.%'
                    ORDER BY created_at
                    """
                ).fetchall()
            response_text = json.dumps([sessions_payload, final_payload], sort_keys=True)
            raw_values = [value for row in raw_sessions for value in (row["session_id"], row["session_hash"])]
            raw_values.extend(cookie_values(owner_jar) + cookie_values(second_jar) + cookie_values(third_jar) + cookie_values(viewer_jar))
            if any(value and value in response_text for value in raw_values):
                failures.append("session API response exposed a raw session identifier, hash, or cookie")
            audit_text = json.dumps([dict(row) for row in audit_rows], sort_keys=True)
            if any(value and value in audit_text for value in raw_values):
                failures.append("human auth audit exposed a raw session identifier, hash, or cookie")
            if any(value in audit_text for value in fixture_values):
                failures.append("human auth audit exposed fixture credential material")
            revoke_actions = {row["action"] for row in audit_rows if "sessions_revoke" in row["action"]}
            expected_actions = {
                "human_auth.sessions_revoke_blocked",
                "human_auth.sessions_revoke_failed",
                "human_auth.sessions_revoked",
            }
            if not expected_actions.issubset(revoke_actions):
                failures.append("session revoke success and bounded failure audits were not recorded")
            evidence["audit"] = {
                "row_count": len(audit_rows),
                "revoke_action_count": len(revoke_actions),
                "raw_session_values_omitted": True,
                "credential_values_omitted": True,
            }
        finally:
            process.terminate()
            try:
                stdout, stderr = process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate(timeout=5)

        combined = (stdout or "") + (stderr or "")
        if any(value in combined for value in fixture_values):
            failures.append("temporary Host output exposed fixture credential material")

    ui_files = {
        "page": ROOT / "ui/start-building-app/src/app/components/pages/AccountSecurity.tsx",
        "app": ROOT / "ui/start-building-app/src/app/App.tsx",
        "topbar": ROOT / "ui/start-building-app/src/app/components/layout/Topbar.tsx",
        "settings": ROOT / "ui/start-building-app/src/app/components/shared/WorkspaceSettings.tsx",
        "api": ROOT / "ui/start-building-app/src/app/data/liveApi.ts",
    }
    ui_source = {name: path.read_text(encoding="utf-8") if path.is_file() else "" for name, path in ui_files.items()}
    ui_checks = {
        "page_exists": bool(ui_source["page"]),
        "route_exists": 'path="/workspace/account"' in ui_source["app"] and "<AccountSecurity" in ui_source["app"],
        "topbar_account_entry": 'to="/workspace/account"' in ui_source["topbar"] and "copy.account" in ui_source["topbar"],
        "session_list_bound": "loadHumanBrowserSessions" in ui_source["page"],
        "session_revoke_bound": "revokeHumanBrowserSession" in ui_source["page"],
        "revoke_requires_confirmation": "window.confirm" in ui_source["page"],
        "current_session_signout_present": "logout()" in ui_source["page"],
        "settings_layout_reused": (
            "WorkspaceSettingsPage" in ui_source["page"]
            and "WorkspaceSettingsSection" in ui_source["page"]
            and 'lg:grid-cols-[220px_minmax(0,1fr)]' in ui_source["settings"]
        ),
        "api_uses_relative_human_auth_paths": all(
            marker in ui_source["api"]
            for marker in ('"/human-auth/sessions"', '"/human-auth/sessions/revoke"')
        ),
    }
    failures.extend(f"ui_contract:{name}" for name, passed in ui_checks.items() if not passed)
    evidence["ui_contract"] = ui_checks

    print(json.dumps({
        "ok": not failures,
        "operation": "human_session_management_smoke",
        "temporary_database": True,
        "real_runtime_called": False,
        "credential_values_omitted": True,
        "raw_session_values_omitted": True,
        "evidence": evidence,
        "failures": failures,
    }, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
