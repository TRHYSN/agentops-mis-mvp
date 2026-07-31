#!/usr/bin/env python3
"""Shared read-only GitHub CI evidence helpers for release gates."""
from __future__ import annotations

import html
import http.client
import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


MAIN_CI_WORKFLOW = "AgentOps MIS CI"
BYOC_COMPOSE_WORKFLOW = "BYOC Docker Compose Acceptance"
BYOC_COMPOSE_REUSABLE_JOB = (
    "BYOC Docker Compose acceptance / "
    "Clean install, restore drill, and same-schema image lifecycle"
)
BYOC_CROSS_SCHEMA_WORKFLOW = "BYOC Cross-Schema v9 to v11 Acceptance"


SECRET_PATTERNS = [
    re.compile(r"Authorization:", re.IGNORECASE),
    re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"agtok_[A-Za-z0-9_]+"),
    re.compile(r"agtsess_[A-Za-z0-9_]+"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"ntn_[A-Za-z0-9]{8,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]+"),
    re.compile(r"gh[opsu]_[A-Za-z0-9_]+"),
]


def redact(text: str) -> str:
    redacted = text
    for pattern in SECRET_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


def run(root: Path, args: list[str], *, timeout: int = 15) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=root, capture_output=True, text=True, timeout=timeout, check=False)


def ci_from_env(head_sha: str, *, workflow_name: str = MAIN_CI_WORKFLOW) -> dict[str, Any] | None:
    if os.environ.get("GITHUB_ACTIONS", "").lower() != "true":
        return None
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    server_url = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    github_sha = os.environ.get("GITHUB_SHA", "")
    github_workflow = os.environ.get("GITHUB_WORKFLOW", "")
    if not repo or not run_id or github_workflow != workflow_name:
        return None
    return {
        "source": "github_actions_env",
        "status": "in_progress",
        "conclusion": None,
        "url": f"{server_url.rstrip('/')}/{repo}/actions/runs/{run_id}",
        "head_sha": github_sha,
        "head_matches": github_sha == head_sha,
        "workflow": github_workflow,
    }


def ci_from_gh(
    root: Path,
    head_sha: str,
    branch: str,
    *,
    workflow_name: str = MAIN_CI_WORKFLOW,
) -> dict[str, Any]:
    gh = shutil.which("gh")
    if not gh:
        return {
            "source": "gh_unavailable",
            "status": "not_available",
            "conclusion": None,
            "head_matches": False,
            "workflow": workflow_name,
        }
    proc = run(
        root,
        [
            gh,
            "run",
            "list",
            "--branch",
            branch,
            "--workflow",
            workflow_name,
            "--limit",
            "50",
            "--json",
            "databaseId,status,conclusion,url,headSha,workflowName,createdAt,name",
        ],
        timeout=15,
    )
    if proc.returncode != 0:
        return {
            "source": "gh_error",
            "status": "not_available",
            "conclusion": None,
            "head_matches": False,
            "workflow": workflow_name,
            "error": redact((proc.stderr or proc.stdout or "gh command failed").strip()),
        }
    try:
        runs = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        return {
            "source": "gh_parse_error",
            "status": "not_available",
            "conclusion": None,
            "head_matches": False,
            "workflow": workflow_name,
        }
    exact = [
        item
        for item in runs
        if item.get("headSha") == head_sha
        and (item.get("workflowName") or item.get("name")) == workflow_name
    ]
    if not exact:
        return {
            "source": "gh_run_list",
            "status": "not_found_for_head",
            "conclusion": None,
            "head_matches": False,
            "recent_runs_checked": len(runs),
            "workflow": workflow_name,
        }
    selected = max(exact, key=lambda item: str(item.get("createdAt") or ""))
    return {
        "source": "gh_run_list",
        "status": selected.get("status") or "unknown",
        "conclusion": selected.get("conclusion") or None,
        "url": selected.get("url"),
        "head_sha": selected.get("headSha"),
        "head_matches": True,
        "workflow": selected.get("workflowName") or selected.get("name"),
        "created_at": selected.get("createdAt"),
        "database_id": selected.get("databaseId"),
        "exact_workflow_runs_found": len(exact),
    }


def repo_from_git_remote(root: Path) -> str | None:
    proc = run(root, ["git", "remote", "get-url", "origin"], timeout=10)
    if proc.returncode != 0:
        return os.environ.get("GITHUB_REPOSITORY")
    remote = (proc.stdout or "").strip()
    patterns = [
        r"github\.com[:/]([^/\s]+/[^/\s]+?)(?:\.git)?$",
        r"https://github\.com/([^/\s]+/[^/\s]+?)(?:\.git)?$",
    ]
    for pattern in patterns:
        match = re.search(pattern, remote)
        if match:
            return match.group(1)
    return os.environ.get("GITHUB_REPOSITORY")


