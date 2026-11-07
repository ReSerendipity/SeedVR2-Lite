#!/usr/bin/env python3
"""视频输出客观画质基线：生成/复算脚本（评估整改：视频路径画质门禁）。

评估发现（音视频维度 P1「质量无基线（视频输出面）」）：golden 质量门与真机
质量门均为图像路径，视频修复验收只能靠人工眼验。本脚本建立**可复算**的视频
画质基线并接进测试/CI：

    1. 纯 numpy 确定性合成 32 帧动态源帧（移动方块 + 渐变背景，无随机源，
       噪声固定 seed），编码为参考视频（libx264 crf 18，yuv420p）；
    2. 同一源帧施加确定性内容退化（4× 降采样回放 + 高斯噪声 seed 固定），
       再以更劣档位编码（crf 28）——模拟「编码器/模型输出质量回退」的坏案例；
    3. 用 ``video_metrics.video_psnr_ssim``（与图像 golden 门同一套
       psnr/ssim 实现，经 ffmpeg 解码 + 逐帧配对）计算两视频的画质指标，
       写入/复算 ``tests/golden/video_quality_baseline.json``。

门禁语义（--check，CI 与 pytest integration 用例调用）：
    - 重算指标 >= manifest 硬下限（psnr_db_min / ssim_min）→ PASS；
    - 低于下限或解码失败 → 退出码 1（红）。
    - 下限 = 生成时实测值 − 跨 ffmpeg 版本余量（默认 PSNR 2.0 dB / SSIM 0.05），
      余量只吸收编码器版本漂移，内容级回归必然击穿。

用法：
    python scripts/generate_video_quality_baseline.py --check    # 门禁复算（CI）
    python scripts/generate_video_quality_baseline.py --generate # 重建基线清单（显式）

依赖：numpy + 系统 ffmpeg（ubuntu runner 自带；无 ffmpeg 时 --check 报错退出 1）。
"""

from __future__ import annotations

import argparse
import json
import subprocess  # noqa: S404 — 受控参数调用系统 ffmpeg
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.integrated_app.utils.video_metrics import ffmpeg_available, video_psnr_ssim  # noqa: E402

MANIFEST_PATH = ROOT / "tests" / "golden" / "video_quality_baseline.json"
MANIFEST_SCHEMA = "seedvr2-video-golden/1"

# 确定性参数（全部写死；改任何一项都会改变基线值，须 --generate 重算并在 PR 说明）
FRAME_COUNT = 32
FRAME_SIZE = 256
FPS = 8
NOISE_SEED = 20261105
REF_CRF = "18"
DEGRADED_CRF = "28"
# 跨 ffmpeg 版本余量：只吸收编码器漂移，内容级回归必然击穿
PSNR_MARGIN_DB = 2.0
SSIM_MARGIN = 0.05


def _source_frame(i: int) -> np.ndarray:
    """第 i 帧确定性源帧：渐变背景 + 匀速移动方块（纯公式，无随机）。"""
    xs = np.linspace(0, 1, FRAME_SIZE, dtype=np.float64)
    grid_x, grid_y = np.meshgrid(xs, xs)
    # 背景：随时间缓慢变化的对角渐变（给时序维度以真实内容）
    phase = 0.1 * (i / FRAME_COUNT)
    r = grid_x * (1 - phase) + phase * grid_y
    g = grid_y
    b = 0.5 + 0.5 * np.sin(np.pi * (grid_x + grid_y) * 0.5)
    frame = np.stack([r, g, b], axis=-1)
    # 前景：匀速移动的亮方块（帧间位移 6px，供时序/配对逻辑感知运动）
    box = 48
    x0 = int((FRAME_SIZE - box) * (i / FRAME_COUNT))
    y0 = int((FRAME_SIZE - box) * 0.5)
    frame[y0 : y0 + box, x0 : x0 + box, :] = 0.95
    return (np.clip(frame, 0, 1) * 255).astype(np.uint8)


