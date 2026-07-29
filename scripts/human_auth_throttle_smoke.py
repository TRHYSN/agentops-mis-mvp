#!/usr/bin/env python3
"""Verify persistent, source-independent Human Login/Pairing throttling."""
from __future__ import annotations

import datetime as dt
import http.cookiejar
import json
import os
import re
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
WINDOW_SECONDS = 5 * 60
BLOCK_SECONDS = 5 * 60
LOGIN_SUBJECT_LIMIT = 8
LOGIN_GLOBAL_LIMIT = 100
PAIRING_SUBJECT_LIMIT = 8
PAIRING_GLOBAL_LIMIT = 60
STALE_BUCKET_KEY = "hatb_" + ("f" * 64)
THROTTLE_COLUMNS = {
    "bucket_key",
    "scope",
    "window_started_at",
    "attempt_count",
    "blocked_until",
    "updated_at",
}
THROTTLE_SCOPES = {
    "login_subject",
    "login_global",
    "pairing_subject",
    "pairing_global",
}
SOURCE_HEADERS = (
    {
        "User-Agent": "ThrottleSmokeSourceA/1.0",
        "X-Forwarded-For": "198.51.100.11",
        "Forwarded": "for=198.51.100.11",
    },
    {
        "User-Agent": "ThrottleSmokeSourceB/1.0",
        "X-Forwarded-For": "203.0.113.22",
        "Forwarded": "for=203.0.113.22",
    },
    {
        "User-Agent": "ThrottleSmokeSourceC/1.0",
        "X-Forwarded-For": "192.0.2.33",
        "Forwarded": "for=192.0.2.33",
    },
)


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def browser() -> urllib.request.OpenerDirector:
    jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def request_json(
    opener: urllib.request.OpenerDirector,
    url: str,
    base_url: str,
    body: dict,
    *,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict, dict]:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Origin": base_url,
            **(headers or {}),
        },
    )
    try:
        with opener.open(request, timeout=5) as response:
            return response.status, dict(response.headers), json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), json.loads(exc.read().decode("utf-8"))


