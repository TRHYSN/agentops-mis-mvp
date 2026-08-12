from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

import pytest
import yaml

from open_cekura.campaigns import service
from open_cekura.cli.main import main as cli_main
from open_cekura.evidence.manifest import canonical_json_bytes, sha256_bytes
from open_cekura.scenarios.schema import ScenarioDefinition


REPO_ROOT = Path(__file__).resolve().parents[3]
SCENARIO_SUITE = REPO_ROOT / "examples" / "open-cekura" / "scenarios"
SOURCE_CAMPAIGN_ID = "occampaign_regression_replay_source"
REPLAY_CAMPAIGN_ID = "occampaign_regression_replay_target"
WORKSPACE_ID = "regression-replay-test"


@pytest.fixture(scope="module")
def baseline_authority(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    root = tmp_path_factory.mktemp("regression-replay-source")
    database = root / "agentops.db"
    artifacts = root / "artifacts"
    result = service.run_campaign(
        suite_path=SCENARIO_SUITE,
        agent="mock",
        version="baseline",
        campaign_id=SOURCE_CAMPAIGN_ID,
        workspace_id=WORKSPACE_ID,
        db_path=database,
        artifact_root=artifacts,
    )
    assert result["release_gate"]["decision"] == "block"
    assert result["regression_count"] > 0
    return database, artifacts


def _copy_authority(
    baseline_authority: tuple[Path, Path], tmp_path: Path
) -> tuple[Path, Path]:
    source_database, source_artifacts = baseline_authority
    database = tmp_path / "agentops.db"
    artifacts = tmp_path / "artifacts"
    shutil.copy2(source_database, database)
    shutil.copytree(source_artifacts, artifacts)
    return database, artifacts


@pytest.fixture(scope="module")
def replay_authority(
    baseline_authority: tuple[Path, Path],
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[Path, Path]:
    root = tmp_path_factory.mktemp("regression-replay-target")
    database, artifacts = _copy_authority(baseline_authority, root)
    service.replay_campaign_regressions(
        source_campaign_id=SOURCE_CAMPAIGN_ID,
        version="candidate",
        replay_campaign_id=REPLAY_CAMPAIGN_ID,
        workspace_id=WORKSPACE_ID,
        db_path=database,
        artifact_root=artifacts,
    )
    return database, artifacts


def _table_counts(database: Path) -> dict[str, int]:
    tables = (
        "tasks",
        "agent_plans",
        "runs",
        "tool_calls",
        "evaluations",
        "artifacts",
        "approvals",
        "audit_logs",
        "reliability_campaigns",
        "reliability_conversation_runs",
        "reliability_observed_tool_calls",
        "reliability_evaluation_results",
        "reliability_evidence_manifests",
        "reliability_regression_replays",
        "reliability_release_gates",
    )
    with sqlite3.connect(database) as connection:
        return {
            table: int(
                connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            )
            for table in tables
        }


def test_cli_replays_persisted_regressions_as_one_complete_governed_campaign(
    baseline_authority: tuple[Path, Path],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database, artifacts = _copy_authority(baseline_authority, tmp_path)

    exit_code = cli_main(
        [
            "regression",
            "replay",
            "--campaign",
            SOURCE_CAMPAIGN_ID,
            "--workspace",
            WORKSPACE_ID,
            "--db",
            str(database),
            "--artifacts",
            str(artifacts),
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["operation"] == "regression_replay"
    assert payload["source_campaign_id"] == SOURCE_CAMPAIGN_ID
    assert payload["replay_campaign_id"] != SOURCE_CAMPAIGN_ID
    assert payload["version"] == "candidate"
    assert payload["regression_count"] >= payload["scenario_count"] > 0
    assert len(payload["regression_case_ids"]) == payload["regression_count"]
    assert len(payload["scenario_ids"]) == payload["scenario_count"]
    assert sorted(payload["scenario_id_mapping"]) == payload["source_scenario_ids"]
    assert sorted(payload["scenario_id_mapping"].values()) == sorted(
        payload["scenario_ids"]
    )
    assert set(payload["scenario_id_mapping"]).isdisjoint(payload["scenario_ids"])
    assert payload["run_result"]["campaign_id"] == payload["replay_campaign_id"]
    assert payload["run_result"]["run_count"] == payload["scenario_count"]
    assert payload["run_result"]["release_gate"]["decision"] == "pass"
    assert payload["run_result"]["evidence_verified"] is True
    assert payload["token_omitted"] is True

    replay_campaign_id = payload["replay_campaign_id"]
    verification = service.verify_campaign_evidence(
        campaign_id=replay_campaign_id,
        workspace_id=WORKSPACE_ID,
        db_path=database,
        artifact_root=artifacts,
        strict=True,
    )
    assert verification["verified"] is True

    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        campaign = connection.execute(
            """SELECT c.*,t.status AS task_status,p.status AS plan_status,
                      p.verified_at,p.verification_result_hash
            FROM reliability_campaigns c
            JOIN tasks t ON t.task_id=c.mis_task_id
            JOIN agent_plans p ON p.plan_id=c.mis_plan_id
            WHERE c.workspace_id=? AND c.campaign_id=?""",
            (WORKSPACE_ID, replay_campaign_id),
        ).fetchone()
        assert campaign is not None
        assert campaign["status"] == "completed"
        assert campaign["task_status"] == "completed"
        # Agent Plan's canonical terminal executable state is `submitted`;
        # completion is represented by verified evidence, not a parallel status.
        assert campaign["plan_status"] == "submitted"
        assert campaign["verified_at"]
        assert campaign["verification_result_hash"]

        run_count = connection.execute(
            """SELECT COUNT(*) FROM reliability_conversation_runs
            WHERE workspace_id=? AND campaign_id=? AND mis_run_id IS NOT NULL""",
            (WORKSPACE_ID, replay_campaign_id),
        ).fetchone()[0]
        assert run_count == payload["scenario_count"]
        assert (
            connection.execute(
                """SELECT COUNT(*) FROM runs WHERE run_id IN (
                SELECT mis_run_id FROM reliability_conversation_runs
                WHERE workspace_id=? AND campaign_id=?)""",
                (WORKSPACE_ID, replay_campaign_id),
            ).fetchone()[0]
            == payload["scenario_count"]
        )
        assert (
            connection.execute(
                """SELECT COUNT(*) FROM reliability_observed_tool_calls c
            JOIN reliability_conversation_runs r
              ON r.workspace_id=c.workspace_id AND r.run_id=c.run_id
            WHERE r.workspace_id=? AND r.campaign_id=?
              AND c.mis_tool_call_id IS NULL""",
                (WORKSPACE_ID, replay_campaign_id),
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                """SELECT COUNT(*) FROM reliability_evaluation_results e
            JOIN reliability_conversation_runs r
              ON r.workspace_id=e.workspace_id AND r.run_id=e.run_id
            WHERE r.workspace_id=? AND r.campaign_id=?
              AND e.status!='skipped' AND e.mis_evaluation_id IS NULL""",
                (WORKSPACE_ID, replay_campaign_id),
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                """SELECT COUNT(*) FROM reliability_evidence_manifests m
            JOIN reliability_conversation_runs r
              ON r.workspace_id=m.workspace_id AND r.run_id=m.run_id
            WHERE r.workspace_id=? AND r.campaign_id=?
              AND m.mis_artifact_id IS NOT NULL""",
                (WORKSPACE_ID, replay_campaign_id),
            ).fetchone()[0]
            == payload["scenario_count"]
        )
        gate = connection.execute(
            """SELECT * FROM reliability_release_gates
            WHERE workspace_id=? AND campaign_id=?""",
            (WORKSPACE_ID, replay_campaign_id),
        ).fetchone()
        assert gate is not None and gate["decision"] == "pass"
        assert gate["mis_approval_id"]
        assert (
            connection.execute(
                "SELECT decision FROM approvals WHERE approval_id=?",
                (gate["mis_approval_id"],),
            ).fetchone()[0]
            == "approved"
        )
        replay_mappings = connection.execute(
            """SELECT * FROM reliability_regression_replays
            WHERE workspace_id=? AND target_campaign_id=?
            ORDER BY created_at,mapping_id""",
            (WORKSPACE_ID, replay_campaign_id),
        ).fetchall()
        assert len(replay_mappings) == payload["regression_count"]

    replay_provenance = json.loads(
        (artifacts / replay_campaign_id / "regression_replay.json").read_bytes()
    )
    campaign_summary = json.loads(
        (artifacts / replay_campaign_id / "campaign_summary.json").read_bytes()
    )["summary"]
    assert replay_provenance is not None
    assert replay_provenance["source_campaign_id"] == SOURCE_CAMPAIGN_ID
    assert replay_provenance["target_campaign_id"] == replay_campaign_id
    assert len(replay_provenance["mappings"]) == payload["regression_count"]
    assert campaign_summary["regression_replay"] is not None
    assert campaign_summary["regression_replay"]["mapping_ids"] == [
        mapping["id"] for mapping in replay_provenance["mappings"]
    ]

    scenario_files = sorted((artifacts / replay_campaign_id).glob("*/scenario.yaml"))
    assert len(scenario_files) == payload["scenario_count"]
    for scenario_file in scenario_files:
        scenario_payload = json.loads(scenario_file.read_text(encoding="utf-8"))
        scenario = ScenarioDefinition.model_validate(scenario_payload)
        assert scenario_file.read_bytes() == scenario.canonical_json_bytes() + b"\n"

    counts_before = _table_counts(database)
    replay = service.replay_campaign_regressions(
        source_campaign_id=SOURCE_CAMPAIGN_ID,
        version="candidate",
        workspace_id=WORKSPACE_ID,
        db_path=database,
        artifact_root=artifacts,
    )
    assert replay["replay_campaign_id"] == replay_campaign_id
    assert replay["run_result"]["idempotent_replay"] is True
    assert _table_counts(database) == counts_before


def test_replay_provenance_is_reconstructible_from_sqlite_and_evidence(
    replay_authority: tuple[Path, Path], tmp_path: Path
) -> None:
    database, artifacts = _copy_authority(replay_authority, tmp_path)
    replay_provenance = json.loads(
        (artifacts / REPLAY_CAMPAIGN_ID / "regression_replay.json").read_bytes()
    )
    summary = json.loads(
        (artifacts / REPLAY_CAMPAIGN_ID / "campaign_summary.json").read_bytes()
    )["summary"]
    assert isinstance(replay_provenance, dict)
    mappings = replay_provenance["mappings"]

    assert replay_provenance["schema_version"] == 1
    assert replay_provenance["source_campaign_id"] == SOURCE_CAMPAIGN_ID
    assert replay_provenance["target_campaign_id"] == REPLAY_CAMPAIGN_ID
    assert mappings
    assert summary["regression_replay"] == {
        "schema_version": 1,
        "source_campaign_id": SOURCE_CAMPAIGN_ID,
        "target_campaign_id": REPLAY_CAMPAIGN_ID,
        "mapping_ids": [mapping["id"] for mapping in mappings],
        "mapping_sha256": sha256_bytes(canonical_json_bytes(mappings)),
    }

    source_regressions = {
        regression["id"]: regression
        for regression in json.loads(
            (
                artifacts / SOURCE_CAMPAIGN_ID / "regression_cases.json"
            ).read_bytes()
        )
    }
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """SELECT * FROM reliability_regression_replays
            WHERE workspace_id=? AND target_campaign_id=?
            ORDER BY created_at,mapping_id""",
            (WORKSPACE_ID, REPLAY_CAMPAIGN_ID),
        ).fetchall()
        persisted_mappings = []
        for row in rows:
            persisted = dict(row)
            persisted["id"] = persisted.pop("mapping_id")
            persisted.pop("workspace_id")
            persisted_mappings.append(persisted)
        assert persisted_mappings == mappings

        source_campaign = connection.execute(
            """SELECT * FROM reliability_campaigns
            WHERE workspace_id=? AND campaign_id=?""",
            (WORKSPACE_ID, SOURCE_CAMPAIGN_ID),
        ).fetchone()
        target_campaign = connection.execute(
            """SELECT * FROM reliability_campaigns
            WHERE workspace_id=? AND campaign_id=?""",
            (WORKSPACE_ID, REPLAY_CAMPAIGN_ID),
        ).fetchone()
        assert source_campaign is not None
        assert target_campaign is not None

        for mapping in mappings:
            regression = source_regressions[mapping["regression_case_id"]]
            assert mapping["source_campaign_id"] == SOURCE_CAMPAIGN_ID
            assert mapping["target_campaign_id"] == REPLAY_CAMPAIGN_ID
            assert mapping["source_run_id"] == regression["source_run_id"]
            assert mapping["source_scenario_id"] == regression["scenario_id"]
            assert mapping["evaluator_id"] == regression["evaluator_id"]
            assert mapping["mis_memory_id"] == regression["mis_memory_id"]
            assert mapping["source_snapshot_sha256"] == sha256_bytes(
                canonical_json_bytes(regression["original_input"])
            )

            source_run = connection.execute(
                """SELECT * FROM reliability_conversation_runs
                WHERE workspace_id=? AND run_id=?""",
                (WORKSPACE_ID, mapping["source_run_id"]),
            ).fetchone()
            replay_run = connection.execute(
                """SELECT * FROM reliability_conversation_runs
                WHERE workspace_id=? AND run_id=?""",
                (WORKSPACE_ID, mapping["replay_run_id"]),
            ).fetchone()
            evaluation = connection.execute(
                """SELECT * FROM reliability_evaluation_results
                WHERE workspace_id=? AND evaluation_id=?""",
                (WORKSPACE_ID, mapping["source_evaluation_result_id"]),
            ).fetchone()
            failure = connection.execute(
                """SELECT * FROM reliability_failures
                WHERE workspace_id=? AND failure_id=?""",
                (WORKSPACE_ID, regression["failure_case_id"]),
            ).fetchone()
            memory = connection.execute(
                "SELECT * FROM memories WHERE memory_id=?",
                (mapping["mis_memory_id"],),
            ).fetchone()
            assert source_run is not None and replay_run is not None
            assert evaluation is not None and failure is not None and memory is not None
            assert source_run["campaign_id"] == SOURCE_CAMPAIGN_ID
            assert source_run["scenario_id"] == mapping["source_scenario_id"]
            assert replay_run["campaign_id"] == REPLAY_CAMPAIGN_ID
            assert replay_run["scenario_id"] == mapping["replay_scenario_id"]
            assert failure["evaluation_result_id"] == evaluation["evaluation_id"]
            assert evaluation["run_id"] == source_run["run_id"]
            assert evaluation["evaluator_id"] == mapping["evaluator_id"]
            assert f'evaluation:{evaluation["evaluation_id"]}' in regression[
                "evidence_refs"
            ]

            source_core_run = connection.execute(
                "SELECT * FROM runs WHERE run_id=?", (source_run["mis_run_id"],)
            ).fetchone()
            replay_core_run = connection.execute(
                "SELECT * FROM runs WHERE run_id=?", (replay_run["mis_run_id"],)
            ).fetchone()
            core_evaluation = connection.execute(
                "SELECT * FROM evaluations WHERE evaluation_id=?",
                (evaluation["mis_evaluation_id"],),
            ).fetchone()
            assert source_core_run is not None
            assert replay_core_run is not None
            assert core_evaluation is not None
            assert source_core_run["task_id"] == source_campaign["mis_task_id"]
            assert source_core_run["agent_plan_id"] == source_campaign["mis_plan_id"]
            assert replay_core_run["task_id"] == target_campaign["mis_task_id"]
            assert replay_core_run["agent_plan_id"] == target_campaign["mis_plan_id"]
            assert core_evaluation["run_id"] == source_core_run["run_id"]
            assert memory["memory_type"] == "failure_case"
            assert memory["review_status"] == "candidate"
            assert memory["task_id"] == source_campaign["mis_task_id"]
            assert memory["source_ref"] == source_core_run["run_id"]

            source_scenario = ScenarioDefinition.model_validate(
                yaml.safe_load(
                    (
                        artifacts
                        / SOURCE_CAMPAIGN_ID
                        / mapping["source_run_id"]
                        / "scenario.yaml"
                    ).read_text(encoding="utf-8")
                )
            )
            replay_scenario = ScenarioDefinition.model_validate(
                yaml.safe_load(
                    (
                        artifacts
                        / REPLAY_CAMPAIGN_ID
                        / mapping["replay_run_id"]
                        / "scenario.yaml"
                    ).read_text(encoding="utf-8")
                )
            )
            assert source_scenario.id == mapping["source_scenario_id"]
            assert replay_scenario.id == mapping["replay_scenario_id"]
            assert mapping["source_scenario_sha256"] == (
                source_scenario.canonical_sha256()
            )
            assert mapping["replay_scenario_sha256"] == (
                replay_scenario.canonical_sha256()
            )
            assert replay_scenario.model_copy(
                update={"id": source_scenario.id}
            ) == source_scenario


def test_replay_evidence_detects_regression_provenance_tamper(
    replay_authority: tuple[Path, Path], tmp_path: Path
) -> None:
    database, artifacts = _copy_authority(replay_authority, tmp_path)
    provenance_path = artifacts / REPLAY_CAMPAIGN_ID / "regression_replay.json"
    assert isinstance(json.loads(provenance_path.read_bytes()), dict)
    provenance_path.write_bytes(provenance_path.read_bytes() + b"\n")

    verification = service.verify_campaign_evidence(
        campaign_id=REPLAY_CAMPAIGN_ID,
        workspace_id=WORKSPACE_ID,
        db_path=database,
        artifact_root=artifacts,
        strict=True,
    )

    assert verification["verified"] is False
    assert any(
        issue["code"] == "campaign_hash_mismatch"
        and issue["path"] == "regression_replay.json"
        for issue in verification["issues"]
    )


@pytest.mark.parametrize(
    "authority_drift",
    [
        "mapping_deleted",
        "mapping_snapshot_drift",
        "memory_review_drift",
        "memory_canonical_text_drift",
        "source_task_status_drift",
        "source_plan_verification_hash_drift",
        "source_run_task_drift",
        "source_evaluation_run_drift",
        "source_evaluation_score_or_rubric_drift",
        "source_scenario_json_drift",
    ],
)
def test_gate_rejects_replay_provenance_authority_drift(
    replay_authority: tuple[Path, Path],
    tmp_path: Path,
    authority_drift: str,
) -> None:
    database, artifacts = _copy_authority(replay_authority, tmp_path)
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        mapping = connection.execute(
            """SELECT * FROM reliability_regression_replays
            WHERE workspace_id=? AND target_campaign_id=?
            ORDER BY mapping_id LIMIT 1""",
            (WORKSPACE_ID, REPLAY_CAMPAIGN_ID),
        ).fetchone()
        assert mapping is not None
        if authority_drift == "mapping_deleted":
            connection.execute(
                """DELETE FROM reliability_regression_replays
                WHERE workspace_id=? AND mapping_id=?""",
                (WORKSPACE_ID, mapping["mapping_id"]),
            )
        elif authority_drift == "mapping_snapshot_drift":
            connection.execute(
                """UPDATE reliability_regression_replays
                SET source_snapshot_sha256=?
                WHERE workspace_id=? AND mapping_id=?""",
                ("f" * 64, WORKSPACE_ID, mapping["mapping_id"]),
            )
        elif authority_drift == "memory_review_drift":
            connection.execute(
                "UPDATE memories SET review_status='approved' WHERE memory_id=?",
                (mapping["mis_memory_id"],),
            )
        elif authority_drift == "memory_canonical_text_drift":
            connection.execute(
                "UPDATE memories SET canonical_text=? WHERE memory_id=?",
                ('{"authority":"drift"}', mapping["mis_memory_id"]),
            )
        elif authority_drift == "source_task_status_drift":
            source_task_id = connection.execute(
                """SELECT mis_task_id FROM reliability_campaigns
                WHERE workspace_id=? AND campaign_id=?""",
                (WORKSPACE_ID, SOURCE_CAMPAIGN_ID),
            ).fetchone()[0]
            connection.execute(
                "UPDATE tasks SET status='running' WHERE task_id=?",
                (source_task_id,),
            )
        elif authority_drift == "source_plan_verification_hash_drift":
            source_plan_id = connection.execute(
                """SELECT mis_plan_id FROM reliability_campaigns
                WHERE workspace_id=? AND campaign_id=?""",
                (WORKSPACE_ID, SOURCE_CAMPAIGN_ID),
            ).fetchone()[0]
            connection.execute(
                """UPDATE agent_plans SET verification_result_hash=?
                WHERE plan_id=?""",
                ("f" * 64, source_plan_id),
            )
        elif authority_drift == "source_run_task_drift":
            target_task_id = connection.execute(
                """SELECT mis_task_id FROM reliability_campaigns
                WHERE workspace_id=? AND campaign_id=?""",
                (WORKSPACE_ID, REPLAY_CAMPAIGN_ID),
            ).fetchone()[0]
            source_mis_run_id = connection.execute(
                """SELECT mis_run_id FROM reliability_conversation_runs
                WHERE workspace_id=? AND run_id=?""",
                (WORKSPACE_ID, mapping["source_run_id"]),
            ).fetchone()[0]
            connection.execute(
                "UPDATE runs SET task_id=? WHERE run_id=?",
                (target_task_id, source_mis_run_id),
            )
        elif authority_drift == "source_evaluation_run_drift":
            replay_mis_run_id = connection.execute(
                """SELECT mis_run_id FROM reliability_conversation_runs
                WHERE workspace_id=? AND run_id=?""",
                (WORKSPACE_ID, mapping["replay_run_id"]),
            ).fetchone()[0]
            source_mis_evaluation_id = connection.execute(
                """SELECT mis_evaluation_id FROM reliability_evaluation_results
                WHERE workspace_id=? AND evaluation_id=?""",
                (WORKSPACE_ID, mapping["source_evaluation_result_id"]),
            ).fetchone()[0]
            connection.execute(
                "UPDATE evaluations SET run_id=? WHERE evaluation_id=?",
                (replay_mis_run_id, source_mis_evaluation_id),
            )
        elif authority_drift == "source_evaluation_score_or_rubric_drift":
            source_mis_evaluation_id = connection.execute(
                """SELECT mis_evaluation_id FROM reliability_evaluation_results
                WHERE workspace_id=? AND evaluation_id=?""",
                (WORKSPACE_ID, mapping["source_evaluation_result_id"]),
            ).fetchone()[0]
            connection.execute(
                """UPDATE evaluations SET score=score + 0.125
                WHERE evaluation_id=?""",
                (source_mis_evaluation_id,),
            )
        else:
            connection.execute(
                """UPDATE reliability_scenarios SET tags_json=?
                WHERE workspace_id=? AND scenario_id=?""",
                ('["authority-drift"]', WORKSPACE_ID, mapping["source_scenario_id"]),
            )
        connection.commit()

    with pytest.raises(service.CampaignServiceError) as rejected:
        service.evaluate_campaign_gate(
            campaign_id=REPLAY_CAMPAIGN_ID,
            baseline_campaign_id=None,
            workspace_id=WORKSPACE_ID,
            db_path=database,
            artifact_root=artifacts,
        )

    assert rejected.value.code == "campaign_ledger_mismatch"


def test_idempotent_replay_does_not_add_provenance_mappings(
    replay_authority: tuple[Path, Path], tmp_path: Path
) -> None:
    database, artifacts = _copy_authority(replay_authority, tmp_path)
    with sqlite3.connect(database) as connection:
        before = connection.execute(
            """SELECT mapping_id FROM reliability_regression_replays
            WHERE workspace_id=? AND target_campaign_id=? ORDER BY mapping_id""",
            (WORKSPACE_ID, REPLAY_CAMPAIGN_ID),
        ).fetchall()
    assert before

    replay = service.replay_campaign_regressions(
        source_campaign_id=SOURCE_CAMPAIGN_ID,
        version="candidate",
        replay_campaign_id=REPLAY_CAMPAIGN_ID,
        workspace_id=WORKSPACE_ID,
        db_path=database,
        artifact_root=artifacts,
    )

    with sqlite3.connect(database) as connection:
        after = connection.execute(
            """SELECT mapping_id FROM reliability_regression_replays
            WHERE workspace_id=? AND target_campaign_id=? ORDER BY mapping_id""",
            (WORKSPACE_ID, REPLAY_CAMPAIGN_ID),
        ).fetchall()
    assert replay["run_result"]["idempotent_replay"] is True
    assert after == before


def test_replay_rejects_source_evidence_tamper_before_target_creation(
    baseline_authority: tuple[Path, Path], tmp_path: Path
) -> None:
    database, artifacts = _copy_authority(baseline_authority, tmp_path)
    target_id = "occampaign_tampered_source_must_not_exist"
    regressions = artifacts / SOURCE_CAMPAIGN_ID / "regression_cases.json"
    regressions.write_bytes(regressions.read_bytes() + b"\n")

    with pytest.raises(service.CampaignServiceError) as rejected:
        service.replay_campaign_regressions(
            source_campaign_id=SOURCE_CAMPAIGN_ID,
            version="candidate",
            replay_campaign_id=target_id,
            workspace_id=WORKSPACE_ID,
            db_path=database,
            artifact_root=artifacts,
        )

    assert rejected.value.code == "evidence_verification_failed"
    with sqlite3.connect(database) as connection:
        assert (
            connection.execute(
                """SELECT COUNT(*) FROM reliability_campaigns
            WHERE workspace_id=? AND campaign_id=?""",
                (WORKSPACE_ID, target_id),
            ).fetchone()[0]
            == 0
        )
    assert not (artifacts / target_id).exists()


def test_replay_rejects_scenario_contract_drift_before_target_creation(
    baseline_authority: tuple[Path, Path], tmp_path: Path
) -> None:
    database, artifacts = _copy_authority(baseline_authority, tmp_path)
    target_id = "occampaign_drifted_source_must_not_exist"
    with sqlite3.connect(database) as connection:
        scenario_id = connection.execute(
            """SELECT scenario_id FROM reliability_regressions
            WHERE workspace_id=? ORDER BY regression_id LIMIT 1""",
            (WORKSPACE_ID,),
        ).fetchone()[0]
        connection.execute(
            """UPDATE reliability_scenarios SET source_sha256=?
            WHERE workspace_id=? AND scenario_id=?""",
            ("0" * 64, WORKSPACE_ID, scenario_id),
        )
        connection.commit()

    with pytest.raises(service.CampaignServiceError) as rejected:
        service.replay_campaign_regressions(
            source_campaign_id=SOURCE_CAMPAIGN_ID,
            version="candidate",
            replay_campaign_id=target_id,
            workspace_id=WORKSPACE_ID,
            db_path=database,
            artifact_root=artifacts,
        )

    assert rejected.value.code == "campaign_ledger_mismatch"
    with sqlite3.connect(database) as connection:
        assert (
            connection.execute(
                """SELECT COUNT(*) FROM reliability_campaigns
            WHERE workspace_id=? AND campaign_id=?""",
                (WORKSPACE_ID, target_id),
            ).fetchone()[0]
            == 0
        )
    assert not (artifacts / target_id).exists()


def test_replay_rejects_campaign_without_regressions_and_creates_no_target(
    baseline_authority: tuple[Path, Path], tmp_path: Path
) -> None:
    database, artifacts = _copy_authority(baseline_authority, tmp_path)
    clean_campaign_id = "occampaign_candidate_without_regressions"
    clean = service.run_campaign(
        suite_path=SCENARIO_SUITE,
        agent="mock",
        version="candidate",
        campaign_id=clean_campaign_id,
        workspace_id=WORKSPACE_ID,
        db_path=database,
        artifact_root=artifacts,
    )
    assert clean["regression_count"] == 0
    target_id = "occampaign_empty_regression_target_must_not_exist"

    with pytest.raises(service.CampaignServiceError) as rejected:
        service.replay_campaign_regressions(
            source_campaign_id=clean_campaign_id,
            version="candidate",
            replay_campaign_id=target_id,
            workspace_id=WORKSPACE_ID,
            db_path=database,
            artifact_root=artifacts,
        )

    assert rejected.value.code == "regression_cases_not_found"
    with sqlite3.connect(database) as connection:
        assert (
            connection.execute(
                """SELECT COUNT(*) FROM reliability_campaigns
            WHERE workspace_id=? AND campaign_id=?""",
                (WORKSPACE_ID, target_id),
            ).fetchone()[0]
            == 0
        )
    assert not (artifacts / target_id).exists()
