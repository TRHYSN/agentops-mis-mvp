from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

from open_cekura.scenarios.loader import load_suite
from open_cekura.scenarios.schema import (
    AmbiguousIdentityChallenge,
    BackendTimeoutChallenge,
    ChangeConstraintChallenge,
    DuplicateRequestChallenge,
    FalseSuccessClaimChallenge,
    InterruptChallenge,
    MutationBeforeConfirmationChallenge,
    ScenarioDefinition,
    ToolSuccessClaimFailureChallenge,
    UnavailableSlotChallenge,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
SCENARIO_SUITE = REPO_ROOT / "examples" / "open-cekura" / "scenarios"

CoveragePredicate = Callable[[ScenarioDefinition], bool]
REQUIRED_COVERAGE: dict[str, CoveragePredicate] = {
    "basic_success": lambda scenario: not scenario.challenges,
    "interruption": lambda scenario: any(
        isinstance(challenge, InterruptChallenge) for challenge in scenario.challenges
    ),
    "change_date_mid_flow": lambda scenario: any(
        isinstance(challenge, ChangeConstraintChallenge) for challenge in scenario.challenges
    ),
    "ambiguous_identity": lambda scenario: any(
        isinstance(challenge, AmbiguousIdentityChallenge) for challenge in scenario.challenges
    ),
    "unavailable_slot": lambda scenario: any(
        isinstance(challenge, UnavailableSlotChallenge) for challenge in scenario.challenges
    ),
    "duplicate_request": lambda scenario: any(
        isinstance(challenge, DuplicateRequestChallenge) for challenge in scenario.challenges
    ),
    "mutation_before_confirmation": lambda scenario: any(
        isinstance(challenge, MutationBeforeConfirmationChallenge)
        for challenge in scenario.challenges
    ),
    "backend_timeout": lambda scenario: any(
        isinstance(challenge, BackendTimeoutChallenge) for challenge in scenario.challenges
    ),
    "tool_succeeded_agent_claims_failure": lambda scenario: any(
        isinstance(challenge, ToolSuccessClaimFailureChallenge)
        for challenge in scenario.challenges
    ),
    "agent_claims_success_state_not_mutated": lambda scenario: any(
        isinstance(challenge, FalseSuccessClaimChallenge)
        for challenge in scenario.challenges
    ),
}

FORBIDDEN_OUTCOME_KEYS = {
    "campaign_id",
    "baseline_campaign_id",
    "candidate_campaign_id",
    "gate_decision",
    "gate_outcome",
    "release_gate_decision",
    "release_gate_outcome",
    "expected_gate_outcome",
}


def scenario_paths() -> list[Path]:
    if not SCENARIO_SUITE.is_dir():
        return []
    return sorted(
        (
            path
            for path in SCENARIO_SUITE.iterdir()
            if path.is_file() and path.suffix.lower() in {".yaml", ".yml"}
        ),
        key=lambda path: (path.name.casefold(), path.name),
    )


def walk_mapping_keys(value: Any) -> Iterator[str]:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            yield str(key)
            yield from walk_mapping_keys(nested)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for nested in value:
            yield from walk_mapping_keys(nested)


def test_all_public_appointment_fixtures_load_as_scenario_v1() -> None:
    paths = scenario_paths()

    assert len(paths) >= 10, f"expected at least ten public appointment YAML fixtures in {SCENARIO_SUITE}"
    scenarios = load_suite(SCENARIO_SUITE)

    assert len(scenarios) == len(paths)
    assert all(scenario.schema_version == 1 for scenario in scenarios)
    assert all(scenario.id.startswith("appointment.") for scenario in scenarios)


def test_public_appointment_fixture_ids_are_unique() -> None:
    scenarios = load_suite(SCENARIO_SUITE)
    ids = [scenario.id for scenario in scenarios]

    assert len(ids) == len(set(ids))


def test_public_appointment_suite_covers_all_required_behavior_classes() -> None:
    scenarios = load_suite(SCENARIO_SUITE)
    covered = {
        category
        for category, predicate in REQUIRED_COVERAGE.items()
        if any(predicate(scenario) for scenario in scenarios)
    }

    assert covered == set(REQUIRED_COVERAGE), (
        "public appointment suite is missing required behavior classes: "
        f"{sorted(set(REQUIRED_COVERAGE) - covered)}"
    )


def test_public_appointment_fixtures_do_not_hardcode_campaign_or_gate_outcomes() -> None:
    paths = scenario_paths()

    assert paths, f"expected public appointment YAML fixtures in {SCENARIO_SUITE}"
    for path in paths:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        keys = set(walk_mapping_keys(payload))
        forbidden = keys.intersection(FORBIDDEN_OUTCOME_KEYS)
        assert not forbidden, f"{path.name} hardcodes campaign/gate outcome keys: {sorted(forbidden)}"
