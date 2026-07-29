# AgentOps MIS CLI Map

Use this reference only after the read-only connection preflight passes.
Replace angle-bracket values with IDs or bounded summaries obtained from the
current MIS task. Never place credentials in arguments.

## Receive And Bind Work

```bash
agentops task pull \
  --agent-id <agent_id> \
  --status planned \
  --limit 1 \
  --enforce-intake

agentops task claim \
  --task-id <task_id> \
  --agent-id <agent_id> \
  --runtime codex
```

## Retrieve Bounded Context

```bash
agentops knowledge evidence-packet \
  --task-id <task_id> \
  --adapter codex \
  --limit 5 \
  --baseline-limit 5

agentops operator loop-launch-packet \
  --task-id <task_id> \
  --agent-id <agent_id>
```

Do not use the compact adapter launch brief for the current Codex client loop.
That brief is designed for launching separate runtime Workers and may contain
operator copy commands that the current Codex task must not execute.

## Plan And Start

```bash
agentops agent-plan create \
  --agent-id <agent_id> \
  --task-id <task_id> \
  --task-understanding "<bounded understanding>" \
  --referenced-specs "<repo-relative spec paths>" \
  --proposed-files-to-change "<repo-relative paths or empty>" \
  --risk <low|medium|high|critical> \
  --execution-steps-json '<json array>' \
  --verification-plan "<bounded verification>" \
  --rollback-plan "<bounded rollback>" \
  --status submitted

agentops agent-plan verify --plan-id <plan_id>

agentops run start \
  --task-id <task_id> \
  --agent-id <agent_id> \
  --runtime codex \
  --plan-id <plan_id> \
  --input-summary "<bounded input summary>"
```

If verification reports an approval requirement, stop before `run start` and
surface the pending approval to the human.

## Record Bounded Evidence

```bash
agentops runtime-event record \
  --run-id <run_id> \
  --agent-id <agent_id> \
  --adapter codex \
  --event-type codex.client.step \
  --status completed \
  --output-summary "<bounded step summary>" \
  --payload-hash <sha256>

agentops toolcall record \
  --run-id <run_id> \
  --agent-id <agent_id> \
  --tool codex.client \
  --category agent_runtime \
  --risk <risk> \
  --status completed \
  --target local://codex/client \
  --args-summary "<bounded inputs>" \
  --summary "<bounded result>"

agentops artifact record \
  --run-id <run_id> \
  --task-id <task_id> \
  --agent-id <agent_id> \
  --type report \
  --title "<artifact title>" \
  --summary "<bounded artifact summary>" \
  --content-hash <sha256>

agentops eval submit \
  --run-id <run_id> \
  --task-id <task_id> \
  --agent-id <agent_id> \
  --gate codex_client_quality \
  --score <0-to-1> \
  --pass \
  --evaluator-type rule \
  --notes "<bounded verification notes>"

agentops audit emit \
  --agent-id <agent_id> \
  --action codex.client.completed \
  --entity-type run \
  --entity-id <run_id> \
  --task-id <task_id> \
  --run-id <run_id> \
  --metadata-json '{"raw_prompt_omitted":true,"raw_response_omitted":true,"token_omitted":true}'
```

For a failed step, record `status failed`, a bounded error type/summary, and
complete the Run as failed. Do not record a false passing Evaluation.

## Close And Verify

```bash
agentops run heartbeat \
  --run-id <run_id> \
  --status completed \
  --summary "<bounded final summary>" \
  --duration-ms <duration_ms>

agentops run evidence-graph --run-id <run_id>
agentops task get --task-id <task_id>
```

Completion requires linked Run, Tool Call, Evaluation, Artifact, Runtime Event,
Audit, and verified Agent Plan evidence appropriate to the task contract.