def _degrade_frame(frame: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """确定性内容退化：4× 降采样回放（像素化）+ 固定 seed 高斯噪声。"""
    small = frame[::4, ::4, :]
    degraded = np.repeat(np.repeat(small, 4, axis=0), 4, axis=1)
    noise = rng.normal(0.0, 8.0, size=degraded.shape)
    return np.clip(degraded.astype(np.float64) + noise, 0, 255).astype(np.uint8)


def _encode_frames(frames: list[np.ndarray], out_path: Path, crf: str) -> None:
    """把 uint8 帧序列编码为 h264 mp4（从 stdin 喂 rawvideo，无中间文件）。"""
    h, w = frames[0].shape[:2]
    cmd = [
        "ffmpeg",
        "-v",
        "error",
        "-y",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{w}x{h}",
        "-r",
        str(FPS),
        "-i",
        "-",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        crf,
        "-pix_fmt",
        "yuv420p",
        str(out_path),
    ]
    proc = subprocess.run(  # noqa: S603 — 参数全部为代码内常量
        cmd, input=b"".join(f.tobytes() for f in frames), capture_output=True, timeout=120
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg 编码失败: {proc.stderr.decode(errors='replace')[:400]}")


def build_clips(tmp_dir: Path) -> tuple[Path, Path]:
    """生成参考/退化两个视频，返回 (ref_path, degraded_path)。"""
    rng = np.random.default_rng(NOISE_SEED)
    source = [_source_frame(i) for i in range(FRAME_COUNT)]
    degraded = [_degrade_frame(f, rng) for f in source]
    ref_path = tmp_dir / "video_ref.mp4"
    out_path = tmp_dir / "video_degraded.mp4"
    _encode_frames(source, ref_path, REF_CRF)
    _encode_frames(degraded, out_path, DEGRADED_CRF)
    return ref_path, out_path


def measure(tmp_dir: Path) -> dict:
    """生成剪辑并计算参考 vs 退化的画质指标（含健全性断言数据）。"""
    ref_path, out_path = build_clips(tmp_dir)
    metrics = video_psnr_ssim(str(ref_path), str(out_path))
    if "error" in metrics:
        raise RuntimeError(f"画质指标计算失败: {metrics['error']}")
    # 健全性：退化必须真实拉低质量（对比参考剪辑自比应为近似无损）
    self_metrics = video_psnr_ssim(str(ref_path), str(ref_path))
    if "error" in self_metrics:
        raise RuntimeError(f"参考剪辑自比失败: {self_metrics['error']}")
    metrics["reference_self_psnr_db"] = self_metrics["psnr_db"]
    metrics["reference_self_ssim"] = self_metrics["ssim"]
    return metrics


def generate() -> int:
    """生成基线清单（显式动作，产出落 tests/golden/）。"""
    if not ffmpeg_available():
        print("FAIL: 未检测到系统 ffmpeg，无法生成视频画质基线")
        return 1
    with tempfile.TemporaryDirectory(prefix="seedvr2_video_baseline_") as td:
        m = measure(Path(td))
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "frames": FRAME_COUNT,
        "frame_size": FRAME_SIZE,
        "fps": FPS,
        "noise_seed": NOISE_SEED,
        "ref_crf": REF_CRF,
        "degraded_crf": DEGRADED_CRF,
        "margin": {"psnr_db": PSNR_MARGIN_DB, "ssim": SSIM_MARGIN},
        "degraded_vs_reference": {
            "psnr_db": m["psnr_db"],
            "ssim": m["ssim"],
            "frames_compared": m["frames_compared"],
        },
        "reference_self_check": {
            "psnr_db": m["reference_self_psnr_db"],
            "ssim": m["reference_self_ssim"],
        },
        "floors": {
            "psnr_db_min": round(float(m["psnr_db"]) - PSNR_MARGIN_DB, 4),
            "ssim_min": round(float(m["ssim"]) - SSIM_MARGIN, 6),
        },
    }
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"[OK] 基线清单已写入 {MANIFEST_PATH}")
    print(json.dumps(manifest["degraded_vs_reference"], ensure_ascii=False))
    print(json.dumps(manifest["floors"], ensure_ascii=False))
    return 0


def check() -> int:
    """门禁复算：重建剪辑 → 重算指标 → 对 manifest 硬下限判定。"""
    if not MANIFEST_PATH.is_file():
        print(f"FAIL: 基线清单不存在：{MANIFEST_PATH}（先跑 --generate）")
        return 1
    if not ffmpeg_available():
        print("FAIL: 未检测到系统 ffmpeg，无法复算视频画质基线")
        return 1
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if manifest.get("schema") != MANIFEST_SCHEMA:
        print(f"FAIL: manifest schema 不匹配: {manifest.get('schema')!r} != {MANIFEST_SCHEMA!r}")
        return 1
    floors = manifest["floors"]
    with tempfile.TemporaryDirectory(prefix="seedvr2_video_baseline_") as td:
        m = measure(Path(td))
    psnr_db, ssim_v = float(m["psnr_db"]), float(m["ssim"])
    failures: list[str] = []
    if psnr_db < floors["psnr_db_min"]:
        failures.append(f"PSNR {psnr_db:.4f}dB < 下限 {floors['psnr_db_min']}dB")
    if ssim_v < floors["ssim_min"]:
        failures.append(f"SSIM {ssim_v:.6f} < 下限 {floors['ssim_min']}")
    # 参考剪辑自比必须近似无损（解码/配对链路健全性）
    if float(m["reference_self_psnr_db"]) < 30.0:
        failures.append(f"参考剪辑自比 PSNR {m['reference_self_psnr_db']:.2f}dB < 30dB（解码链路异常）")
    if failures:
        print("FAIL: 视频画质基线复算未通过（内容级回归或链路异常）：")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(
        f"[PASS] 视频画质基线复算通过：PSNR {psnr_db:.4f}dB / SSIM {ssim_v:.6f} "
        f"（{m['frames_compared']} 帧配对；下限 {floors['psnr_db_min']}dB / {floors['ssim_min']}）"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="视频输出客观画质基线（生成/复算门禁）")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="复算并对硬下限判定（默认；CI 用）")
    mode.add_argument("--generate", action="store_true", help="重建基线清单（须在 PR 说明理由）")
    args = parser.parse_args()
    return generate() if args.generate else check()


if __name__ == "__main__":
    raise SystemExit(main())
