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


def ci_from_env(head_sha: str) -> dict[str, Any] | None:
    if os.environ.get("GITHUB_ACTIONS", "").lower() != "true":
        return None
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    server_url = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    github_sha = os.environ.get("GITHUB_SHA", "")
    if not repo or not run_id:
        return None
    return {
        "source": "github_actions_env",
        "status": "in_progress",
        "conclusion": None,
        "url": f"{server_url.rstrip('/')}/{repo}/actions/runs/{run_id}",
        "head_sha": github_sha,
        "head_matches": github_sha == head_sha,
    }


def ci_from_gh(root: Path, head_sha: str, branch: str) -> dict[str, Any]:
    gh = shutil.which("gh")
    if not gh:
        return {"source": "gh_unavailable", "status": "not_available", "conclusion": None, "head_matches": False}
    proc = run(
        root,
        [
            gh,
            "run",
            "list",
            "--branch",
            branch,
            "--limit",
            "20",
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
            "error": redact((proc.stderr or proc.stdout or "gh command failed").strip()),
        }
    try:
        runs = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        return {"source": "gh_parse_error", "status": "not_available", "conclusion": None, "head_matches": False}
    exact = [item for item in runs if item.get("headSha") == head_sha]
    if not exact:
        return {
            "source": "gh_run_list",
            "status": "not_found_for_head",
            "conclusion": None,
            "head_matches": False,
            "recent_runs_checked": len(runs),
        }
    selected = exact[0]
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


def parse_run_page_for_head_success(run_html: str, *, head_sha: str) -> dict[str, Any]:
    text = html.unescape(run_html)
    if head_sha not in text:
        return {
            "status": "not_found_for_head",
            "conclusion": None,
            "head_matches": False,
            "head_sha": None,
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
    }


def ci_from_public_html(root: Path, head_sha: str, branch: str, *, repo: str | None = None) -> dict[str, Any]:
    repo = repo or repo_from_git_remote(root)
    if not repo:
        return {"source": "github_public_html", "status": "not_available", "conclusion": None, "head_matches": False, "error": "repo_unavailable"}
    query = urllib.parse.quote(f"branch:{branch}", safe="")
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
        parsed = parse_run_page_for_head_success(run_html, head_sha=head_sha)
        if parsed.get("head_matches") is True:
            return {
                "source": "github_public_html",
                "status": parsed.get("status"),
                "conclusion": parsed.get("conclusion"),
                "url": run_url,
                "head_sha": head_sha,
                "head_matches": True,
                "recent_runs_checked": len(candidates),
            }
    return {
        "source": "github_public_html",
        "status": "not_found_for_head",
        "conclusion": None,
        "head_matches": False,
        "url": actions_url,
        "recent_runs_checked": len(candidates),
        "errors": errors[:5],
    }


def ci_status(root: Path, head_sha: str, branch: str, *, required_before_ready: bool = False) -> dict[str, Any]:
    ci = ci_from_env(head_sha)
    if ci is None:
        ci = ci_from_gh(root, head_sha, branch)
    if ci.get("head_matches") is not True or ci.get("status") != "completed" or ci.get("conclusion") != "success":
        fallback = ci_from_public_html(root, head_sha, branch)
        if fallback.get("head_matches") is True and fallback.get("status") == "completed" and fallback.get("conclusion") == "success":
            fallback["fallback_from"] = ci.get("source")
            ci = fallback
        elif ci.get("source") in {"gh_error", "gh_unavailable", "gh_parse_error"}:
            ci = {**ci, "fallback": fallback}
    if required_before_ready:
        ci["required_before_ready"] = ci.get("conclusion") != "success"
    return ci
