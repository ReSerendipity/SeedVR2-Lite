#!/usr/bin/env python3
"""输出质量劣化熔断器单元测试（评估 A3 整改验收）。

覆盖 A3 判据「有阈值、有触发路径、有回滚动作」三件：
1. 阈值判定：连续低亮度样本达到 threshold 打开熔断；健康样本复位计数；
   None 样本（视频/解码失败）不参与判定；luma_min 边界值语义（>= 视为健康）。
2. 触发路径：restore_service.quality_breaker_remaining 按 config 同步阈值/开关，
   提交闸语义与 OOM 熔断一致（>0 拒绝）；config 禁用时恒 0。
3. 回滚动作：熔断打开瞬间按注册顺序调用回滚钩子；钩子异常被吞不拖垮判定路径。

运行：pytest tests/test_quality_breaker.py -q

所属项目：SeedVR2 (SeedVR2 视频/图像修复工具)
"""

import pytest

from app.integrated_app.services.quality_breaker import QualityBreaker
from app.integrated_app.services.restore_service import quality_breaker_remaining


class TestThresholdJudgement:
    """阈值判定状态机。"""

    def test_consecutive_dark_outputs_open_breaker(self):
        breaker = QualityBreaker(threshold=3, luma_min=8.0, cooldown_seconds=600.0)
        assert breaker.record_output_luma(5.0) is False
        assert breaker.record_output_luma(2.0) is False
        assert breaker.record_output_luma(0.0) is True  # 第 3 次连续劣化 → 打开
        snap = breaker.snapshot()
        assert snap["open"] is True
        assert snap["consecutive_breaches"] == 3
        assert snap["last_luma"] == 0.0

    def test_healthy_sample_resets_counter(self):
        breaker = QualityBreaker(threshold=3, luma_min=8.0)
        breaker.record_output_luma(3.0)
        breaker.record_output_luma(3.0)
        assert breaker.record_output_luma(120.0) is False  # 健康样本复位
        snap = breaker.snapshot()
        assert snap["consecutive_breaches"] == 0
        assert snap["open"] is False

    def test_luma_min_boundary_is_inclusive_healthy(self):
        breaker = QualityBreaker(threshold=1, luma_min=8.0)
        # 恰好等于阈值视为健康（>= 判定），不触发熔断
        assert breaker.record_output_luma(8.0) is False
        assert breaker.snapshot()["open"] is False
        # 低于阈值 0.1 记为劣化
        assert breaker.record_output_luma(7.9) is True

    def test_none_sample_not_counted(self):
        breaker = QualityBreaker(threshold=2, luma_min=8.0)
        assert breaker.record_output_luma(None) is False
        assert breaker.record_output_luma(None) is False
        snap = breaker.snapshot()
        assert snap["consecutive_breaches"] == 0
        assert snap["last_luma"] is None

    def test_disabled_breaker_never_opens(self):
        breaker = QualityBreaker(threshold=1, luma_min=8.0, enabled=False)
        assert breaker.record_output_luma(0.0) is False
        assert breaker.remaining_cooldown() == 0.0
        assert breaker.snapshot()["enabled"] is False

    def test_single_breach_threshold_opens_immediately(self):
        breaker = QualityBreaker(threshold=1, luma_min=8.0)
        assert breaker.record_output_luma(1.0) is True

    def test_half_open_after_cooldown_then_close_on_healthy(self):
        breaker = QualityBreaker(threshold=2, luma_min=8.0, cooldown_seconds=30.0)
        breaker.record_output_luma(2.0)
        breaker.record_output_luma(2.0)
        assert breaker.snapshot()["open"] is True
        # 人为把打开时间拨回 31s 前 → 冷却期已过 → 半开（remaining 归 0）
        breaker._opened_at -= 31.0
        assert breaker.remaining_cooldown() == 0.0
        assert breaker.snapshot()["open"] is False  # 半开态对外显示未打开
        # 半开探测：健康样本 → 完全复位
        breaker.record_output_luma(150.0)
        snap = breaker.snapshot()
        assert snap["consecutive_breaches"] == 0

    def test_half_open_probe_dark_reopens(self):
        breaker = QualityBreaker(threshold=2, luma_min=8.0, cooldown_seconds=30.0)
        breaker.record_output_luma(2.0)
        breaker.record_output_luma(2.0)
        breaker._opened_at -= 31.0
        assert breaker.remaining_cooldown() == 0.0  # 半开
        # 探测仍劣化 → 立即重新打开（连续计数已达阈值-1，再记 1 次即达标）
        assert breaker.record_output_luma(1.0) is True
        assert breaker.snapshot()["open"] is True