def fetch_url(url: str, *, timeout: int = 20) -> tuple[str | None, str | None]:
    headers = {
        "Accept": "text/html,application/xhtml+xml",
        "User-Agent": "agentops-mis-ci-evidence-smoke",
    }
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return res.read().decode("utf-8", errors="replace"), None
    except (
        urllib.error.URLError,
        TimeoutError,
        OSError,
        http.client.HTTPException,
    ) as exc:
        return None, redact(str(exc))


def extract_action_run_ids(page_html: str) -> list[str]:
    ids: list[str] = []
    for match in re.finditer(r"/actions/runs/([0-9]+)", page_html):
        run_id = match.group(1)
        if run_id not in ids:
            ids.append(run_id)
    return ids


def parse_run_page_for_head_success(
    run_html: str,
    *,
    head_sha: str,
    workflow_name: str = MAIN_CI_WORKFLOW,
) -> dict[str, Any]:
    text = html.unescape(run_html)
    escaped_workflow = re.escape(workflow_name)
    workflow_identity_patterns = [
        rf"<title[^>]*>[^<]*{escaped_workflow}[^<]*</title>",
        rf'["\']workflowName["\']\s*:\s*["\']{escaped_workflow}["\']',
    ]
    workflow_matches = any(
        re.search(pattern, text, re.IGNORECASE)
        for pattern in workflow_identity_patterns
    )
    if head_sha not in text or not workflow_matches:
        return {
            "status": "not_found_for_head",
            "conclusion": None,
            "head_matches": False,
            "head_sha": None,
            "workflow": workflow_name,
        }
    success_patterns = [
        r"Status\s+Success\b",
        r"\bconclusion[\"']?\s*[:=]\s*[\"']success[\"']",
    ]
    success = any(re.search(pattern, text, re.IGNORECASE) for pattern in success_patterns)
    return {
        "status": "completed" if success else "unknown",
        "conclusion": "success" if success else None,
        "head_matches": True,
        "head_sha": head_sha,
        "workflow": workflow_name,
    }


def ci_from_public_html(
    root: Path,
    head_sha: str,
    branch: str,
    *,
    repo: str | None = None,
    workflow_name: str = MAIN_CI_WORKFLOW,
) -> dict[str, Any]:
    repo = repo or repo_from_git_remote(root)
    if not repo:
        return {"source": "github_public_html", "status": "not_available", "conclusion": None, "head_matches": False, "error": "repo_unavailable"}
    query = urllib.parse.quote(f'branch:{branch} workflow:"{workflow_name}"', safe="")
    actions_url = f"https://github.com/{repo}/actions?query={query}"
    actions_html, actions_error = fetch_url(actions_url)
    if actions_error or not actions_html:
        return {
            "source": "github_public_html",
            "status": "not_available",
            "conclusion": None,
            "head_matches": False,
            "url": actions_url,
            "error": actions_error or "empty_actions_page",
        }
    candidates = extract_action_run_ids(actions_html)[:10]
    errors: list[str] = []
    for run_id in candidates:
        run_url = f"https://github.com/{repo}/actions/runs/{run_id}"
        run_html, run_error = fetch_url(run_url)
        if run_error or not run_html:
            errors.append(f"{run_id}:{run_error or 'empty_run_page'}")
            continue
        parsed = parse_run_page_for_head_success(
            run_html,
            head_sha=head_sha,
            workflow_name=workflow_name,
        )
        if parsed.get("head_matches") is True:
            return {
                "source": "github_public_html",
                "status": parsed.get("status"),
                "conclusion": parsed.get("conclusion"),
                "url": run_url,
                "head_sha": head_sha,
                "head_matches": True,
                "recent_runs_checked": len(candidates),
                "workflow": workflow_name,
            }
    return {
        "source": "github_public_html",
        "status": "not_found_for_head",
        "conclusion": None,
        "head_matches": False,
        "url": actions_url,
        "recent_runs_checked": len(candidates),
        "errors": errors[:5],
        "workflow": workflow_name,
    }


def ci_status(
    root: Path,
    head_sha: str,
    branch: str,
    *,
    required_before_ready: bool = False,
    workflow_name: str = MAIN_CI_WORKFLOW,
) -> dict[str, Any]:
    ci = ci_from_env(head_sha, workflow_name=workflow_name)
    if ci is None:
        ci = ci_from_gh(root, head_sha, branch, workflow_name=workflow_name)
    if ci.get("head_matches") is not True:
        fallback = ci_from_public_html(
            root,
            head_sha,
            branch,
            workflow_name=workflow_name,
        )
        if fallback.get("head_matches") is True and fallback.get("status") == "completed" and fallback.get("conclusion") == "success":
            fallback["fallback_from"] = ci.get("source")
            ci = fallback
        elif ci.get("source") in {"gh_error", "gh_unavailable", "gh_parse_error"}:
            ci = {**ci, "fallback": fallback}
    if required_before_ready:
        ci["required_before_ready"] = ci.get("conclusion") != "success"
    return ci


