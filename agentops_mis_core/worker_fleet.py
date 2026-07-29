"""Pure worker fleet read-model aggregation helpers."""
from __future__ import annotations

import datetime as dt
import hashlib
import re
from typing import Any


SERVICE_WORKER_EXECUTION_SCOPES = frozenset({
    "agents:write",
    "agents:heartbeat",
    "agent_plans:read",
    "agent_plans:write",
    "plan_evidence:read",
    "plan_evidence:write",
    "knowledge:read",
    "knowledge:write",
    "tasks:read",
    "tasks:claim",
    "runs:write",
    "runtime_events:write",
    "toolcalls:write",
    "artifacts:write",
    "memories:propose",
    "evaluations:submit",
    "audit:write",
})
SERVICE_WORKER_READY_STATUSES = frozenset({"idle", "running"})


def service_worker_session_ready(session: dict[str, Any]) -> bool:
    return SERVICE_WORKER_EXECUTION_SCOPES.issubset(set(session.get("scopes") or []))


def stable_id(prefix: str, *parts: Any) -> str:
    raw = "::".join(str(part) for part in parts if part is not None and str(part) != "")
    slug = re.sub(r"[^a-zA-Z0-9_]+", "_", raw).strip("_").lower()
    if slug and len(slug) <= 64:
        return f"{prefix}_{slug}"
    return f"{prefix}_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]}"


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _heartbeat_state(
    timestamp: Any,
    *,
    now_dt: dt.datetime,
    timeout_sec: int,
) -> str:
    if not timestamp:
        return "never_seen"
    try:
        seen = dt.datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
        if seen.tzinfo is None:
            seen = seen.replace(tzinfo=dt.timezone.utc)
        seen = seen.astimezone(dt.timezone.utc)
        normalized_now = now_dt
        if normalized_now.tzinfo is None:
            normalized_now = normalized_now.replace(tzinfo=dt.timezone.utc)
        normalized_now = normalized_now.astimezone(dt.timezone.utc)
        return "stale" if (normalized_now - seen).total_seconds() > max(timeout_sec, 1) else "fresh"
    except (TypeError, ValueError):
        return "unknown"


def _iso_sort_key(timestamp: Any) -> tuple[int, dt.datetime, str]:
    raw = str(timestamp or "")
    try:
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        parsed = parsed.astimezone(dt.timezone.utc)
        return (1, parsed, raw)
    except (TypeError, ValueError):
        return (0, dt.datetime.min.replace(tzinfo=dt.timezone.utc), raw)


def _service_session_record(
    session: dict[str, Any],
    *,
    heartbeats_by_session: dict[str, dict[str, Any]],
    now_dt: dt.datetime,
    timeout_sec: int,
) -> dict[str, Any]:
    session_id = str(session.get("session_id") or "")
    heartbeat = heartbeats_by_session.get(session_id) or {}
    last_heartbeat_at = heartbeat.get("last_heartbeat_at") or heartbeat.get("updated_at")
    heartbeat_state = _heartbeat_state(
        last_heartbeat_at,
        now_dt=now_dt,
        timeout_sec=timeout_sec,
    )
    reported_status = str(heartbeat.get("status") or "unknown").strip().lower() or "unknown"
    if heartbeat_state == "fresh" and reported_status in SERVICE_WORKER_READY_STATUSES:
        selection_class = "fresh_ready"
        selection_rank = 0
    elif heartbeat_state == "fresh":
        selection_class = "fresh_nonready"
        selection_rank = 1
    elif last_heartbeat_at:
        selection_class = "stale_observed"
        selection_rank = 2
    else:
        selection_class = "never_seen"
        selection_rank = 3
    return {
        "session": session,
        "last_heartbeat_at": last_heartbeat_at,
        "heartbeat_state": heartbeat_state,
        "reported_status": reported_status,
        "selection_class": selection_class,
        "selection_rank": selection_rank,
        "sort_key": (
            _iso_sort_key(last_heartbeat_at),
            _iso_sort_key(session.get("last_used_at") or session.get("created_at")),
            session_id,
        ),
    }


def _select_service_session(records: list[dict[str, Any]]) -> dict[str, Any]:
    selection_rank = min(_int(record.get("selection_rank")) for record in records)
    return max(
        (record for record in records if _int(record.get("selection_rank")) == selection_rank),
        key=lambda record: record["sort_key"],
    )


