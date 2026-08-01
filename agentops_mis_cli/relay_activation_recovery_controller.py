"""Exact-confirmed, non-mutating Relay activation recovery writes."""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

from agentops_mis_cli.relay_activation import (
    SHA256_PATTERN,
    ActivationPrerequisiteSnapshot,
    SystemdSnapshot,
)
from agentops_mis_cli.relay_activation_evidence import (
    RelayActivationEvidenceError,
    build_activation_rollback_verification_observation,
    build_activation_step_observation,
)
from agentops_mis_cli.relay_activation_journal import (
    ActivationJournalRecoverySnapshot,
    RelayActivationJournalError,
    _open_locked_production_store,
    build_activation_receipt,
    build_activation_revision,
    parse_activation_receipt,
    parse_activation_revision,
)
from agentops_mis_cli.relay_activation_recovery import (
    ActivationRecoveryDecision,
)
from agentops_mis_cli.relay_activation_recovery_preview import (
    RelayActivationRecoveryPreviewError,
    Scanner,
    SystemdReader,
    _observe_activation_recovery_with,
)
from agentops_mis_cli.relay_activation_scan import (
    _scan_activation_prerequisites_while_locked,
)
from agentops_mis_cli.relay_systemd_read import read_systemd_show


ACTIVATION_RECOVERY_CONTROLLER_SCHEMA = (
    "agentops.relay.activation-recovery-controller.v0"
)
_NO_MUTATION_OPERATIONS = frozenset(
    {
        "none",
        "publish_rollback_receipt",
        "publish_success_receipt",
        "publish_terminal_revision",
        "record_observation",
    }
)


class RelayActivationRecoveryControllerError(Exception):
    """One bounded failure for a private confirmed recovery write."""

    def __init__(self, error_id: str) -> None:
        if error_id not in {
            "activation_recovery_action_blocked",
            "activation_recovery_action_not_supported",
            "activation_recovery_confirmation_invalid",
            "activation_recovery_confirmation_stale",
            "activation_recovery_controller_busy",
            "activation_recovery_controller_failed",
            "activation_recovery_required",
        }:
            error_id = "activation_recovery_controller_failed"
        self.error_id = error_id
        super().__init__(error_id)


class _RecoveryStore(Protocol):
    def _load_recovery_snapshot(
        self,
        plan_sha256: str,
    ) -> ActivationJournalRecoverySnapshot:
        ...

    def publish_revision(self, raw: bytes) -> dict[str, object]:
        ...

    def publish_receipt(self, raw: bytes) -> dict[str, object]:
        ...


class _WriteTrackingStore:
    def __init__(self, store: _RecoveryStore) -> None:
        self.__store = store
        self.write_attempted = False

    def _load_recovery_snapshot(
        self,
        plan_sha256: str,
    ) -> ActivationJournalRecoverySnapshot:
        return self.__store._load_recovery_snapshot(plan_sha256)

    def publish_revision(self, raw: bytes) -> dict[str, object]:
        self.write_attempted = True
        return self.__store.publish_revision(raw)

    def publish_receipt(self, raw: bytes) -> dict[str, object]:
        self.write_attempted = True
        return self.__store.publish_receipt(raw)


def _result_projection(
    decision: ActivationRecoveryDecision,
    snapshot: ActivationJournalRecoverySnapshot,
    *,
    write_id: str,
) -> dict[str, object]:
    last = snapshot.revisions[-1]
    terminal = last.phase == "terminal"
    receipt_sha256 = (
        snapshot.receipt.receipt_sha256
        if snapshot.receipt is not None
        else None
    )
    if (
        decision.plan_sha256 != last.identity.plan_sha256
        or write_id
        not in {
            "none",
            "observed_revision",
            "rollback_receipt",
            "success_receipt",
            "terminal_revision",
        }
        or (
            terminal
            and (
                last.terminal_state
                not in {"active", "service_state_rolled_back"}
                or receipt_sha256 is None
            )
        )
    ):
        raise RelayActivationRecoveryControllerError(
            "activation_recovery_required"
        )
    return {
        "action_id": decision.action_id,
        "decision_sha256": decision.decision_sha256,
        "journal_head_sha256": last.record_sha256,
        "latest_revision": last.revision,
        "ok": True,
        "operation_id": "recover",
        "plan_sha256": decision.plan_sha256,
        "receipt_sha256": receipt_sha256,
        "recovery_required": not terminal,
        "requested_outcome": decision.requested_outcome,
        "schema_id": ACTIVATION_RECOVERY_CONTROLLER_SCHEMA,
        "state": last.terminal_state if terminal else "recovery_required",
        "step_id": decision.step_id,
        "write_id": write_id,
    }


