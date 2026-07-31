#!/usr/bin/env python3
"""Exercise deterministic, read-only Relay activation recovery decisions."""
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
    build_activation_journal_identity,
    build_activation_rollback_verification_observation,
    build_activation_step_observation,
)
from agentops_mis_cli.relay_activation_journal import (  # noqa: E402
    GENESIS_REVISION_SHA256,
    ActivationJournalRecoverySnapshot,
    build_activation_receipt,
    build_activation_revision,
    parse_activation_receipt,
    parse_activation_revision,
)
from agentops_mis_cli.relay_activation_recovery import (  # noqa: E402
    RelayActivationRecoveryError,
    compile_activation_recovery_decision,
    project_activation_recovery_decision,
)


PRIVATE_CANARY = "RECOVERY_DECISION_PRIVATE_CANARY"
INTENTS = {
    "daemon_reload": "daemon_reload_requested",
    "enable": "enable_requested",
    "rollback_disable": "rollback_disable_requested",
    "rollback_stop": "rollback_stop_requested",
    "start": "start_requested",
    "verify": "verify_requested",
}


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


def prerequisites(
    *,
    enabled: bool = False,
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
        unit=file_identity(UNIT_PATH, "b" * 64, inode=10),
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
            (
                LinkIdentity(
                    kind="symlink",
                    canonical_path=ENABLEMENT_LINK_PATH,
                    target=UNIT_PATH,
                    device_id=7,
                    inode=link_inode,
                    owner_id=0,
                    group_id=0,
                    nlink=1,
                ),
            )
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
    exec_main_status: int = 0,
    retain_inactive_invocation: bool = False,
) -> SystemdSnapshot:
    return SystemdSnapshot(
        load_state="loaded",
        unit_file_state="enabled" if enabled else "disabled",
        active_state="active" if active else "inactive",
        sub_state="running" if active else "dead",
        result="success",
        exec_main_status=exec_main_status,
        fragment_path=UNIT_PATH,
        need_daemon_reload=need_reload,
        invocation_id=(
            invocation_id
            if active or retain_inactive_invocation
            else ""
        ),
        main_pid=1701 if active else 0,
    )


