# AgentOps MIS Project Context

## Product Position

AgentOps MIS is a local-first control plane for human and AI teams. Humans use
the browser Console to define work, review evidence, approve risk and accept
delivery. AI workers use the Agent Gateway to pull bounded tasks, retrieve
project context, execute through an external runtime and write compact evidence
back to the MIS ledger.

The MIS does not replace Hermes, OpenClaw, Codex or another runtime. It owns the
task, plan, approval, run, tool, artifact, evaluation, memory-review and audit
records that make work governable across runtimes.

## Current Product Line

The active product line is a product-grade local AI Host with a zero-install
browser Console for a second computer. The Host owns the local SQLite authority
ledger, Knowledge Index, Worker Fleet and Runtime adapters. A remote browser is
a human control surface; it does not become a second authority database or run
the models itself.

The ordinary remote path must remain browser-only. Tailscale is an advanced
private-network option, not a requirement for ordinary customers. Public or
shared deployment claims require separate transport, identity, isolation,
recovery and physical-device evidence.

## Agent Start Context

An Agent starts from a bounded MIS packet, not a full conversation transcript.
The packet should combine:

- the assigned task and acceptance criteria;
- relevant versioned project-knowledge summaries;
- human-approved canonical memories;
- current plan, approval and execution constraints;
- source IDs and hashes needed for later evidence.

The model may receive bounded redacted summaries transiently. Tool calls,
evaluations and audit rows store only IDs, hashes, counts and omission proof;
they do not persist the context body, raw prompt, raw response, credentials,
private messages or full transcripts.

## Authority Rules

- Git and exact CI own code, commit and build truth.
- The AgentOps MIS ledger owns task, run, approval, evaluation and audit truth.
- Versioned Markdown owns reviewed product doctrine and runbooks.
- Only approved Memory rows are canonical Agent memory. Candidate Memory rows
  remain reviewable suggestions and cannot authorize execution.
- Conversation history is a discovery source, not project authority.

When sources disagree, an Agent reports the mismatch and uses the source that
owns that fact. It must not silently promote a recent conversation, a Runtime
log or a Pixel Office visualization over the authority ledger.

## Execution Method

Use the project method block:

```text
READ -> PLAN -> RETRIEVE -> COMPARE -> EXECUTE -> VERIFY -> RECORD
```

Real Hermes/OpenClaw execution is preferred for local product-readiness claims
when the runtime is available and explicitly authorized. Mock evidence is an
offline or CI fallback and must be labeled. External writes, publication,
deployment, credential changes and destructive operations require their
applicable confirmation or Prepared Action approval gate.

## Continuity Rule

At the end of a meaningful work cycle, write a compact Project Delta or
reviewable Memory candidate with the exact branch, commit, verification,
remaining risk and next action. A new Agent should be able to continue from the
MIS context packet and versioned handoff without replaying prior conversations.
