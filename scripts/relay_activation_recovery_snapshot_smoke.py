#!/usr/bin/env python3
"""Exercise guarded read-only Relay activation recovery snapshots."""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Callable


sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agentops_mis_cli.relay_activation_journal import (  # noqa: E402
    GENESIS_REVISION_SHA256,
    ActivationJournalIdentity,
    RelayActivationJournalError,
    _open_fixture_store,
    _open_locked_production_store,
    build_activation_receipt,
    build_activation_revision,
    parse_activation_receipt,
    parse_activation_revision,
)


PLAN_A = "a" * 64
PLAN_B = "b" * 64
PRIVATE_CANARY = "RECOVERY_SNAPSHOT_PRIVATE_CANARY"


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def descriptor_count() -> int | None:
    for directory in ("/proc/self/fd", "/dev/fd"):
        try:
            return len(os.listdir(directory))
        except OSError:
            continue
    return None


def identity(plan_sha256: str) -> ActivationJournalIdentity:
    return ActivationJournalIdentity(
        plan_sha256=plan_sha256,
        release_id="0.1.0-" + ("1" * 12),
        version_id="0.1.0",
        pre_unit_file_state="disabled",
        pre_active_state="inactive",
        pre_enablement_inventory_sha256="2" * 64,
        unit_identity_sha256="3" * 64,
    )


def record_hash(raw: bytes) -> str:
    return parse_activation_revision(raw).record_sha256


def revision(
    records: list[bytes],
    journal_identity: ActivationJournalIdentity,
    *,
    phase: str,
    step_id: str,
    intent_id: str | None = None,
    observation_id: str | None = None,
    owns_enable: bool = False,
    owns_start: bool = False,
    terminal_state: str | None = None,
    receipt_sha256: str | None = None,
) -> bytes:
    observation_sha256 = (
        hashlib.sha256((observation_id or "").encode("ascii")).hexdigest()
        if phase == "observed"
        else None
    )
    return build_activation_revision(
        journal_identity,
        revision=len(records) + 1,
        previous_revision_sha256=(
            GENESIS_REVISION_SHA256
            if not records
            else record_hash(records[-1])
        ),
        phase=phase,
        step_id=step_id,
        intent_id=intent_id,
        observation_id=observation_id,
        observation_sha256=observation_sha256,
        owns_enable=owns_enable,
        owns_start=owns_start,
        terminal_state=terminal_state,
        receipt_sha256=receipt_sha256,
    )


def active_prefix(
    journal_identity: ActivationJournalIdentity,
) -> list[bytes]:
    records: list[bytes] = []

    def add(**values: object) -> None:
        records.append(revision(records, journal_identity, **values))

    add(phase="prepared", step_id="transaction_open")
    add(
        phase="intent",
        step_id="daemon_reload",
        intent_id="daemon_reload_requested",
    )
    add(
        phase="observed",
        step_id="daemon_reload",
        intent_id="daemon_reload_requested",
        observation_id="daemon_reload_observed",
    )
    add(
        phase="intent",
        step_id="enable",
        intent_id="enable_requested",
    )
    add(
        phase="observed",
        step_id="enable",
        intent_id="enable_requested",
        observation_id="enable_observed",
        owns_enable=True,
    )
    add(
        phase="intent",
        step_id="start",
        intent_id="start_requested",
        owns_enable=True,
    )
    add(
        phase="observed",
        step_id="start",
        intent_id="start_requested",
        observation_id="start_observed",
        owns_enable=True,
        owns_start=True,
    )
    add(
        phase="intent",
        step_id="verify",
        intent_id="verify_requested",
        owns_enable=True,
        owns_start=True,
    )
    add(
        phase="observed",
        step_id="verify",
        intent_id="verify_requested",
        observation_id="verify_observed",
        owns_enable=True,
        owns_start=True,
    )
    return records


def terminal_pair(
    records: list[bytes],
    journal_identity: ActivationJournalIdentity,
) -> tuple[bytes, bytes]:
    receipt = build_activation_receipt(
        journal_identity,
        terminal_revision=len(records) + 1,
        previous_revision_sha256=record_hash(records[-1]),
        terminal_state="active",
        owns_enable=True,
        owns_start=True,
        result_id="activation_succeeded",
    )
    receipt_sha256 = parse_activation_receipt(receipt).receipt_sha256
    terminal = revision(
        records,
        journal_identity,
        phase="terminal",
        step_id="terminal",
        owns_enable=True,
        owns_start=True,
        terminal_state="active",
        receipt_sha256=receipt_sha256,
    )
    return receipt, terminal


def expect_error(
    callback: Callable[[], object],
    *,
    failures: list[str],
    label: str,
    expected: str = "activation_journal_recovery_required",
) -> None:
    try:
        callback()
    except RelayActivationJournalError as exc:
        if (
            exc.error_id != expected
            or PRIVATE_CANARY in str(exc)
        ):
            failures.append(f"{label}: wrong bounded error")
        return
    failures.append(f"{label}: unexpectedly succeeded")


def prepare_production_root(root: Path) -> None:
    root.chmod(0o700)
    var = root / "var"
    library = var / "lib"
    admin = library / "agentops-relayctl"
    activation = admin / "activation"
    var.mkdir(mode=0o755)
    library.mkdir(mode=0o755)
    admin.mkdir(mode=0o700)
    activation.mkdir(mode=0o700)
    (activation / "receipts").mkdir(mode=0o700)
    (activation / "transactions").mkdir(mode=0o700)
    lifecycle = admin / "lifecycle.lock"
    lifecycle.write_bytes(b"")
    lifecycle.chmod(0o600)


