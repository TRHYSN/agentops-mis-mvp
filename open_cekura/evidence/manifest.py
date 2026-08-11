"""Canonical evidence hashing and offline run-bundle verification."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PureWindowsPath
from typing import Literal, Mapping, Sequence

import yaml
from pydantic import BaseModel, TypeAdapter, ValidationError

from open_cekura.domain.enums import EvaluationStatus, RunFinalState
from open_cekura.domain.ids import stable_id
from open_cekura.domain.models import (
    AgentUnderTest,
    AgentVersion,
    Campaign,
    ConversationTurn,
    EvaluationResult,
    EvidenceManifest,
    ObservedToolCall,
    RegressionCase,
    ReleaseGateDecision,
    ScenarioSuite,
)
from open_cekura.evaluation.aggregation import (
    EvaluationResultGroup,
    RunMetricFacts,
    aggregate_campaign_metrics,
)
from open_cekura.release_gate.policy import (
    CampaignGateInput,
    evaluate_release_gate_for_policy,
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
    "gate_history.json",
    "release_gate.json",
    "regression_cases.json",
)
CAMPAIGN_SUMMARY_FILENAME = "campaign_summary.json"
GATE_SNAPSHOTS_DIRNAME = "gates"
GATE_SNAPSHOT_FILENAMES = frozenset(
    ("baseline_candidate_diff.json", "release_gate.json")
)
CAMPAIGN_FILENAMES = frozenset(
    (CAMPAIGN_SUMMARY_FILENAME, *CAMPAIGN_ARTIFACT_FILENAMES)
)
SUPPORTED_MANIFEST_SCHEMA_VERSION = 2
SUPPORTED_CAMPAIGN_SCHEMA_VERSION = 2

# Offline verification is intentionally bounded before allocation/parsing.  These
# limits cover the public v0 evidence contract while preventing a local artifact
# tree from driving unbounded directory walks or reads.
MAX_CAMPAIGN_ENTRIES = 1_100
MAX_RUNS_PER_CAMPAIGN = 1_000
MAX_RUN_ENTRIES = 32
MAX_MANIFEST_BYTES = 1 * 1024 * 1024
MAX_RUN_ARTIFACT_BYTES = 16 * 1024 * 1024
MAX_CAMPAIGN_ARTIFACT_BYTES = 16 * 1024 * 1024
MAX_GATE_HISTORY_ENTRIES = 256
MAX_JSON_NESTING = 128
MAX_COMPARISON_DEPTH = 8
MAX_VERIFICATION_CAMPAIGNS = 64
MAX_VERIFICATION_GATE_SNAPSHOTS = 1_024
MAX_VERIFICATION_WORK_UNITS = 8_192

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
_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)(?:^|[\s?&;,])"
    r"(?:[a-z][a-z0-9]{1,31}[_-])?"
    r"(?:api[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret|"
    r"password|private[_-]?key|refresh[_-]?token|session[_-]?token)"
    r"\s*[:=]\s*[\"']?[^\s\"'&,;]{4,}"
)
_AUTHORIZATION_VALUE = re.compile(
    r"(?i)\bauthorization\s*:\s*(?:bearer|basic)\s+[^\s,;]{4,}"
)
_PRIVATE_KEY_MARKER = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")
_OPENAI_STYLE_SECRET = re.compile(r"(?<![A-Za-z0-9_-])sk-[A-Za-z0-9_-]{8,}")
_GITHUB_STYLE_SECRET = re.compile(
    r"(?<![A-Za-z0-9_])(?:gh[pousr]_[A-Za-z0-9]{20,}|"
    r"github_pat_[A-Za-z0-9_]{20,})"
)
_JWT_SECRET = re.compile(
    r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\."
    r"[A-Za-z0-9_-]{8,}(?![A-Za-z0-9_-])"
)
_BARE_BEARER_SECRET = re.compile(
    r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{8,}={0,2}(?![A-Za-z0-9._~+/-])"
)
_CREDENTIAL_URL = re.compile(
    r"(?i)\b[a-z][a-z0-9+.-]*://[^\s/:@]+:[^\s/@]+@[^\s/]+"
)


class EvidenceError(ValueError):
    """Base exception for invalid or unsafe filesystem evidence operations."""


class EvidencePathError(EvidenceError):
    """A configured evidence path is unsafe or leaves its declared root."""


class EvidenceInputError(EvidenceError):
    """Run evidence cannot satisfy the frozen bundle contract."""


class EvidenceWriteError(EvidenceError):
    """An artifact could not be committed atomically."""


class _EvidenceLimitExceeded(EvidenceError):
    """A local artifact exceeds a documented verifier resource bound."""


class _VerificationBudgetExceeded(EvidenceError):
    """The shared recursive verification work budget is exhausted."""


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
class VerifiedCampaignArtifact:
    """Immutable bytes read and checked during one campaign verification pass."""

    name: str
    sha256: str
    content: bytes


@dataclass(frozen=True, slots=True)
class VerifiedGateSnapshot:
    """Immutable gate and comparison bytes covered by the history index."""

    gate_id: str
    release_gate: bytes
    baseline_candidate_diff: bytes


@dataclass(frozen=True, slots=True)
class VerificationReport:
    root: Path
    campaign_id: str
    runs: tuple[RunVerification, ...]
    issues: tuple[VerificationIssue, ...]
    campaign_artifacts: tuple[VerifiedCampaignArtifact, ...] = ()
    gate_snapshots: tuple[VerifiedGateSnapshot, ...] = ()

    @property
    def ok(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)


@dataclass(slots=True)
class VerificationContext:
    """One bounded memoization scope for a top-level campaign verification."""

    root: Path
    strict: bool
    max_campaigns: int = field(
        default_factory=lambda: MAX_VERIFICATION_CAMPAIGNS
    )
    max_gate_snapshots: int = field(
        default_factory=lambda: MAX_VERIFICATION_GATE_SNAPSHOTS
    )
    max_work_units: int = field(
        default_factory=lambda: MAX_VERIFICATION_WORK_UNITS
    )
    campaign_reports: dict[
        tuple[str, frozenset[str]], VerificationReport
    ] = field(default_factory=dict)
    campaigns: int = 0
    gate_snapshots: int = 0
    work_units: int = 0
    exhausted_limit: str | None = None

    def begin_campaign(self) -> None:
        self._consume(campaigns=1, work_units=1)

    def consume_gate_snapshot(self) -> None:
        self._consume(gate_snapshots=1, work_units=1)

    def consume_work(self, units: int = 1) -> None:
        if type(units) is not int or units < 1:
            raise ValueError("verification work units must be a positive integer")
        self._consume(work_units=units)

    def _consume(
        self,
        *,
        campaigns: int = 0,
        gate_snapshots: int = 0,
        work_units: int = 0,
    ) -> None:
        if self.exhausted_limit is not None:
            raise _VerificationBudgetExceeded(self.exhausted_limit)
        proposed = (
            ("campaign", self.campaigns + campaigns, self.max_campaigns),
            (
                "gate snapshot",
                self.gate_snapshots + gate_snapshots,
                self.max_gate_snapshots,
            ),
            ("work unit", self.work_units + work_units, self.max_work_units),
        )
        for label, value, limit in proposed:
            if value > limit:
                self.exhausted_limit = label
                raise _VerificationBudgetExceeded(label)
        self.campaigns += campaigns
        self.gate_snapshots += gate_snapshots
        self.work_units += work_units


def verified_campaign_json(
    report: VerificationReport,
    artifact_name: str,
) -> object:
    """Hydrate one campaign JSON value from the verifier's immutable snapshot."""

    if not isinstance(report, VerificationReport) or not report.ok:
        raise EvidenceInputError("campaign verification report is not successful")
    if artifact_name not in CAMPAIGN_FILENAMES:
        raise EvidenceInputError("artifact_name is not a campaign contract file")
    matching = [
        artifact for artifact in report.campaign_artifacts if artifact.name == artifact_name
    ]
    if len(matching) != 1:
        raise EvidenceInputError("verified campaign artifact snapshot is unavailable")
    snapshot = matching[0]
    if sha256_bytes(snapshot.content) != snapshot.sha256:
        raise EvidenceInputError("verified campaign artifact snapshot is corrupt")
    try:
        return json.loads(
            snapshot.content.decode("utf-8"),
            parse_constant=_reject_json_constant,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError, RecursionError) as error:
        raise EvidenceInputError(
            "verified campaign artifact snapshot is invalid"
        ) from error


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
        elif isinstance(current, str) and _contains_sensitive_text(current):
            return True
    return False


