#!/usr/bin/env python3
"""Verify safe first-Owner bootstrap through the packaged Host CLI contract."""
from __future__ import annotations

import atexit
import json
import contextlib
import io
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
CONNECTION_ENV_KEYS = {
    "AGENTOPS_AGENT_ID",
    "AGENTOPS_API_KEY",
    "AGENTOPS_BASE_URL",
    "AGENTOPS_CONFIG",
    "AGENTOPS_WORKSPACE_ID",
}
ACTIVE_HOST_ENVS: list[dict[str, str]] = []
ACTIVE_FOREGROUND_GROUPS: list[int] = []

from agentops_mis_cli import host as host_module


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def run_host(env: dict, *args: str, expected=(0,), input_text: str | None = None) -> tuple[dict, str]:
    process = subprocess.run(
        [sys.executable, "-m", "agentops_mis_cli.cli", "host", *args],
        cwd=ROOT,
        env=env,
        input=input_text,
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    if process.returncode not in expected:
        raise RuntimeError(f"host {' '.join(args)} exited {process.returncode}: {process.stderr[-300:]}")
    return json.loads(process.stdout), (process.stdout or "") + (process.stderr or "")


def register_host(env: dict[str, str]) -> None:
    ACTIVE_HOST_ENVS.append(dict(env))


def unregister_host(env: dict[str, str]) -> None:
    host_home = env.get("AGENTOPS_HOST_HOME")
    for index in range(len(ACTIVE_HOST_ENVS) - 1, -1, -1):
        if ACTIVE_HOST_ENVS[index].get("AGENTOPS_HOST_HOME") == host_home:
            ACTIVE_HOST_ENVS.pop(index)
            return


def cleanup_processes() -> None:
    while ACTIVE_HOST_ENVS:
        env = ACTIVE_HOST_ENVS.pop()
        try:
            subprocess.run(
                [sys.executable, "-m", "agentops_mis_cli.cli", "host", "stop"],
                cwd=ROOT,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
    while ACTIVE_FOREGROUND_GROUPS:
        process_group = ACTIVE_FOREGROUND_GROUPS.pop()
        try:
            os.killpg(process_group, signal.SIGTERM)
        except OSError:
            pass


def install_cleanup_handlers() -> None:
    atexit.register(cleanup_processes)

    def handle_signal(signum, _frame):
        cleanup_processes()
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)


def request_json(base_url: str, path: str, *, body: dict | None = None) -> tuple[int, dict]:
    request = urllib.request.Request(
        base_url + path,
        data=None if body is None else json.dumps(body).encode("utf-8"),
        method="GET" if body is None else "POST",
        headers={"Content-Type": "application/json", "Origin": base_url},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def main() -> int:
    install_cleanup_handlers()
    failures: list[str] = []
    evidence: dict[str, object] = {}
    parser = host_module.build_parser()
    bootstrap_parser = next(action for action in parser._actions if getattr(action, "choices", None)).choices["bootstrap-owner"]
    password_argv_option_present = any("--password" in action.option_strings for action in bootstrap_parser._actions)
    forbidden_secret = "fixture-forbidden-argv-secret"
    forbidden_variants = (
        ("--password", forbidden_secret),
        (f"--password={forbidden_secret}",),
        ("--pass", forbidden_secret),
        (f"--password-stdin={forbidden_secret}",),
        ("--password-stdin", forbidden_secret),
        (f"--password={forbidden_secret}", "bootstrap-owner"),
        ("--password-stdin", f"-{forbidden_secret}"),
        (f"--pass=-{forbidden_secret}",),
        ("--password-stdin", "--", forbidden_secret),
    )
    forbidden_results = [
        subprocess.run(
            [sys.executable, "-m", "agentops_mis_cli.cli", "host", "bootstrap-owner", *variant],
            cwd=ROOT,
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        for variant in forbidden_variants
    ]
    forbidden_payloads = [json.loads(result.stdout or "{}") for result in forbidden_results]
    password_argv_rejected_safely = all(
        result.returncode == 2
        and payload.get("error") in {"password_argv_forbidden", "invalid_arguments"}
        and forbidden_secret not in ((result.stdout or "") + (result.stderr or ""))
        for result, payload in zip(forbidden_results, forbidden_payloads)
    )

    captured_requests: list[dict[str, str]] = []

    class CaptureHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode("utf-8", errors="replace")
            captured_requests.append({"path": self.path, "body": raw})
            self.send_response(302 if self.path == "/redirect" else 200)
            self.send_header("Content-Type", "application/json")
            if self.path == "/redirect":
                self.send_header("Location", "/captured")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')

        def log_message(self, _format, *_args):
            return

    capture_server = ThreadingHTTPServer(("127.0.0.1", 0), CaptureHandler)
    capture_thread = Thread(target=capture_server.serve_forever, daemon=True)
    capture_thread.start()
    capture_base_url = f"http://127.0.0.1:{capture_server.server_port}"
    proxy_fixture_secret = "fixture-proxy-bootstrap-secret"
    try:
        with mock.patch.dict(os.environ, {
            "HTTP_PROXY": capture_base_url,
            "http_proxy": capture_base_url,
            "NO_PROXY": "",
            "no_proxy": "",
        }):
            proxy_status, _proxy_payload = host_module.local_json_request(
                "http://127.0.0.2:9",
                "/api/human-auth/bootstrap",
                method="POST",
                body={"setup_code": proxy_fixture_secret, "password": proxy_fixture_secret},
            )
            proxy_capture_count = len(captured_requests)
            redirect_status, _redirect_payload = host_module.local_json_request(
                capture_base_url,
                "/redirect",
                method="POST",
                body={"setup_code": proxy_fixture_secret, "password": proxy_fixture_secret},
            )
    finally:
        capture_server.shutdown()
        capture_server.server_close()
        capture_thread.join(timeout=5)
    redirect_paths = [item["path"] for item in captured_requests]
    redirected_secret_forwarded = any(item["path"] == "/captured" and proxy_fixture_secret in item["body"] for item in captured_requests)
    mismatch_calls: list[tuple[str, str]] = []

    def mismatch_request(_base_url: str, path: str, *, method: str = "GET", body: dict | None = None):
        mismatch_calls.append((path, method))
        return 200, {"required": True, "bootstrap_required": True}

    mismatch_output = io.StringIO()
    with (
        mock.patch.object(host_module, "require_initialized", return_value=({"host": "127.0.0.1", "port": 8787}, {"owner_setup_code": "fixture-code"})),
        mock.patch.object(host_module, "health", return_value={"reachable": True, "status": "ready"}),
        mock.patch.object(host_module, "managed_host_running", return_value=True),
        mock.patch.object(host_module, "local_json_request", side_effect=mismatch_request),
        mock.patch.object(host_module.sys.stdin, "isatty", return_value=True),
        mock.patch.object(host_module.getpass, "getpass", side_effect=["fixture-password-a", "fixture-password-b"]),
        contextlib.redirect_stdout(mismatch_output),
    ):
        mismatch_code = host_module.cmd_bootstrap_owner(SimpleNamespace(confirm=True, username="owner.fixture", display_name="Fixture Owner", password_stdin=False))
    mismatch_payload = json.loads(mismatch_output.getvalue())
    weak_server_message = "fixture-untrusted-server-message"

    def weak_password_request(_base_url: str, path: str, *, method: str = "GET", body: dict | None = None):
        if path == "/api/human-auth/status":
            return 200, {"required": True, "bootstrap_required": True}
        return 400, {"error": "weak_password", "message": weak_server_message}

    weak_password_output = io.StringIO()
    with (
        mock.patch.object(host_module, "require_initialized", return_value=({"host": "127.0.0.1", "port": 8787}, {"owner_setup_code": "fixture-code"})),
        mock.patch.object(host_module, "health", return_value={"reachable": True, "status": "ready"}),
        mock.patch.object(host_module, "managed_host_running", return_value=True),
        mock.patch.object(host_module, "local_json_request", side_effect=weak_password_request),
        mock.patch.object(host_module.sys.stdin, "isatty", return_value=True),
        mock.patch.object(host_module.getpass, "getpass", side_effect=["short-value", "short-value"]),
        contextlib.redirect_stdout(weak_password_output),
    ):
        weak_password_code = host_module.cmd_bootstrap_owner(
            SimpleNamespace(confirm=True, username="owner.fixture", display_name="Fixture Owner", password_stdin=False)
        )
    weak_password_payload = json.loads(weak_password_output.getvalue())
    evidence["local_contract"] = {
        "password_argv_option_present": password_argv_option_present,
        "password_argv_variants_rejected_safely": password_argv_rejected_safely,
        "environment_proxy_bypassed": proxy_status == 0 and proxy_capture_count == 0,
        "redirect_rejected": redirect_status == 302 and redirect_paths == ["/redirect"] and not redirected_secret_forwarded,
        "mismatch_code": mismatch_code,
        "mismatch_error": mismatch_payload.get("error"),
        "bootstrap_post_called": any(path == "/api/human-auth/bootstrap" and method == "POST" for path, method in mismatch_calls),
        "weak_password_code": weak_password_code,
        "weak_password_error": weak_password_payload.get("error"),
        "weak_password_guidance": weak_password_payload.get("message"),
        "untrusted_server_message_omitted": weak_server_message not in weak_password_output.getvalue(),
    }
    if (
        password_argv_option_present
        or not evidence["local_contract"]["password_argv_variants_rejected_safely"]
        or not evidence["local_contract"]["environment_proxy_bypassed"]
        or not evidence["local_contract"]["redirect_rejected"]
        or mismatch_code != 2
        or mismatch_payload.get("error") != "password_confirmation_mismatch"
        or evidence["local_contract"]["bootstrap_post_called"]
        or weak_password_code != 2
        or weak_password_payload.get("error") != "weak_password"
        or weak_password_payload.get("message") != "Password must contain at least 12 characters."
        or weak_server_message in weak_password_output.getvalue()
    ):
        failures.append("local Owner bootstrap password-input contract failed")
    with tempfile.TemporaryDirectory(prefix="agentops-owner-bootstrap-") as temporary:
        root = Path(temporary)
        host_home = root / "host"
        fixture_home = root / "home"
        cli_config_path = fixture_home / ".agentops" / "config.json"
        ui_dist = root / "ui"
        ui_dist.mkdir()
        (ui_dist / "index.html").write_text("<!doctype html><div id='root'>OWNER_BOOTSTRAP_FIXTURE</div>\n", encoding="utf-8")
        env = {
            **{key: value for key, value in os.environ.items() if key not in CONNECTION_ENV_KEYS},
            "HOME": str(fixture_home),
            "AGENTOPS_HOST_HOME": str(host_home),
            "AGENTOPS_CONFIG": str(cli_config_path),
        }
        password = "fixture-owner-password-2026"
        setup_code = ""
        try:
            initialized, init_output = run_host(
                env,
                "init",
                "--port",
                str(free_port()),
                "--ui-dist",
                str(ui_dist),
            )
            setup_code = str(initialized.get("owner_setup_code") or "")
            if not setup_code:
                failures.append("Host init did not create the one-time setup code")

            unmanaged_cli, unmanaged_cli_output = run_host(
                env,
                "configure-cli",
                "--confirm",
                expected=(2,),
            )
            unmanaged_password = "fixture-unmanaged-owner-password"
            unmanaged_owner, unmanaged_owner_output = run_host(
                env,
                "bootstrap-owner",
                "--username",
                "unmanaged.owner",
                "--password-stdin",
                "--confirm",
                input_text=unmanaged_password + "\n",
                expected=(2,),
            )
            if (
                unmanaged_cli.get("error") != "managed_host_not_running"
                or unmanaged_owner.get("error") != "managed_host_not_running"
                or cli_config_path.exists()
                or unmanaged_password in (unmanaged_cli_output + unmanaged_owner_output)
            ):
                failures.append("credentialed Host commands did not fail closed before a managed Host was running")

            register_host(env)
            started, start_output = run_host(env, "start", "--no-workers")
            base_url = str(started.get("local_console_url") or "").removesuffix("/workspace")
            before_status, before = request_json(base_url, "/api/human-auth/status")

            configure_preview, configure_preview_output = run_host(env, "configure-cli", expected=(2,))
            if configure_preview.get("error") != "confirmation_required" or cli_config_path.exists():
                failures.append("CLI configuration preview was not side-effect free")
            stale_api_key = "fixture-stale-machine-key"
            cli_config_path.parent.mkdir(parents=True, exist_ok=True)
            cli_config_path.write_text(json.dumps({
                "base_url": "http://127.0.0.1:9",
                "workspace_id": "stale-workspace",
                "agent_id": "agt_stale_fixture",
                "api_key": stale_api_key,
                "api_key_base_url": "http://127.0.0.1:9",
                "request_timeout": 17,
            }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            cli_config_path.chmod(0o600)
            configured_cli, configured_cli_output = run_host(env, "configure-cli", "--confirm")
            cli_config = json.loads(cli_config_path.read_text(encoding="utf-8"))
            configured_api_key = str(cli_config.get("api_key") or "")
            preflight_process = subprocess.run(
                [sys.executable, "-m", "agentops_mis_cli.cli", "worker", "preflight", "--adapter", "mock"],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            preflight_payload = json.loads(preflight_process.stdout or "{}")
            unsafe_config_path = root / "outside-cli-config.json"
            unsafe_config_env = {**env, "AGENTOPS_CONFIG": str(unsafe_config_path)}
            unsafe_config, unsafe_config_output = run_host(
                unsafe_config_env,
                "configure-cli",
                "--confirm",
                expected=(2,),
            )
            symlink_target = root / "symlink-target.json"
            symlink_config_path = fixture_home / ".agentops" / "symlink-config.json"
            symlink_config_path.symlink_to(symlink_target)
            symlink_config_env = {**env, "AGENTOPS_CONFIG": str(symlink_config_path)}
            unsafe_symlink, unsafe_symlink_output = run_host(
                symlink_config_env,
                "configure-cli",
                "--confirm",
                expected=(2,),
            )

            preview, preview_output = run_host(
                env,
                "bootstrap-owner",
                "--username",
                "owner.fixture",
                expected=(2,),
            )
            if preview.get("error") != "confirmation_required":
                failures.append("Owner bootstrap was not confirmation gated")

            config_path = host_home / "config.json"
            host_config = json.loads(config_path.read_text(encoding="utf-8"))
            host_config["host"] = "192.0.2.10"
            config_path.write_text(json.dumps(host_config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            cli_config_before_unsafe = cli_config_path.read_bytes()
            unsafe_cli, unsafe_cli_output = run_host(
                env,
                "configure-cli",
                "--confirm",
                expected=(2,),
            )
            unsafe_target, unsafe_output = run_host(
                env,
                "bootstrap-owner",
                "--username",
                "unsafe.target",
                "--password-stdin",
                "--confirm",
                expected=(2,),
            )
            host_config["host"] = "127.0.0.1"
            config_path.write_text(json.dumps(host_config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            non_tty, non_tty_output = run_host(
                env,
                "bootstrap-owner",
                "--username",
                "non.tty",
                "--confirm",
                input_text="",
                expected=(2,),
            )
            empty_password, empty_output = run_host(
                env,
                "bootstrap-owner",
                "--username",
                "empty.password",
                "--password-stdin",
                "--confirm",
                input_text="\n",
                expected=(2,),
            )
            created, created_output = run_host(
                env,
                "bootstrap-owner",
                "--username",
                "owner.fixture",
                "--display-name",
                "Fixture Owner",
                "--password-stdin",
                "--confirm",
                input_text=password + "\n",
            )
            after_status, after = request_json(base_url, "/api/human-auth/status")
            login_status, login = request_json(
                base_url,
                "/api/human-auth/login",
                body={"username": "owner.fixture", "password": password},
            )
            repeated, repeated_output = run_host(
                env,
                "bootstrap-owner",
                "--username",
                "second.owner",
                "--password-stdin",
                "--confirm",
                expected=(2,),
            )
            combined_output = "".join((init_output, unmanaged_cli_output, unmanaged_owner_output, start_output, configure_preview_output, configured_cli_output, preflight_process.stdout, preflight_process.stderr, unsafe_config_output, unsafe_symlink_output, preview_output, unsafe_cli_output, unsafe_output, non_tty_output, created_output, repeated_output, empty_output))
            secret_leaked = bool(
                (setup_code and setup_code in "".join((start_output, configure_preview_output, configured_cli_output, preflight_process.stdout, preflight_process.stderr, preview_output, unsafe_cli_output, unsafe_output, non_tty_output, created_output, repeated_output, empty_output)))
                or (configured_api_key and configured_api_key in combined_output)
                or stale_api_key in combined_output
                or password in combined_output
                or password in env.values()
            )
            evidence.update({
                "before": {
                    "status": before_status,
                    "bootstrap_required": before.get("bootstrap_required"),
                },
                "preview": {
                    "confirmation_required": preview.get("error") == "confirmation_required",
                    "password_omitted": preview.get("password_omitted"),
                    "setup_code_omitted": preview.get("setup_code_omitted"),
                },
                "configure_cli": {
                    "managed_host_required": unmanaged_cli.get("error") == "managed_host_not_running" and unmanaged_owner.get("error") == "managed_host_not_running",
                    "preview_side_effect_free": configure_preview.get("error") == "confirmation_required",
                    "configured": configured_cli.get("machine_credential_configured"),
                    "browser_session_reused": configured_cli.get("browser_session_reused"),
                    "config_private": (cli_config_path.stat().st_mode & 0o777) == 0o600,
                    "loopback_target": cli_config.get("base_url") == base_url,
                    "workspace_id": cli_config.get("workspace_id"),
                    "stale_context_replaced": configured_api_key != stale_api_key and cli_config.get("agent_id") == "agt_host_local_cli",
                    "unrelated_preference_preserved": cli_config.get("request_timeout") == 17,
                    "machine_key_present": bool(configured_api_key),
                    "preflight_ok": preflight_process.returncode == 0 and preflight_payload.get("ok") is True,
                    "unsafe_target_error": unsafe_cli.get("error"),
                    "unsafe_target_preserved_config": cli_config_path.read_bytes() == cli_config_before_unsafe,
                    "unsafe_config_path_error": unsafe_config.get("error"),
                    "unsafe_config_path_not_written": not unsafe_config_path.exists(),
                    "symlink_config_error": unsafe_symlink.get("error"),
                    "symlink_target_not_written": not symlink_target.exists(),
                    "credential_omitted": configured_cli.get("credential_omitted"),
                },
                "created": {
                    "ok": created.get("ok"),
                    "owner_created": created.get("owner_created"),
                    "role": created.get("role"),
                    "password_omitted": created.get("password_omitted"),
                    "setup_code_omitted": created.get("setup_code_omitted"),
                    "session_cookie_omitted": created.get("session_cookie_omitted"),
                },
                "after": {
                    "status": after_status,
                    "bootstrap_required": after.get("bootstrap_required"),
                    "login_status": login_status,
                    "login_role": (login.get("user") or {}).get("role"),
                },
                "repeat": {
                    "error": repeated.get("error"),
                    "http_status": repeated.get("http_status"),
                },
                "empty_password": {
                    "error": empty_password.get("error"),
                },
                "unsafe_target": {
                    "error": unsafe_target.get("error"),
                    "target_omitted": unsafe_target.get("target_omitted"),
                },
                "non_tty": {
                    "error": non_tty.get("error"),
                },
                "secret_leaked": secret_leaked,
            })
            if before_status != 200 or before.get("bootstrap_required") is not True:
                failures.append("Host did not begin in Owner bootstrap state")
            if (
                configured_cli.get("machine_credential_configured") is not True
                or configured_cli.get("browser_session_reused") is not False
                or configured_cli.get("credential_omitted") is not True
                or not configured_api_key
                or cli_config.get("base_url") != base_url
                or cli_config.get("workspace_id") != "local-demo"
                or configured_api_key == stale_api_key
                or cli_config.get("agent_id") != "agt_host_local_cli"
                or cli_config.get("request_timeout") != 17
                or (cli_config_path.stat().st_mode & 0o777) != 0o600
                or preflight_process.returncode != 0
                or preflight_payload.get("ok") is not True
            ):
                failures.append("confirmed Host CLI configuration did not produce a working private machine CLI context")
            if unsafe_cli.get("error") != "unsafe_cli_target" or cli_config_path.read_bytes() != cli_config_before_unsafe:
                failures.append("non-loopback CLI configuration did not fail closed without mutating saved config")
            if (
                unsafe_config.get("error") != "unsafe_cli_config_path"
                or unsafe_config_path.exists()
                or unsafe_symlink.get("error") != "unsafe_cli_config_path"
                or symlink_target.exists()
            ):
                failures.append("unsafe or symlink CLI config path did not fail closed without writing a credential")
            if (
                created.get("ok") is not True
                or created.get("owner_created") is not True
                or created.get("role") != "owner"
                or created.get("password_omitted") is not True
                or created.get("setup_code_omitted") is not True
                or created.get("session_cookie_omitted") is not True
            ):
                failures.append("confirmed CLI bootstrap did not create a safely reported Owner")
            if after_status != 200 or after.get("bootstrap_required") is not False or login_status != 200 or (login.get("user") or {}).get("role") != "owner":
                failures.append("created Owner could not authenticate or bootstrap remained open")
            if repeated.get("error") != "owner_already_initialized" or repeated.get("http_status") != 409:
                failures.append("repeat Owner bootstrap did not fail closed")
            if empty_password.get("error") != "username_and_password_required":
                failures.append("empty stdin password was not rejected before the API call")
            if unsafe_target.get("error") != "unsafe_bootstrap_target" or unsafe_target.get("target_omitted") is not True:
                failures.append("non-loopback Owner bootstrap target did not fail closed before credential input")
            if non_tty.get("error") != "interactive_terminal_required":
                failures.append("non-interactive Owner bootstrap did not require --password-stdin")
            if secret_leaked:
                failures.append("Owner setup code or password appeared in CLI output")

            concurrent_home = root / "concurrent-host"
            concurrent_env = {
                **{key: value for key, value in os.environ.items() if key not in CONNECTION_ENV_KEYS},
                "AGENTOPS_HOST_HOME": str(concurrent_home),
            }
            concurrent_setup_code = ""
            try:
                concurrent_init, concurrent_init_output = run_host(
                    concurrent_env,
                    "init",
                    "--port",
                    str(free_port()),
                    "--ui-dist",
                    str(ui_dist),
                )
                concurrent_setup_code = str(concurrent_init.get("owner_setup_code") or "")
                register_host(concurrent_env)
                concurrent_start, concurrent_start_output = run_host(concurrent_env, "start", "--no-workers")
                concurrent_base_url = str(concurrent_start.get("local_console_url") or "").removesuffix("/workspace")
                candidates = (
                    ("owner.alpha", "fixture-concurrent-alpha-2026"),
                    ("owner.beta", "fixture-concurrent-beta-2026"),
                )

                def submit(candidate: tuple[str, str]) -> tuple[int, dict]:
                    username, candidate_password = candidate
                    return request_json(
                        concurrent_base_url,
                        "/api/human-auth/bootstrap",
                        body={
                            "setup_code": concurrent_setup_code,
                            "username": username,
                            "display_name": username,
                            "password": candidate_password,
                        },
                    )

                with ThreadPoolExecutor(max_workers=2) as pool:
                    concurrent_results = list(pool.map(submit, candidates))
                concurrent_logins = [
                    request_json(
                        concurrent_base_url,
                        "/api/human-auth/login",
                        body={"username": username, "password": candidate_password},
                    )[0]
                    for username, candidate_password in candidates
                ]
                concurrent_status, concurrent_auth = request_json(concurrent_base_url, "/api/human-auth/status")
                result_codes = sorted(status for status, _payload in concurrent_results)
                result_errors = sorted(str(payload.get("error") or "") for status, payload in concurrent_results if status != 201)
                concurrent_text = concurrent_start_output + json.dumps(concurrent_results, sort_keys=True)
                concurrent_secret_leaked = any(
                    value and value in concurrent_text
                    for value in (concurrent_setup_code, *(candidate_password for _username, candidate_password in candidates))
                )
                evidence["concurrent"] = {
                    "result_codes": result_codes,
                    "loser_errors": result_errors,
                    "successful_login_count": sum(1 for status in concurrent_logins if status == 200),
                    "bootstrap_required_after": concurrent_auth.get("bootstrap_required"),
                    "init_setup_code_visible_once": bool(concurrent_setup_code and concurrent_setup_code in concurrent_init_output),
                    "secret_leaked": concurrent_secret_leaked,
                }
                if (
                    result_codes != [201, 409]
                    or result_errors != ["owner_already_initialized"]
                    or sum(1 for status in concurrent_logins if status == 200) != 1
                    or concurrent_status != 200
                    or concurrent_auth.get("bootstrap_required") is not False
                    or concurrent_secret_leaked
                ):
                    failures.append("concurrent Owner bootstrap did not create exactly one account safely")
            finally:
                try:
                    concurrent_stopped, _output = run_host(concurrent_env, "stop")
                    evidence["concurrent_host_stopped"] = concurrent_stopped.get("ok")
                    if concurrent_stopped.get("ok") is True:
                        unregister_host(concurrent_env)
                except Exception as exc:
                    failures.append(f"Concurrent Host cleanup failed: {type(exc).__name__}")
        except (OSError, RuntimeError, ValueError, urllib.error.URLError) as exc:
            failure_line = traceback.extract_tb(exc.__traceback__)[-1]
            failures.append(f"bootstrap smoke exception at line {failure_line.lineno}: {type(exc).__name__}: {str(exc)[:180]}")
        finally:
            try:
                stopped, _output = run_host(env, "stop")
                evidence["host_stopped"] = stopped.get("ok")
                if stopped.get("ok") is True:
                    unregister_host(env)
            except Exception as exc:
                failures.append(f"Host cleanup failed: {type(exc).__name__}")

    with tempfile.TemporaryDirectory(prefix="agentops-owner-foreground-") as temporary:
        foreground_root = Path(temporary)
        foreground_home = foreground_root / "home"
        foreground_host_home = foreground_root / "host"
        foreground_cli_config = foreground_home / ".agentops" / "config.json"
        foreground_ui = foreground_root / "ui"
        foreground_ui.mkdir()
        (foreground_ui / "index.html").write_text("<!doctype html><div>FOREGROUND_HOST_FIXTURE</div>\n", encoding="utf-8")
        foreground_port = free_port()
        foreground_env = {
            **{key: value for key, value in os.environ.items() if key not in CONNECTION_ENV_KEYS},
            "HOME": str(foreground_home),
            "AGENTOPS_HOST_HOME": str(foreground_host_home),
            "AGENTOPS_CONFIG": str(foreground_cli_config),
        }
        foreground_process = None
        try:
            run_host(
                foreground_env,
                "init",
                "--port",
                str(foreground_port),
                "--ui-dist",
                str(foreground_ui),
            )
            foreground_process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "agentops_mis_cli.cli",
                    "host",
                    "start",
                    "--foreground",
                    "--no-workers",
                ],
                cwd=ROOT,
                env=foreground_env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            ACTIVE_FOREGROUND_GROUPS.append(foreground_process.pid)
            register_host(foreground_env)
            foreground_base_url = f"http://127.0.0.1:{foreground_port}"
            deadline = time.time() + 20
            foreground_ready = False
            while time.time() < deadline:
                try:
                    status, payload = request_json(foreground_base_url, "/health")
                    foreground_ready = status == 200 and payload.get("status") == "ready"
                except (OSError, ValueError, urllib.error.URLError):
                    foreground_ready = False
                if foreground_ready and (foreground_host_home / "run" / "host.pid.json").is_file():
                    break
                time.sleep(0.1)
            foreground_configured, foreground_config_output = run_host(
                foreground_env,
                "configure-cli",
                "--confirm",
            )
            foreground_machine_key = str(json.loads((foreground_host_home / "secrets.json").read_text(encoding="utf-8")).get("api_key") or "")
            foreground_stopped, _foreground_stop_output = run_host(foreground_env, "stop")
            foreground_process.wait(timeout=20)
            if foreground_stopped.get("ok") is True:
                unregister_host(foreground_env)
            if foreground_process.pid in ACTIVE_FOREGROUND_GROUPS:
                ACTIVE_FOREGROUND_GROUPS.remove(foreground_process.pid)
            foreground_pid_removed = not (foreground_host_home / "run" / "host.pid.json").exists()
            foreground_secret_omitted = bool(foreground_machine_key and foreground_machine_key not in foreground_config_output)
            evidence["foreground_host"] = {
                "health_ready": foreground_ready,
                "configure_cli_ok": foreground_configured.get("machine_credential_configured"),
                "credential_omitted": foreground_secret_omitted,
                "stopped": foreground_stopped.get("ok"),
                "pid_record_removed": foreground_pid_removed,
            }
            if (
                not foreground_ready
                or foreground_configured.get("machine_credential_configured") is not True
                or not foreground_secret_omitted
                or foreground_stopped.get("ok") is not True
                or not foreground_pid_removed
            ):
                failures.append("foreground managed Host did not support safe CLI configuration and PID cleanup")
        except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired, urllib.error.URLError) as exc:
            failures.append(f"foreground Host smoke exception: {type(exc).__name__}: {str(exc)[:180]}")
        finally:
            if foreground_process and foreground_process.poll() is None:
                try:
                    os.killpg(foreground_process.pid, signal.SIGTERM)
                except OSError:
                    pass
                try:
                    foreground_process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(foreground_process.pid, signal.SIGKILL)
                    except OSError:
                        pass
                    foreground_process.wait(timeout=5)
            if foreground_process and foreground_process.pid in ACTIVE_FOREGROUND_GROUPS:
                ACTIVE_FOREGROUND_GROUPS.remove(foreground_process.pid)
            unregister_host(foreground_env)

    print(json.dumps({
        "ok": not failures,
        "operation": "private_host_owner_bootstrap_cli_smoke",
        "failures": failures,
        "evidence": evidence,
        "temporary_host_home": True,
        "real_runtime_called": False,
        "init_setup_code_visible_once": True,
        "post_init_credentials_omitted": True,
        "database_persisted": False,
        "signal_cleanup_registered": True,
    }, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