def worker_fleet_health(payload: dict[str, Any]) -> dict[str, Any]:
    remote = payload.get("remote_worker_health") or {}
    daemons = payload.get("daemons") or []
    active_daemons = [daemon for daemon in daemons if daemon.get("running")]
    running_workers = _int(payload.get("execution_capacity_workers", payload.get("running_workers")))
    pending_tasks = _int(payload.get("pending_worker_tasks"))
    stuck_tasks = _int(payload.get("stuck_worker_tasks"))
    workflow_stuck_jobs = _int(payload.get("stuck_workflow_jobs"))
    active_remote = _int(payload.get("active_remote_enrollments"))
    fresh_remote = _int(payload.get("fresh_remote_enrollments"))
    stale_remote = _int(payload.get("stale_remote_enrollments"))
    never_seen_remote = _int(payload.get("never_seen_remote_enrollments"))
    active_sessions = _int(payload.get("active_remote_sessions"))
    ready_service_workers = _int(remote.get("ready_service_workers"))
    unavailable_service_workers = _int(remote.get("unavailable_service_workers"))
    degraded_service_workers = _int(remote.get("degraded_service_workers"))

    gates: list[dict[str, Any]] = []

    def add_gate(gate_id: str, status: str, summary: str, action: str = "") -> None:
        gates.append({
            "id": gate_id,
            "status": status,
            "summary": summary,
            "action": action,
        })

    if stuck_tasks:
        add_gate(
            "worker_task_recovery",
            "fail",
            f"{stuck_tasks} running worker task(s) exceeded the recovery threshold.",
            "Review agentops worker stuck, then release the selected task.",
        )
    else:
        add_gate("worker_task_recovery", "pass", "No stale running worker tasks detected.", "agentops worker stuck")

    if workflow_stuck_jobs:
        add_gate(
            "workflow_job_recovery",
            "fail",
            f"{workflow_stuck_jobs} async workflow job(s) appear stuck.",
            "agentops workflow stuck-jobs",
        )
    else:
        add_gate("workflow_job_recovery", "pass", "No stuck async workflow jobs detected.", "agentops workflow stuck-jobs")

    if running_workers:
        add_gate(
            "execution_capacity",
            "pass",
            f"{running_workers} worker execution path(s) are currently available.",
            "agentops worker status",
        )
    elif pending_tasks:
        add_gate(
            "execution_capacity",
            "warn",
            f"{pending_tasks} worker task(s) are waiting but no active worker is visible.",
            "agentops worker start --adapter mock",
        )
    else:
        add_gate(
            "execution_capacity",
            "warn",
            "No active worker daemon or running worker agent is visible.",
            "agentops worker preflight --adapter mock",
        )

    if active_remote and stale_remote:
        add_gate(
            "remote_heartbeats",
            "warn",
            f"{stale_remote} remote enrollment(s) have stale heartbeats.",
            "agentops enrollment list && agentops doctor",
        )
    elif active_remote and fresh_remote:
        add_gate(
            "remote_heartbeats",
            "pass",
            f"{fresh_remote} remote enrollment(s) have fresh heartbeats.",
            "agentops agent heartbeat",
        )
    elif active_remote and never_seen_remote:
        add_gate(
            "remote_heartbeats",
            "warn",
            f"{never_seen_remote} active enrollment(s) have not heartbeated yet.",
            "agentops agent heartbeat",
        )
    else:
        add_gate(
            "remote_heartbeats",
            "info",
            "No remote agent enrollments are active; local-only operation is allowed.",
            "agentops enrollment create --agent-id <agent_id>",
        )

    if active_remote and active_sessions:
        add_gate(
            "session_hygiene",
            "pass",
            f"{active_sessions} short-lived remote session(s) are active.",
            "agentops session list",
        )
    elif active_remote:
        add_gate(
            "session_hygiene",
            "warn",
            "Remote enrollments exist but no short-lived worker session is active.",
            "agentops session create",
        )
    else:
        add_gate("session_hygiene", "info", "No remote sessions are required for local-only mode.", "agentops session list")

    if degraded_service_workers:
        add_gate(
            "service_session_health",
            "warn",
            f"{degraded_service_workers} service worker(s) retain capacity but have mixed Session health.",
            "agentops worker status",
        )
    elif unavailable_service_workers:
        add_gate(
            "service_session_health",
            "warn",
            f"{unavailable_service_workers} service worker(s) have fresh but non-ready Sessions.",
            "agentops worker status",
        )
    elif ready_service_workers:
        add_gate(
            "service_session_health",
            "pass",
            f"{ready_service_workers} service worker(s) have fresh execution-ready Sessions.",
            "agentops worker status",
        )
    else:
        add_gate(
            "service_session_health",
            "info",
            "No execution-ready Agent Gateway service Session is visible.",
            "agentops worker status",
        )

    if active_daemons:
        daemon_summaries = [
            f"{daemon.get('adapter')} pid={daemon.get('pid')} ({daemon.get('management_mode') or 'daemon_api'})"
            for daemon in active_daemons
            if daemon.get("adapter")
        ]
        host_managed = [daemon for daemon in active_daemons if daemon.get("management_mode") == "host_stack"]
        add_gate(
            "local_daemons",
            "pass",
            "Local worker process(es) running: " + ", ".join(daemon_summaries[:3]),
            "agentops host status" if host_managed else "agentops worker logs --adapter mock",
        )
    else:
        add_gate(
            "local_daemons",
            "info",
            "No repo-local daemon is running; one-shot or remote workers can still execute tasks.",
            "agentops worker start --adapter mock",
        )

    statuses = {gate["status"] for gate in gates}
    overall = "blocked" if "fail" in statuses else "attention" if "warn" in statuses else "ready"
    actions = []
    for gate in gates:
        action = gate.get("action")
        if action and action not in actions and gate.get("status") in {"fail", "warn"}:
            actions.append(action)
    if not actions:
        actions = ["agentops worker status", "agentops workflow run-task --help"]

    return {
        "overall": overall,
        "contract": "agents execute through Agent Gateway CLI/API; browser UI is an operator console only",
        "gates": gates,
        "recommended_actions": actions[:6],
        "remote_status": remote.get("status"),
        "token_omitted": True,
    }


