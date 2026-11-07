#!/usr/bin/env python3
"""视频输出客观画质基线测试（评估整改：视频路径画质门禁接进测试）。

覆盖三层：
1. 配对/聚合核心（纯单元，monkeypatch 解码层，不依赖 ffmpeg）：
   帧数不齐取较少侧并如实计数、指标与手算一致、解码失败返回 error dict。
2. 退化有效性（integration）：numpy 退化显著拉低画质（劣化必须可被指标看见）。
3. 基线门禁（integration）：`generate_video_quality_baseline.py --check`
   退出码 0——重算指标不低于 manifest 硬下限。

integration 用例无 ffmpeg 时干净跳过（windows runner 无 ffmpeg；ubuntu runner
自带 ffmpeg，硬门生效）。运行：pytest tests/test_video_quality_baseline.py -q

所属项目：SeedVR2 (SeedVR2 视频/图像修复工具)
"""

import subprocess  # noqa: S404
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.integrated_app.utils import video_metrics  # noqa: E402
from app.integrated_app.utils.video_metrics import video_psnr_ssim  # noqa: E402

MANIFEST = ROOT / "tests" / "golden" / "video_quality_baseline.json"
GENERATOR = ROOT / "scripts" / "generate_video_quality_baseline.py"

requires_ffmpeg = pytest.mark.skipif(
    not video_metrics.ffmpeg_available(), reason="系统无 ffmpeg（视频基线门禁在此环境跳过）"
)


def _fake_decode(frames: list[np.ndarray]):
    """构造 _decode_frames 替身：按 path 后缀返回不同帧数。"""

    def decode(path: str, max_frames: int) -> np.ndarray | None:
        if path.endswith(".broken"):
            return None
        seq = frames[0] if path.endswith(".ref") else frames[1]
        return np.asarray(seq[:max_frames], dtype=np.uint8)

    return decode


class TestPairingAndAggregation:
    """帧配对与聚合核心（单元层，不依赖 ffmpeg）。"""

    def test_identical_frames_give_perfect_scores(self, monkeypatch):
        frames = [np.full((64, 64, 3), 128, dtype=np.uint8) for _ in range(4)]
        monkeypatch.setattr(video_metrics, "_decode_frames", _fake_decode([frames, frames]))
        m = video_psnr_ssim("a.ref", "a.ref")
        assert m["psnr_db"] == pytest.approx(99.0)
        assert m["ssim"] == pytest.approx(1.0)
        assert m["frames_compared"] == 4

    def test_frame_count_mismatch_pairs_on_shorter_side(self, monkeypatch):
        ref = [np.full((64, 64, 3), 128, dtype=np.uint8) for _ in range(6)]
        out = [np.full((64, 64, 3), 128, dtype=np.uint8) for _ in range(3)]
        monkeypatch.setattr(video_metrics, "_decode_frames", _fake_decode([ref, out]))
        m = video_psnr_ssim("a.ref", "b.out")
        assert m["frames_compared"] == 3
        assert m["reference_frames"] == 6
        assert m["output_frames"] == 3
        assert m["psnr_db"] == pytest.approx(99.0)

    def test_undecodable_side_returns_error_dict(self, monkeypatch):
        frames = [np.full((64, 64, 3), 128, dtype=np.uint8) for _ in range(2)]
        monkeypatch.setattr(video_metrics, "_decode_frames", _fake_decode([frames, frames]))
        m = video_psnr_ssim("a.broken", "b.ref")
        assert "error" in m
        assert "reference" in m["error"]

    def test_degradation_lowers_psnr(self, monkeypatch):
        ref = [np.full((64, 64, 3), 100, dtype=np.uint8) for _ in range(2)]
        out = [np.full((64, 64, 3), 140, dtype=np.uint8) for _ in range(2)]
        monkeypatch.setattr(video_metrics, "_decode_frames", _fake_decode([ref, out]))
        m = video_psnr_ssim("a.ref", "b.out")
        assert m["psnr_db"] < 30.0

    def test_max_frames_caps_decoding(self, monkeypatch):
        ref = [np.full((64, 64, 3), 128, dtype=np.uint8) for _ in range(16)]
        monkeypatch.setattr(video_metrics, "_decode_frames", _fake_decode([ref, ref]))
        m = video_psnr_ssim("a.ref", "b.ref", max_frames=5)
        assert m["frames_compared"] == 5


class TestBaselineGate:
    """基线门禁（integration，需 ffmpeg 的用例单独标注）。"""

    def test_numpy_degradation_is_visible_to_metrics(self):
        """退化帧 vs 源帧（未编码、纯数组）必须显著低于自比（纯 numpy，无 ffmpeg 依赖）。"""
        sys.path.insert(0, str(ROOT / "scripts"))
        try:
            import generate_video_quality_baseline as gen

            rng = np.random.default_rng(gen.NOISE_SEED)
            src = gen._source_frame(0)
            deg = gen._degrade_frame(src, rng)
            from app.integrated_app.utils.image_metrics import psnr, ssim

            degraded_psnr = psnr(src, deg)
            assert degraded_psnr < 40.0  # 退化真实可见
            assert ssim(src, deg) < 0.99
        finally:
            sys.path.pop(0)

    @requires_ffmpeg
    @pytest.mark.integration
    def test_baseline_check_passes_on_current_tree(self):
        """门禁复算：当前代码树重建剪辑并重算，必须 >= manifest 硬下限。"""
        proc = subprocess.run(  # noqa: S603
            [sys.executable, str(GENERATOR), "--check"],
            capture_output=True,
            text=True,
            timeout=300,
            cwd=str(ROOT),
        )
        assert proc.returncode == 0, f"视频画质基线门禁未通过：\n{proc.stdout}\n{proc.stderr}"

    @requires_ffmpeg
    @pytest.mark.integration
    def test_manifest_matches_actual_measurement(self):
        """清单记录值与本机重算值一致（可复算性的直接证据；跨 ffmpeg 版本允许余量）。"""
        manifest = __import__("json").loads(MANIFEST.read_text(encoding="utf-8"))
        sys.path.insert(0, str(ROOT / "scripts"))
        try:
            import tempfile

            import generate_video_quality_baseline as gen

            with tempfile.TemporaryDirectory() as td:
                m = gen.measure(Path(td))
        finally:
            sys.path.pop(0)
        assert m["psnr_db"] == pytest.approx(manifest["degraded_vs_reference"]["psnr_db"], abs=2.0)
        assert m["ssim"] == pytest.approx(manifest["degraded_vs_reference"]["ssim"], abs=0.05)


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
