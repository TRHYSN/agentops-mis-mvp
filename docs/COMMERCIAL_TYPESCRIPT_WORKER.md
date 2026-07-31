# Commercial TypeScript Worker

## Ownership Boundary

The commercial Worker is implemented in `ui/next-app/src/worker/` and runs on
Node.js 20 or newer. It talks only to the production
`/api/mis/agent-gateway/*` contract and never opens PostgreSQL directly.

Python and SQLite are not dependencies of this Worker. They remain available
for Free Local compatibility and trusted acceptance orchestration only.

The Worker currently owns the governed summary workflow:

1. pull and claim one workspace-bound task
2. retrieve bounded Knowledge evidence
3. submit and verify an Agent Plan
4. start a run
5. call real Hermes or OpenClaw after explicit confirmation
6. persist Runtime Event, Tool Call, Evaluation, Artifact, candidate Memory,
   Audit, and Plan Evidence Manifest records
7. request Human customer-delivery approval only when current evidence passes
8. publish a bounded Worker heartbeat

External writes remain fail closed. The Worker records the intent without
calling the provider until a runtime-specific PreparedAction owner can bind
prepare, Human approval, claim, execution, and terminal reconciliation.

## Run One Task

Install and verify the Next application first:

```bash
cd ui/next-app
npm ci
npm run typecheck
npm run test:commercial-worker-contract
```

Provide the Agent credential through the environment. Credentials are rejected
as command-line arguments and are never written to the Worker receipt.

```bash
export AGENTOPS_BASE_URL="https://mis.example.com"
export AGENTOPS_WORKSPACE_ID="workspace-id"
export AGENTOPS_AGENT_ID="agent-id"
export AGENTOPS_AGENT_TOKEN="<agent-token>"
export AGENTOPS_RUN_ESTIMATED_COST_USD="1.000000"
```

Run Hermes:

```bash
npm run worker:commercial -- \
  --adapter hermes \
  --estimated-cost-usd "$AGENTOPS_RUN_ESTIMATED_COST_USD" \
  --confirm-run
```

Run OpenClaw:

```bash
export OPENCLAW_BIN="$(command -v openclaw)"
npm run worker:commercial -- \
  --adapter openclaw \
  --estimated-cost-usd "$AGENTOPS_RUN_ESTIMATED_COST_USD" \
  --confirm-run
```

Loopback HTTP is accepted only with an explicit local-development gate:

```bash
export AGENTOPS_BASE_URL="http://127.0.0.1:3001"
export AGENTOPS_ALLOW_INSECURE_LOOPBACK=true
```

Do not use that gate for hosted or shared deployments.

## Daemon Mode

The daemon uses the same one-task transaction repeatedly and stops cleanly on
`SIGINT` or `SIGTERM`.

```bash
npm run worker:commercial -- \
  --adapter hermes \
  --estimated-cost-usd "$AGENTOPS_RUN_ESTIMATED_COST_USD" \
  --confirm-run \
  --daemon \
  --poll-interval-ms 5000
```

`--max-tasks` can bound a maintenance or acceptance run. High or critical risk
tasks require `--allow-high-risk`; external-write detection still remains
PreparedAction-gated.

The estimate is reserved transactionally against the workspace's concurrent,
monthly-run, and monthly-cost limits. It must be positive, cannot be supplied
as start-time `cost_usd`, and terminal heartbeat cost cannot exceed the
reservation. Until an adapter supplies a trusted provider billing receipt, the
Worker settles the approved estimate instead of reporting an unverified zero.
The estimate is a trusted Worker-side worst-case bound, not an Agent Gateway
token's provider-spend authority. Provider credentials remain inside the
Worker/runtime boundary, and the Gateway rejects any observed or settled cost
above the reservation.

## Evidence Semantics

A successful receipt requires all of the following:

- `provider_call_performed=true`
- `dry_run=false`
- governed Knowledge evidence consumed
- current Plan Evidence Manifest verification passed
- all required ledger records persisted

If the provider ran but later evidence persistence fails, the Worker returns:

- `provider_call_performed=true`
- `ledger_evidence_complete=false`
- `manual_reconciliation_required=true`
- no Plan Evidence Manifest or customer-delivery approval claim

That state must be reconciled before any retry. Mock adapters and deterministic
contracts are CI evidence only; commercial product acceptance requires frozen
source plus explicitly confirmed real Hermes and OpenClaw runs.

## Real Runtime Acceptance

Run the frozen-source acceptance harness against an isolated PostgreSQL
database and the production Next.js server. Its current receipt contract is
`nextjs_postgres_real_worker_human_review_v4`:

```bash
python3 scripts/nextjs_postgres_real_worker_human_review_smoke.py \
  --postgres-dsn "postgresql://<user>:<password>@127.0.0.1:<port>/<database>" \
  --worker-implementation typescript \
  --adapter hermes \
  --adapter openclaw
```

The Python process is test orchestration only. A passing commercial receipt
must report the TypeScript Worker started, the Python Worker and Python API did
not start, a real provider call ran with `dry_run=false`, and `source_commit`
matches the clean candidate `HEAD`. The harness rejects tracked or untracked
worktree changes before execution and requires the tracked source fingerprint
and Git identity to remain unchanged for the full run.
