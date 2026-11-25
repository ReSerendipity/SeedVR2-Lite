#!/usr/bin/env python3
"""doctor.py — SeedVR2-Turbo 一键环境诊断。

聚合此前散落在 install.bat / clean_launch.py / verify_engine.py 各自为政的
环境检查，一次跑完并给出 PASS / WARN / FAIL 分级结论（DX 评估 P2-8）。

检查项：
    1. Python 版本（>= 3.12）
    2. 核心依赖可导入（fastapi/uvicorn/pydantic/yaml/...）；torch 等缺失降级为 WARN
    3. GPU / CUDA 可用性（torch.cuda）
    4. FFmpeg（imageio-ffmpeg 内置二进制或 PATH；缺失仅影响视频修复）
    5. 模型权重文件存在性（按 config.yaml 的 model.models.<default_size> 检查）
    6. 磁盘剩余空间（低于 retention.disk_min_free_gb 阈值对应 FAIL）
    7. 端口 7870 占用情况
    8. .venv 存在性（与 start.bat 的优先级约定一致；precheck.ps1 为维护者本地脚本，未随仓库分发）

用法：
    python scripts/doctor.py                # 人类可读报告
    python scripts/doctor.py --json         # 机器可读输出
    python scripts/doctor.py --port 8080    # 检查自定义端口

退出码：0 = 无 FAIL 项；1 = 存在 FAIL 项（环境不可用）。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import socket
import sys
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_PORT = 7870
DEFAULT_MIN_FREE_GB = 5.0
MIN_PYTHON = (3, 12)

# 缺失即环境不可用的依赖（FAIL）
CORE_PACKAGES = ["fastapi", "uvicorn", "pydantic", "yaml", "aiofiles", "aiosqlite"]
# 缺失仅降级（WARN）：torch 缺失 → 无推理；cv2/PIL → 图像处理受损
OPTIONAL_PACKAGES = ["torch", "cv2", "PIL", "safetensors", "huggingface_hub"]

PASS, WARN, FAIL, SKIP = "PASS", "WARN", "FAIL", "SKIP"


@dataclass
class CheckResult:
    """单项检查结果。status ∈ {PASS, WARN, FAIL, SKIP}。"""

    status: str
    detail: str


def _import_available(name: str) -> bool:
    """仅探测模块是否存在，不真正导入（避免 torch 这类重依赖拖慢诊断）。"""
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def check_python_version(min_version: tuple[int, int] = MIN_PYTHON) -> CheckResult:
    """检查运行解释器版本下限。"""
    version = sys.version_info[:3]
    if (version[0], version[1]) >= min_version:
        return CheckResult(PASS, f"Python {version[0]}.{version[1]}.{version[2]}")
    return CheckResult(
        FAIL,
        f"Python {version[0]}.{version[1]}.{version[2]} 低于最低要求 "
        f"{min_version[0]}.{min_version[1]}（pyproject requires-python >=3.12,<3.15）",
    )


def check_dependencies(core: list[str] | None = None, optional: list[str] | None = None) -> CheckResult:
    """检查核心依赖可导入；可选依赖缺失仅 WARN。"""
    core = CORE_PACKAGES if core is None else core
    optional = OPTIONAL_PACKAGES if optional is None else optional
    missing_core = [p for p in core if not _import_available(p)]
    missing_optional = [p for p in optional if not _import_available(p)]
    if missing_core:
        return CheckResult(
            FAIL,
            f"核心依赖缺失: {', '.join(missing_core)}" "（请在 .venv 内安装依赖：uv sync 或 install.bat）",
        )
    if missing_optional:
        return CheckResult(WARN, f"可选依赖缺失（功能降级）: {', '.join(missing_optional)}")
    return CheckResult(PASS, f"核心 {len(core)} + 可选 {len(optional)} 项依赖全部可用")


def check_gpu() -> CheckResult:
    """检查 torch 与 CUDA 可用性；torch 缺失时 SKIP（check_dependencies 已 WARN）。"""
    if not _import_available("torch"):
        return CheckResult(SKIP, "torch 未安装，跳过 GPU 检查（推理功能不可用）")
    try:
        import torch
    except ImportError as exc:  # pragma: no cover - find_spec 已过滤，防御性分支
        return CheckResult(SKIP, f"torch 导入失败，跳过 GPU 检查: {exc}")
    if torch.cuda.is_available():
        props = torch.cuda.get_device_properties(0)
        vram_gb = props.total_memory / (1024**3)
        return CheckResult(PASS, f"GPU: {props.name}, VRAM {vram_gb:.1f}GB, torch {torch.__version__}")
    return CheckResult(WARN, "torch 已安装但 CUDA 不可用 — 应用可启动，推理功能降级（需 NVIDIA GPU + 驱动）")


def check_ffmpeg() -> CheckResult:
    """检查 FFmpeg：优先 imageio-ffmpeg 自带二进制，其次 PATH。"""
    try:
        import imageio_ffmpeg

        return CheckResult(PASS, f"FFmpeg（imageio-ffmpeg 内置）: {imageio_ffmpeg.get_ffmpeg_exe()}")
    except ImportError:
        pass
    except Exception as exc:  # imageio_ffmpeg 装了但二进制损坏/下载失败
        which = shutil.which("ffmpeg")
        if which:
            return CheckResult(PASS, f"FFmpeg（系统 PATH）: {which}")
        return CheckResult(WARN, f"imageio-ffmpeg 异常（{exc}）且 PATH 无 ffmpeg")
    which = shutil.which("ffmpeg")
    if which:
        return CheckResult(PASS, f"FFmpeg（系统 PATH）: {which}")
    return CheckResult(
        WARN, "未找到 FFmpeg — 图片修复不受影响；视频修复不可用（便携包用户需自行安装，见 NOTICE 第 4 条）"
    )


def _model_base_dir(cfg: dict, project_root: Path) -> Path:
    """根据 model_source_mode 决定权重查找根目录。"""
    if cfg.get("model_source_mode") == "shared" and cfg.get("shared_models_root"):
        return project_root / str(cfg["shared_models_root"])
    return project_root / str(cfg.get("pretrained_dir", "model"))


def check_model_files(config_path: Path | None = None, project_root: Path | None = None) -> CheckResult:
    """按 config.yaml 的默认模型条目检查权重文件是否落盘。"""
    import yaml

    config_path = config_path or PROJECT_ROOT / "config.yaml"
    project_root = project_root or config_path.parent
    if not config_path.exists():
        return CheckResult(SKIP, f"未找到 {config_path.name}，跳过模型文件检查")
    cfg = (yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}).get("model") or {}
    models = cfg.get("models") or {}
    size = str(cfg.get("default_size", "3b"))
    entry = models.get(size)
    if not entry:
        return CheckResult(SKIP, f"config.yaml 中无 model.models.{size} 条目，跳过")

    base = _model_base_dir(cfg, project_root)
    precision = str(cfg.get("default_precision", "fp16"))
    files = [
        entry.get(f"checkpoint_{precision}"),
        entry.get("vae_checkpoint"),
        entry.get("pos_emb"),
        entry.get("neg_emb"),
    ]
    files = [f for f in files if f]
    missing = [f for f in files if not (base / f).exists()]
    if not files:
        return CheckResult(SKIP, "模型条目未配置文件名，跳过")
    if missing:
        return CheckResult(
            WARN,
            f"模型 {size}/{precision} 缺 {len(missing)}/{len(files)} 个文件（查找目录: {base}）: "
            f"{', '.join(missing)} — 运行 python scripts/download_model.py --size {size}",
        )
    return CheckResult(PASS, f"模型 {size}/{precision} 的 {len(files)} 个文件齐全（{base}）")


def check_disk_space(path: Path | None = None, min_gb: float = DEFAULT_MIN_FREE_GB) -> CheckResult:
    """检查磁盘剩余空间；< min_gb FAIL（运行时会 507），< 2×min_gb WARN。"""
    path = path or PROJECT_ROOT
    free_gb = shutil.disk_usage(path).free / (1024**3)
    if free_gb < min_gb:
        return CheckResult(FAIL, f"{path} 剩余 {free_gb:.1f}GB < {min_gb:.0f}GB（任务将被 507 拒绝）")
    if free_gb < 2 * min_gb:
        return CheckResult(WARN, f"{path} 剩余 {free_gb:.1f}GB，接近 {min_gb:.0f}GB 预检线，建议清理")
    return CheckResult(PASS, f"{path} 剩余 {free_gb:.1f}GB")


def check_port(port: int = DEFAULT_PORT) -> CheckResult:
    """检查服务端口占用：可绑定 = PASS；被占 = WARN（服务可能已在运行）。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1.0)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return CheckResult(WARN, f"端口 {port} 已被占用 — 服务可能已在运行（http://127.0.0.1:{port}）")
    return CheckResult(PASS, f"端口 {port} 空闲")


