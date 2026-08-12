from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from open_cekura.domain.models import RegressionReplayMapping
from open_cekura.storage.repository import (
    AuthorityMappingError,
    RepositoryConflictError,
)
from open_cekura.storage.sqlite_repository import SQLiteRepository
from open_cekura.tests.integration.test_sqlite_reliability_repository import (
    NOW,
    domain_graph,
    open_database,
    persist_graph,
    scenario_contract,
)


def _snapshot_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _persist_target(
    repo: SQLiteRepository,
    conn: Any,
    graph: dict[str, Any],
    *,
    workspace_id: str,
) -> tuple[Any, Any]:
    suffix = "a" if workspace_id == "ws-a" else "b"
    core_task_id = f"tsk_replay_{suffix}"
    core_plan_id = f"plan_replay_{suffix}"
    core_run_id = f"run_replay_{suffix}"
    agent_id = f"agt_{suffix}"
    conn.execute(
        "INSERT INTO tasks(task_id,workspace_id) VALUES(?,?)",
        (core_task_id, workspace_id),
    )
    conn.execute(
        "INSERT INTO agent_plans(plan_id,workspace_id,task_id) VALUES(?,?,?)",
        (core_plan_id, workspace_id, core_task_id),
    )
    conn.execute(
        """INSERT INTO runs(
            run_id,workspace_id,task_id,agent_plan_id,agent_id
        ) VALUES(?,?,?,?,?)""",
        (core_run_id, workspace_id, core_task_id, core_plan_id, agent_id),
    )
    campaign = graph["campaign"].model_copy(
        update={
            "id": "occampaign_replay",
            "mis_task_id": core_task_id,
            "mis_plan_id": core_plan_id,
        }
    )
    replay_scenario = graph["scenario"].model_copy(
        update={
            "id": "appointment.replay",
            "name": "Basic appointment replay",
            "source_sha256": "c" * 64,
        }
    )
    replay_contract = scenario_contract().model_copy(
        update={"id": replay_scenario.id, "name": replay_scenario.name}
    )
    assert repo.upsert_scenario(replay_scenario, contract=replay_contract) == "created"
    run = graph["run"].model_copy(
        update={
            "id": "ocrun_replay",
            "campaign_id": campaign.id,
            "scenario_id": replay_scenario.id,
            "mis_run_id": core_run_id,
        }
    )
    assert repo.upsert_campaign(campaign) == "created"
    assert (
        repo.upsert_run_projection(run, turns=[], tool_calls=[], evaluations=[])
        == "created"
    )
    return campaign, run


def _mapping(graph: dict[str, Any], target_campaign: Any, target_run: Any) -> Any:
    source_scenario_sha = graph["scenario"].source_sha256
    return RegressionReplayMapping(
        schema_version=1,
        id="ocreplaymap_confirmation",
        created_at=NOW,
        source_campaign_id=graph["campaign"].id,
        target_campaign_id=target_campaign.id,
        regression_case_id=graph["regression"].id,
        source_run_id=graph["run"].id,
        source_evaluation_result_id=graph["evaluation"].id,
        evaluator_id=graph["evaluation"].evaluator_id,
        source_scenario_id=graph["scenario"].id,
        replay_scenario_id=target_run.scenario_id,
        replay_run_id=target_run.id,
        source_snapshot_sha256=_snapshot_sha256(
            graph["regression"].model_dump(mode="json")["original_input"]
        ),
        source_scenario_sha256=source_scenario_sha,
        replay_scenario_sha256="c" * 64,
        mis_memory_id=graph["regression"].mis_memory_id,
    )


def _persist_replay_graph(
    repo: SQLiteRepository,
    conn: Any,
    *,
    workspace_id: str = "ws-a",
) -> tuple[dict[str, Any], Any, Any, RegressionReplayMapping]:
    graph = domain_graph(workspace_id)
    persist_graph(repo, graph)
    campaign, run = _persist_target(
        repo, conn, graph, workspace_id=workspace_id
    )
    return graph, campaign, run, _mapping(graph, campaign, run)


