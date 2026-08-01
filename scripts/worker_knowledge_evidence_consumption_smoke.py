#!/usr/bin/env python3
"""Verify agent worker execution consumes knowledge retrieval evidence safely."""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agentops_mis_cli.worker import context_packet_endpoint_missing


WORKER = ROOT / "scripts" / "agent_worker.py"
SERVER = ROOT / "server.py"
SECRET_PATTERNS = [
    re.compile(r"Authorization:", re.IGNORECASE),
    re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"agtok_[A-Za-z0-9_]+"),
    re.compile(r"agtsess_[A-Za-z0-9_]+"),
    re.compile(r"sk-[A-Za-z0-9]{8,}"),
    re.compile(r"ntn_[A-Za-z0-9]{8,}"),
]
FORBIDDEN_RAW_KEYS = {"query", "snippet", "content", "prompt", "response", "token"}


def stamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d%H%M%S%f")


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def http_json(
    base_url: str,
    path: str,
    method: str = "GET",
    body: dict | None = None,
    token: str | None = None,
    workspace: str | None = None,
    query: dict | None = None,
) -> tuple[int, dict, str]:
    url = base_url.rstrip("/") + path
    if query:
        url += "?" + urlencode({key: value for key, value in query.items() if value is not None}, doseq=True)
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if workspace:
        headers["X-AgentOps-Workspace-Id"] = workspace
    data = json.dumps(body or {}, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = Request(url, data=data, method=method, headers=headers)
    try:
        with urlopen(req, timeout=60) as res:
            raw = res.read().decode("utf-8")
            return res.status, json.loads(raw) if raw else {}, raw
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(raw), raw
        except Exception:
            return exc.code, {"raw": raw}, raw


def wait_ready(base_url: str, proc: subprocess.Popen[str]) -> None:
    deadline = time.time() + 45
    last_error = ""
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"server exited early with code {proc.returncode}")
        try:
            status, _payload, _raw = http_json(base_url, "/api/agent-gateway/status")
            if status == 200:
                return
        except URLError as exc:
            last_error = str(exc)
        time.sleep(0.4)
    raise RuntimeError(f"server did not become ready: {last_error}")


def parse_json_field(value) -> dict:
    if isinstance(value, dict):
        return value
    try:
        return json.loads(value or "{}")
    except Exception:
        return {}