def _contains_sensitive_text(value: str) -> bool:
    return any(
        pattern.search(value) is not None
        for pattern in (
            _SENSITIVE_ASSIGNMENT,
            _AUTHORIZATION_VALUE,
            _PRIVATE_KEY_MARKER,
            _OPENAI_STYLE_SECRET,
            _GITHUB_STYLE_SECRET,
            _JWT_SECRET,
            _BARE_BEARER_SECRET,
            _CREDENTIAL_URL,
        )
    )


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


def _bounded_directory_entries(path: Path, limit: int) -> list[Path]:
    if type(limit) is not int or limit < 1:
        raise _EvidenceLimitExceeded
    entries: list[Path] = []
    iterator = path.iterdir()
    try:
        for entry in iterator:
            if len(entries) >= limit:
                raise _EvidenceLimitExceeded
            entries.append(entry)
    finally:
        close = getattr(iterator, "close", None)
        if close is not None:
            close()
    return entries


def _read_bounded(path: Path, limit: int) -> bytes:
    if type(limit) is not int or limit < 1:
        raise _EvidenceLimitExceeded
    with path.open("rb") as stream:
        content = stream.read(limit + 1)
    if len(content) > limit:
        raise _EvidenceLimitExceeded
    return content


def _json_nesting_exceeds_limit(content: bytes) -> bool:
    depth = 0
    in_string = False
    escaped = False
    for byte in content:
        if in_string:
            if escaped:
                escaped = False
            elif byte == 0x5C:  # backslash
                escaped = True
            elif byte == 0x22:  # double quote
                in_string = False
            continue
        if byte == 0x22:
            in_string = True
        elif byte in (0x5B, 0x7B):  # [ {
            depth += 1
            if depth > MAX_JSON_NESTING:
                return True
        elif byte in (0x5D, 0x7D):  # ] }
            depth = max(0, depth - 1)
    return False


def verify_run_bundles(
    root: str | Path,
    campaign_id: str,
    *,
    strict: bool = False,
    _verification_context: VerificationContext | None = None,
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
            _bounded_directory_entries(campaign_path, MAX_CAMPAIGN_ENTRIES),
            key=lambda entry: (entry.name.casefold(), entry.name),
        )
    except _EvidenceLimitExceeded:
        campaign_issues.append(
            _issue(
                "evidence_limit_exceeded",
                "error",
                campaign_id,
                "campaign directory exceeds the verifier entry limit",
            )
        )
        campaign_entries = []
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
        if entry.name == GATE_SNAPSHOTS_DIRNAME:
            if not entry.is_dir():
                campaign_issues.append(
                    _issue(
                        "gate_history_not_directory",
                        "error",
                        GATE_SNAPSHOTS_DIRNAME,
                        "gate snapshot path must be a real directory",
                    )
                )
            continue
        if entry.name.casefold() == GATE_SNAPSHOTS_DIRNAME.casefold():
            campaign_issues.append(
                _issue(
                    "reserved_contract_file",
                    "error",
                    relative,
                    "case-conflicting gate snapshot directory is not allowed",
                )
            )
            continue
        if entry.is_dir():
            try:
                child_names = {
                    child.name.casefold()
                    for child in _bounded_directory_entries(entry, MAX_RUN_ENTRIES)
                }
            except _EvidenceLimitExceeded:
                campaign_issues.append(
                    _issue(
                        "evidence_limit_exceeded",
                        "error",
                        "<untrusted-entry>",
                        "run directory exceeds the verifier entry limit",
                    )
                )
                continue
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

    if len(run_paths) > MAX_RUNS_PER_CAMPAIGN:
        campaign_issues.append(
            _issue(
                "evidence_limit_exceeded",
                "error",
                campaign_id,
                "campaign exceeds the verifier run limit",
            )
        )
        run_paths = run_paths[:MAX_RUNS_PER_CAMPAIGN]
    if not run_paths:
        campaign_issues.append(
            _issue(
                "no_run_bundles",
                "error",
                campaign_id,
                "campaign contains no verifiable run bundle",
            )
        )

    verified_runs: list[RunVerification] = []
    for run_path in sorted(
        run_paths,
        key=lambda path: (path.name.casefold(), path.name),
    ):
        if _verification_context is not None:
            try:
                _verification_context.consume_work()
            except _VerificationBudgetExceeded:
                campaign_issues.append(
                    _verification_budget_issue(
                        campaign_id,
                        _verification_context,
                    )
                )
                break
        verified_runs.append(_verify_run(campaign_id, run_path, strict=strict))
    runs = tuple(verified_runs)
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
    _campaign_stack: frozenset[str] | None = None,
    _verification_context: VerificationContext | None = None,
) -> VerificationReport:
    """Verify a campaign with one bounded context shared by all baselines."""

    validate_path_component(campaign_id, label="campaign_id")
    campaign_stack = _campaign_stack or frozenset()
    if campaign_id in campaign_stack:
        return VerificationReport(
            root=absolute_safe_root(root),
            campaign_id=campaign_id,
            runs=(),
            issues=(
                _issue(
                    "campaign_comparison_cycle",
                    "error",
                    CAMPAIGN_SUMMARY_FILENAME,
                    "campaign comparison contains a verification cycle",
                ),
            ),
        )
    root_path = absolute_safe_root(root)
    top_level = _verification_context is None
    context = _verification_context or VerificationContext(
        root=root_path,
        strict=strict,
    )
    if context.root != root_path or context.strict is not strict:
        raise EvidenceInputError(
            "recursive verification context root and strict mode must remain fixed"
        )
    # A report is only reusable beneath the same verified ancestry.  Reusing a
    # shallow report beneath a deeper branch could otherwise hide the cached
    # subtree from the comparison-depth check; reusing it beneath one of its
    # descendants could likewise hide a cycle.  The global context budgets
    # still bound the extra work for wide DAGs.
    cache_key = (campaign_id, campaign_stack)
    cached = context.campaign_reports.get(cache_key)
    if cached is not None:
        return cached
    try:
        context.begin_campaign()
        report = _verify_campaign_uncached(
            root_path,
            campaign_id,
            strict=strict,
            campaign_stack=campaign_stack | {campaign_id},
            verification_context=context,
        )
    except _VerificationBudgetExceeded:
        report = VerificationReport(
            root=root_path,
            campaign_id=campaign_id,
            runs=(),
            issues=(_verification_budget_issue(campaign_id, context),),
        )
    if top_level and context.exhausted_limit is not None and not any(
        issue.code == "verification_budget_exceeded" for issue in report.issues
    ):
        report = VerificationReport(
            root=report.root,
            campaign_id=report.campaign_id,
            runs=report.runs,
            issues=(
                *report.issues,
                _verification_budget_issue(campaign_id, context),
            ),
            campaign_artifacts=report.campaign_artifacts,
            gate_snapshots=report.gate_snapshots,
        )
    context.campaign_reports[cache_key] = report
    return report


