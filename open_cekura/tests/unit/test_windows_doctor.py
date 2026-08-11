from __future__ import annotations

import importlib
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from types import SimpleNamespace

import pytest


EXPECTED_CHECK_IDS = {
    "python",
    "node",
    "npm",
    "git",
    "repo_root",
    "branch",
    "commit",
    "dirty_state",
    "write_access",
    "sqlite",
    "localhost_bind",
    "ui_dependencies",
}


@pytest.fixture
def windows_doctor():
    return importlib.import_module("open_cekura.windows.doctor")


@pytest.fixture
def doctor_repo(tmp_path: Path) -> Path:
    repo_root = tmp_path / "检验 repo with spaces"
    (repo_root / ".git").mkdir(parents=True)
    ui_root = repo_root / "ui" / "start-building-app"
    (ui_root / "node_modules").mkdir(parents=True)
    (ui_root / "package.json").write_text("{}", encoding="utf-8")
    (ui_root / "package-lock.json").write_text("{}", encoding="utf-8")
    return repo_root


def _result(
    argv: Sequence[str],
    *,
    stdout: str = "",
    stderr: str = "",
    returncode: int = 0,
) -> SimpleNamespace:
    return SimpleNamespace(
        argv=tuple(argv),
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        timed_out=False,
        duration_ms=1,
    )


def _command_runner(
    repo_root: Path,
    *,
    dirty_output: str = "",
) -> Callable[..., SimpleNamespace]:
    outputs: Mapping[tuple[str, ...], str] = {
        ("git", "--version"): "git version 2.50.0.windows.1\n",
        ("git", "rev-parse", "--show-toplevel"): f"{repo_root}\n",
        ("git", "branch", "--show-current"): "feat/open-cekura-windows-v0\n",
        ("git", "rev-parse", "--abbrev-ref", "HEAD"): "feat/open-cekura-windows-v0\n",
        ("git", "symbolic-ref", "--short", "HEAD"): "feat/open-cekura-windows-v0\n",
        ("git", "rev-parse", "HEAD"): f"{'a' * 40}\n",
        ("git", "status", "--porcelain"): dirty_output,
        ("git", "status", "--porcelain=v1"): dirty_output,
        ("git", "status", "--short"): dirty_output,
        ("node", "--version"): "v20.19.0\n",
        ("npm", "--version"): "10.8.2\n",
        (sys.executable, "--version"): f"Python {sys.version_info.major}.{sys.version_info.minor}\n",
    }

    def run(
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        timeout_seconds: float = 5,
    ) -> SimpleNamespace:
        del timeout_seconds
        command = tuple(str(part) for part in argv)
        assert cwd is None or Path(cwd) == repo_root
        if command in outputs:
            return _result(command, stdout=outputs[command])

        executable = Path(command[0]).stem.lower()
        normalized = (executable, *command[1:])
        if normalized not in outputs:
            raise AssertionError(f"unexpected doctor command: {command!r}")
        return _result(command, stdout=outputs[normalized])

    return run


def _environment_with_tools(
    monkeypatch: pytest.MonkeyPatch,
    repo_root: Path,
    *,
    missing: frozenset[str] = frozenset(),
    api_key: str | None = None,
) -> dict[str, str]:
    tool_root = repo_root / "fake tools"
    tool_root.mkdir(exist_ok=True)
    for tool in {"git", "node", "npm"} - missing:
        for suffix in ("", ".exe", ".cmd", ".bat"):
            executable = tool_root / f"{tool}{suffix}"
            executable.write_text("stub", encoding="utf-8")
            executable.chmod(0o755)

    monkeypatch.setenv("PATH", str(tool_root))
    monkeypatch.setenv("PATHEXT", ".COM;.EXE;.BAT;.CMD")
    environ = dict(os.environ)
    if api_key is not None:
        environ["OPENAI_API_KEY"] = api_key
    else:
        environ.pop("OPENAI_API_KEY", None)
    return environ


def _checks_by_id(report) -> dict[str, object]:
    return {check.id: check for check in report.checks}


