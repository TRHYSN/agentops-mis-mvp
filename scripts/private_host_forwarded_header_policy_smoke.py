#!/usr/bin/env python3
"""Verify Private Host ignores untrusted HTTP forwarding metadata."""
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
import urllib.request
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agentops_mis_core import human_auth  # noqa: E402
import server as app_server  # noqa: E402


FORWARDING_HEADER_NAMES = (
    "Forwarded",
    "X-Forwarded-For",
    "X-Forwarded-Host",
    "X-Forwarded-Port",
    "X-Forwarded-Proto",
    "X-Real-IP",
)


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def request_json(
    opener: urllib.request.OpenerDirector,
    url: str,
    *,
    method: str = "GET",
    body: dict | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict, dict]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Accept": "application/json", "Content-Type": "application/json", **(headers or {})},
    )
    try:
        with opener.open(request, timeout=5) as response:
            return response.status, dict(response.headers), json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), json.loads(exc.read().decode("utf-8"))


def wait_ready(opener: urllib.request.OpenerDirector, base_url: str, process: subprocess.Popen) -> bool:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        try:
            status, _headers, payload = request_json(opener, base_url + "/health")
            if status == 200 and payload.get("status") == "ready":
                return True
        except (OSError, ValueError, urllib.error.URLError):
            pass
        time.sleep(0.1)
    return False


def secure_cookie(headers: dict) -> bool:
    return any(
        part.strip().lower() == "secure"
        for part in str(headers.get("Set-Cookie") or "").split(";")
    )


def marker_headers(label: str, *, spoofed_proto: str, spoofed_host: str, spoofed_port: str) -> tuple[dict[str, str], list[str]]:
    markers = {
        "forwarded": f"forwarded-policy-{label}-marker",
        "for": f"forwarded-for-policy-{label}-marker",
        "host": f"forwarded-host-policy-{label}-marker",
        "port": f"forwarded-port-policy-{label}-marker",
        "proto": f"forwarded-proto-policy-{label}-marker",
        "real_ip": f"real-ip-policy-{label}-marker",
    }
    headers = {
        "Forwarded": f'for=203.0.113.40;host="{markers["forwarded"]}.invalid";proto={spoofed_proto}',
        "X-Forwarded-For": f"203.0.113.41, {markers['for']}.invalid",
        "X-Forwarded-Host": f"{spoofed_host}, {markers['host']}.invalid",
        "X-Forwarded-Port": f"{spoofed_port}, {markers['port']}",
        "X-Forwarded-Proto": f"{spoofed_proto}, {markers['proto']}",
        "X-Real-IP": f"203.0.113.42, {markers['real_ip']}.invalid",
    }
    return headers, list(markers.values())


def sqlite_markers_absent(db_path: Path, markers: list[str]) -> tuple[bool, bool]:
    if not db_path.is_file():
        return False, False
    database_bytes = b"".join(
        path.read_bytes()
        for path in (db_path, Path(str(db_path) + "-wal"), Path(str(db_path) + "-shm"))
        if path.is_file()
    )
    persisted_absent = all(marker.encode("utf-8") not in database_bytes for marker in markers)
    try:
        with sqlite3.connect(db_path) as conn:
            audit_rows = conn.execute(
                "SELECT action,entity_type,entity_id,metadata_json FROM audit_logs ORDER BY created_at"
            ).fetchall()
    except sqlite3.Error:
        return persisted_absent, False
    audit_text = json.dumps(audit_rows, ensure_ascii=False, sort_keys=True)
    audit_absent = all(marker not in audit_text for marker in markers)
    return persisted_absent, audit_absent


def verify_canonical_origin(
    direct_host: str,
    allowed_origin: str,
    forwarded_headers: dict[str, str],
    failures: list[str],
    label: str,
) -> dict[str, object]:
    env = {"AGENTOPS_ALLOWED_ORIGINS": allowed_origin}
    cases = [{name: forwarded_headers[name]} for name in FORWARDING_HEADER_NAMES]
    cases.append(dict(forwarded_headers))
    helper_results = []
    wrapper_results = []
    ignored_results = []
    with mock.patch.dict(os.environ, env, clear=False):
        for extra_headers in cases:
            headers = {"Host": direct_host, **extra_headers}
            helper_results.append(human_auth.canonical_request_origin(headers, env) == allowed_origin)
            wrapper_results.append(app_server.request_base_url(headers) == allowed_origin)
            ignored_results.append(human_auth.forwarding_headers_ignored(headers) is True)
        allowed_netloc = allowed_origin.split("//", 1)[1]
        hostile_direct_host = {
            "Host": "direct-host-policy-fixture.invalid",
            "Forwarded": f"host={allowed_netloc};proto=https",
            "X-Forwarded-Host": allowed_netloc,
            "X-Forwarded-Proto": "https",
        }
        hostile_direct_host_rejected = (
            human_auth.canonical_request_origin(hostile_direct_host, env) is None
            and app_server.request_base_url(hostile_direct_host) is None
        )
    baseline_not_ignored = human_auth.forwarding_headers_ignored({"Host": direct_host}) is False
    result = {
        "case_count": len(cases),
        "helper_stable": all(helper_results),
        "server_wrapper_stable": all(wrapper_results),
        "forwarding_presence_detected": all(ignored_results),
        "baseline_not_marked_forwarded": baseline_not_ignored,
        "invalid_direct_host_not_rehabilitated": hostile_direct_host_rejected,
    }
    if not all(value is True for key, value in result.items() if key != "case_count"):
        failures.append(f"{label}: canonical origin was influenced by untrusted forwarding metadata")
    return result


