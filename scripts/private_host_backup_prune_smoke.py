#!/usr/bin/env python3
"""Define the fail-closed Host backup-prune CLI acceptance contract."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
from unittest import mock
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import agentops_local_backup  # noqa: E402


RAW_ROW_MARKER = "fixture-raw-ledger-row-must-not-print"
PLAN_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
DEFAULT_KEEP = 5
MINIMUM_KEEP = 2


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_host(env: dict[str, str], *arguments: str) -> tuple[subprocess.CompletedProcess[str], dict]:
    process = subprocess.run(
        [sys.executable, "-m", "agentops_mis_cli.cli", "host", *arguments],
        cwd=ROOT,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    try:
        payload = json.loads(process.stdout)
    except (TypeError, ValueError):
        payload = {}
    return process, payload if isinstance(payload, dict) else {}


def start_host(env: dict[str, str], *arguments: str) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, "-m", "agentops_mis_cli.cli", "host", *arguments],
        cwd=ROOT,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def parse_payload(stdout: str) -> dict:
    try:
        payload = json.loads(stdout)
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def create_sqlite(path: Path, marker: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version=1")
        connection.execute("CREATE TABLE fixture_records (id INTEGER PRIMARY KEY, payload TEXT NOT NULL)")
        connection.execute("INSERT INTO fixture_records(payload) VALUES (?)", (marker,))
    path.chmod(0o600)


def create_verified_pair(
    fixture: dict,
    index: int,
    *,
    directory: Path | None = None,
    with_sidecars: bool = False,
) -> dict:
    backup_dir = directory or fixture["backups"]
    backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = f"20260722T000000{index:06d}Z"
    backup_id = f"agentops-mis-{stamp}"
    database = backup_dir / f"{backup_id}.sqlite"
    manifest_path = backup_dir / f"{backup_id}.manifest.json"
    create_sqlite(database, f"{RAW_ROW_MARKER}:{index}")
    manifest = agentops_local_backup.backup_manifest(database, fixture["database"], stamp)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest_path.chmod(0o600)
    sidecars: list[Path] = []
    if with_sidecars:
        wal_path = Path(f"{database}-wal")
        shm_path = Path(f"{database}-shm")
        wal_path.write_bytes(b"")
        shm_path.write_bytes(b"\0" * 32768)
        wal_path.chmod(0o600)
        shm_path.chmod(0o600)
        sidecars = [wal_path, shm_path]
    timestamp = 1_700_000_000 + index
    os.utime(database, (timestamp, timestamp))
    os.utime(manifest_path, (timestamp, timestamp))
    for sidecar in sidecars:
        os.utime(sidecar, (timestamp, timestamp))
    return {
        "id": backup_id,
        "database": database,
        "manifest": manifest_path,
        "sidecars": sidecars,
        "bytes": (
            database.stat().st_size
            + manifest_path.stat().st_size
            + sum(sidecar.stat().st_size for sidecar in sidecars)
        ),
    }


def create_fixture(root: Path, *, pair_count: int = 7) -> dict:
    home = root / "home"
    host_home = home / ".agentops" / "host"
    install_root = home / ".local" / "share" / "agentops-mis"
    ui_dist = root / "ui-dist"
    ui_dist.mkdir(parents=True)
    (ui_dist / "index.html").write_text("<!doctype html><title>fixture</title>\n", encoding="utf-8")
    env = {
        "AGENTOPS_HOST_HOME": str(host_home),
        "AGENTOPS_INSTALL_ROOT": str(install_root),
        "HOME": str(home),
        "LANG": "C.UTF-8",
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": str(ROOT),
        "TMPDIR": str(root),
    }
    initialized, _payload = run_host(env, "init", "--ui-dist", str(ui_dist))
    if initialized.returncode != 0:
        raise RuntimeError("temporary Host initialization failed")

    database = host_home / "data" / "agentops_mis.db"
    create_sqlite(database, RAW_ROW_MARKER)
    backups = host_home / "backups"
    backups.mkdir(mode=0o700)
    log = host_home / "logs" / "host.log"
    log.write_text("fixture log sentinel\n", encoding="utf-8")
    log.chmod(0o600)

    release = install_root / "versions" / "fixture-v1"
    release.mkdir(parents=True)
    release_manifest = release / "release-manifest.json"
    release_manifest.write_text('{"version":"fixture-v1"}\n', encoding="utf-8")
    current = install_root / "current"
    current.symlink_to(release)

    fixture = {
        "root": root,
        "env": env,
        "host_home": host_home,
        "database": database,
        "backups": backups,
        "secrets": host_home / "secrets.json",
        "log": log,
        "install_root": install_root,
        "release_manifest": release_manifest,
        "current": current,
        "lifecycle_lock": host_home.parent / ".agentops-mis-host-lifecycle.lock",
    }
    fixture["secret_values"] = tuple(
        str(value)
        for value in json.loads(fixture["secrets"].read_text(encoding="utf-8")).values()
        if isinstance(value, str) and value
    )
    fixture["pairs"] = [create_verified_pair(fixture, index) for index in range(1, pair_count + 1)]
    return fixture


def snapshot_flat_directory(path: Path) -> dict[str, tuple]:
    if path.is_symlink():
        return {"@root": ("symlink", os.readlink(path))}
    snapshot: dict[str, tuple] = {}
    for entry in sorted(path.iterdir(), key=lambda item: item.name):
        metadata = entry.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            snapshot[entry.name] = ("symlink", os.readlink(entry))
        elif stat.S_ISREG(metadata.st_mode):
            snapshot[entry.name] = ("file", metadata.st_size, sha256(entry))
        elif stat.S_ISDIR(metadata.st_mode):
            snapshot[entry.name] = ("directory",)
        else:
            snapshot[entry.name] = ("other", stat.S_IFMT(metadata.st_mode))
    return snapshot


def snapshot_protected(fixture: dict) -> dict[str, tuple]:
    return {
        "database": (fixture["database"].stat().st_size, sha256(fixture["database"])),
        "secrets": (fixture["secrets"].stat().st_size, sha256(fixture["secrets"])),
        "log": (fixture["log"].stat().st_size, sha256(fixture["log"])),
        "current": ("symlink", os.readlink(fixture["current"])),
        "release_manifest": (
            fixture["release_manifest"].stat().st_size,
            sha256(fixture["release_manifest"]),
        ),
    }


def normalize_backup_id(value: object) -> str:
    candidate = Path(str(value)).name
    if candidate.endswith(".manifest.json"):
        return candidate[: -len(".manifest.json")]
    if candidate.endswith(".sqlite"):
        return candidate[: -len(".sqlite")]
    return candidate


def item_backup_id(item: object) -> str:
    if isinstance(item, str):
        return normalize_backup_id(item)
    if not isinstance(item, dict):
        return ""
    for key in ("backup_id", "id", "backup_file", "database", "path", "name"):
        if item.get(key):
            return normalize_backup_id(item[key])
    return ""


def output_is_safe(output: str, fixture: dict) -> bool:
    if RAW_ROW_MARKER in output:
        return False
    return not any(secret in output for secret in fixture["secret_values"])


def is_prune_payload(payload: dict, *, ok: bool) -> bool:
    return payload.get("operation") == "host_backup_prune" and payload.get("ok") is ok


def valid_dry_run_contract(payload: dict, pairs: list[dict], *, keep: int) -> bool:
    inventory = payload.get("inventory")
    retained = payload.get("retained")
    candidates = payload.get("candidates")
    counts = payload.get("counts")
    if not all(isinstance(value, list) for value in (inventory, retained, candidates)):
        return False
    if not isinstance(counts, dict):
        return False
    expected_retained = {pair["id"] for pair in pairs[-keep:]}
    expected_candidates = {pair["id"] for pair in pairs[:-keep]}
    inventory_ids = {item_backup_id(item) for item in inventory}
    retained_ids = {item_backup_id(item) for item in retained}
    candidate_ids = {item_backup_id(item) for item in candidates}
    reclaimable = sum(pair["bytes"] for pair in pairs[:-keep])
    return bool(
        is_prune_payload(payload, ok=True)
        and payload.get("dry_run") is True
        and payload.get("keep") == keep
        and PLAN_HASH_PATTERN.fullmatch(str(payload.get("plan_hash") or ""))
        and counts.get("inventory") == len(pairs)
        and counts.get("retained") == min(keep, len(pairs))
        and counts.get("candidates") == max(0, len(pairs) - keep)
        and payload.get("reclaimable_bytes") == reclaimable
        and inventory_ids == {pair["id"] for pair in pairs}
        and retained_ids == expected_retained
        and candidate_ids == expected_candidates
    )


def record(failures: list[str], evidence: dict[str, bool], name: str, passed: bool) -> None:
    evidence[name] = bool(passed)
    if not passed:
        failures.append(f"{name} failed")


def expect_rejected_without_mutation(
    fixture: dict,
    failures: list[str],
    evidence: dict[str, bool],
    name: str,
    *arguments: str,
    extra_snapshot=None,
) -> None:
    before = snapshot_flat_directory(fixture["backups"])
    protected_before = snapshot_protected(fixture)
    external_before = extra_snapshot() if extra_snapshot else None
    process, payload = run_host(fixture["env"], "backup-prune", *arguments)
    after = snapshot_flat_directory(fixture["backups"])
    protected_after = snapshot_protected(fixture)
    external_after = extra_snapshot() if extra_snapshot else None
    record(
        failures,
        evidence,
        name,
        process.returncode != 0
        and is_prune_payload(payload, ok=False)
        and before == after
        and protected_before == protected_after
        and external_before == external_after
        and output_is_safe((process.stdout or "") + (process.stderr or ""), fixture),
    )


def exercise_valid_inventory(root: Path, failures: list[str], evidence: dict[str, bool]) -> None:
    fixture = create_fixture(root / "valid")
    backups_before = snapshot_flat_directory(fixture["backups"])
    protected_before = snapshot_protected(fixture)

    first_process, first = run_host(fixture["env"], "backup-prune")
    second_process, second = run_host(fixture["env"], "backup-prune")
    record(
        failures,
        evidence,
        "default_dry_run",
        first_process.returncode == 0
        and second_process.returncode == 0
        and valid_dry_run_contract(first, fixture["pairs"], keep=DEFAULT_KEEP)
        and first.get("plan_hash") == second.get("plan_hash")
        and snapshot_flat_directory(fixture["backups"]) == backups_before
        and snapshot_protected(fixture) == protected_before
        and output_is_safe(first_process.stdout + first_process.stderr + second_process.stdout + second_process.stderr, fixture),
    )

    minimum_process, minimum = run_host(fixture["env"], "backup-prune", "--keep", str(MINIMUM_KEEP))
    record(
        failures,
        evidence,
        "minimum_keep_and_plan_binding",
        minimum_process.returncode == 0
        and valid_dry_run_contract(minimum, fixture["pairs"], keep=MINIMUM_KEEP)
        and minimum.get("plan_hash") != first.get("plan_hash")
        and snapshot_flat_directory(fixture["backups"]) == backups_before,
    )

    expect_rejected_without_mutation(
        fixture,
        failures,
        evidence,
        "keep_below_minimum_rejected",
        "--keep",
        str(MINIMUM_KEEP - 1),
    )
    expect_rejected_without_mutation(
        fixture,
        failures,
        evidence,
        "confirm_without_hash_rejected",
        "--confirm-prune",
    )
    hash_only_before = snapshot_flat_directory(fixture["backups"])
    hash_only_protected = snapshot_protected(fixture)
    hash_only_process, hash_only = run_host(
        fixture["env"],
        "backup-prune",
        "--plan-hash",
        str(first.get("plan_hash") or "0" * 64),
    )
    hash_only_safe_result = (
        hash_only_process.returncode == 0
        and valid_dry_run_contract(hash_only, fixture["pairs"], keep=DEFAULT_KEEP)
    ) or (
        hash_only_process.returncode != 0
        and is_prune_payload(hash_only, ok=False)
        and hash_only.get("dry_run") is True
    )
    record(
        failures,
        evidence,
        "hash_without_confirm_never_prunes",
        hash_only_safe_result
        and snapshot_flat_directory(fixture["backups"]) == hash_only_before
        and snapshot_protected(fixture) == hash_only_protected
        and output_is_safe(hash_only_process.stdout + hash_only_process.stderr, fixture),
    )
    expect_rejected_without_mutation(
        fixture,
        failures,
        evidence,
        "wrong_hash_rejected",
        "--confirm-prune",
        "--plan-hash",
        "f" * 64,
    )

    old_hash = str(first.get("plan_hash") or "0" * 64)
    fixture["pairs"].append(create_verified_pair(fixture, 8))
    expect_rejected_without_mutation(
        fixture,
        failures,
        evidence,
        "inventory_change_invalidates_old_hash",
        "--confirm-prune",
        "--plan-hash",
        old_hash,
    )

    fresh_process, fresh = run_host(fixture["env"], "backup-prune")
    fresh_hash = str(fresh.get("plan_hash") or "")
    fresh_contract = fresh_process.returncode == 0 and valid_dry_run_contract(
        fresh, fixture["pairs"], keep=DEFAULT_KEEP
    )
    lock_descriptor = os.open(fixture["lifecycle_lock"], os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
    fcntl.flock(lock_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    before_confirm = snapshot_flat_directory(fixture["backups"])
    process = start_host(
        fixture["env"],
        "backup-prune",
        "--confirm-prune",
        "--plan-hash",
        fresh_hash,
    )
    time.sleep(0.75)
    waited_for_lock = process.poll() is None and snapshot_flat_directory(fixture["backups"]) == before_confirm
    fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
    os.close(lock_descriptor)
    try:
        stdout, stderr = process.communicate(timeout=20)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate()
    confirmed = parse_payload(stdout)
    remaining = snapshot_flat_directory(fixture["backups"])
    remaining_ids = {
        normalize_backup_id(name)
        for name in remaining
        if name.endswith(".sqlite")
    }
    remaining_manifest_ids = {
        normalize_backup_id(name)
        for name in remaining
        if name.endswith(".manifest.json")
    }
    expected_remaining = {pair["id"] for pair in fixture["pairs"][-DEFAULT_KEEP:]}
    record(
        failures,
        evidence,
        "confirmed_prune_holds_lifecycle_lock",
        fresh_contract
        and waited_for_lock
        and process.returncode == 0
        and is_prune_payload(confirmed, ok=True)
        and confirmed.get("dry_run") is False
        and remaining_ids == expected_remaining
        and remaining_manifest_ids == expected_remaining
        and len(remaining) == DEFAULT_KEEP * 2
        and snapshot_protected(fixture) == protected_before
        and output_is_safe(stdout + stderr, fixture),
    )


def exercise_fresh_host(root: Path, failures: list[str], evidence: dict[str, bool]) -> None:
    fixture = create_fixture(root / "fresh-host", pair_count=0)
    fixture["backups"].rmdir()
    protected_before = snapshot_protected(fixture)
    process, payload = run_host(fixture["env"], "backup-prune")
    record(
        failures,
        evidence,
        "fresh_host_returns_empty_zero_write_plan",
        process.returncode == 0
        and is_prune_payload(payload, ok=True)
        and payload.get("dry_run") is True
        and payload.get("counts") == {"inventory": 0, "retained": 0, "candidates": 0}
        and payload.get("inventory") == []
        and payload.get("retained") == []
        and payload.get("candidates") == []
        and not fixture["backups"].exists()
        and snapshot_protected(fixture) == protected_before
        and output_is_safe((process.stdout or "") + (process.stderr or ""), fixture),
    )


def exercise_verified_sidecar_inventory(root: Path, failures: list[str], evidence: dict[str, bool]) -> None:
    fixture = create_fixture(root / "verified-sidecars", pair_count=0)
    fixture["pairs"] = [
        create_verified_pair(fixture, index, with_sidecars=True)
        for index in range(1, 8)
    ]
    before = snapshot_flat_directory(fixture["backups"])
    protected_before = snapshot_protected(fixture)
    preview_process, preview = run_host(fixture["env"], "backup-prune")
    after_preview = snapshot_flat_directory(fixture["backups"])
    plan_hash = str(preview.get("plan_hash") or "")
    confirm_process, confirmed = run_host(
        fixture["env"],
        "backup-prune",
        "--confirm-prune",
        "--plan-hash",
        plan_hash,
    )
    remaining = snapshot_flat_directory(fixture["backups"])
    expected_remaining = {pair["id"] for pair in fixture["pairs"][-DEFAULT_KEEP:]}
    remaining_ids = {
        normalize_backup_id(name)
        for name in remaining
        if name.endswith(".sqlite")
    }
    retained_sidecars = {
        name.removesuffix(".sqlite-wal").removesuffix(".sqlite-shm")
        for name in remaining
        if name.endswith(".sqlite-wal") or name.endswith(".sqlite-shm")
    }
    record(
        failures,
        evidence,
        "verified_zero_wal_sidecars_prune_with_backup_pair",
        preview_process.returncode == 0
        and valid_dry_run_contract(preview, fixture["pairs"], keep=DEFAULT_KEEP)
        and after_preview == before
        and confirm_process.returncode == 0
        and is_prune_payload(confirmed, ok=True)
        and confirmed.get("deleted_count") == 2
        and confirmed.get("deleted_file_count") == 8
        and remaining_ids == expected_remaining
        and retained_sidecars == expected_remaining
        and len(remaining) == DEFAULT_KEEP * 4
        and snapshot_protected(fixture) == protected_before
        and output_is_safe(preview_process.stdout + preview_process.stderr + confirm_process.stdout + confirm_process.stderr, fixture),
    )

    active_wal = create_fixture(root / "active-wal-sidecar", pair_count=0)
    active_wal["pairs"] = [
        create_verified_pair(active_wal, index, with_sidecars=True)
        for index in range(1, 7)
    ]
    Path(f"{active_wal['pairs'][0]['database']}-wal").write_bytes(b"uncheckpointed")
    expect_rejected_without_mutation(
        active_wal,
        failures,
        evidence,
        "nonempty_wal_sidecar_fails_closed",
    )

    orphan = create_fixture(root / "orphan-sidecar", pair_count=6)
    orphan_path = orphan["backups"] / "agentops-mis-20260722T235959999999Z.sqlite-shm"
    orphan_path.write_bytes(b"\0" * 32768)
    orphan_path.chmod(0o600)
    expect_rejected_without_mutation(
        orphan,
        failures,
        evidence,
        "orphan_sidecar_fails_closed",
    )


def exercise_invalid_inventory(root: Path, failures: list[str], evidence: dict[str, bool]) -> None:
    unsafe_permissions = create_fixture(root / "unsafe-permissions", pair_count=6)
    unsafe_permissions["backups"].chmod(0o755)
    expect_rejected_without_mutation(
        unsafe_permissions,
        failures,
        evidence,
        "unsafe_directory_permissions_fail_closed",
    )

    unknown = create_fixture(root / "unknown", pair_count=6)
    (unknown["backups"] / "README.txt").write_text("unknown inventory member\n", encoding="utf-8")
    expect_rejected_without_mutation(unknown, failures, evidence, "unknown_file_fail_closed")

    missing_manifest = create_fixture(root / "missing-manifest", pair_count=6)
    missing_manifest["pairs"][0]["manifest"].unlink()
    expect_rejected_without_mutation(missing_manifest, failures, evidence, "missing_manifest_fail_closed")

    missing_database = create_fixture(root / "missing-database", pair_count=6)
    missing_database["pairs"][0]["database"].unlink()
    expect_rejected_without_mutation(missing_database, failures, evidence, "missing_database_fail_closed")

    symlink_directory = create_fixture(root / "symlink-directory", pair_count=0)
    external_backups = symlink_directory["root"] / "external-backups"
    symlink_directory["pairs"] = [
        create_verified_pair(symlink_directory, index, directory=external_backups)
        for index in range(1, 7)
    ]
    symlink_directory["backups"].rmdir()
    symlink_directory["backups"].symlink_to(external_backups)
    expect_rejected_without_mutation(
        symlink_directory,
        failures,
        evidence,
        "symlink_directory_fail_closed",
        extra_snapshot=lambda: snapshot_flat_directory(external_backups),
    )

    symlink_database = create_fixture(root / "symlink-database", pair_count=6)
    linked_database = symlink_database["pairs"][0]["database"]
    external_database = symlink_database["root"] / "external.sqlite"
    linked_database.rename(external_database)
    linked_database.symlink_to(external_database)
    expect_rejected_without_mutation(
        symlink_database,
        failures,
        evidence,
        "symlink_database_member_fail_closed",
        extra_snapshot=lambda: (external_database.stat().st_size, sha256(external_database)),
    )

    symlink_manifest = create_fixture(root / "symlink-manifest", pair_count=6)
    linked_manifest = symlink_manifest["pairs"][0]["manifest"]
    external_manifest = symlink_manifest["root"] / "external.manifest.json"
    linked_manifest.rename(external_manifest)
    linked_manifest.symlink_to(external_manifest)
    expect_rejected_without_mutation(
        symlink_manifest,
        failures,
        evidence,
        "symlink_manifest_member_fail_closed",
        extra_snapshot=lambda: (external_manifest.stat().st_size, sha256(external_manifest)),
    )

    tampered_database = create_fixture(root / "tampered-database", pair_count=6)
    with tampered_database["pairs"][0]["database"].open("ab") as handle:
        handle.write(b"tampered")
    expect_rejected_without_mutation(tampered_database, failures, evidence, "tampered_database_fail_closed")

    tampered_manifest = create_fixture(root / "tampered-manifest", pair_count=6)
    manifest_path = tampered_manifest["pairs"][0]["manifest"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["backup_sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    expect_rejected_without_mutation(tampered_manifest, failures, evidence, "tampered_manifest_fail_closed")

    oversized_manifest = create_fixture(root / "oversized-manifest", pair_count=6)
    oversized_path = oversized_manifest["pairs"][0]["manifest"]
    oversized_path.write_bytes(b" " * (agentops_local_backup.BACKUP_MANIFEST_MAX_BYTES + 1))
    oversized_path.chmod(0o600)
    expect_rejected_without_mutation(
        oversized_manifest,
        failures,
        evidence,
        "oversized_manifest_fail_closed",
    )


def exercise_bounded_output(root: Path, failures: list[str], evidence: dict[str, bool]) -> None:
    fixture = create_fixture(root / "bounded-output", pair_count=111)
    before = snapshot_flat_directory(fixture["backups"])
    process, payload = run_host(fixture["env"], "backup-prune")
    counts = payload.get("counts") or {}
    record(
        failures,
        evidence,
        "large_inventory_output_is_bounded",
        process.returncode == 0
        and is_prune_payload(payload, ok=True)
        and counts == {"inventory": 111, "retained": 5, "candidates": 106}
        and len(payload.get("inventory") or []) == 100
        and payload.get("inventory_truncated") is True
        and len(payload.get("retained") or []) == 5
        and payload.get("retained_truncated") is False
        and len(payload.get("candidates") or []) == 100
        and payload.get("candidates_truncated") is True
        and payload.get("output_limit") == 100
        and snapshot_flat_directory(fixture["backups"]) == before
        and output_is_safe((process.stdout or "") + (process.stderr or ""), fixture),
    )


def exercise_quarantine_rollback(root: Path, failures: list[str], evidence: dict[str, bool]) -> None:
    fixture = create_fixture(root / "quarantine-rollback")
    plan, plan_error = agentops_local_backup.backup_prune_plan(fixture["backups"], DEFAULT_KEEP)
    before = snapshot_flat_directory(fixture["backups"])
    protected_before = snapshot_protected(fixture)
    real_replace = os.replace
    forward_moves = 0

    def fail_third_forward_move(source, destination):
        nonlocal forward_moves
        destination_path = Path(destination)
        if destination_path.parent.name.startswith(".agentops-prune-quarantine-"):
            forward_moves += 1
            if forward_moves == 3:
                raise OSError("injected quarantine move failure")
        return real_replace(source, destination)

    with mock.patch.object(agentops_local_backup.os, "replace", side_effect=fail_third_forward_move):
        payload, status_code = agentops_local_backup.prune_backups(
            argparse.Namespace(
                backup_dir=str(fixture["backups"]),
                keep=DEFAULT_KEEP,
                confirm_prune=True,
                plan_hash=str(plan.get("plan_hash") or ""),
            )
        )
    record(
        failures,
        evidence,
        "quarantine_move_failure_rolls_back",
        plan_error is None
        and status_code == 1
        and payload.get("error") == "backup_prune_quarantine_failed"
        and payload.get("state_rolled_back") is True
        and snapshot_flat_directory(fixture["backups"]) == before
        and snapshot_protected(fixture) == protected_before
        and output_is_safe(json.dumps(payload, sort_keys=True), fixture),
    )


def main() -> int:
    failures: list[str] = []
    evidence: dict[str, bool] = {}
    try:
        with tempfile.TemporaryDirectory(prefix="agentops-host-backup-prune-") as temporary:
            root = Path(temporary)
            exercise_valid_inventory(root, failures, evidence)
            exercise_fresh_host(root, failures, evidence)
            exercise_verified_sidecar_inventory(root, failures, evidence)
            exercise_invalid_inventory(root, failures, evidence)
            exercise_bounded_output(root, failures, evidence)
            exercise_quarantine_rollback(root, failures, evidence)
    except (OSError, RuntimeError, sqlite3.Error, subprocess.SubprocessError) as exc:
        failures.append(f"fixture_exception:{type(exc).__name__}")

    print(
        json.dumps(
            {
                "ok": not failures,
                "operation": "private_host_backup_prune_smoke",
                "checks": len(evidence),
                "evidence": evidence,
                "temporary_host_home": True,
                "real_user_database_used": False,
                "network_used": False,
                "raw_rows_printed": False,
                "credential_values_omitted": True,
                "failures": failures,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