class TestRollbackHook:
    """自动回滚钩子触发路径。"""

    def test_hook_fires_once_on_open_with_reason(self):
        breaker = QualityBreaker(threshold=2, luma_min=8.0, cooldown_seconds=600.0)
        reasons: list[dict] = []
        breaker.register_rollback_hook(reasons.append)
        breaker.record_output_luma(3.0)
        assert reasons == []  # 未达阈值不触发
        breaker.record_output_luma(2.0)
        assert len(reasons) == 1
        reason = reasons[0]
        assert reason["open"] is True
        assert reason["consecutive_breaches"] == 2
        assert reason["threshold"] == 2
        assert reason["luma_min"] == 8.0
        # 持续劣化（冷却期内重新打开被 opened_at 挡住）不会重复触发
        breaker.record_output_luma(1.0)
        assert len(reasons) == 1

    def test_hooks_called_in_registration_order(self):
        breaker = QualityBreaker(threshold=1, luma_min=8.0)
        order: list[str] = []
        breaker.register_rollback_hook(lambda r: order.append("first"))
        breaker.register_rollback_hook(lambda r: order.append("second"))
        breaker.record_output_luma(0.5)
        assert order == ["first", "second"]

    def test_failing_hook_does_not_break_judgement_or_later_hooks(self):
        breaker = QualityBreaker(threshold=1, luma_min=8.0)
        called: list[str] = []

        def boom(reason: dict) -> None:
            raise RuntimeError("rollback exploded")

        breaker.register_rollback_hook(boom)
        breaker.register_rollback_hook(lambda r: called.append("after"))
        assert breaker.record_output_luma(0.5) is True  # 熔断照常打开
        assert called == ["after"]  # 后续钩子照常执行
        assert breaker.snapshot()["open"] is True

    def test_no_hooks_open_still_works(self):
        breaker = QualityBreaker(threshold=1, luma_min=8.0)
        assert breaker.record_output_luma(0.5) is True
        assert breaker.snapshot()["rollback_hooks"] == 0


class TestConfigWiring:
    """restore_service 配置同步与提交闸语义。"""

    def setup_method(self):
        # 单例状态隔离：每个用例前重置到禁用安全态
        from app.integrated_app.services.restore_service import quality_breaker

        quality_breaker.configure(threshold=3, luma_min=8.0, cooldown_seconds=600.0, enabled=False)
        quality_breaker._opened_at = None
        quality_breaker._consecutive_breaches = 0

    def test_config_sync_enables_and_opens_gate(self):
        cfg = {"runtime": {"quality": {"enabled": True, "luma_min": 8.0, "threshold": 2, "cooldown_seconds": 600.0}}}
        from app.integrated_app.services.restore_service import quality_breaker

        assert quality_breaker_remaining(cfg) == 0.0  # 初始未打开
        quality_breaker.record_output_luma(2.0)
        quality_breaker.record_output_luma(2.0)
        remaining = quality_breaker_remaining(cfg)
        assert remaining > 0  # 熔断打开 → 提交闸拒绝
        assert remaining <= 600.0

    def test_config_disabled_returns_zero(self):
        cfg = {"runtime": {"quality": {"enabled": False}}}
        from app.integrated_app.services.restore_service import quality_breaker

        quality_breaker.record_output_luma(0.0)  # 禁用态记录不计数
        assert quality_breaker_remaining(cfg) == 0.0

    def test_missing_config_defaults_to_disabled_safe_state(self):
        # 测试环境无 config 注入时不得误伤（安全态=禁用）
        assert quality_breaker_remaining({}) == 0.0
        assert quality_breaker_remaining(None) == 0.0

    def test_luma_min_zero_degenerates_to_all_healthy(self):
        cfg = {"runtime": {"quality": {"enabled": True, "luma_min": 0.0, "threshold": 1, "cooldown_seconds": 600.0}}}
        from app.integrated_app.services.restore_service import quality_breaker

        # luma_min=0 的边界语义：>= 判定下连纯黑(0.0)也算健康——
        # 该取值等价于经阈值维度关闭劣化检测（文档化于 ML_OPS_GATES §4 边界说明）
        quality_breaker.record_output_luma(0.0)
        assert quality_breaker_remaining(cfg) == 0.0


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
