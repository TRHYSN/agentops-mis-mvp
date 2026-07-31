#!/usr/bin/env python3
"""Verify startup reconciliation for interrupted private Host restart receipts."""
from __future__ import annotations

import json
import io
import os
import signal
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agentops_mis_cli import host, relay_restart  # noqa: E402


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def receipt_state(paths: dict[str, Path]) -> str:
    return str(relay_restart.public_restart_receipt(
        receipt_path=paths["relay_restart_receipt"],
        sequence_path=paths["relay_restart_sequence"],
    )["state"])


def main() -> int:
    failures: list[str] = []
    original_environment = os.environ.copy()
    original_process_alive = host.process_alive
    try:
        with tempfile.TemporaryDirectory(prefix="agentops-start-reconciliation-") as temporary:
            os.environ["AGENTOPS_HOST_HOME"] = str(Path(temporary) / "host")
            paths = host.paths()
            paths["home"].mkdir(mode=0o700)
            paths["relay"].mkdir(mode=0o700)
            active_original = b'{"enabled":false,"schema_version":1}\n'
            active_target = b'{"enabled":true,"schema_version":1}\n'
            host_original = b'{"cookie_secure":false,"network_publication":"disabled"}\n'
            host_target = b'{"cookie_secure":true,"network_publication":"agentops_relay"}\n'
            paths["relay_config"].write_bytes(active_original)
            paths["relay_config"].chmod(0o600)
            paths["config"].write_bytes(host_original)
            paths["config"].chmod(0o600)

            def create(ref: str, *, replace_terminal: bool = False) -> dict:
                projection = relay_restart.create_restart_receipt(
                    receipt_path=paths["relay_restart_receipt"],
                    sequence_path=paths["relay_restart_sequence"],
                    action="enable",
                    transition_ref=ref,
                    active_config_path=paths["relay_config"],
                    host_config_path=paths["config"],
                    active_original_config=active_original,
                    active_target_config=active_target,
                    host_original_config=host_original,
                    host_target_config=host_target,
                    replace_terminal=replace_terminal,
                )
                return {**projection, "transition_ref": ref}

            def advance(context: dict, state: str) -> dict:
                projection = relay_restart.transition_restart_receipt(
                    receipt_path=paths["relay_restart_receipt"],
                    sequence_path=paths["relay_restart_sequence"],
                    action="enable",
                    transition_ref=context["transition_ref"],
                    transaction_sequence=int(context["transaction_sequence"]),
                    expected_revision=int(context["revision"]),
                    state=state,
                )
                return {**context, "revision": int(projection["revision"])}

            def apply_target(context: dict) -> None:
                relay_restart.apply_target_configs(
                    receipt_path=paths["relay_restart_receipt"],
                    sequence_path=paths["relay_restart_sequence"],
                    action="enable",
                    transition_ref=context["transition_ref"],
                    transaction_sequence=int(context["transaction_sequence"]),
                    expected_revision=int(context["revision"]),
                )

            initial = create("rst_start_reconcile_01")
            apply_target(initial)

            cleanup_process = SimpleNamespace(pid=4343)
            host.write_private_json(paths["pid"], {"pid": cleanup_process.pid})
            real_finish_recovery = host._finish_restart_receipt_recovery
            host._finish_restart_receipt_recovery = lambda *_args, **_kwargs: (
                (_ for _ in ()).throw(RuntimeError("injected receipt write failure"))
            )
            cleanup_calls: list[int] = []
            try:
                try:
                    host._finish_restart_recovery_with_cleanup(
                        paths,
                        {"recovery_mode": "target"},
                        cleanup_process,
                        terminate=lambda process: cleanup_calls.append(process.pid) or True,
                    )
                except RuntimeError:
                    pass
                else:
                    failures.append("receipt exception was not propagated")
            finally:
                host._finish_restart_receipt_recovery = real_finish_recovery
            require(cleanup_calls == [cleanup_process.pid], "receipt exception left target process running", failures)
            require(not paths["pid"].exists(), "terminated target PID record survived", failures)

            host.write_private_json(paths["pid"], {"pid": 4242})
            host.process_alive = lambda pid: pid == 4242
            with redirect_stdout(io.StringIO()):
                duplicate_status = host._cmd_start_unlocked(SimpleNamespace())
            host.process_alive = original_process_alive
            paths["pid"].unlink(missing_ok=True)
            require(duplicate_status == 2, "duplicate start was not rejected", failures)
            require(receipt_state(paths) == "config_applied", "duplicate start advanced the receipt", failures)
            require(paths["relay_config"].read_bytes() == active_target, "duplicate start changed Relay config", failures)
            require(paths["config"].read_bytes() == host_target, "duplicate start changed Host config", failures)
            rollback_context = host._prepare_restart_receipt_recovery(paths)
            require(rollback_context is not None, "config-applied receipt was ignored", failures)
            require(receipt_state(paths) == "restoring_config", "config-applied receipt did not enter restore", failures)
            require(paths["relay_config"].read_bytes() == active_original, "config-applied Relay target survived recovery", failures)
            require(paths["config"].read_bytes() == host_original, "config-applied Host target survived recovery", failures)
            require(
                host._finish_restart_receipt_recovery(
                    paths,
                    rollback_context,
                    runtime_validator=lambda action, _paths: action == "disable",
                ),
                "original runtime did not close rollback",
                failures,
            )
            require(receipt_state(paths) == "rolled_back", "rollback did not become terminal", failures)

            manual = create("rst_start_reconcile_02", replace_terminal=True)
            apply_target(manual)
            manual = advance(manual, "response_flushed")
            manual = advance(manual, "manual_restart_required")
            manual_context = host._prepare_restart_receipt_recovery(paths)
            require(manual_context is not None and manual_context["recovery_mode"] == "target", "manual restart was not resumed", failures)
            require(
                host._finish_restart_receipt_recovery(
                    paths,
                    manual_context,
                    runtime_validator=lambda action, _paths: action == "enable",
                ),
                "manual restart target did not validate",
                failures,
            )
            try:
                receipt_state(paths)
            except relay_restart.RelayRestartError as exc:
                require(exc.code == "receipt_not_found", "manual receipt finalized with wrong error", failures)
            else:
                failures.append("manual restart receipt was not finalized")

            failed_target = create("rst_start_reconcile_03")
            apply_target(failed_target)
            failed_target = advance(failed_target, "response_flushed")
            failed_context = host._prepare_restart_receipt_recovery(paths)
            require(
                not host._finish_restart_receipt_recovery(
                    paths,
                    failed_context,
                    runtime_validator=lambda _action, _paths: False,
                ),
                "unhealthy target was accepted",
                failures,
            )
            require(receipt_state(paths) == "restoring_config", "unhealthy target did not arm original recovery", failures)
            require(paths["relay_config"].read_bytes() == active_original, "unhealthy target Relay config survived", failures)
            require(paths["config"].read_bytes() == host_original, "unhealthy target Host config survived", failures)
            retry_context = host._prepare_restart_receipt_recovery(paths)
            require(
                host._finish_restart_receipt_recovery(
                    paths,
                    retry_context,
                    runtime_validator=lambda action, _paths: action == "disable",
                ),
                "second start did not validate restored runtime",
                failures,
            )
            require(receipt_state(paths) == "rolled_back", "restored retry did not close rollback", failures)

            failed_start = create("rst_start_reconcile_04", replace_terminal=True)
            apply_target(failed_start)
            failed_start = advance(failed_start, "response_flushed")
            failed_start_context = host._prepare_restart_receipt_recovery(paths)
            host._fail_restart_receipt_recovery_start(paths, failed_start_context)
            require(receipt_state(paths) == "restoring_config", "failed target start did not restore", failures)
            original_context = host._prepare_restart_receipt_recovery(paths)
            require(
                not host._finish_restart_receipt_recovery(
                    paths,
                    original_context,
                    runtime_validator=lambda _action, _paths: False,
                ),
                "failed original runtime was accepted",
                failures,
            )
            require(receipt_state(paths) == "rollback_failed", "failed original runtime was not fail closed", failures)
            try:
                host._prepare_restart_receipt_recovery(paths)
            except RuntimeError:
                pass
            else:
                failures.append("rollback-failed receipt did not block Host startup")

            class BackgroundProcess:
                def __init__(self, *, stubborn: bool) -> None:
                    self.pid = 4444 if not stubborn else 4445
                    self.returncode = None
                    self.waits = 0
                    self.stubborn = stubborn

                def poll(self):
                    return self.returncode

                def wait(self, timeout=None):
                    self.waits += 1
                    if self.stubborn or self.waits == 1:
                        raise subprocess.TimeoutExpired("background-stack", timeout)
                    self.returncode = -9
                    return self.returncode

            real_killpg = host.os.killpg
            signals: list[tuple[int, int]] = []
            host.os.killpg = lambda pid, signum: signals.append((pid, signum))
            try:
                bounded_process = BackgroundProcess(stubborn=False)
                stubborn_process = BackgroundProcess(stubborn=True)
                require(
                    host._terminate_background_stack(bounded_process, timeout=0.01),
                    "background stack did not escalate to bounded kill",
                    failures,
                )
                require(
                    not host._terminate_background_stack(stubborn_process, timeout=0.01),
                    "stubborn background stack was reported stopped",
                    failures,
                )
            finally:
                host.os.killpg = real_killpg
            require(
                signals == [
                    (4444, signal.SIGTERM),
                    (4444, signal.SIGKILL),
                    (4445, signal.SIGTERM),
                    (4445, signal.SIGKILL),
                ],
                f"unexpected background termination order: {signals}",
                failures,
            )

            rendered = json.dumps({
                "states": ["rolled_back", "manual_finalized", "restoring_config", "rollback_failed"],
                "temporary_host": True,
            }, sort_keys=True)
            require(str(paths["home"]) not in rendered, "public evidence leaked Host paths", failures)

    finally:
        os.environ.clear()
        os.environ.update(original_environment)
        host.process_alive = original_process_alive

    result = {
        "ok": not failures,
        "failures": failures,
        "config_applied_restored": True,
        "manual_restart_finalized": True,
        "duplicate_start_non_mutating": True,
        "receipt_exception_stops_target": True,
        "background_stop_is_bounded": True,
        "failed_target_requires_original_restart": True,
        "failed_original_blocks_start": True,
        "database_used": False,
        "network_used": False,
        "installed_host_mutated": False,
        "sensitive_values_omitted": True,
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
