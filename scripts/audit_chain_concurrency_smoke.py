#!/usr/bin/env python3
"""Prove concurrent audit appends retain one unique linear tamper chain."""

from __future__ import annotations

import json
import multiprocessing
import os
import sqlite3
import sys
import tempfile
import traceback
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROCESS_COUNT = 2
APPENDS_PER_PROCESS = 40
REPLAY_AUDIT_ID = "aud_chain_concurrent_replay"


def append_worker(db_path: str, worker_id: int, barrier, result_queue) -> None:
    try:
        os.environ["AGENTOPS_DB_PATH"] = db_path
        os.environ["AGENTOPS_SKIP_SEED_EXPORTS"] = "1"
        sys.path.insert(0, str(ROOT))
        import server  # noqa: PLC0415

        server.DB_PATH = Path(db_path)
        conn = server.db(timeout_seconds=15)
        try:
            barrier.wait(timeout=15)
            for index in range(APPENDS_PER_PROCESS):
                audit_id = f"aud_chain_p{worker_id}_{index:03d}"
                server.audit(
                    conn,
                    "system",
                    f"audit-chain-worker-{worker_id}",
                    "audit.chain.concurrent_append",
                    "audit_chain_smoke",
                    audit_id,
                    None,
                    {"index": index},
                    {"worker_id": worker_id, "index": index, "raw_omitted": True},
                    audit_id=audit_id,
                )
            server.audit(
                conn,
                "system",
                "audit-chain-replay",
                "audit.chain.deterministic_replay",
                "audit_chain_smoke",
                REPLAY_AUDIT_ID,
                None,
                {"status": "recorded"},
                {"raw_omitted": True},
                audit_id=REPLAY_AUDIT_ID,
                ignore_duplicate=True,
            )
        finally:
            conn.close()
        result_queue.put({"worker_id": worker_id, "ok": True})
    except Exception:
        result_queue.put({
            "worker_id": worker_id,
            "ok": False,
            "error": traceback.format_exc()[-2000:],
        })


def expected_hash(server, row: sqlite3.Row, previous_hash: str) -> str:
    return server.stable_hash({
        "actor_type": row["actor_type"],
        "actor_id": row["actor_id"],
        "action": row["action"],
        "entity_type": row["entity_type"],
        "entity_id": row["entity_id"],
        "before_hash": row["before_hash"],
        "after_hash": row["after_hash"],
        "metadata_json": json.loads(row["metadata_json"]),
        "previous": previous_hash,
    })


def verify_unique_linear_chain(server, rows: list[sqlite3.Row]) -> dict:
    remaining = {row["audit_id"]: row for row in rows}
    current = "genesis"
    ordered_ids: list[str] = []
    branch_points: list[dict] = []
    while remaining:
        candidates = [
            row for row in remaining.values()
            if expected_hash(server, row, current) == row["tamper_chain_hash"]
        ]
        if len(candidates) != 1:
            branch_points.append({
                "predecessor": current,
                "candidate_ids": sorted(row["audit_id"] for row in candidates),
                "remaining": len(remaining),
            })
            break
        row = candidates[0]
        ordered_ids.append(row["audit_id"])
        current = row["tamper_chain_hash"]
        del remaining[row["audit_id"]]
    return {
        "ok": not remaining and not branch_points,
        "visited": len(ordered_ids),
        "remaining": len(remaining),
        "head_hash": current,
        "branch_points": branch_points,
    }


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="agentops-audit-chain-concurrency-") as tmp:
        db_path = Path(tmp) / "audit_chain.db"
        os.environ["AGENTOPS_DB_PATH"] = str(db_path)
        os.environ["AGENTOPS_SKIP_SEED_EXPORTS"] = "1"
        sys.path.insert(0, str(ROOT))
        import server  # noqa: PLC0415

        server.DB_PATH = db_path
        server.init_schema()

        context = multiprocessing.get_context("spawn")
        barrier = context.Barrier(PROCESS_COUNT)
        result_queue = context.Queue()
        processes = [
            context.Process(target=append_worker, args=(str(db_path), worker_id, barrier, result_queue))
            for worker_id in range(PROCESS_COUNT)
        ]
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=45)
        worker_results = [result_queue.get(timeout=5) for _ in processes]

        failures: list[str] = []
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
                failures.append(f"worker process timed out: pid={process.pid}")
            elif process.exitcode != 0:
                failures.append(f"worker process failed: pid={process.pid} exitcode={process.exitcode}")
        if not all(result.get("ok") for result in worker_results):
            failures.append(f"worker append failed: {worker_results}")

        conn = server.db()
        try:
            rows = conn.execute(
                """SELECT audit_id,actor_type,actor_id,action,entity_type,entity_id,
                          before_hash,after_hash,metadata_json,tamper_chain_hash,created_at
                   FROM audit_logs"""
            ).fetchall()
            replay_count = conn.execute(
                "SELECT COUNT(*) FROM audit_logs WHERE audit_id=?",
                (REPLAY_AUDIT_ID,),
            ).fetchone()[0]
            chain_head = conn.execute(
                "SELECT head_hash FROM audit_chain_state WHERE singleton_id=1"
            ).fetchone()
        finally:
            conn.close()

        expected_count = PROCESS_COUNT * APPENDS_PER_PROCESS + 1
        chain = verify_unique_linear_chain(server, rows)
        if len(rows) != expected_count:
            failures.append(f"audit count mismatch: {len(rows)}/{expected_count}")
        if replay_count != 1:
            failures.append(f"deterministic replay count mismatch: {replay_count}/1")
        if not chain["ok"]:
            failures.append(f"audit chain is not uniquely linear: {chain}")
        if chain_head is None or chain_head["head_hash"] != chain["head_hash"]:
            failures.append("chain-head row does not match the verified terminal hash")

        result = {
            "ok": not failures,
            "operation": "audit_chain_concurrency_smoke",
            "processes": PROCESS_COUNT,
            "appends_per_process": APPENDS_PER_PROCESS,
            "audit_rows": len(rows),
            "deterministic_replay_count": int(replay_count),
            "chain": chain,
            "worker_results": sorted(worker_results, key=lambda item: item["worker_id"]),
            "failures": failures,
            "credentials_omitted": True,
            "raw_prompt_response_omitted": True,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
