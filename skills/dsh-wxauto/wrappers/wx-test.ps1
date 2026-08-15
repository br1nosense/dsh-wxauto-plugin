# wx-test.ps1 — 环境预检（Python / wxauto4 / 微信客户端 / 登录状态）
param(
    [switch]$Json,
    [switch]$Debug
)
. $PSScriptRoot\_common.ps1
$p = @()
if ($Json) { $p += '--json' }
if ($Debug) { $p += '--debug' }
Invoke-WxScript -Name 'wx_test' -Arguments $p
