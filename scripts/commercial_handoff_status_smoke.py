#!/usr/bin/env python3
"""Emit and guard the commercial handoff status packet."""
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
BREAKDOWN = ROOT / "docs" / "COMMERCIAL_MIGRATION_CLEAN_ROOM_BREAKDOWN.md"
RELEASE_PACKET = ROOT / "docs" / "RELEASE_EVIDENCE_PACKET.md"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
BYOC_COMPOSE_WORKFLOW = ROOT / ".github" / "workflows" / "byoc-compose-acceptance.yml"
BYOC_CROSS_SCHEMA_WORKFLOW = ROOT / ".github" / "workflows" / "byoc-cross-schema-v9-v11-acceptance.yml"
BYOC_CUSTOMER_RELEASE_WORKFLOW = ROOT / ".github" / "workflows" / "byoc-customer-release-acceptance.yml"
BYOC_README = ROOT / "deploy" / "byoc" / "README.md"
INDEX_ACCEPTANCE = ROOT / "docs" / "COMMERCIAL_EVIDENCE_PACKET_INDEX_ACCEPTANCE.md"
CURRENT_ACCEPTANCE = ROOT / "docs" / "COMMERCIAL_CURRENT_EVIDENCE_STATUS_ACCEPTANCE.md"
HANDOFF_ACCEPTANCE = ROOT / "docs" / "COMMERCIAL_HANDOFF_STATUS_ACCEPTANCE.md"

SOURCE_DOCS = [
    INDEX,
    BREAKDOWN,
    RELEASE_PACKET,
    CI_WORKFLOW,
    BYOC_COMPOSE_WORKFLOW,
    BYOC_CROSS_SCHEMA_WORKFLOW,
    BYOC_CUSTOMER_RELEASE_WORKFLOW,
    BYOC_README,
    INDEX_ACCEPTANCE,
    CURRENT_ACCEPTANCE,
    HANDOFF_ACCEPTANCE,
]
COMMAND = "python3 scripts/commercial_handoff_status_smoke.py"

EXPECTED_LANES = [
    ("Lane 0", "Runtime Boundary"),
    ("Lane 1", "PostgreSQL Schema And Startup"),
    ("Lane 2", "Agent Identity And Plans"),
    ("Lane 3", "Customer Delivery And Human Review"),
    ("Lane 4", "Prepared Actions"),
    ("Lane 5", "Read Models And Supervision"),
    ("Lane 6", "Enrollment And Entitlements"),
    ("Lane 7", "Deployment And Promotion"),
]

PACKET_STATUS = {
    "Current Evidence Status": "generator_smoke_added",
    "Release Evidence Packet": "existing_generator",
    "Commercial Handoff Status": "generator_smoke_added",
    "Promotion Preflight": "generator_smoke_added",
    "Promotion Packet": "generator_smoke_added",
    "Receipt Plan": "generator_smoke_added",
    "Receipt Recording": "generator_smoke_added",
    "Rerun Bundle Preview": "generator_smoke_added",
}

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


def unsafe_claim_hits(text: str) -> list[str]:
    hits: list[str] = []
    negative_markers = ("no ", "not ", "never ", "without ", "must not ", "do not ", "unclaimed")
    for line in text.splitlines():
        lowered = line.lower()
        for claim in UNSAFE_POSITIVE_CLAIMS:
            claim_lower = claim.lower()
            if claim_lower not in lowered:
                continue
            claim_index = lowered.find(claim_lower)
            prefix = lowered[max(0, claim_index - 40) : claim_index]
            if any(marker in prefix for marker in negative_markers):
                continue
            hits.append(claim)
    return sorted(set(hits))


def has_hardcoded_sha(text: str) -> bool:
    return bool(re.search(r"\b[0-9a-f]{40}\b", text))