def forbidden_raw_key_paths(value, prefix: str = "$") -> list[str]:
    hits: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key)
            if key_text in FORBIDDEN_RAW_KEYS:
                hits.append(f"{prefix}.{key_text}")
            hits.extend(forbidden_raw_key_paths(child, f"{prefix}.{key_text}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            hits.extend(forbidden_raw_key_paths(child, f"{prefix}[{index}]"))
    return hits


def secret_leak_labels(text: str) -> list[str]:
    labels = []
    for pattern in SECRET_PATTERNS:
        if pattern.search(text):
            labels.append(pattern.pattern)
    return labels


def approved_memory_blocks(packet: dict) -> list[dict]:
    return [
        block
        for block in (packet.get("context_blocks") or [])
        if block.get("source_type") == "approved_memory"
    ]


def run_worker(base_url: str, token: str, workspace: str, agent_id: str, task_id: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update({
        "AGENTOPS_BASE_URL": base_url,
        "AGENTOPS_WORKSPACE_ID": workspace,
        "AGENTOPS_AGENT_ID": agent_id,
        "AGENTOPS_API_KEY": token,
    })
    return subprocess.run(
        [
            sys.executable,
            str(WORKER),
            "--once",
            "--adapter",
            "mock",
            "--task-id",
            task_id,
            "--no-enforce-intake",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def db_json_field(db_path: Path, sql: str, params: tuple = ()) -> list:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(sql, params).fetchone()
    if not row:
        return []
    try:
        return json.loads(row[0] or "[]")
    except Exception:
        return []


def main() -> int:
    failures: list[str] = []
    outputs: list[str] = []
    require(
        context_packet_endpoint_missing(RuntimeError("GET /api/agent-gateway/knowledge/context-packet failed: 404 {}")),
        "legacy context endpoint absence was not recognized",
        failures,
    )
    require(
        not context_packet_endpoint_missing(RuntimeError("GET /api/agent-gateway/knowledge/context-packet failed: 500 {}")),
        "context endpoint server errors must not silently downgrade to evidence-only mode",
        failures,
    )
    suffix = stamp()
    workspace = f"ws_worker_knowledge_{suffix}"
    other_workspace = f"ws_worker_knowledge_other_{suffix}"
    agent_id = f"agt_worker_knowledge_{suffix}"
    consumer_agent_id = f"agt_worker_knowledge_consumer_{suffix}"
    other_workspace_agent_id = f"agt_worker_knowledge_other_{suffix}"
    token_id = None
    consumer_token_id = None
    other_workspace_token_id = None
    no_knowledge_token_id = None
    proc: subprocess.Popen[str] | None = None
    with tempfile.TemporaryDirectory(prefix="agentops-worker-knowledge-") as tmp:
        db_path = Path(tmp) / "agentops_worker_knowledge.db"
        port = free_port()
        base_url = f"http://127.0.0.1:{port}"
        env = os.environ.copy()
        env["AGENTOPS_DB_PATH"] = str(db_path)
        env["AGENTOPS_BASE_URL"] = base_url
        env["AGENTOPS_SKIP_SEED_EXPORTS"] = "1"
        env.pop("AGENTOPS_API_KEY", None)
        proc = subprocess.Popen(
            [sys.executable, str(SERVER), "--host", "127.0.0.1", "--port", str(port), "--reset", "--serve"],
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            wait_ready(base_url, proc)
            status, enrollment, raw = http_json(base_url, "/api/agent-gateway/enrollment/create", "POST", {
                "workspace_id": workspace,
                "agent_id": agent_id,
                "name": "Worker Knowledge Evidence Consumption Smoke",
                "runtime_type": "mock",
                "scopes": [
                    "agents:write",
                    "agents:heartbeat",
                    "agent_plans:read",
                    "agent_plans:write",
                    "plan_evidence:read",
                    "plan_evidence:write",
                    "tasks:create",
                    "tasks:read",
                    "tasks:claim",
                    "runs:write",
                    "runtime_events:write",
                    "toolcalls:write",
                    "artifacts:write",
                    "memories:propose",
                    "evaluations:submit",
                    "audit:write",
                    "knowledge:read",
                    "knowledge:write",
                ],
                "ttl_days": 1,
            })
            require(status == 201, f"enrollment failed: {status} {enrollment}", failures)
            token = enrollment.get("token")
            token_id = enrollment.get("token_id")
            require(bool(token), f"token missing: {enrollment}", failures)

            status, indexed, raw = http_json(
                base_url,
                "/api/agent-gateway/knowledge/index",
                "POST",
                {"rebuild": True},
                token=token,
                workspace=workspace,
            )
            outputs.append(raw)
            require(status == 200 and int(indexed.get("indexed") or 0) >= 20, f"knowledge index failed: {status} {indexed}", failures)

            status, task, raw = http_json(base_url, "/api/agent-gateway/tasks", "POST", {
                "workspace_id": workspace,
                "title": f"Worker knowledge evidence task {suffix}",
                "description": (
                    "The worker must use Hermes and OpenClaw Agent Gateway runbook evidence before writing the run ledger. "
                    "Compare live adapter confirm-run safety, prepared-action wall, and worker task writeback."
                ),
                "acceptance_criteria": (
                    "Tool, evaluation, audit and worker result must include safe task-aware knowledge retrieval evidence identifiers."
                ),
                "risk_level": "medium",
            }, token=token, workspace=workspace)
            outputs.append(raw)
            task_id = task.get("task_id")
            require(status == 201 and bool(task_id), f"task create failed: {status} {task}", failures)

            approved_context_marker = f"APPROVED_CONTEXT_{suffix}"
            status, proposed_memory, raw = http_json(base_url, "/api/agent-gateway/memories/propose", "POST", {
                "workspace_id": workspace,
                "agent_id": agent_id,
                "task_id": task_id,
                "scope": "project",
                "memory_type": "project_context",
                "canonical_text": (
                    f"{approved_context_marker}: workers must use bounded reviewed project context, "
                    "while tool, evaluation and audit records retain only source identifiers and hashes."
                ),
                "source_type": "manual",
                "source_ref": f"worker-context-smoke:{suffix}",
                "confidence": 0.94,
            }, token=token, workspace=workspace)
            outputs.append(raw)
            approved_memory_id = (proposed_memory.get("memory") or {}).get("memory_id")
            require(status in {200, 201} and bool(approved_memory_id), f"memory propose failed: {status} {proposed_memory}", failures)
            status, approved_memory, raw = http_json(
                base_url,
                f"/api/memories/{approved_memory_id}/approve",
                "POST",
                {},
            )
            outputs.append(raw)
            require(status == 200 and approved_memory.get("review_status") == "approved", f"memory approve failed: {status} {approved_memory}", failures)

            org_context_marker = f"APPROVED_ORG_CONTEXT_{suffix}"
            status, proposed_org_memory, raw = http_json(base_url, "/api/agent-gateway/memories/propose", "POST", {
                "workspace_id": workspace,
                "agent_id": agent_id,
                "task_id": task_id,
                "scope": "org",
                "memory_type": "policy",
                "canonical_text": (
                    f"{org_context_marker}: all workers in this workspace must consume only human-approved "
                    "organizational memory and retain bounded provenance identifiers."
                ),
                "source_type": "manual",
                "source_ref": f"worker-org-context-smoke:{suffix}",
                "confidence": 0.96,
            }, token=token, workspace=workspace)
            outputs.append(raw)
            approved_org_memory_id = (proposed_org_memory.get("memory") or {}).get("memory_id")
            require(status in {200, 201} and bool(approved_org_memory_id), f"org memory propose failed: {status} {proposed_org_memory}", failures)
            require((proposed_org_memory.get("memory") or {}).get("workspace_id") == workspace, f"org memory workspace binding missing: {proposed_org_memory}", failures)
            status, approved_org_memory, raw = http_json(
                base_url,
                f"/api/memories/{approved_org_memory_id}/approve",
                "POST",
                {},
            )
            outputs.append(raw)
            require(status == 200 and approved_org_memory.get("review_status") == "approved", f"org memory approve failed: {status} {approved_org_memory}", failures)
            require(approved_org_memory.get("workspace_id") == workspace, f"approved org memory workspace binding missing: {approved_org_memory}", failures)

            candidate_context_marker = f"CANDIDATE_CONTEXT_MUST_NOT_SHARE_{suffix}"
            status, proposed_candidate_memory, raw = http_json(base_url, "/api/agent-gateway/memories/propose", "POST", {
                "workspace_id": workspace,
                "agent_id": agent_id,
                "task_id": task_id,
                "scope": "project",
                "memory_type": "project_context",
                "canonical_text": (
                    f"{candidate_context_marker}: this unreviewed candidate must remain private to review "
                    "and must never enter another Agent Session Context Packet."
                ),
                "source_type": "manual",
                "source_ref": f"worker-candidate-context-smoke:{suffix}",
                "confidence": 0.91,
            }, token=token, workspace=workspace)
            outputs.append(raw)
            candidate_memory = proposed_candidate_memory.get("memory") or {}
            candidate_memory_id = candidate_memory.get("memory_id")
            require(status in {200, 201} and bool(candidate_memory_id), f"candidate memory propose failed: {status} {proposed_candidate_memory}", failures)
            require(candidate_memory.get("review_status") == "candidate", f"candidate memory status changed before review: {candidate_memory}", failures)
            require(candidate_memory.get("workspace_id") == workspace, f"candidate memory workspace binding missing: {candidate_memory}", failures)

            require((proposed_memory.get("memory") or {}).get("workspace_id") == workspace, f"project memory workspace binding missing: {proposed_memory}", failures)
            require(approved_memory.get("workspace_id") == workspace, f"approved project memory workspace binding missing: {approved_memory}", failures)
            approved_workspace_memory_ids = {approved_memory_id, approved_org_memory_id}

            consumer_parent_scopes = ["agents:write", "tasks:create", "tasks:read", "knowledge:read", "memories:propose"]
            status, consumer_enrollment, _raw = http_json(base_url, "/api/agent-gateway/enrollment/create", "POST", {
                "workspace_id": workspace,
                "agent_id": consumer_agent_id,
                "name": "Worker Knowledge Context Consumer Smoke",
                "runtime_type": "mock",
                "scopes": consumer_parent_scopes,
                "ttl_days": 1,
            })
            require(status == 201, f"consumer enrollment failed: {status} {consumer_enrollment}", failures)
            consumer_token = consumer_enrollment.get("token")
            consumer_token_id = consumer_enrollment.get("token_id")
            require(bool(consumer_token), f"consumer token missing: {consumer_enrollment}", failures)

            consumer_task_terms = f"{approved_context_marker} {org_context_marker} {candidate_context_marker}"
            status, consumer_task, raw = http_json(base_url, "/api/agent-gateway/tasks", "POST", {
                "workspace_id": workspace,
                "title": f"Consume reviewed workspace memory {suffix}",
                "description": f"Use reviewed project and org context matching {consumer_task_terms}.",
                "acceptance_criteria": "Another scoped Agent Session receives approved workspace memory but no candidate memory.",
                "risk_level": "low",
            }, token=consumer_token, workspace=workspace)
            outputs.append(raw)
            consumer_task_id = consumer_task.get("task_id")
            require(status == 201 and bool(consumer_task_id), f"consumer task create failed: {status} {consumer_task}", failures)

            status, foreign_task_proposal, raw = http_json(base_url, "/api/agent-gateway/memories/propose", "POST", {
                "workspace_id": workspace,
                "agent_id": consumer_agent_id,
                "task_id": task_id,
                "scope": "task",
                "memory_type": "project_context",
                "canonical_text": "A different Agent must not attach Memory to a task it cannot access.",
                "source_type": "manual",
            }, token=consumer_token, workspace=workspace)
            outputs.append(raw)
            require(status == 403 and foreign_task_proposal.get("error") == "forbidden", f"another agent attached memory to a foreign task: {status} {foreign_task_proposal}", failures)

            status, consumer_session, _raw = http_json(base_url, "/api/agent-gateway/session/create", "POST", {
                "ttl_sec": 300,
                "scopes": ["tasks:read", "knowledge:read"],
            }, token=consumer_token)
            require(status == 201, f"consumer session create failed: {status} {consumer_session}", failures)
            consumer_session_token = consumer_session.get("session_token")
            require(bool(consumer_session_token), f"consumer session token missing: {consumer_session}", failures)
            require(set(consumer_session.get("scopes") or []) == {"tasks:read", "knowledge:read"}, f"consumer session was not narrowly scoped: {consumer_session}", failures)

            status, candidate_conflict, raw = http_json(base_url, "/api/agent-gateway/memories/propose", "POST", {
                "workspace_id": workspace,
                "agent_id": consumer_agent_id,
                "memory_id": candidate_memory_id,
                "scope": "project",
                "memory_type": "project_context",
                "canonical_text": "Another agent must not overwrite an existing candidate memory identifier.",
                "source_type": "manual",
            }, token=consumer_token, workspace=workspace)
            outputs.append(raw)
            require(status == 409 and candidate_conflict.get("error") == "memory_id_conflict", f"another agent overwrote a candidate memory: {status} {candidate_conflict}", failures)

            status, consumer_context, raw = http_json(
                base_url,
                "/api/agent-gateway/knowledge/context-packet",
                token=consumer_session_token,
                workspace=workspace,
                query={"task_id": consumer_task_id, "adapter": "mock", "limit": 5, "memory_limit": 5},
            )
            outputs.append(raw)
            consumer_memory_blocks = approved_memory_blocks(consumer_context)
            consumer_memory_ids = {block.get("memory_id") for block in consumer_memory_blocks}
            consumer_context_text = "\n".join(str(block.get("summary") or "") for block in consumer_memory_blocks)
            require(status == 200 and consumer_context.get("operation") == "knowledge_project_context_packet", f"consumer context packet failed: {status} {consumer_context}", failures)
            require(consumer_context.get("workspace_id") == workspace, f"consumer context workspace mismatch: {consumer_context}", failures)
            require((consumer_context.get("task_context") or {}).get("task_id") == consumer_task_id, f"consumer context was not bound to the second task: {consumer_context}", failures)
            require(approved_workspace_memory_ids.issubset(consumer_memory_ids), f"approved project/org memory was not shared within workspace: {consumer_memory_blocks}", failures)
            require(approved_context_marker in consumer_context_text and org_context_marker in consumer_context_text, f"approved workspace memory summaries missing: {consumer_memory_blocks}", failures)
            require(candidate_memory_id not in consumer_memory_ids and candidate_context_marker not in consumer_context_text, f"candidate memory leaked to another agent/task: {consumer_memory_blocks}", failures)
            require(all("source_ref" not in block and block.get("source_ref_omitted") is True for block in consumer_memory_blocks), f"consumer context leaked memory source refs: {consumer_memory_blocks}", failures)
            consumer_context_safety = consumer_context.get("safety") or {}
            require(consumer_context_safety.get("raw_prompt_omitted") is True and consumer_context_safety.get("raw_response_omitted") is True, f"consumer context prompt/response omission missing: {consumer_context_safety}", failures)
            require(consumer_context_safety.get("raw_transcript_omitted") is True and consumer_context_safety.get("token_omitted") is True, f"consumer context transcript/token omission missing: {consumer_context_safety}", failures)

            status, other_enrollment, _raw = http_json(base_url, "/api/agent-gateway/enrollment/create", "POST", {
                "workspace_id": other_workspace,
                "agent_id": other_workspace_agent_id,
                "name": "Worker Knowledge Foreign Workspace Smoke",
                "runtime_type": "mock",
                "scopes": consumer_parent_scopes,
                "ttl_days": 1,
            })
            require(status == 201, f"other-workspace enrollment failed: {status} {other_enrollment}", failures)
            other_workspace_token = other_enrollment.get("token")
            other_workspace_token_id = other_enrollment.get("token_id")
            require(bool(other_workspace_token), f"other-workspace token missing: {other_enrollment}", failures)

            status, other_task, raw = http_json(base_url, "/api/agent-gateway/tasks", "POST", {
                "workspace_id": other_workspace,
                "title": f"Reject foreign workspace memory {suffix}",
                "description": f"Attempt retrieval for foreign markers {consumer_task_terms} without crossing workspace authority.",
                "acceptance_criteria": "No project, org, or candidate memory from another workspace is visible.",
                "risk_level": "low",
            }, token=other_workspace_token, workspace=other_workspace)
            outputs.append(raw)
            other_task_id = other_task.get("task_id")
            require(status == 201 and bool(other_task_id), f"other-workspace task create failed: {status} {other_task}", failures)

            status, cross_workspace_conflict, raw = http_json(base_url, "/api/agent-gateway/memories/propose", "POST", {
                "workspace_id": other_workspace,
                "agent_id": other_workspace_agent_id,
                "memory_id": approved_memory_id,
                "scope": "project",
                "memory_type": "project_context",
                "canonical_text": "A foreign workspace must not overwrite an approved memory identifier.",
                "source_type": "manual",
            }, token=other_workspace_token, workspace=other_workspace)
            outputs.append(raw)
            require(status == 409 and cross_workspace_conflict.get("error") == "memory_id_conflict", f"foreign workspace overwrote an approved memory: {status} {cross_workspace_conflict}", failures)

            status, other_session, _raw = http_json(base_url, "/api/agent-gateway/session/create", "POST", {
                "ttl_sec": 300,
                "scopes": ["tasks:read", "knowledge:read"],
            }, token=other_workspace_token)
            require(status == 201, f"other-workspace session create failed: {status} {other_session}", failures)
            other_session_token = other_session.get("session_token")
            require(bool(other_session_token), f"other-workspace session token missing: {other_session}", failures)

            status, other_context, raw = http_json(
                base_url,
                "/api/agent-gateway/knowledge/context-packet",
                token=other_session_token,
                workspace=other_workspace,
                query={"task_id": other_task_id, "adapter": "mock", "limit": 5, "memory_limit": 5},
            )
            outputs.append(raw)
            other_memory_blocks = approved_memory_blocks(other_context)
            other_memory_ids = {block.get("memory_id") for block in other_memory_blocks}
            other_context_text = "\n".join(str(block.get("summary") or "") for block in other_memory_blocks)
            require(status == 200 and other_context.get("workspace_id") == other_workspace, f"other-workspace context failed: {status} {other_context}", failures)
            require(not approved_workspace_memory_ids.intersection(other_memory_ids), f"approved memory crossed workspace boundary: {other_memory_blocks}", failures)
            require(candidate_memory_id not in other_memory_ids, f"candidate memory crossed workspace boundary: {other_memory_blocks}", failures)
            require(all(marker not in other_context_text for marker in (approved_context_marker, org_context_marker, candidate_context_marker)), f"foreign workspace memory summary leaked: {other_memory_blocks}", failures)
            require(all("source_ref" not in block for block in other_context.get("context_blocks") or []), f"other-workspace context leaked source refs: {other_context}", failures)
            other_context_safety = other_context.get("safety") or {}
            require(other_context_safety.get("raw_prompt_omitted") is True and other_context_safety.get("raw_response_omitted") is True, f"other-workspace prompt/response omission missing: {other_context_safety}", failures)
            require(other_context_safety.get("raw_transcript_omitted") is True and other_context_safety.get("token_omitted") is True, f"other-workspace transcript/token omission missing: {other_context_safety}", failures)

            status, context_packet, raw = http_json(
                base_url,
                "/api/agent-gateway/knowledge/context-packet",
                token=token,
                workspace=workspace,
                query={"task_id": task_id, "adapter": "mock", "limit": 5, "memory_limit": 3},
            )
            outputs.append(raw)
            context_blocks = context_packet.get("context_blocks") or []
            require(status == 200 and context_packet.get("operation") == "knowledge_project_context_packet", f"context packet failed: {status} {context_packet}", failures)
            require(context_packet.get("version") == "v1", f"context packet version missing: {context_packet}", failures)
            require(context_packet.get("context_available", True) is not False and int(context_packet.get("context_block_count") or 0) > 0, f"context packet empty: {context_packet}", failures)
            require(bool(context_packet.get("packet_hash")), f"context packet hash missing: {context_packet}", failures)
            require(any(block.get("source_type") == "knowledge_summary" and block.get("summary") for block in context_blocks), f"versioned knowledge summary missing: {context_blocks}", failures)
            require(any(block.get("source_type") == "approved_memory" and block.get("memory_id") == approved_memory_id and approved_context_marker in str(block.get("summary") or "") for block in context_blocks), f"approved memory summary missing: {context_blocks}", failures)
            require(any(block.get("source_type") == "approved_memory" and block.get("memory_id") == approved_org_memory_id and org_context_marker in str(block.get("summary") or "") for block in context_blocks), f"approved org memory summary missing: {context_blocks}", failures)
            require(not any(block.get("memory_id") == candidate_memory_id or candidate_context_marker in str(block.get("summary") or "") for block in context_blocks), f"candidate memory entered source context packet: {context_blocks}", failures)
            require(int(context_packet.get("context_chars") or 0) <= int((context_packet.get("limits") or {}).get("total_chars") or 0), f"context packet exceeded total bound: {context_packet}", failures)
            context_safety = context_packet.get("safety") or {}
            require(context_safety.get("read_only") is True and context_safety.get("raw_transcript_omitted") is True, f"context safety proof missing: {context_safety}", failures)
            require(all("source_ref" not in block for block in context_blocks), f"context packet leaked source refs: {context_blocks}", failures)

            worker = run_worker(base_url, token, workspace, agent_id, task_id or "")
            outputs.extend([worker.stdout, worker.stderr])
            require(worker.returncode == 0, f"worker failed: {worker.returncode} {worker.stderr or worker.stdout}", failures)
            worker_payload = json.loads(worker.stdout or "{}")
            result = next((row for row in worker_payload.get("results") or [] if row.get("processed")), {})
            run_id = result.get("run_id")
            plan_id = result.get("plan_id")
            worker_evidence = result.get("knowledge_retrieval_evidence") or {}
            require(bool(run_id and plan_id), f"worker result missing run/plan: {worker_payload}", failures)
            require(worker_evidence.get("consumed") is True, f"worker result did not consume evidence: {worker_evidence}", failures)
            require(worker_evidence.get("context_consumed") is True, f"worker result did not consume bounded context: {worker_evidence}", failures)
            require(worker_evidence.get("context_contract_version") == "v1", f"worker context contract missing: {worker_evidence}", failures)
            require(bool(worker_evidence.get("context_packet_hash")), f"worker context packet hash missing: {worker_evidence}", failures)
            require(int(worker_evidence.get("context_block_count") or 0) > 0, f"worker context block count missing: {worker_evidence}", failures)
            require(approved_memory_id in (worker_evidence.get("approved_memory_ids") or []), f"worker context missing approved memory id: {worker_evidence}", failures)
            require(bool(worker_evidence.get("packet_hash")), f"worker result missing packet hash: {worker_evidence}", failures)
            require(bool(worker_evidence.get("query_hash")), f"worker result missing query hash: {worker_evidence}", failures)
            require(worker_evidence.get("query_omitted") is True, f"worker result query should be omitted: {worker_evidence}", failures)
            require(worker_evidence.get("raw_content_omitted") is True, f"worker result raw content should be omitted: {worker_evidence}", failures)
            worker_task_context = worker_evidence.get("task_context") or {}
            require(worker_task_context.get("task_id") == task_id, f"worker evidence missing task-bound context: {worker_task_context}", failures)
            require(worker_task_context.get("query_source") == "task_id", f"worker evidence should use task_id packet: {worker_task_context}", failures)
            require(worker_task_context.get("task_text_omitted") is True, f"worker evidence should omit task text: {worker_task_context}", failures)
            task_relevant_path_fragments = [
                "README.md",
                "AGENT_WORKFLOW.md",
                "BASE_INDEX.md",
                "REMOTE_WORKER_OPERATIONS_RUNBOOK",
                "AGENT_GATEWAY_CLI_SPEC",
            ]
            retrieved_paths = [str(path or "") for path in (worker_evidence.get("paths") or [])]
            require(
                any(fragment in path for path in retrieved_paths for fragment in task_relevant_path_fragments),
                f"task-aware query did not retrieve Hermes/OpenClaw/Gateway runbook/spec paths: {retrieved_paths}",
                failures,
            )

            status, run_detail, raw = http_json(base_url, f"/api/runs/{run_id}")
            outputs.append(raw)
            require(status == 200, f"run detail failed: {status} {run_detail}", failures)
            tool = next((item for item in (run_detail.get("tool_calls") or []) if item.get("tool_name") == "agent_worker.mock"), {})
            tool_args = parse_json_field(tool.get("normalized_args_json"))
            eval_row = (run_detail.get("evaluations") or [{}])[0]
            rubric = parse_json_field(eval_row.get("rubric_json") or eval_row.get("rubric"))
            require(tool_args.get("knowledge_retrieval_evidence_consumed") is True, f"tool args missing consumption proof: {tool_args}", failures)
            require(tool_args.get("knowledge_context_consumed") is True, f"tool args missing context consumption proof: {tool_args}", failures)
            require(tool_args.get("knowledge_context_contract_version") == "v1", f"tool args missing context contract: {tool_args}", failures)
            require(bool(tool_args.get("knowledge_context_packet_hash")), f"tool args missing context packet hash: {tool_args}", failures)
            require(int(tool_args.get("knowledge_context_block_count") or 0) > 0, f"tool args missing context block count: {tool_args}", failures)
            require(approved_memory_id in (tool_args.get("knowledge_context_approved_memory_ids") or []), f"tool args missing approved memory id: {tool_args}", failures)
            require(bool(tool_args.get("knowledge_retrieval_packet_hash")), f"tool args missing packet hash: {tool_args}", failures)
            require(bool(tool_args.get("knowledge_retrieval_query_hash")), f"tool args missing query hash: {tool_args}", failures)
            tool_task_context = tool_args.get("knowledge_retrieval_task_context") or {}
            require(tool_task_context.get("task_id") == task_id, f"tool args missing task context: {tool_task_context}", failures)
            require(tool_task_context.get("task_text_omitted") is True, f"tool task context should omit text: {tool_task_context}", failures)
            require((tool_args.get("knowledge_retrieval_omissions") or {}).get("raw_prompt_omitted") is True, f"tool args omission proof missing: {tool_args}", failures)
            require(rubric.get("knowledge_retrieval_evidence_consumed") is True, f"eval rubric missing consumption proof: {rubric}", failures)
            require(rubric.get("requires_project_context_packet") is True and rubric.get("knowledge_context_consumed") is True, f"eval rubric missing context quality gate: {rubric}", failures)
            require(bool(rubric.get("knowledge_retrieval_packet_hash")), f"eval rubric missing packet hash: {rubric}", failures)
            rubric_task_context = rubric.get("knowledge_retrieval_task_context") or {}
            require(rubric_task_context.get("task_id") == task_id, f"eval rubric missing task context: {rubric_task_context}", failures)

            status, audit_page, raw = http_json(base_url, "/api/audit", query={"limit": 120})
            outputs.append(raw)
            audit_rows = audit_page if isinstance(audit_page, list) else audit_page.get("audit_logs") or audit_page.get("items") or []
            audit_match = next((item for item in audit_rows if item.get("action") == "agent_worker.task_processed" and item.get("entity_id") == run_id), {})
            audit_meta = parse_json_field(audit_match.get("metadata_json"))
            require(audit_meta.get("knowledge_retrieval_evidence_consumed") is True, f"audit metadata missing consumption proof: {audit_meta}", failures)
            require(audit_meta.get("knowledge_context_consumed") is True, f"audit metadata missing context consumption proof: {audit_meta}", failures)
            require(bool(audit_meta.get("knowledge_context_packet_hash")), f"audit metadata missing context packet hash: {audit_meta}", failures)
            require(bool(audit_meta.get("knowledge_retrieval_packet_hash")), f"audit metadata missing packet hash: {audit_meta}", failures)
            audit_task_context = audit_meta.get("knowledge_retrieval_task_context") or {}
            require(audit_task_context.get("task_id") == task_id, f"audit metadata missing task context: {audit_task_context}", failures)

            status, evidence_report, raw = http_json(
                base_url,
                "/api/operator/evidence-report",
                workspace=workspace,
                query={"run_id": run_id, "limit": 5},
            )
            outputs.append(raw)
            report_item = (evidence_report.get("runs") or [{}])[0]
            report_knowledge = report_item.get("worker_knowledge_retrieval") or {}
            report_summary = evidence_report.get("summary") or {}
            report_checks = {item.get("id"): item for item in report_item.get("checks") or []}
            require(status == 200 and evidence_report.get("operation") == "operator_evidence_report", f"evidence report failed: {status} {evidence_report}", failures)
            require(report_knowledge.get("status") == "ready", f"evidence report missing worker knowledge readiness: {report_knowledge}", failures)
            require(report_knowledge.get("consumed_tool_calls") == 1, f"evidence report consumed count wrong: {report_knowledge}", failures)
            require(bool(report_knowledge.get("packet_hashes")), f"evidence report missing packet hashes: {report_knowledge}", failures)
            require(bool(report_knowledge.get("context_packet_hashes")), f"evidence report missing context packet hashes: {report_knowledge}", failures)
            require(int(report_knowledge.get("context_block_count") or 0) > 0, f"evidence report missing context block count: {report_knowledge}", failures)
            require(approved_memory_id in (report_knowledge.get("approved_memory_ids") or []), f"evidence report missing approved memory id: {report_knowledge}", failures)
            require(bool(report_knowledge.get("query_hashes")), f"evidence report missing query hashes: {report_knowledge}", failures)
            require(report_knowledge.get("raw_query_omitted") is True, f"evidence report raw query omission missing: {report_knowledge}", failures)
            require(report_knowledge.get("raw_content_omitted") is True, f"evidence report raw content omission missing: {report_knowledge}", failures)
            require((report_checks.get("worker_knowledge_retrieval") or {}).get("ok") is True, f"evidence report quality gate did not pass: {report_checks}", failures)
            require(int(report_summary.get("worker_runs") or 0) >= 1, f"evidence summary missing worker run count: {report_summary}", failures)
            require(int(report_summary.get("worker_knowledge_retrieval_ready") or 0) >= 1, f"evidence summary missing worker knowledge ready count: {report_summary}", failures)
            require(int(report_summary.get("worker_knowledge_retrieval_missing") or 0) == 0, f"evidence summary should not report missing worker knowledge: {report_summary}", failures)

            referenced_specs = db_json_field(db_path, "SELECT referenced_specs_json FROM agent_plans WHERE plan_id=?", (plan_id,))
            referenced_memories = db_json_field(db_path, "SELECT referenced_memories_json FROM agent_plans WHERE plan_id=?", (plan_id,))
            require(len(referenced_specs) >= 3, f"plan missing referenced specs: {referenced_specs}", failures)
            require(any(path in referenced_memories for path in worker_evidence.get("paths") or []), f"plan did not reference retrieved knowledge paths: {referenced_memories} evidence={worker_evidence}", failures)
            require(approved_memory_id in referenced_memories, f"plan did not reference approved memory authority: {referenced_memories}", failures)

            persisted_context_text = "\n".join([
                json.dumps(tool_args, ensure_ascii=False),
                json.dumps(rubric, ensure_ascii=False),
                json.dumps(audit_meta, ensure_ascii=False),
                json.dumps(worker_evidence, ensure_ascii=False),
            ])
            require(approved_context_marker not in persisted_context_text, "bounded context body leaked into worker ledger evidence", failures)

            scoped_payload = {
                "worker_evidence": worker_evidence,
                "tool_args": tool_args,
                "rubric": rubric,
                "audit_meta": audit_meta,
                "report_knowledge": report_knowledge,
                "report_summary": report_summary,
                "task_context": worker_task_context,
            }
            raw_key_hits = forbidden_raw_key_paths(scoped_payload)
            require(not raw_key_hits, f"raw fields leaked in evidence metadata: {raw_key_hits}", failures)
            leak_scope_text = "\n".join([
                worker.stdout,
                worker.stderr,
                json.dumps(scoped_payload, ensure_ascii=False),
                json.dumps(run_detail, ensure_ascii=False),
                json.dumps(audit_match, ensure_ascii=False),
            ])
            leak_labels = secret_leak_labels(leak_scope_text)
            require(not leak_labels, f"worker knowledge evidence smoke leaked token-like material categories: {leak_labels}", failures)

            no_knowledge_agent_id = f"agt_worker_no_knowledge_{suffix}"
            status, no_knowledge_enrollment, raw = http_json(base_url, "/api/agent-gateway/enrollment/create", "POST", {
                "workspace_id": workspace,
                "agent_id": no_knowledge_agent_id,
                "name": "Worker Missing Knowledge Evidence Gate Smoke",
                "runtime_type": "mock",
                "scopes": [
                    "agents:write",
                    "agents:heartbeat",
                    "agent_plans:read",
                    "agent_plans:write",
                    "plan_evidence:read",
                    "plan_evidence:write",
                    "tasks:create",
                    "tasks:read",
                    "tasks:claim",
                    "runs:write",
                    "runtime_events:write",
                    "toolcalls:write",
                    "artifacts:write",
                    "memories:propose",
                    "evaluations:submit",
                    "audit:write",
                ],
                "ttl_days": 1,
            })
            require(status == 201, f"no-knowledge enrollment failed: {status} {no_knowledge_enrollment}", failures)
            no_knowledge_token = no_knowledge_enrollment.get("token")
            no_knowledge_token_id = no_knowledge_enrollment.get("token_id")
            require(bool(no_knowledge_token), f"no-knowledge token missing: {no_knowledge_enrollment}", failures)

            status, missing_task, raw = http_json(base_url, "/api/agent-gateway/tasks", "POST", {
                "workspace_id": workspace,
                "title": f"Worker missing knowledge evidence task {suffix}",
                "description": "The worker can execute, but missing knowledge:read scope must fail the knowledge retrieval quality gate.",
                "acceptance_criteria": "Run/tool/audit evidence is written, while evaluation and operator evidence mark retrieval evidence unavailable.",
                "risk_level": "medium",
            }, token=no_knowledge_token, workspace=workspace)
            outputs.append(raw)
            missing_task_id = missing_task.get("task_id")
            require(status == 201 and bool(missing_task_id), f"missing-knowledge task create failed: {status} {missing_task}", failures)

            missing_worker = run_worker(base_url, no_knowledge_token or "", workspace, no_knowledge_agent_id, missing_task_id or "")
            outputs.extend([missing_worker.stdout, missing_worker.stderr])
            require(missing_worker.returncode == 0, f"missing-knowledge worker failed: {missing_worker.returncode} {missing_worker.stderr or missing_worker.stdout}", failures)
            missing_worker_payload = json.loads(missing_worker.stdout or "{}")
            missing_result = next((row for row in missing_worker_payload.get("results") or [] if row.get("processed")), {})
            missing_run_id = missing_result.get("run_id")
            missing_worker_evidence = missing_result.get("knowledge_retrieval_evidence") or {}
            require(bool(missing_run_id), f"missing-knowledge worker result missing run: {missing_worker_payload}", failures)
            require(missing_result.get("ok") is True, f"missing-knowledge adapter should still complete: {missing_result}", failures)
            require(missing_worker_evidence.get("consumed") is False, f"missing-knowledge evidence should not be consumed: {missing_worker_evidence}", failures)
            require(missing_worker_evidence.get("context_consumed") is False, f"missing-knowledge context should not be consumed: {missing_worker_evidence}", failures)
            require(missing_worker_evidence.get("status") == "unavailable", f"missing-knowledge evidence should be unavailable: {missing_worker_evidence}", failures)
            require(missing_worker_evidence.get("raw_prompt_omitted") is True, f"missing-knowledge raw prompt omission missing: {missing_worker_evidence}", failures)
            missing_task_context = missing_worker_evidence.get("task_context") or {}
            require(missing_task_context.get("task_id") == missing_task_id, f"missing-knowledge evidence missing task context: {missing_task_context}", failures)
            require(missing_task_context.get("task_text_omitted") is True, f"missing-knowledge task context should omit text: {missing_task_context}", failures)
            require(missing_result.get("plan_evidence_pass") is not True, f"missing-knowledge manifest should not pass quality gate: {missing_result}", failures)

            status, missing_run_detail, raw = http_json(base_url, f"/api/runs/{missing_run_id}")
            outputs.append(raw)
            require(status == 200, f"missing-knowledge run detail failed: {status} {missing_run_detail}", failures)
            missing_tool = next((item for item in (missing_run_detail.get("tool_calls") or []) if item.get("tool_name") == "agent_worker.mock"), {})
            missing_tool_args = parse_json_field(missing_tool.get("normalized_args_json"))
            missing_eval = (missing_run_detail.get("evaluations") or [{}])[0]
            missing_rubric = parse_json_field(missing_eval.get("rubric_json") or missing_eval.get("rubric"))
            require(missing_tool_args.get("knowledge_retrieval_evidence_consumed") is False, f"missing-knowledge tool args should show no consumption: {missing_tool_args}", failures)
            require(missing_tool_args.get("knowledge_retrieval_status") == "unavailable", f"missing-knowledge tool status should be unavailable: {missing_tool_args}", failures)
            require(missing_eval.get("pass_fail") == "fail", f"missing-knowledge evaluation should fail: {missing_eval}", failures)
            require(missing_rubric.get("requires_knowledge_retrieval_evidence") is True, f"missing-knowledge rubric missing required evidence flag: {missing_rubric}", failures)
            require(missing_rubric.get("knowledge_retrieval_gate_pass") is False, f"missing-knowledge rubric should fail gate: {missing_rubric}", failures)
            require(missing_rubric.get("knowledge_retrieval_gate_status") == "unavailable", f"missing-knowledge rubric wrong status: {missing_rubric}", failures)
            require(missing_rubric.get("quality_gate_pass") is False, f"missing-knowledge quality gate should fail: {missing_rubric}", failures)

            status, missing_report, raw = http_json(
                base_url,
                "/api/operator/evidence-report",
                workspace=workspace,
                query={"run_id": missing_run_id, "limit": 5},
            )
            outputs.append(raw)
            missing_report_item = (missing_report.get("runs") or [{}])[0]
            missing_report_knowledge = missing_report_item.get("worker_knowledge_retrieval") or {}
            missing_report_summary = missing_report.get("summary") or {}
            missing_report_checks = {item.get("id"): item for item in missing_report_item.get("checks") or []}
            require(status == 200 and missing_report.get("operation") == "operator_evidence_report", f"missing-knowledge evidence report failed: {status} {missing_report}", failures)
            require(missing_report.get("status") in {"attention", "blocked"}, f"missing-knowledge report should not be ready: {missing_report}", failures)
            require(missing_report_knowledge.get("status") == "unavailable", f"missing-knowledge report should mark unavailable: {missing_report_knowledge}", failures)
            require((missing_report_checks.get("worker_knowledge_retrieval") or {}).get("ok") is False, f"missing-knowledge report quality gate should fail: {missing_report_checks}", failures)
            require(int(missing_report_summary.get("worker_knowledge_retrieval_unavailable") or 0) >= 1, f"missing-knowledge summary missing unavailable count: {missing_report_summary}", failures)

            missing_scoped_payload = {
                "worker_evidence": missing_worker_evidence,
                "tool_args": missing_tool_args,
                "rubric": missing_rubric,
                "report_knowledge": missing_report_knowledge,
                "report_summary": missing_report_summary,
                "task_context": missing_task_context,
            }
            missing_raw_key_hits = forbidden_raw_key_paths(missing_scoped_payload)
            require(not missing_raw_key_hits, f"raw fields leaked in missing-knowledge metadata: {missing_raw_key_hits}", failures)
            missing_leak_scope_text = "\n".join([
                missing_worker.stdout,
                missing_worker.stderr,
                json.dumps(missing_scoped_payload, ensure_ascii=False),
                json.dumps(missing_run_detail, ensure_ascii=False),
            ])
            missing_leak_labels = secret_leak_labels(missing_leak_scope_text)
            require(not missing_leak_labels, f"missing-knowledge smoke leaked token-like material categories: {missing_leak_labels}", failures)
        except Exception as exc:
            failures.append(f"unexpected exception: {type(exc).__name__}: {exc}")
        finally:
            if other_workspace_token_id:
                try:
                    http_json(base_url, "/api/agent-gateway/enrollment/revoke", "POST", {"token_id": other_workspace_token_id})
                except Exception:
                    pass
            if consumer_token_id:
                try:
                    http_json(base_url, "/api/agent-gateway/enrollment/revoke", "POST", {"token_id": consumer_token_id})
                except Exception:
                    pass
            if no_knowledge_token_id:
                try:
                    http_json(base_url, "/api/agent-gateway/enrollment/revoke", "POST", {"token_id": no_knowledge_token_id})
                except Exception:
                    pass
            if token_id:
                try:
                    http_json(base_url, "/api/agent-gateway/enrollment/revoke", "POST", {"token_id": token_id})
                except Exception:
                    pass
            if proc:
                proc.terminate()
                try:
                    out, err = proc.communicate(timeout=8)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    out, err = proc.communicate(timeout=8)
                outputs.extend([out or "", err or ""])
    result = {
        "ok": not failures,
        "operation": "worker_knowledge_evidence_consumption_smoke",
        "workspace_id": workspace,
        "other_workspace_id": other_workspace,
        "agent_id": agent_id,
        "consumer_agent_id": consumer_agent_id,
        "workspace_memory_contract": {
            "approved_project_org_shared_with_scoped_session": not failures,
            "candidate_not_shared": not failures,
            "cross_workspace_hidden": not failures,
        },
        "failures": failures,
        "token_omitted": True,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
