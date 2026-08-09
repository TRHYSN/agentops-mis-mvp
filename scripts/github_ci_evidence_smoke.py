#!/usr/bin/env python3
"""Offline contract tests for shared GitHub CI evidence parsing."""
from __future__ import annotations

import json
import http.client
import os
import subprocess
from pathlib import Path
from unittest.mock import patch

from github_ci_evidence import (
    BYOC_COMPOSE_REUSABLE_JOB,
    BYOC_COMPOSE_WORKFLOW,
    BYOC_CROSS_SCHEMA_WORKFLOW,
    BYOC_CUSTOMER_RELEASE_WORKFLOW,
    MAIN_CI_WORKFLOW,
    ci_from_gh,
    ci_status,
    commercial_workflow_evidence,
    extract_action_run_ids,
    fetch_url,
    parse_run_page_for_head_success,
    redact,
    run as run_command,
)


HEAD = "1bdcf5bdab3cd5656febca40cbf30efc70027275"
REGRESSION_HEAD = "87ee537a8c1470dc625d3e8118db27d18cfb6f11"


class IncompleteResponse:
    def __enter__(self) -> "IncompleteResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        raise http.client.IncompleteRead(b"partial", 12)


def main() -> int:
    failures: list[str] = []
    actions_html = """
    <a href="/geogejoy107-jpg/agentops-mis-mvp/actions/runs/28034175584">run</a>
    <a href="/geogejoy107-jpg/agentops-mis-mvp/actions/runs/28034175584">duplicate</a>
    <a href="/geogejoy107-jpg/agentops-mis-mvp/actions/runs/28034111999">older</a>
    """
    ids = extract_action_run_ids(actions_html)
    if ids != ["28034175584", "28034111999"]:
        failures.append(f"unexpected run ids: {ids}")

    success_html = f"""
    <html>
      <title>AgentOps MIS CI</title>
      <body>
        <span>commit {HEAD}</span>
        <div>Status Success</div>
      </body>
    </html>
    """
    success = parse_run_page_for_head_success(
        success_html,
        head_sha=HEAD,
        workflow_name=MAIN_CI_WORKFLOW,
    )
    if not (success.get("head_matches") is True and success.get("status") == "completed" and success.get("conclusion") == "success"):
        failures.append(f"success parse failed: {success}")

    short_only = parse_run_page_for_head_success(
        f"<div>{MAIN_CI_WORKFLOW}</div><div>1bdcf5b</div><div>Status Success</div>",
        head_sha=HEAD,
    )
    if short_only.get("head_matches") is not False:
        failures.append(f"short sha must not prove exact-head CI: {short_only}")

    failure_html = f"<title>{MAIN_CI_WORKFLOW}</title><div>{HEAD}</div><div>Status Failure</div>"
    failed = parse_run_page_for_head_success(failure_html, head_sha=HEAD)
    if not (failed.get("head_matches") is True and failed.get("conclusion") is None):
        failures.append(f"failed run must not parse as success: {failed}")

    completed_without_success_html = (
        f"<title>{MAIN_CI_WORKFLOW}</title>"
        f'<script>{{"headSha":"{HEAD}","status":"completed","conclusion":"failure"}}</script>'
    )
    completed_without_success = parse_run_page_for_head_success(completed_without_success_html, head_sha=HEAD)
    if completed_without_success.get("conclusion") == "success":
        failures.append(f"completed failure must not parse as success: {completed_without_success}")

    cross_schema_success_html = f"""
    <title>{BYOC_CROSS_SCHEMA_WORKFLOW}</title>
    <span>{HEAD}</span>
    <div>Status Success</div>
    """
    wrong_workflow = parse_run_page_for_head_success(
        cross_schema_success_html,
        head_sha=HEAD,
        workflow_name=MAIN_CI_WORKFLOW,
    )
    if wrong_workflow.get("head_matches") is not False:
        failures.append(f"wrong workflow must not prove main CI: {wrong_workflow}")
    sidebar_only_main = parse_run_page_for_head_success(
        f"<title>{BYOC_CROSS_SCHEMA_WORKFLOW}</title><aside>{MAIN_CI_WORKFLOW}</aside>"
        f"<span>{HEAD}</span><div>Status Success</div>",
        head_sha=HEAD,
        workflow_name=MAIN_CI_WORKFLOW,
    )
    if sidebar_only_main.get("head_matches") is not False:
        failures.append(f"sidebar workflow text must not prove main CI: {sidebar_only_main}")

    regression_runs = [
        {
            "conclusion": "success",
            "createdAt": "2026-07-31T12:02:54Z",
            "databaseId": 30629180752,
            "headSha": REGRESSION_HEAD,
            "name": BYOC_CROSS_SCHEMA_WORKFLOW,
            "status": "completed",
            "url": "https://github.com/example/repo/actions/runs/30629180752",
            "workflowName": BYOC_CROSS_SCHEMA_WORKFLOW,
        },
        {
            "conclusion": "cancelled",
            "createdAt": "2026-07-31T12:02:54Z",
            "databaseId": 30629180908,
            "headSha": REGRESSION_HEAD,
            "name": MAIN_CI_WORKFLOW,
            "status": "completed",
            "url": "https://github.com/example/repo/actions/runs/30629180908",
            "workflowName": MAIN_CI_WORKFLOW,
        },
        {
            "conclusion": "success",
            "createdAt": "2026-07-31T12:03:54Z",
            "databaseId": 30629181001,
            "headSha": REGRESSION_HEAD,
            "name": BYOC_CUSTOMER_RELEASE_WORKFLOW,
            "status": "completed",
            "url": "https://github.com/example/repo/actions/runs/30629181001",
            "workflowName": BYOC_CUSTOMER_RELEASE_WORKFLOW,
        },
    ]
    parent_view = {
        "conclusion": "cancelled",
        "createdAt": "2026-07-31T12:02:54Z",
        "databaseId": 30629180908,
        "headSha": REGRESSION_HEAD,
        "jobs": [
            {
                "conclusion": "failure",
                "databaseId": 91151385123,
                "name": BYOC_COMPOSE_REUSABLE_JOB,
                "status": "completed",
                "url": "https://github.com/example/repo/actions/runs/30629180908/job/91151385123",
            }
        ],
        "status": "completed",
        "url": "https://github.com/example/repo/actions/runs/30629180908",
        "workflowName": MAIN_CI_WORKFLOW,
    }

    def fake_gh_run(_root: Path, args: list[str], *, timeout: int = 15) -> subprocess.CompletedProcess[str]:
        del timeout
        if args[1:3] == ["run", "list"]:
            return subprocess.CompletedProcess(args, 0, json.dumps(regression_runs), "")
        if args[1:3] == ["run", "view"]:
            return subprocess.CompletedProcess(args, 0, json.dumps(parent_view), "")
        return subprocess.CompletedProcess(args, 1, "", "unexpected gh command")

    with (
        patch.dict(os.environ, {"GITHUB_ACTIONS": "false"}, clear=False),
        patch("github_ci_evidence.shutil.which", return_value="/usr/bin/gh"),
        patch("github_ci_evidence.run", side_effect=fake_gh_run),
        patch(
            "github_ci_evidence.ci_from_public_html",
            return_value={
                "source": "github_public_html",
                "status": "not_found_for_head",
                "conclusion": None,
                "head_matches": False,
            },
        ),
    ):
        selected_main = ci_from_gh(Path.cwd(), REGRESSION_HEAD, "commercial", workflow_name=MAIN_CI_WORKFLOW)
        default_main = ci_status(Path.cwd(), REGRESSION_HEAD, "commercial")
        workflow_evidence = commercial_workflow_evidence(Path.cwd(), REGRESSION_HEAD, "commercial")

    if selected_main.get("workflow") != MAIN_CI_WORKFLOW or selected_main.get("conclusion") != "cancelled":
        failures.append(f"87ee537 main CI selector regression: {selected_main}")
    if default_main.get("workflow") != MAIN_CI_WORKFLOW or default_main.get("conclusion") != "cancelled":
        failures.append(f"87ee537 default ci_status regression: {default_main}")
    regression_evidence = workflow_evidence["evidence"]
    if regression_evidence["agentops_mis_ci"].get("conclusion") != "cancelled":
        failures.append(f"87ee537 main CI must remain cancelled: {regression_evidence}")
    if regression_evidence["byoc_compose"].get("conclusion") != "failure":
        failures.append(f"87ee537 Compose reusable job must remain failed: {regression_evidence}")
    if regression_evidence["byoc_cross_schema_v9_to_v11"].get("conclusion") != "success":
        failures.append(f"87ee537 cross-schema evidence must remain independently successful: {regression_evidence}")
    if regression_evidence["byoc_customer_release"].get("conclusion") != "success":
        failures.append(f"87ee537 customer release evidence must remain independently successful: {regression_evidence}")
    if workflow_evidence.get("ready") is not False:
        failures.append(f"87ee537 must not be promotion-ready: {workflow_evidence}")

    redacted = redact("Authorization: placeholder-token-value")
    if "Authorization:" in redacted:
        failures.append("redaction did not remove token-like material")

    with patch(
        "github_ci_evidence.urllib.request.urlopen",
        return_value=IncompleteResponse(),
    ):
        incomplete_body, incomplete_error = fetch_url("https://example.invalid")
    if incomplete_body is not None or "IncompleteRead" not in str(incomplete_error):
        failures.append(
            f"incomplete HTTP response did not fail closed: {incomplete_error!r}"
        )

    with patch(
        "github_ci_evidence.subprocess.run",
        side_effect=subprocess.TimeoutExpired(["gh", "run", "list"], 15),
    ):
        timed_out = run_command(Path.cwd(), ["gh", "run", "list"], timeout=15)
    if timed_out.returncode != 124 or timed_out.stderr != "command_timeout_after_15s":
        failures.append(f"command timeout did not fail closed: {timed_out}")

    output = {
        "ok": not failures,
        "operation": "github_ci_evidence_smoke",
        "cases": {
            "dedupe_run_ids": ids,
            "success_source": "fixture_full_sha_status_success",
            "short_sha_rejected": short_only.get("head_matches") is False,
            "failed_status_rejected": failed.get("conclusion") is None,
            "completed_without_success_rejected": completed_without_success.get("conclusion") is None,
            "wrong_workflow_rejected": wrong_workflow.get("head_matches") is False,
            "sidebar_workflow_text_rejected": sidebar_only_main.get("head_matches") is False,
            "host_github_actions_env_isolated": True,
            "regression_87ee537": {
                "agentops_mis_ci": regression_evidence["agentops_mis_ci"].get("conclusion"),
                "byoc_compose": regression_evidence["byoc_compose"].get("conclusion"),
                "byoc_cross_schema_v9_to_v11": regression_evidence["byoc_cross_schema_v9_to_v11"].get("conclusion"),
                "byoc_customer_release": regression_evidence["byoc_customer_release"].get("conclusion"),
                "promotion_ready": workflow_evidence.get("ready"),
            },
            "incomplete_http_response_failed_closed": (
                incomplete_body is None
                and "IncompleteRead" in str(incomplete_error)
            ),
            "command_timeout_failed_closed": (
                timed_out.returncode == 124
                and timed_out.stderr == "command_timeout_after_15s"
            ),
        },
        "safety": {
            "network_performed": False,
            "read_only": True,
            "token_omitted": True,
        },
        "failures": failures,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
