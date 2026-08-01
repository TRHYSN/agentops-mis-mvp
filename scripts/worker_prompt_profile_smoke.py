#!/usr/bin/env python3
"""Verify worker prompt profiles are classified and ledger-visible without raw prompt storage."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agentops_mis_cli.worker import build_task_prompt_bundle  # noqa: E402

WORKER = ROOT / "agentops_mis_cli" / "worker.py"
APPROVAL_WALL = ROOT / "agentops_mis_core" / "approval_wall.py"

SECRET_PATTERNS = [
    re.compile(r"Authorization:", re.IGNORECASE),
    re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"agtok_[A-Za-z0-9_]+"),
    re.compile(r"agtsess_[A-Za-z0-9_]+"),
    re.compile(r"sk-[A-Za-z0-9]{12,}"),
    re.compile(r"ntn_[A-Za-z0-9]{12,}"),
]


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def main() -> int:
    failures: list[str] = []
    cases = [
        (
            "coding",
            {
                "title": "Improve Pixel Office React UI",
                "description": "Change TSX code, patch the repo, and list verification commands.",
                "acceptance_criteria": "Build passes and patch evidence is recorded.",
                "risk_level": "medium",
            },
            "local_coding_project_summary",
        ),
        (
            "knowledge",
            {
                "title": "Build an AI knowledge-base Q&A bot",
                "description": "Prepare source documents for retrieval and File Search style evaluation.",
                "acceptance_criteria": "Return retrieval design and evaluation questions.",
                "risk_level": "medium",
            },
            "knowledge_base_delivery_summary",
        ),
        (
            "review",
            {
                "title": "Audit customer delivery evidence",
                "description": "Review quality gates and missing approval evidence.",
                "acceptance_criteria": "Return pass/fail gate assessment.",
                "risk_level": "high",
            },
            "review_quality_gate_summary",
        ),
        (
            "general",
            {
                "title": "Plan a customer onboarding task",
                "description": "Summarize the work and next actions.",
                "acceptance_criteria": "Return concise delivery notes.",
                "risk_level": "low",
            },
            "general_customer_delivery_summary",
        ),
    ]

    profile_ids: dict[str, str] = {}
    prompt_hash_markers = 0
    combined_prompt = ""
    for label, task, expected in cases:
        prompt, profile = build_task_prompt_bundle(task, adapter="hermes")
        profile_ids[label] = profile.get("profile_id") or ""
        combined_prompt += "\n" + prompt
        require(profile.get("profile_id") == expected, f"{label} profile mismatch: {profile}", failures)
        require(profile.get("version") == "worker_prompt_profiles_v1", f"{label} profile version mismatch: {profile}", failures)
        require(bool(profile.get("profile_hash")), f"{label} missing profile hash: {profile}", failures)
        require(profile.get("raw_prompt_omitted") is True, f"{label} raw prompt omission missing: {profile}", failures)
        require("profile_hash=" in prompt and "output_contract=" in prompt, f"{label} prompt missing profile contract: {prompt}", failures)
        require("raw profile prompt body omitted" in prompt, f"{label} prompt missing raw omission boundary", failures)
        prompt_hash_markers += prompt.count("profile_hash=")

    service_task = {
        "task_id": "tsk_service_context_smoke",
        "title": "Audit launchd daemon loop proof",
        "description": "Review local service-loop evidence for a Hermes worker task.",
        "acceptance_criteria": "The prompt must include only safe service receipt and readback proof.",
        "risk_level": "low",
        "_knowledge_retrieval_evidence": {
            "packet_status": "ready",
            "packet_hash": "kph_service_context",
            "query_hash": "kqh_service_context",
            "paths": ["PROJECT_SPEC.md"],
            "metrics": {"recall_at_5": 1.0, "mrr": 1.0},
        },
        "_loop_supervision_gate": {
            "adapter": "hermes",
            "agent_id": "agt_worker_daemon_hermes",
            "task_id": "tsk_service_context_smoke",
            "status": "record_first",
            "ready_for_live_dispatch": True,
            "supervision_hash": "supervision_hash_smoke",
            "local_deployment": {"local_run_path_present": True},
            "service_managed_loop": {
                "adapter": "hermes",
                "manager": "launchd",
                "service_managed_loop_ready": True,
                "service_loaded": True,
                "service_active_loop_ready": True,
                "active_loop_status": "active",
                "receipt_id": "oar_smoke_receipt",
                "control_readback_id": "ocr_smoke_readback",
                "readback_verification_status": "passed",
            },
        },
        "_intake_plan_evidence": {
            "plan_id": "plan_auto_intake_smoke",
            "plan_verified": True,
            "plan_reused_from_intake": True,
            "source": "task_pull.intake",
            "verification_source": "/api/agent-gateway/agent-plans/plan_auto_intake_smoke/verify",
            "auto_plan_intake_supported": True,
            "raw_plan_body_omitted": True,
            "raw_prompt_omitted": True,
            "raw_response_omitted": True,
            "token_omitted": True,
        },
    }
    service_prompt, _service_profile = build_task_prompt_bundle(service_task, adapter="hermes")
    combined_prompt += "\n" + service_prompt
    service_markers = [
        "本地服务循环证据",
        "service_managed_loop_ready=True",
        "service_loaded=True",
        "service_active_loop_ready=True",
        "manager=launchd",
        "receipt_id=oar_smoke_receipt",
        "control_readback_id=ocr_smoke_readback",
        "readback_status=passed",
        "proof_source=/api/operator/loop-supervision",
        "server_shell=false",
        "raw_service_template/prompt/response/token omitted",
        "执行前计划证据",
        "plan_id=plan_auto_intake_smoke",
        "plan_verified=True",
        "plan_reused_from_intake=True",
        "source=task_pull.intake",
        "auto_plan_intake_supported=True",
        "raw_plan_body/prompt/response/token omitted",
    ]
    for marker in service_markers:
        require(marker in service_prompt, f"service-loop prompt missing marker {marker}: {service_prompt}", failures)
    require("ProgramArguments" not in service_prompt, "service prompt should not expose raw launchd template", failures)
    require("AGENTOPS_API_KEY" not in service_prompt, "service prompt should not expose API-key env", failures)
    require("task_understanding" not in service_prompt, "intake plan prompt should not expose raw plan body", failures)

    current_claim_task = dict(service_task)
    current_claim_task["_loop_supervision_gate"] = {
        **service_task["_loop_supervision_gate"],
        "ready_for_live_dispatch": False,
        "service_managed_loop": {
            **service_task["_loop_supervision_gate"]["service_managed_loop"],
            "service_managed_loop_ready": False,
            "service_loaded": False,
            "service_active_loop_ready": False,
            "active_loop_status": "unverified",
        },
    }
    current_claim_task["_worker_execution_fact"] = {
        "adapter": "hermes",
        "agent_id": "agt_worker_local_stack_hermes",
        "task_id": "tsk_service_context_smoke",
        "worker_process_active": True,
        "gateway_task_claim_succeeded": True,
        "evidence_source": "agent_gateway.task_claim.current_process",
        "os_service_ownership_inferred": False,
        "raw_prompt_omitted": True,
        "raw_response_omitted": True,
        "token_omitted": True,
    }
    current_claim_prompt, _current_claim_profile = build_task_prompt_bundle(
        current_claim_task,
        adapter="hermes",
    )
    combined_prompt += "\n" + current_claim_prompt
    current_claim_markers = [
        "当前 Worker 执行事实",
        "worker_process_active=True",
        "gateway_task_claim_succeeded=True",
        "evidence_source=agent_gateway.task_claim.current_process",
        "os_service_ownership_inferred=False",
        "当前进程已成功认领本任务",
        "不能否定已发生的当前 claim",
        "不能据此推断 launchd/systemd ownership",
        "raw_service_template/prompt/response/token omitted",
    ]
    for marker in current_claim_markers:
        require(
            marker in current_claim_prompt,
            f"current claim prompt missing marker {marker}: {current_claim_prompt}",
            failures,
        )
    require(
        "service_managed_loop_ready=False" in current_claim_prompt,
        "current claim smoke must preserve stale historical service evidence",
        failures,
    )

    worker_source = WORKER.read_text(encoding="utf-8")
    approval_wall_source = APPROVAL_WALL.read_text(encoding="utf-8")
    source_markers = [
        "PROMPT_PROFILE_VERSION",
        "select_task_prompt_profile",
        "build_task_prompt_bundle",
        "prompt_profile_id",
        "prompt_profile_version",
        "prompt_profile_hash",
        "\"prompt_profile\"",
        "agent_worker.external_write_prepared_action_required",
        "raw_prompt_omitted",
        "raw_response_omitted",
        "token_omitted",
        "_loop_supervision_gate",
        "_worker_execution_fact",
        "service_managed_loop",
        "control_readback_id",
        "_intake_plan_evidence",
        "plan_reused_from_intake",
        "auto_plan_intake_supported",
    ]
    for marker in source_markers:
        require(marker in worker_source, f"worker source missing marker: {marker}", failures)
    claim_call_position = worker_source.find('client.post(f"/api/agent-gateway/tasks/{task_id}/claim"')
    current_fact_position = worker_source.find('task["_worker_execution_fact"] = {')
    adapter_execution_position = worker_source.find("result = execute_adapter_with_retries(task, args)")
    current_claim_fact_injected_after_claim = (
        claim_call_position >= 0
        and claim_call_position < current_fact_position < adapter_execution_position
    )
    require(
        current_claim_fact_injected_after_claim,
        "current worker execution fact must be injected after successful claim and before adapter execution",
        failures,
    )
    for marker in ["prompt_profile_id", "prompt_profile_version", "prompt_profile_hash"]:
        require(marker in approval_wall_source, f"approval wall safe metadata missing marker: {marker}", failures)
    require(worker_source.count('"prompt_profile_id": result.prompt_profile_id') >= 3, "tool/eval/audit profile metadata not wired from AdapterResult", failures)
    require(worker_source.count('"prompt_profile_id": prompt_profile.get("profile_id")') >= 2, "external-write prepared-action profile metadata missing", failures)
    require(prompt_hash_markers == len(cases), f"profile hash should appear once per generated prompt: {prompt_hash_markers}", failures)
    secret_hits = [pattern.pattern for pattern in SECRET_PATTERNS if pattern.search(worker_source + combined_prompt)]
    require(not secret_hits, f"secret-like pattern found in worker prompt profile path: {secret_hits}", failures)

    output = {
        "operation": "worker_prompt_profile_smoke",
        "ok": not failures,
        "profile_ids": profile_ids,
        "profile_count": len(set(profile_ids.values())),
        "approval_wall_profile_metadata_safe": all(marker in approval_wall_source for marker in ["prompt_profile_id", "prompt_profile_version", "prompt_profile_hash"]),
        "service_loop_context_prompted": all(marker in service_prompt for marker in service_markers),
        "current_claim_fact_prompted": all(marker in current_claim_prompt for marker in current_claim_markers),
        "current_claim_fact_injected_after_claim": current_claim_fact_injected_after_claim,
        "source_markers_checked": len(source_markers),
        "failures": failures,
        "safety": {
            "read_only": True,
            "ledger_mutated": False,
            "live_execution_performed": False,
            "raw_prompt_omitted": True,
            "raw_response_omitted": True,
            "token_omitted": True,
        },
    }
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
