#!/usr/bin/env python3
"""Exercise exact-confirmed, non-systemd Relay recovery writes."""
from __future__ import annotations

import ast
import json
import sys
import tempfile
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
    _open_fixture_store,
)
from agentops_mis_cli import (  # noqa: E402
    relay_activation_recovery_controller as controller_module,
)
from agentops_mis_cli.relay_activation_recovery_controller import (  # noqa: E402
    RelayActivationRecoveryControllerError,
    _run_confirmed_recovery_write,
    _run_confirmed_recovery_write_with,
)
from agentops_mis_cli.relay_activation_recovery_preview import (  # noqa: E402
    _preview_activation_recovery_with,
)
from agentops_mis_cli.relay_admin import (  # noqa: E402
    EXPECTED_WHEEL_MODULES,
)
from scripts.relay_activation_recovery_decision_smoke import (  # noqa: E402
    append_intent,
    append_observed,
    append_revision,
    prerequisites,
    systemd,
)


PRIVATE_CANARY = "RECOVERY_CONTROLLER_PRIVATE_CANARY"


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
    except RelayActivationRecoveryControllerError as exc:
        if exc.error_id != expected or PRIVATE_CANARY in str(exc):
            failures.append(f"{label}: wrong bounded error")
            return False
        return True
    failures.append(f"{label}: unexpectedly succeeded")
    return False


class CountingStore:
    def __init__(self, store) -> None:
        self.store = store
        self.loads = 0
        self.revision_writes = 0
        self.receipt_writes = 0
        self.fail_after_revision = False

    def reset(self) -> None:
        self.loads = 0
        self.revision_writes = 0
        self.receipt_writes = 0
        self.fail_after_revision = False

    def _load_recovery_snapshot(self, plan_sha256: str):
        self.loads += 1
        return self.store._load_recovery_snapshot(plan_sha256)

    def publish_revision(self, raw: bytes):
        self.revision_writes += 1
        result = self.store.publish_revision(raw)
        if self.fail_after_revision:
            raise RuntimeError(PRIVATE_CANARY)
        return result

    def publish_receipt(self, raw: bytes):
        self.receipt_writes += 1
        return self.store.publish_receipt(raw)


def preview(
    store: CountingStore,
    plan_sha256: str,
    outcome: str,
    current_prerequisites,
    current_systemd,
) -> dict[str, object]:
    return _preview_activation_recovery_with(
        plan_sha256,
        outcome,
        snapshot_loader=store._load_recovery_snapshot,
        scanner=lambda: current_prerequisites,
        systemd_reader=lambda candidate: (
            current_systemd
            if candidate == current_prerequisites
            else None
        ),
    )


def run(
    store: CountingStore,
    plan_sha256: str,
    outcome: str,
    decision_sha256: str,
    current_prerequisites,
    current_systemd,
) -> dict[str, object]:
    return _run_confirmed_recovery_write_with(
        plan_sha256,
        outcome,
        decision_sha256,
        store=store,
        scanner=lambda: current_prerequisites,
        systemd_reader=lambda candidate: (
            current_systemd
            if candidate == current_prerequisites
            else None
        ),
    )


def build_records():
    pre_prerequisites = prerequisites()
    pre_systemd = systemd(need_reload=True)
    plan = compile_activation_plan(pre_prerequisites, pre_systemd)
    if plan.ok is not True or plan.plan_sha256 is None:
        raise AssertionError("fixture plan did not compile")
    plan_sha256 = str(plan.plan_sha256)
    identity = build_activation_journal_identity(
        pre_prerequisites,
        pre_systemd,
        confirmed_plan_sha256=plan_sha256,
    )
    records: list[bytes] = []
    append_revision(
        records,
        identity,
        phase="prepared",
        step_id="transaction_open",
    )
    return (
        pre_prerequisites,
        pre_systemd,
        plan_sha256,
        identity,
        records,
    )


