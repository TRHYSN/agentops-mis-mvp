#!/usr/bin/env python3
"""Verify private Relay restart receipts without external access."""
from __future__ import annotations

import ast
import json
import os
import stat
import sys
import tempfile
from pathlib import Path
from typing import Callable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agentops_mis_cli import relay_restart  # noqa: E402


ACTIVE_ORIGINAL_1 = b'active-original-1\x00{"enabled":false}\n'
ACTIVE_TARGET_1 = b'active-target-1\xff{"enabled":true}\n'
HOST_ORIGINAL_1 = b'host-original-1\x00{"publication":"disabled"}\n'
HOST_TARGET_1 = b'host-target-1\xff{"publication":"relay"}\n'
REF_1 = "rct_restart_smoke_1"
REF_2 = "rct_restart_smoke_2"
REF_3 = "rct_restart_smoke_3"
REF_4 = "rct_restart_smoke_4"

PUBLIC_KEYS = {
    "action",
    "digests_omitted",
    "manual_restart_required",
    "original_configs_omitted",
    "private_paths_omitted",
    "restart_requested",
    "restart_required",
    "revision",
    "state",
    "target_configs_omitted",
    "transaction_sequence",
    "transition_ref_omitted",
}


def expect_error(code: str, operation: Callable[[], object]) -> bool:
    try:
        operation()
    except relay_restart.RelayRestartError as exc:
        return exc.code == code and str(exc) == code
    return False


def write_private(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    path.write_bytes(payload)
    path.chmod(0o600)


def private_file(path: Path) -> bool:
    metadata = path.lstat()
    return bool(
        not path.is_symlink()
        and stat.S_ISREG(metadata.st_mode)
        and metadata.st_uid == os.getuid()
        and stat.S_IMODE(metadata.st_mode) == 0o600
    )


def create_receipt(
    *,
    receipt: Path,
    sequence: Path,
    active: Path,
    host: Path,
    transition_ref: str,
    action: str,
    active_original: bytes,
    active_target: bytes,
    host_original: bytes,
    host_target: bytes,
    replace_terminal: bool = False,
) -> dict[str, object]:
    return relay_restart.create_restart_receipt(
        receipt_path=receipt,
        sequence_path=sequence,
        action=action,
        transition_ref=transition_ref,
        active_config_path=active,
        host_config_path=host,
        active_original_config=active_original,
        active_target_config=active_target,
        host_original_config=host_original,
        host_target_config=host_target,
        replace_terminal=replace_terminal,
    )


def transition(
    *,
    receipt: Path,
    sequence: Path,
    action: str,
    transition_ref: str,
    transaction_sequence: int,
    revision: int,
    state: str,
) -> dict[str, object]:
    return relay_restart.transition_restart_receipt(
        receipt_path=receipt,
        sequence_path=sequence,
        action=action,
        transition_ref=transition_ref,
        transaction_sequence=transaction_sequence,
        expected_revision=revision,
        state=state,
    )


def advance_to_validation(
    *,
    receipt: Path,
    sequence: Path,
    action: str,
    transition_ref: str,
    transaction_sequence: int,
) -> list[dict[str, object]]:
    payloads: list[dict[str, object]] = []
    for revision, state in (
        (1, "response_flushed"),
        (2, "restart_requested"),
        (3, "validating_new_host"),
    ):
        payloads.append(
            transition(
                receipt=receipt,
                sequence=sequence,
                action=action,
                transition_ref=transition_ref,
                transaction_sequence=transaction_sequence,
                revision=revision,
                state=state,
            )
        )
    return payloads


def apply_targets(
    *,
    receipt: Path,
    sequence: Path,
    action: str,
    transition_ref: str,
    transaction_sequence: int,
    revision: int,
) -> dict[str, object]:
    return relay_restart.apply_target_configs(
        receipt_path=receipt,
        sequence_path=sequence,
        action=action,
        transition_ref=transition_ref,
        transaction_sequence=transaction_sequence,
        expected_revision=revision,
    )


def restore_originals(
    *,
    receipt: Path,
    sequence: Path,
    action: str,
    transition_ref: str,
    transaction_sequence: int,
    revision: int,
) -> dict[str, object]:
    return relay_restart.restore_original_configs(
        receipt_path=receipt,
        sequence_path=sequence,
        action=action,
        transition_ref=transition_ref,
        transaction_sequence=transaction_sequence,
        expected_revision=revision,
    )


def inject_second_config_write_failure(
    *, operation: Callable[[], object], active: Path, host: Path
) -> str:
    real_write = relay_restart._atomic_private_write
    config_writes = 0
    targets = {Path(os.path.abspath(active)), Path(os.path.abspath(host))}

    def fail_second(path: Path, payload: bytes, *, allow_create: bool) -> None:
        nonlocal config_writes
        if Path(os.path.abspath(path)) in targets:
            config_writes += 1
            if config_writes == 2:
                raise relay_restart.RelayRestartError("write_failed")
        real_write(path, payload, allow_create=allow_create)

    relay_restart._atomic_private_write = fail_second
    try:
        operation()
    except relay_restart.RelayRestartError as exc:
        return exc.code
    finally:
        relay_restart._atomic_private_write = real_write
    return "missing_error"


def source_has_no_external_access() -> bool:
    source_path = ROOT / "agentops_mis_cli" / "relay_restart.py"
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden_imports = {
        "asyncio",
        "http",
        "requests",
        "socket",
        "sqlite3",
        "subprocess",
        "tailscale",
        "urllib",
    }
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".", 1)[0])
    forbidden_calls = ("os.popen(", "os.system(", "subprocess.")
    return not (imported & forbidden_imports) and not any(
        marker in source for marker in forbidden_calls
    )