def check_venv(project_root: Path | None = None) -> CheckResult:
    """检查项目 .venv 是否存在（start.bat 与维护者本地、未随仓库分发的 precheck.ps1 / run_checks.bat 的默认解释器）。"""
    project_root = project_root or PROJECT_ROOT
    marker = ".venv/Scripts/python.exe" if sys.platform == "win32" else ".venv/bin/python"
    if (project_root / marker).exists():
        return CheckResult(PASS, f".venv 存在（{marker}）")
    return CheckResult(WARN, "未找到 .venv — 本仓所有入口脚本默认使用它（创建：install.bat 或 uv sync）")


def run_all(
    project_root: Path | None = None, port: int = DEFAULT_PORT, min_free_gb: float = DEFAULT_MIN_FREE_GB
) -> list[tuple[str, CheckResult]]:
    """跑全部检查，返回 [(名称, 结果), ...]。"""
    return [
        ("Python 版本", check_python_version()),
        ("核心依赖", check_dependencies()),
        ("GPU / CUDA", check_gpu()),
        ("FFmpeg", check_ffmpeg()),
        ("模型权重", check_model_files((project_root or PROJECT_ROOT) / "config.yaml")),
        ("磁盘空间", check_disk_space(project_root or PROJECT_ROOT, min_free_gb)),
        ("端口占用", check_port(port)),
        (".venv", check_venv(project_root)),
    ]


