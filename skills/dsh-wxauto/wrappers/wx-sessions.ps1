# wx-sessions.ps1 — 列出微信会话（确认要向哪个聊天窗口发送/监听）
param(
    [string]$Match = '',
    [int]$Limit = 50,
    [switch]$OnlyNew,
    [switch]$Json,
    [switch]$Debug
)
. $PSScriptRoot\_common.ps1
$p = @('--limit', "$Limit")
if ($Match) { $p += '--match'; $p += $Match }
if ($OnlyNew) { $p += '--only-new' }
if ($Json) { $p += '--json' }
if ($Debug) { $p += '--debug' }
Invoke-WxScript -Name 'wx_sessions' -Arguments $p
