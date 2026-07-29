# AgentOps MIS 私有 Host 与远程操控台运行手册

状态：目标运行手册，命令是否可用以当前实现验收为准
适用范围：一台受信任电脑运行 AgentOps MIS 与 AI Runtime，另一台电脑只用浏览器操控

2026-07-17 产品路径修正：普通用户默认通过 AgentOps Relay 与一次性配对使用
远程 Console，不安装或理解 Tailscale。本文第 5、6 节现有 Tailscale 步骤
保留为高级私网模式，不再代表默认 onboarding，也不能关闭浏览器零安装验收。
默认 Relay 尚未部署完成前，只能称为目标流程，不能用 Tailscale 证据代替。

## 1. 使用边界

本模式把两台电脑分成两种角色：

- **Host 主机**：保存 SQLite 权威账本、知识库、项目文件和批准后的产物，并运行 AgentOps MIS、Hermes、OpenClaw 与 Worker。
- **Console 操控端**：只打开受认证的浏览器工作台，用于创建任务、监督运行、审批、评估、记忆审核、审计查看和批准产物下载。

操控端不需要 Git、Python、Node、仓库副本、Hermes 或 OpenClaw。模型密钥、Agent 凭据、数据库、知识库原文、raw prompt/response 和运行日志不应传到操控端，也不得进入 Git 仓库。

默认模式由 Host 主动建立经过认证的 Relay 隧道，第二台电脑访问稳定的每 Host
HTTPS 地址；Relay 只做 L4/SNI 透传，应用 TLS 在 Host 终止。不得自动开放
路由器端口、直接绑定公网或把 Relay 误称为 MIS 权威 SaaS。

> 下文 `agentops host ...` 是产品目标命令。只有对应实现、smoke、第二电脑验收和 Release 证据通过后，才可视为已发布能力。若当前安装返回 unknown command，应回到仓库当前验收文档，不要用手工公网暴露绕过缺失功能。

## 2. 上线前检查

Host 主机应满足：

- 使用受支持的 AgentOps MIS 版本化安装资产或明确标注的开发预览；
- 如使用高级私网模式，Tailscale 已由用户手动安装并登录；默认 Relay 模式不要求它；
- 本地磁盘有足够空间保存账本、知识索引和批准产物；
- Hermes/OpenClaw 如需真实运行，已由主机管理员单独安装并通过本地健康检查；
- 没有把 token、`.env`、SQLite DB、raw prompt/response、私聊或完整 transcript 放进仓库；
- 确认本轮不会自动安装或启用 Hermes/OpenClaw，Runtime 自动安装留待后续版本。

Console 电脑只需要现代浏览器、稳定的每 Host HTTPS Console URL 和一次性配对
邀请。不得要求 Tailscale/VPN、AgentOps MIS、Git、Python、Node、Hermes 或
OpenClaw。身份或关键审批仍需用户本人确认，不能把密码交给 Codex。

## 3. 安装版本化 Host 预览

先查看当前安装版本：

```bash
agentops host version
```

从项目 Release 页面选择明确的版本化资产：

```text
https://github.com/geogejoy107-jpg/agentops-mis-mvp/releases
```

下载前确认目标 Release 已实际发布，并同时下载同一版本的 `.zip` 或
`.tar.gz` 与 `.sha256.json`。发布附件中的 checksum manifest 和安装后
`agentops host version` 是版本与提交 provenance 的权威来源；运行手册不
硬编码“当前版本”，避免安装包内说明随下一次发布立即过期。

推荐从同一 Release 下载 `install-agentops-mis-private-host.sh`，然后固定到
明确 tag 执行。它会检查 macOS 与 Python 3.10+，通过 GitHub HTTPS 下载同
版本 tar/checksum，程序化比较 SHA-256，拒绝不安全 archive member，调用
bundle installer，并核对安装版本：

```bash
sh install-agentops-mis-private-host.sh \
  --tag v1.6.0-private-host-preview.<N> \
  --init \
  --start
```

该入口不会创建 Owner、安装或启动 Hermes/OpenClaw、修改 Tailscale 或开放
公网。`--init` 的 setup code 不会回显；`--start` 成功后，安装器会在图形化
macOS 会话中打开现有 AgentOps MIS Console，并把一次性设置权限安全交给页面。
用户直接在 Workspace 界面创建 Owner 和登录。只有无图形环境或恢复场景才使用
`agentops host bootstrap-owner --confirm`。不加 `--init/--start` 时只完成校验
安装。下面的手工下载和比较步骤保留为审计/故障排查回退路径。

