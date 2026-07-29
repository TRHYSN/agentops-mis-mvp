# 数据库 Schema

核心建模原则：Agent、Task、Run、ToolCall、Approval、Memory、Evaluation、Audit 都是一等对象。Run Ledger 不只记录 prompt/response，而要记录任务、agent、模型、成本、错误、委托和审批。

```sql
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    role TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agents (
    agent_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    role TEXT NOT NULL,
    description TEXT,
    runtime_type TEXT NOT NULL CHECK(runtime_type IN ('mock','claude_code','codex','openhands','crewai','langgraph','openclaw','hermes')),
    model_provider TEXT,
    model_name TEXT,
    status TEXT NOT NULL CHECK(status IN ('idle','running','paused','error','disabled')),
    permission_level TEXT NOT NULL,
    allowed_tools TEXT NOT NULL,
    budget_limit_usd REAL NOT NULL DEFAULT 0,
    owner_user_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(owner_user_id) REFERENCES users(user_id)
);

CREATE TABLE IF NOT EXISTS workspace_agent_memberships (
    workspace_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'human-created',
    created_at TEXT NOT NULL,
    PRIMARY KEY(workspace_id, agent_id),
    FOREIGN KEY(agent_id) REFERENCES agents(agent_id)
);

CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL DEFAULT 'local-demo',
    title TEXT NOT NULL,
    description TEXT,
    requester_id TEXT,
    owner_agent_id TEXT,
    collaborator_agent_ids TEXT DEFAULT '[]',
    status TEXT NOT NULL CHECK(status IN ('backlog','planned','running','waiting_approval','blocked','completed','failed','canceled')),
    priority TEXT NOT NULL DEFAULT 'medium',
    due_date TEXT,
    acceptance_criteria TEXT,
    risk_level TEXT NOT NULL CHECK(risk_level IN ('low','medium','high','critical')),
    budget_limit_usd REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(requester_id) REFERENCES users(user_id),
    FOREIGN KEY(owner_agent_id) REFERENCES agents(agent_id)
);

CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL DEFAULT 'local-demo',
    task_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    runtime_type TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    duration_ms INTEGER,
    input_summary TEXT,
    output_summary TEXT,
    model_provider TEXT,
    model_name TEXT,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    reasoning_tokens INTEGER DEFAULT 0,
    cost_usd REAL DEFAULT 0,
    error_type TEXT,
    error_message TEXT,
    trace_id TEXT,
    parent_run_id TEXT,
    delegation_id TEXT,
    approval_required INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    FOREIGN KEY(task_id) REFERENCES tasks(task_id),
    FOREIGN KEY(agent_id) REFERENCES agents(agent_id),
    FOREIGN KEY(parent_run_id) REFERENCES runs(run_id)
);

CREATE TABLE IF NOT EXISTS tool_calls (
    tool_call_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    tool_version TEXT NOT NULL DEFAULT 'v1',
    tool_category TEXT NOT NULL CHECK(tool_category IN ('browser','github','file','shell','email','notion','discord','database','mcp','custom')),
    normalized_args_json TEXT NOT NULL DEFAULT '{}',
    target_resource TEXT,
    risk_level TEXT NOT NULL CHECK(risk_level IN ('low','medium','high','critical')),
    status TEXT NOT NULL,
    result_summary TEXT,
    side_effect_id TEXT,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(run_id) REFERENCES runs(run_id),
    FOREIGN KEY(agent_id) REFERENCES agents(agent_id)
);

CREATE TABLE IF NOT EXISTS approvals (
    approval_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    tool_call_id TEXT,
    requested_by_agent_id TEXT,
    approver_user_id TEXT,
    decision TEXT NOT NULL CHECK(decision IN ('pending','approved','rejected','expired')),
    reason TEXT,
    subject_type TEXT,
    subject_id TEXT,
    subject_hash TEXT,
    expires_at TEXT,
    created_at TEXT NOT NULL,
    decided_at TEXT,
    FOREIGN KEY(task_id) REFERENCES tasks(task_id),
    FOREIGN KEY(run_id) REFERENCES runs(run_id),
    FOREIGN KEY(tool_call_id) REFERENCES tool_calls(tool_call_id),
    FOREIGN KEY(requested_by_agent_id) REFERENCES agents(agent_id),
    FOREIGN KEY(approver_user_id) REFERENCES users(user_id)
);

CREATE TABLE IF NOT EXISTS prepared_actions (
    action_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL DEFAULT 'local-demo',
    task_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    tool_call_id TEXT,
    approval_id TEXT NOT NULL,
    requested_by_agent_id TEXT NOT NULL,
    action_type TEXT NOT NULL,
    normalized_args_json TEXT NOT NULL DEFAULT '{}',
    target_resource TEXT,
    risk_level TEXT NOT NULL CHECK(risk_level IN ('low','medium','high','critical')),
    policy_version TEXT NOT NULL DEFAULT 'approval-wall-v1',
    checkpoint_json TEXT NOT NULL DEFAULT '{}',
    action_hash TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('prepared','approved','rejected','consumed','expired')),
    provider_side_effect_id TEXT,
    result_summary TEXT,
    created_at TEXT NOT NULL,
    approved_at TEXT,
    consumed_at TEXT,
    expires_at TEXT,
    FOREIGN KEY(task_id) REFERENCES tasks(task_id),
    FOREIGN KEY(run_id) REFERENCES runs(run_id),
    FOREIGN KEY(tool_call_id) REFERENCES tool_calls(tool_call_id),
    FOREIGN KEY(approval_id) REFERENCES approvals(approval_id),
    FOREIGN KEY(requested_by_agent_id) REFERENCES agents(agent_id)
);

CREATE TABLE IF NOT EXISTS memories (
    memory_id TEXT PRIMARY KEY,
    scope TEXT NOT NULL CHECK(scope IN ('task','project','org')),
    memory_type TEXT NOT NULL CHECK(memory_type IN ('policy','sop','decision','commitment','risk','failure_case','project_context','customer_preference','agent_lesson','artifact_summary','loop_record')),
    canonical_text TEXT NOT NULL,
    source_type TEXT NOT NULL CHECK(source_type IN ('chat','email','meeting','github','notion','run_log','manual')),
    source_ref TEXT,
    project_id TEXT,
    task_id TEXT,
    agent_id TEXT,
    confidence REAL NOT NULL DEFAULT 0.5,
    review_status TEXT NOT NULL CHECK(review_status IN ('candidate','approved','rejected','stale','superseded')),
    owner_user_id TEXT,
    ttl_review_due_at TEXT,
    supersedes_memory_id TEXT,
    access_tags TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(task_id) REFERENCES tasks(task_id),
    FOREIGN KEY(agent_id) REFERENCES agents(agent_id),
    FOREIGN KEY(owner_user_id) REFERENCES users(user_id),
    FOREIGN KEY(supersedes_memory_id) REFERENCES memories(memory_id)
);

CREATE TABLE IF NOT EXISTS evaluations (
    evaluation_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    evaluator_type TEXT NOT NULL CHECK(evaluator_type IN ('human','rule','llm_mock')),
    score REAL NOT NULL,
    pass_fail TEXT NOT NULL CHECK(pass_fail IN ('pass','fail')),
    rubric_json TEXT NOT NULL DEFAULT '{}',
    notes TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(task_id) REFERENCES tasks(task_id),
    FOREIGN KEY(run_id) REFERENCES runs(run_id),
    FOREIGN KEY(agent_id) REFERENCES agents(agent_id)
);

CREATE TABLE IF NOT EXISTS evaluation_case_candidates (
    case_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL DEFAULT 'local-demo',
    source_type TEXT NOT NULL CHECK(source_type IN ('evaluation','customer_delivery','run','artifact','manual','commander_synthesis')),
    source_ref TEXT,
    task_id TEXT,
    run_id TEXT,
    artifact_id TEXT,
    evaluation_id TEXT,
    agent_id TEXT,
    case_type TEXT NOT NULL CHECK(case_type IN ('regression','golden','safety','quality','cost','tool_use','memory')),
    title TEXT NOT NULL,
    input_summary TEXT,
    expected_output_summary TEXT,
    rubric_json TEXT NOT NULL DEFAULT '{}',
    failure_mode TEXT,
    confidence REAL NOT NULL DEFAULT 0.5,
    review_status TEXT NOT NULL CHECK(review_status IN ('candidate','approved','rejected','stale','superseded')),
    created_by_agent_id TEXT,
    owner_user_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evaluation_case_runs (
    case_run_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL DEFAULT 'local-demo',
    run_id TEXT NOT NULL,
    evaluation_id TEXT NOT NULL,
    artifact_id TEXT,
    runner_type TEXT NOT NULL CHECK(runner_type IN ('rule','llm_mock')),
    status TEXT NOT NULL CHECK(status IN ('preview','completed','skipped')),
    score REAL NOT NULL DEFAULT 0,
    pass_fail TEXT NOT NULL CHECK(pass_fail IN ('pass','fail')),
    checks_json TEXT NOT NULL DEFAULT '{}',
    created_by_agent_id TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id TEXT PRIMARY KEY,
    task_id TEXT,
    run_id TEXT,
    artifact_type TEXT NOT NULL,
    title TEXT NOT NULL,
    uri TEXT,
    summary TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(task_id) REFERENCES tasks(task_id),
    FOREIGN KEY(run_id) REFERENCES runs(run_id)
);

CREATE TABLE IF NOT EXISTS audit_logs (
    audit_id TEXT PRIMARY KEY,
    actor_type TEXT NOT NULL CHECK(actor_type IN ('user','agent','system')),
    actor_id TEXT,
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    before_hash TEXT,
    after_hash TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    tamper_chain_hash TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_owner ON tasks(owner_agent_id);
CREATE INDEX IF NOT EXISTS idx_runs_task ON runs(task_id);
CREATE INDEX IF NOT EXISTS idx_runs_agent ON runs(agent_id);
CREATE INDEX IF NOT EXISTS idx_runs_created ON runs(created_at);
CREATE INDEX IF NOT EXISTS idx_tool_calls_run ON tool_calls(run_id);
CREATE INDEX IF NOT EXISTS idx_tool_calls_risk ON tool_calls(risk_level);
CREATE INDEX IF NOT EXISTS idx_approvals_decision ON approvals(decision);
CREATE INDEX IF NOT EXISTS idx_memories_status ON memories(review_status);
CREATE INDEX IF NOT EXISTS idx_memories_scope ON memories(scope);
CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_logs(entity_type, entity_id);
```

