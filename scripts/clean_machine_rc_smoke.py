#!/usr/bin/env python3
"""Verify a clean checkout can run the RC evidence gates with isolated state.

The smoke clones the current repository into a temporary directory, checks out
the exact current HEAD, rejects tracked/generated runtime files, and runs a
small release-candidate command chain with a temporary SQLite database. It does
not call live runtimes, external providers, Dify, Notion, OpenClaw or Hermes.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable
DEFAULT_COMMANDS = [
    [PYTHON, "-m", "py_compile", "server.py", "agentops_mis_cli/agentops.py", "agentops_mis_cli/worker.py"],
    [PYTHON, "scripts/agentops_pip_install_smoke.py"],
    [PYTHON, "scripts/run_local_stack_smoke.py"],
    [PYTHON, "scripts/release_freeze_protocol_smoke.py"],
    [PYTHON, "scripts/release_evidence_packet_smoke.py"],
    [PYTHON, "scripts/license_provenance_smoke.py"],
    [PYTHON, "scripts/public_claims_release_gate_smoke.py"],
    [PYTHON, "scripts/migration_rollback_smoke.py"],
    [PYTHON, "scripts/safe_closure_evidence_packet_smoke.py"],
]
FORBIDDEN_TRACKED_PATTERNS = [
    re.compile(r"(^|/)(node_modules|\.next|dist|__pycache__|\.pytest_cache|\.agentops_runtime)(/|$)"),
    re.compile(r"(^|/)(agentops_mis\.db|.*\.sqlite3?|.*\.db(?:-wal|-shm)?|\.env$|\.env\..*|.*\.log$|.*\.pid$|.*\.sock$|.*\.pem$|.*\.key$|.*\.jsonl$)"),
]
ALLOWED_TRACKED = {".env.example"}
SECRET_PATTERNS = [
    re.compile(r"Authorization:", re.IGNORECASE),
    re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"agtok_[A-Za-z0-9_]+"),
    re.compile(r"agtsess_[A-Za-z0-9_]+"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"ntn_[A-Za-z0-9]{8,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]+"),
    re.compile(r"gh[opsu]_[A-Za-z0-9_]+"),
]


def run(cmd: list[str], *, cwd: Path = ROOT, env: dict[str, str] | None = None, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout, check=False)


def git_text(args: list[str], *, cwd: Path = ROOT) -> str:
    proc = run(["git", *args], cwd=cwd, timeout=30)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "git command failed").strip())
    return (proc.stdout or "").strip()


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def tracked_files(cwd: Path) -> list[str]:
    proc = run(["git", "ls-files", "-z"], cwd=cwd, timeout=30)
    if proc.returncode != 0:
        return []
    return [item for item in (proc.stdout or "").split("\0") if item]


def forbidden_tracked(files: list[str]) -> list[str]:
    result: list[str] = []
    for path in files:
        if path in ALLOWED_TRACKED:
            continue
        if any(pattern.search(path) for pattern in FORBIDDEN_TRACKED_PATTERNS):
            result.append(path)
    return sorted(result)


def redact(text: str) -> str:
    redacted = text
    for pattern in SECRET_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


def run_clean_command(clone_dir: Path, cmd: list[str], env: dict[str, str]) -> dict[str, Any]:
    proc = run(cmd, cwd=clone_dir, env=env, timeout=180)
    combined = redact((proc.stdout or "") + (proc.stderr or ""))
    return {
        "command": " ".join(cmd),
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "output_tail": combined[-1200:],
    }


def choose_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_ready(base_url: str, proc: subprocess.Popen[str], timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(base_url + "/api/agent-gateway/status", timeout=1) as resp:
                return resp.status == 200
        except (TimeoutError, urllib.error.URLError):
            time.sleep(0.25)
    return False


def terminate(proc: subprocess.Popen[str]) -> tuple[str, str]:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    stdout, stderr = proc.communicate(timeout=5)
    return stdout or "", stderr or ""


def run_reset_delivery_board(clone_dir: Path, tmp_path: Path, env: dict[str, str]) -> dict[str, Any]:
    port = choose_port()
    base_url = f"http://127.0.0.1:{port}"
    server_env = env.copy()
    server_env["AGENTOPS_DB_PATH"] = str(tmp_path / "clean_machine_reset_delivery.sqlite")
    server_env["AGENTOPS_BASE_URL"] = base_url
    proc = subprocess.Popen(
        [PYTHON, "server.py", "--host", "127.0.0.1", "--port", str(port), "--reset", "--serve"],
        cwd=clone_dir,
        env=server_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    ready = False
    delivery: subprocess.CompletedProcess[str] | None = None
    stdout = ""
    stderr = ""
    try:
        ready = wait_ready(base_url, proc)
        if ready:
            delivery = run(
                [PYTHON, "scripts/customer_delivery_board_smoke.py", "--base-url", base_url],
                cwd=clone_dir,
                env=server_env,
                timeout=180,
            )
    finally:
        stdout, stderr = terminate(proc)
    combined = redact(stdout + stderr + ((delivery.stdout if delivery else "") or "") + ((delivery.stderr if delivery else "") or ""))
    return {
        "command": "python3 server.py --reset --serve && python3 scripts/customer_delivery_board_smoke.py",
        "ok": ready and delivery is not None and delivery.returncode == 0,
        "returncode": delivery.returncode if delivery is not None else None,
        "server_ready": ready,
        "base_url": base_url,
        "output_tail": combined[-1200:],
    }


def main() -> int:
    failures: list[str] = []
    current_head = git_text(["rev-parse", "HEAD"])
    source_url = os.environ.get("AGENTOPS_CLEAN_RC_SOURCE", str(ROOT))
    command_results: list[dict[str, Any]] = []
    clone_head = ""
    clone_files: list[str] = []
    forbidden_files: list[str] = []

    with tempfile.TemporaryDirectory(prefix="agentops-clean-rc-") as tmp:
        tmp_path = Path(tmp)
        clone_dir = tmp_path / "agentops-mis-mvp"
        isolated_home = tmp_path / "home"
        isolated_tmp = tmp_path / "tmp"
        isolated_home.mkdir(mode=0o700)
        isolated_tmp.mkdir(mode=0o700)
        clone = run(["git", "clone", "--no-local", source_url, str(clone_dir)], timeout=120)
        require(clone.returncode == 0, f"clean clone failed: {redact(clone.stderr or clone.stdout or '')[-1200:]}", failures)
        if clone.returncode == 0:
            checkout = run(["git", "checkout", "--detach", current_head], cwd=clone_dir, timeout=60)
            require(checkout.returncode == 0, f"checkout current HEAD failed: {redact(checkout.stderr or checkout.stdout or '')[-1200:]}", failures)
            clone_head = git_text(["rev-parse", "HEAD"], cwd=clone_dir)
            require(clone_head == current_head, f"clone head mismatch: {clone_head} != {current_head}", failures)
            clone_files = tracked_files(clone_dir)
            forbidden_files = forbidden_tracked(clone_files)
            require(not forbidden_files, f"clean clone tracks forbidden runtime/generated files: {forbidden_files[:20]}", failures)
            require((clone_dir / "ui" / "start-building-app" / "package-lock.json").exists(), "UI lockfile missing from clean clone", failures)
            require((clone_dir / ".github" / "workflows" / "ci.yml").exists(), "CI workflow missing from clean clone", failures)

            env = os.environ.copy()
            env.update(
                {
                    "HOME": str(isolated_home),
                    "TMPDIR": str(isolated_tmp),
                    "AGENTOPS_DB_PATH": str(tmp_path / "clean_machine_rc.sqlite"),
                    "AGENTOPS_PIP_SMOKE_BASE_URL": "http://127.0.0.1:1",
                    "AGENTOPS_SKIP_SEED_EXPORTS": "1",
                    "AGENTOPS_DEPLOYMENT_MODE": "local",
                    "HERMES_ALLOW_REAL_RUN": "false",
                    "DIFY_ALLOW_REAL_UPLOAD": "false",
                    "NOTION_TOKEN": "",
                    "NOTION_PARENT_PAGE_ID": "",
                    "NOTION_DATABASE_ID": "",
                }
            )
            for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DIFY_KB_API_KEY", "AGENTOPS_API_KEY"):
                env.pop(name, None)
            for cmd in DEFAULT_COMMANDS:
                result = run_clean_command(clone_dir, cmd, env)
                command_results.append(result)
                require(result["ok"], f"clean clone command failed: {result['command']}", failures)
            reset_delivery = run_reset_delivery_board(clone_dir, tmp_path, env)
            command_results.append(reset_delivery)
            require(reset_delivery["ok"], "clean clone reset server / delivery board closure failed", failures)

    output_text = json.dumps(command_results, ensure_ascii=False)
    secret_leaked = any(pattern.search(output_text) for pattern in SECRET_PATTERNS)
    require(not secret_leaked, "clean-machine RC smoke output leaked token-like material", failures)
    print(
        json.dumps(
            {
                "ok": not failures,
                "operation": "clean_machine_rc_smoke",
                "source": "local_git_clone",
                "head_sha": current_head,
                "clone_head_sha": clone_head,
                "tracked_files": len(clone_files),
                "forbidden_tracked_files": forbidden_files,
                "commands": [
                    {
                        "command": item["command"],
                        "ok": item["ok"],
                        "returncode": item["returncode"],
                        "server_ready": item.get("server_ready"),
                    }
                    for item in command_results
                ],
                "ui_build_evidence": "Covered by dedicated CI UI build job; package-lock presence is verified in the clean clone.",
                "closure_evidence": "Clean clone runs pip install, agentops/agentops-worker help, release gates, safe closure packet, server reset and delivery board smoke with isolated SQLite state.",
                "failed_output_tails": [
                    {"command": item["command"], "output_tail": item.get("output_tail", "")}
                    for item in command_results
                    if not item.get("ok")
                ],
                "safety": {
                    "temporary_directory": True,
                    "temporary_home": True,
                    "temporary_tmpdir": True,
                    "temporary_sqlite": True,
                    "existing_local_server_ignored": True,
                    "live_execution_performed": False,
                    "external_provider_calls": False,
                    "token_omitted": True,
                },
                "failures": failures,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