安装完成后，macOS 用户也可以直接双击：

```text
~/Applications/AgentOps MIS.app
```

这是现有浏览器 Workspace 的受管启动入口，不是另一套桌面客户端。首次双击时，
若主机尚未初始化，会使用产品默认端口 `18878` 完成本地初始化；若主机未运行，
只以 `--no-workers` 启动；随后调用 `agentops host open-console` 完成浏览器安全
交接。若主机已带 Hermes/OpenClaw Worker 运行，启动器只打开 Workspace，不会
重启、停止或降级 Worker。安装器固定使用安装时验证过的 Python 绝对路径，避免
Finder 与终端 PATH 不同。无图形/纯服务器环境可用
`AGENTOPS_NO_APP_INSTALL=1` 跳过该入口，CLI 能力不受影响。

preview.2 已发布但随后被真实 Runtime dogfood 替代：安装版 Worker 能
claim 任务，但 Agent Plan 在 `run_start` 前发现 archive 缺少
`PROJECT_SPEC.md`、`AGENT_WORKFLOW.md`、`BASE_INDEX.md` 与
`docs/AGENT_WORK_METHOD_BLOCK.md`，因此按设计 fail closed，未调用模型。
任何 `private-host-preview.*` 都仍是预览版本；物理第二设备和另一台 Mac
验收通过前不得称为正式 RC。具体已完成能力和未完成外部门以 Release
说明及 `PRIVATE_HOST_RC_ACCEPTANCE.md` 为准。

已发布但被替代的预览版本：

```text
v1.6.0-private-host-preview.1
https://github.com/geogejoy107-jpg/agentops-mis-mvp/releases/tag/v1.6.0-private-host-preview.1
```

该版本发布了以下三个资产：

```text
agentops-mis-private-host-1.6.0-private-host-preview.1.zip
agentops-mis-private-host-1.6.0-private-host-preview.1.tar.gz
agentops-mis-private-host-1.6.0-private-host-preview.1.sha256.json
```

preview.1 发布页记录的历史 SHA-256：

```text
zip      624e4ec9cb954bcf208ebf5840d535b07142a1557ba61be649de25f22e97b43f
tar.gz   e68bfae7e29a4f7356db4b162a965d29579ccaf2c9fc2a5ba3584c7440966e0a
manifest a7889a858801b4ef0fc4579d0f9e796b4b597395536417c2a9ac32497cd344ad
```

Release 下载验证发现 preview.1 的安装 shim 在源码 checkout 或继承的
`PYTHONPATH` 环境中可能被源码遮蔽，导致 packaged version provenance
读错；该 Release 已标记 **Superseded preview**。不要再把它作为当前安装
候选。修正版 preview.2 必须在 exact-head CI、资产 checksum、GitHub 下载
和干净安装复验完成后，才会在这里列为当前候选。即使 preview.2 通过，
在第二设备验收完成前也不能称为正式 RC。

从 GitHub Private Host prerelease 下载同一版本的三个文件：

```text
agentops-mis-private-host-<version>.tar.gz 或 .zip
agentops-mis-private-host-<version>.sha256.json
```

若不使用消费端安装器，先在下载目录独立计算 archive 的 SHA-256，并与 JSON 中对应文件名的
值逐字比较。校验不一致时不得解压或执行安装器。以 macOS 自带命令为
例：

```bash
shasum -a 256 agentops-mis-private-host-<version>.tar.gz
python3 -m json.tool agentops-mis-private-host-<version>.sha256.json
```

校验通过后解压并在 bundle 根目录执行：

```bash
tar -xzf agentops-mis-private-host-<version>.tar.gz
cd agentops-mis-private-host-<version>
sh install.sh
export PATH="$HOME/.local/bin:$PATH"
agentops host version
```

该 unsigned developer preview 需要主机已有 Python 3.10+；安装过程不联网，
不安装 Node、Git、Hermes、OpenClaw 或 Tailscale。安装器会再次按
`manifest.json` 校验每个文件，拒绝篡改、遗漏、未声明文件和路径穿越。
安装器还会在写入新版本或创建 pre-update 账本备份之前执行存储门禁：每个
相关文件系统必须在保留至少 `2147483648` bytes（2 GiB）余量后，仍能容纳
新版本的保守双份写入预算；升级时还会把 SQLite 主文件与 `-wal` 一并计入
pre-update backup 的双份预算。安装目录、数据/备份目录、CLI 目录和 App 目录
位于不同文件系统时分别检查。任一检查失败都不会创建 lifecycle lock、新版本、
pre-update backup 或切换 `current`；CLI/App 写入完成后才把 `current` 作为最后
提交点，提交前失败会保持旧版本并清理不可重试的新 target。
默认程序位于 `~/.local/share/agentops-mis`，数据位于
`~/.agentops/host`。操控端电脑不执行本节，它只需要浏览器。

