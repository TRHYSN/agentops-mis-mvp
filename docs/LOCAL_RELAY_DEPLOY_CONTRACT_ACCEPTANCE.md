# Local Relay Deploy Contract Acceptance

Status: static packaging contract accepted; Relay runtime and deployment remain separate gates

Date: 2026-07-24

## Scope

This slice reserves the installable `agentops-relay` console command for
`agentops_mis_cli.relay_daemon:main`, adds a credential-free systemd template,
and makes both contracts executable through a dependency-free static smoke.

The slice does not import or execute the Relay daemon. It does not bind a
listener, contact a Host, invoke systemd, install or enable a service, provision
credentials, publish DNS, request a certificate, or deploy a public endpoint.

```text
deployed_relay: false
dns_acme: false
```

## Entrypoint Contract

Both package metadata sources define the exact same console script:

```text
agentops-relay = agentops_mis_cli.relay_daemon:main
```

The repository uses a custom offline PEP 517 backend, so changing only
`pyproject.toml` would not change the generated wheel. The backend-generated
wheel is inspected to prove that its `entry_points.txt` contains the exact
mapping. The smoke deliberately does not import the target module, allowing the
daemon implementation to remain an independent code slice.

The wheel installs only the console-script metadata and Python package. It does
not install, load, enable, or start the systemd unit. The source distribution
includes this acceptance document, a credential-free
`packaging/relay/config.example.json`, and the operator-controlled unit
template. The example uses a reserved `.invalid` hostname and path references
only; it is never copied into `/etc` automatically.

## Foreground Daemon Contract

The systemd template freezes this foreground invocation:

```text
/usr/local/bin/agentops-relay \
  check \
  --config /etc/agentops-mis-relay/config.json

/usr/local/bin/agentops-relay \
  serve \
  --config /etc/agentops-mis-relay/config.json
```

The daemon owns the schema and fail-closed validation behind the config path.
That config references separately provisioned connector TLS files, per-route key
files, persistent state, and bounded status paths. Deployment must keep immutable
config and credential files under `/etc/agentops-mis-relay`, route state under
`/var/lib/agentops-mis-relay`, and runtime status under
`/run/agentops-mis-relay`.

The unit contains no route, hostname, IP address, port, certificate, tunnel key,
credential-file name, environment variable, credential value, DNS provider, or
ACME configuration. No secret value belongs in the unit, repository, package
metadata, process arguments, or this acceptance record.

## Systemd Boundary

The template:

- runs as the dedicated `agentops-relay` user and group;
- grants only `CAP_NET_BIND_SERVICE` for an operator-configured low port;
- creates private `0700` runtime and state directories under systemd ownership;
- preserves the runtime directory across a controlled stop so activation
  rollback can verify the service-owned stopped-status leaf before explicit
  cleanup;
- applies `UMask=0077`, a bounded file-descriptor/task budget, and a five-second
  on-failure restart delay;
- restricts writable paths to the Relay state and runtime directories;
- keeps `/etc/agentops-mis-relay` read-only after service start;
- allows only Unix, IPv4, and IPv6 socket families;
- sends bounded daemon output to journald under `agentops-mis-relay`;
- uses `SIGTERM` and a bounded stop timeout for foreground shutdown.

The unit intentionally has no shell, `Environment=`, `EnvironmentFile=`,
systemd credential payload, network endpoint, or automatic configuration
provisioning. Installing the file, creating the service account, setting
ownership and permissions, running `daemon-reload`, enabling the unit, opening a
firewall, and rollback remain explicit deployment operations outside this
static template.

## Static Verification

Run from the repository root:

```bash
python3 -m py_compile \
  agentops_mis_cli/_build_backend.py \
  scripts/relay_deploy_contract_smoke.py
python3 scripts/relay_deploy_contract_smoke.py
git diff --check
```

The smoke:

- parses the `project.scripts` entry without importing the daemon;
- loads the offline build backend and builds a wheel plus source distribution
  entirely inside a temporary directory;
- builds each artifact twice, requires byte-for-byte identity, and verifies
  normalized archive timestamps, owners, groups, and file modes;
- requires root-level sdist `PKG-INFO` to match wheel metadata and verifies
  prepared wheel metadata, including unknown backend metadata, round-trips
  byte-for-byte without an invalid pre-generated `RECORD`;
- verifies the wheel entrypoint and verifies that pip installation cannot
  implicitly install the systemd unit;
- verifies the source distribution carries the systemd template and this
  acceptance document, release/install/status acceptance, the pure activation
  plan-core acceptance, the strict config-parser acceptance, the remaining
  activation contract, and the credential-free config example;
- parses the unit without invoking `systemctl` or `systemd-analyze`;
- checks the exact foreground arguments, dedicated identity, restart policy,
  private directories, capability bound, address families, and writable paths;
- rejects embedded credentials, endpoints, IP addresses, Tailscale, DNS/ACME
  configuration, and environment-based secret injection.

## Remaining Gates

- retain `LOCAL_RELAY_DAEMON_ACCEPTANCE.md` as the separate runtime proof for
  config, secret, state, status, listener, SNI, authenticated route ownership,
  bounded forwarding, restart, signal shutdown, and privacy behavior;
- package an explicit installer or infrastructure definition with rollback;
- provision real server, firewall, DNS, public certificates, route credentials,
  monitoring, and retention policy;
- complete physical stock-browser acceptance through the deployed Relay.

This slice is packaging evidence only. Multi-Host runtime evidence is recorded
separately and locally; neither slice may set Host `remote_ready`, and neither
is evidence of a public Relay, DNS/ACME lifecycle, certificate lifecycle, or
the ordinary browser-only product gate.