def public_worker_stale_enrollment(enrollment: dict[str, Any]) -> dict[str, Any]:
    token_id = enrollment.get("token_id") or ""
    public = dict(enrollment)
    public.pop("token_id", None)
    public["token_ref"] = stable_id("token_ref", token_id)[-12:] if token_id else ""
    public["token_id_omitted"] = True
    return public


def build_worker_fleet_hygiene_plan(
    *,
    stuck_tasks: list[dict[str, Any]],
    stale_enrollments: list[dict[str, Any]],
    stale_heartbeat_enrollments: list[dict[str, Any]] | None = None,
    threshold_sec: int,
    enrollment_age_sec: int,
    apply: bool = False,
) -> dict[str, Any]:
    stale_heartbeat_enrollments = stale_heartbeat_enrollments or []
    actions_available = len(stuck_tasks) + len(stale_enrollments) + len(stale_heartbeat_enrollments)
    return {
        "provider": "agentops-worker",
        "operation": "fleet_hygiene",
        "status": "actionable" if actions_available else "ready",
        "threshold_sec": threshold_sec,
        "enrollment_age_sec": enrollment_age_sec,
        "summary": {
            "stuck_tasks": len(stuck_tasks),
            "stale_never_seen_enrollments": len(stale_enrollments),
            "stale_heartbeat_enrollments": len(stale_heartbeat_enrollments),
            "actions_available": actions_available,
        },
        "stuck_tasks": stuck_tasks,
        "stale_never_seen_enrollments": [public_worker_stale_enrollment(enrollment) for enrollment in stale_enrollments],
        "stale_heartbeat_enrollments": [
            public_worker_stale_enrollment(enrollment) for enrollment in stale_heartbeat_enrollments
        ],
        "recommended_actions": [
            "agentops worker hygiene --apply --confirm-cleanup",
        ] if actions_available else ["agentops worker status"],
        "safety": {
            "read_only": not apply,
            "requires_confirm_cleanup": True,
            "live_execution_performed": False,
            "token_omitted": True,
        },
        "token_omitted": True,
        "live_execution_performed": False,
    }


def public_worker_revoked_enrollment(enrollment: dict[str, Any], sessions_revoked: int = 0) -> dict[str, Any]:
    return {
        "token_ref": stable_id("token_ref", enrollment.get("token_id") or "")[-12:],
        "token_id_omitted": True,
        "agent_id": enrollment.get("agent_id"),
        "sessions_revoked": sessions_revoked,
    }


def public_worker_enrollment_error(enrollment: dict[str, Any], *, status: int, error: Any) -> dict[str, Any]:
    return {
        "kind": "enrollment_revoke",
        "token_ref": stable_id("token_ref", enrollment.get("token_id") or "")[-12:],
        "token_id_omitted": True,
        "status": status,
        "error": error,
    }


def public_remote_worker(
    enrollment: dict[str, Any],
    *,
    agent: dict[str, Any] | None = None,
    active_session_count: int = 0,
) -> dict[str, Any]:
    agent = agent or {}
    return {
        "token_ref": stable_id("token_ref", enrollment.get("token_id") or "")[-12:] if enrollment.get("token_id") else "",
        "token_id_omitted": True,
        "workspace_id": enrollment.get("workspace_id"),
        "agent_id": enrollment.get("agent_id"),
        "agent_name": agent.get("name") or enrollment.get("label") or enrollment.get("agent_id"),
        "runtime_type": agent.get("runtime_type") or "external",
        "agent_status": agent.get("status"),
        "token_status": enrollment.get("status") or "unknown",
        "heartbeat_state": enrollment.get("heartbeat_state") or "unknown",
        "heartbeat_timeout_sec": enrollment.get("heartbeat_timeout_sec"),
        "last_heartbeat_at": enrollment.get("last_heartbeat_at"),
        "last_used_at": enrollment.get("last_used_at"),
        "expires_at": enrollment.get("expires_at"),
        "scope_count": len(enrollment.get("scopes") or []),
        "active_session_count": active_session_count,
    }


def public_remote_session(session: dict[str, Any]) -> dict[str, Any]:
    return {
        "session_ref": stable_id("session_ref", session.get("session_id") or "")[-12:] if session.get("session_id") else "",
        "session_id_omitted": True,
        "parent_token_ref": stable_id("token_ref", session.get("parent_token_id") or "")[-12:] if session.get("parent_token_id") else "",
        "workspace_id": session.get("workspace_id"),
        "agent_id": session.get("agent_id"),
        "status": session.get("status"),
        "session_state": session.get("session_state"),
        "created_at": session.get("created_at"),
        "expires_at": session.get("expires_at"),
        "last_used_at": session.get("last_used_at"),
        "scope_count": len(session.get("scopes") or []),
    }


