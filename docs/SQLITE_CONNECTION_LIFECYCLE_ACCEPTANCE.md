# SQLite Connection Lifecycle Acceptance

Status: source fix complete; exact-head CI and a later packaged Host remain
separate release gates.

## Failure Found By Local Dogfood

The installed Private Host preview.41 eventually accepted TCP connections on
`127.0.0.1:18878` but returned an empty HTTP response. Read-only process
metadata showed 70 open handles for the main 103 MB SQLite database and 24 for
its WAL while the LaunchAgent soft file-descriptor limit was 256.

The server used `with db() as conn`. Python's SQLite connection context manager
commits or rolls back a transaction, but it does not close the connection when
the block exits. Repeated Human Workspace polling therefore accumulated live
database handles until the Host stopped responding.

## Fix

- `db()` remains the compatibility API for scripts that explicitly call
  `conn.close()`.
- `db_session()` wraps the SQLite transaction context and always closes the
  connection in `finally`.
- All 12 server-managed database context sites now use `db_session()`, including
  GET/POST/PATCH handlers, schema/seed/export helpers, workflow jobs, Agent
  Gateway auth reads, and private restart-audit ingestion.
- No schema, Memory content, task, Run, approval, credential, or database row is
  changed by this lifecycle fix.

## Verification

```bash
python3 scripts/sqlite_connection_lifecycle_smoke.py
python3 scripts/sqlite_pragmas_smoke.py
python3 scripts/sqlite_reliability_smoke.py
python3 scripts/sqlite_concurrency_smoke.py
python3 -m py_compile server.py scripts/*.py
python3 scripts/secret_scan_smoke.py
git diff --check
```

The focused smoke opens 96 managed sessions, proves every connection is closed
on normal and exceptional exit, verifies commit/rollback behavior, rejects any
remaining `with db()` server call site, and checks bounded process file
descriptors on Linux CI.

## Sustained Polling Acceptance

An isolated source server on `127.0.0.1:18938` handled 1,000 concurrent
`GET /api/dashboard/metrics` requests with 16 clients. Every request returned
successfully. On macOS, process file descriptors stayed exactly `41 -> 41`, and
`lsof` reported zero idle handles for the isolated SQLite database both before
and after the request burst.

The server was stopped and the temporary database was removed after the check.
No installed Host database, credential, prompt, response, transcript, or
repository artifact was read or changed.

## Installed Host Boundary

The installed preview.41 predates this source fix and remains susceptible to
the leak after enough API polling. A controlled restart restored the Host after
old operational Host log space was released; no database, Codex conversation,
Docker data, credential, prompt, response, or transcript was removed. Do not
attribute the fixed lifecycle to the installed product until a later exact
commit is packaged, installed, and exercised through sustained Workspace/API
polling.
