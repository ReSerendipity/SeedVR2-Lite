"""SeedVR2 - 输出质量劣化熔断器（评估 A3 整改：阈值告警 + 自动回滚钩子）。

评估 A3（⛔P0，两轮一致未收敛）：运行时输出亮度信号此前只是记录
（metrics.output_mean_luma 经 /api/system/metrics 暴露），未接告警阈值，
更无模型级回滚动作——「输出全黑」类静默劣化只能靠用户发现或每周真机冒烟兜底。

本模块在「连续 N 次输出平均亮度低于阈值」时打开熔断：
    1. 阈值判定：亮度样本 < luma_min（0-255 刻度，默认 8.0，近黑）记一次劣化；
       健康样本复位连续计数（与 OOM 熔断「非 OOM 失败复位」同语义）。
    2. 告警：打开瞬间打 WARNING 级结构化日志，状态经 /api/system/metrics 暴露。
    3. 自动回滚钩子：打开瞬间逐个调用注册的回滚钩子（app 启动时由 app_server
       注册「等队列空闲后卸载当前模型权重」——下次任务重新走哈希白名单校验加载，
       坏权重/坏采样状态不跨劣化复用）。

设计要点（与 oom_breaker 同构，便于维护者对照）:
    - 进程内单例（quality_breaker），线程安全（Lock 保护状态）
    - 只有亮度样本参与计数；视频/解码失败样本为 None，不入计数
    - 熔断打开后经过 cooldown_seconds 自动半开（放行一次探测输出是否恢复）
    - 阈值/冷却期来自 config.yaml runtime.quality，可在配置中禁用
    - 提交闸复用 OOM 熔断的 503 + Retry-After 语义（upload.py/batch.py）

边界（诚实声明）：亮度阈值是「全黑/全灰」类灾难劣化的探测器，不是画质综合
判定——夜间等低亮度内容的合法输出也可能低于默认阈值，属可配置的取舍
（enabled=false 或调低 luma_min 均可关闭该面），文档化于 docs/ML_OPS_GATES.md §4。

所属项目：SeedVR2 (SeedVR2 视频/图像修复工具)
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

# 回滚钩子类型：接收熔断打开原因快照（dict），同步返回（异步动作自行 create_task）。
RollbackHook = Callable[[dict[str, Any]], None]


class QualityBreaker:
    """输出质量劣化熔断器。

    状态机（与 OomBreaker 同构）:
        closed（正常）--连续低亮度样本达到 threshold--> open（熔断）
        open --冷却期结束--> half_open（放行探测）
        half_open --健康样本--> closed；half_open --再劣化--> open
    """

    def __init__(
        self,
        threshold: int = 3,
        luma_min: float = 8.0,
        cooldown_seconds: float = 600.0,
        enabled: bool = True,
    ):
        """初始化熔断器。

        Args:
            threshold: 连续低亮度样本次数阈值（>=1），达到即熔断。
            luma_min: 输出平均亮度下限（0-255 刻度），低于记一次劣化样本。
            cooldown_seconds: 熔断打开后的冷却期（秒），期间拒绝新任务提交。
            enabled: False 时 record_output_luma 不计数、remaining_cooldown 恒 0。
        """
        self._lock = threading.Lock()
        self._threshold = max(1, int(threshold))
        self._luma_min = float(luma_min)
        self._cooldown = max(1.0, float(cooldown_seconds))
        self._enabled = bool(enabled)
        self._consecutive_breaches = 0
        self._opened_at: float | None = None
        self._last_luma: float | None = None
        self._rollback_hooks: list[RollbackHook] = []

    def configure(
        self,
        threshold: int,
        luma_min: float,
        cooldown_seconds: float,
        enabled: bool = True,
    ) -> None:
        """按配置更新阈值/下限/冷却期/开关（app 启动与提交闸同步时调用）。"""
        with self._lock:
            self._threshold = max(1, int(threshold))
            self._luma_min = float(luma_min)
            self._cooldown = max(1.0, float(cooldown_seconds))
            self._enabled = bool(enabled)

    def register_rollback_hook(self, hook: RollbackHook) -> None:
        """注册回滚钩子（熔断打开时按注册顺序逐个调用）。

        钩子异常被吞掉并记日志：回滚是 best-effort 动作，
        不得让熔断判定路径抛错（与 metrics 采集 best-effort 原则一致）。
        """
        with self._lock:
            self._rollback_hooks.append(hook)

    def record_output_luma(self, luma: float | None) -> bool:
        """记录一次成功推理的输出平均亮度并做阈值判定。

        Args:
            luma: 输出图平均亮度（0-255）；视频/解码失败传 None（不参与判定）。

        Returns:
            bool: 记录后熔断是否处于打开状态。
        """
        with self._lock:
            if not self._enabled or luma is None:
                return self._opened_at is not None
            self._last_luma = float(luma)
            if self._last_luma >= self._luma_min:
                if self._consecutive_breaches:
                    logger.info(f"输出亮度 {self._last_luma:.1f} 恢复正常，质量熔断连续计数复位")
                self._consecutive_breaches = 0
                return self._opened_at is not None
            self._consecutive_breaches += 1
            if self._consecutive_breaches >= self._threshold and self._opened_at is None:
                self._opened_at = time.monotonic()
                reason = self._snapshot_locked()
                logger.warning(
                    f"连续 {self._consecutive_breaches} 次输出平均亮度 "
                    f"{self._last_luma:.1f} < 阈值 {self._luma_min:.1f}，"
                    f"输出质量熔断打开（冷却 {self._cooldown:.0f}s）——疑似「全黑输出」类"
                    f"模型级劣化，已触发自动回滚钩子"
                )
                self._fire_rollback_hooks_locked(reason)
            return self._opened_at is not None

    def _fire_rollback_hooks_locked(self, reason: dict[str, Any]) -> None:
        """锁内收集钩子、锁外逐个调用（钩子可能耗时/再入，不得持锁执行）。"""
        hooks = list(self._rollback_hooks)
        for hook in hooks:
            try:
                hook(reason)
            except Exception as exc:  # noqa: BLE001 — 回滚 best-effort，不拖垮判定路径
                logger.warning(f"质量劣化回滚钩子执行失败（忽略，继续下一个）: {exc!r}")

    def remaining_cooldown(self) -> float:
        """查询剩余拒绝时间（秒）。

        Returns:
            float: >0 表示熔断打开且仍在冷却期（应拒绝新任务）；
                0 表示关闭/禁用或冷却期已过（半开放行探测）。
        """
        with self._lock:
            if not self._enabled or self._opened_at is None:
                return 0.0
            elapsed = time.monotonic() - self._opened_at
            if elapsed >= self._cooldown:
                # 冷却期结束：半开——复位打开时间，允许一次探测；
                # 探测再劣化会立即重新打开（连续计数已达阈值）
                self._opened_at = None
                self._consecutive_breaches = self._threshold - 1
                logger.info("输出质量熔断冷却期结束，半开放行探测")
                return 0.0
            return self._cooldown - elapsed

    def snapshot(self) -> dict[str, Any]:
        """当前状态快照（/api/system/metrics 暴露与诊断用）。"""
        with self._lock:
            return self._snapshot_locked()

    def _snapshot_locked(self) -> dict[str, Any]:
        """生成状态快照（调用方须已持锁）。"""
        return {
            "enabled": self._enabled,
            "open": self._opened_at is not None,
            "consecutive_breaches": self._consecutive_breaches,
            "threshold": self._threshold,
            "luma_min": self._luma_min,
            "last_luma": (round(self._last_luma, 1) if self._last_luma is not None else None),
            "remaining_seconds": round(
                max(
                    0.0,
                    (self._cooldown - (time.monotonic() - self._opened_at)) if self._opened_at is not None else 0.0,
                ),
                1,
            ),
            "rollback_hooks": len(self._rollback_hooks),
        }
