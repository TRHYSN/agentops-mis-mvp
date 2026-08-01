# AgentOps MIS 当前项目记忆与事实基线

> 状态日期：2026-06-24
> 审计基准：`8d1827e00629bdca4779794121ca4a31dfa3f1e1`
> 当前交付 HEAD：`8f2fa73ab72821ed7fe7c062f9e04dd84af05c20`
> 开发主线：`main`
> 相对远端分支：ahead 0 / behind 0 against `origin/main`
> 审计分支：`audit/v1-5-agent-gateway-hardening`

## 1. 基准更新

旧 `main` 只代表早期 Python + SQLite + Mock Runtime 原型，已经不能代表当前项目。

审计开始时用户提供的本地基准是：

```text
dd5e83c9a390519c28efa4b260f428a55974b5ef
Add scoped gateway review queue
```

审计进行期间，远端开发主线继续前进了 3 个 commit，当前冻结审计基准为：

```text
8d1827e00629bdca4779794121ca4a31dfa3f1e1
Add agent work method knowledge block
```

后续产品判断、Codex 任务和架构审计必须显式写出基准 SHA，不能再默认以旧 `main` 推断项目能力。

## 2. 一句话产品定位

AgentOps MIS 是本地优先、可扩展到远程 Agent 的 AI 团队管理控制面：

> 人类使用浏览器创建目标、监督、审批、复盘和交付；Agent 使用 CLI、API、未来 MCP 接口读取项目方法、检索知识、拉取任务、执行工作并把证据写回 MIS。

它不替代 Hermes、OpenClaw、Codex、LangGraph、CrewAI、JiuwenSwarm、Dify 或其他 runtime；它负责跨 runtime 的统一治理。

## 3. 当前权威链路

```text
Project Spec / Agent Workflow / Base Index
→ Knowledge Search
→ Agent Plan
→ Task
→ Run
→ Tool Call
→ Approval
→ Artifact
→ Evaluation
→ Memory Candidate
→ Audit
```

MIS SQLite/API 是权威账本。Pixel Office、Notion、Dify、外部知识库和 Runtime 内部日志都不能替代 MIS 的审批与审计权威。

## 4. 当前真实架构

```text
Human Browser Console
  ├─ goals / tasks / fleet
  ├─ review / approvals / memory
  ├─ delivery / reports / audit
  └─ Pixel Office visualizer
              │
              ▼
AgentOps MIS Control Plane
  ├─ Python HTTP server
  ├─ SQLite ledger
  ├─ Agent Gateway token/session/enrollment
  ├─ workflow jobs / commander inbox / review queue
  ├─ runtime connectors and trust
  ├─ agent plans
  └─ Markdown + SQLite FTS5 knowledge index
              │
              ▼
Machine Interface
  ├─ agentops CLI
  ├─ agentops-worker
  └─ future MCP
              │
       Mock / Hermes / OpenClaw
```

## 5. 已实现能力

### Agent Gateway

- scoped enrollment token；
- hash-only credential storage；
- token rotation/revocation；
- short-lived session；
- heartbeat 与 health state；
- workspace/agent binding；
- scope matrix；
- task pull/claim；
- run start/heartbeat；
- tool/artifact/evaluation/audit/memory/approval 写回；
- scoped tasks/runs/artifacts/approvals/memories/review queue readback。

### CLI 与 Worker

- installable `agentops`；
- installable `agentops-worker`；
- offline PEP 517 backend；
- once/loop/daemon；
- retry/backoff；
- session refresh；
- stuck recovery；
- launchd/systemd template/install/check；
- Mock/Hermes/OpenClaw adapter。

### Commander 与交付

- local readiness；
- worker status/fleet；
- adapter readiness；
- commander project board；
- integration inbox；
- workflow jobs；
- human review queue；
- customer delivery board；
- KB bot demo；
- customer project report/artifact。

### Agent Work Method Block v0

