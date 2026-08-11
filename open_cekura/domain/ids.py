"""Stable OpenCekura identifier construction."""

from __future__ import annotations

import hashlib
import json
import re


_PREFIX_PATTERN = re.compile(r"^[a-z][a-z0-9]{1,23}$")
_DIGEST_LENGTH = 24
_ALGORITHM_VERSION = "open-cekura.stable-id.v1"


def stable_id(prefix: str, *parts: str) -> str:
    """Return a deterministic, type-prefixed ID for length-delimited text parts."""

    if not isinstance(prefix, str) or not _PREFIX_PATTERN.fullmatch(prefix):
        raise ValueError("prefix must be 2-24 lowercase ASCII letters or digits")
    if not parts:
        raise ValueError("at least one stable ID part is required")
    if any(not isinstance(part, str) or not part for part in parts):
        raise ValueError("stable ID parts must be non-empty strings")

    canonical_parts = json.dumps(
        list(parts),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(
        _ALGORITHM_VERSION.encode("ascii") + b"\x00" + canonical_parts
    ).hexdigest()[:_DIGEST_LENGTH]
    return f"{prefix}_{digest}"


__all__ = ["stable_id"]
