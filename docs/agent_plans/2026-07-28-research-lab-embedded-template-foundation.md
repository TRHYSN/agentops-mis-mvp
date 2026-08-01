# Agent Plan — Research Lab Embedded Template Foundation

> Status: Proposed recovery candidate
> Canonical: false
> Date: 2026-07-28
> Repository: `geogejoy107-jpg/agentops-mis-mvp`
> Target branch: `template/research-lab-embedded-v0-recovery-20260801`
> Verified recovery base: `main@99ce51d693f1d646ea84acc2f7f376bde1a95a9a`
> Historical source branch/base: `template/research-lab-embedded-v0` @ `40db4e22387771cf41e7dfed2490eab283fe0b6f`
> Owner: Project Owner
> Implementer: Codex / implementation agent
> Verifier: independent reviewer

## Goal

建立 Research Lab 的 **Embedded Solution Template 基座**：模板可以在同一个 AgentOps MIS AppShell、身份、权限和权威账本中安装，向工作区增加科研对象、流程、导航、默认角色和外部连接映射；不把 Research Lab 做成另一个独立最终产品，也不把 MIS 退化为外链书签页。

## Approved user intent carried into this plan

1. Research Lab 是 AgentOps MIS 的垂直模板，不是第二套 MIS。
2. 模板可先在隔离目录开发，成熟后通过稳定的 Template Package 接入 MIS。
3. 用户日常操作应留在同一 AppShell；GitHub、MLflow/W&B、Jupyter、SSH 等只在需要原生深度能力时打开。
4. Core MIS 的 `Task → Run → Approval → Artifact → Evaluation → Audit` 不重做；科研对象只扩展并映射到它们。
5. 现有 standalone Research Lab 已进入当前主线；embedded mode 是否成为默认用户体验仍是待评审产品提案，本恢复包不作批准结论。

## First bounded slice

本工作包只交付零依赖、可验证的 Template Contract：

- 版本化 template manifest；
- 核心对象与科研对象映射；
- 嵌入式导航与 smooth-handoff 约束；
- 外部 connector 的 summary/deep-link 能力声明；
- Building Wireframe Lab 示例实例；
- 确定性 validator 与 tests；
- 设计说明和 handoff。

## Explicit non-goals

- 不实现 MLflow、W&B、Jupyter、SSH 或 Slurm 的完整客户端；
- 不实现新的训练调度器；
- 不复制 Core MIS 的用户、权限、Task、Run、Approval、Artifact、Evaluation 或 Audit；
- 不修改 Commercial、Private Host、Relay、Spatial OS；
- 不把模板状态写成 Canonical；
- 不自动保存凭据、token、私密日志或完整原始 prompt/response。

## Acceptance gates

| Gate | Acceptance evidence |
|---|---|
| A | Manifest 可被零依赖 validator 解析并通过 |
| B | Manifest 明确依赖 Core MIS authority objects，而非复制它们 |
| C | Research objects 全部有 Core mapping |
| D | 导航全部位于同一 workspace/AppShell 路由下 |
| E | 每个外部 connector 都先声明 in-app summary，再声明 deep link |
| F | Building Wireframe 实例不含凭据，并绑定真实科研语义 |
| G | 禁用模板不会删除 Core MIS authority records |
| H | standalone 与 embedded mode 的边界明确 |
| I | 正向、负向测试均通过 |
| J | 形成 exact-head handoff，最终 verifier 与 implementer 分离 |

## Stop conditions

停止当前 slice 仅限：

1. A–I 全部关闭，进入独立 review；
2. 发现当前 repo 已有冲突的 Template SDK，需要先做关系校准；
3. 当前 main/target branch 与计划基线漂移，且无法安全重放；
4. 需要用户决定会改变 Core schema 或产品优先级。

## Next slice after review

`MISRepositoryAdapter + embedded read model`：让模板实例复用同一 Workspace、Task、Run、Artifact、Evaluation、Approval 和 Audit，并在当前 Vite AppShell 中增加 Research Workspace 路由。该 slice 不在本提交中冒充完成。