- `PROJECT_SPEC.md`；
- `AGENT_WORKFLOW.md`；
- `BASE_INDEX.md`；
- `secret_registry.md`；
- `knowledge/shared/`；
- `knowledge/bases/`；
- `knowledge/runbooks/`；
- `agent_plans`；
- `knowledge_documents`；
- SQLite FTS5 `knowledge_fts`；
- knowledge/agent-plan API 与 CLI；
- `READ → PLAN → RETRIEVE → COMPARE → EXECUTE → VERIFY → RECORD` 方法协议。

## 6. 当前准确的 P0 状态

| P0 | 当前状态 | 结论 |
|---|---|---|
| Agent Method Block | v1.5 硬门已落地 | Agent Plan hash、run-start binding、plan evidence manifest、launch packet、loop-control receipt 和 Method Block admission 已有 CI/本地 smoke；后续重点是继续扩大到更多高风险 runtime 内部动作。 |
| Shared Knowledge Index | v1.5 本地闭环已落地 | Markdown + SQLite FTS5、workspace/scoped knowledge policy、retrieval quality、worker evidence packet、repo-map localization 已有；企业级 semantic/hybrid retrieval 和多租户 ACL 仍属后续商业化层。 |
| Real Local Runtime | v1.5 产品级本地可用 | Mock/Hermes/OpenClaw worker、daemon、remote worker、retry、session、recovery、live readiness、current-code proof 均已有证据；Hermes/OpenClaw 真实执行仍需显式确认和本地 runtime 可用。 |
| Approval Wall | 核心 prepared-action 闭环已落地 | 高风险 toolcall、external-write worker preflight、runtime fixed probe、Dify upload gate、customer delivery manifest gate 已有 exact hash/checkpoint/approval/resume 证据；不能宣称已逐条 introspect Hermes/OpenClaw 内部所有工具动作。 |
| Local Coding Template | v1.5 Commander 本地开发闭环已落地 | Commander work package、repo-map localization、worktree workspace、patch/test/verifier artifact、integration inbox、merge-gate evidence 已有；商业化多人协作和 hosted branch policy 仍属后续。 |

## 7. 当前不能过度宣称的能力

### Approval

可以说：

```text
已有人类审核中心、审批记录、审计和交付/记忆审核。
```

不能说：

```text
任意真实工具动作都能在审批后从 checkpoint 精确恢复并恰好执行一次。
```

### Knowledge

可以说：

```text
已有本地 Markdown + FTS5 知识索引和 Agent Plan。
```

不能说：

```text
已经是多租户、强 ACL、语义检索完备的企业知识中台。
```

### Commercial deployment

当前适合：

```text
loopback、本地自用、课程演示、受控 dogfood、单客户验证。
```

当前不适合：

```text
匿名局域网、公网暴露、多租户 SaaS、真实高风险副作用自动执行。
```

## 8. 当前最重要的风险

1. 不要把本地 loopback v1.5 宣称成 hosted / multi-tenant SaaS；
2. Runtime 内部工具行为尚未逐动作进入 MIS，只能宣称外部/高风险入口受 prepared-action gate 保护；
3. Hermes/OpenClaw live evidence 是本地手动确认路径，不能放进 CI 或默认自动执行；
4. 商业化 RBAC、billing、tenant provisioning、secret manager、backup/restore 仍不属于当前本地 MVP 完成范围；
5. Dify/Notion live sync 当前被排除在 v1.5 active goal 之外，只保留安全边界；
6. `artifacts/sample_export_memories.json` 和 `artifacts/sample_export_runs.json` 可能因本地 server reset/smoke 漂移，除非用户明确重分类，否则不要 stage/commit；
7. 最终合并仍必须满足 exact-head green CI、clean worktree、release packet、freeze protocol 和 merge readiness strict gates；
8. 真实产品可用性 claim 必须引用当前 HEAD 的 live Hermes/OpenClaw/remote worker 证据，而不是旧 run ID 或 mock-only CI。

### 2026-06-22 Reconciliation

The list above is the original audit risk snapshot. Current v1.5 hardening work
has since closed or partially closed several items on the development branch:

- Agent Plan role separation, plan reference provenance checks, and run-start
  `agent_plan_id` / `plan_hash` binding have smoke coverage in
  `scripts/agent_plan_integrity_smoke.py` and `scripts/run_start_plan_gate_smoke.py`.
