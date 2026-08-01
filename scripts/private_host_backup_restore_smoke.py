#!/usr/bin/env python3
"""Verify product-level Host backup, integrity, restore, and stop gates."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def digest(path: Path) -> str:
    value = hashlib.sha256()
    value.update(path.read_bytes())
    return value.hexdigest()


def run_host(env: dict, *args: str, expected=(0,)) -> tuple[dict, str]:
    process = subprocess.run(
        [sys.executable, "-m", "agentops_mis_cli.cli", "host", *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    try:
        payload = json.loads(process.stdout)
    except ValueError:
        payload = {}
    if process.returncode not in expected:
        raise RuntimeError(f"host {' '.join(args)} exited {process.returncode}: {process.stderr[-300:]}")
    return payload, (process.stdout or "") + (process.stderr or "")


def seed_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE agents(agent_id TEXT PRIMARY KEY, name TEXT)")
        conn.execute("CREATE TABLE tasks(task_id TEXT PRIMARY KEY, title TEXT)")
        conn.execute("CREATE TABLE runs(run_id TEXT PRIMARY KEY, task_id TEXT)")
        conn.execute("CREATE TABLE human_sessions(session_id TEXT PRIMARY KEY, status TEXT, revoked_at TEXT)")
        conn.execute("CREATE TABLE agent_gateway_sessions(session_id TEXT PRIMARY KEY, status TEXT, revoked_at TEXT)")
        conn.execute("CREATE TABLE agent_gateway_tokens(token_id TEXT PRIMARY KEY, token_hash TEXT, status TEXT, revoked_at TEXT)")
        conn.execute("INSERT INTO agents VALUES('agt_host_backup_smoke', 'Host Backup Smoke')")
        conn.execute("INSERT INTO tasks VALUES('tsk_host_backup_smoke', 'Host backup smoke task')")
        conn.execute("INSERT INTO runs VALUES('run_host_backup_smoke', 'tsk_host_backup_smoke')")
        conn.execute("INSERT INTO human_sessions VALUES('hs_host_backup_smoke', 'active', NULL)")
        conn.execute("INSERT INTO agent_gateway_sessions VALUES('ags_host_backup_smoke', 'active', NULL)")
        conn.execute("INSERT INTO agent_gateway_tokens VALUES('tok_host_backup_smoke', 'hash-only', 'active', NULL)")


def main() -> int:
    failures: list[str] = []
    evidence: dict[str, object] = {}
    with tempfile.TemporaryDirectory(prefix="agentops-host-backup-") as temporary:
        temp = Path(temporary)
        host_home = temp / "host"
        ui_dist = temp / "ui"
        ui_dist.mkdir()
        (ui_dist / "index.html").write_text("<!doctype html><title>Host backup smoke</title>\n", encoding="utf-8")
        env = {**os.environ, "AGENTOPS_HOST_HOME": str(host_home)}
        try:
            initialized, _ = run_host(env, "init", "--ui-dist", str(ui_dist))
            db_path = host_home / "data" / "agentops_mis.db"
            secrets_path = host_home / "secrets.json"
            seed_database(db_path)
            secrets_hash_before = digest(secrets_path)
            secret_values = list(json.loads(secrets_path.read_text(encoding="utf-8")).values())

            created, backup_output = run_host(env, "backup")
            backup_path = Path(str(created.get("backup_path") or ""))
            manifest_path = Path(str(created.get("manifest_path") or ""))
            evidence["backup"] = {
                "ok": created.get("ok"),
                "integrity": (created.get("manifest") or {}).get("integrity_check"),
                "secret_store_included": created.get("secret_store_included"),
                "hashed_auth_state_included": created.get("hashed_auth_state_included"),
                "private_files": all(
                    path.is_file() and (path.stat().st_mode & 0o077) == 0
                    for path in (backup_path, manifest_path)
                ),
            }
            if not created.get("ok") or not backup_path.is_file() or not manifest_path.is_file():
                failures.append("Host backup did not create the verified backup and manifest")
            if created.get("secret_store_included") is not False or created.get("hashed_auth_state_included") is not True or not evidence["backup"]["private_files"]:
                failures.append("Host backup credential boundary or private permissions failed")

            manifest_backup = manifest_path.with_suffix(".missing-test")
            manifest_path.rename(manifest_backup)
            missing_manifest, missing_output = run_host(
                env, "backup-verify", "--backup", str(backup_path), expected=(1,)
            )
            manifest_backup.rename(manifest_path)
            if missing_manifest.get("error") != "backup_manifest_missing":
                failures.append("Host backup verification accepted a missing manifest")

            original_backup = backup_path.read_bytes()
            with backup_path.open("ab") as handle:
                handle.write(b"tamper")
            tampered, tampered_output = run_host(
                env, "backup-verify", "--backup", str(backup_path), expected=(1,)
            )
            backup_path.write_bytes(original_backup)
            backup_path.chmod(0o600)
            if tampered.get("hash_ok") is not False:
                failures.append("Host backup verification accepted a tampered SQLite file")

            verified, verify_output = run_host(env, "backup-verify")
            evidence["verify"] = {
                "ok": verified.get("ok"),
                "integrity": verified.get("integrity_check"),
                "hash_ok": verified.get("hash_ok"),
                "read_only": verified.get("read_only"),
            }
            if not all((verified.get("ok"), verified.get("hash_ok"), verified.get("read_only"))):
                failures.append("Host backup verification did not prove hash/integrity/read-only state")

            dry_restore, dry_output = run_host(env, "restore", "--backup", str(backup_path), expected=(2,))
            if dry_restore.get("error") != "confirm_restore_required" or dry_restore.get("dry_run") is not True:
                failures.append("Host restore did not require explicit confirmation")

            (host_home / "run" / "host.pid.json").write_text(
                json.dumps({"pid": os.getpid()}), encoding="utf-8"
            )
            running_restore, running_output = run_host(
                env,
                "restore",
                "--backup",
                str(backup_path),
                "--confirm-restore",
                expected=(2,),
            )
            if running_restore.get("error") != "host_running":
                failures.append("Host restore did not fail closed while the managed PID was alive")
            (host_home / "run" / "host.pid.json").unlink()

            with sqlite3.connect(db_path) as conn:
                conn.execute("DELETE FROM agents")
            restored, restore_output = run_host(
                env,
                "restore",
                "--backup",
                str(backup_path),
                "--confirm-restore",
            )
            with sqlite3.connect(db_path) as conn:
                restored_agents = int(conn.execute("SELECT COUNT(*) FROM agents").fetchone()[0])
                revoked_auth = {
                    table: int(conn.execute(f"SELECT COUNT(*) FROM {table} WHERE status='revoked'").fetchone()[0])
                    for table in ("human_sessions", "agent_gateway_sessions", "agent_gateway_tokens")
                }
            pre_restore_copy = Path(str(restored.get("pre_restore_copy") or ""))
            evidence["restore"] = {
                "ok": restored.get("ok"),
                "restored_agents": restored_agents,
                "pre_restore_copy": pre_restore_copy.is_file(),
                "restart_required": restored.get("restart_required"),
                "secret_store_restored": restored.get("secret_store_restored"),
                "hashed_auth_records_restored": restored.get("hashed_auth_records_restored"),
                "restored_auth_state_revoked": restored.get("restored_auth_state_revoked"),
                "secrets_preserved": digest(secrets_path) == secrets_hash_before,
                "restored_integrity": restored.get("restored_integrity_check"),
                "restored_foreign_keys": restored.get("restored_foreign_key_check"),
                "atomic_replace": restored.get("atomic_replace"),
                "revoked_auth_state": revoked_auth,
            }
            if not restored.get("ok") or restored_agents != 1 or not pre_restore_copy.is_file():
                failures.append("Confirmed Host restore did not restore ledger state with a safety copy")
            if (
                restored.get("secret_store_restored") is not False
                or restored.get("hashed_auth_records_restored") is not True
                or restored.get("restored_auth_state_revoked") is not True
                or digest(secrets_path) != secrets_hash_before
            ):
                failures.append("Host restore changed the separate credential store")
            if (
                restored.get("restored_integrity_check") != "ok"
                or restored.get("restored_foreign_key_check") != "ok"
                or restored.get("atomic_replace") is not True
                or any(count != 1 for count in revoked_auth.values())
            ):
                failures.append("Host restore did not verify the staged DB or revoke restored auth state")

            combined_output = backup_output + missing_output + tampered_output + verify_output + dry_output + running_output + restore_output
            if any(str(secret) and str(secret) in combined_output for secret in secret_values):
                failures.append("Host backup/restore output exposed credential material")
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            failures.append(f"backup/restore exception: {type(exc).__name__}: {str(exc)[:180]}")

    print(json.dumps({
        "ok": not failures,
        "operation": "private_host_backup_restore_smoke",
        "temporary_host_home": True,
        "real_user_database_used": False,
        "raw_rows_printed": False,
        "credential_values_omitted": True,
        "evidence": evidence,
        "failures": failures,
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
