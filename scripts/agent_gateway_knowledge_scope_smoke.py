#!/usr/bin/env python3
"""Verify Agent Gateway knowledge search scope, provenance and spoof resistance."""
from __future__ import annotations

import datetime as dt
import hashlib
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
SECRET_PATTERNS = [
    re.compile(r"Authorization:", re.IGNORECASE),
    re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"agtok_[A-Za-z0-9_]+"),
    re.compile(r"agtsess_[A-Za-z0-9_]+"),
    re.compile(r"sk-[A-Za-z0-9]{8,}"),
    re.compile(r"ntn_[A-Za-z0-9]{8,}"),
]


def stable_hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def now_stamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d%H%M%S%f")


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


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
        with urlopen(req, timeout=45) as res:
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
        time.sleep(0.5)
    raise RuntimeError(f"server did not become ready: {last_error}")


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def leaked_secret(text: str) -> bool:
    return any(pattern.search(text) for pattern in SECRET_PATTERNS)


def insert_private_doc(db_path: Path, workspace_id: str, doc_id: str, marker: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        path = f"customer_private/{workspace_id}/{doc_id}.md"
        title = f"Private Knowledge {workspace_id}"
        content = f"# {title}\n\n{marker} belongs only to {workspace_id}."
        source_hash = stable_hash({"path": path, "content": content})
        conn.execute(
            """INSERT OR REPLACE INTO knowledge_documents(
                doc_id, workspace_id, project_id, access_level, path, title,
                category, scope, source_hash, content_summary, indexed_at, updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                doc_id,
                workspace_id,
                f"customer:{workspace_id}",
                "private",
                path,
                title,
                "customer_private",
                "project",
                source_hash,
                content,
                now,
                now,
            ),
        )
        try:
            conn.execute("DELETE FROM knowledge_fts WHERE doc_id=?", (doc_id,))
            conn.execute("INSERT INTO knowledge_fts(doc_id,path,title,content) VALUES(?,?,?,?)", (doc_id, path, title, content))
        except sqlite3.OperationalError:
            pass
        conn.commit()
    finally:
        conn.close()


def main() -> int:
    failures: list[str] = []
    outputs: list[str] = []
    stamp = now_stamp()
    workspace_a = f"ws_gateway_knowledge_a_{stamp}"
    workspace_b = f"ws_gateway_knowledge_b_{stamp}"
    marker_a = f"GatewayPrivateAlpha{stamp}"
    marker_b = f"GatewayPrivateBeta{stamp}"

    with tempfile.TemporaryDirectory(prefix="agentops-gateway-knowledge-scope-") as tmp:
        db_path = Path(tmp) / "agentops_mis.db"
        port = free_port()
        base_url = f"http://127.0.0.1:{port}"
        env = os.environ.copy()
        env["AGENTOPS_DB_PATH"] = str(db_path)
        env["AGENTOPS_SKIP_SEED_EXPORTS"] = "1"
        env.pop("AGENTOPS_API_KEY", None)
        proc = subprocess.Popen(
            [sys.executable, "server.py", "--host", "127.0.0.1", "--port", str(port), "--reset", "--serve"],
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        token_id = None
        try:
            wait_ready(base_url, proc)
            status, enrollment, raw = http_json(base_url, "/api/agent-gateway/enrollment/create", "POST", {
                "workspace_id": workspace_a,
                "agent_id": f"agt_gateway_knowledge_{stamp}",
                "name": "Agent Gateway Knowledge Scope Smoke",
                "runtime_type": "mock",
                "scopes": ["knowledge:read", "knowledge:write"],
                "ttl_days": 1,
            })
            outputs.append(json.dumps({"enrollment_status": status, "token_omitted": True}, sort_keys=True))
            require(status == 201, f"enrollment failed: {status} {enrollment}", failures)
            token = enrollment.get("token")
            token_id = enrollment.get("token_id")
            require(bool(token), f"token missing: {enrollment}", failures)

            status, indexed, raw = http_json(base_url, "/api/agent-gateway/knowledge/index", "POST", {"rebuild": True}, token=token, workspace=workspace_a)
            outputs.append(raw)
            require(status == 200, f"knowledge index failed: {status} {indexed}", failures)
            require(indexed.get("token_omitted") is True, f"index token omission missing: {indexed}", failures)

            insert_private_doc(db_path, workspace_a, "kdoc_gateway_private_a", marker_a)
            insert_private_doc(db_path, workspace_b, "kdoc_gateway_private_b", marker_b)

            status, global_search, raw = http_json(
                base_url,
                "/api/agent-gateway/knowledge/search",
                token=token,
                workspace=workspace_a,
                query={"q": "AgentOps", "limit": 8, "refresh": "true"},
            )
            outputs.append(raw)
            require(status == 200, f"global search failed: {status} {global_search}", failures)
            visibility = global_search.get("visibility") or {}
            index_state = global_search.get("index") or {}
            require(visibility.get("bound_visibility_enforced") is True, f"bound visibility not enforced: {global_search}", failures)
            require(visibility.get("visible_workspaces") == ["global", workspace_a], f"visible workspaces wrong: {visibility}", failures)
            require(index_state.get("read_only") is True, f"Gateway knowledge search should stay read-only: {index_state}", failures)
            require(index_state.get("refresh_skipped_reason") == "knowledge_read_is_non_mutating", f"refresh skip proof missing: {index_state}", failures)
            global_results = global_search.get("results") or []
            require(any(row.get("workspace_id") == "global" for row in global_results), f"global docs missing: {global_results}", failures)
            for row in global_results:
                require(row.get("workspace_id") in {"global", workspace_a}, f"unexpected workspace visible: {row}", failures)
                require(bool(row.get("retrieval_id")), f"retrieval_id missing: {row}", failures)
                require(bool(row.get("source_hash")), f"source_hash missing: {row}", failures)
                require(bool(row.get("access_level")), f"access_level missing: {row}", failures)
                require(row.get("raw_content_omitted") is True, f"raw_content_omitted missing: {row}", failures)
                require(row.get("token_omitted") is True, f"token_omitted missing: {row}", failures)

            status, own_private, raw = http_json(base_url, "/api/agent-gateway/knowledge/search", token=token, workspace=workspace_a, query={"q": marker_a, "limit": 10})
            outputs.append(raw)
            require(status == 200, f"own private search failed: {status} {own_private}", failures)
            require(any(row.get("doc_id") == "kdoc_gateway_private_a" for row in own_private.get("results") or []), f"own private doc missing: {own_private}", failures)

            status, other_private, raw = http_json(base_url, "/api/agent-gateway/knowledge/search", token=token, workspace=workspace_a, query={"q": marker_b, "limit": 10})
            outputs.append(raw)
            require(status == 200, f"other private search failed: {status} {other_private}", failures)
            require(not any(row.get("doc_id") == "kdoc_gateway_private_b" for row in other_private.get("results") or []), f"workspace B private doc leaked: {other_private}", failures)

            status, own_context, raw = http_json(
                base_url,
                "/api/agent-gateway/knowledge/context-packet",
                token=token,
                workspace=workspace_a,
                query={"q": marker_a, "limit": 5, "memory_limit": 0},
            )
            outputs.append(raw)
            own_context_blocks = own_context.get("context_blocks") or []
            require(status == 200, f"own context packet failed: {status} {own_context}", failures)
            require(own_context.get("context_available") is True, f"own context packet unavailable: {own_context}", failures)
            require(any(row.get("doc_id") == "kdoc_gateway_private_a" and marker_a in str(row.get("summary") or "") for row in own_context_blocks), f"own private context missing: {own_context}", failures)
            require(all(row.get("raw_source_body_omitted") is True for row in own_context_blocks), f"context source-body omission proof missing: {own_context}", failures)

            status, other_context, raw = http_json(
                base_url,
                "/api/agent-gateway/knowledge/context-packet",
                token=token,
                workspace=workspace_a,
                query={"q": marker_b, "limit": 5, "memory_limit": 0},
            )
            outputs.append(raw)
            require(status == 200, f"other context packet failed: {status} {other_context}", failures)
            require(not any(row.get("doc_id") == "kdoc_gateway_private_b" or marker_b in str(row.get("summary") or "") for row in other_context.get("context_blocks") or []), f"workspace B private context leaked: {other_context}", failures)

            status, spoofed, raw = http_json(base_url, "/api/agent-gateway/knowledge/search", token=token, workspace=workspace_b, query={"q": marker_b, "limit": 10})
            outputs.append(raw)
            require(status == 403, f"workspace header spoof should fail: {status} {spoofed}", failures)

            status, qs_spoofed, raw = http_json(
                base_url,
                "/api/agent-gateway/knowledge/search",
                token=token,
                workspace=workspace_a,
                query={"q": marker_b, "workspace_id": workspace_b, "limit": 10},
            )
            outputs.append(raw)
            require(status == 403, f"workspace query spoof should fail: {status} {qs_spoofed}", failures)

            status, context_spoofed, raw = http_json(
                base_url,
                "/api/agent-gateway/knowledge/context-packet",
                token=token,
                workspace=workspace_a,
                query={"q": marker_b, "workspace_id": workspace_b, "limit": 5},
            )
            outputs.append(raw)
            require(status == 403, f"context packet workspace spoof should fail: {status} {context_spoofed}", failures)
        finally:
            if token_id:
                http_json(base_url, "/api/agent-gateway/enrollment/revoke", "POST", {"token_id": token_id})
            proc.terminate()
            try:
                stdout, stderr = proc.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                stdout, stderr = proc.communicate(timeout=10)
            outputs.extend([stdout or "", stderr or ""])

    secret_leaked = leaked_secret("\n".join(outputs))
    result = {
        "ok": not failures and not secret_leaked,
        "operation": "agent_gateway_knowledge_scope_smoke",
        "workspace_a": workspace_a,
        "workspace_b": workspace_b,
        "checks": {
            "bound_visibility_enforced": True,
            "private_workspace_a_visible": True,
            "private_workspace_b_hidden": True,
            "context_packet_workspace_isolated": True,
            "context_packet_spoof_rejected": True,
            "header_query_spoof_rejected": True,
            "provenance_fields_required": True,
            "gateway_search_read_only": True,
        },
        "secret_leaked": secret_leaked,
        "failures": failures,
        "token_omitted": True,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