## 4. 目标初始化流程

### 4.1 初始化 Host

```bash
agentops host init
```

目标行为：

- 在仓库外创建权限受限的运行目录；
- 检查生产 UI、Python 服务、SQLite、知识索引和端口条件；
- 初始化本地 Host 配置和 Owner bootstrap 流程；
- 默认保持 loopback，不启动 Tailscale Serve；
- loopback HTTP Session 使用 `HttpOnly`、`SameSite=Strict` Cookie，但不错误添加仅限 HTTPS 的 `Secure` 属性；
- 不安装、不启动 Hermes/OpenClaw，不读取现有 Runtime 密钥；
- 不把 setup code、Session、DB 或运行日志写入仓库。

初始化后先运行：

```bash
agentops host doctor
agentops host status
```

任一关键检查失败时，应先按 `doctor` 的本地修复建议处理，不要改成 `0.0.0.0` 或开放公网端口作为替代方案。

### 4.2 启动 Host

```bash
agentops host start
```

目标行为：

- 启动同源生产 UI、MIS API、SQLite 账本和知识索引；
- 不依赖运行时 Vite 开发服务器；
- 默认只监听 loopback；
- 打印本机 Console URL，但不在 URL 中携带任何凭据；
- Hermes/OpenClaw 未就绪时显示 `unavailable`，而不是伪装为真实运行成功；
- 真实 Agent 任务仍需明确启用并确认。

启动后检查：

```bash
agentops host status
agentops host doctor
agentops host console-url
```

预期至少能区分：Host 进程、生产 UI、API、账本、知识索引、Worker 和 Runtime adapter 的 `ready`、`degraded` 或 `unavailable` 状态。
`human_access.status` 还会明确显示：

- `bootstrap_required`：Host 已运行，但必须先在本机创建首个 Owner；
- `ready`：人类登录入口已就绪；
- `host_stopped` 或 `unavailable`：先启动或修复 Host，再检查登录状态。

Owner 未创建时，`status` 和 `doctor` 的 `next_actions` 会优先提示打开本机
Console；CLI 恢复入口列在其后。这属于人类控制面就绪状态，不会复用或显示
Agent Gateway 机器 token。

### 4.2.1 可选：登录后自动恢复 Host

默认安装不会注册后台服务。需要 macOS 用户登录后由 launchd 自动恢复 Host
控制面时，先预览，再显式确认安装：

```bash
agentops host service-install
agentops host service-install --confirm-install
agentops host service-check
agentops host stop
agentops host service-control --action load
agentops host service-control --action load --confirm-control
```

该 LaunchAgent 只运行 `agentops host start --foreground --no-workers`。它不会
安装、启用或执行 Hermes/OpenClaw，也不会在 plist 中保存 API key、Admin key、
Owner 设置码或 Runtime 凭据。`service-install` 只写文件且不加载；
`service-control` 不带 `--confirm-control` 时只返回计划。不要在一个已经由手工
`host start` 启动的 Host 上再加载服务，命令会拒绝制造第二个进程。

升级或卸载前先停止自动恢复并删除服务文件：

```bash
agentops host service-control --action unload
agentops host service-control --action unload --confirm-control
agentops host service-remove
agentops host service-remove --confirm-remove
```

删除仍处于 loaded 状态的服务会失败关闭；覆盖不属于 AgentOps MIS 的同名 plist
也会失败关闭。服务能力的可复现证据见
`docs/PRIVATE_HOST_BACKGROUND_SERVICE_ACCEPTANCE.md`。

首次使用时，在 Host 本机打开现有 AgentOps MIS Console：

```bash
agentops host open-console
```

命令只输出安全的本地 Console 地址，并通过 macOS 图形会话把受保护的一次性
设置权限交给浏览器；地址栏 fragment 会被页面立即清除，设置码不会进入命令
参数、标准输出、HTTP 请求或浏览器持久存储。Owner 密码至少需要 12 个字符。

无图形环境或界面不可用时，才使用恢复入口：

```bash
agentops host bootstrap-owner \
  --username <owner-name> \
  --display-name "<display name>" \
  --confirm
```

