from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from importlib import import_module

import pytest
from pydantic import BaseModel, ValidationError


CREATED_AT = datetime(2026, 8, 11, 12, 30, tzinfo=timezone.utc)


def domain_modules():
    models = import_module("open_cekura.domain.models")
    enums = import_module("open_cekura.domain.enums")
    ids = import_module("open_cekura.domain.ids")
    return models, enums, ids


def domain_objects():
    models, enums, _ = domain_modules()
    shared = {"schema_version": 1, "created_at": CREATED_AT}

    return [
        models.AgentUnderTest(
            **shared,
            id="ocagent_appointment",
            name="Appointment agent",
            description="Books and changes appointments.",
            workspace_id="ws_demo",
        ),
        models.AgentVersion(
            **shared,
            id="ocagentv_candidate",
            agent_id="ocagent_appointment",
            version="candidate",
            adapter_kind=enums.AdapterKind.MOCK,
            config_sha256="a" * 64,
        ),
        models.ScenarioSuite(
            **shared,
            id="ocsuite_appointments",
            name="Appointment reliability",
            description="Deterministic appointment scenarios.",
        ),
        models.Persona(
            **shared,
            id="ocpersona_impatient",
            name="Impatient caller",
            language="en-US",
            tone="impatient",
            verbosity=enums.Verbosity.SHORT,
        ),
        models.Scenario(
            **shared,
            id="appointment.change_after_interrupt",
            suite_id="ocsuite_appointments",
            persona_id="ocpersona_impatient",
            name="Change after interruption",
            initial_message="I need to move my appointment.",
            goal_type="reschedule",
            source_sha256="b" * 64,
        ),
        models.Campaign(
            **shared,
            id="occampaign_candidate",
            agent_version_id="ocagentv_candidate",
            scenario_suite_id="ocsuite_appointments",
            status=enums.CampaignStatus.COMPLETED,
            mis_task_id="tsk_campaign",
            mis_plan_id="ap_plan_campaign",
        ),
        models.ConversationRun(
            **shared,
            id="ocrun_change_after_interrupt",
            campaign_id="occampaign_candidate",
            scenario_id="appointment.change_after_interrupt",
            agent_version_id="ocagentv_candidate",
            status=enums.RunFinalState.PASS,
            mis_run_id="run_gw_campaign",
        ),
        models.ConversationTurn(
            **shared,
            id="octurn_0001",
            run_id="ocrun_change_after_interrupt",
            turn_index=0,
            role=enums.TurnRole.USER,
            content="I need to move my appointment.",
        ),
        models.ObservedToolCall(
            **shared,
            id="octool_lookup",
            run_id="ocrun_change_after_interrupt",
            turn_id="octurn_0001",
            name="lookup_booking",
            arguments={"booking_id": "booking-123"},
            result={"found": True},
            error=None,
            is_mutation=False,
            duration_ms=12,
            mis_tool_call_id="tc_gw_lookup",
        ),
        models.EvaluationResult(
            **shared,
            id="evr_required_calls",
            run_id="ocrun_change_after_interrupt",
            evaluator_id="required_tool_calls.v1",
            status=enums.EvaluationStatus.PASS,
            score=1.0,
            threshold=1.0,
            reason_codes=[],
            evidence_refs=["tool_call:octool_lookup"],
            metadata={"required_count": 1},
            mis_evaluation_id="eval_gw_required_calls",
        ),
        models.FailureCase(
            **shared,
            id="ocfailure_confirmation",
            run_id="ocrun_change_after_interrupt",
            scenario_id="appointment.change_after_interrupt",
            evaluation_result_id="evr_confirmation",
            reason_code="confirmation_missing",
            expected={"confirmed": True},
            observed={"confirmed": False},
            evidence_refs=["turn:octurn_0001"],
        ),
        models.FailureCluster(
            **shared,
            id="occluster_confirmation",
            campaign_id="occampaign_candidate",
            signature="confirmation_missing:update_booking",
            failure_case_ids=["ocfailure_confirmation"],
        ),
        models.RegressionCase(
            **shared,
            id="ocregression_confirmation",
            failure_case_id="ocfailure_confirmation",
            scenario_id="appointment.change_after_interrupt",
            source_run_id="ocrun_change_after_interrupt",
            name="Confirm before update",
            original_input={"message": "Move my appointment."},
            expected={"confirmed": True},
            observed={"confirmed": False},
            reason_code="confirmation_missing",
            evaluator_id="confirmation_before_mutation.v1",
            evidence_refs=["evaluation:evr_confirmation"],
            mis_memory_id="mem_gw_confirmation",
        ),
        models.ReleaseGateDecision(
            **shared,
            id="ocgate_candidate",
            campaign_id="occampaign_candidate",
            baseline_campaign_id="occampaign_baseline",
            decision=enums.GateDecision.PASS,
            policy_version="release_gate.v1",
            blockers=[],
            warnings=[],
            metrics={"task_success_rate": 1.0},
            evidence_refs=["artifact:campaign_summary.json"],
            mis_approval_id="ap_gate_candidate",
        ),
        models.EvidenceManifest(
            **shared,
            id="ocmanifest_change_after_interrupt",
            campaign_id="occampaign_candidate",
            run_id="ocrun_change_after_interrupt",
            mis_artifact_id="art_gw_manifest",
            mis_plan_evidence_manifest_id="pem_campaign",
            git_commit_sha="c" * 40,
            environment=models.EvidenceEnvironment(
                os="Windows-11",
                python_version="3.11.9",
                node_version="v20.19.0",
            ),
            scenario_sha256="d" * 64,
            agent_config_sha256="e" * 64,
            evaluator_versions=["task_success.v1"],
            artifacts={
                "scenario.yaml": "d" * 64,
                "agent_version.json": "e" * 64,
                "transcript.json": "f" * 64,
            },
            started_at=CREATED_AT - timedelta(seconds=1),
            finished_at=CREATED_AT,
            final_state=enums.RunFinalState.PASS,
        ),
    ]


