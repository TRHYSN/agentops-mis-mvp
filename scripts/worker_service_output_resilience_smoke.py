#!/usr/bin/env python3
"""Verify quiet service templates and nonfatal diagnostic stdout failures."""
from __future__ import annotations

import argparse
import ast
import builtins
import io
import json
import os
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agentops_mis_cli import worker  # noqa: E402


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def service_args(manager: str) -> argparse.Namespace:
    return worker.build_service_template_parser().parse_args([
        "--manager",
        manager,
        "--adapter",
        "openclaw",
        "--agent-id",
        "agt_worker_service_output_smoke",
        "--confirm-run",
    ])


def hermes_service_args(manager: str) -> argparse.Namespace:
    return worker.build_service_template_parser().parse_args([
        "--manager",
        manager,
        "--adapter",
        "hermes",
        "--agent-id",
        "agt_worker_service_output_hermes_smoke",
        "--confirm-run",
        "--hermes-gateway-url",
        "http://127.0.0.1:8643/",
    ])


def function_source(path: Path, function_name: str) -> str:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    node = next(
        item
        for item in tree.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == function_name
    )
    return ast.get_source_segment(source, node) or ""


def main() -> int:
    failures: list[str] = []
    launchd_args = service_args("launchd")
    systemd_args = service_args("systemd")
    launchd = worker.render_launchd_template(launchd_args)
    systemd = worker.render_systemd_template(systemd_args)
    for manager, rendered in (("launchd", launchd), ("systemd", systemd)):
        require("--write-state" in rendered, f"{manager} service lost bounded state output", failures)
        require("--jsonl-log" not in rendered, f"{manager} service enables unbounded per-poll JSONL", failures)
        require("--confirm-run" in rendered, f"{manager} service lost live confirmation gate", failures)
    hermes_launchd = worker.render_launchd_template(hermes_service_args("launchd"))
    hermes_systemd = worker.render_systemd_template(hermes_service_args("systemd"))
    require("HERMES_GATEWAY_URL" in hermes_launchd and "http://127.0.0.1:8643" in hermes_launchd, "launchd Hermes gateway target missing", failures)
    require("HERMES_GATEWAY_URL=http://127.0.0.1:8643" in hermes_systemd, "systemd Hermes gateway target missing", failures)

    gateway_launch = function_source(ROOT / "server.py", "agent_gateway_launch_steps")
    worker_policy = function_source(ROOT / "server.py", "worker_adapter_readiness")
    api_daemon = function_source(ROOT / "server.py", "start_local_worker_daemon")
    local_stack_worker = function_source(ROOT / "scripts" / "run_local_stack.py", "worker_command")
    require("--jsonl-log" not in gateway_launch, "Agent Gateway long-loop packet enables per-poll JSONL", failures)
    require("--jsonl-log" not in worker_policy, "remote Worker policy enables per-poll JSONL", failures)
    require("--jsonl-log" not in api_daemon, "API-started Worker daemon enables per-poll JSONL", failures)
    require("--jsonl-log" not in local_stack_worker, "managed local stack enables per-poll JSONL", failures)

    foreground = worker.build_parser().parse_args(["--jsonl-log"])
    require(foreground.jsonl_log is True, "explicit foreground JSONL flag is unavailable", failures)
    capture = io.StringIO()
    with redirect_stdout(capture):
        emitted = worker.emit_jsonl(foreground, {"event": "bounded.foreground", "token_omitted": True})
    require(emitted is True, "healthy foreground JSONL was not emitted", failures)
    require(json.loads(capture.getvalue()).get("event") == "bounded.foreground", "foreground JSONL payload mismatch", failures)

    original_stdout = sys.stdout
    original_print = builtins.print
    attempts = 0

    def fail_print(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        raise OSError(28, "No space left on device")

    failure_args = worker.build_parser().parse_args(["--jsonl-log"])
    try:
        builtins.print = fail_print
        first = worker.emit_jsonl(failure_args, {"event": "disk.full"})
        second = worker.emit_jsonl(failure_args, {"event": "disk.full.repeat"})
    finally:
        builtins.print = original_print
        replacement_stdout = sys.stdout
        sys.stdout = original_stdout
        if replacement_stdout is not original_stdout:
            replacement_stdout.close()
    require(first is False and second is False, "failed diagnostic output was not suppressed", failures)
    require(attempts == 1, f"disabled JSONL retried failed output {attempts} times", failures)
    require(getattr(failure_args, "_jsonl_log_disabled", False) is True, "failed JSONL did not latch disabled state", failures)

    summary_attempts = 0

    def fail_summary_print(*_args, **_kwargs):
        nonlocal summary_attempts
        summary_attempts += 1
        raise BrokenPipeError("detached terminal")

    try:
        builtins.print = fail_summary_print
        summary_emitted = worker.emit_worker_summary({
            "ok": True,
            "processed": 1,
            "raw_prompt_omitted": True,
            "raw_response_omitted": True,
            "token_omitted": True,
        })
    finally:
        builtins.print = original_print
        replacement_stdout = sys.stdout
        sys.stdout = original_stdout
        if replacement_stdout is not original_stdout:
            replacement_stdout.close()
    require(summary_emitted is False, "failed final Worker summary output was not suppressed", failures)
    require(summary_attempts == 1, f"final Worker summary retried failed output {summary_attempts} times", failures)

    daemon_args = worker.build_parser().parse_args(["--max-tasks", "0"])
    history: list[dict] = []
    omitted = 0
    history_limit = worker.worker_result_history_limit(daemon_args)
    for index in range(history_limit + 7):
        omitted += worker.append_worker_result(history, {"index": index, "ok": True}, history_limit)
    require(history_limit == 20, f"unbounded daemon history limit mismatch: {history_limit}", failures)
    require(len(history) == history_limit, f"daemon result history grew past limit: {len(history)}", failures)
    require(omitted == 7, f"daemon omitted-result count mismatch: {omitted}", failures)
    require(history[0].get("index") == 7, f"daemon history did not retain newest window: {history[0]}", failures)

    one_shot_args = worker.build_parser().parse_args(["--once", "--max-tasks", "0"])
    require(worker.worker_result_history_limit(one_shot_args) == 1, "one-shot history should retain exactly one result", failures)

    require(
        worker.worker_result_failed({"processed": False, "ok": False}) is True,
        "unprocessed explicit failure would leave final Worker status successful",
        failures,
    )
    require(
        worker.worker_result_failed({"processed": False, "ok": True}) is False,
        "intentional approval/intake pause was treated as Worker failure",
        failures,
    )
    require(
        worker.worker_result_failed({"processed": False, "reason": "no_task"}) is False,
        "idle result without explicit failure was treated as Worker failure",
        failures,
    )
    with tempfile.TemporaryDirectory(prefix="agentops-worker-state-") as temp_dir:
        state_args = worker.build_parser().parse_args([
            "--state-path",
            str(Path(temp_dir) / "worker.state.json"),
            "--continue-on-error",
        ])
        state = worker.WorkerState(state_args)
        state.record_result({
            "processed": False,
            "ok": False,
            "error_type": "FixtureExplicitFailure",
            "error_message": "bounded explicit failure",
            "raw_response_omitted": True,
            "token_omitted": True,
        })
        require(state.data.get("status") == "failed", "explicit failure state was reported as idle", failures)
        require(state.data.get("total_errors") == 1, "explicit failure did not increment total errors", failures)
        require(state.data.get("consecutive_errors") == 1, "explicit failure did not increment consecutive errors", failures)
        require(state.data.get("consecutive_idle") == 0, "explicit failure incremented idle count", failures)
        require(
            (state.data.get("last_error") or {}).get("error_type") == "FixtureExplicitFailure",
            "explicit failure lost bounded error evidence",
            failures,
        )
        state.record_result({"processed": False, "reason": "no_task"})
        require(state.data.get("status") == "idle", "healthy idle result did not restore idle state", failures)
        require(state.data.get("consecutive_errors") == 0, "healthy idle result did not clear consecutive errors", failures)
        require(state.data.get("consecutive_idle") == 1, "healthy idle result did not increment idle count", failures)
        require(state.data.get("last_error") is None, "healthy idle result retained stale error evidence", failures)

    sessions: list[dict] = []
    sessions_omitted = 0
    for index in range(27):
        sessions_omitted += worker.append_worker_session_history(sessions, {"index": index})
    require(len(sessions) == 20, f"daemon session history grew past limit: {len(sessions)}", failures)
    require(sessions_omitted == 7, f"daemon omitted-session count mismatch: {sessions_omitted}", failures)
    require(sessions[0].get("index") == 7, f"daemon session history did not retain newest window: {sessions[0]}", failures)

    result = {
        "ok": not failures,
        "operation": "worker_service_output_resilience_smoke",
        "service_templates_quiet_by_default": "--jsonl-log" not in launchd and "--jsonl-log" not in systemd,
        "managed_loop_commands_quiet_by_default": not any(
            "--jsonl-log" in source
            for source in (gateway_launch, worker_policy, api_daemon, local_stack_worker)
        ),
        "foreground_jsonl_preserved": emitted is True,
        "failed_output_nonfatal": first is False and second is False and attempts == 1,
        "failed_final_summary_nonfatal": summary_emitted is False and summary_attempts == 1,
        "daemon_result_history_bounded": len(history) == 20 and omitted == 7,
        "daemon_session_history_bounded": len(sessions) == 20 and sessions_omitted == 7,
        "explicit_failure_semantics": worker.worker_result_failed({"processed": False, "ok": False}),
        "explicit_failure_state_accounting": state.data.get("status") == "idle"
        and state.data.get("consecutive_errors") == 0
        and state.data.get("consecutive_idle") == 1,
        "failures": failures,
        "live_execution_performed": False,
        "token_omitted": True,
    }
    original_print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
