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
    $pyCmd = Get-Command py.exe -ErrorAction SilentlyContinue
    if (-not $pyCmd) { $pyCmd = Get-Command python.exe -ErrorAction SilentlyContinue }
    if (-not $pyCmd) { throw "未找到 Python（py / python）。请先安装 Python 3.9-3.12。" }

    $scriptPath = Join-Path $Script:WxScripts ($Name + '.py')
    if (-not (Test-Path $scriptPath)) { throw "脚本不存在：$scriptPath" }

    if ($pyCmd.Name -eq 'py.exe') {
        & $pyCmd.Source '-3' $scriptPath @Arguments
    } else {
        & $pyCmd.Source $scriptPath @Arguments
    }
    if ($Passthru) { return $LASTEXITCODE }
    exit $LASTEXITCODE
}
