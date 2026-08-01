#!/usr/bin/env python3
"""Verify session-plus-heartbeat service Workers appear as execution capacity."""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def main() -> int:
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="agentops-service-worker-presence-") as tmp:
        root = Path(tmp)
        os.environ["AGENTOPS_DB_PATH"] = str(root / "agentops_mis.db")
        os.environ["AGENTOPS_WORKER_RUNTIME_DIR"] = str(root / "worker-runtime")
        os.environ["AGENTOPS_LIVE_RUNTIME_DISABLED"] = "true"

        import server
        from agentops_mis_core.worker_fleet import SERVICE_WORKER_EXECUTION_SCOPES

        server.init_schema()
        now = dt.datetime.now(dt.timezone.utc)
        expires_at = (now + dt.timedelta(minutes=15)).isoformat()
        agent_id = "agt_worker_service_presence_smoke"
        with server.db() as conn:
            conn.execute(
                """INSERT INTO agents(
                    agent_id,name,role,description,runtime_type,model_provider,
                    model_name,status,permission_level,allowed_tools,
                    budget_limit_usd,owner_user_id,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    agent_id,
                    "Service Presence Smoke Worker",
                    "worker",
                    "Isolated service presence fixture.",
                    "hermes",
                    "local",
                    "fixture",
                    "idle",
                    "standard",
                    json.dumps(["agent_worker"]),
                    0,
                    None,
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            conn.execute(
                """INSERT INTO agent_gateway_sessions(
                    session_id,session_hash,parent_token_id,workspace_id,agent_id,
                    scopes_json,status,created_at,expires_at,revoked_at,last_used_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    "sess_service_presence_smoke",
                    server.token_hash("service-presence-fixture"),
                    None,
                    "local-demo",
                    agent_id,
                    json.dumps(sorted(SERVICE_WORKER_EXECUTION_SCOPES)),
                    "active",
                    now.isoformat(),
                    expires_at,
                    None,
                    now.isoformat(),
                ),
            )
            heartbeat, heartbeat_status = server.agent_gateway_heartbeat(conn, {
                "workspace_id": "local-demo",
                "agent_id": agent_id,
                "_auth_session_id": "sess_service_presence_smoke",
                "status": "idle",
                "summary": "Service Worker presence smoke heartbeat.",
                "runtime_type": "hermes",
            })
            conn.commit()
            status = server.worker_status(conn, refresh_runtime=False)
            fleet = server.worker_fleet_view(conn)

        service_lanes = [
            lane for lane in (fleet.get("lanes") or [])
            if lane.get("lane_type") == "gateway_service_worker"
        ]
        require(heartbeat_status == 200 and heartbeat.get("agent_id") == agent_id, "heartbeat was not recorded", failures)
        require(status.get("running_workers") == 0, "service Worker must not claim a Host-observed process", failures)
        require(status.get("active_service_workers") == 1, "status missed active service Worker", failures)
        require(status.get("execution_capacity_workers") == 1, "status missed service execution capacity", failures)
        require(status.get("status") == "running", "status did not report available execution capacity", failures)
        require((fleet.get("summary") or {}).get("active_service_workers") == 1, "fleet missed active service Worker", failures)
        require((fleet.get("summary") or {}).get("execution_capacity_workers") == 1, "fleet missed service execution capacity", failures)
        require(len(service_lanes) == 1, "fleet did not expose exactly one service Worker lane", failures)
        if service_lanes:
            lane = service_lanes[0]
            require(lane.get("heartbeat_state") == "fresh", "service Worker heartbeat is not fresh", failures)
            require(lane.get("session_state") == "active", "service Worker session is not active", failures)
            require(lane.get("management_mode") == "external_service", "service Worker management mode is wrong", failures)
            require(lane.get("process_state_verified") is False, "service Worker must not claim process verification", failures)
            require(lane.get("token_omitted") is True and lane.get("session_id_omitted") is True, "service Worker omission proof is missing", failures)

        payload = {
            "ok": not failures,
            "operation": "private_host_service_worker_presence_smoke",
            "failures": failures,
            "status": status.get("status"),
            "running_workers": status.get("running_workers"),
            "active_service_workers": status.get("active_service_workers"),
            "execution_capacity_workers": status.get("execution_capacity_workers"),
            "service_lane_count": len(service_lanes),
            "heartbeat_state": service_lanes[0].get("heartbeat_state") if service_lanes else None,
            "process_state_verified": service_lanes[0].get("process_state_verified") if service_lanes else None,
            "ledger_mutated": True,
            "isolated_database": True,
            "live_execution_performed": False,
            "raw_prompt_omitted": True,
            "raw_response_omitted": True,
            "token_omitted": True,
            "session_id_omitted": True,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
