from __future__ import annotations

import os
import zipfile
from pathlib import Path

from agentops_mis_cli import _build_backend as backend
from scripts import build_relay_release as relay_release


COMMIT = "1" * 40


def test_relay_snapshot_isolated_from_full_distribution_inputs() -> None:
    release_inputs = set(relay_release.RELEASE_INPUTS)
    assert {
        "agentops_mis_cli",
        "agentops_mis_core",
        "packaging/relay/config.example.json",
        "packaging/relay/systemd/agentops-mis-relay.service",
        "pyproject.toml",
        "scripts/build_relay_release.py",
    }.issubset(release_inputs)
    assert {
        "agentops_mis_runtime",
        "open_cekura",
        "examples/open-cekura",
        "server.py",
    }.isdisjoint(release_inputs)


def test_backend_build_receives_exact_commit_and_restores_environment(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("AGENTOPS_BUILD_COMMIT_SHA", raising=False)
    observed: list[str | None] = []

    class Backend:
        @staticmethod
        def build_wheel(directory: str, *, config_settings: object) -> str:
            assert Path(directory) == tmp_path
            assert config_settings == {
                backend.DISTRIBUTION_CONFIG_KEY: backend.RELAY_DISTRIBUTION
            }
            observed.append(os.environ.get("AGENTOPS_BUILD_COMMIT_SHA"))
            return "agentops_mis_cli-0.1.0-py3-none-any.whl"

    name = relay_release.build_backend_wheel(
        tmp_path,
        Backend(),
        source_commit=COMMIT,
    )

    assert name == "agentops_mis_cli-0.1.0-py3-none-any.whl"
    assert observed == [COMMIT]
    assert "AGENTOPS_BUILD_COMMIT_SHA" not in os.environ


def test_backend_build_restores_an_existing_commit_environment(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("AGENTOPS_BUILD_COMMIT_SHA", "2" * 40)

    class Backend:
        @staticmethod
        def build_wheel(directory: str, *, config_settings: object) -> str:
            assert config_settings == {
                backend.DISTRIBUTION_CONFIG_KEY: backend.RELAY_DISTRIBUTION
            }
            assert os.environ["AGENTOPS_BUILD_COMMIT_SHA"] == COMMIT
            raise RuntimeError("injected build failure")

    try:
        relay_release.build_backend_wheel(
            tmp_path,
            Backend(),
            source_commit=COMMIT,
        )
    except RuntimeError as exc:
        assert str(exc) == "injected build failure"
    else:  # pragma: no cover - the fake backend must fail
        raise AssertionError("fake backend unexpectedly succeeded")

    assert os.environ["AGENTOPS_BUILD_COMMIT_SHA"] == "2" * 40


def test_relay_build_profile_preserves_the_exact_narrow_package_boundary(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("AGENTOPS_BUILD_COMMIT_SHA", COMMIT)

    wheel_name = backend.build_wheel(
        str(tmp_path),
        config_settings={
            backend.DISTRIBUTION_CONFIG_KEY: backend.RELAY_DISTRIBUTION,
        },
    )

    with zipfile.ZipFile(tmp_path / wheel_name) as wheel:
        names = set(wheel.namelist())
        metadata = wheel.read(f"{backend.DIST_INFO}/METADATA")

    expected_packages = {
        path.relative_to(backend.ROOT).as_posix()
        for package in backend.RELAY_PACKAGES
        for path in package.glob("*.py")
    }
    package_names = {
        name for name in names if not name.startswith(f"{backend.DIST_INFO}/")
    }
    assert package_names == expected_packages
    assert not any(name.startswith("open_cekura/") for name in names)
    assert not any(name.startswith("agentops_mis_runtime/") for name in names)
    assert "server.py" not in names
    assert b"Provides-Extra: reliability" not in metadata
