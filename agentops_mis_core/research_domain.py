"""Native, persistence-agnostic Research Lab domain contracts.

The objects in this module add research semantics while retaining MIS as the
authority for Tasks, Runs, Agent Plans, Artifacts, Evaluations, and audit.  The
module is deliberately stdlib-only and has no server or executor side effects.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
import math
import re
from typing import Any, Mapping


_PUBLIC_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ResearchDomainError(ValueError):
    """Base class for fail-closed domain contract failures."""


class InvalidTransition(ResearchDomainError):
    """Raised when a state-machine edge is not explicitly permitted."""


class StaleStateVersion(ResearchDomainError):
    """Raised when a compare-and-swap state version is stale."""


def canonical_json(value: Any) -> str:
    """Return deterministic JSON and reject non-finite/non-JSON values."""
    def validate(item: Any, path: str = "$") -> None:
        if item is None or isinstance(item, (str, bool, int)):
            return
        if isinstance(item, float):
            if not math.isfinite(item):
                raise ResearchDomainError(f"{path} must be finite")
            return
        if isinstance(item, list):
            for index, child in enumerate(item):
                validate(child, f"{path}[{index}]")
            return
        if isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str):
                    raise ResearchDomainError(f"{path} mapping keys must be strings")
                validate(child, f"{path}.{key}")
            return
        raise ResearchDomainError(f"{path} contains unsupported JSON type")
    validate(value)
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ResearchDomainError("value must be finite JSON data") from exc


def canonical_hash(value: Any) -> str:
    """SHA-256 of UTF-8 canonical JSON (strings are JSON strings, not raw)."""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _id(value: str, field: str) -> str:
    if not isinstance(value, str) or not _PUBLIC_ID.fullmatch(value):
        raise ResearchDomainError(f"{field} must be an opaque public identifier")
    return value


def _hash(value: str, field: str) -> str:
    normalized = str(value).lower()
    if not _SHA256.fullmatch(normalized):
        raise ResearchDomainError(f"{field} must be a SHA-256 hex digest")
    return normalized


def _positive_version(value: int, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ResearchDomainError(f"{field} must be a positive integer")
    return value


CONTRACT_TRANSITIONS: Mapping[str, frozenset[str]] = {
    "draft": frozenset({"proposed", "cancelled"}),
    "proposed": frozenset({"review_pending", "rejected", "cancelled"}),
    "review_pending": frozenset({"approved", "rejected", "cancelled"}),
    "approved": frozenset({"active", "superseded", "cancelled"}),
    "active": frozenset({"superseded", "cancelled"}),
    "superseded": frozenset(),
    "cancelled": frozenset(),
    # Rejection is terminal for that immutable version. A revision is new row.
    "rejected": frozenset(),
}

TRIAL_TRANSITIONS: Mapping[str, frozenset[str]] = {
    "proposed": frozenset({"planned", "rejected", "cancelled"}),
    "planned": frozenset({"approval_pending", "queued", "cancelled"}),
    "approval_pending": frozenset({"queued", "rejected", "cancelled"}),
    "queued": frozenset({"running", "cancelled"}),
    "running": frozenset({"paused", "recovering", "completed", "failed", "cancelled"}),
    "paused": frozenset({"queued", "recovering", "cancelled"}),
    "recovering": frozenset({"running", "failed", "cancelled"}),
    "completed": frozenset({"evaluated"}),
    "failed": frozenset({"recovering", "evaluated"}),
    "cancelled": frozenset({"evaluated"}),
    "evaluated": frozenset({"promoted", "rejected"}),
    "promoted": frozenset(),
    "rejected": frozenset(),
}

JOB_ATTEMPT_TRANSITIONS: Mapping[str, frozenset[str]] = {
    "prepared": frozenset({"submitted", "cancel_pending"}),
    "submitted": frozenset({"acknowledged", "heartbeat_lost", "failed", "cancel_pending"}),
    "acknowledged": frozenset({"running", "heartbeat_lost", "failed", "cancel_pending"}),
    "running": frozenset({"succeeded", "failed", "heartbeat_lost", "cancel_pending"}),
    "heartbeat_lost": frozenset({"reconciling", "cancel_pending"}),
    "reconciling": frozenset({"running", "succeeded", "failed", "orphaned", "reconcile_ambiguous", "cancel_pending"}),
    # UNKNOWN is durable ambiguity: retry inspect/reconcile, never launch again.
    "reconcile_ambiguous": frozenset({"reconciling", "cancel_pending"}),
    "failed": frozenset({"resume_prepared"}),
    "orphaned": frozenset({"resume_prepared"}),
    "resume_prepared": frozenset({"resumed_as_new_attempt", "cancel_pending"}),
    "resumed_as_new_attempt": frozenset(),
    "cancel_pending": frozenset({"cancelled", "reconciling"}),
    "cancelled": frozenset(),
    "succeeded": frozenset(),
}

CLAIM_TRANSITIONS: Mapping[str, frozenset[str]] = {
    "draft": frozenset({"evidence_pending"}),
    "evidence_pending": frozenset({"reviewer_pending", "rejected"}),
    "reviewer_pending": frozenset({"accepted", "rejected", "evidence_pending"}),
    "accepted": frozenset({"stale"}),
    "rejected": frozenset({"stale"}),
    "stale": frozenset(),
}


def validate_transition(
    transitions: Mapping[str, frozenset[str]],
    current: str,
    target: str,
    *,
    expected_state_version: int,
    actual_state_version: int,
) -> int:
    """Validate an explicit edge and return the next CAS version."""
    _positive_version(expected_state_version, "expected_state_version")
    _positive_version(actual_state_version, "actual_state_version")
    if expected_state_version != actual_state_version:
        raise StaleStateVersion(
            f"stale state version: expected {expected_state_version}, actual {actual_state_version}"
        )
    if current not in transitions or target not in transitions[current]:
        raise InvalidTransition(f"illegal transition: {current} -> {target}")
    return actual_state_version + 1


@dataclass(frozen=True, slots=True)
class ResearchContract:
    contract_id: str
    version: int
    workspace_id: str
    project_ref: str
    goal_ref: str
    requirement_ref: str
    agent_plan_id: str
    contract_artifact_id: str
    payload_json: str
    content_hash: str
    protocol_hash: str | None = None
    code_hash: str | None = None
    data_hash: str | None = None
    evidence_hash: str | None = None
    status: str = "draft"
    state_version: int = 1
    supersedes_version: int | None = None

    def __post_init__(self) -> None:
        for field in ("contract_id", "workspace_id", "project_ref", "goal_ref", "requirement_ref", "agent_plan_id", "contract_artifact_id"):
            _id(getattr(self, field), field)
        _positive_version(self.version, "version")
        _positive_version(self.state_version, "state_version")
        if self.status not in CONTRACT_TRANSITIONS:
            raise ResearchDomainError("unsupported contract status")
        parsed = json.loads(self.payload_json)
        if canonical_json(parsed) != self.payload_json:
            raise ResearchDomainError("payload_json must already be canonical")
        if canonical_hash(parsed) != _hash(self.content_hash, "content_hash"):
            raise ResearchDomainError("contract content_hash does not match payload")
        derived_hashes = {
            "protocol_hash": canonical_hash(parsed.get("protocol", parsed)),
            "code_hash": canonical_hash(parsed.get("code_revision")),
            "data_hash": canonical_hash(parsed.get("datasets", [])),
            "evidence_hash": canonical_hash(parsed.get("acceptance_criteria", [])),
        }
        for field, derived in derived_hashes.items():
            supplied = getattr(self, field)
            if supplied is None:
                object.__setattr__(self, field, derived)
            else:
                if _hash(supplied, field) != derived:
                    raise ResearchDomainError(f"{field} does not match canonical payload-derived value")
        if self.supersedes_version is not None and self.supersedes_version >= self.version:
            raise ResearchDomainError("supersedes_version must precede version")

    @classmethod
    def create(cls, *, payload: Mapping[str, Any], **fields: Any) -> "ResearchContract":
        payload_json = canonical_json(payload)
        return cls(payload_json=payload_json, content_hash=canonical_hash(payload), **fields)

    @property
    def payload(self) -> Any:
        return json.loads(self.payload_json)

    def transition(self, target: str, *, expected_state_version: int) -> "ResearchContract":
        next_version = validate_transition(
            CONTRACT_TRANSITIONS, self.status, target,
            expected_state_version=expected_state_version,
            actual_state_version=self.state_version,
        )
        return replace(self, status=target, state_version=next_version)

    def revision(self, *, payload: Mapping[str, Any]) -> "ResearchContract":
        """Create a new immutable draft; persistence supersedes the old row atomically."""
        return ResearchContract.create(
            contract_id=self.contract_id,
            version=self.version + 1,
            workspace_id=self.workspace_id,
            project_ref=self.project_ref,
            goal_ref=self.goal_ref,
            requirement_ref=self.requirement_ref,
            agent_plan_id=self.agent_plan_id,
            contract_artifact_id=self.contract_artifact_id,
            payload=payload,
            status="draft",
            state_version=1,
            supersedes_version=self.version,
        )


@dataclass(frozen=True, slots=True)
class Trial:
    trial_id: str
    contract_id: str
    contract_version: int
    task_id: str
    status: str = "proposed"
    state_version: int = 1
    legacy_trial_id: str | None = None

    def __post_init__(self) -> None:
        for field in ("trial_id", "contract_id", "task_id"):
            _id(getattr(self, field), field)
        _positive_version(self.contract_version, "contract_version")
        _positive_version(self.state_version, "state_version")
        if self.legacy_trial_id is not None:
            _id(self.legacy_trial_id, "legacy_trial_id")
        if self.status not in TRIAL_TRANSITIONS:
            raise ResearchDomainError("unsupported trial status")

    def transition(self, target: str, *, expected_state_version: int) -> "Trial":
        return replace(
            self,
            status=target,
            state_version=validate_transition(
                TRIAL_TRANSITIONS, self.status, target,
                expected_state_version=expected_state_version,
                actual_state_version=self.state_version,
            ),
        )


@dataclass(frozen=True, slots=True)
class JobAttempt:
    attempt_id: str
    trial_id: str
    run_id: str
    attempt_number: int
    status: str = "prepared"
    state_version: int = 1
    reconcile_outcome: str | None = None
    legacy_attempt_id: str | None = None

    def __post_init__(self) -> None:
        for field in ("attempt_id", "trial_id", "run_id"):
            _id(getattr(self, field), field)
        _positive_version(self.attempt_number, "attempt_number")
        _positive_version(self.state_version, "state_version")
        if self.status not in JOB_ATTEMPT_TRANSITIONS:
            raise ResearchDomainError("unsupported attempt status")
        if self.reconcile_outcome not in {None, "running", "succeeded", "failed", "orphaned", "unknown"}:
            raise ResearchDomainError("unsupported reconcile outcome")
        allowed_outcome = {
            "running": "running", "succeeded": "succeeded", "failed": "failed",
            "orphaned": "orphaned", "reconcile_ambiguous": "unknown",
        }.get(self.status)
        if self.reconcile_outcome is not None and self.reconcile_outcome != allowed_outcome:
            raise ResearchDomainError("reconcile_outcome does not match the durable result state")
        if self.legacy_attempt_id is not None:
            _id(self.legacy_attempt_id, "legacy_attempt_id")

    def transition(
        self, target: str, *, expected_state_version: int, reconcile_outcome: str | None = None
    ) -> "JobAttempt":
        if self.status != "reconciling" and reconcile_outcome is not None:
            raise ResearchDomainError("reconcile_outcome is only valid for a reconciling result")
        if self.status == "reconciling" and target != "cancel_pending":
            expected_target = {
                "running": "running", "succeeded": "succeeded", "failed": "failed",
                "orphaned": "orphaned", "unknown": "reconcile_ambiguous",
            }.get(reconcile_outcome or "")
            if target != expected_target:
                raise ResearchDomainError("reconcile result must explicitly determine its target")
        next_version = validate_transition(
            JOB_ATTEMPT_TRANSITIONS, self.status, target,
            expected_state_version=expected_state_version,
            actual_state_version=self.state_version,
        )
        return replace(self, status=target, state_version=next_version, reconcile_outcome=reconcile_outcome)


@dataclass(frozen=True, slots=True)
class Checkpoint:
    checkpoint_id: str
    attempt_id: str
    artifact_id: str
    source_run_id: str
    content_hash: str
    protocol_hash: str
    validity: str = "pending"

    def __post_init__(self) -> None:
        for field in ("checkpoint_id", "attempt_id", "artifact_id", "source_run_id"):
            _id(getattr(self, field), field)
        _hash(self.content_hash, "content_hash")
        _hash(self.protocol_hash, "protocol_hash")
        if self.validity not in {"pending", "valid", "quarantined", "legacy_unverified"}:
            raise ResearchDomainError("unsupported checkpoint validity")


@dataclass(frozen=True, slots=True)
class MetricSnapshot:
    metric_id: str
    attempt_id: str
    artifact_id: str
    name: str
    value: float
    step: int | None = None
    evaluation_id: str | None = None
    validity: str = "valid"

    def __post_init__(self) -> None:
        for field in ("metric_id", "attempt_id", "artifact_id"):
            _id(getattr(self, field), field)
        if self.evaluation_id is not None:
            _id(self.evaluation_id, "evaluation_id")
        if not self.name or len(self.name) > 160:
            raise ResearchDomainError("metric name is required and bounded")
        if isinstance(self.value, bool) or not isinstance(self.value, (int, float)) or not math.isfinite(float(self.value)):
            raise ResearchDomainError("metric value must be finite")
        if self.step is not None and type(self.step) is not int:
            raise ResearchDomainError("metric step must be an exact integer")
        if self.step is not None and self.step < 0:
            raise ResearchDomainError("metric step must be non-negative")
        if self.validity not in {"valid", "invalid", "legacy_unverified"}:
            raise ResearchDomainError("unsupported metric validity")


@dataclass(frozen=True, slots=True)
class ResearchClaim:
    claim_id: str
    contract_id: str
    contract_version: int
    evaluation_id: str
    statement: str
    status: str = "draft"
    state_version: int = 1
    machine_gate_passed: bool = False
    independent_reviewer_id: str | None = None

    def __post_init__(self) -> None:
        for field in ("claim_id", "contract_id", "evaluation_id"):
            _id(getattr(self, field), field)
        _positive_version(self.contract_version, "contract_version")
        _positive_version(self.state_version, "state_version")
        if not self.statement or len(self.statement) > 4000:
            raise ResearchDomainError("claim statement is required and bounded")
        if self.independent_reviewer_id is not None:
            _id(self.independent_reviewer_id, "independent_reviewer_id")
        if type(self.machine_gate_passed) is not bool:
            raise ResearchDomainError("machine_gate_passed must be an exact boolean")
        if self.status not in CLAIM_TRANSITIONS:
            raise ResearchDomainError("unsupported claim status")
        if self.status == "accepted" and not (self.machine_gate_passed and self.independent_reviewer_id):
            raise ResearchDomainError("accepted claim requires machine and independent reviewer gates")

    def transition(
        self,
        target: str,
        *,
        expected_state_version: int,
        machine_gate_passed: bool | None = None,
        independent_reviewer_id: str | None = None,
    ) -> "ResearchClaim":
        gate = self.machine_gate_passed if machine_gate_passed is None else machine_gate_passed
        if type(gate) is not bool:
            raise ResearchDomainError("machine_gate_passed must be an exact boolean")
        reviewer = independent_reviewer_id or self.independent_reviewer_id
        if target == "accepted" and not (gate and reviewer):
            raise ResearchDomainError("accepted claim requires machine and independent reviewer gates")
        return replace(
            self,
            status=target,
            state_version=validate_transition(
                CLAIM_TRANSITIONS, self.status, target,
                expected_state_version=expected_state_version,
                actual_state_version=self.state_version,
            ),
            machine_gate_passed=gate,
            independent_reviewer_id=reviewer,
        )


def classify_legacy_record(kind: str, record_id: str) -> dict[str, str | bool]:
    """Conservatively classify pre-v0.5 rows; never promote historical evidence."""
    if kind not in {"experiment", "trial", "attempt", "checkpoint", "metric", "claim"}:
        raise ResearchDomainError("unsupported legacy record kind")
    _id(record_id, "record_id")
    result: dict[str, str | bool] = {
        "record_id": record_id,
        "source": "legacy",
        "import_status": "imported_unverified",
        "eligible": True,
        "accepted": False,
    }
    if kind == "metric":
        result["validity"] = "legacy_unverified"
    return result
