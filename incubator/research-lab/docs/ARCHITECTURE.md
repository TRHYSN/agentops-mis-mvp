# Standalone Research Lab architecture — v0.4

```text
CLI / read-only local site
            |
Standalone SQLite WAL ledger (schema v1)
            |
Frozen Experiment Protocol + integrity engine
            |
Bounded async orchestrator
       +----+------------------+
       |                       |
 LocalExecutor           SSHExecutor
                               |
                    non-secret Server Registry
                               |
                    OpenSSH transport / remote root
                               |
             stdout / stderr / metrics / actuals / artifacts
            |
HTML report + redacted JSONL events
            |
Future idempotent AgentOps MIS adapter
```

## First-class standalone objects

- `Experiment`: frozen protocol plus scientific Trial matrix.
- `Trial`: one scientific condition; retries never inflate experiment count.
- `JobAttempt`: one local or remote infrastructure attempt.
- `Metric`: step-indexed scalar evidence.
- `Artifact`: content-hashed output.
- `ProtocolDeviation`: expected-versus-actual scientific condition.
- `Event`: normalized integration and audit record.
- `SSHServerProfile`: non-secret execution capability snapshot, not a credential store.

## State model

```text
Experiment: queued -> running -> completed | completed_with_deviation | failed | blocked
Trial:      queued -> running -> retry -> completed | completed_with_deviation | failed | blocked
Attempt:    running -> completed | completed_with_deviation | failed | timed_out
                         | interrupted (local recoverable)
                         | remote_unknown (remote fail-closed)
```

## SSH authority and evidence

A remote server is selected by profile name. The protocol freezes a public snapshot and hash containing host, port, user, remote root, Python executable, host-key policy, concurrency and staging limits. Credential values are excluded.

A changed profile is a changed execution condition. Existing queued Trials are blocked until a new protocol version is submitted.

## Safe staging

Only explicit relative `sync_paths` enter the source archive. The builder rejects traversal, absolute paths, symlinks, VCS/runtime/cache directories, secret-like files, and bundles over the declared profile size limit. The deterministic archive SHA-256 is retained as executor evidence.

## Remote uncertainty

A remotely running attempt left behind by an orchestrator interruption becomes:

```text
Attempt = remote_unknown
Trial   = blocked
```

An operator must reconcile, cancel, or collect the remote job before another attempt is authorized. Automatic retry could create duplicate GPU work and corrupt experiment counts.

## Scientific integrity

Technical success is independent from protocol fidelity and claim eligibility. A process may exit `0` while the Trial is `completed_with_deviation`. The claim gate checks stage, completion, seeds, primary metric coverage, and unresolved deviations.

## MIS boundary

The standalone module owns temporary Research Lab execution state. It does not write into `agentops_mis.db`. Future ingestion maps events through an idempotent adapter; AgentOps MIS remains eventual organization, identity, approval, delivery, reviewed-memory and audit authority.
