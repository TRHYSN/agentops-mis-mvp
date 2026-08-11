from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from open_cekura.campaigns import service


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
