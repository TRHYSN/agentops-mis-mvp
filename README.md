# AgentOps MIS / AI Workforce MIS MVP

这是一个面向“一人公司 / 小团队”的 **AgentOps MIS（AI 数字员工管理信息系统）** 本地原型包。它不是 LLM runtime、不是 agent builder；它是运行在 agent runtime 之上的管理控制面。默认本地优先、外部写入关闭，只有显式确认后才会尝试 Notion 导出或固定安全 runtime probe。

Public claim boundary: v1.5 is for loopback local use, classroom demonstration,
controlled dogfood and single-customer validation with explicit confirmation.
It does not claim hosted SaaS, full multi-tenant RBAC, billing or universal
runtime per-action governance. See
`docs/PUBLIC_CLAIMS_AND_LIMITATIONS.md` before writing demos, release notes or
customer-facing descriptions.

- Agent Registry
- Task Management
- Run Ledger
- Tool Call Ledger
- Approval Workflow
- Organizational Memory
- Evaluation / Quality Gate
- Dashboard
- Audit Log
- OpenClaw local import / probe
- Hermes local health probe
- Agnesfallback dry-run / fixed probe adapter
- Notion External Base dry-run connector
- Template + Base switching preview

## Open Source Base Index

`docs/research/OPEN_SOURCE_BASE_INDEX_V1_1.md` and
`docs/research/OPEN_SOURCE_BASE_REGISTRY_V1_1.yaml` preserve the governed
open-source experiment base index. The index is decision support for adapters,
retrieval, evaluation, CI, security scanning and runtime experiments; it does
not replace AgentOps MIS authority objects such as tasks, runs, approvals,
artifacts, evaluations, reviewed memories or audit logs.

## 产品轨道与技术栈

仓库现在保留两条边界明确的产品轨道：

- **Free Local**：Python + SQLite 和现有本地 UI，继续承担单机体验、兼容回滚和离线开发。
- **Commercial / BYOC**：`ui/next-app/` 中的 Next.js 16 + TypeScript + Node.js 20，以及 `migrations/postgres/` 中的 PostgreSQL 16 权威账本。

Commercial 的 production/shared 模式禁止把控制面写请求代理给 Python，也不会把 SQLite 当成商业权威数据源。尚未迁移的生产路由会 fail closed；只有 Free Local 的显式 allowlist 可以使用本地 Python 兼容路径。

商业迁移仍在进行中，当前分支不是发布完成声明。迁移闭环、各 owner 的退出条件、真实 Hermes/OpenClaw 验收和剩余发布门槛见：

- `docs/COMMERCIAL_MIGRATION_CLEAN_ROOM_BREAKDOWN.md`
- `docs/COMMERCIAL_TYPESCRIPT_WORKER.md`
- `docs/project/PROJECT_STATE.md`
- `docs/project/BACKLOG.md`

商业 Worker 已有独立的 Node.js/TypeScript 入口：

```bash
cd ui/next-app
export AGENTOPS_RUN_ESTIMATED_COST_USD=1.000000
npm run worker:commercial -- --adapter hermes \
  --estimated-cost-usd "$AGENTOPS_RUN_ESTIMATED_COST_USD" --confirm-run
```

它只调用 `/api/mis/agent-gateway/*`，不直接依赖 Python、SQLite 或
PostgreSQL 驱动。Agent 凭证只能通过环境变量传入；完整运行方式和真实
Hermes/OpenClaw 证据边界见 `docs/COMMERCIAL_TYPESCRIPT_WORKER.md`。

## 快速运行

```bash
cd agentops-mis-mvp
python3 server.py --reset
python3 server.py
```

打开：

```text
http://127.0.0.1:8787/dashboard
```

## Local Product UI

Vite UI 位于 `ui/start-building-app/`，但它不是数据权威。Free Local 构建连接
Python/SQLite 兼容后端；Commercial / BYOC 构建只连接同源 Next.js
`/api/mis`，由 TypeScript/PostgreSQL 控制面持有权威账本。

一条命令启动本地后端、`19001` UI 和安全 mock worker：

```bash
python3 scripts/run_local_stack.py --install-ui
```

依赖已经装过时可省略 `--install-ui`：

```bash
python3 scripts/run_local_stack.py
```

显式启动真实 Hermes 和 OpenClaw worker：

```bash
python3 scripts/run_local_stack.py \
  --worker hermes \
  --worker openclaw \
  --confirm-live-workers \
  --configure-cli
```

真实 worker 只有在同时传入 `--confirm-live-workers` 时才会启动。脚本默认不改写
`~/.agentops/config.json`；只有显式 `--configure-cli` 才保存本地 `8787` 连接。

也可以手动启动 UI：

```bash
cd ui/start-building-app
npm install
npm run dev -- --host 127.0.0.1 --port 19001
```

打开：

```text
http://127.0.0.1:19001/
```

构建检查：

```bash
npm run build
```

## 关键验收路径

1. 打开 `/dashboard` 看总览。
2. 去 `/agents` 创建 Agent。
3. 去 `/tasks` 创建任务并分派 Agent。
4. 进入任务详情点击 `Start mock run`。
5. 查看 `/runs` 和 `/tool-calls`。
6. 如果生成高风险 tool call，进入 `/approvals` 批准或拒绝。
7. 注意普通审批是账本/交付/审核决策；只有带 `prepared_action` 的审批才代表批准后可按 action hash 精确恢复，并且仍需要单独 resume。
8. Run 完成后查看 `/evaluations`。
9. 查看 `/memory` 中的候选组织记忆，并批准/拒绝。
10. 查看 `/audit` 的审计记录。
11. 打开 `/integrations`，运行 OpenClaw 本地导入、OpenClaw 手动 probe、Hermes 手动 probe，并查看 Notion dry-run/export。

