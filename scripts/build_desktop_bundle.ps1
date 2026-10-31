#Requires -Version 5.1
# scripts/build_desktop_bundle.ps1
# 构建 SeedVR2-Lite 桌面版**瘦包**数据卷（供 scripts/installer/setup.nsi 分发）。
#
# 产物（默认 dist\desktop-release\）：
#   SeedVR2-Data.7z.001..NNN   数据卷：app/（应用代码 + 引导解释器，**不含 torch/权重**）
#   SHA256SUMS.txt             各分卷校验和
#   manifest.json              版本 / 分卷 / 体积元信息
#   upload-list.txt            待上传文件清单
#
# 数据卷解压后布局（与壳 resolve_app_dir/resolve_runtime_dir、后端 config.PROJECT_ROOT
# 一一对应，改动前先看这三处）：
#   <InstallDir>\app\
#     start_portable.py       壳启动入口（打包态必须在 app/ 根）
#     config.yaml             配置（后端 PROJECT_ROOT 解析到 app/ 这一级）
#     app\integrated_app\...  Python 包（注意 **app/app 两层**）
#     scripts\download_model.py 等随包脚本
#     bootstrap\python.exe    引导解释器（WinPython，仅非 torch 依赖）
#     data\ logs\ outputs\    运行态目录
#
# 用法：
#   .\scripts\build_desktop_bundle.ps1                          # 自动准备解释器（下载 WinPython）
#   .\scripts\build_desktop_bundle.ps1 -RuntimeDir D:\WPy64-312101 -SkipDeps
#   .\scripts\build_desktop_bundle.ps1 -OutDir D:\out -MaxPartBytes 1900MB

[CmdletBinding()]
param(
    [string]$Version = '',
    [string]$Root = '',
    [string]$OutDir = '',
    [string]$StagingDir = '',

    # 已准备好的解释器目录（含 python.exe；可为 WinPython 根或扁平化的 python 目录）
    [string]$RuntimeDir = '',
    [string]$WinPythonUrl = 'https://github.com/winpython/winpython/releases/download/16.5.20250614/Winpython64-3.12.10.1dotb4.exe',
    [switch]$SkipDeps,

    [string]$MaxPartBytes = '1900MB',
    [int]$Level = 1,
    [switch]$KeepStaging,
    [switch]$SkipSizeGate
)

$ErrorActionPreference = 'Stop'

