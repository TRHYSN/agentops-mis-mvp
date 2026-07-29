"""Compile one bounded, read-only Relay activation recovery decision."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace

from agentops_mis_cli.relay_activation import (
    SHA256_PATTERN,
    ActivationPrerequisiteSnapshot,
    SystemdSnapshot,
    compile_activation_plan,
)
from agentops_mis_cli.relay_activation_evidence import (
    RelayActivationEvidenceError,
    _enablement_inventory_sha256,
    _unit_identity_sha256,
    build_activation_rollback_verification_observation,
    build_activation_step_observation,
)
from agentops_mis_cli.relay_activation_journal import (
    MAX_JOURNAL_REVISIONS,
    ActivationJournalIdentity,
    ActivationJournalReceipt,
    ActivationJournalRecoverySnapshot,
    ActivationJournalRevision,
    RelayActivationJournalError,
    _validate_terminal_binding,
    build_activation_revision,
    parse_activation_revision,
    validate_activation_revision_chain,
)


ACTIVATION_RECOVERY_DECISION_SCHEMA = (
    "agentops.relay.activation-recovery-decision.v0"
)
_OUTCOMES = frozenset({"resume", "rollback"})
_ACTIONS = frozenset(
    {"blocked", "complete", "inverse", "resume", "terminalize"}
)
_OPERATIONS = frozenset(
    {
        "none",
        "publish_rollback_receipt",
        "publish_success_receipt",
        "publish_terminal_revision",
        "record_observation",
        "run_step",
    }
)
_REASONS = frozenset(
    {
        "journal_complete",
        "no_owned_change",
        "ownership_ambiguous",
        "ownership_unproven",
        "plan_binding_unproven",
        "receipt_ready",
        "resume_ready",
        "rollback_contract_incomplete",
        "state_drift",
    }
)
_FORWARD_STEPS = ("daemon_reload", "enable", "start", "verify")
_ROLLBACK_STEPS = frozenset({"rollback_stop", "rollback_disable"})


class RelayActivationRecoveryError(Exception):
    """One bounded failure for invalid private recovery inputs."""

    def __init__(self) -> None:
        self.error_id = "activation_recovery_decision_invalid"
        super().__init__(self.error_id)


@dataclass(frozen=True)
class ActivationRecoveryDecision:
    plan_sha256: str
    requested_outcome: str
    action_id: str
    operation_id: str
    step_id: str | None
    reason_id: str
    latest_revision: int
    journal_head_sha256: str
    receipt_sha256: str | None
    observation_sha256: str | None
    decision_sha256: str
    schema_id: str = ACTIVATION_RECOVERY_DECISION_SCHEMA


def _canonical_json(value: object) -> bytes:
    try:
        return (
            json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True)
            + "\n"
        ).encode("ascii")
    except (TypeError, UnicodeEncodeError, ValueError):
        raise RelayActivationRecoveryError() from None


def _decision_payload(
    *,
    plan_sha256: str,
    requested_outcome: str,
    action_id: str,
    operation_id: str,
    step_id: str | None,
    reason_id: str,
    latest_revision: int,
    journal_head_sha256: str,
    receipt_sha256: str | None,
    observation_sha256: str | None,
) -> dict[str, object]:
    if (
        not isinstance(plan_sha256, str)
        or not SHA256_PATTERN.fullmatch(plan_sha256)
        or not isinstance(requested_outcome, str)
        or requested_outcome not in _OUTCOMES
        or not isinstance(action_id, str)
        or action_id not in _ACTIONS
        or not isinstance(operation_id, str)
        or operation_id not in _OPERATIONS
        or not isinstance(reason_id, str)
        or reason_id not in _REASONS
        or type(latest_revision) is not int
        or latest_revision < 1
        or latest_revision > MAX_JOURNAL_REVISIONS
        or not isinstance(journal_head_sha256, str)
        or not SHA256_PATTERN.fullmatch(journal_head_sha256)
        or (
            receipt_sha256 is not None
            and (
                not isinstance(receipt_sha256, str)
                or not SHA256_PATTERN.fullmatch(receipt_sha256)
            )
        )
        or (
            observation_sha256 is not None
            and (
                not isinstance(observation_sha256, str)
                or not SHA256_PATTERN.fullmatch(observation_sha256)
            )
        )
        or (
            step_id is not None
            and (
                not isinstance(step_id, str)
                or step_id
                not in {
                    *_FORWARD_STEPS,
                    *_ROLLBACK_STEPS,
                    "terminal",
                }
            )
        )
    ):
        raise RelayActivationRecoveryError()
    return {
        "action_id": action_id,
        "journal_head_sha256": journal_head_sha256,
        "latest_revision": latest_revision,
        "observation_sha256": observation_sha256,
        "operation_id": operation_id,
        "plan_sha256": plan_sha256,
        "reason_id": reason_id,
        "receipt_sha256": receipt_sha256,
        "requested_outcome": requested_outcome,
        "schema_id": ACTIVATION_RECOVERY_DECISION_SCHEMA,
        "step_id": step_id,
    }


def _decision(
    snapshot: ActivationJournalRecoverySnapshot,
    requested_outcome: str,
    *,
    action_id: str,
    operation_id: str,
    step_id: str | None,
    reason_id: str,
    observation_sha256: str | None = None,
) -> ActivationRecoveryDecision:
    last = snapshot.revisions[-1]
    receipt_sha256 = (
        snapshot.receipt.receipt_sha256
        if snapshot.receipt is not None
        else None
    )
    payload = _decision_payload(
        plan_sha256=last.identity.plan_sha256,
        requested_outcome=requested_outcome,
        action_id=action_id,
        operation_id=operation_id,
        step_id=step_id,
        reason_id=reason_id,
        latest_revision=last.revision,
        journal_head_sha256=last.record_sha256,
        receipt_sha256=receipt_sha256,
        observation_sha256=observation_sha256,
    )
    return ActivationRecoveryDecision(
        plan_sha256=last.identity.plan_sha256,
        requested_outcome=requested_outcome,
        action_id=action_id,
        operation_id=operation_id,
        step_id=step_id,
        reason_id=reason_id,
        latest_revision=last.revision,
        journal_head_sha256=last.record_sha256,
        receipt_sha256=receipt_sha256,
        observation_sha256=observation_sha256,
        decision_sha256=hashlib.sha256(
            _canonical_json(payload)
        ).hexdigest(),
    )


def _validate_snapshot(
    snapshot: ActivationJournalRecoverySnapshot,
) -> None:
    if (
        not isinstance(snapshot, ActivationJournalRecoverySnapshot)
        or not isinstance(snapshot.revisions, tuple)
        or (
            snapshot.receipt is not None
            and not isinstance(snapshot.receipt, ActivationJournalReceipt)
        )
    ):
        raise RelayActivationRecoveryError()
    try:
        validate_activation_revision_chain(snapshot.revisions)
        last = snapshot.revisions[-1]
        if last.phase == "terminal":
            if snapshot.receipt is None:
                raise RelayActivationJournalError(
                    "activation_journal_invalid"
                )
            _validate_terminal_binding(last, snapshot.receipt)
        elif snapshot.receipt is not None:
            receipt = snapshot.receipt
            terminal = parse_activation_revision(
                build_activation_revision(
                    last.identity,
                    revision=receipt.terminal_revision,
                    previous_revision_sha256=(
                        receipt.previous_revision_sha256
                    ),
                    phase="terminal",
                    step_id="terminal",
                    owns_enable=receipt.owns_enable,
                    owns_start=receipt.owns_start,
                    terminal_state=receipt.terminal_state,
                    receipt_sha256=receipt.receipt_sha256,
                )
            )
            validate_activation_revision_chain(
                (*snapshot.revisions, terminal)
            )
            _validate_terminal_binding(terminal, receipt)
    except (RelayActivationJournalError, TypeError, ValueError):
        raise RelayActivationRecoveryError() from None


def _pre_state_matches(
    revision: ActivationJournalRevision,
    prerequisites: ActivationPrerequisiteSnapshot,
    systemd: SystemdSnapshot,
) -> bool:
    try:
        identity = revision.identity
        plan = compile_activation_plan(prerequisites, systemd)
        return (
            plan.ok is True
            and plan.release_id == identity.release_id
            and plan.version_id == identity.version_id
            and _unit_identity_sha256(prerequisites.unit)
            == identity.unit_identity_sha256
            and _enablement_inventory_sha256(
                prerequisites.enablement_links
            )
            == identity.pre_enablement_inventory_sha256
            and systemd.unit_file_state
            == identity.pre_unit_file_state
            and systemd.active_state == identity.pre_active_state
        )
    except Exception:
        return False


def _original_plan_matches(
    identity: ActivationJournalIdentity,
    prerequisites: ActivationPrerequisiteSnapshot,
    systemd: SystemdSnapshot,
) -> bool:
    """Rebuild the exact pre-mutation plan from the current private snapshot."""

    try:
        if identity.pre_unit_file_state == "disabled":
            original_prerequisites = replace(
                prerequisites,
                enablement_links=(),
            )
        else:
            if (
                _enablement_inventory_sha256(
                    prerequisites.enablement_links
                )
                != identity.pre_enablement_inventory_sha256
            ):
                return False
            original_prerequisites = prerequisites
        originally_active = identity.pre_active_state == "active"
        reload_values = (False,) if originally_active else (False, True)
        result_values = (
            (systemd.result,)
            if originally_active
            else tuple(dict.fromkeys((systemd.result, "", "success")))
        )
        for need_daemon_reload in reload_values:
            for result in result_values:
                original_systemd = replace(
                    systemd,
                    unit_file_state=identity.pre_unit_file_state,
                    active_state=identity.pre_active_state,
                    sub_state=(
                        "running" if originally_active else "dead"
                    ),
                    result=result,
                    exec_main_status=0,
                    need_daemon_reload=need_daemon_reload,
                    invocation_id=(
                        systemd.invocation_id
                        if originally_active
                        else ""
                    ),
                    main_pid=systemd.main_pid if originally_active else 0,
                )
                plan = compile_activation_plan(
                    original_prerequisites,
                    original_systemd,
                )
                if (
                    plan.ok is True
                    and plan.plan_sha256 == identity.plan_sha256
                ):
                    return True
        return False
    except Exception:
        return False


def _current_observation(
    revision: ActivationJournalRevision,
    step_id: str,
    prerequisites: ActivationPrerequisiteSnapshot,
    systemd: SystemdSnapshot,
    *,
    rollback_verify: bool = False,
):
    try:
        if rollback_verify:
            return build_activation_rollback_verification_observation(
                revision.identity,
                prerequisites=prerequisites,
                systemd=systemd,
            )
        return build_activation_step_observation(
            revision.identity,
            step_id=step_id,
            prerequisites=prerequisites,
            systemd=systemd,
        )
    except RelayActivationEvidenceError:
        return None


def _revision_state_matches(
    revision: ActivationJournalRevision,
    prerequisites: ActivationPrerequisiteSnapshot,
    systemd: SystemdSnapshot,
    *,
    rollback_verify: bool = False,
) -> tuple[bool, str | None]:
    if revision.phase == "prepared":
        return _pre_state_matches(revision, prerequisites, systemd), None
    if revision.phase != "observed":
        return False, None
    observation = _current_observation(
        revision,
        revision.step_id,
        prerequisites,
        systemd,
        rollback_verify=rollback_verify,
    )
    if (
        observation is None
        or observation.observation_id != revision.observation_id
        or observation.observation_sha256
        != revision.observation_sha256
    ):
        return False, None
    return True, observation.observation_sha256


def _next_forward_step(
    identity,
    completed_step: str | None,
) -> str | None:
    steps = ["daemon_reload"]
    if identity.pre_unit_file_state == "disabled":
        steps.append("enable")
    if identity.pre_active_state == "inactive":
        steps.append("start")
    steps.append("verify")
    if completed_step is None:
        return steps[0]
    try:
        index = steps.index(completed_step)
    except ValueError:
        return None
    return steps[index + 1] if index + 1 < len(steps) else None


def _owned_observation(
    revisions: tuple[ActivationJournalRevision, ...],
    step_id: str,
) -> ActivationJournalRevision | None:
    for revision in reversed(revisions):
        if revision.phase == "observed" and revision.step_id == step_id:
            return revision
    return None


def _enablement_ownership_matches(
    snapshot: ActivationJournalRecoverySnapshot,
    prerequisites: ActivationPrerequisiteSnapshot,
    systemd: SystemdSnapshot,
) -> bool:
    evidence = _owned_observation(snapshot.revisions, "enable")
    if evidence is None:
        return False
    originally_active = (
        evidence.identity.pre_active_state == "active"
    )
    synthetic_systemd = replace(
        systemd,
        unit_file_state="enabled",
        active_state=evidence.identity.pre_active_state,
        sub_state="running" if originally_active else "dead",
        need_daemon_reload=False,
        invocation_id=systemd.invocation_id if originally_active else "",
        main_pid=systemd.main_pid if originally_active else 0,
    )
    observation = _current_observation(
        evidence,
        "enable",
        prerequisites,
        synthetic_systemd,
    )
    return (
        observation is not None
        and observation.observation_id == evidence.observation_id
        and observation.observation_sha256
        == evidence.observation_sha256
    )


def _inverse_decision(
    snapshot: ActivationJournalRecoverySnapshot,
    requested_outcome: str,
    owner: ActivationJournalRevision,
    prerequisites: ActivationPrerequisiteSnapshot,
    systemd: SystemdSnapshot,
) -> ActivationRecoveryDecision:
    if owner.owns_start:
        step_id = "rollback_stop"
        evidence = _owned_observation(snapshot.revisions, "start")
    elif owner.owns_enable:
        step_id = "rollback_disable"
        evidence = _owned_observation(snapshot.revisions, "enable")
    else:
        return _decision(
            snapshot,
            requested_outcome,
            action_id="blocked",
            operation_id="none",
            step_id=None,
            reason_id="no_owned_change",
        )
    if evidence is None:
        return _decision(
            snapshot,
            requested_outcome,
            action_id="blocked",
            operation_id="none",
            step_id=step_id,
            reason_id="ownership_unproven",
        )
    matches, observation_sha256 = _revision_state_matches(
        evidence,
        prerequisites,
        systemd,
    )
    if not matches:
        return _decision(
            snapshot,
            requested_outcome,
            action_id="blocked",
            operation_id="none",
            step_id=step_id,
            reason_id="ownership_unproven",
        )
    return _decision(
        snapshot,
        requested_outcome,
        action_id="inverse",
        operation_id="run_step",
        step_id=step_id,
        reason_id="resume_ready",
        observation_sha256=observation_sha256,
    )


def compile_activation_recovery_decision(
    snapshot: ActivationJournalRecoverySnapshot,
    prerequisites: ActivationPrerequisiteSnapshot,
    systemd: SystemdSnapshot,
    *,
    requested_outcome: str,
) -> ActivationRecoveryDecision:
    """Return one hash-bound action without reading or mutating the host."""

    if (
        not isinstance(requested_outcome, str)
        or requested_outcome not in _OUTCOMES
        or not isinstance(prerequisites, ActivationPrerequisiteSnapshot)
        or not isinstance(systemd, SystemdSnapshot)
    ):
        raise RelayActivationRecoveryError()
    _validate_snapshot(snapshot)
    last = snapshot.revisions[-1]
    rollback_started = any(
        revision.step_id in _ROLLBACK_STEPS
        for revision in snapshot.revisions
    )
    if last.phase == "terminal":
        return _decision(
            snapshot,
            requested_outcome,
            action_id="complete",
            operation_id="none",
            step_id="terminal",
            reason_id="journal_complete",
        )
    if snapshot.receipt is not None:
        return _decision(
            snapshot,
            requested_outcome,
            action_id="terminalize",
            operation_id="publish_terminal_revision",
            step_id="terminal",
            reason_id="receipt_ready",
        )
    if not _original_plan_matches(
        last.identity,
        prerequisites,
        systemd,
    ):
        return _decision(
            snapshot,
            requested_outcome,
            action_id="blocked",
            operation_id="none",
            step_id=last.step_id,
            reason_id="plan_binding_unproven",
        )
    if rollback_started and requested_outcome != "rollback":
        return _decision(
            snapshot,
            requested_outcome,
            action_id="blocked",
            operation_id="none",
            step_id=last.step_id,
            reason_id="rollback_contract_incomplete",
        )

    if last.phase == "intent":
        rollback_verify = (
            rollback_started and last.step_id == "verify"
        )
        observation = _current_observation(
            last,
            last.step_id,
            prerequisites,
            systemd,
            rollback_verify=rollback_verify,
        )
        if observation is not None:
            if last.step_id in {"enable", "start"}:
                return _decision(
                    snapshot,
                    requested_outcome,
                    action_id="blocked",
                    operation_id="none",
                    step_id=last.step_id,
                    reason_id="ownership_ambiguous",
                    observation_sha256=(
                        observation.observation_sha256
                    ),
                )
            if (
                last.step_id == "verify"
                and last.owns_enable
                and not _enablement_ownership_matches(
                    snapshot,
                    prerequisites,
                    systemd,
                )
            ):
                return _decision(
                    snapshot,
                    requested_outcome,
                    action_id="blocked",
                    operation_id="none",
                    step_id=last.step_id,
                    reason_id="ownership_unproven",
                    observation_sha256=(
                        observation.observation_sha256
                    ),
                )
            return _decision(
                snapshot,
                requested_outcome,
                action_id=(
                    "inverse"
                    if last.step_id in _ROLLBACK_STEPS
                    or rollback_verify
                    else "resume"
                ),
                operation_id="record_observation",
                step_id=last.step_id,
                reason_id="resume_ready",
                observation_sha256=observation.observation_sha256,
            )
        previous = snapshot.revisions[-2]
        matches, observation_sha256 = _revision_state_matches(
            previous,
            prerequisites,
            systemd,
        )
        if rollback_verify:
            if not matches:
                return _decision(
                    snapshot,
                    requested_outcome,
                    action_id="blocked",
                    operation_id="none",
                    step_id="verify",
                    reason_id="state_drift",
                )
            return _decision(
                snapshot,
                requested_outcome,
                action_id="inverse",
                operation_id="run_step",
                step_id="verify",
                reason_id="resume_ready",
                observation_sha256=observation_sha256,
            )
        if last.step_id in _ROLLBACK_STEPS:
            if not matches:
                return _decision(
                    snapshot,
                    requested_outcome,
                    action_id="blocked",
                    operation_id="none",
                    step_id=last.step_id,
                    reason_id="state_drift",
                )
            return _decision(
                snapshot,
                requested_outcome,
                action_id="inverse",
                operation_id="run_step",
                step_id=last.step_id,
                reason_id="resume_ready",
                observation_sha256=observation_sha256,
            )
        if requested_outcome == "resume":
            if not matches:
                return _decision(
                    snapshot,
                    requested_outcome,
                    action_id="blocked",
                    operation_id="none",
                    step_id=last.step_id,
                    reason_id="state_drift",
                )
            return _decision(
                snapshot,
                requested_outcome,
                action_id="resume",
                operation_id="run_step",
                step_id=last.step_id,
                reason_id="resume_ready",
                observation_sha256=observation_sha256,
            )
        return _inverse_decision(
            snapshot,
            requested_outcome,
            previous,
            prerequisites,
            systemd,
        )

    if last.phase == "prepared":
        if requested_outcome == "rollback":
            return _decision(
                snapshot,
                requested_outcome,
                action_id="blocked",
                operation_id="none",
                step_id=None,
                reason_id="no_owned_change",
            )
        if not _pre_state_matches(last, prerequisites, systemd):
            return _decision(
                snapshot,
                requested_outcome,
                action_id="blocked",
                operation_id="none",
                step_id="daemon_reload",
                reason_id="state_drift",
            )
        return _decision(
            snapshot,
            requested_outcome,
            action_id="resume",
            operation_id="run_step",
            step_id="daemon_reload",
            reason_id="resume_ready",
        )

    rollback_verify = rollback_started and last.step_id == "verify"
    matches, observation_sha256 = _revision_state_matches(
        last,
        prerequisites,
        systemd,
        rollback_verify=rollback_verify,
    )
    if not matches:
        return _decision(
            snapshot,
            requested_outcome,
            action_id="blocked",
            operation_id="none",
            step_id=last.step_id,
            reason_id="state_drift",
        )
    if rollback_verify:
        if last.owns_enable or last.owns_start:
            return _decision(
                snapshot,
                requested_outcome,
                action_id="blocked",
                operation_id="none",
                step_id="verify",
                reason_id="ownership_unproven",
                observation_sha256=observation_sha256,
            )
        return _decision(
            snapshot,
            requested_outcome,
            action_id="inverse",
            operation_id="publish_rollback_receipt",
            step_id="verify",
            reason_id="resume_ready",
            observation_sha256=observation_sha256,
        )
    if last.step_id in _ROLLBACK_STEPS:
        if last.owns_start or last.owns_enable:
            return _inverse_decision(
                snapshot,
                requested_outcome,
                last,
                prerequisites,
                systemd,
            )
        return _decision(
            snapshot,
            requested_outcome,
            action_id="inverse",
            operation_id="run_step",
            step_id="verify",
            reason_id="resume_ready",
            observation_sha256=observation_sha256,
        )
    if requested_outcome == "rollback":
        return _inverse_decision(
            snapshot,
            requested_outcome,
            last,
            prerequisites,
            systemd,
        )
    if (
        last.owns_enable
        and last.step_id in {"start", "verify"}
        and not _enablement_ownership_matches(
            snapshot,
            prerequisites,
            systemd,
        )
    ):
        return _decision(
            snapshot,
            requested_outcome,
            action_id="blocked",
            operation_id="none",
            step_id=last.step_id,
            reason_id="ownership_unproven",
            observation_sha256=observation_sha256,
        )
    next_step = _next_forward_step(
        last.identity,
        last.step_id,
    )
    if next_step is None:
        return _decision(
            snapshot,
            requested_outcome,
            action_id="resume",
            operation_id="publish_success_receipt",
            step_id="verify",
            reason_id="resume_ready",
            observation_sha256=observation_sha256,
        )
    return _decision(
        snapshot,
        requested_outcome,
        action_id="resume",
        operation_id="run_step",
        step_id=next_step,
        reason_id="resume_ready",
        observation_sha256=observation_sha256,
    )


def project_activation_recovery_decision(
    decision: ActivationRecoveryDecision,
) -> dict[str, object]:
    """Project only the bounded decision contract."""

    try:
        if not isinstance(decision, ActivationRecoveryDecision):
            raise RelayActivationRecoveryError()
        payload = _decision_payload(
            plan_sha256=decision.plan_sha256,
            requested_outcome=decision.requested_outcome,
            action_id=decision.action_id,
            operation_id=decision.operation_id,
            step_id=decision.step_id,
            reason_id=decision.reason_id,
            latest_revision=decision.latest_revision,
            journal_head_sha256=decision.journal_head_sha256,
            receipt_sha256=decision.receipt_sha256,
            observation_sha256=decision.observation_sha256,
        )
        if (
            decision.schema_id != ACTIVATION_RECOVERY_DECISION_SCHEMA
            or decision.decision_sha256
            != hashlib.sha256(_canonical_json(payload)).hexdigest()
        ):
            raise RelayActivationRecoveryError()
        return {
            **payload,
            "decision_sha256": decision.decision_sha256,
            "ok": decision.action_id != "blocked",
        }
    except RelayActivationRecoveryError:
        return {
            "ok": False,
            "operation_id": "recovery_decision",
            "schema_id": ACTIVATION_RECOVERY_DECISION_SCHEMA,
            "state": "invalid",
        }
