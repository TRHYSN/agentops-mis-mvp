#!/usr/bin/env python3
"""Verify private-host human sessions remain separate from Agent Gateway auth."""
from __future__ import annotations

import http.cookiejar
import datetime as dt
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


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def request_json(opener, url: str, *, method="GET", body=None, headers=None) -> tuple[int, dict, dict]:
    payload = None if body is None else json.dumps(body).encode("utf-8")
    request_headers = {"Content-Type": "application/json", **(headers or {})}
    request = urllib.request.Request(url, data=payload, method=method, headers=request_headers)
    try:
        with opener.open(request, timeout=3) as response:
            raw = response.read()
            return response.status, dict(response.headers), json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return exc.code, dict(exc.headers), json.loads(raw.decode("utf-8"))


def insert_agent_plan_review_fixture(
    conn: sqlite3.Connection,
    *,
    plan_id: str,
    approval_id: str,
    task_id: str,
    run_id: str,
    agent_id: str,
    plan_hash: str,
    created_at: str,
) -> None:
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
            plan_id,
            "local-demo",
            task_id,
            run_id,
            agent_id,
            "Review a bounded Private Host browser-session decision fixture.",
            "[]",
            "[]",
            "[]",
            "[]",
            "high",
            1,
            '["review","decide","record"]',
            "Verify the bounded ledger decision only.",
            "Keep the Plan and Approval pending if the decision cannot commit.",
            "submitted",
            1,
            plan_hash,
            None,
            None,
            approval_id,
            None,
            None,
            created_at,
            created_at,
        ),
    )
    conn.execute(
        """INSERT INTO approvals(
            approval_id,task_id,run_id,tool_call_id,requested_by_agent_id,
            approver_user_id,decision,reason,subject_type,subject_id,subject_hash,
            expires_at,created_at,decided_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            approval_id,
            task_id,
            run_id,
            None,
            agent_id,
            None,
            "pending",
            f"Review Agent Plan {plan_id}.",
            "agent_plan",
            plan_id,
            plan_hash,
            None,
            created_at,
            None,
        ),
    )


def main() -> int:
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="agentops-human-auth-") as tmp:
        tmp_path = Path(tmp)
        db_path = tmp_path / "agentops_mis.db"
        port = free_port()
        base_url = f"http://127.0.0.1:{port}"
        env = os.environ.copy()
        env.update(
            {
                "AGENTOPS_DB_PATH": str(db_path),
                "AGENTOPS_SKIP_SEED_EXPORTS": "1",
                "AGENTOPS_DEPLOYMENT_MODE": "private_host",
                "AGENTOPS_HUMAN_AUTH_REQUIRED": "true",
                "AGENTOPS_COOKIE_SECURE": "true",
                "AGENTOPS_API_KEY": "fixture-machine-key",
                "AGENTOPS_ADMIN_KEY": "fixture-admin-key",
                "AGENTOPS_OWNER_SETUP_CODE": "fixture-owner-setup-code",
                "AGENTOPS_ALLOWED_ORIGINS": f"{base_url},https://host.tailnet.test:8443",
                "HERMES_ALLOW_REAL_RUN": "false",
            }
        )
        process = subprocess.Popen(
            [sys.executable, "server.py", "--host", "127.0.0.1", "--port", str(port)],
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        cookie_jar = http.cookiejar.CookieJar()
        browser = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))
        anonymous = urllib.request.build_opener()
        evidence: dict[str, object] = {}
        try:
            deadline = time.time() + 30
            while time.time() < deadline:
                if process.poll() is not None:
                    break
                try:
                    status, _headers, health = request_json(anonymous, base_url + "/health")
                    if status == 200:
                        evidence["health"] = health
                        break
                except (OSError, ValueError, urllib.error.URLError):
                    time.sleep(0.2)
            if "health" not in evidence:
                failures.append("private host did not become ready")

            status, _headers, payload = request_json(anonymous, base_url + "/api/tasks")
            evidence["anonymous_read"] = {"status": status, "error": payload.get("error")}
            if status != 401 or payload.get("error") != "human_auth_required":
                failures.append("anonymous human API read did not fail closed")

            status, _headers, payload = request_json(anonymous, base_url + "/api/agent-gateway/status")
            evidence["anonymous_gateway"] = {"status": status, "error": payload.get("error")}
            if status != 401:
                failures.append("Agent Gateway did not retain independent machine authentication")
            status, _headers, payload = request_json(
                anonymous,
                base_url + "/api/agent-gateway/status",
                headers={"Authorization": "Bearer fixture-machine-key"},
            )
            gateway_valid_scopes = payload.get("valid_scopes") or []
            if status != 200 or payload.get("provider") != "agent_gateway":
                failures.append("machine API key did not authenticate Agent Gateway")

            spoof_agent_id = "agt_worker_session_spoof_fixture"
            machine_headers = {
                "Authorization": "Bearer fixture-machine-key",
                "X-AgentOps-Agent-Id": spoof_agent_id,
                "X-AgentOps-Workspace-Id": "local-demo",
            }
            status, _headers, session_payload = request_json(
                anonymous,
                base_url + "/api/agent-gateway/session/create",
                method="POST",
                body={"scopes": gateway_valid_scopes, "ttl_sec": 900},
                headers=machine_headers,
            )
            spoof_session_id = session_payload.get("session_id")
            if status != 201 or not spoof_session_id:
                failures.append("machine Session fixture could not be created")
            status, _headers, heartbeat_payload = request_json(
                anonymous,
                base_url + "/api/agent-gateway/heartbeat",
                method="POST",
                body={
                    "workspace_id": "local-demo",
                    "agent_id": spoof_agent_id,
                    "_auth_session_id": spoof_session_id,
                    "status": "idle",
                    "summary": "Reserved Session field spoof fixture.",
                    "runtime_type": "openclaw",
                },
                headers=machine_headers,
            )
            status_read, _headers, worker_status_payload = request_json(
                anonymous,
                base_url + "/api/agent-gateway/host-workers/status",
                headers=machine_headers,
            )
            spoof_worker = next((
                item for item in (worker_status_payload.get("service_workers") or [])
                if item.get("agent_id") == spoof_agent_id
            ), {})
            evidence["machine_session_spoof_rejected"] = {
                "heartbeat_status": status,
                "session_observation_recorded": heartbeat_payload.get("session_observation_recorded"),
                "read_status": status_read,
                "fleet_state": spoof_worker.get("heartbeat_state"),
            }
            if (
                status != 200
                or heartbeat_payload.get("session_observation_recorded") is not False
                or status_read != 200
                or spoof_worker.get("heartbeat_state") != "never_seen"
            ):
                failures.append("global machine key could spoof an authenticated Session heartbeat")
            status, _headers, payload = request_json(anonymous, base_url + "/api/operator/loop-supervision")
            if status != 401 or payload.get("error") != "human_auth_required":
                failures.append("anonymous operator supervision read did not require human auth")
            status, _headers, payload = request_json(
                anonymous,
                base_url + "/api/operator/loop-supervision?adapter=hermes&limit=2",
                headers={"Authorization": "Bearer fixture-machine-key"},
            )
            evidence["machine_scoped_supervision"] = {
                "status": status,
                "operation": payload.get("operation"),
                "token_omitted": payload.get("token_omitted"),
            }
            if status != 200 or payload.get("operation") != "operator_loop_supervision":
                failures.append("scoped machine credential could not read loop supervision")

            status, _headers, payload = request_json(browser, base_url + "/api/human-auth/status")
            evidence["bootstrap_status"] = payload
            if status != 200 or not payload.get("required") or not payload.get("bootstrap_required") or payload.get("cookie_secure") is not False:
                failures.append("human auth did not report owner bootstrap requirement")

            status, _headers, payload = request_json(
                browser,
                base_url + "/api/human-auth/bootstrap",
                method="POST",
                body={"setup_code": "wrong-fixture", "username": "owner", "password": "fixture-password-value"},
                headers={"Origin": base_url},
            )
            if status != 401 or payload.get("error") != "invalid_setup_code":
                failures.append("invalid owner setup code was not rejected")

            status, response_headers, payload = request_json(
                browser,
                base_url + "/api/human-auth/bootstrap",
                method="POST",
                body={
                    "setup_code": "fixture-owner-setup-code",
                    "username": "owner",
                    "display_name": "Local Owner",
                    "password": "fixture-password-value",
                },
                headers={"Origin": base_url},
            )
            csrf_token = str(payload.get("csrf_token") or "")
            owner_account_id = str((payload.get("user") or {}).get("account_id") or "")
            set_cookie = response_headers.get("Set-Cookie", "")
            evidence["bootstrap"] = {
                "status": status,
                "role": (payload.get("user") or {}).get("role"),
                "http_only": "HttpOnly" in set_cookie,
                "same_site_strict": "SameSite=Strict" in set_cookie,
                "loopback_cookie_not_secure": "Secure" not in set_cookie,
                "token_omitted": payload.get("token_omitted"),
            }
            if status != 201 or not csrf_token or "HttpOnly" not in set_cookie or "SameSite=Strict" not in set_cookie or "Secure" in set_cookie:
                failures.append("owner bootstrap did not create the expected loopback browser session")

            other_workspace = "workspace-other-fixture"
            other_agent_id = "agt_worker_cross_workspace_fixture"
            other_task_id = "tsk_cross_workspace_hygiene_fixture"
            other_token_id = "tokref_cross_workspace_hygiene_fixture"
            other_memory_id = "mem_cross_workspace_commander_fixture"
            other_run_id = "run_cross_workspace_review_fixture"
            other_approval_id = "ap_cross_workspace_review_fixture"
            other_artifact_id = "art_customer_cross_workspace_review_fixture"
            mixed_artifact_id = "art_customer_mixed_workspace_link_fixture"
            mixed_approval_id = "ap_mixed_workspace_link_fixture"
            other_tool_call_id = "tc_cross_workspace_review_fixture"
            other_evaluation_id = "eval_cross_workspace_review_fixture"
            other_audit_id = "audit_cross_workspace_review_fixture"
            other_case_id = "evalcase_cross_workspace_review_fixture"
            other_knowledge_doc_id = "kdoc_cross_workspace_private_fixture"
            other_knowledge_chunk_id = "kchunk_cross_workspace_private_fixture"
            other_knowledge_term = "CROSSWORKSPACEKNOWLEDGEFIXTURE"
            created_agent_id = "agt_human_workspace_member_fixture"
            collaborator_agent_id = "agt_human_collaborator_only_fixture"
            collaborator_task_id = "tsk_human_collaborator_only_fixture"
            atomic_failure_agent_id = "agt_human_atomic_failure_fixture"
            human_plan_task_id = "tsk_human_plan_review_fixture"
            human_plan_run_id = "run_human_plan_review_fixture"
            human_plan_agent_id = "agt_research"
            human_plan_reject_id = "plan_human_session_reject_fixture"
            human_plan_reject_approval_id = "ap_human_session_reject_fixture"
            human_plan_reject_hash = "hash_human_session_reject_fixture"
            atomic_plan_id = "plan_human_approval_atomic_failure_fixture"
            atomic_plan_approval_id = "ap_human_approval_atomic_failure_fixture"
            atomic_plan_hash = "hash_human_approval_atomic_failure_fixture"
            shared_delegation_id = "delegation-cross-workspace-graph-fixture"
            local_graph_run_id = ""
            local_graph_task_id = ""
            local_visible_agent_count = 0
            old_at = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=2)).isoformat()
            with sqlite3.connect(db_path) as fixture_conn:
                local_graph_row = fixture_conn.execute(
                    """SELECT run_id,task_id FROM runs
                    WHERE COALESCE(workspace_id,'local-demo')='local-demo'
                      AND task_id IS NOT NULL
                    ORDER BY created_at LIMIT 1"""
                ).fetchone()
                if local_graph_row:
                    local_graph_run_id = str(local_graph_row[0])
                    local_graph_task_id = str(local_graph_row[1])
                    fixture_conn.execute(
                        "UPDATE runs SET delegation_id=? WHERE run_id=?",
                        (shared_delegation_id, local_graph_run_id),
                    )
                fixture_conn.execute(
                    """INSERT INTO agents(
                        agent_id,name,role,description,runtime_type,model_provider,model_name,
                        status,permission_level,allowed_tools,budget_limit_usd,owner_user_id,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        other_agent_id,
                        "Cross-workspace fixture",
                        "worker",
                        "Workspace isolation fixture.",
                        "openclaw",
                        "fixture",
                        "fixture",
                        "running",
                        "worker",
                        "[]",
                        0,
                        None,
                        old_at,
                        old_at,
                    ),
                )
                fixture_conn.execute(
                    """INSERT INTO agents(
                        agent_id,name,role,description,runtime_type,model_provider,model_name,
                        status,permission_level,allowed_tools,budget_limit_usd,owner_user_id,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        collaborator_agent_id,
                        "Collaborator-only fixture",
                        "reviewer",
                        "Must be projected through collaborator_agent_ids only.",
                        "mock",
                        "fixture",
                        "fixture",
                        "idle",
                        "worker",
                        "[]",
                        0,
                        None,
                        old_at,
                        old_at,
                    ),
                )
                fixture_conn.execute(
                    """INSERT INTO tasks(
                        task_id,workspace_id,title,description,requester_id,owner_agent_id,
                        collaborator_agent_ids,status,priority,due_date,acceptance_criteria,
                        risk_level,budget_limit_usd,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        collaborator_task_id,
                        "local-demo",
                        "Collaborator-only Agent projection fixture",
                        "The collaborator must remain visible without owning a task or run.",
                        None,
                        "agt_research",
                        json.dumps([collaborator_agent_id]),
                        "planned",
                        "medium",
                        None,
                        "Agent list, detail, performance, and dashboard include the collaborator.",
                        "low",
                        0,
                        old_at,
                        old_at,
                    ),
                )
                fixture_conn.execute(
                    """INSERT INTO tasks(
                        task_id,workspace_id,title,description,requester_id,owner_agent_id,
                        collaborator_agent_ids,status,priority,due_date,acceptance_criteria,
                        risk_level,budget_limit_usd,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        human_plan_task_id,
                        "local-demo",
                        "Human Agent Plan review fixture",
                        "A bounded local fixture for browser-session Plan decisions.",
                        None,
                        human_plan_agent_id,
                        "[]",
                        "waiting_approval",
                        "high",
                        None,
                        "A valid Human owner can decide without machine credentials.",
                        "high",
                        0,
                        old_at,
                        old_at,
                    ),
                )
                fixture_conn.execute(
                    """INSERT INTO runs(
                        run_id,workspace_id,task_id,agent_id,runtime_type,status,
                        started_at,output_summary,approval_required,created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (
                        human_plan_run_id,
                        "local-demo",
                        human_plan_task_id,
                        human_plan_agent_id,
                        "governance",
                        "waiting_approval",
                        old_at,
                        "Bounded Human Agent Plan review fixture.",
                        1,
                        old_at,
                    ),
                )
                insert_agent_plan_review_fixture(
                    fixture_conn,
                    plan_id=human_plan_reject_id,
                    approval_id=human_plan_reject_approval_id,
                    task_id=human_plan_task_id,
                    run_id=human_plan_run_id,
                    agent_id=human_plan_agent_id,
                    plan_hash=human_plan_reject_hash,
                    created_at=old_at,
                )
                insert_agent_plan_review_fixture(
                    fixture_conn,
                    plan_id=atomic_plan_id,
                    approval_id=atomic_plan_approval_id,
                    task_id=human_plan_task_id,
                    run_id=human_plan_run_id,
                    agent_id=human_plan_agent_id,
                    plan_hash=atomic_plan_hash,
                    created_at=old_at,
                )
                fixture_conn.execute(
                    """INSERT INTO tasks(
                        task_id,workspace_id,title,description,requester_id,owner_agent_id,
                        collaborator_agent_ids,status,priority,due_date,acceptance_criteria,
                        risk_level,budget_limit_usd,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        other_task_id,
                        other_workspace,
                        "Cross-workspace hygiene fixture",
                        "Must remain untouched by the local-demo owner.",
                        None,
                        other_agent_id,
                        "[]",
                        "running",
                        "medium",
                        None,
                        "Remain isolated.",
                        "low",
                        0,
                        old_at,
                        old_at,
                    ),
                )
                fixture_conn.execute(
                    """INSERT INTO runs(
                        run_id,workspace_id,task_id,agent_id,runtime_type,status,
                        started_at,output_summary,delegation_id,created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (
                        other_run_id,
                        other_workspace,
                        other_task_id,
                        other_agent_id,
                        "openclaw",
                        "completed",
                        old_at,
                        "Cross-workspace run fixture that must remain hidden.",
                        shared_delegation_id,
                        old_at,
                    ),
                )
                fixture_conn.execute(
                    """INSERT INTO tool_calls(
                        tool_call_id,run_id,agent_id,tool_name,tool_category,risk_level,
                        status,result_summary,started_at,ended_at,created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        other_tool_call_id,
                        other_run_id,
                        other_agent_id,
                        "fixture.read",
                        "custom",
                        "low",
                        "completed",
                        "Cross-workspace tool fixture that must remain hidden.",
                        old_at,
                        old_at,
                        old_at,
                    ),
                )
                fixture_conn.execute(
                    """INSERT INTO approvals(
                        approval_id,task_id,run_id,requested_by_agent_id,decision,
                        reason,created_at
                    ) VALUES(?,?,?,?,?,?,?)""",
                    (
                        mixed_approval_id,
                        local_graph_task_id,
                        other_run_id,
                        other_agent_id,
                        "pending",
                        "Mismatched task/run authority must fail closed.",
                        old_at,
                    ),
                )
                fixture_conn.execute(
                    """INSERT INTO approvals(
                        approval_id,task_id,run_id,requested_by_agent_id,decision,
                        reason,created_at
                    ) VALUES(?,?,?,?,?,?,?)""",
                    (
                        other_approval_id,
                        other_task_id,
                        other_run_id,
                        other_agent_id,
                        "pending",
                        "Cross-workspace approval fixture that must remain hidden.",
                        old_at,
                    ),
                )
                fixture_conn.execute(
                    """INSERT INTO evaluations(
                        evaluation_id,task_id,run_id,agent_id,evaluator_type,score,
                        pass_fail,notes,created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?)""",
                    (
                        other_evaluation_id,
                        other_task_id,
                        other_run_id,
                        other_agent_id,
                        "rule",
                        1.0,
                        "pass",
                        "Cross-workspace evaluation fixture that must remain hidden.",
                        old_at,
                    ),
                )
                fixture_conn.execute(
                    """INSERT INTO artifacts(
                        artifact_id,task_id,run_id,artifact_type,title,summary,created_at
                    ) VALUES(?,?,?,?,?,?,?)""",
                    (
                        other_artifact_id,
                        other_task_id,
                        other_run_id,
                        "customer_delivery_report",
                        "Cross-workspace delivery fixture",
                        "Must remain hidden from the local-demo owner.",
                        old_at,
                    ),
                )
                fixture_conn.execute(
                    """INSERT INTO artifacts(
                        artifact_id,task_id,run_id,artifact_type,title,summary,created_at
                    ) VALUES(?,?,?,?,?,?,?)""",
                    (
                        mixed_artifact_id,
                        local_graph_task_id,
                        other_run_id,
                        "customer_delivery_report",
                        "Mismatched workspace authority fixture",
                        "Must fail closed because task and run ownership disagree.",
                        old_at,
                    ),
                )
                fixture_conn.execute(
                    """INSERT INTO evaluation_case_candidates(
                        case_id,workspace_id,source_type,source_ref,task_id,run_id,
                        artifact_id,evaluation_id,agent_id,case_type,title,input_summary,
                        expected_output_summary,confidence,review_status,created_by_agent_id,
                        created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        other_case_id,
                        other_workspace,
                        "evaluation",
                        other_evaluation_id,
                        other_task_id,
                        other_run_id,
                        other_artifact_id,
                        other_evaluation_id,
                        other_agent_id,
                        "golden",
                        "Cross-workspace evaluation case fixture",
                        "Must remain hidden.",
                        "Must remain hidden.",
                        0.9,
                        "candidate",
                        other_agent_id,
                        old_at,
                        old_at,
                    ),
                )
                fixture_conn.execute(
                    """INSERT INTO agent_gateway_tokens(
                        token_id,token_hash,workspace_id,agent_id,scopes_json,status,label,
                        heartbeat_timeout_sec,created_at,expires_at,revoked_at,last_used_at,last_heartbeat_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        other_token_id,
                        "fixture-cross-workspace-token-hash",
                        other_workspace,
                        other_agent_id,
                        '["agents:heartbeat"]',
                        "active",
                        "Cross-workspace fixture",
                        60,
                        old_at,
                        None,
                        None,
                        None,
                        None,
                    ),
                )
                fixture_conn.execute(
                    """INSERT INTO memories(
                        memory_id,scope,memory_type,canonical_text,source_type,source_ref,
                        project_id,task_id,agent_id,confidence,review_status,owner_user_id,
                        ttl_review_due_at,supersedes_memory_id,access_tags,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        other_memory_id,
                        "task",
                        "project_context",
                        "Cross-workspace memory fixture that must remain hidden.",
                        "manual",
                        other_task_id,
                        None,
                        other_task_id,
                        other_agent_id,
                        0.8,
                        "candidate",
                        None,
                        None,
                        None,
                        "[]",
                        old_at,
                        old_at,
                    ),
                )
                fixture_conn.execute(
                    """INSERT INTO audit_logs(
                        audit_id,actor_type,actor_id,action,entity_type,entity_id,
                        before_hash,after_hash,metadata_json,tamper_chain_hash,created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        other_audit_id,
                        "agent",
                        other_agent_id,
                        "fixture.cross_workspace",
                        "runs",
                        other_run_id,
                        None,
                        None,
                        "{}",
                        "fixture-cross-workspace-audit-chain-hash",
                        old_at,
                    ),
                )
                fixture_conn.execute(
                    """INSERT INTO knowledge_documents(
                        doc_id,workspace_id,access_level,path,title,category,scope,
                        source_hash,content_summary,indexed_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        other_knowledge_doc_id,
                        other_workspace,
                        "private",
                        "knowledge/private/cross-workspace-fixture.md",
                        other_knowledge_term,
                        "fixture",
                        "workspace",
                        "fixture-cross-workspace-source-hash",
                        other_knowledge_term,
                        old_at,
                        old_at,
                    ),
                )
                fixture_conn.execute(
                    """INSERT INTO knowledge_chunks(
                        chunk_id,doc_id,workspace_id,access_level,path,title,heading,
                        heading_path,heading_level,chunk_index,source_hash,content_summary,
                        indexed_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        other_knowledge_chunk_id,
                        other_knowledge_doc_id,
                        other_workspace,
                        "private",
                        "knowledge/private/cross-workspace-fixture.md",
                        other_knowledge_term,
                        "Private fixture",
                        "Private fixture",
                        1,
                        0,
                        "fixture-cross-workspace-chunk-hash",
                        other_knowledge_term,
                        old_at,
                        old_at,
                    ),
                )
                try:
                    fixture_conn.execute(
                        "INSERT INTO knowledge_fts(doc_id,path,title,content) VALUES(?,?,?,?)",
                        (
                            other_knowledge_doc_id,
                            "knowledge/private/cross-workspace-fixture.md",
                            other_knowledge_term,
                            other_knowledge_term,
                        ),
                    )
                    fixture_conn.execute(
                        """INSERT INTO knowledge_chunk_fts(
                            chunk_id,doc_id,path,title,heading,content
                        ) VALUES(?,?,?,?,?,?)""",
                        (
                            other_knowledge_chunk_id,
                            other_knowledge_doc_id,
                            "knowledge/private/cross-workspace-fixture.md",
                            other_knowledge_term,
                            "Private fixture",
                            other_knowledge_term,
                        ),
                    )
                except sqlite3.OperationalError:
                    pass
                fixture_conn.commit()

            create_agent_status, _headers, create_agent_payload = request_json(
                browser,
                base_url + "/api/agents",
                method="POST",
                body={
                    "agent_id": created_agent_id,
                    "name": "Human workspace member fixture",
                    "role": "Worker",
                    "runtime_type": "mock",
                    "status": "idle",
                },
                headers={"X-AgentOps-CSRF": csrf_token, "Origin": base_url},
            )
            agents_status, _headers, agents_payload = request_json(
                browser,
                base_url + "/api/agents",
            )
            visible_agent_ids = {
                item.get("agent_id") for item in agents_payload if isinstance(item, dict)
            } if isinstance(agents_payload, list) else set()
            local_visible_agent_count = len(visible_agent_ids)
            agent_projection_details = []
            for projected_agent_id in (created_agent_id, collaborator_agent_id):
                detail_status, _headers, detail_payload = request_json(
                    browser,
                    base_url + f"/api/agents/{projected_agent_id}",
                )
                performance_status, _headers, performance_payload = request_json(
                    browser,
                    base_url + f"/api/agents/{projected_agent_id}/performance",
                )
                detail_task_ids = {
                    item.get("task_id") for item in (detail_payload.get("tasks") or [])
                }
                agent_projection_details.append({
                    "agent_id": projected_agent_id,
                    "detail_status": detail_status,
                    "performance_status": performance_status,
                    "collaborator_task_visible": (
                        collaborator_task_id in detail_task_ids
                        if projected_agent_id == collaborator_agent_id
                        else None
                    ),
                })
                if detail_status != 200 or performance_status != 200:
                    failures.append("workspace Agent projection did not survive list/detail refresh")
                if (
                    projected_agent_id == collaborator_agent_id
                    and collaborator_task_id not in detail_task_ids
                ):
                    failures.append("collaborator-only Agent detail omitted its workspace task")
            evidence["human_workspace_agent_projection"] = {
                "create_status": create_agent_status,
                "created_workspace": create_agent_payload.get("workspace_id"),
                "list_status": agents_status,
                "created_agent_visible": created_agent_id in visible_agent_ids,
                "collaborator_agent_visible": collaborator_agent_id in visible_agent_ids,
                "details": agent_projection_details,
            }
            if (
                create_agent_status != 201
                or create_agent_payload.get("workspace_id") != "local-demo"
                or agents_status != 200
                or created_agent_id not in visible_agent_ids
                or collaborator_agent_id not in visible_agent_ids
            ):
                failures.append("workspace Agent membership/collaborator projection failed")

            failure_trigger = "fixture_abort_workspace_agent_membership"
            with sqlite3.connect(db_path) as fixture_conn:
                fixture_conn.execute(
                    f"""CREATE TRIGGER {failure_trigger}
                    BEFORE INSERT ON workspace_agent_memberships
                    WHEN NEW.agent_id='{atomic_failure_agent_id}'
                    BEGIN
                        SELECT RAISE(ABORT, 'fixture workspace membership failure');
                    END"""
                )
                fixture_conn.commit()
            try:
                atomic_create_status, _headers, atomic_create_payload = request_json(
                    browser,
                    base_url + "/api/agents",
                    method="POST",
                    body={
                        "agent_id": atomic_failure_agent_id,
                        "name": "Human atomic rollback fixture",
                        "role": "Worker",
                        "runtime_type": "mock",
                        "status": "idle",
                    },
                    headers={"X-AgentOps-CSRF": csrf_token, "Origin": base_url},
                )
            finally:
                with sqlite3.connect(db_path) as fixture_conn:
                    fixture_conn.execute(f"DROP TRIGGER IF EXISTS {failure_trigger}")
                    fixture_conn.commit()
            with sqlite3.connect(db_path) as fixture_conn:
                atomic_agent_count = fixture_conn.execute(
                    "SELECT COUNT(*) FROM agents WHERE agent_id=?",
                    (atomic_failure_agent_id,),
                ).fetchone()[0]
                atomic_membership_count = fixture_conn.execute(
                    "SELECT COUNT(*) FROM workspace_agent_memberships WHERE agent_id=?",
                    (atomic_failure_agent_id,),
                ).fetchone()[0]
                atomic_audit_count = fixture_conn.execute(
                    """SELECT COUNT(*) FROM audit_logs
                    WHERE action='agent.create' AND entity_type='agents' AND entity_id=?""",
                    (atomic_failure_agent_id,),
                ).fetchone()[0]
            evidence["human_agent_create_atomic_rollback"] = {
                "status": atomic_create_status,
                "error": atomic_create_payload.get("error"),
                "agent_rows": atomic_agent_count,
                "membership_rows": atomic_membership_count,
                "audit_rows": atomic_audit_count,
            }
            if (
                atomic_create_status != 500
                or atomic_agent_count != 0
                or atomic_membership_count != 0
                or atomic_audit_count != 0
            ):
                failures.append("Human Agent creation did not roll back after membership failure")

            hygiene_query = "?threshold_sec=30&enrollment_age_sec=0&limit=100"
            preview_status, _headers, hygiene_preview = request_json(
                browser,
                base_url + "/api/workers/fleet/hygiene" + hygiene_query,
            )
            preview_task_ids = {
                item.get("task_id") for item in (hygiene_preview.get("stuck_tasks") or [])
            }
            if preview_status != 200 or other_task_id in preview_task_ids:
                failures.append("human Fleet hygiene preview crossed the Session workspace boundary")
            release_status, _headers, release_payload = request_json(
                browser,
                base_url + "/api/workers/tasks/release",
                method="POST",
                body={"task_id": other_task_id, "force": True},
                headers={"X-AgentOps-CSRF": csrf_token, "Origin": base_url},
            )
            if release_status != 404 or release_payload.get("error") != "task not found":
                failures.append("human task release crossed the Session workspace boundary")
            apply_status, _headers, hygiene_apply = request_json(
                browser,
                base_url + "/api/workers/fleet/hygiene",
                method="POST",
                body={
                    "threshold_sec": 30,
                    "enrollment_age_sec": 0,
                    "limit": 100,
                    "apply": True,
                    "confirm_cleanup": True,
                },
                headers={"X-AgentOps-CSRF": csrf_token, "Origin": base_url},
            )
            with sqlite3.connect(db_path) as fixture_conn:
                other_task_status = fixture_conn.execute(
                    "SELECT status FROM tasks WHERE task_id=?",
                    (other_task_id,),
                ).fetchone()[0]
                other_token_status = fixture_conn.execute(
                    "SELECT status FROM agent_gateway_tokens WHERE token_id=?",
                    (other_token_id,),
                ).fetchone()[0]
                local_task_count = fixture_conn.execute(
                    "SELECT COUNT(*) FROM tasks WHERE COALESCE(workspace_id,'local-demo')='local-demo'"
                ).fetchone()[0]
                local_memory_count = fixture_conn.execute(
                    """SELECT COUNT(*) FROM memories
                    WHERE COALESCE(workspace_id,'local-demo')='local-demo'"""
                ).fetchone()[0]
                local_run_count = fixture_conn.execute(
                    "SELECT COUNT(*) FROM runs WHERE COALESCE(workspace_id,'local-demo')='local-demo'"
                ).fetchone()[0]
                local_tool_call_count = fixture_conn.execute(
                    """SELECT COUNT(*) FROM tool_calls tc JOIN runs r ON r.run_id=tc.run_id
                    WHERE COALESCE(r.workspace_id,'local-demo')='local-demo'"""
                ).fetchone()[0]
                local_evaluation_count = fixture_conn.execute(
                    """SELECT COUNT(*) FROM evaluations e JOIN runs r ON r.run_id=e.run_id
                    WHERE COALESCE(r.workspace_id,'local-demo')='local-demo'"""
                ).fetchone()[0]
                local_approval_count = fixture_conn.execute(
                    """SELECT COUNT(*) FROM approvals ap
                    LEFT JOIN runs r ON r.run_id=ap.run_id
                    LEFT JOIN tasks t ON t.task_id=COALESCE(ap.task_id,r.task_id)
                    WHERE (ap.run_id IS NULL OR COALESCE(r.workspace_id,'local-demo')='local-demo')
                      AND (COALESCE(ap.task_id,r.task_id) IS NULL OR COALESCE(t.workspace_id,'local-demo')='local-demo')
                      AND (ap.run_id IS NOT NULL OR COALESCE(ap.task_id,r.task_id) IS NOT NULL)"""
                ).fetchone()[0]
                local_pending_approval_count = fixture_conn.execute(
                    """SELECT COUNT(*) FROM approvals ap
                    LEFT JOIN runs r ON r.run_id=ap.run_id
                    LEFT JOIN tasks t ON t.task_id=COALESCE(ap.task_id,r.task_id)
                    WHERE ap.decision='pending'
                      AND (ap.run_id IS NULL OR COALESCE(r.workspace_id,'local-demo')='local-demo')
                      AND (COALESCE(ap.task_id,r.task_id) IS NULL OR COALESCE(t.workspace_id,'local-demo')='local-demo')
                      AND (ap.run_id IS NOT NULL OR COALESCE(ap.task_id,r.task_id) IS NOT NULL)"""
                ).fetchone()[0]
                local_artifact_count = fixture_conn.execute(
                    """SELECT COUNT(*) FROM artifacts a
                    LEFT JOIN runs r ON r.run_id=a.run_id
                    LEFT JOIN tasks t ON t.task_id=COALESCE(a.task_id,r.task_id)
                    WHERE (a.run_id IS NULL OR COALESCE(r.workspace_id,'local-demo')='local-demo')
                      AND (COALESCE(a.task_id,r.task_id) IS NULL OR COALESCE(t.workspace_id,'local-demo')='local-demo')
                      AND (a.run_id IS NOT NULL OR COALESCE(a.task_id,r.task_id) IS NOT NULL)"""
                ).fetchone()[0]
                local_direct_audit_count = fixture_conn.execute(
                    """SELECT COUNT(DISTINCT al.audit_id) FROM audit_logs al
                    LEFT JOIN tasks t ON t.task_id=al.entity_id
                    LEFT JOIN runs r ON r.run_id=al.entity_id
                    WHERE COALESCE(t.workspace_id,r.workspace_id,'')='local-demo'"""
                ).fetchone()[0]
            evidence["human_workspace_fleet_hygiene"] = {
                "preview_status": preview_status,
                "other_task_hidden": other_task_id not in preview_task_ids,
                "release_status": release_status,
                "apply_status": apply_status,
                "other_task_status": other_task_status,
                "other_token_status": other_token_status,
                "token_omitted": hygiene_apply.get("token_omitted"),
            }
            if apply_status not in {200, 207} or other_task_status != "running" or other_token_status != "active":
                failures.append("confirmed human Fleet hygiene mutated another workspace")

            readiness_status, _headers, readiness_payload = request_json(
                browser,
                base_url + "/api/local/readiness",
            )
            readiness_evidence = readiness_payload.get("evidence") or {}
            evidence["human_workspace_readiness_scope"] = {
                "status": readiness_status,
                "workspace_id": readiness_payload.get("workspace_id"),
                "tasks": readiness_evidence.get("tasks"),
                "memories": readiness_evidence.get("memories"),
                "runs": readiness_evidence.get("runs"),
                "tool_calls": readiness_evidence.get("tool_calls"),
                "evaluations": readiness_evidence.get("evaluations"),
                "approvals": readiness_evidence.get("approvals"),
                "artifacts": readiness_evidence.get("artifacts"),
                "audit_logs": readiness_evidence.get("audit_logs"),
            }
            if (
                readiness_status != 200
                or readiness_payload.get("workspace_id") != "local-demo"
                or readiness_evidence.get("tasks") != local_task_count
                or readiness_evidence.get("memories") != local_memory_count
                or readiness_evidence.get("runs") != local_run_count
                or readiness_evidence.get("tool_calls") != local_tool_call_count
                or readiness_evidence.get("evaluations") != local_evaluation_count
                or readiness_evidence.get("approvals") != local_approval_count
                or readiness_evidence.get("artifacts") != local_artifact_count
                or readiness_evidence.get("audit_logs") != local_direct_audit_count
            ):
                failures.append("human local readiness aggregated another workspace")

            board_status, _headers, board_payload = request_json(
                browser,
                base_url + "/api/commander/project-board?limit=50",
            )
            board_task_ids = {
                item.get("task_id") for item in (board_payload.get("recent_work_packages") or [])
            }
            board_task_total = sum(
                int(value or 0)
                for value in ((board_payload.get("counts") or {}).get("tasks_by_status") or {}).values()
            )
            if (
                board_status != 200
                or board_payload.get("workspace_id") != "local-demo"
                or other_task_id in board_task_ids
                or board_task_total != local_task_count
            ):
                failures.append("human Commander project board crossed the Session workspace boundary")

            inbox_status, _headers, inbox_payload = request_json(
                browser,
                base_url + "/api/commander/integration-inbox?bucket=all&limit=50&threshold_sec=60",
            )
            inbox_rows = inbox_payload.get("inbox_items") or []
            if (
                inbox_status != 200
                or inbox_payload.get("workspace_id") != "local-demo"
                or any(
                    item.get("task_id") == other_task_id
                    or item.get("memory_id") == other_memory_id
                    for item in inbox_rows
                )
            ):
                failures.append("human Commander inbox crossed the Session workspace boundary")

            workspace_private_refs = {
                other_task_id,
                other_run_id,
                other_approval_id,
                other_memory_id,
                other_artifact_id,
                mixed_artifact_id,
                mixed_approval_id,
                other_tool_call_id,
                other_evaluation_id,
                other_audit_id,
                other_case_id,
                other_knowledge_doc_id,
                other_knowledge_chunk_id,
                other_knowledge_term,
            }
            scoped_read_routes = [
                "/api/agents",
                "/api/tasks",
                "/api/runs?limit=100",
                "/api/runs/export",
                "/api/tool-calls?limit=100",
                "/api/approvals",
                "/api/memories",
                "/api/memories/export",
                "/api/evaluations",
                "/api/evaluation-cases?limit=100",
                "/api/evaluation-case-runs?limit=100",
                "/api/artifacts",
                "/api/audit?limit=500",
                "/api/runtime-events",
                f"/api/knowledge/search?q={other_knowledge_term}&limit=10",
                f"/api/knowledge/evidence-packet?q={other_knowledge_term}&limit=5",
                "/api/review/queue?limit=50",
                "/api/workflows/customer-delivery-board?limit=50",
                "/api/workflows/hermes-openclaw-loop?limit=50",
                "/api/operator/action-plan?limit=30",
                f"/api/operator/evidence-report?workspace_id={other_workspace}&limit=30",
                "/api/operator/loop-audit?limit=30",
                "/api/operator/handoff?limit=12",
                "/api/operator/start-check?adapter=mock&limit=8",
                "/api/operator/agent-loop-handoff?adapter=mock&limit=8",
                "/api/operator/loop-self-check?adapter=mock&limit=8",
                "/api/operator/command-center?limit=30",
                "/api/operator/health?limit=30",
                "/api/dashboard/metrics",
            ]
            scoped_read_results = []
            for route in scoped_read_routes:
                try:
                    scoped_status, _headers, scoped_payload = request_json(
                        browser,
                        base_url + route,
                    )
                except TimeoutError:
                    failures.append(f"human scoped read timed out: {route}")
                    scoped_read_results.append({
                        "route": route.split("?", 1)[0],
                        "status": "timeout",
                        "cross_workspace_refs_hidden": False,
                    })
                    break
                leak_surface = scoped_payload
                if route.startswith("/api/knowledge/search"):
                    # The API safely echoes the caller-supplied query; authority applies to results.
                    leak_surface = {"results": scoped_payload.get("results") or []}
                encoded_payload = json.dumps(leak_surface, ensure_ascii=False, sort_keys=True)
                leaked_refs = sorted(ref for ref in workspace_private_refs if ref in encoded_payload)
                scoped_read_results.append({
                    "route": route.split("?", 1)[0],
                    "status": scoped_status,
                    "cross_workspace_refs_hidden": not leaked_refs,
                })
                if scoped_status != 200 or leaked_refs:
                    failures.append(
                        f"human scoped read crossed the Session workspace boundary: {route}"
                    )
            evidence["human_workspace_review_delivery_scope"] = scoped_read_results

            with sqlite3.connect(db_path) as fixture_conn:
                fixture_conn.execute(
                    """INSERT INTO tasks(
                        task_id,workspace_id,title,description,requester_id,owner_agent_id,
                        collaborator_agent_ids,status,priority,due_date,acceptance_criteria,
                        risk_level,budget_limit_usd,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        other_run_id,
                        "local-demo",
                        "Audit entity-type collision fixture",
                        "A local Task shares an ID with a foreign Run only for this read check.",
                        None,
                        "agt_research",
                        "[]",
                        "planned",
                        "low",
                        None,
                        "Foreign Run audit must remain hidden.",
                        "low",
                        0,
                        old_at,
                        old_at,
                    ),
                )
                fixture_conn.commit()
            collision_audit_status, _headers, collision_audit_payload = request_json(
                browser,
                base_url + "/api/audit?limit=500",
            )
            collision_audit_raw = json.dumps(
                collision_audit_payload,
                ensure_ascii=False,
                sort_keys=True,
            )
            with sqlite3.connect(db_path) as fixture_conn:
                fixture_conn.execute("DELETE FROM tasks WHERE task_id=?", (other_run_id,))
                fixture_conn.commit()
            evidence["human_workspace_audit_entity_pair_scope"] = {
                "status": collision_audit_status,
                "foreign_run_audit_hidden": other_audit_id not in collision_audit_raw,
            }
            if collision_audit_status != 200 or other_audit_id in collision_audit_raw:
                failures.append("Human audit scope ignored entity_type during an ID collision")

            scoped_detail_routes = [
                f"/api/agents/{other_agent_id}",
                f"/api/agents/{other_agent_id}/performance",
                f"/api/tasks/{other_task_id}",
                f"/api/runs/{other_run_id}",
                f"/api/runs/{other_run_id}/graph",
                f"/api/runs/{other_run_id}/evidence-graph",
            ]
            detail_results = []
            for route in scoped_detail_routes:
                detail_status, _headers, detail_payload = request_json(browser, base_url + route)
                detail_results.append({"route": route, "status": detail_status})
                if detail_status != 404 or detail_payload.get("error") != "not found":
                    failures.append(f"human detail route exposed another workspace: {route}")
            evidence["human_workspace_detail_scope"] = detail_results

            if local_graph_run_id:
                local_graph_status, _headers, local_graph_payload = request_json(
                    browser,
                    base_url + f"/api/runs/{local_graph_run_id}/graph",
                )
                local_graph_refs = json.dumps(
                    local_graph_payload,
                    ensure_ascii=False,
                    sort_keys=True,
                )
                evidence["human_workspace_run_graph_related_scope"] = {
                    "status": local_graph_status,
                    "other_workspace_sibling_hidden": other_run_id not in local_graph_refs,
                }
                if local_graph_status != 200 or other_run_id in local_graph_refs:
                    failures.append("human run graph included a cross-workspace related run")
            else:
                failures.append("human run graph workspace fixture had no local root run")

            artifact_download_results = []
            for artifact_id in (other_artifact_id, mixed_artifact_id):
                artifact_download_status, _headers, artifact_download_payload = request_json(
                    browser,
                    base_url + f"/api/artifacts/{artifact_id}/download?format=json",
                )
                artifact_download_results.append({
                    "artifact_id": artifact_id,
                    "status": artifact_download_status,
                    "error": artifact_download_payload.get("error"),
                })
                if (
                    artifact_download_status != 404
                    or artifact_download_payload.get("error") != "artifact_not_found"
                ):
                    failures.append("human artifact download exposed conflicting workspace authority")
            evidence["human_workspace_artifact_download_scope"] = artifact_download_results

            mutation_headers = {"X-AgentOps-CSRF": csrf_token, "Origin": base_url}
            cross_workspace_mutations = [
                ("POST", "/api/tasks", {
                    "workspace_id": other_workspace,
                    "title": "Forbidden cross-workspace task",
                    "description": "Must not be created.",
                }, 403),
                ("POST", "/api/tasks", {
                    "task_id": other_task_id,
                    "title": "Forbidden task ID takeover",
                    "description": "Must not replace another workspace task.",
                }, 409),
                ("PATCH", f"/api/tasks/{other_task_id}/status", {"status": "completed"}, 404),
                ("POST", f"/api/approvals/{other_approval_id}/approve", {}, 404),
                ("POST", f"/api/approvals/{mixed_approval_id}/approve", {}, 404),
                ("POST", f"/api/memories/{other_memory_id}/approve", {}, 404),
                ("POST", "/api/evaluations/run-rule-check", {"run_id": other_run_id}, 404),
                ("POST", f"/api/evaluation-cases/{other_case_id}/approve", {}, 404),
                ("POST", f"/api/commander/work-packages/{other_task_id}/dispatch", {
                    "adapter": "mock",
                }, 404),
                ("POST", f"/api/commander/work-packages/{other_task_id}/coding-workspace", {}, 404),
                ("POST", "/api/commander/work-packages/synthesis/promote", {
                    "artifact_id": other_artifact_id,
                    "confirm_promote": True,
                }, 404),
                ("POST", "/api/operator/execution-evidence/remediation-task", {
                    "run_id": other_run_id,
                    "confirm_create": True,
                }, 404),
                ("POST", "/api/operator/execution-evidence/close-gap", {
                    "run_id": other_run_id,
                    "confirm_close": True,
                }, 404),
                ("POST", "/api/evaluation-cases/propose", {
                    "workspace_id": "local-demo",
                    "evaluation_id": other_evaluation_id,
                    "confirm_create": True,
                }, 404),
            ]
            mutation_results = []
            for method, route, mutation_body, expected_status in cross_workspace_mutations:
                mutation_status, _headers, mutation_payload = request_json(
                    browser,
                    base_url + route,
                    method=method,
                    body=mutation_body,
                    headers=mutation_headers,
                )
                mutation_results.append({
                    "method": method,
                    "route": route,
                    "status": mutation_status,
                })
                if mutation_status != expected_status:
                    failures.append(f"human mutation crossed the Session workspace boundary: {route}")
            with sqlite3.connect(db_path) as fixture_conn:
                mutation_task_status = fixture_conn.execute(
                    "SELECT status FROM tasks WHERE task_id=?",
                    (other_task_id,),
                ).fetchone()[0]
                mutation_task_title = fixture_conn.execute(
                    "SELECT title FROM tasks WHERE task_id=?",
                    (other_task_id,),
                ).fetchone()[0]
                mutation_approval_status = fixture_conn.execute(
                    "SELECT decision FROM approvals WHERE approval_id=?",
                    (other_approval_id,),
                ).fetchone()[0]
                mixed_approval_status = fixture_conn.execute(
                    "SELECT decision FROM approvals WHERE approval_id=?",
                    (mixed_approval_id,),
                ).fetchone()[0]
                mutation_memory_status = fixture_conn.execute(
                    "SELECT review_status FROM memories WHERE memory_id=?",
                    (other_memory_id,),
                ).fetchone()[0]
                mutation_case_status = fixture_conn.execute(
                    "SELECT review_status FROM evaluation_case_candidates WHERE case_id=?",
                    (other_case_id,),
                ).fetchone()[0]
            evidence["human_workspace_mutation_scope"] = {
                "requests": mutation_results,
                "other_task_status": mutation_task_status,
                "other_task_title": mutation_task_title,
                "other_approval_status": mutation_approval_status,
                "mixed_approval_status": mixed_approval_status,
                "other_memory_status": mutation_memory_status,
                "other_case_status": mutation_case_status,
            }
            if (
                mutation_task_status != "running"
                or mutation_task_title != "Cross-workspace hygiene fixture"
                or mutation_approval_status != "pending"
                or mixed_approval_status != "pending"
                or mutation_memory_status != "candidate"
                or mutation_case_status != "candidate"
            ):
                failures.append("blocked Human mutation changed another workspace")

            dashboard_status, _headers, dashboard_payload = request_json(
                browser,
                base_url + "/api/dashboard/metrics?refresh_cache=true",
            )
            dashboard_recent_ids = {
                item.get("run_id") for item in (dashboard_payload.get("recent_runs") or [])
            }
            dashboard_agent_performance = {
                item.get("agent_id"): item
                for item in (dashboard_payload.get("agent_performance_summary") or [])
                if isinstance(item, dict) and item.get("agent_id")
            }
            zero_run_agent_ids = {created_agent_id, collaborator_agent_id}
            zero_run_totals = {
                agent_id: (dashboard_agent_performance.get(agent_id) or {}).get("total_runs")
                for agent_id in sorted(zero_run_agent_ids)
            }
            evidence["human_workspace_dashboard_scope"] = {
                "status": dashboard_status,
                "workspace_id": dashboard_payload.get("workspace_id"),
                "agents_total": dashboard_payload.get("agents_total"),
                "tasks_total": dashboard_payload.get("tasks_total"),
                "pending_approvals": dashboard_payload.get("pending_approvals"),
                "other_run_hidden": other_run_id not in dashboard_recent_ids,
                "zero_run_agents_present": zero_run_agent_ids.issubset(dashboard_agent_performance),
                "zero_run_totals": zero_run_totals,
            }
            if (
                dashboard_status != 200
                or dashboard_payload.get("workspace_id") != "local-demo"
                or dashboard_payload.get("tasks_total") != local_task_count
                or dashboard_payload.get("agents_total") != local_visible_agent_count
                or dashboard_payload.get("pending_approvals") != local_pending_approval_count
                or other_run_id in dashboard_recent_ids
            ):
                failures.append("human dashboard metrics aggregated another workspace")
            if (
                not zero_run_agent_ids.issubset(dashboard_agent_performance)
                or any(total_runs != 0 for total_runs in zero_run_totals.values())
            ):
                failures.append("human dashboard omitted a visible zero-run Agent from performance summary")

            demo_status, _headers, demo_payload = request_json(
                browser,
                base_url + "/api/demo/readiness",
            )
            if demo_status != 200 or demo_payload.get("workspace_id") != "local-demo":
                failures.append("human demo readiness lost the Session workspace binding")

            plan_decision_headers = {"X-AgentOps-CSRF": csrf_token, "Origin": base_url}
            plan_reject_status, _headers, plan_reject_payload = request_json(
                browser,
                base_url + f"/api/agent-plans/{human_plan_reject_id}/reject",
                method="POST",
                body={"reason": "Bounded Human browser-session rejection fixture."},
                headers=plan_decision_headers,
            )
            with sqlite3.connect(db_path) as fixture_conn:
                rejected_plan_status = fixture_conn.execute(
                    "SELECT status FROM agent_plans WHERE plan_id=?",
                    (human_plan_reject_id,),
                ).fetchone()[0]
                rejected_approval_status = fixture_conn.execute(
                    "SELECT decision FROM approvals WHERE approval_id=?",
                    (human_plan_reject_approval_id,),
                ).fetchone()[0]
                rejected_plan_audit = fixture_conn.execute(
                    """SELECT actor_type,action FROM audit_logs
                    WHERE entity_type='agent_plans' AND entity_id=?
                    ORDER BY created_at DESC LIMIT 1""",
                    (human_plan_reject_id,),
                ).fetchone()
            rejected_actor = plan_reject_payload.get("actor") or {}
            evidence["human_session_agent_plan_reject"] = {
                "status": plan_reject_status,
                "error": plan_reject_payload.get("error"),
                "transition": plan_reject_payload.get("transition"),
                "auth_mode": rejected_actor.get("auth_mode"),
                "workspace_id": rejected_actor.get("workspace_id"),
                "machine_credentials_sent": False,
                "plan_status": rejected_plan_status,
                "approval_status": rejected_approval_status,
                "audit_actor_type": rejected_plan_audit[0] if rejected_plan_audit else None,
                "audit_action": rejected_plan_audit[1] if rejected_plan_audit else None,
            }
            if (
                plan_reject_status != 200
                or plan_reject_payload.get("transition") != "rejected"
                or rejected_actor.get("auth_mode") != "human_session"
                or rejected_actor.get("workspace_id") != "local-demo"
                or rejected_plan_status != "rejected"
                or rejected_approval_status != "rejected"
                or not rejected_plan_audit
                or rejected_plan_audit[0] != "user"
                or rejected_plan_audit[1] != "agent_plan.rejected"
            ):
                failures.append("valid Human owner session could not reject a visible Agent Plan without machine credentials")

            atomic_trigger = "fixture_abort_agent_plan_approval_decision"
            with sqlite3.connect(db_path) as fixture_conn:
                atomic_before_plan = fixture_conn.execute(
                    "SELECT status FROM agent_plans WHERE plan_id=?",
                    (atomic_plan_id,),
                ).fetchone()[0]
                atomic_before_approval = fixture_conn.execute(
                    "SELECT decision,decided_at FROM approvals WHERE approval_id=?",
                    (atomic_plan_approval_id,),
                ).fetchone()
                atomic_before_audit_count = fixture_conn.execute(
                    "SELECT COUNT(*) FROM audit_logs WHERE entity_id IN (?,?)",
                    (atomic_plan_id, atomic_plan_approval_id),
                ).fetchone()[0]
                atomic_before_runtime_count = fixture_conn.execute(
                    """SELECT COUNT(*) FROM runtime_events
                    WHERE event_type='agent_plan.rejected' AND run_id=?""",
                    (human_plan_run_id,),
                ).fetchone()[0]
                fixture_conn.execute(
                    f"""CREATE TRIGGER {atomic_trigger}
                    BEFORE UPDATE OF decision ON approvals
                    WHEN NEW.approval_id='{atomic_plan_approval_id}'
                    BEGIN
                        SELECT RAISE(ABORT, 'fixture Agent Plan Approval decision failure');
                    END"""
                )
                fixture_conn.commit()
            try:
                atomic_decision_status, _headers, _atomic_decision_payload = request_json(
                    browser,
                    base_url + f"/api/approvals/{atomic_plan_approval_id}/reject",
                    method="POST",
                    body={},
                    headers=plan_decision_headers,
                )
            finally:
                with sqlite3.connect(db_path) as fixture_conn:
                    fixture_conn.execute(f"DROP TRIGGER IF EXISTS {atomic_trigger}")
                    fixture_conn.commit()
            with sqlite3.connect(db_path) as fixture_conn:
                atomic_after_plan = fixture_conn.execute(
                    "SELECT status FROM agent_plans WHERE plan_id=?",
                    (atomic_plan_id,),
                ).fetchone()[0]
                atomic_after_approval = fixture_conn.execute(
                    "SELECT decision,decided_at FROM approvals WHERE approval_id=?",
                    (atomic_plan_approval_id,),
                ).fetchone()
                atomic_after_audit_count = fixture_conn.execute(
                    "SELECT COUNT(*) FROM audit_logs WHERE entity_id IN (?,?)",
                    (atomic_plan_id, atomic_plan_approval_id),
                ).fetchone()[0]
                atomic_after_runtime_count = fixture_conn.execute(
                    """SELECT COUNT(*) FROM runtime_events
                    WHERE event_type='agent_plan.rejected' AND run_id=?""",
                    (human_plan_run_id,),
                ).fetchone()[0]
            evidence["human_approval_agent_plan_atomic_rollback"] = {
                "status": atomic_decision_status,
                "plan_before": atomic_before_plan,
                "plan_after": atomic_after_plan,
                "approval_before": atomic_before_approval[0],
                "approval_after": atomic_after_approval[0],
                "approval_decided_at_unchanged": atomic_before_approval[1] == atomic_after_approval[1],
                "audit_count_before": atomic_before_audit_count,
                "audit_count_after": atomic_after_audit_count,
                "runtime_count_before": atomic_before_runtime_count,
                "runtime_count_after": atomic_after_runtime_count,
            }
            if (
                atomic_decision_status != 500
                or atomic_before_plan != "submitted"
                or atomic_after_plan != atomic_before_plan
                or atomic_before_approval[0] != "pending"
                or atomic_after_approval != atomic_before_approval
                or atomic_after_audit_count != atomic_before_audit_count
                or atomic_after_runtime_count != atomic_before_runtime_count
            ):
                failures.append("Approval-page Agent Plan failure did not roll back Plan, Approval, Audit, and Runtime Event state")

            remote_browser = urllib.request.build_opener()
            status, remote_headers, payload = request_json(
                remote_browser,
                base_url + "/api/human-auth/login",
                method="POST",
                body={"username": "owner", "password": "fixture-password-value"},
                headers={"Origin": "https://host.tailnet.test:8443"},
            )
            remote_set_cookie = remote_headers.get("Set-Cookie", "")
            evidence["private_https_cookie"] = {
                "status": status,
                "secure": "Secure" in remote_set_cookie,
                "http_only": "HttpOnly" in remote_set_cookie,
                "same_site_strict": "SameSite=Strict" in remote_set_cookie,
            }
            if status != 200 or "Secure" not in remote_set_cookie or "HttpOnly" not in remote_set_cookie or "SameSite=Strict" not in remote_set_cookie:
                failures.append("private HTTPS login did not retain a Secure browser cookie")

            status, _headers, payload = request_json(
                browser,
                base_url + "/api/human-auth/login",
                method="POST",
                body={"username": "owner", "password": "fixture-password-value"},
                headers={"Origin": "https://untrusted.invalid"},
            )
            evidence["wrong_origin"] = {"status": status, "error": payload.get("error")}
            if status != 403 or payload.get("error") != "origin_validation_failed":
                failures.append("untrusted browser Origin was not rejected")

            status, _headers, payload = request_json(browser, base_url + "/api/agent-gateway/status")
            evidence["human_cookie_gateway"] = {"status": status, "error": payload.get("error")}
            if status != 401:
                failures.append("human browser session was incorrectly accepted as a machine credential")

            status, _headers, payload = request_json(browser, base_url + "/api/tasks")
            if status != 200 or not isinstance(payload, list):
                failures.append("authenticated owner could not read human workspace API")
            status, _headers, payload = request_json(browser, base_url + "/api/operator/health")
            evidence["operator_read"] = {"status": status, "provider": payload.get("provider")}
            if status != 200:
                failures.append("authenticated owner could not read operator workspace API")

            mutation_headers = {"X-AgentOps-CSRF": csrf_token, "Origin": base_url}
            status, _headers, receipt_payload = request_json(
                browser,
                base_url + "/api/operator/action-receipts",
                method="POST",
                body={
                    "actor_id": "usr_spoofed_human_receipt_actor",
                    "action_command": "agentops worker service-control --manager launchd --adapter hermes --action restart",
                    "verify_command": "agentops worker service-check --manager launchd --adapter hermes",
                    "action_id": "human_session_actor_binding_fixture",
                    "action_signature": "human-session-actor-binding-signature",
                    "source": "human_browser_auth_smoke.actor_binding",
                    "status": "verified",
                    "result_summary": "Human Session actor binding fixture verified.",
                },
                headers=mutation_headers,
            )
            receipt_id = str((receipt_payload.get("receipt") or {}).get("receipt_id") or "")
            readback_status, _headers, readback_payload = request_json(
                browser,
                base_url + "/api/operator/action-receipts/control-readback",
                method="POST",
                body={
                    "actor_id": "usr_spoofed_human_readback_actor",
                    "receipt_id": receipt_id,
                    "source": "human_browser_auth_smoke.actor_binding.control_readback",
                    "control_readback": {
                        "before": {"selected_gate": "service_control_preview"},
                        "after": {"selected_gate": "service_check_passed"},
                        "self_check": {"server_executes_shell": False, "token_omitted": True},
                        "token_omitted": True,
                    },
                },
                headers=mutation_headers,
            )
            with sqlite3.connect(db_path) as actor_conn:
                actor_rows = actor_conn.execute(
                    """SELECT action,actor_id FROM audit_logs
                       WHERE entity_id=? AND action IN (
                           'operator.action_queue_receipt',
                           'operator.action_queue_control_readback'
                       ) ORDER BY created_at""",
                    (receipt_id,),
                ).fetchall()
            bound_actions = {str(row[0]): str(row[1]) for row in actor_rows}
            actor_bound = (
                bool(owner_account_id)
                and bound_actions.get("operator.action_queue_receipt") == owner_account_id
                and bound_actions.get("operator.action_queue_control_readback") == owner_account_id
            )
            evidence["human_operator_actor_binding"] = {
                "receipt_status": status,
                "readback_status": readback_status,
                "receipt_id_present": bool(receipt_id),
                "session_actor_bound": actor_bound,
                "spoofed_actor_rejected": all(
                    actor not in {
                        "usr_spoofed_human_receipt_actor",
                        "usr_spoofed_human_readback_actor",
                    }
                    for actor in bound_actions.values()
                ),
                "token_omitted": receipt_payload.get("token_omitted") is True
                and readback_payload.get("token_omitted") is True,
            }
            if status != 201 or readback_status != 201 or not receipt_id or not actor_bound:
                failures.append("Human Session operator receipt actor was not bound to the authenticated account")

            status, _headers, payload = request_json(
                browser,
                base_url + "/api/local/readiness",
                headers={"X-AgentOps-Workspace-Id": "workspace-header-spoof"},
            )
            evidence["local_readiness_workspace_binding"] = {
                "status": status,
                "error": payload.get("error"),
            }
            if status != 403 or payload.get("error") != "human_workspace_forbidden":
                failures.append("human local readiness trusted a caller-controlled workspace header")

            status, _headers, payload = request_json(
                browser,
                base_url + "/api/demo/readiness",
                headers={"X-AgentOps-Workspace-Id": "workspace-header-spoof"},
            )
            evidence["demo_readiness_workspace_binding"] = {
                "status": status,
                "error": payload.get("error"),
            }
            if status != 403 or payload.get("error") != "human_workspace_forbidden":
                failures.append("human demo readiness trusted a caller-controlled workspace header")

            task_body = {"title": "Human session fixture task", "description": "Bounded smoke evidence."}
            status, _headers, payload = request_json(browser, base_url + "/api/tasks", method="POST", body=task_body, headers={"Origin": base_url})
            if status != 403 or payload.get("error") != "csrf_validation_failed":
                failures.append("state-changing human request did not require CSRF")
            status, _headers, payload = request_json(
                browser,
                base_url + "/api/tasks",
                method="POST",
                body=task_body,
                headers={"X-AgentOps-CSRF": csrf_token, "Origin": base_url},
            )
            evidence["csrf_write"] = {"status": status, "task_id_present": bool(payload.get("task_id"))}
            if status not in {200, 201} or not payload.get("task_id"):
                failures.append("valid human session plus CSRF could not create a task")

            status, _headers, payload = request_json(
                browser,
                base_url + "/api/human-auth/logout",
                method="POST",
                body={},
                headers={"X-AgentOps-CSRF": csrf_token, "Origin": base_url},
            )
            if status != 200 or payload.get("authenticated") is not False:
                failures.append("human logout did not revoke the session")
            status, _headers, payload = request_json(browser, base_url + "/api/tasks")
            evidence["post_logout_read"] = {"status": status, "error": payload.get("error")}
            if status != 401:
                failures.append("revoked human session retained workspace access")
        finally:
            process.terminate()
            try:
                stdout, stderr = process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate(timeout=5)

        combined = (stdout or "") + (stderr or "")
        forbidden = ("fixture-machine-key", "fixture-admin-key", "fixture-owner-setup-code", "fixture-password-value")
        if any(value in combined for value in forbidden):
            failures.append("private host output exposed fixture credential material")

    print(
        json.dumps(
            {
                "ok": not failures,
                "operation": "human_browser_auth_smoke",
                "human_and_machine_credentials_separate": True,
                "real_runtime_called": False,
                "temporary_database": True,
                "credential_values_omitted": True,
                "evidence": evidence,
                "failures": failures,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
