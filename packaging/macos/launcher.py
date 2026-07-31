#!/usr/bin/env python3
"""Open the installed Private Host Workspace without exposing Host credentials."""
from __future__ import annotations

import json
import fcntl
import os
import stat
import subprocess
import sys
from pathlib import Path


CONFIG_PATH = Path(__file__).with_name("launcher-config.json")
PRODUCT = "AgentOps MIS Private Host Launcher"
OMITTED_ENV_KEYS = {
    "AGENTOPS_ADMIN_KEY",
    "AGENTOPS_AGENT_ID",
    "AGENTOPS_API_KEY",
    "AGENTOPS_CONFIG",
    "AGENTOPS_ENROLLMENT_TOKEN",
    "AGENTOPS_SESSION_TOKEN",
    "AGENTOPS_WORKSPACE_ID",
}


def show_error(message: str) -> None:
    osascript = Path("/usr/bin/osascript")
    if sys.platform != "darwin" or not osascript.is_file():
        return
    try:
        subprocess.run(
            [
                str(osascript),
                "-e",
                "on run argv",
                "-e",
                'display alert "AgentOps MIS" message (item 1 of argv) as critical buttons {"OK"}',
                "-e",
                "end run",
                message,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


def fail(message: str) -> int:
    show_error(message)
    return 1


def load_config() -> dict:
    if CONFIG_PATH.is_symlink():
        raise ValueError("unsafe launcher config")
    payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if (
        payload.get("schema_version") != 1
        or payload.get("product") != PRODUCT
        or not isinstance(payload.get("default_port"), int)
    ):
        raise ValueError("invalid launcher config")
    for key in ("agentops_path", "bin_dir", "current_path", "host_home", "install_root", "python_path"):
        value = Path(str(payload.get(key) or "")).expanduser()
        if not value.is_absolute():
            raise ValueError("launcher path is not absolute")
    return payload


def run_cli(python: Path, current: Path, arguments: list[str], config: dict) -> subprocess.CompletedProcess:
    environment = {key: value for key, value in os.environ.items() if key not in OMITTED_ENV_KEYS}
    environment.update(
        {
            "AGENTOPS_BIN_DIR": str(config["bin_dir"]),
            "AGENTOPS_HOST_HOME": str(config["host_home"]),
            "AGENTOPS_INSTALL_ROOT": str(config["install_root"]),
            "PYTHONPATH": str(current),
        }
    )
    return subprocess.run(
        [str(python), "-m", "agentops_mis_cli", *arguments],
        cwd=current,
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def parse_payload(process: subprocess.CompletedProcess) -> dict:
    try:
        payload = json.loads(process.stdout)
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def main() -> int:
    try:
        config = load_config()
        agentops = Path(config["agentops_path"])
        current = Path(config["current_path"])
        host_home = Path(config["host_home"])
        python = Path(config["python_path"])
        if agentops.is_symlink() or not agentops.is_file() or not os.access(agentops, os.X_OK):
            return fail("AgentOps MIS installation is incomplete. Reinstall the Private Host package.")
        if not current.is_dir() or not (current / "agentops_mis_cli").is_dir():
            return fail("AgentOps MIS installation is incomplete. Reinstall the Private Host package.")
        if python.is_symlink() or not python.is_file() or not os.access(python, os.X_OK):
            return fail("AgentOps MIS Python runtime is unavailable. Reinstall the Private Host package.")
        if host_home.is_symlink():
            return fail("AgentOps MIS Host path is unsafe. Use the CLI recovery guide.")

        lock_path = host_home.parent / ".agentops-mis-launcher.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock_descriptor = os.open(
            lock_path,
            os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        lock_metadata = os.fstat(lock_descriptor)
        if not stat.S_ISREG(lock_metadata.st_mode):
            os.close(lock_descriptor)
            return fail("AgentOps MIS launcher lock is unsafe. Use the CLI recovery guide.")
        os.fchmod(lock_descriptor, 0o600)
        fcntl.flock(lock_descriptor, fcntl.LOCK_EX)
        try:
            host_config = host_home / "config.json"
            host_secrets = host_home / "secrets.json"
            config_exists = host_config.is_file() and not host_config.is_symlink()
            secrets_exist = host_secrets.is_file() and not host_secrets.is_symlink()
            if config_exists != secrets_exist:
                return fail("AgentOps MIS Host setup is incomplete. Use the CLI recovery guide.")
            if not config_exists:
                initialized = run_cli(
                    python,
                    current,
                    ["host", "init", "--port", str(config["default_port"])],
                    config,
                )
                initialized.stdout = ""
                initialized.stderr = ""
                if initialized.returncode != 0:
                    return fail("AgentOps MIS could not initialize the local Host.")

            status_process = run_cli(python, current, ["host", "status"], config)
            status = parse_payload(status_process)
            if status_process.returncode != 0 or status.get("ok") is not True:
                return fail("AgentOps MIS could not read the local Host status.")
            if status.get("running") is not True:
                started = run_cli(python, current, ["host", "start", "--no-workers"], config)
                if started.returncode != 0 or parse_payload(started).get("ok") is not True:
                    return fail("AgentOps MIS could not start the local Host.")

            opened = run_cli(python, current, ["host", "open-console"], config)
            if opened.returncode != 0 or parse_payload(opened).get("ok") is not True:
                return fail(
                    "The local Host is running, but the Workspace could not be opened. "
                    "Run agentops host open-console from Terminal."
                )
            return 0
        finally:
            fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
            os.close(lock_descriptor)
    except (OSError, ValueError, json.JSONDecodeError, subprocess.TimeoutExpired):
        return fail("AgentOps MIS could not open the local Workspace. Use the CLI recovery guide.")


if __name__ == "__main__":
    raise SystemExit(main())
