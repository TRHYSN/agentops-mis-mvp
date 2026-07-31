#!/usr/bin/env python3
"""Verify safe worker service-install behavior without loading services."""
from __future__ import annotations

import json
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run(cmd: list[str], cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=60, check=False)


def parse_json(proc: subprocess.CompletedProcess[str]) -> dict:
    try:
        return json.loads(proc.stdout)
    except Exception:
        return {"parse_error": proc.stdout, "stderr": proc.stderr, "returncode": proc.returncode}


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def main() -> int:
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="agentops_service_install_") as tmp:
        tmp_path = Path(tmp)
        service_path = tmp_path / "local.agentops.worker.agt_service_install.plist"
        wrapper_path = tmp_path / "local.agentops.worker.agt_service_install_wrapper.plist"
        hermes_path = tmp_path / "local.agentops.worker.agt_service_install_hermes.plist"
        unsafe_gateway_path = tmp_path / "unsafe-hermes.plist"

        dry = run([
            sys.executable,
            "-m",
            "agentops_mis_cli.worker",
            "service-install",
            "--manager",
            "launchd",
            "--agent-id",
            "agt_service_install",
            "--adapter",
            "mock",
            "--service-path",
            str(service_path),
        ])
        dry_payload = parse_json(dry)
        require(dry.returncode == 0, f"dry-run install failed: {dry_payload}", failures)
        require(dry_payload.get("dry_run") is True, f"dry-run flag missing: {dry_payload}", failures)
        require(dry_payload.get("wrote") is False and not service_path.exists(), f"dry-run wrote file: {dry_payload}", failures)

        install = run([
            sys.executable,
            "-m",
            "agentops_mis_cli.worker",
            "service-install",
            "--manager",
            "launchd",
            "--agent-id",
            "agt_service_install",
            "--adapter",
            "mock",
            "--service-path",
            str(service_path),
            "--confirm-install",
        ])
        install_payload = parse_json(install)
        require(install.returncode == 0, f"confirmed install failed: {install_payload}", failures)
        require(install_payload.get("wrote") is True and service_path.exists(), f"confirmed install did not write file: {install_payload}", failures)
        require(install_payload.get("service_check", {}).get("ok") is True, f"installed file failed service check: {install_payload}", failures)
        install_text = service_path.read_text(encoding="utf-8")
        require("AGENTOPS_API_KEY" not in install_text, "default service install should not persist API-key placeholder", failures)
        require("--use-session" not in install_text, "default local service install should not require session minting", failures)
        require("WorkingDirectory" in install_text, "launchd service should set repo working directory", failures)
        require("~/Library/Logs" not in install_text, "launchd service install should expand log path", failures)
        require(("agentops-worker" in install_text) or ("agentops_mis_cli.worker" in install_text), "service install missing worker entrypoint", failures)
        require(install_payload.get("service_check", {}).get("service_file", {}).get("local_dev_no_token") is True, f"local dev no-token proof missing: {install_payload}", failures)
        mode = stat.S_IMODE(service_path.stat().st_mode)
        require(mode == 0o600, f"service file mode should be 0600, got {oct(mode)}", failures)

        duplicate = run([
            sys.executable,
            "-m",
            "agentops_mis_cli.worker",
            "service-install",
            "--manager",
            "launchd",
            "--agent-id",
            "agt_service_install",
            "--adapter",
            "mock",
            "--service-path",
            str(service_path),
            "--confirm-install",
        ])
        duplicate_payload = parse_json(duplicate)
        require(duplicate.returncode == 1, f"duplicate install without overwrite should fail: {duplicate_payload}", failures)
        require(duplicate_payload.get("exists_before") is True and duplicate_payload.get("wrote") is False, f"duplicate install wrote file: {duplicate_payload}", failures)

        wrapper = run([
            str(ROOT / "scripts" / "agentops"),
            "worker",
            "service-install",
            "--manager",
            "launchd",
            "--agent-id",
            "agt_service_install_wrapper",
            "--adapter",
            "mock",
            "--service-path",
            str(wrapper_path),
            "--confirm-install",
        ])
        wrapper_payload = parse_json(wrapper)
        require(wrapper.returncode == 0, f"wrapper service-install failed: {wrapper_payload}", failures)
        require(wrapper_payload.get("command") == "agentops worker service-install", f"wrong wrapper command: {wrapper_payload}", failures)
        require(wrapper_payload.get("wrote") is True and wrapper_path.exists(), f"wrapper did not write file: {wrapper_payload}", failures)
        wrapper_text = wrapper_path.read_text(encoding="utf-8")
        require("AGENTOPS_API_KEY" not in wrapper_text, "wrapper install should not persist API-key placeholder", failures)
        require("--use-session" not in wrapper_text, "wrapper local service install should not require session minting", failures)
        require("WorkingDirectory" in wrapper_text, "wrapper launchd service should set repo working directory", failures)
        require("~/Library/Logs" not in wrapper_text, "wrapper launchd service install should expand log path", failures)

        hermes = run([
            str(ROOT / "scripts" / "agentops"),
            "worker",
            "service-install",
            "--manager",
            "launchd",
            "--agent-id",
            "agt_service_install_hermes",
            "--adapter",
            "hermes",
            "--confirm-run",
            "--hermes-gateway-url",
            "http://127.0.0.1:8643/",
            "--service-path",
            str(hermes_path),
            "--confirm-install",
        ])
        hermes_payload = parse_json(hermes)
        require(hermes.returncode == 0, f"Hermes service install failed: {hermes_payload}", failures)
        require(hermes_payload.get("wrote") is True and hermes_path.exists(), f"Hermes service file missing: {hermes_payload}", failures)
        hermes_text = hermes_path.read_text(encoding="utf-8") if hermes_path.exists() else ""
        hermes_gateway_persisted = bool(re.search(
            r"<key>HERMES_GATEWAY_URL</key>\s*<string>http://127\.0\.0\.1:8643</string>",
            hermes_text,
        ))
        require(hermes_gateway_persisted, "Hermes gateway URL was not normalized and persisted", failures)

        unsafe_gateway = run([
            sys.executable,
            "-m",
            "agentops_mis_cli.worker",
            "service-install",
            "--manager",
            "launchd",
            "--agent-id",
            "agt_service_install_unsafe_gateway",
            "--adapter",
            "hermes",
            "--confirm-run",
            "--hermes-gateway-url",
            "http://service-user:credential-value@127.0.0.1:8643",
            "--service-path",
            str(unsafe_gateway_path),
            "--confirm-install",
        ])
        unsafe_gateway_payload = parse_json(unsafe_gateway)
        require(unsafe_gateway.returncode == 1, f"credential-bearing Hermes URL should fail: {unsafe_gateway_payload}", failures)
        require(unsafe_gateway_payload.get("error") == "invalid_hermes_gateway_url", f"wrong Hermes URL error: {unsafe_gateway_payload}", failures)
        require(unsafe_gateway_payload.get("wrote") is False and not unsafe_gateway_path.exists(), f"unsafe Hermes URL wrote a service file: {unsafe_gateway_payload}", failures)

        unsafe = run([
            sys.executable,
            "-m",
            "agentops_mis_cli.worker",
            "service-install",
            "--manager",
            "launchd",
            "--agent-id",
            "agt_service_install_unsafe",
            "--adapter",
            "mock",
            "--service-path",
            str(tmp_path / "unsafe.plist"),
            "--api-key-placeholder",
            "agtok_fake_should_not_be_written",
            "--confirm-install",
        ])
        unsafe_payload = parse_json(unsafe)
        require(unsafe.returncode == 1, f"unsafe install should fail: {unsafe_payload}", failures)
        require(unsafe_payload.get("wrote") is False, f"unsafe install wrote file: {unsafe_payload}", failures)

        serialized = json.dumps({
            "dry": dry_payload,
            "install": install_payload,
            "duplicate": duplicate_payload,
            "wrapper": wrapper_payload,
            "hermes": hermes_payload,
            "unsafe_gateway": unsafe_gateway_payload,
            "unsafe": unsafe_payload,
        }, ensure_ascii=False)
        require("agtok_fake_should_not_be_written" not in serialized, "service-install leaked raw token-like content", failures)
        require("sk-" not in serialized and "ntn_" not in serialized, "service-install leaked secret-like content", failures)
        require("credential-value" not in serialized and "service-user" not in serialized, "service-install echoed credential-bearing Hermes URL", failures)

    print(json.dumps({
        "ok": not failures,
        "dry_run_ok": not failures and dry_payload.get("dry_run"),
        "install_wrote": install_payload.get("wrote"),
        "wrapper_wrote": wrapper_payload.get("wrote"),
        "hermes_gateway_persisted": hermes_gateway_persisted,
        "unsafe_gateway_blocked": unsafe_gateway_payload.get("error") == "invalid_hermes_gateway_url",
        "unsafe_blocked": unsafe_payload.get("ok") is False,
        "failures": failures,
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