def _load_after(
    store: _RecoveryStore,
    decision: ActivationRecoveryDecision,
) -> ActivationJournalRecoverySnapshot:
    snapshot = store._load_recovery_snapshot(decision.plan_sha256)
    if type(snapshot) is not ActivationJournalRecoverySnapshot:
        raise RelayActivationRecoveryControllerError(
            "activation_recovery_required"
        )
    return snapshot


def _publish_observation(
    store: _RecoveryStore,
    decision: ActivationRecoveryDecision,
    snapshot: ActivationJournalRecoverySnapshot,
    *,
    prerequisites: ActivationPrerequisiteSnapshot,
    systemd: SystemdSnapshot,
) -> ActivationJournalRecoverySnapshot:
    last = snapshot.revisions[-1]
    if (
        last.phase != "intent"
        or last.step_id != decision.step_id
        or last.step_id
        not in {
            "daemon_reload",
            "rollback_disable",
            "rollback_stop",
            "verify",
        }
        or last.intent_id is None
        or decision.observation_sha256 is None
    ):
        raise RelayActivationRecoveryControllerError(
            "activation_recovery_required"
        )
    rollback_verify = (
        last.step_id == "verify"
        and last.intent_id == "rollback_verify_requested"
        and any(
            revision.step_id in {"rollback_disable", "rollback_stop"}
            for revision in snapshot.revisions
        )
    )
    try:
        observation = (
            build_activation_rollback_verification_observation(
                last.identity,
                prerequisites=prerequisites,
                systemd=systemd,
            )
            if rollback_verify
            else build_activation_step_observation(
                last.identity,
                step_id=last.step_id,
                prerequisites=prerequisites,
                systemd=systemd,
            )
        )
    except RelayActivationEvidenceError:
        raise RelayActivationRecoveryControllerError(
            "activation_recovery_required"
        ) from None
    if observation.observation_sha256 != decision.observation_sha256:
        raise RelayActivationRecoveryControllerError(
            "activation_recovery_confirmation_stale"
        )
    owns_enable = last.owns_enable
    owns_start = last.owns_start
    if last.step_id == "rollback_stop":
        owns_start = False
    elif last.step_id == "rollback_disable":
        owns_enable = False
        owns_start = False
    raw = build_activation_revision(
        last.identity,
        revision=last.revision + 1,
        previous_revision_sha256=last.record_sha256,
        phase="observed",
        step_id=last.step_id,
        intent_id=last.intent_id,
        observation_id=observation.observation_id,
        observation_sha256=observation.observation_sha256,
        owns_enable=owns_enable,
        owns_start=owns_start,
    )
    expected = parse_activation_revision(raw)
    store.publish_revision(raw)
    after = _load_after(store, decision)
    if (
        len(after.revisions) != len(snapshot.revisions) + 1
        or after.revisions[-1] != expected
        or after.receipt is not None
    ):
        raise RelayActivationRecoveryControllerError(
            "activation_recovery_required"
        )
    return after


