from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
SCENARIO_SUITE = REPO_ROOT / "examples" / "open-cekura" / "scenarios"


def test_campaign_run_contract_defaults_to_candidate_version() -> None:
    from open_cekura.cli.main import build_parser

    args = build_parser().parse_args(
        [
            "campaign",
            "run",
            "--suite",
            str(SCENARIO_SUITE),
            "--agent",
            "mock",
        ]
    )

    assert args.version == "candidate"


def _run_cli(
    *args: str,
    env: dict[str, str],
    expected_code: int,
) -> dict[str, object]:
    completed = subprocess.run(
        [sys.executable, "-m", "open_cekura.cli.main", *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=90,
        check=False,
        shell=False,
    )
    assert completed.returncode == expected_code, (
        f"stdout={completed.stdout}\nstderr={completed.stderr}"
    )
    stream = completed.stdout if expected_code in {0, 3, 4} else completed.stderr
    payload = json.loads(stream)
    assert payload["token_omitted"] is True
    return payload


def test_campaign_cli_closes_cross_process_mis_evidence_compare_and_gate(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "state with spaces" / "agentops reliability.db"
    artifacts = tmp_path / "artifacts with spaces" / "open-cekura"
    env = os.environ.copy()
    env["OPENAI_API_KEY"] = "CLI_SECRET_MUST_NOT_APPEAR"
    env["AGENTOPS_DB_PATH"] = str(db_path)
    # IDs deliberately contradict the selected defect profiles.  Outcomes must
    # come from observations/evaluators, never locator text.
    baseline_id = "occampaign_cli_name_says_candidate"
    candidate_id = "occampaign_cli_name_says_baseline"

    baseline = _run_cli(
        "campaign",
        "run",
        "--suite",
        str(SCENARIO_SUITE),
        "--agent",
        "mock",
        "--version",
        "baseline",
        "--campaign-id",
        baseline_id,
        "--artifacts",
        str(artifacts),
        env=env,
        expected_code=0,
    )
    candidate = _run_cli(
        "campaign",
        "run",
        "--suite",
        str(SCENARIO_SUITE),
        "--agent",
        "mock",
        "--version",
        "candidate",
        "--campaign-id",
        candidate_id,
        "--artifacts",
        str(artifacts),
        env=env,
        expected_code=0,
    )

    assert baseline["campaign_id"] == baseline_id
    assert baseline["release_gate"]["decision"] == "block"
    assert candidate["campaign_id"] == candidate_id
    assert candidate["release_gate"]["decision"] == "pass"
    assert baseline["run_count"] == candidate["run_count"] == 10
    assert "CLI_SECRET_MUST_NOT_APPEAR" not in json.dumps(
        [baseline, candidate], ensure_ascii=False
    )

    comparison = _run_cli(
        "campaign",
        "compare",
        "--baseline",
        baseline_id,
        "--candidate",
        candidate_id,
        "--artifacts",
        str(artifacts),
        env=env,
        expected_code=0,
    )
    assert comparison["release_gate"]["decision"] == "pass"
    assert comparison["release_gate"]["baseline_campaign_id"] == baseline_id

    blocked = _run_cli(
        "gate",
        "evaluate",
        "--campaign",
        baseline_id,
        "--artifacts",
        str(artifacts),
        env=env,
        expected_code=3,
    )
    passed = _run_cli(
        "gate",
        "evaluate",
        "--campaign",
        candidate_id,
        "--artifacts",
        str(artifacts),
        env=env,
        expected_code=0,
    )
    assert blocked["release_gate"]["decision"] == "block"
    assert {blocker["rule_id"] for blocker in blocked["release_gate"]["blockers"]} >= {
        "zero_tolerance.duplicate_mutation.v1",
        "zero_tolerance.confirmation_before_mutation.v1",
    }
    assert passed["release_gate"]["decision"] == "pass"

    candidate_diff_path = artifacts / candidate_id / "baseline_candidate_diff.json"
    candidate_gate_path = artifacts / candidate_id / "release_gate.json"
    comparison_bytes = candidate_diff_path.read_bytes()
    comparison_gate_bytes = candidate_gate_path.read_bytes()
    replay = _run_cli(
        "campaign",
        "run",
        "--suite",
        str(SCENARIO_SUITE),
        "--agent",
        "mock",
        "--version",
        "candidate",
        "--campaign-id",
        candidate_id,
        "--artifacts",
        str(artifacts),
        env=env,
        expected_code=0,
    )
    assert replay["idempotent_replay"] is True
    assert replay["release_gate"]["baseline_campaign_id"] == baseline_id
    assert candidate_diff_path.read_bytes() == comparison_bytes
    assert candidate_gate_path.read_bytes() == comparison_gate_bytes

    for campaign_id in (baseline_id, candidate_id):
        verified = _run_cli(
            "evidence",
            "verify",
            "--campaign",
            campaign_id,
            "--artifacts",
            str(artifacts),
            env=env,
            expected_code=0,
        )
        assert verified["verified"] is True
        assert verified["run_count"] == 10

    with sqlite3.connect(db_path) as conn:
        assert (
            conn.execute("SELECT COUNT(*) FROM reliability_campaigns").fetchone()[0]
            == 2
        )
        assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 2
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM reliability_evidence_manifests"
            ).fetchone()[0]
            == 20
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM reliability_evidence_manifests "
                "WHERE mis_artifact_id IS NULL"
            ).fetchone()[0]
            == 0
        )
        unavailable_plan_evidence = conn.execute(
            """SELECT s.scenario_id
            FROM reliability_evidence_manifests m
            JOIN reliability_conversation_runs r
              ON r.workspace_id=m.workspace_id AND r.run_id=m.run_id
            JOIN reliability_scenarios s
              ON s.workspace_id=r.workspace_id AND s.scenario_id=r.scenario_id
            WHERE m.mis_plan_evidence_manifest_id IS NULL"""
        ).fetchall()
        assert unavailable_plan_evidence == [
            ("appointment.backend_timeout",),
            ("appointment.backend_timeout",),
        ]
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM tool_calls WHERE status='completed' "
                "AND run_id IN (SELECT mis_run_id FROM reliability_conversation_runs "
                "WHERE scenario_id='appointment.backend_timeout')"
            ).fetchone()[0]
            == 0
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM reliability_release_gates "
                "WHERE mis_approval_id IS NULL"
            ).fetchone()[0]
            == 0
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM memories WHERE review_status!='candidate'"
            ).fetchone()[0]
            == 0
        )

    transcript = next((artifacts / candidate_id).glob("*/transcript.json"))
    transcript.write_text("[]", encoding="utf-8")
    failed_verification = _run_cli(
        "evidence",
        "verify",
        "--campaign",
        candidate_id,
        "--artifacts",
        str(artifacts),
        env=env,
        expected_code=4,
    )
    assert failed_verification["verified"] is False
    assert failed_verification["issues"]


