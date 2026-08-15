# dsh-task.ps1 — 让 DSH 在当前会话执行任务（-Wait 等待完成并输出结果）
param([string]$Task = '', [string]$Session = '', [switch]$Wait, [int]$Timeout = 600, [switch]$Json, [switch]$Debug)
. $PSScriptRoot\_common.ps1
$p = @('task', $Task)
if ($Session) { $p += '--session'; $p += $Session }
if ($Wait) { $p += '--wait' }
$p += '--timeout'; $p += "$Timeout"
if ($Json) { $p += '--json' }
if ($Debug) { $p += '--debug' }
Invoke-WxScript -Name 'dsh_ops' -Arguments $p
