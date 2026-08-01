#!/usr/bin/env python3
"""Exercise the exact-confirmed Relay activation success controller."""
from __future__ import annotations

import ast
import contextlib
import io
import json
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Callable


sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agentops_mis_cli import relay_admin  # noqa: E402
from agentops_mis_cli.relay_activation import (  # noqa: E402
    CONFIG_PATH,
    ENABLEMENT_LINK_PATH,
    RUNTIME_DIRECTORY,
    STATE_DIRECTORY,
    UNIT_PATH,
    ActivationPrerequisiteSnapshot,
    DirectoryIdentity,
    FileIdentity,
    LinkIdentity,
    RootIdentity,
    SystemdSnapshot,
    compile_activation_plan,
)
from agentops_mis_cli.relay_activation_controller import (  # noqa: E402
    RelayActivationControllerError,
    _run_confirmed_activation_with,
)
from agentops_mis_cli.relay_activation_journal import (  # noqa: E402
    _open_fixture_store,
    parse_activation_revision,
)


PRIVATE_CANARY = "ACTIVATION_CONTROLLER_PRIVATE_CANARY"


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def file_identity(
    path: str,
    digest: str,
    *,
    inode: int,
    owner: int = 0,
    group: int = 0,
    mode: int = 0o644,
) -> FileIdentity:
    return FileIdentity(
        kind="regular",
        canonical_path=path,
        device_id=7,
        inode=inode,
        owner_id=owner,
        group_id=group,
        mode=mode,
        nlink=1,
        size=128,
        content_sha256=digest,
    )


def enablement_link(*, inode: int = 16) -> LinkIdentity:
    return LinkIdentity(
        kind="symlink",
        canonical_path=ENABLEMENT_LINK_PATH,
        target=UNIT_PATH,
        device_id=7,
        inode=inode,
        owner_id=0,
        group_id=0,
        nlink=1,
    )


def prerequisites(
    *,
    enabled: bool = False,
    unit_digest: str = "b" * 64,
) -> ActivationPrerequisiteSnapshot:
    service_uid = 1701
    service_gid = 1701
    return ActivationPrerequisiteSnapshot(
        root=RootIdentity(
            kind="directory",
            canonical_path="/",
            device_id=1,
            inode=2,
            owner_id=0,
            group_id=0,
            mode=0o755,
        ),
        release_id="0.1.0-" + ("1" * 12),
        version_id="0.1.0",
        release_tree_sha256="a" * 64,
        unit=file_identity(
            UNIT_PATH,
            unit_digest,
            inode=10,
        ),
        config=file_identity(
            CONFIG_PATH,
            "c" * 64,
            inode=11,
            group=service_gid,
            mode=0o640,
        ),
        certificate=file_identity(
            f"/etc/{PRIVATE_CANARY}/relay.crt",
            "d" * 64,
            inode=12,
        ),
        private_key=file_identity(
            f"/etc/{PRIVATE_CANARY}/relay.key",
            "e" * 64,
            inode=13,
            owner=service_uid,
            group=service_gid,
            mode=0o600,
        ),
        route_keys=(
            file_identity(
                f"/etc/{PRIVATE_CANARY}/route-a.key",
                "f" * 64,
                inode=14,
                owner=service_uid,
                group=service_gid,
                mode=0o600,
            ),
        ),
        state_directory=DirectoryIdentity(
            kind="directory",
            canonical_path=STATE_DIRECTORY,
            device_id=7,
            inode=18,
            owner_id=service_uid,
            group_id=service_gid,
            mode=0o700,
            nlink=2,
        ),
        runtime_directory=DirectoryIdentity(
            kind="directory",
            canonical_path=RUNTIME_DIRECTORY,
            device_id=7,
            inode=19,
            owner_id=service_uid,
            group_id=service_gid,
            mode=0o700,
            nlink=2,
        ),
        trusted_parent_chain_sha256="0" * 64,
        service_uid=service_uid,
        service_gid=service_gid,
        service_group_ids=(service_gid,),
        systemctl=file_identity(
            "/usr/bin/systemctl",
            "9" * 64,
            inode=15,
            mode=0o755,
        ),
        enablement_links=(
            (enablement_link(),)
            if enabled
            else ()
        ),
    )