def test_stable_ids_are_deterministic_and_length_delimited() -> None:
    _, _, ids = domain_modules()

    first = ids.stable_id("ocrun", "campaign-a", "scenario-b")

    assert first == ids.stable_id("ocrun", "campaign-a", "scenario-b")
    assert first.startswith("ocrun_")
    assert len(first.removeprefix("ocrun_")) == 24
    assert first != ids.stable_id("ocrun", "campaign-a", "scenario-c")
    assert ids.stable_id("ocrun", "ab", "c") != ids.stable_id("ocrun", "a", "bc")


def test_all_fifteen_domain_objects_round_trip_canonical_json() -> None:
    objects = domain_objects()

    assert len(objects) == 15
    assert len({type(item).__name__ for item in objects}) == 15

    for item in objects:
        assert isinstance(item, BaseModel)
        assert item.schema_version == 1
        assert item.id
        assert item.created_at.tzinfo is timezone.utc

        encoded = item.canonical_json_bytes()
        decoded = json.loads(encoded)

        assert decoded["created_at"].endswith("Z")
        assert encoded == item.__class__.model_validate_json(encoded).canonical_json_bytes()
        assert encoded == json.dumps(
            decoded,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")


def test_derived_objects_keep_explicit_parent_ids() -> None:
    objects = {type(item).__name__: item for item in domain_objects()}
    expected_parents = {
        "AgentVersion": {"agent_id": "ocagent_appointment"},
        "Scenario": {
            "suite_id": "ocsuite_appointments",
            "persona_id": "ocpersona_impatient",
        },
        "Campaign": {
            "agent_version_id": "ocagentv_candidate",
            "scenario_suite_id": "ocsuite_appointments",
        },
        "ConversationRun": {
            "campaign_id": "occampaign_candidate",
            "scenario_id": "appointment.change_after_interrupt",
            "agent_version_id": "ocagentv_candidate",
        },
        "ConversationTurn": {"run_id": "ocrun_change_after_interrupt"},
        "ObservedToolCall": {
            "run_id": "ocrun_change_after_interrupt",
            "turn_id": "octurn_0001",
        },
        "EvaluationResult": {"run_id": "ocrun_change_after_interrupt"},
        "FailureCase": {
            "run_id": "ocrun_change_after_interrupt",
            "scenario_id": "appointment.change_after_interrupt",
            "evaluation_result_id": "evr_confirmation",
        },
        "FailureCluster": {"campaign_id": "occampaign_candidate"},
        "RegressionCase": {
            "failure_case_id": "ocfailure_confirmation",
            "scenario_id": "appointment.change_after_interrupt",
            "source_run_id": "ocrun_change_after_interrupt",
        },
        "ReleaseGateDecision": {
            "campaign_id": "occampaign_candidate",
            "baseline_campaign_id": "occampaign_baseline",
        },
        "EvidenceManifest": {
            "campaign_id": "occampaign_candidate",
            "run_id": "ocrun_change_after_interrupt",
        },
    }

    for class_name, parents in expected_parents.items():
        for field_name, expected in parents.items():
            assert getattr(objects[class_name], field_name) == expected


def test_models_forbid_unknown_fields_and_coercion() -> None:
    for item in domain_objects():
        payload = item.model_dump(mode="python")
        payload["unknown_contract_field"] = True
        with pytest.raises(ValidationError, match="unknown_contract_field"):
            item.__class__.model_validate(payload)

        payload = item.model_dump(mode="python")
        payload["schema_version"] = "1"
        with pytest.raises(ValidationError, match="schema_version"):
            item.__class__.model_validate(payload)


def test_models_require_utc_aware_created_at() -> None:
    models, _, _ = domain_modules()
    base = {
        "schema_version": 1,
        "id": "ocagent_invalid_time",
        "name": "Invalid time",
        "description": "",
        "workspace_id": None,
    }

    with pytest.raises(ValidationError, match="UTC-aware"):
        models.AgentUnderTest(**base, created_at=datetime(2026, 8, 11, 12, 30))

    with pytest.raises(ValidationError, match="UTC-aware"):
        models.AgentUnderTest(
            **base,
            created_at=datetime(
                2026,
                8,
                11,
                20,
                30,
                tzinfo=timezone(timedelta(hours=8)),
            ),
        )


def test_evaluation_status_and_score_are_consistent() -> None:
    models, enums, _ = domain_modules()
    base = {
        "schema_version": 1,
        "id": "evr_error",
        "run_id": "ocrun_error",
        "evaluator_id": "task_success.v1",
        "threshold": 1.0,
        "reason_codes": ["adapter_error"],
        "evidence_refs": [],
        "metadata": {},
        "mis_evaluation_id": None,
        "created_at": CREATED_AT,
    }

    with pytest.raises(ValidationError, match="score must be null"):
        models.EvaluationResult(
            **base,
            status=enums.EvaluationStatus.ERROR,
            score=0.0,
        )

    with pytest.raises(ValidationError, match="score is required"):
        models.EvaluationResult(
            **base,
            status=enums.EvaluationStatus.FAIL,
            score=None,
        )


def test_manifest_requires_matching_core_artifact_hashes() -> None:
    models, enums, _ = domain_modules()

    with pytest.raises(ValidationError, match="scenario_sha256"):
        models.EvidenceManifest(
            schema_version=1,
            id="ocmanifest_invalid",
            campaign_id="occampaign_invalid",
            run_id="ocrun_invalid",
            mis_artifact_id=None,
            mis_plan_evidence_manifest_id=None,
            git_commit_sha="a" * 40,
            environment=models.EvidenceEnvironment(
                os="Windows-11",
                python_version="3.11.9",
                node_version="v20.19.0",
            ),
            scenario_sha256="b" * 64,
            agent_config_sha256="c" * 64,
            evaluator_versions=["task_success.v1"],
            artifacts={
                "scenario.yaml": "d" * 64,
                "agent_version.json": "c" * 64,
            },
            started_at=CREATED_AT,
            finished_at=CREATED_AT,
            final_state=enums.RunFinalState.ERROR,
            created_at=CREATED_AT,
        )
