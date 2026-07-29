#!/usr/bin/env python3
"""Verify Host machine credentials stay bound to their configured origin."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agentops_mis_cli import agentops as agentops_module
from agentops_mis_cli.agentops import AgentOpsClient as SharedAgentOpsClient
from agentops_mis_cli.host import loopback_base_url
from agentops_mis_cli.redaction import redact_text
from agentops_mis_cli.worker import AgentOpsClient, credential_transport_url_allowed


def main() -> int:
    failures: list[str] = []
    machine_key = "agthost_" + "fixture_binding_value_123456789"
    arbitrary_machine_key = "fixture-arbitrary-machine-credential-value"
    explicit_key = "agtok_" + "fixture_explicit_value_123456789"
    with tempfile.TemporaryDirectory(prefix="agentops-host-cli-binding-") as temporary:
        root = Path(temporary)
        original_config_path = agentops_module.CONFIG_PATH
        agentops_module.CONFIG_PATH = root / ".agentops" / "config.json"
        try:
            agentops_module.save_config({
                "base_url": "http://127.0.0.1:18878",
                "api_key": machine_key,
                "api_key_base_url": "http://127.0.0.1:18878",
                "workspace_id": "local-demo",
                "agent_id": "agt_host_local_cli",
                "request_timeout": 17,
            })
            args = SimpleNamespace(
                base_url=None,
                api_key=None,
                workspace_id=None,
                agent_id=None,
                request_timeout=None,
            )
            with mock.patch.dict(os.environ, {"AGENTOPS_BASE_URL": "http://127.0.0.1:19999"}, clear=False):
                os.environ.pop("AGENTOPS_API_KEY", None)
                rebound = agentops_module.resolved_context(args)
            with mock.patch.dict(os.environ, {
                "AGENTOPS_BASE_URL": "http://127.0.0.1:19999",
                "AGENTOPS_API_KEY": explicit_key,
            }, clear=False):
                explicit = agentops_module.resolved_context(args)
            agentops_module.save_config({"api_key": machine_key})
            with mock.patch.dict(os.environ, {}, clear=True):
                unbound = agentops_module.resolved_context(args)
            agentops_module.save_config({
                "base_url": "http://127.0.0.1:18878",
                "api_key": machine_key,
                "api_key_base_url": "http://127.0.0.1:9",
                "workspace_id": "local-demo",
                "agent_id": "agt_host_local_cli",
            })
            login_args = SimpleNamespace(
                base_url=None,
                api_key=None,
                workspace_id=None,
                agent_id=None,
            )
            with mock.patch.dict(os.environ, {}, clear=True):
                agentops_module.cmd_login(login_args)
            switched_config = agentops_module.load_config()
        finally:
            agentops_module.CONFIG_PATH = original_config_path

        captured: list[dict[str, object]] = []

        class FixtureHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                captured.append({
                    "path": self.path,
                    "authorization": self.headers.get("Authorization"),
                    "api_key": self.headers.get("X-AgentOps-Api-Key"),
                })
                if self.path == "/redirect":
                    self.send_response(302)
                    self.send_header("Location", "/captured")
                    self.end_headers()
                    return
                if self.path == "/reflect":
                    body = json.dumps({"echo": machine_key}).encode("utf-8")
                    self.send_response(401)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                body = b'{"ok":true}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format, *_args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base_url = f"http://127.0.0.1:{server.server_port}"
        client = AgentOpsClient(base_url, "local-demo", "agt_fixture", machine_key)
        redirect_error = ""
        reflected_error = ""
        shared_redirect_error = ""
        shared_reflected_error = ""
        try:
            try:
                client.get("/redirect")
            except RuntimeError as exc:
                redirect_error = str(exc)
            try:
                client.get("/reflect")
            except RuntimeError as exc:
                reflected_error = str(exc)
            shared_start = len(captured)
            shared_client = SharedAgentOpsClient({
                "base_url": base_url,
                "api_key": machine_key,
                "workspace_id": "local-demo",
                "agent_id": "agt_fixture",
                "request_timeout": 2,
                "sources": {},
            })
            try:
                shared_client.get("/redirect")
            except RuntimeError as exc:
                shared_redirect_error = str(exc)
            try:
                shared_client.get("/reflect")
            except RuntimeError as exc:
                shared_reflected_error = str(exc)
            shared_paths = [str(item["path"]) for item in captured[shared_start:]]
            before_proxy_probe = len(captured)
            with mock.patch.dict(os.environ, {
                "HTTP_PROXY": base_url,
                "http_proxy": base_url,
                "NO_PROXY": "",
                "no_proxy": "",
            }):
                try:
                    AgentOpsClient("http://127.0.0.2:9", "local-demo", "agt_fixture", machine_key).request("GET", "/status", timeout=1)
                except RuntimeError:
                    pass
                try:
                    SharedAgentOpsClient({
                        "base_url": "http://127.0.0.2:9",
                        "api_key": machine_key,
                        "workspace_id": "local-demo",
                        "agent_id": "agt_fixture",
                        "request_timeout": 1,
                        "sources": {},
                    }).get("/status")
                except RuntimeError:
                    pass
            proxy_bypassed = len(captured) == before_proxy_probe
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        redirect_paths = [str(item["path"]) for item in captured]
        redirect_blocked = redirect_paths.count("/redirect") == 2 and "/captured" not in redirect_paths
        reflected_redacted = bool(reflected_error and machine_key not in reflected_error and "[REDACTED]" in reflected_error)
        shared_client_hardened = (
            shared_paths == ["/redirect", "/reflect"]
            and machine_key not in shared_redirect_error
            and machine_key not in shared_reflected_error
            and "[REDACTED]" in shared_reflected_error
        )
        arbitrary_reflection_redacted = arbitrary_machine_key not in AgentOpsClient(
            base_url,
            "local-demo",
            "agt_fixture",
            arbitrary_machine_key,
        ).safe_error_detail({"echo": arbitrary_machine_key}, 500)
        source_binding = (
            rebound.get("api_key") == ""
            and (rebound.get("sources") or {}).get("api_key") == "blocked_origin_mismatch"
            and explicit.get("api_key") == explicit_key
            and unbound.get("api_key") == ""
            and (unbound.get("sources") or {}).get("api_key") == "blocked_origin_mismatch"
        )
        login_switch_cleared_key = "api_key" not in switched_config and "api_key_base_url" not in switched_config
        literal_loopback_only = (
            loopback_base_url("localhost", 8787) is None
            and loopback_base_url("127.0.0.1", 8787) == "http://127.0.0.1:8787"
            and not credential_transport_url_allowed("http://localhost:8787/api/agent-gateway/status")
            and not credential_transport_url_allowed("http://192.0.2.10/api/agent-gateway/status")
            and credential_transport_url_allowed("https://agentops.example.invalid/api/agent-gateway/status")
        )
        direct_redaction = machine_key not in redact_text({"echo": machine_key}, 500)

        checks = {
            "config_key_origin_binding": source_binding,
            "login_base_switch_clears_stale_key": login_switch_cleared_key,
            "credential_redirect_blocked": redirect_blocked,
            "reflected_host_key_redacted": reflected_redacted,
            "shared_cli_client_hardened": shared_client_hardened,
            "arbitrary_machine_key_redacted": arbitrary_reflection_redacted,
            "environment_proxy_bypassed": proxy_bypassed,
            "literal_loopback_or_https_only": literal_loopback_only,
            "direct_host_key_redaction": direct_redaction,
            "config_private": (root / ".agentops" / "config.json").stat().st_mode & 0o777 == 0o600,
        }
        failures.extend(name for name, passed in checks.items() if not passed)

    print(json.dumps({
        "ok": not failures,
        "operation": "host_cli_credential_binding_smoke",
        "checks": checks,
        "failures": failures,
        "credential_values_omitted": True,
        "network_external": False,
        "temporary_config": True,
    }, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
