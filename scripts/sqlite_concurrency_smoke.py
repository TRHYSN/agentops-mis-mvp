#!/usr/bin/env python3
"""Verify the named SQLite concurrency gate for v1.5 hardening."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run_json(script: str) -> dict:
    proc = subprocess.run(
        [sys.executable, f"scripts/{script}"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        return {
            "ok": False,
            "operation": script,
            "returncode": proc.returncode,
            "parse_error": str(exc),
            "stdout_preview": (proc.stdout or "")[:1000],
            "stderr_preview": (proc.stderr or "")[:1000],
        }
    payload["returncode"] = proc.returncode
    payload["stderr_preview"] = (proc.stderr or "")[:1000]
    return payload


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def main() -> int:
    failures: list[str] = []
    reliability = run_json("sqlite_reliability_smoke.py")
    long_txn = run_json("sqlite_long_transaction_audit_smoke.py")

    require(reliability.get("ok") is True and reliability.get("returncode") == 0, f"sqlite reliability failed: {reliability}", failures)
    require(long_txn.get("ok") is True and long_txn.get("returncode") == 0, f"sqlite long transaction audit failed: {long_txn}", failures)

    pragmas = reliability.get("pragmas") or {}
    concurrency = reliability.get("concurrency") or {}
    runtime = long_txn.get("runtime") or {}
    static = long_txn.get("static") or {}

    require(pragmas.get("foreign_keys") == 1, f"foreign_keys not enabled: {pragmas}", failures)
    require(str(pragmas.get("journal_mode") or "").lower() == "wal", f"WAL not enabled: {pragmas}", failures)
    require(int(pragmas.get("busy_timeout") or 0) >= 5000, f"busy_timeout too low: {pragmas}", failures)
    require(pragmas.get("synchronous") == 1, f"synchronous not NORMAL: {pragmas}", failures)
    require(concurrency.get("reads") == 100, f"concurrent read count mismatch: {concurrency}", failures)
    require(concurrency.get("writes") == 20, f"concurrent write count mismatch: {concurrency}", failures)
    require(concurrency.get("committed_writes") == 20, f"committed write count mismatch: {concurrency}", failures)
    require(concurrency.get("read_errors") == 0 and concurrency.get("write_errors") == 0, f"concurrency errors found: {concurrency}", failures)
    require((runtime.get("concurrent_write_during_subprocess") or {}).get("ok") is True, f"concurrent write during subprocess failed: {runtime}", failures)
    require(runtime.get("written_runtime_events") == 1, f"runtime concurrent event missing: {runtime}", failures)
    require(static.get("transaction_statements") == [], f"explicit transaction statements found: {static}", failures)
    allowed_transactions = static.get("allowed_transaction_statements") or []
    require(
        len(allowed_transactions) == 1
        and allowed_transactions[0].get("function") == "sqlite_atomic_write"
        and allowed_transactions[0].get("sql_prefix") == "BEGIN IMMEDIATE",
        f"atomic write transaction contract changed: {static}",
        failures,
    )

    result = {
        "ok": not failures,
        "operation": "sqlite_concurrency_smoke",
        "contracts": [
            "server.db() applies the local SQLite pragma baseline.",
            "100 concurrent reads and 20 short writes complete without locked/busy failures.",
            "A mocked long subprocess workflow does not hold a write transaction that blocks a concurrent runtime event.",
        ],
        "pragmas": pragmas,
        "concurrency": concurrency,
        "long_transaction_runtime": runtime,
        "long_transaction_static": {
            "slow_call_count": static.get("slow_call_count"),
            "transaction_statements": static.get("transaction_statements"),
            "allowed_transaction_statements": static.get("allowed_transaction_statements"),
        },
        "failures": failures,
        "token_omitted": True,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
