#!/usr/bin/env python3
"""Exercise one-step scanner-bound Relay activation recovery execution."""
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
    FileIdentity,
    compile_activation_plan,
)
from agentops_mis_cli.relay_activation_evidence import (  # noqa: E402
    build_activation_journal_identity,
)
from agentops_mis_cli.relay_activation_journal import (  # noqa: E402
    _open_fixture_store,
)
from agentops_mis_cli.relay_activation_recovery_executor import (  # noqa: E402
    RelayActivationRecoveryExecutorError,
    _run_confirmed_recovery_step_with,
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


PRIVATE_CANARY = "RECOVERY_EXECUTOR_PRIVATE_CANARY"


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


class CountingStore:
    def __init__(self, store) -> None:
        self.store = store
        self.loads = 0
        self.revision_writes = 0

    def reset(self) -> None:
        self.loads = 0
        self.revision_writes = 0

    def _load_recovery_snapshot(self, plan_sha256: str):
        self.loads += 1
        return self.store._load_recovery_snapshot(plan_sha256)

    def publish_revision(self, raw: bytes):
        self.revision_writes += 1
        return self.store.publish_revision(raw)


class FakeRuntime:
    def __init__(
        self,
        current_prerequisites,
        current_systemd,
        *,
        fail_operation: str | None = None,
        scan_override=None,
    ) -> None:
        self.prerequisites = current_prerequisites
        self.systemd = current_systemd
        self.fail_operation = fail_operation
        self.scan_override = scan_override
        self.mutations: list[str] = []
        self.scan_count = 0
        self.read_count = 0

    def scan(self):
        self.scan_count += 1
        if self.scan_override is not None:
            return self.scan_override(
                self.scan_count,
                self.prerequisites,
            )
        return self.prerequisites

    def read(self, observed):
        self.read_count += 1
        if observed != self.prerequisites:
            raise RuntimeError(f"{PRIVATE_CANARY}:stale-reader")
        return self.systemd

    def mutate(self, identity: FileIdentity, operation: str) -> None:
        if identity != self.prerequisites.systemctl:
            raise RuntimeError(f"{PRIVATE_CANARY}:wrong-systemctl")
        self.mutations.append(operation)
        if operation == self.fail_operation:
            raise RuntimeError(f"{PRIVATE_CANARY}:mutation-failed")
        if operation == "daemon_reload":
            self.systemd = systemd()
        elif operation == "enable":
            self.prerequisites = prerequisites(enabled=True)
            self.systemd = systemd(enabled=True)
        elif operation == "start":
            self.systemd = systemd(enabled=True, active=True)
        elif operation == "stop":
            self.systemd = systemd(
                enabled=True,
                exec_main_status=15,
                retain_inactive_invocation=True,
            )
        elif operation == "disable":
            self.prerequisites = prerequisites()
            self.systemd = systemd()
        else:
            raise RuntimeError(f"{PRIVATE_CANARY}:unexpected-mutation")


def preview(
    store: CountingStore,
    plan_sha256: str,
    outcome: str,
    runtime: FakeRuntime,
) -> dict[str, object]:
    return _preview_activation_recovery_with(
        plan_sha256,
        outcome,
        snapshot_loader=store._load_recovery_snapshot,
        scanner=runtime.scan,
        systemd_reader=runtime.read,
    )


def execute(
    store: CountingStore,
    plan_sha256: str,
    outcome: str,
    decision_sha256: str,
    runtime: FakeRuntime,
) -> dict[str, object]:
    return _run_confirmed_recovery_step_with(
        plan_sha256,
        outcome,
        decision_sha256,
        store=store,
        scanner=runtime.scan,
        systemd_reader=runtime.read,
        mutation_runner=runtime.mutate,
    )


def error_id(callback) -> str:
    try:
        callback()
    except RelayActivationRecoveryExecutorError as exc:
        return exc.error_id
    return ""


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
    prepared: list[bytes] = []
    append_revision(
        prepared,
        identity,
        phase="prepared",
        step_id="transaction_open",
    )

    daemon_observed = list(prepared)
    append_intent(
        daemon_observed,
        identity,
        "daemon_reload",
        owns_enable=False,
        owns_start=False,
    )
    append_observed(
        daemon_observed,
        identity,
        "daemon_reload",
        pre_prerequisites,
        systemd(),
        owns_enable=False,
        owns_start=False,
    )

    enable_observed = list(daemon_observed)
    append_intent(
        enable_observed,
        identity,
        "enable",
        owns_enable=False,
        owns_start=False,
    )
    enabled_prerequisites = prerequisites(enabled=True)
    enabled_systemd = systemd(enabled=True)
    append_observed(
        enable_observed,
        identity,
        "enable",
        enabled_prerequisites,
        enabled_systemd,
        owns_enable=True,
        owns_start=False,
    )

    start_observed = list(enable_observed)
    append_intent(
        start_observed,
        identity,
        "start",
        owns_enable=True,
        owns_start=False,
    )
    active_systemd = systemd(enabled=True, active=True)
    stopped_systemd = systemd(
        enabled=True,
        exec_main_status=15,
        retain_inactive_invocation=True,
    )
    append_observed(
        start_observed,
        identity,
        "start",
        enabled_prerequisites,
        active_systemd,
        owns_enable=True,
        owns_start=True,
    )

    stopped_observed = list(start_observed)
    append_intent(
        stopped_observed,
        identity,
        "rollback_stop",
        owns_enable=True,
        owns_start=True,
    )
    append_observed(
        stopped_observed,
        identity,
        "rollback_stop",
        enabled_prerequisites,
        stopped_systemd,
        owns_enable=True,
        owns_start=False,
    )

    disabled_observed = list(stopped_observed)
    append_intent(
        disabled_observed,
        identity,
        "rollback_disable",
        owns_enable=True,
        owns_start=False,
    )
    append_observed(
        disabled_observed,
        identity,
        "rollback_disable",
        pre_prerequisites,
        systemd(),
        owns_enable=False,
        owns_start=False,
    )
    return {
        "active_systemd": active_systemd,
        "daemon_observed": daemon_observed,
        "disabled_observed": disabled_observed,
        "enable_observed": enable_observed,
        "enabled_prerequisites": enabled_prerequisites,
        "enabled_systemd": enabled_systemd,
        "identity": identity,
        "plan_sha256": plan_sha256,
        "pre_prerequisites": pre_prerequisites,
        "pre_systemd": pre_systemd,
        "prepared": prepared,
        "start_observed": start_observed,
        "stopped_systemd": stopped_systemd,
        "stopped_observed": stopped_observed,
    }


def run_case(
    records: list[bytes],
    *,
    plan_sha256: str,
    outcome: str,
    runtime: FakeRuntime,
    expected_step: str,
    expected_mutations: tuple[str, ...],
    expected_writes: int,
    expected_intent_reused: bool,
    expected_ownership: tuple[bool, bool],
    failures: list[str],
    observation_id: str | None = None,
) -> dict[str, object]:
    with tempfile.TemporaryDirectory(
        prefix=f"relay-recovery-executor-{expected_step}-"
    ) as temporary:
        root = Path(temporary)
        root.chmod(0o700)
        with _open_fixture_store(root) as raw_store:
            for raw in records:
                raw_store.publish_revision(raw)
            store = CountingStore(raw_store)
            decision = preview(
                store,
                plan_sha256,
                outcome,
                runtime,
            )
            store.reset()
            result = execute(
                store,
                plan_sha256,
                outcome,
                str(decision["decision_sha256"]),
                runtime,
            )
            snapshot = raw_store._load_recovery_snapshot(plan_sha256)
            last = snapshot.revisions[-1]
            require(
                result.get("ok") is True
                and result.get("step_id") == expected_step
                and result.get("operation_id") == "run_step"
                and result.get("recovery_required") is True
                and result.get("intent_reused")
                is expected_intent_reused
                and result.get("write_id") == "step_observed",
                f"{expected_step}: bounded result changed",
                failures,
            )
            require(
                store.revision_writes == expected_writes
                and tuple(runtime.mutations) == expected_mutations
                and last.phase == "observed"
                and last.step_id == expected_step
                and (last.owns_enable, last.owns_start)
                == expected_ownership
                and (
                    observation_id is None
                    or last.observation_id == observation_id
                ),
                f"{expected_step}: execution evidence changed",
                failures,
            )
            return result


def main() -> int:
    failures: list[str] = []
    fixtures = build_records()
    plan_sha256 = str(fixtures["plan_sha256"])
    pre_prerequisites = fixtures["pre_prerequisites"]
    pre_systemd = fixtures["pre_systemd"]
    enabled_prerequisites = fixtures["enabled_prerequisites"]
    enabled_systemd = fixtures["enabled_systemd"]
    active_systemd = fixtures["active_systemd"]
    stopped_systemd = fixtures["stopped_systemd"]

    results: list[dict[str, object]] = []
    results.append(
        run_case(
            list(fixtures["prepared"]),
            plan_sha256=plan_sha256,
            outcome="resume",
            runtime=FakeRuntime(pre_prerequisites, pre_systemd),
            expected_step="daemon_reload",
            expected_mutations=("daemon_reload",),
            expected_writes=2,
            expected_intent_reused=False,
            expected_ownership=(False, False),
            failures=failures,
        )
    )
    interrupted_daemon = list(fixtures["prepared"])
    append_intent(
        interrupted_daemon,
        fixtures["identity"],
        "daemon_reload",
        owns_enable=False,
        owns_start=False,
    )
    results.append(
        run_case(
            interrupted_daemon,
            plan_sha256=plan_sha256,
            outcome="resume",
            runtime=FakeRuntime(pre_prerequisites, pre_systemd),
            expected_step="daemon_reload",
            expected_mutations=("daemon_reload",),
            expected_writes=1,
            expected_intent_reused=True,
            expected_ownership=(False, False),
            failures=failures,
        )
    )
    results.append(
        run_case(
            list(fixtures["daemon_observed"]),
            plan_sha256=plan_sha256,
            outcome="resume",
            runtime=FakeRuntime(pre_prerequisites, systemd()),
            expected_step="enable",
            expected_mutations=("enable",),
            expected_writes=2,
            expected_intent_reused=False,
            expected_ownership=(True, False),
            failures=failures,
        )
    )
    results.append(
        run_case(
            list(fixtures["enable_observed"]),
            plan_sha256=plan_sha256,
            outcome="resume",
            runtime=FakeRuntime(
                enabled_prerequisites,
                enabled_systemd,
            ),
            expected_step="start",
            expected_mutations=("start",),
            expected_writes=2,
            expected_intent_reused=False,
            expected_ownership=(True, True),
            failures=failures,
        )
    )
    results.append(
        run_case(
            list(fixtures["start_observed"]),
            plan_sha256=plan_sha256,
            outcome="resume",
            runtime=FakeRuntime(
                enabled_prerequisites,
                active_systemd,
            ),
            expected_step="verify",
            expected_mutations=(),
            expected_writes=2,
            expected_intent_reused=False,
            expected_ownership=(True, True),
            failures=failures,
            observation_id="verify_observed",
        )
    )
    results.append(
        run_case(
            list(fixtures["start_observed"]),
            plan_sha256=plan_sha256,
            outcome="rollback",
            runtime=FakeRuntime(
                enabled_prerequisites,
                active_systemd,
            ),
            expected_step="rollback_stop",
            expected_mutations=("stop",),
            expected_writes=2,
            expected_intent_reused=False,
            expected_ownership=(True, False),
            failures=failures,
        )
    )
    results.append(
        run_case(
            list(fixtures["stopped_observed"]),
            plan_sha256=plan_sha256,
            outcome="rollback",
            runtime=FakeRuntime(
                enabled_prerequisites,
                stopped_systemd,
            ),
            expected_step="rollback_disable",
            expected_mutations=("disable",),
            expected_writes=2,
            expected_intent_reused=False,
            expected_ownership=(False, False),
            failures=failures,
        )
    )
    results.append(
        run_case(
            list(fixtures["disabled_observed"]),
            plan_sha256=plan_sha256,
            outcome="rollback",
            runtime=FakeRuntime(pre_prerequisites, systemd()),
            expected_step="verify",
            expected_mutations=(),
            expected_writes=2,
            expected_intent_reused=False,
            expected_ownership=(False, False),
            failures=failures,
            observation_id="rollback_verified",
        )
    )

    with tempfile.TemporaryDirectory(
        prefix="relay-recovery-executor-confirmation-"
    ) as temporary:
        root = Path(temporary)
        root.chmod(0o700)
        with _open_fixture_store(root) as raw_store:
            for raw in fixtures["prepared"]:
                raw_store.publish_revision(raw)
            store = CountingStore(raw_store)
            runtime = FakeRuntime(pre_prerequisites, pre_systemd)
            invalid = error_id(
                lambda: execute(
                    store,
                    plan_sha256,
                    "resume",
                    "invalid",
                    runtime,
                )
            )
            invalid_zero_effect = (
                store.loads == 0
                and store.revision_writes == 0
                and runtime.mutations == []
            )
            store.reset()
            stale = error_id(
                lambda: execute(
                    store,
                    plan_sha256,
                    "resume",
                    "0" * 64,
                    runtime,
                )
            )
            stale_zero_effect = (
                store.revision_writes == 0
                and runtime.mutations == []
            )
            blocked_decision = preview(
                store,
                plan_sha256,
                "rollback",
                runtime,
            )
            store.reset()
            blocked = error_id(
                lambda: execute(
                    store,
                    plan_sha256,
                    "rollback",
                    str(blocked_decision["decision_sha256"]),
                    runtime,
                )
            )
            blocked_zero_effect = (
                store.revision_writes == 0
                and runtime.mutations == []
            )
    require(
        invalid == "activation_recovery_confirmation_invalid"
        and invalid_zero_effect
        and stale == "activation_recovery_confirmation_stale"
        and stale_zero_effect
        and blocked == "activation_recovery_action_blocked"
        and blocked_zero_effect,
        "invalid, stale, or blocked confirmation had an effect",
        failures,
    )

    observed_daemon = list(fixtures["prepared"])
    append_intent(
        observed_daemon,
        fixtures["identity"],
        "daemon_reload",
        owns_enable=False,
        owns_start=False,
    )
    with tempfile.TemporaryDirectory(
        prefix="relay-recovery-executor-unsupported-"
    ) as temporary:
        root = Path(temporary)
        root.chmod(0o700)
        with _open_fixture_store(root) as raw_store:
            for raw in observed_daemon:
                raw_store.publish_revision(raw)
            store = CountingStore(raw_store)
            runtime = FakeRuntime(pre_prerequisites, systemd())
            decision = preview(
                store,
                plan_sha256,
                "resume",
                runtime,
            )
            store.reset()
            unsupported = error_id(
                lambda: execute(
                    store,
                    plan_sha256,
                    "resume",
                    str(decision["decision_sha256"]),
                    runtime,
                )
            )
            unsupported_zero_effect = (
                store.revision_writes == 0
                and runtime.mutations == []
            )
    require(
        unsupported == "activation_recovery_action_not_supported"
        and unsupported_zero_effect,
        "non-run decision reached the executor",
        failures,
    )

    with tempfile.TemporaryDirectory(
        prefix="relay-recovery-executor-failure-"
    ) as temporary:
        root = Path(temporary)
        root.chmod(0o700)
        with _open_fixture_store(root) as raw_store:
            for raw in fixtures["prepared"]:
                raw_store.publish_revision(raw)
            store = CountingStore(raw_store)
            runtime = FakeRuntime(
                pre_prerequisites,
                pre_systemd,
                fail_operation="daemon_reload",
            )
            decision = preview(
                store,
                plan_sha256,
                "resume",
                runtime,
            )
            store.reset()
            failed = error_id(
                lambda: execute(
                    store,
                    plan_sha256,
                    "resume",
                    str(decision["decision_sha256"]),
                    runtime,
                )
            )
            retained = raw_store._load_recovery_snapshot(plan_sha256)
            failure_retained = (
                failed == "activation_recovery_required"
                and store.revision_writes == 1
                and runtime.mutations == ["daemon_reload"]
                and retained.revisions[-1].phase == "intent"
                and retained.revisions[-1].step_id == "daemon_reload"
            )
    require(
        failure_retained,
        "failed mutation did not retain its durable intent",
        failures,
    )

    def drift_before_mutation(scan_count, current):
        if scan_count == 5:
            return replace(
                current,
                unit=replace(
                    current.unit,
                    content_sha256="7" * 64,
                ),
            )
        return current

    with tempfile.TemporaryDirectory(
        prefix="relay-recovery-executor-drift-"
    ) as temporary:
        root = Path(temporary)
        root.chmod(0o700)
        with _open_fixture_store(root) as raw_store:
            for raw in fixtures["prepared"]:
                raw_store.publish_revision(raw)
            store = CountingStore(raw_store)
            runtime = FakeRuntime(
                pre_prerequisites,
                pre_systemd,
                scan_override=drift_before_mutation,
            )
            decision = preview(
                store,
                plan_sha256,
                "resume",
                runtime,
            )
            store.reset()
            drifted = error_id(
                lambda: execute(
                    store,
                    plan_sha256,
                    "resume",
                    str(decision["decision_sha256"]),
                    runtime,
                )
            )
            retained = raw_store._load_recovery_snapshot(plan_sha256)
            pre_mutation_drift_retained = (
                drifted == "activation_recovery_required"
                and store.revision_writes == 1
                and runtime.mutations == []
                and retained.revisions[-1].phase == "intent"
                and retained.revisions[-1].step_id == "daemon_reload"
            )
    require(
        pre_mutation_drift_retained,
        "post-intent prerequisite drift reached mutation",
        failures,
    )

    source_path = (
        ROOT
        / "agentops_mis_cli"
        / "relay_activation_recovery_executor.py"
    )
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    production_composed = all(
        name in source
        for name in (
            "_open_locked_production_store",
            "_scan_activation_prerequisites_while_locked",
            "_run_bound_systemd_mutation",
            "read_systemd_show",
        )
    )
    no_cli_main = not any(
        isinstance(node, ast.FunctionDef) and node.name == "main"
        for node in ast.walk(tree)
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
            == "agentops_mis_cli.relay_activation_recovery_executor"
        )
        or (
            isinstance(node, ast.Name)
            and node.id == "_run_confirmed_recovery_step"
        )
        or (
            isinstance(node, ast.Attribute)
            and node.attr == "_run_confirmed_recovery_step"
        )
        for tree in cli_trees
        for node in ast.walk(tree)
    )
    require(
        production_composed and no_cli_main and not cli_surface_exposed,
        "production executor composition or CLI boundary changed",
        failures,
    )
    require(
        "agentops_mis_cli/relay_activation_recovery_executor.py"
        in EXPECTED_WHEEL_MODULES,
        "exact wheel module set omits recovery executor",
        failures,
    )

    public_text = json.dumps(
        results,
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
        "recovery executor exposed private payload",
        failures,
    )
    result = {
        "cli_surface_exposed": cli_surface_exposed,
        "blocked_zero_effect": blocked_zero_effect,
        "confirmation_zero_effect": (
            invalid_zero_effect and stale_zero_effect
        ),
        "failures": failures,
        "intent_reuse_verified": (
            results[1].get("intent_reused") is True
        ),
        "mutation_failure_retained": failure_retained,
        "network_used": False,
        "ok": not failures,
        "operation": "relay_activation_recovery_executor_smoke",
        "private_payload_omitted": private_payload_omitted,
        "pre_mutation_drift_retained": pre_mutation_drift_retained,
        "production_lock_composed": production_composed,
        "recovery_steps": len(results),
        "systemd_mutation_operations": [
            "daemon_reload",
            "disable",
            "enable",
            "start",
            "stop",
        ],
        "write_scope": "fixture_journal_only",
    }
    print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
