#!/usr/bin/env python3
"""Verify operator service-closure records service loop receipt/readback safely."""
from __future__ import annotations

import json
import os
import re
import shlex
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


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def load_json(raw: str) -> dict:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def leaked_secret(text: str) -> bool:
    return any(pattern.search(text) for pattern in SECRET_PATTERNS)


def run_cli(args: list[str], env: dict, failures: list[str], outputs: list[str], *, expected: int = 0) -> dict:
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
    require(proc.returncode == expected, f"CLI exit mismatch for {args}: {proc.returncode} {proc.stderr}", failures)
    payload = load_json(proc.stdout)
    require(isinstance(payload, dict) and payload, f"CLI did not return JSON for {args}: {proc.stdout}", failures)
    return payload


def service_check_fixture(adapter: str) -> dict:
    return {
        "ok": True,
        "manager": "launchd",
        "label": f"com.agentops.worker.{adapter}",
        "agent_id": f"agt_worker_daemon_{adapter}",
        "workspace_id": "local-demo",
        "adapter": adapter,
        "service_path": f"/tmp/agentops-service-closure/{adapter}.plist",
        "service_file": {
            "exists": True,
            "command_has_worker": True,
            "adapter_present": True,
            "use_session_present": False,
            "local_dev_no_token": True,
            "relaunch_policy_ok": True,
            "confirm_gate_ok": True,
            "placeholder_present": False,
            "token_like_detected": False,
            "raw_content_omitted": True,
        },
        "relaunch_policy": {"enabled": True},
        "service_status": {"loaded": True},
        "token_omitted": True,
        "live_execution_performed": False,
    }


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


