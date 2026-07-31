#!/usr/bin/env python3
"""Verify Host storage checks are deterministic, pre-write, and fail closed.

The installer-facing fixture is intentionally a test contract. Production code may
consume ``AGENTOPS_BUNDLE_INSTALLER_TEST_STORAGE_JSON`` only while explicit bundle
installer test mode is enabled. Each entry binds one managed path to a synthetic
device and free-byte count so split-volume behavior can be tested without mounts or
large files.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agentops_mis_cli import host


TEST_MODE_ENV = "AGENTOPS_BUNDLE_INSTALLER_TEST_MODE"
TEST_STORAGE_ENV = "AGENTOPS_BUNDLE_INSTALLER_TEST_STORAGE_JSON"
LEGACY_TEST_FREE_ENV = "AGENTOPS_BUNDLE_INSTALLER_TEST_FREE_BYTES"
HIGH_TEST_FREE_BYTES = host.HOST_STORAGE_MIN_FREE_BYTES + (64 * 1024 * 1024)


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def fake_usage(free_bytes: int):
    return lambda _path: SimpleNamespace(total=free_bytes * 2, used=free_bytes, free=free_bytes)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot_tree(root: Path) -> dict[str, tuple]:
    """Capture content/type state while ignoring access and directory timestamps."""
    snapshot: dict[str, tuple] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            snapshot[relative] = ("symlink", os.readlink(path))
        elif path.is_file():
            snapshot[relative] = ("file", path.stat().st_mode & 0o777, path.stat().st_size, digest(path))
        elif path.is_dir():
            snapshot[relative] = ("directory", path.stat().st_mode & 0o777)
        else:
            snapshot[relative] = ("other", path.lstat().st_mode)
    return snapshot


def create_upgrade_fixture(root: Path) -> dict[str, Path]:
    home = root / "home"
    home.mkdir(parents=True)
    fake_bin = root / "test-bin"
    fake_bin.mkdir()
    uname = fake_bin / "uname"
    uname.write_text("#!/bin/sh\nprintf '%s\\n' Darwin\n", encoding="utf-8")
    uname.chmod(0o755)

    bundle = root / "bundle"
    payload = bundle / "payload"
    payload.mkdir(parents=True)
    installer = bundle / "install.sh"
    shutil.copy2(ROOT / "packaging" / "macos" / "install.sh", installer)
    (payload / "fixture.txt").write_text("bounded storage fixture\n", encoding="utf-8")
    launcher = payload / "packaging" / "macos" / "launcher.py"
    launcher.parent.mkdir(parents=True)
    shutil.copy2(ROOT / "packaging" / "macos" / "launcher.py", launcher)
    files = []
    for path in sorted(candidate for candidate in bundle.rglob("*") if candidate.is_file()):
        relative = path.relative_to(bundle).as_posix()
        files.append({"path": relative, "size": path.stat().st_size, "sha256": digest(path)})
    (bundle / "manifest.json").write_text(
        json.dumps({"version": "storage-preflight-next", "files": files}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    install_root = home / ".local" / "share" / "agentops-mis"
    old_release = install_root / "versions" / "storage-preflight-old"
    old_release.mkdir(parents=True)
    (old_release / "release-manifest.json").write_text(
        json.dumps({"product": "AgentOps MIS Private Host", "version": "storage-preflight-old"}) + "\n",
        encoding="utf-8",
    )
    backup_utility = old_release / "scripts" / "agentops_local_backup.py"
    backup_utility.parent.mkdir(parents=True)
    backup_utility.write_text(
        "import json\nprint(json.dumps({'ok': False, 'error': 'backup_fixture_must_not_run'}))\nraise SystemExit(83)\n",
        encoding="utf-8",
    )
    (install_root / "current").symlink_to(old_release)
    (install_root / ".agentops-mis-install.json").write_text(
        json.dumps({"schema_version": 1, "product": "AgentOps MIS Private Host", "managed": True}) + "\n",
        encoding="utf-8",
    )
    data_root = home / ".agentops" / "host"
    database = data_root / "data" / "agentops_mis.db"
    database.parent.mkdir(parents=True)
    database.write_bytes(b"storage-preflight-fixture")
    return {
        "root": root,
        "home": home,
        "fake_bin": fake_bin,
        "bundle": bundle,
        "install_root": install_root,
        "bin_dir": home / ".local" / "bin",
        "data_root": data_root,
        "backup_dir": data_root / "backups",
        "app_dir": home / "Applications",
        "database": database,
        "wal": database.with_name(database.name + "-wal"),
        "old_release": old_release,
        "lifecycle_lock": data_root.parent / ".agentops-mis-host-lifecycle.lock",
    }


def storage_fixture_payload(
    fixture: dict[str, Path],
    *,
    free_by_role: dict[str, int] | None = None,
    device_by_role: dict[str, str] | None = None,
) -> str:
    free = {role: HIGH_TEST_FREE_BYTES for role in ("install", "data", "bin", "app")}
    free.update(free_by_role or {})
    devices = {role: f"fixture-{role}-device" for role in free}
    devices.update(device_by_role or {})
    role_paths = {
        "install": [fixture["install_root"]],
        "data": [fixture["data_root"].parent, fixture["data_root"], fixture["backup_dir"]],
        "bin": [fixture["bin_dir"]],
        "app": [fixture["app_dir"]],
    }
    paths = []
    for role, candidates in role_paths.items():
        for path in candidates:
            paths.append({
                "path": str(path.absolute()),
                "device_id": devices[role],
                "free_bytes": int(free[role]),
                "storage_role": role,
            })
    return json.dumps({"schema_version": 1, "paths": paths}, sort_keys=True)


def run_installer(
    fixture: dict[str, Path],
    *,
    free_by_role: dict[str, int] | None = None,
    test_mode: bool = True,
    minimum: str | None = None,
    install_app: bool = True,
    legacy_free_bytes: int | None = None,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    for name in (TEST_MODE_ENV, TEST_STORAGE_ENV, LEGACY_TEST_FREE_ENV, host.HOST_STORAGE_MIN_FREE_ENV):
        environment.pop(name, None)
    environment.update({
        "HOME": str(fixture["home"]),
        "PATH": f"{fixture['fake_bin']}{os.pathsep}{environment.get('PATH', '')}",
        "AGENTOPS_INSTALL_ROOT": str(fixture["install_root"]),
        "AGENTOPS_BIN_DIR": str(fixture["bin_dir"]),
        "AGENTOPS_HOST_HOME": str(fixture["data_root"]),
        "AGENTOPS_APP_DIR": str(fixture["app_dir"]),
        TEST_STORAGE_ENV: storage_fixture_payload(fixture, free_by_role=free_by_role),
    })
    if test_mode:
        environment[TEST_MODE_ENV] = "1"
    if install_app:
        environment.pop("AGENTOPS_NO_APP_INSTALL", None)
    else:
        environment["AGENTOPS_NO_APP_INSTALL"] = "1"
    if minimum is not None:
        environment[host.HOST_STORAGE_MIN_FREE_ENV] = minimum
    if legacy_free_bytes is not None:
        environment[LEGACY_TEST_FREE_ENV] = str(legacy_free_bytes)
    return subprocess.run(
        ["sh", str(fixture["bundle"] / "install.sh")],
        cwd=fixture["bundle"],
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def installer_error(process: subprocess.CompletedProcess[str]) -> dict:
    lines = [line for line in process.stderr.splitlines() if line.strip()]
    if not lines:
        return {}
    try:
        payload = json.loads(lines[-1])
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def record_matches_role(record: dict, fixture: dict[str, Path], role: str) -> bool:
    roles = record.get("storage_roles")
    if isinstance(roles, list) and role in roles:
        return True
    if record.get("storage_role") == role:
        return True
    target_kinds = record.get("target_kinds")
    expected_kinds = {"backup", "data"} if role == "data" else {role}
    if isinstance(target_kinds, list) and expected_kinds.intersection(target_kinds):
        return True
    expected = {
        "install": {fixture["install_root"].absolute()},
        "data": {
            fixture["data_root"].parent.absolute(),
            fixture["data_root"].absolute(),
            fixture["backup_dir"].absolute(),
        },
        "bin": {fixture["bin_dir"].absolute()},
        "app": {fixture["app_dir"].absolute()},
    }[role]
    raw_path = record.get("filesystem_path")
    return bool(raw_path) and Path(str(raw_path)).absolute() in expected


def assert_preflight_failure(
    process: subprocess.CompletedProcess[str],
    fixture: dict[str, Path],
    role: str,
    failures: list[str],
    label: str,
) -> dict:
    error = installer_error(process)
    require(process.returncode != 0, f"{label}: installer unexpectedly succeeded", failures)
    require(error.get("operation") == "host_bundle_storage_preflight", f"{label}: bounded storage error missing", failures)
    require(error.get("status") == "insufficient_free_space", f"{label}: wrong low-space status", failures)
    require(record_matches_role(error, fixture, role), f"{label}: failure was not bound to the {role} volume", failures)
    require(int(error.get("planned_write_bytes") or 0) > 0, f"{label}: {role} planned writes were not budgeted", failures)
    return error


def assert_upgrade_untouched(fixture: dict[str, Path], failures: list[str], label: str) -> None:
    target = fixture["install_root"] / "versions" / "storage-preflight-next"
    current = fixture["install_root"] / "current"
    require(not target.exists(), f"{label}: installer wrote the new version", failures)
    require(current.resolve() == fixture["old_release"].resolve(), f"{label}: current version changed", failures)
    require(not fixture["backup_dir"].exists(), f"{label}: pre-update backup started", failures)
    require(not fixture["lifecycle_lock"].exists(), f"{label}: lifecycle lock was created before storage passed", failures)
    require(
        fixture["database"].stat().st_size == len(b"storage-preflight-fixture"),
        f"{label}: fixture ledger metadata changed",
        failures,
    )


def require_snapshot_unchanged(
    before: dict[str, tuple],
    fixture: dict[str, Path],
    failures: list[str],
    label: str,
) -> None:
    require(snapshot_tree(fixture["root"]) == before, f"{label}: storage failure changed the fixture tree", failures)


def main() -> int:
    failures: list[str] = []
    floor = host.HOST_STORAGE_MIN_FREE_BYTES
    with tempfile.TemporaryDirectory(prefix="agentops-host-storage-preflight-") as tmp:
        root = Path(tmp)
        target = root / "not-yet-created" / "install"
        ready = host.host_storage_preflight(target_path=target, disk_usage=fake_usage(floor), environ={})
        low = host.host_storage_preflight(target_path=target, disk_usage=fake_usage(floor - 1), environ={})
        raised = host.host_storage_preflight(
            target_path=target,
            minimum_free_bytes=floor + 4096,
            disk_usage=fake_usage(floor + 4096),
            environ={},
        )
        lowered = host.host_storage_preflight(
            target_path=target,
            minimum_free_bytes=1,
            disk_usage=fake_usage(floor * 2),
            environ={},
        )
        unavailable = host.host_storage_preflight(
            target_path=target,
            disk_usage=lambda _path: (_ for _ in ()).throw(OSError("fixture unavailable")),
            environ={},
        )

        require(ready.get("ok") is True and ready.get("status") == "ready", "exact floor did not pass", failures)
        require(ready.get("free_bytes") == floor and ready.get("required_bytes") == floor, "ready byte accounting is wrong", failures)
        require(low.get("ok") is False and low.get("status") == "insufficient_free_space", "low free space did not fail", failures)
        require(raised.get("ok") is True and raised.get("required_bytes") == floor + 4096, "raised threshold was not honored", failures)
        require(
            lowered.get("ok") is False
            and lowered.get("status") == "threshold_below_production_floor"
            and lowered.get("required_bytes") == floor,
            "lower threshold did not fail closed",
            failures,
        )
        require(unavailable.get("status") == "storage_unavailable", "disk-usage failure did not fail closed", failures)
        for payload in (ready, low, raised, lowered, unavailable):
            require(bool(payload.get("filesystem_path")), "filesystem path is missing", failures)
            require(payload.get("token_omitted") is True, "token omission marker is missing", failures)
            require(payload.get("read_only") is True, "preflight is not marked read-only", failures)
            require(payload.get("network_used") is False, "preflight claims network use", failures)
            require(payload.get("database_content_read") is False, "preflight claims ledger content access", failures)
            require(payload.get("credentials_read") is False, "preflight claims credential access", failures)

        parsed = host.build_parser().parse_args(["storage-preflight"])
        require(parsed.handler is host.cmd_storage_preflight, "storage-preflight CLI route is missing", failures)

        low_fixture = create_upgrade_fixture(root / "low")
        low_before = snapshot_tree(low_fixture["root"])
        low_process = run_installer(
            low_fixture,
            free_by_role={role: 1 for role in ("install", "data", "bin", "app")},
        )
        low_error = assert_preflight_failure(low_process, low_fixture, "install", failures, "low space")
        require(low_error.get("free_bytes") == 1, "low space: deterministic fixture was not applied", failures)
        require(low_error.get("minimum_free_bytes") == floor, "CLI and installer production floors differ", failures)
        require(low_error.get("token_omitted") is True, "installer omission marker is missing", failures)
        assert_upgrade_untouched(low_fixture, failures, "low space")
        require_snapshot_unchanged(low_before, low_fixture, failures, "low space")
        low_zero_write = (
            not low_fixture["lifecycle_lock"].exists()
            and snapshot_tree(low_fixture["root"]) == low_before
        )

        split_volume_results: dict[str, bool] = {}
        for role in ("install", "data", "bin", "app"):
            split_fixture = create_upgrade_fixture(root / f"split-{role}")
            split_before = snapshot_tree(split_fixture["root"])
            split_free = {name: HIGH_TEST_FREE_BYTES for name in ("install", "data", "bin", "app")}
            split_free[role] = floor
            split_process = run_installer(split_fixture, free_by_role=split_free)
            split_error = assert_preflight_failure(
                split_process,
                split_fixture,
                role,
                failures,
                f"split {role} volume",
            )
            split_volume_results[role] = (
                split_error.get("free_bytes") == floor and record_matches_role(split_error, split_fixture, role)
            )
            require(
                split_error.get("free_bytes") == floor,
                f"split {role} volume: deterministic per-device capacity was not applied",
                failures,
            )
            assert_upgrade_untouched(split_fixture, failures, f"split {role} volume")
            require_snapshot_unchanged(split_before, split_fixture, failures, f"split {role} volume")

        wal_fixture = create_upgrade_fixture(root / "wal")
        wal_fixture["database"].write_bytes(b"d")
        wal_fixture["wal"].write_bytes(b"w" * (64 * 1024))
        wal_before = snapshot_tree(wal_fixture["root"])
        wal_source_bytes = wal_fixture["database"].stat().st_size + wal_fixture["wal"].stat().st_size
        wal_free = floor + (wal_source_bytes * 2) - 1
        wal_process = run_installer(wal_fixture, free_by_role={"data": wal_free})
        wal_error = assert_preflight_failure(wal_process, wal_fixture, "data", failures, "WAL reserve")
        require(wal_error.get("free_bytes") == wal_free, "WAL reserve: deterministic data capacity was not applied", failures)
        require(
            int(wal_error.get("planned_write_bytes") or 0) >= wal_source_bytes * 2,
            "WAL reserve: planned writes did not include db-wal bytes",
            failures,
        )
        require(not wal_fixture["lifecycle_lock"].exists(), "WAL reserve: lifecycle lock was created early", failures)
        require_snapshot_unchanged(wal_before, wal_fixture, failures, "WAL reserve")

        symlink_fixture = create_upgrade_fixture(root / "symlink-backups")
        external_backup = symlink_fixture["root"] / "external-backup-volume"
        external_backup.mkdir()
        symlink_fixture["backup_dir"].symlink_to(external_backup, target_is_directory=True)
        symlink_before = snapshot_tree(symlink_fixture["root"])
        symlink_process = run_installer(symlink_fixture)
        symlink_error = installer_error(symlink_process)
        symlink_message = symlink_process.stderr.lower()
        require(symlink_process.returncode != 0, "symlinked backups directory unexpectedly passed", failures)
        require(
            symlink_error.get("status") == "unsafe_backup_directory"
            or ("unsafe" in symlink_message and "backup" in symlink_message),
            "symlinked backups directory was not rejected for its unsafe topology",
            failures,
        )
        require(not any(external_backup.iterdir()), "symlinked backups directory received a write", failures)
        require(not symlink_fixture["lifecycle_lock"].exists(), "symlink rejection created the lifecycle lock", failures)
        require_snapshot_unchanged(symlink_before, symlink_fixture, failures, "symlinked backups")

        retryable_failures: dict[str, bool] = {}
        for role, failing_path in (
            ("bin", "bin_dir"),
            ("app", "app_dir"),
        ):
            retry_fixture = create_upgrade_fixture(root / f"retry-{role}")
            retry_fixture["database"].unlink()
            blocked_path = retry_fixture[failing_path]
            blocked_path.parent.mkdir(parents=True, exist_ok=True)
            blocked_path.write_text("fixture path blocks directory creation\n", encoding="utf-8")
            failed_install = run_installer(retry_fixture)
            retry_target = retry_fixture["install_root"] / "versions" / "storage-preflight-next"
            retry_current = retry_fixture["install_root"] / "current"
            require(failed_install.returncode != 0, f"{role} write failure unexpectedly succeeded", failures)
            require(not retry_target.exists(), f"{role} write failure left an unretryable target", failures)
            require(
                retry_current.resolve() == retry_fixture["old_release"].resolve(),
                f"{role} write failure switched current",
                failures,
            )
            require(
                not (retry_fixture["bin_dir"] / "agentops").exists()
                and not (retry_fixture["bin_dir"] / "agentops-worker").exists(),
                f"{role} write failure left new shims",
                failures,
            )
            require(not retry_fixture["app_dir"].is_dir(), f"{role} write failure left a new App directory", failures)
            blocked_path.unlink()
            retried_install = run_installer(retry_fixture)
            retryable_failures[role] = (
                retried_install.returncode == 0
                and retry_target.is_dir()
                and retry_current.resolve() == retry_target.resolve()
            )
            require(retryable_failures[role], f"{role} write failure could not be retried", failures)
        symlink_rejected_safely = (
            symlink_process.returncode != 0
            and (
                symlink_error.get("status") == "unsafe_backup_directory"
                or ("unsafe" in symlink_message and "backup" in symlink_message)
            )
            and not any(external_backup.iterdir())
            and snapshot_tree(symlink_fixture["root"]) == symlink_before
        )

        lowered_fixture = create_upgrade_fixture(root / "lowered-threshold")
        lowered_before = snapshot_tree(lowered_fixture["root"])
        lowered_process = run_installer(lowered_fixture, minimum="1")
        lowered_error = installer_error(lowered_process)
        require(lowered_process.returncode != 0, "lowered production threshold unexpectedly succeeded", failures)
        require(
            lowered_error.get("status") == "threshold_below_production_floor",
            "installer accepted a threshold below the production floor",
            failures,
        )
        assert_upgrade_untouched(lowered_fixture, failures, "lowered threshold")
        require_snapshot_unchanged(lowered_before, lowered_fixture, failures, "lowered threshold")

        production_fixture = create_upgrade_fixture(root / "production-test-fixture")
        production_before = snapshot_tree(production_fixture["root"])
        production_process = run_installer(production_fixture, test_mode=False)
        production_error = installer_error(production_process)
        require(production_process.returncode != 0, "production accepted the synthetic storage fixture", failures)
        require(
            production_error.get("status") == "test_storage_fixture_requires_test_mode",
            "synthetic storage fixture was not explicitly disabled outside test mode",
            failures,
        )
        assert_upgrade_untouched(production_fixture, failures, "production fixture")
        require_snapshot_unchanged(production_before, production_fixture, failures, "production fixture")
        production_fixture_disabled = (
            production_error.get("status") == "test_storage_fixture_requires_test_mode"
            and snapshot_tree(production_fixture["root"]) == production_before
        )

        legacy_fixture = create_upgrade_fixture(root / "legacy-increased-capacity")
        legacy_before = snapshot_tree(legacy_fixture["root"])
        legacy_process = run_installer(legacy_fixture, legacy_free_bytes=2**63 - 1)
        legacy_error = installer_error(legacy_process)
        require(legacy_process.returncode != 0, "legacy override increased observed capacity", failures)
        require(
            legacy_error.get("status") == "test_free_space_override_may_not_increase_capacity",
            "legacy capacity-increasing override was not rejected",
            failures,
        )
        assert_upgrade_untouched(legacy_fixture, failures, "legacy increased capacity")
        require_snapshot_unchanged(legacy_before, legacy_fixture, failures, "legacy increased capacity")
        legacy_override_rejected = (
            legacy_error.get("status") == "test_free_space_override_may_not_increase_capacity"
            and snapshot_tree(legacy_fixture["root"]) == legacy_before
        )

    print(json.dumps({
        "ok": not failures,
        "operation": "private_host_storage_preflight_smoke",
        "failures": failures,
        "production_floor_bytes": floor,
        "cli_ready_fixture_passed": ready.get("ok") is True,
        "cli_low_space_failed_closed": low.get("status") == "insufficient_free_space",
        "installer_low_space_failed_closed": low_error.get("status") == "insufficient_free_space",
        "preflight_failure_is_zero_write": low_zero_write,
        "split_volume_results": split_volume_results,
        "retryable_write_failures": retryable_failures,
        "wal_source_bytes": wal_source_bytes,
        "wal_planned_write_bytes": wal_error.get("planned_write_bytes"),
        "symlinked_backups_rejected": symlink_rejected_safely,
        "test_fixture_disabled_in_production": production_fixture_disabled,
        "legacy_override_can_increase_capacity": not legacy_override_rejected,
        "database_content_read": False,
        "credentials_read": False,
        "network_used": False,
        "token_omitted": True,
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
