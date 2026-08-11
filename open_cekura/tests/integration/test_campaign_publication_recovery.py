from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path

import pytest

from open_cekura.api.routes import handle_get
from open_cekura.campaigns import service as campaign_service
from open_cekura.campaigns.service import (
    CampaignServiceError,
    compare_campaigns,
    run_campaign,
)
from open_cekura.evidence import bundle as evidence_bundle
from open_cekura.evidence import manifest as evidence_manifest
from open_cekura.evidence import publication
from open_cekura.evidence.manifest import verify_campaign
from open_cekura.evidence.publication import (
    PUBLICATION_ROOT_NAME,
    campaign_tree_sha256,
    load_pending_publication,
    publication_slot,
)
from open_cekura.storage.sqlite_repository import SQLiteRepository


REPO_ROOT = Path(__file__).resolve().parents[3]
SCENARIO_SUITE = REPO_ROOT / "examples" / "open-cekura" / "scenarios"
WORKSPACE_ID = "default"
REFERENCE_ROOT_ID = "occampaign_publication_reference_root"
BASELINE_ONE_ID = "occampaign_publication_baseline_one"
BASELINE_TWO_ID = "occampaign_publication_baseline_two"
CANDIDATE_ID = "occampaign_publication_candidate"


class InjectedPublicationCrash(RuntimeError):
    pass


def _publication_ids(database: Path, campaign_id: str) -> set[str]:
    with closing(sqlite3.connect(database)) as connection:
        return {
            row[0]
            for row in connection.execute(
                """SELECT publication_id
                FROM reliability_evidence_publications
                WHERE workspace_id=? AND campaign_id=?""",
                (WORKSPACE_ID, campaign_id),
            )
        }


def test_publication_rejects_reparse_private_root_before_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    private_root = artifacts / publication.PUBLICATION_ROOT_NAME
    private_root.mkdir()
    real_publication_check = publication.is_symlink_or_reparse
    real_manifest_check = evidence_manifest.is_symlink_or_reparse

    def simulated_publication_check(path: Path) -> bool:
        candidate = Path(path)
        return candidate == private_root or real_publication_check(candidate)

    def simulated_manifest_check(path: Path) -> bool:
        candidate = Path(path)
        return candidate == private_root or real_manifest_check(candidate)

    monkeypatch.setattr(
        publication,
        "is_symlink_or_reparse",
        simulated_publication_check,
    )
    monkeypatch.setattr(
        evidence_manifest,
        "is_symlink_or_reparse",
        simulated_manifest_check,
    )

    with pytest.raises(evidence_manifest.EvidencePathError):
        publication.begin_publication(
            artifacts,
            authority_id="authority-a",
            workspace_id="workspace-a",
            campaign_id="campaign-a",
            gate_id="gate-a",
            publication_id="publication-a",
            expected_previous_tree_sha256=None,
        )

    assert not publication.publication_slot(
        artifacts,
        workspace_id="workspace-a",
        campaign_id="campaign-a",
    ).exists()


