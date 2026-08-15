---
name: dsh-wxauto
metadata:
  version: 0.1.0
  repository: https://github.com/cluic/wxauto
description: >
  基于 wxauto4（Python，免费版）的微信自动化技能。主要功能：① DSH 任务进行/完成后，
  向指定的微信聊天窗口发送任务进度信息（文本或文件）；② 支持监听指定聊天的微信消息
  （免费版轮询方案）：新消息写入 JSONL 日志供 agent 读取，并支持关键词自动回复。
  需要本机已安装并登录微信 4.1 客户端（Weixin.exe）。调用方式统一走 wrappers 下的
  PowerShell 脚本，避免直接与 Python 细节纠缠。
---

# DSH 微信汇报与监听技能（dsh-wxauto）

基于 **wxauto4**（免费版，`pip install wxauto4`，支持微信 4.1.x 客户端）实现的任务进度微信推送与消息监听。
所有操作都通过 `wrappers` 目录下的 PowerShell 脚本完成，脚本会自动定位 Python（`py -3`）。

## 何时使用本技能

- 用户要求「把任务进度/结果发到微信」「做完后通知我」：用 **发送**。
- 用户要求「监听某个聊天/群」「微信里有人发消息就自动回复/记录」：用 **监听**。
- 需要确认目标聊天窗口的真实名称：先 **列会话**。
- 首次使用或出问题：先 **预检**。

## 调用约定

优先用完整的显式命令（harness 默认可能是 `cmd.exe` 或 `pwsh`，这里统一用 PowerShell 形式）：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File <skill目录>\wrappers\wx-send.ps1 -Who "文件传输助手" -Msg "任务已完成"
```

如果 harness 的 shell 就是 PowerShell/pwsh，也可以省略前面的 `powershell.exe ... -File`，直接：
`wrappers\wx-send.ps1 -Who ...`。不要把 `<skill目录>` 原样复制，要替换成实际路径。

## 1. 环境预检

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File <skill目录>\wrappers\wx-test.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File <skill目录>\wrappers\wx-test.ps1 -Json
```

检查项：Python、wxauto4 库、微信主进程（Weixin.exe）、登录状态（GetMyInfo）。
任一项 ❌ 先解决再继续（常见：微信没开/没登录；`pip install wxauto4` 未执行）。

## 2. 列出会话（确认目标聊天名）

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File <skill目录>\wrappers\wx-sessions.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File <skill目录>\wrappers\wx-sessions.ps1 -Match "工作群" -Json
```

返回会话名、是否有新消息、最近消息内容。**发送/监听前务必确认目标名称与实际会话完全一致**（精确匹配）。

## 3. 发送任务进度 / 完成信息

```powershell
# 发送文本（单行）
... wx-send.ps1 -Who "文件传输助手" -Msg "任务已完成 ✅"

# 发送长报告（写进文件再发送，避免命令行转义问题）
... wx-send.ps1 -Who "@config" -MsgFile "C:\tmp\report.txt"

# 同时发到多个聊天，并附带文件
... wx-send.ps1 -Who "张三,工作群" -File "C:\tmp\report.pdf" -Msg "详见附件"

# 把内容发到配置里 target_chats 指定的所有聊天
... wx-send.ps1 -Who "@config" -Msg "任务进度：50%"
```

参数：`-Who`（逗号分隔多个；`@config` = 配置里 `target_chats`）、`-Msg` / `-MsgFile`、
`-File`（可多次传）、`-At`（@对象，可选）、`-Json`。

**任务进度汇报约定**：
- 用户开启自动汇报（配置 `auto_report: true`，默认开启）时，agent 应在任务的**关键里程碑**和**结束时**主动向 `target_chats` 发送进度/汇总。
- 汇报内容建议含：任务状态（进行中/完成/失败）、当前进度、完成项摘要、耗时、下一步。
- 用 `-Who "@config"` 批量发送时，脚本会自动套用配置里的 `report_prefix`/`report_tail` 统一前后缀。
- 长内容写临时文件后用 `-MsgFile` 发送，避免中文/换行转义问题；发送后可删临时文件。

## 4. 读取聊天消息（查看对话上下文）

```powershell
... wx-read.ps1 -Who "文件传输助手" -Count 20
... wx-read.ps1 -Who "文件传输助手" -Count 20 -Json
```

## 5. 监听（免费版轮询方案）

> 说明：wxauto4 免费版**没有** `AddListenChat` / `GetNextNewMessage`（那是 Plus 版 `wxautox4` 专属）。
> 本技能用「轮询 `GetSession()` 的 isnew 标记 → 打开窗口 → 读新消息」实现，打开窗口即标记已读，配合去重集合避免重复。

```powershell
# 单次检查（适合 agent 快速看有没有新消息）
... wx-listen.ps1 -Who "文件传输助手" -Once -Json

