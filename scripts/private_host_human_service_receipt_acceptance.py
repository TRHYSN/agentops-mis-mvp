#!/usr/bin/env python3
"""Record read-only Worker service evidence through a Private Host Human Session.

The default mode is preview-only. ``--confirm-record`` appends one operator
receipt and one control readback per selected adapter. The Owner password is
read from macOS Keychain into process memory and is never accepted on argv,
printed, or written by this script.
"""
from __future__ import annotations

import argparse
import hashlib
import http.cookiejar
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError, URLError


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agentops_mis_cli import worker as worker_mod  # noqa: E402


DEFAULT_BASE_URL = "http://127.0.0.1:18878"
DEFAULT_USERNAME = os.environ.get("AGENTOPS_OWNER_USERNAME", "owner")
DEFAULT_KEYCHAIN_SERVICE = os.environ.get("AGENTOPS_OWNER_KEYCHAIN_SERVICE", "AgentOps MIS Private Host")
ADAPTERS = ("hermes", "openclaw")


class AcceptanceError(RuntimeError):
    """A bounded error whose code is safe to print."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Preview or record macOS Worker service-check evidence through a "
            "Private Host Owner Human Session."
        )
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--username", default=DEFAULT_USERNAME)
    parser.add_argument("--keychain-service", default=DEFAULT_KEYCHAIN_SERVICE)
    parser.add_argument(
        "--keychain-account",
        default="",
        help="Keychain account name; defaults to --username. This is never a password.",
    )
    parser.add_argument("--adapter", action="append", choices=ADAPTERS)
    parser.add_argument("--request-timeout", type=int, default=15)
    parser.add_argument("--service-check-timeout", type=int, default=5)
    parser.add_argument(
        "--confirm-record",
        action="store_true",
        help="Append receipt/readback evidence. Without this flag the operator ledger is not changed.",
    )
    return parser.parse_args(argv)


def local_origin(base_url: str) -> tuple[str, str]:
    parsed = urllib.parse.urlsplit(base_url.rstrip("/"))
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise AcceptanceError("loopback_host_required")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment or not parsed.netloc:
        raise AcceptanceError("base_url_origin_required")
    origin = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
    return origin, origin


def read_keychain_password(service: str, account: str) -> str:
    if sys.platform != "darwin":
        raise AcceptanceError("macos_keychain_required")
    try:
        result = subprocess.run(
            [
                "/usr/bin/security",
                "find-generic-password",
                "-s",
                service,
                "-a",
                account,
                "-w",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise AcceptanceError("keychain_read_failed") from exc
    password = result.stdout.rstrip("\r\n")
    if result.returncode != 0 or not password:
        raise AcceptanceError("keychain_item_unavailable")
    return password


def http_json(
    opener,
    method: str,
    url: str,
    *,
    payload: dict | None = None,
    headers: dict | None = None,
    timeout: int,
) -> tuple[int, dict]:
    data = None if payload is None else json.dumps(payload, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Accept": "application/json", "Content-Type": "application/json", **(headers or {})},
    )
    try:
        with opener.open(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return response.status, json.loads(raw) if raw else {}
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload_out = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload_out = {}
        return exc.code, payload_out if isinstance(payload_out, dict) else {}
    except (URLError, TimeoutError, OSError) as exc:
        raise AcceptanceError("host_unreachable") from exc
    except json.JSONDecodeError as exc:
        raise AcceptanceError("invalid_host_json") from exc


def authenticate_owner(args: argparse.Namespace, base_url: str, origin: str):
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    status, auth_status = http_json(
        opener,
        "GET",
        base_url + "/api/human-auth/status",
        timeout=args.request_timeout,
    )
    if status != 200 or auth_status.get("required") is not True:
        raise AcceptanceError("human_auth_not_required")
    if auth_status.get("bootstrap_required") is True:
        raise AcceptanceError("owner_bootstrap_required")

    password = read_keychain_password(args.keychain_service, args.keychain_account or args.username)
    try:
        status, authenticated = http_json(
            opener,
            "POST",
            base_url + "/api/human-auth/login",
            payload={"username": args.username, "password": password},
            headers={"Origin": origin},
            timeout=args.request_timeout,
        )
    finally:
        del password
    user = authenticated.get("user") if isinstance(authenticated.get("user"), dict) else {}
    csrf = str(authenticated.get("csrf_token") or "")
    cookie_present = any(cookie.name == "agentops_human_session" and bool(cookie.value) for cookie in jar)
    if status != 200 or user.get("role") != "owner" or not csrf or not cookie_present:
        raise AcceptanceError("owner_login_failed")
    if not user.get("account_id") or not user.get("workspace_id"):
        raise AcceptanceError("owner_context_incomplete")
    return opener, jar, csrf, user


def canonical_commands(adapter: str) -> dict[str, str]:
    agent_id = f"agt_worker_daemon_{adapter}"
    action = (
        "agentops worker service-control --manager launchd --action restart "
        f"--adapter {adapter} --agent-id {agent_id}"
    )
    verify = (
        "agentops worker service-check --manager launchd "
        f"--adapter {adapter} --agent-id {agent_id}"
    )
    signature = hashlib.sha256(
        f"local_readiness.service_control_preview:{adapter}:{action}:{verify}".encode("utf-8")
    ).hexdigest()
    action_id = f"local_readiness.service_control_preview.{adapter}"
    return {
        "agent_id": agent_id,
        "action": action,
        "verify": verify,
        "signature": signature,
        "action_id": action_id,
        "source": action_id,
    }


def run_service_check(adapter: str, workspace_id: str, timeout: int) -> dict:
    commands = canonical_commands(adapter)
    args = SimpleNamespace(
        manager="launchd",
        workspace_id=workspace_id,
        agent_id=commands["agent_id"],
        adapter=adapter,
        label="",
        service_path="",
        api_key_placeholder=worker_mod.DEFAULT_API_KEY_PLACEHOLDER,
        credential_source="auto",
        config_path=str(worker_mod.DEFAULT_CONFIG_PATH),
        timeout=timeout,
    )
    return worker_mod.check_service_installation(args)


def service_gates(service_check: dict) -> dict[str, bool]:
    service_file = service_check.get("service_file") if isinstance(service_check.get("service_file"), dict) else {}
    service_status = service_check.get("service_status") if isinstance(service_check.get("service_status"), dict) else {}
    relaunch = service_check.get("relaunch_policy") if isinstance(service_check.get("relaunch_policy"), dict) else {}
    return {
        "service_check_ok": service_check.get("ok") is True,
        "service_file_exists": service_file.get("exists") is True,
        "service_loaded": service_status.get("loaded") is True,
        "confirm_gate_ok": service_file.get("confirm_gate_ok") is True,
        "relaunch_policy_ok": service_file.get("relaunch_policy_ok") is True or relaunch.get("enabled") is True,
        "token_like_detected": service_file.get("token_like_detected") is True,
        "local_cli_service_check_performed": True,
        "service_control_executed": False,
        "live_execution_performed": False,
        "server_executes_shell": False,
    }


def receipt_listing(
    opener,
    base_url: str,
    source: str,
    signature: str,
    timeout: int,
) -> tuple[dict, list[dict]]:
    query = urllib.parse.urlencode({"limit": 50, "source": source, "action_signature": signature})
    status, payload = http_json(
        opener,
        "GET",
        base_url + "/api/operator/action-receipts?" + query,
        timeout=timeout,
    )
    if status != 200:
        raise AcceptanceError("receipt_read_failed")
    receipts = payload.get("receipts") if isinstance(payload.get("receipts"), list) else []
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    return summary, [item for item in receipts if isinstance(item, dict)]


def safe_counts(summary: dict) -> dict[str, int]:
    return {
        "receipts": int(summary.get("receipts") or 0),
        "verified": int(summary.get("verified") or 0),
        "failed": int(summary.get("failed") or 0),
        "evaluated": int(summary.get("evaluated") or 0),
        "control_readback_attached": int(summary.get("control_readback_attached") or 0),
    }


def acceptance_for_adapter(
    args: argparse.Namespace,
    opener,
    base_url: str,
    origin: str,
    csrf: str,
    user: dict,
    adapter: str,
) -> dict:
    commands = canonical_commands(adapter)
    check = run_service_check(adapter, str(user["workspace_id"]), args.service_check_timeout)
    gates = service_gates(check)
    before_summary, _ = receipt_listing(
        opener, base_url, commands["source"], commands["signature"], args.request_timeout
    )
    before = safe_counts(before_summary)

    receipt_id = None
    receipt_hash = None
    action_hash = None
    verify_hash = None
    readback_id = None
    readback_hash = None
    status_matches_check = True
    actor_bound_to_session = True
    readback_attached = False

    if args.confirm_record:
        receipt_status = "verified" if gates["service_check_ok"] else "failed"
        status, receipt_payload = http_json(
            opener,
            "POST",
            base_url + "/api/operator/action-receipts",
            payload={
                "workspace_id": user["workspace_id"],
                "actor_id": user["account_id"],
                "action_command": commands["action"],
                "verify_command": commands["verify"],
                "action_id": commands["action_id"],
                "action_signature": commands["signature"],
                "source": commands["source"],
                "status": receipt_status,
                "result_summary": f"{adapter} read-only Worker service-check recorded through Owner Human Session.",
            },
            headers={"Origin": origin, "X-AgentOps-CSRF": csrf},
            timeout=args.request_timeout,
        )
        receipt = receipt_payload.get("receipt") if isinstance(receipt_payload.get("receipt"), dict) else {}
        receipt_id = receipt.get("receipt_id")
        if status != 201 or not receipt_id:
            raise AcceptanceError(f"{adapter}_receipt_record_failed")

        control_readback = {
            "before": {
                "step_id": "preview_worker_service_control",
                "status": "preview",
                "adapter": adapter,
                "service_control_preview": True,
            },
            "after": {
                "service_check_expected": True,
                "service_check_ok": gates["service_check_ok"],
                "service_file_exists": gates["service_file_exists"],
                "service_loaded": gates["service_loaded"],
                "confirm_gate_ok": gates["confirm_gate_ok"],
                "relaunch_policy_ok": gates["relaunch_policy_ok"],
                "confirmed_os_mutation": False,
            },
            "self_check": {
                "copy_only": True,
                "server_executes_shell": False,
                "writes_ledger_for_service_control": False,
                "live_execution_performed": False,
                "raw_service_check_omitted": True,
                "local_cli_service_check_performed": True,
                "token_omitted": True,
            },
            "token_omitted": True,
        }
        status, readback_payload = http_json(
            opener,
            "POST",
            base_url + "/api/operator/action-receipts/control-readback",
            payload={
                "workspace_id": user["workspace_id"],
                "actor_id": user["account_id"],
                "receipt_id": receipt_id,
                "source": commands["source"] + ".control_readback",
                "control_readback": control_readback,
            },
            headers={"Origin": origin, "X-AgentOps-CSRF": csrf},
            timeout=args.request_timeout,
        )
        readback = readback_payload.get("readback") if isinstance(readback_payload.get("readback"), dict) else {}
        readback_id = readback.get("readback_id")
        if status != 201 or not readback_id:
            raise AcceptanceError(f"{adapter}_control_readback_failed")

    after_summary, receipts = receipt_listing(
        opener, base_url, commands["source"], commands["signature"], args.request_timeout
    )
    after = safe_counts(after_summary)
    if receipt_id:
        recorded = next((item for item in receipts if item.get("receipt_id") == receipt_id), {})
        receipt_hash = recorded.get("tamper_chain_hash")
        action_hash = recorded.get("action_hash")
        verify_hash = recorded.get("verify_hash")
        readback_id = recorded.get("control_readback_id") or readback_id
        readback_hash = recorded.get("control_readback_hash")
        readback_attached = bool(recorded.get("control_readback"))
        actor_bound_to_session = recorded.get("actor_id") == user["account_id"]
        expected_status = "verified" if gates["service_check_ok"] else "failed"
        status_matches_check = recorded.get("status") == expected_status

    expected_delta = 1 if args.confirm_record else 0
    counts = {
        "before": before,
        "after": after,
        "receipt_delta": after["receipts"] - before["receipts"],
        "control_readback_delta": after["control_readback_attached"] - before["control_readback_attached"],
    }
    write_gate = (
        counts["receipt_delta"] == expected_delta
        and counts["control_readback_delta"] == expected_delta
        and (not args.confirm_record or bool(receipt_id and receipt_hash and readback_id and readback_hash))
    )
    return {
        "adapter": adapter,
        "action_signature": commands["signature"],
        "receipt_id": receipt_id,
        "receipt_hash": receipt_hash,
        "action_hash": action_hash,
        "verify_hash": verify_hash,
        "readback_id": readback_id,
        "readback_hash": readback_hash,
        "gates": {
            **gates,
            "human_actor_bound": actor_bound_to_session,
            "receipt_status_matches_service_check": status_matches_check,
            "control_readback_attached": readback_attached,
            "ledger_write_confirmed": bool(args.confirm_record and write_gate),
            "preview_no_operator_write": bool(not args.confirm_record and write_gate),
        },
        "counts": counts,
    }


def run(args: argparse.Namespace) -> tuple[dict, int]:
    base_url = ""
    origin = ""
    opener = None
    csrf = ""
    session_authenticated = False
    logout_ok = False
    post_logout_401 = False
    results: list[dict] = []
    error_code = None
    try:
        base_url, origin = local_origin(args.base_url)
        opener, _jar, csrf, user = authenticate_owner(args, base_url, origin)
        session_authenticated = True
        selected_adapters = list(dict.fromkeys(args.adapter or ADAPTERS))
        for adapter in selected_adapters:
            results.append(acceptance_for_adapter(args, opener, base_url, origin, csrf, user, adapter))
    except AcceptanceError as exc:
        error_code = exc.code
    except Exception:
        error_code = "unexpected_acceptance_failure"
    finally:
        if opener is not None and csrf:
            try:
                status, _ = http_json(
                    opener,
                    "POST",
                    base_url + "/api/human-auth/logout",
                    payload={},
                    headers={"Origin": origin, "X-AgentOps-CSRF": csrf},
                    timeout=args.request_timeout,
                )
                logout_ok = status == 200
                status, _ = http_json(
                    opener,
                    "GET",
                    base_url + "/api/operator/action-receipts?limit=1",
                    timeout=args.request_timeout,
                )
                post_logout_401 = status == 401
            except AcceptanceError:
                error_code = error_code or "logout_verification_failed"

    selected_adapters = list(dict.fromkeys(args.adapter or ADAPTERS))
    result_gates = [item.get("gates") or {} for item in results]
    adapter_ok = all(
        gate.get("service_check_ok") is True
        and gate.get("service_file_exists") is True
        and gate.get("service_loaded") is True
        and gate.get("confirm_gate_ok") is True
        and gate.get("relaunch_policy_ok") is True
        and gate.get("token_like_detected") is False
        and gate.get("human_actor_bound") is True
        and gate.get("receipt_status_matches_service_check") is True
        and (
            gate.get("ledger_write_confirmed") is True
            if args.confirm_record
            else gate.get("preview_no_operator_write") is True
        )
        for gate in result_gates
    ) and len(results) == len(selected_adapters)
    ok = bool(
        not error_code
        and session_authenticated
        and adapter_ok
        and logout_ok
        and post_logout_401
    )
    return {
        "ok": ok,
        "operation": "private_host_human_service_receipt_acceptance",
        "confirmed_record": bool(args.confirm_record),
        "error": error_code,
        "human_session": {
            "authenticated_owner": session_authenticated,
            "csrf_present": bool(csrf),
            "logout_ok": logout_ok,
            "protected_read_after_logout_401": post_logout_401,
        },
        "adapters": results,
        "credential_omitted": True,
        "raw_prompt_response_omitted": True,
        "direct_database_access": False,
        "log_read": False,
        "token_omitted": True,
    }, 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    payload, exit_code = run(args)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
