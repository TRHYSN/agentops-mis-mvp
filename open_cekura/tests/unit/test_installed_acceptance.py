from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from scripts import open_cekura_installed_acceptance as acceptance


def _report(tmp_path: Path, *, commit: str = "a" * 40) -> dict[str, object]:
    site_packages = tmp_path / "venv" / "Lib" / "site-packages"
    return {
        "schema_version": 1,
        "build_commit": commit,
        "site_roots": [str(site_packages)],
        "modules": {
            "open_cekura": str(site_packages / "open_cekura" / "__init__.py"),
            "agentops_mis_runtime": str(
                site_packages / "agentops_mis_runtime" / "__init__.py"
            ),
            "server": str(site_packages / "server.py"),
        },
        "baseline_idempotent": True,
        "candidate_idempotent": True,
        "comparison_idempotent": True,
        "replay_idempotent": True,
        "api_campaigns_visible": True,
    }


def test_probe_command_uses_isolated_mode() -> None:
    command = acceptance._probe_command("a" * 40)

    assert command[:3] == [sys.executable, "-I", "-c"]
    assert "a" * 40 in command[3]


def test_generated_probe_is_valid_python() -> None:
    compile(acceptance._probe_command(None)[3], "<installed-probe>", "exec")


def test_probe_starts_installed_server_in_isolated_mode() -> None:
    probe = acceptance._probe_command(None)[3]

    assert '[sys.executable, "-I", "-m", "server"' in probe
    assert 'cwd=state_root' in probe
    assert "/api/reliability/campaigns?workspace_id=installed-audit" in probe


def test_report_requires_campaigns_visible_through_installed_server(
    tmp_path: Path,
) -> None:
    report = _report(tmp_path)
    report["api_campaigns_visible"] = False

    with pytest.raises(ValueError, match="api_campaigns_visible"):
        acceptance._validate_report(
            report,
            checkout=tmp_path / "checkout",
            expected_commit=None,
        )


@pytest.mark.parametrize(
    "module_name",
    ["open_cekura", "agentops_mis_runtime", "server"],
)
def test_report_rejects_module_loaded_from_checkout(
    tmp_path: Path,
    module_name: str,
) -> None:
    checkout = tmp_path / "checkout"
    report = _report(tmp_path)
    modules = report["modules"]
    assert isinstance(modules, dict)
    modules[module_name] = str(checkout / module_name / "__init__.py")

    with pytest.raises(ValueError, match=f"{module_name}.*checkout"):
        acceptance._validate_report(report, checkout=checkout, expected_commit=None)


def test_report_rejects_module_outside_site_packages(tmp_path: Path) -> None:
    report = _report(tmp_path)
    modules = report["modules"]
    assert isinstance(modules, dict)
    modules["server"] = str(tmp_path / "elsewhere" / "server.py")

    with pytest.raises(ValueError, match="server.*site-packages"):
        acceptance._validate_report(
            report,
            checkout=tmp_path / "checkout",
            expected_commit=None,
        )


def test_report_rejects_broad_environment_root_as_site_packages(
    tmp_path: Path,
) -> None:
    environment_root = tmp_path / "venv"
    report = _report(tmp_path)
    report["site_roots"] = [str(environment_root)]
    modules = report["modules"]
    assert isinstance(modules, dict)
    modules["open_cekura"] = str(environment_root / "open_cekura" / "__init__.py")
    modules["agentops_mis_runtime"] = str(
        environment_root / "agentops_mis_runtime" / "__init__.py"
    )
    modules["server"] = str(environment_root / "server.py")

    with pytest.raises(ValueError, match="invalid site-packages root"):
        acceptance._validate_report(
            report,
            checkout=tmp_path / "checkout",
            expected_commit=None,
        )


@pytest.mark.parametrize("commit", ["", "abc", "A" * 40, "g" * 40, "a" * 39])
def test_report_rejects_invalid_packaged_build_commit(
    tmp_path: Path,
    commit: str,
) -> None:
    with pytest.raises(ValueError, match="40-character lowercase Git SHA"):
        acceptance._validate_report(
            _report(tmp_path, commit=commit),
            checkout=tmp_path / "checkout",
            expected_commit=None,
        )


def test_report_rejects_expected_commit_mismatch(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="expected commit"):
        acceptance._validate_report(
            _report(tmp_path, commit="a" * 40),
            checkout=tmp_path / "checkout",
            expected_commit="b" * 40,
        )


def test_main_removes_pythonpath_and_parses_probe_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = _report(tmp_path)
    seen: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        seen["command"] = command
        seen["kwargs"] = kwargs
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=f"{acceptance.REPORT_MARKER}{json.dumps(report)}\n",
            stderr="",
        )

    monkeypatch.setenv("PYTHONPATH", "must-not-leak")
    monkeypatch.setattr(acceptance.subprocess, "run", fake_run)
    monkeypatch.setattr(acceptance, "_checkout_root", lambda: tmp_path / "checkout")

    assert acceptance.main(["--expected-commit", "a" * 40]) == 0
    command = seen["command"]
    assert isinstance(command, list)
    assert command[:3] == [sys.executable, "-I", "-c"]
    kwargs = seen["kwargs"]
    assert isinstance(kwargs, dict)
    environment = kwargs["env"]
    assert isinstance(environment, dict)
    assert "PYTHONPATH" not in environment
