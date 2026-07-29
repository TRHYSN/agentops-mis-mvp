#!/usr/bin/env python3
"""Verify managed SQLite sessions commit, roll back, and close every handle."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def connection_is_closed(conn: sqlite3.Connection) -> bool:
    try:
        conn.execute("SELECT 1")
    except sqlite3.ProgrammingError:
        return True
    return False


def linux_fd_count() -> int | None:
    fd_root = Path("/proc/self/fd")
    if not fd_root.is_dir():
        return None
    return len(list(fd_root.iterdir()))


def main() -> int:
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="agentops-sqlite-lifecycle-") as tmp:
        os.environ["AGENTOPS_DB_PATH"] = str(Path(tmp) / "agentops_lifecycle.db")
        os.environ["AGENTOPS_SKIP_SEED_EXPORTS"] = "1"
        sys.path.insert(0, str(ROOT))

        import server  # noqa: PLC0415

        server.init_schema()
        fd_before = linux_fd_count()
        closed_connections = 0
        for _index in range(96):
            with server.db_session() as conn:
                conn.execute("SELECT 1").fetchone()
            if connection_is_closed(conn):
                closed_connections += 1
        fd_after = linux_fd_count()

        with server.db_session() as conn:
            conn.execute("CREATE TABLE lifecycle_probe(value TEXT NOT NULL)")
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("INSERT INTO lifecycle_probe(value) VALUES('committed')")
        normal_exit_closed = connection_is_closed(conn)

        try:
            with server.db_session() as rollback_conn:
                rollback_conn.execute("BEGIN IMMEDIATE")
                rollback_conn.execute("INSERT INTO lifecycle_probe(value) VALUES('rolled_back')")
                raise RuntimeError("exercise rollback")
        except RuntimeError:
            pass
        exceptional_exit_closed = connection_is_closed(rollback_conn)

        direct_conn = server.db()
        try:
            values = [row["value"] for row in direct_conn.execute(
                "SELECT value FROM lifecycle_probe ORDER BY rowid"
            ).fetchall()]
        finally:
            direct_conn.close()

        source = (ROOT / "server.py").read_text(encoding="utf-8")
        unmanaged_context_calls = re.findall(r"^\s*with\s+db\(", source, flags=re.MULTILINE)
        managed_context_calls = re.findall(r"^\s*with\s+db_session\(", source, flags=re.MULTILINE)

        require(closed_connections == 96, f"managed sessions left open connections: {closed_connections}/96", failures)
        require(normal_exit_closed, "normal db_session exit left its connection open", failures)
        require(exceptional_exit_closed, "exceptional db_session exit left its connection open", failures)
        require(values == ["committed"], f"db_session commit/rollback semantics changed: {values}", failures)
        require(not unmanaged_context_calls, "server still uses sqlite Connection as a closing context manager", failures)
        require(len(managed_context_calls) >= 12, f"managed server call-site coverage is unexpectedly low: {len(managed_context_calls)}", failures)
        if fd_before is not None and fd_after is not None:
            require(fd_after <= fd_before + 2, f"process file descriptors grew across managed sessions: {fd_before} -> {fd_after}", failures)

        result = {
            "ok": not failures,
            "operation": "sqlite_connection_lifecycle_smoke",
            "isolated_database": True,
            "managed_sessions": 96,
            "closed_connections": closed_connections,
            "normal_exit_closed": normal_exit_closed,
            "exceptional_exit_closed": exceptional_exit_closed,
            "committed_values": values,
            "server_managed_context_calls": len(managed_context_calls),
            "server_unmanaged_context_calls": len(unmanaged_context_calls),
            "fd_count": {
                "supported": fd_before is not None,
                "before": fd_before,
                "after": fd_after,
            },
            "database_content_omitted": True,
            "token_omitted": True,
            "failures": failures,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