- Prepared-action Approval Wall primitives now cover high-risk tool calls,
  external-write worker preflight, Notion export, runtime fixed probes, and the
  KB-bot external upload gate. This is still not a universal introspection layer
  for every internal Hermes/OpenClaw tool action.
- Shared redaction is centralized in `agentops_mis_cli/redaction.py`; current
  hardening adds `scripts/redaction_fuzz_smoke.py` for common provider token,
  JSON, env, URL, stdout/stderr-like and truncation-before-redaction cases.
- Worker credential handling now exposes the explicit
  `trusted_worker_client_v1` boundary: credentials stay in the trusted worker
  client transport, model prompts use redacted task summaries, and
  `scripts/worker_secret_boundary_smoke.py` verifies fake task/env/URL secrets
  plus the Gateway token do not appear in worker output or ledger readback.
- Release secret scanning is now local and deterministic through
  `scripts/secret_scan_smoke.py`, which scans tracked files for token-like
  credentials and allows only narrow fake-token smoke fixtures.
- Open-source adoption is now a documented project boundary:
  `docs/OPEN_SOURCE_ADOPTION_BOUNDARY_SPEC.md` separates direct tool adoption
  from reference-only method adaptation and first-party MIS authority modules.
  Root `PROJECT_SPEC.md`, `AGENT_WORKFLOW.md`, and the v1.5 closure spec link to
  it so future framework/runtime work preserves the MIS ledger, approval,
  workspace scope, memory, delivery, and audit authority model.
- Non-local/shared deployment, collaborator exact matching, knowledge scope
  policy/redaction, SQLite WAL/busy-timeout pragmas, deterministic CI, and
  customer delivery-board evidence links now have targeted checks in the merge
  readiness checklist.
- SQLite long workflow safety is now covered by
  `scripts/sqlite_long_transaction_audit_smoke.py`: it statically audits
  `server.py` for unexpected explicit transaction statements outside the single
  short `sqlite_atomic_write` boundary and for non-autocommit SQLite
  connections, then runs an isolated temp-DB smoke proving a concurrent writer
  can insert a runtime event while the KB-bot workflow is inside a mocked long
  `subprocess.run`.
- Host authority hardening now binds Agent Plan approvals to an immutable
  `subject_type` / `subject_id` / `subject_hash`, rejects run/task mismatches,
  and commits Plan decisions plus their Approval, Runtime Event, and Audit
  evidence atomically. Human browser sessions can decide Plans without a
  second machine credential, while bound Agents still cannot self-approve.
  Tool Call plus Prepared Action composition is also atomic, and legacy local
  Agent membership backfill excludes Agents with non-local Plan authority;
  those Plan-only Agents remain visible through the Plan workspace projection.
- Worker Fleet execution capacity now comes from eligible active Worker
  Sessions and their session-bound heartbeat observations; revoked, expired,
  paused, disabled, and error-only observations do not create healthy capacity.

Still do not claim hosted/commercial readiness until the exact release-candidate
SHA has green required CI, clean-machine install/build evidence, license/SBOM
evidence, and protected live-runtime acceptance where relevant.

## 9. 当前代码规模事实

```text
server.py                         ≈ 10k lines
agentops_mis_cli/agentops.py      ≈ 1.8k lines
agentops_mis_cli/worker.py        ≈ 1.3k lines
AIEmployees.tsx                   > 1.3k lines
liveApi.ts                        > 2.2k lines
```

下一阶段应绞杀式拆分，而不是大爆炸重写，也不应继续把横向功能塞进单文件。

## 10. 近期唯一正确主线

```text
冻结横向功能
→ 修复执行与权限正确性
→ 建立 CI 和性能基线
→ 形成 v1.5 RC
→ 合并 main
→ 补齐 durable approval 与 coding template
→ 扩展 Research Lab Template
```

## 11. 远期愿景继续保留

- 一人公司模板；
- 科研团队 / AI for Science；
- GPU、服务器、实验、模型、数据集、论文；
- Agent work package 与团队项目板；
- JiuwenSwarm / LangGraph / CrewAI adapters；
- Agent/Skill/Template Marketplace；
- Agent 雇佣、评价、计费和安全沙箱；
- SaaS、BYOC、私有化与企业审计；
- AI 时代公司操作系统。

