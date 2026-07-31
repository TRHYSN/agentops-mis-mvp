#!/usr/bin/env python3
"""Verify the standalone private Relay transition core without using a network."""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agentops_mis_cli import relay_control, relay_restart  # noqa: E402


RELAY_HOSTNAME = "relay.control.test"
HOST_HOSTNAME = "console.control.test"
SECRET_SENTINEL = "a7" * 32
PATH_SENTINEL = "relay-control-private-material"


def write_private(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    path.chmod(0o600)


def generate_certificate(
    openssl: str,
    directory: Path,
    *,
    prefix: str,
    hostname: str,
) -> tuple[Path, Path]:
    certificate = directory / f"{prefix}-cert.pem"
    private_key = directory / f"{prefix}-key.pem"
    subprocess.run(
        [
            openssl,
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-sha256",
            "-nodes",
            "-days",
            "1",
            "-subj",
            f"/CN={hostname}",
            "-addext",
            f"subjectAltName=DNS:{hostname}",
            "-keyout",
            str(private_key),
            "-out",
            str(certificate),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=30,
    )
    private_key.chmod(0o600)
    certificate.chmod(0o600)
    return certificate, private_key


def public_output_safe(payloads: list[dict[str, Any]], private_root: Path) -> bool:
    rendered = json.dumps(payloads, sort_keys=True)
    return bool(
        SECRET_SENTINEL not in rendered
        and PATH_SENTINEL not in rendered
        and str(private_root) not in rendered
        and "material_digest" not in rendered
        and not re.search(r'(?<![A-Za-z0-9])[a-f0-9]{64}(?![A-Za-z0-9])', rendered)
    )


def prepare(
    paths: dict[str, Path],
    *,
    action: str,
    now: int,
    ttl: int = 120,
) -> dict[str, Any]:
    return relay_control.prepare_relay_transition(
        action=action,
        transition_path=paths["transition"],
        active_config_path=paths["active"],
        prepared_config_path=paths["prepared"],
        secrets_path=paths["secrets"],
        host_config_path=paths["host"],
        ttl_seconds=ttl,
        now=now,
    )


def confirm(
    paths: dict[str, Path],
    *,
    action: str,
    transition_ref: str,
    now: int,
) -> dict[str, Any]:
    return relay_control.confirm_relay_transition(
        action=action,
        transition_ref=transition_ref,
        transition_path=paths["transition"],
        active_config_path=paths["active"],
        prepared_config_path=paths["prepared"],
        secrets_path=paths["secrets"],
        host_config_path=paths["host"],
        now=now,
    )


def execute(
    paths: dict[str, Path],
    *,
    action: str,
    transition_ref: str,
    now: int,
) -> dict[str, Any]:
    return relay_control.execute_confirmed_relay_transition(
        action=action,
        transition_ref=transition_ref,
        transition_path=paths["transition"],
        active_config_path=paths["active"],
        prepared_config_path=paths["prepared"],
        secrets_path=paths["secrets"],
        host_config_path=paths["host"],
        now=now,
    )


def main() -> int:
    openssl = shutil.which("openssl")
    if not openssl:
        print(json.dumps({"ok": False, "error": "openssl_unavailable"}, sort_keys=True))
        return 2

    failures: list[str] = []
    public_payloads: list[dict[str, Any]] = []
    evidence: dict[str, bool] = {}
    with tempfile.TemporaryDirectory(prefix=f"{PATH_SENTINEL}-") as temporary:
        root = Path(temporary)
        relay_home = root / "relay"
        host_home = root / "host"
        relay_home.mkdir(mode=0o700)
        host_home.mkdir(mode=0o700)
        paths = {
            "active": relay_home / "config.json",
            "prepared": relay_home / "prepared.json",
            "restart_receipt": relay_home / "restart-receipt.json",
            "restart_sequence": relay_home / "restart-sequence.json",
            "prearm_restart_receipt": relay_home / "prearm-restart-receipt.json",
            "prearm_restart_sequence": relay_home / "prearm-restart-sequence.json",
            "secrets": relay_home / "secrets.json",
            "transition": relay_home / "transition.json",
            "host": host_home / "config.json",
        }
        relay_certificate, _relay_key = generate_certificate(
            openssl,
            relay_home,
            prefix="relay",
            hostname=RELAY_HOSTNAME,
        )
        host_certificate, host_key = generate_certificate(
            openssl,
            relay_home,
            prefix="host",
            hostname=HOST_HOSTNAME,
        )
        enabled = {
            "enabled": True,
            "host_certificate_path": str(host_certificate),
            "host_http_port": 18787,
            "host_private_key_path": str(host_key),
            "host_server_hostname": HOST_HOSTNAME,
            "host_tls_listen_port": 18788,
            "relay_ca_path": str(relay_certificate),
            "relay_host": "127.0.0.1",
            "relay_port": 443,
            "relay_server_hostname": RELAY_HOSTNAME,
            "route": "relay-control-smoke",
            "schema_version": 1,
        }
        tailscale_fields = {
            "tailscale_https_port": 8443,
            "tailscale_device_name": "preserve-this-device",
            "tailscale_custom_policy": {"preserve": True},
        }
        base_host = {
            "allowed_origins": ["http://127.0.0.1:18787", "https://unrelated.example"],
            "cookie_secure": False,
            "database_path": "/private/value/omitted.db",
            "deployment_mode": "private_host",
            "host": "127.0.0.1",
            "network_publication": "disabled",
            "port": 18787,
            "private_console_origin": "",
            "unrelated": {"preserved": True},
            **tailscale_fields,
        }
        write_private(paths["active"], relay_control.DISABLED_RELAY_CONFIG)
        write_private(paths["prepared"], enabled)
        write_private(
            paths["secrets"],
            {"schema_version": 1, "tunnel_key_hex": SECRET_SENTINEL},
        )
        write_private(paths["host"], base_host)

        prepared = prepare(paths, action="enable", now=1_000)
        public_payloads.append(prepared)
        pending_status = relay_control.public_relay_status(
            transition_path=paths["transition"],
            active_config_path=paths["active"],
            host_config_path=paths["host"],
            now=1_000,
        )
        public_payloads.append(pending_status)
        transition_private = bool(
            prepared.get("ok") is True
            and isinstance(prepared.get("transition_ref"), str)
            and prepared.get("expires_at") == 1_120
            and prepared.get("confirmation_required") is True
            and prepared.get("restart_required") is False
            and pending_status.get("state") == "prepared"
            and pending_status.get("transition_ref") == prepared.get("transition_ref")
            and stat.S_IMODE(relay_home.stat().st_mode) == 0o700
            and stat.S_IMODE(paths["transition"].stat().st_mode) == 0o600
            and paths["active"].read_text(encoding="utf-8")
            == json.dumps(relay_control.DISABLED_RELAY_CONFIG, sort_keys=True) + "\n"
        )
        evidence["disabled_default_and_private_prepare"] = transition_private

        confirmed = confirm(
            paths,
            action="enable",
            transition_ref=str(prepared.get("transition_ref") or ""),
            now=1_001,
        )
        public_payloads.append(confirmed)
        active_before_execute = json.loads(paths["active"].read_text(encoding="utf-8"))
        host_before_execute = json.loads(paths["host"].read_text(encoding="utf-8"))
        evidence["confirmation_is_non_executing"] = bool(
            confirmed.get("ok") is True
            and confirmed.get("state") == "confirmed"
            and confirmed.get("expires_at") == 1_120
            and confirmed.get("confirmation_required") is False
            and confirmed.get("restart_required") is False
            and active_before_execute == relay_control.DISABLED_RELAY_CONFIG
            and host_before_execute == base_host
        )
        confirmed_replay = confirm(
            paths,
            action="enable",
            transition_ref=str(prepared.get("transition_ref") or ""),
            now=1_001,
        )
        public_payloads.append(confirmed_replay)
        evidence["confirmation_replay_rejected"] = (
            confirmed_replay.get("error") == "confirmation_already_recorded"
        )
        executed = execute(
            paths,
            action="enable",
            transition_ref=str(prepared.get("transition_ref") or ""),
            now=1_002,
        )
        public_payloads.append(executed)
        enabled_host = json.loads(paths["host"].read_text(encoding="utf-8"))
        relay_origin = f"https://{HOST_HOSTNAME}"
        enable_exact = bool(
            executed.get("ok") is True
            and executed.get("restart_required") is True
            and executed.get("transition_ref") == prepared.get("transition_ref")
            and json.loads(paths["active"].read_text(encoding="utf-8")) == enabled
            and enabled_host["network_publication"] == "agentops_relay"
            and enabled_host["cookie_secure"] is True
            and enabled_host["private_console_origin"] == relay_origin
            and enabled_host["allowed_origins"]
            == base_host["allowed_origins"] + [relay_origin]
            and enabled_host["unrelated"] == base_host["unrelated"]
            and all(enabled_host[key] == value for key, value in tailscale_fields.items())
            and not paths["transition"].exists()
            and not (relay_home / ".relay-transition-rollback.json").exists()
        )
        evidence["enable_exact_and_tailscale_unchanged"] = enable_exact

        replay = execute(
            paths,
            action="enable",
            transition_ref=str(prepared.get("transition_ref") or ""),
            now=1_003,
        )
        public_payloads.append(replay)
        evidence["replay_rejected"] = replay.get("error") == "transition_not_found"

        disable_prepared = prepare(paths, action="disable", now=2_000)
        public_payloads.append(disable_prepared)
        disable_confirmed = confirm(
            paths,
            action="disable",
            transition_ref=str(disable_prepared.get("transition_ref") or ""),
            now=2_001,
        )
        public_payloads.append(disable_confirmed)
        disabled = execute(
            paths,
            action="disable",
            transition_ref=str(disable_prepared.get("transition_ref") or ""),
            now=2_002,
        )
        public_payloads.append(disabled)
        disabled_host = json.loads(paths["host"].read_text(encoding="utf-8"))
        evidence["disable_exact_and_unrelated_origins_preserved"] = bool(
            disabled.get("ok") is True
            and json.loads(paths["active"].read_text(encoding="utf-8"))
            == relay_control.DISABLED_RELAY_CONFIG
            and disabled_host["network_publication"] == "disabled"
            and disabled_host["cookie_secure"] is False
            and disabled_host["private_console_origin"] == ""
            and disabled_host["allowed_origins"] == base_host["allowed_origins"]
            and all(disabled_host[key] == value for key, value in tailscale_fields.items())
        )

        mutation_prepared = prepare(paths, action="enable", now=3_000)
        public_payloads.append(mutation_prepared)
        original_certificate = host_certificate.read_bytes()
        host_certificate.write_bytes(original_certificate + b"\n")
        host_certificate.chmod(0o600)
        mutation = confirm(
            paths,
            action="enable",
            transition_ref=str(mutation_prepared.get("transition_ref") or ""),
            now=3_001,
        )
        public_payloads.append(mutation)
        evidence["material_mutation_rejected"] = bool(
            mutation.get("error") == "transition_material_changed"
            and json.loads(paths["active"].read_text(encoding="utf-8"))
            == relay_control.DISABLED_RELAY_CONFIG
        )
        host_certificate.write_bytes(original_certificate)
        host_certificate.chmod(0o600)

        expiry_prepared = prepare(paths, action="enable", now=4_000, ttl=1)
        public_payloads.append(expiry_prepared)
        expired = confirm(
            paths,
            action="enable",
            transition_ref=str(expiry_prepared.get("transition_ref") or ""),
            now=4_001,
        )
        public_payloads.append(expired)
        evidence["expiry_rejected"] = expired.get("error") == "transition_expired"
        invalid_ttl = prepare(paths, action="enable", now=4_100, ttl=301)
        public_payloads.append(invalid_ttl)
        evidence["ttl_bounded"] = invalid_ttl.get("error") == "invalid_ttl"

        paths["transition"].unlink(missing_ok=True)
        rollback_prepared = prepare(paths, action="enable", now=5_000)
        public_payloads.append(rollback_prepared)
        rollback_confirmed = confirm(
            paths,
            action="enable",
            transition_ref=str(rollback_prepared.get("transition_ref") or ""),
            now=5_001,
        )
        public_payloads.append(rollback_confirmed)
        active_before = paths["active"].read_bytes()
        host_before = paths["host"].read_bytes()
        real_atomic_write = relay_control._atomic_write_bytes
        writes = 0
        journal_was_private = False

        def fail_second_write(path: Path, payload: bytes) -> None:
            nonlocal journal_was_private, writes
            writes += 1
            if writes == 2:
                journal = relay_home / ".relay-transition-rollback.json"
                journal_was_private = bool(
                    journal.is_file()
                    and stat.S_IMODE(journal.stat().st_mode) == 0o600
                )
                raise OSError("injected second write failure")
            real_atomic_write(path, payload)

        relay_control._atomic_write_bytes = fail_second_write
        try:
            rolled_back = execute(
                paths,
                action="enable",
                transition_ref=str(rollback_prepared.get("transition_ref") or ""),
                now=5_002,
            )
        finally:
            relay_control._atomic_write_bytes = real_atomic_write
        public_payloads.append(rolled_back)
        evidence["second_write_failure_rolled_back"] = bool(
            rolled_back.get("error") == "transition_write_failed"
            and paths["active"].read_bytes() == active_before
            and paths["host"].read_bytes() == host_before
            and not (relay_home / ".relay-transition-rollback.json").exists()
        )
        evidence["rollback_journal_was_private"] = journal_was_private

        paths["transition"].unlink(missing_ok=True)
        prearm_prepared = prepare(paths, action="enable", now=5_050)
        public_payloads.append(prearm_prepared)
        prearm_confirmed = confirm(
            paths,
            action="enable",
            transition_ref=str(prearm_prepared.get("transition_ref") or ""),
            now=5_051,
        )
        public_payloads.append(prearm_confirmed)
        prearm_active_before = paths["active"].read_bytes()
        prearm_host_before = paths["host"].read_bytes()
        real_create_restart_receipt = relay_restart.create_restart_receipt

        def fail_before_receipt_arm(**_kwargs: Any) -> dict[str, Any]:
            raise relay_restart.RelayRestartError("write_failed")

        relay_restart.create_restart_receipt = fail_before_receipt_arm
        try:
            prearm_failed = relay_control.execute_confirmed_relay_transition(
                action="enable",
                transition_ref=str(prearm_prepared.get("transition_ref") or ""),
                transition_path=paths["transition"],
                active_config_path=paths["active"],
                prepared_config_path=paths["prepared"],
                secrets_path=paths["secrets"],
                host_config_path=paths["host"],
                restart_receipt_path=paths["prearm_restart_receipt"],
                restart_sequence_path=paths["prearm_restart_sequence"],
                now=5_052,
            )
        finally:
            relay_restart.create_restart_receipt = real_create_restart_receipt
        public_payloads.append(prearm_failed)
        evidence["prearm_failure_preserves_configs_and_transition"] = bool(
            prearm_failed.get("error") == "transition_invalid"
            and paths["active"].read_bytes() == prearm_active_before
            and paths["host"].read_bytes() == prearm_host_before
            and paths["transition"].is_file()
            and not paths["prearm_restart_receipt"].exists()
            and not paths["prearm_restart_sequence"].exists()
        )

        paths["transition"].unlink(missing_ok=True)
        real_token_urlsafe = relay_control.secrets.token_urlsafe
        relay_control.secrets.token_urlsafe = lambda _bytes: "_forced-leading-ref"
        try:
            receipt_rollback_prepared = prepare(paths, action="enable", now=5_100)
        finally:
            relay_control.secrets.token_urlsafe = real_token_urlsafe
        public_payloads.append(receipt_rollback_prepared)
        evidence["restart_receipt_ref_compatible"] = bool(
            receipt_rollback_prepared.get("ok") is True
            and str(receipt_rollback_prepared.get("transition_ref") or "")
            .startswith("relay_")
        )
        receipt_rollback_confirmed = confirm(
            paths,
            action="enable",
            transition_ref=str(receipt_rollback_prepared.get("transition_ref") or ""),
            now=5_101,
        )
        public_payloads.append(receipt_rollback_confirmed)
        receipt_active_before = paths["active"].read_bytes()
        receipt_host_before = paths["host"].read_bytes()
        real_unlink_private = relay_control._unlink_private
        consume_failure_injected = False

        def fail_transition_consume(path: Path) -> None:
            nonlocal consume_failure_injected
            if path == paths["transition"]:
                consume_failure_injected = True
                raise relay_control._ControlFailure("transition_store_invalid")
            real_unlink_private(path)

        relay_control._unlink_private = fail_transition_consume
        try:
            receipt_rolled_back = relay_control.execute_confirmed_relay_transition(
                action="enable",
                transition_ref=str(receipt_rollback_prepared.get("transition_ref") or ""),
                transition_path=paths["transition"],
                active_config_path=paths["active"],
                prepared_config_path=paths["prepared"],
                secrets_path=paths["secrets"],
                host_config_path=paths["host"],
                restart_receipt_path=paths["restart_receipt"],
                restart_sequence_path=paths["restart_sequence"],
                now=5_102,
            )
        finally:
            relay_control._unlink_private = real_unlink_private
        public_payloads.append(receipt_rolled_back)
        receipt_projection: dict[str, Any] = {}
        if consume_failure_injected and paths["restart_receipt"].is_file():
            receipt_projection = relay_restart.public_restart_receipt(
                receipt_path=paths["restart_receipt"],
                sequence_path=paths["restart_sequence"],
            )
        evidence["receipt_consume_failure_rolled_back_both_configs"] = bool(
            consume_failure_injected
            and receipt_rolled_back.get("error") == "transition_write_failed"
            and receipt_projection.get("state") == "rolled_back"
            and paths["active"].read_bytes() == receipt_active_before
            and paths["host"].read_bytes() == receipt_host_before
            and paths["transition"].is_file()
        )

        paths["transition"].unlink(missing_ok=True)
        relay_home.chmod(0o755)
        broad_permissions = prepare(paths, action="enable", now=6_000)
        public_payloads.append(broad_permissions)
        evidence["broad_permissions_rejected"] = bool(
            broad_permissions.get("error") == "transition_store_invalid"
            and not paths["transition"].exists()
        )
        relay_home.chmod(0o700)

        evidence["public_output_redacted"] = public_output_safe(public_payloads, root)
        expected_public_keys = {
            "action",
            "confirmation_required",
            "expires_at",
            "restart_required",
            "transition_ref",
        }
        evidence["stable_public_result_fields"] = all(
            expected_public_keys <= set(payload) for payload in public_payloads
        )
        bounded_error = relay_control.RelayControlError("unbounded-private-detail")
        evidence["bounded_public_error_type"] = bounded_error.code == "transition_invalid"
        evidence["no_network_or_external_state"] = all(
            payload.get("network_used") is False for payload in public_payloads
        )

    failures.extend(name for name, passed in evidence.items() if not passed)
    result = {
        "ok": not failures,
        "operation": "private_host_relay_control_core_smoke",
        "evidence": evidence,
        "failure_codes": failures,
        "network_used": False,
        "subprocess_used_by_transition_core": False,
        "sensitive_values_omitted": True,
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
