#!/usr/bin/env python3
"""Portable offline acceptance for the OpenCekura Reliability Lab v0.

The script intentionally uses only Python subprocess argument arrays, tempfile,
socket, and urllib so the same command exercises Windows and Ubuntu runners.
It expects the existing Vite application to have been built before invocation.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import ProxyHandler, Request, build_opener


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_UI_DIST = ROOT / "ui" / "start-building-app" / "dist"
SCENARIO_SUITE = Path("examples") / "open-cekura" / "scenarios"
BASIC_SCENARIO = SCENARIO_SUITE / "basic.yaml"
BASELINE_ID = "occampaign_ci_locator_says_candidate"
CANDIDATE_ID = "occampaign_ci_locator_says_baseline"
WORKSPACE_ID = "local-demo"


class AcceptanceError(RuntimeError):
    """A bounded, user-safe acceptance contract failure."""


def build_loopback_opener():
    """Build an HTTP opener that never delegates loopback checks to a proxy."""

    return build_opener(ProxyHandler({}))


LOOPBACK_OPENER = build_loopback_opener()


def find_system_browser(
    *,
    environ: Mapping[str, str] | None = None,
    system: str | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> Path | None:
    """Locate a hosted-runner browser without downloading a new binary."""

    runtime_environment = environ if environ is not None else os.environ
    configured = str(runtime_environment.get("OPEN_CEKURA_BROWSER") or "").strip()
    if configured:
        configured_path = Path(configured).expanduser().resolve()
        if not configured_path.is_file():
            raise AcceptanceError("configured browser executable is unavailable")
        return configured_path

    browser_names = (
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
        "microsoft-edge",
        "msedge",
        "chrome",
    )
    for name in browser_names:
        located = which(name)
        if located:
            candidate = Path(located).resolve()
            if candidate.is_file():
                return candidate

    if (system or platform.system()).lower() != "windows":
        return None
    roots = [
        runtime_environment.get("PROGRAMFILES"),
        runtime_environment.get("PROGRAMFILES(X86)"),
        runtime_environment.get("LOCALAPPDATA"),
    ]
    relatives = (
        Path("Google") / "Chrome" / "Application" / "chrome.exe",
        Path("Microsoft") / "Edge" / "Application" / "msedge.exe",
        Path("Chromium") / "Application" / "chrome.exe",
    )
    for root_text in roots:
        if not root_text:
            continue
        root = Path(root_text)
        for relative in relatives:
            candidate = (root / relative).resolve()
            if candidate.is_file():
                return candidate
    return None


def select_acceptance_browser(
    *,
    require_browser: bool,
    environ: Mapping[str, str],
    finder: Callable[..., Path | None] = find_system_browser,
) -> Path | None:
    """Keep non-browser matrix jobs deterministic and fail required jobs closed."""

    if not require_browser:
        return None
    browser = finder(environ=environ)
    if browser is None:
        raise AcceptanceError(
            "a preinstalled Chrome or Edge browser is required for DOM E2E"
        )
    return browser


def browser_dom_command(*, browser: Path, profile_dir: Path, url: str) -> list[str]:
    """Construct one network-bounded Chromium/Edge DOM-render command."""

    parsed = urlparse(url)
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.port is None:
        raise AcceptanceError("browser E2E URL must use an explicit loopback port")
    return [
        str(browser),
        "--headless=new",
        "--disable-background-networking",
        "--disable-component-update",
        "--disable-crash-reporter",
        "--disable-default-apps",
        "--disable-dev-shm-usage",
        "--disable-extensions",
        "--disable-features=MediaRouter,OptimizationHints,Translate",
        "--disable-gpu",
        "--disable-sync",
        "--metrics-recording-only",
        "--mute-audio",
        "--no-default-browser-check",
        "--no-first-run",
        "--no-proxy-server",
        "--window-size=1440,1000",
        f"--user-data-dir={profile_dir}",
        "--virtual-time-budget=10000",
        "--dump-dom",
        url,
    ]


def _browser_environment(environ: Mapping[str, str]) -> dict[str, str]:
    result = dict(environ)
    for name in (
        "ALL_PROXY",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "all_proxy",
        "https_proxy",
        "http_proxy",
    ):
        result.pop(name, None)
    result["NO_PROXY"] = "127.0.0.1,localhost"
    result["no_proxy"] = "127.0.0.1,localhost"
    return result


def render_browser_dom(
    *,
    browser: Path,
    url: str,
    environ: Mapping[str, str],
) -> str:
    """Execute the real React bundle in an isolated preinstalled browser."""

    try:
        with tempfile.TemporaryDirectory(
            prefix="open-cekura-browser-",
        ) as profile_text:
            completed = subprocess.run(
                browser_dom_command(
                    browser=browser,
                    profile_dir=Path(profile_text),
                    url=url,
                ),
                env=_browser_environment(environ),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=45,
                check=False,
                shell=False,
            )
    except subprocess.TimeoutExpired as exc:
        raise AcceptanceError("preinstalled browser DOM render timed out") from exc
    if completed.returncode != 0:
        raise AcceptanceError(
            f"preinstalled browser DOM render exited with code {completed.returncode}"
        )
    dom = completed.stdout
    encoded_size = len(dom.encode("utf-8", errors="replace"))
    if not dom.strip() or encoded_size > 8 * 1024 * 1024:
        raise AcceptanceError("preinstalled browser returned an invalid DOM size")
    return dom


def validate_overview_dom(
    dom: str,
    *,
    baseline_id: str,
    candidate_id: str,
) -> None:
    rendered = html.unescape(dom)
    _require("Reliability Lab" in rendered, "rendered Reliability Lab title missing")
    _require(baseline_id in rendered, "rendered baseline campaign missing")
    _require(candidate_id in rendered, "rendered candidate campaign missing")
    _require(
        bool(re.search(r"\bBLOCK\b", rendered)),
        "rendered baseline gate decision missing",
    )
    _require(
        bool(re.search(r"\bPASS\b", rendered)),
        "rendered candidate gate decision missing",
    )
    _require(
        'data-testid="reliability-unavailable-state"' not in rendered,
        "Reliability overview rendered its unavailable state",
    )


def validate_browser_dom(
    dom: str,
    *,
    run_id: str,
    campaign_id: str,
    scenario_id: str,
    transcript_text: str,
    tool_name: str,
    evaluator_id: str,
    manifest_id: str,
    mis_run_id: str,
    gate_decision: str,
) -> None:
    rendered = html.unescape(dom)
    for marker in (
        "reliability-run-detail",
        "reliability-run-evidence-chain",
        "reliability-run-campaign-link",
        "reliability-run-mis-evidence-link",
        "reliability-run-context-column",
        "reliability-run-trace-column",
        "reliability-run-verdict-column",
    ):
        _require(
            f'data-testid="{marker}"' in rendered,
            f"rendered Reliability Run Detail omitted {marker}",
        )
    expected_values = (
        (run_id, "rendered run result"),
        (campaign_id, "rendered campaign result"),
        (scenario_id, "rendered scenario result"),
        (transcript_text, "rendered transcript result"),
        (tool_name, "rendered tool result"),
        (evaluator_id, "rendered evaluator result"),
        (manifest_id, "rendered evidence manifest result"),
        (mis_run_id, "rendered MIS run result"),
        (gate_decision, "rendered gate result"),
    )
    for value, label in expected_values:
        _require(bool(value) and value in rendered, f"{label} missing")
    _require(
        'data-testid="reliability-unavailable-state"' not in rendered,
        "Reliability Run Detail rendered its unavailable state",
    )


def run_json_command(
    command: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    expected_code: int = 0,
    timeout: float = 180.0,
) -> dict[str, Any]:
    """Run an argv-only command and parse its JSON stdout."""

    if not command or any(not isinstance(argument, str) for argument in command):
        raise AcceptanceError("command must be a non-empty string argument array")
    completed = subprocess.run(
        list(command),
        cwd=str(cwd),
        env=dict(env),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        shell=False,
    )
    if completed.returncode != expected_code:
        raise AcceptanceError(
            f"expected exit code {expected_code}, received {completed.returncode} "
            f"from {Path(command[0]).name}"
        )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise AcceptanceError("command stdout was not one JSON document") from exc
    if not isinstance(payload, dict):
        raise AcceptanceError("command JSON output must be an object")
    if payload.get("token_omitted") is not True:
        raise AcceptanceError("command JSON output did not assert token omission")
    return payload


def atomic_replace_bytes(path: Path, content: bytes) -> None:
    """Replace one temporary artifact without POSIX file-mode assumptions."""

    target = path.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{target.name}.",
            suffix=".tmp",
            dir=target.parent,
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def reserve_loopback_port() -> int:
    """Ask the OS for a currently available loopback TCP port."""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _http_bytes(url: str, *, timeout: float = 10.0) -> tuple[bytes, str]:
    request = Request(url, headers={"Accept": "application/json, text/html, */*"})
    with LOOPBACK_OPENER.open(request, timeout=timeout) as response:
        return response.read(), str(response.headers.get("Content-Type") or "")


def _http_json(url: str) -> dict[str, Any]:
    body, _ = _http_bytes(url)
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AcceptanceError("backend returned a non-JSON API response") from exc
    if not isinstance(payload, dict):
        raise AcceptanceError("backend API response must be an object")
    return payload


def _wait_for_server(process: subprocess.Popen[bytes], base_url: str) -> None:
    deadline = time.monotonic() + 30.0
    last_error = "server readiness timed out"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AcceptanceError(
                f"backend exited before readiness with code {process.returncode}"
            )
        try:
            payload = _http_json(f"{base_url}/health")
            if payload.get("status") == "ready":
                return
            last_error = "health endpoint did not report ready"
        except (AcceptanceError, HTTPError, URLError, OSError) as exc:
            last_error = type(exc).__name__
        time.sleep(0.1)
    raise AcceptanceError(f"backend readiness failed: {last_error}")


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AcceptanceError(message)


def _release_gate(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    gate = payload.get("release_gate")
    if not isinstance(gate, dict):
        raise AcceptanceError("campaign output omitted its structured release gate")
    return gate


def validate_scenario_output(payload: Mapping[str, Any]) -> None:
    if payload.get("ok") is not True or payload.get("operation") != "scenario_validate":
        raise AcceptanceError("Scenario v1 validation did not pass")


def evidence_storage_args(
    *,
    workspace: str,
    database: Path,
    artifacts: Path,
) -> list[str]:
    return [
        "--workspace",
        workspace,
        "--db",
        str(database),
        "--artifacts",
        str(artifacts),
    ]


def _run_cli_contract(
    *,
    root: Path,
    env: Mapping[str, str],
    database: Path,
    artifacts: Path,
) -> dict[str, Any]:
    python = sys.executable
    base = [python, "-m", "open_cekura.cli.main"]
    shared = evidence_storage_args(
        workspace=WORKSPACE_ID,
        database=database,
        artifacts=artifacts,
    )
    evidence_args = shared

    scenario = run_json_command(
        [*base, "scenario", "validate", str(BASIC_SCENARIO)],
        cwd=root,
        env=env,
    )
    validate_scenario_output(scenario)

    baseline = run_json_command(
        [
            *base,
            "campaign",
            "run",
            "--suite",
            str(SCENARIO_SUITE),
            "--agent",
            "mock",
            "--version",
            "baseline",
            "--campaign-id",
            BASELINE_ID,
            *shared,
        ],
        cwd=root,
        env=env,
    )
    candidate = run_json_command(
        [
            *base,
            "campaign",
            "run",
            "--suite",
            str(SCENARIO_SUITE),
            "--agent",
            "mock",
            "--version",
            "candidate",
            "--campaign-id",
            CANDIDATE_ID,
            *shared,
        ],
        cwd=root,
        env=env,
    )
    baseline_gate = _release_gate(baseline)
    candidate_gate = _release_gate(candidate)
    _require(baseline.get("campaign_id") == BASELINE_ID, "baseline ID mismatch")
    _require(candidate.get("campaign_id") == CANDIDATE_ID, "candidate ID mismatch")
    _require(
        baseline.get("run_count") == candidate.get("run_count") == 10,
        "appointment suite must execute exactly ten scenarios per version",
    )
    _require(
        baseline_gate.get("decision") == "block",
        "baseline observations did not naturally derive BLOCK",
    )
    _require(
        candidate_gate.get("decision") == "pass",
        "candidate observations did not naturally derive PASS",
    )

    comparison = run_json_command(
        [
            *base,
            "campaign",
            "compare",
            "--baseline",
            BASELINE_ID,
            "--candidate",
            CANDIDATE_ID,
            *shared,
        ],
        cwd=root,
        env=env,
    )
    _require(
        _release_gate(comparison).get("decision") == "pass",
        "baseline-to-candidate comparison did not derive PASS",
    )

    blocked = run_json_command(
        [
            *base,
            "gate",
            "evaluate",
            "--campaign",
            BASELINE_ID,
            *shared,
        ],
        cwd=root,
        env=env,
        expected_code=3,
    )
    passed = run_json_command(
        [
            *base,
            "gate",
            "evaluate",
            "--campaign",
            CANDIDATE_ID,
            *shared,
        ],
        cwd=root,
        env=env,
    )
    blocker_ids = {
        str(item.get("rule_id"))
        for item in _release_gate(blocked).get("blockers", [])
        if isinstance(item, dict)
    }
    _require(
        {
            "zero_tolerance.duplicate_mutation.v1",
            "zero_tolerance.confirmation_before_mutation.v1",
        }.issubset(blocker_ids),
        "baseline gate omitted the expected observation-derived blockers",
    )
    _require(
        _release_gate(passed).get("decision") == "pass",
        "candidate gate command did not return PASS",
    )

    verified_campaigns: list[str] = []
    for campaign_id in (BASELINE_ID, CANDIDATE_ID):
        verified = run_json_command(
            [
                *base,
                "evidence",
                "verify",
                "--campaign",
                campaign_id,
                *evidence_args,
            ],
            cwd=root,
            env=env,
        )
        _require(verified.get("verified") is True, "evidence verification failed")
        _require(verified.get("run_count") == 10, "evidence run count mismatch")
        verified_campaigns.append(campaign_id)

    transcripts = sorted((artifacts / CANDIDATE_ID).glob("*/transcript.json"))
    _require(bool(transcripts), "candidate evidence omitted transcript artifacts")
    transcript = transcripts[0]
    original = transcript.read_bytes()
    try:
        atomic_replace_bytes(transcript, b"[]\n")
        tampered = run_json_command(
            [
                *base,
                "evidence",
                "verify",
                "--campaign",
                CANDIDATE_ID,
                *evidence_args,
            ],
            cwd=root,
            env=env,
            expected_code=4,
        )
        _require(
            tampered.get("verified") is False and bool(tampered.get("issues")),
            "tampered evidence was not rejected with structured issues",
        )
    finally:
        atomic_replace_bytes(transcript, original)
    restored = run_json_command(
        [
            *base,
            "evidence",
            "verify",
            "--campaign",
            CANDIDATE_ID,
            *evidence_args,
        ],
        cwd=root,
        env=env,
    )
    _require(restored.get("verified") is True, "restored evidence did not verify")

    return {
        "scenario_valid": True,
        "baseline": {
            "campaign_id": BASELINE_ID,
            "decision": baseline_gate.get("decision"),
            "blocker_rule_ids": sorted(blocker_ids),
        },
        "candidate": {
            "campaign_id": CANDIDATE_ID,
            "decision": candidate_gate.get("decision"),
        },
        "comparison_decision": _release_gate(comparison).get("decision"),
        "verified_campaigns": verified_campaigns,
        "tamper_detected": True,
        "restored_after_tamper": True,
    }


def _run_server_e2e(
    *,
    root: Path,
    env: Mapping[str, str],
    database: Path,
    ui_dist: Path,
    require_browser: bool,
) -> dict[str, Any]:
    _require((ui_dist / "index.html").is_file(), "Vite production build is missing")
    javascript_assets = sorted(ui_dist.rglob("*.js"))
    _require(bool(javascript_assets), "Vite production build has no JavaScript assets")
    port = reserve_loopback_port()
    base_url = f"http://127.0.0.1:{port}"
    process_env = dict(env)
    process_env.update(
        {
            "AGENTOPS_DB_PATH": str(database),
            "AGENTOPS_DEPLOYMENT_MODE": "local",
            "AGENTOPS_HUMAN_AUTH_REQUIRED": "false",
            "AGENTOPS_SKIP_SEED_EXPORTS": "1",
            "PYTHONUTF8": "1",
        }
    )
    with tempfile.TemporaryFile(mode="w+b") as server_log:
        process = subprocess.Popen(
            [
                sys.executable,
                "server.py",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--ui-dist",
                str(ui_dist),
            ],
            cwd=str(root),
            env=process_env,
            stdin=subprocess.DEVNULL,
            stdout=server_log,
            stderr=subprocess.STDOUT,
            shell=False,
        )
        try:
            _wait_for_server(process, base_url)
            overview = _http_json(
                f"{base_url}/mis-api/reliability/overview"
                f"?workspace_id={quote(WORKSPACE_ID)}"
            )
            runs = _http_json(
                f"{base_url}/mis-api/reliability/runs"
                f"?workspace_id={quote(WORKSPACE_ID)}"
                f"&campaign_id={quote(CANDIDATE_ID)}&limit=10"
            )
            run_rows = runs.get("runs")
            _require(isinstance(run_rows, list) and len(run_rows) == 10, "API run readback mismatch")
            selected_run = next(
                (
                    row
                    for row in run_rows
                    if isinstance(row, dict)
                    and row.get("scenario_id") == "appointment.basic_success"
                ),
                run_rows[0],
            )
            _require(isinstance(selected_run, dict), "API run row must be an object")
            run_id = str(selected_run.get("run_id") or "")
            _require(bool(run_id), "API run row omitted run_id")
            detail = _http_json(
                f"{base_url}/mis-api/reliability/runs/{quote(run_id)}"
                f"?workspace_id={quote(WORKSPACE_ID)}"
            )

            html, content_type = _http_bytes(
                f"{base_url}/workspace/reliability"
            )
            html_text = html.decode("utf-8")
            _require("text/html" in content_type, "Reliability deep link was not HTML")
            _require('id="root"' in html_text, "Reliability deep link omitted the React root")
            _require(
                bool(re.search(r'<script[^>]+type="module"', html_text)),
                "Reliability deep link omitted the production module entry",
            )

            delivered_assets: list[bytes] = []
            for asset in javascript_assets:
                relative = asset.relative_to(ui_dist).as_posix()
                body, asset_type = _http_bytes(f"{base_url}/{relative}")
                _require(
                    "javascript" in asset_type,
                    f"built asset {relative} was not served as JavaScript",
                )
                delivered_assets.append(body)
            bundle = b"\n".join(delivered_assets)
            _require(b"Reliability Lab" in bundle, "built UI omitted Reliability Lab copy")
            _require(
                b"reliability-run-detail" in bundle,
                "built UI omitted the Reliability Run Detail surface marker",
            )

            counts = ((overview.get("overview") or {}).get("counts") or {})
            _require(counts.get("campaigns") == 2, "API overview campaign count mismatch")
            _require(counts.get("runs") == 20, "API overview run count mismatch")
            _require(detail.get("token_omitted") is True, "API detail omitted safety marker")
            detail_run = ((detail.get("run_detail") or {}).get("run") or {})
            _require(detail_run.get("run_id") == run_id, "API detail run_id mismatch")
            run_detail = detail.get("run_detail")
            _require(isinstance(run_detail, dict), "API run detail must be an object")
            turns = run_detail.get("turns")
            tool_calls = run_detail.get("tool_calls")
            evaluations = run_detail.get("evaluations")
            manifests = run_detail.get("manifests")
            gates = run_detail.get("release_gates")
            mis_links = run_detail.get("mis_links")
            _require(isinstance(turns, list) and bool(turns), "API detail omitted turns")
            _require(
                isinstance(tool_calls, list) and bool(tool_calls),
                "API detail omitted tool calls",
            )
            _require(
                isinstance(evaluations, list) and bool(evaluations),
                "API detail omitted evaluations",
            )
            _require(
                isinstance(manifests, list) and bool(manifests),
                "API detail omitted evidence manifests",
            )
            _require(isinstance(gates, list) and bool(gates), "API detail omitted gates")
            _require(isinstance(mis_links, dict), "API detail omitted MIS links")
            visible_turn = next(
                (
                    turn
                    for turn in turns
                    if isinstance(turn, dict)
                    and turn.get("role") != "system"
                    and str(turn.get("content") or "")
                ),
                None,
            )
            _require(isinstance(visible_turn, dict), "API detail omitted visible transcript")
            first_tool = tool_calls[0]
            first_evaluation = evaluations[0]
            first_manifest = manifests[0]
            latest_gate = gates[-1]
            _require(isinstance(first_tool, dict), "API tool call must be an object")
            _require(
                isinstance(first_evaluation, dict),
                "API evaluation must be an object",
            )
            _require(
                isinstance(first_manifest, dict),
                "API evidence manifest must be an object",
            )
            _require(isinstance(latest_gate, dict), "API gate must be an object")

            browser = select_acceptance_browser(
                require_browser=require_browser,
                environ=env,
            )
            browser_e2e = "not_required"
            browser_name: str | None = None
            if browser is not None:
                browser_name = browser.name
                overview_dom = render_browser_dom(
                    browser=browser,
                    url=f"{base_url}/workspace/reliability",
                    environ=env,
                )
                validate_overview_dom(
                    overview_dom,
                    baseline_id=BASELINE_ID,
                    candidate_id=CANDIDATE_ID,
                )
                run_dom = render_browser_dom(
                    browser=browser,
                    url=f"{base_url}/workspace/reliability/runs/{quote(run_id)}",
                    environ=env,
                )
                validate_browser_dom(
                    run_dom,
                    run_id=run_id,
                    campaign_id=CANDIDATE_ID,
                    scenario_id=str(detail_run.get("scenario_id") or ""),
                    transcript_text=str(visible_turn.get("content") or ""),
                    tool_name=str(first_tool.get("name") or ""),
                    evaluator_id=str(first_evaluation.get("evaluator_id") or ""),
                    manifest_id=str(first_manifest.get("manifest_id") or ""),
                    mis_run_id=str(mis_links.get("run_id") or ""),
                    gate_decision=str(latest_gate.get("decision") or "").upper(),
                )
                browser_e2e = "pass"
            return {
                "health": "ready",
                "campaign_count": counts.get("campaigns"),
                "run_count": counts.get("runs"),
                "candidate_api_rows": len(run_rows),
                "run_detail_read_back": True,
                "ui_deep_link": "/workspace/reliability",
                "ui_asset_count": len(delivered_assets),
                "ui_surface_markers_present": True,
                "browser_e2e": browser_e2e,
                "browser_name": browser_name,
                "browser_required": require_browser,
                "browser_download_or_install_performed": False,
            }
        finally:
            _stop_process(process)


def run_acceptance(
    *,
    root: Path,
    ui_dist: Path,
    require_browser: bool = False,
) -> dict[str, Any]:
    root = root.resolve()
    ui_dist = ui_dist.resolve()
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    with tempfile.TemporaryDirectory(prefix="open-cekura-ci-") as temporary_text:
        temporary = Path(temporary_text)
        database = temporary / "state with spaces" / "reliability.db"
        artifacts = temporary / "artifacts with spaces" / "open-cekura"
        cli = _run_cli_contract(
            root=root,
            env=env,
            database=database,
            artifacts=artifacts,
        )
        e2e = _run_server_e2e(
            root=root,
            env=env,
            database=database,
            ui_dist=ui_dist,
            require_browser=require_browser,
        )
    return {
        "operation": "open_cekura_ci_acceptance",
        "ok": True,
        "platform": platform.system(),
        "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "cli": cli,
        "e2e": e2e,
        "safety": {
            "external_api_keys_required": False,
            "external_browser_install_required": False,
            "temporary_workspace_removed": True,
            "subprocess_argument_arrays": True,
            "token_omitted": True,
        },
        "token_omitted": True,
    }


def _write_result(path: Path | None, payload: Mapping[str, Any]) -> None:
    serialized = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    if path is not None:
        atomic_replace_bytes(path, serialized)
    print(serialized.decode("utf-8"), end="")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--ui-dist", type=Path, default=DEFAULT_UI_DIST)
    parser.add_argument("--result-path", type=Path)
    parser.add_argument(
        "--require-browser",
        action="store_true",
        help="Fail unless a preinstalled Chrome/Edge browser renders real Reliability DOM",
    )
    args = parser.parse_args(argv)
    root = args.root.resolve()
    ui_dist = args.ui_dist
    if not ui_dist.is_absolute():
        ui_dist = root / ui_dist
    result_path = args.result_path
    if result_path is not None and not result_path.is_absolute():
        result_path = root / result_path
    try:
        payload = run_acceptance(
            root=root,
            ui_dist=ui_dist,
            require_browser=args.require_browser,
        )
    except AcceptanceError as exc:
        payload = {
            "operation": "open_cekura_ci_acceptance",
            "ok": False,
            "error": "acceptance_contract_failed",
            "message": str(exc),
            "token_omitted": True,
        }
        _write_result(result_path, payload)
        return 1
    except Exception as exc:  # pragma: no cover - last-resort CI diagnostic boundary
        payload = {
            "operation": "open_cekura_ci_acceptance",
            "ok": False,
            "error": "unexpected_acceptance_error",
            "error_type": type(exc).__name__,
            "token_omitted": True,
        }
        _write_result(result_path, payload)
        return 1
    _write_result(result_path, payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