def _publish_success_receipt(
    store: _RecoveryStore,
    decision: ActivationRecoveryDecision,
    snapshot: ActivationJournalRecoverySnapshot,
) -> ActivationJournalRecoverySnapshot:
    last = snapshot.revisions[-1]
    if (
        last.phase != "observed"
        or last.step_id != "verify"
        or snapshot.receipt is not None
    ):
        raise RelayActivationRecoveryControllerError(
            "activation_recovery_required"
        )
    raw = build_activation_receipt(
        last.identity,
        terminal_revision=last.revision + 1,
        previous_revision_sha256=last.record_sha256,
        terminal_state="active",
        owns_enable=last.owns_enable,
        owns_start=last.owns_start,
        result_id="activation_succeeded",
    )
    expected = parse_activation_receipt(raw)
    store.publish_receipt(raw)
    after = _load_after(store, decision)
    if (
        after.revisions != snapshot.revisions
        or after.receipt != expected
    ):
        raise RelayActivationRecoveryControllerError(
            "activation_recovery_required"
        )
    return after


def _publish_rollback_receipt(
    store: _RecoveryStore,
    decision: ActivationRecoveryDecision,
    snapshot: ActivationJournalRecoverySnapshot,
) -> ActivationJournalRecoverySnapshot:
    last = snapshot.revisions[-1]
    if (
        last.phase != "observed"
        or last.step_id != "verify"
        or last.intent_id != "rollback_verify_requested"
        or last.observation_id != "rollback_verified"
        or last.owns_enable
        or last.owns_start
        or snapshot.receipt is not None
        or not any(
            revision.step_id in {"rollback_disable", "rollback_stop"}
            for revision in snapshot.revisions
        )
    ):
        raise RelayActivationRecoveryControllerError(
            "activation_recovery_required"
        )
    raw = build_activation_receipt(
        last.identity,
        terminal_revision=last.revision + 1,
        previous_revision_sha256=last.record_sha256,
        terminal_state="service_state_rolled_back",
        owns_enable=False,
        owns_start=False,
        result_id="rollback_succeeded",
    )
    expected = parse_activation_receipt(raw)
    store.publish_receipt(raw)
    after = _load_after(store, decision)
    if (
        after.revisions != snapshot.revisions
        or after.receipt != expected
    ):
        raise RelayActivationRecoveryControllerError(
            "activation_recovery_required"
        )
    return after


def _publish_terminal_revision(
    store: _RecoveryStore,
    decision: ActivationRecoveryDecision,
    snapshot: ActivationJournalRecoverySnapshot,
) -> ActivationJournalRecoverySnapshot:
    last = snapshot.revisions[-1]
    receipt = snapshot.receipt
    if last.phase == "terminal" or receipt is None:
        raise RelayActivationRecoveryControllerError(
            "activation_recovery_required"
        )
    raw = build_activation_revision(
        last.identity,
        revision=receipt.terminal_revision,
        previous_revision_sha256=receipt.previous_revision_sha256,
        phase="terminal",
        step_id="terminal",
        owns_enable=receipt.owns_enable,
        owns_start=receipt.owns_start,
        terminal_state=receipt.terminal_state,
        receipt_sha256=receipt.receipt_sha256,
    )
    expected = parse_activation_revision(raw)
    store.publish_revision(raw)
    after = _load_after(store, decision)
    if (
        len(after.revisions) != len(snapshot.revisions) + 1
        or after.revisions[-1] != expected
        or after.receipt != receipt
    ):
        raise RelayActivationRecoveryControllerError(
            "activation_recovery_required"
        )
    return after