恢复命令会在终端中无回显地要求输入并确认密码，内部读取一次性 setup code，
然后只调用 loopback bootstrap API。不要把密码写进命令行、环境变量、URL
或聊天。自动化验收只能用 `--password-stdin` 从标准输入读取一行密码；产品
不提供 `--password` 参数。

若 Host 本机的 `agentops worker preflight` 仍指向旧的开发或验收端口，可显式
配置本机机器 CLI：

```bash
agentops host configure-cli --confirm
agentops worker preflight --adapter hermes
agentops worker preflight --adapter openclaw
```

该命令只连接配置中的 literal loopback Host，先验证 Agent Gateway 机器凭据，
再把当前 Host URL、workspace 和机器 token 写入权限为 `0600` 的本机 CLI
配置。它不会创建或复用浏览器 Session，不会打印 token，也不会配置远程
操控端；浏览器用户仍必须通过独立的人类登录流程。Host 必须由
`agentops host start` 作为受管进程启动，只有端口健康但没有有效 Host PID
记录时命令会失败关闭。

### 4.3 查看日志

```bash
agentops host logs
```

日志输出必须经过脱敏。不得显示 setup code、密码、Session、CSRF、Agent token、模型密钥、完整 prompt/response 或任意数据库内容。需要持续观察时，以实现支持的参数为准，不要假定 `--follow` 已发布。

当前源码还提供显式、停机状态下的有界轮转。先预览，再停止 Host，并确认同一份计划：

```bash
agentops host log-rotate
agentops host stop
agentops host log-rotate
agentops host log-rotate \
  --confirm-rotate \
  --plan-hash <上一条命令返回的 plan_hash>
agentops host start
```

如果预览针对 `launchd.log` 返回
`host_log_inventory_permissions_unsafe`，不要手工修改或读取日志。先通过受管
Service 安装边界预览并确认权限修复，再重新生成轮转计划：

```bash
agentops host service-check
agentops host service-install --overwrite
agentops host service-install --overwrite --confirm-install
agentops host log-rotate
```

该修复只接受当前用户拥有的普通单链接文件和 `0700` 日志目录；它通过已打开的
文件描述符把模式收紧到 `0600`，验证命名 inode 未变化，并保持既有日志字节不变。
软链、hardlink、错误 owner 或不安全目录会失败关闭。完成后再执行上面的
`stop -> log-rotate --confirm-rotate -> start`，不得复用修复前的 plan hash。
`log-rotate` 只会把候选修复引导到只读 `service-check`。只有 `service-check` 输出
`service_log_repair_available: true` 时才执行受管修复命令；若为
`manual_recovery_required: true`，必须先核查文件身份，不得反复确认或直接 `chmod`。

默认阈值为 8 MiB、保留 5 个历史文件；阈值硬下限为 1 MiB，历史数量范围为
2 至 20。预览只读取目录和文件元数据，不打印路径或日志正文。确认操作必须持有
Host lifecycle lock，并在整个操作期间持有与 local stack 相同的私有 runtime lock；
即使启动器异常退出或 PID record 缺失，只要任一受管 backend、UI、Relay 或 Worker 子进程
仍存活，继承的 runtime lock 就会让确认操作零写入拒绝。精确 plan hash 会绑定受管日志
清单、`launchd.log` 身份、阈值和保留配置；恢复时只允许同一 `launchd.log` inode 的 size
随 launchd 追加而单调增加，净截断会失败关闭；
stale plan、权限异常、软链、hardlink、未知 `host.log.*` 或序号断档都会失败关闭。
轮转只管理 `host.log` 与连续的 `host.log.1..N`，不修改账本、secret、配置或版本；
轮转自身不改写 `launchd.log`；它的 inode、owner、mode 与既有字节保留，launchd 在
恢复前追加的内容也保留，
临时 hardlink 可能更新文件系统 ctime。
实现通过目录 FD 锚定全部文件操作，在同卷私有 staging 中准备
完整新目录，再用内核原子目录交换一次提交；stage 成员删除前会逐个复验 inode。journal
首次发布使用原生 no-clobber rename，更新使用两个已打开文件之间的原子交换，不覆盖未验证的
同名文件。交换前骤停保持旧目录；交换后骤停保持完整新目录。有界 journal 会让下一次 dry-run 显示 `recovery_required`，再次显式确认
只完成收尾并要求重新生成计划；断电遗留的私有 journal 临时文件也按同一确认流程清理。
若交换前的 staging 清理失败，journal 会保留，不能先删证据再留下孤儿目录。
这些路径依赖 owner-only `0700` Host 目录以及 lifecycle/runtime lock 排除所有受管协作写者；
POSIX 不提供可移植的“仅当 inode 仍相同才 unlink/rmdir”系统调用，因此同一 Unix 用户下的
恶意并发写者不属于本地 Host 的隔离边界。
存在未完成轮转时 Host 启动会失败关闭。运行中自动轮转仍要等 sole-writer 日志 sink 发布，
不能用本停机维护命令冒充。

