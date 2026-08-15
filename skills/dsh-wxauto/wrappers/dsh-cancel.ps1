# dsh-cancel.ps1 — 取消当前 DSH 会话的运行回合
param([string]$Session = '', [switch]$Json, [switch]$Debug)
. $PSScriptRoot\_common.ps1
$p = @('cancel')
if ($Session) { $p += '--session'; $p += $Session }
if ($Json) { $p += '--json' }
if ($Debug) { $p += '--debug' }
Invoke-WxScript -Name 'dsh_ops' -Arguments $p
