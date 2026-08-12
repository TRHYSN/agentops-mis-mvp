from __future__ import annotations

import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.request import ProxyHandler

import pytest

from scripts.open_cekura_ci_acceptance import (
    AcceptanceError,
    atomic_replace_bytes,
    browser_dom_command,
    build_loopback_opener,
    evidence_storage_args,
    find_system_browser,
    run_json_command,
    select_acceptance_browser,
    validate_browser_dom,
    validate_overview_dom,
    validate_scenario_output,
)


def test_run_json_command_accepts_an_explicit_nonzero_contract(tmp_path: Path) -> None:
    payload = {"decision": "block", "token_omitted": True}
    command = [
        sys.executable,
        "-c",
        "import json,sys; print(json.dumps(" + repr(payload) + ")); raise SystemExit(3)",
    ]

    result = run_json_command(
        command,
        cwd=tmp_path,
        env=os.environ.copy(),
        expected_code=3,
    )

    assert result == payload


def test_run_json_command_rejects_an_unexpected_exit_code(tmp_path: Path) -> None:
    with pytest.raises(AcceptanceError, match="expected exit code 0, received 4"):
        run_json_command(
            [
                sys.executable,
                "-c",
                "import json; print(json.dumps({'verified': False})); raise SystemExit(4)",
            ],
            cwd=tmp_path,
            env=os.environ.copy(),
            expected_code=0,
        )


def test_atomic_replace_bytes_replaces_without_leaving_a_sidecar(tmp_path: Path) -> None:
    artifact = tmp_path / "transcript.json"
    artifact.write_bytes(b"original")

    atomic_replace_bytes(artifact, b"tampered")

    assert artifact.read_bytes() == b"tampered"
    assert [path for path in tmp_path.iterdir() if path != artifact] == []


def test_acceptance_module_does_not_embed_secret_values() -> None:
    source = (
        Path(__file__).resolve().parents[3]
        / "scripts"
        / "open_cekura_ci_acceptance.py"
    ).read_text(encoding="utf-8")

    assert "${{ secrets." not in source
    assert "shell=True" not in source
    assert "Bearer " not in source
    assert "OPENAI_API_KEY" not in source
    assert "ignore_cleanup_errors=True" not in source


def test_scenario_acceptance_uses_the_real_cli_ok_contract() -> None:
    validate_scenario_output(
        {
            "ok": True,
            "operation": "scenario_validate",
            "scenario_id": "appointment.basic_success",
            "schema_version": 1,
            "token_omitted": True,
        }
    )

    with pytest.raises(AcceptanceError, match="Scenario v1 validation did not pass"):
        validate_scenario_output(
            {
                "ok": False,
                "operation": "scenario_validate",
                "token_omitted": True,
            }
        )


def test_evidence_verify_receives_the_governed_state_options(tmp_path: Path) -> None:
    database = tmp_path / "state with spaces" / "reliability.db"
    artifacts = tmp_path / "evidence with spaces"

    arguments = evidence_storage_args(
        workspace="workspace-ci",
        database=database,
        artifacts=artifacts,
    )

    assert arguments == [
        "--workspace",
        "workspace-ci",
        "--db",
        str(database),
        "--artifacts",
        str(artifacts),
    ]


def test_loopback_http_opener_disables_environment_proxies() -> None:
    opener = build_loopback_opener()
    proxy_handlers = [
        handler for handler in opener.handlers if isinstance(handler, ProxyHandler)
    ]

    # urllib omits an explicitly empty ProxyHandler from the installed handler
    # list; the absence is what prevents environment proxy discovery.
    assert proxy_handlers == []


def test_loopback_http_ignores_a_malicious_environment_proxy(monkeypatch) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib callback contract
            body = b"loopback-ok"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    try:
        opener = build_loopback_opener()
        with opener.open(
            f"http://127.0.0.1:{server.server_address[1]}/",
            timeout=5,
        ) as response:
            assert response.read() == b"loopback-ok"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_windows_browser_discovery_uses_a_preinstalled_chrome(
    tmp_path: Path,
) -> None:
    browser = tmp_path / "Program Files" / "Google" / "Chrome" / "Application" / "chrome.exe"
    browser.parent.mkdir(parents=True)
    browser.write_bytes(b"fixture")

    discovered = find_system_browser(
        environ={"PROGRAMFILES": str(tmp_path / "Program Files")},
        system="Windows",
        which=lambda _name: None,
    )

    assert discovered == browser.resolve()