def test_publication_lock_covers_final_campaign_namespace(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    writer_a = publication.begin_publication(
        artifacts,
        authority_id="authority-a",
        workspace_id="workspace-a",
        campaign_id="shared-campaign",
        gate_id="gate-a",
        publication_id="publication-a",
        expected_previous_tree_sha256=None,
    )

    with pytest.raises(publication.PublicationError, match="pending"):
        publication.begin_publication(
            artifacts,
            authority_id="authority-b",
            workspace_id="workspace-b",
            campaign_id="shared-campaign",
            gate_id="gate-b",
            publication_id="publication-b",
            expected_previous_tree_sha256=None,
        )

    writer_a.stage_campaign.mkdir(parents=True)
    (writer_a.stage_campaign / "marker.bin").write_bytes(b"committed-a")
    writer_a = publication.seal_publication(writer_a)
    publication.swap_publication_to_final(writer_a)
    assert (writer_a.final_campaign / "marker.bin").read_bytes() == b"committed-a"


@pytest.mark.skipif(
    os.path.normcase("Campaign-A") != os.path.normcase("campaign-a"),
    reason="filesystem case aliases are a Windows lock-domain contract",
)
def test_publication_lock_normalizes_windows_case_aliases(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    publication.begin_publication(
        artifacts,
        authority_id="authority-a",
        workspace_id="workspace-a",
        campaign_id="Shared-Campaign",
        gate_id="gate-a",
        publication_id="publication-a",
        expected_previous_tree_sha256=None,
    )

    with pytest.raises(publication.PublicationError, match="pending"):
        publication.begin_publication(
            artifacts,
            authority_id="authority-b",
            workspace_id="workspace-b",
            campaign_id="shared-campaign",
            gate_id="gate-b",
            publication_id="publication-b",
            expected_previous_tree_sha256=None,
        )


def test_missing_database_preserves_sealed_publication_and_final(
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    missing_database = tmp_path / "missing.db"
    pending = publication.begin_publication(
        artifacts,
        authority_id="authority-a",
        workspace_id="workspace-a",
        campaign_id="campaign-a",
        gate_id="gate-a",
        publication_id="publication-a",
        expected_previous_tree_sha256=None,
    )
    pending.stage_campaign.mkdir(parents=True)
    (pending.stage_campaign / "marker.bin").write_bytes(b"possibly-committed")
    pending = publication.seal_publication(pending)
    publication.swap_publication_to_final(pending)
    expected_hash = publication.campaign_tree_sha256(pending.final_campaign)
    publication.release_publication_claim(pending)

    with pytest.raises(CampaignServiceError) as raised:
        campaign_service._recover_campaign_publication_entry(
            artifact_root=artifacts,
            db_path=missing_database,
            workspace_id="workspace-a",
            campaign_id="campaign-a",
        )

    assert raised.value.code == "publication_recovery_authority_unavailable"
    assert not missing_database.exists()
    assert pending.journal_path.is_file()
    assert publication.load_pending_publication(
        artifacts,
        workspace_id="workspace-a",
        campaign_id="campaign-a",
    ) is not None
    assert publication.campaign_tree_sha256(pending.final_campaign) == expected_hash


def test_same_path_replacement_database_cannot_recover_foreign_journal(
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    database = tmp_path / "authority.db"
    connection, _ = campaign_service._open_mis_database(database)
    try:
        repository = SQLiteRepository(connection, workspace_id="workspace-a")
        repository.initialize_schema()
        original_authority_id = repository.publication_authority_id()
    finally:
        connection.close()

    pending = publication.begin_publication(
        artifacts,
        authority_id=original_authority_id,
        workspace_id="workspace-a",
        campaign_id="campaign-a",
        gate_id="gate-a",
        publication_id="publication-a",
        expected_previous_tree_sha256=None,
    )
    pending.stage_campaign.mkdir(parents=True)
    (pending.stage_campaign / "marker.bin").write_bytes(b"possibly-committed")
    pending = publication.seal_publication(pending)
    publication.swap_publication_to_final(pending)
    expected_hash = publication.campaign_tree_sha256(pending.final_campaign)
    publication.release_publication_claim(pending)

    database.unlink()
    replacement, _ = campaign_service._open_mis_database(database)
    try:
        replacement_repository = SQLiteRepository(
            replacement,
            workspace_id="workspace-a",
        )
        replacement_repository.initialize_schema()
        assert replacement_repository.publication_authority_id() != original_authority_id
    finally:
        replacement.close()

    with pytest.raises(CampaignServiceError) as raised:
        campaign_service._recover_campaign_publication_entry(
            artifact_root=artifacts,
            db_path=database,
            workspace_id="workspace-a",
            campaign_id="campaign-a",
        )

    assert raised.value.code == "publication_recovery_failed"
    assert pending.journal_path.is_file()
    assert publication.campaign_tree_sha256(pending.final_campaign) == expected_hash


def test_hard_exit_before_seal_releases_lock_and_discards_private_stage(
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    database = tmp_path / "missing.db"
    script = """
import os
import sys
from open_cekura.evidence.publication import begin_publication

begin_publication(
    sys.argv[1],
    authority_id="authority-a",
    workspace_id="workspace-a",
    campaign_id="campaign-a",
    gate_id="gate-a",
    publication_id="publication-a",
    expected_previous_tree_sha256=None,
)
os._exit(0)
"""
    subprocess.run(
        [sys.executable, "-c", script, str(artifacts)],
        check=True,
        timeout=30,
    )
    slot = publication.publication_slot(
        artifacts,
        workspace_id="workspace-a",
        campaign_id="campaign-a",
    )
    assert slot.is_dir()
    assert not (slot / publication.PUBLICATION_JOURNAL_NAME).exists()

    campaign_service._recover_campaign_publication_entry(
        artifact_root=artifacts,
        db_path=database,
        workspace_id="workspace-a",
        campaign_id="campaign-a",
    )

    assert not slot.exists()
    assert not database.exists()


def test_active_unsealed_publication_is_not_reclaimed(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    ready = tmp_path / "ready"
    script = """
import os
import sys
from pathlib import Path
from open_cekura.evidence.publication import begin_publication

begin_publication(
    sys.argv[1],
    authority_id="authority-a",
    workspace_id="workspace-a",
    campaign_id="campaign-a",
    gate_id="gate-a",
    publication_id="publication-a",
    expected_previous_tree_sha256=None,
)
Path(sys.argv[2]).write_text("ready", encoding="utf-8")
sys.stdin.buffer.read(1)
os._exit(0)
"""
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(artifacts), str(ready)],
        cwd=REPO_ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 15
        while (
            not ready.exists()
            and process.poll() is None
            and time.monotonic() < deadline
        ):
            time.sleep(0.05)
        error = (
            process.stderr.read().decode("utf-8", errors="replace")
            if process.poll() is not None and process.stderr is not None
            else "child did not signal readiness"
        )
        assert ready.is_file(), error
        with pytest.raises(publication.PublicationError, match="active pending"):
            publication.load_pending_publication(
                artifacts,
                workspace_id="workspace-a",
                campaign_id="campaign-a",
            )
    finally:
        if process.poll() is None and process.stdin is not None:
            try:
                process.stdin.write(b"x")
                process.stdin.flush()
                process.stdin.close()
            except OSError:
                pass
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=15)

    campaign_service._recover_campaign_publication_entry(
        artifact_root=artifacts,
        db_path=tmp_path / "missing.db",
        workspace_id="workspace-a",
        campaign_id="campaign-a",
    )
    assert not publication.publication_slot(
        artifacts,
        workspace_id="workspace-a",
        campaign_id="campaign-a",
    ).exists()


@pytest.fixture(scope="module")
def source_campaigns(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[Path, Path]:
    root = tmp_path_factory.mktemp("campaign-publication-source")
    artifacts = root / "artifacts"
    database = root / "mis.db"
    for campaign_id, version in (
        (REFERENCE_ROOT_ID, "baseline"),
        (BASELINE_ONE_ID, "baseline"),
        (BASELINE_TWO_ID, "baseline"),
        (CANDIDATE_ID, "candidate"),
    ):
        run_campaign(
            suite_path=SCENARIO_SUITE,
            agent="mock",
            version=version,
            campaign_id=campaign_id,
            workspace_id=WORKSPACE_ID,
            db_path=database,
            artifact_root=artifacts,
        )
    return artifacts, database


def _clone_campaigns(
    source: tuple[Path, Path], destination: Path
) -> tuple[Path, Path]:
    source_artifacts, source_database = source
    artifacts = destination / "artifacts"
    database = destination / "mis.db"
    shutil.copytree(source_artifacts, artifacts)
    with (
        closing(sqlite3.connect(source_database)) as source_connection,
        closing(sqlite3.connect(database)) as target_connection,
    ):
        source_connection.backup(target_connection)
    return artifacts, database


def _campaign_database_head(
    database: Path, campaign_id: str
) -> dict[str, tuple[tuple[object, ...], ...]]:
    queries = {
        "gates": """SELECT gate_id,baseline_campaign_id,decision,policy_version,
            mis_approval_id FROM reliability_release_gates
            WHERE workspace_id=? AND campaign_id=? ORDER BY gate_id""",
        "approvals": """SELECT a.approval_id,a.subject_id,a.subject_hash,a.decision
            FROM approvals a JOIN reliability_release_gates g
              ON g.mis_approval_id=a.approval_id
            WHERE g.workspace_id=? AND g.campaign_id=? ORDER BY a.approval_id""",
        "publications": """SELECT publication_id,gate_id,tree_sha256,status,
            published_at FROM reliability_evidence_publications
            WHERE workspace_id=? AND campaign_id=? ORDER BY publication_id""",
    }
    with closing(sqlite3.connect(database)) as connection:
        return {
            name: tuple(
                tuple(row)
                for row in connection.execute(
                    query, (WORKSPACE_ID, campaign_id)
                ).fetchall()
            )
            for name, query in queries.items()
        }


def _assert_campaign_closed(artifacts: Path, campaign_id: str) -> None:
    report = verify_campaign(artifacts, campaign_id)
    assert report.ok, report.issues
    assert not publication_slot(
        artifacts,
        workspace_id=WORKSPACE_ID,
        campaign_id=campaign_id,
    ).exists()


def _assert_api_current_gate(
    database: Path,
    *,
    campaign_id: str,
    expected_gate_id: str,
) -> None:
    with closing(sqlite3.connect(database)) as connection:
        connection.row_factory = sqlite3.Row
        run_row = connection.execute(
            """SELECT run_id FROM reliability_conversation_runs
            WHERE workspace_id=? AND campaign_id=? ORDER BY created_at,run_id LIMIT 1""",
            (WORKSPACE_ID, campaign_id),
        ).fetchone()
        assert run_row is not None
        campaign, campaign_status = handle_get(
            connection,
            path=f"/api/reliability/campaigns/{campaign_id}",
            query={},
            workspace_id=WORKSPACE_ID,
        )
        gates, gates_status = handle_get(
            connection,
            path="/api/reliability/release-gates",
            query={"campaign_id": [campaign_id], "limit": ["100"]},
            workspace_id=WORKSPACE_ID,
        )
        detail, detail_status = handle_get(
            connection,
            path=f"/api/reliability/runs/{run_row['run_id']}",
            query={},
            workspace_id=WORKSPACE_ID,
        )

    assert (campaign_status, gates_status, detail_status) == (200, 200, 200)
    assert campaign["campaign"]["current_gate_id"] == expected_gate_id
    assert [
        gate["gate_id"]
        for gate in gates["release_gates"]
        if gate["is_current"]
    ] == [expected_gate_id]
    run_detail = detail["run_detail"]
    assert run_detail["campaign"]["current_gate_id"] == expected_gate_id
    assert [
        gate["gate_id"]
        for gate in run_detail["release_gates"]
        if gate["is_current"]
    ] == [expected_gate_id]


def test_swap_then_precommit_failure_restores_old_tree_and_ledger(
    source_campaigns: tuple[Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifacts, database = _clone_campaigns(source_campaigns, tmp_path)
    old_tree_hash = campaign_tree_sha256(artifacts / CANDIDATE_ID)
    old_database_head = _campaign_database_head(database, CANDIDATE_ID)
    real_swap = campaign_service.swap_publication_to_final
    injected = False

    def swap_then_crash(publication) -> None:
        nonlocal injected
        real_swap(publication)
        if not injected:
            injected = True
            raise InjectedPublicationCrash("after final swap before SQLite commit")

    monkeypatch.setattr(
        campaign_service,
        "swap_publication_to_final",
        swap_then_crash,
    )

    with pytest.raises(
        InjectedPublicationCrash,
        match="after final swap before SQLite commit",
    ):
        compare_campaigns(
            baseline_campaign_id=BASELINE_ONE_ID,
            candidate_campaign_id=CANDIDATE_ID,
            workspace_id=WORKSPACE_ID,
            db_path=database,
            artifact_root=artifacts,
        )

    assert injected is True
    assert campaign_tree_sha256(artifacts / CANDIDATE_ID) == old_tree_hash
    assert _campaign_database_head(database, CANDIDATE_ID) == old_database_head
    _assert_campaign_closed(artifacts, CANDIDATE_ID)

    retried = compare_campaigns(
        baseline_campaign_id=BASELINE_ONE_ID,
        candidate_campaign_id=CANDIDATE_ID,
        workspace_id=WORKSPACE_ID,
        db_path=database,
        artifact_root=artifacts,
    )

    assert retried["ok"] is True
    assert retried["release_gate"]["baseline_campaign_id"] == BASELINE_ONE_ID
    _assert_campaign_closed(artifacts, CANDIDATE_ID)


def test_postcommit_crash_is_recovered_and_published_on_retry(
    source_campaigns: tuple[Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifacts, database = _clone_campaigns(source_campaigns, tmp_path)
    real_recovery = campaign_service._recover_campaign_publication_in_connection
    injected = False

    def crash_before_mark_published(
        connection,
        *,
        mis,
        repository,
        artifact_root,
        campaign_id,
    ) -> None:
        nonlocal injected
        slot = publication_slot(
            artifact_root,
            workspace_id=repository.workspace_id,
            campaign_id=campaign_id,
        )
        if not injected and slot.exists():
            injected = True
            raise InjectedPublicationCrash(
                "after SQLite commit before publication finalization"
            )
        real_recovery(
            connection,
            mis=mis,
            repository=repository,
            artifact_root=artifact_root,
            campaign_id=campaign_id,
        )

    monkeypatch.setattr(
        campaign_service,
        "_recover_campaign_publication_in_connection",
        crash_before_mark_published,
    )

    with pytest.raises(
        InjectedPublicationCrash,
        match="after SQLite commit before publication finalization",
    ):
        compare_campaigns(
            baseline_campaign_id=BASELINE_ONE_ID,
            candidate_campaign_id=CANDIDATE_ID,
            workspace_id=WORKSPACE_ID,
            db_path=database,
            artifact_root=artifacts,
        )

    pending = load_pending_publication(
        artifacts,
        workspace_id=WORKSPACE_ID,
        campaign_id=CANDIDATE_ID,
    )
    assert injected is True
    assert pending is not None
    assert verify_campaign(artifacts, CANDIDATE_ID).ok is True
    with closing(sqlite3.connect(database)) as connection:
        outbox = connection.execute(
            """SELECT status,published_at FROM reliability_evidence_publications
            WHERE workspace_id=? AND publication_id=?""",
            (WORKSPACE_ID, pending.publication_id),
        ).fetchone()
    assert outbox == ("prepared", None)
    publication_ids_before_retry = _publication_ids(database, CANDIDATE_ID)
    assert pending.publication_id in publication_ids_before_retry

    retried = compare_campaigns(
        baseline_campaign_id=BASELINE_ONE_ID,
        candidate_campaign_id=CANDIDATE_ID,
        workspace_id=WORKSPACE_ID,
        db_path=database,
        artifact_root=artifacts,
    )

    assert retried["ok"] is True
    with closing(sqlite3.connect(database)) as connection:
        recovered_outbox = connection.execute(
            """SELECT status,published_at FROM reliability_evidence_publications
            WHERE workspace_id=? AND publication_id=?""",
            (WORKSPACE_ID, pending.publication_id),
        ).fetchone()
    assert recovered_outbox is not None
    assert recovered_outbox[0] == "published"
    assert recovered_outbox[1] is not None
    assert _publication_ids(database, CANDIDATE_ID) == publication_ids_before_retry
    assert campaign_service.verify_campaign_evidence(
        campaign_id=CANDIDATE_ID,
        artifact_root=artifacts,
    )["ok"] is True
    _assert_campaign_closed(artifacts, CANDIDATE_ID)


def test_staged_alias_failure_never_changes_final_generation(
    source_campaigns: tuple[Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifacts, database = _clone_campaigns(source_campaigns, tmp_path)
    old_tree_hash = campaign_tree_sha256(artifacts / CANDIDATE_ID)
    old_database_head = _campaign_database_head(database, CANDIDATE_ID)
    real_atomic_write = evidence_bundle._atomic_write_bytes
    injected = False

    def fail_current_gate_alias(
        destination: Path,
        content: bytes,
        *,
        artifact_root: Path,
    ) -> None:
        nonlocal injected
        if (
            not injected
            and destination.name == "release_gate.json"
            and PUBLICATION_ROOT_NAME in destination.parts
        ):
            injected = True
            raise InjectedPublicationCrash("during staged current gate alias write")
        real_atomic_write(destination, content, artifact_root=artifact_root)

    monkeypatch.setattr(
        evidence_bundle,
        "_atomic_write_bytes",
        fail_current_gate_alias,
    )

    with pytest.raises(
        InjectedPublicationCrash,
        match="during staged current gate alias write",
    ):
        compare_campaigns(
            baseline_campaign_id=BASELINE_ONE_ID,
            candidate_campaign_id=CANDIDATE_ID,
            workspace_id=WORKSPACE_ID,
            db_path=database,
            artifact_root=artifacts,
        )

    assert injected is True
    assert campaign_tree_sha256(artifacts / CANDIDATE_ID) == old_tree_hash
    assert _campaign_database_head(database, CANDIDATE_ID) == old_database_head
    _assert_campaign_closed(artifacts, CANDIDATE_ID)

    retried = compare_campaigns(
        baseline_campaign_id=BASELINE_ONE_ID,
        candidate_campaign_id=CANDIDATE_ID,
        workspace_id=WORKSPACE_ID,
        db_path=database,
        artifact_root=artifacts,
    )

    assert retried["ok"] is True
    _assert_campaign_closed(artifacts, CANDIDATE_ID)


def test_multiple_baselines_stage_transitive_gate_reference_closure(
    source_campaigns: tuple[Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifacts, database = _clone_campaigns(source_campaigns, tmp_path)
    compare_campaigns(
        baseline_campaign_id=REFERENCE_ROOT_ID,
        candidate_campaign_id=BASELINE_ONE_ID,
        workspace_id=WORKSPACE_ID,
        db_path=database,
        artifact_root=artifacts,
    )
    compare_campaigns(
        baseline_campaign_id=BASELINE_ONE_ID,
        candidate_campaign_id=CANDIDATE_ID,
        workspace_id=WORKSPACE_ID,
        db_path=database,
        artifact_root=artifacts,
    )
    real_stage_reference = campaign_service.stage_reference_campaign
    staged_references: list[str] = []

    def record_staged_reference(publication, reference_campaign_id: str) -> None:
        staged_references.append(reference_campaign_id)
        real_stage_reference(publication, reference_campaign_id)

    monkeypatch.setattr(
        campaign_service,
        "stage_reference_campaign",
        record_staged_reference,
    )

    compared = compare_campaigns(
        baseline_campaign_id=BASELINE_TWO_ID,
        candidate_campaign_id=CANDIDATE_ID,
        workspace_id=WORKSPACE_ID,
        db_path=database,
        artifact_root=artifacts,
    )

    assert compared["ok"] is True
    assert set(staged_references) == {
        REFERENCE_ROOT_ID,
        BASELINE_ONE_ID,
        BASELINE_TWO_ID,
    }
    report = verify_campaign(artifacts, CANDIDATE_ID)
    assert report.ok, report.issues
    campaign_path = artifacts / CANDIDATE_ID
    history = json.loads((campaign_path / "gate_history.json").read_bytes())
    gates = [
        json.loads(
            (
                campaign_path / "gates" / gate_id / "release_gate.json"
            ).read_bytes()
        )
        for gate_id in history["entries"]
    ]
    assert {gate["baseline_campaign_id"] for gate in gates} == {
        None,
        BASELINE_ONE_ID,
        BASELINE_TWO_ID,
    }
    current_gate = json.loads((campaign_path / "release_gate.json").read_bytes())
    assert current_gate["baseline_campaign_id"] == BASELINE_TWO_ID
    gate_by_baseline = {
        gate["baseline_campaign_id"]: gate["id"] for gate in gates
    }
    _assert_api_current_gate(
        database,
        campaign_id=CANDIDATE_ID,
        expected_gate_id=gate_by_baseline[BASELINE_TWO_ID],
    )

    for revisited_baseline in (BASELINE_ONE_ID, BASELINE_TWO_ID):
        revisited = compare_campaigns(
            baseline_campaign_id=revisited_baseline,
            candidate_campaign_id=CANDIDATE_ID,
            workspace_id=WORKSPACE_ID,
            db_path=database,
            artifact_root=artifacts,
        )
        assert revisited["release_gate"]["baseline_campaign_id"] == revisited_baseline
        assert verify_campaign(artifacts, CANDIDATE_ID).ok is True
        _assert_api_current_gate(
            database,
            campaign_id=CANDIDATE_ID,
            expected_gate_id=gate_by_baseline[revisited_baseline],
        )

    with closing(sqlite3.connect(database)) as connection:
        head_audit_id = connection.execute(
            """SELECT mis_audit_id FROM reliability_campaign_gate_heads
            WHERE workspace_id=? AND campaign_id=?""",
            (WORKSPACE_ID, CANDIDATE_ID),
        ).fetchone()[0]
        latest_audit_id = connection.execute(
            """SELECT audit_id FROM audit_logs
            WHERE action='open_cekura.release_gate.head'
              AND entity_type='reliability_campaign_gate_head'
              AND entity_id=? ORDER BY rowid DESC LIMIT 1""",
            (CANDIDATE_ID,),
        ).fetchone()[0]
    assert head_audit_id == latest_audit_id
    _assert_campaign_closed(artifacts, CANDIDATE_ID)
