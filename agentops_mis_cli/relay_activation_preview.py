"""Read-only Relay activation preview controller."""
from __future__ import annotations

from typing import Callable

from agentops_mis_cli.relay_activation import (
    ActivationPrerequisiteSnapshot,
    SystemdSnapshot,
    compile_activation_plan,
    project_activation_plan,
)
from agentops_mis_cli.relay_activation_scan import (
    MUTABLE_LEAF_RACE_ERROR_ID,
    RelayActivationScanError,
    scan_activation_prerequisites,
)
from agentops_mis_cli.relay_systemd_read import (
    RelaySystemdShowError,
    read_systemd_show,
)


ACTIVATION_PREREQUISITE_CHANGED = "activation_prerequisite_changed"


class RelayActivationPreviewError(Exception):
    """Bounded activation preview failure without host detail."""

    def __init__(self, error_id: str) -> None:
        if error_id not in {
            MUTABLE_LEAF_RACE_ERROR_ID,
            "activation_prerequisite_scan_invalid",
            "systemd_show_failed",
            ACTIVATION_PREREQUISITE_CHANGED,
        }:
            error_id = "activation_preview_failed"
        self.error_id = error_id
        super().__init__(error_id)


Scanner = Callable[[], ActivationPrerequisiteSnapshot]
SystemdReader = Callable[[ActivationPrerequisiteSnapshot], SystemdSnapshot]


def _preview_activation_with(
    *,
    scanner: Scanner,
    systemd_reader: SystemdReader,
) -> dict[str, object]:
    try:
        before = scanner()
        if not isinstance(before, ActivationPrerequisiteSnapshot):
            raise RelayActivationPreviewError(
                "activation_prerequisite_scan_invalid"
            )
        systemd = systemd_reader(before)
        after = scanner()
        if not isinstance(after, ActivationPrerequisiteSnapshot):
            raise RelayActivationPreviewError(
                "activation_prerequisite_scan_invalid"
            )
        if before != after:
            raise RelayActivationPreviewError(
                ACTIVATION_PREREQUISITE_CHANGED
            )
        return project_activation_plan(
            compile_activation_plan(after, systemd)
        )
    except RelayActivationPreviewError:
        raise
    except RelayActivationScanError as exc:
        raise RelayActivationPreviewError(exc.error_id) from None
    except RelaySystemdShowError as exc:
        raise RelayActivationPreviewError(exc.error_id) from None
    except Exception:
        raise RelayActivationPreviewError("activation_preview_failed") from None


def preview_activation() -> dict[str, object]:
    """Build one production preview with no injectable public parameters."""

    return _preview_activation_with(
        scanner=scan_activation_prerequisites,
        systemd_reader=read_systemd_show,
    )