def _verify_campaign_uncached(
    root: Path,
    campaign_id: str,
    *,
    strict: bool,
    campaign_stack: frozenset[str],
    verification_context: VerificationContext,
) -> VerificationReport:
    """Verify one unique campaign inside a shared recursive context."""

    run_report = verify_run_bundles(
        root,
        campaign_id,
        strict=strict,
        _verification_context=verification_context,
    )
    if verification_context.exhausted_limit is not None:
        return run_report
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
        entries = {
            entry.name: entry
            for entry in _bounded_directory_entries(
                campaign_path, MAX_CAMPAIGN_ENTRIES
            )
        }
    except _EvidenceLimitExceeded:
        issues.append(
            _issue(
                "evidence_limit_exceeded",
                "error",
                campaign_id,
                "campaign directory exceeds the verifier entry limit",
            )
        )
        return VerificationReport(
            root=run_report.root,
            campaign_id=campaign_id,
            runs=run_report.runs,
            issues=tuple(issues),
        )
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
            artifact_bytes[artifact_name] = _read_bounded(
                artifact_path, MAX_CAMPAIGN_ARTIFACT_BYTES
            )
        except _EvidenceLimitExceeded:
            issues.append(
                _issue(
                    "evidence_limit_exceeded",
                    "error",
                    artifact_name,
                    "campaign artifact exceeds the verifier byte limit",
                )
            )
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
    gate_snapshots = _verify_gate_history(
        campaign_path,
        campaign_id,
        payloads.get("gate_history.json"),
        artifact_bytes,
        issues,
        verification_context=verification_context,
    )
    _verify_campaign_summary_facts(
        campaign_id,
        payloads.get(CAMPAIGN_SUMMARY_FILENAME),
        payloads,
        run_report.runs,
        issues,
        gate_snapshots=gate_snapshots,
        campaign_root=run_report.root,
        strict=strict,
        campaign_stack=campaign_stack,
        verification_context=verification_context,
    )
    return VerificationReport(
        root=run_report.root,
        campaign_id=campaign_id,
        runs=run_report.runs,
        issues=tuple(issues),
        campaign_artifacts=tuple(
            VerifiedCampaignArtifact(
                name=name,
                sha256=sha256_bytes(content),
                content=content,
            )
            for name, content in sorted(artifact_bytes.items())
        ),
        gate_snapshots=gate_snapshots,
    )


def _campaign_json_payload(
    artifact_name: str,
    artifact_bytes: bytes,
    issues: list[VerificationIssue],
) -> object | None:
    if _json_nesting_exceeds_limit(artifact_bytes):
        issues.append(
            _issue(
                "evidence_limit_exceeded",
                "error",
                artifact_name,
                "campaign JSON exceeds the verifier nesting limit",
            )
        )
        return None
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
        "gate_history.json": dict,
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


