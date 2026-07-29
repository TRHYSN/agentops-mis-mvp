#!/usr/bin/env python3
"""Verify credential-free service templates and fail-closed local config loading."""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class Gateway(BaseHTTPRequestHandler):
    parent_token = ""
    session_token = ""
    session_created = False
    requests: list[dict] = []

    def log_message(self, _format: str, *_args) -> None:
        return

    def send_json(self, status: int, payload: dict) -> None:
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def authorized(self, token: str) -> bool:
        return self.headers.get("Authorization") == f"Bearer {token}"

    def do_GET(self) -> None:  # noqa: N802
        Gateway.requests.append({"method": "GET", "path": self.path})
        if not self.authorized(Gateway.session_token):
            self.send_json(401, {"error": "unauthorized"})
            return
        if self.path.startswith("/api/agent-gateway/tasks/pull"):
            self.send_json(200, {"tasks": []})
            return
        self.send_json(404, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        size = int(self.headers.get("Content-Length") or "0")
        body = json.loads(self.rfile.read(size).decode("utf-8")) if size else {}
        Gateway.requests.append({"method": "POST", "path": self.path, "body": body})
        if self.path == "/api/agent-gateway/session/create":
            if not self.authorized(Gateway.parent_token):
                self.send_json(401, {"error": "unauthorized"})
                return
            Gateway.session_created = True
            expires = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=15)).isoformat()
            self.send_json(201, {
                "session_id": "sess_local_config_smoke",
                "session_token": Gateway.session_token,
                "expires_at": expires,
                "ttl_sec": 900,
                "scopes": ["tasks:read"],
            })
            return
        if not self.authorized(Gateway.session_token):
            self.send_json(401, {"error": "unauthorized"})
            return
        if self.path in {"/api/agent-gateway/register", "/api/agent-gateway/heartbeat"}:
            self.send_json(200, {"ok": True})
            return
        self.send_json(404, {"error": "not_found"})


def run(cmd: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True, timeout=30, check=False)


def parse(proc: subprocess.CompletedProcess[str]) -> dict:
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"stdout": proc.stdout, "stderr": proc.stderr}


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def write_config(path: Path, *, base_url: str, workspace_id: str, token: str, mode: int = 0o600) -> None:
    path.write_text(json.dumps({
        "base_url": base_url,
        "api_key_base_url": base_url,
        "workspace_id": workspace_id,
        "api_key": token,
    }), encoding="utf-8")
    path.chmod(mode)


