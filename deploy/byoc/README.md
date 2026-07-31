# AgentOps MIS BYOC

This package runs the commercial control plane as Next.js/TypeScript on Node.js
with PostgreSQL 16. It does not start or proxy the Python API and does not use
SQLite as an authority store.

## Prepare

1. Copy `.env.example` to an untracked `.env`.
2. Create private secret files without printing their values:

   ```bash
   install -d -m 700 deploy/byoc/secrets
   umask 077
   openssl rand -hex 32 > deploy/byoc/secrets/postgres-migrator-password
   openssl rand -hex 32 > deploy/byoc/secrets/postgres-runtime-password
   openssl rand -hex 32 > deploy/byoc/secrets/postgres-entitlement-admin-password
   openssl rand -base64 32 > deploy/byoc/secrets/entitlement-operator-password
   openssl rand -hex 32 > deploy/byoc/secrets/human-session-hmac-key
   chmod 600 deploy/byoc/secrets/postgres-migrator-password \
     deploy/byoc/secrets/postgres-runtime-password \
     deploy/byoc/secrets/postgres-entitlement-admin-password \
     deploy/byoc/secrets/entitlement-operator-password \
     deploy/byoc/secrets/human-session-hmac-key
   ```

3. For secret settings, keep only file paths in `.env`:

   ```dotenv
   AGENTOPS_POSTGRES_MIGRATOR_PASSWORD_FILE=./secrets/postgres-migrator-password
   AGENTOPS_POSTGRES_RUNTIME_PASSWORD_FILE=./secrets/postgres-runtime-password
   AGENTOPS_POSTGRES_ENTITLEMENT_ADMIN_PASSWORD_FILE=./secrets/postgres-entitlement-admin-password
   AGENTOPS_ENTITLEMENT_OPERATOR_PASSWORD_FILE=./secrets/entitlement-operator-password
   AGENTOPS_HUMAN_SESSION_HMAC_KEY_FILE=./secrets/human-session-hmac-key
   ```

   Never commit `.env` or the `deploy/byoc/secrets/` directory.

4. Keep `AGENTOPS_BIND_ADDRESS=127.0.0.1` unless TLS is terminated by a trusted
   reverse proxy on the same private deployment boundary.
5. Set `AGENTOPS_ALLOWED_ORIGINS` to the exact HTTPS browser origin.
6. Set `AGENTOPS_ENTITLEMENT_CONTROL_PLANE_URL` to an externally reachable,
   trusted HTTPS URL and `AGENTOPS_ENTITLEMENT_OPERATOR_USERNAME` to the
   intended Human operator. Optionally set
   `AGENTOPS_ENTITLEMENT_CONTROL_PLANE_ORIGIN` to the browser origin used for
   Origin/CSRF binding. These values are routing and identity configuration,
   not secrets.

Do not put a raw PostgreSQL password, Human Session HMAC key, or credentialed
DSN in `.env`. The PostgreSQL container and one-shot migrator receive only the
migrator password. The migrator additionally receives the runtime and
entitlement-admin passwords only long enough to create or rotate those
restricted logins. The control plane receives only the runtime password and
Human Session HMAC key; it cannot read the migrator, entitlement-admin, or
operator password, and the migrator cannot read the Human Session HMAC key.

The database identities are intentionally different:

- `agentops_migrator` owns the application schema and applies the
  checksum-pinned manifest.
- `agentops_runtime` owns no protected relation, cannot alter the schema,
  cannot write `agentops_schema_migrations`, and cannot directly
  `INSERT`, `UPDATE`, `DELETE`, or `TRUNCATE` `run_cost_reservations` or
  `workspace_entitlements`.
- `agentops_entitlement_admin` can execute only the v11 entitlement challenge
  plan/apply API. It has no direct table access to Human credentials,
  entitlements, audit rows, reservations, runs, memberships, or migration
  state.
- `agentops_fn_<schema-hash>` is a passwordless `NOLOGIN`, `NOINHERIT` owner
  derived from the application and runtime API schema names. It owns all
  application `SECURITY DEFINER` functions and bounded API wrappers, but no
  application relations and neither schema's `CREATE` privilege. Provisioning
  explicitly clears any pre-existing password verifier.

Cost reservation writes cross a separately owned
`agentops_runtime_api` schema through five explicitly granted
`SECURITY DEFINER` functions with a fixed search path. The runtime retains
ordinary application table operations but cannot invoke the original owner
functions directly. Production startup and health transactions verify this
role boundary against the PostgreSQL catalog and fail closed if an owner or
over-privileged DSN is supplied.

