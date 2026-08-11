# OpenCekura Evaluation Contract v1

Status: frozen for Windows v0

## Result schema

Every evaluator emits one independent, explainable result:

```json
{
  "schema_version": 1,
  "id": "evr_...",
  "run_id": "ocrun_...",
  "evaluator_id": "required_tool_calls.v1",
  "status": "pass",
  "score": 1.0,
  "threshold": 1.0,
  "reason_codes": [],
  "evidence_refs": [],
  "metadata": {},
  "mis_evaluation_id": "eval_...",
  "created_at": "2026-08-11T00:00:00Z"
}
```

`status` is one of `pass`, `fail`, `warn`, `error`, or `skipped`. `score` and `threshold` are finite values from 0.0 through 1.0 when the status is `pass`, `fail`, or `warn`. `error` and `skipped` use `score: null`; they are never silently converted to PASS. `mis_evaluation_id` is nullable only until the MIS mapping write succeeds.

`reason_codes` are stable machine-readable identifiers. `evidence_refs` identify concrete bundle fragments such as `turn:<id>`, `tool_call:<id>`, `expectation:required_tool_calls[1]`, `final_state:/booking_updated`, or `artifact:tool_calls.json`. Human-readable explanations and localized facts live in typed metadata; consumers must not infer a failure from prose alone.

## Deterministic input

Rules receive a closed evaluation context containing:

- the validated Scenario v1 contract;
- the versioned agent configuration;
- ordered conversation turns;
- ordered observed tool calls with turn, arguments, result/error, mutation flag, and timing;
- the initial and final backend state;
- agent success/failure assertions;
- run timing, timeout, and adapter error facts.

The same canonical context and evaluator version must produce the same result. Rules do not call a model, network service, wall-clock-dependent scorer, or random generator.

## Required deterministic evaluators

### `task_success.v1`

PASS requires the expected goal/final state and the agent's final assertion to agree with observed reality. It fails both “tool succeeded but agent claims failure” and “agent claims success but state was not mutated.” Evidence identifies the final agent turn and mismatched state paths.

### `required_tool_calls.v1`

PASS requires every named call in `expectations.required_tool_calls`. Failure metadata lists each missing tool and expectation index. Extra allowed calls do not fail this evaluator.

### `forbidden_tool_calls.v1`

PASS requires zero observed calls named in `expectations.forbidden_tool_calls`. Each violation records the tool-call ID, turn ID, tool name, and expectation index. A violation is a release blocker.

### `duplicate_mutation.v1`

PASS requires that the same logical mutation is not executed more than once without a scenario-defined reason. The idempotency signature is evaluator-versioned and uses normalized tool name, target identity, and mutation intent. Failure evidence identifies the first and duplicate calls and their turns. A violation is a release blocker.

### `confirmation_before_mutation.v1`

When `must_confirm_before_mutation` is true, PASS requires explicit user confirmation in an earlier turn than each state-mutating call. Merely asking for confirmation in the same agent turn or inferring intent from the initial request is insufficient. Failure evidence identifies the mutation and the missing confirmation boundary. A violation is a release blocker.

### `final_state_match.v1`

PASS requires every expected final-state path/value to match the observed backend state. Failure metadata contains JSON-pointer-like paths with expected and observed values. It does not trust an agent's textual claim as state evidence.

### `turn_count_limit.v1`

PASS requires the measured turn count to be at or below the scenario limit. Metadata records the limit and observed count. A result may be WARN rather than FAIL only when the scenario explicitly marks the limit advisory.

### `timeout.v1`

PASS requires no run timeout and no tool timeout that violates the scenario contract. Failure evidence identifies the timed-out adapter/tool operation, turn, configured limit, and measured duration. An internal evaluator exception is `error`, not a timeout failure and not PASS.

## Aggregation

Campaign summaries retain every individual result and compute:

- scenario task-success rate;
- deterministic pass/fail/error counts;
- deterministic evaluator error rate;
- forbidden-call, duplicate-mutation, confirmation-violation, and timeout counts/rates;
- median turns and bounded latency summaries.

Aggregation never hides a zero-tolerance failure inside an average. `error` results are excluded from pass-rate numerators but included in deterministic error-rate denominators. `skipped` optional judges are reported separately and do not affect deterministic success.

## LLM Judge adapter

`LLMJudgeAdapter` is optional and cannot be required for CI. Without a configured provider key it emits:

```json
{
  "status": "skipped",
  "score": null,
  "reason_codes": ["judge_credentials_missing"]
}
```

It must not invent a score, fall back to an undisclosed model/provider, or treat provider/evaluator errors as PASS. When invoked, result metadata and the Evidence Manifest record:

- provider;
- model;
- prompt version;
- judge implementation version;
- temperature;
- request/configuration digest;
- safe error category when applicable.

Secrets, full provider credentials, and hidden prompt bodies are never placed in evaluation metadata. A reproducible prompt version/digest may be recorded without exposing private content.

## Versioning and compatibility

Changing inputs, normalization, scoring, thresholds, reason-code meaning, or aggregation semantics requires a new evaluator version. Readers reject unsupported result schema versions rather than guessing. Historical results retain the evaluator version that produced them.
