#!/usr/bin/env python3
"""Verify the Owner Relay control stays inside the existing Workspace UI."""
from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ACCOUNT = ROOT / "ui" / "start-building-app" / "src" / "app" / "components" / "pages" / "AccountSecurity.tsx"
API = ROOT / "ui" / "start-building-app" / "src" / "app" / "data" / "liveApi.ts"


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def main() -> int:
    failures: list[str] = []
    account = ACCOUNT.read_text(encoding="utf-8")
    api = API.read_text(encoding="utf-8")

    require('testId="host-relay-section"' in account, "Remote Console is not in Account Security", failures)
    require(
        'const canManageRelay = required && user?.role === "owner"' in account,
        "Relay controls are not visibly Owner-only",
        failures,
    )
    require(
        'data-testid="host-relay-prepare"' in account and 'data-testid="host-relay-confirm"' in account,
        "two-stage Relay controls are missing",
        failures,
    )
    require(
        "setRelayTransition({ action: relayAction, ref: transition.transition_ref })" in account
        and "confirmHostRelayTransition(relayTransition.ref, relayTransition.action)" in account,
        "prepare and exact confirmation are not connected",
        failures,
    )
    require(
        "localStorage" not in account and "sessionStorage" not in account,
        "Relay transition state is persisted outside React memory",
        failures,
    )
    require(
        'relayEnabled: "已启用"' in account
        and 'relayRestartScheduled: "正在安全重启主机服务"' in account
        and 'relayManualRestartRequired: "需要手动重启主机服务"' in account
        and 'relayRestartRequired: "需要重启主机服务"' in account
        and 'relayRolledBack: "新配置启动失败，已恢复原配置"' in account
        and 'relayRollbackFailed: "自动恢复失败，需要本机处理"' in account
        and 'relayEnabled: "Enabled"' in account
        and 'relayRestartScheduled: "Safely restarting the Host service"' in account
        and 'relayManualRestartRequired: "Manual Host service restart required"' in account
        and 'relayRestartRequired: "Host service restart required"' in account,
        "Relay state copy is not bilingual",
        failures,
    )
    require(
        'rawState === "restart_scheduled"' in api
        and 'rawState === "manual_restart_required"' in api
        and 'rawState === "rolled_back"' in api
        and 'rawState === "rollback_failed"' in api,
        "managed restart states are not normalized",
        failures,
    )
    require(
        '"/host/relay"' in api
        and '"/host/relay/transitions"' in api
        and "/host/relay/transitions/${encodeURIComponent(transitionRef)}/confirm" in api,
        "Relay HTTP routes are not wired",
        failures,
    )
    require(
        "if (!nextStatus.restart_required) await refreshRelay();" in account,
        "restart-required result is immediately overwritten by a refresh",
        failures,
    )
    forbidden = (
        "host_certificate_path",
        "host_private_key_path",
        "relay_ca_path",
        "tunnel_key_hex",
        "material_digest",
    )
    require(
        not any(value in account or value in api for value in forbidden),
        "frontend contract references private Relay material",
        failures,
    )

    print(
        json.dumps(
            {
                "ok": not failures,
                "operation": "private_host_relay_owner_control_ui_smoke",
                "route": "/workspace/account",
                "checks": 10,
                "owner_only": True,
                "transition_ref_storage": "react_memory_only",
                "private_material_rendered": False,
                "failures": failures,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
