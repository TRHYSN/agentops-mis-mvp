# AgentOps MIS 架构图说明（Evidence: 2026-07-29）

## 一句话结论

AgentOps MIS 不是许多互不相干的系统，而是：

**共享治理语义 / Agent Gateway 合同**
→ **Free Local Python/SQLite 参考实现**
→ **Private Host 部署、安全与远程操控扩展**
→ **Commercial/BYOC 的 Next.js/TypeScript/PostgreSQL clean-room 生产实现**
＋ **Research Lab 等垂直领域模块**
＋ **UI、Runtime、Relay 等横切模块**。

## 商业线的准确关系

商业线保留 Task、Plan、Run、ToolCall、Approval、Artifact、Evaluation、Memory、Audit 等领域语义及 Agent Gateway 合同，但替换生产 owner：

- Python control plane → Next.js 16 / TypeScript
- Python worker → Node.js / TypeScript Worker
- SQLite authority → PostgreSQL 16 authority
- 本地 loopback → Commercial/BYOC shared deployment boundary

前端并非完全重写：

- `ui/start-building-app` 的 Vite React Workspace / Pixel Office 可以用 production mode 复用；
- 商业 build 将 transport 切到 `/api/mis`；
- `ui/next-app` 另外提供 Next.js app shell、API/control-plane 与 worker toolchain。

## Research Lab 的边界

Research Lab 是独立可安装的科研协议/provenance 模块，拥有自己的 SQLite WAL、ExperimentProtocol、Trial、JobAttempt、Provenance 与 Scientific Claim Gate。它通过 redacted event export 与 MIS 集成，但不覆盖 MIS 的 workspace、approval、delivery、reviewed-memory 与 audit 权威。

## Git 分支判断

- `main`：当前代码事实。
- `codex/commercial-control-plane-main-integration`：商业 clean-room Draft PR #110。
- `codex/commercial-migration-closed-loop`：旧商业历史证据线，不应整条 merge/rebase。
- `design/gemini-ui-v2-implementation`：前端展示线，不修改后端与数据库语义。
- `template/research-lab-embedded-v0`：Research Lab 模板嵌入施工线。
- `codex/private-host-codex-bridge-integration`、`codex/codex-mis-product-bridge`：核心结果已由 PR #112 合入 main，分支仍存在不代表独立产品。