def test_replay_mapping_create_retry_and_public_read_are_stable(
    tmp_path: Path,
) -> None:
    conn = open_database(tmp_path / "replay-mapping.db")
    try:
        repo = SQLiteRepository(conn, workspace_id="ws-a")
        repo.initialize_schema()
        graph, campaign, _, mapping = _persist_replay_graph(repo, conn)

        assert repo.upsert_regression_replay(mapping) == "created"
        assert repo.upsert_regression_replay(mapping) == "unchanged"
        assert repo.list_regression_replays(campaign.id) == [
            {
                "workspace_id": "ws-a",
                "mapping_id": mapping.id,
                "schema_version": 1,
                "source_campaign_id": graph["campaign"].id,
                "target_campaign_id": campaign.id,
                "regression_case_id": graph["regression"].id,
                "source_run_id": graph["run"].id,
                "source_evaluation_result_id": graph["evaluation"].id,
                "evaluator_id": graph["evaluation"].evaluator_id,
                "source_scenario_id": graph["scenario"].id,
                "replay_scenario_id": "appointment.replay",
                "replay_run_id": "ocrun_replay",
                "source_snapshot_sha256": mapping.source_snapshot_sha256,
                "source_scenario_sha256": graph["scenario"].source_sha256,
                "replay_scenario_sha256": "c" * 64,
                "mis_memory_id": graph["regression"].mis_memory_id,
                "created_at": mapping.model_dump(mode="json")["created_at"],
            }
        ]

        changed = mapping.model_copy(update={"created_at": NOW + timedelta(seconds=1)})
        with pytest.raises(RepositoryConflictError, match="cannot rebind created_at"):
            repo.upsert_regression_replay(changed)
        duplicate = mapping.model_copy(update={"id": "ocreplaymap_duplicate"})
        with pytest.raises(RepositoryConflictError, match="mapping or relational"):
            repo.upsert_regression_replay(duplicate)
    finally:
        conn.close()


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("source_campaign_id", "occampaign_replay", "source and target campaigns"),
        ("replay_run_id", "ocrun_basic", "replay run.*target campaign"),
        (
            "source_evaluation_result_id",
            "evr_missing",
            "missing vertical parent.*evaluation_id",
        ),
        ("evaluator_id", "timeout.v1", "evaluator"),
        ("source_scenario_id", "appointment.missing", "scenario_id"),
        ("source_snapshot_sha256", "f" * 64, "snapshot"),
        ("source_scenario_sha256", "f" * 64, "source scenario hash"),
        ("replay_scenario_sha256", "f" * 64, "replay scenario hash"),
        ("mis_memory_id", "mem_b", "memory"),
    ],
)
def test_replay_mapping_rejects_vertical_or_snapshot_mismatch(
    tmp_path: Path,
    field: str,
    value: str,
    message: str,
) -> None:
    conn = open_database(tmp_path / f"mismatch-{field}.db")
    try:
        repo = SQLiteRepository(conn, workspace_id="ws-a")
        repo.initialize_schema()
        _, _, _, mapping = _persist_replay_graph(repo, conn)

        with pytest.raises(
            (RepositoryConflictError, AuthorityMappingError), match=message
        ):
            repo.upsert_regression_replay(mapping.model_copy(update={field: value}))
        assert repo.list_regression_replays(mapping.target_campaign_id) == []
    finally:
        conn.close()


@pytest.mark.parametrize(
    ("statement", "message"),
    [
        (
            "UPDATE runs SET task_id='tsk_replay_a' WHERE run_id='run_a'",
            "source MIS run.*campaign task",
        ),
        (
            "UPDATE evaluations SET run_id='run_replay_a' WHERE evaluation_id='eval_a'",
            "source MIS evaluation.*source MIS run",
        ),
        (
            "UPDATE runs SET task_id='tsk_a' WHERE run_id='run_replay_a'",
            "target MIS run.*campaign task",
        ),
    ],
)
def test_replay_mapping_rejects_misaligned_core_authority(
    tmp_path: Path,
    statement: str,
    message: str,
) -> None:
    conn = open_database(tmp_path / "authority-mismatch.db")
    try:
        repo = SQLiteRepository(conn, workspace_id="ws-a")
        repo.initialize_schema()
        _, _, _, mapping = _persist_replay_graph(repo, conn)
        conn.execute(statement)

        with pytest.raises(AuthorityMappingError, match=message):
            repo.upsert_regression_replay(mapping)
    finally:
        conn.close()


def test_replay_mappings_are_isolated_by_workspace_and_schema_is_upgradeable(
    tmp_path: Path,
) -> None:
    conn = open_database(tmp_path / "workspace-replays.db")
    try:
        repo_a = SQLiteRepository(conn, workspace_id="ws-a")
        repo_a.initialize_schema()
        graph_a, campaign_a, _, mapping_a = _persist_replay_graph(repo_a, conn)
        assert repo_a.upsert_regression_replay(mapping_a) == "created"

        repo_b = SQLiteRepository(conn, workspace_id="ws-b")
        repo_b.initialize_schema()
        graph_b, campaign_b, _, mapping_b = _persist_replay_graph(
            repo_b, conn, workspace_id="ws-b"
        )
        assert repo_b.upsert_regression_replay(mapping_b) == "created"

        assert repo_a.list_regression_replays(campaign_a.id)[0]["mis_memory_id"] == (
            graph_a["regression"].mis_memory_id
        )
        assert repo_b.list_regression_replays(campaign_b.id)[0]["mis_memory_id"] == (
            graph_b["regression"].mis_memory_id
        )
        assert len(repo_a.list_regression_replays(campaign_a.id)) == 1
        assert len(repo_b.list_regression_replays(campaign_b.id)) == 1

        conn.execute("DROP TABLE reliability_regression_replays")
        repo_a.initialize_schema()
        assert conn.execute(
            """SELECT name FROM sqlite_master
            WHERE type='table' AND name='reliability_regression_replays'"""
        ).fetchone() is not None
    finally:
        conn.close()