def main() -> int:
    evidence: dict[str, bool] = {}
    public_payloads: list[dict[str, object]] = []

    with tempfile.TemporaryDirectory(prefix="relay-restart-receipt-") as temporary:
        root = Path(temporary)
        private_root = root / "private"
        private_root.mkdir(mode=0o700)
        receipt = private_root / "restart.json"
        sequence = private_root / "restart-sequence.json"
        active = private_root / "active.json"
        host = private_root / "host.json"
        write_private(active, ACTIVE_ORIGINAL_1)
        write_private(host, HOST_ORIGINAL_1)

        first = create_receipt(
            receipt=receipt,
            sequence=sequence,
            active=active,
            host=host,
            transition_ref=REF_1,
            action="enable",
            active_original=ACTIVE_ORIGINAL_1,
            active_target=ACTIVE_TARGET_1,
            host_original=HOST_ORIGINAL_1,
            host_target=HOST_TARGET_1,
        )
        first_sequence = int(first["transaction_sequence"])
        public_payloads.append(first)
        first_retry = create_receipt(
            receipt=receipt,
            sequence=sequence,
            active=active,
            host=host,
            transition_ref=REF_1,
            action="enable",
            active_original=ACTIVE_ORIGINAL_1,
            active_target=ACTIVE_TARGET_1,
            host_original=HOST_ORIGINAL_1,
            host_target=HOST_TARGET_1,
        )
        public_payloads.append(first_retry)
        evidence["initial_create_idempotent"] = first_retry == first

        apply_failure = inject_second_config_write_failure(
            operation=lambda: apply_targets(
                receipt=receipt,
                sequence=sequence,
                action="enable",
                transition_ref=REF_1,
                transaction_sequence=first_sequence,
                revision=1,
            ),
            active=active,
            host=host,
        )
        evidence["apply_second_write_rolled_back_both"] = bool(
            apply_failure == "config_pair_write_failed"
            and active.read_bytes() == ACTIVE_ORIGINAL_1
            and host.read_bytes() == HOST_ORIGINAL_1
        )
        applied = apply_targets(
            receipt=receipt,
            sequence=sequence,
            action="enable",
            transition_ref=REF_1,
            transaction_sequence=first_sequence,
            revision=1,
        )
        applied_again = apply_targets(
            receipt=receipt,
            sequence=sequence,
            action="enable",
            transition_ref=REF_1,
            transaction_sequence=first_sequence,
            revision=1,
        )
        public_payloads.extend((applied, applied_again))
        evidence["pair_apply_exact_and_idempotent"] = bool(
            applied == applied_again
            and active.read_bytes() == ACTIVE_TARGET_1
            and host.read_bytes() == HOST_TARGET_1
        )
        evidence["transition_ref_required"] = expect_error(
            "transition_ref_mismatch",
            lambda: transition(
                receipt=receipt,
                sequence=sequence,
                action="enable",
                transition_ref="rct_wrong_ref",
                transaction_sequence=first_sequence,
                revision=1,
                state="response_flushed",
            ),
        )
        evidence["unsafe_transition_ref_rejected"] = expect_error(
            "invalid_transition_ref",
            lambda: create_receipt(
                receipt=private_root / "unsafe-ref.json",
                sequence=sequence,
                active=active,
                host=host,
                transition_ref="../unsafe ref",
                action="enable",
                active_original=ACTIVE_ORIGINAL_1,
                active_target=ACTIVE_TARGET_1,
                host_original=HOST_ORIGINAL_1,
                host_target=HOST_TARGET_1,
            ),
        )
        evidence["state_jump_rejected"] = expect_error(
            "invalid_state_transition",
            lambda: transition(
                receipt=receipt,
                sequence=sequence,
                action="enable",
                transition_ref=REF_1,
                transaction_sequence=first_sequence,
                revision=1,
                state="restart_requested",
            ),
        )

        response = transition(
            receipt=receipt,
            sequence=sequence,
            action="enable",
            transition_ref=REF_1,
            transaction_sequence=first_sequence,
            revision=1,
            state="response_flushed",
        )
        response_retry = transition(
            receipt=receipt,
            sequence=sequence,
            action="enable",
            transition_ref=REF_1,
            transaction_sequence=first_sequence,
            revision=2,
            state="response_flushed",
        )
        public_payloads.extend((response, response_retry))
        evidence["state_transition_idempotent"] = response_retry == response
        evidence["stale_revision_rejected"] = expect_error(
            "stale_revision",
            lambda: transition(
                receipt=receipt,
                sequence=sequence,
                action="enable",
                transition_ref=REF_1,
                transaction_sequence=first_sequence,
                revision=1,
                state="restart_requested",
            ),
        )

        sequence_before_block = first_sequence
        evidence["nonterminal_replacement_blocked"] = expect_error(
            "receipt_active",
            lambda: create_receipt(
                receipt=receipt,
                sequence=sequence,
                active=active,
                host=host,
                transition_ref=REF_2,
                action="disable",
                active_original=ACTIVE_TARGET_1,
                active_target=b"active-target-2\n",
                host_original=HOST_TARGET_1,
                host_target=b"host-target-2\n",
                replace_terminal=True,
            ),
        )
        evidence["nonterminal_finalize_blocked"] = expect_error(
            "terminal_required",
            lambda: relay_restart.finalize_restart_receipt(
                receipt_path=receipt,
                sequence_path=sequence,
                action="enable",
                transition_ref=REF_1,
                transaction_sequence=first_sequence,
                expected_revision=2,
            ),
        )
        first_restart = transition(
            receipt=receipt,
            sequence=sequence,
            action="enable",
            transition_ref=REF_1,
            transaction_sequence=first_sequence,
            revision=2,
            state="restart_requested",
        )
        first_validating = transition(
            receipt=receipt,
            sequence=sequence,
            action="enable",
            transition_ref=REF_1,
            transaction_sequence=first_sequence,
            revision=3,
            state="validating_new_host",
        )
        healthy = transition(
            receipt=receipt,
            sequence=sequence,
            action="enable",
            transition_ref=REF_1,
            transaction_sequence=first_sequence,
            revision=4,
            state="healthy",
        )
        public_payloads.extend((first_restart, first_validating, healthy))

        active_target_2 = b"active-target-2\x00\n"
        host_target_2 = b"host-target-2\xff\n"
        second = create_receipt(
            receipt=receipt,
            sequence=sequence,
            active=active,
            host=host,
            transition_ref=REF_2,
            action="disable",
            active_original=ACTIVE_TARGET_1,
            active_target=active_target_2,
            host_original=HOST_TARGET_1,
            host_target=host_target_2,
            replace_terminal=True,
        )
        second_sequence = int(second["transaction_sequence"])
        public_payloads.append(second)
        evidence["terminal_replacement_allowed"] = bool(
            second_sequence > sequence_before_block
            and second["revision"] == 1
            and second["state"] == "config_applied"
        )
        evidence["stale_first_transaction_rejected"] = expect_error(
            "stale_transaction_sequence",
            lambda: transition(
                receipt=receipt,
                sequence=sequence,
                action="enable",
                transition_ref=REF_1,
                transaction_sequence=first_sequence,
                revision=5,
                state="healthy",
            ),
        )
        applied_second = apply_targets(
            receipt=receipt,
            sequence=sequence,
            action="disable",
            transition_ref=REF_2,
            transaction_sequence=second_sequence,
            revision=1,
        )
        second_response = transition(
            receipt=receipt,
            sequence=sequence,
            action="disable",
            transition_ref=REF_2,
            transaction_sequence=second_sequence,
            revision=1,
            state="response_flushed",
        )
        manual = transition(
            receipt=receipt,
            sequence=sequence,
            action="disable",
            transition_ref=REF_2,
            transaction_sequence=second_sequence,
            revision=2,
            state="manual_restart_required",
        )
        public_payloads.extend((applied_second, second_response, manual))
        evidence["manual_finalize_blocked"] = expect_error(
            "terminal_required",
            lambda: relay_restart.finalize_restart_receipt(
                receipt_path=receipt,
                sequence_path=sequence,
                action="disable",
                transition_ref=REF_2,
                transaction_sequence=second_sequence,
                expected_revision=3,
            ),
        )
        validating_manual = transition(
            receipt=receipt,
            sequence=sequence,
            action="disable",
            transition_ref=REF_2,
            transaction_sequence=second_sequence,
            revision=3,
            state="validating_new_host",
        )
        healthy_manual = transition(
            receipt=receipt,
            sequence=sequence,
            action="disable",
            transition_ref=REF_2,
            transaction_sequence=second_sequence,
            revision=4,
            state="healthy",
        )
        public_payloads.extend((validating_manual, healthy_manual))
        archive = private_root / "archive" / "transaction-2.json"
        finalized = relay_restart.finalize_restart_receipt(
            receipt_path=receipt,
            sequence_path=sequence,
            action="disable",
            transition_ref=REF_2,
            transaction_sequence=second_sequence,
            expected_revision=5,
            archive_path=archive,
        )
        public_payloads.append(finalized)
        evidence["terminal_archive_finalize_allowed"] = bool(
            not receipt.exists()
            and private_file(archive)
            and private_file(sequence)
            and stat.S_IMODE(archive.parent.stat().st_mode) == 0o700
        )

        active_target_3 = b"active-target-3\x00\xff\n"
        host_target_3 = b"host-target-3\xff\x00\n"
        third = create_receipt(
            receipt=receipt,
            sequence=sequence,
            active=active,
            host=host,
            transition_ref=REF_3,
            action="enable",
            active_original=active_target_2,
            active_target=active_target_3,
            host_original=host_target_2,
            host_target=host_target_3,
        )
        third_sequence = int(third["transaction_sequence"])
        public_payloads.append(third)
        evidence["sequence_survives_finalize"] = bool(
            third_sequence > second_sequence > first_sequence
            and third["revision"] == 1
        )
        applied_third = apply_targets(
            receipt=receipt,
            sequence=sequence,
            action="enable",
            transition_ref=REF_3,
            transaction_sequence=third_sequence,
            revision=1,
        )
        third_path = advance_to_validation(
            receipt=receipt,
            sequence=sequence,
            action="enable",
            transition_ref=REF_3,
            transaction_sequence=third_sequence,
        )
        restoring = transition(
            receipt=receipt,
            sequence=sequence,
            action="enable",
            transition_ref=REF_3,
            transaction_sequence=third_sequence,
            revision=4,
            state="restoring_config",
        )
        public_payloads.extend((applied_third, *third_path, restoring))
        evidence["restore_ref_required"] = expect_error(
            "transition_ref_mismatch",
            lambda: restore_originals(
                receipt=receipt,
                sequence=sequence,
                action="enable",
                transition_ref="rct_wrong_restore",
                transaction_sequence=third_sequence,
                revision=5,
            ),
        )
        restore_failure = inject_second_config_write_failure(
            operation=lambda: restore_originals(
                receipt=receipt,
                sequence=sequence,
                action="enable",
                transition_ref=REF_3,
                transaction_sequence=third_sequence,
                revision=5,
            ),
            active=active,
            host=host,
        )
        evidence["restore_second_write_reapplied_both_targets"] = bool(
            restore_failure == "config_pair_write_failed"
            and active.read_bytes() == active_target_3
            and host.read_bytes() == host_target_3
        )
        restored = restore_originals(
            receipt=receipt,
            sequence=sequence,
            action="enable",
            transition_ref=REF_3,
            transaction_sequence=third_sequence,
            revision=5,
        )
        restored_again = restore_originals(
            receipt=receipt,
            sequence=sequence,
            action="enable",
            transition_ref=REF_3,
            transaction_sequence=third_sequence,
            revision=5,
        )
        rolled_back = transition(
            receipt=receipt,
            sequence=sequence,
            action="enable",
            transition_ref=REF_3,
            transaction_sequence=third_sequence,
            revision=5,
            state="rolled_back",
        )
        public_payloads.extend((restored, restored_again, rolled_back))
        evidence["pair_restore_exact_and_idempotent"] = bool(
            restored == restored_again
            and active.read_bytes() == active_target_2
            and host.read_bytes() == host_target_2
        )

        fourth = create_receipt(
            receipt=receipt,
            sequence=sequence,
            active=active,
            host=host,
            transition_ref=REF_4,
            action="disable",
            active_original=active_target_2,
            active_target=b"active-target-4\n",
            host_original=host_target_2,
            host_target=b"host-target-4\n",
            replace_terminal=True,
        )
        fourth_sequence = int(fourth["transaction_sequence"])
        fourth_path = advance_to_validation(
            receipt=receipt,
            sequence=sequence,
            action="disable",
            transition_ref=REF_4,
            transaction_sequence=fourth_sequence,
        )
        fourth_restoring = transition(
            receipt=receipt,
            sequence=sequence,
            action="disable",
            transition_ref=REF_4,
            transaction_sequence=fourth_sequence,
            revision=4,
            state="restoring_config",
        )
        rollback_failed = transition(
            receipt=receipt,
            sequence=sequence,
            action="disable",
            transition_ref=REF_4,
            transaction_sequence=fourth_sequence,
            revision=5,
            state="rollback_failed",
        )
        public_payloads.extend(
            (fourth, *fourth_path, fourth_restoring, rollback_failed)
        )
        evidence["all_states_covered"] = {
            str(payload["state"]) for payload in public_payloads
        } == set(relay_restart.STATES)

        receipt_lock = private_root / ".restart.json.lock"
        sequence_lock = private_root / ".restart-sequence.json.lock"
        evidence["owner_only_regular_files"] = bool(
            stat.S_IMODE(private_root.stat().st_mode) == 0o700
            and all(
                private_file(path)
                for path in (
                    receipt,
                    sequence,
                    receipt_lock,
                    sequence_lock,
                    active,
                    host,
                    archive,
                )
            )
        )

        bad_mode = private_root / "bad-mode.json"
        bad_mode.write_bytes(receipt.read_bytes())
        bad_mode.chmod(0o644)
        evidence["file_mode_rejected"] = expect_error(
            "target_invalid",
            lambda: relay_restart.public_restart_receipt(
                receipt_path=bad_mode, sequence_path=sequence
            ),
        )
        symlink_receipt = private_root / "symlink.json"
        symlink_receipt.symlink_to(receipt)
        evidence["receipt_symlink_rejected"] = expect_error(
            "target_invalid",
            lambda: relay_restart.public_restart_receipt(
                receipt_path=symlink_receipt, sequence_path=sequence
            ),
        )
        broad_parent = root / "broad"
        broad_parent.mkdir(mode=0o755)
        evidence["parent_mode_rejected"] = expect_error(
            "target_invalid",
            lambda: create_receipt(
                receipt=broad_parent / "receipt.json",
                sequence=sequence,
                active=active,
                host=host,
                transition_ref="rct_broad_parent",
                action="enable",
                active_original=b"a",
                active_target=b"b",
                host_original=b"c",
                host_target=b"d",
            ),
        )
        evidence["config_size_bound_enforced"] = expect_error(
            "config_too_large",
            lambda: create_receipt(
                receipt=private_root / "oversized-config.json",
                sequence=sequence,
                active=active,
                host=host,
                transition_ref="rct_oversized_config",
                action="enable",
                active_original=b"x" * (relay_restart.MAX_CONFIG_BYTES + 1),
                active_target=b"b",
                host_original=b"c",
                host_target=b"d",
            ),
        )
        oversized_receipt = private_root / "oversized-receipt.json"
        write_private(
            oversized_receipt, b"x" * (relay_restart.MAX_RECEIPT_BYTES + 1)
        )
        evidence["receipt_size_bound_enforced"] = expect_error(
            "receipt_invalid",
            lambda: relay_restart.public_restart_receipt(
                receipt_path=oversized_receipt, sequence_path=sequence
            ),
        )

        rendered_public = json.dumps(public_payloads, sort_keys=True)
        private_fragments = (
            REF_1,
            REF_2,
            REF_3,
            REF_4,
            "active-original",
            "active-target",
            "host-original",
            "host-target",
            str(private_root),
            "_b64",
            "config_path",
            "transition_ref\"",
            "digest\"",
        )
        evidence["public_projection_redacted"] = bool(
            all(set(payload) == PUBLIC_KEYS for payload in public_payloads)
            and all(fragment not in rendered_public for fragment in private_fragments)
            and all(
                payload["original_configs_omitted"] is True
                and payload["target_configs_omitted"] is True
                and payload["transition_ref_omitted"] is True
                and payload["private_paths_omitted"] is True
                and payload["digests_omitted"] is True
                for payload in public_payloads
            )
        )

    evidence["no_external_access"] = source_has_no_external_access()
    failures = sorted(name for name, passed in evidence.items() if not passed)
    print(
        json.dumps(
            {
                "ok": not failures,
                "operation": "private_host_relay_restart_receipt_smoke",
                "evidence": evidence,
                "failure_codes": failures,
                "configs_refs_paths_digests_omitted": True,
                "network_used": False,
                "subprocess_used": False,
                "database_used": False,
                "tailscale_used": False,
                "worker_used": False,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
