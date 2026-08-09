#!/usr/bin/env python3
"""Emit and guard the commercial current-evidence status packet."""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from github_ci_evidence import commercial_workflow_evidence


ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "docs" / "COMMERCIAL_EVIDENCE_PACKET_INDEX.md"
CHECKLIST = ROOT / "docs" / "V1_5_MERGE_READINESS_CHECKLIST.md"
RELEASE_PACKET = ROOT / "docs" / "RELEASE_EVIDENCE_PACKET.md"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
ACCEPTANCE = ROOT / "docs" / "COMMERCIAL_CURRENT_EVIDENCE_STATUS_ACCEPTANCE.md"

SOURCE_DOCS = [INDEX, CHECKLIST, RELEASE_PACKET, CI_WORKFLOW]
COMMAND = "python3 scripts/commercial_current_evidence_status_smoke.py"
MANUAL_LIVE_COMMAND = (
    "python3 scripts/v1_5_current_code_product_evidence.py "
    "--base-url http://127.0.0.1:<current-code-port> "
    "--db-path /tmp/<current-code-agentops>.db --confirm-live"
)

SECRET_PATTERNS = [
    re.compile(r"Authorization:", re.IGNORECASE),
    re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"agtok_[A-Za-z0-9_]+"),
    re.compile(r"agtsess_[A-Za-z0-9_]+"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"ntn_[A-Za-z0-9]{8,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]+"),
    re.compile(r"gh[opsu]_[A-Za-z0-9_]+"),
    re.compile(r"AGENTOPS_(API|ADMIN)_KEY=", re.IGNORECASE),
]

UNSAFE_POSITIVE_CLAIMS = [
    "hosted SaaS ready",
    "billing ready",
    "cleanup execution enabled",
    "commercial-ready",
    "Postgres required for local MVP",
    "live runtime execution performed",
]


def run(args: list[str], *, timeout: int = 10) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=timeout, check=False)


def git_text(args: list[str]) -> str:
    proc = run(["git", *args])
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "git command failed").strip())
    return (proc.stdout or "").strip()


def maybe_git_text(args: list[str]) -> str | None:
    proc = run(["git", *args])
    if proc.returncode != 0:
        return None
    return (proc.stdout or "").strip()


def current_branch() -> str:
    return maybe_git_text(["branch", "--show-current"]) or os.environ.get("GITHUB_REF_NAME") or "DETACHED"


