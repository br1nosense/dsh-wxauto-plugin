# dsh-wxauto-plugin — DSH 微信汇报与监听插件

基于 **wxauto4**（Python 免费版，支持微信 4.1.x 客户端）的 DSH 技能插件。

**核心功能**
1. **任务进度微信推送**：DSH 任务进行/完成后，向指定微信聊天窗口发送任务进度信息（文本或文件）。
2. **微信消息监听**：监听指定聊天的微信消息，新消息写入 JSONL 日志供 agent 读取，并支持关键词自动回复。
3. **微信⇄DSH 双向桥（反向操作 DSH）**：在微信里发命令或普通消息即可切换对话、新建对话、驱动 DSH 跑任务，
   并收到 DSH 的**进度截图**（会话状态渲染 PNG）与完成汇报。

## 安装（从 GitHub）

```powershell
# 1. 克隆仓库
git clone https://github.com/br1nosense/dsh-wxauto-plugin.git
cd dsh-wxauto-plugin

# 2. 安装 Python 依赖（wxauto4 免费版 + websocket-client：桥把 DSH 提问转发到微信需要它）
py -3 -m pip install wxauto4 websocket-client

# 3. 一键安装为 DSH 插件（注册到默认 web profile）
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install.ps1

# 4. 重启 dsh web 使插件生效
```

> - 要求：Windows 10/11 + 微信 4.1.x 客户端（已登录）+ Python 3.9–3.12。
> - `install.ps1` 会同时注册技能目录与设置页卡片；若 dsh 装在非默认位置，
>   apiproxy 暴露 `wxauto` 命名空间一步会打印警告（不影响插件主体功能，可手动补）。
> - 卸载：`dsh plugin --profile web rm @dsh-user/dsh-wxauto` 后重启 dsh web。

## 常见问题：安装插件后 dsh 启动失败（peerDependencies 解析）

**症状**：安装本插件（bundle）后重启 dsh，web 启动报错 / 起不来。

**原因**：本插件把 `@deepseek-ai/schemastery`（以及 `@deepseek-ai/cordis`、`@deepseek-ai/dsh-settings`）
声明为 `peerDependencies`，但插件目录（clone 下来的仓库）下没有 `node_modules`。Node ESM 会从插件
所在目录**逐级向上**查找 `node_modules`，却找不到 DSH 自身嵌套目录里的这几个包（dsh 安装目录通常
不在该查找路径上）。

**修复**（一次性，本地环境级）：在**插件工作区根目录**（即插件目录的父目录，如把插件 clone 到
`C:\code\AI\DSH\dsh-wxauto-plugin`，则根目录是 `C:\code\AI\DSH`）的 `node_modules\@deepseek-ai\` 下
创建 3 个目录联接（Junction），指向 DSH 自带的同名包：

| 包 | 指向 |
|---|---|
| `schemastery` | `<dsh 安装目录>\node_modules\@deepseek-ai\schemastery`（v3.18.1） |
| `cordis` | `<dsh 安装目录>\node_modules\@deepseek-ai\cordis` |
| `dsh-settings` | `<dsh 安装目录>\node_modules\@deepseek-ai\dsh-settings` |

例如本机 dsh 位于 `C:\Users\<你>\AppData\Roaming\nvm\v24.19.0\node_modules\@deepseek-ai\dsh`：

```powershell
$dsh = 'C:\Users\<你>\AppData\Roaming\nvm\v24.19.0\node_modules\@deepseek-ai\dsh\node_modules\@deepseek-ai'
$dst = 'C:\code\AI\DSH\node_modules\@deepseek-ai'
New-Item -ItemType Directory -Force -Path $dst | Out-Null
foreach ($p in 'schemastery','cordis','dsh-settings') {
  New-Item -ItemType Junction -Path (Join-Path $dst $p) -Target (Join-Path $dsh $p)
}
```

> 该修复是**本机环境级**配置，创建在 `node_modules` 下，**不应提交到仓库**；dsh 升级或换机后路径变化
> 需重建（建议把上面脚本存成 `fix-peer-deps.ps1`，换环境后重跑）。

## 环境要求

| 项 | 要求 |
|---|---|
| 系统 | Windows 10/11 |
| 微信 | 4.1.x 客户端（`C:\Program Files\Tencent\Weixin\Weixin.exe`），已登录 |
| Python | 3.9–3.12（wxauto4 免费版要求） |
| 依赖 | `pip install wxauto4 websocket-client`（wxauto4 拉取 comtypes/pywin32/pillow/psutil 等；websocket-client 用于把 DSH 提问转发到微信） |

已验证组合：微信客户端 **4.1.8.107** + `wxauto4 41.1.2` + Python 3.12.6。

## 本地安装（等价于 install.ps1）

```powershell
# 1. 安装 Python 依赖
py -3 -m pip install wxauto4 websocket-client