def validate_sources(texts: dict[Path, str], failures: list[str]) -> None:
    for path in SOURCE_DOCS:
        require(path.exists(), f"missing source: {path.relative_to(ROOT)}", failures)

    index_text = texts.get(INDEX, "")
    breakdown_text = texts.get(BREAKDOWN, "")
    release_text = texts.get(RELEASE_PACKET, "")
    ci_text = texts.get(CI_WORKFLOW, "")
    byoc_compose_text = texts.get(BYOC_COMPOSE_WORKFLOW, "")
    byoc_cross_schema_text = texts.get(BYOC_CROSS_SCHEMA_WORKFLOW, "")
    byoc_customer_release_text = texts.get(BYOC_CUSTOMER_RELEASE_WORKFLOW, "")
    byoc_readme_text = texts.get(BYOC_README, "")
    handoff_text = texts.get(HANDOFF_ACCEPTANCE, "")

    require("Commercial Handoff Status" in index_text, "index missing Commercial Handoff Status row", failures)
    require("generator smoke added" in index_text, "index must mark handoff status as generator-smoke guarded", failures)
    require(COMMAND in index_text, "index missing handoff command", failures)
    require(COMMAND in release_text, "release packet doc missing handoff command", failures)
    require(COMMAND in ci_text, "CI workflow missing handoff command", failures)
    require("read-only handoff packet" in handoff_text, "handoff acceptance missing read-only packet boundary", failures)

    for lane_id, lane_name in EXPECTED_LANES:
        require(
            f"{lane_id}: {lane_name}" in breakdown_text,
            f"missing clean-room lane: {lane_name}",
            failures,
        )
    require("Do not merge PR #22 directly." in breakdown_text, "PR #22 direct-merge block missing", failures)
    for marker in (
        "Clean install, restore drill, and same-schema image lifecycle",
        "Run isolated restore and role-boundary drill",
        "Run real same-schema retained-data lifecycle",
        "backup_restore_authoritative",
    ):
        require(marker in byoc_compose_text, f"BYOC Compose workflow missing contract marker: {marker}", failures)
    for marker in (
        "AGENTOPS_HISTORICAL_V9_REVISION",
        "git merge-base --is-ancestor",
        "historical-v9.Dockerfile",
        "grep -E '^v22\\.'",
        "${GITHUB_SHA}",
        ".forward_migrations_applied == 3",
        ".v11_data_probe_written == true",
        ".backup_restore_authoritative == true",
        ".down_migration_performed == false",
        ".old_image_restored == true",
        ".postgres_volume_preserved == true",
        ".postgres_cluster_identity_preserved == true",
    ):
        require(marker in byoc_cross_schema_text, f"BYOC cross-schema workflow missing contract marker: {marker}", failures)
    for marker in (
        "name: BYOC Customer Release Acceptance",
        "workflow_dispatch:",
        "codex/commercial-control-plane-main-integration",
        "packages: write",
        "Install without checkout or repository state",
        "actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093",
        "repository_checkout_required == false",
        "compose_build_performed == false",
    ):
        require(marker in byoc_customer_release_text, f"BYOC customer release workflow missing contract marker: {marker}", failures)
    require("pull_request:" not in byoc_customer_release_text, "BYOC customer release must not grant package publication to PR CI", failures)
    require("workflow_call:" not in byoc_customer_release_text, "BYOC customer release must remain a top-level exact-branch workflow", failures)
    for marker in (
        ".github/workflows/byoc-compose-acceptance.yml",
        ".github/workflows/byoc-cross-schema-v9-v11-acceptance.yml",
        ".github/workflows/byoc-customer-release-acceptance.yml",
        "exactly three manifest migrations",
        "v11-only",
        "backup authority",
        "does not run a down migration",
        "same candidate",
    ):
        require(marker in byoc_readme_text, f"BYOC README missing current promotion boundary: {marker}", failures)

    for packet, status in PACKET_STATUS.items():
        require(packet in index_text, f"missing packet row: {packet}", failures)
        require(status != "generator_smoke_added" or "generator smoke added" in index_text, f"missing generator status for packet: {packet}", failures)

    joined = "\n".join(texts.values())
    for claim in unsafe_claim_hits(joined):
        require(False, f"unsafe positive commercial claim found: {claim}", failures)
    secret_hits = [pattern.pattern for pattern in SECRET_PATTERNS if pattern.search(joined)]
    require(not secret_hits, f"secret-like marker found in handoff sources: {secret_hits}", failures)

    generated_docs = [INDEX, HANDOFF_ACCEPTANCE, BYOC_README]
    hardcoded = [path.name for path in generated_docs if has_hardcoded_sha(texts.get(path, ""))]
    require(not hardcoded, f"hard-coded SHA found in commercial handoff docs: {hardcoded}", failures)


