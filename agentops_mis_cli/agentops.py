#!/usr/bin/env python3
"""
Dependency-free AgentOps MIS CLI wrapper.

This is the v1.4 local agent-facing CLI described in
docs/AGENT_GATEWAY_CLI_SPEC.md. It intentionally keeps auth simple:
environment variables first, then ~/.agentops/config.json. Responses are JSON
so local agents can parse them.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import getpass
import hashlib
import io
import json
import os
import shlex
import stat
import subprocess
import sys
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from agentops_mis_cli.advance_loop_policy import advance_loop_command_policy, advance_loop_policy_summary
from agentops_mis_cli.http_transport import credential_opener, credential_transport_url_allowed, safe_credential_error
from agentops_mis_cli.platform_paths import (
    default_config_path,
    harden_private_file,
    is_windows,
    windows_private_file_is_acceptable,
)
from agentops_mis_cli.redaction import redact_text
from agentops_mis_core.operator_start_check import compact_start_check_local_run_path, operator_agent_loop_packet


DEFAULT_BASE_URL = "http://127.0.0.1:8787"
LOCAL_DEMO_DEFAULT_URL = os.environ.get("AGENTOPS_LOCAL_DEMO_DEFAULT_URL", DEFAULT_BASE_URL).rstrip("/")
DEFAULT_WORKSPACE_ID = "local-demo"
DEFAULT_REQUEST_TIMEOUT = 30
CONFIG_PATH = default_config_path()
REORDERABLE_GLOBAL_OPTIONS = {
    "--base-url": True,
    "--api-key": True,
}


def eprint(*parts):
    print(*parts, file=sys.stderr)


def normalize_reorderable_global_options(argv: list[str] | None) -> list[str] | None:
    """Allow connection flags after subcommands without changing command semantics."""
    if argv is None:
        return None
    global_items: list[str] = []
    rest: list[str] = []
    index = 0
    while index < len(argv):
        token = argv[index]
        if token in REORDERABLE_GLOBAL_OPTIONS:
            if index + 1 >= len(argv):
                rest.append(token)
                index += 1
                continue
            global_items.extend([token, argv[index + 1]])
            index += 2
            continue
        matched = next((name for name in REORDERABLE_GLOBAL_OPTIONS if token.startswith(name + "=")), None)
        if matched:
            global_items.append(token)
            index += 1
            continue
        rest.append(token)
        index += 1
    return [*global_items, *rest]


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_config(config: dict):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = CONFIG_PATH.with_name(f".{CONFIG_PATH.name}.{uuid.uuid4().hex}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, stat.S_IRUSR | stat.S_IWUSR)
    try:
        harden_private_file(temporary)
        if is_windows() and not windows_private_file_is_acceptable(temporary):
            raise OSError("windows_private_acl_verification_failed")
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = -1
            handle.write(json.dumps(config, ensure_ascii=False, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, CONFIG_PATH)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def resolved_context(args) -> dict:
    config = load_config()
    request_timeout_raw = (
        getattr(args, "request_timeout", None)
        or os.environ.get("AGENTOPS_REQUEST_TIMEOUT")
        or config.get("request_timeout")
        or DEFAULT_REQUEST_TIMEOUT
    )
    try:
        request_timeout = max(1, int(request_timeout_raw))
    except (TypeError, ValueError):
        request_timeout = DEFAULT_REQUEST_TIMEOUT
    base_url = (getattr(args, "base_url", None) or os.environ.get("AGENTOPS_BASE_URL") or config.get("base_url") or DEFAULT_BASE_URL).rstrip("/")
    explicit_api_key = getattr(args, "api_key", None)
    if explicit_api_key is None and "AGENTOPS_API_KEY" in os.environ:
        explicit_api_key = os.environ.get("AGENTOPS_API_KEY", "")
    configured_api_key = str(config.get("api_key") or "")
    configured_key_origin = str(config.get("api_key_base_url") or config.get("base_url") or "").rstrip("/")
    config_key_origin_mismatch = bool(configured_api_key and (not configured_key_origin or configured_key_origin != base_url))
    api_key = explicit_api_key if explicit_api_key is not None else ("" if config_key_origin_mismatch else configured_api_key)
    context = {
        "base_url": base_url,
        "api_key": api_key,
        "workspace_id": getattr(args, "workspace_id", None) or os.environ.get("AGENTOPS_WORKSPACE_ID") or config.get("workspace_id") or DEFAULT_WORKSPACE_ID,
        "agent_id": getattr(args, "agent_id", None) or os.environ.get("AGENTOPS_AGENT_ID") or config.get("agent_id") or "",
        "request_timeout": request_timeout,
    }
    context["sources"] = context_sources(args, config)
    if config_key_origin_mismatch and explicit_api_key is None:
        context["sources"]["api_key"] = "blocked_origin_mismatch"
    return context


def context_sources(args, config: dict) -> dict:
    def source_for(flag_name: str, env_name: str, config_key: str, default_value: str = "") -> str:
        value = getattr(args, flag_name, None)
        if value:
            return "flag"
        if os.environ.get(env_name):
            return "env"
        if config.get(config_key):
            return "config"
        return "default" if default_value else "missing"

    return {
        "base_url": source_for("base_url", "AGENTOPS_BASE_URL", "base_url", DEFAULT_BASE_URL),
        "api_key": source_for("api_key", "AGENTOPS_API_KEY", "api_key"),
        "workspace_id": source_for("workspace_id", "AGENTOPS_WORKSPACE_ID", "workspace_id", DEFAULT_WORKSPACE_ID),
        "agent_id": source_for("agent_id", "AGENTOPS_AGENT_ID", "agent_id"),
        "request_timeout": source_for("request_timeout", "AGENTOPS_REQUEST_TIMEOUT", "request_timeout", str(DEFAULT_REQUEST_TIMEOUT)),
    }


def emit(data):
    print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))


def parse_json_value(raw: str | None, fallback):
    if raw is None or raw == "":
        return fallback
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON: {exc}") from exc


def read_json_argument(value: str | None, *, label: str) -> dict:
    if not value:
        return {}
    raw = sys.stdin.read() if value == "-" else Path(value).expanduser().read_text(encoding="utf-8")
    parsed = parse_json_value(raw, {})
    if not isinstance(parsed, dict):
        raise SystemExit(f"{label} must decode to a JSON object")
    return parsed


def split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def apply_limit(rows: list[dict], limit: int | None) -> list[dict]:
    if limit is None:
        return rows
    return rows[: max(0, int(limit))]


def now_stamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d%H%M%S%f")


def cli_truthy_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def cli_deployment_mode() -> str:
    return os.environ.get("AGENTOPS_DEPLOYMENT_MODE", "local").strip().lower() or "local"


def cli_host_is_loopback(url: str) -> bool:
    host = (urlparse(url).hostname or "").strip().lower()
    if host in {"", "localhost", "127.0.0.1", "::1"}:
        return True
    if host in {"0.0.0.0", "::"}:
        return False
    return host.endswith(".localhost")


def cli_git_head() -> str:
    root = Path(__file__).resolve().parents[1]
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except Exception:
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def add_current_code_check(payload: dict, args) -> dict:
    require_current = bool(getattr(args, "require_current_code", False))
    expected_head = str(getattr(args, "expect_head_sha", "") or "").strip()
    if require_current and not expected_head:
        expected_head = cli_git_head()
    if not require_current and not expected_head:
        return payload
    runtime = payload.get("running_instance")
    if not isinstance(runtime, dict):
        runtime = ((payload.get("gateway") or {}).get("running_instance") if isinstance(payload.get("gateway"), dict) else {}) or {}
    server_head = str(runtime.get("git_head_sha") or "")
    source_current = runtime.get("current") is True or runtime.get("status") == "current"
    head_matches = bool(expected_head and server_head and server_head.startswith(expected_head)) if expected_head else True
    ok = bool(runtime) and source_current and head_matches
    payload["local_code_check"] = {
        "operation": "local_code_check",
        "ok": ok,
        "status": "ready" if ok else "blocked",
        "require_current_code": require_current,
        "expected_head_sha": expected_head,
        "server_head_sha": server_head,
        "server_status": runtime.get("status") or "missing",
        "server_started_after_source_mtime": runtime.get("server_started_after_source_mtime"),
        "next_action": "restart the local MIS process from the current checkout, then rerun agentops local readiness --require-current-code",
        "token_omitted": True,
    }
    if not ok:
        payload["_exit_code"] = 2
    return payload


def local_demo_default_probe(target_base_url: str = "", timeout: float = 2.0) -> dict:
    probe_url = LOCAL_DEMO_DEFAULT_URL.rstrip("/")
    same_as_target = bool(target_base_url) and target_base_url.rstrip("/") == probe_url
    result = {
        "operation": "local_demo_default_probe",
        "base_url": probe_url,
        "same_as_target": same_as_target,
        "reachable": False,
        "ready": False,
        "status": "unknown",
        "status_code": None,
        "current_code_ok": None,
        "current_code_status": "unknown",
        "running_instance_current": None,
        "command": f"AGENTOPS_BASE_URL={probe_url} agentops status",
        "current_code_command": f"AGENTOPS_BASE_URL={probe_url} agentops local readiness --require-current-code",
        "repair_command": f"agentops login --base-url {probe_url}",
        "token_omitted": True,
    }
    if same_as_target:
        result["status"] = "target"
        return result
    try:
        req = Request(probe_url + "/api/agent-gateway/status", headers={"Accept": "application/json"})
        with credential_opener().open(req, timeout=timeout) as res:
            raw = res.read().decode("utf-8")
            payload = json.loads(raw) if raw else {}
            result.update({
                "reachable": True,
                "ready": payload.get("status") == "ready",
                "status": payload.get("status") or "reachable",
                "status_code": res.status,
            })
        try:
            req = Request(probe_url + "/api/local/readiness", headers={"Accept": "application/json"})
            with credential_opener().open(req, timeout=timeout) as readiness_res:
                raw = readiness_res.read().decode("utf-8")
                readiness = json.loads(raw) if raw else {}
                runtime = readiness.get("running_instance") if isinstance(readiness.get("running_instance"), dict) else {}
                local_code = readiness.get("local_code_check") if isinstance(readiness.get("local_code_check"), dict) else {}
                current_code_ok = (
                    runtime.get("current") is True
                    or runtime.get("status") == "current"
                    or local_code.get("ok") is True
                )
                result.update({
                    "current_code_ok": current_code_ok,
                    "current_code_status": runtime.get("status") or local_code.get("status") or "unknown",
                    "running_instance_current": runtime.get("current") if "current" in runtime else None,
                })
        except (HTTPError, URLError, OSError, TimeoutError, json.JSONDecodeError):
            pass
    except HTTPError as exc:
        result.update({
            "reachable": True,
            "ready": False,
            "status": "http_error",
            "status_code": exc.code,
        })
    except (URLError, OSError, TimeoutError, json.JSONDecodeError):
        result["status"] = "unreachable"
    return result


class AgentOpsClient:
    def __init__(self, context: dict):
        self.base_url = context["base_url"].rstrip("/")
        self.api_key = context["api_key"] or ""
        self.workspace_id = context["workspace_id"]
        self.agent_id = context["agent_id"]
        self.request_timeout = int(context.get("request_timeout") or DEFAULT_REQUEST_TIMEOUT)
        self.sources = context.get("sources") if isinstance(context.get("sources"), dict) else {}
        self.stale_config_token_ignored = False

    def connection_hint(self) -> str:
        source = self.sources.get("base_url") or "unknown"
        default_probe = local_demo_default_probe(self.base_url)
        probe_status = "ready" if default_probe.get("ready") else default_probe.get("status", "unknown")
        current_code_status = default_probe.get("current_code_status") or "unknown"
        hint = (
            f"base_url_source={source}; config_path={CONFIG_PATH}; "
            f"local_demo_default={LOCAL_DEMO_DEFAULT_URL}; "
            f"local_demo_probe={probe_status}; "
            f"local_demo_current_code={current_code_status}; "
            f"try: AGENTOPS_BASE_URL={LOCAL_DEMO_DEFAULT_URL} agentops status"
        )
        if source == "config":
            hint += f"; or update saved config: agentops login --base-url {LOCAL_DEMO_DEFAULT_URL}"
        elif source == "env":
            hint += "; or unset/adjust AGENTOPS_BASE_URL"
        return hint

    def _can_retry_without_stale_config_token(self, detail: str, status_code: int) -> bool:
        production_requested = cli_deployment_mode() in {"production", "prod", "shared", "hosted"} or cli_truthy_env("AGENTOPS_REQUIRE_PRODUCTION_SECURITY")
        return bool(
            status_code == 401
            and self.api_key
            and self.sources.get("api_key") == "config"
            and cli_host_is_loopback(self.base_url)
            and not production_requested
            and "token is not recognized" in detail
        )

    def _payload_has_stale_config_token_diagnostic(self, payload: dict) -> bool:
        gateway = payload.get("gateway") if isinstance(payload, dict) else None
        if isinstance(gateway, dict) and "token is not recognized" in json.dumps(gateway, ensure_ascii=False):
            return True
        for gate in payload.get("gates") or []:
            if isinstance(gate, dict) and gate.get("status") == "unauthorized" and "token is not recognized" in json.dumps(gate, ensure_ascii=False):
                return True
        return False

    def request(self, method: str, path: str, payload: dict | None = None, query: dict | None = None):
        url = self.base_url + path
        if query:
            url += "?" + urlencode({k: v for k, v in query.items() if v is not None}, doseq=True)
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None

        def make_request(api_key: str) -> Request:
            headers = {
                "Content-Type": "application/json",
                "X-AgentOps-Workspace-Id": self.workspace_id,
            }
            if self.agent_id:
                headers["X-AgentOps-Agent-Id"] = self.agent_id
            if api_key:
                if not credential_transport_url_allowed(url):
                    raise RuntimeError(
                        "Credentialed Agent Gateway requests require HTTPS or a literal loopback HTTP target"
                    )
                headers["X-AgentOps-Api-Key"] = api_key
                headers["Authorization"] = f"Bearer {api_key}"
            return Request(url, data=data, headers=headers, method=method)

        opener = credential_opener()
        try:
            with opener.open(make_request(self.api_key), timeout=self.request_timeout) as res:
                raw = res.read().decode("utf-8")
                parsed = json.loads(raw) if raw else {}
                if (
                    isinstance(parsed, dict)
                    and self._can_retry_without_stale_config_token(json.dumps(parsed, ensure_ascii=False), 401)
                    and self._payload_has_stale_config_token_diagnostic(parsed)
                ):
                    with opener.open(make_request(""), timeout=self.request_timeout) as retry_res:
                        self.stale_config_token_ignored = True
                        retry_raw = retry_res.read().decode("utf-8")
                        return json.loads(retry_raw) if retry_raw else {}
                return parsed
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            if self._can_retry_without_stale_config_token(detail, exc.code):
                with opener.open(make_request(""), timeout=self.request_timeout) as res:
                    self.stale_config_token_ignored = True
                    raw = res.read().decode("utf-8")
                    return json.loads(raw) if raw else {}
            raise RuntimeError(
                f"{method} {path} failed: {exc.code} "
                f"{safe_credential_error(detail, self.api_key, 1200)}"
            ) from exc
        except TimeoutError as exc:
            raise RuntimeError(f"{method} {path} timed out after {self.request_timeout}s; {self.connection_hint()}") from exc
        except URLError as exc:
            raise RuntimeError(f"Cannot reach {safe_credential_error(url, self.api_key, 500)}: {safe_credential_error(exc.reason, self.api_key, 500)}; {self.connection_hint()}") from exc

    def get(self, path: str, query: dict | None = None):
        return self.request("GET", path, query=query)

    def post(self, path: str, payload: dict):
        return self.request("POST", path, payload=payload)


def cmd_login(args) -> dict:
    config = load_config()
    prior_base_url = str(config.get("base_url") or "").rstrip("/")
    prior_key_origin = str(config.get("api_key_base_url") or prior_base_url).rstrip("/")
    base_url = (args.base_url or os.environ.get("AGENTOPS_BASE_URL") or config.get("base_url") or DEFAULT_BASE_URL).rstrip("/")
    explicit_api_key = args.api_key
    if getattr(args, "prompt_api_key", False):
        if explicit_api_key is not None or "AGENTOPS_API_KEY" in os.environ:
            raise RuntimeError("--prompt-api-key cannot be combined with --api-key or AGENTOPS_API_KEY")
        explicit_api_key = getpass.getpass("AgentOps enrollment token: ").strip()
        if not explicit_api_key:
            raise RuntimeError("A non-empty enrollment token is required")
    if explicit_api_key is None and "AGENTOPS_API_KEY" in os.environ:
        explicit_api_key = os.environ.get("AGENTOPS_API_KEY", "")
    api_key = explicit_api_key if explicit_api_key is not None else (config.get("api_key", "") if prior_key_origin and prior_key_origin == base_url else "")
    config.update({
        "base_url": base_url,
        "workspace_id": args.workspace_id or os.environ.get("AGENTOPS_WORKSPACE_ID") or config.get("workspace_id") or DEFAULT_WORKSPACE_ID,
    })
    if args.agent_id or os.environ.get("AGENTOPS_AGENT_ID") or config.get("agent_id"):
        config["agent_id"] = args.agent_id or os.environ.get("AGENTOPS_AGENT_ID") or config.get("agent_id")
    if api_key:
        config["api_key"] = api_key
        config["api_key_base_url"] = base_url
    elif config.get("api_key") and prior_key_origin != base_url:
        config.pop("api_key", None)
        config.pop("api_key_base_url", None)
    save_config(config)
    return {
        "ok": True,
        "config_path": str(CONFIG_PATH),
        "base_url": config["base_url"],
        "workspace_id": config["workspace_id"],
        "agent_id": config.get("agent_id", ""),
        "has_api_key": bool(config.get("api_key")),
    }


def cmd_status(args, client: AgentOpsClient) -> dict:
    return add_current_code_check(client.get("/api/agent-gateway/status"), args)


def cmd_doctor(args, client: AgentOpsClient) -> dict:
    config = load_config()
    sources = context_sources(args, config)
    checks = []
    gateway = None
    workers = None
    local_probe = local_demo_default_probe(client.base_url)
    mode = cli_deployment_mode()
    production_requested = mode in {"production", "prod", "shared", "hosted"} or cli_truthy_env("AGENTOPS_REQUIRE_PRODUCTION_SECURITY")
    non_loopback_target = not cli_host_is_loopback(client.base_url)
    has_token = bool(client.api_key)
    stale_config_token_ignored = False

    deployment_guard_ok = not ((non_loopback_target or production_requested) and not has_token)
    checks.append({
        "name": "shared_deployment_auth_guard",
        "ok": deployment_guard_ok,
        "status": "blocked" if not deployment_guard_ok else "ready",
        "deployment_mode": mode,
        "non_loopback_target": non_loopback_target,
        "has_api_key": has_token,
        "token_omitted": True,
    })

    try:
        gateway = client.get("/api/agent-gateway/status")
        stale_config_token_ignored = bool(client.stale_config_token_ignored)
        checks.append({
            "name": "agent_gateway_status",
            "ok": gateway.get("status") == "ready",
            "status": gateway.get("status"),
            "auth_mode": (gateway.get("auth") or {}).get("mode"),
            "stale_config_token_ignored_for_local_loopback": stale_config_token_ignored,
            "token_omitted": gateway.get("token_omitted") is True,
        })
    except RuntimeError as exc:
        error_text = str(exc)
        can_retry_local_without_config_token = bool(
            sources.get("api_key") == "config"
            and client.api_key
            and cli_host_is_loopback(client.base_url)
            and not production_requested
            and "401" in error_text
            and "token is not recognized" in error_text
        )
        if can_retry_local_without_config_token:
            retry_context = {
                "base_url": client.base_url,
                "api_key": "",
                "workspace_id": client.workspace_id,
                "agent_id": client.agent_id,
                "request_timeout": client.request_timeout,
                "sources": {**client.sources, "api_key": "ignored_config_for_local_dev"},
            }
            try:
                gateway = AgentOpsClient(retry_context).get("/api/agent-gateway/status")
                stale_config_token_ignored = True
                checks.append({
                    "name": "agent_gateway_status",
                    "ok": gateway.get("status") == "ready",
                    "status": gateway.get("status"),
                    "auth_mode": (gateway.get("auth") or {}).get("mode"),
                    "stale_config_token_ignored_for_local_loopback": True,
                    "token_omitted": gateway.get("token_omitted") is True,
                })
            except RuntimeError as retry_exc:
                checks.append({
                    "name": "agent_gateway_status",
                    "ok": False,
                    "error": str(retry_exc),
                    "stale_config_token_retry_failed": True,
                })
        else:
            checks.append({
                "name": "agent_gateway_status",
                "ok": False,
                "error": error_text,
            })

    try:
        workers = client.get("/api/workers/status")
        checks.append({
            "name": "worker_status",
            "ok": workers.get("status") == "ready",
            "status": workers.get("status"),
            "worker_count": workers.get("worker_count"),
            "running_workers": workers.get("running_workers"),
            "stuck_worker_tasks": workers.get("stuck_worker_tasks"),
        })
    except RuntimeError as exc:
        checks.append({
            "name": "worker_status",
            "ok": False,
            "error": str(exc),
        })

    setup_hints = []
    if not has_token:
        setup_hints.append("No AGENTOPS_API_KEY/config token detected. Local dev may still work, but remote agents should use a scoped enrollment token or short-lived session.")
    if (non_loopback_target or production_requested) and not has_token:
        setup_hints.append("Unsafe shared/production target: configure AGENTOPS_API_KEY or scoped token before using this base URL.")
    if not client.agent_id:
        setup_hints.append("No agent id resolved. Set AGENTOPS_AGENT_ID or run agentops login --agent-id ... before remote worker use.")
    if gateway and not (gateway.get("auth") or {}).get("authenticated") and has_token:
        if stale_config_token_ignored:
            setup_hints.append("Ignored a stale saved config token for loopback local-dev doctor; run agentops login --base-url ... --api-key <token> only when you intentionally want authenticated local Gateway checks.")
        else:
            setup_hints.append("A token was provided but Agent Gateway did not authenticate it; rotate or re-enroll the agent.")
    if workers and workers.get("stuck_worker_tasks", 0):
        setup_hints.append("Stuck worker tasks detected. Run agentops worker stuck and agentops worker release after review.")
    if not gateway and local_probe.get("ready"):
        current_status = local_probe.get("current_code_status") or "unknown"
        if local_probe.get("current_code_ok") is True:
            setup_hints.append(f"Configured base URL is unreachable, but local demo default is ready and current-code status is {current_status}. Use AGENTOPS_BASE_URL={local_probe.get('base_url')} or run agentops login --base-url {local_probe.get('base_url')}.")
        else:
            setup_hints.append(f"Configured base URL is unreachable, and local demo default is reachable but current-code status is {current_status}. Run {local_probe.get('current_code_command')} before handing work to Hermes/OpenClaw/Codex.")

    deployment_safety = {
        "deployment_mode": mode,
        "production_requested": production_requested,
        "non_loopback_target": non_loopback_target,
        "ok": deployment_guard_ok,
        "strict_exit_code": 0 if deployment_guard_ok else 2,
        "blocks_unsafe_shared_deployment": not deployment_guard_ok,
        "token_omitted": True,
    }
    return {
        "ok": all(item.get("ok") for item in checks),
        "_exit_code": deployment_safety["strict_exit_code"],
        "command": "agentops doctor",
        "base_url": client.base_url,
        "workspace_id": client.workspace_id,
        "agent_id": client.agent_id,
        "config_path": str(CONFIG_PATH),
        "config_exists": CONFIG_PATH.exists(),
        "auth": {
            "has_api_key": has_token,
            "api_key_source": sources["api_key"],
            "base_url_source": sources["base_url"],
            "workspace_id_source": sources["workspace_id"],
            "agent_id_source": sources["agent_id"],
            "token_omitted": True,
        },
        "deployment_safety": deployment_safety,
        "local_demo_probe": local_probe,
        "checks": checks,
        "gateway": gateway,
        "stale_config_token_ignored_for_local_loopback": stale_config_token_ignored,
        "worker_summary": {
            "status": workers.get("status") if workers else None,
            "worker_count": workers.get("worker_count") if workers else None,
            "running_workers": workers.get("running_workers") if workers else None,
            "pending_worker_tasks": workers.get("pending_worker_tasks") if workers else None,
            "stuck_worker_tasks": workers.get("stuck_worker_tasks") if workers else None,
        },
        "setup_hints": setup_hints,
    }


def cmd_local_readiness(args, client: AgentOpsClient) -> dict:
    return add_current_code_check(client.get("/api/local/readiness"), args)


def cmd_demo_readiness(args, client: AgentOpsClient) -> dict:
    return client.get("/api/demo/readiness")


def cmd_command_center_overview(args, client: AgentOpsClient) -> dict:
    return client.get("/api/command-center/overview", query={
        "limit": args.limit,
        "project_id": args.project_id or None,
        "threshold_sec": args.threshold_sec,
        "refresh_cache": "true" if args.refresh_cache else None,
    })


def cmd_commercial_config_status(args, client: AgentOpsClient) -> dict:
    return client.get("/api/commercial/config-status")


def cmd_operator_action_plan(args, client: AgentOpsClient) -> dict:
    return client.get("/api/operator/action-plan", query={"limit": args.limit})


def cmd_operator_action_receipts(args, client: AgentOpsClient) -> dict:
    receipts = client.get("/api/operator/action-receipts", query={
        "limit": args.limit,
        "source": args.source or None,
        "action_id": args.action_id or None,
        "action_signature": args.action_signature or None,
    })
    action_plan = client.get("/api/operator/action-plan", query={"limit": args.plan_limit})
    coverage = action_plan.get("receipt_coverage") or {}
    return {
        **receipts,
        "operation": "operator_action_receipts_cli",
        "receipt_coverage": coverage,
        "action_plan_status": action_plan.get("status"),
        "action_plan_top_commands": action_plan.get("top_commands") or [],
        "contract": "read-only operator action receipt ledger plus action-plan coverage; recording receipts requires explicit POST/UI or agentops operator record-action-receipt --confirm-record",
        "safety": {
            **(receipts.get("safety") or {}),
            "read_only": True,
            "ledger_mutated": False,
            "live_execution_performed": False,
            "token_omitted": True,
        },
        "token_omitted": True,
    }


def cmd_operator_record_action_receipt(args, client: AgentOpsClient) -> dict:
    action_command = str(args.action_command or "").strip()
    verify_command = str(args.verify_command or "").strip()
    payload = {
        "workspace_id": client.workspace_id,
        "actor_id": args.actor_id,
        "action_command": action_command,
        "verify_command": verify_command,
        "action_id": args.action_id,
        "action_signature": args.action_signature,
        "source": args.source,
        "status": args.status,
        "result_summary": args.result_summary,
        "prepared_action_id": args.prepared_action_id,
        "prepared_action_hash": args.prepared_action_hash,
        "required_prepared_action_status": args.required_prepared_action_status,
    }
    if not args.confirm_record:
        return {
            "provider": "agentops-operator",
            "operation": "operator_action_receipt_cli_preview",
            "status": "preview",
            "recorded": False,
            "workspace_id": client.workspace_id,
            "payload_preview": {
                **{key: value for key, value in payload.items() if value not in (None, "")},
                "action_command": redact_text(action_command, 500),
                "verify_command": redact_text(verify_command, 500) if verify_command else None,
                "action_hash": hashlib.sha256(action_command.encode("utf-8")).hexdigest() if action_command else None,
                "verify_hash": hashlib.sha256(verify_command.encode("utf-8")).hexdigest() if verify_command else None,
            },
            "next_actions": [
                "rerun this command with --confirm-record to append an audited receipt",
                "agentops operator action-receipts --limit 12",
                "agentops operator loop-audit --limit 20",
            ],
            "contract": "preview-only; does not POST, does not execute action_command or verify_command, and does not mutate the ledger; prepared_action_id/hash/status binding is verified only on --confirm-record",
            "safety": {
                "read_only": True,
                "ledger_mutated": False,
                "live_execution_performed": False,
                "raw_prompt_omitted": True,
                "raw_response_omitted": True,
                "token_omitted": True,
            },
            "token_omitted": True,
        }
    result = client.post("/api/operator/action-receipts", payload)
    return {
        **result,
        "cli_operation": "operator_record_action_receipt",
        "confirm_record": True,
        "contract": "confirmed append-only receipt record; CLI never executes action_command or verify_command; prepared_action_id/hash/status binding is server-verified when supplied",
        "token_omitted": True,
    }


def cmd_operator_record_control_readback(args, client: AgentOpsClient) -> dict:
    control_readback = parse_json_value(args.control_readback_json, {})
    if not isinstance(control_readback, dict):
        raise SystemExit("--control-readback-json must decode to a JSON object")
    payload = {
        "workspace_id": client.workspace_id,
        "actor_id": args.actor_id,
        "receipt_id": args.receipt_id,
        "source": args.source,
        "control_readback": control_readback,
    }
    if not args.confirm_record:
        return {
            "provider": "agentops-operator",
            "operation": "operator_control_readback_cli_preview",
            "status": "preview",
            "recorded": False,
            "workspace_id": client.workspace_id,
            "payload_preview": {
                "receipt_id": redact_text(args.receipt_id, 160),
                "source": redact_text(args.source, 160),
                "control_readback_hash": hashlib.sha256(json.dumps(control_readback, sort_keys=True).encode("utf-8")).hexdigest(),
                "control_readback_omitted": True,
                "token_omitted": True,
            },
            "next_actions": [
                "rerun this command with --confirm-record to append an audited control readback",
                "agentops operator action-receipts --limit 12 --plan-limit 12",
            ],
            "contract": "preview-only; does not POST, does not execute commands, and does not mutate the ledger",
            "safety": {
                "read_only": True,
                "ledger_mutated": False,
                "live_execution_performed": False,
                "raw_prompt_omitted": True,
                "raw_response_omitted": True,
                "token_omitted": True,
            },
            "token_omitted": True,
        }
    result = client.post("/api/operator/action-receipts/control-readback", payload)
    return {
        **result,
        "cli_operation": "operator_record_control_readback",
        "confirm_record": True,
        "contract": "confirmed append-only control readback; CLI never executes action_command or verify_command",
        "token_omitted": True,
    }


def cmd_operator_propose_receipt_failure_memory(args, client: AgentOpsClient) -> dict:
    payload = {
        "workspace_id": client.workspace_id,
        "action_hash": args.action_hash,
        "min_failures": args.min_failures,
        "memory_id": args.memory_id,
        "canonical_text": args.canonical_text,
        "actor_id": args.actor_id,
        "confirm_create": bool(args.confirm_create),
    }
    result = client.post(
        "/api/operator/receipt-failure-memories/propose",
        {key: value for key, value in payload.items() if value not in (None, "")},
    )
    return {
        **result,
        "cli_operation": "operator_propose_receipt_failure_memory",
        "confirm_create": bool(args.confirm_create),
        "contract": "preview-only unless --confirm-create is supplied; proposes a memory candidate from repeated failed Action Queue receipt evaluations without executing commands",
        "token_omitted": True,
    }


def cmd_operator_receipt_failure_memories(args, client: AgentOpsClient) -> dict:
    return client.get(
        "/api/operator/receipt-failure-memories",
        query={
            "workspace_id": client.workspace_id,
            "min_failures": args.min_failures,
            "limit": args.limit,
        },
    )


def cmd_operator_loop_audit(args, client: AgentOpsClient) -> dict:
    return client.get("/api/operator/loop-audit", query={"limit": args.limit, "loop_id": args.loop_id or None})


def cmd_operator_loop_control(args, client: AgentOpsClient) -> dict:
    return client.get("/api/operator/loop-control", query={"limit": args.limit, "loop_id": args.loop_id or None})


def cmd_operator_evidence_report(args, client: AgentOpsClient) -> dict:
    return client.get("/api/operator/evidence-report", query={
        "limit": args.limit,
        "run_id": args.run_id,
        "task_id": args.task_id,
    })


def cmd_operator_handoff(args, client: AgentOpsClient) -> dict:
    return client.get("/api/operator/handoff", query={"limit": args.limit, "loop_id": args.loop_id or None})


def cmd_operator_loop_self_check(args, client: AgentOpsClient) -> dict:
    return client.get("/api/operator/loop-self-check", query={"limit": args.limit, "loop_id": args.loop_id or None})


def cmd_operator_health(args, client: AgentOpsClient) -> dict:
    return client.get("/api/operator/health", query={"limit": args.limit, "loop_id": args.loop_id or None})


def cmd_operator_runtime_doctor(args, client: AgentOpsClient) -> dict:
    return client.get(
        "/api/operator/runtime-doctor",
        query={
            "limit": args.limit,
            "loop_id": args.loop_id or None,
            "base_url": args.runtime_base_url or None,
        },
    )


def cmd_operator_live_acceptance(args, client: AgentOpsClient) -> dict:
    return client.get(
        "/api/operator/live-acceptance",
        query={
            "freshness_hours": args.freshness_hours,
            "limit": args.limit,
        },
    )


def cmd_operator_local_harness_proof(args, client: AgentOpsClient) -> dict:
    return client.get(
        "/api/operator/local-harness-proof",
        query={
            "freshness_hours": args.freshness_hours,
            "limit": args.limit,
        },
    )


LIVE_PRODUCT_REQUIRED_EVIDENCE = [
    "completed_adapter_tool_calls",
    "passing_evaluations",
    "runtime_events",
    "audit_logs",
    "customer_worker_artifacts",
    "memories",
    "approvals",
    "verified_plan_evidence_manifests",
]


def summarize_live_product_adapter(adapter: str, item: dict, failures: list[str]) -> dict:
    latest = item.get("latest_passing") if isinstance(item.get("latest_passing"), dict) else {}
    if not latest:
        latest = item.get("latest_attempt") if isinstance(item.get("latest_attempt"), dict) else {}
    evidence = latest.get("evidence") if isinstance(latest.get("evidence"), dict) else {}
    checks = latest.get("checks") if isinstance(latest.get("checks"), list) else []
    if item.get("status") != "fresh":
        failures.append(f"{adapter}: expected fresh status, got {item.get('status') or 'missing'}")
    if item.get("ok") is not True:
        failures.append(f"{adapter}: live acceptance ok flag is not true")
    if latest.get("pass") is not True:
        failures.append(f"{adapter}: latest passing acceptance evidence is missing")
    if latest.get("run_status") != "completed":
        failures.append(f"{adapter}: latest live acceptance run is not completed")
    for key in LIVE_PRODUCT_REQUIRED_EVIDENCE:
        if int(evidence.get(key) or 0) < 1:
            failures.append(f"{adapter}: missing evidence {key}")
    failed_checks = [str(check.get("id") or "unknown") for check in checks if isinstance(check, dict) and not check.get("ok")]
    if failed_checks:
        failures.append(f"{adapter}: failed acceptance checks {', '.join(failed_checks)}")
    return {
        "adapter": adapter,
        "status": item.get("status"),
        "run_id": latest.get("run_id"),
        "task_id": latest.get("task_id"),
        "artifact_id": latest.get("artifact_id"),
        "plan_evidence_manifest_id": latest.get("plan_evidence_manifest_id"),
        "age_hours": latest.get("age_hours"),
        "evidence": {key: int(evidence.get(key) or 0) for key in LIVE_PRODUCT_REQUIRED_EVIDENCE},
        "token_omitted": item.get("token_omitted") is True and latest.get("token_omitted") is True,
    }


def build_live_product_readiness_result(
    *,
    base_url: str,
    workspace_id: str,
    freshness_hours: int,
    required_adapters: list[str],
    live: dict,
    local: dict,
) -> dict:
    failures: list[str] = []
    if live.get("operation") != "live_acceptance_readiness":
        failures.append("live acceptance read model is missing")
    if live.get("live_execution_performed") is not False:
        failures.append("live product-readiness must be read-only and must not execute runtimes")
    if (live.get("safety") or {}).get("read_only") is not True:
        failures.append("live acceptance safety read_only proof is missing")
    if local.get("operation") != "local_readiness":
        failures.append("local readiness read model is missing")
    if local.get("live_execution_performed") is not False:
        failures.append("local readiness must not execute live runtimes")
    adapters = live.get("adapters") if isinstance(live.get("adapters"), dict) else {}
    adapter_summaries = []
    for adapter in required_adapters:
        item = adapters.get(adapter)
        if not isinstance(item, dict):
            failures.append(f"{adapter}: missing live acceptance adapter row")
            continue
        adapter_summaries.append(summarize_live_product_adapter(adapter, item, failures))
    live_summary = live.get("summary") if isinstance(live.get("summary"), dict) else {}
    local_evidence = local.get("evidence") if isinstance(local.get("evidence"), dict) else {}
    if int(live_summary.get("fresh") or 0) < len(required_adapters):
        failures.append("live acceptance summary does not have enough fresh adapters")
    if int(local_evidence.get("live_acceptance_fresh_adapters") or 0) < len(required_adapters):
        failures.append("local readiness does not report enough fresh live adapters")
    gates = {gate.get("id"): gate for gate in (local.get("gates") or []) if isinstance(gate, dict)}
    live_gate = gates.get("live_acceptance_freshness") or {}
    if live_gate.get("ok") is not True:
        failures.append("local readiness live_acceptance_freshness gate is not passing")
    return {
        "provider": "agentops-operator",
        "operation": "operator_live_product_readiness",
        "ok": not failures,
        "_exit_code": 0 if not failures else 1,
        "product_readiness_proof": not failures,
        "evidence_class": "manual_live_ledger_readback",
        "base_url": base_url.rstrip("/"),
        "workspace_id": workspace_id,
        "freshness_hours": freshness_hours,
        "required_adapters": required_adapters,
        "adapters": adapter_summaries,
        "live_acceptance_status": live.get("status"),
        "local_readiness_status": local.get("status"),
        "failures": failures,
        "next_actions": [] if not failures else [
            "python3 scripts/customer_worker_real_runtime_acceptance.py --confirm-live --adapter hermes --adapter openclaw --hermes-max-tokens 512",
            "agentops operator live-acceptance --limit 8",
        ],
        "contract": "read-only product proof from fresh Hermes/OpenClaw customer-worker ledger evidence; does not call runtimes, mutate the ledger, or expose raw prompts/responses/tokens",
        "safety": {
            "read_only": True,
            "ledger_mutated": False,
            "live_execution_performed": False,
            "raw_prompt_omitted": True,
            "raw_response_omitted": True,
            "token_omitted": True,
        },
        "token_omitted": True,
    }


def cmd_operator_live_product_readiness(args, client: AgentOpsClient) -> dict:
    required_adapters = args.require_adapter or ["hermes", "openclaw"]
    live = client.get(
        "/api/operator/live-acceptance",
        query={
            "freshness_hours": args.freshness_hours,
            "limit": args.limit,
        },
    )
    local = client.get("/api/local/readiness")
    return build_live_product_readiness_result(
        base_url=client.base_url,
        workspace_id=client.workspace_id,
        freshness_hours=args.freshness_hours,
        required_adapters=required_adapters,
        live=live,
        local=local,
    )


def cmd_operator_execution_mode(args, client: AgentOpsClient) -> dict:
    return client.get(
        "/api/operator/execution-mode",
        query={
            "adapter": args.adapter,
            "confirm_run": "true" if args.confirm_run else "false",
            "limit": args.limit,
        },
    )


def cmd_operator_command_center(args, client: AgentOpsClient) -> dict:
    return client.get("/api/operator/command-center", query={"limit": args.limit, "project_id": args.project_id or None})


def cmd_operator_intake_checklist(args, client: AgentOpsClient) -> dict:
    return client.get("/api/operator/intake-checklist", query={"limit": args.limit})


def cmd_operator_loop_launch_packet(args, client: AgentOpsClient) -> dict:
    payload = client.get(
        "/api/operator/loop-launch-packet",
        query={
            "limit": args.limit,
            "task_id": args.task_id,
            "agent_id": args.agent_id,
            "q": args.query,
            "handoff_mode": args.handoff_mode,
            "full_handoff": "true" if args.full_handoff else None,
        },
    )
    if args.brief:
        local_readiness = client.get("/api/local/readiness")
        return compact_loop_launch_packet(payload, adapter=args.adapter, local_readiness=local_readiness)
    return payload


def cmd_operator_start_check(args, client: AgentOpsClient) -> dict:
    return client.get(
        "/api/operator/start-check",
        query={
            "adapter": args.adapter,
            "limit": args.limit,
            "loop_id": args.loop_id,
            "task_id": args.task_id,
            "agent_id": args.agent_id,
            "q": args.query,
            "handoff_mode": args.handoff_mode,
            "full_handoff": "true" if args.full_handoff else None,
            "base_url": args.runtime_base_url or None,
            "freshness_hours": args.freshness_hours,
        },
    )


def _local_current_code_summary(local_readiness: dict) -> dict:
    running = local_readiness.get("running_instance") if isinstance(local_readiness.get("running_instance"), dict) else {}
    local_code = local_readiness.get("local_code_check") if isinstance(local_readiness.get("local_code_check"), dict) else {}
    gates = local_readiness.get("gates") if isinstance(local_readiness.get("gates"), list) else []
    freshness_gate = next((gate for gate in gates if isinstance(gate, dict) and gate.get("id") == "running_instance_freshness"), {})
    current = (
        running.get("current") is True
        or local_code.get("ok") is True
        or freshness_gate.get("status") == "current"
    )
    status = running.get("status") or local_code.get("status") or freshness_gate.get("status") or ("current" if current else "unknown")
    return {
        "operation": "agent_loop_handoff_current_code",
        "ok": bool(current),
        "status": status,
        "git_head_sha": running.get("git_head_sha") or local_code.get("server_head_sha"),
        "git_branch": running.get("git_branch"),
        "server_pid": running.get("server_pid"),
        "server_started_after_source_mtime": running.get("server_started_after_source_mtime") if "server_started_after_source_mtime" in running else local_code.get("server_started_after_source_mtime"),
        "command": "agentops local readiness --require-current-code",
        "strict_command": f"agentops local readiness --require-current-code --expect-head-sha {running.get('git_head_sha') or '<head_sha>'}",
        "safety": {
            "read_only": True,
            "ledger_mutated": False,
            "live_execution_performed": False,
            "server_executes_shell": False,
            "token_omitted": True,
        },
        "token_omitted": True,
    }


def _live_product_adapter(live_product: dict, adapter: str) -> dict:
    adapters = live_product.get("adapters") if isinstance(live_product.get("adapters"), list) else []
    item = next((row for row in adapters if isinstance(row, dict) and row.get("adapter") == adapter), {})
    return {
        "adapter": adapter,
        "status": item.get("status") or "not_required",
        "fresh": item.get("status") == "fresh",
        "run_id": item.get("run_id"),
        "task_id": item.get("task_id"),
        "artifact_id": item.get("artifact_id"),
        "plan_evidence_manifest_id": item.get("plan_evidence_manifest_id"),
        "evidence": item.get("evidence") or {},
        "command": f"agentops operator live-product-readiness --require-adapter {adapter}",
        "token_omitted": True,
    }


def _build_agent_loop_handoff_consumer(
    *,
    adapter: str,
    args,
    client: AgentOpsClient,
    local_readiness: dict,
    current_code: dict,
    live_product: dict,
) -> dict:
    start_check = client.get(
        "/api/operator/start-check",
        query={
            "adapter": adapter,
            "limit": args.limit,
            "loop_id": args.loop_id,
            "task_id": args.task_id,
            "agent_id": args.agent_id,
            "q": args.query,
            "handoff_mode": args.handoff_mode,
            "full_handoff": "true" if args.full_handoff else None,
            "freshness_hours": args.freshness_hours,
        },
    )
    launch_packet = client.get(
        "/api/operator/loop-launch-packet",
        query={
            "limit": args.limit,
            "task_id": args.task_id,
            "agent_id": args.agent_id,
            "q": args.query,
            "handoff_mode": args.handoff_mode,
            "full_handoff": "true" if args.full_handoff else None,
        },
    )
    brief = compact_loop_launch_packet(launch_packet, adapter=adapter, local_readiness=local_readiness)
    acceptance = start_check.get("acceptance_packet") if isinstance(start_check.get("acceptance_packet"), dict) else {}
    decision = acceptance.get("decision") if isinstance(acceptance.get("decision"), dict) else {}
    start_safety = start_check.get("safety") if isinstance(start_check.get("safety"), dict) else {}
    agent_loop = start_check.get("agent_loop_packet") if isinstance(start_check.get("agent_loop_packet"), dict) else {}
    method_gates = agent_loop.get("method_gates") if isinstance(agent_loop.get("method_gates"), list) else []
    method_gate_ids = [gate.get("id") for gate in method_gates if isinstance(gate, dict) and gate.get("id")]
    phase_commands = agent_loop.get("phase_commands") if isinstance(agent_loop.get("phase_commands"), dict) else {}
    loop_commands = agent_loop.get("commands") if isinstance(agent_loop.get("commands"), dict) else {}
    required_phase_commands = {"read", "plan", "retrieve", "compare", "preflight", "execute", "verify", "record"}
    live_adapter = _live_product_adapter(live_product, adapter) if adapter in {"hermes", "openclaw"} else {
        "adapter": adapter,
        "status": "not_required",
        "fresh": True,
        "command": "agentops operator live-product-readiness --require-adapter hermes --require-adapter openclaw",
        "token_omitted": True,
    }
    blockers: list[str] = []
    attention: list[str] = []
    if current_code.get("ok") is not True:
        blockers.append("current_code_not_current")
    if start_safety.get("server_executes_shell") is not False:
        blockers.append("server_shell_safety_missing")
    if not method_gate_ids:
        blockers.append("method_gates_missing")
    if not required_phase_commands.issubset(set(phase_commands)):
        blockers.append("phase_commands_incomplete")
    if decision.get("can_preview_loop") is not True:
        blockers.append("preview_loop_not_allowed")
    if decision.get("can_confirm_bounded_loop") is not True:
        attention.append("bounded_loop_confirm_not_ready")
    if adapter in {"hermes", "openclaw"} and live_adapter.get("fresh") is not True:
        attention.append("live_product_evidence_not_fresh")
    brief_summary = brief.get("summary") if isinstance(brief.get("summary"), dict) else {}
    if brief_summary.get("current_code_ok") is False:
        blockers.append("launch_brief_current_code_not_ok")
    local_run_path = brief.get("local_run_path") if isinstance(brief.get("local_run_path"), dict) else {}
    service_managed_loop = local_run_path.get("service_managed_loop") if isinstance(local_run_path.get("service_managed_loop"), dict) else {}
    status = "blocked" if blockers else "attention" if attention or start_check.get("status") == "attention" else "ready"
    return {
        "operation": "agent_loop_handoff_consumer",
        "adapter": adapter,
        "status": status,
        "ready_for_handoff": not blockers,
        "ready_for_bounded_loop_confirm": not blockers and decision.get("can_confirm_bounded_loop") is True,
        "ready_for_live_dispatch": adapter not in {"hermes", "openclaw"} or (live_adapter.get("fresh") is True and decision.get("live_dispatch_allowed") is True),
        "blockers": blockers,
        "attention": attention,
        "task_id": start_check.get("task_id") or brief.get("task_id"),
        "agent_id": start_check.get("agent_id") or brief.get("agent_id"),
        "start_check": {
            "status": start_check.get("status"),
            "command": loop_commands.get("start_check") or f"agentops operator start-check --adapter {adapter} --limit {args.limit}",
            "current_phase": agent_loop.get("current_phase"),
            "can_preview_loop": decision.get("can_preview_loop") is True,
            "can_confirm_bounded_loop": decision.get("can_confirm_bounded_loop") is True,
            "live_dispatch_requires_confirm_run": decision.get("live_dispatch_requires_confirm_run") is True,
            "human_review_required": decision.get("human_review_required") is True,
            "memory_review_required": decision.get("memory_review_required") is True,
            "server_executes_shell": start_safety.get("server_executes_shell"),
            "token_omitted": True,
        },
        "launch_brief": {
            "status": brief.get("status"),
            "adapter": brief.get("adapter"),
            "next_command": brief.get("next_command"),
            "verify_command": brief.get("verify_command"),
            "receipt_command": brief.get("receipt_command"),
            "adapter_preflight_command": brief.get("adapter_preflight_command"),
            "live_run_command": brief.get("live_run_command"),
            "current_code_ok": brief_summary.get("current_code_ok"),
            "control_mode": brief_summary.get("control_mode"),
            "recommended_step": brief_summary.get("recommended_step"),
            "token_omitted": True,
        },
        "local_deployment": {
            "local_run_path": local_run_path,
            "service_managed_loop": service_managed_loop,
            "token_omitted": True,
        },
        "live_product_readiness": live_adapter,
        "method": {
            "phases": ["read", "plan", "retrieve", "compare", "preflight", "execute", "verify", "record"],
            "phase_commands": {key: phase_commands.get(key) for key in ["read", "plan", "retrieve", "compare", "preflight", "execute", "verify", "record"]},
            "method_gate_ids": method_gate_ids,
            "required_gate_ids": [
                "read_start_check",
                "read_current_code",
                "plan_agent_plan",
                "retrieve_knowledge",
                "compare_base_reference",
                "preflight_adapter",
                "execute_bounded_loop",
                "verify_loop",
                "record_memory_candidate",
            ],
            "token_omitted": True,
        },
        "commands": {
            "agent_loop_handoff": f"agentops operator agent-loop-handoff --adapter {adapter} --limit {args.limit}",
            "local_readiness": current_code.get("strict_command") or "agentops local readiness --require-current-code",
            "start_check": loop_commands.get("start_check") or f"agentops operator start-check --adapter {adapter} --limit {args.limit}",
            "launch_brief": f"agentops operator loop-launch-packet --brief --adapter {adapter} --limit {args.limit}",
            "loop_driver_preview": loop_commands.get("preview_loop"),
            "loop_driver_confirm": loop_commands.get("confirm_loop"),
            "adapter_preflight": loop_commands.get("adapter_preflight") or f"agentops worker preflight --adapter {adapter}",
            "live_product_readiness": live_adapter.get("command"),
            "review_queue": loop_commands.get("review_queue") or "agentops review queue --limit 20",
        },
        "contract": "compact consumer-specific Agent Work Method handoff for local Hermes/OpenClaw/Codex loops; copy commands locally and never treat this read as permission for live execution without the confirm gates it names",
        "safety": {
            "read_only": True,
            "ledger_mutated": False,
            "live_execution_performed": False,
            "server_executes_shell": False,
            "raw_prompt_omitted": True,
            "raw_response_omitted": True,
            "raw_content_omitted": True,
            "token_omitted": True,
        },
        "token_omitted": True,
    }


def cmd_operator_agent_loop_handoff(args, client: AgentOpsClient) -> dict:
    adapters = list(dict.fromkeys(args.adapter or ["hermes", "openclaw"]))
    server_query = {
        "adapter": adapters,
        "limit": args.limit,
        "loop_id": args.loop_id,
        "task_id": args.task_id,
        "agent_id": args.agent_id,
        "q": args.query,
        "handoff_mode": args.handoff_mode,
        "full_handoff": "true" if args.full_handoff else None,
        "freshness_hours": args.freshness_hours,
        "include_codex": "true" if args.include_codex else "false",
    }
    try:
        payload = client.get("/api/operator/agent-loop-handoff", query=server_query)
        if payload.get("operation") == "operator_agent_loop_handoff":
            return payload
    except RuntimeError as exc:
        if "failed: 404" not in str(exc):
            raise
    local_readiness = client.get("/api/local/readiness")
    current_code = _local_current_code_summary(local_readiness)
    live_required = [adapter for adapter in adapters if adapter in {"hermes", "openclaw"}]
    live_product = build_live_product_readiness_result(
        base_url=client.base_url,
        workspace_id=client.workspace_id,
        freshness_hours=args.freshness_hours,
        required_adapters=live_required or ["hermes", "openclaw"],
        live=client.get(
            "/api/operator/live-acceptance",
            query={
                "freshness_hours": args.freshness_hours,
                "limit": args.limit,
            },
        ),
        local=local_readiness,
    )
    consumers = [
        _build_agent_loop_handoff_consumer(
            adapter=adapter,
            args=args,
            client=client,
            local_readiness=local_readiness,
            current_code=current_code,
            live_product=live_product,
        )
        for adapter in adapters
    ]
    consumer_statuses = [item.get("status") for item in consumers]
    codex_consumer = {
        "operation": "agent_loop_handoff_codex_consumer",
        "status": "ready" if current_code.get("ok") else "blocked",
        "role": "repo_local_supervisor",
        "uses_same_packets": True,
        "commands": {
            "read_handoff": f"agentops operator agent-loop-handoff --limit {args.limit}",
            "loop_control": f"agentops operator loop-control --limit {args.limit}",
            "loop_launch_brief": f"agentops operator loop-launch-packet --brief --adapter {adapters[0] if adapters else 'mock'} --limit {args.limit}",
            "loop_driver_preview": f"agentops operator loop-driver --adapter {adapters[0] if adapters else 'mock'} --max-steps 3 --limit {args.limit}",
            "review_queue": "agentops review queue --limit 20",
        },
        "contract": "Codex supervises the same copy-only Method Block packet and should not bypass Agent Plan, retrieval, base comparison, receipt, approval, or memory-review gates.",
        "safety": {
            "read_only": True,
            "ledger_mutated": False,
            "live_execution_performed": False,
            "server_executes_shell": False,
            "token_omitted": True,
        },
        "token_omitted": True,
    } if args.include_codex else None
    if any(status == "blocked" for status in consumer_statuses) or (codex_consumer and codex_consumer.get("status") == "blocked"):
        status = "blocked"
    elif any(status == "attention" for status in consumer_statuses) or live_product.get("ok") is not True:
        status = "attention"
    else:
        status = "ready"
    return {
        "provider": "agentops-operator",
        "operation": "operator_agent_loop_handoff",
        "status": status,
        "base_url": client.base_url,
        "workspace_id": client.workspace_id,
        "adapters": adapters,
        "current_code": current_code,
        "live_product_readiness": {
            "ok": live_product.get("ok"),
            "status": live_product.get("live_acceptance_status"),
            "product_readiness_proof": live_product.get("product_readiness_proof"),
            "required_adapters": live_product.get("required_adapters") or [],
            "safety": live_product.get("safety") or {},
            "token_omitted": True,
        },
        "summary": {
            "consumers": len(consumers) + (1 if codex_consumer else 0),
            "ready_consumers": sum(1 for item in consumers if item.get("status") == "ready") + (1 if codex_consumer and codex_consumer.get("status") == "ready" else 0),
            "attention_consumers": sum(1 for item in consumers if item.get("status") == "attention"),
            "blocked_consumers": sum(1 for item in consumers if item.get("status") == "blocked") + (1 if codex_consumer and codex_consumer.get("status") == "blocked" else 0),
            "ready_for_handoff": all(item.get("ready_for_handoff") for item in consumers) and (not codex_consumer or codex_consumer.get("status") != "blocked"),
            "ready_for_all_bounded_loop_confirm": all(item.get("ready_for_bounded_loop_confirm") for item in consumers),
            "fresh_live_adapters": sum(1 for item in consumers if (item.get("live_product_readiness") or {}).get("fresh") is True and item.get("adapter") in {"hermes", "openclaw"}),
            "current_code_ok": current_code.get("ok") is True,
        },
        "consumers": consumers,
        "codex_consumer": codex_consumer,
        "next_actions": [
            command
            for command in [
                current_code.get("strict_command") if current_code.get("ok") is not True else None,
                *(item.get("commands", {}).get("start_check") for item in consumers if item.get("status") != "ready"),
                *(item.get("commands", {}).get("live_product_readiness") for item in consumers if item.get("attention")),
            ]
            if command
        ][:8],
        "contract": "read-only aggregate Agent Work Method handoff for Hermes/OpenClaw/Codex; it reads current-code readiness, live ledger proof, start-check, and compact launch briefs, but never runs adapters, executes shell on the server, mutates ledgers, approves reviews, or exposes raw prompts/responses/tokens",
        "safety": {
            "read_only": True,
            "ledger_mutated": False,
            "live_execution_performed": False,
            "server_executes_shell": False,
            "raw_prompt_omitted": True,
            "raw_response_omitted": True,
            "raw_content_omitted": True,
            "token_omitted": True,
        },
        "token_omitted": True,
        "live_execution_performed": False,
    }


def cmd_operator_loop_supervision(args, client: AgentOpsClient) -> dict:
    payload = client.get(
        "/api/operator/loop-supervision",
        query={
            "adapter": list(dict.fromkeys(args.adapter or ["hermes", "openclaw"])),
            "limit": args.limit,
            "loop_id": args.loop_id,
            "task_id": args.task_id,
            "agent_id": args.agent_id,
            "q": args.query,
            "handoff_mode": args.handoff_mode,
            "full_handoff": "true" if args.full_handoff else None,
            "freshness_hours": args.freshness_hours,
            "include_codex": "true" if args.include_codex else "false",
            "work_packet": "true" if getattr(args, "work_packet", False) else None,
            "decision": "true" if getattr(args, "decision", False) else None,
        },
    )
    if getattr(args, "decision", False):
        if payload.get("operation") == "operator_loop_work_packet_decision":
            return payload
        bundle = payload if payload.get("operation") == "operator_loop_work_packet_bundle" else compact_loop_supervision_work_packets(payload)
        return loop_work_packet_decision_from_bundle(bundle)
    if getattr(args, "work_packet", False):
        if payload.get("operation") == "operator_loop_work_packet_bundle":
            return payload
        return compact_loop_supervision_work_packets(payload)
    return payload


def _shell_option(command: str, name: str, default: str = "") -> str:
    if not command:
        return default
    try:
        parts = shlex.split(command)
    except ValueError:
        return default
    for index, part in enumerate(parts):
        if part == name and index + 1 < len(parts):
            return parts[index + 1]
        if part.startswith(name + "="):
            return part.split("=", 1)[1]
    return default


def _service_check_readback(adapter: str, service_check: dict, *, verify_command: str) -> dict:
    service_file = service_check.get("service_file") if isinstance(service_check.get("service_file"), dict) else {}
    service_status = service_check.get("service_status") if isinstance(service_check.get("service_status"), dict) else {}
    relaunch_policy = service_check.get("relaunch_policy") if isinstance(service_check.get("relaunch_policy"), dict) else {}
    return {
        "verify_command": verify_command,
        "service_check_expected": True,
        "service_check_ok": service_check.get("ok") is True,
        "service_file_exists": service_file.get("exists") is True,
        "service_loaded": service_status.get("loaded") is True,
        "confirm_gate_ok": service_file.get("confirm_gate_ok") is True,
        "relaunch_policy_ok": service_file.get("relaunch_policy_ok") is True or relaunch_policy.get("enabled") is True,
        "adapter": adapter,
        "manager": service_check.get("manager"),
        "service_path": redact_text(service_check.get("service_path"), 240),
        "service_label": redact_text(service_check.get("label"), 120),
        "token_omitted": True,
    }


def _local_service_check_from_command(args, client: AgentOpsClient, command: str, *, manager: str = "") -> tuple[dict, list[str]]:
    from . import worker as worker_mod

    command = command or ""
    missing: list[str] = []
    parsed_manager = args.service_check_manager or _shell_option(command, "--manager", manager or "launchd")
    if parsed_manager not in {"launchd", "systemd"}:
        missing.append("service_check_manager")
    agent_id = args.service_check_agent_id or _shell_option(command, "--agent-id", client.agent_id or worker_mod.DEFAULT_AGENT_ID)
    adapter = _shell_option(command, "--adapter", args.adapter)
    if adapter and adapter != args.adapter:
        missing.append("service_check_adapter_match")
    try:
        timeout = int(_shell_option(command, "--timeout", str(args.service_check_timeout)))
    except ValueError:
        timeout = args.service_check_timeout
    if missing:
        return {}, missing
    check_args = argparse.Namespace(
        manager=parsed_manager,
        workspace_id=client.workspace_id,
        agent_id=agent_id,
        adapter=args.adapter,
        label=args.service_label or _shell_option(command, "--label", ""),
        service_path=args.service_path or _shell_option(command, "--service-path", ""),
        api_key_placeholder=args.api_key_placeholder,
        timeout=timeout,
    )
    payload = worker_mod.check_service_installation(check_args)
    payload["command"] = "agentops worker service-check"
    payload["source_command"] = redact_text(command, 500)
    payload["local_cli_service_check_performed"] = True
    payload["server_executes_shell"] = False
    payload["live_execution_performed"] = False
    payload["token_omitted"] = True
    return payload, []


def _fast_service_closure_context(args, client: AgentOpsClient) -> dict:
    manager = args.service_check_manager or "launchd"
    daemon_id = f"agt_worker_daemon_{args.adapter}"
    local_stack_id = f"agt_worker_local_stack_{args.adapter}"
    requested_agent_id = args.service_check_agent_id or client.agent_id or ""
    agent_id = requested_agent_id if requested_agent_id in {daemon_id, local_stack_id} else daemon_id
    service_check_command = args.service_check_command or " ".join(shlex.quote(str(part)) for part in [
        "agentops",
        "worker",
        "service-check",
        "--manager",
        manager,
        "--adapter",
        args.adapter,
        "--agent-id",
        agent_id,
    ])
    service_control_preview = " ".join(shlex.quote(str(part)) for part in [
        "agentops",
        "worker",
        "service-control",
        "--manager",
        manager,
        "--action",
        "restart",
        "--adapter",
        args.adapter,
        "--agent-id",
        agent_id,
    ])
    action_id = f"local_readiness.service_control_preview.{args.adapter}"
    action_signature = hashlib.sha256(
        f"local_readiness.service_control_preview:{args.adapter}:{service_control_preview}:{service_check_command}".encode("utf-8")
    ).hexdigest()
    record_command = " ".join(shlex.quote(str(part)) for part in [
        "agentops",
        "operator",
        "record-action-receipt",
        "--action-command",
        service_control_preview,
        "--verify-command",
        service_check_command,
        "--action-id",
        action_id,
        "--action-signature",
        action_signature,
        "--source",
        action_id,
        "--status",
        args.receipt_status,
        "--result-summary",
        f"{args.adapter} fast service-check readback inspected for canonical service-managed loop closure.",
        "--confirm-record",
    ])
    return {
        "supervision": {
            "operation": "operator_loop_supervision",
            "status": "not_read_fast_service_closure",
            "mode": "fast",
            "token_omitted": True,
        },
        "item": {
            "operation": "operator_loop_supervision_item",
            "adapter": args.adapter,
            "status": "not_read_fast_service_closure",
            "local_deployment": {
                "service_managed_loop": {
                    "status": "fast_record_required",
                    "manager": manager,
                    "receipt_verified": False,
                    "control_readback_attached": False,
                    "service_managed_loop_ready": False,
                    "service_loaded": False,
                    "commands": {
                        "service_control_preview": service_control_preview,
                        "service_check": service_check_command,
                        "record_verified_receipt": record_command,
                        "token_omitted": True,
                    },
                    "token_omitted": True,
                },
                "token_omitted": True,
            },
            "service_closure": {
                "required": True,
                "status": "attention",
                "step": "fast_record_service_check",
                "phase": "RECORD",
                "command": f"agentops operator service-closure --adapter {args.adapter} --fast --run-service-check --confirm-record",
                "fast": True,
                "token_omitted": True,
            },
            "token_omitted": True,
        },
    }


def cmd_operator_service_closure(args, client: AgentOpsClient) -> dict:
    if not hasattr(args, "fast"):
        args.fast = False
    if args.fast:
        fast_context = _fast_service_closure_context(args, client)
        supervision = fast_context["supervision"]
        item = fast_context["item"]
    else:
        supervision = client.get(
            "/api/operator/loop-supervision",
            query={
                "adapter": args.adapter,
                "limit": args.limit,
                "task_id": args.task_id,
                "agent_id": args.agent_id,
                "q": args.query,
                "handoff_mode": args.handoff_mode,
                "full_handoff": "true" if args.full_handoff else None,
                "include_codex": "false",
            },
        )
        items = supervision.get("items") if isinstance(supervision.get("items"), list) else []
        item = next((row for row in items if isinstance(row, dict) and row.get("adapter") == args.adapter), {})
    local_deployment = item.get("local_deployment") if isinstance(item.get("local_deployment"), dict) else {}
    service_managed_loop = local_deployment.get("service_managed_loop") if isinstance(local_deployment.get("service_managed_loop"), dict) else {}
    commands = service_managed_loop.get("commands") if isinstance(service_managed_loop.get("commands"), dict) else {}
    service_closure = item.get("service_closure") if isinstance(item.get("service_closure"), dict) else {}
    record_command = args.receipt_command or commands.get("record_verified_receipt") or service_closure.get("command") or ""
    action_command = _shell_option(record_command, "--action-command", commands.get("service_control_preview") or "")
    verify_command = _shell_option(record_command, "--verify-command", commands.get("service_check") or "")
    action_id = _shell_option(record_command, "--action-id", f"local_readiness.service_control_preview.{args.adapter}")
    action_signature = _shell_option(record_command, "--action-signature", "")
    source = _shell_option(record_command, "--source", action_id)
    before = {
        "step_id": "preview_worker_service_control",
        "status": "preview",
        "adapter": args.adapter,
        "service_control_preview": True,
        "service_closure_step": service_closure.get("step"),
        "service_closure_status": service_closure.get("status"),
        "service_managed_loop_ready": service_managed_loop.get("service_managed_loop_ready") is True,
        "receipt_verified": service_managed_loop.get("receipt_verified") is True,
        "control_readback_attached": service_managed_loop.get("control_readback_attached") is True,
        "token_omitted": True,
    }
    service_check = read_json_argument(args.service_check_json, label="--service-check-json")
    auto_service_check_missing: list[str] = []
    service_check_source = "json_argument" if service_check else None
    if not service_check and args.run_service_check:
        service_check_command = args.service_check_command or verify_command or commands.get("service_check") or ""
        service_check, auto_service_check_missing = _local_service_check_from_command(
            args,
            client,
            service_check_command,
            manager=str(service_managed_loop.get("manager") or "launchd"),
        )
        service_check_source = "local_cli_service_check" if service_check else None
    after = _service_check_readback(args.adapter, service_check, verify_command=verify_command) if service_check else None
    control_readback = {
        "before": before,
        "after": after,
        "self_check": {
            "copy_only": True,
            "server_executes_shell": False,
            "writes_ledger_for_service_control": False,
            "live_execution_performed": False,
            "raw_service_check_omitted": True,
            "local_cli_service_check_performed": bool(args.run_service_check and service_check_source == "local_cli_service_check"),
            "token_omitted": True,
        },
        "token_omitted": True,
    }
    preview = {
        "provider": "agentops-operator",
        "operation": "operator_service_closure",
        "adapter": args.adapter,
        "mode": "fast" if args.fast else "deep",
        "status": "preview",
        "recorded": False,
        "service_closure": {
            "required": service_closure.get("required") is True,
            "status": service_closure.get("status"),
            "step": service_closure.get("step"),
            "phase": service_closure.get("phase"),
            "command": service_closure.get("command"),
            "fast": args.fast,
            "token_omitted": True,
        },
        "service_managed_loop": {
            "status": service_managed_loop.get("status"),
            "manager": service_managed_loop.get("manager"),
            "receipt_verified": service_managed_loop.get("receipt_verified") is True,
            "control_readback_attached": service_managed_loop.get("control_readback_attached") is True,
            "service_managed_loop_ready": service_managed_loop.get("service_managed_loop_ready") is True,
            "service_loaded": service_managed_loop.get("service_loaded") is True,
            "supervision_status": supervision.get("status"),
            "fast": args.fast,
            "token_omitted": True,
        },
        "planned_receipt": {
            "action_command": redact_text(action_command, 500),
            "verify_command": redact_text(verify_command, 500),
            "action_id": action_id,
            "action_signature": action_signature,
            "source": source,
            "status": args.receipt_status,
            "service_check_source": service_check_source,
            "token_omitted": True,
        },
        "service_check": {
            "source": service_check_source,
            "ok": service_check.get("ok") is True if service_check else None,
            "manager": service_check.get("manager") if service_check else None,
            "service_file_exists": ((service_check.get("service_file") or {}).get("exists") is True) if service_check else None,
            "service_loaded": ((service_check.get("service_status") or {}).get("loaded") is True) if service_check else None,
            "local_cli_service_check_performed": bool(args.run_service_check and service_check_source == "local_cli_service_check"),
            "missing": auto_service_check_missing,
            "raw_content_omitted": True,
            "token_omitted": True,
        },
        "control_readback_preview": control_readback,
        "next_actions": [
            commands.get("service_check"),
            "agentops operator service-closure --adapter "
            f"{args.adapter} --service-check-json <service-check.json> --confirm-record",
            "agentops operator service-closure --adapter "
            f"{args.adapter} --run-service-check --confirm-record",
            "agentops operator action-receipts --limit 20",
        ],
        "contract": "preview-only by default; --confirm-record appends receipt and control-readback evidence; --run-service-check performs only the local CLI read-only service-check in this process and never executes service-control, shell, server-side commands, or live adapter work; --fast skips heavy loop-supervision reads and records only adapter-scoped service-check readback",
        "safety": {
            "read_only": True,
            "ledger_mutated": False,
            "live_execution_performed": False,
            "server_executes_shell": False,
            "local_cli_service_check_performed": bool(args.run_service_check and service_check_source == "local_cli_service_check"),
            "loop_supervision_read": not args.fast,
            "token_omitted": True,
        },
        "token_omitted": True,
    }
    if not args.confirm_record:
        return preview
    missing = [
        name
        for name, value in {
            "action_command": action_command,
            "verify_command": verify_command,
            "service_check_json": service_check,
        }.items()
        if not value
    ] + auto_service_check_missing
    if missing:
        return {
            **preview,
            "status": "blocked",
            "ok": False,
            "recorded": False,
            "missing": missing,
            "_exit_code": 2,
        }
    receipt = client.post("/api/operator/action-receipts", {
        "workspace_id": client.workspace_id,
        "actor_id": args.actor_id,
        "action_command": action_command,
        "verify_command": verify_command,
        "action_id": action_id,
        "action_signature": action_signature,
        "source": source,
        "status": args.receipt_status,
        "result_summary": args.result_summary or f"{args.adapter} worker service-control preview inspected and service-check readback recorded.",
    })
    receipt_id = ((receipt.get("receipt") or {}).get("receipt_id") or receipt.get("receipt_id"))
    readback_receipt = None
    if receipt_id:
        readback_receipt = client.post("/api/operator/action-receipts/control-readback", {
            "workspace_id": client.workspace_id,
            "actor_id": args.actor_id,
            "receipt_id": receipt_id,
            "source": f"{source}.control_readback",
            "control_readback": control_readback,
        })
    return {
        **preview,
        "status": "recorded",
        "ok": True,
        "recorded": True,
        "receipt_id": receipt_id,
        "receipt": receipt,
        "control_readback_receipt": readback_receipt,
        "safety": {
            "read_only": False,
            "ledger_mutated": True,
            "live_execution_performed": False,
            "server_executes_shell": False,
            "local_cli_service_check_performed": bool(args.run_service_check and service_check_source == "local_cli_service_check"),
            "loop_supervision_read": not args.fast,
            "token_omitted": True,
        },
    }


def _append_cli_option(command: str, name: str, value: str | None) -> str:
    command = str(command or "").strip()
    if not command or not value or name in command:
        return command
    return f"{command} {name} {shlex.quote(str(value))}"


def _with_requested_manager(command: str, manager: str) -> str:
    command = str(command or "").strip()
    if manager in {"launchd", "systemd"} and command:
        command = command.replace("--manager launchd", f"--manager {manager}")
        command = command.replace("--manager=launchd", f"--manager={manager}")
    return command


def _operator_loop_bootstrap_stale_endpoint(args, client: AgentOpsClient, *, endpoint: str, error: Exception, error_type: str = "stale_server_or_missing_endpoint") -> dict:
    error_text = str(error or "")
    if error_type == "local_mis_endpoint_timeout" or "timed out" in error_text.lower():
        return _operator_loop_bootstrap_fast_packet(
            args,
            client,
            reason="start_check_timeout" if "start-check" in endpoint else "loop_supervision_timeout",
            error=error,
            error_type="local_mis_endpoint_timeout",
        )
    local_probe = local_demo_default_probe(client.base_url)
    current_code_command = f"AGENTOPS_BASE_URL={client.base_url} agentops local readiness --require-current-code"
    retry_timeout = max(int(getattr(args, "request_timeout", 30) or 30), 120)
    retry_command = f"AGENTOPS_BASE_URL={client.base_url} agentops --request-timeout {retry_timeout} operator loop-bootstrap --adapter {args.adapter} --limit {args.limit} --run-service-check"
    repair_commands = [
        current_code_command,
        f"python3 scripts/run_local_stack.py --install-ui",
        retry_command,
    ]
    if not local_probe.get("same_as_target") and local_probe.get("ready"):
        probe_url = str(local_probe.get("base_url") or LOCAL_DEMO_DEFAULT_URL)
        repair_commands.insert(0, f"AGENTOPS_BASE_URL={probe_url} agentops --request-timeout {retry_timeout} operator loop-bootstrap --adapter {args.adapter} --limit {args.limit} --run-service-check")
        repair_commands.append(f"agentops login --base-url {probe_url}")
    reason = "The selected MIS server is reachable but does not expose the loop-bootstrap dependency endpoint, or the saved CLI target points at an older/stopped local server."
    if error_type == "local_mis_endpoint_timeout":
        reason = "The selected MIS server is reachable, but a loop-bootstrap dependency endpoint exceeded the CLI request timeout on the current local ledger."
    return {
        "provider": "agentops-operator",
        "operation": "operator_loop_bootstrap",
        "status": "blocked",
        "ok": False,
        "adapter": args.adapter,
        "workspace_id": client.workspace_id,
        "target_base_url": client.base_url,
        "error_type": error_type,
        "missing_endpoint": endpoint,
        "error": redact_text(str(error), 800),
        "diagnostic": {
            "configured_target": client.base_url,
            "base_url_source": client.sources.get("base_url") or "unknown",
            "config_path": str(CONFIG_PATH),
            "local_demo_probe": local_probe,
            "current_code_command": current_code_command,
            "restart_current_code_command": "python3 scripts/run_local_stack.py --install-ui",
            "repair_commands": repair_commands,
            "retry_with_longer_timeout_command": retry_command,
            "fast_bootstrap_command": f"AGENTOPS_BASE_URL={client.base_url} agentops operator loop-bootstrap --adapter {args.adapter} --limit {args.limit} --fast",
            "reason": reason,
            "token_omitted": True,
        },
        "next_action": repair_commands[0] if repair_commands else current_code_command,
        "bootstrap_steps": [
            {
                "id": "verify_current_code",
                "phase": "READ",
                "status": "blocked",
                "command": current_code_command,
                "confirm_required": False,
                "server_executes_shell": False,
                "token_omitted": True,
            },
            {
                "id": "restart_current_mis",
                "phase": "PREFLIGHT",
                "status": "manual_action_required",
                "command": "python3 scripts/run_local_stack.py --install-ui",
                "confirm_required": True,
                "server_executes_shell": False,
                "token_omitted": True,
            },
            {
                "id": "retry_loop_bootstrap",
                "phase": "READ",
                "status": "waiting",
                "command": retry_command,
                "confirm_required": False,
                "server_executes_shell": False,
                "token_omitted": True,
            },
        ],
        "commands": {
            "current_code_check": current_code_command,
            "restart_current_mis": "python3 scripts/run_local_stack.py --install-ui",
            "retry_loop_bootstrap": retry_command,
            "fast_loop_bootstrap": f"AGENTOPS_BASE_URL={client.base_url} agentops operator loop-bootstrap --adapter {args.adapter} --limit {args.limit} --fast",
        },
        "contract": "blocked bootstrap recovery packet for Hermes/OpenClaw when the selected local MIS is stale, slow, or lacks loop-bootstrap dependency endpoints; it never executes shell, service-control, service-check, live adapters, or ledger writes",
        "safety": {
            "read_only": True,
            "ledger_mutated": False,
            "live_execution_performed": False,
            "server_executes_shell": False,
            "local_cli_service_check_performed": False,
            "token_omitted": True,
        },
        "token_omitted": True,
        "live_execution_performed": False,
        "_exit_code": 2,
    }


def _operator_loop_bootstrap_minimal_start_check(args, client: AgentOpsClient, *, reason: str) -> dict:
    current_code_command = f"AGENTOPS_BASE_URL={client.base_url} agentops local readiness --require-current-code"
    start_check_command = f"AGENTOPS_BASE_URL={client.base_url} agentops operator start-check --adapter {args.adapter} --limit {args.limit}"
    return {
        "provider": "agentops-operator",
        "operation": "operator_start_check",
        "status": "blocked",
        "adapter": args.adapter,
        "workspace_id": client.workspace_id,
        "task_id": args.task_id,
        "agent_id": args.agent_id or client.agent_id or None,
        "summary": {
            "mode": "fast_bootstrap_minimal",
            "reason": reason,
            "current_code_ok": False,
            "can_confirm_bounded_loop": False,
        },
        "local_loop_admission_packet": {
            "operation": "operator_local_loop_admission_packet",
            "status": "blocked",
            "adapter": args.adapter,
            "admission": {
                "current_code_ok": False,
                "can_confirm_bounded_loop": False,
                "reason": reason,
                "token_omitted": True,
            },
            "commands": {
                "start_check": start_check_command,
                "current_code_check": current_code_command,
                "worker_preflight": f"AGENTOPS_BASE_URL={client.base_url} agentops worker preflight --adapter {args.adapter}",
            },
            "local_deployment": {},
            "safety": {
                "read_only": True,
                "ledger_mutated": False,
                "live_execution_performed": False,
                "server_executes_shell": False,
                "token_omitted": True,
            },
            "token_omitted": True,
        },
        "acceptance_packet": {
            "operation": "operator_local_loop_acceptance_packet",
            "status": "blocked",
            "decision": {
                "current_code_ok": False,
                "can_confirm_bounded_loop": False,
                "live_dispatch_allowed": False,
                "reason": reason,
            },
            "commands": {
                "start_check": start_check_command,
                "current_code_check": current_code_command,
                "loop_driver_preview": f"AGENTOPS_BASE_URL={client.base_url} agentops operator loop-driver --adapter {args.adapter} --max-steps {args.max_steps} --limit {args.limit}",
                "loop_driver_confirm": f"AGENTOPS_BASE_URL={client.base_url} agentops operator loop-driver --adapter {args.adapter} --max-steps {args.max_steps} --limit {args.limit} --confirm-loop",
            },
            "safety": {
                "read_only": True,
                "ledger_mutated": False,
                "live_execution_performed": False,
                "server_executes_shell": False,
                "token_omitted": True,
            },
            "token_omitted": True,
        },
        "loop_driver_entry": {
            "operation": "operator_start_check_loop_driver_entry",
            "status": "blocked",
            "commands": {
                "preview": f"AGENTOPS_BASE_URL={client.base_url} agentops operator loop-driver --adapter {args.adapter} --max-steps {args.max_steps} --limit {args.limit}",
                "confirm_loop": f"AGENTOPS_BASE_URL={client.base_url} agentops operator loop-driver --adapter {args.adapter} --max-steps {args.max_steps} --limit {args.limit} --confirm-loop",
                "review_queue": f"AGENTOPS_BASE_URL={client.base_url} agentops review queue --limit 20",
            },
            "safety": {
                "read_only": True,
                "ledger_mutated": False,
                "live_execution_performed": False,
                "server_executes_shell": False,
                "token_omitted": True,
            },
            "token_omitted": True,
        },
        "safety": {
            "read_only": True,
            "ledger_mutated": False,
            "live_execution_performed": False,
            "server_executes_shell": False,
            "token_omitted": True,
        },
        "token_omitted": True,
        "live_execution_performed": False,
    }


def _operator_loop_bootstrap_minimal_supervision(args) -> dict:
    return {
        "operation": "operator_loop_supervision_item",
        "status": "not_read_fast_bootstrap",
        "adapter": args.adapter,
        "primary_next_action": {
            "id": "read_deep_loop_supervision",
            "phase": "VERIFY",
            "command": f"agentops operator loop-supervision --adapter {args.adapter} --limit {args.limit} --no-codex",
            "server_executes_shell": False,
            "token_omitted": True,
        },
        "service_closure": {
            "required": False,
            "status": "unknown_until_supervision",
            "step": "read_loop_supervision",
            "command": f"agentops operator loop-supervision --adapter {args.adapter} --limit {args.limit} --no-codex",
            "token_omitted": True,
        },
        "token_omitted": True,
    }


def _operator_loop_bootstrap_fast_packet(
    args,
    client: AgentOpsClient,
    *,
    reason: str,
    error: Exception | None = None,
    error_type: str | None = None,
) -> dict:
    start_check = _operator_loop_bootstrap_minimal_start_check(args, client, reason=reason)
    supervision_item = _operator_loop_bootstrap_minimal_supervision(args)
    commands = _bootstrap_command_map(start_check, supervision_item, args)
    service_check_result = None
    service_check_missing: list[str] = []
    if args.run_service_check:
        check_args = argparse.Namespace(
            adapter=args.adapter,
            service_check_manager=args.manager,
            service_check_agent_id=args.service_check_agent_id or "",
            service_path=args.service_path or "",
            service_label=args.service_label or "",
            api_key_placeholder=args.api_key_placeholder,
            service_check_timeout=args.service_check_timeout,
        )
        service_check_result, service_check_missing = _local_service_check_from_command(
            check_args,
            client,
            commands.get("service_check") or "",
            manager=args.manager,
        )
    status = "blocked" if error_type else "attention"
    if not error_type and service_check_result and service_check_result.get("ok") is not True:
        status = "attention"
    bootstrap_steps = [
        {
            "id": "fast_bootstrap_packet",
            "phase": "READ",
            "status": "ready",
            "command": commands["loop_bootstrap_cli"] + " --fast" if "loop_bootstrap_cli" in commands else f"agentops operator loop-bootstrap --adapter {args.adapter} --limit {args.limit} --fast",
            "confirm_required": False,
            "server_executes_shell": False,
            "token_omitted": True,
        },
        {
            "id": "verify_current_code",
            "phase": "READ",
            "status": "blocked",
            "command": commands["current_code_check"],
            "confirm_required": False,
            "server_executes_shell": False,
            "token_omitted": True,
        },
        {
            "id": "preview_service_install",
            "phase": "PREFLIGHT",
            "status": "ready",
            "command": commands["service_install_preview"],
            "confirm_required": False,
            "server_executes_shell": False,
            "token_omitted": True,
        },
        {
            "id": "confirm_service_install",
            "phase": "PREFLIGHT",
            "status": "manual_confirm_required",
            "command": commands["service_install_confirm"],
            "confirm_required": True,
            "loads_service": False,
            "server_executes_shell": False,
            "token_omitted": True,
        },
        {
            "id": "run_service_check",
            "phase": "VERIFY",
            "status": "checked" if service_check_result else "ready",
            "command": commands["service_check"],
            "confirm_required": False,
            "local_cli_service_check_performed": bool(service_check_result),
            "ok": service_check_result.get("ok") if service_check_result else None,
            "missing": service_check_missing,
            "server_executes_shell": False,
            "token_omitted": True,
        },
        {
            "id": "read_deep_loop_supervision",
            "phase": "VERIFY",
            "status": "waiting",
            "command": commands["loop_supervision"],
            "confirm_required": False,
            "server_executes_shell": False,
            "token_omitted": True,
        },
        {
            "id": "confirm_bounded_loop",
            "phase": "EXECUTE",
            "status": "blocked_until_start_check_and_supervision",
            "command": commands["loop_driver_auto_service_closure"],
            "confirm_required": True,
            "uses_auto_service_closure": True,
            "server_executes_shell": False,
            "token_omitted": True,
        },
    ]
    payload = {
        "provider": "agentops-operator",
        "operation": "operator_loop_bootstrap",
        "status": status,
        "mode": "fast",
        "adapter": args.adapter,
        "workspace_id": client.workspace_id,
        "task_id": args.task_id,
        "agent_id": args.agent_id or client.agent_id or None,
        "next_action": commands["current_code_check"],
        "summary": {
            "mode": "fast",
            "reason": reason,
            "start_check_status": "not_read_fast_bootstrap",
            "supervision_status": "not_read_fast_bootstrap",
            "current_code_ok": False,
            "service_closure_required": False,
            "local_cli_service_check_performed": bool(service_check_result),
            "can_confirm_bounded_loop": False,
            "deep_verification_required": True,
        },
        "bootstrap_steps": bootstrap_steps,
        "commands": commands,
        "service_check": {
            "performed": bool(service_check_result),
            "ok": service_check_result.get("ok") if service_check_result else None,
            "manager": service_check_result.get("manager") if service_check_result else args.manager,
            "service_file_exists": ((service_check_result.get("service_file") or {}).get("exists") is True) if service_check_result else None,
            "service_loaded": ((service_check_result.get("service_status") or {}).get("loaded") is True) if service_check_result else None,
            "missing": service_check_missing,
            "raw_content_omitted": True,
            "token_omitted": True,
        },
        "service_closure": supervision_item.get("service_closure"),
        "local_loop_admission_packet": start_check.get("local_loop_admission_packet"),
        "supervision": {
            "status": supervision_item.get("status"),
            "primary_next_action": supervision_item.get("primary_next_action"),
            "service_closure": supervision_item.get("service_closure"),
            "token_omitted": True,
        },
        "contract": "fast read-only local loop bootstrap packet for Hermes/OpenClaw; it gives copy-only service install/check/closure and bounded loop commands without waiting for heavy start-check or loop-supervision, but confirm-loop remains blocked until current-code and deep supervision readback pass",
        "safety": {
            "read_only": True,
            "ledger_mutated": False,
            "live_execution_performed": False,
            "server_executes_shell": False,
            "local_cli_service_check_performed": bool(service_check_result),
            "token_omitted": True,
        },
        "token_omitted": True,
        "live_execution_performed": False,
    }
    if error_type:
        payload.update({
            "error_type": error_type,
            "error": redact_text(str(error), 800) if error else None,
            "diagnostic": {
                "reason": reason,
                "fallback": "fast_bootstrap_packet",
                "deep_start_check_command": commands["start_check"],
                "deep_loop_supervision_command": commands["loop_supervision"],
                "token_omitted": True,
            },
            "_exit_code": 2,
        })
    return payload


def _bootstrap_command_map(start_check: dict, supervision_item: dict, args) -> dict:
    admission = start_check.get("local_loop_admission_packet") if isinstance(start_check.get("local_loop_admission_packet"), dict) else {}
    deployment = admission.get("local_deployment") if isinstance(admission.get("local_deployment"), dict) else {}
    service_install = deployment.get("service_install") if isinstance(deployment.get("service_install"), dict) else {}
    manager_options = service_install.get("manager_options") if isinstance(service_install.get("manager_options"), dict) else {}
    manager_install = manager_options.get(args.manager) if isinstance(manager_options.get(args.manager), dict) else {}
    service_managed_loop = deployment.get("service_managed_loop") if isinstance(deployment.get("service_managed_loop"), dict) else {}
    managed_execution = deployment.get("managed_execution_path") if isinstance(deployment.get("managed_execution_path"), dict) else {}
    admission_commands = admission.get("commands") if isinstance(admission.get("commands"), dict) else {}
    managed_commands = managed_execution.get("commands") if isinstance(managed_execution.get("commands"), dict) else {}
    loop_driver_entry = start_check.get("loop_driver_entry") if isinstance(start_check.get("loop_driver_entry"), dict) else {}
    loop_commands = loop_driver_entry.get("commands") if isinstance(loop_driver_entry.get("commands"), dict) else {}
    service_closure = supervision_item.get("service_closure") if isinstance(supervision_item.get("service_closure"), dict) else {}
    service_check = (
        admission_commands.get("service_check")
        or managed_commands.get("service_check")
        or (service_managed_loop.get("commands") or {}).get("service_check")
        or f"agentops worker service-check --manager {args.manager} --adapter {args.adapter} --agent-id agt_worker_daemon_{args.adapter}"
    )
    service_install_preview = (
        manager_install.get("preview_command")
        or service_install.get("preview_command")
        or admission_commands.get("service_install_preview")
        or f"agentops worker service-install --manager {args.manager} --adapter {args.adapter} --agent-id agt_worker_daemon_{args.adapter}"
    )
    service_install_confirm = (
        manager_install.get("confirm_command")
        or service_install.get("confirm_command")
        or admission_commands.get("service_install_confirm")
        or f"{service_install_preview} --confirm-install"
    )
    service_control_load = (
        managed_commands.get("service_control_load_confirm")
        or (service_managed_loop.get("commands") or {}).get("service_control_load_confirm")
        or f"agentops worker service-control --manager {args.manager} --action load --adapter {args.adapter} --agent-id agt_worker_daemon_{args.adapter} --confirm-control"
    )
    fast_service_closure = bool(
        getattr(args, "fast", False)
        or start_check.get("mode") == "fast"
        or ((start_check.get("summary") or {}).get("mode") == "fast_bootstrap_minimal")
    )
    service_closure_record = " ".join(shlex.quote(str(part)) for part in [
        "agentops",
        "operator",
        "service-closure",
        "--adapter",
        args.adapter,
        *(["--fast"] if fast_service_closure else []),
        "--run-service-check",
        "--confirm-record",
    ])
    loop_confirm = loop_commands.get("confirm_loop") or f"agentops operator loop-driver --adapter {args.adapter} --max-steps {args.max_steps} --limit {args.limit} --confirm-loop"
    if "--auto-service-closure" not in loop_confirm:
        loop_confirm = f"{loop_confirm} --auto-service-closure"
    service_check = _with_requested_manager(service_check, args.manager)
    service_install_preview = _with_requested_manager(service_install_preview, args.manager)
    service_install_confirm = _with_requested_manager(service_install_confirm, args.manager)
    service_control_load = _with_requested_manager(service_control_load, args.manager)
    if args.service_path:
        service_check = _append_cli_option(service_check, "--service-path", args.service_path)
        service_install_preview = _append_cli_option(service_install_preview, "--service-path", args.service_path)
        service_install_confirm = _append_cli_option(service_install_confirm, "--service-path", args.service_path)
        service_control_load = _append_cli_option(service_control_load, "--service-path", args.service_path)
        service_closure_record = _append_cli_option(service_closure_record, "--service-path", args.service_path)
        loop_confirm = _append_cli_option(loop_confirm, "--service-path", args.service_path)
    if args.service_label:
        service_check = _append_cli_option(service_check, "--label", args.service_label)
        service_install_preview = _append_cli_option(service_install_preview, "--label", args.service_label)
        service_install_confirm = _append_cli_option(service_install_confirm, "--label", args.service_label)
        service_control_load = _append_cli_option(service_control_load, "--label", args.service_label)
        service_closure_record = _append_cli_option(service_closure_record, "--service-label", args.service_label)
        loop_confirm = _append_cli_option(loop_confirm, "--service-label", args.service_label)
    return {
        "start_check": f"agentops operator start-check --adapter {args.adapter} --limit {args.limit}",
        "current_code_check": ((start_check.get("local_loop_admission_packet") or {}).get("commands") or {}).get("current_code_check")
        or "agentops local readiness --require-current-code",
        "service_install_preview": service_install_preview,
        "service_install_confirm": service_install_confirm,
        "service_check": service_check,
        "service_closure_record": service_closure_record,
        "service_control_load_confirm": service_control_load,
        "loop_driver_auto_service_closure": loop_confirm,
        "action_receipts": "agentops operator action-receipts --limit 20",
        "loop_supervision": f"agentops operator loop-supervision --adapter {args.adapter} --limit {args.limit} --no-codex",
        "service_closure_recommended": service_closure.get("command"),
    }


def cmd_operator_loop_bootstrap(args, client: AgentOpsClient) -> dict:
    if args.fast:
        return _operator_loop_bootstrap_fast_packet(args, client, reason="fast_requested")
    try:
        start_check = client.get(
            "/api/operator/start-check",
            query={
                "adapter": args.adapter,
                "limit": args.limit,
                "loop_id": args.loop_id,
                "task_id": args.task_id,
                "agent_id": args.agent_id,
                "q": args.query,
                "handoff_mode": args.handoff_mode,
                "full_handoff": "true" if args.full_handoff else None,
            },
        )
    except RuntimeError as exc:
        if "/api/operator/start-check" in str(exc) and ("404" in str(exc) or "unknown endpoint" in str(exc)):
            return _operator_loop_bootstrap_stale_endpoint(args, client, endpoint="/api/operator/start-check", error=exc)
        if "/api/operator/start-check" in str(exc) and "timed out" in str(exc).lower():
            return _operator_loop_bootstrap_fast_packet(args, client, reason="start_check_timeout", error=exc, error_type="local_mis_endpoint_timeout")
        raise
    try:
        supervision = client.get(
            "/api/operator/loop-supervision",
            query={
                "adapter": args.adapter,
                "limit": args.limit,
                "task_id": args.task_id,
                "agent_id": args.agent_id,
                "q": args.query,
                "handoff_mode": args.handoff_mode,
                "full_handoff": "true" if args.full_handoff else None,
                "include_codex": "false",
            },
        )
    except RuntimeError as exc:
        if "/api/operator/loop-supervision" in str(exc) and ("404" in str(exc) or "unknown endpoint" in str(exc)):
            return _operator_loop_bootstrap_stale_endpoint(args, client, endpoint="/api/operator/loop-supervision", error=exc)
        if "/api/operator/loop-supervision" in str(exc) and "timed out" in str(exc).lower():
            return _operator_loop_bootstrap_fast_packet(args, client, reason="loop_supervision_timeout", error=exc, error_type="local_mis_endpoint_timeout")
        raise
    items = supervision.get("items") if isinstance(supervision.get("items"), list) else []
    supervision_item = next((row for row in items if isinstance(row, dict) and row.get("adapter") == args.adapter), {})
    admission = start_check.get("local_loop_admission_packet") if isinstance(start_check.get("local_loop_admission_packet"), dict) else {}
    admission_state = admission.get("admission") if isinstance(admission.get("admission"), dict) else {}
    deployment = admission.get("local_deployment") if isinstance(admission.get("local_deployment"), dict) else {}
    service_managed_loop = deployment.get("service_managed_loop") if isinstance(deployment.get("service_managed_loop"), dict) else {}
    service_closure = supervision_item.get("service_closure") if isinstance(supervision_item.get("service_closure"), dict) else {}
    commands = _bootstrap_command_map(start_check, supervision_item, args)
    service_check_result = None
    service_check_missing: list[str] = []
    if args.run_service_check:
        check_args = argparse.Namespace(
            adapter=args.adapter,
            service_check_manager=args.manager,
            service_check_agent_id=args.service_check_agent_id or "",
            service_path=args.service_path or "",
            service_label=args.service_label or "",
            api_key_placeholder=args.api_key_placeholder,
            service_check_timeout=args.service_check_timeout,
        )
        service_check_result, service_check_missing = _local_service_check_from_command(
            check_args,
            client,
            commands.get("service_check") or "",
            manager=args.manager,
        )
    current_code_ok = admission_state.get("current_code_ok") is True
    service_closure_required = service_closure.get("required") is True
    service_loaded = (
        ((service_check_result or {}).get("service_status") or {}).get("loaded") is True
        if service_check_result
        else service_managed_loop.get("service_loaded") is True
    )
    if start_check.get("status") == "blocked" or not current_code_ok:
        status = "blocked"
        next_action = commands["current_code_check"]
    elif service_check_result and service_check_result.get("ok") is not True:
        status = "attention"
        next_action = commands["service_install_preview"]
    elif service_closure_required:
        status = "attention"
        next_action = commands["service_closure_record"]
    elif service_managed_loop.get("service_active_loop_ready") is not True and not service_loaded:
        status = "attention"
        next_action = commands["service_check"]
    else:
        status = "ready"
        next_action = commands["loop_driver_auto_service_closure"]
    bootstrap_steps = [
        {
            "id": "read_start_check",
            "phase": "READ",
            "status": start_check.get("status"),
            "command": commands["start_check"],
            "confirm_required": False,
            "token_omitted": True,
        },
        {
            "id": "verify_current_code",
            "phase": "READ",
            "status": "pass" if current_code_ok else "blocked",
            "command": commands["current_code_check"],
            "confirm_required": False,
            "token_omitted": True,
        },
        {
            "id": "preview_service_install",
            "phase": "PREFLIGHT",
            "status": "ready",
            "command": commands["service_install_preview"],
            "confirm_required": False,
            "server_executes_shell": False,
            "token_omitted": True,
        },
        {
            "id": "confirm_service_install",
            "phase": "PREFLIGHT",
            "status": "manual_confirm_required",
            "command": commands["service_install_confirm"],
            "confirm_required": True,
            "loads_service": False,
            "server_executes_shell": False,
            "token_omitted": True,
        },
        {
            "id": "run_service_check",
            "phase": "VERIFY",
            "status": "checked" if service_check_result else "ready",
            "command": commands["service_check"],
            "confirm_required": False,
            "local_cli_service_check_performed": bool(service_check_result),
            "ok": service_check_result.get("ok") if service_check_result else None,
            "missing": service_check_missing,
            "server_executes_shell": False,
            "token_omitted": True,
        },
        {
            "id": "record_service_closure",
            "phase": "RECORD",
            "status": "attention" if service_closure_required else "ready",
            "command": commands["service_closure_record"],
            "confirm_required": True,
            "service_closure_step": service_closure.get("step"),
            "server_executes_shell": False,
            "token_omitted": True,
        },
        {
            "id": "confirm_service_activation",
            "phase": "PREFLIGHT",
            "status": "manual_confirm_required" if service_closure.get("step") == "confirm_service_control_load" else "not_required",
            "command": commands["service_control_load_confirm"],
            "confirm_required": True,
            "server_executes_shell": False,
            "token_omitted": True,
        },
        {
            "id": "confirm_bounded_loop",
            "phase": "EXECUTE",
            "status": "ready" if status == "ready" else "waiting",
            "command": commands["loop_driver_auto_service_closure"],
            "confirm_required": True,
            "uses_auto_service_closure": True,
            "server_executes_shell": False,
            "token_omitted": True,
        },
    ]
    return {
        "provider": "agentops-operator",
        "operation": "operator_loop_bootstrap",
        "status": status,
        "adapter": args.adapter,
        "workspace_id": client.workspace_id,
        "task_id": args.task_id,
        "agent_id": args.agent_id or client.agent_id or None,
        "next_action": next_action,
        "summary": {
            "start_check_status": start_check.get("status"),
            "current_code_ok": current_code_ok,
            "service_closure_required": service_closure_required,
            "service_closure_step": service_closure.get("step"),
            "service_managed_loop_ready": service_managed_loop.get("service_managed_loop_ready") is True,
            "service_active_loop_ready": service_managed_loop.get("service_active_loop_ready") is True,
            "service_loaded": service_loaded,
            "local_cli_service_check_performed": bool(service_check_result),
            "can_confirm_bounded_loop": ((start_check.get("acceptance_packet") or {}).get("decision") or {}).get("can_confirm_bounded_loop") is True,
        },
        "bootstrap_steps": bootstrap_steps,
        "commands": commands,
        "service_check": {
            "performed": bool(service_check_result),
            "ok": service_check_result.get("ok") if service_check_result else None,
            "manager": service_check_result.get("manager") if service_check_result else args.manager,
            "service_file_exists": ((service_check_result.get("service_file") or {}).get("exists") is True) if service_check_result else None,
            "service_loaded": ((service_check_result.get("service_status") or {}).get("loaded") is True) if service_check_result else None,
            "missing": service_check_missing,
            "raw_content_omitted": True,
            "token_omitted": True,
        },
        "service_closure": service_closure,
        "local_loop_admission_packet": admission,
        "supervision": {
            "status": supervision_item.get("status"),
            "primary_next_action": supervision_item.get("primary_next_action"),
            "service_closure": service_closure,
            "token_omitted": True,
        },
        "contract": "read-only local loop bootstrap packet for Hermes/OpenClaw; it orders service install/check/closure/activation and bounded loop-driver commands, optionally performs only local read-only service-check, and never mutates ledgers, loads services, executes server shell, or runs live adapters",
        "safety": {
            "read_only": True,
            "ledger_mutated": False,
            "live_execution_performed": False,
            "server_executes_shell": False,
            "local_cli_service_check_performed": bool(service_check_result),
            "token_omitted": True,
        },
        "token_omitted": True,
        "live_execution_performed": False,
    }


def compact_loop_supervision_work_packets(payload: dict) -> dict:
    work_packets = [
        item
        for item in (payload.get("work_packets") if isinstance(payload.get("work_packets"), list) else [])
        if isinstance(item, dict)
    ]
    if not work_packets:
        work_packets = [
            item.get("agent_work_packet")
            for item in (payload.get("items") if isinstance(payload.get("items"), list) else [])
            if isinstance(item, dict) and isinstance(item.get("agent_work_packet"), dict)
        ]
    return {
        "provider": payload.get("provider", "agentops-operator"),
        "operation": "operator_loop_work_packet_bundle",
        "schema_version": "agent_work_packet_bundle_v1",
        "source_operation": payload.get("operation", "operator_loop_supervision"),
        "status": payload.get("status"),
        "workspace_id": payload.get("workspace_id"),
        "adapters": payload.get("adapters") or [item.get("adapter") for item in work_packets],
        "summary": {
            **(payload.get("summary") if isinstance(payload.get("summary"), dict) else {}),
            "work_packets": len(work_packets),
            "packet_hashes": [item.get("packet_hash") for item in work_packets if item.get("packet_hash")],
        },
        "work_packets": work_packets,
        "next_actions": payload.get("next_actions") or [],
        "contract": "compact machine-consumable loop work-packet bundle for Hermes/OpenClaw/Codex; read-only and copy-only, with live execution still gated by local confirmation, Agent Plan, retrieval, approvals, receipts, evidence and memory review",
        "safety": payload.get("safety") if isinstance(payload.get("safety"), dict) else {
            "read_only": True,
            "ledger_mutated": False,
            "live_execution_performed": False,
            "server_executes_shell": False,
            "token_omitted": True,
        },
        "token_omitted": True,
        "live_execution_performed": False,
    }


def loop_work_packet_decision_from_bundle(bundle: dict) -> dict:
    work_packets = [
        item
        for item in (bundle.get("work_packets") if isinstance(bundle.get("work_packets"), list) else [])
        if isinstance(item, dict)
    ]
    decisions = []
    for packet in work_packets:
        action = packet.get("primary_next_action") if isinstance(packet.get("primary_next_action"), dict) else {}
        safety = packet.get("safety") if isinstance(packet.get("safety"), dict) else {}
        contract = packet.get("evidence_contract") if isinstance(packet.get("evidence_contract"), dict) else {}
        service_contract = contract.get("service_managed_loop") if isinstance(contract.get("service_managed_loop"), dict) else {}
        research_consumption = contract.get("research_lab_consumption") if isinstance(contract.get("research_lab_consumption"), dict) else {}
        command = str(action.get("command") or packet.get("recommended_next") or "")
        phase = str(action.get("phase") or "READ").upper()
        blockers = [str(item) for item in (action.get("blockers") or packet.get("blockers") or []) if item]
        attention = [str(item) for item in (packet.get("attention") or []) if item]
        confirm_required = bool(action.get("confirm_required") or "--confirm-" in command)
        safety_blocked = safety.get("server_executes_shell") is True or safety.get("live_execution_performed") is True
        if safety_blocked:
            decision = "stop"
            reason = "safety_boundary_failed"
        elif blockers:
            decision = "blocked"
            reason = "packet_blockers_present"
        elif contract.get("task_intake_auto_plan_required") is True:
            decision = "plan_first"
            reason = "task_intake_agent_plan_required"
        elif service_contract.get("required") is True or service_contract.get("status") == "attention":
            decision = "service_closure_first"
            reason = "service_receipt_or_readback_required"
        elif research_consumption and (
            contract.get("research_lab_consumption_required") is True
            or research_consumption.get("required") is True
        ) and research_consumption.get("consumed") is not True:
            decision = "record_research_consumption_first"
            reason = "research_lab_packet_consumption_missing"
        elif phase == "RECORD" or packet.get("status") == "record_first":
            decision = "record_first"
            reason = "record_before_execute"
        elif phase == "VERIFY":
            decision = "review_first"
            reason = "verification_or_quality_attention"
        elif confirm_required:
            decision = "confirm_ready"
            reason = "explicit_local_confirmation_required"
        elif action.get("safe_to_auto_continue") is True:
            decision = "safe_read_or_preview"
            reason = "read_only_or_preview_action"
        elif command:
            decision = "preview_first"
            reason = "copy_command_for_local_preview"
        else:
            decision = "stop"
            reason = "no_recommended_command"
        decisions.append({
            "adapter": packet.get("adapter"),
            "packet_hash": packet.get("packet_hash"),
            "packet_status": packet.get("status"),
            "decision": decision,
            "reason": reason,
            "phase": phase,
            "command": command,
            "verify_command": action.get("verify_command"),
            "confirm_required": confirm_required,
            "receipt_required": bool(action.get("receipt_required")),
            "safe_to_auto_continue": action.get("safe_to_auto_continue") is True and decision == "safe_read_or_preview",
            "requires_human_before_effect": bool(action.get("requires_human_before_effect") or confirm_required or decision not in {"safe_read_or_preview", "preview_first"}),
            "blockers": blockers,
            "attention": attention,
            "safety": {
                "read_only": safety.get("read_only") is True,
                "ledger_mutated": safety.get("ledger_mutated") is True,
                "live_execution_performed": safety.get("live_execution_performed") is True,
                "server_executes_shell": safety.get("server_executes_shell") is True,
                "copy_only": safety.get("copy_only") is True,
                "token_omitted": True,
            },
            "policy": {
                "server_may_execute": False,
                "agent_may_copy_command": bool(command) and not safety_blocked,
                "agent_may_execute_without_local_confirmation": action.get("safe_to_auto_continue") is True and not confirm_required and decision == "safe_read_or_preview",
                "must_record_receipt_after_confirm": bool(action.get("receipt_required")),
                "raw_prompt_response_omitted": True,
                "token_omitted": True,
            },
            "token_omitted": True,
        })
    decision_counts = {}
    for item in decisions:
        key = str(item.get("decision") or "unknown")
        decision_counts[key] = decision_counts.get(key, 0) + 1
    return {
        "provider": bundle.get("provider", "agentops-operator"),
        "operation": "operator_loop_work_packet_decision",
        "schema_version": "agent_work_packet_decision_v1",
        "source_operation": bundle.get("operation") or "operator_loop_work_packet_bundle",
        "source_schema_version": bundle.get("schema_version"),
        "status": bundle.get("status"),
        "workspace_id": bundle.get("workspace_id"),
        "adapters": bundle.get("adapters") or [item.get("adapter") for item in decisions],
        "summary": {
            **(bundle.get("summary") if isinstance(bundle.get("summary"), dict) else {}),
            "decisions": len(decisions),
            "decision_counts": decision_counts,
            "ready_to_confirm": decision_counts.get("confirm_ready", 0),
            "requires_human_before_effect": sum(1 for item in decisions if item.get("requires_human_before_effect") is True),
            "safe_to_auto_continue": sum(1 for item in decisions if item.get("safe_to_auto_continue") is True),
            "server_may_execute": False,
        },
        "decisions": decisions,
        "contract": "read-only work-packet consumption decision for Hermes/OpenClaw/Codex local loop callers; it classifies the next copyable command but never executes shell, mutates ledgers, starts live adapters, approves work, or stores raw prompt/response content",
        "safety": {
            "read_only": True,
            "ledger_mutated": False,
            "live_execution_performed": False,
            "server_executes_shell": False,
            "raw_prompt_omitted": True,
            "raw_response_omitted": True,
            "raw_content_omitted": True,
            "token_omitted": True,
        },
        "token_omitted": True,
        "live_execution_performed": False,
    }


def compact_loop_launch_packet(payload: dict, *, adapter: str, local_readiness: dict | None = None) -> dict:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    control = payload.get("control_summary") if isinstance(payload.get("control_summary"), dict) else {}
    recommended = control.get("recommended_step") if isinstance(control.get("recommended_step"), dict) else {}
    safety = payload.get("safety") if isinstance(payload.get("safety"), dict) else {}
    evaluation = payload.get("evaluation_contract") if isinstance(payload.get("evaluation_contract"), dict) else {}
    audit = payload.get("audit_contract") if isinstance(payload.get("audit_contract"), dict) else {}
    agent_plan_draft = payload.get("agent_plan_draft") if isinstance(payload.get("agent_plan_draft"), dict) else {}
    sources = payload.get("sources") if isinstance(payload.get("sources"), dict) else {}
    workflow_recovery = payload.get("workflow_job_recovery") if isinstance(payload.get("workflow_job_recovery"), dict) else {}
    if not workflow_recovery:
        workflow_recovery = sources.get("workflow_job_recovery") if isinstance(sources.get("workflow_job_recovery"), dict) else {}
    if not workflow_recovery:
        handoff = sources.get("handoff") if isinstance(sources.get("handoff"), dict) else {}
        handoff_work_order = handoff.get("work_order") if isinstance(handoff.get("work_order"), dict) else {}
        workflow_recovery = handoff_work_order.get("workflow_job_recovery") if isinstance(handoff_work_order.get("workflow_job_recovery"), dict) else {}
    workflow_recovery_summary = workflow_recovery.get("summary") if isinstance(workflow_recovery.get("summary"), dict) else {}
    workflow_recovery_items = workflow_recovery.get("items") if isinstance(workflow_recovery.get("items"), list) else []
    workflow_recovery_commands = []
    for command in [
        *(workflow_recovery.get("next_actions") if isinstance(workflow_recovery.get("next_actions"), list) else []),
        *(workflow_recovery.get("commands") if isinstance(workflow_recovery.get("commands"), list) else []),
    ]:
        command = str(command or "").strip()
        if command and command not in workflow_recovery_commands:
            workflow_recovery_commands.append(command)
    chain = payload.get("execution_chain") if isinstance(payload.get("execution_chain"), list) else []
    compact_chain = []
    for item in chain:
        if not isinstance(item, dict):
            continue
        compact_chain.append({
            "step_id": item.get("step_id"),
            "phase": item.get("phase"),
            "label": item.get("label"),
            "status": item.get("step_status"),
            "next_safe_command": item.get("next_safe_command") or item.get("command"),
            "verify_command": item.get("verify_command"),
            "receipt_command": item.get("receipt_command"),
            "confirm_required": bool(item.get("confirm_required")),
            "receipt_required": bool(item.get("receipt_required")),
            "source": item.get("source"),
            "token_omitted": item.get("token_omitted", True),
        })
    adapter_command = f"agentops worker preflight --adapter {adapter}"
    live_run_command = (
        "agentops workflow run-task "
        f"--adapter {adapter} "
        f"--confirm-run "
        f"--worker-agent-id <{adapter}_agent_id> "
        "--title '<task title>' "
        "--description '<task description>'"
    ) if adapter in {"hermes", "openclaw"} else (
        "agentops workflow run-task "
        "--adapter mock "
        "--worker-agent-id <mock_agent_id> "
        "--title '<task title>' "
        "--description '<task description>'"
    )
    readback_commands = [
        "agentops task get --task-id <task_id>",
        "agentops run get --run-id <run_id>",
        "agentops plan-evidence list --run-id <run_id>",
        "agentops operator loop-audit --limit 20",
        "agentops operator action-receipts --limit 20",
    ]
    local_readiness = local_readiness if isinstance(local_readiness, dict) else {}
    local_run_path = compact_start_check_local_run_path(local_readiness, adapter=adapter)
    compact_local_steps = local_run_path.get("steps") if isinstance(local_run_path.get("steps"), list) else []
    service_step = local_run_path.get("service_control_preview") if isinstance(local_run_path.get("service_control_preview"), dict) else {}
    current_code_gate = local_run_path.get("current_code_gate") if isinstance(local_run_path.get("current_code_gate"), dict) else {}
    local_run_path_commands = local_run_path.get("commands") if isinstance(local_run_path.get("commands"), list) else []
    return {
        "provider": payload.get("provider", "agentops-operator"),
        "operation": "operator_loop_launch_brief",
        "source_operation": payload.get("operation", "operator_loop_launch_packet"),
        "status": payload.get("status", "unknown"),
        "workspace_id": payload.get("workspace_id"),
        "task_id": payload.get("task_id"),
        "agent_id": payload.get("agent_id"),
        "adapter": adapter,
        "method": payload.get("method"),
        "summary": {
            "handoff_mode": summary.get("handoff_mode"),
            "control_status": control.get("status") or summary.get("control_status"),
            "control_mode": control.get("mode") or summary.get("control_mode"),
            "recommended_step": recommended.get("step_id") or summary.get("recommended_step"),
            "recommended_label": recommended.get("label"),
            "requires_human": bool(control.get("requires_human")),
            "requires_receipt": bool(control.get("requires_receipt")),
            "execution_chain_steps": len(compact_chain),
            "blocking_steps": control.get("blocking_steps") or [],
            "attention_steps": control.get("attention_steps") or [],
            "required_ledgers": evaluation.get("required_ledgers") or [],
            "agent_plan_risk": agent_plan_draft.get("risk_level"),
            "agent_plan_approval_required": bool(agent_plan_draft.get("approval_required")),
            "workflow_job_recovery_status": workflow_recovery.get("status"),
            "workflow_job_recovery_items": workflow_recovery_summary.get("items"),
            "workflow_job_recovery_stuck_jobs": workflow_recovery_summary.get("stuck_jobs"),
            "workflow_job_recovery_retryable_failed_jobs": workflow_recovery_summary.get("retryable_failed_jobs"),
            "workflow_job_recovery_receipt_missing": workflow_recovery_summary.get("receipt_missing"),
            "local_readiness_status": local_readiness.get("status"),
            "current_code_status": current_code_gate.get("status"),
            "current_code_ok": current_code_gate.get("ok"),
            "local_run_path_steps": len(compact_local_steps),
            "local_run_path_recommended_adapter": (local_readiness.get("summary") or {}).get("recommended_adapter") if isinstance(local_readiness.get("summary"), dict) else None,
            "service_control_preview": bool(service_step),
        },
        "next_command": control.get("next_command") or recommended.get("command"),
        "verify_command": control.get("verify_command") or recommended.get("verify_command"),
        "receipt_command": control.get("receipt_command") or recommended.get("receipt_command"),
        "adapter_preflight_command": adapter_command,
        "live_run_command": live_run_command,
        "readback_commands": readback_commands,
        "runtime_doctor_command": "agentops operator runtime-doctor --limit 8",
        "local_run_path": local_run_path,
        "workflow_job_recovery": {
            "operation": workflow_recovery.get("operation"),
            "status": workflow_recovery.get("status"),
            "summary": workflow_recovery_summary,
            "next_actions": workflow_recovery.get("next_actions") or [],
            "commands": workflow_recovery_commands[:8],
            "items": [
                {
                    "job_id": item.get("job_id"),
                    "mode": item.get("mode"),
                    "status": item.get("status"),
                    "preview_command": item.get("preview_command"),
                    "confirm_command": item.get("confirm_command"),
                    "verify_command": item.get("verify_command"),
                    "receipt_verify_record_command": item.get("receipt_verify_record_command"),
                    "receipt_state": item.get("receipt_state") or {},
                    "token_omitted": True,
                }
                for item in workflow_recovery_items[:3]
                if isinstance(item, dict)
            ],
            "contract": "compact recover-job work order projection; preview first, confirm only with --confirm-recover, then record/verify receipt",
            "token_omitted": True,
        },
        "execution_chain": compact_chain,
        "policy": {
            "policy_id": control.get("policy_id") or (audit.get("bounded_runner") or {}).get("policy_id"),
            "server_executes_shell": bool(control.get("server_executes_shell") or (audit.get("bounded_runner") or {}).get("server_executes_shell")),
            "live_execution_requires_confirm_run": adapter in {"hermes", "openclaw"},
            "external_writes_require_prepared_action": adapter in {"hermes", "openclaw"},
            "copy_only": control.get("copy_only", True) is not False,
        },
        "safety": {
            "read_only": bool(safety.get("read_only", True)),
            "ledger_mutated": bool(safety.get("ledger_mutated")),
            "live_execution_performed": bool(safety.get("live_execution_performed")),
            "raw_prompt_omitted": bool(safety.get("raw_prompt_omitted", True)),
            "raw_response_omitted": bool(safety.get("raw_response_omitted", True)),
            "token_omitted": bool(safety.get("token_omitted", True)),
        },
        "commands": [
            adapter_command,
            "agentops operator loop-control --limit 8",
            "agentops operator advance-loop --fast-control --limit 8",
            *local_run_path_commands[:8],
            live_run_command,
            *readback_commands,
            *workflow_recovery_commands[:6],
        ],
        "contract": "compact copy-only launch brief for Hermes/OpenClaw/Codex; derived from loop-launch-packet without mutating ledgers, executing runtimes, or exposing raw prompts/responses/tokens",
        "token_omitted": True,
        "live_execution_performed": False,
    }


def compact_advance_loop_result(payload: dict) -> dict:
    preview = payload.get("preview") if isinstance(payload.get("preview"), dict) else {}
    receipt = payload.get("receipt") if isinstance(payload.get("receipt"), dict) else {}
    receipt_row = receipt.get("receipt") if isinstance(receipt.get("receipt"), dict) else receipt
    control = payload.get("control_readback") if isinstance(payload.get("control_readback"), dict) else {}
    after = control.get("after") if isinstance(control.get("after"), dict) else {}
    action_result = payload.get("action_result") if isinstance(payload.get("action_result"), dict) else {}
    verify_result = payload.get("verify_result") if isinstance(payload.get("verify_result"), dict) else {}
    return {
        "operation": payload.get("operation"),
        "status": payload.get("status"),
        "advanced": bool(payload.get("advanced")),
        "control_source": payload.get("control_source"),
        "gate_id": preview.get("gate_id"),
        "source": preview.get("source"),
        "action_command": preview.get("action_command"),
        "verify_command": preview.get("verify_command"),
        "action_ok": action_result.get("ok"),
        "verify_ok": verify_result.get("ok") if verify_result else None,
        "receipt_id": receipt_row.get("receipt_id"),
        "receipt_status": receipt_row.get("status"),
        "control_after_status": after.get("status"),
        "control_after_next_command": after.get("next_command"),
        "raw_output_omitted": True,
        "token_omitted": True,
    }


def compact_loop_driver_adapter_readiness(payload: dict, *, adapter: str) -> dict:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    adapters = payload.get("adapters") if isinstance(payload.get("adapters"), dict) else {}
    item = adapters.get(adapter) if isinstance(adapters.get(adapter), dict) else {}
    checks = item.get("checks") if isinstance(item.get("checks"), dict) else {}
    remediation = item.get("remediation") if isinstance(item.get("remediation"), dict) else {}
    readiness = str(item.get("readiness") or "unknown")
    return {
        "operation": "operator_loop_driver_adapter_readiness",
        "source_operation": "worker_adapter_readiness",
        "status": payload.get("status", "unknown"),
        "adapter": adapter,
        "ok": bool(item.get("ok")),
        "readiness": readiness,
        "connector_id": item.get("connector_id"),
        "trust_status": item.get("trust_status"),
        "requires_confirm_run": bool(item.get("requires_confirm_run")),
        "recommended_action": item.get("recommended_action"),
        "last_error": redact_text(item.get("last_error"), 240) if item.get("last_error") else None,
        "target_resource": redact_text(item.get("target_resource"), 240) if item.get("target_resource") else None,
        "checks": {
            key: checks.get(key)
            for key in [
                "api_listening",
                "api_port",
                "config_exists",
                "auth_exists",
                "binary_exists",
                "binary_executable",
                "agents_count",
                "cron_jobs_count",
                "live_execution_performed",
            ]
            if key in checks
        },
        "summary": {
            "ready_adapters": summary.get("ready_adapters") or [],
            "live_ready_adapters": summary.get("live_ready_adapters") or [],
            "recommended_adapter": summary.get("recommended_adapter"),
            "blocked_adapters": summary.get("blocked_adapters") or [],
            "unavailable_adapters": summary.get("unavailable_adapters") or [],
        },
        "commands": {
            "worker_readiness": "agentops worker readiness",
            "adapter_preflight": f"agentops worker preflight --adapter {adapter}",
            "runtime_doctor": "agentops operator runtime-doctor --limit 8",
        },
        "remediation": {
            "status": remediation.get("status"),
            "primary_next_action": remediation.get("primary_next_action"),
            "missing": remediation.get("missing") or [],
            "commands": [
                {
                    "phase": command.get("phase"),
                    "command": command.get("command"),
                    "mutating": bool(command.get("mutating")),
                    "confirm_required": bool(command.get("confirm_required")),
                }
                for command in (remediation.get("commands") if isinstance(remediation.get("commands"), list) else [])[:8]
                if isinstance(command, dict)
            ],
            "safety": remediation.get("safety") if isinstance(remediation.get("safety"), dict) else {
                "read_only": True,
                "ledger_mutated": False,
                "live_execution_performed": False,
                "server_executes_shell": False,
                "token_omitted": True,
            },
            "token_omitted": True,
        },
        "gate": {
            "live_dispatch_ready": readiness in {"ready", "review_required"},
            "live_dispatch_requires_confirm_run": adapter in {"hermes", "openclaw"},
            "loop_control_may_continue": True,
            "blocks_live_dispatch": readiness in {"blocked", "unavailable", "unknown"},
        },
        "safety": {
            "read_only": True,
            "ledger_mutated": False,
            "live_execution_performed": False,
            "server_executes_shell": False,
            "token_omitted": True,
        },
        "token_omitted": True,
        "live_execution_performed": False,
    }


def fetch_loop_driver_adapter_readiness(args, client: AgentOpsClient) -> dict:
    payload = client.get("/api/workers/adapter-readiness")
    return compact_loop_driver_adapter_readiness(payload, adapter=args.adapter)


def fetch_loop_launch_brief(args, client: AgentOpsClient) -> dict:
    payload = client.get(
        "/api/operator/loop-launch-packet",
        query={
            "limit": args.limit,
            "task_id": args.task_id,
            "agent_id": args.agent_id,
            "q": args.query,
            "handoff_mode": args.handoff_mode,
            "full_handoff": "true" if getattr(args, "full_handoff", False) else None,
        },
    )
    local_readiness = client.get("/api/local/readiness")
    return compact_loop_launch_packet(payload, adapter=args.adapter, local_readiness=local_readiness)


def compact_loop_driver_acceptance_gate(payload: dict, *, adapter: str) -> dict:
    packet = payload.get("acceptance_packet") if isinstance(payload.get("acceptance_packet"), dict) else {}
    decision = packet.get("decision") if isinstance(packet.get("decision"), dict) else {}
    summary = packet.get("summary") if isinstance(packet.get("summary"), dict) else {}
    commands = packet.get("commands") if isinstance(packet.get("commands"), dict) else {}
    safety = packet.get("safety") if isinstance(packet.get("safety"), dict) else {}
    bounded_allowed = bool(decision.get("can_confirm_bounded_loop"))
    live_dispatch_allowed = bool(decision.get("live_dispatch_allowed"))
    live_dispatch_waiting = adapter in {"hermes", "openclaw"} and not live_dispatch_allowed
    server_executes_shell = bool(safety.get("server_executes_shell"))
    stop_reasons = []
    if not packet:
        stop_reasons.append("acceptance_packet_missing")
    if not bounded_allowed:
        stop_reasons.append("bounded_loop_not_accepted")
    if server_executes_shell:
        stop_reasons.append("server_shell_safety_missing")
    return {
        "operation": "operator_loop_driver_acceptance_gate",
        "source_operation": payload.get("operation"),
        "status": "ready" if bounded_allowed and not server_executes_shell else "blocked",
        "adapter": adapter,
        "workspace_id": packet.get("workspace_id") or payload.get("workspace_id"),
        "task_id": packet.get("task_id") or payload.get("task_id"),
        "agent_id": packet.get("agent_id") or payload.get("agent_id"),
        "decision": {
            "can_preview_loop": bool(decision.get("can_preview_loop")),
            "can_confirm_bounded_loop": bounded_allowed,
            "live_dispatch_allowed": live_dispatch_allowed,
            "live_dispatch_requires_confirm_run": bool(decision.get("live_dispatch_requires_confirm_run")),
            "human_review_required": bool(decision.get("human_review_required")),
            "memory_review_required": bool(decision.get("memory_review_required")),
            "current_code_required": decision.get("current_code_required") is not False,
            "current_code_ok": decision.get("current_code_ok") is not False,
            "agent_plan_required": decision.get("agent_plan_required") is not False,
            "knowledge_search_required": decision.get("knowledge_search_required") is not False,
            "base_compare_required": decision.get("base_compare_required") is not False,
            "receipt_required": decision.get("receipt_required") is not False,
        },
        "summary": {
            "blocked_gates": summary.get("blocked_gates") or [],
            "attention_gates": summary.get("attention_gates") or [],
            "review_items_total": int(summary.get("review_items_total") or 0),
            "pending_approvals": int(summary.get("pending_approvals") or 0),
            "memory_candidates": int(summary.get("memory_candidates") or 0),
            "required_ledgers": summary.get("required_ledgers") or [],
            "current_code_status": summary.get("current_code_status"),
            "current_code_ok": summary.get("current_code_ok"),
            "runtime_doctor_status": summary.get("runtime_doctor_status"),
            "adapter_readiness": summary.get("adapter_readiness"),
            "live_product_readiness": summary.get("live_product_readiness"),
        },
        "commands": {
            "start_check": commands.get("start_check") or f"agentops operator start-check --adapter {adapter} --limit 8",
            "current_code_check": commands.get("current_code_check"),
            "loop_driver_preview": commands.get("loop_driver_preview"),
            "loop_driver_confirm": commands.get("loop_driver_confirm"),
            "runtime_doctor": commands.get("runtime_doctor"),
            "review_queue": commands.get("review_queue"),
            "live_product_readiness": commands.get("live_product_readiness"),
            "receipt_readback": commands.get("receipt_readback"),
        },
        "wait_gates": {
            "human_review": bool(decision.get("human_review_required")),
            "live_dispatch": live_dispatch_waiting,
            "memory_review": bool(decision.get("memory_review_required")),
        },
        "stop_reasons": stop_reasons,
        "contract": "loop-driver must read start-check acceptance_packet before preview and before each confirmed bounded advance; it may only continue bounded advance when can_confirm_bounded_loop is true and safety proves no server shell",
        "safety": {
            "read_only": True,
            "ledger_mutated": False,
            "live_execution_performed": False,
            "server_executes_shell": server_executes_shell,
            "raw_prompt_omitted": safety.get("raw_prompt_omitted", True) is not False,
            "raw_response_omitted": safety.get("raw_response_omitted", True) is not False,
            "raw_content_omitted": safety.get("raw_content_omitted", True) is not False,
            "token_omitted": safety.get("token_omitted", True) is not False,
        },
        "token_omitted": True,
        "live_execution_performed": False,
    }


def fetch_loop_driver_acceptance_gate(args, client: AgentOpsClient) -> dict:
    payload = client.get(
        "/api/operator/start-check",
        query={
            "adapter": args.adapter,
            "limit": args.limit,
            "loop_id": args.loop_id,
            "task_id": args.task_id,
            "agent_id": args.agent_id,
            "q": args.query,
            "handoff_mode": args.handoff_mode,
            "full_handoff": "true" if getattr(args, "full_handoff", False) else None,
        },
    )
    return compact_loop_driver_acceptance_gate(payload, adapter=args.adapter)


def fetch_loop_driver_work_packet_decision(args, client: AgentOpsClient) -> dict:
    payload = client.get(
        "/api/operator/loop-supervision",
        query={
            "adapter": args.adapter,
            "limit": args.limit,
            "loop_id": args.loop_id,
            "task_id": args.task_id,
            "agent_id": args.agent_id,
            "q": args.query,
            "handoff_mode": args.handoff_mode,
            "full_handoff": "true" if getattr(args, "full_handoff", False) else None,
            "include_codex": "false",
            "decision": "true",
        },
    )
    decisions = payload.get("decisions") if isinstance(payload.get("decisions"), list) else []
    selected = next((item for item in decisions if isinstance(item, dict) and item.get("adapter") == args.adapter), {})
    selected_safety = selected.get("safety") if isinstance(selected.get("safety"), dict) else {}
    selected_policy = selected.get("policy") if isinstance(selected.get("policy"), dict) else {}
    decision = str(selected.get("decision") or "missing")
    hard_blocked = (
        decision in {"stop", "blocked", "missing"}
        or selected_safety.get("server_executes_shell") is True
        or selected_safety.get("live_execution_performed") is True
        or selected_policy.get("server_may_execute") is True
    )
    return {
        "operation": "operator_loop_driver_work_packet_decision_gate",
        "source_operation": payload.get("operation"),
        "schema_version": payload.get("schema_version"),
        "adapter": args.adapter,
        "status": "blocked" if hard_blocked else "ready",
        "ok": not hard_blocked,
        "decision": selected,
        "summary": payload.get("summary") if isinstance(payload.get("summary"), dict) else {},
        "hard_blocked_decisions": ["stop", "blocked", "missing"],
        "consumed_before_bounded_advance": True,
        "contract": "loop-driver must consume the compact work-packet decision before confirmed bounded advance; governance-first decisions may continue through allowlisted advance-loop, but stop/blocked/safety-boundary decisions fail closed",
        "safety": {
            "read_only": True,
            "ledger_mutated": False,
            "live_execution_performed": False,
            "server_executes_shell": False,
            "token_omitted": True,
        },
        "token_omitted": True,
        "live_execution_performed": False,
    }


def fetch_loop_driver_review_snapshot(args, client: AgentOpsClient) -> dict:
    limit = min(max(int(getattr(args, "limit", 8) or 8), 1), 20)
    payload = client.get("/api/agent-gateway/review/queue", query={"limit": min(limit, 8)})
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    review_items = payload.get("review_items") if isinstance(payload.get("review_items"), list) else []
    compact_items = []
    for item in review_items[: min(limit, 5)]:
        compact_items.append({
            "item_id": item.get("item_id"),
            "item_type": item.get("item_type"),
            "kind": item.get("kind"),
            "status": item.get("status"),
            "priority": item.get("priority"),
            "task_id": item.get("task_id"),
            "run_id": item.get("run_id"),
            "approval_id": item.get("approval_id"),
            "memory_id": item.get("memory_id"),
            "next_action": redact_text(item.get("next_action") or item.get("cli_action") or "", 500),
            "summary_omitted": True,
            "raw_content_omitted": True,
            "token_omitted": True,
        })
    pending_approvals = int(summary.get("pending_approvals") or 0)
    memory_candidates = int(summary.get("memory_candidates") or 0)
    review_items_total = int(summary.get("review_items_total") or len(review_items))
    next_actions = [
        action
        for action in (payload.get("next_actions") or [])
        if isinstance(action, str) and action.strip()
    ]
    return {
        "operation": "loop_driver_record_review_snapshot",
        "status": "attention" if review_items_total or pending_approvals or memory_candidates else "ready",
        "summary": {
            "review_items_total": review_items_total,
            "returned_items": int(summary.get("returned_items") or len(review_items)),
            "pending_approvals": pending_approvals,
            "memory_candidates": memory_candidates,
            "retrieved_pending_approvals": int(summary.get("retrieved_pending_approvals") or 0),
            "retrieved_memory_candidates": int(summary.get("retrieved_memory_candidates") or 0),
        },
        "items": compact_items,
        "next_action": next_actions[0] if next_actions else "agentops review queue --limit 20",
        "review_command": "agentops review queue --limit 20",
        "contract": "read-only RECORD snapshot for loop-driver; exposes review pressure and memory candidates without approving, rejecting, promoting, or storing raw content",
        "safety": {
            "read_only": True,
            "ledger_mutated": False,
            "live_execution_performed": False,
            "raw_prompt_omitted": True,
            "raw_response_omitted": True,
            "raw_content_omitted": True,
            "token_omitted": True,
        },
        "token_omitted": True,
    }


def maybe_auto_close_loop_driver_service(args, client: AgentOpsClient, *, step: int = 0) -> dict | None:
    if not getattr(args, "auto_service_closure", False):
        return None
    if getattr(args, "adapter", "") not in {"hermes", "openclaw"}:
        return None
    service_args = argparse.Namespace(
        adapter=args.adapter,
        fast=False,
        service_check_json="",
        run_service_check=True,
        service_check_command="",
        service_check_manager=None,
        service_check_agent_id="",
        service_path=getattr(args, "service_path", "") or "",
        service_label=getattr(args, "service_label", "") or "",
        api_key_placeholder="<paste one-time token here>",
        service_check_timeout=int(getattr(args, "service_check_timeout", 5) or 5),
        receipt_command="",
        receipt_status="verified",
        result_summary=f"{args.adapter} loop-driver auto service closure readback recorded before bounded loop.",
        actor_id=getattr(args, "actor_id", "usr_founder") or "usr_founder",
        limit=getattr(args, "limit", 8),
        task_id=getattr(args, "task_id", None),
        agent_id=getattr(args, "agent_id", None),
        query=getattr(args, "query", "READ PLAN RETRIEVE COMPARE VERIFY RECORD"),
        handoff_mode=getattr(args, "handoff_mode", "lightweight"),
        full_handoff=bool(getattr(args, "full_handoff", False)),
        confirm_record=True,
    )
    result = cmd_operator_service_closure(service_args, client)
    post_supervision = client.get(
        "/api/operator/loop-supervision",
        query={
            "adapter": args.adapter,
            "limit": getattr(args, "limit", 8),
            "task_id": getattr(args, "task_id", None),
            "agent_id": getattr(args, "agent_id", None),
            "q": getattr(args, "query", "READ PLAN RETRIEVE COMPARE VERIFY RECORD"),
            "handoff_mode": getattr(args, "handoff_mode", "lightweight"),
            "full_handoff": "true" if getattr(args, "full_handoff", False) else None,
            "include_codex": "false",
        },
    )
    post_items = post_supervision.get("items") if isinstance(post_supervision.get("items"), list) else []
    post_item = next((row for row in post_items if isinstance(row, dict) and row.get("adapter") == args.adapter), {})
    post_service_closure = post_item.get("service_closure") if isinstance(post_item.get("service_closure"), dict) else {}
    return {
        "operation": "operator_loop_driver_service_closure",
        "step": step,
        "adapter": args.adapter,
        "status": result.get("status"),
        "ok": result.get("ok") is True,
        "ready_to_continue": result.get("ok") is True and post_service_closure.get("required") is not True,
        "recorded": result.get("recorded") is True,
        "receipt_id": result.get("receipt_id"),
        "service_closure": result.get("service_closure"),
        "post_service_closure": post_service_closure,
        "service_check": result.get("service_check"),
        "missing": result.get("missing") or [],
        "safety": {
            **(result.get("safety") if isinstance(result.get("safety"), dict) else {}),
            "server_executes_shell": False,
            "live_execution_performed": False,
            "token_omitted": True,
        },
        "contract": "explicit loop-driver --auto-service-closure uses only local read-only worker service-check plus Action Receipt/control-readback recording; it does not run service-control, shell, server-side commands, or live adapter work",
        "token_omitted": True,
    }


def cmd_operator_loop_driver(args, client: AgentOpsClient) -> dict:
    max_steps = min(max(int(args.max_steps or 1), 1), 5)
    initial_acceptance_gate = fetch_loop_driver_acceptance_gate(args, client)
    initial_work_packet_decision = fetch_loop_driver_work_packet_decision(args, client)
    adapter_readiness = fetch_loop_driver_adapter_readiness(args, client)
    initial_brief = fetch_loop_launch_brief(args, client)
    initial_review_snapshot = fetch_loop_driver_review_snapshot(args, client)
    initial_agent_loop_packet = operator_agent_loop_packet(
        adapter=args.adapter,
        max_steps=max_steps,
        acceptance_gate=initial_acceptance_gate,
        adapter_readiness=adapter_readiness,
        launch_brief=initial_brief,
        review_snapshot=initial_review_snapshot,
        confirm_loop=bool(args.confirm_loop),
    )
    policy = advance_loop_policy_summary()
    if not args.confirm_loop:
        return {
            "provider": "agentops-operator",
            "operation": "operator_loop_driver",
            "status": "preview",
            "advanced": False,
            "adapter": args.adapter,
            "max_steps": max_steps,
            "acceptance_gate": initial_acceptance_gate,
            "work_packet_decision": initial_work_packet_decision,
            "agent_loop_packet": initial_agent_loop_packet,
            "adapter_readiness": adapter_readiness,
            "initial_brief": initial_brief,
            "record_review_snapshot": initial_review_snapshot,
            "next_actions": [
                (initial_acceptance_gate.get("commands") or {}).get("start_check") or f"agentops operator start-check --adapter {args.adapter} --limit {args.limit}",
                f"agentops worker preflight --adapter {args.adapter}",
                initial_review_snapshot.get("review_command") or "agentops review queue --limit 20",
                (initial_acceptance_gate.get("commands") or {}).get("loop_driver_confirm")
                or "agentops operator loop-driver --confirm-loop --max-steps "
                f"{max_steps} --adapter {args.adapter} --limit {args.limit}",
            ],
            "policy": {
                **policy,
                "driver_max_steps": 5,
                "driver_uses": "operator start-check acceptance_packet plus operator loop-supervision --decision plus operator loop-launch-packet --brief plus operator advance-loop --fast-control --confirm-advance",
                "acceptance_packet_required_before_confirm_loop": True,
                "work_packet_decision_required_before_confirm_loop": True,
                "adapter_preflight_required_before_live_run": args.adapter in {"hermes", "openclaw"},
                "auto_service_closure_available": args.adapter in {"hermes", "openclaw"},
                "auto_service_closure_enabled": False,
            },
            "contract": "preview-only agent loop driver; reads start-check acceptance_packet, compact work-packet decision, compact launch brief, adapter readiness, and review pressure without executing commands or mutating ledgers",
            "safety": {
                "read_only": True,
                "ledger_mutated": False,
                "live_execution_performed": False,
                "server_executes_shell": False,
                "token_omitted": True,
            },
            "token_omitted": True,
            "live_execution_performed": False,
        }

    steps: list[dict] = []
    service_closure_attempts: list[dict] = []
    stop_reason = "max_steps_reached"
    if (initial_acceptance_gate.get("decision") or {}).get("can_confirm_bounded_loop") is not True or (initial_acceptance_gate.get("safety") or {}).get("server_executes_shell") is not False:
        stop_reason = "acceptance_gate_blocked"
        final_adapter_readiness = fetch_loop_driver_adapter_readiness(args, client)
        final_brief = fetch_loop_launch_brief(args, client)
        final_review_snapshot = fetch_loop_driver_review_snapshot(args, client)
        final_agent_loop_packet = operator_agent_loop_packet(
            adapter=args.adapter,
            max_steps=max_steps,
            acceptance_gate=initial_acceptance_gate,
            adapter_readiness=final_adapter_readiness,
            launch_brief=final_brief,
            review_snapshot=final_review_snapshot,
            confirm_loop=True,
            stop_reason=stop_reason,
        )
        return {
            "provider": "agentops-operator",
            "operation": "operator_loop_driver",
            "status": "blocked",
            "adapter": args.adapter,
            "max_steps": max_steps,
            "steps_attempted": 0,
            "steps_advanced": 0,
            "stop_reason": stop_reason,
            "acceptance_gate": initial_acceptance_gate,
            "work_packet_decision": initial_work_packet_decision,
            "initial_work_packet_decision": initial_work_packet_decision,
            "agent_loop_packet": final_agent_loop_packet,
            "steps": [],
            "adapter_readiness": final_adapter_readiness,
            "final_brief": final_brief,
            "initial_record_review_snapshot": initial_review_snapshot,
            "record_review_snapshot": final_review_snapshot,
            "policy": {
                **policy,
                "driver_max_steps": 5,
                "server_executes_shell": False,
                "acceptance_packet_required_before_confirm_loop": True,
                "work_packet_decision_required_before_confirm_loop": True,
                "adapter_preflight_required_before_live_run": args.adapter in {"hermes", "openclaw"},
            },
            "contract": "bounded local agent loop driver stopped before advance because start-check acceptance_packet did not allow confirm-loop",
            "safety": {
                "read_only": True,
                "ledger_mutated": False,
                "live_execution_performed": False,
                "server_executes_shell": False,
                "raw_output_omitted": True,
                "token_omitted": True,
            },
            "token_omitted": True,
            "live_execution_performed": False,
        }
    if initial_work_packet_decision.get("ok") is not True:
        stop_reason = "work_packet_decision_blocked"
        final_adapter_readiness = fetch_loop_driver_adapter_readiness(args, client)
        final_brief = fetch_loop_launch_brief(args, client)
        final_review_snapshot = fetch_loop_driver_review_snapshot(args, client)
        final_agent_loop_packet = operator_agent_loop_packet(
            adapter=args.adapter,
            max_steps=max_steps,
            acceptance_gate=initial_acceptance_gate,
            adapter_readiness=final_adapter_readiness,
            launch_brief=final_brief,
            review_snapshot=final_review_snapshot,
            confirm_loop=True,
            stop_reason=stop_reason,
        )
        return {
            "provider": "agentops-operator",
            "operation": "operator_loop_driver",
            "status": "blocked",
            "adapter": args.adapter,
            "max_steps": max_steps,
            "steps_attempted": 0,
            "steps_advanced": 0,
            "stop_reason": stop_reason,
            "acceptance_gate": initial_acceptance_gate,
            "work_packet_decision": initial_work_packet_decision,
            "initial_work_packet_decision": initial_work_packet_decision,
            "agent_loop_packet": final_agent_loop_packet,
            "steps": [],
            "adapter_readiness": final_adapter_readiness,
            "final_brief": final_brief,
            "initial_record_review_snapshot": initial_review_snapshot,
            "record_review_snapshot": final_review_snapshot,
            "policy": {
                **policy,
                "driver_max_steps": 5,
                "server_executes_shell": False,
                "acceptance_packet_required_before_confirm_loop": True,
                "work_packet_decision_required_before_confirm_loop": True,
                "adapter_preflight_required_before_live_run": args.adapter in {"hermes", "openclaw"},
            },
            "contract": "bounded local agent loop driver stopped before advance because the compact work-packet decision failed closed",
            "safety": {
                "read_only": True,
                "ledger_mutated": False,
                "live_execution_performed": False,
                "server_executes_shell": False,
                "raw_output_omitted": True,
                "token_omitted": True,
            },
            "token_omitted": True,
            "live_execution_performed": False,
        }
    for index in range(max_steps):
        before_acceptance_gate = fetch_loop_driver_acceptance_gate(args, client)
        if (before_acceptance_gate.get("decision") or {}).get("can_confirm_bounded_loop") is not True or (before_acceptance_gate.get("safety") or {}).get("server_executes_shell") is not False:
            stop_reason = "acceptance_gate_blocked"
            break
        before_work_packet_decision = fetch_loop_driver_work_packet_decision(args, client)
        if before_work_packet_decision.get("ok") is not True:
            stop_reason = "work_packet_decision_blocked"
            break
        service_closure_attempt = maybe_auto_close_loop_driver_service(args, client, step=index + 1)
        if service_closure_attempt:
            service_closure_attempts.append(service_closure_attempt)
            if service_closure_attempt.get("ok") is not True:
                stop_reason = "service_closure_blocked"
                break
            if service_closure_attempt.get("ready_to_continue") is not True:
                stop_reason = "service_activation_required"
                break
            before_acceptance_gate = fetch_loop_driver_acceptance_gate(args, client)
            before_work_packet_decision = fetch_loop_driver_work_packet_decision(args, client)
            if (before_acceptance_gate.get("decision") or {}).get("can_confirm_bounded_loop") is not True or (before_acceptance_gate.get("safety") or {}).get("server_executes_shell") is not False:
                stop_reason = "acceptance_gate_blocked"
                break
            if before_work_packet_decision.get("ok") is not True:
                stop_reason = "work_packet_decision_blocked"
                break
        before_readiness = fetch_loop_driver_adapter_readiness(args, client)
        before_brief = fetch_loop_launch_brief(args, client)
        advance_args = argparse.Namespace(
            loop_id=args.loop_id,
            limit=args.limit,
            timeout=args.timeout,
            actor_id=args.actor_id,
            fast_control=True,
            confirm_advance=True,
        )
        advance_result = cmd_operator_advance_loop(advance_args, client)
        after_acceptance_gate = fetch_loop_driver_acceptance_gate(args, client)
        after_work_packet_decision = fetch_loop_driver_work_packet_decision(args, client)
        after_readiness = fetch_loop_driver_adapter_readiness(args, client)
        after_brief = fetch_loop_launch_brief(args, client)
        after_review_snapshot = fetch_loop_driver_review_snapshot(args, client)
        compact_advance = compact_advance_loop_result(advance_result)
        step = {
            "step": index + 1,
            "service_closure": service_closure_attempt,
            "acceptance_gate_before": before_acceptance_gate,
            "work_packet_decision_before": before_work_packet_decision,
            "adapter_readiness_before": before_readiness,
            "before": {
                "status": before_brief.get("status"),
                "next_command": before_brief.get("next_command"),
                "control_status": (before_brief.get("summary") or {}).get("control_status"),
                "control_mode": (before_brief.get("summary") or {}).get("control_mode"),
                "workflow_job_recovery_status": (before_brief.get("summary") or {}).get("workflow_job_recovery_status"),
                "token_omitted": True,
            },
            "advance": compact_advance,
            "acceptance_gate_after": after_acceptance_gate,
            "work_packet_decision_after": after_work_packet_decision,
            "adapter_readiness_after": after_readiness,
            "after": {
                "status": after_brief.get("status"),
                "next_command": after_brief.get("next_command"),
                "control_status": (after_brief.get("summary") or {}).get("control_status"),
                "control_mode": (after_brief.get("summary") or {}).get("control_mode"),
                "workflow_job_recovery_status": (after_brief.get("summary") or {}).get("workflow_job_recovery_status"),
                "token_omitted": True,
            },
            "record_review_snapshot": after_review_snapshot,
            "token_omitted": True,
        }
        steps.append(step)
        status = str(advance_result.get("status") or "")
        if status in {"empty", "blocked", "failed"} or not advance_result.get("advanced"):
            stop_reason = status or "not_advanced"
            break
        after_summary = after_brief.get("summary") or {}
        if after_summary.get("control_status") == "ready" and not after_brief.get("next_command"):
            stop_reason = "control_ready"
            break

    final_acceptance_gate = fetch_loop_driver_acceptance_gate(args, client)
    final_work_packet_decision = fetch_loop_driver_work_packet_decision(args, client)
    final_adapter_readiness = fetch_loop_driver_adapter_readiness(args, client)
    final_brief = fetch_loop_launch_brief(args, client)
    final_review_snapshot = fetch_loop_driver_review_snapshot(args, client)
    failed = [step for step in steps if (step.get("advance") or {}).get("status") in {"failed", "blocked"}]
    advanced = [step for step in steps if (step.get("advance") or {}).get("advanced")]
    final_agent_loop_packet = operator_agent_loop_packet(
        adapter=args.adapter,
        max_steps=max_steps,
        acceptance_gate=final_acceptance_gate,
        adapter_readiness=final_adapter_readiness,
        launch_brief=final_brief,
        review_snapshot=final_review_snapshot,
        confirm_loop=True,
        stop_reason=stop_reason,
        steps_advanced=len(advanced),
    )
    return {
        "provider": "agentops-operator",
        "operation": "operator_loop_driver",
        "status": "failed" if failed else "advanced" if advanced else "empty",
        "adapter": args.adapter,
        "max_steps": max_steps,
        "steps_attempted": len(steps),
        "steps_advanced": len(advanced),
        "stop_reason": stop_reason,
        "acceptance_gate": final_acceptance_gate,
        "initial_acceptance_gate": initial_acceptance_gate,
        "work_packet_decision": final_work_packet_decision,
        "initial_work_packet_decision": initial_work_packet_decision,
        "agent_loop_packet": final_agent_loop_packet,
        "initial_agent_loop_packet": initial_agent_loop_packet,
        "steps": steps,
        "service_closure_attempts": service_closure_attempts,
        "adapter_readiness": final_adapter_readiness,
        "final_brief": final_brief,
        "initial_record_review_snapshot": initial_review_snapshot,
        "record_review_snapshot": final_review_snapshot,
        "policy": {
            **policy,
            "driver_max_steps": 5,
            "server_executes_shell": False,
            "acceptance_packet_required_before_confirm_loop": True,
            "work_packet_decision_required_before_confirm_loop": True,
            "adapter_preflight_required_before_live_run": args.adapter in {"hermes", "openclaw"},
            "auto_service_closure_available": args.adapter in {"hermes", "openclaw"},
            "auto_service_closure_enabled": bool(getattr(args, "auto_service_closure", False)),
        },
        "contract": "bounded local agent loop driver for Hermes/OpenClaw/Codex; each step re-reads start-check acceptance_packet, compact work-packet decision, and launch brief, can explicitly auto-close service readback evidence before advancing, delegates execution to advance-loop allowlist policy only when confirm-loop is accepted, records receipts/control-readback, surfaces a read-only RECORD review snapshot, and never runs live/workflow/approval commands",
        "safety": {
            "read_only": False,
            "ledger_mutated": bool(advanced or service_closure_attempts),
            "live_execution_performed": False,
            "server_executes_shell": False,
            "local_cli_service_check_performed": any(
                ((attempt.get("safety") or {}).get("local_cli_service_check_performed") is True)
                for attempt in service_closure_attempts
            ),
            "raw_output_omitted": True,
            "token_omitted": True,
        },
        "token_omitted": True,
        "live_execution_performed": False,
    }


def cmd_operator_advance_loop_policy(args, client: AgentOpsClient) -> dict:
    sample_commands = [
        "agentops memory propose --type loop_record --text example --agent-id agt_example",
        "agentops memory approve --memory-id mem_example",
        "agentops workflow hermes-openclaw-loop --loop-id loop_example",
        "agentops operator loop-audit --limit 10",
    ]
    samples = [
        {"command": command, "action_decision": advance_loop_command_policy(command, phase="action")}
        for command in sample_commands
    ]
    return {
        "provider": "agentops-operator",
        "operation": "operator_advance_loop_policy",
        "policy": advance_loop_policy_summary(),
        "samples": samples,
        "contract": "read-only bounded runner policy; does not execute commands or mutate ledgers",
        "safety": {
            "read_only": True,
            "ledger_mutated": False,
            "live_execution_performed": False,
            "token_omitted": True,
        },
        "token_omitted": True,
    }


def agentops_cli_command(argv: list[str], client: AgentOpsClient) -> tuple[list[str], dict]:
    repo_cli = Path(__file__).resolve().parents[1] / "scripts" / "agentops"
    cli_entry = Path(sys.argv[0])
    if repo_cli.exists():
        executable = str(repo_cli)
    elif cli_entry.exists() and os.access(cli_entry, os.X_OK):
        executable = str(cli_entry)
    else:
        executable = "agentops"
    env = os.environ.copy()
    env["AGENTOPS_BASE_URL"] = client.base_url
    env["AGENTOPS_WORKSPACE_ID"] = client.workspace_id
    if client.agent_id:
        env["AGENTOPS_AGENT_ID"] = client.agent_id
    if client.api_key:
        env["AGENTOPS_API_KEY"] = client.api_key
    return [executable, "--base-url", client.base_url, *argv[1:]], env


def run_bounded_agentops_command(command: str, client: AgentOpsClient, *, timeout: int) -> dict:
    policy = advance_loop_command_policy(command, phase="action")
    if not policy.get("allowed"):
        return {
            "ok": False,
            "blocked": True,
            "policy": policy,
            "returncode": None,
            "stdout_summary": None,
            "stderr_summary": policy.get("reason"),
            "raw_output_omitted": True,
            "token_omitted": True,
        }
    argv, env = agentops_cli_command(policy["argv"], client)
    proc = subprocess.run(argv, cwd=Path.cwd(), env=env, capture_output=True, text=True, timeout=timeout, check=False)
    return {
        "ok": proc.returncode == 0,
        "blocked": False,
        "policy": {**policy, "argv": policy.get("argv", [])[:4]},
        "returncode": proc.returncode,
        "stdout_summary": redact_text(proc.stdout, 600) if proc.stdout else None,
        "stderr_summary": redact_text(proc.stderr, 600) if proc.stderr else None,
        "raw_output_omitted": True,
        "token_omitted": True,
    }


def select_advance_loop_item(handoff: dict) -> dict:
    work_order = handoff.get("work_order") or {}
    advance_loop = work_order.get("advance_loop") or {}
    selected = advance_loop.get("selected_item") or {}
    command = str(selected.get("action_command") or "").strip()
    if command:
        policy = advance_loop_command_policy(command, phase="action")
        if policy.get("allowed"):
            return {**selected, "advance_policy": policy}
    evidence_work_order = work_order.get("evidence_report") or {}
    evidence_status = str(evidence_work_order.get("status") or "").lower()
    evidence_receipt_state = evidence_work_order.get("receipt_state") or {}
    if not handoff.get("loop_id") and not evidence_receipt_state.get("verified") and evidence_status in {"blocked", "attention"}:
        for command in evidence_work_order.get("next_actions") or []:
            command = str(command or "").strip()
            if not command:
                continue
            policy = advance_loop_command_policy(command, phase="action")
            if not policy.get("allowed"):
                continue
            return {
                "package_id": "operator_evidence_report_work_order",
                "action_id": str(evidence_work_order.get("action_id") or "operator_evidence_report_work_order"),
                "action_signature": evidence_work_order.get("action_signature"),
                "gate_id": "evidence_report",
                "gate_label": "Run evidence report",
                "gate_status": evidence_status,
                "source": "operator_handoff.evidence_report",
                "action_command": command,
                "verify_command": "agentops operator handoff --limit 12",
                "receipt_verify_record_command": None,
                "evidence": {
                    "summary": evidence_work_order.get("summary") or {},
                    "runs": len(evidence_work_order.get("runs") or []),
                    "operation": evidence_work_order.get("operation"),
                    "receipt_state": evidence_receipt_state,
                },
                "advance_policy": policy,
                "token_omitted": True,
            }
    remediation_chain = evidence_work_order.get("remediation_chain") or {}
    if not handoff.get("loop_id") and remediation_chain.get("status") == "attention":
        for item in remediation_chain.get("items") or []:
            receipt_state = item.get("receipt_state") or {}
            if receipt_state.get("verified"):
                continue
            command = str(item.get("preview_command") or "").strip()
            if not command:
                continue
            policy = advance_loop_command_policy(command, phase="action")
            if not policy.get("allowed"):
                continue
            run_id = str(item.get("run_id") or "").strip()
            return {
                "package_id": str(item.get("package_id") or f"evidence_remediation:{run_id}" or "evidence_remediation"),
                "action_id": str(item.get("action_id") or f"evidence_remediation:{run_id}" or "evidence_remediation"),
                "action_signature": receipt_state.get("action_signature"),
                "gate_id": "evidence_remediation",
                "gate_label": "Evidence remediation preview",
                "gate_status": item.get("severity") or item.get("status") or remediation_chain.get("status"),
                "source": "operator_handoff.evidence_remediation",
                "action_command": command,
                "verify_command": item.get("verify_command") or (f"agentops operator evidence-report --run-id {run_id} --limit 1" if run_id else "agentops operator evidence-report --limit 8"),
                "receipt_verify_record_command": item.get("receipt_verify_record_command"),
                "receipt_source": "handoff.evidence_remediation",
                "evidence": {
                    "run_id": run_id,
                    "task_id": item.get("task_id"),
                    "failed_check_ids": item.get("failed_check_ids") or [],
                    "gap_types": item.get("gap_types") or [],
                    "missing_evidence": item.get("missing_evidence") or [],
                    "receipt_state": receipt_state,
                    "operation": item.get("operation"),
                },
                "advance_policy": policy,
                "token_omitted": True,
            }
    action_package = (work_order.get("action_package") or {})
    for item in action_package.get("items") or []:
        command = str(item.get("action_command") or "").strip()
        if not command:
            continue
        if item.get("gate_status") == "pass":
            continue
        policy = advance_loop_command_policy(command, phase="action")
        if policy.get("allowed"):
            return {**item, "advance_policy": policy}
    return {}


def compact_loop_control(payload: dict) -> dict:
    control = payload.get("control_summary") or {}
    step = control.get("recommended_step") or {}
    return {
        "operation": control.get("operation"),
        "status": control.get("status"),
        "mode": control.get("mode"),
        "selected_gate": control.get("selected_gate") or step.get("selected_gate"),
        "selected_status": control.get("selected_status"),
        "next_command": control.get("next_command") or step.get("command"),
        "verify_command": control.get("verify_command") or step.get("verify_command"),
        "receipt_command": control.get("receipt_command") or step.get("receipt_command"),
        "requires_human": control.get("requires_human"),
        "requires_receipt": control.get("requires_receipt"),
        "server_executes_shell": control.get("server_executes_shell"),
        "copy_only": control.get("copy_only"),
        "policy_id": control.get("policy_id") or step.get("policy_id"),
        "read_model_cache": payload.get("read_model_cache") or {},
        "token_omitted": True,
    }


def cmd_operator_advance_loop(args, client: AgentOpsClient) -> dict:
    control_endpoint = "/api/operator/loop-control" if args.fast_control else "/api/operator/handoff"
    handoff = client.get(control_endpoint, query={"limit": args.limit, "loop_id": args.loop_id or None})
    before_control = compact_loop_control(handoff)
    selected = select_advance_loop_item(handoff)
    policy_summary = advance_loop_policy_summary()
    if not selected:
        return {
            "provider": "agentops-operator",
            "operation": "operator_advance_loop",
            "status": "empty",
            "advanced": False,
            "message": "No allowlisted non-passing loop action is available in the handoff action package.",
            "handoff_status": handoff.get("status"),
            "control_readback": {
                "before": before_control,
                "after": None,
                "token_omitted": True,
            },
            "policy": policy_summary,
            "safety": {
                "read_only": True,
                "ledger_mutated": False,
                "live_execution_performed": False,
                "token_omitted": True,
            },
            "token_omitted": True,
        }
    action_command = str(selected.get("action_command") or "")
    verify_command = str(selected.get("verify_command") or "")
    verify_policy = advance_loop_command_policy(verify_command, phase="verify") if verify_command else {"allowed": True, "reason": "no verify command supplied", "argv": [], "token_omitted": True}
    preview = {
        "package_id": selected.get("package_id"),
        "gate_id": selected.get("gate_id"),
        "source": selected.get("source"),
        "gate_status": selected.get("gate_status"),
        "action_command": redact_text(action_command, 500),
        "verify_command": redact_text(verify_command, 500) if verify_command else None,
        "evidence": selected.get("evidence") or {},
        "action_policy": selected.get("advance_policy") or {},
        "verify_policy": verify_policy,
        "receipt_status_on_success": "verified",
        "receipt_status_on_failure": "failed",
        "token_omitted": True,
    }
    if not args.confirm_advance:
        return {
            "provider": "agentops-operator",
            "operation": "operator_advance_loop",
            "status": "preview",
            "advanced": False,
            "preview": preview,
            "control_readback": {
                "before": before_control,
                "after": None,
                "token_omitted": True,
            },
            "policy": policy_summary,
            "next_actions": ["rerun with --confirm-advance to execute exactly one allowlisted loop action"],
            "contract": "preview-only; does not execute commands and does not mutate ledgers without --confirm-advance",
            "safety": {
                "read_only": True,
                "ledger_mutated": False,
                "live_execution_performed": False,
                "token_omitted": True,
            },
            "token_omitted": True,
        }
    if not verify_policy.get("allowed"):
        return {
            "provider": "agentops-operator",
            "operation": "operator_advance_loop",
            "status": "blocked",
            "advanced": False,
            "preview": preview,
            "policy": policy_summary,
            "message": "Verify command failed bounded-runner policy.",
            "control_readback": {
                "before": before_control,
                "after": None,
                "token_omitted": True,
            },
            "safety": {
                "read_only": True,
                "ledger_mutated": False,
                "live_execution_performed": False,
                "token_omitted": True,
            },
            "token_omitted": True,
        }
    action_result = run_bounded_agentops_command(action_command, client, timeout=args.timeout)
    verify_result = None
    if action_result.get("ok") and verify_command:
        verify_policy_payload = advance_loop_command_policy(verify_command, phase="verify")
        argv, env = agentops_cli_command(verify_policy_payload["argv"], client)
        proc = subprocess.run(argv, cwd=Path.cwd(), env=env, capture_output=True, text=True, timeout=args.timeout, check=False)
        verify_result = {
            "ok": proc.returncode == 0,
            "policy": {**verify_policy_payload, "argv": verify_policy_payload.get("argv", [])[:4]},
            "returncode": proc.returncode,
            "stdout_summary": redact_text(proc.stdout, 600) if proc.stdout else None,
            "stderr_summary": redact_text(proc.stderr, 600) if proc.stderr else None,
            "raw_output_omitted": True,
            "token_omitted": True,
        }
    succeeded = bool(action_result.get("ok")) and (verify_result is None or bool(verify_result.get("ok")))
    receipt_payload = {
        "workspace_id": client.workspace_id,
        "actor_id": args.actor_id,
        "action_command": action_command,
        "verify_command": verify_command,
        "action_id": selected.get("action_id"),
        "action_signature": selected.get("action_signature"),
        "source": selected.get("receipt_source") or f"advance_loop:{selected.get('gate_id') or 'gate'}",
        "status": "verified" if succeeded else "failed",
        "result_summary": (
            f"advance-loop {'verified' if succeeded else 'failed'} gate {selected.get('gate_id') or 'unknown'}; "
            f"action_rc={action_result.get('returncode')} verify_rc={(verify_result or {}).get('returncode')}"
        ),
    }
    receipt = client.post("/api/operator/action-receipts", receipt_payload)
    refresh_query = {"limit": args.limit, "loop_id": args.loop_id or None, "refresh_cache": "true"}
    if args.fast_control:
        after_handoff = client.get("/api/operator/loop-control", query=refresh_query)
        after_self_check = after_handoff
    else:
        after_handoff = client.get("/api/operator/handoff", query=refresh_query)
        after_self_check = client.get("/api/operator/loop-self-check", query=refresh_query)
    control_readback = {
        "before": before_control,
        "after": compact_loop_control(after_handoff),
        "after_self_check": compact_loop_control(after_self_check),
        "refresh_cache_requested": True,
        "cache_bypassed": (
            ((after_handoff.get("read_model_cache") or {}).get("status") in {"bypass", "not_cached"})
            and ((after_self_check.get("read_model_cache") or {}).get("status") in {"bypass", "not_cached"})
        ),
        "token_omitted": True,
    }
    receipt_id = ((receipt.get("receipt") or {}).get("receipt_id") or receipt.get("receipt_id"))
    control_readback_receipt = None
    if receipt_id:
        control_readback_receipt = client.post("/api/operator/action-receipts/control-readback", {
            "workspace_id": client.workspace_id,
            "actor_id": args.actor_id,
            "receipt_id": receipt_id,
            "source": f"advance_loop:{selected.get('gate_id') or 'gate'}:control_readback",
            "control_readback": control_readback,
        })
    return {
        "provider": "agentops-operator",
        "operation": "operator_advance_loop",
        "status": "advanced" if succeeded else "failed",
        "control_source": "loop_control" if args.fast_control else "handoff",
        "advanced": True,
        "confirm_advance": True,
        "preview": preview,
        "policy": policy_summary,
        "action_result": action_result,
        "verify_result": verify_result,
        "receipt": receipt,
        "control_readback": control_readback,
        "control_readback_receipt": control_readback_receipt,
        "contract": "bounded CLI runner; executes at most one allowlisted local agentops action, verifies it, and records an append-only receipt; never approves memory or runs live/workflow commands",
        "safety": {
            "read_only": False,
            "ledger_mutated": True,
            "live_execution_performed": False,
            "raw_output_omitted": True,
            "token_omitted": True,
        },
        "token_omitted": True,
    }


def cmd_operator_remediate_evidence_gap(args, client: AgentOpsClient) -> dict:
    return client.post("/api/operator/execution-evidence/remediation-task", {
        "workspace_id": client.workspace_id,
        "run_id": args.run_id,
        "task_id": args.task_id,
        "title": args.title,
        "project_id": args.project_id,
        "plan_id": args.plan_id,
        "lane_id": args.lane_id,
        "owner_agent_id": args.owner_agent_id,
        "priority": args.priority,
        "risk_level": args.risk_level,
        "budget_limit_usd": args.budget_limit_usd,
        "actor_id": args.actor_id,
        "confirm_create": bool(args.confirm_create),
    })


def cmd_operator_close_evidence_gap(args, client: AgentOpsClient) -> dict:
    return client.post("/api/operator/execution-evidence/close-gap", {
        "workspace_id": client.workspace_id,
        "run_id": args.run_id,
        "decision": args.decision,
        "reason": args.reason or args.note,
        "synthesis_artifact_id": args.synthesis_artifact_id,
        "remediation_task_id": args.remediation_task_id,
        "actor_id": args.actor_id,
        "confirm_close": bool(args.confirm_close),
    })


def cmd_commander_board(args, client: AgentOpsClient) -> dict:
    return client.get("/api/commander/project-board", query={
        "project_id": getattr(args, "project_id", None),
        "plan_id": getattr(args, "plan_id", None),
        "limit": getattr(args, "limit", None),
    })


def cmd_commander_repo_map(args, client: AgentOpsClient) -> dict:
    query = args.query_flag if getattr(args, "query_flag", None) is not None else args.query
    return client.get("/api/commander/repo-map", query={
        "q": query,
        "limit": args.limit,
        "char_budget": args.char_budget,
    })


def cmd_commander_coding_template(args, client: AgentOpsClient) -> dict:
    query = args.query_flag if getattr(args, "query_flag", None) is not None else args.query
    return client.get("/api/commander/coding-project-template", query={
        "q": query,
        "project_id": args.project_id,
        "task_id": args.task_id,
        "limit": args.limit,
        "char_budget": args.char_budget,
    })


def cmd_commander_inbox(args, client: AgentOpsClient) -> dict:
    query = {}
    if getattr(args, "bucket", None):
        query["bucket"] = args.bucket
    if getattr(args, "limit", None) is not None:
        query["limit"] = str(args.limit)
    if getattr(args, "threshold_sec", None) is not None:
        query["threshold_sec"] = str(args.threshold_sec)
    path = "/api/commander/integration-inbox"
    if query:
        path = f"{path}?{urlencode(query)}"
    return client.get(path)


def cmd_commander_plan(args, client: AgentOpsClient) -> dict:
    lanes = parse_json_value(args.lanes_json, None) if getattr(args, "lanes_json", None) else None
    payload = {
        "workspace_id": client.workspace_id,
        "project_id": args.project_id,
        "plan_id": args.plan_id,
        "goal": args.goal,
        "max_packages": args.max_packages,
        "confirm_create": bool(args.confirm_create),
        "task_id_prefix": args.task_id_prefix,
    }
    if lanes is not None:
        payload["lanes"] = lanes
    return client.post("/api/commander/work-packages/plan", payload)


def cmd_commander_packages(args, client: AgentOpsClient) -> dict:
    query = {
        "project_id": args.project_id,
        "plan_id": args.plan_id,
        "status": args.status,
        "limit": args.limit,
    }
    return client.get("/api/commander/work-packages", query=query)


def cmd_commander_lane_packets(args, client: AgentOpsClient) -> dict:
    query = {
        "project_id": args.project_id,
        "plan_id": args.plan_id,
        "status": args.status,
        "limit": args.limit,
    }
    return client.get("/api/commander/lane-packets", query=query)


def cmd_commander_dispatch_package(args, client: AgentOpsClient) -> dict:
    payload = {
        "workspace_id": client.workspace_id,
        "adapter": args.adapter,
        "confirm_run": bool(args.confirm_run),
        "worker_agent_id": args.worker_agent_id,
        "hermes_timeout": args.hermes_timeout,
    }
    return client.post(f"/api/commander/work-packages/{args.task_id}/dispatch", payload)


def cmd_commander_coding_evidence(args, client: AgentOpsClient) -> dict:
    payload = {
        "workspace_id": client.workspace_id,
        "run_id": args.run_id,
        "branch": args.branch,
        "collect_from_worktree": bool(args.collect_from_worktree),
        "worktree_root": args.worktree_root,
        "worktree_path": args.worktree_path,
        "confirm_record": bool(args.confirm_record),
        "patch_summary": args.patch_summary,
        "test_summary": args.test_summary,
        "verifier_summary": args.verifier_summary,
        "merge_summary": args.merge_summary,
        "test_status": args.test_status,
        "verifier_status": args.verifier_status,
        "merge_gate_status": args.merge_gate_status,
        "changed_files": args.changed_file or [],
        "verification_commands": args.verification_command or [],
        "actor_id": args.actor_id,
    }
    return client.post(f"/api/commander/work-packages/{args.task_id}/coding-evidence", payload)


def cmd_commander_coding_workspace(args, client: AgentOpsClient) -> dict:
    payload = {
        "workspace_id": client.workspace_id,
        "branch": args.branch,
        "worktree_root": args.worktree_root,
        "worktree_path": args.worktree_path,
        "confirm_create": bool(args.confirm_create),
        "actor_id": args.actor_id,
    }
    return client.post(f"/api/commander/work-packages/{args.task_id}/coding-workspace", payload)


def cmd_commander_coding_workspace_cleanup(args, client: AgentOpsClient) -> dict:
    payload = {
        "workspace_id": client.workspace_id,
        "branch": args.branch,
        "worktree_root": args.worktree_root,
        "worktree_path": args.worktree_path,
        "delete_branch": not bool(args.keep_branch),
        "confirm_cleanup": bool(args.confirm_cleanup),
        "actor_id": args.actor_id,
    }
    return client.post(f"/api/commander/work-packages/{args.task_id}/coding-workspace/cleanup", payload)


def cmd_commander_dispatch_batch(args, client: AgentOpsClient) -> dict:
    payload = {
        "workspace_id": client.workspace_id,
        "project_id": args.project_id,
        "plan_id": args.plan_id,
        "task_ids": args.task_id or [],
        "status": args.status,
        "limit": args.limit,
        "adapter": args.adapter,
        "confirm_run": bool(args.confirm_run),
        "hermes_timeout": args.hermes_timeout,
    }
    result = client.post("/api/commander/work-packages/dispatch-batch", payload)
    if not bool(args.wait):
        return result
    deadline = time.time() + max(int(args.wait_timeout_sec or 1), 1)
    poll_interval = max(float(args.poll_interval or 0.5), 0.2)
    job_ids = [item for item in (result.get("job_ids") or []) if item]
    latest: dict[str, dict] = {}
    while job_ids:
        for job_id in job_ids:
            latest[job_id] = client.get(f"/api/workflows/jobs/{job_id}")
        jobs = [(latest.get(job_id) or {}).get("job") or {} for job_id in job_ids]
        if all(job.get("status") in {"completed", "failed"} for job in jobs):
            break
        if time.time() >= deadline:
            break
        time.sleep(poll_interval)
    jobs = [(latest.get(job_id) or {}).get("job") or {} for job_id in job_ids]
    status_counts: dict[str, int] = {}
    for job in jobs:
        status = str(job.get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
    result["waited"] = True
    result["done"] = bool(jobs) and all(job.get("status") in {"completed", "failed"} for job in jobs)
    result["wait_timeout_sec"] = int(args.wait_timeout_sec or 1)
    result["wait_status_counts"] = status_counts
    result["wait_results"] = jobs
    result["token_omitted"] = True
    return result


def cmd_commander_synthesize(args, client: AgentOpsClient) -> dict:
    payload = {
        "workspace_id": client.workspace_id,
        "project_id": args.project_id,
        "plan_id": args.plan_id,
        "task_ids": args.task_id or [],
        "status": args.status,
        "limit": args.limit,
        "confirm_create": bool(args.confirm_create),
        "artifact_id": args.artifact_id,
    }
    return client.post("/api/commander/work-packages/synthesize", payload)


def cmd_commander_promote_synthesis(args, client: AgentOpsClient) -> dict:
    payload = {
        "workspace_id": client.workspace_id,
        "artifact_id": args.artifact_id,
        "approval_id": args.approval_id,
        "mode": args.mode,
        "confirm_promote": bool(args.confirm_promote),
        "project_id": args.project_id,
        "memory_id": args.memory_id,
        "delivery_artifact_id": args.delivery_artifact_id,
    }
    return client.post("/api/commander/work-packages/synthesis/promote", payload)


def cmd_review_queue(args, client: AgentOpsClient) -> dict:
    return client.get("/api/agent-gateway/review/queue", query={"limit": args.limit})


def cmd_security_production_readiness(args, client: AgentOpsClient) -> dict:
    return client.get("/api/security/production-readiness")


def cmd_agent_register(args, client: AgentOpsClient) -> dict:
    payload = {
        "workspace_id": client.workspace_id,
        "agent_id": args.id or client.agent_id,
        "name": args.name,
        "role": args.role,
        "runtime_type": args.runtime,
        "model_provider": args.model_provider,
        "model_name": args.model_name,
        "permission_level": args.permission_level,
        "allowed_tools": split_csv(args.allowed_tools),
        "budget_limit_usd": args.budget,
        "description": args.description,
    }
    return client.post("/api/agent-gateway/register", payload)


def cmd_agent_heartbeat(args, client: AgentOpsClient) -> dict:
    payload = {
        "workspace_id": client.workspace_id,
        "agent_id": args.id or client.agent_id,
        "status": args.status,
        "summary": args.summary,
        "runtime_type": args.runtime,
    }
    return client.post("/api/agent-gateway/heartbeat", payload)


def cmd_task_pull(args, client: AgentOpsClient) -> dict:
    query = {
        "agent_id": args.agent_id or client.agent_id,
        "workspace_id": client.workspace_id,
        "limit": args.limit,
        "status": args.status,
        "enforce_intake": "true" if args.enforce_intake else None,
        "task_id": args.task_id,
    }
    return client.get("/api/agent-gateway/tasks/pull", query=query)


def cmd_task_create(args, client: AgentOpsClient) -> dict:
    payload = {
        "workspace_id": client.workspace_id,
        "task_id": args.task_id,
        "title": args.title,
        "description": args.description,
        "requester_id": args.requester_id,
        "owner_agent_id": args.owner_agent_id or client.agent_id,
        "collaborator_agent_ids": args.collaborator_agent_id or [],
        "status": args.status,
        "priority": args.priority,
        "due_date": args.due_date,
        "acceptance_criteria": args.acceptance,
        "risk_level": args.risk,
        "budget_limit_usd": args.budget,
    }
    return client.post("/api/agent-gateway/tasks", payload)


def cmd_task_list(args, client: AgentOpsClient) -> dict:
    query = {
        "limit": args.limit,
        "status": args.status,
        "owner_agent_id": args.owner_agent_id,
        "requester_id": args.requester_id,
    }
    return client.get("/api/agent-gateway/tasks", query=query)


def cmd_task_get(args, client: AgentOpsClient) -> dict:
    payload = client.get(f"/api/agent-gateway/tasks/{args.task_id}")
    return {
        "provider": payload.get("provider") or "agentops-mis",
        "operation": "task_get",
        "task_id": args.task_id,
        "task": payload.get("task"),
        "runs": payload.get("runs") or [],
        "approvals": payload.get("approvals") or [],
        "evaluations": payload.get("evaluations") or [],
        "memories": payload.get("memories") or [],
        "artifacts": payload.get("artifacts") or [],
        "evidence": {
            "runs": len(payload.get("runs") or []),
            "approvals": len(payload.get("approvals") or []),
            "evaluations": len(payload.get("evaluations") or []),
            "memories": len(payload.get("memories") or []),
            "artifacts": len(payload.get("artifacts") or []),
        },
        "token_omitted": True,
    }


def cmd_task_claim(args, client: AgentOpsClient) -> dict:
    payload = {
        "workspace_id": client.workspace_id,
        "agent_id": args.agent_id or client.agent_id,
        "runtime_type": args.runtime,
    }
    return client.post(f"/api/agent-gateway/tasks/{args.task_id}/claim", payload)


def cmd_run_start(args, client: AgentOpsClient) -> dict:
    payload = {
        "workspace_id": client.workspace_id,
        "agent_id": args.agent_id or client.agent_id,
        "task_id": args.task_id,
        "runtime_type": args.runtime,
        "input_summary": args.input_summary,
        "delegation_id": args.delegation_id,
        "parent_run_id": args.parent_run_id,
        "approval_required": args.approval_required,
        "agent_plan_id": args.plan_id,
    }
    return client.post("/api/agent-gateway/runs/start", payload)


def cmd_run_list(args, client: AgentOpsClient) -> dict:
    query = {
        "task_id": args.task_id,
        "agent_id": args.agent_id,
        "status": args.status,
        "limit": args.limit,
    }
    return client.get("/api/agent-gateway/runs", query=query)


def cmd_run_get(args, client: AgentOpsClient) -> dict:
    payload = client.get(f"/api/agent-gateway/runs/{args.run_id}")
    return {
        "provider": payload.get("provider") or "agentops-mis",
        "operation": "run_get",
        "run_id": args.run_id,
        "run": payload.get("run"),
        "tool_calls": payload.get("tool_calls") or [],
        "approvals": payload.get("approvals") or [],
        "evaluations": payload.get("evaluations") or [],
        "artifacts": payload.get("artifacts") or [],
        "evidence": {
            "tool_calls": len(payload.get("tool_calls") or []),
            "approvals": len(payload.get("approvals") or []),
            "evaluations": len(payload.get("evaluations") or []),
            "artifacts": len(payload.get("artifacts") or []),
        },
        "token_omitted": True,
    }


def cmd_run_graph(args, client: AgentOpsClient) -> dict:
    payload = client.get(f"/api/agent-gateway/runs/{args.run_id}/graph")
    payload["provider"] = payload.get("provider") or "agentops-mis"
    payload["operation"] = "run_graph"
    payload["token_omitted"] = True
    return payload


def cmd_run_evidence_graph(args, client: AgentOpsClient) -> dict:
    payload = client.get(f"/api/agent-gateway/runs/{args.run_id}/evidence-graph")
    payload["provider"] = payload.get("provider") or "agentops-mis"
    payload["operation"] = "work_delivery_graph_readback"
    payload["token_omitted"] = True
    return payload


def cmd_run_heartbeat(args, client: AgentOpsClient) -> dict:
    payload = {
        "workspace_id": client.workspace_id,
        "status": args.status,
        "output_summary": args.summary,
        "duration_ms": args.duration_ms,
        "output_tokens": args.output_tokens,
        "cost_usd": args.cost,
        "error_type": args.error_type,
        "error_message": args.error_message,
    }
    return client.post(f"/api/agent-gateway/runs/{args.run_id}/heartbeat", payload)


def cmd_runtime_connectors(args, client: AgentOpsClient) -> dict:
    payload = client.get("/api/agent-gateway/host-runtime-connectors")
    return {
        "provider": "agentops-runtime",
        "operation": "runtime_connectors",
        "connectors": payload.get("connectors") or [],
        "auth": payload.get("auth") or {},
        "safety": payload.get("safety") or {},
        "contract": "read-only runtime connector manifest and trust registry view; use worker readiness for route selection and prepared actions for high-risk live side effects",
        "live_execution_performed": False,
        "token_omitted": True,
    }


def cmd_runtime_event_record(args, client: AgentOpsClient) -> dict:
    payload = {
        "workspace_id": client.workspace_id,
        "agent_id": args.agent_id or client.agent_id,
        "run_id": args.run_id,
        "adapter": args.adapter,
        "runtime_connector_id": args.connector_id,
        "event_type": args.event_type,
        "status": args.status,
        "input_summary": args.input_summary,
        "output_summary": args.output_summary or args.summary,
        "error_message": args.error_message,
        "latency_ms": args.latency_ms,
        "model_name": args.model,
        "prompt_hash": args.prompt_hash,
        "raw_payload_hash": args.payload_hash,
        "payload": parse_json_value(args.payload_json, None) if args.payload_json else None,
        "metadata": parse_json_value(args.metadata_json, {}) if args.metadata_json else {},
        "source": args.source,
    }
    return client.post("/api/agent-gateway/runtime-events", payload)


def cmd_toolcall_record(args, client: AgentOpsClient) -> dict:
    payload = {
        "workspace_id": client.workspace_id,
        "run_id": args.run_id,
        "agent_id": args.agent_id or client.agent_id,
        "tool_name": args.tool,
        "tool_category": args.category,
        "risk_level": args.risk,
        "status": args.status,
        "target_resource": args.target,
        "args": parse_json_value(args.args_json, {"summary": args.args_summary or "redacted"}),
        "result_summary": args.summary,
        "prepare_action": bool(args.prepare_action),
        "action_type": args.action_type,
        "policy_version": args.policy_version,
        "checkpoint": parse_json_value(args.checkpoint_json, {}),
        "idempotency_key": args.idempotency_key,
        "approval_reason": args.approval_reason,
        "approver_user_id": args.approver,
    }
    return client.post("/api/agent-gateway/tool-calls", payload)


def cmd_artifact_record(args, client: AgentOpsClient) -> dict:
    payload = {
        "workspace_id": client.workspace_id,
        "run_id": args.run_id,
        "task_id": args.task_id,
        "agent_id": args.agent_id or client.agent_id,
        "artifact_id": args.artifact_id,
        "artifact_type": args.type,
        "title": args.title,
        "uri": args.uri,
        "summary": args.summary,
        "content_hash": args.content_hash,
    }
    return client.post("/api/agent-gateway/artifacts", payload)


def cmd_artifact_list(args, client: AgentOpsClient) -> dict:
    query = {
        "task_id": args.task_id,
        "run_id": args.run_id,
        "type": args.type,
        "limit": args.limit,
    }
    return client.get("/api/agent-gateway/artifacts", query=query)


def cmd_experiment(args, client: AgentOpsClient) -> dict:
    from .research_lab import run_experiment_command

    return run_experiment_command(
        args,
        base_url=client.base_url,
        workspace_id=client.workspace_id,
        api_key=client.api_key,
    )


def cmd_knowledge_search(args, client: AgentOpsClient) -> dict:
    return client.get("/api/agent-gateway/knowledge/search", query={
        "q": args.query,
        "limit": args.limit,
        "refresh": "true" if args.refresh else None,
    })


def cmd_knowledge_index(args, client: AgentOpsClient) -> dict:
    return client.post("/api/agent-gateway/knowledge/index", {"rebuild": bool(args.rebuild)})


def cmd_knowledge_evidence_packet(args, client: AgentOpsClient) -> dict:
    return client.get("/api/agent-gateway/knowledge/evidence-packet", query={
        "q": args.query,
        "task_id": args.task_id,
        "adapter": args.adapter,
        "limit": args.limit,
        "baseline_limit": args.baseline_limit,
    })


def cmd_knowledge_context_packet(args, client: AgentOpsClient) -> dict:
    return client.get("/api/agent-gateway/knowledge/context-packet", query={
        "q": args.query,
        "task_id": args.task_id,
        "adapter": args.adapter,
        "limit": args.limit,
        "memory_limit": args.memory_limit,
        "block_chars": args.block_chars,
        "max_chars": args.max_chars,
    })


def cmd_agent_plan_create(args, client: AgentOpsClient) -> dict:
    payload = {
        "workspace_id": client.workspace_id,
        "agent_id": args.agent_id or client.agent_id,
        "task_id": args.task_id,
        "run_id": args.run_id,
        "task_understanding": args.task_understanding,
        "referenced_specs": split_csv(args.referenced_specs),
        "referenced_memories": split_csv(args.referenced_memories),
        "referenced_bases": split_csv(args.referenced_bases),
        "proposed_files_to_change": split_csv(args.proposed_files_to_change),
        "risk_level": args.risk,
        "approval_required": bool(args.approval_required),
        "execution_steps": parse_json_value(args.execution_steps_json, split_csv(args.execution_steps)),
        "verification_plan": args.verification_plan,
        "rollback_plan": args.rollback_plan,
        "status": args.status,
    }
    return client.post("/api/agent-gateway/agent-plans", payload)


def cmd_agent_plan_list(args, client: AgentOpsClient) -> dict:
    return client.get("/api/agent-gateway/agent-plans", query={
        "task_id": args.task_id,
        "run_id": args.run_id,
        "agent_id": args.agent_id,
        "limit": args.limit,
    })


def cmd_agent_plan_get(args, client: AgentOpsClient) -> dict:
    return client.get(f"/api/agent-gateway/agent-plans/{args.plan_id}")


def cmd_agent_plan_verify(args, client: AgentOpsClient) -> dict:
    return client.get(f"/api/agent-gateway/agent-plans/{args.plan_id}/verify")


def cmd_agent_plan_approve(args, client: AgentOpsClient) -> dict:
    return client.post(f"/api/agent-plans/{args.plan_id}/approve", {
        "workspace_id": client.workspace_id,
        "approver_user_id": args.approver_user_id,
        "actor_type": args.actor_type,
        "reason": args.reason,
    })


def cmd_agent_plan_reject(args, client: AgentOpsClient) -> dict:
    return client.post(f"/api/agent-plans/{args.plan_id}/reject", {
        "workspace_id": client.workspace_id,
        "approver_user_id": args.approver_user_id,
        "actor_type": args.actor_type,
        "reason": args.reason,
    })


def cmd_plan_evidence_create(args, client: AgentOpsClient) -> dict:
    payload = {
        "workspace_id": client.workspace_id,
        "agent_id": args.agent_id or client.agent_id,
        "manifest_id": args.manifest_id,
        "plan_id": args.plan_id,
        "task_id": args.task_id,
        "run_id": args.run_id,
        "mismatch_policy": args.mismatch_policy,
        "expected_steps": parse_json_value(args.expected_steps_json, split_csv(args.expected_steps)),
        "tool_call_ids": split_csv(args.tool_call_ids),
        "evaluation_ids": split_csv(args.evaluation_ids),
        "artifact_ids": split_csv(args.artifact_ids),
        "audit_ids": split_csv(args.audit_ids),
        "verify_now": not args.no_verify,
    }
    return client.post("/api/agent-gateway/plan-evidence-manifests", payload)


def cmd_plan_evidence_list(args, client: AgentOpsClient) -> dict:
    return client.get("/api/agent-gateway/plan-evidence-manifests", query={
        "plan_id": args.plan_id,
        "task_id": args.task_id,
        "run_id": args.run_id,
        "agent_id": args.agent_id,
        "limit": args.limit,
    })


def cmd_plan_evidence_get(args, client: AgentOpsClient) -> dict:
    return client.get(f"/api/agent-gateway/plan-evidence-manifests/{args.manifest_id}")


def cmd_plan_evidence_verify(args, client: AgentOpsClient) -> dict:
    return client.get(f"/api/agent-gateway/plan-evidence-manifests/{args.manifest_id}/verify")


def cmd_approval_request(args, client: AgentOpsClient) -> dict:
    payload = {
        "workspace_id": client.workspace_id,
        "task_id": args.task_id,
        "run_id": args.run_id,
        "tool_call_id": args.tool_call_id,
        "requested_by_agent_id": args.agent_id or client.agent_id,
        "reason": args.reason,
        "approver_user_id": args.approver,
    }
    return client.post("/api/agent-gateway/approvals/request", payload)


def cmd_approval_list(args, client: AgentOpsClient) -> dict:
    payload = client.get("/api/agent-gateway/approvals", query={
        "decision": args.decision,
        "task_id": args.task_id,
        "run_id": args.run_id,
        "limit": args.limit,
    })
    rows = payload.get("approvals") if isinstance(payload, dict) else []
    if not isinstance(rows, list):
        rows = []
    return {
        "provider": "agentops-approval",
        "operation": "approval_list",
        "approvals": rows,
        "total": payload.get("count", len(rows)) if isinstance(payload, dict) else len(rows),
        "limit": args.limit,
        "filters": {
            "decision": args.decision,
            "task_id": args.task_id,
            "run_id": args.run_id,
        },
        "gateway_scope": payload.get("gateway_scope") if isinstance(payload, dict) else None,
        "token_omitted": True,
    }


def cmd_approval_inspect(args, client: AgentOpsClient) -> dict:
    return client.get(f"/api/agent-gateway/approvals/{args.approval_id}")


def cmd_approval_prepared_action_create(args, client: AgentOpsClient) -> dict:
    payload = {
        "workspace_id": client.workspace_id,
        "run_id": args.run_id,
        "tool_call_id": args.tool_call_id,
        "requested_by_agent_id": args.agent_id or client.agent_id,
        "action_type": args.action_type,
        "normalized_args_json": parse_json_value(args.args_json, {}),
        "target_resource": args.target_resource,
        "risk_level": args.risk_level,
        "policy_version": args.policy_version,
        "checkpoint": parse_json_value(args.checkpoint_json, {}),
        "idempotency_key": args.idempotency_key,
        "reason": args.reason,
        "approver_user_id": args.approver,
    }
    return client.post("/api/agent-gateway/prepared-actions", payload)


def cmd_approval_prepared_action_get(args, client: AgentOpsClient) -> dict:
    return client.get(f"/api/agent-gateway/prepared-actions/{args.action_id}")


def cmd_approval_prepared_action_resume(args, client: AgentOpsClient) -> dict:
    payload = {
        "workspace_id": client.workspace_id,
        "agent_id": args.agent_id or client.agent_id,
        "provider_side_effect_id": args.provider_side_effect_id,
        "result_summary": args.result_summary,
    }
    return client.post(f"/api/agent-gateway/prepared-actions/{args.action_id}/resume", payload)


def cmd_approval_decide(args, client: AgentOpsClient) -> dict:
    action = "approve" if args.handler == "approval_approve" else "reject"
    response = client.post(f"/api/approvals/{args.approval_id}/{action}", {})
    approval = response.get("approval") if isinstance(response.get("approval"), dict) else response
    prepared_action = response.get("prepared_action") if isinstance(response.get("prepared_action"), dict) else None
    return {
        "provider": "agentops-approval",
        "operation": f"approval_{action}",
        "approval": approval,
        "prepared_action": prepared_action,
        "resume_required": bool(response.get("resume_required")) if isinstance(response, dict) else False,
        "approval_id": approval.get("approval_id") or args.approval_id,
        "decision": approval.get("decision"),
        "task_id": approval.get("task_id"),
        "run_id": approval.get("run_id"),
        "token_omitted": True,
    }


def cmd_memory_propose(args, client: AgentOpsClient) -> dict:
    payload = {
        "workspace_id": client.workspace_id,
        "agent_id": args.agent_id or client.agent_id,
        "task_id": args.task_id,
        "run_id": args.run_id,
        "scope": args.scope,
        "memory_type": args.type,
        "canonical_text": args.text,
        "source_ref": args.source_ref or args.run_id,
        "access_tags": split_csv(args.access_tags),
        "confidence": args.confidence,
    }
    return client.post("/api/agent-gateway/memories/propose", payload)


def cmd_memory_list(args, client: AgentOpsClient) -> dict:
    payload = client.get("/api/agent-gateway/memories", query={
        "status": args.status,
        "scope": args.scope,
        "type": args.type,
        "task_id": args.task_id,
        "agent_id": args.agent_id,
        "limit": args.limit,
    })
    rows = payload.get("memories") if isinstance(payload, dict) else []
    if not isinstance(rows, list):
        rows = []
    return {
        "provider": "agentops-memory",
        "operation": "memory_list",
        "memories": rows,
        "total": payload.get("count", len(rows)) if isinstance(payload, dict) else len(rows),
        "limit": args.limit,
        "filters": {
            "status": args.status,
            "scope": args.scope,
            "type": args.type,
            "task_id": args.task_id,
            "agent_id": args.agent_id,
        },
        "gateway_scope": payload.get("gateway_scope") if isinstance(payload, dict) else None,
        "token_omitted": True,
    }


def cmd_memory_decide(args, client: AgentOpsClient) -> dict:
    action = "approve" if args.handler == "memory_approve" else "reject"
    memory = client.post(f"/api/memories/{args.memory_id}/{action}", {})
    return {
        "provider": "agentops-memory",
        "operation": f"memory_{action}",
        "memory": memory,
        "memory_id": memory.get("memory_id") or args.memory_id,
        "review_status": memory.get("review_status"),
        "task_id": memory.get("task_id"),
        "agent_id": memory.get("agent_id"),
        "token_omitted": True,
    }


def cmd_eval_submit(args, client: AgentOpsClient) -> dict:
    payload = {
        "workspace_id": client.workspace_id,
        "run_id": args.run_id,
        "task_id": args.task_id,
        "agent_id": args.agent_id or client.agent_id,
        "evaluator_type": args.evaluator_type,
        "score": args.score,
        "pass_fail": "pass" if args.passed else "fail",
        "rubric": parse_json_value(args.rubric_json, {"gate": args.gate}),
        "notes": args.notes,
    }
    return client.post("/api/agent-gateway/evaluations/submit", payload)


def cmd_eval_cases(args, client: AgentOpsClient) -> dict:
    return client.get("/api/evaluation-cases", query={
        "workspace_id": client.workspace_id,
        "status": args.status,
        "limit": args.limit,
        "run_id": args.run_id,
        "task_id": args.task_id,
        "artifact_id": args.artifact_id,
    })


def cmd_eval_case_runs(args, client: AgentOpsClient) -> dict:
    return client.get("/api/evaluation-case-runs", query={
        "workspace_id": client.workspace_id,
        "limit": args.limit,
        "case_id": args.case_id,
        "run_id": args.run_id,
        "task_id": args.task_id,
        "pass_fail": args.pass_fail,
        "review_status": args.review_status,
    })


def cmd_eval_review_case_run(args, client: AgentOpsClient) -> dict:
    return client.post(f"/api/evaluation-case-runs/{args.case_run_id}/review", {
        "workspace_id": client.workspace_id,
        "review_status": args.status,
        "review_note": args.note,
        "reviewed_by_user_id": args.actor_id,
    })


def cmd_eval_remediate_case_run(args, client: AgentOpsClient) -> dict:
    return client.post(f"/api/evaluation-case-runs/{args.case_run_id}/remediation-task", {
        "workspace_id": client.workspace_id,
        "task_id": args.task_id,
        "title": args.title,
        "project_id": args.project_id,
        "plan_id": args.plan_id,
        "lane_id": args.lane_id,
        "owner_agent_id": args.owner_agent_id,
        "priority": args.priority,
        "risk_level": args.risk_level,
        "budget_limit_usd": args.budget_limit_usd,
        "actor_id": args.actor_id,
        "confirm_create": bool(args.confirm_create),
    })


def cmd_eval_propose_case(args, client: AgentOpsClient) -> dict:
    payload = {
        "workspace_id": client.workspace_id,
        "case_id": args.case_id,
        "source_type": args.source_type,
        "source_ref": args.source_ref,
        "evaluation_id": args.evaluation_id,
        "artifact_id": args.artifact_id,
        "run_id": args.run_id,
        "task_id": args.task_id,
        "agent_id": args.agent_id,
        "case_type": args.case_type,
        "title": args.title,
        "input_summary": args.input_summary,
        "expected_output_summary": args.expected_output_summary,
        "failure_mode": args.failure_mode,
        "confidence": args.confidence,
        "rubric": parse_json_value(args.rubric_json, None),
        "confirm_create": bool(args.confirm_create),
    }
    return client.post("/api/evaluation-cases/propose", payload)


def cmd_eval_review_case(args, client: AgentOpsClient) -> dict:
    action = "approve" if args.handler == "eval_approve_case" else "reject"
    return client.post(f"/api/evaluation-cases/{args.case_id}/{action}", {})


def cmd_eval_run_cases(args, client: AgentOpsClient) -> dict:
    payload = {
        "workspace_id": client.workspace_id,
        "case_ids": args.case_id or [],
        "case_type": args.case_type,
        "status": args.status,
        "runner_type": args.runner_type,
        "agent_id": args.agent_id,
        "task_id": args.task_id,
        "run_id": args.run_id,
        "artifact_id": args.artifact_id,
        "limit": args.limit,
        "min_score": args.min_score,
        "confirm_run": bool(args.confirm_run),
    }
    return client.post("/api/evaluation-cases/run", payload)


def cmd_audit_emit(args, client: AgentOpsClient) -> dict:
    payload = {
        "workspace_id": client.workspace_id,
        "agent_id": args.agent_id or client.agent_id,
        "action": args.action,
        "entity_type": args.entity_type,
        "entity_id": args.entity_id,
        "task_id": args.task_id,
        "run_id": args.run_id,
        "metadata": parse_json_value(args.metadata_json, {}),
    }
    return client.post("/api/agent-gateway/audit", payload)


def cmd_workflow_customer_worker_task(args, client: AgentOpsClient) -> dict:
    payload = {
        "adapter": args.adapter,
        "confirm_run": bool(args.confirm_run),
        "title": args.title,
        "description": args.description,
        "acceptance_criteria": args.acceptance,
        "task_id": args.task_id,
        "priority": args.priority,
        "risk_level": args.risk,
        "selected_agent_ids": args.selected_agent_id or [],
        "worker_agent_id": args.worker_agent_id,
        "hermes_timeout": args.hermes_timeout,
        "hermes_max_tokens": args.hermes_max_tokens,
        "adapter_max_attempts": args.adapter_max_attempts,
        "adapter_retry_delay_sec": args.adapter_retry_delay_sec,
        "external_write_intent": bool(args.external_write_intent),
        "target_resource": args.target_resource,
        "external_action_type": args.external_action_type,
        "approval_reason": args.approval_reason,
    }
    endpoint = "/api/workflows/customer-worker-task/submit" if args.async_job else "/api/workflows/customer-worker-task"
    return client.post(endpoint, payload)


def cmd_workflow_templates(args, client: AgentOpsClient) -> dict:
    return client.get("/api/workflows/customer-task-templates")


def cmd_workflow_delivery_board(args, client: AgentOpsClient) -> dict:
    return client.get("/api/workflows/customer-delivery-board", query={"limit": args.limit})


def cmd_workflow_hermes_openclaw_loop(args, client: AgentOpsClient) -> dict:
    if args.readback:
        return client.get("/api/workflows/hermes-openclaw-loop", query={"loop_id": args.loop_id or "", "limit": args.limit})
    payload = {
        "workspace_id": client.workspace_id,
        "topic": args.topic,
        "rounds": args.rounds,
        "mode": args.mode,
        "confirm_live": bool(args.confirm_live),
        "loop_id": args.loop_id,
        "resume": bool(args.resume),
        "order": args.order,
        "request_timeout": args.request_timeout,
        "max_agent_attempts": args.max_agent_attempts,
        "retry_delay_sec": args.retry_delay_sec,
        "simulate_failure_agent": args.simulate_failure_agent or [],
    }
    return client.post("/api/workflows/hermes-openclaw-loop", payload)


def cmd_workflow_run_template(args, client: AgentOpsClient) -> dict:
    if args.adapter in {"hermes", "openclaw"} and args.confirm_run:
        minimum_timeout = (int(args.hermes_timeout or 300) + 60) if args.adapter == "hermes" else 240
        client.request_timeout = max(client.request_timeout, minimum_timeout)
    payload = {
        "template_id": args.template_id,
        "confirm_run": bool(args.confirm_run),
        "selected_agent_ids": args.selected_agent_id or [],
    }
    if args.adapter:
        payload["adapter"] = args.adapter
    if args.title:
        payload["title"] = args.title
    if args.description:
        payload["description"] = args.description
    if args.acceptance:
        payload["acceptance_criteria"] = args.acceptance
    if args.priority:
        payload["priority"] = args.priority
    if args.risk:
        payload["risk_level"] = args.risk
    if args.owner_agent_id:
        payload["owner_agent_id"] = args.owner_agent_id
    if args.worker_agent_id:
        payload["worker_agent_id"] = args.worker_agent_id
    if args.hermes_timeout:
        payload["hermes_timeout"] = args.hermes_timeout
    endpoint = "/api/workflows/customer-task-templates/submit" if args.async_job else "/api/workflows/customer-task-templates/run"
    return client.post(endpoint, payload)


def cmd_workflow_job_status(args, client: AgentOpsClient) -> dict:
    deadline = time.time() + max(args.timeout, 1)
    result = client.get(f"/api/workflows/jobs/{args.job_id}")
    while args.wait and (result.get("job") or {}).get("status") in {"queued", "running"} and time.time() < deadline:
        time.sleep(max(args.poll_interval, 0.2))
        result = client.get(f"/api/workflows/jobs/{args.job_id}")
    job = result.get("job") or {}
    result["waited"] = bool(args.wait)
    result["done"] = job.get("status") in {"completed", "failed"}
    result["token_omitted"] = True
    return result


def cmd_workflow_jobs(args, client: AgentOpsClient) -> dict:
    query = {"limit": args.limit}
    if args.status:
        query["status"] = args.status
    if args.workflow_type:
        query["workflow_type"] = args.workflow_type
    result = client.get("/api/workflows/jobs", query=query)
    result["read_only"] = True
    result["token_omitted"] = True
    return result


def cmd_workflow_stuck_jobs(args, client: AgentOpsClient) -> dict:
    return client.get("/api/workflows/jobs/stuck", query={"threshold_sec": args.threshold_sec, "limit": args.limit})


def cmd_workflow_job_mark_failed(args, client: AgentOpsClient) -> dict:
    return client.post(
        f"/api/workflows/jobs/{args.job_id}/mark-failed",
        {"reason": args.reason, "actor_id": args.actor_id},
    )


def cmd_workflow_recover_job(args, client: AgentOpsClient) -> dict:
    payload = {
        "mode": args.mode,
        "reason": args.reason,
        "task_id": args.task_id,
        "adapter": args.adapter,
        "actor_id": args.actor_id,
        "confirm_recover": bool(args.confirm_recover),
        "record_receipt": bool(args.record_receipt),
        "confirm_run": bool(args.confirm_run),
        "hermes_timeout": args.hermes_timeout,
    }
    return client.post(f"/api/workflows/jobs/{args.job_id}/recover", payload)


def cmd_workflow_run_task(args, client: AgentOpsClient) -> dict:
    from . import worker as worker_mod

    worker_agent_id = args.worker_agent_id or client.agent_id or f"agt_cli_workflow_{args.adapter}_{now_stamp()}_{uuid.uuid4().hex[:6]}"
    register_result = None
    register_error = None
    try:
        register_result = client.post("/api/agent-gateway/register", {
            "workspace_id": client.workspace_id,
            "agent_id": worker_agent_id,
            "name": args.worker_name or f"{args.adapter} Workflow Worker",
            "role": "Workflow Task Worker",
            "runtime_type": args.adapter,
            "model_provider": args.adapter,
            "model_name": args.adapter,
            "description": "Registered by agentops workflow run-task.",
        })
    except RuntimeError as exc:
        register_error = str(exc)
    if args.adapter in {"hermes", "openclaw", "codex"} and not args.confirm_run:
        created = client.post("/api/agent-gateway/tasks", {
            "workspace_id": client.workspace_id,
            "task_id": args.task_id,
            "title": args.title,
            "description": args.description,
            "requester_id": args.requester_id,
            "owner_agent_id": worker_agent_id,
            "status": "planned",
            "priority": args.priority,
            "acceptance_criteria": args.acceptance,
            "risk_level": args.risk,
            "budget_limit_usd": args.budget,
        })
        return {
            "ok": False,
            "dry_run": True,
            "provider": "agentops-worker",
            "workflow": "run_task",
            "adapter": args.adapter,
            "task_id": created.get("task_id"),
            "agent_id": worker_agent_id,
            "reason": "confirm_run_required_for_live_adapter",
            "requires": {"confirm_run": True},
            "created_task": created,
            "agent_register": register_result,
            "agent_register_error": register_error,
            "token_omitted": True,
        }

    created = client.post("/api/agent-gateway/tasks", {
        "workspace_id": client.workspace_id,
        "task_id": args.task_id,
        "title": args.title,
        "description": args.description,
        "requester_id": args.requester_id,
        "owner_agent_id": worker_agent_id,
        "status": "planned",
        "priority": args.priority,
        "acceptance_criteria": args.acceptance,
        "risk_level": args.risk,
        "budget_limit_usd": args.budget,
    })
    task_id = created.get("task_id")
    worker_api_key = "" if client.stale_config_token_ignored and client.sources.get("api_key") == "config" else client.api_key
    worker_api_key_source = "missing" if not worker_api_key else client.sources.get("api_key") or "env"
    worker_argv = [
        "--base-url",
        client.base_url,
        "--workspace-id",
        client.workspace_id,
        "--agent-id",
        worker_agent_id,
        "--task-id",
        task_id,
        "--api-key",
        worker_api_key,
        "--api-key-source",
        worker_api_key_source,
        "--adapter",
        args.adapter,
        "--once",
        "--no-enforce-intake",
        "--status",
        "planned",
        "--adapter-max-attempts",
        str(args.adapter_max_attempts),
        "--adapter-retry-delay-sec",
        str(args.adapter_retry_delay_sec),
    ]
    if args.confirm_run:
        worker_argv.append("--confirm-run")
    if args.use_session:
        worker_argv.extend(["--use-session", "--session-ttl-sec", str(args.session_ttl_sec)])
    if args.hermes_gateway_url:
        worker_argv.extend(["--hermes-gateway-url", args.hermes_gateway_url])
    if args.hermes_timeout is not None:
        worker_argv.extend(["--hermes-timeout", str(args.hermes_timeout)])
    if args.hermes_max_tokens is not None:
        worker_argv.extend(["--hermes-max-tokens", str(args.hermes_max_tokens)])
    if args.openclaw_bin:
        worker_argv.extend(["--openclaw-bin", args.openclaw_bin])
    if args.openclaw_timeout is not None:
        worker_argv.extend(["--openclaw-timeout", str(args.openclaw_timeout)])
    if args.codex_bin:
        worker_argv.extend(["--codex-bin", args.codex_bin])
    if args.codex_timeout is not None:
        worker_argv.extend(["--codex-timeout", str(args.codex_timeout)])

    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        exit_code = worker_mod.main(worker_argv)
    raw_worker_output = stdout.getvalue().strip()
    try:
        worker_result = json.loads(raw_worker_output) if raw_worker_output else {}
    except json.JSONDecodeError:
        worker_result = {"raw_output_summary": raw_worker_output[:500]}

    first_result = ((worker_result.get("results") or [{}])[0] or {})
    run_id = first_result.get("run_id")
    run_detail = client.get(f"/api/agent-gateway/runs/{run_id}") if run_id else None
    task_detail = client.get(f"/api/agent-gateway/tasks/{task_id}") if task_id else None
    run = (run_detail or {}).get("run") or {}
    plan_id = first_result.get("plan_id") or run.get("agent_plan_id")
    plan_verify = client.get(f"/api/agent-gateway/agent-plans/{plan_id}/verify") if plan_id else None
    plan_verification = (plan_verify or {}).get("verification") or {}
    agent_plan = (plan_verify or {}).get("agent_plan") or {}
    manifest_id = first_result.get("plan_evidence_manifest_id")
    if not manifest_id and run_id:
        manifest_list = client.get("/api/agent-gateway/plan-evidence-manifests", query={"run_id": run_id, "limit": 1})
        manifests = manifest_list.get("manifests") or []
        manifest_id = (manifests[0] or {}).get("manifest_id") if manifests else None
    manifest_verify = client.get(f"/api/agent-gateway/plan-evidence-manifests/{manifest_id}/verify") if manifest_id else None
    manifest_verification = (manifest_verify or {}).get("verification") or {}
    manifest = (manifest_verify or {}).get("manifest") or {}
    agent_plan_readback = {
        "plan_id": plan_id,
        "status": agent_plan.get("status"),
        "verified": bool(plan_verification.get("pass")),
        "verification_status": plan_verification.get("status"),
        "failed_checks": [item.get("id") for item in plan_verification.get("failed_checks") or []],
        "token_omitted": True,
    }
    plan_evidence_readback = {
        "manifest_id": manifest_id,
        "status": manifest_verification.get("status") or manifest.get("status") or first_result.get("plan_evidence_status"),
        "verified": bool(manifest_verification.get("pass")),
        "evidence_counts": manifest_verification.get("evidence_counts") or {},
        "failed_checks": [item.get("id") for item in manifest_verification.get("failed_checks") or []],
        "token_omitted": True,
    }
    evidence = {
        "tool_calls": len((run_detail or {}).get("tool_calls") or []),
        "evaluations": len((run_detail or {}).get("evaluations") or []),
        "approvals": len((run_detail or {}).get("approvals") or []),
        "artifacts": len((run_detail or {}).get("artifacts") or []),
    }
    return {
        "ok": bool(exit_code == 0 and worker_result.get("ok") is True and run_id),
        "dry_run": False,
        "provider": "agentops-worker",
        "workflow": "run_task",
        "adapter": args.adapter,
        "agent_id": worker_agent_id,
        "task_id": task_id,
        "run_id": run_id,
        "worker_exit_code": exit_code,
        "worker_processed": worker_result.get("processed"),
        "run_status": run.get("status"),
        "task_status": ((task_detail or {}).get("task") or {}).get("status"),
        "readback": {
            "run_provider": (run_detail or {}).get("provider"),
            "task_provider": (task_detail or {}).get("provider"),
            "required_scope": "tasks:read",
            "agent_plan_id": plan_id,
            "agent_plan_verified": agent_plan_readback["verified"],
            "plan_evidence_manifest_id": manifest_id,
            "plan_evidence_verified": plan_evidence_readback["verified"],
        },
        "agent_plan": agent_plan_readback,
        "plan_evidence": plan_evidence_readback,
        "evidence": evidence,
        "created_task": created,
        "agent_register": register_result,
        "agent_register_error": register_error,
        "worker_result": worker_result,
        "raw_worker_output_omitted": True,
        "token_omitted": True,
    }


def cmd_workflow_codex_workspace_write(args, client: AgentOpsClient) -> dict:
    from . import worker as worker_mod

    if not args.confirm_run:
        return {
            "ok": False,
            "dry_run": True,
            "workflow": "codex_workspace_write",
            "reason": "confirm_run_required",
            "requires": ["--confirm-run"],
            "live_execution_performed": False,
            "token_omitted": True,
        }
    if not args.allow_path:
        raise RuntimeError("codex workspace-write requires at least one --allow-path")

    action = None
    task = None
    task_id = args.task_id
    worker_agent_id = args.worker_agent_id
    if args.prepared_action_id:
        action_payload = client.get(f"/api/agent-gateway/prepared-actions/{args.prepared_action_id}")
        action = action_payload.get("prepared_action") or {}
        task_id = action.get("task_id")
        worker_agent_id = action.get("requested_by_agent_id")
        if action.get("action_type") != "agent_worker.codex.workspace_write":
            raise RuntimeError("prepared action is not a Codex workspace-write authorization")
    elif task_id:
        try:
            task = (client.get(f"/api/agent-gateway/tasks/{task_id}").get("task") or {})
            worker_agent_id = worker_agent_id or task.get("owner_agent_id")
        except RuntimeError:
            task = None

    worker_agent_id = worker_agent_id or client.agent_id or f"agt_codex_write_{now_stamp()}_{uuid.uuid4().hex[:6]}"
    register_result = client.post("/api/agent-gateway/register", {
        "workspace_id": client.workspace_id,
        "agent_id": worker_agent_id,
        "name": args.worker_name or "Codex Workspace Write Worker",
        "role": "Governed Coding Worker",
        "runtime_type": "codex",
        "model_provider": "codex",
        "model_name": "codex-cli",
        "description": "Approval-gated Codex worker using managed detached Git worktrees.",
    })
    if not task_id:
        if not args.title or not args.description:
            raise RuntimeError("--title and --description are required when creating a Codex workspace-write task")
        created = client.post("/api/agent-gateway/tasks", {
            "workspace_id": client.workspace_id,
            "title": args.title,
            "description": args.description,
            "requester_id": args.requester_id,
            "owner_agent_id": worker_agent_id,
            "status": "planned",
            "priority": args.priority,
            "acceptance_criteria": args.acceptance,
            "risk_level": args.risk,
            "budget_limit_usd": args.budget,
        })
        task_id = created.get("task_id")
        task = created
    elif task is None and not action:
        if not args.title or not args.description:
            raise RuntimeError("task_id was not found; provide --title and --description to create it")
        created = client.post("/api/agent-gateway/tasks", {
            "workspace_id": client.workspace_id,
            "task_id": task_id,
            "title": args.title,
            "description": args.description,
            "requester_id": args.requester_id,
            "owner_agent_id": worker_agent_id,
            "status": "planned",
            "priority": args.priority,
            "acceptance_criteria": args.acceptance,
            "risk_level": args.risk,
            "budget_limit_usd": args.budget,
        })
        task = created

    worker_api_key = "" if client.stale_config_token_ignored and client.sources.get("api_key") == "config" else client.api_key
    worker_argv = [
        "--base-url", client.base_url,
        "--workspace-id", client.workspace_id,
        "--agent-id", worker_agent_id,
        "--task-id", task_id,
        "--api-key", worker_api_key,
        "--api-key-source", "missing" if not worker_api_key else client.sources.get("api_key") or "env",
        "--adapter", "codex",
        "--codex-mode", "workspace-write",
        "--codex-source-repo", str(Path(args.source_repo).expanduser().resolve()),
        "--codex-timeout", str(args.codex_timeout),
        "--once",
        "--no-enforce-intake",
        "--status", "planned",
        "--confirm-run",
    ]
    for path in args.allow_path:
        worker_argv.extend(["--codex-allowed-path", path])
    if args.codex_bin:
        worker_argv.extend(["--codex-bin", args.codex_bin])
    if args.worktree_root:
        worker_argv.extend(["--codex-worktree-root", args.worktree_root])
    if args.prepared_action_id:
        worker_argv.extend(["--codex-prepared-action-id", args.prepared_action_id])
    if args.confirm_workspace_write:
        worker_argv.append("--confirm-workspace-write")
    if args.allow_high_risk:
        worker_argv.append("--allow-high-risk")

    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        exit_code = worker_mod.main(worker_argv)
    raw_worker_output = stdout.getvalue().strip()
    try:
        worker_result = json.loads(raw_worker_output) if raw_worker_output else {}
    except json.JSONDecodeError:
        worker_result = {"ok": False, "raw_output_omitted": True}
    first_result = ((worker_result.get("results") or [{}])[0] or {})
    return {
        "ok": bool(exit_code == 0 and worker_result.get("ok") is True),
        "dry_run": False,
        "workflow": "codex_workspace_write",
        "phase": (
            "completed" if first_result.get("processed") and first_result.get("ok")
            else "workspace_write_approval" if first_result.get("prepared_action_id")
            else "agent_plan_approval" if first_result.get("approval_id")
            else "blocked"
        ),
        "task_id": task_id,
        "agent_id": worker_agent_id,
        "run_id": first_result.get("run_id"),
        "plan_id": first_result.get("plan_id"),
        "approval_id": first_result.get("approval_id"),
        "prepared_action_id": first_result.get("prepared_action_id"),
        "worker_exit_code": exit_code,
        "worker_result": worker_result,
        "agent_register": register_result,
        "source_repo_path_hash": hashlib.sha256(str(Path(args.source_repo).expanduser().resolve()).encode("utf-8")).hexdigest(),
        "allowed_paths": args.allow_path,
        "main_worktree_mutated": False,
        "raw_worker_output_omitted": True,
        "token_omitted": True,
    }


def cmd_worker_stuck(args, client: AgentOpsClient) -> dict:
    return client.get("/api/agent-gateway/host-workers/stuck-tasks", query={"threshold_sec": args.threshold_sec, "limit": args.limit})


def cmd_worker_status(args, client: AgentOpsClient) -> dict:
    return client.get("/api/agent-gateway/host-workers/status")


def cmd_worker_fleet(args, client: AgentOpsClient) -> dict:
    return client.get("/api/agent-gateway/host-workers/fleet")


def cmd_worker_readiness(args, client: AgentOpsClient) -> dict:
    return client.get("/api/agent-gateway/host-workers/adapter-readiness")


def cmd_worker_logs(args, client: AgentOpsClient) -> dict:
    return client.get("/api/workers/local/logs", query={"adapter": args.adapter})


def cmd_worker_preflight(args, client: AgentOpsClient) -> dict:
    from . import worker as worker_mod

    check_args = argparse.Namespace(
        base_url=client.base_url,
        workspace_id=client.workspace_id,
        agent_id=args.agent_id or client.agent_id or worker_mod.DEFAULT_AGENT_ID,
        api_key=client.api_key,
        api_key_source=client.sources.get("api_key") or ("env" if client.api_key else "missing"),
        adapter=args.adapter,
        timeout=args.timeout,
        hermes_gateway_url=args.hermes_gateway_url,
        openclaw_bin=args.openclaw_bin,
        codex_bin=args.codex_bin,
    )
    gateway = worker_mod.check_gateway_preflight(check_args)
    adapter = worker_mod.check_adapter_preflight(check_args)
    return {
        "provider": "agentops-worker",
        "command": "agentops worker preflight",
        "ok": bool(gateway.get("ok") and adapter.get("ok")),
        "adapter": args.adapter,
        "base_url": client.base_url,
        "workspace_id": client.workspace_id,
        "agent_id": check_args.agent_id,
        "gateway_preflight": gateway,
        "adapter_preflight": adapter,
        "live_execution_performed": False,
        "token_omitted": True,
    }


def cmd_worker_service_check(args, client: AgentOpsClient) -> dict:
    from . import worker as worker_mod

    check_args = argparse.Namespace(
        manager=args.manager,
        base_url=client.base_url,
        workspace_id=client.workspace_id,
        agent_id=args.agent_id or client.agent_id or worker_mod.DEFAULT_AGENT_ID,
        adapter=args.adapter,
        confirm_run=bool(args.confirm_run),
        use_session=bool(args.use_session),
        session_ttl_sec=args.session_ttl_sec,
        session_refresh_margin_sec=args.session_refresh_margin_sec,
        poll_interval=args.poll_interval,
        label=args.label or "",
        working_directory=args.working_directory or str(worker_mod.DEFAULT_WORKER_CWD),
        runtime_dir=args.runtime_dir or "",
        worker_command=args.worker_command or "",
        hermes_gateway_url=args.hermes_gateway_url or "",
        codex_bin=args.codex_bin or "",
        service_path=args.service_path or "",
        api_key_placeholder=args.api_key_placeholder,
        credential_source=args.credential_source,
        config_path=args.config_path,
        timeout=args.timeout,
    )
    payload = worker_mod.check_service_installation(check_args)
    payload["command"] = "agentops worker service-check"
    return payload


def cmd_worker_service_install(args, client: AgentOpsClient) -> dict:
    from . import worker as worker_mod

    install_args = argparse.Namespace(
        manager=args.manager,
        base_url=client.base_url,
        workspace_id=client.workspace_id,
        agent_id=args.agent_id or client.agent_id or worker_mod.DEFAULT_AGENT_ID,
        adapter=args.adapter,
        confirm_run=bool(args.confirm_run),
        use_session=bool(args.use_session),
        session_ttl_sec=args.session_ttl_sec,
        session_refresh_margin_sec=args.session_refresh_margin_sec,
        poll_interval=args.poll_interval,
        label=args.label or "",
        working_directory=args.working_directory or str(worker_mod.DEFAULT_WORKER_CWD),
        runtime_dir=args.runtime_dir or "",
        log_path=args.log_path or "",
        api_key_placeholder=args.api_key_placeholder,
        credential_source=args.credential_source,
        config_path=args.config_path,
        worker_command=args.worker_command or "",
        hermes_gateway_url=args.hermes_gateway_url or "",
        codex_bin=args.codex_bin or "",
        service_path=args.service_path or "",
        confirm_install=bool(args.confirm_install),
        overwrite=bool(args.overwrite),
        timeout=args.timeout,
    )
    payload = worker_mod.install_service_file(install_args)
    payload["command"] = "agentops worker service-install"
    return payload


def cmd_worker_service_control(args, client: AgentOpsClient) -> dict:
    from . import worker as worker_mod

    control_args = argparse.Namespace(
        manager=args.manager,
        action=args.service_action,
        base_url=client.base_url,
        workspace_id=client.workspace_id,
        agent_id=args.agent_id or client.agent_id or worker_mod.DEFAULT_AGENT_ID,
        adapter=args.adapter,
        confirm_run=bool(args.confirm_run),
        use_session=bool(args.use_session),
        session_ttl_sec=args.session_ttl_sec,
        session_refresh_margin_sec=args.session_refresh_margin_sec,
        poll_interval=args.poll_interval,
        label=args.label or "",
        working_directory=args.working_directory or str(worker_mod.DEFAULT_WORKER_CWD),
        runtime_dir=args.runtime_dir or "",
        worker_command=args.worker_command or "",
        hermes_gateway_url=args.hermes_gateway_url or "",
        codex_bin=args.codex_bin or "",
        service_path=args.service_path or "",
        api_key_placeholder=args.api_key_placeholder,
        credential_source=args.credential_source,
        config_path=args.config_path,
        timeout=args.timeout,
        confirm_control=bool(args.confirm_control),
    )
    payload = worker_mod.control_service(control_args)
    payload["command"] = "agentops worker service-control"
    return payload


def cmd_worker_start(args, client: AgentOpsClient) -> dict:
    payload = {
        "adapter": args.adapter,
        "agent_id": args.agent_id,
        "poll_interval": args.poll_interval,
        "max_tasks": args.max_tasks,
        "max_errors": args.max_errors,
        "status": args.status or ["planned"],
        "confirm_run": bool(args.confirm_run),
    }
    if args.openclaw_timeout is not None:
        payload["openclaw_timeout"] = args.openclaw_timeout
    return client.post("/api/workers/local/start", payload)


def cmd_worker_stop(args, client: AgentOpsClient) -> dict:
    return client.post("/api/workers/local/stop", {"adapter": args.adapter})


def cmd_worker_restart(args, client: AgentOpsClient) -> dict:
    payload = {
        "adapter": args.adapter,
        "agent_id": args.agent_id,
        "poll_interval": args.poll_interval,
        "max_tasks": args.max_tasks,
        "max_errors": args.max_errors,
        "status": args.status or None,
        "confirm_run": bool(args.confirm_run),
    }
    if args.openclaw_timeout is not None:
        payload["openclaw_timeout"] = args.openclaw_timeout
    return client.post("/api/workers/local/restart", payload)


def cmd_worker_release(args, client: AgentOpsClient) -> dict:
    return client.post("/api/workers/tasks/release", {
        "task_id": args.task_id,
        "reason": args.reason,
        "force": args.force,
    })


def cmd_worker_hygiene(args, client: AgentOpsClient) -> dict:
    payload = {
        "threshold_sec": args.threshold_sec,
        "enrollment_age_sec": args.enrollment_age_sec,
        "limit": args.limit,
    }
    if args.apply:
        payload["apply"] = True
        payload["confirm_cleanup"] = bool(args.confirm_cleanup)
        payload["release_reason"] = args.reason
        return client.post("/api/workers/fleet/hygiene", payload)
    return client.get("/api/workers/fleet/hygiene", query=payload)


def cmd_enrollment_create(args, client: AgentOpsClient) -> dict:
    payload = {
        "workspace_id": client.workspace_id,
        "agent_id": args.agent_id,
        "name": args.name,
        "role": args.role,
        "runtime_type": args.runtime,
        "scopes": split_csv(args.scopes),
        "ttl_days": args.ttl_days,
        "heartbeat_timeout_sec": args.heartbeat_timeout_sec,
        "label": args.label,
    }
    result = client.post("/api/agent-gateway/enrollment/create", payload)
    if args.save_token and result.get("token"):
        config = load_config()
        config.update({
            "base_url": client.base_url,
            "workspace_id": result.get("workspace_id") or client.workspace_id,
            "agent_id": result.get("agent_id") or args.agent_id,
            "api_key": result["token"],
        })
        save_config(config)
        result["saved_to"] = str(CONFIG_PATH)
    return result


def cmd_enrollment_policy_preview(args, client: AgentOpsClient) -> dict:
    return client.post("/api/agent-gateway/enrollment/policy-preview", {
        "workspace_id": args.workspace_id or client.workspace_id,
        "runtime_type": args.runtime,
        "scopes": split_csv(args.scopes),
    })


def cmd_enrollment_request(args, client: AgentOpsClient) -> dict:
    payload = {
        "workspace_id": client.workspace_id,
        "agent_id": args.agent_id,
        "name": args.name,
        "role": args.role,
        "runtime_type": args.runtime,
        "scopes": split_csv(args.scopes),
        "reason": args.reason,
    }
    return client.post("/api/agent-gateway/enrollment/request", payload)


def cmd_enrollment_issue_approved(args, client: AgentOpsClient) -> dict:
    payload = {
        "request_id": args.request_id,
        "approval_id": args.approval_id,
        "ttl_days": args.ttl_days,
        "heartbeat_timeout_sec": args.heartbeat_timeout_sec,
        "label": args.label,
    }
    result = client.post("/api/agent-gateway/enrollment/issue-approved", payload)
    if args.save_token and result.get("token"):
        config = load_config()
        config.update({
            "base_url": client.base_url,
            "workspace_id": result.get("workspace_id") or client.workspace_id,
            "agent_id": result.get("agent_id") or client.agent_id,
            "api_key": result["token"],
        })
        save_config(config)
        result["saved_to"] = str(CONFIG_PATH)
    return result


def cmd_enrollment_list(args, client: AgentOpsClient) -> dict:
    return client.get("/api/agent-gateway/enrollments")


def cmd_enrollment_revoke(args, client: AgentOpsClient) -> dict:
    payload = {
        "token_id": args.token_id,
        "agent_id": args.agent_id,
    }
    return client.post("/api/agent-gateway/enrollment/revoke", payload)


def cmd_enrollment_rotate(args, client: AgentOpsClient) -> dict:
    payload = {
        "token_id": args.token_id,
        "agent_id": args.agent_id,
        "scopes": split_csv(args.scopes) if args.scopes else None,
        "ttl_days": args.ttl_days,
        "heartbeat_timeout_sec": args.heartbeat_timeout_sec,
        "label": args.label,
    }
    result = client.post("/api/agent-gateway/enrollment/rotate", payload)
    if args.save_token and result.get("token"):
        config = load_config()
        config.update({
            "base_url": client.base_url,
            "workspace_id": result.get("workspace_id") or client.workspace_id,
            "agent_id": result.get("agent_id") or args.agent_id,
            "api_key": result["token"],
        })
        save_config(config)
        result["saved_to"] = str(CONFIG_PATH)
    return result


def cmd_session_create(args, client: AgentOpsClient) -> dict:
    payload = {
        "workspace_id": client.workspace_id,
        "agent_id": client.agent_id,
        "ttl_sec": args.ttl_sec,
        "scopes": split_csv(args.scopes) if args.scopes else None,
    }
    result = client.post("/api/agent-gateway/session/create", payload)
    if args.save_session and result.get("session_token"):
        config = load_config()
        config.update({
            "base_url": client.base_url,
            "workspace_id": result.get("workspace_id") or client.workspace_id,
            "agent_id": result.get("agent_id") or client.agent_id,
            "api_key": result["session_token"],
        })
        save_config(config)
        result["saved_to"] = str(CONFIG_PATH)
    return result


def cmd_session_list(args, client: AgentOpsClient) -> dict:
    return client.get("/api/agent-gateway/sessions")


def cmd_session_revoke(args, client: AgentOpsClient) -> dict:
    payload = {
        "session_id": args.session_id,
        "agent_id": args.agent_id,
    }
    return client.post("/api/agent-gateway/session/revoke", payload)


def add_global_args(parser, suppress_defaults: bool = False):
    default = argparse.SUPPRESS if suppress_defaults else None
    parser.add_argument("--base-url", default=default, help="AgentOps MIS base URL. Defaults to env/config/http://127.0.0.1:8787.")
    parser.add_argument("--api-key", default=default, help="Local API key. Prefer AGENTOPS_API_KEY for real use.")
    parser.add_argument("--workspace-id", default=default, help="Workspace id. Defaults to env/config/local-demo.")
    parser.add_argument("--agent-id", default=default, help="Default agent id for this command.")
    parser.add_argument("--request-timeout", type=int, default=default, help="HTTP request timeout in seconds. Defaults to env/config/30.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agentops", description="AgentOps MIS local Agent Gateway CLI.")
    add_global_args(parser)
    sub = parser.add_subparsers(dest="resource", required=True)

    login = sub.add_parser("login", help="Store local AgentOps MIS CLI config.")
    add_global_args(login, suppress_defaults=True)
    login.add_argument("--prompt-api-key", action="store_true", help="Read the enrollment token without echoing it or placing it in shell history.")
    login.set_defaults(handler="login")

    status = sub.add_parser("status", help="Check Agent Gateway connectivity and safe auth metadata.")
    add_global_args(status, suppress_defaults=True)
    status.add_argument("--require-current-code", action="store_true", help="Fail if the connected MIS process is older than backend source files.")
    status.add_argument("--expect-head-sha", default="", help="Fail if the connected MIS process reports a different git HEAD.")
    status.set_defaults(handler="status")

    doctor = sub.add_parser("doctor", help="Diagnose local/remote agent CLI setup without printing secrets.")
    add_global_args(doctor, suppress_defaults=True)
    doctor.set_defaults(handler="doctor")

    local = sub.add_parser("local", help="Single-workspace local readiness commands.")
    local_sub = local.add_subparsers(dest="action", required=True)
    local_readiness = local_sub.add_parser("readiness", help="Show end-to-end local MIS readiness and evidence closure.")
    local_readiness.add_argument("--require-current-code", action="store_true", help="Fail if the connected MIS process is older than backend source files.")
    local_readiness.add_argument("--expect-head-sha", default="", help="Fail if the connected MIS process reports a different git HEAD.")
    local_readiness.set_defaults(handler="local_readiness")

    demo = sub.add_parser("demo", help="Read-only demo and recording readiness commands.")
    demo_sub = demo.add_subparsers(dest="action", required=True)
    demo_readiness = demo_sub.add_parser("readiness", help="Show the canonical v1.5 classroom recording path readiness.")
    demo_readiness.set_defaults(handler="demo_readiness")

    command_center = sub.add_parser("command-center", help="Stable read-only Command Center BFF for human UI and agent debugging.")
    command_center_sub = command_center.add_subparsers(dest="action", required=True)
    command_center_overview = command_center_sub.add_parser("overview", help="Aggregate projects, blocked runs, approvals, deliveries, stale workers, and next actions.")
    command_center_overview.add_argument("--project-id", default=None)
    command_center_overview.add_argument("--limit", type=int, default=8)
    command_center_overview.add_argument("--threshold-sec", type=int, default=900)
    command_center_overview.add_argument("--refresh-cache", action="store_true")
    command_center_overview.set_defaults(handler="command_center_overview")

    commercial = sub.add_parser("commercial", help="Commercial-readiness config previews.")
    commercial_sub = commercial.add_subparsers(dest="action", required=True)
    commercial_config_status = commercial_sub.add_parser("config-status", help="Read safe-by-default commercial config status without billing or cleanup execution.")
    commercial_config_status.set_defaults(handler="commercial_config_status")

    operator = sub.add_parser("operator", help="Read-only operator command-center plans.")
    operator_sub = operator.add_subparsers(dest="action", required=True)
    operator_plan = operator_sub.add_parser("action-plan", help="Show the prioritized next safe CLI/UI actions.")
    operator_plan.add_argument("--limit", type=int, default=12)
    operator_plan.set_defaults(handler="operator_action_plan")
    operator_receipts = operator_sub.add_parser("action-receipts", help="Read Action Queue receipt ledger rows plus action-plan coverage.")
    operator_receipts.add_argument("--limit", type=int, default=12)
    operator_receipts.add_argument("--plan-limit", type=int, default=12)
    operator_receipts.add_argument("--source", default="", help="Optional exact receipt source filter.")
    operator_receipts.add_argument("--action-id", default="", help="Optional exact receipt action id filter.")
    operator_receipts.add_argument("--action-signature", default="", help="Optional exact receipt action signature filter.")
    operator_receipts.set_defaults(handler="operator_action_receipts")
    operator_record_receipt = operator_sub.add_parser("record-action-receipt", help="Preview or append an audited Action Queue receipt without executing commands.")
    operator_record_receipt.add_argument("--action-command", required=True, help="The exact recovery/action command that was run or inspected.")
    operator_record_receipt.add_argument("--verify-command", default="", help="The acceptance-check command paired with the action.")
    operator_record_receipt.add_argument("--status", default="recorded", choices=["recorded", "verified", "failed", "skipped"])
    operator_record_receipt.add_argument("--result-summary", default="")
    operator_record_receipt.add_argument("--action-id", default="")
    operator_record_receipt.add_argument("--action-signature", default="")
    operator_record_receipt.add_argument("--prepared-action-id", default="", help="Optional prepared_actions.action_id that this receipt must bind to.")
    operator_record_receipt.add_argument("--prepared-action-hash", default="", help="Optional expected prepared action hash; server rejects mismatches.")
    operator_record_receipt.add_argument("--required-prepared-action-status", default="", help="Optional prepared action status required before recording, for example consumed.")
    operator_record_receipt.add_argument("--source", default="agentops_cli.operator_record_action_receipt")
    operator_record_receipt.add_argument("--actor-id", default="usr_founder")
    operator_record_receipt.add_argument("--confirm-record", action="store_true", help="Actually append runtime/audit receipt evidence. Default is preview only.")
    operator_record_receipt.set_defaults(handler="operator_record_action_receipt")
    operator_record_readback = operator_sub.add_parser("record-control-readback", help="Preview or append an audited control readback for an Action Queue receipt.")
    operator_record_readback.add_argument("--receipt-id", required=True, help="Receipt id returned by record-action-receipt.")
    operator_record_readback.add_argument("--source", default="agentops_cli.operator_record_control_readback")
    operator_record_readback.add_argument("--control-readback-json", required=True, help="JSON object with before/after/self_check readback evidence.")
    operator_record_readback.add_argument("--actor-id", default="usr_founder")
    operator_record_readback.add_argument("--confirm-record", action="store_true", help="Actually append control-readback evidence. Default is preview only.")
    operator_record_readback.set_defaults(handler="operator_record_control_readback")
    operator_service_closure = operator_sub.add_parser("service-closure", help="Preview or record service-managed loop receipt/readback closure for Hermes/OpenClaw.")
    operator_service_closure.add_argument("--adapter", choices=["hermes", "openclaw"], required=True)
    operator_service_closure.add_argument("--fast", action="store_true", help="Skip heavy loop-supervision reads and build the service closure receipt from local service-check commands.")
    operator_service_closure.add_argument("--service-check-json", default="", help="Path to JSON from agentops worker service-check, or '-' for stdin.")
    operator_service_closure.add_argument("--run-service-check", action="store_true", help="Run the local read-only worker service-check in this CLI process when no JSON file is supplied.")
    operator_service_closure.add_argument("--service-check-command", default="", help="Optional exact worker service-check command to parse for --run-service-check.")
    operator_service_closure.add_argument("--service-check-manager", choices=["launchd", "systemd"], default=None)
    operator_service_closure.add_argument("--service-check-agent-id", default="")
    operator_service_closure.add_argument("--service-path", default="", help="Optional service file path override for --run-service-check.")
    operator_service_closure.add_argument("--service-label", default="", help="Optional service label override for --run-service-check.")
    operator_service_closure.add_argument("--api-key-placeholder", default="<paste one-time token here>")
    operator_service_closure.add_argument("--service-check-timeout", type=int, default=5)
    operator_service_closure.add_argument("--receipt-command", default="", help="Optional exact record-action-receipt command to parse; defaults to loop-supervision command.")
    operator_service_closure.add_argument("--receipt-status", default="verified", choices=["verified", "failed", "recorded", "skipped"])
    operator_service_closure.add_argument("--result-summary", default="")
    operator_service_closure.add_argument("--actor-id", default="usr_founder")
    operator_service_closure.add_argument("--limit", type=int, default=8)
    operator_service_closure.add_argument("--task-id", default=None)
    operator_service_closure.add_argument("--agent-id", default=None)
    operator_service_closure.add_argument("--query", default="READ PLAN RETRIEVE COMPARE VERIFY RECORD")
    operator_service_closure.add_argument("--handoff-mode", choices=["lightweight", "full"], default="lightweight")
    operator_service_closure.add_argument("--full-handoff", action="store_true")
    operator_service_closure.add_argument("--confirm-record", action="store_true", help="Append receipt/readback evidence. Default is preview only.")
    operator_service_closure.set_defaults(handler="operator_service_closure")
    operator_receipt_memory_lane = operator_sub.add_parser("receipt-failure-memories", help="Read repeated failed receipt evaluations that should become memory candidates.")
    operator_receipt_memory_lane.add_argument("--min-failures", type=int, default=2)
    operator_receipt_memory_lane.add_argument("--limit", type=int, default=8)
    operator_receipt_memory_lane.set_defaults(handler="operator_receipt_failure_memories")
    operator_receipt_memory = operator_sub.add_parser("propose-receipt-failure-memory", help="Preview or create a memory candidate from repeated failed Action Queue receipt evaluations.")
    operator_receipt_memory.add_argument("--action-hash", default="")
    operator_receipt_memory.add_argument("--min-failures", type=int, default=2)
    operator_receipt_memory.add_argument("--memory-id", default=None)
    operator_receipt_memory.add_argument("--canonical-text", default=None)
    operator_receipt_memory.add_argument("--actor-id", default="usr_founder")
    operator_receipt_memory.add_argument("--confirm-create", action="store_true")
    operator_receipt_memory.set_defaults(handler="operator_propose_receipt_failure_memory")
    operator_loop = operator_sub.add_parser("loop-audit", help="Audit the READ/PLAN/RETRIEVE/COMPARE/EXECUTE/VERIFY/RECORD loop contract.")
    operator_loop.add_argument("--loop-id", default=None)
    operator_loop.add_argument("--limit", type=int, default=12)
    operator_loop.set_defaults(handler="operator_loop_audit")
    operator_loop_control_parser = operator_sub.add_parser("loop-control", help="Read the lightweight loop-control next step for real local ledgers.")
    operator_loop_control_parser.add_argument("--loop-id", default=None)
    operator_loop_control_parser.add_argument("--limit", type=int, default=8)
    operator_loop_control_parser.set_defaults(handler="operator_loop_control")
    operator_evidence = operator_sub.add_parser("evidence-report", help="Read a run-by-run evidence report across Agent Plans, approvals, manifests, and ledger rows.")
    operator_evidence.add_argument("--run-id", default=None)
    operator_evidence.add_argument("--task-id", default=None)
    operator_evidence.add_argument("--limit", type=int, default=12)
    operator_evidence.set_defaults(handler="operator_evidence_report")
    operator_handoff = operator_sub.add_parser("handoff", help="Read a loop/operator handoff package with work-order, receipt, review, and safety state.")
    operator_handoff.add_argument("--loop-id", default=None)
    operator_handoff.add_argument("--limit", type=int, default=12)
    operator_handoff.set_defaults(handler="operator_handoff")
    operator_loop_self_check = operator_sub.add_parser("loop-self-check", help="Read a loop self-check across policy, receipts, evaluations, audit, and handoff health.")
    operator_loop_self_check.add_argument("--loop-id", default=None)
    operator_loop_self_check.add_argument("--limit", type=int, default=12)
    operator_loop_self_check.set_defaults(handler="operator_loop_self_check")
    operator_advance = operator_sub.add_parser("advance-loop", help="Preview or execute one bounded allowlisted loop action, verify it, and record a receipt.")
    operator_advance.add_argument("--loop-id", default=None)
    operator_advance.add_argument("--limit", type=int, default=12)
    operator_advance.add_argument("--timeout", type=int, default=90)
    operator_advance.add_argument("--actor-id", default="usr_founder")
    operator_advance.add_argument("--fast-control", action="store_true", help="Use lightweight loop-control readback instead of full handoff.")
    operator_advance.add_argument("--confirm-advance", action="store_true", help="Execute exactly one allowlisted local agentops action and record its receipt.")
    operator_advance.set_defaults(handler="operator_advance_loop")
    operator_advance_policy = operator_sub.add_parser("advance-loop-policy", help="Read the bounded advance-loop policy.")
    operator_advance_policy.set_defaults(handler="operator_advance_loop_policy")
    operator_health = operator_sub.add_parser("health", help="Read the aggregate operator health snapshot across loop, readiness, security, worker, review, and action queues.")
    operator_health.add_argument("--loop-id", default=None)
    operator_health.add_argument("--limit", type=int, default=12)
    operator_health.set_defaults(handler="operator_health")
    operator_runtime_doctor = operator_sub.add_parser("runtime-doctor", help="Read the local MIS/Hermes/OpenClaw/Codex runtime doctor with launch, safety, and evidence commands.")
    operator_runtime_doctor.add_argument("--loop-id", default=None)
    operator_runtime_doctor.add_argument("--limit", type=int, default=8)
    operator_runtime_doctor.add_argument("--runtime-base-url", default=None, help="Base URL to embed in suggested runtime commands; defaults to the server host.")
    operator_runtime_doctor.set_defaults(handler="operator_runtime_doctor")
    operator_live_acceptance = operator_sub.add_parser("live-acceptance", help="Read Hermes/OpenClaw live customer-worker acceptance freshness without running adapters.")
    operator_live_acceptance.add_argument("--freshness-hours", type=int, default=72)
    operator_live_acceptance.add_argument("--limit", type=int, default=8)
    operator_live_acceptance.set_defaults(handler="operator_live_acceptance")
    operator_local_harness_proof = operator_sub.add_parser("local-harness-proof", help="Read local task harness proof for mock/Hermes/OpenClaw without running adapters.")
    operator_local_harness_proof.add_argument("--freshness-hours", type=int, default=72)
    operator_local_harness_proof.add_argument("--limit", type=int, default=8)
    operator_local_harness_proof.set_defaults(handler="operator_local_harness_proof")
    operator_live_product = operator_sub.add_parser("live-product-readiness", help="Return product-readiness proof from fresh Hermes/OpenClaw live ledger evidence without running adapters.")
    operator_live_product.add_argument("--freshness-hours", type=int, default=72)
    operator_live_product.add_argument("--limit", type=int, default=8)
    operator_live_product.add_argument("--require-adapter", action="append", choices=["hermes", "openclaw"], default=None)
    operator_live_product.set_defaults(handler="operator_live_product_readiness")
    operator_execution_mode = operator_sub.add_parser("execution-mode", help="Read the current dispatch execution mode for mock/Hermes/OpenClaw without running adapters.")
    operator_execution_mode.add_argument("--adapter", choices=["mock", "hermes", "openclaw"], default="mock")
    operator_execution_mode.add_argument("--confirm-run", action="store_true", help="Preview the mode after explicit live confirmation; does not execute the adapter.")
    operator_execution_mode.add_argument("--limit", type=int, default=8)
    operator_execution_mode.set_defaults(handler="operator_execution_mode")
    operator_command_center = operator_sub.add_parser("command-center", help="Read the unified operator command-center BFF for projects, blockers, approvals, deliveries, stale workers, and next actions.")
    operator_command_center.add_argument("--project-id", default=None)
    operator_command_center.add_argument("--limit", type=int, default=12)
    operator_command_center.set_defaults(handler="operator_command_center")
    operator_intake = operator_sub.add_parser("intake-checklist", help="Show read-only pre-intake gates for planned/backlog tasks.")
    operator_intake.add_argument("--limit", type=int, default=12)
    operator_intake.set_defaults(handler="operator_intake_checklist")
    operator_launch = operator_sub.add_parser("loop-launch-packet", help="Build a read-only Agent Work Method launch packet for the next agent loop.")
    operator_launch.add_argument("--limit", type=int, default=8)
    operator_launch.add_argument("--task-id", default=None)
    operator_launch.add_argument("--agent-id", default=None)
    operator_launch.add_argument("--query", default="READ PLAN RETRIEVE COMPARE VERIFY RECORD")
    operator_launch.add_argument("--handoff-mode", choices=["lightweight", "full"], default="lightweight", help="Use lightweight loop-control by default; choose full for deeper operator handoff diagnostics.")
    operator_launch.add_argument("--full-handoff", action="store_true", help="Shortcut for --handoff-mode full.")
    operator_launch.add_argument("--brief", action="store_true", help="Return a compact copy-only launch brief for Hermes/OpenClaw/Codex instead of the full packet.")
    operator_launch.add_argument("--adapter", choices=["mock", "codex", "hermes", "openclaw"], default="mock", help="Adapter context to include in --brief preflight and live-confirmation guidance.")
    operator_launch.set_defaults(handler="operator_loop_launch_packet")
    operator_start_check = operator_sub.add_parser("start-check", help="Read the pre-task local loop start check for Hermes/OpenClaw/Codex.")
    operator_start_check.add_argument("--adapter", choices=["mock", "hermes", "openclaw"], default="mock")
    operator_start_check.add_argument("--limit", type=int, default=8)
    operator_start_check.add_argument("--loop-id", default=None)
    operator_start_check.add_argument("--task-id", default=None)
    operator_start_check.add_argument("--agent-id", default=None)
    operator_start_check.add_argument("--query", default="READ PLAN RETRIEVE COMPARE VERIFY RECORD")
    operator_start_check.add_argument("--handoff-mode", choices=["lightweight", "full"], default="lightweight")
    operator_start_check.add_argument("--full-handoff", action="store_true", help="Shortcut for --handoff-mode full when reading the launch brief.")
    operator_start_check.add_argument("--runtime-base-url", default=None, help="Base URL to embed in suggested runtime-doctor commands.")
    operator_start_check.add_argument("--freshness-hours", type=int, default=72)
    operator_start_check.set_defaults(handler="operator_start_check")
    operator_agent_loop_handoff = operator_sub.add_parser("agent-loop-handoff", help="Read a compact Hermes/OpenClaw/Codex loop handoff matrix from current-code, live-readiness, start-check, and launch-brief gates.")
    operator_agent_loop_handoff.add_argument("--adapter", action="append", choices=["mock", "hermes", "openclaw"], default=None, help="Adapter consumer to include. Defaults to Hermes and OpenClaw; repeat for multiple adapters.")
    operator_agent_loop_handoff.add_argument("--limit", type=int, default=8)
    operator_agent_loop_handoff.add_argument("--loop-id", default=None)
    operator_agent_loop_handoff.add_argument("--task-id", default=None)
    operator_agent_loop_handoff.add_argument("--agent-id", default=None)
    operator_agent_loop_handoff.add_argument("--query", default="READ PLAN RETRIEVE COMPARE VERIFY RECORD")
    operator_agent_loop_handoff.add_argument("--handoff-mode", choices=["lightweight", "full"], default="lightweight")
    operator_agent_loop_handoff.add_argument("--full-handoff", action="store_true", help="Shortcut for --handoff-mode full when reading launch briefs.")
    operator_agent_loop_handoff.add_argument("--freshness-hours", type=int, default=72)
    operator_agent_loop_handoff.add_argument("--include-codex", dest="include_codex", action="store_true", default=True, help="Include the Codex supervisor consumer block. Enabled by default.")
    operator_agent_loop_handoff.add_argument("--no-codex", dest="include_codex", action="store_false", help="Omit the Codex supervisor consumer block.")
    operator_agent_loop_handoff.set_defaults(handler="operator_agent_loop_handoff")
    operator_loop_supervision = operator_sub.add_parser("loop-supervision", help="Read the pre-confirm Hermes/OpenClaw/Codex loop supervision gate without running loop-driver.")
    operator_loop_supervision.add_argument("--adapter", action="append", choices=["mock", "hermes", "openclaw"], default=None, help="Adapter to supervise. Defaults to Hermes and OpenClaw; repeat for multiple adapters.")
    operator_loop_supervision.add_argument("--limit", type=int, default=8)
    operator_loop_supervision.add_argument("--loop-id", default=None)
    operator_loop_supervision.add_argument("--task-id", default=None)
    operator_loop_supervision.add_argument("--agent-id", default=None)
    operator_loop_supervision.add_argument("--query", default="READ PLAN RETRIEVE COMPARE VERIFY RECORD")
    operator_loop_supervision.add_argument("--handoff-mode", choices=["lightweight", "full"], default="lightweight")
    operator_loop_supervision.add_argument("--full-handoff", action="store_true", help="Shortcut for --handoff-mode full when reading supervision sources.")
    operator_loop_supervision.add_argument("--freshness-hours", type=int, default=72)
    operator_loop_supervision.add_argument("--include-codex", dest="include_codex", action="store_true", default=True, help="Include Codex handoff context in the source handoff. Enabled by default.")
    operator_loop_supervision.add_argument("--no-codex", dest="include_codex", action="store_false", help="Omit Codex handoff context from the source handoff.")
    operator_loop_supervision.add_argument("--work-packet", action="store_true", help="Return only the compact machine-consumable loop work-packet bundle.")
    operator_loop_supervision.add_argument("--decision", action="store_true", help="Return a compact read-only work-packet consumption decision for local loop callers.")
    operator_loop_supervision.set_defaults(handler="operator_loop_supervision")
    operator_loop_bootstrap = operator_sub.add_parser("loop-bootstrap", help="Build a read-only local loop bootstrap packet for Hermes/OpenClaw service deployment and bounded loop start.")
    operator_loop_bootstrap.add_argument("--adapter", choices=["hermes", "openclaw"], required=True)
    operator_loop_bootstrap.add_argument("--manager", choices=["launchd", "systemd"], default="launchd")
    operator_loop_bootstrap.add_argument("--max-steps", type=int, default=3)
    operator_loop_bootstrap.add_argument("--limit", type=int, default=8)
    operator_loop_bootstrap.add_argument("--loop-id", default=None)
    operator_loop_bootstrap.add_argument("--task-id", default=None)
    operator_loop_bootstrap.add_argument("--agent-id", default=None)
    operator_loop_bootstrap.add_argument("--query", default="READ PLAN RETRIEVE COMPARE VERIFY RECORD")
    operator_loop_bootstrap.add_argument("--handoff-mode", choices=["lightweight", "full"], default="lightweight")
    operator_loop_bootstrap.add_argument("--full-handoff", action="store_true")
    operator_loop_bootstrap.add_argument("--fast", action="store_true", help="Return a lightweight copy-only bootstrap packet without reading heavy start-check or loop-supervision endpoints.")
    operator_loop_bootstrap.add_argument("--run-service-check", action="store_true", help="Perform only local read-only worker service-check while building the packet.")
    operator_loop_bootstrap.add_argument("--service-check-agent-id", default="")
    operator_loop_bootstrap.add_argument("--service-path", default="")
    operator_loop_bootstrap.add_argument("--service-label", default="")
    operator_loop_bootstrap.add_argument("--api-key-placeholder", default="<paste one-time token here>")
    operator_loop_bootstrap.add_argument("--service-check-timeout", type=int, default=5)
    operator_loop_bootstrap.set_defaults(handler="operator_loop_bootstrap")
    operator_loop_driver = operator_sub.add_parser("loop-driver", help="Preview or run a bounded multi-step local loop for Hermes/OpenClaw/Codex.")
    operator_loop_driver.add_argument("--adapter", choices=["mock", "hermes", "openclaw"], default="mock")
    operator_loop_driver.add_argument("--max-steps", type=int, default=3, help="Maximum bounded advance-loop steps to run; capped at 5.")
    operator_loop_driver.add_argument("--limit", type=int, default=8)
    operator_loop_driver.add_argument("--loop-id", default=None)
    operator_loop_driver.add_argument("--task-id", default=None)
    operator_loop_driver.add_argument("--agent-id", default=None)
    operator_loop_driver.add_argument("--query", default="READ PLAN RETRIEVE COMPARE VERIFY RECORD")
    operator_loop_driver.add_argument("--handoff-mode", choices=["lightweight", "full"], default="lightweight")
    operator_loop_driver.add_argument("--full-handoff", action="store_true", help="Shortcut for --handoff-mode full when reading the launch brief.")
    operator_loop_driver.add_argument("--timeout", type=int, default=90)
    operator_loop_driver.add_argument("--actor-id", default="usr_founder")
    operator_loop_driver.add_argument("--confirm-loop", action="store_true", help="Run bounded advance-loop steps and record receipts/control-readback.")
    operator_loop_driver.add_argument("--auto-service-closure", action="store_true", help="Before each confirmed Hermes/OpenClaw bounded step, run local read-only service-check and record service closure evidence.")
    operator_loop_driver.add_argument("--service-path", default="", help="Optional service file path override for --auto-service-closure.")
    operator_loop_driver.add_argument("--service-label", default="", help="Optional service label override for --auto-service-closure.")
    operator_loop_driver.add_argument("--service-check-timeout", type=int, default=5)
    operator_loop_driver.set_defaults(handler="operator_loop_driver")
    evidence_gap = operator_sub.add_parser("remediate-evidence-gap", help="Preview or create a Commander package for a run execution-evidence gap.")
    evidence_gap.add_argument("--run-id", required=True)
    evidence_gap.add_argument("--task-id", default=None)
    evidence_gap.add_argument("--title", default=None)
    evidence_gap.add_argument("--project-id", default=None)
    evidence_gap.add_argument("--plan-id", default=None)
    evidence_gap.add_argument("--lane-id", default=None)
    evidence_gap.add_argument("--owner-agent-id", default=None)
    evidence_gap.add_argument("--priority", default=None, choices=["low", "medium", "high", "critical"])
    evidence_gap.add_argument("--risk-level", default=None, choices=["low", "medium", "high", "critical"])
    evidence_gap.add_argument("--budget-limit-usd", type=float, default=None)
    evidence_gap.add_argument("--actor-id", default="usr_founder")
    evidence_gap.add_argument("--confirm-create", action="store_true")
    evidence_gap.set_defaults(handler="operator_remediate_evidence_gap")
    close_evidence_gap = operator_sub.add_parser("close-evidence-gap", help="Preview or record an operator decision closing/reopening a run execution-evidence gap.")
    close_evidence_gap.add_argument("--run-id", required=True)
    close_evidence_gap.add_argument("--decision", default="accepted_remediation", choices=["accepted_remediation", "waived", "reopen"])
    close_evidence_gap.add_argument("--reason", default=None)
    close_evidence_gap.add_argument("--note", default=None)
    close_evidence_gap.add_argument("--synthesis-artifact-id", default=None)
    close_evidence_gap.add_argument("--remediation-task-id", default=None)
    close_evidence_gap.add_argument("--actor-id", default="usr_founder")
    close_evidence_gap.add_argument("--confirm-close", action="store_true")
    close_evidence_gap.set_defaults(handler="operator_close_evidence_gap")

    commander = sub.add_parser("commander", help="Commander planning, dispatch and readback commands.")
    commander_sub = commander.add_subparsers(dest="action", required=True)
    commander_board = commander_sub.add_parser("board", help="Read the Commander project board.")
    commander_board.add_argument("--project-id", default=None, help="Optional Commander project id for a scoped team board.")
    commander_board.add_argument("--plan-id", default=None, help="Optional Commander plan id for a scoped team board.")
    commander_board.add_argument("--limit", type=int, default=25, help="Maximum scoped work packages to include in team_board.")
    commander_board.set_defaults(handler="commander_board")
    commander_repo_map = commander_sub.add_parser("repo-map", help="Localize relevant repo files for a coding work package.")
    commander_repo_map.add_argument("query", nargs="?", default="", help="Task or feature query to localize.")
    commander_repo_map.add_argument("--query", "-q", dest="query_flag", default=None, help="Task or feature query to localize.")
    commander_repo_map.add_argument("--limit", type=int, default=12)
    commander_repo_map.add_argument("--char-budget", type=int, default=8000)
    commander_repo_map.set_defaults(handler="commander_repo_map")
    commander_coding_template = commander_sub.add_parser("coding-template", help="Read the local coding project template with worktree, patch, verifier and merge-gate evidence.")
    commander_coding_template.add_argument("query", nargs="?", default="", help="Coding goal or work-package query to localize.")
    commander_coding_template.add_argument("--query", "-q", dest="query_flag", default=None, help="Coding goal or work-package query to localize.")
    commander_coding_template.add_argument("--project-id", default="proj_local_coding")
    commander_coding_template.add_argument("--task-id", default="<task_id>")
    commander_coding_template.add_argument("--limit", type=int, default=8)
    commander_coding_template.add_argument("--char-budget", type=int, default=4800)
    commander_coding_template.set_defaults(handler="commander_coding_template")
    commander_inbox = commander_sub.add_parser("inbox", help="Read the Commander integration inbox.")
    commander_inbox.add_argument("--bucket", choices=["all", "ready_for_review", "still_running", "blocked", "late_or_stale", "needs_memory_review"], default="all")
    commander_inbox.add_argument("--limit", type=int, default=20)
    commander_inbox.add_argument("--threshold-sec", type=int, default=900)
    commander_inbox.set_defaults(handler="commander_inbox")
    commander_plan = commander_sub.add_parser("plan", help="Preview or create commander work-package tasks from a project goal.")
    commander_plan.add_argument("--goal", required=True, help="Customer/project goal to decompose into work packages.")
    commander_plan.add_argument("--project-id", default=None)
    commander_plan.add_argument("--plan-id", default=None)
    commander_plan.add_argument("--max-packages", type=int, default=5)
    commander_plan.add_argument("--task-id-prefix", default=None)
    commander_plan.add_argument("--lanes-json", default=None, help="Optional JSON array overriding the default commander lanes.")
    commander_plan.add_argument("--confirm-create", action="store_true", help="Actually create MIS tasks; omitted means preview only.")
    commander_plan.set_defaults(handler="commander_plan")
    commander_packages = commander_sub.add_parser("packages", help="Read persisted commander work-package task status and evidence.")
    commander_packages.add_argument("--project-id", default=None)
    commander_packages.add_argument("--plan-id", default=None)
    commander_packages.add_argument("--status", default="all")
    commander_packages.add_argument("--limit", type=int, default=25)
    commander_packages.set_defaults(handler="commander_packages")
    commander_lane_packets = commander_sub.add_parser("lane-packets", help="Read machine-facing Commander lane packets for agents/workers.")
    commander_lane_packets.add_argument("--project-id", default=None)
    commander_lane_packets.add_argument("--plan-id", default=None)
    commander_lane_packets.add_argument("--status", default="all")
    commander_lane_packets.add_argument("--limit", type=int, default=25)
    commander_lane_packets.set_defaults(handler="commander_lane_packets")
    commander_dispatch = commander_sub.add_parser("dispatch-package", help="Dispatch one persisted commander work package through a worker adapter.")
    commander_dispatch.add_argument("--task-id", required=True)
    commander_dispatch.add_argument("--adapter", choices=["mock", "hermes", "openclaw"], default="mock")
    commander_dispatch.add_argument("--confirm-run", action="store_true", help="Required for Hermes/OpenClaw live execution.")
    commander_dispatch.add_argument("--worker-agent-id", default=None)
    commander_dispatch.add_argument("--hermes-timeout", type=int, default=300)
    commander_dispatch.set_defaults(handler="commander_dispatch_package")
    commander_coding_workspace = commander_sub.add_parser("coding-workspace", help="Preview or create an isolated git worktree for one Commander coding package.")
    commander_coding_workspace.add_argument("--task-id", required=True)
    commander_coding_workspace.add_argument("--branch", default=None)
    commander_coding_workspace.add_argument("--worktree-root", default=None)
    commander_coding_workspace.add_argument("--worktree-path", default=None)
    commander_coding_workspace.add_argument("--actor-id", default="usr_founder")
    commander_coding_workspace.add_argument("--confirm-create", action="store_true", help="Actually create the git worktree and record workspace evidence.")
    commander_coding_workspace.set_defaults(handler="commander_coding_workspace")
    commander_coding_workspace_cleanup = commander_sub.add_parser("coding-workspace-cleanup", help="Preview or remove a Commander coding worktree and optional branch.")
    commander_coding_workspace_cleanup.add_argument("--task-id", required=True)
    commander_coding_workspace_cleanup.add_argument("--branch", default=None)
    commander_coding_workspace_cleanup.add_argument("--worktree-root", default=None)
    commander_coding_workspace_cleanup.add_argument("--worktree-path", default=None)
    commander_coding_workspace_cleanup.add_argument("--keep-branch", action="store_true")
    commander_coding_workspace_cleanup.add_argument("--actor-id", default="usr_founder")
    commander_coding_workspace_cleanup.add_argument("--confirm-cleanup", action="store_true", help="Actually remove the git worktree and delete the branch unless --keep-branch is set.")
    commander_coding_workspace_cleanup.set_defaults(handler="commander_coding_workspace_cleanup")
    commander_coding_evidence = commander_sub.add_parser("coding-evidence", help="Record safe patch/test/verifier evidence for a dispatched coding work package.")
    commander_coding_evidence.add_argument("--task-id", required=True)
    commander_coding_evidence.add_argument("--run-id", default="")
    commander_coding_evidence.add_argument("--branch", default=None)
    commander_coding_evidence.add_argument("--collect-from-worktree", action="store_true", help="Collect git diff/status and verifier summaries from the package worktree.")
    commander_coding_evidence.add_argument("--worktree-root", default=None)
    commander_coding_evidence.add_argument("--worktree-path", default=None)
    commander_coding_evidence.add_argument("--patch-summary", default="Patch manifest recorded as summary/hash only; raw patch omitted.")
    commander_coding_evidence.add_argument("--test-summary", default="Focused tests passed; raw logs omitted.")
    commander_coding_evidence.add_argument("--verifier-summary", default="Independent verifier evidence recorded; raw logs omitted.")
    commander_coding_evidence.add_argument("--merge-summary", default="Merge gate remains pending human approval and exact-head release checks.")
    commander_coding_evidence.add_argument("--test-status", choices=["pass", "fail", "warn"], default="pass")
    commander_coding_evidence.add_argument("--verifier-status", choices=["pass", "fail", "warn"], default="pass")
    commander_coding_evidence.add_argument("--merge-gate-status", choices=["pending_human_approval", "ready", "blocked", "not_checked"], default="pending_human_approval")
    commander_coding_evidence.add_argument("--changed-file", action="append", default=None, help="Repo-relative touched file path. Repeatable; raw file bodies are never sent.")
    commander_coding_evidence.add_argument("--verification-command", action="append", default=None, help="Verification command summary. Repeatable.")
    commander_coding_evidence.add_argument("--actor-id", default="usr_founder")
    commander_coding_evidence.add_argument("--confirm-record", action="store_true", help="Actually record artifacts/evaluation/audit. Omitted means preview only.")
    commander_coding_evidence.set_defaults(handler="commander_coding_evidence")
    commander_dispatch_batch = commander_sub.add_parser("dispatch-batch", help="Queue multiple persisted commander work packages as async workflow jobs.")
    commander_dispatch_batch.add_argument("--project-id", default=None)
    commander_dispatch_batch.add_argument("--plan-id", default=None)
    commander_dispatch_batch.add_argument("--task-id", action="append", default=None, help="Exact task id to queue. Repeatable.")
    commander_dispatch_batch.add_argument("--status", default="planned")
    commander_dispatch_batch.add_argument("--limit", type=int, default=5)
    commander_dispatch_batch.add_argument("--adapter", choices=["mock", "hermes", "openclaw"], default="mock")
    commander_dispatch_batch.add_argument("--confirm-run", action="store_true", help="Required for Hermes/OpenClaw live execution.")
    commander_dispatch_batch.add_argument("--hermes-timeout", type=int, default=300)
    commander_dispatch_batch.add_argument("--wait", action="store_true", help="Poll queued workflow jobs until completion or timeout.")
    commander_dispatch_batch.add_argument("--wait-timeout-sec", type=int, default=60)
    commander_dispatch_batch.add_argument("--poll-interval", type=float, default=0.5)
    commander_dispatch_batch.set_defaults(handler="commander_dispatch_batch")
    commander_synthesize = commander_sub.add_parser("synthesize", help="Preview or create a synthesis artifact from returned commander work packages.")
    commander_synthesize.add_argument("--project-id", default=None)
    commander_synthesize.add_argument("--plan-id", default=None)
    commander_synthesize.add_argument("--task-id", action="append", default=None, help="Exact task id to include. Repeatable.")
    commander_synthesize.add_argument("--status", default="ready_for_review")
    commander_synthesize.add_argument("--limit", type=int, default=10)
    commander_synthesize.add_argument("--artifact-id", default=None)
    commander_synthesize.add_argument("--confirm-create", action="store_true", help="Actually persist the synthesis artifact; omitted means preview only.")
    commander_synthesize.set_defaults(handler="commander_synthesize")
    commander_promote = commander_sub.add_parser("promote-synthesis", help="Promote an approved commander synthesis to memory and/or delivery artifacts.")
    commander_promote.add_argument("--artifact-id", required=True)
    commander_promote.add_argument("--approval-id", default=None)
    commander_promote.add_argument("--mode", choices=["memory", "delivery", "both"], default="both")
    commander_promote.add_argument("--project-id", default=None)
    commander_promote.add_argument("--memory-id", default=None)
    commander_promote.add_argument("--delivery-artifact-id", default=None)
    commander_promote.add_argument("--confirm-promote", action="store_true", help="Actually create memory/delivery rows; omitted means preview only.")
    commander_promote.set_defaults(handler="commander_promote_synthesis")

    review = sub.add_parser("review", help="Human review queue commands.")
    review_sub = review.add_subparsers(dest="action", required=True)
    review_queue = review_sub.add_parser("queue", help="Read pending approvals, memory candidates and customer deliveries.")
    review_queue.add_argument("--limit", type=int, default=20)
    review_queue.set_defaults(handler="review_queue")

    security = sub.add_parser("security", help="Read-only security and production-readiness checks.")
    security_sub = security.add_subparsers(dest="action", required=True)
    security_prod = security_sub.add_parser("production-readiness", help="Show whether the local Gateway is safe for shared/production use.")
    security_prod.set_defaults(handler="security_production_readiness")

    agent = sub.add_parser("agent", help="Agent identity commands.")
    agent_sub = agent.add_subparsers(dest="action", required=True)
    register = agent_sub.add_parser("register", help="Register or update an AI digital employee.")
    register.add_argument("--id", default=None)
    register.add_argument("--name", required=True)
    register.add_argument("--role", default="AI Digital Employee")
    register.add_argument("--runtime", default="mock")
    register.add_argument("--model-provider", default="local")
    register.add_argument("--model-name", default="agentops-cli")
    register.add_argument("--permission-level", default="standard")
    register.add_argument("--allowed-tools", default="agent_gateway.task,agent_gateway.run,agent_gateway.audit")
    register.add_argument("--budget", type=float, default=5.0)
    register.add_argument("--description", default="Registered through agentops CLI.")
    register.set_defaults(handler="agent_register")

    heartbeat = agent_sub.add_parser("heartbeat", help="Send agent heartbeat.")
    heartbeat.add_argument("--id", default=None)
    heartbeat.add_argument("--status", default="idle", choices=["idle", "running", "paused", "error", "disabled"])
    heartbeat.add_argument("--summary", default="CLI heartbeat.")
    heartbeat.add_argument("--runtime", default="mock")
    heartbeat.set_defaults(handler="agent_heartbeat")

    task = sub.add_parser("task", help="Task pull/claim commands.")
    task_sub = task.add_subparsers(dest="action", required=True)
    create = task_sub.add_parser("create", help="Create a normal MIS task for agents/workers.")
    create.add_argument("--task-id", default=None)
    create.add_argument("--title", required=True)
    create.add_argument("--description", default="")
    create.add_argument("--requester-id", default="usr_customer_demo")
    create.add_argument("--owner-agent-id", default=None)
    create.add_argument("--collaborator-agent-id", action="append", default=None, help="Optional collaborator agent id. Repeatable.")
    create.add_argument("--status", default="planned", choices=["backlog", "planned", "running", "waiting_approval", "blocked", "completed", "failed", "canceled"])
    create.add_argument("--priority", default="medium", choices=["low", "medium", "high", "critical"])
    create.add_argument("--due-date", default=None)
    create.add_argument("--acceptance", default="Worker must satisfy task acceptance criteria and write ledger evidence.")
    create.add_argument("--risk", default="medium", choices=["low", "medium", "high", "critical"])
    create.add_argument("--budget", type=float, default=3.0)
    create.set_defaults(handler="task_create")

    task_list = task_sub.add_parser("list", help="List normal MIS tasks with optional local filtering.")
    task_list.add_argument("--limit", type=int, default=25)
    task_list.add_argument("--status", action="append", default=None, help="Task status filter. Can be repeated.")
    task_list.add_argument("--owner-agent-id", default=None)
    task_list.add_argument("--requester-id", default=None)
    task_list.set_defaults(handler="task_list")

    task_get = task_sub.add_parser("get", help="Inspect one task plus run/evaluation/artifact evidence.")
    task_get.add_argument("--task-id", required=True)
    task_get.set_defaults(handler="task_get")

    pull = task_sub.add_parser("pull", help="Pull available tasks for an agent.")
    pull.add_argument("--agent-id", default=None)
    pull.add_argument("--task-id", default=None, help="Optional exact task id to test pull visibility.")
    pull.add_argument("--limit", type=int, default=10)
    pull.add_argument("--status", action="append", default=None, help="Task status filter. Can be repeated.")
    pull.add_argument("--enforce-intake", action="store_true", help="Exclude tasks blocked by Agent Plan, knowledge, base-reference, or risk-boundary intake gates.")
    pull.set_defaults(handler="task_pull")

    claim = task_sub.add_parser("claim", help="Claim a task.")
    claim.add_argument("--task-id", required=True)
    claim.add_argument("--agent-id", default=None)
    claim.add_argument("--runtime", default="mock")
    claim.set_defaults(handler="task_claim")

    run = sub.add_parser("run", help="Run lifecycle commands.")
    run_sub = run.add_subparsers(dest="action", required=True)
    run_list = run_sub.add_parser("list", help="List runs with optional task/agent/status filtering.")
    run_list.add_argument("--task-id", default=None)
    run_list.add_argument("--agent-id", default=None)
    run_list.add_argument("--status", action="append", default=None, help="Run status filter. Can be repeated.")
    run_list.add_argument("--limit", type=int, default=25)
    run_list.set_defaults(handler="run_list")

    run_get = run_sub.add_parser("get", help="Inspect one run plus tool/evaluation/artifact evidence.")
    run_get.add_argument("--run-id", required=True)
    run_get.set_defaults(handler="run_get")

    graph = run_sub.add_parser("graph", help="Inspect parent/child delegation graph for one run.")
    graph.add_argument("--run-id", required=True)
    graph.set_defaults(handler="run_graph")

    evidence_graph = run_sub.add_parser("evidence-graph", help="Inspect task/run/evidence readback graph for one run.")
    evidence_graph.add_argument("--run-id", required=True)
    evidence_graph.set_defaults(handler="run_evidence_graph")

    start = run_sub.add_parser("start", help="Start a run for a task.")
    start.add_argument("--task-id", required=True)
    start.add_argument("--agent-id", default=None)
    start.add_argument("--runtime", default="mock")
    start.add_argument("--input-summary", default="")
    start.add_argument("--delegation-id", default=None)
    start.add_argument("--parent-run-id", default=None)
    start.add_argument("--plan-id", default=None, help="Verified Agent Plan id that authorizes this run.")
    start.add_argument("--approval-required", action="store_true")
    start.set_defaults(handler="run_start")

    run_hb = run_sub.add_parser("heartbeat", help="Update run status.")
    run_hb.add_argument("--run-id", required=True)
    run_hb.add_argument("--status", default="running", choices=["running", "completed", "failed", "blocked", "waiting_approval"])
    run_hb.add_argument("--summary", default="")
    run_hb.add_argument("--duration-ms", type=int, default=None)
    run_hb.add_argument("--output-tokens", type=int, default=0)
    run_hb.add_argument("--cost", type=float, default=0.0)
    run_hb.add_argument("--error-type", default=None)
    run_hb.add_argument("--error-message", default=None)
    run_hb.set_defaults(handler="run_heartbeat")

    runtime = sub.add_parser("runtime", help="Read-only runtime connector and capability commands.")
    runtime_sub = runtime.add_subparsers(dest="action", required=True)
    runtime_connectors = runtime_sub.add_parser("connectors", help="List runtime connector capability manifests and trust states.")
    runtime_connectors.set_defaults(handler="runtime_connectors")

    runtime_event = sub.add_parser("runtime-event", help="Runtime internal event evidence commands.")
    runtime_event_sub = runtime_event.add_subparsers(dest="action", required=True)
    runtime_event_record = runtime_event_sub.add_parser("record", help="Record a redacted runtime/tool event summary for a run.")
    runtime_event_record.add_argument("--run-id", required=True)
    runtime_event_record.add_argument("--agent-id", default=None)
    runtime_event_record.add_argument("--adapter", default=None, choices=["mock", "codex", "hermes", "openclaw", "agnesfallback"])
    runtime_event_record.add_argument("--connector-id", default=None)
    runtime_event_record.add_argument("--event-type", default="runtime.tool_event")
    runtime_event_record.add_argument("--status", default="completed", choices=["planned", "running", "completed", "failed", "blocked", "waiting_approval", "unavailable"])
    runtime_event_record.add_argument("--input-summary", default=None)
    runtime_event_record.add_argument("--output-summary", "--summary", dest="output_summary", default=None)
    runtime_event_record.add_argument("--error-message", default=None)
    runtime_event_record.add_argument("--latency-ms", type=int, default=None)
    runtime_event_record.add_argument("--model", default=None)
    runtime_event_record.add_argument("--prompt-hash", default=None)
    runtime_event_record.add_argument("--payload-hash", default=None)
    runtime_event_record.add_argument("--payload-json", default=None, help="Optional raw event payload used only to compute a hash server-side; it is not stored.")
    runtime_event_record.add_argument("--metadata-json", default=None)
    runtime_event_record.add_argument("--source", default="agentops-cli.runtime-event")
    runtime_event_record.set_defaults(handler="runtime_event_record")

    toolcall = sub.add_parser("toolcall", help="Tool call evidence commands.")
    tool_sub = toolcall.add_subparsers(dest="action", required=True)
    record = tool_sub.add_parser("record", help="Record a tool call.")
    record.add_argument("--run-id", required=True)
    record.add_argument("--agent-id", default=None)
    record.add_argument("--tool", required=True)
    record.add_argument("--category", default="custom")
    record.add_argument("--risk", default="low", choices=["low", "medium", "high", "critical"])
    record.add_argument("--status", default="completed")
    record.add_argument("--target", default=None)
    record.add_argument("--args-json", default=None)
    record.add_argument("--args-summary", default=None)
    record.add_argument("--summary", default="")
    record.add_argument("--prepare-action", action="store_true", help="Create a linked prepared action and approval gate for exact resume.")
    record.add_argument("--action-type", default=None, help="Prepared action type. Defaults to --tool.")
    record.add_argument("--policy-version", default="approval-wall-v1")
    record.add_argument("--checkpoint-json", default="{}")
    record.add_argument("--idempotency-key", default=None)
    record.add_argument("--approval-reason", default=None)
    record.add_argument("--approver", default="usr_founder")
    record.set_defaults(handler="toolcall_record")

    artifact = sub.add_parser("artifact", help="Artifact evidence commands.")
    artifact_sub = artifact.add_subparsers(dest="action", required=True)
    artifact_list = artifact_sub.add_parser("list", help="List artifact summaries without fetching raw content.")
    artifact_list.add_argument("--task-id", default=None)
    artifact_list.add_argument("--run-id", default=None)
    artifact_list.add_argument("--type", default=None)
    artifact_list.add_argument("--limit", type=int, default=25)
    artifact_list.set_defaults(handler="artifact_list")

    artifact_record = artifact_sub.add_parser("record", help="Record an artifact summary without storing raw content.")
    artifact_record.add_argument("--run-id", required=True)
    artifact_record.add_argument("--task-id", default=None)
    artifact_record.add_argument("--agent-id", default=None)
    artifact_record.add_argument("--artifact-id", default=None)
    artifact_record.add_argument("--type", default="report")
    artifact_record.add_argument("--title", required=True)
    artifact_record.add_argument("--uri", default=None)
    artifact_record.add_argument("--summary", required=True)
    artifact_record.add_argument("--content-hash", default=None)
    artifact_record.set_defaults(handler="artifact_record")

    experiment = sub.add_parser("experiment", help="Validate, run, inspect, or sync Research Lab experiments.")
    experiment_sub = experiment.add_subparsers(dest="experiment_action", required=True)
    experiment_validate = experiment_sub.add_parser("validate", help="Validate a Research Lab experiment spec.")
    experiment_validate.add_argument("--spec", required=True)
    experiment_validate.add_argument("--servers", default=None, help="Non-secret server profile file for SSH spec validation.")
    experiment_validate.set_defaults(handler="experiment")
    experiment_run = experiment_sub.add_parser("run", help="Plan or explicitly confirm a local Research Lab run.")
    experiment_run.add_argument("--spec", required=True)
    experiment_run.add_argument("--state-dir", default=".research-lab")
    experiment_run.add_argument("--confirm-run", action="store_true", help="Execute the run. The default only returns a dry-run plan.")
    experiment_run.set_defaults(handler="experiment")
    experiment_show = experiment_sub.add_parser("show", help="Read a bounded Research Lab experiment summary.")
    experiment_show.add_argument("--experiment-id", required=True)
    experiment_show.add_argument("--state-dir", default=".research-lab")
    experiment_show.set_defaults(handler="experiment")
    experiment_sync = experiment_sub.add_parser("sync", help="Plan or explicitly confirm bounded evidence sync to AgentOps MIS.")
    experiment_sync.add_argument("--experiment-id", required=True)
    experiment_sync.add_argument("--state-dir", default=".research-lab")
    experiment_sync.add_argument("--confirm-sync", action="store_true", help="Publish bounded evidence. The default only returns a sync plan.")
    experiment_sync.set_defaults(handler="experiment")

    knowledge = sub.add_parser("knowledge", help="Knowledge base and Markdown index commands.")
    knowledge_sub = knowledge.add_subparsers(dest="action", required=True)
    knowledge_search = knowledge_sub.add_parser("search", help="Search indexed specs, base notes, runbooks and shared memory.")
    knowledge_search.add_argument("query", nargs="?", default="")
    knowledge_search.add_argument("--limit", type=int, default=10)
    knowledge_search.add_argument("--refresh", action="store_true")
    knowledge_search.set_defaults(handler="knowledge_search")
    knowledge_index = knowledge_sub.add_parser("index", help="Refresh the local Markdown knowledge FTS index.")
    knowledge_index.add_argument("--rebuild", action="store_true")
    knowledge_index.set_defaults(handler="knowledge_index")
    knowledge_packet = knowledge_sub.add_parser("evidence-packet", help="Read retrieval quality and provenance proof without exposing raw content.")
    knowledge_packet.add_argument("query", nargs="?", default="")
    knowledge_packet.add_argument("--task-id", default=None, help="Build the retrieval packet from a MIS task without exposing raw task text.")
    knowledge_packet.add_argument("--adapter", default=None, help="Optional runtime adapter hint used only for task-aware query hashing.")
    knowledge_packet.add_argument("--limit", type=int, default=5)
    knowledge_packet.add_argument("--baseline-limit", type=int, default=5)
    knowledge_packet.set_defaults(handler="knowledge_evidence_packet")
    knowledge_context = knowledge_sub.add_parser("context-packet", help="Read bounded project summaries and approved memories for transient model context.")
    knowledge_context.add_argument("query", nargs="?", default="")
    knowledge_context.add_argument("--task-id", default=None, help="Build context from an existing MIS task without returning raw task text.")
    knowledge_context.add_argument("--adapter", default=None, help="Optional runtime adapter hint used for task-aware retrieval.")
    knowledge_context.add_argument("--limit", type=int, default=5, help="Maximum versioned knowledge summaries (1-8).")
    knowledge_context.add_argument("--memory-limit", type=int, default=3, help="Maximum approved canonical memories (0-5).")
    knowledge_context.add_argument("--block-chars", type=int, default=480, help="Maximum characters per redacted summary (120-800).")
    knowledge_context.add_argument("--max-chars", type=int, default=4000, help="Maximum combined context characters (600-6000).")
    knowledge_context.set_defaults(handler="knowledge_context_packet")

    agent_plan = sub.add_parser("agent-plan", help="Agent work method plan commands.")
    agent_plan_sub = agent_plan.add_subparsers(dest="action", required=True)
    agent_plan_create = agent_plan_sub.add_parser("create", help="Submit the required READ/PLAN/RETRIEVE/COMPARE execution plan.")
    agent_plan_create.add_argument("--agent-id", default=None)
    agent_plan_create.add_argument("--task-id", default=None)
    agent_plan_create.add_argument("--run-id", default=None)
    agent_plan_create.add_argument("--task-understanding", "--understanding", dest="task_understanding", required=True)
    agent_plan_create.add_argument("--referenced-specs", default="")
    agent_plan_create.add_argument("--referenced-memories", default="")
    agent_plan_create.add_argument("--referenced-bases", default="")
    agent_plan_create.add_argument("--proposed-files-to-change", default="")
    agent_plan_create.add_argument("--risk", default="medium", choices=["low", "medium", "high", "critical"])
    agent_plan_create.add_argument("--approval-required", action="store_true")
    agent_plan_create.add_argument("--execution-steps", default="")
    agent_plan_create.add_argument("--execution-steps-json", default=None)
    agent_plan_create.add_argument("--verification-plan", default="")
    agent_plan_create.add_argument("--rollback-plan", default="")
    agent_plan_create.add_argument("--status", default="submitted", choices=["draft", "submitted"])
    agent_plan_create.set_defaults(handler="agent_plan_create")
    agent_plan_list = agent_plan_sub.add_parser("list", help="List submitted agent plans.")
    agent_plan_list.add_argument("--task-id", default=None)
    agent_plan_list.add_argument("--run-id", default=None)
    agent_plan_list.add_argument("--agent-id", default=None)
    agent_plan_list.add_argument("--limit", type=int, default=25)
    agent_plan_list.set_defaults(handler="agent_plan_list")
    agent_plan_get = agent_plan_sub.add_parser("get", help="Read one agent plan.")
    agent_plan_get.add_argument("--plan-id", required=True)
    agent_plan_get.set_defaults(handler="agent_plan_get")
    agent_plan_verify = agent_plan_sub.add_parser("verify", help="Verify one agent plan has required method-block evidence.")
    agent_plan_verify.add_argument("--plan-id", required=True)
    agent_plan_verify.set_defaults(handler="agent_plan_verify")
    agent_plan_approve = agent_plan_sub.add_parser("approve", help="Approve a verified Agent Plan through the human/admin governance API.")
    agent_plan_approve.add_argument("--plan-id", required=True)
    agent_plan_approve.add_argument("--approver-user-id", default="usr_founder")
    agent_plan_approve.add_argument("--actor-type", default="user", choices=["user", "admin", "policy"])
    agent_plan_approve.add_argument("--reason", default="CLI Agent Plan approval.")
    agent_plan_approve.set_defaults(handler="agent_plan_approve")
    agent_plan_reject = agent_plan_sub.add_parser("reject", help="Reject an Agent Plan through the human/admin governance API.")
    agent_plan_reject.add_argument("--plan-id", required=True)
    agent_plan_reject.add_argument("--approver-user-id", default="usr_founder")
    agent_plan_reject.add_argument("--actor-type", default="user", choices=["user", "admin", "policy"])
    agent_plan_reject.add_argument("--reason", default="CLI Agent Plan rejection.")
    agent_plan_reject.set_defaults(handler="agent_plan_reject")

    plan_evidence = sub.add_parser("plan-evidence", help="Bind verified agent plans to run/tool/eval/artifact/audit evidence.")
    plan_evidence_sub = plan_evidence.add_subparsers(dest="action", required=True)
    plan_evidence_create = plan_evidence_sub.add_parser("create", help="Create a plan_evidence_manifest for a run.")
    plan_evidence_create.add_argument("--agent-id", default=None)
    plan_evidence_create.add_argument("--manifest-id", default=None)
    plan_evidence_create.add_argument("--plan-id", required=True)
    plan_evidence_create.add_argument("--task-id", default=None)
    plan_evidence_create.add_argument("--run-id", required=True)
    plan_evidence_create.add_argument("--mismatch-policy", default="block", choices=["block", "warn"])
    plan_evidence_create.add_argument("--expected-steps", default="")
    plan_evidence_create.add_argument("--expected-steps-json", default=None)
    plan_evidence_create.add_argument("--tool-call-ids", default="")
    plan_evidence_create.add_argument("--evaluation-ids", default="")
    plan_evidence_create.add_argument("--artifact-ids", default="")
    plan_evidence_create.add_argument("--audit-ids", default="")
    plan_evidence_create.add_argument("--no-verify", action="store_true")
    plan_evidence_create.set_defaults(handler="plan_evidence_create")
    plan_evidence_list = plan_evidence_sub.add_parser("list", help="List plan evidence manifests.")
    plan_evidence_list.add_argument("--plan-id", default=None)
    plan_evidence_list.add_argument("--task-id", default=None)
    plan_evidence_list.add_argument("--run-id", default=None)
    plan_evidence_list.add_argument("--agent-id", default=None)
    plan_evidence_list.add_argument("--limit", type=int, default=25)
    plan_evidence_list.set_defaults(handler="plan_evidence_list")
    plan_evidence_get = plan_evidence_sub.add_parser("get", help="Read one plan evidence manifest.")
    plan_evidence_get.add_argument("--manifest-id", required=True)
    plan_evidence_get.set_defaults(handler="plan_evidence_get")
    plan_evidence_verify = plan_evidence_sub.add_parser("verify", help="Re-verify a plan evidence manifest against the ledger.")
    plan_evidence_verify.add_argument("--manifest-id", required=True)
    plan_evidence_verify.set_defaults(handler="plan_evidence_verify")

    approval = sub.add_parser("approval", help="Approval commands.")
    approval_sub = approval.add_subparsers(dest="action", required=True)
    approval_list = approval_sub.add_parser("list", help="List approvals for operator review.")
    approval_list.add_argument("--decision", choices=["pending", "approved", "rejected", "expired"], default=None)
    approval_list.add_argument("--task-id", default=None)
    approval_list.add_argument("--run-id", default=None)
    approval_list.add_argument("--limit", type=int, default=25)
    approval_list.set_defaults(handler="approval_list")
    approval_inspect = approval_sub.add_parser("inspect", help="Inspect approval evidence and risk before deciding.")
    approval_inspect.add_argument("--approval-id", required=True)
    approval_inspect.set_defaults(handler="approval_inspect")
    approval_prepared = approval_sub.add_parser("prepared-action", help="Create, inspect, and resume exact approval-gated prepared actions.")
    approval_prepared_sub = approval_prepared.add_subparsers(dest="prepared_action_action", required=True)
    prepared_create = approval_prepared_sub.add_parser("create", help="Create a prepared action and linked approval gate.")
    prepared_create.add_argument("--run-id", required=True)
    prepared_create.add_argument("--tool-call-id", default=None)
    prepared_create.add_argument("--agent-id", default=None)
    prepared_create.add_argument("--action-type", required=True)
    prepared_create.add_argument("--args-json", default="{}")
    prepared_create.add_argument("--target-resource", default=None)
    prepared_create.add_argument("--risk-level", choices=["low", "medium", "high", "critical"], default="high")
    prepared_create.add_argument("--policy-version", default="approval-wall-v1")
    prepared_create.add_argument("--checkpoint-json", default="{}")
    prepared_create.add_argument("--idempotency-key", default=None)
    prepared_create.add_argument("--approver", default="usr_founder")
    prepared_create.add_argument("--reason", default="Prepared action requires human approval before exact resume.")
    prepared_create.set_defaults(handler="approval_prepared_action_create")
    prepared_get = approval_prepared_sub.add_parser("get", help="Inspect one prepared action and verify its action hash.")
    prepared_get.add_argument("--action-id", required=True)
    prepared_get.set_defaults(handler="approval_prepared_action_get")
    prepared_resume = approval_prepared_sub.add_parser("resume", help="Resume an approved prepared action exactly once.")
    prepared_resume.add_argument("--action-id", required=True)
    prepared_resume.add_argument("--agent-id", default=None)
    prepared_resume.add_argument("--provider-side-effect-id", default=None)
    prepared_resume.add_argument("--result-summary", default=None)
    prepared_resume.set_defaults(handler="approval_prepared_action_resume")
    approval_approve = approval_sub.add_parser("approve", help="Approve an approval gate and sync linked ledger rows.")
    approval_approve.add_argument("--approval-id", required=True)
    approval_approve.set_defaults(handler="approval_approve")
    approval_reject = approval_sub.add_parser("reject", help="Reject an approval gate and block linked ledger rows.")
    approval_reject.add_argument("--approval-id", required=True)
    approval_reject.set_defaults(handler="approval_reject")
    request = approval_sub.add_parser("request", help="Request human approval.")
    request.add_argument("--task-id", required=True)
    request.add_argument("--run-id", required=True)
    request.add_argument("--tool-call-id", default=None)
    request.add_argument("--agent-id", default=None)
    request.add_argument("--approver", default="usr_founder")
    request.add_argument("--reason", required=True)
    request.set_defaults(handler="approval_request")

    memory = sub.add_parser("memory", help="Memory commands.")
    memory_sub = memory.add_subparsers(dest="action", required=True)
    memory_list = memory_sub.add_parser("list", help="List reviewable memory candidates.")
    memory_list.add_argument("--status", choices=["candidate", "approved", "rejected", "stale", "superseded"], default=None)
    memory_list.add_argument("--scope", choices=["task", "project", "org"], default=None)
    memory_list.add_argument("--type", default=None)
    memory_list.add_argument("--task-id", default=None)
    memory_list.add_argument("--agent-id", default=None)
    memory_list.add_argument("--limit", type=int, default=25)
    memory_list.set_defaults(handler="memory_list")
    memory_approve = memory_sub.add_parser("approve", help="Approve a memory candidate.")
    memory_approve.add_argument("--memory-id", required=True)
    memory_approve.set_defaults(handler="memory_approve")
    memory_reject = memory_sub.add_parser("reject", help="Reject a memory candidate.")
    memory_reject.add_argument("--memory-id", required=True)
    memory_reject.set_defaults(handler="memory_reject")
    propose = memory_sub.add_parser("propose", help="Propose reviewable memory.")
    propose.add_argument("--agent-id", default=None)
    propose.add_argument("--task-id", default=None)
    propose.add_argument("--run-id", default=None)
    propose.add_argument("--scope", default="project", choices=["task", "project", "org"])
    propose.add_argument("--type", default="artifact_summary")
    propose.add_argument("--text", required=True)
    propose.add_argument("--source-ref", default=None)
    propose.add_argument("--access-tags", default="agentops-cli,review")
    propose.add_argument("--confidence", type=float, default=0.72)
    propose.set_defaults(handler="memory_propose")

    eval_parser = sub.add_parser("eval", help="Evaluation commands.")
    eval_sub = eval_parser.add_subparsers(dest="action", required=True)
    submit = eval_sub.add_parser("submit", help="Submit evaluation result.")
    submit.add_argument("--run-id", required=True)
    submit.add_argument("--task-id", default=None)
    submit.add_argument("--agent-id", default=None)
    submit.add_argument("--gate", default="agentops_cli_gate")
    submit.add_argument("--score", type=float, default=1.0)
    submit.add_argument("--pass", dest="passed", action="store_true")
    submit.add_argument("--fail", dest="passed", action="store_false")
    submit.set_defaults(passed=True)
    submit.add_argument("--evaluator-type", default="rule", choices=["human", "rule", "llm_mock"])
    submit.add_argument("--rubric-json", default=None)
    submit.add_argument("--notes", default="Submitted through agentops CLI.")
    submit.set_defaults(handler="eval_submit")
    cases = eval_sub.add_parser("cases", help="List evaluation case candidates.")
    cases.add_argument("--status", default="candidate", choices=["candidate", "approved", "rejected", "stale", "superseded"])
    cases.add_argument("--limit", type=int, default=25)
    cases.add_argument("--run-id", default=None)
    cases.add_argument("--task-id", default=None)
    cases.add_argument("--artifact-id", default=None)
    cases.set_defaults(handler="eval_cases")
    case_runs = eval_sub.add_parser("case-runs", help="List local benchmark evidence produced from approved evaluation cases.")
    case_runs.add_argument("--limit", type=int, default=25)
    case_runs.add_argument("--case-id", default=None)
    case_runs.add_argument("--run-id", default=None)
    case_runs.add_argument("--task-id", default=None)
    case_runs.add_argument("--pass-fail", default=None, choices=["pass", "fail"])
    case_runs.add_argument("--review-status", default=None, choices=["open", "investigating", "acknowledged", "waived"])
    case_runs.set_defaults(handler="eval_case_runs")
    review_case_run = eval_sub.add_parser("review-case-run", help="Mark a failed evaluation case run as investigating, acknowledged, waived, or open.")
    review_case_run.add_argument("--case-run-id", required=True)
    review_case_run.add_argument("--status", default="acknowledged", choices=["open", "investigating", "acknowledged", "waived"])
    review_case_run.add_argument("--note", default="Reviewed from agentops CLI.")
    review_case_run.add_argument("--actor-id", default="usr_operator")
    review_case_run.set_defaults(handler="eval_review_case_run")
    remediate_case_run = eval_sub.add_parser("remediate-case-run", help="Preview or create a normal MIS task from a failed evaluation case run.")
    remediate_case_run.add_argument("--case-run-id", required=True)
    remediate_case_run.add_argument("--task-id", default=None)
    remediate_case_run.add_argument("--title", default=None)
    remediate_case_run.add_argument("--project-id", default=None)
    remediate_case_run.add_argument("--plan-id", default=None)
    remediate_case_run.add_argument("--lane-id", default=None)
    remediate_case_run.add_argument("--owner-agent-id", default=None)
    remediate_case_run.add_argument("--priority", default=None, choices=["low", "medium", "high", "critical"])
    remediate_case_run.add_argument("--risk-level", default=None, choices=["low", "medium", "high", "critical"])
    remediate_case_run.add_argument("--budget-limit-usd", type=float, default=None)
    remediate_case_run.add_argument("--actor-id", default="usr_founder")
    remediate_case_run.add_argument("--confirm-create", action="store_true")
    remediate_case_run.set_defaults(handler="eval_remediate_case_run")
    propose_case = eval_sub.add_parser("propose-case", help="Preview or create an evaluation case candidate from run/eval/artifact evidence.")
    propose_case.add_argument("--case-id", default=None)
    propose_case.add_argument("--source-type", default=None, choices=["evaluation", "customer_delivery", "run", "artifact", "manual", "commander_synthesis"])
    propose_case.add_argument("--source-ref", default=None)
    propose_case.add_argument("--evaluation-id", default=None)
    propose_case.add_argument("--artifact-id", default=None)
    propose_case.add_argument("--run-id", default=None)
    propose_case.add_argument("--task-id", default=None)
    propose_case.add_argument("--agent-id", default=None)
    propose_case.add_argument("--case-type", default=None, choices=["regression", "golden", "safety", "quality", "cost", "tool_use", "memory"])
    propose_case.add_argument("--title", default=None)
    propose_case.add_argument("--input-summary", default=None)
    propose_case.add_argument("--expected-output-summary", default=None)
    propose_case.add_argument("--failure-mode", default=None)
    propose_case.add_argument("--confidence", type=float, default=0.72)
    propose_case.add_argument("--rubric-json", default=None)
    propose_case.add_argument("--confirm-create", action="store_true")
    propose_case.set_defaults(handler="eval_propose_case")
    approve_case = eval_sub.add_parser("approve-case", help="Approve an evaluation case candidate for future regression use.")
    approve_case.add_argument("--case-id", required=True)
    approve_case.set_defaults(handler="eval_approve_case")
    reject_case = eval_sub.add_parser("reject-case", help="Reject a noisy evaluation case candidate.")
    reject_case.add_argument("--case-id", required=True)
    reject_case.set_defaults(handler="eval_reject_case")
    run_cases = eval_sub.add_parser("run-cases", help="Preview or execute approved evaluation cases through the local benchmark runner.")
    run_cases.add_argument("--case-id", action="append", default=None, help="Approved case id to run. Repeatable. Defaults to latest approved cases.")
    run_cases.add_argument("--case-type", default=None, choices=["regression", "golden", "safety", "quality", "cost", "tool_use", "memory"])
    run_cases.add_argument("--status", default="approved", choices=["candidate", "approved", "rejected", "stale", "superseded"])
    run_cases.add_argument("--runner-type", default="rule", choices=["rule", "llm_mock"])
    run_cases.add_argument("--agent-id", default=None)
    run_cases.add_argument("--task-id", default=None)
    run_cases.add_argument("--run-id", default=None)
    run_cases.add_argument("--artifact-id", default=None)
    run_cases.add_argument("--limit", type=int, default=10)
    run_cases.add_argument("--min-score", type=float, default=0.75)
    run_cases.add_argument("--confirm-run", action="store_true")
    run_cases.set_defaults(handler="eval_run_cases")

    audit_parser = sub.add_parser("audit", help="Audit commands.")
    audit_sub = audit_parser.add_subparsers(dest="action", required=True)
    audit_emit = audit_sub.add_parser("emit", help="Emit audit event.")
    audit_emit.add_argument("--agent-id", default=None)
    audit_emit.add_argument("--action", required=True)
    audit_emit.add_argument("--entity-type", required=True)
    audit_emit.add_argument("--entity-id", required=True)
    audit_emit.add_argument("--task-id", default=None)
    audit_emit.add_argument("--run-id", default=None)
    audit_emit.add_argument("--metadata-json", default=None)
    audit_emit.set_defaults(handler="audit_emit")

    workflow = sub.add_parser("workflow", help="Customer-facing workflow commands.")
    workflow_sub = workflow.add_subparsers(dest="action", required=True)
    templates_cmd = workflow_sub.add_parser("templates", help="List customer task templates.")
    templates_cmd.set_defaults(handler="workflow_templates")
    delivery_board = workflow_sub.add_parser("delivery-board", help="Read customer delivery evidence board without mutating the ledger.")
    delivery_board.add_argument("--limit", type=int, default=12)
    delivery_board.set_defaults(handler="workflow_delivery_board")
    loop_lane = workflow_sub.add_parser("hermes-openclaw-loop", help="Run or read back the supervised Hermes/OpenClaw loop lane.")
    loop_lane.add_argument("--topic", default="Review the supervised Hermes/OpenClaw loop lane.")
    loop_lane.add_argument("--rounds", type=int, default=1)
    loop_lane.add_argument("--mode", choices=["dry-run", "live-hermes", "live-openclaw", "live-both"], default="dry-run")
    loop_lane.add_argument("--confirm-live", action="store_true")
    loop_lane.add_argument("--loop-id", default="")
    loop_lane.add_argument("--resume", action="store_true")
    loop_lane.add_argument("--order", nargs="+", choices=["hermes", "openclaw"], default=["hermes", "openclaw"])
    loop_lane.add_argument("--request-timeout", type=int, default=30)
    loop_lane.add_argument("--max-agent-attempts", type=int, default=1)
    loop_lane.add_argument("--retry-delay-sec", type=float, default=1.0)
    loop_lane.add_argument("--simulate-failure-agent", action="append", choices=["hermes", "openclaw"], default=None)
    loop_lane.add_argument("--readback", action="store_true", help="Read ledger evidence for --loop-id instead of running a new loop.")
    loop_lane.add_argument("--limit", type=int, default=10)
    loop_lane.set_defaults(handler="workflow_hermes_openclaw_loop")
    run_template = workflow_sub.add_parser("run-template", help="Run a customer task template through the MIS workflow layer.")
    run_template.add_argument("--template-id", default="tpl_customer_kb_qa_bot")
    run_template.add_argument("--adapter", choices=["mock", "hermes", "openclaw"], default=None, help="Optional Agent Worker adapter. Without this, the template uses its default safe workflow.")
    run_template.add_argument("--confirm-run", action="store_true", help="Required when --adapter is hermes or openclaw.")
    run_template.add_argument("--title", default="")
    run_template.add_argument("--description", default="")
    run_template.add_argument("--acceptance", default="")
    run_template.add_argument("--priority", choices=["low", "medium", "high", "critical"], default=None)
    run_template.add_argument("--risk", choices=["low", "medium", "high", "critical"], default=None)
    run_template.add_argument("--selected-agent-id", action="append", default=None)
    run_template.add_argument("--owner-agent-id", default=None)
    run_template.add_argument("--worker-agent-id", default=None)
    run_template.add_argument("--hermes-timeout", type=int, default=None)
    run_template.add_argument("--request-timeout", type=int, default=None)
    run_template.add_argument("--async-job", action="store_true", help="Submit a workflow job and return immediately; use workflow job-status to poll.")
    run_template.set_defaults(handler="workflow_run_template")
    jobs_list = workflow_sub.add_parser("jobs", help="List async workflow jobs with read-only queue summary.")
    jobs_list.add_argument("--status", default="", help="Optional comma-separated status filter: queued,running,completed,failed.")
    jobs_list.add_argument("--workflow-type", default="", help="Optional comma-separated workflow_type filter.")
    jobs_list.add_argument("--limit", type=int, default=25)
    jobs_list.set_defaults(handler="workflow_jobs")
    job_status = workflow_sub.add_parser("job-status", help="Inspect or wait for a submitted workflow job.")
    job_status.add_argument("--job-id", required=True)
    job_status.add_argument("--wait", action="store_true")
    job_status.add_argument("--poll-interval", type=float, default=1.0)
    job_status.add_argument("--timeout", type=int, default=120)
    job_status.set_defaults(handler="workflow_job_status")
    stuck_jobs = workflow_sub.add_parser("stuck-jobs", help="List queued/running workflow jobs that exceeded a threshold.")
    stuck_jobs.add_argument("--threshold-sec", type=int, default=900)
    stuck_jobs.add_argument("--limit", type=int, default=25)
    stuck_jobs.set_defaults(handler="workflow_stuck_jobs")
    job_mark_failed = workflow_sub.add_parser("job-mark-failed", help="Mark a stale queued/running workflow job as failed after operator review.")
    job_mark_failed.add_argument("--job-id", required=True)
    job_mark_failed.add_argument("--reason", default="Operator marked stale workflow job as failed.")
    job_mark_failed.add_argument("--actor-id", default="usr_operator")
    job_mark_failed.set_defaults(handler="workflow_job_mark_failed")
    recover_job = workflow_sub.add_parser("recover-job", help="Preview or execute receipt-backed workflow job recovery.")
    recover_job.add_argument("--job-id", required=True)
    recover_job.add_argument("--mode", choices=["mark-failed", "retry"], default="mark-failed")
    recover_job.add_argument("--reason", default="workflow job recovery requested by operator.")
    recover_job.add_argument("--task-id", default=None, help="Required for retry when the failed job has no result_task_id.")
    recover_job.add_argument("--adapter", choices=["mock", "hermes", "openclaw"], default=None)
    recover_job.add_argument("--actor-id", default="usr_operator")
    recover_job.add_argument("--confirm-recover", action="store_true", help="Actually perform recovery. Omitted means preview only.")
    recover_job.add_argument("--record-receipt", action="store_true", help="Record an operator action receipt after confirmed recovery.")
    recover_job.add_argument("--confirm-run", action="store_true", help="Required for Hermes/OpenClaw retry dispatch.")
    recover_job.add_argument("--hermes-timeout", type=int, default=300)
    recover_job.set_defaults(handler="workflow_recover_job")
    customer_worker = workflow_sub.add_parser("customer-worker-task", help="Dispatch a customer task through the AgentOps worker loop.")
    customer_worker.add_argument("--adapter", choices=["mock", "hermes", "openclaw"], default="mock")
    customer_worker.add_argument("--confirm-run", action="store_true", help="Required for Hermes/OpenClaw live execution.")
    customer_worker.add_argument("--title", required=True)
    customer_worker.add_argument("--description", required=True)
    customer_worker.add_argument("--acceptance", default="Worker must write run, tool, evaluation, audit and artifact evidence.")
    customer_worker.add_argument("--task-id", default=None, help="Optional existing task id to execute; useful for task-bound evaluation cases.")
    customer_worker.add_argument("--priority", choices=["low", "medium", "high", "critical"], default="high")
    customer_worker.add_argument("--risk", choices=["low", "medium", "high", "critical"], default="medium")
    customer_worker.add_argument("--selected-agent-id", action="append", default=None, help="Optional business agent id to record as selected context. Repeatable.")
    customer_worker.add_argument("--worker-agent-id", default=None, help="Optional exact worker agent id. Defaults to a unique id per dispatch.")
    customer_worker.add_argument("--hermes-timeout", type=int, default=300)
    customer_worker.add_argument("--hermes-max-tokens", type=int, default=int(os.environ.get("HERMES_MAX_TOKENS", "512")))
    customer_worker.add_argument("--adapter-max-attempts", type=int, default=None, help="Maximum live adapter attempts for retryable failures.")
    customer_worker.add_argument("--adapter-retry-delay-sec", type=float, default=None, help="Delay between retryable live adapter attempts.")
    customer_worker.add_argument("--external-write-intent", action="store_true", help="Declare that the live runtime task intends to publish/upload/write to an external target; opaque runtimes will create a prepared action instead of running immediately.")
    customer_worker.add_argument("--target-resource", default=None, help="External write target resource used in the prepared action contract.")
    customer_worker.add_argument("--external-action-type", default=None, help="Prepared action type for external write governance.")
    customer_worker.add_argument("--approval-reason", default=None, help="Human-readable reason for the external-write prepared action approval.")
    customer_worker.add_argument("--async-job", action="store_true", help="Submit the customer worker task as a workflow job and return immediately.")
    customer_worker.set_defaults(handler="workflow_customer_worker_task")

    run_task = workflow_sub.add_parser("run-task", help="Create a normal MIS task and execute one local worker iteration.")
    run_task.add_argument("--adapter", choices=["mock", "hermes", "openclaw", "codex"], default="mock")
    run_task.add_argument("--confirm-run", action="store_true", help="Required for Hermes/OpenClaw/Codex live model execution.")
    run_task.add_argument("--task-id", default=None)
    run_task.add_argument("--title", required=True)
    run_task.add_argument("--description", required=True)
    run_task.add_argument("--acceptance", default="Worker must write run, tool, evaluation and audit evidence.")
    run_task.add_argument("--requester-id", default="usr_customer_demo")
    run_task.add_argument("--worker-agent-id", default=None)
    run_task.add_argument("--worker-name", default=None)
    run_task.add_argument("--priority", choices=["low", "medium", "high", "critical"], default="high")
    run_task.add_argument("--risk", choices=["low", "medium", "high", "critical"], default="medium")
    run_task.add_argument("--budget", type=float, default=3.0)
    run_task.add_argument("--use-session", action="store_true", help="Mint a short-lived session before worker execution.")
    run_task.add_argument("--session-ttl-sec", type=int, default=900)
    run_task.add_argument("--adapter-max-attempts", type=int, default=1)
    run_task.add_argument("--adapter-retry-delay-sec", type=float, default=1.0)
    run_task.add_argument("--hermes-gateway-url", default=os.environ.get("HERMES_GATEWAY_URL", "http://127.0.0.1:8642"))
    run_task.add_argument("--hermes-timeout", type=int, default=300)
    run_task.add_argument("--hermes-max-tokens", type=int, default=int(os.environ.get("HERMES_MAX_TOKENS", "512")))
    run_task.add_argument("--openclaw-bin", default=os.environ.get("OPENCLAW_BIN", "/opt/homebrew/bin/openclaw"))
    run_task.add_argument("--openclaw-timeout", type=int, default=180)
    run_task.add_argument("--codex-bin", default=os.environ.get("CODEX_BIN", ""))
    run_task.add_argument("--codex-timeout", type=int, default=300)
    run_task.set_defaults(handler="workflow_run_task")

    codex_write = workflow_sub.add_parser("codex-workspace-write", help="Prepare or resume an approval-gated Codex write in a managed detached Git worktree.")
    codex_write.add_argument("--task-id", default=None, help="Reuse the same task after Agent Plan approval. Omit only when creating a new task.")
    codex_write.add_argument("--title", default=None)
    codex_write.add_argument("--description", default=None)
    codex_write.add_argument("--acceptance", default="Approved Codex worktree diff must pass bounded path, Git, secret, evaluation, audit and manifest gates.")
    codex_write.add_argument("--requester-id", default="usr_customer_demo")
    codex_write.add_argument("--worker-agent-id", default=None)
    codex_write.add_argument("--worker-name", default=None)
    codex_write.add_argument("--priority", choices=["low", "medium", "high", "critical"], default="high")
    codex_write.add_argument("--risk", choices=["low", "medium", "high", "critical"], default="high")
    codex_write.add_argument("--budget", type=float, default=5.0)
    codex_write.add_argument("--source-repo", required=True, help="Exact clean Git root. Codex writes only in a managed detached worktree derived from this HEAD.")
    codex_write.add_argument("--allow-path", action="append", default=[], help="Approved repository-relative path prefix. Repeatable.")
    codex_write.add_argument("--prepared-action-id", default=None, help="Resume the exact approved workspace-write action after approval.")
    codex_write.add_argument("--confirm-run", action="store_true", help="Confirm creation or resumption of the governed Codex workflow.")
    codex_write.add_argument("--confirm-workspace-write", action="store_true", help="Second confirmation required when executing an approved prepared action.")
    codex_write.add_argument("--allow-high-risk", action="store_true", help="Required with --confirm-workspace-write because workspace-write has a high risk floor.")
    codex_write.add_argument("--codex-bin", default=os.environ.get("CODEX_BIN", ""))
    codex_write.add_argument("--codex-timeout", type=int, default=600)
    codex_write.add_argument("--worktree-root", default=None, help="Optional managed worktree parent for advanced operators and isolated acceptance.")
    codex_write.set_defaults(handler="workflow_codex_workspace_write")

    worker = sub.add_parser("worker", help="Worker fleet recovery commands.")
    worker_sub = worker.add_subparsers(dest="action", required=True)
    worker_status = worker_sub.add_parser("status", help="Show worker fleet, daemon, pending task and stuck-task status.")
    worker_status.set_defaults(handler="worker_status")
    worker_fleet = worker_sub.add_parser("fleet", help="Show normalized local/remote worker fleet lanes.")
    worker_fleet.set_defaults(handler="worker_fleet")
    worker_readiness = worker_sub.add_parser("readiness", help="Show read-only mock/Hermes/OpenClaw adapter readiness.")
    worker_readiness.set_defaults(handler="worker_readiness")
    worker_logs = worker_sub.add_parser("logs", help="Show local worker daemon metadata and log tail.")
    worker_logs.add_argument("--adapter", choices=["mock", "hermes", "openclaw"], default="mock")
    worker_logs.set_defaults(handler="worker_logs")
    worker_preflight = worker_sub.add_parser("preflight", help="Run read-only Gateway and adapter readiness checks.")
    worker_preflight.add_argument("--adapter", choices=["mock", "hermes", "openclaw", "codex"], default="mock")
    worker_preflight.add_argument("--agent-id", default=None)
    worker_preflight.add_argument("--timeout", type=int, default=5)
    worker_preflight.add_argument("--hermes-gateway-url", default=os.environ.get("HERMES_GATEWAY_URL", "http://127.0.0.1:8642"))
    worker_preflight.add_argument("--openclaw-bin", default=os.environ.get("OPENCLAW_BIN", "/opt/homebrew/bin/openclaw"))
    worker_preflight.add_argument("--codex-bin", default=os.environ.get("CODEX_BIN", ""))
    worker_preflight.set_defaults(handler="worker_preflight")
    worker_service_check = worker_sub.add_parser("service-check", help="Read-only check for a launchd/systemd/Windows Task Scheduler worker service file.")
    worker_service_check.add_argument("--manager", choices=["launchd", "systemd", "windows-task"], required=True)
    worker_service_check.add_argument("--agent-id", default=None)
    worker_service_check.add_argument("--adapter", choices=["mock", "hermes", "openclaw", "codex"], default="mock")
    worker_service_check.add_argument("--confirm-run", action="store_true")
    worker_service_check.add_argument("--use-session", action="store_true")
    worker_service_check.add_argument("--session-ttl-sec", type=int, default=900)
    worker_service_check.add_argument("--session-refresh-margin-sec", type=float, default=60)
    worker_service_check.add_argument("--poll-interval", type=float, default=5.0)
    worker_service_check.add_argument("--label", default="")
    worker_service_check.add_argument("--working-directory", default="")
    worker_service_check.add_argument("--runtime-dir", default="")
    worker_service_check.add_argument("--worker-command", default="")
    worker_service_check.add_argument("--hermes-gateway-url", default=os.environ.get("HERMES_GATEWAY_URL", ""))
    worker_service_check.add_argument("--codex-bin", default=os.environ.get("CODEX_BIN", ""))
    worker_service_check.add_argument("--service-path", default="")
    worker_service_check.add_argument("--api-key-placeholder", default="<paste one-time token here>")
    worker_service_check.add_argument("--credential-source", choices=["auto", "direct", "local_config"], default="auto")
    worker_service_check.add_argument("--config-path", default=str(CONFIG_PATH))
    worker_service_check.add_argument("--timeout", type=int, default=5)
    worker_service_check.set_defaults(handler="worker_service_check")
    worker_service_install = worker_sub.add_parser("service-install", help="Dry-run or write a safe launchd/systemd/Windows Task Scheduler worker service file.")
    worker_service_install.add_argument("--manager", choices=["launchd", "systemd", "windows-task"], required=True)
    worker_service_install.add_argument("--agent-id", default=None)
    worker_service_install.add_argument("--adapter", choices=["mock", "hermes", "openclaw", "codex"], default="mock")
    worker_service_install.add_argument("--confirm-run", action="store_true")
    worker_service_install.add_argument("--use-session", action="store_true", help="Render a session-minting worker command for remote/scoped tokens. Local loopback services omit this by default.")
    worker_service_install.add_argument("--session-ttl-sec", type=int, default=900)
    worker_service_install.add_argument("--session-refresh-margin-sec", type=float, default=60)
    worker_service_install.add_argument("--poll-interval", type=float, default=5.0)
    worker_service_install.add_argument("--label", default="")
    worker_service_install.add_argument("--working-directory", default="", help="Worker project directory. Installed Private Host defaults to its managed current package link.")
    worker_service_install.add_argument("--runtime-dir", default="")
    worker_service_install.add_argument("--log-path", default="")
    worker_service_install.add_argument("--api-key-placeholder", default="<paste one-time token here>")
    worker_service_install.add_argument("--credential-source", choices=["direct", "local_config"], default="direct")
    worker_service_install.add_argument("--config-path", default=str(CONFIG_PATH))
    worker_service_install.add_argument("--worker-command", default="", help="Worker executable command for service templates. Defaults to installed agentops-worker or python -m fallback.")
    worker_service_install.add_argument("--hermes-gateway-url", default=os.environ.get("HERMES_GATEWAY_URL", ""), help="Persist an explicit credential-free Hermes HTTP(S) base URL for a Hermes service.")
    worker_service_install.add_argument("--codex-bin", default=os.environ.get("CODEX_BIN", ""), help="Persist the exact local Codex executable or Windows command shim for a Codex service.")
    worker_service_install.add_argument("--service-path", default="")
    worker_service_install.add_argument("--confirm-install", action="store_true", help="Write the service file. Default is dry-run.")
    worker_service_install.add_argument("--overwrite", action="store_true")
    worker_service_install.add_argument("--timeout", type=int, default=5)
    worker_service_install.set_defaults(handler="worker_service_install")
    worker_service_control = worker_sub.add_parser("service-control", help="Preview or explicitly run OS service load, unload, or restart for a worker service.")
    worker_service_control.add_argument("--manager", choices=["launchd", "systemd", "windows-task"], required=True)
    worker_service_control.add_argument("--action", dest="service_action", choices=["load", "unload", "restart"], required=True)
    worker_service_control.add_argument("--agent-id", default=None)
    worker_service_control.add_argument("--adapter", choices=["mock", "hermes", "openclaw", "codex"], default="mock")
    worker_service_control.add_argument("--confirm-run", action="store_true")
    worker_service_control.add_argument("--use-session", action="store_true")
    worker_service_control.add_argument("--session-ttl-sec", type=int, default=900)
    worker_service_control.add_argument("--session-refresh-margin-sec", type=float, default=60)
    worker_service_control.add_argument("--poll-interval", type=float, default=5.0)
    worker_service_control.add_argument("--label", default="")
    worker_service_control.add_argument("--working-directory", default="")
    worker_service_control.add_argument("--runtime-dir", default="")
    worker_service_control.add_argument("--worker-command", default="")
    worker_service_control.add_argument("--hermes-gateway-url", default=os.environ.get("HERMES_GATEWAY_URL", ""))
    worker_service_control.add_argument("--codex-bin", default=os.environ.get("CODEX_BIN", ""))
    worker_service_control.add_argument("--service-path", default="")
    worker_service_control.add_argument("--api-key-placeholder", default="<paste one-time token here>")
    worker_service_control.add_argument("--credential-source", choices=["auto", "direct", "local_config"], default="auto")
    worker_service_control.add_argument("--config-path", default=str(CONFIG_PATH))
    worker_service_control.add_argument("--timeout", type=int, default=10)
    worker_service_control.add_argument("--confirm-control", action="store_true", help="Actually call the selected OS service manager. Default is preview only.")
    worker_service_control.set_defaults(handler="worker_service_control")
    worker_start = worker_sub.add_parser("start", help="Start a local worker daemon through the MIS supervisor.")
    worker_start.add_argument("--adapter", choices=["mock", "hermes", "openclaw"], default="mock")
    worker_start.add_argument("--agent-id", default=None)
    worker_start.add_argument("--poll-interval", type=float, default=5.0)
    worker_start.add_argument("--max-tasks", type=int, default=0)
    worker_start.add_argument("--max-errors", type=int, default=5)
    worker_start.add_argument("--status", action="append", default=None)
    worker_start.add_argument("--confirm-run", action="store_true", help="Required for Hermes/OpenClaw live daemons.")
    worker_start.add_argument("--openclaw-timeout", type=int, default=None)
    worker_start.set_defaults(handler="worker_start")
    worker_stop = worker_sub.add_parser("stop", help="Stop one local worker daemon or all daemons.")
    worker_stop.add_argument("--adapter", choices=["mock", "hermes", "openclaw", "all"], default="all")
    worker_stop.set_defaults(handler="worker_stop")
    worker_restart = worker_sub.add_parser("restart", help="Restart one local worker daemon through the MIS supervisor.")
    worker_restart.add_argument("--adapter", choices=["mock", "hermes", "openclaw"], default="mock")
    worker_restart.add_argument("--agent-id", default=None)
    worker_restart.add_argument("--poll-interval", type=float, default=None)
    worker_restart.add_argument("--max-tasks", type=int, default=None)
    worker_restart.add_argument("--max-errors", type=int, default=None)
    worker_restart.add_argument("--status", action="append", default=None)
    worker_restart.add_argument("--confirm-run", action="store_true", help="Required before restarting Hermes/OpenClaw live daemons.")
    worker_restart.add_argument("--openclaw-timeout", type=int, default=None)
    worker_restart.set_defaults(handler="worker_restart")
    worker_stuck = worker_sub.add_parser("stuck", help="List running worker tasks that exceeded a threshold.")
    worker_stuck.add_argument("--threshold-sec", type=int, default=900)
    worker_stuck.add_argument("--limit", type=int, default=25)
    worker_stuck.set_defaults(handler="worker_stuck")
    worker_release = worker_sub.add_parser("release", help="Release a running worker task back to planned.")
    worker_release.add_argument("--task-id", required=True)
    worker_release.add_argument("--reason", default="operator_release")
    worker_release.add_argument("--force", action="store_true")
    worker_release.set_defaults(handler="worker_release")
    worker_hygiene = worker_sub.add_parser("hygiene", help="Plan or apply fleet cleanup for stuck tasks and never-seen enrollments.")
    worker_hygiene.add_argument("--threshold-sec", type=int, default=900)
    worker_hygiene.add_argument("--enrollment-age-sec", type=int, default=900)
    worker_hygiene.add_argument("--limit", type=int, default=25)
    worker_hygiene.add_argument("--reason", default="fleet_hygiene_cleanup")
    worker_hygiene.add_argument("--apply", action="store_true", help="Apply cleanup actions. Default is read-only.")
    worker_hygiene.add_argument("--confirm-cleanup", action="store_true", help="Required with --apply.")
    worker_hygiene.set_defaults(handler="worker_hygiene")

    enrollment = sub.add_parser("enrollment", help="Remote/local agent enrollment token commands.")
    enrollment_sub = enrollment.add_subparsers(dest="action", required=True)
    enroll_policy = enrollment_sub.add_parser("policy-preview", help="Preview enrollment scope risk without issuing a token.")
    enroll_policy.add_argument("--runtime", default="mock")
    enroll_policy.add_argument("--workspace-id", default=None)
    enroll_policy.add_argument("--scopes", default="agents:heartbeat,tasks:read,audit:write")
    enroll_policy.set_defaults(handler="enrollment_policy_preview")

    enroll_create = enrollment_sub.add_parser("create", help="Create a scoped one-time-visible agent token.")
    enroll_create.add_argument("--agent-id", required=True)
    enroll_create.add_argument("--name", default="Remote Agent")
    enroll_create.add_argument("--role", default="Remote AI Digital Employee")
    enroll_create.add_argument("--runtime", default="mock")
    enroll_create.add_argument("--scopes", default="agents:write,agents:heartbeat,knowledge:read,knowledge:write,agent_plans:read,agent_plans:write,plan_evidence:read,plan_evidence:write,tasks:create,tasks:read,tasks:claim,runs:write,runtime_events:write,toolcalls:write,artifacts:write,approvals:request,memories:propose,evaluations:submit,audit:write")
    enroll_create.add_argument("--ttl-days", type=int, default=30)
    enroll_create.add_argument("--heartbeat-timeout-sec", type=int, default=300)
    enroll_create.add_argument("--label", default="")
    enroll_create.add_argument("--save-token", action="store_true", help="Save returned token to local config for this CLI.")
    enroll_create.set_defaults(handler="enrollment_create")

    enroll_request = enrollment_sub.add_parser("request", help="Request human approval before issuing an enrollment token.")
    enroll_request.add_argument("--agent-id", required=True)
    enroll_request.add_argument("--name", default="Remote Agent")
    enroll_request.add_argument("--role", default="Remote AI Digital Employee")
    enroll_request.add_argument("--runtime", default="mock")
    enroll_request.add_argument("--scopes", default="agents:heartbeat,knowledge:read,knowledge:write,agent_plans:read,agent_plans:write,plan_evidence:read,plan_evidence:write,tasks:create,tasks:read,tasks:claim,runs:write,runtime_events:write,toolcalls:write,artifacts:write,memories:propose,evaluations:submit,audit:write")
    enroll_request.add_argument("--reason", default="Remote worker needs scoped access to process assigned MIS tasks.")
    enroll_request.set_defaults(handler="enrollment_request")

    enroll_issue = enrollment_sub.add_parser("issue-approved", help="Issue a token for an approved enrollment request.")
    enroll_issue.add_argument("--request-id", default=None)
    enroll_issue.add_argument("--approval-id", default=None)
    enroll_issue.add_argument("--ttl-days", type=int, default=30)
    enroll_issue.add_argument("--heartbeat-timeout-sec", type=int, default=300)
    enroll_issue.add_argument("--label", default="")
    enroll_issue.add_argument("--save-token", action="store_true", help="Save returned token to local config for this CLI.")
    enroll_issue.set_defaults(handler="enrollment_issue_approved")

    enroll_list = enrollment_sub.add_parser("list", help="List token metadata without secrets.")
    enroll_list.set_defaults(handler="enrollment_list")

    enroll_revoke = enrollment_sub.add_parser("revoke", help="Revoke a token by token id or all active tokens for an agent.")
    enroll_revoke.add_argument("--token-id", default=None)
    enroll_revoke.add_argument("--agent-id", default=None)
    enroll_revoke.set_defaults(handler="enrollment_revoke")

    enroll_rotate = enrollment_sub.add_parser("rotate", help="Rotate an active enrollment token and show the new token once.")
    enroll_rotate.add_argument("--token-id", default=None)
    enroll_rotate.add_argument("--agent-id", default=None)
    enroll_rotate.add_argument("--scopes", default=None, help="Optional replacement scope list. Defaults to old token scopes.")
    enroll_rotate.add_argument("--ttl-days", type=int, default=30)
    enroll_rotate.add_argument("--heartbeat-timeout-sec", type=int, default=None)
    enroll_rotate.add_argument("--label", default="")
    enroll_rotate.add_argument("--save-token", action="store_true", help="Save returned token to local config for this CLI.")
    enroll_rotate.set_defaults(handler="enrollment_rotate")

    session = sub.add_parser("session", help="Short-lived Agent Gateway session commands.")
    session_sub = session.add_subparsers(dest="action", required=True)
    session_create = session_sub.add_parser("create", help="Mint a short-lived session from an enrollment token.")
    session_create.add_argument("--ttl-sec", type=int, default=900)
    session_create.add_argument("--scopes", default=None, help="Optional scope subset for this session.")
    session_create.add_argument("--save-session", action="store_true", help="Save returned session token to local config for this CLI.")
    session_create.set_defaults(handler="session_create")
    session_list = session_sub.add_parser("list", help="List short-lived session metadata without secrets.")
    session_list.set_defaults(handler="session_list")
    session_revoke = session_sub.add_parser("revoke", help="Revoke a session by id or all active sessions for an agent.")
    session_revoke.add_argument("--session-id", default=None)
    session_revoke.add_argument("--agent-id", default=None)
    session_revoke.set_defaults(handler="session_revoke")

    return parser


HANDLERS = {
    "login": lambda args, client: cmd_login(args),
    "status": cmd_status,
    "doctor": cmd_doctor,
    "local_readiness": cmd_local_readiness,
    "demo_readiness": cmd_demo_readiness,
    "command_center_overview": cmd_command_center_overview,
    "commercial_config_status": cmd_commercial_config_status,
    "operator_action_plan": cmd_operator_action_plan,
    "operator_action_receipts": cmd_operator_action_receipts,
    "operator_record_action_receipt": cmd_operator_record_action_receipt,
    "operator_record_control_readback": cmd_operator_record_control_readback,
    "operator_receipt_failure_memories": cmd_operator_receipt_failure_memories,
    "operator_propose_receipt_failure_memory": cmd_operator_propose_receipt_failure_memory,
    "operator_loop_audit": cmd_operator_loop_audit,
    "operator_loop_control": cmd_operator_loop_control,
    "operator_evidence_report": cmd_operator_evidence_report,
    "operator_handoff": cmd_operator_handoff,
    "operator_loop_self_check": cmd_operator_loop_self_check,
    "operator_advance_loop": cmd_operator_advance_loop,
    "operator_advance_loop_policy": cmd_operator_advance_loop_policy,
    "operator_health": cmd_operator_health,
    "operator_runtime_doctor": cmd_operator_runtime_doctor,
    "operator_live_acceptance": cmd_operator_live_acceptance,
    "operator_local_harness_proof": cmd_operator_local_harness_proof,
    "operator_live_product_readiness": cmd_operator_live_product_readiness,
    "operator_execution_mode": cmd_operator_execution_mode,
    "operator_command_center": cmd_operator_command_center,
    "operator_intake_checklist": cmd_operator_intake_checklist,
    "operator_loop_launch_packet": cmd_operator_loop_launch_packet,
    "operator_start_check": cmd_operator_start_check,
    "operator_agent_loop_handoff": cmd_operator_agent_loop_handoff,
    "operator_loop_supervision": cmd_operator_loop_supervision,
    "operator_loop_bootstrap": cmd_operator_loop_bootstrap,
    "operator_service_closure": cmd_operator_service_closure,
    "operator_loop_driver": cmd_operator_loop_driver,
    "operator_remediate_evidence_gap": cmd_operator_remediate_evidence_gap,
    "operator_close_evidence_gap": cmd_operator_close_evidence_gap,
    "commander_board": cmd_commander_board,
    "commander_repo_map": cmd_commander_repo_map,
    "commander_coding_template": cmd_commander_coding_template,
    "commander_inbox": cmd_commander_inbox,
    "commander_plan": cmd_commander_plan,
    "commander_packages": cmd_commander_packages,
    "commander_lane_packets": cmd_commander_lane_packets,
    "commander_dispatch_package": cmd_commander_dispatch_package,
    "commander_coding_workspace": cmd_commander_coding_workspace,
    "commander_coding_workspace_cleanup": cmd_commander_coding_workspace_cleanup,
    "commander_coding_evidence": cmd_commander_coding_evidence,
    "commander_dispatch_batch": cmd_commander_dispatch_batch,
    "commander_synthesize": cmd_commander_synthesize,
    "commander_promote_synthesis": cmd_commander_promote_synthesis,
    "review_queue": cmd_review_queue,
    "security_production_readiness": cmd_security_production_readiness,
    "agent_register": cmd_agent_register,
    "agent_heartbeat": cmd_agent_heartbeat,
    "task_create": cmd_task_create,
    "task_list": cmd_task_list,
    "task_get": cmd_task_get,
    "task_pull": cmd_task_pull,
    "task_claim": cmd_task_claim,
    "run_list": cmd_run_list,
    "run_get": cmd_run_get,
    "run_graph": cmd_run_graph,
    "run_evidence_graph": cmd_run_evidence_graph,
    "run_start": cmd_run_start,
    "run_heartbeat": cmd_run_heartbeat,
    "runtime_connectors": cmd_runtime_connectors,
    "runtime_event_record": cmd_runtime_event_record,
    "toolcall_record": cmd_toolcall_record,
    "artifact_list": cmd_artifact_list,
    "artifact_record": cmd_artifact_record,
    "experiment": cmd_experiment,
    "knowledge_search": cmd_knowledge_search,
    "knowledge_index": cmd_knowledge_index,
    "knowledge_evidence_packet": cmd_knowledge_evidence_packet,
    "knowledge_context_packet": cmd_knowledge_context_packet,
    "agent_plan_create": cmd_agent_plan_create,
    "agent_plan_list": cmd_agent_plan_list,
    "agent_plan_get": cmd_agent_plan_get,
    "agent_plan_verify": cmd_agent_plan_verify,
    "agent_plan_approve": cmd_agent_plan_approve,
    "agent_plan_reject": cmd_agent_plan_reject,
    "plan_evidence_create": cmd_plan_evidence_create,
    "plan_evidence_list": cmd_plan_evidence_list,
    "plan_evidence_get": cmd_plan_evidence_get,
    "plan_evidence_verify": cmd_plan_evidence_verify,
    "approval_list": cmd_approval_list,
    "approval_inspect": cmd_approval_inspect,
    "approval_prepared_action_create": cmd_approval_prepared_action_create,
    "approval_prepared_action_get": cmd_approval_prepared_action_get,
    "approval_prepared_action_resume": cmd_approval_prepared_action_resume,
    "approval_approve": cmd_approval_decide,
    "approval_reject": cmd_approval_decide,
    "approval_request": cmd_approval_request,
    "memory_list": cmd_memory_list,
    "memory_approve": cmd_memory_decide,
    "memory_reject": cmd_memory_decide,
    "memory_propose": cmd_memory_propose,
    "eval_submit": cmd_eval_submit,
    "eval_cases": cmd_eval_cases,
    "eval_case_runs": cmd_eval_case_runs,
    "eval_review_case_run": cmd_eval_review_case_run,
    "eval_remediate_case_run": cmd_eval_remediate_case_run,
    "eval_propose_case": cmd_eval_propose_case,
    "eval_approve_case": cmd_eval_review_case,
    "eval_reject_case": cmd_eval_review_case,
    "eval_run_cases": cmd_eval_run_cases,
    "audit_emit": cmd_audit_emit,
    "workflow_templates": cmd_workflow_templates,
    "workflow_delivery_board": cmd_workflow_delivery_board,
    "workflow_hermes_openclaw_loop": cmd_workflow_hermes_openclaw_loop,
    "workflow_run_template": cmd_workflow_run_template,
    "workflow_jobs": cmd_workflow_jobs,
    "workflow_job_status": cmd_workflow_job_status,
    "workflow_stuck_jobs": cmd_workflow_stuck_jobs,
    "workflow_job_mark_failed": cmd_workflow_job_mark_failed,
    "workflow_recover_job": cmd_workflow_recover_job,
    "workflow_customer_worker_task": cmd_workflow_customer_worker_task,
    "workflow_run_task": cmd_workflow_run_task,
    "workflow_codex_workspace_write": cmd_workflow_codex_workspace_write,
    "worker_status": cmd_worker_status,
    "worker_fleet": cmd_worker_fleet,
    "worker_readiness": cmd_worker_readiness,
    "worker_logs": cmd_worker_logs,
    "worker_preflight": cmd_worker_preflight,
    "worker_service_check": cmd_worker_service_check,
    "worker_service_install": cmd_worker_service_install,
    "worker_service_control": cmd_worker_service_control,
    "worker_start": cmd_worker_start,
    "worker_stop": cmd_worker_stop,
    "worker_restart": cmd_worker_restart,
    "worker_stuck": cmd_worker_stuck,
    "worker_release": cmd_worker_release,
    "worker_hygiene": cmd_worker_hygiene,
    "enrollment_policy_preview": cmd_enrollment_policy_preview,
    "enrollment_create": cmd_enrollment_create,
    "enrollment_request": cmd_enrollment_request,
    "enrollment_issue_approved": cmd_enrollment_issue_approved,
    "enrollment_list": cmd_enrollment_list,
    "enrollment_revoke": cmd_enrollment_revoke,
    "enrollment_rotate": cmd_enrollment_rotate,
    "session_create": cmd_session_create,
    "session_list": cmd_session_list,
    "session_revoke": cmd_session_revoke,
}


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(normalize_reorderable_global_options(argv if argv is not None else sys.argv[1:]))
    context = resolved_context(args)
    client = AgentOpsClient(context)
    try:
        result = HANDLERS[args.handler](args, client)
    except RuntimeError as exc:
        eprint(redact_text(str(exc), 1200))
        return 1
    exit_code = int(result.pop("_exit_code", 0) or 0) if isinstance(result, dict) else 0
    emit(result)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
