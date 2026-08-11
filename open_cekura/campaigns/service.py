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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from pydantic import ValidationError

from open_cekura.campaigns.runner import CampaignExecution, execute_mock_campaign
from open_cekura.domain.enums import EvaluationStatus, GateDecision, RunFinalState
from open_cekura.domain.ids import stable_id
from open_cekura.domain.models import (
    EvidenceEnvironment,
    EvidenceManifest,
    ReleaseGateDecision,
)
from open_cekura.evidence.bundle import (
    CampaignBundleInputs,
    RunBundleInputs,
    write_campaign_bundle,
    write_run_bundle,
)
from open_cekura.evidence.manifest import (
    EvidenceError,
    VerificationReport,
    canonical_json_bytes,
    sha256_bytes,
    verified_campaign_json,
    verify_campaign,
    verify_run_bundles,
)
from open_cekura.mis.persistence import (
    MISBridgeError,
    PersistedCampaignMappings,
    persist_campaign_execution,
)
from open_cekura.release_gate.policy import (
    CampaignGateInput,
    evaluate_release_gate,
)
from open_cekura.simulation.mock_agent import MockAgentConfig
from open_cekura.storage.repository import RepositoryError
from open_cekura.storage.sqlite_repository import SQLiteRepository


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WORKSPACE_ID = "local-demo"
DEFAULT_ARTIFACT_ROOT = REPO_ROOT / "artifacts" / "open-cekura"
EXIT_BLOCKED = 3
EXIT_EVIDENCE_INVALID = 4


