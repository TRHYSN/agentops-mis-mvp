"""Additive, checksummed SQLite migration for native research-domain state.

This module never opens the product database itself. Callers must provide an
explicit connection; therefore importing it cannot mutate any user ledger.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import sqlite3
from typing import Iterable


MIGRATION_ID = "2026-08-09-research-domain-v0.5"
MIGRATION_DESCRIPTION = "Add immutable native research contracts, trial/job state, checkpoints, metrics, claims, and evidence edges."


class ResearchMigrationError(RuntimeError):
    """Base migration failure."""


class MigrationChecksumMismatch(ResearchMigrationError):
    """An existing receipt does not describe this exact migration."""


class LegacyPreflightError(ResearchMigrationError):
    """Legacy rows are ambiguous; no schema changes were applied."""

    def __init__(self, anomalies: Iterable["LegacyAnomaly"]):
        self.anomalies = tuple(anomalies)
        super().__init__("legacy research data failed closed preflight: " + ", ".join(a.code for a in self.anomalies))


@dataclass(frozen=True, slots=True)
class LegacyAnomaly:
    code: str
    table: str
    identity: str
    detail: str


@dataclass(frozen=True, slots=True)
class MigrationReceipt:
    migration_id: str
    checksum: str
    applied: bool
    idempotent_replay: bool
    legacy_read_compatible: bool = True


MIGRATION_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS research_contract_versions (
        contract_id TEXT NOT NULL,
        version INTEGER NOT NULL CHECK(version >= 1),
        workspace_id TEXT NOT NULL,
        project_ref TEXT NOT NULL,
        goal_ref TEXT NOT NULL,
        requirement_ref TEXT NOT NULL,
        agent_plan_id TEXT NOT NULL,
        contract_artifact_id TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        protocol_hash TEXT NOT NULL,
        code_hash TEXT NOT NULL,
        data_hash TEXT NOT NULL,
        evidence_hash TEXT NOT NULL,
        status TEXT NOT NULL,
        state_version INTEGER NOT NULL CHECK(state_version >= 1),
        supersedes_version INTEGER,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY(contract_id, version),
        FOREIGN KEY(agent_plan_id) REFERENCES agent_plans(plan_id),
        FOREIGN KEY(contract_artifact_id) REFERENCES artifacts(artifact_id),
        FOREIGN KEY(contract_id, supersedes_version)
          REFERENCES research_contract_versions(contract_id, version)
    )""",
    """CREATE UNIQUE INDEX IF NOT EXISTS uq_research_contract_single_current
       ON research_contract_versions(contract_id)
       WHERE status NOT IN ('superseded','rejected','cancelled')""",
    """CREATE TABLE IF NOT EXISTS research_trial_records (
        trial_id TEXT PRIMARY KEY,
        contract_id TEXT NOT NULL,
        contract_version INTEGER NOT NULL,
        task_id TEXT NOT NULL,
        status TEXT NOT NULL,
        state_version INTEGER NOT NULL CHECK(state_version >= 1),
        legacy_trial_id TEXT UNIQUE,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY(contract_id, contract_version)
          REFERENCES research_contract_versions(contract_id, version),
        FOREIGN KEY(task_id) REFERENCES tasks(task_id)
    )""",
    """CREATE TABLE IF NOT EXISTS research_job_attempts (
        attempt_id TEXT PRIMARY KEY,
        trial_id TEXT NOT NULL,
        run_id TEXT NOT NULL UNIQUE,
        attempt_number INTEGER NOT NULL CHECK(attempt_number >= 1),
        status TEXT NOT NULL,
        state_version INTEGER NOT NULL CHECK(state_version >= 1),
        reconcile_outcome TEXT,
        legacy_attempt_id TEXT UNIQUE,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(trial_id, attempt_number),
        FOREIGN KEY(trial_id) REFERENCES research_trial_records(trial_id),
        FOREIGN KEY(run_id) REFERENCES runs(run_id)
    )""",
    """CREATE TABLE IF NOT EXISTS research_checkpoints (
        checkpoint_id TEXT PRIMARY KEY,
        attempt_id TEXT NOT NULL,
        artifact_id TEXT NOT NULL UNIQUE,
        source_run_id TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        protocol_hash TEXT NOT NULL,
        validity TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY(attempt_id) REFERENCES research_job_attempts(attempt_id),
        FOREIGN KEY(artifact_id) REFERENCES artifacts(artifact_id),
        FOREIGN KEY(source_run_id) REFERENCES runs(run_id)
    )""",
    """CREATE TABLE IF NOT EXISTS research_metric_snapshots (
        metric_id TEXT PRIMARY KEY,
        attempt_id TEXT NOT NULL,
        artifact_id TEXT NOT NULL,
        evaluation_id TEXT,
        name TEXT NOT NULL,
        value REAL NOT NULL,
        step INTEGER,
        validity TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(attempt_id, name, step, artifact_id),
        FOREIGN KEY(attempt_id) REFERENCES research_job_attempts(attempt_id),
        FOREIGN KEY(artifact_id) REFERENCES artifacts(artifact_id),
        FOREIGN KEY(evaluation_id) REFERENCES evaluations(evaluation_id)
    )""",
    """CREATE TABLE IF NOT EXISTS research_claims (
        claim_id TEXT PRIMARY KEY,
        contract_id TEXT NOT NULL,
        contract_version INTEGER NOT NULL,
        evaluation_id TEXT NOT NULL,
        statement TEXT NOT NULL,
        status TEXT NOT NULL,
        state_version INTEGER NOT NULL CHECK(state_version >= 1),
        machine_gate_passed INTEGER NOT NULL DEFAULT 0,
        independent_reviewer_id TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY(contract_id, contract_version)
          REFERENCES research_contract_versions(contract_id, version),
        FOREIGN KEY(evaluation_id) REFERENCES evaluations(evaluation_id)
    )""",
    """CREATE TABLE IF NOT EXISTS research_evidence_edges (
        edge_id TEXT PRIMARY KEY,
        claim_id TEXT NOT NULL,
        source_type TEXT NOT NULL,
        source_ref TEXT NOT NULL,
        evidence_hash TEXT NOT NULL,
        validity TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(claim_id, source_type, source_ref, evidence_hash),
        FOREIGN KEY(claim_id) REFERENCES research_claims(claim_id)
    )""",
    """CREATE INDEX IF NOT EXISTS idx_research_trial_contract
       ON research_trial_records(contract_id, contract_version, status)""",
    """CREATE INDEX IF NOT EXISTS idx_research_job_trial
       ON research_job_attempts(trial_id, attempt_number)""",
    """CREATE INDEX IF NOT EXISTS idx_research_claim_contract
       ON research_claims(contract_id, contract_version, status)""",
    # Existing v0.4 rows remain readable. These indexes only tighten identities
    # after inspect_legacy_anomalies proves they are unambiguous.
    """CREATE UNIQUE INDEX IF NOT EXISTS uq_research_attempts_trial_attempt_number
       ON research_attempts(trial_id, attempt_number)""",
    """CREATE UNIQUE INDEX IF NOT EXISTS uq_research_metrics_attempt_slot
       ON research_metrics(attempt_id, name, COALESCE(step, -1), COALESCE(recorded_at, ''))""",
)


