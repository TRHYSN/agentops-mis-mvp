#!/usr/bin/env python3
"""Exercise the lifecycle-lock-bound Relay activation journal opener."""
from __future__ import annotations

import contextlib
import fcntl
import gc
import io
import json
import os
import stat
import sys
import tempfile
from pathlib import Path
from typing import Callable


sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agentops_mis_cli import relay_activation_journal as journal  # noqa: E402
from agentops_mis_cli import relay_admin  # noqa: E402
from agentops_mis_cli.relay_activation_journal import (  # noqa: E402
    GENESIS_REVISION_SHA256,
    ActivationJournalIdentity,
    RelayActivationJournalError,
    _open_locked_production_store,
    build_activation_revision,
)


PLAN_SHA256 = "a" * 64


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


def prepare_root(root: Path, *, exact_namespace: bool = False) -> Path:
    root.chmod(0o700)
    var = root / "var"
    library = var / "lib"
    admin = library / "agentops-relayctl"
    var.mkdir(mode=0o755)
    library.mkdir(mode=0o755)
    admin.mkdir(mode=0o700)
    var.chmod(0o755)
    library.chmod(0o755)
    admin.chmod(0o700)
    lifecycle = admin / "lifecycle.lock"
    lifecycle.write_bytes(b"")
    lifecycle.chmod(0o600)
    if exact_namespace:
        activation = admin / "activation"
        activation.mkdir(mode=0o700)
        (activation / "receipts").mkdir(mode=0o700)
        (activation / "transactions").mkdir(mode=0o700)
    return admin


def identity() -> ActivationJournalIdentity:
    return ActivationJournalIdentity(
        plan_sha256=PLAN_SHA256,
        release_id="0.1.0-" + ("1" * 12),
        version_id="0.1.0",
        pre_unit_file_state="disabled",
        pre_active_state="inactive",
        pre_enablement_inventory_sha256="2" * 64,
        unit_identity_sha256="3" * 64,
    )


def prepared_revision() -> bytes:
    return build_activation_revision(
        identity(),
        revision=1,
        previous_revision_sha256=GENESIS_REVISION_SHA256,
        phase="prepared",
        step_id="transaction_open",
    )


def open_once(root: Path) -> None:
    with _open_locked_production_store(root):
        pass


def expect_error(
    operation: Callable[[], object],
    *,
    expected: str,
    label: str,
    failures: list[str],
) -> None:
    try:
        operation()
    except RelayActivationJournalError as exc:
        if exc.error_id != expected:
            failures.append(
                f"{label}: expected {expected}, got {exc.error_id}"
            )
        return
    failures.append(f"{label}: operation unexpectedly succeeded")


def locked_lifecycle_case(
    failures: list[str],
) -> tuple[bool, bool, bool]:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        admin = prepare_root(root, exact_namespace=True)
        before = descriptor_count()
        raw_store = None
        session = None
        with _open_locked_production_store(root) as session:
            raw_store = session._store
            initial = session.inspect_store()
            expect_error(
                lambda: open_once(root),
                expected="activation_journal_busy",
                label="concurrent opener",
                failures=failures,
            )
            published = session.publish_revision(prepared_revision())
            require(
                initial.get("state") == "ready"
                and initial.get("completed_transaction_count") == 0,
                "fresh exact namespace was not ready",
                failures,
            )
            require(
                published.get("recovery_required") is True,
                "prepared production revision was not recoverable",
                failures,
            )
        expect_error(
            session.inspect_store,
            expected="activation_journal_invalid",
            label="closed session",
            failures=failures,
        )
        expect_error(
            raw_store.inspect_store,
            expected="activation_journal_invalid",
            label="raw store after context exit",
            failures=failures,
        )
        with _open_locked_production_store(root) as reopened:
            persisted = reopened.inspect_store()
            require(
                persisted.get("recovery_required") is True,
                "incomplete production journal did not persist",
                failures,
            )
        after = descriptor_count()
        fd_stable = (
            before is None or after is None or before == after
        )
        exact_preserved = (
            sorted(path.name for path in admin.iterdir())
            == ["activation", "lifecycle.lock"]
            and sorted(
                path.name
                for path in (admin / "activation").iterdir()
            )
            == ["receipts", "transactions"]
        )
        require(fd_stable, "production context leaked descriptors", failures)
        require(
            exact_preserved,
            "production opener changed the namespace topology",
            failures,
        )
        return True, fd_stable, exact_preserved


