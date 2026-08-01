#!/usr/bin/env python3
"""Verify one-shot workers do not hard-block on advisory service-loop closure."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from agentops_mis_cli.worker import compact_worker_loop_supervision_gate


def main() -> int:
    payload = {
        "operation": "operator_loop_supervision",
        "status": "attention",
        "summary": {"current_code_ok": True},
        "items": [{
            "adapter": "hermes",
            "status": "record_first",
            "can_preview_loop": True,
            "can_confirm_bounded_loop": True,
            "should_record_before_execute": True,
            "ready_for_live_dispatch": False,
            "blockers": [],
            "attention": ["service_managed_loop:record_service_control_receipt"],
            "safety": {"server_executes_shell": False},
            "plan_quality": {"issue_count": 0, "hard_run_start_gate": False},
            "service_closure": {
                "required": True,
                "status": "attention",
                "step": "record_service_control_receipt",
                "hard_run_start_gate": False,
                "server_executes_shell": False,
            },
            "gates": [
                {"id": "current_code", "ok": True, "status": "pass"},
                {"id": "bounded_confirm", "ok": True, "status": "pass", "confirm_required": True},
                {"id": "plan_quality", "ok": True, "status": "pass", "hard_run_start_gate": False},
                {"id": "service_managed_loop", "ok": False, "status": "attention", "hard_run_start_gate": False},
                {"id": "server_shell_boundary", "ok": True, "status": "pass", "server_executes_shell": False},
            ],
            "local_deployment": {
                "local_run_path": {
                    "recommended_adapter": "hermes",
                    "safety": {"server_executes_shell": False},
                },
                "service_managed_loop": {"adapter": "hermes", "status": "attention"},
            },
        }],
    }
    soft = compact_worker_loop_supervision_gate(
        payload,
        adapter="hermes",
        task_id="tsk_soft_service_gate",
        agent_id="agt_soft_service_gate",
    )
    hard_payload = json.loads(json.dumps(payload))
    hard_payload["items"][0]["service_closure"]["hard_run_start_gate"] = True
    hard_payload["items"][0]["gates"][3]["hard_run_start_gate"] = True
    hard = compact_worker_loop_supervision_gate(
        hard_payload,
        adapter="hermes",
        task_id="tsk_hard_service_gate",
        agent_id="agt_hard_service_gate",
    )
    failures = []
    if soft.get("ok") is not True or (soft.get("service_closure") or {}).get("hard_run_start_gate") is not False:
        failures.append(f"advisory service closure blocked one-shot worker: {soft}")
    if hard.get("ok") is not False or (hard.get("service_closure") or {}).get("hard_run_start_gate") is not True:
        failures.append(f"explicit hard service closure did not block worker: {hard}")
    print(json.dumps({
        "ok": not failures,
        "operation": "worker_loop_supervision_soft_gate_smoke",
        "soft_gate_allowed": soft.get("ok") is True,
        "hard_gate_blocked": hard.get("ok") is False,
        "real_runtime_called": False,
        "token_omitted": True,
        "failures": failures,
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
