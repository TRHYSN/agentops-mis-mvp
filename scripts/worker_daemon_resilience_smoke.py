#!/usr/bin/env python3
"""Verify local worker daemon state, quiet diagnostics, and bounded error recovery."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agentops_mis_cli.worker import backoff_sleep  # noqa: E402


def stamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d%H%M%S")


def http_json(method: str, base_url: str, path: str, payload: dict | None = None, timeout: int = 30) -> tuple[int, dict]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req = Request(
        base_url.rstrip("/") + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen(req, timeout=timeout) as res:
            raw = res.read().decode("utf-8")
            return res.status, json.loads(raw) if raw else {}
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            body = json.loads(raw)
        except Exception:
            body = {"raw": raw}
        return exc.code, body
    except URLError as exc:
        raise RuntimeError(f"Cannot reach {base_url}{path}: {exc.reason}") from exc


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def poll_until(deadline: float, interval: float, fn):
    last = None
    while time.time() < deadline:
        last = fn()
        if last:
            return last
        time.sleep(interval)
    return last


def direct_backoff_sleep_smoke() -> dict:
    cases = [
        ("streak_one", 2.0, 30.0, 1, 2.0, 2.0),
        ("normal_growth", 2.0, 30.0, 4, 2.0, 16.0),
        ("huge_streak", 1.0, 30.0, 1_000_000, 2.0, 30.0),
        ("zero_base", 0.0, 30.0, 10, 2.0, 0.0),
        ("unit_factor", 2.0, 30.0, 10, 1.0, 2.0),
        ("cap_below_base", 10.0, 3.0, 2, 2.0, 3.0),
    ]
    results = {}
    for name, base, cap, streak, factor, expected in cases:
        sleep_sec = backoff_sleep(base, cap, streak, factor)
        require(math.isfinite(sleep_sec), f"{name} backoff is not finite: {sleep_sec}")
        require(sleep_sec >= 0.0, f"{name} backoff is negative: {sleep_sec}")
        require(sleep_sec <= cap, f"{name} backoff exceeds cap {cap}: {sleep_sec}")
        require(sleep_sec == expected, f"{name} backoff mismatch: expected {expected}, got {sleep_sec}")
        results[name] = sleep_sec
    require(results["huge_streak"] == 30.0, f"huge streak did not saturate cap exactly: {results['huge_streak']}")
    return {"cases": results, "huge_streak": 1_000_000, "huge_streak_saturated_cap": True}


def server_daemon_smoke(base_url: str, run_stamp: str) -> dict:
    agent_id = f"agt_worker_daemon_mock_{run_stamp}"
    task_id = f"tsk_worker_daemon_resilience_{run_stamp}"
    http_json("POST", base_url, "/api/workers/local/stop", {"adapter": "mock"})

    status, started = http_json("POST", base_url, "/api/workers/local/start", {
        "adapter": "mock",
        "agent_id": agent_id,
        "poll_interval": 1,
        "max_tasks": 0,
        "max_errors": 3,
    })
    require(status in {200, 201}, f"daemon start failed: {status} {started}")

    status, task = http_json("POST", base_url, "/api/tasks", {
        "task_id": task_id,
        "workspace_id": "local-demo",
        "title": "worker daemon resilience smoke task",
        "description": "Verify bounded daemon state telemetry while processing a normal MIS task.",
        "owner_agent_id": agent_id,
        "status": "planned",
        "priority": "high",
        "risk_level": "low",
        "acceptance_criteria": "Daemon must process task and expose processed/iteration state.",
    })
    require(status == 201, f"task create failed: {status} {task}")

    def task_done():
        task_status, detail = http_json("GET", base_url, f"/api/tasks/{task_id}")
        if task_status != 200:
            return None
        row = detail.get("task") or {}
        if row.get("status") == "completed":
            return detail
        return None

    detail = poll_until(time.time() + 20, 1.0, task_done)
    require(bool(detail), "daemon did not complete smoke task before timeout")
    runs = detail.get("runs") or []
    run_id = (runs[0] or {}).get("run_id") if runs else None
    require(bool(run_id), f"completed task has no run evidence: {detail}")

    latest_worker_status: dict = {}

    def daemon_processed():
        status, worker_status = http_json("GET", base_url, "/api/workers/status")
        latest_worker_status.update({"status": status, "payload": worker_status})
        if status != 200:
            return None
        candidate = next((item for item in worker_status.get("daemons", []) if item.get("adapter") == "mock"), {})
        if candidate.get("running") is True and int(candidate.get("processed") or 0) >= 1:
            return candidate
        return None

    daemon = poll_until(time.time() + 10, 0.25, daemon_processed)
    require(bool(daemon), f"daemon state did not converge after task completion: {latest_worker_status}")
    worker_status = latest_worker_status.get("payload") or {}
    require(daemon.get("running") is True, f"mock daemon is not running: {daemon}")
    require(int(daemon.get("processed") or 0) >= 1, f"daemon state did not record processed count: {daemon}")
    require(int(daemon.get("iterations") or 0) >= 1, f"daemon state did not record iterations: {daemon}")
    require(daemon.get("continue_on_error") is True, f"daemon did not report continue_on_error: {daemon}")
    require(daemon.get("jsonl_log") is False, f"API-started daemon should keep per-poll JSONL disabled: {daemon}")

    status, logs = http_json("GET", base_url, "/api/workers/local/logs?adapter=mock")
    require(status == 200, f"log endpoint failed: {status} {logs}")
    require((logs.get("daemon") or {}).get("jsonl_log") is False, f"daemon log read model lost quiet-mode evidence: {logs}")

    return {
        "task_id": task_id,
        "run_id": run_id,
        "processed": daemon.get("processed"),
        "iterations": daemon.get("iterations"),
        "status": daemon.get("status"),
        "worker_status": daemon.get("worker_status"),
        "log_jsonl": False,
        "state_telemetry": True,
    }


def direct_error_recovery_smoke(run_stamp: str) -> dict:
    state_dir = Path(os.environ.get("AGENTOPS_WORKER_RUNTIME_DIR") or (ROOT / ".agentops_runtime" / "workers"))
    state_dir.mkdir(parents=True, exist_ok=True)
    state_path = state_dir / f"resilience-smoke-{run_stamp}.state.json"
    if state_path.exists():
        state_path.unlink()
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "agent_worker.py"),
        "--adapter",
        "mock",
        "--agent-id",
        f"agt_worker_error_resilience_{run_stamp}",
        "--base-url",
        "http://127.0.0.1:9",
        "--poll-interval",
        "0.1",
        "--error-backoff-max",
        "0.2",
        "--backoff-factor",
        "2",
        "--max-tasks",
        "0",
        "--continue-on-error",
        "--max-errors",
        "2",
        "--state-path",
        str(state_path),
        "--jsonl-log",
    ]
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=12, check=False)
    require(proc.returncode != 0, f"bad-url worker should fail after max errors: {proc.returncode} {proc.stdout}")
    require(state_path.exists(), "bad-url worker did not write state file")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    require(int(state.get("total_errors") or 0) >= 2, f"state did not record two errors: {state}")
    require(state.get("status") in {"failed", "failed_max_errors"}, f"unexpected final error state: {state}")
    require(state.get("last_error"), f"state missing last_error: {state}")
    require(state.get("last_sleep_reason") == "error_backoff", f"state missing error backoff reason: {state}")
    require(float(state.get("last_sleep_sec") or 0) > 0, f"state missing backoff sleep seconds: {state}")
    return {
        "state_path": str(state_path),
        "returncode": proc.returncode,
        "total_errors": state.get("total_errors"),
        "consecutive_errors": state.get("consecutive_errors"),
        "status": state.get("status"),
        "last_sleep_reason": state.get("last_sleep_reason"),
        "last_sleep_sec": state.get("last_sleep_sec"),
        "token_omitted": True,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify worker daemon resilience and state telemetry.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8787")
    parser.add_argument("--leave-daemon-running", action="store_true")
    args = parser.parse_args(argv)
    run_stamp = stamp()
    result = {"ok": False, "base_url": args.base_url}
    try:
        result["direct_backoff_sleep"] = direct_backoff_sleep_smoke()
        result["server_daemon"] = server_daemon_smoke(args.base_url, run_stamp)
        result["direct_error_recovery"] = direct_error_recovery_smoke(run_stamp)
        result["ok"] = True
        return 0
    except Exception as exc:
        result["error"] = str(exc)
        return 1
    finally:
        if not args.leave_daemon_running:
            try:
                status, stopped = http_json("POST", args.base_url, "/api/workers/local/stop", {"adapter": "mock"})
                result["cleanup"] = {"status": status, "stopped": bool(stopped.get("ok")) if isinstance(stopped, dict) else False}
            except Exception as exc:
                result["cleanup"] = {"error": str(exc)}
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
