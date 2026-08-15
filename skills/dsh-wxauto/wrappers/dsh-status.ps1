# dsh-status.ps1 — 查看当前 DSH 会话进度（标题/状态/统计/任务清单）
param([string]$Session = '', [switch]$Json, [switch]$Debug)
. $PSScriptRoot\_common.ps1
$p = @('status')
if ($Session) { $p += '--session'; $p += $Session }
if ($Json) { $p += '--json' }
if ($Debug) { $p += '--debug' }
Invoke-WxScript -Name 'dsh_ops' -Arguments $p