def test_run_doctor_checks_the_complete_windows_prerequisite_contract(
    doctor_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    windows_doctor,
) -> None:
    environ = _environment_with_tools(
        monkeypatch,
        doctor_repo,
        api_key="sk" + "-test-secret",
    )

    report = windows_doctor.run_doctor(
        repo_root=doctor_repo,
        environ=environ,
        command_runner=_command_runner(doctor_repo),
    )

    checks = _checks_by_id(report)
    assert EXPECTED_CHECK_IDS <= checks.keys()
    for check in checks.values():
        assert isinstance(check.id, str) and check.id
        assert isinstance(check.status, str) and check.status
        assert isinstance(check.required, bool)
        assert isinstance(check.message, str) and check.message
    assert all(check.status == "PASS" for check in checks.values() if check.required)


def test_run_doctor_fails_required_node_and_npm_checks_when_tools_are_missing(
    doctor_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    windows_doctor,
) -> None:
    environ = _environment_with_tools(
        monkeypatch,
        doctor_repo,
        missing=frozenset({"node", "npm"}),
    )

    report = windows_doctor.run_doctor(
        repo_root=doctor_repo,
        environ=environ,
        command_runner=_command_runner(doctor_repo),
    )

    checks = _checks_by_id(report)
    for check_id in ("node", "npm"):
        assert checks[check_id].required is True
        assert checks[check_id].status == "FAIL"


def test_run_doctor_refreshes_registered_windows_path_when_environment_is_implicit(
    doctor_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    windows_doctor,
) -> None:
    registered_tools = doctor_repo / "registered tools"
    registered_tools.mkdir()
    for tool in ("git", "node", "npm"):
        for suffix in ("", ".exe", ".cmd", ".bat"):
            executable = registered_tools / f"{tool}{suffix}"
            executable.write_text("stub", encoding="utf-8")
            executable.chmod(0o755)
    stale_path = doctor_repo / "stale process path"
    stale_path.mkdir()
    monkeypatch.setenv("PATH", str(stale_path))
    monkeypatch.setenv("PATHEXT", ".COM;.EXE;.BAT;.CMD")
    monkeypatch.setattr(
        windows_doctor,
        "_registered_windows_path_values",
        lambda: (str(registered_tools),),
        raising=False,
    )

    report = windows_doctor.run_doctor(
        repo_root=doctor_repo,
        command_runner=_command_runner(doctor_repo),
    )

    checks = _checks_by_id(report)
    assert checks["node"].status == "PASS"
    assert checks["npm"].status == "PASS"
    assert checks["git"].status == "PASS"


def test_run_doctor_reports_a_dirty_worktree_as_a_required_failure(
    doctor_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    windows_doctor,
) -> None:
    environ = _environment_with_tools(monkeypatch, doctor_repo)

    report = windows_doctor.run_doctor(
        repo_root=doctor_repo,
        environ=environ,
        command_runner=_command_runner(doctor_repo, dirty_output=" M tracked.py\n"),
    )

    dirty_check = _checks_by_id(report)["dirty_state"]
    assert dirty_check.required is True
    assert dirty_check.status == "FAIL"


def test_run_doctor_fails_when_ui_dependencies_are_missing(
    doctor_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    windows_doctor,
) -> None:
    (doctor_repo / "ui" / "start-building-app" / "node_modules").rmdir()
    environ = _environment_with_tools(monkeypatch, doctor_repo)

    report = windows_doctor.run_doctor(
        repo_root=doctor_repo,
        environ=environ,
        command_runner=_command_runner(doctor_repo),
    )

    ui_check = _checks_by_id(report)["ui_dependencies"]
    assert ui_check.required is True
    assert ui_check.status == "FAIL"


@pytest.mark.parametrize(
    ("environ", "expected_status"),
    [
        ({"OPENAI_API_KEY": "sk" + "-test-secret-that-must-not-leak"}, "PRESENT"),
        ({}, "MISSING"),
    ],
)
def test_render_report_only_discloses_api_key_presence(
    doctor_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    windows_doctor,
    environ: dict[str, str],
    expected_status: str,
) -> None:
    secret = environ.get("OPENAI_API_KEY")
    runtime_environ = _environment_with_tools(
        monkeypatch,
        doctor_repo,
        api_key=secret,
    )

    report = windows_doctor.run_doctor(
        repo_root=doctor_repo,
        environ=runtime_environ,
        command_runner=_command_runner(doctor_repo),
    )
    rendered = windows_doctor.render_report(report)

    assert f"OPENAI_API_KEY: {expected_status}" in rendered
    if secret is not None:
        assert secret not in rendered
        assert secret not in repr(report)
