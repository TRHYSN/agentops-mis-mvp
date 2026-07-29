#!/usr/bin/env python3
"""Exercise the read-only Relay activation recovery preview controller."""
from __future__ import annotations

import ast
import json
import sys
import tempfile
from dataclasses import replace
from pathlib import Path


sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agentops_mis_cli.relay_activation import (  # noqa: E402
    compile_activation_plan,
)
from agentops_mis_cli.relay_activation_evidence import (  # noqa: E402
    build_activation_journal_identity,
)
from agentops_mis_cli.relay_activation_journal import (  # noqa: E402
    GENESIS_REVISION_SHA256,
    _open_fixture_store,
    build_activation_revision,
    parse_activation_revision,
)
from agentops_mis_cli.relay_activation_recovery_preview import (  # noqa: E402
    RelayActivationRecoveryPreviewError,
    _preview_activation_recovery_with,
)
from agentops_mis_cli.relay_admin import (  # noqa: E402
    EXPECTED_WHEEL_MODULES,
)
from agentops_mis_cli.relay_systemd_read import (  # noqa: E402
    RelaySystemdShowError,
)
from scripts.relay_activation_recovery_decision_smoke import (  # noqa: E402
    prerequisites,
    systemd,
)


PRIVATE_CANARY = "RECOVERY_PREVIEW_PRIVATE_CANARY"


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def expect_error(
    callback,
    *,
    expected: str,
    label: str,
    failures: list[str],
) -> bool:
    try:
        callback()
    except RelayActivationRecoveryPreviewError as exc:
        if exc.error_id != expected or PRIVATE_CANARY in str(exc):
            failures.append(f"{label}: wrong bounded error")
            return False
        return True
    failures.append(f"{label}: unexpectedly succeeded")
    return False


