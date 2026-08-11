"""Compose a validated Scenario suite into a deterministic reliability campaign."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from open_cekura.domain.enums import (
    AdapterKind,
    CampaignStatus,
    EvaluationStatus,
    RunFinalState,
    Verbosity,
)
from open_cekura.domain.ids import stable_id
from open_cekura.domain.models import (
    AgentUnderTest,
    AgentVersion,
    Campaign,
    EvaluationResult,
    FailureCase,
    Persona,
    RegressionCase,
    Scenario,
    ScenarioSuite,
)
from open_cekura.evaluation.aggregation import (
    CampaignMetrics,
    EvaluationResultGroup,
    RunMetricFacts,
    aggregate_campaign_metrics,
)
from open_cekura.evaluation.base import EvaluationContext, context_from_simulation
from open_cekura.evaluation.rules import evaluate_deterministic
from open_cekura.regression.builder import build_failure_cases, build_regression_case
from open_cekura.release_gate.policy import CampaignGateInput
from open_cekura.scenarios.loader import (
    DuplicateScenarioIdError,
    ScenarioContractError,
    load_scenario,
)
from open_cekura.scenarios.schema import ScenarioDefinition
from open_cekura.simulation.mock_agent import MockAgentAdapter, MockAgentConfig
from open_cekura.simulation.runner import SimulationResult, run_scenario


@dataclass(frozen=True, slots=True)
class CampaignRunRecord:
    """One complete, explainable Scenario execution inside a campaign."""

    source_path: Path
    source_bytes: bytes
    scenario_contract: ScenarioDefinition
    scenario: Scenario
    persona: Persona
    simulation: SimulationResult
    evaluation_context: EvaluationContext
    evaluations: tuple[EvaluationResult, ...]
    failures: tuple[FailureCase, ...]
    regressions: tuple[RegressionCase, ...]


@dataclass(frozen=True, slots=True)
class CampaignExecution:
    """Closed deterministic facts ready for evidence and persistence adapters."""

    agent: AgentUnderTest
    agent_version: AgentVersion
    agent_config: MockAgentConfig
    scenario_suite: ScenarioSuite
    campaign: Campaign
    records: tuple[CampaignRunRecord, ...]
    metrics: CampaignMetrics

    @property
    def failures(self) -> tuple[FailureCase, ...]:
        return tuple(failure for record in self.records for failure in record.failures)

    @property
    def regressions(self) -> tuple[RegressionCase, ...]:
        return tuple(
            regression for record in self.records for regression in record.regressions
        )

    def gate_input(
        self,
        *,
        evidence_verified: bool,
        evidence_refs: Iterable[str] | None = None,
    ) -> CampaignGateInput:
        references = list(
            evidence_refs
            if evidence_refs is not None
            else [f"artifact:{self.campaign.id}/campaign_summary.json"]
        )
        return CampaignGateInput(
            campaign_id=self.campaign.id,
            metrics=self.metrics,
            scenario_by_run={
                record.simulation.run.id: record.scenario.id for record in self.records
            },
            scenario_ids=[record.scenario.id for record in self.records],
            scenario_sha256_by_id={
                record.scenario.id: record.scenario.source_sha256
                for record in self.records
            },
            evaluator_versions=sorted(
                {
                    result.evaluator_id
                    for record in self.records
                    for result in record.evaluations
                }
            ),
            evidence_verified=evidence_verified,
            evidence_refs=references,
        )


async def execute_mock_campaign(
    *,
    suite_path: str | Path,
    config: MockAgentConfig,
    version: str,
    campaign_id: str,
    workspace_id: str,
    created_at: datetime | None = None,
) -> CampaignExecution:
    """Run the deterministic v0 loop without performing persistence side effects."""

    if not isinstance(config, MockAgentConfig):
        raise TypeError("config must be MockAgentConfig")
    if not isinstance(version, str) or not version.strip():
        raise ValueError("version must be a non-empty string")
    if not isinstance(campaign_id, str) or not campaign_id:
        raise ValueError("campaign_id must be a non-empty string")
    if not isinstance(workspace_id, str) or not workspace_id:
        raise ValueError("workspace_id must be a non-empty string")

    timestamp = created_at or datetime.now(timezone.utc)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("created_at must be timezone-aware")
    timestamp = timestamp.astimezone(timezone.utc)
    sources = _load_sources(Path(suite_path))

    suite_fingerprint = "|".join(
        f"{contract.id}:{_sha256(source_bytes)}"
        for _, source_bytes, contract in sources
    )
    suite = ScenarioSuite(
        schema_version=1,
        id=stable_id("ocsuite", suite_fingerprint),
        name="AI Appointment Agent Reliability Test",
        description="Deterministic appointment-agent reliability scenarios.",
        created_at=timestamp,
    )
    agent = AgentUnderTest(
        schema_version=1,
        id=stable_id("ocagent", workspace_id, "appointment-agent"),
        name="AI Appointment Agent",
        description="Appointment agent exercised by the public deterministic demo.",
        workspace_id=workspace_id,
        created_at=timestamp,
    )
    agent_version = AgentVersion(
        schema_version=1,
        id=stable_id("ocagentv", agent.id, version.strip(), config.canonical_sha256()),
        agent_id=agent.id,
        version=version.strip(),
        adapter_kind=AdapterKind.MOCK,
        config_sha256=config.canonical_sha256(),
        created_at=timestamp,
    )
    campaign = Campaign(
        schema_version=1,
        id=campaign_id,
        agent_version_id=agent_version.id,
        scenario_suite_id=suite.id,
        status=CampaignStatus.COMPLETED,
        mis_task_id=None,
        mis_plan_id=None,
        created_at=timestamp,
    )

    records: list[CampaignRunRecord] = []
    groups: list[EvaluationResultGroup] = []
    run_facts: list[RunMetricFacts] = []
    for source_path, source_bytes, contract in sources:
        persona = _persona(contract, timestamp)
        scenario = Scenario(
            schema_version=1,
            id=contract.id,
            suite_id=suite.id,
            persona_id=persona.id,
            name=contract.name,
            initial_message=contract.initial_message,
            goal_type=contract.goal.type.value,
            source_sha256=_sha256(source_bytes),
            created_at=timestamp,
        )
        simulation = await run_scenario(
            scenario=contract,
            agent_version=agent_version,
            adapter=MockAgentAdapter(config),
            campaign_id=campaign.id,
            created_at=timestamp,
        )
        context = context_from_simulation(simulation, evaluated_at=simulation.finished_at)
        evaluations = tuple(evaluate_deterministic(context))
        evaluated_status = _evaluated_run_status(
            simulation.run.status,
            evaluations,
            adapter_error=simulation.adapter_error,
        )
        simulation = simulation.model_copy(
            update={
                "run": simulation.run.model_copy(update={"status": evaluated_status})
            }
        )
        failures = tuple(build_failure_cases(context, evaluations))
        evaluations_by_id = {evaluation.id: evaluation for evaluation in evaluations}
        regressions = tuple(
            build_regression_case(
                failure=failure,
                scenario=contract,
                evaluation=evaluations_by_id[failure.evaluation_result_id],
                created_at=simulation.finished_at,
            )
            for failure in failures
        )
        record = CampaignRunRecord(
            source_path=source_path,
            source_bytes=source_bytes,
            scenario_contract=contract,
            scenario=scenario,
            persona=persona,
            simulation=simulation,
            evaluation_context=context,
            evaluations=evaluations,
            failures=failures,
            regressions=regressions,
        )
        records.append(record)
        groups.append(
            EvaluationResultGroup(
                run_id=simulation.run.id,
                deterministic_results=list(evaluations),
            )
        )
        run_facts.append(
            RunMetricFacts(
                run_id=simulation.run.id,
                turn_count=len(simulation.turns),
                latency_ms=simulation.duration_ms,
            )
        )

    return CampaignExecution(
        agent=agent,
        agent_version=agent_version,
        agent_config=config,
        scenario_suite=suite,
        campaign=campaign,
        records=tuple(records),
        metrics=aggregate_campaign_metrics(groups, run_facts),
    )


def _evaluated_run_status(
    _observed_status: RunFinalState,
    evaluations: tuple[EvaluationResult, ...],
    *,
    adapter_error: str | None,
) -> RunFinalState:
    # A deterministic mock tool timeout is an observed scenario outcome, not an
    # adapter lifecycle/contract failure.  The timeout evaluator remains the
    # authority for whether that outcome is expected.  All other adapter errors
    # fail closed before evaluator pass/fail aggregation.
    fatal_adapter_error = adapter_error is not None and not adapter_error.startswith(
        "tool_timeout:"
    )
    if fatal_adapter_error or not evaluations:
        return RunFinalState.ERROR
    if any(
        result.status in {EvaluationStatus.ERROR, EvaluationStatus.SKIPPED}
        for result in evaluations
    ):
        return RunFinalState.ERROR
    if any(result.status is EvaluationStatus.FAIL for result in evaluations):
        return RunFinalState.FAIL
    return RunFinalState.PASS


def _load_sources(
    suite_path: Path,
) -> list[tuple[Path, bytes, ScenarioDefinition]]:
    if not suite_path.is_dir():
        raise ScenarioContractError(
            f"Scenario suite directory does not exist: {suite_path}"
        )
    paths = sorted(
        (
            path
            for path in suite_path.iterdir()
            if path.is_file() and path.suffix.lower() in {".yaml", ".yml"}
        ),
        key=lambda path: (path.name.casefold(), path.name),
    )
    if not paths:
        raise ScenarioContractError(
            f"Scenario suite contains no .yaml or .yml files: {suite_path}"
        )
    sources: list[tuple[Path, bytes, ScenarioDefinition]] = []
    source_by_id: dict[str, Path] = {}
    for path in paths:
        contract = load_scenario(path)
        previous = source_by_id.get(contract.id)
        if previous is not None:
            raise DuplicateScenarioIdError(
                f"Duplicate scenario id {contract.id!r} in {previous} and {path}"
            )
        try:
            source_bytes = path.read_bytes()
        except OSError as error:
            raise ScenarioContractError(f"Cannot read Scenario YAML {path}: {error}") from error
        source_by_id[contract.id] = path
        sources.append((path.resolve(), source_bytes, contract))
    return sources


def _persona(contract: ScenarioDefinition, created_at: datetime) -> Persona:
    spec = contract.persona
    return Persona(
        schema_version=1,
        id=stable_id(
            "ocpersona",
            spec.language,
            spec.tone,
            spec.verbosity.value,
        ),
        name=f"{spec.tone} {spec.language} persona",
        language=spec.language,
        tone=spec.tone,
        verbosity=Verbosity(spec.verbosity.value),
        created_at=created_at,
    )


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


__all__ = ["CampaignExecution", "CampaignRunRecord", "execute_mock_campaign"]