## 5. 高级模式：Tailscale Serve 预览

先只生成并审查预览：

```bash
agentops host tailscale-preview --https-port 8443
```

目标输出应包含：

- 检测到的 Tailscale 状态；
- 拟使用的本地 loopback 上游；
- 拟生成的 tailnet HTTPS Console URL；
- 用户需要手动执行的 Tailscale Serve 命令；
- 当前未执行任何网络变更的明确提示；
- 停止和撤销 Serve 的对应命令。

`tailscale-preview` 只能做只读检查和命令预览，不得：

- 自动运行 `tailscale serve`；
- 自动修改 ACL、DNS、路由器或防火墙；
- 自动启用 Funnel；
- 暴露公网；
- 把 token、setup code 或 Session 放进 URL、终端历史或日志。

macOS 上，CLI 会先查找 `PATH`，随后自动识别 `/Applications/Tailscale.app/Contents/MacOS/Tailscale`。特殊安装位置可以通过 `AGENTOPS_TAILSCALE_BIN` 显式指定；该变量只应指向可执行文件路径，不应包含认证信息。

管理员审查预览后，显式确认应用：

```bash
agentops host tailscale-apply --https-port 8443 --confirm
agentops host restart
```

命令调用当前 Tailscale CLI 的 `serve --https=<port> --bg`，只代理 Host loopback 地址，随后把
tailnet HTTPS Origin 写入私有 Host 配置并启用 `Secure` Session Cookie。没有 `--confirm` 时必须保持零副作用；
该路径不得调用 `tailscale funnel`。如果 `443` 已被 OpenClaw 或其他本地服务使用，优先选择独立端口（例如 `8443`），不得默认替换现有 Serve 入口。

启用后重新检查：

```bash
agentops host status
agentops host doctor
agentops host console-url
```

`console-url` 应只输出不含凭据的 HTTPS 地址。另一台电脑加入同一 tailnet 后，在浏览器中打开该地址，完成 Owner 初始化或登录。未认证用户不得读取 workspace 数据。

## 6. 高级模式：Tailscale 第二电脑流程

正式逐项协议与 bounded evidence 字段见
`docs/PRIVATE_HOST_SECOND_DEVICE_ACCEPTANCE.md`。浏览器生成的 device
checklist 只用于现场操作提醒，必须标记 `non_authoritative:true`；最终验收
回执必须由 Host 根据权威账本生成并计算 payload hash。

1. 手动安装并登录 Tailscale，确认与 Host 位于同一受信任 tailnet。
2. 在浏览器打开 `agentops host console-url` 给出的 HTTPS 地址。
3. 使用一次性设置/邀请流程建立人类账号，不使用 Agent Gateway 机器 token 登录浏览器。
4. 查看 Host、账本、知识索引、Worker 和 Runtime readiness。
5. 创建一条低风险任务并派发给可用团队。
6. 观察任务被 claim、Run 启动、Runtime Event、Evaluation、Artifact 和 Audit 写回。
7. 如出现 prepared action，在审批中心由有权限的人批准或拒绝。
8. 只下载明确批准的 ID 化产物；浏览器不得获得 Host 的任意文件路径。
9. 关闭或断开浏览器后，Host Worker 应继续运行；重新连接只恢复观察，不应重启任务。

真实 Hermes/OpenClaw 验收必须使用新鲜任务和明确确认。Mock 仅用于离线/CI 回退，并应在界面和录屏中标记为 Mock。

仓库或开发预览内置的真实验收客户端可以通过人类 Session 派发同一条客户闭环，而不是绕过 Private Host 认证。临时密码和一次性 setup code 只能放在当前 shell 的 `AGENTOPS_ACCEPTANCE_PASSWORD`、`AGENTOPS_OWNER_SETUP_CODE` 环境变量中，然后使用 `customer_worker_real_runtime_acceptance.py --human-auth --confirm-live`；不得把值写进命令行参数、文档、日志或 Git。

## 7. 日常生命周期

