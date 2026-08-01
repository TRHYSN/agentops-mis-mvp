#!/usr/bin/env python3
"""Verify the Host rejects unbounded or malformed JSON before route handling."""
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


def request_bytes(url: str, body: bytes) -> tuple[int, dict]:
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def raw_request(port: int, headers: list[str], body: bytes = b"") -> tuple[int, dict]:
    request = [
        "POST /api/human-auth/login HTTP/1.0",
        f"Host: 127.0.0.1:{port}",
        "Content-Type: application/json",
        *headers,
        "Connection: close",
        "",
        "",
    ]
    with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        sock.sendall("\r\n".join(request).encode("ascii") + body)
        sock.shutdown(socket.SHUT_WR)
        chunks: list[bytes] = []
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
    raw = b"".join(chunks)
    head, _, response_body = raw.partition(b"\r\n\r\n")
    status_line = head.split(b"\r\n", 1)[0].decode("ascii", errors="replace")
    status = int(status_line.split(" ", 2)[1])
    return status, json.loads(response_body.decode("utf-8"))


def main() -> int:
    failures: list[str] = []
    evidence: dict[str, object] = {}
    marker = "bounded-body-marker-must-not-be-echoed"
    with tempfile.TemporaryDirectory(prefix="agentops-bounded-json-") as tmp:
        port = free_port()
        base_url = f"http://127.0.0.1:{port}"
        env = os.environ.copy()
        env.update({
            "AGENTOPS_DB_PATH": str(Path(tmp) / "agentops_mis.db"),
            "AGENTOPS_SKIP_SEED_EXPORTS": "1",
            "AGENTOPS_DEPLOYMENT_MODE": "private_host",
            "AGENTOPS_HUMAN_AUTH_REQUIRED": "true",
            "AGENTOPS_ALLOWED_ORIGINS": base_url,
            "AGENTOPS_MAX_JSON_BODY_BYTES": "1024",
            "AGENTOPS_API_KEY": "fixture-machine-key",
            "AGENTOPS_ADMIN_KEY": "fixture-admin-key",
            "HERMES_ALLOW_REAL_RUN": "false",
        })
        process = subprocess.Popen(
            [sys.executable, "server.py", "--host", "127.0.0.1", "--port", str(port)],
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        process_output = ""
        try:
            deadline = time.time() + 30
            while time.time() < deadline and process.poll() is None:
                try:
                    with urllib.request.urlopen(base_url + "/health", timeout=2) as response:
                        if response.status == 200:
                            break
                except (OSError, urllib.error.URLError):
                    time.sleep(0.2)
            else:
                failures.append("temporary Host did not become ready")

            cases = {
                "oversized": request_bytes(
                    base_url + "/api/human-auth/login",
                    json.dumps({"padding": marker * 80}).encode("utf-8"),
                ),
                "malformed": request_bytes(base_url + "/api/human-auth/login", b'{"username":'),
                "non_object": request_bytes(base_url + "/api/human-auth/login", b"[]"),
                "invalid_length": raw_request(port, ["Content-Length: invalid"]),
                "incomplete": raw_request(port, ["Content-Length: 20"], b"{}"),
                "chunked": raw_request(
                    port,
                    ["Transfer-Encoding: chunked"],
                    b"2\r\n{}\r\n0\r\n\r\n",
                ),
            }
            expected = {
                "oversized": (413, "request_body_too_large"),
                "malformed": (400, "invalid_json_body"),
                "non_object": (400, "invalid_json_object"),
                "invalid_length": (400, "invalid_content_length"),
                "incomplete": (400, "incomplete_request_body"),
                "chunked": (400, "unsupported_transfer_encoding"),
            }
            for name, (status, payload) in cases.items():
                expected_status, expected_error = expected[name]
                evidence[name] = {
                    "status": status,
                    "error": payload.get("error"),
                    "body_omitted": payload.get("body_omitted"),
                }
                if status != expected_status or payload.get("error") != expected_error or payload.get("body_omitted") is not True:
                    failures.append(f"{name} JSON request did not fail with the bounded contract")
                if marker in json.dumps(payload, ensure_ascii=False):
                    failures.append(f"{name} response reflected request content")
        finally:
            process.terminate()
            try:
                stdout, stderr = process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate(timeout=5)
            process_output = (stdout or "") + (stderr or "")

        if marker in process_output:
            failures.append("temporary Host process output contained request content")

    print(json.dumps({
        "operation": "bounded_json_request_smoke",
        "ok": not failures,
        "failures": failures,
        "configured_limit_bytes": 1024,
        "evidence": evidence,
        "safety": {
            "temporary_database": True,
            "raw_body_omitted": True,
            "live_runtime_called": False,
            "credentials_used": False,
        },
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
