# OpenCekura Windows v0 执行规格（原始归档）

## 来源元数据

- Product: OpenCekura
- Module: Reliability Lab
- Repository: `geogejoy107-jpg/agentops-mis-mvp`
- GitHub Issue: `#123 — OpenCekura Windows v0 — Reliability Lab vertical slice`
- Canonical Notion page: `Spec｜OpenCekura Windows 独立开发与 AgentOps MIS 回并方案｜2026-08-11`
- Notion page ID: `3b96adfd-d920-81cf-9f99-d2990deea005`
- Notion URL: <https://app.notion.com/p/3b96adfdd92081cf9f99d2990deea005?pvs=204>
- Execution date: `2026-08-11`
- Target platform: Windows（同时要求 Ubuntu CI）

> 本文件忠实归档本轮开发请求中的 0–29 节执行规格，用作本地不可歧义的实施基线。

你现在在我的 Windows 电脑上负责开发 OpenCekura / Reliability Lab。

## 0. 项目事实

GitHub 主仓库：

`geogejoy107-jpg/agentops-mis-mvp`

GitHub Issue：

`#123 — OpenCekura Windows v0 — Reliability Lab vertical slice`

Windows 本机目前不保证存在任何 AgentOps MIS 项目目录。

不要寻找或依赖我 Mac 上的工作目录。

GitHub main 是 Windows 端的代码起点。

你需要从 fresh clone 开始，最后形成一个能够通过 PR 回并主项目的完整垂直产品线。

---

## 1. 第一件事：建立 Windows 工作副本

如果本机还没有仓库：

```powershell
git clone https://github.com/geogejoy107-jpg/agentops-mis-mvp.git
cd agentops-mis-mvp
```

随后必须执行：

```powershell
git fetch --all --prune
git checkout main
git pull --ff-only
git branch --show-current
git rev-parse HEAD
git status --short
```

记录：

- Repository
- Branch
- Exact HEAD
- Working Tree 状态

然后创建开发分支：

```powershell
git checkout -b feat/open-cekura-windows-v0
```

不要直接在 main 上开发。

---

## 2. 开始编码前必须读懂现有项目

至少阅读：

```text
README.md
docs/project/*
docs/PUBLIC_CLAIMS_AND_LIMITATIONS.md
```

并搜索当前项目中关于以下对象的实现：

```text
Task
Plan
Run
ToolCall
Approval
Memory
Evaluation
Artifact
Audit
Evidence
Agent Gateway
Workspace UI
SQLite
```

还要检查：

```text
ui/start-building-app/
```

现有 UI 架构。

目标是：

复用现有 AgentOps MIS 权威账本，而不是给 OpenCekura 再造一个第二套 MIS。

---

## 3. 产品定义

产品暂定名称：

OpenCekura

产品模块正式名称：

Reliability Lab

定位：

Open-source reliability testing, simulation, evaluation, regression and release gating for AI agents.

第一阶段重点支持：

- Chat Agent
- Tool-using Agent
- HTTP Agent
- deterministic Mock Agent

实时 Voice / WebRTC / SIP 不属于 v0.1 硬性范围。

先完成：

```text
Scenario
→ Simulation
→ ToolCall observation
→ Evaluation
→ Failure
→ Regression
→ Release Gate
→ Evidence
→ UI
```

完整闭环。

不要做成只有聊天界面的 Demo。

---

## 4. 与现有 MIS 的关系

OpenCekura 是 AgentOps MIS 的一个 vertical product。

映射必须保持：

```text
Campaign
→ MIS Task / Plan
ConversationRun
→ MIS Run
ObservedToolCall
→ MIS ToolCall
EvaluationResult
→ MIS Evaluation
EvidenceManifest
→ MIS Artifact / Evidence
ReleaseGate
→ MIS Quality Gate / Approval
RegressionCase
→ MIS Memory + future Plan input
```

OpenCekura 可以建立自己的垂直业务表。

但不得建立第二套：

