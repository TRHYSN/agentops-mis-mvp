#!/usr/bin/env python3
"""Prove the built commercial Next artifact cannot reach a Python API."""
from __future__ import annotations

import json
import hashlib
import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NEXT_APP = ROOT / "ui" / "next-app"
POSTGRES_MIGRATIONS = ROOT / "migrations" / "postgres"
BUILD_TIMEOUT_SECONDS = 300
STARTUP_TIMEOUT_SECONDS = 90
REQUEST_TIMEOUT_SECONDS = 10


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class PythonObserver(BaseHTTPRequestHandler):
    hits = 0
    lock = threading.Lock()

    def respond(self) -> None:
        with type(self).lock:
            type(self).hits += 1
        body = b'{"ok":true,"unexpected_python_proxy":true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = respond  # type: ignore[assignment]
    do_POST = respond  # type: ignore[assignment]

    def log_message(self, _format: str, *_args: object) -> None:
        return


def request_json(url: str, method: str = "GET") -> tuple[int, dict[str, object], str]:
    body = b"{}" if method == "POST" else None
    request = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={"Content-Type": "application/json"} if body else {},
    )
    try:
        response = urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        payload = json.loads(response.read().decode("utf-8") or "{}")
        require(isinstance(payload, dict), f"response was not a JSON object: {url}")
        return int(response.status), payload, str(response.headers.get("Cache-Control") or "")


def stop_process(process: subprocess.Popen[str] | None) -> None:
    if process is None or process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=10)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)


def free_port() -> int:
    server = ThreadingHTTPServer(("127.0.0.1", 0), PythonObserver)
    port = int(server.server_port)
    server.server_close()
    return port


def postgres_environment() -> dict[str, str]:
    direct_dsn = os.environ.get("AGENTOPS_POSTGRES_DSN", "").strip()
    dsn_file = os.environ.get("AGENTOPS_POSTGRES_DSN_FILE", "").strip()
    require(bool(direct_dsn) != bool(dsn_file), "exactly one PostgreSQL DSN source is required")
    if os.environ.get("AGENTOPS_REQUIRE_FILE_BACKED_POSTGRES_DSN") == "true":
        require(bool(dsn_file), "production smoke requires a file-backed PostgreSQL DSN")
    if dsn_file:
        require(Path(dsn_file).is_file(), "AGENTOPS_POSTGRES_DSN_FILE is not a file")
        return {"AGENTOPS_POSTGRES_DSN_FILE": dsn_file}
    return {"AGENTOPS_POSTGRES_DSN": direct_dsn}


def isolated_environment(upstream_port: int, temp_root: Path) -> dict[str, str]:
    environment = {
        key: value
        for key in ("HOME", "LANG", "LC_ALL", "PATH", "SHELL")
        if (value := os.environ.get(key))
    }
    environment.update({
        "AGENTOPS_API_BASE": f"http://127.0.0.1:{upstream_port}/api",
        "AGENTOPS_CONTROL_PLANE_MODE": "proxy",
        "AGENTOPS_DEPLOYMENT_MODE": "production",
        "AGENTOPS_NEXT_HOST": "127.0.0.1",
        "NEXT_TELEMETRY_DISABLED": "1",
        "NODE_ENV": "production",
        "TEMP": str(temp_root),
        "TMP": str(temp_root),
        "TMPDIR": str(temp_root),
    })
    environment.update(postgres_environment())
    for key in (
        "AGENTOPS_POSTGRES_SCHEMA",
        "AGENTOPS_POSTGRES_RUNTIME_API_SCHEMA",
        "AGENTOPS_POSTGRES_RUNTIME_ROLE",
    ):
        if value := os.environ.get(key, "").strip():
            environment[key] = value
    return environment


def copy_app(destination: Path) -> None:
    def ignore(_directory: str, names: list[str]) -> set[str]:
        return {name for name in names if name == ".next" or name.startswith(".env")}

    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(NEXT_APP, destination, ignore=ignore, copy_function=shutil.copy2)
    shutil.copytree(
        POSTGRES_MIGRATIONS,
        destination.parents[1] / "migrations" / "postgres",
        copy_function=shutil.copy2,
    )


