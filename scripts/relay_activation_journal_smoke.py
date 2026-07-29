#!/usr/bin/env python3
"""Exercise immutable Relay activation journal and recovery primitives."""
from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable


sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agentops_mis_cli import relay_activation_journal as journal  # noqa: E402
from agentops_mis_cli.relay_activation_journal import (  # noqa: E402
    ACTIVATION_JOURNAL_SCHEMA,
    GENESIS_REVISION_SHA256,
    ActivationJournalIdentity,
    RelayActivationJournalError,
    _open_fixture_store,
    build_activation_receipt,
    build_activation_revision,
    parse_activation_receipt,
    parse_activation_revision,
    project_activation_journal,
    validate_activation_revision_chain,
)


PLAN_A = "a" * 64
PLAN_B = "b" * 64
PRIVATE_CANARY = "relay-journal-private-canary"


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def canonical(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("ascii")


def record_hash(raw: bytes) -> str:
    return parse_activation_revision(raw).record_sha256


def identity(
    plan_sha256: str = PLAN_A,
    *,
    pre_unit_file_state: str = "disabled",
    pre_active_state: str = "inactive",
) -> ActivationJournalIdentity:
    return ActivationJournalIdentity(
        plan_sha256=plan_sha256,
        release_id="0.1.0-" + ("1" * 12),
        version_id="0.1.0",
        pre_unit_file_state=pre_unit_file_state,
        pre_active_state=pre_active_state,
        pre_enablement_inventory_sha256="2" * 64,
        unit_identity_sha256="3" * 64,
    )


def revision(
    records: list[bytes],
    journal_identity: ActivationJournalIdentity,
    *,
    phase: str,
    step_id: str,
    intent_id: str | None = None,
    observation_id: str | None = None,
    observation_sha256: str | None = None,
    owns_enable: bool = False,
    owns_start: bool = False,
    terminal_state: str | None = None,
    receipt_sha256: str | None = None,
) -> bytes:
    if phase == "observed" and observation_sha256 is None:
        observation_sha256 = hashlib.sha256(
            (observation_id or "").encode("ascii")
        ).hexdigest()
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
        observation_id="daemon_reload_completed",
    )
    owns_enable = False
    owns_start = False
    if journal_identity.pre_unit_file_state == "disabled":
        add(
            phase="intent",
            step_id="enable",
            intent_id="enable_requested",
        )
        owns_enable = True
        add(
            phase="observed",
            step_id="enable",
            intent_id="enable_requested",
            observation_id="enable_observed",
            owns_enable=owns_enable,
        )
    if journal_identity.pre_active_state == "inactive":
        add(
            phase="intent",
            step_id="start",
            intent_id="start_requested",
            owns_enable=owns_enable,
        )
        owns_start = True
        add(
            phase="observed",
            step_id="start",
            intent_id="start_requested",
            observation_id="start_observed",
            owns_enable=owns_enable,
            owns_start=owns_start,
        )
    add(
        phase="intent",
        step_id="verify",
        intent_id="verify_requested",
        owns_enable=owns_enable,
        owns_start=owns_start,
    )
    add(
        phase="observed",
        step_id="verify",
        intent_id="verify_requested",
        observation_id="active_verified",
        owns_enable=owns_enable,
        owns_start=owns_start,
    )
    return records


def rolled_back_prefix(
    journal_identity: ActivationJournalIdentity,
) -> list[bytes]:
    records = active_prefix(journal_identity)[:-2]

    def add(**values: object) -> None:
        records.append(revision(records, journal_identity, **values))

    add(
        phase="intent",
        step_id="rollback_stop",
        intent_id="rollback_stop_requested",
        owns_enable=True,
        owns_start=True,
    )
    add(
        phase="observed",
        step_id="rollback_stop",
        intent_id="rollback_stop_requested",
        observation_id="rollback_stop_observed",
        owns_enable=True,
    )
    add(
        phase="intent",
        step_id="rollback_disable",
        intent_id="rollback_disable_requested",
        owns_enable=True,
    )
    add(
        phase="observed",
        step_id="rollback_disable",
        intent_id="rollback_disable_requested",
        observation_id="rollback_disable_observed",
    )
    add(
        phase="intent",
        step_id="verify",
        intent_id="rollback_verify_requested",
    )
    add(
        phase="observed",
        step_id="verify",
        intent_id="rollback_verify_requested",
        observation_id="rollback_verified",
    )
    return records