## v1.2.1 可录屏复现路径

生成红acted、大规模、可复现 demo 数据：

```bash
python3 scripts/demo_seed_openclaw_redacted.py --reset
```

一键验收本地 API：

```bash
python3 scripts/demo_acceptance.py --start-server
```

验收覆盖：

- Dashboard runtime health 和 Agent performance summary。
- OpenClaw status。
- Hermes/Agnesfallback status、models、dry-run CLI probe。
- Notion status、preview、dry-run export。
- Bases、connectors、template packages、migration preview。
- SQLite 中 audit/runtime/template/bases 基础数据存在。

除 Dify / Notion 之外的本地 runtime live 验收：

```bash
HERMES_ALLOW_REAL_RUN=true \
HERMES_REQUIRE_CONFIRM_RUN=true \
AGNESFALLBACK_GATEWAY_URL=http://127.0.0.1:8642 \
python3 server.py

python3 scripts/local_runtime_acceptance.py \
  --live-openclaw \
  --live-hermes \
  --live-agnesfallback \
  --live-agnesfallback-api \
  --require-hermes-api \
  --require-agnesfallback-api
```

这条验收会跑 Agent Gateway CLI smoke、OpenClaw import/probe、Hermes default gateway fixed run-task、Agnesfallback CLI fixed probe 和 Agnesfallback OpenAI-compatible fixed probe。它不会调用 Dify 或 Notion 写入路径。

## Local live recording mode

默认可复现 demo 仍然是 dry-run，这是 GitHub clone 后最安全的行为。录制本机视频时，如果要展示一次真实 Agnesfallback fixed probe，可以按 `docs/LIVE_DEMO_RUNBOOK.md` 显式开启 live mode：

- 不要把 `HERMES_ALLOW_REAL_RUN=true` 写入 `.env.example`。
- 启动 server 前只在当前 shell 中 `export HERMES_ALLOW_REAL_RUN=true`。
- 调用 probe 时必须传 `{"confirm_run": true}`。
- 录完 `unset HERMES_ALLOW_REAL_RUN`，停止临时 gateway，并确认 `agentops_mis.db` 没有被提交。

## Pixel Office Demo Mode

v1.3 当前默认使用原创 React/CSS Pixel Operating Map，不复制 Star-Office-UI 资产。它把 AgentOps MIS 的 agents / tasks / runs / approvals / memory / audit 状态映射到一个可点击运营大厅。

当前本地 demo 主入口：

- `http://127.0.0.1:19001/workspace/pixel-office`：原生 Pixel Office 前台。顾客可以提交任务、选择 AI 员工，并跳转到正式 MIS 页面。
- `http://127.0.0.1:19001/admin`：MIS 后台管理端。查看控制塔、Run Ledger、Tool Calls、Evaluations、Connectors、External Bases 和 Audit。
- `http://127.0.0.1:19000/workspace`：可选 legacy Star-Office visualizer，仅当你单独启动 Star-Office-UI 时使用。

重要边界：

- Star-Office-UI 只作为课程 demo / 本地录屏视觉层。
- 它不替代 AgentOps MIS Core，MIS 的 SQLite/API 仍是权威账本。
- 本仓库不提交 Star-Office-UI 美术资产。
- Star-Office-UI 资产仅限非商业学习、演示、交流用途；正式发布、商业化、官网宣传、对外产品版本必须换成原创 AgentOps MIS Pixel Office assets。

文档：

- `docs/STAR_OFFICE_UI_DEMO_INTEGRATION.md`
- `docs/PIXEL_OFFICE_ASSET_REPLACEMENT_PLAN.md`

## Agent Gateway Customer Task Demo

v1.4 增加了最小 Agent Gateway/API slice，用于让本机或远程 AI 员工通过 CLI/API/MCP 写入 MIS，而不是让 agent 操作浏览器 UI。

客户本地部署、运维、远程 agent 接入、备份/恢复路径见：

- `docs/CUSTOMER_LOCAL_DEPLOYMENT_RUNBOOK.md`
- `docs/REMOTE_WORKER_OPERATIONS_RUNBOOK.md`

本地 CLI wrapper / 可安装 CLI：

```bash
./scripts/agentops --help
python3 scripts/install_agentops_cli.py
agentops --help
python3 -m pip install -e .
python3 -m pip install .
agentops doctor
agentops status
agentops demo readiness
agentops local readiness
agentops review queue
agentops workflow delivery-board
./scripts/agentops login --base-url http://127.0.0.1:8787 --workspace-id local-demo --agent-id agt_local_worker
./scripts/agentops agent register --id agt_local_worker --name "Local Worker" --role "AI Digital Employee"
./scripts/agentops task create \
  --title "客户提交的 AI 知识库任务" \
  --description "清洗资料、建立知识库、返回可验收的问答机器人交付摘要。" \
  --owner-agent-id agt_local_worker \
  --acceptance "必须写入 run/tool/evaluation/audit 证据。"
./scripts/agentops task pull --agent-id agt_local_worker
```

