"""Human browser authentication for private AgentOps MIS hosts."""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import urllib.parse
import uuid
from http.cookies import SimpleCookie


SESSION_COOKIE = "agentops_human_session"
DEVICE_COOKIE = "agentops_human_device"
SESSION_TTL_SECONDS = 12 * 60 * 60
DEVICE_TTL_SECONDS = 90 * 24 * 60 * 60
RECOVERY_TTL_SECONDS = 10 * 60
PAIRING_TTL_SECONDS = 10 * 60
PAIRING_MAX_ATTEMPTS = 5
AUTH_THROTTLE_WINDOW_SECONDS = 5 * 60
AUTH_THROTTLE_BLOCK_SECONDS = 5 * 60
LOGIN_SUBJECT_MAX_FAILURES = 8
LOGIN_GLOBAL_MAX_FAILURES = 100
PAIRING_SUBJECT_MAX_FAILURES = 8
PAIRING_GLOBAL_MAX_FAILURES = 60
MIN_PASSWORD_LENGTH = 12
ROLE_RANK = {"viewer": 10, "operator": 20, "approver": 30, "owner": 40}
UNTRUSTED_FORWARDING_HEADERS = (
    "Forwarded",
    "X-Forwarded-For",
    "X-Forwarded-Host",
    "X-Forwarded-Port",
    "X-Forwarded-Proto",
    "X-Real-IP",
)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS human_accounts (
    account_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL DEFAULT 'local-demo',
    username TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('owner','operator','approver','viewer')),
    password_hash TEXT NOT NULL,
    password_salt TEXT NOT NULL,
    password_params_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL CHECK(status IN ('active','disabled')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_login_at TEXT
);

CREATE TABLE IF NOT EXISTS human_devices (
    device_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    device_hash TEXT NOT NULL UNIQUE,
    label TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('active','revoked')),
    created_at TEXT NOT NULL,
    last_seen_at TEXT,
    revoked_at TEXT,
    FOREIGN KEY(account_id) REFERENCES human_accounts(account_id)
);

CREATE INDEX IF NOT EXISTS idx_human_devices_account
ON human_devices(account_id,status);

CREATE TABLE IF NOT EXISTS human_sessions (
    session_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    session_hash TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK(status IN ('active','revoked','expired')),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    last_seen_at TEXT,
    revoked_at TEXT,
    device_id TEXT,
    FOREIGN KEY(account_id) REFERENCES human_accounts(account_id)
);

CREATE INDEX IF NOT EXISTS idx_human_sessions_hash ON human_sessions(session_hash);
CREATE INDEX IF NOT EXISTS idx_human_sessions_account ON human_sessions(account_id,status);

CREATE TABLE IF NOT EXISTS human_recovery_challenges (
    challenge_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    challenge_hash TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK(status IN ('active','used','expired')),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used_at TEXT,
    FOREIGN KEY(account_id) REFERENCES human_accounts(account_id)
);

CREATE INDEX IF NOT EXISTS idx_human_recovery_challenges_hash
ON human_recovery_challenges(challenge_hash,status);

CREATE TABLE IF NOT EXISTS human_pairing_invitations (
    invitation_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    created_by_account_id TEXT NOT NULL,
    secret_hash TEXT NOT NULL UNIQUE,
    role TEXT NOT NULL CHECK(role IN ('viewer','operator','approver')),
    label TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('active','redeemed','revoked','expired','locked')),
    attempt_count INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 5,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    redeemed_at TEXT,
    revoked_at TEXT,
    redeemed_account_id TEXT,
    redeemed_device_id TEXT,
    FOREIGN KEY(created_by_account_id) REFERENCES human_accounts(account_id)
);

CREATE INDEX IF NOT EXISTS idx_human_pairing_invitations_workspace
ON human_pairing_invitations(workspace_id,status,created_at);

