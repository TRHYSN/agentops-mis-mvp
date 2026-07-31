#!/usr/bin/env python3
"""Verify real-runtime UI controls require an explicit confirmation latch."""
from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AI_EMPLOYEES = ROOT / "ui" / "start-building-app" / "src" / "app" / "components" / "pages" / "AIEmployees.tsx"
CUSTOMER_DISPATCH = ROOT / "ui" / "start-building-app" / "src" / "app" / "components" / "pixel" / "CustomerDispatchPanel.tsx"

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
    ai = AI_EMPLOYEES.read_text(encoding="utf-8")
    customer = CUSTOMER_DISPATCH.read_text(encoding="utf-8")

    expected_markers = {
        "ai_live_confirm_state": "const [liveRuntimeConfirmed, setLiveRuntimeConfirmed] = useState(false);",
        "ai_live_confirm_label_en": 'liveRuntimeConfirmLabel: "I understand this will run a real local Hermes/OpenClaw adapter',
        "ai_live_confirm_label_zh": 'liveRuntimeConfirmLabel: "我确认这会运行真实本地 Hermes/OpenClaw adapter',
        "ai_live_confirm_helper": "const liveAdapterConfirmMissing = (adapter:",
        "ai_selected_adapter_confirm_gate": "selectedAdapterLiveConfirmMissing",
        "ai_customer_live_button_gate": "selectedAdapterLiveBlocked || selectedAdapterLiveConfirmMissing",
        "ai_commander_live_button_gate": "action.confirm && liveAdapterConfirmMissing(action.adapter)",
        "ai_worker_once_gate": "disabled={Boolean(dispatching) || liveAdapterConfirmMissing(item.adapter)}",
        "ai_daemon_start_gate": "workerStartBlocked || liveAdapterConfirmMissing(item.adapter)",
        "ai_host_managed_adapter_gate": "isHostManagedAdapter(item.adapter)",
        "ai_host_managed_stop_gate": "disabled={Boolean(dispatching) || hostManagedAdapters.size > 0 || controlBlockedAdapters.size > 0}",
        "ai_process_identity_gate": "daemon.process_claim_active && daemon.control_allowed === false",
        "ai_process_identity_badge": "daemon.process_identity_verified ? copy.processVerified : copy.processUnverified",
        "ai_host_managed_label_zh": 'hostManaged: "主机托管"',
        "ai_host_managed_control_hint_zh": 'hostManagedHint: "由 AgentOps 主机托管，请使用主机生命周期控制。"',
        "ai_execution_mode_strip": 'data-testid="execution-mode-strip"',
        "ai_execution_mode_title_en": 'executionModeTitle: "Execution mode"',
        "ai_execution_mode_title_zh": 'executionModeTitle: "执行模式"',
        "ai_execution_mode_summary_en": 'executionModeSummary: "One read-only strip',
        "ai_execution_mode_summary_zh": 'executionModeSummary: "只读汇总当前客户任务路径',
        "ai_execution_mode_cards": "const executionModeCards = [",
        "ai_execution_mode_api_loader": "loadOperatorExecutionMode",
        "ai_execution_mode_api_payload": "operatorExecutionMode",
        "ai_execution_mode_api_status": "const selectedExecutionStatus = operatorExecutionMode?.status || fallbackSelectedExecutionStatus",
        "ai_execution_mode_selected_status_fallback": "const fallbackSelectedExecutionStatus = selectedAdapterLiveBlocked",
        "ai_execution_mode_selected_label_fallback": "const fallbackSelectedExecutionLabel = selectedAdapterLiveBlocked",
        "ai_execution_mode_confirm_wall": 'id: "execution-mode-confirm-run-wall"',
        "ai_execution_mode_prepared_wall": 'id: "execution-mode-prepared-action-wall"',
        "ai_execution_mode_approval_waiting": 'id: "execution-mode-approval-waiting"',
        "ai_execution_mode_async_jobs": 'id: "execution-mode-async-jobs"',
        "ai_execution_mode_copy_readiness": 'agentops worker readiness',
        "customer_live_confirm_state": "const [liveRuntimeConfirmed, setLiveRuntimeConfirmed] = useState(false);",
        "customer_live_confirm_helper": 'const liveAdapterConfirmMissing = workerAdapter !== "mock" && !liveRuntimeConfirmed;',
        "customer_live_confirm_label_en": "I confirm this may run a real Hermes/OpenClaw adapter",
        "customer_live_confirm_label_zh": "我确认将运行真实 Hermes/OpenClaw adapter",
        "customer_execution_mode_loader": "loadOperatorExecutionMode",
        "customer_execution_mode_payload": "executionMode",
        "customer_execution_mode_strip": 'data-testid="pixel-customer-execution-mode"',
        "customer_execution_mode_command": "agentops operator execution-mode --adapter",
        "customer_worker_live_gate": "disabled={workerBusy || !title.trim() || liveAdapterConfirmMissing}",
        "customer_async_worker_gate": "disabled={jobBusy || !title.trim() || liveAdapterConfirmMissing}",
        "customer_async_template_gate": "disabled={templateJobBusy || !selectedTemplateId || liveAdapterConfirmMissing}",
        "customer_real_run_gate": "disabled={busy || !title.trim() || liveAdapterConfirmMissing}",
        "customer_dispatch_mode_key": "customerDispatchMode.key",
        "customer_mode_safe_dry_run": "safe_dry_run",
        "customer_mode_mock_ledger_write": "mock_ledger_write",
        "customer_mode_real_runtime_gated": "real_runtime_gated",
        "customer_mode_real_runtime_confirmed": "real_runtime_confirmed",
        "customer_mode_approval_prepared_action": "approval_prepared_action",
        "customer_result_ledger_state": "resultLedgerState",
    }
    source_bundle = f"{ai}\n{customer}"
    for label, marker in expected_markers.items():
        if marker not in source_bundle:
            failures.append(f"missing {label}: {marker}")

    if "confirm_run: adapter !== \"mock\"" not in ai:
        failures.append("AIEmployees live dispatch should still pass confirm_run only for live adapters")
    if "confirm_run: customerTaskForm.adapter !== \"mock\"" not in ai:
        failures.append("AIEmployees async customer task should still gate confirm_run by adapter")
    if "confirm_run: confirmRun" not in customer:
        failures.append("CustomerDispatchPanel worker call should keep explicit confirm_run input")

    secret_hits = [pattern.pattern for pattern in SECRET_PATTERNS if pattern.search(source_bundle)]
    if secret_hits:
        failures.append(f"secret-like pattern found in UI source: {secret_hits}")

    output = {
        "ok": not failures,
        "operation": "real_runtime_ui_confirm_smoke",
        "files": [str(AI_EMPLOYEES.relative_to(ROOT)), str(CUSTOMER_DISPATCH.relative_to(ROOT))],
        "markers_checked": len(expected_markers),
        "safety": {
            "read_only": True,
            "ledger_mutated": False,
            "live_execution_performed": False,
            "token_omitted": True,
        },
        "failures": failures,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