当前 Gateway、Worker、Method Block、Knowledge Index、Review Queue 和 Evidence Ledger 是这些长期能力的共同底座。

## 12. 后续 Codex 必读规则

任何修改前必须：

1. 写明当前 branch 和 SHA；
2. 读取本文件、Project Spec、Agent Workflow、Base Index 和相关 spec；
3. 搜索现有实现，不得平行再造；
4. 创建并验证 Agent Plan；
5. 引用真实存在的 spec、memory、base 和文件；
6. 给出验证与回滚；
7. 保留 workspace、scope、audit、redaction、confirm-run 边界；
8. 不提交数据库、credential、原始 prompt/response、私聊和客户原文；
9. 不以状态改成 completed 代替真实执行证据；
10. 不以文档中的历史通过记录代替当前 HEAD 的 CI。

## 13. 异步并行执行硬约束

Added: 2026-06-23

用户已经多次纠正：AgentOps MIS 快速产品交付不能串行等待。后续 Codex、
子代理和自动续跑必须把异步并行当成执行要求，而不是风格偏好。

- MUST 把 CI、浏览器构建、live runtime、长命令和子代理工作拆成
  asynchronous lanes；启动预计超过 60 秒的 lane 后，立刻推进另一条安全
  可验证的实现、验证、文档或集成 lane。
- MUST 在实质性产品交付期间维护 compact commander board，写清 running
  lanes、merged results、blockers 和 next lane；任何有意等待前都要说明
  为什么没有独立安全 lane 可推进。
- MUST 不等齐所有子代理、不为了整洁批量关闭子代理、不把 CI/live runtime
  等待当成主线停顿理由；慢 lane 结果回来后再合并。
- MUST 把子代理容量限制看成调度约束，而不是阻塞；开不了子代理时继续主线
  最高价值 slice，并记录稍后补跑的 lane。
- MUST 在 context compaction、heartbeat resume 和跨天续跑后保留这套
  lane board，不重新退回串行等待。

工作区根规则已写入 `/Users/wuji/Documents/MIS/AGENTS.md`；当前仓库详细执行
规则仍以 `AGENTS.md` 第 5 节为准。

## 14. Loop Control 最新契约

- `agentops operator advance-loop --confirm-advance` 的 `control_readback`
  仍是一次推进的前后读回执；确认路径必须请求
  `refresh_cache=true`，并返回 handoff/self-check 两份后置控制摘要。
  已确认推进还必须把同一份读回执作为 append-only
  `operator.action_queue_control_readback` 审计行挂到 Action Queue receipt
  上，`/api/operator/action-receipts` 和 `/workspace/agents` 需要展示
  before/after/self-check、cache bypass 与 tamper hash 证明。
  Action Queue receipt summary 还必须计算 control readback required/attached/
  missing/coverage/status，并由 operator health 的 `control_readbacks` gate
  把缺失读回执作为 attention 风险暴露。
- `agentops operator handoff` 的 `loop_health.gates.loop_control` 是当前
  推荐控制面的机器可读审计入口，字段包含 mode、selected gate、next
  action、verify command、receipt command、copy-only、server-shell boundary、
  `control_readback_source` 和 post-receipt cache refresh 需求。
- `agentops operator health` 必须暴露同一份 `control_summary`/`loop_control`
  并把它作为健康组件；否则总览看不到 Hermes/OpenClaw/Codex 下一步应当
  复制、确认、验收还是等待回执。
- `agentops operator loop-audit` 的 RECORD evidence 必须包含 handoff、
  self-check、advance preview 和 `advance-loop --confirm-advance` readback
  来源，避免控制推荐只存在于 CLI 输出而不进入审计视图。
- `/workspace/agents` 的 Operator Command Center 必须把
  `operatorHealth.control_summary` / `operatorHealth.loop_control` 作为一等
  可视化证据展示，包括当前 selected gate、control mode、human/receipt
  requirement、readback source 和 post-receipt cache refresh 状态。