```text
Task Ledger
Run Ledger
Approval Ledger
Audit Ledger
```

垂直对象应尽量保存：

```text
mis_task_id
mis_run_id
mis_evaluation_id
mis_artifact_id
```

或者等价的稳定 mapping。

---

## 5. 新目录

建议新增：

```text
open_cekura/
    __init__.py
    domain/
        models.py
        enums.py
        ids.py
    scenarios/
        schema.py
        loader.py
        fixtures/
    simulation/
        runner.py
        personas.py
        agent_adapter.py
        mock_agent.py
        http_agent.py
    evaluation/
        base.py
        rules.py
        llm_judge.py
        aggregation.py
    regression/
        builder.py
        clustering.py
    release_gate/
        gate.py
        policy.py
    evidence/
        bundle.py
        manifest.py
    storage/
        repository.py
        sqlite_repository.py
    api/
        routes.py
        schemas.py
    cli/
        main.py
    windows/
        doctor.py
        paths.py
        process.py
    tests/
        unit/
        integration/
        fixtures/
```

UI：

```text
ui/start-building-app/src/features/open-cekura/
    pages/
    components/
    api/
    types/
```

文档：

```text
docs/open-cekura/
    PRODUCT_SPEC.md
    ARCHITECTURE.md
    WINDOWS_DEV_RUNBOOK.md
    EVALUATION_CONTRACT.md
    EVIDENCE_CONTRACT.md
    RELEASE_GATE_CONTRACT.md
    HANDOFF.md
```

Demo：

```text
examples/open-cekura/
    appointment-agent/
    scenarios/
```

---

## 6. 核心 Domain Objects

至少实现：

```text
AgentUnderTest
AgentVersion
ScenarioSuite
Scenario
Persona
Campaign
ConversationRun
ConversationTurn
ObservedToolCall
EvaluationResult
FailureCase
FailureCluster
RegressionCase
ReleaseGateDecision
EvidenceManifest
```

所有对象：

- stable ID
- schema_version
- created_at
- relevant parent ID
- serialization contract

不能随意用巨大 JSON blob 代替所有 domain model。

---

## 7. Scenario v1

使用 YAML。

示例：

```yaml
schema_version: 1
id: appointment.change_after_interrupt
name: Change appointment after interruption
persona:
  language: en-US
  tone: impatient
  verbosity: short
initial_message: "I need to move my appointment."
goal:
  type: reschedule
challenges:
  - interrupt_after_turn: 2
  - change_constraint_after_turn: 3
expectations:
  required_tool_calls:
    - lookup_booking
    - update_booking
  forbidden_tool_calls:
    - create_duplicate_booking
  must_confirm_before_mutation: true
  final_state:
    booking_updated: true
```

使用 Pydantic 或同等级 schema validator。

关键字段：

- 非法值 fail-fast；
- schema version 不兼容 fail；
- 不得静默吞掉 contract 错误。

---

## 8. Simulation Engine

设计 adapter：

```python
class AgentAdapter:
    async def start(...)
    async def send(...)
    async def observe_tool_calls(...)
    async def close(...)
```

v0 必须支持：

```text
MockAgentAdapter
HTTPAgentAdapter
```

以后再扩：

```text
LiveKit
Pipecat
Vapi
Retell
SIP
```

这些不是当前 v0 blocker。

---

## 9. deterministic evaluators

v0 必须实现：

```text
task_success
required_tool_calls
forbidden_tool_calls
duplicate_mutation
confirmation_before_mutation
final_state_match
turn_count_limit
timeout
```

统一结果格式：

```json
{
  "evaluator_id": "tool_call_correctness.v1",
  "status": "pass",
  "score": 1.0,
  "threshold": 0.95,
  "reason_codes": [],
  "evidence_refs": [],
  "metadata": {}
}
```

不能只保存一个：

```text
score = 83
```

必须能解释：

- 为什么 fail；
- 哪个 turn；
- 哪个 tool；
- 哪条 expectation；
- 哪份 evidence。