def main(argv: list[str] | None = None) -> int:
    """CLI 入口：打印报告，存在 FAIL 项时退出码 1。"""
    parser = argparse.ArgumentParser(description="SeedVR2-Turbo 一键环境诊断")
    parser.add_argument("--json", action="store_true", help="输出 JSON（供 CI/脚本消费）")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="服务端口（默认 7870）")
    parser.add_argument("--min-free-gb", type=float, default=DEFAULT_MIN_FREE_GB, help="磁盘预检线（GB）")
    args = parser.parse_args(argv)

    results = run_all(port=args.port, min_free_gb=args.min_free_gb)

    if args.json:
        print(
            json.dumps(
                {name: {"status": res.status, "detail": res.detail} for name, res in results},
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        icon = {PASS: "✅", WARN: "⚠️ ", FAIL: "❌", SKIP: "⏭️ "}
        width = max(len(name) for name, _ in results) + 2
        print("=" * 70)
        print("SeedVR2-Turbo 环境诊断 (doctor)")
        print("=" * 70)
        for name, res in results:
            print(f"{icon[res.status]} {name:<{width}} [{res.status}] {res.detail}")
        print("=" * 70)

    has_fail = any(res.status == FAIL for _, res in results)
    if not args.json:
        if has_fail:
            print("结论: 存在 FAIL 项 — 请先修复再加运行行推理任务")
        else:
            print("结论: 无 FAIL 项 — 环境可用（WARN 项不影响启动）")
    return 1 if has_fail else 0


if __name__ == "__main__":
    sys.exit(main())
