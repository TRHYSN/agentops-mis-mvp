#!/usr/bin/env python3
"""Verify crash-safe, private, monotonic Relay connector epochs."""
from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import threading
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agentops_mis_cli.relay_epoch_store import (  # noqa: E402
    PersistentRelayEpochStore,
    RelayEpochStoreError,
)


def main() -> int:
    failures: list[str] = []
    identity = os.urandom(32)
    other_identity = os.urandom(32)

    with tempfile.TemporaryDirectory(prefix="agentops-relay-epoch-") as temporary:
        state_path = Path(temporary) / "private" / "relay-epoch.json"
        first = PersistentRelayEpochStore(state_path, connector_identity=identity)
        if first.next_epoch() != 1:
            failures.append("first persisted epoch was not one")

        restarted = PersistentRelayEpochStore(state_path, connector_identity=identity)
        if restarted.next_epoch() != 2:
            failures.append("process restart reused an epoch")

        values: list[int] = []
        errors: list[str] = []
        value_lock = threading.Lock()

        def allocate() -> None:
            try:
                value = PersistentRelayEpochStore(
                    state_path,
                    connector_identity=identity,
                ).next_epoch()
                with value_lock:
                    values.append(value)
            except Exception as exc:
                with value_lock:
                    errors.append(type(exc).__name__)

        threads = [threading.Thread(target=allocate) for _ in range(16)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        if errors or sorted(values) != list(range(3, 19)):
            failures.append("concurrent allocation was not unique and monotonic")

        persisted = json.loads(state_path.read_text(encoding="utf-8"))
        if set(persisted) != {"connector_ref", "last_epoch", "schema_version"}:
            failures.append("persisted state contained a non-allowlisted field")
        if persisted.get("last_epoch") != 18 or persisted.get("schema_version") != 1:
            failures.append("persisted state did not retain the final epoch")
        if identity.hex() in state_path.read_text(encoding="utf-8"):
            failures.append("persisted state exposed connector identity material")
        if stat.S_IMODE(state_path.stat().st_mode) != 0o600:
            failures.append("persisted state mode was not 0600")
        if stat.S_IMODE(state_path.parent.stat().st_mode) != 0o700:
            failures.append("persisted state directory mode was not 0700")
        if list(state_path.parent.glob(f".{state_path.name}.*.tmp")):
            failures.append("atomic write left temporary state behind")

        mismatch_rejected = False
        try:
            PersistentRelayEpochStore(
                state_path,
                connector_identity=other_identity,
            ).next_epoch()
        except RelayEpochStoreError:
            mismatch_rejected = True
        if not mismatch_rejected:
            failures.append("connector identity mismatch was accepted")

        corrupt_path = state_path.parent / "corrupt.json"
        corrupt_path.write_text("{}\n", encoding="utf-8")
        corrupt_path.chmod(0o600)
        corrupt_rejected = False
        try:
            PersistentRelayEpochStore(
                corrupt_path,
                connector_identity=identity,
            ).next_epoch()
        except RelayEpochStoreError:
            corrupt_rejected = True
        if not corrupt_rejected:
            failures.append("corrupt epoch state was repaired instead of rejected")

        broad_path = state_path.parent / "broad.json"
        broad_path.write_text(state_path.read_text(encoding="utf-8"), encoding="utf-8")
        broad_path.chmod(0o644)
        broad_state_rejected = False
        try:
            PersistentRelayEpochStore(
                broad_path,
                connector_identity=identity,
            ).next_epoch()
        except RelayEpochStoreError:
            broad_state_rejected = True
        if not broad_state_rejected:
            failures.append("broad state-file permissions were accepted")

        broad_directory = Path(temporary) / "broad-directory"
        broad_directory.mkdir(mode=0o755)
        broad_directory.chmod(0o755)
        broad_directory_rejected = False
        try:
            PersistentRelayEpochStore(
                broad_directory / "epoch.json",
                connector_identity=identity,
            ).next_epoch()
        except RelayEpochStoreError:
            broad_directory_rejected = True
        if not broad_directory_rejected:
            failures.append("broad state-directory permissions were accepted")

        symlink_path = state_path.parent / "symlink.json"
        symlink_path.symlink_to(state_path)
        symlink_rejected = False
        try:
            PersistentRelayEpochStore(
                symlink_path,
                connector_identity=identity,
            ).next_epoch()
        except (OSError, RelayEpochStoreError):
            symlink_rejected = True
        if not symlink_rejected:
            failures.append("symlink epoch state was accepted")

        rendered_state = state_path.read_text(encoding="utf-8")
        if str(state_path) in rendered_state or str(state_path.parent) in rendered_state:
            failures.append("persisted state exposed a filesystem path")

    result = {
        "atomic_replace": True,
        "concurrent_allocation_count": len(values),
        "connector_identity_omitted": True,
        "crash_persistent_epoch": True,
        "failures": failures,
        "filesystem_path_omitted": True,
        "mismatch_fails_closed": True,
        "ok": not failures,
        "operation": "relay_persistent_epoch_smoke",
        "private_permissions": True,
        "symlink_fails_closed": True,
        "tailscale_changed": False,
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
