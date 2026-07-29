#!/usr/bin/env python3
"""Verify the real-runtime acceptance client can use Private Host human auth."""
from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import hashlib
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import customer_worker_real_runtime_acceptance as acceptance
import v1_5_live_product_readiness_smoke as readiness


ROOT = Path(__file__).resolve().parents[1]


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def ledger_counts(db_path: Path) -> dict[str, int]:
    tables = ("tasks", "runs", "tool_calls", "evaluations", "runtime_events", "audit_logs")
    with sqlite3.connect(db_path) as conn:
        return {
            table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in tables
        }


def offline_async_disconnect_state_machine() -> dict:
    original_authenticate = acceptance.authenticate_human_session
    original_http_json = acceptance.http_json
    auth_count = 0
    submit_count = 0
    job_id = "wfjob_offline_disconnect"
    request_hash = "offline_disconnect_request_hash"
    task_id = "tsk_offline_disconnect"
    run_id = "run_offline_disconnect"
    evidence = {
        "tool_calls": 1,
        "evaluations": 1,
        "runtime_events": 2,
        "audit_logs": 2,
        "artifacts": 1,
        "memories": 1,
        "approvals": 1,
        "plan_evidence_manifests": 1,
    }
    completed_job = {
        "job_id": job_id,
        "status": "completed",
        "adapter": "hermes",
        "confirm_run": True,
        "request_hash": request_hash,
        "result_task_id": task_id,
        "result_run_id": run_id,
        "result_artifact_id": "art_offline_disconnect",
        "result": {
            "ok": True,
            "dry_run": False,
            "adapter": "hermes",
            "task_id": task_id,
            "run_id": run_id,
            "artifact_id": "art_offline_disconnect",
            "approval_id": "ap_offline_disconnect",
            "plan_id": "plan_offline_disconnect",
            "plan_evidence_manifest_id": "pem_offline_disconnect",
            "plan_evidence_pass": True,
            "evidence": evidence,
        },
    }

    def fake_authenticate(_args, *, include_session_cookie=False):
        nonlocal auth_count
        auth_count += 1
        values = (object(), f"csrf-{auth_count}", "http://127.0.0.1:8787")
        return (*values, f"session-{auth_count}") if include_session_cookie else values

    def fake_http_json(method, _base_url, path, _payload, _timeout, *, opener=None, headers=None):
        nonlocal submit_count
        if method == "POST" and path.endswith("/submit"):
            submit_count += 1
            return 202, {
                "job_id": job_id,
                "idempotent_replay": submit_count > 1,
                "job": {
                    "job_id": job_id,
                    "status": "queued",
                    "adapter": "hermes",
                    "confirm_run": True,
                    "request_hash": request_hash,
                },
            }
        if method == "GET" and path == f"/api/workflows/jobs/{job_id}" and opener is None:
            return 401, {"error": "human_auth_required"}
        if method == "GET" and path == f"/api/workflows/jobs/{job_id}":
            completed_job["completed_at"] = (
                dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=1)
            ).isoformat()
            return 200, {"job": completed_job}
        if method == "GET" and path.startswith("/api/workflows/jobs?"):
            return 200, {"jobs": [completed_job]}
        raise AssertionError(f"unexpected offline async request: {method} {path}")

    args = argparse.Namespace(
        base_url="http://127.0.0.1:8787",
        request_timeout=5,
        origin="http://127.0.0.1:8787",
        username="offline-owner",
        password_env="AGENTOPS_ACCEPTANCE_PASSWORD",
        setup_code_env="AGENTOPS_OWNER_SETUP_CODE",
        hermes_timeout=5,
        hermes_max_tokens=64,
        disconnect_delay_sec=0.0,
    )
    try:
        acceptance.authenticate_human_session = fake_authenticate
        acceptance.http_json = fake_http_json
        result = acceptance.run_adapter_async_disconnect(args, "hermes")
    finally:
        acceptance.authenticate_human_session = original_authenticate
        acceptance.http_json = original_http_json
    return result