# 后台持续监听（DSH 里用后台任务启动，可限时）
# 例如：run_in_background 启动，监听 3600 秒后自动退出
... wx-listen.ps1 -Who "文件传输助手,工作群" -Timeout 3600

# 不带 -Who 时用配置 listen_chats
... wx-listen.ps1 -Timeout 3600
```

监听行为：
- 新消息**逐条追加**到 JSONL 日志（默认 `<skill目录>\data\listen.jsonl`），每行一条 JSON：`{ts, chat, chat_type, attr, type, sender, content, reply_sent?, reply?}`。
- 每收到一条新消息会在 stdout 打印一行（后台任务可捕获）。
- **关键词自动回复**：编辑 `<skill目录>\data\reply_rules.json`（示例见 `data\reply_rules.example.json`），格式 `{ "关键词": "回复文本" }`，回复支持 `{who}{sender}{content}` 占位。匹配到且发送者为好友/群友时，自动回一条消息；同一条消息只回一次。
- 忽略自动回复造成的「自己发的消息」（`attr=self`），不会自触发。

agent 读取监听结果的方式：
1. 让监听以后台任务运行（`run_in_background`），读取其输出；
2. 或直接读 JSONL 日志文件（**务必加 `-Encoding UTF8`**，避免中文在控制台乱码）：
   `Get-Content <skill目录>\data\listen.jsonl -Tail 50 -Encoding UTF8`；
3. 或执行一次 `-Once -Json` 检查当前是否有新消息。

## 6. 配置

配置文件：`<skill目录>\config.json`（模板 `config.example.json`），字段：
- `target_chats`：`@config` 发送时默认的聊天列表
- `listen_chats`：`-Who` 缺省时的监听聊天列表
- `listen_interval`：轮询间隔秒数（默认 3）
- `listen_log` / `reply_rules_file`：日志与回复规则路径（相对技能目录）
- `auto_report`：是否在任务完成时自动汇报
- `report_prefix` / `report_tail`：汇报消息前后缀
- `dsh_base`：DSH web 地址（默认 http://127.0.0.1:3080）
- `dsh_cwd`：新建 DSH 会话的工作目录
- `bridge_chats`：桥的控制聊天（`-Who` 缺省时使用）
- `bridge_poll_interval` / `bridge_task_timeout`：桥轮询间隔 / 任务等待超时（秒）
- `bridge_state` / `bridge_log` / `shots_dir`：桥状态、日志、截图目录

### 6.1 优先在 DSH 设置里配置（插件已注册 `wxauto` 命名空间）

插件注册了 `wxauto` 设置命名空间，配置落在 **`~/.dsh/settings.yaml` 的 `wxauto:` 段**
（Web 设置页 → 侧边栏「微信自动化」Tab，位于「换肤与宠物」下方），**优先级高于 `config.json`**：

| 设置字段 | 含义 | config.json 对应 |
|---|---|---|
| `bridgeEnabled` | 双向桥开关（默认 false） | `bridge_enabled` |
| `listenEnabled` | 监听开关（默认 false） | `listen_enabled` |
| `bridgeChats` | 桥控制聊天 | `bridge_chats` |
| `targetChats` | 汇报目标聊天 | `target_chats` |
| `listenChats` | 监听聊天 | `listen_chats` |
| `dshBase` / `cwd` / `listenInterval` / `taskTimeout` / `autoReport` / `reportPrefix` / `reportTail` | 同 config.json 字段 | 对应 snake_case |

优先级：**DSH 设置（settings.yaml / 设置页）> config.json > 内置默认**。
改设置即时生效（热重载）；Python 侧由插件镜像到 `data/dsh_settings.json` 读取。

## 7. 微信⇄DSH 双向桥（反向操作 DSH）

通过微信反向操作 DSH：在「控制聊天」里发命令或直接发普通消息，即可驱动 DSH 切换对话、
新建对话、跑任务，并收到 DSH 的**进度截图**与完成汇报。

**原理**：DSH web 服务（http://127.0.0.1:3080）暴露回环 RPC API（`POST /api/<method>`，
免认证）。桥（wx_bridge）复用本技能的微信监听/发送能力，把微信消息映射为 DSH 的
`session.list/create/prompt/history/cancel` 调用；进度截图由 dsh_card 把会话状态
（标题/统计/任务清单/最近对话）渲染成 PNG 再经微信发送。

### 7.1 命令一览（在控制聊天里输入）

```
/help            帮助
/new             新建对话（设为当前）
/list            会话列表（▶=当前，带序号）
/switch <序号|前缀>  切换对话
/active          当前对话
/status          查看进度（标题/统计/任务清单）
/shot            发送 DSH 进度截图（PNG 卡片）
/history [n]     最近对话
/task <内容>     执行任务（= 直接发普通消息）
/cancel          取消当前回合
```

直接发**普通消息** = 当作任务交给当前 DSH 会话执行，完成后自动推送结果 + 进度截图。

### 7.2 一体化守护进程（桥 + 监听整合）与占用说明

- **桥与监听已整合为同一个守护进程**（wx_bridge.py），不会双进程互踩：
  - `--mode full`（默认，双向桥）：`/`命令 + 关键词自动回复 + 普通消息当任务执行 + 完成推送（内容分片 + 浏览器渲染进度截图）
  - `--mode listen`（纯监听，`wx-listen.ps1` 即此模式别名）：记录 JSONL + 关键词自动回复，不做 DSH 任务
- **开关**：`bridgeEnabled`（full 模式）/ `listenEnabled`（listen 模式）——任一开即启动同一进程，两开关都开时以 full 运行；两者都关则不启动。
- **前提：微信客户端必须打开并登录**（wxauto 驱动本机微信窗口）。若微信未开，进程退出后**自动退避重试**（10s→30s→60s），打开微信即恢复。
- **看护自愈**：`wx-bridge.ps1` 带看护循环，进程意外退出自动退避重启；插件自动拉起另有 60 秒兜底；重启不重复处理旧消息（seen 持久化），待推送任务完成回复自动补发（pending 持久化）。
- **单实例守卫（防任务重复受理）**：桥/监听共用一把进程级单实例锁（`data/wx_daemon.lock`）。
  同一时间只允许一个 wxauto 守护进程轮询，**第二个进程启动即退出（退出码 0，看护不会反复重启）**，
  杜绝「插件自动拉起 + 手动 wx-bridge.ps1 看护」等多开导致同一条微信消息被两个进程重复受理、
  重复推送完成。另在轮询锁内合并磁盘 seen 并即时持久化，双进程意外并存时也不会重复受理。
- **完成推送如实上报（防假完成）**：等待回合完成时传入 `from_seq`，只等**本次任务的新回合**结束，
  不会误匹配旧回合而提前返回；推送时按 `turn/end` 的 reason 如实标注：`completed` 才发「✅ 任务完成」，
  `aborted/cancelled` 发「⚠️ 任务被取消」，`error/blocked/max-tokens/interrupted` 发「❌ 任务未完成」，
  未确认时发「⚠️ 已结束但未确认完成」，绝不假装成功。
- **占用提示**：运行期间会**占用本机微信窗口**，**期间请勿手动操作微信**。启动时打印醒目警告；用完后关闭对应开关即恢复。
- **省鼠标设计**：轮询间隔默认 8s；`GetSession` 查新消息不切窗，仅在"有新消息且窗口不在控制聊天"时才切换；发送时窗口已在目标聊天则跳过 ChatWith；`resize=False` 不重置窗口。
- **兜底强读（防止「开了监听却没反应」）**：控制聊天是用户明确指定的，不能只靠 `GetSession` 的 `isnew` 标记 —— 被微信折叠进「折叠的聊天」的会话，`GetSession` 顶层列表里看不到（拿不到 `isnew`），若不兜底就会永远跳过、收到消息没反应。桥会**每 `bridge_force_interval` 秒（默认 30）强制 `ChatWith` 切过去读一次** `GetAllMessage`，保证这类聊天的新消息也能被捕获并受理。
- **忙会话自动新建（防止「已受理但 DSH 不执行」）**：提交任务前检查 active 会话是否正在运行。若 active 会话忙碌（例如 agent 卡在等用户回答的 `ask_user_question`、或正处理长任务），任务**不排队**到该会话，而是**自动新建会话执行** —— 否则排队任务会一直不跑（表现为微信回了「已受理任务」但 DSH 里毫无动静）。空闲会话则复用（保持上下文）。

### 7.3 启动桥（DSH 后台任务）

```powershell
# 常驻：监听「我的控制群」，跑 1 天（需先在设置打开 bridgeEnabled）
powershell.exe -NoProfile -ExecutionPolicy Bypass -File <skill目录>\wrappers\wx-bridge.ps1 -Who "我的控制群" -Timeout 86400