Private Host 的 Human Session 不把全局 Agent 表直接当 workspace 权威。Human 创建的 Agent 由 `workspace_agent_memberships` 显式绑定；任务 owner/collaborator、Run、Agent Plan 和 Agent Gateway enrollment/Session 仍可提供派生投影。Plan-only 旧 Agent 必须在 Plan 所属 workspace 可见，同时不得泄漏到 `local-demo`。Audit 查询必须按 `(entity_type, entity_id)` 成对验证对象归属，不能只按 `entity_id` 做跨表并集。

升级旧库时，迁移 `2026-07-22-workspace-agent-membership-authority` 只为没有任何显式 membership、且没有非 `local-demo` Task/Run/Agent Plan/Token/Session/Enrollment 权威证据的旧 Agent 补一条 `source='legacy-local-backfill'` 的本地 membership；迁移记录和主键共同保证幂等。Human 创建 Agent 时，Agent、membership 和 Audit 必须在同一 SQLite 事务中成功或整体回滚。

`approvals.approval_id` 不能作为可复用的显示编号。Agent Plan Approval 还必须显式绑定 `subject_type='agent_plan'`、`subject_id=plan_id` 和 `subject_hash=plan_hash`；仅有相同 Task/Run/Agent 的普通 Approval 不能被复用。Agent Plan 和 Prepared Action 创建/决策必须比较不可变权威绑定；ID 已被另一对象占用时返回 `409 approval_id_conflict`，且不得留下 Plan、Approval、Tool Call、Prepared Action 或 Run/Task 状态更新的半截写入。Private Host Human Session 的真实 Account ID进入 Agent Plan 与 Audit，旧 `approver_user_id -> users` 字段保留兼容映射。