def upstream_sync() -> dict[str, int | None]:
    upstream = maybe_git_text(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"])
    if not upstream:
        return {"ahead": None, "behind": None}
    counts = maybe_git_text(["rev-list", "--left-right", "--count", f"{upstream}...HEAD"])
    if not counts:
        return {"ahead": None, "behind": None}
    behind_text, ahead_text = counts.split()
    return {"ahead": int(ahead_text), "behind": int(behind_text)}


def status_entries() -> list[str]:
    raw = maybe_git_text(["status", "--porcelain"]) or ""
    return [line for line in raw.splitlines() if line.strip()]


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def checklist_state(text: str) -> dict[str, str]:
    header = re.search(r"Current status:\s*`([^`]+)`", text)
    final = re.search(r"Current state:\s*```text\s*([A-Z_]+)\s*```", text, re.MULTILINE)
    return {
        "checklist_status": header.group(1).strip() if header else "UNKNOWN",
        "final_state": final.group(1).strip() if final else "UNKNOWN",
    }


def has_hardcoded_sha(text: str) -> bool:
    # Avoid turning generated packet output into tracked evidence source of truth.
    return bool(re.search(r"\b[0-9a-f]{40}\b", text))


def validate_sources(texts: dict[Path, str], failures: list[str]) -> None:
    for path in SOURCE_DOCS:
        require(path.exists(), f"missing source: {path.relative_to(ROOT)}", failures)
    require(ACCEPTANCE.exists(), f"missing acceptance: {ACCEPTANCE.relative_to(ROOT)}", failures)

    index_text = texts.get(INDEX, "")
    release_text = texts.get(RELEASE_PACKET, "")
    ci_text = texts.get(CI_WORKFLOW, "")

    require("Current Evidence Status" in index_text, "index missing Current Evidence Status row", failures)
    require("generator smoke added" in index_text, "index must mark current evidence status as generator-smoke guarded", failures)
    require(COMMAND in index_text, "index missing current evidence command", failures)
    require(COMMAND in release_text, "release packet doc missing current evidence command", failures)
    require(COMMAND in ci_text, "CI workflow missing current evidence command", failures)

    joined = "\n".join(texts.values())
    for claim in UNSAFE_POSITIVE_CLAIMS:
        require(claim not in joined, f"unsafe positive commercial claim found: {claim}", failures)
    secret_hits = [pattern.pattern for pattern in SECRET_PATTERNS if pattern.search(joined)]
    require(not secret_hits, f"secret-like marker found in current evidence sources: {secret_hits}", failures)

    generated_packet_docs = [
        path
        for path in [INDEX, ACCEPTANCE]
        if has_hardcoded_sha(texts.get(path, ""))
    ]
    require(not generated_packet_docs, f"hard-coded SHA found in commercial packet docs: {[p.name for p in generated_packet_docs]}", failures)


def main() -> int:
    failures: list[str] = []
    texts = {path: read(path) for path in [*SOURCE_DOCS, ACCEPTANCE]}
    validate_sources(texts, failures)

    head_sha = git_text(["rev-parse", "HEAD"])
    branch = current_branch()
    promotion_workflows = commercial_workflow_evidence(ROOT, head_sha, branch)
    ci = promotion_workflows["evidence"]["agentops_mis_ci"]
    state = checklist_state(texts.get(CHECKLIST, ""))
    strict_promotion_ready = (
        state["checklist_status"] == "READY_TO_MERGE"
        and state["final_state"] == "READY_TO_MERGE"
        and promotion_workflows["ready"]
        and not status_entries()
    )

    output: dict[str, Any] = {
        "operation": "commercial_current_evidence_status_smoke",
        "ok": not failures,
        "evidence_class": "commercial_current_evidence_status",
        "head": {
            "sha": head_sha,
            "branch": branch,
            "upstream_sync": upstream_sync(),
            "working_tree_entries": len(status_entries()),
        },
        "ci": ci,
        "promotion_workflows": promotion_workflows,
        "release_state": {
            **state,
            "strict_promotion_ready": strict_promotion_ready,
        },
        "source_docs": [str(path.relative_to(ROOT)) for path in SOURCE_DOCS],
        "commercial_limits": {
            "hosted_ready": False,
            "billing_ready": False,
            "cleanup_execution_enabled": False,
            "postgres_required_for_local_mvp": False,
            "live_runtime_execution_performed": False,
        },
        "canonical_commands": {
            "release_evidence": "python3 scripts/release_evidence_packet_smoke.py",
            "merge_readiness": "python3 scripts/merge_readiness_status_smoke.py",
            "current_code_product_evidence": MANUAL_LIVE_COMMAND,
        },
        "safety": {
            "read_only": True,
            "ledger_mutated": False,
            "db_read": False,
            "env_dumped": False,
            "billing_call_performed": False,
            "cleanup_execution_performed": False,
            "live_execution_performed": False,
            "raw_logs_omitted": True,
            "raw_prompts_omitted": True,
            "raw_responses_omitted": True,
            "token_omitted": True,
        },
        "failure_count": len(failures),
        "failures": failures,
    }
    rendered = json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True)
    output_secret_hits = [pattern.pattern for pattern in SECRET_PATTERNS if pattern.search(rendered)]
    if output_secret_hits:
        output["ok"] = False
        output["failure_count"] += 1
        output["failures"].append(f"secret-like marker found in output: {output_secret_hits}")
        rendered = json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True)
    print(rendered)
    return 1 if output["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
