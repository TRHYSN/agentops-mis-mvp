#!/usr/bin/env python3
"""Verify the legacy Agent workspace-membership backfill is idempotent."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LEGACY_AGENT_ID = "agt_legacy_membership_fixture"
REMOTE_PLAN_AGENT_ID = "agt_remote_plan_membership_fixture"
REMOTE_PLAN_ID = "plan_remote_membership_fixture"
REMOTE_WORKSPACE_ID = "workspace-remote"
MIGRATION_ID = "2026-07-22-workspace-agent-membership-authority"


def create_legacy_database(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """CREATE TABLE agents (
                agent_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                role TEXT NOT NULL,
                description TEXT,
                runtime_type TEXT NOT NULL,
                model_provider TEXT,
                model_name TEXT,
                status TEXT NOT NULL,
                permission_level TEXT NOT NULL,
                allowed_tools TEXT NOT NULL,
                budget_limit_usd REAL NOT NULL DEFAULT 0,
                owner_user_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE agent_plans (
                plan_id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL DEFAULT 'local-demo',
                task_id TEXT,
                run_id TEXT,
                agent_id TEXT NOT NULL,
                task_understanding TEXT NOT NULL,
                referenced_specs_json TEXT NOT NULL DEFAULT '[]',
                referenced_memories_json TEXT NOT NULL DEFAULT '[]',
                referenced_bases_json TEXT NOT NULL DEFAULT '[]',
                proposed_files_to_change_json TEXT NOT NULL DEFAULT '[]',
                risk_level TEXT NOT NULL CHECK(risk_level IN ('low','medium','high','critical')),
                approval_required INTEGER NOT NULL DEFAULT 0,
                execution_steps_json TEXT NOT NULL DEFAULT '[]',
                verification_plan TEXT,
                rollback_plan TEXT,
                status TEXT NOT NULL CHECK(status IN ('draft','submitted','approved','rejected','superseded')),
                plan_version INTEGER NOT NULL DEFAULT 1,
                plan_hash TEXT,
                verified_at TEXT,
                verification_result_hash TEXT,
                approval_id TEXT,
                approved_by_user_id TEXT,
                approved_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(agent_id) REFERENCES agents(agent_id)
            )"""
        )
        conn.executemany(
            """INSERT INTO agents(
                agent_id,name,role,description,runtime_type,model_provider,
                model_name,status,permission_level,allowed_tools,budget_limit_usd,
                owner_user_id,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [
                (
                    LEGACY_AGENT_ID,
                    "Legacy local Agent",
                    "operator",
                    "Unanchored migration fixture",
                    "openclaw",
                    "local",
                    "fixture",
                    "idle",
                    "standard",
                    "[]",
                    0.0,
                    None,
                    "2026-01-01T00:00:00+00:00",
                    "2026-01-01T00:00:00+00:00",
                ),
                (
                    REMOTE_PLAN_AGENT_ID,
                    "Remote plan Agent",
                    "operator",
                    "Non-local Agent Plan authority fixture",
                    "openclaw",
                    "local",
                    "fixture",
                    "idle",
                    "standard",
                    "[]",
                    0.0,
                    None,
                    "2026-01-01T00:00:00+00:00",
                    "2026-01-01T00:00:00+00:00",
                ),
            ],
        )
        conn.execute(
            """INSERT INTO agent_plans(
                plan_id,workspace_id,task_id,run_id,agent_id,task_understanding,
                risk_level,approval_required,status,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                REMOTE_PLAN_ID,
                REMOTE_WORKSPACE_ID,
                None,
                None,
                REMOTE_PLAN_AGENT_ID,
                "Retain the existing non-local Agent Plan authority.",
                "low",
                0,
                "draft",
                "2026-01-01T00:00:00+00:00",
                "2026-01-01T00:00:00+00:00",
            ),
        )


def init_in_subprocess(db_path: Path, isolated_home: Path) -> int:
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
        [sys.executable, "-c", "import server; server.init_schema()"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        check=False,
        text=True,
        timeout=90,
    )
    return completed.returncode


def visible_agent_ids_in_subprocess(
    db_path: Path,
    isolated_home: Path,
    workspace_id: str,
) -> tuple[int, list[str]]:
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
        "WORKSPACE_ID": workspace_id,
    }
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """import json, os, server
conn = server.db()
try:
    visibility = server.agent_workspace_visibility_sql('a', '?1')
    rows = conn.execute(
        f'SELECT a.agent_id FROM agents a WHERE {visibility} ORDER BY a.agent_id',
        (os.environ['WORKSPACE_ID'],),
    ).fetchall()
    print(json.dumps([row['agent_id'] for row in rows]))
finally:
    conn.close()
""",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        check=False,
        text=True,
        timeout=90,
    )
    if completed.returncode != 0:
        return completed.returncode, []
    try:
        return completed.returncode, json.loads(completed.stdout or "[]")
    except json.JSONDecodeError:
        return completed.returncode, []


def main() -> int:
    failures: list[str] = []
    init_exit_codes: list[int] = []

    with tempfile.TemporaryDirectory(prefix="agentops-membership-migration-") as tmp:
        isolated_root = Path(tmp)
        db_path = isolated_root / "legacy.db"
        create_legacy_database(db_path)

        for _ in range(2):
            init_exit_codes.append(init_in_subprocess(db_path, isolated_root))

        remote_visibility_exit_code, remote_visible_agent_ids = visible_agent_ids_in_subprocess(
            db_path,
            isolated_root,
            REMOTE_WORKSPACE_ID,
        )
        local_visibility_exit_code, local_visible_agent_ids = visible_agent_ids_in_subprocess(
            db_path,
            isolated_root,
            "local-demo",
        )

        if init_exit_codes != [0, 0]:
            failures.append(f"init subprocess exit codes were {init_exit_codes}")

        with sqlite3.connect(db_path) as conn:
            membership_total = conn.execute(
                "SELECT COUNT(*) FROM workspace_agent_memberships WHERE agent_id=?",
                (LEGACY_AGENT_ID,),
            ).fetchone()[0]
            local_backfill_count = conn.execute(
                """SELECT COUNT(*) FROM workspace_agent_memberships
                   WHERE agent_id=? AND workspace_id='local-demo'
                     AND source='legacy-local-backfill'""",
                (LEGACY_AGENT_ID,),
            ).fetchone()[0]
            remote_plan_membership_total = conn.execute(
                "SELECT COUNT(*) FROM workspace_agent_memberships WHERE agent_id=?",
                (REMOTE_PLAN_AGENT_ID,),
            ).fetchone()[0]
            remote_plan_local_backfill_count = conn.execute(
                """SELECT COUNT(*) FROM workspace_agent_memberships
                   WHERE agent_id=? AND workspace_id='local-demo'
                     AND source='legacy-local-backfill'""",
                (REMOTE_PLAN_AGENT_ID,),
            ).fetchone()[0]
            migration_count = conn.execute(
                "SELECT COUNT(*) FROM schema_migrations WHERE migration_id=?",
                (MIGRATION_ID,),
            ).fetchone()[0]

        if membership_total != 1:
            failures.append(f"legacy Agent membership count was {membership_total}, expected 1")
        if local_backfill_count != 1:
            failures.append(f"local-demo backfill count was {local_backfill_count}, expected 1")
        if remote_plan_membership_total != 0:
            failures.append(
                "remote-plan Agent membership count was "
                f"{remote_plan_membership_total}, expected 0"
            )
        if remote_plan_local_backfill_count != 0:
            failures.append(
                "remote-plan Agent local-demo backfill count was "
                f"{remote_plan_local_backfill_count}, expected 0"
            )
        if remote_visibility_exit_code != 0:
            failures.append(
                f"remote Agent visibility subprocess exited {remote_visibility_exit_code}"
            )
        if REMOTE_PLAN_AGENT_ID not in remote_visible_agent_ids:
            failures.append("remote-plan Agent was not visible in its Plan workspace")
        if local_visibility_exit_code != 0:
            failures.append(
                f"local Agent visibility subprocess exited {local_visibility_exit_code}"
            )
        if REMOTE_PLAN_AGENT_ID in local_visible_agent_ids:
            failures.append("remote-plan Agent leaked into local-demo visibility")
        if migration_count != 1:
            failures.append(f"migration row count was {migration_count}, expected 1")

    result = {
        "ok": not failures,
        "operation": "workspace_agent_membership_migration_smoke",
        "init_exit_codes": init_exit_codes,
        "unanchored_legacy_agent_memberships": {
            "total": membership_total,
            "local_demo_backfill": local_backfill_count,
        },
        "remote_plan_agent_memberships": {
            "total": remote_plan_membership_total,
            "local_demo_backfill": remote_plan_local_backfill_count,
        },
        "remote_plan_agent_visibility": {
            "remote_workspace_visible": REMOTE_PLAN_AGENT_ID in remote_visible_agent_ids,
            "local_demo_visible": REMOTE_PLAN_AGENT_ID in local_visible_agent_ids,
        },
        "migration_rows": migration_count,
        "failures": failures,
        "secrets_omitted": True,
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
