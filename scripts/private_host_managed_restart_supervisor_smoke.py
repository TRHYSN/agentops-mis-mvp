#!/usr/bin/env python3
"""Deterministically exercise the exact LaunchAgent Host restart supervisor."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agentops_mis_cli import host


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def wait_for(predicate, *, timeout: float = 2) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


class FakeChild:
    def __init__(self, pid: int, *, stubborn: bool) -> None:
        self.pid = pid
        self.stubborn = stubborn
        self.returncode = None
        self.terminate_calls = 0
        self.kill_calls = 0

    def poll(self):
        return self.returncode

    def terminate(self) -> None:
        self.terminate_calls += 1
        if not self.stubborn:
            self.returncode = -15

    def kill(self) -> None:
        self.kill_calls += 1
        self.returncode = -9

    def wait(self, timeout=None):
        if self.returncode is None:
            raise subprocess.TimeoutExpired("fake-stack", timeout)
        return self.returncode


def main() -> int:
    failures: list[str] = []
    public_results: list[dict] = []
    secret_marker = "agthost_PRIVATE_SMOKE_SECRET"
    original_environment = os.environ.copy()
    original_write_pid = host.write_managed_pid_record
    original_process_alive = host.process_alive
    original_record_matches = host.managed_process_record_matches
    try:
        peer_left, peer_right = socket.socketpair()
        try:
            kernel_peer_pid = host._unix_peer_pid(peer_left)
            exact_backend_peer = host._managed_restart_peer_authorized(
                peer_left,
                42000,
                peer_pid_reader=lambda _connection: 42001,
                backend_checker=lambda pid, parent: pid == 42001 and parent == 42000,
            )
            unrelated_peer = host._managed_restart_peer_authorized(
                peer_left,
                42000,
                peer_pid_reader=lambda _connection: 42002,
                backend_checker=lambda pid, parent: pid == 42001 and parent == 42000,
            )
            require(kernel_peer_pid == os.getpid(), "kernel peer PID was unavailable", failures)
            require(exact_backend_peer is True, "exact backend peer was rejected", failures)
            require(unrelated_peer is False, "unrelated same-UID peer was accepted", failures)
        finally:
            peer_left.close()
            peer_right.close()

        with tempfile.TemporaryDirectory(prefix="agentops-managed-restart-") as temporary:
            temp = Path(temporary)
            host_home = temp / "host"
            service_path = temp / "dev.agentops.mis.private-host.plist"
            os.environ.update({
                "AGENTOPS_HOST_HOME": str(host_home),
                "AGENTOPS_INSTALL_ROOT": str(ROOT),
                "PYTHONPATH": str(ROOT),
            })
            service_path.write_bytes(host.host_service_template())
            service_path.chmod(0o600)
            loaded_state = {"loaded": True, "label": host.HOST_SERVICE_LABEL}
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

            exact_gate = host._managed_launch_agent_gate(
                args,
                service_path=service_path,
                parent_pid=1,
                launchd_state=loaded_state,
            )
            require(exact_gate["ok"] is True, f"exact LaunchAgent gate failed: {exact_gate}", failures)
            manual_args = SimpleNamespace(**{**vars(args), "managed_launch_agent": False})
            manual_gate = host._managed_launch_agent_gate(
                manual_args,
                service_path=service_path,
                parent_pid=1,
                launchd_state=loaded_state,
            )
            require(manual_gate["ok"] is False, "manual --foreground gained managed restart authority", failures)
            wrong_parent = host._managed_launch_agent_gate(
                args,
                service_path=service_path,
                parent_pid=99,
                launchd_state=loaded_state,
            )
            require(wrong_parent["ok"] is False, "non-launchd parent passed the managed gate", failures)
            service_path.write_bytes(host.host_service_template() + b"\n")
            service_path.chmod(0o600)
            wrong_template = host._managed_launch_agent_gate(
                args,
                service_path=service_path,
                parent_pid=1,
                launchd_state=loaded_state,
            )
            require(wrong_template["ok"] is False, "non-exact service template passed the managed gate", failures)
            service_path.write_bytes(host.host_service_template())
            service_path.chmod(0o600)

            manual_request = host.managed_host_restart_status(
                service_path=service_path,
                launchd_state=loaded_state,
            )
            public_results.append(manual_request)
            require(
                manual_request.get("error") == "managed_launch_agent_required",
                f"manual foreground request did not fail closed: {manual_request}",
                failures,
            )

            unsafe_root = temp / "unsafe-run-directories"
            unsafe_root.mkdir(mode=0o700)

            def require_unsafe_run_rejected(socket_path: Path, label: str) -> None:
                try:
                    unsafe_listener = host._open_managed_restart_socket(socket_path)
                except RuntimeError:
                    return
                unsafe_listener.close()
                host._remove_managed_restart_socket(socket_path)
                failures.append(f"{label} run directory was accepted")

            symlink_target = unsafe_root / "symlink-target"
            symlink_target.mkdir(mode=0o700)
            symlink_run = unsafe_root / "symlink-run"
            symlink_run.symlink_to(symlink_target, target_is_directory=True)
            require_unsafe_run_rejected(symlink_run / "restart.sock", "symlink")

            nondirectory_run = unsafe_root / "not-a-directory"
            nondirectory_run.write_text("sentinel", encoding="utf-8")
            require_unsafe_run_rejected(nondirectory_run / "restart.sock", "non-directory")
            require(nondirectory_run.read_text(encoding="utf-8") == "sentinel", "non-directory sentinel changed", failures)

            wrong_mode_run = unsafe_root / "wrong-mode"
            wrong_mode_run.mkdir(mode=0o700)
            wrong_mode_run.chmod(0o755)
            require_unsafe_run_rejected(wrong_mode_run / "restart.sock", "wrong-mode")
            require(wrong_mode_run.stat().st_mode & 0o777 == 0o755, "unsafe run mode was silently repaired", failures)

            wrong_uid_run = unsafe_root / "wrong-uid"
            wrong_uid_run.mkdir(mode=0o700)
            with mock.patch.object(host.os, "getuid", return_value=os.getuid() + 1):
                require_unsafe_run_rejected(wrong_uid_run / "restart.sock", "wrong-uid")

            p = host.paths()
            p["run"].mkdir(parents=True, mode=0o700)
            p["home"].chmod(0o700)
            p["run"].chmod(0o700)
            listener = host._open_managed_restart_socket(p["restart_socket"])
            require(host._private_managed_restart_socket(p["restart_socket"]), "restart socket was not mode 0600", failures)
            children: list[FakeChild] = []
            launch_environments: list[dict[str, str]] = []

            def fake_popen(_command, **kwargs):
                child = FakeChild(41001 + len(children), stubborn=not children)
                children.append(child)
                launch_environments.append(kwargs["env"])
                ready_fd = kwargs["pass_fds"][0]
                os.write(ready_fd, b"\x01")
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

            host.write_managed_pid_record = fake_write_pid
            host.process_alive = lambda pid: bool(
                pid == os.getpid()
                or any(child.pid == pid and child.poll() is None for child in children)
            )
            host.managed_process_record_matches = lambda record, pid: int(record.get("pid") or 0) == pid
            config = {
                "host": "127.0.0.1",
                "port": 18997,
                "database_path": str(host_home / "data" / "agentops.db"),
                "ui_dist": str(ROOT / "ui" / "start-building-app" / "dist"),
                "allowed_origins": ["http://127.0.0.1:18997"],
                "workspace_id": "managed-restart-smoke",
                "cookie_secure": False,
            }
            secrets = {
                "api_key": secret_marker,
                "admin_key": "agtadmin_PRIVATE_SMOKE_SECRET",
                "owner_setup_code": "PRIVATE_SMOKE_SETUP_CODE",
            }
            replacement_origin = "https://transition.example.invalid"
            replacement_secret_marker = "agthost_REPLACEMENT_SMOKE_SECRET"
            transition_ref = "rst_supervisor_smoke_01"
            p["relay"].mkdir(parents=True, mode=0o700)
            p["relay"].chmod(0o700)
            active_original = b'{"enabled":false,"schema_version":1}\n'
            active_target = b'{"enabled":true,"schema_version":1}\n'
            host_original = (json.dumps(config, sort_keys=True) + "\n").encode("utf-8")
            host_target_config = {
                **config,
                "allowed_origins": [replacement_origin],
                "cookie_secure": True,
            }
            host_target = (json.dumps(host_target_config, sort_keys=True) + "\n").encode("utf-8")
            p["relay_config"].write_bytes(active_original)
            p["relay_config"].chmod(0o600)
            p["config"].write_bytes(host_original)
            p["config"].chmod(0o600)
            restart_receipt = host.relay_restart.create_restart_receipt(
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
            host.relay_restart.apply_target_configs(
                receipt_path=p["relay_restart_receipt"],
                sequence_path=p["relay_restart_sequence"],
                action="enable",
                transition_ref=transition_ref,
                transaction_sequence=int(restart_receipt["transaction_sequence"]),
                expected_revision=int(restart_receipt["revision"]),
            )
            restart_receipt = host.relay_restart.transition_restart_receipt(
                receipt_path=p["relay_restart_receipt"],
                sequence_path=p["relay_restart_sequence"],
                action="enable",
                transition_ref=transition_ref,
                transaction_sequence=int(restart_receipt["transaction_sequence"]),
                expected_revision=int(restart_receipt["revision"]),
                state="response_flushed",
            )
            request_context = {
                "action": "enable",
                "transition_ref": transition_ref,
                "transaction_sequence": int(restart_receipt["transaction_sequence"]),
                "expected_revision": int(restart_receipt["revision"]),
            }
            config_loads = 0

            def load_replacement_config():
                nonlocal config_loads
                config_loads += 1
                replacement_config = {
                    **config,
                    "allowed_origins": [replacement_origin],
                    "cookie_secure": True,
                }
                replacement_secrets = {**secrets, "api_key": replacement_secret_marker}
                return replacement_config, replacement_secrets

            supervisor_result: list[int] = []
            supervisor_error: list[str] = []

            def supervise() -> None:
                try:
                    supervisor_result.append(host._run_managed_foreground_supervisor(
                        args,
                        config,
                        secrets,
                        p,
                        listener,
                        exact_gate["template_hash"],
                        startup_timeout=0.25,
                        stop_timeout=0.05,
                        popen_factory=fake_popen,
                        health_check=lambda _url: {"reachable": True, "status": "ok"},
                        config_loader=load_replacement_config,
                        relay_runtime_validator=lambda _action, _paths: True,
                        peer_authorizer=lambda _connection, _parent_pid: True,
                        install_signal_handlers=False,
                    ))
                except Exception as exc:
                    supervisor_error.append(f"{type(exc).__name__}: {exc}")

            thread = threading.Thread(target=supervise, daemon=True)
            thread.start()
            require(wait_for(lambda: p["service_instance"].is_file()), "service instance record was not created", failures)
            instance = host._read_private_bounded_json(p["service_instance"])
            require(instance is not None, "service instance record was not private/readable", failures)
            require(
                instance is not None and set(instance) == {
                    "schema_version", "supervisor_pid", "stack_child_pid", "label", "template_hash"
                },
                f"service instance shape was not exact: {instance}",
                failures,
            )
            require(
                p["service_instance"].stat().st_mode & 0o777 == 0o600,
                "service instance record mode was not 0600",
                failures,
            )
            require(
                secret_marker not in json.dumps(instance) and str(temp) not in json.dumps(instance),
                "service instance record contained secret or path material",
                failures,
            )

            wrong_instance = dict(instance or {})
            wrong_instance["template_hash"] = "0" * 64
            host.write_private_json(p["service_instance"], wrong_instance)
            rejected = host.request_managed_host_restart(
                **request_context,
                service_path=service_path,
                launchd_state=loaded_state,
                _caller_parent_pid_override=int((instance or {}).get("stack_child_pid") or 0),
            )
            public_results.append(rejected)
            require(rejected.get("ok") is False and len(children) == 1, "wrong instance was allowed to restart", failures)
            host.write_private_json(p["service_instance"], instance or {})

            unrelated_caller = host.request_managed_host_restart(
                **request_context,
                service_path=service_path,
                launchd_state=loaded_state,
            )
            public_results.append(unrelated_caller)
            require(
                unrelated_caller.get("ok") is False and len(children) == 1,
                "unrelated same-user caller was allowed to request restart",
                failures,
            )

            requested = host.request_managed_host_restart(
                **request_context,
                service_path=service_path,
                launchd_state=loaded_state,
                _caller_parent_pid_override=int((instance or {}).get("stack_child_pid") or 0),
            )
            public_results.append(requested)
            require(requested.get("accepted") is True, f"exact managed request was rejected: {requested}", failures)
            require(wait_for(lambda: len(children) == 2), "one request did not produce one replacement child", failures)
            require(len(children) == 2, f"one request produced {len(children) - 1} restarts", failures)
            require(config_loads == 1, f"replacement config was loaded {config_loads} times", failures)
            require(
                len(launch_environments) == 2
                and launch_environments[0].get("AGENTOPS_ALLOWED_ORIGINS") == "http://127.0.0.1:18997"
                and launch_environments[0].get("AGENTOPS_COOKIE_SECURE") == "false"
                and launch_environments[1].get("AGENTOPS_ALLOWED_ORIGINS") == replacement_origin
                and launch_environments[1].get("AGENTOPS_COOKIE_SECURE") == "true"
                and launch_environments[1].get("AGENTOPS_API_KEY") == replacement_secret_marker,
                "replacement child did not receive reloaded origin/cookie/secret policy",
                failures,
            )
            require(
                children[0].terminate_calls == 1 and children[0].kill_calls == 1,
                "bounded cleanup did not escalate only the exact stubborn child",
                failures,
            )
            if len(children) == 2:
                children[1].returncode = 0
            thread.join(timeout=2)
            require(not thread.is_alive(), "supervisor cleanup exceeded its bound", failures)
            require(not supervisor_error, f"supervisor raised: {supervisor_error}", failures)
            require(supervisor_result == [0], f"supervisor returned unexpected status: {supervisor_result}", failures)
            require(not p["service_instance"].exists(), "service instance record survived supervisor cleanup", failures)
            require(not p["restart_socket"].exists(), "restart socket survived supervisor cleanup", failures)

            public_text = json.dumps(public_results, sort_keys=True)
            require(secret_marker not in public_text, "public restart result leaked token material", failures)
            require(replacement_secret_marker not in public_text, "public restart result leaked replacement token material", failures)
            require(str(temp) not in public_text, "public restart result leaked a private path", failures)
    finally:
        os.environ.clear()
        os.environ.update(original_environment)
        host.write_managed_pid_record = original_write_pid
        host.process_alive = original_process_alive
        host.managed_process_record_matches = original_record_matches

    result = {
        "ok": not failures,
        "failures": failures,
        "exact_service_gating": True,
        "manual_foreground_rejected": True,
        "one_restart_per_request": True,
        "bounded_exact_child_cleanup": True,
        "replacement_config_reloaded": True,
        "caller_parent_bound": True,
        "kernel_peer_identity_bound": True,
        "unrelated_same_uid_peer_rejected": True,
        "unsafe_run_directories_rejected": True,
        "private_record_mode": "0600",
        "public_results_redacted": True,
        "rollback_integration": False,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
