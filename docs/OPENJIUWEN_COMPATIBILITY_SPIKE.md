# openJiuwen compatibility spike ADR

Status: Proposed compatibility harness

Canonical: false

Real openJiuwen execution: `NOT_RUN`

## Decision

Use a managed Python 3.11 subprocess with bounded canonical JSONL as the first
candidate composition boundary for openJiuwen `agent-core`. Keep AgentOps MIS
as the only authority for tasks, runs, approvals, prepared actions, evidence,
memory review, and audit. The current slice is an executable fake-worker
contract only; installing or calling openJiuwen requires a separately reviewed
dependency action.

The pin is `openJiuwen-ai/agent-core@bf0a3eb2c70fcbae404403530519ca02e7fc4692`
(2026-08-08). Official metadata observed for this decision records GitHub
release `v0.1.16`, PyPI version `0.1.16.post2`, Python
`>=3.11,<3.14`, and Apache-2.0 plus upstream
`Open_Source_Software_Notice.txt`.
Commit-to-PyPI equivalence, transitive dependencies, installability, callback
order, and real runtime semantics remain `UNKNOWN` or `NOT_RUN`.

## Why subprocess first

In-process composition remains the long-term preference only if dependency and
isolation checks pass. It is not proven here. A managed subprocess gives the
first real spike a narrow Python/runtime boundary and lets the parent reject
malformed, oversized, secret-bearing, duplicate, conflicting, or out-of-order
records before mapping any event into first-party MIS objects. A fork is not
justified: no missing hook or upstream patch has been demonstrated. HTTP is
also deferred because no remote service boundary is needed for this spike.

## Protocol and authority boundary

The fake contract accepts only `action.propose`, `cancel`, and `resume` request
envelopes. It derives permission locally; a caller cannot supply an approval.
Two read-only metadata/evidence actions are explicitly allowed, known protected
actions ask, and explicit or unknown actions deny. An ASK receipt carries a
permission-request ID and `human_approval` requirement, never an approval fact.

Events are sequence-bound per request and deduplicated by event ID plus
canonical content. Idempotency keys bind operation and effect-bearing payload:
an exact retry replays the original receipt; changed content fails. Cancel and
resume receipts are intentionally labelled no-effect. The parent must not
interpret them as a stopped process or restored runtime snapshot.

Receipt commit recomputes the complete expected event sequence from the
validated request. Action ID/type, classifier decision/reason, deterministic
event IDs, terminal event, permission-request ID, and cancel/resume target are
all exact-bound. Individually valid events cannot be recombined into a forged
receipt for another action.

The wire decoder accepts only its exact canonical UTF-8 encoding with one
terminal LF; alternate key order, whitespace, escapes, CRLF, or a partial
record fail closed. Unpaired Unicode surrogates are rejected before canonical
encoding and produce only a bounded error code. A worker stream holds at most 64 idempotency receipts, 64
request/event streams, three contiguous events per receipt, and 192 event
identities. It does not evict or silently forget authority-relevant
deduplication state: exhaustion fails closed, and a new managed worker process
begins a new bounded stream.

Raw prompts, model responses, transcripts, messages, credentials, secrets,
tokens, private keys, or checkpoint bodies are forbidden on this boundary.
Only bounded identifiers, summaries, references, decisions, hashes, and
cursors may cross it. openJiuwen working memory and checkpoint storage remain
runtime-private, non-canonical inputs. Upstream checkpoint documentation uses
trusted pickle-backed SQLite/Shelve (with a Redis extension) and warns against
concurrent execution of one session; therefore checkpoint bytes must never be
accepted from an untrusted protocol peer or treated as MIS evidence.

Sensitive field names are split at camel-case and arbitrary delimiters. Every
contiguous token range is checked against exact known compounds, so trailing
descriptors cannot hide an API key, password, authorization, credential, or
private-key field. Matching exact token compounds avoids rejecting unrelated
words such as `passage`, `author`, `credentialing`, or `secretary`.

## License and notice boundary

This harness copies no upstream code and does not install a distribution. If a
later approved slice vendors, packages, or distributes `agent-core`, it must
retrieve the exact pinned source, retain its Apache-2.0 `LICENSE`, upstream
`Open_Source_Software_Notice.txt`, attribution, and applicable third-party
notices, then verify those bytes in release/SBOM evidence. Metadata in the
manifest is not a substitute for carrying the required notice files.

## Verified upstream reference surface

The official pinned sources describe `ReActAgent.configure`, `@tool`,
`Runner.resource_mgr.add_tool`, ability registration, `Runner.run_agent` /
`agent.invoke`, tool start/completion/error/stream/parse/invoke callbacks,
`TOOL_AUTH`, allow/ask/deny and `ToolPermissionHost`, and checkpoint support.
They also show that disabling permission or host checks can permit execution,
so the outer protocol remains fail closed regardless of upstream defaults. The
pinned commit includes a cancellation/unexpected-exception context-persistence
fix, but this harness does not claim to have executed it.

Official refs:

- [Pinned source](https://github.com/openJiuwen-ai/agent-core/tree/bf0a3eb2c70fcbae404403530519ca02e7fc4692)
- [Release v0.1.16](https://github.com/openJiuwen-ai/agent-core/releases/tag/v0.1.16)
- [PyPI project](https://pypi.org/project/openjiuwen/)
- [Building ReActAgent](https://github.com/openJiuwen-ai/agent-core/blob/bf0a3eb2c70fcbae404403530519ca02e7fc4692/docs/en/2.Development%20Guide/Agents/Building%20ReActAgent.md)
- [Custom tools](https://github.com/openJiuwen-ai/agent-core/blob/bf0a3eb2c70fcbae404403530519ca02e7fc4692/docs/en/2.Development%20Guide/Basic%20Functions/Custom%20Tools.md)
- [Tool permissions and host integration](https://github.com/openJiuwen-ai/agent-core/blob/bf0a3eb2c70fcbae404403530519ca02e7fc4692/docs/en/2.Development%20Guide/Tool%20permissions%20and%20host%20integration.md)
- [Checkpoint mechanism](https://github.com/openJiuwen-ai/agent-core/blob/bf0a3eb2c70fcbae404403530519ca02e7fc4692/docs/en/2.Development%20Guide/Advanced%20Usage/Checkpointer%20Checkpoint%20Mechanism.md)
- [Callback event source](https://github.com/openJiuwen-ai/agent-core/blob/bf0a3eb2c70fcbae404403530519ca02e7fc4692/openjiuwen/core/runner/callback/events.py)

## Exit conditions not met

- Real isolated install and dependency lock: `NOT_RUN`.
- Minimal `ReActAgent` and restricted tool: `NOT_RUN`.
- Actual callback order and MIS ToolCall mapping: `NOT_RUN`.
- Real allow/ask/deny host behavior: `NOT_RUN`.
- Trusted checkpoint save/restore and session concurrency: `NOT_RUN`.
- Real interruption/cancellation and process restart: `NOT_RUN`.
- In-process compatibility comparison: `NOT_RUN`.
- JiuwenSwarm: `NOT_INTEGRATED` and outside this manifest.

Rollback is deletion or revert of the six isolated harness files. No database,
root dependency, runtime configuration, or canonical project state changes.