def append_revision(
    records: list[bytes],
    identity,
    *,
    phase: str,
    step_id: str,
    intent_id: str | None = None,
    observation_id: str | None = None,
    observation_sha256: str | None = None,
    owns_enable: bool = False,
    owns_start: bool = False,
    terminal_state: str | None = None,
    receipt_sha256: str | None = None,
) -> None:
    records.append(
        build_activation_revision(
            identity,
            revision=len(records) + 1,
            previous_revision_sha256=(
                GENESIS_REVISION_SHA256
                if not records
                else parse_activation_revision(
                    records[-1]
                ).record_sha256
            ),
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
    )


def append_intent(
    records: list[bytes],
    identity,
    step_id: str,
    *,
    owns_enable: bool,
    owns_start: bool,
    intent_id: str | None = None,
) -> None:
    append_revision(
        records,
        identity,
        phase="intent",
        step_id=step_id,
        intent_id=intent_id or INTENTS[step_id],
        owns_enable=owns_enable,
        owns_start=owns_start,
    )


def append_observed(
    records: list[bytes],
    identity,
    step_id: str,
    current_prerequisites: ActivationPrerequisiteSnapshot,
    current_systemd: SystemdSnapshot,
    *,
    owns_enable: bool,
    owns_start: bool,
    intent_id: str | None = None,
    rollback_verify: bool = False,
) -> None:
    observation = (
        build_activation_rollback_verification_observation(
            identity,
            prerequisites=current_prerequisites,
            systemd=current_systemd,
        )
        if rollback_verify
        else build_activation_step_observation(
            identity,
            step_id=step_id,
            prerequisites=current_prerequisites,
            systemd=current_systemd,
        )
    )
    append_revision(
        records,
        identity,
        phase="observed",
        step_id=step_id,
        intent_id=intent_id or INTENTS[step_id],
        observation_id=observation.observation_id,
        observation_sha256=observation.observation_sha256,
        owns_enable=owns_enable,
        owns_start=owns_start,
    )


def snapshot(
    records: list[bytes],
    receipt: bytes | None = None,
) -> ActivationJournalRecoverySnapshot:
    return ActivationJournalRecoverySnapshot(
        revisions=tuple(parse_activation_revision(raw) for raw in records),
        receipt=(
            parse_activation_receipt(receipt)
            if receipt is not None
            else None
        ),
    )


def expect_invalid(callback, failures: list[str], label: str) -> None:
    try:
        callback()
    except RelayActivationRecoveryError as exc:
        if exc.error_id != "activation_recovery_decision_invalid":
            failures.append(f"{label}: wrong bounded error")
        return
    failures.append(f"{label}: unexpectedly succeeded")


def main() -> int:
    failures: list[str] = []
    pre_prerequisites = prerequisites()
    pre_systemd = systemd(need_reload=True)
    plan = compile_activation_plan(pre_prerequisites, pre_systemd)
    require(
        plan.ok is True and plan.plan_sha256 is not None,
        "fixture plan did not compile",
        failures,
    )
    identity = build_activation_journal_identity(
        pre_prerequisites,
        pre_systemd,
        confirmed_plan_sha256=str(plan.plan_sha256),
    )
    records: list[bytes] = []
    append_revision(
        records,
        identity,
        phase="prepared",
        step_id="transaction_open",
    )

    prepared_resume = compile_activation_recovery_decision(
        snapshot(records),
        pre_prerequisites,
        pre_systemd,
        requested_outcome="resume",
    )
    prepared_rollback = compile_activation_recovery_decision(
        snapshot(records),
        pre_prerequisites,
        pre_systemd,
        requested_outcome="rollback",
    )
    require(
        prepared_resume.action_id == "resume"
        and prepared_resume.operation_id == "run_step"
        and prepared_resume.step_id == "daemon_reload",
        "prepared recovery did not resume daemon reload",
        failures,
    )
    require(
        prepared_rollback.action_id == "blocked"
        and prepared_rollback.reason_id == "no_owned_change",
        "prepared rollback was not safely blocked",
        failures,
    )

    append_intent(
        records,
        identity,
        "daemon_reload",
        owns_enable=False,
        owns_start=False,
    )
    daemon_retry = compile_activation_recovery_decision(
        snapshot(records),
        pre_prerequisites,
        pre_systemd,
        requested_outcome="resume",
    )
    post_daemon_systemd = systemd()
    daemon_record = compile_activation_recovery_decision(
        snapshot(records),
        pre_prerequisites,
        post_daemon_systemd,
        requested_outcome="resume",
    )
    require(
        daemon_retry.operation_id == "run_step"
        and daemon_retry.step_id == "daemon_reload",
        "pre-mutation intent was not retried",
        failures,
    )
    require(
        daemon_record.operation_id == "record_observation"
        and daemon_record.observation_sha256 is not None,
        "post-mutation intent was not resumable as observation",
        failures,
    )

    append_observed(
        records,
        identity,
        "daemon_reload",
        pre_prerequisites,
        post_daemon_systemd,
        owns_enable=False,
        owns_start=False,
    )
    daemon_observed = compile_activation_recovery_decision(
        snapshot(records),
        pre_prerequisites,
        post_daemon_systemd,
        requested_outcome="resume",
    )
    require(
        daemon_observed.step_id == "enable"
        and daemon_observed.operation_id == "run_step",
        "daemon observation did not select enable",
        failures,
    )

    append_intent(
        records,
        identity,
        "enable",
        owns_enable=False,
        owns_start=False,
    )
    enabled_prerequisites = prerequisites(enabled=True)
    enabled_systemd = systemd(enabled=True)
    append_observed(
        records,
        identity,
        "enable",
        enabled_prerequisites,
        enabled_systemd,
        owns_enable=True,
        owns_start=False,
    )
    enable_resume = compile_activation_recovery_decision(
        snapshot(records),
        enabled_prerequisites,
        enabled_systemd,
        requested_outcome="resume",
    )
    enable_rollback = compile_activation_recovery_decision(
        snapshot(records),
        enabled_prerequisites,
        enabled_systemd,
        requested_outcome="rollback",
    )
    require(
        enable_resume.step_id == "start",
        "enable observation did not select start",
        failures,
    )
    require(
        enable_rollback.action_id == "inverse"
        and enable_rollback.step_id == "rollback_disable",
        "owned enable did not permit exact inverse",
        failures,
    )

    append_intent(
        records,
        identity,
        "start",
        owns_enable=True,
        owns_start=False,
    )
    active_systemd = systemd(enabled=True, active=True)
    interrupted_start = compile_activation_recovery_decision(
        snapshot(records),
        enabled_prerequisites,
        active_systemd,
        requested_outcome="rollback",
    )
    require(
        interrupted_start.action_id == "blocked"
        and interrupted_start.reason_id == "ownership_ambiguous",
        "ambiguous post-start ownership was not blocked",
        failures,
    )
    append_observed(
        records,
        identity,
        "start",
        enabled_prerequisites,
        active_systemd,
        owns_enable=True,
        owns_start=True,
    )
    start_resume = compile_activation_recovery_decision(
        snapshot(records),
        enabled_prerequisites,
        active_systemd,
        requested_outcome="resume",
    )
    start_rollback = compile_activation_recovery_decision(
        snapshot(records),
        enabled_prerequisites,
        active_systemd,
        requested_outcome="rollback",
    )
    require(
        start_resume.step_id == "verify",
        "start observation did not select verify",
        failures,
    )
    require(
        start_rollback.action_id == "inverse"
        and start_rollback.step_id == "rollback_stop",
        "owned start did not permit exact inverse",
        failures,
    )

    rollback_records = list(records)
    append_intent(
        rollback_records,
        identity,
        "rollback_stop",
        owns_enable=True,
        owns_start=True,
    )
    stopped_systemd = systemd(
        enabled=True,
        exec_main_status=15,
        retain_inactive_invocation=True,
    )
    rollback_stop_observation = compile_activation_recovery_decision(
        snapshot(rollback_records),
        enabled_prerequisites,
        stopped_systemd,
        requested_outcome="rollback",
    )
    require(
        rollback_stop_observation.action_id == "inverse"
        and rollback_stop_observation.operation_id
        == "record_observation"
        and rollback_stop_observation.step_id == "rollback_stop",
        "completed rollback stop was not recordable",
        failures,
    )
    append_observed(
        rollback_records,
        identity,
        "rollback_stop",
        enabled_prerequisites,
        stopped_systemd,
        owns_enable=True,
        owns_start=False,
    )
    rollback_disable = compile_activation_recovery_decision(
        snapshot(rollback_records),
        enabled_prerequisites,
        stopped_systemd,
        requested_outcome="rollback",
    )
    require(
        rollback_disable.action_id == "inverse"
        and rollback_disable.operation_id == "run_step"
        and rollback_disable.step_id == "rollback_disable",
        "rollback stop did not select disable",
        failures,
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
    rollback_disable_observation = (
        compile_activation_recovery_decision(
            snapshot(rollback_records),
            restored_prerequisites,
            restored_systemd,
            requested_outcome="rollback",
        )
    )
    require(
        rollback_disable_observation.action_id == "inverse"
        and rollback_disable_observation.operation_id
        == "record_observation"
        and rollback_disable_observation.step_id
        == "rollback_disable",
        "completed rollback disable was not recordable",
        failures,
    )
    append_observed(
        rollback_records,
        identity,
        "rollback_disable",
        restored_prerequisites,
        restored_systemd,
        owns_enable=False,
        owns_start=False,
    )
    rollback_verify = compile_activation_recovery_decision(
        snapshot(rollback_records),
        restored_prerequisites,
        restored_systemd,
        requested_outcome="rollback",
    )
    resume_after_rollback = compile_activation_recovery_decision(
        snapshot(rollback_records),
        restored_prerequisites,
        restored_systemd,
        requested_outcome="resume",
    )
    require(
        rollback_verify.action_id == "inverse"
        and rollback_verify.operation_id == "run_step"
        and rollback_verify.step_id == "verify"
        and resume_after_rollback.action_id == "blocked"
        and resume_after_rollback.reason_id
        == "rollback_contract_incomplete",
        "rollback did not require verification or blocked resume",
        failures,
    )
    append_intent(
        rollback_records,
        identity,
        "verify",
        owns_enable=False,
        owns_start=False,
        intent_id="rollback_verify_requested",
    )
    rollback_verify_observation = (
        compile_activation_recovery_decision(
            snapshot(rollback_records),
            restored_prerequisites,
            restored_systemd,
            requested_outcome="rollback",
        )
    )
    require(
        rollback_verify_observation.action_id == "inverse"
        and rollback_verify_observation.operation_id
        == "record_observation"
        and rollback_verify_observation.step_id == "verify",
        "completed rollback verification was not recordable",
        failures,
    )
    append_observed(
        rollback_records,
        identity,
        "verify",
        restored_prerequisites,
        restored_systemd,
        owns_enable=False,
        owns_start=False,
        intent_id="rollback_verify_requested",
        rollback_verify=True,
    )
    rollback_receipt_decision = (
        compile_activation_recovery_decision(
            snapshot(rollback_records),
            restored_prerequisites,
            restored_systemd,
            requested_outcome="rollback",
        )
    )
    require(
        rollback_receipt_decision.action_id == "inverse"
        and rollback_receipt_decision.operation_id
        == "publish_rollback_receipt",
        "verified rollback did not select receipt",
        failures,
    )
    rollback_last = parse_activation_revision(rollback_records[-1])
    rollback_receipt = build_activation_receipt(
        identity,
        terminal_revision=rollback_last.revision + 1,
        previous_revision_sha256=rollback_last.record_sha256,
        terminal_state="service_state_rolled_back",
        owns_enable=False,
        owns_start=False,
        result_id="rollback_succeeded",
    )
    rollback_terminalize = compile_activation_recovery_decision(
        snapshot(rollback_records, rollback_receipt),
        restored_prerequisites,
        restored_systemd,
        requested_outcome="rollback",
    )
    parsed_rollback_receipt = parse_activation_receipt(
        rollback_receipt
    )
    append_revision(
        rollback_records,
        identity,
        phase="terminal",
        step_id="terminal",
        owns_enable=False,
        owns_start=False,
        terminal_state="service_state_rolled_back",
        receipt_sha256=(
            parsed_rollback_receipt.receipt_sha256
        ),
    )
    rollback_complete = compile_activation_recovery_decision(
        snapshot(rollback_records, rollback_receipt),
        restored_prerequisites,
        restored_systemd,
        requested_outcome="rollback",
    )
    rollback_terminal_contract = (
        rollback_terminalize.action_id == "terminalize"
        and rollback_complete.action_id == "complete"
        and rollback_complete.reason_id == "journal_complete"
    )
    require(
        rollback_terminal_contract,
        "rollback receipt did not terminalize idempotently",
        failures,
    )

    changed_link_before_verify = (
        compile_activation_recovery_decision(
            snapshot(records),
            prerequisites(enabled=True, link_inode=17),
            active_systemd,
            requested_outcome="resume",
        )
    )
    require(
        changed_link_before_verify.action_id == "blocked"
        and changed_link_before_verify.reason_id
        == "ownership_unproven",
        "changed owned link did not block forward verification",
        failures,
    )

    changed_invocation = compile_activation_recovery_decision(
        snapshot(records),
        enabled_prerequisites,
        systemd(
            enabled=True,
            active=True,
            invocation_id="2" * 32,
        ),
        requested_outcome="rollback",
    )
    require(
        changed_invocation.action_id == "blocked"
        and changed_invocation.reason_id == "state_drift",
        "changed invocation did not block rollback",
        failures,
    )

    append_intent(
        records,
        identity,
        "verify",
        owns_enable=True,
        owns_start=True,
    )
    append_observed(
        records,
        identity,
        "verify",
        enabled_prerequisites,
        active_systemd,
        owns_enable=True,
        owns_start=True,
    )
    verified = compile_activation_recovery_decision(
        snapshot(records),
        enabled_prerequisites,
        active_systemd,
        requested_outcome="resume",
    )
    require(
        verified.operation_id == "publish_success_receipt"
        and verified.action_id == "resume",
        "verified chain did not select receipt publication",
        failures,
    )

    last = parse_activation_revision(records[-1])
    receipt = build_activation_receipt(
        identity,
        terminal_revision=last.revision + 1,
        previous_revision_sha256=last.record_sha256,
        terminal_state="active",
        owns_enable=True,
        owns_start=True,
        result_id="activation_succeeded",
    )
    orphan = compile_activation_recovery_decision(
        snapshot(records, receipt),
        enabled_prerequisites,
        active_systemd,
        requested_outcome="resume",
    )
    require(
        orphan.action_id == "terminalize"
        and orphan.operation_id == "publish_terminal_revision",
        "orphan receipt was not selected for terminalization",
        failures,
    )
    parsed_receipt = parse_activation_receipt(receipt)
    append_revision(
        records,
        identity,
        phase="terminal",
        step_id="terminal",
        owns_enable=True,
        owns_start=True,
        terminal_state="active",
        receipt_sha256=parsed_receipt.receipt_sha256,
    )
    complete = compile_activation_recovery_decision(
        snapshot(records, receipt),
        enabled_prerequisites,
        active_systemd,
        requested_outcome="resume",
    )
    require(
        complete.action_id == "complete"
        and complete.reason_id == "journal_complete",
        "terminal chain was not idempotently complete",
        failures,
    )

    changed_inventory = compile_activation_recovery_decision(
        snapshot(records[:5]),
        prerequisites(enabled=True, link_inode=17),
        enabled_systemd,
        requested_outcome="rollback",
    )
    require(
        changed_inventory.action_id == "blocked",
        "changed enablement inventory did not block inverse",
        failures,
    )
    changed_config = compile_activation_recovery_decision(
        snapshot(records[:3]),
        replace(
            pre_prerequisites,
            config=replace(
                pre_prerequisites.config,
                content_sha256="8" * 64,
            ),
        ),
        post_daemon_systemd,
        requested_outcome="resume",
    )
    require(
        changed_config.action_id == "blocked"
        and changed_config.reason_id == "plan_binding_unproven",
        "changed private prerequisite did not block recovery",
        failures,
    )

    stable_again = compile_activation_recovery_decision(
        snapshot(records, receipt),
        enabled_prerequisites,
        active_systemd,
        requested_outcome="resume",
    )
    require(
        stable_again.decision_sha256 == complete.decision_sha256,
        "recovery decision hash was not deterministic",
        failures,
    )
    invalid_snapshot = ActivationJournalRecoverySnapshot(
        revisions=(parse_activation_revision(records[-1]),),
        receipt=parsed_receipt,
    )
    expect_invalid(
        lambda: compile_activation_recovery_decision(
            invalid_snapshot,
            enabled_prerequisites,
            active_systemd,
            requested_outcome="resume",
        ),
        failures,
        "invalid chain",
    )
    expect_invalid(
        lambda: compile_activation_recovery_decision(
            snapshot(records, receipt),
            enabled_prerequisites,
            active_systemd,
            requested_outcome="guess",
        ),
        failures,
        "invalid outcome",
    )
    expect_invalid(
        lambda: compile_activation_recovery_decision(
            snapshot(records, receipt),
            enabled_prerequisites,
            active_systemd,
            requested_outcome=[],  # type: ignore[arg-type]
        ),
        failures,
        "non-string outcome",
    )

    projection = project_activation_recovery_decision(complete)
    tampered_projection = project_activation_recovery_decision(
        replace(complete, decision_sha256="0" * 64)
    )
    public_text = json.dumps(
        projection,
        ensure_ascii=True,
        sort_keys=True,
    )
    require(
        projection.get("ok") is True
        and PRIVATE_CANARY not in public_text
        and "/etc/" not in public_text,
        "bounded projection exposed private detail",
        failures,
    )
    require(
        tampered_projection.get("state") == "invalid"
        and tampered_projection.get("ok") is False,
        "tampered decision projection was not rejected",
        failures,
    )

    result = {
        "ambiguous_ownership_blocked": (
            interrupted_start.reason_id == "ownership_ambiguous"
        ),
        "decision_hash_deterministic": (
            stable_again.decision_sha256 == complete.decision_sha256
        ),
        "failures": failures,
        "forward_resume_cases": 7,
        "invalid_inputs_rejected": True,
        "network_used": False,
        "ok": not failures,
        "operation": "relay_activation_recovery_decision_smoke",
        "orphan_receipt_terminalized": orphan.action_id == "terminalize",
        "owned_enablement_drift_blocked": (
            changed_link_before_verify.reason_id
            == "ownership_unproven"
        ),
        "ownership_inverse_cases": 2,
        "private_payload_omitted": PRIVATE_CANARY not in public_text,
        "private_prerequisite_drift_blocked": (
            changed_config.reason_id == "plan_binding_unproven"
        ),
        "rollback_terminal_contract": rollback_terminal_contract,
        "systemd_mutation_performed": False,
        "write_scope": "none",
    }
    print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
