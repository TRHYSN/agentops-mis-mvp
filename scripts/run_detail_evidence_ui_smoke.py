#!/usr/bin/env python3
"""Verify the run detail page exposes customer/operator evidence-chain state."""
from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUN_DETAIL = ROOT / "ui" / "start-building-app" / "src" / "app" / "components" / "pages" / "RunDetail.tsx"
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
    run_detail = RUN_DETAIL.read_text(encoding="utf-8")
    live_api = LIVE_API.read_text(encoding="utf-8")
    source_bundle = f"{run_detail}\n{live_api}"

    expected_markers = {
        "run_detail_loads_live_detail": "loadRunDetail",
        "run_detail_evidence_chain_testid": 'data-testid="run-detail-evidence-chain"',
        "run_detail_evidence_chain_title": "Run Evidence Chain",
        "run_detail_chain_status": "evidenceChainStatus",
        "run_detail_runtime_status": "runtimeEvidenceStatus",
        "run_detail_live_runtime": "Hermes/OpenClaw live",
        "run_detail_mock_runtime": "Mock/offline",
        "run_detail_tool_count": "runTools.length",
        "run_detail_eval_count": "runEvaluations.length",
        "run_detail_artifact_count": "runArtifacts.length",
        "run_detail_approved_artifact_download": 'data-testid="approved-artifact-download"',
        "run_detail_download_uses_same_origin_api": "/mis-api/artifacts/${encodeURIComponent(artifact.artifact_id)}/download",
        "run_detail_download_requires_approved_ledger_state": "hasApprovedDelivery",
        "run_detail_approval_count": "runApprovals.length",
        "run_detail_pending_approvals": "pendingApprovals.length",
        "run_detail_audit_refs": "runAudit.length",
        "run_detail_benchmarks": "caseRuns.length",
        "run_detail_task_link": "to={`/admin/tasks/${run.task_id}`}",
        "run_detail_approval_link": 'to="/workspace/approvals"',
        "run_detail_api_returns_artifacts": "artifacts: asArray(raw.artifacts)",
        "run_detail_api_returns_case_runs": "evaluation_case_runs: asArray<Record<string, unknown>>(raw.evaluation_case_runs).map(normalizeEvaluationCaseRun)",
        "run_detail_loads_work_delivery_graph": "loadRunEvidenceGraph",
        "run_detail_work_delivery_graph_testid": 'data-testid="run-detail-work-delivery-graph"',
        "run_detail_work_delivery_graph_title": "Work Delivery Evidence Graph",
        "run_detail_work_delivery_graph_hash": "graph_hash",
        "run_detail_work_delivery_graph_counts": "graphCounts",
        "run_detail_work_delivery_graph_runtime_events": "runtime_events",
        "run_detail_work_delivery_graph_plan_manifest": "plan_evidence_manifests",
        "run_detail_work_delivery_graph_safety": "graphSafety",
        "run_detail_work_delivery_graph_authority": "read_model_over_mis_ledgers",
        "live_api_run_evidence_graph_payload": "RunEvidenceGraphPayload",
        "live_api_run_evidence_graph_endpoint": "/evidence-graph",
        "live_api_run_evidence_graph_fallback": 'status: "unavailable"',
        "run_detail_loads_scoped_evidence_report": 'loadOperatorEvidenceReport(1, { runId: id || "" })',
        "run_detail_project_context_testid": 'data-testid="run-detail-project-context-receipt"',
        "run_detail_project_context_title": "Project Context Receipt",
        "run_detail_project_context_zh_title": "项目上下文收据",
        "run_detail_context_block_count": "context_block_count",
        "run_detail_approved_memory_ids": "approved_memory_ids",
        "run_detail_context_body_omission": "context_body_not_persisted",
        "run_detail_raw_transcript_omission": "raw_transcript_omitted",
        "run_detail_memory_review_items": "memory_review?.items",
        "run_detail_result_memory_id": "item.memory_id",
        "run_detail_result_memory_source_ref": "item.source_ref",
        "run_detail_memory_review_link": 'to="/workspace/memory"',
        "live_api_evidence_report_run_filter": 'query.set("run_id", filters.runId)',
        "live_api_context_packet_hashes": "context_packet_hashes",
    }
    for label, marker in expected_markers.items():
        if marker not in source_bundle:
            failures.append(f"missing {label}: {marker}")

    secret_hits = [pattern.pattern for pattern in SECRET_PATTERNS if pattern.search(source_bundle)]
    if secret_hits:
        failures.append(f"secret-like pattern found in run detail UI source: {secret_hits}")

    output = {
        "operation": "run_detail_evidence_ui_smoke",
        "ok": not failures,
        "files": [str(RUN_DETAIL.relative_to(ROOT)), str(LIVE_API.relative_to(ROOT))],
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
