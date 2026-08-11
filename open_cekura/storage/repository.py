"""Persistence protocol for the OpenCekura reliability projection."""

from __future__ import annotations

from typing import Any, Protocol, Sequence, runtime_checkable

from open_cekura.domain.models import (
    AgentUnderTest,
    AgentVersion,
    Campaign,
    ConversationRun,
    ConversationTurn,
    EvaluationResult,
    EvidenceManifest,
    FailureCase,
    FailureCluster,
    ObservedToolCall,
    Persona,
    RegressionCase,
    ReleaseGateDecision,
    Scenario,
    ScenarioSuite,
)
from open_cekura.scenarios.schema import ScenarioDefinition


class RepositoryError(RuntimeError):
    """Base persistence failure for a reliability projection."""


class RepositoryConflictError(RepositoryError):
    """A stable vertical ID was rebound to incompatible facts."""


class AuthorityMappingError(RepositoryError):
    """A non-null MIS mapping is missing, cross-workspace, or inconsistent."""


@runtime_checkable
class Repository(Protocol):
    workspace_id: str

    def initialize_schema(self) -> None: ...

    def upsert_agent(self, agent: AgentUnderTest) -> str: ...
    def upsert_agent_version(self, version: AgentVersion) -> str: ...
    def list_agents(
        self, *, limit: int = 50, offset: int = 0
    ) -> list[dict[str, Any]]: ...
    def get_agent(self, agent_id: str) -> dict[str, Any] | None: ...

    def upsert_scenario_suite(self, suite: ScenarioSuite) -> str: ...
    def upsert_persona(self, persona: Persona) -> str: ...
    def upsert_scenario(
        self,
        scenario: Scenario,
        *,
        contract: ScenarioDefinition,
    ) -> str: ...
    def list_scenario_suites(
        self, *, limit: int = 50, offset: int = 0
    ) -> list[dict[str, Any]]: ...
    def get_scenario_suite(self, suite_id: str) -> dict[str, Any] | None: ...
    def list_scenarios(
        self,
        *,
        suite_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]: ...
    def get_scenario(self, scenario_id: str) -> dict[str, Any] | None: ...

    def upsert_campaign(self, campaign: Campaign) -> str: ...
    def list_campaigns(
        self, *, limit: int = 50, offset: int = 0
    ) -> list[dict[str, Any]]: ...
    def get_campaign(self, campaign_id: str) -> dict[str, Any] | None: ...

    def upsert_run_projection(
        self,
        run: ConversationRun,
        *,
        turns: Sequence[ConversationTurn],
        tool_calls: Sequence[ObservedToolCall],
        evaluations: Sequence[EvaluationResult],
    ) -> str: ...
    def list_runs(
        self,
        *,
        campaign_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]: ...
    def get_run(self, run_id: str) -> dict[str, Any] | None: ...

    def upsert_failure(self, failure: FailureCase) -> str: ...
    def upsert_failure_cluster(self, cluster: FailureCluster) -> str: ...
    def list_failure_clusters(
        self,
        *,
        campaign_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]: ...
    def get_failure_cluster(self, cluster_id: str) -> dict[str, Any] | None: ...
    def list_failures(
        self,
        *,
        run_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]: ...
    def get_failure(self, failure_id: str) -> dict[str, Any] | None: ...

    def upsert_regression(self, regression: RegressionCase) -> str: ...
    def list_regressions(
        self,
        *,
        scenario_id: str | None = None,
        source_run_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]: ...
    def get_regression(self, regression_id: str) -> dict[str, Any] | None: ...

    def upsert_release_gate(self, gate: ReleaseGateDecision) -> str: ...
    def list_release_gates(
        self,
        *,
        campaign_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]: ...
    def get_release_gate(self, gate_id: str) -> dict[str, Any] | None: ...

    def upsert_evidence_manifest(self, manifest: EvidenceManifest) -> str: ...
    def list_evidence_manifests(
        self, *, limit: int = 50, offset: int = 0
    ) -> list[dict[str, Any]]: ...
    def get_evidence_manifest(self, manifest_id: str) -> dict[str, Any] | None: ...


__all__ = [
    "AuthorityMappingError",
    "Repository",
    "RepositoryConflictError",
    "RepositoryError",
]
