#!/usr/bin/env python3
"""Exercise the preview-first, host-only macOS LaunchAgent lifecycle."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def run(env: dict[str, str], *arguments: str, expected: tuple[int, ...] = (0,)) -> tuple[int, dict, str]:
    process = subprocess.run(
        [sys.executable, "-m", "agentops_mis_cli", "host", *arguments],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    try:
        payload = json.loads(process.stdout)
    except ValueError:
        payload = {}
    if process.returncode not in expected:
        raise RuntimeError(
            f"host {' '.join(arguments)} exited {process.returncode}: "
            f"stdout={process.stdout[-800:]} stderr={process.stderr[-800:]}"
        )
    return process.returncode, payload, process.stdout + process.stderr


def write_private_service(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    path.chmod(0o600)


def replace_service_fragment(content: bytes, old: bytes, new: bytes, *, count: int = 1) -> bytes:
    replaced = content.replace(old, new, count)
    if replaced == content:
        raise RuntimeError(f"service fixture fragment was not found: {old!r}")
    return replaced


def main() -> int:
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="agentops-host-service-") as temporary:
        temp = Path(temporary)
        host_home = temp / "host"
        service_path = temp / "LaunchAgents" / "dev.agentops.mis.private-host.plist"
        launchctl_state = temp / "launchctl.loaded"
        launchctl_calls = temp / "launchctl.calls"
        launchctl_stale_reads = temp / "launchctl.stale-reads"
        fake_launchctl = temp / "launchctl"
        fake_launchctl.write_text(
            "#!/bin/sh\n"
            "set -eu\n"
            f"printf '%s\\n' \"$*\" >> {launchctl_calls}\n"
            "case \"${1:-}\" in\n"
            "  print)\n"
            "    if [ -f \"$AGENTOPS_TEST_LAUNCHCTL_STALE_READS_STATE\" ]; then\n"
            "      stale_reads=$(cat \"$AGENTOPS_TEST_LAUNCHCTL_STALE_READS_STATE\")\n"
            "      if [ \"$stale_reads\" -gt 0 ]; then\n"
            "        printf '%s\\n' \"$((stale_reads - 1))\" > \"$AGENTOPS_TEST_LAUNCHCTL_STALE_READS_STATE\"\n"
            "        exit 0\n"
            "      fi\n"
            "    fi\n"
            "    [ -f \"$AGENTOPS_TEST_LAUNCHCTL_STATE\" ]\n"
            "    ;;\n"
            "  bootstrap) : > \"$AGENTOPS_TEST_LAUNCHCTL_STATE\" ;;\n"
            "  bootout)\n"
            "    rm -f \"$AGENTOPS_TEST_LAUNCHCTL_STATE\"\n"
            "    printf '%s\\n' \"${AGENTOPS_TEST_LAUNCHCTL_STALE_UNLOAD_READS:-0}\" > \"$AGENTOPS_TEST_LAUNCHCTL_STALE_READS_STATE\"\n"
            "    ;;\n"
            "  kickstart) [ -f \"$AGENTOPS_TEST_LAUNCHCTL_STATE\" ] ;;\n"
            "  *) exit 2 ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        fake_launchctl.chmod(0o700)
        env = os.environ.copy()
        env.update(
            {
                "AGENTOPS_HOST_HOME": str(host_home),
                "AGENTOPS_INSTALL_ROOT": str(ROOT),
                "AGENTOPS_LAUNCHCTL_BIN": str(fake_launchctl),
                "AGENTOPS_TEST_LAUNCHCTL_STATE": str(launchctl_state),
                "AGENTOPS_TEST_LAUNCHCTL_STALE_READS_STATE": str(launchctl_stale_reads),
            }
        )

        run(env, "init", "--port", "18998")
        _, preview, preview_output = run(env, "service-install", "--service-path", str(service_path))
        require(preview.get("dry_run") is True, "service install must default to dry-run", failures)
        require(preview.get("wrote") is False and not service_path.exists(), "dry-run wrote a service file", failures)
        require(preview.get("workers") == [], "service preview must declare an empty Worker set", failures)
        require(preview.get("live_workers_started") is False, "service preview claimed live Worker execution", failures)
        require("agthost_" not in preview_output and "agtadmin_" not in preview_output, "service preview exposed a Host credential", failures)

        _, installed, installed_output = run(
            env,
            "service-install",
            "--service-path",
            str(service_path),
            "--confirm-install",
        )
        require(installed.get("wrote") is True, "confirmed install did not write the service file", failures)
        require(service_path.exists() and service_path.stat().st_mode & 0o077 == 0, "service file is not mode 0600", failures)
        launchd_log = host_home / "logs" / "launchd.log"
        require(
            launchd_log.is_file()
            and launchd_log.stat().st_mode & 0o777 == 0o600
            and launchd_log.stat().st_nlink == 1
            and launchd_log.stat().st_size == 0,
            "confirmed install did not create an empty private launchd log",
            failures,
        )
        require(
            installed.get("service_log", {}).get("created") is True
            and installed.get("service_log", {}).get("mode_private") is True,
            "confirmed install did not report private launchd log preparation",
            failures,
        )
        require("agthost_" not in installed_output and "agtadmin_" not in installed_output, "confirmed install output exposed a Host credential", failures)
        service = service_path.read_text(encoding="utf-8")
        require("<string>start</string>" in service and "<string>--foreground</string>" in service and "<string>--no-workers</string>" in service, "Host command is not host-only", failures)
        require("<string>--worker</string>" not in service and "--confirm-live-workers" not in service, "service command can start live Workers", failures)
        require("<key>RunAtLoad</key>\n  <true/>" in service and "<key>KeepAlive</key>\n  <true/>" in service, "service relaunch policy is missing", failures)
        require(all(f"<key>{key}</key>" in service for key in ("AGENTOPS_HOST_HOME", "AGENTOPS_INSTALL_ROOT", "PYTHONPATH")), "expected service environment is incomplete", failures)
        require("AGENTOPS_API_KEY" not in service and "AGENTOPS_ADMIN_KEY" not in service, "service environment contains credential keys", failures)
        require(not any(prefix in service for prefix in ("agthost_", "agtadmin_", "agtok_", "agtsess_", "ntn_", "sk-")), "service file contains token-like material", failures)
        current_service = service_path.read_bytes()
        managed_argument = b"      <string>--managed-launch-agent</string>\n"
        previous_service = replace_service_fragment(current_service, managed_argument, b"")
        require(previous_service != current_service, "previous managed service fixture did not differ from the current template", failures)
        require(b"<string>dev.agentops.mis.private-host</string>" in previous_service, "previous managed service fixture is invalid", failures)

        _, checked, _ = run(env, "service-check", "--service-path", str(service_path))
        require(checked.get("ok") is True, f"installed service check failed: {checked}", failures)
        require(checked.get("service_state", {}).get("loaded") is False, "service unexpectedly loaded during install", failures)

        service_path.chmod(0o400)
        _, non_writable_service, _ = run(
            env,
            "service-check",
            "--service-path",
            str(service_path),
            expected=(1,),
        )
        require(
            non_writable_service.get("ok") is False
            and non_writable_service.get("service_file", {}).get("exact_managed_definition") is True
            and non_writable_service.get("service_file", {}).get("mode_exact_private") is False,
            "exact service definition bypassed the required 0600 mode",
            failures,
        )
        service_path.chmod(0o600)

        launchd_marker = b"existing launchd log fixture\n"
        launchd_log.write_bytes(launchd_marker)
        launchd_log.chmod(0o644)
        _, repair_preview, _ = run(
            env,
            "service-install",
            "--service-path",
            str(service_path),
            "--overwrite",
        )
        require(
            repair_preview.get("service_log", {}).get("planned_action") == "tighten_permissions"
            and launchd_log.stat().st_mode & 0o777 == 0o644,
            "service install preview did not report the legacy launchd log repair without mutating it",
            failures,
        )
        _, maintenance_check, _ = run(env, "service-check", "--service-path", str(service_path))
        require(
            maintenance_check.get("ok") is True
            and maintenance_check.get("log_rotation_ready") is False
            and maintenance_check.get("maintenance_status") == "attention"
            and maintenance_check.get("maintenance_next_action") == "agentops host service-install --overwrite",
            "service check did not expose the bounded launchd log repair action",
            failures,
        )
        _, blocked_rotation, _ = run(env, "log-rotate", expected=(1,))
        require(
            blocked_rotation.get("error") == "host_log_inventory_permissions_unsafe"
            and blocked_rotation.get("entry_label") == "launchd.log"
            and blocked_rotation.get("service_log_repair_candidate") is True
            and blocked_rotation.get("service_check_required") is True
            and blocked_rotation.get("next_action") == "agentops host service-check",
            "blocked log rotation did not route repair through the read-only service check",
            failures,
        )
        _, unsafe_log_load_refused, _ = run(
            env,
            "service-control",
            "--action",
            "load",
            "--service-path",
            str(service_path),
            "--confirm-control",
            expected=(1,),
        )
        require(
            "host_service_log_not_private" in (unsafe_log_load_refused.get("blockers") or [])
            and not launchctl_state.exists(),
            "service control loaded a Host before its launchd log was private",
            failures,
        )
        _, repaired, _ = run(
            env,
            "service-install",
            "--service-path",
            str(service_path),
            "--overwrite",
            "--confirm-install",
        )
        require(
            repaired.get("service_log", {}).get("permissions_tightened") is True
            and launchd_log.stat().st_mode & 0o777 == 0o600
            and launchd_log.read_bytes() == launchd_marker,
            "confirmed install did not tighten launchd log permissions without changing content",
            failures,
        )
        _, rotation_ready, _ = run(env, "log-rotate")
        require(
            rotation_ready.get("ok") is True
            and rotation_ready.get("rotation_required") is False,
            f"service install did not leave the log inventory rotation-ready: {rotation_ready}",
            failures,
        )

        outside_log = temp / "outside-launchd.log"
        outside_log.write_bytes(launchd_marker)
        outside_log.chmod(0o600)
        launchd_log.unlink()
        launchd_log.symlink_to(outside_log)
        _, unsafe_log_refused, _ = run(
            env,
            "service-install",
            "--service-path",
            str(service_path),
            "--overwrite",
            "--confirm-install",
            expected=(1,),
        )
        require(
            "host_service_log_unsafe" in (unsafe_log_refused.get("blockers") or [])
            and launchd_log.is_symlink()
            and outside_log.read_bytes() == launchd_marker,
            "service install did not fail closed on a symlinked launchd log",
            failures,
        )
        _, unsafe_log_check, _ = run(env, "service-check", "--service-path", str(service_path))
        require(
            unsafe_log_check.get("maintenance_status") == "attention"
            and unsafe_log_check.get("service_log_repair_available") is False
            and unsafe_log_check.get("manual_recovery_required") is True
            and unsafe_log_check.get("maintenance_next_action") is None,
            "service check advertised automatic repair for an unsafe launchd log identity",
            failures,
        )
        launchd_log.unlink()
        outside_log.chmod(0o644)
        os.link(outside_log, launchd_log)
        _, hardlinked_log_rotation, _ = run(env, "log-rotate", expected=(1,))
        require(
            hardlinked_log_rotation.get("error") == "host_log_inventory_permissions_unsafe"
            and hardlinked_log_rotation.get("service_log_repair_candidate") is False
            and hardlinked_log_rotation.get("manual_recovery_required") is True
            and hardlinked_log_rotation.get("next_action") is None,
            "log rotation advertised automatic repair for a hardlinked launchd log",
            failures,
        )
        launchd_log.unlink()
        outside_log.unlink()
        launchd_log.write_bytes(launchd_marker)
        launchd_log.chmod(0o600)

        unavailable_env = {**env, "AGENTOPS_LAUNCHCTL_BIN": str(temp / "missing-launchctl")}
        _, unverified_control, _ = run(
            unavailable_env,
            "service-control",
            "--action",
            "load",
            "--service-path",
            str(service_path),
            "--confirm-control",
            expected=(1,),
        )
        require("launchctl_unavailable" in (unverified_control.get("blockers") or []), "control did not fail closed without launchctl", failures)
        _, unverified_remove, _ = run(
            unavailable_env,
            "service-remove",
            "--service-path",
            str(service_path),
            "--confirm-remove",
            expected=(1,),
        )
        require("launchctl_state_unverified" in (unverified_remove.get("blockers") or []), "remove did not fail closed without launchctl", failures)
        require(service_path.exists(), "unverified removal deleted the service file", failures)

        calls_before = launchctl_calls.read_text(encoding="utf-8").splitlines() if launchctl_calls.exists() else []
        _, load_preview, _ = run(env, "service-control", "--action", "load", "--service-path", str(service_path))
        calls_after = launchctl_calls.read_text(encoding="utf-8").splitlines()
        require(load_preview.get("dry_run") is True and load_preview.get("service_mutated") is False, "load preview mutated service state", failures)
        require(not launchctl_state.exists(), "load preview marked the service loaded", failures)
        require(calls_after == calls_before + [f"print gui/{os.getuid()}/dev.agentops.mis.private-host"], "load preview executed more than read-only status", failures)

        _, loaded, _ = run(
            env,
            "service-control",
            "--action",
            "load",
            "--service-path",
            str(service_path),
            "--confirm-control",
        )
        require(loaded.get("service_mutated") is True and launchctl_state.exists(), f"confirmed load failed: {loaded}", failures)
        _, loaded_again, _ = run(
            env,
            "service-control",
            "--action",
            "load",
            "--service-path",
            str(service_path),
            "--confirm-control",
        )
        require(loaded_again.get("service_control_skipped") is True, "confirmed duplicate load was not idempotent", failures)

        _, remove_blocked, _ = run(
            env,
            "service-remove",
            "--service-path",
            str(service_path),
            "--confirm-remove",
            expected=(1,),
        )
        require("service_still_loaded" in (remove_blocked.get("blockers") or []), "loaded service removal did not fail closed", failures)
        require(service_path.exists(), "loaded service file was removed", failures)

        _, restarted, _ = run(
            env,
            "service-control",
            "--action",
            "restart",
            "--service-path",
            str(service_path),
            "--confirm-control",
        )
        require(restarted.get("service_mutated") is True, f"confirmed restart failed: {restarted}", failures)
        stale_then_converged_env = {**env, "AGENTOPS_TEST_LAUNCHCTL_STALE_UNLOAD_READS": "1"}
        _, unloaded, _ = run(
            stale_then_converged_env,
            "service-control",
            "--action",
            "unload",
            "--service-path",
            str(service_path),
            "--confirm-control",
        )
        require(unloaded.get("service_mutated") is True and not launchctl_state.exists(), f"confirmed unload failed: {unloaded}", failures)
        require(not (unloaded.get("blockers") or []), f"stale unload read did not converge: {unloaded}", failures)
        require(
            unloaded.get("service_state_after", {}).get("converged") is True
            and unloaded.get("service_state_after", {}).get("verification_attempts") == 2,
            f"stale unload read did not require one bounded retry: {unloaded}",
            failures,
        )

        launchctl_state.touch(mode=0o600)
        never_converged_env = {**env, "AGENTOPS_TEST_LAUNCHCTL_STALE_UNLOAD_READS": "99"}
        _, never_converged, _ = run(
            never_converged_env,
            "service-control",
            "--action",
            "unload",
            "--service-path",
            str(service_path),
            "--confirm-control",
            expected=(1,),
        )
        require(
            "launchctl_state_verification_failed" in (never_converged.get("blockers") or []),
            f"never-converging unload did not fail closed: {never_converged}",
            failures,
        )
        require(never_converged.get("service_mutated") is False, f"unverified mutation was reported as successful: {never_converged}", failures)
        require(
            never_converged.get("service_state_after", {}).get("converged") is False
            and never_converged.get("service_state_after", {}).get("verification_attempts") == 4,
            f"never-converging unload was not bounded: {never_converged}",
            failures,
        )
        require(not launchctl_state.exists(), "fake bootout did not mutate its underlying state", failures)
        launchctl_stale_reads.unlink(missing_ok=True)
        _, remove_preview, _ = run(env, "service-remove", "--service-path", str(service_path))
        require(remove_preview.get("dry_run") is True and service_path.exists(), "remove preview deleted the service", failures)
        _, removed, _ = run(env, "service-remove", "--service-path", str(service_path), "--confirm-remove")
        require(removed.get("removed") is True and not service_path.exists(), "confirmed service removal failed", failures)

        service_path.parent.mkdir(parents=True, exist_ok=True)
        service_path.write_text("unmanaged service", encoding="utf-8")
        service_path.chmod(0o600)
        launchd_log.chmod(0o644)
        _, unmanaged_check, _ = run(
            env,
            "service-check",
            "--service-path",
            str(service_path),
            expected=(1,),
        )
        require(
            unmanaged_check.get("service_log_repair_available") is False
            and unmanaged_check.get("manual_recovery_required") is True
            and unmanaged_check.get("maintenance_next_action") is None,
            "service check advertised log repair through an unowned service definition",
            failures,
        )
        launchd_log.chmod(0o600)
        _, refused, _ = run(
            env,
            "service-install",
            "--service-path",
            str(service_path),
            "--confirm-install",
            "--overwrite",
            expected=(1,),
        )
        require("existing_service_not_owned" in (refused.get("blockers") or []), "unmanaged service overwrite did not fail closed", failures)
        require(
            refused.get("next_action") is None
            and refused.get("manual_recovery_required") is True,
            "unmanaged service overwrite advertised an unsafe automatic next action",
            failures,
        )
        require(service_path.read_text(encoding="utf-8") == "unmanaged service", "unmanaged service file was overwritten", failures)

        write_private_service(service_path, previous_service)
        migration_code, migrated, _ = run(
            env,
            "service-install",
            "--service-path",
            str(service_path),
            "--confirm-install",
            "--overwrite",
            expected=(0, 1),
        )
        require(migration_code == 0, f"unloaded previous managed service migration failed: {migrated}", failures)
        require(migrated.get("wrote") is True, "unloaded previous managed service migration did not write the current template", failures)
        require(not (migrated.get("blockers") or []), f"unloaded previous managed service migration returned blockers: {migrated}", failures)
        require(
            migrated.get("service_check", {}).get("service_file", {}).get("exact_managed_definition") is True,
            "unloaded previous managed service migration did not produce the exact current definition",
            failures,
        )

        write_private_service(service_path, previous_service)
        service_path.chmod(0o644)
        _, public_mode_refused, _ = run(
            env,
            "service-install",
            "--service-path",
            str(service_path),
            "--confirm-install",
            "--overwrite",
            expected=(1,),
        )
        require("existing_service_not_owned" in (public_mode_refused.get("blockers") or []), "non-private previous service overwrite did not fail closed", failures)
        require(service_path.read_bytes() == previous_service, "non-private previous service changed on refusal", failures)

        write_private_service(service_path, previous_service)
        service_path.parent.chmod(0o777)
        _, unsafe_parent_refused, _ = run(
            env,
            "service-install",
            "--service-path",
            str(service_path),
            "--confirm-install",
            "--overwrite",
            expected=(1,),
        )
        require("existing_service_not_owned" in (unsafe_parent_refused.get("blockers") or []), "unsafe service directory overwrite did not fail closed", failures)
        require(service_path.read_bytes() == previous_service, "service in unsafe directory changed on refusal", failures)
        service_path.parent.chmod(0o700)

        write_private_service(service_path, previous_service)
        _, unverified_migration, _ = run(
            unavailable_env,
            "service-install",
            "--service-path",
            str(service_path),
            "--confirm-install",
            "--overwrite",
            expected=(1,),
        )
        require("launchctl_state_unverified" in (unverified_migration.get("blockers") or []), "legacy migration did not fail closed without launchctl", failures)
        require(service_path.read_bytes() == previous_service, "legacy service changed without verified launchctl state", failures)

        arbitrary_service = replace_service_fragment(
            previous_service,
            b"      <string>agentops_mis_cli</string>",
            b"      <string>agentops_unowned_cli</string>",
        )
        write_private_service(service_path, arbitrary_service)
        _, arbitrary_refused, _ = run(
            env,
            "service-install",
            "--service-path",
            str(service_path),
            "--confirm-install",
            "--overwrite",
            expected=(1,),
        )
        require("existing_service_not_owned" in (arbitrary_refused.get("blockers") or []), "arbitrary same-label service overwrite did not fail closed", failures)
        require(service_path.read_bytes() == arbitrary_service, "arbitrary same-label service was overwritten", failures)

        credential_marker = "agt" + "host_fixture_not_a_secret"
        environment_open = b"  <key>EnvironmentVariables</key>\n  <dict>\n"
        credential_service = replace_service_fragment(
            previous_service,
            environment_open,
            environment_open
            + b"      <key>AGENTOPS_API_KEY</key>\n"
            + f"      <string>{credential_marker}</string>\n".encode("utf-8"),
        )
        write_private_service(service_path, credential_service)
        _, credential_refused, _ = run(
            env,
            "service-install",
            "--service-path",
            str(service_path),
            "--confirm-install",
            "--overwrite",
            expected=(1,),
        )
        require("existing_service_not_owned" in (credential_refused.get("blockers") or []), "credential-bearing service overwrite did not fail closed", failures)
        require(service_path.read_bytes() == credential_service, "credential-bearing service was overwritten", failures)

        outside_root = temp.parent / "outside-agentops-install"
        escaped_service = replace_service_fragment(
            previous_service,
            str(ROOT).encode("utf-8"),
            str(outside_root).encode("utf-8"),
            count=3,
        )
        write_private_service(service_path, escaped_service)
        _, escaped_refused, _ = run(
            env,
            "service-install",
            "--service-path",
            str(service_path),
            "--confirm-install",
            "--overwrite",
            expected=(1,),
        )
        require("existing_service_not_owned" in (escaped_refused.get("blockers") or []), "path-escaping service overwrite did not fail closed", failures)
        require(service_path.read_bytes() == escaped_service, "path-escaping service was overwritten", failures)

        write_private_service(service_path, previous_service)
        launchctl_state.touch(mode=0o600)
        _, loaded_previous_refused, _ = run(
            env,
            "service-install",
            "--service-path",
            str(service_path),
            "--confirm-install",
            "--overwrite",
            expected=(1,),
        )
        require(loaded_previous_refused.get("wrote") is False, "loaded previous managed service was overwritten", failures)
        require(
            "legacy_service_still_loaded" in (loaded_previous_refused.get("blockers") or []),
            f"loaded previous managed service did not return the exact blocker: {loaded_previous_refused}",
            failures,
        )
        require(service_path.read_bytes() == previous_service, "loaded previous managed service changed on refusal", failures)

        _, legacy_load_refused, _ = run(
            env,
            "service-control",
            "--action",
            "load",
            "--service-path",
            str(service_path),
            "--confirm-control",
            expected=(1,),
        )
        require("managed_service_check_failed" in (legacy_load_refused.get("blockers") or []), "current CLI allowed loading a legacy service", failures)

        _, legacy_restart_refused, _ = run(
            env,
            "service-control",
            "--action",
            "restart",
            "--service-path",
            str(service_path),
            "--confirm-control",
            expected=(1,),
        )
        require("managed_service_check_failed" in (legacy_restart_refused.get("blockers") or []), "current CLI allowed restarting a legacy service", failures)

        _, legacy_unloaded, _ = run(
            env,
            "service-control",
            "--action",
            "unload",
            "--service-path",
            str(service_path),
            "--confirm-control",
        )
        require(legacy_unloaded.get("legacy_unload") is True, f"legacy service unload was not explicitly classified: {legacy_unloaded}", failures)
        require(legacy_unloaded.get("service_mutated") is True and not launchctl_state.exists(), f"legacy service unload failed: {legacy_unloaded}", failures)
        require(service_path.read_bytes() == previous_service, "legacy unload changed the service file", failures)

        _, post_unload_migrated, _ = run(
            env,
            "service-install",
            "--service-path",
            str(service_path),
            "--confirm-install",
            "--overwrite",
        )
        require(post_unload_migrated.get("wrote") is True, f"legacy service did not migrate after unload: {post_unload_migrated}", failures)
        require(
            post_unload_migrated.get("service_check", {}).get("service_file", {}).get("exact_managed_definition") is True,
            "post-unload migration did not produce the current service definition",
            failures,
        )

    result = {
        "ok": not failures,
        "failures": failures,
        "preview_first_install": True,
        "explicit_control_confirmation": True,
        "host_only_no_workers": True,
        "credential_material_omitted": True,
        "launchd_log_private_preparation_covered": True,
        "launchd_log_repair_and_load_gate_covered": True,
        "unmanaged_file_overwrite_blocked": True,
        "previous_managed_service_migration_covered": True,
        "previous_managed_service_unload_only_control_covered": True,
        "post_mutation_state_convergence_covered": True,
        "post_mutation_state_never_converges_fail_closed": True,
        "same_label_credential_path_mode_state_and_loaded_rejections_covered": True,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
