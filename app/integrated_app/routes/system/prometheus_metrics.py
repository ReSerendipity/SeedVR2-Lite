#!/usr/bin/env python3
"""Prometheus 文本格式指标暴露路由模块。

将 metrics_collector 的运行时指标快照映射为 Prometheus exposition 格式
（text/plain; version=0.0.4），供 Prometheus 抓取、Grafana 展示，以及
基于 custom.metrics.k8s.io 的 HPA 扩缩容判据（对应云原生评估报告 P2-6）。

API 端点：
- GET /metrics: Prometheus 文本格式指标（无 /api 前缀，遵循抓取约定）

指标清单：
- seedvr2_uptime_seconds                 进程运行时长
- seedvr2_host_ram_used_percent          宿主内存使用率
- seedvr2_host_ram_available_bytes       宿主可用内存
- seedvr2_gpu_available                  GPU 可用（0/1）
- seedvr2_gpu_vram_total_bytes           显存总量
- seedvr2_gpu_vram_available_bytes       显存可用量
- seedvr2_gpu_utilization_percent        GPU 利用率（SM 优先，回退显存占比）
- seedvr2_inferences_total               推理总次数（counter）
- seedvr2_inference_successes_total      推理成功次数（counter）
- seedvr2_inference_failures_total       推理失败次数（counter）
- seedvr2_inference_duration_seconds     平均推理耗时
- seedvr2_last_inference_duration_seconds 最近一次推理耗时
- seedvr2_cache_files / seedvr2_cache_bytes  文件缓存统计

注意：本端点返回 Prometheus 文本协议原文，不套 respond_success 包装
（§13 统一响应规范仅约束业务 JSON API，抓取协议属于例外）。

所属项目：SeedVR2 (SeedVR2 视频/图像修复工具)
"""

import logging

from fastapi import APIRouter, Response

from app.integrated_app.metrics import metrics_collector

logger = logging.getLogger(__name__)
router = APIRouter(tags=["可观测性"])

_PROM_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


def _fmt(value: object) -> str:
    """格式化指标值为 Prometheus 兼容的数字字面量。

    Args:
        value: 任意来源的数值（int / float / str / None）。

    Returns:
        可被 Prometheus 解析的数字字符串；非法值回退为 0。
    """
    try:
        return repr(float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "0.0"


def build_prometheus_exposition() -> str:
    """从 metrics_collector 快照构建 Prometheus 文本暴露内容。

    Returns:
        符合 exposition format 0.0.4 的多行字符串（以 \n 结尾）。
    """
    snap = metrics_collector.snapshot().to_dict()

    lines: list[str] = []

    def emit(name: str, mtype: str, help_text: str, value: object) -> None:
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} {mtype}")
        lines.append(f"{name} {_fmt(value)}")

    system = snap.get("system", {})
    gpu = snap.get("gpu", {})
    inference = snap.get("inference", {})
    cache = snap.get("cache", {})

    emit("seedvr2_uptime_seconds", "gauge", "Process uptime in seconds.", system.get("uptime_seconds"))
    emit("seedvr2_host_ram_used_percent", "gauge", "Host RAM usage percentage.", system.get("ram_usage_pct"))
    emit(
        "seedvr2_host_ram_available_bytes",
        "gauge",
        "Host RAM available in bytes.",
        _gb_to_bytes(system.get("ram_available_gb")),
    )

    emit(
        "seedvr2_gpu_available",
        "gauge",
        "Whether a CUDA GPU backend is available (1) or not (0).",
        gpu.get("available"),
    )
    emit("seedvr2_gpu_vram_total_bytes", "gauge", "Total GPU VRAM in bytes.", _mb_to_bytes(gpu.get("vram_total_mb")))
    emit(
        "seedvr2_gpu_vram_available_bytes",
        "gauge",
        "Available GPU VRAM in bytes.",
        _mb_to_bytes(gpu.get("vram_available_mb")),
    )
    emit(
        "seedvr2_gpu_utilization_percent",
        "gauge",
        "GPU utilization percentage (SM utilization preferred, VRAM ratio fallback).",
        gpu.get("utilization_pct"),
    )

    emit("seedvr2_inferences_total", "counter", "Total number of inference requests.", inference.get("total"))
    emit(
        "seedvr2_inference_successes_total", "counter", "Number of successful inferences.", inference.get("successful")
    )
    emit("seedvr2_inference_failures_total", "counter", "Number of failed inferences.", inference.get("failed"))
    emit(
        "seedvr2_inference_duration_seconds",
        "gauge",
        "Average inference duration in seconds.",
        inference.get("avg_duration_seconds"),
    )
    emit(
        "seedvr2_last_inference_duration_seconds",
        "gauge",
        "Duration of the most recent inference in seconds.",
        inference.get("last_duration_seconds"),
    )

    emit("seedvr2_cache_files", "gauge", "Number of files in the upload/cache directory.", cache.get("total_files"))
    emit(
        "seedvr2_cache_bytes",
        "gauge",
        "Total size of the upload/cache directory in bytes.",
        _mb_to_bytes(cache.get("total_size_mb")),
    )

    # X2 本地崩溃遥测（运维 P1#1）：壳 panic / 后端异常退出的本地记录计数，尽力而为
    try:
        from app.integrated_app.utils.crash_telemetry import (
            DEFAULT_WINDOW_DAYS,
            count_crashes,
        )

        emit(
            "seedvr2_shell_crashes_total",
            "gauge",
            "Local crash records written by the desktop shell (logs/crashes), all time.",
            count_crashes(),
        )
        emit(
            "seedvr2_shell_crashes_7d",
            "gauge",
            "Local crash records written by the desktop shell (logs/crashes), last 7 days.",
            count_crashes(DEFAULT_WINDOW_DAYS),
        )
    except Exception:  # 遥测尽力而为，不影响主指标面
        logger.debug("crash telemetry metrics unavailable", exc_info=True)

    return "\n".join(lines) + "\n"


def _gb_to_bytes(value: object) -> float:
    """GB 转 bytes（非法输入回退 0）。"""
    try:
        return float(value) * 1024**3  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _mb_to_bytes(value: object) -> float:
    """MB 转 bytes（非法输入回退 0）。"""
    try:
        return float(value) * 1024 * 1024  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


@router.get("/metrics")
async def prometheus_metrics() -> Response:
    """Prometheus 抓取端点。

    API 端点：GET /metrics

    Returns:
        Response：text/plain; version=0.0.4 格式的指标文本。
    """
    return Response(content=build_prometheus_exposition(), media_type=_PROM_CONTENT_TYPE)
