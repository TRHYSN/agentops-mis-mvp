#!/usr/bin/env python3
"""Test-only orchestration for a real Worker-to-Human-review loop.

Commercial authority stays in the production Next.js/TypeScript/Postgres
runtime; this harness never starts the Python API.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import secrets
import signal
import shutil
import socket
import stat
import subprocess
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_SOURCE_ROOT = Path(__file__).resolve().parents[1]
ROOT = DEFAULT_SOURCE_ROOT
NEXT_APP = ROOT / "ui" / "next-app"
CONTRACT_ID = "nextjs_postgres_real_worker_human_review_v4"
WORKSPACE_ID = "ws_real_worker_human_review"
OTHER_WORKSPACE_ID = "ws_real_worker_human_review_other"
REQUESTER_ID = "usr_founder"
OWNER_USERNAME = "real-worker-owner"
RUN_ESTIMATED_COST_USD = "1.000000"
SCOPES = [
    "agents:write",
    "agents:heartbeat",
    "tasks:read",
    "tasks:claim",
    "agent_plans:read",
    "agent_plans:write",
    "knowledge:read",
    "knowledge:write",
    "runs:write",
    "toolcalls:write",
    "evaluations:submit",
    "artifacts:write",
    "memories:propose",
    "approvals:request",
    "audit:write",
    "plan_evidence:write",
    "runtime_events:write",
]

NODE_PG_HELPER = r"""
const fs = require('node:fs');
const { Client } = require('./ui/next-app/node_modules/pg');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));

function translateQmarks(sql) {
  let output = '';
  let inSingle = false;
  let parameter = 0;
  for (let index = 0; index < sql.length; index += 1) {
    const char = sql[index];
    if (char === "'") {
      output += char;
      if (inSingle && sql[index + 1] === "'") {
        output += sql[index + 1];
        index += 1;
      } else {
        inSingle = !inSingle;
      }
    } else if (char === '?' && !inSingle) {
      parameter += 1;
      output += `$${parameter}`;
    } else {
      output += char;
    }
  }
  return output;
}

