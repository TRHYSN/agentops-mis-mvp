"""Execute one exact-confirmed Relay activation recovery step."""
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
    build_activation_rollback_verification_observation,
    build_activation_step_observation,
)
from agentops_mis_cli.relay_activation_journal import (
    ActivationJournalRecoverySnapshot,
    ActivationJournalRevision,
    RelayActivationJournalError,
    _open_locked_production_store,
    build_activation_revision,
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
from agentops_mis_cli.relay_systemd_mutation import (
    _run_bound_systemd_mutation,
)
from agentops_mis_cli.relay_systemd_read import read_systemd_show


ACTIVATION_RECOVERY_EXECUTOR_SCHEMA = (
    "agentops.relay.activation-recovery-executor.v0"
)
_INTENT_IDS = {
    "daemon_reload": "daemon_reload_requested",
    "enable": "enable_requested",
    "rollback_disable": "rollback_disable_requested",
    "rollback_stop": "rollback_stop_requested",
    "start": "start_requested",
    "verify": "verify_requested",
}
_MUTATION_IDS = {
    "daemon_reload": "daemon_reload",
    "enable": "enable",
    "rollback_disable": "disable",
    "rollback_stop": "stop",
    "start": "start",
}
_ROLLBACK_STEPS = frozenset({"rollback_disable", "rollback_stop"})
_OUTCOMES = frozenset({"resume", "rollback"})


class RelayActivationRecoveryExecutorError(Exception):
    """One bounded failure for a confirmed one-step recovery execution."""

    def __init__(self, error_id: str) -> None:
        if error_id not in {
            "activation_recovery_action_blocked",
            "activation_recovery_action_not_supported",
            "activation_recovery_confirmation_invalid",
            "activation_recovery_confirmation_stale",
            "activation_recovery_executor_busy",
            "activation_recovery_executor_failed",
            "activation_recovery_required",
        }:
            error_id = "activation_recovery_executor_failed"
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


MutationRunner = Callable[[FileIdentity, str], None]


class _ExecutionState:
    def __init__(
        self,
        store: _RecoveryStore,
        mutation_runner: MutationRunner,
    ) -> None:
        self.__store = store
        self.__mutation_runner = mutation_runner
        self.write_attempted = False
        self.mutation_attempted = False

    def _load_recovery_snapshot(
        self,
        plan_sha256: str,
    ) -> ActivationJournalRecoverySnapshot:
        return self.__store._load_recovery_snapshot(plan_sha256)

    def publish_revision(self, raw: bytes) -> dict[str, object]:
        self.write_attempted = True
        return self.__store.publish_revision(raw)

    def mutate(self, identity: FileIdentity, operation: str) -> None:
        self.mutation_attempted = True
        self.__mutation_runner(identity, operation)


def _stable_snapshot(
    scanner: Scanner,
    systemd_reader: SystemdReader,
) -> tuple[ActivationPrerequisiteSnapshot, SystemdSnapshot]:
    before = scanner()
    if not isinstance(before, ActivationPrerequisiteSnapshot):
        raise RelayActivationRecoveryExecutorError(
            "activation_recovery_required"
        )
    systemd = systemd_reader(before)
    if not isinstance(systemd, SystemdSnapshot):
        raise RelayActivationRecoveryExecutorError(
            "activation_recovery_required"
        )
    after = scanner()
    if (
        not isinstance(after, ActivationPrerequisiteSnapshot)
        or after != before
    ):
        raise RelayActivationRecoveryExecutorError(
            "activation_recovery_required"
        )
    return after, systemd


def _load_after(
    store: _RecoveryStore,
    decision: ActivationRecoveryDecision,
) -> ActivationJournalRecoverySnapshot:
    snapshot = store._load_recovery_snapshot(decision.plan_sha256)
    if type(snapshot) is not ActivationJournalRecoverySnapshot:
        raise RelayActivationRecoveryExecutorError(
            "activation_recovery_required"
        )
    return snapshot


def _publish_intent(
    store: _RecoveryStore,
    decision: ActivationRecoveryDecision,
    snapshot: ActivationJournalRecoverySnapshot,
) -> tuple[
    ActivationJournalRecoverySnapshot,
    ActivationJournalRevision,
    bool,
]:
    last = snapshot.revisions[-1]
    step_id = decision.step_id
    if step_id is None or step_id not in _INTENT_IDS:
        raise RelayActivationRecoveryExecutorError(
            "activation_recovery_required"
        )
    rollback_verify = (
        step_id == "verify"
        and any(
            revision.step_id in _ROLLBACK_STEPS
            for revision in snapshot.revisions
        )
    )
    intent_id = (
        "rollback_verify_requested"
        if rollback_verify
        else _INTENT_IDS[step_id]
    )
    if last.phase == "intent":
        if (
            last.step_id != step_id
            or last.intent_id != intent_id
            or snapshot.receipt is not None
        ):
            raise RelayActivationRecoveryExecutorError(
                "activation_recovery_required"
            )
        return snapshot, last, True
    if last.phase not in {"observed", "prepared"}:
        raise RelayActivationRecoveryExecutorError(
            "activation_recovery_required"
        )
    raw = build_activation_revision(
        last.identity,
        revision=last.revision + 1,
        previous_revision_sha256=last.record_sha256,
        phase="intent",
        step_id=step_id,
        intent_id=intent_id,
        owns_enable=last.owns_enable,
        owns_start=last.owns_start,
    )
    expected = parse_activation_revision(raw)
    store.publish_revision(raw)
    after = _load_after(store, decision)
    if (
        len(after.revisions) != len(snapshot.revisions) + 1
        or after.revisions[-1] != expected
        or after.receipt is not None
    ):
        raise RelayActivationRecoveryExecutorError(
            "activation_recovery_required"
        )
    return after, expected, False


def _next_ownership(
    intent: ActivationJournalRevision,
) -> tuple[bool, bool]:
    owns_enable = intent.owns_enable
    owns_start = intent.owns_start
    if intent.step_id == "enable":
        owns_enable = True
    elif intent.step_id == "start":
        owns_start = True
    elif intent.step_id == "rollback_stop":
        owns_start = False
    elif intent.step_id == "rollback_disable":
        owns_enable = False
        owns_start = False
    return owns_enable, owns_start


def _publish_observation(
    store: _RecoveryStore,
    decision: ActivationRecoveryDecision,
    snapshot: ActivationJournalRecoverySnapshot,
    intent: ActivationJournalRevision,
    *,
    prerequisites: ActivationPrerequisiteSnapshot,
    systemd: SystemdSnapshot,
) -> ActivationJournalRecoverySnapshot:
    rollback_verify = (
        intent.step_id == "verify"
        and intent.intent_id == "rollback_verify_requested"
        and any(
            revision.step_id in _ROLLBACK_STEPS
            for revision in snapshot.revisions
        )
    )
    try:
        observation = (
            build_activation_rollback_verification_observation(
                intent.identity,
                prerequisites=prerequisites,
                systemd=systemd,
            )
            if rollback_verify
            else build_activation_step_observation(
                intent.identity,
                step_id=intent.step_id,
                prerequisites=prerequisites,
                systemd=systemd,
            )
        )
    except RelayActivationEvidenceError:
        raise RelayActivationRecoveryExecutorError(
            "activation_recovery_required"
        ) from None
    owns_enable, owns_start = _next_ownership(intent)
    raw = build_activation_revision(
        intent.identity,
        revision=intent.revision + 1,
        previous_revision_sha256=intent.record_sha256,
        phase="observed",
        step_id=intent.step_id,
        intent_id=intent.intent_id,
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
        raise RelayActivationRecoveryExecutorError(
            "activation_recovery_required"
        )
    return after


def _result_projection(
    decision: ActivationRecoveryDecision,
    snapshot: ActivationJournalRecoverySnapshot,
    *,
    intent_reused: bool,
) -> dict[str, object]:
    last = snapshot.revisions[-1]
    if (
        last.phase != "observed"
        or last.step_id != decision.step_id
        or snapshot.receipt is not None
        or last.identity.plan_sha256 != decision.plan_sha256
    ):
        raise RelayActivationRecoveryExecutorError(
            "activation_recovery_required"
        )
    return {
        "action_id": decision.action_id,
        "decision_sha256": decision.decision_sha256,
        "intent_reused": intent_reused,
        "journal_head_sha256": last.record_sha256,
        "latest_revision": last.revision,
        "ok": True,
        "operation_id": "run_step",
        "plan_sha256": decision.plan_sha256,
        "recovery_required": True,
        "requested_outcome": decision.requested_outcome,
        "schema_id": ACTIVATION_RECOVERY_EXECUTOR_SCHEMA,
        "state": "recovery_required",
        "step_id": decision.step_id,
        "write_id": "step_observed",
    }


def _run_confirmed_recovery_step_with(
    plan_sha256: str,
    requested_outcome: str,
    confirmed_decision_sha256: str,
    *,
    store: _RecoveryStore,
    scanner: Scanner,
    systemd_reader: SystemdReader,
    mutation_runner: MutationRunner,
) -> dict[str, object]:
    """Execute exactly one confirmed scanner-bound recovery step."""

    if (
        not isinstance(plan_sha256, str)
        or not SHA256_PATTERN.fullmatch(plan_sha256)
        or not isinstance(requested_outcome, str)
        or requested_outcome not in _OUTCOMES
        or not isinstance(confirmed_decision_sha256, str)
        or not SHA256_PATTERN.fullmatch(confirmed_decision_sha256)
        or not callable(
            getattr(store, "_load_recovery_snapshot", None)
        )
        or not callable(getattr(store, "publish_revision", None))
        or not callable(scanner)
        or not callable(systemd_reader)
        or not callable(mutation_runner)
    ):
        raise RelayActivationRecoveryExecutorError(
            "activation_recovery_confirmation_invalid"
        )
    execution = _ExecutionState(store, mutation_runner)
    confirmed = False
    try:
        observation = _observe_activation_recovery_with(
            plan_sha256,
            requested_outcome,
            snapshot_loader=execution._load_recovery_snapshot,
            scanner=scanner,
            systemd_reader=systemd_reader,
        )
        decision = observation.decision
        if decision.decision_sha256 != confirmed_decision_sha256:
            raise RelayActivationRecoveryExecutorError(
                "activation_recovery_confirmation_stale"
            )
        if decision.action_id == "blocked":
            raise RelayActivationRecoveryExecutorError(
                "activation_recovery_action_blocked"
            )
        if (
            decision.operation_id != "run_step"
            or decision.step_id not in _INTENT_IDS
        ):
            raise RelayActivationRecoveryExecutorError(
                "activation_recovery_action_not_supported"
            )
        rollback_action = (
            decision.step_id in _ROLLBACK_STEPS
            or (
                decision.step_id == "verify"
                and any(
                    revision.step_id in _ROLLBACK_STEPS
                    for revision in observation.snapshot.revisions
                )
            )
        )
        expected_action = "inverse" if rollback_action else "resume"
        if decision.action_id != expected_action:
            raise RelayActivationRecoveryExecutorError(
                "activation_recovery_required"
            )
        confirmed = True
        intent_snapshot, intent, intent_reused = _publish_intent(
            execution,
            decision,
            observation.snapshot,
        )
        before_prerequisites, before_systemd = _stable_snapshot(
            scanner,
            systemd_reader,
        )
        if (
            before_prerequisites != observation.prerequisites
            or before_systemd != observation.systemd
        ):
            raise RelayActivationRecoveryExecutorError(
                "activation_recovery_required"
            )
        mutation_id = _MUTATION_IDS.get(str(decision.step_id))
        if mutation_id is not None:
            execution.mutate(
                before_prerequisites.systemctl,
                mutation_id,
            )
        after_prerequisites, after_systemd = _stable_snapshot(
            scanner,
            systemd_reader,
        )
        after = _publish_observation(
            execution,
            decision,
            intent_snapshot,
            intent,
            prerequisites=after_prerequisites,
            systemd=after_systemd,
        )
        return _result_projection(
            decision,
            after,
            intent_reused=intent_reused,
        )
    except RelayActivationRecoveryExecutorError as exc:
        if (
            confirmed
            and (
                execution.write_attempted
                or execution.mutation_attempted
            )
            and exc.error_id != "activation_recovery_required"
        ):
            raise RelayActivationRecoveryExecutorError(
                "activation_recovery_required"
            ) from None
        raise exc
    except RelayActivationRecoveryPreviewError as exc:
        error_id = (
            "activation_recovery_confirmation_invalid"
            if exc.error_id == "activation_recovery_preview_invalid"
            else "activation_recovery_required"
        )
        raise RelayActivationRecoveryExecutorError(error_id) from None
    except RelayActivationJournalError:
        raise RelayActivationRecoveryExecutorError(
            "activation_recovery_required"
        ) from None
    except Exception:
        raise RelayActivationRecoveryExecutorError(
            (
                "activation_recovery_required"
                if confirmed
                or execution.write_attempted
                or execution.mutation_attempted
                else "activation_recovery_executor_failed"
            )
        ) from None


def _run_confirmed_recovery_step(
    plan_sha256: str,
    requested_outcome: str,
    confirmed_decision_sha256: str,
) -> dict[str, object]:
    """Private production entrypoint; intentionally absent from the CLI."""

    try:
        with _open_locked_production_store(Path("/")) as store:
            capability = store._activation_scan_capability()
            return _run_confirmed_recovery_step_with(
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
                mutation_runner=_run_bound_systemd_mutation,
            )
    except RelayActivationRecoveryExecutorError:
        raise
    except RelayActivationJournalError as exc:
        error_id = (
            "activation_recovery_executor_busy"
            if exc.error_id == "activation_journal_busy"
            else "activation_recovery_required"
        )
        raise RelayActivationRecoveryExecutorError(error_id) from None
    except Exception:
        raise RelayActivationRecoveryExecutorError(
            "activation_recovery_executor_failed"
        ) from None
