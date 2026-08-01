#!/usr/bin/env python3
"""Prove the default Worker cadence cannot create a periodic Fleet stale window."""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


AGENT_ID = "agt_worker_heartbeat_cadence_fixture"
WORKSPACE_ID = "local-demo"
SESSION_ID = "ags_heartbeat_cadence_fixture"
BASE_TIME = dt.datetime(2035, 1, 2, 3, 4, 5, tzinfo=dt.timezone.utc)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class FixedClock:
    def __init__(self) -> None:
        self.offset_sec = 0.0

    def monotonic(self) -> float:
        return self.offset_sec

    def utc_at(self, offset_sec: float | None = None) -> dt.datetime:
        offset = self.offset_sec if offset_sec is None else offset_sec
        return BASE_TIME + dt.timedelta(seconds=offset)


class RecordingClient:
    workspace_id = WORKSPACE_ID
    agent_id = AGENT_ID

    def __init__(self, clock: FixedClock) -> None:
        self.clock = clock
        self.posts: list[dict[str, object]] = []
        self.session_observations: dict[str, dict[str, str]] = {}

    def post(self, path: str, payload: dict, timeout: int = 20) -> dict:
        require(path == "/api/agent-gateway/heartbeat", f"unexpected heartbeat path: {path}")
        require(timeout == 20, f"unexpected heartbeat timeout: {timeout}")
        require(payload.get("workspace_id") == WORKSPACE_ID, f"unexpected heartbeat workspace: {payload}")
        require(payload.get("agent_id") == AGENT_ID, f"unexpected heartbeat agent: {payload}")
        timestamp = self.clock.utc_at().isoformat()
        observation = {"last_heartbeat_at": timestamp, "updated_at": timestamp}
        self.session_observations[SESSION_ID] = observation
        self.posts.append({
            "offset_sec": self.clock.offset_sec,
            "timestamp": timestamp,
            "status": payload.get("status"),
            "scoped_observation": dict(observation),
        })
        return {"ledger_recorded": True}


def default_worker_args(worker):
    sentinel = object()
    previous = os.environ.pop("AGENTOPS_WORKER_HEARTBEAT_INTERVAL_SEC", sentinel)
    try:
        return worker.build_parser().parse_args([])
    finally:
        if previous is not sentinel:
            os.environ["AGENTOPS_WORKER_HEARTBEAT_INTERVAL_SEC"] = str(previous)


def fleet_at(
    worker_fleet,
    *,
    now_at: dt.datetime,
    scoped_heartbeat_at: dt.datetime | None,
    heartbeat_status: str = "running",
) -> dict:
    heartbeats_by_session = {}
    if scoped_heartbeat_at is not None:
        scoped_timestamp = scoped_heartbeat_at.isoformat()
        heartbeats_by_session[SESSION_ID] = {
            "last_heartbeat_at": scoped_timestamp,
            "updated_at": scoped_timestamp,
            "status": heartbeat_status,
        }
    summary = worker_fleet.build_worker_remote_fleet_summary(
        enrollments=[],
        sessions=[{
            "session_id": SESSION_ID,
            "agent_id": AGENT_ID,
            "workspace_id": WORKSPACE_ID,
            "session_state": "active",
            "scopes": sorted(worker_fleet.SERVICE_WORKER_EXECUTION_SCOPES),
            "created_at": BASE_TIME.isoformat(),
            "last_used_at": BASE_TIME.isoformat(),
        }],
        agents_by_id={
            AGENT_ID: {
                "agent_id": AGENT_ID,
                "name": "Heartbeat Cadence Fixture",
                "runtime_type": "openclaw",
                "status": "running",
            },
        },
        heartbeats_by_session=heartbeats_by_session,
        now_dt=now_at,
    )
    service_workers = summary.get("service_workers") or []
    require(len(service_workers) == 1, f"expected one service Worker: {summary}")
    return {"summary": summary, "worker": service_workers[0]}


def session_fixture(
    worker_fleet,
    session_id: str,
    *,
    activity_offset_sec: float,
    session_state: str = "active",
) -> dict[str, object]:
    activity_at = (BASE_TIME + dt.timedelta(seconds=activity_offset_sec)).isoformat()
    return {
        "session_id": session_id,
        "agent_id": AGENT_ID,
        "workspace_id": WORKSPACE_ID,
        "session_state": session_state,
        "scopes": sorted(worker_fleet.SERVICE_WORKER_EXECUTION_SCOPES),
        "created_at": activity_at,
        "last_used_at": activity_at,
    }