def test_campaign_cli_explicit_db_overrides_environment_and_workspace_is_scoped(
    tmp_path: Path,
) -> None:
    env_db = tmp_path / "wrong.db"
    explicit_db = tmp_path / "selected.db"
    artifacts = tmp_path / "evidence"
    env = os.environ.copy()
    env["AGENTOPS_DB_PATH"] = str(env_db)

    result = _run_cli(
        "campaign",
        "run",
        "--suite",
        str(SCENARIO_SUITE),
        "--agent",
        "mock",
        "--version",
        "candidate",
        "--campaign-id",
        "occampaign_cli_explicit_db",
        "--workspace",
        "workspace-cli",
        "--db",
        str(explicit_db),
        "--artifacts",
        str(artifacts),
        env=env,
        expected_code=0,
    )

    assert result["workspace_id"] == "workspace-cli"
    assert explicit_db.is_file()
    assert not env_db.exists()
    with sqlite3.connect(explicit_db) as conn:
        assert (
            conn.execute("SELECT workspace_id FROM reliability_campaigns").fetchone()[0]
            == "workspace-cli"
        )

    replay = _run_cli(
        "campaign",
        "run",
        "--suite",
        str(SCENARIO_SUITE),
        "--agent",
        "mock",
        "--version",
        "candidate",
        "--campaign-id",
        "occampaign_cli_explicit_db",
        "--workspace",
        "workspace-cli",
        "--db",
        str(explicit_db),
        "--artifacts",
        str(artifacts),
        env=env,
        expected_code=0,
    )
    assert replay["campaign_id"] == result["campaign_id"]
    with sqlite3.connect(explicit_db) as conn:
        assert (
            conn.execute("SELECT COUNT(*) FROM reliability_campaigns").fetchone()[0]
            == 1
        )
        assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 1
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM reliability_evidence_manifests"
            ).fetchone()[0]
            == 10
        )


