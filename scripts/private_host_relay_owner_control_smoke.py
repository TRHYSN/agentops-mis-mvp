#!/usr/bin/env python3
"""Verify the Owner-only Relay control HTTP boundary without external network use."""
from __future__ import annotations

import http.cookiejar
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ID = "local-demo"
STAMP = "2026-07-18T00:00:00+00:00"


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def browser_client():
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))


def request_json(opener, url: str, *, method="GET", body=None, headers=None) -> tuple[int, dict, dict]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        with opener.open(request, timeout=5) as response:
            raw = response.read().decode("utf-8")
            return response.status, dict(response.headers), json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
        except ValueError:
            payload = {"error": "non_json_error"}
        return exc.code, dict(exc.headers), payload


def write_private(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    path.chmod(0o600)


def generate_certificate(openssl: str, directory: Path, *, prefix: str, hostname: str) -> tuple[Path, Path]:
    certificate = directory / f"{prefix}-cert.pem"
    private_key = directory / f"{prefix}-key.pem"
    subprocess.run(
        [
            openssl,
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-sha256",
            "-nodes",
            "-days",
            "1",
            "-subj",
            f"/CN={hostname}",
            "-addext",
            f"subjectAltName=DNS:{hostname}",
            "-keyout",
            str(private_key),
            "-out",
            str(certificate),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=30,
    )
    certificate.chmod(0o600)
    private_key.chmod(0o600)
    return certificate, private_key


def wait_ready(opener, base_url: str, process: subprocess.Popen) -> bool:
    deadline = time.time() + 30
    while time.time() < deadline:
        if process.poll() is not None:
            return False
        try:
            status, _headers, payload = request_json(opener, base_url + "/health")
            if status == 200 and payload.get("status") == "ready":
                return True
        except (OSError, ValueError, urllib.error.URLError):
            pass
        time.sleep(0.2)
    return False


def wait_receipt_state(path: Path, expected: str, timeout: float = 2) -> dict:
    deadline = time.monotonic() + timeout
    receipt = {}
    while time.monotonic() < deadline:
        try:
            receipt = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            receipt = {}
        if receipt.get("state") == expected:
            break
        time.sleep(0.02)
    return receipt


def add_operator_account(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        owner = conn.execute("SELECT * FROM human_accounts WHERE role='owner' LIMIT 1").fetchone()
        conn.execute(
            """INSERT INTO human_accounts(account_id,workspace_id,username,display_name,role,password_hash,password_salt,
                   password_params_json,status,created_at,updated_at,last_login_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "husr_relay_operator",
                WORKSPACE_ID,
                "relay-operator",
                "Relay Operator",
                "operator",
                owner[5],
                owner[6],
                owner[7],
                "active",
                STAMP,
                STAMP,
                None,
            ),
        )


def main() -> int:
    openssl = shutil.which("openssl")
    if not openssl:
        print(json.dumps({"ok": False, "error": "openssl_unavailable"}, sort_keys=True))
        return 2

    failures: list[str] = []
    evidence: dict[str, object] = {}
    secrets = {
        "machine": "fixture-relay-machine-secret",
        "admin": "fixture-relay-admin-secret",
        "setup": "fixture-relay-setup-secret",
        "password": "fixture-relay-password-value",
        "tunnel": "b8" * 32,
    }

    with tempfile.TemporaryDirectory(prefix="agentops-relay-owner-control-") as temporary:
        root = Path(temporary)
        host_home = root / "host"
        relay_home = host_home / "relay"
        host_home.mkdir(mode=0o700)
        relay_home.mkdir(mode=0o700)
        db_path = root / "agentops_mis.db"
        port = free_port()
        base_url = f"http://127.0.0.1:{port}"
        relay_certificate, _relay_key = generate_certificate(
            openssl,
            relay_home,
            prefix="relay",
            hostname="relay.owner-control.test",
        )
        host_certificate, host_key = generate_certificate(
            openssl,
            relay_home,
            prefix="host",
            hostname="console.owner-control.test",
        )
        write_private(
            host_home / "config.json",
            {
                "allowed_origins": [base_url, "https://preserve.example"],
                "cookie_secure": False,
                "database_path": str(db_path),
                "deployment_mode": "private_host",
                "host": "127.0.0.1",
                "network_publication": "disabled",
                "port": port,
                "private_console_origin": "",
                "tailscale_https_port": 8443,
                "tailscale_device_name": "preserve-device",
            },
        )
        write_private(relay_home / "config.json", {"enabled": False, "schema_version": 1})
        write_private(
            relay_home / "prepared.json",
            {
                "enabled": True,
                "host_certificate_path": str(host_certificate),
                "host_http_port": port,
                "host_private_key_path": str(host_key),
                "host_server_hostname": "console.owner-control.test",
                "host_tls_listen_port": free_port(),
                "relay_ca_path": str(relay_certificate),
                "relay_host": "127.0.0.1",
                "relay_port": 443,
                "relay_server_hostname": "relay.owner-control.test",
                "route": "owner-control-smoke",
                "schema_version": 1,
            },
        )
        write_private(
            relay_home / "secrets.json",
            {"schema_version": 1, "tunnel_key_hex": secrets["tunnel"]},
        )

        env = os.environ.copy()
        env.update(
            {
                "AGENTOPS_DB_PATH": str(db_path),
                "AGENTOPS_HOST_HOME": str(host_home),
                "AGENTOPS_SKIP_SEED_EXPORTS": "1",
                "AGENTOPS_DEPLOYMENT_MODE": "private_host",
                "AGENTOPS_HUMAN_AUTH_REQUIRED": "true",
                "AGENTOPS_API_KEY": secrets["machine"],
                "AGENTOPS_ADMIN_KEY": secrets["admin"],
                "AGENTOPS_OWNER_SETUP_CODE": secrets["setup"],
                "AGENTOPS_ALLOWED_ORIGINS": base_url,
                "HERMES_ALLOW_REAL_RUN": "false",
            }
        )
        process = subprocess.Popen(
            [sys.executable, "server.py", "--host", "127.0.0.1", "--port", str(port)],
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        anonymous = urllib.request.build_opener()
        owner = browser_client()
        operator = browser_client()
        stdout = ""
        stderr = ""
        try:
            if not wait_ready(anonymous, base_url, process):
                failures.append("private Host did not become ready")

            status, _headers, payload = request_json(anonymous, base_url + "/api/host/relay")
            evidence["anonymous_blocked"] = status == 401 and payload.get("error") == "human_auth_required"

            status, _headers, payload = request_json(
                anonymous,
                base_url + "/api/host/relay",
                headers={"Authorization": f"Bearer {secrets['machine']}"},
            )
            evidence["machine_credential_blocked"] = status == 401 and payload.get("error") == "human_auth_required"

            status, _headers, payload = request_json(
                owner,
                base_url + "/api/human-auth/bootstrap",
                method="POST",
                body={
                    "setup_code": secrets["setup"],
                    "username": "relay-owner",
                    "display_name": "Relay Owner",
                    "password": secrets["password"],
                },
                headers={"Origin": base_url},
            )
            owner_csrf = str(payload.get("csrf_token") or "")
            if status != 201 or not owner_csrf:
                failures.append("Owner bootstrap failed")

            add_operator_account(db_path)
            status, _headers, payload = request_json(
                operator,
                base_url + "/api/human-auth/login",
                method="POST",
                body={"username": "relay-operator", "password": secrets["password"]},
                headers={"Origin": base_url},
            )
            operator_csrf = str(payload.get("csrf_token") or "")
            status, _headers, payload = request_json(operator, base_url + "/api/host/relay")
            evidence["non_owner_blocked"] = bool(
                operator_csrf and status == 403 and payload.get("error") == "human_role_forbidden"
            )

            status, _headers, payload = request_json(owner, base_url + "/api/host/relay")
            evidence["disabled_status"] = bool(
                status == 200
                and payload.get("state") == "disabled"
                and payload.get("active_enabled") is False
                and payload.get("control_available") is True
            )

            status, _headers, payload = request_json(
                owner,
                base_url + "/api/host/relay/transitions",
                method="POST",
                body={"action": "enable"},
                headers={"Origin": base_url},
            )
            evidence["csrf_required"] = status == 403 and payload.get("error") == "csrf_validation_failed"

            owner_headers = {"Origin": base_url, "X-AgentOps-CSRF": owner_csrf}
            status, _headers, prepared = request_json(
                owner,
                base_url + "/api/host/relay/transitions",
                method="POST",
                body={"action": "enable"},
                headers=owner_headers,
            )
            transition_ref = str(prepared.get("transition_ref") or "")
            evidence["prepared_without_execution"] = bool(
                status == 200
                and transition_ref
                and prepared.get("state") == "prepared"
                and prepared.get("confirmation_required") is True
                and json.loads((relay_home / "config.json").read_text(encoding="utf-8")).get("enabled") is False
            )

            confirm_url = base_url + f"/api/host/relay/transitions/{transition_ref}/confirm"
            status, _headers, payload = request_json(
                owner,
                confirm_url,
                method="POST",
                body={"action": "enable"},
                headers={"Origin": "https://wrong-origin.invalid", "X-AgentOps-CSRF": owner_csrf},
            )
            evidence["exact_origin_required"] = status == 403 and payload.get("error") == "origin_validation_failed"

            status, _headers, executed = request_json(
                owner,
                confirm_url,
                method="POST",
                body={"action": "enable"},
                headers=owner_headers,
            )
            active = json.loads((relay_home / "config.json").read_text(encoding="utf-8"))
            host_config = json.loads((host_home / "config.json").read_text(encoding="utf-8"))
            restart_receipt = wait_receipt_state(relay_home / "restart-receipt.json", "manual_restart_required")
            evidence["confirmed_execution_observed"] = {
                "http_status": status,
                "state": executed.get("state"),
                "restart_mode": executed.get("restart_mode"),
                "restart_required": executed.get("restart_required"),
                "rollback_armed": executed.get("rollback_armed"),
                "active_enabled": executed.get("active_enabled"),
                "receipt_state": restart_receipt.get("state"),
            }
            evidence["confirmed_execution"] = bool(
                status == 202
                and executed.get("state") == "manual_restart_required"
                and executed.get("restart_mode") == "manual"
                and executed.get("restart_required") is True
                and executed.get("rollback_armed") is True
                and executed.get("active_enabled") is True
                and active.get("enabled") is True
                and host_config.get("network_publication") == "agentops_relay"
                and host_config.get("tailscale_https_port") == 8443
                and host_config.get("tailscale_device_name") == "preserve-device"
                and restart_receipt.get("state") == "manual_restart_required"
            )

            status, _headers, replay = request_json(
                owner,
                confirm_url,
                method="POST",
                body={"action": "enable"},
                headers=owner_headers,
            )
            evidence["confirmation_replay_rejected"] = bool(
                status == 409 and replay.get("error") == "transition_not_found"
            )

            status, _headers, blocked_prepare = request_json(
                owner,
                base_url + "/api/host/relay/transitions",
                method="POST",
                body={"action": "disable"},
                headers=owner_headers,
            )
            blocked_ref = str(blocked_prepare.get("transition_ref") or "")
            status_confirm, _headers, blocked_confirm = request_json(
                owner,
                base_url + f"/api/host/relay/transitions/{blocked_ref}/confirm",
                method="POST",
                body={"action": "disable"},
                headers=owner_headers,
            )
            evidence["pending_restart_blocks_next_transition"] = bool(
                status == 200
                and blocked_ref
                and status_confirm == 409
                and blocked_confirm.get("error") == "rollback_pending"
                and json.loads((relay_home / "config.json").read_text(encoding="utf-8")).get("enabled") is True
            )

            with sqlite3.connect(db_path) as conn:
                rows = conn.execute(
                    "SELECT action,entity_id,metadata_json FROM audit_logs WHERE action LIKE 'host.relay.%' ORDER BY created_at"
                ).fetchall()
            rendered_audit = json.dumps(rows, sort_keys=True)
            evidence["bounded_audit"] = bool(
                any(row[0] == "host.relay.prepared" for row in rows)
                and any(row[0] == "host.relay.executed" for row in rows)
                and any(row[0] == "host.relay.confirm_failed" for row in rows)
                and secrets["tunnel"] not in rendered_audit
                and str(root) not in rendered_audit
                and "material_digest" not in rendered_audit
            )
        finally:
            process.terminate()
            try:
                stdout, stderr = process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate(timeout=5)

        for name, passed in evidence.items():
            if isinstance(passed, bool) and passed is not True:
                failures.append(f"{name} failed")
        combined = (stdout or "") + (stderr or "")
        if any(value in combined for value in secrets.values()) or str(root) in combined:
            failures.append("server output exposed private fixture material")

    print(
        json.dumps(
            {
                "ok": not failures,
                "operation": "private_host_relay_owner_control_smoke",
                "network_used": False,
                "tailscale_mutated": False,
                "temporary_database": True,
                "sensitive_values_omitted": True,
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