def main() -> int:
    failures: list[str] = []
    evidence: dict[str, object] = {}
    fixture_values = {
        "machine": "fixture-private-host-machine-key",
        "admin": "fixture-private-host-admin-key",
        "setup": "fixture-private-host-setup-code",
        "password": "fixture-private-host-password",
    }
    with tempfile.TemporaryDirectory(prefix="agentops-private-client-") as temporary:
        temp = Path(temporary)
        port = free_port()
        base_url = f"http://127.0.0.1:{port}"
        env = {
            **os.environ,
            "AGENTOPS_DB_PATH": str(temp / "agentops_mis.db"),
            "AGENTOPS_SKIP_SEED_EXPORTS": "1",
            "AGENTOPS_DEPLOYMENT_MODE": "private_host",
            "AGENTOPS_HUMAN_AUTH_REQUIRED": "true",
            "AGENTOPS_COOKIE_SECURE": "false",
            "AGENTOPS_API_KEY": fixture_values["machine"],
            "AGENTOPS_ADMIN_KEY": fixture_values["admin"],
            "AGENTOPS_OWNER_SETUP_CODE": fixture_values["setup"],
            "AGENTOPS_ALLOWED_ORIGINS": base_url,
            "AGENTOPS_ACCEPTANCE_PASSWORD": fixture_values["password"],
        }
        process = subprocess.Popen(
            [sys.executable, "server.py", "--host", "127.0.0.1", "--port", str(port)],
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        old_password = os.environ.get("AGENTOPS_ACCEPTANCE_PASSWORD")
        old_setup = os.environ.get("AGENTOPS_OWNER_SETUP_CODE")
        os.environ["AGENTOPS_ACCEPTANCE_PASSWORD"] = fixture_values["password"]
        os.environ["AGENTOPS_OWNER_SETUP_CODE"] = fixture_values["setup"]
        try:
            deadline = time.time() + 30
            while time.time() < deadline:
                try:
                    with urllib.request.urlopen(base_url + "/health", timeout=1) as response:
                        if response.status == 200:
                            break
                except OSError:
                    time.sleep(0.2)
            else:
                raise RuntimeError("private Host fixture did not become ready")

            args = argparse.Namespace(
                base_url=base_url,
                request_timeout=10,
                origin=base_url,
                username="acceptance-owner",
                password_env="AGENTOPS_ACCEPTANCE_PASSWORD",
                setup_code_env="AGENTOPS_OWNER_SETUP_CODE",
            )
            opener, csrf_token, origin = acceptance.authenticate_human_session(args)
            owner_status, owner_session = acceptance.http_json(
                "GET", base_url, "/api/human-auth/status", None, 10, opener=opener
            )
            read_status, tasks = acceptance.http_json(
                "GET", base_url, "/api/tasks", None, 10, opener=opener
            )
            marker_counts_before = ledger_counts(temp / "agentops_mis.db")
            write_status, task = acceptance.http_json(
                "POST",
                base_url,
                "/api/tasks",
                {
                    "title": "Private Host acceptance marker smoke",
                    "description": "Low-risk browser acceptance marker. No Runtime or external connector is invoked.",
                    "acceptance_criteria": "The marker task is readable through the same authenticated human Session.",
                    "owner_agent_id": "",
                    "collaborator_agent_ids": [],
                    "status": "planned",
                    "priority": "low",
                    "risk_level": "low",
                    "budget_limit_usd": 0,
                },
                10,
                opener=opener,
                headers={"Origin": origin, "X-AgentOps-CSRF": csrf_token},
            )
            marker_counts_after = ledger_counts(temp / "agentops_mis.db")
            marker_task = task.get("task") or {}
            evidence = {
                "owner_session_created": (
                    bool(csrf_token)
                    and owner_status == 200
                    and (owner_session.get("user") or {}).get("role") == "owner"
                ),
                "authenticated_read": read_status == 200 and isinstance(tasks, list),
                "csrf_write": write_status in {200, 201} and bool(task.get("task_id")),
                "marker_task_created": (
                    write_status == 201
                    and bool(task.get("task_id"))
                    and marker_counts_after["tasks"] == marker_counts_before["tasks"] + 1
                ),
                "marker_owner_unassigned": (
                    write_status == 201
                    and bool(marker_task)
                    and marker_task.get("owner_agent_id") is None
                ),
                "marker_low_risk_zero_budget": (
                    marker_task.get("risk_level") == "low"
                    and float(marker_task.get("budget_limit_usd", -1)) == 0
                ),
                "marker_runtime_not_called": (
                    marker_counts_after["runs"] == marker_counts_before["runs"]
                    and marker_counts_after["tool_calls"] == marker_counts_before["tool_calls"]
                    and marker_counts_after["evaluations"] == marker_counts_before["evaluations"]
                ),
                "marker_ledger_recorded": (
                    marker_counts_after["runtime_events"] == marker_counts_before["runtime_events"] + 1
                    and marker_counts_after["audit_logs"] == marker_counts_before["audit_logs"] + 2
                ),
                "authenticated_readiness": False,
                "machine_token_used_for_browser": False,
                "real_runtime_called": False,
                "async_disconnect_state_machine": False,
                "async_idempotent_replay": False,
                "async_idempotency_conflict": False,
                "async_single_job": False,
                "async_single_run": False,
                "queued_reservation_recovered": False,
                "transport_alias_replay": False,
                "cross_workspace_job_hidden": False,
                "cross_workspace_job_list_hidden": False,
                "cross_workspace_stuck_hidden": False,
                "cross_workspace_submit_denied": False,
                "cross_workspace_mark_failed_hidden": False,
                "cross_workspace_recover_hidden": False,
            }
            idempotency_body = {
                "adapter": "mock",
                "confirm_run": True,
                "title": "Private Host idempotent async smoke",
                "description": "Verify a repeated async submit does not duplicate Worker execution.",
                "acceptance_criteria": "One job, task and run must be recorded.",
                "priority": "high",
                "risk_level": "low",
                "task_id": "tsk_async_idempotency_fixture",
                "worker_agent_id": "agt_async_idempotency_fixture",
                "idempotency_key": "fixture-async-idempotency-key",
            }
            second_opener, second_csrf, second_origin = acceptance.authenticate_human_session(args)

            def submit_with(client, csrf, request_origin):
                return acceptance.http_json(
                    "POST",
                    base_url,
                    "/api/workflows/customer-worker-task/submit",
                    idempotency_body,
                    10,
                    opener=client,
                    headers={"Origin": request_origin, "X-AgentOps-CSRF": csrf},
                )

            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                futures = [
                    executor.submit(submit_with, opener, csrf_token, origin),
                    executor.submit(submit_with, second_opener, second_csrf, second_origin),
                ]
                concurrent_results = [future.result() for future in futures]
            initial_responses = [item for item in concurrent_results if not item[1].get("idempotent_replay")]
            replay_responses = [item for item in concurrent_results if item[1].get("idempotent_replay") is True]
            first_status, first_submit = initial_responses[0] if initial_responses else (0, {})
            replay_status, replay_submit = replay_responses[0] if replay_responses else (0, {})
            conflict_status, conflict_submit = acceptance.http_json(
                "POST",
                base_url,
                "/api/workflows/customer-worker-task/submit",
                {**idempotency_body, "title": "Changed payload must conflict"},
                10,
                opener=opener,
                headers={"Origin": origin, "X-AgentOps-CSRF": csrf_token},
            )
            job_id = str(first_submit.get("job_id") or "")
            request_hash = str((first_submit.get("job") or {}).get("request_hash") or "")
            terminal_job = {}
            deadline = time.time() + 30
            while job_id and time.time() < deadline:
                job_status, job_payload = acceptance.http_json(
                    "GET",
                    base_url,
                    f"/api/workflows/jobs/{job_id}",
                    None,
                    10,
                    opener=opener,
                )
                if job_status != 200:
                    break
                terminal_job = job_payload.get("job") or {}
                if terminal_job.get("status") in {"completed", "failed"}:
                    break
                time.sleep(0.2)
            list_status, listed_jobs = acceptance.http_json(
                "GET",
                base_url,
                "/api/workflows/jobs?workflow_type=customer_worker_task&limit=200",
                None,
                10,
                opener=opener,
            )
            matching_jobs = [
                item for item in (listed_jobs.get("jobs") or [])
                if request_hash and item.get("request_hash") == request_hash
            ]
            evidence["async_idempotent_replay"] = (
                first_status == 202
                and replay_status in {200, 202}
                and len(initial_responses) == 1
                and len(replay_responses) == 1
                and replay_submit.get("idempotent_replay") is True
                and replay_submit.get("job_id") == job_id
            )
            evidence["async_idempotency_conflict"] = (
                conflict_status == 409
                and conflict_submit.get("error") == "idempotency_conflict"
            )
            evidence["async_single_job"] = (
                list_status == 200
                and terminal_job.get("status") == "completed"
                and len(matching_jobs) == 1
            )
            task_id = str(terminal_job.get("result_task_id") or "")
            task_status, task_detail = acceptance.http_json(
                "GET",
                base_url,
                f"/api/tasks/{task_id}",
                None,
                10,
                opener=opener,
            )
            task_runs = task_detail.get("runs") or []
            evidence["async_single_run"] = (
                task_status == 200
                and len(task_runs) == 1
                and task_runs[0].get("run_id") == terminal_job.get("result_run_id")
            )
            alias_status, alias_replay = acceptance.http_json(
                "POST",
                base_url,
                "/api/workflows/customer-worker-task/submit",
                {**idempotency_body, "base_url": "http://transport-alias.invalid"},
                10,
                opener=opener,
                headers={"Origin": origin, "X-AgentOps-CSRF": csrf_token},
            )
            evidence["transport_alias_replay"] = (
                alias_status == 200
                and alias_replay.get("idempotent_replay") is True
                and alias_replay.get("job_id") == job_id
            )
            recovery_body = {
                "adapter": "mock",
                "confirm_run": True,
                "title": "Recover persisted queued reservation",
                "description": "A same-key retry must launch this durable queued job.",
                "acceptance_criteria": "The queued reservation completes once.",
                "priority": "high",
                "risk_level": "low",
                "task_id": "tsk_queued_reservation_fixture",
                "worker_agent_id": "agt_queued_reservation_fixture",
                "idempotency_key": "fixture-queued-reservation-recovery",
            }

            def stable_hash(value):
                raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
                return hashlib.sha256(raw.encode("utf-8")).hexdigest()

            recovery_canonical = {
                **{key: value for key, value in recovery_body.items() if key != "idempotency_key"},
                "workspace_id": "local-demo",
            }
            recovery_request_hash = stable_hash(recovery_canonical)
            recovery_job_id = "wfjob_" + stable_hash({
                "workspace_id": "local-demo",
                "workflow_type": "customer_worker_task",
                "idempotency_key": recovery_body["idempotency_key"],
            })[:24]
            fixture_stamp = "2026-07-12T00:00:00+00:00"
            with sqlite3.connect(temp / "agentops_mis.db") as fixture_conn:
                fixture_conn.execute(
                    """INSERT INTO workflow_jobs(job_id,workspace_id,workflow_type,status,template_id,adapter,confirm_run,title,input_summary,request_hash,result_json,result_task_id,result_run_id,result_artifact_id,error_message,created_at,started_at,completed_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        recovery_job_id,
                        "local-demo",
                        "customer_worker_task",
                        "queued",
                        None,
                        "mock",
                        1,
                        recovery_body["title"],
                        recovery_body["description"],
                        recovery_request_hash,
                        "{}",
                        None,
                        None,
                        None,
                        None,
                        fixture_stamp,
                        None,
                        None,
                        fixture_stamp,
                    ),
                )
                fixture_conn.commit()
            recovery_status, recovered = acceptance.http_json(
                "POST",
                base_url,
                "/api/workflows/customer-worker-task/submit",
                recovery_body,
                10,
                opener=opener,
                headers={"Origin": origin, "X-AgentOps-CSRF": csrf_token},
            )
            recovered_job = {}
            recovery_deadline = time.time() + 30
            while recovery_status == 202 and time.time() < recovery_deadline:
                recovered_status, recovered_payload = acceptance.http_json(
                    "GET",
                    base_url,
                    f"/api/workflows/jobs/{recovery_job_id}",
                    None,
                    10,
                    opener=opener,
                )
                if recovered_status != 200:
                    break
                recovered_job = recovered_payload.get("job") or {}
                if recovered_job.get("status") in {"completed", "failed"}:
                    break
                time.sleep(0.2)
            evidence["queued_reservation_recovered"] = (
                recovery_status == 202
                and recovered.get("idempotent_replay") is True
                and recovered.get("queued_launch_ensured") is True
                and recovered_job.get("status") == "completed"
                and bool(recovered_job.get("result_run_id"))
            )
            cross_workspace_job_id = "wfjob_cross_workspace_fixture"
            with sqlite3.connect(temp / "agentops_mis.db") as fixture_conn:
                fixture_conn.execute(
                    """INSERT INTO workflow_jobs(job_id,workspace_id,workflow_type,status,template_id,adapter,confirm_run,title,input_summary,request_hash,result_json,result_task_id,result_run_id,result_artifact_id,error_message,created_at,started_at,completed_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        cross_workspace_job_id,
                        "other-workspace",
                        "customer_worker_task",
                        "queued",
                        None,
                        "mock",
                        0,
                        "Cross workspace fixture",
                        "Bounded fixture",
                        "cross_workspace_hash",
                        "{}",
                        None,
                        None,
                        None,
                        None,
                        "2026-07-12T00:00:00+00:00",
                        "2026-07-12T00:00:00+00:00",
                        None,
                        "2026-07-12T00:00:00+00:00",
                    ),
                )
                fixture_conn.commit()
            cross_status, cross_payload = acceptance.http_json(
                "GET",
                base_url,
                f"/api/workflows/jobs/{cross_workspace_job_id}",
                None,
                10,
                opener=opener,
            )
            evidence["cross_workspace_job_hidden"] = (
                cross_status == 404 and cross_payload.get("error") == "not found"
            )
            cross_list_status, cross_list = acceptance.http_json(
                "GET",
                base_url,
                "/api/workflows/jobs?limit=200",
                None,
                10,
                opener=opener,
            )
            evidence["cross_workspace_job_list_hidden"] = (
                cross_list_status == 200
                and all(item.get("job_id") != cross_workspace_job_id for item in (cross_list.get("jobs") or []))
            )
            cross_stuck_status, cross_stuck = acceptance.http_json(
                "GET",
                base_url,
                "/api/workflows/jobs/stuck?threshold_sec=30&limit=200",
                None,
                10,
                opener=opener,
            )
            evidence["cross_workspace_stuck_hidden"] = (
                cross_stuck_status == 200
                and all(item.get("job_id") != cross_workspace_job_id for item in (cross_stuck.get("stuck_jobs") or []))
            )
            cross_submit_status, cross_submit = acceptance.http_json(
                "POST",
                base_url,
                "/api/workflows/customer-worker-task/submit",
                {**idempotency_body, "idempotency_key": "fixture-cross-workspace-submit", "workspace_id": "other-workspace"},
                10,
                opener=opener,
                headers={"Origin": origin, "X-AgentOps-CSRF": csrf_token},
            )
            evidence["cross_workspace_submit_denied"] = (
                cross_submit_status == 403
                and cross_submit.get("error") == "human_workspace_forbidden"
            )
            cross_mark_status, cross_mark = acceptance.http_json(
                "POST",
                base_url,
                f"/api/workflows/jobs/{cross_workspace_job_id}/mark-failed",
                {},
                10,
                opener=opener,
                headers={"Origin": origin, "X-AgentOps-CSRF": csrf_token},
            )
            evidence["cross_workspace_mark_failed_hidden"] = (
                cross_mark_status == 404 and cross_mark.get("error") == "not found"
            )
            cross_recover_status, cross_recover = acceptance.http_json(
                "POST",
                base_url,
                f"/api/workflows/jobs/{cross_workspace_job_id}/recover",
                {"mode": "mark-failed"},
                10,
                opener=opener,
                headers={"Origin": origin, "X-AgentOps-CSRF": csrf_token},
            )
            evidence["cross_workspace_recover_hidden"] = (
                cross_recover_status == 404 and cross_recover.get("error") == "not found"
            )
            readiness_args = argparse.Namespace(
                base_url=base_url,
                timeout=10,
                origin=base_url,
                username="acceptance-owner",
                password_env="AGENTOPS_ACCEPTANCE_PASSWORD",
            )
            readiness_opener = readiness.authenticated_human_opener(readiness_args)
            readiness_status, readiness_payload = readiness.http_get_json(
                base_url, "/api/local/readiness", 10, opener=readiness_opener
            )
            evidence["authenticated_readiness"] = (
                readiness_status == 200 and readiness_payload.get("operation") == "local_readiness"
            )
            disconnect_result = offline_async_disconnect_state_machine()
            evidence["async_disconnect_state_machine"] = (
                disconnect_result.get("ok") is True
                and disconnect_result.get("fresh_session_after_reconnect") is True
                and disconnect_result.get("anonymous_after_disconnect_status") == 401
                and disconnect_result.get("matching_job_count") == 1
            )
            if not all((evidence["owner_session_created"], evidence["authenticated_read"], evidence["csrf_write"], evidence["marker_task_created"], evidence["marker_owner_unassigned"], evidence["marker_low_risk_zero_budget"], evidence["marker_runtime_not_called"], evidence["marker_ledger_recorded"], evidence["authenticated_readiness"], evidence["async_disconnect_state_machine"], evidence["async_idempotent_replay"], evidence["async_idempotency_conflict"], evidence["async_single_job"], evidence["async_single_run"], evidence["queued_reservation_recovered"], evidence["transport_alias_replay"], evidence["cross_workspace_job_hidden"], evidence["cross_workspace_job_list_hidden"], evidence["cross_workspace_stuck_hidden"], evidence["cross_workspace_submit_denied"], evidence["cross_workspace_mark_failed_hidden"], evidence["cross_workspace_recover_hidden"])):
                failures.append(f"Private Host acceptance client auth failed: {evidence}")
        except (OSError, RuntimeError, ValueError) as exc:
            failures.append(f"acceptance client exception: {type(exc).__name__}: {str(exc)[:180]}")
        finally:
            if old_password is None:
                os.environ.pop("AGENTOPS_ACCEPTANCE_PASSWORD", None)
            else:
                os.environ["AGENTOPS_ACCEPTANCE_PASSWORD"] = old_password
            if old_setup is None:
                os.environ.pop("AGENTOPS_OWNER_SETUP_CODE", None)
            else:
                os.environ["AGENTOPS_OWNER_SETUP_CODE"] = old_setup
            process.terminate()
            try:
                stdout, stderr = process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate(timeout=5)
        combined = (stdout or "") + (stderr or "")
        if any(value in combined for value in fixture_values.values()):
            failures.append("Private Host acceptance client or server log exposed fixture credentials")

    print(json.dumps({
        "ok": not failures,
        "operation": "private_host_acceptance_client_smoke",
        "temporary_database": True,
        "credential_values_omitted": True,
        "evidence": evidence,
        "failures": failures,
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