def tracked_diff_digest() -> str:
    result = subprocess.run(
        ["git", "diff", "--binary", "HEAD", "--"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return hashlib.sha256(result.stdout).hexdigest()


def start_app(
    npm: str,
    isolated_app: Path,
    environment: dict[str, str],
    port: int,
    log_path: Path,
) -> subprocess.Popen[str]:
    start_environment = dict(environment)
    start_environment["AGENTOPS_NEXT_PORT"] = str(port)
    with log_path.open("w", encoding="utf-8") as output:
        return subprocess.Popen(
            [npm, "start"],
            cwd=isolated_app,
            env=start_environment,
            text=True,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )


def wait_until_ready(base_url: str, process: subprocess.Popen[str], log_path: Path) -> None:
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Next exited before readiness: {log_path.read_text(errors='replace')[-4000:]}")
        try:
            request_json(f"{base_url}/api/mis/readiness-probe")
            return
        except (OSError, ValueError, json.JSONDecodeError):
            time.sleep(0.2)
    raise RuntimeError(f"Next startup timed out: {log_path.read_text(errors='replace')[-4000:]}")


def main() -> int:
    node = shutil.which("node")
    npm = shutil.which("npm")
    require(bool(node), "node is required")
    require(bool(npm), "npm is required")
    postgres_environment()
    require((NEXT_APP / "node_modules").is_dir(), "run npm ci in ui/next-app first")
    require(POSTGRES_MIGRATIONS.is_dir(), "Postgres migrations are required")
    source_diff_before = tracked_diff_digest()

    with PythonObserver.lock:
        PythonObserver.hits = 0
    observer = ThreadingHTTPServer(("127.0.0.1", 0), PythonObserver)
    observer_thread = threading.Thread(target=observer.serve_forever, daemon=True)
    observer_thread.start()
    process: subprocess.Popen[str] | None = None
    try:
        with tempfile.TemporaryDirectory(prefix="agentops-next-production-") as temporary:
            temp_root = Path(temporary)
            isolated_app = temp_root / "repo" / "ui" / "next-app"
            copy_app(isolated_app)
            next_cli = isolated_app / "node_modules" / "next" / "dist" / "bin" / "next"
            require(next_cli.is_file(), "isolated Next CLI is missing")
            environment = isolated_environment(int(observer.server_port), temp_root)

            build_log = temp_root / "build.log"
            with build_log.open("w", encoding="utf-8") as output:
                build = subprocess.run(
                    [str(node), str(next_cli), "build"],
                    cwd=isolated_app,
                    env=environment,
                    text=True,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    timeout=BUILD_TIMEOUT_SECONDS,
                    check=False,
                )
            require(build.returncode == 0, f"Next build failed: {build_log.read_text(errors='replace')[-5000:]}")
            require((isolated_app / ".next" / "BUILD_ID").is_file(), "production BUILD_ID is missing")

            port = free_port()
            base_url = f"http://127.0.0.1:{port}"
            start_log = temp_root / "start.log"
            process = start_app(str(npm), isolated_app, environment, port, start_log)
            wait_until_ready(base_url, process, start_log)

            statuses: dict[str, int] = {}
            for method, path in (
                ("GET", "/api/mis/not-migrated"),
                ("POST", "/api/mis/not-migrated"),
                ("GET", "/api/agent-gateway/not-migrated"),
            ):
                status, payload, cache_control = request_json(f"{base_url}{path}", method)
                require(status == 503, f"{method} {path} returned {status}")
                require(payload.get("error") == "typescript_route_owner_required", f"{path} lost owner failure")
                require(payload.get("python_proxy_performed") is False, f"{path} allowed Python proxy")
                require(payload.get("unexpected_python_proxy") is not True, f"{path} returned Python observer")
                require("no-store" in cache_control.lower(), f"{path} response is cacheable")
                statuses[f"{method} {path}"] = status

            approval_path = "/api/mis/agent-gateway/approvals/request"
            status, payload, cache_control = request_json(
                f"{base_url}{approval_path}",
                "POST",
            )
            require(status == 401, f"POST {approval_path} returned {status}")
            require(
                status == 401 and payload.get("error") == "unauthorized",
                f"{approval_path} did not enter its TypeScript/Postgres owner",
            )
            require(
                payload.get("unexpected_python_proxy") is not True,
                f"{approval_path} returned Python observer",
            )
            require(
                "no-store" in cache_control.lower(),
                f"{approval_path} response is cacheable",
            )
            statuses[f"POST {approval_path}"] = status

            with PythonObserver.lock:
                production_upstream_hits = PythonObserver.hits
            require(production_upstream_hits == 0, "production Next reached the Python observer")

            stop_process(process)
            process = None
            local_environment = dict(environment)
            local_environment["AGENTOPS_DEPLOYMENT_MODE"] = "free_local"
            local_environment["AGENTOPS_CONTROL_PLANE_MODE"] = "proxy"
            local_port = free_port()
            local_base_url = f"http://127.0.0.1:{local_port}"
            local_start_log = temp_root / "free-local-start.log"
            process = start_app(str(npm), isolated_app, local_environment, local_port, local_start_log)
            wait_until_ready(local_base_url, process, local_start_log)

            with PythonObserver.lock:
                local_hits_before = PythonObserver.hits
            blocked_mutations: dict[str, int] = {}
            for path in (
                "/api/mis/workflows/hermes-openclaw-loop",
                "/api/mis/workflows/coding-workspace/cleanup",
                "/api/mis/workflows/coding-workspace/branch/delete",
            ):
                status, payload, cache_control = request_json(f"{local_base_url}{path}", "POST")
                require(status == 403, f"Free Local dangerous mutation returned {status}: {path}")
                require(
                    payload.get("error") == "free_local_python_proxy_path_not_allowed",
                    f"Free Local dangerous mutation lost deny reason: {path}",
                )
                require(payload.get("python_proxy_performed") is False, f"Free Local mutation proxied: {path}")
                require("no-store" in cache_control.lower(), f"Free Local mutation deny is cacheable: {path}")
                blocked_mutations[path] = status
            with PythonObserver.lock:
                local_hits_after_denies = PythonObserver.hits
            require(
                local_hits_after_denies == local_hits_before,
                "Free Local dangerous mutation reached the upstream observer",
            )

            status, payload, _cache_control = request_json(
                f"{local_base_url}/api/mis/workflows/local-brief",
                "POST",
            )
            require(status == 200, f"Free Local allowlisted local brief returned {status}")
            require(payload.get("unexpected_python_proxy") is True, "Free Local allowlisted path did not proxy")
            with PythonObserver.lock:
                local_hits_after_allow = PythonObserver.hits
            require(
                local_hits_after_allow == local_hits_after_denies + 1,
                "Free Local allowlisted mutation did not make exactly one upstream request",
            )

            source_diff_after = tracked_diff_digest()
            require(source_diff_after == source_diff_before, "isolated build mutated tracked source files")
            print(json.dumps({
                "contract": "nextjs_production_python_proxy_fail_closed_v5",
                "ok": True,
                "production_artifact_built": True,
                "production_artifact_started_through_npm_start": True,
                "postgres_migrations_packaged": True,
                "production_schema_readiness": True,
                "runtime_credentials_file_backed": bool(
                    os.environ.get("AGENTOPS_POSTGRES_DSN_FILE", "").strip()
                ),
                "python_api_started": False,
                "python_proxy_performed": False,
                "production_upstream_request_count": production_upstream_hits,
                "route_statuses": statuses,
                "free_local_loopback_start_verified": True,
                "free_local_blocked_mutations": blocked_mutations,
                "free_local_dangerous_mutation_upstream_request_count": (
                    local_hits_after_denies - local_hits_before
                ),
                "free_local_allowlisted_mutation_upstream_request_count": (
                    local_hits_after_allow - local_hits_after_denies
                ),
                "tracked_source_unchanged": True,
                "token_omitted": True,
            }, indent=2, sort_keys=True))
            return 0
    finally:
        stop_process(process)
        observer.shutdown()
        observer.server_close()
        observer_thread.join(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
