from __future__ import annotations

import hashlib
import importlib
import json
import os
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


CREATED_AT = datetime(2026, 8, 11, 12, 30, tzinfo=timezone.utc)
REQUIRED_RUN_FILES = {
    "scenario.yaml",
    "agent_version.json",
    "transcript.json",
    "tool_calls.json",
    "timing.json",
    "evaluations.json",
    "evidence_manifest.json",
}
REQUIRED_CAMPAIGN_FILES = {
    "campaign_summary.json",
    "baseline_candidate_diff.json",
    "release_gate.json",
    "regression_cases.json",
}
SCENARIO_YAML = b"""schema_version: 1
id: appointment.basic
name: Basic appointment lookup
persona:
  language: en-US
  tone: neutral
  verbosity: short
initial_message: Please find booking booking-123.
goal:
  type: lookup
  booking_id: booking-123
  requested_slot: null
  requested_date: null
  requested_time: null
challenges: []
expectations:
  required_tool_calls:
    - lookup_booking
  forbidden_tool_calls: []
  must_confirm_before_mutation: false
  final_state:
    found: true
  max_turns: 6
  timeout_ms: 5000
tags:
  - basic
"""


def _evidence_modules():
    bundle = importlib.import_module("open_cekura.evidence.bundle")
    manifest = importlib.import_module("open_cekura.evidence.manifest")
    return bundle, manifest


def _inputs(bundle, manifest):
    models = importlib.import_module("open_cekura.domain.models")
    enums = importlib.import_module("open_cekura.domain.enums")
    agent_config = {
        "backend": "mock",
        "profile": "candidate",
        "temperature": 0.0,
        "tools": ["lookup_booking"],
    }
    agent_version = models.AgentVersion(
        schema_version=1,
        id="ocagentv_candidate",
        agent_id="ocagent_appointment",
        version="candidate",
        adapter_kind=enums.AdapterKind.MOCK,
        config_sha256=manifest.sha256_bytes(
            manifest.canonical_json_bytes(agent_config)
        ),
        created_at=CREATED_AT,
    )
    turn = models.ConversationTurn(
        schema_version=1,
        id="octurn_0001",
        run_id="ocrun_basic",
        turn_index=0,
        role=enums.TurnRole.USER,
        content="Please find booking booking-123.",
        created_at=CREATED_AT,
    )
    evaluation = models.EvaluationResult(
        schema_version=1,
        id="evr_task_success",
        run_id="ocrun_basic",
        evaluator_id="task_success.v1",
        status=enums.EvaluationStatus.PASS,
        score=1.0,
        threshold=1.0,
        reason_codes=[],
        evidence_refs=["turn:octurn_0001"],
        metadata={},
        mis_evaluation_id=None,
        created_at=CREATED_AT,
    )
    return bundle.RunBundleInputs(
        manifest_id="ocmanifest_basic",
        campaign_id="occampaign_candidate",
        run_id="ocrun_basic",
        mis_artifact_id="art_manifest_basic",
        mis_plan_evidence_manifest_id=None,
        git_commit_sha="a" * 40,
        environment=models.EvidenceEnvironment(
            os="Windows-11",
            python_version="3.11.9",
            node_version="v20.19.0",
        ),
        scenario_yaml=SCENARIO_YAML,
        agent_version=agent_version,
        agent_config=agent_config,
        transcript=(turn,),
        tool_calls=(),
        initial_state={"found": False},
        observed_final_state={"found": True},
        timing={
            "started_at": "2026-08-11T12:29:59Z",
            "finished_at": "2026-08-11T12:30:00Z",
            "duration_ms": 1_000,
            "timed_out": False,
        },
        evaluations=(evaluation,),
        started_at=CREATED_AT - timedelta(seconds=1),
        finished_at=CREATED_AT,
        final_state=enums.RunFinalState.PASS,
        created_at=CREATED_AT,
    )


def _campaign_inputs(bundle):
    return bundle.CampaignBundleInputs(
        campaign_id="occampaign_candidate",
        campaign_summary={"run_count": 1, "pass_rate": 1.0},
        baseline_candidate_diff=None,
        release_gate={"decision": "pass", "policy_version": "release_gate.v1"},
        regression_cases=(),
    )


