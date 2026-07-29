#!/usr/bin/env python3
"""Verify legacy memories gain deterministic workspace authority exactly once."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_ID = "2026-07-23-memory-workspace-authority"
TASK_MEMORY_ID = "mem_legacy_workspace_task_fixture"
TASKLESS_MEMORY_ID = "mem_legacy_workspace_taskless_fixture"
DIRECT_MEMORY_ID = "mem_post_migration_direct_write_fixture"
WORKSPACE_ID = "ws_memory_workspace_authority_fixture"

LEGACY_MEMORIES_SQL = """
CREATE TABLE memories_legacy (
    memory_id TEXT PRIMARY KEY,
    scope TEXT NOT NULL CHECK(scope IN ('task','project','org')),
    memory_type TEXT NOT NULL CHECK(memory_type IN ('policy','sop','decision','commitment','risk','failure_case','project_context','customer_preference','agent_lesson','artifact_summary','loop_record')),
    canonical_text TEXT NOT NULL,
    source_type TEXT NOT NULL CHECK(source_type IN ('chat','email','meeting','github','notion','run_log','manual')),
    source_ref TEXT,
    project_id TEXT,
    task_id TEXT,
    agent_id TEXT,
    confidence REAL NOT NULL DEFAULT 0.5,
    review_status TEXT NOT NULL CHECK(review_status IN ('candidate','approved','rejected','stale','superseded')),
    owner_user_id TEXT,
    ttl_review_due_at TEXT,
    supersedes_memory_id TEXT,
    access_tags TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(task_id) REFERENCES tasks(task_id),
    FOREIGN KEY(agent_id) REFERENCES agents(agent_id),
    FOREIGN KEY(owner_user_id) REFERENCES users(user_id),
    FOREIGN KEY(supersedes_memory_id) REFERENCES memories_legacy(memory_id)
)
"""

LEGACY_COLUMNS = (
    "memory_id,scope,memory_type,canonical_text,source_type,source_ref,project_id,"
    "task_id,agent_id,confidence,review_status,owner_user_id,ttl_review_due_at,"
    "supersedes_memory_id,access_tags,created_at,updated_at"
)


def server_call(db_path: Path, isolated_home: Path, expression: str) -> int:
    env = {
        "AGENTOPS_DB_PATH": str(db_path),
        "AGENTOPS_DEPLOYMENT_MODE": "local",
        "AGENTOPS_SKIP_SEED_EXPORTS": "1",
        "DIFY_ALLOW_REAL_UPLOAD": "false",
        "HERMES_ALLOW_REAL_RUN": "false",
        "HERMES_REQUIRE_CONFIRM_RUN": "true",
        "HOME": str(isolated_home),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "PYTHONUNBUFFERED": "1",
        "TMPDIR": str(isolated_home),
    }
    completed = subprocess.run(
        [sys.executable, "-c", expression],
        cwd=ROOT,
        env=env,
        capture_output=True,
        check=False,
        text=True,
        timeout=90,
    )
    return completed.returncode


def make_legacy_fixture(db_path: Path) -> str:
    with sqlite3.connect(db_path) as conn:
        task_id = conn.execute("SELECT task_id FROM tasks ORDER BY created_at LIMIT 1").fetchone()[0]
        conn.execute("UPDATE tasks SET workspace_id=? WHERE task_id=?", (WORKSPACE_ID, task_id))
        now = "2026-01-01T00:00:00+00:00"
        common = (
            "project",
            "project_context",
            "Legacy migration fixture with bounded non-sensitive context.",
            "manual",
            "migration://memory-workspace-authority",
            "proj_mvp",
            None,
            None,
            0.9,
            "approved",
            None,
            None,
            None,
            '["migration-fixture"]',
            now,
            now,
        )
        conn.execute(
            f"INSERT INTO memories({LEGACY_COLUMNS}) VALUES({','.join('?' for _ in range(17))})",
            (TASK_MEMORY_ID, *common[:6], task_id, *common[7:]),
        )
        conn.execute(
            f"INSERT INTO memories({LEGACY_COLUMNS}) VALUES({','.join('?' for _ in range(17))})",
            (TASKLESS_MEMORY_ID, *common),
        )
        for trigger in (
            "trg_memories_workspace_from_task_insert",
            "trg_memories_workspace_from_task_update",
            "trg_task_workspace_to_memories",
        ):
            conn.execute(f"DROP TRIGGER IF EXISTS {trigger}")
        conn.execute(LEGACY_MEMORIES_SQL)
        conn.execute(
            f"INSERT INTO memories_legacy({LEGACY_COLUMNS}) SELECT {LEGACY_COLUMNS} FROM memories"
        )
        conn.execute("DROP TABLE memories")
        conn.execute("ALTER TABLE memories_legacy RENAME TO memories")
        conn.execute("DELETE FROM schema_migrations WHERE migration_id=?", (MIGRATION_ID,))
        conn.commit()
    return task_id


def main() -> int:
    failures: list[str] = []
    init_exit_codes: list[int] = []
    task_workspace = None
    taskless_workspace = None
    migration_rows = 0
    workspace_column = None
    workspace_index = False
    authority_trigger_count = 0
    direct_write_workspace = None
    baseline_foreign_key_violation_count = 0
    foreign_key_violation_count = 0
    memory_foreign_key_violation_count = 0
    memory_foreign_key_parents: list[str] = []
    initial = -1
    with tempfile.TemporaryDirectory(prefix="agentops-memory-workspace-migration-") as tmp:
        isolated_root = Path(tmp)
        db_path = isolated_root / "legacy.db"
        initial = server_call(db_path, isolated_root, "import server; server.seed(reset=False)")
        if initial != 0:
            failures.append(f"initial seed exit code was {initial}")
        else:
            with sqlite3.connect(db_path) as conn:
                baseline_foreign_key_violation_count = len(conn.execute("PRAGMA foreign_key_check").fetchall())
            task_id = make_legacy_fixture(db_path)
            for _ in range(2):
                init_exit_codes.append(server_call(db_path, isolated_root, "import server; server.init_schema()"))
            with sqlite3.connect(db_path) as conn:
                now = "2026-01-02T00:00:00+00:00"
                conn.execute(
                    f"INSERT INTO memories({LEGACY_COLUMNS}) VALUES({','.join('?' for _ in range(17))})",
                    (
                        DIRECT_MEMORY_ID,
                        "project",
                        "project_context",
                        "Post-migration direct writer fixture.",
                        "manual",
                        "migration://direct-writer",
                        "proj_mvp",
                        task_id,
                        None,
                        0.8,
                        "candidate",
                        None,
                        None,
                        None,
                        '["migration-fixture"]',
                        now,
                        now,
                    ),
                )
                conn.execute(
                    "UPDATE memories SET workspace_id='local-demo' WHERE memory_id=?",
                    (DIRECT_MEMORY_ID,),
                )
                task_workspace = conn.execute(
                    "SELECT workspace_id FROM memories WHERE memory_id=? AND task_id=?",
                    (TASK_MEMORY_ID, task_id),
                ).fetchone()
                taskless_workspace = conn.execute(
                    "SELECT workspace_id FROM memories WHERE memory_id=? AND task_id IS NULL",
                    (TASKLESS_MEMORY_ID,),
                ).fetchone()
                migration_rows = conn.execute(
                    "SELECT COUNT(*) FROM schema_migrations WHERE migration_id=?",
                    (MIGRATION_ID,),
                ).fetchone()[0]
                workspace_column = next(
                    (row for row in conn.execute("PRAGMA table_info(memories)") if row[1] == "workspace_id"),
                    None,
                )
                workspace_index = any(
                    row[1] == "idx_memories_workspace"
                    for row in conn.execute("PRAGMA index_list(memories)")
                )
                authority_trigger_count = conn.execute(
                    """SELECT COUNT(*) FROM sqlite_master
                       WHERE type='trigger' AND name IN (
                           'trg_memories_workspace_from_task_insert',
                           'trg_memories_workspace_from_task_update',
                           'trg_task_workspace_to_memories'
                       )"""
                ).fetchone()[0]
                direct_write_workspace = conn.execute(
                    "SELECT workspace_id FROM memories WHERE memory_id=?",
                    (DIRECT_MEMORY_ID,),
                ).fetchone()[0]
                foreign_key_violations = conn.execute("PRAGMA foreign_key_check").fetchall()
                foreign_key_violation_count = len(foreign_key_violations)
                memory_foreign_key_violation_count = sum(
                    1 for row in foreign_key_violations if "memories" in {row[0], row[2]}
                )
                memory_foreign_key_parents = sorted({
                    str(row[2]) for row in foreign_key_violations if "memories" in {row[0], row[2]}
                })

        task_workspace_value = task_workspace[0] if task_workspace else None
        taskless_workspace_value = taskless_workspace[0] if taskless_workspace else None
        if init_exit_codes != [0, 0]:
            failures.append(f"migration init exit codes were {init_exit_codes}")
        if task_workspace_value != WORKSPACE_ID:
            failures.append(f"task-bound workspace was {task_workspace_value!r}, expected {WORKSPACE_ID!r}")
        if taskless_workspace_value != "local-demo":
            failures.append(f"taskless workspace was {taskless_workspace_value!r}, expected 'local-demo'")
        if migration_rows != 1:
            failures.append(f"migration row count was {migration_rows}, expected 1")
        if not workspace_column or workspace_column[3] != 1:
            failures.append("memories.workspace_id is missing or nullable")
        if not workspace_index:
            failures.append("idx_memories_workspace is missing")
        if authority_trigger_count != 3:
            failures.append(f"memory workspace authority trigger count was {authority_trigger_count}, expected 3")
        if direct_write_workspace != WORKSPACE_ID:
            failures.append(f"direct task-bound write workspace was {direct_write_workspace!r}, expected {WORKSPACE_ID!r}")
        if foreign_key_violation_count != baseline_foreign_key_violation_count:
            failures.append(
                "foreign key violation count changed across migration: "
                f"{baseline_foreign_key_violation_count} -> {foreign_key_violation_count}"
            )
        if memory_foreign_key_violation_count:
            failures.append(f"memory foreign key violations after migration: {memory_foreign_key_violation_count}")

    print(json.dumps({
        "ok": not failures,
        "operation": "memory_workspace_authority_migration_smoke",
        "initial_seed_exit_code": initial,
        "migration_init_exit_codes": init_exit_codes,
        "task_bound_workspace_backfilled": task_workspace_value == WORKSPACE_ID,
        "taskless_legacy_memory_conservative": taskless_workspace_value == "local-demo",
        "migration_rows": migration_rows,
        "workspace_column_not_null": bool(workspace_column and workspace_column[3] == 1),
        "workspace_index_present": workspace_index,
        "authority_trigger_count": authority_trigger_count,
        "direct_task_write_authority_enforced": direct_write_workspace == WORKSPACE_ID,
        "baseline_foreign_key_violation_count": baseline_foreign_key_violation_count,
        "foreign_key_violation_count": foreign_key_violation_count,
        "memory_foreign_key_violation_count": memory_foreign_key_violation_count,
        "memory_foreign_key_parents": memory_foreign_key_parents,
        "failures": failures,
        "credentials_omitted": True,
    }, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
