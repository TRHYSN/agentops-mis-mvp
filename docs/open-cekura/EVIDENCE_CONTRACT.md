# OpenCekura Evidence Contract v3

Status: frozen for Windows v0

## Bundle layout

Each run writes:

```text
artifacts/open-cekura/<campaign_id>/<run_id>/
  scenario.yaml
  agent_version.json
  transcript.json
  tool_calls.json
  timing.json
  evaluations.json
  evidence_manifest.json
```

The campaign directory also contains:

```text
campaign_summary.json
baseline_candidate_diff.json
release_gate.json
regression_cases.json
regression_replay.json
gate_history.json
gates/
  <gate_id>/
    release_gate.json
    baseline_candidate_diff.json
```

Run `EvidenceManifest` objects and the campaign summary envelope use schema v3.
Manifest v3 defines `scenario_sha256` as the SHA-256 of the validated Scenario's
canonical JSON form (UTF-8, sorted keys, fixed separators), independent of YAML
key order and CRLF/LF. The `artifacts["scenario.yaml"]` digest separately covers
the exact stored YAML bytes, so byte tampering still fails verification.
Manifest v3 also defines `agent_config_sha256` as the digest of the canonical
effective `config` value, while the artifact map independently covers the full
`agent_version.json` envelope. The campaign envelope v3 adds hash-linked,
immutable release-gate history and governed regression-replay provenance. The
root gate and diff files are current views; `gate_history.json.current_gate_id`
selects the matching create-once snapshot. History entries contain the SHA-256
of both snapshot files and are keyed only by validated stable gate IDs.