def reusable_workflow_job_status(
    root: Path,
    head_sha: str,
    parent: dict[str, Any],
    *,
    workflow_name: str,
    job_name: str,
    parent_workflow_name: str = MAIN_CI_WORKFLOW,
    required_before_ready: bool = False,
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "source": "gh_run_view",
        "evidence_type": "reusable_workflow_job",
        "invocation": "workflow_call",
        "workflow": workflow_name,
        "parent_workflow": parent_workflow_name,
        "job": job_name,
        "head_sha": head_sha,
        "head_matches": False,
        "status": "not_available",
        "conclusion": None,
    }
    if parent.get("head_matches") is not True or parent.get("workflow") != parent_workflow_name:
        base["error"] = "exact_parent_workflow_run_unavailable"
        if required_before_ready:
            base["required_before_ready"] = True
        return base

    run_id = parent.get("database_id")
    if not run_id:
        match = re.search(r"/actions/runs/([0-9]+)", str(parent.get("url") or ""))
        run_id = match.group(1) if match else None
    gh = shutil.which("gh")
    if not gh or not run_id:
        base["source"] = "gh_unavailable" if not gh else "parent_run_id_unavailable"
        if required_before_ready:
            base["required_before_ready"] = True
        return base

    proc = run(
        root,
        [
            gh,
            "run",
            "view",
            str(run_id),
            "--json",
            "databaseId,status,conclusion,url,headSha,workflowName,createdAt,jobs",
        ],
        timeout=20,
    )
    if proc.returncode != 0:
        base["source"] = "gh_error"
        base["error"] = redact((proc.stderr or proc.stdout or "gh command failed").strip())
        if required_before_ready:
            base["required_before_ready"] = True
        return base
    try:
        viewed = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        base["source"] = "gh_parse_error"
        if required_before_ready:
            base["required_before_ready"] = True
        return base

    if viewed.get("headSha") != head_sha or viewed.get("workflowName") != parent_workflow_name:
        base["error"] = "parent_run_identity_mismatch"
        if required_before_ready:
            base["required_before_ready"] = True
        return base
    matching_jobs = [job for job in viewed.get("jobs", []) if job.get("name") == job_name]
    if len(matching_jobs) != 1:
        base["status"] = "not_found_for_head" if not matching_jobs else "ambiguous_for_head"
        base["error"] = "reusable_workflow_job_not_unique"
        base["matching_jobs"] = len(matching_jobs)
        if required_before_ready:
            base["required_before_ready"] = True
        return base

    selected = matching_jobs[0]
    base.update(
        {
            "status": selected.get("status") or "unknown",
            "conclusion": selected.get("conclusion") or None,
            "url": selected.get("url"),
            "head_matches": True,
            "database_id": selected.get("databaseId"),
            "parent_run_database_id": viewed.get("databaseId"),
            "parent_run_url": viewed.get("url"),
        }
    )
    if required_before_ready:
        base["required_before_ready"] = base.get("conclusion") != "success"
    return base


def commercial_workflow_evidence(root: Path, head_sha: str, branch: str) -> dict[str, Any]:
    main_ci = ci_status(
        root,
        head_sha,
        branch,
        required_before_ready=True,
        workflow_name=MAIN_CI_WORKFLOW,
    )
    byoc_compose = reusable_workflow_job_status(
        root,
        head_sha,
        main_ci,
        workflow_name=BYOC_COMPOSE_WORKFLOW,
        job_name=BYOC_COMPOSE_REUSABLE_JOB,
        required_before_ready=True,
    )
    byoc_cross_schema = ci_status(
        root,
        head_sha,
        branch,
        required_before_ready=True,
        workflow_name=BYOC_CROSS_SCHEMA_WORKFLOW,
    )
    evidence = {
        "agentops_mis_ci": main_ci,
        "byoc_compose": byoc_compose,
        "byoc_cross_schema_v9_to_v11": byoc_cross_schema,
    }
    return {
        "model": {
            "agentops_mis_ci": "top_level_workflow",
            "byoc_compose": "reusable_workflow_job_in_agentops_mis_ci",
            "byoc_cross_schema_v9_to_v11": "top_level_workflow",
        },
        "exact_sha": head_sha,
        "evidence": evidence,
        "ready": all(item.get("required_before_ready") is False for item in evidence.values()),
    }
