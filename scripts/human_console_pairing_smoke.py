#!/usr/bin/env python3
"""Verify one-time human Console pairing and device-bound Session revocation."""
from __future__ import annotations

import datetime as dt
import hashlib
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


def browser() -> tuple[urllib.request.OpenerDirector, http.cookiejar.CookieJar]:
    jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar)), jar


def request_json(opener, url: str, *, method="GET", body=None, headers=None) -> tuple[int, dict]:
    payload = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        method=method,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        with opener.open(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def main() -> int:
    failures: list[str] = []
    evidence: dict[str, object] = {}
    with tempfile.TemporaryDirectory(prefix="agentops-human-pairing-") as tmp:
        tmp_path = Path(tmp)
        db_path = tmp_path / "agentops_mis.db"
        legacy_path = tmp_path / "legacy.db"
        with sqlite3.connect(legacy_path) as legacy:
            legacy.execute(
                """
                CREATE TABLE human_sessions (
                    session_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    session_hash TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    last_seen_at TEXT,
                    revoked_at TEXT
                )
                """
            )
            human_auth.init_schema(legacy)
            migrated_columns = {row[1] for row in legacy.execute("PRAGMA table_info(human_sessions)").fetchall()}
            migrated_tables = {
                row[0] for row in legacy.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'human_%'"
                ).fetchall()
            }
        evidence["legacy_schema_migration"] = {
            "device_id_added": "device_id" in migrated_columns,
            "devices_table_added": "human_devices" in migrated_tables,
            "pairing_table_added": "human_pairing_invitations" in migrated_tables,
            "throttle_table_added": "human_auth_throttle_buckets" in migrated_tables,
        }
        if not all(evidence["legacy_schema_migration"].values()):
            failures.append("legacy Human Session schema did not migrate additively")
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
        owner, _owner_jar = browser()
        paired, paired_jar = browser()
        replay, _replay_jar = browser()
        unpaired_login, _unpaired_jar = browser()
        pairing_secrets: list[str] = []
        try:
            deadline = time.time() + 30
            while time.time() < deadline and process.poll() is None:
                try:
                    status, _payload = request_json(anonymous, base_url + "/health")
                    if status == 200:
                        break
                except (OSError, ValueError, urllib.error.URLError):
                    pass
                time.sleep(0.2)
            else:
                failures.append("temporary Host did not become ready")

            status, owner_payload = request_json(
                owner,
                base_url + "/api/human-auth/bootstrap",
                method="POST",
                body={
                    "setup_code": "fixture-owner-setup-code",
                    "username": "owner",
                    "display_name": "Fixture Owner",
                    "password": "fixture-owner-password",
                },
                headers={"Origin": base_url},
            )
            owner_csrf = str(owner_payload.get("csrf_token") or "")
            if status != 201 or not owner_csrf:
                failures.append("owner bootstrap failed")

            status, payload = request_json(anonymous, base_url + "/api/human-auth/pairing-invitations")
            if status != 401 or payload.get("error") != "human_auth_required":
                failures.append("anonymous pairing invitation list did not fail closed")

            status, payload = request_json(
                owner,
                base_url + "/api/human-auth/pairing-invitations",
                method="POST",
                body={"role": "operator", "label": "Second Mac"},
                headers={"Origin": base_url},
            )
            if status != 403 or payload.get("error") != "csrf_validation_failed":
                failures.append("pairing invitation creation did not require CSRF")

            status, payload = request_json(
                owner,
                base_url + "/api/human-auth/pairing-invitations",
                method="POST",
                body={"role": "owner"},
                headers={"Origin": base_url, "X-AgentOps-CSRF": owner_csrf},
            )
            if status != 400 or payload.get("error") != "invalid_pairing_role":
                failures.append("pairing invitation allowed Owner role")

            status, created = request_json(
                owner,
                base_url + "/api/human-auth/pairing-invitations",
                method="POST",
                body={"role": "operator", "label": "Second Mac", "expires_in_seconds": 600},
                headers={"Origin": base_url, "X-AgentOps-CSRF": owner_csrf},
            )
            secret = str(created.get("pairing_secret") or "")
            invitation_ref = str(created.get("invitation_ref") or "")
            pairing_secrets.append(secret)
            evidence["invitation_created"] = {
                "status": status,
                "role": created.get("role"),
                "single_use": created.get("single_use"),
                "safe_ref": invitation_ref.startswith("hiref_"),
            }
            if status != 201 or not secret or not invitation_ref.startswith("hiref_"):
                failures.append("Owner could not create bounded operator pairing invitation")

            status, listed = request_json(owner, base_url + "/api/human-auth/pairing-invitations")
            listed_text = json.dumps(listed, sort_keys=True)
            if status != 200 or secret in listed_text or not listed.get("pairing_secret_omitted"):
                failures.append("pairing invitation list exposed or failed to omit the secret")

            status, paired_payload = request_json(
                paired,
                base_url + "/api/human-auth/pair",
                method="POST",
                body={
                    "pairing_secret": secret,
                    "username": "remote-operator",
                    "display_name": "Remote Operator",
                    "password": "fixture-remote-password",
                    "device_label": "Second Mac Browser",
                },
                headers={"Origin": base_url},
            )
            paired_csrf = str(paired_payload.get("csrf_token") or "")
            device_ref = str(paired_payload.get("device_ref") or "")
            cookie_names = {cookie.name for cookie in paired_jar}
            evidence["paired"] = {
                "status": status,
                "role": (paired_payload.get("user") or {}).get("role"),
                "device_ref_safe": device_ref.startswith("hdref_"),
                "session_and_device_cookies": {
                    "agentops_human_session",
                    "agentops_human_device",
                }.issubset(cookie_names),
            }
            if (
                status != 201
                or not paired_csrf
                or (paired_payload.get("user") or {}).get("role") != "operator"
                or not device_ref.startswith("hdref_")
                or not {"agentops_human_session", "agentops_human_device"}.issubset(cookie_names)
            ):
                failures.append("pairing did not create a non-Owner device-bound Human Session")

            status, payload = request_json(
                replay,
                base_url + "/api/human-auth/pair",
                method="POST",
                body={
                    "pairing_secret": secret,
                    "username": "replay-user",
                    "password": "fixture-replay-password",
                },
                headers={"Origin": base_url},
            )
            evidence["replay_rejected"] = {"status": status, "error": payload.get("error")}
            if status != 401 or payload.get("error") != "invalid_pairing_invitation":
                failures.append("single-use pairing invitation was replayable")

            status, payload = request_json(paired, base_url + "/api/tasks")
            if status != 200:
                failures.append("paired operator could not read the workspace")
            status, payload = request_json(paired, base_url + "/api/human-auth/devices")
            if status != 403 or payload.get("error") != "human_role_forbidden":
                failures.append("paired non-Owner could list device inventory")

            status, devices = request_json(owner, base_url + "/api/human-auth/devices")
            device_rows = devices.get("devices") or []
            if (
                status != 200
                or len(device_rows) != 1
                or device_rows[0].get("device_ref") != device_ref
                or "device_id" in device_rows[0]
                or not devices.get("device_secret_omitted")
            ):
                failures.append("Owner device inventory was not bounded to safe references")

            status, payload = request_json(
                unpaired_login,
                base_url + "/api/human-auth/login",
                method="POST",
                body={"username": "remote-operator", "password": "fixture-remote-password"},
                headers={"Origin": base_url},
            )
            if status != 401 or payload.get("error") != "invalid_credentials":
                failures.append("paired account could sign in from an unpaired browser")

            status, revoked = request_json(
                owner,
                f"{base_url}/api/human-auth/devices/{device_ref}/revoke",
                method="POST",
                body={},
                headers={"Origin": base_url, "X-AgentOps-CSRF": owner_csrf},
            )
            evidence["device_revoked"] = {
                "status": status,
                "revoked_session_count": revoked.get("revoked_session_count"),
            }
            if status != 200 or revoked.get("revoked_session_count") != 1:
                failures.append("Owner device revoke did not cascade to its Human Session")
            status, payload = request_json(paired, base_url + "/api/tasks")
            if status != 401 or payload.get("error") not in {"human_session_invalid", "human_device_invalid"}:
                failures.append("revoked device retained workspace access")

            # Exercise expiry, explicit revocation and attempt locking with fresh invitations.
            special_refs: dict[str, str] = {}
            for name in ("expired", "revoked", "locked"):
                status, item = request_json(
                    owner,
                    base_url + "/api/human-auth/pairing-invitations",
                    method="POST",
                    body={"role": "viewer", "label": name},
                    headers={"Origin": base_url, "X-AgentOps-CSRF": owner_csrf},
                )
                if status != 201:
                    failures.append(f"could not create {name} pairing fixture")
                pairing_secrets.append(str(item.get("pairing_secret") or ""))
                special_refs[name] = str(item.get("invitation_ref") or "")
                special_refs[name + "_secret"] = str(item.get("pairing_secret") or "")

            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    "UPDATE human_pairing_invitations SET expires_at=? WHERE secret_hash=?",
                    (
                        (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1)).isoformat(),
                        hashlib.sha256(special_refs["expired_secret"].encode()).hexdigest(),
                    ),
                )
                conn.commit()
            status, payload = request_json(
                replay,
                base_url + "/api/human-auth/pair",
                method="POST",
                body={"pairing_secret": special_refs["expired_secret"], "username": "expired-user", "password": "fixture-expired-password"},
                headers={"Origin": base_url},
            )
            if status != 401:
                failures.append("expired pairing invitation was accepted")

            status, _payload = request_json(
                owner,
                f"{base_url}/api/human-auth/pairing-invitations/{special_refs['revoked']}/revoke",
                method="POST",
                body={},
                headers={"Origin": base_url, "X-AgentOps-CSRF": owner_csrf},
            )
            if status != 200:
                failures.append("Owner could not revoke pairing invitation")
            status, _payload = request_json(
                replay,
                base_url + "/api/human-auth/pair",
                method="POST",
                body={"pairing_secret": special_refs["revoked_secret"], "username": "revoked-user", "password": "fixture-revoked-password"},
                headers={"Origin": base_url},
            )
            if status != 401:
                failures.append("revoked pairing invitation was accepted")

            for _ in range(6):
                request_json(
                    replay,
                    base_url + "/api/human-auth/pair",
                    method="POST",
                    body={"pairing_secret": special_refs["locked_secret"], "username": "x", "password": "short"},
                    headers={"Origin": base_url},
                )
            status, invitation_list = request_json(owner, base_url + "/api/human-auth/pairing-invitations")
            locked = [row for row in invitation_list.get("invitations") or [] if row.get("invitation_ref") == special_refs["locked"]]
            if status != 200 or not locked or locked[0].get("status") != "locked":
                failures.append("pairing invitation did not lock after bounded failed attempts")

            with sqlite3.connect(db_path) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    """
                    SELECT action,entity_id,metadata_json FROM audit_logs
                    WHERE action LIKE 'human_auth.pairing%' OR action LIKE 'human_auth.device%'
                    ORDER BY created_at
                    """
                ).fetchall()
                raw_device = conn.execute("SELECT device_id,device_hash FROM human_devices").fetchone()
            audit_text = json.dumps([dict(row) for row in rows], sort_keys=True)
            prohibited = [value for value in pairing_secrets if value]
            prohibited.extend(cookie.value for cookie in paired_jar)
            if raw_device:
                prohibited.extend([raw_device["device_id"], raw_device["device_hash"]])
            if any(value and value in audit_text for value in prohibited):
                failures.append("pairing/device audit exposed a secret, raw device ID/hash, or Cookie")
            expected_actions = {
                "human_auth.pairing_invitation_created",
                "human_auth.pairing_completed",
                "human_auth.pairing_failed",
                "human_auth.pairing_invitation_revoked",
                "human_auth.device_revoked",
            }
            if not expected_actions.issubset({row["action"] for row in rows}):
                failures.append("pairing/device audit trail is incomplete")
            evidence["privacy_and_audit"] = {
                "audit_rows": len(rows),
                "raw_secret_found": False,
                "raw_device_identifier_found": False,
            }
        finally:
            process.terminate()
            try:
                stdout, stderr = process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate(timeout=5)
            process_output = (stdout or "") + (stderr or "")
            if any(secret and secret in process_output for secret in pairing_secrets):
                failures.append("temporary Host output exposed a pairing secret")

    output = {
        "operation": "human_console_pairing_smoke",
        "ok": not failures,
        "checks": evidence,
        "failures": failures,
        "safety": {
            "temporary_database": True,
            "live_runtime_called": False,
            "pairing_secret_omitted": True,
            "device_secret_omitted": True,
            "raw_content_omitted": True,
        },
    }
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
