# install.ps1 — 把 dsh-wxauto 安装为 DSH 插件（profile bundle）
param(
    [switch]$Junction,   # 旧方式：仅把技能链接进 ~/.dsh/skills，不装 bundle
    [string]$Profile = 'web'
)
$ErrorActionPreference = 'Stop'

$src = Join-Path $PSScriptRoot 'skills\dsh-wxauto'
$dst = Join-Path $env:USERPROFILE '.dsh\skills\dsh-wxauto'

if (-not (Test-Path (Join-Path $src 'SKILL.md'))) { throw "未找到技能源码：$src" }

# 先清理旧 junction（避免技能同时由文件系统与插件运行时注册）
if (Test-Path $dst) {
    $item = Get-Item $dst -Force -ErrorAction SilentlyContinue
    if ($item -and $item.LinkType) {
        (Get-Item $dst).Delete()
        Write-Host "已移除旧 junction：$dst"
    } elseif ($item) {
        Write-Host "保留已有目录 $dst（非链接）。如由插件注册，可手动删除以避免重复。"
    }
}

if ($Junction) {
    # 旧方式：仅注册技能目录
    New-Item -ItemType Junction -Path $dst -Target $src | Out-Null
    Write-Host "已创建技能联接：$dst -> $src"
    Write-Host "完成（技能方式）。"
    exit 0
}

# 新方式：安装为 DSH 插件（bundle）
Write-Host "通过 dsh plugin 安装到 profile '$Profile' ..."
dsh plugin --profile $Profile add $PSScriptRoot
if ($LASTEXITCODE -ne 0) { throw "dsh plugin 安装失败（exit $LASTEXITCODE）" }

# 让 DSH 设置 API/设置页暴露 `wxauto` 命名空间：给 apiproxy 的
# WEB_SETTINGS_NAMESPACES 加上 wxauto（幂等；dsh 升级后该文件被覆盖需重跑本步骤）。
# profile node_modules 里的 apiproxy 是 junction，指向全局 dsh 安装的真实文件。
function Find-ApiproxyPath {
    param([string]$Profile)
    $cands = @()
    # 1) profile 内 junction
    $cands += Join-Path $env:USERPROFILE ".dsh\profiles\$Profile\node_modules\@deepseek-ai\dsh-host-apiproxy"
    # 2) 全局 npm root
    try {
        $g = (npm root -g 2>$null).Trim()
        if ($g) {
            $cands += Join-Path $g "@deepseek-ai\dsh\node_modules\@deepseek-ai\dsh-host-apiproxy"
            $cands += Join-Path $g "@deepseek-ai\dsh-host-apiproxy"
        }
    } catch { }
    # 3) nvm / 用户目录式全局安装（扫描各版本目录）
    foreach ($root in @("$env:LOCALAPPDATA\nvm", "$env:APPDATA\nvm", "$env:USERPROFILE\.nvm")) {
        if (Test-Path $root) {
            Get-ChildItem $root -Directory -ErrorAction SilentlyContinue | ForEach-Object {
                $cands += Join-Path $_.FullName "node_modules\@deepseek-ai\dsh\node_modules\@deepseek-ai\dsh-host-apiproxy"
            }
        }
    }
    foreach ($c in $cands) { if ($c -and (Test-Path $c)) { return $c } }
    return $null
}

Write-Host "确保 apiproxy 暴露 wxauto 设置命名空间 ..."
$apiproxyLink = Find-ApiproxyPath -Profile $Profile
$apiproxyFile = Join-Path $apiproxyLink 'lib\index.js'
if (Test-Path $apiproxyFile) {
    $text = [System.IO.File]::ReadAllText($apiproxyFile, [System.Text.Encoding]::UTF8)
    $anchor = '"web-search-deepseek"'
    if ($text.Contains('"wxauto"')) {
        Write-Host "apiproxy 已暴露 wxauto（无需修改）。"
    } elseif ($text.Contains($anchor)) {
        $text = $text.Replace("$anchor`n]", "$anchor,`n`t`"wxauto`"`n]").Replace("$anchor`r`n]", "$anchor,`r`n`t`"wxauto`"`r`n]")
        [System.IO.File]::WriteAllText($apiproxyFile, $text, [System.Text.UTF8Encoding]::new($false))
        Write-Host "已为 apiproxy 添加 wxauto 命名空间暴露：$apiproxyFile"
    } else {
        Write-Host "警告：apiproxy 结构不识别，请手动把 wxauto 加入 WEB_SETTINGS_NAMESPACES。"
    }
} else {
    Write-Host "警告：未找到 apiproxy 文件，设置页将不显示 wxauto 卡片（设置仍可写 settings.yaml 的 wxauto: 段）。"
}

Write-Host ""
Write-Host "✅ 插件已安装到 profile '$Profile'。"
Write-Host "请【重启 dsh web】（退出当前 dsh web 进程后重新 dsh web）以生效。"
Write-Host "验证：dsh --profile $Profile --dump-config 应出现 id: wxauto 的行；"
Write-Host "      设置页 → 插件 应出现「微信自动化」卡片（含双向桥开关）。"