def main() -> int:
    failures: list[str] = []
    (
        pre_prerequisites,
        pre_systemd,
        plan_sha256,
        identity,
        prepared_records,
    ) = build_records()

    with tempfile.TemporaryDirectory(
        prefix="relay-recovery-controller-confirmation-"
    ) as temporary:
        root = Path(temporary)
        root.chmod(0o700)
        with _open_fixture_store(root) as raw_store:
            raw_store.publish_revision(prepared_records[0])
            store = CountingStore(raw_store)
            resume_preview = preview(
                store,
                plan_sha256,
                "resume",
                pre_prerequisites,
                pre_systemd,
            )
            rollback_preview = preview(
                store,
                plan_sha256,
                "rollback",
                pre_prerequisites,
                pre_systemd,
            )
            store.reset()
            invalid_confirmation = expect_error(
                lambda: run(
                    store,
                    plan_sha256,
                    "resume",
                    "invalid",
                    pre_prerequisites,
                    pre_systemd,
                ),
                expected="activation_recovery_confirmation_invalid",
                label="invalid confirmation",
                failures=failures,
            )
            invalid_zero_read = (
                store.loads == 0
                and store.revision_writes == 0
                and store.receipt_writes == 0
            )
            store.reset()
            stale_confirmation = expect_error(
                lambda: run(
                    store,
                    plan_sha256,
                    "resume",
                    "0" * 64,
                    pre_prerequisites,
                    pre_systemd,
                ),
                expected="activation_recovery_confirmation_stale",
                label="stale confirmation",
                failures=failures,
            )
            stale_zero_write = (
                store.revision_writes == 0
                and store.receipt_writes == 0
            )
            store.reset()
            unsupported_run_step = expect_error(
                lambda: run(
                    store,
                    plan_sha256,
                    "resume",
                    str(resume_preview["decision_sha256"]),
                    pre_prerequisites,
                    pre_systemd,
                ),
                expected="activation_recovery_action_not_supported",
                label="systemd action",
                failures=failures,
            )
            unsupported_zero_write = (
                store.revision_writes == 0
                and store.receipt_writes == 0
            )
            store.reset()
            blocked_action = expect_error(
                lambda: run(
                    store,
                    plan_sha256,
                    "rollback",
                    str(rollback_preview["decision_sha256"]),
                    pre_prerequisites,
                    pre_systemd,
                ),
                expected="activation_recovery_action_blocked",
                label="blocked action",
                failures=failures,
            )
            blocked_zero_write = (
                store.revision_writes == 0
                and store.receipt_writes == 0
            )

    post_daemon_systemd = systemd()
    with tempfile.TemporaryDirectory(
        prefix="relay-recovery-controller-observation-"
    ) as temporary:
        root = Path(temporary)
        root.chmod(0o700)
        observation_records = list(prepared_records)
        append_intent(
            observation_records,
            identity,
            "daemon_reload",
            owns_enable=False,
            owns_start=False,
        )
        with _open_fixture_store(root) as raw_store:
            for raw in observation_records:
                raw_store.publish_revision(raw)
            store = CountingStore(raw_store)
            observation_preview = preview(
                store,
                plan_sha256,
                "resume",
                pre_prerequisites,
                post_daemon_systemd,
            )
            store.reset()
            observation_result = run(
                store,
                plan_sha256,
                "resume",
                str(observation_preview["decision_sha256"]),
                pre_prerequisites,
                post_daemon_systemd,
            )
            observation_snapshot = raw_store._load_recovery_snapshot(
                plan_sha256
            )
            observation_one_write = (
                store.revision_writes == 1
                and store.receipt_writes == 0
                and observation_result.get("write_id")
                == "observed_revision"
                and observation_snapshot.revisions[-1].phase
                == "observed"
                and observation_snapshot.revisions[-1].step_id
                == "daemon_reload"
            )

    production_calls: dict[str, object] = {}
    capability = object()

    class ProductionSession:
        def __init__(self, store: CountingStore) -> None:
            self.store = store

        def __enter__(self):
            production_calls["entered"] = True
            return self

        def __exit__(self, exc_type, exc, traceback) -> None:
            production_calls["exited"] = True

        def _activation_scan_capability(self):
            return capability

        def _load_recovery_snapshot(self, candidate: str):
            return self.store._load_recovery_snapshot(candidate)

        def publish_revision(self, raw: bytes):
            return self.store.publish_revision(raw)

        def publish_receipt(self, raw: bytes):
            return self.store.publish_receipt(raw)

    with tempfile.TemporaryDirectory(
        prefix="relay-recovery-controller-production-"
    ) as temporary:
        root = Path(temporary)
        root.chmod(0o700)
        with _open_fixture_store(root) as raw_store:
            for raw in observation_records:
                raw_store.publish_revision(raw)
            store = CountingStore(raw_store)
            production_preview = preview(
                store,
                plan_sha256,
                "resume",
                pre_prerequisites,
                post_daemon_systemd,
            )
            store.reset()
            session = ProductionSession(store)
            original_opener = (
                controller_module._open_locked_production_store
            )
            original_scanner = (
                controller_module
                ._scan_activation_prerequisites_while_locked
            )
            original_reader = controller_module.read_systemd_show
            try:
                controller_module._open_locked_production_store = (
                    lambda candidate: (
                        production_calls.__setitem__(
                            "root",
                            candidate,
                        )
                        or session
                    )
                )
                controller_module._scan_activation_prerequisites_while_locked = (
                    lambda candidate: (
                        production_calls.__setitem__(
                            "capability",
                            candidate,
                        )
                        or pre_prerequisites
                    )
                )
                controller_module.read_systemd_show = (
                    lambda candidate: (
                        post_daemon_systemd
                        if candidate == pre_prerequisites
                        else None
                    )
                )
                production_result = _run_confirmed_recovery_write(
                    plan_sha256,
                    "resume",
                    str(production_preview["decision_sha256"]),
                )
            finally:
                controller_module._open_locked_production_store = (
                    original_opener
                )
                controller_module._scan_activation_prerequisites_while_locked = (
                    original_scanner
                )
                controller_module.read_systemd_show = original_reader
            production_one_write = (
                store.revision_writes == 1
                and store.receipt_writes == 0
                and production_result.get("write_id")
                == "observed_revision"
                and production_calls
                == {
                    "capability": capability,
                    "entered": True,
                    "exited": True,
                    "root": Path("/"),
                }
            )

    with tempfile.TemporaryDirectory(
        prefix="relay-recovery-controller-failure-"
    ) as temporary:
        root = Path(temporary)
        root.chmod(0o700)
        with _open_fixture_store(root) as raw_store:
            for raw in observation_records:
                raw_store.publish_revision(raw)
            store = CountingStore(raw_store)
            failure_preview = preview(
                store,
                plan_sha256,
                "resume",
                pre_prerequisites,
                post_daemon_systemd,
            )
            store.reset()
            store.fail_after_revision = True
            failure_mapped = expect_error(
                lambda: run(
                    store,
                    plan_sha256,
                    "resume",
                    str(failure_preview["decision_sha256"]),
                    pre_prerequisites,
                    post_daemon_systemd,
                ),
                expected="activation_recovery_required",
                label="post-write failure",
                failures=failures,
            )
            retained = raw_store._load_recovery_snapshot(plan_sha256)
            post_write_state_retained = (
                failure_mapped
                and store.revision_writes == 1
                and retained.revisions[-1].phase == "observed"
            )

    enabled_prerequisites = prerequisites(enabled=True)
    enabled_systemd = systemd(enabled=True)
    active_systemd = systemd(enabled=True, active=True)
    completed_records = list(prepared_records)
    append_intent(
        completed_records,
        identity,
        "daemon_reload",
        owns_enable=False,
        owns_start=False,
    )
    append_observed(
        completed_records,
        identity,
        "daemon_reload",
        pre_prerequisites,
        post_daemon_systemd,
        owns_enable=False,
        owns_start=False,
    )
    append_intent(
        completed_records,
        identity,
        "enable",
        owns_enable=False,
        owns_start=False,
    )
    append_observed(
        completed_records,
        identity,
        "enable",
        enabled_prerequisites,
        enabled_systemd,
        owns_enable=True,
        owns_start=False,
    )
    append_intent(
        completed_records,
        identity,
        "start",
        owns_enable=True,
        owns_start=False,
    )
    append_observed(
        completed_records,
        identity,
        "start",
        enabled_prerequisites,
        active_systemd,
        owns_enable=True,
        owns_start=True,
    )
    append_intent(
        completed_records,
        identity,
        "verify",
        owns_enable=True,
        owns_start=True,
    )
    append_observed(
        completed_records,
        identity,
        "verify",
        enabled_prerequisites,
        active_systemd,
        owns_enable=True,
        owns_start=True,
    )

    with tempfile.TemporaryDirectory(
        prefix="relay-recovery-controller-terminal-"
    ) as temporary:
        root = Path(temporary)
        root.chmod(0o700)
        with _open_fixture_store(root) as raw_store:
            for raw in completed_records:
                raw_store.publish_revision(raw)
            store = CountingStore(raw_store)
            receipt_preview = preview(
                store,
                plan_sha256,
                "resume",
                enabled_prerequisites,
                active_systemd,
            )
            store.reset()
            receipt_result = run(
                store,
                plan_sha256,
                "resume",
                str(receipt_preview["decision_sha256"]),
                enabled_prerequisites,
                active_systemd,
            )
            receipt_one_write = (
                store.receipt_writes == 1
                and store.revision_writes == 0
                and receipt_result.get("write_id")
                == "success_receipt"
                and receipt_result.get("recovery_required") is True
            )

            terminal_preview = preview(
                store,
                plan_sha256,
                "resume",
                enabled_prerequisites,
                active_systemd,
            )
            store.reset()
            terminal_result = run(
                store,
                plan_sha256,
                "resume",
                str(terminal_preview["decision_sha256"]),
                enabled_prerequisites,
                active_systemd,
            )
            terminal_one_write = (
                store.revision_writes == 1
                and store.receipt_writes == 0
                and terminal_result.get("write_id")
                == "terminal_revision"
                and terminal_result.get("state") == "active"
                and terminal_result.get("recovery_required") is False
            )

            complete_preview = preview(
                store,
                plan_sha256,
                "resume",
                enabled_prerequisites,
                active_systemd,
            )
            store.reset()
            complete_result = run(
                store,
                plan_sha256,
                "resume",
                str(complete_preview["decision_sha256"]),
                enabled_prerequisites,
                active_systemd,
            )
            complete_zero_write = (
                store.revision_writes == 0
                and store.receipt_writes == 0
                and complete_result.get("write_id") == "none"
                and complete_result.get("state") == "active"
            )

    rollback_records = completed_records[:-2]
    append_intent(
        rollback_records,
        identity,
        "rollback_stop",
        owns_enable=True,
        owns_start=True,
    )
    append_observed(
        rollback_records,
        identity,
        "rollback_stop",
        enabled_prerequisites,
        enabled_systemd,
        owns_enable=True,
        owns_start=False,
    )
    append_intent(
        rollback_records,
        identity,
        "rollback_disable",
        owns_enable=True,
        owns_start=False,
    )
    restored_prerequisites = prerequisites()
    restored_systemd = systemd()
    append_observed(
        rollback_records,
        identity,
        "rollback_disable",
        restored_prerequisites,
        restored_systemd,
        owns_enable=False,
        owns_start=False,
    )
    append_intent(
        rollback_records,
        identity,
        "verify",
        owns_enable=False,
        owns_start=False,
        intent_id="rollback_verify_requested",
    )

    with tempfile.TemporaryDirectory(
        prefix="relay-recovery-controller-rollback-"
    ) as temporary:
        root = Path(temporary)
        root.chmod(0o700)
        with _open_fixture_store(root) as raw_store:
            for raw in rollback_records:
                raw_store.publish_revision(raw)
            store = CountingStore(raw_store)
            rollback_observation_preview = preview(
                store,
                plan_sha256,
                "rollback",
                restored_prerequisites,
                restored_systemd,
            )
            store.reset()
            rollback_observation_result = run(
                store,
                plan_sha256,
                "rollback",
                str(
                    rollback_observation_preview[
                        "decision_sha256"
                    ]
                ),
                restored_prerequisites,
                restored_systemd,
            )
            rollback_observation_snapshot = (
                raw_store._load_recovery_snapshot(plan_sha256)
            )
            rollback_observation_one_write = (
                store.revision_writes == 1
                and store.receipt_writes == 0
                and rollback_observation_result.get("write_id")
                == "observed_revision"
                and rollback_observation_snapshot.revisions[
                    -1
                ].observation_id
                == "rollback_verified"
                and rollback_observation_snapshot.revisions[-1].owns_enable
                is False
                and rollback_observation_snapshot.revisions[-1].owns_start
                is False
            )

            rollback_receipt_preview = preview(
                store,
                plan_sha256,
                "rollback",
                restored_prerequisites,
                restored_systemd,
            )
            store.reset()
            rollback_receipt_result = run(
                store,
                plan_sha256,
                "rollback",
                str(rollback_receipt_preview["decision_sha256"]),
                restored_prerequisites,
                restored_systemd,
            )
            rollback_receipt_one_write = (
                store.receipt_writes == 1
                and store.revision_writes == 0
                and rollback_receipt_result.get("write_id")
                == "rollback_receipt"
                and rollback_receipt_result.get("recovery_required")
                is True
            )

            rollback_terminal_preview = preview(
                store,
                plan_sha256,
                "rollback",
                restored_prerequisites,
                restored_systemd,
            )
            store.reset()
            rollback_terminal_result = run(
                store,
                plan_sha256,
                "rollback",
                str(rollback_terminal_preview["decision_sha256"]),
                restored_prerequisites,
                restored_systemd,
            )
            rollback_terminal_one_write = (
                store.revision_writes == 1
                and store.receipt_writes == 0
                and rollback_terminal_result.get("write_id")
                == "terminal_revision"
                and rollback_terminal_result.get("state")
                == "service_state_rolled_back"
                and rollback_terminal_result.get("recovery_required")
                is False
            )

            rollback_complete_preview = preview(
                store,
                plan_sha256,
                "rollback",
                restored_prerequisites,
                restored_systemd,
            )
            store.reset()
            rollback_complete_result = run(
                store,
                plan_sha256,
                "rollback",
                str(rollback_complete_preview["decision_sha256"]),
                restored_prerequisites,
                restored_systemd,
            )
            rollback_complete_zero_write = (
                store.revision_writes == 0
                and store.receipt_writes == 0
                and rollback_complete_result.get("write_id") == "none"
                and rollback_complete_result.get("state")
                == "service_state_rolled_back"
            )

    source_path = (
        ROOT
        / "agentops_mis_cli"
        / "relay_activation_recovery_controller.py"
    )
    source = source_path.read_text(encoding="utf-8")
    source_tree = ast.parse(source)
    production_lock_composed = all(
        name in source
        for name in (
            "_open_locked_production_store",
            "_scan_activation_prerequisites_while_locked",
            "read_systemd_show",
        )
    )
    no_mutation_surface = (
        "relay_systemd_mutation" not in source
        and "_run_bound_systemd_mutation" not in source
        and not any(
            isinstance(node, ast.FunctionDef)
            and node.name == "main"
            for node in ast.walk(source_tree)
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
            == "agentops_mis_cli.relay_activation_recovery_controller"
        )
        or (
            isinstance(node, ast.Name)
            and node.id
            in {
                "_run_confirmed_recovery_write",
                "_run_confirmed_recovery_write_with",
            }
        )
        or (
            isinstance(node, ast.Attribute)
            and node.attr
            in {
                "_run_confirmed_recovery_write",
                "_run_confirmed_recovery_write_with",
            }
        )
        for tree in cli_trees
        for node in ast.walk(tree)
    )
    require(
        production_lock_composed and no_mutation_surface,
        "production writer composition gained a mutation surface",
        failures,
    )
    require(
        production_one_write,
        "production writer did not use one locked store and capability",
        failures,
    )
    require(
        not cli_surface_exposed,
        "recovery controller became reachable from CLI",
        failures,
    )
    require(
        "agentops_mis_cli/relay_activation_recovery_controller.py"
        in EXPECTED_WHEEL_MODULES,
        "exact wheel module set omits recovery controller",
        failures,
    )

    public_text = json.dumps(
        {
            "complete": complete_result,
            "observation": observation_result,
            "production": production_result,
            "receipt": receipt_result,
            "rollback_complete": rollback_complete_result,
            "rollback_observation": rollback_observation_result,
            "rollback_receipt": rollback_receipt_result,
            "rollback_terminal": rollback_terminal_result,
            "terminal": terminal_result,
        },
        ensure_ascii=True,
        sort_keys=True,
    )
    private_payload_omitted = (
        PRIVATE_CANARY not in public_text
        and "/etc/" not in public_text
        and "/var/" not in public_text
    )
    require(
        private_payload_omitted,
        "recovery controller exposed private payload",
        failures,
    )
    require(
        rollback_observation_one_write,
        "rollback verification did not publish exactly one observation",
        failures,
    )
    require(
        rollback_receipt_one_write,
        "verified rollback did not publish exactly one receipt",
        failures,
    )
    require(
        rollback_terminal_one_write and rollback_complete_zero_write,
        "rollback did not terminalize once and then complete idempotently",
        failures,
    )
    result = {
        "blocked_action_zero_write": (
            blocked_action and blocked_zero_write
        ),
        "cli_surface_exposed": cli_surface_exposed,
        "complete_zero_write": complete_zero_write,
        "confirmation_zero_write": (
            invalid_confirmation
            and invalid_zero_read
            and stale_confirmation
            and stale_zero_write
        ),
        "failures": failures,
        "network_used": False,
        "observation_one_write": observation_one_write,
        "ok": not failures,
        "operation": "relay_activation_recovery_controller_smoke",
        "post_write_failure_retained": post_write_state_retained,
        "private_payload_omitted": private_payload_omitted,
        "production_lock_composed": production_lock_composed,
        "production_one_write": production_one_write,
        "receipt_one_write": receipt_one_write,
        "rollback_observation_one_write": (
            rollback_observation_one_write
        ),
        "rollback_receipt_one_write": rollback_receipt_one_write,
        "rollback_terminal_contract": (
            rollback_terminal_one_write
            and rollback_complete_zero_write
        ),
        "systemd_action_zero_write": (
            unsupported_run_step and unsupported_zero_write
        ),
        "systemd_mutation_performed": False,
        "terminal_one_write": terminal_one_write,
        "write_scope": "fixture_journal_only",
    }
    print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