def _verify_gate_history(
    campaign_path: Path,
    campaign_id: str,
    payload: object | None,
    campaign_artifacts: Mapping[str, bytes],
    issues: list[VerificationIssue],
    *,
    verification_context: VerificationContext,
) -> tuple[VerifiedGateSnapshot, ...]:
    required = {"schema_version", "campaign_id", "current_gate_id", "entries"}
    if (
        not isinstance(payload, dict)
        or set(payload) != required
        or payload.get("schema_version") != 1
        or payload.get("campaign_id") != campaign_id
        or not isinstance(payload.get("current_gate_id"), str)
        or not isinstance(payload.get("entries"), dict)
    ):
        issues.append(
            _issue(
                "invalid_gate_history",
                "error",
                "gate_history.json",
                "gate history index does not satisfy its strict contract",
            )
        )
        return ()
    entries = payload["entries"]
    current_gate_id = payload["current_gate_id"]
    if (
        not entries
        or len(entries) > MAX_GATE_HISTORY_ENTRIES
        or current_gate_id not in entries
    ):
        issues.append(
            _issue(
                "invalid_gate_history",
                "error",
                "gate_history.json",
                "gate history index is empty, oversized, or lacks its current gate",
            )
        )
        return ()
    for gate_id in entries:
        try:
            validate_path_component(gate_id, label="gate_id")
        except EvidencePathError:
            issues.append(
                _issue(
                    "invalid_gate_history_path",
                    "error",
                    "gate_history.json",
                    "gate history contains an unsafe gate identifier",
                )
            )
            return ()
    if len({gate_id.casefold() for gate_id in entries}) != len(entries):
        issues.append(
            _issue(
                "reserved_contract_file",
                "error",
                "gate_history.json",
                "gate history contains case-conflicting gate identifiers",
            )
        )
        return ()

    gates_path = campaign_path / GATE_SNAPSHOTS_DIRNAME
    if (
        not gates_path.exists()
        or is_symlink_or_reparse(gates_path)
        or not gates_path.is_dir()
    ):
        issues.append(
            _issue(
                "gate_history_missing",
                "error",
                GATE_SNAPSHOTS_DIRNAME,
                "gate snapshot directory is missing or unsafe",
            )
        )
        return ()
    try:
        disk_entries = sorted(
            _bounded_directory_entries(gates_path, MAX_GATE_HISTORY_ENTRIES + 1),
            key=lambda entry: (entry.name.casefold(), entry.name),
        )
    except (_EvidenceLimitExceeded, OSError):
        issues.append(
            _issue(
                "gate_history_unreadable",
                "error",
                GATE_SNAPSHOTS_DIRNAME,
                "gate snapshot directory cannot be safely enumerated",
            )
        )
        return ()
    if (
        len(disk_entries) != len(entries)
        or {entry.name for entry in disk_entries} != set(entries)
    ):
        issues.append(
            _issue(
                "gate_history_set_mismatch",
                "error",
                GATE_SNAPSHOTS_DIRNAME,
                "gate history index and immutable snapshot set differ",
            )
        )

    verified: list[VerifiedGateSnapshot] = []
    for gate_id, index_entry in sorted(entries.items()):
        try:
            verification_context.consume_gate_snapshot()
        except _VerificationBudgetExceeded:
            issues.append(_verification_budget_issue(campaign_id, verification_context))
            break
        if (
            not isinstance(index_entry, dict)
            or set(index_entry)
            != {
                "release_gate_sha256",
                "baseline_candidate_diff_sha256",
            }
            or not all(
                isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)
                for value in index_entry.values()
            )
        ):
            issues.append(
                _issue(
                    "invalid_gate_history",
                    "error",
                    "gate_history.json",
                    "gate history entry has invalid hash metadata",
                )
            )
            continue
        gate_path = gates_path / gate_id
        if (
            not gate_path.exists()
            or is_symlink_or_reparse(gate_path)
            or not gate_path.is_dir()
        ):
            issues.append(
                _issue(
                    "gate_snapshot_missing",
                    "error",
                    f"gates/{gate_id}",
                    "gate snapshot directory is missing or unsafe",
                )
            )
            continue
        try:
            snapshot_entries = {
                entry.name: entry
                for entry in _bounded_directory_entries(
                    gate_path, len(GATE_SNAPSHOT_FILENAMES) + 1
                )
            }
        except (_EvidenceLimitExceeded, OSError):
            issues.append(
                _issue(
                    "gate_snapshot_unreadable",
                    "error",
                    f"gates/{gate_id}",
                    "gate snapshot cannot be safely enumerated",
                )
            )
            continue
        if set(snapshot_entries) != set(GATE_SNAPSHOT_FILENAMES):
            issues.append(
                _issue(
                    "gate_snapshot_set_mismatch",
                    "error",
                    f"gates/{gate_id}",
                    "gate snapshot file set is incomplete or contains extras",
                )
            )
            continue
        contents: dict[str, bytes] = {}
        snapshot_valid = True
        for filename in sorted(GATE_SNAPSHOT_FILENAMES):
            path = snapshot_entries[filename]
            if is_symlink_or_reparse(path) or not path.is_file():
                issues.append(
                    _issue(
                        "symlink_not_allowed",
                        "error",
                        f"gates/{gate_id}/{filename}",
                        "gate snapshot file must be a regular file",
                    )
                )
                snapshot_valid = False
                continue
            try:
                content = _read_bounded(path, MAX_CAMPAIGN_ARTIFACT_BYTES)
            except (_EvidenceLimitExceeded, OSError):
                issues.append(
                    _issue(
                        "gate_snapshot_unreadable",
                        "error",
                        f"gates/{gate_id}/{filename}",
                        "gate snapshot file cannot be safely read",
                    )
                )
                snapshot_valid = False
                continue
            expected_hash = index_entry[
                "release_gate_sha256"
                if filename == "release_gate.json"
                else "baseline_candidate_diff_sha256"
            ]
            if sha256_bytes(content) != expected_hash:
                issues.append(
                    _issue(
                        "gate_snapshot_hash_mismatch",
                        "error",
                        f"gates/{gate_id}/{filename}",
                        "gate snapshot hash does not match the history index",
                        expected_sha256=expected_hash,
                        actual_sha256=sha256_bytes(content),
                    )
                )
                snapshot_valid = False
                continue
            if _campaign_json_payload(
                f"gates/{gate_id}/{filename}", content, issues
            ) is None:
                snapshot_valid = False
                continue
            contents[filename] = content
        if not snapshot_valid or set(contents) != set(GATE_SNAPSHOT_FILENAMES):
            continue
        try:
            gate_payload = json.loads(contents["release_gate.json"].decode("utf-8"))
            diff_payload = json.loads(
                contents["baseline_candidate_diff.json"].decode("utf-8")
            )
        except (UnicodeError, json.JSONDecodeError, ValueError, RecursionError):
            continue
        if isinstance(gate_payload, dict) and "id" in gate_payload:
            try:
                gate = ReleaseGateDecision.model_validate_json(
                    contents["release_gate.json"]
                )
            except ValidationError:
                issues.append(
                    _issue(
                        "invalid_gate_snapshot",
                        "error",
                        f"gates/{gate_id}/release_gate.json",
                        "governed gate snapshot violates its domain contract",
                    )
                )
                continue
            baseline_id = gate.baseline_campaign_id
            expected_gate_id = stable_id(
                "ocgate",
                campaign_id,
                baseline_id if baseline_id is not None else "standalone",
                gate.policy_version,
            )
            diff_baseline = (
                diff_payload.get("baseline_campaign_id")
                if isinstance(diff_payload, dict)
                else None
            )
            if (
                gate.id != gate_id
                or gate.id != expected_gate_id
                or gate.campaign_id != campaign_id
                or diff_baseline != baseline_id
            ):
                issues.append(
                    _issue(
                        "gate_snapshot_mapping_mismatch",
                        "error",
                        f"gates/{gate_id}",
                        "gate snapshot IDs do not match their immutable location",
                    )
                )
                continue
        verified.append(
            VerifiedGateSnapshot(
                gate_id=gate_id,
                release_gate=contents["release_gate.json"],
                baseline_candidate_diff=contents["baseline_candidate_diff.json"],
            )
        )

    current = next(
        (snapshot for snapshot in verified if snapshot.gate_id == current_gate_id),
        None,
    )
    if (
        current is None
        or campaign_artifacts.get("release_gate.json") != current.release_gate
        or campaign_artifacts.get("baseline_candidate_diff.json")
        != current.baseline_candidate_diff
    ):
        issues.append(
            _issue(
                "gate_history_current_mismatch",
                "error",
                "gate_history.json",
                "current gate aliases do not match the indexed immutable snapshot",
            )
        )
    return tuple(verified)


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
        or payload.get("schema_version") != SUPPORTED_CAMPAIGN_SCHEMA_VERSION
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


_STRUCTURED_SUMMARY_FIELDS = frozenset(
    {
        "schema_version",
        "campaign",
        "campaign_created_at",
        "workspace_id",
        "agent",
        "agent_version",
        "agent_config_sha256",
        "scenario_suite",
        "scenario_ids",
        "run_ids",
        "metrics",
        "gate_input",
        "manifest_ids",
        "plan_evidence",
        "mis_task_id",
        "mis_plan_id",
        "git_commit_sha",
        "failure_count",
        "regression_count",
    }
)


