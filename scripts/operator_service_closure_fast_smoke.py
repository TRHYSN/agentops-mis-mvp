#!/usr/bin/env python3
"""Verify fast service-closure records receipt/readback without loop-supervision pre-read."""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "agentops"
SECRET_PATTERNS = [
    re.compile(r"Authorization:", re.IGNORECASE),
    re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE),
    re.compile(r"agtok_[A-Za-z0-9_-]{16,}"),
    re.compile(r"agtsess_[A-Za-z0-9_-]{16,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"ntn_[A-Za-z0-9_-]{8,}"),
    re.compile(r"AGENTOPS_API_KEY=", re.IGNORECASE),
]


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def http_json(base_url: str, path: str, query: dict | None = None) -> tuple[int, dict]:
    url = base_url.rstrip("/") + path
    if query:
        url += "?" + urlencode(query, doseq=True)
    req = Request(url, headers={"Accept": "application/json"})
    try:
        with urlopen(req, timeout=30) as res:
            raw = res.read().decode("utf-8")
            return res.status, json.loads(raw) if raw else {}
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(raw)
        except Exception:
            return exc.code, {"raw": raw}


def wait_ready(base_url: str, proc: subprocess.Popen[str]) -> None:
    deadline = time.time() + 45
    last_error = ""
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"server exited early with code {proc.returncode}")
        try:
            status, _ = http_json(base_url, "/api/operator/action-plan", {"limit": 1})
            if status == 200:
                return
        except URLError as exc:
            last_error = str(exc)
        time.sleep(0.5)
    raise RuntimeError(f"server did not become ready: {last_error}")


