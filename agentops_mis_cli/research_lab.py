"""Thin, secret-safe delegation from ``agentops experiment`` to Research Lab."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from .redaction import redact_full_text, redact_text


RESEARCH_LAB_BIN_ENV = "AGENTOPS_RESEARCH_LAB_BIN"
SENSITIVE_ENV_MARKERS = (
    "API_KEY",
    "AUTHORIZATION",
    "CREDENTIAL",
    "PASSWORD",
    "PRIVATE_KEY",
    "SECRET",
    "SESSION_TOKEN",
    "TOKEN",
)


def _repo_research_lab_root() -> Path:
    return Path(__file__).resolve().parents[1] / "incubator" / "research-lab"


def _safe_child_env(extra_pythonpath: str | None = None) -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if not any(marker in key.upper() for marker in SENSITIVE_ENV_MARKERS)
    }
    env.pop(RESEARCH_LAB_BIN_ENV, None)
    env.pop("AGENTOPS_CONFIG", None)
    if extra_pythonpath:
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = extra_pythonpath + (os.pathsep + existing if existing else "")
    return env


def _resolve_command() -> tuple[list[str], dict[str, str], str]:
    explicit = os.environ.get(RESEARCH_LAB_BIN_ENV, "").strip()
    if explicit:
        executable = Path(explicit).expanduser()
        if not executable.is_file() or not os.access(executable, os.X_OK):
            raise RuntimeError(
                f"Research Lab executable configured by {RESEARCH_LAB_BIN_ENV} "
                "is missing or not executable. Install agentops-research-lab or fix that path."
            )
        return [str(executable)], _safe_child_env(), "configured_executable"

    installed = shutil.which("research-lab")
    if installed:
        return [installed], _safe_child_env(), "installed_executable"

    repo_root = _repo_research_lab_root()
    module_entry = repo_root / "research_lab" / "__main__.py"
    if module_entry.is_file():
        return (
            [sys.executable, "-m", "research_lab"],
            _safe_child_env(str(repo_root)),
            "repository_module",
        )

    raise RuntimeError(
        "Research Lab is not installed. Install it with "
        "`python3 -m pip install -e incubator/research-lab` or set "
        f"{RESEARCH_LAB_BIN_ENV} to its executable."
    )


def _redact_known_secret(value: str, secret: str) -> str:
    redacted = redact_full_text(value)
    return redacted.replace(secret, "[SECRET_REDACTED]") if secret else redacted


def _safe_payload(value: Any, *, secret: str = "") -> Any:
    if isinstance(value, dict):
        return {str(key): _safe_payload(item, secret=secret) for key, item in value.items()}
    if isinstance(value, list):
        return [_safe_payload(item, secret=secret) for item in value]
    if isinstance(value, str):
        return _redact_known_secret(value, secret)
    return value


def delegate_research_lab(
    arguments: list[str],
    *,
    action: str,
    api_key: str = "",
) -> dict[str, Any]:
    command, env, source = _resolve_command()
    delegated_api_key = api_key if action == "sync" else ""
    if delegated_api_key:
        env["AGENTOPS_API_KEY"] = delegated_api_key
    try:
        completed = subprocess.run(
            [*command, *arguments],
            cwd=Path.cwd(),
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise RuntimeError(f"Research Lab could not start: {exc}") from exc

    stdout = _redact_known_secret(completed.stdout, delegated_api_key)
    stderr = _redact_known_secret(completed.stderr, delegated_api_key)
    try:
        parsed = json.loads(stdout)
    except json.JSONDecodeError:
        parsed = {
            "ok": False,
            "error": "research_lab_invalid_output",
            "message": redact_text(stderr or stdout or "Research Lab returned no JSON output.", 1200),
        }
    if not isinstance(parsed, dict):
        parsed = {
            "ok": False,
            "error": "research_lab_invalid_output",
            "message": "Research Lab output must be a JSON object.",
        }

    payload = _safe_payload(parsed, secret=delegated_api_key)
    payload["delegated_by"] = "agentops experiment"
    payload["delegate_action"] = action
    payload["delegate_source"] = source
    payload["token_omitted"] = True
    if completed.returncode != 0:
        payload["ok"] = False
        payload["_exit_code"] = completed.returncode
        if "invalid choice" in stderr and "run-local" in " ".join(arguments):
            payload["error"] = "research_lab_capability_unavailable"
            payload["message"] = (
                "Installed Research Lab does not provide local experiment execution. "
                "Upgrade agentops-research-lab to a version with `run-local`."
            )
        elif "invalid choice" in stderr and arguments:
            payload["error"] = "research_lab_capability_unavailable"
            payload["message"] = (
                f"Installed Research Lab does not provide `{arguments[0]}`. "
                "Upgrade agentops-research-lab."
            )
        elif stderr and not payload.get("message"):
            payload["message"] = redact_text(stderr, 1200)
    return payload


def command_arguments(args: Any) -> list[str]:
    action = str(args.experiment_action)
    if action == "validate":
        arguments = ["validate-spec", "--spec", args.spec]
        if args.servers:
            arguments.extend(["--servers", args.servers])
        return arguments
    if action == "run":
        arguments = ["run-local", "--spec", args.spec, "--state-dir", args.state_dir]
        if args.confirm_run:
            arguments.append("--confirm-run")
        return arguments
    if action == "show":
        return ["show", "--experiment-id", args.experiment_id, "--state-dir", args.state_dir]
    if action == "sync":
        arguments = [
            "sync-mis",
            "--experiment-id",
            args.experiment_id,
            "--state-dir",
            args.state_dir,
            "--workspace-id",
            args.delegate_workspace_id,
            "--base-url",
            args.delegate_base_url,
        ]
        if args.confirm_sync:
            arguments.append("--confirm-sync")
        return arguments
    raise RuntimeError(f"Unsupported experiment action: {action}")


def run_experiment_command(
    args: Any,
    *,
    base_url: str,
    workspace_id: str,
    api_key: str = "",
) -> dict[str, Any]:
    args.delegate_base_url = base_url
    args.delegate_workspace_id = workspace_id
    return delegate_research_lab(
        command_arguments(args),
        action=str(args.experiment_action),
        api_key=api_key,
    )