Provisioning also removes the migrator's global default `PUBLIC EXECUTE` grant
for future functions. Existing and newly created application functions are not
runtime-executable unless the bounded API grants them explicitly. Runtime
readiness verifies the complete application-function allowlist; it contains
only the approval-binding and PreparedAction-lease validation helpers required
by ordinary trigger-backed writes. Before pending migrations execute, the
migrator receives temporary membership and schema `CREATE` for the derived
function owner, allowing upgrades to replace functions already owned by that
identity. The transaction revokes that handoff after migration, rebuilds the
wrappers, transfers application `SECURITY DEFINER` ownership, and revokes both
again before commit. No fourth password or customer-managed secret is required.
Readiness verifies the final role attributes, zero memberships, schema grants,
a database-wide ownership closed set that permits only the expected functions,
wrapper source, fixed search path, and exact executable grantees.
Re-provisioning removes stale non-owner `EXECUTE` grants from application
`SECURITY DEFINER` functions before the transaction commits.
The runtime API schema is verified as an exact eight-function closed set, so an
unregistered wrapper, owner, signature, or executable grantee fails readiness.

Entitlement administration is a high-privilege operator action, not a normal
control-plane request. The optional `entitlement-admin` Compose profile is
one-shot and mounts only the entitlement-admin database password and the Human
operator password. It never receives migrator, runtime, or Human Session HMAC
secrets. It waits for the TypeScript control plane to become healthy, requires
an externally trusted HTTPS control-plane URL (plain HTTP is accepted only for
an explicit loopback test/local URL), and probes the workspace-scoped v11
challenge route before starting the CLI. Do not use the Compose-internal
`http://control-plane` service address as a commercial authentication channel.
A missing route, redirect, transport error, or route that does not advertise
`POST` fails closed before entitlement code runs.

The CLI uses the Human operator login to request a short-lived, single-use
challenge bound to the complete entitlement request, then uses the separate
admin database identity to plan or apply it. It never receives the runtime or
migrator DSN and cannot fall back to the legacy direct-table administration
path when the challenge API is unavailable. Preview a change without
`--confirm` first:

```bash
docker compose --env-file deploy/byoc/.env \
  -f deploy/byoc/compose.yaml \
  --profile entitlement-admin run --rm entitlement-admin \
  --workspace-id ws_customer \
  --operator-user-id usr_owner \
  --edition team_governance \
  --status active \
  --capabilities enrollment_issue,session_issue,run_start \
  --max-agents 20 \
  --max-active-enrollments 20 \
  --max-active-sessions-per-agent 5 \
  --max-concurrent-runs 10 \
  --max-monthly-runs 1000 \
  --max-monthly-cost-usd 1000.000000 \
  --effective-at 2026-08-01T00:00:00.000Z \
  --expires-at 2027-08-01T00:00:00.000Z
```

After reviewing the plan receipt, repeat with `--confirm --expect-absent` for
initial creation, or `--confirm --expected-revision <sha256>` for an update.
The password in `entitlement-operator-password` must match the intended Human
operator's active login credential, and `AGENTOPS_ENTITLEMENT_OPERATOR_USERNAME`
must identify that same account. Never mount either admin secret into the
long-running control-plane service. The preflight does not read an API response
body and neither the entrypoint nor its errors print request URLs, URL
credentials, cookies, CSRF values, operator passwords, or challenge tokens.

The Node services do not depend on the host file UID being `1000`. Their
entrypoint is PID 1 and starts with only the capabilities needed to read the
Compose secret,
copies each required regular non-symlink file through a stable descriptor into
an unpredictable `0700` directory inside the dedicated
`/run/agentops-runtime-secrets` tmpfs, sets the copy to `0400` and `1000:1000`,
then drops all groups and switches to the fixed Node UID/GID before starting
`npm`. It also requires Linux to report zero capabilities in the inheritable,
permitted, effective, and ambient sets, plus `NoNewPrivs=1` after the drop. Any
unreadable, empty, oversized, replaced, or symlinked secret, an unexpected
runtime UID/GID, retained capability, or a failed privilege drop stops startup.
The container-local copies are removed on normal exit and live only in a
size-limited `noexec,nosuid` tmpfs, not in the container writable layer or
PostgreSQL authority volume. A crash or forced container removal therefore
cannot preserve the prepared copies in an image layer. No privileged init
process remains above the entrypoint; after the entrypoint drops its own
identity, it forwards termination signals to the service, removes the prepared
copies after the child exits, and then preserves the child's exit status or
terminating signal.

