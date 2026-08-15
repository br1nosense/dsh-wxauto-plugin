# dsh-new.ps1 — 新建 DSH 会话（自动设为当前）
param([string]$Cwd = '', [switch]$Json, [switch]$Debug)
. $PSScriptRoot\_common.ps1
$p = @('new')
if ($Cwd) { $p += '--cwd'; $p += $Cwd }
if ($Json) { $p += '--json' }
if ($Debug) { $p += '--debug' }
Invoke-WxScript -Name 'dsh_ops' -Arguments $p
