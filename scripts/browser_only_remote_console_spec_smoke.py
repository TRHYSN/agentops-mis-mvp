#!/usr/bin/env python3
"""Keep the ordinary Remote Console product path browser-only and Relay-based."""
from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "docs" / "LOCAL_HOST_REMOTE_CONSOLE_SPEC.md"
PLAN = ROOT / "docs" / "LOCAL_HOST_REMOTE_CONSOLE_DELIVERY_PLAN.md"
DECISION = ROOT / "docs" / "BROWSER_ONLY_REMOTE_CONSOLE_TRANSPORT_DECISION.md"
TAILSCALE_ACCEPTANCE = ROOT / "docs" / "PRIVATE_HOST_SECOND_DEVICE_ACCEPTANCE.md"
BROWSER_RELAY_ACCEPTANCE = ROOT / "docs" / "PRIVATE_HOST_BROWSER_RELAY_ACCEPTANCE.md"
RC = ROOT / "docs" / "PRIVATE_HOST_RC_ACCEPTANCE.md"
RUNBOOK = ROOT / "docs" / "PRIVATE_HOST_OPERATOR_RUNBOOK.md"


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def main() -> int:
    failures: list[str] = []
    spec = SPEC.read_text(encoding="utf-8")
    plan = PLAN.read_text(encoding="utf-8")
    decision = DECISION.read_text(encoding="utf-8")
    tailscale = TAILSCALE_ACCEPTANCE.read_text(encoding="utf-8")
    browser_acceptance = BROWSER_RELAY_ACCEPTANCE.read_text(encoding="utf-8")
    rc = RC.read_text(encoding="utf-8")
    runbook = RUNBOOK.read_text(encoding="utf-8")
    combined = " ".join((spec + plan + decision + browser_acceptance).split()).lower()
    normalized_tailscale = " ".join(tailscale.split())

    require(
        "browser-only transport amendment accepted" in spec,
        "authoritative spec does not accept the browser-only amendment",
        failures,
    )
    require(
        "Recommended product path: outbound AgentOps Relay" in spec,
        "outbound Relay is not the recommended product transport",
        failures,
    )
    require(
        "Advanced path: Tailscale Serve" in spec,
        "Tailscale must remain available as an advanced profile",
        failures,
    )
    require(
        "no Tailscale, VPN" in plan,
        "physical browser-only acceptance does not explicitly exclude a VPN client",
        failures,
    )
    require(
        all(label in plan for label in (
            "3A Pairing",
            "3B Transport contract",
            "3C Relay delivery",
            "3D Physical acceptance",
        )),
        "delivery plan is missing a Relay implementation slice",
        failures,
    )
    require(
        "The Relay is transport, not MIS authority." in decision,
        "Relay authority boundary is missing",
        failures,
    )
    require(
        "host-side tls" in combined and "l4" in combined and "sni" in combined,
        "Host-terminated TLS/L4 Relay content-confidentiality requirement is missing",
        failures,
    )
    require(
        "does not serve Workspace JavaScript" in decision
        and "private key never leaves the Host" in decision,
        "Relay executable-content or Host-key boundary is missing",
        failures,
    )
    require(
        "Status: advanced Tailscale physical browser workflow partially accepted" in tailscale
        and "ordinary browser-only Relay protocol pending" in tailscale,
        "legacy second-device protocol is not labeled as advanced fallback",
        failures,
    )
    require(
        "cannot close the browser-only Console gate" in normalized_tailscale,
        "legacy Tailscale acceptance can still be mistaken for ordinary onboarding proof",
        failures,
    )
    require(
        "public quick tunnel" in combined and "cannot substitute" in combined,
        "unsafe shortcut anti-substitution boundary is missing",
        failures,
    )
    require(
        "no Tailscale/VPN client" in browser_acceptance,
        "mandatory physical protocol does not prove a browser-only Console",
        failures,
    )
    require(
        "ordinary Relay evidence required" in rc
        and "advanced Tailscale HTTPS profile" in rc,
        "RC gates still treat Tailscale as the ordinary transport",
        failures,
    )
    require(
        "普通用户默认通过 AgentOps Relay" in runbook
        and "高级模式：Tailscale Serve" in runbook,
        "operator runbook still presents Tailscale as ordinary onboarding",
        failures,
    )

    output = {
        "operation": "browser_only_remote_console_spec_smoke",
        "ok": not failures,
        "ordinary_console_dependency": "modern_browser_only",
        "default_transport": "outbound_relay",
        "advanced_transport": "tailscale_serve",
        "relay_is_authority": False,
        "physical_browser_only_acceptance_pending": True,
        "failures": failures,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
