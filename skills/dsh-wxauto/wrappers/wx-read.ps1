# wx-read.ps1 — 读取指定聊天窗口最近消息
param(
    [string]$Who = '',
    [int]$Count = 20,
    [switch]$Json,
    [switch]$Debug
)
. $PSScriptRoot\_common.ps1
$p = @('--who', $Who, '--count', "$Count")
if ($Json) { $p += '--json' }
if ($Debug) { $p += '--debug' }
Invoke-WxScript -Name 'wx_read' -Arguments $p