---

## 10. LLM Judge

可以实现：

```text
LLMJudgeAdapter
```

但注意：

CI 不能依赖它。

没有 API Key：

```text
status = SKIPPED
```

不能：

- fake score；
- fallback 到神秘模型；
- 把 evaluator error 当 PASS。

Evidence 必须记录：

```text
model
provider
prompt version
judge version
temperature
```

---

## 11. Evidence Bundle

每个 Run：

```text
artifacts/
  open-cekura/
    <campaign_id>/
      <run_id>/
        scenario.yaml
        agent_version.json
        transcript.json
        tool_calls.json
        timing.json
        evaluations.json
        evidence_manifest.json
```

Campaign：

```text
campaign_summary.json
baseline_candidate_diff.json
release_gate.json
regression_cases.json
```

EvidenceManifest 至少：

```text
schema version
campaign_id
run_id
git commit SHA
OS
Python version
Node version
scenario SHA256
agent config SHA256
evaluator versions
artifact SHA256
started_at
finished_at
final state
```

必须实现：

```powershell
python -m open_cekura.cli.main evidence verify --campaign <id>
```

篡改 artifact 后 verify 必须 FAIL。

---

## 12. Release Gate

首版：

BLOCK

当：

```text
forbidden tool call > 0
duplicate mutation > 0
confirmation-before-mutation violation > 0
task success regression > 5 percentage points
deterministic evaluator error rate > 0
```

WARN：

```text
median turns regression > 20%
timeout rate increases but remains <= 2%
```

Release Gate 输出不能只是：

```text
FAIL
```

必须：

```text
FAIL
Blockers:
- scenario x: duplicate mutation
- scenario y: mutation before confirmation
- task success: 94% → 84%
```

---

## 13. Regression Loop

必须实现：

```text
FailureCase
→ review/normalize
→ RegressionCase
→ ScenarioSuite
→ next Campaign
```

至少自动生成：

```text
original failing input
expected state
observed state
failure reason
source run
evaluator
```

并实现 replay。

---

## 14. 第一个公开 Demo

固定：

AI Appointment Agent Reliability Test

Mock backend：

```text
lookup_booking
list_available_slots
update_booking
cancel_booking
```

至少十类 Scenario：

1. basic success
2. interruption
3. change date mid-flow
4. ambiguous identity
5. unavailable slot
6. duplicate request
7. mutation before confirmation
8. backend timeout
9. tool succeeded but Agent claims failure
10. Agent claims success but state was not mutated

准备两个版本：

```text
Baseline
Candidate
```

Baseline 故意带 2–3 个可靠性缺陷。

Candidate 修复。

最终必须证明：

```text
Baseline → BLOCKED
Candidate → PASS
```

不是靠硬编码 campaign ID。

而是 evaluator 和 gate 自然推导。

---

## 15. Windows 是本轮硬要求

所有 OpenCekura 新代码：

必须 Windows-safe。

统一使用：

```python
pathlib.Path
```

禁止：

```text
/tmp
~/.local
bash
sh
grep
sed
awk
lsof
ps
chmod 0600 assumptions
systemd
LaunchAgent
```

作为核心流程依赖。

subprocess：

```python
subprocess.run([
    executable,
    arg1,
    arg2
])
```

不要拼 shell string。

端口检测：

使用 Python socket。

临时目录：

使用：

```python
tempfile
```

原子文件写：

```text
same-directory temp
→ fsync where appropriate
→ os.replace
```

---

## 16. Windows Doctor

必须新增：

```powershell
python -m open_cekura.windows.doctor
```

至少检查：

```text
Python
Node
npm
Git
repo root
branch
commit
dirty state
write access
SQLite
localhost bind
UI dependencies
optional external API keys
```

API key：

只显示：

```text
OPENAI_API_KEY: PRESENT
```

或者：

```text
MISSING
```

永远不得显示 value。

---

## 17. CLI

至少：

