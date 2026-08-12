"""Prove the installed OpenCekura distribution works outside its checkout."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any, Mapping, Sequence


COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")
REPORT_MARKER = "OPEN_CEKURA_INSTALLED_ACCEPTANCE="


def _checkout_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _commit_argument(value: str) -> str:
    if COMMIT_SHA.fullmatch(value) is None:
        raise argparse.ArgumentTypeError(
            "expected commit must be a 40-character lowercase Git SHA"
        )
    return value


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Exercise an installed OpenCekura distribution in isolated mode."
    )
    parser.add_argument(
        "--expected-commit",
        type=_commit_argument,
        help="Require open_cekura/_build_commit.txt to equal this exact Git SHA.",
    )
    return parser.parse_args(argv)


def _probe_command(expected_commit: str | None) -> list[str]:
    expected_literal = json.dumps(expected_commit)
    probe = f'''
import json
from importlib.resources import files
import os
from pathlib import Path
import site
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.request import urlopen

import agentops_mis_runtime
import open_cekura
import server
from open_cekura.campaigns.service import (
    compare_campaigns,
    replay_campaign_regressions,
    run_campaign,
)
from open_cekura.evidence.manifest import verify_campaign
from open_cekura.scenarios.loader import load_scenario

expected_commit = {expected_literal}
package = files("open_cekura")
build_commit = package.joinpath("_build_commit.txt").read_text(
    encoding="ascii"
).strip()
if expected_commit is not None and build_commit != expected_commit:
    raise AssertionError(
        f"packaged build commit {{build_commit!r}} does not match "
        f"expected commit {{expected_commit!r}}"
    )
suite = Path(str(package.joinpath("resources/scenarios")))
scenario = load_scenario(suite / "basic.yaml")
assert scenario.id == "appointment.basic_success"
with tempfile.TemporaryDirectory(prefix="open-cekura-runtime-") as state:
    state_root = Path(state)
    database = state_root / "mis.db"
    artifacts = state_root / "artifacts"
    common = {{
        "suite_path": suite,
        "agent": "mock",
        "workspace_id": "installed-audit",
        "db_path": database,
        "artifact_root": artifacts,
    }}
    baseline = run_campaign(
        **common,
        version="baseline",
        campaign_id="occampaign_installed_baseline",
    )
    baseline_again = run_campaign(
        **common,
        version="baseline",
        campaign_id="occampaign_installed_baseline",
    )
    candidate = run_campaign(
        **common,
        version="candidate",
        campaign_id="occampaign_installed_candidate",
    )
    candidate_again = run_campaign(
        **common,
        version="candidate",
        campaign_id="occampaign_installed_candidate",
    )
    comparison = compare_campaigns(
        baseline_campaign_id=baseline["campaign_id"],
        candidate_campaign_id=candidate["campaign_id"],
        workspace_id="installed-audit",
        db_path=database,
        artifact_root=artifacts,
    )
    comparison_again = compare_campaigns(
        baseline_campaign_id=baseline["campaign_id"],
        candidate_campaign_id=candidate["campaign_id"],
        workspace_id="installed-audit",
        db_path=database,
        artifact_root=artifacts,
    )
    assert baseline["release_gate"]["decision"] == "block"
    assert candidate["release_gate"]["decision"] == "pass"
    assert comparison["release_gate"]["decision"] == "pass"
    assert verify_campaign(artifacts, candidate["campaign_id"]).ok
    replay = replay_campaign_regressions(
        source_campaign_id=baseline["campaign_id"],
        version="candidate",
        workspace_id="installed-audit",
        db_path=database,
        artifact_root=artifacts,
    )
    assert replay["run_result"]["release_gate"]["decision"] == "pass"
    assert replay["run_result"]["idempotent_replay"] is False
    assert verify_campaign(artifacts, replay["replay_campaign_id"]).ok
    replay_again = replay_campaign_regressions(
        source_campaign_id=baseline["campaign_id"],
        version="candidate",
        workspace_id="installed-audit",
        db_path=database,
        artifact_root=artifacts,
    )
    assert replay_again["replay_campaign_id"] == replay["replay_campaign_id"]

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        port = int(listener.getsockname()[1])
    server_environment = os.environ.copy()
    server_environment["AGENTOPS_DB_PATH"] = str(database)
    server_environment["AGENTOPS_SKIP_SEED_EXPORTS"] = "1"
    server_process = subprocess.Popen(
        [sys.executable, "-I", "-m", "server", "--host", "127.0.0.1", "--port", str(port)],
        cwd=state_root,
        env=server_environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    api_campaigns_visible = False
    last_api_error = "server did not become ready"
    try:
        deadline = time.monotonic() + 30.0
        endpoint = f"http://127.0.0.1:{{port}}/api/reliability/campaigns?workspace_id=installed-audit"
        while time.monotonic() < deadline:
            if server_process.poll() is not None:
                break
            try:
                with urlopen(endpoint, timeout=1.0) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                campaign_ids = {{
                    item.get("id") or item.get("campaign_id")
                    for item in payload.get("campaigns", [])
                    if isinstance(item, dict)
                }}
                api_campaigns_visible = {{
                    baseline["campaign_id"],
                    candidate["campaign_id"],
                }}.issubset(campaign_ids)
                if api_campaigns_visible:
                    break
                last_api_error = f"campaigns missing from API response: {{campaign_ids!r}}"
            except (OSError, URLError, ValueError, json.JSONDecodeError) as exc:
                last_api_error = str(exc)
            time.sleep(0.1)
        if not api_campaigns_visible:
            raise AssertionError(last_api_error)
    finally:
        if server_process.poll() is None:
            server_process.terminate()
        try:
            server_stdout, server_stderr = server_process.communicate(timeout=5.0)
        except subprocess.TimeoutExpired:
            server_process.kill()
            server_stdout, server_stderr = server_process.communicate(timeout=5.0)
        if not api_campaigns_visible and (server_stdout or server_stderr):
            raise AssertionError(
                "installed server API smoke failed\\n"
                + server_stdout[-2000:]
                + server_stderr[-2000:]
            )

site_roots = [
    str(Path(path).resolve())
    for path in site.getsitepackages()
    if Path(path).name.casefold() in {{"site-packages", "dist-packages"}}
]
user_site = site.getusersitepackages()
if (
    isinstance(user_site, str)
    and Path(user_site).name.casefold() in {{"site-packages", "dist-packages"}}
):
    site_roots.append(str(Path(user_site).resolve()))
report = {{
    "schema_version": 1,
    "build_commit": build_commit,
    "site_roots": site_roots,
    "modules": {{
        "open_cekura": str(Path(open_cekura.__file__).resolve()),
        "agentops_mis_runtime": str(Path(agentops_mis_runtime.__file__).resolve()),
        "server": str(Path(server.__file__).resolve()),
    }},
    "baseline_idempotent": baseline_again["idempotent_replay"] is True,
    "candidate_idempotent": candidate_again["idempotent_replay"] is True,
    "comparison_idempotent": comparison_again["idempotent_replay"] is True,
    "replay_idempotent": replay_again["run_result"]["idempotent_replay"] is True,
    "api_campaigns_visible": api_campaigns_visible,
}}
print({REPORT_MARKER!r} + json.dumps(report, sort_keys=True))
'''
    return [sys.executable, "-I", "-c", probe]


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _validate_report(
    report: Mapping[str, Any],
    *,
    checkout: Path,
    expected_commit: str | None,
) -> None:
    if report.get("schema_version") != 1:
        raise ValueError("installed acceptance report schema is not version 1")
    build_commit = report.get("build_commit")
    if not isinstance(build_commit, str) or COMMIT_SHA.fullmatch(build_commit) is None:
        raise ValueError(
            "packaged build commit must be a 40-character lowercase Git SHA"
        )
    if expected_commit is not None and build_commit != expected_commit:
        raise ValueError(
            f"packaged build commit {build_commit} does not match expected commit "
            f"{expected_commit}"
        )

    roots_value = report.get("site_roots")
    if not isinstance(roots_value, list) or not roots_value:
        raise ValueError("installed acceptance report has no site-packages roots")
    site_roots = [Path(value).resolve() for value in roots_value if isinstance(value, str)]
    if len(site_roots) != len(roots_value):
        raise ValueError("installed acceptance report has an invalid site-packages root")
    if any(
        root.name.casefold() not in {"site-packages", "dist-packages"}
        for root in site_roots
    ):
        raise ValueError("installed acceptance report has an invalid site-packages root")

    modules = report.get("modules")
    if not isinstance(modules, Mapping):
        raise ValueError("installed acceptance report has no module paths")
    checkout = checkout.resolve()
    for module_name in ("open_cekura", "agentops_mis_runtime", "server"):
        value = modules.get(module_name)
        if not isinstance(value, str):
            raise ValueError(f"{module_name} did not report an import path")
        module_path = Path(value).resolve()
        if _is_within(module_path, checkout):
            raise ValueError(f"{module_name} resolved from the checkout")
        if not any(_is_within(module_path, root) for root in site_roots):
            raise ValueError(f"{module_name} did not resolve from site-packages")

    for name in (
        "baseline_idempotent",
        "candidate_idempotent",
        "comparison_idempotent",
        "replay_idempotent",
        "api_campaigns_visible",
    ):
        if report.get(name) is not True:
            raise ValueError(f"installed acceptance did not prove {name}")


def _report_from_stdout(stdout: str) -> Mapping[str, Any]:
    marked = [
        line[len(REPORT_MARKER) :]
        for line in stdout.splitlines()
        if line.startswith(REPORT_MARKER)
    ]
    if len(marked) != 1:
        raise ValueError("installed acceptance probe did not emit one report")
    report = json.loads(marked[0])
    if not isinstance(report, Mapping):
        raise ValueError("installed acceptance probe report is not an object")
    return report


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    with tempfile.TemporaryDirectory(prefix="open-cekura-installed-") as temp:
        completed = subprocess.run(
            _probe_command(args.expected_commit),
            cwd=Path(temp),
            env=environment,
            check=False,
            text=True,
            capture_output=True,
        )
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr)
    if completed.returncode != 0:
        if completed.stdout:
            print(completed.stdout, end="")
        return completed.returncode
    try:
        report = _report_from_stdout(completed.stdout)
        _validate_report(
            report,
            checkout=_checkout_root(),
            expected_commit=args.expected_commit,
        )
    except (json.JSONDecodeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
