#!/usr/bin/env python3
"""Exercise the production Relay scanner/store on a disposable Linux install."""
from __future__ import annotations

import grp
import hashlib
import json
import os
import pwd
import re
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agentops_mis_cli.relay_activation import (  # noqa: E402
    ENABLEMENT_LINK_PATH,
    UNIT_PATH,
)
from agentops_mis_cli.relay_activation_journal import (  # noqa: E402
    _open_locked_production_store,
)
from agentops_mis_cli.relay_activation_scan import (  # noqa: E402
    _scan_activation_prerequisites_while_locked,
    scan_activation_prerequisites,
)
from agentops_mis_cli.relay_admin import (  # noqa: E402
    RelayAdminError,
    _plan_for_install,
    _publish_install,
    inspect_bundle,
    relay_status,
)


OPT_IN = "AGENTOPS_RELAY_LINUX_PRODUCTION_ACCEPTANCE"
PARENT_HARDEN_OPT_IN = (
    "AGENTOPS_RELAY_LINUX_PRODUCTION_HARDEN_INSTALL_PARENTS"
)
BUNDLE_ENV = "AGENTOPS_RELAY_LINUX_PRODUCTION_BUNDLE"
BUNDLE_SHA_ENV = "AGENTOPS_RELAY_LINUX_PRODUCTION_BUNDLE_SHA256"
SERVICE_ACCOUNT = "agentops-relay"
INSTALL_ROOT = Path("/opt/agentops-mis-relay")
STABLE_LAUNCHER = Path("/usr/local/bin/agentops-relay")
CONFIG_ROOT = Path("/etc/agentops-mis-relay")
ADMIN_ROOT = Path("/var/lib/agentops-relayctl")
STATE_ROOT = Path("/var/lib/agentops-mis-relay")
RUNTIME_ROOT = Path("/run/agentops-mis-relay")
UNIT = Path(UNIT_PATH)
ENABLEMENT_LINK = Path(ENABLEMENT_LINK_PATH)
UNIT_SOURCE = (
    ROOT / "packaging" / "relay" / "systemd" / "agentops-mis-relay.service"
)
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
ABSENT_PATHS = (
    INSTALL_ROOT,
    STABLE_LAUNCHER,
    CONFIG_ROOT,
    ADMIN_ROOT,
    STATE_ROOT,
    RUNTIME_ROOT,
    UNIT,
    ENABLEMENT_LINK,
)
INSTALL_PLAN_ERROR_IDS = frozenset(
    {
        "install_parent_invalid",
        "install_parent_mode_invalid",
        "install_parent_owner_invalid",
        "install_path_escape",
        "installed_state_conflict",
        "installed_state_invalid",
        "recovery_required",
        "root_unavailable",
        "same_version_release_conflict",
        "upgrade_required",
    }
)
INSTALL_PARENT_PATHS = (
    Path("/opt"),
    Path("/usr/local/bin"),
)


class AcceptanceFailure(Exception):
    def __init__(self, stage: str = "unknown") -> None:
        self.stage = stage
        super().__init__("linux_production_install_acceptance_failed")


@dataclass(frozen=True)
class ParentModeSnapshot:
    path: Path
    device_id: int
    inode: int
    owner_id: int
    group_id: int
    mode: int


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("ascii")


def _account_present() -> bool:
    try:
        pwd.getpwnam(SERVICE_ACCOUNT)
    except KeyError:
        user_present = False
    else:
        user_present = True
    try:
        grp.getgrnam(SERVICE_ACCOUNT)
    except KeyError:
        group_present = False
    else:
        group_present = True
    return user_present or group_present


def _parent_mode_snapshot(path: Path) -> ParentModeSnapshot:
    try:
        metadata = os.lstat(path)
    except OSError:
        raise AcceptanceFailure("install_parent_invalid") from None
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or (stat.S_IMODE(metadata.st_mode) & 0o700) != 0o700
    ):
        raise AcceptanceFailure("install_parent_invalid")
    return ParentModeSnapshot(
        path=path,
        device_id=metadata.st_dev,
        inode=metadata.st_ino,
        owner_id=metadata.st_uid,
        group_id=metadata.st_gid,
        mode=stat.S_IMODE(metadata.st_mode),
    )


def _same_parent(
    snapshot: ParentModeSnapshot,
    metadata: os.stat_result,
) -> bool:
    return (
        stat.S_ISDIR(metadata.st_mode)
        and not stat.S_ISLNK(metadata.st_mode)
        and metadata.st_dev == snapshot.device_id
        and metadata.st_ino == snapshot.inode
        and metadata.st_uid == snapshot.owner_id
        and metadata.st_gid == snapshot.group_id
    )


