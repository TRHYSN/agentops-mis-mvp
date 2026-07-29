#!/usr/bin/env python3
"""Local AgentOps MIS SQLite backup, verification, and explicit restore utility."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import hmac
import json
import os
import re
import shutil
import sqlite3
import stat
import sys
import tempfile
from datetime import timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = Path(os.environ.get("AGENTOPS_DB_PATH") or (ROOT / "agentops_mis.db"))
DEFAULT_BACKUP_DIR = ROOT / ".agentops_runtime" / "backups"
BACKUP_FILE_PATTERN = re.compile(r"^agentops-mis-(\d{8}T\d{6})(\d{6})?Z\.sqlite$")
MANIFEST_FILE_PATTERN = re.compile(r"^agentops-mis-(\d{8}T\d{6})(\d{6})?Z\.manifest\.json$")
BACKUP_SIDECAR_PATTERN = re.compile(
    r"^(agentops-mis-(\d{8}T\d{6})(\d{6})?Z)\.sqlite-(wal|shm)$"
)
BACKUP_PRUNE_DEFAULT_KEEP = 5
BACKUP_PRUNE_MIN_KEEP = 2
BACKUP_PRUNE_OUTPUT_LIMIT = 100
BACKUP_MANIFEST_MAX_BYTES = 1024 * 1024
BACKUP_SIDECAR_MAX_BYTES = 1024 * 1024
COUNT_TABLES = [
    "agents",
    "tasks",
    "runs",
    "tool_calls",
    "approvals",
    "memories",
    "evaluations",
    "audit_logs",
    "artifacts",
    "workflow_jobs",
    "agent_gateway_tokens",
    "agent_gateway_sessions",
]


def now_stamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def now_iso() -> str:
    return dt.datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def connect_readonly(path: Path) -> sqlite3.Connection:
    # Backups are complete immutable snapshots. SQLite may otherwise create
    # zero-byte WAL and shared-memory sidecars merely while verifying a WAL-mode
    # database, causing retention inventory drift on every read.
    uri = f"{path.resolve().as_uri()}?mode=ro&immutable=1"
    return sqlite3.connect(uri, uri=True)


def count_rows(conn: sqlite3.Connection) -> dict[str, int]:
    counts: dict[str, int] = {}
    existing = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    for table in COUNT_TABLES:
        if table not in existing:
            counts[table] = 0
            continue
        counts[table] = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    return counts


def integrity_check(path: Path) -> str:
    with connect_readonly(path) as conn:
        return str(conn.execute("PRAGMA integrity_check").fetchone()[0])


def foreign_key_check(path: Path) -> str:
    with connect_readonly(path) as conn:
        return "ok" if not conn.execute("PRAGMA foreign_key_check").fetchone() else "failed"


def schema_evidence(path: Path) -> dict:
    with connect_readonly(path) as conn:
        user_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        rows = conn.execute(
            "SELECT type,name,tbl_name,COALESCE(sql,'') FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
        ).fetchall()
    payload = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
    return {
        "sqlite_user_version": user_version,
        "schema_sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
    }


def backup_manifest(backup_path: Path, source_db: Path, started_at: str) -> dict:
    with connect_readonly(backup_path) as conn:
        counts = count_rows(conn)
    return {
        "manifest_schema_version": 1,
        "provider": "agentops-local-backup",
        "operation": "backup_create",
        "backup_id": backup_path.stem,
        "created_at": started_at,
        "source_db_label": source_db.name,
        "backup_file": backup_path.name,
        "backup_size_bytes": backup_path.stat().st_size,
        "backup_sha256": sha256_file(backup_path),
        "integrity_check": integrity_check(backup_path),
        "foreign_key_check": foreign_key_check(backup_path),
        **schema_evidence(backup_path),
        "counts": counts,
        "safety": {
            "local_only": True,
            "raw_rows_printed": False,
            "tokens_omitted": True,
            "secret_store_included": False,
            "hashed_auth_state_included": True,
        },
    }


def create_backup(args: argparse.Namespace) -> tuple[dict, int]:
    source = Path(args.db_path or DEFAULT_DB).expanduser().resolve()
    backup_dir = Path(args.backup_dir or DEFAULT_BACKUP_DIR).expanduser().resolve()
    if not source.exists():
        return {"ok": False, "error": "source_db_not_found", "source_db_label": source.name}, 1
    backup_dir.mkdir(parents=True, exist_ok=True)
    started_at = now_stamp()
    backup_path = backup_dir / f"agentops-mis-{started_at}.sqlite"
    manifest_path = backup_path.with_suffix(".manifest.json")
    with sqlite3.connect(source) as src, sqlite3.connect(backup_path) as dst:
        src.backup(dst)
    backup_path.chmod(0o600)
    manifest = backup_manifest(backup_path, source, started_at)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest_path.chmod(0o600)
    return {
        "ok": manifest["integrity_check"] == "ok",
        "backup_path": str(backup_path),
        "manifest_path": str(manifest_path),
        "manifest": manifest,
    }, 0


def latest_backup(backup_dir: Path) -> Path | None:
    backups = sorted(backup_dir.glob("agentops-mis-*.sqlite"), key=lambda path: path.stat().st_mtime, reverse=True)
    return backups[0] if backups else None


def verify_backup(args: argparse.Namespace) -> tuple[dict, int]:
    backup_dir = Path(args.backup_dir or DEFAULT_BACKUP_DIR).expanduser().resolve()
    backup_path = Path(args.backup).expanduser().resolve() if args.backup else latest_backup(backup_dir)
    if not backup_path or not backup_path.exists():
        return {"ok": False, "error": "backup_not_found"}, 1
    manifest_path = backup_path.with_suffix(".manifest.json")
    if not manifest_path.is_file():
        return {"ok": False, "error": "backup_manifest_missing", "backup_path": str(backup_path)}, 1
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"ok": False, "error": "backup_manifest_unreadable", "backup_path": str(backup_path)}, 1
    required = {
        "manifest_schema_version",
        "provider",
        "backup_file",
        "backup_size_bytes",
        "backup_sha256",
        "integrity_check",
        "foreign_key_check",
        "sqlite_user_version",
        "schema_sha256",
    }
    missing = sorted(required - set(manifest))
    if missing:
        return {"ok": False, "error": "backup_manifest_incomplete", "missing_fields": missing}, 1
    try:
        with connect_readonly(backup_path) as conn:
            counts = count_rows(conn)
        actual_hash = sha256_file(backup_path)
        expected_hash = str(manifest["backup_sha256"])
        hash_ok = expected_hash == actual_hash
        file_name_ok = manifest["backup_file"] == backup_path.name
        size_ok = int(manifest["backup_size_bytes"]) == backup_path.stat().st_size
        integrity = integrity_check(backup_path)
        foreign_keys = foreign_key_check(backup_path)
        schema = schema_evidence(backup_path)
        schema_ok = (
            int(manifest["sqlite_user_version"]) == schema["sqlite_user_version"]
            and manifest["schema_sha256"] == schema["schema_sha256"]
        )
    except (OSError, TypeError, ValueError, sqlite3.DatabaseError):
        return {
            "ok": False,
            "error": "backup_verification_failed",
            "backup_path": str(backup_path),
            "failure_detail_omitted": True,
            "raw_rows_printed": False,
            "token_omitted": True,
        }, 1
    manifest_ok = (
        manifest["manifest_schema_version"] == 1
        and manifest["provider"] == "agentops-local-backup"
        and manifest["integrity_check"] == "ok"
        and manifest["foreign_key_check"] == "ok"
    )
    ok = integrity == "ok" and foreign_keys == "ok" and hash_ok and file_name_ok and size_ok and schema_ok and manifest_ok
    return {
        "ok": ok,
        "backup_path": str(backup_path),
        "manifest_path": str(manifest_path) if manifest_path.exists() else None,
        "integrity_check": integrity,
        "hash_ok": hash_ok,
        "file_name_ok": file_name_ok,
        "size_ok": size_ok,
        "schema_ok": schema_ok,
        "manifest_ok": manifest_ok,
        "foreign_key_check": foreign_keys,
        "backup_sha256": actual_hash,
        "counts": counts,
        "raw_rows_printed": False,
        "token_omitted": True,
    }, 0 if ok else 1


def backup_inventory(backup_dir: Path) -> tuple[list[dict], dict | None]:
    if backup_dir.is_symlink():
        return [], {"ok": False, "error": "backup_directory_symlink"}
    if not backup_dir.exists():
        return [], None
    if not backup_dir.is_dir():
        return [], {"ok": False, "error": "backup_directory_not_directory"}
    try:
        backup_dir_metadata = backup_dir.stat(follow_symlinks=False)
    except OSError:
        return [], {"ok": False, "error": "backup_directory_unreadable"}
    if (
        backup_dir_metadata.st_uid != os.getuid()
        or stat.S_IMODE(backup_dir_metadata.st_mode) != 0o700
    ):
        return [], {"ok": False, "error": "backup_directory_permissions_unsafe"}

    sqlite_entries: dict[str, Path] = {}
    manifest_entries: dict[str, Path] = {}
    sidecar_entries: dict[str, dict[str, Path]] = {}
    try:
        entries = sorted(backup_dir.iterdir(), key=lambda path: path.name)
    except OSError:
        return [], {"ok": False, "error": "backup_directory_unreadable"}
    for entry in entries:
        if entry.is_symlink():
            return [], {
                "ok": False,
                "error": "backup_inventory_symlink",
                "entry_label": entry.name,
            }
        if not entry.is_file():
            return [], {
                "ok": False,
                "error": "backup_inventory_unknown_entry",
                "entry_label": entry.name,
            }
        backup_match = BACKUP_FILE_PATTERN.fullmatch(entry.name)
        manifest_match = MANIFEST_FILE_PATTERN.fullmatch(entry.name)
        sidecar_match = BACKUP_SIDECAR_PATTERN.fullmatch(entry.name)
        if backup_match:
            sqlite_entries[entry.stem] = entry
        elif manifest_match:
            manifest_entries[entry.name.removesuffix(".manifest.json")] = entry
        elif sidecar_match:
            backup_id = sidecar_match.group(1)
            sidecar_kind = sidecar_match.group(4)
            sidecar_entries.setdefault(backup_id, {})[sidecar_kind] = entry
        else:
            return [], {
                "ok": False,
                "error": "backup_inventory_unknown_entry",
                "entry_label": entry.name,
            }

    missing_manifests = sorted(set(sqlite_entries) - set(manifest_entries))
    missing_backups = sorted(set(manifest_entries) - set(sqlite_entries))
    if missing_manifests or missing_backups:
        return [], {
            "ok": False,
            "error": "backup_inventory_incomplete_pair",
            "missing_manifest_labels": missing_manifests[:BACKUP_PRUNE_OUTPUT_LIMIT],
            "missing_manifest_count": len(missing_manifests),
            "missing_manifest_labels_truncated": len(missing_manifests) > BACKUP_PRUNE_OUTPUT_LIMIT,
            "missing_backup_labels": missing_backups[:BACKUP_PRUNE_OUTPUT_LIMIT],
            "missing_backup_count": len(missing_backups),
            "missing_backup_labels_truncated": len(missing_backups) > BACKUP_PRUNE_OUTPUT_LIMIT,
        }
    orphan_sidecars = sorted(set(sidecar_entries) - set(sqlite_entries))
    if orphan_sidecars:
        return [], {
            "ok": False,
            "error": "backup_inventory_orphan_sidecar",
            "orphan_sidecar_labels": orphan_sidecars[:BACKUP_PRUNE_OUTPUT_LIMIT],
            "orphan_sidecar_count": len(orphan_sidecars),
            "orphan_sidecar_labels_truncated": len(orphan_sidecars) > BACKUP_PRUNE_OUTPUT_LIMIT,
        }

    inventory: list[dict] = []
    for backup_id in sorted(sqlite_entries):
        backup_path = sqlite_entries[backup_id]
        manifest_path = manifest_entries[backup_id]
        try:
            backup_before = backup_path.stat(follow_symlinks=False)
            manifest_before = manifest_path.stat(follow_symlinks=False)
        except OSError:
            return [], {
                "ok": False,
                "error": "backup_inventory_entry_unreadable",
                "entry_label": backup_id,
            }
        sidecars_before: dict[str, tuple[Path, os.stat_result]] = {}
        for sidecar_kind, sidecar_path in sorted((sidecar_entries.get(backup_id) or {}).items()):
            try:
                sidecar_before = sidecar_path.stat(follow_symlinks=False)
            except OSError:
                return [], {
                    "ok": False,
                    "error": "backup_inventory_entry_unreadable",
                    "entry_label": backup_id,
                    "sidecar_kind": sidecar_kind,
                }
            if not stat.S_ISREG(sidecar_before.st_mode):
                return [], {
                    "ok": False,
                    "error": "backup_inventory_entry_not_regular",
                    "entry_label": backup_id,
                    "sidecar_kind": sidecar_kind,
                }
            if sidecar_before.st_size > BACKUP_SIDECAR_MAX_BYTES:
                return [], {
                    "ok": False,
                    "error": "backup_sidecar_too_large",
                    "entry_label": backup_id,
                    "sidecar_kind": sidecar_kind,
                    "maximum_sidecar_bytes": BACKUP_SIDECAR_MAX_BYTES,
                }
            if sidecar_kind == "wal" and sidecar_before.st_size != 0:
                return [], {
                    "ok": False,
                    "error": "backup_wal_not_empty",
                    "entry_label": backup_id,
                    "sidecar_kind": sidecar_kind,
                    "sidecar_content_omitted": True,
                }
            sidecars_before[sidecar_kind] = (sidecar_path, sidecar_before)
        if not stat.S_ISREG(backup_before.st_mode) or not stat.S_ISREG(manifest_before.st_mode):
            return [], {
                "ok": False,
                "error": "backup_inventory_entry_not_regular",
                "entry_label": backup_id,
            }
        if manifest_before.st_size > BACKUP_MANIFEST_MAX_BYTES:
            return [], {
                "ok": False,
                "error": "backup_manifest_too_large",
                "entry_label": backup_id,
                "maximum_manifest_bytes": BACKUP_MANIFEST_MAX_BYTES,
            }
        verification, status = verify_backup(
            argparse.Namespace(backup=str(backup_path), backup_dir=str(backup_dir))
        )
        try:
            manifest_sha256 = sha256_file(manifest_path)
            backup_after = backup_path.stat(follow_symlinks=False)
            manifest_after = manifest_path.stat(follow_symlinks=False)
        except OSError:
            return [], {
                "ok": False,
                "error": "backup_inventory_entry_unreadable",
                "entry_label": backup_id,
            }
        sidecar_files: list[dict] = []
        sidecars_stable = True
        for sidecar_kind, (sidecar_path, sidecar_before) in sorted(sidecars_before.items()):
            try:
                sidecar_after = sidecar_path.stat(follow_symlinks=False)
                sidecar_sha256 = sha256_file(sidecar_path)
            except OSError:
                return [], {
                    "ok": False,
                    "error": "backup_inventory_entry_unreadable",
                    "entry_label": backup_id,
                    "sidecar_kind": sidecar_kind,
                }
            sidecars_stable = sidecars_stable and (
                (sidecar_before.st_dev, sidecar_before.st_ino, sidecar_before.st_mode,
                 sidecar_before.st_size, sidecar_before.st_mtime_ns)
                == (sidecar_after.st_dev, sidecar_after.st_ino, sidecar_after.st_mode,
                    sidecar_after.st_size, sidecar_after.st_mtime_ns)
                and stat.S_ISREG(sidecar_after.st_mode)
            )
            sidecar_files.append({
                "kind": sidecar_kind,
                "file": sidecar_path.name,
                "size_bytes": sidecar_after.st_size,
                "sha256": sidecar_sha256,
            })
        stable = (
            (backup_before.st_dev, backup_before.st_ino, backup_before.st_mode, backup_before.st_size, backup_before.st_mtime_ns)
            == (backup_after.st_dev, backup_after.st_ino, backup_after.st_mode, backup_after.st_size, backup_after.st_mtime_ns)
            and (manifest_before.st_dev, manifest_before.st_ino, manifest_before.st_mode, manifest_before.st_size, manifest_before.st_mtime_ns)
            == (manifest_after.st_dev, manifest_after.st_ino, manifest_after.st_mode, manifest_after.st_size, manifest_after.st_mtime_ns)
            and stat.S_ISREG(backup_after.st_mode)
            and stat.S_ISREG(manifest_after.st_mode)
            and sidecars_stable
        )
        if status != 0 or verification.get("ok") is not True:
            return [], {
                "ok": False,
                "error": "backup_inventory_verification_failed",
                "entry_label": backup_id,
                "verification_error": verification.get("error") or "verification_mismatch",
            }
        if not stable:
            return [], {
                "ok": False,
                "error": "backup_inventory_changed_during_verification",
                "entry_label": backup_id,
            }
        name_match = BACKUP_FILE_PATTERN.fullmatch(backup_path.name)
        timestamp_key = f"{name_match.group(1)}{name_match.group(2) or '000000'}Z"
        inventory.append({
            "backup_id": backup_id,
            "backup_file": backup_path.name,
            "manifest_file": manifest_path.name,
            "timestamp_key": timestamp_key,
            "backup_size_bytes": backup_after.st_size,
            "manifest_size_bytes": manifest_after.st_size,
            "sidecar_files": sidecar_files,
            "sidecar_size_bytes": sum(int(item["size_bytes"]) for item in sidecar_files),
            "pair_size_bytes": (
                backup_after.st_size
                + manifest_after.st_size
                + sum(int(item["size_bytes"]) for item in sidecar_files)
            ),
            "backup_sha256": verification["backup_sha256"],
            "manifest_sha256": manifest_sha256,
        })
    inventory.sort(key=lambda item: (item["timestamp_key"], item["backup_id"]))
    return inventory, None


def backup_prune_plan(backup_dir: Path, keep: int) -> tuple[dict, dict | None]:
    inventory, error = backup_inventory(backup_dir)
    if error:
        return {}, error
    candidate_count = max(0, len(inventory) - keep)
    candidates = inventory[:candidate_count]
    retained = list(reversed(inventory[candidate_count:]))
    canonical = {
        "plan_schema_version": 1,
        "operation": "backup_prune",
        "backup_root_sha256": hashlib.sha256(str(backup_dir).encode("utf-8")).hexdigest(),
        "keep": keep,
        "inventory": inventory,
        "retained": [item["backup_id"] for item in retained],
        "candidates": [item["backup_id"] for item in candidates],
    }
    encoded = json.dumps(canonical, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return {
        "ok": True,
        "dry_run": True,
        "keep": keep,
        "minimum_keep": BACKUP_PRUNE_MIN_KEEP,
        "inventory": inventory,
        "retained": canonical["retained"],
        "candidates": canonical["candidates"],
        "counts": {
            "inventory": len(inventory),
            "retained": len(retained),
            "candidates": len(candidates),
        },
        "reclaimable_bytes": sum(int(item["pair_size_bytes"]) for item in candidates),
        "plan_hash": hashlib.sha256(encoded).hexdigest(),
        "raw_rows_printed": False,
        "token_omitted": True,
    }, None


def bounded_prune_plan(plan: dict) -> dict:
    bounded = dict(plan)
    for key in ("inventory", "retained", "candidates"):
        values = list(plan.get(key) or [])
        bounded[key] = values[:BACKUP_PRUNE_OUTPUT_LIMIT]
        bounded[f"{key}_truncated"] = len(values) > BACKUP_PRUNE_OUTPUT_LIMIT
    bounded["output_limit"] = BACKUP_PRUNE_OUTPUT_LIMIT
    return bounded


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def prune_backups(args: argparse.Namespace) -> tuple[dict, int]:
    keep = int(args.keep)
    if keep < BACKUP_PRUNE_MIN_KEEP:
        return {
            "ok": False,
            "dry_run": True,
            "error": "backup_prune_keep_below_minimum",
            "keep": keep,
            "minimum_keep": BACKUP_PRUNE_MIN_KEEP,
            "raw_rows_printed": False,
            "token_omitted": True,
        }, 2
    backup_dir = Path(args.backup_dir or DEFAULT_BACKUP_DIR).expanduser().absolute()
    plan, error = backup_prune_plan(backup_dir, keep)
    if error:
        return {
            **error,
            "dry_run": True,
            "raw_rows_printed": False,
            "token_omitted": True,
        }, 1
    public_plan = bounded_prune_plan(plan)
    if args.plan_hash and not args.confirm_prune:
        return {
            **public_plan,
            "ok": False,
            "error": "backup_prune_confirmation_required",
            "confirmation_applied": False,
        }, 2
    if not args.confirm_prune:
        return public_plan, 0
    if not args.plan_hash:
        return {
            **public_plan,
            "ok": False,
            "error": "backup_prune_plan_hash_required",
            "confirmation_applied": False,
        }, 2
    if not re.fullmatch(r"[0-9a-f]{64}", str(args.plan_hash)):
        return {
            **public_plan,
            "ok": False,
            "error": "backup_prune_plan_hash_invalid",
            "confirmation_applied": False,
        }, 2
    if not secrets_compare(args.plan_hash, plan["plan_hash"]):
        return {
            **public_plan,
            "ok": False,
            "error": "backup_prune_plan_mismatch",
            "stale_plan": True,
            "confirmation_applied": False,
        }, 2

    refreshed_plan, refreshed_error = backup_prune_plan(backup_dir, keep)
    if refreshed_error or not secrets_compare(args.plan_hash, refreshed_plan.get("plan_hash", "")):
        return {
            "ok": False,
            "dry_run": True,
            "error": "backup_prune_inventory_changed",
            "stale_plan": True,
            "confirmation_applied": False,
            "raw_rows_printed": False,
            "token_omitted": True,
        }, 2
    public_refreshed_plan = bounded_prune_plan(refreshed_plan)
    if not refreshed_plan["candidates"]:
        return {
            **public_refreshed_plan,
            "dry_run": False,
            "pruned": True,
            "deleted": [],
            "deleted_count": 0,
            "confirmation_applied": True,
        }, 0

    quarantine = backup_dir / f".agentops-prune-quarantine-{now_stamp()}"
    moved: list[tuple[Path, Path]] = []
    quarantine_created = False
    candidate_ids = set(refreshed_plan["candidates"])
    candidate_items = [item for item in refreshed_plan["inventory"] if item["backup_id"] in candidate_ids]
    try:
        quarantine.mkdir(mode=0o700)
        quarantine_created = True
        for item in candidate_items:
            files_to_move = [
                (item["backup_file"], item["backup_sha256"]),
                (item["manifest_file"], item["manifest_sha256"]),
                *[
                    (sidecar["file"], sidecar["sha256"])
                    for sidecar in item.get("sidecar_files") or []
                ],
            ]
            for file_name, expected_hash in files_to_move:
                source = backup_dir / file_name
                if source.is_symlink() or not source.is_file():
                    raise OSError("candidate changed before quarantine")
                if not secrets_compare(sha256_file(source), expected_hash):
                    raise OSError("candidate hash changed before quarantine")
                destination = quarantine / source.name
                os.replace(source, destination)
                moved.append((source, destination))
        fsync_directory(backup_dir)
        fsync_directory(quarantine)
    except OSError:
        rollback_ok = True
        for source, destination in reversed(moved):
            try:
                if destination.exists() and not source.exists():
                    os.replace(destination, source)
            except OSError:
                rollback_ok = False
        if quarantine_created:
            try:
                quarantine.rmdir()
            except OSError:
                rollback_ok = False
        return {
            **public_refreshed_plan,
            "ok": False,
            "dry_run": False,
            "error": "backup_prune_quarantine_failed",
            "state_rolled_back": rollback_ok,
            "confirmation_applied": True,
            "failure_detail_omitted": True,
        }, 1

    deleted_files = 0
    cleanup_failed = False
    for _source, destination in moved:
        try:
            destination.unlink()
            deleted_files += 1
        except OSError:
            cleanup_failed = True
    try:
        quarantine.rmdir()
    except OSError:
        cleanup_failed = True
    try:
        fsync_directory(backup_dir)
    except OSError:
        cleanup_failed = True
    if cleanup_failed:
        return {
            **public_refreshed_plan,
            "ok": False,
            "dry_run": False,
            "error": "backup_prune_cleanup_incomplete",
            "confirmation_applied": True,
            "deleted_file_count": deleted_files,
            "quarantine_label": quarantine.name,
            "failure_detail_omitted": True,
        }, 1
    return {
        **public_refreshed_plan,
        "dry_run": False,
        "pruned": True,
        "deleted": public_refreshed_plan["candidates"],
        "deleted_truncated": public_refreshed_plan["candidates_truncated"],
        "deleted_count": len(refreshed_plan["candidates"]),
        "deleted_file_count": deleted_files,
        "confirmation_applied": True,
    }, 0


def secrets_compare(left: str, right: str) -> bool:
    return hmac.compare_digest(str(left), str(right))


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def revoke_restored_auth_state(path: Path) -> dict[str, int]:
    revoked: dict[str, int] = {}
    stamp = now_iso()
    with sqlite3.connect(path) as conn:
        existing = {str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in ("human_sessions", "agent_gateway_sessions", "agent_gateway_tokens"):
            columns = table_columns(conn, table) if table in existing else set()
            if {"status", "revoked_at"}.issubset(columns):
                cursor = conn.execute(
                    f"UPDATE {table} SET status='revoked', revoked_at=COALESCE(revoked_at, ?) WHERE status='active'",
                    (stamp,),
                )
                revoked[table] = int(cursor.rowcount)
            else:
                revoked[table] = 0
    return revoked


def sqlite_snapshot(source: Path, destination: Path) -> None:
    with sqlite3.connect(source) as src, sqlite3.connect(destination) as dst:
        src.backup(dst)
    destination.chmod(0o600)


def fsync_file(path: Path) -> None:
    with path.open("rb") as handle:
        os.fsync(handle.fileno())


def restore_backup(args: argparse.Namespace) -> tuple[dict, int]:
    if not args.confirm_restore:
        return {
            "ok": False,
            "dry_run": True,
            "error": "confirm_restore_required",
            "message": "Pass --confirm-restore to write the target DB.",
        }, 2
    backup_path = Path(args.backup).expanduser().resolve()
    target = Path(args.target).expanduser().resolve()
    if not backup_path.exists():
        return {"ok": False, "error": "backup_not_found", "backup_path": str(backup_path)}, 1
    verify_payload, verify_status = verify_backup(argparse.Namespace(backup=str(backup_path), backup_dir=None))
    if verify_status != 0:
        return {"ok": False, "error": "backup_verification_failed", "verify": verify_payload}, 1
    if target.exists() and not args.overwrite:
        return {
            "ok": False,
            "error": "target_exists",
            "target_label": target.name,
            "message": "Pass --overwrite with --confirm-restore after making a separate safety copy.",
        }, 2
    target.parent.mkdir(parents=True, exist_ok=True)
    pre_restore_copy = None
    if target.exists():
        pre_restore_copy = target.with_suffix(target.suffix + f".pre-restore-{now_stamp()}")
        sqlite_snapshot(target, pre_restore_copy)
        if integrity_check(pre_restore_copy) != "ok" or foreign_key_check(pre_restore_copy) != "ok":
            pre_restore_copy.unlink(missing_ok=True)
            return {"ok": False, "error": "pre_restore_snapshot_verification_failed"}, 1
    target.parent.mkdir(parents=True, exist_ok=True)
    stage_fd, stage_name = tempfile.mkstemp(prefix=f".{target.name}.restore-", dir=target.parent)
    os.close(stage_fd)
    stage = Path(stage_name)
    try:
        shutil.copy2(backup_path, stage)
        revoked_auth = revoke_restored_auth_state(stage)
        restored_integrity = integrity_check(stage)
        restored_foreign_keys = foreign_key_check(stage)
        if restored_integrity != "ok" or restored_foreign_keys != "ok":
            return {
                "ok": False,
                "error": "restored_database_verification_failed",
                "integrity_check": restored_integrity,
                "foreign_key_check": restored_foreign_keys,
            }, 1
        stage.chmod(0o600)
        fsync_file(stage)
        os.replace(stage, target)
        for suffix in ("-wal", "-shm"):
            Path(str(target) + suffix).unlink(missing_ok=True)
    finally:
        stage.unlink(missing_ok=True)
    return {
        "ok": True,
        "restored": True,
        "target_path": str(target),
        "backup_path": str(backup_path),
        "pre_restore_copy": str(pre_restore_copy) if pre_restore_copy else None,
        "restored_integrity_check": restored_integrity,
        "restored_foreign_key_check": restored_foreign_keys,
        "revoked_auth_state": revoked_auth,
        "auth_sessions_preserved": False,
        "atomic_replace": True,
        "verify": verify_payload,
        "raw_rows_printed": False,
        "token_omitted": True,
    }, 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AgentOps MIS local SQLite backup utility.")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create", help="Create a local SQLite backup and manifest.")
    create.add_argument("--db-path", default=str(DEFAULT_DB))
    create.add_argument("--backup-dir", default=str(DEFAULT_BACKUP_DIR))
    create.set_defaults(func=create_backup)

    verify = sub.add_parser("verify", help="Verify a backup integrity/hash without printing rows.")
    verify.add_argument("--backup", default="")
    verify.add_argument("--backup-dir", default=str(DEFAULT_BACKUP_DIR))
    verify.set_defaults(func=verify_backup)

    prune = sub.add_parser("prune", help="Prepare or confirm a verified backup-retention plan.")
    prune.add_argument("--backup-dir", default=str(DEFAULT_BACKUP_DIR))
    prune.add_argument("--keep", type=int, default=BACKUP_PRUNE_DEFAULT_KEEP)
    prune.add_argument("--confirm-prune", action="store_true")
    prune.add_argument("--plan-hash", default="")
    prune.set_defaults(func=prune_backups)

    restore = sub.add_parser("restore", help="Restore a backup to a target DB path with explicit confirmation.")
    restore.add_argument("--backup", required=True)
    restore.add_argument("--target", required=True)
    restore.add_argument("--confirm-restore", action="store_true")
    restore.add_argument("--overwrite", action="store_true")
    restore.set_defaults(func=restore_backup)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload, status = args.func(args)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
