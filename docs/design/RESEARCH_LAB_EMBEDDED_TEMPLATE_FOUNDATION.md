# Research Lab Embedded Solution Template — Foundation

> Status: Proposed
> Canonical: false
> Version: 0.1.0
> Date: 2026-07-28

## 1. Product definition

Research Lab 不是外部链接集合，也不是另一套最终用户系统。它是可安装到 AgentOps MIS Workspace 的垂直解决方案模板：

```text
AgentOps MIS Core
+ Research domain objects
+ Research workflows
+ Research navigation/read models
+ Optional external adapters
= Research Lab Workspace
```

安装后仍然复用同一：

- AppShell 与 Workspace context；
- 身份、角色和权限；
- Task、Agent Plan、Run、Approval；
- Artifact、Evaluation、Memory Review、Audit；
- Project governance 与 External Base authority rules。

## 2. Smooth experience contract

模板不能让用户在多个系统间不断迷路。交互按四级降级：

1. **In-app summary**：状态、最新指标、Git revision、Artifact、审批等先在 MIS 内可见。
2. **In-app detail**：实验协议、Trial、Run、指标快照、Checkpoint、评价在 MIS 路由内查看。
3. **Embedded/preview surface**：日志、图表和 Artifact 可在安全只读区域预览。
4. **Native deep action**：只有完整 PR diff、Notebook 编辑、MLflow/W&B 高级分析或服务器终端才进入外部原生工具。

所有外部 deep link 必须保存 `workspace_id / research_project_id / experiment_id / mis_run_id / return_route`，返回后仍定位原对象。

## 3. Template package contract

```text
incubator/research-lab/template/
├── manifest.json
├── schema/research-lab-template.schema.json
├── examples/building-wireframe-lab.instance.json
├── scripts/validate_template_manifest.py
└── tests/test_template_manifest.py
```

v0 使用 JSON，避免给当前零依赖参考实现增加 YAML/Schema 运行依赖。后续 UI 可提供 YAML authoring，再编译为相同的 canonical JSON manifest。

## 4. Authority mapping

| Research object | Core MIS authority mapping |
|---|---|
| `research_project` | `Project / Workspace` |
| `experiment_protocol` | `Agent Plan + Artifact` |
| `trial` | `Task` |
| `job_attempt` | `Run` |
| `dataset_version` | `Artifact + External Base reference` |
| `environment_snapshot` | `Artifact` |
| `metric_snapshot` | `Artifact + Evaluation evidence` |
| `checkpoint` | `Artifact` |
| `model_version` | `Artifact + Approval` |
| `scientific_review` | `Evaluation + Approval` |
| `paper_evidence` | `Knowledge/Artifact reference` |
| `server_profile` | `External Base / Worker capability reference` |

Research objects may add domain metadata, but cannot independently claim task completion, approval, delivery or canonical memory.

## 5. Embedded routes

All primary routes remain within one MIS workspace:

```text
/workspaces/:workspaceId/research
/workspaces/:workspaceId/research/projects
/workspaces/:workspaceId/research/experiments
/workspaces/:workspaceId/research/experiments/:experimentId
/workspaces/:workspaceId/research/datasets
/workspaces/:workspaceId/research/models
/workspaces/:workspaceId/research/servers
/workspaces/:workspaceId/research/papers
```

External URLs are actions on an internal detail page, never the only representation of an object.

## 6. Installation behavior

Installing the template creates:

- Research navigation and dashboard configuration;
- default research roles and agent-role suggestions;
- workflow definitions for experiment lifecycle and model promotion;
- research-domain projections and example filters;
- connector configuration slots without credentials;
- optional demo data only when explicitly selected.

It does not create a second user database or copy Core records.

## 7. Uninstall and upgrade

- Disabling the template removes navigation/projections, not Core authority records.
- Research extension metadata remains exportable and recoverable.
- Template upgrades are versioned and migration-driven.
- No destructive migration runs without Prepared Action + Approval.

## 8. Standalone vs embedded mode

```text
Research Domain Contract
├── Standalone adapter: protocol/validator development and isolated tests
└── MIS adapter: production workspace, authority, policy and UI integration
```

Standalone mode is an implemented developer and validation surface. Embedded MIS mode is a proposed user experience whose default-product status remains subject to review; this foundation does not deprecate the standalone runtime.

## 9. External adapter levels

| Level | Capability |
|---|---|
| L0 | configured/connected state + safe deep link |
| L1 | read-only summary projection |
| L2 | governed import of metrics/artifacts |
| L3 | Prepared Action + Approval for side effects |

The first embedded release may ship L0 plus selected L1 adapters. L2/L3 are not required for the foundation.

## 10. Building Wireframe reference workspace

The included example proves the template can represent the user's current research:

- project: Building Wireframe Reconstruction;
- experiment lines: M1, R1, R2;
- two-stage freeze/unfreeze protocol;
- datasets, code revision and seed tracking;
- metric/artifact/checkpoint placeholders;
- GitHub, MLflow/W&B, Jupyter, SSH and Notion connector slots;
- no credentials or invented experimental results.