def main() -> int:
    failures: list[str] = []
    descriptors_before = descriptor_count()

    with tempfile.TemporaryDirectory(
        prefix="relay-recovery-snapshot-orphan-"
    ) as temporary:
        root = Path(temporary)
        root.chmod(0o700)
        journal_identity = identity(PLAN_A)
        records = active_prefix(journal_identity)
        receipt, terminal = terminal_pair(records, journal_identity)
        with _open_fixture_store(root) as store:
            for raw in records:
                store.publish_revision(raw)
            before_receipt = store._load_recovery_snapshot(PLAN_A)
            store.publish_receipt(receipt)
            orphan = store._load_recovery_snapshot(PLAN_A)
            store.publish_revision(terminal)
            completed = store._load_recovery_snapshot(PLAN_A)
        require(
            len(before_receipt.revisions) == len(records)
            and before_receipt.receipt is None,
            "receipt-free snapshot was not exact",
            failures,
        )
        require(
            orphan.revisions[-1].phase == "observed"
            and orphan.receipt is not None
            and orphan.receipt.receipt_sha256
            == parse_activation_receipt(receipt).receipt_sha256,
            "valid orphan receipt was not identified",
            failures,
        )
        require(
            completed.revisions[-1].phase == "terminal"
            and completed.receipt is not None,
            "completed terminal snapshot was not identified",
            failures,
        )

    with tempfile.TemporaryDirectory(
        prefix="relay-recovery-snapshot-multi-plan-"
    ) as temporary:
        root = Path(temporary)
        root.chmod(0o700)
        identity_a = identity(PLAN_A)
        records_a = active_prefix(identity_a)
        receipt_a, terminal_a = terminal_pair(records_a, identity_a)
        records_b: list[bytes] = []
        records_b.append(
            revision(
                records_b,
                identity(PLAN_B),
                phase="prepared",
                step_id="transaction_open",
            )
        )
        with _open_fixture_store(root) as store:
            for raw in records_a:
                store.publish_revision(raw)
            store.publish_receipt(receipt_a)
            store.publish_revision(terminal_a)
            store.publish_revision(records_b[0])
            second = store._load_recovery_snapshot(PLAN_B)
        require(
            len(second.revisions) == 1
            and second.revisions[0].phase == "prepared"
            and second.receipt is None,
            "another completed plan receipt contaminated recovery",
            failures,
        )

    with tempfile.TemporaryDirectory(
        prefix="relay-recovery-snapshot-invalid-orphan-"
    ) as temporary:
        root = Path(temporary)
        root.chmod(0o700)
        journal_identity = identity(PLAN_A)
        records: list[bytes] = []
        records.append(
            revision(
                records,
                journal_identity,
                phase="prepared",
                step_id="transaction_open",
            )
        )
        premature_receipt = build_activation_receipt(
            journal_identity,
            terminal_revision=2,
            previous_revision_sha256=record_hash(records[-1]),
            terminal_state="active",
            owns_enable=False,
            owns_start=False,
            result_id="activation_succeeded",
        )
        with _open_fixture_store(root) as store:
            store.publish_revision(records[0])
            store.publish_receipt(premature_receipt)
            expect_error(
                lambda: store._load_recovery_snapshot(PLAN_A),
                failures=failures,
                label="premature receipt",
            )

    with tempfile.TemporaryDirectory(
        prefix="relay-recovery-snapshot-production-"
    ) as temporary:
        root = Path(temporary)
        prepare_production_root(root)
        records: list[bytes] = []
        records.append(
            revision(
                records,
                identity(PLAN_A),
                phase="prepared",
                step_id="transaction_open",
            )
        )
        session = None
        with _open_locked_production_store(root) as session:
            session.publish_revision(records[0])
            locked = session._load_recovery_snapshot(PLAN_A)
        require(
            len(locked.revisions) == 1
            and locked.receipt is None,
            "locked production recovery snapshot was not readable",
            failures,
        )
        expect_error(
            lambda: session._load_recovery_snapshot(PLAN_A),
            failures=failures,
            label="closed production session",
            expected="activation_journal_invalid",
        )

    descriptors_after = descriptor_count()
    descriptor_stable = (
        descriptors_before is None
        or descriptors_after is None
        or descriptors_before == descriptors_after
    )
    require(
        descriptor_stable,
        "recovery snapshot leaked descriptors",
        failures,
    )

    public_values = json.dumps(
        {
            "completed_revision_count": len(completed.revisions),
            "orphan_receipt_sha256": (
                orphan.receipt.receipt_sha256
                if orphan.receipt is not None
                else None
            ),
            "prepared_revision_count": len(second.revisions),
        },
        ensure_ascii=True,
        sort_keys=True,
    )
    require(
        PRIVATE_CANARY not in public_values
        and "/var/lib" not in public_values,
        "recovery snapshot summary exposed private detail",
        failures,
    )

    result = {
        "completed_terminal_identified": (
            completed.revisions[-1].phase == "terminal"
        ),
        "descriptor_stable": descriptor_stable,
        "failures": failures,
        "foreign_receipts_ignored": second.receipt is None,
        "invalid_orphan_rejected": True,
        "locked_store_guarded": len(locked.revisions) == 1,
        "network_used": False,
        "ok": not failures,
        "operation": "relay_activation_recovery_snapshot_smoke",
        "orphan_receipt_identified": orphan.receipt is not None,
        "private_payload_omitted": PRIVATE_CANARY not in public_values,
        "systemd_mutation_performed": False,
        "write_scope": "fixture_journal_only",
    }
    print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
