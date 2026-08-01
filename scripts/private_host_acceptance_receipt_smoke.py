#!/usr/bin/env python3
"""Verify Owner-only Private Host acceptance receipt generation and download."""
from __future__ import annotations

import http.cookiejar
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
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agentops_mis_core.agent_plans import compute_agent_plan_hash
from agentops_mis_core.private_host_acceptance import verify_acceptance_receipt


WORKSPACE_ID = "local-demo"
STAMP = "2026-07-12T12:00:00+00:00"


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def browser_client():
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))


def request_json(opener, url: str, *, method="GET", body=None, headers=None) -> tuple[int, dict, dict]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        with opener.open(request, timeout=10) as response:
            raw = response.read().decode("utf-8")
            return response.status, dict(response.headers), json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
        except ValueError:
            payload = {"error": "non_json_error"}
        return exc.code, dict(exc.headers), payload


def request_download(opener, url: str) -> tuple[int, dict, bytes]:
    try:
        with opener.open(urllib.request.Request(url, method="GET"), timeout=10) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def insert_agent(conn: sqlite3.Connection, agent_id: str) -> None:
    conn.execute(
        """INSERT INTO agents(agent_id,name,role,description,runtime_type,model_provider,model_name,status,
               permission_level,allowed_tools,budget_limit_usd,owner_user_id,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (agent_id, "Receipt fixture worker", "Worker", "Offline receipt fixture", "mock", "offline", "fixture", "idle", "standard", "[]", 0, None, STAMP, STAMP),
    )


def insert_task(conn: sqlite3.Connection, task_id: str, agent_id: str) -> None:
    conn.execute(
        """INSERT INTO tasks(task_id,workspace_id,title,description,requester_id,owner_agent_id,
               collaborator_agent_ids,status,priority,due_date,acceptance_criteria,risk_level,
               budget_limit_usd,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (task_id, WORKSPACE_ID, "Private Host receipt fixture", "Bounded offline fixture", None, agent_id, "[]", "completed", "high", None, "Receipt gates must pass", "low", 0, STAMP, STAMP),
    )


def insert_run(conn: sqlite3.Connection, run_id: str, task_id: str, agent_id: str, plan_id: str | None, *, completed: bool) -> None:
    conn.execute(
        """INSERT INTO runs(run_id,workspace_id,task_id,agent_id,runtime_type,status,started_at,ended_at,
               duration_ms,input_summary,output_summary,model_provider,model_name,input_tokens,output_tokens,
               reasoning_tokens,cost_usd,error_type,error_message,trace_id,parent_run_id,delegation_id,
               approval_required,agent_plan_id,plan_hash,created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            run_id, WORKSPACE_ID, task_id, agent_id, "mock", "completed" if completed else "running", STAMP,
            STAMP if completed else None, 1000 if completed else None, "RAW_PROMPT_RECEIPT_SMOKE_MUST_NOT_LEAK",
            "RAW_RESPONSE_RECEIPT_SMOKE_MUST_NOT_LEAK", "offline", "fixture", 0, 0, 0, 0, None, None,
            "trace_fixture", None, "receipt-smoke", 0, plan_id, None, STAMP,
        ),
    )


def seed_incomplete(conn: sqlite3.Connection) -> str:
    agent_id = "agt_receipt_incomplete"
    task_id = "tsk_receipt_incomplete"
    run_id = "run_receipt_incomplete"
    insert_agent(conn, agent_id)
    insert_task(conn, task_id, agent_id)
    insert_run(conn, run_id, task_id, agent_id, None, completed=False)
    return run_id


def seed_complete(conn: sqlite3.Connection) -> dict:
    agent_id = "agt_receipt_complete"
    task_id = "tsk_receipt_complete"
    run_id = "run_receipt_complete"
    plan_id = "plan_receipt_complete"
    tool_id = "tc_receipt_complete"
    evaluation_id = "eval_receipt_complete"
    approval_id = "ap_receipt_complete"
    artifact_id = "art_receipt_complete"
    audit_id = "aud_receipt_complete"
    manifest_id = "pem_receipt_complete"
    insert_agent(conn, agent_id)
    insert_task(conn, task_id, agent_id)

    execution_steps = ["READ", "PLAN", "RETRIEVE", "COMPARE", "EXECUTE", "VERIFY", "RECORD"]
    plan = {
        "plan_id": plan_id,
        "workspace_id": WORKSPACE_ID,
        "task_id": task_id,
        "run_id": run_id,
        "agent_id": agent_id,
        "task_understanding": "Generate a bounded Private Host acceptance receipt from verified MIS evidence.",
        "referenced_specs_json": json.dumps(["PROJECT_SPEC.md"]),
        "referenced_memories_json": json.dumps(["knowledge/shared/common_failures.md"]),
        "referenced_bases_json": json.dumps(["base_local_tasks"]),
        "proposed_files_to_change_json": "[]",
        "risk_level": "low",
        "approval_required": 0,
        "execution_steps_json": json.dumps(execution_steps),
        "verification_plan": "Verify completion, passing evaluation, approval, artifact, and manifest evidence.",
        "rollback_plan": "Reject receipt generation and preserve the existing authority ledger unchanged.",
        "status": "approved",
        "plan_version": 1,
        "plan_hash": None,
        "verified_at": STAMP,
        "verification_result_hash": "fixture_plan_verification_hash",
        "approval_id": None,
        "approved_by_user_id": "fixture-owner",
        "approved_at": STAMP,
        "created_at": STAMP,
        "updated_at": STAMP,
    }
    plan["plan_hash"] = compute_agent_plan_hash(plan)
    conn.execute(
        """INSERT INTO agent_plans(plan_id,workspace_id,task_id,run_id,agent_id,task_understanding,
               referenced_specs_json,referenced_memories_json,referenced_bases_json,proposed_files_to_change_json,
               risk_level,approval_required,execution_steps_json,verification_plan,rollback_plan,status,plan_version,
               plan_hash,verified_at,verification_result_hash,approval_id,approved_by_user_id,approved_at,created_at,updated_at)
           VALUES(:plan_id,:workspace_id,:task_id,:run_id,:agent_id,:task_understanding,
               :referenced_specs_json,:referenced_memories_json,:referenced_bases_json,:proposed_files_to_change_json,
               :risk_level,:approval_required,:execution_steps_json,:verification_plan,:rollback_plan,:status,:plan_version,
               :plan_hash,:verified_at,:verification_result_hash,:approval_id,:approved_by_user_id,:approved_at,:created_at,:updated_at)""",
        plan,
    )
    insert_run(conn, run_id, task_id, agent_id, plan_id, completed=True)
    conn.execute("UPDATE runs SET plan_hash=? WHERE run_id=?", (plan["plan_hash"], run_id))
    conn.execute(
        """INSERT INTO tool_calls(tool_call_id,run_id,agent_id,tool_name,tool_version,tool_category,
               normalized_args_json,target_resource,risk_level,status,result_summary,side_effect_id,started_at,ended_at,created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (tool_id, run_id, agent_id, "receipt.fixture", "v1", "custom", '{"raw_prompt_omitted":true}', "local://receipt-fixture", "low", "completed", "Bounded fixture result", None, STAMP, STAMP, STAMP),
    )
    conn.execute(
        """INSERT INTO evaluations(evaluation_id,task_id,run_id,agent_id,evaluator_type,score,pass_fail,rubric_json,notes,created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (evaluation_id, task_id, run_id, agent_id, "rule", 1.0, "pass", "{}", "Bounded pass", STAMP),
    )
    conn.execute(
        """INSERT INTO approvals(approval_id,task_id,run_id,tool_call_id,requested_by_agent_id,approver_user_id,
               decision,reason,expires_at,created_at,decided_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (approval_id, task_id, run_id, None, agent_id, None, "approved", "Fixture delivery approved", None, STAMP, STAMP),
    )
    conn.execute(
        """INSERT INTO artifacts(artifact_id,task_id,run_id,artifact_type,title,uri,summary,created_at)
           VALUES(?,?,?,?,?,?,?,?)""",
        (artifact_id, task_id, run_id, "customer_delivery_report", "Bounded delivery", "file:///PRIVATE_PATH_MUST_NOT_LEAK", "TOKEN_RECEIPT_SMOKE_MUST_NOT_LEAK", STAMP),
    )
    conn.execute(
        """INSERT INTO audit_logs(audit_id,actor_type,actor_id,action,entity_type,entity_id,before_hash,after_hash,
               metadata_json,tamper_chain_hash,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (audit_id, "system", "receipt-fixture", "fixture.complete", "runs", run_id, None, "fixture", '{"raw_content_omitted":true}', "fixture-chain", STAMP),
    )
    conn.execute(
        """INSERT INTO plan_evidence_manifests(manifest_id,workspace_id,plan_id,task_id,run_id,agent_id,
               mismatch_policy,expected_steps_json,tool_call_ids_json,evaluation_ids_json,artifact_ids_json,audit_ids_json,
               plan_hash,verification_result_hash,status,verification_json,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            manifest_id, WORKSPACE_ID, plan_id, task_id, run_id, agent_id, "block", json.dumps(execution_steps),
            json.dumps([tool_id]), json.dumps([evaluation_id]), json.dumps([artifact_id]), json.dumps([audit_id]),
            plan["plan_hash"], "fixture_manifest_verification_hash", "verified", '{"pass":true}', STAMP, STAMP,
        ),
    )
    return {
        "run_id": run_id,
        "task_id": task_id,
        "approval_id": approval_id,
        "artifact_id": artifact_id,
        "manifest_id": manifest_id,
    }


def add_operator_account(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        owner = conn.execute("SELECT * FROM human_accounts WHERE role='owner' LIMIT 1").fetchone()
        conn.execute(
            """INSERT INTO human_accounts(account_id,workspace_id,username,display_name,role,password_hash,password_salt,
                   password_params_json,status,created_at,updated_at,last_login_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("husr_receipt_operator", WORKSPACE_ID, "receipt-operator", "Receipt Operator", "operator", owner[5], owner[6], owner[7], "active", STAMP, STAMP, None),
        )


def wait_ready(opener, base_url: str, process: subprocess.Popen) -> bool:
    deadline = time.time() + 30
    while time.time() < deadline:
        if process.poll() is not None:
            return False
        try:
            status, _headers, payload = request_json(opener, base_url + "/health")
            if status == 200 and payload.get("status") == "ready":
                return True
        except (OSError, ValueError, urllib.error.URLError):
            pass
        time.sleep(0.2)
    return False


def main() -> int:
    failures: list[str] = []
    evidence: dict[str, object] = {}
    secrets = {
        "machine": "fixture-receipt-machine-secret",
        "admin": "fixture-receipt-admin-secret",
        "setup": "fixture-receipt-setup-secret",
        "password": "fixture-receipt-password-value",
    }
    forbidden = [
        *secrets.values(),
        "RAW_PROMPT_RECEIPT_SMOKE_MUST_NOT_LEAK",
        "RAW_RESPONSE_RECEIPT_SMOKE_MUST_NOT_LEAK",
        "PRIVATE_PATH_MUST_NOT_LEAK",
        "TOKEN_RECEIPT_SMOKE_MUST_NOT_LEAK",
    ]

    with tempfile.TemporaryDirectory(prefix="agentops-private-host-receipt-") as temporary:
        temp = Path(temporary)
        db_path = temp / "agentops_mis.db"
        port = free_port()
        base_url = f"http://127.0.0.1:{port}"
        env = {
            **os.environ,
            "AGENTOPS_DB_PATH": str(db_path),
            "AGENTOPS_SKIP_SEED_EXPORTS": "1",
            "AGENTOPS_DEPLOYMENT_MODE": "private_host",
            "AGENTOPS_HUMAN_AUTH_REQUIRED": "true",
            "AGENTOPS_COOKIE_SECURE": "false",
            "AGENTOPS_API_KEY": secrets["machine"],
            "AGENTOPS_ADMIN_KEY": secrets["admin"],
            "AGENTOPS_OWNER_SETUP_CODE": secrets["setup"],
            "AGENTOPS_ALLOWED_ORIGINS": base_url,
            "AGENTOPS_HOST_VERSION": "1.6.0-smoke",
            "AGENTOPS_GIT_COMMIT": "a" * 40,
            "HERMES_ALLOW_REAL_RUN": "false",
        }
        process = subprocess.Popen(
            [sys.executable, "server.py", "--host", "127.0.0.1", "--port", str(port)],
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        anonymous = urllib.request.build_opener()
        owner = browser_client()
        operator = browser_client()
        second_owner = browser_client()
        captured: list[str] = []
        try:
            if not wait_ready(anonymous, base_url, process):
                failures.append("Private Host did not become ready")

            with sqlite3.connect(db_path) as conn:
                incomplete_run = seed_incomplete(conn)
                complete = seed_complete(conn)

            receipt_endpoint = base_url + "/api/host/acceptance-receipts"
            status, _headers, payload = request_json(
                anonymous,
                receipt_endpoint,
                method="POST",
                body={"run_id": complete["run_id"]},
                headers={"Authorization": f"Bearer {secrets['machine']}"},
            )
            evidence["machine_token_denied"] = {"status": status, "error": payload.get("error")}
            if status != 401:
                failures.append("machine token substituted for a human Session")

            status, _headers, owner_auth = request_json(
                owner,
                base_url + "/api/human-auth/bootstrap",
                method="POST",
                body={"setup_code": secrets["setup"], "username": "receipt-owner", "display_name": "Receipt Owner", "password": secrets["password"]},
                headers={"Origin": base_url},
            )
            owner_csrf = str(owner_auth.get("csrf_token") or "")
            if status != 201 or not owner_csrf:
                failures.append("Owner bootstrap failed")
            add_operator_account(db_path)

            status, _headers, operator_auth = request_json(
                operator,
                base_url + "/api/human-auth/login",
                method="POST",
                body={"username": "receipt-operator", "password": secrets["password"]},
                headers={"Origin": base_url},
            )
            operator_csrf = str(operator_auth.get("csrf_token") or "")
            status, _headers, payload = request_json(
                operator,
                receipt_endpoint,
                method="POST",
                body={"run_id": complete["run_id"]},
                headers={"Origin": base_url, "X-AgentOps-CSRF": operator_csrf},
            )
            evidence["non_owner_denied"] = {"status": status, "error": payload.get("error")}
            if status != 403 or payload.get("required_role") != "owner":
                failures.append("non-Owner receipt generation did not fail closed")

            status, _headers, payload = request_json(
                owner,
                receipt_endpoint,
                method="POST",
                body={"run_id": complete["run_id"]},
                headers={"Origin": base_url},
            )
            evidence["missing_csrf"] = {"status": status, "error": payload.get("error")}
            if status != 403 or payload.get("error") != "csrf_validation_failed":
                failures.append("receipt generation did not require CSRF")

            status, _headers, payload = request_json(
                owner,
                receipt_endpoint,
                method="POST",
                body={"run_id": incomplete_run},
                headers={"Origin": base_url, "X-AgentOps-CSRF": owner_csrf},
            )
            evidence["incomplete_run"] = {"status": status, "error": payload.get("error"), "failed_gates": payload.get("failed_gates")}
            if status != 409 or payload.get("error") != "acceptance_receipt_not_ready":
                failures.append("incomplete run did not fail closed")

            status, _headers, receipt = request_json(
                owner,
                receipt_endpoint,
                method="POST",
                body={"run_id": complete["run_id"]},
                headers={"Origin": base_url, "X-AgentOps-CSRF": owner_csrf},
            )
            if status != 201 or not verify_acceptance_receipt(receipt):
                failures.append("complete fixture did not create a valid receipt")
            receipt_id = str(receipt.get("receipt_id") or "")
            first_hash = str(receipt.get("payload_sha256") or "")

            operator_receipt_url = base_url + f"/api/host/acceptance-receipts/{receipt_id}"
            status, _headers, payload = request_json(operator, operator_receipt_url)
            evidence["non_owner_read_denied"] = {"status": status, "error": payload.get("error")}
            if status != 403 or payload.get("required_role") != "owner":
                failures.append("non-Owner receipt read did not fail closed")
            status, _headers, _payload = request_download(operator, operator_receipt_url + "/download")
            evidence["non_owner_download_denied"] = {"status": status}
            if status != 403:
                failures.append("non-Owner receipt download did not fail closed")

            status, _headers, artifacts_payload = request_json(operator, base_url + "/api/artifacts")
            artifact_items = artifacts_payload if isinstance(artifacts_payload, list) else None
            generic_receipt = next(
                (item for item in (artifact_items or []) if item.get("artifact_id") == receipt_id),
                None,
            )
            generic_summary = str((generic_receipt or {}).get("summary") or "")
            generic_summary_safe = (
                status == 200
                and generic_receipt is not None
                and "payload_sha256" not in generic_summary
                and "generated_by_user_id" not in generic_summary
                and first_hash not in generic_summary
                and "{" not in generic_summary
                and "}" not in generic_summary
            )
            evidence["operator_artifact_summary_safe"] = {
                "status": status,
                "receipt_metadata_visible": generic_receipt is not None,
                "payload_omitted": generic_summary_safe,
            }
            if not generic_summary_safe:
                failures.append("generic artifact summary exposed receipt authority payload")

            status, _headers, repeated = request_json(
                owner,
                receipt_endpoint,
                method="POST",
                body={"run_id": complete["run_id"]},
                headers={"Origin": base_url, "X-AgentOps-CSRF": owner_csrf},
            )
            stable = status == 200 and repeated.get("receipt_id") == receipt_id and repeated.get("payload_sha256") == first_hash
            evidence["idempotency"] = {"status": status, "stable_id": stable, "stable_hash": stable}
            if not stable:
                failures.append("repeat generation was not idempotent")

            status, _headers, second_login = request_json(
                second_owner,
                base_url + "/api/human-auth/login",
                method="POST",
                body={"username": "receipt-owner", "password": secrets["password"]},
                headers={"Origin": base_url},
            )
            second_csrf = str(second_login.get("csrf_token") or "")
            if status != 200 or not second_csrf:
                failures.append("second Owner Session login failed")
            status, _headers, readback = request_json(second_owner, base_url + f"/api/host/acceptance-receipts/{receipt_id}")
            if status != 200 or readback.get("payload_sha256") != first_hash:
                failures.append("second Owner Session could not read receipt")

            status, headers, downloaded = request_download(second_owner, base_url + f"/api/host/acceptance-receipts/{receipt_id}/download")
            downloaded_receipt = json.loads(downloaded.decode("utf-8")) if status == 200 else {}
            expected_filename = f'attachment; filename="host-acceptance-receipt-{receipt_id}.json"'
            if status != 200 or headers.get("Content-Disposition") != expected_filename or downloaded_receipt.get("payload_sha256") != first_hash:
                failures.append("second Owner Session receipt download failed")

            status, _headers, logged_out = request_json(
                second_owner,
                base_url + "/api/human-auth/logout",
                method="POST",
                body={},
                headers={"Origin": base_url, "X-AgentOps-CSRF": second_csrf},
            )
            status_after_logout, _headers, denied = request_json(second_owner, base_url + f"/api/host/acceptance-receipts/{receipt_id}")
            evidence["logout_denied"] = {"logout_status": status, "read_status": status_after_logout, "error": denied.get("error")}
            if logged_out.get("authenticated") is not False or status_after_logout != 401:
                failures.append("logged-out Session retained receipt access")

            with sqlite3.connect(db_path) as conn:
                conn.row_factory = sqlite3.Row
                audit_rows = conn.execute(
                    "SELECT action,metadata_json FROM audit_logs WHERE entity_id=? AND action LIKE 'host.acceptance_receipt.%' ORDER BY created_at",
                    (receipt_id,),
                ).fetchall()
                stored = conn.execute("SELECT artifact_type,summary,uri FROM artifacts WHERE artifact_id=?", (receipt_id,)).fetchone()
                authority = conn.execute(
                    """SELECT workspace_id,task_id,run_id,payload_json,payload_sha256,generated_by
                       FROM private_host_acceptance_receipts WHERE receipt_id=?""",
                    (receipt_id,),
                ).fetchone()
            actions = [row["action"] for row in audit_rows]
            if "host.acceptance_receipt.generate" not in actions or "host.acceptance_receipt.download" not in actions:
                failures.append("receipt generation/download audit evidence missing")
            if not stored or stored["artifact_type"] != "private_host_acceptance_receipt" or not str(stored["uri"]).startswith("mis://"):
                failures.append("receipt artifact metadata was not persisted inside MIS")
            if (
                not authority
                or authority["workspace_id"] != WORKSPACE_ID
                or authority["task_id"] != complete["task_id"]
                or authority["run_id"] != complete["run_id"]
                or authority["payload_sha256"] != first_hash
                or authority["generated_by"] != (owner_auth.get("user") or {}).get("account_id")
                or json.loads(authority["payload_json"] or "{}") != receipt
            ):
                failures.append("dedicated receipt authority row did not preserve the validated payload")

            scanned = "\n".join([
                json.dumps(receipt, sort_keys=True),
                json.dumps(readback, sort_keys=True),
                downloaded.decode("utf-8", errors="replace"),
                stored["summary"] if stored else "",
                *[row["metadata_json"] for row in audit_rows],
            ])
            if any(value in scanned for value in forbidden):
                failures.append("receipt or audit exposed secret/raw/path fixture material")
            if "?" in str((stored or {"uri": ""})["uri"]):
                failures.append("receipt artifact metadata stored a URL query")

            evidence["receipt"] = {
                "receipt_id_present": bool(receipt_id),
                "stable_payload_sha256": len(first_hash) == 64,
                "artifact_metadata_sha256_present": len(str(receipt.get("artifact_metadata_sha256") or "")) == 64,
                "artifact_file_content_omitted": (receipt.get("omission_flags") or {}).get("artifact_file_content_omitted") is True,
                "evaluation_pass": (receipt.get("evaluation") or {}).get("pass_fail") == "pass",
                "approval_id": receipt.get("approval_id"),
                "artifact_id": receipt.get("artifact_id"),
                "plan_manifest_id": receipt.get("plan_manifest_id"),
                "download_status": 200 if downloaded_receipt else status,
                "audit_actions": actions,
            }
        except (OSError, RuntimeError, ValueError, sqlite3.DatabaseError, urllib.error.URLError) as exc:
            failures.append(f"receipt smoke exception: {type(exc).__name__}: {str(exc)[:180]}")
        finally:
            process.terminate()
            try:
                stdout, stderr = process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate(timeout=5)
            captured.extend([stdout or "", stderr or ""])

        if any(value in "\n".join(captured) for value in forbidden):
            failures.append("Private Host process output exposed fixture secret/raw material")

    print(json.dumps({
        "ok": not failures,
        "operation": "private_host_acceptance_receipt_smoke",
        "temporary_database": True,
        "human_session_required": True,
        "owner_role_required": True,
        "machine_token_substitution_blocked": True,
        "real_runtime_called": False,
        "external_file_written": False,
        "evidence": evidence,
        "failures": failures,
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
