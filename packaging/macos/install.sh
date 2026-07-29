#!/bin/sh
set -eu

BUNDLE_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
INSTALL_ROOT=${AGENTOPS_INSTALL_ROOT:-"$HOME/.local/share/agentops-mis"}
BIN_DIR=${AGENTOPS_BIN_DIR:-"$HOME/.local/bin"}
DATA_ROOT=${AGENTOPS_HOST_HOME:-"$HOME/.agentops/host"}
APP_DIR=${AGENTOPS_APP_DIR:-"$HOME/Applications"}
INSTALL_APP=true
if [ "${AGENTOPS_NO_APP_INSTALL:-}" = "1" ]; then
  INSTALL_APP=false
fi

if [ "$(uname -s)" != "Darwin" ] && [ "${AGENTOPS_BUNDLE_INSTALLER_TEST_MODE:-}" != "1" ]; then
  echo "this unsigned Private Host bundle supports macOS only" >&2
  exit 2
fi
python3 - <<'PY'
import sys
if sys.version_info < (3, 10):
    raise SystemExit("Python 3.10+ is required")
PY

python3 - "$BUNDLE_DIR" "$INSTALL_ROOT" "$BIN_DIR" "$DATA_ROOT" "$APP_DIR" "$INSTALL_APP" <<'PY'
import atexit
import json
import fcntl
import hashlib
import os
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

bundle = Path(sys.argv[1]).resolve()
install_root = Path(sys.argv[2]).expanduser().resolve()
bin_dir = Path(sys.argv[3]).expanduser().resolve()
data_root = Path(sys.argv[4]).expanduser().resolve()
raw_app_dir = Path(sys.argv[5]).expanduser()
install_app = sys.argv[6].lower() == "true"
if raw_app_dir.is_symlink():
    raise SystemExit("unsafe symlinked application directory")
app_dir = raw_app_dir.resolve()
manifest_path = bundle / "manifest.json"
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
version = str(manifest["version"])
if not version or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for char in version):
    raise SystemExit("invalid bundle version")
target = install_root / "versions" / version
app_bundle = app_dir / "AgentOps MIS.app"
production_min_free_bytes = 2 * 1024 * 1024 * 1024
launcher_marker_payload = {
    "schema_version": 1,
    "product": "AgentOps MIS Private Host Launcher",
    "managed": True,
}


def existing_filesystem_path(path):
    candidate = path.expanduser().absolute()
    while not candidate.exists() and candidate.parent != candidate:
        candidate = candidate.parent
    if candidate.is_file():
        candidate = candidate.parent
    return candidate


def configured_minimum_free_bytes():
    raw = os.environ.get("AGENTOPS_HOST_MIN_FREE_BYTES", "").strip()
    if not raw:
        return production_min_free_bytes, None
    try:
        requested = int(raw)
    except ValueError:
        return production_min_free_bytes, "invalid_threshold"
    if requested < production_min_free_bytes:
        return production_min_free_bytes, "threshold_below_production_floor"
    return requested, None


def observed_free_bytes(filesystem_path, volume_key):
    raw_override = os.environ.get("AGENTOPS_BUNDLE_INSTALLER_TEST_FREE_BYTES", "").strip()
    if raw_override:
        actual = int(shutil.disk_usage(existing_filesystem_path(filesystem_path)).free)
        if os.environ.get("AGENTOPS_BUNDLE_INSTALLER_TEST_MODE") != "1":
            return actual, False, "test_override_requires_test_mode"
        try:
            simulated = int(raw_override)
        except ValueError:
            return actual, False, "invalid_test_free_space_override"
        if simulated < 0:
            return actual, False, "invalid_test_free_space_override"
        if simulated > actual:
            return actual, False, "test_free_space_override_may_not_increase_capacity"
        if test_storage_fixture:
            return actual, False, "conflicting_test_storage_overrides"
        return simulated, True, None
    if test_storage_fixture:
        return test_storage_devices[volume_key]["free_bytes"], True, None
    return int(shutil.disk_usage(filesystem_path).free), False, None


def fail_storage_preflight(record):
    raise SystemExit(json.dumps({
        **record,
        "ok": False,
        "operation": "host_bundle_storage_preflight",
        "read_only": True,
        "network_used": False,
        "database_content_read": False,
        "credentials_read": False,
        "token_omitted": True,
    }, sort_keys=True))