def build_worker_remote_fleet_summary(
    *,
    enrollments: list[dict[str, Any]],
    sessions: list[dict[str, Any]],
    agents_by_id: dict[str, dict[str, Any]],
    heartbeats_by_session: dict[str, dict[str, Any]] | None = None,
    heartbeats_by_agent: dict[str, dict[str, Any]] | None = None,
    heartbeats_by_worker: dict[tuple[str, str], dict[str, Any]] | None = None,
    now_dt: dt.datetime | None = None,
    service_heartbeat_timeout_sec: int = 90,
) -> dict[str, Any]:
    active_sessions_by_worker: dict[tuple[str, str], int] = {}
    execution_sessions_by_worker: dict[tuple[str, str], list[dict[str, Any]]] = {}
    session_state_counts: dict[str, int] = {}
    for session in sessions:
        state = session.get("session_state") or session.get("status") or "unknown"
        session_state_counts[state] = session_state_counts.get(state, 0) + 1
        if state == "active" and session.get("agent_id"):
            worker_key = (str(session.get("workspace_id") or "local-demo"), str(session["agent_id"]))
            active_sessions_by_worker[worker_key] = active_sessions_by_worker.get(worker_key, 0) + 1
            if service_worker_session_ready(session):
                execution_sessions_by_worker.setdefault(worker_key, []).append(session)

    heartbeat_counts: dict[str, int] = {}
    token_status_counts: dict[str, int] = {}
    remote_workers = []
    for enrollment in enrollments:
        heartbeat_state = enrollment.get("heartbeat_state") or "unknown"
        token_status = enrollment.get("status") or "unknown"
        heartbeat_counts[heartbeat_state] = heartbeat_counts.get(heartbeat_state, 0) + 1
        token_status_counts[token_status] = token_status_counts.get(token_status, 0) + 1
        agent = agents_by_id.get(enrollment.get("agent_id") or "") or {}
        remote_workers.append(public_remote_worker(
            enrollment,
            agent=agent,
            active_session_count=active_sessions_by_worker.get((
                str(enrollment.get("workspace_id") or "local-demo"),
                str(enrollment.get("agent_id") or ""),
            ), 0),
        ))

    active_enrollments = [item for item in remote_workers if item.get("token_status") == "active"]
    stale_enrollments = [item for item in remote_workers if item.get("heartbeat_state") == "stale"]
    never_seen_enrollments = [item for item in remote_workers if item.get("heartbeat_state") == "never_seen"]
    fresh_enrollments = [item for item in remote_workers if item.get("heartbeat_state") == "fresh"]
    heartbeats_by_session = heartbeats_by_session or {}
    now_dt = now_dt or dt.datetime.now(dt.timezone.utc)
    service_workers = []
    for (workspace_id, agent_id), execution_sessions in execution_sessions_by_worker.items():
        agent = agents_by_id.get(agent_id) or {}
        session_records = [
            _service_session_record(
                session,
                heartbeats_by_session=heartbeats_by_session,
                now_dt=now_dt,
                timeout_sec=service_heartbeat_timeout_sec,
            )
            for session in execution_sessions
        ]
        session_records = [
            record for record in session_records
            if record.get("reported_status") != "disabled"
        ]
        if not session_records:
            continue
        selected = _select_service_session(session_records)
        selected_session = selected["session"]
        last_heartbeat_at = selected["last_heartbeat_at"]
        heartbeat_state = selected["heartbeat_state"]
        reported_status = selected["reported_status"]
        heartbeat_state_counts: dict[str, int] = {}
        reported_status_counts: dict[str, int] = {}
        fresh_reported_status_counts: dict[str, int] = {}
        for record in session_records:
            state = str(record["heartbeat_state"])
            status = str(record["reported_status"])
            heartbeat_state_counts[state] = heartbeat_state_counts.get(state, 0) + 1
            reported_status_counts[status] = reported_status_counts.get(status, 0) + 1
            if state == "fresh":
                fresh_reported_status_counts[status] = fresh_reported_status_counts.get(status, 0) + 1
        fresh_ready_session_count = sum(
            count
            for status, count in fresh_reported_status_counts.items()
            if status in SERVICE_WORKER_READY_STATUSES
        )
        fresh_nonready_session_count = sum(fresh_reported_status_counts.values()) - fresh_ready_session_count
        has_degraded_sessions = fresh_ready_session_count > 0 and fresh_nonready_session_count > 0
        service_workers.append({
            "agent_id": agent_id,
            "agent_name": agent.get("name") or agent_id,
            "workspace_id": workspace_id,
            "runtime_type": agent.get("runtime_type") or "external",
            "agent_status": agent.get("status") or "unknown",
            "reported_status": reported_status,
            "heartbeat_state": heartbeat_state,
            "heartbeat_timeout_sec": service_heartbeat_timeout_sec,
            "last_heartbeat_at": last_heartbeat_at,
            "active_session_count": len(execution_sessions),
            "eligible_session_count": len(execution_sessions),
            "fresh_session_count": heartbeat_state_counts.get("fresh", 0),
            "fresh_ready_session_count": fresh_ready_session_count,
            "fresh_nonready_session_count": fresh_nonready_session_count,
            "degraded_session_count": fresh_nonready_session_count,
            "stale_observed_session_count": sum(
                1 for record in session_records if record.get("selection_class") == "stale_observed"
            ),
            "never_seen_session_count": sum(
                1 for record in session_records if record.get("selection_class") == "never_seen"
            ),
            "has_degraded_sessions": has_degraded_sessions,
            "heartbeat_state_counts": heartbeat_state_counts,
            "reported_status_counts": reported_status_counts,
            "fresh_reported_status_counts": fresh_reported_status_counts,
            "selected_session_class": selected["selection_class"],
            "selected_session_activity_at": (
                selected_session.get("last_used_at") or selected_session.get("created_at")
            ),
            "session_state": "active",
            "execution_scope_ready": True,
            "management_mode": "external_service",
            "process_state_verified": False,
            "token_omitted": True,
            "session_id_omitted": True,
        })

    fresh_service_workers = [item for item in service_workers if item.get("heartbeat_state") == "fresh"]
    ready_service_workers = [
        item for item in fresh_service_workers
        if item.get("reported_status") in SERVICE_WORKER_READY_STATUSES
    ]
    unavailable_service_workers = [
        item for item in fresh_service_workers
        if item.get("reported_status") not in SERVICE_WORKER_READY_STATUSES
    ]
    degraded_service_workers = [
        item for item in service_workers
        if item.get("has_degraded_sessions")
    ]
    stale_service_workers = [item for item in service_workers if item.get("heartbeat_state") == "stale"]
    never_seen_service_workers = [item for item in service_workers if item.get("heartbeat_state") == "never_seen"]
    service_session_status_counts: dict[str, int] = {}
    fresh_service_session_status_counts: dict[str, int] = {}
    for worker in service_workers:
        for status, count in (worker.get("reported_status_counts") or {}).items():
            service_session_status_counts[status] = service_session_status_counts.get(status, 0) + _int(count)
        for status, count in (worker.get("fresh_reported_status_counts") or {}).items():
            fresh_service_session_status_counts[status] = (
                fresh_service_session_status_counts.get(status, 0) + _int(count)
            )
    health_status = (
        "attention"
        if stale_enrollments or stale_service_workers or unavailable_service_workers or degraded_service_workers
        else "ready"
    )
    if active_enrollments and not fresh_enrollments and len(never_seen_enrollments) == len(active_enrollments):
        health_status = "waiting_for_heartbeat"
    if service_workers and not fresh_service_workers and len(never_seen_service_workers) == len(service_workers):
        health_status = "waiting_for_heartbeat"

    return {
        "status": health_status,
        "remote_worker_count": len(active_enrollments),
        "total_remote_enrollments": len(remote_workers),
        "active_enrollments": len(active_enrollments),
        "fresh_enrollments": len(fresh_enrollments),
        "stale_enrollments": len(stale_enrollments),
        "never_seen_enrollments": len(never_seen_enrollments),
        "active_sessions": session_state_counts.get("active", 0),
        "service_worker_count": len(service_workers),
        "fresh_service_workers": len(fresh_service_workers),
        "ready_service_workers": len(ready_service_workers),
        "unavailable_service_workers": len(unavailable_service_workers),
        "degraded_service_workers": len(degraded_service_workers),
        "degraded_service_sessions": sum(
            _int(worker.get("degraded_session_count")) for worker in service_workers
        ),
        "stale_service_workers": len(stale_service_workers),
        "never_seen_service_workers": len(never_seen_service_workers),
        "expired_sessions": session_state_counts.get("expired", 0),
        "revoked_sessions": session_state_counts.get("revoked", 0),
        "heartbeat_state_counts": heartbeat_counts,
        "token_status_counts": token_status_counts,
        "session_state_counts": session_state_counts,
        "service_session_status_counts": service_session_status_counts,
        "fresh_service_session_status_counts": fresh_service_session_status_counts,
        "remote_workers": remote_workers[:50],
        "service_workers": service_workers[:50],
        "recent_sessions": [public_remote_session(session) for session in sessions[:25]],
        "token_omitted": True,
    }