# 2. 安装为 DSH 插件（bundle，注册到 web profile）
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install.ps1

# 3. 重启 dsh web 使插件生效
```

### 插件（bundle）说明

本包是标准 DSH profile bundle：
- `package.json` 声明 `dsh.bundle.patch: ./cordis.patch.yml`，经 `dsh plugin --profile web add <dir>` 安装后自动加入 profile 的 `dsh.profile.bundles` 层栈。
- `cordis.patch.yml` 挂载 `wxauto` 插件行（inject `skills` + `settings` 服务）。
- `lib/index.js` 在运行时把 `dsh-wxauto` 技能注册进 `ctx.skills`（`resourceBase` 指向本包 `skills/dsh-wxauto` 目录），注册 `wxauto` **设置命名空间**（配置落在 `~/.dsh/settings.yaml` 的 `wxauto:` 段），并提供 `ctx.wxauto` 服务（双向桥开关 `bridgeEnabled` 实时起停 wx_bridge.py、桥状态、配置镜像 `data/dsh_settings.json`）。
- `lib/client.js` 在**设置页侧边栏注册独立的「微信自动化」Tab**（位于「换肤与宠物」下方，含双向桥/监听开关与聊天配置）。
- install.ps1 还会给 apiproxy 的 `WEB_SETTINGS_NAMESPACES` 幂等加入 `wxauto`（dsh 升级覆盖后需重跑）。

**双向桥开关**：`wxauto.bridgeEnabled`（设置页「微信自动化」Tab 或 settings.yaml）。开启=自动拉起桥（**占用本机微信窗口**，期间勿手动操作微信）；关闭=自动停止。监听同理有 `listenEnabled`。

旧方式（仅技能目录，不装 bundle）可用 `install.ps1 -Junction`。已装过 junction 时，插件生效后技能改由插件运行时注册，旧 junction 可删除（install.ps1 会自动清理）。

### 手工安装（等价于 install.ps1）

```powershell
dsh plugin --profile web add <仓库目录>
dsh --profile web --dump-config   # 验证应出现 id: wxauto 的行
```

### 首次使用要改的默认配置

- 插件默认把汇报/监听/控制聊天指向「文件传输助手」（`skills/dsh-wxauto/config.json`）。
- 新会话工作目录（`cwd`）缺省为**用户主目录**；要改成工作区，在 `~/.dsh/settings.yaml` 的 `wxauto:` 段加 `cwd: D:\你的\工作区`。
- 启用双向桥：设置页「微信自动化」Tab 打开总开关（滑块），或 settings.yaml 设 `wxauto.enabled: true`（会占用本机微信窗口）。

## 快速开始

```powershell
# 0) 预检
powershell.exe -NoProfile -ExecutionPolicy Bypass -File skills\dsh-wxauto\wrappers\wx-test.ps1

# 1) 列出会话，确认聊天名
... wx-sessions.ps1

# 2) 向文件传输助手发一条测试
... wx-send.ps1 -Who "文件传输助手" -Msg "任务已完成 ✅"

# 3) 单次监听检查（需先打开设置里的 listenEnabled，或加 -Force）
... wx-listen.ps1 -Who "文件传输助手" -Once -Force -Json