def abandoned_context_case(failures: list[str]) -> bool:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        prepare_root(root, exact_namespace=True)
        before = descriptor_count()

        unopened = _open_locked_production_store(root)
        del unopened
        gc.collect()
        open_once(root)

        entered = _open_locked_production_store(root)
        session = entered.__enter__()
        del session
        del entered
        gc.collect()
        open_once(root)

        after = descriptor_count()
        released = before is None or after is None or before == after
        require(
            released,
            "abandoned context retained the lock or descriptors",
            failures,
        )
        return released


def binding_race_cases(failures: list[str]) -> tuple[bool, bool]:
    activation_untouched = False
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        admin = prepare_root(root, exact_namespace=True)

        def replace_activation() -> None:
            nonlocal activation_untouched
            with _open_locked_production_store(root) as session:
                activation = admin / "activation"
                detached = admin / "activation-replaced"
                activation.rename(detached)
                activation.mkdir(mode=0o700)
                (activation / "receipts").mkdir(mode=0o700)
                (activation / "transactions").mkdir(mode=0o700)
                expect_error(
                    lambda: session.publish_revision(
                        prepared_revision()
                    ),
                    expected="activation_journal_recovery_required",
                    label="activation replacement publish",
                    failures=failures,
                )
                activation_untouched = not any(
                    any((directory / name).iterdir())
                    for directory in (activation, detached)
                    for name in ("receipts", "transactions")
                )

        expect_error(
            replace_activation,
            expected="activation_journal_recovery_required",
            label="activation replacement close",
            failures=failures,
        )
        require(
            activation_untouched,
            "replacement or detached activation namespace was modified",
            failures,
        )

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        admin = prepare_root(root, exact_namespace=True)

        def replace_lock() -> None:
            with _open_locked_production_store(root) as session:
                lifecycle = admin / "lifecycle.lock"
                lifecycle.rename(admin / "lifecycle.lock.replaced")
                lifecycle.write_bytes(b"")
                lifecycle.chmod(0o600)
                expect_error(
                    session.inspect_store,
                    expected="activation_journal_recovery_required",
                    label="lifecycle lock replacement",
                    failures=failures,
                )

        expect_error(
            replace_lock,
            expected="activation_journal_recovery_required",
            label="lock replacement close",
            failures=failures,
        )
    return activation_untouched, True


def acquisition_lock_race_case(failures: list[str]) -> bool:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        admin = prepare_root(root, exact_namespace=True)
        activation = admin / "activation"

        def signature() -> tuple[tuple[object, ...], ...]:
            paths = (
                activation,
                activation / "receipts",
                activation / "transactions",
            )
            return tuple(
                (
                    path.name,
                    os.lstat(path).st_dev,
                    os.lstat(path).st_ino,
                    stat.S_IMODE(os.lstat(path).st_mode),
                    os.lstat(path).st_mtime_ns,
                    os.lstat(path).st_ctime_ns,
                    tuple(sorted(child.name for child in path.iterdir())),
                )
                for path in paths
            )

        before = signature()
        original_names = journal._bounded_directory_names
        injected = False

        def replace_lock_after_flock(
            descriptor: int,
            limit: int,
        ) -> tuple[str, ...]:
            nonlocal injected
            if not injected:
                injected = True
                lifecycle = admin / "lifecycle.lock"
                lifecycle.unlink()
                lifecycle.write_bytes(b"")
                lifecycle.chmod(0o600)
            return original_names(descriptor, limit)

        journal._bounded_directory_names = replace_lock_after_flock
        try:
            expect_error(
                lambda: open_once(root),
                expected="activation_journal_recovery_required",
                label="post-flock lifecycle lock replacement",
                failures=failures,
            )
        finally:
            journal._bounded_directory_names = original_names
        after = signature()
        rejected = injected and before == after
        require(
            rejected,
            "post-flock lock replacement changed the activation namespace",
            failures,
        )
        return rejected


