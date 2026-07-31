#!/usr/bin/env python3
"""Verify the task detail page exposes customer-usable evidence state."""
from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TASK_DETAIL = ROOT / "ui" / "start-building-app" / "src" / "app" / "components" / "pages" / "TaskDetail.tsx"
LIVE_API = ROOT / "ui" / "start-building-app" / "src" / "app" / "data" / "liveApi.ts"

SECRET_PATTERNS = [
    re.compile(r"Authorization:", re.IGNORECASE),
    re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"agtok_[A-Za-z0-9_]+"),
    re.compile(r"agtsess_[A-Za-z0-9_]+"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"ntn_[A-Za-z0-9]{20,}"),
]


def main() -> int:
    failures: list[str] = []
    task_detail = TASK_DETAIL.read_text(encoding="utf-8")
    live_api = LIVE_API.read_text(encoding="utf-8")
    source_bundle = f"{task_detail}\n{live_api}"

    expected_markers = {
        "task_detail_uses_live_detail": "loadTaskDetail",
        "task_detail_evidence_summary_en": "Delivery Evidence Summary",
        "task_detail_evidence_summary_zh": "交付证据摘要",
        "task_detail_execution_posture_en": "Execution Posture",
        "task_detail_execution_posture_zh": "执行状态",
        "task_detail_execution_posture_testid": 'data-testid="task-detail-execution-posture"',
        "task_detail_live_runtime_counts": "liveRuntimeRuns.length",
        "task_detail_mock_runtime_counts": "mockRuntimeRuns.length",
        "task_detail_delivery_gate": "deliveryGateStatus",
        "task_detail_approval_wall": "approvalWall",
        "task_detail_live_runtime_label": "Hermes/OpenClaw live evidence",
        "task_detail_mock_runtime_label": "Mock/offline evidence only",
        "task_detail_ledger_state": "ledgerState",
        "task_detail_latest_run": "latestRun",
        "task_detail_pending_approvals": "pendingApprovals",
        "task_detail_open_run_link": "to={`/admin/runs/${latestRun.run_id}`}",
        "task_detail_approval_link": 'to="/workspace/approvals"',
        "task_detail_artifact_count": "taskArtifacts.length",
        "task_detail_approved_artifact_download": 'data-testid="approved-artifact-download"',
        "task_detail_download_uses_same_origin_api": "/mis-api/artifacts/${encodeURIComponent(artifact.artifact_id)}/download",
        "task_detail_download_requires_approved_ledger_state": "hasApprovedDelivery",
        "task_detail_benchmark_count": "taskCaseRuns.length",
        "task_detail_evaluation_count": "taskEvals.length",
        "task_detail_memory_count": "taskMemories.length",
        "task_detail_evidence_counts": "evidenceCounts",
        "task_detail_status_badges": "StatusBadge status={item.status}",
        "task_detail_api_returns_artifacts": "artifacts: asArray(raw.artifacts)",
        "task_detail_api_returns_case_runs": "evaluation_case_runs: asArray<Record<string, unknown>>(raw.evaluation_case_runs).map(normalizeEvaluationCaseRun)",
    }
    for label, marker in expected_markers.items():
        if marker not in source_bundle:
            failures.append(f"missing {label}: {marker}")

    secret_hits = [pattern.pattern for pattern in SECRET_PATTERNS if pattern.search(source_bundle)]
    if secret_hits:
        failures.append(f"secret-like pattern found in task detail UI source: {secret_hits}")

    output = {
        "operation": "task_detail_evidence_ui_smoke",
        "ok": not failures,
        "files": [str(TASK_DETAIL.relative_to(ROOT)), str(LIVE_API.relative_to(ROOT))],
        "markers_checked": len(expected_markers),
        "failures": failures,
        "safety": {
            "read_only": True,
            "ledger_mutated": False,
            "live_execution_performed": False,
            "token_omitted": True,
        },
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