```bash
agentops host status
agentops host doctor
agentops host storage-preflight
agentops host logs
agentops host log-rotate
agentops host restart
agentops host stop
agentops host backup
agentops host backup-verify
agentops host backup-prune
```

- `status`：查看受管进程、版本、端口和组件状态，不输出 workspace 私密数据。
- `doctor`：做只读诊断，并给出可操作修复建议；不得静默修改网络或 Runtime。
- `storage-preflight`：无需初始化 Host，只读取目标文件系统容量元数据，返回
  `filesystem_path`、`free_bytes`、`required_bytes` 和 `status`；不读取账本内容、
  credential 或网络，且始终返回 `token_omitted:true`。
- `logs`：查看脱敏后的 Host 日志。
- `log-rotate`：默认只生成有界轮转计划；停机后提交同一 plan hash 并显式确认才写入。
- `restart`：只重启该 Host 拥有的进程，保留账本和知识状态。
- `stop`：停止 Host 服务和受管 Worker，不删除 DB、知识库、配置或备份。
- `backup`：通过 SQLite 在线备份 API 创建带 SHA-256 manifest 的本地账本备份；Host 可以继续运行。
- `backup-verify`：只读校验最新或指定备份的 SHA-256 与 SQLite integrity，不打印账本行。
- `backup-prune`：默认只生成保留计划；只有再次提交同一份计划的 hash 并显式确认，才删除已验证的旧备份配对。

命令的精确参数、服务管理方式和退出码均以当前实现验收为准。发布前必须证明重启不丢状态、停止不误杀其他进程。

### 7.1 备份与恢复

创建并校验备份：

```bash
agentops host backup
agentops host backup-verify
```

默认备份目录为 `~/.agentops/host/backups`，目录权限为 `0700`，SQLite 备份和 manifest 权限为 `0600`。备份包含完整权威 SQLite 账本，包括 hash-only Session/Token 状态，因此仍须作为敏感文件保护；它不复制 `secrets.json`、Host 日志、PID、原始 Runtime 密钥或 raw prompt/response。

清理旧备份必须先预览，再确认同一份计划：

```bash
agentops host backup-prune --keep 5
agentops host backup-prune \
  --keep 5 \
  --confirm-prune \
  --plan-hash <上一条命令返回的 plan_hash>
```

默认保留最新 5 份，硬下限为 2 份。尚未产生备份的 fresh Host 会返回空计划且不创建目录。预览会验证目录中的全部 SQLite/manifest 配对，并只返回有界的 inventory、retained、candidates、计数、可回收字节和 plan hash；不会打印账本行。只读校验使用 immutable SQLite 连接，不会因为检查 WAL-mode 备份而新建 sidecar。为兼容旧版校验遗留文件，严格匹配同一 backup id 的零字节 `.sqlite-wal` 和不超过 1 MiB 的 `.sqlite-shm` 会绑定到对应备份、进入 plan hash 并随配对一起保留或清理；孤立 sidecar、非空 WAL 或过大 sidecar 都会失败关闭。任何新备份都会改变 plan hash，因此旧计划不能重放。只提供 hash 而未确认、只确认而缺少 hash、hash 不匹配、未知文件、缺少配对、软链或内容篡改都会失败关闭且不删除文件。

确认路径持有 Host lifecycle lock，并先把候选配对移动到同一文件系统的私有 quarantine；移动失败会尝试完整回滚，清理未完成则返回有界错误和 quarantine 标签，避免把部分成功伪装成完成。该命令不修改当前 DB、`secrets.json`、Host 日志或安装版本。仍应先保留独立的离机灾备，不能把本地 retention 当作灾难恢复副本。

恢复前必须停止 Host，并先校验选定备份：

```bash
agentops host stop
agentops host backup-verify --backup /path/to/agentops-mis-YYYYMMDDTHHMMSSZ.sqlite
agentops host restore \
  --backup /path/to/agentops-mis-YYYYMMDDTHHMMSSZ.sqlite \
  --confirm-restore
agentops host start
```

没有 `--confirm-restore` 时恢复保持 dry-run；Host 进程仍运行时恢复会失败关闭。缺少 manifest、文件 hash/size/schema 不匹配、SQLite integrity 或 foreign-key 检查失败时也会拒绝恢复。覆盖现有账本前会创建并验证同目录的 `.pre-restore-<timestamp>` SQLite 安全快照，随后通过同目录临时文件原子替换。

恢复不会替换当前 Host 的独立密钥文件。为避免旧备份复活已经撤销的访问权限，恢复后的 `human_sessions`、`agent_gateway_sessions` 和长期 `agent_gateway_tokens` 会默认统一撤销；Owner 需要重新登录，Agent 需要重新 enrollment。

