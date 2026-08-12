from __future__ import annotations

import ast
import os
import tarfile
import zipfile
from pathlib import Path

import pytest

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

    backend_packages = {
        path.relative_to(backend.ROOT).as_posix()
        for package in backend.RELAY_PACKAGES
        for path in package.glob("*.py")
    }
    admin_source = (backend.ROOT / "agentops_mis_cli" / "relay_admin.py").read_text(
        encoding="utf-8"
    )
    admin_tree = ast.parse(admin_source)
    expected_assignment = next(
        node
        for node in admin_tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name)
            and target.id == "EXPECTED_WHEEL_MODULES"
            for target in node.targets
        )
    )
    assert isinstance(expected_assignment.value, ast.Call)
    expected_packages = set(ast.literal_eval(expected_assignment.value.args[0]))
    package_names = {
        name for name in names if not name.startswith(f"{backend.DIST_INFO}/")
    }
    assert backend_packages == expected_packages
    assert package_names == expected_packages
    assert not any(name.startswith("open_cekura/") for name in names)
    assert not any(name.startswith("agentops_mis_runtime/") for name in names)
    assert "server.py" not in names
    assert b"Provides-Extra: reliability" not in metadata


def test_prepared_metadata_is_bound_to_the_requested_distribution_profile(
    tmp_path: Path,
) -> None:
    prepared = tmp_path / "prepared"
    wheel_output = tmp_path / "wheel"
    wheel_output.mkdir()
    backend.prepare_metadata_for_build_wheel(str(prepared))

    with pytest.raises(ValueError, match="distribution profile"):
        backend.build_wheel(
            str(wheel_output),
            config_settings={
                backend.DISTRIBUTION_CONFIG_KEY: backend.RELAY_DISTRIBUTION,
            },
            metadata_directory=str(prepared),
        )


def test_relay_sdist_preserves_the_narrow_profile_when_rebuilt(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("AGENTOPS_BUILD_COMMIT_SHA", COMMIT)
    sdist_output = tmp_path / "sdist"
    extracted = tmp_path / "extracted"
    wheel_output = tmp_path / "wheel"
    for path in (sdist_output, extracted, wheel_output):
        path.mkdir()
    config = {
        backend.DISTRIBUTION_CONFIG_KEY: backend.RELAY_DISTRIBUTION,
    }

    sdist_name = backend.build_sdist(
        str(sdist_output),
        config_settings=config,
    )
    with tarfile.open(sdist_output / sdist_name, "r:gz") as archive:
        names = set(archive.getnames())
        prefix = f"{backend.DIST}-{backend.VERSION}/"
        pkg_info = archive.extractfile(f"{prefix}PKG-INFO")
        assert pkg_info is not None
        metadata = pkg_info.read()
        for member in archive.getmembers():
            relative = Path(member.name)
            assert member.isfile()
            assert not relative.is_absolute() and ".." not in relative.parts
            source = archive.extractfile(member)
            assert source is not None
            target = extracted / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read())

    assert metadata == backend._metadata(backend.RELAY_DISTRIBUTION).encode(
        "utf-8"
    )
    assert f"{prefix}agentops_mis_cli/relay_admin.py" in names
    assert f"{prefix}agentops_mis_core/relay_transport.py" in names
    assert f"{prefix}open_cekura/__init__.py" not in names
    assert f"{prefix}agentops_mis_runtime/__init__.py" not in names
    assert f"{prefix}server.py" not in names

    extracted_backend_path = (
        extracted / prefix / "agentops_mis_cli" / "_build_backend.py"
    )
    namespace: dict[str, object] = {
        "__file__": str(extracted_backend_path),
        "__name__": "_relay_sdist_backend",
    }
    exec(
        compile(
            extracted_backend_path.read_bytes(),
            str(extracted_backend_path),
            "exec",
        ),
        namespace,
    )
    build_wheel = namespace["build_wheel"]
    assert callable(build_wheel)
    rebuilt_name = build_wheel(str(wheel_output))
    with zipfile.ZipFile(wheel_output / rebuilt_name) as wheel:
        rebuilt_names = set(wheel.namelist())
        rebuilt_metadata = wheel.read(f"{backend.DIST_INFO}/METADATA")
    assert rebuilt_metadata == metadata
    assert not any(name.startswith("open_cekura/") for name in rebuilt_names)
    assert not any(name.startswith("agentops_mis_runtime/") for name in rebuilt_names)
    assert "server.py" not in rebuilt_names