```powershell
python -m open_cekura.cli.main doctor
python -m open_cekura.cli.main scenario validate `
  examples/open-cekura/scenarios/basic.yaml
python -m open_cekura.cli.main campaign run `
  --suite examples/open-cekura/scenarios `
  --agent mock
python -m open_cekura.cli.main campaign compare `
  --baseline <id> `
  --candidate <id>
python -m open_cekura.cli.main gate evaluate `
  --campaign <id>
python -m open_cekura.cli.main evidence verify `
  --campaign <id>
```

---

## 18. API

使用现有 MIS API 体系。

建议：

```text
/mis-api/reliability/agents
/mis-api/reliability/scenario-suites
/mis-api/reliability/campaigns
/mis-api/reliability/runs
/mis-api/reliability/failures
/mis-api/reliability/regressions
/mis-api/reliability/release-gates
```

不要新增第二套：

```text
login
user
owner
session
RBAC
```

除非现有接口无法复用，并且有明确架构证据。

---

## 19. UI

复用：

```text
ui/start-building-app
```

不要创建：

```text
open-cekura-ui/
```

这种第二个前端项目。

侧栏新增：

Reliability Lab

页面：

```text
Overview
Agents
Scenario Suites
Campaigns
Run Detail
Failures
Regression Suite
Release Gates
```

Run Detail：

```text
Top:
PASS/FAIL
turn count
latency
tool calls
evaluators
Left:
conversation timeline
Center:
user / agent transcript
Right:
tool call
evaluation
evidence
```

UI 第一阶段优先完整读 Evidence。

Scenario visual editor 后面再做。

---

## 20. Windows CI

新增真实 GitHub Actions：

```yaml
strategy:
  matrix:
    os:
      - ubuntu-latest
      - windows-latest
    python:
      - "3.10"
      - "3.11"
