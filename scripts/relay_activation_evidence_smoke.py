#!/usr/bin/env python3
"""Exercise deterministic Relay activation evidence compilation."""
from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path


sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

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
from agentops_mis_cli.relay_activation_evidence import (  # noqa: E402
    RelayActivationEvidenceError,
    build_activation_journal_identity,
    build_activation_rollback_verification_observation,
    build_activation_step_observation,
)


PRIVATE_CANARY = "ACTIVATION_EVIDENCE_PRIVATE_CANARY"


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
    link_inode: int = 16,
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
            (enablement_link(inode=link_inode),)
            if enabled
            else ()
        ),
    )


def systemd(
    *,
    enabled: bool = False,
    active: bool = False,
    need_reload: bool = False,
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


def error_id(callback) -> str:
    try:
        callback()
    except RelayActivationEvidenceError as exc:
        return str(exc)
    return ""


def main() -> int:
    failures: list[str] = []
    initial_prerequisites = prerequisites()
    initial_systemd = systemd(need_reload=True)
    plan = compile_activation_plan(
        initial_prerequisites,
        initial_systemd,
    )
    require(
        plan.state == "plan_ready"
        and plan.plan_sha256 is not None,
        "fixture did not compile an activation plan",
        failures,
    )
    identity = build_activation_journal_identity(
        initial_prerequisites,
        initial_systemd,
        confirmed_plan_sha256=plan.plan_sha256 or "",
    )
    repeated_identity = build_activation_journal_identity(
        initial_prerequisites,
        initial_systemd,
        confirmed_plan_sha256=plan.plan_sha256 or "",
    )
    require(
        identity == repeated_identity
        and identity.plan_sha256 == plan.plan_sha256,
        "journal identity was not deterministic and plan-bound",
        failures,
    )

    states = {
        "daemon_reload": (
            prerequisites(),
            systemd(),
        ),
        "enable": (
            prerequisites(enabled=True),
            systemd(enabled=True),
        ),
        "start": (
            prerequisites(enabled=True),
            systemd(enabled=True, active=True),
        ),
        "verify": (
            prerequisites(enabled=True),
            systemd(enabled=True, active=True),
        ),
        "rollback_stop": (
            prerequisites(enabled=True),
            systemd(enabled=True),
        ),
        "rollback_disable": (
            prerequisites(),
            systemd(),
        ),
    }
    observations = {
        step_id: build_activation_step_observation(
            identity,
            step_id=step_id,
            prerequisites=current_prerequisites,
            systemd=current_systemd,
        )
        for step_id, (
            current_prerequisites,
            current_systemd,
        ) in states.items()
    }
    rollback_verify = build_activation_rollback_verification_observation(
        identity,
        prerequisites=prerequisites(),
        systemd=systemd(),
    )
    repeated_rollback_verify = (
        build_activation_rollback_verification_observation(
            identity,
            prerequisites=prerequisites(),
            systemd=systemd(),
        )
    )
    observations["rollback_verify"] = rollback_verify
    repeat_verify = build_activation_step_observation(
        identity,
        step_id="verify",
        prerequisites=states["verify"][0],
        systemd=states["verify"][1],
    )
    require(
        len(
            {
                value.observation_sha256
                for value in observations.values()
            }
        )
        == len(observations)
        and observations["verify"] == repeat_verify
        and rollback_verify == repeated_rollback_verify
        and rollback_verify.observation_id == "rollback_verified",
        "step observations were not unique and deterministic",
        failures,
    )

    changed_link = build_activation_step_observation(
        identity,
        step_id="enable",
        prerequisites=prerequisites(
            enabled=True,
            link_inode=99,
        ),
        systemd=systemd(enabled=True),
    )
    changed_invocation = build_activation_step_observation(
        identity,
        step_id="start",
        prerequisites=prerequisites(enabled=True),
        systemd=systemd(
            enabled=True,
            active=True,
            invocation_id="2" * 32,
        ),
    )
    require(
        changed_link.observation_sha256
        != observations["enable"].observation_sha256
        and changed_invocation.observation_sha256
        != observations["start"].observation_sha256,
        "ownership evidence ignored link or invocation identity",
        failures,
    )

    wrong_hash_error = error_id(
        lambda: build_activation_journal_identity(
            initial_prerequisites,
            initial_systemd,
            confirmed_plan_sha256="f" * 64,
        )
    )
    already_active_plan = compile_activation_plan(
        prerequisites(enabled=True),
        systemd(enabled=True, active=True),
    )
    already_active_error = error_id(
        lambda: build_activation_journal_identity(
            prerequisites(enabled=True),
            systemd(enabled=True, active=True),
            confirmed_plan_sha256=(
                already_active_plan.plan_sha256 or ("0" * 64)
            ),
        )
    )
    wrong_unit_error = error_id(
        lambda: build_activation_step_observation(
            identity,
            step_id="verify",
            prerequisites=prerequisites(
                enabled=True,
                unit_digest="8" * 64,
            ),
            systemd=systemd(enabled=True, active=True),
        )
    )
    invalid_step_error = error_id(
        lambda: build_activation_step_observation(
            identity,
            step_id="restart",
            prerequisites=prerequisites(enabled=True),
            systemd=systemd(enabled=True, active=True),
        )
    )
    wrong_state_error = error_id(
        lambda: build_activation_step_observation(
            identity,
            step_id="enable",
            prerequisites=prerequisites(),
            systemd=systemd(),
        )
    )
    tampered_identity = replace(
        identity,
        unit_identity_sha256="7" * 64,
    )
    tampered_identity_error = error_id(
        lambda: build_activation_step_observation(
            tampered_identity,
            step_id="verify",
            prerequisites=prerequisites(enabled=True),
            systemd=systemd(enabled=True, active=True),
        )
    )
    rollback_state_error = error_id(
        lambda: build_activation_rollback_verification_observation(
            identity,
            prerequisites=prerequisites(enabled=True),
            systemd=systemd(enabled=True),
        )
    )
    errors = (
        wrong_hash_error,
        already_active_error,
        wrong_unit_error,
        invalid_step_error,
        wrong_state_error,
        tampered_identity_error,
        rollback_state_error,
    )
    require(
        all(
            value == "activation_evidence_invalid"
            and PRIVATE_CANARY not in value
            for value in errors
        ),
        "invalid evidence input was accepted or leaked private detail",
        failures,
    )
    public_values = json.dumps(
        {
            step_id: {
                "observation_id": value.observation_id,
                "observation_sha256": value.observation_sha256,
            }
            for step_id, value in observations.items()
        },
        ensure_ascii=True,
        sort_keys=True,
    )
    require(
        PRIVATE_CANARY not in public_values
        and UNIT_PATH not in public_values
        and "/usr/bin/systemctl" not in public_values,
        "evidence output exposed source identity detail",
        failures,
    )

    result = {
        "deterministic_plan_bound_identity": (
            identity == repeated_identity
        ),
        "failures": failures,
        "invalid_inputs_redacted": all(
            value == "activation_evidence_invalid"
            for value in errors
        ),
        "network_used": False,
        "observation_count": len(observations),
        "ok": not failures,
        "operation": "relay_activation_evidence_smoke",
        "ownership_identity_changes_detected": (
            changed_link.observation_sha256
            != observations["enable"].observation_sha256
            and changed_invocation.observation_sha256
            != observations["start"].observation_sha256
        ),
        "private_payload_omitted": PRIVATE_CANARY not in public_values,
        "systemd_mutation_performed": False,
    }
    print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
