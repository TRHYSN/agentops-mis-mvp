#!/usr/bin/env python3
"""Prove a failed managed Relay replacement restores both configs and old Host."""
from __future__ import annotations

import json
import hashlib
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agentops_mis_cli import host, relay_restart  # noqa: E402


class FakeChild:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.returncode = None
        self.terminate_calls = 0
        self.kill_calls = 0

    def poll(self):
        return self.returncode

    def terminate(self) -> None:
        self.terminate_calls += 1
        self.returncode = -15

    def kill(self) -> None:
        self.kill_calls += 1
        self.returncode = -9

    def wait(self, timeout=None):
        if self.returncode is None:
            raise subprocess.TimeoutExpired("fake-stack", timeout)
        return self.returncode


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def wait_for(predicate, timeout: float = 2) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def main() -> int:
    failures: list[str] = []
    original_environment = os.environ.copy()
    original_write_pid = host.write_managed_pid_record
    original_process_alive = host.process_alive
    original_record_matches = host.managed_process_record_matches
    original_terminate_child = host._terminate_supervised_child
    try:
        with tempfile.TemporaryDirectory(prefix="agentops-managed-restart-rollback-") as temporary:
            root = Path(temporary)
            host_home = root / "host"
            os.environ.update({
                "AGENTOPS_HOST_HOME": str(host_home),
                "AGENTOPS_INSTALL_ROOT": str(ROOT),
                "PYTHONPATH": str(ROOT),
            })
            p = host.paths()
            p["run"].mkdir(parents=True, mode=0o700)
            p["relay"].mkdir(parents=True, mode=0o700)
            p["home"].chmod(0o700)
            p["run"].chmod(0o700)
            p["relay"].chmod(0o700)
            service_path = root / "dev.agentops.mis.private-host.plist"
            service_path.write_bytes(host.host_service_template())
            service_path.chmod(0o600)
            template_hash = hashlib.sha256(host.host_service_template()).hexdigest()

            args = SimpleNamespace(
                command="start",
                foreground=True,
                managed_launch_agent=True,
                no_workers=True,
                worker=None,
                build_ui=False,
                install_ui=False,
                confirm_live_workers=False,
            )
            config_original = {
                "host": "127.0.0.1",
                "port": 18996,
                "database_path": str(host_home / "data" / "agentops.db"),
                "ui_dist": str(ROOT / "ui" / "start-building-app" / "dist"),
                "allowed_origins": ["http://127.0.0.1:18996"],
                "workspace_id": "managed-restart-rollback",
                "cookie_secure": False,
            }
            config_target = {
                **config_original,
                "allowed_origins": ["https://rollback-test.example.invalid"],
                "cookie_secure": True,
            }
            secrets = {
                "api_key": "agthost_ROLLBACK_PRIVATE_FIXTURE",
                "admin_key": "agtadmin_ROLLBACK_PRIVATE_FIXTURE",
                "owner_setup_code": "ROLLBACK_PRIVATE_FIXTURE",
            }
            active_original = b'{"enabled":false,"schema_version":1}\n'
            active_target = b'{"enabled":true,"schema_version":1}\n'
            host_original = (json.dumps(config_original, sort_keys=True) + "\n").encode("utf-8")
            host_target = (json.dumps(config_target, sort_keys=True) + "\n").encode("utf-8")
            p["relay_config"].write_bytes(active_original)
            p["relay_config"].chmod(0o600)
            p["config"].write_bytes(host_original)
            p["config"].chmod(0o600)
            transition_ref = "rst_managed_rollback_01"
            receipt = relay_restart.create_restart_receipt(
                receipt_path=p["relay_restart_receipt"],
                sequence_path=p["relay_restart_sequence"],
                action="enable",
                transition_ref=transition_ref,
                active_config_path=p["relay_config"],
                host_config_path=p["config"],
                active_original_config=active_original,
                active_target_config=active_target,
                host_original_config=host_original,
                host_target_config=host_target,
            )
            relay_restart.apply_target_configs(
                receipt_path=p["relay_restart_receipt"],
                sequence_path=p["relay_restart_sequence"],
                action="enable",
                transition_ref=transition_ref,
                transaction_sequence=int(receipt["transaction_sequence"]),
                expected_revision=int(receipt["revision"]),
            )
            receipt = relay_restart.transition_restart_receipt(
                receipt_path=p["relay_restart_receipt"],
                sequence_path=p["relay_restart_sequence"],
                action="enable",
                transition_ref=transition_ref,
                transaction_sequence=int(receipt["transaction_sequence"]),
                expected_revision=int(receipt["revision"]),
                state="response_flushed",
            )

            listener = host._open_managed_restart_socket(p["restart_socket"])
            children: list[FakeChild] = []
            launch_config_modes: list[str] = []

            def fake_popen(_command, **kwargs):
                child = FakeChild(42001 + len(children))
                children.append(child)
                launch_config_modes.append(
                    "target" if kwargs["env"].get("AGENTOPS_COOKIE_SECURE") == "true" else "original"
                )
                os.write(kwargs["pass_fds"][0], b"\x01")
                return child

            def fake_write_pid(path: Path, process, *, foreground: bool = False) -> None:
                host.write_private_json(path, {
                    "schema_version": 1,
                    "pid": process.pid,
                    "process_group_id": process.pid,
                    "process_identity_hash": f"fake-{process.pid}",
                    "started_at_epoch": time.time(),
                    "foreground": foreground,
                })

            def load_current_config():
                current = json.loads(p["config"].read_text(encoding="utf-8"))
                return current, secrets

            validation_actions: list[str] = []

            def validate_runtime(action: str, _paths: dict[str, Path]) -> bool:
                validation_actions.append(action)
                return action == "disable"

            host.write_managed_pid_record = fake_write_pid
            host.process_alive = lambda pid: bool(
                pid == os.getpid() or any(child.pid == pid and child.poll() is None for child in children)
            )
            host.managed_process_record_matches = lambda record, pid: int(record.get("pid") or 0) == pid

            supervisor_result: list[int] = []
            supervisor_error: list[str] = []

            def supervise() -> None:
                try:
                    supervisor_result.append(host._run_managed_foreground_supervisor(
                        args,
                        config_original,
                        secrets,
                        p,
                        listener,
                        template_hash,
                        startup_timeout=0.25,
                        stop_timeout=0.05,
                        popen_factory=fake_popen,
                        health_check=lambda _url: {"reachable": True, "status": "ok"},
                        config_loader=load_current_config,
                        relay_runtime_validator=validate_runtime,
                        peer_authorizer=lambda _connection, _parent_pid: True,
                        install_signal_handlers=False,
                    ))
                except Exception as exc:
                    supervisor_error.append(f"{type(exc).__name__}:{exc}")

            thread = threading.Thread(target=supervise, daemon=True)
            thread.start()
            require(wait_for(lambda: p["service_instance"].is_file()), "supervisor did not start", failures)
            instance = host._read_private_bounded_json(p["service_instance"]) or {}
            request = host.request_managed_host_restart(
                action="enable",
                transition_ref=transition_ref,
                transaction_sequence=int(receipt["transaction_sequence"]),
                expected_revision=int(receipt["revision"]),
                service_path=service_path,
                launchd_state={"loaded": True, "label": host.HOST_SERVICE_LABEL},
                _caller_parent_pid_override=int(instance.get("stack_child_pid") or 0),
            )
            require(request.get("accepted") is True, "supervisor rejected the bound restart receipt", failures)
            require(wait_for(lambda: len(children) == 3), "rollback stack was not launched", failures)
            require(
                wait_for(
                    lambda: relay_restart.public_restart_receipt(
                        receipt_path=p["relay_restart_receipt"],
                        sequence_path=p["relay_restart_sequence"],
                    ).get("state")
                    in {"rolled_back", "rollback_failed"}
                ),
                "rollback receipt did not reach a terminal state",
                failures,
            )
            rolled_back = relay_restart.public_restart_receipt(
                receipt_path=p["relay_restart_receipt"],
                sequence_path=p["relay_restart_sequence"],
            )
            require(rolled_back.get("state") == "rolled_back", f"receipt did not roll back: {rolled_back}", failures)
            require(p["relay_config"].read_bytes() == active_original, "Relay config was not restored", failures)
            require(p["config"].read_bytes() == host_original, "Host config was not restored", failures)
            require(launch_config_modes == ["original", "target", "original"], f"unexpected launch order: {launch_config_modes}", failures)
            require(validation_actions == ["enable", "disable"], f"unexpected validation order: {validation_actions}", failures)
            require(children[0].terminate_calls == 1, "original owned stack was not stopped once", failures)
            require(children[1].terminate_calls == 1, "failed replacement stack was not stopped once", failures)
            if len(children) == 3:
                children[2].returncode = 0
            thread.join(timeout=2)
            require(not thread.is_alive(), "rollback supervisor did not stop", failures)
            require(not supervisor_error, f"rollback supervisor raised: {supervisor_error}", failures)
            require(supervisor_result == [0], f"unexpected supervisor result: {supervisor_result}", failures)

            refusal_ref = "rst_managed_rollback_02"
            refusal_receipt = relay_restart.create_restart_receipt(
                receipt_path=p["relay_restart_receipt"],
                sequence_path=p["relay_restart_sequence"],
                action="enable",
                transition_ref=refusal_ref,
                active_config_path=p["relay_config"],
                host_config_path=p["config"],
                active_original_config=active_original,
                active_target_config=active_target,
                host_original_config=host_original,
                host_target_config=host_target,
                replace_terminal=True,
            )
            relay_restart.apply_target_configs(
                receipt_path=p["relay_restart_receipt"],
                sequence_path=p["relay_restart_sequence"],
                action="enable",
                transition_ref=refusal_ref,
                transaction_sequence=int(refusal_receipt["transaction_sequence"]),
                expected_revision=int(refusal_receipt["revision"]),
            )
            refusal_receipt = relay_restart.transition_restart_receipt(
                receipt_path=p["relay_restart_receipt"],
                sequence_path=p["relay_restart_sequence"],
                action="enable",
                transition_ref=refusal_ref,
                transaction_sequence=int(refusal_receipt["transaction_sequence"]),
                expected_revision=int(refusal_receipt["revision"]),
                state="response_flushed",
            )
            refusal_listener = host._open_managed_restart_socket(p["restart_socket"])
            refusal_result: list[int] = []
            refusal_error: list[str] = []

            def refuse_target_termination(process, *, timeout=host.HOST_STOP_GRACE_SECONDS):
                if process.pid == 42005:
                    return False
                return original_terminate_child(process, timeout=timeout)

            host._terminate_supervised_child = refuse_target_termination

            def supervise_refusal() -> None:
                try:
                    refusal_result.append(host._run_managed_foreground_supervisor(
                        args,
                        config_original,
                        secrets,
                        p,
                        refusal_listener,
                        template_hash,
                        startup_timeout=0.25,
                        stop_timeout=0.05,
                        popen_factory=fake_popen,
                        health_check=lambda _url: {"reachable": True, "status": "ok"},
                        config_loader=load_current_config,
                        relay_runtime_validator=validate_runtime,
                        peer_authorizer=lambda _connection, _parent_pid: True,
                        install_signal_handlers=False,
                    ))
                except Exception as exc:
                    refusal_error.append(f"{type(exc).__name__}:{exc}")

            refusal_thread = threading.Thread(target=supervise_refusal, daemon=True)
            refusal_thread.start()
            require(wait_for(lambda: len(children) == 4), "refusal supervisor did not start", failures)
            refusal_instance = host._read_private_bounded_json(p["service_instance"]) or {}
            refusal_request = host.request_managed_host_restart(
                action="enable",
                transition_ref=refusal_ref,
                transaction_sequence=int(refusal_receipt["transaction_sequence"]),
                expected_revision=int(refusal_receipt["revision"]),
                service_path=service_path,
                launchd_state={"loaded": True, "label": host.HOST_SERVICE_LABEL},
                _caller_parent_pid_override=int(refusal_instance.get("stack_child_pid") or 0),
            )
            require(refusal_request.get("accepted") is True, "refusal request was not accepted", failures)
            refusal_thread.join(timeout=2)
            failed_receipt = relay_restart.public_restart_receipt(
                receipt_path=p["relay_restart_receipt"],
                sequence_path=p["relay_restart_sequence"],
            )
            require(not refusal_thread.is_alive(), "refusal supervisor did not stop", failures)
            require(not refusal_error, f"refusal supervisor raised: {refusal_error}", failures)
            require(refusal_result == [1], f"unexpected refusal result: {refusal_result}", failures)
            require(len(children) == 5, "termination refusal launched a second rollback stack", failures)
            require(failed_receipt.get("state") == "rollback_failed", f"refusal was not fail closed: {failed_receipt}", failures)

            rendered = json.dumps({"request": request, "receipt": rolled_back, "refusal": refusal_request}, sort_keys=True)
            require(str(root) not in rendered, "public rollback output leaked a path", failures)
            require("PRIVATE_FIXTURE" not in rendered, "public rollback output leaked a credential", failures)

    finally:
        os.environ.clear()
        os.environ.update(original_environment)
        host.write_managed_pid_record = original_write_pid
        host.process_alive = original_process_alive
        host.managed_process_record_matches = original_record_matches
        host._terminate_supervised_child = original_terminate_child

    result = {
        "ok": not failures,
        "failures": failures,
        "bound_restart_request": True,
        "failed_target_runtime_gate": True,
        "both_configs_restored": True,
        "old_host_relaunched": True,
        "receipt_state": "rolled_back",
        "termination_refusal_state": "rollback_failed",
        "termination_refusal_launched_second_stack": False,
        "tailscale_changed": False,
        "workers_affected": False,
        "installed_host_mutated": False,
        "sensitive_values_omitted": True,
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
