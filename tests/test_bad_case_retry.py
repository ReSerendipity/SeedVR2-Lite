"""bad_case_retry.py 单元测试。

验证失败分类、参数调整、重试逻辑的纯函数行为，
不依赖 GPU/Engine 实例。
"""

from __future__ import annotations

import dataclasses
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.integrated_app.bad_case_retry import (
    FailureType,
    RetryConfig,
    adjust_params_for_retry,
    classify_failure,
    retry_with_bad_case_detection,
)

# 抑制测试中的日志噪音
logging.getLogger("app.integrated_app.bad_case_retry").setLevel(logging.CRITICAL)


@pytest.fixture(autouse=True)
def _fast_backoff(monkeypatch):
    """将退避等待替换为空操作，加速测试。"""

    async def _noop(_delay):
        return None

    monkeypatch.setattr("app.integrated_app.bad_case_retry.asyncio.sleep", _noop)


# ---------------------------------------------------------------------------
# 测试用 dataclass（模拟 ImageInferenceConfig）
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class _FakeConfig:
    blocks_to_swap: int = 32
    resolution: int = 2160
    seed: int = -1
    dit_model: str = "3b_fp16"
    max_resolution: int = 0
    encode_tile_size: int = 1024
    encode_tile_overlap: int = 512
    decode_tile_size: int = 1024
    decode_tile_overlap: int = 512


# ---------------------------------------------------------------------------
# classify_failure
# ---------------------------------------------------------------------------


class TestClassifyFailure:
    def test_oom_cuda_out_of_memory(self):
        has_failure, ftype, _reason = classify_failure(message="CUDA out of memory")
        assert has_failure
        assert ftype == FailureType.OOM

    def test_oom_runtime_error_cuda(self):
        has_failure, ftype, _reason = classify_failure(error=RuntimeError("RuntimeError: CUDA error: out of memory"))
        assert has_failure
        assert ftype == FailureType.OOM

    def test_oom_from_exception_type_name(self):
        has_failure, ftype, _reason = classify_failure(error=MemoryError("alloc failed"))
        assert has_failure
        assert ftype == FailureType.OOM

    def test_network_connection_timeout(self):
        has_failure, ftype, _reason = classify_failure(message="Connection timed out")
        assert has_failure
        assert ftype == FailureType.NETWORK

    def test_network_socket_error(self):
        has_failure, ftype, _reason = classify_failure(error=ConnectionError("socket error: broken pipe"))
        assert has_failure
        assert ftype == FailureType.NETWORK

    def test_cancelled_by_user(self):
        has_failure, ftype, _reason = classify_failure(message="InferenceCancelledError")
        assert not has_failure
        assert ftype == FailureType.CANCELLED

    def test_unknown_error(self):
        has_failure, ftype, _reason = classify_failure(message="Something went wrong")
        assert has_failure
        assert ftype == FailureType.UNKNOWN

    def test_empty_message(self):
        has_failure, ftype, _reason = classify_failure(message="")
        assert has_failure
        assert ftype == FailureType.UNKNOWN


# ---------------------------------------------------------------------------
# adjust_params_for_retry
# ---------------------------------------------------------------------------


