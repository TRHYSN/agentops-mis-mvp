"""Run one exact-confirmed Relay activation under the journal lifecycle lock."""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Protocol

from agentops_mis_cli.relay_activation import (
    SHA256_PATTERN,
    ActivationPrerequisiteSnapshot,
    FileIdentity,
    SystemdSnapshot,
)
from agentops_mis_cli.relay_activation_evidence import (
    RelayActivationEvidenceError,
    build_activation_journal_identity,
    build_activation_step_observation,
)
from agentops_mis_cli.relay_activation_journal import (
    GENESIS_REVISION_SHA256,
    ActivationJournalIdentity,
    ActivationJournalRevision,
    RelayActivationJournalError,
    _open_locked_production_store,
    build_activation_receipt,
    build_activation_revision,
    parse_activation_receipt,
    parse_activation_revision,
)
from agentops_mis_cli.relay_activation_scan import (
    _scan_activation_prerequisites_while_locked,
)
from agentops_mis_cli.relay_systemd_mutation import (
    _run_bound_systemd_mutation,
)
from agentops_mis_cli.relay_systemd_read import read_systemd_show


ACTIVATION_CONTROLLER_SCHEMA = "agentops.relay.activation-controller.v0"
_INTENT_IDS = {
    "daemon_reload": "daemon_reload_requested",
    "enable": "enable_requested",
    "start": "start_requested",
    "verify": "verify_requested",
}


class RelayActivationControllerError(Exception):
    """One bounded failure identifier for the private controller."""

    def __init__(self, error_id: str) -> None:
        if error_id not in {
            "activation_controller_busy",
            "activation_controller_failed",
            "activation_confirmation_invalid",
            "activation_plan_stale",
            "activation_recovery_required",
        }:
            error_id = "activation_controller_failed"
        self.error_id = error_id
        super().__init__(error_id)


class _ActivationStore(Protocol):
    def inspect_store(self) -> dict[str, object]:
        ...

    def inspect_plan(self, plan_sha256: str) -> dict[str, object]:
        ...

    def publish_revision(self, raw: bytes) -> dict[str, object]:
        ...

    def publish_receipt(self, raw: bytes) -> dict[str, object]:
        ...


Scanner = Callable[[], ActivationPrerequisiteSnapshot]
SystemdReader = Callable[[ActivationPrerequisiteSnapshot], SystemdSnapshot]
MutationRunner = Callable[[FileIdentity, str], None]


class _ControllerInvalid(Exception):
    pass


def _stable_snapshot(
    *,
    scanner: Scanner,
    systemd_reader: SystemdReader,
) -> tuple[ActivationPrerequisiteSnapshot, SystemdSnapshot]:
    before = scanner()
    if not isinstance(before, ActivationPrerequisiteSnapshot):
        raise _ControllerInvalid
    systemd = systemd_reader(before)
    if not isinstance(systemd, SystemdSnapshot):
        raise _ControllerInvalid
    after = scanner()
    if (
        not isinstance(after, ActivationPrerequisiteSnapshot)
        or before != after
    ):
        raise _ControllerInvalid
    return after, systemd


