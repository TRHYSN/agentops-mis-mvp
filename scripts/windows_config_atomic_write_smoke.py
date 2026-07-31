#!/usr/bin/env python3
"""Prove config ACL failure cannot publish replacement credentials."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agentops_mis_cli import agentops


def main() -> int:
    original_path = agentops.CONFIG_PATH
    original_hardener = agentops.harden_private_file
    try:
        with tempfile.TemporaryDirectory(prefix="agentops-config-atomic-") as temporary:
            config_path = Path(temporary) / "config.json"
            original = {"base_url": "https://host.example", "api_key": "old-secret"}
            replacement = {"base_url": "https://host.example", "api_key": "new-secret"}
            config_path.write_text(json.dumps(original), encoding="utf-8")
            agentops.CONFIG_PATH = config_path

            def fail_acl(_path: Path) -> None:
                raise OSError("injected_acl_failure")

            agentops.harden_private_file = fail_acl
            failed_closed = False
            try:
                agentops.save_config(replacement)
            except OSError as exc:
                failed_closed = str(exc) == "injected_acl_failure"

            persisted = json.loads(config_path.read_text(encoding="utf-8"))
            temporary_files = list(config_path.parent.glob(f".{config_path.name}.*.tmp"))
            assert failed_closed, "ACL failure was not propagated"
            assert persisted == original, "replacement config was published before ACL success"
            assert not temporary_files, "failed private config left temporary files"
            print(json.dumps({
                "ok": True,
                "acl_failure_injected": True,
                "previous_config_preserved": True,
                "replacement_secret_published": False,
                "temporary_files_removed": True,
                "secret_values_omitted": True,
            }, indent=2, sort_keys=True))
            return 0
    finally:
        agentops.CONFIG_PATH = original_path
        agentops.harden_private_file = original_hardener


if __name__ == "__main__":
    raise SystemExit(main())
