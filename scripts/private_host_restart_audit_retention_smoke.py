#!/usr/bin/env python3
"""Verify terminal Host restart outcomes enter the audit ledger exactly once."""
from __future__ import annotations

import json
import os
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import server
from agentops_mis_cli import relay_restart


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def enqueue(outbox: Path, sequence: int, state: str = "healthy") -> dict:
    return relay_restart.write_restart_audit_event(
        outbox_dir=outbox,
        action="enable",
        state=state,
        transaction_sequence=sequence,
        revision=4,
        transition_ref=f"transition_audit_{sequence}",
    )


def main() -> int:
    failures: list[str] = []
    original_db_path = server.DB_PATH
    original_host_home = os.environ.get("AGENTOPS_HOST_HOME")
    try:
        with tempfile.TemporaryDirectory(prefix="agentops-restart-audit-") as temporary:
            root = Path(temporary)
            host_home = root / "host"
            host_home.mkdir(mode=0o700)
            (host_home / "data").mkdir(mode=0o700)
            (host_home / "relay").mkdir(mode=0o700)
            os.environ["AGENTOPS_HOST_HOME"] = str(host_home)
            server.DB_PATH = host_home / "data" / "agentops_mis.db"
            server.init_schema()
            outbox = server.private_host_cli.paths()["relay_restart_audit_outbox"]

            first = enqueue(outbox, 101)
            duplicate_enqueue = enqueue(outbox, 101)
            require(first == duplicate_enqueue, "exact enqueue was not idempotent", failures)
            event_path = outbox / "restart-101.json"
            require(event_path.is_file(), "restart audit event was not durable", failures)
            require(
                stat.S_IMODE(outbox.stat().st_mode) == 0o700,
                "restart audit outbox was not owner-only",
                failures,
            )
            require(
                stat.S_IMODE(event_path.stat().st_mode) == 0o600,
                "restart audit event was not owner-only",
                failures,
            )
            stored = event_path.read_text(encoding="ascii")
            for forbidden in ("config", "path", "certificate", "credential", "secret", "token"):
                require(forbidden not in stored.lower(), f"event exposed forbidden field: {forbidden}", failures)

            consumed = server.consume_private_host_restart_audit_events()
            require(consumed.get("ok") is True, f"first ingest failed: {consumed}", failures)
            require(consumed.get("ingested") == 1, f"first ingest count invalid: {consumed}", failures)
            require(consumed.get("acknowledged") == 1, f"first acknowledge failed: {consumed}", failures)
            require(not event_path.exists(), "committed event remained in outbox", failures)
            require(
                not any(path.name.startswith(".restart-") for path in outbox.iterdir()),
                "per-event lock files accumulated in outbox",
                failures,
            )

            with server.db() as conn:
                rows = conn.execute(
                    """SELECT actor_type,actor_id,action,entity_type,entity_id,metadata_json
                       FROM audit_logs WHERE action='host.relay.restart.healthy'"""
                ).fetchall()
            require(len(rows) == 1, f"expected one restart outcome audit, got {len(rows)}", failures)
            if rows:
                row = rows[0]
                metadata = json.loads(row["metadata_json"])
                require(row["actor_type"] == "system", "restart audit actor was not system", failures)
                require(row["actor_id"] == "private-host-supervisor", "restart audit actor id invalid", failures)
                require(row["entity_type"] == "private_host_relay_transition", "restart audit entity type invalid", failures)
                require(row["entity_id"] == "transition_audit_101", "restart audit entity id invalid", failures)
                require(metadata.get("transaction_sequence") == 101, "transaction sequence missing", failures)
                require(metadata.get("state") == "healthy", "terminal state missing", failures)
                require(metadata.get("credentials_omitted") is True, "credential omission flag missing", failures)
                require(metadata.get("paths_omitted") is True, "path omission flag missing", failures)

            enqueue(outbox, 101)
            replay = server.consume_private_host_restart_audit_events()
            require(replay.get("ingested") == 0, f"replay duplicated audit: {replay}", failures)
            require(replay.get("acknowledged") == 1, f"replay was not acknowledged: {replay}", failures)
            with server.db() as conn:
                count = conn.execute(
                    "SELECT COUNT(*) FROM audit_logs WHERE action='host.relay.restart.healthy'"
                ).fetchone()[0]
            require(count == 1, f"restart audit was not exactly once: {count}", failures)

            second = enqueue(outbox, 102, "rolled_back")
            bound_db = server.DB_PATH
            server.DB_PATH = root / "unbound.sqlite"
            skipped = server.consume_private_host_restart_audit_events()
            require(
                skipped.get("skipped") == "database_not_private_host",
                f"unbound database consumed Host event: {skipped}",
                failures,
            )
            require((outbox / "restart-102.json").exists(), "unbound ingest removed event", failures)
            server.DB_PATH = bound_db
            resumed = server.consume_private_host_restart_audit_events()
            require(resumed.get("ingested") == 1, f"bound ingest did not resume: {resumed}", failures)
            require(second.get("state") == "rolled_back", "rollback fixture invalid", failures)

            enqueue(outbox, 103, "rollback_failed")
            failed_outcome = server.consume_private_host_restart_audit_events()
            require(
                failed_outcome.get("ingested") == 1,
                f"failed rollback outcome was not retained: {failed_outcome}",
                failures,
            )
            with server.db() as conn:
                failed_count = conn.execute(
                    "SELECT COUNT(*) FROM audit_logs WHERE action='host.relay.restart.rollback_failed'"
                ).fetchone()[0]
            require(failed_count == 1, "rollback_failed audit was not retained", failures)

            relay_dir = host_home / "relay"
            relay_dir.chmod(0o700)
            receipt_path = relay_dir / "restart-receipt.json"
            sequence_path = relay_dir / "restart-sequence.json"
            active_config = relay_dir / "config.json"
            host_config = host_home / "config.json"
            active_config.write_bytes(b'{"enabled":false}\n')
            host_config.write_bytes(b'{"relay_enabled":false}\n')
            active_config.chmod(0o600)
            host_config.chmod(0o600)
            receipt = relay_restart.create_restart_receipt(
                receipt_path=receipt_path,
                sequence_path=sequence_path,
                action="enable",
                transition_ref="transition_deferred_healthy",
                active_config_path=active_config,
                host_config_path=host_config,
                active_original_config=active_config.read_bytes(),
                active_target_config=b'{"enabled":true}\n',
                host_original_config=host_config.read_bytes(),
                host_target_config=b'{"relay_enabled":true}\n',
            )
            for state in ("response_flushed", "restart_requested", "validating_new_host", "healthy"):
                receipt = relay_restart.transition_restart_receipt(
                    receipt_path=receipt_path,
                    sequence_path=sequence_path,
                    action="enable",
                    transition_ref="transition_deferred_healthy",
                    transaction_sequence=receipt["transaction_sequence"],
                    expected_revision=receipt["revision"],
                    state=state,
                )
            relay_restart.write_restart_audit_event(
                outbox_dir=outbox,
                action="enable",
                state="healthy",
                transaction_sequence=receipt["transaction_sequence"],
                revision=receipt["revision"],
                transition_ref="transition_deferred_healthy",
            )
            deferred = server.consume_private_host_restart_audit_events()
            require(deferred.get("deferred") == 1, f"unfinalized healthy was not deferred: {deferred}", failures)
            with server.db() as conn:
                premature = conn.execute(
                    "SELECT COUNT(*) FROM audit_logs WHERE entity_id='transition_deferred_healthy'"
                ).fetchone()[0]
            require(premature == 0, "unfinalized healthy entered audit ledger", failures)
            relay_restart.finalize_restart_receipt(
                receipt_path=receipt_path,
                sequence_path=sequence_path,
                action="enable",
                transition_ref="transition_deferred_healthy",
                transaction_sequence=receipt["transaction_sequence"],
                expected_revision=receipt["revision"],
            )
            finalized = server.consume_private_host_restart_audit_events()
            require(finalized.get("ingested") == 1, f"finalized healthy was not retained: {finalized}", failures)

            relay_restart.write_restart_audit_event(
                outbox_dir=outbox,
                action="enable",
                state="healthy",
                transaction_sequence=105,
                revision=4,
                transition_ref="transition_replaced_healthy",
            )
            replacement = relay_restart.write_restart_audit_event(
                outbox_dir=outbox,
                action="enable",
                state="rolled_back",
                transaction_sequence=105,
                revision=6,
                transition_ref="transition_replaced_healthy",
            )
            require(replacement.get("state") == "rolled_back", "stale healthy was not replaced", failures)
            replacement_ingest = server.consume_private_host_restart_audit_events()
            require(replacement_ingest.get("ingested") == 1, "replacement outcome was not retained", failures)

            pending_receipt = relay_restart.create_restart_receipt(
                receipt_path=receipt_path,
                sequence_path=sequence_path,
                action="enable",
                transition_ref="transition_outbox_failure",
                active_config_path=active_config,
                host_config_path=host_config,
                active_original_config=active_config.read_bytes(),
                active_target_config=b'{"enabled":true}\n',
                host_original_config=host_config.read_bytes(),
                host_target_config=b'{"relay_enabled":true}\n',
            )
            for state in ("response_flushed", "restart_requested", "validating_new_host"):
                pending_receipt = relay_restart.transition_restart_receipt(
                    receipt_path=receipt_path,
                    sequence_path=sequence_path,
                    action="enable",
                    transition_ref="transition_outbox_failure",
                    transaction_sequence=pending_receipt["transaction_sequence"],
                    expected_revision=pending_receipt["revision"],
                    state=state,
                )
            context = {
                "action": "enable",
                "transition_ref": "transition_outbox_failure",
                "transaction_sequence": pending_receipt["transaction_sequence"],
                "expected_revision": pending_receipt["revision"],
            }
            real_writer = relay_restart.write_restart_audit_event

            def fail_writer(**_kwargs):
                raise relay_restart.RelayRestartError("write_failed")

            relay_restart.write_restart_audit_event = fail_writer
            try:
                pending_outcome = server.private_host_cli._advance_managed_restart_receipt(
                    server.private_host_cli.paths(),
                    context,
                    "healthy",
                )
            finally:
                relay_restart.write_restart_audit_event = real_writer
            require(
                pending_outcome.get("audit_event_pending") is True,
                "outbox failure was not retained as pending",
                failures,
            )
            require(
                relay_restart.restart_recovery_context(
                    receipt_path=receipt_path,
                    sequence_path=sequence_path,
                ).get("state") == "healthy",
                "outbox failure damaged the healthy receipt",
                failures,
            )
            retried_outcome = server.consume_private_host_restart_audit_events()
            require(
                retried_outcome.get("deferred") == 1,
                f"pending outbox was not recreated safely: {retried_outcome}",
                failures,
            )
            relay_restart.finalize_restart_receipt(
                receipt_path=receipt_path,
                sequence_path=sequence_path,
                action="enable",
                transition_ref="transition_outbox_failure",
                transaction_sequence=pending_outcome["transaction_sequence"],
                expected_revision=pending_outcome["expected_revision"],
            )
            completed_retry = server.consume_private_host_restart_audit_events()
            require(completed_retry.get("ingested") == 1, "retried outcome was not committed", failures)

            finalize_receipt = relay_restart.create_restart_receipt(
                receipt_path=receipt_path,
                sequence_path=sequence_path,
                action="enable",
                transition_ref="transition_finalize_retry",
                active_config_path=active_config,
                host_config_path=host_config,
                active_original_config=active_config.read_bytes(),
                active_target_config=b'{"enabled":true}\n',
                host_original_config=host_config.read_bytes(),
                host_target_config=b'{"relay_enabled":true}\n',
            )
            finalize_context = {
                "action": "enable",
                "transition_ref": "transition_finalize_retry",
                "transaction_sequence": finalize_receipt["transaction_sequence"],
                "expected_revision": finalize_receipt["revision"],
            }
            for state in ("response_flushed", "restart_requested", "validating_new_host", "healthy"):
                finalize_context = server.private_host_cli._advance_managed_restart_receipt(
                    server.private_host_cli.paths(),
                    finalize_context,
                    state,
                )
            finalize_process = SimpleNamespace(pid=5151)
            finalize_terminations: list[int] = []
            real_finalize = relay_restart.finalize_restart_receipt

            def fail_finalize_retryable(**_kwargs):
                raise relay_restart.RelayRestartError("write_failed")

            relay_restart.finalize_restart_receipt = fail_finalize_retryable
            try:
                finalize_healthy, finalize_terminated = (
                    server.private_host_cli._finish_restart_recovery_with_cleanup(
                        server.private_host_cli.paths(),
                        {
                            **finalize_context,
                            "state": "healthy",
                            "recovery_mode": "target",
                        },
                        finalize_process,
                        terminate=lambda process: finalize_terminations.append(process.pid) or True,
                        runtime_validator=lambda _action, _paths: True,
                    )
                )
            finally:
                relay_restart.finalize_restart_receipt = real_finalize
            require(
                finalize_healthy and not finalize_terminated and not finalize_terminations,
                "retryable finalize failure terminated a healthy Host",
                failures,
            )
            require(
                relay_restart.restart_recovery_context(
                    receipt_path=receipt_path,
                    sequence_path=sequence_path,
                ).get("state") == "healthy",
                "retryable finalize failure did not preserve terminal receipt",
                failures,
            )
            require(
                (outbox / f"restart-{finalize_context['transaction_sequence']}.json").is_file(),
                "retryable finalize failure did not preserve terminal outbox evidence",
                failures,
            )
            require(
                server.private_host_cli._finish_restart_receipt_recovery(
                    server.private_host_cli.paths(),
                    {
                        **finalize_context,
                        "state": "healthy",
                        "recovery_mode": "target",
                    },
                    runtime_validator=lambda _action, _paths: True,
                ),
                "retryable finalize receipt did not recover",
                failures,
            )
            require(
                server.consume_private_host_restart_audit_events().get("ingested") == 1,
                "recovered finalize outcome was not retained",
                failures,
            )

            invalid_receipt = relay_restart.create_restart_receipt(
                receipt_path=receipt_path,
                sequence_path=sequence_path,
                action="enable",
                transition_ref="transition_finalize_invalid",
                active_config_path=active_config,
                host_config_path=host_config,
                active_original_config=active_config.read_bytes(),
                active_target_config=b'{"enabled":true}\n',
                host_original_config=host_config.read_bytes(),
                host_target_config=b'{"relay_enabled":true}\n',
            )
            invalid_context = {
                "action": "enable",
                "transition_ref": "transition_finalize_invalid",
                "transaction_sequence": invalid_receipt["transaction_sequence"],
                "expected_revision": invalid_receipt["revision"],
            }
            for state in ("response_flushed", "restart_requested", "validating_new_host", "healthy"):
                invalid_context = server.private_host_cli._advance_managed_restart_receipt(
                    server.private_host_cli.paths(),
                    invalid_context,
                    state,
                )
            invalid_process = SimpleNamespace(pid=5152)
            invalid_terminations: list[int] = []
            server.private_host_cli.write_private_json(
                server.private_host_cli.paths()["pid"],
                {"pid": invalid_process.pid},
            )

            def fail_finalize_invalid(**_kwargs):
                raise relay_restart.RelayRestartError("receipt_invalid")

            relay_restart.finalize_restart_receipt = fail_finalize_invalid
            try:
                try:
                    server.private_host_cli._finish_restart_recovery_with_cleanup(
                        server.private_host_cli.paths(),
                        {
                            **invalid_context,
                            "state": "healthy",
                            "recovery_mode": "target",
                        },
                        invalid_process,
                        terminate=lambda process: invalid_terminations.append(process.pid) or True,
                        runtime_validator=lambda _action, _paths: True,
                    )
                except relay_restart.RelayRestartError as exc:
                    require(exc.code == "receipt_invalid", "identity failure changed error code", failures)
                else:
                    failures.append("identity failure did not fail closed")
            finally:
                relay_restart.finalize_restart_receipt = real_finalize
            require(
                invalid_terminations == [invalid_process.pid],
                "identity failure left the Host process running",
                failures,
            )
            real_finalize(
                receipt_path=receipt_path,
                sequence_path=sequence_path,
                action=invalid_context["action"],
                transition_ref=invalid_context["transition_ref"],
                transaction_sequence=invalid_context["transaction_sequence"],
                expected_revision=invalid_context["expected_revision"],
            )
            require(
                server.consume_private_host_restart_audit_events().get("ingested") == 1,
                "identity failure cleanup outcome was not retained",
                failures,
            )

            enqueue(outbox, 500)
            blocker = sqlite3.connect(server.DB_PATH, timeout=1, isolation_level=None)
            blocker.execute("PRAGMA journal_mode = WAL")
            blocker.execute("BEGIN IMMEDIATE")
            started = time.monotonic()
            busy_result = server.private_host_restart_audit_tick()
            elapsed = time.monotonic() - started
            blocker.rollback()
            blocker.close()
            require(
                busy_result.get("error") == "audit_database_busy" and busy_result.get("retryable") is True,
                f"SQLite contention was not bounded: {busy_result}",
                failures,
            )
            require(elapsed < 1.0, f"SQLite contention blocked API path for {elapsed:.3f}s", failures)
            require(
                server.consume_private_host_restart_audit_events().get("ingested") == 1,
                "busy audit event did not retry",
                failures,
            )

            enqueue(outbox, 550)
            lock_descriptor = relay_restart._acquire_lock(outbox / "events")
            started = time.monotonic()
            try:
                lock_busy_result = server.private_host_restart_audit_tick()
            finally:
                relay_restart._release_lock(lock_descriptor)
            lock_elapsed = time.monotonic() - started
            require(
                lock_busy_result.get("error") == "audit_event_busy"
                and lock_busy_result.get("ok") is False,
                f"outbox lock contention was not bounded: {lock_busy_result}",
                failures,
            )
            require(lock_elapsed < 1.0, f"outbox lock blocked API path for {lock_elapsed:.3f}s", failures)
            require(
                server.consume_private_host_restart_audit_events().get("ingested") == 1,
                "lock-busy audit event did not retry",
                failures,
            )

            lock_path = outbox / ".events.lock"
            holder = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    (
                        "import fcntl,os,sys,time;"
                        "fd=os.open(sys.argv[1],os.O_RDWR|os.O_CREAT,0o600);"
                        "os.fchmod(fd,0o600);fcntl.flock(fd,fcntl.LOCK_EX);"
                        "print('ready',flush=True);time.sleep(5)"
                    ),
                    str(lock_path),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            require(holder.stdout is not None and holder.stdout.readline().strip() == "ready", "lock holder did not start", failures)
            started = time.monotonic()
            cross_process_write = server.private_host_cli._try_write_managed_restart_audit_event(
                server.private_host_cli.paths(),
                {
                    "action": "enable",
                    "transition_ref": "transition_cross_process_lock",
                    "transaction_sequence": 560,
                },
                state="healthy",
                revision=4,
            )
            cross_process_elapsed = time.monotonic() - started
            holder.terminate()
            holder.wait(timeout=2)
            require(
                cross_process_write is False and cross_process_elapsed < 1.0,
                "cross-process outbox lock blocked lifecycle write",
                failures,
            )

            lock_receipt = relay_restart.create_restart_receipt(
                receipt_path=receipt_path,
                sequence_path=sequence_path,
                action="enable",
                transition_ref="transition_receipt_lock_busy",
                active_config_path=active_config,
                host_config_path=host_config,
                active_original_config=active_config.read_bytes(),
                active_target_config=b'{"enabled":true}\n',
                host_original_config=host_config.read_bytes(),
                host_target_config=b'{"relay_enabled":true}\n',
            )
            receipt_lock_descriptor = relay_restart._acquire_lock(receipt_path)
            started = time.monotonic()
            try:
                receipt_busy_result = server.private_host_restart_audit_tick()
            finally:
                relay_restart._release_lock(receipt_lock_descriptor)
            receipt_lock_elapsed = time.monotonic() - started
            require(
                receipt_busy_result.get("error") == "audit_event_busy"
                and receipt_lock_elapsed < 1.0,
                f"receipt lock contention was not bounded: {receipt_busy_result}",
                failures,
            )
            lock_receipt = relay_restart.transition_restart_receipt(
                receipt_path=receipt_path,
                sequence_path=sequence_path,
                action="enable",
                transition_ref="transition_receipt_lock_busy",
                transaction_sequence=lock_receipt["transaction_sequence"],
                expected_revision=lock_receipt["revision"],
                state="restoring_config",
            )
            lock_receipt = relay_restart.transition_restart_receipt(
                receipt_path=receipt_path,
                sequence_path=sequence_path,
                action="enable",
                transition_ref="transition_receipt_lock_busy",
                transaction_sequence=lock_receipt["transaction_sequence"],
                expected_revision=lock_receipt["revision"],
                state="rolled_back",
            )
            relay_restart.finalize_restart_receipt(
                receipt_path=receipt_path,
                sequence_path=sequence_path,
                action="enable",
                transition_ref="transition_receipt_lock_busy",
                transaction_sequence=lock_receipt["transaction_sequence"],
                expected_revision=lock_receipt["revision"],
            )

            corrupt_event = outbox / "restart-570.json"
            corrupt_event.write_bytes(b"{invalid-json\n")
            corrupt_event.chmod(0o600)
            valid_after_corrupt = enqueue(outbox, 571)
            isolated_events = relay_restart.pending_restart_audit_events(
                outbox_dir=outbox,
                limit=1,
            )
            quarantine = outbox / "quarantine"
            quarantined = list(quarantine.glob("restart-570*.invalid")) if quarantine.is_dir() else []
            require(
                isolated_events == [valid_after_corrupt],
                "corrupt event prevented the next valid event from being read",
                failures,
            )
            require(
                len(quarantined) == 1
                and stat.S_IMODE(quarantine.stat().st_mode) == 0o700
                and stat.S_IMODE(quarantined[0].stat().st_mode) == 0o600,
                "corrupt event was not retained in owner-only quarantine",
                failures,
            )
            relay_restart.acknowledge_restart_audit_event(
                outbox_dir=outbox,
                transaction_sequence=valid_after_corrupt["transaction_sequence"],
                revision=valid_after_corrupt["revision"],
                transition_ref=valid_after_corrupt["transition_ref"],
            )

            backlog_events = [enqueue(outbox, sequence) for sequence in range(700, 770)]
            exact_backlog_event = relay_restart.read_restart_audit_event(
                outbox_dir=outbox,
                transaction_sequence=769,
                nonblocking=True,
            )
            require(
                exact_backlog_event == backlog_events[-1],
                "terminal event beyond the first 64 entries was not found directly",
                failures,
            )
            for event in backlog_events:
                relay_restart.acknowledge_restart_audit_event(
                    outbox_dir=outbox,
                    transaction_sequence=event["transaction_sequence"],
                    revision=event["revision"],
                    transition_ref=event["transition_ref"],
                )

            ignored = outbox / "operator-note.txt"
            ignored.write_text("ignored non-event fixture\n", encoding="ascii")
            ignored.chmod(0o600)
            original_limit = relay_restart.MAX_AUDIT_OUTBOX_ENTRIES
            relay_restart.MAX_AUDIT_OUTBOX_ENTRIES = 1
            enqueue(outbox, 601)
            capacity_rejected = False
            try:
                enqueue(outbox, 602)
            except relay_restart.RelayRestartError as exc:
                capacity_rejected = exc.code == "audit_event_invalid"
            finally:
                relay_restart.MAX_AUDIT_OUTBOX_ENTRIES = original_limit
            require(capacity_rejected, "outbox capacity was not enforced on write", failures)
            require(
                server.consume_private_host_restart_audit_events().get("ingested") == 1,
                "bounded outbox event was not consumable",
                failures,
            )

            print(
                json.dumps(
                    {
                        "ok": not failures,
                        "operation": "private_host_restart_audit_retention_smoke",
                        "terminal_states_recorded": ["healthy", "rolled_back", "rollback_failed"],
                        "exactly_once": count == 1,
                        "database_binding_enforced": skipped.get("skipped") == "database_not_private_host",
                        "unfinalized_healthy_deferred": deferred.get("deferred") == 1,
                        "failed_finalize_outcome_replaceable": replacement.get("state") == "rolled_back",
                        "outbox_failure_nonfatal": pending_outcome.get("audit_event_pending") is True,
                        "sqlite_busy_nonblocking": busy_result.get("error") == "audit_database_busy" and elapsed < 1.0,
                        "outbox_lock_nonblocking": lock_busy_result.get("error") == "audit_event_busy" and lock_elapsed < 1.0,
                        "receipt_lock_nonblocking": receipt_busy_result.get("error") == "audit_event_busy" and receipt_lock_elapsed < 1.0,
                        "cross_process_lifecycle_lock_nonblocking": cross_process_write is False and cross_process_elapsed < 1.0,
                        "retryable_finalize_kept_host_alive": finalize_healthy and not finalize_terminated and not finalize_terminations,
                        "invalid_finalize_failed_closed": invalid_terminations == [invalid_process.pid],
                        "corrupt_event_quarantined": len(quarantined) == 1,
                        "backlog_exact_lookup": exact_backlog_event == backlog_events[-1],
                        "outbox_capacity_enforced": capacity_rejected,
                        "private_event_acknowledged": not (outbox / "restart-102.json").exists(),
                        "credentials_omitted": True,
                        "paths_omitted": True,
                        "failures": failures,
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
    finally:
        server.DB_PATH = original_db_path
        if original_host_home is None:
            os.environ.pop("AGENTOPS_HOST_HOME", None)
        else:
            os.environ["AGENTOPS_HOST_HOME"] = original_host_home
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
