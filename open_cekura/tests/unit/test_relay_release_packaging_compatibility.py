from __future__ import annotations

import os
from pathlib import Path

from scripts import build_relay_release as relay_release


COMMIT = "1" * 40


def test_relay_snapshot_covers_the_distribution_build_input_closure() -> None:
    assert {
        "agentops_mis_cli",
        "agentops_mis_core",
        "agentops_mis_runtime",
        "open_cekura",
        "examples/open-cekura",
        "server.py",
    }.issubset(set(relay_release.RELEASE_INPUTS))


def test_backend_build_receives_exact_commit_and_restores_environment(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("AGENTOPS_BUILD_COMMIT_SHA", raising=False)
    observed: list[str | None] = []

    class Backend:
        @staticmethod
        def build_wheel(directory: str) -> str:
            assert Path(directory) == tmp_path
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
        def build_wheel(directory: str) -> str:
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
