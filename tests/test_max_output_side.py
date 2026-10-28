"""输出长边服务端硬上限（inference.max_output_side）回归测试。

背景：SeedVR2 的 ``resolution`` 由客户端决定，``max_resolution`` 只是"请求侧"
的上限（0=不限）。客户端传 ``resolution=20000`` 时，服务端没有任何拦截点——
请求会被接受、加载模型、然后必然 OOM，且已经烧掉几十分钟 GPU 时间。
本配置项是**服务端兜底**（对齐 VOSR2-Lite 的同名项）。

覆盖：
- 配置解析（缺省 6144 / 0 表示不限制 / 非法值回退）
- 超限拒绝（400，消息带可用上限）
- 边界放行（等于上限）
- max_resolution 被动钳位（只减不增，不拒绝请求）
- 两倍模式算出的分辨率同样受约束
- 走真实 API 依赖链时确实在推理前被拒
"""

import io

import pytest
from fastapi import HTTPException
from PIL import Image
from pydantic import ValidationError

from app.integrated_app.config_models import InferenceConfig, UnifiedRestoreParams
from app.integrated_app.routes.restore import common

CAP_CFG = {"inference": {"max_output_side": 6144}}


def _params(**kwargs) -> UnifiedRestoreParams:
    base = {"task_type": "image", "resolution": 2048}
    base.update(kwargs)
    return UnifiedRestoreParams(**base)


# ── 配置解析 ────────────────────────────────────────────────────────────────
def test_resolve_default_when_missing():
    assert common.resolve_max_output_side({}) == 6144
    assert common.resolve_max_output_side({"inference": {}}) == 6144


def test_resolve_zero_means_unlimited():
    assert common.resolve_max_output_side({"inference": {"max_output_side": 0}}) == 0


def test_resolve_negative_treated_as_unlimited():
    """负数没有意义，按 0（不限制）处理而不是崩掉。"""
    assert common.resolve_max_output_side({"inference": {"max_output_side": -1}}) == 0


@pytest.mark.parametrize("bad", ["abc", None, [], {}])
def test_resolve_invalid_falls_back_to_default(bad):
    assert common.resolve_max_output_side({"inference": {"max_output_side": bad}}) == 6144


def test_config_model_defaults_and_validation():
    assert InferenceConfig().max_output_side == 6144
    assert InferenceConfig(max_output_side=0).max_output_side == 0
    with pytest.raises(ValidationError):
        InferenceConfig(max_output_side=-1)  # ge=0


# ── 超限拒绝 ────────────────────────────────────────────────────────────────
def test_rejects_resolution_over_cap():
    params = _params(resolution=8000)
    with pytest.raises(HTTPException) as exc:
        common.enforce_max_output_side(params, CAP_CFG)
    assert "6144" in str(exc.value)
    assert "max_output_side" in str(exc.value)


def test_allows_exact_cap():
    params = _params(resolution=6144)
    common.enforce_max_output_side(params, CAP_CFG)  # 不抛异常
    assert params.resolution == 6144


def test_allows_below_cap():
    params = _params(resolution=2048)
    common.enforce_max_output_side(params, CAP_CFG)
    assert params.resolution == 2048


def test_disabled_when_zero():
    params = _params(resolution=20000)
    common.enforce_max_output_side(params, {"inference": {"max_output_side": 0}})
    assert params.resolution == 20000


def test_clamps_max_resolution_instead_of_rejecting():
    """max_resolution 是"上限"语义，超过服务端上限时钳位即可，不必拒绝请求。"""
    params = _params(resolution=2048, max_resolution=9999)
    common.enforce_max_output_side(params, CAP_CFG)
    assert params.max_resolution == 6144
    assert params.resolution == 2048


# ── 两倍模式 ────────────────────────────────────────────────────────────────
def test_double_res_resolution_clamped_by_cap(tmp_path):
    """两倍模式算出的分辨率（短边×2）同样受服务端上限约束。"""
    img_path = tmp_path / "in.png"
    Image.new("RGB", (200, 100), (10, 20, 30)).save(img_path)

    params = _params(resolution=999, double_res=True)
    common.enforce_double_resolution_if_enabled(params, "image", str(img_path), 150)
    assert params.resolution == 150  # 原本应为 100*2=200，被上限 150 钳位


def test_double_res_unaffected_when_cap_disabled(tmp_path):
    img_path = tmp_path / "in.png"
    Image.new("RGB", (200, 100), (10, 20, 30)).save(img_path)

    params = _params(resolution=999, double_res=True)
    common.enforce_double_resolution_if_enabled(params, "image", str(img_path), 0)
    assert params.resolution == 200


# ── 真实依赖链：推理前就拒绝 ────────────────────────────────────────────────
def test_api_rejects_huge_resolution_before_inference(test_app):
    """走 FastAPI 依赖解析路径：超限请求必须 400，且不能进入推理。"""
    from tests.conftest import csrf_post

    buf = io.BytesIO()
    Image.new("RGB", (32, 32), (1, 2, 3)).save(buf, "PNG")
    buf.seek(0)

    resp = csrf_post(
        test_app,
        "/api/restore/",
        files={"file": ("t.png", buf, "image/png")},
        data={"resolution": "20000", "task_type": "image"},
    )
    assert resp.status_code == 400, resp.text
    assert "6144" in str(resp.json())
