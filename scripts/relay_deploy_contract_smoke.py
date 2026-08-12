#!/usr/bin/env python3
"""Verify the static, credential-free Relay packaging contract."""
from __future__ import annotations

import ast
import configparser
import importlib.util
import json
import re
import shlex
import tarfile
import tempfile
import zipfile
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
BACKEND = ROOT / "agentops_mis_cli" / "_build_backend.py"
SYSTEMD_UNIT = ROOT / "packaging" / "relay" / "systemd" / "agentops-mis-relay.service"
CONFIG_EXAMPLE = ROOT / "packaging" / "relay" / "config.example.json"
ACCEPTANCE = ROOT / "docs" / "LOCAL_RELAY_DEPLOY_CONTRACT_ACCEPTANCE.md"
ENTRYPOINT_NAME = "agentops-relay"
ENTRYPOINT_TARGET = "agentops_mis_cli.relay_daemon:main"
ADMIN_ENTRYPOINT_NAME = "agentops-relayctl"
ADMIN_ENTRYPOINT_TARGET = "agentops_mis_cli.relay_admin:main"
EXPECTED_EXEC_START = [
    "/usr/local/bin/agentops-relay",
    "serve",
    "--config",
    "/etc/agentops-mis-relay/config.json",
]
EXPECTED_EXEC_START_PRE = [
    "/usr/local/bin/agentops-relay",
    "check",
    "--config",
    "/etc/agentops-mis-relay/config.json",
]


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def project_scripts(payload: str) -> dict[str, str]:
    scripts: dict[str, str] = {}
    active = False
    for raw_line in payload.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            active = line == "[project.scripts]"
            continue
        if not active or "=" not in line:
            continue
        name, raw_value = line.split("=", 1)
        value = ast.literal_eval(raw_value.strip())
        if not isinstance(value, str):
            raise ValueError("project script target must be a string")
        scripts[name.strip()] = value
    return scripts


