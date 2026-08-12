from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from open_cekura.campaigns import service
from open_cekura.evidence.manifest import canonical_json_bytes
from open_cekura.tests.integration.test_regression_campaign_replay import (
    REPLAY_CAMPAIGN_ID,
    SCENARIO_SUITE,
    SOURCE_CAMPAIGN_ID,
    WORKSPACE_ID,
    _copy_authority,
)


@pytest.fixture(scope="module")
def baseline_authority(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[Path, Path]:
    root = tmp_path_factory.mktemp("regression-replay-governance-source")
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
    return database, artifacts


@pytest.fixture(scope="module")
def replay_authority(
    baseline_authority: tuple[Path, Path],
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[Path, Path]:
    root = tmp_path_factory.mktemp("regression-replay-governance-target")
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


def test_governed_verify_rejects_a_missing_target_replay_mapping(
    replay_authority: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    database, artifacts = _copy_authority(replay_authority, tmp_path)
    with sqlite3.connect(database) as connection:
        deleted = connection.execute(
            """DELETE FROM reliability_regression_replays
            WHERE workspace_id=? AND target_campaign_id=?
            AND mapping_id=(
                SELECT mapping_id FROM reliability_regression_replays
                WHERE workspace_id=? AND target_campaign_id=?
                ORDER BY mapping_id LIMIT 1
            )""",
            (
                WORKSPACE_ID,
                REPLAY_CAMPAIGN_ID,
                WORKSPACE_ID,
                REPLAY_CAMPAIGN_ID,
            ),
        ).rowcount
        connection.commit()
    assert deleted == 1

    with pytest.raises(service.CampaignServiceError) as rejected:
        service.verify_campaign_evidence(
            campaign_id=REPLAY_CAMPAIGN_ID,
            workspace_id=WORKSPACE_ID,
            db_path=database,
            artifact_root=artifacts,
            strict=True,
        )

    assert rejected.value.code == "campaign_ledger_mismatch"


def test_ordinary_candidate_has_canonical_null_replay_provenance(
    baseline_authority: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    database, artifacts = _copy_authority(baseline_authority, tmp_path)
    campaign_id = "occampaign_ordinary_candidate_governance"

    result = service.run_campaign(
        suite_path=SCENARIO_SUITE,
        agent="mock",
        version="candidate",
        campaign_id=campaign_id,
        workspace_id=WORKSPACE_ID,
        db_path=database,
        artifact_root=artifacts,
    )

    campaign_dir = artifacts / campaign_id
    summary = json.loads((campaign_dir / "campaign_summary.json").read_bytes())
    assert result["release_gate"]["decision"] == "pass"
    assert (campaign_dir / "regression_replay.json").read_bytes() == (
        canonical_json_bytes(None)
    )
    assert summary["summary"]["regression_replay"] is None
    with sqlite3.connect(database) as connection:
        mapping_count = connection.execute(
            """SELECT COUNT(*) FROM reliability_regression_replays
            WHERE workspace_id=? AND target_campaign_id=?""",
            (WORKSPACE_ID, campaign_id),
        ).fetchone()[0]
    assert mapping_count == 0


def test_gate_rewrites_preserve_replay_provenance_and_governed_verification(
    replay_authority: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    database, artifacts = _copy_authority(replay_authority, tmp_path)
    campaign_dir = artifacts / REPLAY_CAMPAIGN_ID
    replay_path = campaign_dir / "regression_replay.json"
    summary_path = campaign_dir / "campaign_summary.json"
    replay_bytes = replay_path.read_bytes()
    replay_pointer = json.loads(summary_path.read_bytes())["summary"][
        "regression_replay"
    ]
    peer_ids = (
        "occampaign_replay_governance_peer_a",
        "occampaign_replay_governance_peer_b",
    )
    for peer_id in peer_ids:
        service.replay_campaign_regressions(
            source_campaign_id=SOURCE_CAMPAIGN_ID,
            version="candidate",
            replay_campaign_id=peer_id,
            workspace_id=WORKSPACE_ID,
            db_path=database,
            artifact_root=artifacts,
        )

    evaluated = service.evaluate_campaign_gate(
        campaign_id=REPLAY_CAMPAIGN_ID,
        baseline_campaign_id=peer_ids[0],
        workspace_id=WORKSPACE_ID,
        db_path=database,
        artifact_root=artifacts,
    )
    assert evaluated["release_gate"]["decision"] == "pass"
    assert replay_path.read_bytes() == replay_bytes
    assert json.loads(summary_path.read_bytes())["summary"][
        "regression_replay"
    ] == replay_pointer

    compared = service.compare_campaigns(
        baseline_campaign_id=peer_ids[1],
        candidate_campaign_id=REPLAY_CAMPAIGN_ID,
        workspace_id=WORKSPACE_ID,
        db_path=database,
        artifact_root=artifacts,
    )
    assert compared["release_gate"]["decision"] == "pass"
    assert replay_path.read_bytes() == replay_bytes
    assert json.loads(summary_path.read_bytes())["summary"][
        "regression_replay"
    ] == replay_pointer

    verification = service.verify_campaign_evidence(
        campaign_id=REPLAY_CAMPAIGN_ID,
        workspace_id=WORKSPACE_ID,
        db_path=database,
        artifact_root=artifacts,
        strict=True,
    )
    assert verification["verified"] is True
    assert verification["authority_verified"] is True
