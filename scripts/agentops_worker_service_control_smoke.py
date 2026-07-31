#!/usr/bin/env python3
"""Verify safe worker service-control previews without loading services."""
from __future__ import annotations

import json
import argparse
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def run(cmd: list[str], cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=60, check=False)


def parse_json(proc: subprocess.CompletedProcess[str]) -> dict:
    try:
        return json.loads(proc.stdout)
    except Exception:
        return {"parse_error": proc.stdout, "stderr": proc.stderr, "returncode": proc.returncode}


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def service_template(agent_id: str, adapter: str = "mock", confirm_run: bool = False) -> str:
    cmd = [
        sys.executable,
        "-m",
        "agentops_mis_cli.worker",
        "service-template",
        "--manager",
        "launchd",
        "--agent-id",
        agent_id,
        "--adapter",
        adapter,
    ]
    if confirm_run:
        cmd.append("--confirm-run")
    proc = run(cmd)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or proc.stdout)
    return proc.stdout


def idempotent_loaded_control_smoke(failures: list[str]) -> dict:
    from agentops_mis_cli import worker as worker_mod

    original_check = worker_mod.check_service_installation
    original_execute = worker_mod.execute_service_command
    executed: list[list[str]] = []

    def fake_check(args) -> dict:
        return {
            "ok": True,
            "service_file": {
                "exists": True,
                "command_has_worker": True,
                "adapter_present": True,
                "confirm_gate_ok": True,
                "token_like_detected": False,
                "raw_content_omitted": True,
            },
            "service_status": {"loaded": True},
            "token_omitted": True,
        }

    def fake_execute(command, timeout):
        executed.append(command)
        return {"command": " ".join(command), "ok": False, "summary": "should not execute"}

    worker_mod.check_service_installation = fake_check
    worker_mod.execute_service_command = fake_execute
    try:
        payload = worker_mod.control_service(argparse.Namespace(
            manager="launchd",
            action="load",
            workspace_id="proj_mvp",
            agent_id="agt_loaded_noop",
            adapter="hermes",
            label="",
            service_path="/tmp/local.agentops.worker.agt_loaded_noop.plist",
            api_key_placeholder="<paste one-time token here>",
            timeout=1,
            confirm_control=True,
        ))
    finally:
        worker_mod.check_service_installation = original_check
        worker_mod.execute_service_command = original_execute

    require(payload.get("ok") is True, f"loaded service control should pass: {payload}", failures)
    require(payload.get("service_already_loaded") is True, f"already-loaded marker missing: {payload}", failures)
    require(payload.get("service_control_skipped") is True, f"skip marker missing: {payload}", failures)
    require(payload.get("service_mutated") is False, f"already-loaded load should not mutate: {payload}", failures)
    require(payload.get("live_execution_performed") is False, f"already-loaded load should not count as live execution: {payload}", failures)
    require(not executed, f"already-loaded load executed OS command: {executed}", failures)
    return payload


def windows_stop_settle_smoke(failures: list[str]) -> dict:
    from agentops_mis_cli import worker as worker_mod

    original_check = worker_mod.check_service_installation
    original_execute = worker_mod.execute_service_command
    original_sleep = worker_mod.time.sleep
    executed: list[list[str]] = []
    sleeps: list[float] = []

    def fake_check(args) -> dict:
        return {
            "ok": True,
            "service_file": {
                "exists": True,
                "command_has_worker": True,
                "confirm_gate_ok": True,
                "token_like_detected": False,
            },
            "service_status": {"loaded": True},
        }

    def fake_execute(command, timeout):
        executed.append(command)
        return {"command": " ".join(command), "ok": True, "returncode": 0}

    worker_mod.check_service_installation = fake_check
    worker_mod.execute_service_command = fake_execute
    worker_mod.time.sleep = sleeps.append
    try:
        payload = worker_mod.control_service(argparse.Namespace(
            manager="windows-task",
            action="unload",
            workspace_id="proj_mvp",
            agent_id="agt_windows_settle",
            adapter="mock",
            label="",
            service_path="C:/AgentOps/agentops-worker.xml",
            api_key_placeholder="<paste one-time token here>",
            timeout=10,
            confirm_control=True,
        ))
    finally:
        worker_mod.check_service_installation = original_check
        worker_mod.execute_service_command = original_execute
        worker_mod.time.sleep = original_sleep

    require(payload.get("ok") is True, f"Windows unload should pass: {payload}", failures)
    require([command[1] for command in executed] == ["/End", "/Delete"], f"Windows unload sequence drifted: {executed}", failures)
    require(len(sleeps) == 1 and sleeps[0] > 0, f"Windows unload did not settle after /End: {sleeps}", failures)
    return payload


