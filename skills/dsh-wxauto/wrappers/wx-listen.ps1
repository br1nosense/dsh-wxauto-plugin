# wx-listen.ps1 — 纯监听模式（桥的 listen 模式别名，一体化守护进程）
# 桥与监听已整合为同一个守护进程（wx_bridge.py）：本脚本等价于
#   wx_bridge.py --mode listen —— 轮询指定聊天、记录 JSONL、关键词自动回复（不做 DSH 任务）。
# 在 DSH 中一般以后台任务运行：pwsh 工具 run_in_background 启动；--timeout 可限时。
# 示例：
#   wx-listen.ps1 -Who "文件传输助手,工作群" -Once -Json      # 单次检查
#   wx-listen.ps1 -Who "文件传输助手" -Timeout 3600           # 后台监听 1 小时
param(
    [string]$Who = '',
    [double]$Interval = 0,
    [string]$Log = '',
    [string]$ReplyRules = '',
    [switch]$Once,
    [int]$Timeout = 0,
    [switch]$Force,
    [switch]$Json,
    [switch]$Debug
)
. $PSScriptRoot\_common.ps1
$p = @('--mode', 'listen')
if ($Who) { $p += '--who'; $p += $Who }
if ($Interval -gt 0) { $p += '--interval'; $p += "$Interval" }
if ($Log) { $p += '--log'; $p += $Log }
if ($ReplyRules) { $p += '--reply-rules'; $p += $ReplyRules }
if ($Once) { $p += '--once' }
if ($Timeout -gt 0) { $p += '--timeout'; $p += "$Timeout" }
if ($Force) { $p += '--force' }
if ($Json) { $p += '--json' }
if ($Debug) { $p += '--debug' }
Invoke-WxScript -Name 'wx_bridge' -Arguments $p
