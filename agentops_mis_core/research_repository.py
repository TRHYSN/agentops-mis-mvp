"""Repository boundary and SQLite primitives for native research objects."""
from __future__ import annotations

from datetime import datetime, timezone
import sqlite3
from typing import Any, Mapping, Protocol, runtime_checkable

from agentops_mis_core.research_domain import (
    Checkpoint,
    InvalidTransition,
    JobAttempt,
    MetricSnapshot,
    ResearchClaim,
    ResearchContract,
    StaleStateVersion,
    Trial,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RepositoryConflict(RuntimeError):
    """A unique identity or immutable row already exists."""


@runtime_checkable
class ResearchRepository(Protocol):
    def add_contract(self, contract: ResearchContract) -> None: ...
    def get_contract(self, contract_id: str, version: int | None = None) -> ResearchContract | None: ...
    def transition_contract(self, contract_id: str, version: int, target: str, *, expected_state_version: int) -> ResearchContract: ...
    def revise_contract(self, contract_id: str, version: int, *, payload: Mapping[str, Any], expected_state_version: int) -> ResearchContract: ...
    def add_trial(self, trial: Trial) -> None: ...
    def get_trial(self, trial_id: str) -> Trial | None: ...
    def transition_trial(self, trial_id: str, target: str, *, expected_state_version: int) -> Trial: ...
    def add_job_attempt(self, attempt: JobAttempt) -> None: ...
    def get_job_attempt(self, attempt_id: str) -> JobAttempt | None: ...
    def transition_job_attempt(self, attempt_id: str, target: str, *, expected_state_version: int, reconcile_outcome: str | None = None) -> JobAttempt: ...


class SQLiteResearchRepository:
    """Small transactional adapter; lifecycle policy stays in domain objects."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")

    @staticmethod
    def _contract(row: sqlite3.Row) -> ResearchContract:
        return ResearchContract(
            contract_id=row["contract_id"], version=row["version"],
            workspace_id=row["workspace_id"], project_ref=row["project_ref"],
            goal_ref=row["goal_ref"], requirement_ref=row["requirement_ref"],
            agent_plan_id=row["agent_plan_id"], contract_artifact_id=row["contract_artifact_id"],
            payload_json=row["payload_json"], content_hash=row["content_hash"],
            protocol_hash=row["protocol_hash"], code_hash=row["code_hash"],
            data_hash=row["data_hash"], evidence_hash=row["evidence_hash"],
            status=row["status"], state_version=row["state_version"],
            supersedes_version=row["supersedes_version"],
        )

    @staticmethod
    def _trial(row: sqlite3.Row) -> Trial:
        return Trial(
            trial_id=row["trial_id"], contract_id=row["contract_id"],
            contract_version=row["contract_version"], task_id=row["task_id"],
            status=row["status"], state_version=row["state_version"],
            legacy_trial_id=row["legacy_trial_id"],
        )

    @staticmethod
    def _attempt(row: sqlite3.Row) -> JobAttempt:
        return JobAttempt(
            attempt_id=row["attempt_id"], trial_id=row["trial_id"], run_id=row["run_id"],
            attempt_number=row["attempt_number"], status=row["status"],
            state_version=row["state_version"], reconcile_outcome=row["reconcile_outcome"],
            legacy_attempt_id=row["legacy_attempt_id"],
        )

    def add_contract(self, contract: ResearchContract) -> None:
        now = _now()
        try:
            with self.conn:
                self.conn.execute(
                    """INSERT INTO research_contract_versions(
                        contract_id,version,workspace_id,project_ref,goal_ref,requirement_ref,
                        agent_plan_id,contract_artifact_id,payload_json,content_hash,
                        protocol_hash,code_hash,data_hash,evidence_hash,status,
                        state_version,supersedes_version,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        contract.contract_id, contract.version, contract.workspace_id,
                        contract.project_ref, contract.goal_ref, contract.requirement_ref,
                        contract.agent_plan_id, contract.contract_artifact_id,
                        contract.payload_json, contract.content_hash, contract.protocol_hash,
                        contract.code_hash, contract.data_hash, contract.evidence_hash, contract.status,
                        contract.state_version, contract.supersedes_version, now, now,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise RepositoryConflict("contract version or current-version identity already exists") from exc

    def get_contract(self, contract_id: str, version: int | None = None) -> ResearchContract | None:
        if version is None:
            row = self.conn.execute(
                "SELECT * FROM research_contract_versions WHERE contract_id=? ORDER BY version DESC LIMIT 1",
                (contract_id,),
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT * FROM research_contract_versions WHERE contract_id=? AND version=?",
                (contract_id, version),
            ).fetchone()
        return self._contract(row) if row else None

    def _require_contract(self, contract_id: str, version: int) -> ResearchContract:
        contract = self.get_contract(contract_id, version)
        if contract is None:
            raise KeyError(f"unknown contract {contract_id} v{version}")
        return contract

    def transition_contract(
        self, contract_id: str, version: int, target: str, *, expected_state_version: int
    ) -> ResearchContract:
        current = self._require_contract(contract_id, version)
        updated = current.transition(target, expected_state_version=expected_state_version)
        with self.conn:
            cursor = self.conn.execute(
                """UPDATE research_contract_versions SET status=?,state_version=?,updated_at=?
                   WHERE contract_id=? AND version=? AND state_version=? AND status=?""",
                (updated.status, updated.state_version, _now(), contract_id, version, expected_state_version, current.status),
            )
            if cursor.rowcount != 1:
                raise StaleStateVersion("contract state changed concurrently")
        return updated

    def revise_contract(
        self,
        contract_id: str,
        version: int,
        *,
        payload: Mapping[str, Any],
        expected_state_version: int,
    ) -> ResearchContract:
        current = self._require_contract(contract_id, version)
        if current.state_version != expected_state_version:
            raise StaleStateVersion("contract state changed concurrently")
        revised = current.revision(payload=payload)
        now = _now()
        try:
            with self.conn:
                if current.status in {"approved", "active"}:
                    superseded = current.transition("superseded", expected_state_version=expected_state_version)
                    cursor = self.conn.execute(
                        """UPDATE research_contract_versions SET status=?,state_version=?,updated_at=?
                           WHERE contract_id=? AND version=? AND state_version=? AND status=?""",
                        (superseded.status, superseded.state_version, now, contract_id, version, expected_state_version, current.status),
                    )
                    if cursor.rowcount != 1:
                        raise StaleStateVersion("contract state changed concurrently")
                elif current.status not in {"rejected", "superseded", "cancelled"}:
                    raise InvalidTransition(f"contract in {current.status} cannot be revised")
                self.conn.execute(
                    """INSERT INTO research_contract_versions(
                        contract_id,version,workspace_id,project_ref,goal_ref,requirement_ref,
                        agent_plan_id,contract_artifact_id,payload_json,content_hash,
                        protocol_hash,code_hash,data_hash,evidence_hash,status,
                        state_version,supersedes_version,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        revised.contract_id, revised.version, revised.workspace_id,
                        revised.project_ref, revised.goal_ref, revised.requirement_ref,
                        revised.agent_plan_id, revised.contract_artifact_id,
                        revised.payload_json, revised.content_hash, revised.protocol_hash,
                        revised.code_hash, revised.data_hash, revised.evidence_hash, revised.status,
                        revised.state_version, revised.supersedes_version, now, now,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise RepositoryConflict("contract revision identity already exists") from exc
        return revised

    def add_trial(self, trial: Trial) -> None:
        now = _now()
        try:
            with self.conn:
                self.conn.execute(
                    """INSERT INTO research_trial_records(
                        trial_id,contract_id,contract_version,task_id,status,state_version,
                        legacy_trial_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)""",
                    (trial.trial_id, trial.contract_id, trial.contract_version, trial.task_id,
                     trial.status, trial.state_version, trial.legacy_trial_id, now, now),
                )
        except sqlite3.IntegrityError as exc:
            raise RepositoryConflict("trial identity or authority reference conflicts") from exc

    def get_trial(self, trial_id: str) -> Trial | None:
        row = self.conn.execute("SELECT * FROM research_trial_records WHERE trial_id=?", (trial_id,)).fetchone()
        return self._trial(row) if row else None

    def transition_trial(self, trial_id: str, target: str, *, expected_state_version: int) -> Trial:
        current = self.get_trial(trial_id)
        if current is None:
            raise KeyError(f"unknown trial {trial_id}")
        updated = current.transition(target, expected_state_version=expected_state_version)
        with self.conn:
            cursor = self.conn.execute(
                """UPDATE research_trial_records SET status=?,state_version=?,updated_at=?
                   WHERE trial_id=? AND status=? AND state_version=?""",
                (updated.status, updated.state_version, _now(), trial_id, current.status, expected_state_version),
            )
            if cursor.rowcount != 1:
                raise StaleStateVersion("trial state changed concurrently")
        return updated

    def add_job_attempt(self, attempt: JobAttempt) -> None:
        now = _now()
        try:
            with self.conn:
                self.conn.execute(
                    """INSERT INTO research_job_attempts(
                        attempt_id,trial_id,run_id,attempt_number,status,state_version,
                        reconcile_outcome,legacy_attempt_id,created_at,updated_at)
                        VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (attempt.attempt_id, attempt.trial_id, attempt.run_id, attempt.attempt_number,
                     attempt.status, attempt.state_version, attempt.reconcile_outcome,
                     attempt.legacy_attempt_id, now, now),
                )
        except sqlite3.IntegrityError as exc:
            raise RepositoryConflict("attempt, run, or trial attempt-number identity conflicts") from exc

    def get_job_attempt(self, attempt_id: str) -> JobAttempt | None:
        row = self.conn.execute("SELECT * FROM research_job_attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
        return self._attempt(row) if row else None

    def transition_job_attempt(
        self,
        attempt_id: str,
        target: str,
        *,
        expected_state_version: int,
        reconcile_outcome: str | None = None,
    ) -> JobAttempt:
        current = self.get_job_attempt(attempt_id)
        if current is None:
            raise KeyError(f"unknown attempt {attempt_id}")
        updated = current.transition(
            target, expected_state_version=expected_state_version,
            reconcile_outcome=reconcile_outcome,
        )
        with self.conn:
            cursor = self.conn.execute(
                """UPDATE research_job_attempts
                   SET status=?,state_version=?,reconcile_outcome=?,updated_at=?
                   WHERE attempt_id=? AND status=? AND state_version=?""",
                (updated.status, updated.state_version, updated.reconcile_outcome, _now(),
                 attempt_id, current.status, expected_state_version),
            )
            if cursor.rowcount != 1:
                raise StaleStateVersion("attempt state changed concurrently")
        return updated

    def add_checkpoint(self, checkpoint: Checkpoint) -> None:
        try:
            with self.conn:
                self.conn.execute(
                    """INSERT INTO research_checkpoints(
                        checkpoint_id,attempt_id,artifact_id,source_run_id,content_hash,
                        protocol_hash,validity,created_at) VALUES(?,?,?,?,?,?,?,?)""",
                    (checkpoint.checkpoint_id, checkpoint.attempt_id, checkpoint.artifact_id,
                     checkpoint.source_run_id, checkpoint.content_hash, checkpoint.protocol_hash,
                     checkpoint.validity, _now()),
                )
        except sqlite3.IntegrityError as exc:
            raise RepositoryConflict("checkpoint or artifact identity conflicts") from exc

    def add_metric_snapshot(self, metric: MetricSnapshot) -> None:
        try:
            with self.conn:
                self.conn.execute(
                    """INSERT INTO research_metric_snapshots(
                        metric_id,attempt_id,artifact_id,evaluation_id,name,value,step,validity,created_at)
                        VALUES(?,?,?,?,?,?,?,?,?)""",
                    (metric.metric_id, metric.attempt_id, metric.artifact_id, metric.evaluation_id,
                     metric.name, float(metric.value), metric.step, metric.validity, _now()),
                )
        except sqlite3.IntegrityError as exc:
            raise RepositoryConflict("metric identity or immutable parent conflicts") from exc

    def add_claim(self, claim: ResearchClaim) -> None:
        now = _now()
        try:
            with self.conn:
                self.conn.execute(
                    """INSERT INTO research_claims(
                        claim_id,contract_id,contract_version,evaluation_id,statement,status,
                        state_version,machine_gate_passed,independent_reviewer_id,created_at,updated_at)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (claim.claim_id, claim.contract_id, claim.contract_version,
                     claim.evaluation_id, claim.statement, claim.status, claim.state_version,
                     int(claim.machine_gate_passed), claim.independent_reviewer_id, now, now),
                )
        except sqlite3.IntegrityError as exc:
            raise RepositoryConflict("claim identity or authority reference conflicts") from exc

    def get_claim(self, claim_id: str) -> ResearchClaim | None:
        row = self.conn.execute("SELECT * FROM research_claims WHERE claim_id=?", (claim_id,)).fetchone()
        if row is None:
            return None
        return ResearchClaim(
            claim_id=row["claim_id"], contract_id=row["contract_id"],
            contract_version=row["contract_version"], evaluation_id=row["evaluation_id"],
            statement=row["statement"], status=row["status"], state_version=row["state_version"],
            machine_gate_passed=bool(row["machine_gate_passed"]),
            independent_reviewer_id=row["independent_reviewer_id"],
        )

    def transition_claim(
        self,
        claim_id: str,
        target: str,
        *,
        expected_state_version: int,
        machine_gate_passed: bool | None = None,
        independent_reviewer_id: str | None = None,
    ) -> ResearchClaim:
        current = self.get_claim(claim_id)
        if current is None:
            raise KeyError(f"unknown claim {claim_id}")
        updated = current.transition(
            target, expected_state_version=expected_state_version,
            machine_gate_passed=machine_gate_passed,
            independent_reviewer_id=independent_reviewer_id,
        )
        with self.conn:
            cursor = self.conn.execute(
                """UPDATE research_claims SET status=?,state_version=?,machine_gate_passed=?,
                   independent_reviewer_id=?,updated_at=?
                   WHERE claim_id=? AND status=? AND state_version=?""",
                (updated.status, updated.state_version, int(updated.machine_gate_passed),
                 updated.independent_reviewer_id, _now(), claim_id, current.status,
                 expected_state_version),
            )
            if cursor.rowcount != 1:
                raise StaleStateVersion("claim state changed concurrently")
        return updated

    def add_evidence_edge(
        self, *, edge_id: str, claim_id: str, source_type: str,
        source_ref: str, evidence_hash: str, validity: str = "valid"
    ) -> None:
        if source_type not in {"run", "metric", "artifact", "protocol", "commit"}:
            raise ValueError("unsupported evidence source_type")
        if validity not in {"valid", "invalid", "legacy_unverified"}:
            raise ValueError("unsupported evidence validity")
        try:
            with self.conn:
                self.conn.execute(
                    """INSERT INTO research_evidence_edges(
                        edge_id,claim_id,source_type,source_ref,evidence_hash,validity,created_at)
                        VALUES(?,?,?,?,?,?,?)""",
                    (edge_id, claim_id, source_type, source_ref, evidence_hash, validity, _now()),
                )
        except sqlite3.IntegrityError as exc:
            raise RepositoryConflict("evidence edge identity conflicts") from exc