test_storage_fixture = False
test_storage_paths = {}
test_storage_devices = {}
raw_test_storage = os.environ.get("AGENTOPS_BUNDLE_INSTALLER_TEST_STORAGE_JSON", "").strip()
if raw_test_storage:
    if os.environ.get("AGENTOPS_BUNDLE_INSTALLER_TEST_MODE") != "1":
        fail_storage_preflight({
            "filesystem_path": "",
            "free_bytes": None,
            "required_bytes": production_min_free_bytes,
            "minimum_free_bytes": production_min_free_bytes,
            "planned_write_bytes": 0,
            "status": "test_storage_fixture_requires_test_mode",
            "test_override_applied": False,
            "target_kinds": [],
        })
    test_storage_payload = None
    try:
        test_storage_payload = json.loads(raw_test_storage)
        test_storage_records = test_storage_payload.get("paths")
    except (AttributeError, json.JSONDecodeError):
        test_storage_records = None
    if (
        not isinstance(test_storage_payload, dict)
        or test_storage_payload.get("schema_version") != 1
        or not isinstance(test_storage_records, list)
        or not test_storage_records
    ):
        fail_storage_preflight({
            "filesystem_path": "",
            "free_bytes": None,
            "required_bytes": production_min_free_bytes,
            "minimum_free_bytes": production_min_free_bytes,
            "planned_write_bytes": 0,
            "status": "invalid_test_storage_fixture",
            "test_override_applied": False,
            "target_kinds": [],
        })
    for test_record in test_storage_records:
        try:
            raw_path = str(test_record["path"])
            path = Path(raw_path)
            device_id = str(test_record["device_id"])
            free_bytes = int(test_record["free_bytes"])
            storage_role = str(test_record["storage_role"])
        except (KeyError, TypeError, ValueError):
            fail_storage_preflight({
                "filesystem_path": "",
                "free_bytes": None,
                "required_bytes": production_min_free_bytes,
                "minimum_free_bytes": production_min_free_bytes,
                "planned_write_bytes": 0,
                "status": "invalid_test_storage_fixture",
                "test_override_applied": False,
                "target_kinds": [],
            })
        if (
            not path.is_absolute()
            or not device_id
            or len(device_id) > 120
            or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for char in device_id)
            or free_bytes < 0
            or storage_role not in {"install", "data", "bin", "app"}
        ):
            fail_storage_preflight({
                "filesystem_path": raw_path,
                "free_bytes": None,
                "required_bytes": production_min_free_bytes,
                "minimum_free_bytes": production_min_free_bytes,
                "planned_write_bytes": 0,
                "status": "invalid_test_storage_fixture",
                "test_override_applied": False,
                "target_kinds": [],
            })
        normalized_path = str(path.absolute())
        volume_key = ("test", device_id)
        prior_device = test_storage_devices.get(volume_key)
        if prior_device and prior_device["free_bytes"] != free_bytes:
            fail_storage_preflight({
                "filesystem_path": normalized_path,
                "free_bytes": None,
                "required_bytes": production_min_free_bytes,
                "minimum_free_bytes": production_min_free_bytes,
                "planned_write_bytes": 0,
                "status": "inconsistent_test_storage_fixture",
                "test_override_applied": False,
                "target_kinds": [],
            })
        test_storage_devices[volume_key] = {
            "free_bytes": free_bytes,
            "storage_roles": set((prior_device or {}).get("storage_roles") or ()) | {storage_role},
        }
        test_storage_paths[normalized_path] = {
            "volume_key": volume_key,
            "storage_role": storage_role,
        }
    test_storage_fixture = True