def main() -> int:
    failures: list[str] = []
    before = prerequisites()
    observed_systemd = systemd(need_reload=True)
    plan = compile_activation_plan(before, observed_systemd)
    require(
        plan.ok is True and plan.plan_sha256 is not None,
        "preview fixture plan did not compile",
        failures,
    )
    plan_sha256 = str(plan.plan_sha256)
    identity = build_activation_journal_identity(
        before,
        observed_systemd,
        confirmed_plan_sha256=plan_sha256,
    )
    prepared = build_activation_revision(
        identity,
        revision=1,
        previous_revision_sha256=GENESIS_REVISION_SHA256,
        phase="prepared",
        step_id="transaction_open",
    )

    stable_scans = 0
    stable_loads = 0
    with tempfile.TemporaryDirectory(
        prefix="relay-recovery-preview-"
    ) as temporary:
        root = Path(temporary)
        root.chmod(0o700)
        with _open_fixture_store(root) as store:
            store.publish_revision(prepared)

            def stable_loader(candidate: str):
                nonlocal stable_loads
                stable_loads += 1
                return store._load_recovery_snapshot(candidate)

            def stable_scanner():
                nonlocal stable_scans
                stable_scans += 1
                return before

            projection = _preview_activation_recovery_with(
                plan_sha256,
                "resume",
                snapshot_loader=stable_loader,
                scanner=stable_scanner,
                systemd_reader=lambda candidate: (
                    observed_systemd if candidate == before else None
                ),
            )
            stable_again = _preview_activation_recovery_with(
                plan_sha256,
                "resume",
                snapshot_loader=store._load_recovery_snapshot,
                scanner=lambda: before,
                systemd_reader=lambda _candidate: observed_systemd,
            )
            require(
                projection.get("ok") is True
                and projection.get("action_id") == "resume"
                and projection.get("operation_id") == "run_step"
                and projection.get("step_id") == "daemon_reload"
                and stable_scans == 2
                and stable_loads == 2,
                "stable preview did not compose exact reads",
                failures,
            )
            require(
                stable_again.get("decision_sha256")
                == projection.get("decision_sha256"),
                "stable preview decision hash changed",
                failures,
            )

            changed = replace(
                before,
                trusted_parent_chain_sha256="8" * 64,
            )
            scan_values = iter((before, changed))
            prerequisite_drift_rejected = expect_error(
                lambda: _preview_activation_recovery_with(
                    plan_sha256,
                    "resume",
                    snapshot_loader=store._load_recovery_snapshot,
                    scanner=lambda: next(scan_values),
                    systemd_reader=lambda _candidate: observed_systemd,
                ),
                expected="activation_prerequisite_changed",
                label="prerequisite drift",
                failures=failures,
            )

            intent = build_activation_revision(
                identity,
                revision=2,
                previous_revision_sha256=(
                    parse_activation_revision(prepared).record_sha256
                ),
                phase="intent",
                step_id="daemon_reload",
                intent_id="daemon_reload_requested",
            )

            def journal_drift_reader(_candidate):
                store.publish_revision(intent)
                return observed_systemd

            journal_drift_rejected = expect_error(
                lambda: _preview_activation_recovery_with(
                    plan_sha256,
                    "resume",
                    snapshot_loader=store._load_recovery_snapshot,
                    scanner=lambda: before,
                    systemd_reader=journal_drift_reader,
                ),
                expected="activation_recovery_required",
                label="journal drift",
                failures=failures,
            )

    callback_calls = 0

    def should_not_run():
        nonlocal callback_calls
        callback_calls += 1
        return before

    invalid_plan_rejected = expect_error(
        lambda: _preview_activation_recovery_with(
            "not-a-plan",
            "resume",
            snapshot_loader=should_not_run,
            scanner=should_not_run,
            systemd_reader=lambda _candidate: observed_systemd,
        ),
        expected="activation_recovery_preview_invalid",
        label="invalid plan",
        failures=failures,
    )
    invalid_outcome_rejected = expect_error(
        lambda: _preview_activation_recovery_with(
            plan_sha256,
            "guess",
            snapshot_loader=should_not_run,
            scanner=should_not_run,
            systemd_reader=lambda _candidate: observed_systemd,
        ),
        expected="activation_recovery_preview_invalid",
        label="invalid outcome",
        failures=failures,
    )
    require(
        callback_calls == 0,
        "invalid input reached host callbacks",
        failures,
    )

    class FailingSystemdReader:
        def __call__(self, _candidate):
            raise RelaySystemdShowError()

    with tempfile.TemporaryDirectory(
        prefix="relay-recovery-preview-systemd-"
    ) as temporary:
        root = Path(temporary)
        root.chmod(0o700)
        with _open_fixture_store(root) as store:
            store.publish_revision(prepared)
            systemd_failure_rejected = expect_error(
                lambda: _preview_activation_recovery_with(
                    plan_sha256,
                    "resume",
                    snapshot_loader=store._load_recovery_snapshot,
                    scanner=lambda: before,
                    systemd_reader=FailingSystemdReader(),
                ),
                expected="systemd_show_failed",
                label="systemd failure",
                failures=failures,
            )

    source_path = (
        ROOT
        / "agentops_mis_cli"
        / "relay_activation_recovery_preview.py"
    )
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_names = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    production_composition = {
        "_open_locked_production_store",
        "_scan_activation_prerequisites_while_locked",
        "read_systemd_show",
    }.issubset(imported_names) and "_activation_scan_capability" in source
    no_mutation_surface = (
        "_run_bound_systemd_mutation" not in imported_names
        and "publish_revision" not in source
        and "publish_receipt" not in source
        and "relay_admin" not in source
        and not any(
            isinstance(node, ast.FunctionDef)
            and node.name == "main"
            for node in ast.walk(tree)
        )
    )
    cli_trees = tuple(
        ast.parse(path.read_text(encoding="utf-8"))
        for path in (
            ROOT / "agentops_mis_cli" / "relay_admin.py",
            ROOT / "agentops_mis_cli" / "cli.py",
        )
    )
    cli_surface_exposed = any(
        (
            isinstance(node, ast.ImportFrom)
            and node.module
            == "agentops_mis_cli.relay_activation_recovery_preview"
        )
        or (
            isinstance(node, ast.Import)
            and any(
                alias.name
                == "agentops_mis_cli.relay_activation_recovery_preview"
                for alias in node.names
            )
        )
        or (
            isinstance(node, ast.Name)
            and node.id == "_preview_activation_recovery"
        )
        or (
            isinstance(node, ast.Attribute)
            and node.attr == "_preview_activation_recovery"
        )
        for cli_tree in cli_trees
        for node in ast.walk(cli_tree)
    )
    no_cli_surface = not cli_surface_exposed
    require(
        production_composition,
        "production preview did not retain locked composition",
        failures,
    )
    require(
        no_mutation_surface,
        "recovery preview exposed mutation or CLI composition",
        failures,
    )
    require(
        no_cli_surface,
        "recovery preview became reachable from a CLI",
        failures,
    )
    require(
        "agentops_mis_cli/relay_activation_recovery_preview.py"
        in EXPECTED_WHEEL_MODULES,
        "exact wheel module set omits recovery preview",
        failures,
    )

    public_text = json.dumps(
        projection,
        ensure_ascii=True,
        sort_keys=True,
    )
    require(
        PRIVATE_CANARY not in public_text
        and "/etc/" not in public_text
        and "/var/" not in public_text,
        "recovery preview exposed private payload",
        failures,
    )
    result = {
        "cli_surface_exposed": not no_cli_surface,
        "decision_hash_deterministic": (
            stable_again.get("decision_sha256")
            == projection.get("decision_sha256")
        ),
        "failures": failures,
        "invalid_input_zero_read": (
            invalid_plan_rejected
            and invalid_outcome_rejected
            and callback_calls == 0
        ),
        "journal_drift_rejected": journal_drift_rejected,
        "lifecycle_lock_composed": production_composition,
        "network_used": False,
        "ok": not failures,
        "operation": "relay_activation_recovery_preview_smoke",
        "prerequisite_drift_rejected": prerequisite_drift_rejected,
        "private_payload_omitted": PRIVATE_CANARY not in public_text,
        "systemd_failure_bounded": systemd_failure_rejected,
        "systemd_mutation_performed": False,
        "write_scope": "fixture_journal_only",
    }
    print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
