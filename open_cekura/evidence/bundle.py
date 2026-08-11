"""Windows-safe atomic construction of OpenCekura run evidence bundles."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Mapping

import yaml
from pydantic import JsonValue, ValidationError

from open_cekura.domain.enums import RunFinalState
from open_cekura.domain.models import (
    AgentVersion,
    ConversationTurn,
    EvaluationResult,
    EvidenceEnvironment,
    EvidenceManifest,
    ObservedToolCall,
)
from open_cekura.scenarios.schema import ScenarioDefinition

from .manifest import (
    CAMPAIGN_ARTIFACT_FILENAMES,
    CAMPAIGN_SUMMARY_FILENAME,
    EvidenceError,
    EvidenceInputError,
    EvidencePathError,
    EvidenceWriteError,
    MANIFEST_FILENAME,
    RUN_ARTIFACT_FILENAMES,
    RunVerification,
    VerificationIssue,
    VerificationReport,
    absolute_safe_root,
    canonical_json_bytes,
    contains_sensitive_fields,
    deterministic_final_state,
    evidence_ids_are_unique,
    evidence_references_resolve,
    is_symlink_or_reparse,
    sha256_bytes,
    tool_call_turns_resolve,
    validate_path_component,
    verify_campaign,
    verify_run_bundles,
)


@dataclass(frozen=True, slots=True)
class RunBundleInputs:
    manifest_id: str
    campaign_id: str
    run_id: str
    mis_artifact_id: str | None
    mis_plan_evidence_manifest_id: str | None
    git_commit_sha: str
    environment: EvidenceEnvironment
    scenario_yaml: bytes
    agent_version: AgentVersion
    agent_config: Mapping[str, JsonValue]
    transcript: tuple[ConversationTurn, ...]
    tool_calls: tuple[ObservedToolCall, ...]
    initial_state: Mapping[str, JsonValue]
    observed_final_state: Mapping[str, JsonValue]
    timing: Mapping[str, JsonValue]
    evaluations: tuple[EvaluationResult, ...]
    started_at: datetime
    finished_at: datetime
    final_state: RunFinalState
    created_at: datetime


@dataclass(frozen=True, slots=True)
class RunBundle:
    path: Path
    manifest_path: Path
    manifest: EvidenceManifest


@dataclass(frozen=True, slots=True)
class CampaignBundleInputs:
    campaign_id: str
    campaign_summary: Mapping[str, JsonValue]
    baseline_candidate_diff: Mapping[str, JsonValue] | None
    release_gate: Mapping[str, JsonValue]
    regression_cases: tuple[JsonValue, ...]


@dataclass(frozen=True, slots=True)
class CampaignBundle:
    path: Path
    artifact_paths: Mapping[str, Path]
    artifact_sha256: Mapping[str, str]


def write_run_bundle(root: str | Path, inputs: RunBundleInputs) -> RunBundle:
    """Create or replace one complete run bundle beneath its artifact root."""

    if not isinstance(inputs, RunBundleInputs):
        raise EvidenceInputError("inputs must be RunBundleInputs")
    validate_path_component(inputs.campaign_id, label="campaign_id")
    validate_path_component(inputs.run_id, label="run_id")
    artifact_bytes, evidence_manifest = _prepare_artifacts(inputs)
    run_path = _prepare_run_path(root, inputs.campaign_id, inputs.run_id)
    artifact_root = absolute_safe_root(root)

    for artifact_name in RUN_ARTIFACT_FILENAMES:
        _atomic_write_bytes(
            run_path / artifact_name,
            artifact_bytes[artifact_name],
            artifact_root=artifact_root,
        )
    manifest_path = run_path / MANIFEST_FILENAME
    _atomic_write_bytes(
        manifest_path,
        evidence_manifest.canonical_json_bytes(),
        artifact_root=artifact_root,
    )
    return RunBundle(
        path=run_path,
        manifest_path=manifest_path,
        manifest=evidence_manifest,
    )


def write_campaign_bundle(
    root: str | Path,
    inputs: CampaignBundleInputs,
) -> CampaignBundle:
    """Create or replace the four campaign evidence files, summary last."""

    if not isinstance(inputs, CampaignBundleInputs):
        raise EvidenceInputError("inputs must be CampaignBundleInputs")
    validate_path_component(inputs.campaign_id, label="campaign_id")
    artifact_bytes, artifact_hashes = _prepare_campaign_artifacts(inputs)
    campaign_path = _prepare_campaign_path(root, inputs.campaign_id)
    artifact_root = absolute_safe_root(root)

    for artifact_name in CAMPAIGN_ARTIFACT_FILENAMES:
        _atomic_write_bytes(
            campaign_path / artifact_name,
            artifact_bytes[artifact_name],
            artifact_root=artifact_root,
        )
    _atomic_write_bytes(
        campaign_path / CAMPAIGN_SUMMARY_FILENAME,
        artifact_bytes[CAMPAIGN_SUMMARY_FILENAME],
        artifact_root=artifact_root,
    )
    artifact_paths = {
        name: campaign_path / name
        for name in (CAMPAIGN_SUMMARY_FILENAME, *CAMPAIGN_ARTIFACT_FILENAMES)
    }
    return CampaignBundle(
        path=campaign_path,
        artifact_paths=artifact_paths,
        artifact_sha256=artifact_hashes,
    )


def _prepare_campaign_artifacts(
    inputs: CampaignBundleInputs,
) -> tuple[dict[str, bytes], dict[str, str]]:
    if not isinstance(inputs.campaign_summary, Mapping):
        raise EvidenceInputError("campaign_summary must be a JSON object")
    if inputs.baseline_candidate_diff is not None and not isinstance(
        inputs.baseline_candidate_diff,
        Mapping,
    ):
        raise EvidenceInputError(
            "baseline_candidate_diff must be a JSON object or null"
        )
    if not isinstance(inputs.release_gate, Mapping):
        raise EvidenceInputError("release_gate must be a JSON object")
    if not isinstance(inputs.regression_cases, tuple):
        raise EvidenceInputError("regression_cases must be a tuple of JSON values")

    summary_payload = dict(inputs.campaign_summary)
    diff_payload = (
        dict(inputs.baseline_candidate_diff)
        if inputs.baseline_candidate_diff is not None
        else {
            "schema_version": 1,
            "campaign_id": inputs.campaign_id,
            "baseline": None,
            "comparison": "no_comparison",
        }
    )
    release_gate_payload = dict(inputs.release_gate)
    regression_payload = list(inputs.regression_cases)
    if any(
        contains_sensitive_fields(payload)
        for payload in (
            summary_payload,
            diff_payload,
            release_gate_payload,
            regression_payload,
        )
    ):
        raise EvidenceInputError(
            "campaign evidence contains a prohibited sensitive field"
        )

    try:
        artifact_bytes = {
            "baseline_candidate_diff.json": canonical_json_bytes(diff_payload),
            "release_gate.json": canonical_json_bytes(release_gate_payload),
            "regression_cases.json": canonical_json_bytes(regression_payload),
        }
    except (TypeError, ValueError, UnicodeError, RecursionError) as error:
        raise EvidenceInputError(
            "campaign evidence must be finite JSON data"
        ) from error
    covered_hashes = {
        name: sha256_bytes(artifact_bytes[name]) for name in CAMPAIGN_ARTIFACT_FILENAMES
    }
    try:
        artifact_bytes[CAMPAIGN_SUMMARY_FILENAME] = canonical_json_bytes(
            {
                "schema_version": 1,
                "campaign_id": inputs.campaign_id,
                "summary": summary_payload,
                "artifacts": covered_hashes,
            }
        )
    except (TypeError, ValueError, UnicodeError, RecursionError) as error:
        raise EvidenceInputError("campaign summary must be finite JSON data") from error
    artifact_hashes = {
        name: sha256_bytes(content) for name, content in artifact_bytes.items()
    }
    return artifact_bytes, artifact_hashes


def _prepare_artifacts(
    inputs: RunBundleInputs,
) -> tuple[dict[str, bytes], EvidenceManifest]:
    if not isinstance(inputs.scenario_yaml, bytes) or not inputs.scenario_yaml:
        raise EvidenceInputError("scenario_yaml must contain exact non-empty bytes")
    try:
        scenario_payload = yaml.safe_load(inputs.scenario_yaml.decode("utf-8"))
        scenario = ScenarioDefinition.model_validate(scenario_payload)
    except (UnicodeError, yaml.YAMLError, ValidationError, RecursionError) as error:
        raise EvidenceInputError("scenario_yaml must satisfy Scenario v1") from error
    if not isinstance(inputs.environment, EvidenceEnvironment):
        raise EvidenceInputError("environment must be EvidenceEnvironment")
    if not isinstance(inputs.agent_version, AgentVersion):
        raise EvidenceInputError("agent_version must be AgentVersion")
    if not isinstance(inputs.agent_config, Mapping):
        raise EvidenceInputError("agent_config must be a JSON object")
    if not isinstance(inputs.timing, Mapping):
        raise EvidenceInputError("timing must be a JSON object")
    if not isinstance(inputs.initial_state, Mapping):
        raise EvidenceInputError("initial_state must be a JSON object")
    if not isinstance(inputs.observed_final_state, Mapping):
        raise EvidenceInputError("observed_final_state must be a JSON object")
    if not all(isinstance(turn, ConversationTurn) for turn in inputs.transcript):
        raise EvidenceInputError("transcript must contain ConversationTurn values")
    if not all(isinstance(call, ObservedToolCall) for call in inputs.tool_calls):
        raise EvidenceInputError("tool_calls must contain ObservedToolCall values")
    if not inputs.evaluations or not all(
        isinstance(evaluation, EvaluationResult) for evaluation in inputs.evaluations
    ):
        raise EvidenceInputError("evaluations must contain typed results")
    if any(turn.run_id != inputs.run_id for turn in inputs.transcript):
        raise EvidenceInputError("all transcript turns must reference run_id")
    if any(call.run_id != inputs.run_id for call in inputs.tool_calls):
        raise EvidenceInputError("all tool calls must reference run_id")
    if any(evaluation.run_id != inputs.run_id for evaluation in inputs.evaluations):
        raise EvidenceInputError("all evaluations must reference run_id")
    if not evidence_ids_are_unique(
        inputs.transcript,
        inputs.tool_calls,
        inputs.evaluations,
    ):
        raise EvidenceInputError("run evidence contains duplicate logical identifiers")
    if not tool_call_turns_resolve(inputs.transcript, inputs.tool_calls):
        raise EvidenceInputError(
            "tool call evidence references a missing transcript turn"
        )

    config_payload = dict(inputs.agent_config)
    transcript_payload = [turn.model_dump(mode="json") for turn in inputs.transcript]
    tool_call_payload = [call.model_dump(mode="json") for call in inputs.tool_calls]
    timing_payload = dict(inputs.timing)
    if {"initial_state", "observed_final_state"}.intersection(timing_payload):
        raise EvidenceInputError("timing contains a reserved evidence field")
    timing_payload["initial_state"] = dict(inputs.initial_state)
    timing_payload["observed_final_state"] = dict(inputs.observed_final_state)
    evaluation_payload = [
        evaluation.model_dump(mode="json") for evaluation in inputs.evaluations
    ]
    if any(
        contains_sensitive_fields(payload)
        for payload in (
            scenario_payload,
            config_payload,
            transcript_payload,
            tool_call_payload,
            timing_payload,
            evaluation_payload,
        )
    ):
        raise EvidenceInputError("run evidence contains a prohibited sensitive field")
    if deterministic_final_state(inputs.evaluations) is not inputs.final_state:
        raise EvidenceInputError(
            "final_state must match deterministic evaluation results"
        )
    mis_ids = {
        "artifact": (
            {inputs.mis_artifact_id} if inputs.mis_artifact_id is not None else set()
        ),
        "plan_evidence_manifest": (
            {inputs.mis_plan_evidence_manifest_id}
            if inputs.mis_plan_evidence_manifest_id is not None
            else set()
        ),
        "tool_call": {
            call.mis_tool_call_id
            for call in inputs.tool_calls
            if call.mis_tool_call_id is not None
        },
        "evaluation": {
            evaluation.mis_evaluation_id
            for evaluation in inputs.evaluations
            if evaluation.mis_evaluation_id is not None
        },
    }
    if not evidence_references_resolve(
        inputs.evaluations,
        scenario=scenario,
        turns=inputs.transcript,
        tool_calls=inputs.tool_calls,
        observed_final_state=dict(inputs.observed_final_state),
        mis_ids=mis_ids,
    ):
        raise EvidenceInputError(
            "run evidence contains a malformed or unresolved evidence reference"
        )

    try:
        config_bytes = canonical_json_bytes(config_payload)
    except (TypeError, ValueError, UnicodeError, RecursionError) as error:
        raise EvidenceInputError("agent_config must be finite JSON data") from error
    if sha256_bytes(config_bytes) != inputs.agent_version.config_sha256:
        raise EvidenceInputError(
            "effective agent config does not match AgentVersion.config_sha256"
        )

    if timing_payload.get("started_at") != _utc_json_time(
        inputs.started_at
    ) or timing_payload.get("finished_at") != _utc_json_time(inputs.finished_at):
        raise EvidenceInputError("timing timestamps must match manifest metadata")

    try:
        agent_envelope = canonical_json_bytes(
            {
                "schema_version": 1,
                "agent_version": inputs.agent_version.model_dump(mode="json"),
                "config": config_payload,
            }
        )
        transcript = canonical_json_bytes(transcript_payload)
        tool_calls = canonical_json_bytes(tool_call_payload)
        timing = canonical_json_bytes(timing_payload)
        evaluations = canonical_json_bytes(evaluation_payload)
    except (TypeError, ValueError, UnicodeError, RecursionError) as error:
        raise EvidenceInputError("run evidence must be finite JSON data") from error

    artifact_bytes = {
        "scenario.yaml": inputs.scenario_yaml,
        "agent_version.json": agent_envelope,
        "transcript.json": transcript,
        "tool_calls.json": tool_calls,
        "timing.json": timing,
        "evaluations.json": evaluations,
    }
    artifact_hashes = {
        name: sha256_bytes(artifact_bytes[name]) for name in RUN_ARTIFACT_FILENAMES
    }
    evaluator_versions = sorted(
        {evaluation.evaluator_id for evaluation in inputs.evaluations}
    )
    try:
        manifest = EvidenceManifest(
            schema_version=1,
            id=inputs.manifest_id,
            campaign_id=inputs.campaign_id,
            run_id=inputs.run_id,
            mis_artifact_id=inputs.mis_artifact_id,
            mis_plan_evidence_manifest_id=inputs.mis_plan_evidence_manifest_id,
            git_commit_sha=inputs.git_commit_sha,
            environment=inputs.environment,
            scenario_sha256=artifact_hashes["scenario.yaml"],
            agent_config_sha256=artifact_hashes["agent_version.json"],
            evaluator_versions=evaluator_versions,
            artifacts=artifact_hashes,
            started_at=inputs.started_at,
            finished_at=inputs.finished_at,
            final_state=inputs.final_state,
            created_at=inputs.created_at,
        )
    except ValidationError as error:
        raise EvidenceInputError(
            "manifest metadata violates the domain contract"
        ) from error
    return artifact_bytes, manifest


def _prepare_run_path(root: str | Path, campaign_id: str, run_id: str) -> Path:
    campaign_path = _prepare_campaign_path(root, campaign_id)
    root_path = absolute_safe_root(root)
    run_path = campaign_path / run_id
    if is_symlink_or_reparse(run_path):
        raise EvidencePathError("run directory is a symlink or reparse point")
    try:
        run_path.mkdir(exist_ok=True)
    except OSError as error:
        raise EvidencePathError("run directory cannot be created") from error
    if is_symlink_or_reparse(run_path) or not run_path.is_dir():
        raise EvidencePathError("run directory must be a real directory")
    resolved_run_path = run_path.resolve(strict=True)
    try:
        resolved_run_path.relative_to(root_path)
    except ValueError as error:
        raise EvidencePathError("run directory escapes the artifact root") from error
    return resolved_run_path


def _prepare_campaign_path(root: str | Path, campaign_id: str) -> Path:
    root_path = absolute_safe_root(root)
    try:
        root_path.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise EvidencePathError("artifact root cannot be created") from error
    if is_symlink_or_reparse(root_path) or not root_path.is_dir():
        raise EvidencePathError("artifact root must be a real directory")

    campaign_path = root_path / campaign_id
    if is_symlink_or_reparse(campaign_path):
        raise EvidencePathError("campaign directory is a symlink or reparse point")
    try:
        campaign_path.mkdir(exist_ok=True)
    except OSError as error:
        raise EvidencePathError("campaign directory cannot be created") from error
    if is_symlink_or_reparse(campaign_path) or not campaign_path.is_dir():
        raise EvidencePathError("campaign directory must be a real directory")
    resolved_campaign_path = campaign_path.resolve(strict=True)
    try:
        resolved_campaign_path.relative_to(root_path)
    except ValueError as error:
        raise EvidencePathError(
            "campaign directory escapes the artifact root"
        ) from error
    return resolved_campaign_path


def _atomic_write_bytes(
    destination: Path,
    content: bytes,
    *,
    artifact_root: Path,
) -> None:
    destination = _validated_atomic_destination(destination, artifact_root)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        destination = _validated_atomic_destination(destination, artifact_root)
        os.replace(temporary_path, destination)
        temporary_path = None
        _best_effort_directory_fsync(destination.parent)
    except EvidenceError:
        raise
    except OSError as error:
        raise EvidenceWriteError(
            f"failed to atomically write {destination.name}"
        ) from error
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass


def _validated_atomic_destination(destination: Path, artifact_root: Path) -> Path:
    root_path = absolute_safe_root(artifact_root)
    if is_symlink_or_reparse(root_path) or not root_path.is_dir():
        raise EvidencePathError("artifact root must remain a real directory")

    destination_path = Path(os.path.abspath(os.fspath(destination)))
    parent = destination_path.parent
    if is_symlink_or_reparse(parent) or not parent.is_dir():
        raise EvidencePathError(
            "artifact destination parent is a symlink, reparse point, or non-directory"
        )
    resolved_parent = absolute_safe_root(parent)
    try:
        resolved_parent.relative_to(root_path)
    except ValueError as error:
        raise EvidencePathError(
            "artifact destination escapes the configured root"
        ) from error
    resolved_destination = resolved_parent / destination_path.name
    if is_symlink_or_reparse(resolved_destination):
        raise EvidencePathError(
            f"destination {destination_path.name!r} is a symlink or reparse point"
        )
    return resolved_destination


def _best_effort_directory_fsync(directory: Path) -> None:
    descriptor: int | None = None
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(directory, flags)
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _utc_json_time(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


__all__ = [
    "EvidenceError",
    "EvidenceInputError",
    "EvidencePathError",
    "EvidenceWriteError",
    "CampaignBundle",
    "CampaignBundleInputs",
    "RunBundle",
    "RunBundleInputs",
    "RunVerification",
    "VerificationIssue",
    "VerificationReport",
    "verify_campaign",
    "verify_run_bundles",
    "write_campaign_bundle",
    "write_run_bundle",
]