def build_worker_status_payload(
    *,
    worker_agents: list[dict[str, Any]],
    worker_runs: list[dict[str, Any]],
    worker_tasks: list[dict[str, Any]],
    worker_events: list[dict[str, Any]],
    daemons: list[dict[str, Any]],
    stuck_tasks: list[dict[str, Any]],
    remote_fleet: dict[str, Any],
    stuck_workflow_jobs: list[dict[str, Any]],
    adapter_readiness: dict[str, Any],
) -> dict[str, Any]:
    active_daemons = [daemon for daemon in daemons if daemon.get("running")]
    unverified_process_claims = [
        daemon
        for daemon in daemons
        if daemon.get("process_claim_active") and daemon.get("process_identity_verified") is not True
    ]
    running_worker_refs = {
        ("local-demo", str(daemon.get("agent_id") or f"local-daemon:{daemon.get('adapter') or 'unknown'}"))
        for daemon in active_daemons
    }
    fresh_service_worker_refs = {
        (str(worker.get("workspace_id") or "local-demo"), str(worker.get("agent_id")))
        for worker in (remote_fleet.get("service_workers") or [])
        if worker.get("agent_id")
        and worker.get("heartbeat_state") == "fresh"
        and worker.get("reported_status") in SERVICE_WORKER_READY_STATUSES
    }
    execution_capacity_refs = set(running_worker_refs)
    execution_capacity_refs.update(fresh_service_worker_refs)
    payload = {
        "provider": "agentops-worker",
        "status": "attention" if remote_fleet.get("stale_enrollments") or remote_fleet.get("stale_service_workers") or remote_fleet.get("unavailable_service_workers") or remote_fleet.get("degraded_service_workers") or unverified_process_claims else "running" if execution_capacity_refs else "ready",
        "worker_count": len(worker_agents),
        "running_workers": len(running_worker_refs),
        "active_service_workers": len(fresh_service_worker_refs),
        "degraded_service_workers": remote_fleet.get("degraded_service_workers", 0),
        "unavailable_service_workers": remote_fleet.get("unavailable_service_workers", 0),
        "stale_service_workers": remote_fleet.get("stale_service_workers", 0),
        "execution_capacity_workers": len(execution_capacity_refs),
        "unverified_process_claims": len(unverified_process_claims),
        "recent_completed_runs": len([run for run in worker_runs if run.get("status") == "completed"]),
        "pending_worker_tasks": len([task for task in worker_tasks if task.get("status") in ("planned", "backlog")]),
        "stuck_worker_tasks": len(stuck_tasks),
        "stuck_workflow_jobs": len(stuck_workflow_jobs),
        "remote_worker_count": remote_fleet.get("remote_worker_count", 0),
        "total_remote_enrollments": remote_fleet.get("total_remote_enrollments", 0),
        "active_remote_enrollments": remote_fleet.get("active_enrollments", 0),
        "fresh_remote_enrollments": remote_fleet.get("fresh_enrollments", 0),
        "stale_remote_enrollments": remote_fleet.get("stale_enrollments", 0),
        "never_seen_remote_enrollments": remote_fleet.get("never_seen_enrollments", 0),
        "active_remote_sessions": remote_fleet.get("active_sessions", 0),
        "remote_worker_health": remote_fleet,
        "service_workers": remote_fleet.get("service_workers") or [],
        "adapter_readiness": adapter_readiness.get("summary"),
        "daemons": daemons,
        "workers": worker_agents,
        "recent_runs": worker_runs,
        "recent_tasks": worker_tasks,
        "stuck_tasks": stuck_tasks,
        "stuck_workflow_job_refs": [{
            "job_id": job.get("job_id"),
            "workflow_type": job.get("workflow_type"),
            "status": job.get("status"),
            "age_sec": job.get("age_sec"),
            "stuck_reason": job.get("stuck_reason"),
        } for job in stuck_workflow_jobs],
        "recent_events": worker_events,
    }
    payload["fleet_health"] = worker_fleet_health(payload)
    return payload


