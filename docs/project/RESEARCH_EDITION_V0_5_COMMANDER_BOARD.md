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
| L02 openJiuwen spike | verifying | openJiuwen integrator | isolated incubator runtime package/docs/tests | Task `tsk_re_v05_l02_openjiuwen`; Plan `plan_21c9c79c9588e3ab`; Run `run_gw_ddb3915b002a`; remediation SHA `e926889`; real install/network remains `NOT_RUN` | Independent Phase B re-verification |
| L03 Domain | remediating | research domain engineer | domain/repository/migration/tests | Task `tsk_re_v05_l03_domain`; Plan `plan_b802370e7c5eb757`; Run `run_gw_c7c6e0a62419`; SHA `138e6ab` failed second Phase B | Fix transaction/type/state-edge blockers, then fresh Phase B |
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
- `codex_goal_mode=active`
- `package_unpacked=true`
- `package_manifest_validation=PASS_39_FILES`
- `mis_ledger_write=written_and_read_back`
- `mis_task_id=tsk_re_v05_l00_99ce51d`
- `mis_plan_id=plan_336c6f572cdcd906`
- `mis_run_id=run_gw_e5fcd972a353`
- `mis_plan_quality=100`
- `canonical_state_changed=false`
- `live_gpu_uat=NOT_RUN`
- `openjiuwen_real_install=NOT_RUN`
- `control_draft_pr=120`
- `implementation_draft_prs=NOT_CREATED_PHASE_B_BLOCKED`

L02 exact SHA `fe045c3` failed independent Phase B on canonical JSON enforcement, camel-case secret keys, permission-authority consistency, and an inaccurate upstream notice filename. Remediation SHA `e926889` passes the implementer and Commander regression suites and is under independent Phase B; the failed SHA remains audit history. L03 SHA `138e6ab` fixed all first-round blockers but failed a second Phase B on autocommit revision atomicity, strict Claim/Metric types, and an unreachable cancellation edge; another bounded remediation is in progress. Candidate integration remains blocked until both lanes pass. This reconciliation branch remains docs/evidence-only.
