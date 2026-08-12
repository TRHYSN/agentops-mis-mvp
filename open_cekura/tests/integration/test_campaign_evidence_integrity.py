from __future__ import annotations

import json
import shutil
import sqlite3
from collections.abc import Callable
from pathlib import Path

import pytest

import server
from open_cekura.evidence import manifest as evidence_manifest
from open_cekura.campaigns.service import (
    CampaignServiceError,
    compare_campaigns,
    evaluate_campaign_gate,
    run_campaign,
)
from open_cekura.evidence.manifest import (
    canonical_json_bytes,
    sha256_bytes,
    verified_campaign_json,
    verify_campaign,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
SCENARIO_SUITE = REPO_ROOT / "examples" / "open-cekura" / "scenarios"
CAMPAIGN_ID = "occampaign_evidence_integrity"
BASELINE_ID = "occampaign_evidence_baseline"
COMPARED_CANDIDATE_ID = "occampaign_evidence_compared_candidate"
AUTHORITY_BASELINE_ID = "occampaign_authority_baseline"
AUTHORITY_CANDIDATE_ID = "occampaign_authority_candidate"
SHARED_BASELINE_ID = "occampaign_shared_baseline"
LEFT_BASELINE_ID = "occampaign_left_baseline"
RIGHT_BASELINE_ID = "occampaign_right_baseline"
SHARED_DAG_CANDIDATE_ID = "occampaign_shared_dag_candidate"


@pytest.fixture(scope="module")
def governed_campaign(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("campaign-integrity")
    artifacts = root / "artifacts"
    run_campaign(
        suite_path=SCENARIO_SUITE,
        agent="mock",
        version="candidate",
        campaign_id=CAMPAIGN_ID,
        workspace_id="default",
        db_path=root / "mis.db",
        artifact_root=artifacts,
    )
    assert verify_campaign(artifacts, CAMPAIGN_ID).ok is True
    return artifacts


@pytest.fixture(scope="module")
def compared_campaigns(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("compared-campaign-integrity")
    artifacts = root / "artifacts"
    database = root / "mis.db"
    for campaign_id, version in (
        (BASELINE_ID, "baseline"),
        (COMPARED_CANDIDATE_ID, "candidate"),
    ):
        run_campaign(
            suite_path=SCENARIO_SUITE,
            agent="mock",
            version=version,
            campaign_id=campaign_id,
            workspace_id="default",
            db_path=database,
            artifact_root=artifacts,
        )
    compare_campaigns(
        baseline_campaign_id=BASELINE_ID,
        candidate_campaign_id=COMPARED_CANDIDATE_ID,
        workspace_id="default",
        db_path=database,
        artifact_root=artifacts,
    )
    assert verify_campaign(artifacts, BASELINE_ID).ok is True
    assert verify_campaign(artifacts, COMPARED_CANDIDATE_ID).ok is True
    return artifacts


@pytest.fixture(scope="module")
def authority_campaigns(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[Path, Path]:
    root = tmp_path_factory.mktemp("campaign-authority")
    artifacts = root / "artifacts"
    database = root / "mis.db"
    for campaign_id, version in (
        (AUTHORITY_BASELINE_ID, "baseline"),
        (AUTHORITY_CANDIDATE_ID, "candidate"),
    ):
        run_campaign(
            suite_path=SCENARIO_SUITE,
            agent="mock",
            version=version,
            campaign_id=campaign_id,
            workspace_id="default",
            db_path=database,
            artifact_root=artifacts,
        )
    return artifacts, database


def _copy_authority_campaigns(
    authority_campaigns: tuple[Path, Path], tmp_path: Path
) -> tuple[Path, Path]:
    source_artifacts, source_database = authority_campaigns
    artifacts = tmp_path / "artifacts"
    database = tmp_path / "mis.db"
    shutil.copytree(source_artifacts, artifacts)
    with sqlite3.connect(source_database) as source, sqlite3.connect(database) as target:
        source.backup(target)
    return artifacts, database


def _open_authority_database(database: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    connection.create_function(
        "agentops_audit_chain_hash",
        9,
        server.audit_chain_hash_sql,
        deterministic=True,
    )
    return connection


def _candidate_authority_ids(connection: sqlite3.Connection) -> tuple[str, str]:
    row = connection.execute(
        """SELECT mis_task_id,mis_plan_id FROM reliability_campaigns
        WHERE workspace_id='default' AND campaign_id=?""",
        (AUTHORITY_CANDIDATE_ID,),
    ).fetchone()
    assert row is not None
    return row["mis_task_id"], row["mis_plan_id"]


def _gate_write_counts(connection: sqlite3.Connection) -> dict[str, int]:
    return {
        "approvals": connection.execute(
            "SELECT COUNT(*) FROM approvals"
        ).fetchone()[0],
        "gates": connection.execute(
            "SELECT COUNT(*) FROM reliability_release_gates"
        ).fetchone()[0],
        "outbox": connection.execute(
            "SELECT COUNT(*) FROM reliability_evidence_publications"
        ).fetchone()[0],
    }


def test_comparison_rejects_completed_campaign_with_replanned_core_task(
    authority_campaigns: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    artifacts, database = _copy_authority_campaigns(authority_campaigns, tmp_path)
    with _open_authority_database(database) as connection:
        task_id, _ = _candidate_authority_ids(connection)
        task = dict(
            connection.execute(
                "SELECT * FROM tasks WHERE task_id=?", (task_id,)
            ).fetchone()
        )
        task.update(status="planned", updated_at=server.now_iso())
        assert (
            server.upsert_task(
                connection,
                task,
                actor_id="open-cekura-authority-drift-test",
            )
            == "updated"
        )
        writes_before = _gate_write_counts(connection)

    with pytest.raises(CampaignServiceError) as captured:
        compare_campaigns(
            baseline_campaign_id=AUTHORITY_BASELINE_ID,
            candidate_campaign_id=AUTHORITY_CANDIDATE_ID,
            workspace_id="default",
            db_path=database,
            artifact_root=artifacts,
        )

    assert captured.value.code == "campaign_ledger_mismatch"
    with sqlite3.connect(database) as connection:
        assert _gate_write_counts(connection) == writes_before


def test_gate_rejects_completed_campaign_with_rejected_core_plan(
    authority_campaigns: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    artifacts, database = _copy_authority_campaigns(authority_campaigns, tmp_path)
    with _open_authority_database(database) as connection:
        _, plan_id = _candidate_authority_ids(connection)
        response, status = server.transition_agent_plan(
            connection,
            plan_id,
            "rejected",
            {},
            {"reason": "Exercise a legitimate audited Plan rejection."},
            actor_override={
                "actor_type": "user",
                "actor_id": "usr_founder",
                "workspace_id": "default",
                "auth_mode": "test_human_session",
            },
        )
        assert status == 200
        assert response["agent_plan"]["status"] == "rejected"
        writes_before = _gate_write_counts(connection)

    with pytest.raises(CampaignServiceError) as captured:
        evaluate_campaign_gate(
            campaign_id=AUTHORITY_CANDIDATE_ID,
            baseline_campaign_id=None,
            workspace_id="default",
            db_path=database,
            artifact_root=artifacts,
        )

    assert captured.value.code == "campaign_ledger_mismatch"
    with sqlite3.connect(database) as connection:
        assert _gate_write_counts(connection) == writes_before


@pytest.fixture
def shared_baseline_dag(tmp_path: Path) -> Path:
    artifacts = tmp_path / "artifacts"
    for campaign_id in (
        SHARED_BASELINE_ID,
        LEFT_BASELINE_ID,
        RIGHT_BASELINE_ID,
        SHARED_DAG_CANDIDATE_ID,
    ):
        (artifacts / campaign_id).mkdir(parents=True)
    return artifacts


def _install_recursive_campaign_graph(
    monkeypatch: pytest.MonkeyPatch,
    calls_by_campaign: dict[str, int],
) -> None:
    graph = {
        SHARED_DAG_CANDIDATE_ID: (LEFT_BASELINE_ID, RIGHT_BASELINE_ID),
        LEFT_BASELINE_ID: (SHARED_BASELINE_ID,),
        RIGHT_BASELINE_ID: (SHARED_BASELINE_ID,),
        SHARED_BASELINE_ID: (),
    }

    def fake_verify_run_bundles(
        root: str | Path,
        campaign_id: str,
        *,
        strict: bool = False,
        **kwargs: object,
    ):
        del strict, kwargs
        calls_by_campaign[campaign_id] = calls_by_campaign.get(campaign_id, 0) + 1
        return evidence_manifest.VerificationReport(
            root=Path(root).resolve(),
            campaign_id=campaign_id,
            runs=(),
            issues=(),
        )

    def fake_verify_gate_history(
        campaign_path: Path,
        campaign_id: str,
        payload: object,
        campaign_artifacts: object,
        issues: object,
        *,
        verification_context: object,
    ):
        del campaign_path, payload, campaign_artifacts, issues
        for _ in graph[campaign_id]:
            verification_context.consume_gate_snapshot()
        return ()

    def fake_verify_summary_facts(
        campaign_id: str,
        envelope: object,
        campaign_payloads: object,
        runs: object,
        issues: list[object],
        *,
        campaign_root: Path,
        strict: bool,
        campaign_stack: frozenset[str],
        verification_context: object,
        **kwargs: object,
    ) -> None:
        del envelope, campaign_payloads, runs, kwargs
        for baseline_id in graph[campaign_id]:
            report = evidence_manifest.verify_campaign(
                campaign_root,
                baseline_id,
                strict=strict,
                _campaign_stack=campaign_stack,
                _verification_context=verification_context,
            )
            issues.extend(report.issues)

    monkeypatch.setattr(evidence_manifest, "CAMPAIGN_FILENAMES", frozenset())
    monkeypatch.setattr(
        evidence_manifest,
        "verify_run_bundles",
        fake_verify_run_bundles,
    )
    monkeypatch.setattr(
        evidence_manifest,
        "_verify_campaign_summary",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        evidence_manifest,
        "_verify_gate_history",
        fake_verify_gate_history,
    )
    monkeypatch.setattr(
        evidence_manifest,
        "_verify_campaign_summary_facts",
        fake_verify_summary_facts,
    )


def test_shared_baseline_campaign_is_rechecked_for_distinct_ancestry(
    shared_baseline_dag: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls_by_campaign: dict[str, int] = {}
    _install_recursive_campaign_graph(monkeypatch, calls_by_campaign)

    report = evidence_manifest.verify_campaign(
        shared_baseline_dag,
        SHARED_DAG_CANDIDATE_ID,
    )

    assert report.ok is True
    assert calls_by_campaign[SHARED_BASELINE_ID] == 2


def test_memoization_cannot_bypass_comparison_depth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = {
        "top": ("x", "p1"),
        "x": ("y",),
        "y": ("z",),
        "z": (),
        "p1": ("p2",),
        "p2": ("p3",),
        "p3": ("p4",),
        "p4": ("p5",),
        "p5": ("p6",),
        "p6": ("x",),
    }
    calls: dict[str, int] = {}
    for campaign_id in graph:
        (tmp_path / campaign_id).mkdir()

    def fake_verify_run_bundles(
        root: str | Path,
        campaign_id: str,
        *,
        strict: bool = False,
        **kwargs: object,
    ) -> evidence_manifest.VerificationReport:
        del strict, kwargs
        calls[campaign_id] = calls.get(campaign_id, 0) + 1
        return evidence_manifest.VerificationReport(
            root=Path(root).resolve(),
            campaign_id=campaign_id,
            runs=(),
            issues=(),
        )

    def fake_verify_summary_facts(
        campaign_id: str,
        envelope: object,
        payloads: object,
        runs: object,
        issues: list[evidence_manifest.VerificationIssue],
        *,
        campaign_root: Path,
        strict: bool,
        campaign_stack: frozenset[str],
        verification_context: evidence_manifest.VerificationContext,
        **kwargs: object,
    ) -> None:
        del envelope, payloads, runs, kwargs
        for child in graph[campaign_id]:
            if len(campaign_stack) >= evidence_manifest.MAX_COMPARISON_DEPTH:
                issues.append(
                    evidence_manifest._issue(
                        "campaign_comparison_invalid",
                        "error",
                        evidence_manifest.CAMPAIGN_SUMMARY_FILENAME,
                        f"depth rejected at {campaign_id}->{child}",
                    )
                )
                continue
            child_report = evidence_manifest.verify_campaign(
                campaign_root,
                child,
                strict=strict,
                _campaign_stack=campaign_stack,
                _verification_context=verification_context,
            )
            issues.extend(child_report.issues)

    monkeypatch.setattr(evidence_manifest, "CAMPAIGN_FILENAMES", frozenset())
    monkeypatch.setattr(
        evidence_manifest,
        "verify_run_bundles",
        fake_verify_run_bundles,
    )
    monkeypatch.setattr(
        evidence_manifest,
        "_verify_campaign_summary",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        evidence_manifest,
        "_verify_gate_history",
        lambda *args, **kwargs: (),
    )
    monkeypatch.setattr(
        evidence_manifest,
        "_verify_campaign_summary_facts",
        fake_verify_summary_facts,
    )

    report = evidence_manifest.verify_campaign(tmp_path, "top")

    assert report.ok is False
    assert "campaign_comparison_invalid" in {issue.code for issue in report.issues}


@pytest.mark.parametrize(
    ("limit_name", "limit"),
    [
        ("MAX_VERIFICATION_CAMPAIGNS", 2),
        ("MAX_VERIFICATION_GATE_SNAPSHOTS", 1),
        ("MAX_VERIFICATION_WORK_UNITS", 1),
    ],
)
def test_recursive_verification_global_budgets_fail_closed(
    shared_baseline_dag: Path,
    monkeypatch: pytest.MonkeyPatch,
    limit_name: str,
    limit: int,
) -> None:
    monkeypatch.setattr(evidence_manifest, limit_name, limit, raising=False)
    _install_recursive_campaign_graph(monkeypatch, {})

    report = evidence_manifest.verify_campaign(
        shared_baseline_dag,
        SHARED_DAG_CANDIDATE_ID,
    )

    assert report.ok is False
    assert "verification_budget_exceeded" in {
        issue.code for issue in report.issues
    }


def _mutate_mis_evaluation(connection: sqlite3.Connection) -> None:
    row = connection.execute(
        """SELECT e.mis_evaluation_id
        FROM reliability_evaluation_results e
        JOIN reliability_conversation_runs r
          ON r.workspace_id=e.workspace_id AND r.run_id=e.run_id
        WHERE r.workspace_id='default' AND r.campaign_id=?
          AND e.mis_evaluation_id IS NOT NULL
        ORDER BY e.evaluation_id LIMIT 1""",
        (AUTHORITY_CANDIDATE_ID,),
    ).fetchone()
    assert row is not None
    connection.execute(
        "UPDATE evaluations SET score=score + 0.123 WHERE evaluation_id=?",
        (row[0],),
    )


def _mutate_vertical_evaluation(connection: sqlite3.Connection) -> None:
    row = connection.execute(
        """SELECT e.evaluation_id
        FROM reliability_evaluation_results e
        JOIN reliability_conversation_runs r
          ON r.workspace_id=e.workspace_id AND r.run_id=e.run_id
        WHERE r.workspace_id='default' AND r.campaign_id=?
        ORDER BY e.evaluation_id LIMIT 1""",
        (AUTHORITY_CANDIDATE_ID,),
    ).fetchone()
    assert row is not None
    connection.execute(
        """UPDATE reliability_evaluation_results
        SET score=score + 0.123
        WHERE workspace_id='default' AND evaluation_id=?""",
        (row[0],),
    )


def _mutate_mis_artifact_hash(connection: sqlite3.Connection) -> None:
    row = connection.execute(
        """SELECT m.mis_artifact_id
        FROM reliability_evidence_manifests m
        WHERE m.workspace_id='default' AND m.campaign_id=?
        ORDER BY m.manifest_id LIMIT 1""",
        (AUTHORITY_CANDIDATE_ID,),
    ).fetchone()
    assert row is not None and row[0] is not None
    connection.execute(
        "UPDATE artifacts SET content_hash=? WHERE artifact_id=?",
        ("f" * 64, row[0]),
    )


def _mutate_plan_evidence(connection: sqlite3.Connection) -> None:
    row = connection.execute(
        """SELECT m.mis_plan_evidence_manifest_id
        FROM reliability_evidence_manifests m
        WHERE m.workspace_id='default' AND m.campaign_id=?
          AND m.mis_plan_evidence_manifest_id IS NOT NULL
        ORDER BY m.manifest_id LIMIT 1""",
        (AUTHORITY_CANDIDATE_ID,),
    ).fetchone()
    assert row is not None
    connection.execute(
        "UPDATE plan_evidence_manifests SET status='submitted' WHERE manifest_id=?",
        (row[0],),
    )


def _mutate_gate_approval_hash(connection: sqlite3.Connection) -> None:
    row = connection.execute(
        """SELECT mis_approval_id FROM reliability_release_gates
        WHERE workspace_id='default' AND campaign_id=?
          AND mis_approval_id IS NOT NULL
        ORDER BY gate_id LIMIT 1""",
        (AUTHORITY_CANDIDATE_ID,),
    ).fetchone()
    assert row is not None
    connection.execute(
        "UPDATE approvals SET subject_hash=? WHERE approval_id=?",
        ("e" * 64, row[0]),
    )


def _mutate_core_tool_call_args(connection: sqlite3.Connection) -> None:
    row = connection.execute(
        """SELECT tc.tool_call_id FROM tool_calls tc
        JOIN reliability_conversation_runs r ON r.mis_run_id=tc.run_id
        WHERE r.workspace_id='default' AND r.campaign_id=?
        ORDER BY tc.tool_call_id LIMIT 1""",
        (AUTHORITY_CANDIDATE_ID,),
    ).fetchone()
    assert row is not None
    connection.execute(
        "UPDATE tool_calls SET normalized_args_json=? WHERE tool_call_id=?",
        ('{"forged":true}', row[0]),
    )


def _mutate_vertical_tool_call_args(connection: sqlite3.Connection) -> None:
    row = connection.execute(
        """SELECT c.tool_call_id FROM reliability_observed_tool_calls c
        JOIN reliability_conversation_runs r
          ON r.workspace_id=c.workspace_id AND r.run_id=c.run_id
        WHERE r.workspace_id='default' AND r.campaign_id=?
        ORDER BY c.tool_call_id LIMIT 1""",
        (AUTHORITY_CANDIDATE_ID,),
    ).fetchone()
    assert row is not None
    connection.execute(
        """UPDATE reliability_observed_tool_calls SET arguments_json=?
        WHERE workspace_id='default' AND tool_call_id=?""",
        ('{"forged":true}', row[0]),
    )


def _add_vertical_manifest(connection: sqlite3.Connection) -> None:
    connection.execute(
        """INSERT INTO reliability_evidence_manifests(
            workspace_id,manifest_id,schema_version,campaign_id,run_id,
            mis_artifact_id,mis_plan_evidence_manifest_id,git_commit_sha,
            environment_json,scenario_sha256,agent_config_sha256,
            evaluator_versions_json,artifacts_json,started_at,finished_at,
            final_state,created_at
        )
        SELECT workspace_id,'ocmanifest_forged_extra',schema_version,campaign_id,
            run_id,NULL,NULL,git_commit_sha,environment_json,scenario_sha256,
            agent_config_sha256,evaluator_versions_json,artifacts_json,started_at,
            finished_at,final_state,created_at
        FROM reliability_evidence_manifests
        WHERE workspace_id='default' AND campaign_id=?
        ORDER BY manifest_id LIMIT 1""",
        (AUTHORITY_CANDIDATE_ID,),
    )


def _rebind_campaign_agent_version(connection: sqlite3.Connection) -> None:
    baseline = connection.execute(
        """SELECT agent_version_id FROM reliability_campaigns
        WHERE workspace_id='default' AND campaign_id=?""",
        (AUTHORITY_BASELINE_ID,),
    ).fetchone()
    assert baseline is not None
    connection.execute(
        """UPDATE reliability_campaigns SET agent_version_id=?
        WHERE workspace_id='default' AND campaign_id=?""",
        (baseline[0], AUTHORITY_CANDIDATE_ID),
    )


def _delete_plan_evidence_manifest(connection: sqlite3.Connection) -> None:
    connection.execute(
        """DELETE FROM plan_evidence_manifests WHERE manifest_id=(
            SELECT m.mis_plan_evidence_manifest_id
            FROM reliability_evidence_manifests m
            WHERE m.workspace_id='default' AND m.campaign_id=?
              AND m.mis_plan_evidence_manifest_id IS NOT NULL
            ORDER BY m.manifest_id LIMIT 1
        )""",
        (AUTHORITY_CANDIDATE_ID,),
    )


def _add_plan_evidence_manifest(connection: sqlite3.Connection) -> None:
    connection.execute(
        """INSERT INTO plan_evidence_manifests(
            manifest_id,workspace_id,plan_id,task_id,run_id,agent_id,
            mismatch_policy,expected_steps_json,tool_call_ids_json,
            evaluation_ids_json,artifact_ids_json,audit_ids_json,plan_hash,
            verification_result_hash,status,verification_json,created_at,updated_at
        )
        SELECT 'ocpem_forged_extra',workspace_id,plan_id,task_id,run_id,agent_id,
            mismatch_policy,expected_steps_json,tool_call_ids_json,
            evaluation_ids_json,artifact_ids_json,audit_ids_json,plan_hash,
            verification_result_hash,status,verification_json,created_at,updated_at
        FROM plan_evidence_manifests
        WHERE plan_id=(
            SELECT mis_plan_id FROM reliability_campaigns
            WHERE workspace_id='default' AND campaign_id=?
        )
        ORDER BY manifest_id LIMIT 1""",
        (AUTHORITY_CANDIDATE_ID,),
    )


def _delete_gate_audit(connection: sqlite3.Connection) -> None:
    connection.execute(
        """DELETE FROM audit_logs
        WHERE action='open_cekura.release_gate.evaluate'
          AND entity_type='reliability_release_gate'
          AND entity_id IN (
            SELECT gate_id FROM reliability_release_gates
            WHERE workspace_id='default' AND campaign_id=?
          )""",
        (AUTHORITY_CANDIDATE_ID,),
    )


def _add_orphan_gate_approval(connection: sqlite3.Connection) -> None:
    connection.execute(
        """INSERT INTO approvals(
            approval_id,task_id,run_id,decision,subject_type,subject_id,created_at
        )
        SELECT 'apoc_forged_orphan',task_id,run_id,decision,subject_type,
            subject_id,created_at
        FROM approvals
        WHERE task_id=(
            SELECT mis_task_id FROM reliability_campaigns
            WHERE workspace_id='default' AND campaign_id=?
        ) AND subject_type='reliability_release_gate'
        ORDER BY approval_id LIMIT 1""",
        (AUTHORITY_CANDIDATE_ID,),
    )


@pytest.mark.parametrize(
    "mutation",
    [
        _mutate_mis_evaluation,
        _mutate_vertical_evaluation,
        _mutate_mis_artifact_hash,
        _mutate_plan_evidence,
        _mutate_gate_approval_hash,
        _mutate_core_tool_call_args,
        _mutate_vertical_tool_call_args,
        _add_vertical_manifest,
        _rebind_campaign_agent_version,
        _delete_plan_evidence_manifest,
        _add_plan_evidence_manifest,
        _delete_gate_audit,
        _add_orphan_gate_approval,
    ],
    ids=[
        "mis-evaluation",
        "vertical-evaluation",
        "mis-artifact-hash",
        "plan-evidence",
        "gate-approval-hash",
        "core-tool-call-args",
        "vertical-tool-call-args",
        "extra-vertical-manifest",
        "campaign-agent-version-rebind",
        "missing-plan-evidence-id",
        "extra-plan-evidence-id",
        "missing-gate-audit",
        "orphan-gate-approval",
    ],
)
def test_comparison_rejects_evidence_that_disagrees_with_mis_authority(
    authority_campaigns: tuple[Path, Path],
    tmp_path: Path,
    mutation: Callable[[sqlite3.Connection], None],
) -> None:
    source_artifacts, source_database = authority_campaigns
    artifacts = tmp_path / "artifacts"
    database = tmp_path / "mis.db"
    shutil.copytree(source_artifacts, artifacts)
    with sqlite3.connect(source_database) as source, sqlite3.connect(database) as target:
        source.backup(target)
    with sqlite3.connect(database) as connection:
        mutation(connection)
        approvals_before = connection.execute(
            "SELECT COUNT(*) FROM approvals"
        ).fetchone()[0]

    with pytest.raises(CampaignServiceError) as captured:
        compare_campaigns(
            baseline_campaign_id=AUTHORITY_BASELINE_ID,
            candidate_campaign_id=AUTHORITY_CANDIDATE_ID,
            workspace_id="default",
            db_path=database,
            artifact_root=artifacts,
        )

    assert captured.value.code == "campaign_ledger_mismatch"
    with sqlite3.connect(database) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM approvals").fetchone()[0]
            == approvals_before
        )


def test_comparison_preserves_versioned_gate_evidence_history(
    authority_campaigns: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    source_artifacts, source_database = authority_campaigns
    artifacts = tmp_path / "artifacts"
    database = tmp_path / "mis.db"
    shutil.copytree(source_artifacts, artifacts)
    with sqlite3.connect(source_database) as source, sqlite3.connect(database) as target:
        source.backup(target)
    candidate_path = artifacts / AUTHORITY_CANDIDATE_ID
    standalone_gate = json.loads((candidate_path / "release_gate.json").read_bytes())
    standalone_diff = json.loads(
        (candidate_path / "baseline_candidate_diff.json").read_bytes()
    )

    compare_campaigns(
        baseline_campaign_id=AUTHORITY_BASELINE_ID,
        candidate_campaign_id=AUTHORITY_CANDIDATE_ID,
        workspace_id="default",
        db_path=database,
        artifact_root=artifacts,
    )

    history_path = candidate_path / "gate_history.json"
    history_bytes = history_path.read_bytes()
    history = json.loads(history_bytes)
    current_gate = json.loads((candidate_path / "release_gate.json").read_bytes())
    current_diff = json.loads(
        (candidate_path / "baseline_candidate_diff.json").read_bytes()
    )
    standalone_id = standalone_gate["id"]
    current_id = current_gate["id"]
    assert history["schema_version"] == 1
    assert history["campaign_id"] == AUTHORITY_CANDIDATE_ID
    assert history["current_gate_id"] == current_id
    assert set(history["entries"]) == {standalone_id, current_id}
    for gate_id, gate, diff in (
        (standalone_id, standalone_gate, standalone_diff),
        (current_id, current_gate, current_diff),
    ):
        snapshot_path = candidate_path / "gates" / gate_id
        gate_bytes = (snapshot_path / "release_gate.json").read_bytes()
        diff_bytes = (snapshot_path / "baseline_candidate_diff.json").read_bytes()
        assert json.loads(gate_bytes) == gate
        assert json.loads(diff_bytes) == diff
        assert history["entries"][gate_id] == {
            "release_gate_sha256": sha256_bytes(gate_bytes),
            "baseline_candidate_diff_sha256": sha256_bytes(diff_bytes),
        }
    snapshot_bytes = {
        path.relative_to(candidate_path).as_posix(): path.read_bytes()
        for path in (candidate_path / "gates").glob("*/*.json")
    }

    compare_campaigns(
        baseline_campaign_id=AUTHORITY_BASELINE_ID,
        candidate_campaign_id=AUTHORITY_CANDIDATE_ID,
        workspace_id="default",
        db_path=database,
        artifact_root=artifacts,
    )

    assert history_path.read_bytes() == history_bytes
    assert {
        path.relative_to(candidate_path).as_posix(): path.read_bytes()
        for path in (candidate_path / "gates").glob("*/*.json")
    } == snapshot_bytes


def test_comparison_rejects_coordinated_filesystem_and_database_gate_head_rollback(
    authority_campaigns: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    source_artifacts, source_database = authority_campaigns
    artifacts = tmp_path / "artifacts"
    database = tmp_path / "mis.db"
    shutil.copytree(source_artifacts, artifacts)
    with sqlite3.connect(source_database) as source, sqlite3.connect(database) as target:
        source.backup(target)
    campaign_path = artifacts / AUTHORITY_CANDIDATE_ID
    standalone_summary = json.loads(
        (campaign_path / "campaign_summary.json").read_bytes()
    )

    compare_campaigns(
        baseline_campaign_id=AUTHORITY_BASELINE_ID,
        candidate_campaign_id=AUTHORITY_CANDIDATE_ID,
        workspace_id="default",
        db_path=database,
        artifact_root=artifacts,
    )

    history_path = campaign_path / "gate_history.json"
    history = json.loads(history_path.read_bytes())
    current_gate_id = history["current_gate_id"]
    previous_gate_id = next(
        gate_id for gate_id in history["entries"] if gate_id != current_gate_id
    )
    previous_snapshot = campaign_path / "gates" / previous_gate_id
    (campaign_path / "release_gate.json").write_bytes(
        (previous_snapshot / "release_gate.json").read_bytes()
    )
    (campaign_path / "baseline_candidate_diff.json").write_bytes(
        (previous_snapshot / "baseline_candidate_diff.json").read_bytes()
    )
    history["current_gate_id"] = previous_gate_id
    changed_history = canonical_json_bytes(history)
    history_path.write_bytes(changed_history)
    standalone_summary["artifacts"]["gate_history.json"] = sha256_bytes(
        changed_history
    )
    (campaign_path / "campaign_summary.json").write_bytes(
        canonical_json_bytes(standalone_summary)
    )
    assert verify_campaign(artifacts, AUTHORITY_CANDIDATE_ID).ok is True

    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        head_audits = connection.execute(
            """SELECT audit_id,created_at,metadata_json FROM audit_logs
            WHERE action='open_cekura.release_gate.head'
              AND entity_type='reliability_campaign_gate_head'
              AND entity_id=?""",
            (AUTHORITY_CANDIDATE_ID,),
        ).fetchall()
        previous_head_audit = next(
            row
            for row in head_audits
            if json.loads(row["metadata_json"]).get("current_gate_id")
            == previous_gate_id
        )
        connection.execute(
            """UPDATE reliability_campaign_gate_heads
            SET current_gate_id=?,mis_audit_id=?,updated_at=?
            WHERE workspace_id='default' AND campaign_id=?""",
            (
                previous_gate_id,
                previous_head_audit["audit_id"],
                previous_head_audit["created_at"],
                AUTHORITY_CANDIDATE_ID,
            ),
        )
        approvals_before = connection.execute(
            "SELECT COUNT(*) FROM approvals"
        ).fetchone()[0]

    with pytest.raises(CampaignServiceError) as captured:
        compare_campaigns(
            baseline_campaign_id=AUTHORITY_BASELINE_ID,
            candidate_campaign_id=AUTHORITY_CANDIDATE_ID,
            workspace_id="default",
            db_path=database,
            artifact_root=artifacts,
        )

    assert captured.value.code == "campaign_ledger_mismatch"
    with sqlite3.connect(database) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM approvals").fetchone()[0]
            == approvals_before
        )


def test_coordinated_old_gate_snapshot_rewrite_fails_policy_reconstruction(
    compared_campaigns: Path,
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    shutil.copytree(compared_campaigns, artifacts)
    campaign_path = artifacts / COMPARED_CANDIDATE_ID
    history_path = campaign_path / "gate_history.json"
    history = json.loads(history_path.read_bytes())
    old_gate_id = next(
        gate_id
        for gate_id in history["entries"]
        if gate_id != history["current_gate_id"]
    )
    old_gate_path = campaign_path / "gates" / old_gate_id / "release_gate.json"
    old_gate = json.loads(old_gate_path.read_bytes())
    old_gate["metrics"]["task_success_rate"] = 0.5
    changed_gate = canonical_json_bytes(old_gate)
    old_gate_path.write_bytes(changed_gate)
    history["entries"][old_gate_id]["release_gate_sha256"] = sha256_bytes(
        changed_gate
    )
    changed_history = canonical_json_bytes(history)
    history_path.write_bytes(changed_history)
    summary_path = campaign_path / "campaign_summary.json"
    summary = json.loads(summary_path.read_bytes())
    summary["artifacts"]["gate_history.json"] = sha256_bytes(changed_history)
    summary_path.write_bytes(canonical_json_bytes(summary))

    report = verify_campaign(artifacts, COMPARED_CANDIDATE_ID)

    assert report.ok is False
    assert "campaign_summary_facts_mismatch" in {
        issue.code for issue in report.issues
    }


def test_orphan_gate_snapshot_directory_is_rejected(
    compared_campaigns: Path,
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    shutil.copytree(compared_campaigns, artifacts)
    campaign_path = artifacts / COMPARED_CANDIDATE_ID
    history = json.loads((campaign_path / "gate_history.json").read_bytes())
    source = campaign_path / "gates" / history["current_gate_id"]
    shutil.copytree(source, campaign_path / "gates" / "ocgate_orphan")

    report = verify_campaign(artifacts, COMPARED_CANDIDATE_ID)

    assert report.ok is False
    assert "gate_history_set_mismatch" in {issue.code for issue in report.issues}


def _mutate_failure_count(summary: dict[str, object]) -> None:
    summary["failure_count"] = int(summary["failure_count"]) + 1


def _mutate_gate_metric(summary: dict[str, object]) -> None:
    gate_input = summary["gate_input"]
    assert isinstance(gate_input, dict)
    metrics = gate_input["metrics"]
    assert isinstance(metrics, dict)
    metrics["task_success_pass_count"] = int(metrics["task_success_pass_count"]) - 1


def _mutate_gate_result_group(summary: dict[str, object]) -> None:
    gate_input = summary["gate_input"]
    assert isinstance(gate_input, dict)
    metrics = gate_input["metrics"]
    assert isinstance(metrics, dict)
    groups = metrics["result_groups"]
    assert isinstance(groups, list)
    group = groups[0]
    assert isinstance(group, dict)
    results = group["deterministic_results"]
    assert isinstance(results, list)
    result = results[0]
    assert isinstance(result, dict)
    result["mis_evaluation_id"] = "evl_forged_mapping"


def _mutate_run_ids(summary: dict[str, object]) -> None:
    run_ids = summary["run_ids"]
    assert isinstance(run_ids, list)
    run_ids.pop()


def _mutate_manifest_ids(summary: dict[str, object]) -> None:
    manifest_ids = summary["manifest_ids"]
    assert isinstance(manifest_ids, list)
    manifest_ids[0] = "ocmanifest_forged"


def _mutate_scenario_ids(summary: dict[str, object]) -> None:
    scenario_ids = summary["scenario_ids"]
    assert isinstance(scenario_ids, list)
    scenario_ids[0] = "scenario.forged"


def _mutate_git_commit(summary: dict[str, object]) -> None:
    summary["git_commit_sha"] = "f" * 40


def _mutate_mis_task_mapping(summary: dict[str, object]) -> None:
    summary["mis_task_id"] = "tsk_forged"


def _mutate_plan_evidence_mapping(summary: dict[str, object]) -> None:
    plan_evidence = summary["plan_evidence"]
    assert isinstance(plan_evidence, list)
    row = plan_evidence[0]
    assert isinstance(row, dict)
    row["status"] = "forged"


def _mutate_comparison(summary: dict[str, object]) -> None:
    summary["comparison"] = {
        "schema_version": 1,
        "baseline_campaign_id": "occampaign_forged",
        "candidate_campaign_id": CAMPAIGN_ID,
    }


def _mutate_agent_name(summary: dict[str, object]) -> None:
    agent = summary["agent"]
    assert isinstance(agent, dict)
    agent["name"] = "Forged Appointment Agent"


@pytest.mark.parametrize(
    "mutation",
    [
        _mutate_failure_count,
        _mutate_gate_metric,
        _mutate_gate_result_group,
        _mutate_run_ids,
        _mutate_manifest_ids,
        _mutate_scenario_ids,
        _mutate_git_commit,
        _mutate_mis_task_mapping,
        _mutate_plan_evidence_mapping,
        _mutate_comparison,
        _mutate_agent_name,
    ],
    ids=[
        "failure-count",
        "gate-metrics",
        "gate-result-groups",
        "run-ids",
        "manifest-ids",
        "scenario-ids",
        "git-commit",
        "mis-task-mapping",
        "plan-evidence-mapping",
        "comparison",
        "agent-name",
    ],
)
def test_campaign_verification_rejects_forged_summary_facts(
    governed_campaign: Path,
    tmp_path: Path,
    mutation: Callable[[dict[str, object]], None],
) -> None:
    artifacts = tmp_path / "artifacts"
    shutil.copytree(governed_campaign, artifacts)
    summary_path = artifacts / CAMPAIGN_ID / "campaign_summary.json"
    envelope = json.loads(summary_path.read_bytes())
    summary = envelope["summary"]
    assert isinstance(summary, dict)
    mutation(summary)
    summary_path.write_bytes(canonical_json_bytes(envelope))

    report = verify_campaign(artifacts, CAMPAIGN_ID)

    assert report.ok is False
    assert "campaign_summary_facts_mismatch" in {
        issue.code for issue in report.issues
    }


def test_verified_campaign_snapshot_cannot_be_replaced_after_verification(
    governed_campaign: Path,
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    shutil.copytree(governed_campaign, artifacts)
    report = verify_campaign(artifacts, CAMPAIGN_ID)
    original = verified_campaign_json(report, "campaign_summary.json")
    assert isinstance(original, dict)
    original_failure_count = original["summary"]["failure_count"]

    summary_path = artifacts / CAMPAIGN_ID / "campaign_summary.json"
    forged = json.loads(summary_path.read_bytes())
    forged["summary"]["failure_count"] = original_failure_count + 1
    summary_path.write_bytes(canonical_json_bytes(forged))

    hydrated = verified_campaign_json(report, "campaign_summary.json")
    assert hydrated["summary"]["failure_count"] == original_failure_count
    assert verify_campaign(artifacts, CAMPAIGN_ID).ok is False


def test_governed_run_mappings_cannot_be_downgraded_to_arbitrary_summary(
    governed_campaign: Path,
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    shutil.copytree(governed_campaign, artifacts)
    summary_path = artifacts / CAMPAIGN_ID / "campaign_summary.json"
    original = json.loads(summary_path.read_bytes())
    downgraded = {
        "schema_version": 1,
        "campaign_id": CAMPAIGN_ID,
        "summary": {"run_count": 10, "pass_rate": 1.0},
        "artifacts": original["artifacts"],
    }
    summary_path.write_bytes(canonical_json_bytes(downgraded))

    report = verify_campaign(artifacts, CAMPAIGN_ID)

    assert report.ok is False
    assert "governed_campaign_summary_required" in {
        issue.code for issue in report.issues
    }


def test_compared_campaign_requires_existing_verified_baseline(
    compared_campaigns: Path,
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    shutil.copytree(compared_campaigns, artifacts)
    shutil.rmtree(artifacts / BASELINE_ID)

    report = verify_campaign(artifacts, COMPARED_CANDIDATE_ID)

    assert report.ok is False
    assert "campaign_baseline_unverified" in {issue.code for issue in report.issues}


@pytest.mark.parametrize("mutation", ["metrics", "run_ids", "scenario_ids"])
def test_compared_campaign_rejects_tampered_baseline_facts(
    compared_campaigns: Path,
    tmp_path: Path,
    mutation: str,
) -> None:
    artifacts = tmp_path / "artifacts"
    shutil.copytree(compared_campaigns, artifacts)
    summary_path = artifacts / BASELINE_ID / "campaign_summary.json"
    baseline = json.loads(summary_path.read_bytes())
    summary = baseline["summary"]
    if mutation == "metrics":
        summary["gate_input"]["metrics"]["task_success_pass_count"] -= 1
    elif mutation == "run_ids":
        summary["run_ids"].pop()
    else:
        summary["scenario_ids"][0] = "scenario.forged"
    summary_path.write_bytes(canonical_json_bytes(baseline))

    assert verify_campaign(artifacts, BASELINE_ID).ok is False
    candidate = verify_campaign(artifacts, COMPARED_CANDIDATE_ID)
    assert candidate.ok is False
    assert "campaign_baseline_unverified" in {
        issue.code for issue in candidate.issues
    }


@pytest.mark.parametrize(
    "field",
    ["name", "original_input", "expected", "observed", "evidence_refs", "created_at"],
)
def test_regression_cases_are_fully_anchored_to_verified_run_facts(
    compared_campaigns: Path,
    tmp_path: Path,
    field: str,
) -> None:
    artifacts = tmp_path / "artifacts"
    shutil.copytree(compared_campaigns, artifacts)
    campaign_path = artifacts / BASELINE_ID
    regressions_path = campaign_path / "regression_cases.json"
    regressions = json.loads(regressions_path.read_bytes())
    assert regressions
    regression = regressions[0]
    if field == "name":
        regression[field] = "Forged regression name"
    elif field == "original_input":
        regression[field]["initial_message"] = "Forged input"
    elif field == "expected":
        regression[field] = {"evaluation_status": "pass", "final_state": {}}
    elif field == "observed":
        regression[field] = {"evaluation_status": "fail", "final_state": {}}
    elif field == "evidence_refs":
        regression[field].append("artifact:transcript.json")
    else:
        regression[field] = "2026-08-11T00:00:00Z"
    changed = canonical_json_bytes(regressions)
    regressions_path.write_bytes(changed)
    summary_path = campaign_path / "campaign_summary.json"
    summary = json.loads(summary_path.read_bytes())
    summary["artifacts"]["regression_cases.json"] = sha256_bytes(changed)
    summary_path.write_bytes(canonical_json_bytes(summary))

    report = verify_campaign(artifacts, BASELINE_ID)

    assert report.ok is False
    assert "campaign_summary_facts_mismatch" in {
        issue.code for issue in report.issues
    }


@pytest.mark.parametrize(
    "field",
    ["candidate_campaign_id", "candidate_metrics", "delta"],
)
def test_comparison_diff_is_reconstructed_in_full(
    compared_campaigns: Path,
    tmp_path: Path,
    field: str,
) -> None:
    artifacts = tmp_path / "artifacts"
    shutil.copytree(compared_campaigns, artifacts)
    campaign_path = artifacts / COMPARED_CANDIDATE_ID
    diff_path = campaign_path / "baseline_candidate_diff.json"
    diff = json.loads(diff_path.read_bytes())
    if field == "candidate_campaign_id":
        diff[field] = "occampaign_forged"
    elif field == "candidate_metrics":
        diff[field]["task_success_pass_count"] -= 1
    else:
        diff[field]["task_success_percentage_points"] = 99.0
    changed = canonical_json_bytes(diff)
    diff_path.write_bytes(changed)
    summary_path = campaign_path / "campaign_summary.json"
    summary = json.loads(summary_path.read_bytes())
    summary["summary"]["comparison"] = diff
    summary["artifacts"]["baseline_candidate_diff.json"] = sha256_bytes(changed)
    summary_path.write_bytes(canonical_json_bytes(summary))

    report = verify_campaign(artifacts, COMPARED_CANDIDATE_ID)

    assert report.ok is False
    assert "campaign_summary_facts_mismatch" in {
        issue.code for issue in report.issues
    }


def test_path_unsafe_comparison_baseline_is_a_safe_verification_failure(
    compared_campaigns: Path,
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    shutil.copytree(compared_campaigns, artifacts)
    campaign_path = artifacts / COMPARED_CANDIDATE_ID
    diff_path = campaign_path / "baseline_candidate_diff.json"
    diff = json.loads(diff_path.read_bytes())
    diff["baseline_campaign_id"] = "C:evil"
    changed = canonical_json_bytes(diff)
    diff_path.write_bytes(changed)
    summary_path = campaign_path / "campaign_summary.json"
    summary = json.loads(summary_path.read_bytes())
    summary["summary"]["comparison"] = diff
    summary["artifacts"]["baseline_candidate_diff.json"] = sha256_bytes(changed)
    summary_path.write_bytes(canonical_json_bytes(summary))

    report = verify_campaign(artifacts, COMPARED_CANDIDATE_ID)

    assert report.ok is False
    assert "campaign_baseline_unverified" in {
        issue.code for issue in report.issues
    }


def test_comparison_cycle_fails_without_recursive_verification(
    compared_campaigns: Path,
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    shutil.copytree(compared_campaigns, artifacts)
    campaign_path = artifacts / COMPARED_CANDIDATE_ID
    diff_path = campaign_path / "baseline_candidate_diff.json"
    diff = json.loads(diff_path.read_bytes())
    diff["baseline_campaign_id"] = COMPARED_CANDIDATE_ID
    changed = canonical_json_bytes(diff)
    diff_path.write_bytes(changed)
    summary_path = campaign_path / "campaign_summary.json"
    summary = json.loads(summary_path.read_bytes())
    summary["summary"]["comparison"] = diff
    summary["artifacts"]["baseline_candidate_diff.json"] = sha256_bytes(changed)
    summary_path.write_bytes(canonical_json_bytes(summary))

    report = verify_campaign(artifacts, COMPARED_CANDIDATE_ID)

    assert report.ok is False
    assert "campaign_baseline_unverified" in {
        issue.code for issue in report.issues
    }
