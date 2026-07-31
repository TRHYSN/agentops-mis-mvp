#!/usr/bin/env python3
"""Smoke-test the root AgentOps CLI Research Lab delegation contract."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "agentops"
SECRET = "experiment_cli_secret_value_123"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def run_cli(arguments: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(CLI), *arguments],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def payload(process: subprocess.CompletedProcess[str]) -> dict:
    try:
        value = json.loads(process.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"CLI returned non-JSON output: {process.stdout!r} {process.stderr!r}") from exc
    require(isinstance(value, dict), "CLI payload must be an object")
    return value


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="agentops-experiment-cli-") as tmp:
        temp = Path(tmp)
        fake = temp / "research-lab"
        fake.write_text(
            """#!/usr/bin/env python3
import json
import os
import sys

markers = ("API_KEY", "AUTHORIZATION", "CREDENTIAL", "PASSWORD", "PRIVATE_KEY", "SECRET", "SESSION_TOKEN", "TOKEN")
api_key = os.environ.get("AGENTOPS_API_KEY", "")
print(json.dumps({
    "ok": True,
    "operation": "fake_research_lab",
    "argv": sys.argv[1:],
    "api_key_present": bool(api_key),
    "api_key_value": api_key,
    "other_sensitive_environment_present": any(
        any(marker in key.upper() for marker in markers)
        for key in os.environ
        if key != "AGENTOPS_API_KEY"
    ),
    "agentops_config_present": "AGENTOPS_CONFIG" in os.environ,
    "normal_environment_preserved": os.environ.get("NORMAL_VISIBLE") == "yes",
}))
""",
            encoding="utf-8",
        )
        fake.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)

        env = os.environ.copy()
        env["AGENTOPS_CONFIG"] = str(temp / "config.json")
        env["AGENTOPS_RESEARCH_LAB_BIN"] = str(fake)
        env["AGENTOPS_API_KEY"] = SECRET
        env["DIFY_KB_API_KEY"] = SECRET
        env["EXPERIMENT_SESSION_TOKEN"] = SECRET
        env["NORMAL_VISIBLE"] = "yes"

        cases = [
            (
                "validate",
                ["experiment", "validate", "--spec", "/tmp/spec.json", "--servers", "/tmp/servers.json"],
                ["validate-spec", "--spec", "/tmp/spec.json", "--servers", "/tmp/servers.json"],
                False,
            ),
            (
                "run_dry",
                ["experiment", "run", "--spec", "/tmp/spec.json", "--state-dir", "/tmp/state"],
                ["run-local", "--spec", "/tmp/spec.json", "--state-dir", "/tmp/state"],
                False,
            ),
            (
                "run_confirmed",
                ["experiment", "run", "--spec", "/tmp/spec.json", "--state-dir", "/tmp/state", "--confirm-run"],
                ["run-local", "--spec", "/tmp/spec.json", "--state-dir", "/tmp/state", "--confirm-run"],
                True,
            ),
            (
                "show",
                ["experiment", "show", "--experiment-id", "exp_smoke", "--state-dir", "/tmp/state"],
                ["show", "--experiment-id", "exp_smoke", "--state-dir", "/tmp/state"],
                False,
            ),
            (
                "sync_dry",
                [
                    "--base-url",
                    "http://127.0.0.1:9876",
                    "--workspace-id",
                    "research-smoke",
                    "experiment",
                    "sync",
                    "--experiment-id",
                    "exp_smoke",
                    "--state-dir",
                    "/tmp/state",
                ],
                [
                    "sync-mis",
                    "--experiment-id",
                    "exp_smoke",
                    "--state-dir",
                    "/tmp/state",
                    "--workspace-id",
                    "research-smoke",
                    "--base-url",
                    "http://127.0.0.1:9876",
                ],
                False,
            ),
            (
                "sync_confirmed",
                [
                    "--base-url",
                    "http://127.0.0.1:9876",
                    "--workspace-id",
                    "research-smoke",
                    "experiment",
                    "sync",
                    "--experiment-id",
                    "exp_smoke",
                    "--state-dir",
                    "/tmp/state",
                    "--confirm-sync",
                ],
                [
                    "sync-mis",
                    "--experiment-id",
                    "exp_smoke",
                    "--state-dir",
                    "/tmp/state",
                    "--workspace-id",
                    "research-smoke",
                    "--base-url",
                    "http://127.0.0.1:9876",
                    "--confirm-sync",
                ],
                True,
            ),
        ]

        results: dict[str, dict] = {}
        combined_output = ""
        for name, arguments, expected_argv, confirmed in cases:
            process = run_cli(arguments, env)
            combined_output += process.stdout + process.stderr
            value = payload(process)
            require(process.returncode == 0, f"{name} failed: {process.stderr or process.stdout}")
            require(value.get("argv") == expected_argv, f"{name} delegated wrong argv: {value.get('argv')}")
            require(value.get("delegated_by") == "agentops experiment", f"{name} missing delegation marker")
            require(value.get("delegate_source") == "configured_executable", f"{name} wrong delegate source")
            require(value.get("token_omitted") is True, f"{name} missing token omission marker")
            expects_api_key = name.startswith("sync")
            require(value.get("api_key_present") is expects_api_key, f"{name} API key delegation mismatch")
            require(
                value.get("api_key_value") == ("[SECRET_REDACTED]" if expects_api_key else ""),
                f"{name} API key output was not omitted",
            )
            require(value.get("other_sensitive_environment_present") is False, f"{name} leaked unrelated sensitive environment")
            require(value.get("agentops_config_present") is False, f"{name} leaked AgentOps config path")
            require(value.get("normal_environment_preserved") is True, f"{name} removed ordinary environment")
            delegated_flags = set(value.get("argv") or [])
            if name.startswith("run"):
                require(("--confirm-run" in delegated_flags) is confirmed, f"{name} confirmation mapping failed")
            if name.startswith("sync"):
                require(("--confirm-sync" in delegated_flags) is confirmed, f"{name} confirmation mapping failed")
            results[name] = {
                "returncode": process.returncode,
                "delegate_action": value.get("delegate_action"),
                "argv": value.get("argv"),
            }

        missing_env = env.copy()
        missing_env["AGENTOPS_RESEARCH_LAB_BIN"] = str(temp / "missing-research-lab")
        missing = run_cli(["experiment", "validate", "--spec", "/tmp/spec.json"], missing_env)
        combined_output += missing.stdout + missing.stderr
        require(missing.returncode == 1, "missing Research Lab executable must fail")
        require(
            "missing or not executable" in missing.stderr,
            f"missing executable error is not actionable: {missing.stderr!r}",
        )
        require(SECRET not in combined_output, "secret appeared in CLI output")

        result = {
            "ok": True,
            "operation": "agentops_experiment_cli_smoke",
            "cases": results,
            "missing_executable_failed_clearly": True,
            "dry_run_defaults_preserved": True,
            "api_key_environment_scoped_to_sync": True,
            "sensitive_environment_omitted": True,
            "token_omitted": True,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(f"agentops_experiment_cli_smoke FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
