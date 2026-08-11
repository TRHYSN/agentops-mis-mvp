"""Cross-process campaign orchestration for the OpenCekura CLI.

The service composes existing simulation, MIS, evidence, repository, and gate
contracts.  It owns no second ledger: SQLite writes target the canonical MIS
tables and the normalized Reliability Lab projection in one caller-owned
transaction.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import platform
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

import yaml
from pydantic import TypeAdapter, ValidationError

from open_cekura.campaigns.runner import CampaignExecution, execute_mock_campaign
from open_cekura.domain.enums import EvaluationStatus, GateDecision, RunFinalState
from open_cekura.domain.ids import stable_id
from open_cekura.domain.models import (
    AgentUnderTest,
    AgentVersion,
    Campaign,
    ConversationTurn,
    EvidenceEnvironment,
    EvidenceManifest,
    EvaluationResult,
    ObservedToolCall,
    RegressionCase,
    ReleaseGateDecision,
    ScenarioSuite,
)
from open_cekura.evidence.bundle import (
    CampaignBundleInputs,
    GateSnapshotInputs,
    RunBundleInputs,
    write_campaign_bundle,
    write_run_bundle,
)
from open_cekura.evidence.manifest import (
    EvidenceError,
    VerificationReport,
    canonical_json_bytes,
    is_symlink_or_reparse,
    sha256_bytes,
    verified_campaign_json,
    verify_campaign,
    verify_run_bundles,
)
from open_cekura.evidence.publication import (
    CampaignPublication,
    PublicationError,
    begin_publication,
    campaign_tree_sha256,
    cleanup_publication_recovery_files,
    discard_unsealed_publication,
    finish_committed_publication,
    load_pending_publication,
    release_publication_claim,
    rollback_publication,
    seal_publication,
    stage_reference_campaign,
    swap_publication_to_final,
)
from open_cekura.mis.persistence import (
    MISBridgeError,
    PersistedCampaignMappings,
    persist_campaign_execution,
    safe_mis_metadata,
)
from open_cekura.release_gate.policy import (
    CampaignGateInput,
    evaluate_release_gate,
)
from open_cekura.simulation.mock_agent import MockAgentConfig
from open_cekura.scenarios.schema import ScenarioDefinition
from open_cekura.storage.repository import RepositoryError
from open_cekura.storage.sqlite_repository import SQLiteRepository


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WORKSPACE_ID = "local-demo"
DEFAULT_ARTIFACT_ROOT = REPO_ROOT / "artifacts" / "open-cekura"
EXIT_BLOCKED = 3
EXIT_EVIDENCE_INVALID = 4
_MAX_AUTHORITY_ARTIFACT_BYTES = 8 * 1024 * 1024
_EVALUATION_RESULTS_ADAPTER = TypeAdapter(list[EvaluationResult])
_TURN_RESULTS_ADAPTER = TypeAdapter(list[ConversationTurn])
_TOOL_CALL_RESULTS_ADAPTER = TypeAdapter(list[ObservedToolCall])
_REGRESSION_RESULTS_ADAPTER = TypeAdapter(list[RegressionCase])


class CampaignServiceError(RuntimeError):
    """A safe, user-facing orchestration failure."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def resolve_db_path(value: str | Path | None) -> Path:
    raw = value if value is not None else os.environ.get("AGENTOPS_DB_PATH")
    return Path(raw).resolve() if raw else (REPO_ROOT / "agentops_mis.db").resolve()


def resolve_artifact_root(value: str | Path | None) -> Path:
    raw = value if value is not None else DEFAULT_ARTIFACT_ROOT
    return Path(os.path.abspath(os.fspath(raw)))


def run_campaign(
    *,
    suite_path: str | Path,
    agent: str,
    version: str,
    campaign_id: str | None,
    workspace_id: str,
    db_path: str | Path | None,
    artifact_root: str | Path | None,
) -> dict[str, Any]:
    """Execute, govern, persist, bundle, and verify one deterministic campaign."""

    if agent != "mock":
        raise CampaignServiceError(
            "unsupported_agent", "v0 campaign run supports agent=mock"
        )
    config = _mock_config(version)
    artifacts = resolve_artifact_root(artifact_root)
    database = resolve_db_path(db_path)
    campaign_id = campaign_id or stable_id(
        "occampaign",
        workspace_id,
        version,
        datetime.now(timezone.utc).isoformat(),
    )
    _recover_campaign_publication_entry(
        artifact_root=artifacts,
        db_path=database,
        workspace_id=workspace_id,
        campaign_id=campaign_id,
    )
    existing_facts = _existing_campaign_facts(artifacts, campaign_id)
    created_at = existing_facts["created_at"] if existing_facts is not None else None
    execution = asyncio.run(
        execute_mock_campaign(
            suite_path=Path(suite_path).resolve(),
            config=config,
            version=version,
            campaign_id=campaign_id,
            workspace_id=workspace_id,
            created_at=created_at,
        )
    )
    if existing_facts is not None:
        _validate_idempotent_execution(
            execution,
            facts=existing_facts,
            workspace_id=workspace_id,
        )
        if not database.is_file():
            raise CampaignServiceError(
                "campaign_ledger_missing",
                "existing campaign evidence requires its authoritative MIS ledger",
            )
    git_commit_sha = _git_commit_sha()
    environment = _environment()

    conn, mis = _open_mis_database(database)
    publication: CampaignPublication | None = None
    try:
        repository = SQLiteRepository(conn, workspace_id=workspace_id)
        repository.initialize_schema()
        if existing_facts is not None:
            if _database_campaign_is_closed(
                conn,
                mis=mis,
                repository=repository,
                facts=existing_facts,
                execution=execution,
            ):
                return _idempotent_campaign_result(
                    execution,
                    facts=existing_facts,
                    artifact_root=artifacts,
                    db_path=database,
                )
            raise CampaignServiceError(
                "campaign_ledger_incomplete",
                "existing campaign evidence is not backed by a closed MIS ledger",
            )
        conn.execute("BEGIN IMMEDIATE")
        mappings = persist_campaign_execution(
            conn,
            execution,
            workspace_id=workspace_id,
        )
        gate_input = execution.gate_input(evidence_verified=True)
        predicted_gate = evaluate_release_gate(
            gate_input,
            baseline=None,
            created_at=execution.campaign.created_at,
        )
        publication = begin_publication(
            artifacts,
            authority_id=repository.publication_authority_id(),
            workspace_id=workspace_id,
            campaign_id=campaign_id,
            gate_id=predicted_gate.id,
            publication_id=_new_publication_id(
                workspace_id, campaign_id, predicted_gate.id
            ),
            expected_previous_tree_sha256=None,
        )
        manifests = _write_governed_run_bundles(
            conn=conn,
            mis=mis,
            repository=repository,
            execution=execution,
            mappings=mappings,
            artifact_root=publication.stage_root,
            git_commit_sha=git_commit_sha,
            environment=environment,
        )
        run_report = verify_run_bundles(publication.stage_root, campaign_id)
        if not run_report.ok:
            raise CampaignServiceError(
                "evidence_verification_failed",
                _verification_message(run_report),
            )
        gate = _persist_gate(
            conn=conn,
            mis=mis,
            repository=repository,
            candidate=gate_input,
            baseline=None,
            created_at=execution.campaign.created_at,
        )
        if gate.id != predicted_gate.id:
            raise CampaignServiceError(
                "gate_identity_mismatch",
                "release gate identity changed during staged publication",
            )
        summary = _campaign_summary(
            execution,
            mappings=mappings,
            gate_input=gate_input,
            manifests=manifests,
            git_commit_sha=git_commit_sha,
            workspace_id=workspace_id,
        )
        write_campaign_bundle(
            publication.stage_root,
            CampaignBundleInputs(
                campaign_id=campaign_id,
                campaign_summary=summary,
                baseline_candidate_diff=None,
                release_gate=gate.model_dump(mode="json"),
                regression_cases=_mapped_regressions(execution, mappings),
            ),
        )
        staged_facts = _load_campaign_facts(publication.stage_root, campaign_id)
        _require_authoritative_campaign(
            conn,
            mis=mis,
            repository=repository,
            facts=staged_facts,
        )
        publication = seal_publication(publication)
        _record_publication_outbox(
            conn,
            publication=publication,
            created_at=execution.campaign.created_at,
        )
        swap_publication_to_final(publication)
        closed_facts = _load_campaign_facts(artifacts, campaign_id)
        _require_authoritative_campaign(
            conn,
            mis=mis,
            repository=repository,
            facts=closed_facts,
        )
        conn.commit()
    except BaseException:
        if conn.in_transaction:
            conn.rollback()
        _release_or_discard_owned_publication(publication)
        _recover_campaign_publication_in_connection(
            conn,
            mis=mis,
            repository=SQLiteRepository(conn, workspace_id=workspace_id),
            artifact_root=artifacts,
            campaign_id=campaign_id,
        )
        raise
    else:
        _release_or_discard_owned_publication(publication)
        _recover_campaign_publication_in_connection(
            conn,
            mis=mis,
            repository=SQLiteRepository(conn, workspace_id=workspace_id),
            artifact_root=artifacts,
            campaign_id=campaign_id,
        )
    finally:
        conn.close()

    return {
        "ok": True,
        "operation": "campaign_run",
        "campaign_id": campaign_id,
        "workspace_id": workspace_id,
        "agent": agent,
        "version": version,
        "run_count": execution.metrics.run_count,
        "failure_count": len(execution.failures),
        "regression_count": len(execution.regressions),
        "release_gate": gate.model_dump(mode="json"),
        "evidence_verified": True,
        "artifact_root": str(artifacts),
        "database": str(database),
        "idempotent_replay": False,
        "token_omitted": True,
    }


def compare_campaigns(
    *,
    baseline_campaign_id: str,
    candidate_campaign_id: str,
    workspace_id: str,
    db_path: str | Path | None,
    artifact_root: str | Path | None,
) -> dict[str, Any]:
    """Compare two verified persisted campaigns and update candidate evidence."""

    if baseline_campaign_id == candidate_campaign_id:
        raise CampaignServiceError(
            "incompatible_campaigns",
            "baseline and candidate must be different campaigns",
        )
    artifacts = resolve_artifact_root(artifact_root)
    database = resolve_db_path(db_path)
    conn, mis = _open_mis_database(database)
    publication: CampaignPublication | None = None
    try:
        repository = SQLiteRepository(conn, workspace_id=workspace_id)
        repository.initialize_schema()
        _recover_campaign_publication_in_connection(
            conn,
            mis=mis,
            repository=repository,
            artifact_root=artifacts,
            campaign_id=candidate_campaign_id,
        )
        _recover_campaign_publication_in_connection(
            conn,
            mis=mis,
            repository=repository,
            artifact_root=artifacts,
            campaign_id=baseline_campaign_id,
        )
        conn.execute("BEGIN IMMEDIATE")
        baseline = _load_campaign_facts(artifacts, baseline_campaign_id)
        candidate = _load_campaign_facts(artifacts, candidate_campaign_id)
        _require_authoritative_campaign(
            conn,
            mis=mis,
            repository=repository,
            facts=baseline,
        )
        _require_authoritative_campaign(
            conn,
            mis=mis,
            repository=repository,
            facts=candidate,
        )
        gate = _persist_gate(
            conn=conn,
            mis=mis,
            repository=repository,
            candidate=candidate["gate_input"],
            baseline=baseline["gate_input"],
            created_at=candidate["created_at"],
        )
        comparison = _comparison_payload(
            baseline_campaign_id,
            candidate_campaign_id,
            baseline["gate_input"],
            candidate["gate_input"],
        )
        if _gate_evidence_view_is_current(candidate, gate, comparison):
            conn.commit()
            return {
                "ok": gate.decision is not GateDecision.BLOCK,
                "operation": "campaign_compare",
                "baseline_campaign_id": baseline_campaign_id,
                "candidate_campaign_id": candidate_campaign_id,
                "release_gate": gate.model_dump(mode="json"),
                "comparison": comparison,
                "evidence_verified": True,
                "idempotent_replay": True,
                "token_omitted": True,
            }
        publication = begin_publication(
            artifacts,
            authority_id=repository.publication_authority_id(),
            workspace_id=workspace_id,
            campaign_id=candidate_campaign_id,
            gate_id=gate.id,
            publication_id=_new_publication_id(
                workspace_id, candidate_campaign_id, gate.id
            ),
            expected_previous_tree_sha256=candidate["tree_sha256"],
        )
        _stage_campaign_reference_closure(
            publication,
            gate_history=candidate["gate_history"],
            additional_campaign_ids=(baseline_campaign_id,),
        )
        write_campaign_bundle(
            publication.stage_root,
            CampaignBundleInputs(
                campaign_id=candidate_campaign_id,
                campaign_summary={
                    **candidate["summary"],
                    "comparison": comparison,
                },
                baseline_candidate_diff=comparison,
                release_gate=gate.model_dump(mode="json"),
                regression_cases=tuple(candidate["regression_cases"]),
                gate_history=(
                    *candidate["gate_history"],
                    GateSnapshotInputs(
                        release_gate=gate.model_dump(mode="json"),
                        baseline_candidate_diff=comparison,
                    ),
                ),
            ),
        )
        staged_facts = _load_campaign_facts(
            publication.stage_root, candidate_campaign_id
        )
        _require_authoritative_campaign(
            conn,
            mis=mis,
            repository=repository,
            facts=staged_facts,
        )
        publication = seal_publication(publication)
        _record_publication_outbox(
            conn,
            publication=publication,
            created_at=candidate["created_at"],
        )
        swap_publication_to_final(publication)
        closed_facts = _load_campaign_facts(artifacts, candidate_campaign_id)
        _require_authoritative_campaign(
            conn,
            mis=mis,
            repository=repository,
            facts=closed_facts,
        )
        conn.commit()
    except BaseException:
        if conn.in_transaction:
            conn.rollback()
        _release_or_discard_owned_publication(publication)
        _recover_campaign_publication_in_connection(
            conn,
            mis=mis,
            repository=SQLiteRepository(conn, workspace_id=workspace_id),
            artifact_root=artifacts,
            campaign_id=candidate_campaign_id,
        )
        raise
    else:
        _release_or_discard_owned_publication(publication)
        _recover_campaign_publication_in_connection(
            conn,
            mis=mis,
            repository=SQLiteRepository(conn, workspace_id=workspace_id),
            artifact_root=artifacts,
            campaign_id=candidate_campaign_id,
        )
    finally:
        conn.close()
    return {
        "ok": gate.decision is not GateDecision.BLOCK,
        "operation": "campaign_compare",
        "baseline_campaign_id": baseline_campaign_id,
        "candidate_campaign_id": candidate_campaign_id,
        "release_gate": gate.model_dump(mode="json"),
        "comparison": comparison,
        "evidence_verified": True,
        "token_omitted": True,
    }


