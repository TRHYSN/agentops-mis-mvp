#!/usr/bin/env python3
"""Accept the installed dependency-free CLI wheel on Windows."""
from __future__ import annotations

import argparse
import configparser
import email
import importlib.metadata
import json
import os
import subprocess
import sys
import tempfile
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


DIST_NAME = "agentops-mis-cli"
TASK_ID = "tsk_windows_offline_mock"
PLAN_ID = "plan_windows_offline_mock"
RUN_ID = "run_windows_offline_mock"
EXPECTED_SCRIPTS = {
    "agentops": "agentops_mis_cli.cli:main",
    "agentops-worker": "agentops_mis_cli.worker:main",
}


class SmokeGateway(BaseHTTPRequestHandler):
    requests: list[dict[str, object]] = []

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        self._record("GET", path)
        if path == "/api/agent-gateway/status":
            self._send_json(
                200,
                {
                    "auth": {
                        "mode": "local_dev",
                        "agent_id": "agt_windows_acceptance",
                        "workspace_id": "local-demo",
                        "scopes": [],
                        "token_omitted": True,
                    }
                },
            )
            return
        if path == "/api/agent-gateway/tasks/pull":
            self._send_json(
                200,
                {
                    "tasks": [
                        {
                            "task_id": TASK_ID,
                            "title": "Windows CI offline mock worker contract",
                            "description": "Exercise the dependency-free installed worker without external runtime execution.",
                            "acceptance_criteria": "Record bounded mock evidence and write isolated worker state.",
                            "risk_level": "low",
                            "status": "planned",
                            "intake": {"plan_id": PLAN_ID, "plan_verified": True},
                        }
                    ],
                    "intake": {"blocked": 0},
                },
            )
            return
        if path == "/api/agent-gateway/knowledge/context-packet":
            self._send_json(
                200,
                {
                    "operation": "knowledge_retrieval_evidence_packet",
                    "status": "ready",
                    "query_hash": "sha256:windows-offline-query",
                    "task_context": {
                        "task_id": TASK_ID,
                        "task_found": True,
                        "query_source": "task_id",
                        "source_fields": ["title", "acceptance_criteria"],
                        "task_text_omitted": True,
                        "token_omitted": True,
                    },
                    "primary_search": {
                        "results": [
                            {
                                "retrieval_id": "ret_windows_offline_mock",
                                "doc_id": "doc_project_spec",
                                "chunk_id": "chunk_project_spec",
                                "path": "PROJECT_SPEC.md",
                                "source_hash": "sha256:windows-offline-source",
                                "rank": 1,
                            }
                        ]
                    },
                    "metrics": {"recall_at_5": 1.0, "mrr": 1.0},
                    "raw_content_omitted": True,
                    "token_omitted": True,
                },
            )
            return
        if path == f"/api/agent-gateway/agent-plans/{PLAN_ID}/verify":
            self._send_json(
                200,
                {
                    "agent_plan": {
                        "plan_id": PLAN_ID,
                        "task_id": TASK_ID,
                        "agent_id": "agt_windows_acceptance",
                        "status": "submitted",
                        "plan_hash": "sha256:windows-offline-plan",
                        "verification_result_hash": "sha256:windows-offline-verification",
                    },
                    "verification": {"pass": True, "failures": []},
                },
            )
            return
        self._send_json(404, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        content_length = int(self.headers.get("Content-Length") or "0")
        raw = self.rfile.read(content_length) if content_length else b""
        payload = json.loads(raw.decode("utf-8")) if raw else {}
        require(isinstance(payload, dict), f"Gateway request payload must be an object: {path}")
        self._record("POST", path, payload)

        if path in {"/api/agent-gateway/register", "/api/agent-gateway/heartbeat"}:
            self._send_json(200, {"ok": True})
            return
        if path == f"/api/agent-gateway/tasks/{TASK_ID}/claim":
            self._send_json(200, {"ok": True, "task": {"task_id": TASK_ID, "status": "claimed"}})
            return
        if path == "/api/agent-gateway/runs/start":
            self._send_json(200, {"run": {"run_id": RUN_ID, "task_id": TASK_ID, "status": "running"}})
            return
        if path == "/api/agent-gateway/runtime-events":
            self._send_json(200, {"runtime_event": {"runtime_event_id": "rte_windows_offline_mock"}})
            return
        if path == "/api/agent-gateway/tool-calls":
            self._send_json(200, {"tool_call": {"tool_call_id": "tool_windows_offline_mock"}})
            return
        if path == f"/api/agent-gateway/runs/{RUN_ID}/heartbeat":
            self._send_json(200, {"ok": True, "run": {"run_id": RUN_ID, "status": payload.get("status")}})
            return
        if path == "/api/agent-gateway/evaluations/submit":
            self._send_json(200, {"evaluation": {"evaluation_id": "eval_windows_offline_mock"}})
            return
        if path == "/api/agent-gateway/artifacts":
            self._send_json(200, {"artifact": {"artifact_id": "art_windows_offline_mock"}})
            return
        if path == "/api/agent-gateway/memories/propose":
            self._send_json(200, {"memory": {"memory_id": "mem_windows_offline_mock"}})
            return
        if path == "/api/agent-gateway/audit":
            self._send_json(200, {"audit_id": "audit_windows_offline_mock", "emitted": True})
            return
        if path == "/api/agent-gateway/plan-evidence-manifests":
            self._send_json(
                200,
                {
                    "manifest": {"manifest_id": "pem_windows_offline_mock", "status": "verified"},
                    "verification": {"pass": True, "failures": []},
                },
            )
            return
        self._send_json(404, {"error": "not_found"})

    def _record(self, method: str, path: str, payload: dict[str, object] | None = None) -> None:
        credential_headers_present = any(
            self.headers.get(name)
            for name in ("Authorization", "X-AgentOps-Api-Key")
        )
        self.__class__.requests.append(
            {
                "method": method,
                "path": path,
                "payload": payload or {},
                "credential_headers_present": credential_headers_present,
            }
        )

    def _send_json(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def run(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    expected_returncode: int = 0,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    require(
        result.returncode == expected_returncode,
        "command failed: "
        + subprocess.list2cmdline(command)
        + f"\nexpected={expected_returncode} actual={result.returncode}"
        + f"\nstdout={result.stdout[-1600:]}\nstderr={result.stderr[-1600:]}",
    )
    return result


def json_stdout(result: subprocess.CompletedProcess[str], label: str) -> dict[str, object]:
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"{label} did not emit JSON: {result.stdout[-1600:]}") from exc
    require(isinstance(payload, dict), f"{label} JSON must be an object")
    return payload


def require_request_order(expected: list[tuple[str, str]]) -> None:
    position = -1
    for method, path in expected:
        for index in range(position + 1, len(SmokeGateway.requests)):
            request = SmokeGateway.requests[index]
            if request.get("method") == method and request.get("path") == path:
                position = index
                break
        else:
            observed = [(request.get("method"), request.get("path")) for request in SmokeGateway.requests]
            raise AssertionError(f"missing ordered Gateway request {(method, path)}: {observed}")


def inspect_wheel(wheel_path: Path) -> dict[str, object]:
    require(wheel_path.is_file(), f"wheel does not exist: {wheel_path}")
    require(wheel_path.name.endswith("-py3-none-any.whl"), "wheel must be platform-independent")
    with zipfile.ZipFile(wheel_path) as archive:
        names = archive.namelist()
        metadata_names = [name for name in names if name.endswith(".dist-info/METADATA")]
        wheel_names = [name for name in names if name.endswith(".dist-info/WHEEL")]
        entry_point_names = [name for name in names if name.endswith(".dist-info/entry_points.txt")]
        require(len(metadata_names) == 1, "wheel must contain one METADATA file")
        require(len(wheel_names) == 1, "wheel must contain one WHEEL file")
        require(len(entry_point_names) == 1, "wheel must contain one entry_points.txt file")
        metadata = email.message_from_bytes(archive.read(metadata_names[0]))
        wheel_metadata = archive.read(wheel_names[0]).decode("utf-8")
        entry_points_text = archive.read(entry_point_names[0]).decode("utf-8")

    require(not metadata.get_all("Requires-Dist", []), "wheel declares runtime dependencies")
    require("Root-Is-Purelib: true" in wheel_metadata, "wheel is not marked pure Python")
    require("Tag: py3-none-any" in wheel_metadata, "wheel has an unexpected platform tag")
    entry_points = configparser.ConfigParser()
    entry_points.read_string(entry_points_text)
    require(entry_points.has_section("console_scripts"), "console_scripts entry points are missing")
    for name, target in EXPECTED_SCRIPTS.items():
        require(entry_points.get("console_scripts", name, fallback="") == target, f"{name} entry point drifted")
    return {
        "filename": wheel_path.name,
        "file_count": len(names),
        "runtime_dependency_count": 0,
        "tag": "py3-none-any",
    }


def inspect_install(source_root: Path) -> dict[str, object]:
    distribution = importlib.metadata.distribution(DIST_NAME)
    require(not distribution.requires, "installed distribution declares runtime dependencies")
    installed_root = Path(distribution.locate_file("")).resolve()
    require(
        installed_root != source_root and source_root not in installed_root.parents,
        f"acceptance imported the source checkout instead of the installed wheel: {installed_root}",
    )
    scripts = {
        entry.name: entry.value
        for entry in distribution.entry_points
        if entry.group == "console_scripts" and entry.name in EXPECTED_SCRIPTS
    }
    require(scripts == EXPECTED_SCRIPTS, f"installed console scripts drifted: {scripts}")
    return {
        "distribution": distribution.metadata["Name"],
        "version": distribution.version,
        "installed_root": str(installed_root),
        "runtime_dependency_count": 0,
    }


def executable(name: str) -> Path:
    path = Path(sys.executable).resolve().parent / f"{name}.exe"
    require(path.is_file(), f"installed console script is missing: {path}")
    return path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheel", type=Path, required=True)
    args = parser.parse_args()

    require(os.name == "nt", "Windows acceptance must run on Windows")
    source_root = Path(__file__).resolve().parents[1]
    wheel_summary = inspect_wheel(args.wheel.resolve())
    install_summary = inspect_install(source_root)
    agentops = executable("agentops")
    worker = executable("agentops-worker")

    gateway = ThreadingHTTPServer(("127.0.0.1", 0), SmokeGateway)
    thread = threading.Thread(target=gateway.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{gateway.server_port}"

    try:
        with tempfile.TemporaryDirectory(prefix="agentops-windows-acceptance-") as temporary:
            temp_root = Path(temporary)
            env = os.environ.copy()
            env.pop("AGENTOPS_API_KEY", None)
            env.pop("PYTHONPATH", None)
            env["AGENTOPS_CONFIG"] = str(temp_root / "config.json")
            env["AGENTOPS_HOME"] = str(temp_root / "home")
            env["AGENTOPS_LOCAL_HOME"] = str(temp_root / "local")
            env["AGENTOPS_WORKER_RUNTIME_DIR"] = str(temp_root / "runtime")

            cli_help = run([str(agentops), "--help"], cwd=temp_root, env=env)
            require("AgentOps MIS local Agent Gateway CLI" in cli_help.stdout, "CLI help contract drifted")

            worker_help = run([str(worker), "--help"], cwd=temp_root, env=env)
            require("Run an AgentOps MIS worker loop" in worker_help.stdout, "worker help contract drifted")

            login = run(
                [
                    str(agentops),
                    "login",
                    "--base-url",
                    base_url,
                    "--workspace-id",
                    "local-demo",
                    "--agent-id",
                    "agt_windows_acceptance",
                ],
                cwd=temp_root,
                env=env,
            )
            login_payload = json_stdout(login, "agentops login")
            require(login_payload.get("ok") is True, "agentops login failed")
            require(login_payload.get("has_api_key") is False, "login unexpectedly persisted an API key")
            require(Path(env["AGENTOPS_CONFIG"]).is_file(), "login did not create the isolated config")

            cli_preflight = run(
                [
                    str(agentops),
                    "--base-url",
                    base_url,
                    "worker",
                    "preflight",
                    "--adapter",
                    "mock",
                    "--agent-id",
                    "agt_windows_acceptance",
                ],
                cwd=temp_root,
                env=env,
            )
            cli_preflight_payload = json_stdout(cli_preflight, "agentops worker preflight")
            require(cli_preflight_payload.get("ok") is True, "CLI worker preflight failed")
            require(cli_preflight_payload.get("provider") == "agentops-worker", "CLI worker provider drifted")
            require(cli_preflight_payload.get("live_execution_performed") is False, "CLI preflight executed work")

            worker_preflight = run(
                [
                    str(worker),
                    "preflight",
                    "--adapter",
                    "mock",
                    "--base-url",
                    base_url,
                    "--workspace-id",
                    "local-demo",
                    "--agent-id",
                    "agt_windows_acceptance",
                ],
                cwd=temp_root,
                env=env,
            )
            worker_preflight_payload = json_stdout(worker_preflight, "agentops-worker preflight")
            require(worker_preflight_payload.get("ok") is True, "worker preflight failed")
            require(worker_preflight_payload.get("live_execution_performed") is False, "worker preflight executed work")
            require(worker_preflight_payload.get("token_omitted") is True, "worker preflight token gate drifted")

            state_path = temp_root / "state" / "mock-worker.json"
            worker_once = run(
                [
                    str(worker),
                    "--once",
                    "--adapter",
                    "mock",
                    "--base-url",
                    base_url,
                    "--workspace-id",
                    "local-demo",
                    "--agent-id",
                    "agt_windows_acceptance",
                    "--task-id",
                    TASK_ID,
                    "--state-path",
                    str(state_path),
                    "--poll-interval",
                    "0",
                ],
                cwd=temp_root,
                env=env,
            )
            worker_once_payload = json_stdout(worker_once, "agentops-worker --once")
            worker_results = worker_once_payload.get("results") or []
            require(worker_once_payload.get("ok") is True, "one-shot mock worker failed")
            require(worker_once_payload.get("processed") == 1, "one-shot mock worker did not process one task")
            require(len(worker_results) == 1 and isinstance(worker_results[0], dict), "one-shot result is missing")
            require(worker_results[0].get("task_id") == TASK_ID, "one-shot result task drifted")
            require(worker_results[0].get("run_id") == RUN_ID, "one-shot result run drifted")
            require(worker_results[0].get("ok") is True, "one-shot result failed")
            require(worker_results[0].get("plan_evidence_pass") is True, "one-shot manifest did not verify")

            require(state_path.is_file(), "one-shot worker did not write isolated state")
            state_payload = json.loads(state_path.read_text(encoding="utf-8"))
            require(state_payload.get("status") == "completed", "worker state did not reach completed")
            require(state_payload.get("processed") == 1, "worker state processed count drifted")
            require(state_payload.get("last_task_id") == TASK_ID, "worker state task id drifted")
            require(state_payload.get("last_run_id") == RUN_ID, "worker state run id drifted")

            require_request_order(
                [
                    ("GET", "/api/agent-gateway/tasks/pull"),
                    ("POST", f"/api/agent-gateway/tasks/{TASK_ID}/claim"),
                    ("POST", "/api/agent-gateway/runs/start"),
                    ("POST", "/api/agent-gateway/runtime-events"),
                    ("POST", "/api/agent-gateway/tool-calls"),
                    ("POST", f"/api/agent-gateway/runs/{RUN_ID}/heartbeat"),
                    ("POST", "/api/agent-gateway/evaluations/submit"),
                    ("POST", "/api/agent-gateway/audit"),
                    ("POST", "/api/agent-gateway/plan-evidence-manifests"),
                ]
            )
            request_payloads = {
                str(request.get("path")): request.get("payload") or {}
                for request in SmokeGateway.requests
                if request.get("method") == "POST"
            }
            require(request_payloads[f"/api/agent-gateway/tasks/{TASK_ID}/claim"].get("runtime_type") == "mock", "claim was not mock")
            require(request_payloads["/api/agent-gateway/runs/start"].get("runtime_type") == "mock", "run was not mock")
            require(request_payloads["/api/agent-gateway/tool-calls"].get("tool_name") == "agent_worker.mock", "tool evidence drifted")
            require(request_payloads["/api/agent-gateway/evaluations/submit"].get("pass_fail") == "pass", "evaluation did not pass")
            require(request_payloads["/api/agent-gateway/audit"].get("action") == "agent_worker.task_processed", "audit evidence drifted")
            require(
                not any(request.get("credential_headers_present") for request in SmokeGateway.requests),
                "offline mock worker sent credential headers",
            )
            rendered_requests = json.dumps(SmokeGateway.requests, sort_keys=True)
            require(
                not any(marker in rendered_requests for marker in ("agtok_", "agtsess_", "sk-", "ntn_")),
                "offline mock protocol captured token-like content",
            )

            service_template = run(
                [
                    str(worker),
                    "service-template",
                    "--manager",
                    "windows-task",
                    "--adapter",
                    "mock",
                    "--base-url",
                    base_url,
                    "--workspace-id",
                    "local-demo",
                    "--agent-id",
                    "agt_windows_acceptance",
                    "--working-directory",
                    str(temp_root),
                ],
                cwd=temp_root,
                env=env,
            )
            for marker in ("<Task", "LogonTrigger", "RestartOnFailure", "powershell.exe", "agentops-worker"):
                require(marker in service_template.stdout, f"Windows service template is missing {marker}")
            require("AGENTOPS_API_KEY" not in service_template.stdout, "Windows service template contains an API key")

            service_path = temp_root / "services" / "agentops-worker.xml"
            service_install = run(
                [
                    str(worker),
                    "service-install",
                    "--manager",
                    "windows-task",
                    "--adapter",
                    "mock",
                    "--base-url",
                    base_url,
                    "--workspace-id",
                    "local-demo",
                    "--agent-id",
                    "agt_windows_acceptance",
                    "--working-directory",
                    str(temp_root),
                    "--service-path",
                    str(service_path),
                    "--confirm-install",
                ],
                cwd=temp_root,
                env=env,
            )
            service_install_payload = json_stdout(service_install, "agentops-worker service-install")
            require(service_install_payload.get("ok") is True, "isolated Windows service install failed")
            require(service_install_payload.get("wrote") is True, "isolated Windows service XML was not written")
            require(service_install_payload.get("confirmed_install") is True, "service file write was not explicitly confirmed")
            require(service_install_payload.get("service_path") == str(service_path), "service install escaped the isolated path")
            require(service_install_payload.get("live_execution_performed") is False, "service install executed a live runtime")
            require(service_install_payload.get("service_loaded") is False, "service install mutated Task Scheduler")
            require(service_path.is_file(), "isolated Windows service XML is missing")
            installed_xml = service_path.read_text(encoding="utf-8")
            require("<Task" in installed_xml and "agentops-worker" in installed_xml, "installed service XML drifted")
            require("AGENTOPS_API_KEY" not in installed_xml, "installed service XML contains an API key")

            host = run([str(agentops), "host", "--help"], cwd=temp_root, env=env, expected_returncode=2)
            host_payload = json_stdout(host, "agentops host")
            require(host_payload.get("error") == "host_not_supported_on_windows", "Windows Host must fail closed")
            require(host_payload.get("ok") is False, "Windows Host unexpectedly reported success")
            database_files = [path for path in temp_root.rglob("*") if path.is_file() and ".db" in path.name]
            require(not database_files, f"offline mock acceptance created database files: {database_files}")
    finally:
        gateway.shutdown()
        gateway.server_close()
        thread.join(timeout=5)

    require(
        any(request.get("path") == "/api/agent-gateway/status" for request in SmokeGateway.requests),
        "worker preflight did not reach the smoke Gateway",
    )
    print(
        json.dumps(
            {
                "ok": True,
                "platform": "windows",
                "wheel": wheel_summary,
                "install": install_summary,
                "cli_help": True,
                "cli_login": True,
                "cli_worker_preflight": True,
                "worker_help": True,
                "worker_preflight": True,
                "worker_once_offline_mock": True,
                "worker_state_written": True,
                "evidence_request_order_verified": True,
                "windows_task_template": True,
                "windows_task_xml_installed": True,
                "task_scheduler_mutated": False,
                "windows_host_fail_closed": True,
                "gateway_request_count": len(SmokeGateway.requests),
                "ci_offline_mock": True,
                "database_created": False,
                "live_execution_performed": False,
                "token_omitted": True,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
