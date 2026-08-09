# Research Edition v0.5 Commander Board

Status: Proposed
Canonical: false
Base: `99ce51d693f1d646ea84acc2f7f376bde1a95a9a`
Plan: `RE-V05-PLAN-001`

This board tracks candidate delivery facts. It does not replace MIS or the reviewed project ledger.

| Lane | State | Owner role | Owned paths | Gate / blocker | Next action |
|---|---|---|---|---|---|
| L00 Reconcile | completed | Sol Commander | plan, board, Gate 0 receipt | G0 `PASS_WITH_CONDITIONS` | Keep this branch docs/evidence-only |
| L01 Dogfood | completed with condition | MIS integrator | MIS objects/readback only | Task/Plan/Run passed; Project/Goal route is Human Session only | Preserve IDs and record later evidence |
| L02 openJiuwen spike | ready | openJiuwen integrator | isolated incubator runtime package/docs/tests | Own Task/Plan/Run required; install/network still not authorized | Create isolated branch and fake protocol/manifest slice |
| L03 Domain | ready | research domain engineer | domain/repository/migration/tests | Own Task/Plan/Run required | Create isolated branch and pure contract foundation |
| L04 Durable executor | pending | executor engineer | executor/reconcile/tests | L03 contract required | No action |
| L05 Evidence | pending | evidence engineer | ingest/claim/invalidation/tests | L03 contract required | No action |
| L06 Jiuwen runtime | pending | runtime integrator | runtime adapter/tests | L02 + L03 + L05 | No action |
| L07 UI | pending | UI engineer | existing AppShell research routes | domain/executor/evidence read models required | No action |
| L08 SSH/GPU | pending | executor engineer | connector/profile/authorized UAT | credentials, infrastructure, approval | Keep `live_gpu_uat=NOT_RUN` |
| L09 BWFormer | pending | demo/evidence owner | existing adapter and bounded fixtures | evidence and executor gates | Keep scientific claim pending |
| L10 Acceptance | pending | independent reviewers | read-only verification | implementation incomplete | No action |

## First-wave read-only reports

| Work card | Result | Key output |
|---|---|---|
| `RE-V05-L00` repo reconciler | PASS_WITH_CONDITIONS | Exact main; stale checkout; PR #118 conflict/test-discovery gap |
| `RE-V05-L02` openJiuwen researcher | PASS_WITH_CONDITIONS | Managed Python 3.11 subprocess; pin agent-core `bf0a3eb`; no JiuwenSwarm default |
| `RE-V05-L03` research domain architect | PASS_WITH_CONDITIONS | Additive hybrid domain; immutable evidence; explicit recovery and review gates |
| `RE-V05-LMEM` memory curator | PASS_WITH_CONDITIONS | Candidate context only; canonical docs stale; no sensitive topology in packet |

## Current truth

- `feature_write_allowed=true_for_separately_bound_wave_1_only`
- `mis_ledger_write=written_and_read_back`
- `mis_task_id=tsk_re_v05_l00_99ce51d`
- `mis_plan_id=plan_336c6f572cdcd906`
- `mis_run_id=run_gw_e5fcd972a353`
- `mis_plan_quality=100`
- `canonical_state_changed=false`
- `live_gpu_uat=NOT_RUN`
- `openjiuwen_real_install=NOT_RUN`
- `draft_pr=NOT_CREATED`

The next transition is separate L02/L03 worktrees and their own MIS bindings. This reconciliation branch remains docs/evidence-only.