def _restore_install_parent_modes(
    snapshots: tuple[ParentModeSnapshot, ...],
) -> bool:
    ok = True
    for snapshot in reversed(snapshots):
        try:
            before = os.lstat(snapshot.path)
            if not _same_parent(snapshot, before):
                ok = False
                continue
            os.chmod(
                snapshot.path,
                snapshot.mode,
                follow_symlinks=False,
            )
            after = os.lstat(snapshot.path)
            if (
                not _same_parent(snapshot, after)
                or stat.S_IMODE(after.st_mode) != snapshot.mode
            ):
                ok = False
        except OSError:
            ok = False
    return ok


def _harden_install_parents() -> tuple[ParentModeSnapshot, ...]:
    snapshots = tuple(
        _parent_mode_snapshot(path)
        for path in INSTALL_PARENT_PATHS
    )
    changed = tuple(
        snapshot
        for snapshot in snapshots
        if snapshot.mode & 0o022
    )
    if not changed:
        return ()
    if os.environ.get(PARENT_HARDEN_OPT_IN) != "1":
        raise AcceptanceFailure("install_parent_hardening_required")
    applied: list[ParentModeSnapshot] = []
    try:
        for snapshot in changed:
            before = os.lstat(snapshot.path)
            if not _same_parent(snapshot, before):
                raise AcceptanceFailure(
                    "install_parent_changed"
                )
            applied.append(snapshot)
            os.chmod(
                snapshot.path,
                snapshot.mode & ~0o022,
                follow_symlinks=False,
            )
            after = os.lstat(snapshot.path)
            if (
                not _same_parent(snapshot, after)
                or stat.S_IMODE(after.st_mode) & 0o022
            ):
                raise AcceptanceFailure(
                    "install_parent_hardening_failed"
                )
    except Exception:
        if not _restore_install_parent_modes(tuple(applied)):
            raise AcceptanceFailure(
                "install_parent_restore_failed"
            ) from None
        raise
    return changed


def _run_quiet(argv: tuple[str, ...]) -> bool:
    try:
        result = subprocess.run(
            argv,
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
    except Exception:
        return False
    return result.returncode == 0


def _preflight() -> None:
    if (
        os.environ.get(OPT_IN) != "1"
        or not sys.platform.startswith("linux")
        or os.geteuid() != 0
        or not Path("/run/systemd/system").is_dir()
        or _account_present()
        or any(os.path.lexists(path) for path in ABSENT_PATHS)
        or not UNIT_SOURCE.is_file()
        or UNIT_SOURCE.is_symlink()
    ):
        raise AcceptanceFailure("preflight")


def _bundle_input() -> tuple[Path, str]:
    path_value = os.environ.get(BUNDLE_ENV)
    expected_sha256 = os.environ.get(BUNDLE_SHA_ENV)
    if (
        not isinstance(path_value, str)
        or not path_value
        or len(path_value) > 4096
        or "\x00" in path_value
        or any(
            ord(character) < 32 or ord(character) == 127
            for character in path_value
        )
        or not isinstance(expected_sha256, str)
        or not SHA256_PATTERN.fullmatch(expected_sha256)
    ):
        raise AcceptanceFailure("preflight")
    path = Path(path_value)
    try:
        if (
            not path.is_absolute()
            or path_value != path.as_posix()
            or path.is_symlink()
            or not path.is_file()
        ):
            raise AcceptanceFailure("preflight")
    except OSError:
        raise AcceptanceFailure("preflight") from None
    return path, expected_sha256


def _create_service_account() -> tuple[int, int]:
    useradd = shutil.which("useradd")
    if not useradd or not _run_quiet(
        (
            useradd,
            "--system",
            "--user-group",
            "--no-create-home",
            "--home-dir",
            "/nonexistent",
            "--shell",
            "/usr/sbin/nologin",
            SERVICE_ACCOUNT,
        )
    ):
        raise AcceptanceFailure
    try:
        user = pwd.getpwnam(SERVICE_ACCOUNT)
        group = grp.getgrnam(SERVICE_ACCOUNT)
    except KeyError:
        raise AcceptanceFailure from None
    if (
        user.pw_name != SERVICE_ACCOUNT
        or group.gr_name != SERVICE_ACCOUNT
        or user.pw_uid == 0
        or user.pw_gid == 0
        or user.pw_gid != group.gr_gid
    ):
        raise AcceptanceFailure
    return user.pw_uid, user.pw_gid


def _mkdir_owned(path: Path, mode: int, uid: int, gid: int) -> None:
    try:
        os.mkdir(path, mode)
        os.chown(path, uid, gid, follow_symlinks=False)
        os.chmod(path, mode, follow_symlinks=False)
        metadata = os.lstat(path)
    except OSError:
        raise AcceptanceFailure from None
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != uid
        or metadata.st_gid != gid
        or stat.S_IMODE(metadata.st_mode) != mode
    ):
        raise AcceptanceFailure


