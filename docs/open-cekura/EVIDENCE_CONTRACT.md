# OpenCekura Evidence Contract v1

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
```

Files are UTF-8. JSON used for hashing is canonicalized with stable key ordering, explicit separators, and no platform-dependent newline assumptions. YAML evidence preserves validated semantic content; its recorded SHA-256 covers the exact stored bytes.

## Evidence Manifest

Each run manifest is a versioned domain object with at least:

```json
{
  "schema_version": 1,
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

`final_state` is `pass`, `fail`, or `error` and reflects deterministic evaluation reality. The manifest does not hash itself. `scenario_sha256` and `agent_config_sha256` must equal the corresponding entries in `artifacts`.

The MIS Artifact stores the bundle/manifest identity and content hash; an available MIS plan-evidence manifest links Task, Plan, Run, ToolCall, Evaluation, Artifact, and Audit IDs. OpenCekura does not replace that authority with its filesystem manifest.

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

Every artifact uses the same Windows-safe algorithm:

1. validate that the resolved destination remains within the configured artifact root;
2. create a uniquely named temporary file in the destination directory;
3. write all bytes, flush, and call `os.fsync` where the platform/filesystem supports it;
4. close the temporary file;
5. replace the destination with `os.replace`;
6. best-effort fsync the directory on platforms that support directory handles;
7. remove an uncommitted temporary file after an error.

No core evidence flow uses `/tmp`, shell redirection, a cross-volume rename, or POSIX chmod assumptions. Concurrent writers use distinct temporary names and stable idempotency boundaries.

## Verification

```powershell
python -m open_cekura.cli.main evidence verify --campaign <id>
```

Verification is offline and deterministic. It:

- loads supported manifest schema versions strictly;
- rejects malformed paths and path escape;
- requires every declared artifact;
- recomputes SHA-256 over exact bytes;
- checks scenario/agent digest consistency;
- checks campaign/run IDs and evaluator-version coherence;
- reports missing files and expected/actual hashes without secret content;
- exits non-zero when any run or campaign artifact fails.

Changing one byte of a covered artifact must cause verification to fail. Verification must not rewrite the manifest, repair hashes, or silently regenerate files.

Unexpected files are reported. They cause failure when they use a reserved contract filename or when strict verification is requested; benign unrelated files cannot be used as evidence because they are not manifested.

## Provenance and privacy

Evidence records git commit, OS, runtime versions, agent/scenario digests, evaluator versions, timing, and final state. Optional LLM judge evidence includes provider/model/prompt/judge/temperature provenance.

Evidence never stores API key values, authorization headers, MIS agent tokens/sessions, raw hidden prompts, unrelated environment variables, or unredacted customer secrets. The Windows doctor reports key presence only. UI read models expose safe transcripts and provenance while preserving repository public-claims limitations.

## Retention and reproducibility

Generated local bundles are runtime artifacts and are ignored by git unless an explicitly curated, redacted fixture is reviewed for inclusion. CI may upload evidence artifacts. A replay records a new campaign/run and manifest rather than mutating historical evidence.
