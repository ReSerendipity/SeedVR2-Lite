#!/usr/bin/env python3
"""视频输出客观画质指标（评估整改：视频路径 PSNR/SSIM 基线）。

图像路径的画质门禁由 ``image_metrics.py`` 承担（golden 质量门 + 真机
冒烟 --quality-gate 均为图像路径）；视频修复此前无任何客观画质抽检，
验收只能靠人工眼验（评估报告音视频维度 P1「质量无基线（视频输出面）」）。

本模块把视频对（参考视频 vs 输出视频）落到与图像门**同一套指标口径**：
    1. 双侧视频经 ffmpeg 解码为定长缩放帧（128×128 letterbox，确定性）；
    2. 按帧序号一一配对（帧数不齐时取较少侧，多出帧不参与——诚实计数）；
    3. 逐帧调用 ``image_metrics.psnr/ssim``（与 golden/真机门同源实现），
       求均值并如实报告参与配对的帧数。

设计原则：
- ffmpeg 子进程解码（与应用视频链路同一解码器），不引入 cv2/imageio 新依赖；
- 无 ffmpeg / 视频打不开 / 无可比帧时返回带 error 的 dict（best-effort，
  不抛异常，调用方判失败），与 smoke 脚本 compute_quality 的错误语义一致；
- 纯 CPU，可进 CI（ubuntu runner 自带 ffmpeg；无 ffmpeg 环境由测试层跳过）。

所属项目：SeedVR2 (SeedVR2 视频/图像修复工具)
"""

from __future__ import annotations

import shutil
import subprocess  # noqa: S404 — 受控参数调用系统 ffmpeg，路径来自调用方

import numpy as np

from app.integrated_app.utils.image_metrics import psnr, ssim

# 解码目标帧几何：固定缩放+letterbox，保证双侧帧形状一致且字节分块无歧义
_FRAME_SIDE = 128
_FRAME_CHANNELS = 3
# ffmpeg 定帧几何滤镜：等比缩放到 128 边内，再 pad 到 128×128（黑边居中）
_SCALE_FILTER = (
    f"scale={_FRAME_SIDE}:{_FRAME_SIDE}:force_original_aspect_ratio=decrease,"
    f"pad={_FRAME_SIDE}:{_FRAME_SIDE}:(ow-iw)/2:(oh-ih)/2:black"
)


def ffmpeg_available() -> bool:
    """探测系统 ffmpeg 是否可用（测试层据此决定跳过还是硬门）。"""
    return shutil.which("ffmpeg") is not None


def _decode_frames(path: str, max_frames: int) -> np.ndarray | None:
    """把视频解码为 (N, 128, 128, 3) uint8 帧数组。

    Args:
        path: 视频文件路径。
        max_frames: 最多解码的帧数（控制内存与耗时）。

    Returns:
        帧数组；视频打不开/无 ffmpeg/无可解码帧时 None。
    """
    if not ffmpeg_available() or not path:
        return None
    frame_bytes = _FRAME_SIDE * _FRAME_SIDE * _FRAME_CHANNELS
    try:
        proc = subprocess.run(  # noqa: S603 — 参数全部来自代码内常量与调用方路径
            [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                path,
                "-vf",
                _SCALE_FILTER,
                "-frames:v",
                str(max_frames),
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgb24",
                "-",
            ],
            capture_output=True,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    raw = proc.stdout
    if not raw:
        return None
    count = len(raw) // frame_bytes
    if count <= 0:
        return None
    frames = np.frombuffer(raw[: count * frame_bytes], dtype=np.uint8)
    return frames.reshape(count, _FRAME_SIDE, _FRAME_SIDE, _FRAME_CHANNELS)


def video_psnr_ssim(
    reference_path: str,
    output_path: str,
    max_frames: int = 48,
) -> dict[str, float | int | str]:
    """计算视频对（参考 vs 输出）的逐帧平均 PSNR/SSIM。

    帧数不齐（编码器丢帧/尾帧差异）时按较少侧配对，如实报告
    ``frames_compared`` 与两侧解码帧数——不静默截断。

    Args:
        reference_path: 参考视频路径（基线/源视频）。
        output_path: 待评视频路径（修复输出/再编码产物）。
        max_frames: 最多参与配对的帧数。

    Returns:
        ``{"psnr_db": float, "ssim": float, "frames_compared": int,
        "reference_frames": int, "output_frames": int}``；
        解码失败/无可比帧时 ``{"error": "..."}``。
    """
    ref_frames = _decode_frames(reference_path, max_frames)
    out_frames = _decode_frames(output_path, max_frames)
    if ref_frames is None:
        return {"error": f"reference video not decodable: {reference_path}"}
    if out_frames is None:
        return {"error": f"output video not decodable: {output_path}"}
    n = min(ref_frames.shape[0], out_frames.shape[0])
    if n <= 0:
        return {"error": "no comparable frames"}
    psnrs: list[float] = []
    ssims: list[float] = []
    for i in range(n):
        psnrs.append(psnr(ref_frames[i], out_frames[i]))
        ssims.append(ssim(ref_frames[i], out_frames[i]))
    return {
        "psnr_db": round(float(np.mean(psnrs)), 4),
        "ssim": round(float(np.mean(ssims)), 6),
        "frames_compared": int(n),
        "reference_frames": int(ref_frames.shape[0]),
        "output_frames": int(out_frames.shape[0]),
    }
