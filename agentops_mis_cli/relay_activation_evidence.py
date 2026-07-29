"""Compile bounded activation ownership evidence without storing raw state."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from agentops_mis_cli.relay_activation import (
    SHA256_PATTERN,
    UNIT_NAME,
    ActivationPrerequisiteSnapshot,
    FileIdentity,
    LinkIdentity,
    SystemdSnapshot,
    compile_activation_plan,
)
from agentops_mis_cli.relay_activation_journal import (
    GENESIS_REVISION_SHA256,
    ActivationJournalIdentity,
    RelayActivationJournalError,
    build_activation_revision,
)


ACTIVATION_EVIDENCE_SCHEMA = "agentops.relay.activation-evidence.v0"
ACTIVATION_UNIT_IDENTITY_SCHEMA = (
    "agentops.relay.activation-unit-identity.v0"
)
ACTIVATION_ENABLEMENT_INVENTORY_SCHEMA = (
    "agentops.relay.activation-enablement-inventory.v0"
)
_STEP_OBSERVATION_IDS = {
    "daemon_reload": "daemon_reload_observed",
    "enable": "enable_observed",
    "start": "start_observed",
    "verify": "verify_observed",
    "rollback_stop": "rollback_stop_observed",
    "rollback_disable": "rollback_disable_observed",
}


class RelayActivationEvidenceError(Exception):
    """One bounded failure for every evidence compilation error."""

    def __init__(self) -> None:
        self.error_id = "activation_evidence_invalid"
        super().__init__(self.error_id)


@dataclass(frozen=True)
class ActivationStepObservation:
    step_id: str
    observation_id: str
    observation_sha256: str


def _canonical_json(value: object) -> bytes:
    try:
        return (
            json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True)
            + "\n"
        ).encode("ascii")
    except (TypeError, UnicodeEncodeError, ValueError):
        raise RelayActivationEvidenceError() from None


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _file_identity_payload(identity: FileIdentity) -> dict[str, object]:
    return {
        "canonical_path": identity.canonical_path,
        "content_sha256": identity.content_sha256,
        "device_id": identity.device_id,
        "group_id": identity.group_id,
        "inode": identity.inode,
        "kind": identity.kind,
        "mode": identity.mode,
        "nlink": identity.nlink,
        "owner_id": identity.owner_id,
        "size": identity.size,
    }


def _link_identity_payload(identity: LinkIdentity) -> dict[str, object]:
    return {
        "canonical_path": identity.canonical_path,
        "device_id": identity.device_id,
        "group_id": identity.group_id,
        "inode": identity.inode,
        "kind": identity.kind,
        "nlink": identity.nlink,
        "owner_id": identity.owner_id,
        "target": identity.target,
    }


def _unit_identity_sha256(identity: FileIdentity) -> str:
    return _sha256(
        {
            "schema_id": ACTIVATION_UNIT_IDENTITY_SCHEMA,
            "unit": _file_identity_payload(identity),
            "unit_id": UNIT_NAME,
        }
    )


def _enablement_inventory_sha256(
    links: tuple[LinkIdentity, ...],
) -> str:
    return _sha256(
        {
            "links": tuple(
                _link_identity_payload(identity)
                for identity in links
            ),
            "schema_id": ACTIVATION_ENABLEMENT_INVENTORY_SCHEMA,
            "unit_id": UNIT_NAME,
        }
    )


def _validate_journal_identity(
    identity: ActivationJournalIdentity,
) -> None:
    try:
        build_activation_revision(
            identity,
            revision=1,
            previous_revision_sha256=GENESIS_REVISION_SHA256,
            phase="prepared",
            step_id="transaction_open",
        )
    except (RelayActivationJournalError, TypeError, ValueError):
        raise RelayActivationEvidenceError() from None


def build_activation_journal_identity(
    prerequisites: ActivationPrerequisiteSnapshot,
    systemd: SystemdSnapshot,
    *,
    confirmed_plan_sha256: str,
) -> ActivationJournalIdentity:
    """Bind one exact refreshed plan to the immutable journal identity."""

    try:
        if (
            not isinstance(confirmed_plan_sha256, str)
            or not SHA256_PATTERN.fullmatch(confirmed_plan_sha256)
        ):
            raise RelayActivationEvidenceError()
        plan = compile_activation_plan(prerequisites, systemd)
        if (
            plan.ok is not True
            or plan.state != "plan_ready"
            or plan.plan_sha256 != confirmed_plan_sha256
            or plan.release_id != prerequisites.release_id
            or plan.version_id != prerequisites.version_id
        ):
            raise RelayActivationEvidenceError()
        identity = ActivationJournalIdentity(
            plan_sha256=confirmed_plan_sha256,
            release_id=prerequisites.release_id,
            version_id=prerequisites.version_id,
            pre_unit_file_state=systemd.unit_file_state,
            pre_active_state=systemd.active_state,
            pre_enablement_inventory_sha256=(
                _enablement_inventory_sha256(
                    prerequisites.enablement_links
                )
            ),
            unit_identity_sha256=_unit_identity_sha256(
                prerequisites.unit
            ),
        )
        _validate_journal_identity(identity)
        return identity
    except RelayActivationEvidenceError:
        raise
    except Exception:
        raise RelayActivationEvidenceError() from None


def _validate_observation_state(
    identity: ActivationJournalIdentity,
    step_id: str,
    prerequisites: ActivationPrerequisiteSnapshot,
    systemd: SystemdSnapshot,
) -> tuple[str, str]:
    _validate_journal_identity(identity)
    if step_id not in _STEP_OBSERVATION_IDS:
        raise RelayActivationEvidenceError()
    current_plan = compile_activation_plan(prerequisites, systemd)
    unit_sha256 = _unit_identity_sha256(prerequisites.unit)
    inventory_sha256 = _enablement_inventory_sha256(
        prerequisites.enablement_links
    )
    if (
        current_plan.ok is not True
        or current_plan.release_id != identity.release_id
        or current_plan.version_id != identity.version_id
        or unit_sha256 != identity.unit_identity_sha256
        or systemd.need_daemon_reload
    ):
        raise RelayActivationEvidenceError()

    if step_id == "daemon_reload":
        valid = (
            systemd.unit_file_state == identity.pre_unit_file_state
            and systemd.active_state == identity.pre_active_state
            and inventory_sha256
            == identity.pre_enablement_inventory_sha256
        )
    elif step_id == "enable":
        valid = (
            identity.pre_unit_file_state == "disabled"
            and systemd.unit_file_state == "enabled"
            and systemd.active_state == identity.pre_active_state
            and inventory_sha256
            != identity.pre_enablement_inventory_sha256
        )
    elif step_id == "start":
        valid = (
            identity.pre_active_state == "inactive"
            and systemd.unit_file_state == "enabled"
            and systemd.active_state == "active"
        )
    elif step_id == "verify":
        valid = (
            systemd.unit_file_state == "enabled"
            and systemd.active_state == "active"
        )
    elif step_id == "rollback_stop":
        valid = (
            identity.pre_active_state == "inactive"
            and systemd.unit_file_state == "enabled"
            and systemd.active_state == "inactive"
        )
    else:
        valid = (
            identity.pre_unit_file_state == "disabled"
            and systemd.unit_file_state == "disabled"
            and systemd.active_state == identity.pre_active_state
            and inventory_sha256
            == identity.pre_enablement_inventory_sha256
        )
    if not valid:
        raise RelayActivationEvidenceError()
    return unit_sha256, inventory_sha256


def build_activation_step_observation(
    identity: ActivationJournalIdentity,
    *,
    step_id: str,
    prerequisites: ActivationPrerequisiteSnapshot,
    systemd: SystemdSnapshot,
) -> ActivationStepObservation:
    """Compile one strict post-step hash; never return the source payload."""

    try:
        unit_sha256, inventory_sha256 = (
            _validate_observation_state(
                identity,
                step_id,
                prerequisites,
                systemd,
            )
        )
        payload: dict[str, object] = {
            "schema_id": ACTIVATION_EVIDENCE_SCHEMA,
            "step_id": step_id,
            "unit_id": UNIT_NAME,
            "unit_identity_sha256": unit_sha256,
        }
        if step_id in {"enable", "rollback_disable"}:
            payload["enablement_inventory_sha256"] = inventory_sha256
        elif step_id == "start":
            payload["invocation_id"] = systemd.invocation_id
        elif step_id in {"daemon_reload", "rollback_stop"}:
            payload["active_state"] = systemd.active_state
            payload["need_daemon_reload"] = systemd.need_daemon_reload
            payload["unit_file_state"] = systemd.unit_file_state
        else:
            payload["enablement_inventory_sha256"] = inventory_sha256
            payload["systemd"] = {
                "active_state": systemd.active_state,
                "exec_main_status": systemd.exec_main_status,
                "invocation_id": systemd.invocation_id,
                "load_state": systemd.load_state,
                "main_pid": systemd.main_pid,
                "need_daemon_reload": systemd.need_daemon_reload,
                "result": systemd.result,
                "sub_state": systemd.sub_state,
                "unit_file_state": systemd.unit_file_state,
            }
        observation_sha256 = _sha256(payload)
        if not SHA256_PATTERN.fullmatch(observation_sha256):
            raise RelayActivationEvidenceError()
        return ActivationStepObservation(
            step_id=step_id,
            observation_id=_STEP_OBSERVATION_IDS[step_id],
            observation_sha256=observation_sha256,
        )
    except RelayActivationEvidenceError:
        raise
    except Exception:
        raise RelayActivationEvidenceError() from None


def build_activation_rollback_verification_observation(
    identity: ActivationJournalIdentity,
    *,
    prerequisites: ActivationPrerequisiteSnapshot,
    systemd: SystemdSnapshot,
) -> ActivationStepObservation:
    """Hash proof that rollback restored the exact pre-activation state."""

    try:
        _validate_journal_identity(identity)
        current_plan = compile_activation_plan(prerequisites, systemd)
        unit_sha256 = _unit_identity_sha256(prerequisites.unit)
        inventory_sha256 = _enablement_inventory_sha256(
            prerequisites.enablement_links
        )
        if (
            current_plan.ok is not True
            or current_plan.release_id != identity.release_id
            or current_plan.version_id != identity.version_id
            or unit_sha256 != identity.unit_identity_sha256
            or inventory_sha256
            != identity.pre_enablement_inventory_sha256
            or systemd.need_daemon_reload
            or systemd.unit_file_state
            != identity.pre_unit_file_state
            or systemd.active_state != identity.pre_active_state
        ):
            raise RelayActivationEvidenceError()
        payload = {
            "enablement_inventory_sha256": inventory_sha256,
            "recovery_outcome": "rollback",
            "schema_id": ACTIVATION_EVIDENCE_SCHEMA,
            "step_id": "verify",
            "systemd": {
                "active_state": systemd.active_state,
                "exec_main_status": systemd.exec_main_status,
                "invocation_id": systemd.invocation_id,
                "load_state": systemd.load_state,
                "main_pid": systemd.main_pid,
                "need_daemon_reload": systemd.need_daemon_reload,
                "result": systemd.result,
                "sub_state": systemd.sub_state,
                "unit_file_state": systemd.unit_file_state,
            },
            "unit_id": UNIT_NAME,
            "unit_identity_sha256": unit_sha256,
        }
        observation_sha256 = _sha256(payload)
        if not SHA256_PATTERN.fullmatch(observation_sha256):
            raise RelayActivationEvidenceError()
        return ActivationStepObservation(
            step_id="verify",
            observation_id="rollback_verified",
            observation_sha256=observation_sha256,
        )
    except RelayActivationEvidenceError:
        raise
    except Exception:
        raise RelayActivationEvidenceError() from None
