"""Fail-fast YAML loading for OpenCekura Scenario v1."""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

import yaml
from pydantic import ValidationError

from .schema import ScenarioDefinition


class ScenarioContractError(ValueError):
    """A scenario cannot be parsed or does not satisfy the declared schema."""


class DuplicateScenarioIdError(ScenarioContractError):
    """A suite contains more than one scenario with the same stable ID."""


def load_scenario(path: str | Path) -> ScenarioDefinition:
    scenario_path = Path(path)
    if scenario_path.suffix.lower() not in {".yaml", ".yml"}:
        raise ScenarioContractError(f"Scenario path must end in .yaml or .yml: {scenario_path}")
    try:
        source = scenario_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ScenarioContractError(f"Cannot read Scenario YAML {scenario_path}: {exc}") from exc
    try:
        payload = yaml.safe_load(source)
    except yaml.YAMLError as exc:
        raise ScenarioContractError(f"Invalid Scenario YAML {scenario_path}: {exc}") from exc
    try:
        return ScenarioDefinition.model_validate(payload)
    except ValidationError as exc:
        raise ScenarioContractError(f"Scenario contract validation failed for {scenario_path}: {exc}") from exc


def _scenario_paths(directory: Path) -> Iterable[Path]:
    return sorted(
        (
            path
            for path in directory.iterdir()
            if path.is_file() and path.suffix.lower() in {".yaml", ".yml"}
        ),
        key=lambda path: (path.name.casefold(), path.name),
    )


def load_suite(directory: str | Path) -> list[ScenarioDefinition]:
    suite_path = Path(directory)
    if not suite_path.is_dir():
        raise ScenarioContractError(f"Scenario suite directory does not exist: {suite_path}")

    scenarios: list[ScenarioDefinition] = []
    source_by_id: dict[str, Path] = {}
    for path in _scenario_paths(suite_path):
        scenario = load_scenario(path)
        previous = source_by_id.get(scenario.id)
        if previous is not None:
            raise DuplicateScenarioIdError(
                f"Duplicate scenario id {scenario.id!r} in {previous} and {path}"
            )
        source_by_id[scenario.id] = path
        scenarios.append(scenario)
    if not scenarios:
        raise ScenarioContractError(f"Scenario suite contains no .yaml or .yml files: {suite_path}")
    return scenarios
