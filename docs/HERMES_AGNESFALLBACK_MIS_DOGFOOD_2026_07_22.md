# Hermes / Agnesfallback MIS Dogfood Acceptance (2026-07-22)

## Result

PASS for both a confirmed one-shot task and an unattended launchd-managed real
Hermes task through the installed local AgentOps Host. This is real runtime
evidence, not a mock, dry-run, or fixed health probe.

Codex created the unattended MIS task but did not invoke the Worker process.
The persistent Worker pulled the task, created and verified its Agent Plan,
called the Agnesfallback Hermes profile, and wrote the governed result back to
MIS. The remaining service receipt/readback is intentionally a Human Session
gate: a machine API key cannot attest its own operating-system control action.

## Runtime Shape

- Installed AgentOps Host: `http://127.0.0.1:18878`
- Default Hermes gateway: `127.0.0.1:8642`, left running and unchanged
- Agnesfallback API-only profile: `127.0.0.1:8643`
- Agnesfallback model profile: `agnes-2.0-flash` via its existing local provider
- `/health`: HTTP 200
- `/v1/models`: HTTP 200
- Real execution confirmation: explicit `--confirm-run`
- Worker session: short-lived, scoped Agent Gateway session
- Gateway LaunchAgent: `ai.hermes.gateway-agnesfallback`
- Worker LaunchAgent: `local.agentops.worker.agt_worker_daemon_hermes`
- Worker credential source: private local AgentOps config; no token in plist
- Worker Hermes target: explicit `http://127.0.0.1:8643` in the private plist

The API-only profile explicitly disables its Discord adapter so it does not
compete with the default Hermes gateway for the same messaging credential. Its
Figma MCP entry is retained but disabled for this API-only runtime, avoiding an
unrelated `npx` startup wait. No credential value was read into acceptance
output or committed state.

## Command Shape

The successful run used the installed CLI with the local Host URL supplied
explicitly:

```bash
AGENTOPS_BASE_URL=http://127.0.0.1:18878 \
  agentops workflow run-task \
  --adapter hermes \
  --confirm-run \
  --use-session \
  --hermes-gateway-url http://127.0.0.1:8643 \
  --hermes-timeout 180 \
  --hermes-max-tokens 256 \
  --risk low \
  --title '<bounded acceptance task>' \
  --description '<bounded non-sensitive task summary>' \
  --acceptance '<ledger acceptance criteria>'
```

The local CLI configuration was then corrected from a stale development port
to `http://127.0.0.1:18878`; the existing API key was preserved without
printing it.

## Ledger Evidence

| Object | Evidence |
| --- | --- |
| Task | `tsk_1c514a0cc90c`, completed |
| Run | `run_gw_060b45283ca3`, completed |
| Agent | `agt_accept_cli_20260716191958832520_c7c1b033` |
| Agent Plan | `plan_2d0e7373664e2d61`, verified |
| Plan evidence | `pem_ba9570bc1ac6b6a0`, verified |
| Runtime event | `rte_e2b1a7a47542` |
| Audit | `aud_10dffc6050e6` |
| Memory candidate | `mem_gw_438a590d0024a8b6` |

The workflow readback reported one tool call, one evaluation, one artifact,
one audit row, and verified plan evidence. The adapter completed on its first
attempt. Only a redacted 200-character output summary and hashes entered the
ledger; raw prompt/response and credentials were omitted.

### Unattended service task

| Object | Evidence |
| --- | --- |
| Task | `tsk_dogfood_hermes_service_20260722T125818Z`, completed |
| Run | `run_gw_4ed2c6654050`, completed |
| Agent | `agt_worker_daemon_hermes` |
| Agent Plan | `plan_c656cf5e8f7b48e8`, verified |
| Plan evidence | `pem_dc23cceea63b4653`, verified |
| Evaluation | `eval_gw_run_gw_4ed2c6654050_rule`, pass |
| Runtime event | `rte_5c7b6eb87a68` |
| Artifact | `art_gw_run_gw_4ed2c6654050_agent_worker_result_codex` |
| Memory candidate | `mem_gw_5df6bb17c0059b51` |