class TestAdjustParams:
    cfg = RetryConfig()

    def test_oom_first_attempt_increases_blocks_to_swap(self):
        params = {"config": _FakeConfig(blocks_to_swap=32)}
        adjusted = adjust_params_for_retry(params, FailureType.OOM, 1, self.cfg)
        cfg = adjusted["config"]
        assert cfg.blocks_to_swap == 36  # 32 + 4
        # 第一次重试不动 tile / resolution（阶梯顺序：blockswap 优先）
        assert cfg.encode_tile_size == 1024
        assert cfg.resolution == 2160

    def test_oom_second_attempt_decreases_tile(self):
        """P0-2：第二次重试先缩 VAE tile（size 与 overlap 同步缩）。"""
        params = {"config": _FakeConfig(blocks_to_swap=32, resolution=2160)}
        adjusted = adjust_params_for_retry(params, FailureType.OOM, 2, self.cfg)
        cfg = adjusted["config"]
        assert cfg.blocks_to_swap == 36
        assert cfg.resolution == 2160  # 分辨率档在 tile 之后，本次不动
        assert cfg.encode_tile_size == 512  # 1024 * 0.5
        assert cfg.encode_tile_overlap == 128  # min(512, 512//4)
        assert cfg.decode_tile_size == 512
        assert cfg.decode_tile_overlap == 128

    def test_oom_third_attempt_reduces_resolution(self):
        params = {"config": _FakeConfig(blocks_to_swap=32, resolution=2160, dit_model="3b_fp16")}
        adjusted = adjust_params_for_retry(params, FailureType.OOM, 3, self.cfg)
        cfg = adjusted["config"]
        assert cfg.blocks_to_swap == 36
        assert cfg.encode_tile_size == 512
        assert cfg.resolution == 1620  # 2160 * 0.75 = 1620
        assert cfg.dit_model == "3b_fp16"  # 精度档还要再晚一档

    def test_oom_fourth_attempt_falls_back_precision(self):
        params = {"config": _FakeConfig(blocks_to_swap=32, resolution=2160, dit_model="3b_fp16")}
        adjusted = adjust_params_for_retry(params, FailureType.OOM, 4, self.cfg)
        cfg = adjusted["config"]
        assert cfg.blocks_to_swap == 36
        assert cfg.encode_tile_size == 512
        assert cfg.resolution == 1620
        assert cfg.dit_model == "3b_fp8"

    def test_oom_without_config_uses_top_level(self):
        params = {"blocks_to_swap": 32, "resolution": 2048}
        adjusted = adjust_params_for_retry(params, FailureType.OOM, 3, self.cfg)
        assert adjusted["blocks_to_swap"] == 36
        assert adjusted["resolution"] == 1536

    def test_oom_flat_params_tile_decrease(self):
        """视频/批量链路走扁平 dict：tile 键存在时同样降档。"""
        params = {
            "blocks_to_swap": 32,
            "resolution": 2048,
            "encode_tile_size": 1024,
            "encode_tile_overlap": 512,
            "decode_tile_size": 1024,
            "decode_tile_overlap": 512,
        }
        adjusted = adjust_params_for_retry(params, FailureType.OOM, 2, self.cfg)
        assert adjusted["encode_tile_size"] == 512
        assert adjusted["encode_tile_overlap"] == 128
        assert adjusted["decode_tile_size"] == 512
        assert adjusted["decode_tile_overlap"] == 128
        assert adjusted["resolution"] == 2048  # 本次不降分辨率

    def test_tile_decrease_floors_at_min(self):
        params = {"config": _FakeConfig(encode_tile_size=256, encode_tile_overlap=64)}
        adjusted = adjust_params_for_retry(params, FailureType.OOM, 2, self.cfg)
        assert adjusted["config"].encode_tile_size == 256  # 已在下限，不再缩
        assert adjusted["config"].encode_tile_overlap == 64

    def test_tile_decrease_can_be_disabled(self):
        cfg = RetryConfig(enable_tile_decrease=False)
        params = {"config": _FakeConfig(blocks_to_swap=36)}
        adjusted = adjust_params_for_retry(params, FailureType.OOM, 2, cfg)
        assert adjusted["config"].encode_tile_size == 1024
        assert adjusted["config"].resolution == 2160

    def test_network_does_not_degrade_params(self):
        params = {"config": _FakeConfig(blocks_to_swap=32, resolution=2160)}
        adjusted = adjust_params_for_retry(params, FailureType.NETWORK, 2, self.cfg)
        cfg = adjusted["config"]
        assert cfg.blocks_to_swap == 32  # 不变
        assert cfg.resolution == 2160  # 不变

    def test_seed_rotation(self):
        params = {"config": _FakeConfig(seed=42)}
        adjusted = adjust_params_for_retry(params, FailureType.NETWORK, 1, self.cfg)
        assert adjusted["config"].seed != 42

    def test_seed_rotation_random_when_minus_one(self):
        params = {"config": _FakeConfig(seed=-1)}
        adjusted = adjust_params_for_retry(params, FailureType.NETWORK, 1, self.cfg)
        assert adjusted["config"].seed >= 0  # 换成了随机正数

    def test_blocks_to_swap_capped(self):
        params = {"config": _FakeConfig(blocks_to_swap=35)}
        adjusted = adjust_params_for_retry(params, FailureType.OOM, 1, self.cfg)
        assert adjusted["config"].blocks_to_swap == 36  # capped at max

    def test_unknown_does_not_degrade(self):
        params = {"config": _FakeConfig(blocks_to_swap=32, resolution=2160)}
        adjusted = adjust_params_for_retry(params, FailureType.UNKNOWN, 2, self.cfg)
        assert adjusted["config"].blocks_to_swap == 32
        assert adjusted["config"].resolution == 2160

    def test_degradation_disabled_when_config_false(self):
        cfg = RetryConfig(enable_degradation=False)
        params = {"config": _FakeConfig(blocks_to_swap=32, resolution=2160)}
        adjusted = adjust_params_for_retry(params, FailureType.OOM, 2, cfg)
        assert adjusted["config"].blocks_to_swap == 32
        assert adjusted["config"].resolution == 2160


