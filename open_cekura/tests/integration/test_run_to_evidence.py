from __future__ import annotations

import hashlib
import importlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


CREATED_AT = datetime(2026, 8, 11, 12, 30, tzinfo=timezone.utc)
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


def _write_bundle(tmp_path: Path, *, include_campaign: bool = True):
    bundle, manifest = _evidence_modules()
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
        evidence_refs=[
            "turn:octurn_0001",
            "artifact:transcript.json",
            "expectation:required_tool_calls[0]",
            "final_state:/found",
        ],
        metadata={},
        mis_evaluation_id=None,
        created_at=CREATED_AT,
    )
    optional_judge = models.EvaluationResult(
        schema_version=1,
        id="evr_optional_judge",
        run_id="ocrun_basic",
        evaluator_id="llm_judge.openai.v1",
        status=enums.EvaluationStatus.SKIPPED,
        score=None,
        threshold=None,
        reason_codes=["judge_credentials_missing"],
        evidence_refs=[],
        metadata={"provider": "openai", "prompt_version": "judge.v1"},
        mis_evaluation_id=None,
        created_at=CREATED_AT,
    )
    inputs = bundle.RunBundleInputs(
        manifest_id="ocmanifest_basic",
        campaign_id="occampaign_candidate",
        run_id="ocrun_basic",
        mis_artifact_id="art_manifest_basic",
        mis_plan_evidence_manifest_id="pem_campaign",
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
        evaluations=(evaluation, optional_judge),
        started_at=CREATED_AT - timedelta(seconds=1),
        finished_at=CREATED_AT,
        final_state=enums.RunFinalState.PASS,
        created_at=CREATED_AT,
    )
    root = tmp_path / "artifacts open-cekura 证据"
    written = bundle.write_run_bundle(root, inputs)
    if include_campaign:
        _write_campaign(bundle, root)
    return bundle, manifest, root, written


def _issue_codes(report) -> set[str]:
    return {issue.code for issue in report.issues}


def _write_campaign(bundle, root: Path):
    return bundle.write_campaign_bundle(
        root,
        bundle.CampaignBundleInputs(
            campaign_id="occampaign_candidate",
            campaign_summary={"run_count": 1, "pass_rate": 1.0},
            baseline_candidate_diff=None,
            release_gate={"decision": "pass", "policy_version": "release_gate.v1"},
            regression_cases=(),
        ),
    )


def test_run_only_verification_avoids_campaign_gate_cycle(tmp_path: Path) -> None:
    bundle, _, root, _ = _write_bundle(tmp_path, include_campaign=False)

    run_report = bundle.verify_run_bundles(root, "occampaign_candidate")
    full_report = bundle.verify_campaign(root, "occampaign_candidate")

    assert run_report.ok is True
    assert full_report.ok is False
    assert "missing_campaign_artifact" in _issue_codes(full_report)


def test_full_campaign_verification_accepts_bundle_and_rejects_tamper(
    tmp_path: Path,
) -> None:
    bundle, _, root, _ = _write_bundle(tmp_path)
    written = _write_campaign(bundle, root)

    assert bundle.verify_campaign(root, "occampaign_candidate").ok is True

    release_gate_path = written.path / "release_gate.json"
    release_gate_path.write_bytes(b'{"decision":"fail"}')
    report = bundle.verify_campaign(root, "occampaign_candidate")

    assert report.ok is False
    assert "campaign_hash_mismatch" in _issue_codes(report)


def test_full_campaign_verification_fails_closed_when_campaign_becomes_unreadable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle, _, root, written = _write_bundle(tmp_path)
    campaign_path = written.path.parent
    real_iterdir = Path.iterdir
    campaign_reads = 0

    def transient_failure(path: Path):
        nonlocal campaign_reads
        if path == campaign_path:
            campaign_reads += 1
            if campaign_reads == 2:
                raise OSError("simulated campaign directory race")
        return real_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", transient_failure)

    report = bundle.verify_campaign(root, "occampaign_candidate")

    assert report.ok is False
    assert "campaign_unreadable" in _issue_codes(report)


