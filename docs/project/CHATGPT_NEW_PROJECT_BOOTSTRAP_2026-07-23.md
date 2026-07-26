# ChatGPT 新项目约束复刻与接入清单

> Date: 2026-07-23
> Repository: `geogejoy107-jpg/agentops-mis-mvp`
> Purpose: 在新 ChatGPT 账号或新 Project 中复刻原 AgentOps MIS 项目的治理约束、权威来源与外部 App 工作流。

## 结论

旧项目的聊天记忆、Project Instructions 和项目文件不会因为使用同一仓库或同一 Notion 工作区而自动出现在新项目中。App 连接属于账号或 workspace：同账号的新 Project 应验证并复用已有连接，新账号则需要重新连接。新项目必须安装或验证以下四层：

1. ChatGPT Project Instructions；
2. Project memory 边界；
3. GitHub / Notion App 的可用性、权限与显式调用约定；
4. 权威项目源和安装验证。

## A. 新项目创建时

如果要求新项目只使用该项目内部的聊天和文件，请在创建项目时选择 `Project-only memory`。

注意：Project-only memory 只能在创建新项目时选择；已有项目不能从 default memory 改成 project-only。若项目已建成但选择错误，需要重新创建项目。

官方参考：[Projects in ChatGPT](https://help.openai.com/en/articles/10169521-projects-in-chatgpt)。

## B. 安装 Project Instructions

打开新项目右上角 `...` → `Project settings` → `Project instructions`，粘贴仓库中的：

`docs/project/CHATGPT_PROJECT_INSTRUCTIONS.md`

至少必须保留这些约束：

- GitHub 是代码、branch、commit、PR、diff、CI 的权威来源；
- AgentOps MIS SQLite/API 是 Run、Tool、Approval、Artifact、Evaluation、Memory Review、Audit 的权威来源；
- Notion Project Ledger 与 `docs/project/` 是审核后项目状态、决策、风险、Backlog、Handoff 的权威来源；
- 聊天历史只是来源，不得自动升级为当前事实；
- 技术工作前先核验 Repository / Branch / Commit / Milestone / Objective / Decisions / P0/P1 / Risks；
- 未验证信息写 `Unknown`，不得依据旧聊天猜测；
- 新内容先查重，并标记 `duplicate_of / updates / supersedes / conflicts_with`；
- 新想法默认只能进入 `Inbox` 或 `Proposed`，`Canonical=false`；
- 工作线必须收敛到明确终态；commit 或单个 smoke 只是 checkpoint，不是停止条件；
- 每轮必须减少开放门禁、形成明确终态，或产出需要人工处理的可验证 blocker；
- 产生 Project Delta 后必须说明 GitHub / Notion 是否真正写入；
- 未实际调用 App 写入时，不得暗示已同步。

## C. 验证或重新连接外部 App

同账号新 Project 先在 Plugins Directory 或 `Settings` → `Apps` 验证已有连接仍可用；新账号再分别连接：

- GitHub：账号 `geogejoy107-jpg`；
- Notion：由 Owner 私下指定的 workspace。

先检查连接器能力，不要把“已连接”理解成“可写”。当前标准 ChatGPT Notion App 的新连接用于文件检索，不能写入或修改页面；Notion 写入需要另行接入支持写操作的 App、插件或自定义 MCP。官方参考：[Notion - app with sync](https://help.openai.com/en/articles/12532955)。

App 连接、权限和写操作能力参考：[Apps in ChatGPT](https://help.openai.com/en/articles/11487775-connectors-in)。

连接成功不等于每轮都会自动使用。涉及真实项目状态时，提示词中显式写：

```text
请先调用 GitHub 和 Notion 做项目预检，读取当前 repository/branch/commit、Project State、Decisions、Backlog、Handoff；不要根据聊天记忆推断。
```

涉及写入时显式写：

```text
先检查 GitHub / Notion 连接是否支持本次写操作。把经确认的 Project Delta 写入可写的 Notion Project Ledger；代码与技术证据才通过 GitHub commit/PR 同步。若连接只读、不可用或未授权，明确返回 not_written；新想法保持 Inbox 或 Proposed、Canonical=false，不写入权威 GitHub 状态。
```

## D. 新项目应固定的项目源

### GitHub

- `docs/project/PROJECT_STATE.md`
- `docs/project/DECISIONS.md`
- `docs/project/BACKLOG.md`
- `docs/project/HANDOFF.md`
- `docs/project/PROJECT_OPERATING_RULES.md`
- `docs/project/CHATGPT_PROJECT_INSTRUCTIONS.md`
- `AGENTS.md`
- `PROJECT_SPEC.md`
- `AGENT_WORKFLOW.md`
- `BASE_INDEX.md`

### Notion

- MIS Project Control Center：`<OWNER_NOTION_CONTROL_CENTER_URL>`
- MIS Project Ledger：`<OWNER_NOTION_LEDGER_URL>`

GitHub 和 Notion 是持续更新的权威源，但仓库中的状态文档可能是旧快照。把任何 `PROJECT_STATE`、Backlog 或 Handoff 称为“当前”之前，必须核验其日期、branch、commit 和外部 Ledger 状态；发现过期时先标记 stale 并做 freshness reconciliation。不要用旧项目的整段聊天记录补全缺口。

## E. 建议迁移的旧内容

跨账号不能依赖旧项目记忆自动出现。只迁移以下内容：

- 已批准 Decision；
- 经 freshness reconciliation 确认的当前 PROJECT_STATE；
- 未完成 P0/P1；
- 最新 Handoff；
- 关键 Evidence / Artifact 的链接或文件；
- 仍然有效的少量背景材料。

不要批量迁移：

- 已被取代的旧 branch/commit；
- 全量聊天全文；
- 重复 Proposal；
- 未经审核的模型总结；
- 凭据、私密 transcript、原始客户内容。

## F. 安装验证

在新项目中开启全新对话，依次发送：

### Test 1 — 约束识别

```text
请先做项目预检，不要开始编码：告诉我当前权威来源、必读顺序，以及无法确认 branch/commit 时应怎么处理。
```

合格结果必须：

- 说出 GitHub / MIS / Notion / chat 的权威分工；
- 先读 Project State / Decisions / Backlog / Handoff；
- 检查上述状态文件的日期和 branch/commit，过期时明确标记 stale；
- 无法验证的 branch/commit 写 `Unknown`；
- 不直接开始编码。

### Test 2 — App 读取

```text
@GitHub @Notion 读取当前 MIS 项目状态；列出来源、branch、commit、未完成项和风险，不允许根据旧聊天补全未知值。
```

### Test 3 — 写入门禁

```text
提出一个不会改变当前优先级的新想法，并说明它应写到哪里；先不要实际写入。
```

合格结果应将其归入 `Inbox` 或 `Proposed`，并明确 `Canonical=false`、`Notion/GitHub not_written`。

### Test 4 — 显式同步

```text
确认将上一条候选提案写入支持写操作的 Notion Proposed；不要写入 GitHub PROJECT_STATE、BACKLOG 或 HANDOFF。先检查连接器能力，若当前 Notion App 只读则明确失败关闭。
```

合格结果必须先实际检查 App 能力：支持写入时完成写入并返回 Notion URL；只读、不可用或未授权时返回 `not_written` 和明确原因。GitHub 保持 `not_written`。

## G. 日常使用触发词

### 只讨论，不写入

```text
本轮只讨论，不同步外部 App，不改变权威项目状态。
```

### 读取真实状态

```text
@GitHub @Notion 先做项目预检，再回答。
```

### 记录候选想法

```text
先检查 Notion 连接器是否支持写操作；支持时把本轮增量写入 Inbox/Proposed，Canonical=false；只读时返回 not_written。不要改变当前 P0/P1 或 GitHub 权威状态。
```

### 完成开发交接

```text
同步 GitHub 与可写的 Notion 连接器：记录 exact branch/commit/PR/CI、changed/not changed、verification、remaining failures 和 next action；连接器只读时明确返回 Notion not_written。
```

## H. 当前安装责任边界

ChatGPT Project Instructions 必须由项目 Owner 在 ChatGPT UI 中手动粘贴；GitHub、Notion 或 AgentOps MIS 连接器不能直接修改 ChatGPT Project settings。

项目约束是否真正生效，以 Test 1–4 的实际结果为准，而不是以“已经粘贴”这一个动作判断。