def lane_status(promotion_ready: bool, worktree_clean: bool) -> list[dict[str, str]]:
    status = (
        "implementation_present_uncommitted_changes"
        if not worktree_clean
        else "implementation_complete_exact_head_ci_verified"
        if promotion_ready
        else "implementation_complete_exact_head_ci_pending"
    )
    return [
        {
            "lane": "Lane 0",
            "name": "Runtime Boundary",
            "status": status,
            "evidence": "production Next and shared Vite builds fail closed on Python, SQLite, unsafe transport, and unknown production routes.",
        },
        {
            "lane": "Lane 1",
            "name": "PostgreSQL Schema And Startup",
            "status": status,
            "evidence": "schema v11, thirteen pinned migrations, catalog fingerprinting, restricted runtime/admin roles, and PostgreSQL 16 contracts are implemented.",
        },
        {
            "lane": "Lane 2",
            "name": "Agent Identity And Plans",
            "status": status,
            "evidence": "TypeScript/PostgreSQL owns Agent identity, sessions, tasks, plans, runs, manifests, and governed evidence.",
        },
        {
            "lane": "Lane 3",
            "name": "Customer Delivery And Human Review",
            "status": status,
            "evidence": "TypeScript owns delivery requests and Human Session review with workspace, CSRF, replay, and sealed-evidence gates.",
        },
        {
            "lane": "Lane 4",
            "name": "Prepared Actions",
            "status": status,
            "evidence": "PostgreSQL owns immutable approval bindings, execution leases, terminal receipts, and response-loss reconciliation.",
        },
        {
            "lane": "Lane 5",
            "name": "Read Models And Supervision",
            "status": status,
            "evidence": "TypeScript owns Human and Agent task, run, artifact, evidence-graph, and supervision reads.",
        },
        {
            "lane": "Lane 6",
            "name": "Enrollment And Entitlements",
            "status": status,
            "evidence": "TypeScript/PostgreSQL owns approval-gated enrollment, sessions, entitlements, quotas, cost reservations, and fail-closed denial audits.",
        },
        {
            "lane": "Lane 7",
            "name": "Deployment And Promotion",
            "status": status,
            "evidence": "An immutable-image, checksum-manifested source-free customer bundle drives real Compose clean-install, committed backup, isolated restore, and same-schema retained-volume lifecycle; cross-schema v9-to-v11 rollback is separately exercised, and every candidate still requires exact-head workflow evidence plus same-SHA runtime acceptance.",
        },
    ]


def main() -> int:
    failures: list[str] = []
    texts = {path: read(path) for path in SOURCE_DOCS}
    validate_sources(texts, failures)

    head_sha = git_text(["rev-parse", "HEAD"])
    branch = current_branch()
    promotion_workflows = commercial_workflow_evidence(ROOT, head_sha, branch)
    packets = [
        {"packet": packet, "status": status, "source": "docs/COMMERCIAL_EVIDENCE_PACKET_INDEX.md"}
        for packet, status in PACKET_STATUS.items()
    ]

    worktree_entries = status_entries()
    candidate_clean = not worktree_entries
    output: dict[str, Any] = {
        "operation": "commercial_handoff_status_smoke",
        "ok": not failures,
        "evidence_class": "commercial_handoff_status",
        "handoff_class": "commercial_handoff_status",
        "head": {
            "sha": head_sha,
            "branch": branch,
            "upstream_sync": upstream_sync(),
            "working_tree_entries": len(worktree_entries),
        },
        "ci": promotion_workflows["evidence"]["agentops_mis_ci"],
        "promotion_workflows": promotion_workflows,
        "source_docs": [str(path.relative_to(ROOT)) for path in SOURCE_DOCS],
        "clean_room_lanes": lane_status(
            bool(promotion_workflows["ready"]),
            candidate_clean,
        ),
        "packet_status": packets,
        "next_recommended_generator": (
            "commit_candidate_before_exact_head_ci"
            if not candidate_clean
            else "same_sha_real_runtime_acceptance"
            if promotion_workflows["ready"]
            else "exact_head_ci_and_byoc_acceptance"
        ),
        "commercial_limits": {
            "hosted_ready": False,
            "billing_ready": False,
            "cleanup_execution_enabled": False,
            "postgres_required_for_local_mvp": False,
            "live_runtime_execution_performed": False,
            "direct_pr22_merge_allowed": False,
        },
        "safety": {
            "read_only": True,
            "ledger_mutated": False,
            "db_read": False,
            "env_dumped": False,
            "pr22_contents_read": False,
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