def evaluate_campaign_gate(
    *,
    campaign_id: str,
    baseline_campaign_id: str | None,
    workspace_id: str,
    db_path: str | Path | None,
    artifact_root: str | Path | None,
) -> dict[str, Any]:
    """Recompute a gate from verified persisted facts, optionally as a comparison."""

    artifacts = resolve_artifact_root(artifact_root)
    database = resolve_db_path(db_path)
    conn, mis = _open_mis_database(database)
    publication: CampaignPublication | None = None
    try:
        repository = SQLiteRepository(conn, workspace_id=workspace_id)
        repository.initialize_schema()
        _recover_campaign_publication_in_connection(
            conn,
            mis=mis,
            repository=repository,
            artifact_root=artifacts,
            campaign_id=campaign_id,
        )
        candidate = _load_campaign_facts(artifacts, campaign_id)
        selected_baseline = baseline_campaign_id or _stored_baseline_id(
            candidate["diff"]
        )
        if selected_baseline is not None:
            _recover_campaign_publication_in_connection(
                conn,
                mis=mis,
                repository=repository,
                artifact_root=artifacts,
                campaign_id=selected_baseline,
            )
        baseline = (
            _load_campaign_facts(artifacts, selected_baseline)
            if selected_baseline is not None
            else None
        )
        conn.execute("BEGIN IMMEDIATE")
        _require_authoritative_campaign(
            conn,
            mis=mis,
            repository=repository,
            facts=candidate,
        )
        if selected_baseline is not None:
            assert baseline is not None
            _require_authoritative_campaign(
                conn,
                mis=mis,
                repository=repository,
                facts=baseline,
            )
        gate = _persist_gate(
            conn=conn,
            mis=mis,
            repository=repository,
            candidate=candidate["gate_input"],
            baseline=None if baseline is None else baseline["gate_input"],
            created_at=candidate["created_at"],
        )
        comparison = (
            None
            if baseline is None or selected_baseline is None
            else _comparison_payload(
                selected_baseline,
                campaign_id,
                baseline["gate_input"],
                candidate["gate_input"],
            )
        )
        if _gate_evidence_view_is_current(candidate, gate, comparison):
            conn.commit()
            return {
                "ok": gate.decision is not GateDecision.BLOCK,
                "operation": "gate_evaluate",
                "campaign_id": campaign_id,
                "baseline_campaign_id": selected_baseline,
                "release_gate": gate.model_dump(mode="json"),
                "evidence_verified": True,
                "idempotent_replay": True,
                "token_omitted": True,
            }
        publication = begin_publication(
            artifacts,
            authority_id=repository.publication_authority_id(),
            workspace_id=workspace_id,
            campaign_id=campaign_id,
            gate_id=gate.id,
            publication_id=_new_publication_id(workspace_id, campaign_id, gate.id),
            expected_previous_tree_sha256=candidate["tree_sha256"],
        )
        _stage_campaign_reference_closure(
            publication,
            gate_history=candidate["gate_history"],
            additional_campaign_ids=(
                (selected_baseline,) if selected_baseline is not None else ()
            ),
        )
        write_campaign_bundle(
            publication.stage_root,
            CampaignBundleInputs(
                campaign_id=campaign_id,
                campaign_summary={
                    **candidate["summary"],
                    **({"comparison": comparison} if comparison is not None else {}),
                },
                baseline_candidate_diff=comparison,
                release_gate=gate.model_dump(mode="json"),
                regression_cases=tuple(candidate["regression_cases"]),
                gate_history=(
                    *candidate["gate_history"],
                    GateSnapshotInputs(
                        release_gate=gate.model_dump(mode="json"),
                        baseline_candidate_diff=(
                            comparison
                            if comparison is not None
                            else {
                                "schema_version": 1,
                                "campaign_id": campaign_id,
                                "baseline": None,
                                "comparison": "no_comparison",
                            }
                        ),
                    ),
                ),
            ),
        )
        staged_facts = _load_campaign_facts(publication.stage_root, campaign_id)
        _require_authoritative_campaign(
            conn,
            mis=mis,
            repository=repository,
            facts=staged_facts,
        )
        publication = seal_publication(publication)
        _record_publication_outbox(
            conn,
            publication=publication,
            created_at=candidate["created_at"],
        )
        swap_publication_to_final(publication)
        closed_facts = _load_campaign_facts(artifacts, campaign_id)
        _require_authoritative_campaign(
            conn,
            mis=mis,
            repository=repository,
            facts=closed_facts,
        )
        conn.commit()
    except BaseException:
        if conn.in_transaction:
            conn.rollback()
        _release_or_discard_owned_publication(publication)
        _recover_campaign_publication_in_connection(
            conn,
            mis=mis,
            repository=SQLiteRepository(conn, workspace_id=workspace_id),
            artifact_root=artifacts,
            campaign_id=campaign_id,
        )
        raise
    else:
        _release_or_discard_owned_publication(publication)
        _recover_campaign_publication_in_connection(
            conn,
            mis=mis,
            repository=SQLiteRepository(conn, workspace_id=workspace_id),
            artifact_root=artifacts,
            campaign_id=campaign_id,
        )
    finally:
        conn.close()
    return {
        "ok": gate.decision is not GateDecision.BLOCK,
        "operation": "gate_evaluate",
        "campaign_id": campaign_id,
        "baseline_campaign_id": selected_baseline,
        "release_gate": gate.model_dump(mode="json"),
        "evidence_verified": True,
        "token_omitted": True,
    }


def verify_campaign_evidence(
    *, campaign_id: str, artifact_root: str | Path | None, strict: bool = False
) -> dict[str, Any]:
    artifacts = resolve_artifact_root(artifact_root)
    report = verify_campaign(artifacts, campaign_id, strict=strict)
    issues = _public_issues(report)
    return {
        "ok": report.ok,
        "operation": "evidence_verify",
        "campaign_id": campaign_id,
        "verified": report.ok,
        "run_count": len(report.runs),
        "issue_count": len(issues),
        "issues": issues,
        "artifact_root": str(artifacts),
        "token_omitted": True,
    }


def _mock_config(version: str) -> MockAgentConfig:
    if version == "baseline":
        return MockAgentConfig.baseline()
    if version == "candidate":
        return MockAgentConfig.candidate()
    raise CampaignServiceError(
        "unsupported_mock_version", "mock version must be baseline or candidate"
    )


def _existing_campaign_facts(
    artifact_root: Path, campaign_id: str
) -> dict[str, Any] | None:
    campaign_path = artifact_root / campaign_id
    if not campaign_path.exists():
        return None
    return _load_campaign_facts(artifact_root, campaign_id)


def _database_campaign_is_closed(
    conn: sqlite3.Connection,
    *,
    mis: Any,
    repository: SQLiteRepository,
    facts: Mapping[str, Any],
    execution: CampaignExecution,
) -> bool:
    campaign_id = facts["gate_input"].campaign_id
    campaign = repository.get_campaign(campaign_id)
    if (
        campaign is None
        or not campaign.get("mis_task_id")
        or not campaign.get("mis_plan_id")
    ):
        return False
    expected_counts = {
        "runs": len(execution.records),
        "turns": sum(len(record.simulation.turns) for record in execution.records),
        "tool_calls": sum(
            len(record.simulation.tool_calls) for record in execution.records
        ),
        "evaluations": sum(
            len(record.evaluations) for record in execution.records
        ),
        "failures": len(execution.failures),
        "regressions": len(execution.regressions),
        "manifests": len(execution.records),
    }
    count_sql = {
        "runs": """SELECT COUNT(*) FROM reliability_conversation_runs
            WHERE workspace_id=? AND campaign_id=? AND mis_run_id IS NOT NULL""",
        "turns": """SELECT COUNT(*) FROM reliability_conversation_turns t
            JOIN reliability_conversation_runs r
              ON r.workspace_id=t.workspace_id AND r.run_id=t.run_id
            WHERE r.workspace_id=? AND r.campaign_id=?""",
        "tool_calls": """SELECT COUNT(*) FROM reliability_observed_tool_calls c
            JOIN reliability_conversation_runs r
              ON r.workspace_id=c.workspace_id AND r.run_id=c.run_id
            WHERE r.workspace_id=? AND r.campaign_id=?
              AND c.mis_tool_call_id IS NOT NULL""",
        "evaluations": """SELECT COUNT(*) FROM reliability_evaluation_results e
            JOIN reliability_conversation_runs r
              ON r.workspace_id=e.workspace_id AND r.run_id=e.run_id
            WHERE r.workspace_id=? AND r.campaign_id=?""",
        "failures": """SELECT COUNT(*) FROM reliability_failures f
            JOIN reliability_conversation_runs r
              ON r.workspace_id=f.workspace_id AND r.run_id=f.run_id
            WHERE r.workspace_id=? AND r.campaign_id=?""",
        "regressions": """SELECT COUNT(*) FROM reliability_regressions g
            JOIN reliability_conversation_runs r
              ON r.workspace_id=g.workspace_id AND r.run_id=g.source_run_id
            WHERE r.workspace_id=? AND r.campaign_id=?
              AND g.mis_memory_id IS NOT NULL""",
        "manifests": """SELECT COUNT(*) FROM reliability_evidence_manifests
            WHERE workspace_id=? AND campaign_id=?
              AND mis_artifact_id IS NOT NULL""",
    }
    for name, expected in expected_counts.items():
        actual = int(
            conn.execute(
                count_sql[name],
                (repository.workspace_id, campaign_id),
            ).fetchone()[0]
        )
        if actual != expected:
            return False

    try:
        return _campaign_authority_matches(
            conn,
            mis=mis,
            repository=repository,
            campaign=campaign,
            facts=facts,
        )
    except (
        MISBridgeError,
        OSError,
        sqlite3.Error,
        ValueError,
        ValidationError,
        RepositoryError,
    ):
        return False