# 4) 后台持续监听 1 小时（DSH 后台任务）
... wx-listen.ps1 -Who "文件传输助手" -Timeout 3600 -Force
```

## 目录结构

```
dsh-wxauto-plugin/
├── package.json            # DSH bundle 声明（dsh.bundle.patch -> cordis.patch.yml）
├── cordis.patch.yml        # 挂载 wxauto 插件行（inject skills）
├── lib/index.js            # 插件入口：ctx.skills.register 注册技能 + ctx.wxauto 服务
├── lib/types/index.d.ts
├── README.md
├── install.ps1             # 一键安装为 DSH 插件（-Junction 为旧方式回退）
└── skills/dsh-wxauto/
    ├── SKILL.md            # 技能说明（agent 据此调用）
    ├── config.json         # 配置（target_chats / listen_chats / bridge_chats / dsh_base…）
    ├── scripts/            # Python 实现（wx_* / dsh_api / dsh_card / dsh_ops / wx_bridge）
    ├── wrappers/           # PowerShell 封装（wx-* / dsh-*）
    └── data/               # 运行时：listen.jsonl / bridge_state.json / shots/
```

## 命令一览

| 命令 | 作用 |
|---|---|
| `wx-test.ps1` | 环境预检（Python/wxauto4/微信进程/登录） |
| `wx-sessions.ps1` | 列出会话（确认目标聊天名） |
| `wx-send.ps1` | 发送进度/完成信息（文本/文件，多目标，`@config`） |
| `wx-read.ps1` | 读取某聊天最近 N 条消息 |
| `wx-listen.ps1` | 监听（轮询，JSONL 落盘 + 关键词自动回复；`-Once` 单次） |
| `wx-bridge.ps1` | 微信⇄DSH 双向桥（控制聊天驱动 DSH，任务完成推结果+进度截图） |
| `dsh-list.ps1` / `dsh-new.ps1` / `dsh-switch.ps1` | 会话列表 / 新建 / 切换（agent 侧直连） |
| `dsh-status.ps1` / `dsh-shot.ps1` / `dsh-history.ps1` | 进度文本 / 进度截图 / 最近对话 |
| `dsh-task.ps1` / `dsh-cancel.ps1` | 执行任务 / 取消回合 |

## 微信反向操作 DSH（双向桥）

桥监听「控制聊天」，把微信消息映射为 DSH 的 RPC 调用（DSH web 的 `/api/<method>`，回环免认证）：

```
/help /new /list /switch <序号|前缀> /active /status /shot /history [n] /task <内容> /cancel
直接发普通消息 = 把内容作为任务交给当前 DSH 会话执行
```

启动（DSH 后台任务）：`wx-bridge.ps1 -Who "我的控制群" -Timeout 86400`
自测（文件传输助手）：`wx-bridge.ps1 -Who "文件传输助手" -AllowSelf -Timeout 3600`

「DSH 截图发给用户」= `dsh-shot.ps1` 把会话状态（标题/统计/任务清单/最近对话）渲染成 PNG
（`data/shots/`），再经微信发送；`/shot` 与任务完成推送自动完成。

**DSH 提问转发（ask_user_question）**：agent 需要用户选择/回答时，桥会订阅 DSH 的
events.mux，把问题+选项**推送到微信**（编号列出），你在微信里直接回复「选项编号/选项内容」
（多问题用「序号: 答案」分行，回复 0 跳过，多选用逗号分隔）即可，回答会回填给 agent，
任务继续执行、完成后照常推送结果。需安装 `websocket-client`；等待回答超时（默认 600 秒，
配置 `question_timeout`）自动跳过。

## 监听实现说明

wxauto4 **免费版**没有 `AddListenChat`/`GetNextNewMessage`（Plus 版 `wxautox4` 专属），
因此监听采用**轮询方案**：`GetSession()` 找 isnew 标记 → 打开窗口读新消息 → 打开即标记已读，再配合去重集合。
新消息逐条写入 `data/listen.jsonl`；`data/reply_rules.json` 配置关键词自动回复。

## 合规声明

wxauto 官方声明代码仅用于 UIAutomation 技术交流学习，禁止用于生产/营销/非法用途。
频繁群发、营销、新号大量加好友等有封号风险。向非本人聊天窗口发送前请确认对象与频率。
