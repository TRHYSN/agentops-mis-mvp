#!/usr/bin/env python3
"""Fresh/idempotent/tamper/legacy-fail-closed migration smoke."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agentops_mis_core.research_migrations import (  # noqa: E402
    MIGRATION_ID, LegacyPreflightError, MigrationChecksumMismatch,
    apply_research_domain_migration, classify_legacy_row,
    legacy_read_compatibility_contract, migration_checksum,
)


AUTHORITY_SQL = """
CREATE TABLE schema_migrations(migration_id TEXT PRIMARY KEY,description TEXT NOT NULL,applied_at TEXT NOT NULL);
CREATE TABLE agent_plans(plan_id TEXT PRIMARY KEY);
CREATE TABLE artifacts(artifact_id TEXT PRIMARY KEY);
CREATE TABLE tasks(task_id TEXT PRIMARY KEY);
CREATE TABLE runs(run_id TEXT PRIMARY KEY);
CREATE TABLE evaluations(evaluation_id TEXT PRIMARY KEY);
"""

LEGACY_SQL = """
CREATE TABLE research_trials(
 trial_id TEXT,experiment_id TEXT,workspace_id TEXT,status TEXT,
 params_json TEXT,params_hash TEXT
);
CREATE TABLE research_attempts(
 attempt_id TEXT,experiment_id TEXT,trial_id TEXT,workspace_id TEXT,
 attempt_number INTEGER,status TEXT,run_id TEXT
);
CREATE TABLE research_metrics(
 metric_id TEXT,experiment_id TEXT,trial_id TEXT,attempt_id TEXT,workspace_id TEXT,
 name TEXT,value REAL,step INTEGER,recorded_at TEXT
);
"""


def require(value: bool, message: str, failures: list[str]) -> None:
    if not value:
        failures.append(message)


def main() -> int:
    failures: list[str] = []
    with sqlite3.connect(":memory:") as fresh:
        fresh.executescript(AUTHORITY_SQL)
        first = apply_research_domain_migration(fresh)
        second = apply_research_domain_migration(fresh)
        table_count = fresh.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name LIKE 'research_%'"
        ).fetchone()[0]
        receipt_count = fresh.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE migration_id=?", (MIGRATION_ID,)
        ).fetchone()[0]
        require(first.applied and not first.idempotent_replay, "fresh migration was not applied", failures)
        require(second.idempotent_replay and not second.applied, "migration replay was not idempotent", failures)
        require(receipt_count == 1, "migration receipt was duplicated", failures)
        require(table_count >= 7, "native research tables are missing", failures)
        fresh.execute("UPDATE schema_migrations SET description='tampered' WHERE migration_id=?", (MIGRATION_ID,))
        fresh.commit()
        try:
            apply_research_domain_migration(fresh)
            failures.append("tampered migration receipt was accepted")
        except MigrationChecksumMismatch:
            pass

    with sqlite3.connect(":memory:") as drifted:
        drifted.executescript(AUTHORITY_SQL)
        apply_research_domain_migration(drifted)
        drifted.execute("DROP INDEX idx_research_claim_contract")
        drifted.commit()
        try:
            apply_research_domain_migration(drifted)
            failures.append("matching receipt accepted missing target index")
        except MigrationChecksumMismatch:
            pass

    anomaly_codes: set[str] = set()
    with sqlite3.connect(":memory:") as legacy_bad:
        legacy_bad.executescript(AUTHORITY_SQL + LEGACY_SQL)
        empty_hash = __import__("hashlib").sha256(b"{}").hexdigest()
        legacy_bad.execute(
            "INSERT INTO research_trials VALUES('trial_1','exp_1','ws_1','running','{}',?)",
            (empty_hash,),
        )
        legacy_bad.executemany(
            "INSERT INTO research_attempts VALUES(?,?,?,?,?,?,?)",
            [
                ("attempt_same", "exp_1", "trial_1", "ws_1", 1, "running", "run_1"),
                ("attempt_other", "exp_1", "trial_1", "ws_1", 1, "failed", "run_2"),
                ("attempt_same", "exp_2", "trial_2", "ws_1", 2, "failed", "run_3"),
            ],
        )
        legacy_bad.executemany(
            "INSERT INTO research_metrics VALUES(?,?,?,?,?,?,?,?,?)",
            [
                ("metric_same", "exp_1", "trial_1", "attempt_same", "ws_1", "loss", 1.0, 1, "t"),
                ("metric_same", "exp_2", "trial_2", "attempt_other", "ws_1", "loss", 2.0, 2, "u"),
                ("metric_other", "exp_1", "trial_1", "attempt_same", "ws_1", "loss", 3.0, 1, "t"),
            ],
        )
        legacy_bad.commit()
        try:
            apply_research_domain_migration(legacy_bad)
            failures.append("legacy anomalies did not fail closed")
        except LegacyPreflightError as exc:
            anomaly_codes = {item.code for item in exc.anomalies}
        require("duplicate_trial_attempt_number" in anomaly_codes, "duplicate attempts were not detected", failures)
        require("attempt_id_parent_conflict" in anomaly_codes, "attempt parent conflict was not detected", failures)
        require("mutable_metric_id_parent" in anomaly_codes, "mutable metric parent was not detected", failures)
        require("duplicate_metric_slot" in anomaly_codes, "duplicate metric slot was not detected", failures)
        new_table = legacy_bad.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='research_contract_versions'"
        ).fetchone()
        require(new_table is None, "failed preflight left partial native tables", failures)

    with sqlite3.connect(":memory:") as legacy_clean:
        legacy_clean.executescript(AUTHORITY_SQL + LEGACY_SQL)
        empty_hash = __import__("hashlib").sha256(b"{}").hexdigest()
        legacy_clean.execute(
            "INSERT INTO research_trials VALUES('trial_old','exp_old','ws_old','completed','{}',?)",
            (empty_hash,),
        )
        legacy_clean.execute(
            "INSERT INTO research_attempts VALUES('attempt_old','exp_old','trial_old','ws_old',1,'completed','run_old')"
        )
        legacy_clean.execute(
            "INSERT INTO research_metrics VALUES('metric_old','exp_old','trial_old','attempt_old','ws_old','loss',0.5,1,'t')"
        )
        legacy_clean.commit()
        apply_research_domain_migration(legacy_clean)
        legacy_readback = legacy_clean.execute(
            "SELECT status FROM research_attempts WHERE attempt_id='attempt_old'"
        ).fetchone()
        require(legacy_readback == ("completed",), "legacy readback changed after additive migration", failures)
        classified = classify_legacy_row("research_metrics", "metric_old")
        require(classified.get("validity") == "legacy_unverified", "legacy metric classification was promoted", failures)

    rollback = legacy_read_compatibility_contract()
    require(rollback["destructive_down_migration_available"] is False, "destructive down migration advertised", failures)
    print(json.dumps({
        "ok": not failures,
        "operation": "research_domain_migration_smoke",
        "ddl_sha256": migration_checksum(),
        "fresh_db": True,
        "idempotent_replay": True,
        "checksum_mismatch_rejected": True,
        "legacy_anomaly_codes": sorted(anomaly_codes),
        "legacy_read_compatible": True,
        "destructive_down_migration": False,
        "failures": failures,
        "credentials_omitted": True,
    }, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
