#!/usr/bin/env python3
"""Verify intake-blocked Workers stay fresh without heartbeat ledger amplification."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def heartbeat_counts(server, agent_id: str) -> dict[str, int]:
    with server.db() as conn:
        return {
            "runtime_events": conn.execute(
                "SELECT COUNT(*) FROM runtime_events WHERE event_type='agent.heartbeat' AND agent_id=?",
                (agent_id,),
            ).fetchone()[0],
            "audit_logs": conn.execute(
                "SELECT COUNT(*) FROM audit_logs WHERE action='agent_gateway.heartbeat' AND actor_id=?",
                (agent_id,),
            ).fetchone()[0],
        }


def pull_counts(server, agent_id: str) -> dict[str, int]:
    with server.db() as conn:
        return {
            "runtime_events": conn.execute(
                "SELECT COUNT(*) FROM runtime_events WHERE event_type='task.pull' AND agent_id=?",
                (agent_id,),
            ).fetchone()[0],
            "audit_logs": conn.execute(
                "SELECT COUNT(*) FROM audit_logs WHERE action='agent_gateway.task_pull' AND actor_id=?",
                (agent_id,),
            ).fetchone()[0],
        }


class AuthenticatedHeartbeatClient:
    workspace_id = "local-demo"
    agent_id = "agt_worker_intake_heartbeat_fleet"

    def __init__(self, server, session_token: str, *, workspace_id: str = "local-demo", agent_id: str | None = None) -> None:
        self.server = server
        self._session_token = session_token
        self.workspace_id = workspace_id
        self.agent_id = agent_id or type(self).agent_id
        self.heartbeat_responses: list[dict] = []
        self.pull_responses: list[dict] = []

    def get(self, path: str, query: dict | None = None) -> dict:
        if path != "/api/agent-gateway/tasks/pull":
            raise AssertionError(f"unexpected GET {path}")
        headers = {"Authorization": f"Bearer {self._session_token}"}
        normalized_query = {
            key: value if isinstance(value, list) else [str(value)]
            for key, value in (query or {}).items()
        }
        with self.server.db() as conn:
            auth_ctx, auth_error = self.server.agent_gateway_auth_context(
                conn,
                headers,
                required_scope="tasks:read",
            )
            if auth_error:
                raise RuntimeError(auth_error.get("error") or "pull_auth_failed")
            response, status = self.server.agent_gateway_pull_tasks(
                conn,
                normalized_query,
                headers,
                auth_ctx,
            )
            if status != 200:
                raise RuntimeError(response.get("error") or "pull_failed")
            conn.commit()
        self.pull_responses.append(response)
        return response

    def post(self, path: str, payload: dict, timeout: int = 180) -> dict:
        if path != "/api/agent-gateway/heartbeat":
            raise AssertionError(f"unexpected POST {path}")
        headers = {"Authorization": f"Bearer {self._session_token}"}
        with self.server.db() as conn:
            auth_ctx, auth_error = self.server.agent_gateway_auth_context(
                conn,
                headers,
                required_scope="agents:heartbeat",
            )
            if auth_error:
                raise RuntimeError(auth_error.get("error") or "heartbeat_auth_failed")
            body = dict(payload)
            body["agent_id"] = auth_ctx["agent_id"]
            body["workspace_id"] = auth_ctx["workspace_id"]
            body["_auth_token_id"] = auth_ctx.get("token_id")
            body["_auth_session_id"] = auth_ctx.get("session_id")
            response, status = self.server.agent_gateway_heartbeat(conn, body)
            if status != 200:
                raise RuntimeError(response.get("error") or "heartbeat_failed")
            conn.commit()
        self.heartbeat_responses.append(response)
        return response


def main() -> int:
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="agentops-worker-intake-heartbeat-") as tmp:
        os.environ["AGENTOPS_DB_PATH"] = str(Path(tmp) / "agentops_mis.db")
        os.environ["AGENTOPS_LIVE_RUNTIME_DISABLED"] = "true"
        os.environ["AGENTOPS_SKIP_SEED_EXPORTS"] = "1"
        os.environ["AGENTOPS_WORKER_RUNTIME_DIR"] = str(Path(tmp) / "worker-runtime")
        os.environ["AGENTOPS_API_KEY"] = "worker-heartbeat-machine-fixture"

        import server
        from agentops_mis_cli import worker
        from agentops_mis_core.worker_fleet import SERVICE_WORKER_EXECUTION_SCOPES

        primary_db_path = server.DB_PATH
        legacy_db_path = Path(tmp) / "legacy_preview38.db"
        with sqlite3.connect(legacy_db_path) as legacy_conn:
            legacy_conn.execute(
                """CREATE TABLE agent_gateway_heartbeat_observations (
                    workspace_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    last_ledger_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(workspace_id, agent_id)
                )"""
            )
            legacy_conn.execute(
                """INSERT INTO agent_gateway_heartbeat_observations(
                    workspace_id,agent_id,status,last_ledger_at,updated_at
                ) VALUES(?,?,?,?,?)""",
                (
                    "local-demo",
                    "agt_worker_legacy_observation_fixture",
                    "idle",
                    datetime.now(timezone.utc).isoformat(),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            legacy_conn.commit()
        try:
            server.DB_PATH = legacy_db_path
            server.init_schema()
            with sqlite3.connect(legacy_db_path) as legacy_conn:
                new_session_table = legacy_conn.execute(
                    """SELECT 1 FROM sqlite_master
                    WHERE type='table' AND name='agent_gateway_session_heartbeat_observations'"""
                ).fetchone()
                legacy_row_count = legacy_conn.execute(
                    "SELECT COUNT(*) FROM agent_gateway_heartbeat_observations"
                ).fetchone()[0]
                migrated_session_row_count = legacy_conn.execute(
                    "SELECT COUNT(*) FROM agent_gateway_session_heartbeat_observations"
                ).fetchone()[0]
        finally:
            server.DB_PATH = primary_db_path
        require(bool(new_session_table), "legacy schema upgrade did not create Session heartbeat observations", failures)
        require(legacy_row_count == 1, "legacy heartbeat sampling row was not preserved", failures)
        require(migrated_session_row_count == 0,
                "legacy per-Agent observation was incorrectly promoted to Session liveness", failures)

        server.seed(reset=True)
        worker_scopes = list(worker.LOCAL_CONFIG_WORKER_SESSION_SCOPES)
        require(set(worker_scopes) == set(SERVICE_WORKER_EXECUTION_SCOPES),
                "Fleet execution scopes drifted from the real Worker session policy", failures)
        with server.db() as conn:
            enrollment, enrollment_status = server.agent_gateway_create_enrollment(conn, {
                "agent_id": AuthenticatedHeartbeatClient.agent_id,
                "name": "Worker Intake Heartbeat Fleet Smoke",
                "runtime_type": "openclaw",
                "workspace_id": "local-demo",
                "scopes": worker_scopes,
                "heartbeat_timeout_sec": 300,
                "ttl_days": 1,
            })
            require(enrollment_status == 201, f"enrollment failed: {enrollment_status}", failures)
            token = enrollment.get("token") or ""
            session, session_status = server.agent_gateway_create_session(
                conn,
                {"Authorization": f"Bearer {token}"},
                {"scopes": worker_scopes, "ttl_sec": 900},
            )
            require(session_status == 201, f"session failed: {session_status}", failures)
            session_token = session.get("session_token") or ""
            task, task_status = server.create_task_api(conn, {
                "task_id": "tsk_worker_intake_heartbeat_fleet",
                "workspace_id": "local-demo",
                "title": "Worker intake heartbeat Fleet smoke",
                "description": "Remain blocked at the Agent Plan intake gate.",
                "acceptance_criteria": "No Runtime executes while the Worker remains visible.",
                "owner_agent_id": AuthenticatedHeartbeatClient.agent_id,
                "status": "planned",
                "priority": "medium",
                "risk_level": "low",
            })
            require(task_status == 201 and task.get("task_id"), f"task create failed: {task_status}", failures)
            conn.commit()

        client = AuthenticatedHeartbeatClient(server, session_token)
        args = worker.build_parser().parse_args([
            "--once",
            "--adapter",
            "openclaw",
            "--confirm-run",
            "--no-auto-plan-intake",
            "--heartbeat-interval-sec",
            "60",
        ])
        before = heartbeat_counts(server, client.agent_id)
        pulls_before = pull_counts(server, client.agent_id)
        results = [worker.process_one_task(client, args) for _ in range(20)]
        after_repeated = heartbeat_counts(server, client.agent_id)

        client._worker_heartbeat_sent_at = time.monotonic() - 61
        forced_result = worker.process_one_task(client, args)
        after_due_client = heartbeat_counts(server, client.agent_id)
        pulls_after = pull_counts(server, client.agent_id)

        with server.db() as conn:
            enrollment_row = conn.execute(
                "SELECT last_heartbeat_at FROM agent_gateway_tokens WHERE token_id=?",
                (enrollment.get("token_id"),),
            ).fetchone()
            fleet = server.worker_remote_fleet_summary(conn, "local-demo")
            status = server.worker_status(conn, refresh_runtime=False, workspace_id="local-demo")

        remote_worker = next(
            (
                item
                for item in (fleet.get("remote_workers") or [])
                if item.get("agent_id") == client.agent_id
            ),
            {},
        )
        require(all(result.get("reason") == "intake_blocked" for result in results), "blocked intake result changed", failures)
        require(forced_result.get("reason") == "intake_blocked", "due-client result changed", failures)
        require(len(client.heartbeat_responses) == 2, f"client cadence mismatch: {len(client.heartbeat_responses)}", failures)
        require(client.heartbeat_responses[0].get("ledger_recorded") is True, "first heartbeat missed ledger evidence", failures)
        require(client.heartbeat_responses[1].get("ledger_recorded") is False, "same-state heartbeat was not coalesced", failures)
        require(after_repeated["runtime_events"] - before["runtime_events"] == 1, f"runtime heartbeat amplification: {after_repeated}", failures)
        require(after_repeated["audit_logs"] - before["audit_logs"] == 1, f"audit heartbeat amplification: {after_repeated}", failures)
        require(after_due_client == after_repeated, f"server coalescing changed ledger counts: {after_due_client}", failures)
        require(pulls_after["runtime_events"] - pulls_before["runtime_events"] == 1, f"task.pull Runtime Event amplification: {pulls_after}", failures)
        require(pulls_after["audit_logs"] - pulls_before["audit_logs"] == 1, f"task.pull Audit amplification: {pulls_after}", failures)
        require(sum(bool((response.get("observation") or {}).get("ledger_recorded")) for response in client.pull_responses) == 1,
                "unchanged pull state was not coalesced", failures)
        require(bool(enrollment_row and enrollment_row["last_heartbeat_at"]), "token heartbeat timestamp missing", failures)
        require(remote_worker.get("heartbeat_state") == "fresh", f"Fleet did not become fresh: {remote_worker}", failures)
        require(int(remote_worker.get("active_session_count") or 0) == 1, f"Fleet session state missing: {remote_worker}", failures)
        service_worker = next((
            item for item in (fleet.get("service_workers") or [])
            if item.get("agent_id") == client.agent_id and item.get("workspace_id") == "local-demo"
        ), {})
        require(service_worker.get("heartbeat_state") == "fresh", f"service Worker projection stayed stale: {service_worker}", failures)
        require(fleet.get("fresh_service_workers") == 1, f"fresh service Worker count mismatch: {fleet}", failures)
        require(status.get("active_service_workers") == 1, f"active service Worker count mismatch: {status}", failures)
        require(status.get("execution_capacity_workers") == 1, f"execution capacity mismatch: {status}", failures)
        deduplicated_status = server.build_worker_status_payload(
            worker_agents=[],
            worker_runs=[],
            worker_tasks=[],
            worker_events=[],
            daemons=[{
                "running": True,
                "agent_id": client.agent_id,
                "adapter": "openclaw",
                "process_claim_active": True,
                "process_identity_verified": True,
            }],
            stuck_tasks=[],
            remote_fleet=fleet,
            stuck_workflow_jobs=[],
            adapter_readiness={"summary": {}},
        )
        require(deduplicated_status.get("running_workers") == 1,
                f"local daemon count mismatch: {deduplicated_status}", failures)
        require(deduplicated_status.get("active_service_workers") == 1,
                f"service Worker count changed during deduplication: {deduplicated_status}", failures)
        require(deduplicated_status.get("execution_capacity_workers") == 1,
                f"same Worker was counted twice as execution capacity: {deduplicated_status}", failures)

        aged_pull_at = (datetime.now(timezone.utc) - timedelta(seconds=901)).isoformat()
        with server.db() as conn:
            conn.execute(
                "UPDATE agent_gateway_pull_observations SET last_ledger_at=? WHERE workspace_id=? AND agent_id=?",
                (aged_pull_at, "local-demo", client.agent_id),
            )
            conn.commit()
        aged_pull = client.get("/api/agent-gateway/tasks/pull", {
            "agent_id": client.agent_id,
            "workspace_id": "local-demo",
            "limit": 1,
            "status": ["planned"],
            "enforce_intake": "true",
        })
        pulls_after_interval = pull_counts(server, client.agent_id)
        require((aged_pull.get("observation") or {}).get("ledger_recorded") is True,
                "aged unchanged pull was not sampled", failures)
        require((aged_pull.get("observation") or {}).get("state_changed") is False,
                "aged unchanged pull was mislabeled as a queue change", failures)
        require(pulls_after_interval["runtime_events"] - pulls_after["runtime_events"] == 1,
                f"aged pull Runtime Event sample missing: {pulls_after_interval}", failures)
        require(pulls_after_interval["audit_logs"] - pulls_after["audit_logs"] == 1,
                f"aged pull Audit sample missing: {pulls_after_interval}", failures)

        with server.db() as conn:
            restricted_session, restricted_session_status = server.agent_gateway_create_session(
                conn,
                {"Authorization": f"Bearer {token}"},
                {"scopes": ["tasks:read"], "ttl_sec": 900},
            )
            conn.commit()
        restricted_client = AuthenticatedHeartbeatClient(
            server,
            restricted_session.get("session_token") or "",
        )
        restricted_rejected = False
        try:
            worker.process_one_task(restricted_client, args)
        except RuntimeError as exc:
            restricted_rejected = str(exc) == "forbidden"
        require(restricted_session_status == 201, "restricted session setup failed", failures)
        require(restricted_rejected, "real scoped Session heartbeat rejection did not propagate", failures)

        observer_agent_id = "agt_worker_read_only_observer"
        with server.db() as conn:
            observer_enrollment, observer_enrollment_status = server.agent_gateway_create_enrollment(conn, {
                "agent_id": observer_agent_id,
                "name": "Read-only Fleet Observer",
                "runtime_type": "openclaw",
                "workspace_id": "local-demo",
                "scopes": ["tasks:read"],
                "heartbeat_timeout_sec": 300,
                "ttl_days": 1,
            })
            observer_session, observer_session_status = server.agent_gateway_create_session(
                conn,
                {"Authorization": f"Bearer {observer_enrollment.get('token') or ''}"},
                {"scopes": ["tasks:read"], "ttl_sec": 900},
            )
        observer_client = AuthenticatedHeartbeatClient(
            server,
            observer_session.get("session_token") or "",
            agent_id=observer_agent_id,
        )
        observer_client.get("/api/agent-gateway/tasks/pull", {
            "agent_id": observer_agent_id,
            "workspace_id": "local-demo",
            "limit": 1,
            "status": ["planned"],
            "enforce_intake": "true",
        })
        with server.db() as conn:
            observer_fleet = server.worker_remote_fleet_summary(conn, "local-demo")
            observer_status = server.worker_status(conn, refresh_runtime=False, workspace_id="local-demo")
        observer_remote_worker = next((
            item for item in (observer_fleet.get("remote_workers") or [])
            if item.get("agent_id") == observer_agent_id
        ), {})
        observer_service_worker = next((
            item for item in (observer_fleet.get("service_workers") or [])
            if item.get("agent_id") == observer_agent_id
        ), {})
        require(observer_enrollment_status == 201 and observer_session_status == 201,
                "read-only observer fixture setup failed", failures)
        require(observer_remote_worker.get("active_session_count") == 1,
                f"read-only Session activity was not visible: {observer_remote_worker}", failures)
        require(not observer_service_worker,
                f"read-only Session was misclassified as a service Worker: {observer_service_worker}", failures)
        require(observer_status.get("execution_capacity_workers") == 1,
                f"read-only Session inflated execution capacity: {observer_status}", failures)

        with server.db() as conn:
            other_enrollment, other_enrollment_status = server.agent_gateway_create_enrollment(conn, {
                "agent_id": client.agent_id,
                "name": "Same Agent Other Workspace",
                "runtime_type": "openclaw",
                "workspace_id": "workspace-other",
                "scopes": worker_scopes,
                "heartbeat_timeout_sec": 300,
                "ttl_days": 1,
            })
            other_token = other_enrollment.get("token") or ""
            other_session, other_session_status = server.agent_gateway_create_session(
                conn,
                {"Authorization": f"Bearer {other_token}"},
                {"scopes": worker_scopes, "ttl_sec": 900},
            )
            conn.commit()
        other_client = AuthenticatedHeartbeatClient(
            server,
            other_session.get("session_token") or "",
            workspace_id="workspace-other",
            agent_id=client.agent_id,
        )
        other_heartbeat = other_client.post("/api/agent-gateway/heartbeat", {
            "workspace_id": "workspace-other",
            "agent_id": client.agent_id,
            "status": "idle",
            "summary": "Other workspace heartbeat sampling smoke.",
            "runtime_type": "openclaw",
        })

        other_only_agent_id = "agt_worker_other_workspace_only"
        other_only_task_id = "tsk_worker_other_workspace_only"
        other_only_run_id = "run_worker_other_workspace_only"
        with server.db() as conn:
            other_only_enrollment, other_only_enrollment_status = server.agent_gateway_create_enrollment(conn, {
                "agent_id": other_only_agent_id,
                "name": "Other Workspace Only Worker",
                "runtime_type": "openclaw",
                "workspace_id": "workspace-other",
                "scopes": worker_scopes,
                "heartbeat_timeout_sec": 300,
                "ttl_days": 1,
            })
            other_only_token = other_only_enrollment.get("token") or ""
            other_only_session, other_only_session_status = server.agent_gateway_create_session(
                conn,
                {"Authorization": f"Bearer {other_only_token}"},
                {"scopes": worker_scopes, "ttl_sec": 900},
            )
            other_only_task, other_only_task_status = server.create_task_api(conn, {
                "task_id": other_only_task_id,
                "workspace_id": "workspace-other",
                "title": "Other workspace isolation fixture",
                "description": "Must never appear in the local-demo Worker status read model.",
                "acceptance_criteria": "Current workspace remains isolated.",
                "owner_agent_id": other_only_agent_id,
                "status": "running",
                "priority": "medium",
                "risk_level": "low",
            })
            fixture_at = server.now_iso()
            server.upsert_run(conn, {
                "run_id": other_only_run_id,
                "workspace_id": "workspace-other",
                "task_id": other_only_task_id,
                "agent_id": other_only_agent_id,
                "runtime_type": "openclaw",
                "status": "running",
                "started_at": fixture_at,
                "ended_at": None,
                "duration_ms": None,
                "input_summary": "Other workspace fixture input omitted.",
                "output_summary": None,
                "model_provider": "fixture",
                "model_name": "fixture",
                "input_tokens": 0,
                "output_tokens": 0,
                "reasoning_tokens": 0,
                "cost_usd": 0,
                "error_type": None,
                "error_message": None,
                "trace_id": "trace_worker_other_workspace_only",
                "parent_run_id": None,
                "delegation_id": "worker:workspace-other",
                "approval_required": 0,
                "created_at": fixture_at,
            }, "worker-workspace-isolation-smoke")
            server.runtime_event(
                conn,
                "rtc_agent_gateway_local",
                "task.claim",
                "running",
                run_id=other_only_run_id,
                task_id=other_only_task_id,
                agent_id=other_only_agent_id,
                output_summary="Other workspace fixture event.",
            )
            old_fixture_at = (datetime.now(timezone.utc) - timedelta(seconds=1200)).isoformat()
            conn.execute("UPDATE tasks SET updated_at=? WHERE task_id=?", (old_fixture_at, other_only_task_id))
            conn.execute("UPDATE runs SET started_at=? WHERE run_id=?", (old_fixture_at, other_only_run_id))
            conn.commit()
        other_only_client = AuthenticatedHeartbeatClient(
            server,
            other_only_session.get("session_token") or "",
            workspace_id="workspace-other",
            agent_id=other_only_agent_id,
        )
        other_only_heartbeat = other_only_client.post("/api/agent-gateway/heartbeat", {
            "workspace_id": "workspace-other",
            "agent_id": other_only_agent_id,
            "status": "running",
            "summary": "Other workspace isolation heartbeat.",
            "runtime_type": "openclaw",
        })
        with server.db() as conn:
            stale_at = (datetime.now(timezone.utc) - timedelta(seconds=600)).isoformat()
            conn.execute(
                "UPDATE agent_gateway_tokens SET last_heartbeat_at=?,last_used_at=? WHERE token_id=?",
                (stale_at, stale_at, other_enrollment.get("token_id")),
            )
            conn.execute(
                "UPDATE agent_gateway_sessions SET last_used_at=? WHERE session_id=?",
                (stale_at, other_session.get("session_id")),
            )
            conn.execute(
                "UPDATE agent_gateway_heartbeat_observations SET updated_at=? WHERE workspace_id=? AND agent_id=?",
                (stale_at, "workspace-other", client.agent_id),
            )
            conn.execute(
                "UPDATE agent_gateway_session_heartbeat_observations SET updated_at=? WHERE session_id=?",
                (stale_at, other_session.get("session_id")),
            )
            conn.commit()
            scoped_fleet = server.worker_remote_fleet_summary(conn)
            primary_workspace_fleet = server.worker_remote_fleet_summary(conn, "local-demo")
            scoped_status = server.worker_status(conn, refresh_runtime=False, workspace_id="local-demo")
            heartbeat_observation_workspaces = {
                row["workspace_id"]
                for row in conn.execute(
                    "SELECT workspace_id FROM agent_gateway_heartbeat_observations WHERE agent_id=?",
                    (client.agent_id,),
                ).fetchall()
            }
        require(other_enrollment_status == 201 and other_session_status == 201, "cross-workspace fixture setup failed", failures)
        require(
            other_only_enrollment_status == 201
            and other_only_session_status == 201
            and other_only_task_status == 201
            and bool(other_only_task.get("task_id"))
            and other_only_heartbeat.get("ledger_recorded") is True,
            "other-workspace-only fixture setup failed",
            failures,
        )
        require(other_heartbeat.get("ledger_recorded") is True,
                f"other workspace heartbeat was suppressed: {other_heartbeat}", failures)
        require(heartbeat_observation_workspaces == {"local-demo", "workspace-other"},
                f"heartbeat sampling state crossed workspace boundary: {heartbeat_observation_workspaces}", failures)
        scoped_service_workers = {
            item.get("workspace_id"): item
            for item in (scoped_fleet.get("service_workers") or [])
            if item.get("agent_id") == client.agent_id
        }
        require(scoped_service_workers.get("local-demo", {}).get("heartbeat_state") == "fresh",
                f"primary workspace lost fresh state: {scoped_service_workers}", failures)
        require(scoped_service_workers.get("workspace-other", {}).get("heartbeat_state") == "stale",
                f"fresh heartbeat crossed workspace boundary: {scoped_service_workers}", failures)
        require(scoped_status.get("active_service_workers") == 1,
                f"stale workspace was counted as active service capacity: {scoped_status}", failures)
        require(scoped_status.get("execution_capacity_workers") == 1,
                f"cross-workspace capacity count mismatch: {scoped_status}", failures)
        require(
            {
                item.get("workspace_id")
                for item in (primary_workspace_fleet.get("service_workers") or [])
            } == {"local-demo"},
            f"workspace-scoped Fleet exposed another workspace: {primary_workspace_fleet}",
            failures,
        )
        require(
            other_only_agent_id not in {
                item.get("agent_id") for item in (scoped_status.get("workers") or [])
            },
            f"workspace-scoped Worker status exposed another workspace Agent: {scoped_status}",
            failures,
        )
        require(
            other_only_task_id not in {
                item.get("task_id") for item in (scoped_status.get("recent_tasks") or [])
            }
            and other_only_run_id not in {
                item.get("run_id") for item in (scoped_status.get("recent_runs") or [])
            }
            and other_only_task_id not in {
                item.get("task_id") for item in (scoped_status.get("stuck_tasks") or [])
            },
            f"workspace-scoped Worker status exposed another workspace ledger row: {scoped_status}",
            failures,
        )
        require(
            other_only_run_id not in {
                item.get("run_id") for item in (scoped_status.get("recent_events") or [])
            },
            f"workspace-scoped Worker status exposed another workspace Runtime Event: {scoped_status}",
            failures,
        )

        concurrent_agent_id = "agt_worker_heartbeat_concurrency"
        with server.db() as conn:
            concurrent_enrollment, concurrent_enrollment_status = server.agent_gateway_create_enrollment(conn, {
                "agent_id": concurrent_agent_id,
                "name": "Worker Heartbeat Concurrency Smoke",
                "runtime_type": "openclaw",
                "workspace_id": "local-demo",
                "scopes": ["agents:heartbeat"],
                "heartbeat_timeout_sec": 300,
                "ttl_days": 1,
            })
            conn.commit()
        require(concurrent_enrollment_status == 201, "concurrent enrollment failed", failures)

        def concurrent_heartbeat(_index: int) -> bool:
            with server.db() as conn:
                response, status = server.agent_gateway_heartbeat(conn, {
                    "agent_id": concurrent_agent_id,
                    "workspace_id": "local-demo",
                    "runtime_type": "openclaw",
                    "status": "idle",
                    "summary": "Concurrent heartbeat smoke.",
                    "_auth_token_id": concurrent_enrollment.get("token_id"),
                })
                conn.commit()
            if status != 200:
                raise RuntimeError(response.get("error") or "concurrent_heartbeat_failed")
            return bool(response.get("ledger_recorded"))

        with ThreadPoolExecutor(max_workers=20) as pool:
            concurrent_recorded = list(pool.map(concurrent_heartbeat, range(20)))
        concurrent_counts = heartbeat_counts(server, concurrent_agent_id)
        require(sum(concurrent_recorded) == 1, f"concurrent ledger decisions were not atomic: {concurrent_recorded}", failures)
        require(concurrent_counts == {"runtime_events": 1, "audit_logs": 1}, f"concurrent heartbeat amplification: {concurrent_counts}", failures)

        with server.db() as conn:
            conn.execute(
                "DELETE FROM agent_gateway_pull_observations WHERE workspace_id=? AND agent_id=?",
                ("local-demo", client.agent_id),
            )
        concurrent_pull_before = pull_counts(server, client.agent_id)

        def concurrent_pull(_index: int) -> bool:
            headers = {"Authorization": f"Bearer {session_token}"}
            query = {
                "agent_id": [client.agent_id],
                "workspace_id": ["local-demo"],
                "limit": ["1"],
                "status": ["planned"],
                "enforce_intake": ["true"],
            }
            with server.db() as conn:
                auth_ctx, auth_error = server.agent_gateway_auth_context(
                    conn,
                    headers,
                    required_scope="tasks:read",
                )
                if auth_error:
                    raise RuntimeError(auth_error.get("error") or "concurrent_pull_auth_failed")
                response, status = server.agent_gateway_pull_tasks(conn, query, headers, auth_ctx)
            if status != 200:
                raise RuntimeError(response.get("error") or "concurrent_pull_failed")
            return bool((response.get("observation") or {}).get("ledger_recorded"))

        with ThreadPoolExecutor(max_workers=20) as pool:
            concurrent_pull_recorded = list(pool.map(concurrent_pull, range(20)))
        concurrent_pull_after = pull_counts(server, client.agent_id)
        require(sum(concurrent_pull_recorded) == 1,
                f"concurrent pull ledger decisions were not atomic: {concurrent_pull_recorded}", failures)
        require(concurrent_pull_after["runtime_events"] - concurrent_pull_before["runtime_events"] == 1,
                f"concurrent task.pull Runtime Event amplification: {concurrent_pull_after}", failures)
        require(concurrent_pull_after["audit_logs"] - concurrent_pull_before["audit_logs"] == 1,
                f"concurrent task.pull Audit amplification: {concurrent_pull_after}", failures)

        machine_agent_id = "agt_worker_host_machine_session"
        machine_headers = {
            "Authorization": "Bearer worker-heartbeat-machine-fixture",
            "X-AgentOps-Agent-Id": machine_agent_id,
            "X-AgentOps-Workspace-Id": "local-demo",
        }
        with server.db() as conn:
            machine_session, machine_session_status = server.agent_gateway_create_session(
                conn,
                machine_headers,
                {"scopes": worker_scopes, "ttl_sec": 900},
            )
            conn.commit()
        machine_client = AuthenticatedHeartbeatClient(
            server,
            machine_session.get("session_token") or "",
            agent_id=machine_agent_id,
        )
        first_machine_heartbeat = machine_client.post("/api/agent-gateway/heartbeat", {
            "workspace_id": "local-demo",
            "agent_id": machine_agent_id,
            "status": "idle",
            "summary": "Host machine Session heartbeat smoke.",
            "runtime_type": "openclaw",
        })
        stale_history_at = (datetime.now(timezone.utc) - timedelta(seconds=600)).isoformat()
        with server.db() as conn:
            machine_session_row = conn.execute(
                "SELECT parent_token_id FROM agent_gateway_sessions WHERE session_id=?",
                (machine_session.get("session_id"),),
            ).fetchone()
            machine_observation_row = conn.execute(
                """SELECT session_id FROM agent_gateway_session_heartbeat_observations
                WHERE workspace_id=? AND agent_id=? AND session_id=?""",
                ("local-demo", machine_agent_id, machine_session.get("session_id")),
            ).fetchone()
            conn.execute(
                "UPDATE runtime_events SET created_at=? WHERE event_type='agent.heartbeat' AND agent_id=?",
                (stale_history_at, machine_agent_id),
            )
            conn.commit()
            machine_fleet_from_observation = server.worker_remote_fleet_summary(conn, "local-demo")
            conn.execute(
                "UPDATE agent_gateway_session_heartbeat_observations SET updated_at=? WHERE session_id=?",
                (stale_history_at, machine_session.get("session_id")),
            )
            conn.commit()
            machine_stale_fleet = server.worker_remote_fleet_summary(conn, "local-demo")

        machine_worker_from_observation = next((
            item for item in (machine_fleet_from_observation.get("service_workers") or [])
            if item.get("agent_id") == machine_agent_id and item.get("workspace_id") == "local-demo"
        ), {})
        machine_worker_stale = next((
            item for item in (machine_stale_fleet.get("service_workers") or [])
            if item.get("agent_id") == machine_agent_id and item.get("workspace_id") == "local-demo"
        ), {})
        second_machine_heartbeat = machine_client.post("/api/agent-gateway/heartbeat", {
            "workspace_id": "local-demo",
            "agent_id": machine_agent_id,
            "status": "idle",
            "summary": "Host machine Session heartbeat smoke.",
            "runtime_type": "openclaw",
        })
        with server.db() as conn:
            machine_recovered_fleet = server.worker_remote_fleet_summary(conn, "local-demo")
        machine_worker_recovered = next((
            item for item in (machine_recovered_fleet.get("service_workers") or [])
            if item.get("agent_id") == machine_agent_id and item.get("workspace_id") == "local-demo"
        ), {})
        machine_heartbeat_evidence = heartbeat_counts(server, machine_agent_id)
        require(machine_session_status == 201 and machine_session.get("session_id"),
                "Host machine Session setup failed", failures)
        require(machine_session_row and machine_session_row["parent_token_id"] is None,
                "Host machine Session unexpectedly received an enrollment parent", failures)
        require(
            machine_observation_row
            and machine_observation_row["session_id"] == machine_session.get("session_id"),
            "Host machine heartbeat observation was not bound to its Session",
            failures,
        )
        require(first_machine_heartbeat.get("ledger_recorded") is True,
                "first Host machine heartbeat missed ledger evidence", failures)
        require(machine_worker_from_observation.get("heartbeat_state") == "fresh",
                f"fresh scoped observation did not override stale history: {machine_worker_from_observation}", failures)
        require(machine_worker_stale.get("heartbeat_state") == "stale",
                f"expired scoped observation did not become stale: {machine_worker_stale}", failures)
        require(second_machine_heartbeat.get("ledger_recorded") is False,
                "same-state Host machine heartbeat was not coalesced", failures)
        require(machine_worker_recovered.get("heartbeat_state") == "fresh",
                f"coalesced Host machine heartbeat did not restore Fleet freshness: {machine_worker_recovered}", failures)
        require(machine_heartbeat_evidence == {"runtime_events": 1, "audit_logs": 1},
                f"Host machine heartbeat amplified historical evidence: {machine_heartbeat_evidence}", failures)

        with server.db() as conn:
            heartbeat_only_session, heartbeat_only_session_status = server.agent_gateway_create_session(
                conn,
                machine_headers,
                {"scopes": ["agents:heartbeat"], "ttl_sec": 900},
            )
            conn.commit()
        heartbeat_only_client = AuthenticatedHeartbeatClient(
            server,
            heartbeat_only_session.get("session_token") or "",
            agent_id=machine_agent_id,
        )
        heartbeat_only_client.post("/api/agent-gateway/heartbeat", {
            "workspace_id": "local-demo",
            "agent_id": machine_agent_id,
            "status": "idle",
            "summary": "Heartbeat-only Session isolation smoke.",
            "runtime_type": "openclaw",
        })
        with server.db() as conn:
            heartbeat_only_fleet = server.worker_remote_fleet_summary(conn, "local-demo")
        heartbeat_only_worker = next((
            item for item in (heartbeat_only_fleet.get("service_workers") or [])
            if item.get("agent_id") == machine_agent_id
        ), {})
        require(heartbeat_only_session_status == 201,
                "heartbeat-only Session setup failed", failures)
        require(heartbeat_only_worker.get("heartbeat_state") == "fresh",
                f"heartbeat-only Session altered the execution Session observation: {heartbeat_only_worker}", failures)

        with server.db() as conn:
            conn.execute(
                "DELETE FROM agent_gateway_session_heartbeat_observations WHERE session_id=?",
                (machine_session.get("session_id"),),
            )
            conn.commit()
            legacy_observation_fleet = server.worker_remote_fleet_summary(conn, "local-demo")
        legacy_observation_worker = next((
            item for item in (legacy_observation_fleet.get("service_workers") or [])
            if item.get("agent_id") == machine_agent_id
        ), {})
        require(
            legacy_observation_worker.get("heartbeat_state") == "never_seen",
            f"legacy per-Agent observation was reused as Session liveness: {legacy_observation_worker}",
            failures,
        )

        machine_client.post("/api/agent-gateway/heartbeat", {
            "workspace_id": "local-demo",
            "agent_id": machine_agent_id,
            "status": "idle",
            "summary": "Host machine Session heartbeat smoke.",
            "runtime_type": "openclaw",
        })
        with server.db() as conn:
            execution_session_restored_fleet = server.worker_remote_fleet_summary(conn, "local-demo")
        execution_session_restored = next((
            item for item in (execution_session_restored_fleet.get("service_workers") or [])
            if item.get("agent_id") == machine_agent_id
        ), {})
        require(execution_session_restored.get("heartbeat_state") == "fresh",
                f"execution Session heartbeat did not restore its own Fleet state: {execution_session_restored}", failures)

        output = {
            "ok": not failures,
            "operation": "worker_intake_heartbeat_fleet_smoke",
            "blocked_iterations": len(results) + 1,
            "heartbeat_requests": len(client.heartbeat_responses),
            "heartbeat_runtime_events_added": after_due_client["runtime_events"] - before["runtime_events"],
            "heartbeat_audit_rows_added": after_due_client["audit_logs"] - before["audit_logs"],
            "pull_runtime_events_added": pulls_after["runtime_events"] - pulls_before["runtime_events"],
            "pull_audit_rows_added": pulls_after["audit_logs"] - pulls_before["audit_logs"],
            "aged_pull_ledger_recorded": (aged_pull.get("observation") or {}).get("ledger_recorded"),
            "restricted_session_heartbeat_rejected": restricted_rejected,
            "read_only_session_execution_capacity_excluded": not bool(observer_service_worker),
            "fleet_heartbeat_state": remote_worker.get("heartbeat_state"),
            "service_worker_heartbeat_state": service_worker.get("heartbeat_state"),
            "active_session_count": remote_worker.get("active_session_count"),
            "deduplicated_execution_capacity_workers": deduplicated_status.get("execution_capacity_workers"),
            "host_machine_session_parent_omitted": bool(machine_session_row and machine_session_row["parent_token_id"] is None),
            "host_machine_observation_state": machine_worker_from_observation.get("heartbeat_state"),
            "host_machine_expired_observation_state": machine_worker_stale.get("heartbeat_state"),
            "host_machine_recovered_state": machine_worker_recovered.get("heartbeat_state"),
            "host_machine_second_heartbeat_ledger_recorded": second_machine_heartbeat.get("ledger_recorded"),
            "heartbeat_only_session_execution_state": heartbeat_only_worker.get("heartbeat_state"),
            "legacy_observation_execution_state": legacy_observation_worker.get("heartbeat_state"),
            "legacy_schema_session_rows": migrated_session_row_count,
            "execution_session_restored_state": execution_session_restored.get("heartbeat_state"),
            "cross_workspace_states": {
                workspace_id: item.get("heartbeat_state")
                for workspace_id, item in scoped_service_workers.items()
            },
            "cross_workspace_heartbeat_ledgers": len(heartbeat_observation_workspaces),
            "concurrent_heartbeat_requests": len(concurrent_recorded),
            "concurrent_ledger_records": sum(concurrent_recorded),
            "concurrent_runtime_events": concurrent_counts["runtime_events"],
            "concurrent_audit_rows": concurrent_counts["audit_logs"],
            "concurrent_pull_requests": len(concurrent_pull_recorded),
            "concurrent_pull_ledger_records": sum(concurrent_pull_recorded),
            "concurrent_pull_runtime_events_added": concurrent_pull_after["runtime_events"] - concurrent_pull_before["runtime_events"],
            "concurrent_pull_audit_rows_added": concurrent_pull_after["audit_logs"] - concurrent_pull_before["audit_logs"],
            "isolated_database": True,
            "live_execution_performed": False,
            "raw_prompt_omitted": True,
            "raw_response_omitted": True,
            "token_omitted": True,
            "failures": failures,
        }
        print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
