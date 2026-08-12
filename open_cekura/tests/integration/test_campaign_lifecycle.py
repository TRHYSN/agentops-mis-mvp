from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from open_cekura.campaigns import service
from open_cekura.domain.ids import stable_id


REPO_ROOT = Path(__file__).resolve().parents[3]
SCENARIO_SUITE = REPO_ROOT / "examples" / "open-cekura" / "scenarios"
WORKSPACE_ID = "campaign-lifecycle-test"


def _authority_counts(database: Path) -> dict[str, int]:
    with sqlite3.connect(database) as connection:
        return {
            table: int(
                connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            )
            for table in (
                "agents",
                "tasks",
                "agent_plans",
                "reliability_agents",
                "reliability_agent_versions",
                "reliability_scenario_suites",
                "reliability_campaigns",
                "audit_logs",
            )
        }


def _campaign_states(database: Path, campaign_id: str) -> tuple[str | None, str | None]:
    if not database.is_file():
        return None, None
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            """SELECT c.status,t.status
            FROM reliability_campaigns c
            JOIN tasks t ON t.task_id=c.mis_task_id
            WHERE c.workspace_id=? AND c.campaign_id=?""",
            (WORKSPACE_ID, campaign_id),
        ).fetchone()
    return (None, None) if row is None else (str(row[0]), str(row[1]))


def test_simulation_failure_closes_safe_idempotent_campaign_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "mis.db"
    artifacts = tmp_path / "artifacts"
    campaign_id = "occampaign_simulation_failure"
    secret = "CAMPAIGN_EXCEPTION_SECRET_DO_NOT_PERSIST"
    observed_pre_execution_states: list[tuple[str | None, str | None]] = []

    async def fail_simulation(**_kwargs: object) -> object:
        observed_pre_execution_states.append(_campaign_states(database, campaign_id))
        raise RuntimeError(
            f"Authorization=Bearer {secret}; raw_payload={{'secret':'{secret}'}}"
        )

    monkeypatch.setattr(service, "execute_mock_campaign", fail_simulation)

    def invoke() -> None:
        service.run_campaign(
            suite_path=SCENARIO_SUITE,
            agent="mock",
            version="candidate",
            campaign_id=campaign_id,
            workspace_id=WORKSPACE_ID,
            db_path=database,
            artifact_root=artifacts,
        )

    with pytest.raises(service.CampaignServiceError) as first_failure:
        invoke()

    assert first_failure.value.code == "campaign_simulation_failed"
    assert str(first_failure.value) == "campaign simulation failed"
    assert secret not in str(first_failure.value)
    assert observed_pre_execution_states == [("running", "running")]

    mis_task_id = stable_id("tskoc", WORKSPACE_ID, campaign_id)
    mis_plan_id = stable_id("planoc", mis_task_id, campaign_id)
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        campaign = connection.execute(
            """SELECT * FROM reliability_campaigns
            WHERE workspace_id=? AND campaign_id=?""",
            (WORKSPACE_ID, campaign_id),
        ).fetchone()
        task = connection.execute(
            "SELECT * FROM tasks WHERE task_id=?", (mis_task_id,)
        ).fetchone()
        plan = connection.execute(
            "SELECT * FROM agent_plans WHERE plan_id=?", (mis_plan_id,)
        ).fetchone()
        lifecycle_audits = connection.execute(
            """SELECT action,metadata_json FROM audit_logs
            WHERE entity_type='reliability_campaigns' AND entity_id=?
              AND action LIKE 'open_cekura.campaign.lifecycle.%'
            ORDER BY rowid""",
            (campaign_id,),
        ).fetchall()
        database_dump = "\n".join(connection.iterdump())

    assert campaign is not None
    assert campaign["mis_task_id"] == mis_task_id
    assert campaign["mis_plan_id"] == mis_plan_id
    assert campaign["status"] == "error"
    assert task is not None and task["status"] == "failed"
    assert plan is not None and plan["task_id"] == mis_task_id
    assert plan["verified_at"]
    assert plan["verification_result_hash"]
    assert [row["action"] for row in lifecycle_audits] == [
        "open_cekura.campaign.lifecycle.pending",
        "open_cekura.campaign.lifecycle.running",
        "open_cekura.campaign.lifecycle.error",
    ]
    error_metadata = json.loads(lifecycle_audits[-1]["metadata_json"])
    assert error_metadata == {
        "campaign_id": campaign_id,
        "failure_category": "simulation_runtime_error",
        "raw_exception_omitted": True,
        "raw_payload_omitted": True,
        "workspace_id": WORKSPACE_ID,
    }
    assert secret not in database_dump
    assert not (artifacts / campaign_id).exists()

    counts_after_failure = _authority_counts(database)
    with pytest.raises(service.CampaignServiceError) as replay_failure:
        invoke()

    assert replay_failure.value.code == "campaign_simulation_failed"
    assert str(replay_failure.value) == "campaign simulation failed"
    assert observed_pre_execution_states == [("running", "running")]
    assert _authority_counts(database) == counts_after_failure


