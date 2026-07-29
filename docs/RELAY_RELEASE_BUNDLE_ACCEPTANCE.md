# Relay Release Bundle Acceptance

## Scope

This slice builds one deterministic, offline Relay release archive from an
immutable `git archive HEAD` snapshot of the committed Relay source inputs.
It does not install software, modify
system services, contact a network endpoint, provision credentials, or claim a
public Relay deployment.

The builder uses the repository's dependency-free PEP 517 backend to create the
wheel. It then normalizes wheel ZIP metadata before placing the wheel in a
canonical tar archive and a gzip stream with `mtime=0`.

## Build Command

An output directory is mandatory:

```bash
output_dir="$(mktemp -d)"
python3 scripts/build_relay_release.py --output-dir "$output_dir"
```

The builder refuses:

- an omitted `--output-dir`;
- an existing output archive;
- repository-local output that is not ignored by Git;
- every output path under the repository's `dist/`;
- dirty or untracked source inputs that would make `git_commit` provenance
  misleading.

Wheel, systemd, and config bytes are read only from the selected Git commit
snapshot. Live worktree files and ignored Python files are never build inputs,
so a concurrent worktree edit cannot change the bytes attributed to the
manifest's `git_commit`.

It never uses `dist/` as an intermediate directory. Wheel construction happens
under an operating-system temporary directory. The final archive is fsynced
under a private temporary name, linked into place without overwriting an
existing release, and published as mode `0644`.

## Archive Contract

The archive has one versioned root and exactly these payload classes:

```text
agentops-mis-relay-<version>/
  wheel/<wheel-name>.whl
  systemd/agentops-mis-relay.service
  config/config.example.json
  manifest.json
  SHA256SUMS
```

`manifest.json` has only:

- `schema`;
- `version`;
- full `git_commit`;
- `files`, containing path, byte size, and SHA-256 for the wheel, systemd unit,
  and config example.

`SHA256SUMS` covers those three payloads plus `manifest.json`. It intentionally
does not hash itself, avoiding a circular checksum definition.

The manifest contains no build time, hostname, local path, token, endpoint, or
database field. Tar ownership, names, modes, member order, and mtimes are
normalized. The gzip filename and timestamp are omitted.

## Verification

Run:

```bash
PYTHONPYCACHEPREFIX="$(mktemp -d)" \
  python3 -m py_compile \
  scripts/build_relay_release.py \
  scripts/relay_release_bundle_smoke.py
python3 scripts/relay_release_bundle_smoke.py
git diff --check
```

The smoke test:

1. creates an isolated temporary HOME, cache, and temp directory;
2. passes no credentials from the parent environment;
3. blocks network calls and rejects every builder subprocess except local
   `git` provenance reads;
4. verifies explicit-output, Git-ignore, and repository-`dist/` refusal;
5. builds into two distinct temporary output directories;
6. requires byte-identical archives and identical SHA-256 values;
7. proves an existing archive cannot be overwritten and no atomic-write
   temporary file remains;
8. validates normalized tar/gzip metadata, manifest provenance, every declared
   file hash, and `SHA256SUMS`;
9. injects an ignored live-worktree Python canary and proves it cannot enter
   the exact-commit wheel;
10. scans expanded bundle content for local machine paths and common credential
   value patterns;
11. requires repository status to be unchanged.

## Acceptance Result

The current smoke output is the authoritative acceptance receipt. It reports
the exact committed HEAD and bundle SHA-256 for that run without embedding a
stale self-reference in this source document:

- `py_compile`: passed;
- `relay_release_bundle_smoke.py`: passed with no failures;
- two archive builds were byte-identical;
- archive SHA-256 and full Git commit are emitted by the smoke;
- existing `relay_deploy_contract_smoke.py`: passed;
- tracked and candidate-file whitespace checks: passed;
- repository status remained unchanged except for the three intended new files.

Test archives and isolated guard files were created only in operating-system
temporary directories and were removed by the smoke.

## Boundaries

This is a release-artifact construction slice only. It does not provide:

- Linux account or directory provisioning;
- systemd install, start, upgrade, rollback, or uninstall;
- route-key or TLS material generation and rotation;
- firewall, DNS, ACME, or public endpoint setup;
- a real Linux VM or second-device browser acceptance.

Those lifecycle and public-network gates remain separate work.
