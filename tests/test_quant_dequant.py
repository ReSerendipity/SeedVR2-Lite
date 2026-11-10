"""Comfy-Org 量化权重加载期反量化测试（int8_convrot / mxfp8 / nvfp4）。

诚实边界：本文件用**合成量化数据**验证 dequant 函数的数学自洽与 dispatch 行为；
真实权重上的格式约定（nibble 序 / swizzle / 旋转核）需下载 Comfy-Org 包做真机验证，
步骤记录在维护者本地目录 docs/plans/ 的交接文档里（未随仓库分发，可在 Issue 中索取）。
"""

import json

import pytest

torch = pytest.importorskip("torch")

from app.integrated_app.engines.quant_dequant import (  # noqa: E402
    build_hadamard,
    decode_comfy_quant,
    dequantize_int8_convrot,
    dequantize_mxfp8,
    dequantize_nvfp4,
    dequantize_state_dict,
    from_blocked,
    to_blocked,
)

# ---------------------------------------------------------------------------
# 测试端量化器（与 ComfyUI 前向语义一致，独立于被测的反量化实现）
# ---------------------------------------------------------------------------

_E2M1_VALUES = [0.0, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0]


def _fake_quantize_int8_convrot(w: torch.Tensor, groupsize: int = 256):
    """int8 逐行量化 + 分组 Hadamard 旋转（W_rot = W @ H^T）。"""
    n, k = w.shape
    h = build_hadamard(groupsize, dtype=torch.float32)
    w_rot = (w.reshape(n, k // groupsize, groupsize) @ h.T).reshape(n, k)
    scale = w_rot.abs().amax(dim=1, keepdim=True) / 127.0
    q = torch.round(w_rot / scale).clamp(-127, 127).to(torch.int8)
    return q, scale


def _fake_quantize_mxfp8(x: torch.Tensor):
    """MXFP8：E8M0 偏置指数块缩放（32 一块）+ to_blocked swizzle 存储。"""
    r, c = x.shape
    xb = x.reshape(r, c // 32, 32)
    max_abs = xb.abs().amax(dim=-1)
    needed = (max_abs.float() / 448.0).clamp(min=2**-127)
    exp_biased = (needed.log2().ceil().to(torch.int32) + 127).clamp(0, 254)
    scales = (exp_biased << 23).view(torch.float32)
    data = (xb.float() / scales.unsqueeze(-1)).reshape(r, c).to(torch.float8_e4m3fn)
    scale_lin = exp_biased.to(torch.uint8).reshape(r, c // 32)
    return data, to_blocked(scale_lin.float()).to(torch.uint8)


def _fake_quantize_nvfp4(x: torch.Tensor):
    """NVFP4：nibble 打包（偶数元素高 4 位）× e4m3 块缩放（16 一块）× 全局标量。"""
    r, c = x.shape
    global_scale = x.abs().amax() / (448.0 * 6.0)
    xb = x.reshape(r, c // 16, 16)
    block_scale = (xb.abs().amax(dim=-1) / 6.0 / global_scale).clamp(max=448.0).to(torch.float8_e4m3fn)
    scaled = xb.float() / (global_scale * block_scale.float()).unsqueeze(-1)
    table = torch.tensor(_E2M1_VALUES + [-v for v in _E2M1_VALUES], dtype=torch.float32)
    codes = (scaled.reshape(r, c).unsqueeze(-1) - table).abs().argmin(dim=-1).to(torch.uint8)
    packed = (codes[:, 0::2] << 4) | codes[:, 1::2]
    return packed, to_blocked(block_scale.float().reshape(r, c // 16)), global_scale


# ---------------------------------------------------------------------------
# Hadamard / swizzle 基础
# ---------------------------------------------------------------------------


class TestHadamard:
    def test_normalized_orthogonal(self):
        h = build_hadamard(256)
        eye = torch.eye(256)
        assert torch.allclose(h @ h.T, eye, atol=1e-5)

    def test_symmetric(self):
        """h4 核对称 → Sylvester 乘积对称，反旋转可与正旋转同用。"""
        h = build_hadamard(64)
        assert torch.allclose(h, h.T, atol=1e-6)

    def test_rejects_non_power_of_4(self):
        with pytest.raises(ValueError, match="power of 4"):
            build_hadamard(8)


class TestBlockedSwizzle:
    @pytest.mark.parametrize("shape", [(64, 4), (128, 8), (256, 16), (100, 5), (64, 40), (32, 1)])
    def test_roundtrip_various_shapes(self, shape):
        m = torch.randn(*shape)
        assert torch.allclose(from_blocked(to_blocked(m), *shape), m)

    def test_swizzle_actually_permutes(self):
        """swizzle 不是恒等变换（防呆：若退化为直通则往返测试无意义）。"""
        m = torch.arange(128 * 8, dtype=torch.float32).view(128, 8)
        assert not torch.equal(to_blocked(m), m)


# ---------------------------------------------------------------------------
# 三种格式合成往返
# ---------------------------------------------------------------------------


class TestDequantInt8Convrot:
    def test_roundtrip_error_near_quantization_noise(self):
        torch.manual_seed(0)
        w = torch.randn(128, 256)
        q, s = _fake_quantize_int8_convrot(w)
        rec = dequantize_int8_convrot(q, s, convrot=True, groupsize=256)
        err = (rec - w).abs().max().item()
        assert rec.shape == w.shape
        assert err < 0.1, f"int8 往返误差过大: {err}"

    def test_no_convrot_variant(self):
        torch.manual_seed(1)
        w = torch.randn(64, 128)
        scale = w.abs().amax(dim=1, keepdim=True) / 127.0
        q = torch.round(w / scale).clamp(-127, 127).to(torch.int8)
        rec = dequantize_int8_convrot(q, scale, convrot=False)
        assert (rec - w).abs().max().item() < 0.05

    def test_groupsize_must_divide_in_features(self):
        q = torch.zeros(4, 100, dtype=torch.int8)
        with pytest.raises(ValueError, match="not divisible"):
            dequantize_int8_convrot(q, torch.ones(4, 1), convrot=True, groupsize=256)


class TestDequantMxfp8:
    def test_roundtrip_relative_error(self):
        torch.manual_seed(2)
        x = torch.randn(64, 128)
        data, scale = _fake_quantize_mxfp8(x)
        rec = dequantize_mxfp8(data, scale)
        assert rec.shape == x.shape
        rel = (rec - x).abs().max().item() / x.abs().max().item()
        assert rel < 0.1, f"mxfp8 往返相对误差过大: {rel}"

    def test_e8m0_boundary_bytes(self):
        """E8M0 字节 127→2^0=1，0 视为 0（ComfyUI 零块约定）。"""
        data = torch.ones(128, 32, dtype=torch.float8_e4m3fn)  # 128 行满足 swizzle 行块
        scale_lin = torch.full((128, 1), 127, dtype=torch.uint8)
        rec = dequantize_mxfp8(data, to_blocked(scale_lin.float()).to(torch.uint8))
        assert torch.allclose(rec, torch.ones(128, 32), atol=1e-6)
        zero_lin = torch.zeros((128, 1), dtype=torch.uint8)
        rec0 = dequantize_mxfp8(data, to_blocked(zero_lin.float()).to(torch.uint8))
        assert torch.isfinite(rec0).all()


class TestDequantNvfp4:
    def test_roundtrip_relative_error(self):
        torch.manual_seed(3)
        x = torch.randn(64, 128)
        packed, scale, g = _fake_quantize_nvfp4(x)
        rec = dequantize_nvfp4(packed, scale, g)
        assert rec.shape == x.shape
        rel = (rec - x).abs().max().item() / x.abs().max().item()
        # E2M1 仅 8 个幅值档，4-bit 量化粗糙度 ~10% 属正常
        assert rel < 0.2, f"nvfp4 往返相对误差过大: {rel}"

    def test_nibble_order_even_high(self):
        """packed=(even<<4)|odd：高 nibble 必须还原到偶数列（逻辑列需为 16 的倍数）。"""
        codes = torch.tensor([_E2M1_VALUES.index(1.0), 7] * 8, dtype=torch.uint8).repeat(32, 1)
        codes = (codes + torch.arange(16, dtype=torch.uint8) % 16).clamp(0, 15)
        packed = (codes[:, 0::2] << 4) | codes[:, 1::2]
        scale = to_blocked(torch.ones(32, 1))
        g = torch.tensor(1.0)
        rec = dequantize_nvfp4(packed, scale.to(torch.float8_e4m3fn), g)
        table = torch.tensor(_E2M1_VALUES + [-t for t in _E2M1_VALUES], dtype=torch.float32)
        assert rec.shape == (32, 16)
        assert torch.allclose(rec[:, 0], table[codes[:, 0].long()], atol=1e-6)
        assert torch.allclose(rec[:, 1], table[codes[:, 1].long()], atol=1e-6)
        assert torch.allclose(rec[:, 14], table[codes[:, 14].long()], atol=1e-6)


# ---------------------------------------------------------------------------
# comfy_quant 元数据与 state_dict dispatch
# ---------------------------------------------------------------------------


def _cq_tensor(payload: dict) -> torch.Tensor:
    return torch.tensor(list(json.dumps(payload).encode("utf-8")), dtype=torch.uint8)


class TestDecodeComfyQuant:
    def test_valid_payloads(self):
        assert decode_comfy_quant(_cq_tensor({"format": "nvfp4"})) == {"format": "nvfp4"}
        obj = decode_comfy_quant(_cq_tensor({"format": "int8_tensorwise", "convrot": True, "convrot_groupsize": 256}))
        assert obj["convrot"] is True

    def test_garbage_returns_none(self):
        assert decode_comfy_quant(torch.tensor([255, 254, 253], dtype=torch.uint8)) is None


class TestDequantizeStateDict:
    def test_dispatch_int8(self):
        torch.manual_seed(4)
        w = torch.randn(64, 256)
        q, s = _fake_quantize_int8_convrot(w)
        sd = {
            "blk.fc.comfy_quant": _cq_tensor({"format": "int8_tensorwise", "convrot": True, "convrot_groupsize": 256}),
            "blk.fc.weight": q,
            "blk.fc.weight_scale": s,
            "blk.fc.bias": torch.randn(64),
        }
        assert dequantize_state_dict(sd) == 1
        assert "blk.fc.comfy_quant" not in sd and "blk.fc.weight_scale" not in sd
        assert sd["blk.fc.weight"].shape == (64, 256)
        assert sd["blk.fc.weight"].dtype == torch.float32
        assert (sd["blk.fc.weight"] - w).abs().max().item() < 0.1

    def test_dispatch_nvfp4_consumes_scale2(self):
        torch.manual_seed(5)
        x = torch.randn(32, 128)
        packed, scale, g = _fake_quantize_nvfp4(x)
        sd = {
            "blk.fc.comfy_quant": _cq_tensor({"format": "nvfp4"}),
            "blk.fc.weight": packed,
            "blk.fc.weight_scale": scale,
            "blk.fc.weight_scale_2": g,
        }
        assert dequantize_state_dict(sd) == 1
        assert "blk.fc.weight_scale_2" not in sd
        assert sd["blk.fc.weight"].shape == (32, 128)

    def test_plain_state_dict_noop(self):
        """无 comfy_quant 键（numz 权重）时静默返回 0，不改动任何张量。"""
        sd = {"a.weight": torch.randn(8, 8), "b.weight": torch.randn(8, 8)}
        before = {k: v.clone() for k, v in sd.items()}
        assert dequantize_state_dict(sd) == 0
        assert set(sd) == set(before) and all(torch.equal(sd[k], before[k]) for k in sd)

    def test_unknown_format_raises(self):
        sd = {
            "blk.fc.comfy_quant": _cq_tensor({"format": "int4_something"}),
            "blk.fc.weight": torch.zeros(4, 8, dtype=torch.int8),
            "blk.fc.weight_scale": torch.ones(4, 1),
        }
        with pytest.raises(ValueError, match="不支持的 comfy_quant 格式"):
            dequantize_state_dict(sd)

    def test_missing_weight_raises(self):
        sd = {"blk.fc.comfy_quant": _cq_tensor({"format": "mxfp8"})}
        with pytest.raises(ValueError, match="缺失"):
            dequantize_state_dict(sd)


# ---------------------------------------------------------------------------
# 下载脚本：精度→文件名清单 与 双源路由（只测纯逻辑，不发网络）
# ---------------------------------------------------------------------------


class TestDownloadRouting:
    def test_source_routing(self):
        from scripts.download_model import (
            _DEFAULT_REPO,
            _comfy_remote_name,
            _is_comfy_org,
            _resolve_source,
        )

        # 2026-11-01 定案：默认源即官方 Comfy-Org 仓；ema_ 命名的 fp16/fp8 也走官方，
        # 仓内名去掉 ema_ 前缀（seedvr2_ema_3b_fp16 → seedvr2_3b_fp16）。
        assert _DEFAULT_REPO == "Comfy-Org/SeedVR2"
        assert _resolve_source("seedvr2_ema_3b_fp16.safetensors", _DEFAULT_REPO) == (
            "Comfy-Org/SeedVR2",
            "diffusion_models",
        )
        assert _comfy_remote_name("seedvr2_ema_3b_fp16.safetensors") == "seedvr2_3b_fp16.safetensors"
        # VAE 走 vae/ 子目录且保持原名（官方仓里两种命名都存在）
        assert _resolve_source("ema_vae_fp16.safetensors", _DEFAULT_REPO) == (
            "Comfy-Org/SeedVR2",
            "vae",
        )
        assert _comfy_remote_name("seedvr2_ema_vae_fp16.safetensors") == ("seedvr2_ema_vae_fp16.safetensors")
        # 非 seedvr2 组件仍走调用方指定仓
        assert _resolve_source("qwen_component.safetensors", "some/repo") == ("some/repo", None)
        repo, sub = _resolve_source("seedvr2_3b_nvfp4.safetensors", _DEFAULT_REPO)
        assert (repo, sub) == ("Comfy-Org/SeedVR2", "diffusion_models")
        assert not _is_comfy_org("ema_vae_fp16.safetensors")

    def test_checkpoint_files_from_config(self):
        from pathlib import Path

        from scripts.download_model import _load_checkpoint_files

        cfg = Path(__file__).resolve().parent.parent / "config.yaml"
        quant = _load_checkpoint_files(cfg, "3b", ["int8_convrot", "mxfp8", "nvfp4"])
        assert quant == [
            "seedvr2_3b_int8_convrot.safetensors",
            "seedvr2_3b_mxfp8.safetensors",
            "seedvr2_3b_nvfp4.safetensors",
        ]
        all5 = _load_checkpoint_files(cfg, "7b_sharp", ["fp16", "fp8", "int8_convrot", "mxfp8", "nvfp4"])
        assert len(all5) == 5
        assert all5[0] == "seedvr2_ema_7b_sharp_fp16.safetensors"
        with pytest.raises(ValueError, match="未知精度"):
            _load_checkpoint_files(cfg, "3b", ["int4"])

    def test_expected_hashes_cover_quant_precisions(self):
        """config.yaml 新量化哈希必须进入「文件名→期望哈希」映射，否则校验静默跳过。"""
        from pathlib import Path

        from scripts.download_model import _load_expected_hashes

        cfg = Path(__file__).resolve().parent.parent / "config.yaml"
        hm = _load_expected_hashes(cfg)
        for f in (
            "seedvr2_3b_int8_convrot.safetensors",
            "seedvr2_3b_mxfp8.safetensors",
            "seedvr2_3b_nvfp4.safetensors",
            "seedvr2_7b_sharp_nvfp4.safetensors",
        ):
            assert f in hm and len(hm[f]) == 64, f


# ---------------------------------------------------------------------------
# spec：dit_model → 精度解析（含多下划线精度）
# ---------------------------------------------------------------------------


class TestPrecisionFromDitModel:
    @pytest.mark.parametrize(
        ("dit_model", "expected"),
        [
            ("3b_fp16", "fp16"),
            ("3b_fp8", "fp8"),
            ("3b_int8_convrot", "int8_convrot"),
            ("7b_mxfp8", "mxfp8"),
            ("7b_sharp_fp16", "fp16"),
            ("7b_sharp_int8_convrot", "int8_convrot"),
            ("7b_sharp_nvfp4", "nvfp4"),
            ("3b", None),
            ("", None),
            ("99b_fp16", "fp16"),  # 未知尺寸前缀仍按「剥离尺寸段」解析
            ("3b_int4", None),
        ],
    )
    def test_parse(self, dit_model, expected):
        from app.integrated_app.spec import precision_from_dit_model

        assert precision_from_dit_model(dit_model) == expected
