#!/usr/bin/env python3
"""Verify the Host CLI preserves explicit prepare/confirm Relay semantics."""
from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agentops_mis_cli import host  # noqa: E402


def invoke(argv: list[str], *, host_paths: dict[str, Path], confirm_result: dict | None = None):
    prepare_result = {
        "ok": True,
        "operation": "relay_transition_prepare",
        "state": "prepared",
        "transition_ref": "fixture-safe-ref",
        "confirmation_required": True,
        "restart_required": False,
    }
    execute_result = {
        "ok": True,
        "operation": "relay_transition_execute",
        "state": "executed",
        "transition_ref": "fixture-safe-ref",
        "confirmation_required": False,
        "restart_required": True,
    }
    output = io.StringIO()
    with (
        mock.patch.object(host, "require_initialized", return_value=({}, {})),
        mock.patch.object(host, "paths", return_value=host_paths),
        mock.patch.object(host, "lifecycle_lock", side_effect=lambda: contextlib.nullcontext()),
        mock.patch.object(host.relay_control, "prepare_relay_transition", return_value=prepare_result) as prepare,
        mock.patch.object(
            host.relay_control,
            "confirm_relay_transition",
            return_value=confirm_result or {
                "ok": True,
                "operation": "relay_transition_confirm",
                "state": "confirmed",
            },
        ) as confirm,
        mock.patch.object(host.relay_control, "execute_confirmed_relay_transition", return_value=execute_result) as execute,
        contextlib.redirect_stdout(output),
    ):
        code = host.main(argv)
    return code, json.loads(output.getvalue()), prepare, confirm, execute


def main() -> int:
    failures: list[str] = []
    evidence: dict[str, bool] = {}
    with tempfile.TemporaryDirectory(prefix="agentops-relay-cli-control-") as temporary:
        root = Path(temporary)
        host_paths = {
            "config": root / "config.json",
            "relay_transition": root / "relay" / "transition.json",
            "relay_config": root / "relay" / "config.json",
            "relay_prepared": root / "relay" / "prepared.json",
            "relay_secrets": root / "relay" / "secrets.json",
            "relay_restart_receipt": root / "relay" / "restart-receipt.json",
            "relay_restart_sequence": root / "relay" / "restart-sequence.json",
        }

        code, payload, prepare, confirm, execute = invoke(
            ["relay-transition", "--action", "enable"],
            host_paths=host_paths,
        )
        evidence["prepare_only"] = bool(
            code == 0
            and payload.get("operation") == "relay_transition_prepare"
            and prepare.call_count == 1
            and confirm.call_count == 0
            and execute.call_count == 0
        )

        code, payload, prepare, confirm, execute = invoke(
            ["relay-transition", "--action", "enable", "--confirm-ref", "fixture-safe-ref"],
            host_paths=host_paths,
        )
        evidence["explicit_confirm_executes_once"] = bool(
            code == 0
            and payload.get("operation") == "relay_transition_execute"
            and prepare.call_count == 0
            and confirm.call_count == 1
            and execute.call_count == 1
            and confirm.call_args.kwargs.get("transition_ref") == "fixture-safe-ref"
            and execute.call_args.kwargs.get("transition_ref") == "fixture-safe-ref"
            and execute.call_args.kwargs.get("restart_receipt_path") == host_paths["relay_restart_receipt"]
            and execute.call_args.kwargs.get("restart_sequence_path") == host_paths["relay_restart_sequence"]
        )

        code, payload, prepare, confirm, execute = invoke(
            ["relay-transition", "--action", "disable", "--confirm-ref", "fixture-safe-ref"],
            host_paths=host_paths,
            confirm_result={"ok": False, "operation": "relay_transition_confirm", "error": "transition_expired"},
        )
        evidence["failed_confirm_never_executes"] = bool(
            code == 1
            and payload.get("error") == "transition_expired"
            and prepare.call_count == 0
            and confirm.call_count == 1
            and execute.call_count == 0
        )

    for name, passed in evidence.items():
        if not passed:
            failures.append(f"{name} failed")
    print(
        json.dumps(
            {
                "ok": not failures,
                "operation": "private_host_relay_cli_control_smoke",
                "checks": len(evidence),
                "evidence": evidence,
                "external_state_mutated": False,
                "failures": failures,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
