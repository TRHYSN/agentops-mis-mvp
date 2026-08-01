#!/usr/bin/env python3
"""Offline static contract smoke for the repository AgentOps MIS Codex plugin."""

from __future__ import annotations

import ast
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
FILES = {
    "manifest": ROOT / "plugins" / "agentops-mis" / ".codex-plugin" / "plugin.json",
    "marketplace": ROOT / ".agents" / "plugins" / "marketplace.json",
    "skill": (
        ROOT
        / "plugins"
        / "agentops-mis"
        / "skills"
        / "agentops-mis"
        / "SKILL.md"
    ),
    "cli_map": (
        ROOT
        / "plugins"
        / "agentops-mis"
        / "skills"
        / "agentops-mis"
        / "references"
        / "CLI_MAP.md"
    ),
    "cli_parser": ROOT / "agentops_mis_cli" / "agentops.py",
}

EXPECTED_PLUGIN_NAME = "agentops-mis"
EXPECTED_PLUGIN_VERSION = "0.1.0"
EXPECTED_MARKETPLACE_SOURCE = "./plugins/agentops-mis"

REQUIRED_INTERFACE_CAPABILITIES = {"Interactive", "Read", "Write"}

REQUIRED_SKILL_STATEMENTS = {
    "bidirectional_mis_to_codex": (
        "MIS -> Codex",
        "scoped task + bounded context + plan/approval state",
    ),
    "bidirectional_codex_to_mis": (
        "Codex -> MIS",
        "heartbeat + run/tool/evaluation/artifact/audit summaries",
    ),
    "mis_authority": (
        "Use AgentOps MIS as the authority for tasks, plans, approvals, runs, evidence,",
        "Codex is the execution client.",
    ),
    "no_nested_codex_worker": (
        "Current Codex Is The MIS Client",
        "Do **not** call:",
        "agentops workflow run-task --adapter codex",
        "would create a nested execution",
    ),
    "separate_worker_explicit_only": (
        "MIS Launches A Separate Codex Worker",
        "only when the",
        "human explicitly asks MIS to start a separate bounded Codex Worker",
    ),
    "self_approval_forbidden": (
        "An Agent must never approve its own plan or PreparedAction.",
        "Never call `agentops agent-plan approve`, `agentops approval decide`,",
    ),
    "prepared_action_boundary": (
        "must use an exact",
        "PreparedAction and human decision",
        "bound action",
        "hash and checkpoint",
    ),
    "credential_boundary": (
        "Never pass an API key as a command-line argument.",
        "without printing",
        "credentials",
        "Do not ingest `.env`, credential stores",
    ),
    "raw_model_data_boundary": (
        "omit raw prompts, raw responses,",
        "private transcripts",
        "full",
        "transcripts",
    ),
    "database_boundary": (
        "Do not read local SQLite directly.",
        "Use Agent Gateway CLI/API.",
    ),
    "memory_authority_boundary": (
        "Propose Memory only as a candidate for",
        "human review.",
        "Do not promote candidate Memory or Project Delta to canonical state.",
    ),
    "evidence_readback": (
        "Do not claim success from a CLI exit code alone",
        "read back Run and evidence",
    ),
}

REQUIRED_DOCUMENTED_COMMANDS = (
    "agentops status",
    "agentops doctor",
    "agentops worker preflight --adapter codex",
    "agentops runtime connectors",
    "agentops task pull",
    "agentops task claim",
    "agentops knowledge evidence-packet",
    "agentops operator loop-launch-packet",
    "agentops agent-plan create",
    "agentops agent-plan verify",
    "agentops run start",
    "agentops runtime-event record",
    "agentops toolcall record",
    "agentops artifact record",
    "agentops eval submit",
    "agentops audit emit",
    "agentops run heartbeat",
    "agentops run get",
    "agentops run evidence-graph",
    "agentops task get",
    "agentops workflow run-task --adapter codex --confirm-run",
)

REQUIRED_PARSER_PATHS = (
    ("status",),
    ("doctor",),
    ("worker", "preflight"),
    ("runtime", "connectors"),
    ("task", "pull"),
    ("task", "claim"),
    ("knowledge", "evidence-packet"),
    ("operator", "loop-launch-packet"),
    ("agent-plan", "create"),
    ("agent-plan", "verify"),
    ("run", "start"),
    ("runtime-event", "record"),
    ("toolcall", "record"),
    ("artifact", "record"),
    ("eval", "submit"),
    ("audit", "emit"),
    ("run", "heartbeat"),
    ("run", "get"),
    ("run", "evidence-graph"),
    ("task", "get"),
    ("workflow", "run-task"),
)