CREATE TABLE IF NOT EXISTS human_auth_throttle_buckets (
    bucket_key TEXT PRIMARY KEY,
    scope TEXT NOT NULL CHECK(scope IN ('login_subject','login_global','pairing_subject','pairing_global')),
    window_started_at TEXT NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    blocked_until TEXT,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_human_auth_throttle_updated
ON human_auth_throttle_buckets(updated_at);
"""


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def throttle_bucket_key(scope: str, discriminator: str) -> str:
    digest = hashlib.sha256(
        f"agentops-human-auth-throttle-v1:{scope}:{discriminator}".encode("utf-8")
    ).hexdigest()
    return f"hatb_{digest}"


def _throttle_retry_after(blocked_until: str, current: dt.datetime) -> int:
    try:
        remaining = (dt.datetime.fromisoformat(blocked_until) - current).total_seconds()
    except (TypeError, ValueError):
        remaining = AUTH_THROTTLE_BLOCK_SECONDS
    return max(1, int(remaining) + 1)


def _throttle_response(retry_after_seconds: int) -> dict:
    return {
        "error": "too_many_attempts",
        "message": "Too many authentication attempts. Try again later.",
        "retry_after_seconds": max(1, int(retry_after_seconds)),
        "source_identity_omitted": True,
        "token_omitted": True,
    }


def _throttle_scope_keys(kind: str, discriminator: str) -> tuple[tuple[str, str, int], tuple[str, str, int]]:
    if kind == "login":
        return (
            ("login_subject", throttle_bucket_key("login_subject", discriminator or "missing"), LOGIN_SUBJECT_MAX_FAILURES),
            ("login_global", throttle_bucket_key("login_global", "endpoint"), LOGIN_GLOBAL_MAX_FAILURES),
        )
    if kind != "pairing":
        raise ValueError("unsupported human authentication throttle kind")
    return (
        ("pairing_subject", throttle_bucket_key("pairing_subject", discriminator or "missing"), PAIRING_SUBJECT_MAX_FAILURES),
        ("pairing_global", throttle_bucket_key("pairing_global", "endpoint"), PAIRING_GLOBAL_MAX_FAILURES),
    )


def auth_throttle_check(conn, kind: str, discriminator: str) -> dict | None:
    current = dt.datetime.now(dt.timezone.utc)
    retry_after = 0
    for _scope, bucket_key, _limit in _throttle_scope_keys(kind, discriminator):
        row = conn.execute(
            "SELECT blocked_until FROM human_auth_throttle_buckets WHERE bucket_key=?",
            (bucket_key,),
        ).fetchone()
        if row and row["blocked_until"] and row["blocked_until"] > current.isoformat():
            retry_after = max(retry_after, _throttle_retry_after(row["blocked_until"], current))
    return _throttle_response(retry_after) if retry_after else None


def _record_throttle_bucket(conn, scope: str, bucket_key: str, limit: int, current: dt.datetime) -> int:
    timestamp = current.isoformat()
    window_cutoff = (current - dt.timedelta(seconds=AUTH_THROTTLE_WINDOW_SECONDS)).isoformat()
    row = conn.execute(
        "SELECT window_started_at,attempt_count,blocked_until FROM human_auth_throttle_buckets WHERE bucket_key=?",
        (bucket_key,),
    ).fetchone()
    if not row or row["window_started_at"] <= window_cutoff or (
        row["blocked_until"] and row["blocked_until"] <= timestamp
    ):
        attempt_count = 1
        window_started_at = timestamp
    else:
        attempt_count = int(row["attempt_count"] or 0) + 1
        window_started_at = row["window_started_at"]
    blocked_until = None
    if attempt_count >= limit:
        blocked_until = (current + dt.timedelta(seconds=AUTH_THROTTLE_BLOCK_SECONDS)).isoformat()
    conn.execute(
        """
        INSERT INTO human_auth_throttle_buckets(
            bucket_key,scope,window_started_at,attempt_count,blocked_until,updated_at
        ) VALUES(?,?,?,?,?,?)
        ON CONFLICT(bucket_key) DO UPDATE SET
            scope=excluded.scope,
            window_started_at=excluded.window_started_at,
            attempt_count=excluded.attempt_count,
            blocked_until=excluded.blocked_until,
            updated_at=excluded.updated_at
        """,
        (bucket_key, scope, window_started_at, attempt_count, blocked_until, timestamp),
    )
    return _throttle_retry_after(blocked_until, current) if blocked_until else 0


def auth_throttle_failure(conn, kind: str, discriminator: str) -> dict | None:
    current = dt.datetime.now(dt.timezone.utc)
    stale_cutoff = (current - dt.timedelta(days=1)).isoformat()
    conn.execute("DELETE FROM human_auth_throttle_buckets WHERE updated_at<?", (stale_cutoff,))
    retry_after = 0
    for scope, bucket_key, limit in _throttle_scope_keys(kind, discriminator):
        retry_after = max(retry_after, _record_throttle_bucket(conn, scope, bucket_key, limit, current))
    return _throttle_response(retry_after) if retry_after else None


def auth_throttle_clear_subject(conn, kind: str, discriminator: str) -> None:
    scope, bucket_key, _limit = _throttle_scope_keys(kind, discriminator)[0]
    conn.execute(
        "DELETE FROM human_auth_throttle_buckets WHERE bucket_key=? AND scope=?",
        (bucket_key, scope),
    )


def session_reference(session_id: str) -> str:
    digest = hashlib.sha256(f"agentops-human-session-ref-v1:{session_id}".encode("utf-8")).hexdigest()
    return f"hsref_{digest[:16]}"


def invitation_reference(invitation_id: str) -> str:
    digest = hashlib.sha256(f"agentops-human-invitation-ref-v1:{invitation_id}".encode("utf-8")).hexdigest()
    return f"hiref_{digest[:16]}"


def device_reference(device_id: str) -> str:
    digest = hashlib.sha256(f"agentops-human-device-ref-v1:{device_id}".encode("utf-8")).hexdigest()
    return f"hdref_{digest[:16]}"


def init_schema(conn) -> None:
    conn.executescript(SCHEMA_SQL)
    session_columns = {row[1] for row in conn.execute("PRAGMA table_info(human_sessions)").fetchall()}
    if "device_id" not in session_columns:
        conn.execute("ALTER TABLE human_sessions ADD COLUMN device_id TEXT")


def required(env=None) -> bool:
    env = env or os.environ
    configured = str(env.get("AGENTOPS_HUMAN_AUTH_REQUIRED", "")).strip().lower()
    if configured:
        return configured in {"1", "true", "yes", "on"}
    mode = str(env.get("AGENTOPS_DEPLOYMENT_MODE", "local")).strip().lower()
    return mode == "private_host"


def cookie_secure(env=None) -> bool:
    env = env or os.environ
    configured = str(env.get("AGENTOPS_COOKIE_SECURE", "")).strip().lower()
    if configured in {"1", "true", "yes", "on"}:
        return True
    if configured in {"0", "false", "no", "off"}:
        return False
    return required(env)


def cookie_secure_for_request(headers, env=None) -> bool:
    if not cookie_secure(env):
        return False
    supplied = (headers.get("Origin") or "").strip().rstrip("/") if headers else ""
    if supplied:
        try:
            parsed = urllib.parse.urlsplit(supplied)
            if parsed.scheme == "https":
                return True
            hostname = parsed.hostname or ""
            if parsed.scheme == "http" and hostname:
                if hostname.lower() == "localhost" or ipaddress.ip_address(hostname).is_loopback:
                    return False
        except ValueError:
            return True
    host = (headers.get("Host") or "").strip() if headers else ""
    try:
        hostname = urllib.parse.urlsplit(f"//{host}").hostname or ""
        if hostname:
            if hostname.lower() == "localhost" or ipaddress.ip_address(hostname).is_loopback:
                return False
    except ValueError:
        pass
    return True


def allowed_origins(env=None) -> list[str]:
    env = env or os.environ
    return sorted({
        item.strip().rstrip("/")
        for item in str(env.get("AGENTOPS_ALLOWED_ORIGINS", "")).split(",")
        if item.strip()
    })


def canonical_request_origin(headers, env=None) -> str | None:
    """Resolve a safe request origin without trusting HTTP proxy headers."""
    host = (headers.get("Host") or "").strip().lower() if headers else ""
    if not host:
        return None
    configured_origins = allowed_origins(env)
    for origin in configured_origins:
        try:
            parsed = urllib.parse.urlsplit(origin)
        except ValueError:
            continue
        if parsed.scheme in {"http", "https"} and parsed.netloc.lower() == host:
            return origin
    if configured_origins:
        return None
    try:
        parsed_host = urllib.parse.urlsplit(f"//{host}")
        hostname = parsed_host.hostname or ""
        if hostname.lower() == "localhost" or (hostname and ipaddress.ip_address(hostname).is_loopback):
            return f"http://{host}"
    except ValueError:
        return None
    return None


def forwarding_headers_ignored(headers) -> bool:
    """Return whether untrusted proxy metadata was present and ignored."""
    if not headers:
        return False
    known = {name.lower() for name in UNTRUSTED_FORWARDING_HEADERS}
    return any(
        str(name).lower() in known
        or str(name).lower().startswith("x-forwarded-")
        for name in headers.keys()
    )


def direct_host_allowed(headers, env=None) -> bool:
    host = (headers.get("Host") or "").strip().lower() if headers else ""
    if not host:
        return False
    authorities = set()
    for origin in allowed_origins(env):
        try:
            parsed = urllib.parse.urlsplit(origin)
        except ValueError:
            continue
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            authorities.add(parsed.netloc.lower())
    return host in authorities


def origin_error(headers) -> tuple[dict, int] | None:
    allowed = allowed_origins()
    if not allowed:
        if required():
            return {
                "error": "origin_configuration_required",
                "message": "Private Host browser access requires an explicit Origin allowlist.",
                "origin_omitted": True,
                "host_omitted": True,
            }, 503
        return None
    supplied = (headers.get("Origin") or "").strip().rstrip("/")
    if supplied in allowed and direct_host_allowed(headers):
        return None
    return {
        "error": "origin_validation_failed",
        "message": "The browser Origin or direct Host is not allowed for this Host.",
        "origin_omitted": True,
        "host_omitted": True,
    }, 403


def local_recovery_origin_error(headers) -> tuple[dict, int] | None:
    supplied = (headers.get("Origin") or "").strip().rstrip("/") if headers else ""
    scheme = ""
    try:
        parsed = urllib.parse.urlsplit(supplied)
        scheme = parsed.scheme
        hostname = parsed.hostname or ""
        is_loopback = bool(hostname and ipaddress.ip_address(hostname).is_loopback)
    except ValueError:
        is_loopback = False
    if supplied and scheme == "http" and is_loopback:
        return None
    return {
        "error": "local_recovery_required",
        "message": "Password recovery can only be started from the Host's loopback Console.",
        "origin_omitted": True,
    }, 403


def account_public(row) -> dict:
    return {
        "account_id": row["account_id"],
        "workspace_id": row["workspace_id"],
        "username": row["username"],
        "display_name": row["display_name"],
        "role": row["role"],
        "status": row["status"] if "status" in row.keys() else row["account_status"],
    }


def password_hash(password: str, salt: bytes, params=None) -> tuple[str, dict]:
    params = params or {"name": "scrypt", "n": 16384, "r": 8, "p": 1, "dklen": 32}
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=int(params["n"]),
        r=int(params["r"]),
        p=int(params["p"]),
        dklen=int(params["dklen"]),
    )
    return derived.hex(), params


def password_valid(password: str, row) -> bool:
    try:
        params = json.loads(row["password_params_json"] or "{}")
        actual, _ = password_hash(password, bytes.fromhex(row["password_salt"]), params)
    except (TypeError, ValueError, KeyError, json.JSONDecodeError):
        return False
    return hmac.compare_digest(actual, row["password_hash"])


def session_token(headers) -> str:
    raw = (headers.get("Cookie") if headers else "") or ""
    if not raw:
        return ""
    cookie = SimpleCookie()
    try:
        cookie.load(raw)
    except Exception:
        return ""
    morsel = cookie.get(SESSION_COOKIE)
    return morsel.value.strip() if morsel else ""


def device_token(headers) -> str:
    raw = (headers.get("Cookie") if headers else "") or ""
    if not raw:
        return ""
    cookie = SimpleCookie()
    try:
        cookie.load(raw)
    except Exception:
        return ""
    morsel = cookie.get(DEVICE_COOKIE)
    return morsel.value.strip() if morsel else ""


def csrf_token(token: str) -> str:
    return hmac.new(token.encode("utf-8"), b"agentops-human-csrf-v1", hashlib.sha256).hexdigest()


def auth_context(conn, headers, *, touch=True) -> tuple[dict | None, dict | None]:
    token = session_token(headers)
    if not token:
        return None, {"error": "human_auth_required", "message": "A human browser session is required."}
    row = conn.execute(
        """
        SELECT s.*,a.workspace_id,a.username,a.display_name,a.role,a.status AS account_status,
               d.status AS device_status,d.device_hash,d.account_id AS device_account_id,
               d.workspace_id AS device_workspace_id
        FROM human_sessions s
        JOIN human_accounts a ON a.account_id=s.account_id
        LEFT JOIN human_devices d ON d.device_id=s.device_id
        WHERE s.session_hash=?
        """,
        (token_hash(token),),
    ).fetchone()
    if not row or row["status"] != "active" or row["account_status"] != "active":
        return None, {"error": "human_session_invalid", "message": "The human browser session is invalid or revoked."}
    if row["expires_at"] <= now_iso():
        conn.execute("UPDATE human_sessions SET status='expired',revoked_at=? WHERE session_id=?", (now_iso(), row["session_id"]))
        return None, {"error": "human_session_expired", "message": "The human browser session has expired."}
    if row["device_id"]:
        supplied_device = device_token(headers)
        valid_device = (
            supplied_device
            and row["device_status"] == "active"
            and row["device_hash"]
            and hmac.compare_digest(token_hash(supplied_device), row["device_hash"])
            and row["device_account_id"] == row["account_id"]
            and row["device_workspace_id"] == row["workspace_id"]
        )
        if not valid_device:
            return None, {"error": "human_device_invalid", "message": "The paired browser device is invalid or revoked."}
    if touch:
        conn.execute("UPDATE human_sessions SET last_seen_at=? WHERE session_id=?", (now_iso(), row["session_id"]))
        if row["device_id"]:
            conn.execute("UPDATE human_devices SET last_seen_at=? WHERE device_id=?", (now_iso(), row["device_id"]))
    return {
        "mode": "human_session",
        "session_id": row["session_id"],
        "account_id": row["account_id"],
        "workspace_id": row["workspace_id"],
        "username": row["username"],
        "display_name": row["display_name"],
        "role": row["role"],
        "session_token": token,
        "expires_at": row["expires_at"],
        "device_ref": device_reference(row["device_id"]) if row["device_id"] else None,
    }, None


def required_role(path: str, method: str) -> str:
    if method == "GET":
        if path in {
            "/api/human-auth/sessions",
            "/api/host/relay",
            "/api/workers/local/logs",
            "/api/runs/export",
            "/api/memories/export",
        } or path.startswith(("/api/human-auth/pairing-invitations", "/api/human-auth/devices")):
            return "owner"
        return "viewer"
    if path == "/api/knowledge/index" or path.startswith((
        "/api/host/relay",
        "/api/human-auth/sessions",
        "/api/human-auth/pairing-invitations",
        "/api/human-auth/devices",
        "/api/workers/local/",
        "/api/integrations/",
        "/api/migration/",
        "/api/commander/work-packages/",
    )):
        return "owner"
    if path.startswith(("/api/approvals/", "/api/memories/", "/api/agent-plans/")):
        return "approver"
    return "operator"


def request_auth(conn, headers, path: str, method: str) -> tuple[dict | None, tuple[dict, int] | None]:
    context, error = auth_context(conn, headers)
    if error:
        return None, (error, 401)
    requested_workspace = (headers.get("X-AgentOps-Workspace-Id") or "").strip()
    if requested_workspace and requested_workspace != context["workspace_id"]:
        return None, ({
            "error": "human_workspace_forbidden",
            "message": "The human session cannot access another workspace.",
        }, 403)
    role = required_role(path, method)
    if ROLE_RANK.get(context["role"], 0) < ROLE_RANK[role]:
        return None, ({
            "error": "human_role_forbidden",
            "message": f"This action requires the {role} role.",
            "required_role": role,
            "current_role": context["role"],
        }, 403)
    if method in {"POST", "PATCH", "PUT", "DELETE"}:
        invalid_origin = origin_error(headers)
        if invalid_origin:
            return None, invalid_origin
        supplied = (headers.get("X-AgentOps-CSRF") or "").strip()
        expected = csrf_token(context["session_token"])
        if not supplied or not hmac.compare_digest(supplied, expected):
            return None, ({
                "error": "csrf_validation_failed",
                "message": "A valid X-AgentOps-CSRF token is required for state-changing browser requests.",
            }, 403)
    return context, None


def status(conn, headers) -> dict:
    is_required = required()
    account_count = int(conn.execute("SELECT COUNT(*) FROM human_accounts WHERE status='active'").fetchone()[0])
    context, _error = auth_context(conn, headers) if is_required else (None, None)
    payload = {
        "provider": "agentops-human-auth",
        "required": is_required,
        "authenticated": bool(context),
        "bootstrap_required": is_required and account_count == 0,
        "bootstrap_available": bool(os.environ.get("AGENTOPS_OWNER_SETUP_CODE", "").strip()),
        "password_recovery_available": is_required and account_count > 0,
        "password_recovery_local_only": True,
        "cookie_secure": cookie_secure_for_request(headers),
        "forwarding_headers_trusted": False,
        "forwarding_headers_ignored": forwarding_headers_ignored(headers),
        "token_omitted": True,
    }
    if context:
        payload.update({
            "user": {key: context[key] for key in ("account_id", "workspace_id", "username", "display_name", "role")},
            "csrf_token": csrf_token(context["session_token"]),
            "session_expires_at": context["expires_at"],
        })
    return payload


def session_cookie(token: str, *, clear=False, secure=None) -> str:
    parts = [f"{SESSION_COOKIE}={'' if clear else token}", "Path=/", "HttpOnly", "SameSite=Strict"]
    if clear:
        parts.extend(["Max-Age=0", "Expires=Thu, 01 Jan 1970 00:00:00 GMT"])
    else:
        parts.append(f"Max-Age={SESSION_TTL_SECONDS}")
    effective_secure = cookie_secure() if secure is None else bool(secure)
    if effective_secure:
        parts.append("Secure")
    return "; ".join(parts)


def device_cookie(token: str, *, clear=False, secure=None) -> str:
    parts = [f"{DEVICE_COOKIE}={'' if clear else token}", "Path=/", "HttpOnly", "SameSite=Strict"]
    if clear:
        parts.extend(["Max-Age=0", "Expires=Thu, 01 Jan 1970 00:00:00 GMT"])
    else:
        parts.append(f"Max-Age={DEVICE_TTL_SECONDS}")
    effective_secure = cookie_secure() if secure is None else bool(secure)
    if effective_secure:
        parts.append("Secure")
    return "; ".join(parts)


def create_session(conn, account_row, *, device_id: str | None = None) -> tuple[dict, str, dict]:
    token = secrets.token_urlsafe(32)
    created_at = now_iso()
    expires_at = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=SESSION_TTL_SECONDS)).isoformat()
    session_id = new_id("hsess")
    conn.execute(
        "INSERT INTO human_sessions(session_id,account_id,session_hash,status,created_at,expires_at,last_seen_at,revoked_at,device_id) VALUES(?,?,?,?,?,?,?,?,?)",
        (session_id, account_row["account_id"], token_hash(token), "active", created_at, expires_at, created_at, None, device_id),
    )
    conn.execute("UPDATE human_accounts SET last_login_at=?,updated_at=? WHERE account_id=?", (created_at, created_at, account_row["account_id"]))
    return {
        "provider": "agentops-human-auth",
        "authenticated": True,
        "user": account_public(account_row),
        "csrf_token": csrf_token(token),
        "session_expires_at": expires_at,
        "token_omitted": True,
        "device_ref": device_reference(device_id) if device_id else None,
    }, token, {"account_id": account_row["account_id"], "session_id": session_id}


def bootstrap_owner(conn, body) -> tuple[dict, int, str | None, dict]:
    if not required():
        return {"error": "human_auth_disabled", "message": "Human authentication is not enabled."}, 409, None, {"event": "bootstrap_blocked"}
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    if conn.execute("SELECT 1 FROM human_accounts LIMIT 1").fetchone():
        return {"error": "owner_already_initialized", "message": "The owner account is already initialized."}, 409, None, {"event": "bootstrap_blocked"}
    expected = os.environ.get("AGENTOPS_OWNER_SETUP_CODE", "").strip()
    supplied = str(body.get("setup_code") or "").strip()
    if not expected or not supplied or not hmac.compare_digest(supplied, expected):
        return {"error": "invalid_setup_code", "message": "The one-time owner setup code is invalid or unavailable."}, 401, None, {"event": "bootstrap_failed"}
    username = str(body.get("username") or "").strip().lower()
    password = str(body.get("password") or "")
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,63}", username):
        return {"error": "invalid_username", "message": "Username must be 3-64 lowercase letters, digits, dot, underscore, or dash."}, 400, None, {"event": "bootstrap_failed"}
    if len(password) < MIN_PASSWORD_LENGTH:
        return {"error": "weak_password", "message": f"Password must contain at least {MIN_PASSWORD_LENGTH} characters."}, 400, None, {"event": "bootstrap_failed"}
    salt = secrets.token_bytes(16)
    derived, params = password_hash(password, salt)
    account_id = new_id("husr")
    created_at = now_iso()
    display_name = str(body.get("display_name") or username).strip()[:80] or username
    conn.execute(
        "INSERT INTO human_accounts(account_id,workspace_id,username,display_name,role,password_hash,password_salt,password_params_json,status,created_at,updated_at,last_login_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (account_id, "local-demo", username, display_name, "owner", derived, salt.hex(), json.dumps(params, sort_keys=True), "active", created_at, created_at, None),
    )
    account = conn.execute("SELECT * FROM human_accounts WHERE account_id=?", (account_id,)).fetchone()
    payload, token, audit = create_session(conn, account)
    return payload, 201, token, {"event": "owner_bootstrapped", **audit}


def login(conn, body, headers=None) -> tuple[dict, int, str | None, dict]:
    username = str(body.get("username") or "").strip().lower()
    password = str(body.get("password") or "")
    username_hash = hashlib.sha256(username.encode("utf-8")).hexdigest()
    throttle = auth_throttle_check(conn, "login", username_hash)
    if throttle:
        return throttle, 429, None, {
            "event": "login_throttled",
            "username_hash": username_hash,
            "retry_after_seconds": throttle["retry_after_seconds"],
        }
    account = conn.execute("SELECT * FROM human_accounts WHERE username=?", (username,)).fetchone()
    if not account or account["status"] != "active" or not password_valid(password, account):
        throttle = auth_throttle_failure(conn, "login", username_hash)
        if throttle:
            return throttle, 429, None, {
                "event": "login_throttled",
                "username_hash": username_hash,
                "retry_after_seconds": throttle["retry_after_seconds"],
            }
        return {"error": "invalid_credentials", "message": "Username or password is invalid."}, 401, None, {"event": "login_failed", "username_hash": username_hash}
    paired_device_id = None
    supplied_device = device_token(headers)
    if supplied_device:
        device = conn.execute(
            "SELECT * FROM human_devices WHERE device_hash=? AND account_id=? AND status='active'",
            (token_hash(supplied_device), account["account_id"]),
        ).fetchone()
        if device and device["workspace_id"] == account["workspace_id"]:
            paired_device_id = device["device_id"]
    has_device_binding = bool(conn.execute(
        "SELECT 1 FROM human_devices WHERE account_id=? LIMIT 1",
        (account["account_id"],),
    ).fetchone())
    if has_device_binding and not paired_device_id:
        throttle = auth_throttle_failure(conn, "login", username_hash)
        if throttle:
            return throttle, 429, None, {
                "event": "login_throttled",
                "username_hash": username_hash,
                "retry_after_seconds": throttle["retry_after_seconds"],
            }
        return {"error": "invalid_credentials", "message": "Username or password is invalid."}, 401, None, {
            "event": "login_failed", "username_hash": username_hash
        }
    auth_throttle_clear_subject(conn, "login", username_hash)
    payload, token, audit = create_session(conn, account, device_id=paired_device_id)
    return payload, 200, token, {"event": "login_succeeded", **audit}


def start_password_recovery(conn, body) -> tuple[dict, int, dict]:
    if not required():
        return {"error": "human_auth_disabled", "message": "Human authentication is not enabled."}, 409, {"event": "password_recovery_blocked"}
    expected = os.environ.get("AGENTOPS_OWNER_SETUP_CODE", "").strip()
    supplied = str(body.get("setup_code") or "").strip()
    if not expected or not supplied or not hmac.compare_digest(supplied, expected):
        return {
            "error": "local_recovery_authority_required",
            "message": "Reopen the local Console from the AgentOps MIS application before resetting the password.",
        }, 403, {"event": "password_recovery_blocked"}
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    account = conn.execute(
        "SELECT * FROM human_accounts WHERE role='owner' AND status='active' ORDER BY created_at LIMIT 1"
    ).fetchone()
    if not account:
        return {"error": "owner_not_initialized", "message": "Create the administrator account first."}, 409, {"event": "password_recovery_blocked"}
    timestamp = now_iso()
    conn.execute(
        "UPDATE human_recovery_challenges SET status='expired' WHERE status='active'",
    )
    authority = secrets.token_urlsafe(32)
    challenge_id = new_id("hrec")
    expires_at = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=RECOVERY_TTL_SECONDS)).isoformat()
    conn.execute(
        "INSERT INTO human_recovery_challenges(challenge_id,account_id,challenge_hash,status,created_at,expires_at,used_at) VALUES(?,?,?,?,?,?,?)",
        (challenge_id, account["account_id"], token_hash(authority), "active", timestamp, expires_at, None),
    )
    return {
        "provider": "agentops-human-auth",
        "operation": "password_recovery_start",
        "recovery_authority": authority,
        "expires_at": expires_at,
        "local_host_only": True,
        "single_use": True,
        "recovery_authority_ephemeral": True,
        "token_omitted": True,
    }, 201, {
        "event": "password_recovery_started",
        "account_id": account["account_id"],
        "challenge_ref": session_reference(challenge_id),
    }


def complete_password_recovery(conn, body) -> tuple[dict, int, str | None, dict]:
    authority = str(body.get("recovery_authority") or "").strip()
    username = str(body.get("username") or "").strip().lower()
    password = str(body.get("password") or "")
    generic_error = {"error": "invalid_recovery_authority", "message": "The recovery request is invalid or expired."}
    if not authority or not username:
        return generic_error, 401, None, {"event": "password_recovery_failed"}
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    row = conn.execute(
        """
        SELECT c.*,a.username,a.status AS account_status
        FROM human_recovery_challenges c
        JOIN human_accounts a ON a.account_id=c.account_id
        WHERE c.challenge_hash=?
        """,
        (token_hash(authority),),
    ).fetchone()
    if not row or row["status"] != "active" or row["account_status"] != "active":
        return generic_error, 401, None, {"event": "password_recovery_failed"}
    timestamp = now_iso()
    if row["expires_at"] <= timestamp:
        conn.execute(
            "UPDATE human_recovery_challenges SET status='expired' WHERE challenge_id=? AND status='active'",
            (row["challenge_id"],),
        )
        return generic_error, 401, None, {"event": "password_recovery_failed", "challenge_ref": session_reference(row["challenge_id"])}
    if not hmac.compare_digest(username, row["username"]):
        return generic_error, 401, None, {"event": "password_recovery_failed", "challenge_ref": session_reference(row["challenge_id"])}
    if len(password) < MIN_PASSWORD_LENGTH:
        return {"error": "weak_password", "message": f"Password must contain at least {MIN_PASSWORD_LENGTH} characters."}, 400, None, {
            "event": "password_recovery_failed",
            "account_id": row["account_id"],
            "challenge_ref": session_reference(row["challenge_id"]),
        }
    consumed = conn.execute(
        "UPDATE human_recovery_challenges SET status='used',used_at=? WHERE challenge_id=? AND status='active'",
        (timestamp, row["challenge_id"]),
    ).rowcount
    if consumed != 1:
        return generic_error, 401, None, {
            "event": "password_recovery_failed",
            "challenge_ref": session_reference(row["challenge_id"]),
        }
    salt = secrets.token_bytes(16)
    derived, params = password_hash(password, salt)
    conn.execute(
        "UPDATE human_accounts SET password_hash=?,password_salt=?,password_params_json=?,updated_at=? WHERE account_id=?",
        (derived, salt.hex(), json.dumps(params, sort_keys=True), timestamp, row["account_id"]),
    )
    revoked = conn.execute(
        "UPDATE human_sessions SET status='revoked',revoked_at=? WHERE account_id=? AND status='active'",
        (timestamp, row["account_id"]),
    ).rowcount
    account = conn.execute("SELECT * FROM human_accounts WHERE account_id=?", (row["account_id"],)).fetchone()
    payload, token, session_audit = create_session(conn, account)
    payload.update({
        "operation": "password_recovery_complete",
        "previous_sessions_revoked": max(0, int(revoked or 0)),
        "recovery_authority_omitted": True,
    })
    return payload, 200, token, {
        "event": "password_recovery_completed",
        "account_id": row["account_id"],
        "challenge_ref": session_reference(row["challenge_id"]),
        "revoked_count": max(0, int(revoked or 0)),
        **session_audit,
    }


def logout(conn, headers) -> tuple[dict, int, dict]:
    invalid_origin = origin_error(headers)
    if invalid_origin:
        payload, status = invalid_origin
        return payload, status, {"event": "logout_failed"}
    context, error = auth_context(conn, headers, touch=False)
    if error:
        return error, 401, {"event": "logout_failed"}
    supplied = (headers.get("X-AgentOps-CSRF") or "").strip()
    if not supplied or not hmac.compare_digest(supplied, csrf_token(context["session_token"])):
        return {"error": "csrf_validation_failed", "message": "A valid X-AgentOps-CSRF token is required."}, 403, {"event": "logout_failed", "account_id": context["account_id"]}
    timestamp = now_iso()
    conn.execute("UPDATE human_sessions SET status='revoked',revoked_at=? WHERE session_id=?", (timestamp, context["session_id"]))
    return {"provider": "agentops-human-auth", "authenticated": False, "token_omitted": True}, 200, {
        "event": "logout",
        "account_id": context["account_id"],
        "session_id": context["session_id"],
    }


def _expire_account_sessions(conn, account_id: str) -> int:
    timestamp = now_iso()
    cursor = conn.execute(
        """
        UPDATE human_sessions
        SET status='expired',revoked_at=?
        WHERE account_id=? AND status='active' AND expires_at<=?
        """,
        (timestamp, account_id, timestamp),
    )
    return max(0, int(cursor.rowcount or 0))


def list_sessions(conn, context) -> tuple[dict, int]:
    if not context:
        return {"error": "human_auth_required", "message": "A human browser session is required."}, 401
    _expire_account_sessions(conn, context["account_id"])
    rows = conn.execute(
        """
        SELECT session_id,status,created_at,expires_at,last_seen_at,revoked_at,device_id
        FROM human_sessions
        WHERE account_id=?
        ORDER BY created_at DESC
        LIMIT 50
        """,
        (context["account_id"],),
    ).fetchall()
    sessions = [
        {
            "session_ref": session_reference(row["session_id"]),
            "status": row["status"],
            "current": hmac.compare_digest(row["session_id"], context["session_id"]),
            "created_at": row["created_at"],
            "expires_at": row["expires_at"],
            "last_seen_at": row["last_seen_at"],
            "revoked_at": row["revoked_at"],
            "device_ref": device_reference(row["device_id"]) if row["device_id"] else None,
        }
        for row in rows
    ]
    return {
        "provider": "agentops-human-auth",
        "operation": "human_sessions_list",
        "sessions": sessions,
        "current_session_ref": session_reference(context["session_id"]),
        "active_count": sum(1 for row in sessions if row["status"] == "active"),
        "session_count": len(sessions),
        "session_id_omitted": True,
        "session_hash_omitted": True,
        "token_omitted": True,
    }, 200


def revoke_sessions(conn, context, body) -> tuple[dict, int, dict]:
    if not context:
        payload = {"error": "human_auth_required", "message": "A human browser session is required."}
        return payload, 401, {"event": "sessions_revoke_failed", "target_ref": "unavailable"}
    body = body if isinstance(body, dict) else {}
    unexpected = sorted(set(body) - {"session_ref", "all_other"})
    requested_ref = str(body.get("session_ref") or "").strip()
    all_other = body.get("all_other") is True
    has_session_ref = "session_ref" in body
    has_all_other = "all_other" in body
    invalid_operation = (
        has_session_ref == has_all_other
        or (has_session_ref and not requested_ref)
        or (has_all_other and not all_other)
    )
    if unexpected or invalid_operation:
        payload = {
            "error": "invalid_session_revoke_request",
            "message": "Provide exactly one of session_ref or all_other=true.",
            "unexpected_fields": unexpected,
        }
        return payload, 400, {"event": "sessions_revoke_failed", "target_ref": requested_ref or "all_other"}

    _expire_account_sessions(conn, context["account_id"])
    current_ref = session_reference(context["session_id"])
    timestamp = now_iso()
    revoked_refs: list[str] = []

    if all_other:
        rows = conn.execute(
            """
            SELECT session_id FROM human_sessions
            WHERE account_id=? AND status='active' AND session_id<>?
            """,
            (context["account_id"], context["session_id"]),
        ).fetchall()
        revoked_refs = [session_reference(row["session_id"]) for row in rows]
        if rows:
            conn.execute(
                """
                UPDATE human_sessions
                SET status='revoked',revoked_at=?
                WHERE account_id=? AND status='active' AND session_id<>?
                """,
                (timestamp, context["account_id"], context["session_id"]),
            )
        target_ref = "all_other"
    else:
        target = None
        rows = conn.execute(
            """
            SELECT session_id,status FROM human_sessions
            WHERE account_id=?
            """,
            (context["account_id"],),
        ).fetchall()
        for row in rows:
            if hmac.compare_digest(session_reference(row["session_id"]), requested_ref):
                target = row
                break
        if target is None:
            payload = {"error": "human_session_not_found", "message": "The browser session was not found."}
            return payload, 404, {"event": "sessions_revoke_failed", "target_ref": requested_ref}
        if hmac.compare_digest(target["session_id"], context["session_id"]):
            payload = {
                "error": "current_session_requires_logout",
                "message": "Use sign out to revoke the current browser session.",
                "current_session_ref": current_ref,
            }
            return payload, 409, {"event": "sessions_revoke_blocked", "target_ref": requested_ref}
        target_ref = requested_ref
        if target["status"] == "active":
            conn.execute(
                """
                UPDATE human_sessions
                SET status='revoked',revoked_at=?
                WHERE account_id=? AND session_id=? AND status='active'
                """,
                (timestamp, context["account_id"], target["session_id"]),
            )
            revoked_refs = [requested_ref]

    return {
        "provider": "agentops-human-auth",
        "operation": "human_sessions_revoke",
        "status": "revoked" if revoked_refs else "no_active_session_changed",
        "revoked_count": len(revoked_refs),
        "revoked_session_refs": revoked_refs,
        "current_session_ref": current_ref,
        "current_session_preserved": True,
        "session_id_omitted": True,
        "session_hash_omitted": True,
        "token_omitted": True,
    }, 200, {
        "event": "sessions_revoked",
        "target_ref": target_ref,
        "revoked_session_refs": revoked_refs,
    }


def _row_by_safe_ref(conn, table: str, id_column: str, workspace_id: str, reference: str, ref_fn):
    rows = conn.execute(
        f"SELECT * FROM {table} WHERE workspace_id=? ORDER BY created_at DESC LIMIT 200",
        (workspace_id,),
    ).fetchall()
    for row in rows:
        if hmac.compare_digest(ref_fn(row[id_column]), reference):
            return row
    return None


def _expire_pairing_invitations(conn, workspace_id: str) -> int:
    timestamp = now_iso()
    cursor = conn.execute(
        """
        UPDATE human_pairing_invitations
        SET status='expired'
        WHERE workspace_id=? AND status='active' AND expires_at<=?
        """,
        (workspace_id, timestamp),
    )
    return max(0, int(cursor.rowcount or 0))


def create_pairing_invitation(conn, context, body) -> tuple[dict, int, dict]:
    if not context or context.get("role") != "owner":
        return {"error": "human_role_forbidden", "message": "Only an Owner can create a pairing invitation."}, 403, {
            "event": "pairing_invitation_create_failed", "invitation_ref": "unavailable"
        }
    body = body if isinstance(body, dict) else {}
    role = str(body.get("role") or "operator").strip().lower()
    if role not in {"viewer", "operator", "approver"}:
        return {"error": "invalid_pairing_role", "message": "Pairing role must be viewer, operator, or approver."}, 400, {
            "event": "pairing_invitation_create_failed", "invitation_ref": "unavailable"
        }
    try:
        ttl = int(body.get("expires_in_seconds") or PAIRING_TTL_SECONDS)
    except (TypeError, ValueError):
        ttl = 0
    if ttl < 120 or ttl > 3600:
        return {"error": "invalid_pairing_expiry", "message": "Pairing expiry must be between 120 and 3600 seconds."}, 400, {
            "event": "pairing_invitation_create_failed", "invitation_ref": "unavailable"
        }
    label = str(body.get("label") or "Remote Console").strip()[:80] or "Remote Console"
    secret = secrets.token_urlsafe(24)
    invitation_id = new_id("hinv")
    created_at = now_iso()
    expires_at = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=ttl)).isoformat()
    conn.execute(
        """
        INSERT INTO human_pairing_invitations(
            invitation_id,workspace_id,created_by_account_id,secret_hash,role,label,status,
            attempt_count,max_attempts,created_at,expires_at,redeemed_at,revoked_at,
            redeemed_account_id,redeemed_device_id
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            invitation_id, context["workspace_id"], context["account_id"], token_hash(secret),
            role, label, "active", 0, PAIRING_MAX_ATTEMPTS, created_at, expires_at,
            None, None, None, None,
        ),
    )
    ref = invitation_reference(invitation_id)
    return {
        "provider": "agentops-human-auth",
        "operation": "human_pairing_invitation_create",
        "invitation_ref": ref,
        "pairing_secret": secret,
        "pairing_secret_ephemeral": True,
        "pairing_secret_omitted": False,
        "role": role,
        "label": label,
        "expires_at": expires_at,
        "single_use": True,
        "max_attempts": PAIRING_MAX_ATTEMPTS,
        "owner_role_forbidden": True,
        "token_omitted": True,
    }, 201, {
        "event": "pairing_invitation_created",
        "invitation_ref": ref,
        "role": role,
        "expires_at": expires_at,
    }


