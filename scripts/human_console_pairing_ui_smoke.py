#!/usr/bin/env python3
"""Verify the compact Workspace pairing UI and secret-handling contract."""
from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUTH = ROOT / "ui" / "start-building-app" / "src" / "app" / "components" / "auth" / "AuthGate.tsx"
ACCOUNT = ROOT / "ui" / "start-building-app" / "src" / "app" / "components" / "pages" / "AccountSecurity.tsx"
API = ROOT / "ui" / "start-building-app" / "src" / "app" / "data" / "liveApi.ts"


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def main() -> int:
    failures: list[str] = []
    auth = AUTH.read_text(encoding="utf-8")
    account = ACCOUNT.read_text(encoding="utf-8")
    api = API.read_text(encoding="utf-8")

    require('params.get("pair")' in auth, "AuthGate does not consume #pair", failures)
    require(
        "window.history.replaceState" in auth and "window.location.hash.slice(1)" in auth,
        "pairing fragment is not scrubbed immediately",
        failures,
    )
    require(
        "useState(initialPairingHandoff.value)" in auth and 'type GateState = "checking"' in auth and '"pairing"' in auth,
        "pairing secret is not held in component memory/state",
        failures,
    )
    require(
        "localStorage" not in auth and "sessionStorage" not in auth,
        "AuthGate persists pairing state in browser storage",
        failures,
    )
    require(
        "{pairingSecret}" not in auth and "value={pairingSecret}" not in auth,
        "pairing secret is rendered into the DOM",
        failures,
    )
    require(
        "pairHuman({" in auth and "pairing_secret: pairingSecret" in auth,
        "pairing form is not connected to the bounded API",
        failures,
    )
    require(
        'zh: isBootstrap ? "设置管理员账户"' in auth
        and 'isPairing ? "设置成员账户"' in auth
        and 'isPairing ? "Set up member account"' in auth,
        "pairing form is not bilingual inside the existing account shell",
        failures,
    )
    require(
        "too_many_attempts" in auth and "尝试次数过多" in auth and "Too many attempts" in auth,
        "bounded authentication throttling error is not bilingual",
        failures,
    )
    require(
        "一次性邀请授权另一台电脑的浏览器" in auth
        and "one-time invitation" in auth
        and "私人网络浏览器" not in auth
        and "authorized private network" not in auth,
        "bootstrap copy still implies that the default second-device path requires a private network client",
        failures,
    )
    require(
        'testId="human-pairing-section"' in account
        and 'data-testid="pairing-invitation-form"' in account
        and 'data-testid="paired-device-row"' in account,
        "Owner pairing/device controls are missing from Account Security",
        failures,
    )
    require(
        "createHumanPairingInvitation" in account
        and "revokeHumanPairingInvitation" in account
        and "revokeHumanPairedDevice" in account,
        "Owner controls are not wired to invitation/device APIs",
        failures,
    )
    require(
        "navigator.clipboard.writeText" in account and "#pair=" in account,
        "Owner cannot copy the one-time fragment link",
        failures,
    )
    require(
        "{createdInvitation.pairing_secret}" not in account,
        "Account Security renders the raw pairing secret",
        failures,
    )
    for route in (
        '"/human-auth/pairing-invitations"',
        '"/human-auth/devices"',
        '"/human-auth/pair"',
    ):
        require(route in api, f"frontend API contract missing {route}", failures)
    require(
        "HumanAuthRequestError" in api and "boundedHumanAuthErrorCode" in api,
        "pairing API errors are not reduced to bounded codes",
        failures,
    )

    output = {
        "operation": "human_console_pairing_ui_smoke",
        "ok": not failures,
        "checks": 17,
        "route": "/workspace/account",
        "pairing_fragment": "immediately_scrubbed_memory_only",
        "existing_workspace_shell": True,
        "failures": failures,
        "safety": {
            "static_only": True,
            "secret_value_read": False,
            "browser_storage_used": False,
            "live_runtime_called": False,
        },
    }
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
