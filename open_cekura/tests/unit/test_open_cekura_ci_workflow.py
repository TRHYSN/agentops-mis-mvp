from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github" / "workflows" / "open-cekura-windows.yml"


def _workflow() -> tuple[str, dict[str, object]]:
    source = WORKFLOW.read_text(encoding="utf-8")
    payload = yaml.load(source, Loader=yaml.BaseLoader)
    assert isinstance(payload, dict)
    return source, payload


def test_ci_matrix_covers_windows_and_ubuntu_on_supported_python_versions() -> None:
    _, workflow = _workflow()
    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    job = jobs["open-cekura-acceptance"]
    matrix = job["strategy"]["matrix"]

    assert matrix["os"] == ["ubuntu-latest", "windows-latest"]
    assert matrix["python-version"] == ["3.10", "3.11"]
    assert job["runs-on"] == "${{ matrix.os }}"
    assert job["strategy"]["fail-fast"] == "false"


def test_each_matrix_job_runs_the_complete_offline_acceptance_contract() -> None:
    source, workflow = _workflow()
    job = workflow["jobs"]["open-cekura-acceptance"]
    steps = job["steps"]
    run_commands = "\n".join(
        str(step.get("run", "")) for step in steps if isinstance(step, dict)
    )
    uses = [str(step.get("uses", "")) for step in steps if isinstance(step, dict)]

    assert "actions/setup-python@v5" in uses
    assert "actions/setup-node@v4" in uses
    assert "python -m pip install -r requirements-open-cekura.txt" in run_commands
    assert "python -m pytest open_cekura/tests/unit -q" in run_commands
    assert "python -m pytest open_cekura/tests/integration -q" in run_commands
    assert (
        "python -m open_cekura.cli.main scenario validate "
        "examples/open-cekura/scenarios/basic.yaml"
    ) in run_commands
    assert "python scripts/reliability_lab_ui_smoke.py" in run_commands
    assert "npm ci" in run_commands
    assert "npm run build" in run_commands
    windows_acceptance = next(
        step for step in steps if step.get("name") == "Run Windows browser acceptance"
    )
    ubuntu_acceptance = next(
        step for step in steps if step.get("name") == "Run Ubuntu portable acceptance"
    )
    assert windows_acceptance["if"] == "runner.os == 'Windows'"
    assert "--require-browser" in windows_acceptance["run"]
    assert ubuntu_acceptance["if"] == "runner.os != 'Windows'"
    assert "--require-browser" not in ubuntu_acceptance["run"]
    assert "--ui-dist ui/start-building-app/dist" in windows_acceptance["run"]
    assert "--ui-dist ui/start-building-app/dist" in ubuntu_acceptance["run"]

    doctor = next(step for step in steps if step.get("name") == "Run Windows Doctor")
    assert doctor["if"] == "runner.os == 'Windows'"
    assert doctor["run"] == "python -m open_cekura.windows.doctor"

    assert "${{ secrets." not in source
    assert "set -euo pipefail" not in source
    assert "python3 " not in source
    assert "bash" not in source.lower()