```

Windows job 至少：

```text
install Python deps
unit tests
integration tests
scenario validate
deterministic campaign
baseline/candidate comparison
gate evaluation
evidence verify
UI install/build
```

CI：

不能要求 secrets 才能通过。

---

## 21. Tests

Unit：

```text
scenario schema
domain serialization
deterministic evaluators
release gate
evidence hashes
Windows path utilities
Windows process utilities
```

Integration：

```text
Scenario
→ Run
→ Evaluation
→ Evidence
Baseline
→ Candidate
→ Compare
→ Gate
Failure
→ Regression
→ Replay
API
→ SQLite
```

E2E：

Windows：

```text
backend start
UI start/build
Reliability Lab loads
campaign runs
result visible
```

---

## 22. 开发方式

不要一次性改 100 个文件。

建议顺序：

1. docs + contracts
2. domain + scenario
3. simulator
4. evaluator
5. evidence
6. regression
7. release gate
8. API
9. UI
10. Windows CI
11. full acceptance
12. handoff

建议 commits：

```text
docs: add OpenCekura product and Windows contracts
feat: add reliability domain and scenario schema
feat: add deterministic conversation simulator
feat: add reliability evaluators and evidence bundle
feat: add regression and release gate
feat: add reliability API
feat(ui): add Reliability Lab views
test: add Windows reliability acceptance
docs: close OpenCekura Windows v0 handoff
```

---

## 23. 每完成一个阶段都必须执行

不要只写代码。

执行实际：

```text
test
run
inspect
compare
fix
rerun
```

失败时：

```text
identify root cause
make smallest correct fix
rerun affected tests
rerun full acceptance where needed
```

然后再进入下一阶段。

---

## 24. 使用好 AgentOps MIS 自己

如果当前仓库和环境中已经能使用 MIS CLI / Gateway：

本轮开发本身应尽量使用：

```text
Task
Plan
Run
ToolCall
Evaluation
Artifact
Memory
Audit
```

形成开发证据。

也就是说：

用 AgentOps MIS 开发 AgentOps MIS 的 OpenCekura 模块。

不要为了使用而阻塞开发。

如果本机首次 Windows clone 尚不能运行相关路径：

记录 blocked reason，

继续完成能够独立完成的部分，

再补 governed-run evidence。

---

## 25. 多 Agent / 子任务

如果环境支持多个 Codex worker / 子代理：

总控负责：

```text
architecture
contract
integration
acceptance
final review
```

可以并行拆：

```text
Worker A
Domain + Scenario + Simulator
Worker B
Evaluator + Evidence + Release Gate
Worker C
API + SQLite integration
Worker D
Vite Reliability Lab UI
Worker E
Windows compatibility + CI + tests
```

但是：

不能让多个 worker 同时随意改核心 schema。

核心 contract 由总控冻结。

每个 worker 完成后必须：

```text
review diff
run tests
integrate
```

不是看到子代理说“完成”就相信。

---

## 26. 禁止事项

禁止：

```text
复制 Cekura 私有代码
逆向私人 API
照抄品牌 UI
把 OpenCekura 说成官方 Cekura 开源版
把 LLM Judge 当唯一 ground truth
把模拟测试说成 production reliability
为了 Windows 支持重写整个 Private Host
修改 systemd / LaunchAgent 线来凑 Windows 支持
把 TODO 当交付
只跑 happy path
只做静态 UI
hardcode Baseline FAIL / Candidate PASS
```

---

## 27. Definition of Done

以下项目必须全部检查。

- [ ] Windows fresh clone 可以工作
- [ ] Windows doctor critical checks PASS
- [ ] Scenario contract 完成
- [ ] deterministic simulator 完成
- [ ] appointment 10+ scenarios 完成
- [ ] baseline/candidate 可以 replay
- [ ] deterministic evaluator 完成
- [ ] Evidence Bundle 完成
- [ ] artifact hashes 可验证
- [ ] RegressionCase 自动产生
- [ ] RegressionCase 可 replay
- [ ] Release Gate 真正 BLOCK baseline
- [ ] Candidate 能通过 gate fixture
- [ ] API 完成
- [ ] Reliability Lab UI 完整展示 evidence chain
- [ ] Windows tests 全绿
- [ ] Ubuntu tests 全绿
- [ ] Windows GitHub Actions 全绿
- [ ] UI build 全绿
- [ ] docs/open-cekura contracts 完成
- [ ] Windows runbook 完成
- [ ] HANDOFF 完成
- [ ] HANDOFF 包含 exact branch
- [ ] HANDOFF 包含 exact commit
- [ ] HANDOFF 包含 exact CI run
- [ ] PR 已创建到 main

这不是：

```text
prototype done
demo done
MVP-ish
core works
```

才停止。

目标是：

Windows-supported OpenCekura v0 vertical slice fully closed.

---

## 28. 最终 PR

完成后：

```text
feat: add Windows-supported OpenCekura Reliability Lab v0
```

PR body 必须包括：

```text
What changed
Architecture
MIS integration
Windows support
Scenario coverage
Evaluation design
Evidence design
Regression loop
Release Gate
UI
Tests
Windows CI
Known limitations
Exact acceptance commands
Evidence
Remaining v0.2 scope
```

不要自行 merge。

由 Owner 最终 review。

---

## 29. v0.2 才进入 Voice

当 v0 完成后，再开始：

```text
Pipecat adapter
LiveKit adapter
audio file pipeline
ASR/TTS observation
interruption metrics
dead air
turn latency
audio clipping
SIP / telephone adapter
real-world voice campaign
```

不要提前把主线拖进 WebRTC/SIP 泥潭。

---

## 执行指令

现在开始执行。

第一步不是写代码。

第一步：

```text
fresh clone / repository preflight
→ exact HEAD
→ inspect existing architecture
→ produce implementation plan
→ immediately begin Phase 1
```

不要停在计划阶段。

持续实现、运行、验证、修复，直到 Definition of Done 收口。

把这个 spec 保存到本地，然后开目标模式执行，并使用 GitHub 与 Notion 集成。
