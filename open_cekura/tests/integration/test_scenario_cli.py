from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
VALID_SCENARIO = (
    REPO_ROOT
    / "open_cekura"
    / "tests"
    / "fixtures"
    / "scenarios"
    / "valid_change_after_interrupt.yaml"
)


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-B", "-m", "open_cekura.cli.main", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )


def test_scenario_validate_command_reports_contract_identity() -> None:
    result = run_cli("scenario", "validate", str(VALID_SCENARIO))

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload == {
        "ok": True,
        "operation": "scenario_validate",
        "scenario_id": "appointment.change_after_interrupt",
        "schema_version": 1,
        "source": str(VALID_SCENARIO.resolve()),
        "token_omitted": True,
    }


def test_scenario_validate_command_fails_nonzero_on_incompatible_version(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.yaml"
    invalid.write_text(
        VALID_SCENARIO.read_text(encoding="utf-8").replace("schema_version: 1", "schema_version: 2"),
        encoding="utf-8",
    )

    result = run_cli("scenario", "validate", str(invalid))

    assert result.returncode == 2
    payload = json.loads(result.stderr)
    assert payload["ok"] is False
    assert payload["operation"] == "scenario_validate"
    assert payload["error"] == "scenario_contract_error"
    assert "schema_version" in payload["message"]
    assert payload["token_omitted"] is True
