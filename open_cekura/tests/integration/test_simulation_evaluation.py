from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

from open_cekura.domain.enums import AdapterKind, EvaluationStatus
from open_cekura.domain.models import AgentVersion
from open_cekura.evaluation.base import context_from_simulation
from open_cekura.evaluation.rules import evaluate_deterministic
from open_cekura.scenarios.loader import load_suite
from open_cekura.simulation.mock_agent import MockAgentAdapter, MockAgentConfig
from open_cekura.simulation.runner import run_scenario


NOW = datetime(2026, 8, 11, 16, 0, tzinfo=timezone.utc)
SCENARIOS = Path(__file__).resolve().parents[3] / "examples" / "open-cekura" / "scenarios"


def evaluate_profile(config: MockAgentConfig, profile: str):
    version = AgentVersion(
        schema_version=1,
        id=f"ocagentv_{profile}",
        agent_id="ocagent_appointment",
        version=profile,
        adapter_kind=AdapterKind.MOCK,
        config_sha256=config.canonical_sha256(),
        created_at=NOW,
    )
    scenarios = load_suite(SCENARIOS)

    async def simulate_suite():
        simulations = []
        for scenario in scenarios:
            simulations.append(
                await run_scenario(
                    scenario=scenario,
                    agent_version=version,
                    adapter=MockAgentAdapter(config),
                    campaign_id=f"occampaign_{profile}",
                    created_at=NOW,
                )
            )
        return simulations

    rows = {}
    for scenario, simulation in zip(
        scenarios,
        asyncio.run(simulate_suite()),
        strict=True,
    ):
        context = context_from_simulation(simulation, evaluated_at=NOW)
        rows[scenario.id] = evaluate_deterministic(context)
    return rows


def test_candidate_passes_all_deterministic_rules_across_public_suite() -> None:
    rows = evaluate_profile(MockAgentConfig.candidate(), "candidate")

    assert len(rows) >= 10
    assert {
        result.status
        for results in rows.values()
        for result in results
    } == {EvaluationStatus.PASS}


def test_baseline_failures_are_naturally_derived_from_three_defect_classes() -> None:
    rows = evaluate_profile(MockAgentConfig.baseline(), "baseline")
    failed_by_scenario = {
        scenario_id: {
            result.evaluator_id
            for result in results
            if result.status is EvaluationStatus.FAIL
        }
        for scenario_id, results in rows.items()
    }

    assert "duplicate_mutation.v1" in failed_by_scenario["appointment.duplicate_request"]
    assert "confirmation_before_mutation.v1" in failed_by_scenario[
        "appointment.mutation_before_confirmation"
    ]
    assert "task_success.v1" in failed_by_scenario[
        "appointment.agent_claims_success_without_mutation"
    ]
    assert "final_state_match.v1" in failed_by_scenario[
        "appointment.agent_claims_success_without_mutation"
    ]
    assert all(
        not failures
        for scenario_id, failures in failed_by_scenario.items()
        if scenario_id
        not in {
            "appointment.duplicate_request",
            "appointment.mutation_before_confirmation",
            "appointment.agent_claims_success_without_mutation",
        }
    )