这一版只证明 SQLite 权威账本的产品命令闭环。Host 外部项目目录或未来可变 Markdown 知识源仍需要单独的目录级备份策略，不能把本命令宣传成整机灾备。

### 7.2 版本升级与二进制回滚

先停止 Host 并检查当前版本：

```bash
agentops host stop
agentops host version
agentops host update --check
agentops host storage-preflight
```

`update --check` 和 `storage-preflight` 都是离线只读检查，不会访问 GitHub 或
自动下载。默认最低余量是 2 GiB；管理员只能通过
`AGENTOPS_HOST_MIN_FREE_BYTES` 把门槛调高，低于 2 GiB、非法值或容量不可读都会
fail closed。管理员下载并校验更高版本的 bundle 后，运行该 bundle 自带的
`install.sh`。安装器在 Host PID 存活时拒绝升级；随后先执行与 CLI 同底线的
文件系统检查，如已有账本，再用旧版本工具创建并校验 pre-update 备份，失败则
拒绝升级。通过后才将新版本写入临时目录，原子切换 `current`，并保留
`previous` 指针；`~/.agentops/host` 数据目录不随程序版本移动。

CI 的正向/失败夹具使用
`AGENTOPS_BUNDLE_INSTALLER_TEST_STORAGE_JSON` 为隔离临时路径声明虚拟 device
和容量；安装器只会在显式 `AGENTOPS_BUNDLE_INSTALLER_TEST_MODE=1` 时解析它，
正常安装出现该变量会在任何写入前 fail closed。旧的
`AGENTOPS_BUNDLE_INSTALLER_TEST_FREE_BYTES` 即使在 TEST_MODE 中也只能降低真实
可用空间，不能抬高容量。两者都不能降低生产调用的 2 GiB 底线，不属于运维配置，
正常安装和运维不要设置这些测试变量。

如新版本启动验收失败，保持 Host 停止并执行：

```bash
agentops host rollback
agentops host rollback --confirm-rollback
agentops host start
```

第一条只显示 dry-run。确认回滚前，CLI 会先创建并校验当前 SQLite 账本备份，再原子交换 `current`/`previous` 二进制指针。当前版本尚无自动 schema downgrade；如果未来版本引入不向后兼容的数据库迁移，必须在 Release 级迁移策略中增加对应 downgrade 或数据恢复步骤，不能只靠切换二进制。

## 8. 录屏建议

建议录制 4 至 6 分钟的连续闭环，而不是逐页浏览 Dashboard：

1. **Host 终端**：展示 `agentops host status`、`doctor` 和 `console-url`；遮挡用户名、设备名及任何敏感信息。
2. **网络边界**：展示 Host 仍只监听 loopback，并说明远程入口由 AgentOps Relay 的 Host 端 TLS 隧道提供；Relay 未部署时必须明确标为待验收，不能用临时公网隧道替代。
3. **第二电脑配对**：Owner 在“账户与访问”创建一次性邀请，第二台电脑只用系统浏览器打开 HTTPS Console 地址并完成配对；该电脑不安装 AgentOps、Tailscale、项目依赖或 Agent Runtime。
4. **远程派发**：创建一条具体客户任务，选择 Host 上已就绪的 Hermes 或 OpenClaw，并进行显式确认。
5. **断线证明**：关闭第二电脑页面片刻，在 Host 或重新连接后的 Worker 页面展示任务仍在运行。
6. **证据闭环**：展示最新任务的 Run、Runtime Events、Tool Calls、Evaluation、Artifact、Approval 和 Audit；不要展示 raw prompt/response。
7. **知识边界**：展示有界检索引用或已批准记忆，说明完整知识库仍留在 Host。
8. **收尾撤销**：展示 Owner 撤销配对设备、远程浏览器立即失效，以及停止 Host/远程 Console 的步骤。

最有说服力的三张截图：

- Host 状态页或终端：生产 Host、账本、知识索引与 Hermes/OpenClaw readiness 同时可见；
- 第二电脑浏览器：远程任务与 Worker 状态同屏，地址栏是稳定的 AgentOps HTTPS Console 地址且不含 token；
- 新鲜 Run 详情：任务、事件、评估、批准产物和审计链路同屏。

录制前关闭系统通知，使用 16:9 画面，并检查画面中没有 `.env`、token、DB 路径、private message、完整 transcript、raw prompt/response 或未经批准的产物。

## 9. 故障排查

