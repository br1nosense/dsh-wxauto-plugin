# dsh-active.ps1 — 显示当前 DSH 会话
param([switch]$Json, [switch]$Debug)
. $PSScriptRoot\_common.ps1
$p = @('active')
if ($Json) { $p += '--json' }
if ($Debug) { $p += '--debug' }
Invoke-WxScript -Name 'dsh_ops' -Arguments $p