def terminal_pair(
    records: list[bytes],
    journal_identity: ActivationJournalIdentity,
    *,
    terminal_state: str,
    owns_enable: bool,
    owns_start: bool,
) -> tuple[bytes, bytes]:
    receipt = build_activation_receipt(
        journal_identity,
        terminal_revision=len(records) + 1,
        previous_revision_sha256=record_hash(records[-1]),
        terminal_state=terminal_state,
        owns_enable=owns_enable,
        owns_start=owns_start,
        result_id=(
            "activation_succeeded"
            if terminal_state == "active"
            else "rollback_succeeded"
        ),
    )
    receipt_sha256 = parse_activation_receipt(receipt).receipt_sha256
    terminal = revision(
        records,
        journal_identity,
        phase="terminal",
        step_id="terminal",
        owns_enable=owns_enable,
        owns_start=owns_start,
        terminal_state=terminal_state,
        receipt_sha256=receipt_sha256,
    )
    return receipt, terminal


def expect_error(
    operation: Callable[[], object],
    *,
    expected: str | None,
    label: str,
    failures: list[str],
) -> None:
    try:
        operation()
    except RelayActivationJournalError as exc:
        if expected is not None and exc.error_id != expected:
            failures.append(
                f"{label}: expected {expected}, got {exc.error_id}"
            )
        return
    failures.append(f"{label}: operation unexpectedly succeeded")


def prepare_root(path: Path) -> None:
    path.chmod(0o700)


def descriptor_count() -> int | None:
    for directory in ("/proc/self/fd", "/dev/fd"):
        try:
            return len(os.listdir(directory))
        except OSError:
            continue
    return None


def exercise_complete_chain(
    *,
    records: list[bytes],
    journal_identity: ActivationJournalIdentity,
    terminal_state: str,
    owns_enable: bool,
    owns_start: bool,
    failures: list[str],
) -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        prepare_root(root)
        with _open_fixture_store(root) as store:
            initial = store.inspect_store()
            require(
                initial.get("state") == "ready"
                and initial.get("completed_transaction_count") == 0,
                f"{terminal_state}: empty store not ready",
                failures,
            )
            for raw in records:
                projection = store.publish_revision(raw)
                require(
                    projection.get("recovery_required") is True,
                    f"{terminal_state}: in-progress chain not recoverable",
                    failures,
                )
            receipt, terminal = terminal_pair(
                records,
                journal_identity,
                terminal_state=terminal_state,
                owns_enable=owns_enable,
                owns_start=owns_start,
            )
            created = store.publish_receipt(receipt)
            require(
                created.get("outcome") == "created",
                f"{terminal_state}: receipt was not created",
                failures,
            )
            require(
                store.inspect_store().get("recovery_required") is True,
                f"{terminal_state}: orphan receipt did not require recovery",
                failures,
            )
            final = store.publish_revision(terminal)
            require(
                final.get("ok") is True
                and final.get("state") == terminal_state
                and final.get("revision_count") == len(records) + 1,
                f"{terminal_state}: terminal projection mismatch",
                failures,
            )
            require(
                store.inspect_store().get("completed_transaction_count") == 1,
                f"{terminal_state}: completed store count mismatch",
                failures,
            )
            replay = store.publish_receipt(receipt)
            require(
                replay.get("outcome") == "existing",
                f"{terminal_state}: exact receipt replay not idempotent",
                failures,
            )
            expect_error(
                lambda: store.publish_revision(terminal),
                expected="activation_journal_recovery_required",
                label=f"{terminal_state}: revision replay",
                failures=failures,
            )