class ContractSmoke:
    def __init__(self) -> None:
        self.checks = 0
        self.failures: list[str] = []
        self.group_checks: dict[str, int] = defaultdict(int)
        self.group_failures: dict[str, list[str]] = defaultdict(list)

    def require(self, group: str, condition: bool, message: str) -> None:
        self.checks += 1
        self.group_checks[group] += 1
        if condition:
            return
        failure = f"{group}: {message}"
        self.failures.append(failure)
        self.group_failures[group].append(message)

    def contains_all(
        self,
        group: str,
        text: str,
        needles: tuple[str, ...],
        message: str,
    ) -> None:
        missing = [needle for needle in needles if needle not in text]
        self.require(
            group,
            not missing,
            f"{message}; missing markers: {', '.join(missing)}",
        )

    def group_results(self) -> dict[str, dict[str, Any]]:
        groups = sorted(set(self.group_checks) | set(self.group_failures))
        return {
            group: {
                "ok": not self.group_failures.get(group),
                "checks": self.group_checks.get(group, 0),
                "failures": self.group_failures.get(group, []),
            }
            for group in groups
        }


def read_allowlisted_files(smoke: ContractSmoke) -> dict[str, str]:
    content: dict[str, str] = {}
    for label, path in FILES.items():
        exists = path.is_file()
        smoke.require(
            "files",
            exists,
            f"required repository file missing: {path.relative_to(ROOT)}",
        )
        if not exists:
            continue
        try:
            content[label] = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            smoke.require(
                "files",
                False,
                f"required repository file is not readable UTF-8: {path.relative_to(ROOT)}",
            )
    return content


def load_json_object(
    smoke: ContractSmoke,
    group: str,
    source: str | None,
    label: str,
) -> dict[str, Any]:
    if source is None:
        return {}
    try:
        value = json.loads(source)
    except json.JSONDecodeError as exc:
        smoke.require(group, False, f"{label} is invalid JSON at line {exc.lineno}")
        return {}
    smoke.require(group, isinstance(value, dict), f"{label} must be a JSON object")
    return value if isinstance(value, dict) else {}


def normalized_shell_text(text: str) -> str:
    without_continuations = re.sub(r"\\\s*\n\s*", " ", text)
    return re.sub(r"\s+", " ", without_continuations).strip()


def literal_string_collection(node: ast.AST | None) -> set[str] | None:
    if not isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return None
    values: set[str] = set()
    for item in node.elts:
        if not isinstance(item, ast.Constant) or not isinstance(item.value, str):
            return None
        values.add(item.value)
    return values


def simple_assignment_target(node: ast.Assign | ast.AnnAssign) -> str | None:
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    if len(targets) != 1 or not isinstance(targets[0], ast.Name):
        return None
    return targets[0].id


def parser_contract(
    smoke: ContractSmoke,
    source: str | None,
) -> tuple[set[tuple[str, ...]], dict[tuple[tuple[str, ...], str], set[str]]]:
    if source is None:
        return set(), {}
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        smoke.require(
            "cli_parser",
            False,
            f"agentops CLI parser is invalid Python at line {exc.lineno}",
        )
        return set(), {}

    parser_entries: dict[str, tuple[str, str]] = {}
    subparser_parents: dict[str, str] = {}
    argument_calls: list[ast.Call] = []

    nodes = sorted(ast.walk(tree), key=lambda node: getattr(node, "lineno", -1))
    for node in nodes:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            target = simple_assignment_target(node)
            value = node.value
            if (
                target
                and isinstance(value, ast.Call)
                and isinstance(value.func, ast.Attribute)
                and isinstance(value.func.value, ast.Name)
            ):
                owner = value.func.value.id
                if value.func.attr == "add_subparsers":
                    subparser_parents[target] = owner
                elif (
                    value.func.attr == "add_parser"
                    and value.args
                    and isinstance(value.args[0], ast.Constant)
                    and isinstance(value.args[0].value, str)
                ):
                    parser_entries[target] = (owner, value.args[0].value)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_argument"
            and isinstance(node.func.value, ast.Name)
        ):
            argument_calls.append(node)

    resolving: set[str] = set()
    resolved: dict[str, tuple[str, ...]] = {}

    def resolve_parser_path(parser_name: str) -> tuple[str, ...]:
        if parser_name in resolved:
            return resolved[parser_name]
        if parser_name in resolving:
            return ()
        resolving.add(parser_name)
        entry = parser_entries.get(parser_name)
        if entry is None:
            path: tuple[str, ...] = ()
        else:
            subparser_name, command = entry
            parent_parser = subparser_parents.get(subparser_name)
            parent_path = resolve_parser_path(parent_parser) if parent_parser else ()
            path = (*parent_path, command)
        resolving.remove(parser_name)
        resolved[parser_name] = path
        return path

    paths = {
        resolve_parser_path(parser_name)
        for parser_name in parser_entries
        if resolve_parser_path(parser_name)
    }
    choices: dict[tuple[tuple[str, ...], str], set[str]] = {}
    for call in argument_calls:
        if not call.args:
            continue
        option_node = call.args[0]
        if not isinstance(option_node, ast.Constant) or not isinstance(option_node.value, str):
            continue
        choice_node = next(
            (keyword.value for keyword in call.keywords if keyword.arg == "choices"),
            None,
        )
        parsed_choices = literal_string_collection(choice_node)
        if parsed_choices is None:
            continue
        parser_name = call.func.value.id
        path = resolve_parser_path(parser_name)
        if path:
            choices[(path, option_node.value)] = parsed_choices
    return paths, choices


