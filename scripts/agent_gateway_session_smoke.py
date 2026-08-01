#!/usr/bin/env python3
"""Verify short-lived Agent Gateway sessions inherit scope, list, revoke, and expire."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


def now_stamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d%H%M%S")


def http_json(
    method: str,
    base_url: str,
    path: str,
    payload: dict | None = None,
    token: str | None = None,
    query: dict | None = None,
) -> tuple[int, dict]:
    url = base_url.rstrip("/") + path
    if query:
        url += "?" + urllib.parse.urlencode({k: v for k, v in query.items() if v is not None}, doseq=True)
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            body = json.loads(raw)
        except Exception:
            body = {"raw": raw}
        return exc.code, body


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def safe_ref(prefix: str, raw: str) -> str:
    import hashlib
    import re

    slug = re.sub(r"[^a-zA-Z0-9_]+", "_", raw or "").strip("_").lower()
    if slug and len(slug) <= 64:
        return f"{prefix}_{slug}"[-12:]
    return f"{prefix}_{hashlib.sha256((raw or '').encode('utf-8')).hexdigest()[:16]}"[-12:]


def smoke(base_url: str, stamp: str) -> dict:
    agent_id = f"agt_session_smoke_{stamp}"
    task_id = f"tsk_session_smoke_{stamp}"
    token_id = None
    try:
        status, enrollment = http_json("POST", base_url, "/api/agent-gateway/enrollment/create", {
            "agent_id": agent_id,
            "name": "Session Smoke Agent",
            "runtime_type": "mock",
            "workspace_id": "local-demo",
            "scopes": ["agents:heartbeat", "tasks:read", "audit:write"],
            "ttl_days": 1,
            "heartbeat_timeout_sec": 60,
        })
        require(status == 201, f"enrollment create failed: {status} {enrollment}")
        enrollment_token = enrollment.get("token")
        token_id = enrollment.get("token_id")
        require(enrollment_token and token_id, f"missing enrollment token: {enrollment}")

        status, session = http_json("POST", base_url, "/api/agent-gateway/session/create", {
            "ttl_sec": 60,
            "scopes": ["agents:heartbeat", "tasks:read"],
        }, token=enrollment_token)
        require(status == 201, f"session create failed: {status} {session}")
        session_token = session.get("session_token")
        session_id = session.get("session_id")
        require(session_token and session_id, f"missing session token: {session}")
        require("audit:write" not in session.get("scopes", []), f"session scopes were not narrowed: {session}")

        status, revoke_session = http_json("POST", base_url, "/api/agent-gateway/session/create", {
            "ttl_sec": 60,
            "scopes": ["agents:heartbeat", "tasks:read"],
        }, token=enrollment_token)
        require(status == 201, f"revoke session create failed: {status} {revoke_session}")
        revoke_session_token = revoke_session.get("session_token")
        revoke_session_id = revoke_session.get("session_id")
        require(revoke_session_token and revoke_session_id, f"missing revoke session token: {revoke_session}")

        status, self_revoke_session = http_json("POST", base_url, "/api/agent-gateway/session/create", {
            "ttl_sec": 60,
            "scopes": ["agents:heartbeat", "tasks:read"],
        }, token=enrollment_token)
        require(status == 201, f"self-revoke session create failed: {status} {self_revoke_session}")
        self_revoke_session_token = self_revoke_session.get("session_token")
        self_revoke_session_id = self_revoke_session.get("session_id")
        require(self_revoke_session_token and self_revoke_session_id, f"missing self-revoke session token: {self_revoke_session}")

        status, cascade_session = http_json("POST", base_url, "/api/agent-gateway/session/create", {
            "ttl_sec": 60,
            "scopes": ["agents:heartbeat", "tasks:read"],
        }, token=enrollment_token)
        require(status == 201, f"cascade session create failed: {status} {cascade_session}")
        cascade_session_id = cascade_session.get("session_id")
        require(cascade_session_id, f"missing cascade session id: {cascade_session}")

        status, expire_session = http_json("POST", base_url, "/api/agent-gateway/session/create", {
            "ttl_sec": 1,
            "scopes": ["agents:heartbeat"],
        }, token=enrollment_token)
        require(status == 201, f"expire session create failed: {status} {expire_session}")
        expire_session_token = expire_session.get("session_token")
        expire_session_id = expire_session.get("session_id")
        require(expire_session_token and expire_session_id, f"missing expire session token: {expire_session}")

        status, listed = http_json("GET", base_url, "/api/agent-gateway/sessions")
        require(status == 200, f"session list failed: {status} {listed}")
        session_rows = listed.get("sessions", [])
        listed_refs = {item.get("session_ref") for item in session_rows}
        expected_refs = {
            safe_ref("session_ref", session_id),
            safe_ref("session_ref", revoke_session_id),
            safe_ref("session_ref", self_revoke_session_id),
            safe_ref("session_ref", cascade_session_id),
            safe_ref("session_ref", expire_session_id),
        }
        require(expected_refs.issubset(listed_refs), f"session list missing refs: {listed_refs}")
        require(all("session_hash" not in item for item in session_rows), "session list leaked session_hash")
        require(all(item.get("session_id_omitted") is True for item in session_rows), f"session list missing omission proof: {session_rows}")
        listed_serialized = json.dumps(listed, ensure_ascii=False)
        require(all("session_id" not in item for item in session_rows), f"session list leaked session_id field: {listed}")
        require(all("parent_token_id" not in item for item in session_rows), f"session list leaked parent_token_id field: {listed}")
        require(session_id not in listed_serialized, f"session list leaked raw session id: {listed}")
        require(token_id not in listed_serialized, f"session list leaked raw parent token id: {listed}")

        status, revoked = http_json("POST", base_url, "/api/agent-gateway/session/revoke", {"session_id": revoke_session_id})
        require(status == 200, f"session revoke failed: {status} {revoked}")
        require(revoked.get("revoked") == 1, f"session revoke result wrong: {revoked}")
        require(revoked.get("session_id_omitted") is True, f"session revoke should omit raw session id: {revoked}")
        require(revoke_session_id not in json.dumps(revoked, ensure_ascii=False), f"session revoke leaked raw session id: {revoked}")
        require(safe_ref("session_ref", revoke_session_id) in set(revoked.get("session_refs", [])), f"session revoke missing safe ref: {revoked}")
        status, rejected = http_json("POST", base_url, "/api/agent-gateway/heartbeat", {
            "status": "idle",
            "summary": "revoked session heartbeat",
        }, token=revoke_session_token)
        require(status == 401, f"revoked session should be rejected: {status} {rejected}")
        require("revoked" in json.dumps(rejected).lower(), f"revoked session message missing: {rejected}")

        status, wrong_actor_self_revoke = http_json(
            "POST",
            base_url,
            "/api/agent-gateway/session/revoke-self",
            {},
            token=enrollment_token,
        )
        require(status == 403 and wrong_actor_self_revoke.get("error") == "session_auth_required",
                f"parent enrollment token revoked a child session as self: {status} {wrong_actor_self_revoke}")
        status, self_revoked = http_json(
            "POST",
            base_url,
            "/api/agent-gateway/session/revoke-self",
            {},
            token=self_revoke_session_token,
        )
        require(status == 200 and self_revoked.get("revoked") is True,
                f"session self-revoke failed: {status} {self_revoked}")
        require(self_revoked.get("session_id_omitted") is True and self_revoked.get("token_omitted") is True,
                f"session self-revoke omission proof missing: {self_revoked}")
        require(self_revoke_session_id not in json.dumps(self_revoked, ensure_ascii=False),
                f"session self-revoke leaked raw session id: {self_revoked}")
        require(self_revoked.get("session_ref") == safe_ref("session_ref", self_revoke_session_id),
                f"session self-revoke safe ref mismatch: {self_revoked}")
        status, self_revoke_rejected = http_json("POST", base_url, "/api/agent-gateway/heartbeat", {
            "status": "idle",
            "summary": "self-revoked session heartbeat",
        }, token=self_revoke_session_token)
        require(status == 401 and "revoked" in json.dumps(self_revoke_rejected).lower(),
                f"self-revoked session retained access: {status} {self_revoke_rejected}")

        status, task = http_json("POST", base_url, "/api/tasks", {
            "task_id": task_id,
            "workspace_id": "local-demo",
            "title": "session smoke task",
            "description": "Verify short-lived Agent Gateway session can pull tasks.",
            "owner_agent_id": agent_id,
            "status": "planned",
            "priority": "medium",
            "risk_level": "low",
        })
        require(status == 201, f"task create failed: {status} {task}")

        status, gateway_status = http_json("GET", base_url, "/api/agent-gateway/status", token=session_token)
        require(status == 200, f"session status failed: {status} {gateway_status}")
        gateway_auth = gateway_status.get("auth") or {}
        gateway_auth_serialized = json.dumps(gateway_auth, ensure_ascii=False)
        require(gateway_auth.get("mode") == "agent_session", f"wrong auth mode: {gateway_status}")
        require(gateway_auth.get("session_ref") == safe_ref("session_ref", session_id), f"wrong session ref: {gateway_status}")
        require(gateway_auth.get("parent_token_ref") == safe_ref("token_ref", token_id), f"wrong parent token ref: {gateway_status}")
        require(gateway_auth.get("session_id_omitted") is True, f"session id omission flag missing: {gateway_status}")
        require(gateway_auth.get("parent_token_id_omitted") is True, f"parent token id omission flag missing: {gateway_status}")
        require("session_id" not in gateway_auth, f"status auth leaked session_id field: {gateway_status}")
        require("parent_token_id" not in gateway_auth, f"status auth leaked parent_token_id field: {gateway_status}")
        require(session_id not in gateway_auth_serialized, f"status auth leaked raw session id: {gateway_status}")
        require(token_id not in gateway_auth_serialized, f"status auth leaked raw parent token id: {gateway_status}")

        status, heartbeat = http_json("POST", base_url, "/api/agent-gateway/heartbeat", {
            "status": "idle",
            "summary": "session smoke heartbeat",
        }, token=session_token)
        require(status == 200, f"session heartbeat failed: {status} {heartbeat}")

        status, pulled = http_json("GET", base_url, "/api/agent-gateway/tasks/pull", token=session_token, query={"status": "planned", "limit": 10})
        require(status == 200, f"session pull failed: {status} {pulled}")
        pulled_ids = {item.get("task_id") for item in pulled.get("tasks", [])}
        require(task_id in pulled_ids, f"session did not pull owned task: {pulled_ids}")

        status, nested = http_json("POST", base_url, "/api/agent-gateway/session/create", {"ttl_sec": 30}, token=session_token)
        require(status == 401, f"session should not mint another session: {status} {nested}")

        time.sleep(2)
        status, expired = http_json("POST", base_url, "/api/agent-gateway/heartbeat", {
            "status": "idle",
            "summary": "expired session heartbeat",
        }, token=expire_session_token)
        require(status == 401, f"expired session should be rejected: {status} {expired}")
        require("expired" in json.dumps(expired).lower(), f"expired session message missing: {expired}")

        status, revoke_parent = http_json("POST", base_url, "/api/agent-gateway/enrollment/revoke", {"token_id": token_id})
        require(status == 200, f"enrollment revoke failed: {status} {revoke_parent}")
        require(revoke_parent.get("sessions_revoked", 0) >= 1, f"parent revoke did not cascade to sessions: {revoke_parent}")
        require(revoke_parent.get("token_id_omitted") is True, f"parent revoke should omit raw token ids: {revoke_parent}")
        require(revoke_parent.get("session_id_omitted") is True, f"parent revoke should omit raw session ids: {revoke_parent}")
        revoke_parent_serialized = json.dumps(revoke_parent, ensure_ascii=False)
        require(token_id not in revoke_parent_serialized, f"parent revoke leaked raw token id: {revoke_parent}")
        require(cascade_session_id not in revoke_parent_serialized, f"parent revoke leaked cascade session id: {revoke_parent}")
        require(session_id not in revoke_parent_serialized, f"parent revoke leaked active session id: {revoke_parent}")
        require(safe_ref("session_ref", cascade_session_id) in set(revoke_parent.get("session_refs", [])), f"cascade session ref missing from revoke result: {revoke_parent}")
        require(safe_ref("session_ref", session_id) in set(revoke_parent.get("session_refs", [])), f"active session ref missing from parent revoke result: {revoke_parent}")
        status, parent_revoked = http_json("POST", base_url, "/api/agent-gateway/heartbeat", {
            "status": "idle",
            "summary": "parent-revoked session heartbeat",
        }, token=session_token)
        require(status == 401, f"parent-revoked session should be rejected: {status} {parent_revoked}")
        require("revoked" in json.dumps(parent_revoked).lower(), f"parent-revoked session message missing: {parent_revoked}")
        token_id = None

        return {
            "agent_id": agent_id,
            "task_id": task_id,
            "token_ref": safe_ref("token_ref", enrollment.get("token_id") or ""),
            "session_ref": safe_ref("session_ref", session_id),
            "revoked_session_ref": safe_ref("session_ref", revoke_session_id),
            "self_revoked_session_ref": safe_ref("session_ref", self_revoke_session_id),
            "cascade_session_ref": safe_ref("session_ref", cascade_session_id),
            "expired_session_ref": safe_ref("session_ref", expire_session_id),
            "session_scopes": session.get("scopes", []),
            "auth_mode": gateway_auth.get("mode"),
            "status_session_ref": gateway_auth.get("session_ref"),
            "status_parent_token_ref": gateway_auth.get("parent_token_ref"),
            "listed_session_count": len(session_rows),
            "revoke_status": rejected.get("error"),
            "self_revoke_status": self_revoke_rejected.get("error"),
            "expired_status": expired.get("error"),
            "parent_revoked_status": parent_revoked.get("error"),
            "cascade_sessions_revoked": revoke_parent.get("sessions_revoked"),
            "token_omitted": True,
        }
    finally:
        if token_id:
            http_json("POST", base_url, "/api/agent-gateway/enrollment/revoke", {"token_id": token_id})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify Agent Gateway short-lived session behavior.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8787")
    args = parser.parse_args(argv)
    result = {"ok": True, "base_url": args.base_url, "smoke": smoke(args.base_url, now_stamp())}
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise
