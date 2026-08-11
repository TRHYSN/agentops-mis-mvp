"""Fail-closed path helpers that preserve Windows and Unicode semantics."""

from __future__ import annotations

import re
from pathlib import Path, PureWindowsPath


_RESERVED_WINDOWS_NAME = re.compile(
    r"^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])$",
    re.IGNORECASE,
)
_INVALID_WINDOWS_CHARACTERS = frozenset('<>:"|?*')


def resolve_within(root: str | Path, candidate: str | Path) -> Path:
    """Resolve a path and reject lexical or symlink escapes from ``root``."""

    root_path = Path(root).resolve()
    candidate_path = Path(candidate)
    resolved = (
        candidate_path.resolve()
        if candidate_path.is_absolute()
        else (root_path / candidate_path).resolve()
    )
    try:
        resolved.relative_to(root_path)
    except ValueError as exc:
        raise ValueError(f"path escapes the allowed root: {candidate}") from exc
    return resolved


def validate_windows_relative_path(value: str) -> str:
    """Validate an untrusted Windows path without consulting the host OS."""

    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError("Windows relative path must be a non-empty string")
    path = PureWindowsPath(value)
    if path.drive or path.root or path.is_absolute():
        raise ValueError("Windows path must be relative and must not contain a drive or share")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("Windows path must not contain empty, current, or parent segments")

    for part in path.parts:
        if part.endswith((" ", ".")):
            raise ValueError("Windows path segments must not end in a space or dot")
        if any(character in _INVALID_WINDOWS_CHARACTERS for character in part):
            raise ValueError("Windows path contains an unsupported character")
        if any(ord(character) < 32 for character in part):
            raise ValueError("Windows path contains a control character")
        stem = part.split(".", 1)[0]
        if _RESERVED_WINDOWS_NAME.fullmatch(stem):
            raise ValueError(f"Windows path uses a reserved device name: {part}")
    return str(path)


__all__ = ["resolve_within", "validate_windows_relative_path"]
