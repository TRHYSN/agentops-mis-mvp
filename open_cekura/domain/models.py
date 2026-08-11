"""Strict, versioned OpenCekura reliability-domain objects."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    field_validator,
    model_validator,
)

from .enums import (
    AdapterKind,
    CampaignStatus,
    EvaluationStatus,
    GateDecision,
    RunFinalState,
    TurnRole,
    Verbosity,
)


StableIdentifier = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=1,
        max_length=200,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    ),
]
NonEmptyText = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=1000),
]
LongText = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=100_000)]
Sha256Digest = Annotated[
    str,
    StringConstraints(strict=True, pattern=r"^[0-9a-f]{64}$"),
]
GitCommitSha = Annotated[
    str,
    StringConstraints(strict=True, pattern=r"^[0-9a-f]{40}$"),
]
Score = Annotated[float, Field(strict=True, ge=0.0, le=1.0, allow_inf_nan=False)]
NonNegativeInt = Annotated[int, Field(strict=True, ge=0)]
JsonObject = dict[str, JsonValue]


def _require_utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{field_name} must be UTC-aware")
    return value.astimezone(timezone.utc)


class StrictContract(BaseModel):
    """Base for immutable, fail-closed Pydantic v2 contracts."""

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        frozen=True,
        validate_default=True,
    )


class DomainObject(StrictContract):
    schema_version: Literal[1]
    id: StableIdentifier
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def created_at_is_utc(cls, value: datetime) -> datetime:
        return _require_utc(value, "created_at")

    def canonical_json_bytes(self) -> bytes:
        """Serialize deterministically for hashing and round trips."""

        return json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")


class AgentUnderTest(DomainObject):
    name: NonEmptyText
    description: Annotated[str, StringConstraints(strict=True, max_length=10_000)]
    workspace_id: StableIdentifier | None


class AgentVersion(DomainObject):
    agent_id: StableIdentifier
    version: NonEmptyText
    adapter_kind: AdapterKind
    config_sha256: Sha256Digest


class ScenarioSuite(DomainObject):
    name: NonEmptyText
    description: Annotated[str, StringConstraints(strict=True, max_length=10_000)]


class Persona(DomainObject):
    name: NonEmptyText
    language: NonEmptyText
    tone: NonEmptyText
    verbosity: Verbosity


class Scenario(DomainObject):
    suite_id: StableIdentifier
    persona_id: StableIdentifier
    name: NonEmptyText
    initial_message: LongText
    goal_type: NonEmptyText
    source_sha256: Sha256Digest


class Campaign(DomainObject):
    agent_version_id: StableIdentifier
    scenario_suite_id: StableIdentifier
    status: CampaignStatus
    mis_task_id: StableIdentifier | None
    mis_plan_id: StableIdentifier | None


class ConversationRun(DomainObject):
    campaign_id: StableIdentifier
    scenario_id: StableIdentifier
    agent_version_id: StableIdentifier
    status: RunFinalState
    mis_run_id: StableIdentifier | None


class ConversationTurn(DomainObject):
    run_id: StableIdentifier
    turn_index: NonNegativeInt
    role: TurnRole
    content: LongText


class ObservedToolCall(DomainObject):
    run_id: StableIdentifier
    turn_id: StableIdentifier
    name: NonEmptyText
    arguments: JsonObject
    result: JsonValue | None
    error: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=20_000)] | None
    is_mutation: bool
    duration_ms: NonNegativeInt
    mis_tool_call_id: StableIdentifier | None


class EvaluationResult(DomainObject):
    run_id: StableIdentifier
    evaluator_id: StableIdentifier
    status: EvaluationStatus
    score: Score | None
    threshold: Score | None
    reason_codes: list[StableIdentifier]
    evidence_refs: list[NonEmptyText]
    metadata: JsonObject
    mis_evaluation_id: StableIdentifier | None

    @model_validator(mode="after")
    def status_matches_score(self) -> "EvaluationResult":
        scored = {
            EvaluationStatus.PASS,
            EvaluationStatus.FAIL,
            EvaluationStatus.WARN,
        }
        if self.status in scored and self.score is None:
            raise ValueError("score is required for pass, fail, and warn evaluations")
        if self.status in scored and self.threshold is None:
            raise ValueError("threshold is required for pass, fail, and warn evaluations")
        if self.status in {EvaluationStatus.ERROR, EvaluationStatus.SKIPPED} and self.score is not None:
            raise ValueError("score must be null for error and skipped evaluations")
        return self


class FailureCase(DomainObject):
    run_id: StableIdentifier
    scenario_id: StableIdentifier
    evaluation_result_id: StableIdentifier
    reason_code: StableIdentifier
    expected: JsonValue | None
    observed: JsonValue | None
    evidence_refs: list[NonEmptyText]


class FailureCluster(DomainObject):
    campaign_id: StableIdentifier
    signature: NonEmptyText
    failure_case_ids: Annotated[list[StableIdentifier], Field(min_length=1)]


class RegressionCase(DomainObject):
    failure_case_id: StableIdentifier
    scenario_id: StableIdentifier
    source_run_id: StableIdentifier
    name: NonEmptyText
    original_input: JsonObject
    expected: JsonValue
    observed: JsonValue
    reason_code: StableIdentifier
    evaluator_id: StableIdentifier
    evidence_refs: list[NonEmptyText]
    mis_memory_id: StableIdentifier | None


class GateFact(StrictContract):
    rule_id: StableIdentifier
    message: NonEmptyText
    measured_value: JsonValue | None
    threshold: JsonValue | None
    evidence_refs: list[NonEmptyText]
    scenario_id: StableIdentifier | None = None
    run_id: StableIdentifier | None = None
    evaluation_result_id: StableIdentifier | None = None


class ReleaseGateDecision(DomainObject):
    campaign_id: StableIdentifier
    baseline_campaign_id: StableIdentifier | None
    decision: GateDecision
    policy_version: StableIdentifier
    blockers: list[GateFact]
    warnings: list[GateFact]
    metrics: JsonObject
    evidence_refs: list[NonEmptyText]
    mis_approval_id: StableIdentifier | None


class EvidenceEnvironment(StrictContract):
    os: NonEmptyText
    python_version: NonEmptyText
    node_version: NonEmptyText


class EvidenceManifest(DomainObject):
    campaign_id: StableIdentifier
    run_id: StableIdentifier
    mis_artifact_id: StableIdentifier | None
    mis_plan_evidence_manifest_id: StableIdentifier | None
    git_commit_sha: GitCommitSha
    environment: EvidenceEnvironment
    scenario_sha256: Sha256Digest
    agent_config_sha256: Sha256Digest
    evaluator_versions: Annotated[list[StableIdentifier], Field(min_length=1)]
    artifacts: dict[NonEmptyText, Sha256Digest]
    started_at: datetime
    finished_at: datetime
    final_state: RunFinalState

    @field_validator("started_at", "finished_at")
    @classmethod
    def evidence_times_are_utc(cls, value: datetime, info) -> datetime:
        return _require_utc(value, info.field_name)

    @model_validator(mode="after")
    def evidence_graph_is_consistent(self) -> "EvidenceManifest":
        if self.finished_at < self.started_at:
            raise ValueError("finished_at must not precede started_at")
        if self.created_at < self.finished_at:
            raise ValueError("created_at must not precede finished_at")
        if self.artifacts.get("scenario.yaml") != self.scenario_sha256:
            raise ValueError("scenario_sha256 must match artifacts['scenario.yaml']")
        if self.artifacts.get("agent_version.json") != self.agent_config_sha256:
            raise ValueError(
                "agent_config_sha256 must match artifacts['agent_version.json']"
            )
        return self


__all__ = [
    "AgentUnderTest",
    "AgentVersion",
    "Campaign",
    "ConversationRun",
    "ConversationTurn",
    "DomainObject",
    "EvaluationResult",
    "EvidenceEnvironment",
    "EvidenceManifest",
    "FailureCase",
    "FailureCluster",
    "GateFact",
    "ObservedToolCall",
    "Persona",
    "RegressionCase",
    "ReleaseGateDecision",
    "Scenario",
    "ScenarioSuite",
]