def main() -> int:
    failures: list[str] = []
    Gateway.parent_token = "agt" + "host_local_config_parent"
    Gateway.session_token = "agt" + "sess_local_config_child"
    server = ThreadingHTTPServer(("127.0.0.1", 0), Gateway)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    env = os.environ.copy()
    env.pop("AGENTOPS_API_KEY", None)
    env.pop("AGENTOPS_CONFIG", None)
    env.pop("AGENTOPS_WORKER_CREDENTIAL_SOURCE", None)
    try:
        with tempfile.TemporaryDirectory(prefix="agentops_worker_local_config_") as tmp:
            root = Path(tmp)
            config_path = root / "config.json"
            write_config(config_path, base_url=base_url, workspace_id="ws_local_config", token=Gateway.parent_token)

            template = run([
                sys.executable, "-m", "agentops_mis_cli.worker", "service-template",
                "--manager", "launchd", "--base-url", base_url,
                "--workspace-id", "ws_local_config", "--agent-id", "agt_local_config",
                "--adapter", "mock", "--credential-source", "local_config",
                "--config-path", str(config_path),
            ], env)
            require(template.returncode == 0, f"local config template failed: {template.stderr}", failures)
            require("AGENTOPS_WORKER_CREDENTIAL_SOURCE" in template.stdout, "credential source marker missing", failures)
            require("AGENTOPS_CONFIG" in template.stdout and str(config_path) in template.stdout, "config reference missing", failures)
            require("--use-session" in template.stdout, "local config service must mint a session", failures)
            require("AGENTOPS_API_KEY" not in template.stdout, "service template persisted an API key field", failures)
            require(Gateway.parent_token not in template.stdout, "service template leaked parent token", failures)

            service_path = root / "local.agentops.worker.agt_local_config.plist"
            install = run([
                sys.executable, "-m", "agentops_mis_cli.worker", "service-install",
                "--manager", "launchd", "--base-url", base_url,
                "--workspace-id", "ws_local_config", "--agent-id", "agt_local_config",
                "--adapter", "mock", "--credential-source", "local_config",
                "--config-path", str(config_path), "--service-path", str(service_path),
                "--confirm-install",
            ], env)
            install_payload = parse(install)
            require(install.returncode == 0 and install_payload.get("wrote") is True, f"local config service install failed: {install_payload}", failures)
            require(install_payload.get("service_check", {}).get("service_file", {}).get("local_config_reference") is True, f"local config reference check missing: {install_payload}", failures)
            require(service_path.exists() and (service_path.stat().st_mode & 0o777) == 0o600, "local config service file is not mode 0600", failures)
            installed_text = service_path.read_text(encoding="utf-8")
            require("AGENTOPS_API_KEY" not in installed_text and Gateway.parent_token not in installed_text, "installed service leaked credentials", failures)
            require(f"<string>{ROOT}</string>" in installed_text, "direct service install did not use managed package/repository working directory", failures)

            wrapper_path = root / "local.agentops.worker.agt_local_config_wrapper.plist"
            wrapper_env = dict(env)
            wrapper_env["AGENTOPS_CONFIG"] = str(config_path)
            wrapper = run([
                str(ROOT / "scripts" / "agentops"), "worker", "service-install",
                "--manager", "launchd", "--agent-id", "agt_local_config_wrapper",
                "--adapter", "mock", "--credential-source", "local_config",
                "--config-path", str(config_path), "--service-path", str(wrapper_path),
                "--confirm-install",
            ], wrapper_env)
            wrapper_payload = parse(wrapper)
            require(wrapper.returncode == 0 and wrapper_payload.get("wrote") is True, f"agentops wrapper local config install failed: {wrapper_payload}", failures)
            require(wrapper_payload.get("command") == "agentops worker service-install", f"wrapper command marker missing: {wrapper_payload}", failures)
            wrapper_text = wrapper_path.read_text(encoding="utf-8") if wrapper_path.exists() else ""
            require("AGENTOPS_API_KEY" not in wrapper_text and Gateway.parent_token not in wrapper_text, "wrapper service leaked credentials", failures)
            require(f"<string>{ROOT}</string>" in wrapper_text, "agentops wrapper pinned the caller working directory", failures)

            worker = run([
                sys.executable, "-m", "agentops_mis_cli.worker",
                "--once", "--adapter", "mock", "--base-url", base_url,
                "--workspace-id", "ws_local_config", "--agent-id", "agt_local_config",
                "--credential-source", "local_config", "--config-path", str(config_path),
                "--status", "no_such_status_for_local_config_smoke",
            ], env)
            worker_payload = parse(worker)
            require(worker.returncode == 0, f"local config worker failed: {worker_payload}", failures)
            require(worker_payload.get("ok") is True and worker_payload.get("processed") == 0, f"worker result invalid: {worker_payload}", failures)
            require(Gateway.session_created, "worker did not mint a short-lived session", failures)
            session_requests = [item for item in Gateway.requests if item.get("path") == "/api/agent-gateway/session/create"]
            minted_scopes = set((session_requests[-1].get("body") or {}).get("scopes") or []) if session_requests else set()
            require("tasks:create" not in minted_scopes and "approvals:request" not in minted_scopes, f"local config session inherited non-worker scopes: {sorted(minted_scopes)}", failures)
            require({"agents:write", "tasks:read", "tasks:claim", "runs:write", "audit:write"}.issubset(minted_scopes), f"local config session missing worker scopes: {sorted(minted_scopes)}", failures)

            failure_cases: dict[str, dict] = {}
            open_path = root / "open.json"
            write_config(open_path, base_url=base_url, workspace_id="ws_local_config", token=Gateway.parent_token, mode=0o644)
            wrong_origin_path = root / "wrong-origin.json"
            write_config(wrong_origin_path, base_url="http://127.0.0.1:9", workspace_id="ws_local_config", token=Gateway.parent_token)
            missing_origin_path = root / "missing-origin.json"
            missing_origin_path.write_text(json.dumps({
                "base_url": base_url,
                "workspace_id": "ws_local_config",
                "api_key": Gateway.parent_token,
            }), encoding="utf-8")
            missing_origin_path.chmod(0o600)
            symlink_path = root / "link.json"
            symlink_path.symlink_to(config_path)
            for label, path, expected in (
                ("permissions", open_path, "local_config_permissions_too_open"),
                ("origin", wrong_origin_path, "local_config_origin_mismatch"),
                ("origin_binding", missing_origin_path, "local_config_origin_mismatch"),
                ("symlink", symlink_path, "local_config_symlink_rejected"),
            ):
                proc = run([
                    sys.executable, "-m", "agentops_mis_cli.worker", "--once", "--adapter", "mock",
                    "--base-url", base_url, "--workspace-id", "ws_local_config", "--agent-id", "agt_local_config",
                    "--credential-source", "local_config", "--config-path", str(path),
                ], env)
                payload = parse(proc)
                failure_cases[label] = payload
                require(proc.returncode == 1 and payload.get("error") == expected, f"{label} did not fail closed: {payload}", failures)

            broad_scope = run([
                sys.executable, "-m", "agentops_mis_cli.worker", "--once", "--adapter", "mock",
                "--base-url", base_url, "--workspace-id", "ws_local_config", "--agent-id", "agt_local_config",
                "--credential-source", "local_config", "--config-path", str(config_path),
                "--session-scopes", "tasks:create",
            ], env)
            failure_cases["scope"] = parse(broad_scope)
            require(
                broad_scope.returncode == 1 and failure_cases["scope"].get("error") == "local_config_session_scopes_exceed_worker_policy",
                f"broad local config session scope did not fail closed: {failure_cases['scope']}",
                failures,
            )

            serialized = json.dumps({"worker": worker_payload, "install": install_payload, "wrapper": wrapper_payload, "failures": failure_cases}, ensure_ascii=False)
            require(Gateway.parent_token not in serialized and Gateway.session_token not in serialized, "worker output leaked credentials", failures)
    finally:
        server.shutdown()
        server.server_close()

    print(json.dumps({
        "ok": not failures,
        "template_credential_free": not failures and "AGENTOPS_API_KEY" not in template.stdout,
        "session_created": Gateway.session_created,
        "least_privilege_session": "tasks:create" not in minted_scopes and "approvals:request" not in minted_scopes,
        "service_installed": install_payload.get("wrote") is True,
        "wrapper_service_installed": wrapper_payload.get("wrote") is True,
        "fail_closed_cases": sorted(failure_cases),
        "token_omitted": True,
        "failures": failures,
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
