# wx-bridge.ps1 — 微信⇄DSH 双向桥（后台常驻，带看护自动重启）
# 监听控制聊天，/ 命令或普通消息 → 操作/驱动 DSH；任务完成自动推送结果+进度截图。
# 看护：python 桥进程意外退出（COM/UIA 致命错误等）时自动重启，配合桥的启动预读不会重复处理消息。
# 示例：
#   wx-bridge.ps1 -Who "文件传输助手" -AllowSelf -Once          # 自测：处理一次
#   wx-bridge.ps1 -Who "我的控制群" -Timeout 86400               # 后台常驻 1 天
#   wx-bridge.ps1 -Who "文件传输助手" -AllowSelf -Timeout 3600   # 用文件传输助手自测 1 小时
param(
    [string]$Who = '',
    [double]$Interval = 0,
    [int]$Timeout = 0,
    [switch]$Once,
    [switch]$AllowSelf,
    [switch]$Force,       # 忽略设置开关强行启动（python --force）
    [switch]$Json,
    [switch]$Debug,
    [switch]$NoSupervise   # 关闭看护（单次运行不自动重启）
)
. $PSScriptRoot\_common.ps1
$p = @()
if ($Who) { $p += '--who'; $p += $Who }
if ($Interval -gt 0) { $p += '--interval'; $p += "$Interval" }
if ($Timeout -gt 0) { $p += '--timeout'; $p += "$Timeout" }
if ($Once) { $p += '--once' }
if ($AllowSelf) { $p += '--allow-self' }
if ($Force) { $p += '--force' }
if ($Json) { $p += '--json' }
if ($Debug) { $p += '--debug' }

if ($Once -or $NoSupervise) {
    Invoke-WxScript -Name 'wx_bridge' -Arguments $p
    exit $LASTEXITCODE
}

# ── 启动前预检：开关未开启则直接退出（不进入重启循环）──
$pyProbe = "import sys; sys.path.insert(0, r'$($Script:WxScripts)'); import wx_common; c=wx_common.load_config(); print('ENABLED=' + str(bool(c.get('enabled') or c.get('bridge_enabled') or c.get('listen_enabled'))).lower())"
$pyCmd2 = Get-Command py.exe -ErrorAction SilentlyContinue
if (-not $pyCmd2) { $pyCmd2 = Get-Command python.exe -ErrorAction SilentlyContinue }
function Get-WxEnabled {
    $out = "false"
    if ($pyCmd2) {
        $probeOut = & $pyCmd2.Source '-3' '-c' $pyProbe 2>$null | Select-Object -Last 1
        if ($probeOut -match 'ENABLED=(true|false)') { $out = $Matches[1] }
    }
    return $out
}
if ((Get-WxEnabled) -ne 'true') {
    Write-Host "⚠️ 总开关未开启（wxauto.enabled=false）。请在 DSH 设置页「微信自动化」打开总开关后重试；或用 -Force 临时启动。" -ForegroundColor Yellow
    exit 0
}

# ── 看护循环：进程意外退出（非 0 / 非中断）→ 退避后自动重启（10s→30s→60s）──
# 每次重启前复查总开关：若开关已被关闭（插件灭杀了进程），直接退出，不复活。
$attempt = 0
while ($true) {
    $code = Invoke-WxScript -Name 'wx_bridge' -Arguments $p -Passthru
    if ($code -eq 0 -or $code -eq 130) {
        Write-Host "[wx-bridge] 守护进程正常退出（code=$code）。"
        break
    }
    if ((Get-WxEnabled) -ne 'true') {
        Write-Host "[wx-bridge] 总开关已关闭，不再重启。"
        break
    }
    $attempt++
    $delay = [Math]::Min(60, 10 * [Math]::Pow(2, [Math]::Min(2, $attempt - 1)))
    Write-Host "[wx-bridge] ⚠️ 守护进程异常退出（code=$code），${delay} 秒后自动重启（第 $attempt 次）。如微信未打开，打开微信即可恢复。" -ForegroundColor Yellow
    Start-Sleep -Seconds $delay
}
