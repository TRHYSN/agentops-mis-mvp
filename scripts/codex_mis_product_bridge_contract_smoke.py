#!/usr/bin/env python3
"""Offline contract smoke for the Codex to AgentOps MIS product bridge spec."""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FILES = {
    "spec": ROOT / "docs" / "CODEX_MIS_PRODUCT_BRIDGE_SPEC.md",
    "server": ROOT / "server.py",
    "cli": ROOT / "agentops_mis_cli" / "agentops.py",
    "worker": ROOT / "agentops_mis_cli" / "worker.py",
    "codex_runtime": ROOT / "agentops_mis_cli" / "codex_runtime.py",
    "capabilities": ROOT / "agentops_mis_runtime" / "capabilities.py",
    "connectors": ROOT / "agentops_mis_runtime" / "connectors.py",
    "pyproject": ROOT / "pyproject.toml",
    "app": ROOT / "ui" / "start-building-app" / "src" / "app" / "App.tsx",
    "codex_ui": (
        ROOT
        / "ui"
        / "start-building-app"
        / "src"
        / "app"
        / "components"
        / "pages"
        / "CodexConnection.tsx"
    ),
}

EXPECTED_GATEWAY_SCOPES = {
    "agents:write",
    "agents:heartbeat",
    "agent_plans:read",
    "agent_plans:write",
    "plan_evidence:read",
    "plan_evidence:write",
    "knowledge:read",
    "knowledge:write",
    "tasks:create",
    "tasks:read",
    "tasks:claim",
    "runs:write",
    "runtime_events:write",
    "toolcalls:write",
    "artifacts:write",
    "approvals:request",
    "memories:propose",
    "evaluations:submit",
    "audit:write",
}

RECOMMENDED_CODEX_SCOPES = {
    "agents:heartbeat",
    "knowledge:read",
    "agent_plans:read",
    "agent_plans:write",
    "plan_evidence:read",
    "plan_evidence:write",
    "tasks:read",
    "tasks:claim",
    "runs:write",
    "runtime_events:write",
    "toolcalls:write",
    "artifacts:write",
    "approvals:request",
    "memories:propose",
    "evaluations:submit",
    "audit:write",
}

REQUIRED_SPEC_HEADINGS = [
    "## Product Positioning",
    "## Current Implementation Baseline",
    "## Installation, Login, And Connection Flow",
    "## Authority Boundaries",
    "## Context Packet Contract",
    "## CLI, API, And MCP Tool Surface",
    "## Permission Scopes",
    "## Approval And Execution Policy",
    "## Project Delta Writeback Contract",
    "## Data Minimization And Secret Handling",
    "## Control Panel Acceptance",
    "## Phased Delivery Slices",
    "## Acceptance Contract",
]

REQUIRED_CURRENT_API_ROUTES = [
    "/api/agent-gateway/status",
    "/api/agent-gateway/tasks/pull",
    "/api/agent-gateway/runs/start",
    "/api/agent-gateway/runtime-events",
    "/api/agent-gateway/tool-calls",
    "/api/agent-gateway/artifacts",
    "/api/agent-gateway/knowledge/evidence-packet",
    "/api/agent-gateway/agent-plans",
    "/api/agent-gateway/plan-evidence-manifests",
    "/api/agent-gateway/approvals/request",
    "/api/agent-gateway/prepared-actions",
    "/api/agent-gateway/memories/propose",
    "/api/agent-gateway/evaluations/submit",
    "/api/agent-gateway/audit",
    "/api/operator/loop-launch-packet",
]