function Write-Step([string]$Text) { Write-Host "`n$Text" -ForegroundColor Yellow }
function Format-Size([long]$Bytes) {
    if ($Bytes -ge 1GB) { return ('{0:N1} GB' -f ($Bytes / 1GB)) }
    if ($Bytes -ge 1MB) { return ('{0:N1} MB' -f ($Bytes / 1MB)) }
    return ('{0:N0} KB' -f ($Bytes / 1KB))
}
function Convert-ToBytes([string]$Text) {
    if ($Text -match '^\s*(\d+(?:\.\d+)?)\s*(MB|GB|B)?\s*$') {
        $value = [double]$Matches[1]
        switch ($Matches[2]) {
            'GB' { return [long]($value * 1GB) }
            'MB' { return [long]($value * 1MB) }
            default { return [long]$value }
        }
    }
    return [long]$Text
}
function Remove-TreeFast([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return }
    $full = (Resolve-Path -LiteralPath $Path).Path
    if ($full.Length -lt 12) { throw "拒绝删除可疑路径：$full" }
    & cmd /c "rmdir /s /q `"$full`"" 2>$null | Out-Null
}
function Copy-TreeBulk([string]$Source, [string]$Dest, [string[]]$ExcludeDirs = @(), [string[]]$ExcludeFiles = @()) {
    if (-not (Test-Path -LiteralPath $Source)) { throw "源不存在：$Source" }
    Remove-TreeFast $Dest
    New-Item -ItemType Directory -Path $Dest -Force | Out-Null
    $args = @($Source, $Dest, '/E', '/NFL', '/NDL', '/NJH', '/NJS', '/NP', '/R:1', '/W:1')
    if ($ExcludeDirs.Count -gt 0) { $args += '/XD'; $args += $ExcludeDirs }
    if ($ExcludeFiles.Count -gt 0) { $args += '/XF'; $args += $ExcludeFiles }
    $out = & robocopy @args 2>&1
    if ($LASTEXITCODE -ge 8) { throw "robocopy 失败（$LASTEXITCODE）：$($out | Select-Object -Last 5)" }
}
function Get-TreeStats([string]$Path) {
    $files = @(Get-ChildItem -LiteralPath $Path -Recurse -File -Force -ErrorAction SilentlyContinue)
    $bytes = [long]0
    foreach ($f in $files) { $bytes += $f.Length }
    return [pscustomobject]@{ Files = $files.Count; Bytes = $bytes }
}
function Find-PythonExe([string]$Dir) {
    foreach ($candidate in @(
            (Join-Path $Dir 'python.exe'),
            (Join-Path $Dir 'python\python.exe'))) {
        if (Test-Path -LiteralPath $candidate) { return $candidate }
    }
    $found = @(Get-ChildItem -LiteralPath $Dir -Recurse -Filter 'python.exe' -File -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -match '\\python[^\\]*\\python\.exe$' -or $_.DirectoryName -eq $Dir } |
        Select-Object -First 1)
    if ($found.Count -gt 0) { return $found[0].FullName }
    throw "在 $Dir 下找不到 python.exe"
}

# ---------------------------------------------------------------- 路径 ----
if (-not $Root) { $Root = Split-Path -Parent $PSScriptRoot }
$Root = (Resolve-Path -LiteralPath $Root).Path
if (-not $OutDir) { $OutDir = Join-Path $Root 'dist\desktop-release' }
if (-not $StagingDir) { $StagingDir = Join-Path $Root 'dist\desktop-staging' }
New-Item -ItemType Directory -Path $OutDir -Force | Out-Null
New-Item -ItemType Directory -Path $StagingDir -Force | Out-Null

if (-not $Version) {
    $vfile = Join-Path $Root 'version.json'
    if (Test-Path -LiteralPath $vfile) { $Version = (Get-Content -LiteralPath $vfile -Raw | ConvertFrom-Json).version }
    if (-not $Version) { throw '无法从 version.json 解析版本号，请显式传 -Version' }
}

Write-Host "=============================================="
Write-Host " SeedVR2-Lite 瘦包构建 v$Version"
Write-Host " 产物目录：$OutDir"
Write-Host "=============================================="

$payload = Join-Path $StagingDir 'payload'
$appDir = Join-Path $payload 'app'
Remove-TreeFast $payload
New-Item -ItemType Directory -Path $appDir -Force | Out-Null

# ------------------------------------------------------------ 应用代码 ----
Write-Step '[打包] 应用代码'
$codeDirs = @('app', 'scripts')
$excludeDirs = @('__pycache__', '.pytest_cache', '.mypy_cache', '.ruff_cache', 'tests', 'model',
    'dist', 'build', '.git', 'desktop', 'outputs', 'logs', '.venv', 'node_modules', '.torch_cache',
    # scripts\installer 里有历史安装器 exe（26 MB），绝不能随包分发
    'installer')
$excludeFiles = @('*.pyc', '*.pyo', '*.log', '*.db', '*.db-wal', '*.db-shm', '*.bak', '*.pem')
foreach ($d in $codeDirs) {
    $src = Join-Path $Root $d
    if (-not (Test-Path -LiteralPath $src)) { Write-Warning "跳过不存在的目录 $d"; continue }
    # Python 包必须落在 app/app/：后端 config.PROJECT_ROOT 上溯三级正好是 app/
    $dest = if ($d -eq 'app') { Join-Path $appDir 'app' } else { Join-Path $appDir $d }
    Copy-TreeBulk -Source $src -Dest $dest -ExcludeDirs $excludeDirs -ExcludeFiles $excludeFiles
    $st = Get-TreeStats $dest
    Write-Host ("    · {0,-10} {1,6} 文件 / {2}" -f $d, $st.Files, (Format-Size $st.Bytes))
}
foreach ($f in @('start_portable.py')) {
    $src = Join-Path (Join-Path $Root 'scripts') $f
    if (Test-Path -LiteralPath $src) { Copy-Item -LiteralPath $src -Destination (Join-Path $appDir $f) -Force }
}
# pyproject.toml 必须跟包：app/integrated_app/version.py 的版本来源是
# `parents[2]/pyproject.toml`，在安装态 parents[2] 正好是 <安装根>\app，少了它
# 后端会把 app_version 报成 unknown（下载页、关于页都会显示 unknown）。
# downloads_catalog.json 同理：后端 _catalog_path() = PROJECT_ROOT/downloads_catalog.json
# （安装态 = <安装根>\app），必须落在数据卷根；源码态种子在仓库 app\ 下。
foreach ($f in @('config.yaml', 'version.json', 'pyproject.toml', 'README.md', 'LICENSE', 'NOTICE', 'CHANGELOG.md', 'requirements.txt')) {
    $src = Join-Path $Root $f
    if (Test-Path -LiteralPath $src) { Copy-Item -LiteralPath $src -Destination $appDir -Force }
}
# downloads_catalog.json 的种子在仓库 app\ 下（与后端源码态 PROJECT_ROOT 同级），
# 但要拷到数据卷根（安装态 PROJECT_ROOT 这一级），不能随 app 包多套一层。
$catalogSeed = Join-Path $Root 'app\downloads_catalog.json'
if (Test-Path -LiteralPath $catalogSeed) {
    Copy-Item -LiteralPath $catalogSeed -Destination (Join-Path $appDir 'downloads_catalog.json') -Force
}
# 完整性公钥必须进卷：上面 ExcludeFiles 风格的 *.pem 排除（若走 robocopy /XF）会把
# manifest_signing_public_key.pem 一并剔掉 → 自检无法验签（Image 仓 enforce 下拒绝启动）。
# 私钥是 dotfile（.manifest_signing_key 等），仍被排除。
$pubKey = Join-Path $Root 'app\integrated_app\security\manifest_signing_public_key.pem'
if (Test-Path -LiteralPath $pubKey) {
    New-Item -ItemType Directory -Path (Join-Path $appDir 'app\integrated_app\security') -Force | Out-Null
    Copy-Item -LiteralPath $pubKey -Destination (Join-Path $appDir 'app\integrated_app\security\manifest_signing_public_key.pem') -Force
}
foreach ($d in @('data', 'logs', 'outputs')) {
    New-Item -ItemType Directory -Path (Join-Path $appDir $d) -Force | Out-Null
}

if (-not (Test-Path -LiteralPath (Join-Path $appDir 'start_portable.py'))) { throw '缺少 start_portable.py' }
if (-not (Test-Path -LiteralPath (Join-Path $appDir 'app\integrated_app\app_server.py'))) { throw '缺少 app\integrated_app\app_server.py' }

# ------------------------------------------------------------ 引导解释器 ----
Write-Step '[打包] 引导解释器（bootstrap/）'
$bootstrapDir = Join-Path $appDir 'bootstrap'

if (-not $RuntimeDir) {
    $prep = Join-Path $StagingDir '_prepare'
    New-Item -ItemType Directory -Path $prep -Force | Out-Null
    $installer = Join-Path $prep 'winpython.exe'
    if (-not (Test-Path -LiteralPath $installer)) {
        Write-Host '    下载 WinPython ...'
        Invoke-WebRequest -Uri $WinPythonUrl -OutFile $installer -UseBasicParsing
    }
    Write-Host '    解压 WinPython ...'
    & $installer /S /D=$prep | Out-Null
    $wpRoot = Get-ChildItem -LiteralPath $prep -Directory -Filter 'WPy64-*' | Select-Object -First 1
    if (-not $wpRoot) { throw 'WinPython 解压后未找到 WPy64-* 目录' }
    $RuntimeDir = $wpRoot.FullName
}

$pyExe = Find-PythonExe $RuntimeDir
$pyHome = Split-Path -Parent $pyExe
Write-Host "    解释器：$pyExe"
Copy-TreeBulk -Source $pyHome -Dest $bootstrapDir `
    -ExcludeDirs @('notebooks', 'apps', 'dev', 'settings', 'data') `
    -ExcludeFiles @('pip-log.txt', '*.typecheck.log', 'WINPYTHON_*', 'qt.conf')
$pyExe = Join-Path $bootstrapDir 'python.exe'
if (-not (Test-Path -LiteralPath $pyExe)) { throw "bootstrap 复制后缺少 python.exe：$pyExe" }

# 剔除 WinPython 完整版自带的 CPU 版 torch：瘦包本就不该带，而且会让
# 「是否已装环境」判定失真（壳看 RUNTIME.json，但用户看到的是「装了却用不了 CUDA」）。
$sitePackages = Join-Path $bootstrapDir 'Lib\site-packages'
if (Test-Path -LiteralPath $sitePackages) {
    foreach ($pkg in @('torch', 'torchvision', 'torchaudio', 'torchgen')) {
        $pkgDir = Join-Path $sitePackages $pkg
        if (Test-Path -LiteralPath $pkgDir) {
            Remove-TreeFast $pkgDir
            Write-Host "    剔除自带 $pkg（CPU 版，非本仓要求的 CUDA 版）"
        }
        Get-ChildItem -LiteralPath $sitePackages -Directory -Filter "$pkg-*.dist-info" -ErrorAction SilentlyContinue |
            ForEach-Object { Remove-TreeFast $_.FullName }
    }
}

# ------------------------------------------------------------ 依赖安装 ----
if (-not $SkipDeps) {
    Write-Step '[打包] 安装非 torch 依赖（装进 bootstrap 自身）'
    $reqSrc = Join-Path $Root 'requirements.txt'
    if (-not (Test-Path -LiteralPath $reqSrc)) { throw "缺少 requirements.txt：$reqSrc" }
    $reqNoTorch = Join-Path $StagingDir 'requirements-notorch.txt'
    Get-Content -LiteralPath $reqSrc | Where-Object {
        $_ -notmatch '^\s*(torch|torchvision|torchaudio)\b' -and $_ -notmatch '^\s*#?\s*torch'
    } | Set-Content -LiteralPath $reqNoTorch -Encoding utf8
    # 用变量接住输出：管道会把 $LASTEXITCODE 冲掉，失败时只剩一句无信息的 throw
    $pipOut = & $pyExe -m pip install --disable-pip-version-check -q --no-warn-script-location -r $reqNoTorch 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw ("依赖安装失败（退出码 {0}）：`n{1}" -f $LASTEXITCODE, (($pipOut | Select-Object -Last 12) -join "`n"))
    }
} else {
    Write-Host '    [跳过] 依赖安装（-SkipDeps）'
}

$bootStats = Get-TreeStats $bootstrapDir
Write-Host ("    · bootstrap {0,6} 文件 / {1}" -f $bootStats.Files, (Format-Size $bootStats.Bytes))

# ---------------------------------------------------------------- 归档 ----
Write-Step '[归档] 7z 分卷'
$sevenZip = @(
    'C:\Program Files\7-Zip\7z.exe',
    'C:\Program Files (x86)\7-Zip\7z.exe'
) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $sevenZip) {
    $cmd = Get-Command 7z -ErrorAction SilentlyContinue
    if ($cmd) { $sevenZip = $cmd.Source }
}
if (-not $sevenZip) { throw '未找到 7z.exe，请安装 7-Zip' }

$maxBytes = Convert-ToBytes $MaxPartBytes
$archiveBase = Join-Path $OutDir 'SeedVR2-Data.7z'
# PS 5.1 里空管道调 Remove-Item 会报 'missing path operand'，先收进数组再逐个删
$stale = @(Get-ChildItem -LiteralPath $OutDir -Filter 'SeedVR2-Data.7z.*' -File -ErrorAction SilentlyContinue)
foreach ($f in $stale) { Remove-Item -LiteralPath $f.FullName -Force }
Push-Location $payload
try {
    & $sevenZip a -t7z "-mx=$Level" "-v${maxBytes}b" $archiveBase 'app' -bso0 -bsp0 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "7z 打包失败（$LASTEXITCODE）" }
} finally {
    Pop-Location
}

$volumes = @(Get-ChildItem -LiteralPath $OutDir -Filter 'SeedVR2-Data.7z.*' -File | Sort-Object Name)
if ($volumes.Count -eq 0) { throw '未生成任何分卷' }

$totalBytes = [long]0
$volMeta = @()
foreach ($v in $volumes) {
    if (-not $SkipSizeGate -and $v.Length -gt 2GB) { throw "分卷 $($v.Name) 超过 2 GB" }
    $hash = (Get-FileHash -LiteralPath $v.FullName -Algorithm SHA256).Hash.ToLower()
    $totalBytes += $v.Length
    $volMeta += [pscustomobject]@{ index = $volumes.IndexOf($v) + 1; file = $v.Name; bytes = $v.Length; sha256 = $hash }
    Write-Host ("    · {0} {1}" -f $v.Name, (Format-Size $v.Length))
}

$sumLines = $volMeta | ForEach-Object { "$($_.sha256)  $($_.file)" }
Set-Content -LiteralPath (Join-Path $OutDir 'SHA256SUMS.txt') -Value $sumLines -Encoding ascii

$manifest = [pscustomobject]@{
    schema            = 1
    product           = 'SeedVR2-Lite-Desktop'
    app_version       = $Version
    arch              = 'win-x64'
    created_utc       = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    max_part_bytes    = $maxBytes
    installer         = "SeedVR2-Setup-v$Version.exe"
    data_archive      = 'SeedVR2-Data.7z'
    unpack_note       = '数据分卷必须与安装器同目录；安装器用内嵌 7za 解 SeedVR2-Data.7z.001 到安装目录'
    bootstrap_files   = $bootStats.Files
    payload_files     = (Get-TreeStats $appDir).Files
    payload_bytes     = (Get-TreeStats $appDir).Bytes
    data_volumes      = $volMeta
}
$manifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $OutDir 'manifest.json') -Encoding utf8

$upload = @($volumes | ForEach-Object { $_.FullName })
$upload += (Join-Path $OutDir 'SHA256SUMS.txt')
$upload += (Join-Path $OutDir 'manifest.json')
Set-Content -LiteralPath (Join-Path $OutDir 'upload-list.txt') -Value $upload -Encoding utf8

if (-not $KeepStaging) { Remove-TreeFast $payload }

Write-Host ''
Write-Host ("[完成] 产物：{0} 个分卷 / 合计 {1}" -f $volumes.Count, (Format-Size $totalBytes)) -ForegroundColor Green
Write-Host '[下一步] 编译安装器：把独立版 7za.exe（单文件、不依赖 7z.dll，可从 TTS-MultiModel 仓的' -ForegroundColor Green
Write-Host '          scripts\installer\7za.exe 复制）放到 scripts\installer\ 下，然后执行：' -ForegroundColor Green
Write-Host ("          cd scripts\installer; makensis -DAPP_VERSION=$Version setup.nsi") -ForegroundColor Green
Write-Host '[注意] -DSRC_7ZA 绝不能指向 7z.exe（它依赖同目录 7z.dll，嵌入安装器后解压会静默失败）' -ForegroundColor Yellow