@pytest.mark.parametrize("scope", ["manifest", "campaign"])
def test_verification_rejects_escaped_lone_surrogate_without_crashing(
    tmp_path: Path,
    scope: str,
) -> None:
    bundle, _, root, written = _write_bundle(tmp_path)
    if scope == "manifest":
        content = written.manifest_path.read_bytes()
        written.manifest_path.write_bytes(content[:-1] + b',"note":"\\ud800"}')
    else:
        (written.path.parent / "release_gate.json").write_bytes(b'{"note":"\\ud800"}')

    report = bundle.verify_campaign(root, "occampaign_candidate")

    assert report.ok is False
    assert {"malformed_manifest", "invalid_campaign_json"}.intersection(
        _issue_codes(report)
    )


@pytest.mark.parametrize("scope", ["manifest", "campaign", "run_artifact"])
def test_verification_rejects_excessive_json_depth_without_crashing(
    tmp_path: Path,
    scope: str,
) -> None:
    bundle, manifest, root, written = _write_bundle(tmp_path)
    deeply_nested = b"[" * 10_000 + b"0" + b"]" * 10_000
    if scope == "manifest":
        written.manifest_path.write_bytes(deeply_nested)
    elif scope == "campaign":
        (written.path.parent / "release_gate.json").write_bytes(deeply_nested)
    else:
        evaluations_path = written.path / "evaluations.json"
        evaluations_path.write_bytes(deeply_nested)

        def update_covered_hash(payload: dict[str, object]) -> None:
            artifacts = payload["artifacts"]
            assert isinstance(artifacts, dict)
            artifacts["evaluations.json"] = manifest.sha256_bytes(deeply_nested)

        _rewrite_manifest(manifest, written.manifest_path, update_covered_hash)

    report = bundle.verify_campaign(root, "occampaign_candidate")

    assert report.ok is False
    assert {
        "malformed_manifest",
        "invalid_campaign_json",
        "invalid_json_artifact",
    }.intersection(_issue_codes(report))


@pytest.mark.parametrize(
    "artifact_name",
    ["transcript.json", "tool_calls.json", "evaluations.json"],
)
def test_verification_rejects_rehashed_duplicate_logical_ids(
    tmp_path: Path,
    artifact_name: str,
) -> None:
    bundle, manifest, root, written = _write_bundle(tmp_path)
    artifact_path = written.path / artifact_name
    payload = json.loads(artifact_path.read_bytes())
    if artifact_name == "tool_calls.json":
        models = importlib.import_module("open_cekura.domain.models")
        call = models.ObservedToolCall(
            schema_version=1,
            id="octool_duplicate",
            run_id="ocrun_basic",
            turn_id="octurn_0001",
            name="lookup_booking",
            arguments={"booking_id": "booking-123"},
            result={"found": True},
            error=None,
            is_mutation=False,
            duration_ms=1,
            mis_tool_call_id=None,
            created_at=CREATED_AT,
        ).model_dump(mode="json")
        payload = [call, dict(call)]
    else:
        payload.append(dict(payload[0]))
    changed_bytes = manifest.canonical_json_bytes(payload)
    artifact_path.write_bytes(changed_bytes)

    def update_covered_hash(payload: dict[str, object]) -> None:
        artifacts = payload["artifacts"]
        assert isinstance(artifacts, dict)
        artifacts[artifact_name] = manifest.sha256_bytes(changed_bytes)

    _rewrite_manifest(manifest, written.manifest_path, update_covered_hash)

    report = bundle.verify_campaign(root, "occampaign_candidate")

    assert report.ok is False
    assert "duplicate_evidence_id" in _issue_codes(report)