def load_json(raw: str) -> dict:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def run_cli(args: list[str], env: dict, outputs: list[str], *, expected: int = 0) -> dict:
    proc = subprocess.run(
        [str(CLI), *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    outputs.extend([proc.stdout, proc.stderr])
    if proc.returncode != expected:
        return {"_error": f"exit {proc.returncode}", "stdout": proc.stdout, "stderr": proc.stderr}
    payload = load_json(proc.stdout)
    if not payload:
        return {"_error": "no json", "stdout": proc.stdout, "stderr": proc.stderr}
    return payload


def write_launchd_service_fixture(path: Path, adapter: str, base_url: str) -> None:
    path.write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.agentops.worker.{adapter}</string>
  <key>ProgramArguments</key>
  <array>
    <string>agentops-worker</string>
    <string>--adapter</string>
    <string>{adapter}</string>
    <string>--confirm-run</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>AGENTOPS_BASE_URL</key>
    <string>{base_url}</string>
  </dict>
  <key>KeepAlive</key>
  <true/>
</dict>
</plist>
""",
        encoding="utf-8",
    )


def supervision_item(base_url: str, adapter: str, failures: list[str], outputs: list[str]) -> dict:
    status, payload = http_json(
        base_url,
        "/api/operator/loop-supervision",
        {"adapter": adapter, "limit": 8, "include_codex": "false"},
    )
    outputs.append(json.dumps(payload, ensure_ascii=False))
    require(status == 200, f"loop-supervision status mismatch for {adapter}: {status} {payload}", failures)
    items = payload.get("items") if isinstance(payload.get("items"), list) else []
    item = next((row for row in items if isinstance(row, dict) and row.get("adapter") == adapter), {})
    require(bool(item), f"loop-supervision item missing for {adapter}: {payload}", failures)
    return item


def leaked_secret(text: str) -> bool:
    return any(pattern.search(text) for pattern in SECRET_PATTERNS)


def main() -> int:
    failures: list[str] = []
    outputs: list[str] = []
    adapter = "hermes"
    with tempfile.TemporaryDirectory(prefix="agentops-service-closure-fast-") as tmp:
        tmp_path = Path(tmp)
        db_path = tmp_path / "agentops_mis.db"
        port = free_port()
        base_url = f"http://127.0.0.1:{port}"
        env = os.environ.copy()
        env["AGENTOPS_DB_PATH"] = str(db_path)
        env["AGENTOPS_SKIP_SEED_EXPORTS"] = "1"
        env["AGENTOPS_CONFIG"] = str(tmp_path / "config.json")
        env["AGENTOPS_BASE_URL"] = base_url
        env.pop("AGENTOPS_API_KEY", None)
        proc = subprocess.Popen(
            [sys.executable, "server.py", "--host", "127.0.0.1", "--port", str(port), "--reset", "--serve"],
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            wait_ready(base_url, proc)
            before = supervision_item(base_url, adapter, failures, outputs)
            before_closure = before.get("service_closure") if isinstance(before.get("service_closure"), dict) else {}
            require(before_closure.get("required") is True, f"fast smoke should start with required service closure: {before_closure}", failures)

            service_path = tmp_path / f"{adapter}.plist"
            write_launchd_service_fixture(service_path, adapter, base_url)
            poisoned_preview = run_cli(
                [
                    "--base-url",
                    base_url,
                    "--agent-id",
                    "agt_acceptance_client_must_not_become_service_identity",
                    "operator",
                    "service-closure",
                    "--adapter",
                    adapter,
                    "--fast",
                    "--run-service-check",
                    "--service-path",
                    str(service_path),
                ],
                env,
                outputs,
            )
            poisoned_planned = poisoned_preview.get("planned_receipt") if isinstance(poisoned_preview.get("planned_receipt"), dict) else {}
            poisoned_commands = " ".join(
                str(poisoned_planned.get(key) or "")
                for key in ("action_command", "verify_command")
            )
            require(
                f"--agent-id agt_worker_daemon_{adapter}" in poisoned_commands,
                f"fast closure trusted an unrelated CLI Agent as the service identity: {poisoned_planned}",
                failures,
            )
            require(
                "agt_acceptance_client_must_not_become_service_identity" not in poisoned_commands,
                f"fast closure leaked the unrelated CLI Agent into service commands: {poisoned_planned}",
                failures,
            )
            preview = run_cli(
                [
                    "--base-url",
                    base_url,
                    "operator",
                    "service-closure",
                    "--adapter",
                    adapter,
                    "--fast",
                    "--run-service-check",
                    "--service-path",
                    str(service_path),
                ],
                env,
                outputs,
            )
            require(preview.get("operation") == "operator_service_closure", f"fast preview operation mismatch: {preview}", failures)
            require(preview.get("mode") == "fast", f"fast preview mode missing: {preview}", failures)
            require(preview.get("status") == "preview", f"fast preview status mismatch: {preview}", failures)
            require((preview.get("safety") or {}).get("ledger_mutated") is False, f"fast preview mutated ledger: {preview}", failures)
            require((preview.get("safety") or {}).get("loop_supervision_read") is False, f"fast preview should skip loop-supervision: {preview}", failures)
            require((preview.get("safety") or {}).get("local_cli_service_check_performed") is True, f"fast preview did not run local service-check: {preview}", failures)
            planned = preview.get("planned_receipt") if isinstance(preview.get("planned_receipt"), dict) else {}
            require(planned.get("action_id") == f"local_readiness.service_control_preview.{adapter}", f"fast preview must use canonical action id: {planned}", failures)

            record = run_cli(
                [
                    "--base-url",
                    base_url,
                    "operator",
                    "service-closure",
                    "--adapter",
                    adapter,
                    "--fast",
                    "--run-service-check",
                    "--service-path",
                    str(service_path),
                    "--actor-id",
                    "usr_fast_service_closure_smoke",
                    "--confirm-record",
                ],
                env,
                outputs,
            )
            require(record.get("status") == "recorded", f"fast record status mismatch: {record}", failures)
            require(record.get("recorded") is True, f"fast record marker missing: {record}", failures)
            require(bool(record.get("receipt_id")), f"fast record receipt id missing: {record}", failures)
            safety = record.get("safety") if isinstance(record.get("safety"), dict) else {}
            require(safety.get("ledger_mutated") is True, f"fast record should mutate receipt ledger: {record}", failures)
            require(safety.get("loop_supervision_read") is False, f"fast record should skip loop-supervision pre-read: {record}", failures)
            require(safety.get("server_executes_shell") is False, f"fast record server-shell proof missing: {record}", failures)
            require(safety.get("live_execution_performed") is False, f"fast record live proof missing: {record}", failures)
            after = ((record.get("control_readback_preview") or {}).get("after") or {})
            require(after.get("service_check_ok") is True, f"fast record service-check proof missing: {after}", failures)
            require(after.get("service_file_exists") is True, f"fast record service-file proof missing: {after}", failures)

            readback = supervision_item(base_url, adapter, failures, outputs)
            service_loop = ((readback.get("local_deployment") or {}).get("service_managed_loop") or {})
            closure = readback.get("service_closure") if isinstance(readback.get("service_closure"), dict) else {}
            require(service_loop.get("receipt_verified") is True, f"fast receipt not visible to loop-supervision: {service_loop}", failures)
            require(service_loop.get("control_readback_attached") is True, f"fast readback not visible to loop-supervision: {service_loop}", failures)
            require(service_loop.get("service_file_exists") is True, f"fast service file not projected: {service_loop}", failures)
            require(service_loop.get("service_loaded") in {True, False}, f"fast service loaded state missing: {service_loop}", failures)
            if service_loop.get("service_loaded") is True:
                require(closure.get("required") is False, f"loaded fast closure should pass: {closure}", failures)
                require(service_loop.get("service_managed_loop_ready") is True, f"loaded fast service loop should be ready: {service_loop}", failures)
            else:
                require(closure.get("step") == "confirm_service_control_load", f"unloaded fast closure should advance to activation: {closure}", failures)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)
            if proc.stdout:
                outputs.append(proc.stdout.read() or "")
            if proc.stderr:
                outputs.append(proc.stderr.read() or "")
    combined = "\n".join(outputs)
    require(not leaked_secret(combined), "secret-like material leaked in fast service-closure smoke", failures)
    result = {
        "ok": not failures,
        "operation": "operator_service_closure_fast_smoke",
        "failures": failures,
        "secret_leaked": leaked_secret(combined),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