`scripts/install_agentops_cli.py` installs a small local shim at `~/.local/bin/agentops`. It does not store tokens; auth remains env/config based.

`pyproject.toml` also exposes the same command through a Python console script:

```bash
python3 -m pip install -e .
# or, for a source-package style install:
python3 -m pip install .
agentops --help
agentops doctor
agentops status
```

This install path is intended for local/remote agent machines that already have a Python environment. It keeps the same JSON output contract and still reads auth from env/config; it does not create tokens by itself.
The package uses a tiny offline build backend, so `python3 -m pip install .` does not need to download setuptools or wheel during local source installs.
`agentops doctor` is a deployment gate as well as a diagnostic: local loopback
demo mode returns exit code 0, but unsafe shared/production targets without a
Gateway token return a non-zero exit code while still printing redacted JSON.

已支持：

```text
agentops login
agentops doctor
agentops status
agentops enrollment create/list/revoke/rotate
agentops agent register
agentops agent heartbeat
agentops task create
agentops task pull
agentops task claim
agentops run start
agentops run heartbeat
agentops toolcall record
agentops artifact record
agentops approval list
agentops approval request
agentops approval approve/reject
agentops memory list
agentops memory propose
agentops memory approve/reject
agentops eval submit
agentops audit emit
agentops review queue
agentops workflow run-task
```

远程/外部 agent 最小接入：

```bash
./scripts/agentops enrollment create \
  --agent-id agt_remote_builder \
  --name "Remote Builder" \
  --runtime openclaw \
  --save-token

./scripts/agentops agent heartbeat --id agt_remote_builder --status idle
./scripts/agentops task pull --agent-id agt_remote_builder
./scripts/agentops enrollment rotate --agent-id agt_remote_builder --ttl-days 30
```

远程 token + worker 完整 smoke：

```bash
python3 scripts/remote_agent_token_worker_smoke.py
python3 scripts/enrollment_rotation_smoke.py
python3 scripts/workspace_isolation_smoke.py
python3 scripts/agentops_pip_install_smoke.py
python3 scripts/agentops_doctor_smoke.py
python3 scripts/agentops_worker_status_smoke.py
python3 scripts/agentops_task_create_cli_smoke.py
python3 scripts/agent_gateway_task_create_scope_smoke.py
python3 scripts/agentops_workflow_run_task_smoke.py
```

它会创建 scoped token、创建一个普通 MIS 任务、用 token 跑 `scripts/agent_worker.py --once`、验证 run/tool/eval 证据，并默认吊销 token。
Rotation smoke 会验证 API/CLI token 轮换：旧 token 变为 revoked，新 token active，输出不包含原始 token。
Workspace isolation smoke 会验证：token 绑定 workspace A 后，只能 pull/claim/start/write workspace A 的任务和 run；header/query/body 伪造 workspace B 会返回 403。

注意：

- enrollment token 只在创建响应里显示一次，MIS 只存 hash。
- token 绑定 `agent_id`，不能冒充其他 agent。
- token 绑定 `workspace_id`，不能通过 header/query/body 切换到其他 workspace。
- API 会检查 endpoint scope，例如 `tasks:create`、`tasks:read`、`runs:write`、`audit:write`。
- `./scripts/agentops demo readiness` 可查看 v1.5 录屏主路径是否齐备：local readiness、安全边界、worker fleet lanes、async inbox、客户任务闭环和 run ledger 证据。它只读，不启动 worker、不写账本、不触发 live runtime。
- `./scripts/agentops commander plan --goal "..."` 可把一个客户/项目目标预览拆成多条 AI 团队工作包；加 `--confirm-create` 后才写入 planned MIS tasks，并记录 commander runtime/audit evidence。`./scripts/agentops commander packages` 可读回持久化工作包状态、最新 run 和 evidence counts。浏览器入口在 `/workspace/agents` 的 Commander Work Package Planner，详见 `docs/COMMANDER_WORK_PACKAGE_PLANNER.md`。
- `./scripts/agentops worker status` 可从命令行查看 worker fleet、daemon、pending task 和 stuck task 状态。
- `./scripts/agentops worker hygiene` 默认只读，会列出 stuck worker tasks 和超过阈值仍 never-seen 的 enrollment；只有加 `--apply --confirm-cleanup` 才释放任务、吊销 stale token，并写入 audit/runtime evidence。
- `./scripts/agentops local readiness` 可查看单机开源版闭环体检：Agent Gateway、worker route、memory/knowledge、approval、task->run->tool/eval/audit/artifact 证据、runbook 是否齐备。它只读，不启动 worker、不拉任务、不触发 Hermes/OpenClaw live runtime。返回里的 `local_run_path` 会给出可复制的启动、预检、worker、service-control 预览、派活和验收命令；这些命令只展示给操作者复制执行，server 不会代替用户执行 shell。
- `./scripts/agentops worker preflight --adapter mock|hermes|openclaw` 可从主 CLI 执行只读 Gateway/adapter 预检，不拉任务、不写账本、不触发 live runtime。
- `./scripts/agentops worker start|stop|logs` 可从命令行控制本地 worker daemon；Hermes/OpenClaw start 必须显式 `--confirm-run`。
- `./scripts/agentops enrollment revoke --agent-id agt_remote_builder` 可吊销该 agent 的 active token。
- `/admin/connectors` 可把 runtime connector 标记为 trusted / review_required / blocked；blocked 的 Hermes/OpenClaw connector 会阻止 confirmed customer worker live execution，并写入 runtime event 与 audit。