if install_app:
    home = Path.home().resolve()
    try:
        app_relative = app_dir.relative_to(home)
    except ValueError:
        raise SystemExit("application directory is outside HOME; install refused")
    if not app_relative.parts:
        raise SystemExit("unsafe application directory")
    managed_roots = (install_root, bin_dir, data_root)
    if any(
        app_dir == root
        or app_dir in root.parents
        or root in app_dir.parents
        for root in managed_roots
    ):
        raise SystemExit("application directory overlaps another managed root")
    if app_bundle.exists() or app_bundle.is_symlink():
        launcher_marker = app_bundle / "Contents" / "Resources" / "agentops-mis-launcher.json"
        try:
            existing_launcher_marker = json.loads(launcher_marker.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raise SystemExit("existing AgentOps MIS application ownership cannot be verified")
        if app_bundle.is_symlink() or not app_bundle.is_dir() or launcher_marker.is_symlink() or existing_launcher_marker != launcher_marker_payload:
            raise SystemExit("existing AgentOps MIS application ownership cannot be verified")

def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()

declared = set()
verified_bundle_bytes = manifest_path.stat().st_size
for record in manifest.get("files", []):
    relative = Path(str(record["path"]))
    if relative.is_absolute() or ".." in relative.parts:
        raise SystemExit("unsafe path in bundle manifest")
    source = bundle / relative
    if not source.is_file():
        raise SystemExit(f"missing bundle file: {relative}")
    record_size = int(record["size"])
    if source.stat().st_size != record_size or digest(source) != record["sha256"]:
        raise SystemExit(f"bundle integrity check failed: {relative}")
    declared.add(relative.as_posix())
    verified_bundle_bytes += record_size

actual = {
    path.relative_to(bundle).as_posix()
    for path in bundle.rglob("*")
    if path.is_file() and path.name != "manifest.json"
}
if actual != declared:
    raise SystemExit("bundle contains undeclared or unverified files")

current = install_root / "current"
previous = install_root / "previous"
old_current = current.resolve() if current.is_symlink() else None
if current.exists() and not current.is_symlink():
    raise SystemExit("unsafe non-symlink current install path")

install_marker = install_root / ".agentops-mis-install.json"
expected_install_marker = {
    "schema_version": 1,
    "product": "AgentOps MIS Private Host",
    "managed": True,
}
if install_marker.exists():
    try:
        existing_marker = json.loads(install_marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise SystemExit("installed product ownership marker is invalid")
    if install_marker.is_symlink() or existing_marker != expected_install_marker:
        raise SystemExit("installed product ownership marker is invalid")
elif install_root.is_dir() and any(install_root.iterdir()):
    allowed_legacy_entries = {"current", "previous", "versions"}
    actual_entries = {entry.name for entry in install_root.iterdir()}
    legacy_manifest = old_current / "release-manifest.json" if old_current else None
    try:
        legacy_payload = json.loads(legacy_manifest.read_text(encoding="utf-8")) if legacy_manifest else {}
        old_current.relative_to((install_root / "versions").resolve()) if old_current else None
    except (OSError, ValueError, json.JSONDecodeError):
        legacy_payload = {}
    if actual_entries - allowed_legacy_entries or legacy_payload.get("product") != "AgentOps MIS Private Host":
        raise SystemExit("non-empty install root lacks a valid product ownership marker")

shim = bin_dir / "agentops"
worker_shim = bin_dir / "agentops-worker"

def shim_content(module):
    quoted_current = shlex.quote(str(current))
    return (
        "#!/bin/sh\n"
        "set -eu\n"
        f"cd {quoted_current}\n"
        f"PYTHONPATH={quoted_current} exec python3 -m {module} \"$@\"\n"
    )

for candidate, module in ((shim, "agentops_mis_cli"), (worker_shim, "agentops_mis_cli.worker")):
    if candidate.exists() or candidate.is_symlink():
        if candidate.is_symlink() or not old_current:
            raise SystemExit(f"existing {candidate.name} shim ownership cannot be verified")
        try:
            existing_shim = candidate.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            raise SystemExit(f"existing {candidate.name} shim ownership cannot be verified")
        if existing_shim != shim_content(module):
            raise SystemExit(f"existing {candidate.name} shim ownership cannot be verified")
if target.exists():
    raise SystemExit(f"version is already installed: {version}")
database = data_root / "data" / "agentops_mis.db"
database_wal = Path(str(database) + "-wal")
backup_dir = data_root / "backups"
backup_source_bytes = 0
if old_current and database.is_file():
    if database.is_symlink():
        raise SystemExit("unsafe symlinked Host database; update refused")
    if backup_dir.is_symlink() or (backup_dir.exists() and not backup_dir.is_dir()):
        raise SystemExit("unsafe Host backup directory; update refused")
    if database_wal.is_symlink():
        raise SystemExit("unsafe symlinked Host database WAL; update refused")
    backup_source_bytes = database.stat().st_size
    if database_wal.is_file():
        backup_source_bytes += database_wal.stat().st_size
minimum_free_bytes, threshold_error = configured_minimum_free_bytes()
volume_requirements = {}


def reserve_volume(path, planned_write_bytes, target_kind):
    requested_path = path.expanduser().absolute()
    if test_storage_fixture:
        test_observation = test_storage_paths.get(str(requested_path))
        if not test_observation:
            fail_storage_preflight({
                "filesystem_path": str(requested_path),
                "free_bytes": None,
                "required_bytes": minimum_free_bytes + planned_write_bytes,
                "status": "test_storage_fixture_path_missing",
                "test_override_applied": False,
                "target_kinds": [target_kind],
            })
        filesystem_path = requested_path
        volume_key = test_observation["volume_key"]
        storage_roles = set(test_storage_devices[volume_key]["storage_roles"])
    else:
        filesystem_path = existing_filesystem_path(requested_path)
        storage_roles = set()
        try:
            volume_key = filesystem_path.stat().st_dev
        except OSError:
            fail_storage_preflight({
                "filesystem_path": str(filesystem_path),
                "free_bytes": None,
                "required_bytes": minimum_free_bytes + planned_write_bytes,
                "status": "storage_unavailable",
                "test_override_applied": False,
                "target_kinds": [target_kind],
            })
    entry = volume_requirements.setdefault(volume_key, {
        "filesystem_path": filesystem_path,
        "planned_write_bytes": 0,
        "target_kinds": set(),
        "storage_roles": set(),
    })
    entry["planned_write_bytes"] += planned_write_bytes
    entry["target_kinds"].add(target_kind)
    entry["storage_roles"].update(storage_roles)


reserve_volume(install_root, max(1, verified_bundle_bytes * 2), "install")
shim_write_bytes = sum(
    len(shim_content(module).encode("utf-8"))
    for module in ("agentops_mis_cli", "agentops_mis_cli.worker")
)
reserve_volume(bin_dir, max(4096, shim_write_bytes * 2), "bin")
reserve_volume(data_root.parent, 4096, "data")
if install_app:
    bundled_launcher = bundle / "payload" / "packaging" / "macos" / "launcher.py"
    if not bundled_launcher.is_file():
        raise SystemExit("release lacks the managed macOS launcher")
    launcher_write_bytes = max(64 * 1024, bundled_launcher.stat().st_size * 2 + 64 * 1024)
    reserve_volume(app_dir, launcher_write_bytes, "app")
if backup_source_bytes:
    reserve_volume(backup_dir, backup_source_bytes * 2, "backup")

storage_preflight = []
for volume_key, requirement in volume_requirements.items():
    filesystem_path = requirement["filesystem_path"]
    planned_write_bytes = int(requirement["planned_write_bytes"])
    required_bytes = minimum_free_bytes + planned_write_bytes
    try:
        free_bytes, test_override_applied, override_error = observed_free_bytes(filesystem_path, volume_key)
    except OSError:
        free_bytes, test_override_applied, override_error = None, False, "storage_unavailable"
    status = threshold_error or override_error
    if status is None:
        status = "ready" if free_bytes is not None and free_bytes >= required_bytes else "insufficient_free_space"
    record = {
        "filesystem_path": str(filesystem_path),
        "free_bytes": free_bytes,
        "required_bytes": required_bytes,
        "minimum_free_bytes": minimum_free_bytes,
        "planned_write_bytes": planned_write_bytes,
        "target_kinds": sorted(requirement["target_kinds"]),
        "storage_roles": sorted(requirement["storage_roles"]),
        "status": status,
        "test_override_applied": test_override_applied,
        "token_omitted": True,
    }
    storage_preflight.append(record)
    if status != "ready":
        fail_storage_preflight(record)

lock_path = data_root.parent / ".agentops-mis-host-lifecycle.lock"
lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
lock_descriptor = os.open(
    lock_path,
    os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
    0o600,
)
lock_metadata = os.fstat(lock_descriptor)
if not stat.S_ISREG(lock_metadata.st_mode):
    os.close(lock_descriptor)
    raise SystemExit("Host lifecycle lock is not a regular file; install refused")
os.fchmod(lock_descriptor, 0o600)
try:
    fcntl.flock(lock_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    os.close(lock_descriptor)
    raise SystemExit("Host lifecycle operation is active; install refused")

pid_path = data_root / "run" / "host.pid.json"
if pid_path.is_file():
    try:
        pid_payload = json.loads(pid_path.read_text(encoding="utf-8"))
        pid = int(pid_payload.get("pid") or 0) if isinstance(pid_payload, dict) else 0
    except (OSError, ValueError, json.JSONDecodeError):
        raise SystemExit("cannot verify Host process state; update refused")
    if pid <= 0:
        raise SystemExit("invalid managed Host PID record; update refused")
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        pass
    except PermissionError:
        raise SystemExit("cannot verify Host process state; update refused")
    else:
        raise SystemExit("AgentOps MIS Host is running; stop it before installing an update")

pre_update_backup = None
if old_current:
    backup_utility = old_current / "scripts" / "agentops_local_backup.py"
    if database.is_file():
        if not backup_utility.is_file():
            raise SystemExit("installed version lacks the required pre-update backup utility")
        process = subprocess.run(
            [
                sys.executable,
                str(backup_utility),
                "create",
                "--db-path",
                str(database),
                "--backup-dir",
                str(backup_dir),
            ],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        try:
            backup_payload = json.loads(process.stdout)
        except json.JSONDecodeError:
            backup_payload = {}
        if process.returncode != 0 or backup_payload.get("ok") is not True:
            raise SystemExit("verified pre-update ledger backup failed")
        pre_update_backup = backup_payload.get("backup_path")

shim_existed_before = {
    shim: shim.exists(),
    worker_shim: worker_shim.exists(),
}
app_existed_before = app_bundle.exists()
install_transaction = {"committed": False}


def rollback_uncommitted_install():
    if install_transaction["committed"]:
        return
    for candidate, existed in shim_existed_before.items():
        if not existed:
            try:
                candidate.unlink(missing_ok=True)
            except OSError:
                pass
    if install_app and not app_existed_before and app_bundle.exists():
        try:
            shutil.rmtree(app_bundle)
        except OSError:
            pass
    try:
        (install_root / ".agentops-mis-install.json.next").unlink(missing_ok=True)
    except OSError:
        pass
    current_points_to_target = current.is_symlink() and current.resolve() == target.resolve()
    if target.exists() and not current_points_to_target:
        try:
            shutil.rmtree(target)
        except OSError:
            pass


atexit.register(rollback_uncommitted_install)
target.parent.mkdir(parents=True, exist_ok=True)
stage = Path(tempfile.mkdtemp(prefix=f".install-{version}-", dir=target.parent))
try:
    shutil.copytree(bundle / "payload", stage, dirs_exist_ok=True, symlinks=False)
    shutil.copy2(manifest_path, stage / "release-manifest.json")
    stage.rename(target)
finally:
    if stage.exists():
        shutil.rmtree(stage)

def atomic_symlink(link, destination):
    temporary = link.with_name(link.name + ".next")
    temporary.unlink(missing_ok=True)
    try:
        temporary.symlink_to(destination)
        os.replace(temporary, link)
    finally:
        temporary.unlink(missing_ok=True)

bin_dir.mkdir(parents=True, exist_ok=True)

def atomic_write_shim(candidate, content):
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{candidate.name}.",
        suffix=".next",
        dir=candidate.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o755)
        os.replace(temporary, candidate)
    finally:
        temporary.unlink(missing_ok=True)

for candidate, module in ((shim, "agentops_mis_cli"), (worker_shim, "agentops_mis_cli.worker")):
    atomic_write_shim(candidate, shim_content(module))

launcher_installed = False
if install_app:
    app_dir.mkdir(parents=True, exist_ok=True)
    stage_parent = Path(tempfile.mkdtemp(prefix=".agentops-mis-app-", dir=app_dir))
    stage_app = stage_parent / "AgentOps MIS.app"
    contents = stage_app / "Contents"
    macos = contents / "MacOS"
    resources = contents / "Resources"
    macos.mkdir(parents=True)
    resources.mkdir(parents=True)
    launcher_source = target / "packaging" / "macos" / "launcher.py"
    if not launcher_source.is_file():
        raise SystemExit("installed release lacks the managed macOS launcher")
    shutil.copy2(launcher_source, resources / "launcher.py")
    (resources / "launcher.py").chmod(0o644)
    launcher_config = {
        "schema_version": 1,
        "product": "AgentOps MIS Private Host Launcher",
        "version": version,
        "agentops_path": str(shim),
        "bin_dir": str(bin_dir),
        "current_path": str(current),
        "host_home": str(data_root),
        "install_root": str(install_root),
        "python_path": str(Path(sys.executable).resolve()),
        "default_port": 18878,
        "credentials_included": False,
    }
    (resources / "launcher-config.json").write_text(
        json.dumps(launcher_config, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (resources / "launcher-config.json").chmod(0o644)
    (resources / "agentops-mis-launcher.json").write_text(
        json.dumps(launcher_marker_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (resources / "agentops-mis-launcher.json").chmod(0o644)
    (contents / "Info.plist").write_text(
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
        "<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">\n"
        "<plist version=\"1.0\">\n"
        "<dict>\n"
        "  <key>CFBundleDisplayName</key><string>AgentOps MIS</string>\n"
        "  <key>CFBundleExecutable</key><string>agentops-mis-launcher</string>\n"
        "  <key>CFBundleIdentifier</key><string>dev.agentops.mis.private-host</string>\n"
        "  <key>CFBundleName</key><string>AgentOps MIS</string>\n"
        "  <key>CFBundlePackageType</key><string>APPL</string>\n"
        f"  <key>CFBundleShortVersionString</key><string>{version}</string>\n"
        "  <key>CFBundleVersion</key><string>1</string>\n"
        "  <key>LSUIElement</key><true/>\n"
        "</dict>\n"
        "</plist>\n",
        encoding="utf-8",
    )
    executable = macos / "agentops-mis-launcher"
    final_launcher_source = app_bundle / "Contents" / "Resources" / "launcher.py"
    executable.write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        f"exec {shlex.quote(str(Path(sys.executable).resolve()))} {shlex.quote(str(final_launcher_source))} \"$@\"\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    backup_app = app_dir / ".AgentOps MIS.app.previous"
    if backup_app.exists() or backup_app.is_symlink():
        shutil.rmtree(stage_parent)
        raise SystemExit("stale AgentOps MIS application update state; install refused")
    previous_app = False
    try:
        if app_bundle.exists():
            app_bundle.rename(backup_app)
            previous_app = True
        stage_app.rename(app_bundle)
    except Exception:
        if previous_app and backup_app.exists() and not app_bundle.exists():
            backup_app.rename(app_bundle)
        raise
    else:
        if previous_app:
            shutil.rmtree(backup_app)
        launcher_installed = True
    finally:
        if stage_parent.exists():
            shutil.rmtree(stage_parent)

if not install_marker.exists():
    marker_stage = install_root / ".agentops-mis-install.json.next"
    marker_stage.write_text(json.dumps(expected_install_marker, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    marker_stage.chmod(0o600)
    os.replace(marker_stage, install_marker)
install_marker.chmod(0o600)

if old_current and old_current != target:
    atomic_symlink(previous, old_current)
atomic_symlink(current, target)
install_transaction["committed"] = True
atexit.unregister(rollback_uncommitted_install)

fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
os.close(lock_descriptor)

print(json.dumps({
    "ok": True,
    "operation": "install",
    "version": version,
    "install_path": str(target),
    "current_path": str(current),
    "previous_version": old_current.name if old_current and old_current != target else None,
    "pre_update_backup_path": pre_update_backup,
    "shim": str(shim),
    "worker_shim": str(worker_shim),
    "launcher": str(app_bundle) if install_app else None,
    "launcher_installed": launcher_installed,
    "launcher_starts_live_workers": False,
    "storage_preflight": storage_preflight,
    "user_data_preserved": True,
    "token_omitted": True,
}, indent=2, sort_keys=True))
PY