def _verify_campaign_summary_facts(
    campaign_id: str,
    envelope: object | None,
    campaign_payloads: Mapping[str, object | None],
    runs: Sequence[RunVerification],
    issues: list[VerificationIssue],
    *,
    gate_snapshots: tuple[VerifiedGateSnapshot, ...],
    campaign_root: Path,
    strict: bool,
    campaign_stack: frozenset[str],
    verification_context: VerificationContext,
) -> None:
    """Cross-check governed summary facts against independently hashed run evidence."""

    if not isinstance(envelope, dict) or not isinstance(envelope.get("summary"), dict):
        return
    summary = envelope["summary"]
    present = _STRUCTURED_SUMMARY_FIELDS.intersection(summary)
    if not present:
        # The low-level evidence writer also supports small caller-defined summaries.
        # Those remain hash envelopes, not governed campaign facts.
        if _runs_have_governed_mappings(runs):
            issues.append(
                _issue(
                    "governed_campaign_summary_required",
                    "error",
                    CAMPAIGN_SUMMARY_FILENAME,
                    "governed run mappings require a structured campaign summary",
                )
            )
        return
    if not _STRUCTURED_SUMMARY_FIELDS.issubset(summary):
        _campaign_facts_issue(issues, "structured campaign summary is incomplete")
        return
    allowed_fields = _STRUCTURED_SUMMARY_FIELDS | {"comparison"}
    if not set(summary).issubset(allowed_fields):
        _campaign_facts_issue(issues, "structured campaign summary has unknown fields")
        return

    try:
        campaign = Campaign.model_validate_json(canonical_json_bytes(summary["campaign"]))
        agent = AgentUnderTest.model_validate_json(canonical_json_bytes(summary["agent"]))
        agent_version = AgentVersion.model_validate_json(
            canonical_json_bytes(summary["agent_version"])
        )
        scenario_suite = ScenarioSuite.model_validate_json(
            canonical_json_bytes(summary["scenario_suite"])
        )
        gate_input = CampaignGateInput.model_validate_json(
            canonical_json_bytes(summary["gate_input"])
        )
    except (ValidationError, TypeError, ValueError, RecursionError):
        _campaign_facts_issue(issues, "structured campaign summary is invalid")
        return

    verified_runs = [run for run in runs if run.ok and run.manifest is not None]
    if len(verified_runs) != len(runs) or not verified_runs:
        return

    groups: list[EvaluationResultGroup] = []
    run_facts: list[RunMetricFacts] = []
    scenario_by_run: dict[str, str] = {}
    scenario_digest_by_id: dict[str, str] = {}
    scenarios_by_id: dict[str, ScenarioDefinition] = {}
    final_state_by_run: dict[str, dict[str, object]] = {}
    manifest_ids: list[str] = []
    git_commits: set[str] = set()
    agent_versions: list[AgentVersion] = []
    failure_count = 0
    evaluations_by_id: dict[str, EvaluationResult] = {}
    plan_evidence: list[dict[str, object | None]] = []

    for run in verified_runs:
        loaded = _load_campaign_run_facts(run, issues)
        if loaded is None:
            return
        scenario, loaded_agent_version, turns, tool_calls, timing, evaluations = loaded
        deterministic = [
            result.model_copy(update={"mis_evaluation_id": None})
            for result in evaluations
            if not _is_optional_judge(result.evaluator_id)
        ]
        judges = [
            result.model_copy(update={"mis_evaluation_id": None})
            for result in evaluations
            if _is_optional_judge(result.evaluator_id)
        ]
        try:
            groups.append(
                EvaluationResultGroup(
                    run_id=run.run_id,
                    deterministic_results=deterministic,
                    judge_results=judges,
                )
            )
            duration_ms = timing.get("duration_ms")
            if type(duration_ms) is not int or duration_ms < 0:
                raise ValueError("duration_ms must be a non-negative integer")
            run_facts.append(
                RunMetricFacts(
                    run_id=run.run_id,
                    turn_count=len(turns),
                    latency_ms=duration_ms,
                )
            )
        except (ValidationError, TypeError, ValueError):
            _campaign_facts_issue(issues, "run evidence cannot produce campaign metrics")
            return
        scenario_by_run[run.run_id] = scenario.id
        observed_final_state = timing.get("observed_final_state")
        if not isinstance(observed_final_state, dict):
            _campaign_facts_issue(issues, "run final state cannot be reconstructed")
            return
        scenarios_by_id[scenario.id] = scenario
        final_state_by_run[run.run_id] = observed_final_state
        manifest = run.manifest
        assert manifest is not None
        expected_task_id = stable_id(
            "tskoc", str(summary["workspace_id"]), campaign_id
        )
        expected_plan_id = stable_id("planoc", expected_task_id, campaign_id)
        expected_mis_run_id = stable_id("runoc", expected_task_id, run.run_id)
        mapping_matches = all(
            call.mis_tool_call_id
            == stable_id("tcloc", expected_mis_run_id, call.id)
            for call in tool_calls
        ) and all(
            result.mis_evaluation_id
            == (
                None
                if result.status is EvaluationStatus.SKIPPED
                else stable_id("evaloc", expected_mis_run_id, result.id)
            )
            for result in evaluations
        )
        mapping_matches = mapping_matches and manifest.mis_artifact_id == stable_id(
            "artoc", expected_mis_run_id, "evidence-manifest.v2"
        )
        if manifest.mis_plan_evidence_manifest_id is not None:
            mapping_matches = (
                mapping_matches
                and manifest.mis_plan_evidence_manifest_id
                == stable_id("pemoc", expected_plan_id, expected_mis_run_id)
            )
        if not mapping_matches:
            _campaign_facts_issue(issues, "run MIS mappings are not deterministic")
            return
        manifest_ids.append(manifest.id)
        scenario_digest_by_id[scenario.id] = manifest.scenario_sha256
        git_commits.add(manifest.git_commit_sha)
        agent_versions.append(loaded_agent_version)
        failure_count += sum(
            max(1, len(result.reason_codes))
            for result in evaluations
            if result.status in {EvaluationStatus.FAIL, EvaluationStatus.ERROR}
        )
        evaluations_by_id.update({result.id: result for result in evaluations})
        plan_evidence.append(
            {
                "run_id": run.run_id,
                "status": (
                    "verified"
                    if manifest.mis_plan_evidence_manifest_id is not None
                    else "unavailable"
                ),
                "reason": (
                    None
                    if manifest.mis_plan_evidence_manifest_id is not None
                    else "run_has_no_completed_tool_call"
                ),
                "mis_plan_evidence_manifest_id": (
                    manifest.mis_plan_evidence_manifest_id
                ),
            }
        )

    try:
        metrics = aggregate_campaign_metrics(groups, run_facts)
    except (ValidationError, TypeError, ValueError):
        _campaign_facts_issue(issues, "run evidence cannot produce campaign metrics")
        return

    regressions_payload = campaign_payloads.get("regression_cases.json")
    if not isinstance(regressions_payload, list):
        return
    try:
        regressions = TypeAdapter(list[RegressionCase]).validate_json(
            canonical_json_bytes(regressions_payload)
        )
    except (ValidationError, TypeError, ValueError, RecursionError):
        _campaign_facts_issue(issues, "regression evidence does not satisfy its contract")
        return

    release_gate_payload = campaign_payloads.get("release_gate.json")
    try:
        release_gate = ReleaseGateDecision.model_validate_json(
            canonical_json_bytes(release_gate_payload)
        )
    except (ValidationError, TypeError, ValueError, RecursionError):
        _campaign_facts_issue(issues, "release gate evidence does not satisfy its contract")
        return

    expected_evaluator_versions = sorted(
        {
            result.evaluator_id
            for group in groups
            for result in group.deterministic_results
        }
    )
    expected_run_ids = set(scenario_by_run)
    expected_scenario_ids = set(scenario_by_run.values())
    expected_plan_evidence = {
        canonical_json_bytes(row) for row in plan_evidence
    }
    actual_plan_evidence = summary.get("plan_evidence")
    actual_plan_rows = (
        {canonical_json_bytes(row) for row in actual_plan_evidence}
        if isinstance(actual_plan_evidence, list)
        else set()
    )
    expected_task_id = stable_id("tskoc", str(summary["workspace_id"]), campaign_id)
    expected_plan_id = stable_id("planoc", expected_task_id, campaign_id)
    diff_payload = campaign_payloads.get("baseline_candidate_diff.json")
    comparison = summary.get("comparison")
    ordered_scenarios = _string_list(summary.get("scenario_ids"))
    expected_suite_id = stable_id(
        "ocsuite",
        "|".join(
            f"{scenario_id}:{scenario_digest_by_id.get(scenario_id, '')}"
            for scenario_id in ordered_scenarios
        ),
    )
    expected_agent_id = stable_id(
        "ocagent", str(summary["workspace_id"]), "appointment-agent"
    )
    expected_agent_version_id = stable_id(
        "ocagentv",
        expected_agent_id,
        agent_version.version.strip(),
        agent_version.config_sha256,
    )
    baseline_campaign_id = (
        diff_payload.get("baseline_campaign_id")
        if isinstance(diff_payload, dict)
        else None
    )
    expected_approval_id = stable_id(
        "apoc",
        campaign_id,
        baseline_campaign_id if isinstance(baseline_campaign_id, str) else "standalone",
        release_gate.policy_version,
    )
    try:
        baseline_gate_input = _baseline_gate_input(
            diff_payload,
            scenario_ids=expected_scenario_ids,
            evaluator_versions=expected_evaluator_versions,
            campaign_root=campaign_root,
            strict=strict,
            campaign_stack=campaign_stack,
            verification_context=verification_context,
        )
        expected_release_gate = evaluate_release_gate_for_policy(
            release_gate.policy_version,
            gate_input,
            baseline=baseline_gate_input,
            created_at=campaign.created_at,
            mis_approval_id=expected_approval_id,
        )
        expected_diff = _expected_comparison_payload(gate_input, baseline_gate_input)
        gate_history_matches = _governed_gate_history_matches(
            gate_snapshots,
            candidate=gate_input,
            created_at=campaign.created_at,
            current_gate=expected_release_gate,
            current_diff=expected_diff,
            scenario_ids=expected_scenario_ids,
            evaluator_versions=expected_evaluator_versions,
            campaign_root=campaign_root,
            strict=strict,
            campaign_stack=campaign_stack,
            verification_context=verification_context,
        )
    except _CampaignBaselineError:
        issues.append(
            _issue(
                "campaign_baseline_unverified",
                "error",
                "baseline_candidate_diff.json",
                "comparison baseline campaign is unavailable or unverified",
            )
        )
        return
    except (ValidationError, TypeError, ValueError, RecursionError):
        _campaign_facts_issue(issues, "release gate facts cannot be reconstructed")
        return
    comparison_matches = (
        comparison == diff_payload == expected_diff
        if baseline_gate_input is not None
        else comparison is None and diff_payload == expected_diff
    )
    regression_mappings_match = all(
        _regression_matches_verified_facts(
            regression,
            expected_task_id=expected_task_id,
            scenario_by_run=scenario_by_run,
            evaluations_by_id=evaluations_by_id,
            scenarios_by_id=scenarios_by_id,
            final_state_by_run=final_state_by_run,
        )
        for regression in regressions
    )
    facts_match = all(
        (
            summary.get("schema_version") == SUPPORTED_CAMPAIGN_SCHEMA_VERSION,
            campaign.id == campaign_id,
            campaign.status.value == "completed",
            campaign.created_at == agent_version.created_at,
            agent.created_at == agent_version.created_at,
            scenario_suite.created_at == agent_version.created_at,
            summary.get("campaign_created_at") == _utc_json_time(campaign.created_at),
            gate_input.campaign_id == campaign_id,
            campaign.agent_version_id == agent_version.id,
            campaign.scenario_suite_id == scenario_suite.id,
            campaign.mis_task_id is None,
            campaign.mis_plan_id is None,
            summary.get("mis_task_id") == expected_task_id,
            summary.get("mis_plan_id") == expected_plan_id,
            agent_version.agent_id == agent.id,
            agent.id == expected_agent_id,
            agent.name == "AI Appointment Agent",
            agent.description
            == "Appointment agent exercised by the public deterministic demo.",
            agent_version.id == expected_agent_version_id,
            scenario_suite.id == expected_suite_id,
            scenario_suite.name == "AI Appointment Agent Reliability Test",
            scenario_suite.description
            == "Deterministic appointment-agent reliability scenarios.",
            agent.workspace_id == summary.get("workspace_id"),
            all(version == agent_version for version in agent_versions),
            summary.get("agent_config_sha256") == agent_version.config_sha256,
            set(_string_list(summary.get("run_ids"))) == expected_run_ids,
            len(_string_list(summary.get("run_ids"))) == len(expected_run_ids),
            set(_string_list(summary.get("manifest_ids"))) == set(manifest_ids),
            len(_string_list(summary.get("manifest_ids"))) == len(manifest_ids),
            set(_string_list(summary.get("scenario_ids"))) == expected_scenario_ids,
            len(_string_list(summary.get("scenario_ids")))
            == len(expected_scenario_ids),
            git_commits == {summary.get("git_commit_sha")},
            summary.get("metrics") == metrics.model_dump(mode="json"),
            gate_input.metrics == metrics,
            gate_input.scenario_by_run == scenario_by_run,
            set(gate_input.scenario_ids) == expected_scenario_ids,
            gate_input.scenario_sha256_by_id == scenario_digest_by_id,
            gate_input.evaluator_versions == expected_evaluator_versions,
            gate_input.evidence_verified is True,
            summary.get("failure_count") == failure_count,
            summary.get("regression_count") == len(regressions),
            len(regressions) == failure_count,
            expected_plan_evidence == actual_plan_rows,
            comparison_matches,
            gate_history_matches,
            regression_mappings_match,
            release_gate == expected_release_gate,
            release_gate.campaign_id == campaign_id,
            release_gate.baseline_campaign_id
            == (
                baseline_campaign_id
                if isinstance(baseline_campaign_id, str)
                else None
            ),
            release_gate.mis_approval_id == expected_approval_id,
            release_gate.evidence_refs == gate_input.evidence_refs,
            release_gate.created_at == campaign.created_at,
            release_gate.metrics.get("task_success_rate")
            == metrics.task_success_rate,
            release_gate.metrics.get("deterministic_error_rate")
            == metrics.deterministic_error_rate,
            release_gate.metrics.get("forbidden_call_violations")
            == metrics.forbidden_call_violation_count,
            release_gate.metrics.get("duplicate_mutation_violations")
            == metrics.duplicate_mutation_violation_count,
            release_gate.metrics.get("confirmation_violations")
            == metrics.confirmation_violation_count,
            release_gate.metrics.get("timeout_rate") == metrics.timeout_rate,
            release_gate.metrics.get("median_turns") == metrics.median_turns,
        )
    )
    if not facts_match:
        _campaign_facts_issue(
            issues,
            "campaign summary facts do not match verified run evidence",
        )


