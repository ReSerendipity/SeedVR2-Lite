"""VAE 解码内部 OOM 自动降档的可观测性测试（P0-2 缺陷修复）。

覆盖：
- 首次 OOM 减半 tile 时记录 from→to 并可从 get_vae_decode_oom_fallback() 读取；
- 第二次 OOM 关闭 tiled 时记录 disabled_tiled；
- 无 OOM 时返回 None。
"""

import types

import pytest
import torch

from app.integrated_app.engines import _vae_pipeline as vp


class _NoopCtx:
    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class FakeDecodeVae:
    def __init__(self, oom_times: int):
        self.oom_times = oom_times
        self.calls: list[dict] = []

    def decode(self, batch, tiled=False, tile_size=None, tile_overlap=None):
        self.calls.append({"tiled": tiled, "tile": tile_size, "overlap": tile_overlap})
        if tiled and len(self.calls) <= self.oom_times:
            raise RuntimeError("CUDA out of memory. Tried to allocate 2.50 GiB")
        return types.SimpleNamespace(sample=torch.zeros(1, 3, 2, 8, 8))


class DummyPipeline(vp._VAEPipelineMixin):
    def __init__(self, oom_times: int):
        self._model_config = {"vae": {"scaling_factor": 1.0, "shifting_factor": 0.0, "dtype": "float32"}}
        self.device = torch.device("cpu")
        self._vae_tiled_config = {
            "decode_tiled": True,
            "decode_tile_size": 1024,
            "decode_tile_overlap": 256,
            "auto_tile_size": False,
        }
        self.vae = FakeDecodeVae(oom_times)


@pytest.fixture(autouse=True)
def _patch_heavy_deps(monkeypatch):
    monkeypatch.setattr(vp, "_TileProgressLogger", _NoopCtx)
    monkeypatch.setattr(vp, "_VaeStallWatchdog", _NoopCtx)
    monkeypatch.setattr(vp, "empty_gpu_cache", lambda: None)
    monkeypatch.setattr(vp, "_force_release_memory", lambda: None)
    monkeypatch.setattr(vp, "get_free_vram_gb", lambda: 0.0)
    import app.integrated_app.optimization.inference.vae_tiled_enhance as vte

    monkeypatch.setattr(vte, "detect_nan", lambda *a, **k: False)


def _latents():
    # _vae_decode 期望 (T, H, W, C) 形态
    return [torch.randn(2, 8, 8, 4)]


def test_first_oom_records_tile_reduction():
    engine = DummyPipeline(oom_times=1)
    engine._vae_decode(_latents())

    info = engine.get_vae_decode_oom_fallback()
    assert info is not None
    assert info["reason"] == "oom"
    assert info["from_tile"] == 1024
    assert info["to_tile"] == 512
    assert info["from_overlap"] == 256
    assert info["to_overlap"] == 128
    assert info["disabled_tiled"] is False
    # 第二次解码确实使用了更小的 tile
    assert engine.vae.calls[1]["tile"] == (512, 512)
    assert engine.vae.calls[1]["overlap"] == (128, 128)


def test_second_oom_records_tiled_disabled():
    engine = DummyPipeline(oom_times=2)
    engine._vae_decode(_latents())

    info = engine.get_vae_decode_oom_fallback()
    assert info is not None
    assert info["disabled_tiled"] is True
    assert info["to_tile"] == 0
    assert info["to_overlap"] == 0
    # 第三次调用退回非 tiled
    assert engine.vae.calls[-1]["tiled"] is False


def test_no_oom_returns_none():
    engine = DummyPipeline(oom_times=0)
    engine._vae_decode(_latents())
    assert engine.get_vae_decode_oom_fallback() is None