def build_worker_fleet_view(
    *,
    daemons: list[dict[str, Any]],
    remote_fleet: dict[str, Any],
    adapter_readiness: dict[str, Any],
    stuck_tasks: list[dict[str, Any]],
    stuck_workflow_jobs: list[dict[str, Any]],
    worker_agents: list[dict[str, Any]],
) -> dict[str, Any]:
    lanes: list[dict[str, Any]] = []
    seen_workers: set[tuple[str, str]] = set()
    service_workers_by_key = {
        (
            str(worker.get("workspace_id") or "local-demo"),
            str(worker.get("agent_id") or ""),
        ): worker
        for worker in (remote_fleet.get("service_workers") or [])
        if worker.get("agent_id")
    }

    def add_lane(lane: dict[str, Any]) -> None:
        lane["token_omitted"] = True
        lane["session_id_omitted"] = True
        lanes.append(lane)
        if lane.get("agent_id"):
            seen_workers.add((
                str(lane.get("workspace_id") or "local-demo"),
                str(lane["agent_id"]),
            ))

    for daemon in daemons:
        running = bool(daemon.get("running"))
        identity_unverified = bool(daemon.get("process_claim_active") and daemon.get("process_identity_verified") is not True)
        status = daemon.get("status") if identity_unverified else daemon.get("worker_status") or daemon.get("status") or "unknown"
        management_mode = daemon.get("management_mode") or "daemon_api"
        control_allowed = daemon.get("control_allowed") is not False
        health = "warn" if identity_unverified else "pass" if running else "info"
        if running and _int(daemon.get("consecutive_errors")) > 0:
            health = "warn"
        add_lane({
            "lane_id": f"local_daemon:{daemon.get('adapter')}",
            "lane_type": "local_daemon",
            "adapter": daemon.get("adapter"),
            "agent_id": daemon.get("agent_id"),
            "workspace_id": "local-demo",
            "runtime_type": daemon.get("adapter") or "mock",
            "status": status,
            "management_mode": management_mode,
            "control_allowed": control_allowed,
            "process_claim_active": bool(daemon.get("process_claim_active")),
            "process_identity_status": daemon.get("process_identity_status"),
            "process_identity_verified": daemon.get("process_identity_verified") is True,
            "health": health,
            "heartbeat_state": "local_process" if running else "not_running",
            "session_state": "not_required",
            "active_session_count": 0,
            "last_seen_at": daemon.get("state_updated_at") or daemon.get("started_at") or daemon.get("stopped_at"),
            "workload": {
                "processed": _int(daemon.get("processed")),
                "iterations": _int(daemon.get("iterations")),
                "consecutive_errors": _int(daemon.get("consecutive_errors")),
                "total_errors": _int(daemon.get("total_errors")),
            },
            "next_action": (
                "agentops host status"
                if management_mode == "host_stack" and (running or identity_unverified)
                else "agentops worker logs --adapter " + str(daemon.get("adapter") or "mock")
                if running
                else "agentops worker start --adapter " + str(daemon.get("adapter") or "mock")
            ),
            "safe_ref": stable_id("fleet_lane", "local_daemon", daemon.get("adapter") or "mock")[-12:],
        })

    for worker in (remote_fleet.get("remote_workers") or []):
        token_status = worker.get("token_status") or "unknown"
        heartbeat_state = worker.get("heartbeat_state") or "unknown"
        active_sessions = _int(worker.get("active_session_count"))
        worker_key = (
            str(worker.get("workspace_id") or "local-demo"),
            str(worker.get("agent_id") or ""),
        )
        service_worker = service_workers_by_key.get(worker_key) or {}
        service_heartbeat_state = service_worker.get("heartbeat_state")
        reported_status = service_worker.get("reported_status")
        degraded_session_count = _int(service_worker.get("degraded_session_count"))
        if token_status != "active":
            health = "info"
            next_action = "agentops enrollment create --agent-id <agent_id>"
        elif service_worker and (
            service_heartbeat_state != "fresh"
            or reported_status not in SERVICE_WORKER_READY_STATUSES
            or degraded_session_count > 0
        ):
            health = "warn"
            next_action = "agentops doctor && agentops worker status"
        elif heartbeat_state == "stale":
            health = "warn"
            next_action = "agentops doctor && agentops agent heartbeat"
        elif heartbeat_state == "never_seen":
            health = "warn"
            next_action = "agentops agent heartbeat"
        elif active_sessions <= 0:
            health = "warn"
            next_action = "agentops session create --ttl-sec 900 --save-session"
        else:
            health = "pass"
            next_action = "agentops-worker --once --adapter mock --use-session"
        add_lane({
            "lane_id": f"remote_worker:{worker.get('agent_id')}:{worker.get('token_ref')}",
            "lane_type": "remote_worker",
            "adapter": worker.get("runtime_type") or "external",
            "agent_id": worker.get("agent_id"),
            "agent_name": worker.get("agent_name"),
            "workspace_id": worker.get("workspace_id"),
            "runtime_type": worker.get("runtime_type") or "external",
            "status": token_status,
            "health": health,
            "heartbeat_state": heartbeat_state,
            "service_heartbeat_state": service_heartbeat_state,
            "reported_status": reported_status,
            "degraded_session_count": degraded_session_count,
            "reported_status_counts": service_worker.get("reported_status_counts") or {},
            "session_state": "active" if active_sessions else "missing",
            "active_session_count": active_sessions,
            "last_seen_at": worker.get("last_heartbeat_at") or worker.get("last_used_at"),
            "expires_at": worker.get("expires_at"),
            "scope_count": _int(worker.get("scope_count")),
            "next_action": next_action,
            "safe_ref": worker.get("token_ref"),
            "token_id_omitted": True,
        })

    for worker in (remote_fleet.get("service_workers") or []):
        agent_id = worker.get("agent_id")
        workspace_id = str(worker.get("workspace_id") or "local-demo")
        if (workspace_id, str(agent_id or "")) in seen_workers:
            continue
        heartbeat_state = worker.get("heartbeat_state") or "unknown"
        reported_status = worker.get("reported_status") or "unknown"
        degraded_session_count = _int(worker.get("degraded_session_count"))
        execution_ready = heartbeat_state == "fresh" and reported_status in SERVICE_WORKER_READY_STATUSES
        health = "pass" if execution_ready and degraded_session_count == 0 else "warn"
        add_lane({
            "lane_id": f"gateway_service_worker:{workspace_id}:{agent_id}",
            "lane_type": "gateway_service_worker",
            "adapter": worker.get("runtime_type") or "external",
            "agent_id": agent_id,
            "agent_name": worker.get("agent_name"),
            "workspace_id": workspace_id,
            "runtime_type": worker.get("runtime_type") or "external",
            "status": reported_status,
            "health": health,
            "heartbeat_state": heartbeat_state,
            "degraded_session_count": degraded_session_count,
            "reported_status_counts": worker.get("reported_status_counts") or {},
            "session_state": worker.get("session_state") or "active",
            "active_session_count": _int(worker.get("active_session_count")),
            "last_seen_at": worker.get("last_heartbeat_at"),
            "management_mode": "external_service",
            "control_allowed": False,
            "process_state_verified": False,
            "next_action": "agentops worker status" if health == "pass" else "agentops doctor",
            "safe_ref": stable_id("fleet_lane", "gateway_service_worker", workspace_id, agent_id or "")[-12:],
        })

    for agent in worker_agents:
        workspace_id = str(agent.get("workspace_id") or "local-demo")
        if (workspace_id, str(agent.get("agent_id") or "")) in seen_workers:
            continue
        status = agent.get("status") or "unknown"
        add_lane({
            "lane_id": f"registered_worker:{agent.get('agent_id')}",
            "lane_type": "registered_worker",
            "adapter": agent.get("runtime_type") or "mock",
            "agent_id": agent.get("agent_id"),
            "agent_name": agent.get("name"),
            "workspace_id": workspace_id,
            "runtime_type": agent.get("runtime_type") or "mock",
            "status": status,
            "health": "info",
            "heartbeat_state": "registered_unverified",
            "session_state": "unknown",
            "active_session_count": 0,
            "last_seen_at": agent.get("updated_at"),
            "next_action": "agentops worker status",
            "safe_ref": stable_id("fleet_lane", "registered_worker", agent.get("agent_id") or "")[-12:],
        })

    lane_counts: dict[str, int] = {}
    health_counts: dict[str, int] = {}
    for lane in lanes:
        lane_counts[lane["lane_type"]] = lane_counts.get(lane["lane_type"], 0) + 1
        health_counts[lane["health"]] = health_counts.get(lane["health"], 0) + 1
    overall = "blocked" if health_counts.get("fail") else "attention" if health_counts.get("warn") else "ready"
    running_local_refs = {
        ("local-demo", str(daemon.get("agent_id") or f"local-daemon:{daemon.get('adapter') or 'unknown'}"))
        for daemon in daemons
        if daemon.get("running")
    }
    fresh_service_refs = {
        (str(worker.get("workspace_id") or "local-demo"), str(worker.get("agent_id")))
        for worker in (remote_fleet.get("service_workers") or [])
        if worker.get("agent_id")
        and worker.get("heartbeat_state") == "fresh"
        and worker.get("reported_status") in SERVICE_WORKER_READY_STATUSES
    }
    execution_capacity_refs = running_local_refs | fresh_service_refs
    next_actions = []
    for lane in lanes:
        action = lane.get("next_action")
        if action and lane.get("health") in {"fail", "warn"} and action not in next_actions:
            next_actions.append(action)
    if stuck_tasks and "agentops worker stuck" not in next_actions:
        next_actions.append("agentops worker stuck")
    if stuck_workflow_jobs and "agentops workflow stuck-jobs" not in next_actions:
        next_actions.append("agentops workflow stuck-jobs")
    if not next_actions:
        next_actions = ["agentops worker status", "agentops commander inbox --bucket ready_for_review"]

    return {
        "provider": "agentops-worker",
        "operation": "fleet_view",
        "status": overall,
        "summary": {
            "lane_count": len(lanes),
            "lane_counts": lane_counts,
            "health_counts": health_counts,
            "local_daemon_count": len(daemons),
            "running_local_daemons": len([daemon for daemon in daemons if daemon.get("running")]),
            "active_service_workers": len(fresh_service_refs),
            "degraded_service_workers": remote_fleet.get("degraded_service_workers", 0),
            "unavailable_service_workers": remote_fleet.get("unavailable_service_workers", 0),
            "stale_service_workers": remote_fleet.get("stale_service_workers", 0),
            "service_session_status_counts": remote_fleet.get("service_session_status_counts") or {},
            "execution_capacity_workers": len(execution_capacity_refs),
            "host_managed_workers": len([daemon for daemon in daemons if daemon.get("running") and daemon.get("management_mode") == "host_stack"]),
            "api_managed_daemons": len([daemon for daemon in daemons if daemon.get("running") and daemon.get("management_mode") != "host_stack"]),
            "unverified_process_claims": len([daemon for daemon in daemons if daemon.get("process_claim_active") and daemon.get("process_identity_verified") is not True]),
            "remote_worker_count": remote_fleet.get("remote_worker_count", 0),
            "fresh_remote_enrollments": remote_fleet.get("fresh_enrollments", 0),
            "stale_remote_enrollments": remote_fleet.get("stale_enrollments", 0),
            "never_seen_remote_enrollments": remote_fleet.get("never_seen_enrollments", 0),
            "active_remote_sessions": remote_fleet.get("active_sessions", 0),
            "stuck_worker_tasks": len(stuck_tasks),
            "stuck_workflow_jobs": len(stuck_workflow_jobs),
            "recommended_adapter": adapter_readiness.get("recommended_adapter"),
        },
        "lanes": lanes[:80],
        "next_actions": next_actions[:8],
        "contract": "read-only fleet management view; agents execute through Agent Gateway CLI/API and live adapters require explicit confirmation",
        "safety": {
            "read_only": True,
            "live_execution_performed": False,
            "token_omitted": True,
            "session_id_omitted": True,
            "raw_prompt_omitted": True,
        },
        "token_omitted": True,
        "live_execution_performed": False,
    }