def verify_fail_closed_configuration(failures: list[str]) -> dict[str, object]:
    private_env = {
        "AGENTOPS_DEPLOYMENT_MODE": "private_host",
        "AGENTOPS_HUMAN_AUTH_REQUIRED": "true",
        "AGENTOPS_ALLOWED_ORIGINS": "",
        "AGENTOPS_COOKIE_SECURE": "typo-must-not-disable-security",
    }
    with mock.patch.dict(os.environ, private_env, clear=False):
        origin_result = human_auth.origin_error(
            {"Host": "private-host.invalid", "Origin": "https://private-host.invalid"}
        )
        invalid_cookie_secure = human_auth.cookie_secure()
    empty_allowlist_blocked = bool(
        origin_result
        and origin_result[1] == 503
        and origin_result[0].get("error") == "origin_configuration_required"
    )
    arbitrary_forwarding_detected = human_auth.forwarding_headers_ignored(
        {"Host": "127.0.0.1", "X-Forwarded-Unrecognized": "ignored-marker.invalid"}
    )
    if not empty_allowlist_blocked:
        failures.append("private Host accepted browser auth with an empty Origin allowlist")
    if invalid_cookie_secure is not True:
        failures.append("invalid private Host cookie policy silently disabled Secure cookies")
    if not arbitrary_forwarding_detected:
        failures.append("arbitrary X-Forwarded-* metadata was not classified as ignored")
    return {
        "empty_private_host_allowlist_blocked": empty_allowlist_blocked,
        "invalid_cookie_setting_fails_secure": invalid_cookie_secure is True,
        "arbitrary_x_forwarded_header_ignored": arbitrary_forwarding_detected,
    }


