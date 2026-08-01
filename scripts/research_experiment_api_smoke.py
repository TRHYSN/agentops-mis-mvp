#!/usr/bin/env python3
"""Exercise bounded multi-Trial Research Lab ingest and readback on an isolated DB."""
from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "server.py"
sys.path.insert(0, str(ROOT))

from agentops_mis_core.research_experiments import stable_hash  # noqa: E402


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def start_server(db_path: Path, port: int) -> subprocess.Popen[str]:
    env = os.environ.copy()
    env["AGENTOPS_DB_PATH"] = str(db_path)
    env["AGENTOPS_SKIP_SEED_EXPORTS"] = "1"
    env["AGENTOPS_DEPLOYMENT_MODE"] = "local"
    env["AGENTOPS_HUMAN_AUTH_REQUIRED"] = "false"
    env.pop("AGENTOPS_API_KEY", None)
    env.pop("AGENTOPS_ADMIN_KEY", None)
    return subprocess.Popen(
        [sys.executable, str(SERVER), "--host", "127.0.0.1", "--port", str(port)],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def wait_ready(base_url: str, process: subprocess.Popen[str]) -> bool:
    deadline = time.time() + 30
    while time.time() < deadline:
        if process.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(base_url + "/health", timeout=1) as response:
                return response.status == 200
        except (urllib.error.URLError, TimeoutError):
            time.sleep(0.2)
    return False


def request_json(base_url: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    data = None
    method = "GET"
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        method = "POST"
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(base_url + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return int(response.status), json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return int(exc.code), json.loads(exc.read().decode("utf-8"))


def evidence_bundle() -> dict:
    created_at = "2026-07-31T02:00:00+00:00"
    finished_at = "2026-07-31T02:00:03+00:00"
    protocol = {
        "name": "tiny-mlp-api-smoke",
        "stage": "confirmatory",
        "command_template": ["python3", "tiny_mlp_train.py", "{seed}"],
        "matrix": {"seed": [17, 29]},
        "protocol": {
            "primary_metric": "validation_accuracy",
            "initialization_mode": "from_scratch",
            "training_scope": "full_model",
        },
        "integrity": {"minimum_primary_metric": 0.9},
        "environment_keys": [],
    }
    trials = [
        {
            "trial_id": f"trl_seed_{seed}",
            "params": {"seed": seed},
            "status": "completed",
            "created_at": created_at,
            "updated_at": finished_at,
        }
        for seed in (17, 29)
    ]
    attempts = [
        {
            "attempt_id": f"att_seed_{seed}",
            "trial_id": f"trl_seed_{seed}",
            "attempt_number": 1,
            "status": "completed",
            "exit_code": 0,
            "error_summary": None,
            "stdout_sha256": str(seed)[:1] * 64,
            "stderr_sha256": "e" * 64,
            "started_at": created_at,
            "finished_at": finished_at,
            "raw_output_omitted": True,
        }
        for seed in (17, 29)
    ]
    metrics = []
    for seed in (17, 29):
        for step, value in ((1, 0.75), (250, 1.0)):
            metrics.append({
                "metric_id": f"rmet_{seed}_{step}",
                "trial_id": f"trl_seed_{seed}",
                "attempt_id": f"att_seed_{seed}",
                "name": "validation_accuracy",
                "value": value,
                "step": step,
                "recorded_at": finished_at,
            })
    artifacts = [
        {
            "artifact_id": f"rart_seed_{seed}",
            "trial_id": f"trl_seed_{seed}",
            "attempt_id": f"att_seed_{seed}",
            "relative_path": f"seed-{seed}/model.json",
            "content_hash": "c" * 64 if seed == 17 else "d" * 64,
            "size_bytes": 2048,
            "artifact_body_omitted": True,
        }
        for seed in (17, 29)
    ]
    bundle = {
        "schema_version": "research_lab_mis_evidence_v1",
        "workspace_id": "local-demo",
        "experiment": {
            "experiment_id": "exp_tiny_mlp_api_smoke",
            "name": "Tiny MLP API smoke",
            "stage": "confirmatory",
            "status": "completed",
            "protocol_hash": "a" * 64,
            "provenance_hash": "b" * 64,
            "protocol": protocol,
            "claim_eligible": True,
            "claim_reasons": [],
            "created_at": created_at,
            "updated_at": finished_at,
        },
        "trials": trials,
        "attempts": attempts,
        "metrics": metrics,
        "artifacts": artifacts,
        "deviations": [],
        "omissions": {
            "credentials": True,
            "state_directory": True,
            "raw_stdout": True,
            "raw_stderr": True,
            "artifact_bodies": True,
            "raw_prompts": True,
            "raw_responses": True,
        },
    }
    bundle["evidence_hash"] = stable_hash(bundle)
    return bundle


def table_counts(db_path: Path) -> dict[str, int]:
    tables = [
        "research_experiments", "research_trials", "research_attempts",
        "research_metrics", "research_deviations", "tasks", "runs",
        "evaluations", "artifacts",
    ]
    with sqlite3.connect(db_path) as connection:
        counts = {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in tables
        }
        counts["research_audits"] = int(connection.execute(
            "SELECT COUNT(*) FROM audit_logs WHERE action='research.evidence_ingest'"
        ).fetchone()[0])
        return counts


def main() -> int:
    failures: list[str] = []
    process: subprocess.Popen[str] | None = None
    with tempfile.TemporaryDirectory(prefix="agentops-research-api-") as temporary:
        db_path = Path(temporary) / "agentops_mis.db"
        port = free_port()
        base_url = f"http://127.0.0.1:{port}"
        try:
            process = start_server(db_path, port)
            require(wait_ready(base_url, process), "isolated server did not become ready", failures)
            if failures:
                raise AssertionError(failures[-1])

            bundle = evidence_bundle()
            first_status, first = request_json(
                base_url,
                "/api/research/experiments/ingest",
                bundle,
            )
            first_counts = table_counts(db_path)
            second_status, second = request_json(
                base_url,
                "/api/research/experiments/ingest",
                bundle,
            )
            second_counts = table_counts(db_path)
            list_status, listing = request_json(base_url, "/api/research/experiments")
            detail_status, detail = request_json(
                base_url,
                "/api/research/experiments/exp_tiny_mlp_api_smoke",
            )

            require(first_status == 201 and first.get("ok") is True, f"first ingest failed: {first}", failures)
            require(second_status == 200, f"replay status mismatch: {second_status}", failures)
            require(second.get("idempotent_replay") is True, f"replay not idempotent: {second}", failures)
            require(first_counts == second_counts, f"replay inflated rows: {first_counts} -> {second_counts}", failures)
            require(first_counts["research_experiments"] == 1, f"experiment count mismatch: {first_counts}", failures)
            require(first_counts["research_trials"] == 2, f"trial count mismatch: {first_counts}", failures)
            require(first_counts["research_attempts"] == 2, f"attempt count mismatch: {first_counts}", failures)
            require(first_counts["research_metrics"] == 4, f"metric count mismatch: {first_counts}", failures)
            require(first_counts["runs"] >= 2, f"run mapping missing: {first_counts}", failures)
            require(first_counts["evaluations"] >= 1, f"evaluation mapping missing: {first_counts}", failures)
            require(first_counts["artifacts"] >= 2, f"artifact mapping missing: {first_counts}", failures)
            require(first_counts["research_audits"] == 1, f"audit mapping mismatch: {first_counts}", failures)
            require(list_status == 200 and listing.get("count") == 1, f"list failed: {listing}", failures)
            listed_experiment = ((listing.get("experiments") or [{}])[0])
            require(listed_experiment.get("completed_trial_count") == 2, f"list completed Trial count missing: {listing}", failures)
            require(listed_experiment.get("final_metric_value") == 1.0, f"list final metric missing: {listing}", failures)
            require(detail_status == 200, f"detail failed: {detail}", failures)
            require((detail.get("evidence_counts") or {}).get("trials") == 2, f"detail trials missing: {detail}", failures)
            require((detail.get("evidence_counts") or {}).get("attempts") == 2, f"detail attempts missing: {detail}", failures)
            require((detail.get("evidence_counts") or {}).get("metrics") == 4, f"detail metrics missing: {detail}", failures)
            require(len(detail.get("latest_metrics") or []) == 2, f"latest metric projection missing: {detail}", failures)
            require(
                all(row.get("final_metric_value") == 1.0 for row in detail.get("trials") or []),
                f"detail Trial final metrics missing: {detail}",
                failures,
            )
            require(
                all(row.get("run_id") for row in detail.get("trials") or []),
                f"detail Trial Run links missing: {detail}",
                failures,
            )
            require(detail.get("server_executes_shell") is False, f"shell boundary missing: {detail}", failures)

            invalid = evidence_bundle()
            invalid["raw_stdout"] = "must never enter MIS"
            invalid["evidence_hash"] = stable_hash({
                key: value for key, value in invalid.items() if key != "evidence_hash"
            })
            invalid_status, invalid_payload = request_json(
                base_url,
                "/api/research/experiments/ingest",
                invalid,
            )
            require(invalid_status == 400, f"unsafe field was accepted: {invalid_payload}", failures)
            require(table_counts(db_path) == second_counts, "invalid bundle mutated the ledger", failures)

            unsafe_protocol = evidence_bundle()
            unsafe_protocol["experiment"]["protocol"]["protocol"]["raw_prompt"] = "must never enter MIS"
            unsafe_protocol["evidence_hash"] = stable_hash({
                key: value for key, value in unsafe_protocol.items() if key != "evidence_hash"
            })
            unsafe_protocol_status, unsafe_protocol_payload = request_json(
                base_url,
                "/api/research/experiments/ingest",
                unsafe_protocol,
            )
            require(
                unsafe_protocol_status == 400,
                f"unsafe protocol payload was accepted: {unsafe_protocol_payload}",
                failures,
            )
            require(table_counts(db_path) == second_counts, "unsafe protocol mutated the ledger", failures)

            with sqlite3.connect(db_path) as connection:
                database_text = "\n".join(
                    str(value)
                    for row in connection.execute(
                        """SELECT input_summary,output_summary,error_message FROM runs
                        WHERE runtime_type='research_lab_local'"""
                    ).fetchall()
                    for value in row
                    if value is not None
                )
            for forbidden in ("must never enter MIS", "state_dir", "stdout.log", "stderr.log"):
                require(forbidden not in database_text, f"forbidden content persisted: {forbidden}", failures)

            print(json.dumps({
                "operation": "research_experiment_api_smoke",
                "ok": not failures,
                "failures": failures,
                "experiment_id": first.get("experiment_id"),
                "task_id": first.get("task_id"),
                "run_ids": first.get("run_ids"),
                "evaluation_id": first.get("evaluation_id"),
                "counts": second_counts,
                "idempotent_replay": second.get("idempotent_replay"),
                "invalid_bundle_rejected": invalid_status == 400,
                "unsafe_protocol_rejected": unsafe_protocol_status == 400,
                "server_executes_shell": False,
                "isolated_db": True,
                "token_omitted": True,
            }, ensure_ascii=False, indent=2, sort_keys=True))
            return 0 if not failures else 1
        finally:
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=8)
            if process and failures:
                stdout, stderr = process.communicate()
                if stdout:
                    print(stdout, file=sys.stderr)
                if stderr:
                    print(stderr, file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
