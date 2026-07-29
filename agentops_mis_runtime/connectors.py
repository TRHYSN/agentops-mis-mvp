"""Runtime connector registry helpers.

This is the first P1-05 strangler boundary for the oversized local server:
runtime connector declarations and persistence helpers live here, while
server routes, trust decisions, runtime events, and audit writes stay in
``server.py``.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import shlex
import shutil
from pathlib import Path

from agentops_mis_runtime.capabilities import runtime_connector_capability_manifest


ROOT = Path(__file__).resolve().parents[1]
OPENCLAW_BIN = Path("/opt/homebrew/bin/openclaw")
CODEX_APP_BIN = Path("/Applications/ChatGPT.app/Contents/Resources/codex")


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def hermes_runtime_config() -> dict:
    return {
        "gateway_url": os.environ.get("HERMES_GATEWAY_URL", "http://127.0.0.1:8642").strip(),
        "profile": os.environ.get("HERMES_PROFILE", "default").strip() or "default",
        "runtime_mode": os.environ.get("HERMES_RUNTIME_MODE", "health_only").strip() or "health_only",
        "allow_real_run": os.environ.get("HERMES_ALLOW_REAL_RUN", "").strip().lower() in ("1", "true", "yes"),
        "require_confirm_run": os.environ.get("HERMES_REQUIRE_CONFIRM_RUN", "true").strip().lower() not in ("0", "false", "no"),
    }


def agnesfallback_config() -> dict:
    extra_args = shlex.split(os.environ.get("AGNESFALLBACK_CLI_EXTRA_ARGS", "").strip())
    return {
        "binary_path": os.path.expanduser(os.environ.get("AGNESFALLBACK_BIN", "~/.local/bin/agnesfallback").strip()),
        "gateway_url": os.environ.get("AGNESFALLBACK_GATEWAY_URL", "http://127.0.0.1:8643").strip(),
        "profile": os.environ.get("AGNESFALLBACK_PROFILE", "agnesfallback").strip() or "agnesfallback",
        "extra_args": extra_args,
    }


def agnesfallback_cli_command(agnes: dict, prompt: str) -> list[str]:
    return [agnes["binary_path"], "-z", prompt, *agnes.get("extra_args", [])]


def codex_binary_available() -> bool:
    candidates = [
        os.environ.get("CODEX_BIN", "").strip(),
        str(CODEX_APP_BIN),
        shutil.which("codex") or "",
    ]
    return any(
        candidate and Path(candidate).expanduser().is_file() and os.access(Path(candidate).expanduser(), os.X_OK)
        for candidate in candidates
    )


def runtime_connector_rows() -> list[dict]:
    now = now_iso()
    hermes = hermes_runtime_config()
    agnes = agnesfallback_config()
    codex_available = codex_binary_available()
    rows = [
        {
            "runtime_connector_id": "rtc_agent_gateway_local",
            "provider": "agent-gateway",
            "connector_type": "local_cli_api_mcp",
            "profile_name": "local-demo",
            "base_url": "http://127.0.0.1:8787/api/agent-gateway",
            "binary_path": None,
            "status": "available",
            "allow_real_run": 1,
            "require_confirm_run": 0,
            "trust_status": "trusted",
            "trust_note": None,
            "trust_updated_at": now,
            "last_health_at": now,
            "last_error": None,
            "created_at": now,
            "updated_at": now,
        },
        {
            "runtime_connector_id": "rtc_openclaw_local",
            "provider": "openclaw",
            "connector_type": "local_cli",
            "profile_name": "main",
            "base_url": None,
            "binary_path": str(OPENCLAW_BIN),
            "status": "available" if OPENCLAW_BIN.exists() else "unavailable",
            "allow_real_run": 1,
            "require_confirm_run": 1,
            "trust_status": "trusted",
            "trust_note": None,
            "trust_updated_at": now,
            "last_health_at": now,
            "last_error": None if OPENCLAW_BIN.exists() else f"missing {OPENCLAW_BIN}",
            "created_at": now,
            "updated_at": now,
        },
        {
            "runtime_connector_id": "rtc_codex_local",
            "provider": "codex",
            "connector_type": "governed_cli_worker",
            "profile_name": "read-only-default",
            "base_url": None,
            "binary_path": None,
            "status": "available" if codex_available else "unavailable",
            "allow_real_run": 1,
            "require_confirm_run": 1,
            "trust_status": "trusted",
            "trust_note": None,
            "trust_updated_at": now,
            "last_health_at": now,
            "last_error": None if codex_available else "Codex CLI is unavailable.",
            "created_at": now,
            "updated_at": now,
        },
        {
            "runtime_connector_id": "rtc_hermes_default_gateway",
            "provider": "hermes",
            "connector_type": "health_probe",
            "profile_name": hermes["profile"],
            "base_url": hermes["gateway_url"],
            "binary_path": None,
            "status": "unknown",
            "allow_real_run": 1 if hermes["allow_real_run"] else 0,
            "require_confirm_run": 1 if hermes["require_confirm_run"] else 0,
            "trust_status": "trusted",
            "trust_note": None,
            "trust_updated_at": now,
            "last_health_at": None,
            "last_error": None,
            "created_at": now,
            "updated_at": now,
        },
        {
            "runtime_connector_id": "rtc_agnesfallback_cli",
            "provider": "agnesfallback",
            "connector_type": "cli_probe",
            "profile_name": agnes["profile"],
            "base_url": None,
            "binary_path": agnes["binary_path"],
            "status": "available" if Path(agnes["binary_path"]).exists() else "unavailable",
            "allow_real_run": 1 if hermes["allow_real_run"] else 0,
            "require_confirm_run": 1 if hermes["require_confirm_run"] else 0,
            "trust_status": "trusted",
            "trust_note": None,
            "trust_updated_at": now,
            "last_health_at": None,
            "last_error": None if Path(agnes["binary_path"]).exists() else "AGNESFALLBACK_BIN not found.",
            "created_at": now,
            "updated_at": now,
        },
        {
            "runtime_connector_id": "rtc_agnesfallback_openai_api",
            "provider": "agnesfallback",
            "connector_type": "openai_compatible",
            "profile_name": agnes["profile"],
            "base_url": agnes["gateway_url"],
            "binary_path": None,
            "status": "unknown",
            "allow_real_run": 1 if hermes["allow_real_run"] else 0,
            "require_confirm_run": 1 if hermes["require_confirm_run"] else 0,
            "trust_status": "trusted",
            "trust_note": None,
            "trust_updated_at": now,
            "last_health_at": None,
            "last_error": None,
            "created_at": now,
            "updated_at": now,
        },
    ]
    for row in rows:
        manifest = runtime_connector_capability_manifest(
            row["runtime_connector_id"],
            row["provider"],
            row["connector_type"],
            repo_root=ROOT,
        )
        row["observation_level"] = manifest["observation_level"]
        row["capability_manifest_json"] = json.dumps(manifest, ensure_ascii=False, sort_keys=True)
        row["capability_policy_hash"] = manifest["manifest_hash"]
    return rows


def runtime_connector_refresh_rows(status: dict | None = None, *, now: str | None = None) -> list[dict]:
    """Return connector rows updated with a server-supplied health snapshot.

    The server still owns collecting Hermes/Agnesfallback health and persisting
    rows. This helper only applies the deterministic row-status projection.
    """
    rows = runtime_connector_rows()
    if not status:
        return rows
    health_at = now or now_iso()
    default_gateway = status.get("default_gateway") or {}
    agnesfallback = status.get("agnesfallback") or {}
    for row in rows:
        connector_id = row.get("runtime_connector_id")
        if connector_id == "rtc_hermes_default_gateway":
            listening = bool(default_gateway.get("api_server_listening"))
            row["status"] = "available" if listening else "unavailable"
            row["last_health_at"] = health_at
            row["last_error"] = default_gateway.get("last_error")
        elif connector_id == "rtc_agnesfallback_cli":
            binary_exists = bool(agnesfallback.get("binary_exists"))
            row["status"] = "available" if binary_exists else "unavailable"
            row["last_health_at"] = health_at
            row["last_error"] = None if binary_exists else "AGNESFALLBACK_BIN not found."
        elif connector_id == "rtc_agnesfallback_openai_api":
            api_listening = bool(agnesfallback.get("api_server_listening"))
            row["status"] = "available" if api_listening else "unavailable"
            row["last_health_at"] = health_at
            row["last_error"] = None if api_listening else "Agnesfallback OpenAI-compatible API is not listening."
    return rows


def upsert_runtime_connector(conn, row: dict) -> None:
    before = conn.execute("SELECT * FROM runtime_connectors WHERE runtime_connector_id=?", (row["runtime_connector_id"],)).fetchone()
    if before:
        conn.execute(
            """UPDATE runtime_connectors SET provider=:provider, connector_type=:connector_type, profile_name=:profile_name,
            base_url=:base_url, binary_path=:binary_path, status=:status, allow_real_run=:allow_real_run,
            require_confirm_run=:require_confirm_run, observation_level=:observation_level,
            capability_manifest_json=:capability_manifest_json, capability_policy_hash=:capability_policy_hash,
            last_health_at=:last_health_at, last_error=:last_error,
            updated_at=:updated_at WHERE runtime_connector_id=:runtime_connector_id""",
            row,
        )
    else:
        conn.execute(
            """INSERT INTO runtime_connectors(runtime_connector_id,provider,connector_type,profile_name,base_url,binary_path,status,allow_real_run,require_confirm_run,observation_level,capability_manifest_json,capability_policy_hash,last_health_at,last_error,created_at,updated_at)
            VALUES(:runtime_connector_id,:provider,:connector_type,:profile_name,:base_url,:binary_path,:status,:allow_real_run,:require_confirm_run,:observation_level,:capability_manifest_json,:capability_policy_hash,:last_health_at,:last_error,:created_at,:updated_at)""",
            row,
        )