def _write_owned(
    path: Path,
    data: bytes,
    mode: int,
    uid: int,
    gid: int,
) -> None:
    descriptor = -1
    complete = False
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            mode,
        )
        os.fchown(descriptor, uid, gid)
        os.fchmod(descriptor, mode)
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise AcceptanceFailure
            view = view[written:]
        os.fsync(descriptor)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != uid
            or metadata.st_gid != gid
            or stat.S_IMODE(metadata.st_mode) != mode
            or metadata.st_nlink != 1
            or metadata.st_size != len(data)
        ):
            raise AcceptanceFailure
        complete = True
    except AcceptanceFailure:
        raise
    except OSError:
        raise AcceptanceFailure from None
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if not complete:
            try:
                path.unlink()
            except OSError:
                pass


def _provision_runtime_material(uid: int, gid: int) -> None:
    _mkdir_owned(CONFIG_ROOT, 0o755, 0, 0)
    _mkdir_owned(CONFIG_ROOT / "tls", 0o755, 0, 0)
    _mkdir_owned(CONFIG_ROOT / "routes", 0o755, 0, 0)
    _mkdir_owned(STATE_ROOT, 0o700, uid, gid)
    _mkdir_owned(RUNTIME_ROOT, 0o700, uid, gid)

    config = {
        "browser_listen": {"host": "127.0.0.1", "port": 443},
        "connector_listen": {"host": "127.0.0.1", "port": 9443},
        "connector_tls": {
            "cert_file": "/etc/agentops-mis-relay/tls/relay-cert.pem",
            "key_file": "/etc/agentops-mis-relay/tls/relay-key.pem",
        },
        "routes": [
            {
                "hostname": "acceptance.example.invalid",
                "key_file": (
                    "/etc/agentops-mis-relay/routes/acceptance.key"
                ),
                "route": "rte_linux_acceptance",
            }
        ],
        "schema_version": 1,
        "state_path": "/var/lib/agentops-mis-relay/epochs.json",
        "status_path": "/run/agentops-mis-relay/status.json",
    }
    route_key = hashlib.sha256(
        b"agentops relay linux production acceptance"
    ).hexdigest().encode("ascii") + b"\n"
    _write_owned(CONFIG_ROOT / "config.json", _canonical_json(config), 0o640, 0, gid)
    _write_owned(
        CONFIG_ROOT / "tls" / "relay-cert.pem",
        b"synthetic acceptance certificate\n",
        0o640,
        0,
        gid,
    )
    _write_owned(
        CONFIG_ROOT / "tls" / "relay-key.pem",
        b"synthetic acceptance private material\n",
        0o600,
        uid,
        gid,
    )
    _write_owned(
        CONFIG_ROOT / "routes" / "acceptance.key",
        route_key,
        0o600,
        uid,
        gid,
    )


def _remove_directory(path: Path) -> bool:
    if not os.path.lexists(path):
        return True
    try:
        metadata = os.lstat(path)
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            return False
        shutil.rmtree(path)
    except OSError:
        return False
    return not os.path.lexists(path)


def _cleanup(
    *,
    account_created: bool,
    parent_modes: tuple[ParentModeSnapshot, ...],
) -> bool:
    ok = True
    try:
        if os.path.lexists(STABLE_LAUNCHER):
            if (
                not STABLE_LAUNCHER.is_symlink()
                or os.readlink(STABLE_LAUNCHER)
                != "../../../opt/agentops-mis-relay/current/bin/agentops-relay"
            ):
                ok = False
            else:
                STABLE_LAUNCHER.unlink()
    except OSError:
        ok = False
    try:
        if os.path.lexists(UNIT):
            metadata = os.lstat(UNIT)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or UNIT.read_bytes() != UNIT_SOURCE.read_bytes()
            ):
                ok = False
            else:
                UNIT.unlink()
    except OSError:
        ok = False
    if os.path.lexists(ENABLEMENT_LINK):
        ok = False
    for path in (
        CONFIG_ROOT,
        STATE_ROOT,
        RUNTIME_ROOT,
        ADMIN_ROOT,
        INSTALL_ROOT,
    ):
        ok = _remove_directory(path) and ok
    if account_created:
        userdel = shutil.which("userdel")
        if not userdel or not _run_quiet((userdel, SERVICE_ACCOUNT)):
            ok = False
        try:
            grp.getgrnam(SERVICE_ACCOUNT)
        except KeyError:
            pass
        else:
            groupdel = shutil.which("groupdel")
            if not groupdel or not _run_quiet((groupdel, SERVICE_ACCOUNT)):
                ok = False
    product_cleanup_ok = (
        ok
        and not _account_present()
        and not any(os.path.lexists(path) for path in ABSENT_PATHS)
    )
    parents_restored = _restore_install_parent_modes(parent_modes)
    return product_cleanup_ok and parents_restored