def systemd(
    *,
    enabled: bool = False,
    active: bool = False,
    need_reload: bool = True,
    invocation_id: str = "1" * 32,
) -> SystemdSnapshot:
    return SystemdSnapshot(
        load_state="loaded",
        unit_file_state="enabled" if enabled else "disabled",
        active_state="active" if active else "inactive",
        sub_state="running" if active else "dead",
        result="success",
        exec_main_status=0,
        fragment_path=UNIT_PATH,
        need_daemon_reload=need_reload,
        invocation_id=invocation_id if active else "",
        main_pid=1701 if active else 0,
    )


class FakeRuntime:
    def __init__(
        self,
        *,
        enabled: bool = False,
        fail_operation: str | None = None,
        scan_override: Callable[
            [int, ActivationPrerequisiteSnapshot],
            ActivationPrerequisiteSnapshot,
        ]
        | None = None,
    ) -> None:
        self.prerequisites = prerequisites(enabled=enabled)
        self.systemd = systemd(enabled=enabled)
        self.fail_operation = fail_operation
        self.scan_override = scan_override
        self.scan_count = 0
        self.read_count = 0
        self.mutations: list[str] = []

    def scan(self) -> ActivationPrerequisiteSnapshot:
        self.scan_count += 1
        if self.scan_override is not None:
            return self.scan_override(
                self.scan_count,
                self.prerequisites,
            )
        return self.prerequisites

    def read(
        self,
        observed: ActivationPrerequisiteSnapshot,
    ) -> SystemdSnapshot:
        self.read_count += 1
        if observed != self.prerequisites:
            raise RuntimeError(f"{PRIVATE_CANARY}:stale-reader-input")
        return self.systemd

    def mutate(self, identity: FileIdentity, operation: str) -> None:
        if identity != self.prerequisites.systemctl:
            raise RuntimeError(f"{PRIVATE_CANARY}:wrong-systemctl")
        self.mutations.append(operation)
        if operation == self.fail_operation:
            raise RuntimeError(f"{PRIVATE_CANARY}:mutation-failed")
        if operation == "daemon_reload":
            self.systemd = replace(
                self.systemd,
                need_daemon_reload=False,
            )
        elif operation == "enable":
            self.prerequisites = replace(
                self.prerequisites,
                enablement_links=(enablement_link(),),
            )
            self.systemd = replace(
                self.systemd,
                unit_file_state="enabled",
            )
        elif operation == "start":
            self.systemd = replace(
                self.systemd,
                active_state="active",
                sub_state="running",
                result="success",
                exec_main_status=0,
                invocation_id="1" * 32,
                main_pid=1701,
            )
        else:
            raise RuntimeError(f"{PRIVATE_CANARY}:unexpected-operation")


class FailBeforeTerminalStore:
    def __init__(self, store) -> None:
        self.store = store
        self.terminal_rejected = False

    def inspect_store(self) -> dict[str, object]:
        return self.store.inspect_store()

    def inspect_plan(self, plan_sha256: str) -> dict[str, object]:
        return self.store.inspect_plan(plan_sha256)

    def publish_receipt(self, raw: bytes) -> dict[str, object]:
        return self.store.publish_receipt(raw)

    def publish_revision(self, raw: bytes) -> dict[str, object]:
        revision = parse_activation_revision(raw)
        if revision.phase == "terminal":
            self.terminal_rejected = True
            raise RuntimeError(f"{PRIVATE_CANARY}:terminal-interrupt")
        return self.store.publish_revision(raw)


def error_id(callback: Callable[[], object]) -> str:
    try:
        callback()
    except RelayActivationControllerError as exc:
        return str(exc)
    return ""


