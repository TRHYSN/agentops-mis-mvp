#!/usr/bin/env python3
"""Exercise private restart recovery primitives without launching a Host."""
from __future__ import annotations

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


ACTIVE_ORIGINAL = b"recovery-active-original\x00\n"
ACTIVE_TARGET = b"recovery-active-target\xff\n"
HOST_ORIGINAL = b"recovery-host-original\x00\n"
HOST_TARGET = b"recovery-host-target\xff\n"
CONTEXT_KEYS = {
    "action",
    "state",
    "transaction_sequence",
    "revision",
    "transition_ref",
}


def write_private(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    path.write_bytes(payload)
    path.chmod(0o600)


def expect_error(code: str, operation: Callable[[], object]) -> bool:
    try:
        operation()
    except relay_restart.RelayRestartError as exc:
        return exc.code == code and str(exc) == code
    return False


def transition(case: dict[str, object], state: str) -> dict[str, object]:
    current = relay_restart.restart_recovery_context(
        receipt_path=case["receipt"],
        sequence_path=case["sequence"],
    )
    return relay_restart.transition_restart_receipt(
        receipt_path=case["receipt"],
        sequence_path=case["sequence"],
        action=current["action"],
        transition_ref=current["transition_ref"],
        transaction_sequence=current["transaction_sequence"],
        expected_revision=current["revision"],
        state=state,
    )


def make_case(root: Path, name: str, state: str) -> dict[str, object]:
    private = root / name
    private.mkdir(mode=0o700)
    case: dict[str, object] = {
        "receipt": private / "receipt.json",
        "sequence": private / "sequence.json",
        "active": private / "active.json",
        "host": private / "host.json",
    }
    write_private(case["active"], ACTIVE_ORIGINAL)
    write_private(case["host"], HOST_ORIGINAL)
    created = relay_restart.create_restart_receipt(
        receipt_path=case["receipt"],
        sequence_path=case["sequence"],
        action="enable",
        transition_ref=f"recovery_{name}",
        active_config_path=case["active"],
        host_config_path=case["host"],
        active_original_config=ACTIVE_ORIGINAL,
        active_target_config=ACTIVE_TARGET,
        host_original_config=HOST_ORIGINAL,
        host_target_config=HOST_TARGET,
    )
    case["created"] = created
    if state == "config_applied":
        return case

    relay_restart.apply_target_configs(
        receipt_path=case["receipt"],
        sequence_path=case["sequence"],
        action="enable",
        transition_ref=f"recovery_{name}",
        transaction_sequence=created["transaction_sequence"],
        expected_revision=created["revision"],
    )
    paths = {
        "response_flushed": ("response_flushed",),
        "restart_requested": ("response_flushed", "restart_requested"),
        "validating_new_host": (
            "response_flushed",
            "restart_requested",
            "validating_new_host",
        ),
        "manual_restart_required": (
            "response_flushed",
            "manual_restart_required",
        ),
        "healthy": (
            "response_flushed",
            "restart_requested",
            "validating_new_host",
            "healthy",
        ),
        "restoring_config": ("response_flushed", "restoring_config"),
        "rolled_back": (
            "response_flushed",
            "restoring_config",
            "rolled_back",
        ),
        "rollback_failed": (
            "response_flushed",
            "restoring_config",
            "rollback_failed",
        ),
    }
    for next_state in paths[state]:
        transition(case, next_state)
    return case


def ensure(case: dict[str, object], *, use_target: bool) -> dict[str, object]:
    context = relay_restart.restart_recovery_context(
        receipt_path=case["receipt"],
        sequence_path=case["sequence"],
    )
    return relay_restart.ensure_restart_recovery_configs(
        receipt_path=case["receipt"],
        sequence_path=case["sequence"],
        action=context["action"],
        transition_ref=context["transition_ref"],
        transaction_sequence=context["transaction_sequence"],
        expected_revision=context["revision"],
        use_target=use_target,
    )


def is_private_file(path: Path) -> bool:
    metadata = path.lstat()
    return bool(
        not path.is_symlink()
        and stat.S_ISREG(metadata.st_mode)
        and metadata.st_uid == os.getuid()
        and stat.S_IMODE(metadata.st_mode) == 0o600
    )


def main() -> int:
    evidence: dict[str, bool] = {}
    contexts: list[dict[str, object]] = []
    projections: list[dict[str, object]] = []

    with tempfile.TemporaryDirectory(prefix="relay-restart-recovery-") as temporary:
        root = Path(temporary) / "private"
        root.mkdir(mode=0o700)

        target_states = (
            "response_flushed",
            "restart_requested",
            "validating_new_host",
            "manual_restart_required",
            "healthy",
        )
        target_cases: dict[str, dict[str, object]] = {}
        for state in target_states:
            case = make_case(root, f"target_{state}", state)
            target_cases[state] = case
            write_private(case["active"], ACTIVE_ORIGINAL)
            write_private(case["host"], HOST_ORIGINAL)
            context = relay_restart.restart_recovery_context(
                receipt_path=case["receipt"], sequence_path=case["sequence"]
            )
            contexts.append(context)
            first = ensure(case, use_target=True)
            second = ensure(case, use_target=True)
            projections.extend((first, second))
            evidence[f"target_{state}"] = bool(
                context["state"] == state
                and set(context) == CONTEXT_KEYS
                and first == second
                and first["state"] == state
                and first["revision"] == context["revision"]
                and case["active"].read_bytes() == ACTIVE_TARGET
                and case["host"].read_bytes() == HOST_TARGET
            )

        original_states = ("restoring_config", "rolled_back")
        original_cases: dict[str, dict[str, object]] = {}
        for state in original_states:
            case = make_case(root, f"original_{state}", state)
            original_cases[state] = case
            context = relay_restart.restart_recovery_context(
                receipt_path=case["receipt"], sequence_path=case["sequence"]
            )
            contexts.append(context)
            first = ensure(case, use_target=False)
            second = ensure(case, use_target=False)
            projections.extend((first, second))
            evidence[f"original_{state}"] = bool(
                context["state"] == state
                and first == second
                and first["state"] == state
                and first["revision"] == context["revision"]
                and case["active"].read_bytes() == ACTIVE_ORIGINAL
                and case["host"].read_bytes() == HOST_ORIGINAL
            )

        identity_case = target_cases["response_flushed"]
        identity = relay_restart.restart_recovery_context(
            receipt_path=identity_case["receipt"],
            sequence_path=identity_case["sequence"],
        )

        def identity_ensure(**overrides: object) -> object:
            arguments = {
                "receipt_path": identity_case["receipt"],
                "sequence_path": identity_case["sequence"],
                "action": identity["action"],
                "transition_ref": identity["transition_ref"],
                "transaction_sequence": identity["transaction_sequence"],
                "expected_revision": identity["revision"],
                "use_target": True,
            }
            arguments.update(overrides)
            return relay_restart.ensure_restart_recovery_configs(**arguments)

        evidence["stale_revision_rejected"] = expect_error(
            "stale_revision",
            lambda: identity_ensure(expected_revision=identity["revision"] + 1),
        )
        evidence["stale_sequence_rejected"] = expect_error(
            "stale_transaction_sequence",
            lambda: identity_ensure(
                transaction_sequence=identity["transaction_sequence"] + 1
            ),
        )
        evidence["wrong_ref_rejected"] = expect_error(
            "transition_ref_mismatch",
            lambda: identity_ensure(transition_ref="recovery_wrong_ref"),
        )
        evidence["wrong_action_rejected"] = expect_error(
            "invalid_action", lambda: identity_ensure(action="disable")
        )
        evidence["direction_mismatch_rejected"] = expect_error(
            "invalid_state", lambda: identity_ensure(use_target=False)
        ) and expect_error(
            "invalid_state", lambda: ensure(original_cases["rolled_back"], use_target=True)
        )
        evidence["non_boolean_direction_rejected"] = expect_error(
            "receipt_invalid", lambda: identity_ensure(use_target=1)
        )

        initial = make_case(root, "invalid_initial", "config_applied")
        failed = make_case(root, "invalid_failed", "rollback_failed")
        evidence["invalid_initial_state_rejected"] = expect_error(
            "invalid_state", lambda: ensure(initial, use_target=True)
        )
        evidence["rollback_failed_rejected"] = expect_error(
            "invalid_state", lambda: ensure(failed, use_target=False)
        )

        manual_validate = make_case(
            root, "manual_to_validation", "manual_restart_required"
        )
        manual_context = relay_restart.restart_recovery_context(
            receipt_path=manual_validate["receipt"],
            sequence_path=manual_validate["sequence"],
        )
        evidence["manual_finalize_blocked"] = expect_error(
            "terminal_required",
            lambda: relay_restart.finalize_restart_receipt(
                receipt_path=manual_validate["receipt"],
                sequence_path=manual_validate["sequence"],
                action=manual_context["action"],
                transition_ref=manual_context["transition_ref"],
                transaction_sequence=manual_context["transaction_sequence"],
                expected_revision=manual_context["revision"],
            ),
        )
        evidence["manual_replacement_blocked"] = expect_error(
            "receipt_active",
            lambda: relay_restart.create_restart_receipt(
                receipt_path=manual_validate["receipt"],
                sequence_path=manual_validate["sequence"],
                action="disable",
                transition_ref="recovery_manual_replacement",
                active_config_path=manual_validate["active"],
                host_config_path=manual_validate["host"],
                active_original_config=ACTIVE_TARGET,
                active_target_config=ACTIVE_ORIGINAL,
                host_original_config=HOST_TARGET,
                host_target_config=HOST_ORIGINAL,
                replace_terminal=True,
            ),
        )
        evidence["manual_to_validation_allowed"] = bool(
            transition(manual_validate, "validating_new_host")["state"]
            == "validating_new_host"
        )
        manual_restore = make_case(
            root, "manual_to_restore", "manual_restart_required"
        )
        evidence["manual_to_restore_allowed"] = bool(
            transition(manual_restore, "restoring_config")["state"]
            == "restoring_config"
        )

        healthy_restore = make_case(root, "healthy_to_restore", "healthy")
        evidence["healthy_to_restore_allowed"] = bool(
            transition(healthy_restore, "restoring_config")["state"]
            == "restoring_config"
            and ensure(healthy_restore, use_target=False)["state"]
            == "restoring_config"
            and healthy_restore["active"].read_bytes() == ACTIVE_ORIGINAL
            and healthy_restore["host"].read_bytes() == HOST_ORIGINAL
        )

        permission_case = make_case(root, "permission", "response_flushed")
        permission_case["receipt"].chmod(0o644)
        evidence["broad_file_mode_rejected"] = expect_error(
            "target_invalid",
            lambda: relay_restart.restart_recovery_context(
                receipt_path=permission_case["receipt"],
                sequence_path=permission_case["sequence"],
            ),
        )
        permission_case["receipt"].chmod(0o600)
        permission_case["receipt"].parent.chmod(0o755)
        evidence["broad_directory_mode_rejected"] = expect_error(
            "target_invalid",
            lambda: relay_restart.restart_recovery_context(
                receipt_path=permission_case["receipt"],
                sequence_path=permission_case["sequence"],
            ),
        )
        permission_case["receipt"].parent.chmod(0o700)
        private_paths = (
            permission_case["receipt"],
            permission_case["sequence"],
            permission_case["active"],
            permission_case["host"],
            permission_case["receipt"].parent / ".receipt.json.lock",
            permission_case["receipt"].parent / ".sequence.json.lock",
        )
        evidence["owner_only_files_preserved"] = all(
            is_private_file(path) for path in private_paths
        )

        public = relay_restart.public_restart_receipt(
            receipt_path=identity_case["receipt"],
            sequence_path=identity_case["sequence"],
        )
        rendered_contexts = json.dumps(contexts, sort_keys=True)
        rendered_projections = json.dumps([*projections, public], sort_keys=True)
        private_markers = (
            str(identity_case["active"]),
            str(identity_case["host"]),
            "recovery-active-original",
            "recovery-active-target",
            "recovery-host-original",
            "recovery-host-target",
        )
        evidence["recovery_context_exact_keys"] = all(
            set(context) == CONTEXT_KEYS for context in contexts
        )
        evidence["private_material_not_returned"] = bool(
            all(marker not in rendered_contexts for marker in private_markers)
            and all(marker not in rendered_projections for marker in private_markers)
            and "transition_ref" not in public
            and public.get("transition_ref_omitted") is True
        )

    print(json.dumps({"evidence": evidence, "ok": all(evidence.values())}, indent=2))
    return 0 if all(evidence.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