Files are UTF-8. JSON used for hashing is canonicalized with stable key ordering,
explicit separators, no BOM, and no platform-dependent newline assumptions.
Logical artifact paths are campaign-relative POSIX paths using `/`, including on
Windows; absolute paths, drive-relative paths, and `\` aliases are not contract
identities. YAML evidence preserves validated semantic content; its artifact
SHA-256 covers the exact stored bytes.
Campaign, Run, Gate, workspace, and other evidence path identifiers are
lowercase portable components. Mixed-case identifiers are rejected on every OS
so Linux cannot publish names that alias after transfer to Windows.

## Evidence Manifest

Each run manifest is a versioned domain object with at least:

```json
{
  "schema_version": 3,
  "id": "ocmanifest_...",
  "campaign_id": "occampaign_...",
  "run_id": "ocrun_...",
  "mis_artifact_id": "art_...",
  "git_commit_sha": "40-hex-sha",
  "environment": {
    "os": "Windows-...",
    "python_version": "3.11.x",
    "node_version": "v22.x"
  },
  "scenario_sha256": "64-hex",
  "agent_config_sha256": "64-hex",
  "evaluator_versions": ["task_success.v1"],
  "artifacts": {
    "scenario.yaml": "64-hex",
    "agent_version.json": "64-hex",
    "transcript.json": "64-hex",
    "tool_calls.json": "64-hex",
    "timing.json": "64-hex",
    "evaluations.json": "64-hex"
  },
  "started_at": "2026-08-11T00:00:00Z",
  "finished_at": "2026-08-11T00:00:01Z",
  "final_state": "pass",
  "created_at": "2026-08-11T00:00:01Z"
}
```

`final_state` is `pass`, `fail`, or `error` and reflects deterministic evaluation reality. The manifest does not hash itself. `scenario_sha256` hashes the canonical validated Scenario contract; `artifacts["scenario.yaml"]` hashes the exact stored bytes. `agent_config_sha256` hashes the canonical `config` value inside `agent_version.json`; it intentionally differs from the hash of that complete envelope.

Pre-release manifest v1/v2 used different digest meanings and is unsupported.
Readers reject them at the schema boundary before digest reconstruction; they
never reinterpret or rewrite them.

The MIS Artifact stores the bundle/manifest identity and content hash; an available MIS plan-evidence manifest links Task, Plan, Run, ToolCall, Evaluation, Artifact, and Audit IDs. OpenCekura does not replace that authority with its filesystem manifest.

## Campaign envelope and regression replay

`campaign_summary.json` is the schema-v3 hash envelope and commit marker. Its
top-level `artifacts` map covers the exact canonical bytes of every campaign
artifact, including `regression_replay.json`. A normal, non-replay Campaign
writes that artifact as the exact canonical JSON value `null` and stores
`summary.regression_replay` as `null`.

A replay target writes this strict `regression_replay.json` shape:

```json
{
  "schema_version": 1,
  "source_campaign_id": "occampaign_source",
  "target_campaign_id": "occampaign_replay",
  "mappings": [
    {
      "schema_version": 1,
      "id": "ocreplaymap_...",
      "created_at": "2026-08-12T00:00:00Z",
      "source_campaign_id": "occampaign_source",
      "target_campaign_id": "occampaign_replay",
      "regression_case_id": "ocregression_...",
      "source_run_id": "ocrun_source",
      "source_evaluation_result_id": "evr_source",
      "evaluator_id": "confirmation_before_mutation.v1",
      "source_scenario_id": "appointment.basic",
      "replay_scenario_id": "ocreplay_...",
      "replay_run_id": "ocrun_replay",
      "source_snapshot_sha256": "64-hex",
      "source_scenario_sha256": "64-hex",
      "replay_scenario_sha256": "64-hex",
      "mis_memory_id": "mem_..."
    }
  ]
}
```

There is exactly one typed `RegressionReplayMapping` per source
`RegressionCase`. It proves the chain from source Campaign, Run, Evaluation,
evaluator, Scenario, canonical input snapshot, and candidate MIS Memory to the
target Campaign's replay Run and replay-local Scenario. Scenario digests cover
canonical Scenario JSON; `source_snapshot_sha256` covers canonical JSON of the
source `RegressionCase.original_input` value.

The full mappings are not duplicated in `campaign_summary.json`. The only replay
provenance in its `summary` is this hash-protected pointer:

```json
{
  "schema_version": 1,
  "source_campaign_id": "occampaign_source",
  "target_campaign_id": "occampaign_replay",
  "mapping_ids": ["ocreplaymap_..."],
  "mapping_sha256": "64-hex"
}
```

`mappings` is sorted by mapping ID; `mapping_ids` uses that same order, and
`mapping_sha256` is SHA-256 over the canonical UTF-8 JSON bytes of the complete
typed `mappings` array. The outer artifact digest independently covers the full
`regression_replay.json` envelope. Unknown fields, duplicate mapping IDs, empty
replay mappings, cross-campaign bindings, or a pointer/digest mismatch fail
closed.

## Evidence references

Evaluation and failure evidence refers to stable logical fragments:

- `artifact:<relative-path>`;
- `turn:<turn-id>`;
- `tool_call:<tool-call-id>`;
- `evaluation:<evaluation-result-id>`;
- `expectation:<typed-path>`;
- `final_state:<json-pointer>`;
- `mis:<object-type>:<stable-id>`.

References must resolve inside the campaign evidence graph. They may not point to absolute developer paths, credentials, or ephemeral memory addresses.

## Atomic write algorithm

Individual staged artifacts use this Windows-safe algorithm:

1. validate that the resolved destination remains within the configured artifact root;
2. create a uniquely named temporary file in the destination directory;
3. write all bytes, flush, and call `os.fsync` where the platform/filesystem supports it;
4. close the temporary file;
5. replace the destination with `os.replace`;
6. best-effort fsync the directory on platforms that support directory handles;
7. remove an uncommitted temporary file after an error.

No core evidence flow uses `/tmp`, shell redirection, a cross-volume rename, or POSIX chmod assumptions. Concurrent writers use distinct temporary names and stable idempotency boundaries.

Campaign publication is a recoverable whole-tree protocol. A complete candidate
generation is built and verified under a private same-volume slot. A canonical,
fsynced journal records its tree hash; the same SQLite transaction that writes
MIS facts records a publication outbox row. The verified campaign directory is
then swapped into place with same-volume renames. Recovery treats the committed
outbox as authority: without it the previous directory is restored; with it the
new generation is promoted, reverified against MIS, marked published, and only
then are private backup files removed. A crash cannot leave a half-published
alias set as the accepted campaign generation.

Publication journal schema v2 binds the workspace and campaign to a persistent,
random SQLite-instance authority ID stored inside that database, plus the gate,
publication ID, previous tree hash, and candidate tree hash. A database newly
created at the same pathname has a different authority ID and cannot recover or
roll back the original database's journal. If the original SQLite authority is
missing or replaced, recovery preserves the sealed journal, backup, and final
tree and fails closed for operator intervention.

The private slot is keyed by the filesystem-normalized campaign component, the
same namespace used by the final campaign directory; on Windows, case aliases
therefore cannot acquire separate publication slots. A persistent lock file
holds a non-blocking operating-system lease for the writer's process lifetime.
Recovery must acquire that lease before inspecting or changing a slot. An
unsealed slot whose lease can be acquired is a stale pre-journal stage left by a
dead process and is discarded. If the lease is still held, recovery reports an
active pending publication and does not touch its stage. Lock files remain as
small namespace sentinels so cleanup never depends on deleting and recreating a
lock inode.

Whole-tree hashing opens each file without shell mediation, verifies the opened
descriptor is the same regular file as the checked path, counts the bytes
actually streamed against bounded file/byte limits, and rechecks identity,
size, and modification time after reading. Growth, truncation, replacement, or
in-place mutation observed across that snapshot boundary invalidates the
generation instead of producing a hash from inconsistent bytes.

Within a staged generation, Gate snapshots are create-once. Existing identical
bytes are an idempotent replay; existing different bytes fail and are never
replaced. The history index and current aliases are written only after
snapshots, with `campaign_summary.json` written last as the generation's hash
commit marker.

For a regression replay, the source Evidence check and source MIS/vertical
reconciliation are held stable while the target Campaign graph, immutable replay
mapping rows, MIS mappings, and publication outbox are committed in one SQLite
transaction. An exact retry reuses the same stable Campaign and mapping IDs and
does not add ledger rows; a different row at the same idempotency boundary, or
any source authority drift, rolls back and fails closed. Filesystem exposure
still follows the recoverable publication protocol above rather than pretending
that SQLite and NTFS share one transaction.

`campaign compare` and `gate evaluate` may append Gate history and change the
current Gate aliases, but they must carry a verified `regression_replay.json`
and `summary.regression_replay` forward unchanged. They may not replace replay
provenance with canonical `null`, omit it, reorder it, or rebind it.

## Verification

```powershell
python -m open_cekura.cli.main evidence verify --campaign <id> --workspace local-demo --db agentops_mis.db
```

The public CLI performs governed, deterministic verification: it first runs the
offline filesystem verifier, then reconciles the verified facts with the
selected workspace's SQLite/MIS authority in one stable transaction. The
lower-level `open_cekura.evidence.manifest.verify_campaign` API remains the
explicit filesystem-only verifier. Together they:

- loads supported manifest schema versions strictly;
- rejects malformed paths and path escape;
- requires every declared artifact;
- recomputes SHA-256 over exact bytes;
- checks scenario/agent digest consistency;
- checks campaign/run IDs and evaluator-version coherence;
- requires the exact schema-v3 campaign artifact set, treating canonical `null`
  as the only ordinary-Campaign replay value and validating every replay mapping
  and its summary pointer for a replay target;
- recursively verifies each replay source Campaign and its run Evidence before
  accepting the source-to-target chain; cycles, excessive ancestry depth, and
  shared global campaign/gate/work budgets fail closed;
- verifies that the gate-history index and snapshot directory have the exact
  same bounded set, with no orphan, missing, linked, or case-conflicting entry;
- reconstructs every historical gate from verified candidate/baseline facts and
  requires the current root aliases to equal `current_gate_id` byte-for-byte;
- reports missing files and expected/actual hashes without secret content;
- exits non-zero when any run or campaign artifact fails.

Changing one byte of a covered artifact must cause verification to fail. Verification must not rewrite the manifest, repair hashes, or silently regenerate files.

Filesystem-only verification proves internal hash and policy consistency. It
cannot, by itself, prove append-only history against an attacker able to
coordinate a full filesystem rewrite. Public `evidence verify`, `campaign
compare`, and `gate evaluate` therefore reconcile the typed campaign hierarchy,
every Turn, ToolCall, Run, Evaluation, Failure, Regression/Memory,
EvidenceManifest, Artifact hash, exact PlanEvidence set, historical Gate,
Approval, Audit chain, and the explicit latest Gate head against the workspace
SQLite/MIS authority. Replay targets additionally reconcile the immutable replay
mapping set and the source Run/Evaluation/evaluator/Scenario/snapshot/Memory
chain. Missing, extra, stale, rolled-back, or conflicting ledger facts fail
closed before any new Approval or target publication.

Unexpected files are reported. They cause failure when they use a reserved contract filename or when strict verification is requested; benign unrelated files cannot be used as evidence because they are not manifested.

## Provenance and privacy

Evidence records git commit, OS, runtime versions, agent/scenario digests, evaluator versions, timing, and final state. Optional LLM judge evidence includes provider/model/prompt/judge/temperature provenance.

Evidence never stores API key values, authorization headers, MIS agent tokens/sessions, raw hidden prompts, unrelated environment variables, or unredacted customer secrets. The Windows doctor reports key presence only. UI read models expose safe transcripts and provenance while preserving repository public-claims limitations.

## Retention and reproducibility

Generated local bundles are runtime artifacts and are ignored by git unless an explicitly curated, redacted fixture is reviewed for inclusion. CI may upload evidence artifacts. A replay records a new campaign/run and manifest rather than mutating historical evidence.

No in-place migration from pre-release EvidenceManifest v1 or v2 is provided.
Existing pre-release databases and artifact roots must be archived or replaced,
then campaigns must be rerun to generate v3 evidence. Changing only the stored
schema number or digest would invalidate evidence immutability and MIS Artifact
anchors.