def migration_checksum() -> str:
    payload = "\n-- statement --\n".join(statement.strip() for statement in MIGRATION_STATEMENTS)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def receipt_description() -> str:
    return f"{MIGRATION_DESCRIPTION} ddl_sha256={migration_checksum()}"


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")')}


def _query_if_columns(
    conn: sqlite3.Connection, table: str, required: set[str], sql: str
) -> list[sqlite3.Row | tuple]:
    if not _table_exists(conn, table) or not required.issubset(_columns(conn, table)):
        return []
    return list(conn.execute(sql))


_TARGET_REQUIRED_COLUMNS = {
    "research_contract_versions": {"contract_id", "version", "workspace_id", "agent_plan_id", "contract_artifact_id", "payload_json", "content_hash", "protocol_hash", "code_hash", "data_hash", "evidence_hash", "status", "state_version"},
    "research_trial_records": {"trial_id", "contract_id", "contract_version", "task_id", "status", "state_version"},
    "research_job_attempts": {"attempt_id", "trial_id", "run_id", "attempt_number", "status", "state_version"},
    "research_checkpoints": {"checkpoint_id", "attempt_id", "artifact_id", "source_run_id", "content_hash", "protocol_hash", "validity"},
    "research_metric_snapshots": {"metric_id", "attempt_id", "artifact_id", "name", "value", "validity"},
    "research_claims": {"claim_id", "contract_id", "contract_version", "evaluation_id", "status", "state_version"},
    "research_evidence_edges": {"edge_id", "claim_id", "source_type", "source_ref", "evidence_hash", "validity"},
}