def test_successful_campaign_is_running_before_simulation_and_closes_completed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "mis.db"
    artifacts = tmp_path / "artifacts"
    campaign_id = "occampaign_success_lifecycle"
    observed_pre_execution_states: list[tuple[str | None, str | None]] = []
    execute = service.execute_mock_campaign

    async def observe_simulation(**kwargs: object) -> object:
        observed_pre_execution_states.append(_campaign_states(database, campaign_id))
        return await execute(**kwargs)

    monkeypatch.setattr(service, "execute_mock_campaign", observe_simulation)

    result = service.run_campaign(
        suite_path=SCENARIO_SUITE,
        agent="mock",
        version="candidate",
        campaign_id=campaign_id,
        workspace_id=WORKSPACE_ID,
        db_path=database,
        artifact_root=artifacts,
    )

    assert result["ok"] is True
    assert observed_pre_execution_states == [("running", "running")]
    with sqlite3.connect(database) as connection:
        final_states = connection.execute(
            """SELECT c.status,t.status
            FROM reliability_campaigns c
            JOIN tasks t ON t.task_id=c.mis_task_id
            WHERE c.workspace_id=? AND c.campaign_id=?""",
            (WORKSPACE_ID, campaign_id),
        ).fetchone()
        lifecycle_actions = [
            str(row[0])
            for row in connection.execute(
                """SELECT action FROM audit_logs
                WHERE entity_type='reliability_campaigns' AND entity_id=?
                  AND action LIKE 'open_cekura.campaign.lifecycle.%'
                ORDER BY rowid""",
                (campaign_id,),
            ).fetchall()
        ]

    assert final_states == ("completed", "completed")
    assert lifecycle_actions == [
        "open_cekura.campaign.lifecycle.pending",
        "open_cekura.campaign.lifecycle.running",
        "open_cekura.campaign.lifecycle.completed",
    ]


def test_replay_rejects_mismatched_terminal_task_without_healing_authority(
    tmp_path: Path,
) -> None:
    database = tmp_path / "mis.db"
    artifacts = tmp_path / "artifacts"
    campaign_id = "occampaign_mismatched_terminal_authority"
    arguments = {
        "suite_path": SCENARIO_SUITE,
        "agent": "mock",
        "version": "candidate",
        "campaign_id": campaign_id,
        "workspace_id": WORKSPACE_ID,
        "db_path": database,
        "artifact_root": artifacts,
    }
    service.run_campaign(**arguments)
    mis_task_id = stable_id("tskoc", WORKSPACE_ID, campaign_id)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE tasks SET status='running' WHERE task_id=?", (mis_task_id,)
        )
        connection.commit()
        audits_before = int(
            connection.execute("SELECT COUNT(*) FROM audit_logs").fetchone()[0]
        )

    with pytest.raises(service.MISBridgeError):
        service.run_campaign(**arguments)

    with sqlite3.connect(database) as connection:
        task_status = connection.execute(
            "SELECT status FROM tasks WHERE task_id=?", (mis_task_id,)
        ).fetchone()[0]
        campaign_status = connection.execute(
            """SELECT status FROM reliability_campaigns
            WHERE workspace_id=? AND campaign_id=?""",
            (WORKSPACE_ID, campaign_id),
        ).fetchone()[0]
        audits_after = int(
            connection.execute("SELECT COUNT(*) FROM audit_logs").fetchone()[0]
        )

    assert (campaign_status, task_status) == ("completed", "running")
    assert audits_after == audits_before


