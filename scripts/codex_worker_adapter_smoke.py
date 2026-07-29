#!/usr/bin/env python3
"""Exercise the remote-shaped Codex worker path with a deterministic fake CLI."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agentops_mis_cli.codex_runtime import codex_preflight, execute_codex_read_only
from agentops_mis_cli.worker import adapter_capability_profile, worker_external_write_intent
from scripts.remote_agent_token_worker_smoke import runtime_attestation


SERVER = ROOT / "server.py"
REMOTE_SMOKE = ROOT / "scripts" / "remote_agent_token_worker_smoke.py"
CLI = ROOT / "scripts" / "agentops"


FAKE_CODEX = r'''#!/usr/bin/env python3
import json
import os
import sys

if "--version" in sys.argv:
    print("codex-cli deterministic-fixture")
    raise SystemExit(0)

required = ["exec", "--json", "--ephemeral", "--ignore-user-config", "--strict-config", "--skip-git-repo-check", "--sandbox", "read-only", "-C", "-"]
if any(value not in sys.argv for value in required):
    print("missing bounded Codex flags", file=sys.stderr)
    raise SystemExit(2)
if 'web_search="disabled"' not in sys.argv:
    print("web search was not disabled", file=sys.stderr)
    raise SystemExit(6)
for feature in ["apps", "browser_use", "computer_use", "goals", "hooks", "image_generation", "multi_agent", "plugins", "shell_tool", "unified_exec"]:
    if not any(sys.argv[index:index + 2] == ["--disable", feature] for index in range(len(sys.argv) - 1)):
        print(f"feature was not disabled: {feature}", file=sys.stderr)
        raise SystemExit(7)
if "AGENTOPS_API_KEY" in os.environ:
    print("AgentOps token reached Codex child", file=sys.stderr)
    raise SystemExit(3)
prompt = sys.stdin.read()
if not prompt or "AgentOps MIS" not in prompt:
    print("prompt was not delivered over stdin", file=sys.stderr)
    raise SystemExit(4)
if any("AgentOps MIS" in arg for arg in sys.argv):
    print("prompt leaked into argv", file=sys.stderr)
    raise SystemExit(5)

events = [
    {"type": "thread.started", "thread_id": "thr_fixture"},
    {"type": "turn.started"},
    {"type": "item.completed", "item": {"id": "msg_1", "type": "agent_message", "text": "Codex fixture completed a read-only customer analysis with risks and next actions."}},
    {"type": "turn.completed", "usage": {"input_tokens": 21, "output_tokens": 13}},
]
for event in events:
    print(json.dumps(event, separators=(",", ":")))
'''

FAKE_CODEX_PROHIBITED = r'''#!/usr/bin/env python3
import json
import sys

if "--version" in sys.argv:
    print("codex-cli prohibited-fixture")
    raise SystemExit(0)
sys.stdin.read()
events = [
    {"type": "thread.started", "thread_id": "thr_prohibited"},
    {"type": "turn.started"},
    {"type": "item.completed", "item": {"id": "cmd_1", "type": "command_execution", "command": "pwd", "status": "completed"}},
    {"type": "item.completed", "item": {"id": "msg_1", "type": "agent_message", "text": "This result must be rejected."}},
    {"type": "turn.completed", "usage": {"output_tokens": 4}},
]
for event in events:
    print(json.dumps(event, separators=(",", ":")))
'''

FAKE_CODEX_MALFORMED = r'''#!/usr/bin/env python3
import json
import sys
if "--version" in sys.argv:
    print("codex-cli malformed-fixture")
    raise SystemExit(0)
sys.stdin.read()
print(json.dumps({"type": "thread.started", "thread_id": "thr_malformed"}))
print("not-json")
print(json.dumps({"type": "turn.started"}))
print(json.dumps({"type": "item.completed", "item": {"id": "msg_1", "type": "agent_message", "text": "Must fail closed."}}))
print(json.dumps({"type": "turn.completed", "usage": {"output_tokens": 3}}))
'''

FAKE_CODEX_RECOVERED = r'''#!/usr/bin/env python3
import json
import sys
if "--version" in sys.argv:
    print("codex-cli recovered-fixture")
    raise SystemExit(0)
sys.stdin.read()
events = [
    {"type": "thread.started", "thread_id": "thr_recovered"},
    {"type": "turn.started"},
    {"type": "error", "message": "Reconnecting after a transient transport interruption."},
    {"type": "item.completed", "item": {"id": "err_1", "type": "error", "message": "Transient transport interruption recovered."}},
    {"type": "item.completed", "item": {"id": "msg_1", "type": "agent_message", "text": "Codex fixture completed after a recovered transport interruption."}},
    {"type": "turn.completed", "usage": {"output_tokens": 7}},
]
for event in events:
    print(json.dumps(event, separators=(",", ":")))
'''


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_ready(base_url: str, proc: subprocess.Popen[str]) -> None:
    deadline = time.time() + 45
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"isolated server exited with {proc.returncode}")
        try:
            with urlopen(base_url + "/api/local/readiness", timeout=2) as response:
                if response.status == 200:
                    return
        except Exception:
            time.sleep(0.25)
    raise RuntimeError("isolated server did not become ready")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    codex_args = SimpleNamespace(adapter="codex", confirm_run=True)
    codex_capability = adapter_capability_profile("codex")
    require(
        worker_external_write_intent(
            {
                "title": "Read-only review",
                "description": "Do not modify files or use the network.",
                "acceptance_criteria": "Return a concise analysis.",
            },
            codex_args,
            codex_capability,
        ) is False,
        "The Dify keyword must not match the word 'modify'.",
    )
    require(
        worker_external_write_intent(
            {
                "title": "Publish knowledge base",
                "description": "Upload the approved content to Dify.",
                "acceptance_criteria": "The external dataset is updated.",
            },
            codex_args,
            codex_capability,
        ) is True,
        "Explicit Dify upload intent must require a prepared action.",
    )
    with tempfile.TemporaryDirectory(prefix="agentops-codex-worker-") as tmp:
        temp = Path(tmp)
        fake_bin = temp / "codex-fixture"
        fake_bin.write_text(FAKE_CODEX, encoding="utf-8")
        fake_bin.chmod(0o700)
        prohibited_bin = temp / "codex-prohibited-fixture"
        prohibited_bin.write_text(FAKE_CODEX_PROHIBITED, encoding="utf-8")
        prohibited_bin.chmod(0o700)
        malformed_bin = temp / "codex-malformed-fixture"
        malformed_bin.write_text(FAKE_CODEX_MALFORMED, encoding="utf-8")
        malformed_bin.chmod(0o700)
        recovered_bin = temp / "codex-recovered-fixture"
        recovered_bin.write_text(FAKE_CODEX_RECOVERED, encoding="utf-8")
        recovered_bin.chmod(0o700)
        preflight = codex_preflight(binary_path=str(fake_bin), cwd=ROOT, timeout=5)
        require(preflight.get("ok") is True, f"Codex preflight failed: {preflight}")
        fixture_attestation = runtime_attestation(SimpleNamespace(adapter="codex", codex_bin=str(fake_bin), confirm_run=True))
        require(fixture_attestation.get("attested") is False, f"fixture binary was incorrectly attested: {fixture_attestation}")
        prior_api_key = os.environ.get("AGENTOPS_API_KEY")
        os.environ["AGENTOPS_API_KEY"] = "fixture-agentops-token-must-not-propagate"
        try:
            prohibited = execute_codex_read_only(
                binary_path=str(prohibited_bin),
                prompt="AgentOps MIS prohibited-event fixture",
                cwd=ROOT,
                timeout=10,
            )
        finally:
            if prior_api_key is None:
                os.environ.pop("AGENTOPS_API_KEY", None)
            else:
                os.environ["AGENTOPS_API_KEY"] = prior_api_key
        require(prohibited.ok is False, "prohibited Codex tool event unexpectedly passed")
        require(prohibited.error_type == "CodexProhibitedToolEvent", f"wrong prohibited-event failure: {prohibited}")
        require((prohibited.observation or {}).get("prohibited_event_count") == 1, f"prohibited-event count missing: {prohibited}")
        malformed = execute_codex_read_only(
            binary_path=str(malformed_bin),
            prompt="AgentOps MIS malformed-event fixture",
            cwd=ROOT,
            timeout=10,
        )
        require(malformed.ok is False, "malformed Codex JSONL unexpectedly passed")
        require(malformed.error_type == "CodexProtocolViolation", f"wrong malformed-event failure: {malformed}")
        recovered = execute_codex_read_only(
            binary_path=str(recovered_bin),
            prompt="AgentOps MIS recovered-event fixture",
            cwd=ROOT,
            timeout=10,
        )
        require(recovered.ok is True, f"recovered Codex event stream unexpectedly failed: {recovered}")
        require(
            (recovered.observation or {}).get("runtime_error_recovered") is True,
            f"recovered runtime event was not retained as evidence: {recovered}",
        )
        require(
            (recovered.observation or {}).get("runtime_error_event_count") == 1,
            f"recovered runtime event count missing: {recovered}",
        )
        port = free_port()
        base_url = f"http://127.0.0.1:{port}"
        env = os.environ.copy()
        env.pop("AGENTOPS_API_KEY", None)
        env["AGENTOPS_DB_PATH"] = str(temp / "agentops.db")
        env["AGENTOPS_SKIP_SEED_EXPORTS"] = "1"
        env["AGENTOPS_DEPLOYMENT_MODE"] = "local"
        server = subprocess.Popen(
            [sys.executable, str(SERVER), "--host", "127.0.0.1", "--port", str(port), "--serve"],
            cwd=ROOT,
            env=env,
            stdout=(temp / "server.log").open("w"),
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            wait_ready(base_url, server)
            proc = subprocess.run(
                [
                    sys.executable,
                    str(REMOTE_SMOKE),
                    "--base-url",
                    base_url,
                    "--adapter",
                    "codex",
                    "--confirm-run",
                    "--codex-bin",
                    str(fake_bin),
                    "--evidence-class",
                    "deterministic_fixture",
                    "--timeout",
                    "180",
                ],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=240,
                check=False,
            )
            cli_proc = subprocess.run(
                [
                    str(CLI),
                    "--base-url",
                    base_url,
                    "workflow",
                    "run-task",
                    "--adapter",
                    "codex",
                    "--confirm-run",
                    "--worker-agent-id",
                    "agt_codex_workflow_fixture",
                    "--title",
                    "Codex workflow CLI fixture",
                    "--description",
                    "Return a bounded read-only delivery assessment through the public workflow CLI.",
                    "--risk",
                    "low",
                    "--codex-bin",
                    str(fake_bin),
                    "--codex-timeout",
                    "60",
                ],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
            )
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=10)
        server_log = (temp / "server.log").read_text(encoding="utf-8", errors="replace")

    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise AssertionError(f"remote Codex smoke did not emit JSON: {proc.stdout}") from exc
    require(proc.returncode == 0, f"remote Codex smoke failed: {proc.stderr or proc.stdout}")
    require(payload.get("ok") is True, f"Codex worker did not complete: {payload}")
    require(payload.get("adapter") == "codex", f"wrong adapter: {payload}")
    require(payload.get("run_status") == "completed", f"run not completed: {payload}")
    require(payload.get("short_lived_session_used") is True, f"short-lived session missing: {payload}")
    require(payload.get("revocation_verified") is True, f"token revocation was not verified: {payload}")
    require(payload.get("agent_runtime_type_verified") is True, f"Codex agent runtime type was not preserved: {payload}")
    require(payload.get("launch_adapter_verified") is True, f"Codex launch adapter was not preserved: {payload}")
    require(payload.get("plan_evidence_pass") is True, f"plan evidence did not pass: {payload}")
    require(payload.get("tool_calls", 0) >= 1, f"tool evidence missing: {payload}")
    require(payload.get("evaluations", 0) >= 1, f"evaluation evidence missing: {payload}")
    require(payload.get("runtime_events", 0) >= 1, f"runtime evidence missing: {payload}")
    require(payload.get("artifacts", 0) >= 1, f"artifact evidence missing: {payload}")
    require(payload.get("memories", 0) >= 1, f"memory candidate missing: {payload}")
    require(payload.get("audit_logs", 0) >= 1, f"audit evidence missing: {payload}")
    require(payload.get("evidence_class") == "deterministic_fixture", f"fixture label missing: {payload}")
    require(payload.get("product_readiness_proof") is False, f"fixture must not claim product readiness: {payload}")
    try:
        cli_payload = json.loads(cli_proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise AssertionError(f"Codex workflow CLI did not emit JSON: {cli_proc.stdout}") from exc
    require(cli_proc.returncode == 0, f"Codex workflow CLI failed: {cli_proc.stderr or cli_proc.stdout}")
    require(cli_payload.get("ok") is True, f"Codex workflow CLI did not complete: {cli_payload}")
    require(cli_payload.get("adapter") == "codex", f"Codex workflow CLI adapter mismatch: {cli_payload}")
    require(cli_payload.get("run_status") == "completed", f"Codex workflow CLI run not completed: {cli_payload}")
    require((cli_payload.get("agent_plan") or {}).get("verified") is True, f"Codex workflow CLI plan not verified: {cli_payload}")
    require((cli_payload.get("plan_evidence") or {}).get("verified") is True, f"Codex workflow CLI manifest not verified: {cli_payload}")
    combined = "\n".join([proc.stdout, proc.stderr, cli_proc.stdout, cli_proc.stderr, server_log])
    require("Authorization:" not in combined and "Bearer " not in combined, "secret-like authorization leaked")
    print(json.dumps({
        "ok": True,
        "operation": "codex_worker_adapter_smoke",
        "run_id": payload.get("run_id"),
        "plan_id": payload.get("plan_id"),
        "plan_evidence_manifest_id": payload.get("plan_evidence_manifest_id"),
        "workflow_cli_run_id": cli_payload.get("run_id"),
        "short_lived_session_used": True,
        "evidence": {
            "tool_calls": payload.get("tool_calls"),
            "evaluations": payload.get("evaluations"),
            "runtime_events": payload.get("runtime_events"),
            "artifacts": payload.get("artifacts"),
            "memories": payload.get("memories"),
            "audit_logs": payload.get("audit_logs"),
        },
        "evidence_class": "deterministic_fixture",
        "prohibited_tool_event_blocked": True,
        "product_readiness_proof": False,
        "secret_leaked": False,
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
