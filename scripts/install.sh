#!/usr/bin/env bash
# SeedVR2 Video Restoration Toolbox - Setup (Linux/macOS)
# Equivalent of install.bat for Unix-like systems

set -e

echo "============================================"
echo "  SeedVR2 Video Restoration Toolbox - Setup"
echo "============================================"
echo ""

# Detect script directory (scripts/) and repo root (parent)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

# ============================================================
# 1. Detect Python interpreter (prefer project-local .venv, then 3.12+)
# ============================================================
PYTHON_CMD=""

# 1a. Prefer project-local .venv (consistent with install.bat priority)
if [ -x "$REPO_ROOT/.venv/bin/python" ]; then
    PYTHON_CMD="$REPO_ROOT/.venv/bin/python"
    echo "[OK] Found project venv: $REPO_ROOT/.venv"
fi

# 1b. Check for python3.12 explicitly
if [ -z "$PYTHON_CMD" ] && command -v python3.12 &>/dev/null; then
    PYTHON_CMD="python3.12"
    echo "[OK] Found Python 3.12: $(which python3.12)"
elif command -v python3 &>/dev/null; then
    # Check if python3 is 3.12+
    PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || echo "0.0")
    PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
    PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)
    if [ "$PY_MAJOR" -ge 3 ] && [ "$PY_MINOR" -ge 12 ]; then
        PYTHON_CMD="python3"
        echo "[OK] Found Python 3: $(which python3) (version $PY_VERSION)"
    fi
elif command -v python &>/dev/null; then
    PY_VERSION=$(python -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || echo "0.0")
    PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
    PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)
    if [ "$PY_MAJOR" -ge 3 ] && [ "$PY_MINOR" -ge 12 ]; then
        PYTHON_CMD="python"
        echo "[OK] Found Python: $(which python) (version $PY_VERSION)"
    fi
fi

# ============================================================
# 2. No suitable Python found
# ============================================================
if [ -z "$PYTHON_CMD" ]; then
    echo "[ERROR] Python 3.12+ not found!"
    echo ""
    echo "============================================"
    echo "  Please install Python 3.12+:"
    echo "============================================"
    echo ""
    echo "  Ubuntu/Debian:"
    echo "    sudo apt-get update"
    echo "    sudo apt-get install -y python3.12 python3.12-venv python3-pip"
    echo ""
    echo "  macOS (Homebrew):"
    echo "    brew install python@3.12"
    echo ""
    echo "  CentOS/RHEL/Fedora:"
    echo "    sudo dnf install -y python3.12 python3.12-pip"
    echo ""
    echo "  Or download from: https://www.python.org/downloads/"
    echo ""
    echo "============================================"
    exit 1
fi

echo "Using Python: $PYTHON_CMD"
$PYTHON_CMD --version
echo ""

# ============================================================
# 2b. Unify on project-local .venv（与 install.bat 同一策略）
# ============================================================
if [ ! -x "$REPO_ROOT/.venv/bin/python" ]; then
    echo "[Setup] Creating project-local virtual environment .venv ..."
    if ! "$PYTHON_CMD" -m venv "$REPO_ROOT/.venv"; then
        echo "[WARN] Failed to create .venv - continuing with base interpreter"
    else
        echo "[OK] Created .venv - all dependencies will be installed into it"
        PYTHON_CMD="$REPO_ROOT/.venv/bin/python"
    fi
fi

# ============================================================
# 3. Check NVIDIA GPU
# ============================================================
echo "[Check] NVIDIA GPU..."
if command -v nvidia-smi &>/dev/null; then
    GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)
    echo "[OK] NVIDIA GPU detected: $GPU_NAME"
else
    echo "[!] nvidia-smi not found. NVIDIA GPU and driver are REQUIRED."
    echo "    SeedVR2 does not support CPU-only inference."
    echo ""
fi
echo ""

# ============================================================
# 3b. Check FFmpeg (required by video restore; NOT bundled - see NOTICE item 4)
# ============================================================
echo "[Check] FFmpeg..."
if command -v ffmpeg &>/dev/null; then
    echo "[OK] FFmpeg found: $(command -v ffmpeg)"
else
    echo "[WARN] FFmpeg not found in PATH. Video restore REQUIRES FFmpeg but it is not"
    echo "       bundled with this repository (license reasons, see NOTICE item 4)."
    echo "       Install it with one of:"
    echo "         Ubuntu/Debian:     sudo apt-get install -y ffmpeg"
    echo "         macOS (Homebrew):  brew install ffmpeg"
    echo "         Fedora:            sudo dnf install -y ffmpeg"
    echo "       Install continues - image tasks work without it."