知识库机器人客户任务演示：

```bash
python3 scripts/run_kb_bot_demo.py
python3 scripts/kb_bot_demo_smoke.py
python3 scripts/kb_bot_workflow_api_smoke.py
python3 scripts/customer_worker_task_workflow_smoke.py
python3 scripts/customer_worker_live_dogfood.py --adapter hermes
python3 scripts/customer_worker_live_dogfood.py --adapter openclaw
```

CLI 方式从客户/外部 agent 侧派发 worker 任务：

```bash
./scripts/agentops workflow customer-worker-task \
  --adapter mock \
  --title "优化 AgentOps MIS 客户工作台" \
  --description "以客户视角审视任务创建、AI 执行、审批、评估、审计和交付报告闭环。" \
  --acceptance "必须返回 run、tool、evaluation、audit 和 artifact 证据。"
```

交付完成后，用只读看板确认客户结果确实进入 MIS 管理账本：

```bash
./scripts/agentops review queue --limit 12
./scripts/agentops workflow delivery-board --limit 10
./scripts/agentops approval list --decision pending --limit 10
./scripts/agentops approval approve --approval-id ap_...
./scripts/agentops memory list --status candidate --limit 10
./scripts/agentops memory approve --memory-id mem_...
curl -fsS http://127.0.0.1:8787/api/workflows/customer-delivery-board?limit=10 | jq .
curl -fsS http://127.0.0.1:8787/api/review/queue?limit=12 | jq .
```

这个看板聚合 delivery artifact、task、run、approval、evaluation、audit evidence 和下一步动作；它不启动 worker、不写账本、不触发 live runtime。交付审批可以继续走浏览器 `/workspace/approvals`，也可以用 `agentops approval approve/reject` 在 CLI 里完成；项目记忆候选同理可在 `/memory` 或 `agentops memory approve/reject` 审核。
`agentops review queue` 是更高层的人类审核队列：它把待审批 gate、候选记忆和客户交付状态合到一个只读列表里，适合总指挥在多个 worker/子线程速度不同的时候先处理已返回的工作。浏览器/本地 UI 保留 `GET /api/review/queue`；CLI/远程 agent 使用 scoped Agent Gateway 路径 `GET /api/agent-gateway/review/queue`，token 需要 `tasks:read`，并且只返回该 token 绑定 workspace/agent 可见的队列项。
`agentops approval list` 和 `agentops memory list` 也走 scoped Agent Gateway 读路径；远程 agent 可以看见自己可见任务/运行关联的审核项，但 approve/reject 仍是人类/管理员决策，不给 agent token 自动越权。

更底层的 agent/API 方式是先创建普通 MIS 任务，再由 worker 拉取执行：

```bash
./scripts/agentops task create \
  --title "优化 AgentOps MIS 客户工作台" \
  --description "以客户视角审视任务创建、AI 执行、审批、评估、审计和交付报告闭环。" \
  --owner-agent-id agt_local_worker \
  --priority high \
  --risk medium

agentops-worker --once --adapter mock --agent-id agt_local_worker
```

这条路径用于本地或远程 agent 接入：浏览器 UI 给人看、审批和复盘；agent 使用 CLI/API 创建/拉取/认领任务并写回证据。

也可以用一条 CLI 命令完成“创建普通任务并执行一次 worker”：

```bash
./scripts/agentops workflow run-task \
  --adapter mock \
  --worker-agent-id agt_local_worker \
  --title "优化 AgentOps MIS 客户工作台" \
  --description "以客户视角审视任务创建、AI 执行、审批、评估、审计和交付报告闭环。"
```

Hermes/OpenClaw 真实执行仍必须显式加 `--confirm-run`。

预定义客户项目模板也可以不经过浏览器，直接给本地/远程 agent 或脚本调用：

```bash
./scripts/agentops workflow templates
./scripts/agentops workflow run-template --template-id tpl_customer_kb_qa_bot
```

这会走 `GET /api/workflows/customer-task-templates` 和 `POST /api/workflows/customer-task-templates/run`，返回 project/task/run/artifact/approval/report URL 等账本证据。

如果要让模板真的调用本地/远程 agent worker，而不是只走模板默认安全工作流，可以显式指定 adapter：

```bash
./scripts/agentops workflow run-template \
  --template-id tpl_customer_ui_review \
  --adapter openclaw \
  --confirm-run \
  --request-timeout 420
```

`mock` 会立即写真实账本；`hermes` / `openclaw` 必须加 `--confirm-run`，否则只创建 planned task。长任务可用 `--request-timeout` 或 `AGENTOPS_REQUEST_TIMEOUT`。

更像产品/远程 agent 的用法是异步提交再轮询：

```bash
./scripts/agentops workflow run-template \
  --template-id tpl_customer_ui_review \
  --adapter hermes \
  --confirm-run \
  --async-job

./scripts/agentops workflow job-status --job-id wfjob_... --wait --timeout 420
```

这避免真实 Hermes/OpenClaw 长任务占住一个同步 HTTP 请求，同时仍然回写 run、tool、evaluation、audit、artifact、memory 和 approval。

它会模拟 AI 团队完成“正式 AI 知识库 / 问答机器人”项目：

