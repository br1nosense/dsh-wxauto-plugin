# dsh-shot.ps1 — 生成 DSH 进度截图（PNG 进度卡片），打印图片路径
param([string]$Session = '', [switch]$Json, [switch]$Debug)
. $PSScriptRoot\_common.ps1
$p = @('shot')
if ($Session) { $p += '--session'; $p += $Session }
if ($Json) { $p += '--json' }
if ($Debug) { $p += '--debug' }
Invoke-WxScript -Name 'dsh_ops' -Arguments $p