def run_success_case(
    *,
    initially_enabled: bool,
    failures: list[str],
) -> tuple[dict[str, object], tuple[str, ...], int]:
    with tempfile.TemporaryDirectory(
        prefix="relay-activation-controller-success-"
    ) as temporary:
        root = Path(temporary)
        root.chmod(0o700)
        runtime = FakeRuntime(enabled=initially_enabled)
        plan = compile_activation_plan(
            runtime.prerequisites,
            runtime.systemd,
        )
        require(
            plan.state == "plan_ready"
            and plan.plan_sha256 is not None,
            "success fixture did not produce a ready plan",
            failures,
        )
        with _open_fixture_store(root) as store:
            result = _run_confirmed_activation_with(
                plan.plan_sha256 or "",
                store=store,
                scanner=runtime.scan,
                systemd_reader=runtime.read,
                mutation_runner=runtime.mutate,
            )
            chain = store._load_chain(plan.plan_sha256 or "")
            final_store = store.inspect_store()
            replay_error = error_id(
                lambda: _run_confirmed_activation_with(
                    plan.plan_sha256 or "",
                    store=store,
                    scanner=runtime.scan,
                    systemd_reader=runtime.read,
                    mutation_runner=runtime.mutate,
                )
            )
        expected_mutations = (
            ("daemon_reload", "start")
            if initially_enabled
            else ("daemon_reload", "enable", "start")
        )
        expected_steps = (
            [
                "transaction_open",
                "daemon_reload",
                "daemon_reload",
            ]
            + (
                []
                if initially_enabled
                else ["enable", "enable"]
            )
            + [
                "start",
                "start",
                "verify",
                "verify",
                "terminal",
            ]
        )
        require(
            result.get("ok") is True
            and result.get("state") == "active"
            and result.get("recovery_required") is False
            and result.get("plan_sha256") == plan.plan_sha256
            and result.get("revision_count") == len(expected_steps),
            "success controller projection was not exact",
            failures,
        )
        require(
            tuple(runtime.mutations) == expected_mutations,
            "success controller mutation sequence changed",
            failures,
        )
        require(
            [revision.step_id for revision in chain] == expected_steps
            and chain[-1].phase == "terminal"
            and chain[-1].terminal_state == "active"
            and chain[-1].owns_enable is (not initially_enabled)
            and chain[-1].owns_start is True,
            "success journal sequence or ownership changed",
            failures,
        )
        require(
            final_store.get("state") == "ready"
            and final_store.get("completed_transaction_count") == 1,
            "terminal transaction did not restore a ready store",
            failures,
        )
        require(
            replay_error == "activation_plan_stale",
            "completed plan replay was not rejected as stale",
            failures,
        )
        return result, tuple(runtime.mutations), len(chain)


