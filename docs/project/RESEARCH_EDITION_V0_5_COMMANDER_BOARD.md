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
| L02 openJiuwen spike | completed with conditions | openJiuwen integrator | isolated incubator runtime package/docs/tests | SHA `71cc411`; dual Phase B `PASS`; Draft PR `#122`; verified manifest `pem_re_v05_l02_71cc411e`; real install/network remains `NOT_RUN` | Keep real runtime work separately gated |
| L03 Domain | completed with conditions | research domain engineer | domain/repository/migration/tests | SHA `44a6d8f`; dual Phase B `PASS`; Draft PR `#121`; verified manifest `pem_re_v05_l03_44a6d8ff` | Keep EvidenceEdge Claim gate deferred to L05; request separate Relay allowlist integration permission |
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
- `l03_draft_pr=121`
- `l03_final_artifact=art_re_v05_l03_44a6d8ff`
- `l03_final_evaluation=eval_gw_run_gw_30889b1ffa9b_rule`
- `l03_final_manifest=pem_re_v05_l03_44a6d8ff_verified`
- `l03_exact_head_ci=FAIL_RELAY_WHEEL_ALLOWLIST_INTEGRATION_PERMISSION_REQUIRED`
- `l02_draft_pr=122`
- `l02_final_artifact=art_re_v05_l02_71cc411e`
- `l02_final_evaluation=eval_gw_run_gw_ddb3915b002a_rule`
- `l02_final_manifest=pem_re_v05_l02_71cc411e_verified`

L02 SHAs `fe045c3`, `e926889`, `d0c716b`, `0bf78fd`, `e8f8e8d`, and `9be2b3a` remain failed/superseded audit history. Exact SHA `71cc411` closes receipt binding, Unicode, canonical JSON, notice, arbitrary prefix/suffix raw-field classification, and checkpoint-body findings; it passed two independent final reviews and is published as Draft PR `#122`. L03 SHAs through `18537df` remain superseded audit history; exact SHA `44a6d8f` passed two final independent reviews and its MIS Artifact, Evaluation, ToolCall, and plan-evidence manifest are linked. PR `#121` exact-head CI still fails because the Relay wheel exact-module allowlist does not yet include the three new Research Domain modules; an in-memory proof validates the narrow fix, but that shared integration path has not been changed without separate permission. This reconciliation branch remains docs/evidence-only.
