# Research Lab AgentOps CLI

The root AgentOps CLI exposes Research Lab through a thin local delegation
layer. Experiment parsing, execution, storage, and MIS evidence mapping remain
owned by the `agentops-research-lab` package.

## Commands

```bash
agentops experiment validate --spec experiment.json
agentops experiment run --spec experiment.json --state-dir .research-lab
agentops experiment run --spec experiment.json --state-dir .research-lab --confirm-run
agentops experiment show --experiment-id exp_example --state-dir .research-lab
agentops experiment sync --experiment-id exp_example --state-dir .research-lab
agentops experiment sync --experiment-id exp_example --state-dir .research-lab --confirm-sync
```

`run` and `sync` preserve Research Lab's safe defaults. Without
`--confirm-run` or `--confirm-sync`, they only request a dry-run plan.

Connection and workspace options remain root AgentOps options:

```bash
agentops \
  --base-url http://127.0.0.1:8787 \
  --workspace-id local-demo \
  experiment sync \
  --experiment-id exp_example
```

## Resolution And Safety

The delegate is resolved in this order:

1. Executable configured by `AGENTOPS_RESEARCH_LAB_BIN`.
2. Installed `research-lab` executable on `PATH`.
3. Repository module under `incubator/research-lab`.

If none is available, the command fails with an install hint. Passwords,
unrelated tokens, credentials, private keys, and the AgentOps config path are
not forwarded to the Research Lab child process. A resolved
`AGENTOPS_API_KEY` is forwarded only in the `sync` child environment so a
protected local MIS Host can authenticate it; it is never placed on the child
argument list or emitted. Child JSON and error text pass through the shared
CLI redaction boundary.

Install the local package when using AgentOps outside this repository:

```bash
python3 -m pip install -e incubator/research-lab
```