def main() -> int:
    failures: list[str] = []
    disabled_result, disabled_mutations, disabled_revisions = (
        run_success_case(
            initially_enabled=False,
            failures=failures,
        )
    )
    enabled_result, enabled_mutations, enabled_revisions = (
        run_success_case(
            initially_enabled=True,
            failures=failures,
        )
    )

    with tempfile.TemporaryDirectory(
        prefix="relay-activation-controller-invalid-"
    ) as temporary:
        root = Path(temporary)
        root.chmod(0o700)
        runtime = FakeRuntime()
        plan = compile_activation_plan(
            runtime.prerequisites,
            runtime.systemd,
        )
        with _open_fixture_store(root) as store:
            invalid_confirmation = error_id(
                lambda: _run_confirmed_activation_with(
                    "invalid",
                    store=store,
                    scanner=runtime.scan,
                    systemd_reader=runtime.read,
                    mutation_runner=runtime.mutate,
                )
            )
            stale_plan = error_id(
                lambda: _run_confirmed_activation_with(
                    "f" * 64,
                    store=store,
                    scanner=runtime.scan,
                    systemd_reader=runtime.read,
                    mutation_runner=runtime.mutate,
                )
            )
            invalid_store = store.inspect_store()
    require(
        invalid_confirmation == "activation_confirmation_invalid"
        and stale_plan == "activation_plan_stale"
            and plan.plan_sha256 != "f" * 64
            and invalid_store.get("completed_transaction_count") == 0
            and runtime.mutations == [],
            "invalid confirmation or stale plan mutated the store",
        failures,
    )

    def drift_on_second_scan(
        scan_count: int,
        current: ActivationPrerequisiteSnapshot,
    ) -> ActivationPrerequisiteSnapshot:
        if scan_count == 2:
            return replace(
                current,
                unit=replace(
                    current.unit,
                    content_sha256="7" * 64,
                ),
            )
        return current

    with tempfile.TemporaryDirectory(
        prefix="relay-activation-controller-prepared-zero-write-"
    ) as temporary:
        root = Path(temporary)
        root.chmod(0o700)
        runtime = FakeRuntime(scan_override=drift_on_second_scan)
        plan = compile_activation_plan(
            runtime.prerequisites,
            runtime.systemd,
        )
        with _open_fixture_store(root) as store:
            preprepared_drift_error = error_id(
                lambda: _run_confirmed_activation_with(
                    plan.plan_sha256 or "",
                    store=store,
                    scanner=runtime.scan,
                    systemd_reader=runtime.read,
                    mutation_runner=runtime.mutate,
                )
            )
            preprepared_store = store.inspect_store()
        require(
            preprepared_drift_error == "activation_controller_failed"
            and preprepared_store.get("state") == "ready"
            and preprepared_store.get("completed_transaction_count") == 0
            and runtime.mutations == [],
            "pre-prepared prerequisite drift wrote durable state",
            failures,
        )

    with tempfile.TemporaryDirectory(
        prefix="relay-activation-controller-failure-"
    ) as temporary:
        root = Path(temporary)
        root.chmod(0o700)
        runtime = FakeRuntime(fail_operation="enable")
        plan = compile_activation_plan(
            runtime.prerequisites,
            runtime.systemd,
        )
        with _open_fixture_store(root) as store:
            failure_error = error_id(
                lambda: _run_confirmed_activation_with(
                    plan.plan_sha256 or "",
                    store=store,
                    scanner=runtime.scan,
                    systemd_reader=runtime.read,
                    mutation_runner=runtime.mutate,
                )
            )
            failed_projection = store.inspect_plan(
                plan.plan_sha256 or ""
            )
            recovery_error = error_id(
                lambda: _run_confirmed_activation_with(
                    plan.plan_sha256 or "",
                    store=store,
                    scanner=runtime.scan,
                    systemd_reader=runtime.read,
                    mutation_runner=runtime.mutate,
                )
            )
        require(
            failure_error == "activation_recovery_required"
            and recovery_error == "activation_recovery_required"
            and failed_projection.get("recovery_required") is True
            and runtime.mutations == ["daemon_reload", "enable"]
            and PRIVATE_CANARY not in failure_error,
            "post-intent failure was not retained for recovery",
            failures,
        )

    def drift_on_third_scan(
        scan_count: int,
        current: ActivationPrerequisiteSnapshot,
    ) -> ActivationPrerequisiteSnapshot:
        if scan_count == 3:
            return replace(
                current,
                unit=replace(
                    current.unit,
                    content_sha256="8" * 64,
                ),
            )
        return current

    with tempfile.TemporaryDirectory(
        prefix="relay-activation-controller-drift-"
    ) as temporary:
        root = Path(temporary)
        root.chmod(0o700)
        runtime = FakeRuntime(scan_override=drift_on_third_scan)
        plan = compile_activation_plan(
            runtime.prerequisites,
            runtime.systemd,
        )
        with _open_fixture_store(root) as store:
            drift_error = error_id(
                lambda: _run_confirmed_activation_with(
                    plan.plan_sha256 or "",
                    store=store,
                    scanner=runtime.scan,
                    systemd_reader=runtime.read,
                    mutation_runner=runtime.mutate,
                )
            )
            drift_projection = store.inspect_plan(
                plan.plan_sha256 or ""
            )
        require(
            drift_error == "activation_recovery_required"
            and drift_projection.get("revision_count") == 1
            and runtime.mutations == [],
            "post-prepared prerequisite drift was not retained",
            failures,
        )

    with tempfile.TemporaryDirectory(
        prefix="relay-activation-controller-terminal-"
    ) as temporary:
        root = Path(temporary)
        root.chmod(0o700)
        runtime = FakeRuntime()
        plan = compile_activation_plan(
            runtime.prerequisites,
            runtime.systemd,
        )
        with _open_fixture_store(root) as raw_store:
            store = FailBeforeTerminalStore(raw_store)
            terminal_error = error_id(
                lambda: _run_confirmed_activation_with(
                    plan.plan_sha256 or "",
                    store=store,
                    scanner=runtime.scan,
                    systemd_reader=runtime.read,
                    mutation_runner=runtime.mutate,
                )
            )
            terminal_store = raw_store.inspect_store()
        require(
            terminal_error == "activation_recovery_required"
            and store.terminal_rejected
            and terminal_store.get("recovery_required") is True,
            "receipt-before-terminal interruption was not recoverable",
            failures,
        )

    admin_source = (
        ROOT / "agentops_mis_cli" / "relay_admin.py"
    ).read_text(encoding="utf-8")
    admin_tree = ast.parse(admin_source)
    controller_imported = any(
        isinstance(node, ast.ImportFrom)
        and node.module
        == "agentops_mis_cli.relay_activation_controller"
        for node in ast.walk(admin_tree)
    )
    controller_called = any(
        isinstance(node, ast.Call)
        and (
            (
                isinstance(node.func, ast.Name)
                and node.func.id == "_run_confirmed_activation"
            )
            or (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "_run_confirmed_activation"
            )
        )
        for node in ast.walk(admin_tree)
    )
    controller_source = (
        ROOT
        / "agentops_mis_cli"
        / "relay_activation_controller.py"
    ).read_text(encoding="utf-8")
    controller_tree = ast.parse(controller_source)
    locked_scanner_called = any(
        isinstance(node, ast.Call)
        and (
            (
                isinstance(node.func, ast.Name)
                and node.func.id
                == "_scan_activation_prerequisites_while_locked"
            )
            or (
                isinstance(node.func, ast.Attribute)
                and node.func.attr
                == "_scan_activation_prerequisites_while_locked"
            )
        )
        for node in ast.walk(controller_tree)
    )
    unlocked_scanner_imported = any(
        isinstance(node, ast.ImportFrom)
        and node.module
        == "agentops_mis_cli.relay_activation_scan"
        and any(
            alias.name == "scan_activation_prerequisites"
            for alias in node.names
        )
        for node in ast.walk(controller_tree)
    )
    cli_output = io.StringIO()
    with contextlib.redirect_stdout(cli_output), contextlib.redirect_stderr(
        cli_output
    ):
        cli_code = relay_admin.main(
            [
                "--root",
                "/",
                "activate",
                "--confirm-activate",
                "--plan-sha256",
                "a" * 64,
            ]
        )
    try:
        cli_payload = json.loads(cli_output.getvalue())
    except json.JSONDecodeError:
        cli_payload = {}
    require(
        not controller_imported
        and not controller_called
        and cli_code != 0
        and cli_payload.get("error_id")
        == "activation_mutation_unavailable",
        "private controller became reachable through the CLI",
        failures,
    )
    require(
        locked_scanner_called and not unlocked_scanner_imported,
        "production controller did not retain locked scanner capability",
        failures,
    )

    public_values = json.dumps(
        {
            "disabled": disabled_result,
            "enabled": enabled_result,
            "errors": [
                invalid_confirmation,
                stale_plan,
                preprepared_drift_error,
                failure_error,
                recovery_error,
                drift_error,
                terminal_error,
            ],
        },
        ensure_ascii=True,
        sort_keys=True,
    )
    require(
        PRIVATE_CANARY not in public_values
        and UNIT_PATH not in public_values
        and "/usr/bin/systemctl" not in public_values,
        "controller output exposed private source detail",
        failures,
    )

    result = {
        "cli_mutation_exposed": False,
        "disabled_initial_mutations": disabled_mutations,
        "disabled_initial_revision_count": disabled_revisions,
        "enabled_initial_mutations": enabled_mutations,
        "enabled_initial_revision_count": enabled_revisions,
        "failure_requires_recovery": (
            failure_error == "activation_recovery_required"
        ),
        "failures": failures,
        "locked_rescan_capability": (
            locked_scanner_called and not unlocked_scanner_imported
        ),
        "network_used": False,
        "ok": not failures,
        "operation": "relay_activation_controller_smoke",
        "private_payload_omitted": PRIVATE_CANARY not in public_values,
        "receipt_before_terminal": True,
        "stale_plan_zero_write": (
            stale_plan == "activation_plan_stale"
            and preprepared_drift_error
            == "activation_controller_failed"
        ),
        "systemd_mutation_performed": False,
    }
    print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