def test_canonical_json_and_sha256_are_stable_utf8_bytes() -> None:
    _, manifest = _evidence_modules()
    left = {"z": [3, 2, 1], "message": "你好", "a": {"z": False, "a": 1}}
    right = {"a": {"a": 1, "z": False}, "message": "你好", "z": [3, 2, 1]}

    encoded = manifest.canonical_json_bytes(left)

    assert encoded == manifest.canonical_json_bytes(right)
    assert encoded == ('{"a":{"a":1,"z":false},"message":"你好","z":[3,2,1]}').encode(
        "utf-8"
    )
    assert not encoded.endswith(b"\n")
    assert manifest.sha256_bytes(encoded) == hashlib.sha256(encoded).hexdigest()


def test_write_run_bundle_creates_exact_canonical_files_and_hashes(
    tmp_path: Path,
) -> None:
    bundle, manifest_module = _evidence_modules()
    inputs = _inputs(bundle, manifest_module)
    root = tmp_path / "artifacts open cekura 证据"

    written = bundle.write_run_bundle(root, inputs)

    expected_path = root / inputs.campaign_id / inputs.run_id
    assert written.path == expected_path.resolve()
    assert written.manifest_path == written.path / "evidence_manifest.json"
    assert {path.name for path in written.path.iterdir()} == REQUIRED_RUN_FILES
    assert written.manifest == written.manifest.__class__.model_validate_json(
        written.manifest_path.read_bytes()
    )
    assert written.manifest_path.read_bytes() == written.manifest.canonical_json_bytes()
    assert set(written.manifest.artifacts) == REQUIRED_RUN_FILES - {
        "evidence_manifest.json"
    }
    for relative_path, expected_digest in written.manifest.artifacts.items():
        stored = (written.path / relative_path).read_bytes()
        assert hashlib.sha256(stored).hexdigest() == expected_digest

    assert written.manifest.scenario_sha256 == hashlib.sha256(SCENARIO_YAML).hexdigest()
    agent_envelope_bytes = (written.path / "agent_version.json").read_bytes()
    assert (
        written.manifest.agent_config_sha256
        == hashlib.sha256(agent_envelope_bytes).hexdigest()
    )
    agent_envelope = json.loads(agent_envelope_bytes)
    assert agent_envelope == {
        "schema_version": 1,
        "agent_version": inputs.agent_version.model_dump(mode="json"),
        "config": inputs.agent_config,
    }
    assert written.manifest.evaluator_versions == ["task_success.v1"]
    timing = json.loads((written.path / "timing.json").read_bytes())
    assert timing["initial_state"] == {"found": False}
    assert timing["observed_final_state"] == {"found": True}


def test_write_campaign_bundle_creates_four_hash_checked_canonical_files(
    tmp_path: Path,
) -> None:
    bundle, manifest_module = _evidence_modules()
    root = tmp_path / "artifacts"

    written = bundle.write_campaign_bundle(root, _campaign_inputs(bundle))

    assert {path.name for path in written.path.iterdir()} == REQUIRED_CAMPAIGN_FILES
    diff = json.loads((written.path / "baseline_candidate_diff.json").read_bytes())
    assert diff["baseline"] is None
    assert diff["comparison"] == "no_comparison"
    summary_bytes = (written.path / "campaign_summary.json").read_bytes()
    summary = json.loads(summary_bytes)
    assert summary["schema_version"] == 1
    assert summary["campaign_id"] == "occampaign_candidate"
    assert summary["summary"] == {"pass_rate": 1.0, "run_count": 1}
    assert set(summary["artifacts"]) == REQUIRED_CAMPAIGN_FILES - {
        "campaign_summary.json"
    }
    for name, digest in summary["artifacts"].items():
        stored = (written.path / name).read_bytes()
        assert digest == manifest_module.sha256_bytes(stored)
        assert stored == manifest_module.canonical_json_bytes(json.loads(stored))
    assert written.artifact_sha256["campaign_summary.json"] == (
        manifest_module.sha256_bytes(summary_bytes)
    )