- 注册 Project Planner、Document Cleaner、Knowledge Base Builder、Q&A Evaluator、Customer Report Writer。
- 创建并认领任务。
- 写入 Run Ledger、Tool Calls、Runtime Events、Evaluations、Memories 和 Audit。
- 通过 Agent Gateway 登记一份客户交付摘要 artifact，可从任务/运行详情看到。
- 对 Dify / OpenAI File Search / AnythingLLM 外部上传创建 pending approval，不上传原始资料、不保存凭证。
- 也可以从 Pixel Office 里的“一键生成知识库机器人项目”按钮触发同一条浏览器工作流，后端接口是 `POST /api/workflows/kb-bot-project`。
- Pixel Office 的客户派活面板也能触发 `POST /api/workflows/customer-worker-task`：客户任务进入 Agent Gateway worker，mock/Hermes/OpenClaw adapter 执行后写回 run、tool call、evaluation、audit、`customer_worker_result` artifact、`agent_plan` 和 verified `plan_evidence_manifest`。交付审批只会在 manifest 门禁通过后生成；Hermes/OpenClaw 仍需显式确认。

## Local Open Source Experiment Base

本地开源底座不是把 LangGraph、CrewAI、OpenHands、Dify 或 UI 模板变成新的任务权威，而是把它们作为参考、工具或 runtime adapter 输入，统一落到 MIS 的知识检索、Agent Plan、evaluation case、artifact、memory candidate 和 audit 证据链里。

核心 spec：`docs/LOCAL_OPEN_SOURCE_EXPERIMENT_BASE_SPEC.md`。

只读验收：

```bash
python3 scripts/local_open_source_experiment_base_smoke.py
./scripts/agentops knowledge evidence-packet "open source experiment base" --limit 5
./scripts/agentops eval propose-case --source-type manual --title "Local experiment regression" --expectation "Experiment learnings must become reviewable MIS evidence before product adoption."
```

这条 smoke 不刷新知识索引、不写 SQLite、不调用 Hermes/OpenClaw、不联网；它只确认开源采纳边界、知识底座、实验脚本、evaluation case API/CLI 和 CI 入口还连在一起。真实 OpenClaw/Hermes 实验仍走显式确认和账本证据。

## v1.5 Local Agent Worker Loop

`agentops-worker` 是可安装的 worker daemon 命令。它通过 Agent Gateway API 拉取普通 MIS 任务，认领后调用 adapter，并把 run/tool/eval/artifact/audit、`agent_plan` 和 `plan_evidence_manifest` 写回 MIS。`scripts/agent_worker.py` 仍保留为 repo-local 兼容 wrapper，供本地 UI/smoke 继续使用。

Hermes/OpenClaw 监督 loop 也可以作为 MIS workflow 运行：

```bash
agentops workflow hermes-openclaw-loop \
  --topic "Review the next AgentOps MIS loop guardrail" \
  --rounds 1

agentops workflow hermes-openclaw-loop --readback --loop-id loop_...
```

这条 lane 默认 dry-run，live Hermes/OpenClaw 需要 `--confirm-live`。运行时写入 parent/child task/run、tool/eval/artifact/audit、每条 lane 的 `agent_plan` 和 `plan_evidence_manifest`；`--resume` 会复用 `.agentops_runtime/loops/` 里的 gitignored JSONL 继续缺失轮次，blocked lane 会保留 blocked manifest 供 operator 回读。

8 点产品闭环目标与总 spec 见 `docs/V1_5_EIGHT_PRODUCT_CLOSURE_SPEC.md`。

客户/远程机器安装后运行：

```bash
python3 -m pip install .
agentops doctor
agentops worker preflight --adapter mock --agent-id agt_worker_local
agentops-worker preflight --adapter mock --agent-id agt_worker_local
agentops-worker --once --adapter mock --agent-id agt_worker_local
agentops-worker --adapter mock --poll-interval 5 --max-tasks 0 --continue-on-error --write-state --jsonl-log
agentops-worker service-template --manager launchd --adapter mock --agent-id agt_worker_local > ~/Library/LaunchAgents/local.agentops.worker.agt_worker_local.plist
agentops-worker service-install --manager launchd --adapter mock --agent-id agt_worker_local
agentops-worker service-install --manager launchd --adapter mock --agent-id agt_worker_local --confirm-install
agentops-worker service-template --manager systemd --adapter mock --agent-id agt_worker_local > ~/.config/systemd/user/agentops-worker-agt_worker_local.service
agentops-worker service-check --manager launchd --adapter mock --agent-id agt_worker_local
agentops worker service-check --manager launchd --adapter mock --agent-id agt_worker_local
agentops worker service-install --manager launchd --adapter mock --agent-id agt_worker_local
agentops worker service-control --manager launchd --action restart --adapter mock --agent-id agt_worker_local
```