# 用文件传输助手自测（--AllowSelf 允许处理自己发的 /命令；开关未开时加 --Force）
... wx-bridge.ps1 -Who "文件传输助手" -AllowSelf -Force -Timeout 3600

# 单次处理（排查用）
... wx-bridge.ps1 -Who "文件传输助手" -AllowSelf -Force -Once
```

注意：
- **控制聊天建议用「好友/群聊」**（你从手机发消息给 PC 账号、或你在的群）。文件传输助手里的
  消息是 `attr=self`，默认被忽略，需 `-AllowSelf` 才处理（且只处理 `/` 开头的命令，防自循环）。
- 桥**串行化**所有微信 UI 操作（wxauto4 非线程安全），任务完成后台推送，命令回复即时。
- 进度截图保存在 `<skill目录>\data\shots\`。

### 7.4 agent 侧直连 DSH 的命令（不经过微信）

```powershell
... dsh-list.ps1            # 会话列表
... dsh-new.ps1             # 新建会话
... dsh-switch.ps1 -Target 1   # 切换
... dsh-active.ps1          # 当前会话
... dsh-status.ps1          # 进度文本
... dsh-shot.ps1            # 生成进度截图（打印路径）
... dsh-history.ps1 -Count 10
... dsh-task.ps1 -Task "要做的事" -Wait   # 执行并等待结果
... dsh-cancel.ps1          # 取消
```

「截图发送给用户」的完整链路：`dsh-shot.ps1` 生成 PNG → `wx-send.ps1 -Who 聊天 -File <png>`
发送；桥的 `/shot` 与任务完成推送自动完成这一步。

## 8. 免费版 vs Plus 版

- 免费版 `wxauto4`：支持发送文本/文件、列会话、读消息、`GetMyInfo`/`ChatInfo`。监听只能用本技能的轮询方案。
- Plus 版 `wxautox4`：额外支持 `AddListenChat` 回调式监听、`GetNextNewMessage`、`GetSubWindow`、`AtAll` 等（付费）。
- 若本机安装了 `wxautox4` 且用户要求更强的回调监听，可另行说明，本技能默认走免费版。

## 9. 合规与风控提示

wxauto 官方声明代码仅用于 UIAutomation 交流学习，禁止用于实际生产/营销/非法用途。
频繁群发、营销、新号大量加好友等行为有封号风险。向**非本人**的聊天窗口发送消息前，
应确认发送对象与频率，遵守平台规则。

## 维护约定

- Python 脚本源码保持 UTF-8；中文通过参数/文件传入，stdout 已强制 UTF-8。
- 新增命令时：在 `scripts` 加 `.py`、在 `wrappers` 加同名 `.ps1`，并在此文档补调用示例。
- 监听逻辑改动后，用 `-Once` 先验证单次轮询再跑后台。