def _mis_evidence_anchor_is_valid(
    conn: sqlite3.Connection,
    *,
    mis: Any,
    repository: SQLiteRepository,
    manifest: EvidenceManifest,
    campaign: Mapping[str, Any],
    tool_calls: list[ObservedToolCall],
    evaluations: list[EvaluationResult],
) -> bool:
    artifact = conn.execute(
        "SELECT * FROM artifacts WHERE artifact_id=?",
        (manifest.mis_artifact_id,),
    ).fetchone()
    run = conn.execute(
        "SELECT mis_run_id FROM reliability_conversation_runs "
        "WHERE workspace_id=? AND run_id=?",
        (repository.workspace_id, manifest.run_id),
    ).fetchone()
    expected_uri = (
        f"artifact:open-cekura/{manifest.campaign_id}/"
        f"{manifest.run_id}/evidence_manifest.json"
    )
    if artifact is None or run is None:
        return False
    expected_artifact = {
        "artifact_id": manifest.mis_artifact_id,
        "task_id": campaign.get("mis_task_id"),
        "run_id": run["mis_run_id"],
        "artifact_type": "open_cekura_evidence",
        "title": "OpenCekura run evidence manifest",
        "uri": expected_uri,
        "summary": "Canonical OpenCekura run evidence hashes; raw secrets omitted.",
        "content_hash": sha256_bytes(manifest.canonical_json_bytes()),
    }
    if any(artifact[key] != value for key, value in expected_artifact.items()):
        return False
    if manifest.mis_plan_evidence_manifest_id is None:
        return True
    plan_manifest = conn.execute(
        "SELECT * FROM plan_evidence_manifests WHERE manifest_id=?",
        (manifest.mis_plan_evidence_manifest_id,),
    ).fetchone()
    plan = conn.execute(
        "SELECT * FROM agent_plans WHERE plan_id=?", (campaign.get("mis_plan_id"),)
    ).fetchone()
    core_run = conn.execute(
        "SELECT * FROM runs WHERE run_id=?", (run["mis_run_id"],)
    ).fetchone()
    if plan_manifest is None or plan is None or core_run is None:
        return False
    expected_tool_ids = [
        call.mis_tool_call_id
        for call in tool_calls
        if call.mis_tool_call_id is not None and call.error is None
    ]
    expected_evaluation_ids = [
        evaluation.mis_evaluation_id
        for evaluation in evaluations
        if evaluation.mis_evaluation_id is not None
        and evaluation.status is EvaluationStatus.PASS
    ]
    if (
        plan_manifest["workspace_id"] != repository.workspace_id
        or plan_manifest["plan_id"] != campaign.get("mis_plan_id")
        or plan_manifest["task_id"] != campaign.get("mis_task_id")
        or plan_manifest["run_id"] != run["mis_run_id"]
        or plan_manifest["agent_id"] != core_run["agent_id"]
        or plan_manifest["mismatch_policy"] != "block"
        or json.loads(plan_manifest["expected_steps_json"])
        != json.loads(plan["execution_steps_json"])
        or json.loads(plan_manifest["tool_call_ids_json"]) != expected_tool_ids
        or json.loads(plan_manifest["evaluation_ids_json"])
        != expected_evaluation_ids
        or json.loads(plan_manifest["artifact_ids_json"])
        != [manifest.mis_artifact_id]
        or json.loads(plan_manifest["audit_ids_json"]) != []
        or plan_manifest["plan_hash"] != plan["plan_hash"]
        or plan_manifest["verification_result_hash"]
        != plan["verification_result_hash"]
        or plan_manifest["status"] != "verified"
    ):
        return False
    verification = mis.verify_plan_evidence_manifest_row(conn, plan_manifest)
    return bool(verification.get("pass")) and verification.get("status") == "verified"


def _mis_gate_anchor_is_valid(
    conn: sqlite3.Connection,
    *,
    release_gate: ReleaseGateDecision,
    campaign: Mapping[str, Any],
    workspace_id: str,
) -> bool:
    approval = conn.execute(
        "SELECT * FROM approvals WHERE approval_id=?",
        (release_gate.mis_approval_id,),
    ).fetchone()
    if approval is None:
        return False
    linked_run = conn.execute(
        "SELECT mis_run_id FROM reliability_conversation_runs "
        "WHERE workspace_id=? AND campaign_id=? AND mis_run_id IS NOT NULL "
        "ORDER BY created_at,run_id LIMIT 1",
        (workspace_id, release_gate.campaign_id),
    ).fetchone()
    task = conn.execute(
        "SELECT owner_agent_id FROM tasks WHERE task_id=?",
        (campaign.get("mis_task_id"),),
    ).fetchone()
    if linked_run is None or task is None:
        return False
    expected_decision = (
        "rejected" if release_gate.decision is GateDecision.BLOCK else "approved"
    )
    expected = {
        "approval_id": release_gate.mis_approval_id,
        "task_id": campaign.get("mis_task_id"),
        "run_id": linked_run["mis_run_id"],
        "tool_call_id": None,
        "requested_by_agent_id": task["owner_agent_id"],
        "approver_user_id": None,
        "decision": expected_decision,
        "reason": (
            f"OpenCekura {release_gate.policy_version}: "
            f"{release_gate.decision.value}"
        ),
        "subject_type": "reliability_release_gate",
        "subject_id": release_gate.id,
        "subject_hash": hashlib.sha256(
            release_gate.canonical_json_bytes()
        ).hexdigest(),
        "expires_at": None,
        "created_at": _utc_text(release_gate.created_at),
        "decided_at": _utc_text(release_gate.created_at),
    }
    if not all(approval[key] == value for key, value in expected.items()):
        return False
    audit = conn.execute(
        "SELECT * FROM audit_logs WHERE audit_id=?",
        (stable_id("audocgate", release_gate.id),),
    ).fetchone()
    expected_metadata = {
        "campaign_id": release_gate.campaign_id,
        "baseline_campaign_id": release_gate.baseline_campaign_id,
        "approval_id": release_gate.mis_approval_id,
        "raw_transcript_omitted": True,
    }
    return bool(
        audit is not None
        and audit["actor_type"] == "system"
        and audit["actor_id"] == "open-cekura-gate"
        and audit["action"] == "open_cekura.release_gate.evaluate"
        and audit["entity_type"] == "reliability_release_gate"
        and audit["entity_id"] == release_gate.id
        and audit["before_hash"] is None
        and audit["after_hash"]
        == _mis_stable_hash(release_gate.model_dump(mode="json"))
        and json.loads(audit["metadata_json"]) == expected_metadata
        and isinstance(audit["tamper_chain_hash"], str)
        and bool(audit["tamper_chain_hash"])
    )


def _gate_head_audit_is_valid(
    conn: sqlite3.Connection, head: Mapping[str, Any]
) -> bool:
    audit = conn.execute(
        "SELECT * FROM audit_logs WHERE audit_id=?", (head["mis_audit_id"],)
    ).fetchone()
    latest = conn.execute(
        """SELECT audit_id FROM audit_logs
        WHERE action='open_cekura.release_gate.head'
          AND entity_type='reliability_campaign_gate_head'
          AND entity_id=?
        ORDER BY rowid DESC LIMIT 1""",
        (head["campaign_id"],),
    ).fetchone()
    if audit is None or latest is None or latest["audit_id"] != head["mis_audit_id"]:
        return False
    try:
        metadata = json.loads(audit["metadata_json"])
    except (TypeError, json.JSONDecodeError):
        return False
    if not isinstance(metadata, dict):
        return False
    previous_gate_id = metadata.get("previous_gate_id")
    previous_head_audit_id = metadata.get("previous_head_audit_id")
    if previous_gate_id is None:
        if previous_head_audit_id is not None:
            return False
    elif not isinstance(previous_head_audit_id, str):
        return False
    else:
        previous_audit = conn.execute(
            "SELECT * FROM audit_logs WHERE audit_id=?",
            (previous_head_audit_id,),
        ).fetchone()
        if previous_audit is None:
            return False
        try:
            previous_metadata = json.loads(previous_audit["metadata_json"])
        except (TypeError, json.JSONDecodeError):
            return False
        if (
            previous_audit["action"] != "open_cekura.release_gate.head"
            or previous_audit["entity_type"] != "reliability_campaign_gate_head"
            or previous_audit["entity_id"] != head["campaign_id"]
            or not isinstance(previous_metadata, dict)
            or previous_metadata.get("current_gate_id") != previous_gate_id
        ):
            return False
    expected_audit_id = stable_id(
        "audochead",
        head["campaign_id"],
        previous_gate_id or "none",
        head["current_gate_id"],
        previous_head_audit_id or "none",
    )
    before = (
        None
        if previous_gate_id is None
        else {
            "campaign_id": head["campaign_id"],
            "current_gate_id": previous_gate_id,
        }
    )
    after = {
        "campaign_id": head["campaign_id"],
        "current_gate_id": head["current_gate_id"],
    }
    expected_metadata = {
        "campaign_id": head["campaign_id"],
        "previous_gate_id": previous_gate_id,
        "current_gate_id": head["current_gate_id"],
        "previous_head_audit_id": previous_head_audit_id,
        "raw_transcript_omitted": True,
    }
    return bool(
        head["mis_audit_id"] == expected_audit_id
        and audit["audit_id"] == expected_audit_id
        and audit["actor_type"] == "system"
        and audit["actor_id"] == "open-cekura-gate-head"
        and audit["action"] == "open_cekura.release_gate.head"
        and audit["entity_type"] == "reliability_campaign_gate_head"
        and audit["entity_id"] == head["campaign_id"]
        and audit["before_hash"]
        == (None if before is None else _mis_stable_hash(before))
        and audit["after_hash"] == _mis_stable_hash(after)
        and metadata == expected_metadata
        and audit["created_at"] == head["updated_at"]
        and isinstance(audit["tamper_chain_hash"], str)
        and bool(audit["tamper_chain_hash"])
    )