def test_campaign_writer_rejects_sensitive_fields_without_creating_root(
    tmp_path: Path,
) -> None:
    bundle, _ = _evidence_modules()
    secret = "sk-campaign-secret"
    inputs = replace(
        _campaign_inputs(bundle),
        release_gate={"decision": "pass", "api_key": secret},
    )
    root = tmp_path / "artifacts"

    with pytest.raises(bundle.EvidenceInputError) as captured:
        bundle.write_campaign_bundle(root, inputs)

    assert secret not in str(captured.value)
    assert not root.exists()


@pytest.mark.parametrize(
    ("field_name", "unsafe_value"),
    [
        ("campaign_id", "../outside"),
        ("campaign_id", r"C:drive-relative"),
        ("campaign_id", r"\\server\share"),
        ("run_id", r"nested/run"),
        ("run_id", r"nested\run"),
        ("run_id", "CON"),
        ("run_id", "trailing."),
    ],
)
def test_write_run_bundle_rejects_unsafe_windows_path_components(
    tmp_path: Path,
    field_name: str,
    unsafe_value: str,
) -> None:
    bundle, manifest_module = _evidence_modules()
    inputs = replace(_inputs(bundle, manifest_module), **{field_name: unsafe_value})

    with pytest.raises(bundle.EvidencePathError):
        bundle.write_run_bundle(tmp_path / "artifacts", inputs)


def test_atomic_writes_fsync_before_same_directory_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle, manifest_module = _evidence_modules()
    inputs = _inputs(bundle, manifest_module)
    real_fsync = os.fsync
    real_replace = os.replace
    fsync_calls: list[int] = []
    replace_calls: list[tuple[Path, Path]] = []

    def observed_fsync(file_descriptor: int) -> None:
        fsync_calls.append(file_descriptor)
        real_fsync(file_descriptor)

    def observed_replace(source, destination) -> None:
        source_path = Path(source)
        destination_path = Path(destination)
        assert source_path.parent == destination_path.parent
        assert source_path.exists()
        assert len(fsync_calls) > len(replace_calls)
        replace_calls.append((source_path, destination_path))
        real_replace(source, destination)

    monkeypatch.setattr(bundle.os, "fsync", observed_fsync)
    monkeypatch.setattr(bundle.os, "replace", observed_replace)

    bundle.write_run_bundle(tmp_path / "artifacts", inputs)

    assert len(replace_calls) == len(REQUIRED_RUN_FILES)
    assert len(fsync_calls) >= len(replace_calls)
    assert all(source != destination for source, destination in replace_calls)


def test_atomic_write_removes_uncommitted_temp_file_after_replace_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle, manifest_module = _evidence_modules()
    inputs = _inputs(bundle, manifest_module)
    real_replace = os.replace

    def fail_timing_replace(source, destination) -> None:
        if Path(destination).name == "timing.json":
            raise PermissionError("simulated Windows sharing violation")
        real_replace(source, destination)

    monkeypatch.setattr(bundle.os, "replace", fail_timing_replace)

    with pytest.raises(bundle.EvidenceWriteError, match="timing.json"):
        bundle.write_run_bundle(tmp_path / "artifacts", inputs)

    run_path = tmp_path / "artifacts" / inputs.campaign_id / inputs.run_id
    assert not any(path.name.endswith(".tmp") for path in run_path.iterdir())


def test_atomic_write_rechecks_parent_before_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle, _ = _evidence_modules()
    root = tmp_path / "artifacts"
    destination_parent = root / "campaign" / "run"
    destination_parent.mkdir(parents=True)
    destination = destination_parent / "timing.json"
    real_is_link = bundle.is_symlink_or_reparse
    parent_checks = 0
    replace_called = False

    def simulated_link_swap(path: Path) -> bool:
        nonlocal parent_checks
        if Path(path) == destination_parent:
            parent_checks += 1
            return parent_checks >= 2
        return real_is_link(Path(path))

    def unexpected_replace(source, target) -> None:
        del source, target
        nonlocal replace_called
        replace_called = True

    monkeypatch.setattr(bundle, "is_symlink_or_reparse", simulated_link_swap)
    monkeypatch.setattr(bundle.os, "replace", unexpected_replace)

    with pytest.raises(bundle.EvidencePathError, match="symlink|reparse"):
        bundle._atomic_write_bytes(destination, b"{}", artifact_root=root)

    assert replace_called is False
    assert not destination.exists()


