#!/usr/bin/env python3
"""Verify the dedicated Worker Console UI stays wired to live Worker APIs."""
from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "ui" / "start-building-app" / "src" / "app" / "App.tsx"
SIDEBAR = ROOT / "ui" / "start-building-app" / "src" / "app" / "components" / "layout" / "Sidebar.tsx"
HOME = ROOT / "ui" / "start-building-app" / "src" / "app" / "components" / "pages" / "WorkspaceHome.tsx"
CONSOLE = ROOT / "ui" / "start-building-app" / "src" / "app" / "components" / "pages" / "WorkerConsole.tsx"
LIVE_API = ROOT / "ui" / "start-building-app" / "src" / "app" / "data" / "liveApi.ts"

SECRET_PATTERNS = [
    re.compile(r"Authorization:", re.IGNORECASE),
    re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"agtok_[A-Za-z0-9_]+"),
    re.compile(r"agtsess_[A-Za-z0-9_]+"),
    re.compile(r"sk-[A-Za-z0-9]{8,}"),
    re.compile(r"ntn_[A-Za-z0-9]{8,}"),
]

EXPECTED = {
    "route_import": (APP, 'import { WorkerConsole } from "./components/pages/WorkerConsole";'),
    "route_path": (APP, 'path="/workspace/workers" element={<WorkerConsole />}'),
    "sidebar_label_en": (SIDEBAR, 'workerConsole: "Worker Console"'),
    "sidebar_label_zh": (SIDEBAR, 'workerConsole: "Worker 控制台"'),
    "sidebar_path": (SIDEBAR, 'path: "/workspace/workers"'),
    "home_link": (HOME, 'to: "/workspace/workers"'),
    "console_title_en": (CONSOLE, 'title: "Worker Control Console"'),
    "console_title_zh": (CONSOLE, 'title: "Worker 控制台"'),
    "real_status_loader": (CONSOLE, "loadWorkerStatus()"),
    "real_fleet_loader": (CONSOLE, "loadWorkerFleet()"),
    "service_worker_lane": (CONSOLE, 'lane.lane_type === "gateway_service_worker"'),
    "service_worker_card": (CONSOLE, 'data-testid="gateway-service-worker"'),
    "service_worker_adapter_names": (CONSOLE, 'worker.adapter === "openclaw" ? "OpenClaw Worker" : worker.adapter === "hermes" ? "Hermes Worker"'),
    "execution_capacity_metric": (CONSOLE, "workerStatus?.execution_capacity_workers"),
    "service_worker_metric": (CONSOLE, "workerStatus?.active_service_workers"),
    "real_fleet_hygiene_loader": (CONSOLE, "loadWorkerFleetHygiene({ limit: 8 })"),
    "real_readiness_loader": (CONSOLE, "loadWorkerAdapterReadiness()"),
    "real_local_readiness_loader": (CONSOLE, "loadLocalReadiness()"),
    "local_harness_proof_type": (LIVE_API, "export interface LocalHarnessProofReadiness"),
    "local_harness_proof_payload": (LIVE_API, "local_harness_proof_readiness?: LocalHarnessProofReadiness;"),
    "local_harness_proof_normalize": (LIVE_API, "normalizeHarnessAdapter"),
    "local_harness_proof_panel": (CONSOLE, 'data-testid="worker-local-harness-proof"'),
    "local_harness_proof_source": (CONSOLE, "localReadiness?.local_harness_proof_readiness"),
    "local_harness_proof_command": (CONSOLE, "agentops operator local-harness-proof --limit 8"),
    "local_harness_governed_type": (LIVE_API, "governed_launch?:"),
    "local_harness_governed_packet_type": (LIVE_API, "governed_launch_packet?:"),
    "local_harness_governed_entrypoint": (LIVE_API, "agentops workflow customer-worker-task"),
    "local_harness_governed_ui_label": (CONSOLE, 'governedLaunch: "Governed launch"'),
    "local_harness_governed_ui_label_zh": (CONSOLE, 'governedLaunch: "治理启动"'),
    "local_harness_governed_copy": (CONSOLE, "proof?.governed_launch?.confirmed_command"),
    "local_harness_receipt_copy": (CONSOLE, "proof?.governed_launch?.receipt_record_command"),
    "local_harness_receipt_label": (CONSOLE, 'receiptCommand: "Receipt command"'),
    "local_harness_receipt_label_zh": (CONSOLE, 'receiptCommand: "回执命令"'),
    "local_harness_governed_readback": (CONSOLE, "localHarnessProof?.governed_launch_packet?.readback_command"),
    "local_harness_receipt_status_type": (LIVE_API, "receipt_status?: LocalHarnessReceiptStatus;"),
    "local_harness_receipt_summary_type": (LIVE_API, "receipt_summary?: LocalHarnessReceiptSummary;"),
    "local_harness_receipt_status_normalize": (LIVE_API, "local_harness_proof_launch_receipt_status"),
    "local_harness_receipt_summary_normalize": (LIVE_API, "local_harness_proof_launch_receipt_summary"),
    "local_harness_receipt_summary_panel": (CONSOLE, 'data-testid="worker-local-harness-receipt-summary"'),
    "local_harness_receipt_status_panel": (CONSOLE, 'data-testid="worker-local-harness-receipt-status"'),
    "local_harness_receipt_current": (CONSOLE, 'receiptCurrent: "Current receipts"'),
    "local_harness_receipt_current_zh": (CONSOLE, 'receiptCurrent: "当前回执"'),
    "local_harness_receipt_not_runtime": (CONSOLE, "receipt_presence_is_runtime_success"),
    "local_harness_proof_real_runtime": (CONSOLE, "fresh_real_runtime_adapters"),
    "local_harness_proof_mock_fallback": (CONSOLE, "fresh_mock_fallback"),
    "local_harness_proof_no_live": (CONSOLE, "localHarnessProof?.safety.live_execution_performed"),
    "local_harness_proof_token_omitted": (CONSOLE, "localHarnessProof?.safety.token_omitted"),
    "real_execution_mode_loader": (CONSOLE, "loadOperatorExecutionMode(selectedAdapter, confirmRun, 8)"),
    "real_start_check_loader": (CONSOLE, "loadOperatorStartCheck(selectedAdapter, 8)"),
    "local_install_packet_panel": (CONSOLE, 'data-testid="worker-local-install-packet"'),
    "local_install_service_install": (CONSOLE, "serviceInstall.preview_command"),
    "local_install_confirm_install": (CONSOLE, "serviceInstall.confirm_command"),
    "local_install_service_check": (CONSOLE, "serviceInstall.verify_command"),
    "local_install_no_service_load": (CONSOLE, "serviceInstall.loads_service"),
    "local_install_no_server_shell": (CONSOLE, "serviceInstall.server_executes_shell"),
    "local_install_first_safe": (CONSOLE, "firstSafeHasInstall"),
    "local_run_path_start_check_type": (LIVE_API, "local_run_path?: LocalRunPathStep[];"),
    "local_run_path_start_check_normalize": (LIVE_API, "const localRunPath = asArray<Record<string, unknown>>(raw.local_run_path)"),
    "service_control_audit_panel": (CONSOLE, 'data-testid="worker-service-control-audit"'),
    "service_control_step_source": (CONSOLE, "const serviceControlStep = localRunPath.find"),
    "service_control_preview_command": (CONSOLE, "serviceControlStep.command"),
    "service_control_verify_command": (CONSOLE, "serviceControlStep.verify_command"),
    "service_control_receipt_command": (CONSOLE, "serviceControlStep.receipt_verify_record_command"),
    "service_control_record_receipt": (CONSOLE, "recordOperatorActionReceipt({"),
    "service_control_record_readback": (CONSOLE, "recordOperatorActionControlReadback({"),
    "service_control_readback_gate": (CONSOLE, "serviceControlStep.control_readback_required"),
    "service_control_no_server_shell": (CONSOLE, "serviceControlStep?.server_executes_shell"),
    "service_control_no_ledger_write": (CONSOLE, "serviceControlStep?.writes_ledger"),
    "service_managed_loop_source": (CONSOLE, "localDeployment.service_managed_loop"),
    "service_managed_loop_panel": (CONSOLE, 'data-testid="worker-service-managed-loop"'),
    "service_managed_loop_ready": (CONSOLE, "serviceManagedLoop.service_managed_loop_ready"),
    "service_managed_active_loop_ready": (CONSOLE, "serviceManagedLoop.service_active_loop_ready"),
    "service_managed_service_loaded": (CONSOLE, "serviceManagedLoop.service_loaded"),
    "service_managed_installed_status": (CONSOLE, "serviceManagedLoop.installed_status"),
    "service_managed_checked_status": (CONSOLE, "serviceManagedLoop.checked_status"),
    "service_managed_no_service_load": (CONSOLE, "serviceManagedLoop.loads_service"),
    "managed_execution_path_source": (CONSOLE, "localDeployment.managed_execution_path"),
    "managed_execution_path_panel": (CONSOLE, 'data-testid="worker-managed-execution-path"'),
    "managed_execution_commands": (CONSOLE, "managedExecutionPath.commands"),
    "managed_execution_gates": (CONSOLE, "managedExecutionPath.gates"),
    "managed_execution_next_command": (CONSOLE, "const managedExecutionNextCommand"),
    "managed_execution_dispatch": (CONSOLE, "managedExecutionCommands.customer_worker_dispatch"),
    "managed_execution_evidence": (CONSOLE, "managedExecutionCommands.evidence_report"),
    "fleet_hygiene_apply_api": (CONSOLE, "applyWorkerFleetHygiene({"),
    "fleet_hygiene_panel": (CONSOLE, 'data-testid="worker-fleet-hygiene-panel"'),
    "fleet_hygiene_confirm_gate": (CONSOLE, "disabled={Boolean(busyAction) || !confirmCleanup || hygieneActionsAvailable === 0}"),
    "fleet_hygiene_no_live": (CONSOLE, "fleetHygiene?.live_execution_performed"),
    "fleet_hygiene_token_omitted": (CONSOLE, "fleetHygiene?.token_omitted"),
    "dispatch_api": (CONSOLE, "dispatchLocalWorkerOnce({"),
    "start_daemon_api": (CONSOLE, "startLocalWorkerDaemon({"),
    "restart_daemon_api": (CONSOLE, "restartLocalWorkerDaemon({"),
    "stop_daemon_api": (CONSOLE, 'stopLocalWorkerDaemon("all")'),
    "live_confirm_gate": (CONSOLE, 'const liveBlocked = selectedAdapter !== "mock" && !confirmRun;'),
    "live_confirm_disabled_dispatch": (CONSOLE, "disabled={Boolean(busyAction) || liveBlocked}"),
    "host_managed_selected_gate": (CONSOLE, "const selectedHostManaged = Boolean(selectedDaemon?.running && selectedDaemon.management_mode === \"host_stack\");"),
    "host_managed_stop_gate": (CONSOLE, "disabled={Boolean(busyAction) || hostManagedRunning || anyControlBlocked}"),
    "host_managed_start_gate": (CONSOLE, "disabled={Boolean(busyAction) || liveBlocked || selectedHostManaged || selectedControlBlocked}"),
    "host_managed_badge": (CONSOLE, 'daemon.management_mode === "host_stack"'),
    "host_managed_label_zh": (CONSOLE, 'hostManaged: "主机托管"'),
    "host_managed_control_hint_zh": (CONSOLE, 'hostManagedHint: "由 AgentOps 主机托管，请使用主机生命周期控制。"'),
    "process_identity_gate": (CONSOLE, "daemon.process_claim_active && daemon.control_allowed === false"),
    "process_identity_badge": (CONSOLE, "daemon.process_identity_verified ? copy.processVerified : copy.processUnverified"),
    "process_identity_label_zh": (CONSOLE, 'processUnverified: "进程身份异常"'),
    "process_identity_type": (LIVE_API, "process_identity_verified?: boolean;"),
    "process_identity_normalizer": (LIVE_API, "process_identity_verified: row.process_identity_verified === undefined"),
    "ledger_links_task": (CONSOLE, "to={`/admin/tasks/${lastDispatch.task_id}`}"),
    "ledger_links_run": (CONSOLE, "to={`/admin/runs/${lastDispatch.run_id}`}"),
    "safety_read_only": (CONSOLE, "executionMode?.safety.read_only"),
    "safety_no_server_shell": (CONSOLE, "executionMode?.safety.server_executes_shell"),
    "safety_token_omitted": (CONSOLE, "executionMode?.safety.token_omitted"),
    "api_worker_status": (LIVE_API, '"/workers/status"'),
    "api_worker_readiness": (LIVE_API, '"/workers/adapter-readiness"'),
    "api_worker_fleet_hygiene": (LIVE_API, '"/workers/fleet/hygiene"'),
}


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def main() -> int:
    failures: list[str] = []
    for name, (path, marker) in EXPECTED.items():
        require(path.exists(), f"{name}: missing file {path.relative_to(ROOT)}", failures)
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        require(marker in text, f"{name}: missing marker {marker}", failures)
    console_text = CONSOLE.read_text(encoding="utf-8") if CONSOLE.exists() else ""
    require("mock" in console_text and "hermes" in console_text and "openclaw" in console_text, "worker adapters missing", failures)
    require("Fleet hygiene" in console_text and "Fleet 清理" in console_text, "fleet hygiene labels missing", failures)
    require("confirm_cleanup" in LIVE_API.read_text(encoding="utf-8"), "hygiene apply must send confirm_cleanup", failures)
    require("mockData" not in console_text, "Worker Console must not import mockData", failures)
    require(not any(pattern.search(console_text) for pattern in SECRET_PATTERNS), "Worker Console contains token-like material", failures)
    output = {
        "operation": "worker_console_ui_smoke",
        "ok": not failures,
        "route": "/workspace/workers",
        "checks": len(EXPECTED) + 3,
        "failures": failures,
        "safety": {
            "static_only": True,
            "live_execution_performed": False,
            "token_omitted": True,
        },
    }
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
