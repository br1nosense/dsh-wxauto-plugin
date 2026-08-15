# wx-send.ps1 — 向指定聊天窗口发送任务进度/完成信息（文本或文件）
# 示例：
#   wx-send.ps1 -Who "文件传输助手" -Msg "任务已完成 ✅"
#   wx-send.ps1 -Who "@config" -MsgFile "C:\tmp\report.txt"
#   wx-send.ps1 -Who "张三,工作群" -File "C:\tmp\report.pdf"
param(
    [string]$Who = '@config',
    [string]$Msg = '',
    [string]$MsgFile = '',
    [string[]]$File = @(),
    [string]$At = '',
    [switch]$NoExact,
    [switch]$Json,
    [switch]$Debug
)
. $PSScriptRoot\_common.ps1
$p = @('--who', $Who)
if ($Msg) { $p += '--msg'; $p += $Msg }
if ($MsgFile) { $p += '--msg-file'; $p += $MsgFile }
foreach ($f in $File) { $p += '--file'; $p += $f }
if ($At) { $p += '--at'; $p += $At }
if ($NoExact) { $p += '--no-exact' }
if ($Json) { $p += '--json' }
if ($Debug) { $p += '--debug' }
Invoke-WxScript -Name 'wx_send' -Arguments $p
