"""SeedVR2 - 性能指标收集器模块

提供轻量级运行时性能指标收集，用于系统监控和 KPI 追踪。
所有指标收集都是线程安全的，且不依赖 GPU（GPU 信息通过 gpu_backend 获取）。

指标分类：
- 系统指标：运行时间、内存使用率
- GPU 指标：显存使用、GPU 利用率
- 推理指标：推理次数、平均耗时、成功率
- 缓存指标：文件缓存命中率、大小

使用方式：
    from app.integrated_app.metrics import metrics_collector

    # 记录推理
    metrics_collector.record_inference(success=True, duration=12.5, model_size="3b")

    # 获取快照
    snapshot = metrics_collector.snapshot()
"""

import logging
import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

_START_TIME = time.time()
_MAX_HISTORY = 100


@dataclass
class InferenceRecord:
    """单次推理记录"""

    timestamp: float
    duration: float
    success: bool
    model_size: str
    input_type: str  # "image" or "video"
    # MLOps P-2：输出侧轻量质量信号——输出图的平均亮度（0-255）。
    # 仅图片类产出计算，视频/解码失败时为 None。用途：灰度检测「输出全黑」类
    # 静默劣化（模型加载错配/采样异常时最典型的症状就是输出黑帧）。
    output_mean_luma: float | None = None


@dataclass
class MetricsSnapshot:
    """指标快照，某一时刻的性能指标集合"""

    # 系统指标
    uptime_seconds: float = 0.0
    ram_usage_pct: float = 0.0
    ram_available_gb: float = 0.0

    # GPU 指标
    gpu_available: bool = False
    gpu_name: str = ""
    gpu_vram_total_mb: int = 0
    gpu_vram_available_mb: int = 0
    gpu_utilization_pct: float = 0.0

    # 推理指标
    total_inferences: int = 0
    successful_inferences: int = 0
    failed_inferences: int = 0
    avg_inference_duration: float = 0.0
    last_inference_duration: float = 0.0
    # MLOps P-2：最近成功推理的输出平均亮度均值（无样本时为 None）
    avg_output_mean_luma: float | None = None

    # 缓存指标
    cache_total_files: int = 0
    cache_total_size_mb: float = 0.0

    # 时间戳
    snapshot_time: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        """转换为字典格式，便于 JSON 序列化"""
        return {
            "system": {
                "uptime_seconds": round(self.uptime_seconds, 1),
                "ram_usage_pct": round(self.ram_usage_pct, 1),
                "ram_available_gb": round(self.ram_available_gb, 2),
            },
            "gpu": {
                "available": self.gpu_available,
                "name": self.gpu_name,
                "vram_total_mb": self.gpu_vram_total_mb,
                "vram_available_mb": self.gpu_vram_available_mb,
                "utilization_pct": round(self.gpu_utilization_pct, 1),
            },
            "inference": {
                "total": self.total_inferences,
                "successful": self.successful_inferences,
                "failed": self.failed_inferences,
                "success_rate": round(self.successful_inferences / max(self.total_inferences, 1) * 100, 1),
                "avg_duration_seconds": round(self.avg_inference_duration, 2),
                "last_duration_seconds": round(self.last_inference_duration, 2),
                # MLOps P-2：无样本时为 null，前端按「未采集」处理
                "avg_output_mean_luma": (
                    round(self.avg_output_mean_luma, 1) if self.avg_output_mean_luma is not None else None
                ),
            },
            "cache": {
                "total_files": self.cache_total_files,
                "total_size_mb": round(self.cache_total_size_mb, 2),
            },
            "snapshot_time": self.snapshot_time,
        }


