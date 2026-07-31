#!/usr/bin/env python3
"""Verify disabled-by-default Relay visibility in the private Host CLI."""
from __future__ import annotations

import contextlib
import io
import json
import os
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agentops_mis_cli import host


def invoke(*arguments: str) -> tuple[int, dict[str, Any]]:
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = host.main(list(arguments))
    payload = json.loads(output.getvalue())
    return code, payload


def write_private(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    path.chmod(0o600)


def main() -> int:
    failures: list[str] = []
    sentinel = "RELAY_PRIVATE_SENTINEL_MUST_NOT_RENDER"
    old_home = os.environ.get("AGENTOPS_HOST_HOME")

    with tempfile.TemporaryDirectory(prefix="agentops-host-relay-config-") as temporary:
        root = Path(temporary)
        host_home = root / "host"
        ui_dist = root / "ui"
        ui_dist.mkdir(mode=0o700)
        (ui_dist / "index.html").write_text("host ui\n", encoding="utf-8")
        os.environ["AGENTOPS_HOST_HOME"] = str(host_home)

        original_tailscale_state = host.tailscale_state
        original_tailscale_binary = host.tailscale_binary
        original_tailscale_serve_state = host.tailscale_serve_state
        host.tailscale_state = lambda: {
            "available": False,
            "backend_state": "Unavailable",
            "dns_name": "",
            "installation_source": "unavailable",
            "signed_in": False,
        }
        host.tailscale_binary = lambda: (None, "unavailable")
        host.tailscale_serve_state = lambda *_args: {
            "configured": False,
            "conflict": False,
            "status_available": False,
            "target_matches": False,
        }
        try:
            init_code, _init = invoke(
                "init",
                "--port",
                "18787",
                "--workspace-id",
                "relay-smoke",
                "--ui-dist",
                str(ui_dist),
            )
            p = host.paths()
            relay_config = p["relay_config"]
            if (
                init_code != 0
                or json.loads(relay_config.read_text(encoding="utf-8"))
                != {"enabled": False, "schema_version": 1}
                or stat.S_IMODE(p["relay"].stat().st_mode) != 0o700
                or stat.S_IMODE(relay_config.stat().st_mode) != 0o600
            ):
                failures.append("new Host did not create the exact private disabled Relay config")

            status_code, status = invoke("status")
            doctor_code, doctor = invoke("doctor")
            preflight_code, preflight = invoke("relay-preflight")
            relay = status.get("relay_connector") or {}
            if (
                status_code != 1
                or relay.get("state") != "disabled"
                or relay.get("enabled") is not False
                or relay.get("ready") is not False
                or doctor_code != 0
                or preflight_code != 2
                or preflight.get("error") != "prepared_relay_material_unavailable"
                or p["relay_prepared"].exists()
            ):
                failures.append("disabled Relay projection was not healthy and inactive")

            relay_config.unlink()
            _code, legacy = invoke("status")
            if (legacy.get("relay_connector") or {}).get("state") != "unconfigured":
                failures.append("legacy Host without Relay config was not implicitly disabled")

            write_private(
                relay_config,
                {
                    "enabled": True,
                    "host_certificate_path": sentinel,
                    "host_http_port": 18787,
                    "host_private_key_path": sentinel,
                    "host_server_hostname": sentinel,
                    "host_tls_listen_port": 18788,
                    "relay_ca_path": sentinel,
                    "relay_host": sentinel,
                    "relay_port": 443,
                    "relay_server_hostname": sentinel,
                    "route": sentinel,
                    "schema_version": 1,
                },
            )
            _code, enabled = invoke("status")
            enabled_doctor_code, enabled_doctor = invoke("doctor")
            if (
                (enabled.get("relay_connector") or {}).get("state") != "enabled_unmanaged"
                or enabled_doctor_code != 1
                or any(
                    gate.get("ok") is not False
                    for gate in enabled_doctor.get("gates", [])
                    if gate.get("id") == "relay_connector_safe_default"
                )
            ):
                failures.append("unmanaged enabled Relay config did not fail the Doctor gate")

            write_private(
                p["relay_status"],
                {
                    "connect_attempts": 2,
                    "enabled": True,
                    "failure_code": sentinel,
                    "host_tls_ready": True,
                    "relay_tls_enabled": True,
                    "state": "connected",
                    "successful_connections": 1,
                },
            )
            _code, invalid = invoke("status")
            invalid_doctor_code, invalid_doctor = invoke("doctor")
            rendered = json.dumps(
                {"status": invalid, "doctor": invalid_doctor},
                ensure_ascii=True,
                sort_keys=True,
            )
            if (
                (invalid.get("relay_connector") or {}).get("state") != "enabled_unmanaged"
                or (invalid.get("relay_connector") or {}).get("ready") is not False
                or invalid_doctor_code != 1
                or sentinel in rendered
            ):
                failures.append("stale Relay status influenced Host readiness or disclosed values")

            p["relay_status"].unlink()
            relay_config.chmod(0o644)
            _code, broad = invoke("status")
            broad_doctor_code, _broad_doctor = invoke("doctor")
            if (
                (broad.get("relay_connector") or {}).get("config_valid") is not False
                or broad_doctor_code != 1
            ):
                failures.append("broad Relay config permissions were not rejected")

            symlink_host = root / "symlink-host"
            symlink_host.mkdir(mode=0o700)
            external_relay = root / "external-relay"
            external_relay.mkdir(mode=0o700)
            external_sentinel = external_relay / "sentinel.txt"
            external_sentinel.write_text("preserve\n", encoding="utf-8")
            (symlink_host / "relay").symlink_to(
                external_relay,
                target_is_directory=True,
            )
            os.environ["AGENTOPS_HOST_HOME"] = str(symlink_host)
            symlink_code, symlink_result = invoke(
                "init",
                "--port",
                "18789",
                "--workspace-id",
                "relay-symlink-smoke",
                "--ui-dist",
                str(ui_dist),
            )
            if (
                symlink_code != 2
                or symlink_result.get("error") != "host_not_ready"
                or not external_sentinel.is_file()
                or (external_relay / "config.json").exists()
            ):
                failures.append("Host init followed or cleaned a symlinked Relay directory")

            partial_host = root / "partial-host"
            partial_relay = partial_host / "relay"
            partial_relay.mkdir(parents=True, mode=0o700)
            write_private(partial_host / ".agentops-host-data.json", host.HOST_DATA_MARKER)
            partial_config = partial_relay / "config.json"
            write_private(partial_config, {"enabled": False, "schema_version": 1})
            partial_before = partial_config.read_bytes()
            os.environ["AGENTOPS_HOST_HOME"] = str(partial_host)
            partial_code, _partial = invoke(
                "init",
                "--port",
                "18790",
                "--workspace-id",
                "relay-partial-smoke",
                "--ui-dist",
                str(ui_dist),
            )
            if partial_code != 0 or partial_config.read_bytes() != partial_before:
                failures.append("safe interrupted Relay initialization was not recoverable")
            os.environ["AGENTOPS_HOST_HOME"] = str(host_home)
        finally:
            host.tailscale_state = original_tailscale_state
            host.tailscale_binary = original_tailscale_binary
            host.tailscale_serve_state = original_tailscale_serve_state
            if old_home is None:
                os.environ.pop("AGENTOPS_HOST_HOME", None)
            else:
                os.environ["AGENTOPS_HOST_HOME"] = old_home

    result = {
        "disabled_by_default": True,
        "failures": failures,
        "default_host_lifecycle_starts_relay": False,
        "enabled_lifecycle_covered_separately": True,
        "legacy_host_compatible": not any("legacy" in item for item in failures),
        "ok": not failures,
        "operation": "private_host_relay_config_smoke",
        "private_values_omitted": True,
        "tailscale_changed": False,
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
