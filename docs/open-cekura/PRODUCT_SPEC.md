# OpenCekura Reliability Lab v0 Product Specification

Status: implementation contract

Schema generation: v0 / Scenario schema version 1

Source issue: `geogejoy107-jpg/agentops-mis-mvp#123`

Canonical product context: Notion page `3b96adfd-d920-81cf-9f99-d2990deea005`

Execution branch: `feat/open-cekura-windows-v0`
Starting commit: `99ce51d693f1d646ea84acc2f7f376bde1a95a9a`

## Product definition

OpenCekura is an open-source vertical product within AgentOps MIS. Its formal module name is **Reliability Lab**.

It provides reliability testing, deterministic simulation, evaluation, regression management, release gating, and inspectable evidence for AI agents. Version 0 supports chat agents, tool-using agents, HTTP agents, and a deterministic mock agent. Live voice, WebRTC, SIP, LiveKit, Pipecat, Vapi, and Retell are explicitly deferred to v0.2.

The v0 value chain is complete only when it closes this loop:

```text
Scenario
→ Simulation
→ ToolCall observation
→ Evaluation
→ Failure
→ Regression
→ Release Gate
→ Evidence
→ Reliability Lab UI
```

A chat-only demonstration, a static UI, or a numeric score without an evidence chain does not satisfy this specification.

## Clean-room and public-claims boundary

The implementation is based on the user's product requirements and the public capability category, not on private Cekura code, private APIs, reverse engineering, copied HTML, or copied brand design. OpenCekura must not be described as an official Cekura open-source edition. Simulation evidence demonstrates only the configured test conditions; it must not be presented as proof of production reliability.

The repository's `docs/PUBLIC_CLAIMS_AND_LIMITATIONS.md` remains controlling for authentication, tenancy, production readiness, security, and deployment claims.

## MIS authority mapping

OpenCekura may own vertical business tables, but it must not create a second Task, Run, Approval, or Audit ledger.

| Reliability object | MIS authority | Required stable mapping |
| --- | --- | --- |
| Campaign | Task and Agent Plan | `mis_task_id`, `mis_agent_plan_id` |
| ConversationRun | Run | `mis_run_id` |
| ObservedToolCall | ToolCall | `mis_tool_call_id` |
| EvaluationResult | Evaluation | `mis_evaluation_id` |
| EvidenceManifest | Artifact and Evidence | `mis_artifact_id`, optional plan-evidence manifest ID |
| ReleaseGateDecision | Quality Gate / Approval | `mis_approval_id` or equivalent authoritative gate reference |
| RegressionCase | Memory and future Plan input | `mis_memory_id` |

MIS IDs are mappings, not values inferred from string shape. The existing Human Auth, owner, session, RBAC, workspace visibility, Audit chain, and Agent Gateway remain authoritative.

## Required domain objects

The following are explicit versioned models, not unstructured catch-all JSON blobs:

- `AgentUnderTest`
- `AgentVersion`
- `ScenarioSuite`
- `Scenario`
- `Persona`
- `Campaign`
- `ConversationRun`
- `ConversationTurn`
- `ObservedToolCall`
- `EvaluationResult`
- `FailureCase`
- `FailureCluster`
- `RegressionCase`
- `ReleaseGateDecision`
- `EvidenceManifest`

Every object has a stable ID, `schema_version`, UTC `created_at`, the relevant parent ID, and a deterministic JSON serialization contract. Bounded nested contracts may be stored as JSON, but a single giant JSON document must not replace the object graph.

## Scenario v1

Scenario input is YAML validated with Pydantic v2 or an equivalent strict validator. `schema_version: 1` is required. Unknown fields, invalid enum values, missing required fields, and incompatible schema versions fail immediately and are surfaced to the CLI. Contract errors must never be silently ignored.

The contract includes:

- stable scenario ID and human name;
- a typed persona;
- an initial user message and typed goal;
- typed challenges, including interruption and mid-flow constraint changes;
- required and forbidden tool calls;
- confirmation-before-mutation requirements;
- expected final state;
- optional turn and timeout limits.

## Simulation

The adapter contract is asynchronous:

```python
class AgentAdapter:
    async def start(...): ...
    async def send(...): ...
    async def observe_tool_calls(...): ...
    async def close(...): ...
```

v0 provides `MockAgentAdapter` and `HTTPAgentAdapter`. A run records turns, observed tool calls, timing, final backend state, timeout/error state, and agent assertions. Deterministic mode produces the same semantic outcome for identical scenario, agent version, and configuration inputs.

## Appointment reliability demo

The first public demo is **AI Appointment Agent Reliability Test**. Its mock backend exposes:

- `lookup_booking`
- `list_available_slots`
- `update_booking`
- `cancel_booking`

The suite contains at least these ten behavior classes:

1. basic success;
2. interruption;
3. change date mid-flow;
4. ambiguous identity;
5. unavailable slot;
6. duplicate request;
7. mutation before confirmation;
8. backend timeout;
9. tool succeeded but the agent claims failure;
10. agent claims success but state was not mutated.

Two agent versions are provided. Baseline contains two or three real reliability defects. Candidate fixes them. Deterministic evaluators and the gate policy—not a campaign-ID switch—must derive `Baseline → BLOCKED` and `Candidate → PASS`.

## Product surfaces

The CLI provides:

```powershell
python -m open_cekura.cli.main doctor
python -m open_cekura.cli.main scenario validate examples/open-cekura/scenarios/basic.yaml
python -m open_cekura.cli.main campaign run --suite examples/open-cekura/scenarios --agent mock
python -m open_cekura.cli.main campaign compare --baseline <id> --candidate <id>
python -m open_cekura.cli.main gate evaluate --campaign <id>
python -m open_cekura.cli.main evidence verify --campaign <id>
```

The existing MIS API hosts `/mis-api/reliability/*`. The existing Vite application hosts a `Reliability Lab` workspace feature with Overview, Agents, Scenario Suites, Campaigns, Run Detail, Failures, Regression Suite, and Release Gates. v0 prioritizes complete read-only evidence inspection over a visual scenario editor.

## Completion criteria

Completion requires a Windows fresh-clone workflow, passing critical doctor checks, 10+ deterministic scenarios, replayable baseline/candidate campaigns, explanatory deterministic evaluations, tamper-detecting evidence, automatic and replayable regressions, naturally derived gate outcomes, MIS-mapped SQLite/API records, the complete UI evidence chain, Windows and Ubuntu CI for Python 3.10/3.11, a green UI build, complete contracts/runbook/handoff, an exact CI run, and an open PR to `main`. The PR must not be merged by the implementation agent.
