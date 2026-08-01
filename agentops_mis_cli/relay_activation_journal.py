"""Immutable journal primitives for a future confirmed Relay activation."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import stat
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from agentops_mis_cli.relay_activation import (
    RELEASE_ID_PATTERN,
    SHA256_PATTERN,
    UNIT_NAME,
    VERSION_PATTERN,
)


ACTIVATION_REVISION_SCHEMA = "agentops.relay.activation-revision.v0"
ACTIVATION_RECEIPT_SCHEMA = "agentops.relay.activation-receipt.v0"
ACTIVATION_JOURNAL_SCHEMA = "agentops.relay.activation-journal.v0"
GENESIS_REVISION_SHA256 = "0" * 64
MAX_JOURNAL_RECORD_BYTES = 16 * 1024
MAX_JOURNAL_REVISIONS = 128
MAX_JOURNAL_PLANS = 128
MAX_JOURNAL_RECEIPTS = 128
_LABEL_PATTERN = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_REVISION_NAME_PATTERN = re.compile(r"revision-([0-9]{6})\.json\Z")
_PLAN_DIRECTORY_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_RECEIPT_NAME_PATTERN = re.compile(r"([0-9a-f]{64})\.json\Z")
_ACTION_STEPS = frozenset(
    {
        "daemon_reload",
        "enable",
        "start",
        "verify",
        "rollback_stop",
        "rollback_disable",
    }
)
_TERMINAL_STATES = frozenset({"active", "service_state_rolled_back"})
_REVISION_KEYS = frozenset(
    {
        "schema_id",
        "plan_sha256",
        "unit_id",
        "release_id",
        "version_id",
        "pre_unit_file_state",
        "pre_active_state",
        "pre_enablement_inventory_sha256",
        "unit_identity_sha256",
        "revision",
        "previous_revision_sha256",
        "phase",
        "step_id",
        "intent_id",
        "observation_id",
        "observation_sha256",
        "owns_enable",
        "owns_start",
        "terminal_state",
        "receipt_sha256",
        "record_sha256",
    }
)
_RECEIPT_KEYS = frozenset(
    {
        "schema_id",
        "plan_sha256",
        "unit_id",
        "release_id",
        "version_id",
        "pre_unit_file_state",
        "pre_active_state",
        "pre_enablement_inventory_sha256",
        "unit_identity_sha256",
        "terminal_revision",
        "previous_revision_sha256",
        "terminal_state",
        "owns_enable",
        "owns_start",
        "result_id",
        "receipt_sha256",
    }
)


class RelayActivationJournalError(Exception):
    """One bounded error identifier for private journal failures."""

    def __init__(self, error_id: str) -> None:
        if error_id not in {
            "activation_journal_busy",
            "activation_journal_invalid",
            "activation_journal_recovery_required",
            "activation_journal_write_failed",
        }:
            error_id = "activation_journal_invalid"
        self.error_id = error_id
        super().__init__(error_id)


class _DuplicateJsonKey(ValueError):
    pass


@dataclass(frozen=True)
class ActivationJournalIdentity:
    plan_sha256: str
    release_id: str
    version_id: str
    pre_unit_file_state: str
    pre_active_state: str
    pre_enablement_inventory_sha256: str
    unit_identity_sha256: str
    unit_id: str = UNIT_NAME


@dataclass(frozen=True)
class ActivationJournalRevision:
    identity: ActivationJournalIdentity
    revision: int
    previous_revision_sha256: str
    phase: str
    step_id: str
    intent_id: str | None
    observation_id: str | None
    observation_sha256: str | None
    owns_enable: bool
    owns_start: bool
    terminal_state: str | None
    receipt_sha256: str | None
    record_sha256: str


@dataclass(frozen=True)
class ActivationJournalReceipt:
    identity: ActivationJournalIdentity
    terminal_revision: int
    previous_revision_sha256: str
    terminal_state: str
    owns_enable: bool
    owns_start: bool
    result_id: str
    receipt_sha256: str


@dataclass(frozen=True)
class ActivationJournalRecoverySnapshot:
    revisions: tuple[ActivationJournalRevision, ...]
    receipt: ActivationJournalReceipt | None


def _canonical_json(value: object) -> bytes:
    try:
        return (
            json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True)
            + "\n"
        ).encode("ascii")
    except (TypeError, UnicodeEncodeError, ValueError):
        raise RelayActivationJournalError("activation_journal_invalid") from None


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    output: dict[str, object] = {}
    for key, value in pairs:
        if key in output:
            raise _DuplicateJsonKey
        output[key] = value
    return output


def _reject_constant(_value: str) -> object:
    raise ValueError


def _load_canonical_object(raw: bytes) -> dict[str, object]:
    if (
        not isinstance(raw, bytes)
        or not raw
        or len(raw) > MAX_JOURNAL_RECORD_BYTES
    ):
        raise RelayActivationJournalError("activation_journal_invalid")
    try:
        text = raw.decode("ascii")
        value = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        _DuplicateJsonKey,
        ValueError,
    ):
        raise RelayActivationJournalError("activation_journal_invalid") from None
    if not isinstance(value, dict) or _canonical_json(value) != raw:
        raise RelayActivationJournalError("activation_journal_invalid")
    return value


def _validate_identity(identity: ActivationJournalIdentity) -> None:
    if (
        not isinstance(identity, ActivationJournalIdentity)
        or not isinstance(identity.plan_sha256, str)
        or not SHA256_PATTERN.fullmatch(identity.plan_sha256)
        or not isinstance(identity.release_id, str)
        or not RELEASE_ID_PATTERN.fullmatch(identity.release_id)
        or not isinstance(identity.version_id, str)
        or not VERSION_PATTERN.fullmatch(identity.version_id)
        or not identity.release_id.startswith(f"{identity.version_id}-")
        or not isinstance(identity.pre_unit_file_state, str)
        or identity.pre_unit_file_state not in {"enabled", "disabled"}
        or not isinstance(identity.pre_active_state, str)
        or identity.pre_active_state not in {"active", "inactive"}
        or (
            identity.pre_unit_file_state == "enabled"
            and identity.pre_active_state == "active"
        )
        or not isinstance(identity.pre_enablement_inventory_sha256, str)
        or not SHA256_PATTERN.fullmatch(
            identity.pre_enablement_inventory_sha256
        )
        or not isinstance(identity.unit_identity_sha256, str)
        or not SHA256_PATTERN.fullmatch(identity.unit_identity_sha256)
        or identity.unit_id != UNIT_NAME
    ):
        raise RelayActivationJournalError("activation_journal_invalid")


def _validate_label(value: str | None, *, required: bool) -> None:
    if value is None and not required:
        return
    if (
        not isinstance(value, str)
        or not _LABEL_PATTERN.fullmatch(value)
    ):
        raise RelayActivationJournalError("activation_journal_invalid")


def _positive_revision(value: object) -> int:
    if (
        type(value) is not int
        or value < 1
        or value > MAX_JOURNAL_REVISIONS
    ):
        raise RelayActivationJournalError("activation_journal_invalid")
    return value


def _optional_hash(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not SHA256_PATTERN.fullmatch(value):
        raise RelayActivationJournalError("activation_journal_invalid")
    return value


def _identity_payload(identity: ActivationJournalIdentity) -> dict[str, str]:
    _validate_identity(identity)
    return {
        "plan_sha256": identity.plan_sha256,
        "pre_active_state": identity.pre_active_state,
        "pre_enablement_inventory_sha256": (
            identity.pre_enablement_inventory_sha256
        ),
        "pre_unit_file_state": identity.pre_unit_file_state,
        "release_id": identity.release_id,
        "unit_id": identity.unit_id,
        "unit_identity_sha256": identity.unit_identity_sha256,
        "version_id": identity.version_id,
    }


def _revision_payload(
    identity: ActivationJournalIdentity,
    *,
    revision: int,
    previous_revision_sha256: str,
    phase: str,
    step_id: str,
    intent_id: str | None,
    observation_id: str | None,
    observation_sha256: str | None,
    owns_enable: bool,
    owns_start: bool,
    terminal_state: str | None,
    receipt_sha256: str | None,
) -> dict[str, object]:
    _validate_identity(identity)
    revision = _positive_revision(revision)
    if (
        not isinstance(previous_revision_sha256, str)
        or not SHA256_PATTERN.fullmatch(previous_revision_sha256)
        or type(owns_enable) is not bool
        or type(owns_start) is not bool
        or not isinstance(phase, str)
        or phase not in {"prepared", "intent", "observed", "terminal"}
        or not isinstance(step_id, str)
    ):
        raise RelayActivationJournalError("activation_journal_invalid")
    if phase == "prepared":
        if (
            revision != 1
            or previous_revision_sha256 != GENESIS_REVISION_SHA256
            or step_id != "transaction_open"
            or intent_id is not None
            or observation_id is not None
            or observation_sha256 is not None
            or owns_enable
            or owns_start
            or terminal_state is not None
            or receipt_sha256 is not None
        ):
            raise RelayActivationJournalError("activation_journal_invalid")
    elif phase == "intent":
        if (
            step_id not in _ACTION_STEPS
            or intent_id is None
            or observation_id is not None
            or observation_sha256 is not None
            or terminal_state is not None
            or receipt_sha256 is not None
        ):
            raise RelayActivationJournalError("activation_journal_invalid")
        _validate_label(intent_id, required=True)
    elif phase == "observed":
        if (
            step_id not in _ACTION_STEPS
            or intent_id is None
            or observation_id is None
            or not isinstance(observation_sha256, str)
            or not SHA256_PATTERN.fullmatch(observation_sha256)
            or terminal_state is not None
            or receipt_sha256 is not None
        ):
            raise RelayActivationJournalError("activation_journal_invalid")
        _validate_label(intent_id, required=True)
        _validate_label(observation_id, required=True)
    else:
        if (
            step_id != "terminal"
            or intent_id is not None
            or observation_id is not None
            or observation_sha256 is not None
            or not isinstance(terminal_state, str)
            or terminal_state not in _TERMINAL_STATES
            or receipt_sha256 is None
        ):
            raise RelayActivationJournalError("activation_journal_invalid")
        _optional_hash(receipt_sha256)
    return {
        "schema_id": ACTIVATION_REVISION_SCHEMA,
        **_identity_payload(identity),
        "revision": revision,
        "previous_revision_sha256": previous_revision_sha256,
        "phase": phase,
        "step_id": step_id,
        "intent_id": intent_id,
        "observation_id": observation_id,
        "observation_sha256": observation_sha256,
        "owns_enable": owns_enable,
        "owns_start": owns_start,
        "terminal_state": terminal_state,
        "receipt_sha256": receipt_sha256,
    }


def build_activation_revision(
    identity: ActivationJournalIdentity,
    *,
    revision: int,
    previous_revision_sha256: str,
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
    """Build one strict canonical revision without filesystem effects."""

    payload = _revision_payload(
        identity,
        revision=revision,
        previous_revision_sha256=previous_revision_sha256,
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
    payload["record_sha256"] = _sha256(_canonical_json(payload))
    return _canonical_json(payload)


def parse_activation_revision(raw: bytes) -> ActivationJournalRevision:
    """Parse and authenticate one exact canonical revision."""

    value = _load_canonical_object(raw)
    if set(value) != _REVISION_KEYS:
        raise RelayActivationJournalError("activation_journal_invalid")
    record_sha256 = value.get("record_sha256")
    if (
        not isinstance(record_sha256, str)
        or not SHA256_PATTERN.fullmatch(record_sha256)
    ):
        raise RelayActivationJournalError("activation_journal_invalid")
    unhashed = dict(value)
    unhashed.pop("record_sha256")
    if _sha256(_canonical_json(unhashed)) != record_sha256:
        raise RelayActivationJournalError("activation_journal_invalid")
    identity = ActivationJournalIdentity(
        plan_sha256=value.get("plan_sha256"),
        release_id=value.get("release_id"),
        version_id=value.get("version_id"),
        pre_unit_file_state=value.get("pre_unit_file_state"),
        pre_active_state=value.get("pre_active_state"),
        pre_enablement_inventory_sha256=value.get(
            "pre_enablement_inventory_sha256"
        ),
        unit_identity_sha256=value.get("unit_identity_sha256"),
        unit_id=value.get("unit_id"),
    )
    payload = _revision_payload(
        identity,
        revision=value.get("revision"),
        previous_revision_sha256=value.get("previous_revision_sha256"),
        phase=value.get("phase"),
        step_id=value.get("step_id"),
        intent_id=value.get("intent_id"),
        observation_id=value.get("observation_id"),
        observation_sha256=value.get("observation_sha256"),
        owns_enable=value.get("owns_enable"),
        owns_start=value.get("owns_start"),
        terminal_state=value.get("terminal_state"),
        receipt_sha256=value.get("receipt_sha256"),
    )
    if payload != unhashed:
        raise RelayActivationJournalError("activation_journal_invalid")
    return ActivationJournalRevision(
        identity=identity,
        revision=value["revision"],
        previous_revision_sha256=value["previous_revision_sha256"],
        phase=value["phase"],
        step_id=value["step_id"],
        intent_id=value["intent_id"],
        observation_id=value["observation_id"],
        observation_sha256=value["observation_sha256"],
        owns_enable=value["owns_enable"],
        owns_start=value["owns_start"],
        terminal_state=value["terminal_state"],
        receipt_sha256=value["receipt_sha256"],
        record_sha256=record_sha256,
    )


def _receipt_payload(
    identity: ActivationJournalIdentity,
    *,
    terminal_revision: int,
    previous_revision_sha256: str,
    terminal_state: str,
    owns_enable: bool,
    owns_start: bool,
    result_id: str,
) -> dict[str, object]:
    _validate_identity(identity)
    terminal_revision = _positive_revision(terminal_revision)
    if (
        terminal_revision < 2
        or not isinstance(previous_revision_sha256, str)
        or not SHA256_PATTERN.fullmatch(previous_revision_sha256)
        or not isinstance(terminal_state, str)
        or terminal_state not in _TERMINAL_STATES
        or type(owns_enable) is not bool
        or type(owns_start) is not bool
    ):
        raise RelayActivationJournalError("activation_journal_invalid")
    _validate_label(result_id, required=True)
    return {
        "schema_id": ACTIVATION_RECEIPT_SCHEMA,
        **_identity_payload(identity),
        "terminal_revision": terminal_revision,
        "previous_revision_sha256": previous_revision_sha256,
        "terminal_state": terminal_state,
        "owns_enable": owns_enable,
        "owns_start": owns_start,
        "result_id": result_id,
    }


def build_activation_receipt(
    identity: ActivationJournalIdentity,
    *,
    terminal_revision: int,
    previous_revision_sha256: str,
    terminal_state: str,
    owns_enable: bool,
    owns_start: bool,
    result_id: str,
) -> bytes:
    """Build an immutable receipt that a terminal revision can bind."""

    payload = _receipt_payload(
        identity,
        terminal_revision=terminal_revision,
        previous_revision_sha256=previous_revision_sha256,
        terminal_state=terminal_state,
        owns_enable=owns_enable,
        owns_start=owns_start,
        result_id=result_id,
    )
    payload["receipt_sha256"] = _sha256(_canonical_json(payload))
    return _canonical_json(payload)


def parse_activation_receipt(raw: bytes) -> ActivationJournalReceipt:
    """Parse and authenticate one exact canonical terminal receipt."""

    value = _load_canonical_object(raw)
    if set(value) != _RECEIPT_KEYS:
        raise RelayActivationJournalError("activation_journal_invalid")
    receipt_sha256 = value.get("receipt_sha256")
    if (
        not isinstance(receipt_sha256, str)
        or not SHA256_PATTERN.fullmatch(receipt_sha256)
    ):
        raise RelayActivationJournalError("activation_journal_invalid")
    unhashed = dict(value)
    unhashed.pop("receipt_sha256")
    if _sha256(_canonical_json(unhashed)) != receipt_sha256:
        raise RelayActivationJournalError("activation_journal_invalid")
    identity = ActivationJournalIdentity(
        plan_sha256=value.get("plan_sha256"),
        release_id=value.get("release_id"),
        version_id=value.get("version_id"),
        pre_unit_file_state=value.get("pre_unit_file_state"),
        pre_active_state=value.get("pre_active_state"),
        pre_enablement_inventory_sha256=value.get(
            "pre_enablement_inventory_sha256"
        ),
        unit_identity_sha256=value.get("unit_identity_sha256"),
        unit_id=value.get("unit_id"),
    )
    payload = _receipt_payload(
        identity,
        terminal_revision=value.get("terminal_revision"),
        previous_revision_sha256=value.get("previous_revision_sha256"),
        terminal_state=value.get("terminal_state"),
        owns_enable=value.get("owns_enable"),
        owns_start=value.get("owns_start"),
        result_id=value.get("result_id"),
    )
    if payload != unhashed:
        raise RelayActivationJournalError("activation_journal_invalid")
    return ActivationJournalReceipt(
        identity=identity,
        terminal_revision=value["terminal_revision"],
        previous_revision_sha256=value["previous_revision_sha256"],
        terminal_state=value["terminal_state"],
        owns_enable=value["owns_enable"],
        owns_start=value["owns_start"],
        result_id=value["result_id"],
        receipt_sha256=receipt_sha256,
    )


def _identity_matches(
    left: ActivationJournalIdentity,
    right: ActivationJournalIdentity,
) -> bool:
    return left == right


def _validate_ownership_transition(
    previous: ActivationJournalRevision,
    current: ActivationJournalRevision,
) -> None:
    if current.phase == "intent":
        if (
            current.owns_enable != previous.owns_enable
            or current.owns_start != previous.owns_start
        ):
            raise RelayActivationJournalError("activation_journal_invalid")
        return
    if current.phase != "observed":
        return
    if current.step_id in {"daemon_reload", "verify"}:
        valid = (
            current.owns_enable == previous.owns_enable
            and current.owns_start == previous.owns_start
        )
    elif current.step_id == "enable":
        valid = (
            not previous.owns_enable
            and current.owns_enable
            and current.owns_start == previous.owns_start
        )
    elif current.step_id == "start":
        valid = (
            previous.owns_enable == current.owns_enable
            and not previous.owns_start
            and current.owns_start
        )
    elif current.step_id == "rollback_stop":
        valid = (
            previous.owns_start
            and not current.owns_start
            and current.owns_enable == previous.owns_enable
        )
    else:
        valid = (
            previous.owns_enable
            and not current.owns_enable
            and not previous.owns_start
            and not current.owns_start
        )
    if not valid:
        raise RelayActivationJournalError("activation_journal_invalid")


def _forward_steps(
    identity: ActivationJournalIdentity,
) -> tuple[str, ...]:
    steps = ["daemon_reload"]
    if identity.pre_unit_file_state == "disabled":
        steps.append("enable")
    if identity.pre_active_state == "inactive":
        steps.append("start")
    steps.append("verify")
    return tuple(steps)


def _next_rollback_step(
    previous: ActivationJournalRevision,
) -> str:
    if previous.owns_start:
        return "rollback_stop"
    if previous.owns_enable:
        return "rollback_disable"
    return "verify"


def _validate_step_sequence(
    revisions: tuple[ActivationJournalRevision, ...],
) -> None:
    identity = revisions[0].identity
    forward_steps = _forward_steps(identity)
    forward_index = 0
    rollback = False
    for index, current in enumerate(revisions[1:], start=1):
        previous = revisions[index - 1]
        if current.phase == "intent":
            if rollback:
                expected_step = _next_rollback_step(previous)
            else:
                if forward_index >= len(forward_steps):
                    expected_step = None
                else:
                    expected_step = forward_steps[forward_index]
                if current.step_id != expected_step:
                    rollback_step = _next_rollback_step(previous)
                    if (
                        rollback_step == "verify"
                        or current.step_id != rollback_step
                    ):
                        raise RelayActivationJournalError(
                            "activation_journal_invalid"
                        )
                    rollback = True
                    expected_step = rollback_step
            if current.step_id != expected_step:
                raise RelayActivationJournalError(
                    "activation_journal_invalid"
                )
        elif current.phase == "observed" and not rollback:
            if (
                forward_index >= len(forward_steps)
                or current.step_id != forward_steps[forward_index]
            ):
                raise RelayActivationJournalError(
                    "activation_journal_invalid"
                )
            forward_index += 1
        elif current.phase == "terminal":
            if current.terminal_state == "active":
                expected_enable = (
                    identity.pre_unit_file_state == "disabled"
                )
                expected_start = identity.pre_active_state == "inactive"
                if (
                    rollback
                    or forward_index != len(forward_steps)
                    or previous.step_id != "verify"
                    or current.owns_enable is not expected_enable
                    or current.owns_start is not expected_start
                ):
                    raise RelayActivationJournalError(
                        "activation_journal_invalid"
                    )
            elif (
                not rollback
                or previous.step_id != "verify"
                or current.owns_enable
                or current.owns_start
            ):
                raise RelayActivationJournalError(
                    "activation_journal_invalid"
                )


def validate_activation_revision_chain(
    revisions: tuple[ActivationJournalRevision, ...],
) -> None:
    """Validate exact order, identity, hash links, and state transitions."""

    if not revisions or len(revisions) > MAX_JOURNAL_REVISIONS:
        raise RelayActivationJournalError("activation_journal_invalid")
    first = revisions[0]
    if (
        first.revision != 1
        or first.phase != "prepared"
        or first.previous_revision_sha256 != GENESIS_REVISION_SHA256
    ):
        raise RelayActivationJournalError("activation_journal_invalid")
    terminal_seen = False
    for index, current in enumerate(revisions):
        if current.revision != index + 1:
            raise RelayActivationJournalError("activation_journal_invalid")
        if not _identity_matches(first.identity, current.identity):
            raise RelayActivationJournalError("activation_journal_invalid")
        if index == 0:
            continue
        previous = revisions[index - 1]
        if (
            terminal_seen
            or current.previous_revision_sha256
            != previous.record_sha256
        ):
            raise RelayActivationJournalError("activation_journal_invalid")
        if current.phase == "intent":
            if previous.phase not in {"prepared", "observed"}:
                raise RelayActivationJournalError(
                    "activation_journal_invalid"
                )
        elif current.phase == "observed":
            if (
                previous.phase != "intent"
                or previous.step_id != current.step_id
                or previous.intent_id != current.intent_id
            ):
                raise RelayActivationJournalError(
                    "activation_journal_invalid"
                )
        elif current.phase == "terminal":
            if (
                previous.phase != "observed"
                or current.owns_enable != previous.owns_enable
                or current.owns_start != previous.owns_start
            ):
                raise RelayActivationJournalError(
                    "activation_journal_invalid"
                )
            terminal_seen = True
        else:
            raise RelayActivationJournalError("activation_journal_invalid")
        _validate_ownership_transition(previous, current)
    _validate_step_sequence(revisions)


def _validate_terminal_binding(
    revision: ActivationJournalRevision,
    receipt: ActivationJournalReceipt,
) -> None:
    if (
        revision.phase != "terminal"
        or revision.receipt_sha256 != receipt.receipt_sha256
        or revision.revision != receipt.terminal_revision
        or revision.previous_revision_sha256
        != receipt.previous_revision_sha256
        or revision.terminal_state != receipt.terminal_state
        or revision.owns_enable != receipt.owns_enable
        or revision.owns_start != receipt.owns_start
        or not _identity_matches(revision.identity, receipt.identity)
        or receipt.result_id
        != (
            "activation_succeeded"
            if revision.terminal_state == "active"
            else "rollback_succeeded"
        )
    ):
        raise RelayActivationJournalError("activation_journal_invalid")


def project_activation_journal(
    revisions: tuple[ActivationJournalRevision, ...],
    receipt: ActivationJournalReceipt | None,
) -> dict[str, object]:
    """Project only bounded journal state; in-progress chains need recovery."""

    try:
        validate_activation_revision_chain(revisions)
        last = revisions[-1]
        if last.phase != "terminal":
            if receipt is not None:
                raise RelayActivationJournalError(
                    "activation_journal_recovery_required"
                )
            return {
                "ok": False,
                "operation_id": "activate",
                "recovery_required": True,
                "revision_count": len(revisions),
                "schema_id": ACTIVATION_JOURNAL_SCHEMA,
                "state": "recovery_required",
            }
        if receipt is None:
            raise RelayActivationJournalError(
                "activation_journal_recovery_required"
            )
        _validate_terminal_binding(last, receipt)
        return {
            "ok": True,
            "operation_id": "activate",
            "receipt_sha256": receipt.receipt_sha256,
            "recovery_required": False,
            "revision_count": len(revisions),
            "schema_id": ACTIVATION_JOURNAL_SCHEMA,
            "state": last.terminal_state,
        }
    except RelayActivationJournalError as exc:
        if exc.error_id == "activation_journal_recovery_required":
            return {
                "ok": False,
                "operation_id": "activate",
                "recovery_required": True,
                "schema_id": ACTIVATION_JOURNAL_SCHEMA,
                "state": "recovery_required",
            }
        return {
            "ok": False,
            "operation_id": "activate",
            "recovery_required": True,
            "schema_id": ACTIVATION_JOURNAL_SCHEMA,
            "state": "invalid",
        }


def _fingerprint(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        stat.S_IFMT(metadata.st_mode),
        stat.S_IMODE(metadata.st_mode),
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _directory_flags() -> int:
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    directory = getattr(os, "O_DIRECTORY", 0)
    cloexec = getattr(os, "O_CLOEXEC", 0)
    if not nofollow or not directory or not cloexec:
        raise RelayActivationJournalError("activation_journal_invalid")
    return os.O_RDONLY | nofollow | directory | cloexec


def _file_flags() -> int:
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    cloexec = getattr(os, "O_CLOEXEC", 0)
    if not nofollow or not cloexec:
        raise RelayActivationJournalError("activation_journal_invalid")
    return nofollow | cloexec


def _validate_directory_metadata(
    metadata: os.stat_result,
    *,
    expected_uid: int,
    expected_gid: int,
) -> None:
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != expected_uid
        or metadata.st_gid != expected_gid
        or stat.S_IMODE(metadata.st_mode) != 0o700
        or metadata.st_nlink < 2
    ):
        raise RelayActivationJournalError("activation_journal_invalid")


def _open_directory_at(
    parent_fd: int,
    name: str,
    *,
    expected_uid: int,
    expected_gid: int,
    create: bool,
    exclusive_create: bool = False,
) -> int:
    if (
        not isinstance(name, str)
        or not name
        or "/" in name
        or name in {".", ".."}
        or (exclusive_create and not create)
    ):
        raise RelayActivationJournalError("activation_journal_invalid")
    descriptor = -1
    try:
        if create:
            try:
                os.mkdir(name, 0o700, dir_fd=parent_fd)
                os.fsync(parent_fd)
            except FileExistsError:
                if exclusive_create:
                    raise RelayActivationJournalError(
                        "activation_journal_recovery_required"
                    ) from None
        before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        descriptor = os.open(
            name,
            _directory_flags(),
            dir_fd=parent_fd,
        )
        opened = os.fstat(descriptor)
        after = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError:
        if descriptor >= 0:
            os.close(descriptor)
        raise RelayActivationJournalError(
            "activation_journal_invalid"
        ) from None
    try:
        if not (
            _fingerprint(before)
            == _fingerprint(opened)
            == _fingerprint(after)
        ):
            raise RelayActivationJournalError(
                "activation_journal_invalid"
            )
        _validate_directory_metadata(
            opened,
            expected_uid=expected_uid,
            expected_gid=expected_gid,
        )
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _validate_safe_parent_directory_metadata(
    metadata: os.stat_result,
    *,
    expected_uid: int,
    expected_gid: int,
    final: bool,
) -> None:
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != expected_uid
        or metadata.st_gid != expected_gid
        or metadata.st_nlink < 2
        or (
            stat.S_IMODE(metadata.st_mode) != 0o700
            if final
            else stat.S_IMODE(metadata.st_mode) & 0o022
        )
    ):
        raise RelayActivationJournalError("activation_journal_invalid")


def _open_existing_directory_chain(
    root_fd: int,
    parts: tuple[str, ...],
    *,
    expected_uid: int,
    expected_gid: int,
) -> int:
    if (
        type(root_fd) is not int
        or root_fd < 0
        or not parts
        or any(
            not isinstance(part, str)
            or not part
            or "/" in part
            or part in {".", ".."}
            for part in parts
        )
    ):
        raise RelayActivationJournalError("activation_journal_invalid")
    descriptor = -1
    try:
        descriptor = os.dup(root_fd)
        for index, part in enumerate(parts):
            child = -1
            try:
                before = os.stat(
                    part,
                    dir_fd=descriptor,
                    follow_symlinks=False,
                )
                child = os.open(
                    part,
                    _directory_flags(),
                    dir_fd=descriptor,
                )
                opened = os.fstat(child)
                after = os.stat(
                    part,
                    dir_fd=descriptor,
                    follow_symlinks=False,
                )
                if not (
                    _fingerprint(before)
                    == _fingerprint(opened)
                    == _fingerprint(after)
                ):
                    raise RelayActivationJournalError(
                        "activation_journal_invalid"
                    )
                _validate_safe_parent_directory_metadata(
                    opened,
                    expected_uid=expected_uid,
                    expected_gid=expected_gid,
                    final=index == len(parts) - 1,
                )
            except Exception:
                if child >= 0:
                    os.close(child)
                raise
            os.close(descriptor)
            descriptor = child
        return descriptor
    except RelayActivationJournalError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError:
        if descriptor >= 0:
            os.close(descriptor)
        raise RelayActivationJournalError(
            "activation_journal_invalid"
        ) from None


def _read_file_at(
    parent_fd: int,
    name: str,
    *,
    expected_uid: int,
    expected_gid: int,
) -> bytes:
    if (
        not isinstance(name, str)
        or not name
        or "/" in name
        or name in {".", ".."}
    ):
        raise RelayActivationJournalError("activation_journal_invalid")
    descriptor = -1
    try:
        before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        descriptor = os.open(
            name,
            os.O_RDONLY | _file_flags(),
            dir_fd=parent_fd,
        )
        opened = os.fstat(descriptor)
        after = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if not (
            _fingerprint(before)
            == _fingerprint(opened)
            == _fingerprint(after)
            and stat.S_ISREG(opened.st_mode)
            and opened.st_uid == expected_uid
            and opened.st_gid == expected_gid
            and stat.S_IMODE(opened.st_mode) == 0o600
            and opened.st_nlink == 1
            and 0 < opened.st_size <= MAX_JOURNAL_RECORD_BYTES
        ):
            raise RelayActivationJournalError(
                "activation_journal_invalid"
            )
        output = bytearray()
        while len(output) <= MAX_JOURNAL_RECORD_BYTES:
            chunk = os.read(
                descriptor,
                min(4096, MAX_JOURNAL_RECORD_BYTES - len(output) + 1),
            )
            if not chunk:
                break
            output.extend(chunk)
        final_fd = os.fstat(descriptor)
        final_path = os.stat(
            name,
            dir_fd=parent_fd,
            follow_symlinks=False,
        )
        if (
            len(output) != opened.st_size
            or len(output) > MAX_JOURNAL_RECORD_BYTES
            or _fingerprint(final_fd) != _fingerprint(opened)
            or _fingerprint(final_path) != _fingerprint(opened)
        ):
            raise RelayActivationJournalError(
                "activation_journal_invalid"
            )
        return bytes(output)
    except RelayActivationJournalError:
        raise
    except OSError:
        raise RelayActivationJournalError(
            "activation_journal_invalid"
        ) from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _name_exists(parent_fd: int, name: str) -> bool:
    try:
        os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        return True
    except FileNotFoundError:
        return False
    except OSError:
        raise RelayActivationJournalError(
            "activation_journal_recovery_required"
        ) from None


def _bounded_directory_names(
    directory_fd: int,
    maximum_entries: int,
) -> tuple[str, ...]:
    if type(maximum_entries) is not int or maximum_entries < 1:
        raise RelayActivationJournalError("activation_journal_invalid")
    names: list[str] = []
    try:
        with os.scandir(directory_fd) as entries:
            for entry in entries:
                if len(names) >= maximum_entries:
                    raise RelayActivationJournalError(
                        "activation_journal_recovery_required"
                    )
                names.append(entry.name)
    except RelayActivationJournalError:
        raise
    except OSError:
        raise RelayActivationJournalError(
            "activation_journal_recovery_required"
        ) from None
    return tuple(sorted(names))


def _write_all(descriptor: int, raw: bytes) -> None:
    offset = 0
    while offset < len(raw):
        written = os.write(descriptor, raw[offset:])
        if written <= 0:
            raise OSError("short write")
        offset += written


def _read_descriptor_bytes(descriptor: int, expected_size: int) -> bytes:
    os.lseek(descriptor, 0, os.SEEK_SET)
    output = bytearray()
    while len(output) < expected_size:
        chunk = os.read(
            descriptor,
            min(4096, expected_size - len(output)),
        )
        if not chunk:
            break
        output.extend(chunk)
    if len(output) != expected_size:
        raise OSError("published file changed")
    return bytes(output)


def _publication_identity_matches(
    metadata: os.stat_result,
    original: os.stat_result,
    *,
    expected_nlink: int,
) -> bool:
    return (
        stat.S_ISREG(metadata.st_mode)
        and metadata.st_dev == original.st_dev
        and metadata.st_ino == original.st_ino
        and stat.S_IFMT(metadata.st_mode) == stat.S_IFMT(original.st_mode)
        and stat.S_IMODE(metadata.st_mode) == stat.S_IMODE(original.st_mode)
        and metadata.st_uid == original.st_uid
        and metadata.st_gid == original.st_gid
        and metadata.st_size == original.st_size
        and metadata.st_nlink == expected_nlink
    )


def _publish_bytes_at(
    parent_fd: int,
    *,
    final_name: str,
    temporary_name: str,
    raw: bytes,
    expected_uid: int,
    expected_gid: int,
) -> None:
    if (
        not isinstance(raw, bytes)
        or not raw
        or len(raw) > MAX_JOURNAL_RECORD_BYTES
        or "/" in final_name
        or "/" in temporary_name
        or final_name in {".", ".."}
        or temporary_name in {".", ".."}
    ):
        raise RelayActivationJournalError("activation_journal_invalid")
    if _name_exists(parent_fd, final_name) or _name_exists(
        parent_fd,
        temporary_name,
    ):
        raise RelayActivationJournalError(
            "activation_journal_recovery_required"
        )
    descriptor = -1
    publication_started = False
    try:
        descriptor = os.open(
            temporary_name,
            os.O_RDWR
            | os.O_CREAT
            | os.O_EXCL
            | _file_flags(),
            0o600,
            dir_fd=parent_fd,
        )
        os.fchmod(descriptor, 0o600)
        _write_all(descriptor, raw)
        os.fsync(descriptor)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != expected_uid
            or metadata.st_gid != expected_gid
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
            or metadata.st_size != len(raw)
        ):
            raise OSError("temporary identity mismatch")
        path_before_link = os.stat(
            temporary_name,
            dir_fd=parent_fd,
            follow_symlinks=False,
        )
        if not _publication_identity_matches(
            path_before_link,
            metadata,
            expected_nlink=1,
        ):
            raise RelayActivationJournalError(
                "activation_journal_recovery_required"
            )
        publication_started = True
        os.link(
            temporary_name,
            final_name,
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
            follow_symlinks=False,
        )
        linked_identities = (
            os.fstat(descriptor),
            os.stat(
                temporary_name,
                dir_fd=parent_fd,
                follow_symlinks=False,
            ),
            os.stat(
                final_name,
                dir_fd=parent_fd,
                follow_symlinks=False,
            ),
        )
        if (
            any(
                not _publication_identity_matches(
                    current,
                    metadata,
                    expected_nlink=2,
                )
                for current in linked_identities
            )
            or _read_descriptor_bytes(descriptor, len(raw)) != raw
        ):
            raise RelayActivationJournalError(
                "activation_journal_recovery_required"
            )
        os.fsync(parent_fd)
        os.unlink(temporary_name, dir_fd=parent_fd)
        final_identities = (
            os.fstat(descriptor),
            os.stat(
                final_name,
                dir_fd=parent_fd,
                follow_symlinks=False,
            ),
        )
        if (
            any(
                not _publication_identity_matches(
                    current,
                    metadata,
                    expected_nlink=1,
                )
                for current in final_identities
            )
            or _read_descriptor_bytes(descriptor, len(raw)) != raw
        ):
            raise RelayActivationJournalError(
                "activation_journal_recovery_required"
            )
        os.fsync(parent_fd)
        os.close(descriptor)
        descriptor = -1
        if (
            _read_file_at(
                parent_fd,
                final_name,
                expected_uid=expected_uid,
                expected_gid=expected_gid,
            )
            != raw
        ):
            raise OSError("published bytes changed")
    except RelayActivationJournalError:
        if publication_started:
            raise RelayActivationJournalError(
                "activation_journal_recovery_required"
            ) from None
        raise
    except FileExistsError:
        raise RelayActivationJournalError(
            "activation_journal_recovery_required"
        ) from None
    except OSError:
        if publication_started:
            raise RelayActivationJournalError(
                "activation_journal_recovery_required"
            ) from None
        raise RelayActivationJournalError(
            "activation_journal_write_failed"
        ) from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)


class _ActivationJournalStore:
    """Private descriptor-anchored store used by the future controller."""

    def __init__(
        self,
        *,
        activation_fd: int,
        transactions_fd: int,
        receipts_fd: int,
        expected_uid: int,
        expected_gid: int,
    ) -> None:
        self.activation_fd = activation_fd
        self.transactions_fd = transactions_fd
        self.receipts_fd = receipts_fd
        self.expected_uid = expected_uid
        self.expected_gid = expected_gid
        self.closed = False

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        for descriptor in (
            self.receipts_fd,
            self.transactions_fd,
            self.activation_fd,
        ):
            try:
                os.close(descriptor)
            except OSError:
                pass

    def __enter__(self) -> "_ActivationJournalStore":
        if self.closed:
            raise RelayActivationJournalError("activation_journal_invalid")
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _plan_directory(
        self,
        plan_sha256: str,
        *,
        create: bool,
    ) -> int:
        if (
            self.closed
            or not isinstance(plan_sha256, str)
            or not _PLAN_DIRECTORY_PATTERN.fullmatch(plan_sha256)
        ):
            raise RelayActivationJournalError("activation_journal_invalid")
        if create:
            names = _bounded_directory_names(
                self.transactions_fd,
                MAX_JOURNAL_PLANS,
            )
            if (
                len(names) >= MAX_JOURNAL_PLANS
                or any(
                    _PLAN_DIRECTORY_PATTERN.fullmatch(name) is None
                    for name in names
                )
            ):
                raise RelayActivationJournalError(
                    "activation_journal_recovery_required"
                )
        return _open_directory_at(
            self.transactions_fd,
            plan_sha256,
            expected_uid=self.expected_uid,
            expected_gid=self.expected_gid,
            create=create,
            exclusive_create=create,
        )

    def _load_chain_from_fd(
        self,
        plan_fd: int,
        plan_sha256: str,
    ) -> tuple[ActivationJournalRevision, ...]:
        try:
            names = _bounded_directory_names(
                plan_fd,
                MAX_JOURNAL_REVISIONS,
            )
            if not names or len(names) > MAX_JOURNAL_REVISIONS:
                raise RelayActivationJournalError(
                    "activation_journal_recovery_required"
                )
            indexed: list[tuple[int, str]] = []
            for name in names:
                match = _REVISION_NAME_PATTERN.fullmatch(name)
                if match is None:
                    raise RelayActivationJournalError(
                        "activation_journal_recovery_required"
                    )
                indexed.append((int(match.group(1)), name))
            indexed.sort()
            if tuple(index for index, _name in indexed) != tuple(
                range(1, len(indexed) + 1)
            ):
                raise RelayActivationJournalError(
                    "activation_journal_recovery_required"
                )
            revisions = tuple(
                parse_activation_revision(
                    _read_file_at(
                        plan_fd,
                        name,
                        expected_uid=self.expected_uid,
                        expected_gid=self.expected_gid,
                    )
                )
                for _index, name in indexed
            )
            if (
                _bounded_directory_names(
                    plan_fd,
                    MAX_JOURNAL_REVISIONS,
                )
                != names
            ):
                raise RelayActivationJournalError(
                    "activation_journal_recovery_required"
                )
            validate_activation_revision_chain(revisions)
            if revisions[0].identity.plan_sha256 != plan_sha256:
                raise RelayActivationJournalError(
                    "activation_journal_recovery_required"
                )
            return revisions
        except RelayActivationJournalError as exc:
            if exc.error_id == "activation_journal_invalid":
                raise RelayActivationJournalError(
                    "activation_journal_recovery_required"
                ) from None
            raise
        except OSError:
            raise RelayActivationJournalError(
                "activation_journal_recovery_required"
            ) from None

    def _load_chain(
        self,
        plan_sha256: str,
    ) -> tuple[ActivationJournalRevision, ...]:
        plan_fd = self._plan_directory(plan_sha256, create=False)
        try:
            return self._load_chain_from_fd(plan_fd, plan_sha256)
        finally:
            os.close(plan_fd)

    def _load_receipt(self, receipt_sha256: str) -> ActivationJournalReceipt:
        if (
            not isinstance(receipt_sha256, str)
            or not SHA256_PATTERN.fullmatch(receipt_sha256)
        ):
            raise RelayActivationJournalError(
                "activation_journal_recovery_required"
            )
        try:
            receipt = parse_activation_receipt(
                _read_file_at(
                    self.receipts_fd,
                    f"{receipt_sha256}.json",
                    expected_uid=self.expected_uid,
                    expected_gid=self.expected_gid,
                )
            )
        except RelayActivationJournalError:
            raise RelayActivationJournalError(
                "activation_journal_recovery_required"
            ) from None
        if receipt.receipt_sha256 != receipt_sha256:
            raise RelayActivationJournalError(
                "activation_journal_recovery_required"
            )
        return receipt

    def _load_recovery_snapshot(
        self,
        plan_sha256: str,
    ) -> ActivationJournalRecoverySnapshot:
        """Load one exact chain plus its optional terminal-bound receipt."""

        revisions = self._load_chain(plan_sha256)
        last = revisions[-1]
        receipt_names = _bounded_directory_names(
            self.receipts_fd,
            MAX_JOURNAL_RECEIPTS,
        )
        if (
            len(receipt_names) > MAX_JOURNAL_RECEIPTS
            or any(
                _RECEIPT_NAME_PATTERN.fullmatch(name) is None
                for name in receipt_names
            )
        ):
            raise RelayActivationJournalError(
                "activation_journal_recovery_required"
            )
        matches: list[ActivationJournalReceipt] = []
        for name in receipt_names:
            match = _RECEIPT_NAME_PATTERN.fullmatch(name)
            if match is None:
                raise RelayActivationJournalError(
                    "activation_journal_recovery_required"
                )
            receipt = self._load_receipt(match.group(1))
            if receipt.identity.plan_sha256 != plan_sha256:
                continue
            expected_terminal_revision = (
                last.revision
                if last.phase == "terminal"
                else last.revision + 1
            )
            expected_previous_sha256 = (
                last.previous_revision_sha256
                if last.phase == "terminal"
                else last.record_sha256
            )
            if (
                not _identity_matches(receipt.identity, last.identity)
                or receipt.terminal_revision
                != expected_terminal_revision
                or receipt.previous_revision_sha256
                != expected_previous_sha256
                or receipt.owns_enable != last.owns_enable
                or receipt.owns_start != last.owns_start
            ):
                raise RelayActivationJournalError(
                    "activation_journal_recovery_required"
                )
            matches.append(receipt)
        if len(matches) > 1:
            raise RelayActivationJournalError(
                "activation_journal_recovery_required"
            )
        receipt = matches[0] if matches else None
        if last.phase == "terminal":
            if receipt is None:
                raise RelayActivationJournalError(
                    "activation_journal_recovery_required"
                )
            _validate_terminal_binding(last, receipt)
        elif receipt is not None:
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
            try:
                validate_activation_revision_chain(
                    (*revisions, terminal)
                )
                _validate_terminal_binding(terminal, receipt)
            except RelayActivationJournalError:
                raise RelayActivationJournalError(
                    "activation_journal_recovery_required"
                ) from None
        return ActivationJournalRecoverySnapshot(
            revisions=revisions,
            receipt=receipt,
        )

    def publish_revision(self, raw: bytes) -> dict[str, object]:
        revision = parse_activation_revision(raw)
        create_plan = revision.revision == 1
        plan_fd = self._plan_directory(
            revision.identity.plan_sha256,
            create=create_plan,
        )
        try:
            names = _bounded_directory_names(
                plan_fd,
                MAX_JOURNAL_REVISIONS,
            )
            if any(
                _REVISION_NAME_PATTERN.fullmatch(name) is None
                for name in names
            ):
                raise RelayActivationJournalError(
                    "activation_journal_recovery_required"
                )
            existing: tuple[ActivationJournalRevision, ...]
            if names:
                existing = self._load_chain_from_fd(
                    plan_fd,
                    revision.identity.plan_sha256
                )
            else:
                existing = ()
            if revision.revision != len(existing) + 1:
                raise RelayActivationJournalError(
                    "activation_journal_recovery_required"
                )
            candidate = (*existing, revision)
            validate_activation_revision_chain(candidate)
            if revision.phase == "terminal":
                receipt = self._load_receipt(
                    revision.receipt_sha256 or ""
                )
                _validate_terminal_binding(revision, receipt)
            final_name = f"revision-{revision.revision:06d}.json"
            _publish_bytes_at(
                plan_fd,
                final_name=final_name,
                temporary_name=f".{final_name}.tmp",
                raw=raw,
                expected_uid=self.expected_uid,
                expected_gid=self.expected_gid,
            )
        finally:
            os.close(plan_fd)
        return self.inspect_plan(revision.identity.plan_sha256)

    def publish_receipt(self, raw: bytes) -> dict[str, object]:
        receipt = parse_activation_receipt(raw)
        revisions = self._load_chain(receipt.identity.plan_sha256)
        last = revisions[-1]
        final_name = f"{receipt.receipt_sha256}.json"
        receipt_names = _bounded_directory_names(
            self.receipts_fd,
            MAX_JOURNAL_RECEIPTS,
        )
        if (
            len(receipt_names) > MAX_JOURNAL_RECEIPTS
            or any(
                _RECEIPT_NAME_PATTERN.fullmatch(name) is None
                for name in receipt_names
            )
            or (
                final_name not in receipt_names
                and len(receipt_names) >= MAX_JOURNAL_RECEIPTS
            )
        ):
            raise RelayActivationJournalError(
                "activation_journal_recovery_required"
            )
        if _name_exists(self.receipts_fd, final_name):
            existing = _read_file_at(
                self.receipts_fd,
                final_name,
                expected_uid=self.expected_uid,
                expected_gid=self.expected_gid,
            )
            if existing != raw:
                raise RelayActivationJournalError(
                    "activation_journal_recovery_required"
                )
            if last.phase == "terminal":
                _validate_terminal_binding(last, receipt)
            elif (
                receipt.terminal_revision != len(revisions) + 1
                or receipt.previous_revision_sha256 != last.record_sha256
                or not _identity_matches(receipt.identity, last.identity)
                or receipt.owns_enable != last.owns_enable
                or receipt.owns_start != last.owns_start
            ):
                raise RelayActivationJournalError(
                    "activation_journal_recovery_required"
                )
            return {
                "ok": True,
                "operation_id": "activate",
                "outcome": "existing",
                "receipt_sha256": receipt.receipt_sha256,
                "schema_id": ACTIVATION_JOURNAL_SCHEMA,
            }
        if (
            last.phase == "terminal"
            or receipt.terminal_revision != len(revisions) + 1
            or receipt.previous_revision_sha256 != last.record_sha256
            or not _identity_matches(receipt.identity, last.identity)
            or receipt.owns_enable != last.owns_enable
            or receipt.owns_start != last.owns_start
        ):
            raise RelayActivationJournalError(
                "activation_journal_recovery_required"
            )
        _publish_bytes_at(
            self.receipts_fd,
            final_name=final_name,
            temporary_name=f".{final_name}.tmp",
            raw=raw,
            expected_uid=self.expected_uid,
            expected_gid=self.expected_gid,
        )
        return {
            "ok": True,
            "operation_id": "activate",
            "outcome": "created",
            "receipt_sha256": receipt.receipt_sha256,
            "schema_id": ACTIVATION_JOURNAL_SCHEMA,
        }

    def inspect_plan(self, plan_sha256: str) -> dict[str, object]:
        try:
            revisions = self._load_chain(plan_sha256)
            last = revisions[-1]
            receipt = (
                self._load_receipt(last.receipt_sha256 or "")
                if last.phase == "terminal"
                else None
            )
            return project_activation_journal(revisions, receipt)
        except RelayActivationJournalError:
            return {
                "ok": False,
                "operation_id": "activate",
                "recovery_required": True,
                "schema_id": ACTIVATION_JOURNAL_SCHEMA,
                "state": "recovery_required",
            }

    def _snapshot_sha256(self, *, require_ready: bool) -> str:
        if (
            type(require_ready) is not bool
            or (
                require_ready
                and self.inspect_store().get("state") != "ready"
            )
        ):
            raise RelayActivationJournalError(
                "activation_journal_recovery_required"
            )
        plan_names = _bounded_directory_names(
            self.transactions_fd,
            MAX_JOURNAL_PLANS,
        )
        receipt_names = _bounded_directory_names(
            self.receipts_fd,
            MAX_JOURNAL_RECEIPTS,
        )
        plans: list[dict[str, object]] = []
        for plan_sha256 in plan_names:
            plan_fd = self._plan_directory(plan_sha256, create=False)
            try:
                before = _fingerprint(os.fstat(plan_fd))
                revision_names = _bounded_directory_names(
                    plan_fd,
                    MAX_JOURNAL_REVISIONS,
                )
                records = []
                for name in revision_names:
                    raw = _read_file_at(
                        plan_fd,
                        name,
                        expected_uid=self.expected_uid,
                        expected_gid=self.expected_gid,
                    )
                    records.append(
                        {
                            "content_sha256": _sha256(raw),
                            "metadata": _fingerprint(
                                os.stat(
                                    name,
                                    dir_fd=plan_fd,
                                    follow_symlinks=False,
                                )
                            ),
                            "name": name,
                        }
                    )
                after = _fingerprint(os.fstat(plan_fd))
                if (
                    before != after
                    or _bounded_directory_names(
                        plan_fd,
                        MAX_JOURNAL_REVISIONS,
                    )
                    != revision_names
                ):
                    raise RelayActivationJournalError(
                        "activation_journal_recovery_required"
                    )
                plans.append(
                    {
                        "directory_metadata": before,
                        "plan_sha256": plan_sha256,
                        "records": records,
                    }
                )
            finally:
                os.close(plan_fd)
        receipts = []
        for name in receipt_names:
            raw = _read_file_at(
                self.receipts_fd,
                name,
                expected_uid=self.expected_uid,
                expected_gid=self.expected_gid,
            )
            receipts.append(
                {
                    "content_sha256": _sha256(raw),
                    "metadata": _fingerprint(
                        os.stat(
                            name,
                            dir_fd=self.receipts_fd,
                            follow_symlinks=False,
                        )
                    ),
                    "name": name,
                }
            )
        if (
            _bounded_directory_names(
                self.transactions_fd,
                MAX_JOURNAL_PLANS,
            )
            != plan_names
            or _bounded_directory_names(
                self.receipts_fd,
                MAX_JOURNAL_RECEIPTS,
            )
            != receipt_names
        ):
            raise RelayActivationJournalError(
                "activation_journal_recovery_required"
            )
        return _sha256(
            _canonical_json(
                {
                    "activation_metadata": _fingerprint(
                        os.fstat(self.activation_fd)
                    ),
                    "plans": plans,
                    "receipts": receipts,
                    "receipts_metadata": _fingerprint(
                        os.fstat(self.receipts_fd)
                    ),
                    "transactions_metadata": _fingerprint(
                        os.fstat(self.transactions_fd)
                    ),
                }
            )
        )

    def snapshot_sha256(self) -> str:
        return self._snapshot_sha256(require_ready=True)

    def identity_summary(self) -> dict[str, tuple[str, ...]]:
        if self.inspect_store().get("state") != "ready":
            raise RelayActivationJournalError(
                "activation_journal_recovery_required"
            )
        release_ids: set[str] = set()
        unit_ids: set[str] = set()
        version_ids: set[str] = set()
        for plan_sha256 in _bounded_directory_names(
            self.transactions_fd,
            MAX_JOURNAL_PLANS,
        ):
            identity = self._load_chain(plan_sha256)[0].identity
            release_ids.add(identity.release_id)
            unit_ids.add(identity.unit_id)
            version_ids.add(identity.version_id)
        return {
            "release_ids": tuple(sorted(release_ids)),
            "unit_ids": tuple(sorted(unit_ids)),
            "version_ids": tuple(sorted(version_ids)),
        }

    def inspect_store(self) -> dict[str, object]:
        if self.closed:
            raise RelayActivationJournalError("activation_journal_invalid")
        try:
            plan_names = _bounded_directory_names(
                self.transactions_fd,
                MAX_JOURNAL_PLANS,
            )
            receipt_names = _bounded_directory_names(
                self.receipts_fd,
                MAX_JOURNAL_RECEIPTS,
            )
        except RelayActivationJournalError:
            return {
                "ok": False,
                "operation_id": "activate",
                "recovery_required": True,
                "schema_id": ACTIVATION_JOURNAL_SCHEMA,
                "state": "recovery_required",
            }
        if (
            len(plan_names) > MAX_JOURNAL_PLANS
            or len(receipt_names) > MAX_JOURNAL_RECEIPTS
            or any(
                _PLAN_DIRECTORY_PATTERN.fullmatch(name) is None
                for name in plan_names
            )
            or any(
                _RECEIPT_NAME_PATTERN.fullmatch(name) is None
                for name in receipt_names
            )
        ):
            return {
                "ok": False,
                "operation_id": "activate",
                "recovery_required": True,
                "schema_id": ACTIVATION_JOURNAL_SCHEMA,
                "state": "recovery_required",
            }
        referenced_receipts: set[str] = set()
        completed = 0
        for plan_sha256 in sorted(plan_names):
            result = self.inspect_plan(plan_sha256)
            if result.get("recovery_required") is True:
                return result
            receipt_sha256 = result.get("receipt_sha256")
            if not isinstance(receipt_sha256, str):
                return {
                    "ok": False,
                    "operation_id": "activate",
                    "recovery_required": True,
                    "schema_id": ACTIVATION_JOURNAL_SCHEMA,
                    "state": "recovery_required",
                }
            if receipt_sha256 in referenced_receipts:
                return {
                    "ok": False,
                    "operation_id": "activate",
                    "recovery_required": True,
                    "schema_id": ACTIVATION_JOURNAL_SCHEMA,
                    "state": "recovery_required",
                }
            referenced_receipts.add(receipt_sha256)
            completed += 1
        receipt_hashes = {
            match.group(1)
            for name in receipt_names
            if (match := _RECEIPT_NAME_PATTERN.fullmatch(name)) is not None
        }
        if receipt_hashes != referenced_receipts:
            return {
                "ok": False,
                "operation_id": "activate",
                "recovery_required": True,
                "schema_id": ACTIVATION_JOURNAL_SCHEMA,
                "state": "recovery_required",
            }
        return {
            "completed_transaction_count": completed,
            "ok": True,
            "operation_id": "activate",
            "recovery_required": False,
            "schema_id": ACTIVATION_JOURNAL_SCHEMA,
            "state": "ready",
        }


def inspect_activation_journal_directory(
    activation_fd: int,
    *,
    expected_uid: int,
    expected_gid: int,
) -> dict[str, object]:
    """Inspect one already-open activation directory without host mutation."""

    recovery = {
        "ok": False,
        "operation_id": "activate",
        "recovery_required": True,
        "schema_id": ACTIVATION_JOURNAL_SCHEMA,
        "state": "recovery_required",
    }
    if (
        type(activation_fd) is not int
        or activation_fd < 0
        or type(expected_uid) is not int
        or expected_uid < 0
        or type(expected_gid) is not int
        or expected_gid < 0
    ):
        return recovery
    owned_activation_fd = -1
    transactions_fd = -1
    receipts_fd = -1
    store: _ActivationJournalStore | None = None
    try:
        owned_activation_fd = os.dup(activation_fd)
        activation_metadata = os.fstat(owned_activation_fd)
        _validate_directory_metadata(
            activation_metadata,
            expected_uid=expected_uid,
            expected_gid=expected_gid,
        )
        if _bounded_directory_names(owned_activation_fd, 2) != (
            "receipts",
            "transactions",
        ):
            raise RelayActivationJournalError(
                "activation_journal_recovery_required"
            )
        transactions_fd = _open_directory_at(
            owned_activation_fd,
            "transactions",
            expected_uid=expected_uid,
            expected_gid=expected_gid,
            create=False,
        )
        receipts_fd = _open_directory_at(
            owned_activation_fd,
            "receipts",
            expected_uid=expected_uid,
            expected_gid=expected_gid,
            create=False,
        )
        store = _ActivationJournalStore(
            activation_fd=owned_activation_fd,
            transactions_fd=transactions_fd,
            receipts_fd=receipts_fd,
            expected_uid=expected_uid,
            expected_gid=expected_gid,
        )
        owned_activation_fd = -1
        transactions_fd = -1
        receipts_fd = -1
        result = store.inspect_store()
        snapshot_sha256 = (
            store.snapshot_sha256()
            if result.get("state") == "ready"
            else None
        )
        identity_summary = (
            store.identity_summary()
            if result.get("state") == "ready"
            else None
        )
        if (
            _fingerprint(os.fstat(store.activation_fd))
            != _fingerprint(activation_metadata)
            or _bounded_directory_names(store.activation_fd, 2)
            != ("receipts", "transactions")
            or _fingerprint(
                os.stat(
                    "transactions",
                    dir_fd=store.activation_fd,
                    follow_symlinks=False,
                )
            )
            != _fingerprint(os.fstat(store.transactions_fd))
            or _fingerprint(
                os.stat(
                    "receipts",
                    dir_fd=store.activation_fd,
                    follow_symlinks=False,
                )
            )
            != _fingerprint(os.fstat(store.receipts_fd))
        ):
            return recovery
        if snapshot_sha256 is None or identity_summary is None:
            return result
        return {
            **result,
            **identity_summary,
            "snapshot_sha256": snapshot_sha256,
        }
    except (OSError, RelayActivationJournalError):
        return recovery
    finally:
        if store is not None:
            store.close()
        else:
            for descriptor in (
                receipts_fd,
                transactions_fd,
                owned_activation_fd,
            ):
                if descriptor >= 0:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass


_PRODUCTION_ADMIN_PARTS = ("var", "lib", "agentops-relayctl")


def _validate_lifecycle_lock_metadata(
    metadata: os.stat_result,
    *,
    expected_uid: int,
    expected_gid: int,
) -> None:
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != expected_uid
        or metadata.st_gid != expected_gid
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
        or metadata.st_size != 0
    ):
        raise RelayActivationJournalError("activation_journal_invalid")


class _LockedActivationJournalStore:
    """Production journal store that owns the lifecycle lock."""

    def __init__(
        self,
        *,
        root_path: Path,
        root_fd: int,
        root_metadata: os.stat_result,
        admin_fd: int,
        lock_fd: int,
        lock_metadata: os.stat_result,
        store: _ActivationJournalStore,
    ) -> None:
        self.root_path = root_path
        self.root_fd = root_fd
        self.root_metadata = root_metadata
        self.admin_fd = admin_fd
        self.lock_fd = lock_fd
        self.lock_metadata = lock_metadata
        self._store = store
        self.closed = False

    def __enter__(self) -> "_LockedActivationJournalStore":
        if self.closed:
            raise RelayActivationJournalError("activation_journal_invalid")
        return self

    def __del__(self) -> None:
        try:
            self._close(validate=False)
        except Exception:
            pass

    def __exit__(
        self,
        exc_type: object,
        _exc: object,
        _traceback: object,
    ) -> None:
        try:
            self.close()
        except RelayActivationJournalError:
            if exc_type is None:
                raise

    def close(self) -> None:
        self._close(validate=True)

    def _close(self, *, validate: bool) -> None:
        if self.closed:
            return
        validation_failed = False
        if validate:
            try:
                self._validate_bindings()
            except (OSError, RelayActivationJournalError):
                validation_failed = True
        self.closed = True
        self._store.close()
        try:
            fcntl.flock(self.lock_fd, fcntl.LOCK_UN)
        except OSError:
            pass
        for descriptor in (self.lock_fd, self.admin_fd, self.root_fd):
            try:
                os.close(descriptor)
            except OSError:
                pass
        if validation_failed:
            raise RelayActivationJournalError(
                "activation_journal_recovery_required"
            )

    def _validate_bindings(self) -> None:
        if self.closed:
            raise RelayActivationJournalError("activation_journal_invalid")
        current_root = os.lstat(self.root_path)
        if not (
            _fingerprint(current_root)
            == _fingerprint(os.fstat(self.root_fd))
            == _fingerprint(self.root_metadata)
        ):
            raise RelayActivationJournalError(
                "activation_journal_recovery_required"
            )
        reopened_admin = _open_existing_directory_chain(
            self.root_fd,
            _PRODUCTION_ADMIN_PARTS,
            expected_uid=self.root_metadata.st_uid,
            expected_gid=self.root_metadata.st_gid,
        )
        try:
            if _fingerprint(os.fstat(reopened_admin)) != _fingerprint(
                os.fstat(self.admin_fd)
            ):
                raise RelayActivationJournalError(
                    "activation_journal_recovery_required"
                )
        finally:
            os.close(reopened_admin)
        lock_path = os.stat(
            "lifecycle.lock",
            dir_fd=self.admin_fd,
            follow_symlinks=False,
        )
        lock_opened = os.fstat(self.lock_fd)
        _validate_lifecycle_lock_metadata(
            lock_opened,
            expected_uid=self.root_metadata.st_uid,
            expected_gid=self.root_metadata.st_gid,
        )
        if not (
            _fingerprint(lock_path)
            == _fingerprint(lock_opened)
            == _fingerprint(self.lock_metadata)
        ):
            raise RelayActivationJournalError(
                "activation_journal_recovery_required"
            )
        if _bounded_directory_names(self.admin_fd, 2) != (
            "activation",
            "lifecycle.lock",
        ):
            raise RelayActivationJournalError(
                "activation_journal_recovery_required"
            )
        if (
            _fingerprint(
                os.stat(
                    "activation",
                    dir_fd=self.admin_fd,
                    follow_symlinks=False,
                )
            )
            != _fingerprint(os.fstat(self._store.activation_fd))
            or _bounded_directory_names(self._store.activation_fd, 2)
            != ("receipts", "transactions")
            or _fingerprint(
                os.stat(
                    "transactions",
                    dir_fd=self._store.activation_fd,
                    follow_symlinks=False,
                )
            )
            != _fingerprint(os.fstat(self._store.transactions_fd))
            or _fingerprint(
                os.stat(
                    "receipts",
                    dir_fd=self._store.activation_fd,
                    follow_symlinks=False,
                )
            )
            != _fingerprint(os.fstat(self._store.receipts_fd))
        ):
            raise RelayActivationJournalError(
                "activation_journal_recovery_required"
            )

    def _guarded(self, callback):
        if self.closed:
            raise RelayActivationJournalError(
                "activation_journal_invalid"
            )
        try:
            self._validate_bindings()
        except (OSError, RelayActivationJournalError):
            raise RelayActivationJournalError(
                "activation_journal_recovery_required"
            ) from None
        try:
            result = callback()
        except RelayActivationJournalError:
            try:
                self._validate_bindings()
            except (OSError, RelayActivationJournalError):
                raise RelayActivationJournalError(
                    "activation_journal_recovery_required"
                ) from None
            raise
        except OSError:
            try:
                self._validate_bindings()
            except (OSError, RelayActivationJournalError):
                pass
            raise RelayActivationJournalError(
                "activation_journal_recovery_required"
            ) from None
        try:
            self._validate_bindings()
        except (OSError, RelayActivationJournalError):
            raise RelayActivationJournalError(
                "activation_journal_recovery_required"
            ) from None
        return result

    def inspect_plan(self, plan_sha256: str) -> dict[str, object]:
        return self._guarded(lambda: self._store.inspect_plan(plan_sha256))

    def inspect_store(self) -> dict[str, object]:
        return self._guarded(self._store.inspect_store)

    def snapshot_sha256(self) -> str:
        return self._guarded(self._store.snapshot_sha256)

    def _recovery_snapshot_sha256(self) -> str:
        return self._guarded(
            lambda: self._store._snapshot_sha256(
                require_ready=False
            )
        )

    def identity_summary(self) -> dict[str, tuple[str, ...]]:
        return self._guarded(self._store.identity_summary)

    def _load_recovery_snapshot(
        self,
        plan_sha256: str,
    ) -> ActivationJournalRecoverySnapshot:
        return self._guarded(
            lambda: self._store._load_recovery_snapshot(plan_sha256)
        )

    def _activation_scan_capability(
        self,
    ) -> "_LockedActivationScanCapability":
        return self._guarded(
            lambda: _LockedActivationScanCapability(self)
        )

    def publish_revision(self, raw: bytes) -> dict[str, object]:
        return self._guarded(lambda: self._store.publish_revision(raw))

    def publish_receipt(self, raw: bytes) -> dict[str, object]:
        return self._guarded(lambda: self._store.publish_receipt(raw))


class _LockedActivationScanCapability:
    """Prove that one exact root is scanned under its live lifecycle lock."""

    def __init__(self, session: _LockedActivationJournalStore) -> None:
        if type(session) is not _LockedActivationJournalStore:
            raise RelayActivationJournalError(
                "activation_journal_invalid"
            )
        self.__session = session

    def _snapshot_sha256_for_root(self, root: Path) -> str:
        if (
            not isinstance(root, Path)
            or not root.is_absolute()
            or root != self.__session.root_path
        ):
            raise RelayActivationJournalError(
                "activation_journal_recovery_required"
            )
        return self.__session._recovery_snapshot_sha256()


def _acquire_locked_production_store(
    root: Path,
) -> _LockedActivationJournalStore:
    """Open the production namespace while owning its lifecycle lock."""

    if not isinstance(root, Path) or not root.is_absolute():
        raise RelayActivationJournalError("activation_journal_invalid")
    root_fd = -1
    admin_fd = -1
    lock_fd = -1
    activation_fd = -1
    transactions_fd = -1
    receipts_fd = -1
    store: _ActivationJournalStore | None = None
    session: _LockedActivationJournalStore | None = None
    lock_acquired = False
    success = False
    try:
        before = os.lstat(root)
        root_fd = os.open(root, _directory_flags())
        opened = os.fstat(root_fd)
        after = os.lstat(root)
        if not (
            _fingerprint(before)
            == _fingerprint(opened)
            == _fingerprint(after)
            and stat.S_ISDIR(opened.st_mode)
            and opened.st_uid in {0, os.geteuid()}
            and not stat.S_IMODE(opened.st_mode) & 0o022
        ):
            raise RelayActivationJournalError(
                "activation_journal_invalid"
            )
        expected_uid = opened.st_uid
        expected_gid = opened.st_gid
        admin_fd = _open_existing_directory_chain(
            root_fd,
            _PRODUCTION_ADMIN_PARTS,
            expected_uid=expected_uid,
            expected_gid=expected_gid,
        )
        before_lock = os.stat(
            "lifecycle.lock",
            dir_fd=admin_fd,
            follow_symlinks=False,
        )
        _validate_lifecycle_lock_metadata(
            before_lock,
            expected_uid=expected_uid,
            expected_gid=expected_gid,
        )
        lock_fd = os.open(
            "lifecycle.lock",
            (
                os.O_RDWR
                | _file_flags()
                | getattr(os, "O_NONBLOCK", 0)
            ),
            dir_fd=admin_fd,
        )
        opened_lock = os.fstat(lock_fd)
        after_lock = os.stat(
            "lifecycle.lock",
            dir_fd=admin_fd,
            follow_symlinks=False,
        )
        _validate_lifecycle_lock_metadata(
            opened_lock,
            expected_uid=expected_uid,
            expected_gid=expected_gid,
        )
        if not (
            _fingerprint(before_lock)
            == _fingerprint(opened_lock)
            == _fingerprint(after_lock)
        ):
            raise RelayActivationJournalError(
                "activation_journal_invalid"
            )
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RelayActivationJournalError(
                "activation_journal_busy"
            ) from None
        lock_acquired = True
        if _fingerprint(
            os.stat(
                "lifecycle.lock",
                dir_fd=admin_fd,
                follow_symlinks=False,
            )
        ) != _fingerprint(os.fstat(lock_fd)):
            raise RelayActivationJournalError(
                "activation_journal_recovery_required"
            )
        admin_names = _bounded_directory_names(admin_fd, 2)
        if admin_names != ("activation", "lifecycle.lock"):
            raise RelayActivationJournalError(
                "activation_journal_recovery_required"
            )
        activation_fd = _open_directory_at(
            admin_fd,
            "activation",
            expected_uid=expected_uid,
            expected_gid=expected_gid,
            create=False,
        )
        activation_names = _bounded_directory_names(activation_fd, 2)
        if activation_names != ("receipts", "transactions"):
            raise RelayActivationJournalError(
                "activation_journal_recovery_required"
            )
        transactions_fd = _open_directory_at(
            activation_fd,
            "transactions",
            expected_uid=expected_uid,
            expected_gid=expected_gid,
            create=False,
        )
        receipts_fd = _open_directory_at(
            activation_fd,
            "receipts",
            expected_uid=expected_uid,
            expected_gid=expected_gid,
            create=False,
        )
        store = _ActivationJournalStore(
            activation_fd=activation_fd,
            transactions_fd=transactions_fd,
            receipts_fd=receipts_fd,
            expected_uid=expected_uid,
            expected_gid=expected_gid,
        )
        activation_fd = -1
        transactions_fd = -1
        receipts_fd = -1
        session = _LockedActivationJournalStore(
            root_path=root,
            root_fd=root_fd,
            root_metadata=opened,
            admin_fd=admin_fd,
            lock_fd=lock_fd,
            lock_metadata=opened_lock,
            store=store,
        )
        root_fd = -1
        admin_fd = -1
        lock_fd = -1
        store = None
        session._validate_bindings()
        success = True
        return session
    except RelayActivationJournalError as exc:
        if (
            lock_acquired
            and exc.error_id == "activation_journal_invalid"
        ):
            raise RelayActivationJournalError(
                "activation_journal_recovery_required"
            ) from None
        raise
    except OSError:
        raise RelayActivationJournalError(
            (
                "activation_journal_recovery_required"
                if lock_acquired
                else "activation_journal_invalid"
            )
        ) from None
    finally:
        if not success:
            if session is not None:
                session._close(validate=False)
            elif store is not None:
                store.close()
            for descriptor in (
                receipts_fd,
                transactions_fd,
                activation_fd,
                lock_fd,
                admin_fd,
                root_fd,
            ):
                if descriptor >= 0:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass


@contextmanager
def _open_locked_production_store(root: Path):
    """Yield one production store for exactly one locked lexical scope."""

    session = _acquire_locked_production_store(root)
    try:
        yield session
    finally:
        session.close()


def _open_fixture_store(root: Path) -> _ActivationJournalStore:
    """Open an isolated private store for deterministic tests only."""

    if not isinstance(root, Path) or not root.is_absolute():
        raise RelayActivationJournalError("activation_journal_invalid")
    descriptor = -1
    activation_fd = -1
    transactions_fd = -1
    receipts_fd = -1
    success = False
    try:
        before = os.lstat(root)
        descriptor = os.open(root, _directory_flags())
        opened = os.fstat(descriptor)
        after = os.lstat(root)
        if not (
            _fingerprint(before)
            == _fingerprint(opened)
            == _fingerprint(after)
        ):
            raise RelayActivationJournalError(
                "activation_journal_invalid"
            )
        expected_uid = opened.st_uid
        expected_gid = opened.st_gid
        _validate_directory_metadata(
            opened,
            expected_uid=expected_uid,
            expected_gid=expected_gid,
        )
        activation_fd = _open_directory_at(
            descriptor,
            "activation",
            expected_uid=expected_uid,
            expected_gid=expected_gid,
            create=True,
        )
        transactions_fd = _open_directory_at(
            activation_fd,
            "transactions",
            expected_uid=expected_uid,
            expected_gid=expected_gid,
            create=True,
        )
        receipts_fd = _open_directory_at(
            activation_fd,
            "receipts",
            expected_uid=expected_uid,
            expected_gid=expected_gid,
            create=True,
        )
        os.close(descriptor)
        descriptor = -1
        store = _ActivationJournalStore(
            activation_fd=activation_fd,
            transactions_fd=transactions_fd,
            receipts_fd=receipts_fd,
            expected_uid=expected_uid,
            expected_gid=expected_gid,
        )
        success = True
        return store
    except RelayActivationJournalError:
        raise
    except OSError:
        raise RelayActivationJournalError(
            "activation_journal_invalid"
        ) from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if not success:
            for child in (receipts_fd, transactions_fd, activation_fd):
                if child >= 0:
                    try:
                        os.close(child)
                    except OSError:
                        pass
