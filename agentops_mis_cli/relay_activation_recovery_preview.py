"""Read-only, lifecycle-lock-bound Relay activation recovery preview."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from agentops_mis_cli.relay_activation import (
    SHA256_PATTERN,
    ActivationPrerequisiteSnapshot,
    SystemdSnapshot,
)
from agentops_mis_cli.relay_activation_journal import (
    ActivationJournalRecoverySnapshot,
    RelayActivationJournalError,
    _open_locked_production_store,
)
from agentops_mis_cli.relay_activation_recovery import (
    ACTIVATION_RECOVERY_DECISION_SCHEMA,
    ActivationRecoveryDecision,
    RelayActivationRecoveryError,
    compile_activation_recovery_decision,
    project_activation_recovery_decision,
)
from agentops_mis_cli.relay_activation_scan import (
    MUTABLE_LEAF_RACE_ERROR_ID,
    RelayActivationScanError,
    _scan_activation_prerequisites_while_locked,
)
from agentops_mis_cli.relay_systemd_read import (
    RelaySystemdShowError,
    read_systemd_show,
)


_OUTCOMES = frozenset({"resume", "rollback"})
_ERROR_IDS = frozenset(
    {
        MUTABLE_LEAF_RACE_ERROR_ID,
        "activation_prerequisite_changed",
        "activation_prerequisite_scan_invalid",
        "activation_recovery_preview_busy",
        "activation_recovery_preview_failed",
        "activation_recovery_preview_invalid",
        "activation_recovery_required",
        "systemd_show_failed",
    }
)


class RelayActivationRecoveryPreviewError(Exception):
    """One bounded failure without private host or journal detail."""

    def __init__(self, error_id: str) -> None:
        if error_id not in _ERROR_IDS:
            error_id = "activation_recovery_preview_failed"
        self.error_id = error_id
        super().__init__(error_id)


SnapshotLoader = Callable[[str], ActivationJournalRecoverySnapshot]
Scanner = Callable[[], ActivationPrerequisiteSnapshot]
SystemdReader = Callable[[ActivationPrerequisiteSnapshot], SystemdSnapshot]


@dataclass(frozen=True)
class _ActivationRecoveryObservation:
    snapshot: ActivationJournalRecoverySnapshot
    prerequisites: ActivationPrerequisiteSnapshot
    systemd: SystemdSnapshot
    decision: ActivationRecoveryDecision


def _observe_activation_recovery_with(
    plan_sha256: str,
    requested_outcome: str,
    *,
    snapshot_loader: SnapshotLoader,
    scanner: Scanner,
    systemd_reader: SystemdReader,
) -> _ActivationRecoveryObservation:
    """Return one private stable observation without projecting its payload."""

    if (
        not isinstance(plan_sha256, str)
        or not SHA256_PATTERN.fullmatch(plan_sha256)
        or not isinstance(requested_outcome, str)
        or requested_outcome not in _OUTCOMES
        or not callable(snapshot_loader)
        or not callable(scanner)
        or not callable(systemd_reader)
    ):
        raise RelayActivationRecoveryPreviewError(
            "activation_recovery_preview_invalid"
        )
    try:
        snapshot_before = snapshot_loader(plan_sha256)
        if type(snapshot_before) is not ActivationJournalRecoverySnapshot:
            raise RelayActivationRecoveryPreviewError(
                "activation_recovery_required"
            )
        prerequisites_before = scanner()
        if not isinstance(
            prerequisites_before,
            ActivationPrerequisiteSnapshot,
        ):
            raise RelayActivationRecoveryPreviewError(
                "activation_prerequisite_scan_invalid"
            )
        systemd = systemd_reader(prerequisites_before)
        if not isinstance(systemd, SystemdSnapshot):
            raise RelayActivationRecoveryPreviewError(
                "systemd_show_failed"
            )
        prerequisites_after = scanner()
        if not isinstance(
            prerequisites_after,
            ActivationPrerequisiteSnapshot,
        ):
            raise RelayActivationRecoveryPreviewError(
                "activation_prerequisite_scan_invalid"
            )
        snapshot_after = snapshot_loader(plan_sha256)
        if (
            type(snapshot_after) is not ActivationJournalRecoverySnapshot
            or snapshot_after != snapshot_before
        ):
            raise RelayActivationRecoveryPreviewError(
                "activation_recovery_required"
            )
        if prerequisites_after != prerequisites_before:
            raise RelayActivationRecoveryPreviewError(
                "activation_prerequisite_changed"
            )
        decision = compile_activation_recovery_decision(
            snapshot_after,
            prerequisites_after,
            systemd,
            requested_outcome=requested_outcome,
        )
        return _ActivationRecoveryObservation(
            snapshot=snapshot_after,
            prerequisites=prerequisites_after,
            systemd=systemd,
            decision=decision,
        )
    except RelayActivationRecoveryPreviewError:
        raise
    except RelayActivationScanError as exc:
        raise RelayActivationRecoveryPreviewError(exc.error_id) from None
    except RelaySystemdShowError as exc:
        raise RelayActivationRecoveryPreviewError(exc.error_id) from None
    except (RelayActivationJournalError, RelayActivationRecoveryError):
        raise RelayActivationRecoveryPreviewError(
            "activation_recovery_required"
        ) from None
    except Exception:
        raise RelayActivationRecoveryPreviewError(
            "activation_recovery_preview_failed"
        ) from None


def _preview_activation_recovery_with(
    plan_sha256: str,
    requested_outcome: str,
    *,
    snapshot_loader: SnapshotLoader,
    scanner: Scanner,
    systemd_reader: SystemdReader,
) -> dict[str, object]:
    """Compile one stable recovery decision without writes or mutation."""

    try:
        observation = _observe_activation_recovery_with(
            plan_sha256,
            requested_outcome,
            snapshot_loader=snapshot_loader,
            scanner=scanner,
            systemd_reader=systemd_reader,
        )
        projection = project_activation_recovery_decision(
            observation.decision
        )
        projected_hash = projection.get("decision_sha256")
        if (
            projection.get("schema_id")
            != ACTIVATION_RECOVERY_DECISION_SCHEMA
            or projection.get("plan_sha256") != plan_sha256
            or projection.get("requested_outcome") != requested_outcome
            or not isinstance(projected_hash, str)
            or not SHA256_PATTERN.fullmatch(projected_hash)
        ):
            raise RelayActivationRecoveryPreviewError(
                "activation_recovery_required"
            )
        return projection
    except RelayActivationRecoveryPreviewError:
        raise
    except Exception:
        raise RelayActivationRecoveryPreviewError(
            "activation_recovery_preview_failed"
        ) from None


def _preview_activation_recovery(
    plan_sha256: str,
    requested_outcome: str,
) -> dict[str, object]:
    """Private production entrypoint; intentionally absent from the CLI."""

    try:
        with _open_locked_production_store(Path("/")) as store:
            capability = store._activation_scan_capability()
            return _preview_activation_recovery_with(
                plan_sha256,
                requested_outcome,
                snapshot_loader=store._load_recovery_snapshot,
                scanner=lambda: (
                    _scan_activation_prerequisites_while_locked(
                        capability
                    )
                ),
                systemd_reader=read_systemd_show,
            )
    except RelayActivationRecoveryPreviewError:
        raise
    except RelayActivationJournalError as exc:
        error_id = (
            "activation_recovery_preview_busy"
            if exc.error_id == "activation_journal_busy"
            else "activation_recovery_required"
        )
        raise RelayActivationRecoveryPreviewError(error_id) from None
    except Exception:
        raise RelayActivationRecoveryPreviewError(
            "activation_recovery_preview_failed"
        ) from None
