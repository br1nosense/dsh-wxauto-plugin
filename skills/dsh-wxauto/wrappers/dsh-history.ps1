# dsh-history.ps1 — 查看当前 DSH 会话最近对话
param([string]$Session = '', [int]$Count = 20, [switch]$Json, [switch]$Debug)
. $PSScriptRoot\_common.ps1
$p = @('history', '--count', "$Count")
if ($Session) { $p += '--session'; $p += $Session }
if ($Json) { $p += '--json' }
if ($Debug) { $p += '--debug' }
Invoke-WxScript -Name 'dsh_ops' -Arguments $p
