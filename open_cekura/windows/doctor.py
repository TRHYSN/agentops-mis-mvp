"""OpenCekura Windows prerequisite doctor with secret-safe reporting."""

from __future__ import annotations

import os
import re
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
_COMMIT_SHA = re.compile(r"[0-9a-f]{40}\Z")
_SAFE_BRANCH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,254}\Z")


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


def _registered_windows_path_values() -> tuple[str, ...]:
    """Read current User/Machine PATH values without launching a shell.

    A long-running Windows process does not observe PATH changes made by an
    installer after that process started.  The registry is the authoritative
    source for new terminals, so the doctor refreshes those two non-secret
    values when it owns environment discovery.
    """

    if os.name != "nt":
        return ()
    try:
        import winreg
    except ImportError:
        return ()
    locations = (
        (winreg.HKEY_CURRENT_USER, r"Environment"),
        (
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
        ),
    )
    values: list[str] = []
    for hive, key_name in locations:
        try:
            with winreg.OpenKey(hive, key_name) as key:
                raw, _kind = winreg.QueryValueEx(key, "Path")
        except OSError:
            continue
        if isinstance(raw, str) and raw:
            values.append(winreg.ExpandEnvironmentStrings(raw))
    return tuple(values)


def _merged_path(*values: str) -> str:
    entries: list[str] = []
    seen: set[str] = set()
    for value in values:
        for raw_entry in value.split(os.pathsep):
            entry = raw_entry.strip().strip('"')
            if not entry:
                continue
            identity = os.path.normcase(os.path.normpath(entry))
            if identity in seen:
                continue
            seen.add(identity)
            entries.append(entry)
    return os.pathsep.join(entries)


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


def _branch_check(
    git_path: str | None,
    *,
    repo_root: Path,
    runner: CommandRunner,
    environ: Mapping[str, str],
) -> DoctorCheck:
    """Accept a branch, or an exact event-SHA-bound GitHub detached checkout."""

    if not git_path:
        return _check("branch", False, "git executable is missing")
    try:
        branch_result = _run(
            runner,
            [git_path, "branch", "--show-current"],
            repo_root=repo_root,
        )
    except (OSError, ValueError) as exc:
        return _check("branch", False, f"git check could not run: {exc}")
    branch = branch_result.stdout.strip()
    if branch_result.returncode == 0 and not branch_result.timed_out and branch:
        return _check("branch", True, branch[:300])

    expected_commit = environ.get("GITHUB_SHA", "").strip()
    event_branch = (
        environ.get("GITHUB_HEAD_REF", "").strip()
        or environ.get("GITHUB_REF_NAME", "").strip()
    )
    safe_event_branch = (
        _SAFE_BRANCH.fullmatch(event_branch) is not None
        and ".." not in event_branch
        and "//" not in event_branch
        and not event_branch.endswith((".", "/"))
    )
    if not (
        branch_result.returncode == 0
        and not branch_result.timed_out
        and environ.get("GITHUB_ACTIONS") == "true"
        and _COMMIT_SHA.fullmatch(expected_commit) is not None
        and safe_event_branch
    ):
        return _check("branch", False, "detached HEAD or branch unavailable")
    try:
        head_result = _run(
            runner,
            [git_path, "rev-parse", "HEAD"],
            repo_root=repo_root,
        )
    except (OSError, ValueError) as exc:
        return _check("branch", False, f"git check could not run: {exc}")
    actual_commit = head_result.stdout.strip().lower()
    if (
        head_result.returncode != 0
        or head_result.timed_out
        or actual_commit != expected_commit
    ):
        return _check(
            "branch",
            False,
            "detached GitHub Actions checkout does not match GITHUB_SHA",
        )
    return _check(
        "branch",
        True,
        f"{event_branch} (detached at exact GitHub Actions event SHA)",
    )


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
    if environ is None:
        runtime_environment["PATH"] = _merged_path(
            runtime_environment.get("PATH", ""),
            *_registered_windows_path_values(),
        )
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
        _branch_check(
            git_path,
            repo_root=root,
            runner=command_runner,
            environ=runtime_environment,
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