def _build_revision(
    identity: ActivationJournalIdentity,
    previous: ActivationJournalRevision | None,
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
) -> tuple[bytes, ActivationJournalRevision]:
    raw = build_activation_revision(
        identity,
        revision=1 if previous is None else previous.revision + 1,
        previous_revision_sha256=(
            GENESIS_REVISION_SHA256
            if previous is None
            else previous.record_sha256
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
    return raw, parse_activation_revision(raw)


def _publish_revision(
    store: _ActivationStore,
    identity: ActivationJournalIdentity,
    previous: ActivationJournalRevision | None,
    **values: object,
) -> ActivationJournalRevision:
    raw, revision = _build_revision(
        identity,
        previous,
        **values,
    )
    store.publish_revision(raw)
    return revision


def _require_unchanged(
    expected_prerequisites: ActivationPrerequisiteSnapshot,
    expected_systemd: SystemdSnapshot,
    observed_prerequisites: ActivationPrerequisiteSnapshot,
    observed_systemd: SystemdSnapshot,
) -> None:
    if (
        observed_prerequisites != expected_prerequisites
        or observed_systemd != expected_systemd
    ):
        raise _ControllerInvalid


def _run_step(
    *,
    store: _ActivationStore,
    identity: ActivationJournalIdentity,
    previous: ActivationJournalRevision,
    step_id: str,
    expected_prerequisites: ActivationPrerequisiteSnapshot,
    expected_systemd: SystemdSnapshot,
    owns_enable: bool,
    owns_start: bool,
    scanner: Scanner,
    systemd_reader: SystemdReader,
    mutation_runner: MutationRunner,
) -> tuple[
    ActivationJournalRevision,
    ActivationPrerequisiteSnapshot,
    SystemdSnapshot,
    bool,
    bool,
]:
    if step_id not in _INTENT_IDS:
        raise _ControllerInvalid
    before_prerequisites, before_systemd = _stable_snapshot(
        scanner=scanner,
        systemd_reader=systemd_reader,
    )
    _require_unchanged(
        expected_prerequisites,
        expected_systemd,
        before_prerequisites,
        before_systemd,
    )
    current = _publish_revision(
        store,
        identity,
        previous,
        phase="intent",
        step_id=step_id,
        intent_id=_INTENT_IDS[step_id],
        owns_enable=owns_enable,
        owns_start=owns_start,
    )
    if step_id != "verify":
        mutation_runner(before_prerequisites.systemctl, step_id)
    after_prerequisites, after_systemd = _stable_snapshot(
        scanner=scanner,
        systemd_reader=systemd_reader,
    )
    observation = build_activation_step_observation(
        identity,
        step_id=step_id,
        prerequisites=after_prerequisites,
        systemd=after_systemd,
    )
    next_owns_enable = owns_enable or step_id == "enable"
    next_owns_start = owns_start or step_id == "start"
    current = _publish_revision(
        store,
        identity,
        current,
        phase="observed",
        step_id=step_id,
        intent_id=_INTENT_IDS[step_id],
        observation_id=observation.observation_id,
        observation_sha256=observation.observation_sha256,
        owns_enable=next_owns_enable,
        owns_start=next_owns_start,
    )
    return (
        current,
        after_prerequisites,
        after_systemd,
        next_owns_enable,
        next_owns_start,
    )


def _success_projection(
    result: dict[str, object],
    identity: ActivationJournalIdentity,
) -> dict[str, object]:
    if (
        result.get("ok") is not True
        or result.get("state") != "active"
        or result.get("recovery_required") is not False
        or not isinstance(result.get("receipt_sha256"), str)
        or not SHA256_PATTERN.fullmatch(
            str(result["receipt_sha256"])
        )
    ):
        raise _ControllerInvalid
    return {
        "ok": True,
        "operation_id": "activate",
        "plan_sha256": identity.plan_sha256,
        "receipt_sha256": result["receipt_sha256"],
        "recovery_required": False,
        "revision_count": result.get("revision_count"),
        "schema_id": ACTIVATION_CONTROLLER_SCHEMA,
        "state": "active",
    }


def _run_confirmed_activation_with(
    confirmed_plan_sha256: str,
    *,
    store: _ActivationStore,
    scanner: Scanner,
    systemd_reader: SystemdReader,
    mutation_runner: MutationRunner,
) -> dict[str, object]:
    """Execute only the success path; durable failures require recovery."""

    if (
        type(confirmed_plan_sha256) is not str
        or not SHA256_PATTERN.fullmatch(confirmed_plan_sha256)
    ):
        raise RelayActivationControllerError(
            "activation_confirmation_invalid"
        )
    durable_state_may_exist = False
    try:
        store_state = store.inspect_store()
        if (
            store_state.get("ok") is not True
            or store_state.get("state") != "ready"
            or store_state.get("recovery_required") is not False
        ):
            raise RelayActivationControllerError(
                "activation_recovery_required"
            )
        prerequisites, systemd = _stable_snapshot(
            scanner=scanner,
            systemd_reader=systemd_reader,
        )
        try:
            identity = build_activation_journal_identity(
                prerequisites,
                systemd,
                confirmed_plan_sha256=confirmed_plan_sha256,
            )
        except RelayActivationEvidenceError:
            raise RelayActivationControllerError(
                "activation_plan_stale"
            ) from None

        prepared_raw, current = _build_revision(
            identity,
            None,
            phase="prepared",
            step_id="transaction_open",
        )
        durable_state_may_exist = True
        store.publish_revision(prepared_raw)
        owns_enable = False
        owns_start = False
        steps = ["daemon_reload"]
        if identity.pre_unit_file_state == "disabled":
            steps.append("enable")
        if identity.pre_active_state == "inactive":
            steps.append("start")
        steps.append("verify")
        for step_id in steps:
            (
                current,
                prerequisites,
                systemd,
                owns_enable,
                owns_start,
            ) = _run_step(
                store=store,
                identity=identity,
                previous=current,
                step_id=step_id,
                expected_prerequisites=prerequisites,
                expected_systemd=systemd,
                owns_enable=owns_enable,
                owns_start=owns_start,
                scanner=scanner,
                systemd_reader=systemd_reader,
                mutation_runner=mutation_runner,
            )

        receipt_raw = build_activation_receipt(
            identity,
            terminal_revision=current.revision + 1,
            previous_revision_sha256=current.record_sha256,
            terminal_state="active",
            owns_enable=owns_enable,
            owns_start=owns_start,
            result_id="activation_succeeded",
        )
        receipt = parse_activation_receipt(receipt_raw)
        store.publish_receipt(receipt_raw)
        _publish_revision(
            store,
            identity,
            current,
            phase="terminal",
            step_id="terminal",
            owns_enable=owns_enable,
            owns_start=owns_start,
            terminal_state="active",
            receipt_sha256=receipt.receipt_sha256,
        )
        result = store.inspect_store()
        if (
            result.get("ok") is not True
            or result.get("state") != "ready"
        ):
            raise _ControllerInvalid
        return _success_projection(
            store.inspect_plan(identity.plan_sha256),
            identity,
        )
    except RelayActivationControllerError as exc:
        if durable_state_may_exist:
            raise RelayActivationControllerError(
                "activation_recovery_required"
            ) from None
        raise exc
    except Exception:
        raise RelayActivationControllerError(
            (
                "activation_recovery_required"
                if durable_state_may_exist
                else "activation_controller_failed"
            )
        ) from None


def _run_confirmed_activation(
    confirmed_plan_sha256: str,
) -> dict[str, object]:
    """Private production entrypoint; intentionally absent from the CLI."""

    try:
        with _open_locked_production_store(Path("/")) as store:
            capability = store._activation_scan_capability()
            return _run_confirmed_activation_with(
                confirmed_plan_sha256,
                store=store,
                scanner=lambda: (
                    _scan_activation_prerequisites_while_locked(
                        capability
                    )
                ),
                systemd_reader=read_systemd_show,
                mutation_runner=_run_bound_systemd_mutation,
            )
    except RelayActivationControllerError:
        raise
    except RelayActivationJournalError as exc:
        if exc.error_id == "activation_journal_busy":
            error_id = "activation_controller_busy"
        elif exc.error_id == "activation_journal_recovery_required":
            error_id = "activation_recovery_required"
        else:
            error_id = "activation_controller_failed"
        raise RelayActivationControllerError(error_id) from None
    except Exception:
        raise RelayActivationControllerError(
            "activation_controller_failed"
        ) from None
