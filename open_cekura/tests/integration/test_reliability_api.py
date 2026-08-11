from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.request import urlopen

import server
from open_cekura.api.routes import handle_get, initialize_schema
from open_cekura.campaigns.runner import execute_mock_campaign
from open_cekura.mis.persistence import persist_campaign_execution
from open_cekura.simulation.mock_agent import MockAgentConfig


REPO_ROOT = Path(__file__).resolve().parents[3]
SCENARIOS = REPO_ROOT / "examples" / "open-cekura" / "scenarios"


def _database(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.create_function("agentops_json_array_contains", 2, server.json_array_contains)
    conn.create_function(
        "agentops_audit_chain_hash",
        9,
        server.audit_chain_hash_sql,
        deterministic=True,
    )
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(server.SCHEMA_SQL)
    initialize_schema(conn)
    conn.commit()
    return conn


def test_real_campaign_projects_through_workspace_scoped_reliability_api(
    tmp_path: Path,
) -> None:
    execution = asyncio.run(
        execute_mock_campaign(
            suite_path=SCENARIOS,
            config=MockAgentConfig.baseline(),
            version="baseline-api",
            campaign_id="occampaign_api_integration",
            workspace_id="api-workspace",
            created_at=datetime(2026, 8, 11, 16, 0, tzinfo=timezone.utc),
        )
    )
    conn = _database(tmp_path / "api.db")
    try:
        conn.execute("BEGIN")
        mappings = persist_campaign_execution(
            conn, execution, workspace_id="api-workspace"
        )
        conn.commit()

        overview, overview_status = handle_get(
            conn,
            path="/api/reliability/overview",
            query={},
            workspace_id="api-workspace",
        )
        assert overview_status == 200
        assert overview["overview"]["counts"]["campaigns"] == 1
        assert overview["overview"]["counts"]["runs"] == 10
        assert overview["overview"]["counts"]["failures"] > 0
        assert overview["overview"]["counts"]["regressions"] > 0

        runs, runs_status = handle_get(
            conn,
            path="/mis-api/reliability/runs",
            query={"campaign_id": [execution.campaign.id], "limit": ["10"]},
            workspace_id="api-workspace",
        )
        assert runs_status == 200
        assert len(runs["runs"]) == 10
        assert runs["page"]["has_more"] is False

        run_id = execution.records[0].simulation.run.id
        detail, detail_status = handle_get(
            conn,
            path=f"/api/reliability/runs/{run_id}",
            query={},
            workspace_id="api-workspace",
        )
        assert detail_status == 200
        assert detail["run_detail"]["mis_links"]["task_id"] == mappings.mis_task_id
        assert detail["run_detail"]["mis_links"]["plan_id"] == mappings.mis_plan_id
        assert detail["run_detail"]["mis_links"]["run_id"]
        assert len(detail["run_detail"]["evaluations"]) == 8

        other_workspace, other_status = handle_get(
            conn,
            path=f"/api/reliability/runs/{run_id}",
            query={},
            workspace_id="other-workspace",
        )
        assert other_status == 404
        assert "api-workspace" not in json.dumps(other_workspace, sort_keys=True)
    finally:
        conn.close()


def test_live_mis_api_handler_reads_the_sqlite_reliability_projection(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database = tmp_path / "live reliability.db"
    execution = asyncio.run(
        execute_mock_campaign(
            suite_path=SCENARIOS,
            config=MockAgentConfig.candidate(),
            version="candidate-live-api",
            campaign_id="occampaign_live_api",
            workspace_id="live-workspace",
            created_at=datetime(2026, 8, 11, 17, 0, tzinfo=timezone.utc),
        )
    )
    conn = _database(database)
    try:
        conn.execute("BEGIN")
        persist_campaign_execution(conn, execution, workspace_id="live-workspace")
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setattr(server, "DB_PATH", database)
    monkeypatch.setattr(server.human_auth, "required", lambda: False)
    monkeypatch.setattr(server, "private_host_restart_audit_tick", lambda: None)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    httpd.daemon_threads = True
    thread = Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        with urlopen(
            f"{base_url}/mis-api/reliability/overview?workspace_id=live-workspace",
            timeout=10,
        ) as response:
            overview = json.loads(response.read().decode("utf-8"))
        with urlopen(
            f"{base_url}/mis-api/reliability/runs"
            "?workspace_id=live-workspace&campaign_id=occampaign_live_api&limit=10",
            timeout=10,
        ) as response:
            runs = json.loads(response.read().decode("utf-8"))
        run_id = runs["runs"][0]["run_id"]
        with urlopen(
            f"{base_url}/mis-api/reliability/runs/{run_id}"
            "?workspace_id=live-workspace",
            timeout=10,
        ) as response:
            detail = json.loads(response.read().decode("utf-8"))
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=10)

    assert overview["overview"]["counts"]["campaigns"] == 1
    assert overview["overview"]["counts"]["runs"] == 10
    assert len(runs["runs"]) == 10
    assert detail["run_detail"]["run"]["run_id"] == run_id
    assert detail["run_detail"]["mis_links"]["task_id"]
    assert detail["summary"]["turn_count"] > 0
    assert detail["summary"]["evaluator_count"] == 8
    assert detail["token_omitted"] is True