class CampaignServiceError(RuntimeError):
    """A safe, user-facing orchestration failure."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def resolve_db_path(value: str | Path | None) -> Path:
    raw = value if value is not None else os.environ.get("AGENTOPS_DB_PATH")
    return Path(raw).resolve() if raw else (REPO_ROOT / "agentops_mis.db").resolve()


def resolve_artifact_root(value: str | Path | None) -> Path:
    return (
        Path(value).resolve() if value is not None else DEFAULT_ARTIFACT_ROOT.resolve()
    )


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
                artifact_root=artifacts,
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
        manifests = _write_governed_run_bundles(
            conn=conn,
            mis=mis,
            repository=repository,
            execution=execution,
            mappings=mappings,
            artifact_root=artifacts,
            git_commit_sha=git_commit_sha,
            environment=environment,
        )
        run_report = verify_run_bundles(artifacts, campaign_id)
        if not run_report.ok:
            raise CampaignServiceError(
                "evidence_verification_failed",
                _verification_message(run_report),
            )
        gate_input = execution.gate_input(evidence_verified=True)
        gate = _persist_gate(
            conn=conn,
            mis=mis,
            repository=repository,
            candidate=gate_input,
            baseline=None,
            created_at=execution.campaign.created_at,
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
            artifacts,
            CampaignBundleInputs(
                campaign_id=campaign_id,
                campaign_summary=summary,
                baseline_candidate_diff=None,
                release_gate=gate.model_dump(mode="json"),
                regression_cases=_mapped_regressions(execution, mappings),
            ),
        )
        full_report = verify_campaign(artifacts, campaign_id)
        if not full_report.ok:
            raise CampaignServiceError(
                "evidence_verification_failed",
                _verification_message(full_report),
            )
        conn.commit()
    except BaseException:
        if conn.in_transaction:
            conn.rollback()
        raise
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
    baseline = _load_campaign_facts(artifacts, baseline_campaign_id)
    candidate = _load_campaign_facts(artifacts, candidate_campaign_id)
    conn, mis = _open_mis_database(database)
    try:
        conn.execute("BEGIN IMMEDIATE")
        repository = SQLiteRepository(conn, workspace_id=workspace_id)
        repository.initialize_schema()
        _require_persisted_campaign(repository, baseline_campaign_id)
        _require_persisted_campaign(repository, candidate_campaign_id)
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
        write_campaign_bundle(
            artifacts,
            CampaignBundleInputs(
                campaign_id=candidate_campaign_id,
                campaign_summary={
                    **candidate["summary"],
                    "comparison": comparison,
                },
                baseline_candidate_diff=comparison,
                release_gate=gate.model_dump(mode="json"),
                regression_cases=tuple(candidate["regression_cases"]),
            ),
        )
        report = verify_campaign(artifacts, candidate_campaign_id)
        if not report.ok:
            raise CampaignServiceError(
                "evidence_verification_failed", _verification_message(report)
            )
        conn.commit()
    except BaseException:
        if conn.in_transaction:
            conn.rollback()
        raise
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
    candidate = _load_campaign_facts(artifacts, campaign_id)
    selected_baseline = baseline_campaign_id or _stored_baseline_id(candidate["diff"])
    baseline = (
        _load_campaign_facts(artifacts, selected_baseline)
        if selected_baseline is not None
        else None
    )
    conn, mis = _open_mis_database(database)
    try:
        conn.execute("BEGIN IMMEDIATE")
        repository = SQLiteRepository(conn, workspace_id=workspace_id)
        repository.initialize_schema()
        _require_persisted_campaign(repository, campaign_id)
        if selected_baseline is not None:
            _require_persisted_campaign(repository, selected_baseline)
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
        write_campaign_bundle(
            artifacts,
            CampaignBundleInputs(
                campaign_id=campaign_id,
                campaign_summary={
                    **candidate["summary"],
                    **({"comparison": comparison} if comparison is not None else {}),
                },
                baseline_candidate_diff=comparison,
                release_gate=gate.model_dump(mode="json"),
                regression_cases=tuple(candidate["regression_cases"]),
            ),
        )
        report = verify_campaign(artifacts, campaign_id)
        if not report.ok:
            raise CampaignServiceError(
                "evidence_verification_failed", _verification_message(report)
            )
        conn.commit()
    except BaseException:
        if conn.in_transaction:
            conn.rollback()
        raise
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
    artifact_root: Path,
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

    gate = repository.get_release_gate(facts["release_gate"].id)
    if gate is None or not gate.get("mis_approval_id"):
        return False

    savepoint = "open_cekura_closed_campaign_validation"
    conn.execute(f"SAVEPOINT {savepoint}")
    try:
        mappings = persist_campaign_execution(
            conn,
            execution,
            workspace_id=repository.workspace_id,
        )
        if (
            mappings.mis_task_id != campaign.get("mis_task_id")
            or mappings.mis_plan_id != campaign.get("mis_plan_id")
        ):
            return False
        for record in execution.records:
            run_id = record.simulation.run.id
            manifest_path = (
                artifact_root
                / campaign_id
                / run_id
                / "evidence_manifest.json"
            )
            manifest = EvidenceManifest.model_validate_json(manifest_path.read_bytes())
            repository.upsert_evidence_manifest(manifest)
            if not _mis_evidence_anchor_is_valid(
                conn,
                mis=mis,
                repository=repository,
                manifest=manifest,
                manifest_path=manifest_path,
                campaign=campaign,
            ):
                return False
        release_gate = facts["release_gate"]
        repository.upsert_release_gate(release_gate)
        if not _mis_gate_anchor_is_valid(
            conn,
            release_gate=release_gate,
            campaign=campaign,
            workspace_id=repository.workspace_id,
        ):
            return False
        return True
    except (
        MISBridgeError,
        OSError,
        sqlite3.Error,
        ValueError,
        ValidationError,
        RepositoryError,
    ):
        return False
    finally:
        conn.execute(f"ROLLBACK TO {savepoint}")
        conn.execute(f"RELEASE {savepoint}")


def _mis_evidence_anchor_is_valid(
    conn: sqlite3.Connection,
    *,
    mis: Any,
    repository: SQLiteRepository,
    manifest: EvidenceManifest,
    manifest_path: Path,
    campaign: Mapping[str, Any],
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
        "content_hash": sha256_bytes(manifest_path.read_bytes()),
    }
    if any(artifact[key] != value for key, value in expected_artifact.items()):
        return False
    if manifest.mis_plan_evidence_manifest_id is None:
        return True
    plan_manifest = conn.execute(
        "SELECT * FROM plan_evidence_manifests WHERE manifest_id=?",
        (manifest.mis_plan_evidence_manifest_id,),
    ).fetchone()
    if plan_manifest is None or plan_manifest["status"] != "verified":
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
    return all(approval[key] == value for key, value in expected.items())


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
        artifact_id = stable_id("artoc", mis_run_id, "evidence-manifest.v1")
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
    approval_id = stable_id(
        "apoc",
        candidate.campaign_id,
        baseline.campaign_id if baseline is not None else "standalone",
        "release_gate.v1",
    )
    gate = evaluate_release_gate(
        candidate,
        baseline=baseline,
        created_at=created_at,
        mis_approval_id=approval_id,
    )
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
        )
    elif any(existing[key] != value for key, value in row.items()):
        raise CampaignServiceError(
            "mis_gate_mapping_conflict",
            "stable MIS Approval mapping conflicts with release gate facts",
        )
    repository.upsert_release_gate(gate)
    return gate


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
        "schema_version": 1,
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
    if not isinstance(summary, dict) or not isinstance(regressions, list):
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
