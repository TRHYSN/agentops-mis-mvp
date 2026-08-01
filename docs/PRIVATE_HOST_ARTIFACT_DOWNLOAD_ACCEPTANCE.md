# Private Host Approved Artifact Download Acceptance

## Scope

This slice adds a bounded, ledger-backed download for approved artifacts:

```http
GET /api/artifacts/:artifact_id/download
GET /api/artifacts/:artifact_id/download?format=json
```

The endpoint is a human-console route. It requires an active Private Host human
Session and accepts neither an Agent Gateway machine credential nor an
anonymous request as a substitute.

## Authorization Contract

A download is allowed only when:

- the artifact ID is a single bounded ledger identifier;
- the artifact belongs to the human Session workspace through its task/run;
- an `approvals` row with `decision='approved'` references the artifact's run
  or effective task;
- the requested format is Markdown or JSON.

Anonymous requests return `401`, unapproved artifacts return `403`, and missing,
invalid or traversal-shaped artifact IDs return `404`. Cross-workspace artifacts
are returned as not found to avoid disclosing their existence.

## Content Boundary

Downloads are generated from bounded fields already stored in the MIS ledger:

- artifact ID, type and redacted title;
- task ID and run ID;
- approval ID and artifact creation time;
- a redacted artifact summary capped at 4,000 characters.

The implementation does not open or follow `artifacts.uri`, does not accept a
filesystem path from the request, and does not read task descriptions, run
input/output, raw prompts, raw responses, credentials, private messages or full
transcripts. Responses use `Cache-Control: no-store`, `nosniff`, and a bounded
ASCII `Content-Disposition` filename derived from the validated artifact ID.

Each successful Markdown or JSON download writes an `artifact.download` audit
record with the human account ID, workspace, approval ID, format, byte count and
content hash. Download content, artifact URI and credentials are omitted from
audit metadata.

## Verification

```bash
python3 -m py_compile server.py scripts/private_host_artifact_download_smoke.py
python3 scripts/private_host_artifact_download_smoke.py
git diff --check -- server.py scripts/private_host_artifact_download_smoke.py docs/PRIVATE_HOST_ARTIFACT_DOWNLOAD_ACCEPTANCE.md
```

The smoke uses a temporary SQLite database and a loopback Private Host with
human authentication enabled. It verifies:

- approved Markdown and JSON downloads;
- active Owner Session authentication;
- anonymous denial;
- unapproved denial;
- missing and encoded path-traversal denial;
- deterministic safe attachment filenames;
- bounded secret-redacted content;
- a malicious local-file artifact URI is never read;
- run input/output markers are not returned;
- one human audit record per successful download;
- no real Runtime call.

## Limitations

- This slice exports the approved bounded ledger summary, not arbitrary binary
  artifacts or Host filesystem files.
- Approval is associated through the artifact's run or task because the current
  `approvals` schema has no direct `artifact_id` column.
- Task Detail and Run Detail expose an icon download action only when their
  loaded ledger approvals contain an approved decision. The same-origin backend
  remains authoritative and repeats every authentication, workspace and
  approval check. The backend smoke is part of the deterministic CI suite.
