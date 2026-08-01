# AgentOps MIS 项目材料对接基线与开发交接

> Date: 2026-07-23
> Canonical: false
> Recovery verification: 2026-08-01
> Verified repository base: `main@99ce51d693f1d646ea84acc2f7f376bde1a95a9a`
> Existing delivery line: Draft PR `#109`, branch `docs/materials-integration-handoff-20260723`

## Purpose

整合 2026-06-21 至 2026-06-23 项目讨论材料，并记录 2026-08-01 的恢复查重结论。本文件是候选 Evidence/Handoff，不声称代表当前项目状态，也不得覆盖当前 Canon 文档。

## Recovery Evidence

- `AgentOps_MIS_跨应用同步执行包_2026-07-25.zip`: `16704704a0e860602067cdaa43248e320e9c77739d0e063b29b98d2c5e2e0d31`
- `AgentOps_MIS_ChatGPT_Codex统一项目包_2026-07-25.zip`: `0adffc4788b219a630bd0f3abdf3bae8a5d88ecab006eb5105ce71a15b17a6f6`
- `AgentOps_MIS_Codex项目记忆接入包_2026-07-28.zip`: `40f808203b71d4e43b76cf7f9a130bb2e83c89ce906bcaafb3e762388229288d`
- PR #109 historical observed head `b45943e287264ca8c4924c890c06b34656b61138` was superseded by later work on the same PR line.
- The Private Host/Codex bridge and governed context-packet implementation are already represented by merged PR #112 and current `main`; the recovered bundles must not create a parallel implementation claim.

Relationship classification:

```yaml
updates: existing PR #109 and the existing Project Migration record
duplicate_of:
  - AGENTS.md
  - docs/project/PROJECT_OPERATING_RULES.md
  - merged Codex bridge/context-packet implementation
supersedes: direct application of the three historical recovery bundles
canonical: false
```

## Authority

- GitHub: 代码、branch、commit、PR、CI 事实。
- AgentOps MIS: Run、Tool、Approval、Artifact、Evaluation、Audit 事实。
- Notion Project Ledger + docs/project: 审核后的项目状态和决策。
- Chat history: source material，不是 canonical。

## Product Position

AgentOps MIS 是 Agent Control Plane，不是 LLM runtime。
它管理 Codex、Hermes、OpenClaw 等执行者的项目目标、任务、计划、审批、证据和审计。

## Integrated Tracks

1. Governance / Keep Green
2. Local Product / Private Host
3. Governed Agent & Codex Dogfood
4. Spatial OS
5. Research Lab
6. Skill / Context Self-Evolution

## Boundaries

- Spatial OS 是 MIS 状态投影，不拥有 workspace/task/run/approval/artifact/evaluation/audit 真相。
- Codex workspace-write 必须经过 Agent Plan、Prepared Action、审批、managed worktree、验证和证据链。
- External Base、MLflow、Runtime 不替代 MIS authority objects。
- Candidate Skill/Memory 不自动进入 Canonical。

## Next Actions

1. 从 GitHub 核验 exact branch/commit/PR/CI，并从可用的 Notion 连接读取 reviewed Ledger 状态。
2. 将 `PROJECT_STATE`、`BACKLOG`、`HANDOFF` 的日期和事实标记为 current 或 stale；未经证据支持不得刷新 Canonical 状态。
3. reconciliation 完成后重新确定 Private Host 和真实项目闭环的剩余验收门禁。