def list_pairing_invitations(conn, context) -> tuple[dict, int]:
    if not context or context.get("role") != "owner":
        return {"error": "human_role_forbidden", "message": "Only an Owner can list pairing invitations."}, 403
    _expire_pairing_invitations(conn, context["workspace_id"])
    rows = conn.execute(
        """
        SELECT invitation_id,role,label,status,attempt_count,max_attempts,created_at,expires_at,redeemed_at,revoked_at
        FROM human_pairing_invitations
        WHERE workspace_id=?
        ORDER BY created_at DESC LIMIT 50
        """,
        (context["workspace_id"],),
    ).fetchall()
    return {
        "provider": "agentops-human-auth",
        "operation": "human_pairing_invitations_list",
        "invitations": [
            {
                "invitation_ref": invitation_reference(row["invitation_id"]),
                "role": row["role"],
                "label": row["label"],
                "status": row["status"],
                "attempt_count": row["attempt_count"],
                "max_attempts": row["max_attempts"],
                "created_at": row["created_at"],
                "expires_at": row["expires_at"],
                "redeemed_at": row["redeemed_at"],
                "revoked_at": row["revoked_at"],
            }
            for row in rows
        ],
        "pairing_secret_omitted": True,
        "token_omitted": True,
    }, 200


def revoke_pairing_invitation(conn, context, invitation_ref: str) -> tuple[dict, int, dict]:
    if not context or context.get("role") != "owner":
        return {"error": "human_role_forbidden", "message": "Only an Owner can revoke pairing invitations."}, 403, {
            "event": "pairing_invitation_revoke_failed", "invitation_ref": invitation_ref
        }
    row = _row_by_safe_ref(
        conn, "human_pairing_invitations", "invitation_id", context["workspace_id"], invitation_ref, invitation_reference
    )
    if not row:
        return {"error": "pairing_invitation_not_found", "message": "The pairing invitation was not found."}, 404, {
            "event": "pairing_invitation_revoke_failed", "invitation_ref": invitation_ref
        }
    changed = 0
    if row["status"] == "active":
        changed = conn.execute(
            "UPDATE human_pairing_invitations SET status='revoked',revoked_at=? WHERE invitation_id=? AND status='active'",
            (now_iso(), row["invitation_id"]),
        ).rowcount
    return {
        "provider": "agentops-human-auth",
        "operation": "human_pairing_invitation_revoke",
        "invitation_ref": invitation_ref,
        "status": "revoked" if changed else row["status"],
        "pairing_secret_omitted": True,
        "token_omitted": True,
    }, 200, {"event": "pairing_invitation_revoked", "invitation_ref": invitation_ref}


