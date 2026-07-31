#!/usr/bin/env python3
"""Validate the commercial migration clean-room breakdown stays safe and actionable."""
from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "COMMERCIAL_MIGRATION_CLEAN_ROOM_BREAKDOWN.md"

SECRET_PATTERNS = [
    re.compile(r"Authorization:", re.IGNORECASE),
    re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"agtok_[A-Za-z0-9_]+"),
    re.compile(r"agtsess_[A-Za-z0-9_]+"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"ntn_[A-Za-z0-9]{8,}"),
    re.compile(r"AGENTOPS_(API|ADMIN)_KEY=", re.IGNORECASE),
]


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def main() -> int:
    failures: list[str] = []
    text = DOC.read_text(encoding="utf-8")

    required_markers = {
        "pr_number": "PR #22",
        "direct_merge_block": "Do not merge PR #22 directly.",
        "clean_room_rule": "Every production owner is rebuilt as a small commit from current `origin/main`",
        "generated_artifacts_block": "Do not copy generated docs, DB files, caches, `node_modules`, `dist`, `.env`",
        "production_stack": "Next.js 16 App Router",
        "production_write_owner": "Production writes are Next.js/TypeScript/PostgreSQL only.",
        "lane_0": "Lane 0: Runtime Boundary",
        "lane_1": "Lane 1: PostgreSQL Schema And Startup",
        "lane_2": "Lane 2: Agent Identity And Plans",
        "lane_3": "Lane 3: Customer Delivery And Human Review",
        "lane_4": "Lane 4: Prepared Actions",
        "lane_5": "Lane 5: Read Models And Supervision",
        "lane_6": "Lane 6: Enrollment And Entitlements",
        "lane_7": "Lane 7: Deployment And Promotion",
        "real_runtime_gate": "explicitly confirmed Hermes and OpenClaw provider calls",
    }
    for label, marker in required_markers.items():
        require(marker in text, f"missing breakdown marker {label}: {marker}", failures)
    normalized_text = " ".join(text.split())
    require(
        "final same-SHA dual-runtime acceptance and exact-head CI" in normalized_text
        and (
            "Lane 7 clean-customer image installation, retained-data "
            "upgrade/rollback, and an external restore drill before promotion"
        )
        in normalized_text,
        "missing breakdown marker next_slice: final runtime, CI, and Lane 7 gates",
        failures,
    )

    forbidden_claims = [
        "merge PR #22 directly",
        "hosted SaaS ready",
        "billing ready",
        "cleanup execution enabled",
        "Postgres required for local MVP",
    ]
    for claim in forbidden_claims:
        if claim == "merge PR #22 directly":
            require("Do not merge PR #22 directly." in text, "direct merge must be explicitly blocked", failures)
        else:
            require(claim not in text, f"unsafe commercial claim found: {claim}", failures)

    secret_hits = [pattern.pattern for pattern in SECRET_PATTERNS if pattern.search(text)]
    require(not secret_hits, f"secret-like marker found in breakdown doc: {secret_hits}", failures)

    output = {
        "operation": "commercial_migration_breakdown_smoke",
        "ok": not failures,
        "failure_count": len(failures),
        "failures": failures,
        "doc": str(DOC.relative_to(ROOT)),
        "contract": "PR #22 is a reference lane; commercial production owners are rebuilt on current main with a Next.js/TypeScript/PostgreSQL boundary and real-runtime gates.",
        "safety": {
            "read_only": True,
            "ledger_mutated": False,
            "billing_call_performed": False,
            "cleanup_execution_performed": False,
            "live_execution_performed": False,
            "token_omitted": True,
        },
    }
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
