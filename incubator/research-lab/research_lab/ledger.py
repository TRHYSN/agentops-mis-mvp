from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator


SCHEMA_VERSION = 1


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def encode_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


class ResearchLedger:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS experiments (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    protocol_hash TEXT NOT NULL UNIQUE,
                    provenance_hash TEXT,
                    protocol_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    claim_eligible INTEGER,
                    claim_reasons_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS trials (
                    id TEXT PRIMARY KEY,
                    experiment_id TEXT NOT NULL REFERENCES experiments(id) ON DELETE CASCADE,
                    params_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(experiment_id, params_json)
                );
                CREATE TABLE IF NOT EXISTS attempts (
                    id TEXT PRIMARY KEY,
                    trial_id TEXT NOT NULL REFERENCES trials(id) ON DELETE CASCADE,
                    attempt_number INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    exit_code INTEGER,
                    error_summary TEXT,
                    run_dir TEXT NOT NULL,
                    stdout_sha256 TEXT,
                    stderr_sha256 TEXT,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    UNIQUE(trial_id, attempt_number)
                );
                CREATE TABLE IF NOT EXISTS metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trial_id TEXT NOT NULL REFERENCES trials(id) ON DELETE CASCADE,
                    attempt_id TEXT NOT NULL REFERENCES attempts(id) ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    value REAL NOT NULL,
                    step INTEGER,
                    recorded_at TEXT
                );
                CREATE TABLE IF NOT EXISTS artifacts (
                    id TEXT PRIMARY KEY,
                    trial_id TEXT NOT NULL REFERENCES trials(id) ON DELETE CASCADE,
                    attempt_id TEXT NOT NULL REFERENCES attempts(id) ON DELETE CASCADE,
                    relative_path TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    UNIQUE(attempt_id, relative_path)
                );
                CREATE TABLE IF NOT EXISTS deviations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trial_id TEXT NOT NULL REFERENCES trials(id) ON DELETE CASCADE,
                    attempt_id TEXT NOT NULL REFERENCES attempts(id) ON DELETE CASCADE,
                    field_path TEXT NOT NULL,
                    expected_json TEXT NOT NULL,
                    actual_json TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    message TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'open'
                );
                CREATE INDEX IF NOT EXISTS idx_trials_experiment ON trials(experiment_id);
                CREATE INDEX IF NOT EXISTS idx_attempts_trial ON attempts(trial_id);
                CREATE INDEX IF NOT EXISTS idx_metrics_trial ON metrics(trial_id);
                """
            )
            row = connection.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()
            if row is not None and int(row["value"]) != SCHEMA_VERSION:
                raise RuntimeError(f"unsupported Research Lab schema version: {row['value']}")
            connection.execute(
                "INSERT OR IGNORE INTO schema_meta(key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )

    def upsert_experiment(
        self,
        *,
        experiment_id: str,
        name: str,
        stage: str,
        protocol_hash: str,
        provenance_hash: str | None,
        protocol_document: dict[str, Any],
    ) -> None:
        now = utc_now()
        protocol_json = encode_json(protocol_document)
        with self.connect() as connection:
            existing = connection.execute(
                "SELECT protocol_json FROM experiments WHERE id = ?",
                (experiment_id,),
            ).fetchone()
            if existing is not None and existing["protocol_json"] != protocol_json:
                raise RuntimeError("experiment identity collision with different protocol")
            connection.execute(
                """
                INSERT INTO experiments(
                    id, name, stage, protocol_hash, provenance_hash, protocol_json,
                    status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?)
                ON CONFLICT(id) DO UPDATE SET updated_at = excluded.updated_at
                """,
                (experiment_id, name, stage, protocol_hash, provenance_hash, protocol_json, now, now),
            )

    def ensure_trial(self, *, trial_id: str, experiment_id: str, params: dict[str, Any]) -> None:
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO trials(id, experiment_id, params_json, status, created_at, updated_at)
                VALUES (?, ?, ?, 'queued', ?, ?)
                ON CONFLICT(id) DO NOTHING
                """,
                (trial_id, experiment_id, encode_json(params), now, now),
            )

    def set_experiment_status(self, experiment_id: str, status: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE experiments SET status = ?, updated_at = ? WHERE id = ?",
                (status, utc_now(), experiment_id),
            )

    def set_trial_status(self, trial_id: str, status: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE trials SET status = ?, updated_at = ? WHERE id = ?",
                (status, utc_now(), trial_id),
            )

    def trial_status(self, trial_id: str) -> str | None:
        with self.connect() as connection:
            row = connection.execute("SELECT status FROM trials WHERE id = ?", (trial_id,)).fetchone()
            return None if row is None else str(row["status"])

    def next_attempt_number(self, trial_id: str) -> int:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(attempt_number), 0) AS maximum FROM attempts WHERE trial_id = ?",
                (trial_id,),
            ).fetchone()
            return int(row["maximum"]) + 1

    def start_attempt(self, *, attempt_id: str, trial_id: str, attempt_number: int, run_dir: Path) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO attempts(id, trial_id, attempt_number, status, run_dir, started_at)
                VALUES (?, ?, ?, 'running', ?, ?)
                """,
                (attempt_id, trial_id, attempt_number, str(run_dir), utc_now()),
            )

    def finish_attempt(
        self,
        *,
        attempt_id: str,
        status: str,
        exit_code: int | None,
        error_summary: str | None,
        stdout_sha256: str | None,
        stderr_sha256: str | None,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE attempts
                SET status = ?, exit_code = ?, error_summary = ?,
                    stdout_sha256 = ?, stderr_sha256 = ?, finished_at = ?
                WHERE id = ?
                """,
                (status, exit_code, error_summary, stdout_sha256, stderr_sha256, utc_now(), attempt_id),
            )

    def supersede_prior_attempt_deviations(self, *, trial_id: str, current_attempt_id: str) -> None:
        """Retain failed-attempt evidence without letting it override a later exact rerun."""
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE deviations
                SET status = 'superseded'
                WHERE trial_id = ? AND attempt_id != ? AND status = 'open'
                """,
                (trial_id, current_attempt_id),
            )

    def replace_attempt_evidence(
        self,
        *,
        trial_id: str,
        attempt_id: str,
        metrics: list[dict[str, Any]],
        artifacts: list[dict[str, Any]],
        deviations: list[dict[str, Any]],
    ) -> None:
        with self.connect() as connection:
            connection.execute("DELETE FROM metrics WHERE attempt_id = ?", (attempt_id,))
            connection.execute("DELETE FROM artifacts WHERE attempt_id = ?", (attempt_id,))
            connection.execute("DELETE FROM deviations WHERE attempt_id = ?", (attempt_id,))
            connection.executemany(
                """
                INSERT INTO metrics(trial_id, attempt_id, name, value, step, recorded_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (trial_id, attempt_id, item["name"], item["value"], item.get("step"), item.get("recorded_at"))
                    for item in metrics
                ],
            )
            connection.executemany(
                """
                INSERT INTO artifacts(id, trial_id, attempt_id, relative_path, sha256, size_bytes)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        item["id"],
                        trial_id,
                        attempt_id,
                        item["relative_path"],
                        item["sha256"],
                        item["size_bytes"],
                    )
                    for item in artifacts
                ],
            )
            connection.executemany(
                """
                INSERT INTO deviations(
                    trial_id, attempt_id, field_path, expected_json, actual_json,
                    severity, message, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'open')
                """,
                [
                    (
                        trial_id,
                        attempt_id,
                        item["field_path"],
                        encode_json(item.get("expected")),
                        encode_json(item.get("actual")),
                        item["severity"],
                        item["message"],
                    )
                    for item in deviations
                ],
            )

    def finalize_claim(self, experiment_id: str, *, status: str, eligible: bool, reasons: list[str]) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE experiments
                SET status = ?, claim_eligible = ?, claim_reasons_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, int(eligible), encode_json(reasons), utc_now(), experiment_id),
            )

    def experiment_evidence(self, experiment_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            experiment = connection.execute("SELECT * FROM experiments WHERE id = ?", (experiment_id,)).fetchone()
            if experiment is None:
                raise KeyError(experiment_id)
            trials = connection.execute(
                "SELECT * FROM trials WHERE experiment_id = ? ORDER BY id",
                (experiment_id,),
            ).fetchall()
            attempts = connection.execute(
                """
                SELECT a.* FROM attempts a JOIN trials t ON t.id = a.trial_id
                WHERE t.experiment_id = ? ORDER BY a.trial_id, a.attempt_number
                """,
                (experiment_id,),
            ).fetchall()
            metrics = connection.execute(
                """
                SELECT m.* FROM metrics m JOIN trials t ON t.id = m.trial_id
                WHERE t.experiment_id = ? ORDER BY m.trial_id, m.id
                """,
                (experiment_id,),
            ).fetchall()
            artifacts = connection.execute(
                """
                SELECT a.* FROM artifacts a JOIN trials t ON t.id = a.trial_id
                WHERE t.experiment_id = ? ORDER BY a.trial_id, a.relative_path
                """,
                (experiment_id,),
            ).fetchall()
            deviations = connection.execute(
                """
                SELECT d.* FROM deviations d JOIN trials t ON t.id = d.trial_id
                WHERE t.experiment_id = ? ORDER BY d.trial_id, d.id
                """,
                (experiment_id,),
            ).fetchall()
        return {
            "experiment": dict(experiment),
            "trials": [dict(row) for row in trials],
            "attempts": [dict(row) for row in attempts],
            "metrics": [dict(row) for row in metrics],
            "artifacts": [dict(row) for row in artifacts],
            "deviations": [dict(row) for row in deviations],
        }