def main() -> int:
    failures: list[str] = []
    outputs: list[str] = []
    with tempfile.TemporaryDirectory(prefix="agentops-service-closure-cli-") as tmp:
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
            for adapter in ("hermes", "openclaw"):
                initial = supervision_item(base_url, adapter, failures, outputs)
                initial_closure = initial.get("service_closure") if isinstance(initial.get("service_closure"), dict) else {}
                require(initial_closure.get("required") is True, f"{adapter} should require service closure before smoke record: {initial_closure}", failures)

                before_preview = supervision_item(base_url, adapter, failures, outputs)
                preview = run_cli(
                    ["--base-url", base_url, "operator", "service-closure", "--adapter", adapter],
                    env,
                    failures,
                    outputs,
                )
                after_preview = supervision_item(base_url, adapter, failures, outputs)
                require(preview.get("operation") == "operator_service_closure", f"{adapter} wrong preview operation: {preview}", failures)
                require(preview.get("status") == "preview", f"{adapter} preview status mismatch: {preview}", failures)
                require(preview.get("recorded") is False, f"{adapter} preview should not record: {preview}", failures)
                require((preview.get("safety") or {}).get("ledger_mutated") is False, f"{adapter} preview mutated ledger: {preview}", failures)
                require((preview.get("safety") or {}).get("server_executes_shell") is False, f"{adapter} preview server shell safety missing: {preview}", failures)
                require((preview.get("control_readback_preview") or {}).get("after") is None, f"{adapter} preview should not fake after readback: {preview}", failures)
                require((before_preview.get("service_closure") or {}).get("required") == (after_preview.get("service_closure") or {}).get("required"), f"{adapter} preview changed closure state", failures)

                blocked = run_cli(
                    ["--base-url", base_url, "operator", "service-closure", "--adapter", adapter, "--confirm-record"],
                    env,
                    failures,
                    outputs,
                    expected=2,
                )
                require(blocked.get("status") == "blocked", f"{adapter} confirm without readback should block: {blocked}", failures)
                require("service_check_json" in (blocked.get("missing") or []), f"{adapter} missing service_check_json not reported: {blocked}", failures)

                service_path = tmp_path / f"{adapter}.plist"
                write_launchd_service_fixture(service_path, adapter, base_url)
                before_auto_preview = supervision_item(base_url, adapter, failures, outputs)
                auto_preview = run_cli(
                    [
                        "--base-url",
                        base_url,
                        "operator",
                        "service-closure",
                        "--adapter",
                        adapter,
                        "--run-service-check",
                        "--service-path",
                        str(service_path),
                    ],
                    env,
                    failures,
                    outputs,
                )
                after_auto_preview = supervision_item(base_url, adapter, failures, outputs)
                require(auto_preview.get("status") == "preview", f"{adapter} auto-check preview status mismatch: {auto_preview}", failures)
                require(auto_preview.get("recorded") is False, f"{adapter} auto-check preview should not record: {auto_preview}", failures)
                require((auto_preview.get("safety") or {}).get("ledger_mutated") is False, f"{adapter} auto-check preview mutated ledger: {auto_preview}", failures)
                require((auto_preview.get("safety") or {}).get("local_cli_service_check_performed") is True, f"{adapter} auto-check preview did not run local check: {auto_preview}", failures)
                auto_preview_check = auto_preview.get("service_check") if isinstance(auto_preview.get("service_check"), dict) else {}
                require(auto_preview_check.get("source") == "local_cli_service_check", f"{adapter} auto-check source missing: {auto_preview_check}", failures)
                require(auto_preview_check.get("service_file_exists") is True, f"{adapter} auto-check service file missing: {auto_preview_check}", failures)
                require(auto_preview_check.get("service_loaded") in {True, False}, f"{adapter} auto-check loaded state should be boolean: {auto_preview_check}", failures)
                require((before_auto_preview.get("service_closure") or {}).get("required") == (after_auto_preview.get("service_closure") or {}).get("required"), f"{adapter} auto-check preview changed closure state", failures)

                auto_record = run_cli(
                    [
                        "--base-url",
                        base_url,
                        "operator",
                        "service-closure",
                        "--adapter",
                        adapter,
                        "--run-service-check",
                        "--service-path",
                        str(service_path),
                        "--actor-id",
                        "usr_service_closure_auto_smoke",
                        "--confirm-record",
                    ],
                    env,
                    failures,
                    outputs,
                )
                require(auto_record.get("status") == "recorded", f"{adapter} auto-check record status mismatch: {auto_record}", failures)
                require((auto_record.get("safety") or {}).get("ledger_mutated") is True, f"{adapter} auto-check record should mutate ledger: {auto_record}", failures)
                require((auto_record.get("safety") or {}).get("local_cli_service_check_performed") is True, f"{adapter} auto-check record did not report local check: {auto_record}", failures)
                auto_readback = auto_record.get("control_readback_preview") if isinstance(auto_record.get("control_readback_preview"), dict) else {}
                auto_after = auto_readback.get("after") if isinstance(auto_readback.get("after"), dict) else {}
                require(auto_after.get("service_check_ok") is True, f"{adapter} auto-check should pass file/service policy checks: {auto_after}", failures)
                require(auto_after.get("service_file_exists") is True, f"{adapter} auto-check readback lacks file proof: {auto_after}", failures)
                require(auto_after.get("service_loaded") in {True, False}, f"{adapter} auto-check should report boolean loaded state: {auto_after}", failures)
                activation_needed = supervision_item(base_url, adapter, failures, outputs)
                activation_closure = activation_needed.get("service_closure") if isinstance(activation_needed.get("service_closure"), dict) else {}
                activation_loop = ((activation_needed.get("local_deployment") or {}).get("service_managed_loop") or {})
                activation_commands = activation_loop.get("commands") if isinstance(activation_loop.get("commands"), dict) else {}
                receipt_parts = shlex.split(str(activation_commands.get("record_verified_receipt") or ""))
                expected_signature = (
                    receipt_parts[receipt_parts.index("--action-signature") + 1]
                    if "--action-signature" in receipt_parts and receipt_parts.index("--action-signature") + 1 < len(receipt_parts)
                    else ""
                )
                planned_receipt = auto_record.get("planned_receipt") if isinstance(auto_record.get("planned_receipt"), dict) else {}
                require(bool(expected_signature), f"{adapter} canonical service signature missing: {activation_commands}", failures)
                require(planned_receipt.get("action_signature") == expected_signature, f"{adapter} CLI/server service signature mismatch: {planned_receipt}", failures)
                require(planned_receipt.get("action_command") == activation_commands.get("service_control_preview"), f"{adapter} CLI/server service command mismatch: {planned_receipt}", failures)
                require(activation_loop.get("receipt_verified") is True, f"{adapter} auto-check receipt should be verified: {activation_loop}", failures)
                require(activation_loop.get("control_readback_attached") is True, f"{adapter} auto-check readback should attach: {activation_loop}", failures)
                if activation_loop.get("service_loaded") is True:
                    require(activation_closure.get("required") is False, f"{adapter} loaded service should close service closure: {activation_closure}", failures)
                    require(activation_closure.get("status") == "pass", f"{adapter} loaded service should pass closure: {activation_closure}", failures)
                    require(activation_loop.get("service_managed_loop_ready") is True, f"{adapter} loaded service should make loop ready: {activation_loop}", failures)
                else:
                    require(activation_closure.get("required") is True, f"{adapter} unloaded service should still require activation: {activation_closure}", failures)
                    require(activation_closure.get("step") == "confirm_service_control_load", f"{adapter} auto-check should advance to load confirmation: {activation_closure}", failures)

                fixture_path = tmp_path / f"{adapter}-service-check.json"
                fixture_path.write_text(json.dumps(service_check_fixture(adapter), ensure_ascii=False), encoding="utf-8")
                record = run_cli(
                    [
                        "--base-url",
                        base_url,
                        "operator",
                        "service-closure",
                        "--adapter",
                        adapter,
                        "--service-check-json",
                        str(fixture_path),
                        "--actor-id",
                        "usr_service_closure_smoke",
                        "--confirm-record",
                    ],
                    env,
                    failures,
                    outputs,
                )
                require(record.get("status") == "recorded", f"{adapter} record status mismatch: {record}", failures)
                require(record.get("recorded") is True, f"{adapter} record marker missing: {record}", failures)
                require(bool(record.get("receipt_id")), f"{adapter} receipt id missing: {record}", failures)
                safety = record.get("safety") if isinstance(record.get("safety"), dict) else {}
                require(safety.get("ledger_mutated") is True, f"{adapter} record should mutate ledger: {record}", failures)
                require(safety.get("live_execution_performed") is False, f"{adapter} record must not run live execution: {record}", failures)
                require(safety.get("server_executes_shell") is False, f"{adapter} record must not execute shell: {record}", failures)
                readback = record.get("control_readback_preview") if isinstance(record.get("control_readback_preview"), dict) else {}
                after = readback.get("after") if isinstance(readback.get("after"), dict) else {}
                require(after.get("service_check_ok") is True, f"{adapter} service check readback not OK: {after}", failures)
                require(after.get("service_loaded") is True, f"{adapter} service loaded readback missing: {after}", failures)
                require(bool(record.get("control_readback_receipt")), f"{adapter} control readback receipt missing: {record}", failures)

                closed = supervision_item(base_url, adapter, failures, outputs)
                closed_closure = closed.get("service_closure") if isinstance(closed.get("service_closure"), dict) else {}
                service_loop = ((closed.get("local_deployment") or {}).get("service_managed_loop") or {})
                require(closed_closure.get("required") is False, f"{adapter} closure should be no longer required: {closed_closure}", failures)
                require(closed_closure.get("status") == "pass", f"{adapter} closure status should pass: {closed_closure}", failures)
                require(service_loop.get("receipt_verified") is True, f"{adapter} receipt not verified in service loop: {service_loop}", failures)
                require(service_loop.get("control_readback_attached") is True, f"{adapter} readback not attached in service loop: {service_loop}", failures)
                require(service_loop.get("service_loaded") is True, f"{adapter} service_loaded not projected: {service_loop}", failures)
                require(service_loop.get("service_managed_loop_ready") is True, f"{adapter} service loop not ready: {service_loop}", failures)
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
    if leaked_secret(combined):
        failures.append("secret-like material leaked in smoke output")
    if failures:
        print("operator_service_closure_cli_smoke FAILED")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("operator_service_closure_cli_smoke OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