class MetricsCollector:
    """线程安全的性能指标收集器

    收集推理次数、耗时、成功率等运行时指标，
    并在请求时生成包含系统/GPU/缓存信息的完整快照。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._inference_records: deque[InferenceRecord] = deque(maxlen=_MAX_HISTORY)
        self._total_inferences = 0
        self._successful_inferences = 0
        self._failed_inferences = 0
        self._total_duration = 0.0
        self._last_duration = 0.0
        # MLOps P-2：输出亮度样本（与推理历史同窗口），None 不入队
        self._luma_samples: deque[float] = deque(maxlen=_MAX_HISTORY)

    def record_inference(
        self,
        success: bool,
        duration: float,
        model_size: str = "unknown",
        input_type: str = "image",
        output_mean_luma: float | None = None,
    ) -> None:
        """记录一次推理结果

        Args:
            success: 推理是否成功
            duration: 推理耗时（秒）
            model_size: 模型大小标识（如 "3b"、"7b"）
            input_type: 输入类型（"image" 或 "video"）
            output_mean_luma: 输出图平均亮度（0-255）；图片产出由调用方 best-effort 采集，
                视频/失败/解码失败传 None，不参与统计
        """
        with self._lock:
            record = InferenceRecord(
                timestamp=time.time(),
                duration=duration,
                success=success,
                model_size=model_size,
                input_type=input_type,
                output_mean_luma=output_mean_luma,
            )
            self._inference_records.append(record)
            self._total_inferences += 1
            if success:
                self._successful_inferences += 1
                if output_mean_luma is not None:
                    self._luma_samples.append(output_mean_luma)
            else:
                self._failed_inferences += 1
            self._total_duration += duration
            self._last_duration = duration

    def snapshot(self) -> MetricsSnapshot:
        """生成当前指标快照

        收集系统、GPU 和推理指标，返回 MetricsSnapshot 对象。
        GPU 信息获取失败时使用默认值。
        """
        snap = MetricsSnapshot()

        # 系统指标
        snap.uptime_seconds = time.time() - _START_TIME
        try:
            from app.integrated_app.engines._memory_utils import _get_system_memory

            mem = _get_system_memory()
            snap.ram_usage_pct = mem.percent
            snap.ram_available_gb = mem.available / (1024**3)
        except Exception:
            pass

        # GPU 指标
        try:
            from app.integrated_app.gpu_backend import gpu_manager

            gpu_info = gpu_manager.get_gpu_info()
            snap.gpu_available = gpu_manager.is_gpu_available
            snap.gpu_name = gpu_info.name
            snap.gpu_vram_total_mb = gpu_info.total_vram_mb
            snap.gpu_vram_available_mb = gpu_info.available_vram_mb
            # P2-1: 优先 SM 真实利用率，nvidia-smi 不可用时回退显存占用比
            snap.gpu_utilization_pct = (
                gpu_info.sm_utilization_pct if gpu_info.sm_utilization_pct is not None else gpu_info.utilization_pct
            )
        except Exception:
            snap.gpu_available = False

        # 推理指标
        with self._lock:
            snap.total_inferences = self._total_inferences
            snap.successful_inferences = self._successful_inferences
            snap.failed_inferences = self._failed_inferences
            snap.avg_inference_duration = self._total_duration / max(self._total_inferences, 1)
            snap.last_inference_duration = self._last_duration
            if self._luma_samples:
                snap.avg_output_mean_luma = sum(self._luma_samples) / len(self._luma_samples)

        # 缓存指标 (best-effort) — 使用 os.scandir 递归遍历，比 os.walk 更高效
        try:
            cache_dir = os.environ.get("SEEDVR2_CACHE_DIR", "data/uploads")
            if os.path.exists(cache_dir):
                total_files, total_size = _scan_dir_stats(cache_dir)
                snap.cache_total_files = total_files
                snap.cache_total_size_mb = total_size / (1024 * 1024)
        except Exception:
            pass

        return snap

    def recent_inferences(self) -> list["InferenceRecord"]:
        """返回最近推理记录的**拷贝**（锁内取，线程安全）。

        供 ``/api/system/metrics/inference`` 端点使用。此前该端点直接读取
        ``_lock`` / ``_inference_records`` 私有成员，属封装泄漏——一旦内部
        结构（deque、字段名）调整就会在路由层炸开。
        """
        with self._lock:
            return list(self._inference_records)

    def reset(self) -> None:
        """重置所有推理计数器（不重置运行时间）"""
        with self._lock:
            self._inference_records.clear()
            self._total_inferences = 0
            self._successful_inferences = 0
            self._failed_inferences = 0
            self._total_duration = 0.0
            self._last_duration = 0.0
            self._luma_samples.clear()


# MLOps P-2：输出侧轻量质量信号——支持的图片扩展名（视频容器不在此列，返回 None）
_LUMA_MEAN_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


def compute_output_mean_luma(path: str, max_side: int = 64) -> float | None:
    """Best-effort 计算输出图的平均亮度（0-255）。

    用途：输出「全黑/全灰」静默劣化的最低成本探测器——模型加载错配、采样器异常
    时最典型症状是输出黑帧，而耗时/成功率/PSNR 门在离线环境之外无法在线采集。

    设计原则（与 vram_leak_detector 调用处一致）：
    - 纯 best-effort：文件缺失、非图片、解码失败一律返回 None，绝不抛出；
    - 缩到 max_side 边长再算均值，单次开销毫秒级，不拖慢任务收尾路径；
    - PIL 延迟导入（与 gpu_backend 同文件风格），无图片场景零成本。

    Args:
        path: 输出文件路径。
        max_side: 缩放后的最长边，控制解码开销。

    Returns:
        0-255 亮度均值；不适用或失败时 None。
    """
    if not path:
        return None
    ext = os.path.splitext(path)[1].lower()
    if ext not in _LUMA_MEAN_IMAGE_EXTS:
        return None
    if not os.path.exists(path):
        return None
    try:
        from PIL import Image

        with Image.open(path) as img:
            gray = img.convert("L")
            if max(gray.size) > max_side:
                gray.thumbnail((max_side, max_side))
            pixels = gray.tobytes()
            if not pixels:
                return None
            return sum(pixels) / len(pixels)
    except Exception:
        return None


def _scan_dir_stats(dir_path: str) -> tuple[int, int]:
    """递归扫描目录，统计文件数量和总大小。

    使用 os.scandir 替代 os.walk，DirEntry 对象缓存 stat 信息，
    避免对每个文件额外调用 os.path.getsize 的系统开销。

    Args:
        dir_path: 要扫描的目录路径。

    Returns:
        (文件总数, 文件总字节数) 元组。目录访问失败时返回 (0, 0)。
    """
    total_files = 0
    total_size = 0
    try:
        with os.scandir(dir_path) as entries:
            for entry in entries:
                if entry.is_dir(follow_symlinks=False):
                    sub_files, sub_size = _scan_dir_stats(entry.path)
                    total_files += sub_files
                    total_size += sub_size
                elif entry.is_file(follow_symlinks=False):
                    try:
                        total_files += 1
                        total_size += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
    except OSError:
        pass
    return total_files, total_size


# 全局单例
metrics_collector = MetricsCollector()