def _mis_stable_hash(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _audit_chain_is_valid(conn: sqlite3.Connection) -> bool:
    previous = "genesis"
    try:
        rows = conn.execute("SELECT * FROM audit_logs ORDER BY rowid").fetchall()
        for row in rows:
            metadata = json.loads(row["metadata_json"])
            expected = _mis_stable_hash(
                {
                    "actor_type": row["actor_type"],
                    "actor_id": row["actor_id"],
                    "action": row["action"],
                    "entity_type": row["entity_type"],
                    "entity_id": row["entity_id"],
                    "before_hash": row["before_hash"],
                    "after_hash": row["after_hash"],
                    "metadata_json": metadata,
                    "previous": previous,
                }
            )
            if row["tamper_chain_hash"] != expected:
                return False
            previous = expected
        state = conn.execute(
            "SELECT head_hash FROM audit_chain_state WHERE singleton_id=1"
        ).fetchone()
    except (sqlite3.Error, TypeError, json.JSONDecodeError):
        return False
    return state is not None and state["head_hash"] == previous


def _validate_idempotent_execution(
    execution: CampaignExecution,
    *,
    facts: Mapping[str, Any],
    workspace_id: str,
) -> None:
    summary = facts["summary"]
    stored_version = summary.get("agent_version")
    stored_suite = summary.get("scenario_suite")
    checks = {
        "workspace": summary.get("workspace_id") == workspace_id,
        "agent version": isinstance(stored_version, dict)
        and stored_version.get("id") == execution.agent_version.id,
        "scenario suite": isinstance(stored_suite, dict)
        and stored_suite.get("id") == execution.scenario_suite.id,
        "campaign": facts["gate_input"].campaign_id == execution.campaign.id,
    }
    failed = [name for name, matches in checks.items() if not matches]
    if failed:
        raise CampaignServiceError(
            "campaign_id_conflict",
            "existing campaign ID is bound to different " + ", ".join(failed),
        )


def _idempotent_campaign_result(
    execution: CampaignExecution,
    *,
    facts: Mapping[str, Any],
    artifact_root: Path,
    db_path: Path,
) -> dict[str, Any]:
    summary = facts["summary"]
    return {
        "ok": True,
        "operation": "campaign_run",
        "campaign_id": execution.campaign.id,
        "workspace_id": execution.agent.workspace_id,
        "agent": "mock",
        "version": execution.agent_version.version,
        "run_count": facts["gate_input"].metrics.run_count,
        "failure_count": int(summary.get("failure_count") or 0),
        "regression_count": int(summary.get("regression_count") or 0),
        "release_gate": facts["release_gate"].model_dump(mode="json"),
        "evidence_verified": True,
        "artifact_root": str(artifact_root),
        "database": str(db_path),
        "idempotent_replay": True,
        "token_omitted": True,
    }


def _open_mis_database(db_path: Path) -> tuple[sqlite3.Connection, Any]:
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        import server as mis

        conn = sqlite3.connect(db_path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.create_function("agentops_json_array_contains", 2, mis.json_array_contains)
        conn.create_function(
            "agentops_audit_chain_hash",
            9,
            mis.audit_chain_hash_sql,
            deterministic=True,
        )
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.executescript(mis.SCHEMA_SQL)
        mis.human_auth.init_schema(conn)
        mis.ensure_research_schema(conn)
        mis.ensure_schema_migrations(conn)
        mis.ensure_v121_reference_data(conn)
        conn.commit()
        return conn, mis
    except (OSError, sqlite3.Error) as exc:
        raise CampaignServiceError(
            "database_unavailable", f"MIS database is unavailable: {db_path.name}"
        ) from exc


def _new_publication_id(
    workspace_id: str, campaign_id: str, gate_id: str
) -> str:
    return stable_id(
        "ocpub",
        workspace_id,
        campaign_id,
        gate_id,
        os.urandom(16).hex(),
    )


def _record_publication_outbox(
    conn: sqlite3.Connection,
    *,
    publication: CampaignPublication,
    created_at: datetime,
) -> None:
    if not conn.in_transaction or publication.expected_tree_sha256 is None:
        raise CampaignServiceError(
            "publication_not_prepared",
            "evidence publication requires a sealed caller-owned transaction",
        )
    row = {
        "workspace_id": publication.workspace_id,
        "publication_id": publication.publication_id,
        "campaign_id": publication.campaign_id,
        "gate_id": publication.gate_id,
        "tree_sha256": publication.expected_tree_sha256,
        "status": "prepared",
        "created_at": _utc_text(created_at),
        "published_at": None,
    }
    existing = conn.execute(
        "SELECT * FROM reliability_evidence_publications "
        "WHERE workspace_id=? AND publication_id=?",
        (publication.workspace_id, publication.publication_id),
    ).fetchone()
    if existing is None:
        conn.execute(
            """INSERT INTO reliability_evidence_publications(
                workspace_id,publication_id,campaign_id,gate_id,tree_sha256,
                status,created_at,published_at
            ) VALUES(
                :workspace_id,:publication_id,:campaign_id,:gate_id,:tree_sha256,
                :status,:created_at,:published_at
            )""",
            row,
        )
    elif any(existing[key] != value for key, value in row.items()):
        raise CampaignServiceError(
            "publication_outbox_conflict",
            "stable evidence publication outbox facts conflict",
        )


def _release_or_discard_owned_publication(
    publication: CampaignPublication | None,
) -> None:
    if publication is None:
        return
    if (
        publication.expected_tree_sha256 is None
        and not publication.journal_path.exists()
    ):
        discard_unsealed_publication(publication)
        return
    release_publication_claim(publication)


def _recover_campaign_publication_entry(
    *,
    artifact_root: Path,
    db_path: Path,
    workspace_id: str,
    campaign_id: str,
) -> None:
    if not db_path.is_file():
        publication = load_pending_publication(
            artifact_root,
            workspace_id=workspace_id,
            campaign_id=campaign_id,
        )
        if publication is not None:
            raise CampaignServiceError(
                "publication_recovery_authority_unavailable",
                "sealed evidence publication requires its original SQLite authority",
            )
        return
    conn, mis = _open_mis_database(db_path)
    try:
        repository = SQLiteRepository(conn, workspace_id=workspace_id)
        repository.initialize_schema()
        _recover_campaign_publication_in_connection(
            conn,
            mis=mis,
            repository=repository,
            artifact_root=artifact_root,
            campaign_id=campaign_id,
        )
    finally:
        conn.close()


def _recover_campaign_publication_in_connection(
    conn: sqlite3.Connection,
    *,
    mis: Any,
    repository: SQLiteRepository,
    artifact_root: Path,
    campaign_id: str,
) -> None:
    """Resolve one journal using the durable outbox as the commit authority."""

    if conn.in_transaction:
        raise CampaignServiceError(
            "publication_recovery_transaction_active",
            "evidence recovery requires a clean SQLite transaction boundary",
        )
    repository.initialize_schema()
    conn.execute("BEGIN IMMEDIATE")
    publication: CampaignPublication | None = None
    try:
        publication = load_pending_publication(
            artifact_root,
            workspace_id=repository.workspace_id,
            campaign_id=campaign_id,
            claim=True,
        )
        if publication is None:
            conn.commit()
            return
        if publication.authority_id != repository.publication_authority_id():
            raise PublicationError(
                "publication journal belongs to a different SQLite authority"
            )
        outbox = conn.execute(
            "SELECT * FROM reliability_evidence_publications "
            "WHERE workspace_id=? AND publication_id=?",
            (repository.workspace_id, publication.publication_id),
        ).fetchone()
        if outbox is None:
            rollback_publication(publication)
            if publication.had_final:
                restored = _load_campaign_facts(artifact_root, campaign_id)
                _require_authoritative_campaign(
                    conn,
                    mis=mis,
                    repository=repository,
                    facts=restored,
                )
            conn.commit()
            cleanup_publication_recovery_files(publication)
            return
        expected = {
            "workspace_id": publication.workspace_id,
            "publication_id": publication.publication_id,
            "campaign_id": publication.campaign_id,
            "gate_id": publication.gate_id,
            "tree_sha256": publication.expected_tree_sha256,
        }
        if any(outbox[key] != value for key, value in expected.items()) or outbox[
            "status"
        ] not in {"prepared", "published"}:
            raise PublicationError("publication journal and SQLite outbox disagree")
        finish_committed_publication(publication)
        facts = _load_campaign_facts(artifact_root, campaign_id)
        _require_authoritative_campaign(
            conn,
            mis=mis,
            repository=repository,
            facts=facts,
        )
        if outbox["status"] == "prepared":
            conn.execute(
                """UPDATE reliability_evidence_publications
                SET status='published',published_at=?
                WHERE workspace_id=? AND publication_id=? AND status='prepared'""",
                (
                    _utc_text(datetime.now(timezone.utc)),
                    repository.workspace_id,
                    publication.publication_id,
                ),
            )
        conn.commit()
        cleanup_publication_recovery_files(publication)
    except BaseException as exc:
        if conn.in_transaction:
            conn.rollback()
        if isinstance(exc, CampaignServiceError):
            raise
        raise CampaignServiceError(
            "publication_recovery_failed",
            f"campaign {campaign_id} evidence publication could not be recovered",
        ) from exc
    finally:
        if publication is not None:
            release_publication_claim(publication)


def _write_governed_run_bundles(
    *,
    conn: sqlite3.Connection,
    mis: Any,
    repository: SQLiteRepository,
    execution: CampaignExecution,
    mappings: PersistedCampaignMappings,
    artifact_root: Path,
    git_commit_sha: str,
    environment: EvidenceEnvironment,
) -> tuple[dict[str, Any], ...]:
    manifests: list[dict[str, Any]] = []
    for record in execution.records:
        simulation = record.simulation
        mis_run_id = mappings.mis_run_ids[simulation.run.id]
        artifact_id = stable_id("artoc", mis_run_id, "evidence-manifest.v2")
        calls = tuple(
            call.model_copy(
                update={"mis_tool_call_id": mappings.mis_tool_call_ids[call.id]}
            )
            for call in simulation.tool_calls
        )
        evaluations = tuple(
            evaluation.model_copy(
                update={
                    "mis_evaluation_id": mappings.mis_evaluation_ids.get(evaluation.id)
                }
            )
            for evaluation in record.evaluations
        )
        plan_evidence_available = _plan_evidence_can_verify(calls, evaluations)
        plan_manifest_id = (
            stable_id("pemoc", mappings.mis_plan_id, mis_run_id)
            if plan_evidence_available
            else None
        )
        written = write_run_bundle(
            artifact_root,
            RunBundleInputs(
                manifest_id=stable_id(
                    "ocmanifest", execution.campaign.id, simulation.run.id
                ),
                campaign_id=execution.campaign.id,
                run_id=simulation.run.id,
                mis_artifact_id=artifact_id,
                mis_plan_evidence_manifest_id=plan_manifest_id,
                git_commit_sha=git_commit_sha,
                environment=environment,
                scenario_yaml=record.source_bytes,
                agent_version=execution.agent_version,
                agent_config=execution.agent_config.model_dump(mode="json"),
                transcript=simulation.turns,
                tool_calls=calls,
                initial_state=simulation.initial_state,
                observed_final_state=simulation.final_state,
                timing={
                    "started_at": _utc_text(simulation.started_at),
                    "finished_at": _utc_text(simulation.finished_at),
                    "duration_ms": simulation.duration_ms,
                    "timed_out": simulation.timed_out,
                    "adapter_error": simulation.adapter_error,
                },
                evaluations=evaluations,
                started_at=simulation.started_at,
                finished_at=simulation.finished_at,
                final_state=_deterministic_final_state(evaluations),
                created_at=simulation.finished_at,
            ),
        )
        _record_artifact(
            conn,
            mis=mis,
            workspace_id=repository.workspace_id,
            agent_id=mappings.mis_agent_id,
            run_id=mis_run_id,
            artifact_id=artifact_id,
            campaign_id=execution.campaign.id,
            manifest_path=written.manifest_path,
        )
        if plan_manifest_id is not None:
            _record_plan_evidence(
                conn,
                mis=mis,
                workspace_id=repository.workspace_id,
                agent_id=mappings.mis_agent_id,
                plan_id=mappings.mis_plan_id,
                run_id=mis_run_id,
                manifest_id=plan_manifest_id,
                artifact_id=artifact_id,
                mapped_calls=calls,
                mapped_evaluations=evaluations,
            )
        repository.upsert_evidence_manifest(written.manifest)
        manifests.append(
            {
                **written.manifest.model_dump(mode="json"),
                "plan_evidence_status": (
                    "verified" if plan_manifest_id is not None else "unavailable"
                ),
                "plan_evidence_unavailable_reason": (
                    None
                    if plan_manifest_id is not None
                    else "run_has_no_completed_tool_call"
                ),
            }
        )
    return tuple(manifests)


def _record_artifact(
    conn: sqlite3.Connection,
    *,
    mis: Any,
    workspace_id: str,
    agent_id: str,
    run_id: str,
    artifact_id: str,
    campaign_id: str,
    manifest_path: Path,
) -> None:
    core_run = conn.execute(
        "SELECT task_id FROM runs WHERE run_id=?",
        (run_id,),
    ).fetchone()
    if core_run is None:
        raise CampaignServiceError(
            "mis_artifact_mapping_failed",
            "MIS Artifact mapping requires its authoritative Run",
        )
    expected = {
        "artifact_id": artifact_id,
        "task_id": core_run["task_id"],
        "run_id": run_id,
        "artifact_type": "open_cekura_evidence",
        "title": "OpenCekura run evidence manifest",
        "uri": (
            f"artifact:open-cekura/{campaign_id}/"
            f"{manifest_path.parent.name}/{manifest_path.name}"
        ),
        "summary": "Canonical OpenCekura run evidence hashes; raw secrets omitted.",
        "content_hash": sha256_bytes(manifest_path.read_bytes()),
    }
    payload, status = mis.agent_gateway_record_artifact(
        conn,
        {
            "workspace_id": workspace_id,
            "agent_id": agent_id,
            **expected,
        },
    )
    if status >= 400:
        raise CampaignServiceError(
            "mis_artifact_mapping_failed",
            str(payload.get("error") or "MIS Artifact mapping failed"),
        )
    artifact = payload.get("artifact") if isinstance(payload, dict) else None
    if not isinstance(artifact, Mapping) or any(
        artifact.get(key) != value for key, value in expected.items()
    ):
        raise CampaignServiceError(
            "mis_artifact_mapping_conflict",
            "MIS Artifact mapping conflicts with the evidence manifest",
        )


def _record_plan_evidence(
    conn: sqlite3.Connection,
    *,
    mis: Any,
    workspace_id: str,
    agent_id: str,
    plan_id: str,
    run_id: str,
    manifest_id: str,
    artifact_id: str,
    mapped_calls: tuple[Any, ...],
    mapped_evaluations: tuple[Any, ...],
) -> None:
    existing = conn.execute(
        "SELECT * FROM plan_evidence_manifests WHERE manifest_id=?", (manifest_id,)
    ).fetchone()
    if existing is not None:
        if (
            existing["workspace_id"] != workspace_id
            or existing["plan_id"] != plan_id
            or existing["run_id"] != run_id
            or existing["agent_id"] != agent_id
            or existing["status"] != "verified"
            or artifact_id not in _json_list(existing["artifact_ids_json"])
        ):
            raise CampaignServiceError(
                "mis_plan_evidence_conflict",
                "stable MIS PlanEvidence mapping conflicts with campaign facts",
            )
        return

    completed_tool_ids = [
        call.mis_tool_call_id
        for call in mapped_calls
        if call.mis_tool_call_id is not None and call.error is None
    ]
    passing_evaluation_ids = [
        evaluation.mis_evaluation_id
        for evaluation in mapped_evaluations
        if evaluation.mis_evaluation_id is not None
        and evaluation.status is EvaluationStatus.PASS
    ]
    if not completed_tool_ids or not passing_evaluation_ids:
        raise CampaignServiceError(
            "mis_plan_evidence_incomplete",
            "run lacks completed ToolCall and passing Evaluation facts for PlanEvidence",
        )
    payload, status = mis.agent_gateway_create_plan_evidence_manifest(
        conn,
        {
            "workspace_id": workspace_id,
            "agent_id": agent_id,
            "plan_id": plan_id,
            "run_id": run_id,
            "manifest_id": manifest_id,
            "mismatch_policy": "block",
            "tool_call_ids": completed_tool_ids,
            "evaluation_ids": passing_evaluation_ids,
            "artifact_ids": [artifact_id],
            "verify_now": True,
        },
    )
    verification = payload.get("verification") if isinstance(payload, dict) else None
    if (
        status >= 400
        or not isinstance(verification, dict)
        or not verification.get("pass")
    ):
        raise CampaignServiceError(
            "mis_plan_evidence_failed",
            "MIS PlanEvidence verification did not pass",
        )


def _plan_evidence_can_verify(
    mapped_calls: tuple[Any, ...], mapped_evaluations: tuple[Any, ...]
) -> bool:
    return any(
        call.mis_tool_call_id is not None and call.error is None
        for call in mapped_calls
    ) and any(
        evaluation.mis_evaluation_id is not None
        and evaluation.status is EvaluationStatus.PASS
        for evaluation in mapped_evaluations
    )


def _persist_gate(
    *,
    conn: sqlite3.Connection,
    mis: Any,
    repository: SQLiteRepository,
    candidate: CampaignGateInput,
    baseline: CampaignGateInput | None,
    created_at: datetime,
) -> ReleaseGateDecision:
    gate = evaluate_release_gate(
        candidate,
        baseline=baseline,
        created_at=created_at,
    )
    approval_id = stable_id(
        "apoc",
        candidate.campaign_id,
        baseline.campaign_id if baseline is not None else "standalone",
        gate.policy_version,
    )
    gate = gate.model_copy(update={"mis_approval_id": approval_id})
    campaign = repository.get_campaign(candidate.campaign_id)
    if campaign is None:
        raise CampaignServiceError(
            "campaign_not_found", f"campaign {candidate.campaign_id} is not persisted"
        )
    linked_run = conn.execute(
        "SELECT mis_run_id FROM reliability_conversation_runs "
        "WHERE workspace_id=? AND campaign_id=? AND mis_run_id IS NOT NULL "
        "ORDER BY created_at,run_id LIMIT 1",
        (repository.workspace_id, candidate.campaign_id),
    ).fetchone()
    task_id = campaign.get("mis_task_id")
    if linked_run is None or not task_id:
        raise CampaignServiceError(
            "mis_gate_mapping_failed", "campaign MIS Task/Run mapping is incomplete"
        )
    agent_row = conn.execute(
        "SELECT owner_agent_id FROM tasks WHERE task_id=?", (task_id,)
    ).fetchone()
    if agent_row is None:
        raise CampaignServiceError(
            "mis_gate_mapping_failed", "campaign MIS Agent mapping is unavailable"
        )
    decision = "rejected" if gate.decision is GateDecision.BLOCK else "approved"
    row = {
        "approval_id": approval_id,
        "task_id": task_id,
        "run_id": linked_run["mis_run_id"],
        "tool_call_id": None,
        "requested_by_agent_id": agent_row["owner_agent_id"],
        "approver_user_id": None,
        "decision": decision,
        "reason": f"OpenCekura {gate.policy_version}: {gate.decision.value}",
        "subject_type": "reliability_release_gate",
        "subject_id": gate.id,
        "subject_hash": hashlib.sha256(gate.canonical_json_bytes()).hexdigest(),
        "expires_at": None,
        "created_at": _utc_text(created_at),
        "decided_at": _utc_text(created_at),
    }
    existing = conn.execute(
        "SELECT * FROM approvals WHERE approval_id=?", (approval_id,)
    ).fetchone()
    if existing is None:
        conn.execute(
            """INSERT INTO approvals(
                approval_id,task_id,run_id,tool_call_id,requested_by_agent_id,
                approver_user_id,decision,reason,subject_type,subject_id,subject_hash,
                expires_at,created_at,decided_at
            ) VALUES(
                :approval_id,:task_id,:run_id,:tool_call_id,:requested_by_agent_id,
                :approver_user_id,:decision,:reason,:subject_type,:subject_id,:subject_hash,
                :expires_at,:created_at,:decided_at
            )""",
            row,
        )
    elif any(existing[key] != value for key, value in row.items()):
        raise CampaignServiceError(
            "mis_gate_mapping_conflict",
            "stable MIS Approval mapping conflicts with release gate facts",
        )
    mis.audit(
        conn,
        "system",
        "open-cekura-gate",
        "open_cekura.release_gate.evaluate",
        "reliability_release_gate",
        gate.id,
        None,
        gate.model_dump(mode="json"),
        {
            "campaign_id": candidate.campaign_id,
            "baseline_campaign_id": (
                baseline.campaign_id if baseline is not None else None
            ),
            "approval_id": approval_id,
            "raw_transcript_omitted": True,
        },
        audit_id=stable_id("audocgate", gate.id),
        ignore_duplicate=True,
    )
    repository.upsert_release_gate(gate)
    _persist_gate_head(
        conn,
        mis=mis,
        repository=repository,
        gate=gate,
    )
    return gate


def _persist_gate_head(
    conn: sqlite3.Connection,
    *,
    mis: Any,
    repository: SQLiteRepository,
    gate: ReleaseGateDecision,
) -> None:
    existing = conn.execute(
        "SELECT * FROM reliability_campaign_gate_heads "
        "WHERE workspace_id=? AND campaign_id=?",
        (repository.workspace_id, gate.campaign_id),
    ).fetchone()
    previous_gate_id = None if existing is None else existing["current_gate_id"]
    previous_head_audit_id = None if existing is None else existing["mis_audit_id"]
    if previous_gate_id == gate.id:
        return
    before = (
        None
        if previous_gate_id is None
        else {
            "campaign_id": gate.campaign_id,
            "current_gate_id": previous_gate_id,
        }
    )
    after = {"campaign_id": gate.campaign_id, "current_gate_id": gate.id}
    audit_id = stable_id(
        "audochead",
        gate.campaign_id,
        previous_gate_id or "none",
        gate.id,
        previous_head_audit_id or "none",
    )
    mis.audit(
        conn,
        "system",
        "open-cekura-gate-head",
        "open_cekura.release_gate.head",
        "reliability_campaign_gate_head",
        gate.campaign_id,
        before,
        after,
        {
            "campaign_id": gate.campaign_id,
            "previous_gate_id": previous_gate_id,
            "current_gate_id": gate.id,
            "previous_head_audit_id": previous_head_audit_id,
            "raw_transcript_omitted": True,
        },
        audit_id=audit_id,
        ignore_duplicate=True,
    )
    audit = conn.execute(
        "SELECT created_at FROM audit_logs WHERE audit_id=?", (audit_id,)
    ).fetchone()
    if audit is None:
        raise CampaignServiceError(
            "mis_gate_head_audit_failed",
            "release gate head Audit mapping was not recorded",
        )
    values = {
        "workspace_id": repository.workspace_id,
        "campaign_id": gate.campaign_id,
        "current_gate_id": gate.id,
        "mis_audit_id": audit_id,
        "updated_at": audit["created_at"],
    }
    if existing is None:
        conn.execute(
            """INSERT INTO reliability_campaign_gate_heads(
                workspace_id,campaign_id,current_gate_id,mis_audit_id,updated_at
            ) VALUES(
                :workspace_id,:campaign_id,:current_gate_id,:mis_audit_id,:updated_at
            )""",
            values,
        )
    else:
        conn.execute(
            """UPDATE reliability_campaign_gate_heads
            SET current_gate_id=:current_gate_id,
                mis_audit_id=:mis_audit_id,
                updated_at=:updated_at
            WHERE workspace_id=:workspace_id AND campaign_id=:campaign_id""",
            values,
        )


def _campaign_summary(
    execution: CampaignExecution,
    *,
    mappings: PersistedCampaignMappings,
    gate_input: CampaignGateInput,
    manifests: tuple[dict[str, Any], ...],
    git_commit_sha: str,
    workspace_id: str,
) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "campaign": execution.campaign.model_dump(mode="json"),
        "campaign_created_at": _utc_text(execution.campaign.created_at),
        "workspace_id": workspace_id,
        "agent": execution.agent.model_dump(mode="json"),
        "agent_version": execution.agent_version.model_dump(mode="json"),
        "agent_config_sha256": execution.agent_config.canonical_sha256(),
        "scenario_suite": execution.scenario_suite.model_dump(mode="json"),
        "scenario_ids": [record.scenario.id for record in execution.records],
        "run_ids": [record.simulation.run.id for record in execution.records],
        "metrics": execution.metrics.model_dump(mode="json"),
        "gate_input": gate_input.model_dump(mode="json"),
        "manifest_ids": [manifest["id"] for manifest in manifests],
        "plan_evidence": [
            {
                "run_id": manifest["run_id"],
                "status": manifest["plan_evidence_status"],
                "reason": manifest["plan_evidence_unavailable_reason"],
                "mis_plan_evidence_manifest_id": manifest[
                    "mis_plan_evidence_manifest_id"
                ],
            }
            for manifest in manifests
        ],
        "mis_task_id": mappings.mis_task_id,
        "mis_plan_id": mappings.mis_plan_id,
        "git_commit_sha": git_commit_sha,
        "failure_count": len(execution.failures),
        "regression_count": len(execution.regressions),
    }


def _mapped_regressions(
    execution: CampaignExecution, mappings: PersistedCampaignMappings
) -> tuple[Any, ...]:
    return tuple(
        regression.model_copy(
            update={"mis_memory_id": mappings.mis_memory_ids.get(regression.id)}
        ).model_dump(mode="json")
        for regression in execution.regressions
    )


def _load_campaign_facts(artifact_root: Path, campaign_id: str) -> dict[str, Any]:
    report = verify_campaign(artifact_root, campaign_id)
    if not report.ok:
        raise CampaignServiceError(
            "evidence_verification_failed", _verification_message(report)
        )
    try:
        summary_envelope = verified_campaign_json(
            report,
            "campaign_summary.json",
        )
        summary = summary_envelope["summary"]
        gate_input = CampaignGateInput.model_validate_json(
            canonical_json_bytes(summary["gate_input"])
        )
        created_at = _parse_utc(summary["campaign_created_at"])
        diff = verified_campaign_json(
            report,
            "baseline_candidate_diff.json",
        )
        regressions = verified_campaign_json(
            report,
            "regression_cases.json",
        )
        release_gate = ReleaseGateDecision.model_validate_json(
            canonical_json_bytes(
                verified_campaign_json(report, "release_gate.json")
            )
        )
        gate_history_index = verified_campaign_json(report, "gate_history.json")
        current_gate_id = gate_history_index["current_gate_id"]
        gate_history = tuple(
            GateSnapshotInputs(
                release_gate=json.loads(snapshot.release_gate.decode("utf-8")),
                baseline_candidate_diff=json.loads(
                    snapshot.baseline_candidate_diff.decode("utf-8")
                ),
            )
            for snapshot in report.gate_snapshots
        )
    except (
        OSError,
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        ValidationError,
        EvidenceError,
    ) as exc:
        raise CampaignServiceError(
            "campaign_summary_invalid", "verified campaign summary cannot be reloaded"
        ) from exc
    if (
        not isinstance(summary, dict)
        or not isinstance(regressions, list)
        or not isinstance(gate_history_index, dict)
        or not isinstance(current_gate_id, str)
    ):
        raise CampaignServiceError(
            "campaign_summary_invalid", "campaign summary has an invalid shape"
        )
    return {
        "summary": summary,
        "gate_input": gate_input,
        "created_at": created_at,
        "diff": diff,
        "regression_cases": regressions,
        "release_gate": release_gate,
        "gate_history": gate_history,
        "current_gate_id": current_gate_id,
        "tree_sha256": campaign_tree_sha256(artifact_root / campaign_id),
        "verification_report": report,
    }


def _require_persisted_campaign(
    repository: SQLiteRepository, campaign_id: str
) -> Mapping[str, Any]:
    campaign = repository.get_campaign(campaign_id)
    if campaign is None:
        raise CampaignServiceError(
            "campaign_not_found",
            f"campaign {campaign_id} is not persisted in workspace {repository.workspace_id}",
        )
    return campaign


def _require_authoritative_campaign(
    conn: sqlite3.Connection,
    *,
    mis: Any,
    repository: SQLiteRepository,
    facts: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Reconcile verified files with both vertical and core MIS authority."""

    gate_input = facts.get("gate_input")
    if not isinstance(gate_input, CampaignGateInput):
        raise CampaignServiceError(
            "campaign_ledger_mismatch",
            "verified campaign facts cannot be reconciled with the MIS ledger",
        )
    campaign = _require_persisted_campaign(repository, gate_input.campaign_id)
    try:
        matches = _campaign_authority_matches(
            conn,
            mis=mis,
            repository=repository,
            campaign=campaign,
            facts=facts,
        )
    except (OSError, sqlite3.Error, ValidationError, ValueError, TypeError) as exc:
        raise CampaignServiceError(
            "campaign_ledger_mismatch",
            f"campaign {gate_input.campaign_id} evidence does not match its authoritative MIS ledger",
        ) from exc
    if not matches:
        raise CampaignServiceError(
            "campaign_ledger_mismatch",
            f"campaign {gate_input.campaign_id} evidence does not match its authoritative MIS ledger",
        )
    return campaign


def _campaign_authority_matches(
    conn: sqlite3.Connection,
    *,
    mis: Any,
    repository: SQLiteRepository,
    campaign: Mapping[str, Any],
    facts: Mapping[str, Any],
) -> bool:
    gate_input = facts["gate_input"]
    summary = facts.get("summary")
    report = facts.get("verification_report")
    if (
        not isinstance(gate_input, CampaignGateInput)
        or not isinstance(summary, dict)
        or not isinstance(report, VerificationReport)
        or not report.ok
        or report.campaign_id != gate_input.campaign_id
        or campaign.get("campaign_id") != gate_input.campaign_id
        or campaign.get("workspace_id") != repository.workspace_id
        or campaign.get("status") != "completed"
        or not campaign.get("mis_task_id")
        or not campaign.get("mis_plan_id")
        or summary.get("workspace_id") != repository.workspace_id
        or summary.get("mis_task_id") != campaign.get("mis_task_id")
        or summary.get("mis_plan_id") != campaign.get("mis_plan_id")
        or not _audit_chain_is_valid(conn)
    ):
        return False

    task = conn.execute(
        "SELECT * FROM tasks WHERE task_id=?",
        (campaign["mis_task_id"],),
    ).fetchone()
    plan = conn.execute(
        "SELECT * FROM agent_plans WHERE plan_id=?",
        (campaign["mis_plan_id"],),
    ).fetchone()
    if (
        task is None
        or plan is None
        or task["workspace_id"] != repository.workspace_id
        or plan["workspace_id"] != repository.workspace_id
        or plan["task_id"] != campaign["mis_task_id"]
        or plan["agent_id"] != task["owner_agent_id"]
        or not _vertical_campaign_hierarchy_matches(
            conn,
            repository=repository,
            campaign=campaign,
            summary=summary,
            report=report,
            gate_input=gate_input,
        )
        or not _manifest_authority_sets_match(
            conn,
            repository=repository,
            campaign=campaign,
            report=report,
        )
    ):
        return False

    run_rows = {
        row["run_id"]: row
        for row in conn.execute(
            """SELECT * FROM reliability_conversation_runs
            WHERE workspace_id=? AND campaign_id=?""",
            (repository.workspace_id, gate_input.campaign_id),
        ).fetchall()
    }
    expected_run_ids = set(gate_input.scenario_by_run)
    if set(run_rows) != expected_run_ids or len(report.runs) != len(expected_run_ids):
        return False

    verified_evaluations: dict[str, EvaluationResult] = {}
    for verified_run in report.runs:
        manifest = verified_run.manifest
        run_row = run_rows.get(verified_run.run_id)
        if manifest is None or run_row is None or not verified_run.ok:
            return False
        if (
            run_row["scenario_id"]
            != gate_input.scenario_by_run.get(verified_run.run_id)
            or not run_row["mis_run_id"]
            or _parse_utc(run_row["created_at"]) != manifest.started_at
        ):
            return False

        core_run = conn.execute(
            "SELECT * FROM runs WHERE run_id=?",
            (run_row["mis_run_id"],),
        ).fetchone()
        if (
            core_run is None
            or core_run["workspace_id"] != repository.workspace_id
            or core_run["task_id"] != campaign["mis_task_id"]
            or core_run["agent_id"] != task["owner_agent_id"]
            or core_run["agent_plan_id"] != campaign["mis_plan_id"]
            or _parse_utc(core_run["started_at"]) != manifest.started_at
            or _parse_utc(core_run["ended_at"]) != manifest.finished_at
        ):
            return False

        evaluations = _verified_run_evaluations(verified_run)
        verified_evaluations.update(
            {evaluation.id: evaluation for evaluation in evaluations}
        )
        turns = _TURN_RESULTS_ADAPTER.validate_json(
            _verified_run_artifact_bytes(verified_run, "transcript.json")
        )
        tool_calls = _TOOL_CALL_RESULTS_ADAPTER.validate_json(
            _verified_run_artifact_bytes(verified_run, "tool_calls.json")
        )
        expected_run_state = _deterministic_final_state(tuple(evaluations))
        expected_core_status = (
            "failed" if expected_run_state is RunFinalState.ERROR else "completed"
        )
        if (
            run_row["status"] != expected_run_state.value
            or core_run["status"] != expected_core_status
            or not _observation_authority_matches(
                conn,
                mis=mis,
                repository=repository,
                run_row=run_row,
                core_run=core_run,
                turns=turns,
                tool_calls=tool_calls,
            )
            or not _evaluation_authority_matches(
                conn,
                repository=repository,
                campaign=campaign,
                run_row=run_row,
                core_run=core_run,
                evaluations=evaluations,
            )
        ):
            return False

        persisted_manifest = repository.get_evidence_manifest(manifest.id)
        if (
            not _manifest_projection_matches(persisted_manifest, manifest)
            or not _mis_evidence_anchor_is_valid(
                conn,
                mis=mis,
                repository=repository,
                manifest=manifest,
                campaign=campaign,
                tool_calls=tool_calls,
                evaluations=evaluations,
            )
        ):
            return False
    if not _regression_authority_matches(
        conn,
        mis=mis,
        repository=repository,
        campaign=campaign,
        run_rows=run_rows,
        regression_payload=facts.get("regression_cases"),
        evaluations=verified_evaluations,
    ):
        return False
    return _gate_history_authority_matches(
        conn,
        repository=repository,
        campaign=campaign,
        snapshots=facts.get("gate_history"),
        current_gate_id=facts.get("current_gate_id"),
    )


def _vertical_campaign_hierarchy_matches(
    conn: sqlite3.Connection,
    *,
    repository: SQLiteRepository,
    campaign: Mapping[str, Any],
    summary: Mapping[str, Any],
    report: VerificationReport,
    gate_input: CampaignGateInput,
) -> bool:
    try:
        summary_campaign = Campaign.model_validate_json(
            canonical_json_bytes(summary.get("campaign"))
        )
        agent = AgentUnderTest.model_validate_json(
            canonical_json_bytes(summary.get("agent"))
        )
        agent_version = AgentVersion.model_validate_json(
            canonical_json_bytes(summary.get("agent_version"))
        )
        suite = ScenarioSuite.model_validate_json(
            canonical_json_bytes(summary.get("scenario_suite"))
        )
    except ValidationError:
        return False
    if (
        summary_campaign.id != gate_input.campaign_id
        or summary_campaign.agent_version_id != agent_version.id
        or summary_campaign.scenario_suite_id != suite.id
        or agent_version.agent_id != agent.id
        or agent.workspace_id != repository.workspace_id
        or campaign.get("agent_version_id") != agent_version.id
        or campaign.get("scenario_suite_id") != suite.id
        or campaign.get("status") != summary_campaign.status.value
        or _parse_utc(campaign.get("created_at")) != summary_campaign.created_at
        or summary.get("agent_config_sha256") != agent_version.config_sha256
    ):
        return False
    agent_row = conn.execute(
        "SELECT * FROM reliability_agents WHERE workspace_id=? AND agent_id=?",
        (repository.workspace_id, agent.id),
    ).fetchone()
    version_row = conn.execute(
        "SELECT * FROM reliability_agent_versions "
        "WHERE workspace_id=? AND agent_version_id=?",
        (repository.workspace_id, agent_version.id),
    ).fetchone()
    suite_row = conn.execute(
        "SELECT * FROM reliability_scenario_suites "
        "WHERE workspace_id=? AND suite_id=?",
        (repository.workspace_id, suite.id),
    ).fetchone()
    if (
        agent_row is None
        or version_row is None
        or suite_row is None
        or agent_row["schema_version"] != agent.schema_version
        or agent_row["name"] != agent.name
        or agent_row["description"] != agent.description
        or version_row["schema_version"] != agent_version.schema_version
        or version_row["agent_id"] != agent.id
        or version_row["version"] != agent_version.version
        or version_row["adapter_kind"] != agent_version.adapter_kind.value
        or version_row["config_sha256"] != agent_version.config_sha256
        or suite_row["schema_version"] != suite.schema_version
        or suite_row["name"] != suite.name
        or suite_row["description"] != suite.description
    ):
        return False

    contracts: dict[str, tuple[ScenarioDefinition, str]] = {}
    try:
        for verified_run in report.runs:
            source = _verified_run_artifact_bytes(verified_run, "scenario.yaml")
            contract = ScenarioDefinition.model_validate_json(
                canonical_json_bytes(yaml.safe_load(source))
            )
            digest = sha256_bytes(source)
            previous = contracts.get(contract.id)
            if previous is not None and previous != (contract, digest):
                return False
            contracts[contract.id] = (contract, digest)
    except (OSError, TypeError, ValueError, yaml.YAMLError, ValidationError):
        return False
    if set(contracts) != set(gate_input.scenario_ids):
        return False
    scenario_rows = {
        row["scenario_id"]: row
        for row in conn.execute(
            "SELECT * FROM reliability_scenarios "
            "WHERE workspace_id=? AND suite_id=?",
            (repository.workspace_id, suite.id),
        ).fetchall()
    }
    if set(scenario_rows) != set(contracts):
        return False
    expected_persona_ids: set[str] = set()
    for scenario_id, (contract, digest) in contracts.items():
        if gate_input.scenario_sha256_by_id.get(scenario_id) != digest:
            return False
        spec = contract.persona
        persona_id = stable_id(
            "ocpersona", spec.language, spec.tone, spec.verbosity.value
        )
        expected_persona_ids.add(persona_id)
        persona = conn.execute(
            "SELECT * FROM reliability_personas "
            "WHERE workspace_id=? AND persona_id=?",
            (repository.workspace_id, persona_id),
        ).fetchone()
        row = scenario_rows[scenario_id]
        contract_json = contract.model_dump(mode="json")
        if (
            persona is None
            or persona["schema_version"] != 1
            or persona["name"] != f"{spec.tone} {spec.language} persona"
            or persona["language"] != spec.language
            or persona["tone"] != spec.tone
            or persona["verbosity"] != spec.verbosity.value
            or row["schema_version"] != contract.schema_version
            or row["persona_id"] != persona_id
            or row["name"] != contract.name
            or row["initial_message"] != contract.initial_message
            or row["goal_type"] != contract.goal.type.value
            or row["source_sha256"] != digest
            or json.loads(row["persona_json"]) != contract_json["persona"]
            or json.loads(row["goal_json"]) != contract_json["goal"]
            or json.loads(row["challenges_json"]) != contract_json["challenges"]
            or json.loads(row["expectations_json"])
            != contract_json["expectations"]
            or json.loads(row["tags_json"]) != contract_json["tags"]
        ):
            return False
    relevant_personas = {
        row["persona_id"]
        for row in conn.execute(
            """SELECT DISTINCT p.persona_id FROM reliability_personas p
            JOIN reliability_scenarios s
              ON s.workspace_id=p.workspace_id AND s.persona_id=p.persona_id
            WHERE s.workspace_id=? AND s.suite_id=?""",
            (repository.workspace_id, suite.id),
        ).fetchall()
    }
    return relevant_personas == expected_persona_ids


def _manifest_authority_sets_match(
    conn: sqlite3.Connection,
    *,
    repository: SQLiteRepository,
    campaign: Mapping[str, Any],
    report: VerificationReport,
) -> bool:
    manifests = [run.manifest for run in report.runs]
    if any(manifest is None for manifest in manifests):
        return False
    typed = [manifest for manifest in manifests if manifest is not None]
    expected_manifest_ids = {manifest.id for manifest in typed}
    rows = conn.execute(
        "SELECT manifest_id,run_id FROM reliability_evidence_manifests "
        "WHERE workspace_id=? AND campaign_id=?",
        (repository.workspace_id, campaign["campaign_id"]),
    ).fetchall()
    if (
        {row["manifest_id"] for row in rows} != expected_manifest_ids
        or len({row["run_id"] for row in rows}) != len(rows)
    ):
        return False
    expected_artifact_ids = {
        manifest.mis_artifact_id for manifest in typed if manifest.mis_artifact_id
    }
    artifact_ids = {
        row["artifact_id"]
        for row in conn.execute(
            """SELECT artifact_id FROM artifacts
            WHERE task_id=? AND artifact_type='open_cekura_evidence'""",
            (campaign.get("mis_task_id"),),
        ).fetchall()
    }
    expected_plan_manifest_ids = {
        manifest.mis_plan_evidence_manifest_id
        for manifest in typed
        if manifest.mis_plan_evidence_manifest_id
    }
    plan_manifest_ids = {
        row["manifest_id"]
        for row in conn.execute(
            "SELECT manifest_id FROM plan_evidence_manifests WHERE plan_id=?",
            (campaign.get("mis_plan_id"),),
        ).fetchall()
    }
    return (
        len(expected_artifact_ids) == len(typed)
        and artifact_ids == expected_artifact_ids
        and plan_manifest_ids == expected_plan_manifest_ids
    )


def _observation_authority_matches(
    conn: sqlite3.Connection,
    *,
    mis: Any,
    repository: SQLiteRepository,
    run_row: Mapping[str, Any],
    core_run: Mapping[str, Any],
    turns: list[ConversationTurn],
    tool_calls: list[ObservedToolCall],
) -> bool:
    turn_rows = {
        row["turn_id"]: row
        for row in conn.execute(
            "SELECT * FROM reliability_conversation_turns "
            "WHERE workspace_id=? AND run_id=?",
            (repository.workspace_id, run_row["run_id"]),
        ).fetchall()
    }
    if set(turn_rows) != {turn.id for turn in turns}:
        return False
    for turn in turns:
        row = turn_rows[turn.id]
        if (
            row["schema_version"] != turn.schema_version
            or row["run_id"] != turn.run_id
            or row["turn_index"] != turn.turn_index
            or row["role"] != turn.role.value
            or row["content"] != turn.content
            or _parse_utc(row["created_at"]) != turn.created_at
        ):
            return False

    projection_rows = {
        row["tool_call_id"]: row
        for row in conn.execute(
            "SELECT * FROM reliability_observed_tool_calls "
            "WHERE workspace_id=? AND run_id=?",
            (repository.workspace_id, run_row["run_id"]),
        ).fetchall()
    }
    if set(projection_rows) != {call.id for call in tool_calls}:
        return False
    expected_core_ids = {
        stable_id("tcloc", core_run["run_id"], call.id) for call in tool_calls
    }
    core_rows = {
        row["tool_call_id"]: row
        for row in conn.execute(
            "SELECT * FROM tool_calls WHERE run_id=?", (core_run["run_id"],)
        ).fetchall()
    }
    if set(core_rows) != expected_core_ids:
        return False
    for call in tool_calls:
        projection = projection_rows[call.id]
        expected_core_id = stable_id("tcloc", core_run["run_id"], call.id)
        if (
            call.mis_tool_call_id != expected_core_id
            or projection["schema_version"] != call.schema_version
            or projection["run_id"] != call.run_id
            or projection["turn_id"] != call.turn_id
            or projection["name"] != call.name
            or json.loads(projection["arguments_json"]) != call.arguments
            or json.loads(projection["result_json"]) != call.result
            or projection["error"] != call.error
            or bool(projection["is_mutation"]) != call.is_mutation
            or projection["duration_ms"] != call.duration_ms
            or projection["mis_tool_call_id"] != expected_core_id
            or _parse_utc(projection["created_at"]) != call.created_at
        ):
            return False
        core = core_rows[expected_core_id]
        ended_at = call.created_at + timedelta(milliseconds=call.duration_ms)
        if (
            core["agent_id"] != core_run["agent_id"]
            or core["tool_name"] != call.name
            or core["tool_version"] != "open-cekura-observation.v1"
            or core["tool_category"] != "custom"
            or json.loads(core["normalized_args_json"])
            != safe_mis_metadata(mis, call.arguments)
            or core["target_resource"] is not None
            or core["risk_level"] != ("medium" if call.is_mutation else "low")
            or core["status"] != ("failed" if call.error else "completed")
            or core["result_summary"]
            != (
                "OpenCekura observed tool error; raw error omitted."
                if call.error
                else f"Observed {call.name} completion; raw result omitted."
            )
            or core["side_effect_id"]
            != (stable_id("sideoc", expected_core_id) if call.is_mutation else None)
            or _parse_utc(core["started_at"]) != call.created_at
            or _parse_utc(core["ended_at"]) != ended_at
            or _parse_utc(core["created_at"]) != call.created_at
        ):
            return False
    return True


def _verified_run_evaluations(verified_run: Any) -> list[EvaluationResult]:
    content = _verified_run_artifact_bytes(verified_run, "evaluations.json")
    evaluations = _EVALUATION_RESULTS_ADAPTER.validate_json(content)
    if any(evaluation.run_id != verified_run.run_id for evaluation in evaluations):
        raise ValueError("verified evaluations belong to another run")
    return evaluations


def _verified_run_artifact_bytes(verified_run: Any, artifact_name: str) -> bytes:
    manifest = verified_run.manifest
    if manifest is None or artifact_name not in manifest.artifacts:
        raise ValueError("verified run manifest is unavailable")
    path = verified_run.manifest_path.parent / artifact_name
    if is_symlink_or_reparse(path) or not path.is_file():
        raise ValueError("verified run artifact identity changed")
    with path.open("rb") as stream:
        content = stream.read(_MAX_AUTHORITY_ARTIFACT_BYTES + 1)
    if (
        len(content) > _MAX_AUTHORITY_ARTIFACT_BYTES
        or sha256_bytes(content) != manifest.artifacts.get(artifact_name)
    ):
        raise ValueError("verified run artifact content changed")
    return content


def _evaluation_authority_matches(
    conn: sqlite3.Connection,
    *,
    repository: SQLiteRepository,
    campaign: Mapping[str, Any],
    run_row: Mapping[str, Any],
    core_run: Mapping[str, Any],
    evaluations: list[EvaluationResult],
) -> bool:
    projection_rows = {
        row["evaluation_id"]: row
        for row in conn.execute(
            """SELECT * FROM reliability_evaluation_results
            WHERE workspace_id=? AND run_id=?""",
            (repository.workspace_id, run_row["run_id"]),
        ).fetchall()
    }
    if set(projection_rows) != {evaluation.id for evaluation in evaluations}:
        return False
    for evaluation in evaluations:
        projection = projection_rows[evaluation.id]
        if not _evaluation_projection_matches(projection, evaluation):
            return False
        expected_mis_id = (
            None
            if evaluation.status is EvaluationStatus.SKIPPED
            else stable_id("evaloc", run_row["mis_run_id"], evaluation.id)
        )
        if evaluation.mis_evaluation_id != expected_mis_id:
            return False
        if expected_mis_id is None:
            continue
        core = conn.execute(
            "SELECT * FROM evaluations WHERE evaluation_id=?",
            (expected_mis_id,),
        ).fetchone()
        expected_score = (
            0.0 if evaluation.status is EvaluationStatus.ERROR else evaluation.score
        )
        expected_rubric = {
            "schema_version": "open_cekura.mis_evaluation.v1",
            "evaluator_id": evaluation.evaluator_id,
            "vertical_status": evaluation.status.value,
            "threshold": evaluation.threshold,
            "reason_codes": list(evaluation.reason_codes),
            "evidence_refs": list(evaluation.evidence_refs),
        }
        if (
            core is None
            or core["task_id"] != campaign["mis_task_id"]
            or core["run_id"] != run_row["mis_run_id"]
            or core["agent_id"] != core_run["agent_id"]
            or core["evaluator_type"] != "rule"
            or core["score"] != expected_score
            or core["pass_fail"]
            != ("pass" if evaluation.status is EvaluationStatus.PASS else "fail")
            or json.loads(core["rubric_json"]) != expected_rubric
            or core["notes"]
            != f"OpenCekura {evaluation.evaluator_id}: {evaluation.status.value}"
            or _parse_utc(core["created_at"]) != evaluation.created_at
        ):
            return False
    return True


def _evaluation_projection_matches(
    row: Mapping[str, Any], evaluation: EvaluationResult
) -> bool:
    data = evaluation.model_dump(mode="json")
    return all(
        (
            row["schema_version"] == data["schema_version"],
            row["evaluation_id"] == data["id"],
            row["run_id"] == data["run_id"],
            row["evaluator_id"] == data["evaluator_id"],
            row["status"] == data["status"],
            row["score"] == data["score"],
            row["threshold"] == data["threshold"],
            json.loads(row["reason_codes_json"]) == data["reason_codes"],
            json.loads(row["evidence_refs_json"]) == data["evidence_refs"],
            json.loads(row["metadata_json"]) == data["metadata"],
            row["mis_evaluation_id"] == data["mis_evaluation_id"],
            _parse_utc(row["created_at"]) == evaluation.created_at,
        )
    )


def _manifest_projection_matches(
    row: Mapping[str, Any] | None, manifest: EvidenceManifest
) -> bool:
    if row is None:
        return False
    data = manifest.model_dump(mode="json")
    return all(
        (
            row.get("schema_version") == data["schema_version"],
            row.get("manifest_id") == data["id"],
            row.get("campaign_id") == data["campaign_id"],
            row.get("run_id") == data["run_id"],
            row.get("mis_artifact_id") == data["mis_artifact_id"],
            row.get("mis_plan_evidence_manifest_id")
            == data["mis_plan_evidence_manifest_id"],
            row.get("git_commit_sha") == data["git_commit_sha"],
            row.get("environment") == data["environment"],
            row.get("scenario_sha256") == data["scenario_sha256"],
            row.get("agent_config_sha256") == data["agent_config_sha256"],
            row.get("evaluator_versions") == data["evaluator_versions"],
            row.get("artifacts") == data["artifacts"],
            _parse_utc(row.get("started_at")) == manifest.started_at,
            _parse_utc(row.get("finished_at")) == manifest.finished_at,
            row.get("final_state") == data["final_state"],
            _parse_utc(row.get("created_at")) == manifest.created_at,
        )
    )


def _regression_authority_matches(
    conn: sqlite3.Connection,
    *,
    mis: Any,
    repository: SQLiteRepository,
    campaign: Mapping[str, Any],
    run_rows: Mapping[str, Mapping[str, Any]],
    regression_payload: object,
    evaluations: Mapping[str, EvaluationResult],
) -> bool:
    try:
        regressions = _REGRESSION_RESULTS_ADAPTER.validate_json(
            canonical_json_bytes(regression_payload)
        )
    except ValidationError:
        return False
    expected_regression_ids = {regression.id for regression in regressions}
    placeholders = ",".join("?" for _ in run_rows)
    if not placeholders:
        return not regressions
    regression_rows = {
        row["regression_id"]: row
        for row in conn.execute(
            f"""SELECT * FROM reliability_regressions
            WHERE workspace_id=? AND source_run_id IN ({placeholders})""",
            (repository.workspace_id, *run_rows),
        ).fetchall()
    }
    if set(regression_rows) != expected_regression_ids:
        return False
    expected_failure_ids = {regression.failure_case_id for regression in regressions}
    failure_rows = {
        row["failure_id"]: row
        for row in conn.execute(
            f"""SELECT * FROM reliability_failures
            WHERE workspace_id=? AND run_id IN ({placeholders})""",
            (repository.workspace_id, *run_rows),
        ).fetchall()
    }
    if set(failure_rows) != expected_failure_ids:
        return False
    expected_memory_ids = {
        stable_id("memoc", campaign["mis_task_id"], regression.id)
        for regression in regressions
    }
    memory_rows = {
        row["memory_id"]: row
        for row in conn.execute(
            """SELECT * FROM memories
            WHERE task_id=? AND memory_type='failure_case'""",
            (campaign["mis_task_id"],),
        ).fetchall()
    }
    if set(memory_rows) != expected_memory_ids:
        return False
    for regression in regressions:
        row = regression_rows[regression.id]
        evaluation_matches = [
            evaluation
            for evaluation in evaluations.values()
            if evaluation.run_id == regression.source_run_id
            and evaluation.evaluator_id == regression.evaluator_id
            and f"evaluation:{evaluation.id}" in regression.evidence_refs
        ]
        if len(evaluation_matches) != 1:
            return False
        evaluation = evaluation_matches[0]
        expected_memory_id = stable_id(
            "memoc", campaign["mis_task_id"], regression.id
        )
        if (
            regression.mis_memory_id != expected_memory_id
            or row["schema_version"] != regression.schema_version
            or row["failure_case_id"] != regression.failure_case_id
            or row["scenario_id"] != regression.scenario_id
            or row["source_run_id"] != regression.source_run_id
            or row["name"] != regression.name
            or json.loads(row["original_input_json"]) != regression.original_input
            or json.loads(row["expected_json"]) != regression.expected
            or json.loads(row["observed_json"]) != regression.observed
            or row["reason_code"] != regression.reason_code
            or row["evaluator_id"] != regression.evaluator_id
            or json.loads(row["evidence_refs_json"]) != regression.evidence_refs
            or row["mis_memory_id"] != expected_memory_id
            or _parse_utc(row["created_at"]) != regression.created_at
        ):
            return False
        failure = failure_rows[regression.failure_case_id]
        if (
            failure["schema_version"] != 1
            or failure["run_id"] != regression.source_run_id
            or failure["scenario_id"] != regression.scenario_id
            or failure["evaluation_result_id"] != evaluation.id
            or failure["reason_code"] != regression.reason_code
            or json.loads(failure["expected_json"]) != regression.expected
            or json.loads(failure["observed_json"]) != regression.observed
            or json.loads(failure["evidence_refs_json"])
            != regression.evidence_refs
            or _parse_utc(failure["created_at"]) != evaluation.created_at
        ):
            return False
        vertical_run = run_rows[regression.source_run_id]
        memory = memory_rows[expected_memory_id]
        canonical = safe_mis_metadata(
            mis,
            {
                "schema_version": "open_cekura.regression_memory.v1",
                "regression_case_id": regression.id,
                "original_failing_input": regression.original_input,
                "expected_state": regression.expected,
                "observed_state": regression.observed,
                "failure_reason": regression.reason_code,
                "source_run": vertical_run["mis_run_id"],
                "evaluator": regression.evaluator_id,
                "evidence_refs": regression.evidence_refs,
            },
        )
        expected_memory = {
            "workspace_id": repository.workspace_id,
            "scope": "task",
            "memory_type": "failure_case",
            "canonical_text": mis.redact_text(_compact_json(canonical), 10_000),
            "source_type": "run_log",
            "source_ref": vertical_run["mis_run_id"],
            "project_id": "open-cekura-reliability-lab",
            "task_id": campaign["mis_task_id"],
            "agent_id": conn.execute(
                "SELECT owner_agent_id FROM tasks WHERE task_id=?",
                (campaign["mis_task_id"],),
            ).fetchone()[0],
            "confidence": 1.0,
            "review_status": "candidate",
            "owner_user_id": None,
            "ttl_review_due_at": None,
            "supersedes_memory_id": None,
            "access_tags": _compact_json(
                ["open-cekura", "reliability-regression"]
            ),
        }
        if any(memory[key] != value for key, value in expected_memory.items()) or (
            _parse_utc(memory["created_at"]) != regression.created_at
            or _parse_utc(memory["updated_at"]) != regression.created_at
        ):
            return False
        audit = conn.execute(
            "SELECT * FROM audit_logs WHERE audit_id=?",
            (stable_id("audocmem", expected_memory_id, "propose"),),
        ).fetchone()
        expected_metadata = {
            "campaign_id": campaign["campaign_id"],
            "regression_case_id": regression.id,
            "basis": "deterministic_failure_evidence",
            "authority_granted": False,
            "raw_transcript_omitted": True,
        }
        if (
            audit is None
            or audit["action"] != "open_cekura.regression_memory.propose"
            or audit["entity_type"] != "memories"
            or audit["entity_id"] != expected_memory_id
            or audit["after_hash"]
            != _mis_stable_hash({"review_status": "candidate"})
            or json.loads(audit["metadata_json"]) != expected_metadata
        ):
            return False
    return True


def _compact_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _gate_history_authority_matches(
    conn: sqlite3.Connection,
    *,
    repository: SQLiteRepository,
    campaign: Mapping[str, Any],
    snapshots: object,
    current_gate_id: object,
) -> bool:
    if (
        not isinstance(snapshots, tuple)
        or not snapshots
        or not isinstance(current_gate_id, str)
    ):
        return False
    gates: dict[str, ReleaseGateDecision] = {}
    for snapshot in snapshots:
        if not isinstance(snapshot, GateSnapshotInputs):
            return False
        try:
            gate = ReleaseGateDecision.model_validate_json(
                canonical_json_bytes(snapshot.release_gate)
            )
        except (ValidationError, TypeError, ValueError, RecursionError):
            return False
        if gate.campaign_id != campaign.get("campaign_id"):
            return False
        existing = gates.get(gate.id)
        if existing is not None and existing != gate:
            return False
        gates[gate.id] = gate
    if current_gate_id not in gates:
        return False
    ledger_ids = {
        row["gate_id"]
        for row in conn.execute(
            """SELECT gate_id FROM reliability_release_gates
            WHERE workspace_id=? AND campaign_id=?""",
            (repository.workspace_id, campaign["campaign_id"]),
        ).fetchall()
    }
    if ledger_ids != set(gates):
        return False
    expected_approval_ids = {
        gate.mis_approval_id for gate in gates.values() if gate.mis_approval_id
    }
    approval_ids = {
        row["approval_id"]
        for row in conn.execute(
            """SELECT approval_id FROM approvals
            WHERE task_id=? AND subject_type='reliability_release_gate'""",
            (campaign.get("mis_task_id"),),
        ).fetchall()
    }
    if approval_ids != expected_approval_ids:
        return False
    gate_audit_rows = []
    for row in conn.execute(
        """SELECT * FROM audit_logs
        WHERE action='open_cekura.release_gate.evaluate'
          AND entity_type='reliability_release_gate'"""
    ).fetchall():
        try:
            metadata = json.loads(row["metadata_json"])
        except (TypeError, json.JSONDecodeError):
            return False
        if isinstance(metadata, dict) and metadata.get("campaign_id") == campaign.get(
            "campaign_id"
        ):
            gate_audit_rows.append(row)
    if {row["audit_id"] for row in gate_audit_rows} != {
        stable_id("audocgate", gate_id) for gate_id in gates
    }:
        return False
    for gate in gates.values():
        if (
            not _gate_projection_matches(repository.get_release_gate(gate.id), gate)
            or not _mis_gate_anchor_is_valid(
                conn,
                release_gate=gate,
                campaign=campaign,
                workspace_id=repository.workspace_id,
            )
        ):
            return False
    head = conn.execute(
        "SELECT * FROM reliability_campaign_gate_heads "
        "WHERE workspace_id=? AND campaign_id=?",
        (repository.workspace_id, campaign["campaign_id"]),
    ).fetchone()
    return bool(
        head is not None
        and head["current_gate_id"] == current_gate_id
        and _gate_head_audit_is_valid(conn, head)
    )


def _gate_projection_matches(
    row: Mapping[str, Any] | None, gate: ReleaseGateDecision
) -> bool:
    if row is None:
        return False
    data = gate.model_dump(mode="json")
    return all(
        (
            row.get("schema_version") == data["schema_version"],
            row.get("gate_id") == data["id"],
            row.get("campaign_id") == data["campaign_id"],
            row.get("baseline_campaign_id") == data["baseline_campaign_id"],
            row.get("decision") == data["decision"],
            row.get("policy_version") == data["policy_version"],
            row.get("blockers") == data["blockers"],
            row.get("warnings") == data["warnings"],
            row.get("metrics") == data["metrics"],
            row.get("evidence_refs") == data["evidence_refs"],
            row.get("mis_approval_id") == data["mis_approval_id"],
            _parse_utc(row.get("created_at")) == gate.created_at,
        )
    )


def _comparison_payload(
    baseline_campaign_id: str,
    candidate_campaign_id: str,
    baseline: CampaignGateInput,
    candidate: CampaignGateInput,
) -> dict[str, Any]:
    baseline_metrics = baseline.metrics
    candidate_metrics = candidate.metrics
    return {
        "schema_version": 1,
        "baseline_campaign_id": baseline_campaign_id,
        "candidate_campaign_id": candidate_campaign_id,
        "baseline_metrics": baseline_metrics.model_dump(mode="json"),
        "candidate_metrics": candidate_metrics.model_dump(mode="json"),
        "delta": {
            "task_success_percentage_points": _rate_delta(
                baseline_metrics.task_success_rate,
                candidate_metrics.task_success_rate,
                scale=100.0,
            ),
            "median_turns_ratio": _ratio_delta(
                baseline_metrics.median_turns, candidate_metrics.median_turns
            ),
            "timeout_rate": _rate_delta(
                baseline_metrics.timeout_rate, candidate_metrics.timeout_rate
            ),
        },
    }


def _gate_evidence_view_is_current(
    facts: Mapping[str, Any],
    gate: ReleaseGateDecision,
    comparison: Mapping[str, Any] | None,
) -> bool:
    expected_diff: Mapping[str, Any] = (
        comparison
        if comparison is not None
        else {
            "schema_version": 1,
            "campaign_id": gate.campaign_id,
            "baseline": None,
            "comparison": "no_comparison",
        }
    )
    return bool(
        facts.get("release_gate") == gate
        and facts.get("current_gate_id") == gate.id
        and facts.get("diff") == expected_diff
    )


def _rate_delta(
    baseline: float | None, candidate: float | None, *, scale: float = 1.0
) -> float | None:
    return (
        None
        if baseline is None or candidate is None
        else (candidate - baseline) * scale
    )


def _ratio_delta(baseline: float | None, candidate: float | None) -> float | None:
    if baseline is None or candidate is None or baseline <= 0:
        return None
    return (candidate - baseline) / baseline


def _stored_baseline_id(value: object) -> str | None:
    if not isinstance(value, dict):
        return None
    baseline = value.get("baseline_campaign_id")
    return baseline if isinstance(baseline, str) and baseline else None


def _stage_campaign_reference_closure(
    publication: CampaignPublication,
    *,
    gate_history: tuple[GateSnapshotInputs, ...],
    additional_campaign_ids: tuple[str, ...],
) -> None:
    """Stage every campaign needed by current and historical gate snapshots."""

    pending = [
        baseline_id
        for snapshot in gate_history
        if (
            baseline_id := _stored_baseline_id(
                dict(snapshot.baseline_candidate_diff)
            )
        )
        is not None
    ]
    pending.extend(additional_campaign_ids)
    staged = {publication.campaign_id}
    while pending:
        campaign_id = pending.pop()
        if campaign_id in staged:
            continue
        if len(staged) >= 64:
            raise CampaignServiceError(
                "campaign_reference_limit_exceeded",
                "campaign evidence reference closure exceeds 64 campaigns",
            )
        facts = _load_campaign_facts(publication.artifact_root, campaign_id)
        stage_reference_campaign(publication, campaign_id)
        staged.add(campaign_id)
        pending.extend(
            baseline_id
            for snapshot in facts["gate_history"]
            if (
                baseline_id := _stored_baseline_id(
                    dict(snapshot.baseline_candidate_diff)
                )
            )
            is not None
        )


def _deterministic_final_state(evaluations: tuple[Any, ...]) -> RunFinalState:
    if not evaluations or any(
        evaluation.status in {EvaluationStatus.ERROR, EvaluationStatus.SKIPPED}
        for evaluation in evaluations
    ):
        return RunFinalState.ERROR
    if any(evaluation.status is EvaluationStatus.FAIL for evaluation in evaluations):
        return RunFinalState.FAIL
    return RunFinalState.PASS


def _git_commit_sha() -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise CampaignServiceError(
            "git_commit_unavailable", "cannot determine the repository commit"
        ) from exc
    commit = result.stdout.strip().lower()
    if (
        result.returncode != 0
        or len(commit) != 40
        or any(character not in "0123456789abcdef" for character in commit)
    ):
        raise CampaignServiceError(
            "git_commit_unavailable", "cannot determine the repository commit"
        )
    return commit


def _environment() -> EvidenceEnvironment:
    return EvidenceEnvironment(
        os=platform.platform(),
        python_version=platform.python_version(),
        node_version=_node_version(),
    )


def _node_version() -> str:
    try:
        result = subprocess.run(
            ["node", "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "MISSING"
    return (
        result.stdout.strip()
        if result.returncode == 0 and result.stdout.strip()
        else "MISSING"
    )


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_utc(value: object) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError("timestamp must be text")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _json_list(value: object) -> list[Any]:
    try:
        parsed = json.loads(str(value or "[]"))
    except (TypeError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _verification_message(report: VerificationReport) -> str:
    issues = _public_issues(report)
    codes = ", ".join(str(issue["code"]) for issue in issues[:5])
    return f"campaign evidence verification failed: {codes or 'unknown_error'}"


def _public_issues(report: VerificationReport) -> list[dict[str, Any]]:
    issues = [*report.issues]
    for run in report.runs:
        issues.extend(run.issues)
    unique: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for issue in issues:
        key = (issue.code, issue.path, issue.message)
        if key in seen:
            continue
        seen.add(key)
        unique.append(
            {
                "code": issue.code,
                "severity": issue.severity,
                "path": issue.path,
                "message": issue.message,
                "expected_sha256": issue.expected_sha256,
                "actual_sha256": issue.actual_sha256,
            }
        )
    return unique


__all__ = [
    "CampaignServiceError",
    "EXIT_BLOCKED",
    "EXIT_EVIDENCE_INVALID",
    "compare_campaigns",
    "evaluate_campaign_gate",
    "resolve_artifact_root",
    "resolve_db_path",
    "run_campaign",
    "verify_campaign_evidence",
]
