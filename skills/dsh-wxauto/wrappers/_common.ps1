# _common.ps1 — dsh-wxauto 封装的公共逻辑
# 用法：在每个 wrapper 中先 `. $PSScriptRoot\_common.ps1`，再调用 Invoke-WxScript。
$ErrorActionPreference = 'Stop'
$Script:WxRoot = Split-Path -Parent $PSScriptRoot   # wrappers 上一级 = skill 根目录
$Script:WxScripts = Join-Path $Script:WxRoot 'scripts'

# 让控制台按 UTF-8 输出中文
try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $OutputEncoding = [System.Text.Encoding]::UTF8
} catch { }

function Invoke-WxScript {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [string[]]$Arguments = @(),
        [switch]$Passthru   # 不 exit，返回退出码（供看护循环使用）
    )
    $scriptPath = Join-Path $Script:WxScripts ($Name + '.py')
    if (-not (Test-Path $scriptPath)) { throw "脚本不存在：$scriptPath" }

    # 优先使用 DSH_WX_PYTHON 指定的解释器（与插件 lib/index.js 的解析一致）；
    # 未设置时回退 py -3 / python。Windows 上 py -3 会选到最新 3.x，若该版本
    # 不受 wxauto4 支持（要求 3.9-3.12），请用 DSH_WX_PYTHON 指定（如 3.11 venv）。
    $exe = $null
    $preArgs = @()
    if ($env:DSH_WX_PYTHON) {
        $parts = $env:DSH_WX_PYTHON.Trim().Split(' ', [System.StringSplitOptions]::RemoveEmptyEntries)
        $exe = $parts[0]
        if ($parts.Length -gt 1) { $preArgs = $parts[1..($parts.Length - 1)] }
    } else {
        $pyCmd = Get-Command py.exe -ErrorAction SilentlyContinue
        if (-not $pyCmd) { $pyCmd = Get-Command python.exe -ErrorAction SilentlyContinue }
        if (-not $pyCmd) { throw "未找到 Python（py / python）。请先安装 Python 3.9-3.12。" }
        $exe = $pyCmd.Source
        if ($pyCmd.Name -eq 'py.exe') { $preArgs = @('-3') }
    }

    & $exe @preArgs $scriptPath @Arguments
    if ($Passthru) { return $LASTEXITCODE }
    exit $LASTEXITCODE
}
