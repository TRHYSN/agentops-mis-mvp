#!/usr/bin/env python3
"""Prove restart actions can run only after an accepted JSON response flushes."""
from __future__ import annotations

import json
import io
import os
import sys
import tempfile
from contextlib import redirect_stderr
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agentops_mis_cli import relay_restart  # noqa: E402
import server as server_module  # noqa: E402
from server import (  # noqa: E402
    Handler,
    private_host_relay_after_send,
    private_host_relay_public_payload,
    private_host_relay_send_error,
)


class OrderedWriter:
    def __init__(self, events: list[str], *, fail_write: bool = False) -> None:
        self.events = events
        self.fail_write = fail_write
        self.payload = b""

    def write(self, payload: bytes) -> None:
        self.events.append("body_write")
        if self.fail_write:
            raise BrokenPipeError("fixture_write_failed")
        self.payload += payload

    def flush(self) -> None:
        self.events.append("body_flush")


class FakeHandler:
    send_json = Handler.send_json

    def __init__(self, events: list[str], *, fail_write: bool = False) -> None:
        self.events = events
        self.wfile = OrderedWriter(events, fail_write=fail_write)
        self.headers: list[tuple[str, str]] = []
        self.close_connection = False

    def send_response(self, status: int) -> None:
        self.events.append(f"status:{status}")

    def send_header(self, name: str, value: str) -> None:
        self.headers.append((name, value))

    def end_headers(self) -> None:
        self.events.append("headers_complete")


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def main() -> int:
    failures: list[str] = []
    success_events: list[str] = []
    success = FakeHandler(success_events)
    success.send_json(
        {"ok": True, "state": "restart_scheduled", "sensitive_values_omitted": True},
        202,
        after_send=lambda: success_events.append("restart_request"),
        on_send_error=lambda: success_events.append("rollback"),
    )
    require(
        success_events == [
            "status:202",
            "headers_complete",
            "body_write",
            "body_flush",
            "restart_request",
        ],
        f"post-response action order changed: {success_events}",
        failures,
    )
    require(success.close_connection is True, "accepted response did not close the connection", failures)
    require(("Connection", "close") in success.headers, "accepted response omitted Connection: close", failures)
    require(json.loads(success.wfile.payload).get("state") == "restart_scheduled", "response body was incomplete", failures)

    failure_events: list[str] = []
    broken = FakeHandler(failure_events, fail_write=True)
    try:
        broken.send_json(
            {"ok": True},
            202,
            after_send=lambda: failure_events.append("restart_request"),
            on_send_error=lambda: failure_events.append("rollback"),
        )
    except BrokenPipeError:
        pass
    else:
        failures.append("broken response did not propagate the write failure")
    require("rollback" in failure_events, "broken response did not invoke rollback", failures)
    require("restart_request" not in failure_events, "broken response requested a restart", failures)
    require("body_flush" not in failure_events, "broken response claimed a flush", failures)

    callback_events: list[str] = []
    callback_failure = FakeHandler(callback_events)
    stderr = io.StringIO()
    with redirect_stderr(stderr):
        callback_failure.send_json(
            {"ok": True},
            202,
            after_send=lambda: (_ for _ in ()).throw(RuntimeError("private_fixture_value")),
        )
    require(callback_failure.close_connection is True, "callback failure reopened the response", failures)
    require("post-response action failed" in stderr.getvalue(), "callback failure omitted bounded operator notice", failures)
    require("private_fixture_value" not in stderr.getvalue(), "callback failure leaked exception detail", failures)

    projected = private_host_relay_public_payload(
        {
            "ok": True,
            "state": "restart_scheduled",
            "action": "enable",
            "restart_required": True,
            "restart_pending": True,
            "rollback_armed": True,
            "private_receipt": {"original_config": "private_fixture_value"},
        }
    )
    require(projected.get("state") == "restart_scheduled", "restart state was flattened", failures)
    require(projected.get("restart_pending") is True, "restart pending flag was omitted", failures)
    require(projected.get("rollback_armed") is True, "rollback armed flag was omitted", failures)
    require(projected.get("remote_ready") is False, "restart response claimed remote readiness", failures)
    require(projected.get("tailscale_changed") is False, "restart response claimed a Tailscale mutation", failures)
    require(projected.get("workers_affected") is False, "restart response claimed Worker mutation", failures)
    require("private_receipt" not in projected, "private restart receipt entered the public response", failures)
    require("private_fixture_value" not in json.dumps(projected), "private restart material leaked", failures)

    original_host_home = os.environ.get("AGENTOPS_HOST_HOME")
    try:
        with tempfile.TemporaryDirectory(prefix="agentops-post-response-rollback-") as temporary:
            root = Path(temporary)
            host_home = root / "host"
            relay_home = host_home / "relay"
            host_home.mkdir(mode=0o700)
            relay_home.mkdir(mode=0o700)
            os.environ["AGENTOPS_HOST_HOME"] = str(host_home)
            active_path = relay_home / "config.json"
            host_path = host_home / "config.json"
            receipt_path = relay_home / "restart-receipt.json"
            sequence_path = relay_home / "restart-sequence.json"
            active_original = b'{"enabled":false,"schema_version":1}\n'
            active_target = b'{"enabled":true,"schema_version":1}\n'
            host_original = b'{"cookie_secure":false,"network_publication":"disabled"}\n'
            host_target = b'{"cookie_secure":true,"network_publication":"agentops_relay"}\n'
            active_path.write_bytes(active_original)
            active_path.chmod(0o600)
            host_path.write_bytes(host_original)
            host_path.chmod(0o600)
            transition_ref = "rst_broken_response_01"
            receipt = relay_restart.create_restart_receipt(
                receipt_path=receipt_path,
                sequence_path=sequence_path,
                action="enable",
                transition_ref=transition_ref,
                active_config_path=active_path,
                host_config_path=host_path,
                active_original_config=active_original,
                active_target_config=active_target,
                host_original_config=host_original,
                host_target_config=host_target,
            )
            relay_restart.apply_target_configs(
                receipt_path=receipt_path,
                sequence_path=sequence_path,
                action="enable",
                transition_ref=transition_ref,
                transaction_sequence=int(receipt["transaction_sequence"]),
                expected_revision=int(receipt["revision"]),
            )
            private_host_relay_send_error({
                "action": "enable",
                "transition_ref": transition_ref,
                "transaction_sequence": int(receipt["transaction_sequence"]),
                "expected_revision": int(receipt["revision"]),
                "receipt_path": receipt_path,
                "sequence_path": sequence_path,
            })
            rolled_back = relay_restart.public_restart_receipt(
                receipt_path=receipt_path,
                sequence_path=sequence_path,
            )
            require(rolled_back.get("state") == "rolled_back", "broken response receipt did not roll back", failures)
            require(active_path.read_bytes() == active_original, "broken response left target Relay config", failures)
            require(host_path.read_bytes() == host_original, "broken response left target Host config", failures)

            failed_ref = "rst_broken_response_02"
            failed_receipt = relay_restart.create_restart_receipt(
                receipt_path=receipt_path,
                sequence_path=sequence_path,
                action="enable",
                transition_ref=failed_ref,
                active_config_path=active_path,
                host_config_path=host_path,
                active_original_config=active_original,
                active_target_config=active_target,
                host_original_config=host_original,
                host_target_config=host_target,
                replace_terminal=True,
            )
            relay_restart.apply_target_configs(
                receipt_path=receipt_path,
                sequence_path=sequence_path,
                action="enable",
                transition_ref=failed_ref,
                transaction_sequence=int(failed_receipt["transaction_sequence"]),
                expected_revision=int(failed_receipt["revision"]),
            )
            real_restore_original_configs = relay_restart.restore_original_configs
            relay_restart.restore_original_configs = lambda **_kwargs: (_ for _ in ()).throw(
                relay_restart.RelayRestartError("config_pair_write_failed")
            )
            try:
                try:
                    private_host_relay_send_error({
                        "action": "enable",
                        "transition_ref": failed_ref,
                        "transaction_sequence": int(failed_receipt["transaction_sequence"]),
                        "expected_revision": int(failed_receipt["revision"]),
                        "receipt_path": receipt_path,
                        "sequence_path": sequence_path,
                    })
                except relay_restart.RelayRestartError:
                    pass
                else:
                    failures.append("failed rollback did not propagate its bounded failure")
            finally:
                relay_restart.restore_original_configs = real_restore_original_configs
            rollback_failed = relay_restart.public_restart_receipt(
                receipt_path=receipt_path,
                sequence_path=sequence_path,
            )
            require(
                rollback_failed.get("state") == "rollback_failed",
                "failed rollback did not enter the fail-closed terminal state",
                failures,
            )

            active_path.write_bytes(active_original)
            host_path.write_bytes(host_original)
            callback_ref = "rst_after_send_failure_01"
            callback_receipt = relay_restart.create_restart_receipt(
                receipt_path=receipt_path,
                sequence_path=sequence_path,
                action="enable",
                transition_ref=callback_ref,
                active_config_path=active_path,
                host_config_path=host_path,
                active_original_config=active_original,
                active_target_config=active_target,
                host_original_config=host_original,
                host_target_config=host_target,
                replace_terminal=True,
            )
            relay_restart.apply_target_configs(
                receipt_path=receipt_path,
                sequence_path=sequence_path,
                action="enable",
                transition_ref=callback_ref,
                transaction_sequence=int(callback_receipt["transaction_sequence"]),
                expected_revision=int(callback_receipt["revision"]),
            )
            real_restart_request = server_module.private_host_cli.request_managed_host_restart
            server_module.private_host_cli.request_managed_host_restart = lambda **_kwargs: (
                (_ for _ in ()).throw(RuntimeError("bounded callback failure"))
            )
            try:
                try:
                    private_host_relay_after_send({
                        "action": "enable",
                        "transition_ref": callback_ref,
                        "transaction_sequence": int(callback_receipt["transaction_sequence"]),
                        "expected_revision": int(callback_receipt["revision"]),
                        "receipt_path": receipt_path,
                        "sequence_path": sequence_path,
                        "restart_mode": "managed_launchagent",
                    })
                except RuntimeError:
                    pass
                else:
                    failures.append("post-flush callback failure was not propagated")
            finally:
                server_module.private_host_cli.request_managed_host_restart = real_restart_request
            callback_rolled_back = relay_restart.public_restart_receipt(
                receipt_path=receipt_path,
                sequence_path=sequence_path,
            )
            require(
                callback_rolled_back.get("state") == "rolled_back",
                "post-flush callback failure left a pending receipt",
                failures,
            )
            require(active_path.read_bytes() == active_original, "callback failure left target Relay config", failures)
            require(host_path.read_bytes() == host_original, "callback failure left target Host config", failures)
    finally:
        if original_host_home is None:
            os.environ.pop("AGENTOPS_HOST_HOME", None)
        else:
            os.environ["AGENTOPS_HOST_HOME"] = original_host_home

    result = {
        "ok": not failures,
        "failures": failures,
        "accepted_status": 202,
        "flush_precedes_restart_request": True,
        "broken_response_blocks_restart": True,
        "callback_failure_keeps_single_response": True,
        "real_broken_response_rollback": True,
        "failed_rollback_is_terminal": True,
        "post_flush_failure_compensated": True,
        "connection_close_for_restart": True,
        "bounded_restart_projection": True,
        "network_used": False,
        "installed_host_mutated": False,
        "sensitive_values_omitted": True,
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
