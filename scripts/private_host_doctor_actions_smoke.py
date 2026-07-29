#!/usr/bin/env python3
"""Verify Private Host doctor reports only actions that are currently needed."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agentops_mis_cli.host import host_doctor_next_actions


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def main() -> int:
    failures: list[str] = []
    ready_gates = [
        {"id": "config_private", "ok": True},
        {"id": "secrets_private", "ok": True},
        {"id": "database_parent_private", "ok": True},
        {"id": "production_ui", "ok": True},
        {"id": "stack_entrypoint", "ok": True},
        {"id": "relay_connector_safe_default", "ok": True},
    ]
    ready_tailscale = {
        "installed": True,
        "backend_state": "Running",
        "dns_name": "host.example.ts.net",
    }
    ready_human = {"status": "ready"}

    ready = host_doctor_next_actions(
        gates=ready_gates,
        tailscale=ready_tailscale,
        human_access=ready_human,
        base_url="http://127.0.0.1:18878",
        ui_dist_managed=True,
    )
    require(ready == [], f"healthy Host still advertised setup actions: {ready}", failures)

    missing_ui = host_doctor_next_actions(
        gates=[
            {**gate, "ok": False} if gate["id"] == "production_ui" else gate
            for gate in ready_gates
        ],
        tailscale=ready_tailscale,
        human_access=ready_human,
        base_url="http://127.0.0.1:18878",
        ui_dist_managed=False,
    )
    require(
        len(missing_ui) == 1 and "--build-ui" in missing_ui[0],
        f"missing UI did not produce one bounded build action: {missing_ui}",
        failures,
    )

    managed_missing_ui = host_doctor_next_actions(
        gates=[
            {**gate, "ok": False} if gate["id"] == "production_ui" else gate
            for gate in ready_gates
        ],
        tailscale=ready_tailscale,
        human_access=ready_human,
        base_url="http://127.0.0.1:18878",
        ui_dist_managed=True,
    )
    require(
        len(managed_missing_ui) == 1
        and "reinstall" in managed_missing_ui[0].lower()
        and "--build-ui" not in managed_missing_ui[0],
        f"managed UI failure advertised a source build: {managed_missing_ui}",
        failures,
    )
    require(
        not any("install" in action.lower() and "tailscale" in action.lower() for action in missing_ui),
        f"ready Tailscale still produced an install action: {missing_ui}",
        failures,
    )

    missing_tailscale = host_doctor_next_actions(
        gates=ready_gates,
        tailscale={"installed": False, "backend_state": "unavailable", "dns_name": ""},
        human_access=ready_human,
        base_url="http://127.0.0.1:18878",
        ui_dist_managed=True,
    )
    require(
        len(missing_tailscale) == 1
        and missing_tailscale[0].startswith("Optional private Console:"),
        f"missing Tailscale guidance was not optional and bounded: {missing_tailscale}",
        failures,
    )

    stopped_tailscale = host_doctor_next_actions(
        gates=ready_gates,
        tailscale={"installed": True, "backend_state": "Stopped", "dns_name": ""},
        human_access=ready_human,
        base_url="http://127.0.0.1:18878",
        ui_dist_managed=True,
    )
    require(
        len(stopped_tailscale) == 1
        and "open Tailscale" in stopped_tailscale[0],
        f"stopped Tailscale guidance was incorrect: {stopped_tailscale}",
        failures,
    )

    bootstrap = host_doctor_next_actions(
        gates=ready_gates,
        tailscale=ready_tailscale,
        human_access={"status": "bootstrap_required"},
        base_url="http://127.0.0.1:18878",
        ui_dist_managed=True,
    )
    require(
        len(bootstrap) == 2
        and bootstrap[0].startswith("Open http://127.0.0.1:18878/workspace")
        and "bootstrap-owner" in bootstrap[1],
        f"Owner bootstrap actions were not preserved: {bootstrap}",
        failures,
    )

    print(json.dumps({
        "ok": not failures,
        "operation": "private_host_doctor_actions_smoke",
        "healthy_next_actions": len(ready),
        "missing_ui_action_count": len(missing_ui),
        "managed_ui_repair_action_count": len(managed_missing_ui),
        "optional_tailscale_guidance": len(missing_tailscale) == 1,
        "owner_bootstrap_actions": len(bootstrap),
        "failures": failures,
        "credentials_read": False,
        "network_changed": False,
        "ledger_mutated": False,
        "token_omitted": True,
    }, ensure_ascii=True, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
