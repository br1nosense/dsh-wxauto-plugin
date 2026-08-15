# dsh-switch.ps1 — 切换当前 DSH 会话（按 /list 的序号或 sessionId 前缀）
param([string]$Target = '', [switch]$Json, [switch]$Debug)
. $PSScriptRoot\_common.ps1
$p = @('switch', $Target)
if ($Json) { $p += '--json' }
if ($Debug) { $p += '--debug' }
Invoke-WxScript -Name 'dsh_ops' -Arguments $p
