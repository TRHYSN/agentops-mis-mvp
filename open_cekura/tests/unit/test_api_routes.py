from __future__ import annotations

import sqlite3
from typing import Any

import pytest

from open_cekura.api import routes
from open_cekura.storage.repository import RepositoryError


class FakeRepository:
    calls: list[tuple[str, dict[str, Any]]] = []
    missing: set[str] = set()
    fail: bool = False

    def __init__(self, conn: sqlite3.Connection, *, workspace_id: str):
        self.conn = conn
        self.workspace_id = workspace_id

    @classmethod
    def reset(cls) -> None:
        cls.calls = []
        cls.missing = set()
        cls.fail = False

    def _list(self, resource: str, **kwargs: Any) -> list[dict[str, Any]]:
        type(self).calls.append((f"list_{resource}", kwargs))
        if type(self).fail:
            raise RepositoryError("PRIVATE_DATABASE_DETAIL")
        id_key = {
            "scenario_suites": "suite_id",
            "release_gates": "gate_id",
        }.get(resource, f"{resource.removesuffix('s')}_id")
        return [{id_key: f"{resource}-{index}"} for index in range(3)]

    def _get(self, resource: str, object_id: str) -> dict[str, Any] | None:
        type(self).calls.append((f"get_{resource}", {"id": object_id}))
        if type(self).fail:
            raise RepositoryError("PRIVATE_DATABASE_DETAIL")
        if resource in type(self).missing:
            return None
        if resource == "run":
            return {
                "run": {"run_id": object_id, "status": "pass"},
                "turns": [{"turn_id": "turn-1"}],
                "tool_calls": [{"tool_call_id": "tool-1"}],
                "evaluations": [{"evaluation_id": "evaluation-1"}],
                "failures": [],
                "regressions": [],
                "release_gates": [],
                "manifests": [],
                "mis_links": {"task_id": "task-1"},
            }
        key = {
            "scenario_suite": "suite_id",
            "release_gate": "gate_id",
        }.get(resource, f"{resource}_id")
        return {key: object_id}

    def list_agents(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self._list("agents", **kwargs)

    def get_agent(self, object_id: str) -> dict[str, Any] | None:
        return self._get("agent", object_id)

    def list_scenario_suites(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self._list("scenario_suites", **kwargs)

    def get_scenario_suite(self, object_id: str) -> dict[str, Any] | None:
        return self._get("scenario_suite", object_id)

    def list_campaigns(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self._list("campaigns", **kwargs)

    def get_campaign(self, object_id: str) -> dict[str, Any] | None:
        return self._get("campaign", object_id)

    def list_runs(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self._list("runs", **kwargs)

    def get_run(self, object_id: str) -> dict[str, Any] | None:
        return self._get("run", object_id)

    def list_failures(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self._list("failures", **kwargs)

    def get_failure(self, object_id: str) -> dict[str, Any] | None:
        return self._get("failure", object_id)

    def list_regressions(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self._list("regressions", **kwargs)

    def get_regression(self, object_id: str) -> dict[str, Any] | None:
        return self._get("regression", object_id)

    def list_release_gates(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self._list("release_gates", **kwargs)

    def get_release_gate(self, object_id: str) -> dict[str, Any] | None:
        return self._get("release_gate", object_id)


@pytest.fixture(autouse=True)
def fake_repository(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeRepository.reset()
    monkeypatch.setattr(routes, "SQLiteRepository", FakeRepository)


@pytest.fixture
def conn() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    try:
        yield connection
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("path", "field", "method", "filter_query", "filter_kwargs"),
    [
        ("/api/reliability/agents", "agents", "list_agents", {}, {}),
        (
            "/api/reliability/scenario-suites",
            "scenario_suites",
            "list_scenario_suites",
            {},
            {},
        ),
        ("/api/reliability/campaigns", "campaigns", "list_campaigns", {}, {}),
        (
            "/api/reliability/runs",
            "runs",
            "list_runs",
            {"campaign_id": ["campaign-a"]},
            {"campaign_id": "campaign-a"},
        ),
        (
            "/api/reliability/failures",
            "failures",
            "list_failures",
            {"run_id": ["run-a"]},
            {"run_id": "run-a"},
        ),
        (
            "/api/reliability/regressions",
            "regressions",
            "list_regressions",
            {"scenario_id": ["scenario-a"], "source_run_id": ["run-a"]},
            {"scenario_id": "scenario-a", "source_run_id": "run-a"},
        ),
        (
            "/api/reliability/release-gates",
            "release_gates",
            "list_release_gates",
            {"campaign_id": ["campaign-a"]},
            {"campaign_id": "campaign-a"},
        ),
    ],
)
def test_list_routes_are_bounded_filterable_and_workspace_scoped(
    conn: sqlite3.Connection,
    path: str,
    field: str,
    method: str,
    filter_query: dict[str, list[str]],
    filter_kwargs: dict[str, str],
) -> None:
    query = {
        "limit": ["2"],
        "offset": ["4"],
        "workspace_id": ["attacker-workspace"],
        **filter_query,
    }

    payload, status = routes.handle_get(
        conn,
        path=path,
        query=query,
        workspace_id="session-workspace",
    )

    assert status == 200
    assert payload["schema_version"] == "open_cekura.reliability_api.v1"
    assert payload["workspace_id"] == "session-workspace"
    assert len(payload[field]) == 2
    assert payload["page"] == {
        "limit": 2,
        "offset": 4,
        "returned": 2,
        "has_more": True,
        "next_offset": 6,
    }
    assert payload["safety"]["read_only"] is True
    assert payload["safety"]["ledger_mutated"] is False
    assert payload["token_omitted"] is True
    assert FakeRepository.calls == [
        (method, {"limit": 3, "offset": 4, **filter_kwargs})
    ]


@pytest.mark.parametrize(
    ("path", "field", "resource"),
    [
        ("/api/reliability/agents/agent-a", "agent", "agent"),
        (
            "/api/reliability/scenario-suites/suite-a",
            "scenario_suite",
            "scenario_suite",
        ),
        ("/api/reliability/campaigns/campaign-a", "campaign", "campaign"),
        ("/api/reliability/failures/failure-a", "failure", "failure"),
        (
            "/api/reliability/regressions/regression-a",
            "regression",
            "regression",
        ),
        (
            "/api/reliability/release-gates/gate-a",
            "release_gate",
            "release_gate",
        ),
    ],
)
def test_detail_routes_return_safe_not_found_without_cross_workspace_leak(
    conn: sqlite3.Connection,
    path: str,
    field: str,
    resource: str,
) -> None:
    payload, status = routes.handle_get(
        conn, path=path, query={}, workspace_id="workspace-a"
    )
    assert status == 200
    assert payload[field]

    FakeRepository.missing = {resource}
    missing, missing_status = routes.handle_get(
        conn, path=path, query={}, workspace_id="workspace-a"
    )
    assert missing_status == 404
    assert missing["error"] == f"reliability_{resource}_not_found"
    assert "workspace" not in missing.get("message", "").lower()
    assert missing["token_omitted"] is True


def test_run_detail_adds_evidence_summary_without_fabricating_latency(
    conn: sqlite3.Connection,
) -> None:
    payload, status = routes.handle_get(
        conn,
        path="/mis-api/reliability/runs/run-a",
        query={},
        workspace_id="workspace-a",
    )

    assert status == 200
    assert payload["run_detail"]["mis_links"]["task_id"] == "task-1"
    assert payload["summary"] == {
        "status": "pass",
        "turn_count": 1,
        "latency_ms": None,
        "tool_call_count": 1,
        "evaluator_count": 1,
    }


@pytest.mark.parametrize(
    "query",
    [
        {"limit": ["0"]},
        {"limit": ["101"]},
        {"limit": ["abc"]},
        {"limit": ["1", "2"]},
        {"offset": ["-1"]},
        {"offset": ["1000001"]},
        {"unknown": ["value"]},
        {"campaign_id": ["bad/id"]},
        {"workspace_id": ["workspace-a", "workspace-b"]},
    ],
)
def test_invalid_queries_fail_fast_without_echoing_values(
    conn: sqlite3.Connection, query: dict[str, list[str]]
) -> None:
    payload, status = routes.handle_get(
        conn,
        path="/api/reliability/runs",
        query=query,
        workspace_id="workspace-a",
    )
    assert status == 400
    assert payload["error"] == "invalid_reliability_query"
    assert "bad/id" not in str(payload)
    assert payload["token_omitted"] is True


def test_repository_errors_are_mapped_without_private_exception_text(
    conn: sqlite3.Connection,
) -> None:
    FakeRepository.fail = True
    payload, status = routes.handle_get(
        conn,
        path="/api/reliability/agents",
        query={},
        workspace_id="workspace-a",
    )
    assert status == 500
    assert payload["error"] == "reliability_data_integrity_error"
    assert "PRIVATE_DATABASE_DETAIL" not in str(payload)


def test_unknown_reliability_path_is_a_safe_404(conn: sqlite3.Connection) -> None:
    payload, status = routes.handle_get(
        conn,
        path="/api/reliability/not-a-resource",
        query={},
        workspace_id="workspace-a",
    )
    assert status == 404
    assert payload["error"] == "reliability_route_not_found"
    assert payload["token_omitted"] is True


def test_identifier_boundaries_match_the_domain_and_repository_contract(
    conn: sqlite3.Connection,
) -> None:
    valid = "a" * 200
    detail, detail_status = routes.handle_get(
        conn,
        path=f"/api/reliability/runs/{valid}",
        query={},
        workspace_id=valid,
    )
    listed, list_status = routes.handle_get(
        conn,
        path="/api/reliability/runs",
        query={"campaign_id": [valid], "workspace_id": [valid]},
        workspace_id=valid,
    )

    assert detail_status == 200
    assert detail["run_detail"]["run"]["run_id"] == valid
    assert list_status == 200
    assert FakeRepository.calls[-1][1]["campaign_id"] == valid

    too_long = "a" * 201
    invalid_filter, invalid_filter_status = routes.handle_get(
        conn,
        path="/api/reliability/runs",
        query={"campaign_id": [too_long]},
        workspace_id="workspace-a",
    )
    invalid_workspace, invalid_workspace_status = routes.handle_get(
        conn,
        path="/api/reliability/runs",
        query={},
        workspace_id="..",
    )

    assert invalid_filter_status == 400
    assert invalid_filter["error"] == "invalid_reliability_query"
    assert invalid_workspace_status == 400
    assert invalid_workspace["error"] == "invalid_reliability_workspace"


def test_schema_initialization_and_empty_overview_use_the_real_mis_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import server

    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(server.SCHEMA_SQL)
    monkeypatch.setattr(
        routes,
        "SQLiteRepository",
        __import__(
            "open_cekura.storage.sqlite_repository",
            fromlist=["SQLiteRepository"],
        ).SQLiteRepository,
    )
    try:
        routes.initialize_schema(connection)
        routes.initialize_schema(connection)

        table_count = connection.execute(
            "SELECT COUNT(*) FROM sqlite_master "
            "WHERE type='table' AND name LIKE 'reliability_%'"
        ).fetchone()[0]
        payload, status = routes.handle_get(
            connection,
            path="/api/reliability/overview",
            query={},
            workspace_id="workspace-a",
        )
        assert table_count == 20
        assert status == 200
        assert payload["overview"]["counts"] == {
            "agents": 0,
            "scenario_suites": 0,
            "campaigns": 0,
            "runs": 0,
            "failures": 0,
            "regressions": 0,
            "release_gates": 0,
        }
    finally:
        connection.close()