安装版 worker 默认把 state 写入 `~/.agentops/workers`；repo 内 wrapper 默认写入 `.agentops_runtime/workers`。可用 `AGENTOPS_WORKER_RUNTIME_DIR` 覆盖 state 目录，用 `AGENTOPS_WORKER_CWD` 覆盖 OpenClaw adapter 的执行目录。
`service-template` 只生成带 token placeholder 的 launchd/systemd 模板，不会自动安装、加载服务，也不会写入真实 token。
`agentops-worker service-install` 和 `agentops worker service-install` 默认只做 dry-run；加 `--confirm-install` 后才把安全模板写到 launchd/systemd 路径，文件权限为 `0600`，仍不会写入真实 token、不会加载服务、不会启动 worker。
`agentops worker preflight` 和 `agentops-worker preflight` 都是只读 adapter 预检：检查 Gateway/adapter 可用性，不执行真实任务、不写账本、不保存 prompt/response。
`agentops-worker service-check` 和 `agentops worker service-check` 是只读服务诊断：检查 launchd/systemd 模板文件、adapter 参数、session/confirm-run 保护、自动重启策略（launchd `KeepAlive=true` / systemd `Restart=always`）、服务加载状态和 token-like 泄露风险，不会安装、加载、重启服务，也不会打印服务文件原文。
`agentops-worker service-control` 和 `agentops worker service-control` 默认只预览 launchd/systemd load/unload/restart 命令；只有加 `--confirm-control` 才调用本机服务管理器。Hermes/OpenClaw 服务模板如果缺少 `--confirm-run` 会拒绝 load/restart，含 token-like 内容的服务文件也会被拦截。确认执行 `load` 时如果服务已经 loaded，会作为幂等 no-op 返回 `service_control_skipped:true`，不会重复调用 launchd/systemd，也不会计为 live runtime execution。
完整本地/远程 worker 运维路径见 `docs/REMOTE_WORKER_OPERATIONS_RUNBOOK.md`。

单轮 mock：

```bash
python3 scripts/agent_worker.py --once --adapter mock --agent-id agt_worker_local
```

本地闭环体检：

```bash
curl -fsS http://127.0.0.1:8787/api/local/readiness | jq .
./scripts/agentops local readiness
python3 scripts/local_readiness_smoke.py
```

这条检查会汇总 `/workspace/agents`、Agent Gateway CLI/API、memory/knowledge、approval、run/tool/eval/audit/artifact 证据链和本地 runbook；它不执行任务，也不会打印 token。

单轮 Hermes live adapter：

```bash
python3 scripts/agent_worker.py \
  --once \
  --adapter hermes \
  --confirm-run \
  --agent-id agt_worker_local \
  --hermes-gateway-url http://127.0.0.1:8642
```

单轮 OpenClaw live adapter：

```bash
python3 scripts/agent_worker.py \
  --once \
  --adapter openclaw \
  --confirm-run \
  --agent-id agt_worker_local
```

循环模式：

```bash
python3 scripts/agent_worker.py --adapter mock --poll-interval 5 --max-tasks 10
python3 scripts/agent_worker.py --adapter mock --poll-interval 5 --max-tasks 0 --continue-on-error --max-errors 5 --write-state --jsonl-log
```

浏览器派发：

- `/workspace/agents` 现在有 “本地 Worker 循环 / Local Worker Loop” 面板。
- `/workspace/agents` 现在也有 “客户任务派发 / Customer Task Dispatch” 面板：用户填写一个正常业务任务，选择 mock/Hermes/OpenClaw adapter，系统通过 `POST /api/workflows/customer-worker-task` 创建任务、执行 worker，并显示 task/run/artifact/evidence/plan-evidence 链接。
- 它可以从页面触发一次 `mock`、`Hermes` 或 `OpenClaw` worker run。
- 它也可以启动/停止本地 mock / Hermes / OpenClaw daemon，让 worker 持续拉取普通 MIS 任务。
- 它可以查看 “Worker Fleet 观测 / Worker Fleet Telemetry”，包括 daemon 状态计数、错误计数、state 文件路径、日志尾部和最近 Agent Gateway runtime events。
- 它也包含 “远程 Agent 接入 / Remote Agent Enrollment” 面板，可以用权限预设创建、查看、轮换和吊销 scoped enrollment token；原始 token 只在创建/轮换后显示一次。
- 后端会先创建普通 MIS 任务，再调用 `scripts/agent_worker.py --once`，结果写入 Run Ledger、Tool Calls、Evaluations 和 Audit。
- Hermes/OpenClaw 页面派发仍会带显式确认，不改变默认安全策略。

Worker API：

```bash
curl -fsS http://127.0.0.1:8787/api/workers/status | jq .
curl -fsS -X POST http://127.0.0.1:8787/api/workers/local/dispatch-once \
  -H "Content-Type: application/json" \
  -d '{"adapter":"mock"}' | jq .
curl -fsS -X POST http://127.0.0.1:8787/api/workers/local/start \
  -H "Content-Type: application/json" \
  -d '{"adapter":"mock","poll_interval":2,"max_tasks":0}' | jq .
curl -fsS -X POST http://127.0.0.1:8787/api/workers/local/stop \
  -H "Content-Type: application/json" \
  -d '{"adapter":"all"}' | jq .
python3 scripts/worker_daemon_resilience_smoke.py
```

边界：

- 不调用 Dify / Notion。
- 不保存完整 prompt、raw response、credentials、transcripts。
- Hermes/OpenClaw 真实执行必须显式传 `--confirm-run`。
- 页面 daemon 控制是本地录屏/自用 supervisor；现在有 state/JSONL/error counters 和 bounded continue-on-error，但仍不是 launchd/systemd 或远程 fleet manager。
- Free Local worker 仍可通过 Python source package 安装为 `agentops-worker`；
  Commercial / BYOC 使用 `ui/next-app/` 的 TypeScript worker。远程
  enrollment 已有 scoped token、rotation、short-lived session、Human
  administration 和 workspace entitlement，但最终 approval-gated
  enrollment、完整 BYOC 升级/回滚证据和发布 promotion 仍未关闭。

