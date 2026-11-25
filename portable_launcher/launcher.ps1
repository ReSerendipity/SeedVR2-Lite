#Requires -Version 5.1
<#
  SeedVR2-Turbo — 便携启动器（SCAFFOLD）
  ====================================================
  Mie-Package-Launcher 风格：所有路径都相对于本脚本自身解析，
  自动引导（bootstrap）一个本地 Python 环境，安装依赖，然后启动应用。
  设计目标：应用可以从 U 盘 / 任意路径直接运行，无需系统 Python。

  用法（Windows）：
    powershell -ExecutionPolicy Bypass -File launcher.ps1
    powershell -ExecutionPolicy Bypass -File launcher.ps1 -Size 7b -SkipDeps

  参数：
    -Size <3b|7b|7b_sharp>   仅用于权重缺失提示，默认 3b
    -SkipDeps                跳过 pip 安装（环境已就绪时提速）
    -SkipModelCheck          跳过权重文件存在性检查

  目录约定（脚本会自动探测 PKG_ROOT）：
    <pkg>/
    ├── launcher.ps1          # 本文件
    ├── app/clean_launch.py   # 应用入口（PKG_ROOT 锚点）
    ├── model/                # 模型权重目录（portable 模式）
    ├── venv/                 # 本地 venv（首次运行时创建）
    ├── python/               # 可选：内置便携 Python（优先于 venv）
    ├── wheels/               # 可选：离线 wheel 缓存（优先离线安装）
    ├── requirements.txt      # 应用依赖（或 requirements-lock.txt）
    └── config.yaml

  未决项（SCAFFOLD，需人工确认后落地）：
    * 确切权重路径：以 config.yaml 的 model.pretrained_dir=model 为准，
      文件名必须与 docs 一致（seedvr2_ema_3b_fp16.safetensors 等）。
    * 模型下载 URL：默认仓库 numz/SeedVR2_comfyUI；
      国内镜像用 HF_ENDPOINT=https://hf-mirror.com。
    * venv 可移植性：Windows venv 默认含绝对路径（pyvenv.cfg 的 home），
      移动到新盘符可能失效。本启动器用 `python -m venv --copies` 并以
      直接调用 <venv>/Scripts/python.exe 的方式规避激活脚本依赖；
      真正的「纯净可移动」推荐内置 python/（WinPython 路线）。
#>
param(
    [ValidateSet("3b", "7b", "7b_sharp")]
    [string]$Size = "3b",
    [switch]$SkipDeps,
    [switch]$SkipModelCheck
)

$ErrorActionPreference = 'Stop'

# ---- 1. 相对本脚本解析包根 PKG_ROOT ----
$SCRIPT_DIR = Split-Path -Parent $MyInvocation.MyCommand.Path
$PKG_ROOT   = $SCRIPT_DIR
# 若同目录没有 app/clean_launch.py，说明本脚本位于 <repo>/portable_launcher/，
# 则上溯一级以仓库根为 PKG_ROOT（便于在仓库内直接试运行）。
if (-not (Test-Path (Join-Path $PKG_ROOT "app/clean_launch.py"))) {
    $candidate = Split-Path -Parent $PKG_ROOT
    if (Test-Path (Join-Path $candidate "app/clean_launch.py")) {
        $PKG_ROOT = $candidate
    }
}
Write-Host "[launcher] Package root: $PKG_ROOT"

# ---- 2. 定位 Python 解释器 ----
function Find-Python {
    # 1) 内置便携 Python（最优先，完全隔离）
    $bundled = Join-Path $PKG_ROOT "python/python.exe"
    if (Test-Path $bundled) { return $bundled }
    # 2) 已存在的本地 venv
    $venvPy = Join-Path $PKG_ROOT "venv/Scripts/python.exe"
    if (Test-Path $venvPy) { return $venvPy }
    # 3) 系统 Python（仅用于首次引导创建 venv）
    $sys = Get-Command python -ErrorAction SilentlyContinue
    if ($sys) { return $sys.Source }
    return $null
}

$PYTHON = Find-Python
if (-not $PYTHON) {
    Write-Error "[launcher] 未找到 Python。请内置 python/ 目录，或先安装 Python 3.12+ 用于引导 venv。"
    exit 1
}
Write-Host "[launcher] Using Python: $PYTHON"

# ---- 3. 若不存在 venv，则创建（从系统 Python 引导） ----
$venvPy = Join-Path $PKG_ROOT "venv/Scripts/python.exe"
if (-not (Test-Path $venvPy)) {
    Write-Host "[launcher] Creating venv at $PKG_ROOT/venv (--copies) ..."
    & $PYTHON -m venv --copies (Join-Path $PKG_ROOT "venv")
    if ($LASTEXITCODE -ne 0) {
        Write-Error "[launcher] venv 创建失败"
        exit 1
    }
    $PYTHON = $venvPy
}

# ---- 4. 安装依赖 ----
if (-not $SkipDeps) {
    $wheels = Join-Path $PKG_ROOT "wheels"
    $reqs   = Join-Path $PKG_ROOT "requirements.txt"
    if (-not (Test-Path $reqs)) {
        $lock = Join-Path $PKG_ROOT "requirements-lock.txt"
        if (Test-Path $lock) { $reqs = $lock }
    }
    if (-not (Test-Path $reqs)) {
        Write-Warning "[launcher] 未找到 requirements.txt / requirements-lock.txt，跳过依赖安装"
    } else {
        & $PYTHON -m pip install --upgrade pip
        if (Test-Path $wheels) {
            Write-Host "[launcher] 从内置 wheels 离线安装..."
            & $PYTHON -m pip install --no-index --find-links $wheels -r $reqs
        } else {
            Write-Host "[launcher] 从 requirements 在线安装（需联网）..."
            & $PYTHON -m pip install -r $reqs
        }
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "[launcher] pip install 报告错误，请检查网络 / wheels"
        }
    }
}

# ---- 5. 设置环境变量（相对、可移植） ----
$env:KMP_DUPLICATE_LIB_OK = "TRUE"
$env:PYTHONPATH = $PKG_ROOT
$env:PYTORCH_CUDA_ALLOC_CONF = "expandable_segments:True"
$env:TORCHINDUCTOR_CACHE_DIR = Join-Path $PKG_ROOT ".torch_cache/inductor"
$env:PYTHONUTF8 = "1"

# ---- 6. 权重存在性检查（非致命） ----
if (-not $SkipModelCheck) {
    $modelDir = Join-Path $PKG_ROOT "model"
    $required = @("ema_vae_fp16.safetensors", "pos_emb.pt", "neg_emb.pt")
    if (-not (Test-Path $modelDir)) {
        Write-Warning "[launcher] 未找到 model/ 目录：$modelDir。请运行：python scripts/download_model.py --size $Size"
    } else {
        $missing = $required | Where-Object { -not (Test-Path (Join-Path $modelDir $_)) }
        if ($missing) {
            Write-Warning "[launcher] 缺少权重文件：$($missing -join ', ')"
        }
    }
}

# ---- 7. 启动应用 ----
Write-Host "[launcher] 正在启动，请在浏览器打开 http://127.0.0.1:7870 ..."
Set-Location $PKG_ROOT
& $PYTHON (Join-Path $PKG_ROOT "app/clean_launch.py")
exit $LASTEXITCODE
