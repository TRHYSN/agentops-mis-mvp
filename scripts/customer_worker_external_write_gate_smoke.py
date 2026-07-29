#!/usr/bin/env python3
"""Verify opaque live customer worker external writes create a prepared-action gate before execution."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import server  # noqa: E402
from agentops_mis_cli import agentops as cli  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def leaked_secret(text: str) -> bool:
    patterns = [
        re.compile(r"Authorization:", re.IGNORECASE),
        re.compile(r"Bearer\s+(?!\[REDACTED\])[A-Za-z0-9._~+/=-]+", re.IGNORECASE),
        re.compile(r"AGENTOPS_API_KEY\s*=\s*['\"]?(?!<paste|\\[REDACTED\\])[A-Za-z0-9._~+/=-]+", re.IGNORECASE),
        re.compile(r"agtok_[A-Za-z0-9_-]{16,}"),
        re.compile(r"agtsess_[A-Za-z0-9_-]{16,}"),
        re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
        re.compile(r"ntn_[A-Za-z0-9_-]{8,}"),
    ]
    return any(pattern.search(text) for pattern in patterns)


def main() -> int:
    try:
        classifier_cases = [
            (
                False,
                {},
                "本地只读验收",
                "不得发布任何内容，也不使用外部连接器。",
                "不要上传或发送数据，只返回本地摘要。",
                "Chinese negated task",
            ),
            (
                False,
                {},
                "Read-only acceptance",
                "Do not publish, send, upload, or perform external writes.",
                "Return a bounded local summary only.",
                "English negated task",
            ),
            (
                False,
                {},
                "Read-only acceptance",
                "This task does not publish, send, upload, or deploy anything.",
                "The worker should not perform an external write.",
                "English third-person negated task",
            ),
            (
                False,
                {},
                "Read-only acceptance",
                "Without publishing or uploading, return a local summary.",
                "No external write is allowed.",
                "English tight negation chain",
            ),
            (
                False,
                {},
                "本地只读验收",
                "无需发布或上传，只返回本地摘要。",
                "不进行外部写入。",
                "Chinese tight negation chain",
            ),
            (
                False,
                {},
                "Read-only integration review",
                "Return evidence without modifying files or using external tools.",
                "Summarize the connector status notionally.",
                "ASCII connector names require word boundaries",
            ),
            (
                True,
                {},
                "Customer delivery",
                "Publish the approved report to the customer portal.",
                "Record the delivery receipt.",
                "English publish intent",
            ),
            (
                True,
                {},
                "客户交付",
                "将批准版本发送并上传到客户门户。",
                "记录外部写入回执。",
                "Chinese send and upload intent",
            ),
            (
                True,
                {},
                "Dify dataset delivery",
                "Upload the approved document to Dify datasets.",
                "Record the webhook receipt after sending.",
                "ASCII connector and inflected action intent",
            ),
            (
                True,
                {},
                "Mixed instruction",
                "Do not upload drafts, but publish the approved release.",
                "Record the delivery receipt.",
                "English later positive intent",
            ),
            (
                True,
                {},
                "混合指令",
                "不得上传草稿；随后发布批准版本。",
                "记录交付回执。",
                "Chinese later positive intent",
            ),
            (
                True,
                {},
                "Unsafe English instruction",
                "Without approval, publish the report.",
                "Record the delivery receipt.",
                "without approval does not negate publish",
            ),
            (
                True,
                {},
                "不安全中文指令",
                "无需审批即可发布报告。",
                "记录交付回执。",
                "无需审批 does not negate publish",
            ),
            (
                True,
                {"external_write_intent": False},
                "Customer delivery",
                "Upload the approved artifact.",
                "Record the delivery receipt.",
                "explicit false cannot suppress positive prose",
            ),
            (
                True,
                {
                    "external_write_intent": False,
                    "external_action_type": "customer.portal.publish",
                },
                "Local summary",
                "Do not publish from task prose.",
                "Return a summary.",
                "structured external action remains fail closed",
            ),
            (
                True,
                {"external_write_intent": {"unexpected": True}},
                "Local summary",
                "Return a bounded local summary.",
                "No connector action is requested.",
                "malformed explicit intent fails closed",
            ),
            (
                True,
                {"target_resource": "http://localhost.evil.example/write"},
                "Local summary",
                "Return a bounded local summary.",
                "No connector action is requested.",
                "localhost suffix is not loopback",
            ),
            (
                True,
                {"target_resource": "http://127.0.0.1.evil.example/write"},
                "Local summary",
                "Return a bounded local summary.",
                "No connector action is requested.",
                "loopback-looking suffix is not loopback",
            ),
            (
                False,
                {"target_resource": "http://127.0.0.1:8787/local"},
                "Local summary",
                "Return a bounded local summary.",
                "No connector action is requested.",
                "exact IPv4 loopback remains local",
            ),
            (
                False,
                {"target_resource": "http://[::1]:8787/local"},
                "Local summary",
                "Return a bounded local summary.",
                "No connector action is requested.",
                "exact IPv6 loopback remains local",
            ),
        ]
        for expected, body, title, description, acceptance, label in classifier_cases:
            actual = server.customer_worker_external_write_intent(body, title, description, acceptance)
            require(actual is expected, f"{label} classified as {actual}, expected {expected}")

        require(
            server.tool_call_has_external_side_effect_intent(
                "local.read", "read", "http://localhost.evil.example/write", {}
            ) is True,
            "tool-call gate accepted a localhost-suffix external target",
        )
        require(
            server.tool_call_has_external_side_effect_intent(
                "local.read", "read", "http://127.0.0.1:8787/local", {}
            ) is False,
            "tool-call gate rejected an exact IPv4 loopback read",
        )
        require(
            server.tool_call_has_external_side_effect_intent(
                "local.read", "read", None, {"url": "http://127.0.0.1.evil.example/write"}
            ) is True,
            "tool-call gate accepted a loopback-looking explicit target",
        )

        class FakeClient:
            workspace_id = "local-demo"
            agent_id = "agt_fake"

            def __init__(self) -> None:
                self.endpoint = None
                self.payload = None

            def post(self, endpoint: str, payload: dict) -> dict:
                self.endpoint = endpoint
                self.payload = payload
                return {"ok": True, "endpoint": endpoint, "payload": payload}

        parser = cli.build_parser()
        args = parser.parse_args([
            "workflow",
            "customer-worker-task",
            "--adapter",
            "hermes",
            "--confirm-run",
            "--external-write-intent",
            "--target-resource",
            "mock://customer-portal/delivery",
            "--external-action-type",
            "customer.portal.publish",
            "--approval-reason",
            "Human review required.",
            "--title",
            "Publish customer delivery through Hermes",
            "--description",
            "Prepare a customer portal update.",
        ])
        fake_client = FakeClient()
        cli.cmd_workflow_customer_worker_task(args, fake_client)
        require(fake_client.endpoint == "/api/workflows/customer-worker-task", "CLI used wrong endpoint")
        require(fake_client.payload and fake_client.payload.get("external_write_intent") is True, "CLI omitted external_write_intent")
        require(fake_client.payload.get("target_resource") == "mock://customer-portal/delivery", "CLI omitted target_resource")
        require(fake_client.payload.get("external_action_type") == "customer.portal.publish", "CLI omitted external_action_type")
        require(fake_client.payload.get("approval_reason") == "Human review required.", "CLI omitted approval_reason")

        with server.db() as conn:
            server.refresh_runtime_connectors(conn)
            conn.execute(
                "UPDATE runtime_connectors SET trust_status='trusted', trust_note=NULL WHERE runtime_connector_id='rtc_hermes_default_gateway'"
            )
            payload, status = server.run_customer_worker_task_workflow(conn, {
                "adapter": "hermes",
                "confirm_run": True,
                "external_write_intent": True,
                "external_action_type": "customer.portal.publish",
                "target_resource": "mock://customer-portal/delivery",
                "title": "Publish customer delivery through Hermes",
                "description": "Use the live Hermes worker to publish the delivery artifact to an external customer portal.",
                "acceptance_criteria": "Do not execute the live runtime until the exact external publish action is prepared and approved.",
                "risk_level": "medium",
            })
            task = conn.execute("SELECT * FROM tasks WHERE task_id=?", (payload.get("task_id"),)).fetchone()
            run = conn.execute("SELECT * FROM runs WHERE run_id=?", (payload.get("run_id"),)).fetchone()
            tool = conn.execute("SELECT * FROM tool_calls WHERE tool_call_id=?", (payload.get("tool_call_id"),)).fetchone()
            prepared = conn.execute("SELECT * FROM prepared_actions WHERE action_id=?", (payload.get("prepared_action_id"),)).fetchone()
            approval = conn.execute("SELECT * FROM approvals WHERE approval_id=?", (payload.get("approval_id"),)).fetchone()
            runtime_events = conn.execute(
                "SELECT COUNT(*) c FROM runtime_events WHERE run_id=? AND event_type='customer_worker_task.external_write_prepared_action_required'",
                (payload.get("run_id"),),
            ).fetchone()["c"]
            audit_rows = conn.execute(
                "SELECT COUNT(*) c FROM audit_logs WHERE entity_id=? AND action='workflow.customer_worker_task.external_write_prepared_action_required'",
                (payload.get("run_id"),),
            ).fetchone()["c"]
        require(status == 202, f"expected 202, got {status}: {payload}")
        require(payload.get("reason") == "external_write_prepared_action_required", f"wrong reason: {payload}")
        require(payload.get("live_execution_performed") is False, f"live runtime should not execute: {payload}")
        require(task is not None and task["status"] == "waiting_approval", f"task gate missing: {payload}")
        require(run is not None and run["status"] == "waiting_approval" and run["approval_required"] == 1, f"run gate missing: {payload}")
        require(tool is not None and tool["status"] == "waiting_approval" and tool["risk_level"] in {"high", "critical"}, f"tool gate missing: {payload}")
        require(prepared is not None and prepared["status"] == "prepared", f"prepared action missing: {payload}")
        require(approval is not None and approval["decision"] == "pending", f"approval missing: {payload}")
        require("approval prepared-action resume" in (payload.get("next_action") or ""), f"next action missing resume: {payload}")
        require(runtime_events >= 1, "runtime event evidence missing")
        require(audit_rows >= 1, "audit evidence missing")
        serialized = json.dumps(payload, ensure_ascii=False)
        require(not leaked_secret(serialized), "payload leaked token-like material")
        print(json.dumps({
            "ok": True,
            "status": status,
            "task_id": payload.get("task_id"),
            "run_id": payload.get("run_id"),
            "tool_call_id": payload.get("tool_call_id"),
            "prepared_action_id": payload.get("prepared_action_id"),
            "approval_id": payload.get("approval_id"),
            "cli_payload_checked": True,
            "live_execution_performed": False,
            "secret_leaked": False,
        }, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2, sort_keys=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