def check_manifest(
    smoke: ContractSmoke,
    manifest: dict[str, Any],
) -> None:
    smoke.require(
        "manifest",
        manifest.get("name") == EXPECTED_PLUGIN_NAME,
        f"manifest name must be {EXPECTED_PLUGIN_NAME!r}",
    )
    version = manifest.get("version")
    smoke.require(
        "manifest",
        version == EXPECTED_PLUGIN_VERSION,
        f"manifest version must be {EXPECTED_PLUGIN_VERSION!r}",
    )
    smoke.require(
        "manifest",
        isinstance(version, str)
        and re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version) is not None,
        "manifest version must be semantic x.y.z",
    )
    smoke.require(
        "manifest",
        manifest.get("skills") == "./skills/",
        "manifest skills must point to ./skills/",
    )

    skill_root = ROOT / "plugins" / "agentops-mis" / "skills"
    smoke.require(
        "manifest",
        skill_root.is_dir(),
        "manifest skills directory must exist",
    )
    interface = manifest.get("interface")
    smoke.require(
        "manifest",
        isinstance(interface, dict),
        "manifest interface must be an object",
    )
    if not isinstance(interface, dict):
        return
    smoke.require(
        "manifest",
        interface.get("displayName") == "AgentOps MIS",
        "interface displayName must be AgentOps MIS",
    )
    for field in ("shortDescription", "longDescription", "developerName", "category"):
        smoke.require(
            "manifest",
            isinstance(interface.get(field), str) and bool(interface[field].strip()),
            f"interface {field} must be a non-empty string",
        )
    capabilities = interface.get("capabilities")
    smoke.require(
        "manifest",
        isinstance(capabilities, list)
        and REQUIRED_INTERFACE_CAPABILITIES.issubset(set(capabilities)),
        "interface capabilities must include Interactive, Read, and Write",
    )
    default_prompt = interface.get("defaultPrompt")
    smoke.require(
        "manifest",
        isinstance(default_prompt, list)
        and bool(default_prompt)
        and all(isinstance(item, str) and item.strip() for item in default_prompt),
        "interface defaultPrompt must be a non-empty list of prompts",
    )


def check_marketplace(
    smoke: ContractSmoke,
    marketplace: dict[str, Any],
) -> None:
    smoke.require(
        "marketplace",
        marketplace.get("name") == EXPECTED_PLUGIN_NAME,
        f"marketplace name must be {EXPECTED_PLUGIN_NAME!r}",
    )
    interface = marketplace.get("interface")
    smoke.require(
        "marketplace",
        isinstance(interface, dict)
        and interface.get("displayName") == "AgentOps MIS",
        "marketplace interface must display AgentOps MIS",
    )
    plugins = marketplace.get("plugins")
    smoke.require(
        "marketplace",
        isinstance(plugins, list),
        "marketplace plugins must be a list",
    )
    if not isinstance(plugins, list):
        return
    matches = [
        item
        for item in plugins
        if isinstance(item, dict) and item.get("name") == EXPECTED_PLUGIN_NAME
    ]
    smoke.require(
        "marketplace",
        len(matches) == 1,
        "marketplace must contain exactly one agentops-mis entry",
    )
    if len(matches) != 1:
        return
    plugin = matches[0]
    source = plugin.get("source")
    smoke.require(
        "marketplace",
        isinstance(source, dict)
        and source.get("source") == "local"
        and source.get("path") == EXPECTED_MARKETPLACE_SOURCE,
        "marketplace source must be the repository-local plugin path",
    )
    smoke.require(
        "marketplace",
        (ROOT / EXPECTED_MARKETPLACE_SOURCE).is_dir(),
        "marketplace local source path must exist",
    )
    policy = plugin.get("policy")
    smoke.require(
        "marketplace",
        isinstance(policy, dict)
        and policy.get("installation") == "AVAILABLE"
        and policy.get("authentication") == "ON_USE",
        "marketplace policy must be AVAILABLE with ON_USE authentication",
    )


