# Private Host Doctor Action Acceptance

Status: source implementation and deterministic smoke complete; installed
preview.41 predates this correction.

## Product Problem

`agentops host doctor` previously returned generic setup instructions even when
the production UI, Owner login and the advanced Tailscale profile were already
ready. A healthy Host therefore told an operator to install and sign in to
Tailscale again. This was misleading and increased the apparent setup cost.

## Contract

Doctor actions are now derived from current gates:

- a healthy Host with a running Tailscale identity returns no setup actions;
- a missing custom/development UI returns only the bounded build action, while
  a missing managed UI tells the customer to repair or reinstall the package;
- Tailscale guidance appears only when it is unavailable, stopped or missing a
  DNS identity, and is explicitly labeled as an optional private Console path;
- first-Owner bootstrap and stopped-Host recovery actions remain intact;
- the command remains read-only and does not inspect credentials, modify the
  ledger or change network configuration.

## Verification

```bash
python3 -m py_compile agentops_mis_cli/host.py \
  scripts/private_host_doctor_actions_smoke.py
python3 scripts/private_host_doctor_actions_smoke.py
```

The smoke covers ready, missing-UI, missing-Tailscale, stopped-Tailscale and
Owner-bootstrap states without starting a Host or invoking Tailscale.

A read-only source check was also run against the installed release root:

```bash
AGENTOPS_INSTALL_ROOT="$HOME/.local/share/agentops-mis" \
  ./scripts/agentops host doctor
```

The projection reported all gates ready, Host and Human access ready, Tailscale
running with a DNS identity, and an empty `next_actions` list. This command used
the source implementation with installed package metadata; it did not replace
or mutate the installed release.

## Release Boundary

The currently installed `v1.6.0-private-host-preview.41` remains the authority
for physical runtime evidence and still contains the old generic Doctor copy.
This source correction must be included in a later exact package before an
installed-Host or second-device claim may attribute the behavior to the
product.