def parser_cases(failures: list[str]) -> None:
    records = active_prefix(identity())
    base = records[0]
    parsed = parse_activation_revision(base)
    require(
        parsed.revision == 1
        and parsed.previous_revision_sha256
        == GENESIS_REVISION_SHA256,
        "prepared parser round trip failed",
        failures,
    )

    boolean_revision = json.loads(base.decode("ascii"))
    boolean_revision["revision"] = True
    boolean_revision.pop("record_sha256")
    boolean_revision["record_sha256"] = hashlib.sha256(
        canonical(boolean_revision)
    ).hexdigest()
    expect_error(
        lambda: parse_activation_revision(canonical(boolean_revision)),
        expected="activation_journal_invalid",
        label="bool revision",
        failures=failures,
    )

    unknown_key = json.loads(base.decode("ascii"))
    unknown_key["unexpected"] = "value"
    expect_error(
        lambda: parse_activation_revision(canonical(unknown_key)),
        expected="activation_journal_invalid",
        label="unknown revision key",
        failures=failures,
    )

    wrong_hash = json.loads(base.decode("ascii"))
    wrong_hash["record_sha256"] = "0" * 64
    expect_error(
        lambda: parse_activation_revision(canonical(wrong_hash)),
        expected="activation_journal_invalid",
        label="wrong revision hash",
        failures=failures,
    )

    compact = json.dumps(
        json.loads(base.decode("ascii")),
        ensure_ascii=True,
        sort_keys=True,
    ).encode("ascii")
    expect_error(
        lambda: parse_activation_revision(compact),
        expected="activation_journal_invalid",
        label="noncanonical revision",
        failures=failures,
    )

    expect_error(
        lambda: parse_activation_revision(
            b'{"schema_id":"one","schema_id":"two"}\n'
        ),
        expected="activation_journal_invalid",
        label="duplicate revision key",
        failures=failures,
    )

    for key in json.loads(base.decode("ascii")):
        hostile = json.loads(base.decode("ascii"))
        hostile[key] = []
        if key != "record_sha256":
            hostile.pop("record_sha256")
            hostile["record_sha256"] = hashlib.sha256(
                canonical(hostile)
            ).hexdigest()
        expect_error(
            lambda value=canonical(hostile): parse_activation_revision(value),
            expected="activation_journal_invalid",
            label=f"hostile revision type {key}",
            failures=failures,
        )

    receipt, terminal = terminal_pair(
        records,
        identity(),
        terminal_state="active",
        owns_enable=True,
        owns_start=True,
    )
    parsed_receipt = parse_activation_receipt(receipt)
    complete = tuple(
        parse_activation_revision(raw) for raw in (*records, terminal)
    )
    validate_activation_revision_chain(complete)
    projection = project_activation_journal(complete, parsed_receipt)
    require(
        projection.get("state") == "active"
        and set(projection)
        == {
            "ok",
            "operation_id",
            "receipt_sha256",
            "recovery_required",
            "revision_count",
            "schema_id",
            "state",
        },
        "bounded terminal projection mismatch",
        failures,
    )
    require(
        PRIVATE_CANARY not in json.dumps(projection, sort_keys=True),
        "private canary leaked into projection",
        failures,
    )
    for (
        plan_sha256,
        pre_unit_file_state,
        pre_active_state,
        owns_enable,
        owns_start,
    ) in (
        ("c" * 64, "enabled", "inactive", False, True),
        ("d" * 64, "disabled", "active", True, False),
    ):
        alternate_identity = identity(
            plan_sha256,
            pre_unit_file_state=pre_unit_file_state,
            pre_active_state=pre_active_state,
        )
        alternate_records = active_prefix(alternate_identity)
        alternate_receipt, alternate_terminal = terminal_pair(
            alternate_records,
            alternate_identity,
            terminal_state="active",
            owns_enable=owns_enable,
            owns_start=owns_start,
        )
        validate_activation_revision_chain(
            tuple(
                parse_activation_revision(raw)
                for raw in (*alternate_records, alternate_terminal)
            )
        )
        require(
            parse_activation_receipt(alternate_receipt).owns_enable
            is owns_enable,
            "alternate pre-state receipt ownership mismatch",
            failures,
        )
    expect_error(
        lambda: build_activation_revision(
            identity(
                "e" * 64,
                pre_unit_file_state="enabled",
                pre_active_state="active",
            ),
            revision=1,
            previous_revision_sha256=GENESIS_REVISION_SHA256,
            phase="prepared",
            step_id="transaction_open",
        ),
        expected="activation_journal_invalid",
        label="already-active identity",
        failures=failures,
    )
    for key in json.loads(receipt.decode("ascii")):
        hostile = json.loads(receipt.decode("ascii"))
        hostile[key] = []
        if key != "receipt_sha256":
            hostile.pop("receipt_sha256")
            hostile["receipt_sha256"] = hashlib.sha256(
                canonical(hostile)
            ).hexdigest()
        expect_error(
            lambda value=canonical(hostile): parse_activation_receipt(value),
            expected="activation_journal_invalid",
            label=f"hostile receipt type {key}",
            failures=failures,
        )

    invalid_observed = list(records[:1])
    invalid_observed.append(
        revision(
            invalid_observed,
            identity(),
            phase="observed",
            step_id="start",
            intent_id="start_requested",
            observation_id="start_observed",
            owns_start=True,
        )
    )
    expect_error(
        lambda: validate_activation_revision_chain(
            tuple(parse_activation_revision(raw) for raw in invalid_observed)
        ),
        expected="activation_journal_invalid",
        label="observed without intent",
        failures=failures,
    )

    skipped: list[bytes] = []
    skipped.append(
        revision(
            skipped,
            identity(),
            phase="prepared",
            step_id="transaction_open",
        )
    )
    skipped.append(
        revision(
            skipped,
            identity(),
            phase="intent",
            step_id="verify",
            intent_id="verify_requested",
        )
    )
    skipped.append(
        revision(
            skipped,
            identity(),
            phase="observed",
            step_id="verify",
            intent_id="verify_requested",
            observation_id="active_verified",
        )
    )
    skipped_receipt, skipped_terminal = terminal_pair(
        skipped,
        identity(),
        terminal_state="active",
        owns_enable=False,
        owns_start=False,
    )
    require(
        parse_activation_receipt(skipped_receipt).terminal_state == "active",
        "skipped-chain receipt parser failed unexpectedly",
        failures,
    )
    expect_error(
        lambda: validate_activation_revision_chain(
            tuple(
                parse_activation_revision(raw)
                for raw in (*skipped, skipped_terminal)
            )
        ),
        expected="activation_journal_invalid",
        label="skipped mutation sequence",
        failures=failures,
    )


