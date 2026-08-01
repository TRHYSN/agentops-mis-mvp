#!/usr/bin/env python3
"""Verify legacy Agent Plan approvals gain immutable subject authority once."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_ID = "2026-07-22-agent-plan-approval-subject-authority"
PLAN_ID = "plan_legacy_approval_subject_fixture"
APPROVAL_ID = "ap_legacy_approval_subject_fixture"


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


def insert_legacy_fixture(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        anchor = conn.execute(
            """SELECT r.run_id,r.task_id,r.agent_id,COALESCE(r.workspace_id,'local-demo')
            FROM runs r
            WHERE r.task_id IS NOT NULL AND r.agent_id IS NOT NULL
            ORDER BY r.created_at LIMIT 1"""
        ).fetchone()
        if not anchor:
            raise RuntimeError("seeded run authority anchor missing")
        run_id, task_id, agent_id, workspace_id = anchor
        now = "2026-01-01T00:00:00+00:00"
        conn.execute(
            """INSERT INTO agent_plans(
                plan_id,workspace_id,task_id,run_id,agent_id,task_understanding,
                referenced_specs_json,referenced_memories_json,referenced_bases_json,
                proposed_files_to_change_json,risk_level,approval_required,
                execution_steps_json,verification_plan,rollback_plan,status,plan_version,
                plan_hash,verified_at,verification_result_hash,approval_id,
                approved_by_user_id,approved_at,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                PLAN_ID,
                workspace_id,
                task_id,
                run_id,
                agent_id,
                "Legacy Agent Plan subject migration fixture.",
                "[]",
                "[]",
                "[]",
                "[]",
                "high",
                1,
                '["READ","PLAN","VERIFY"]',
                "Verify the bounded migration only.",
                "Rollback the temporary database.",
                "submitted",
                1,
                None,
                None,
                None,
                APPROVAL_ID,
                None,
                None,
                now,
                now,
            ),
        )
        conn.execute(
            """INSERT INTO approvals(
                approval_id,task_id,run_id,tool_call_id,requested_by_agent_id,
                approver_user_id,decision,reason,subject_type,subject_id,subject_hash,
                expires_at,created_at,decided_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                APPROVAL_ID,
                task_id,
                run_id,
                None,
                agent_id,
                "usr_founder",
                "pending",
                "Legacy Agent Plan approval without subject authority.",
                None,
                None,
                None,
                None,
                now,
                None,
            ),
        )
        conn.execute(
            "DELETE FROM schema_migrations WHERE migration_id=?",
            (MIGRATION_ID,),
        )


def main() -> int:
    failures: list[str] = []
    init_exit_codes: list[int] = []
    with tempfile.TemporaryDirectory(prefix="agentops-plan-approval-subject-") as tmp:
        isolated_root = Path(tmp)
        db_path = isolated_root / "legacy.db"
        initial = server_call(db_path, isolated_root, "import server; server.seed(reset=False)")
        if initial != 0:
            failures.append(f"initial seed exit code was {initial}")
        else:
            insert_legacy_fixture(db_path)
            for _ in range(2):
                init_exit_codes.append(
                    server_call(db_path, isolated_root, "import server; server.init_schema()")
                )

        plan_hash = None
        subject = None
        migration_count = 0
        if db_path.exists():
            with sqlite3.connect(db_path) as conn:
                plan_row = conn.execute(
                    "SELECT plan_hash FROM agent_plans WHERE plan_id=?",
                    (PLAN_ID,),
                ).fetchone()
                subject = conn.execute(
                    "SELECT subject_type,subject_id,subject_hash FROM approvals WHERE approval_id=?",
                    (APPROVAL_ID,),
                ).fetchone()
                migration_count = conn.execute(
                    "SELECT COUNT(*) FROM schema_migrations WHERE migration_id=?",
                    (MIGRATION_ID,),
                ).fetchone()[0]
                plan_hash = plan_row[0] if plan_row else None

        if init_exit_codes != [0, 0]:
            failures.append(f"migration init exit codes were {init_exit_codes}")
        if not plan_hash:
            failures.append("legacy Agent Plan hash was not backfilled")
        if subject != ("agent_plan", PLAN_ID, plan_hash):
            failures.append(f"approval subject authority mismatch: {subject}")
        if migration_count != 1:
            failures.append(f"migration row count was {migration_count}, expected 1")

    print(json.dumps({
        "ok": not failures,
        "operation": "agent_plan_approval_subject_migration_smoke",
        "initial_seed_exit_code": initial,
        "migration_init_exit_codes": init_exit_codes,
        "plan_hash_backfilled": bool(plan_hash),
        "subject_bound": subject == ("agent_plan", PLAN_ID, plan_hash),
        "migration_rows": migration_count,
        "failures": failures,
        "credentials_omitted": True,
    }, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
