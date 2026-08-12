from __future__ import annotations

import sqlite3
from importlib import resources
from pathlib import Path

import pytest

from open_cekura.campaigns import service


def test_git_commit_falls_back_to_packaged_build_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailedGit:
        returncode = 128
        stdout = ""

    class CommitResource:
        def read_text(self, *, encoding: str) -> str:
            assert encoding == "ascii"
            return f"{'a' * 40}\n"

    class PackageResources:
        def joinpath(self, name: str) -> CommitResource:
            assert name == "_build_commit.txt"
            return CommitResource()

    monkeypatch.setattr(service.subprocess, "run", lambda *_args, **_kwargs: FailedGit())
    monkeypatch.setattr(resources, "files", lambda package: PackageResources())
    monkeypatch.setattr(service, "REPO_ROOT", tmp_path / "site-packages")

    assert service._git_commit_sha() == "a" * 40


def test_installed_default_state_paths_use_current_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    installed_root = tmp_path / "venv" / "Lib" / "site-packages"
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.setattr(service, "REPO_ROOT", installed_root)
    monkeypatch.chdir(outside)
    monkeypatch.delenv("AGENTOPS_DB_PATH", raising=False)

    assert service.resolve_db_path(None) == outside / "agentops_mis.db"
    assert service.resolve_artifact_root(None) == outside / "artifacts" / "open-cekura"


def test_artifact_root_normalization_does_not_resolve_link_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    def fail_if_resolved(self: Path, *args: object, **kwargs: object) -> Path:
        raise AssertionError(f"artifact root was resolved: {self}")

    monkeypatch.setattr(Path, "resolve", fail_if_resolved)

    assert service.resolve_artifact_root("linked-artifacts") == (
        tmp_path / "linked-artifacts"
    )


def test_record_artifact_rejects_idempotent_core_hash_mismatch(tmp_path: Path) -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute(
        "CREATE TABLE runs(run_id TEXT PRIMARY KEY, task_id TEXT NOT NULL)"
    )
    connection.execute("INSERT INTO runs VALUES('run-core','task-core')")
    manifest_path = tmp_path / "campaign" / "run-vertical" / "evidence_manifest.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_bytes(b'{"schema_version":1}')

    class StaleMIS:
        @staticmethod
        def agent_gateway_record_artifact(_conn, body):
            return {
                "artifact": {
                    "artifact_id": body["artifact_id"],
                    "task_id": "task-core",
                    "run_id": body["run_id"],
                    "artifact_type": body["artifact_type"],
                    "title": body["title"],
                    "uri": body["uri"],
                    "summary": body["summary"],
                    "content_hash": "0" * 64,
                },
                "idempotent_replay": True,
            }, 200

    try:
        with pytest.raises(
            service.CampaignServiceError,
            match="Artifact mapping conflicts",
        ):
            service._record_artifact(
                connection,
                mis=StaleMIS(),
                workspace_id="local-demo",
                agent_id="agent-core",
                run_id="run-core",
                artifact_id="artifact-core",
                campaign_id="campaign",
                manifest_path=manifest_path,
            )
    finally:
        connection.close()