def start_host(env: dict[str, str], port: int) -> subprocess.Popen:
    process = subprocess.Popen(
        [sys.executable, "server.py", "--host", "127.0.0.1", "--port", str(port)],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.time() + 30
    base_url = f"http://127.0.0.1:{port}"
    while time.time() < deadline and process.poll() is None:
        try:
            with urllib.request.urlopen(base_url + "/health", timeout=2) as response:
                if response.status == 200:
                    return process
        except (OSError, urllib.error.URLError):
            time.sleep(0.2)
    output = stop_host(process)
    raise RuntimeError(f"temporary Host did not become ready (output_bytes={len(output)})")


def stop_host(process: subprocess.Popen | None) -> str:
    if process is None:
        return ""
    if process.poll() is None:
        process.terminate()
    try:
        stdout, stderr = process.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate(timeout=5)
    return (stdout or "") + (stderr or "")


def throttle_rows(db_path: Path) -> list[dict]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return [
            dict(row)
            for row in conn.execute(
                "SELECT bucket_key,scope,window_started_at,attempt_count,blocked_until,updated_at "
                "FROM human_auth_throttle_buckets ORDER BY scope,bucket_key"
            ).fetchall()
        ]


def scope_rows(db_path: Path, scope: str) -> list[dict]:
    return [row for row in throttle_rows(db_path) if row["scope"] == scope]


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def bounded_throttle(
    result: tuple[int, dict, dict],
    label: str,
    failures: list[str],
) -> dict[str, object]:
    status, headers, payload = result
    try:
        retry_header = int(headers.get("Retry-After") or 0)
    except (TypeError, ValueError):
        retry_header = 0
    try:
        retry_body = int(payload.get("retry_after_seconds") or 0)
    except (TypeError, ValueError):
        retry_body = 0
    bounded_keys = {
        "error",
        "message",
        "retry_after_seconds",
        "source_identity_omitted",
        "token_omitted",
    }
    require(status == 429, f"{label} did not return 429", failures)
    require(payload.get("error") == "too_many_attempts", f"{label} returned the wrong error", failures)
    require(
        isinstance(payload.get("retry_after_seconds"), int)
        and not isinstance(payload.get("retry_after_seconds"), bool),
        f"{label} JSON retry interval was not an integer",
        failures,
    )
    require(retry_header == retry_body, f"{label} Retry-After disagreed with the JSON body", failures)
    require(1 <= retry_body <= BLOCK_SECONDS + 1, f"{label} retry interval was not bounded", failures)
    require(set(payload) <= bounded_keys, f"{label} returned an unbounded response shape", failures)
    require(payload.get("source_identity_omitted") is True, f"{label} omitted no source-identity flag", failures)
    require(payload.get("token_omitted") is True, f"{label} omitted no token flag", failures)
    require(len(json.dumps(payload, sort_keys=True)) <= 512, f"{label} response exceeded the bounded envelope", failures)
    return {
        "status": status,
        "error": payload.get("error"),
        "retry_after_body": retry_body,
        "retry_after_header_matches": retry_header == retry_body,
        "bounded_response": set(payload) <= bounded_keys,
        "source_identity_omitted": payload.get("source_identity_omitted"),
    }


def main() -> int:
    failures: list[str] = []
    evidence: dict[str, object] = {}
    process_outputs: list[str] = []
    fixture_values = {
        "fixture-owner-password",
        "wrong-fixture-password",
        "fixture-owner",
        "fixture-member",
        "fixture-member-password",
        "fixture-viewer-password",
        "fixture-pairing-subject",
        "prune-trigger-user",
        "global-login-subject-",
        "fresh-after-login-global",
        "pairing-global-user-",
        "valid-global-viewer",
        "same-unknown-pairing-secret",
        "unknown-pairing-secret-",
        "ThrottleSmokeSourceA/1.0",
        "ThrottleSmokeSourceB/1.0",
        "ThrottleSmokeSourceC/1.0",
        "198.51.100.11",
        "203.0.113.22",
        "192.0.2.33",
    }
    with tempfile.TemporaryDirectory(prefix="agentops-human-throttle-") as tmp:
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
                "AGENTOPS_OWNER_SETUP_CODE": "fixture-owner-setup",
                "AGENTOPS_ALLOWED_ORIGINS": base_url,
                "AGENTOPS_API_KEY": "fixture-machine-key",
                "AGENTOPS_ADMIN_KEY": "fixture-admin-key",
                "HERMES_ALLOW_REAL_RUN": "false",
            }
        )
        owner = browser()
        anonymous = urllib.request.build_opener()
        process: subprocess.Popen | None = None
        owner_csrf = ""
        try:
            process = start_host(env, port)
            status, _headers, payload = request_json(
                owner,
                base_url + "/api/human-auth/bootstrap",
                base_url,
                {
                    "setup_code": "fixture-owner-setup",
                    "username": "fixture-owner",
                    "display_name": "Fixture Owner",
                    "password": "fixture-owner-password",
                },
            )
            owner_csrf = str(payload.get("csrf_token") or "")
            require(
                status == 201 and payload.get("user", {}).get("role") == "owner" and bool(owner_csrf),
                "owner bootstrap failed",
                failures,
            )

            # Seven failures establish history without reaching the per-subject limit.
            for index in range(LOGIN_SUBJECT_LIMIT - 1):
                status, _headers, payload = request_json(
                    owner,
                    base_url + "/api/human-auth/login",
                    base_url,
                    {"username": "fixture-owner", "password": "wrong-fixture-password"},
                    headers=SOURCE_HEADERS[index % len(SOURCE_HEADERS)],
                )
                require(
                    status == 401 and payload.get("error") == "invalid_credentials",
                    "login subject was throttled before its configured threshold",
                    failures,
                )
            status, _headers, payload = request_json(
                owner,
                base_url + "/api/human-auth/login",
                base_url,
                {"username": "fixture-owner", "password": "fixture-owner-password"},
                headers=SOURCE_HEADERS[1],
            )
            login_subject_after_success = scope_rows(db_path, "login_subject")
            login_global_after_success = scope_rows(db_path, "login_global")
            login_global_count = int(login_global_after_success[0]["attempt_count"]) if len(login_global_after_success) == 1 else -1
            evidence["login_success_reset"] = {
                "status": status,
                "authenticated": payload.get("authenticated"),
                "subject_bucket_rows": len(login_subject_after_success),
                "global_attempt_count_preserved": login_global_count,
            }
            require(status == 200 and payload.get("authenticated") is True, "fixture owner login failed", failures)
            owner_csrf = str(payload.get("csrf_token") or "")
            require(bool(owner_csrf), "successful owner login omitted its CSRF token", failures)
            require(not login_subject_after_success, "successful login did not clear its subject bucket", failures)
            require(
                login_global_count == LOGIN_SUBJECT_LIMIT - 1,
                "successful login erased or changed endpoint-global failure history",
                failures,
            )

            final_login = (0, {}, {})
            for index in range(LOGIN_SUBJECT_LIMIT):
                final_login = request_json(
                    owner,
                    base_url + "/api/human-auth/login",
                    base_url,
                    {"username": "fixture-owner", "password": "wrong-fixture-password"},
                    headers=SOURCE_HEADERS[index % len(SOURCE_HEADERS)],
                )
                if index < LOGIN_SUBJECT_LIMIT - 1:
                    require(final_login[0] == 401, "login subject blocked below its limit", failures)
            evidence["login_subject_block"] = bounded_throttle(final_login, "login subject threshold", failures)
            evidence["login_correct_password_blocked"] = bounded_throttle(
                request_json(
                    owner,
                    base_url + "/api/human-auth/login",
                    base_url,
                    {"username": "fixture-owner", "password": "fixture-owner-password"},
                    headers=SOURCE_HEADERS[2],
                ),
                "correct password during active login block",
                failures,
            )

            # Restart the isolated Host against the same fixture DB and prove the block survives.
            process_outputs.append(stop_host(process))
            process = None
            old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=2)).isoformat()
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    "INSERT INTO human_auth_throttle_buckets("
                    "bucket_key,scope,window_started_at,attempt_count,blocked_until,updated_at"
                    ") VALUES(?,?,?,?,?,?)",
                    (STALE_BUCKET_KEY, "login_subject", old, 1, None, old),
                )
                conn.commit()
            process = start_host(env, port)
            evidence["login_block_after_restart"] = bounded_throttle(
                request_json(
                    anonymous,
                    base_url + "/api/human-auth/login",
                    base_url,
                    {"username": "fixture-owner", "password": "fixture-owner-password"},
                    headers=SOURCE_HEADERS[0],
                ),
                "persisted login block after restart",
                failures,
            )

            status, _headers, payload = request_json(
                anonymous,
                base_url + "/api/human-auth/login",
                base_url,
                {"username": "prune-trigger-user", "password": "wrong-fixture-password"},
                headers=SOURCE_HEADERS[1],
            )
            require(status == 401 and payload.get("error") == "invalid_credentials", "stale-prune trigger failed", failures)
            stale_pruned = not any(row["bucket_key"] == STALE_BUCKET_KEY for row in throttle_rows(db_path))
            evidence["stale_bucket_pruned"] = stale_pruned
            require(stale_pruned, "bucket older than one day was not pruned on a later failure", failures)

            login_global_rows = scope_rows(db_path, "login_global")
            login_global_count = int(login_global_rows[0]["attempt_count"]) if len(login_global_rows) == 1 else -1
            require(0 < login_global_count < LOGIN_GLOBAL_LIMIT, "login global fixture count was invalid", failures)
            final_login_global = (0, {}, {})
            for index in range(max(0, LOGIN_GLOBAL_LIMIT - login_global_count)):
                final_login_global = request_json(
                    anonymous,
                    base_url + "/api/human-auth/login",
                    base_url,
                    {
                        "username": f"global-login-subject-{index}",
                        "password": "wrong-fixture-password",
                    },
                    headers=SOURCE_HEADERS[index % len(SOURCE_HEADERS)],
                )
                if index < LOGIN_GLOBAL_LIMIT - login_global_count - 1:
                    require(final_login_global[0] == 401, "login global bucket blocked below its limit", failures)
            evidence["login_global_block"] = bounded_throttle(
                final_login_global,
                "login endpoint-global threshold",
                failures,
            )
            evidence["login_global_fresh_subject_blocked"] = bounded_throttle(
                request_json(
                    anonymous,
                    base_url + "/api/human-auth/login",
                    base_url,
                    {"username": "fresh-after-login-global", "password": "wrong-fixture-password"},
                    headers=SOURCE_HEADERS[2],
                ),
                "fresh login subject during endpoint-global block",
                failures,
            )

            # One failed profile followed by a valid redemption proves success clears
            # only the pairing subject while preserving the endpoint-global history.
            status, _headers, invitation = request_json(
                owner,
                base_url + "/api/human-auth/pairing-invitations",
                base_url,
                {"role": "operator", "label": "Throttle reset fixture", "expires_in_seconds": 600},
                headers={"X-AgentOps-CSRF": owner_csrf},
            )
            pairing_secret = str(invitation.get("pairing_secret") or "")
            require(status == 201 and bool(pairing_secret), "pairing reset invitation creation failed", failures)
            if pairing_secret:
                fixture_values.add(pairing_secret)
                status, _headers, payload = request_json(
                    anonymous,
                    base_url + "/api/human-auth/pair",
                    base_url,
                    {"pairing_secret": pairing_secret, "username": "x", "password": "fixture-member-password"},
                    headers=SOURCE_HEADERS[0],
                )
                require(status == 400 and payload.get("error") == "invalid_pairing_profile", "pairing failure fixture failed", failures)
                status, _headers, payload = request_json(
                    anonymous,
                    base_url + "/api/human-auth/pair",
                    base_url,
                    {
                        "pairing_secret": pairing_secret,
                        "username": "fixture-member",
                        "display_name": "Fixture Member",
                        "password": "fixture-member-password",
                    },
                    headers=SOURCE_HEADERS[1],
                )
                pairing_subject_after_success = scope_rows(db_path, "pairing_subject")
                pairing_global_after_success = scope_rows(db_path, "pairing_global")
                pairing_global_count = int(pairing_global_after_success[0]["attempt_count"]) if len(pairing_global_after_success) == 1 else -1
                evidence["pairing_success_reset"] = {
                    "status": status,
                    "role": payload.get("user", {}).get("role"),
                    "subject_bucket_rows": len(pairing_subject_after_success),
                    "global_attempt_count_preserved": pairing_global_count,
                }
                require(status == 201 and payload.get("user", {}).get("role") == "operator", "valid pairing failed", failures)
                require(not pairing_subject_after_success, "successful pairing did not clear its subject bucket", failures)
                require(pairing_global_count == 1, "successful pairing erased or changed global failure history", failures)

            status, _headers, valid_global_invitation = request_json(
                owner,
                base_url + "/api/human-auth/pairing-invitations",
                base_url,
                {"role": "viewer", "label": "Global block fixture", "expires_in_seconds": 600},
                headers={"X-AgentOps-CSRF": owner_csrf},
            )
            valid_global_secret = str(valid_global_invitation.get("pairing_secret") or "")
            require(status == 201 and bool(valid_global_secret), "pairing global invitation creation failed", failures)
            if valid_global_secret:
                fixture_values.add(valid_global_secret)

            same_pairing = "same-unknown-pairing-secret"
            final_pairing_subject = (0, {}, {})
            for index in range(PAIRING_SUBJECT_LIMIT):
                final_pairing_subject = request_json(
                    anonymous,
                    base_url + "/api/human-auth/pair",
                    base_url,
                    {
                        "pairing_secret": same_pairing,
                        "username": "fixture-pairing-subject",
                        "password": "fixture-member-password",
                    },
                    headers=SOURCE_HEADERS[index % len(SOURCE_HEADERS)],
                )
                if index < PAIRING_SUBJECT_LIMIT - 1:
                    require(final_pairing_subject[0] == 401, "pairing subject blocked below its limit", failures)
            evidence["pairing_subject_block"] = bounded_throttle(
                final_pairing_subject,
                "pairing subject threshold",
                failures,
            )

            pairing_global_rows = scope_rows(db_path, "pairing_global")
            pairing_global_count = int(pairing_global_rows[0]["attempt_count"]) if len(pairing_global_rows) == 1 else -1
            require(0 < pairing_global_count < PAIRING_GLOBAL_LIMIT, "pairing global fixture count was invalid", failures)
            final_pairing_global = (0, {}, {})
            for index in range(max(0, PAIRING_GLOBAL_LIMIT - pairing_global_count)):
                final_pairing_global = request_json(
                    anonymous,
                    base_url + "/api/human-auth/pair",
                    base_url,
                    {
                        "pairing_secret": f"unknown-pairing-secret-{index}",
                        "username": f"pairing-global-user-{index}",
                        "password": "fixture-member-password",
                    },
                    headers=SOURCE_HEADERS[index % len(SOURCE_HEADERS)],
                )
                if index < PAIRING_GLOBAL_LIMIT - pairing_global_count - 1:
                    require(final_pairing_global[0] == 401, "pairing global bucket blocked below its limit", failures)
            evidence["pairing_global_block"] = bounded_throttle(
                final_pairing_global,
                "pairing endpoint-global threshold",
                failures,
            )
            if valid_global_secret:
                evidence["valid_pairing_during_global_block"] = bounded_throttle(
                    request_json(
                        anonymous,
                        base_url + "/api/human-auth/pair",
                        base_url,
                        {
                            "pairing_secret": valid_global_secret,
                            "username": "valid-global-viewer",
                            "password": "fixture-viewer-password",
                        },
                        headers=SOURCE_HEADERS[2],
                    ),
                    "valid pairing during endpoint-global block",
                    failures,
                )
        except (OSError, RuntimeError, sqlite3.Error, TypeError, ValueError, urllib.error.URLError) as exc:
            failures.append(f"isolated throttle smoke exception: {type(exc).__name__}: {str(exc)[:180]}")
        finally:
            process_outputs.append(stop_host(process))

        try:
            with sqlite3.connect(db_path) as conn:
                conn.row_factory = sqlite3.Row
                columns = {row[1] for row in conn.execute("PRAGMA table_info(human_auth_throttle_buckets)").fetchall()}
                bucket_rows = [
                    dict(row)
                    for row in conn.execute(
                        "SELECT bucket_key,scope,window_started_at,attempt_count,blocked_until,updated_at "
                        "FROM human_auth_throttle_buckets ORDER BY scope,bucket_key"
                    ).fetchall()
                ]
                throttle_audits = [
                    dict(row)
                    for row in conn.execute(
                        "SELECT action,metadata_json FROM audit_logs "
                        "WHERE action IN ('human_auth.login_throttled','human_auth.pairing_throttled')"
                    ).fetchall()
                ]
        except sqlite3.Error as exc:
            columns = set()
            bucket_rows = []
            throttle_audits = []
            failures.append(f"final throttle persistence inspection failed: {type(exc).__name__}")

        serialized = json.dumps({"buckets": bucket_rows, "audits": throttle_audits}, ensure_ascii=False, sort_keys=True)
        process_output = "".join(process_outputs)
        keys_hashed = bool(bucket_rows) and all(
            re.fullmatch(r"hatb_[0-9a-f]{64}", str(row.get("bucket_key") or "")) for row in bucket_rows
        )
        scopes_bounded = bool(bucket_rows) and {str(row.get("scope") or "") for row in bucket_rows} <= THROTTLE_SCOPES
        raw_values_absent = not any(value and (value in serialized or value in process_output) for value in fixture_values)
        actions = {str(row.get("action") or "") for row in throttle_audits}
        evidence["persistence"] = {
            "bucket_rows": len(bucket_rows),
            "throttle_audit_rows": len(throttle_audits),
            "schema_is_bounded": columns == THROTTLE_COLUMNS,
            "bucket_keys_hashed": keys_hashed,
            "scopes_bounded": scopes_bounded,
            "raw_fixture_and_source_values_absent": raw_values_absent,
            "stale_fixture_absent": not any(row.get("bucket_key") == STALE_BUCKET_KEY for row in bucket_rows),
        }
        require(columns == THROTTLE_COLUMNS, "throttle schema was not limited to hashed bucket state", failures)
        require(keys_hashed, "throttle persistence contains a non-hashed bucket key", failures)
        require(scopes_bounded, "throttle persistence contains an unexpected scope", failures)
        require(raw_values_absent, "throttle persistence, audit, or Host output exposed raw auth/source input", failures)
        require(
            {"human_auth.login_throttled", "human_auth.pairing_throttled"} <= actions,
            "bounded throttle audit actions are incomplete",
            failures,
        )

    print(
        json.dumps(
            {
                "operation": "human_auth_throttle_smoke",
                "ok": not failures,
                "failures": failures,
                "evidence": evidence,
                "policy": {
                    "source_independent": True,
                    "login_subject_limit": LOGIN_SUBJECT_LIMIT,
                    "login_global_limit": LOGIN_GLOBAL_LIMIT,
                    "pairing_subject_limit": PAIRING_SUBJECT_LIMIT,
                    "pairing_global_limit": PAIRING_GLOBAL_LIMIT,
                    "window_seconds": WINDOW_SECONDS,
                    "block_seconds": BLOCK_SECONDS,
                },
                "safety": {
                    "temporary_database": True,
                    "existing_host_contacted": False,
                    "fixture_only": True,
                    "real_runtime_called": False,
                    "raw_auth_and_source_input_omitted": True,
                },
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