This task completed in 29,164 ms on adapter attempt 1. It produced a bounded
architecture review that assigned command/integration work to Codex,
governance and ledger authority to MIS, and independent model execution to
Hermes/OpenClaw. It performed no external write and required no approval.

The first service run exposed an actual queue starvation defect: an older
intake-blocked task at the queue head hid an eligible assigned task because the
Worker pulled only one candidate. The Worker now requests a bounded 50-item
candidate window; the server still applies Agent Plan/intake policy and returns
only eligible work. Regression coverage places a blocked unassigned task ahead
of the assigned task.

### Canonical service identity task

After migrating the Worker to the canonical LaunchAgent label and restarting
it through the supported service-control command, a second task completed
without a one-shot Worker invocation:

| Object | Evidence |
| --- | --- |
| Task | `tsk_dogfood_hermes_canonical_20260722T1315Z`, completed |
| Run | `run_gw_3c649a3a0c8e`, completed in 13,319 ms |
| Agent | `agt_worker_daemon_hermes` |
| Agent Plan | `plan_c23c1e3d91cde315` |
| Plan evidence | `pem_fa3dd92dc686b752`, verified |
| Tool call | `tc_gw_685b379a4082`, completed |
| Evaluation | `eval_gw_run_gw_3c649a3a0c8e_rule`, pass |
| Runtime event | `rte_66a9e89f70f7` |
| Artifact | `art_gw_run_gw_3c649a3a0c8e_agent_worker_result` |
| Audit | `aud_88b167bccabe` |
| Memory candidate | `mem_gw_a35aece5c5149683` |

The Worker found the task, auto-planned it, executed one real Hermes attempt,
and recorded 1 tool call, 1 evaluation, 1 artifact, 1 memory candidate, 8
runtime events, 8 audit rows, and a verified manifest. The bounded answer was a
three-step customer acceptance checklist. No external write or approval was
required.

The run's knowledge retrieval diagnostics remained `attention` (`recall@5`
0.2 and MRR 0.2) even though the method, runtime, and rule evaluation passed.
That is a retrieval-quality follow-up; it does not invalidate the unattended
execution proof.

## Companion OpenClaw Evidence

The same installed Host also ran an independently supervised OpenClaw Worker:

| Object | Evidence |
| --- | --- |
| Worker service | `local.agentops.worker.agt_worker_daemon_openclaw` |
| Task | `tsk_dogfood_openclaw_service_20260722T1352Z`, completed |
| Run | `run_gw_fa17de22b53d`, completed in 11,358 ms |
| Agent | `agt_worker_daemon_openclaw` |
| Agent Plan | `plan_76cc2fab45fb22ee` |
| Plan evidence | `pem_6dc9cf22b732e6bf`, verified |
| Tool call | `tc_gw_940b877458e0`, completed |
| Evaluation | `eval_gw_run_gw_fa17de22b53d_rule`, pass |
| Runtime event | `rte_7b5558e0b9ce` |
| Artifact | `art_gw_run_gw_fa17de22b53d_agent_worker_result` |
| Audit | `aud_4645baab0682` |
| Memory candidate | `mem_gw_66e6979d9404c82b` |

The persistent OpenClaw Worker pulled the assigned task and completed one real
adapter attempt without a one-shot Worker invocation. Its bounded review gave
a conditional pass and independently identified retrieval quality and service
governance follow-ups. It performed no external write and required no
approval. Together, the Hermes and OpenClaw service runs prove that Codex can
act as commander while MIS dispatches governed work to two independently
configured local model runtimes.

## Remaining Product Gate

The unattended execution path is now real and locally usable. These narrower
promotion gates remain:

1. record the exact Worker service-control receipt and control readback from an
   authenticated Owner/Operator browser session;
2. install the same source behavior in the packaged Host release after the
   local storage floor permits a Preview 39 upgrade.

## Safety

- No token, `.env`, database, raw prompt, raw response, transcript, runtime log,
  model cache, or generated export is committed.
- Default Hermes and OpenClaw services were not removed.
- The Agnesfallback gateway binds only to `127.0.0.1`.
- Real execution required an explicit confirmation flag.