def main() -> int:
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="agentops_service_control_") as tmp:
        tmp_path = Path(tmp)
        mock_path = tmp_path / "local.agentops.worker.agt_service_control.plist"
        mock_path.write_text(service_template("agt_service_control"), encoding="utf-8")

        direct = run([
            sys.executable,
            "-m",
            "agentops_mis_cli.worker",
            "service-control",
            "--manager",
            "launchd",
            "--action",
            "load",
            "--agent-id",
            "agt_service_control",
            "--adapter",
            "mock",
            "--service-path",
            str(mock_path),
        ])
        direct_payload = parse_json(direct)
        require(direct.returncode == 0, f"agentops-worker service-control preview failed: {direct_payload}", failures)
        require(direct_payload.get("command") == "agentops-worker service-control", f"wrong direct command: {direct_payload}", failures)
        require(direct_payload.get("dry_run") is True and direct_payload.get("service_mutated") is False, f"preview mutated service: {direct_payload}", failures)
        require(direct_payload.get("command_results") == [], f"preview executed service command: {direct_payload}", failures)
        require(any("launchctl" in item for item in direct_payload.get("planned_commands") or []), f"launchd plan missing: {direct_payload}", failures)
        require(direct_payload.get("live_execution_performed") is False, f"preview performed live execution: {direct_payload}", failures)

        wrapper = run([
            str(ROOT / "scripts" / "agentops"),
            "worker",
            "service-control",
            "--manager",
            "launchd",
            "--action",
            "restart",
            "--agent-id",
            "agt_service_control",
            "--adapter",
            "mock",
            "--service-path",
            str(mock_path),
        ])
        wrapper_payload = parse_json(wrapper)
        require(wrapper.returncode == 0, f"agentops worker service-control preview failed: {wrapper_payload}", failures)
        require(wrapper_payload.get("command") == "agentops worker service-control", f"wrong wrapper command: {wrapper_payload}", failures)
        require(wrapper_payload.get("dry_run") is True and wrapper_payload.get("service_mutated") is False, f"wrapper preview mutated service: {wrapper_payload}", failures)
        require(len(wrapper_payload.get("planned_commands") or []) == 2, f"restart should preview unload+load: {wrapper_payload}", failures)

        hermes_path = tmp_path / "local.agentops.worker.agt_service_control_hermes.plist"
        hermes_path.write_text(service_template("agt_service_control_hermes", adapter="hermes", confirm_run=False), encoding="utf-8")
        hermes = run([
            sys.executable,
            "-m",
            "agentops_mis_cli.worker",
            "service-control",
            "--manager",
            "launchd",
            "--action",
            "load",
            "--agent-id",
            "agt_service_control_hermes",
            "--adapter",
            "hermes",
            "--service-path",
            str(hermes_path),
        ])
        hermes_payload = parse_json(hermes)
        require(hermes.returncode == 1, f"Hermes service without --confirm-run should fail: {hermes_payload}", failures)
        require(any("confirm-run" in item for item in hermes_payload.get("failures") or []), f"confirm-run failure missing: {hermes_payload}", failures)
        require(hermes_payload.get("live_execution_performed") is False, f"blocked Hermes control performed live execution: {hermes_payload}", failures)

        openclaw_path = tmp_path / "local.agentops.worker.agt_service_control_openclaw.plist"
        openclaw_path.write_text(service_template("agt_service_control_openclaw", adapter="openclaw", confirm_run=False), encoding="utf-8")
        openclaw = run([
            sys.executable,
            "-m",
            "agentops_mis_cli.worker",
            "service-control",
            "--manager",
            "launchd",
            "--action",
            "restart",
            "--agent-id",
            "agt_service_control_openclaw",
            "--adapter",
            "openclaw",
            "--service-path",
            str(openclaw_path),
        ])
        openclaw_payload = parse_json(openclaw)
        require(openclaw.returncode == 1, f"OpenClaw service without --confirm-run should fail: {openclaw_payload}", failures)
        require(any("confirm-run" in item for item in openclaw_payload.get("failures") or []), f"OpenClaw confirm-run failure missing: {openclaw_payload}", failures)
        require(openclaw_payload.get("live_execution_performed") is False, f"blocked OpenClaw control performed live execution: {openclaw_payload}", failures)

        unsafe_path = tmp_path / "unsafe.plist"
        fake_token = "agt" + "ok_fake_should_not_be_printed"
        unsafe_path.write_text(mock_path.read_text(encoding="utf-8") + f"\n<!-- {fake_token} -->\n", encoding="utf-8")
        unsafe = run([
            sys.executable,
            "-m",
            "agentops_mis_cli.worker",
            "service-control",
            "--manager",
            "launchd",
            "--action",
            "load",
            "--agent-id",
            "agt_service_control",
            "--adapter",
            "mock",
            "--service-path",
            str(unsafe_path),
        ])
        unsafe_payload = parse_json(unsafe)
        require(unsafe.returncode == 1, f"unsafe service control should fail: {unsafe_payload}", failures)
        require(any("token-like" in item for item in unsafe_payload.get("failures") or []), f"token-like failure missing: {unsafe_payload}", failures)

        serialized = json.dumps({
            "direct": direct_payload,
            "wrapper": wrapper_payload,
            "hermes": hermes_payload,
            "openclaw": openclaw_payload,
            "unsafe": unsafe_payload,
        }, ensure_ascii=False)
        require(fake_token not in serialized, "service-control leaked raw token-like content", failures)
        require("sk-" not in serialized and "ntn_" not in serialized, "service-control leaked secret-like content", failures)

        loaded_noop_payload = idempotent_loaded_control_smoke(failures)
        windows_settle_payload = windows_stop_settle_smoke(failures)

    print(json.dumps({
        "ok": not failures,
        "direct_preview_ok": not failures and direct_payload.get("dry_run"),
        "wrapper_preview_ok": not failures and wrapper_payload.get("dry_run"),
        "hermes_confirm_gate_blocked": hermes_payload.get("ok") is False,
        "openclaw_confirm_gate_blocked": openclaw_payload.get("ok") is False,
        "unsafe_blocked": unsafe_payload.get("ok") is False,
        "loaded_noop_ok": loaded_noop_payload.get("ok") is True and loaded_noop_payload.get("service_control_skipped") is True,
        "windows_stop_settle_ok": windows_settle_payload.get("ok") is True,
        "failures": failures,
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