def load_backend() -> ModuleType:
    spec = importlib.util.spec_from_file_location("agentops_mis_build_backend", BACKEND)
    if spec is None or spec.loader is None:
        raise RuntimeError("build backend is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def entry_points(payload: str) -> dict[str, str]:
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    parser.read_string(payload)
    return dict(parser.items("console_scripts")) if parser.has_section("console_scripts") else {}


def unit_sections(payload: str) -> dict[str, dict[str, list[str]]]:
    sections: dict[str, dict[str, list[str]]] = {}
    current: dict[str, list[str]] | None = None
    for raw_line in payload.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = sections.setdefault(line[1:-1], {})
            continue
        if current is None or "=" not in line:
            raise ValueError("invalid systemd unit syntax")
        name, value = line.split("=", 1)
        current.setdefault(name, []).append(value)
    return sections


def one(sections: dict[str, dict[str, list[str]]], section: str, name: str) -> str:
    values = sections.get(section, {}).get(name, [])
    return values[0] if len(values) == 1 else ""


def main() -> int:
    failures: list[str] = []
    pyproject_scripts: dict[str, str] = {}
    backend_scripts: dict[str, str] = {}
    wheel_scripts: dict[str, str] = {}
    wheel_has_unit = False
    sdist_has_unit = False
    sdist_has_config_example = False
    sdist_has_acceptance = False
    sdist_has_release_acceptance = False
    sdist_has_install_acceptance = False
    sdist_has_status_acceptance = False
    sdist_has_activation_controller_acceptance = False
    sdist_has_activation_evidence_acceptance = False
    sdist_has_activation_journal_acceptance = False
    sdist_has_activation_journal_status_acceptance = False
    sdist_has_activation_namespace_install_acceptance = False
    sdist_has_activation_production_store_acceptance = False
    sdist_has_activation_recovery_controller_acceptance = False
    sdist_has_activation_recovery_decision_acceptance = False
    sdist_has_activation_recovery_executor_acceptance = False
    sdist_has_activation_recovery_preview_acceptance = False
    sdist_has_activation_recovery_snapshot_acceptance = False
    sdist_has_activation_plan_acceptance = False
    sdist_has_activation_preview_acceptance = False
    sdist_has_activation_scanner_acceptance = False
    sdist_has_linux_production_install_acceptance = False
    sdist_has_linux_production_systemd_acceptance = False
    sdist_has_linux_systemd_recovery_acceptance = False
    sdist_has_systemd_mutation_acceptance = False
    sdist_has_config_parser_acceptance = False
    sdist_has_activation_spec = False
    sdist_has_pkg_info = False
    sdist_pkg_info_matches = False
    metadata_version_current = False
    wheel_reproducible = False
    wheel_metadata_normalized = False
    prepared_metadata_round_trip = False
    sdist_reproducible = False
    sdist_metadata_normalized = False
    unit_payload = ""
    sections: dict[str, dict[str, list[str]]] = {}
    unit_contains_forbidden_material = False

    try:
        pyproject_scripts = project_scripts(PYPROJECT.read_text(encoding="utf-8"))
    except Exception:
        failures.append("pyproject scripts could not be parsed")

    try:
        backend = load_backend()
        backend_scripts = entry_points(backend._entry_points())
        with tempfile.TemporaryDirectory(prefix="agentops-relay-deploy-contract-") as temporary:
            output = Path(temporary)
            first = output / "first"
            second = output / "second"
            first.mkdir()
            second.mkdir()
            relay_config = {
                backend.DISTRIBUTION_CONFIG_KEY: backend.RELAY_DISTRIBUTION,
            }
            wheel_name = backend.build_wheel(
                str(first),
                config_settings=relay_config,
            )
            second_wheel_name = backend.build_wheel(
                str(second),
                config_settings=relay_config,
            )
            wheel_reproducible = (
                wheel_name == second_wheel_name
                and (first / wheel_name).read_bytes()
                == (second / second_wheel_name).read_bytes()
            )
            with zipfile.ZipFile(first / wheel_name) as wheel:
                names = wheel.namelist()
                wheel_metadata_normalized = all(
                    info.date_time == (1980, 1, 1, 0, 0, 0)
                    and (info.external_attr >> 16) & 0o777 == 0o644
                    for info in wheel.infolist()
                )
                entry_name = next(
                    (name for name in names if name.endswith(".dist-info/entry_points.txt")),
                    "",
                )
                if entry_name:
                    wheel_scripts = entry_points(wheel.read(entry_name).decode("utf-8"))
                metadata_name = next(
                    (name for name in names if name.endswith(".dist-info/METADATA")),
                    "",
                )
                if metadata_name:
                    metadata_version_current = (
                        wheel.read(metadata_name)
                        .decode("utf-8")
                        .startswith("Metadata-Version: 2.2\n")
                    )
                wheel_has_unit = any(name.endswith("/agentops-mis-relay.service") for name in names)

            prepared_root = output / "prepared"
            prepared_wheel_output = output / "prepared-wheel"
            prepared_wheel_output.mkdir()
            prepared_name = backend.prepare_metadata_for_build_wheel(
                str(prepared_root),
                config_settings=relay_config,
            )
            prepared_dist_info = prepared_root / prepared_name
            custom_metadata = prepared_dist_info / "licenses" / "agentops-build-contract.json"
            custom_metadata.parent.mkdir()
            custom_metadata.write_text('{"schema_version":1}\n', encoding="utf-8")
            prepared_wheel_name = backend.build_wheel(
                str(prepared_wheel_output),
                config_settings=relay_config,
                metadata_directory=str(prepared_root),
            )
            with zipfile.ZipFile(prepared_wheel_output / prepared_wheel_name) as wheel:
                prepared_metadata_round_trip = (
                    not (prepared_dist_info / "RECORD").exists()
                    and all(
                        wheel.read(
                            f"{backend.DIST_INFO}/"
                            f"{path.relative_to(prepared_dist_info).as_posix()}"
                        )
                        == path.read_bytes()
                        for path in prepared_dist_info.rglob("*")
                        if path.is_file()
                    )
                )

            sdist_name = backend.build_sdist(str(first))
            second_sdist_name = backend.build_sdist(str(second))
            sdist_reproducible = (
                sdist_name == second_sdist_name
                and (first / sdist_name).read_bytes()
                == (second / second_sdist_name).read_bytes()
            )
            with tarfile.open(first / sdist_name, "r:gz") as source:
                members = source.getmembers()
                sdist_metadata_normalized = all(
                    member.mtime == 0
                    and member.uid == 0
                    and member.gid == 0
                    and member.uname == ""
                    and member.gname == ""
                    and member.mode == 0o644
                    for member in members
                )
                names = source.getnames()
                sdist_has_unit = any(
                    name.endswith("/packaging/relay/systemd/agentops-mis-relay.service")
                    for name in names
                )
                sdist_has_config_example = any(
                    name.endswith("/packaging/relay/config.example.json")
                    for name in names
                )
                sdist_has_acceptance = any(
                    name.endswith("/docs/LOCAL_RELAY_DEPLOY_CONTRACT_ACCEPTANCE.md")
                    for name in names
                )
                sdist_has_release_acceptance = any(
                    name.endswith("/docs/RELAY_RELEASE_BUNDLE_ACCEPTANCE.md")
                    for name in names
                )
                sdist_has_install_acceptance = any(
                    name.endswith("/docs/RELAY_OFFLINE_INSTALL_ACCEPTANCE.md")
                    for name in names
                )
                sdist_has_status_acceptance = any(
                    name.endswith("/docs/RELAY_OFFLINE_STATUS_ACCEPTANCE.md")
                    for name in names
                )
                sdist_has_activation_plan_acceptance = any(
                    name.endswith(
                        "/docs/RELAY_ACTIVATION_PLAN_CORE_ACCEPTANCE.md"
                    )
                    for name in names
                )
                sdist_has_activation_journal_acceptance = any(
                    name.endswith(
                        "/docs/RELAY_ACTIVATION_JOURNAL_ACCEPTANCE.md"
                    )
                    for name in names
                )
                sdist_has_activation_controller_acceptance = any(
                    name.endswith(
                        "/docs/RELAY_ACTIVATION_CONTROLLER_SUCCESS_ACCEPTANCE.md"
                    )
                    for name in names
                )
                sdist_has_activation_evidence_acceptance = any(
                    name.endswith(
                        "/docs/RELAY_ACTIVATION_EVIDENCE_ACCEPTANCE.md"
                    )
                    for name in names
                )
                sdist_has_activation_journal_status_acceptance = any(
                    name.endswith(
                        "/docs/RELAY_ACTIVATION_JOURNAL_STATUS_ACCEPTANCE.md"
                    )
                    for name in names
                )
                sdist_has_activation_namespace_install_acceptance = any(
                    name.endswith(
                        "/docs/RELAY_ACTIVATION_NAMESPACE_INSTALL_ACCEPTANCE.md"
                    )
                    for name in names
                )
                sdist_has_activation_production_store_acceptance = any(
                    name.endswith(
                        "/docs/RELAY_ACTIVATION_PRODUCTION_STORE_ACCEPTANCE.md"
                    )
                    for name in names
                )
                sdist_has_activation_recovery_snapshot_acceptance = any(
                    name.endswith(
                        "/docs/RELAY_ACTIVATION_RECOVERY_SNAPSHOT_ACCEPTANCE.md"
                    )
                    for name in names
                )
                sdist_has_activation_recovery_controller_acceptance = any(
                    name.endswith(
                        "/docs/RELAY_ACTIVATION_RECOVERY_CONTROLLER_ACCEPTANCE.md"
                    )
                    for name in names
                )
                sdist_has_activation_recovery_decision_acceptance = any(
                    name.endswith(
                        "/docs/RELAY_ACTIVATION_RECOVERY_DECISION_ACCEPTANCE.md"
                    )
                    for name in names
                )
                sdist_has_activation_recovery_executor_acceptance = any(
                    name.endswith(
                        "/docs/RELAY_ACTIVATION_RECOVERY_EXECUTOR_ACCEPTANCE.md"
                    )
                    for name in names
                )
                sdist_has_activation_recovery_preview_acceptance = any(
                    name.endswith(
                        "/docs/RELAY_ACTIVATION_RECOVERY_PREVIEW_ACCEPTANCE.md"
                    )
                    for name in names
                )
                sdist_has_activation_preview_acceptance = any(
                    name.endswith(
                        "/docs/RELAY_ACTIVATION_PREVIEW_ACCEPTANCE.md"
                    )
                    for name in names
                )
                sdist_has_activation_scanner_acceptance = any(
                    name.endswith(
                        "/docs/RELAY_ACTIVATION_SCANNER_ACCEPTANCE.md"
                    )
                    for name in names
                )
                sdist_has_linux_production_install_acceptance = any(
                    name.endswith(
                        "/docs/RELAY_LINUX_PRODUCTION_INSTALL_ACCEPTANCE.md"
                    )
                    for name in names
                )
                sdist_has_linux_production_systemd_acceptance = any(
                    name.endswith(
                        "/docs/RELAY_LINUX_PRODUCTION_SYSTEMD_ACCEPTANCE.md"
                    )
                    for name in names
                )
                sdist_has_linux_systemd_recovery_acceptance = any(
                    name.endswith(
                        "/docs/RELAY_LINUX_SYSTEMD_RECOVERY_ACCEPTANCE.md"
                    )
                    for name in names
                )
                sdist_has_systemd_mutation_acceptance = any(
                    name.endswith(
                        "/docs/RELAY_SYSTEMD_MUTATION_ADAPTER_ACCEPTANCE.md"
                    )
                    for name in names
                )
                sdist_has_config_parser_acceptance = any(
                    name.endswith("/docs/RELAY_CONFIG_PARSER_ACCEPTANCE.md")
                    for name in names
                )
                sdist_has_activation_spec = any(
                    name.endswith("/docs/RELAY_SERVICE_ACTIVATION_SPEC.md")
                    for name in names
                )
                pkg_info_name = next(
                    (name for name in names if name.endswith("/PKG-INFO")),
                    "",
                )
                sdist_has_pkg_info = bool(pkg_info_name)
                pkg_info = source.extractfile(pkg_info_name) if pkg_info_name else None
                sdist_pkg_info_matches = bool(
                    pkg_info
                    and pkg_info.read()
                    == backend._metadata().encode("utf-8")
                )
    except Exception:
        failures.append("offline build artifacts could not be inspected")

    try:
        unit_payload = SYSTEMD_UNIT.read_text(encoding="utf-8")
        sections = unit_sections(unit_payload)
    except Exception:
        failures.append("systemd unit could not be parsed")

    require(
        pyproject_scripts.get(ENTRYPOINT_NAME) == ENTRYPOINT_TARGET,
        "pyproject entrypoint is missing or incorrect",
        failures,
    )
    require(
        backend_scripts.get(ENTRYPOINT_NAME) == ENTRYPOINT_TARGET,
        "custom build backend entrypoint is missing or incorrect",
        failures,
    )
    require(
        wheel_scripts.get(ENTRYPOINT_NAME) == ENTRYPOINT_TARGET,
        "wheel entrypoint is missing or incorrect",
        failures,
    )
    for source, scripts in (
        ("pyproject", pyproject_scripts),
        ("custom build backend", backend_scripts),
        ("wheel", wheel_scripts),
    ):
        require(
            scripts.get(ADMIN_ENTRYPOINT_NAME) == ADMIN_ENTRYPOINT_TARGET,
            f"{source} Relay admin entrypoint is missing or incorrect",
            failures,
        )
    require(wheel_reproducible, "wheel bytes are not reproducible", failures)
    require(wheel_metadata_normalized, "wheel metadata is not normalized", failures)
    require(
        metadata_version_current,
        "wheel and sdist metadata must use Core Metadata 2.2 or newer",
        failures,
    )
    require(
        prepared_metadata_round_trip,
        "prepared wheel metadata was not preserved exactly",
        failures,
    )
    require(sdist_reproducible, "source distribution bytes are not reproducible", failures)
    require(
        sdist_metadata_normalized,
        "source distribution metadata is not normalized",
        failures,
    )
    require(not wheel_has_unit, "wheel must not auto-install a systemd unit", failures)
    require(sdist_has_unit, "source distribution omits the systemd template", failures)
    require(sdist_has_pkg_info, "source distribution omits PKG-INFO", failures)
    require(
        sdist_pkg_info_matches,
        "source distribution PKG-INFO differs from wheel metadata",
        failures,
    )
    require(
        sdist_has_config_example,
        "source distribution omits the credential-free config example",
        failures,
    )
    require(sdist_has_acceptance, "source distribution omits the deploy acceptance", failures)
    require(
        sdist_has_release_acceptance,
        "source distribution omits the release-bundle acceptance",
        failures,
    )
    require(
        sdist_has_install_acceptance,
        "source distribution omits the offline-install acceptance",
        failures,
    )
    require(
        sdist_has_status_acceptance,
        "source distribution omits the offline-status acceptance",
        failures,
    )
    require(
        sdist_has_activation_plan_acceptance,
        "source distribution omits the activation-plan-core acceptance",
        failures,
    )
    require(
        sdist_has_activation_controller_acceptance,
        "source distribution omits the activation-controller acceptance",
        failures,
    )
    require(
        sdist_has_activation_evidence_acceptance,
        "source distribution omits the activation-evidence acceptance",
        failures,
    )
    require(
        sdist_has_activation_journal_acceptance,
        "source distribution omits the activation-journal acceptance",
        failures,
    )
    require(
        sdist_has_activation_journal_status_acceptance,
        "source distribution omits the activation-journal-status acceptance",
        failures,
    )
    require(
        sdist_has_activation_namespace_install_acceptance,
        "source distribution omits the activation-namespace-install acceptance",
        failures,
    )
    require(
        sdist_has_activation_production_store_acceptance,
        "source distribution omits the activation-production-store acceptance",
        failures,
    )
    require(
        sdist_has_activation_recovery_controller_acceptance,
        "source distribution omits the activation-recovery-controller acceptance",
        failures,
    )
    require(
        sdist_has_activation_recovery_decision_acceptance,
        "source distribution omits the activation-recovery-decision acceptance",
        failures,
    )
    require(
        sdist_has_activation_recovery_executor_acceptance,
        "source distribution omits the activation-recovery-executor acceptance",
        failures,
    )
    require(
        sdist_has_activation_recovery_preview_acceptance,
        "source distribution omits the activation-recovery-preview acceptance",
        failures,
    )
    require(
        sdist_has_activation_recovery_snapshot_acceptance,
        "source distribution omits the activation-recovery-snapshot acceptance",
        failures,
    )
    require(
        sdist_has_activation_preview_acceptance,
        "source distribution omits the activation-preview acceptance",
        failures,
    )
    require(
        sdist_has_activation_scanner_acceptance,
        "source distribution omits the activation-scanner acceptance",
        failures,
    )
    require(
        sdist_has_linux_production_install_acceptance,
        "source distribution omits the Linux production-install acceptance",
        failures,
    )
    require(
        sdist_has_linux_production_systemd_acceptance,
        "source distribution omits the Linux production-systemd acceptance",
        failures,
    )
    require(
        sdist_has_linux_systemd_recovery_acceptance,
        "source distribution omits the Linux systemd recovery acceptance",
        failures,
    )
    require(
        sdist_has_systemd_mutation_acceptance,
        "source distribution omits the systemd-mutation acceptance",
        failures,
    )
    require(
        sdist_has_config_parser_acceptance,
        "source distribution omits the config-parser acceptance",
        failures,
    )
    require(
        sdist_has_activation_spec,
        "source distribution omits the service-activation specification",
        failures,
    )

    exec_start = one(sections, "Service", "ExecStart")
    try:
        exec_arguments = shlex.split(exec_start)
    except ValueError:
        exec_arguments = []
    require(exec_arguments == EXPECTED_EXEC_START, "systemd ExecStart contract changed", failures)
    try:
        preflight_arguments = shlex.split(one(sections, "Service", "ExecStartPre"))
    except ValueError:
        preflight_arguments = []
    require(
        preflight_arguments == EXPECTED_EXEC_START_PRE,
        "systemd ExecStartPre contract changed",
        failures,
    )
    required_service_values = {
        "Type": "simple",
        "User": "agentops-relay",
        "Group": "agentops-relay",
        "Restart": "on-failure",
        "RestartSec": "5s",
        "KillSignal": "SIGTERM",
        "RuntimeDirectoryMode": "0700",
        "RuntimeDirectoryPreserve": "yes",
        "StateDirectoryMode": "0700",
        "UMask": "0077",
        "NoNewPrivileges": "true",
        "PrivateTmp": "true",
        "PrivateDevices": "true",
        "ProtectSystem": "strict",
        "ProtectHome": "true",
        "RestrictAddressFamilies": "AF_UNIX AF_INET AF_INET6",
        "CapabilityBoundingSet": "CAP_NET_BIND_SERVICE",
        "AmbientCapabilities": "CAP_NET_BIND_SERVICE",
    }
    for name, expected in required_service_values.items():
        require(
            one(sections, "Service", name) == expected,
            f"systemd {name} contract changed",
            failures,
        )
    require(
        one(sections, "Install", "WantedBy") == "multi-user.target",
        "systemd install target is missing",
        failures,
    )

    forbidden_unit_patterns = (
        r"(?im)^\s*(Environment|EnvironmentFile|PassEnvironment|LoadCredential|SetCredential)=",
        r"(?i)(AGENTOPS_API_KEY|AGENTOPS_ADMIN_KEY|TOKEN=|PASSWORD=|tunnel_key|private_key)",
        r"(?i)(https?://|tailscale|acme)",
        r"\b(?:\d{1,3}\.){3}\d{1,3}\b",
    )
    unit_contains_forbidden_material = any(
        re.search(pattern, unit_payload) for pattern in forbidden_unit_patterns
    )
    require(
        not unit_contains_forbidden_material,
        "systemd template contains credential, endpoint, or external-infrastructure material",
        failures,
    )
    require(
        one(sections, "Service", "ReadOnlyPaths") == "/etc/agentops-mis-relay",
        "systemd config directory is not read-only",
        failures,
    )
    require(
        one(sections, "Service", "ReadWritePaths")
        == "/var/lib/agentops-mis-relay /run/agentops-mis-relay",
        "systemd writable paths are not narrowly scoped",
        failures,
    )
    try:
        config_example = json.loads(CONFIG_EXAMPLE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        config_example = {}
    example_text = json.dumps(config_example, sort_keys=True)
    require(
        config_example.get("schema_version") == 1
        and isinstance(config_example.get("routes"), list)
        and len(config_example.get("routes") or []) == 1,
        "config example shape is missing or ambiguous",
        failures,
    )
    require(
        "example.invalid" in example_text,
        "config example does not use a reserved invalid hostname",
        failures,
    )
    require(
        not re.search(r"(?i)(token|password|api_key|tunnel_key)", example_text),
        "config example embeds a credential field",
        failures,
    )

    try:
        acceptance = ACCEPTANCE.read_text(encoding="utf-8")
    except OSError:
        acceptance = ""
    require("deployed_relay: false" in acceptance, "acceptance omits deployed Relay denial", failures)
    require("dns_acme: false" in acceptance, "acceptance omits DNS/ACME denial", failures)
    require(
        "does not import or execute the Relay daemon" in acceptance,
        "acceptance overstates runtime verification",
        failures,
    )

    result = {
        "operation": "relay_deploy_contract_smoke",
        "ok": not failures,
        "entrypoint": ENTRYPOINT_NAME,
        "entrypoint_target": ENTRYPOINT_TARGET,
        "relay_admin_entrypoint": ADMIN_ENTRYPOINT_NAME,
        "relay_admin_entrypoint_target": ADMIN_ENTRYPOINT_TARGET,
        "relay_admin_wheel_entrypoint_present": (
            wheel_scripts.get(ADMIN_ENTRYPOINT_NAME) == ADMIN_ENTRYPOINT_TARGET
        ),
        "wheel_entrypoint_present": wheel_scripts.get(ENTRYPOINT_NAME) == ENTRYPOINT_TARGET,
        "wheel_installs_systemd_unit": wheel_has_unit,
        "wheel_reproducible": wheel_reproducible,
        "wheel_metadata_normalized": wheel_metadata_normalized,
        "prepared_metadata_round_trip": prepared_metadata_round_trip,
        "sdist_reproducible": sdist_reproducible,
        "sdist_metadata_normalized": sdist_metadata_normalized,
        "sdist_has_pkg_info": sdist_has_pkg_info,
        "sdist_pkg_info_matches": sdist_pkg_info_matches,
        "sdist_includes_systemd_unit": sdist_has_unit,
        "sdist_includes_config_example": sdist_has_config_example,
        "sdist_includes_acceptance": sdist_has_acceptance,
        "sdist_includes_release_acceptance": sdist_has_release_acceptance,
        "sdist_includes_install_acceptance": sdist_has_install_acceptance,
        "sdist_includes_status_acceptance": sdist_has_status_acceptance,
        "sdist_includes_activation_plan_acceptance": (
            sdist_has_activation_plan_acceptance
        ),
        "sdist_includes_activation_journal_acceptance": (
            sdist_has_activation_journal_acceptance
        ),
        "sdist_includes_activation_journal_status_acceptance": (
            sdist_has_activation_journal_status_acceptance
        ),
        "sdist_includes_activation_controller_acceptance": (
            sdist_has_activation_controller_acceptance
        ),
        "sdist_includes_activation_evidence_acceptance": (
            sdist_has_activation_evidence_acceptance
        ),
        "sdist_includes_activation_namespace_install_acceptance": (
            sdist_has_activation_namespace_install_acceptance
        ),
        "sdist_includes_activation_production_store_acceptance": (
            sdist_has_activation_production_store_acceptance
        ),
        "sdist_includes_activation_recovery_controller_acceptance": (
            sdist_has_activation_recovery_controller_acceptance
        ),
        "sdist_includes_activation_recovery_decision_acceptance": (
            sdist_has_activation_recovery_decision_acceptance
        ),
        "sdist_includes_activation_recovery_executor_acceptance": (
            sdist_has_activation_recovery_executor_acceptance
        ),
        "sdist_includes_activation_recovery_preview_acceptance": (
            sdist_has_activation_recovery_preview_acceptance
        ),
        "sdist_includes_activation_recovery_snapshot_acceptance": (
            sdist_has_activation_recovery_snapshot_acceptance
        ),
        "sdist_includes_activation_preview_acceptance": (
            sdist_has_activation_preview_acceptance
        ),
        "sdist_includes_activation_scanner_acceptance": (
            sdist_has_activation_scanner_acceptance
        ),
        "sdist_includes_linux_production_install_acceptance": (
            sdist_has_linux_production_install_acceptance
        ),
        "sdist_includes_linux_production_systemd_acceptance": (
            sdist_has_linux_production_systemd_acceptance
        ),
        "sdist_includes_linux_systemd_recovery_acceptance": (
            sdist_has_linux_systemd_recovery_acceptance
        ),
        "sdist_includes_systemd_mutation_acceptance": (
            sdist_has_systemd_mutation_acceptance
        ),
        "sdist_includes_config_parser_acceptance": (
            sdist_has_config_parser_acceptance
        ),
        "sdist_includes_activation_spec": sdist_has_activation_spec,
        "credential_or_endpoint_material_present": unit_contains_forbidden_material,
        "daemon_imported_or_executed": False,
        "network_used": False,
        "systemd_invoked": False,
        "deployed_relay": False,
        "dns_acme": False,
        "failures": failures,
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
