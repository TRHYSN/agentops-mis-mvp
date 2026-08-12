from __future__ import annotations

import json
import hashlib
from pathlib import Path

import pytest

from scripts import open_cekura_distribution_audit as audit


WHEEL_NAME = "agentops_mis_cli-0.1.0-py3-none-any.whl"
SDIST_NAME = "agentops_mis_cli-0.1.0.tar.gz"
EXPECTED_COMMIT = "a" * 40


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _record(
    root: Path,
    os_name: str,
    python: str,
    *,
    wheel: bytes = b"portable-wheel",
    sdist: bytes = b"portable-sdist",
) -> Path:
    path = root / f"{os_name}-{python}" / f"distribution-digest-{os_name}-py{python}.json"
    path.parent.mkdir(parents=True)
    archives = path.parent / "open-cekura-dist"
    archives.mkdir()
    (archives / WHEEL_NAME).write_bytes(wheel)
    (archives / SDIST_NAME).write_bytes(sdist)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source_commit_sha": EXPECTED_COMMIT,
                "os": os_name,
                "python_version": python,
                "artifacts": {
                    WHEEL_NAME: _sha256(wheel),
                    SDIST_NAME: _sha256(sdist),
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def test_verify_accepts_one_exact_cross_platform_distribution(tmp_path: Path) -> None:
    for os_name in ("Linux", "Windows"):
        for python in ("3.10", "3.11"):
            _record(tmp_path, os_name, python)

    assert audit.verify(tmp_path, expected_commit=EXPECTED_COMMIT) == 0


def test_verify_rejects_one_platform_digest_drift(tmp_path: Path) -> None:
    for os_name in ("Linux", "Windows"):
        for python in ("3.10", "3.11"):
            wheel = (
                b"drifted-wheel"
                if (os_name, python) == ("Windows", "3.11")
                else b"portable-wheel"
            )
            _record(tmp_path, os_name, python, wheel=wheel)

    try:
        audit.verify(tmp_path, expected_commit=EXPECTED_COMMIT)
    except ValueError as exc:
        assert "cross-platform distribution mismatch" in str(exc)
    else:
        raise AssertionError("digest drift was accepted")


def test_verify_rejects_a_missing_downloaded_archive(tmp_path: Path) -> None:
    records = [
        _record(tmp_path, os_name, python)
        for os_name in ("Linux", "Windows")
        for python in ("3.10", "3.11")
    ]
    (records[0].parent / "open-cekura-dist" / WHEEL_NAME).unlink()

    with pytest.raises(ValueError, match="downloaded artifact missing"):
        audit.verify(tmp_path, expected_commit=EXPECTED_COMMIT)


def test_verify_rejects_a_replaced_downloaded_archive(tmp_path: Path) -> None:
    records = [
        _record(tmp_path, os_name, python)
        for os_name in ("Linux", "Windows")
        for python in ("3.10", "3.11")
    ]
    (records[0].parent / "open-cekura-dist" / SDIST_NAME).write_bytes(
        b"replacement"
    )

    with pytest.raises(ValueError, match="downloaded artifact digest mismatch"):
        audit.verify(tmp_path, expected_commit=EXPECTED_COMMIT)


def test_verify_rejects_records_from_an_unexpected_commit(tmp_path: Path) -> None:
    for os_name in ("Linux", "Windows"):
        for python in ("3.10", "3.11"):
            _record(tmp_path, os_name, python)

    with pytest.raises(ValueError, match="expected source commit"):
        audit.verify(tmp_path, expected_commit="b" * 40)