Dify 可以作为本地或客户服务器上的 agent 工具层，而不是 MIS 的替代品。MIS 负责记录任务、运行、工具、审批、评估和审计；Dify 负责知识库/工作流/问答应用。查看 Dify 当前信任域和配置：

```bash
curl -fsS http://127.0.0.1:8787/api/integrations/dify/status | jq .
```

本地 agent 触发一次安全 dry-run：

```bash
python3 scripts/dify_local_agent_demo.py
```

本地/私有 Dify 真正上传需要显式环境变量和确认：

```bash
export DIFY_API_BASE_URL="http://127.0.0.1:8088/v1"
export DIFY_KB_API_KEY="..."
export DIFY_DATASET_ID="..."
export DIFY_ALLOW_REAL_UPLOAD=true
python3 scripts/dify_local_agent_demo.py --confirm-upload
```

云端或跨信任域 Dify 默认还需要传入已批准的 `approval_id`。不管 dry-run 还是真实上传，MIS 都不会保存 API key 或完整文档正文。

录屏可看：

- `/workspace/pixel-office`
- `/workspace/tasks`
- `/admin/runs`
- `/admin/toolcalls`
- `/workspace/approvals`
- `/admin/evaluations`
- `/admin/audit`

详情见 `docs/AI_KNOWLEDGE_BASE_BOT_DEMO.md`。

Dry-run 预览 payload：

```bash
python3 scripts/push_star_office_state.py
```

本地 Star-Office-UI 跑在 `127.0.0.1:19000` 后，显式发送：

```bash
python3 scripts/push_star_office_state.py --send
```

自定义 endpoint：

```bash
python3 scripts/push_star_office_state.py --base-url http://127.0.0.1:19000 --endpoint set_state --send
```

## 强本地 MVP 集成

页面 `/integrations` 已经支持三个本地优先能力：

- OpenClaw status/import/probe：读取 `~/.openclaw/openclaw.json`、cron jobs、cron run JSONL 和 subagent run index。
- Hermes status/probe：检查本机 Hermes gateway 与 `127.0.0.1:8642`，不可用时记录 health failure，不让 MIS 崩溃。
- Notion export：默认 dry-run，真实导出必须显式 `dry_run:false` + `confirm_export:true`。
- 产品化 Notion 路径：除 parent/database 导出外，也支持 `NOTION_WORKSPACE_PRIVATE_EXPORT=true` 尝试 workspace-level private page；该模式取决于 token 类型，internal integration 通常仍需要指定 parent。
- Agnesfallback：默认只返回 dry-run 计划；真实固定 probe 必须同时设置 `HERMES_ALLOW_REAL_RUN=true` 并在请求体里传 `confirm_run:true`。默认不会加 `--yolo`。
- Base/template switching：提供 Notion、W&B、Plane、Docmost、Mattermost 等外部 base 的能力矩阵和迁移 preview；Agent-MIS local base 仍是权威账本。
- Local AI Workflows：`/workflows` 里提供一个真实本地 AI brief 工作流。它读取 MIS 的结构化安全指标，调用本机 Agnesfallback 生成中文项目/运营简报，并写入 Run Ledger、Runtime Events、Evaluation、Audit 和 Artifact。默认仍是 dry-run；真实运行必须启动 server 前显式设置 `HERMES_ALLOW_REAL_RUN=true`，再用页面确认按钮或 API 传 `confirm_run:true`。

OpenClaw 导入使用确定性 ID，重复导入不会重复造 agents/tasks/runs/evaluations/tool_calls。cron run summary 不存原文，只存脱敏前 200 字和 hash/source metadata。

常用 API：

```bash
curl -fsS http://127.0.0.1:8787/api/integrations/openclaw/status | jq .
curl -fsS -X POST http://127.0.0.1:8787/api/integrations/openclaw/import -d '{}' | jq .
curl -fsS -X POST http://127.0.0.1:8787/api/integrations/hermes/probe -d '{}' | jq .
curl -fsS -X POST http://127.0.0.1:8787/api/integrations/hermes/cli-probe -d '{}' | jq .
curl -fsS -X POST http://127.0.0.1:8787/api/workflows/local-brief -d '{}' | jq .
curl -fsS -X POST http://127.0.0.1:8787/api/integrations/notion/dry-run-export -d '{}' | jq .
curl -fsS -X POST http://127.0.0.1:8787/api/migration/preview -d '{}' | jq .
curl -fsS http://127.0.0.1:8787/api/dashboard/metrics | jq .
```

本机录屏或自用时执行一次真实 brief：

```bash
export HERMES_ALLOW_REAL_RUN=true
export HERMES_REQUIRE_CONFIRM_RUN=true
python3 server.py
curl -fsS -X POST http://127.0.0.1:8787/api/workflows/local-brief \
  -H "Content-Type: application/json" \
  -d '{"confirm_run":true}' | jq .
```

录完或用完关闭 live mode：

```bash
unset HERMES_ALLOW_REAL_RUN
```

## V1 OpenClaw 实验脚本

本地 v1 目标已经加入一个可重复运行的 OpenClaw 观测实验：

```bash
python3 scripts/openclaw_v1_experiment.py
```

它会读取安全元数据，跑一次 OpenClaw main agent live probe，并把结果写入 MIS 数据库：

