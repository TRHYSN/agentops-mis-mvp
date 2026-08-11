"""OpenCekura Windows prerequisite doctor with secret-safe reporting."""

from __future__ import annotations

import os
import shutil
import socket
import sqlite3
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from .process import ProcessResult, run_bounded


CommandRunner = Callable[..., ProcessResult]


@dataclass(frozen=True, slots=True)
class DoctorCheck:
    id: str
    status: str
    required: bool
    message: str


@dataclass(frozen=True, slots=True)
class DoctorReport:
    repo_root: str
    checks: tuple[DoctorCheck, ...]
    openai_api_key_status: str

    @property
    def ok(self) -> bool:
        return all(check.status == "PASS" for check in self.checks if check.required)


def _check(check_id: str, passed: bool, message: str, *, required: bool = True) -> DoctorCheck:
    return DoctorCheck(
        id=check_id,
        status="PASS" if passed else "FAIL",
        required=required,
        message=message,
    )


def _tool_path(name: str, environ: Mapping[str, str]) -> str | None:
    return shutil.which(name, path=environ.get("PATH"))


def _run(
    runner: CommandRunner,
    argv: Sequence[str],
    *,
    repo_root: Path,
) -> ProcessResult:
    return runner(argv, cwd=repo_root, timeout_seconds=5)


def _command_check(
    check_id: str,
    executable: str | None,
    arguments: Sequence[str],
    *,
    repo_root: Path,
    runner: CommandRunner,
) -> DoctorCheck:
    if not executable:
        return _check(check_id, False, f"{check_id} executable is missing")
    try:
        result = _run(runner, [executable, *arguments], repo_root=repo_root)
    except (OSError, ValueError) as exc:
        return _check(check_id, False, f"{check_id} could not run: {exc}")
    output = (result.stdout or result.stderr).strip().splitlines()
    detail = output[0] if output else f"exit {result.returncode}"
    return _check(
        check_id,
        result.returncode == 0 and not result.timed_out,
        detail[:300],
    )


def _git_fact(
    check_id: str,
    git_path: str | None,
    arguments: Sequence[str],
    *,
    repo_root: Path,
    runner: CommandRunner,
    predicate: Callable[[str, Path], bool],
    missing_message: str,
) -> DoctorCheck:
    if not git_path:
        return _check(check_id, False, "git executable is missing")
    try:
        result = _run(runner, [git_path, *arguments], repo_root=repo_root)
    except (OSError, ValueError) as exc:
        return _check(check_id, False, f"git check could not run: {exc}")
    value = result.stdout.strip()
    passed = result.returncode == 0 and not result.timed_out and predicate(value, repo_root)
    return _check(check_id, passed, value[:300] if value else missing_message)


def _write_access(repo_root: Path) -> DoctorCheck:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix=".open-cekura-doctor-",
            suffix=".tmp",
            dir=repo_root,
            delete=False,
        ) as handle:
            handle.write("write-check")
            handle.flush()
            os.fsync(handle.fileno())
            temporary_path = Path(handle.name)
        return _check("write_access", True, "repository root is writable")
    except OSError as exc:
        return _check("write_access", False, f"repository root is not writable: {exc}")
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass


def _sqlite_check() -> DoctorCheck:
    try:
        with sqlite3.connect(":memory:") as connection:
            value = connection.execute("SELECT sqlite_version()").fetchone()[0]
        return _check("sqlite", True, f"SQLite {value}")
    except sqlite3.Error as exc:
        return _check("sqlite", False, f"SQLite unavailable: {exc}")


def _localhost_bind_check() -> DoctorCheck:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        return _check("localhost_bind", True, f"127.0.0.1:{port} bind succeeded")
    except OSError as exc:
        return _check("localhost_bind", False, f"localhost bind failed: {exc}")


def run_doctor(
    *,
    repo_root: str | Path,
    environ: Mapping[str, str] | None = None,
    command_runner: CommandRunner = run_bounded,
) -> DoctorReport:
    """Measure all required local prerequisites without retaining secret values."""

    root = Path(repo_root).resolve()
    runtime_environment = dict(os.environ if environ is None else environ)
    git_path = _tool_path("git", runtime_environment)
    node_path = _tool_path("node", runtime_environment)
    npm_path = _tool_path("npm", runtime_environment)
    checks: list[DoctorCheck] = [
        _command_check(
            "python",
            sys.executable,
            ["--version"],
            repo_root=root,
            runner=command_runner,
        ),
        _command_check(
            "node",
            node_path,
            ["--version"],
            repo_root=root,
            runner=command_runner,
        ),
        _command_check(
            "npm",
            npm_path,
            ["--version"],
            repo_root=root,
            runner=command_runner,
        ),
        _command_check(
            "git",
            git_path,
            ["--version"],
            repo_root=root,
            runner=command_runner,
        ),
        _git_fact(
            "repo_root",
            git_path,
            ["rev-parse", "--show-toplevel"],
            repo_root=root,
            runner=command_runner,
            predicate=lambda value, expected: bool(value)
            and Path(value).resolve() == expected,
            missing_message="not inside the requested Git repository",
        ),
        _git_fact(
            "branch",
            git_path,
            ["branch", "--show-current"],
            repo_root=root,
            runner=command_runner,
            predicate=lambda value, _root: bool(value),
            missing_message="detached HEAD or branch unavailable",
        ),
        _git_fact(
            "commit",
            git_path,
            ["rev-parse", "HEAD"],
            repo_root=root,
            runner=command_runner,
            predicate=lambda value, _root: len(value) == 40
            and all(character in "0123456789abcdefABCDEF" for character in value),
            missing_message="commit unavailable",
        ),
        _git_fact(
            "dirty_state",
            git_path,
            ["status", "--porcelain"],
            repo_root=root,
            runner=command_runner,
            predicate=lambda value, _root: not value,
            missing_message="working tree is clean",
        ),
    ]
    checks.extend(
        [
            _write_access(root),
            _sqlite_check(),
            _localhost_bind_check(),
            _check(
                "ui_dependencies",
                (root / "ui" / "start-building-app" / "package.json").is_file()
                and (root / "ui" / "start-building-app" / "node_modules").is_dir(),
                "UI package and installed node_modules are present",
            ),
        ]
    )
    return DoctorReport(
        repo_root=str(root),
        checks=tuple(checks),
        openai_api_key_status=(
            "PRESENT" if bool(runtime_environment.get("OPENAI_API_KEY")) else "MISSING"
        ),
    )


def render_report(report: DoctorReport) -> str:
    lines = [f"OpenCekura Windows Doctor: {'PASS' if report.ok else 'FAIL'}"]
    lines.extend(
        f"[{check.status}] {check.id}: {check.message}"
        for check in report.checks
    )
    lines.append(f"OPENAI_API_KEY: {report.openai_api_key_status}")
    return "\n".join(lines)


def find_repo_root(start: Path) -> Path:
    for candidate in (start, *start.parents):
        if (candidate / ".git").exists():
            return candidate
    return start


def main() -> int:
    report = run_doctor(repo_root=find_repo_root(Path.cwd()))
    print(render_report(report))
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DoctorCheck",
    "DoctorReport",
    "find_repo_root",
    "render_report",
    "run_doctor",
]
