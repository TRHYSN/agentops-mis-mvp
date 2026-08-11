"""Workspace-scoped, read-only routes for Reliability Lab evidence."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Mapping, Sequence

from open_cekura.storage.repository import RepositoryError
from open_cekura.storage.sqlite_repository import SQLiteRepository

from .schemas import error_envelope, success_envelope


_IDENTIFIER_PATTERN = r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}"
_ID = re.compile(_IDENTIFIER_PATTERN + r"\Z")
_WORKSPACE = re.compile(_IDENTIFIER_PATTERN + r"\Z")
_MAX_LIMIT = 100
_MAX_OFFSET = 1_000_000


class InvalidReliabilityQuery(ValueError):
    """A public query violates the bounded Reliability API contract."""

    def __init__(self, field: str, message: str):
        super().__init__(message)
        self.field = field


def initialize_schema(conn: sqlite3.Connection) -> None:
    """Initialize only the vertical projection on an already-migrated MIS DB."""

    if not isinstance(conn, sqlite3.Connection):
        raise TypeError("conn must be sqlite3.Connection")
    SQLiteRepository(conn, workspace_id="local-demo").initialize_schema()


@dataclass(frozen=True, slots=True)
class _ListRoute:
    field: str
    repository_method: str
    filters: tuple[str, ...] = ()


_LIST_ROUTES = {
    "/api/reliability/agents": _ListRoute("agents", "list_agents"),
    "/api/reliability/scenario-suites": _ListRoute(
        "scenario_suites", "list_scenario_suites"
    ),
    "/api/reliability/campaigns": _ListRoute("campaigns", "list_campaigns"),
    "/api/reliability/runs": _ListRoute("runs", "list_runs", ("campaign_id",)),
    "/api/reliability/failures": _ListRoute("failures", "list_failures", ("run_id",)),
    "/api/reliability/regressions": _ListRoute(
        "regressions",
        "list_regressions",
        ("scenario_id", "source_run_id"),
    ),
    "/api/reliability/release-gates": _ListRoute(
        "release_gates", "list_release_gates", ("campaign_id",)
    ),
}

_DETAIL_ROUTES: tuple[
    tuple[re.Pattern[str], str, str, str],
    ...,
] = (
    (
        re.compile(rf"/api/reliability/agents/({_IDENTIFIER_PATTERN})\Z"),
        "agent",
        "get_agent",
        "agent",
    ),
    (
        re.compile(
            rf"/api/reliability/scenario-suites/({_IDENTIFIER_PATTERN})\Z"
        ),
        "scenario_suite",
        "get_scenario_suite",
        "scenario_suite",
    ),
    (
        re.compile(rf"/api/reliability/campaigns/({_IDENTIFIER_PATTERN})\Z"),
        "campaign",
        "get_campaign",
        "campaign",
    ),
    (
        re.compile(rf"/api/reliability/runs/({_IDENTIFIER_PATTERN})\Z"),
        "run_detail",
        "get_run",
        "run",
    ),
    (
        re.compile(rf"/api/reliability/failures/({_IDENTIFIER_PATTERN})\Z"),
        "failure",
        "get_failure",
        "failure",
    ),
    (
        re.compile(rf"/api/reliability/regressions/({_IDENTIFIER_PATTERN})\Z"),
        "regression",
        "get_regression",
        "regression",
    ),
    (
        re.compile(
            rf"/api/reliability/release-gates/({_IDENTIFIER_PATTERN})\Z"
        ),
        "release_gate",
        "get_release_gate",
        "release_gate",
    ),
)


def handle_get(
    conn: sqlite3.Connection,
    *,
    path: str,
    query: Mapping[str, Sequence[str]],
    workspace_id: str,
) -> tuple[dict[str, Any], int]:
    """Project one Reliability GET request without owning DB or HTTP state."""

    if not isinstance(conn, sqlite3.Connection):
        raise TypeError("conn must be sqlite3.Connection")
    normalized_path = _normalize_path(path)
    if not isinstance(workspace_id, str) or not _WORKSPACE.fullmatch(workspace_id):
        return (
            error_envelope(
                "invalid_reliability_workspace",
                "The authenticated workspace identifier is invalid.",
            ),
            400,
        )
    try:
        _validate_workspace_hint(query)
        repository = SQLiteRepository(conn, workspace_id=workspace_id)
        if normalized_path == "/api/reliability/overview":
            _reject_unknown_query(query, {"workspace_id"})
            return _overview(conn, repository, workspace_id), 200
        list_route = _LIST_ROUTES.get(normalized_path)
        if list_route is not None:
            return _list_response(
                repository,
                route=list_route,
                query=query,
                workspace_id=workspace_id,
            ), 200
        for pattern, field, method_name, resource in _DETAIL_ROUTES:
            match = pattern.fullmatch(normalized_path)
            if match is None:
                continue
            _reject_unknown_query(query, {"workspace_id"})
            object_id = match.group(1)
            value = getattr(repository, method_name)(object_id)
            if value is None:
                return (
                    error_envelope(
                        f"reliability_{resource}_not_found",
                        "The requested Reliability Lab record was not found.",
                    ),
                    404,
                )
            body: dict[str, Any] = {field: value}
            if resource == "run":
                body["summary"] = _run_summary(value)
            return (
                success_envelope(
                    operation=f"reliability_{resource}_detail",
                    workspace_id=workspace_id,
                    payload=body,
                ),
                200,
            )
        return (
            error_envelope(
                "reliability_route_not_found",
                "The requested Reliability Lab route was not found.",
            ),
            404,
        )
    except InvalidReliabilityQuery as exc:
        return (
            error_envelope(
                "invalid_reliability_query",
                "A Reliability Lab query field is invalid.",
                field=exc.field,
            ),
            400,
        )
    except sqlite3.OperationalError:
        return (
            error_envelope(
                "reliability_store_unavailable",
                "The Reliability Lab store is temporarily unavailable.",
                retryable=True,
            ),
            503,
        )
    except RepositoryError:
        return (
            error_envelope(
                "reliability_data_integrity_error",
                "Reliability Lab data failed its integrity contract.",
            ),
            500,
        )


def _list_response(
    repository: SQLiteRepository,
    *,
    route: _ListRoute,
    query: Mapping[str, Sequence[str]],
    workspace_id: str,
) -> dict[str, Any]:
    allowed = {"limit", "offset", "workspace_id", *route.filters}
    _reject_unknown_query(query, allowed)
    limit = _integer_query(query, "limit", default=50, minimum=1, maximum=_MAX_LIMIT)
    offset = _integer_query(query, "offset", default=0, minimum=0, maximum=_MAX_OFFSET)
    kwargs: dict[str, Any] = {"limit": limit + 1, "offset": offset}
    for field in route.filters:
        value = _single_query(query, field, default=None)
        if value is not None:
            if not _ID.fullmatch(value):
                raise InvalidReliabilityQuery(field, "invalid opaque identifier")
            kwargs[field] = value
    method: Callable[..., list[dict[str, Any]]] = getattr(
        repository, route.repository_method
    )
    rows = method(**kwargs)
    has_more = len(rows) > limit
    page_rows = rows[:limit]
    return success_envelope(
        operation=f"reliability_{route.field}_list",
        workspace_id=workspace_id,
        payload={
            route.field: page_rows,
            "page": {
                "limit": limit,
                "offset": offset,
                "returned": len(page_rows),
                "has_more": has_more,
                "next_offset": offset + len(page_rows) if has_more else None,
            },
        },
    )


def _overview(
    conn: sqlite3.Connection,
    repository: SQLiteRepository,
    workspace_id: str,
) -> dict[str, Any]:
    tables = {
        "agents": "reliability_agents",
        "scenario_suites": "reliability_scenario_suites",
        "campaigns": "reliability_campaigns",
        "runs": "reliability_conversation_runs",
        "failures": "reliability_failures",
        "regressions": "reliability_regressions",
        "release_gates": "reliability_release_gates",
    }
    counts = {
        name: int(
            conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE workspace_id=?",
                (workspace_id,),
            ).fetchone()[0]
        )
        for name, table in tables.items()
    }
    run_status_counts = _grouped_counts(
        conn,
        table="reliability_conversation_runs",
        field="status",
        workspace_id=workspace_id,
    )
    gate_decision_counts = _grouped_counts(
        conn,
        table="reliability_release_gates",
        field="decision",
        workspace_id=workspace_id,
    )
    return success_envelope(
        operation="reliability_overview",
        workspace_id=workspace_id,
        payload={
            "overview": {
                "counts": counts,
                "run_status_counts": run_status_counts,
                "gate_decision_counts": gate_decision_counts,
                "recent_campaigns": repository.list_campaigns(limit=6, offset=0),
                "recent_release_gates": repository.list_release_gates(
                    limit=6, offset=0
                ),
            }
        },
    )


def _grouped_counts(
    conn: sqlite3.Connection,
    *,
    table: str,
    field: str,
    workspace_id: str,
) -> dict[str, int]:
    return {
        str(row[0]): int(row[1])
        for row in conn.execute(
            f"SELECT {field},COUNT(*) FROM {table} WHERE workspace_id=? GROUP BY {field}",
            (workspace_id,),
        ).fetchall()
    }


def _run_summary(detail: Mapping[str, Any]) -> dict[str, Any]:
    run_value = detail.get("run")
    run: Mapping[str, Any] = run_value if isinstance(run_value, Mapping) else {}
    latency_ms = None
    manifests = detail.get("manifests")
    if isinstance(manifests, list) and manifests:
        manifest = manifests[0] if isinstance(manifests[0], Mapping) else {}
        started = _parse_time(manifest.get("started_at"))
        finished = _parse_time(manifest.get("finished_at"))
        if started is not None and finished is not None and finished >= started:
            latency_ms = int((finished - started).total_seconds() * 1000)
    return {
        "status": run.get("status"),
        "turn_count": _collection_size(detail.get("turns")),
        "latency_ms": latency_ms,
        "tool_call_count": _collection_size(detail.get("tool_calls")),
        "evaluator_count": _collection_size(detail.get("evaluations")),
    }


def _collection_size(value: Any) -> int:
    return len(value) if isinstance(value, list) else 0


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def _normalize_path(path: str) -> str:
    if not isinstance(path, str):
        return ""
    if path == "/mis-api":
        return "/api"
    if path.startswith("/mis-api/"):
        return "/api/" + path[len("/mis-api/") :]
    return path


def _reject_unknown_query(
    query: Mapping[str, Sequence[str]], allowed: set[str]
) -> None:
    for key in query:
        if key not in allowed:
            raise InvalidReliabilityQuery(str(key), "unknown query field")


def _validate_workspace_hint(query: Mapping[str, Sequence[str]]) -> None:
    workspace_hint = _single_query(query, "workspace_id", default=None)
    if workspace_hint is not None and not _WORKSPACE.fullmatch(workspace_hint):
        raise InvalidReliabilityQuery("workspace_id", "invalid workspace identifier")


def _single_query(
    query: Mapping[str, Sequence[str]], field: str, *, default: str | None
) -> str | None:
    values = query.get(field)
    if values is None:
        return default
    if isinstance(values, (str, bytes)) or len(values) != 1:
        raise InvalidReliabilityQuery(field, "query field must occur exactly once")
    value = values[0]
    if not isinstance(value, str) or not value:
        raise InvalidReliabilityQuery(field, "query field must be non-empty")
    return value


def _integer_query(
    query: Mapping[str, Sequence[str]],
    field: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    raw = _single_query(query, field, default=None)
    if raw is None:
        return default
    try:
        value = int(raw, 10)
    except ValueError as exc:
        raise InvalidReliabilityQuery(field, "query field must be an integer") from exc
    if str(value) != raw or not minimum <= value <= maximum:
        raise InvalidReliabilityQuery(field, "query field is outside its bounds")
    return value


__all__ = ["InvalidReliabilityQuery", "handle_get", "initialize_schema"]
