#!/usr/bin/env python3
"""Exercise the exact BYOC restore guardian against a disposable PostgreSQL."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import textwrap
import time
import urllib.parse
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESTORE_GUARDIAN = (
    ROOT / "deploy" / "byoc" / "postgres-restore-guardian.sh"
)
BEGIN = "# AGENTOPS_RESTORE_GUARDIAN_SCRIPT_BEGIN"
END = "# AGENTOPS_RESTORE_GUARDIAN_SCRIPT_END"
LEASE_KEY = 7157544864185932631
CONTRACT = "agentops_restore_guardian_real_postgres_v1"


class SmokeError(RuntimeError):
    pass


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def require_binary(name: str) -> str:
    executable = shutil.which(name)
    if executable is None:
        raise SmokeError(f"{name}_unavailable")
    return executable


def parse_connection(dsn: str) -> tuple[dict[str, str], str]:
    parsed = urllib.parse.urlsplit(dsn)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise SmokeError("postgres_dsn_invalid")
    if not parsed.hostname or not parsed.username:
        raise SmokeError("postgres_dsn_incomplete")
    database = parsed.path.lstrip("/")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", database):
        raise SmokeError("postgres_database_invalid")
    env = os.environ.copy()
    env.update(
        {
            "PGHOST": parsed.hostname,
            "PGPORT": str(parsed.port or 5432),
            "PGUSER": urllib.parse.unquote(parsed.username),
            "PGPASSWORD": urllib.parse.unquote(parsed.password or ""),
            "PGCONNECT_TIMEOUT": "5",
        }
    )
    return env, database


def checked_identifier(value: str) -> str:
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", value):
        raise SmokeError("generated_database_identifier_invalid")
    return f'"{value}"'


def psql(
    executable: str,
    env: dict[str, str],
    database: str,
    sql: str,
    *,
    variables: dict[str, str] | None = None,
    timeout: int = 20,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    command = [
        executable,
        "--no-psqlrc",
        "--no-password",
        "--dbname",
        database,
        "--set",
        "ON_ERROR_STOP=1",
        "--tuples-only",
        "--no-align",
        "--quiet",
    ]
    for key, value in sorted((variables or {}).items()):
        command.extend(["--set", f"{key}={value}"])
    result = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        input=sql.encode(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    if check and result.returncode != 0:
        raise SmokeError(
            "psql_failed:"
            f"{result.returncode}:stdout={digest(result.stdout)}:"
            f"stderr={digest(result.stderr)}"
        )
    return result


def scalar(
    executable: str,
    env: dict[str, str],
    database: str,
    sql: str,
    *,
    variables: dict[str, str] | None = None,
) -> str:
    return psql(
        executable,
        env,
        database,
        sql,
        variables=variables,
    ).stdout.decode().strip()


def extract_guardian_script() -> str:
    source = RESTORE_GUARDIAN.read_text(encoding="utf-8")
    if source.count(BEGIN) != 1 or source.count(END) != 1:
        raise SmokeError("restore_guardian_markers_invalid")
    script = textwrap.dedent(source).strip()
    if "pg_restore" not in script or "guardian_watch_pid" not in script:
        raise SmokeError("restore_guardian_script_incomplete")
    return script


def create_database(
    psql_bin: str,
    env: dict[str, str],
    admin_database: str,
    database: str,
) -> None:
    psql(
        psql_bin,
        env,
        admin_database,
        f"CREATE DATABASE {checked_identifier(database)};\n",
    )


def drop_database(
    psql_bin: str,
    env: dict[str, str],
    admin_database: str,
    database: str,
) -> subprocess.CompletedProcess[bytes]:
    return psql(
        psql_bin,
        env,
        admin_database,
        f"DROP DATABASE IF EXISTS {checked_identifier(database)} WITH (FORCE);\n",
        check=False,
    )


def cleanup_databases(
    psql_bin: str,
    env: dict[str, str],
    admin_database: str,
    databases: tuple[str, ...],
) -> None:
    cleanup_failed = False
    for database in databases:
        try:
            result = drop_database(
                psql_bin,
                env,
                admin_database,
                database,
            )
            cleanup_failed = cleanup_failed or result.returncode != 0
        except (OSError, SmokeError, subprocess.SubprocessError):
            cleanup_failed = True

    try:
        remaining = scalar(
            psql_bin,
            env,
            admin_database,
            "SELECT count(*) FROM pg_database "
            "WHERE datname IN (:'source_database', :'target_database');\n",
            variables={
                "source_database": databases[0],
                "target_database": databases[1],
            },
        )
        cleanup_failed = cleanup_failed or remaining != "0"
    except (OSError, SmokeError, subprocess.SubprocessError):
        cleanup_failed = True

    if cleanup_failed:
        raise SmokeError("fixture_database_cleanup_failed")


def run_guardian(
    script: str,
    env: dict[str, str],
    database: str,
    payload: bytes,
    *,
    path_prefix: Path | None = None,
    timeout: int = 30,
) -> subprocess.CompletedProcess[bytes]:
    child_env = env.copy()
    child_env["POSTGRES_USER"] = env["PGUSER"]
    if path_prefix is not None:
        child_env["PATH"] = f"{path_prefix}:{child_env.get('PATH', '')}"
    return subprocess.run(
        ["sh", "-ceu", script, "sh", database],
        cwd=ROOT,
        env=child_env,
        input=payload,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )


def assert_release(
    psql_bin: str,
    env: dict[str, str],
    admin_database: str,
) -> None:
    released = scalar(
        psql_bin,
        env,
        admin_database,
        "SELECT CASE WHEN pg_try_advisory_lock("
        f"{LEASE_KEY}) THEN pg_advisory_unlock({LEASE_KEY}) ELSE false END;\n",
    )
    if released != "t":
        raise SmokeError("restore_database_lease_not_released")
    guardians = scalar(
        psql_bin,
        env,
        admin_database,
        "SELECT count(*) FROM pg_stat_activity "
        "WHERE application_name LIKE 'agentops_restore_guardian_%';\n",
    )
    if guardians != "0":
        raise SmokeError("restore_guardian_backend_residue")


def guardian_identity(
    psql_bin: str,
    env: dict[str, str],
    admin_database: str,
    *,
    timeout: float = 10.0,
) -> tuple[str, str, str]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = scalar(
            psql_bin,
            env,
            admin_database,
            "SELECT pid::text || '|' || backend_start::text || '|' "
            "|| application_name FROM pg_stat_activity "
            "WHERE application_name LIKE 'agentops_restore_guardian_%' "
            "ORDER BY backend_start DESC LIMIT 1;\n",
        )
        parts = value.split("|", 2)
        if len(parts) == 3 and parts[0].isdigit():
            return parts[0], parts[1], parts[2]
        time.sleep(0.05)
    raise SmokeError("restore_guardian_not_observed")


def terminate_exact_guardian(
    psql_bin: str,
    env: dict[str, str],
    admin_database: str,
    identity: tuple[str, str, str],
) -> None:
    pid, backend_start, application_name = identity
    variables = {
        "guardian_pid": pid,
        "guardian_backend_start": backend_start,
        "guardian_application_name": application_name,
    }
    wrong = scalar(
        psql_bin,
        env,
        admin_database,
        "SELECT COALESCE((SELECT pg_terminate_backend(pid, 5000) "
        "FROM pg_stat_activity WHERE pid=:'guardian_pid'::integer "
        "AND backend_start='2000-01-01 UTC'::timestamptz "
        "AND application_name=:'guardian_application_name'), false);\n",
        variables=variables,
    )
    if wrong != "f":
        raise SmokeError("wrong_guardian_identity_terminated_backend")
    still_present = scalar(
        psql_bin,
        env,
        admin_database,
        "SELECT count(*) FROM pg_stat_activity "
        "WHERE pid=:'guardian_pid'::integer "
        "AND backend_start=:'guardian_backend_start'::timestamptz "
        "AND application_name=:'guardian_application_name';\n",
        variables=variables,
    )
    if still_present != "1":
        raise SmokeError("guardian_missing_after_wrong_identity")
    terminated = scalar(
        psql_bin,
        env,
        admin_database,
        "SELECT pg_terminate_backend(pid, 5000) FROM pg_stat_activity "
        "WHERE pid=:'guardian_pid'::integer "
        "AND backend_start=:'guardian_backend_start'::timestamptz "
        "AND application_name=:'guardian_application_name' "
        "AND backend_type='client backend' "
        "AND datname=:'admin_database' AND usename=current_user;\n",
        variables={**variables, "admin_database": admin_database},
    )
    if terminated != "t":
        raise SmokeError("exact_guardian_termination_failed")


def start_fake_restore(
    script: str,
    env: dict[str, str],
    database: str,
    fake_bin: Path,
    pid_file: Path,
) -> subprocess.Popen[bytes]:
    child_env = env.copy()
    child_env.update(
        {
            "POSTGRES_USER": env["PGUSER"],
            "PATH": f"{fake_bin}:{child_env.get('PATH', '')}",
            "AGENTOPS_FAKE_RESTORE_PID_FILE": str(pid_file),
        }
    )
    return subprocess.Popen(
        ["sh", "-ceu", script, "sh", database],
        cwd=ROOT,
        env=child_env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=False,
    )


def wait_nonzero(process: subprocess.Popen[bytes], *, timeout: float = 10.0) -> None:
    try:
        returncode = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        process.kill()
        process.wait(timeout=5)
        raise SmokeError("restore_guardian_failure_not_supervised") from exc
    if returncode == 0:
        raise SmokeError("restore_guardian_failure_unexpected_success")


def wait_pid_gone(pid: int, *, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    raise SmokeError("restore_process_residue")


def wait_file(path: Path, *, timeout: float = 10.0) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file():
            value = path.read_text(encoding="ascii").strip()
            if value.isdigit():
                return int(value)
        time.sleep(0.05)
    raise SmokeError("fake_restore_pid_not_observed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--postgres-dsn",
        default=os.environ.get("AGENTOPS_POSTGRES_DSN", ""),
    )
    args = parser.parse_args()
    if not args.postgres_dsn:
        raise SmokeError("postgres_dsn_required")

    psql_bin = require_binary("psql")
    pg_dump_bin = require_binary("pg_dump")
    require_binary("pg_restore")
    env, configured_database = parse_connection(args.postgres_dsn)
    admin_database = "postgres"
    script = extract_guardian_script()
    token = secrets.token_hex(5)
    source_database = f"agentops_guardian_source_{token}"
    target_database = f"agentops_guardian_target_{token}"

    with tempfile.TemporaryDirectory(prefix="agentops-guardian-smoke-") as raw_tmp:
        temporary = Path(raw_tmp)
        dump_path = temporary / "database.dump"
        fake_bin = temporary / "bin"
        fake_bin.mkdir(mode=0o700)
        fake_restore = fake_bin / "pg_restore"
        fake_restore.write_text(
            "#!/bin/sh\n"
            "set -eu\n"
            "printf '%s' \"$$\" > \"$AGENTOPS_FAKE_RESTORE_PID_FILE\"\n"
            "exec sleep 30\n",
            encoding="ascii",
        )
        fake_restore.chmod(0o700)

        try:
            for database in (source_database, target_database):
                drop_database(psql_bin, env, admin_database, database)
                create_database(psql_bin, env, admin_database, database)
            psql(
                psql_bin,
                env,
                source_database,
                "CREATE TABLE guardian_probe("
                "id integer PRIMARY KEY, value text NOT NULL);\n"
                "INSERT INTO guardian_probe VALUES (1, 'restored');\n",
            )
            dump = subprocess.run(
                [
                    pg_dump_bin,
                    "--no-password",
                    "--format",
                    "custom",
                    "--file",
                    str(dump_path),
                    source_database,
                ],
                cwd=ROOT,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=30,
                check=False,
            )
            if dump.returncode != 0:
                raise SmokeError(
                    "pg_dump_failed:"
                    f"{dump.returncode}:stderr={digest(dump.stderr)}"
                )
            restored = run_guardian(
                script,
                env,
                target_database,
                dump_path.read_bytes(),
            )
            if restored.returncode != 0:
                raise SmokeError(
                    "real_restore_failed:"
                    f"{restored.returncode}:stdout={digest(restored.stdout)}:"
                    f"stderr={digest(restored.stderr)}"
                )
            if scalar(
                psql_bin,
                env,
                target_database,
                "SELECT value FROM guardian_probe WHERE id=1;\n",
            ) != "restored":
                raise SmokeError("restored_probe_missing")
            assert_release(psql_bin, env, admin_database)

            drop_database(psql_bin, env, admin_database, target_database)
            create_database(psql_bin, env, admin_database, target_database)
            invalid = run_guardian(
                script,
                env,
                target_database,
                b"invalid-dump",
            )
            if invalid.returncode == 0:
                raise SmokeError("invalid_dump_unexpected_success")
            assert_release(psql_bin, env, admin_database)

            pid_file = temporary / "fault-restore.pid"
            fault = start_fake_restore(
                script,
                env,
                target_database,
                fake_bin,
                pid_file,
            )
            fake_pid = wait_file(pid_file)
            identity = guardian_identity(
                psql_bin,
                env,
                admin_database,
            )
            terminate_exact_guardian(
                psql_bin,
                env,
                admin_database,
                identity,
            )
            wait_nonzero(fault)
            wait_pid_gone(fake_pid)
            assert_release(psql_bin, env, admin_database)

            kill_pid_file = temporary / "kill-restore.pid"
            killed_parent = start_fake_restore(
                script,
                env,
                target_database,
                fake_bin,
                kill_pid_file,
            )
            orphan_candidate_pid = wait_file(kill_pid_file)
            _, _, application_name = guardian_identity(
                psql_bin,
                env,
                admin_database,
            )
            nonce = application_name.removeprefix(
                "agentops_restore_guardian_"
            )
            lease_directory = Path(tempfile.gettempdir()) / nonce
            killed_parent.kill()
            killed_parent.wait(timeout=5)
            wait_pid_gone(orphan_candidate_pid)
            deadline = time.monotonic() + 10
            while lease_directory.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            if lease_directory.exists():
                raise SmokeError("restore_lease_directory_residue")
            assert_release(psql_bin, env, admin_database)
        finally:
            cleanup_databases(
                psql_bin,
                env,
                admin_database,
                (source_database, target_database),
            )

    print(
        json.dumps(
            {
                "contract": CONTRACT,
                "ok": True,
                "configured_database_contacted": configured_database,
                "real_postgres_restore_verified": True,
                "invalid_dump_failed": True,
                "guardian_backend_start_bound": True,
                "wrong_identity_preserved_guardian": True,
                "exact_guardian_termination_supervised": True,
                "wrapper_sigkill_released_fifo_lease": True,
                "restore_process_zero_residue": True,
                "guardian_backend_zero_residue": True,
                "fixture_database_cleanup_verified": True,
                "credentials_omitted": True,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SmokeError as exc:
        print(
            json.dumps(
                {
                    "contract": CONTRACT,
                    "ok": False,
                    "error_code": str(exc),
                    "credentials_omitted": True,
                },
                sort_keys=True,
            )
        )
        raise SystemExit(1)
