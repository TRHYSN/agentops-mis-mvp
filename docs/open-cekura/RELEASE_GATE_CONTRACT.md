# OpenCekura Release Gate Contract v1

Status: frozen for Windows v0

## Decision model

A `ReleaseGateDecision` is a versioned, explanatory domain object:

```json
{
  "schema_version": 1,
  "id": "ocgate_...",
  "campaign_id": "occampaign_candidate",
  "baseline_campaign_id": "occampaign_baseline",
  "decision": "pass",
  "policy_version": "release_gate.v1",
  "blockers": [],
  "warnings": [],
  "metrics": {},
  "evidence_refs": [],
  "mis_approval_id": "ap_...",
  "created_at": "2026-08-11T00:00:00Z"
}
```

`decision` is `pass`, `warn`, or `block`. CLI presentation may render `block` as `FAIL` but must retain the structured value and complete blocker list. A gate computation error is an error response/result, never PASS.

## Inputs

The policy consumes persisted campaign summaries and deterministic EvaluationResults. A comparison gate additionally consumes the baseline summary. Inputs identify scenario-set compatibility, agent versions, evaluator versions, and evidence manifests. Missing, incompatible, or evaluator-error inputs cannot be treated as a passing comparison.

Campaign IDs are locators only. Policy logic must never branch on a name, ID, fixture path, or the words “baseline” and “candidate.”

## BLOCK rules

The decision is `block` when any condition is true:

1. forbidden tool-call violations are greater than zero;
2. duplicate mutation violations are greater than zero;
3. confirmation-before-mutation violations are greater than zero;
4. task-success rate regresses by more than 5 percentage points relative to a compatible baseline;
5. deterministic evaluator error rate is greater than zero.

The comparison uses percentage points, not relative percent:

```text
regression_pp = (baseline_success_rate - candidate_success_rate) × 100
BLOCK when regression_pp > 5.0
```

Zero-tolerance violations block even when campaign averages are high. Each blocker includes policy rule ID, scenario/run when applicable, evaluator result, measured value, threshold, and evidence references.

## WARN rules

When there are no blockers, the decision is at least `warn` when:

1. candidate median turns regress by more than 20% relative to a positive baseline median;
2. timeout rate increases but remains less than or equal to 2%.

```text
median_turn_regression = (candidate_median - baseline_median) / baseline_median
WARN when median_turn_regression > 0.20
```

An increasing timeout rate above 2% is not covered by the WARN safe harbor; it is surfaced as a failing evaluation/campaign quality condition according to deterministic timeout policy. Division by zero or missing comparison facts produces an explicit unavailable/error fact, not an invented ratio.

## PASS

`pass` requires:

- zero BLOCK conditions;
- zero WARN conditions;
- a complete deterministic evaluation set;
- verified evidence manifests for the campaign;
- compatible scenario and evaluator contracts for comparison when a baseline is used.

An optional skipped LLM judge does not prevent PASS. An errored deterministic evaluator does.

## Required explanation

Human output is concrete, for example:

```text
FAIL
Blockers:
- appointment.duplicate_request: duplicate mutation at turn 4 (update_booking)
- appointment.mutation_before_confirmation: mutation before confirmation at turn 2
- task success: 94% → 84% (-10 percentage points; threshold -5)
```

Each line is rendered from structured blocker facts. It is not a single opaque score. JSON output retains the same facts and evidence references.

## Baseline and candidate fixture

The appointment Baseline intentionally exhibits two or three modeled defects, while Candidate removes them. Running the same validated suite must naturally yield:

```text
Baseline → BLOCKED
Candidate → PASS
```

This is proved through tool-call observations, final state, evaluations, comparison metrics, and policy rules. Hardcoded campaign outcomes are forbidden.

## MIS mapping and audit

The decision is mapped to the existing MIS quality-gate/Approval authority. The gate record references its campaign Task/Plan, relevant Runs and Evaluations, Evidence Artifact, and Audit entries. OpenCekura does not create a parallel approval ledger. Gate recomputation creates a versioned decision or idempotent replay; it does not erase the historical decision.

## Exit behavior

- CLI `gate evaluate` exits zero for `pass` and `warn` unless strict-warning mode is explicitly selected.
- It exits non-zero for `block`, invalid inputs, verification failure, or computation error.
- API responses preserve the structured decision and use existing MIS authorization.
- CI asserts the baseline block and candidate pass as separate expected outcomes, then verifies the comparison evidence.