### Host 命令不存在

说明当前安装尚未包含该产品切片。检查版本和当前 Release 验收记录。不要把 `run_local_stack.py` 或 Vite 开发服务器直接暴露给第二电脑来模拟 Host 产品。

### Host 启动失败

依次运行：

```bash
agentops host status
agentops host doctor
agentops host logs
```

检查端口占用、生产 UI 资产、运行目录权限、磁盘空间和数据库可写性。不要删除现有 DB 作为第一修复手段。

### 第二电脑无法打开 Console

- 确认 Host 本地服务仍为 loopback ready；
- 确认 Host 已显式启用远程 Console，并且 AgentOps Relay 隧道健康；
- 确认使用 `console-url` 的稳定 HTTPS 地址，而非 Host 的 `127.0.0.1`；
- 检查 Host 端 TLS 证书、DNS/SNI 路由和隧道状态，不要改用公网端口转发或临时 quick tunnel。

如果管理员显式选择第 5、6 节的高级 Tailscale 模式，再检查同一 tailnet、Serve 状态、ACL 和 MagicDNS；这些步骤不属于普通用户默认流程。

### 页面能打开但无法登录或写入

- 检查人类 Session 是否过期或被撤销；
- 检查账号角色是否允许派发、审批或管理；
- 检查 Host 时间是否准确；
- 通过 `doctor` 检查 allowed origin、Secure Cookie 和 CSRF 状态；
- 不要把 Agent Gateway token 粘贴到浏览器作为替代凭据。

### Agent 显示 unavailable

这表示 Host 上对应 Runtime 未安装、未启动或健康检查失败。浏览器操控端无需安装 Runtime。由 Host 管理员在本机修复 Hermes/OpenClaw，并再次运行 `doctor`。第一阶段不提供 Runtime 自动安装。

### 浏览器断开后任务停止

这是验收失败，而不是正常行为。检查 Worker 是否由 Host 生命周期管理、是否错误绑定到浏览器请求，以及重连后是否能查询同一 task/run。保留脱敏日志和 ID 供排查，不保存 raw prompt/response。

## 10. 停止、退出与撤销

先让远程操作者退出登录，再在 Host 执行：

```bash
agentops host stop
agentops host status
```

默认 Relay 模式下，先从“账户与访问”撤销对应设备，再关闭远程 Console 隧道；精确 CLI/UI 以 Relay 实现验收为准。Host 应继续保持 loopback，不删除本地账本、知识库或 Runtime 状态。

只有在显式使用高级 Tailscale 模式时，才撤销 Tailscale Serve 并重启 Host：

```bash
agentops host tailscale-revoke --confirm
agentops host restart
```

撤销内部使用与 apply 相同端口的 `tailscale serve --https=<port> off`，只移除 MIS 入口和对应私网 HTTPS trusted Origin，不清空其他 Serve 服务。确认：

- `console-url` 不再可从另一台电脑访问；
- Host 受管进程已停止；
- 未删除 SQLite、知识库、项目文件或备份；
- 没有 token、DB、日志、缓存或生成产物进入 Git 状态。

如需撤销浏览器访问，应从 Host 撤销对应人类 Session/设备授权；精确命令和 UI 以实现验收为准。删除 Tailscale 设备、修改 tailnet ACL 或清理 Host 数据属于独立高影响操作，不应由 `host stop` 自动执行。

## 11. 发布验收口径

只有以下证据同时存在，才能把本手册描述为正式可用流程：

- 版本化 Host 安装资产及 SHA-256；
- `init/start/status/storage-preflight/doctor/logs/stop/restart/console-url/tailscale-preview` 的命令 smoke；
- 生产 UI 同源服务，不依赖运行时 Node/Vite；
- 第二电脑仅凭系统浏览器和一次性配对完成认证与客户任务闭环，不安装 Tailscale/VPN；
- 未认证读取、错误角色、CSRF、错误 Origin 和撤销 Session 均 fail closed；
- Console 断线不影响 Host Worker，Host 重启不丢账本和知识状态；
- 新鲜、显式确认的 Hermes/OpenClaw 运行证据；
- 停止远程 Console、撤销配对设备并验证现有会话失效；
- exact-head CI、secret scan、干净安装、备份恢复和 Release provenance 通过。

在上述验收完成前，本文件是产品操作契约和录屏准备依据，不是“功能已经全部发布”的声明。

高级 Tailscale 模式另需保留 `tailscale-preview/apply/revoke` 的独立验收，但它不能关闭默认浏览器零安装路径的发布门。
