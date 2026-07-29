#!/usr/bin/env python3
"""Exercise lifecycle-lock-owned activation namespace initialization."""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from types import ModuleType


sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
OFFLINE_SMOKE = ROOT / "scripts" / "relay_offline_install_smoke.py"


def load_offline_smoke() -> ModuleType:
    name = "_agentops_relay_namespace_offline_fixture"
    spec = importlib.util.spec_from_file_location(name, OFFLINE_SMOKE)
    if spec is None or spec.loader is None:
        raise RuntimeError("offline install fixture unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def descriptor_count() -> int | None:
    for directory in ("/proc/self/fd", "/dev/fd"):
        try:
            return len(os.listdir(directory))
        except OSError:
            continue
    return None


def prepare_root(root: Path, *, namespace: str) -> Path:
    root.mkdir(mode=0o700)
    admin = root / "var" / "lib" / "agentops-relayctl"
    admin.mkdir(parents=True, mode=0o700)
    admin.chmod(0o700)
    lifecycle = admin / "lifecycle.lock"
    lifecycle.write_bytes(b"")
    lifecycle.chmod(0o600)
    if namespace != "missing":
        activation = admin / "activation"
        activation.mkdir(mode=0o700)
        if namespace in {"exact", "history"}:
            (activation / "receipts").mkdir(mode=0o700)
            transactions = activation / "transactions"
            transactions.mkdir(mode=0o700)
            if namespace == "history":
                revision = transactions / ("a" * 64)
                revision.mkdir(mode=0o700)
                (revision / "00000001.json").write_bytes(b"{}\n")
        elif namespace == "partial":
            (activation / "transactions").mkdir(mode=0o700)
    return admin


def exact_namespace(admin: Path) -> bool:
    activation = admin / "activation"
    if (
        sorted(path.name for path in admin.iterdir())
        != ["activation", "lifecycle.lock"]
        or sorted(path.name for path in activation.iterdir())
        != ["receipts", "transactions"]
    ):
        return False
    for path in (
        activation,
        activation / "receipts",
        activation / "transactions",
    ):
        metadata = path.lstat()
        if (
            not path.is_dir()
            or path.is_symlink()
            or metadata.st_mode & 0o777 != 0o700
        ):
            return False
    return not any((activation / "receipts").iterdir()) and not any(
        (activation / "transactions").iterdir()
    )


def publish_error(admin: ModuleType, plan: object) -> str:
    try:
        admin._publish_install(plan)
    except admin.RelayAdminError as exc:
        return exc.error_id
    return ""


def main() -> int:
    failures: list[str] = []
    status_before = subprocess.run(
        [
            "git",
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    with tempfile.TemporaryDirectory(
        prefix="relay-activation-namespace-"
    ) as temporary_name:
        temporary = Path(temporary_name).resolve()
        offline = load_offline_smoke()
        bundle_path, bundle_sha256 = offline.build_real_bundle(temporary)
        admin = offline.load_admin()
        bundle = admin.inspect_bundle(bundle_path, bundle_sha256)
        from agentops_mis_cli.relay_activation_journal import (
            _open_locked_production_store,
        )

        fresh_root = temporary / "fresh"
        fresh_root.mkdir(mode=0o700)
        fresh_plan = admin._plan_for_install(fresh_root, bundle)
        fresh_error = publish_error(admin, fresh_plan)
        fresh_admin = (
            fresh_root / "var" / "lib" / "agentops-relayctl"
        )
        fresh_initialized = (
            not fresh_error
            and fresh_plan.activation_namespace_state == "missing"
            and exact_namespace(fresh_admin)
        )
        production_opener_ready = False
        if fresh_initialized:
            with _open_locked_production_store(fresh_root) as store:
                snapshot = store.inspect_store()
                production_opener_ready = (
                    snapshot.get("state") == "ready"
                    and snapshot.get("recovery_required") is False
                    and snapshot.get("completed_transaction_count") == 0
                )

        exact_root = temporary / "exact"
        exact_admin = prepare_root(exact_root, namespace="exact")
        exact_identity = tuple(
            (exact_admin / "activation" / name).stat().st_ino
            for name in ("receipts", "transactions")
        )
        exact_plan = admin._plan_for_install(exact_root, bundle)
        exact_error = publish_error(admin, exact_plan)
        exact_idempotent = (
            not exact_error
            and exact_plan.activation_namespace_state == "exact_empty"
            and exact_namespace(exact_admin)
            and exact_identity
            == tuple(
                (exact_admin / "activation" / name).stat().st_ino
                for name in ("receipts", "transactions")
            )
        )

        binding_root = temporary / "plan-binding"
        binding_admin = prepare_root(binding_root, namespace="missing")
        missing_plan = admin._plan_for_install(binding_root, bundle)
        activation = binding_admin / "activation"
        activation.mkdir(mode=0o700)
        (activation / "receipts").mkdir(mode=0o700)
        (activation / "transactions").mkdir(mode=0o700)
        exact_bound_plan = admin._plan_for_install(binding_root, bundle)
        plan_state_bound = (
            missing_plan.activation_namespace_state == "missing"
            and exact_bound_plan.activation_namespace_state == "exact_empty"
            and missing_plan.plan_sha256 != exact_bound_plan.plan_sha256
        )

        rejected_states = 0
        for state in ("partial", "history"):
            root = temporary / f"rejected-{state}"
            prepare_root(root, namespace=state)
            before = offline.tree_digest(root)
            try:
                admin._plan_for_install(root, bundle)
            except admin.RelayAdminError as exc:
                rejected = exc.error_id == "recovery_required"
            else:
                rejected = False
            if rejected and offline.tree_digest(root) == before:
                rejected_states += 1
        unknown_root = temporary / "rejected-unknown"
        unknown_admin = prepare_root(
            unknown_root,
            namespace="missing",
        )
        (unknown_admin / "unexpected").write_bytes(b"x")
        unknown_before = offline.tree_digest(unknown_root)
        try:
            admin._plan_for_install(unknown_root, bundle)
        except admin.RelayAdminError as exc:
            unknown_rejected = exc.error_id == "recovery_required"
        else:
            unknown_rejected = False
        if (
            unknown_rejected
            and offline.tree_digest(unknown_root) == unknown_before
        ):
            rejected_states += 1
        external = temporary / "external-activation"
        external.mkdir(mode=0o700)
        canary = external / "canary"
        canary.write_bytes(b"ACTIVATION_NAMESPACE_CANARY")
        for state in ("symlink", "fifo"):
            root = temporary / f"rejected-{state}"
            special_admin = prepare_root(root, namespace="missing")
            activation = special_admin / "activation"
            if state == "symlink":
                activation.symlink_to(
                    external,
                    target_is_directory=True,
                )
            else:
                os.mkfifo(activation, mode=0o700)
            before = offline.tree_digest(root)
            try:
                admin._plan_for_install(root, bundle)
            except admin.RelayAdminError as exc:
                rejected = exc.error_id == "recovery_required"
            else:
                rejected = False
            if (
                rejected
                and offline.tree_digest(root) == before
                and canary.read_bytes()
                == b"ACTIVATION_NAMESPACE_CANARY"
            ):
                rejected_states += 1

        failure_root = temporary / "failure-marker"
        failure_root.mkdir(mode=0o700)
        failure_plan = admin._plan_for_install(failure_root, bundle)
        before_descriptors = descriptor_count()
        original_create = admin._create_install_activation_directory_at

        def fail_receipts(
            parent_descriptor: int,
            name: str,
            **kwargs: object,
        ) -> int:
            if name == "receipts":
                raise admin.RelayAdminError("recovery_required")
            return original_create(
                parent_descriptor,
                name,
                **kwargs,
            )

        admin._create_install_activation_directory_at = fail_receipts
        try:
            failure_error = publish_error(admin, failure_plan)
        finally:
            admin._create_install_activation_directory_at = original_create
        after_descriptors = descriptor_count()
        failure_admin = (
            failure_root / "var" / "lib" / "agentops-relayctl"
        )
        marker = failure_admin / "transaction.json"
        try:
            admin._plan_for_install(failure_root, bundle)
        except admin.RelayAdminError as exc:
            restart_recovery = exc.error_id == "recovery_required"
        else:
            restart_recovery = False
        failure_marker_retained = (
            failure_error == "recovery_required"
            and marker.read_bytes()
            == admin._install_transaction_data(failure_plan)
            and (failure_admin / "activation").is_dir()
            and not any((failure_admin / "activation").iterdir())
            and restart_recovery
            and (
                before_descriptors is None
                or after_descriptors is None
                or before_descriptors == after_descriptors
            )
        )

        race_root = temporary / "activation-race"
        race_root.mkdir(mode=0o700)
        race_plan = admin._plan_for_install(race_root, bundle)
        race_admin = race_root / "var" / "lib" / "agentops-relayctl"
        race_before_descriptors = descriptor_count()
        original_create = admin._create_install_activation_directory_at
        race_injected = False

        def replace_activation_after_open(
            parent_descriptor: int,
            name: str,
            **kwargs: object,
        ) -> int:
            nonlocal race_injected
            descriptor = original_create(
                parent_descriptor,
                name,
                **kwargs,
            )
            if name == "activation" and not race_injected:
                race_injected = True
                activation_path = race_admin / "activation"
                activation_path.rename(race_admin / "activation-retired")
                activation_path.mkdir(mode=0o700)
                (activation_path / "receipts").mkdir(mode=0o700)
                (activation_path / "transactions").mkdir(mode=0o700)
            return descriptor

        admin._create_install_activation_directory_at = (
            replace_activation_after_open
        )
        try:
            race_error = publish_error(admin, race_plan)
        finally:
            admin._create_install_activation_directory_at = original_create
        race_after_descriptors = descriptor_count()
        try:
            admin._plan_for_install(race_root, bundle)
        except admin.RelayAdminError as exc:
            race_restart_recovery = exc.error_id == "recovery_required"
        else:
            race_restart_recovery = False
        activation_race_rejected = (
            race_error == "recovery_required"
            and race_injected
            and (race_admin / "transaction.json").is_file()
            and (race_admin / "activation-retired").is_dir()
            and race_restart_recovery
            and (
                race_before_descriptors is None
                or race_after_descriptors is None
                or race_before_descriptors == race_after_descriptors
            )
        )

        require(
            fresh_initialized,
            "fresh install did not initialize the exact namespace",
            failures,
        )
        require(
            production_opener_ready,
            "production opener could not use the installed namespace",
            failures,
        )
        require(
            exact_idempotent,
            "exact empty namespace was not preserved",
            failures,
        )
        require(
            plan_state_bound,
            "namespace state was not bound into the install plan",
            failures,
        )
        require(
            rejected_states == 5,
            "unsafe preinstall namespace state was accepted",
            failures,
        )
        require(
            failure_marker_retained,
            "failed initialization did not retain recovery evidence",
            failures,
        )
        require(
            activation_race_rejected,
            "activation path replacement was not retained as recovery",
            failures,
        )

    status_after = subprocess.run(
        [
            "git",
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    require(
        status_after == status_before,
        "namespace smoke changed repository status",
        failures,
    )
    result = {
        "activation_path_race_rejected": activation_race_rejected,
        "exact_namespace_idempotent": exact_idempotent,
        "failure_marker_retained": failure_marker_retained,
        "failures": failures,
        "fresh_namespace_initialized": fresh_initialized,
        "ok": not failures,
        "operation": "relay_activation_namespace_install_smoke",
        "plan_state_bound": plan_state_bound,
        "production_opener_ready": production_opener_ready,
        "rejected_preinstall_states": rejected_states,
        "repository_status_unchanged": status_after == status_before,
    }
    print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