def check_skill(
    smoke: ContractSmoke,
    source: str | None,
    cli_map: str | None,
) -> None:
    if source is None:
        return
    smoke.require(
        "skill",
        source.startswith("---\n") and "\n---\n" in source[4:],
        "skill must have YAML frontmatter",
    )
    frontmatter_match = re.match(r"^---\n(?P<body>.*?)\n---\n", source, re.DOTALL)
    frontmatter = frontmatter_match.group("body") if frontmatter_match else ""
    smoke.require(
        "skill",
        re.search(r"^name:\s*agentops-mis\s*$", frontmatter, re.MULTILINE) is not None,
        "skill frontmatter name must be agentops-mis",
    )
    smoke.require(
        "skill",
        re.search(r"^description:\s*.+$", frontmatter, re.MULTILINE) is not None,
        "skill frontmatter must include a description",
    )

    for contract, markers in REQUIRED_SKILL_STATEMENTS.items():
        smoke.contains_all(
            "skill",
            source,
            markers,
            f"skill must enforce {contract}",
        )

    combined = normalized_shell_text(f"{source}\n{cli_map or ''}")
    for command in REQUIRED_DOCUMENTED_COMMANDS:
        smoke.require(
            "cli_commands",
            command in combined,
            f"plugin documentation must use real command: {command}",
        )


def check_cli_parser(
    smoke: ContractSmoke,
    source: str | None,
) -> None:
    paths, choices = parser_contract(smoke, source)
    for path in REQUIRED_PARSER_PATHS:
        smoke.require(
            "cli_parser",
            path in paths,
            f"agentops CLI parser must expose {' '.join(path)}",
        )

    for path in (
        ("operator", "loop-launch-packet"),
        ("runtime-event", "record"),
    ):
        adapter_choices = choices.get((path, "--adapter"), set())
        smoke.require(
            "codex_adapter",
            "codex" in adapter_choices,
            f"{' '.join(path)} --adapter choices must include codex",
        )


def check_offline_boundaries(smoke: ContractSmoke) -> None:
    source = Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".", 1)[0])

    forbidden_imports = {
        "http",
        "os",
        "requests",
        "socket",
        "sqlite3",
        "subprocess",
        "urllib",
    }
    smoke.require(
        "offline_boundary",
        not (imported_roots & forbidden_imports),
        "smoke must not import environment, process, database, or network modules",
    )
    smoke.require(
        "offline_boundary",
        set(FILES)
        == {"manifest", "marketplace", "skill", "cli_map", "cli_parser"},
        "smoke may read only the repository contract allowlist",
    )
    smoke.require(
        "offline_boundary",
        all(path.is_relative_to(ROOT) for path in FILES.values()),
        "all inspected files must stay inside the repository",
    )


def main() -> int:
    smoke = ContractSmoke()
    content = read_allowlisted_files(smoke)
    manifest = load_json_object(
        smoke,
        "manifest",
        content.get("manifest"),
        "plugin manifest",
    )
    marketplace = load_json_object(
        smoke,
        "marketplace",
        content.get("marketplace"),
        "marketplace manifest",
    )

    check_manifest(smoke, manifest)
    check_marketplace(smoke, marketplace)
    check_skill(smoke, content.get("skill"), content.get("cli_map"))
    check_cli_parser(smoke, content.get("cli_parser"))
    check_offline_boundaries(smoke)

    result = {
        "ok": not smoke.failures,
        "script": str(Path(__file__).relative_to(ROOT)),
        "mode": "offline_static",
        "checks": smoke.checks,
        "groups": smoke.group_results(),
        "files_checked": [
            str(path.relative_to(ROOT))
            for path in FILES.values()
            if path.is_file()
        ],
        "boundaries": {
            "environment_read": False,
            "credential_read": False,
            "database_read": False,
            "network_access": False,
        },
        "failures": smoke.failures,
    }
    print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
