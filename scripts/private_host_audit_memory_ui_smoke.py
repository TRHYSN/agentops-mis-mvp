#!/usr/bin/env python3
"""Statically verify the Private Host audit and memory pages use live MIS APIs."""
from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "ui" / "start-building-app" / "src" / "app" / "components" / "pages" / "AuditCenter.tsx"
MEMORY = ROOT / "ui" / "start-building-app" / "src" / "app" / "components" / "pages" / "MemoryLibrary.tsx"

SECRET_PATTERNS = [
    re.compile(r"Authorization:", re.IGNORECASE),
    re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"agtok_[A-Za-z0-9_]+"),
    re.compile(r"agtsess_[A-Za-z0-9_]+"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"ntn_[A-Za-z0-9]{20,}"),
]


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def main() -> int:
    failures: list[str] = []
    audit = AUDIT.read_text(encoding="utf-8")
    memory = MEMORY.read_text(encoding="utf-8")

    expected = {
        "audit_live_loader": (audit, "loadAudit"),
        "audit_live_hook": (audit, "useLiveData(loadAudit"),
        "audit_read_only_boundary_en": (audit, "Read-only view of bounded audit records"),
        "audit_read_only_boundary_zh": (audit, "只读展示经过边界化处理的审计记录"),
        "audit_actor_filter": (audit, 'data-testid="audit-actor-filter"'),
        "audit_refresh": (audit, "void refresh()"),
        "audit_loading": (audit, "Loading live audit ledger"),
        "audit_error": (audit, "Audit API error"),
        "memory_live_loader": (memory, "loadMemories"),
        "memory_live_hook": (memory, "useLiveData(loadMemories"),
        "memory_decision_api": (memory, "decideMemory(memoryId, decision)"),
        "memory_approve": (memory, 'handleDecision(memory.memory_id, "approve")'),
        "memory_reject": (memory, 'handleDecision(memory.memory_id, "reject")'),
        "memory_busy": (memory, "busyId"),
        "memory_error": (memory, "actionError"),
        "memory_refresh": (memory, "onClick={handleRefresh}"),
        "memory_loading_en": (memory, "Loading live memory ledger"),
        "memory_loading_zh": (memory, "正在加载实时记忆账本"),
        "memory_filters": (memory, 'data-testid="memory-review-filters"'),
        "memory_boundary_en": (memory, "Only reviewed memory enters the host authority store"),
        "memory_boundary_zh": (memory, "只有经过审核的记忆才能进入主机权威存储"),
    }
    for name, (source, marker) in expected.items():
        require(marker in source, f"{name}: missing marker {marker}", failures)

    require("mockData" not in audit, "AuditCenter must not import or read mockData", failures)
    require("mockData" not in memory, "MemoryLibrary must not import or read mockData", failures)
    require("decideMemory" not in audit, "AuditCenter must remain read-only", failures)
    require("tamper-chain verified" not in audit, "AuditCenter must not claim unproven chain verification", failures)
    require(not any(pattern.search(audit + memory) for pattern in SECRET_PATTERNS), "UI source contains token-like material", failures)

    output = {
        "operation": "private_host_audit_memory_ui_smoke",
        "ok": not failures,
        "files": [str(AUDIT.relative_to(ROOT)), str(MEMORY.relative_to(ROOT))],
        "checks": len(expected) + 5,
        "failures": failures,
        "safety": {
            "static_only": True,
            "audit_read_only": True,
            "memory_review_write_only": True,
            "live_execution_performed": False,
            "token_omitted": True,
        },
    }
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