# ---------------------------------------------------------------------------
# retry_with_bad_case_detection（异步）
# ---------------------------------------------------------------------------


class TestRetryLoop:
    @pytest.mark.asyncio
    async def test_success_on_first_attempt(self):
        mock_result = MagicMock(success=True)
        mock_fn = AsyncMock(return_value=mock_result)
        result = await retry_with_bad_case_detection(mock_fn, {"x": 1})
        assert result.success is True
        assert result.attempts == 1
        assert result.degraded is False
        mock_fn.assert_called_once_with(x=1)

    @pytest.mark.asyncio
    async def test_retries_on_failure_then_succeeds(self):
        mock_fn = AsyncMock()
        mock_fn.side_effect = [
            MagicMock(success=False, error="CUDA out of memory"),
            MagicMock(success=True),
        ]
        result = await retry_with_bad_case_detection(mock_fn, {"config": _FakeConfig()})
        assert result.success
        assert result.attempts == 2
        assert result.degraded is True

    @pytest.mark.asyncio
    async def test_retries_on_exception_then_succeeds(self):
        mock_fn = AsyncMock()
        mock_fn.side_effect = [
            RuntimeError("CUDA out of memory: no available memory"),
            MagicMock(success=True),
        ]
        result = await retry_with_bad_case_detection(mock_fn, {"x": 1})
        assert result.success
        assert result.attempts == 2

    @pytest.mark.asyncio
    async def test_graceful_degradation_when_retries_exhausted(self):
        mock_fn = AsyncMock(return_value=MagicMock(success=False, error="CUDA OOM"))
        result = await retry_with_bad_case_detection(mock_fn, {"x": 1}, RetryConfig(max_retries=2))
        assert result.success is True  # 优雅降级
        assert result.attempts == 3
        assert result.degraded is True
        assert "重试耗尽" in result.failure_reason

    @pytest.mark.asyncio
    async def test_all_attempts_exception_returns_failure(self):
        mock_fn = AsyncMock(side_effect=RuntimeError("Something fatal"))
        result = await retry_with_bad_case_detection(mock_fn, {"x": 1}, RetryConfig(max_retries=2))
        assert result.success is False
        assert result.attempts == 3

    @pytest.mark.asyncio
    async def test_no_retry_on_cancelled_error(self):
        mock_fn = AsyncMock(return_value=MagicMock(success=False, error="InferenceCancelledError"))
        result = await retry_with_bad_case_detection(mock_fn, {"x": 1})
        assert result.success is False
        assert result.attempts == 1

    @pytest.mark.asyncio
    async def test_cancelled_exception_propagates_without_retry(self):
        mock_fn = AsyncMock(side_effect=RuntimeError("task cancelled by user"))
        with pytest.raises(RuntimeError):
            await retry_with_bad_case_detection(mock_fn, {"x": 1})
        assert mock_fn.await_count == 1  # 未重试

    @pytest.mark.asyncio
    async def test_network_error_retries_without_degradation(self):
        mock_fn = AsyncMock()
        mock_fn.side_effect = [
            MagicMock(success=False, error="Connection timed out"),
            MagicMock(success=True),
        ]
        result = await retry_with_bad_case_detection(mock_fn, {"x": 1})
        assert result.success
        assert result.attempts == 2
        assert result.degraded is False  # 网络错误不降级

    @pytest.mark.asyncio
    async def test_result_without_success_attr_treated_as_success(self):
        mock_fn = AsyncMock(return_value={"data": "ok"})
        result = await retry_with_bad_case_detection(mock_fn, {"x": 1})
        assert result.success is True
        assert result.result == {"data": "ok"}


