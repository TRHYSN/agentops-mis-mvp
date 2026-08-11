from __future__ import annotations

import importlib
import sys
import time
from pathlib import Path

import pytest


@pytest.fixture
def windows_process():
    return importlib.import_module("open_cekura.windows.process")


def test_run_bounded_returns_typed_result_and_captures_output(
    tmp_path: Path,
    windows_process,
) -> None:
    argv = [
        sys.executable,
        "-B",
        "-c",
        "import sys; print(sys.argv[1]); print('diagnostic', file=sys.stderr)",
        "你好 from a path with spaces",
    ]

    result = windows_process.run_bounded(argv, cwd=tmp_path, timeout_seconds=5)

    assert not isinstance(result, dict)
    assert list(result.argv) == argv
    assert result.returncode == 0
    assert result.stdout.strip() == "你好 from a path with spaces"
    assert result.stderr.strip() == "diagnostic"
    assert result.timed_out is False
    assert isinstance(result.duration_ms, int)
    assert result.duration_ms >= 0


def test_run_bounded_passes_shell_metacharacters_as_a_literal_argument(
    tmp_path: Path,
    windows_process,
) -> None:
    marker = tmp_path / "shell should not create this.txt"
    literal = f'hello & echo unsafe > "{marker}"'
    argv = [
        sys.executable,
        "-B",
        "-c",
        "import sys; print(sys.argv[1])",
        literal,
    ]

    result = windows_process.run_bounded(argv, cwd=tmp_path, timeout_seconds=5)

    assert result.returncode == 0
    assert result.stdout.strip() == literal
    assert result.timed_out is False
    assert not marker.exists()


def test_run_bounded_prefers_valid_utf8_emitted_by_cross_platform_tools(
    tmp_path: Path,
    windows_process,
) -> None:
    value = "文档/reliability"
    code = "import sys; sys.stdout.buffer.write(sys.argv[1].encode('utf-8'))"

    result = windows_process.run_bounded(
        [sys.executable, "-B", "-c", code, value],
        cwd=tmp_path,
        timeout_seconds=5,
    )

    assert result.returncode == 0
    assert result.stdout == value


def test_run_bounded_times_out_and_terminates_the_process_tree(
    tmp_path: Path,
    windows_process,
) -> None:
    marker = tmp_path / "orphan process marker.txt"
    grandchild_code = (
        "import pathlib, sys, time; "
        "time.sleep(1.0); "
        "pathlib.Path(sys.argv[1]).write_text('orphan', encoding='utf-8')"
    )
    parent_code = (
        "import subprocess, sys, time; "
        "subprocess.Popen([sys.executable, '-B', '-c', sys.argv[1], sys.argv[2]]); "
        "print('spawned', flush=True); "
        "time.sleep(10)"
    )
    argv = [
        sys.executable,
        "-B",
        "-c",
        parent_code,
        grandchild_code,
        str(marker),
    ]

    result = windows_process.run_bounded(argv, cwd=tmp_path, timeout_seconds=0.2)

    assert not isinstance(result, dict)
    assert list(result.argv) == argv
    assert result.timed_out is True
    assert result.returncode is not None
    assert result.returncode != 0
    assert "spawned" in result.stdout
    assert result.duration_ms < 3_000

    time.sleep(1.3)
    assert not marker.exists(), "a timed-out grandchild survived the bounded process"
