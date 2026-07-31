#!/usr/bin/env python3
"""Offline contract tests for shared GitHub CI evidence parsing."""
from __future__ import annotations

import json
import http.client
from unittest.mock import patch

from github_ci_evidence import (
    extract_action_run_ids,
    fetch_url,
    parse_run_page_for_head_success,
    redact,
)


HEAD = "1bdcf5bdab3cd5656febca40cbf30efc70027275"


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
    success = parse_run_page_for_head_success(success_html, head_sha=HEAD)
    if not (success.get("head_matches") is True and success.get("status") == "completed" and success.get("conclusion") == "success"):
        failures.append(f"success parse failed: {success}")

    short_only = parse_run_page_for_head_success("<div>1bdcf5b</div><div>Status Success</div>", head_sha=HEAD)
    if short_only.get("head_matches") is not False:
        failures.append(f"short sha must not prove exact-head CI: {short_only}")

    failure_html = f"<div>{HEAD}</div><div>Status Failure</div>"
    failed = parse_run_page_for_head_success(failure_html, head_sha=HEAD)
    if not (failed.get("head_matches") is True and failed.get("conclusion") is None):
        failures.append(f"failed run must not parse as success: {failed}")

    completed_without_success_html = f"<script>{{\"headSha\":\"{HEAD}\",\"status\":\"completed\",\"conclusion\":\"failure\"}}</script>"
    completed_without_success = parse_run_page_for_head_success(completed_without_success_html, head_sha=HEAD)
    if completed_without_success.get("conclusion") == "success":
        failures.append(f"completed failure must not parse as success: {completed_without_success}")

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

    output = {
        "ok": not failures,
        "operation": "github_ci_evidence_smoke",
        "cases": {
            "dedupe_run_ids": ids,
            "success_source": "fixture_full_sha_status_success",
            "short_sha_rejected": short_only.get("head_matches") is False,
            "failed_status_rejected": failed.get("conclusion") is None,
            "completed_without_success_rejected": completed_without_success.get("conclusion") is None,
            "incomplete_http_response_failed_closed": (
                incomplete_body is None
                and "IncompleteRead" in str(incomplete_error)
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