if __name__ == "__main__":
    pytest.main([__file__, "-q", "-p", "no:cacheprovider"])


# ---------------------------------------------------------------------------
# 评估 P2-2：系统内存守卫错误分类与不重试语义
# ---------------------------------------------------------------------------


class TestRamGuardClassification:
    """_memory_utils 内存守卫的 MemoryError 必须归类为 RAM（不可重试）"""

    def test_ram_guard_usage_message(self):
        msg = "内存使用率 96.8% 超过阈值 95%，可用: 1.0GB / 31.7GB。必须立即终止模型！"
        has_failure, ftype, _reason = classify_failure(message=msg)
        assert has_failure is True
        assert ftype == FailureType.RAM

    def test_ram_guard_memory_error_exception_not_oom(self):
        """守卫异常类名为 MemoryError（命中 OOM 关键词），RAM 判定必须先行"""
        err = MemoryError("内存使用率 96.8% 已超过阈值 95%，无法加载 DiT (4.5GB)。可用: 1.0GB")
        has_failure, ftype, _reason = classify_failure(error=err)
        assert has_failure is True
        assert ftype == FailureType.RAM

    def test_plain_memory_error_still_oom(self):
        """普通 MemoryError（非守卫文案）保持 OOM 分类不回归"""
        has_failure, ftype, _reason = classify_failure(error=MemoryError("alloc failed"))
        assert has_failure is True
        assert ftype == FailureType.OOM

    def test_ram_no_degradation_on_adjust(self):
        """RAM 类型不做 OOM 降级阶梯（blockswap 会加剧系统内存压力）"""
        params = {"blocks_to_swap": 0, "resolution": 2160, "seed": 42}
        adjusted = adjust_params_for_retry(params, FailureType.RAM, attempt=2)
        assert adjusted["blocks_to_swap"] == 0
        assert adjusted["resolution"] == 2160


class TestRamGuardNoRetry:
    """RAM 守卫失败在重试主循环中必须立即终止（不白烧重试）"""

    @pytest.mark.asyncio
    async def test_result_failure_ram_no_retry(self):
        calls = {"n": 0}

        async def _generate(**_kwargs):
            calls["n"] += 1
            return MagicMock(
                success=False, error="内存使用率 96.8% 超过阈值 95%，可用: 1.0GB / 31.7GB。必须立即终止模型！"
            )

        result = await retry_with_bad_case_detection(_generate, {"config": None})
        assert result.success is False
        assert result.attempts == 1
        assert calls["n"] == 1
        assert "系统内存耗尽" in result.failure_reason

    @pytest.mark.asyncio
    async def test_exception_ram_no_retry(self):
        calls = {"n": 0}

        async def _generate(**_kwargs):
            calls["n"] += 1
            raise MemoryError("可用内存 0.8GB 低于下限 2.0GB，无法加载 VAE")

        result = await retry_with_bad_case_detection(_generate, {"config": None})
        assert result.success is False
        assert result.attempts == 1
        assert calls["n"] == 1

    @pytest.mark.asyncio
    async def test_oom_still_retries(self):
        """OOM（GPU 显存）保持原有重试语义不回归"""
        mock_fn = AsyncMock(
            side_effect=[
                MagicMock(success=False, error="CUDA out of memory"),
                MagicMock(success=True),
            ]
        )
        result = await retry_with_bad_case_detection(mock_fn, {"config": None})
        assert result.success is True
        assert result.attempts == 2
