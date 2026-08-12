from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from open_cekura.scenarios.loader import (
    DuplicateScenarioIdError,
    ScenarioContractError,
    load_scenario,
    load_suite,
)
from open_cekura.scenarios.schema import ScenarioDefinition


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "scenarios"


VALID_SCENARIO = """\
schema_version: 1
id: appointment.change_after_interrupt
name: Change appointment after interruption
persona:
  language: en-US
  tone: impatient
  verbosity: short
initial_message: "I need to move my appointment."
goal:
  type: reschedule
challenges:
  - interrupt_after_turn: 2
  - change_constraint_after_turn: 3
expectations:
  required_tool_calls:
    - lookup_booking
    - update_booking
  forbidden_tool_calls:
    - create_duplicate_booking
  must_confirm_before_mutation: true
  final_state:
    booking_updated: true
"""


def write_scenario(tmp_path: Path, text: str, name: str = "scenario.yaml") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_example_scenario_loads_and_round_trips_strictly() -> None:
    scenario = load_scenario(FIXTURES / "valid_change_after_interrupt.yaml")

    assert scenario.schema_version == 1
    assert scenario.id == "appointment.change_after_interrupt"
    assert scenario.persona.language == "en-US"
    assert scenario.persona.verbosity.value == "short"
    assert scenario.goal.type.value == "reschedule"
    assert scenario.challenges[0].interrupt_after_turn == 2
    assert scenario.challenges[1].change_constraint_after_turn == 3
    assert scenario.expectations.required_tool_calls == ["lookup_booking", "update_booking"]
    assert scenario.expectations.must_confirm_before_mutation is True
    assert scenario.expectations.final_state == {"booking_updated": True}

    serialized = scenario.model_dump(mode="json")
    assert ScenarioDefinition.model_validate(serialized) == scenario


@pytest.mark.parametrize(
    ("replacement", "expected_fragment"),
    [
        ("schema_version: 2", "schema_version"),
        ("verbosity: enormous", "verbosity"),
        ("  unknown_contract_field: true\ninitial_message:", "unknown_contract_field"),
        ("  - unknown_challenge: 2", "unknown_challenge"),
    ],
)
def test_contract_errors_fail_fast(
    tmp_path: Path,
    replacement: str,
    expected_fragment: str,
) -> None:
    if replacement.startswith("schema_version"):
        invalid = VALID_SCENARIO.replace("schema_version: 1", replacement)
    elif replacement.startswith("verbosity"):
        invalid = VALID_SCENARIO.replace("verbosity: short", replacement)
    elif replacement.startswith("  unknown_contract"):
        invalid = VALID_SCENARIO.replace("initial_message:", replacement)
    else:
        invalid = VALID_SCENARIO.replace("  - interrupt_after_turn: 2", replacement)

    with pytest.raises(ScenarioContractError, match=expected_fragment):
        load_scenario(write_scenario(tmp_path, invalid))


def test_direct_schema_validation_does_not_ignore_unknown_fields() -> None:
    payload = {
        "schema_version": 1,
        "id": "appointment.basic",
        "name": "Basic appointment",
        "persona": {"language": "en-US", "tone": "calm", "verbosity": "short"},
        "initial_message": "Move my appointment.",
        "goal": {"type": "reschedule"},
        "challenges": [],
        "expectations": {
            "required_tool_calls": ["lookup_booking", "update_booking"],
            "forbidden_tool_calls": [],
            "must_confirm_before_mutation": True,
            "final_state": {"booking_updated": True},
        },
        "silently_ignored": "forbidden",
    }

    with pytest.raises(ValidationError) as exc_info:
        ScenarioDefinition.model_validate(payload)

    assert "silently_ignored" in str(exc_info.value)


def test_malformed_yaml_is_a_visible_contract_error(tmp_path: Path) -> None:
    path = write_scenario(tmp_path, "schema_version: 1\nexpectations: [unterminated")

    with pytest.raises(ScenarioContractError, match="YAML") as exc_info:
        load_scenario(path)

    assert str(path) in str(exc_info.value)


def test_scenario_digest_is_canonical_across_yaml_order_and_line_endings(
    tmp_path: Path,
) -> None:
    source = VALID_SCENARIO
    reordered = yaml.safe_dump(yaml.safe_load(source), sort_keys=True).replace(
        "\n", "\r\n"
    )
    first_path = tmp_path / "first.yaml"
    second_path = tmp_path / "second.yaml"
    first_path.write_bytes(source.encode("utf-8"))
    second_path.write_bytes(reordered.encode("utf-8"))

    first = load_scenario(first_path)
    second = load_scenario(second_path)

    assert hashlib.sha256(first_path.read_bytes()).hexdigest() != hashlib.sha256(
        second_path.read_bytes()
    ).hexdigest()
    assert first == second
    assert first.canonical_sha256() == second.canonical_sha256()


def test_suite_rejects_duplicate_stable_ids(tmp_path: Path) -> None:
    write_scenario(tmp_path, VALID_SCENARIO, "first.yaml")
    write_scenario(
        tmp_path,
        VALID_SCENARIO.replace(
            "name: Change appointment after interruption",
            "name: Duplicate scenario identity",
        ),
        "second.yml",
    )

    with pytest.raises(DuplicateScenarioIdError, match="appointment.change_after_interrupt"):
        load_suite(tmp_path)


def test_suite_loads_yaml_files_in_stable_path_order(tmp_path: Path) -> None:
    write_scenario(
        tmp_path,
        VALID_SCENARIO.replace(
            "appointment.change_after_interrupt",
            "appointment.z_last",
        ),
        "z.yml",
    )
    write_scenario(
        tmp_path,
        VALID_SCENARIO.replace(
            "appointment.change_after_interrupt",
            "appointment.a_first",
        ),
        "a.yaml",
    )
    (tmp_path / "ignored.txt").write_text(VALID_SCENARIO, encoding="utf-8")

    scenarios = load_suite(tmp_path)

    assert [scenario.id for scenario in scenarios] == [
        "appointment.a_first",
        "appointment.z_last",
    ]