def store_recovery_cases(failures: list[str]) -> None:
    prepared = active_prefix(identity())[0]

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        prepare_root(root)
        with _open_fixture_store(root) as store:
            os.mkdir(PLAN_A, 0o700, dir_fd=store.transactions_fd)
            expect_error(
                lambda: store.publish_revision(prepared),
                expected="activation_journal_recovery_required",
                label="preexisting empty plan directory",
                failures=failures,
            )

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        prepare_root(root)
        with _open_fixture_store(root) as store:
            store.publish_revision(prepared)
            plan_fd = store._plan_directory(PLAN_A, create=False)
            try:
                descriptor = os.open(
                    ".revision-000002.json.tmp",
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                    dir_fd=plan_fd,
                )
                os.close(descriptor)
            finally:
                os.close(plan_fd)
            require(
                store.inspect_plan(PLAN_A).get("recovery_required") is True,
                "ambiguous temporary file did not require recovery",
                failures,
            )
            expect_error(
                lambda: journal._read_file_at(
                    store.transactions_fd,
                    "../escape",
                    expected_uid=store.expected_uid,
                    expected_gid=store.expected_gid,
                ),
                expected="activation_journal_invalid",
                label="path escape",
                failures=failures,
            )

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        root.chmod(0o755)
        expect_error(
            lambda: _open_fixture_store(root),
            expected="activation_journal_invalid",
            label="unsafe root mode",
            failures=failures,
        )

    with tempfile.TemporaryDirectory() as temporary:
        base = Path(temporary)
        prepare_root(base)
        real_root = base / "real"
        real_root.mkdir(mode=0o700)
        linked_root = base / "linked"
        linked_root.symlink_to(real_root, target_is_directory=True)
        expect_error(
            lambda: _open_fixture_store(linked_root),
            expected="activation_journal_invalid",
            label="symlink root",
            failures=failures,
        )

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        prepare_root(root)
        with _open_fixture_store(root) as store:
            for index in range(journal.MAX_JOURNAL_RECEIPTS + 1):
                descriptor = os.open(
                    f"{index:064x}.json",
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                    dir_fd=store.receipts_fd,
                )
                os.write(descriptor, b"x")
                os.close(descriptor)
            require(
                store.inspect_store().get("recovery_required") is True,
                "receipt count overflow did not require recovery",
                failures,
            )


