#!/usr/bin/env bash
# SeedVR2-Turbo — Portable Launcher (SCAFFOLD, cross-platform)
# ============================================================
# Mie-Package-Launcher style: every path is resolved relative to this
# script; it bootstraps a local Python env, installs deps, then starts the app.
# Goal: run from a USB drive / any path with no system Python required.
#
# Usage:
#   ./launcher.sh
#   ./launcher.sh --size 7b --skip-deps
#
# Flags:
#   --size <3b|7b|7b_sharp>   only used for the weights-missing hint (default 3b)
#   --skip-deps                skip pip install (env already ready)
#   --skip-model-check         skip weights presence check
#
# Directory layout (PKG_ROOT is auto-detected):
#   <pkg>/
#   ├── launcher.sh            # this file
#   ├── app/clean_launch.py    # app entrypoint (PKG_ROOT anchor)
#   ├── model/                 # weights dir (portable mode)
#   ├── venv/                  # local venv (created on first run)
#   ├── python/                # optional: bundled portable python (preferred)
#   ├── wheels/                # optional: offline wheel cache (preferred)
#   └── requirements.txt       # app deps (or requirements-lock.txt)
#
# Open questions (SCAFFOLD — confirm before shipping):
#   * Exact weight paths follow config.yaml model.pretrained_dir=model;
#     filenames must match the docs (seedvr2_ema_3b_fp16.safetensors, etc.).
#   * Model download URL: default repo numz/SeedVR2_comfyUI;
#     use HF_ENDPOINT=https://hf-mirror.com for CN mirrors.
#   * Linux/macOS note: this app targets NVIDIA/CUDA + Windows primarily;
#     a CUDA Linux build of torch is installed via requirements on those hosts.
set -euo pipefail

# ---- 1. Resolve PKG_ROOT relative to this script ----
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PKG_ROOT="$SCRIPT_DIR"
if [ ! -f "$PKG_ROOT/app/clean_launch.py" ]; then
  CANDIDATE="$(dirname "$PKG_ROOT")"
  if [ -f "$CANDIDATE/app/clean_launch.py" ]; then
    PKG_ROOT="$CANDIDATE"
  fi
fi
echo "[launcher] Package root: $PKG_ROOT"

# ---- parse flags ----
SIZE="3b"
SKIP_DEPS=0
SKIP_MODEL_CHECK=0
while [ $# -gt 0 ]; do
  case "$1" in
    --size) SIZE="$2"; shift 2 ;;
    --skip-deps) SKIP_DEPS=1; shift ;;
    --skip-model-check) SKIP_MODEL_CHECK=1; shift ;;
    *) echo "[launcher] Unknown arg: $1" >&2; exit 1 ;;
  esac
done

# ---- 2. Locate a Python interpreter ----
PYTHON=""
if [ -x "$PKG_ROOT/python/bin/python3" ]; then
  PYTHON="$PKG_ROOT/python/bin/python3"
elif [ -x "$PKG_ROOT/venv/bin/python" ]; then
  PYTHON="$PKG_ROOT/venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON="$(command -v python3)"
elif command -v python >/dev/null 2>&1; then
  PYTHON="$(command -v python)"
fi
if [ -z "$PYTHON" ]; then
  echo "[launcher] ERROR: no Python found. Bundle python/ or install Python 3.12+." >&2
  exit 1
fi
echo "[launcher] Using Python: $PYTHON"

# ---- 3. Create venv if missing ----
VENV_PY="$PKG_ROOT/venv/bin/python"
if [ ! -x "$VENV_PY" ]; then
  echo "[launcher] Creating venv at $PKG_ROOT/venv (--copies) ..."
  "$PYTHON" -m venv --copies "$PKG_ROOT/venv"
  VENV_PY="$PKG_ROOT/venv/bin/python"
fi
PYTHON="$VENV_PY"

# ---- 4. Install dependencies ----
if [ "$SKIP_DEPS" -eq 0 ]; then
  REQS="$PKG_ROOT/requirements.txt"
  if [ ! -f "$REQS" ] && [ -f "$PKG_ROOT/requirements-lock.txt" ]; then
    REQS="$PKG_ROOT/requirements-lock.txt"
  fi
  if [ ! -f "$REQS" ]; then
    echo "[launcher] WARNING: requirements.txt / requirements-lock.txt not found, skipping install" >&2
  else
    "$PYTHON" -m pip install --upgrade pip
    if [ -d "$PKG_ROOT/wheels" ]; then
      echo "[launcher] Installing from bundled wheels (offline)..."
      "$PYTHON" -m pip install --no-index --find-links "$PKG_ROOT/wheels" -r "$REQS"
    else
      echo "[launcher] Installing from requirements (online)..."
      "$PYTHON" -m pip install -r "$REQS"
    fi
  fi
fi

# ---- 5. Environment variables (relative, portable) ----
export KMP_DUPLICATE_LIB_OK=TRUE
export PYTHONPATH="$PKG_ROOT"
export PYTORCH_CUDA_ALLOC_CONF="expandable_segments:True"
export TORCHINDUCTOR_CACHE_DIR="$PKG_ROOT/.torch_cache/inductor"
export PYTHONUTF8=1

# ---- 6. Weights presence check (non-fatal) ----
if [ "$SKIP_MODEL_CHECK" -eq 0 ]; then
  MODEL_DIR="$PKG_ROOT/model"
  if [ ! -d "$MODEL_DIR" ]; then
    echo "[launcher] WARNING: model/ not found at $MODEL_DIR. Run: python scripts/download_model.py --size $SIZE" >&2
  else
    for f in ema_vae_fp16.safetensors pos_emb.pt neg_emb.pt; do
      if [ ! -f "$MODEL_DIR/$f" ]; then
        echo "[launcher] WARNING: missing weight file: $f" >&2
      fi
    done
  fi
fi

# ---- 7. Launch ----
echo "[launcher] Starting app — open http://127.0.0.1:7870 in your browser ..."
cd "$PKG_ROOT"
exec "$PYTHON" "$PKG_ROOT/app/clean_launch.py"