def _run() -> dict[str, object]:
    _preflight()
    bundle_path, bundle_sha256 = _bundle_input()
    stage = "account_provisioning"
    account_created = False
    cleanup_ok = False
    parent_modes: tuple[ParentModeSnapshot, ...] = ()
    try:
        stage = "install_parent_hardening"
        parent_modes = _harden_install_parents()

        stage = "account_provisioning"
        uid, gid = _create_service_account()
        account_created = True

        stage = "offline_bundle_inspect"
        bundle = inspect_bundle(bundle_path, bundle_sha256)
        stage = "offline_install_plan"
        try:
            plan = _plan_for_install(Path("/"), bundle)
        except RelayAdminError as exc:
            diagnostic = (
                exc.error_id
                if exc.error_id in INSTALL_PLAN_ERROR_IDS
                else "other"
            )
            raise AcceptanceFailure(
                f"offline_install_plan_{diagnostic}"
            ) from None
        if plan.no_op:
            raise AcceptanceFailure
        stage = "offline_install_publish"
        _publish_install(plan)

        stage = "runtime_provisioning"
        _provision_runtime_material(uid, gid)
        status, status_code = relay_status(Path("/"))
        if (
            status_code != 0
            or status.get("state_id") != "installed_valid"
            or status.get("installed") is not True
        ):
            raise AcceptanceFailure

        stage = "production_scan"
        ordinary = scan_activation_prerequisites()
        if ordinary.release_id != bundle.release_id:
            raise AcceptanceFailure

        stage = "production_store"
        with _open_locked_production_store(Path("/")) as store:
            journal_before = store.snapshot_sha256()
            capability = store._activation_scan_capability()
            locked = _scan_activation_prerequisites_while_locked(capability)
            store_state = store.inspect_store()
            journal_after = store.snapshot_sha256()
            if (
                locked != ordinary
                or journal_before != journal_after
                or store_state.get("ok") is not True
                or store_state.get("state") != "ready"
                or store_state.get("completed_transaction_count") != 0
            ):
                raise AcceptanceFailure

        stage = "complete"
        return {
            "account_provisioned": True,
            "installed_tree": True,
            "install_parent_modes_restored": True,
            "journal_unchanged": True,
            "network_used": False,
            "ok": True,
            "operation": "relay_linux_production_install_acceptance",
            "production_scanner": True,
            "production_store": True,
            "stage": stage,
            "systemd_mutated": False,
        }
    except AcceptanceFailure as exc:
        raise AcceptanceFailure(
            stage if exc.stage == "unknown" else exc.stage
        ) from None
    except Exception:
        raise AcceptanceFailure(stage) from None
    finally:
        cleanup_ok = _cleanup(
            account_created=account_created or _account_present(),
            parent_modes=parent_modes,
        )
        if not cleanup_ok:
            raise AcceptanceFailure("cleanup")


def main() -> int:
    try:
        result = _run()
        result["cleanup_ok"] = True
    except AcceptanceFailure as exc:
        result = {
            "cleanup_ok": (
                exc.stage != "cleanup"
                and not _account_present()
                and not any(os.path.lexists(path) for path in ABSENT_PATHS)
            ),
            "failure_id": "linux_production_install_acceptance_failed",
            "linux": sys.platform.startswith("linux"),
            "network_used": False,
            "ok": False,
            "operation": "relay_linux_production_install_acceptance",
            "stage": exc.stage,
        }
    except Exception:
        result = {
            "cleanup_ok": False,
            "failure_id": "linux_production_install_acceptance_failed",
            "linux": sys.platform.startswith("linux"),
            "network_used": False,
            "ok": False,
            "operation": "relay_linux_production_install_acceptance",
            "stage": "unknown",
        }
    print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
    return 0 if result.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