The control-plane healthcheck uses the same drop-and-verify function before its
loopback request, so the periodic probe does not retain root identity or
effective capabilities and does not read or print the response body.

## Start

```bash
docker compose --env-file deploy/byoc/.env \
  -f deploy/byoc/compose.yaml \
  up --build --detach
```

The one-shot `migrate` service applies the checksum-pinned PostgreSQL manifest
through `AGENTOPS_POSTGRES_MIGRATOR_DSN(_FILE)` or the equivalent migrator
component settings. It then provisions the restricted runtime role and grants
the bounded cost API. The application uses only
`AGENTOPS_POSTGRES_DSN(_FILE)` or the equivalent runtime component settings,
checks the same manifest and catalog fingerprint, verifies the role boundary,
and fails closed if the schema, ownership, or privileges have drifted.

Existing installations that used one PostgreSQL role do not silently continue
with that shared owner. Add all three database password files and role names to the
untracked `.env`, then run the one-shot migrator. It creates the restricted
runtime login transactionally before the control plane is allowed to start.

```bash
curl --fail http://127.0.0.1:3001/api/mis/health
```

Do not run `docker compose down --volumes` against a customer environment. The
named PostgreSQL volume is authority data; backup, upgrade, restore, and
rollback procedures must be completed before image promotion.

## Backup And Restore Drill

Create a permission-restricted backup bundle:

```bash
deploy/byoc/backup.sh backups/agentops-before-upgrade.bundle
```

The output is a new `0700` directory containing `database.dump`,
`SHA256SUMS`, and `COMMITTED`. The script reserves the output directory
atomically, builds the dump and checksum in a random private staging directory,
and publishes `COMMITTED` last. A bundle is valid only after that marker exists.
The script never reuses an existing path, so concurrent writers and symlinked
outputs fail closed without replacing another backup.

Restore into a new isolated database and verify it against the migration
manifest and catalog fingerprint embedded in the current image:

```bash
deploy/byoc/restore-drill.sh backups/agentops-before-upgrade.bundle
```

The drill rejects incomplete, symlinked, or checksum-mismatched bundles before
creating a database. It drops its isolated database after verification and
prints success only after the requested cleanup or retention disposition is
confirmed. Restore or verification failures always trigger cleanup even when
`AGENTOPS_RESTORE_KEEP=true`; a cleanup failure is itself a non-zero result.
Before validation, the three bundle files are copied into a random private
staging directory. After structural checks, the files are sealed `0400` and the
directory is sealed `0500`. The checksum and `pg_restore` both use that staged
read-only object, so replacing the original bundle path after validation cannot
change the restored bytes. The staging directory is removed on success, failure,
or a handled termination signal.

Because the dump deliberately omits ownership and ACLs, restore validation does
not stop at a read-only schema check. The default Compose migrator component
settings are converted beside the staged migrator password into three new
`0400` DSN files targeting the isolated database. The checksum-pinned
migration/provisioning command consumes only the derived migrator DSN while the
runtime and entitlement-admin role passwords remain available for provisioning.
This rebuilds their grants and transfers every bounded `SECURITY DEFINER`
function to the derived `NOLOGIN` function owner. The password inputs are then
removed from the child environment, and the manifest, fingerprint, runtime
boundary, and entitlement-admin boundary checks consume only their respective
derived DSN files. The v4 receipt is emitted only when both role boundaries
explicitly verify the restricted function owner.

Custom Compose deployments may supply
`AGENTOPS_POSTGRES_MIGRATOR_DSN_FILE` to the migrator instead of component
settings. The default BYOC entrypoint rejects credentialed DSN environment
values and rejects a migrator DSN file combined with migrator component
credentials. When the drill selects the isolated restore database, it parses
the staged migrator DSN as a PostgreSQL URL and changes only the database
pathname; query parameters such as `sslmode`, certificate settings, and
connection timeouts are preserved in all three derived DSNs. Component and DSN
file inputs therefore converge on the same file-backed migration and validation
flow. Direct runtime/admin DSNs or password values fail closed. All derived
files are removed on success, failure, or a handled signal, and no full DSN is
copied into an environment variable or printed.

Set a unique `AGENTOPS_RESTORE_DATABASE` and
`AGENTOPS_RESTORE_KEEP=true` only when an operator intends to retain a fully
verified restore for a separately reviewed promotion. The script refuses to
target the configured production database. `AGENTOPS_RESTORE_KEEP` accepts
only `true` or `false`.

The repository contracts exercise these fail-closed paths without a Docker
daemon. They are packaging and offline behavior evidence only; a real
clean-customer Docker/Compose installation and restore drill remain required
before BYOC promotion.