def test_non_browser_matrix_does_not_probe_or_execute_a_browser() -> None:
    def unexpected_finder(**_kwargs):
        raise AssertionError("browser discovery must not run")

    assert (
        select_acceptance_browser(
            require_browser=False,
            environ={},
            finder=unexpected_finder,
        )
        is None
    )


def test_required_browser_matrix_fails_closed_when_browser_is_missing() -> None:
    with pytest.raises(AcceptanceError, match="preinstalled Chrome or Edge"):
        select_acceptance_browser(
            require_browser=True,
            environ={},
            finder=lambda **_kwargs: None,
        )


def test_headless_browser_command_is_network_bounded_and_argv_only(
    tmp_path: Path,
) -> None:
    browser = tmp_path / "Browser With Spaces" / "chrome.exe"
    profile = tmp_path / "Profile With Spaces"
    url = "http://127.0.0.1:48123/workspace/reliability/runs/ocrun_demo"

    command = browser_dom_command(browser=browser, profile_dir=profile, url=url)

    assert command[0] == str(browser)
    assert command[-1] == url
    assert "--headless=new" in command
    assert "--no-proxy-server" in command
    assert "--disable-background-networking" in command
    assert f"--user-data-dir={profile}" in command
    assert all(" " not in argument or argument in {str(browser), f"--user-data-dir={profile}"} for argument in command)


def test_rendered_dom_must_contain_real_run_and_campaign_readback() -> None:
    dom = (
        '<main data-testid="reliability-run-detail">'
        '<section data-testid="reliability-run-evidence-chain">'
        '<a data-testid="reliability-run-campaign-link">Campaign</a>'
        '<a data-testid="reliability-run-mis-evidence-link">MIS Run</a>'
        "ocrun_candidate_01 occampaign_ci_locator_says_baseline "
        "appointment.basic_success I need to move my appointment. "
        "lookup_booking task_success.v1 ocmanifest_demo mis_run_demo PASS"
        "</section>"
        '<div data-testid="reliability-run-context-column"></div>'
        '<div data-testid="reliability-run-trace-column"></div>'
        '<div data-testid="reliability-run-verdict-column"></div>'
        "</main>"
    )

    validate_browser_dom(
        dom,
        run_id="ocrun_candidate_01",
        campaign_id="occampaign_ci_locator_says_baseline",
        scenario_id="appointment.basic_success",
        transcript_text="I need to move my appointment.",
        tool_name="lookup_booking",
        evaluator_id="task_success.v1",
        manifest_id="ocmanifest_demo",
        mis_run_id="mis_run_demo",
        gate_decision="PASS",
    )

    with pytest.raises(AcceptanceError, match="rendered campaign result"):
        validate_browser_dom(
            dom.replace("occampaign_ci_locator_says_baseline", "missing"),
            run_id="ocrun_candidate_01",
            campaign_id="occampaign_ci_locator_says_baseline",
            scenario_id="appointment.basic_success",
            transcript_text="I need to move my appointment.",
            tool_name="lookup_booking",
            evaluator_id="task_success.v1",
            manifest_id="ocmanifest_demo",
            mis_run_id="mis_run_demo",
            gate_decision="PASS",
        )


def test_rendered_overview_must_show_both_campaigns_and_gate_decisions() -> None:
    dom = (
        "<main><h1>Reliability Lab</h1>"
        "<div>occampaign_ci_locator_says_candidate <span>BLOCK</span></div>"
        "<div>occampaign_ci_locator_says_baseline <span>PASS</span></div>"
        "</main>"
    )

    validate_overview_dom(
        dom,
        baseline_id="occampaign_ci_locator_says_candidate",
        candidate_id="occampaign_ci_locator_says_baseline",
    )

    with pytest.raises(AcceptanceError, match="rendered baseline gate decision"):
        validate_overview_dom(
            dom.replace("BLOCK", "UNKNOWN"),
            baseline_id="occampaign_ci_locator_says_candidate",
            candidate_id="occampaign_ci_locator_says_baseline",
        )
