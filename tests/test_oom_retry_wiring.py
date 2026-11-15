#!/usr/bin/env python3
"""OOM 坏案例自动重试接线单元测试（成本治理 P0-2）。

覆盖评估报告 P0-2 的验收标准：
1. oom_protect 装饰器：CUDA OOM → 强制回收 + MemoryError（携带解决建议）；
   非 OOM 异常原样透传
2. retry_with_bad_case_detection 集成：OOM 后 blocks_to_swap 阶梯上升、
   失败结果透传（不伪装成功）、重试耗尽语义
3. build_retry_config：runtime.retry 配置读取与禁用语义
4. 批量降级 helper：OOM 持久降级到批量级配置，非 OOM 不动参数

所属项目：SeedVR2 (SeedVR2 视频/图像修复工具)
"""

from dataclasses import dataclass

import pytest

from app.integrated_app.bad_case_retry import (
    FailureType,
    RetryConfig,
    classify_failure,
    retry_with_bad_case_detection,
)
from app.integrated_app.gpu_utils import oom_protect
from app.integrated_app.routes.restore.common import build_retry_config
from app.integrated_app.services.restore_service import apply_oom_degradation


@dataclass
class FakeImageConfig:
    """模拟 ImageInferenceConfig 的降级相关字段（dataclasses.replace 目标）。"""

    dit_model: str = "3b_fp16"
    blocks_to_swap: int = 32
    resolution: int = 2048
    seed: int = 42
    force_reload_dit: bool = False


class TestOomProtect:
    """oom_protect 装饰器行为。"""

    @pytest.mark.asyncio
    async def test_cuda_oom_converted_to_memory_error(self):
        @oom_protect
        async def boom():
            raise RuntimeError("CUDA out of memory. Tried to allocate 2.00 GiB")

        with pytest.raises(MemoryError) as exc_info:
            await boom()
        assert "GPU 显存不足" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_non_oom_runtime_error_passthrough(self):
        @oom_protect
        async def boom():
            raise RuntimeError("CUDA error: device-side assert triggered")

        with pytest.raises(RuntimeError, match="device-side assert"):
            await boom()

    @pytest.mark.asyncio
    async def test_success_passthrough(self):
        @oom_protect
        async def ok():
            return "result"

        assert await ok() == "result"


class TestClassifyOomFailure:
    """OOM 关键词分类（含 oom_protect 转换后的 MemoryError 消息）。"""

    def test_torch_oom_message(self):
        has_failure, ftype, _ = classify_failure(message="CUDA out of memory. Tried to allocate...")
        assert has_failure
        assert ftype == FailureType.OOM

    def test_memory_error_message(self):
        has_failure, ftype, _ = classify_failure(error=MemoryError("GPU 显存不足，请尝试：切换到 3B 模型"))
        assert has_failure
        assert ftype == FailureType.OOM

    def test_chinese_oom_result_error_message(self):
        """引擎 result.error 常为中文：仅凭 message 字符串也要能识别 OOM。"""
        has_failure, ftype, _ = classify_failure(message="GPU 显存不足，任务无法继续")
        assert has_failure
        assert ftype == FailureType.OOM

    def test_cancelled_not_retryable(self):
        has_failure, ftype, _ = classify_failure(message="推理已被取消 cancelled")
        assert not has_failure
        assert ftype == FailureType.CANCELLED