def test_writer_rejects_timing_that_disagrees_with_manifest_metadata(
    tmp_path: Path,
) -> None:
    bundle, manifest_module = _evidence_modules()
    inputs = _inputs(bundle, manifest_module)
    invalid = replace(
        inputs,
        timing={
            **inputs.timing,
            "finished_at": "2026-08-11T12:30:01Z",
        },
    )
    root = tmp_path / "artifacts"

    with pytest.raises(bundle.EvidenceInputError, match="timing"):
        bundle.write_run_bundle(root, invalid)

    assert not root.exists()


def test_writer_rejects_final_state_that_disagrees_with_deterministic_results(
    tmp_path: Path,
) -> None:
    bundle, manifest_module = _evidence_modules()
    enums = importlib.import_module("open_cekura.domain.enums")
    inputs = replace(
        _inputs(bundle, manifest_module),
        final_state=enums.RunFinalState.FAIL,
    )
    root = tmp_path / "artifacts"

    with pytest.raises(bundle.EvidenceInputError, match="final_state"):
        bundle.write_run_bundle(root, inputs)

    assert not root.exists()


def test_writer_rejects_sensitive_config_fields_without_echoing_values(
    tmp_path: Path,
) -> None:
    bundle, manifest_module = _evidence_modules()
    inputs = _inputs(bundle, manifest_module)
    secret = "sk-test-secret-that-must-not-leak"
    sensitive_config = {**inputs.agent_config, "OPENAI_API_KEY": secret}
    agent_version = inputs.agent_version.model_copy(
        update={
            "config_sha256": manifest_module.sha256_bytes(
                manifest_module.canonical_json_bytes(sensitive_config)
            )
        }
    )
    root = tmp_path / "artifacts"

    with pytest.raises(bundle.EvidenceInputError) as captured:
        bundle.write_run_bundle(
            root,
            replace(
                inputs,
                agent_version=agent_version,
                agent_config=sensitive_config,
            ),
        )

    assert secret not in str(captured.value)
    assert not root.exists()


@pytest.mark.parametrize("location", ["scenario", "transcript"])
def test_writer_rejects_inline_secret_patterns_before_creating_artifacts(
    tmp_path: Path,
    location: str,
) -> None:
    bundle, manifest_module = _evidence_modules()
    inputs = _inputs(bundle, manifest_module)
    if location == "scenario":
        secret = "sk-inline-scenario-secret"
        changed = inputs.scenario_yaml.replace(
            b"Please find booking booking-123.",
            f"OPENAI_API_KEY={secret}".encode(),
        )
        inputs = replace(inputs, scenario_yaml=changed)
    else:
        secret = "bearer-inline-transcript-secret"
        changed_turn = inputs.transcript[0].model_copy(
            update={"content": f"Authorization: Bearer {secret}"}
        )
        inputs = replace(inputs, transcript=(changed_turn,))
    root = tmp_path / "artifacts"

    with pytest.raises(bundle.EvidenceInputError) as captured:
        bundle.write_run_bundle(root, inputs)

    assert secret not in str(captured.value)
    assert not root.exists()


@pytest.mark.parametrize(
    "sensitive_text",
    [
        "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signaturevalue123",
        "Bearer bare-token-value-123456789",
        "https://alice:p4ssword-secret@example.invalid/private",
    ],
    ids=["github-token", "jwt", "bare-bearer", "credential-url"],
)
def test_writer_rejects_additional_inline_credential_patterns(
    tmp_path: Path,
    sensitive_text: str,
) -> None:
    bundle, manifest_module = _evidence_modules()
    inputs = _inputs(bundle, manifest_module)
    changed_turn = inputs.transcript[0].model_copy(
        update={"content": f"Do not persist {sensitive_text}"}
    )
    root = tmp_path / "artifacts"

    with pytest.raises(bundle.EvidenceInputError) as captured:
        bundle.write_run_bundle(root, replace(inputs, transcript=(changed_turn,)))

    assert sensitive_text not in str(captured.value)
    assert not root.exists()