- `agt_openclaw_main`
- `agt_openclaw_subagents`
- `tsk_v1_openclaw_observability`
- `run_v1_openclaw_probe_*`

实验报告会写到：

```text
outputs/V1_OPENCLAW_EXPERIMENT.md
```

隐私边界：脚本只记录模型、耗时、token usage、cron/job 计数、subagent run 计数和本机路径引用；不读取 credentials、消息正文或 session transcript 全文。

## 今日交付与汇报材料

- 多线程/分支协作规则：`docs/OPERATING_RULES.md`
- 10 分钟课堂汇报骨架：`docs/PRESENTATION_BRIEF.md`
- 10 分钟中文口播脚本：`docs/CHINESE_PRESENTATION_SCRIPT.md`
- 前后台 demo checklist：`docs/DEMO_CHECKLIST.md`
- 系统规划、分析与设计：`docs/SYSTEM_PLANNING_ANALYSIS_DESIGN.md`
- 能力路线与 Pro 模型分工：`outputs/CAPABILITY_ROADMAP.md`

Notion 连接已预留：

```bash
export NOTION_TOKEN="..."
export NOTION_PARENT_PAGE_ID="..."
python3 server.py
```

如果使用 Notion public integration / OAuth / personal access token，并希望模拟 GPT-style “直接创建到 workspace private area”，可以尝试：

```bash
export NOTION_TOKEN="..."
export NOTION_WORKSPACE_PRIVATE_EXPORT=true
python3 server.py
```

打开 `/integrations` 可以查看 OpenClaw/Hermes/Notion 状态、汇报预览，并执行 dry-run 或实际导出。

Notion / 开源实验底座 / 本地 Hermes-OpenClaw loop 的收拢顺序见 `docs/NOTION_OPEN_SOURCE_LOOP_CONVERGENCE_PLAN.md`。它把 Notion Project Ledger 定位成 Web GPT / 人类协作可见层，把开源底座定位成本地实验和证据来源，把 AgentOps MIS SQLite/API 保持为运行、审批、交付和审计的权威账本。

Notion Project Memory 的产品边界见 `docs/NOTION_PROJECT_MEMORY_CONNECTOR_LAYER.md`：当前把 Notion Project Ledger 当作 Web GPT / 人类协作的项目记忆层，负责候选增量、已审查决策、风险、Backlog 与 Handoff；AgentOps MIS 仍然负责 Run、Tool Call、Approval、Artifact、Evaluation 与 Audit 的执行账本。对应静态验收为：

```bash
python3 scripts/notion_project_memory_connector_layer_smoke.py
```

## 文件结构

```text
agentops-mis-mvp/
├── server.py                         # 零依赖本地服务 + SQLite API + mock/OpenClaw/Hermes adapters
├── scripts/
│   ├── openclaw_v1_experiment.py      # OpenClaw v1 safe metadata + live probe experiment
│   ├── demo_seed_openclaw_redacted.py  # Synthetic redacted scale data for video demos
│   └── demo_acceptance.py              # Local API acceptance checks
├── static/
│   ├── index.html                     # 单页 UI
│   ├── styles.css
│   └── app.js
├── docs/
│   ├── PRODUCT_UNDERSTANDING.md
│   ├── TECHNICAL_SOLUTION.md
│   ├── COMPETITOR_MATRIX.md
│   ├── ARCHITECTURE.md
│   ├── DATABASE_SCHEMA.md
│   ├── API_SPEC.md
│   ├── PAGE_INFORMATION_ARCHITECTURE.md
│   ├── IMPLEMENTATION_PLAN.md
│   ├── TEST_PLAN.md
│   ├── RISK_REGISTER.md
│   ├── SANDBOX_VS_CODEX.md
│   └── CODEX_NEXTJS_HANDOFF_PROMPT.md
├── sql/
│   └── schema.sql
├── inputs/
│   └── USER_SPEC_PROMPT.md
├── outputs/
│   └── OUTPUT_SUMMARY.md
└── artifacts/
    ├── sample_export_runs.json
    └── sample_export_memories.json
```

## 默认安全策略

- 不含真实 API key。
- 默认不调用外部 API；Notion export 和 Agnesfallback fixed probe 需要显式确认。
- 高风险工具调用默认进入审批。
- 所有关键写操作写入 audit log。
- 预留 `tamper_chain_hash` 字段。
- 禁止隐藏 telemetry；如果未来接入第三方观测系统，必须显式记录并可关闭。
- 不提交 `agentops_mis.db`、credentials、真实 prompts、私聊正文或完整 transcripts。

## 当前迁移方向

商业版不再以 SQLite 为目标。当前迁移按
`docs/COMMERCIAL_MIGRATION_CLEAN_ROOM_BREAKDOWN.md` 推进：

1. Commercial / BYOC 的 API、Human Session、RBAC、审批、配额、审计和证据
   由 Next.js + TypeScript + PostgreSQL 16 直接持有。
2. Hermes / OpenClaw 商业 worker 使用 Node.js/TypeScript，不启动 Python
   worker 或 Python API。
3. Python + SQLite 只保留给 Free Local、离线测试编排、迁移验证和显式兼容
   回滚，不得成为 production/shared 权威。
4. 迁移只有在客户环境安装、升级、备份恢复、回滚、当前提交 CI，以及同一
   冻结提交的真实 Hermes/OpenClaw 闭环全部通过后才算完成。