fi
echo ""

# ============================================================
# 4. Install PyTorch with CUDA support
# ============================================================
echo "[Install] Installing PyTorch with CUDA support..."
echo "          If download is too slow, install manually from:"
echo "          https://pytorch.org/get-started/locally/"
echo ""

# Detect CUDA version from nvidia-smi — compatible with both the classic
# "CUDA Version: X.Y" and the newer "CUDA UMD Version: X.Y" driver formats
# (see KNOWN_ISSUES #10). Tier mapping mirrors install.bat so both platforms
# provision the same PyTorch build from one driver report.
TORCH_INDEX="https://download.pytorch.org/whl/cu128"
CUDA_VERSION=""
if command -v nvidia-smi &>/dev/null; then
    CUDA_LINE=$(nvidia-smi 2>/dev/null | grep -oE 'CUDA( UMD)? Version: [0-9.]+' | head -1)
    CUDA_VERSION="${CUDA_LINE##*: }"
fi

if [ -n "$CUDA_VERSION" ]; then
    CUDA_MAJOR="${CUDA_VERSION%%.*}"
    CUDA_MINOR=$(echo "$CUDA_VERSION" | cut -s -d. -f2)
    echo "[OK] Detected CUDA Version: $CUDA_VERSION"
    if [ "${CUDA_MAJOR:-0}" -ge 13 ] 2>/dev/null; then
        TORCH_INDEX="https://download.pytorch.org/whl/cu132"
    elif [ "${CUDA_MAJOR:-0}" -eq 12 ] 2>/dev/null; then
        if [ "${CUDA_MINOR:-0}" -ge 8 ] 2>/dev/null; then
            TORCH_INDEX="https://download.pytorch.org/whl/cu128"
        else
            TORCH_INDEX="https://download.pytorch.org/whl/cu121"
        fi
    elif [ "${CUDA_MAJOR:-0}" -eq 11 ] 2>/dev/null; then
        TORCH_INDEX="https://download.pytorch.org/whl/cu118"
    fi
else
    echo "[!] nvidia-smi not found or no CUDA line - defaulting to the cu128 build."
fi

echo "[Install] Installing PyTorch from: $TORCH_INDEX"
$PYTHON_CMD -m pip install torch torchvision torchaudio --index-url "$TORCH_INDEX" --timeout 1200 --retries 10 || {
    echo "[WARN] PyTorch CUDA install failed, trying default index..."
    $PYTHON_CMD -m pip install torch torchvision torchaudio --timeout 1200 --retries 10
}

# ============================================================
# 5. Install other dependencies
# ============================================================
echo ""
echo "[Install] Installing Python dependencies..."
$PYTHON_CMD -m pip install -r requirements.txt --timeout 300 --retries 3 || {
    echo "[WARN] Some dependencies failed to install"
}

# ============================================================
# 6. Install git hooks - two-layer chain per the hook-reproducibility rule
#    (full protocol lives in maintainer-local AGENTS.md, not distributed with this repo)
#    commit layer = pre-commit; pre-push layer = precheck.ps1 (maintainer-local; GIT_HOOK_PRE_PUSH.sh)
# ============================================================
if command -v git &>/dev/null && [ -d "$REPO_ROOT/.git" ]; then
    if ! "$PYTHON_CMD" -m pre_commit --version &>/dev/null; then
        echo "[SKIP] pre-commit not installed - enable with:"
        echo "       $PYTHON_CMD -m pip install pre-commit && $PYTHON_CMD -m pre_commit install"
    else
        echo "[Setup] Installing git hooks (pre-commit + pre-push fast checks)..."
        "$PYTHON_CMD" -m pre_commit install
        if [ -f "$REPO_ROOT/docs/agents/GIT_HOOK_PRE_PUSH.sh" ]; then
            mkdir -p "$REPO_ROOT/.git/hooks"
            cp "$REPO_ROOT/docs/agents/GIT_HOOK_PRE_PUSH.sh" "$REPO_ROOT/.git/hooks/pre-push"
            chmod +x "$REPO_ROOT/.git/hooks/pre-push"
            echo "[OK] pre-push fast checks installed (precheck.ps1)"
        else
            echo "[SKIP] pre-push source is maintainer-local (not shipped in repo) -"
            echo "       commit layer installed; push gates are enforced by CI"
        fi
    fi
else
    echo "[SKIP] git not found - skipping git hooks installation"
fi

echo ""
echo "============================================"
echo "  Installation complete!"
echo "  Run ./scripts/start.sh to launch the application"
echo "============================================"