(async () => {
  const client = new Client({
    connectionString: process.env.AGENTOPS_NODE_PG_DSN,
    application_name: 'agentops-real-worker-human-review-smoke',
  });
  try {
    await client.connect();
    const query = input.script ? input.sql : translateQmarks(input.sql);
    const response = await client.query(query, input.params || []);
    process.stdout.write(JSON.stringify({ rows: response.rows || [], row_count: response.rowCount || 0 }));
  } finally {
    await client.end().catch(() => undefined);
  }
})().catch((error) => {
  process.stderr.write(JSON.stringify({
    message: String(error && error.message ? error.message : error),
    code: error && typeof error.code === 'string' ? error.code : null,
    name: error && typeof error.name === 'string' ? error.name : 'Error',
  }));
  process.exitCode = 1;
});
"""


class NodePgError(RuntimeError):
    def __init__(self, message: str, sqlstate: str | None):
        super().__init__(message)
        self.sqlstate = sqlstate


class NodePgAdapter:
    """Test-only structured Postgres client using the Next runtime's pinned pg."""

    def __init__(self, dsn: str, node_binary: str):
        self.dsn = dsn
        self.node_binary = node_binary

    def _request(self, sql: str, params: tuple[Any, ...] = (), *, script: bool = False) -> dict[str, Any]:
        env = environment_without_privileged_control_plane_credentials()
        env["AGENTOPS_NODE_PG_DSN"] = self.dsn
        completed = subprocess.run(
            [self.node_binary, "-e", NODE_PG_HELPER],
            cwd=ROOT,
            env=env,
            input=json.dumps({"sql": sql, "params": list(params), "script": script}, ensure_ascii=False),
            text=True,
            capture_output=True,
            timeout=60,
            check=False,
        )
        if completed.returncode != 0:
            message = completed.stderr[-1000:]
            sqlstate = None
            try:
                failure = json.loads(completed.stderr or "{}")
                if isinstance(failure, dict):
                    message = str(failure.get("message") or message)
                    code = failure.get("code")
                    sqlstate = str(code) if code else None
            except json.JSONDecodeError:
                pass
            raise NodePgError(
                f"Node Postgres query failed: {message}",
                sqlstate,
            )
        return json.loads(completed.stdout or "{}")

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any]:
        return self._request(sql, params)

    def executescript(self, sql: str) -> None:
        self._request(sql, script=True)

    def fetchone(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        rows = self._request(sql, params).get("rows") or []
        return rows[0] if rows else None

    def fetchall(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        return list(self._request(sql, params).get("rows") or [])

    def commit(self) -> None:
        return None

    def close(self) -> None:
        return None


def redact(value: object, sensitive: list[str]) -> str:
    output = str(value)
    for secret in sorted((item for item in sensitive if item), key=len, reverse=True):
        output = output.replace(secret, "[REDACTED]")
    return output


def result(payload: dict[str, Any], sensitive: list[str]) -> None:
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str)
    print(redact(rendered, sensitive))


def dsn_with_search_path(dsn: str, schema: str) -> str:
    parsed = urllib.parse.urlsplit(dsn)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise ValueError("--postgres-dsn must be a postgres URL")
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    existing = [value for key, value in query if key == "options"]
    query = [(key, value) for key, value in query if key != "options"]
    query.append(("options", " ".join([*existing, f"-c search_path={schema}"]).strip()))
    return urllib.parse.urlunsplit((
        parsed.scheme,
        parsed.netloc,
        parsed.path,
        urllib.parse.urlencode(query, quote_via=urllib.parse.quote),
        parsed.fragment,
    ))


def dsn_with_credentials(dsn: str, username: str, password: str) -> str:
    parsed = urllib.parse.urlsplit(dsn)
    if parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname:
        raise ValueError("--postgres-dsn must be a postgres URL with a host")
    host = parsed.hostname
    if ":" in host:
        host = f"[{host}]"
    netloc = (
        f"{urllib.parse.quote(username, safe='')}:"
        f"{urllib.parse.quote(password, safe='')}@{host}"
    )
    if parsed.port is not None:
        netloc += f":{parsed.port}"
    return urllib.parse.urlunsplit((
        parsed.scheme,
        netloc,
        parsed.path,
        parsed.query,
        parsed.fragment,
    ))


def dsn_with_search_path_sequence(dsn: str, schemas: tuple[str, ...]) -> str:
    if not schemas or any(
        not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", schema)
        for schema in schemas
    ):
        raise ValueError("postgres_search_path_identifier_invalid")
    return dsn_with_search_path(dsn, ",".join(schemas))


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _hash_field(digest: Any, value: bytes) -> None:
    digest.update(len(value).to_bytes(8, "big"))
    digest.update(value)


def stable_tree_sha256(root: Path) -> str:
    """Hash tree paths, entry types, and contents without filesystem metadata."""
    if root.is_symlink() or not root.is_dir():
        raise RuntimeError("next_build_artifact_missing_or_unsafe")
    entries = sorted(root.rglob("*"), key=lambda item: os.fsencode(item.relative_to(root).as_posix()))
    if not entries:
        raise RuntimeError("next_build_artifact_empty")
    digest = hashlib.sha256()
    _hash_field(digest, b"agentops-stable-tree-sha256-v1")
    for path in entries:
        relative = os.fsencode(path.relative_to(root).as_posix())
        metadata = path.lstat()
        _hash_field(digest, relative)
        if stat.S_ISDIR(metadata.st_mode):
            _hash_field(digest, b"directory")
        elif stat.S_ISLNK(metadata.st_mode):
            _hash_field(digest, b"symlink")
            _hash_field(digest, os.fsencode(os.readlink(path)))
        elif stat.S_ISREG(metadata.st_mode):
            _hash_field(digest, b"file")
            _hash_field(digest, bytes.fromhex(file_sha256(path)))
        else:
            raise RuntimeError("next_build_artifact_contains_special_file")
    return digest.hexdigest()


def tracked_worktree_fingerprint(source_root: Path) -> str:
    """Hash the live contents of every Git-indexed path in a dirty-safe way."""
    git = shutil.which("git")
    if not git:
        raise RuntimeError("git_binary_unavailable")
    listed = subprocess.run(
        [git, "-C", str(source_root), "ls-files", "--stage", "-z"],
        text=False,
        capture_output=True,
        timeout=30,
        check=False,
    )
    if listed.returncode != 0:
        raise RuntimeError("tracked_worktree_inventory_failed")
    records = sorted(record for record in listed.stdout.split(b"\0") if record)
    digest = hashlib.sha256()
    _hash_field(digest, b"agentops-tracked-worktree-sha256-v1")
    for record in records:
        index_entry, separator, relative = record.partition(b"\t")
        if not separator or not relative:
            raise RuntimeError("tracked_worktree_inventory_invalid")
        path = source_root / os.fsdecode(relative)
        _hash_field(digest, index_entry)
        _hash_field(digest, relative)
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            _hash_field(digest, b"missing")
            continue
        if stat.S_ISLNK(metadata.st_mode):
            _hash_field(digest, b"symlink")
            _hash_field(digest, os.fsencode(os.readlink(path)))
        elif stat.S_ISREG(metadata.st_mode):
            _hash_field(digest, b"file")
            _hash_field(digest, str(stat.S_IMODE(metadata.st_mode) & 0o111).encode("ascii"))
            _hash_field(digest, bytes.fromhex(file_sha256(path)))
        elif stat.S_ISDIR(metadata.st_mode):
            _hash_field(digest, b"directory")
        else:
            _hash_field(digest, b"special")
    return digest.hexdigest()


def git_source_state(source_root: Path) -> tuple[str, bool]:
    git = shutil.which("git")
    if not git:
        raise RuntimeError("git_binary_unavailable")
    commit = subprocess.run(
        [git, "-C", str(source_root), "rev-parse", "--verify", "HEAD^{commit}"],
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    source_commit = commit.stdout.strip().lower()
    if commit.returncode != 0 or not re.fullmatch(r"[a-f0-9]{40}", source_commit):
        raise RuntimeError("source_commit_unavailable")
    status = subprocess.run(
        [
            git,
            "-C",
            str(source_root),
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "-z",
        ],
        text=False,
        capture_output=True,
        timeout=30,
        check=False,
    )
    if status.returncode != 0:
        raise RuntimeError("source_worktree_status_unavailable")
    return source_commit, status.stdout == b""


def resolve_source_root(value: str) -> Path:
    try:
        source_root = Path(value).expanduser().resolve(strict=True)
    except OSError as exc:
        raise RuntimeError("source_root_unavailable") from exc
    required = [
        source_root / "migrations" / "postgres" / "20260724_current_main_commercial_baseline.sql",
        source_root / "migrations" / "postgres" / "20260731_cost_reservations_v10.sql",
        source_root / "ui" / "next-app" / "scripts" / "bootstrap-owner.ts",
        source_root / "ui" / "next-app" / "scripts" / "commercial-worker.ts",
        source_root / "ui" / "next-app" / "src" / "server" / "controlPlane" / "runCostReservations.ts",
        source_root / "ui" / "next-app" / "package.json",
    ]
    if not source_root.is_dir() or not all(path.is_file() for path in required):
        raise RuntimeError("source_root_contract_files_missing")
    git = shutil.which("git")
    if not git:
        raise RuntimeError("git_binary_unavailable")
    top_level = subprocess.run(
        [git, "-C", str(source_root), "rev-parse", "--show-toplevel"],
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    if top_level.returncode != 0 or Path(top_level.stdout.strip()).resolve() != source_root:
        raise RuntimeError("source_root_must_be_git_worktree_root")
    return source_root


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def stop_process(
    proc: subprocess.Popen[str],
    *,
    timeout: int = 5,
) -> dict[str, Any]:
    errors: list[str] = []

    def exited() -> bool:
        try:
            return proc.poll() is not None
        except Exception as exc:
            errors.append(f"poll:{exc.__class__.__name__}")
            return False

    if exited():
        return {"stopped": True, "errors": errors}
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    except Exception as exc:
        errors.append(f"sigterm:{exc.__class__.__name__}")
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        pass
    except Exception as exc:
        errors.append(f"wait_after_sigterm:{exc.__class__.__name__}")
    if not exited():
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except Exception as exc:
            errors.append(f"sigkill:{exc.__class__.__name__}")
    if not exited():
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            errors.append("wait:TimeoutExpired")
        except Exception as exc:
            errors.append(f"wait:{exc.__class__.__name__}")
    return {
        "stopped": exited(),
        "errors": errors,
    }


def assert_harness_safety_helper_contracts() -> None:
    assert_subprocess_environment_scrub()

    class FailingProcess:
        pid = 987654321

        def poll(self) -> None:
            return None

        def wait(self, timeout: int) -> None:
            del timeout
            raise OSError("bounded stop helper contract")

    original_killpg = os.killpg
    try:
        def fail_killpg(pid: int, sig: signal.Signals) -> None:
            del pid, sig
            raise PermissionError("bounded stop helper contract")

        os.killpg = fail_killpg
        receipt = stop_process(FailingProcess(), timeout=0)  # type: ignore[arg-type]
    finally:
        os.killpg = original_killpg
    if (
        receipt.get("stopped") is not False
        or not receipt.get("errors")
        or not any(
            str(item).startswith("sigterm:PermissionError")
            for item in receipt["errors"]
        )
    ):
        raise RuntimeError("stop_process_failure_bounding_contract_failed")


def http_json(
    method: str,
    url: str,
    body: dict[str, Any] | None = None,
    *,
    headers: dict[str, str] | None = None,
    timeout: int = 30,
) -> tuple[int, Any, dict[str, str]]:
    request_headers = dict(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")
    request = urllib.request.Request(url, data=data, headers=request_headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            response_headers = {key.lower(): value for key, value in response.headers.items()}
            return int(response.status), json.loads(raw) if raw else {}, response_headers
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {"raw_omitted": True}
        response_headers = {key.lower(): value for key, value in exc.headers.items()}
        return int(exc.code), payload, response_headers


def raw_cookie(headers: dict[str, str]) -> str:
    prefix = "agentops_human_session="
    for part in headers.get("set-cookie", "").split(";"):
        item = part.strip()
        if item.startswith(prefix):
            return item[len(prefix):]
    return ""


def wait_for_next(base_url: str, proc: subprocess.Popen[str], sensitive: list[str]) -> None:
    deadline = time.time() + 90
    last = ""
    while time.time() < deadline:
        if proc.poll() is not None:
            stdout, stderr = proc.communicate(timeout=2)
            raise RuntimeError(
                redact(
                    f"Next exited early (code={proc.returncode}): {(stdout or '')[-800:]} {(stderr or '')[-800:]}",
                    sensitive,
                )
            )
        try:
            status, payload, _ = http_json(
                "GET",
                f"{base_url}/api/agent-gateway/tasks/pull?workspace_id={WORKSPACE_ID}&limit=1&status=planned",
            )
            if status == 401 and isinstance(payload, dict) and payload.get("error") == "unauthorized":
                return
            last = f"{status}:{payload}"
        except Exception as exc:  # pragma: no cover - diagnostics only
            last = str(exc)
        time.sleep(0.25)
    raise RuntimeError(redact(f"Next Agent Gateway alias did not become ready: {last}", sensitive))


def run_next_build(npm: str) -> subprocess.CompletedProcess[str]:
    safe_keys = ("HOME", "LANG", "LC_ALL", "PATH", "SHELL", "TMPDIR", "TMP", "TEMP")
    env = {key: os.environ[key] for key in safe_keys if os.environ.get(key)}
    env.update({
        "CI": "1",
        "NEXT_TELEMETRY_DISABLED": "1",
    })
    return subprocess.run(
        [npm, "run", "--silent", "build"],
        cwd=NEXT_APP,
        env=env,
        text=True,
        capture_output=True,
        timeout=600,
        check=False,
    )


PRIVILEGED_CONTROL_PLANE_ENVIRONMENT = (
    "POSTGRES_PRISMA_URL",
    "POSTGRES_URL",
    "POSTGRES_URL_NON_POOLING",
)


def environment_without_privileged_control_plane_credentials(
    source: dict[str, str] | None = None,
) -> dict[str, str]:
    env = dict(os.environ if source is None else source)
    for name in tuple(env):
        if (
            name.startswith("AGENTOPS_")
            or name.startswith("PG")
            or name.startswith("POSTGRES_")
            or name.startswith("DATABASE_")
            or name in PRIVILEGED_CONTROL_PLANE_ENVIRONMENT
        ):
            env.pop(name, None)
    return env


def assert_subprocess_environment_scrub() -> None:
    canaries = {
        "PATH": "/usr/bin",
        "AGENTOPS_POSTGRES_DSN": "postgresql://runtime-canary.invalid/db",
        "AGENTOPS_POSTGRES_DSN_FILE": "/tmp/runtime-dsn-canary",
        "AGENTOPS_POSTGRES_HOST": "component-canary.invalid",
        "AGENTOPS_POSTGRES_MIGRATOR_PASSWORD": "migrator-canary",
        "AGENTOPS_POSTGRES_ENTITLEMENT_ADMIN_DSN":
            "postgresql://admin-canary.invalid/db",
        "AGENTOPS_NODE_PG_DSN": "postgresql://node-helper-canary.invalid/db",
        "AGENTOPS_API_KEY": "gateway-canary",
        "AGENTOPS_ENTITLEMENT_OPERATOR_PASSWORD": "operator-canary",
        "AGENTOPS_HUMAN_SESSION_HMAC_KEY": "session-canary",
        "DATABASE_URL": "postgresql://database-url-canary.invalid/db",
        "DATABASE_AUTH_TOKEN": "database-auth-canary",
        "POSTGRES_URL": "postgresql://postgres-url-canary.invalid/db",
        "POSTGRES_PASSWORD": "postgres-password-canary",
        "POSTGRES_USER": "postgres-user-canary",
        "POSTGRES_HOST": "postgres-host-canary.invalid",
        "POSTGRES_DB": "postgres-db-canary",
        "PGPASSWORD": "libpq-canary",
        "PGSERVICEFILE": "/tmp/libpq-service-canary",
    }
    scrubbed = environment_without_privileged_control_plane_credentials(
        canaries,
    )
    if scrubbed != {"PATH": "/usr/bin"}:
        raise RuntimeError("privileged_subprocess_environment_scrub_failed")


def run_npm(
    npm: str,
    postgres_dsn: str | None,
    args: list[str],
    *,
    stdin: str | None = None,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = environment_without_privileged_control_plane_credentials()
    env.update({
        "AGENTOPS_POSTGRES_SSL": "0",
        **(environment or {}),
    })
    if postgres_dsn:
        env["AGENTOPS_POSTGRES_DSN"] = postgres_dsn
    return subprocess.run(
        [npm, "run", "--silent", *args],
        cwd=NEXT_APP,
        env=env,
        input=stdin,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )


def assert_postgres_statement_forbidden(
    adapter: NodePgAdapter,
    sql: str,
    *,
    script: bool = False,
) -> str:
    try:
        if script:
            adapter.executescript(sql)
        else:
            adapter.execute(sql)
    except NodePgError as error:
        if error.sqlstate == "42501":
            return error.sqlstate
        raise RuntimeError(
            "restricted_database_statement_check_inconclusive:"
            f"sqlstate={error.sqlstate or 'missing'}"
        ) from error
    raise RuntimeError("restricted_database_statement_unexpectedly_allowed")


def assert_entitlement_admin_environment_isolated(
    environment: dict[str, str],
) -> None:
    required = {
        "AGENTOPS_POSTGRES_ENTITLEMENT_ADMIN_DSN",
        "AGENTOPS_ENTITLEMENT_OPERATOR_PASSWORD",
        "AGENTOPS_ENTITLEMENT_OPERATOR_USERNAME",
        "AGENTOPS_ENTITLEMENT_CONTROL_PLANE_URL",
        "AGENTOPS_ENTITLEMENT_CONTROL_PLANE_ORIGIN",
        "AGENTOPS_POSTGRES_SCHEMA",
        "AGENTOPS_POSTGRES_RUNTIME_API_SCHEMA",
        "AGENTOPS_POSTGRES_ENTITLEMENT_ADMIN_ROLE",
    }
    forbidden = {
        "AGENTOPS_POSTGRES_DSN",
        "AGENTOPS_POSTGRES_MIGRATOR_DSN",
        "AGENTOPS_POSTGRES_RUNTIME_DSN",
        "AGENTOPS_POSTGRES_RUNTIME_ROLE",
        "AGENTOPS_POSTGRES_RUNTIME_PASSWORD",
        "AGENTOPS_POSTGRES_MIGRATOR_PASSWORD",
        "AGENTOPS_HUMAN_SESSION_HMAC_KEY",
    }
    if (
        not required.issubset(environment)
        or forbidden.intersection(environment)
    ):
        raise RuntimeError("entitlement_admin_environment_isolation_failed")


def assert_entitlement_admin_receipt_safe(
    receipt: dict[str, Any],
    stdout: str,
    stderr: str,
) -> None:
    required_true = {
        "ok",
        "audit_appended",
        "challenge_consumed",
        "human_session_consumed",
        "long_lived_credentials_omitted",
        "challenge_token_omitted",
        "credentials_omitted",
        "dsn_omitted",
        "raw_config_omitted",
        "control_plane_network_used",
    }
    if (
        receipt.get("contract")
            != "agentops_workspace_entitlement_administration_v2"
        or receipt.get("mode") != "confirmed"
        or receipt.get("outcome") != "created"
        or any(receipt.get(field) is not True for field in required_true)
    ):
        raise RuntimeError("entitlement_administration_v2_receipt_unverified")

    forbidden_keys = {
        "agentops_human_session",
        "challenge_token",
        "cookie",
        "csrf",
        "csrf_token",
        "human_session_id",
        "set_cookie",
    }

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, nested in value.items():
                if str(key).lower() in forbidden_keys:
                    raise RuntimeError(
                        "entitlement_administration_auth_material_exposed"
                    )
                walk(nested)
        elif isinstance(value, list):
            for nested in value:
                walk(nested)

    walk(receipt)
    rendered = f"{stdout}\n{stderr}".lower()
    forbidden_output_markers = (
        "agentops_human_session=",
        '"challenge_token":',
        '"csrf_token":',
        '"set_cookie":',
    )
    if any(marker in rendered for marker in forbidden_output_markers):
        raise RuntimeError("entitlement_administration_auth_material_exposed")


def derived_postgres_function_owner_role(
    application_schema: str,
    runtime_api_schema: str,
) -> str:
    digest = hashlib.sha256(
        f"{application_schema}\0{runtime_api_schema}".encode("utf-8")
    ).hexdigest()
    return f"agentops_fn_{digest[:24]}"


def cleanup_postgres_fixture(
    base_dsn: str,
    node: str,
    application_schema: str,
    runtime_api_schema: str,
    runtime_role: str,
    entitlement_admin_role: str,
    function_owner_role: str,
) -> dict[str, bool]:
    cleanup = NodePgAdapter(base_dsn, node)
    errors: list[str] = []
    role_names = (
        ("runtime", runtime_role),
        ("entitlement_admin", entitlement_admin_role),
        ("function_owner", function_owner_role),
    )
    existing_roles: dict[str, bool] = {}

    for label, role in role_names:
        try:
            row = cleanup.fetchone(
                "SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=?) AS present",
                (role,),
            )
            existing_roles[role] = bool(row and row.get("present"))
        except Exception:
            existing_roles[role] = True
            errors.append(f"inspect_role:{label}")

    for label, role in role_names:
        if not existing_roles.get(role):
            continue
        try:
            cleanup.execute(
                """SELECT pg_terminate_backend(pid)
                FROM pg_stat_activity
                WHERE usename=? AND pid<>pg_backend_pid()""",
                (role,),
            )
        except Exception:
            errors.append(f"terminate_role:{label}")

    for label, schema in (
        ("runtime_api_schema", runtime_api_schema),
        ("application_schema", application_schema),
    ):
        try:
            cleanup.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        except Exception:
            errors.append(f"drop_schema:{label}")

    for label, role in role_names:
        if not existing_roles.get(role):
            continue
        try:
            cleanup.execute(f'DROP OWNED BY "{role}"')
        except Exception:
            errors.append(f"drop_owned:{label}")
        try:
            cleanup.execute(f'DROP ROLE IF EXISTS "{role}"')
        except Exception:
            errors.append(f"drop_role:{label}")

    try:
        residue = cleanup.fetchone(
            """SELECT
              EXISTS(
                SELECT 1 FROM pg_namespace
                WHERE nspname IN (?,?)
              ) AS schema_present,
              EXISTS(
                SELECT 1 FROM pg_roles
                WHERE rolname IN (?,?,?)
              ) AS role_present""",
            (
                application_schema,
                runtime_api_schema,
                runtime_role,
                entitlement_admin_role,
                function_owner_role,
            ),
        )
        if (
            not residue
            or residue.get("schema_present") is not False
            or residue.get("role_present") is not False
        ):
            errors.append("catalog_residue")
    except Exception:
        errors.append("catalog_verification")

    if errors:
        raise RuntimeError(
            "postgres_fixture_cleanup_failed:" + ",".join(sorted(errors))
        )
    return {
        "schemas_removed": True,
        "roles_removed": True,
        "function_owner_role_removed": True,
        "catalog_zero_residue_verified": True,
    }


def seed_foundation(adapter: NodePgAdapter) -> None:
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    adapter.execute(
        "INSERT INTO users(user_id,name,email,role,created_at) VALUES(?,?,?,?,?)",
        (REQUESTER_ID, "Real Worker Requester", "real-worker-requester@local.invalid", "customer", now),
    )
    adapter.execute(
        """INSERT INTO runtime_connectors(
            runtime_connector_id,provider,connector_type,profile_name,base_url,binary_path,status,allow_real_run,
            require_confirm_run,trust_status,trust_note,trust_updated_at,last_health_at,last_error,created_at,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            "rtc_agent_gateway_local",
            "agent_gateway",
            "local",
            "TypeScript/Postgres real Worker acceptance",
            None,
            None,
            "ready",
            1,
            1,
            "trusted",
            "Ephemeral acceptance fixture.",
            now,
            now,
            None,
            now,
            now,
        ),
    )
    adapter.commit()


def seed_workers(
    adapter: NodePgAdapter,
    adapters: list[str],
    tokens: dict[str, str],
) -> None:
    now = dt.datetime.now(dt.timezone.utc)
    now_text = now.isoformat()
    expires = (now + dt.timedelta(hours=2)).isoformat()
    for runtime in adapters:
        agent_id = f"agt_real_{runtime}_review"
        task_id = f"tsk_real_{runtime}_review"
        adapter.execute(
            """INSERT INTO agents(
                agent_id,name,role,description,runtime_type,model_provider,model_name,status,permission_level,
                allowed_tools,budget_limit_usd,owner_user_id,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                agent_id,
                f"Real {runtime} Worker",
                "operator",
                "Ephemeral real Runtime acceptance Worker.",
                runtime,
                runtime,
                runtime,
                "idle",
                "standard",
                json.dumps(["agent_gateway.task", f"{runtime}.execute", "agent_gateway.audit"]),
                5.0,
                REQUESTER_ID,
                now_text,
                now_text,
            ),
        )
        adapter.execute(
            """INSERT INTO tasks(
                task_id,workspace_id,title,description,requester_id,owner_agent_id,collaborator_agent_ids,
                status,priority,due_date,acceptance_criteria,risk_level,budget_limit_usd,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                task_id,
                WORKSPACE_ID,
                f"Real {runtime} candidate review",
                "Return a bounded delivery summary from the governed task evidence.",
                REQUESTER_ID,
                agent_id,
                "[]",
                "planned",
                "high",
                None,
                "A real Runtime call must finish and propose one reviewable memory candidate.",
                "low",
                0,
                now_text,
                now_text,
            ),
        )
        adapter.execute(
            """INSERT INTO agent_gateway_tokens(
                token_id,token_hash,workspace_id,agent_id,scopes_json,status,label,heartbeat_timeout_sec,
                created_at,expires_at,revoked_at,last_used_at,last_heartbeat_at
            ) VALUES(?,?,?,?,?,'active',?,300,?,?,NULL,NULL,NULL)""",
            (
                f"tok_real_{runtime}_review",
                hashlib.sha256(tokens[runtime].encode("utf-8")).hexdigest(),
                WORKSPACE_ID,
                agent_id,
                json.dumps(SCOPES),
                f"Real {runtime} acceptance token",
                now_text,
                expires,
            ),
        )
    adapter.commit()


def run_worker(
    runtime: str,
    base_url: str,
    token: str,
    hermes_url: str,
    openclaw_bin: str,
    sensitive: list[str],
    worker_implementation: str,
    node: str,
) -> dict[str, Any]:
    if worker_implementation != "typescript":
        raise RuntimeError("commercial_worker_implementation_must_be_typescript")
    agent_id = f"agt_real_{runtime}_review"
    command = [
        node,
        str(NEXT_APP / "node_modules" / "tsx" / "dist" / "cli.mjs"),
        str(NEXT_APP / "scripts" / "commercial-worker.ts"),
        "--base-url",
        base_url,
        "--workspace-id",
        WORKSPACE_ID,
        "--agent-id",
        agent_id,
        "--task-id",
        f"tsk_real_{runtime}_review",
        "--adapter",
        runtime,
        "--once",
        "--confirm-run",
        "--allow-insecure-loopback",
        "--estimated-cost-usd",
        RUN_ESTIMATED_COST_USD,
        "--max-adapter-attempts",
        "1",
    ]
    if runtime == "hermes":
        command.extend([
            "--hermes-gateway-url",
            hermes_url,
            "--hermes-model",
            "hermes-agent",
            "--hermes-timeout-ms",
            "180000",
        ])
    else:
        command.extend([
            "--openclaw-bin",
            openclaw_bin,
            "--openclaw-agent",
            "main",
            "--openclaw-timeout-seconds",
            "180",
            "--working-directory",
            str(ROOT),
        ])
    env = environment_without_privileged_control_plane_credentials()
    env["AGENTOPS_API_KEY"] = token
    env["AGENTOPS_AGENT_TOKEN"] = token
    env["NODE_ENV"] = "production"
    completed = subprocess.run(
        command,
        cwd=NEXT_APP,
        env=env,
        text=True,
        capture_output=True,
        timeout=240,
        check=False,
    )
    if token in completed.stdout or token in completed.stderr:
        raise RuntimeError(f"{runtime} Worker output exposed its Agent Gateway credential")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        stdout_bytes = completed.stdout.encode("utf-8", errors="replace")
        stderr_bytes = completed.stderr.encode("utf-8", errors="replace")
        failure = {
            "worker_implementation": worker_implementation,
            "returncode": completed.returncode,
            "stdout_sha256": hashlib.sha256(stdout_bytes).hexdigest(),
            "stdout_size_bytes": len(stdout_bytes),
            "stderr_sha256": hashlib.sha256(stderr_bytes).hexdigest(),
            "stderr_size_bytes": len(stderr_bytes),
            "raw_worker_output_omitted": True,
        }
        raise RuntimeError(
            f"{runtime} Worker returned invalid JSON: "
            f"{json.dumps(failure, sort_keys=True)}"
        ) from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{runtime} Worker returned a non-object receipt")
    if completed.returncode != 0:
        state = payload.get("state") if isinstance(payload, dict) else None
        last_result = state.get("last_result") if isinstance(state, dict) else None
        last_error = state.get("last_error") if isinstance(state, dict) else None
        results = payload.get("results") if isinstance(payload, dict) else None
        first_result = results[0] if isinstance(results, list) and results else None
        direct_result = payload
        safe_last_result = {
            key: (last_result or direct_result).get(key)
            for key in (
                "runtime",
                "dry_run",
                "ok",
                "processed",
                "provider_call_performed",
                "reason",
                "error_type",
                "attempt_count",
                "run_id",
                "task_id",
                "ledger_evidence_complete",
                "manual_reconciliation_required",
                "evidence_failure_stage",
                "evidence_failure_code",
                "evidence_failure_status",
            )
            if isinstance(last_result or direct_result, dict)
            and key in (last_result or direct_result)
        }
        stderr_bytes = completed.stderr.encode("utf-8", errors="replace")
        failure = {
            "worker_implementation": worker_implementation,
            "returncode": completed.returncode,
            "status": state.get("status") if isinstance(state, dict) else None,
            "last_result": safe_last_result or None,
            "last_error": {
                "error_type": last_error.get("error_type"),
                "error_message": last_error.get("error_message"),
            } if isinstance(last_error, dict) else None,
            "result_error": {
                "error_type": first_result.get("error_type"),
                "error_message": first_result.get("error_message"),
            } if isinstance(first_result, dict) else None,
            "stderr_sha256": hashlib.sha256(stderr_bytes).hexdigest(),
            "stderr_size_bytes": len(stderr_bytes),
            "raw_worker_output_omitted": True,
        }
        raise RuntimeError(redact(f"{runtime} Worker failed: {json.dumps(failure, ensure_ascii=False, sort_keys=True)}", sensitive))
    if not payload.get("ok") or payload.get("processed") != 1:
        raise RuntimeError(redact(f"{runtime} Worker did not complete one task: {payload}", sensitive))
    return payload


def check_runtime_evidence(
    adapter: NodePgAdapter,
    runtime: str,
    worker_payload: dict[str, Any],
    sensitive: list[str],
) -> dict[str, Any]:
    results = worker_payload.get("results")
    iteration = (
        results[0]
        if isinstance(results, list) and results and isinstance(results[0], dict)
        else worker_payload
    )
    run_id = str(iteration.get("run_id") or "")
    if (
        not run_id.startswith("run_gw_")
        or iteration.get("plan_evidence_pass") is not True
        or iteration.get("provider_call_performed") is not True
        or iteration.get("dry_run") is not False
    ):
        raise RuntimeError(f"{runtime} Worker did not return a verified run/plan-evidence receipt")
    run = adapter.fetchone("SELECT run_id,status,runtime_type FROM runs WHERE run_id=?", (run_id,))
    cost_reservation = adapter.fetchone(
        """SELECT state,estimated_cost_usd::text AS estimated_cost_usd,
        observed_cost_usd::text AS observed_cost_usd,
        settled_cost_usd::text AS settled_cost_usd
        FROM run_cost_reservations WHERE workspace_id=? AND run_id=?""",
        (WORKSPACE_ID, run_id),
    )
    tool = adapter.fetchone(
        """SELECT tool_name,status,target_resource,normalized_args_json,result_summary
        FROM tool_calls WHERE run_id=? AND agent_id=?""",
        (run_id, f"agt_real_{runtime}_review"),
    )
    memory = adapter.fetchone(
        """SELECT memory_id,workspace_id,task_id,agent_id,canonical_text,source_type,source_ref,review_status
        FROM memories WHERE workspace_id=? AND agent_id=? AND source_type='run_log' AND source_ref=?""",
        (WORKSPACE_ID, f"agt_real_{runtime}_review", run_id),
    )
    manifest = adapter.fetchone(
        "SELECT manifest_id,status,run_id FROM plan_evidence_manifests WHERE run_id=?",
        (run_id,),
    )
    approval = adapter.fetchone(
        """SELECT approval_id,approval_kind,task_id,run_id,requested_by_agent_id,
        approver_user_id,decision,reason,expires_at,created_at,decided_at
        FROM approvals WHERE run_id=? AND approval_kind='customer_delivery'""",
        (run_id,),
    )
    task = adapter.fetchone(
        "SELECT status FROM tasks WHERE task_id=? AND workspace_id=?",
        (f"tsk_real_{runtime}_review", WORKSPACE_ID),
    )
    approval_event_count = int((adapter.fetchone(
        """SELECT COUNT(*) AS count FROM runtime_events
        WHERE run_id=? AND event_type='approval.customer_delivery.request'""",
        (run_id,),
    ) or {"count": 0})["count"])
    approval_audit_count = int((adapter.fetchone(
        """SELECT COUNT(*) AS count FROM audit_logs
        WHERE workspace_id=? AND action='agent_gateway.customer_delivery_approval_request'
          AND entity_type='approvals' AND entity_id=?""",
        (WORKSPACE_ID, str((approval or {}).get("approval_id") or "")),
    ) or {"count": 0})["count"])
    worker_audit = adapter.fetchone(
        """SELECT actor_type,actor_id,entity_type,entity_id,metadata_json,tamper_chain_hash
        FROM audit_logs WHERE action='agent_worker.task_processed' AND entity_type='runs' AND entity_id=?""",
        (run_id,),
    )
    adapter.commit()
    expected_target = "/v1/chat/completions" if runtime == "hermes" else "local://openclaw/main"
    if not run or run["status"] != "completed" or run["runtime_type"] != runtime:
        raise RuntimeError(f"{runtime} run ledger did not close as completed")
    if not (
        cost_reservation
        and cost_reservation["state"] == "settled"
        and cost_reservation["estimated_cost_usd"] == RUN_ESTIMATED_COST_USD
        and cost_reservation["observed_cost_usd"] == RUN_ESTIMATED_COST_USD
        and cost_reservation["settled_cost_usd"] == RUN_ESTIMATED_COST_USD
    ):
        raise RuntimeError(
            f"{runtime} run cost reservation did not settle the approved estimate"
        )
    if not tool or tool["status"] != "completed" or expected_target not in str(tool["target_resource"]):
        raise RuntimeError(f"{runtime} tool evidence does not prove the real adapter target")
    try:
        tool_args = json.loads(str(tool["normalized_args_json"] or "{}"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{runtime} tool evidence args are invalid") from exc
    if tool_args.get("provider_call_performed") is not True or tool_args.get("dry_run") is not False:
        raise RuntimeError(f"{runtime} tool evidence does not prove a non-dry-run provider call")
    if not manifest or manifest["status"] != "verified":
        raise RuntimeError(f"{runtime} plan-evidence manifest is not verified")
    if not (
        iteration.get("customer_delivery_approval_requested") is True
        and iteration.get("customer_delivery_approval_outcome") == "created"
        and iteration.get("customer_delivery_approval_control_plane") == "typescript_postgres"
        and approval
        and iteration.get("customer_delivery_approval_id") == approval["approval_id"]
        and approval["approval_kind"] == "customer_delivery"
        and approval["task_id"] == f"tsk_real_{runtime}_review"
        and approval["requested_by_agent_id"] == f"agt_real_{runtime}_review"
        and approval["approver_user_id"] is None
        and approval["decision"] == "pending"
        and approval["decided_at"] is None
        and task
        and task["status"] == "waiting_approval"
        and approval_event_count == approval_audit_count == 1
    ):
        raise RuntimeError(
            f"{runtime} Worker did not create one production-owned customer-delivery approval"
        )
    if not memory or memory["review_status"] != "candidate" or memory["source_ref"] != run_id:
        raise RuntimeError(f"{runtime} real run did not create a bound memory candidate")
    if not worker_audit or not worker_audit["tamper_chain_hash"]:
        raise RuntimeError(f"{runtime} Worker audit is absent from the tamper-evident chain")
    try:
        audit_metadata = json.loads(str(worker_audit["metadata_json"] or "{}"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{runtime} Worker audit metadata is invalid") from exc
    if not (
        worker_audit["actor_type"] == "agent"
        and worker_audit["actor_id"] == f"agt_real_{runtime}_review"
        and audit_metadata.get("provider_call_performed") is True
        and audit_metadata.get("dry_run") is False
    ):
        raise RuntimeError(f"{runtime} Worker audit does not bind the server-derived agent/provider-call evidence")
    evidence = adapter.fetchall(
        """SELECT input_summary,output_summary,error_message,raw_payload_hash FROM runtime_events
        WHERE run_id=? OR agent_id=? ORDER BY created_at""",
        (run_id, f"agt_real_{runtime}_review"),
    )
    evidence_text = json.dumps(
        {
            "tool": tool,
            "cost_reservation": cost_reservation,
            "memory": memory,
            "approval": approval,
            "audit": worker_audit,
            "events": evidence,
        },
        ensure_ascii=False,
        default=str,
    )
    for secret in sensitive:
        if secret and secret in evidence_text:
            raise RuntimeError(f"{runtime} persisted runtime evidence exposed protected input material")
    return {
        "run_id": run_id,
        "memory_id": str(memory["memory_id"]),
        "manifest_id": str(manifest["manifest_id"]),
        "approval_id": str(approval["approval_id"]),
        "cost_reservation_state": str(cost_reservation["state"]),
        "cost_reservation_settled": True,
        "source_type": str(memory["source_type"]),
        "provider_call_performed": True,
        "dry_run": False,
        "delivery_approval_creation_source": "production_next_typescript_postgres_agent_gateway_route",
        "delivery_approval_request_outcome": "created",
        "delivery_approval_runtime_event_count": approval_event_count,
        "delivery_approval_audit_count": approval_audit_count,
    }


def login_owner(base_url: str, public_origin: str, password: str) -> tuple[str, str]:
    status, payload, headers = http_json(
        "POST",
        f"{base_url}/api/mis/human-auth/login",
        {"username": OWNER_USERNAME, "password": password},
        headers={"Origin": public_origin},
    )
    cookie = raw_cookie(headers)
    csrf = str(payload.get("csrf_token") or "") if isinstance(payload, dict) else ""
    if status != 200 or not cookie or not csrf:
        error_code = str(payload.get("error") or "unknown") if isinstance(payload, dict) else "unknown"
        raise RuntimeError(
            f"Human Owner login failed closed with status {status} and error {error_code}"
        )
    return cookie, csrf


def human_review(
    adapter: NodePgAdapter,
    base_url: str,
    public_origin: str,
    cookie: str,
    csrf: str,
    runtime: str,
    receipt: dict[str, str],
) -> dict[str, Any]:
    list_headers = {
        "Cookie": f"agentops_human_session={cookie}",
        "X-AgentOps-Workspace-Id": WORKSPACE_ID,
    }
    status, candidates, _ = http_json(
        "GET",
        f"{base_url}/api/mis/memories?workspace_id={WORKSPACE_ID}",
        headers=list_headers,
    )
    candidate_ids = {str(item.get("memory_id")) for item in candidates} if isinstance(candidates, list) else set()
    if status != 200 or receipt["memory_id"] not in candidate_ids:
        raise RuntimeError(f"{runtime} candidate is absent from the Human workspace queue")

    approval_status, approvals, _ = http_json(
        "GET",
        f"{base_url}/api/mis/approvals?workspace_id={WORKSPACE_ID}",
        headers=list_headers,
    )
    approval_ids = {str(item.get("approval_id")) for item in approvals} if isinstance(approvals, list) else set()
    if approval_status != 200 or receipt["approval_id"] not in approval_ids:
        error_code = str(approvals.get("error") or "unknown") if isinstance(approvals, dict) else "unknown"
        raise RuntimeError(
            f"{runtime} delivery approval is absent from the Human workspace queue "
            f"with status {approval_status} and error {error_code}"
        )
    approval_headers = {
        **list_headers,
        "Origin": public_origin,
        "X-AgentOps-CSRF": csrf,
        "Idempotency-Key": f"real-worker-{runtime}-delivery-approve-0001",
    }
    approval_route = (
        f"{base_url}/api/mis/approvals/"
        f"{urllib.parse.quote(receipt['approval_id'])}/approve"
    )
    approval_first_status, approval_first, _ = http_json(
        "POST",
        approval_route,
        {"workspace_id": WORKSPACE_ID},
        headers=approval_headers,
    )
    approval_replay_status, approval_replay, _ = http_json(
        "POST",
        approval_route,
        {"workspace_id": WORKSPACE_ID},
        headers=approval_headers,
    )
    key = f"real-worker-{runtime}-approve-0001"
    write_headers = {
        **list_headers,
        "Origin": public_origin,
        "X-AgentOps-CSRF": csrf,
        "Idempotency-Key": key,
    }
    route = f"{base_url}/api/mis/memories/{urllib.parse.quote(receipt['memory_id'])}/approve"
    first_status, first, _ = http_json("POST", route, {"workspace_id": WORKSPACE_ID}, headers=write_headers)
    replay_status, replay, _ = http_json("POST", route, {"workspace_id": WORKSPACE_ID}, headers=write_headers)
    foreign_headers = {**list_headers, "X-AgentOps-Workspace-Id": OTHER_WORKSPACE_ID}
    foreign_status, _, _ = http_json(
        "GET",
        f"{base_url}/api/mis/memories?workspace_id={OTHER_WORKSPACE_ID}",
        headers=foreign_headers,
    )
    memory = adapter.fetchone(
        "SELECT review_status,owner_user_id FROM memories WHERE memory_id=? AND workspace_id=?",
        (receipt["memory_id"], WORKSPACE_ID),
    )
    request_count = int((adapter.fetchone(
        "SELECT COUNT(*) AS count FROM human_memory_review_requests WHERE workspace_id=? AND memory_id=?",
        (WORKSPACE_ID, receipt["memory_id"]),
    ) or {"count": 0})["count"])
    audit_count = int((adapter.fetchone(
        """SELECT COUNT(*) AS count FROM audit_logs
        WHERE actor_type='user' AND action='memory.approved' AND entity_type='memories' AND entity_id=?""",
        (receipt["memory_id"],),
    ) or {"count": 0})["count"])
    event_count = int((adapter.fetchone(
        "SELECT COUNT(*) AS count FROM runtime_events WHERE event_type='memory.approved' AND task_id=? AND agent_id=?",
        (f"tsk_real_{runtime}_review", f"agt_real_{runtime}_review"),
    ) or {"count": 0})["count"])
    owner = adapter.fetchone(
        "SELECT user_id FROM human_login_credentials WHERE username=?",
        (OWNER_USERNAME,),
    )
    approval = adapter.fetchone(
        """SELECT decision,approver_user_id FROM approvals
        WHERE approval_id=? AND task_id=? AND run_id=?""",
        (receipt["approval_id"], f"tsk_real_{runtime}_review", receipt["run_id"]),
    )
    approval_request_count = int((adapter.fetchone(
        """SELECT COUNT(*) AS count FROM human_approval_decision_requests
        WHERE workspace_id=? AND approval_id=?""",
        (WORKSPACE_ID, receipt["approval_id"]),
    ) or {"count": 0})["count"])
    approval_audit_count = int((adapter.fetchone(
        """SELECT COUNT(*) AS count FROM audit_logs
        WHERE workspace_id=? AND actor_type='user'
          AND action='approval.customer_delivery.approved'
          AND entity_type='approvals' AND entity_id=?""",
        (WORKSPACE_ID, receipt["approval_id"]),
    ) or {"count": 0})["count"])
    approval_event_count = int((adapter.fetchone(
        """SELECT COUNT(*) AS count FROM runtime_events
        WHERE event_type='approval.customer_delivery.approved'
          AND run_id=? AND task_id=? AND agent_id=?""",
        (receipt["run_id"], f"tsk_real_{runtime}_review", f"agt_real_{runtime}_review"),
    ) or {"count": 0})["count"])
    delivery_run = adapter.fetchone(
        "SELECT status,approval_required FROM runs WHERE run_id=? AND workspace_id=?",
        (receipt["run_id"], WORKSPACE_ID),
    )
    delivery_task = adapter.fetchone(
        "SELECT status FROM tasks WHERE task_id=? AND workspace_id=?",
        (f"tsk_real_{runtime}_review", WORKSPACE_ID),
    )
    delivery_manifest = adapter.fetchone(
        """SELECT status FROM plan_evidence_manifests
        WHERE manifest_id=? AND workspace_id=? AND task_id=? AND run_id=? AND agent_id=?""",
        (
            receipt["manifest_id"],
            WORKSPACE_ID,
            f"tsk_real_{runtime}_review",
            receipt["run_id"],
            f"agt_real_{runtime}_review",
        ),
    )
    adapter.commit()
    review_checks = {
        "approval_first_status": approval_first_status == 200,
        "approval_first_outcome": approval_first.get("outcome") == "updated",
        "approval_typescript_owner": (
            approval_first.get("control_plane") == "typescript_postgres"
        ),
        "approval_reason_omitted": (
            (approval_first.get("approval") or {}).get("reason") is None
        ),
        "legacy_delivery_gate_omitted": "delivery_approval_gate" not in approval_first,
        "approval_replay_status": approval_replay_status == 200,
        "approval_replay_outcome": approval_replay.get("outcome") == "unchanged",
        "owner_present": bool(owner),
        "approval_present": bool(approval),
        "approval_decision": bool(approval and approval["decision"] == "approved"),
        "approval_actor": bool(
            approval
            and owner
            and approval["approver_user_id"] == owner["user_id"]
        ),
        "approval_evidence_counts": (
            approval_request_count
            == approval_audit_count
            == approval_event_count
            == 1
        ),
        "delivery_run_completed": bool(
            delivery_run and delivery_run["status"] == "completed"
        ),
        "delivery_run_gate_cleared": bool(
            delivery_run and int(delivery_run["approval_required"] or 0) == 0
        ),
        "delivery_task_completed": bool(
            delivery_task and delivery_task["status"] == "completed"
        ),
        "delivery_manifest_verified": bool(
            delivery_manifest and delivery_manifest["status"] == "verified"
        ),
        "memory_first_status": first_status == 200,
        "memory_first_outcome": first.get("outcome") == "updated",
        "memory_replay_status": replay_status == 200,
        "memory_replay_outcome": replay.get("outcome") == "unchanged",
        "memory_cross_workspace_denied": foreign_status == 403,
        "memory_approved": bool(memory and memory["review_status"] == "approved"),
        "memory_evidence_counts": request_count == audit_count == event_count == 1,
    }
    failed_review_checks = [
        name for name, passed in review_checks.items() if not passed
    ]
    if failed_review_checks:
        raise RuntimeError(
            f"{runtime} Human review evidence or replay/isolation contract "
            f"failed checks: {','.join(failed_review_checks)}"
        )
    return {
        "queue_visible": True,
        "first_outcome": first.get("outcome"),
        "replay_outcome": replay.get("outcome"),
        "cross_workspace_status": foreign_status,
        "request_count": request_count,
        "human_audit_count": audit_count,
        "human_runtime_event_count": event_count,
        "delivery_approval_queue_visible": True,
        "delivery_approval_first_outcome": approval_first.get("outcome"),
        "delivery_approval_replay_outcome": approval_replay.get("outcome"),
        "delivery_approval_request_count": approval_request_count,
        "delivery_approval_audit_count": approval_audit_count,
        "delivery_approval_runtime_event_count": approval_event_count,
        "delivery_manifest_gate_passed": bool(
            approval_first_status == 200
            and delivery_manifest
            and delivery_manifest["status"] == "verified"
        ),
        "delivery_manifest_gate_status": delivery_manifest["status"] if delivery_manifest else None,
    }


def prepare_manifest_authority_guard_fixture(
    adapter: NodePgAdapter,
    base_url: str,
    runtime: str,
    token: str,
) -> dict[str, Any]:
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    agent_id = f"agt_real_{runtime}_review"
    task_id = f"tsk_real_{runtime}_manifest_guard"
    run_id = f"run_real_{runtime}_manifest_guard"
    plan_id = f"plan_real_{runtime}_manifest_guard"
    expected_steps = [
        "READ",
        "PLAN",
        "RETRIEVE",
        "COMPARE",
        "EXECUTE",
        "VERIFY",
        "RECORD",
    ]
    adapter.execute(
        """INSERT INTO tasks(
            task_id,workspace_id,title,description,requester_id,owner_agent_id,collaborator_agent_ids,
            status,priority,due_date,acceptance_criteria,risk_level,budget_limit_usd,created_at,updated_at
        ) VALUES(?,?,?,?,?,?,'[]','planned','medium',NULL,?,'low',0,?,?)""",
        (
            task_id,
            WORKSPACE_ID,
            f"{runtime} plan-evidence authority guard",
            "Isolated negative fixture; no provider call or customer output.",
            REQUESTER_ID,
            agent_id,
            "Selective evidence must fail closed.",
            now,
            now,
        ),
    )
    adapter.commit()
    headers = {
        "Authorization": f"Bearer {token}",
        "X-AgentOps-Workspace-Id": WORKSPACE_ID,
        "X-AgentOps-Agent-Id": agent_id,
    }
    claim_status, claim_payload, _ = http_json(
        "POST",
        f"{base_url}/api/mis/agent-gateway/tasks/"
        f"{urllib.parse.quote(task_id)}/claim",
        {
            "workspace_id": WORKSPACE_ID,
            "agent_id": agent_id,
            "task_id": task_id,
        },
        headers=headers,
    )
    plan_status, plan_payload, _ = http_json(
        "POST",
        f"{base_url}/api/mis/agent-gateway/agent-plans",
        {
            "workspace_id": WORKSPACE_ID,
            "agent_id": agent_id,
            "plan_id": plan_id,
            "task_id": task_id,
            "task_understanding": (
                "Verify that complete run evidence is authoritative."
            ),
            "referenced_specs": [
                "docs/COMMERCIAL_MIGRATION_CLEAN_ROOM_BREAKDOWN.md",
            ],
            "referenced_memories": [
                "manifest-authority-isolated-fixture",
            ],
            "referenced_bases": ["agent-gateway-ledger"],
            "proposed_files_to_change": [],
            "risk_level": "low",
            "approval_required": False,
            "execution_steps": expected_steps,
            "verification_plan": (
                "Require every tool, evaluation, and artifact row."
            ),
            "rollback_plan": "Discard the isolated fixture schema.",
            "status": "submitted",
        },
        headers=headers,
    )
    verify_status, verify_payload, _ = http_json(
        "GET",
        f"{base_url}/api/mis/agent-gateway/agent-plans/"
        f"{urllib.parse.quote(plan_id)}/verify",
        headers=headers,
    )
    verified_plan = (
        verify_payload.get("agent_plan")
        if isinstance(verify_payload, dict)
        else None
    )
    verification = (
        verify_payload.get("verification")
        if isinstance(verify_payload, dict)
        else None
    )
    plan_hash = str(
        (verified_plan or {}).get("plan_hash")
        if isinstance(verified_plan, dict)
        else ""
    )
    run_status, run_payload, _ = http_json(
        "POST",
        f"{base_url}/api/mis/agent-gateway/runs/start",
        {
            "workspace_id": WORKSPACE_ID,
            "agent_id": agent_id,
            "run_id": run_id,
            "task_id": task_id,
            "runtime_type": "codex",
            "model_provider": "authority-fixture",
            "model_name": "no-provider-call",
            "agent_plan_id": plan_id,
            "plan_hash": plan_hash,
            "estimated_cost_usd": RUN_ESTIMATED_COST_USD,
            "input_summary": "Isolated manifest authority fixture.",
            "delegation_id": f"manifest_guard_{runtime}",
        },
        headers=headers,
    )
    heartbeat_status, heartbeat_payload, _ = http_json(
        "POST",
        f"{base_url}/api/mis/agent-gateway/runs/"
        f"{urllib.parse.quote(run_id)}/heartbeat",
        {
            "workspace_id": WORKSPACE_ID,
            "agent_id": agent_id,
            "task_id": task_id,
            "status": "completed",
            "output_summary": (
                "Fixture run completed before evidence verification."
            ),
            "cost_usd": RUN_ESTIMATED_COST_USD,
        },
        headers=headers,
    )
    setup_checks = {
        "claim": (
            claim_status == 200
            and isinstance(claim_payload, dict)
            and claim_payload.get("outcome") == "claimed"
        ),
        "plan": (
            plan_status == 201
            and isinstance(plan_payload, dict)
            and plan_payload.get("outcome") == "created"
        ),
        "verification": (
            verify_status == 200
            and isinstance(verification, dict)
            and verification.get("pass") is True
            and len(plan_hash) == 64
        ),
        "run": (
            run_status == 201
            and isinstance(run_payload, dict)
            and run_payload.get("outcome") == "created"
        ),
        "heartbeat": (
            heartbeat_status == 200
            and isinstance(heartbeat_payload, dict)
            and heartbeat_payload.get("outcome") == "updated"
        ),
    }
    failed_setup_checks = [
        name for name, passed in setup_checks.items() if not passed
    ]
    if failed_setup_checks:
        raise RuntimeError(
            f"{runtime} manifest authority fixture setup failed checks: "
            f"{','.join(failed_setup_checks)}"
        )
    return {
        "task_id": task_id,
        "run_id": run_id,
        "plan_id": plan_id,
        "plan_hash": plan_hash,
        "agent_id": agent_id,
        "expected_steps": expected_steps,
        "runtime_type": "codex",
        "provider_call_performed": False,
    }


def verify_manifest_authority_guards(
    adapter: NodePgAdapter,
    base_url: str,
    runtime: str,
    token: str,
    receipt: dict[str, str],
) -> dict[str, Any]:
    approved_run_id = receipt["run_id"]
    approved_task_id = f"tsk_real_{runtime}_review"
    agent_id = f"agt_real_{runtime}_review"
    approved_manifest = adapter.fetchone(
        """SELECT plan_id,expected_steps_json,tool_call_ids_json,evaluation_ids_json,artifact_ids_json
        FROM plan_evidence_manifests WHERE manifest_id=? AND run_id=?""",
        (receipt["manifest_id"], approved_run_id),
    )
    if not approved_manifest:
        raise RuntimeError(f"{runtime} verified manifest disappeared before authority guard checks")
    approved_expected_steps = json.loads(str(approved_manifest["expected_steps_json"] or "[]"))
    approved_tool_call_ids = json.loads(str(approved_manifest["tool_call_ids_json"] or "[]"))
    approved_evaluation_ids = json.loads(str(approved_manifest["evaluation_ids_json"] or "[]"))
    approved_artifact_ids = json.loads(str(approved_manifest["artifact_ids_json"] or "[]"))
    if not approved_expected_steps or not approved_tool_call_ids or not approved_evaluation_ids or not approved_artifact_ids:
        raise RuntimeError(f"{runtime} verified manifest lacks declared evidence needed for authority guard checks")

    guard = prepare_manifest_authority_guard_fixture(
        adapter,
        base_url,
        runtime,
        token,
    )
    run_id = str(guard["run_id"])
    task_id = str(guard["task_id"])
    expected_steps = list(guard["expected_steps"])
    plan_id = str(guard["plan_id"])
    tool_call_ids = [f"tc_real_{runtime}_guard_completed"]
    evaluation_ids = [f"eval_real_{runtime}_guard_passed"]
    artifact_ids = [f"art_real_{runtime}_guard_report"]
    headers = {
        "Authorization": f"Bearer {token}",
        "X-AgentOps-Workspace-Id": WORKSPACE_ID,
    }
    success_tool_status, success_tool_payload, _ = http_json(
        "POST",
        f"{base_url}/api/agent-gateway/tool-calls",
        {
            "workspace_id": WORKSPACE_ID,
            "agent_id": agent_id,
            "tool_call_id": tool_call_ids[0],
            "run_id": run_id,
            "task_id": task_id,
            "tool_name": "agent_gateway.authority_guard",
            "tool_category": "custom",
            "risk_level": "low",
            "status": "completed",
            "args": {"contract": "plan_evidence_authority_negative_fixture_v1"},
            "result_summary": "Completed evidence retained for selective-manifest testing.",
        },
        headers=headers,
    )
    success_evaluation_status, success_evaluation_payload, _ = http_json(
        "POST",
        f"{base_url}/api/agent-gateway/evaluations/submit",
        {
            "workspace_id": WORKSPACE_ID,
            "agent_id": agent_id,
            "evaluation_id": evaluation_ids[0],
            "run_id": run_id,
            "task_id": task_id,
            "evaluator_type": "rule",
            "score": 1,
            "pass_fail": "pass",
            "rubric": {"contract": "plan_evidence_authority_negative_fixture_v1"},
            "notes": "Passing evidence retained for selective-manifest testing.",
        },
        headers=headers,
    )
    success_artifact_status, success_artifact_payload, _ = http_json(
        "POST",
        f"{base_url}/api/agent-gateway/artifacts",
        {
            "workspace_id": WORKSPACE_ID,
            "agent_id": agent_id,
            "artifact_id": artifact_ids[0],
            "run_id": run_id,
            "task_id": task_id,
            "artifact_type": "report",
            "title": "Manifest authority guard report",
            "summary": "Bounded isolated fixture artifact.",
        },
        headers=headers,
    )
    baseline_manifest_id = f"pem_real_{runtime}_guard_verified"
    baseline_status, baseline_payload, _ = http_json(
        "POST",
        f"{base_url}/api/agent-gateway/plan-evidence-manifests",
        {
            "workspace_id": WORKSPACE_ID,
            "agent_id": agent_id,
            "manifest_id": baseline_manifest_id,
            "plan_id": plan_id,
            "task_id": task_id,
            "run_id": run_id,
            "mismatch_policy": "block",
            "expected_steps": expected_steps,
            "tool_call_ids": tool_call_ids,
            "evaluation_ids": evaluation_ids,
            "artifact_ids": artifact_ids,
            "verify_now": True,
        },
        headers=headers,
    )
    baseline_verification = baseline_payload.get("verification") if isinstance(baseline_payload, dict) else {}
    if not (
        success_tool_status == 201
        and success_evaluation_status == 201
        and success_artifact_status == 201
        and baseline_status == 201
        and isinstance(baseline_verification, dict)
        and baseline_verification.get("pass") is True
    ):
        raise RuntimeError(
            f"{runtime} isolated manifest authority baseline failed: "
            f"tool={success_tool_status}:{success_tool_payload} "
            f"evaluation={success_evaluation_status}:{success_evaluation_payload} "
            f"artifact={success_artifact_status}:{success_artifact_payload} "
            f"manifest={baseline_status}:{baseline_payload}"
        )
    manifest = {"plan_id": plan_id}
    conflict_manifest_id = f"pem_real_{runtime}_expected_steps_conflict"
    conflict_status, conflict_payload, _ = http_json(
        "POST",
        f"{base_url}/api/agent-gateway/plan-evidence-manifests",
        {
            "workspace_id": WORKSPACE_ID,
            "agent_id": agent_id,
            "manifest_id": conflict_manifest_id,
            "plan_id": manifest["plan_id"],
            "task_id": task_id,
            "run_id": run_id,
            "mismatch_policy": "block",
            "expected_steps": ["READ", "OMIT_FAILED_EVIDENCE", "DELIVER"],
            "tool_call_ids": tool_call_ids,
            "evaluation_ids": evaluation_ids,
            "artifact_ids": artifact_ids,
            "verify_now": True,
        },
        headers=headers,
    )
    conflict_count = adapter.fetchone(
        "SELECT COUNT(*) AS count FROM plan_evidence_manifests WHERE manifest_id=?",
        (conflict_manifest_id,),
    )
    if (
        conflict_status != 409
        or not isinstance(conflict_payload, dict)
        or conflict_payload.get("error") != "plan_evidence_expected_steps_conflict"
        or int((conflict_count or {}).get("count") or 0) != 0
    ):
        raise RuntimeError(f"{runtime} manifest expected_steps override was not rejected before persistence")

    audit_override_manifest_id = f"pem_real_{runtime}_audit_ids_override"
    audit_override_status, audit_override_payload, _ = http_json(
        "POST",
        f"{base_url}/api/agent-gateway/plan-evidence-manifests",
        {
            "workspace_id": WORKSPACE_ID,
            "agent_id": agent_id,
            "manifest_id": audit_override_manifest_id,
            "plan_id": manifest["plan_id"],
            "task_id": task_id,
            "run_id": run_id,
            "mismatch_policy": "block",
            "expected_steps": expected_steps,
            "tool_call_ids": tool_call_ids,
            "evaluation_ids": evaluation_ids,
            "artifact_ids": artifact_ids,
            "audit_ids": ["audit_caller_selected"],
            "verify_now": True,
        },
        headers=headers,
    )
    audit_override_row = adapter.fetchone(
        """SELECT status,audit_ids_json FROM plan_evidence_manifests
        WHERE manifest_id=?""",
        (audit_override_manifest_id,),
    )
    audit_override_manifest = (
        audit_override_payload.get("manifest")
        if isinstance(audit_override_payload, dict)
        else {}
    )
    audit_override_verification = (
        audit_override_payload.get("verification")
        if isinstance(audit_override_payload, dict)
        else {}
    )
    if not (
        audit_override_status == 201
        and isinstance(audit_override_manifest, dict)
        and audit_override_manifest.get("audit_ids_json") == "[]"
        and isinstance(audit_override_verification, dict)
        and audit_override_verification.get("pass") is True
        and audit_override_row
        and audit_override_row.get("status") == "verified"
        and audit_override_row.get("audit_ids_json") == "[]"
        and "audit_caller_selected" not in json.dumps(
            audit_override_payload,
            sort_keys=True,
        )
    ):
        raise RuntimeError(
            f"{runtime} caller-selected audit IDs were not replaced by "
            "server-derived audit evidence"
        )

    failed_tool_id = f"tc_real_{runtime}_omitted_failed"
    failed_tool_status, failed_tool_payload, _ = http_json(
        "POST",
        f"{base_url}/api/agent-gateway/tool-calls",
        {
            "workspace_id": WORKSPACE_ID,
            "agent_id": agent_id,
            "tool_call_id": failed_tool_id,
            "run_id": run_id,
            "task_id": task_id,
            "tool_name": "agent_gateway.negative_fixture",
            "tool_category": "custom",
            "risk_level": "low",
            "status": "failed",
            "args": {"contract": "plan_evidence_authority_negative_fixture_v1"},
            "result_summary": "Intentional failed evidence retained for completeness verification.",
        },
        headers=headers,
    )
    failed_evaluation_id = f"eval_real_{runtime}_omitted_failed"
    failed_evaluation_status, failed_evaluation_payload, _ = http_json(
        "POST",
        f"{base_url}/api/agent-gateway/evaluations/submit",
        {
            "workspace_id": WORKSPACE_ID,
            "agent_id": agent_id,
            "evaluation_id": failed_evaluation_id,
            "run_id": run_id,
            "task_id": task_id,
            "evaluator_type": "rule",
            "score": 0.1,
            "pass_fail": "fail",
            "rubric": {"contract": "plan_evidence_authority_negative_fixture_v1"},
            "notes": "Intentional failed evaluation retained for completeness verification.",
        },
        headers=headers,
    )
    omitted_artifact_id = f"art_real_{runtime}_omitted_additional"
    omitted_artifact_status, omitted_artifact_payload, _ = http_json(
        "POST",
        f"{base_url}/api/agent-gateway/artifacts",
        {
            "workspace_id": WORKSPACE_ID,
            "agent_id": agent_id,
            "artifact_id": omitted_artifact_id,
            "run_id": run_id,
            "task_id": task_id,
            "artifact_type": "report",
            "title": "Omitted additional guard artifact",
            "summary": "Intentional additional artifact retained for completeness verification.",
        },
        headers=headers,
    )
    if failed_tool_status != 201 or failed_evaluation_status != 201 or omitted_artifact_status != 201:
        raise RuntimeError(
            f"{runtime} could not persist negative completeness fixtures: "
            f"tool={failed_tool_status}:{failed_tool_payload} "
            f"evaluation={failed_evaluation_status}:{failed_evaluation_payload} "
            f"artifact={omitted_artifact_status}:{omitted_artifact_payload}"
        )

    selective_manifest_id = f"pem_real_{runtime}_selective_success_only"
    selective_status, selective_payload, _ = http_json(
        "POST",
        f"{base_url}/api/agent-gateway/plan-evidence-manifests",
        {
            "workspace_id": WORKSPACE_ID,
            "agent_id": agent_id,
            "manifest_id": selective_manifest_id,
            "plan_id": manifest["plan_id"],
            "task_id": task_id,
            "run_id": run_id,
            "mismatch_policy": "block",
            "expected_steps": expected_steps,
            "tool_call_ids": tool_call_ids,
            "evaluation_ids": evaluation_ids,
            "artifact_ids": artifact_ids,
            "verify_now": True,
        },
        headers=headers,
    )
    verification = selective_payload.get("verification") if isinstance(selective_payload, dict) else {}
    verification = verification if isinstance(verification, dict) else {}
    failed_checks = {
        str(item.get("id"))
        for item in verification.get("failed_checks") or []
        if isinstance(item, dict)
    }
    required_failures = {
        "tool_evidence_completed",
        "tool_evidence_complete",
        "evaluation_evidence_passed",
        "evaluation_evidence_complete",
        "artifact_evidence_complete",
    }
    persisted = adapter.fetchone(
        "SELECT status FROM plan_evidence_manifests WHERE manifest_id=? AND run_id=?",
        (selective_manifest_id, run_id),
    )
    if (
        selective_status != 201
        or verification.get("pass") is not False
        or not required_failures.issubset(failed_checks)
        or not persisted
        or persisted.get("status") != "blocked"
    ):
        raise RuntimeError(f"{runtime} selective success-only evidence was not blocked against the complete run ledger")

    blocked_approval_id = f"ap_customer_worker_delivery_blocked_{run_id}"
    blocked_status, blocked_payload, _ = http_json(
        "POST",
        f"{base_url}/api/agent-gateway/approvals/request",
        {
            "workspace_id": WORKSPACE_ID,
            "agent_id": agent_id,
            "requested_by_agent_id": agent_id,
            "approval_id": blocked_approval_id,
            "approval_kind": "customer_delivery",
            "decision": "pending",
            "task_id": task_id,
            "run_id": run_id,
            "reason": "Customer delivery requires Human Owner review.",
        },
        headers={
            "Authorization": f"Bearer {token}",
            "X-AgentOps-Workspace-Id": WORKSPACE_ID,
        },
    )
    blocked_approval_count = int((adapter.fetchone(
        """SELECT COUNT(*) AS count FROM approvals
        WHERE approval_id=? OR (run_id=? AND approval_kind='customer_delivery')""",
        (blocked_approval_id, run_id),
    ) or {"count": 0})["count"])
    blocked_run = adapter.fetchone(
        "SELECT status,approval_required FROM runs WHERE run_id=? AND workspace_id=?",
        (run_id, WORKSPACE_ID),
    )
    blocked_task = adapter.fetchone(
        "SELECT status FROM tasks WHERE task_id=? AND workspace_id=?",
        (task_id, WORKSPACE_ID),
    )
    blocked_runtime_event_count = int((adapter.fetchone(
        """SELECT COUNT(*) AS count FROM runtime_events
        WHERE run_id=? AND event_type='approval.customer_delivery.request'""",
        (run_id,),
    ) or {"count": 0})["count"])
    blocked_audit_count = int((adapter.fetchone(
        """SELECT COUNT(*) AS count FROM audit_logs
        WHERE workspace_id=? AND action='agent_gateway.customer_delivery_approval_request'
          AND entity_id=?""",
        (WORKSPACE_ID, blocked_approval_id),
    ) or {"count": 0})["count"])
    if not (
        blocked_status == 409
        and isinstance(blocked_payload, dict)
        and blocked_payload.get("error") == "verified_plan_evidence_manifest_required"
        and blocked_approval_count == 0
        and blocked_run
        and blocked_run["status"] == "completed"
        and int(blocked_run["approval_required"] or 0) == 0
        and blocked_task
        and blocked_task["status"] == "completed"
        and blocked_runtime_event_count == 0
        and blocked_audit_count == 0
    ):
        raise RuntimeError(
            f"{runtime} production customer-delivery request did not fail closed before persistence"
        )

    sealed_ids = {
        "tool": f"tc_real_{runtime}_sealed_append",
        "evaluation": f"eval_real_{runtime}_sealed_append",
        "artifact": f"art_real_{runtime}_sealed_append",
        "manifest": f"pem_real_{runtime}_sealed_append",
    }
    sealed_requests = [
        (
            "tool",
            f"{base_url}/api/agent-gateway/tool-calls",
            {
                "workspace_id": WORKSPACE_ID,
                "agent_id": agent_id,
                "tool_call_id": sealed_ids["tool"],
                "run_id": approved_run_id,
                "task_id": approved_task_id,
                "tool_name": "agent_gateway.sealed_append",
                "tool_category": "custom",
                "risk_level": "low",
                "status": "failed",
                "args": {"contract": "customer_delivery_evidence_seal_v1"},
            },
        ),
        (
            "evaluation",
            f"{base_url}/api/agent-gateway/evaluations/submit",
            {
                "workspace_id": WORKSPACE_ID,
                "agent_id": agent_id,
                "evaluation_id": sealed_ids["evaluation"],
                "run_id": approved_run_id,
                "task_id": approved_task_id,
                "evaluator_type": "rule",
                "score": 0,
                "pass_fail": "fail",
                "rubric": {"contract": "customer_delivery_evidence_seal_v1"},
            },
        ),
        (
            "artifact",
            f"{base_url}/api/agent-gateway/artifacts",
            {
                "workspace_id": WORKSPACE_ID,
                "agent_id": agent_id,
                "artifact_id": sealed_ids["artifact"],
                "run_id": approved_run_id,
                "task_id": approved_task_id,
                "artifact_type": "report",
                "title": "Forbidden post-delivery artifact",
            },
        ),
        (
            "manifest",
            f"{base_url}/api/agent-gateway/plan-evidence-manifests",
            {
                "workspace_id": WORKSPACE_ID,
                "agent_id": agent_id,
                "manifest_id": sealed_ids["manifest"],
                "plan_id": approved_manifest["plan_id"],
                "task_id": approved_task_id,
                "run_id": approved_run_id,
                "mismatch_policy": "block",
                "expected_steps": approved_expected_steps,
                "tool_call_ids": approved_tool_call_ids,
                "evaluation_ids": approved_evaluation_ids,
                "artifact_ids": approved_artifact_ids,
                "verify_now": True,
            },
        ),
    ]
    sealed_statuses: dict[str, int] = {}
    for kind, route, payload in sealed_requests:
        status, response_payload, _ = http_json("POST", route, payload, headers=headers)
        sealed_statuses[kind] = status
        if status != 409 or response_payload.get("error") != "customer_delivery_evidence_sealed":
            raise RuntimeError(f"{runtime} approved customer-delivery {kind} evidence was not sealed")
    sealed_row_counts = {
        "tool": int((adapter.fetchone("SELECT COUNT(*) AS count FROM tool_calls WHERE tool_call_id=?", (sealed_ids["tool"],)) or {"count": 0})["count"]),
        "evaluation": int((adapter.fetchone("SELECT COUNT(*) AS count FROM evaluations WHERE evaluation_id=?", (sealed_ids["evaluation"],)) or {"count": 0})["count"]),
        "artifact": int((adapter.fetchone("SELECT COUNT(*) AS count FROM artifacts WHERE artifact_id=?", (sealed_ids["artifact"],)) or {"count": 0})["count"]),
        "manifest": int((adapter.fetchone("SELECT COUNT(*) AS count FROM plan_evidence_manifests WHERE manifest_id=?", (sealed_ids["manifest"],)) or {"count": 0})["count"]),
    }
    if any(sealed_row_counts.values()):
        raise RuntimeError(f"{runtime} post-delivery evidence seal persisted rejected rows")
    return {
        "expected_steps_server_derived": True,
        "complete_run_tool_evidence_enforced": True,
        "complete_run_evaluation_evidence_enforced": True,
        "complete_run_artifact_evidence_enforced": True,
        "audit_evidence_server_derived": True,
        "selective_manifest_status": "blocked",
        "failed_checks": sorted(required_failures),
        "customer_delivery_revalidation_status": blocked_status,
        "customer_delivery_revalidation_blocked": True,
        "blocked_customer_delivery_request_persisted": False,
        "approved_customer_delivery_evidence_sealed": True,
        "sealed_evidence_statuses": sealed_statuses,
    }


def main() -> int:
    global ROOT, NEXT_APP

    parser = argparse.ArgumentParser(description="Run real Hermes/OpenClaw Worker -> Human Review through Next/Postgres only.")
    parser.add_argument("--postgres-dsn", required=True, help="External Postgres URL; the smoke uses and drops an isolated schema.")
    parser.add_argument("--adapter", action="append", choices=["hermes", "openclaw"], default=[])
    parser.add_argument(
        "--worker-implementation",
        choices=["typescript"],
        default="typescript",
        help="Commercial provider execution is owned exclusively by the TypeScript Worker.",
    )
    parser.add_argument("--hermes-gateway-url", default=os.environ.get("HERMES_GATEWAY_URL", "http://127.0.0.1:8642"))
    parser.add_argument("--openclaw-bin", default=os.environ.get("OPENCLAW_BIN", shutil.which("openclaw") or "/opt/homebrew/bin/openclaw"))
    parser.add_argument(
        "--source-root",
        default=str(DEFAULT_SOURCE_ROOT),
        help="Candidate Git worktree root; defaults to the checkout containing this trusted harness.",
    )
    args = parser.parse_args()

    try:
        ROOT = resolve_source_root(args.source_root)
    except Exception as exc:
        result({
            "ok": False,
            "contract": CONTRACT_ID,
            "error_type": exc.__class__.__name__,
            "error": str(exc),
            "next_runtime_mode": "production_start",
            "python_api_started": False,
            "worker_implementation": args.worker_implementation,
            "python_worker_started": False,
            "credentials_omitted": True,
        }, [])
        return 1
    NEXT_APP = ROOT / "ui" / "next-app"

    adapters = list(dict.fromkeys(args.adapter or ["hermes", "openclaw"]))
    node = shutil.which("node")
    npm = shutil.which("npm")
    if not node or not npm or not (NEXT_APP / "node_modules" / "next").exists():
        result({
            "ok": False,
            "contract": CONTRACT_ID,
            "error": "next_runtime_unavailable",
            "next_runtime_mode": "production_start",
            "worker_implementation": args.worker_implementation,
        }, [])
        return 1
    if "openclaw" in adapters and not Path(args.openclaw_bin).exists():
        result({
            "ok": False,
            "contract": CONTRACT_ID,
            "error": "openclaw_binary_unavailable",
            "next_runtime_mode": "production_start",
            "worker_implementation": args.worker_implementation,
        }, [])
        return 1

    tracked_before = ""
    tracked_after_build = ""
    next_artifact_sha256 = ""
    next_artifact_before_start_sha256 = ""
    next_artifact_after_acceptance_sha256 = ""
    next_artifact_after_cleanup_sha256 = ""
    source_commit = ""
    tracked_worktree_clean = False
    try:
        assert_harness_safety_helper_contracts()
        source_commit, tracked_worktree_clean = git_source_state(ROOT)
        if not tracked_worktree_clean:
            raise RuntimeError("candidate_source_worktree_not_clean")
        tracked_before = tracked_worktree_fingerprint(ROOT)
        built = run_next_build(npm)
        tracked_after_build = tracked_worktree_fingerprint(ROOT)
        if tracked_after_build != tracked_before:
            raise RuntimeError(
                "next_build_modified_tracked_source:"
                f"before={tracked_before}:after={tracked_after_build}"
            )
        if built.returncode != 0:
            raise RuntimeError(
                "Next production build failed "
                f"(code={built.returncode}): {(built.stdout or '')[-1200:]} {(built.stderr or '')[-1200:]}"
            )
        next_artifact_sha256 = stable_tree_sha256(NEXT_APP / ".next")
        tracked_after_prepare = tracked_worktree_fingerprint(ROOT)
        if tracked_after_prepare != tracked_before:
            raise RuntimeError(
                "acceptance_preparation_modified_tracked_source:"
                f"before={tracked_before}:after={tracked_after_prepare}"
            )
        source_commit_after_build, clean_after_build = git_source_state(ROOT)
        if source_commit_after_build != source_commit or not clean_after_build:
            raise RuntimeError("candidate_source_identity_changed_during_build")
    except Exception as exc:
        original_traceback = traceback.format_exc()
        source_commit_after_failure = ""
        clean_after_failure = False
        try:
            tracked_after_failure = tracked_worktree_fingerprint(ROOT) if tracked_before else ""
        except Exception:
            tracked_after_failure = ""
        try:
            source_commit_after_failure, clean_after_failure = git_source_state(
                ROOT
            )
        except Exception:
            pass
        source_identity_changed = (
            not source_commit_after_failure
            or source_commit_after_failure != source_commit
            or (tracked_worktree_clean and not clean_after_failure)
        )
        mutation_detected = (
            source_identity_changed
            or (
                bool(tracked_before)
                and (
                    not tracked_after_failure
                    or tracked_after_failure != tracked_before
                )
            )
        )
        result({
            "ok": False,
            "contract": CONTRACT_ID,
            "error_type": "RuntimeError" if mutation_detected else exc.__class__.__name__,
            "error": (
                "tracked_worktree_modified_or_unverifiable_during_next_build_or_preparation"
                if mutation_detected
                else str(exc)
            ),
            "traceback": original_traceback[-4000:],
            "next_runtime_mode": "production_start",
            "next_artifact_sha256": next_artifact_sha256 or None,
            "next_build_completed": bool(next_artifact_sha256),
            "source_commit": source_commit or None,
            "tracked_worktree_clean": clean_after_failure,
            "tracked_worktree_fingerprint_before": tracked_before or None,
            "tracked_worktree_fingerprint_after_build": tracked_after_build or None,
            "tracked_worktree_fingerprint_after_acceptance": tracked_after_failure or None,
            "tracked_worktree_unchanged": bool(tracked_before) and not mutation_detected,
            "python_api_started": False,
            "worker_implementation": args.worker_implementation,
            "python_worker_started": False,
            "real_runtime_execution_performed": False,
            "credentials_omitted": True,
        }, [str(ROOT)])
        return 1

    runtime_dependency_identity: dict[str, str] = {}
    if "hermes" in adapters:
        runtime_dependency_identity["hermes_endpoint_sha256"] = hashlib.sha256(
            args.hermes_gateway_url.encode("utf-8")
        ).hexdigest()
    if "openclaw" in adapters:
        runtime_dependency_identity["openclaw_binary_sha256"] = file_sha256(Path(args.openclaw_bin))

    fixture_suffix = secrets.token_hex(8)
    schema = f"agentops_real_worker_review_{fixture_suffix}"
    runtime_api_schema = f"agentops_real_runtime_api_{fixture_suffix}"
    runtime_role = f"agentops_real_runtime_{fixture_suffix}"
    entitlement_admin_role = f"agentops_real_admin_{fixture_suffix}"
    function_owner_role = derived_postgres_function_owner_role(
        schema,
        runtime_api_schema,
    )
    migrator_dsn = dsn_with_search_path(args.postgres_dsn, schema)
    runtime_password = "Runtime-" + secrets.token_urlsafe(24)
    entitlement_admin_password = "Admin-" + secrets.token_urlsafe(24)
    runtime_dsn = dsn_with_credentials(
        migrator_dsn,
        runtime_role,
        runtime_password,
    )
    entitlement_admin_dsn = dsn_with_search_path_sequence(
        dsn_with_credentials(
            args.postgres_dsn,
            entitlement_admin_role,
            entitlement_admin_password,
        ),
        ("pg_catalog", runtime_api_schema, "pg_temp"),
    )
    owner_password = "Owner-" + secrets.token_urlsafe(24)
    hmac_key = secrets.token_urlsafe(48)
    tokens = {
        runtime: f"contract_real_token_{runtime}_{secrets.token_urlsafe(24)}"
        for runtime in adapters
    }
    sensitive = [
        args.postgres_dsn,
        runtime_dsn,
        args.hermes_gateway_url,
        args.openclaw_bin,
        str(ROOT),
        migrator_dsn,
        entitlement_admin_dsn,
        owner_password,
        runtime_password,
        entitlement_admin_password,
        hmac_key,
        *tokens.values(),
    ]
    # Runtime locations are redacted from diagnostics, while only credentials and
    # protected task input are forbidden from the bounded persisted evidence.
    persisted_sensitive = [
        args.postgres_dsn,
        migrator_dsn,
        runtime_dsn,
        entitlement_admin_dsn,
        owner_password,
        runtime_password,
        entitlement_admin_password,
        hmac_key,
        *tokens.values(),
    ]
    setup: NodePgAdapter | None = None
    adapter: NodePgAdapter | None = None
    next_proc: subprocess.Popen[str] | None = None
    worker_receipts: dict[str, Any] = {}
    human_receipts: dict[str, Any] = {}
    manifest_authority_receipts: dict[str, Any] = {}
    entitlement_admin_receipt: dict[str, Any] = {}
    cleanup_receipt: dict[str, bool] = {}
    process_stop_receipt: dict[str, Any] = {}
    entitlement_admin_forbidden_operations: dict[str, str] = {}
    tracked_after_acceptance = ""
    worker_process_started = False
    fixture_cleanup_complete = False
    try:
        setup = NodePgAdapter(args.postgres_dsn, node)
        setup.execute(f'CREATE SCHEMA "{schema}"')
        setup.commit()
        setup.close()
        setup = None

        role_environment = {
            "AGENTOPS_DEPLOYMENT_MODE": "production",
            "AGENTOPS_CONTROL_PLANE_MODE": "postgres",
            "AGENTOPS_POSTGRES_MIGRATOR_DSN": migrator_dsn,
            "AGENTOPS_POSTGRES_SCHEMA": schema,
            "AGENTOPS_POSTGRES_RUNTIME_API_SCHEMA": runtime_api_schema,
            "AGENTOPS_POSTGRES_RUNTIME_ROLE": runtime_role,
            "AGENTOPS_POSTGRES_RUNTIME_PASSWORD": runtime_password,
            "AGENTOPS_POSTGRES_ENTITLEMENT_ADMIN_ROLE":
                entitlement_admin_role,
            "AGENTOPS_POSTGRES_ENTITLEMENT_ADMIN_PASSWORD":
                entitlement_admin_password,
        }
        migrated = run_npm(
            npm,
            migrator_dsn,
            ["migrate:postgres"],
            environment=role_environment,
        )
        if migrated.returncode != 0:
            raise RuntimeError(redact(f"Commercial schema migration failed: {migrated.stdout} {migrated.stderr}", sensitive))
        runtime_environment = {
            "AGENTOPS_DEPLOYMENT_MODE": "production",
            "AGENTOPS_CONTROL_PLANE_MODE": "postgres",
            "AGENTOPS_POSTGRES_SCHEMA": schema,
            "AGENTOPS_POSTGRES_RUNTIME_API_SCHEMA": runtime_api_schema,
            "AGENTOPS_POSTGRES_RUNTIME_ROLE": runtime_role,
        }
        checked = run_npm(
            npm,
            runtime_dsn,
            ["check:postgres-schema"],
            environment=runtime_environment,
        )
        if checked.returncode != 0:
            raise RuntimeError(redact(
                f"Restricted runtime schema check failed: "
                f"{checked.stdout} {checked.stderr}",
                sensitive,
            ))
        try:
            checked_receipt = json.loads(checked.stdout or "{}")
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "restricted_runtime_schema_receipt_invalid"
            ) from exc
        if (
            checked_receipt.get("ok") is not True
            or checked_receipt.get("operation") != "check"
            or checked_receipt.get("database_role_boundary_verified")
                is not True
        ):
            raise RuntimeError("restricted_runtime_role_boundary_unverified")
        adapter = NodePgAdapter(runtime_dsn, node)
        seed_foundation(adapter)
        adapter.close()
        adapter = None
        bootstrapped = run_npm(
            npm,
            runtime_dsn,
            [
                "bootstrap:owner",
                "--",
                "--workspace-id",
                WORKSPACE_ID,
                "--username",
                OWNER_USERNAME,
                "--display-name",
                "Real Worker Review Owner",
                "--password-stdin",
            ],
            stdin=f"{owner_password}\n",
            environment=runtime_environment,
        )
        if bootstrapped.returncode != 0:
            raise RuntimeError(redact(f"Owner bootstrap failed: {bootstrapped.stdout} {bootstrapped.stderr}", sensitive))
        try:
            bootstrap_receipt = json.loads(bootstrapped.stdout or "{}")
            owner_user_id = str(
                (bootstrap_receipt.get("user") or {}).get("user_id") or ""
            )
        except (AttributeError, json.JSONDecodeError) as exc:
            raise RuntimeError("owner_bootstrap_receipt_invalid") from exc
        if (
            bootstrap_receipt.get("ok") is not True
            or not owner_user_id
        ):
            raise RuntimeError("owner_bootstrap_identity_unverified")

        next_artifact_before_start_sha256 = stable_tree_sha256(
            NEXT_APP / ".next"
        )
        if next_artifact_before_start_sha256 != next_artifact_sha256:
            raise RuntimeError("next_artifact_changed_before_start")
        port = free_port()
        base_url = f"http://127.0.0.1:{port}"
        public_origin = f"https://127.0.0.1:{port}"
        env = environment_without_privileged_control_plane_credentials()
        env.update({
            "AGENTOPS_DEPLOYMENT_MODE": "production",
            "AGENTOPS_CONTROL_PLANE_MODE": "postgres",
            "AGENTOPS_TS_CONTROL_PLANE_MODE": "postgres",
            "AGENTOPS_POSTGRES_DSN": runtime_dsn,
            "AGENTOPS_POSTGRES_SCHEMA": schema,
            "AGENTOPS_POSTGRES_RUNTIME_API_SCHEMA": runtime_api_schema,
            "AGENTOPS_POSTGRES_RUNTIME_ROLE": runtime_role,
            "AGENTOPS_POSTGRES_SSL": "0",
            "AGENTOPS_API_BASE": f"http://127.0.0.1:{free_port()}/api",
            "AGENTOPS_ALLOWED_ORIGINS": public_origin,
            "AGENTOPS_HUMAN_SESSION_HMAC_KEY": hmac_key,
            "NEXT_TELEMETRY_DISABLED": "1",
            "NODE_ENV": "production",
        })
        next_proc = subprocess.Popen(
            [
                node,
                str(
                    NEXT_APP
                    / "node_modules"
                    / "next"
                    / "dist"
                    / "bin"
                    / "next"
                ),
                "start",
                "-p",
                str(port),
            ],
            cwd=NEXT_APP,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        wait_for_next(base_url, next_proc, sensitive)

        entitlement_now = dt.datetime.now(dt.timezone.utc)
        effective_at = (
            entitlement_now - dt.timedelta(minutes=1)
        ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        expires_at = (
            entitlement_now + dt.timedelta(hours=8)
        ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        entitlement_admin_environment = {
            "AGENTOPS_DEPLOYMENT_MODE": "production",
            "AGENTOPS_CONTROL_PLANE_MODE": "postgres",
            "AGENTOPS_POSTGRES_SCHEMA": schema,
            "AGENTOPS_POSTGRES_RUNTIME_API_SCHEMA": runtime_api_schema,
            "AGENTOPS_POSTGRES_ENTITLEMENT_ADMIN_ROLE":
                entitlement_admin_role,
            "AGENTOPS_POSTGRES_ENTITLEMENT_ADMIN_DSN":
                entitlement_admin_dsn,
            "AGENTOPS_ENTITLEMENT_CONTROL_PLANE_URL": base_url,
            "AGENTOPS_ENTITLEMENT_CONTROL_PLANE_ORIGIN": public_origin,
            "AGENTOPS_ENTITLEMENT_OPERATOR_USERNAME": OWNER_USERNAME,
            "AGENTOPS_ENTITLEMENT_OPERATOR_PASSWORD": owner_password,
        }
        assert_entitlement_admin_environment_isolated(
            entitlement_admin_environment
        )
        configured = run_npm(
            npm,
            None,
            [
                "configure:workspace-entitlement",
                "--",
                "--workspace-id",
                WORKSPACE_ID,
                "--operator-user-id",
                owner_user_id,
                "--edition",
                "enterprise_byoc",
                "--status",
                "active",
                "--capabilities",
                "enrollment_issue,session_issue,run_start",
                "--max-agents",
                str(max(10, len(SCOPES))),
                "--max-active-enrollments",
                str(max(10, len(SCOPES))),
                "--max-active-sessions-per-agent",
                "10",
                "--max-concurrent-runs",
                "10",
                "--max-monthly-runs",
                "1000",
                "--max-monthly-cost-usd",
                "1000.000000",
                "--effective-at",
                effective_at,
                "--expires-at",
                expires_at,
                "--confirm",
                "--expect-absent",
            ],
            environment=entitlement_admin_environment,
        )
        if configured.returncode != 0:
            raise RuntimeError(redact(
                "Entitlement administration failed: "
                f"{configured.stdout} {configured.stderr}",
                sensitive,
            ))
        try:
            entitlement_admin_receipt = json.loads(
                configured.stdout or "{}"
            )
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "entitlement_administration_receipt_invalid"
            ) from exc
        if not isinstance(entitlement_admin_receipt, dict):
            raise RuntimeError("entitlement_administration_receipt_invalid")
        assert_entitlement_admin_receipt_safe(
            entitlement_admin_receipt,
            configured.stdout,
            configured.stderr,
        )

        challenge_persistence = NodePgAdapter(
            migrator_dsn,
            node,
        ).fetchone(
            """SELECT
              count(*)::integer AS challenge_count,
              bool_and(token_sha256 ~ '^[a-f0-9]{64}$')
                AS token_hash_only,
              bool_and(
                position(
                  'agentops_human_session='
                  IN to_jsonb(entitlement_admin_challenges)::text
                )=0
                AND position(
                  '"challenge_token"'
                  IN to_jsonb(entitlement_admin_challenges)::text
                )=0
                AND position(
                  '"csrf_token"'
                  IN to_jsonb(entitlement_admin_challenges)::text
                )=0
              ) AS raw_auth_payload_omitted,
              NOT EXISTS(
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema=?
                  AND table_name='entitlement_admin_challenges'
                  AND column_name IN (
                    'challenge_token','cookie','csrf','csrf_token'
                  )
              ) AS raw_auth_columns_omitted
            FROM entitlement_admin_challenges
            WHERE workspace_id=? AND operator_user_id=?""",
            (schema, WORKSPACE_ID, owner_user_id),
        )
        if challenge_persistence != {
            "challenge_count": 1,
            "token_hash_only": True,
            "raw_auth_payload_omitted": True,
            "raw_auth_columns_omitted": True,
        }:
            raise RuntimeError(
                "entitlement_challenge_persistence_boundary_unverified"
            )

        admin_adapter = NodePgAdapter(entitlement_admin_dsn, node)
        admin_search_path = admin_adapter.fetchone(
            "SELECT current_setting('search_path') AS search_path"
        )
        normalized_admin_search_path = [
            part.strip().strip('"')
            for part in str(
                (admin_search_path or {}).get("search_path") or ""
            ).split(",")
            if part.strip()
        ]
        if normalized_admin_search_path != [
            "pg_catalog",
            runtime_api_schema,
            "pg_temp",
        ]:
            raise RuntimeError("entitlement_admin_search_path_unverified")

        executable_application_functions = admin_adapter.fetchall(
            """SELECT
              namespace_row.nspname AS schema_name,
              procedure_row.proname AS function_name,
              oidvectortypes(procedure_row.proargtypes)
                AS identity_arguments
            FROM pg_proc AS procedure_row
            JOIN pg_namespace AS namespace_row
              ON namespace_row.oid=procedure_row.pronamespace
            WHERE namespace_row.nspname IN (?,?)
              AND has_function_privilege(
                current_user,procedure_row.oid,'EXECUTE'
              )
            ORDER BY
              namespace_row.nspname,
              procedure_row.proname,
              oidvectortypes(procedure_row.proargtypes)""",
            (schema, runtime_api_schema),
        )
        if executable_application_functions != [
            {
                "schema_name": runtime_api_schema,
                "function_name":
                    "agentops_apply_workspace_entitlement_v11",
                "identity_arguments": "text, text, jsonb",
            },
            {
                "schema_name": runtime_api_schema,
                "function_name":
                    "agentops_plan_workspace_entitlement_v11",
                "identity_arguments": "text, text, jsonb",
            },
        ]:
            raise RuntimeError(
                "entitlement_admin_execute_allowlist_unverified"
            )
        entitlement_admin_forbidden_operations["issue"] = (
            assert_postgres_statement_forbidden(
                admin_adapter,
                f'SELECT "{runtime_api_schema}".'
                "agentops_issue_workspace_entitlement_admin_challenge_v11("
                "NULL::text,NULL::text,NULL::text,NULL::text,"
                "NULL::jsonb,NULL::text,NULL::interval)",
            )
        )

        application_tables = admin_adapter.fetchall(
            """SELECT class_row.relname AS table_name
            FROM pg_class AS class_row
            JOIN pg_namespace AS namespace_row
              ON namespace_row.oid=class_row.relnamespace
            WHERE namespace_row.nspname=?
              AND class_row.relkind IN ('r','p')
            ORDER BY class_row.relname""",
            (schema,),
        )
        if not application_tables:
            raise RuntimeError("entitlement_admin_app_table_matrix_empty")
        for table in application_tables:
            table_name = str(table.get("table_name") or "")
            quoted_table = table_name.replace('"', '""')
            assert_postgres_statement_forbidden(
                admin_adapter,
                f'SELECT 1 FROM "{schema}"."{quoted_table}" LIMIT 0',
            )
        entitlement_admin_forbidden_operations["select"] = "42501"

        for operation, forbidden_sql, script in (
            (
                "insert",
                f'INSERT INTO "{schema}".runs(run_id) SELECT '
                "'agentops_forbidden_insert_probe' WHERE FALSE",
                False,
            ),
            (
                "update",
                f'UPDATE "{schema}".run_cost_reservations '
                "SET state=state WHERE FALSE",
                False,
            ),
            (
                "delete",
                f'DELETE FROM "{schema}".workspace_memberships WHERE FALSE',
                False,
            ),
            (
                "truncate",
                f'BEGIN; TRUNCATE TABLE "{schema}".'
                "agentops_schema_migrations; ROLLBACK;",
                True,
            ),
            (
                "ddl",
                f'BEGIN; CREATE TABLE "{schema}".'
                "agentops_forbidden_admin_ddl_probe(id integer); ROLLBACK;",
                True,
            ),
        ):
            entitlement_admin_forbidden_operations[operation] = (
                assert_postgres_statement_forbidden(
                    admin_adapter,
                    forbidden_sql,
                    script=script,
                )
            )
        if set(entitlement_admin_forbidden_operations) != {
            "select",
            "insert",
            "update",
            "delete",
            "truncate",
            "ddl",
            "issue",
        } or set(entitlement_admin_forbidden_operations.values()) != {"42501"}:
            raise RuntimeError(
                "entitlement_admin_forbidden_operation_matrix_unverified"
            )

        adapter = NodePgAdapter(runtime_dsn, node)
        seed_workers(adapter, adapters, tokens)

        for runtime in adapters:
            worker_process_started = True
            worker_payload = run_worker(
                runtime,
                base_url,
                tokens[runtime],
                args.hermes_gateway_url,
                args.openclaw_bin,
                sensitive,
                args.worker_implementation,
                node,
            )
            worker_receipts[runtime] = check_runtime_evidence(
                adapter,
                runtime,
                worker_payload,
                persisted_sensitive,
            )

        cookie, csrf = login_owner(base_url, public_origin, owner_password)
        sensitive.extend([cookie, csrf])
        for runtime in adapters:
            human_receipts[runtime] = human_review(
                adapter,
                base_url,
                public_origin,
                cookie,
                csrf,
                runtime,
                worker_receipts[runtime],
            )
        for runtime in adapters:
            manifest_authority_receipts[runtime] = verify_manifest_authority_guards(
                adapter,
                base_url,
                runtime,
                tokens[runtime],
                worker_receipts[runtime],
            )

        process_stop_receipt = stop_process(next_proc)
        next_proc = None
        if (
            process_stop_receipt.get("stopped") is not True
            or process_stop_receipt.get("errors")
        ):
            raise RuntimeError("next_process_stop_failed")
        next_artifact_after_acceptance_sha256 = stable_tree_sha256(
            NEXT_APP / ".next"
        )
        if next_artifact_after_acceptance_sha256 != next_artifact_sha256:
            raise RuntimeError("next_artifact_changed_during_acceptance")
        if adapter is not None:
            try:
                adapter.close()
            except Exception:
                pass
            adapter = None
        cleanup_receipt = cleanup_postgres_fixture(
            args.postgres_dsn,
            node,
            schema,
            runtime_api_schema,
            runtime_role,
            entitlement_admin_role,
            function_owner_role,
        )
        fixture_cleanup_complete = True
        next_artifact_after_cleanup_sha256 = stable_tree_sha256(
            NEXT_APP / ".next"
        )
        if next_artifact_after_cleanup_sha256 != next_artifact_sha256:
            raise RuntimeError("next_artifact_changed_during_cleanup")
        tracked_after_acceptance = tracked_worktree_fingerprint(ROOT)
        if tracked_after_acceptance != tracked_before:
            raise RuntimeError(
                "acceptance_modified_tracked_source:"
                f"before={tracked_before}:after={tracked_after_acceptance}"
            )
        source_commit_after_acceptance, clean_after_acceptance = git_source_state(
            ROOT
        )
        if (
            source_commit_after_acceptance != source_commit
            or not clean_after_acceptance
        ):
            raise RuntimeError(
                "candidate_source_identity_changed_during_acceptance"
            )
        result({
            "ok": True,
            "contract": CONTRACT_ID,
            "control_plane": "typescript_postgres",
            "deployment_mode": "production",
            "next_runtime_mode": "production_start",
            "next_internal_transport_scheme": "http_loopback",
            "human_session_public_origin_scheme": "https",
            "next_artifact_sha256": next_artifact_sha256,
            "next_artifact_before_start_sha256":
                next_artifact_before_start_sha256,
            "next_artifact_after_acceptance_sha256":
                next_artifact_after_acceptance_sha256,
            "next_artifact_after_cleanup_sha256":
                next_artifact_after_cleanup_sha256,
            "next_artifact_identity_verified": True,
            "next_build_completed": True,
            "source_commit": source_commit,
            "tracked_worktree_clean": True,
            "tracked_worktree_fingerprint_before": tracked_before,
            "tracked_worktree_fingerprint_after_build": tracked_after_build,
            "tracked_worktree_fingerprint_after_acceptance": tracked_after_acceptance,
            "tracked_worktree_unchanged": True,
            "python_api_started": False,
            "python_or_sqlite_commercial_default": False,
            "database_role_boundary_verified": True,
            "migrator_runtime_roles_distinct": True,
            "runtime_migrator_credentials_separated": True,
            "runtime_entitlement_admin_credentials_separated": True,
            "subprocess_environment_scrub_verified": True,
            "worker_database_credentials_omitted": True,
            "worker_human_session_credentials_omitted": True,
            "entitlement_admin_executed": True,
            "entitlement_admin_online_human_challenge_verified": True,
            "entitlement_admin_environment_isolated": True,
            "entitlement_admin_search_path_verified": True,
            "entitlement_admin_search_path": [
                "pg_catalog",
                runtime_api_schema,
                "pg_temp",
            ],
            "entitlement_admin_execute_allowlist_verified": True,
            "entitlement_admin_execute_allowlist": [
                "agentops_apply_workspace_entitlement_v11",
                "agentops_plan_workspace_entitlement_v11",
            ],
            "entitlement_admin_issue_forbidden": True,
            "entitlement_admin_all_app_table_select_forbidden": True,
            "entitlement_admin_app_table_count": len(application_tables),
            "entitlement_admin_forbidden_dml_verified": True,
            "entitlement_admin_forbidden_ddl_verified": True,
            "entitlement_admin_forbidden_operations":
                entitlement_admin_forbidden_operations,
            "entitlement_admin_forbidden_sqlstate_verified": True,
            "entitlement_challenge_consumed": True,
            "entitlement_challenge_token_hash_only_persisted": True,
            "entitlement_raw_auth_material_omitted": True,
            "next_process_stop": process_stop_receipt,
            "entitlement_administration": entitlement_admin_receipt,
            "worker_implementation": args.worker_implementation,
            "typescript_worker_started": (
                worker_process_started
                and args.worker_implementation == "typescript"
            ),
            "python_worker_started": False,
            "real_runtime_execution_performed": all(
                receipt.get("provider_call_performed") is True and receipt.get("dry_run") is False
                for receipt in worker_receipts.values()
            ),
            "adapters": adapters,
            "workers": worker_receipts,
            "human_reviews": human_receipts,
            "manifest_authority_guards": manifest_authority_receipts,
            "manifest_authority_guards_passed": len(manifest_authority_receipts) == len(adapters),
            "runtime_dependency_identity": runtime_dependency_identity,
            "real_run_bound_delivery_decisions_completed": all(
                receipt.get("delivery_manifest_gate_passed") is True
                and receipt.get("delivery_approval_first_outcome") == "updated"
                and receipt.get("delivery_approval_replay_outcome") == "unchanged"
                for receipt in human_receipts.values()
            ),
            "worker_created_delivery_approvals": all(
                receipt.get("delivery_approval_request_outcome") == "created"
                for receipt in worker_receipts.values()
            ),
            "run_cost_reservations_settled": all(
                receipt.get("cost_reservation_settled") is True
                for receipt in worker_receipts.values()
            ),
            "delivery_approval_creation_source": "production_next_typescript_postgres_agent_gateway_route",
            "agent_gateway_legacy_path_rewrite_verified": True,
            "raw_prompt_response_omitted": True,
            "credentials_omitted": True,
            "schema_isolated_and_ephemeral": True,
            "fixture_cleanup": cleanup_receipt,
            "fixture_cleanup_verified_before_success": True,
        }, sensitive)
        return 0
    except Exception as exc:
        original_traceback = traceback.format_exc()
        process_stop_error = ""
        if next_proc is not None:
            process_stop_receipt = stop_process(next_proc)
            next_proc = None
            if (
                process_stop_receipt.get("stopped") is not True
                or process_stop_receipt.get("errors")
            ):
                process_stop_error = "next_process_stop_failed"
        if adapter is not None:
            adapter.close()
            adapter = None
        cleanup_error = ""
        try:
            cleanup_receipt = cleanup_postgres_fixture(
                args.postgres_dsn,
                node,
                schema,
                runtime_api_schema,
                runtime_role,
                entitlement_admin_role,
                function_owner_role,
            )
            fixture_cleanup_complete = True
            next_artifact_after_cleanup_sha256 = stable_tree_sha256(
                NEXT_APP / ".next"
            )
        except Exception as cleanup_exc:
            cleanup_error = str(cleanup_exc)
        fingerprint_error = ""
        source_state_error = ""
        source_commit_after_failure = ""
        clean_after_failure = False
        try:
            tracked_after_acceptance = tracked_worktree_fingerprint(ROOT)
        except Exception as fingerprint_exc:
            tracked_after_acceptance = ""
            fingerprint_error = str(fingerprint_exc)
        try:
            source_commit_after_failure, clean_after_failure = git_source_state(
                ROOT
            )
        except Exception as source_state_exc:
            source_state_error = str(source_state_exc)
        mutation_detected = (
            not tracked_after_acceptance
            or tracked_after_acceptance != tracked_before
            or not source_commit_after_failure
            or source_commit_after_failure != source_commit
            or not clean_after_failure
        )
        result({
            "ok": False,
            "contract": CONTRACT_ID,
            "error_type": "RuntimeError" if mutation_detected else exc.__class__.__name__,
            "error": (
                "tracked_worktree_modified_or_unverifiable_during_acceptance"
                if mutation_detected
                else redact(str(exc), sensitive)
            ),
            "underlying_error_type": exc.__class__.__name__ if mutation_detected else None,
            "fingerprint_error": redact(fingerprint_error, sensitive) if fingerprint_error else None,
            "source_state_error": redact(source_state_error, sensitive) if source_state_error else None,
            "traceback": redact(original_traceback, sensitive)[-4000:],
            "next_runtime_mode": "production_start",
            "next_artifact_sha256": next_artifact_sha256,
            "next_artifact_before_start_sha256":
                next_artifact_before_start_sha256 or None,
            "next_artifact_after_acceptance_sha256":
                next_artifact_after_acceptance_sha256 or None,
            "next_artifact_after_cleanup_sha256":
                next_artifact_after_cleanup_sha256 or None,
            "next_build_completed": True,
            "source_commit": source_commit or None,
            "tracked_worktree_clean": clean_after_failure,
            "tracked_worktree_fingerprint_before": tracked_before,
            "tracked_worktree_fingerprint_after_build": tracked_after_build,
            "tracked_worktree_fingerprint_after_acceptance": tracked_after_acceptance or None,
            "tracked_worktree_unchanged": not mutation_detected,
            "python_api_started": False,
            "worker_implementation": args.worker_implementation,
            "typescript_worker_started": (
                worker_process_started
                and args.worker_implementation == "typescript"
            ),
            "python_worker_started": False,
            "real_runtime_execution_performed": bool(worker_receipts) and all(
                receipt.get("provider_call_performed") is True and receipt.get("dry_run") is False
                for receipt in worker_receipts.values()
            ),
            "fixture_cleanup": cleanup_receipt or None,
            "fixture_cleanup_error":
                redact(cleanup_error, sensitive) if cleanup_error else None,
            "fixture_cleanup_verified_before_failure_receipt":
                fixture_cleanup_complete,
            "next_process_stop": process_stop_receipt or None,
            "next_process_stop_error": process_stop_error or None,
            "credentials_omitted": True,
        }, sensitive)
        return 1
    finally:
        if next_proc is not None:
            stop_process(next_proc)
            next_proc = None
        if adapter is not None:
            try:
                adapter.close()
            except Exception:
                pass
        if setup is not None:
            try:
                setup.close()
            except Exception:
                pass
        if not fixture_cleanup_complete:
            try:
                cleanup_postgres_fixture(
                    args.postgres_dsn,
                    node,
                    schema,
                    runtime_api_schema,
                    runtime_role,
                    entitlement_admin_role,
                    function_owner_role,
                )
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
