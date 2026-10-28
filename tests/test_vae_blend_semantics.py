"""VAE tile 融合语义哨兵（ADR-0006）。

守住三件事，防止「混合可切换」这个谎报再次长回来：

1. 引擎产出的 `_vae_tiled_config` 不得再包含 `gaussian_blend` /
   `groupnorm_accumulate` —— 它们没有消费方，留着只会让日志骗人；
2. 解码路径必须真的以 `tiled=True` + 非零 overlap 调用 VAE，
   否则上游 `tiled_decode` 的 raised-cosine 融合不会生效（接缝全靠它）；
3. `vae_tiled_enhance` 里的 Gaussian 分支仍是未接线死代码，
   不得被引擎引用（引用即意味着有人重新接上了它却没写 ADR）。
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENGINE = ROOT / "app/integrated_app/engines/seedvr2_engine.py"
VAE_PIPELINE = ROOT / "app/integrated_app/engines/_vae_pipeline.py"
ENHANCE = ROOT / "app/integrated_app/optimization/inference/vae_tiled_enhance.py"
UPSTREAM_VAE = ROOT / "model_lib/video_vae_v3/modules/attn_video_vae.py"


def test_engine_tiled_config_has_no_lying_keys():
    text = ENGINE.read_text(encoding="utf-8")
    # 允许出现在注释里（解释为何移除），但不允许作为字典键赋值
    for key in ("gaussian_blend", "groupnorm_accumulate"):
        pattern = re.compile(rf'"{key}"\s*:')
        assert not pattern.search(text), f"{key} 又回到了 _vae_tiled_config（无消费方，属谎报）"


def test_vae_decode_passes_tiled_and_overlap():
    """融合生效的前提：decode 收到 tiled=True 且 tile_overlap > 0。"""
    text = VAE_PIPELINE.read_text(encoding="utf-8")
    assert "tiled=True" in text, "解码未走 tiled 路径，接缝融合不会生效"
    assert "tile_overlap=(current_tile_overlap" in text, "未传 tile_overlap，融合窗口为 0"
    # overlap 必须有下限兜底，否则 OOM 回退可能把它降到 0
    assert re.search(r"current_tile_overlap = max\(", text), "overlap 无下限保护"


def test_upstream_tiled_decode_has_raised_cosine_ramp():
    """上游融合的代码证据：ADR-0006 的结论依赖这一行。"""
    text = UPSTREAM_VAE.read_text(encoding="utf-8")
    assert "def tiled_decode" in text, "上游 VAE 不再提供 tiled_decode，ADR-0006 需重评"
    assert "0.5 - 0.5 * torch.cos" in text, "未找到 raised-cosine 斜坡，接缝融合可能已被移除"


def test_gaussian_hook_is_not_wired_into_engine():
    """死代码可以留着，但引擎不得 import / 实例化它。"""
    engine_text = ENGINE.read_text(encoding="utf-8")
    pipeline_text = VAE_PIPELINE.read_text(encoding="utf-8")
    for name in ("TiledVAEHook", "GroupNormAccumulator"):
        for label, text in (("engine", engine_text), ("vae_pipeline", pipeline_text)):
            # 注释里提到名字是允许的（解释历史），import/实例化不允许
            code_lines = [ln for ln in text.splitlines() if name in ln and not ln.lstrip().startswith("#")]
            assert code_lines == [], f"{label} 里重新接线了 {name}: {code_lines[:2]}"


def test_dead_hook_class_still_documents_why():
    """死代码自身必须继续带着「未接线」说明，否则下一个人会以为它能用。"""
    source = ENHANCE.read_text(encoding="utf-8")
    idx = source.find("class TiledVAEHook")
    assert idx != -1, "TiledVAEHook 已被删除（若为有意清理，请同步更新本测试）"
    head = source[idx : idx + 1200]
    assert "未接线" in head, "TiledVAEHook 丢失了未接线说明"
