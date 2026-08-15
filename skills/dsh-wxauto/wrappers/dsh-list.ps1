# dsh-list.ps1 — 列出 DSH 会话
param([switch]$Json, [switch]$Debug)
. $PSScriptRoot\_common.ps1
$p = @('list')
if ($Json) { $p += '--json' }
if ($Debug) { $p += '--debug' }
Invoke-WxScript -Name 'dsh_ops' -Arguments $p
