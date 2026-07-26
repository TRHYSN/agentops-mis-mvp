#!/usr/bin/env python3
"""Exercise the read-only Relay activation prerequisite scanner."""
from __future__ import annotations

import builtins
import hashlib
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import zipfile
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Callable, Iterator


sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agentops_mis_cli import _build_backend as backend  # noqa: E402
from agentops_mis_cli import relay_activation_scan as scanner  # noqa: E402
from agentops_mis_cli.relay_activation import (  # noqa: E402
    CONFIG_PATH,
    ENABLEMENT_LINK_PATH,
    RUNTIME_DIRECTORY,
    STATE_DIRECTORY,
    UNIT_NAME,
    UNIT_PATH,
    compile_activation_plan,
    parse_systemd_show_bytes,
    project_activation_plan,
)
from agentops_mis_cli.relay_activation_evidence import (  # noqa: E402
    build_activation_journal_identity,
)
from agentops_mis_cli.relay_activation_journal import (  # noqa: E402
    GENESIS_REVISION_SHA256,
    _LockedActivationScanCapability,
    _open_locked_production_store,
    build_activation_revision,
    parse_activation_revision,
)
from agentops_mis_cli.relay_admin import (  # noqa: E402
    BUNDLE_SCHEMA,
    EXPECTED_WHEEL_MODULES,
    INSTALLED_SCHEMA,
    _launcher_data,
    _status_directory_flags,
)


