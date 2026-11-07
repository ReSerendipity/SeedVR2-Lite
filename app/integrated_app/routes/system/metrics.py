#!/usr/bin/env python3
"""性能指标监控路由模块。

提供运行时性能 KPI 指标查询端点，用于系统监控和运维。

API 端点：
- GET /api/system/metrics: 获取当前性能指标快照
- GET /api/system/metrics/inference: 获取推理历史记录

所属项目：SeedVR2 (SeedVR2 视频/图像修复工具)
"""

import logging

from fastapi import APIRouter

from app.integrated_app.metrics import metrics_collector
from app.integrated_app.utils.response import respond_success

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/system", tags=["性能指标"])


@router.get("/metrics")
async def get_metrics():
    """获取当前性能指标快照

    返回系统运行时间、内存使用率、GPU 显存/利用率、
    推理次数/成功率/平均耗时、缓存统计等 KPI 指标。

    Returns:
        JSON 响应，data 字段包含指标快照字典（含 vram_leak 显存峰值趋势，P2-4）
    """
    snapshot = metrics_collector.snapshot()
    data = snapshot.to_dict()
    # P2-4：暴露显存峰值趋势，便于运维在 OOM 前发现泄漏苗头
    try:
        from app.integrated_app.optimization.gpu.vram_leak_detector import vram_leak_detector

        data["vram_leak"] = vram_leak_detector.snapshot()
    except Exception as e:  # noqa: BLE001 — 可观测性字段失败不影响主指标
        logger.warning(f"显存泄漏监控快照获取失败: {e}")
    # 评估 A3 整改：暴露输出质量劣化熔断状态（阈值/连续计数/回滚钩子挂载数）
    try:
        from app.integrated_app.services.restore_service import quality_breaker

        data["quality_breaker"] = quality_breaker.snapshot()
    except Exception as e:  # noqa: BLE001 — 可观测性字段失败不影响主指标
        logger.warning(f"质量劣化熔断快照获取失败: {e}")
    return respond_success(data=data)


@router.get("/metrics/inference")
async def get_inference_history():
    """获取最近的推理历史记录

    返回最近 100 条推理记录，包含时间戳、耗时、成功/失败、模型大小等。

    Returns:
        JSON 响应，data 字段包含推理记录列表
    """
    records = [
        {
            "timestamp": r.timestamp,
            "duration": round(r.duration, 2),
            "success": r.success,
            "model_size": r.model_size,
            "input_type": r.input_type,
        }
        for r in metrics_collector.recent_inferences()
    ]
    return respond_success(data=records)


@router.post("/metrics/reset")
async def reset_metrics():
    """重置推理计数器

    清空推理历史记录和计数器，不重置运行时间。
    用于运维场景下的指标重置。

    Returns:
        JSON 响应，确认重置成功
    """
    metrics_collector.reset()
    return respond_success(data={"status": "ok"})
