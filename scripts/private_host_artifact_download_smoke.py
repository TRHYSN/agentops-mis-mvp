#!/usr/bin/env python3
"""Verify approved artifact downloads use bounded ledger data and human auth."""
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


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def request_json(opener, url: str, *, method="GET", body=None, headers=None) -> tuple[int, dict, dict]:
    payload = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        method=method,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        with opener.open(request, timeout=5) as response:
            return response.status, dict(response.headers), json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), json.loads(exc.read().decode("utf-8"))


def request_download(opener, url: str) -> tuple[int, dict, bytes]:
    request = urllib.request.Request(url, method="GET")
    try:
        with opener.open(request, timeout=5) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def seed_artifacts(db_path: Path, arbitrary_file: Path) -> None:
    stamp = "2026-07-12T00:00:00+00:00"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """INSERT INTO agents(agent_id,name,role,description,runtime_type,model_provider,model_name,status,
                   permission_level,allowed_tools,budget_limit_usd,owner_user_id,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("agt_artifact_download_smoke", "Artifact download smoke", "delivery", "Offline fixture", "mock",
             "offline", "fixture", "idle", "standard", "[]", 0, None, stamp, stamp),
        )
        for suffix in ("approved", "pending"):
            task_id = f"tsk_artifact_download_{suffix}"
            run_id = f"run_artifact_download_{suffix}"
            artifact_id = f"art_artifact_download_{suffix}"
            conn.execute(
                """INSERT INTO tasks(task_id,workspace_id,title,description,requester_id,owner_agent_id,
                       collaborator_agent_ids,status,priority,due_date,acceptance_criteria,risk_level,
                       budget_limit_usd,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (task_id, "local-demo", f"Artifact {suffix}", "Bounded fixture", None,
                 "agt_artifact_download_smoke", "[]", "completed", "medium", None,
                 "Approved delivery only", "low", 0, stamp, stamp),
            )
            conn.execute(
                """INSERT INTO runs(run_id,workspace_id,task_id,agent_id,runtime_type,status,started_at,ended_at,
                       input_summary,output_summary,approval_required,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (run_id, "local-demo", task_id, "agt_artifact_download_smoke", "mock", "completed", stamp, stamp,
                 "RAW_PROMPT_MUST_NOT_LEAK", "RAW_RESPONSE_MUST_NOT_LEAK", 1, stamp),
            )
            summary = "Approved bounded delivery summary. api_key=fixture-artifact-secret-value " + ("A" * 5000)
            conn.execute(
                """INSERT INTO artifacts(artifact_id,task_id,run_id,artifact_type,title,uri,summary,created_at)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (artifact_id, task_id, run_id, "customer_delivery_report", "Customer delivery\r\nunsafe-name",
                 arbitrary_file.as_uri(), summary, stamp),
            )
            conn.execute(
                """INSERT INTO approvals(approval_id,task_id,run_id,tool_call_id,requested_by_agent_id,
                       approver_user_id,decision,reason,expires_at,created_at,decided_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (f"ap_artifact_download_{suffix}", task_id, run_id, None, "agt_artifact_download_smoke", None,
                 "approved" if suffix == "approved" else "pending", "Fixture delivery gate", None, stamp,
                 stamp if suffix == "approved" else None),
            )


def main() -> int:
    failures: list[str] = []
    evidence: dict[str, object] = {}
    fixture_secrets = (
        "fixture-artifact-download-machine-key",
        "fixture-artifact-download-admin-key",
        "fixture-artifact-download-setup-code",
        "fixture-artifact-download-password",
        "fixture-artifact-secret-value",
    )
    with tempfile.TemporaryDirectory(prefix="agentops-private-artifact-download-") as temporary:
        temp = Path(temporary)
        db_path = temp / "agentops_mis.db"
        arbitrary_file = temp / "must-not-be-read.txt"
        arbitrary_marker = "ARBITRARY_FILE_CONTENT_MUST_NOT_LEAK"
        arbitrary_file.write_text(arbitrary_marker, encoding="utf-8")
        port = free_port()
        base_url = f"http://127.0.0.1:{port}"
        env = {
            **os.environ,
            "AGENTOPS_DB_PATH": str(db_path),
            "AGENTOPS_SKIP_SEED_EXPORTS": "1",
            "AGENTOPS_DEPLOYMENT_MODE": "private_host",
            "AGENTOPS_HUMAN_AUTH_REQUIRED": "true",
            "AGENTOPS_COOKIE_SECURE": "false",
            "AGENTOPS_API_KEY": fixture_secrets[0],
            "AGENTOPS_ADMIN_KEY": fixture_secrets[1],
            "AGENTOPS_OWNER_SETUP_CODE": fixture_secrets[2],
            "AGENTOPS_ALLOWED_ORIGINS": base_url,
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
        browser = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        anonymous = urllib.request.build_opener()
        stdout = stderr = ""
        try:
            deadline = time.time() + 30
            while time.time() < deadline:
                if process.poll() is not None:
                    break
                try:
                    status, _headers, health = request_json(anonymous, base_url + "/health")
                    if status == 200 and health.get("status") == "ready":
                        break
                except (OSError, ValueError, urllib.error.URLError):
                    time.sleep(0.2)
            else:
                failures.append("Private Host fixture did not become ready")

            seed_artifacts(db_path, arbitrary_file)
            approved_path = "/api/artifacts/art_artifact_download_approved/download"
            status, _headers, payload = request_json(anonymous, base_url + approved_path)
            evidence["anonymous"] = {"status": status, "error": payload.get("error")}
            if status != 401 or payload.get("error") != "human_auth_required":
                failures.append("anonymous artifact download did not fail closed")

            status, _headers, auth_payload = request_json(
                browser,
                base_url + "/api/human-auth/bootstrap",
                method="POST",
                body={
                    "setup_code": fixture_secrets[2],
                    "username": "artifact-owner",
                    "display_name": "Artifact Owner",
                    "password": fixture_secrets[3],
                },
                headers={"Origin": base_url},
            )
            if status != 201 or not auth_payload.get("authenticated"):
                failures.append("Private Host Owner session was not created")

            status, headers, markdown = request_download(browser, base_url + approved_path)
            markdown_text = markdown.decode("utf-8", errors="replace")
            evidence["markdown"] = {
                "status": status,
                "content_type": headers.get("Content-Type"),
                "content_disposition": headers.get("Content-Disposition"),
                "size_bytes": len(markdown),
            }
            if status != 200 or not headers.get("Content-Type", "").startswith("text/markdown"):
                failures.append("approved Markdown artifact download failed")
            if headers.get("Content-Disposition") != 'attachment; filename="artifact-art_artifact_download_approved.md"':
                failures.append("artifact download filename was not bounded and deterministic")

            status, headers, json_bytes = request_download(browser, base_url + approved_path + "?format=json")
            json_document = json.loads(json_bytes.decode("utf-8")) if status == 200 else {}
            evidence["json"] = {
                "status": status,
                "content_type": headers.get("Content-Type"),
                "artifact_id": json_document.get("artifact_id"),
                "uri_read": (json_document.get("content_boundary") or {}).get("artifact_uri_read"),
            }
            if status != 200 or json_document.get("artifact_id") != "art_artifact_download_approved":
                failures.append("approved JSON artifact download failed")

            combined_download = markdown_text + json_bytes.decode("utf-8", errors="replace")
            forbidden_download_values = (
                arbitrary_marker,
                arbitrary_file.as_uri(),
                "RAW_PROMPT_MUST_NOT_LEAK",
                "RAW_RESPONSE_MUST_NOT_LEAK",
                fixture_secrets[4],
            )
            if any(value in combined_download for value in forbidden_download_values):
                failures.append("artifact download exposed URI content, raw run content, or secret material")
            if len(markdown) > 12000 or len(json_bytes) > 12000:
                failures.append("artifact download was not bounded")

            status, _headers, denied = request_json(
                browser,
                base_url + "/api/artifacts/art_artifact_download_pending/download",
            )
            evidence["unapproved"] = {"status": status, "error": denied.get("error")}
            if status != 403 or denied.get("error") != "artifact_not_approved":
                failures.append("unapproved artifact download did not fail closed")

            status, _headers, missing = request_json(
                browser,
                base_url + "/api/artifacts/art_artifact_download_missing/download",
            )
            evidence["missing"] = {"status": status, "error": missing.get("error")}
            if status != 404 or missing.get("error") != "artifact_not_found":
                failures.append("missing artifact download did not fail closed")

            status, _headers, traversal = request_json(
                browser,
                base_url + "/api/artifacts/%2E%2E%2Fmust-not-be-read/download",
            )
            evidence["path_traversal"] = {"status": status, "error": traversal.get("error")}
            if status != 404 or traversal.get("error") != "artifact_not_found":
                failures.append("path traversal artifact id did not fail closed")

            with sqlite3.connect(db_path) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT actor_type,actor_id,action,entity_id,metadata_json FROM audit_logs "
                    "WHERE action='artifact.download' AND entity_id=? ORDER BY created_at",
                    ("art_artifact_download_approved",),
                ).fetchall()
            metadata = [json.loads(row["metadata_json"]) for row in rows]
            evidence["audit"] = {
                "count": len(rows),
                "human_actor": all(row["actor_type"] == "user" and row["actor_id"] for row in rows),
                "uri_not_read": all(item.get("artifact_uri_read") is False for item in metadata),
                "raw_content_omitted": all(item.get("raw_content_omitted") is True for item in metadata),
            }
            if len(rows) != 2 or not all(evidence["audit"].values()):
                failures.append("successful artifact downloads did not write bounded human audit records")
        except (OSError, RuntimeError, ValueError, sqlite3.DatabaseError, urllib.error.URLError) as exc:
            failures.append(f"artifact download smoke exception: {type(exc).__name__}: {str(exc)[:180]}")
        finally:
            process.terminate()
            try:
                stdout, stderr = process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate(timeout=5)

        combined_logs = (stdout or "") + (stderr or "")
        if any(value in combined_logs for value in fixture_secrets):
            failures.append("Private Host artifact download logs exposed fixture credentials")

    print(json.dumps({
        "ok": not failures,
        "operation": "private_host_artifact_download_smoke",
        "temporary_database": True,
        "human_session_required": True,
        "real_runtime_called": False,
        "arbitrary_uri_read": False,
        "credential_values_omitted": True,
        "evidence": evidence,
        "failures": failures,
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
