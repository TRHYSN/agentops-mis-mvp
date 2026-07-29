#!/usr/bin/env python3
"""Verify same-origin production UI serving without using the Vite dev server."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def fetch(url: str) -> tuple[int, dict, bytes]:
    request = urllib.request.Request(url)
    with urllib.request.urlopen(request, timeout=2) as response:
        return response.status, dict(response.headers), response.read()


def main() -> int:
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="agentops-production-ui-") as tmp:
        tmp_path = Path(tmp)
        ui_dist = tmp_path / "dist"
        assets = ui_dist / "assets"
        assets.mkdir(parents=True)
        (ui_dist / "index.html").write_text(
            '<!doctype html><html><body><div id="root">AGENTOPS_PRODUCTION_UI</div></body></html>',
            encoding="utf-8",
        )
        (assets / "app.js").write_text("window.AGENTOPS_PRODUCTION_UI = true;\n", encoding="utf-8")
        port = free_port()
        base_url = f"http://127.0.0.1:{port}"
        env = os.environ.copy()
        env.update(
            {
                "AGENTOPS_DB_PATH": str(tmp_path / "agentops_mis.db"),
                "AGENTOPS_SKIP_SEED_EXPORTS": "1",
                "HERMES_ALLOW_REAL_RUN": "false",
            }
        )
        process = subprocess.Popen(
            [sys.executable, "server.py", "--host", "127.0.0.1", "--port", str(port), "--ui-dist", str(ui_dist)],
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        responses: dict[str, tuple[int, dict, bytes]] = {}
        try:
            deadline = time.time() + 30
            while time.time() < deadline:
                if process.poll() is not None:
                    break
                try:
                    responses["root"] = fetch(base_url + "/")
                    break
                except (OSError, urllib.error.URLError):
                    time.sleep(0.2)
            if "root" not in responses:
                failures.append("production host did not become ready")
            for name, path in {
                "deep_route": "/workspace/pixel-office",
                "asset": "/assets/app.js",
                "same_origin_api": "/mis-api/agent-gateway/status",
                "canonical_api": "/api/agent-gateway/status",
            }.items():
                try:
                    responses[name] = fetch(base_url + path)
                except (OSError, urllib.error.URLError) as exc:
                    failures.append(f"{name} request failed: {type(exc).__name__}")
        finally:
            process.terminate()
            try:
                stdout, stderr = process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate(timeout=5)

        if b"AGENTOPS_PRODUCTION_UI" not in responses.get("root", (0, {}, b""))[2]:
            failures.append("root did not serve production index")
        if b"AGENTOPS_PRODUCTION_UI" not in responses.get("deep_route", (0, {}, b""))[2]:
            failures.append("SPA deep route did not fall back to production index")
        if b"window.AGENTOPS_PRODUCTION_UI" not in responses.get("asset", (0, {}, b""))[2]:
            failures.append("hashed-style asset path was not served")
        if "immutable" not in responses.get("asset", (0, {}, b""))[1].get("Cache-Control", ""):
            failures.append("asset cache policy was not immutable")
        if responses.get("root", (0, {}, b""))[1].get("Cache-Control") != "no-cache":
            failures.append("index cache policy was not no-cache")
        for name in ("same_origin_api", "canonical_api"):
            try:
                payload = json.loads(responses.get(name, (0, {}, b"{}"))[2].decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                payload = {}
            if payload.get("provider") != "agent_gateway":
                failures.append(f"{name} did not reach Agent Gateway")
        combined = (stdout or "") + (stderr or "")
        if any(marker in combined for marker in ("Authorization:", "Bearer ", "agtok_", "agtsess_", "sk-", "ntn_")):
            failures.append("host output contained token-like material")

    print(
        json.dumps(
            {
                "ok": not failures,
                "operation": "production_ui_host_smoke",
                "same_origin_api": "same_origin_api" in responses,
                "spa_fallback": "deep_route" in responses,
                "asset_served": "asset" in responses,
                "vite_runtime_required": False,
                "real_runtime_called": False,
                "token_omitted": True,
                "failures": failures,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