def missing_and_partial_cases(
    failures: list[str],
) -> tuple[bool, bool]:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        admin = prepare_root(root)
        expect_error(
            lambda: open_once(root),
            expected="activation_journal_recovery_required",
            label="missing activation namespace",
            failures=failures,
        )
        missing_zero_write = not (admin / "activation").exists()
        require(
            missing_zero_write,
            "opener initialized a missing activation namespace",
            failures,
        )

    partial_rejected = True
    for label, child_name in (
        ("empty activation", None),
        ("transactions-only activation", "transactions"),
    ):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            admin = prepare_root(root)
            activation = admin / "activation"
            activation.mkdir(mode=0o700)
            if child_name is not None:
                (activation / child_name).mkdir(mode=0o700)
            before_names = tuple(
                sorted(path.name for path in activation.iterdir())
            )
            expect_error(
                lambda: open_once(root),
                expected="activation_journal_recovery_required",
                label=label,
                failures=failures,
            )
            after_names = tuple(
                sorted(path.name for path in activation.iterdir())
            )
            unchanged = before_names == after_names
            partial_rejected = partial_rejected and unchanged
            require(
                unchanged,
                f"{label}: opener completed a partial namespace",
                failures,
            )
    return missing_zero_write, partial_rejected


def externally_held_lock_case(failures: list[str]) -> bool:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        admin = prepare_root(root)
        lock_fd = os.open(
            admin / "lifecycle.lock",
            os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
        )
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            expect_error(
                lambda: open_once(root),
                expected="activation_journal_busy",
                label="externally held lifecycle lock",
                failures=failures,
            )
            zero_write = not (admin / "activation").exists()
            require(
                zero_write,
                "busy opener wrote the activation namespace",
                failures,
            )
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)
        return zero_write


def invalid_topology_cases(failures: list[str]) -> int:
    def admin_mode(_root: Path, admin: Path) -> Path:
        admin.chmod(0o755)
        return _root

    def unsafe_parent(root: Path, admin: Path) -> Path:
        admin.parent.chmod(0o777)
        return root

    def nonempty_lock(root: Path, admin: Path) -> Path:
        (admin / "lifecycle.lock").write_bytes(b"x")
        return root

    def lock_mode(root: Path, admin: Path) -> Path:
        (admin / "lifecycle.lock").chmod(0o644)
        return root

    def lock_hardlink(root: Path, admin: Path) -> Path:
        os.link(
            admin / "lifecycle.lock",
            admin / "lifecycle.lock.peer",
        )
        return root

    def unknown_entry(root: Path, admin: Path) -> Path:
        (admin / "unexpected").write_bytes(b"x")
        return root

    def lock_symlink(root: Path, admin: Path) -> Path:
        (admin / "lifecycle.lock").unlink()
        (admin / "lifecycle.lock").symlink_to("missing")
        return root

    def lock_fifo(root: Path, admin: Path) -> Path:
        (admin / "lifecycle.lock").unlink()
        os.mkfifo(admin / "lifecycle.lock", mode=0o600)
        return root

    def lock_directory(root: Path, admin: Path) -> Path:
        (admin / "lifecycle.lock").unlink()
        (admin / "lifecycle.lock").mkdir(mode=0o700)
        return root

    cases = (
        ("admin-mode", admin_mode, "activation_journal_invalid"),
        ("unsafe-parent", unsafe_parent, "activation_journal_invalid"),
        ("nonempty-lock", nonempty_lock, "activation_journal_invalid"),
        ("lock-mode", lock_mode, "activation_journal_invalid"),
        ("lock-hardlink", lock_hardlink, "activation_journal_invalid"),
        (
            "unknown-admin-entry",
            unknown_entry,
            "activation_journal_recovery_required",
        ),
        ("lock-symlink", lock_symlink, "activation_journal_invalid"),
        ("lock-fifo", lock_fifo, "activation_journal_invalid"),
        ("lock-directory", lock_directory, "activation_journal_invalid"),
    )
    for label, mutate, expected in cases:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            admin = prepare_root(root)
            target = mutate(root, admin)
            expect_error(
                lambda target=target: open_once(target),
                expected=expected,
                label=label,
                failures=failures,
            )
            require(
                not (admin / "activation").exists(),
                f"{label}: invalid topology created activation state",
                failures,
            )

    with tempfile.TemporaryDirectory() as temporary:
        parent = Path(temporary)
        root = parent / "root"
        root.mkdir(mode=0o700)
        admin = prepare_root(root)
        linked_root = parent / "linked-root"
        linked_root.symlink_to(root, target_is_directory=True)
        expect_error(
            lambda: open_once(linked_root),
            expected="activation_journal_invalid",
            label="symlink root",
            failures=failures,
        )
        require(
            not (admin / "activation").exists(),
            "symlink root created activation state",
            failures,
        )

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        admin = prepare_root(root)
        moved = admin.with_name("agentops-relayctl-real")
        admin.rename(moved)
        admin.symlink_to(moved, target_is_directory=True)
        expect_error(
            lambda: open_once(root),
            expected="activation_journal_invalid",
            label="symlink admin",
            failures=failures,
        )
        require(
            not (moved / "activation").exists(),
            "symlink admin created activation state",
            failures,
        )
    return len(cases) + 2


