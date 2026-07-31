#!/usr/bin/env python3
"""Verify release-branch hygiene before RC packaging or merge review."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UNSAFE_PATH_PATTERNS = [
    re.compile(r"(^|/)(node_modules|dist|\.agentops_runtime|__pycache__|\.pytest_cache|\.next)(/|$)"),
    re.compile(r"(^|/)(agentops_mis\.db|.*\.sqlite3?|.*\.db(?:-wal|-shm)?|\.env$|\.env\..*|.*\.log$|.*\.pid$|.*\.sock$|.*\.key$|.*\.pem$|.*\.jsonl$)"),
]
ALLOWED_BASENAMES = {
    ".env.example",
}
PATH_POLICY_CASES = {
    ".env.example": False,
    "deploy/byoc/.env.example": False,
    "nested/customer/config/.env.example": False,
    ".env": True,
    "deploy/byoc/.env": True,
    ".env.local": True,
    "deploy/byoc/.env.local": True,
    "deploy/byoc/.env.production": True,
    "deploy/byoc/.env.example.local": True,
}
SECRET_MARKERS = ("Authorization:", "Bearer ", "agtok_", "agtsess_", "sk-", "ntn_", "github_pat_")


def git(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=check,
    )


def git_text(args: list[str], *, check: bool = True) -> str:
    proc = git(args, check=check)
    return (proc.stdout or "").strip()


def maybe_git_text(args: list[str]) -> str | None:
    proc = git(args, check=False)
    if proc.returncode != 0:
        return None
    return (proc.stdout or "").strip()


def tracked_files() -> list[str]:
    raw = git(["ls-files", "-z"]).stdout
    return [item for item in raw.split("\0") if item]


def status_entries() -> list[str]:
    raw = maybe_git_text(["status", "--porcelain"]) or ""
    return [line for line in raw.splitlines() if line.strip()]


def branch_name() -> str:
    name = maybe_git_text(["branch", "--show-current"]) or ""
    if name:
        return name
    return "DETACHED"


def upstream_ref() -> str | None:
    return maybe_git_text(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"])


def ahead_behind(upstream: str | None) -> dict[str, int | None]:
    if not upstream:
        return {"ahead": None, "behind": None}
    counts = maybe_git_text(["rev-list", "--left-right", "--count", f"{upstream}...HEAD"])
    if not counts:
        return {"ahead": None, "behind": None}
    behind_text, ahead_text = counts.split()
    return {"ahead": int(ahead_text), "behind": int(behind_text)}


def main_history() -> dict[str, int | None | bool]:
    main_ref = None
    for candidate in ("origin/main", "main"):
        if maybe_git_text(["rev-parse", "--verify", candidate]):
            main_ref = candidate
            break
    if not main_ref:
        return {"main_ref": None, "ahead_main": None, "behind_main": None, "merge_base_exists": False}
    ahead = int(maybe_git_text(["rev-list", "--count", f"{main_ref}..HEAD"]) or 0)
    behind = int(maybe_git_text(["rev-list", "--count", f"HEAD..{main_ref}"]) or 0)
    merge_base = maybe_git_text(["merge-base", "HEAD", main_ref])
    return {
        "main_ref": main_ref,
        "ahead_main": ahead,
        "behind_main": behind,
        "merge_base_exists": bool(merge_base),
    }


def is_merged_main_context(current_branch: str, upstream: str | None, main: dict[str, int | None | bool]) -> bool:
    if current_branch != "main":
        return False
    if upstream not in ("origin/main", "main"):
        return False
    return (
        main.get("main_ref") in ("origin/main", "main")
        and main.get("merge_base_exists") is True
        and int(main.get("ahead_main") or 0) == 0
        and int(main.get("behind_main") or 0) == 0
    )


def is_allowed_tracked_path(path: str) -> bool:
    return Path(path).name in ALLOWED_BASENAMES


def is_unsafe_tracked_path(path: str) -> bool:
    if is_allowed_tracked_path(path):
        return False
    return any(pattern.search(path) for pattern in UNSAFE_PATH_PATTERNS)


def path_policy_failures() -> list[str]:
    failures: list[str] = []
    for path, expected_unsafe in PATH_POLICY_CASES.items():
        actual_unsafe = is_unsafe_tracked_path(path)
        if actual_unsafe != expected_unsafe:
            failures.append(
                f"path policy classified {path!r} as unsafe={actual_unsafe}, expected unsafe={expected_unsafe}"
            )
    return failures


def unsafe_tracked_files(files: list[str]) -> list[str]:
    unsafe: list[str] = []
    for path in files:
        if is_unsafe_tracked_path(path):
            unsafe.append(path)
    return sorted(unsafe)


def output_leaks(payload: dict) -> bool:
    text = json.dumps(payload, ensure_ascii=False)
    return any(marker in text for marker in SECRET_MARKERS)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-branch", default="", help="Require this branch name when not detached.")
    parser.add_argument("--require-clean", action="store_true", help="Fail if the working tree has local changes.")
    parser.add_argument("--require-upstream-synced", action="store_true", help="Fail if the branch is ahead/behind upstream.")
    parser.add_argument("--min-reviewable-commits", type=int, default=2, help="Minimum commits ahead of main when main is available.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    failures: list[str] = []
    files = tracked_files()
    current_branch = branch_name()
    head_sha = git_text(["rev-parse", "HEAD"])
    upstream = upstream_ref()
    upstream_counts = ahead_behind(upstream)
    main = main_history()
    merged_main_context = is_merged_main_context(current_branch, upstream, main)
    status = status_entries()
    unsafe_files = unsafe_tracked_files(files)
    failures.extend(path_policy_failures())

    if args.expected_branch and current_branch != args.expected_branch:
        failures.append(f"expected branch {args.expected_branch}, got {current_branch}")
    if args.require_clean and status:
        failures.append("working tree has local changes")
    if args.require_upstream_synced and upstream:
        if upstream_counts.get("ahead") != 0 or upstream_counts.get("behind") != 0:
            failures.append(f"branch is not synced with upstream {upstream}: {upstream_counts}")
    if upstream and upstream_counts.get("behind") not in (0, None):
        failures.append(f"branch is behind upstream {upstream}: {upstream_counts.get('behind')}")
    if unsafe_files:
        failures.append(f"tracked unsafe runtime/generated files: {unsafe_files[:20]}")
    if main.get("main_ref") and not main.get("merge_base_exists"):
        failures.append(f"no merge-base with {main.get('main_ref')}")
    if (
        not merged_main_context
        and main.get("ahead_main") is not None
        and int(main["ahead_main"] or 0) < args.min_reviewable_commits
    ):
        failures.append(f"history ahead of main is too small for reviewable functional history: {main['ahead_main']}")

    payload = {
        "ok": not failures,
        "operation": "release_branch_control_smoke",
        "branch": current_branch,
        "head_sha": head_sha,
        "upstream": upstream,
        "upstream_sync": upstream_counts,
        "main_history": main,
        "merged_main_context": merged_main_context,
        "reviewable_history_required": not merged_main_context,
        "tracked_files": len(files),
        "unsafe_tracked_files": unsafe_files,
        "working_tree_entries": len(status),
        "require_clean": bool(args.require_clean),
        "require_upstream_synced": bool(args.require_upstream_synced),
        "allowed_paths": sorted(ALLOWED_BASENAMES),
        "allowed_path_basenames": sorted(ALLOWED_BASENAMES),
        "path_policy_cases": len(PATH_POLICY_CASES),
        "contract": "Release branch has an identifiable head, reviewable history, no unsafe tracked runtime/generated state, and no upstream-behind drift.",
        "failures": failures,
        "token_omitted": True,
    }
    if output_leaks(payload):
        payload["ok"] = False
        payload["failures"].append("secret-like marker leaked in release branch control output")
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