def run_scenario(
    root: Path,
    *,
    label: str,
    https_origin: bool,
    failures: list[str],
) -> dict[str, object]:
    scenario_root = root / label
    scenario_root.mkdir()
    db_path = scenario_root / "agentops_mis.db"
    port = free_port()
    base_url = f"http://127.0.0.1:{port}"
    if https_origin:
        allowed_origin = "https://private-host.forwarded-policy.ts.net:8443"
        direct_host = "private-host.forwarded-policy.ts.net:8443"
        spoofed_proto = "http"
        spoofed_host = f"127.0.0.1:{port}"
        spoofed_port = "80"
    else:
        allowed_origin = base_url
        direct_host = f"127.0.0.1:{port}"
        spoofed_proto = "https"
        spoofed_host = "forwarded-policy-attacker.invalid"
        spoofed_port = "443"
    forwarded_headers, markers = marker_headers(
        label,
        spoofed_proto=spoofed_proto,
        spoofed_host=spoofed_host,
        spoofed_port=spoofed_port,
    )
    canonical = verify_canonical_origin(
        direct_host,
        allowed_origin,
        forwarded_headers,
        failures,
        label,
    )

    setup_code = f"temporary-setup-{label}-value"
    password = f"Temporary-password-{label}-value"
    env = os.environ.copy()
    env.pop("AGENTOPS_COOKIE_SECURE", None)
    env.update({
        "HOME": str(scenario_root),
        "AGENTOPS_DB_PATH": str(db_path),
        "AGENTOPS_SKIP_SEED_EXPORTS": "1",
        "AGENTOPS_DEPLOYMENT_MODE": "private_host",
        "AGENTOPS_HUMAN_AUTH_REQUIRED": "true",
        "AGENTOPS_OWNER_SETUP_CODE": setup_code,
        "AGENTOPS_ALLOWED_ORIGINS": allowed_origin,
        "AGENTOPS_API_KEY": f"temporary-machine-{label}-value",
        "AGENTOPS_ADMIN_KEY": f"temporary-admin-{label}-value",
        "HERMES_ALLOW_REAL_RUN": "false",
        "OPENCLAW_ALLOW_REAL_RUN": "false",
    })
    process = subprocess.Popen(
        [sys.executable, "server.py", "--host", "127.0.0.1", "--port", str(port)],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    accepted_count = 0
    rejected_count = 0
    cookie_count = 0
    status_cookie_stable = True
    bootstrap_ok = False
    output = ""
    try:
        if not wait_ready(opener, base_url, process):
            failures.append(f"{label}: temporary Host did not become ready")
        else:
            direct_headers = {"Host": direct_host}
            status, _response_headers, payload = request_json(
                opener,
                base_url + "/api/human-auth/status",
                headers={**direct_headers, **forwarded_headers},
            )
            expected_secure = https_origin
            status_cookie_stable = status == 200 and payload.get("cookie_secure") is expected_secure
            if not status_cookie_stable:
                failures.append(f"{label}: auth status cookie policy followed forwarding metadata")

            status, response_headers, payload = request_json(
                opener,
                base_url + "/api/human-auth/bootstrap",
                method="POST",
                body={
                    "setup_code": setup_code,
                    "username": "forward-policy-owner",
                    "display_name": "Forward Policy Owner",
                    "password": password,
                },
                headers={"Host": direct_host, "Origin": allowed_origin, **forwarded_headers},
            )
            bootstrap_ok = (
                status == 201
                and (payload.get("user") or {}).get("role") == "owner"
                and secure_cookie(response_headers) is expected_secure
            )
            if not bootstrap_ok:
                failures.append(f"{label}: valid Origin bootstrap was changed or rejected by forwarding metadata")

            for name in FORWARDING_HEADER_NAMES:
                one_forwarded_header = {name: forwarded_headers[name]}
                status, _response_headers, payload = request_json(
                    opener,
                    base_url + "/api/human-auth/status",
                    headers={"Host": direct_host, **one_forwarded_header},
                )
                if status != 200 or payload.get("cookie_secure") is not expected_secure:
                    status_cookie_stable = False
                    failures.append(f"{label}: {name} changed the auth status cookie decision")

                status, response_headers, _payload = request_json(
                    opener,
                    base_url + "/api/human-auth/login",
                    method="POST",
                    body={"username": "forward-policy-owner", "password": password},
                    headers={"Host": direct_host, "Origin": allowed_origin, **one_forwarded_header},
                )
                if status == 200:
                    accepted_count += 1
                else:
                    failures.append(f"{label}: {name} caused a valid Origin login to be rejected")
                if secure_cookie(response_headers) is expected_secure:
                    cookie_count += 1
                else:
                    failures.append(f"{label}: {name} changed the login cookie Secure decision")

                status, _response_headers, payload = request_json(
                    opener,
                    base_url + "/api/human-auth/login",
                    method="POST",
                    body={"username": "forward-policy-owner", "password": password},
                    headers={
                        "Host": direct_host,
                        "Origin": "https://origin-policy-attacker.invalid",
                        **one_forwarded_header,
                    },
                )
                if status == 403 and payload.get("error") == "origin_validation_failed":
                    rejected_count += 1
                else:
                    failures.append(f"{label}: {name} bypassed the Human Auth Origin decision")
    finally:
        process.terminate()
        try:
            stdout, stderr = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate(timeout=5)
        output = stdout + stderr

    persisted_absent, audit_absent = sqlite_markers_absent(db_path, markers)
    logs_absent = all(marker not in output for marker in markers)
    credentials_absent = setup_code not in output and password not in output
    if not persisted_absent:
        failures.append(f"{label}: raw forwarding marker was persisted in temporary SQLite state")
    if not audit_absent:
        failures.append(f"{label}: raw forwarding marker was written to audit evidence")
    if not logs_absent:
        failures.append(f"{label}: raw forwarding marker was written to Host process output")
    if not credentials_absent:
        failures.append(f"{label}: fixture Human Auth authority was written to Host process output")

    return {
        "origin_kind": "configured_https_tailnet" if https_origin else "literal_http_loopback",
        "temporary_database": True,
        "loopback_transport": True,
        "header_case_count": len(FORWARDING_HEADER_NAMES),
        "valid_origin_accept_count": accepted_count,
        "invalid_origin_reject_count": rejected_count,
        "cookie_decision_match_count": cookie_count,
        "bootstrap_with_all_headers_accepted": bootstrap_ok,
        "auth_status_cookie_decision_stable": status_cookie_stable,
        "expected_cookie_secure": https_origin,
        "canonical_origin": canonical,
        "sqlite_marker_values_absent": persisted_absent,
        "audit_marker_values_absent": audit_absent,
        "process_log_marker_values_absent": logs_absent,
        "fixture_authority_absent_from_process_log": credentials_absent,
    }


def main() -> int:
    failures: list[str] = []
    fail_closed_configuration = verify_fail_closed_configuration(failures)
    with tempfile.TemporaryDirectory(prefix="agentops-forwarded-header-policy-") as temporary:
        root = Path(temporary)
        scenarios = [
            run_scenario(root, label="tailnet_https", https_origin=True, failures=failures),
            run_scenario(root, label="loopback_http", https_origin=False, failures=failures),
        ]
    output = {
        "ok": not failures,
        "operation": "private_host_forwarded_header_policy_smoke",
        "policy": "forwarding_headers_ignored",
        "forwarding_header_names": list(FORWARDING_HEADER_NAMES),
        "scenario_count": len(scenarios),
        "temporary_database": True,
        "temporary_loopback_host": True,
        "installed_host_contacted": False,
        "real_runtime_called": False,
        "tailscale_command_called": False,
        "raw_marker_values_omitted": True,
        "fail_closed_configuration": fail_closed_configuration,
        "scenarios": scenarios,
        "failures": failures,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