def _load_campaign_run_facts(
    run: RunVerification,
    issues: list[VerificationIssue],
) -> tuple[
    ScenarioDefinition,
    AgentVersion,
    list[ConversationTurn],
    list[ObservedToolCall],
    dict[str, object],
    list[EvaluationResult],
] | None:
    manifest = run.manifest
    if manifest is None:
        return None
    contents: dict[str, bytes] = {}
    for artifact_name in (
        "scenario.yaml",
        "agent_version.json",
        "transcript.json",
        "tool_calls.json",
        "timing.json",
        "evaluations.json",
    ):
        path = run.manifest_path.parent / artifact_name
        try:
            content = _read_bounded(path, MAX_RUN_ARTIFACT_BYTES)
        except (_EvidenceLimitExceeded, OSError):
            _campaign_facts_issue(issues, "run evidence changed during verification")
            return None
        if sha256_bytes(content) != manifest.artifacts.get(artifact_name):
            _campaign_facts_issue(issues, "run evidence changed during verification")
            return None
        contents[artifact_name] = content
    try:
        scenario = ScenarioDefinition.model_validate(
            yaml.safe_load(contents["scenario.yaml"].decode("utf-8"))
        )
        agent_envelope = json.loads(
            contents["agent_version.json"].decode("utf-8"),
            parse_constant=_reject_json_constant,
        )
        if not isinstance(agent_envelope, dict):
            raise ValueError("agent envelope must be an object")
        loaded_agent_version = AgentVersion.model_validate_json(
            canonical_json_bytes(agent_envelope.get("agent_version"))
        )
        turns = TypeAdapter(list[ConversationTurn]).validate_json(
            contents["transcript.json"]
        )
        tool_calls = TypeAdapter(list[ObservedToolCall]).validate_json(
            contents["tool_calls.json"]
        )
        timing = TypeAdapter(dict[str, object]).validate_json(contents["timing.json"])
        evaluations = TypeAdapter(list[EvaluationResult]).validate_json(
            contents["evaluations.json"]
        )
    except (
        UnicodeError,
        json.JSONDecodeError,
        yaml.YAMLError,
        ValidationError,
        TypeError,
        ValueError,
        RecursionError,
    ):
        _campaign_facts_issue(issues, "run evidence cannot be reconstructed")
        return None
    return scenario, loaded_agent_version, turns, tool_calls, timing, evaluations


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return []
    return value