def failure_injection_cases(failures: list[str]) -> None:
    prepared = active_prefix(identity())[0]
    cases = ("write", "fsync", "link", "unlink")

    for operation_name in cases:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prepare_root(root)
            with _open_fixture_store(root) as store:
                original = getattr(journal.os, operation_name)
                calls = 0

                def fail(*args: object, **kwargs: object) -> object:
                    nonlocal calls
                    calls += 1
                    if operation_name != "fsync" or calls == 2:
                        raise OSError(f"injected {operation_name}")
                    return original(*args, **kwargs)

                setattr(journal.os, operation_name, fail)
                try:
                    expect_error(
                        lambda: store.publish_revision(prepared),
                        expected=None,
                        label=f"injected {operation_name}",
                        failures=failures,
                    )
                finally:
                    setattr(journal.os, operation_name, original)
                require(
                    store.inspect_store().get("recovery_required") is True,
                    (
                        f"injected {operation_name}: ambiguous store did not "
                        "require recovery"
                    ),
                    failures,
                )


def publication_race_case(failures: list[str]) -> None:
    prepared = active_prefix(identity())[0]
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        prepare_root(root)
        with _open_fixture_store(root) as store:
            original_link = journal.os.link

            def replace_before_link(
                source: str,
                destination: str,
                *,
                src_dir_fd: int,
                dst_dir_fd: int,
                follow_symlinks: bool,
            ) -> None:
                os.unlink(source, dir_fd=src_dir_fd)
                replacement = os.open(
                    source,
                    os.O_RDWR | os.O_CREAT | os.O_EXCL,
                    0o600,
                    dir_fd=src_dir_fd,
                )
                try:
                    os.write(replacement, b"x" * len(prepared))
                    os.fsync(replacement)
                finally:
                    os.close(replacement)
                original_link(
                    source,
                    destination,
                    src_dir_fd=src_dir_fd,
                    dst_dir_fd=dst_dir_fd,
                    follow_symlinks=follow_symlinks,
                )

            journal.os.link = replace_before_link
            try:
                expect_error(
                    lambda: store.publish_revision(prepared),
                    expected="activation_journal_recovery_required",
                    label="temporary path replacement race",
                    failures=failures,
                )
            finally:
                journal.os.link = original_link
            require(
                store.inspect_store().get("recovery_required") is True,
                "temporary replacement race did not retain recovery state",
                failures,
            )


def bounded_enumeration_case(failures: list[str]) -> None:
    original_scandir = journal.os.scandir
    yielded = 0

    class FakeEntry:
        def __init__(self, name: str) -> None:
            self.name = name

    class FakeScandir:
        def __enter__(self) -> "FakeScandir":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def __iter__(self) -> "FakeScandir":
            return self

        def __next__(self) -> FakeEntry:
            nonlocal yielded
            yielded += 1
            if yielded > journal.MAX_JOURNAL_RECEIPTS + 1:
                raise AssertionError("bounded scanner consumed extra entries")
            return FakeEntry(f"{yielded:064x}.json")

    journal.os.scandir = lambda _descriptor: FakeScandir()
    try:
        expect_error(
            lambda: journal._bounded_directory_names(
                707,
                journal.MAX_JOURNAL_RECEIPTS,
            ),
            expected="activation_journal_recovery_required",
            label="bounded directory enumeration",
            failures=failures,
        )
    finally:
        journal.os.scandir = original_scandir
    require(
        yielded == journal.MAX_JOURNAL_RECEIPTS + 1,
        "bounded scanner did not stop at the first overflow entry",
        failures,
    )


