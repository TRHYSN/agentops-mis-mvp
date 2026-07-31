#!/usr/bin/env python3
"""Verify task requester validation is bounded, atomic, and deterministic."""
from __future__ import annotations

import datetime as dt
import importlib
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def stamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d%H%M%S%f")


def evidence_counts(conn: sqlite3.Connection, task_id: str) -> dict[str, int]:
    return {
        "tasks": conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE task_id=?",
            (task_id,),
        ).fetchone()[0],
        "runtime_events": conn.execute(
            "SELECT COUNT(*) FROM runtime_events WHERE task_id=?",
            (task_id,),
        ).fetchone()[0],
        "audit_logs": conn.execute(
            """SELECT COUNT(*) FROM audit_logs
               WHERE entity_type='tasks' AND entity_id=?""",
            (task_id,),
        ).fetchone()[0],
    }


def create_body(task_id: str, requester_id: str | None) -> dict[str, object]:
    body: dict[str, object] = {
        "task_id": task_id,
        "title": "Task requester validation smoke",
        "description": "Reject an unknown requester before any ledger write.",
        "owner_agent_id": "agt_research",
        "acceptance": "The API returns a bounded 400 without partial writes.",
        "priority": "medium",
        "risk_level": "low",
    }
    if requester_id is not None:
        body["requester_id"] = requester_id
    return body


def main() -> int:
    failures: list[str] = []
    suffix = stamp()
    with tempfile.TemporaryDirectory(
        prefix="agentops-task-requester-validation-"
    ) as temporary:
        database_path = Path(temporary) / "agentops.db"
        previous_database = os.environ.get("AGENTOPS_DB_PATH")
        previous_skip_exports = os.environ.get("AGENTOPS_SKIP_SEED_EXPORTS")
        os.environ["AGENTOPS_DB_PATH"] = str(database_path)
        os.environ["AGENTOPS_SKIP_SEED_EXPORTS"] = "1"
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        try:
            server = importlib.import_module("server")
            server.seed(reset=True)
            with server.db_session() as conn:
                missing_task_id = f"tsk_missing_requester_{suffix}"
                private_canary = (
                    f"usr_missing_{suffix}_"
                    "PRIVATE_REQUESTER_CANARY_DO_NOT_REFLECT"
                )
                before = evidence_counts(conn, missing_task_id)
                rejected, rejected_status = server.create_task_api(
                    conn,
                    create_body(missing_task_id, private_canary),
                )
                after = evidence_counts(conn, missing_task_id)
                serialized_rejection = json.dumps(
                    rejected,
                    ensure_ascii=False,
                    sort_keys=True,
                )
                require(
                    rejected_status == 400,
                    f"unknown requester returned {rejected_status}",
                    failures,
                )
                require(
                    rejected
                    == {
                        "error": "requester_user_not_found",
                        "message": "Task requester user does not exist.",
                        "token_omitted": True,
                    },
                    f"unknown requester projection changed: {rejected}",
                    failures,
                )
                require(
                    before == after == {
                        "tasks": 0,
                        "runtime_events": 0,
                        "audit_logs": 0,
                    },
                    f"unknown requester produced partial writes: {before} -> {after}",
                    failures,
                )
                require(
                    private_canary not in serialized_rejection
                    and "FOREIGN KEY" not in serialized_rejection
                    and "sqlite" not in serialized_rejection.lower(),
                    "unknown requester response leaked input or storage detail",
                    failures,
                )

                valid_task_id = f"tsk_valid_requester_{suffix}"
                valid, valid_status = server.create_task_api(
                    conn,
                    create_body(valid_task_id, "usr_customer_demo"),
                )
                require(
                    valid_status == 201
                    and valid.get("ok") is True
                    and (valid.get("task") or {}).get("requester_id")
                    == "usr_customer_demo",
                    f"valid requester failed: {valid_status} {valid}",
                    failures,
                )

                valid_counts = evidence_counts(conn, valid_task_id)
                before_update = conn.execute(
                    "SELECT title, requester_id FROM tasks WHERE task_id=?",
                    (valid_task_id,),
                ).fetchone()
                rejected_update, rejected_update_status = (
                    server.create_task_api(
                        conn,
                        {
                            **create_body(valid_task_id, private_canary),
                            "title": "This update must not persist",
                        },
                    )
                )
                after_update = conn.execute(
                    "SELECT title, requester_id FROM tasks WHERE task_id=?",
                    (valid_task_id,),
                ).fetchone()
                require(
                    rejected_update_status == 400
                    and rejected_update.get("error")
                    == "requester_user_not_found",
                    "unknown requester update was not rejected",
                    failures,
                )
                require(
                    tuple(before_update or ())
                    == tuple(after_update or ())
                    == (
                        "Task requester validation smoke",
                        "usr_customer_demo",
                    ),
                    "rejected update changed the existing task",
                    failures,
                )
                require(
                    evidence_counts(conn, valid_task_id) == valid_counts,
                    "rejected update added runtime or audit evidence",
                    failures,
                )

                default_task_id = f"tsk_default_requester_{suffix}"
                defaulted, defaulted_status = server.create_task_api(
                    conn,
                    create_body(default_task_id, None),
                )
                require(
                    defaulted_status == 201
                    and (defaulted.get("task") or {}).get("requester_id")
                    == "usr_customer_demo",
                    f"default requester changed: {defaulted_status} {defaulted}",
                    failures,
                )
        finally:
            if previous_database is None:
                os.environ.pop("AGENTOPS_DB_PATH", None)
            else:
                os.environ["AGENTOPS_DB_PATH"] = previous_database
            if previous_skip_exports is None:
                os.environ.pop("AGENTOPS_SKIP_SEED_EXPORTS", None)
            else:
                os.environ["AGENTOPS_SKIP_SEED_EXPORTS"] = (
                    previous_skip_exports
                )

    output = {
        "default_requester_preserved": not failures,
        "failures": failures,
        "ok": not failures,
        "partial_writes_on_rejection": False,
        "raw_requester_omitted": True,
        "rejected_status": 400,
        "schema_id": "agentops.task-requester-validation.v0",
        "token_omitted": True,
        "unknown_requester_error": "requester_user_not_found",
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