SECRET_PATTERNS = [
    re.compile(r"\b(?:ntn|ghp|github_pat|agtok|agtsess|sk-proj)_[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"https?://[^/\s:@]+:[^/\s@]+@"),
]


class ContractSmoke:
    def __init__(self) -> None:
        self.checks = 0
        self.failures: list[str] = []

    def require(self, condition: bool, message: str) -> None:
        self.checks += 1
        if not condition:
            self.failures.append(message)

    def contains(self, text: str, needle: str, label: str) -> None:
        self.require(needle in text, f"{label} missing: {needle}")

    def excludes(self, text: str, needle: str, label: str) -> None:
        self.require(needle not in text, f"{label} must omit: {needle}")


def read_allowlisted_files(smoke: ContractSmoke) -> dict[str, str]:
    content: dict[str, str] = {}
    for label, path in FILES.items():
        smoke.require(path.is_file(), f"allowlisted file missing: {path.relative_to(ROOT)}")
        if path.is_file():
            content[label] = path.read_text(encoding="utf-8")
    return content


def literal_string_set(source: str, assignment_name: str) -> set[str] | None:
    tree = ast.parse(source)
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(isinstance(target, ast.Name) and target.id == assignment_name for target in targets):
            continue
        value = node.value
        if not isinstance(value, (ast.Set, ast.List, ast.Tuple)):
            return None
        result: set[str] = set()
        for item in value.elts:
            if not isinstance(item, ast.Constant) or not isinstance(item.value, str):
                return None
            result.add(item.value)
        return result
    return None


def function_source(source: str, function_name: str) -> str:
    pattern = re.compile(
        rf"^def {re.escape(function_name)}\(.*?(?=^def |\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(source)
    return match.group(0) if match else ""


def phase_commands(source: str) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    pattern = re.compile(
        r'\{\s*"phase":\s*"([^"]+)",\s*"command":\s*"([^"]+)"',
        re.MULTILINE,
    )
    for phase, command in pattern.findall(source):
        found.setdefault(phase, []).append(command)
    return found


def check_spec(smoke: ContractSmoke, source: dict[str, str]) -> None:
    spec = source["spec"]

    for heading in REQUIRED_SPEC_HEADINGS:
        smoke.contains(spec, heading, "spec heading")

    for status in ("**CURRENT**", "**GAP**", "**PROPOSED**"):
        smoke.contains(spec, status, "status vocabulary")

    authority_statements = [
        "Codex is an execution client, not an authority system.",
        "GitHub is authoritative for code, branch, commit, diff, PR, and CI facts.",
        "AgentOps MIS is authoritative for execution, approval, evidence, and audit",
        "Notion is authoritative only for reviewed project-memory records",
        "Project Delta is the durable change relative to existing project state, not",
        "It is always candidate-first when",
        "Codex can request approval and inspect status. It cannot approve its own plan",
    ]
    for statement in authority_statements:
        smoke.contains(spec, statement, "authority boundary")

    product_gap_statements = [
        "| Product MCP bridge | GAP |",
        "| One-click customer connection | GAP |",
        "| Dedicated Context Packet | GAP |",
        "| First-class Project Delta writeback | GAP |",
        "| Connection lifecycle receipt | GAP |",
        "**PROPOSED:** `GET /api/agent-gateway/context-packet?task_id=<id>`",
        "**PROPOSED:** `agentops connect codex`, `agentops disconnect codex`",
        "**PROPOSED:** `agentops project-delta propose` /",
        "MCP is not implemented at the inspected baseline.",
    ]
    for statement in product_gap_statements:
        smoke.contains(spec, statement, "gap/proposal boundary")

    onboarding_statements = [
        "Local loopback Codex execution must omit `--use-session`",
        "`--use-session` is reserved for remote/enrolled workers",
        "Adding `--use-session` to the un-enrolled local",
        "remote/enrolled path exchanges its enrollment credential for a short-lived",
    ]
    for statement in onboarding_statements:
        smoke.contains(spec, statement, "onboarding authority")

    runtime_isolation_statements = [
        "`rtc_codex_local`",
        "`adapters.codex`",
        "Codex run-start admission depends on the Codex current-code gate",
        "It must not depend on Hermes or OpenClaw readiness.",
        "Hermes/OpenClaw adapter availability is not an input to the Codex decision.",
        "A blocked unrelated adapter cannot block an otherwise valid Codex start.",
    ]
    for statement in runtime_isolation_statements:
        smoke.contains(spec, statement, "Codex runtime isolation")

    minimization_statements = [
        "credential_omitted",
        "raw_prompt_omitted",
        "raw_response_omitted",
        "private_transcript_omitted",
        "raw patch/source content in MIS",
        "The target product stores long-lived client credentials in the OS keychain.",
        "does not inspect environment variables,",
        "credentials, local databases, runtime logs, generated artifacts, or private",
    ]
    for statement in minimization_statements:
        smoke.contains(spec, statement, "data minimization")

    for scope in RECOMMENDED_CODEX_SCOPES:
        smoke.contains(spec, scope, "recommended Codex scope")
    smoke.contains(
        spec,
        "There is intentionally no `approvals:approve` Agent Gateway scope.",
        "self-approval scope boundary",
    )

    for pattern in SECRET_PATTERNS:
        smoke.require(pattern.search(spec) is None, f"spec contains a secret-shaped literal: {pattern.pattern}")
    smoke.excludes(spec, "/Users/", "portable spec")


def check_source_contract(smoke: ContractSmoke, source: dict[str, str]) -> None:
    server = source["server"]
    cli = source["cli"]
    worker = source["worker"]
    runtime = source["codex_runtime"]
    capabilities = source["capabilities"]
    connectors = source["connectors"]
    app = source["app"]
    codex_ui = source["codex_ui"]
    pyproject = source["pyproject"]

    for route in REQUIRED_CURRENT_API_ROUTES:
        smoke.contains(server, route, "current Agent Gateway route")

    smoke.excludes(
        server,
        'path == "/api/agent-gateway/context-packet"',
        "proposed Context Packet route",
    )

    current_scopes = literal_string_set(server, "VALID_AGENT_GATEWAY_SCOPES")
    smoke.require(current_scopes is not None, "VALID_AGENT_GATEWAY_SCOPES must be a static literal")
    if current_scopes is not None:
        smoke.require(
            current_scopes == EXPECTED_GATEWAY_SCOPES,
            "Agent Gateway scope vocabulary drifted; update the bridge spec intentionally",
        )
        smoke.require(
            RECOMMENDED_CODEX_SCOPES.issubset(current_scopes),
            "recommended Codex profile contains an unknown scope",
        )
        smoke.require(
            "approvals:approve" not in current_scopes,
            "Codex/Gateway scope vocabulary must not include approval decision authority",
        )

    cli_markers = [
        'add_parser("login"',
        'add_parser("status"',
        'add_parser("register"',
        'add_parser("heartbeat"',
        'add_parser("pull"',
        'add_parser("claim"',
        'add_parser("start"',
        'add_parser("record"',
        'add_parser("request"',
        'add_parser("propose"',
        'add_parser("submit"',
        'add_parser("emit"',
        'add_parser("evidence-packet"',
        'add_parser("codex-workspace-write"',
    ]
    for marker in cli_markers:
        smoke.contains(cli, marker, "current CLI")

    smoke.contains(pyproject, 'agentops = "agentops_mis_cli.cli:main"', "installable CLI")
    smoke.contains(pyproject, 'agentops-worker = "agentops_mis_cli.worker:main"', "installable worker")

    for marker in [
        '"--ephemeral"',
        '"--ignore-user-config"',
        '"--strict-config"',
        '"--sandbox"',
        '"read-only"',
        "READ_ONLY_PROHIBITED_ITEM_TYPES",
        "WORKSPACE_WRITE_PROHIBITED_ITEM_TYPES",
    ]:
        smoke.contains(runtime, marker, "Codex bounded runtime")
    smoke.contains(worker, "execute_codex_read_only", "Codex worker")
    smoke.contains(worker, "execute_codex_workspace_write", "Codex worker")
    smoke.contains(worker, "managed_detached_git_worktree", "Codex workspace isolation")

    for text, label in [
        ("rtc_codex_local", "Codex connector registry"),
        ('"provider": "codex"', "Codex connector provider"),
        ('"trust_status": "trusted"', "Codex connector trust"),
    ]:
        smoke.contains(connectors, text, label)
    smoke.contains(capabilities, 'if adapter == "codex":', "Codex connector mapping")
    smoke.contains(capabilities, 'return "rtc_codex_local"', "Codex connector mapping")
    smoke.contains(capabilities, '"structured_runtime_events"', "Codex observation level")

    readiness_markers = [
        "codex_binary_attestation",
        'adapters["codex"]',
        '"connector_id": codex_trust.get("connector_id")',
        '"workspace_write_ready"',
        '"raw_binary_path_omitted": True',
        '"target_resource": "local://codex/read-only"',
    ]
    for marker in readiness_markers:
        smoke.contains(server, marker, "Codex readiness contract")

    commands = phase_commands(server)
    local_commands = [
        command
        for command in commands.get("run_read_only", [])
        if "--adapter codex" in command
    ]
    remote_commands = [
        command
        for command in commands.get("run_remote_scoped", [])
        if "--adapter codex" in command
    ]
    smoke.require(len(local_commands) == 1, f"expected one local Codex command, got {local_commands}")
    smoke.require(len(remote_commands) == 1, f"expected one remote Codex command, got {remote_commands}")
    if local_commands:
        smoke.require(
            "--use-session" not in local_commands[0],
            "local loopback Codex command must omit --use-session",
        )
        smoke.require(
            "--confirm-run" in local_commands[0],
            "local loopback Codex command must retain explicit confirmation",
        )
    if remote_commands:
        smoke.require(
            "--use-session" in remote_commands[0],
            "remote/enrolled Codex command must use a short-lived session",
        )

    run_start_gate = function_source(
        server,
        "agent_gateway_run_start_loop_supervision_readback",
    )
    smoke.require(bool(run_start_gate), "Codex run-start supervision function missing")
    smoke.contains(run_start_gate, 'if runtime_type == "codex":', "Codex run-start branch")
    smoke.contains(
        run_start_gate,
        'status = "ready_to_confirm" if summary.get("current_code_ok") is True else "blocked"',
        "Codex current-code gate",
    )
    smoke.contains(
        run_start_gate,
        'can_confirm = summary.get("current_code_ok") is True',
        "Codex current-code gate",
    )
    codex_branch = ""
    if 'if runtime_type == "codex":' in run_start_gate:
        codex_branch = run_start_gate.split('if runtime_type == "codex":', 1)[1].split("    else:", 1)[0]
    smoke.excludes(codex_branch, "items", "Codex branch unrelated adapter aggregation")
    smoke.excludes(codex_branch.lower(), "hermes", "Codex branch Hermes readiness")
    smoke.excludes(codex_branch.lower(), "openclaw", "Codex branch OpenClaw readiness")

    smoke.contains(app, 'path="/admin/codex"', "Codex control-panel route")
    smoke.contains(codex_ui, "loadWorkerAdapterReadiness", "Codex live readiness source")
    smoke.contains(codex_ui, "data.adapterReadiness.data.adapters.codex", "Codex-only readiness")
    smoke.contains(codex_ui, "workspace_write_ready", "Codex workspace-write attestation UI")
    smoke.contains(
        codex_ui,
        "no other adapter status is used as a substitute",
        "Codex readiness substitution boundary",
    )


def main() -> int:
    smoke = ContractSmoke()
    source = read_allowlisted_files(smoke)
    if len(source) == len(FILES):
        check_spec(smoke, source)
        check_source_contract(smoke, source)

    result = {
        "ok": not smoke.failures,
        "operation": "codex_mis_product_bridge_contract_smoke",
        "checks": smoke.checks,
        "failures": smoke.failures,
        "network_used": False,
        "database_read": False,
        "credential_read": False,
        "environment_read": False,
        "allowlisted_files": [
            str(path.relative_to(ROOT))
            for path in FILES.values()
        ],
    }
    print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