@pytest.mark.parametrize(
    "conflicting_arguments",
    [
        ("--version", "candidate", "--workspace", "workspace-a"),
        ("--version", "baseline", "--workspace", "workspace-b"),
    ],
)
def test_existing_campaign_id_cannot_rebind_or_rewrite_historical_evidence(
    tmp_path: Path,
    conflicting_arguments: tuple[str, ...],
) -> None:
    artifacts = tmp_path / "immutable evidence"
    first_db = tmp_path / "first.db"
    conflicting_db = tmp_path / "conflicting.db"
    env = os.environ.copy()
    campaign_id = "occampaign_cli_immutable_history"
    _run_cli(
        "campaign",
        "run",
        "--suite",
        str(SCENARIO_SUITE),
        "--agent",
        "mock",
        "--version",
        "baseline",
        "--campaign-id",
        campaign_id,
        "--workspace",
        "workspace-a",
        "--db",
        str(first_db),
        "--artifacts",
        str(artifacts),
        env=env,
        expected_code=0,
    )
    summary_path = artifacts / campaign_id / "campaign_summary.json"
    original_summary = summary_path.read_bytes()

    rejected = _run_cli(
        "campaign",
        "run",
        "--suite",
        str(SCENARIO_SUITE),
        "--agent",
        "mock",
        "--campaign-id",
        campaign_id,
        "--db",
        str(conflicting_db),
        "--artifacts",
        str(artifacts),
        *conflicting_arguments,
        env=env,
        expected_code=2,
    )

    assert rejected["error"] == "campaign_id_conflict"
    assert summary_path.read_bytes() == original_summary
    assert not conflicting_db.exists()


