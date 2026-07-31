#!/usr/bin/env python3
"""Security and failure-atomicity contracts for Windows Worker tasks."""
from __future__ import annotations

import argparse
import json
import shlex
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agentops_mis_cli import worker
from agentops_mis_cli import platform_paths


def install_args(path: Path, *, adapter: str = "mock", confirm_run: bool = False) -> argparse.Namespace:
    values = [
        "--manager", "windows-task",
        "--adapter", adapter,
        "--base-url", "https://host.example",
        "--workspace-id", "local-demo",
        "--agent-id", f"agt_windows_{adapter}_security",
        "--credential-source", "local_config",
        "--config-path", str(path.parent / "config.json"),
        "--service-path", str(path),
        "--confirm-install",
    ]
    if confirm_run:
        values.append("--confirm-run")
    if adapter == "hermes":
        values.extend(["--hermes-gateway-url", "http://127.0.0.1:8642"])
    return worker.build_service_install_parser().parse_args(values)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="agentops-windows-service-security-") as temporary:
        root = Path(temporary)

        hermes_path = root / "hermes.xml"
        hermes = worker.install_service_file(install_args(hermes_path, adapter="hermes", confirm_run=True))
        assert hermes.get("ok") is True, hermes
        assert (hermes.get("service_check") or {}).get("service_file", {}).get("confirm_gate_ok") is True

        unsafe_args = install_args(root / "unsafe-token.xml")
        unsafe_args.credential_source = "direct"
        unsafe_args.api_key_placeholder = "agtok_TEST_ONLY"
        unsafe = worker.install_service_file(unsafe_args)
        assert unsafe.get("ok") is False and unsafe.get("wrote") is False, unsafe
        assert not Path(unsafe_args.service_path).exists()

        forged_path = root / "forged.xml"
        forged_args = install_args(forged_path)
        forged_xml = worker.render_windows_task_template(forged_args)
        namespace = {"task": worker.WINDOWS_TASK_NAMESPACE}
        tree = ET.fromstring(forged_xml)
        command = tree.find("./task:Actions/task:Exec/task:Command", namespace)
        assert command is not None
        command.text = "calc.exe"
        description = tree.find("./task:RegistrationInfo/task:Description", namespace)
        assert description is not None
        description.text = "agentops-worker --adapter mock --base-url https://host.example --confirm-run"
        forged_path.write_text(ET.tostring(tree, encoding="unicode"), encoding="utf-8")
        forged_path.chmod(0o600)
        check_args = worker.build_service_check_parser().parse_args([
            "--manager", "windows-task",
            "--adapter", "mock",
            "--base-url", "https://host.example",
            "--workspace-id", "local-demo",
            "--agent-id", "agt_windows_mock_security",
            "--credential-source", "local_config",
            "--service-path", str(forged_path),
        ])
        forged_check = worker.check_service_installation(check_args)
        assert forged_check.get("ok") is False, forged_check
        assert (forged_check.get("windows_task_contract") or {}).get("command_matches") is False
        control_args = worker.build_service_control_parser().parse_args([
            "--manager", "windows-task",
            "--action", "load",
            "--adapter", "mock",
            "--base-url", "https://host.example",
            "--workspace-id", "local-demo",
            "--agent-id", "agt_windows_mock_security",
            "--credential-source", "local_config",
            "--service-path", str(forged_path),
        ])
        forged_control = worker.control_service(control_args)
        assert forged_control.get("ok") is False, forged_control
        assert any("complete service contract" in item for item in forged_control.get("failures") or [])

        valid_xml = worker.render_windows_task_template(forged_args)

        extra_action_tree = ET.fromstring(valid_xml)
        actions = extra_action_tree.find("./task:Actions", namespace)
        assert actions is not None
        com_handler = ET.SubElement(actions, f"{{{worker.WINDOWS_TASK_NAMESPACE}}}ComHandler")
        ET.SubElement(com_handler, f"{{{worker.WINDOWS_TASK_NAMESPACE}}}ClassId").text = "{00000000-0000-0000-0000-000000000000}"
        extra_action = worker.inspect_windows_task(ET.tostring(extra_action_tree, encoding="unicode"), forged_args)
        assert extra_action.get("valid") is False and extra_action.get("error") == "windows_task_structure_invalid", extra_action

        duplicate_option_tree = ET.fromstring(valid_xml)
        duplicate_arguments = duplicate_option_tree.find("./task:Actions/task:Exec/task:Arguments", namespace)
        assert duplicate_arguments is not None
        duplicate_arguments.text = (duplicate_arguments.text or "") + " --adapter openclaw --confirm-run --openclaw-bin calc.exe"
        duplicate_option = worker.inspect_windows_task(ET.tostring(duplicate_option_tree, encoding="unicode"), forged_args)
        assert duplicate_option.get("valid") is False and duplicate_option.get("argument_bindings_match") is False, duplicate_option

        working_directory_tree = ET.fromstring(valid_xml)
        working_directory = working_directory_tree.find("./task:Actions/task:Exec/task:WorkingDirectory", namespace)
        assert working_directory is not None
        working_directory.text = str(root / "substituted-working-directory")
        substituted_directory = worker.inspect_windows_task(ET.tostring(working_directory_tree, encoding="unicode"), forged_args)
        assert substituted_directory.get("valid") is False and substituted_directory.get("working_directory_matches") is False, substituted_directory

        original_is_windows = worker.is_windows
        original_sid = worker.windows_current_sid
        original_parser = worker.parse_windows_command_line
        try:
            worker.is_windows = lambda: True
            worker.windows_current_sid = lambda: ""
            worker.parse_windows_command_line = lambda arguments: [
                item[1:-1] if len(item) >= 2 and item[0] == item[-1] == '"' else item
                for item in shlex.split(arguments, posix=False)
            ]
            sid_failure = worker.inspect_windows_task(valid_xml, forged_args)
        finally:
            worker.is_windows = original_is_windows
            worker.windows_current_sid = original_sid
            worker.parse_windows_command_line = original_parser
        assert sid_failure.get("valid") is False and sid_failure.get("sid_resolution_ok") is False, sid_failure

        original_acl_reader = platform_paths._windows_acl_sddl
        original_platform_sid = platform_paths.windows_current_sid
        try:
            platform_paths._windows_acl_sddl = lambda _path: "O:BAD:P(A;;FA;;;LA)(A;;FA;;;SY)"
            platform_paths.windows_current_sid = lambda: "S-1-5-21-1000-500"
            singleton_acl_shape = platform_paths.windows_private_file_is_acceptable(root / "shape-only")
        finally:
            platform_paths._windows_acl_sddl = original_acl_reader
            platform_paths.windows_current_sid = original_platform_sid
        assert singleton_acl_shape is True

        prior_path = root / "prior.xml"
        prior_content = "prior-service-definition\n"
        prior_path.write_text(prior_content, encoding="utf-8")
        prior_path.chmod(0o600)
        atomic_args = install_args(prior_path)
        atomic_args.overwrite = True
        original_hardener = worker.harden_private_file
        try:
            def fail_acl(_path: Path) -> None:
                raise OSError("injected_service_acl_failure")

            worker.harden_private_file = fail_acl
            atomic = worker.install_service_file(atomic_args)
        finally:
            worker.harden_private_file = original_hardener
        assert atomic.get("ok") is False and atomic.get("wrote") is False, atomic
        assert prior_path.read_text(encoding="utf-8") == prior_content
        assert not list(root.glob(f".{prior_path.name}.*.tmp"))

        print(json.dumps({
            "ok": True,
            "hermes_confirm_gate": True,
            "raw_api_key_task_blocked": True,
            "forged_action_blocked": True,
            "additional_action_blocked": True,
            "duplicate_override_blocked": True,
            "working_directory_substitution_blocked": True,
            "sid_resolution_failure_blocked": True,
            "local_administrator_sddl_alias_accepted": True,
            "complete_contract_required_for_control": True,
            "service_acl_failure_atomic": True,
            "previous_service_preserved": True,
            "token_omitted": True,
        }, indent=2, sort_keys=True))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