def failure_cleanup_case(failures: list[str]) -> bool:
    for label, target_name in (
        ("activation open failure", "activation"),
        ("receipts open failure", "receipts"),
    ):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prepare_root(root, exact_namespace=True)
            original_open = journal._open_directory_at

            def fail_target(
                *args: object,
                **kwargs: object,
            ) -> int:
                if len(args) >= 2 and args[1] == target_name:
                    raise RelayActivationJournalError(
                        "activation_journal_write_failed"
                    )
                return original_open(*args, **kwargs)

            before = descriptor_count()
            journal._open_directory_at = fail_target
            try:
                expect_error(
                    lambda: open_once(root),
                    expected="activation_journal_write_failed",
                    label=label,
                    failures=failures,
                )
            finally:
                journal._open_directory_at = original_open
            after = descriptor_count()
            open_once(root)
            stable = before is None or after is None or before == after
            require(
                stable,
                f"{label}: opener leaked lock or descriptors",
                failures,
            )
    return True


def cli_lockout_case(failures: list[str]) -> bool:
    opener_calls = 0
    original_opener = journal._open_locked_production_store

    def blocked_opener(*_args: object, **_kwargs: object) -> object:
        nonlocal opener_calls
        opener_calls += 1
        raise AssertionError("confirmed CLI reached production opener")

    journal._open_locked_production_store = blocked_opener
    output = io.StringIO()
    try:
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(
            output
        ):
            code = relay_admin.main(
                ["--root", "/", "activate", "--confirm-activate"]
            )
    finally:
        journal._open_locked_production_store = original_opener
    try:
        payload = json.loads(output.getvalue())
    except json.JSONDecodeError:
        payload = {}
    locked_out = (
        code == 1
        and payload.get("error_id") == "activation_mutation_unavailable"
        and opener_calls == 0
    )
    require(
        locked_out,
        "confirmed CLI exposed the production journal opener",
        failures,
    )
    return locked_out


def main() -> int:
    failures: list[str] = []
    lock_held, fd_stable, exact_preserved = locked_lifecycle_case(
        failures
    )
    abandoned_released = abandoned_context_case(failures)
    activation_race, lock_race = binding_race_cases(failures)
    acquisition_lock_race = acquisition_lock_race_case(failures)
    missing_zero_write, partial_rejected = missing_and_partial_cases(
        failures
    )
    busy_zero_write = externally_held_lock_case(failures)
    invalid_cases = invalid_topology_cases(failures)
    failure_cleanup = failure_cleanup_case(failures)
    cli_locked_out = cli_lockout_case(failures)
    result = {
        "abandoned_context_released_lock": abandoned_released,
        "activation_path_race_rejected": activation_race,
        "acquisition_lock_race_rejected": acquisition_lock_race,
        "busy_opener_zero_write": busy_zero_write,
        "cli_mutation_exposed": not cli_locked_out,
        "descriptor_lifecycle_stable": fd_stable,
        "exact_namespace_preserved": exact_preserved,
        "failure_cleanup_released_lock": failure_cleanup,
        "failures": failures,
        "invalid_topology_cases": invalid_cases,
        "lifecycle_lock_held": lock_held,
        "lock_path_race_rejected": lock_race,
        "missing_namespace_zero_write": missing_zero_write,
        "ok": not failures,
        "operation": "relay_activation_production_store_smoke",
        "partial_namespace_rejected": partial_rejected,
    }
    print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