def test_existing_campaign_requires_its_authoritative_ledger_for_replay(
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "historical evidence"
    ledger = tmp_path / "authoritative.db"
    fresh_db = tmp_path / "fresh.db"
    env = os.environ.copy()
    baseline_id = "occampaign_cli_history_baseline"
    candidate_id = "occampaign_cli_history_candidate"
    for campaign_id, version in (
        (baseline_id, "baseline"),
        (candidate_id, "candidate"),
    ):
        _run_cli(
            "campaign",
            "run",
            "--suite",
            str(SCENARIO_SUITE),
            "--agent",
            "mock",
            "--version",
            version,
            "--campaign-id",
            campaign_id,
            "--db",
            str(ledger),
            "--artifacts",
            str(artifacts),
            env=env,
            expected_code=0,
        )
    _run_cli(
        "campaign",
        "compare",
        "--baseline",
        baseline_id,
        "--candidate",
        candidate_id,
        "--db",
        str(ledger),
        "--artifacts",
        str(artifacts),
        env=env,
        expected_code=0,
    )
    candidate_dir = artifacts / candidate_id
    original_files = {
        path.relative_to(candidate_dir): path.read_bytes()
        for path in candidate_dir.rglob("*")
        if path.is_file()
    }

    rejected = _run_cli(
        "campaign",
        "run",
        "--suite",
        str(SCENARIO_SUITE),
        "--agent",
        "mock",
        "--version",
        "candidate",
        "--campaign-id",
        candidate_id,
        "--db",
        str(fresh_db),
        "--artifacts",
        str(artifacts),
        env=env,
        expected_code=2,
    )

    assert rejected["error"] == "campaign_ledger_missing"
    assert not fresh_db.exists()
    assert {
        path.relative_to(candidate_dir): path.read_bytes()
        for path in candidate_dir.rglob("*")
        if path.is_file()
    } == original_files


@pytest.mark.parametrize(
    "corruption_sql",
    [
        "UPDATE artifacts SET content_hash='" + ("0" * 64) + "'",
        "UPDATE plan_evidence_manifests SET status='blocked' "
        "WHERE status='verified'",
        "UPDATE approvals SET decision='rejected' "
        "WHERE subject_type='reliability_release_gate'",
        "UPDATE approvals SET approver_user_id='usr_founder' "
        "WHERE subject_type='reliability_release_gate'",
    ],
)
def test_idempotent_replay_rejects_corrupted_mis_evidence_authority(
    tmp_path: Path,
    corruption_sql: str,
) -> None:
    database = tmp_path / "authority.db"
    artifacts = tmp_path / "evidence"
    env = os.environ.copy()
    campaign_id = "occampaign_cli_authority_integrity"
    command = (
        "campaign",
        "run",
        "--suite",
        str(SCENARIO_SUITE),
        "--agent",
        "mock",
        "--version",
        "candidate",
        "--campaign-id",
        campaign_id,
        "--db",
        str(database),
        "--artifacts",
        str(artifacts),
    )
    _run_cli(*command, env=env, expected_code=0)
    campaign_dir = artifacts / campaign_id
    original_files = {
        path.relative_to(campaign_dir): path.read_bytes()
        for path in campaign_dir.rglob("*")
        if path.is_file()
    }
    with sqlite3.connect(database) as conn:
        conn.execute(corruption_sql)
        conn.commit()

    rejected = _run_cli(*command, env=env, expected_code=2)

    assert rejected["error"] == "campaign_ledger_incomplete"
    assert {
        path.relative_to(campaign_dir): path.read_bytes()
        for path in campaign_dir.rglob("*")
        if path.is_file()
    } == original_files


def test_compare_and_gate_fail_safely_when_projection_database_is_empty(
    tmp_path: Path,
) -> None:
    source_db = tmp_path / "source.db"
    empty_db = tmp_path / "empty.db"
    artifacts = tmp_path / "evidence"
    env = os.environ.copy()
    baseline_id = "occampaign_cli_empty_db_baseline"
    candidate_id = "occampaign_cli_empty_db_candidate"
    for campaign_id, version in (
        (baseline_id, "baseline"),
        (candidate_id, "candidate"),
    ):
        _run_cli(
            "campaign",
            "run",
            "--suite",
            str(SCENARIO_SUITE),
            "--agent",
            "mock",
            "--version",
            version,
            "--campaign-id",
            campaign_id,
            "--db",
            str(source_db),
            "--artifacts",
            str(artifacts),
            env=env,
            expected_code=0,
        )
    sqlite3.connect(empty_db).close()

    compare = _run_cli(
        "campaign",
        "compare",
        "--baseline",
        baseline_id,
        "--candidate",
        candidate_id,
        "--db",
        str(empty_db),
        "--artifacts",
        str(artifacts),
        env=env,
        expected_code=2,
    )
    gate = _run_cli(
        "gate",
        "evaluate",
        "--campaign",
        candidate_id,
        "--db",
        str(empty_db),
        "--artifacts",
        str(artifacts),
        env=env,
        expected_code=2,
    )

    assert compare["error"] == "campaign_not_found"
    assert gate["error"] == "campaign_not_found"
    assert "no such table" not in json.dumps([compare, gate]).lower()