def redeem_pairing_invitation(conn, body) -> tuple[dict, int, str | None, str | None, dict]:
    body = body if isinstance(body, dict) else {}
    secret = str(body.get("pairing_secret") or "").strip()
    secret_fingerprint = token_hash(secret)
    generic_error = {"error": "invalid_pairing_invitation", "message": "The pairing invitation is invalid or expired."}

    def failed(payload: dict, status: int, event: dict):
        throttle = auth_throttle_failure(conn, "pairing", secret_fingerprint)
        if throttle:
            return throttle, 429, None, None, {
                "event": "pairing_throttled",
                "invitation_ref": event.get("invitation_ref") or "unavailable",
                "retry_after_seconds": throttle["retry_after_seconds"],
            }
        return payload, status, None, None, event

    throttle = auth_throttle_check(conn, "pairing", secret_fingerprint)
    if throttle:
        return throttle, 429, None, None, {
            "event": "pairing_throttled",
            "invitation_ref": "unavailable",
            "retry_after_seconds": throttle["retry_after_seconds"],
        }
    if not secret:
        return failed(generic_error, 401, {"event": "pairing_failed", "invitation_ref": "unavailable"})
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    row = conn.execute(
        "SELECT * FROM human_pairing_invitations WHERE secret_hash=?",
        (token_hash(secret),),
    ).fetchone()
    if not row:
        return failed(generic_error, 401, {"event": "pairing_failed", "invitation_ref": "unavailable"})
    ref = invitation_reference(row["invitation_id"])
    timestamp = now_iso()
    if row["status"] != "active":
        return failed(generic_error, 401, {"event": "pairing_failed", "invitation_ref": ref})
    if row["expires_at"] <= timestamp:
        conn.execute("UPDATE human_pairing_invitations SET status='expired' WHERE invitation_id=?", (row["invitation_id"],))
        return failed(generic_error, 401, {"event": "pairing_failed", "invitation_ref": ref})
    attempt_count = int(row["attempt_count"] or 0) + 1
    if attempt_count > int(row["max_attempts"] or PAIRING_MAX_ATTEMPTS):
        conn.execute(
            "UPDATE human_pairing_invitations SET status='locked',attempt_count=? WHERE invitation_id=?",
            (attempt_count, row["invitation_id"]),
        )
        return failed(generic_error, 401, {"event": "pairing_failed", "invitation_ref": ref})
    conn.execute(
        "UPDATE human_pairing_invitations SET attempt_count=? WHERE invitation_id=?",
        (attempt_count, row["invitation_id"]),
    )

    username = str(body.get("username") or "").strip().lower()
    password = str(body.get("password") or "")
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,63}", username):
        return failed({"error": "invalid_pairing_profile", "message": "The pairing profile is invalid."}, 400, {
            "event": "pairing_failed", "invitation_ref": ref
        })
    if len(password) < MIN_PASSWORD_LENGTH:
        return failed({"error": "weak_password", "message": f"Password must contain at least {MIN_PASSWORD_LENGTH} characters."}, 400, {
            "event": "pairing_failed", "invitation_ref": ref
        })
    if conn.execute("SELECT 1 FROM human_accounts WHERE username=?", (username,)).fetchone():
        return failed({"error": "invalid_pairing_profile", "message": "The pairing profile is invalid."}, 409, {
            "event": "pairing_failed", "invitation_ref": ref
        })

    salt = secrets.token_bytes(16)
    derived, params = password_hash(password, salt)
    account_id = new_id("husr")
    display_name = str(body.get("display_name") or username).strip()[:80] or username
    conn.execute(
        """
        INSERT INTO human_accounts(
            account_id,workspace_id,username,display_name,role,password_hash,password_salt,
            password_params_json,status,created_at,updated_at,last_login_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            account_id, row["workspace_id"], username, display_name, row["role"], derived,
            salt.hex(), json.dumps(params, sort_keys=True), "active", timestamp, timestamp, None,
        ),
    )
    device_secret = secrets.token_urlsafe(32)
    device_id = new_id("hdev")
    device_label = str(body.get("device_label") or row["label"] or "Remote Console").strip()[:80] or "Remote Console"
    conn.execute(
        """
        INSERT INTO human_devices(device_id,workspace_id,account_id,device_hash,label,status,created_at,last_seen_at,revoked_at)
        VALUES(?,?,?,?,?,?,?,?,?)
        """,
        (device_id, row["workspace_id"], account_id, token_hash(device_secret), device_label, "active", timestamp, timestamp, None),
    )
    consumed = conn.execute(
        """
        UPDATE human_pairing_invitations
        SET status='redeemed',redeemed_at=?,redeemed_account_id=?,redeemed_device_id=?
        WHERE invitation_id=? AND status='active'
        """,
        (timestamp, account_id, device_id, row["invitation_id"]),
    ).rowcount
    if consumed != 1:
        return failed(generic_error, 401, {"event": "pairing_failed", "invitation_ref": ref})
    account = conn.execute("SELECT * FROM human_accounts WHERE account_id=?", (account_id,)).fetchone()
    auth_throttle_clear_subject(conn, "pairing", secret_fingerprint)
    payload, session_secret, session_audit = create_session(conn, account, device_id=device_id)
    payload.update({
        "operation": "human_pairing_complete",
        "invitation_ref": ref,
        "device_ref": device_reference(device_id),
        "pairing_secret_omitted": True,
        "device_secret_omitted": True,
        "owner_role_forbidden": True,
    })
    return payload, 201, session_secret, device_secret, {
        "event": "pairing_completed",
        "invitation_ref": ref,
        "account_id": account_id,
        "device_ref": device_reference(device_id),
        **session_audit,
    }


def list_devices(conn, context) -> tuple[dict, int]:
    if not context or context.get("role") != "owner":
        return {"error": "human_role_forbidden", "message": "Only an Owner can list paired devices."}, 403
    rows = conn.execute(
        """
        SELECT d.device_id,d.label,d.status,d.created_at,d.last_seen_at,d.revoked_at,
               a.display_name,a.role
        FROM human_devices d
        JOIN human_accounts a ON a.account_id=d.account_id
        WHERE d.workspace_id=?
        ORDER BY d.created_at DESC LIMIT 100
        """,
        (context["workspace_id"],),
    ).fetchall()
    return {
        "provider": "agentops-human-auth",
        "operation": "human_devices_list",
        "devices": [
            {
                "device_ref": device_reference(row["device_id"]),
                "label": row["label"],
                "status": row["status"],
                "display_name": row["display_name"],
                "role": row["role"],
                "created_at": row["created_at"],
                "last_seen_at": row["last_seen_at"],
                "revoked_at": row["revoked_at"],
            }
            for row in rows
        ],
        "device_id_omitted": True,
        "device_secret_omitted": True,
        "token_omitted": True,
    }, 200


def revoke_device(conn, context, device_ref: str) -> tuple[dict, int, dict]:
    if not context or context.get("role") != "owner":
        return {"error": "human_role_forbidden", "message": "Only an Owner can revoke paired devices."}, 403, {
            "event": "device_revoke_failed", "device_ref": device_ref
        }
    row = _row_by_safe_ref(conn, "human_devices", "device_id", context["workspace_id"], device_ref, device_reference)
    if not row:
        return {"error": "human_device_not_found", "message": "The paired device was not found."}, 404, {
            "event": "device_revoke_failed", "device_ref": device_ref
        }
    timestamp = now_iso()
    device_changed = conn.execute(
        "UPDATE human_devices SET status='revoked',revoked_at=? WHERE device_id=? AND status='active'",
        (timestamp, row["device_id"]),
    ).rowcount
    sessions = conn.execute(
        "UPDATE human_sessions SET status='revoked',revoked_at=? WHERE device_id=? AND status='active'",
        (timestamp, row["device_id"]),
    ).rowcount
    return {
        "provider": "agentops-human-auth",
        "operation": "human_device_revoke",
        "device_ref": device_ref,
        "status": "revoked" if device_changed else row["status"],
        "revoked_session_count": max(0, int(sessions or 0)),
        "device_secret_omitted": True,
        "session_id_omitted": True,
        "token_omitted": True,
    }, 200, {
        "event": "device_revoked",
        "device_ref": device_ref,
        "revoked_session_count": max(0, int(sessions or 0)),
    }