_LEGACY_REQUIRED_COLUMNS = {
    "research_trials": {"trial_id", "experiment_id", "workspace_id", "status", "params_json", "params_hash"},
    "research_attempts": {"attempt_id", "experiment_id", "trial_id", "workspace_id", "attempt_number", "status", "run_id"},
    "research_metrics": {"metric_id", "experiment_id", "trial_id", "attempt_id", "workspace_id", "name", "value", "step", "recorded_at"},
}


def inspect_legacy_anomalies(conn: sqlite3.Connection) -> tuple[LegacyAnomaly, ...]:
    """Inspect v0.4 identities before adding any uniqueness constraint."""
    anomalies: list[LegacyAnomaly] = []
    for table, required in _TARGET_REQUIRED_COLUMNS.items():
        if _table_exists(conn, table) and not required.issubset(_columns(conn, table)):
            anomalies.append(LegacyAnomaly(
                "incompatible_target_table", table, table,
                "missing columns: " + ",".join(sorted(required - _columns(conn, table))),
            ))
    for table, required in _LEGACY_REQUIRED_COLUMNS.items():
        if _table_exists(conn, table) and not required.issubset(_columns(conn, table)):
            anomalies.append(LegacyAnomaly(
                "incompatible_legacy_table", table, table,
                "missing columns: " + ",".join(sorted(required - _columns(conn, table))),
            ))
    attempts = _query_if_columns(
        conn,
        "research_attempts",
        {"attempt_id", "trial_id", "attempt_number"},
        """SELECT trial_id,attempt_number,COUNT(*),GROUP_CONCAT(attempt_id)
           FROM research_attempts GROUP BY trial_id,attempt_number HAVING COUNT(*)>1""",
    )
    for trial_id, number, count, ids in attempts:
        anomalies.append(LegacyAnomaly(
            "duplicate_trial_attempt_number", "research_attempts",
            f"{trial_id}:{number}", f"{count} rows: {ids}",
        ))

    id_conflicts = _query_if_columns(
        conn,
        "research_attempts",
        {"attempt_id", "trial_id", "experiment_id"},
        """SELECT attempt_id,COUNT(DISTINCT trial_id),COUNT(DISTINCT experiment_id)
           FROM research_attempts GROUP BY attempt_id
           HAVING COUNT(DISTINCT trial_id)>1 OR COUNT(DISTINCT experiment_id)>1""",
    )
    for attempt_id, trial_count, experiment_count in id_conflicts:
        anomalies.append(LegacyAnomaly(
            "attempt_id_parent_conflict", "research_attempts", str(attempt_id),
            f"trial parents={trial_count}, experiment parents={experiment_count}",
        ))

    for attempt_id, run_id, count in _query_if_columns(
        conn, "research_attempts", {"attempt_id", "run_id"},
        """SELECT MIN(attempt_id),run_id,COUNT(*) FROM research_attempts
           GROUP BY run_id HAVING run_id IS NOT NULL AND run_id<>'' AND COUNT(*)>1""",
    ):
        anomalies.append(LegacyAnomaly(
            "ambiguous_run_binding", "research_attempts", str(run_id),
            f"{count} attempts share one MIS Run (including {attempt_id})",
        ))

    if (
        _table_exists(conn, "research_attempts")
        and _table_exists(conn, "research_trials")
        and {"trial_id", "experiment_id"}.issubset(_columns(conn, "research_attempts"))
        and {"trial_id", "experiment_id"}.issubset(_columns(conn, "research_trials"))
    ):
        for attempt_id, trial_id in conn.execute(
            """SELECT a.attempt_id,a.trial_id FROM research_attempts a
               JOIN research_trials t ON t.trial_id=a.trial_id
               WHERE a.experiment_id<>t.experiment_id"""
        ):
            anomalies.append(LegacyAnomaly(
                "attempt_parent_mismatch", "research_attempts", str(attempt_id),
                f"trial {trial_id} belongs to a different experiment",
            ))
        for attempt_id, trial_id in conn.execute(
            """SELECT a.attempt_id,a.trial_id FROM research_attempts a
               LEFT JOIN research_trials t ON t.trial_id=a.trial_id
               WHERE t.trial_id IS NULL"""
        ):
            anomalies.append(LegacyAnomaly(
                "orphan_attempt", "research_attempts", str(attempt_id), f"missing trial {trial_id}",
            ))

    metric_ids = _query_if_columns(
        conn,
        "research_metrics",
        {"metric_id", "attempt_id", "trial_id", "experiment_id"},
        """SELECT metric_id,COUNT(DISTINCT attempt_id),COUNT(DISTINCT trial_id),COUNT(DISTINCT experiment_id)
           FROM research_metrics GROUP BY metric_id
           HAVING COUNT(DISTINCT attempt_id)>1 OR COUNT(DISTINCT trial_id)>1 OR COUNT(DISTINCT experiment_id)>1""",
    )
    for metric_id, attempt_count, trial_count, experiment_count in metric_ids:
        anomalies.append(LegacyAnomaly(
            "mutable_metric_id_parent", "research_metrics", str(metric_id),
            f"attempts={attempt_count}, trials={trial_count}, experiments={experiment_count}",
        ))

    metric_slots = _query_if_columns(
        conn,
        "research_metrics",
        {"metric_id", "attempt_id", "name", "step", "recorded_at"},
        """SELECT attempt_id,name,COALESCE(step,-1),COALESCE(recorded_at,''),COUNT(*),GROUP_CONCAT(metric_id)
           FROM research_metrics
           GROUP BY attempt_id,name,COALESCE(step,-1),COALESCE(recorded_at,'')
           HAVING COUNT(*)>1""",
    )
    for attempt_id, name, step, recorded_at, count, ids in metric_slots:
        anomalies.append(LegacyAnomaly(
            "duplicate_metric_slot", "research_metrics",
            f"{attempt_id}:{name}:{step}:{recorded_at}", f"{count} rows: {ids}",
        ))

    if (_table_exists(conn, "research_metrics") and _table_exists(conn, "research_attempts")
            and _LEGACY_REQUIRED_COLUMNS["research_metrics"].issubset(_columns(conn, "research_metrics"))
            and _LEGACY_REQUIRED_COLUMNS["research_attempts"].issubset(_columns(conn, "research_attempts"))):
        for metric_id, attempt_id in conn.execute(
            """SELECT m.metric_id,m.attempt_id FROM research_metrics m
               LEFT JOIN research_attempts a ON a.attempt_id=m.attempt_id
               WHERE a.attempt_id IS NULL"""
        ):
            anomalies.append(LegacyAnomaly(
                "orphan_metric", "research_metrics", str(metric_id), f"missing attempt {attempt_id}",
            ))
        for (metric_id,) in conn.execute(
            """SELECT m.metric_id FROM research_metrics m JOIN research_attempts a ON a.attempt_id=m.attempt_id
               WHERE m.trial_id<>a.trial_id OR m.experiment_id<>a.experiment_id"""
        ):
            anomalies.append(LegacyAnomaly(
                "metric_parent_mismatch", "research_metrics", str(metric_id),
                "metric parent bindings disagree with its attempt",
            ))

    if _table_exists(conn, "research_trials") and _LEGACY_REQUIRED_COLUMNS["research_trials"].issubset(_columns(conn, "research_trials")):
        allowed = {"queued", "running", "completed", "completed_with_deviation", "failed", "blocked"}
        for trial_id, status, params_json, params_hash in conn.execute(
            "SELECT trial_id,status,params_json,params_hash FROM research_trials"
        ):
            if status not in allowed:
                anomalies.append(LegacyAnomaly("invalid_legacy_status", "research_trials", str(trial_id), str(status)))
            try:
                parsed = json.loads(params_json)
                encoded = json.dumps(parsed, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
                expected = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
                if params_hash != expected:
                    anomalies.append(LegacyAnomaly("legacy_hash_mismatch", "research_trials", str(trial_id), "params_hash mismatch"))
            except (TypeError, ValueError, json.JSONDecodeError):
                anomalies.append(LegacyAnomaly("malformed_legacy_json", "research_trials", str(trial_id), "params_json is not finite JSON"))

    if _table_exists(conn, "research_attempts") and _LEGACY_REQUIRED_COLUMNS["research_attempts"].issubset(_columns(conn, "research_attempts")):
        allowed = {"queued", "running", "completed", "completed_with_deviation", "failed", "blocked", "timed_out"}
        for attempt_id, status in conn.execute("SELECT attempt_id,status FROM research_attempts"):
            if status not in allowed:
                anomalies.append(LegacyAnomaly("invalid_legacy_status", "research_attempts", str(attempt_id), str(status)))

    if _table_exists(conn, "research_metrics") and _LEGACY_REQUIRED_COLUMNS["research_metrics"].issubset(_columns(conn, "research_metrics")):
        for metric_id, value in conn.execute("SELECT metric_id,value FROM research_metrics"):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                anomalies.append(LegacyAnomaly("invalid_legacy_metric", "research_metrics", str(metric_id), "metric value must be finite"))
    return tuple(anomalies)


def classify_legacy_row(table: str, row_id: str) -> dict[str, str | bool]:
    """Migration-facing fail-closed legacy classification."""
    result: dict[str, str | bool] = {
        "table": table,
        "row_id": row_id,
        "source": "legacy",
        "import_status": "imported_unverified",
        "eligible": True,
        "accepted": False,
    }
    if table == "research_metrics":
        result["validity"] = "legacy_unverified"
    return result


def apply_research_domain_migration(conn: sqlite3.Connection) -> MigrationReceipt:
    """Apply once using the core ``schema_migrations`` authority ledger."""
    if conn.in_transaction:
        raise ResearchMigrationError("migration requires a connection with no active transaction")
    conn.execute("PRAGMA foreign_keys=ON")
    if _table_exists(conn, "schema_migrations"):
        receipt_columns = {"migration_id", "description", "applied_at"}
        if not receipt_columns.issubset(_columns(conn, "schema_migrations")):
            raise LegacyPreflightError((LegacyAnomaly(
                "incompatible_migration_ledger", "schema_migrations", "schema_migrations",
                "missing columns: " + ",".join(sorted(receipt_columns - _columns(conn, "schema_migrations"))),
            ),))
        existing = conn.execute(
            "SELECT description FROM schema_migrations WHERE migration_id=?", (MIGRATION_ID,)
        ).fetchone()
    else:
        existing = None
    if existing is not None:
        if str(existing[0]) != receipt_description():
            raise MigrationChecksumMismatch(
                f"{MIGRATION_ID} receipt checksum does not match current DDL"
            )
        return MigrationReceipt(MIGRATION_ID, migration_checksum(), False, True)

    anomalies = inspect_legacy_anomalies(conn)
    if anomalies:
        raise LegacyPreflightError(anomalies)

    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS schema_migrations (
                migration_id TEXT PRIMARY KEY,
                description TEXT NOT NULL,
                applied_at TEXT NOT NULL
            )"""
        )
        for statement in MIGRATION_STATEMENTS:
            # The two compatibility indexes only apply when old v0.4 tables exist.
            if " ON research_attempts" in statement and not _table_exists(conn, "research_attempts"):
                continue
            if " ON research_metrics" in statement and not _table_exists(conn, "research_metrics"):
                continue
            conn.execute(statement)
        conn.execute(
            "INSERT INTO schema_migrations(migration_id,description,applied_at) VALUES(?,?,?)",
            (MIGRATION_ID, receipt_description(), datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return MigrationReceipt(MIGRATION_ID, migration_checksum(), True, False)


def legacy_read_compatibility_contract() -> dict[str, object]:
    """Document the rollback boundary: readers remain, destructive down does not."""
    return {
        "legacy_tables_renamed": False,
        "legacy_rows_rewritten": False,
        "new_tables_additive": True,
        "destructive_down_migration_available": False,
        "rollback": "stop v0.5 writers; legacy v0.4 readers continue unchanged",
    }