@pytest.mark.asyncio
class TestRetryWiring:
    """retry_with_bad_case_detection 与推理入口的集成语义。"""

    async def test_oom_retry_increases_blocks_to_swap(self):
        """首次 OOM → 重试时 blocks_to_swap 阶梯上升并传给生成函数。"""
        seen_params = []

        async def generate_fn(**params):
            seen_params.append(params)
            if len(seen_params) == 1:
                return type("R", (), {"success": False, "error": "CUDA out of memory"})()
            return type("R", (), {"success": True, "error": None, "output_path": "out.png"})()

        result = await retry_with_bad_case_detection(
            generate_fn,
            {"config": FakeImageConfig(blocks_to_swap=32)},
            config=RetryConfig(max_retries=2, base_delay_seconds=0.0, jitter_ratio=0.0),
        )

        assert result.success
        assert result.attempts == 2
        assert len(seen_params) == 2
        assert seen_params[1]["config"].blocks_to_swap == 36

    async def test_exhausted_failed_result_passthrough_not_masqueraded(self):
        """重试耗尽时失败的原始结果必须原样透传，调用方据其标记任务失败。"""

        async def generate_fn(**params):
            return type("R", (), {"success": False, "error": "CUDA out of memory"})()

        result = await retry_with_bad_case_detection(
            generate_fn,
            {"config": FakeImageConfig()},
            config=RetryConfig(max_retries=1, base_delay_seconds=0.0, jitter_ratio=0.0),
        )

        assert result.result.success is False
        assert result.result.error == "CUDA out of memory"

    async def test_all_attempts_raise_returns_failure_with_reason(self):
        """所有尝试均抛 OOM 异常时返回失败 RetryResult（result=None）。"""

        async def generate_fn(**params):
            raise RuntimeError("CUDA out of memory")

        result = await retry_with_bad_case_detection(
            generate_fn,
            {"config": FakeImageConfig()},
            config=RetryConfig(max_retries=1, base_delay_seconds=0.0, jitter_ratio=0.0),
        )

        assert result.success is False
        assert result.result is None
        assert "out of memory" in result.failure_reason

    async def test_none_result_convention(self):
        """upload.py 契约：result=None 的失败 RetryResult 转译为 RuntimeError。"""
        failure_reason = "所有重试均失败: CUDA out of memory"

        async def generate_fn(**params):
            raise RuntimeError("CUDA out of memory")

        result = await retry_with_bad_case_detection(
            generate_fn,
            {"config": FakeImageConfig()},
            config=RetryConfig(max_retries=0, base_delay_seconds=0.0, jitter_ratio=0.0),
        )

        assert result.result is None
        assert "out of memory" in result.failure_reason
        with pytest.raises(RuntimeError, match="out of memory"):
            # 复现 upload.py 的处理逻辑
            if result.result is None:
                raise RuntimeError(result.failure_reason or failure_reason)


class TestBuildRetryConfig:
    """runtime.retry 配置读取。"""

    def test_defaults(self):
        cfg = build_retry_config({})
        assert cfg.max_retries == 3
        assert cfg.enable_degradation

    def test_disabled(self):
        cfg = build_retry_config({"runtime": {"retry": {"enabled": False}}})
        assert cfg.max_retries == 0
        assert not cfg.enable_degradation

    def test_custom_values(self):
        cfg = build_retry_config(
            {"runtime": {"retry": {"max_retries": 3, "base_delay_seconds": 0.5, "max_delay_seconds": 10.0}}}
        )
        assert cfg.max_retries == 3
        assert cfg.base_delay_seconds == 0.5
        assert cfg.max_delay_seconds == 10.0

    def test_none_config(self):
        cfg = build_retry_config(None)
        assert cfg.max_retries == 3


class TestApplyOomDegradation:
    """批量降级 helper。"""

    def test_oom_persists_degradation_to_batch_config(self):
        current = {"resolution": 2048, "blocks_to_swap": 32, "seed": 42}
        batch_cfg = dict(current)

        applied = apply_oom_degradation(current, batch_cfg, "CUDA out of memory", attempt=1, app_config={})

        assert applied
        assert current["blocks_to_swap"] == 36
        assert batch_cfg["blocks_to_swap"] == 36
        # 种子轮换后两边保持一致
        assert current["seed"] == batch_cfg["seed"]

    def test_non_oom_keeps_params(self):
        current = {"resolution": 2048, "blocks_to_swap": 32, "seed": 42}
        batch_cfg = dict(current)

        applied = apply_oom_degradation(current, batch_cfg, "连接超时 timeout", attempt=1, app_config={})

        # 网络类失败只换种子不降级（enable_seed_rotation 默认开启）
        assert isinstance(applied, bool)
        assert current["blocks_to_swap"] == 32

    def test_noop_when_no_effective_change(self):
        current = {"seed": 42}
        batch_cfg = dict(current)

        applied = apply_oom_degradation(current, batch_cfg, "CUDA out of memory", attempt=1, app_config={})

        # current_config 中没有可降级字段时不应崩溃
        assert isinstance(applied, bool)
