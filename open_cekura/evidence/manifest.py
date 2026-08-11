"""Canonical evidence hashing and offline run-bundle verification."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Literal, Mapping, Sequence

import yaml
from pydantic import BaseModel, TypeAdapter, ValidationError

from open_cekura.domain.enums import EvaluationStatus, RunFinalState
from open_cekura.domain.models import (
    AgentVersion,
    ConversationTurn,
    EvaluationResult,
    EvidenceManifest,
    ObservedToolCall,
)
from open_cekura.scenarios.schema import ScenarioDefinition


MANIFEST_FILENAME = "evidence_manifest.json"
RUN_ARTIFACT_FILENAMES = (
    "scenario.yaml",
    "agent_version.json",
    "transcript.json",
    "tool_calls.json",
    "timing.json",
    "evaluations.json",
)
RUN_FILENAMES = frozenset((*RUN_ARTIFACT_FILENAMES, MANIFEST_FILENAME))
CAMPAIGN_ARTIFACT_FILENAMES = (
    "baseline_candidate_diff.json",
    "release_gate.json",
    "regression_cases.json",
)
CAMPAIGN_SUMMARY_FILENAME = "campaign_summary.json"
CAMPAIGN_FILENAMES = frozenset(
    (CAMPAIGN_SUMMARY_FILENAME, *CAMPAIGN_ARTIFACT_FILENAMES)
)
SUPPORTED_MANIFEST_SCHEMA_VERSION = 1

_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")
_WINDOWS_RESERVED_NAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{number}" for number in range(1, 10)),
        *(f"LPT{number}" for number in range(1, 10)),
    }
)
_SENSITIVE_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "auth_token",
        "authorization",
        "authorization_header",
        "bearer_token",
        "client_secret",
        "cookie",
        "hidden_prompt",
        "password",
        "private_key",
        "raw_prompt",
        "raw_response",
        "refresh_token",
        "secret",
        "session",
        "session_id",
        "session_token",
        "set_cookie",
        "token",
    }
)
_SENSITIVE_SUFFIXES = (
    "_access_token",
    "_api_key",
    "_auth_token",
    "_authorization",
    "_client_secret",
    "_password",
    "_private_key",
    "_refresh_token",
    "_secret",
    "_session",
    "_session_id",
    "_session_token",
    "_token",
)


class EvidenceError(ValueError):
    """Base exception for invalid or unsafe filesystem evidence operations."""


class EvidencePathError(EvidenceError):
    """A configured evidence path is unsafe or leaves its declared root."""


class EvidenceInputError(EvidenceError):
    """Run evidence cannot satisfy the frozen bundle contract."""


class EvidenceWriteError(EvidenceError):
    """An artifact could not be committed atomically."""


@dataclass(frozen=True, slots=True)
class VerificationIssue:
    code: str
    severity: Literal["error", "warning"]
    path: str
    message: str
    expected_sha256: str | None = None
    actual_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class RunVerification:
    campaign_id: str
    run_id: str
    manifest_path: Path
    manifest: EvidenceManifest | None
    issues: tuple[VerificationIssue, ...]

    @property
    def ok(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)


@dataclass(frozen=True, slots=True)
class VerificationReport:
    root: Path
    campaign_id: str
    runs: tuple[RunVerification, ...]
    issues: tuple[VerificationIssue, ...]

    @property
    def ok(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)


def canonical_json_bytes(value: object) -> bytes:
    """Return canonical UTF-8 JSON bytes with no platform newline dependence."""

    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    """Return a lowercase SHA-256 digest for exact stored bytes."""

    if not isinstance(value, bytes):
        raise TypeError("sha256_bytes requires bytes")
    return hashlib.sha256(value).hexdigest()


def contains_sensitive_fields(value: object) -> bool:
    """Detect credential-bearing field names without inspecting or echoing values."""

    pending = [value]
    visited_containers: set[int] = set()
    while pending:
        current = pending.pop()
        if isinstance(current, BaseModel):
            current = current.model_dump(mode="json")
        if isinstance(current, dict):
            if id(current) in visited_containers:
                continue
            visited_containers.add(id(current))
            for key, child in current.items():
                snake_key = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", str(key))
                snake_key = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", snake_key)
                normalized = re.sub(r"[^a-z0-9]+", "_", snake_key.casefold()).strip("_")
                if normalized in _SENSITIVE_KEYS or normalized.endswith(
                    _SENSITIVE_SUFFIXES
                ):
                    return True
                pending.append(child)
        elif isinstance(current, (list, tuple)):
            if id(current) in visited_containers:
                continue
            visited_containers.add(id(current))
            pending.extend(current)
    return False


def deterministic_final_state(
    evaluations: list[EvaluationResult] | tuple[EvaluationResult, ...],
) -> RunFinalState:
    """Derive run outcome from deterministic evaluators, excluding optional judges."""

    deterministic = [
        evaluation
        for evaluation in evaluations
        if not _is_optional_judge(evaluation.evaluator_id)
    ]
    if not deterministic or any(
        evaluation.status in {EvaluationStatus.ERROR, EvaluationStatus.SKIPPED}
        for evaluation in deterministic
    ):
        return RunFinalState.ERROR
    if any(evaluation.status is EvaluationStatus.FAIL for evaluation in deterministic):
        return RunFinalState.FAIL
    return RunFinalState.PASS


def evidence_references_resolve(
    evaluations: Sequence[EvaluationResult],
    *,
    scenario: ScenarioDefinition,
    turns: Sequence[ConversationTurn],
    tool_calls: Sequence[ObservedToolCall],
    observed_final_state: Mapping[str, object],
    mis_ids: Mapping[str, set[str] | frozenset[str]],
) -> bool:
    """Return whether every evidence reference is typed and resolves in this run."""

    turn_ids = {turn.id for turn in turns}
    tool_call_ids = {call.id for call in tool_calls}
    evaluation_ids = {evaluation.id for evaluation in evaluations}
    expected_final_state = scenario.expectations.final_state
    expectations = scenario.expectations.model_dump(mode="json")

    for evaluation in evaluations:
        for reference in evaluation.evidence_refs:
            if not isinstance(reference, str) or ":" not in reference:
                return False
            prefix, target = reference.split(":", 1)
            if not target:
                return False
            if prefix == "artifact":
                if target not in RUN_ARTIFACT_FILENAMES:
                    return False
            elif prefix == "turn":
                if target not in turn_ids:
                    return False
            elif prefix == "tool_call":
                if target not in tool_call_ids:
                    return False
            elif prefix == "evaluation":
                if target not in evaluation_ids:
                    return False
            elif prefix == "expectation":
                if not _expectation_path_resolves(expectations, target):
                    return False
            elif prefix == "final_state":
                if not (
                    _json_pointer_resolves(observed_final_state, target)
                    or _json_pointer_resolves(expected_final_state, target)
                ):
                    return False
            elif prefix == "mis":
                if not _mis_reference_resolves(target, mis_ids):
                    return False
            else:
                return False
    return True


def _expectation_path_resolves(expectations: object, path: str) -> bool:
    if not path or "/" in path or "\\" in path:
        return False
    current = expectations
    for segment in path.split("."):
        match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)(.*)", segment)
        if match is None or not isinstance(current, dict):
            return False
        key, indexes = match.groups()
        if key not in current:
            return False
        current = current[key]
        while indexes:
            index_match = re.match(r"\[(0|[1-9][0-9]*)\]", indexes)
            if index_match is None or not isinstance(current, list):
                return False
            index = int(index_match.group(1))
            if index >= len(current):
                return False
            current = current[index]
            indexes = indexes[index_match.end() :]
    return True


def _json_pointer_resolves(document: object, pointer: str) -> bool:
    if not pointer.startswith("/"):
        return False
    current = document
    for encoded_token in pointer[1:].split("/"):
        if re.search(r"~(?![01])", encoded_token):
            return False
        token = encoded_token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            if token not in current:
                return False
            current = current[token]
        elif isinstance(current, list):
            if not re.fullmatch(r"0|[1-9][0-9]*", token):
                return False
            index = int(token)
            if index >= len(current):
                return False
            current = current[index]
        else:
            return False
    return True


def _mis_reference_resolves(
    target: str,
    known_ids: Mapping[str, set[str] | frozenset[str]],
) -> bool:
    if ":" not in target:
        return False
    object_type, identifier = target.split(":", 1)
    return bool(
        re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,63}", object_type)
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}", identifier)
        and object_type in known_ids
        and identifier in known_ids[object_type]
    )


def evidence_ids_are_unique(
    turns: Sequence[ConversationTurn],
    tool_calls: Sequence[ObservedToolCall],
    evaluations: Sequence[EvaluationResult],
) -> bool:
    """Reject ambiguous logical IDs, turn positions, and external child mappings."""

    collections: tuple[Sequence[object], ...] = (turns, tool_calls, evaluations)
    if any(
        len({getattr(item, "id") for item in collection}) != len(collection)
        for collection in collections
    ):
        return False
    if len({turn.turn_index for turn in turns}) != len(turns):
        return False
    for collection, field_name in (
        (tool_calls, "mis_tool_call_id"),
        (evaluations, "mis_evaluation_id"),
    ):
        identifiers = [
            getattr(item, field_name)
            for item in collection
            if getattr(item, field_name) is not None
        ]
        if len(set(identifiers)) != len(identifiers):
            return False
    return True


def tool_call_turns_resolve(
    turns: Sequence[ConversationTurn],
    tool_calls: Sequence[ObservedToolCall],
) -> bool:
    turn_ids = {turn.id for turn in turns}
    return all(call.turn_id in turn_ids for call in tool_calls)


def validate_path_component(value: str, *, label: str) -> str:
    """Validate one portable Windows-safe campaign or run path component."""

    if not isinstance(value, str) or not value:
        raise EvidencePathError(f"{label} must be a non-empty string")
    windows_path = PureWindowsPath(value)
    reserved_base = value.rstrip(" .").split(".", 1)[0].upper()
    if (
        value in {".", ".."}
        or not _SAFE_COMPONENT.fullmatch(value)
        or windows_path.drive
        or windows_path.root
        or len(windows_path.parts) != 1
        or value.endswith((" ", "."))
        or reserved_base in _WINDOWS_RESERVED_NAMES
    ):
        raise EvidencePathError(f"{label} is not a safe single path component")
    return value


def is_symlink_or_reparse(path: Path) -> bool:
    """Return whether an existing path is a link or Windows reparse point."""

    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    except OSError as error:
        raise EvidencePathError(
            f"cannot inspect evidence path {path.name!r}"
        ) from error
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    file_attributes = getattr(metadata, "st_file_attributes", 0)
    return stat.S_ISLNK(metadata.st_mode) or bool(file_attributes & reparse_flag)


def absolute_safe_root(root: str | Path) -> Path:
    """Normalize a configured root without accepting linked existing ancestry."""

    raw_root = Path(root)
    absolute_root = Path(os.path.abspath(os.fspath(raw_root)))
    _reject_linked_existing_ancestry(absolute_root)
    return absolute_root.resolve(strict=False)


def _reject_linked_existing_ancestry(path: Path) -> None:
    chain = [path, *path.parents]
    for component in reversed(chain):
        if is_symlink_or_reparse(component):
            raise EvidencePathError(
                f"evidence path ancestry contains a symlink or reparse point: {component.name!r}"
            )


def verify_run_bundles(
    root: str | Path,
    campaign_id: str,
    *,
    strict: bool = False,
) -> VerificationReport:
    """Verify only run bundles, before campaign gate evidence is available."""

    validate_path_component(campaign_id, label="campaign_id")
    root_path = absolute_safe_root(root)
    campaign_path = root_path / campaign_id
    campaign_issues: list[VerificationIssue] = []

    if not campaign_path.exists():
        campaign_issues.append(
            _issue(
                "campaign_missing",
                "error",
                campaign_id,
                "campaign evidence directory is missing",
            )
        )
        return VerificationReport(
            root=root_path,
            campaign_id=campaign_id,
            runs=(),
            issues=tuple(campaign_issues),
        )
    if is_symlink_or_reparse(campaign_path):
        campaign_issues.append(
            _issue(
                "symlink_not_allowed",
                "error",
                campaign_id,
                "campaign evidence directory is a symlink or reparse point",
            )
        )
        return VerificationReport(
            root=root_path,
            campaign_id=campaign_id,
            runs=(),
            issues=tuple(campaign_issues),
        )
    if not campaign_path.is_dir():
        campaign_issues.append(
            _issue(
                "campaign_not_directory",
                "error",
                campaign_id,
                "campaign evidence path is not a directory",
            )
        )
        return VerificationReport(
            root=root_path,
            campaign_id=campaign_id,
            runs=(),
            issues=tuple(campaign_issues),
        )

    run_paths: list[Path] = []
    try:
        campaign_entries = sorted(
            campaign_path.iterdir(),
            key=lambda entry: (entry.name.casefold(), entry.name),
        )
    except OSError:
        campaign_issues.append(
            _issue(
                "campaign_unreadable",
                "error",
                campaign_id,
                "campaign evidence directory cannot be read",
            )
        )
        campaign_entries = []

    reserved_run_names = {name.casefold() for name in RUN_FILENAMES}
    reserved_campaign_names = {name.casefold() for name in CAMPAIGN_FILENAMES}
    for entry in campaign_entries:
        relative = entry.name
        if is_symlink_or_reparse(entry):
            campaign_issues.append(
                _issue(
                    "symlink_not_allowed",
                    "error",
                    "<untrusted-entry>",
                    "campaign entry is a symlink or reparse point",
                )
            )
            continue
        if entry.is_dir():
            try:
                child_names = {child.name.casefold() for child in entry.iterdir()}
            except OSError:
                campaign_issues.append(
                    _issue(
                        "campaign_entry_unreadable",
                        "error",
                        "<untrusted-entry>",
                        "campaign directory entry cannot be inspected",
                    )
                )
                continue
            if not child_names.intersection(reserved_run_names):
                campaign_issues.append(
                    _issue(
                        "unexpected_file",
                        "error" if strict else "warning",
                        "<unexpected-entry>",
                        "unexpected campaign entry is not manifested evidence",
                    )
                )
                continue
            try:
                validate_path_component(entry.name, label="run_id")
            except EvidencePathError:
                campaign_issues.append(
                    _issue(
                        "invalid_run_path",
                        "error",
                        "<invalid-run-entry>",
                        "run directory name is not a safe path component",
                    )
                )
            else:
                run_paths.append(entry)
            continue
        if entry.name in CAMPAIGN_FILENAMES:
            continue
        if entry.name.casefold() in reserved_run_names | reserved_campaign_names:
            campaign_issues.append(
                _issue(
                    "reserved_contract_file",
                    "error",
                    relative,
                    "run contract file appears at campaign scope",
                )
            )
            continue
        campaign_issues.append(
            _issue(
                "unexpected_file",
                "error" if strict else "warning",
                "<unexpected-entry>",
                "unexpected campaign entry is not manifested evidence",
            )
        )

    if not run_paths:
        campaign_issues.append(
            _issue(
                "no_run_bundles",
                "error",
                campaign_id,
                "campaign contains no verifiable run bundle",
            )
        )

    runs = tuple(
        _verify_run(campaign_id, run_path, strict=strict)
        for run_path in sorted(
            run_paths,
            key=lambda path: (path.name.casefold(), path.name),
        )
    )
    all_issues = [*campaign_issues]
    for run in runs:
        all_issues.extend(run.issues)
    return VerificationReport(
        root=root_path,
        campaign_id=campaign_id,
        runs=runs,
        issues=tuple(all_issues),
    )


def verify_campaign(
    root: str | Path,
    campaign_id: str,
    *,
    strict: bool = False,
) -> VerificationReport:
    """Verify run bundles and the four hash-linked campaign evidence files."""

    run_report = verify_run_bundles(root, campaign_id, strict=strict)
    campaign_path = run_report.root / campaign_id
    if (
        not campaign_path.exists()
        or is_symlink_or_reparse(campaign_path)
        or not campaign_path.is_dir()
    ):
        return run_report

    issues = list(run_report.issues)
    entries: dict[str, Path] = {}
    try:
        entries = {entry.name: entry for entry in campaign_path.iterdir()}
    except OSError:
        issues = [*run_report.issues]
        issues.append(
            _issue(
                "campaign_unreadable",
                "error",
                campaign_id,
                "campaign evidence directory cannot be read",
            )
        )
        return VerificationReport(
            root=run_report.root,
            campaign_id=campaign_id,
            runs=run_report.runs,
            issues=tuple(issues),
        )

    artifact_bytes: dict[str, bytes] = {}
    for artifact_name in sorted(CAMPAIGN_FILENAMES):
        artifact_path = entries.get(artifact_name)
        if artifact_path is None:
            issues.append(
                _issue(
                    "missing_campaign_artifact",
                    "error",
                    artifact_name,
                    "required campaign evidence file is missing",
                )
            )
            continue
        if is_symlink_or_reparse(artifact_path):
            issues.append(
                _issue(
                    "symlink_not_allowed",
                    "error",
                    artifact_name,
                    "campaign evidence file is a symlink or reparse point",
                )
            )
            continue
        if not artifact_path.is_file():
            issues.append(
                _issue(
                    "campaign_artifact_not_file",
                    "error",
                    artifact_name,
                    "required campaign evidence path is not a regular file",
                )
            )
            continue
        try:
            artifact_bytes[artifact_name] = artifact_path.read_bytes()
        except OSError:
            issues.append(
                _issue(
                    "campaign_artifact_unreadable",
                    "error",
                    artifact_name,
                    "campaign evidence file cannot be read",
                )
            )

    payloads = {
        name: _campaign_json_payload(name, content, issues)
        for name, content in artifact_bytes.items()
    }
    _verify_campaign_payload_shapes(payloads, issues)
    _verify_campaign_summary(
        campaign_id,
        payloads.get(CAMPAIGN_SUMMARY_FILENAME),
        artifact_bytes,
        issues,
    )
    return VerificationReport(
        root=run_report.root,
        campaign_id=campaign_id,
        runs=run_report.runs,
        issues=tuple(issues),
    )


def _campaign_json_payload(
    artifact_name: str,
    artifact_bytes: bytes,
    issues: list[VerificationIssue],
) -> object | None:
    try:
        payload = json.loads(
            artifact_bytes.decode("utf-8"),
            parse_constant=_reject_json_constant,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError, RecursionError):
        issues.append(
            _issue(
                "invalid_campaign_json",
                "error",
                artifact_name,
                "campaign evidence is not valid finite UTF-8 JSON",
            )
        )
        return None
    try:
        canonical = canonical_json_bytes(payload)
    except (TypeError, ValueError, UnicodeError, RecursionError):
        issues.append(
            _issue(
                "invalid_campaign_json",
                "error",
                artifact_name,
                "campaign evidence is not valid finite UTF-8 JSON",
            )
        )
        return None
    if canonical != artifact_bytes:
        issues.append(
            _issue(
                "noncanonical_json",
                "error",
                artifact_name,
                "campaign evidence JSON is not canonical",
            )
        )
    if contains_sensitive_fields(payload):
        issues.append(
            _issue(
                "sensitive_evidence",
                "error",
                artifact_name,
                "campaign evidence contains a prohibited sensitive field",
            )
        )
    return payload


def _verify_campaign_payload_shapes(
    payloads: Mapping[str, object | None],
    issues: list[VerificationIssue],
) -> None:
    expected_shapes = {
        "baseline_candidate_diff.json": dict,
        "release_gate.json": dict,
        "regression_cases.json": list,
    }
    for artifact_name, expected_type in expected_shapes.items():
        payload = payloads.get(artifact_name)
        if payload is not None and not isinstance(payload, expected_type):
            issues.append(
                _issue(
                    "invalid_campaign_artifact",
                    "error",
                    artifact_name,
                    "campaign evidence has an invalid top-level shape",
                )
            )
        if (
            isinstance(payload, dict)
            and "schema_version" in payload
            and payload.get("schema_version") != 1
        ):
            issues.append(
                _issue(
                    "unsupported_campaign_schema",
                    "error",
                    artifact_name,
                    "campaign evidence schema version is unsupported",
                )
            )


def _verify_campaign_summary(
    campaign_id: str,
    payload: object | None,
    artifact_bytes: Mapping[str, bytes],
    issues: list[VerificationIssue],
) -> None:
    if payload is None:
        return
    required_fields = {"schema_version", "campaign_id", "summary", "artifacts"}
    if (
        not isinstance(payload, dict)
        or set(payload) != required_fields
        or payload.get("schema_version") != 1
        or not isinstance(payload.get("summary"), dict)
        or not isinstance(payload.get("artifacts"), dict)
    ):
        issues.append(
            _issue(
                "invalid_campaign_summary",
                "error",
                CAMPAIGN_SUMMARY_FILENAME,
                "campaign summary does not satisfy the evidence envelope",
            )
        )
        return
    if payload.get("campaign_id") != campaign_id:
        issues.append(
            _issue(
                "campaign_id_mismatch",
                "error",
                CAMPAIGN_SUMMARY_FILENAME,
                "campaign summary ID does not match its directory",
            )
        )
    declared_artifacts = payload["artifacts"]
    if set(declared_artifacts) != set(CAMPAIGN_ARTIFACT_FILENAMES) or not all(
        isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest)
        for digest in declared_artifacts.values()
    ):
        issues.append(
            _issue(
                "campaign_artifact_set_mismatch",
                "error",
                CAMPAIGN_SUMMARY_FILENAME,
                "campaign summary artifact set or digest is invalid",
            )
        )
        return
    for artifact_name in CAMPAIGN_ARTIFACT_FILENAMES:
        content = artifact_bytes.get(artifact_name)
        if content is None:
            continue
        expected_digest = declared_artifacts[artifact_name]
        actual_digest = sha256_bytes(content)
        if actual_digest != expected_digest:
            issues.append(
                _issue(
                    "campaign_hash_mismatch",
                    "error",
                    artifact_name,
                    "campaign artifact SHA-256 does not match campaign summary",
                    expected_sha256=expected_digest,
                    actual_sha256=actual_digest,
                )
            )


def _verify_run(campaign_id: str, run_path: Path, *, strict: bool) -> RunVerification:
    run_id = run_path.name
    manifest_path = run_path / MANIFEST_FILENAME
    issues: list[VerificationIssue] = []
    entries: dict[str, Path] = {}
    try:
        listed_entries = sorted(
            run_path.iterdir(),
            key=lambda entry: (entry.name.casefold(), entry.name),
        )
    except OSError:
        listed_entries = []
        issues.append(
            _run_issue(
                run_id,
                "run_unreadable",
                "error",
                "",
                "run evidence directory cannot be read",
            )
        )

    reserved_names = {name.casefold() for name in RUN_FILENAMES | CAMPAIGN_FILENAMES}
    for entry in listed_entries:
        entries[entry.name] = entry
        relative = entry.name
        if is_symlink_or_reparse(entry):
            issues.append(
                _run_issue(
                    run_id,
                    "symlink_not_allowed",
                    "error",
                    (relative if entry.name in RUN_FILENAMES else "<untrusted-entry>"),
                    "run evidence entry is a symlink or reparse point",
                )
            )
            continue
        if entry.name not in RUN_FILENAMES:
            if entry.name.casefold() in reserved_names:
                code = "reserved_contract_file"
                message = "case-conflicting reserved contract filename is not allowed"
                severity: Literal["error", "warning"] = "error"
            else:
                code = "unexpected_file"
                message = "unexpected run entry is not manifested evidence"
                severity = "error" if strict else "warning"
            reported_path = (
                relative if code == "reserved_contract_file" else "<unexpected-entry>"
            )
            issues.append(_run_issue(run_id, code, severity, reported_path, message))

    for required_name in RUN_FILENAMES:
        if required_name not in entries:
            code = (
                "missing_manifest"
                if required_name == MANIFEST_FILENAME
                else "missing_artifact"
            )
            issues.append(
                _run_issue(
                    run_id,
                    code,
                    "error",
                    required_name,
                    "required run evidence file is missing",
                )
            )
        elif (
            not is_symlink_or_reparse(entries[required_name])
            and not entries[required_name].is_file()
        ):
            issues.append(
                _run_issue(
                    run_id,
                    "artifact_not_file",
                    "error",
                    required_name,
                    "required run evidence path is not a regular file",
                )
            )

    manifest_entry = entries.get(MANIFEST_FILENAME)
    if (
        manifest_entry is None
        or is_symlink_or_reparse(manifest_entry)
        or not manifest_entry.is_file()
    ):
        return RunVerification(
            campaign_id=campaign_id,
            run_id=run_id,
            manifest_path=manifest_path,
            manifest=None,
            issues=tuple(issues),
        )

    try:
        manifest_bytes = manifest_entry.read_bytes()
        raw_manifest = json.loads(
            manifest_bytes.decode("utf-8"),
            parse_constant=_reject_json_constant,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, RecursionError):
        issues.append(
            _run_issue(
                run_id,
                "malformed_manifest",
                "error",
                MANIFEST_FILENAME,
                "evidence manifest is not readable canonical UTF-8 JSON",
            )
        )
        return RunVerification(
            campaign_id=campaign_id,
            run_id=run_id,
            manifest_path=manifest_path,
            manifest=None,
            issues=tuple(issues),
        )

    if not isinstance(raw_manifest, dict):
        issues.append(
            _run_issue(
                run_id,
                "malformed_manifest",
                "error",
                MANIFEST_FILENAME,
                "evidence manifest root must be an object",
            )
        )
        return RunVerification(
            campaign_id=campaign_id,
            run_id=run_id,
            manifest_path=manifest_path,
            manifest=None,
            issues=tuple(issues),
        )
    if raw_manifest.get("schema_version") != SUPPORTED_MANIFEST_SCHEMA_VERSION:
        issues.append(
            _run_issue(
                run_id,
                "unsupported_manifest_schema",
                "error",
                MANIFEST_FILENAME,
                "evidence manifest schema version is unsupported",
            )
        )
        return RunVerification(
            campaign_id=campaign_id,
            run_id=run_id,
            manifest_path=manifest_path,
            manifest=None,
            issues=tuple(issues),
        )
    try:
        canonical_manifest = canonical_json_bytes(raw_manifest)
    except (TypeError, ValueError, UnicodeError, RecursionError):
        issues.append(
            _run_issue(
                run_id,
                "malformed_manifest",
                "error",
                MANIFEST_FILENAME,
                "evidence manifest cannot be represented as canonical UTF-8 JSON",
            )
        )
        return RunVerification(
            campaign_id=campaign_id,
            run_id=run_id,
            manifest_path=manifest_path,
            manifest=None,
            issues=tuple(issues),
        )
    if canonical_manifest != manifest_bytes:
        issues.append(
            _run_issue(
                run_id,
                "noncanonical_json",
                "error",
                MANIFEST_FILENAME,
                "evidence manifest JSON is not canonical",
            )
        )

    declared_artifacts = raw_manifest.get("artifacts")
    if isinstance(declared_artifacts, dict):
        for declared_path in declared_artifacts:
            if not _is_safe_artifact_name(declared_path):
                issues.append(
                    _run_issue(
                        run_id,
                        "invalid_artifact_path",
                        "error",
                        MANIFEST_FILENAME,
                        "manifest artifact path is not a safe contract filename",
                    )
                )
        if set(declared_artifacts) != set(RUN_ARTIFACT_FILENAMES):
            issues.append(
                _run_issue(
                    run_id,
                    "manifest_artifact_set_mismatch",
                    "error",
                    MANIFEST_FILENAME,
                    "manifest artifact set does not match the run contract",
                )
            )

    try:
        manifest = EvidenceManifest.model_validate_json(manifest_bytes)
    except ValidationError:
        issues.append(
            _run_issue(
                run_id,
                "malformed_manifest",
                "error",
                MANIFEST_FILENAME,
                "evidence manifest does not satisfy the strict domain contract",
            )
        )
        return RunVerification(
            campaign_id=campaign_id,
            run_id=run_id,
            manifest_path=manifest_path,
            manifest=None,
            issues=tuple(issues),
        )

    if manifest.campaign_id != campaign_id:
        issues.append(
            _run_issue(
                run_id,
                "campaign_id_mismatch",
                "error",
                MANIFEST_FILENAME,
                "manifest campaign ID does not match its directory",
            )
        )
    if manifest.run_id != run_id:
        issues.append(
            _run_issue(
                run_id,
                "run_id_mismatch",
                "error",
                MANIFEST_FILENAME,
                "manifest run ID does not match its directory",
            )
        )

    verified_bytes: dict[str, bytes] = {}
    for artifact_name in RUN_ARTIFACT_FILENAMES:
        artifact_path = entries.get(artifact_name)
        expected_digest = manifest.artifacts.get(artifact_name)
        if (
            artifact_path is None
            or expected_digest is None
            or is_symlink_or_reparse(artifact_path)
            or not artifact_path.is_file()
        ):
            continue
        try:
            artifact_bytes = artifact_path.read_bytes()
        except OSError:
            issues.append(
                _run_issue(
                    run_id,
                    "artifact_unreadable",
                    "error",
                    artifact_name,
                    "covered artifact cannot be read",
                )
            )
            continue
        actual_digest = sha256_bytes(artifact_bytes)
        if actual_digest != expected_digest:
            issues.append(
                _run_issue(
                    run_id,
                    "hash_mismatch",
                    "error",
                    artifact_name,
                    "covered artifact SHA-256 does not match the manifest",
                    expected_sha256=expected_digest,
                    actual_sha256=actual_digest,
                )
            )
            continue
        verified_bytes[artifact_name] = artifact_bytes

    scenario = _verify_scenario(run_id, verified_bytes.get("scenario.yaml"), issues)
    _verify_agent_envelope(
        run_id,
        verified_bytes.get("agent_version.json"),
        issues,
    )
    turns = _verify_transcript(run_id, verified_bytes.get("transcript.json"), issues)
    calls = _verify_tool_calls(run_id, verified_bytes.get("tool_calls.json"), issues)
    if turns is not None and calls is not None:
        if not evidence_ids_are_unique(turns, calls, ()):
            issues.append(
                _run_issue(
                    run_id,
                    "duplicate_evidence_id",
                    "error",
                    "transcript.json",
                    "run evidence contains duplicate logical identifiers",
                )
            )
        if not tool_call_turns_resolve(turns, calls):
            issues.append(
                _run_issue(
                    run_id,
                    "dangling_turn_id",
                    "error",
                    "tool_calls.json",
                    "tool-call evidence references a missing transcript turn",
                )
            )
    states = _verify_timing(
        run_id,
        manifest,
        verified_bytes.get("timing.json"),
        issues,
    )
    _verify_evaluations(
        run_id,
        manifest,
        verified_bytes.get("evaluations.json"),
        issues,
        scenario=scenario,
        turns=turns,
        tool_calls=calls,
        states=states,
    )
    return RunVerification(
        campaign_id=campaign_id,
        run_id=run_id,
        manifest_path=manifest_path,
        manifest=manifest,
        issues=tuple(issues),
    )


def _verify_scenario(
    run_id: str,
    artifact_bytes: bytes | None,
    issues: list[VerificationIssue],
) -> ScenarioDefinition | None:
    if artifact_bytes is None:
        return None
    try:
        parsed = yaml.safe_load(artifact_bytes.decode("utf-8"))
        scenario = ScenarioDefinition.model_validate(parsed)
    except (UnicodeError, yaml.YAMLError, ValidationError, RecursionError):
        issues.append(
            _run_issue(
                run_id,
                "invalid_scenario",
                "error",
                "scenario.yaml",
                "scenario evidence does not satisfy Scenario v1",
            )
        )
        return None
    if contains_sensitive_fields(parsed):
        issues.append(
            _run_issue(
                run_id,
                "sensitive_evidence",
                "error",
                "scenario.yaml",
                "scenario evidence contains a prohibited sensitive field",
            )
        )
    return scenario


def _verify_agent_envelope(
    run_id: str,
    artifact_bytes: bytes | None,
    issues: list[VerificationIssue],
) -> None:
    if artifact_bytes is None:
        return
    payload = _canonical_json_payload(
        run_id, "agent_version.json", artifact_bytes, issues
    )
    if not isinstance(payload, dict) or set(payload) != {
        "schema_version",
        "agent_version",
        "config",
    }:
        issues.append(
            _run_issue(
                run_id,
                "invalid_agent_envelope",
                "error",
                "agent_version.json",
                "agent evidence must contain version and effective config",
            )
        )
        return
    if payload.get("schema_version") != 1 or not isinstance(
        payload.get("config"), dict
    ):
        issues.append(
            _run_issue(
                run_id,
                "invalid_agent_envelope",
                "error",
                "agent_version.json",
                "agent evidence envelope schema or config is invalid",
            )
        )
        return
    try:
        agent_version = AgentVersion.model_validate_json(
            canonical_json_bytes(payload.get("agent_version"))
        )
        config_digest = sha256_bytes(canonical_json_bytes(payload["config"]))
    except (ValidationError, TypeError, ValueError, RecursionError):
        issues.append(
            _run_issue(
                run_id,
                "invalid_agent_envelope",
                "error",
                "agent_version.json",
                "agent evidence envelope is malformed",
            )
        )
        return
    if config_digest != agent_version.config_sha256:
        issues.append(
            _run_issue(
                run_id,
                "agent_config_digest_mismatch",
                "error",
                "agent_version.json",
                "effective agent config does not match its version digest",
                expected_sha256=agent_version.config_sha256,
                actual_sha256=config_digest,
            )
        )


def _verify_transcript(
    run_id: str,
    artifact_bytes: bytes | None,
    issues: list[VerificationIssue],
) -> list[ConversationTurn] | None:
    if artifact_bytes is None:
        return None
    payload = _canonical_json_payload(run_id, "transcript.json", artifact_bytes, issues)
    try:
        if not isinstance(payload, list):
            raise TypeError
        turns = TypeAdapter(list[ConversationTurn]).validate_json(artifact_bytes)
    except (TypeError, ValidationError, RecursionError):
        issues.append(
            _run_issue(
                run_id,
                "invalid_transcript",
                "error",
                "transcript.json",
                "transcript evidence is not a list of typed turns",
            )
        )
        return None
    if any(turn.run_id != run_id for turn in turns):
        issues.append(
            _run_issue(
                run_id,
                "artifact_run_id_mismatch",
                "error",
                "transcript.json",
                "transcript turn references another run",
            )
        )
    return turns


def _verify_tool_calls(
    run_id: str,
    artifact_bytes: bytes | None,
    issues: list[VerificationIssue],
) -> list[ObservedToolCall] | None:
    if artifact_bytes is None:
        return None
    payload = _canonical_json_payload(run_id, "tool_calls.json", artifact_bytes, issues)
    try:
        if not isinstance(payload, list):
            raise TypeError
        calls = TypeAdapter(list[ObservedToolCall]).validate_json(artifact_bytes)
    except (TypeError, ValidationError, RecursionError):
        issues.append(
            _run_issue(
                run_id,
                "invalid_tool_calls",
                "error",
                "tool_calls.json",
                "tool-call evidence is not a list of typed calls",
            )
        )
        return None
    if any(call.run_id != run_id for call in calls):
        issues.append(
            _run_issue(
                run_id,
                "artifact_run_id_mismatch",
                "error",
                "tool_calls.json",
                "tool-call evidence references another run",
            )
        )
    return calls


def _verify_timing(
    run_id: str,
    manifest: EvidenceManifest,
    artifact_bytes: bytes | None,
    issues: list[VerificationIssue],
) -> tuple[dict[str, object], dict[str, object]] | None:
    if artifact_bytes is None:
        return None
    payload = _canonical_json_payload(run_id, "timing.json", artifact_bytes, issues)
    if not isinstance(payload, dict):
        issues.append(
            _run_issue(
                run_id,
                "invalid_timing",
                "error",
                "timing.json",
                "timing evidence must be an object",
            )
        )
        return None
    initial_state = payload.get("initial_state")
    observed_final_state = payload.get("observed_final_state")
    if not isinstance(initial_state, dict) or not isinstance(
        observed_final_state, dict
    ):
        issues.append(
            _run_issue(
                run_id,
                "invalid_timing",
                "error",
                "timing.json",
                "timing evidence must contain initial and observed state objects",
            )
        )
        return None
    if payload.get("started_at") != _utc_json_time(manifest.started_at) or payload.get(
        "finished_at"
    ) != _utc_json_time(manifest.finished_at):
        issues.append(
            _run_issue(
                run_id,
                "timing_manifest_mismatch",
                "error",
                "timing.json",
                "timing evidence does not match manifest timestamps",
            )
        )
    return initial_state, observed_final_state


def _verify_evaluations(
    run_id: str,
    manifest: EvidenceManifest,
    artifact_bytes: bytes | None,
    issues: list[VerificationIssue],
    *,
    scenario: ScenarioDefinition | None,
    turns: list[ConversationTurn] | None,
    tool_calls: list[ObservedToolCall] | None,
    states: tuple[dict[str, object], dict[str, object]] | None,
) -> None:
    if artifact_bytes is None:
        return
    payload = _canonical_json_payload(
        run_id, "evaluations.json", artifact_bytes, issues
    )
    try:
        if not isinstance(payload, list) or not payload:
            raise TypeError
        evaluations = TypeAdapter(list[EvaluationResult]).validate_json(artifact_bytes)
    except (TypeError, ValidationError, RecursionError):
        issues.append(
            _run_issue(
                run_id,
                "invalid_evaluations",
                "error",
                "evaluations.json",
                "evaluation evidence is not a non-empty list of typed results",
            )
        )
        return
    if any(evaluation.run_id != run_id for evaluation in evaluations):
        issues.append(
            _run_issue(
                run_id,
                "artifact_run_id_mismatch",
                "error",
                "evaluations.json",
                "evaluation evidence references another run",
            )
        )
    actual_versions = sorted({evaluation.evaluator_id for evaluation in evaluations})
    if not evidence_ids_are_unique((), (), evaluations):
        issues.append(
            _run_issue(
                run_id,
                "duplicate_evidence_id",
                "error",
                "evaluations.json",
                "evaluation evidence contains duplicate logical identifiers",
            )
        )
    if manifest.evaluator_versions != actual_versions:
        issues.append(
            _run_issue(
                run_id,
                "evaluator_versions_mismatch",
                "error",
                "evaluations.json",
                "manifest evaluator versions do not match evaluation evidence",
            )
        )
    if deterministic_final_state(evaluations) is not manifest.final_state:
        issues.append(
            _run_issue(
                run_id,
                "final_state_mismatch",
                "error",
                MANIFEST_FILENAME,
                "manifest final state does not match deterministic evaluations",
            )
        )
    if (
        scenario is not None
        and turns is not None
        and tool_calls is not None
        and states is not None
    ):
        mis_ids = {
            "artifact": (
                {manifest.mis_artifact_id}
                if manifest.mis_artifact_id is not None
                else set()
            ),
            "plan_evidence_manifest": (
                {manifest.mis_plan_evidence_manifest_id}
                if manifest.mis_plan_evidence_manifest_id is not None
                else set()
            ),
            "tool_call": {
                call.mis_tool_call_id
                for call in tool_calls
                if call.mis_tool_call_id is not None
            },
            "evaluation": {
                evaluation.mis_evaluation_id
                for evaluation in evaluations
                if evaluation.mis_evaluation_id is not None
            },
        }
        if not evidence_references_resolve(
            evaluations,
            scenario=scenario,
            turns=turns,
            tool_calls=tool_calls,
            observed_final_state=states[1],
            mis_ids=mis_ids,
        ):
            issues.append(
                _run_issue(
                    run_id,
                    "invalid_evidence_ref",
                    "error",
                    "evaluations.json",
                    "evaluation evidence contains a malformed or unresolved reference",
                )
            )


def _canonical_json_payload(
    run_id: str,
    artifact_name: str,
    artifact_bytes: bytes,
    issues: list[VerificationIssue],
) -> object | None:
    try:
        payload = json.loads(artifact_bytes.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        issues.append(
            _run_issue(
                run_id,
                "invalid_json_artifact",
                "error",
                artifact_name,
                "covered JSON artifact is not valid UTF-8 JSON",
            )
        )
        return None
    try:
        canonical = canonical_json_bytes(payload)
    except (TypeError, ValueError, UnicodeError, RecursionError):
        canonical = b""
    if canonical != artifact_bytes:
        issues.append(
            _run_issue(
                run_id,
                "noncanonical_json",
                "error",
                artifact_name,
                "covered JSON artifact is not canonical",
            )
        )
    if contains_sensitive_fields(payload):
        issues.append(
            _run_issue(
                run_id,
                "sensitive_evidence",
                "error",
                artifact_name,
                "covered JSON artifact contains a prohibited sensitive field",
            )
        )
    return payload


def _is_safe_artifact_name(value: object) -> bool:
    if not isinstance(value, str) or value not in RUN_ARTIFACT_FILENAMES:
        return False
    windows_path = PureWindowsPath(value)
    return (
        not windows_path.drive
        and not windows_path.root
        and len(windows_path.parts) == 1
    )


def _reject_json_constant(value: str) -> None:
    del value
    raise ValueError("non-finite JSON constants are not allowed")


def _is_optional_judge(evaluator_id: str) -> bool:
    return evaluator_id == "llm_judge.v1" or evaluator_id.startswith("llm_judge.")


def _utc_json_time(value) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _run_issue(
    run_id: str,
    code: str,
    severity: Literal["error", "warning"],
    artifact_path: str,
    message: str,
    *,
    expected_sha256: str | None = None,
    actual_sha256: str | None = None,
) -> VerificationIssue:
    path = f"{run_id}/{artifact_path}" if artifact_path else run_id
    return _issue(
        code,
        severity,
        path,
        message,
        expected_sha256=expected_sha256,
        actual_sha256=actual_sha256,
    )


def _issue(
    code: str,
    severity: Literal["error", "warning"],
    path: str,
    message: str,
    *,
    expected_sha256: str | None = None,
    actual_sha256: str | None = None,
) -> VerificationIssue:
    return VerificationIssue(
        code=code,
        severity=severity,
        path=path.replace("\\", "/"),
        message=message,
        expected_sha256=expected_sha256,
        actual_sha256=actual_sha256,
    )


__all__ = [
    "CAMPAIGN_ARTIFACT_FILENAMES",
    "CAMPAIGN_FILENAMES",
    "CAMPAIGN_SUMMARY_FILENAME",
    "EvidenceError",
    "EvidenceInputError",
    "EvidencePathError",
    "EvidenceWriteError",
    "MANIFEST_FILENAME",
    "RUN_ARTIFACT_FILENAMES",
    "RUN_FILENAMES",
    "RunVerification",
    "VerificationIssue",
    "VerificationReport",
    "canonical_json_bytes",
    "contains_sensitive_fields",
    "deterministic_final_state",
    "sha256_bytes",
    "validate_path_component",
    "verify_campaign",
    "verify_run_bundles",
]