def test_verification_rejects_rehashed_dangling_tool_call_turn(
    tmp_path: Path,
) -> None:
    bundle, manifest, root, written = _write_bundle(tmp_path)
    models = importlib.import_module("open_cekura.domain.models")
    dangling_call = models.ObservedToolCall(
        schema_version=1,
        id="octool_dangling",
        run_id="ocrun_basic",
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
    changed_bytes = manifest.canonical_json_bytes(
        [dangling_call.model_dump(mode="json")]
    )
    tool_calls_path = written.path / "tool_calls.json"
    tool_calls_path.write_bytes(changed_bytes)

    def update_covered_hash(payload: dict[str, object]) -> None:
        artifacts = payload["artifacts"]
        assert isinstance(artifacts, dict)
        artifacts["tool_calls.json"] = manifest.sha256_bytes(changed_bytes)

    _rewrite_manifest(manifest, written.manifest_path, update_covered_hash)

    report = bundle.verify_campaign(root, "occampaign_candidate")

    assert report.ok is False
    assert "dangling_turn_id" in _issue_codes(report)


def _rewrite_manifest(manifest_module, manifest_path: Path, transform) -> None:
    payload = json.loads(manifest_path.read_bytes())
    transform(payload)
    manifest_path.write_bytes(manifest_module.canonical_json_bytes(payload))


def test_run_bundle_verifies_offline_with_exact_ids_and_evaluator_versions(
    tmp_path: Path,
) -> None:
    bundle, _, root, written = _write_bundle(tmp_path)

    report = bundle.verify_campaign(root, "occampaign_candidate")

    assert report.ok is True
    assert report.campaign_id == "occampaign_candidate"
    assert report.issues == ()
    assert len(report.runs) == 1
    assert report.runs[0].ok is True
    assert report.runs[0].run_id == "ocrun_basic"
    assert written.manifest.evaluator_versions == [
        "llm_judge.openai.v1",
        "task_success.v1",
    ]


@pytest.mark.parametrize("mutation", ["missing", "tampered"])
def test_verification_rejects_missing_or_changed_covered_artifact(
    tmp_path: Path,
    mutation: str,
) -> None:
    bundle, _, root, written = _write_bundle(tmp_path)
    transcript_path = written.path / "transcript.json"
    if mutation == "missing":
        transcript_path.unlink()
        expected_code = "missing_artifact"
    else:
        transcript_path.write_bytes(transcript_path.read_bytes() + b" ")
        expected_code = "hash_mismatch"

    report = bundle.verify_campaign(root, "occampaign_candidate")

    assert report.ok is False
    assert expected_code in _issue_codes(report)
    issue = next(issue for issue in report.issues if issue.code == expected_code)
    assert issue.path == "ocrun_basic/transcript.json"
    if mutation == "tampered":
        assert issue.expected_sha256 == written.manifest.artifacts["transcript.json"]
        assert (
            issue.actual_sha256
            == hashlib.sha256(transcript_path.read_bytes()).hexdigest()
        )


def test_verification_rejects_contract_path_that_is_not_a_regular_file(
    tmp_path: Path,
) -> None:
    bundle, _, root, written = _write_bundle(tmp_path)
    transcript_path = written.path / "transcript.json"
    transcript_path.unlink()
    transcript_path.mkdir()

    report = bundle.verify_campaign(root, "occampaign_candidate")

    assert report.ok is False
    assert "artifact_not_file" in _issue_codes(report)
    issue = next(issue for issue in report.issues if issue.code == "artifact_not_file")
    assert issue.path == "ocrun_basic/transcript.json"


def test_benign_extra_is_reported_and_only_fails_strict_verification(
    tmp_path: Path,
) -> None:
    bundle, _, root, written = _write_bundle(tmp_path)
    (written.path / "operator-notes.txt").write_text("not evidence", encoding="utf-8")

    normal = bundle.verify_campaign(root, "occampaign_candidate")
    strict = bundle.verify_campaign(root, "occampaign_candidate", strict=True)

    assert normal.ok is True
    assert "unexpected_file" in _issue_codes(normal)
    normal_issue = next(
        issue for issue in normal.issues if issue.code == "unexpected_file"
    )
    assert normal_issue.severity == "warning"
    assert strict.ok is False
    strict_issue = next(
        issue for issue in strict.issues if issue.code == "unexpected_file"
    )
    assert strict_issue.severity == "error"


def test_unexpected_filename_is_not_echoed_in_verification_report(
    tmp_path: Path,
) -> None:
    bundle, _, root, written = _write_bundle(tmp_path)
    secret_filename = "API_KEY=secret-that-must-not-leak.txt"
    (written.path / secret_filename).write_text("notes", encoding="utf-8")

    report = bundle.verify_campaign(root, "occampaign_candidate")

    assert "unexpected_file" in _issue_codes(report)
    assert secret_filename not in repr(report)


def test_reserved_contract_file_in_campaign_directory_always_fails(
    tmp_path: Path,
) -> None:
    bundle, _, root, written = _write_bundle(tmp_path)
    (written.path.parent / "scenario.yaml").write_bytes(SCENARIO_YAML)

    report = bundle.verify_campaign(root, "occampaign_candidate")

    assert report.ok is False
    assert "reserved_contract_file" in _issue_codes(report)


@pytest.mark.parametrize(
    ("scope", "filename"),
    [
        ("run", "campaign_summary.json"),
        ("campaign", "Campaign_Summary.json"),
    ],
)
def test_campaign_contract_filenames_are_reserved_case_insensitively(
    tmp_path: Path,
    scope: str,
    filename: str,
) -> None:
    bundle, _, root, written = _write_bundle(
        tmp_path,
        include_campaign=scope == "run",
    )
    directory = written.path if scope == "run" else written.path.parent
    (directory / filename).write_text("{}", encoding="utf-8")

    report = bundle.verify_campaign(root, "occampaign_candidate")

    assert report.ok is False
    assert "reserved_contract_file" in _issue_codes(report)


def test_benign_extra_campaign_directory_only_fails_strict_verification(
    tmp_path: Path,
) -> None:
    bundle, _, root, written = _write_bundle(tmp_path)
    (written.path.parent / "operator-notes").mkdir()

    normal = bundle.verify_campaign(root, "occampaign_candidate")
    strict = bundle.verify_campaign(root, "occampaign_candidate", strict=True)

    assert normal.ok is True
    assert "unexpected_file" in _issue_codes(normal)
    assert strict.ok is False


def test_verification_rejects_manifest_artifact_path_traversal(tmp_path: Path) -> None:
    bundle, manifest, root, written = _write_bundle(tmp_path)

    def add_traversal(payload: dict[str, object]) -> None:
        artifacts = payload["artifacts"]
        assert isinstance(artifacts, dict)
        artifacts["../timing.json"] = artifacts.pop("timing.json")

    _rewrite_manifest(manifest, written.manifest_path, add_traversal)

    report = bundle.verify_campaign(root, "occampaign_candidate")

    assert report.ok is False
    assert "invalid_artifact_path" in _issue_codes(report)
    assert not (root / "occampaign_candidate" / "timing.json").exists()


@pytest.mark.parametrize(
    ("manifest_bytes", "expected_code"),
    [
        (b"{not-json", "malformed_manifest"),
        (b'{"schema_version":2}', "unsupported_manifest_schema"),
    ],
)
def test_verification_rejects_malformed_or_unsupported_manifest(
    tmp_path: Path,
    manifest_bytes: bytes,
    expected_code: str,
) -> None:
    bundle, _, root, written = _write_bundle(tmp_path)
    written.manifest_path.write_bytes(manifest_bytes)

    report = bundle.verify_campaign(root, "occampaign_candidate")

    assert report.ok is False
    assert expected_code in _issue_codes(report)


def test_verification_reports_non_finite_manifest_json_instead_of_raising(
    tmp_path: Path,
) -> None:
    bundle, _, root, written = _write_bundle(tmp_path)
    written.manifest_path.write_bytes(b'{"schema_version":1,"value":NaN}')

    report = bundle.verify_campaign(root, "occampaign_candidate")

    assert report.ok is False
    assert "malformed_manifest" in _issue_codes(report)


def test_verification_rejects_rehashed_evaluator_version_mismatch(
    tmp_path: Path,
) -> None:
    bundle, manifest, root, written = _write_bundle(tmp_path)
    evaluations_path = written.path / "evaluations.json"
    evaluations = json.loads(evaluations_path.read_bytes())
    evaluations[0]["evaluator_id"] = "different_evaluator.v1"
    changed_bytes = manifest.canonical_json_bytes(evaluations)
    evaluations_path.write_bytes(changed_bytes)

    def update_covered_hash(payload: dict[str, object]) -> None:
        artifacts = payload["artifacts"]
        assert isinstance(artifacts, dict)
        artifacts["evaluations.json"] = manifest.sha256_bytes(changed_bytes)

    _rewrite_manifest(manifest, written.manifest_path, update_covered_hash)

    report = bundle.verify_campaign(root, "occampaign_candidate")

    assert report.ok is False
    assert "evaluator_versions_mismatch" in _issue_codes(report)


def test_verification_rejects_rehashed_invalid_effective_agent_config(
    tmp_path: Path,
) -> None:
    bundle, manifest, root, written = _write_bundle(tmp_path)
    agent_path = written.path / "agent_version.json"
    envelope = json.loads(agent_path.read_bytes())
    envelope["config"]["temperature"] = 0.9
    changed_bytes = manifest.canonical_json_bytes(envelope)
    changed_hash = manifest.sha256_bytes(changed_bytes)
    agent_path.write_bytes(changed_bytes)

    def update_covered_hash(payload: dict[str, object]) -> None:
        payload["agent_config_sha256"] = changed_hash
        artifacts = payload["artifacts"]
        assert isinstance(artifacts, dict)
        artifacts["agent_version.json"] = changed_hash

    _rewrite_manifest(manifest, written.manifest_path, update_covered_hash)

    report = bundle.verify_campaign(root, "occampaign_candidate")

    assert report.ok is False
    assert "agent_config_digest_mismatch" in _issue_codes(report)


def test_verification_rejects_rehashed_final_state_mismatch(tmp_path: Path) -> None:
    bundle, manifest, root, written = _write_bundle(tmp_path)

    def change_final_state(payload: dict[str, object]) -> None:
        payload["final_state"] = "fail"

    _rewrite_manifest(manifest, written.manifest_path, change_final_state)

    report = bundle.verify_campaign(root, "occampaign_candidate")

    assert report.ok is False
    assert "final_state_mismatch" in _issue_codes(report)


def test_verification_rejects_sensitive_rehashed_evidence_without_leaking_it(
    tmp_path: Path,
) -> None:
    bundle, manifest, root, written = _write_bundle(tmp_path)
    evaluations_path = written.path / "evaluations.json"
    evaluations = json.loads(evaluations_path.read_bytes())
    secret = "Bearer secret-that-must-not-leak"
    evaluations[0]["metadata"]["Authorization"] = secret
    changed_bytes = manifest.canonical_json_bytes(evaluations)
    evaluations_path.write_bytes(changed_bytes)

    def update_covered_hash(payload: dict[str, object]) -> None:
        artifacts = payload["artifacts"]
        assert isinstance(artifacts, dict)
        artifacts["evaluations.json"] = manifest.sha256_bytes(changed_bytes)

    _rewrite_manifest(manifest, written.manifest_path, update_covered_hash)

    report = bundle.verify_campaign(root, "occampaign_candidate")

    assert report.ok is False
    assert "sensitive_evidence" in _issue_codes(report)
    assert secret not in repr(report)


def test_verification_rejects_rehashed_invalid_reference_without_leaking_it(
    tmp_path: Path,
) -> None:
    bundle, manifest, root, written = _write_bundle(tmp_path)
    evaluations_path = written.path / "evaluations.json"
    evaluations = json.loads(evaluations_path.read_bytes())
    invalid_reference = r"artifact:C:\Users\alice\.env?API_KEY=sekret"
    evaluations[0]["evidence_refs"] = [invalid_reference]
    changed_bytes = manifest.canonical_json_bytes(evaluations)
    evaluations_path.write_bytes(changed_bytes)

    def update_covered_hash(payload: dict[str, object]) -> None:
        artifacts = payload["artifacts"]
        assert isinstance(artifacts, dict)
        artifacts["evaluations.json"] = manifest.sha256_bytes(changed_bytes)

    _rewrite_manifest(manifest, written.manifest_path, update_covered_hash)

    report = bundle.verify_campaign(root, "occampaign_candidate")

    assert report.ok is False
    assert "invalid_evidence_ref" in _issue_codes(report)
    assert invalid_reference not in repr(report)


def test_verification_rejects_symlinked_artifact(tmp_path: Path) -> None:
    bundle, _, root, written = _write_bundle(tmp_path)
    transcript_path = written.path / "transcript.json"
    outside = tmp_path / "outside transcript.json"
    outside.write_bytes(transcript_path.read_bytes())
    transcript_path.unlink()
    try:
        transcript_path.symlink_to(outside)
    except OSError as error:
        pytest.skip(f"file links are unavailable in this environment: {error}")

    report = bundle.verify_campaign(root, "occampaign_candidate")

    assert report.ok is False
    assert "symlink_not_allowed" in _issue_codes(report)
