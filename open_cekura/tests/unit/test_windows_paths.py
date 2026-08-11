from __future__ import annotations

import importlib
from pathlib import Path, PureWindowsPath

import pytest


@pytest.fixture
def windows_paths():
    return importlib.import_module("open_cekura.windows.paths")


def test_resolve_within_preserves_unicode_and_spaces(tmp_path: Path, windows_paths) -> None:
    root = tmp_path / "工作 区"
    root.mkdir()
    candidate = Path("报告 数据") / "运行 结果.json"

    resolved = windows_paths.resolve_within(root, candidate)

    assert isinstance(resolved, Path)
    assert resolved == (root / candidate).resolve()
    assert resolved.relative_to(root.resolve()) == candidate


@pytest.mark.parametrize(
    "candidate",
    [
        Path("..") / "escape.json",
        Path("safe") / ".." / ".." / "escape.json",
    ],
)
def test_resolve_within_rejects_parent_traversal(
    tmp_path: Path,
    windows_paths,
    candidate: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()

    with pytest.raises(ValueError):
        windows_paths.resolve_within(root, candidate)


def test_resolve_within_rejects_an_absolute_path_outside_root(
    tmp_path: Path,
    windows_paths,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside" / "result.json"

    with pytest.raises(ValueError):
        windows_paths.resolve_within(root, outside)


def test_resolve_within_rejects_a_link_that_escapes_root(
    tmp_path: Path,
    windows_paths,
) -> None:
    root = tmp_path / "repo"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    link = root / "linked outside"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"directory links are unavailable in this environment: {error}")

    with pytest.raises(ValueError):
        windows_paths.resolve_within(root, Path("linked outside") / "result.json")


def test_validate_windows_relative_path_accepts_unicode_and_spaces(windows_paths) -> None:
    value = r"reports\客户 data\result file.json"

    validated = windows_paths.validate_windows_relative_path(value)

    validated_path = PureWindowsPath(validated)
    assert validated_path == PureWindowsPath(value)
    assert not validated_path.is_absolute()


@pytest.mark.parametrize(
    "value",
    [
        r"C:\outside\result.json",
        r"C:drive-relative\result.json",
        r"\\server\share\result.json",
        r"\\?\C:\outside\result.json",
        r"\\.\PIPE\open-cekura",
    ],
)
def test_validate_windows_relative_path_rejects_drive_unc_and_device_paths(
    windows_paths,
    value: str,
) -> None:
    with pytest.raises(ValueError):
        windows_paths.validate_windows_relative_path(value)


@pytest.mark.parametrize(
    "value",
    [
        "CON",
        "nul.txt",
        r"reports\AUX.json",
        r"reports\COM1\result.json",
        "LPT9.log",
    ],
)
def test_validate_windows_relative_path_rejects_reserved_names(
    windows_paths,
    value: str,
) -> None:
    with pytest.raises(ValueError):
        windows_paths.validate_windows_relative_path(value)


def test_validate_windows_relative_path_rejects_windows_parent_traversal(
    windows_paths,
) -> None:
    with pytest.raises(ValueError):
        windows_paths.validate_windows_relative_path(r"reports\..\..\escape.json")