def _regression_matches_verified_facts(
    regression: RegressionCase,
    *,
    expected_task_id: str,
    scenario_by_run: Mapping[str, str],
    evaluations_by_id: Mapping[str, EvaluationResult],
    scenarios_by_id: Mapping[str, ScenarioDefinition],
    final_state_by_run: Mapping[str, dict[str, object]],
) -> bool:
    evaluation = evaluations_by_id.get(
        regression.evidence_refs[-1].split(":", 1)[1]
        if regression.evidence_refs
        and regression.evidence_refs[-1].startswith("evaluation:")
        else ""
    )
    if evaluation is None:
        matching = [
            result
            for result in evaluations_by_id.values()
            if f"evaluation:{result.id}" in regression.evidence_refs
        ]
        evaluation = matching[0] if len(matching) == 1 else None
    if evaluation is None or evaluation.run_id != regression.source_run_id:
        return False
    if scenario_by_run.get(regression.source_run_id) != regression.scenario_id:
        return False
    reason_codes = evaluation.reason_codes or ["evaluation_failed"]
    if regression.reason_code not in reason_codes:
        return False
    expected_failure_id = stable_id(
        "ocfailure",
        regression.source_run_id,
        evaluation.id,
        regression.reason_code,
    )
    expected_regression_id = stable_id(
        "ocregression", expected_failure_id, evaluation.evaluator_id
    )
    scenario = scenarios_by_id.get(regression.scenario_id)
    observed_final_state = final_state_by_run.get(regression.source_run_id)
    if scenario is None or observed_final_state is None:
        return False
    scenario_json = scenario.model_dump(mode="json")
    expected_evidence_refs = list(
        dict.fromkeys(
            [*evaluation.evidence_refs, f"evaluation:{evaluation.id}"]
        )
    )
    try:
        expected_regression = RegressionCase(
            schema_version=1,
            id=expected_regression_id,
            failure_case_id=expected_failure_id,
            scenario_id=scenario.id,
            source_run_id=evaluation.run_id,
            name=f"Regression: {scenario.name} [{regression.reason_code}]",
            original_input={
                "initial_message": scenario.initial_message,
                "persona": scenario_json["persona"],
                "goal": scenario_json["goal"],
                "challenges": scenario_json["challenges"],
                "expectations": scenario_json["expectations"],
            },
            expected={
                "evaluation_status": "pass",
                "final_state": scenario.expectations.final_state,
            },
            observed={
                "evaluation_status": evaluation.status.value,
                "final_state": observed_final_state,
                "metadata": evaluation.metadata,
            },
            reason_code=regression.reason_code,
            evaluator_id=evaluation.evaluator_id,
            evidence_refs=expected_evidence_refs,
            mis_memory_id=stable_id(
                "memoc", expected_task_id, expected_regression_id
            ),
            created_at=evaluation.created_at,
        )
    except ValidationError:
        return False
    return regression == expected_regression


def _governed_gate_history_matches(
    snapshots: tuple[VerifiedGateSnapshot, ...],
    *,
    candidate: CampaignGateInput,
    created_at: datetime,
    current_gate: ReleaseGateDecision,
    current_diff: Mapping[str, object],
    scenario_ids: set[str],
    evaluator_versions: list[str],
    campaign_root: Path,
    strict: bool,
    campaign_stack: frozenset[str],
    verification_context: VerificationContext,
) -> bool:
    if not snapshots:
        return False
    for snapshot in snapshots:
        try:
            gate = ReleaseGateDecision.model_validate_json(snapshot.release_gate)
            diff = json.loads(
                snapshot.baseline_candidate_diff.decode("utf-8"),
                parse_constant=_reject_json_constant,
            )
            if gate == current_gate and diff == current_diff:
                continue
            baseline = _baseline_gate_input(
                diff,
                scenario_ids=scenario_ids,
                evaluator_versions=evaluator_versions,
                campaign_root=campaign_root,
                strict=strict,
                campaign_stack=campaign_stack,
                verification_context=verification_context,
            )
            approval_id = stable_id(
                "apoc",
                candidate.campaign_id,
                baseline.campaign_id if baseline is not None else "standalone",
                gate.policy_version,
            )
            expected_gate = evaluate_release_gate_for_policy(
                gate.policy_version,
                candidate,
                baseline=baseline,
                created_at=created_at,
                mis_approval_id=approval_id,
            )
            expected_diff = _expected_comparison_payload(candidate, baseline)
        except (
            UnicodeError,
            json.JSONDecodeError,
            ValidationError,
            TypeError,
            ValueError,
            RecursionError,
        ):
            return False
        if (
            gate.id != snapshot.gate_id
            or gate != expected_gate
            or diff != expected_diff
        ):
            return False
    return True


