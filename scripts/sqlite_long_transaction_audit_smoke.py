#!/usr/bin/env python3
"""Audit long workflow calls for SQLite transaction safety."""

from __future__ import annotations

import ast
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVER_PATH = ROOT / "server.py"
TRANSACTION_PREFIXES = ("BEGIN", "SAVEPOINT", "START TRANSACTION")
ALLOWED_TRANSACTION_STATEMENTS = {
    ("sqlite_atomic_write", "BEGIN IMMEDIATE"),
}


def dotted_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = dotted_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    if isinstance(node, ast.Call):
        return dotted_name(node.func)
    return ""


def constant_sql(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def normalized_sql_prefix(sql: str) -> str:
    return " ".join(sql.strip().upper().split())[:80]


def is_sqlite_connect_call(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and dotted_name(node.func) == "sqlite3.connect"


def is_slow_call(node: ast.AST) -> str | None:
    if not isinstance(node, ast.Call):
        return None
    name = dotted_name(node.func)
    if name == "subprocess.run":
        return "subprocess.run"
    if name == "urlopen":
        return "urlopen"
    if name == "time.sleep":
        return "time.sleep"
    if name == "threading.Thread.start":
        return "threading.Thread(...).start"
    return None


def function_name_for(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> str:
    current = node
    while current in parents:
        current = parents[current]
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return current.name
    return "<module>"


def inside_connection_context(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    current = node
    while current in parents:
        current = parents[current]
        if isinstance(current, ast.With):
            for item in current.items:
                expr_name = dotted_name(item.context_expr)
                if expr_name in {"db", "conn"}:
                    return True
                if isinstance(item.context_expr, ast.Call) and dotted_name(item.context_expr.func) == "db":
                    return True
    return False


def static_audit() -> dict:
    tree = ast.parse(SERVER_PATH.read_text(encoding="utf-8"), filename=str(SERVER_PATH))
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    failures: list[str] = []
    slow_calls: list[dict] = []
    transaction_statements: list[dict] = []
    sqlite_connects: list[dict] = []

    for node in ast.walk(tree):
        if is_sqlite_connect_call(node):
            isolation_level = None
            has_isolation_level = False
            for keyword in node.keywords:
                if keyword.arg == "isolation_level":
                    has_isolation_level = True
                    isolation_level = ast.literal_eval(keyword.value) if isinstance(keyword.value, ast.Constant) else "<dynamic>"
            entry = {
                "line": node.lineno,
                "function": function_name_for(node, parents),
                "has_isolation_level": has_isolation_level,
                "isolation_level": isolation_level,
            }
            sqlite_connects.append(entry)
            if isolation_level is not None:
                failures.append(f"sqlite3.connect at line {node.lineno} is not explicit autocommit")

        slow_kind = is_slow_call(node)
        if slow_kind:
            slow_calls.append({
                "line": node.lineno,
                "function": function_name_for(node, parents),
                "kind": slow_kind,
                "inside_connection_context": inside_connection_context(node, parents),
            })

        if isinstance(node, ast.Call) and dotted_name(node.func).endswith((".execute", ".executescript")):
            sql = constant_sql(node.args[0]) if node.args else None
            if sql and normalized_sql_prefix(sql).startswith(TRANSACTION_PREFIXES):
                transaction_statements.append({
                    "line": node.lineno,
                    "function": function_name_for(node, parents),
                    "sql_prefix": normalized_sql_prefix(sql),
                })

    allowed_transaction_statements = [
        item
        for item in transaction_statements
        if (item["function"], item["sql_prefix"]) in ALLOWED_TRANSACTION_STATEMENTS
    ]
    unexpected_transaction_statements = [
        item
        for item in transaction_statements
        if (item["function"], item["sql_prefix"]) not in ALLOWED_TRANSACTION_STATEMENTS
    ]
    if unexpected_transaction_statements:
        failures.append(
            f"unexpected explicit transaction statements found: {unexpected_transaction_statements}"
        )
    if len(allowed_transaction_statements) != 1:
        failures.append(
            "sqlite_atomic_write must own exactly one BEGIN IMMEDIATE statement"
        )
    if not any(item["function"] == "db" and item["isolation_level"] is None for item in sqlite_connects):
        failures.append("server.db() does not create an explicit autocommit SQLite connection")
    if not slow_calls:
        failures.append("no long-running calls were found; audit selector may be stale")

    return {
        "ok": not failures,
        "sqlite_connects": sqlite_connects,
        "slow_call_count": len(slow_calls),
        "slow_calls": slow_calls,
        "transaction_statements": unexpected_transaction_statements,
        "allowed_transaction_statements": allowed_transaction_statements,
        "failures": failures,
    }


def runtime_concurrent_write_smoke() -> dict:
    completed_process = subprocess.CompletedProcess
    with tempfile.TemporaryDirectory(prefix="agentops-sqlite-long-txn-") as tmp:
        db_path = Path(tmp) / "agentops_long_txn.db"
        os.environ["AGENTOPS_DB_PATH"] = str(db_path)
        os.environ["AGENTOPS_SKIP_SEED_EXPORTS"] = "1"
        sys.path.insert(0, str(ROOT))

        import server  # noqa: PLC0415

        server.DB_PATH = db_path
        server.seed(reset=True)
        original_run = server.subprocess.run
        runtime_event_id = "rtc_sqlite_long_txn_concurrent_write"
        concurrent_write = {"ok": False, "elapsed_ms": None, "error": None}

        def fake_run(cmd, *args, **kwargs):
            started = time.monotonic()
            time.sleep(0.2)
            try:
                writer = sqlite3.connect(db_path, timeout=1, isolation_level=None)
                try:
                    writer.execute("PRAGMA foreign_keys = ON")
                    writer.execute("PRAGMA busy_timeout = 1000")
                    writer.execute(
                        """INSERT INTO runtime_events(
                            runtime_event_id, runtime_connector_id, event_type, status,
                            run_id, task_id, agent_id, model_name, latency_ms, prompt_hash,
                            input_summary, output_summary, error_message, raw_payload_hash, created_at
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            runtime_event_id,
                            None,
                            "sqlite.long_transaction.concurrent_write",
                            "completed",
                            None,
                            None,
                            None,
                            None,
                            0,
                            None,
                            "Concurrent write while mocked KB bot subprocess is running.",
                            "Write succeeded; no long write transaction blocked the control plane.",
                            None,
                            server.stable_hash({"runtime_event_id": runtime_event_id}),
                            server.now_iso(),
                        ),
                    )
                    concurrent_write["ok"] = True
                finally:
                    writer.close()
            except Exception as exc:  # pragma: no cover - surfaced in JSON failure output
                concurrent_write["error"] = str(exc)
            concurrent_write["elapsed_ms"] = int((time.monotonic() - started) * 1000)
            time.sleep(0.2)
            payload = {
                "project_id": "sqlite_long_transaction_smoke",
                "results": [{
                    "task_id": "tsk_sqlite_long_transaction_smoke",
                    "run_id": "run_sqlite_long_transaction_smoke",
                    "artifact_id": "art_sqlite_long_transaction_smoke",
                }],
            }
            return completed_process(cmd, 0, stdout=json.dumps(payload), stderr="")

        try:
            server.subprocess.run = fake_run
            with server.db() as conn:
                result = server.run_kb_bot_project_workflow(conn, {"base_url": "http://127.0.0.1:0"})
        finally:
            server.subprocess.run = original_run

        conn = server.db()
        try:
            written = conn.execute(
                "SELECT COUNT(*) AS count FROM runtime_events WHERE runtime_event_id=?",
                (runtime_event_id,),
            ).fetchone()["count"]
        finally:
            conn.close()

        failures: list[str] = []
        if not concurrent_write["ok"]:
            failures.append(f"concurrent write failed: {concurrent_write['error']}")
        if int(written or 0) != 1:
            failures.append(f"concurrent runtime_event missing: {written}")
        if not result.get("ok"):
            failures.append(f"mocked workflow failed: {result}")

        return {
            "ok": not failures,
            "db_path": str(db_path),
            "workflow": "formal_ai_knowledge_base_qa_bot",
            "concurrent_write_during_subprocess": concurrent_write,
            "written_runtime_events": int(written or 0),
            "workflow_ok": bool(result.get("ok")),
            "seed_exports_skipped": os.environ.get("AGENTOPS_SKIP_SEED_EXPORTS") == "1",
            "failures": failures,
        }


def main() -> int:
    static = static_audit()
    runtime = runtime_concurrent_write_smoke()
    failures = [*static["failures"], *runtime["failures"]]
    result = {
        "ok": not failures,
        "operation": "sqlite_long_transaction_audit_smoke",
        "static": static,
        "runtime": runtime,
        "failures": failures,
        "token_omitted": True,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