FIXTURE_ERROR = scanner.SCAN_ERROR_ID
CERTIFICATE_DATA = b"synthetic relay certificate for scanner acceptance\n"
PRIVATE_KEY_DATA = b"synthetic relay private material for scanner acceptance\n"
ROUTE_KEY_A = (b"11" * 32) + b"\n"
ROUTE_KEY_B = (b"22" * 32) + b"\n"
UNIT_SOURCE = ROOT / "packaging" / "relay" / "systemd" / UNIT_NAME


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def canonical_json(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("ascii")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def ensure_directory(path: Path, mode: int) -> None:
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(mode)


def write_file(path: Path, data: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    path.chmod(mode)


def config_payload(
    *,
    route_paths: tuple[str, ...] = (
        "/etc/agentops-mis-relay/routes/route-a.key",
    ),
    state_path: str = "/var/lib/agentops-mis-relay/epochs.json",
    status_path: str = "/run/agentops-mis-relay/status.json",
) -> dict[str, object]:
    return {
        "browser_listen": {"host": "0.0.0.0", "port": 443},
        "connector_listen": {"host": "0.0.0.0", "port": 9443},
        "connector_tls": {
            "cert_file": "/etc/agentops-mis-relay/tls/relay-cert.pem",
            "key_file": "/etc/agentops-mis-relay/tls/relay-key.pem",
        },
        "routes": [
            {
                "hostname": f"scan-{index}.example.invalid",
                "key_file": path,
                "route": f"rte_scan_{index}",
            }
            for index, path in enumerate(route_paths, start=1)
        ],
        "schema_version": 1,
        "state_path": state_path,
        "status_path": status_path,
    }


def build_installed_tree(root: Path, wheel_directory: Path) -> str:
    root.chmod(0o755)
    wheel_name = backend.build_wheel(str(wheel_directory))
    wheel_path = wheel_directory / wheel_name
    version = backend.VERSION
    commit = "1" * 40
    release_id = f"{version}-{commit[:12]}"
    release = root / "opt" / "agentops-mis-relay" / "releases" / release_id
    site = release / "private" / "site-packages"
    ensure_directory(site, 0o755)
    with zipfile.ZipFile(wheel_path) as archive:
        wheel_names = tuple(sorted(archive.namelist()))
        for name in wheel_names:
            target = site / name
            write_file(target, archive.read(name), 0o644)

    launcher = _launcher_data()
    unit = UNIT_SOURCE.read_bytes()
    write_file(release / "bin" / "agentops-relay", launcher, 0o755)
    write_file(release / "systemd" / UNIT_NAME, unit, 0o644)
    release_metadata = {
        "archive_sha256": "a" * 64,
        "bundle_schema": BUNDLE_SCHEMA,
        "git_commit": commit,
        "installed_file_count": len(wheel_names) + 3,
        "launcher_sha256": sha256(launcher),
        "manifest_sha256": "b" * 64,
        "release_id": release_id,
        "schema": INSTALLED_SCHEMA,
        "unit_sha256": sha256(unit),
        "version": version,
        "wheel_member_count": len(wheel_names),
        "wheel_sha256": sha256(wheel_path.read_bytes()),
    }
    write_file(
        release / "release.json",
        canonical_json(release_metadata),
        0o644,
    )
    for directory in sorted(
        (path for path in release.rglob("*") if path.is_dir()),
        key=lambda value: len(value.parts),
        reverse=True,
    ):
        directory.chmod(0o755)
    for directory in (
        root / "opt",
        root / "opt" / "agentops-mis-relay",
        root / "opt" / "agentops-mis-relay" / "releases",
    ):
        directory.chmod(0o755)
    base = root / "opt" / "agentops-mis-relay"
    (base / "current").symlink_to(f"releases/{release_id}")
    (base / "controller").symlink_to(f"releases/{release_id}")

    stable = root / "usr" / "local" / "bin" / "agentops-relay"
    ensure_directory(stable.parent, 0o755)
    stable.symlink_to(
        "../../../opt/agentops-mis-relay/current/bin/agentops-relay"
    )
    write_file(
        root / "etc" / "systemd" / "system" / UNIT_NAME,
        unit,
        0o644,
    )
    ensure_directory(
        root / "etc" / "systemd" / "system" / "multi-user.target.wants",
        0o755,
    )
    admin_state = root / "var" / "lib" / "agentops-relayctl"
    ensure_directory(admin_state, 0o700)
    write_file(admin_state / "lifecycle.lock", b"", 0o600)

    write_file(
        root / CONFIG_PATH.lstrip("/"),
        canonical_json(config_payload()),
        0o640,
    )
    write_file(
        root / "etc" / "agentops-mis-relay" / "tls" / "relay-cert.pem",
        CERTIFICATE_DATA,
        0o640,
    )
    write_file(
        root / "etc" / "agentops-mis-relay" / "tls" / "relay-key.pem",
        PRIVATE_KEY_DATA,
        0o600,
    )
    write_file(
        root / "etc" / "agentops-mis-relay" / "routes" / "route-a.key",
        ROUTE_KEY_A,
        0o600,
    )
    state_directory = root / STATE_DIRECTORY.lstrip("/")
    runtime_directory = root / RUNTIME_DIRECTORY.lstrip("/")
    ensure_directory(state_directory, 0o700)
    ensure_directory(runtime_directory, 0o700)
    write_file(root / "usr" / "bin" / "systemctl", b"synthetic systemctl\n", 0o755)
    return release_id


def fixture_resolver(name: str) -> tuple[int, int, tuple[int, ...]]:
    if name != scanner.SERVICE_ACCOUNT_NAME:
        raise AssertionError("scanner requested an unexpected account")
    return os.geteuid(), os.getegid(), (os.getegid(),)


def clone_root(source: Path, destination: Path) -> Path:
    shutil.copytree(source, destination, symlinks=True)
    destination.chmod(0o755)
    return destination


def _write_requested(mode: object) -> bool:
    return isinstance(mode, str) and any(character in mode for character in "wax+")


def _write_flags(flags: object) -> bool:
    if type(flags) is not int:
        return False
    mask = (
        os.O_WRONLY
        | os.O_RDWR
        | os.O_CREAT
        | os.O_TRUNC
        | os.O_APPEND
    )
    return bool(flags & mask)


@contextmanager
def scanner_guard(root: Path) -> Iterator[dict[str, int]]:
    original_open = builtins.open
    original_os_open = os.open
    original_socket = socket.socket
    original_run = subprocess.run
    original_popen = subprocess.Popen
    original_system = os.system
    original_mutations = {
        name: getattr(os, name)
        for name in (
            "chmod",
            "chown",
            "link",
            "mkdir",
            "remove",
            "rename",
            "replace",
            "rmdir",
            "symlink",
            "truncate",
            "unlink",
        )
        if hasattr(os, name)
    }
    counters = {"read_opens": 0, "unanchored_opens": 0, "writes": 0}
    root_text = str(root)

    def guarded_open(file, mode="r", *args, **kwargs):
        if _write_requested(mode):
            counters["writes"] += 1
            raise AssertionError("scanner attempted a write")
        return original_open(file, mode, *args, **kwargs)

    def guarded_os_open(path, flags, mode=0o777, *, dir_fd=None):
        if _write_flags(flags):
            counters["writes"] += 1
            raise AssertionError("scanner attempted an os.open write")
        counters["read_opens"] += 1
        if dir_fd is None and str(path) != root_text:
            counters["unanchored_opens"] += 1
            raise AssertionError("scanner opened a non-root path without dir_fd")
        return original_os_open(path, flags, mode, dir_fd=dir_fd)

    def blocked_external(*_args, **_kwargs):
        raise AssertionError("scanner attempted network or subprocess activity")

    def blocked_mutation(*_args, **_kwargs):
        counters["writes"] += 1
        raise AssertionError("scanner attempted a filesystem mutation")

    builtins.open = guarded_open
    os.open = guarded_os_open
    socket.socket = blocked_external
    subprocess.run = blocked_external
    subprocess.Popen = blocked_external
    os.system = blocked_external
    for name in original_mutations:
        setattr(os, name, blocked_mutation)
    try:
        yield counters
    finally:
        builtins.open = original_open
        os.open = original_os_open
        socket.socket = original_socket
        subprocess.run = original_run
        subprocess.Popen = original_popen
        os.system = original_system
        for name, value in original_mutations.items():
            setattr(os, name, value)


def descriptor_count() -> int | None:
    directory = Path("/dev/fd")
    if not directory.is_dir():
        return None
    return len(tuple(directory.iterdir()))


def scan_guarded(root: Path):
    before = descriptor_count()
    with scanner_guard(root) as counters:
        snapshot = scanner._scan_fixture_activation_prerequisites(
            root,
            account_resolver=fixture_resolver,
        )
    after = descriptor_count()
    if before is not None and after != before:
        raise AssertionError("scanner leaked file descriptors")
    if counters["writes"] or counters["unanchored_opens"]:
        raise AssertionError("scanner side-effect guard failed")
    return snapshot, counters


def scan_guarded_locked(root: Path, capability):
    before = descriptor_count()
    with scanner_guard(root) as counters:
        snapshot = (
            scanner._scan_fixture_activation_prerequisites_while_locked(
                root,
                account_resolver=fixture_resolver,
                capability=capability,
            )
        )
    after = descriptor_count()
    if before is not None and after != before:
        raise AssertionError("locked scanner leaked file descriptors")
    if counters["writes"] or counters["unanchored_opens"]:
        raise AssertionError("locked scanner side-effect guard failed")
    return snapshot, counters


def expect_rejected(
    root: Path,
    failures: list[str],
    label: str,
    *,
    resolver: Callable[[str], tuple[int, int, tuple[int, ...]]] = fixture_resolver,
) -> None:
    before = descriptor_count()
    observed = ""
    cause = object()
    context = object()
    try:
        with scanner_guard(root):
            scanner._scan_fixture_activation_prerequisites(
                root,
                account_resolver=resolver,
            )
    except scanner.RelayActivationScanError as exc:
        observed = str(exc)
        cause = exc.__cause__
        context = exc.__context__
    except Exception as exc:
        observed = f"unexpected:{type(exc).__name__}"
    after = descriptor_count()
    require(observed == FIXTURE_ERROR, f"{label}: error was not bounded", failures)
    require(cause is None and context is None, f"{label}: exception chain retained", failures)
    require(
        str(root) not in observed
        and "synthetic relay" not in observed
        and "route-a" not in observed,
        f"{label}: error disclosed fixture content",
        failures,
    )
    require(
        before is None or after == before,
        f"{label}: file descriptors leaked",
        failures,
    )


def disabled_systemd_bytes() -> bytes:
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


def enabled_systemd_bytes() -> bytes:
    return disabled_systemd_bytes().replace(
        b"UnitFileState=disabled\n",
        b"UnitFileState=enabled\n",
    )


def rewrite_config(root: Path, payload: dict[str, object]) -> None:
    path = root / CONFIG_PATH.lstrip("/")
    path.write_bytes(canonical_json(payload))
    path.chmod(0o640)


def main() -> int:
    failures: list[str] = []
    rejected_cases = 0
    active_transaction_locked_scan = False
    with tempfile.TemporaryDirectory(
        prefix="relay-activation-scan-"
    ) as temporary_name:
        temporary = Path(temporary_name)
        wheel_directory = temporary / "wheel"
        wheel_directory.mkdir(mode=0o700)
        valid = temporary / "valid"
        valid.mkdir(mode=0o700)
        release_id = build_installed_tree(valid, wheel_directory)

        snapshot, counters = scan_guarded(valid)
        require(
            snapshot.release_id == release_id
            and snapshot.version_id == backend.VERSION,
            "success snapshot release identity mismatch",
            failures,
        )
        require(
            snapshot.root.owner_id == 0
            and snapshot.unit.owner_id == 0
            and snapshot.service_uid == os.geteuid()
            and snapshot.service_gid == os.getegid(),
            "fixture logical ownership mapping mismatch",
            failures,
        )
        require(
            snapshot.config.canonical_path == CONFIG_PATH
            and snapshot.state_directory.canonical_path == STATE_DIRECTORY
            and snapshot.runtime_directory.canonical_path == RUNTIME_DIRECTORY,
            "fixed activation paths drifted",
            failures,
        )
        require(
            tuple(value.canonical_path for value in snapshot.route_keys)
            == ("/etc/agentops-mis-relay/routes/route-a.key",),
            "route key inventory mismatch",
            failures,
        )
        require(
            snapshot.enablement_links == (),
            "disabled fixture exposed an enablement link",
            failures,
        )
        require(
            counters["read_opens"] > 20,
            "scanner did not exercise anchored file traversal",
            failures,
        )

        locked_root = temporary / "locked-active-transaction"
        shutil.copytree(valid, locked_root, symlinks=True)
        activation = (
            locked_root
            / "var"
            / "lib"
            / "agentops-relayctl"
            / "activation"
        )
        ensure_directory(activation, 0o700)
        ensure_directory(activation / "receipts", 0o700)
        ensure_directory(activation / "transactions", 0o700)
        locked_initial, _locked_initial_counters = scan_guarded(
            locked_root
        )
        locked_systemd = parse_systemd_show_bytes(
            disabled_systemd_bytes()
        )
        locked_plan = compile_activation_plan(
            locked_initial,
            locked_systemd,
        )
        require(
            locked_plan.plan_sha256 is not None,
            "locked scanner fixture plan did not compile",
            failures,
        )
        locked_identity = build_activation_journal_identity(
            locked_initial,
            locked_systemd,
            confirmed_plan_sha256=str(locked_plan.plan_sha256),
        )
        prepared = build_activation_revision(
            locked_identity,
            revision=1,
            previous_revision_sha256=GENESIS_REVISION_SHA256,
            phase="prepared",
            step_id="transaction_open",
        )
        capability = None
        ordinary_rejected = False
        mismatched_root_rejected = False
        journal_drift_rejected = False
        with _open_locked_production_store(locked_root) as locked_store:
            capability = locked_store._activation_scan_capability()
            locked_store.publish_revision(prepared)
            failures_before = len(failures)
            expect_rejected(
                locked_root,
                failures,
                "ordinary scanner accepted active transaction",
            )
            ordinary_rejected = len(failures) == failures_before
            locked_snapshot, locked_counters = scan_guarded_locked(
                locked_root,
                capability,
            )
            require(
                locked_snapshot.release_id == locked_initial.release_id
                and locked_counters["read_opens"] > 20,
                "live locked capability did not preserve exact scan",
                failures,
            )
            try:
                scanner._scan_fixture_activation_prerequisites_while_locked(
                    valid,
                    account_resolver=fixture_resolver,
                    capability=capability,
                )
            except scanner.RelayActivationScanError:
                mismatched_root_rejected = True
            require(
                mismatched_root_rejected,
                "locked capability was reusable for another root",
                failures,
            )
            forged_capability_rejected = False

            class ForgedCapability(_LockedActivationScanCapability):
                pass

            forged_capability = object.__new__(ForgedCapability)
            try:
                scanner._scan_fixture_activation_prerequisites_while_locked(
                    locked_root,
                    account_resolver=fixture_resolver,
                    capability=forged_capability,
                )
            except scanner.RelayActivationScanError:
                forged_capability_rejected = True
            require(
                forged_capability_rejected,
                "forged locked capability subtype was accepted",
                failures,
            )
            prepared_revision = parse_activation_revision(prepared)
            intent = build_activation_revision(
                locked_identity,
                revision=2,
                previous_revision_sha256=(
                    prepared_revision.record_sha256
                ),
                phase="intent",
                step_id="daemon_reload",
                intent_id="daemon_reload_requested",
            )
            resolver_calls = 0

            def drifting_resolver(
                name: str,
            ) -> tuple[int, int, tuple[int, ...]]:
                nonlocal resolver_calls
                resolver_calls += 1
                if resolver_calls == 2:
                    locked_store.publish_revision(intent)
                return fixture_resolver(name)

            try:
                scanner._scan_fixture_activation_prerequisites_while_locked(
                    locked_root,
                    account_resolver=drifting_resolver,
                    capability=capability,
                )
            except scanner.RelayActivationScanError:
                journal_drift_rejected = True
            require(
                journal_drift_rejected and resolver_calls == 2,
                "journal drift during locked scan was not rejected",
                failures,
            )
        closed_capability_rejected = False
        try:
            scanner._scan_fixture_activation_prerequisites_while_locked(
                locked_root,
                account_resolver=fixture_resolver,
                capability=capability,
            )
        except scanner.RelayActivationScanError:
            closed_capability_rejected = True
        require(
            closed_capability_rejected,
            "closed locked capability remained usable",
            failures,
        )
        active_transaction_locked_scan = (
            ordinary_rejected
            and mismatched_root_rejected
            and forged_capability_rejected
            and closed_capability_rejected
            and journal_drift_rejected
        )

        original_geteuid = os.geteuid
        original_stat_for_authority = os.stat
        original_open_for_authority = os.open
        authority_host_accessed = False

        def non_root_euid() -> int:
            return 1

        def blocked_authority_host_access(*_args, **_kwargs):
            nonlocal authority_host_accessed
            authority_host_accessed = True
            raise AssertionError("non-root scan attempted host access")

        scanner.os.geteuid = non_root_euid
        scanner.os.stat = blocked_authority_host_access
        scanner.os.open = blocked_authority_host_access
        authority_error = ""
        authority_cause = object()
        authority_context = object()
        try:
            scanner.scan_activation_prerequisites()
        except scanner.RelayActivationScanError as exc:
            authority_error = str(exc)
            authority_cause = exc.__cause__
            authority_context = exc.__context__
        finally:
            scanner.os.geteuid = original_geteuid
            scanner.os.stat = original_stat_for_authority
            scanner.os.open = original_open_for_authority
        require(
            authority_error == FIXTURE_ERROR
            and authority_cause is None
            and authority_context is None
            and not authority_host_accessed,
            "non-root production scan did not fail before host access",
            failures,
        )
        rejected_cases += 1

        require(
            scanner._trusted_service_parent_group(
                4242,
                expected_gid=0,
                service_group_ids=(3000, 4242),
            )
            and not scanner._trusted_service_parent_group(
                4243,
                expected_gid=0,
                service_group_ids=(3000, 4242),
            ),
            "supplementary service-group trust drifted",
            failures,
        )

        plan = compile_activation_plan(
            snapshot,
            parse_systemd_show_bytes(disabled_systemd_bytes()),
        )
        projected = project_activation_plan(plan)
        require(
            plan.state == "plan_ready"
            and projected.get("requested")
            == {"daemon_reload": True, "enable": True, "start": True},
            "success snapshot did not compile into a plan",
            failures,
        )

        enabled_root = clone_root(valid, temporary / "enabled")
        enabled_link = enabled_root / ENABLEMENT_LINK_PATH.lstrip("/")
        enabled_link.symlink_to(UNIT_PATH)
        enabled_snapshot, _enabled_counters = scan_guarded(enabled_root)
        enabled_plan = compile_activation_plan(
            enabled_snapshot,
            parse_systemd_show_bytes(enabled_systemd_bytes()),
        )
        require(
            len(enabled_snapshot.enablement_links) == 1
            and enabled_snapshot.enablement_links[0].target == UNIT_PATH
            and project_activation_plan(enabled_plan).get("requested")
            == {"daemon_reload": True, "enable": False, "start": True},
            "exact enablement link inventory did not compile",
            failures,
        )

        existing_leaves = clone_root(valid, temporary / "existing-leaves")
        empty_leaf_snapshot, _empty_leaf_counters = scan_guarded(
            existing_leaves
        )
        write_file(
            existing_leaves
            / STATE_DIRECTORY.lstrip("/")
            / "epochs.json",
            b'{"schema_version":1}\n',
            0o600,
        )
        write_file(
            existing_leaves
            / RUNTIME_DIRECTORY.lstrip("/")
            / "status.json",
            b'{"ready":false}\n',
            0o600,
        )
        existing_snapshot, _existing_counters = scan_guarded(existing_leaves)
        if sys.platform.startswith("linux"):
            require(
                existing_snapshot.trusted_parent_chain_sha256
                == empty_leaf_snapshot.trusted_parent_chain_sha256,
                "safe mutable leaf creation changed the Linux parent hash",
                failures,
            )
        (
            existing_leaves
            / STATE_DIRECTORY.lstrip("/")
            / "epochs.json"
        ).unlink()
        (
            existing_leaves
            / RUNTIME_DIRECTORY.lstrip("/")
            / "status.json"
        ).unlink()
        write_file(
            existing_leaves
            / STATE_DIRECTORY.lstrip("/")
            / "epochs.json",
            b'{"routes":{},"schema_version":1}\n',
            0o600,
        )
        write_file(
            existing_leaves
            / RUNTIME_DIRECTORY.lstrip("/")
            / "status.json",
            b'{"ready":true}\n',
            0o600,
        )
        replacement_snapshot, _replacement_counters = scan_guarded(
            existing_leaves
        )
        require(
            replacement_snapshot.trusted_parent_chain_sha256
            == existing_snapshot.trusted_parent_chain_sha256,
            "safe mutable leaf replacement changed the parent-chain hash",
            failures,
        )
        empty_leaf_plan = compile_activation_plan(
            empty_leaf_snapshot,
            parse_systemd_show_bytes(disabled_systemd_bytes()),
        )
        existing_leaf_plan = compile_activation_plan(
            existing_snapshot,
            parse_systemd_show_bytes(disabled_systemd_bytes()),
        )
        replacement_leaf_plan = compile_activation_plan(
            replacement_snapshot,
            parse_systemd_show_bytes(disabled_systemd_bytes()),
        )
        if sys.platform.startswith("linux"):
            require(
                empty_leaf_plan.plan_sha256
                == existing_leaf_plan.plan_sha256,
                "safe mutable leaf creation changed the Linux plan hash",
                failures,
            )
        require(
            existing_leaf_plan.ok is True
            and existing_leaf_plan.plan_sha256
            == replacement_leaf_plan.plan_sha256,
            "safe mutable leaf replacement changed the plan hash",
            failures,
        )

        release_file = (
            valid
            / "opt"
            / "agentops-mis-relay"
            / "releases"
            / release_id
            / "private"
            / "site-packages"
            / "agentops_mis_cli"
            / "relay_activation.py"
        )
        original_release_data = release_file.read_bytes()
        release_file.write_bytes(original_release_data + b"\n")
        root_descriptor = os.open(valid, _status_directory_flags())
        inventory = scanner._AnchoredInventory(root_descriptor)
        try:
            changed_tree_hash = scanner._release_tree_digest(
                inventory,
                release_id,
                expected_uid=os.geteuid(),
                expected_gid=os.getegid(),
            )
            inventory.verify()
        finally:
            inventory.close()
            os.close(root_descriptor)
        require(
            changed_tree_hash != snapshot.release_tree_sha256,
            "single release file change did not change live tree hash",
            failures,
        )
        release_file.write_bytes(original_release_data)
        release_file.chmod(0o644)

        def add_case(label: str, mutate: Callable[[Path], None]) -> None:
            nonlocal rejected_cases
            case = clone_root(valid, temporary / f"case-{label}")
            mutate(case)
            expect_rejected(case, failures, label)
            rejected_cases += 1

        def cert_symlink(root: Path) -> None:
            path = (
                root
                / "etc"
                / "agentops-mis-relay"
                / "tls"
                / "relay-cert.pem"
            )
            path.unlink()
            path.symlink_to("relay-key.pem")

        add_case("symlink-material", cert_symlink)

        def hardlinked_material(root: Path) -> None:
            route = (
                root
                / "etc"
                / "agentops-mis-relay"
                / "routes"
                / "route-a.key"
            )
            private = (
                root
                / "etc"
                / "agentops-mis-relay"
                / "tls"
                / "relay-key.pem"
            )
            route.unlink()
            os.link(private, route)

        add_case("hardlinked-material", hardlinked_material)
        add_case(
            "material-mode",
            lambda root: (
                root
                / "etc"
                / "agentops-mis-relay"
                / "routes"
                / "route-a.key"
            ).chmod(0o640),
        )
        add_case(
            "state-directory-mode",
            lambda root: (
                root / STATE_DIRECTORY.lstrip("/")
            ).chmod(0o755),
        )
        add_case(
            "service-parent-traversal",
            lambda root: (
                root / "etc" / "agentops-mis-relay"
            ).chmod(0o700),
        )

        def state_leaf_symlink(root: Path) -> None:
            path = (
                root
                / STATE_DIRECTORY.lstrip("/")
                / "epochs.json"
            )
            path.symlink_to(
                root
                / RUNTIME_DIRECTORY.lstrip("/")
                / "status.json"
            )

        add_case("state-leaf-symlink", state_leaf_symlink)

        def status_leaf_mode(root: Path) -> None:
            write_file(
                root
                / RUNTIME_DIRECTORY.lstrip("/")
                / "status.json",
                b'{"ready":false}\n',
                0o644,
            )

        add_case("status-leaf-mode", status_leaf_mode)

        owner_case = clone_root(valid, temporary / "case-owner")

        def wrong_owner_resolver(
            name: str,
        ) -> tuple[int, int, tuple[int, ...]]:
            if name != scanner.SERVICE_ACCOUNT_NAME:
                raise AssertionError("unexpected account")
            uid = os.geteuid() + 1
            gid = os.getegid() + 1
            return uid, gid, (gid,)

        expect_rejected(
            owner_case,
            failures,
            "owner-mismatch",
            resolver=wrong_owner_resolver,
        )
        rejected_cases += 1

        account_race = clone_root(valid, temporary / "case-account-race")
        account_calls = 0

        def changing_account_resolver(
            name: str,
        ) -> tuple[int, int, tuple[int, ...]]:
            nonlocal account_calls
            if name != scanner.SERVICE_ACCOUNT_NAME:
                raise AssertionError("unexpected account")
            account_calls += 1
            if account_calls == 1:
                return fixture_resolver(name)
            uid = os.geteuid() + 1
            gid = os.getegid() + 1
            return uid, gid, (gid,)

        expect_rejected(
            account_race,
            failures,
            "account-identity-race",
            resolver=changing_account_resolver,
        )
        require(account_calls == 2, "account resolver was not revalidated", failures)
        rejected_cases += 1

        def outside_route(root: Path) -> None:
            rewrite_config(
                root,
                config_payload(route_paths=("/tmp/route-outside.key",)),
            )

        add_case("path-confinement", outside_route)
        add_case(
            "state-path-confinement",
            lambda root: rewrite_config(
                root,
                config_payload(
                    state_path="/var/lib/agentops-mis-relay/nested/epochs.json"
                ),
            ),
        )
        add_case(
            "status-path-confinement",
            lambda root: rewrite_config(
                root,
                config_payload(status_path="/run/status.json"),
            ),
        )
        add_case(
            "state-filename",
            lambda root: rewrite_config(
                root,
                config_payload(
                    state_path="/var/lib/agentops-mis-relay/.epochs.json"
                ),
            ),
        )

        def duplicate_route_key(root: Path) -> None:
            second = (
                root
                / "etc"
                / "agentops-mis-relay"
                / "routes"
                / "route-b.key"
            )
            write_file(second, ROUTE_KEY_A, 0o600)
            rewrite_config(
                root,
                config_payload(
                    route_paths=(
                        "/etc/agentops-mis-relay/routes/route-a.key",
                        "/etc/agentops-mis-relay/routes/route-b.key",
                    )
                ),
            )

        add_case("duplicate-route-key", duplicate_route_key)

        def oversized_route_key(root: Path) -> None:
            path = (
                root
                / "etc"
                / "agentops-mis-relay"
                / "routes"
                / "route-a.key"
            )
            path.write_bytes(b"3" * (scanner.MAX_KEY_FILE_BYTES + 1))
            path.chmod(0o600)

        add_case("route-key-size", oversized_route_key)

        def oversized_config(root: Path) -> None:
            path = root / CONFIG_PATH.lstrip("/")
            path.write_bytes(b"{" + b" " * scanner.MAX_CONFIG_BYTES + b"}")
            path.chmod(0o640)

        add_case("config-size", oversized_config)

        def duplicate_config_key(root: Path) -> None:
            path = root / CONFIG_PATH.lstrip("/")
            data = canonical_json(config_payload())
            marker = b'  "schema_version": 1,\n'
            path.write_bytes(data.replace(marker, marker + marker, 1))
            path.chmod(0o640)

        add_case("duplicate-config-key", duplicate_config_key)

        root_link = temporary / "case-root-symlink"
        root_link.symlink_to(valid, target_is_directory=True)
        expect_rejected(root_link, failures, "root-symlink")
        rejected_cases += 1

        root_swap = clone_root(valid, temporary / "case-root-swap")
        retired_root = temporary / "case-root-swap-retired"
        original_status_scan = scanner._status_scan_anchored
        raw_rename = os.rename
        raw_mkdir = os.mkdir
        root_swapped = False

        def swap_after_status(root_descriptor: int):
            nonlocal root_swapped
            result = original_status_scan(root_descriptor)
            raw_rename(root_swap, retired_root)
            raw_mkdir(root_swap, 0o700)
            root_swapped = True
            return result

        scanner._status_scan_anchored = swap_after_status
        try:
            expect_rejected(root_swap, failures, "root-swap")
        finally:
            scanner._status_scan_anchored = original_status_scan
        require(root_swapped, "root-swap injection did not run", failures)
        rejected_cases += 1

        release_race = clone_root(valid, temporary / "case-release-race")
        installed_unit = (
            release_race
            / "etc"
            / "systemd"
            / "system"
            / UNIT_NAME
        )
        original_release_status_scan = scanner._status_scan_anchored
        raw_open_for_race = os.open
        raw_write_for_race = os.write
        raw_close_for_race = os.close
        release_status_calls = 0
        release_changed = False

        def change_unit_after_first_status(root_descriptor: int):
            nonlocal release_status_calls, release_changed
            result = original_release_status_scan(root_descriptor)
            release_status_calls += 1
            if release_status_calls == 1:
                descriptor = raw_open_for_race(installed_unit, os.O_WRONLY | os.O_APPEND)
                try:
                    raw_write_for_race(descriptor, b"\n")
                finally:
                    raw_close_for_race(descriptor)
                release_changed = True
            return result

        scanner._status_scan_anchored = change_unit_after_first_status
        try:
            expect_rejected(
                release_race,
                failures,
                "release-change-after-status",
            )
        finally:
            scanner._status_scan_anchored = original_release_status_scan
        require(
            release_changed and release_status_calls >= 1,
            "release race injection did not run",
            failures,
        )
        rejected_cases += 1

        absent_link_race = clone_root(
            valid,
            temporary / "case-absent-link-race",
        )
        absent_enablement = (
            absent_link_race / ENABLEMENT_LINK_PATH.lstrip("/")
        )
        original_lstat_optional = scanner._AnchoredInventory.lstat_optional
        raw_symlink_for_absence = os.symlink
        absent_link_created = False

        def create_link_after_absence(
            inventory,
            path: str,
            *,
            record_absence: bool = False,
        ):
            nonlocal absent_link_created
            observed = original_lstat_optional(
                inventory,
                path,
                record_absence=record_absence,
            )
            if (
                path == ENABLEMENT_LINK_PATH
                and observed is None
                and not absent_link_created
            ):
                raw_symlink_for_absence(
                    UNIT_PATH,
                    absent_enablement,
                )
                absent_link_created = True
            return observed

        scanner._AnchoredInventory.lstat_optional = create_link_after_absence
        try:
            expect_rejected(
                absent_link_race,
                failures,
                "enablement-link-appeared-after-absence",
            )
        finally:
            scanner._AnchoredInventory.lstat_optional = original_lstat_optional
        require(
            absent_link_created,
            "enablement-link absence race injection did not run",
            failures,
        )
        rejected_cases += 1

        link_swap = clone_root(valid, temporary / "case-link-swap")
        enablement = link_swap / ENABLEMENT_LINK_PATH.lstrip("/")
        enablement.symlink_to(UNIT_PATH)
        original_readlink = os.readlink
        raw_unlink = os.unlink
        raw_symlink = os.symlink
        link_swapped = False

        def swap_same_target(path, *, dir_fd=None):
            nonlocal link_swapped
            target = original_readlink(path, dir_fd=dir_fd)
            if (
                not link_swapped
                and path == UNIT_NAME
                and dir_fd is not None
            ):
                raw_unlink(enablement)
                raw_symlink(target, enablement)
                link_swapped = True
            return original_readlink(path, dir_fd=dir_fd)

        os.readlink = swap_same_target
        try:
            expect_rejected(link_swap, failures, "same-target-link-swap")
        finally:
            os.readlink = original_readlink
        require(link_swapped, "same-target link swap did not run", failures)
        rejected_cases += 1

        identity_change = clone_root(valid, temporary / "case-identity-change")
        route_path = (
            identity_change
            / "etc"
            / "agentops-mis-relay"
            / "routes"
            / "route-a.key"
        )
        route_inode = route_path.stat().st_ino
        original_read = os.read
        raw_open = os.open
        raw_write = os.write
        raw_fsync = os.fsync
        raw_close = os.close
        identity_changed = False

        def change_during_read(descriptor: int, count: int) -> bytes:
            nonlocal identity_changed
            data = original_read(descriptor, count)
            if (
                data
                and not identity_changed
                and os.fstat(descriptor).st_ino == route_inode
            ):
                target = raw_open(route_path, os.O_WRONLY | os.O_TRUNC)
                try:
                    raw_write(target, ROUTE_KEY_B)
                    raw_fsync(target)
                finally:
                    raw_close(target)
                identity_changed = True
            return data

        os.read = change_during_read
        try:
            expect_rejected(
                identity_change,
                failures,
                "identity-change-during-read",
            )
        finally:
            os.read = original_read
        require(identity_changed, "identity-change injection did not run", failures)
        rejected_cases += 1

        require(
            "agentops_mis_cli/relay_activation_scan.py" in EXPECTED_WHEEL_MODULES,
            "exact wheel module set omits scanner",
            failures,
        )
        require(
            not hasattr(scanner, "main"),
            "scanner exposed a CLI entrypoint",
            failures,
        )

    payload = {
        "active_transaction_locked_scan": (
            active_transaction_locked_scan
        ),
        "anchored_read_only": not failures,
        "compile_activation_plan": not failures,
        "exact_wheel_module_set": not failures,
        "fd_leak_free": not failures,
        "journal_drift_rejected": journal_drift_rejected,
        "live_release_tree_hash": not failures,
        "ok": not failures,
        "rejected_cases": rejected_cases,
        "schema_id": "agentops.relay.activation-scan-smoke.v0",
    }
    print(json.dumps(payload, ensure_ascii=True, sort_keys=True))
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