def test_post_simulation_failure_closes_running_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "mis.db"
    artifacts = tmp_path / "artifacts"
    campaign_id = "occampaign_post_simulation_failure"

    def fail_environment() -> object:
        raise RuntimeError("POST_SIMULATION_SECRET")

    monkeypatch.setattr(service, "_environment", fail_environment)

    with pytest.raises(service.CampaignServiceError) as failure:
        service.run_campaign(
            suite_path=SCENARIO_SUITE,
            agent="mock",
            version="candidate",
            campaign_id=campaign_id,
            workspace_id=WORKSPACE_ID,
            db_path=database,
            artifact_root=artifacts,
        )

    assert failure.value.code == "campaign_execution_failed"
    assert str(failure.value) == "campaign execution failed"
    assert "POST_SIMULATION_SECRET" not in str(failure.value)
    assert _campaign_states(database, campaign_id) == ("error", "failed")
    with sqlite3.connect(database) as connection:
        dump = "\n".join(connection.iterdump())
        metadata = json.loads(
            connection.execute(
                """SELECT metadata_json FROM audit_logs
                WHERE action='open_cekura.campaign.lifecycle.error'
                  AND entity_id=?""",
                (campaign_id,),
            ).fetchone()[0]
        )
    assert metadata["failure_category"] == "post_simulation_error"
    assert "POST_SIMULATION_SECRET" not in dump


def test_primary_and_recovery_failures_still_close_safe_idempotent_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "mis.db"
    artifacts = tmp_path / "artifacts"
    campaign_id = "occampaign_primary_and_recovery_failure"
    primary_secret = "PRIMARY_POST_SIMULATION_SECRET"
    recovery_secret = "SECONDARY_RECOVERY_SECRET"
    simulation_calls = 0
    execute = service.execute_mock_campaign

    async def observe_simulation(**kwargs: object) -> object:
        nonlocal simulation_calls
        simulation_calls += 1
        return await execute(**kwargs)

    def fail_environment() -> object:
        raise RuntimeError(primary_secret)

    def fail_recovery(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError(recovery_secret)

    monkeypatch.setattr(service, "execute_mock_campaign", observe_simulation)
    monkeypatch.setattr(service, "_environment", fail_environment)
    monkeypatch.setattr(
        service, "_recover_campaign_publication_in_connection", fail_recovery
    )

    arguments = {
        "suite_path": SCENARIO_SUITE,
        "agent": "mock",
        "version": "candidate",
        "campaign_id": campaign_id,
        "workspace_id": WORKSPACE_ID,
        "db_path": database,
        "artifact_root": artifacts,
    }
    with pytest.raises(service.CampaignServiceError) as first:
        service.run_campaign(**arguments)

    assert first.value.code == "campaign_execution_failed"
    assert str(first.value) == "campaign execution failed"
    assert primary_secret not in str(first.value)
    assert recovery_secret not in str(first.value)
    assert _campaign_states(database, campaign_id) == ("error", "failed")
    assert simulation_calls == 1

    with pytest.raises(service.CampaignServiceError) as retry:
        service.run_campaign(**arguments)

    assert retry.value.code == "campaign_recovery_failed"
    assert str(retry.value) == "campaign recovery failed"
    assert primary_secret not in str(retry.value)
    assert recovery_secret not in str(retry.value)
    assert simulation_calls == 1
    with sqlite3.connect(database) as connection:
        dump = "\n".join(connection.iterdump())
    assert primary_secret not in dump
    assert recovery_secret not in dump