## v1.2.1 扩展表

为了支持 runtime connector、Notion External Base、Template + Base Switching 和 demo acceptance，`server.py` 还会初始化以下扩展表：

| Table | 用途 |
|---|---|
| `runtime_connectors` | Hermes、Agnesfallback 等本地 runtime 的 endpoint/profile/binary/status。 |
| `runtime_events` | health check、models discovery、dry-run probe、fixed probe 等 runtime 事件。 |
| `bases` | Agent-MIS local base 与 Notion/W&B/Plane/Docmost/Mattermost external base。 |
| `base_capabilities` | 每个 base 支持 task/comment/artifact/metrics/webhook/OAuth/writeback/audit 等能力。 |
| `connectors` | Notion 等外部 base 的 connector 状态、安全默认和写回开关。 |
| `connector_scopes` | connector 需要/已授予的权限范围。 |
| `external_object_links` | 本地对象与外部对象的映射关系，用于未来 reconciliation。 |
| `sync_jobs` | 外部同步 job 摘要。 |
| `sync_events` | dry-run/export/import-preview 等同步事件，存 hash 和状态。 |
| `field_mappings` | 内部字段到外部字段的映射。 |
| `template_packages` | 用例模板：agent roles、task schema、memory schema、quality gates、approval policy。 |
| `template_bindings` | 模板与 base/workspace 的绑定。 |
| `migration_runs` | base/template switching preview 和未来迁移结果。 |
| `agent_plans` | Agent 执行前的 READ/PLAN/RETRIEVE/COMPARE 计划账本。 |
| `plan_evidence_manifests` | Agent 执行后的计划证据绑定：plan、run、tool call、evaluation、artifact、audit 证据与验证状态。 |
| `knowledge_documents` | Markdown 知识文件的索引元数据、hash、分类和摘要。 |
| `knowledge_chunks` | Markdown 知识文件按 heading 切分后的章节级检索元数据、hash、标题路径和短摘要。 |
| `knowledge_fts` | SQLite FTS5 虚拟表，用于兼容旧的整篇文档检索和手动插入的私有知识行。 |
| `knowledge_chunk_fts` | SQLite FTS5 虚拟表，用于优先返回 heading-aware chunk 检索结果。 |

隐私边界保持不变：这些扩展表记录结构化状态、短摘要、hash、连接器状态和迁移预览，不存 credentials、私聊正文、完整 transcript 或真实 prompt 原文。