def descriptor_cases(failures: list[str]) -> None:
    before = descriptor_count()
    for _index in range(20):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prepare_root(root)
            store = _open_fixture_store(root)
            store.close()
            store.close()
    after = descriptor_count()
    if before is not None and after is not None:
        require(before == after, "fixture store leaked descriptors", failures)

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        prepare_root(root)
        child = root / "child"
        child.mkdir(mode=0o700)
        parent_fd = os.open(root, journal._directory_flags())
        original_stat = journal.os.stat
        stat_calls = 0

        def fail_after_directory_open(*args: object, **kwargs: object):
            nonlocal stat_calls
            if args and args[0] == "child":
                stat_calls += 1
                if stat_calls % 2 == 0:
                    raise FileNotFoundError
            return original_stat(*args, **kwargs)

        failure_before = descriptor_count()
        journal.os.stat = fail_after_directory_open
        try:
            for index in range(12):
                expect_error(
                    lambda: journal._open_directory_at(
                        parent_fd,
                        "child",
                        expected_uid=os.geteuid(),
                        expected_gid=os.getegid(),
                        create=False,
                    ),
                    expected="activation_journal_invalid",
                    label=f"directory post-open stat failure {index}",
                    failures=failures,
                )
        finally:
            journal.os.stat = original_stat
        failure_after = descriptor_count()
        os.close(parent_fd)
        if failure_before is not None and failure_after is not None:
            require(
                failure_before == failure_after,
                "post-open directory stat failure leaked descriptors",
                failures,
            )


def main() -> int:
    failures: list[str] = []
    external_calls: list[str] = []
    original_socket = socket.socket
    original_create_connection = socket.create_connection
    original_popen = subprocess.Popen
    original_run = subprocess.run

    def blocked(*_args: object, **_kwargs: object) -> object:
        external_calls.append("blocked")
        raise AssertionError("journal smoke attempted external behavior")

    socket.socket = blocked
    socket.create_connection = blocked
    subprocess.Popen = blocked
    subprocess.run = blocked
    try:
        parser_cases(failures)
        exercise_complete_chain(
            records=active_prefix(identity()),
            journal_identity=identity(),
            terminal_state="active",
            owns_enable=True,
            owns_start=True,
            failures=failures,
        )
        exercise_complete_chain(
            records=rolled_back_prefix(identity(PLAN_B)),
            journal_identity=identity(PLAN_B),
            terminal_state="service_state_rolled_back",
            owns_enable=False,
            owns_start=False,
            failures=failures,
        )
        store_recovery_cases(failures)
        failure_injection_cases(failures)
        publication_race_case(failures)
        bounded_enumeration_case(failures)
        descriptor_cases(failures)
    finally:
        socket.socket = original_socket
        socket.create_connection = original_create_connection
        subprocess.Popen = original_popen
        subprocess.run = original_run

    require(not external_calls, "external behavior was attempted", failures)
    if failures:
        print(
            json.dumps(
                {
                    "failures": failures,
                    "ok": False,
                    "operation": "relay_activation_journal_smoke",
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 1
    print(
        json.dumps(
            {
                "failure_injection_cases": 4,
                "descriptor_failure_leak_free": True,
                "publication_race_rejected": True,
                "bounded_enumeration": True,
                "ok": True,
                "operation": "relay_activation_journal_smoke",
                "production_mutation_exposed": False,
                "recovery_cases": 4,
                "schema_id": ACTIVATION_JOURNAL_SCHEMA,
                "terminal_chains": 2,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
