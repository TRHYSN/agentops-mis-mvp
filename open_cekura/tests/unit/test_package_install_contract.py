"""Distribution contract for the installable Reliability Lab runtime."""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
import tarfile
import zipfile
from email.parser import Parser
from pathlib import Path

from agentops_mis_cli import _build_backend as backend


COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")


def _extract_sdist(archive_path: Path, destination: Path) -> None:
    """Extract the generated sdist without relying on version-specific filters."""

    with tarfile.open(archive_path, "r:gz") as archive:
        for member in archive.getmembers():
            relative = Path(member.name)
            assert not relative.is_absolute()
            assert ".." not in relative.parts
            assert member.isfile()
            source = archive.extractfile(member)
            assert source is not None
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read())


def test_wheel_contains_reliability_runtime_authority_and_demo_resources(
    tmp_path: Path,
) -> None:
    wheel_name = backend.build_wheel(str(tmp_path))

    with zipfile.ZipFile(tmp_path / wheel_name) as archive:
        names = set(archive.namelist())
        build_commit = archive.read("open_cekura/_build_commit.txt").decode("ascii").strip()

    assert "open_cekura/__init__.py" in names
    assert "open_cekura/campaigns/service.py" in names
    assert "open_cekura/evidence/manifest.py" in names
    assert "agentops_mis_runtime/__init__.py" in names
    assert "server.py" in names
    assert (
        "open_cekura/contracts/RELIABILITY_CAMPAIGN_EXECUTION.md" in names
    )
    assert "open_cekura/resources/scenarios/basic.yaml" in names
    assert "open_cekura/resources/appointment-agent/baseline.json" in names
    assert "open_cekura/resources/appointment-agent/candidate.json" in names
    assert not any(name.startswith("open_cekura/tests/") for name in names)
    assert COMMIT_SHA.fullmatch(build_commit)


def test_sdist_contains_reliability_sources_public_examples_and_build_commit(
    tmp_path: Path,
) -> None:
    sdist_name = backend.build_sdist(str(tmp_path))

    with tarfile.open(tmp_path / sdist_name, "r:gz") as archive:
        names = {member.name for member in archive.getmembers()}
        prefix = f"{backend.DIST}-{backend.VERSION}/"
        commit_member = archive.extractfile(f"{prefix}open_cekura/_build_commit.txt")
        assert commit_member is not None
        build_commit = commit_member.read().decode("ascii").strip()

    assert f"{prefix}open_cekura/campaigns/service.py" in names
    assert f"{prefix}agentops_mis_runtime/__init__.py" in names
    assert f"{prefix}server.py" in names
    assert (
        f"{prefix}open_cekura/contracts/RELIABILITY_CAMPAIGN_EXECUTION.md" in names
    )
    assert f"{prefix}examples/open-cekura/scenarios/basic.yaml" in names
    assert f"{prefix}examples/open-cekura/appointment-agent/baseline.json" in names
    assert not any(f"{prefix}open_cekura/tests/" in name for name in names)
    assert COMMIT_SHA.fullmatch(build_commit)


def test_reliability_dependencies_are_declared_as_an_optional_extra() -> None:
    metadata = Parser().parsestr(backend._metadata())

    assert metadata.get_all("Provides-Extra") == ["reliability"]
    requirements = metadata.get_all("Requires-Dist") or []
    assert any("pydantic" in requirement and "extra == \"reliability\"" in requirement for requirement in requirements)
    assert any("PyYAML" in requirement and "extra == \"reliability\"" in requirement for requirement in requirements)
    assert not any("pytest" in requirement for requirement in requirements)


def test_prepared_metadata_bytes_equal_direct_wheel_metadata(tmp_path: Path) -> None:
    dist_info_name = backend.prepare_metadata_for_build_wheel(str(tmp_path))
    dist_info = tmp_path / dist_info_name

    expected = dict(backend._default_metadata_files())
    prepared = dict(backend._prepared_metadata_files(str(tmp_path)))

    assert prepared == expected
    assert (dist_info / "METADATA").read_bytes() == backend._metadata().encode("utf-8")
    assert (dist_info / "WHEEL").read_bytes() == backend._wheel().encode("utf-8")
    assert (dist_info / "entry_points.txt").read_bytes() == backend._entry_points().encode("utf-8")


def test_direct_wheel_build_is_byte_reproducible(tmp_path: Path) -> None:
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first_dir.mkdir()
    second_dir.mkdir()

    first = first_dir / backend.build_wheel(str(first_dir))
    second = second_dir / backend.build_wheel(str(second_dir))

    assert hashlib.sha256(first.read_bytes()).digest() == hashlib.sha256(second.read_bytes()).digest()


def test_distribution_text_bytes_are_host_newline_independent(tmp_path: Path) -> None:
    lf = tmp_path / "lf.md"
    crlf = tmp_path / "crlf.md"
    lf.write_bytes(b"contract\nline\n")
    crlf.write_bytes(b"contract\r\nline\r\n")

    assert backend._portable_source_bytes(lf) == backend._portable_source_bytes(crlf)


def test_wheel_built_from_sdist_matches_direct_backend_bytes(tmp_path: Path) -> None:
    direct_dir = tmp_path / "direct"
    sdist_dir = tmp_path / "sdist"
    extracted_dir = tmp_path / "extracted"
    pep517_dir = tmp_path / "pep517"
    for directory in (direct_dir, sdist_dir, extracted_dir, pep517_dir):
        directory.mkdir()
    direct = direct_dir / backend.build_wheel(str(direct_dir))
    sdist = sdist_dir / backend.build_sdist(str(sdist_dir))
    _extract_sdist(sdist, extracted_dir)
    source = extracted_dir / f"{backend.DIST}-{backend.VERSION}"

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            str(pep517_dir),
            str(source),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert completed.returncode == 0, completed.stderr
    rebuilt = next(pep517_dir.glob("*.whl"))
    assert hashlib.sha256(direct.read_bytes()).digest() == hashlib.sha256(
        rebuilt.read_bytes()
    ).digest()