@pytest.mark.parametrize(
    "field_name",
    [
        "auth_token",
        "apiKey",
        "clientSecret",
        "accessToken",
        "refreshToken",
        "sessionToken",
        "privateKey",
        "MIS_AGENT_TOKEN",
        "misAgentToken",
        "MIS_SESSION_ID",
        "misSessionId",
        "APIKey",
        "OAuthToken",
        "proxyAuthorization",
    ],
)
def test_sensitive_field_detection_handles_auth_tokens_and_camel_case(
    field_name: str,
) -> None:
    _, manifest_module = _evidence_modules()

    assert manifest_module.contains_sensitive_fields({field_name: "not-for-evidence"})


@pytest.mark.parametrize(
    "invalid_reference",
    [
        r"artifact:C:\Users\alice\.env?API_KEY=sekret",
        "turn:octurn_missing",
        "tool:lookup_booking",
        "final_state:/missing",
        "mis:banana:art_manifest_basic",
        "mis:evaluation:art_manifest_basic",
    ],
)
def test_writer_rejects_malformed_or_unresolved_evidence_references(
    tmp_path: Path,
    invalid_reference: str,
) -> None:
    bundle, manifest_module = _evidence_modules()
    inputs = _inputs(bundle, manifest_module)
    evaluation = inputs.evaluations[0].model_copy(
        update={"evidence_refs": [invalid_reference]}
    )
    root = tmp_path / "artifacts"

    with pytest.raises(bundle.EvidenceInputError) as captured:
        bundle.write_run_bundle(
            root,
            replace(inputs, evaluations=(evaluation,)),
        )

    assert invalid_reference not in str(captured.value)
    assert not root.exists()


def test_writer_accepts_type_correct_resolved_mis_reference(tmp_path: Path) -> None:
    bundle, manifest_module = _evidence_modules()
    inputs = _inputs(bundle, manifest_module)
    evaluation = inputs.evaluations[0].model_copy(
        update={"evidence_refs": ["mis:artifact:art_manifest_basic"]}
    )

    bundle.write_run_bundle(
        tmp_path / "artifacts",
        replace(inputs, evaluations=(evaluation,)),
    )


def test_writer_rejects_duplicate_logical_evidence_ids(tmp_path: Path) -> None:
    bundle, manifest_module = _evidence_modules()
    inputs = _inputs(bundle, manifest_module)
    duplicate_turn = inputs.transcript[0].model_copy(update={"turn_index": 1})
    root = tmp_path / "artifacts"

    with pytest.raises(bundle.EvidenceInputError, match="duplicate"):
        bundle.write_run_bundle(
            root,
            replace(inputs, transcript=(*inputs.transcript, duplicate_turn)),
        )

    assert not root.exists()


def test_writer_rejects_tool_call_with_dangling_turn_id(tmp_path: Path) -> None:
    bundle, manifest_module = _evidence_modules()
    models = importlib.import_module("open_cekura.domain.models")
    inputs = _inputs(bundle, manifest_module)
    dangling_call = models.ObservedToolCall(
        schema_version=1,
        id="octool_dangling",
        run_id=inputs.run_id,
        turn_id="octurn_missing",
        name="lookup_booking",
        arguments={"booking_id": "booking-123"},
        result={"found": True},
        error=None,
        is_mutation=False,
        duration_ms=1,
        mis_tool_call_id=None,
        created_at=CREATED_AT,
    )
    root = tmp_path / "artifacts"

    with pytest.raises(bundle.EvidenceInputError, match="turn"):
        bundle.write_run_bundle(root, replace(inputs, tool_calls=(dangling_call,)))

    assert not root.exists()


def test_writer_rejects_symlinked_bundle_ancestry(tmp_path: Path) -> None:
    bundle, manifest_module = _evidence_modules()
    inputs = _inputs(bundle, manifest_module)
    actual_root = tmp_path / "actual root"
    actual_root.mkdir()
    linked_root = tmp_path / "linked root"
    try:
        linked_root.symlink_to(actual_root, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"directory links are unavailable in this environment: {error}")

    with pytest.raises(bundle.EvidencePathError, match="symlink|reparse"):
        bundle.write_run_bundle(linked_root, inputs)
