# openJiuwen compatibility harness

This directory is a no-install, standard-library-only contract spike for a
future openJiuwen `agent-core` worker. It does **not** import or execute
openJiuwen, call a model, load a checkpoint, perform a tool action, or write to
AgentOps MIS.

The upstream candidate is pinned as metadata in
`dependency-manifest.json`: `agent-core` commit
`bf0a3eb2c70fcbae404403530519ca02e7fc4692`, Python `>=3.11,<3.14`,
Apache-2.0 with upstream `NOTICE`. A later distribution must retain the exact
pinned upstream license, notice, attribution, and applicable third-party
notices. No upstream code or notice body is copied here.

## Contract

`protocol.py` defines a canonical JSONL request/event envelope with:

- one complete UTF-8 JSON object per newline-terminated record;
- fixed schemas and event names; unknown fields and types fail closed;
- byte, record, identifier, string, collection, nesting, and payload limits;
- recursive rejection of prompt, response, transcript, secret and
  credential-shaped fields/values;
- ordered event application plus exact duplicate detection;
- in-memory idempotency receipts: same key and effect payload replays the
  original receipt, while changed content fails;
- explicit read-only `ALLOW`, protected-action `ASK`, and unknown/default
  `DENY`. `ASK` creates a permission request only; it is never approval;
- cancellation/resume *protocol receipts* with `effect_performed=false`.

`fake_worker.py` deterministically exercises this contract in memory. Its
cancel/resume receipts do not prove a real process stopped or a real
openJiuwen checkpoint restored.

Run the focused suite from the repository root:

```bash
PYTHONPATH=incubator/research-lab \
  python3 -m unittest discover \
  -s incubator/research-lab/tests \
  -p 'test_openjiuwen_spike.py' -v
```

The real pinned dependency install, minimal agent, callback ordering,
permission host, checkpoint recovery, cancellation, and version equivalence
remain `NOT_RUN` or `UNKNOWN`; see `docs/OPENJIUWEN_COMPATIBILITY_SPIKE.md`.