def session_summary(
    worker_fleet,
    *,
    sessions: list[dict[str, object]],
    heartbeats_by_session: dict[str, dict[str, str]],
    now_at: dt.datetime,
) -> dict:
    return worker_fleet.build_worker_remote_fleet_summary(
        enrollments=[],
        sessions=sessions,
        agents_by_id={
            AGENT_ID: {
                "agent_id": AGENT_ID,
                "name": "Session Aggregation Fixture",
                "runtime_type": "openclaw",
                "status": "running",
            },
        },
        heartbeats_by_session=heartbeats_by_session,
        now_dt=now_at,
    )


def main() -> int:
    try:
        from agentops_mis_cli import worker
        from agentops_mis_core import worker_fleet
        import server as server_module

        args = default_worker_args(worker)
        heartbeat_interval_sec = float(args.heartbeat_interval_sec)
        require(heartbeat_interval_sec > 0, "default heartbeat interval must be positive")

        clock = FixedClock()
        client = RecordingClient(clock)
        poll_offsets: list[float] = []
        heartbeat_results: list[dict] = []
        next_poll_offset = 0.0

        with mock.patch.object(worker.time, "monotonic", clock.monotonic):
            for idle_streak in range(1, 13):
                clock.offset_sec = next_poll_offset
                poll_offsets.append(clock.offset_sec)
                heartbeat_results.append(
                    worker.worker_heartbeat(
                        client,
                        args,
                        "idle",
                        "Fixed heartbeat cadence smoke.",
                    )
                )
                next_poll_offset += worker.backoff_sleep(
                    args.poll_interval,
                    args.idle_backoff_max,
                    idle_streak,
                    args.backoff_factor,
                )

        sent_offsets = [float(post["offset_sec"]) for post in client.posts]
        require(len(sent_offsets) >= 4, f"insufficient heartbeat cycles simulated: {sent_offsets}")
        require(sent_offsets[0] == 0.0, f"first heartbeat was not immediate: {sent_offsets}")
        require(
            sum(bool(result.get("sent")) for result in heartbeat_results) == len(sent_offsets),
            "worker_heartbeat results disagreed with recorded requests",
        )

        no_session_observation = fleet_at(
            worker_fleet,
            now_at=BASE_TIME,
            scoped_heartbeat_at=None,
        )
        require(no_session_observation["worker"].get("heartbeat_state") == "never_seen",
                f"missing Session-bound heartbeat was not fail-closed: {no_session_observation}")
        unverified_agent_status = worker_fleet.build_worker_status_payload(
            worker_agents=[{
                "agent_id": AGENT_ID,
                "workspace_id": WORKSPACE_ID,
                "status": "running",
            }],
            worker_runs=[],
            worker_tasks=[],
            worker_events=[],
            daemons=[],
            stuck_tasks=[],
            remote_fleet=no_session_observation["summary"],
            stuck_workflow_jobs=[],
            adapter_readiness={"summary": {}},
        )
        require(unverified_agent_status.get("running_workers") == 0,
                f"global Agent status was treated as a verified process: {unverified_agent_status}")
        require(unverified_agent_status.get("execution_capacity_workers") == 0,
                f"global Agent status bypassed Session heartbeat authority: {unverified_agent_status}")

        disabled_summary = worker_fleet.build_worker_remote_fleet_summary(
            enrollments=[],
            sessions=[session_fixture(worker_fleet, SESSION_ID, activity_offset_sec=0)],
            agents_by_id={AGENT_ID: {"status": "running"}},
            heartbeats_by_session={
                SESSION_ID: {
                    "last_heartbeat_at": BASE_TIME.isoformat(),
                    "status": "disabled",
                },
            },
            now_dt=BASE_TIME,
        )
        require(disabled_summary.get("service_worker_count") == 0,
                f"disabled Worker retained service capacity or attention state: {disabled_summary}")
        require(disabled_summary.get("stale_service_workers") == 0,
                f"disabled Worker Session created stale Fleet pressure: {disabled_summary}")

        legacy_keyword_summary = worker_fleet.build_worker_remote_fleet_summary(
            enrollments=[],
            sessions=[{
                "session_id": SESSION_ID,
                "agent_id": AGENT_ID,
                "workspace_id": WORKSPACE_ID,
                "session_state": "active",
                "scopes": sorted(worker_fleet.SERVICE_WORKER_EXECUTION_SCOPES),
                "created_at": BASE_TIME.isoformat(),
                "last_used_at": BASE_TIME.isoformat(),
            }],
            agents_by_id={AGENT_ID: {"status": "running"}},
            heartbeats_by_agent={AGENT_ID: {"last_heartbeat_at": BASE_TIME.isoformat()}},
            heartbeats_by_worker={(WORKSPACE_ID, AGENT_ID): {"last_heartbeat_at": BASE_TIME.isoformat()}},
            now_dt=BASE_TIME,
        )
        legacy_keyword_worker = (legacy_keyword_summary.get("service_workers") or [{}])[0]
        require(
            legacy_keyword_worker.get("heartbeat_state") == "never_seen",
            f"legacy unscoped heartbeat keyword regained liveness authority: {legacy_keyword_summary}",
        )

        older_session_id = "ags_heartbeat_cadence_older"
        newer_session_id = "ags_heartbeat_cadence_newer"
        concurrent_sessions = [
            {
                "session_id": older_session_id,
                "agent_id": AGENT_ID,
                "workspace_id": WORKSPACE_ID,
                "session_state": "active",
                "scopes": sorted(worker_fleet.SERVICE_WORKER_EXECUTION_SCOPES),
                "created_at": BASE_TIME.isoformat(),
                "last_used_at": BASE_TIME.isoformat(),
            },
            {
                "session_id": newer_session_id,
                "agent_id": AGENT_ID,
                "workspace_id": WORKSPACE_ID,
                "session_state": "active",
                "scopes": sorted(worker_fleet.SERVICE_WORKER_EXECUTION_SCOPES),
                "created_at": (BASE_TIME + dt.timedelta(seconds=10)).isoformat(),
                "last_used_at": (BASE_TIME + dt.timedelta(seconds=10)).isoformat(),
            },
        ]
        older_heartbeat_at = (BASE_TIME + dt.timedelta(seconds=20)).isoformat()
        concurrent_summary = worker_fleet.build_worker_remote_fleet_summary(
            enrollments=[],
            sessions=concurrent_sessions,
            agents_by_id={AGENT_ID: {"status": "running"}},
            heartbeats_by_session={
                older_session_id: {"last_heartbeat_at": older_heartbeat_at, "status": "idle"},
            },
            now_dt=BASE_TIME + dt.timedelta(seconds=30),
        )
        concurrent_worker = (concurrent_summary.get("service_workers") or [{}])[0]
        require(concurrent_worker.get("last_heartbeat_at") == older_heartbeat_at,
                f"unobserved newer Session shadowed a healthy execution Session: {concurrent_summary}")
        require(concurrent_worker.get("heartbeat_state") == "fresh",
                f"healthy concurrent execution Session lost Fleet capacity: {concurrent_summary}")
        newer_heartbeat_at = (BASE_TIME + dt.timedelta(seconds=25)).isoformat()
        advanced_summary = worker_fleet.build_worker_remote_fleet_summary(
            enrollments=[],
            sessions=concurrent_sessions,
            agents_by_id={AGENT_ID: {"status": "running"}},
            heartbeats_by_session={
                older_session_id: {"last_heartbeat_at": older_heartbeat_at, "status": "idle"},
                newer_session_id: {"last_heartbeat_at": newer_heartbeat_at, "status": "running"},
            },
            now_dt=BASE_TIME + dt.timedelta(seconds=30),
        )
        advanced_worker = (advanced_summary.get("service_workers") or [{}])[0]
        require(advanced_worker.get("last_heartbeat_at") == newer_heartbeat_at,
                f"newer observed execution Session did not take authority: {advanced_summary}")

        mixed_status_results: dict[str, dict[str, object]] = {}
        mixed_healthy_session_id = "ags_mixed_healthy"
        mixed_nonready_session_id = "ags_mixed_nonready"
        mixed_now = BASE_TIME + dt.timedelta(seconds=60)
        mixed_healthy_heartbeat_at = (BASE_TIME + dt.timedelta(seconds=40)).isoformat()
        mixed_nonready_heartbeat_at = (BASE_TIME + dt.timedelta(seconds=50)).isoformat()
        mixed_sessions = [
            session_fixture(
                worker_fleet,
                mixed_healthy_session_id,
                activity_offset_sec=0,
            ),
            session_fixture(
                worker_fleet,
                mixed_nonready_session_id,
                activity_offset_sec=10,
            ),
        ]
        for nonready_status in ("error", "paused"):
            mixed_status_summary = session_summary(
                worker_fleet,
                sessions=mixed_sessions,
                heartbeats_by_session={
                    mixed_healthy_session_id: {
                        "last_heartbeat_at": mixed_healthy_heartbeat_at,
                        "status": "idle",
                    },
                    mixed_nonready_session_id: {
                        "last_heartbeat_at": mixed_nonready_heartbeat_at,
                        "status": nonready_status,
                    },
                },
                now_at=mixed_now,
            )
            mixed_status_worker = (mixed_status_summary.get("service_workers") or [{}])[0]
            require(mixed_status_worker.get("reported_status") == "idle",
                    f"newer {nonready_status} Session shadowed healthy capacity: {mixed_status_summary}")
            require(mixed_status_worker.get("last_heartbeat_at") == mixed_healthy_heartbeat_at,
                    f"healthy Session timestamp was not selected over {nonready_status}: {mixed_status_summary}")
            require(mixed_status_worker.get("selected_session_class") == "fresh_ready",
                    f"mixed {nonready_status} Session selection class was wrong: {mixed_status_summary}")
            require(mixed_status_summary.get("ready_service_workers") == 1,
                    f"mixed {nonready_status} Sessions lost deduplicated capacity: {mixed_status_summary}")
            require(mixed_status_summary.get("unavailable_service_workers") == 0,
                    f"mixed {nonready_status} Sessions marked the Agent unavailable: {mixed_status_summary}")
            require(mixed_status_summary.get("degraded_service_workers") == 1,
                    f"mixed {nonready_status} Sessions did not surface degradation: {mixed_status_summary}")
            require(mixed_status_summary.get("degraded_service_sessions") == 1,
                    f"mixed {nonready_status} Session count was not preserved: {mixed_status_summary}")
            require(
                mixed_status_summary.get("fresh_service_session_status_counts")
                == {"idle": 1, nonready_status: 1},
                f"mixed {nonready_status} status counts were not exact: {mixed_status_summary}",
            )
            require(mixed_status_summary.get("status") == "attention",
                    f"mixed {nonready_status} Sessions did not set Fleet attention: {mixed_status_summary}")

            mixed_status_view = worker_fleet.build_worker_fleet_view(
                daemons=[],
                remote_fleet=mixed_status_summary,
                adapter_readiness={},
                stuck_tasks=[],
                stuck_workflow_jobs=[],
                worker_agents=[],
            )
            mixed_view_summary = mixed_status_view.get("summary") or {}
            mixed_view_lanes = mixed_status_view.get("lanes") or []
            require(mixed_view_summary.get("active_service_workers") == 1,
                    f"Fleet view lost mixed {nonready_status} Agent capacity: {mixed_status_view}")
            require(mixed_view_summary.get("execution_capacity_workers") == 1,
                    f"Fleet view did not deduplicate mixed {nonready_status} capacity: {mixed_status_view}")
            require(mixed_view_summary.get("degraded_service_workers") == 1,
                    f"Fleet view omitted mixed {nonready_status} degradation: {mixed_status_view}")
            require(mixed_status_view.get("status") == "attention",
                    f"Fleet view did not retain mixed {nonready_status} attention: {mixed_status_view}")
            require(len(mixed_view_lanes) == 1 and mixed_view_lanes[0].get("health") == "warn",
                    f"Fleet view lane did not surface mixed {nonready_status} health: {mixed_status_view}")

            mixed_status_payload = worker_fleet.build_worker_status_payload(
                worker_agents=[],
                worker_runs=[],
                worker_tasks=[],
                worker_events=[],
                daemons=[],
                stuck_tasks=[],
                remote_fleet=mixed_status_summary,
                stuck_workflow_jobs=[],
                adapter_readiness={"summary": {}},
            )
            require(mixed_status_payload.get("execution_capacity_workers") == 1,
                    f"Worker status lost mixed {nonready_status} capacity: {mixed_status_payload}")
            require(mixed_status_payload.get("status") == "attention",
                    f"Worker status omitted mixed {nonready_status} attention: {mixed_status_payload}")
            require((mixed_status_payload.get("fleet_health") or {}).get("overall") == "attention",
                    f"Fleet health omitted mixed {nonready_status} attention: {mixed_status_payload}")
            mixed_status_results[nonready_status] = {
                "selected_status": mixed_status_worker.get("reported_status"),
                "capacity": mixed_view_summary.get("execution_capacity_workers"),
                "degraded_workers": mixed_view_summary.get("degraded_service_workers"),
                "fleet_status": mixed_status_view.get("status"),
            }

        error_then_running_summary = session_summary(
            worker_fleet,
            sessions=mixed_sessions,
            heartbeats_by_session={
                mixed_healthy_session_id: {
                    "last_heartbeat_at": mixed_healthy_heartbeat_at,
                    "status": "error",
                },
                mixed_nonready_session_id: {
                    "last_heartbeat_at": mixed_nonready_heartbeat_at,
                    "status": "running",
                },
            },
            now_at=mixed_now,
        )
        error_then_running_worker = (error_then_running_summary.get("service_workers") or [{}])[0]
        require(error_then_running_worker.get("reported_status") == "running",
                f"newer running Session was not selected over error: {error_then_running_summary}")
        require(error_then_running_worker.get("last_heartbeat_at") == mixed_nonready_heartbeat_at,
                f"newer running Session timestamp was not selected: {error_then_running_summary}")
        require(error_then_running_summary.get("ready_service_workers") == 1,
                f"error plus running Sessions lost capacity: {error_then_running_summary}")
        require(error_then_running_summary.get("degraded_service_workers") == 1,
                f"error plus running Sessions did not surface degradation: {error_then_running_summary}")

        invalid_session_results: dict[str, dict[str, object]] = {}
        active_session_id = "ags_state_active"
        invalid_session_id = "ags_state_invalid"
        active_heartbeat_at = (BASE_TIME + dt.timedelta(seconds=30)).isoformat()
        invalid_heartbeat_at = (BASE_TIME + dt.timedelta(seconds=55)).isoformat()
        for invalid_state in ("revoked", "expired"):
            invalid_state_summary = session_summary(
                worker_fleet,
                sessions=[
                    session_fixture(
                        worker_fleet,
                        active_session_id,
                        activity_offset_sec=0,
                    ),
                    session_fixture(
                        worker_fleet,
                        invalid_session_id,
                        activity_offset_sec=20,
                        session_state=invalid_state,
                    ),
                ],
                heartbeats_by_session={
                    active_session_id: {
                        "last_heartbeat_at": active_heartbeat_at,
                        "status": "idle",
                    },
                    invalid_session_id: {
                        "last_heartbeat_at": invalid_heartbeat_at,
                        "status": "running",
                    },
                },
                now_at=mixed_now,
            )
            invalid_state_worker = (invalid_state_summary.get("service_workers") or [{}])[0]
            require(invalid_state_worker.get("last_heartbeat_at") == active_heartbeat_at,
                    f"{invalid_state} Session observation produced authority: {invalid_state_summary}")
            require(invalid_state_summary.get("ready_service_workers") == 1,
                    f"active Session lost capacity beside {invalid_state}: {invalid_state_summary}")
            require(invalid_state_summary.get("degraded_service_workers") == 0,
                    f"{invalid_state} Session polluted degradation counts: {invalid_state_summary}")
            require(invalid_state_summary.get("service_session_status_counts") == {"idle": 1},
                    f"{invalid_state} Session polluted status counts: {invalid_state_summary}")
            require(invalid_state_summary.get(f"{invalid_state}_sessions") == 1,
                    f"{invalid_state} Session was not retained in hygiene counts: {invalid_state_summary}")

            invalid_only_summary = session_summary(
                worker_fleet,
                sessions=[session_fixture(
                    worker_fleet,
                    invalid_session_id,
                    activity_offset_sec=20,
                    session_state=invalid_state,
                )],
                heartbeats_by_session={
                    invalid_session_id: {
                        "last_heartbeat_at": invalid_heartbeat_at,
                        "status": "running",
                    },
                },
                now_at=mixed_now,
            )
            require(invalid_only_summary.get("service_worker_count") == 0,
                    f"{invalid_state}-only Session created a service Worker: {invalid_only_summary}")
            require(invalid_only_summary.get("ready_service_workers") == 0,
                    f"{invalid_state}-only Session created execution capacity: {invalid_only_summary}")
            invalid_session_results[invalid_state] = {
                "selected_status": invalid_state_worker.get("reported_status"),
                "eligible_status_counts": invalid_state_summary.get("service_session_status_counts"),
                "invalid_only_capacity": invalid_only_summary.get("ready_service_workers"),
            }

        stale_session_id = "ags_selection_stale"
        never_seen_session_id = "ags_selection_never_seen"
        stale_heartbeat_at = BASE_TIME.isoformat()
        stale_precedence_summary = session_summary(
            worker_fleet,
            sessions=[
                session_fixture(worker_fleet, stale_session_id, activity_offset_sec=0),
                session_fixture(worker_fleet, never_seen_session_id, activity_offset_sec=20),
            ],
            heartbeats_by_session={
                stale_session_id: {
                    "last_heartbeat_at": stale_heartbeat_at,
                    "status": "idle",
                },
            },
            now_at=BASE_TIME + dt.timedelta(seconds=200),
        )
        stale_precedence_worker = (stale_precedence_summary.get("service_workers") or [{}])[0]
        require(stale_precedence_worker.get("selected_session_class") == "stale_observed",
                f"never-seen Session shadowed stale observed Session: {stale_precedence_summary}")
        require(stale_precedence_worker.get("last_heartbeat_at") == stale_heartbeat_at,
                f"stale observed Session timestamp was not selected: {stale_precedence_summary}")

        never_seen_summary = session_summary(
            worker_fleet,
            sessions=[
                session_fixture(worker_fleet, "ags_never_seen_older", activity_offset_sec=0),
                session_fixture(worker_fleet, "ags_never_seen_newer", activity_offset_sec=20),
            ],
            heartbeats_by_session={},
            now_at=mixed_now,
        )
        never_seen_worker = (never_seen_summary.get("service_workers") or [{}])[0]
        require(never_seen_worker.get("selected_session_class") == "never_seen",
                f"never-seen Session fallback class was wrong: {never_seen_summary}")
        require(
            never_seen_worker.get("selected_session_activity_at")
            == (BASE_TIME + dt.timedelta(seconds=20)).isoformat(),
            f"newest eligible never-seen Session was not selected: {never_seen_summary}",
        )

        initial_fleet = fleet_at(
            worker_fleet,
            now_at=BASE_TIME,
            scoped_heartbeat_at=BASE_TIME,
        )
        freshness_timeout_sec = float(initial_fleet["worker"].get("heartbeat_timeout_sec") or 0)
        require(freshness_timeout_sec > 0, "Fleet service heartbeat timeout must be positive")
        require(
            freshness_timeout_sec > heartbeat_interval_sec,
            "Fleet freshness timeout must be strictly greater than the default heartbeat request interval",
        )
        require(initial_fleet["worker"].get("heartbeat_state") == "fresh",
                f"fresh Session-bound observation was not selected: {initial_fleet}")
        require(initial_fleet["worker"].get("last_heartbeat_at") == BASE_TIME.isoformat(),
                f"Fleet did not select the scoped observation: {initial_fleet['worker']}")
        non_capacity_results: dict[str, dict[str, object]] = {}
        for heartbeat_status in ("paused", "error"):
            non_capacity_fleet = fleet_at(
                worker_fleet,
                now_at=BASE_TIME,
                scoped_heartbeat_at=BASE_TIME,
                heartbeat_status=heartbeat_status,
            )
            require(non_capacity_fleet["worker"].get("heartbeat_state") == "fresh",
                    f"fresh {heartbeat_status} heartbeat should remain observable as live")
            require(non_capacity_fleet["summary"].get("ready_service_workers") == 0,
                    f"{heartbeat_status} heartbeat was counted as execution capacity: {non_capacity_fleet}")
            require(non_capacity_fleet["summary"].get("unavailable_service_workers") == 1,
                    f"{heartbeat_status} heartbeat did not enter unavailable state: {non_capacity_fleet}")
            non_capacity_view = worker_fleet.build_worker_fleet_view(
                daemons=[],
                remote_fleet=non_capacity_fleet["summary"],
                adapter_readiness={},
                stuck_tasks=[],
                stuck_workflow_jobs=[],
                worker_agents=[],
            )
            view_summary = non_capacity_view.get("summary") or {}
            require(view_summary.get("active_service_workers") == 0,
                    f"Fleet view counted {heartbeat_status} heartbeat as active capacity: {non_capacity_view}")
            require(view_summary.get("execution_capacity_workers") == 0,
                    f"Fleet view counted {heartbeat_status} heartbeat as execution capacity: {non_capacity_view}")
            require(non_capacity_view.get("status") == "attention",
                    f"Fleet view did not surface {heartbeat_status} heartbeat attention: {non_capacity_view}")
            non_capacity_results[heartbeat_status] = {
                "ready_workers": non_capacity_fleet["summary"].get("ready_service_workers"),
                "unavailable_workers": non_capacity_fleet["summary"].get("unavailable_service_workers"),
                "fleet_view_capacity": view_summary.get("execution_capacity_workers"),
            }

        disabled_beside_healthy = session_summary(
            worker_fleet,
            sessions=[
                session_fixture(worker_fleet, "ags_disabled_terminal", activity_offset_sec=10),
                session_fixture(worker_fleet, "ags_healthy_concurrent", activity_offset_sec=20),
            ],
            heartbeats_by_session={
                "ags_disabled_terminal": {
                    "last_heartbeat_at": (BASE_TIME + dt.timedelta(seconds=20)).isoformat(),
                    "status": "disabled",
                },
                "ags_healthy_concurrent": {
                    "last_heartbeat_at": (BASE_TIME + dt.timedelta(seconds=20)).isoformat(),
                    "status": "idle",
                },
            },
            now_at=BASE_TIME + dt.timedelta(seconds=20),
        )
        disabled_beside_healthy_worker = (disabled_beside_healthy.get("service_workers") or [{}])[0]
        require(disabled_beside_healthy.get("service_worker_count") == 1,
                f"terminal Session removed a concurrent healthy Worker: {disabled_beside_healthy}")
        require(disabled_beside_healthy.get("ready_service_workers") == 1,
                f"terminal Session removed concurrent execution capacity: {disabled_beside_healthy}")
        require(disabled_beside_healthy_worker.get("degraded_session_count") == 0,
                f"terminal Session polluted concurrent Worker degradation: {disabled_beside_healthy}")
        require(
            client.session_observations.get(SESSION_ID, {}).get("last_heartbeat_at")
            == client.posts[-1]["timestamp"],
            "each heartbeat request did not update the scoped Worker observation",
        )
        require(
            len([post for post in client.posts if post.get("scoped_observation")]) == len(sent_offsets),
            "scoped observation update count did not match heartbeat request count",
        )

        request_gaps = [later - earlier for earlier, later in zip(sent_offsets, sent_offsets[1:])]
        max_request_gap_sec = max(request_gaps)
        require(
            max_request_gap_sec <= freshness_timeout_sec,
            f"default polling/backoff produced a heartbeat gap beyond Fleet freshness: {request_gaps}",
        )

        sample_offsets: set[float] = set()
        for earlier, later in zip(sent_offsets, sent_offsets[1:]):
            sample_offsets.update(float(second) for second in range(int(earlier), int(later) + 1))
            sample_offsets.add(max(earlier, later - 0.001))

        stale_samples: list[dict[str, object]] = []
        for sample_offset in sorted(sample_offsets):
            heartbeat_offset = max(offset for offset in sent_offsets if offset <= sample_offset)
            fleet = fleet_at(
                worker_fleet,
                now_at=clock.utc_at(sample_offset),
                scoped_heartbeat_at=clock.utc_at(heartbeat_offset),
            )
            service_worker = fleet["worker"]
            if service_worker.get("heartbeat_state") != "fresh":
                stale_samples.append({
                    "sample_offset_sec": sample_offset,
                    "heartbeat_offset_sec": heartbeat_offset,
                    "state": service_worker.get("heartbeat_state"),
                })
            require(fleet["summary"].get("fresh_service_workers") == 1,
                    f"fresh service Worker count dropped at t={sample_offset}: {fleet['summary']}")
            require(fleet["summary"].get("stale_service_workers") == 0,
                    f"stale service Worker appeared at t={sample_offset}: {fleet['summary']}")

        require(not stale_samples, f"periodic stale window detected: {stale_samples}")

        last_heartbeat_offset = sent_offsets[-1]
        at_timeout = fleet_at(
            worker_fleet,
            now_at=clock.utc_at(last_heartbeat_offset + freshness_timeout_sec),
            scoped_heartbeat_at=clock.utc_at(last_heartbeat_offset),
        )
        after_timeout = fleet_at(
            worker_fleet,
            now_at=clock.utc_at(last_heartbeat_offset + freshness_timeout_sec + 0.001),
            scoped_heartbeat_at=clock.utc_at(last_heartbeat_offset),
        )
        require(at_timeout["worker"].get("heartbeat_state") == "fresh",
                "Fleet freshness boundary changed from inclusive to exclusive")
        require(after_timeout["worker"].get("heartbeat_state") == "stale",
                "Fleet did not mark an actually expired service heartbeat stale")

        mixed_now = dt.datetime(2035, 1, 2, 3, 40, tzinfo=dt.timezone.utc)
        mixed_summary = worker_fleet.build_worker_remote_fleet_summary(
            enrollments=[],
            sessions=[
                {
                    "session_id": "ags_mixed_timezone_older",
                    "agent_id": AGENT_ID,
                    "workspace_id": WORKSPACE_ID,
                    "session_state": "active",
                    "scopes": sorted(worker_fleet.SERVICE_WORKER_EXECUTION_SCOPES),
                    "created_at": "2035-01-02T04:00:00+08:00",
                    "last_used_at": "2035-01-02T04:00:00+08:00",
                },
                {
                    "session_id": "ags_mixed_timezone_newer",
                    "agent_id": AGENT_ID,
                    "workspace_id": WORKSPACE_ID,
                    "session_state": "active",
                    "scopes": sorted(worker_fleet.SERVICE_WORKER_EXECUTION_SCOPES),
                    "created_at": "2035-01-02T03:30:00Z",
                    "last_used_at": "2035-01-02T03:30:00Z",
                },
            ],
            agents_by_id={AGENT_ID: {"name": "Mixed timezone fixture"}},
            heartbeats_by_session={
                "ags_mixed_timezone_newer": {
                    "last_heartbeat_at": "2035-01-02T03:39:30Z",
                    "status": "idle",
                },
            },
            now_dt=mixed_now,
        )
        mixed_worker = (mixed_summary.get("service_workers") or [{}])[0]
        require(
            mixed_worker.get("heartbeat_state") == "fresh",
            f"mixed-offset Session ordering selected the wrong Worker Session: {mixed_summary}",
        )

        expiry_now = dt.datetime(2035, 1, 2, 4, 0, tzinfo=dt.timezone.utc)
        session_state_results = {
            "future_z": server_module.agent_gateway_session_state(
                {"status": "active", "expires_at": "2035-01-02T04:00:01Z"},
                expiry_now,
            ),
            "exact_now": server_module.agent_gateway_session_state(
                {"status": "active", "expires_at": expiry_now.isoformat()},
                expiry_now,
            ),
            "invalid": server_module.agent_gateway_session_state(
                {"status": "active", "expires_at": "not-an-iso-timestamp"},
                expiry_now,
            ),
            "missing": server_module.agent_gateway_session_state(
                {"status": "active", "expires_at": None},
                expiry_now,
            ),
            "revoked": server_module.agent_gateway_session_state(
                {"status": "revoked", "expires_at": "2035-01-02T04:00:01Z"},
                expiry_now,
            ),
        }
        require(session_state_results["future_z"] == "active",
                f"future Z expiry did not remain active: {session_state_results}")
        require(session_state_results["exact_now"] == "expired",
                f"exact-now Session expiry did not fail closed: {session_state_results}")
        require(session_state_results["invalid"] == "invalid_expiry",
                f"invalid Session expiry did not fail closed: {session_state_results}")
        require(session_state_results["missing"] == "invalid_expiry",
                f"missing Session expiry did not fail closed: {session_state_results}")
        require(session_state_results["revoked"] == "revoked",
                f"revoked Session was reactivated by its expiry: {session_state_results}")

        print(json.dumps({
            "ok": True,
            "default_heartbeat_interval_sec": heartbeat_interval_sec,
            "default_idle_backoff_max_sec": float(args.idle_backoff_max),
            "fleet_service_freshness_timeout_sec": freshness_timeout_sec,
            "poll_offsets_sec": poll_offsets,
            "heartbeat_request_offsets_sec": sent_offsets,
            "max_heartbeat_request_gap_sec": max_request_gap_sec,
            "fleet_samples_checked": len(sample_offsets),
            "periodic_stale_windows": len(stale_samples),
            "missing_session_observation_state": no_session_observation["worker"].get("heartbeat_state"),
            "legacy_keyword_observation_state": legacy_keyword_worker.get("heartbeat_state"),
            "unverified_agent_execution_capacity": unverified_agent_status.get("execution_capacity_workers"),
            "concurrent_session_selected_heartbeat": concurrent_worker.get("last_heartbeat_at"),
            "concurrent_session_advanced_heartbeat": advanced_worker.get("last_heartbeat_at"),
            "mixed_session_statuses": mixed_status_results,
            "error_then_running_selected_status": error_then_running_worker.get("reported_status"),
            "revoked_expired_session_results": invalid_session_results,
            "stale_observed_selection_class": stale_precedence_worker.get("selected_session_class"),
            "never_seen_selection_activity_at": never_seen_worker.get("selected_session_activity_at"),
            "fresh_session_observation_state": initial_fleet["worker"].get("heartbeat_state"),
            "non_capacity_session_statuses": non_capacity_results,
            "session_observation_updates": len(client.posts),
            "boundary_state_at_timeout": at_timeout["worker"].get("heartbeat_state"),
            "boundary_state_after_timeout": after_timeout["worker"].get("heartbeat_state"),
            "mixed_timezone_session_selection": mixed_worker.get("heartbeat_state"),
            "session_expiry_states": session_state_results,
            "service_started": False,
            "database_read": False,
            "credentials_read": False,
        }, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2, sort_keys=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