def _baseline_gate_input(
    diff_payload: object,
    *,
    scenario_ids: set[str],
    evaluator_versions: list[str],
    campaign_root: Path,
    strict: bool,
    campaign_stack: frozenset[str],
    verification_context: VerificationContext,
) -> CampaignGateInput | None:
    if not isinstance(diff_payload, dict):
        raise ValueError("comparison evidence must be an object")
    baseline_campaign_id = diff_payload.get("baseline_campaign_id")
    if baseline_campaign_id is None:
        if diff_payload.get("comparison") != "no_comparison":
            raise ValueError("standalone comparison evidence is invalid")
        return None
    if not isinstance(baseline_campaign_id, str):
        raise ValueError("baseline campaign ID is invalid")
    if baseline_campaign_id in campaign_stack:
        raise _CampaignBaselineError
    if len(campaign_stack) >= MAX_COMPARISON_DEPTH:
        raise _CampaignBaselineError
    try:
        baseline_report = verify_campaign(
            campaign_root,
            baseline_campaign_id,
            strict=strict,
            _campaign_stack=campaign_stack,
            _verification_context=verification_context,
        )
    except EvidenceError:
        raise _CampaignBaselineError from None
    if not baseline_report.ok:
        raise _CampaignBaselineError
    try:
        baseline_envelope = verified_campaign_json(
            baseline_report, CAMPAIGN_SUMMARY_FILENAME
        )
        if not isinstance(baseline_envelope, dict) or not isinstance(
            baseline_envelope.get("summary"), dict
        ):
            raise _CampaignBaselineError
        baseline_summary = baseline_envelope["summary"]
        baseline_gate = CampaignGateInput.model_validate_json(
            canonical_json_bytes(baseline_summary.get("gate_input"))
        )
    except (EvidenceInputError, ValidationError, TypeError, ValueError, RecursionError):
        raise _CampaignBaselineError from None
    if (
        baseline_gate.campaign_id != baseline_campaign_id
        or set(baseline_gate.scenario_ids) != scenario_ids
        or baseline_gate.evaluator_versions != evaluator_versions
        or diff_payload.get("baseline_metrics")
        != baseline_gate.metrics.model_dump(mode="json")
    ):
        raise _CampaignBaselineError
    return baseline_gate


def _expected_comparison_payload(
    candidate: CampaignGateInput,
    baseline: CampaignGateInput | None,
) -> dict[str, object]:
    if baseline is None:
        return {
            "schema_version": 1,
            "campaign_id": candidate.campaign_id,
            "baseline": None,
            "comparison": "no_comparison",
        }
    baseline_metrics = baseline.metrics
    candidate_metrics = candidate.metrics
    return {
        "schema_version": 1,
        "baseline_campaign_id": baseline.campaign_id,
        "candidate_campaign_id": candidate.campaign_id,
        "baseline_metrics": baseline_metrics.model_dump(mode="json"),
        "candidate_metrics": candidate_metrics.model_dump(mode="json"),
        "delta": {
            "task_success_percentage_points": _metric_rate_delta(
                baseline_metrics.task_success_rate,
                candidate_metrics.task_success_rate,
                scale=100.0,
            ),
            "median_turns_ratio": _metric_ratio_delta(
                baseline_metrics.median_turns,
                candidate_metrics.median_turns,
            ),
            "timeout_rate": _metric_rate_delta(
                baseline_metrics.timeout_rate,
                candidate_metrics.timeout_rate,
            ),
        },
    }


def _metric_rate_delta(
    baseline: float | None,
    candidate: float | None,
    *,
    scale: float = 1.0,
) -> float | None:
    if baseline is None or candidate is None:
        return None
    return (candidate - baseline) * scale


def _metric_ratio_delta(
    baseline: float | None,
    candidate: float | None,
) -> float | None:
    if baseline is None or candidate is None or baseline <= 0:
        return None
    return (candidate - baseline) / baseline


class _CampaignBaselineError(ValueError):
    """A comparison baseline cannot establish verified campaign facts."""


def _runs_have_governed_mappings(runs: Sequence[RunVerification]) -> bool:
    return any(
        run.manifest is not None
        and isinstance(run.manifest.mis_artifact_id, str)
        and run.manifest.mis_artifact_id.startswith("artoc_")
        for run in runs
    )


def _campaign_facts_issue(
    issues: list[VerificationIssue],
    message: str,
) -> None:
    issues.append(
        _issue(
            "campaign_summary_facts_mismatch",
            "error",
            CAMPAIGN_SUMMARY_FILENAME,
            message,
        )
    )


def _verify_run(campaign_id: str, run_path: Path, *, strict: bool) -> RunVerification:
    run_id = run_path.name
    manifest_path = run_path / MANIFEST_FILENAME
    issues: list[VerificationIssue] = []
    entries: dict[str, Path] = {}
    try:
        listed_entries = sorted(
            _bounded_directory_entries(run_path, MAX_RUN_ENTRIES),
            key=lambda entry: (entry.name.casefold(), entry.name),
        )
    except _EvidenceLimitExceeded:
        listed_entries = []
        issues.append(
            _run_issue(
                run_id,
                "evidence_limit_exceeded",
                "error",
                "",
                "run directory exceeds the verifier entry limit",
            )
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
        manifest_bytes = _read_bounded(manifest_entry, MAX_MANIFEST_BYTES)
        if _json_nesting_exceeds_limit(manifest_bytes):
            raise _EvidenceLimitExceeded
        raw_manifest = json.loads(
            manifest_bytes.decode("utf-8"),
            parse_constant=_reject_json_constant,
        )
    except _EvidenceLimitExceeded:
        issues.append(
            _run_issue(
                run_id,
                "evidence_limit_exceeded",
                "error",
                MANIFEST_FILENAME,
                "evidence manifest exceeds a verifier resource limit",
            )
        )
        return RunVerification(
            campaign_id=campaign_id,
            run_id=run_id,
            manifest_path=manifest_path,
            manifest=None,
            issues=tuple(issues),
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
            artifact_bytes = _read_bounded(artifact_path, MAX_RUN_ARTIFACT_BYTES)
            if artifact_name.endswith(".json") and _json_nesting_exceeds_limit(
                artifact_bytes
            ):
                raise _EvidenceLimitExceeded
        except _EvidenceLimitExceeded:
            issues.append(
                _run_issue(
                    run_id,
                    "evidence_limit_exceeded",
                    "error",
                    artifact_name,
                    "covered artifact exceeds a verifier resource limit",
                )
            )
            continue
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
        manifest,
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
    manifest: EvidenceManifest,
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
    if config_digest != manifest.agent_config_sha256:
        issues.append(
            _run_issue(
                run_id,
                "manifest_agent_config_digest_mismatch",
                "error",
                MANIFEST_FILENAME,
                "manifest agent config SHA-256 does not match effective config",
                expected_sha256=manifest.agent_config_sha256,
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


def _verification_budget_issue(
    campaign_id: str,
    context: VerificationContext,
) -> VerificationIssue:
    limit = context.exhausted_limit or "verification"
    return _issue(
        "verification_budget_exceeded",
        "error",
        campaign_id,
        f"recursive campaign verification exceeded its global {limit} limit",
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
    "GATE_SNAPSHOT_FILENAMES",
    "GATE_SNAPSHOTS_DIRNAME",
    "MANIFEST_FILENAME",
    "RUN_ARTIFACT_FILENAMES",
    "RUN_FILENAMES",
    "RunVerification",
    "VerificationIssue",
    "VerificationContext",
    "VerificationReport",
    "VerifiedGateSnapshot",
    "VerifiedCampaignArtifact",
    "canonical_json_bytes",
    "contains_sensitive_fields",
    "deterministic_final_state",
    "sha256_bytes",
    "is_symlink_or_reparse",
    "validate_path_component",
    "verify_campaign",
    "verified_campaign_json",
    "verify_run_bundles",
]
