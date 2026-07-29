#!/usr/bin/env python3
"""Exercise the pure Relay activation-plan core without host side effects."""
from __future__ import annotations

import builtins
import json
import os
import socket
import subprocess
import sys
from dataclasses import replace
from pathlib import Path


sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agentops_mis_cli.relay_activation import (  # noqa: E402
    ACTIVATION_PLAN_SCHEMA,
    CONFIG_PATH,
    ENABLEMENT_LINK_PATH,
    RUNTIME_DIRECTORY,
    STATE_DIRECTORY,
    UNIT_NAME,
    UNIT_PATH,
    ActivationPrerequisiteSnapshot,
    ActivationPlan,
    DirectoryIdentity,
    FileIdentity,
    LinkIdentity,
    RelayActivationError,
    RootIdentity,
    SystemdSnapshot,
    compile_activation_plan,
    parse_systemd_show_bytes,
    project_activation_plan,
)


PRIVATE_CANARY = "relay-activation-private-canary"
HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
HASH_D = "d" * 64
HASH_E = "e" * 64
HASH_F = "f" * 64


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def identity(
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


def prerequisites() -> ActivationPrerequisiteSnapshot:
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
        release_tree_sha256=HASH_A,
        unit=identity(UNIT_PATH, HASH_B, inode=10),
        config=identity(CONFIG_PATH, HASH_C, inode=11, group=service_gid, mode=0o640),
        certificate=identity(
            "/etc/agentops-mis-relay/tls/relay.crt",
            HASH_D,
            inode=12,
        ),
        private_key=identity(
            "/etc/agentops-mis-relay/tls/relay.key",
            HASH_E,
            inode=13,
            owner=service_uid,
            group=service_gid,
            mode=0o600,
        ),
        route_keys=(
            identity(
                "/etc/agentops-mis-relay/routes/route-a.key",
                HASH_F,
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
        systemctl=identity(
            "/usr/bin/systemctl",
            "9" * 64,
            inode=15,
            mode=0o755,
        ),
        enablement_links=(),
    )


def systemd_bytes(**overrides: str) -> bytes:
    values = {
        "LoadState": "loaded",
        "UnitFileState": "disabled",
        "ActiveState": "inactive",
        "SubState": "dead",
        "Result": "success",
        "ExecMainStatus": "0",
        "FragmentPath": UNIT_PATH,
        "NeedDaemonReload": "no",
        "InvocationID": "",
        "MainPID": "0",
    }
    values.update(overrides)
    order = (
        "LoadState",
        "UnitFileState",
        "ActiveState",
        "SubState",
        "Result",
        "ExecMainStatus",
        "FragmentPath",
        "NeedDaemonReload",
        "InvocationID",
        "MainPID",
    )
    return "".join(f"{name}={values[name]}\n" for name in order).encode("ascii")


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


def expect_parse_failure(data: bytes, label: str, failures: list[str]) -> None:
    try:
        parse_systemd_show_bytes(data)
    except RelayActivationError:
        return
    failures.append(f"{label}: parser accepted invalid state")


def main() -> int:
    failures: list[str] = []
    side_effect_calls: list[str] = []

    original_open = builtins.open
    original_os_open = os.open
    original_socket = socket.socket
    original_create_connection = socket.create_connection
    original_run = subprocess.run
    original_popen = subprocess.Popen

    def blocked(*_args, **_kwargs):
        side_effect_calls.append("blocked")
        raise AssertionError("activation plan core attempted external behavior")

    builtins.open = blocked
    os.open = blocked
    socket.socket = blocked
    socket.create_connection = blocked
    subprocess.run = blocked
    subprocess.Popen = blocked
    try:
        base_prerequisites = prerequisites()
        base_systemd = parse_systemd_show_bytes(systemd_bytes())
        plan = compile_activation_plan(base_prerequisites, base_systemd)
        projected = project_activation_plan(plan)

        plan_keys = {
            "ok",
            "operation_id",
            "plan_sha256",
            "prerequisites",
            "release_id",
            "requested",
            "schema_id",
            "state",
            "systemd",
            "unit_id",
            "version_id",
        }
        require(plan.state == "plan_ready", "base plan not ready", failures)
        require(plan.ok, "base plan not ok", failures)
        require(set(projected) == plan_keys, "plan output keys drifted", failures)
        require(
            projected.get("schema_id") == ACTIVATION_PLAN_SCHEMA,
            "plan schema missing",
            failures,
        )
        require(
            projected.get("requested")
            == {"daemon_reload": True, "enable": True, "start": True},
            "base requested actions mismatch",
            failures,
        )
        require(
            projected.get("systemd")
            == {
                "active_state": "inactive",
                "load_state": "loaded",
                "sub_state": "dead",
                "unit_file_state": "disabled",
            },
            "public systemd projection drifted",
            failures,
        )

        active_systemd = parse_systemd_show_bytes(
            systemd_bytes(
                UnitFileState="enabled",
                ActiveState="active",
                SubState="running",
                InvocationID="2" * 32,
                MainPID="42",
            )
        )
        enabled_prerequisites = replace(
            base_prerequisites,
            enablement_links=(enablement_link(),),
        )
        active = compile_activation_plan(enabled_prerequisites, active_systemd)
        active_output = project_activation_plan(active)
        require(active.state == "already_active", "active state not idempotent", failures)
        require(
            "plan_sha256" not in active_output
            and active_output.get("requested")
            == {"daemon_reload": False, "enable": False, "start": False},
            "active output exposed executable plan",
            failures,
        )

        enabled_inactive = compile_activation_plan(
            enabled_prerequisites,
            parse_systemd_show_bytes(systemd_bytes(UnitFileState="enabled")),
        )
        retained_inactive = compile_activation_plan(
            enabled_prerequisites,
            parse_systemd_show_bytes(
                systemd_bytes(
                    UnitFileState="enabled",
                    InvocationID="4" * 32,
                )
            ),
        )
        stopped_inactive = compile_activation_plan(
            enabled_prerequisites,
            parse_systemd_show_bytes(
                systemd_bytes(
                    UnitFileState="enabled",
                    ExecMainStatus="15",
                    InvocationID="5" * 32,
                )
            ),
        )
        disabled_active = compile_activation_plan(
            base_prerequisites,
            parse_systemd_show_bytes(
                systemd_bytes(
                    ActiveState="active",
                    SubState="running",
                    InvocationID="3" * 32,
                    MainPID="43",
                )
            ),
        )
        require(
            project_activation_plan(enabled_inactive).get("requested")
            == {"daemon_reload": True, "enable": False, "start": True},
            "enabled inactive action plan mismatch",
            failures,
        )
        require(
            retained_inactive.ok is True
            and retained_inactive.systemd is not None
            and retained_inactive.systemd.invocation_id == "4" * 32,
            "inactive state rejected a retained valid invocation id",
            failures,
        )
        require(
            stopped_inactive.ok is True
            and stopped_inactive.systemd is not None
            and stopped_inactive.systemd.exec_main_status == 15,
            "inactive clean stop rejected a bounded signal status",
            failures,
        )
        require(
            project_activation_plan(disabled_active).get("requested")
            == {"daemon_reload": True, "enable": True, "start": False},
            "disabled active action plan mismatch",
            failures,
        )

        recovery = compile_activation_plan(
            replace(base_prerequisites, recovery_required=True),
            base_systemd,
        )
        invalid = compile_activation_plan(
            replace(base_prerequisites, installed_state="invalid"),
            base_systemd,
        )
        expected_terminal_keys = {"ok", "operation_id", "schema_id", "state"}
        require(
            project_activation_plan(recovery)
            == {
                "ok": False,
                "operation_id": "activate",
                "schema_id": ACTIVATION_PLAN_SCHEMA,
                "state": "recovery_required",
            },
            "recovery projection drifted",
            failures,
        )
        require(
            set(project_activation_plan(invalid)) == expected_terminal_keys
            and invalid.state == "invalid",
            "invalid projection drifted",
            failures,
        )
        invalid_prerequisites = (
            replace(
                base_prerequisites,
                root=replace(base_prerequisites.root, owner_id=1701),
            ),
            replace(
                base_prerequisites,
                root=replace(base_prerequisites.root, kind="regular"),
            ),
            replace(base_prerequisites, service_uid=0),
            replace(
                base_prerequisites,
                config=replace(base_prerequisites.config, nlink=2),
            ),
            replace(
                base_prerequisites,
                config=replace(base_prerequisites.config, nlink=True),
            ),
            replace(
                base_prerequisites,
                config=replace(base_prerequisites.config, kind="directory"),
            ),
            replace(
                base_prerequisites,
                state_directory=replace(
                    base_prerequisites.state_directory,
                    mode=0o755,
                ),
            ),
            replace(
                base_prerequisites,
                state_directory=replace(
                    base_prerequisites.state_directory,
                    kind="regular",
                ),
            ),
            replace(
                base_prerequisites,
                runtime_directory=replace(
                    base_prerequisites.runtime_directory,
                    canonical_path="/tmp/agentops-mis-relay",
                ),
            ),
            replace(
                base_prerequisites,
                config=replace(
                    base_prerequisites.config,
                    group_id=0,
                    mode=0o600,
                ),
            ),
            replace(
                base_prerequisites,
                certificate=replace(
                    base_prerequisites.certificate,
                    canonical_path=base_prerequisites.config.canonical_path,
                ),
            ),
            replace(
                base_prerequisites,
                certificate=replace(
                    base_prerequisites.certificate,
                    device_id=base_prerequisites.config.device_id,
                    inode=base_prerequisites.config.inode,
                ),
            ),
            replace(
                base_prerequisites,
                service_account_ready="true",  # type: ignore[arg-type]
            ),
            replace(
                base_prerequisites,
                release_id="different-0.1.0-" + ("1" * 12),
            ),
            replace(
                base_prerequisites,
                config=replace(
                    base_prerequisites.config,
                    canonical_path="//etc/agentops-mis-relay/config.json",
                ),
            ),
            replace(base_prerequisites, enablement_links=(enablement_link(),)),
        )
        require(
            all(
                compile_activation_plan(value, base_systemd).state == "invalid"
                for value in invalid_prerequisites
            ),
            "unsafe prerequisite snapshot was accepted",
            failures,
        )
        require(
            compile_activation_plan(
                base_prerequisites,
                parse_systemd_show_bytes(
                    systemd_bytes(UnitFileState="enabled")
                ),
            ).state
            == "invalid",
            "enabled state without enablement link was accepted",
            failures,
        )
        invalid_enabled_prerequisites = (
            replace(
                enabled_prerequisites,
                enablement_links=(
                    replace(enablement_link(), kind="regular"),
                ),
            ),
            replace(
                enabled_prerequisites,
                enablement_links=(
                    replace(
                        enablement_link(),
                        canonical_path=(
                            "/etc/systemd/system/not-an-enablement-link"
                        ),
                    ),
                ),
            ),
            replace(
                enabled_prerequisites,
                enablement_links=(
                    replace(enablement_link(), owner_id=1701),
                ),
            ),
            replace(
                enabled_prerequisites,
                enablement_links=(
                    replace(enablement_link(), nlink=True),
                ),
            ),
        )
        require(
            all(
                compile_activation_plan(
                    value,
                    parse_systemd_show_bytes(
                        systemd_bytes(UnitFileState="enabled")
                    ),
                ).state
                == "invalid"
                for value in invalid_enabled_prerequisites
            ),
            "unsafe enablement link snapshot was accepted",
            failures,
        )

        baseline_hash = plan.plan_sha256
        private_mutations = (
            replace(
                base_prerequisites,
                root=replace(base_prerequisites.root, inode=99),
            ),
            replace(base_prerequisites, release_tree_sha256="8" * 64),
            replace(
                base_prerequisites,
                unit=replace(base_prerequisites.unit, content_sha256="7" * 64),
            ),
            replace(
                base_prerequisites,
                config=replace(base_prerequisites.config, content_sha256="6" * 64),
            ),
            replace(
                base_prerequisites,
                certificate=replace(
                    base_prerequisites.certificate,
                    content_sha256="5" * 64,
                ),
            ),
            replace(
                base_prerequisites,
                private_key=replace(
                    base_prerequisites.private_key,
                    content_sha256="4" * 64,
                ),
            ),
            replace(
                base_prerequisites,
                route_keys=(
                    replace(
                        base_prerequisites.route_keys[0],
                        content_sha256="3" * 64,
                    ),
                ),
            ),
            replace(base_prerequisites, service_group_ids=(1701, 1702)),
            replace(
                base_prerequisites,
                systemctl=replace(
                    base_prerequisites.systemctl,
                    content_sha256="2" * 64,
                ),
            ),
            replace(
                base_prerequisites,
                trusted_parent_chain_sha256="1" * 64,
            ),
        )
        changed_hashes = {
            compile_activation_plan(value, base_systemd).plan_sha256
            for value in private_mutations
        }
        require(
            None not in changed_hashes
            and baseline_hash not in changed_hashes
            and len(changed_hashes) == len(private_mutations),
            "private input did not uniquely bind plan hash",
            failures,
        )
        enabled_baseline = compile_activation_plan(
            enabled_prerequisites,
            parse_systemd_show_bytes(systemd_bytes(UnitFileState="enabled")),
        )
        changed_link = compile_activation_plan(
            replace(
                enabled_prerequisites,
                enablement_links=(enablement_link(inode=17),),
            ),
            parse_systemd_show_bytes(systemd_bytes(UnitFileState="enabled")),
        )
        require(
            enabled_baseline.plan_sha256 is not None
            and changed_link.plan_sha256 is not None
            and enabled_baseline.plan_sha256 != changed_link.plan_sha256,
            "enablement link identity did not bind plan hash",
            failures,
        )

        invalid_systemd_cases = {
            "missing": b"\n".join(systemd_bytes().splitlines()[:-1]) + b"\n",
            "duplicate": systemd_bytes() + b"MainPID=0\n",
            "unknown": systemd_bytes().replace(b"MainPID=0", b"Unknown=0"),
            "oversized": b"x" * (16 * 1024 + 1),
            "load": systemd_bytes(LoadState="not-found"),
            "unit-file": systemd_bytes(UnitFileState="masked"),
            "active": systemd_bytes(ActiveState="failed"),
            "sub": systemd_bytes(SubState="running"),
            "result": systemd_bytes(Result="failed"),
            "active-exec-status": systemd_bytes(
                ActiveState="active",
                SubState="running",
                ExecMainStatus="15",
                InvocationID="2" * 32,
                MainPID="42",
            ),
            "inactive-exec-status": systemd_bytes(
                Result="",
                ExecMainStatus="15",
            ),
            "inactive-unexpected-signal": systemd_bytes(
                ExecMainStatus="9",
            ),
            "exec-status-overflow": systemd_bytes(ExecMainStatus="256"),
            "fragment": systemd_bytes(FragmentPath="/tmp/../unsafe.service"),
            "double-root": systemd_bytes(FragmentPath="//etc/systemd/system/unsafe"),
            "reload": systemd_bytes(NeedDaemonReload="maybe"),
            "invocation": systemd_bytes(
                ActiveState="active",
                SubState="running",
                InvocationID="not-an-id",
                MainPID="42",
            ),
            "inactive-invocation": systemd_bytes(
                InvocationID="not-an-id",
            ),
            "pid": systemd_bytes(
                ActiveState="active",
                SubState="running",
                InvocationID="2" * 32,
                MainPID="0",
            ),
        }
        for label, data in invalid_systemd_cases.items():
            expect_parse_failure(data, label, failures)

        active_reload = compile_activation_plan(
            enabled_prerequisites,
            parse_systemd_show_bytes(
                systemd_bytes(
                    UnitFileState="enabled",
                    ActiveState="active",
                    SubState="running",
                    NeedDaemonReload="yes",
                    InvocationID="2" * 32,
                    MainPID="42",
                )
            ),
        )
        require(
            active_reload.state == "invalid" and not active_reload.ok,
            "active reload state was treated as executable",
            failures,
        )
        forged_systemd = compile_activation_plan(
            base_prerequisites,
            SystemdSnapshot(
                load_state="loaded",
                unit_file_state="disabled",
                active_state="active",
                sub_state="running",
                result="success",
                exec_main_status=0,
                fragment_path=UNIT_PATH,
                need_daemon_reload=False,
                invocation_id="2" * 32,
                main_pid=0,
            ),
        )
        require(
            forged_systemd.state == "invalid" and not forged_systemd.ok,
            "forged systemd snapshot bypassed validation",
            failures,
        )
        forged_projection = project_activation_plan(
            ActivationPlan(
                state="already_active",
                ok=True,
                release_id=base_prerequisites.release_id,
                version_id=base_prerequisites.version_id,
                plan_sha256="1" * 64,
                systemd=active_systemd,
            )
        )
        require(
            forged_projection
            == {
                "ok": False,
                "operation_id": "activate",
                "schema_id": ACTIVATION_PLAN_SCHEMA,
                "state": "invalid",
            },
            "forged public plan bypassed projection validation",
            failures,
        )
        forged_release_projection = project_activation_plan(
            replace(plan, release_id=PRIVATE_CANARY)
        )
        require(
            forged_release_projection
            == {
                "ok": False,
                "operation_id": "activate",
                "schema_id": ACTIVATION_PLAN_SCHEMA,
                "state": "invalid",
            },
            "forged release identifier bypassed projection validation",
            failures,
        )
        forged_ready_projection = project_activation_plan(
            ActivationPlan(
                state="plan_ready",
                ok=True,
                release_id=base_prerequisites.release_id,
                version_id=base_prerequisites.version_id,
                plan_sha256="1" * 64,
                systemd=base_systemd,
                daemon_reload=True,
                enable=True,
                start=True,
            )
        )
        require(
            forged_ready_projection
            == {
                "ok": False,
                "operation_id": "activate",
                "schema_id": ACTIVATION_PLAN_SCHEMA,
                "state": "invalid",
            },
            "hand-constructed ready plan bypassed origin validation",
            failures,
        )
        tampered_compiled_projection = project_activation_plan(
            replace(plan, plan_sha256="1" * 64)
        )
        require(
            tampered_compiled_projection
            == {
                "ok": False,
                "operation_id": "activate",
                "schema_id": ACTIVATION_PLAN_SCHEMA,
                "state": "invalid",
            },
            "compiled plan hash mutation bypassed projection binding",
            failures,
        )

        serialized = json.dumps(projected, ensure_ascii=True, sort_keys=True)
        forbidden_values = (
            CONFIG_PATH,
            UNIT_PATH,
            "/usr/bin/systemctl",
            "/etc/agentops-mis-relay/tls/relay.key",
            "/etc/agentops-mis-relay/routes/route-a.key",
            PRIVATE_CANARY,
            "InvocationID",
            "MainPID",
            "FragmentPath",
        )
        require(
            not any(value in serialized for value in forbidden_values),
            "public plan leaked private inputs",
            failures,
        )
    finally:
        builtins.open = original_open
        os.open = original_os_open
        socket.socket = original_socket
        socket.create_connection = original_create_connection
        subprocess.run = original_run
        subprocess.Popen = original_popen

    require(not side_effect_calls, "pure core attempted external behavior", failures)
    result = {
        "external_behavior": False,
        "failures": failures,
        "ok": not failures,
        "operation": "relay_activation_plan_smoke",
        "private_inputs_omitted": True,
        "prerequisite_invalid_cases": 21,
        "schema_id": ACTIVATION_PLAN_SCHEMA,
        "systemd_invalid_cases": len(invalid_systemd_cases),
    }
    print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