def _run_confirmed_recovery_write_with(
    plan_sha256: str,
    requested_outcome: str,
    confirmed_decision_sha256: str,
    *,
    store: _RecoveryStore,
    scanner: Scanner,
    systemd_reader: SystemdReader,
) -> dict[str, object]:
    """Perform at most one confirmed non-systemd recovery write."""

    if (
        not isinstance(confirmed_decision_sha256, str)
        or not SHA256_PATTERN.fullmatch(confirmed_decision_sha256)
    ):
        raise RelayActivationRecoveryControllerError(
            "activation_recovery_confirmation_invalid"
        )
    tracked_store = _WriteTrackingStore(store)
    try:
        observation = _observe_activation_recovery_with(
            plan_sha256,
            requested_outcome,
            snapshot_loader=tracked_store._load_recovery_snapshot,
            scanner=scanner,
            systemd_reader=systemd_reader,
        )
        decision = observation.decision
        if decision.decision_sha256 != confirmed_decision_sha256:
            raise RelayActivationRecoveryControllerError(
                "activation_recovery_confirmation_stale"
            )
        if decision.action_id == "blocked":
            raise RelayActivationRecoveryControllerError(
                "activation_recovery_action_blocked"
            )
        if decision.operation_id not in _NO_MUTATION_OPERATIONS:
            raise RelayActivationRecoveryControllerError(
                "activation_recovery_action_not_supported"
            )
        expected_actions = {
            "none": {"complete"},
            "publish_rollback_receipt": {"inverse"},
            "publish_success_receipt": {"resume"},
            "publish_terminal_revision": {"terminalize"},
            "record_observation": {"inverse", "resume"},
        }[decision.operation_id]
        if decision.action_id not in expected_actions:
            raise RelayActivationRecoveryControllerError(
                "activation_recovery_required"
            )
        if decision.operation_id == "none":
            return _result_projection(
                decision,
                observation.snapshot,
                write_id="none",
            )

        if decision.operation_id == "record_observation":
            after = _publish_observation(
                tracked_store,
                decision,
                observation.snapshot,
                prerequisites=observation.prerequisites,
                systemd=observation.systemd,
            )
            write_id = "observed_revision"
        elif decision.operation_id == "publish_success_receipt":
            after = _publish_success_receipt(
                tracked_store,
                decision,
                observation.snapshot,
            )
            write_id = "success_receipt"
        elif decision.operation_id == "publish_rollback_receipt":
            after = _publish_rollback_receipt(
                tracked_store,
                decision,
                observation.snapshot,
            )
            write_id = "rollback_receipt"
        else:
            after = _publish_terminal_revision(
                tracked_store,
                decision,
                observation.snapshot,
            )
            write_id = "terminal_revision"
        return _result_projection(
            decision,
            after,
            write_id=write_id,
        )
    except RelayActivationRecoveryControllerError as exc:
        if (
            tracked_store.write_attempted
            and exc.error_id != "activation_recovery_required"
        ):
            raise RelayActivationRecoveryControllerError(
                "activation_recovery_required"
            ) from None
        raise exc
    except RelayActivationRecoveryPreviewError as exc:
        error_id = (
            "activation_recovery_confirmation_invalid"
            if (
                not tracked_store.write_attempted
                and exc.error_id
                == "activation_recovery_preview_invalid"
            )
            else "activation_recovery_required"
        )
        raise RelayActivationRecoveryControllerError(error_id) from None
    except RelayActivationJournalError:
        raise RelayActivationRecoveryControllerError(
            "activation_recovery_required"
        ) from None
    except Exception:
        raise RelayActivationRecoveryControllerError(
            (
                "activation_recovery_required"
                if tracked_store.write_attempted
                else "activation_recovery_controller_failed"
            )
        ) from None


def _run_confirmed_recovery_write(
    plan_sha256: str,
    requested_outcome: str,
    confirmed_decision_sha256: str,
) -> dict[str, object]:
    """Private production entrypoint; intentionally absent from the CLI."""

    try:
        with _open_locked_production_store(Path("/")) as store:
            capability = store._activation_scan_capability()
            return _run_confirmed_recovery_write_with(
                plan_sha256,
                requested_outcome,
                confirmed_decision_sha256,
                store=store,
                scanner=lambda: (
                    _scan_activation_prerequisites_while_locked(
                        capability
                    )
                ),
                systemd_reader=read_systemd_show,
            )
    except RelayActivationRecoveryControllerError:
        raise
    except RelayActivationJournalError as exc:
        error_id = (
            "activation_recovery_controller_busy"
            if exc.error_id == "activation_journal_busy"
            else "activation_recovery_required"
        )
        raise RelayActivationRecoveryControllerError(error_id) from None
    except Exception:
        raise RelayActivationRecoveryControllerError(
            "activation_recovery_controller_failed"
        ) from None
