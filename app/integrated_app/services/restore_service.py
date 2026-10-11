"""SeedVR2 - 修复任务编排服务层。

P0-2 分层治理：将原 routes/restore/upload.py 与 routes/restore/batch.py 中的
任务执行编排（状态机推进、进度回调、OOM 降级重试、历史落账、断点续跑）
整体迁移到服务层，路由层只保留 HTTP 协议适配（参数解析、校验、提交队列）。

架构约束（见 services/__init__.py）:
    - 本模块**禁止**依赖 FastAPI 的 Request/Response/HTTPException —— 领域错误
      统一抛 RestoreError 子类（如 DiskSpaceError），由全局异常处理器转换为
      HTTP 507/503 等响应
    - 状态修改必须通过 task_state_store 单例，禁止绕过（双真源会漂移）
    - 推理在 TaskQueue 单 worker 中串行执行，本模块不自行管理并发

主要职责:
    - ensure_disk_space: 任务前磁盘预检（领域异常 DiskSpaceError）
    - build_retry_config: runtime.retry 配置 → RetryConfig
    - model_size_from_dit_model: DiT 模型名 → 模型尺寸标识
    - create_db_progress_persister: 推理线程 → 事件循环的进度节流落库器
    - run_task_with_state: 任务执行状态机模板（processing → completed/failed/cancelled）
    - process_image_task / process_video_task: 单文件推理编排（含坏案例重试）
    - process_batch_background: 批量推理编排（含逐文件重试、OOM 降级、断点续跑）
    - apply_oom_degradation: 批量级 OOM 分类降级（参数持久到批级配置）
    - build_degradation_metadata / merge_degradation_into_parameters:
      OOM 降级事件写入历史 parameters（数据治理 P0-3，血缘可解释性）

所属项目：SeedVR2 (SeedVR2 视频/图像修复工具)
"""

import asyncio
import contextlib
import dataclasses
import inspect
import json
import logging
import os
import shutil
import time
from collections.abc import Callable

from app.integrated_app.bad_case_retry import (
    FailureType,
    RetryConfig,
    adjust_params_for_retry,
    classify_failure,
    retry_with_bad_case_detection,
)
from app.integrated_app.checkpoint import TaskCheckpoint, _file_fingerprint
from app.integrated_app.engines.seedvr2_engine import ImageInferenceConfig
from app.integrated_app.exceptions import DiskSpaceError, InsufficientVramError
from app.integrated_app.gpu_utils import (
    estimate_vram_requirements,
    get_gpu_memory_info,
    recommend_params,
)
from app.integrated_app.history_db import HistoryDB, HistoryRecord
from app.integrated_app.metrics import compute_output_mean_luma, metrics_collector
from app.integrated_app.model_registry import model_registry
from app.integrated_app.optimization.gpu.vram_leak_detector import vram_leak_detector
from app.integrated_app.services.oom_breaker import OomBreaker
from app.integrated_app.services.task_state import task_state_store
from app.integrated_app.utils.hashing import compute_file_sha256
from app.integrated_app.utils.retry import exponential_backoff_with_jitter

logger = logging.getLogger(__name__)

# P2-12：进程级 OOM 熔断器单例（阈值/冷却期由 app_config 在 oom_breaker_remaining 中同步）
oom_breaker = OomBreaker()


def oom_breaker_remaining(app_config: dict | None = None) -> float:
    """查询 OOM 熔断剩余拒绝时间（秒），并按 config 同步阈值/冷却期。

    Returns:
        float: >0 表示熔断打开（调用方应拒绝新任务并返回 Retry-After）。
    """
    cfg = (app_config or {}).get("runtime", {}).get("retry", {}).get("oom_breaker", {}) or {}
    if not cfg.get("enabled", True):
        return 0.0
    oom_breaker.configure(
        threshold=int(cfg.get("threshold", 3) or 3),
        cooldown_seconds=float(cfg.get("cooldown_seconds", 600.0) or 600.0),
    )
    return oom_breaker.remaining_cooldown()


def is_ram_exhaustion(error: str | None) -> bool:
    """判定错误是否为系统内存守卫触发（评估 P2-2）。

    RAM 守卫失败是确定性资源耗尽：重试只会重复白烧模型加载时间，
    且 OOM 降级阶梯的 blockswap（换出到 CPU）会进一步加剧系统内存压力。

    Args:
        error: 引擎返回的错误消息。

    Returns:
        bool: True 表示系统内存耗尽，调用方应跳过重试。
    """
    _has_failure, failure_type, _reason = classify_failure(message=error or "")
    return failure_type == FailureType.RAM


# ---------------------------------------------------------------------------
# 任务前预检与配置构建
# ---------------------------------------------------------------------------


def _probe_media_geometry(input_path: str, media_type: str) -> tuple[int, int, int] | None:
    """探测输入媒体的宽高与帧数（显存预检估算输入）。

    Args:
        input_path: 媒体文件路径。
        media_type: "image" 或 "video"。

    Returns:
        (width, height, num_frames) 元组；图片帧数恒为 1，视频取
        ffprobe 帧数（缺失时按时长×帧率估算）。任何探测失败返回 None，
        由门禁 fail-open 放行（OOM 由运行期降级重试兜底）。
    """
    try:
        if media_type == "image":
            from PIL import Image  # type: ignore

            with Image.open(input_path) as im:  # noqa: S311 — 上游已完成魔数校验
                width, height = im.size
            if width <= 0 or height <= 0:
                return None
            return width, height, 1

        from app.integrated_app.video_processor import FFmpegWrapper

        info = FFmpegWrapper().get_video_info(input_path)
        if info is None or info.width <= 0 or info.height <= 0:
            return None
        if info.frame_count and info.frame_count > 0:
            num_frames = int(info.frame_count)
        else:
            num_frames = max(1, int((info.duration or 0.0) * (info.fps or 0.0)))
        return info.width, info.height, max(1, num_frames)
    except Exception as e:  # noqa: BLE001 — 预检探测失败不阻塞任务
        logger.debug(f"显存预检媒体探测失败（fail-open 跳过门禁）: {input_path}: {e}")
        return None


_PRECISION_LOAD_ORDER = ("fp16", "fp8", "mxfp8", "int8_convrot", "nvfp4")


def _list_owned_precisions(model_manager: object | None, model_size: str) -> list[str] | None:
    """列出磁盘上真实存在的检查点精度（无从探测时返回 None = 不限制）。

    Args:
        model_manager: ModelManager 实例（None 时无法探测）。
        model_size: 模型尺寸标识（"3b" / "7b" / "7b_sharp"）。

    Returns:
        list[str] | None: 存在的精度列表；无 manager 或列表为空（如 CI/无权重
        环境）时返回 None，调用方按「不掌握磁盘事实」的保守旧语义处理。
    """
    if model_manager is None:
        return None
    try:
        owned: list[str] = []
        for p in _PRECISION_LOAD_ORDER:
            exists = model_manager.check_model_exists(model_size, p)  # type: ignore[attr-defined]
            if inspect.isawaitable(exists):
                # 测试用 AsyncMock 注入的 manager：探测同步语义不成立，放弃探测（不阻塞提交）
                exists.close()  # type: ignore[attr-defined]
                return None
            if exists:
                owned.append(p)
    except Exception as e:  # noqa: BLE001 — 探测失败不得阻塞任务提交
        logger.debug(f"精度可用性探测失败（按不限制处理）: {e}")
        return None
    return owned or None


def _resolve_effective_precision(
    model_manager: object | None,
    model_size: str,
    requested: str,
    owned: list[str] | None,
) -> str:
    """复刻 model_manager 的加载期精度回退链，得出**实际会加载**的精度。

    预检若按「用户所选但磁盘上不存在」的精度估算，会用错基线档位（如按 fp16
    的 16GB 门槛评估一个只有 nvfp4 的任务），造成系统性误拒。

    Args:
        model_manager: ModelManager 实例（None 时不探测）。
        model_size: 模型尺寸标识。
        requested: 用户/配置所选精度。
        owned: `_list_owned_precisions` 结果（None 时直接采纳 requested）。

    Returns:
        str: 实际会用于加载的精度标识。
    """
    if owned is None:
        return requested
    if requested in owned:
        return requested
    # 与 model_manager._load_model_locked 一致：先 fp16↔fp8 互备，再按固定顺序取第一个存在的
    counterpart = "fp8" if requested == "fp16" else "fp16"
    if counterpart in owned:
        return counterpart
    return next((p for p in _PRECISION_LOAD_ORDER if p in owned), requested)


def _max_degraded_fit(model_size: str, width: int, height: int, num_frames: int, owned: list[str] | None) -> float:
    """最大降级组合下的显存估算（GB）：fp8 存在则先换 fp8，再叠加全量 BlockSwap。

    这是门禁判定「真的装不下」的唯一依据 —— 只要该组合能容纳，任务就应该放行，
    由运行期 OOM 阶梯（`blocks_to_swap↑ → resolution↓`）继续兜底。

    Args:
        model_size: 模型尺寸标识。
        width: 输入宽度（像素）。
        height: 输入高度（像素）。
        num_frames: 帧数。
        owned: 磁盘持有的精度列表（None = 不掌握磁盘事实）。

    Returns:
        float: 降级后的估算显存需求（GB）。
    """
    num_blocks = _model_num_blocks_for(model_size)
    precision = "fp8" if (owned is None or "fp8" in owned) else "fp16"
    return estimate_vram_requirements(
        model_size, precision, width, height, num_frames, blocks_to_swap=max(1, num_blocks - 4)
    )


def _model_num_blocks_for(model_size: str) -> int:
    """取模型 Transformer 块数（回退 32，与内置默认一致）。

    Args:
        model_size: 模型尺寸标识。

    Returns:
        int: 块数。
    """
    from app.integrated_app.gpu_utils import _model_num_blocks

    return int(_model_num_blocks().get(model_size, 32))


def vram_preflight_gate(
    app_config: dict | None,
    model_size: str,
    precision: str | None,
    input_path: str,
    media_type: str,
    blocks_to_swap: int = 0,
    model_manager: object | None = None,
) -> dict | None:
    """任务提交前的显存预检门禁（成本治理 P1-2）。

    估算「实际会执行的配置」的显存需求并与可用预算比较。门禁的定位是
    **拦截必然 OOM 的任务**，不是替用户决定是否值得尝试：

    - 先按 model_manager 的加载期回退链解析**实际精度**（用户所选精度文件
      常不存在，按所选精度估算会拿错基线档位造成误拒）；
    - 预算 = mem_get_info 驱动层可用显存 + 本进程已分配显存（模型已加载时
      权重驻留会被计为不可用，需加回，避免与估算公式的权重基线二次扣减）；
    - 估算按任务实际生效的 BlockSwap 折算；
    - 只有**最大降级组合（fp8 可用则降 fp8 + 全量 BlockSwap）仍超预算**（risk=high）
      才拒绝（InsufficientVramError → HTTP 503）；其余情形一律放行，tight 时
      把降档建议写入 warning 交前端提示，OOM 由运行期降级阶梯重试兜底。

    Args:
        app_config: 应用配置（读取 runtime.vram_preflight_enabled）。
        model_size: 模型尺寸标识（"3b" / "7b" / "7b_sharp"）。
        precision: 用户/配置所选精度；None 时按 fp16 基线估算。
        input_path: 输入媒体文件路径（用于探测宽高与帧数）。
        media_type: "image" 或 "video"。
        blocks_to_swap: 任务实际生效的 BlockSwap 换出块数（计入估算）。
        model_manager: 模型管理器，用于探测磁盘上真实存在的精度并复刻回退链；
            None 时不探测（按所选精度与「不限制」语义评估）。

    Returns:
        dict | None: 放行时的预检结果（estimated_vram_gb / available_vram_gb /
        risk / warning / input_width / input_height / num_frames /
        requested_precision / effective_precision / blocks_to_swap）；
        fail-open 跳过时返回 None。

    Raises:
        InsufficientVramError: 最大降级仍放不下时抛出（HTTP 503）。
    """
    if not ((app_config or {}).get("runtime", {}) or {}).get("vram_preflight_enabled", True):
        return None

    geometry = _probe_media_geometry(input_path, media_type)
    if geometry is None:
        return None
    width, height, num_frames = geometry

    info = get_gpu_memory_info()
    if info.get("total_mb", 0) <= 0:
        return None
    # 已加载权重/常驻张量计入 allocated：mem_get_info 把它们算作不可用，
    # 而估算公式的权重基线又包含它们，直接用 available 会双重扣减
    budget_gb = (info["available_mb"] + info["allocated_mb"]) / 1024.0

    requested_precision = precision or "fp16"
    owned = _list_owned_precisions(model_manager, model_size)
    effective_precision = _resolve_effective_precision(model_manager, model_size, requested_precision, owned)

    swap = max(0, int(blocks_to_swap or 0))
    estimated = estimate_vram_requirements(model_size, effective_precision, width, height, num_frames, swap)
    recommendation = recommend_params(
        model_size, width, height, num_frames, available_vram_gb=budget_gb, available_precisions=owned
    )
    risk = str(recommendation.get("risk", "low"))
    degraded_fit = _max_degraded_fit(model_size, width, height, num_frames, owned)

    warnings: list[str] = []
    if effective_precision != requested_precision:
        owned_text = "/".join(owned) if owned else "未知"
        warnings.append(
            f"所选精度 {requested_precision} 的权重文件不存在，本次将自动改用已下载的 {effective_precision}（{owned_text}）。"
        )
    if risk == "medium":
        # 需要的降档（BlockSwap 等）尚未体现在任务配置里 → 把推荐原样转达用户
        warnings.append(str(recommendation.get("warning") or "").strip())
    if estimated > budget_gb:
        if risk != "medium":
            warnings.append("显存偏紧，建议开启 BlockSwap 或降低分辨率。")
        if swap == 0:
            warnings.append("若推理中途提示显存不足，系统会自动加大 BlockSwap 重试（速度变慢但可完成）。")

    # 唯一硬拒条件：连最大降级组合都放不下
    if risk == "high" and degraded_fit > budget_gb:
        raise InsufficientVramError(
            f"当前可用显存 {budget_gb:.1f}GB 不足以完成本次任务：即使降到低显存精度并把 BlockSwap 开到最大"
            f"仍需约 {degraded_fit:.1f}GB。建议：① 降低分辨率或减少帧数；② 开启 BlockSwap（速度换显存）；"
            f"③ 改用更小的模型，或用 scripts/download_model.py 下载更省显存的精度权重。",
            detail={
                "estimated_vram_gb": estimated,
                "degraded_vram_gb": round(degraded_fit, 2),
                "available_vram_gb": round(budget_gb, 2),
                "model_size": model_size,
                "precision": effective_precision,
                "requested_precision": requested_precision,
                "owned_precisions": owned,
                "blocks_to_swap": swap,
                "input_width": width,
                "input_height": height,
                "num_frames": num_frames,
                "recommendation": recommendation,
            },
        )

    return {
        "estimated_vram_gb": estimated,
        "available_vram_gb": round(budget_gb, 2),
        "risk": risk,
        "warning": " ".join(w for w in warnings if w),
        "input_width": width,
        "input_height": height,
        "num_frames": num_frames,
        "requested_precision": requested_precision,
        "effective_precision": effective_precision,
        "blocks_to_swap": swap,
    }


def ensure_disk_space(target_dir: str, min_free_gb: float) -> None:
    """任务启动前磁盘剩余空间预检（成本治理 P0-1）。

    检查输出目录所在磁盘的剩余空间，不足时抛出领域异常 DiskSpaceError
    （HTTP 507），避免推理中途（尤其长视频帧落盘阶段）写满磁盘导致服务不可用。

    Args:
        target_dir: 输出目录（以其所在磁盘为检查对象，无需事先存在）。
        min_free_gb: 最低剩余空间（GB），<=0 时跳过预检。

    Raises:
        DiskSpaceError: 剩余空间低于阈值时抛出（全局处理器转换为 HTTP 507）。
    """
    if not min_free_gb or min_free_gb <= 0:
        return
    # shutil.disk_usage 要求路径存在：向上回溯到最近存在的祖先目录
    # （输出目录通常在任务启动后才创建，其所在磁盘与最终落盘磁盘一致）
    probe = target_dir
    while not os.path.exists(probe):
        parent = os.path.dirname(probe)
        if parent == probe:
            break
        probe = parent
    try:
        usage = shutil.disk_usage(probe)
    except OSError:
        # 磁盘信息不可用时放行，不阻塞正常推理
        return
    free_gb = usage.free / (1024**3)
    if free_gb < min_free_gb:
        raise DiskSpaceError(
            f"磁盘剩余空间不足（当前 {free_gb:.1f}GB < 最低要求 {min_free_gb:.1f}GB），"
            f"请清理 outputs/ 输出目录或释放磁盘空间后重试",
            detail={"free_gb": round(free_gb, 2), "min_required_gb": min_free_gb},
        )


def build_retry_config(app_config: dict | None = None) -> RetryConfig:
    """从应用配置构建推理坏案例自动重试配置（成本治理 P0-2 接线）。

    读取 config.yaml 的 runtime.retry 段；enabled=false 或 max_retries=0
    时返回不重试的空配置。

    Args:
        app_config: 应用配置字典（get_app_config() 或依赖注入的 config）。

    Returns:
        RetryConfig: 传给 retry_with_bad_case_detection 的重试配置。
    """
    cfg = (app_config or {}).get("runtime", {}).get("retry", {}) or {}
    if not cfg.get("enabled", True):
        return RetryConfig(max_retries=0, enable_degradation=False, enable_seed_rotation=False)
    return RetryConfig(
        max_retries=int(cfg.get("max_retries", 2) or 0),
        base_delay_seconds=float(cfg.get("base_delay_seconds", 1.0) or 0.0),
        max_delay_seconds=float(cfg.get("max_delay_seconds", 30.0) or 1.0),
    )


def model_size_from_dit_model(dit_model: str) -> str:
    """根据 dit_model 参数确定使用的模型尺寸。

    解析模型名称字符串，提取模型尺寸标识。对于 sharp 变体保留 "size_sharp" 格式，
    其他模型只返回尺寸前缀。如参数为空则返回当前已加载模型尺寸或默认 "3b"。

    Args:
        dit_model: DiT 模型名称字符串，如 "3b_fp16"、"7b_sharp_fp16"。

    Returns:
        模型尺寸字符串，如 "3b"、"7b_sharp"。
    """
    if dit_model:
        parts = dit_model.split("_")
        if len(parts) >= 3 and parts[1] in ("sharp",):
            return f"{parts[0]}_{parts[1]}"
        return parts[0]
    return model_registry.current_model_size or "3b"


def create_db_progress_persister(
    task_id: str,
    history_db: HistoryDB,
    interval_seconds: float = 30.0,
):
    """创建「定期写数据库进度」的同步持久化器（断点续传 / 心跳）。

    引擎的进度回调运行在 `asyncio.to_thread` 的工作线程、且为同步函数，无法直接
    await 写数据库。此工厂在异步上下文调用时捕获主事件循环，返回一个同步 `persist(progress)`
    函数：按 `interval_seconds` 节流，通过 `asyncio.run_coroutine_threadsafe` 把进度
    写回数据库。

    好处：
    1. 定期同步 DB 的 `progress` 与 `updated_at` → 长视频工作期间 DB 时间戳保持新鲜，
       卡死清理不会因时间戳陈旧而误杀正常任务；
    2. 进度落盘 → 服务重启后 `recover_tasks`/断点续传能拿到更接近实时的进度。

    Args:
        task_id: 任务唯一标识。
        history_db: 历史记录数据库实例。
        interval_seconds: 两次写库的最小间隔秒数，默认 30 秒。

    Returns:
        `Callable[[float], None]`：同步进度持久化函数。
    """
    loop = asyncio.get_running_loop()
    last_persist = [0.0]

    def _consume(_fut) -> None:
        # 消费 future 结果，避免「异常未被获取」告警；忽略写库失败
        with contextlib.suppress(Exception):
            _fut.exception()

    def persist(progress: float) -> None:
        now = time.monotonic()
        if now - last_persist[0] < interval_seconds:
            return
        last_persist[0] = now
        fut = asyncio.run_coroutine_threadsafe(
            task_state_store.update(task_id, history_db, progress=float(progress)),
            loop,
        )
        fut.add_done_callback(_consume)

    return persist


def create_batch_item(path: str) -> dict:
    """创建批量任务中的单文件项结构。

    Args:
        path: 文件的绝对路径。

    Returns:
        批量任务项字典，包含 path/name/status/output_path/error/processing_time/retry_count 字段。
    """
    return {
        "path": path,
        "name": os.path.basename(path),
        "status": "pending",
        "output_path": None,
        "error": None,
        "processing_time": None,
        "retry_count": 0,
    }


# ---------------------------------------------------------------------------
# 单文件任务编排
# ---------------------------------------------------------------------------


async def run_task_with_state(
    task_id: str,
    record_id: int,
    task_fn: Callable,
    history_db: HistoryDB,
    task_queue,
    input_type: str = "image",
    model_size: str = "unknown",
):
    """公共任务执行模板 - 统一状态管理和异常处理。

    封装任务执行的通用流程：
    1. 更新状态为 processing
    2. 检查取消状态
    3. 获取引擎实例
    4. 执行实际推理函数
    5. 根据结果更新状态为 completed/failed/cancelled，并记录推理指标与输出体积
    6. 异常统一处理并记录日志

    Args:
        task_id: 任务 ID。
        record_id: 历史记录 ID。
        task_fn: 实际推理函数，接收 engine 参数，返回 RestoreResult。
        history_db: 历史数据库实例。
        task_queue: 任务队列实例。
        input_type: 输入类型（"image"/"video"），用于推理指标归因（P1-1）。
        model_size: 模型档位标识，用于推理指标归因（P1-1）。
    """
    try:
        # P1-2: 任务开始即刷新活动时间戳，空闲卸载不会打断执行中的任务
        model_registry.touch_activity()
        await task_state_store.update(task_id, history_db, status="processing")
        await history_db.update_record(record_id, status="processing")

        if task_queue.is_cancelled(task_id):
            raise asyncio.CancelledError()

        engine = model_registry.get_engine()
        if engine is None:
            raise RuntimeError("引擎实例不可用")

        result = await task_fn(engine)

        if task_queue.is_cancelled(task_id):
            raise asyncio.CancelledError()

        if result.success:
            output_size = 0
            try:
                if result.output_path and os.path.exists(result.output_path):
                    output_size = os.path.getsize(result.output_path)
            except OSError:
                output_size = 0
            # MLOps P-2：输出侧轻量质量信号（best-effort，失败不影响主流程）
            output_mean_luma = compute_output_mean_luma(result.output_path or "")
            # P2-1: VRAM 峰值落库（引擎 metadata.vram_peak_mb）
            vram_peak_mb = float((getattr(result, "metadata", None) or {}).get("vram_peak_mb") or 0.0)
            await task_state_store.update(
                task_id,
                history_db,
                status="completed",
                progress=100.0,
                output_path=result.output_path,
                processing_time=result.processing_time,
            )
            await history_db.update_record(
                record_id,
                status="completed",
                output_file=result.output_path,
                processing_time=result.processing_time,
                output_size_bytes=output_size,
                vram_peak_mb=vram_peak_mb,
            )
            # 推理指标记账（成本治理 P1-1）：/api/system/metrics 的推理计数由此点亮
            metrics_collector.record_inference(
                success=True,
                duration=result.processing_time or 0.0,
                model_size=model_size,
                input_type=input_type,
                output_mean_luma=output_mean_luma,
            )
            # P2-4：显存泄漏自动告警（峰值落库后喂给趋势检测器）
            if vram_peak_mb > 0:
                try:
                    vram_leak_detector.record(
                        peak_mb=vram_peak_mb,
                        model_size=model_size,
                        input_type=input_type,
                        task_id=task_id,
                    )
                except Exception as leak_err:  # noqa: BLE001 — 检测失败不影响主流程
                    logger.warning(f"[{task_id}] 显存泄漏检测失败: {leak_err}")
            # P2-12：成功推理复位 OOM 熔断器
            oom_breaker.record_success()
            logger.info(f"任务完成: {task_id}, 耗时 {result.processing_time:.1f}s, 输出 {output_size} 字节")
        else:
            error = result.error or "未知错误"
            await task_state_store.update(task_id, history_db, status="failed", error_message=error)
            await history_db.update_record(record_id, status="failed", error_message=error)
            metrics_collector.record_inference(
                success=False,
                duration=result.processing_time or 0.0,
                model_size=model_size,
                input_type=input_type,
            )
            # P2-12：OOM 失败计入熔断（非 OOM 失败重置连续计数）；
            # 系统内存守卫（RAM）同属资源耗尽，同样计入熔断（评估 P2-2）
            _has_failure, failure_type, _reason = classify_failure(message=error)
            oom_breaker.record_failure(is_oom=(failure_type in (FailureType.OOM, FailureType.RAM)))
            logger.error(f"任务失败: {task_id}, 错误: {result.error}")

    except asyncio.CancelledError:
        await task_state_store.update(task_id, history_db, status="cancelled", error_message="用户取消")
        await history_db.update_record(record_id, status="cancelled", error_message="用户取消")
        logger.info(f"任务已取消: {task_id}")
        raise
    except Exception as e:
        logger.error(f"任务异常: {task_id}, {e}")
        await task_state_store.update(task_id, history_db, status="failed", error_message=str(e))
        await history_db.update_record(record_id, status="failed", error_message=str(e))


async def process_image_task(
    task_id: str,
    record_id: int,
    input_path: str,
    params,
    history_db: HistoryDB,
    task_queue,
    output_name: str | None = None,
):
    """后台单张图像修复任务（服务层编排）。

    Args:
        task_id: 任务 ID。
        record_id: 历史记录 ID。
        input_path: 输入图片路径。
        params: 图像修复参数（ImageRestoreParams）。
        history_db: 历史数据库实例。
        task_queue: 任务队列实例。
        output_name: 期望输出文件名（上传分支用来保住用户原始文件名——输入路径
            已被上传缓存层改名为 `<时间戳>_<原名>_<uuid6>`，引擎从路径推会丢原名）。
            None 时由引擎按输入名生成。
    """

    # 重要：进度回调必须为同步函数。
    # infer_image 在 asyncio.to_thread 中同步执行，回调被同步调用；
    # 若此处注册 async 函数，其函数体不会被执行（仅产生未 await 的 coroutine），
    # 导致进度永远停留在 0%。
    # db_persist: 定期把进度写入 DB（刷新 updated_at + 断点续传），在异步上下文创建。
    db_persist = create_db_progress_persister(task_id, history_db)

    def _progress_callback(current_frame: int, total_frames: int, progress: float, **kwargs):
        # 仅更新内存缓存（同步），DB 持久化由 run_task_with_state 在终态时统一写
        task_state_store.update_cached(
            task_id,
            current_frame=current_frame,
            total_frames=total_frames,
            progress=round(progress, 1),
            message=kwargs.get("message", ""),
        )
        # 定期把进度同步到 DB，保证长任务工作期间 updated_at 保持新鲜
        db_persist(progress)

    async def _do_infer(engine):
        engine.set_progress_callback(_progress_callback)
        output_dir = os.path.join(os.getcwd(), "outputs", "image")
        image_config = ImageInferenceConfig(
            **{k: v for k, v in params.model_dump().items() if k in ImageInferenceConfig.__dataclass_fields__}
        )

        # OOM 坏案例自动重试（成本治理 P0-2）：降级阶梯 blocks_to_swap↑ → resolution↓ → 种子轮换。
        # 精度降级（fp16→fp8）在当前引擎架构下不生效（checkpoint 在 load_model 时固定），
        # 保留在阶梯中，引擎未来支持精度热切换后自动获益
        force_reload = {"flag": False}

        def _on_retry(attempt: int, max_attempts: int, reason: str) -> None:
            if attempt > 0:
                force_reload["flag"] = True
                logger.warning(f"[{task_id}] 推理失败，自动重试 {attempt}/{max_attempts}: {reason}")

        async def _generate(**kwargs):
            cfg = kwargs.get("config") or image_config
            if force_reload["flag"] and not cfg.force_reload_dit:
                # 已缓存的 DiT 以旧 blocks_to_swap 加载，重试必须强制重载新参数才生效
                cfg = dataclasses.replace(cfg, force_reload_dit=True)
            # P3-1：水印 payload 绑定 task_id（输出图可反查到本任务与参数）
            return await engine.infer_image(
                image_path=input_path,
                output_dir=output_dir,
                config=cfg,
                output_name=output_name,
                watermark_payload=task_id,
            )

        retry_result = await retry_with_bad_case_detection(
            _generate,
            {"config": image_config},
            config=build_retry_config(),
            progress_callback=_on_retry,
        )
        if retry_result.result is None:
            raise RuntimeError(retry_result.failure_reason or "推理重试耗尽")
        if retry_result.attempts > 1 and getattr(retry_result.result, "success", False):
            task_state_store.update_cached(
                task_id,
                message=(
                    f"自动重试成功（第 {retry_result.attempts} 次尝试"
                    + ("，参数已自动降级" if retry_result.degraded else "")
                    + "）"
                ),
            )
        # P0-3：降级事件写入历史 parameters（血缘可解释性，best-effort 不影响主流程）
        degradation_meta = build_degradation_metadata({"config": image_config}, retry_result.final_params, retry_result)
        # MLOps P1-1：实际物化的种子（-1 随机抽签或重试轮换）必须落库，否则该记录不可复现
        seed_effective = (getattr(retry_result.result, "metadata", None) or {}).get("seed_effective")
        if degradation_meta or isinstance(seed_effective, int):
            try:
                merged = merge_degradation_into_parameters(params.model_dump_json(), degradation_meta)
                if isinstance(seed_effective, int):
                    merged = merge_provenance_into_parameters(merged, {"seed_effective": seed_effective})
                await history_db.update_record(record_id, parameters=merged)
            except Exception as meta_err:  # noqa: BLE001 — 血缘记录失败不阻断任务
                logger.warning(f"[{task_id}] 降级/种子血缘写入历史失败: {meta_err}")
        return retry_result.result

    await run_task_with_state(
        task_id,
        record_id,
        _do_infer,
        history_db,
        task_queue,
        input_type="image",
        model_size=model_size_from_dit_model(params.dit_model),
    )


async def process_video_task(
    task_id: str,
    record_id: int,
    input_path: str,
    model_size: str,
    params,
    history_db: HistoryDB,
    task_queue,
    resume_frames: bool = False,
    output_name: str | None = None,
):
    """后台单视频修复任务（服务层编排）。

    包含帧进度回调，实时更新任务进度到缓存和数据库。

    Args:
        task_id: 任务 ID。
        record_id: 历史记录 ID。
        input_path: 输入视频路径。
        model_size: 模型尺寸标识。
        params: 视频修复参数（VideoRestoreParams）。
        history_db: 历史数据库实例。
        task_queue: 任务队列实例。
        resume_frames: 启用段级帧续跑（评估 P2-6）。崩溃恢复（recovery）以
            True 调用：任务自有帧目录中已完整写盘的段帧将被跳过复用。
            帧目录始终为任务隔离的 ``_frames_<task_id>``，跨任务无污染。
    """

    async def _do_infer(engine):
        # 重要：进度回调必须为同步函数。
        # infer_video 在 asyncio.to_thread 中同步执行，回调被同步调用；
        # 若此处注册 async 函数，其函数体不会被执行（仅产生未 await 的 coroutine），
        # 导致进度永远停留在 0%。
        # db_persist: 定期把进度写入 DB（刷新 updated_at + 断点续传）。
        db_persist = create_db_progress_persister(task_id, history_db)

        def progress_callback(current_frame: int, total_frames: int, progress: float, **kwargs):
            task_state_store.update_cached(
                task_id,
                current_frame=current_frame,
                total_frames=total_frames,
                progress=round(progress, 1),
            )
            # 定期把进度同步到 DB，保证长视频工作期间 updated_at 保持新鲜
            db_persist(progress)

        engine.set_progress_callback(progress_callback)

        output_dir = os.path.join(os.getcwd(), "outputs", "video")
        video_params = {
            "resolution": params.resolution,
            "max_resolution": params.max_resolution,
            "cache_model": params.cache_model,
            "seed": params.seed,
            "blocks_to_swap": params.blocks_to_swap,
            "batch_size": params.batch_size,
            "force_reload_dit": params.force_reload_dit,
        }

        # OOM 坏案例自动重试（成本治理 P0-2）：降级阶梯 blocks_to_swap↑ → resolution↓ → 种子轮换
        force_reload = {"flag": False}
        # 帧级断点续跑（成本治理 P2 + 评估 P2-6）：任务级帧目录隔离后，
        # 续跑意图=崩溃恢复传入 or 自动重试已启用。意图为 True 时引擎在
        # 失败路径保留段帧，重试/恢复才能跳过已完成段（否则退化为整体重跑）
        resume_frames_flag = {"flag": bool(resume_frames) or build_retry_config().max_retries > 0}
        # 任务隔离帧目录：段级续跑只在任务自有目录判定帧完整性，杜绝跨任务污染
        frames_dir_override = os.path.join(output_dir, f"_frames_{task_id}")

        def _on_retry(attempt: int, max_attempts: int, reason: str) -> None:
            if attempt > 0:
                force_reload["flag"] = True
                resume_frames_flag["flag"] = True
                logger.warning(f"[{task_id}] 视频推理失败，自动重试 {attempt}/{max_attempts}: {reason}")

        async def _generate(**kwargs):
            merged = {**video_params, **kwargs}
            if force_reload["flag"]:
                # 已缓存的 DiT 以旧 blocks_to_swap 加载，重试必须强制重载新参数才生效
                merged["force_reload_dit"] = True
            return await engine.infer_video(
                video_path=input_path,
                output_dir=output_dir,
                resolution=merged["resolution"],
                max_resolution=merged["max_resolution"],
                cache_model=merged["cache_model"],
                seed=merged["seed"],
                blocks_to_swap=merged["blocks_to_swap"],
                batch_size=merged["batch_size"],
                force_reload_dit=merged["force_reload_dit"],
                resume_frames=resume_frames_flag["flag"],
                frames_dir_override=frames_dir_override,
                output_name=output_name,
                # P3-1：水印 payload 绑定 task_id
                watermark_payload=task_id,
            )

        retry_result = await retry_with_bad_case_detection(
            _generate,
            dict(video_params),
            config=build_retry_config(),
            progress_callback=_on_retry,
        )
        if retry_result.result is None:
            raise RuntimeError(retry_result.failure_reason or "视频推理重试耗尽")
        if retry_result.attempts > 1 and getattr(retry_result.result, "success", False):
            task_state_store.update_cached(
                task_id,
                message=(
                    f"自动重试成功（第 {retry_result.attempts} 次尝试"
                    + ("，参数已自动降级" if retry_result.degraded else "")
                    + "）"
                ),
            )
        # P0-3：降级事件写入历史 parameters（血缘可解释性，best-effort 不影响主流程）
        degradation_meta = build_degradation_metadata(dict(video_params), retry_result.final_params, retry_result)
        # MLOps P1-1：实际物化的种子（-1 随机抽签或重试轮换）必须落库，否则该记录不可复现
        seed_effective = (getattr(retry_result.result, "metadata", None) or {}).get("seed_effective")
        if degradation_meta or isinstance(seed_effective, int):
            try:
                # 重写 parameters 会整体覆盖创建时的值，先重注入 ffmpeg 版本血缘（数据治理 P1-2）
                merged = apply_ffmpeg_lineage(params.model_dump_json(), "video")
                merged = merge_degradation_into_parameters(merged, degradation_meta)
                if isinstance(seed_effective, int):
                    merged = merge_provenance_into_parameters(merged, {"seed_effective": seed_effective})
                await history_db.update_record(record_id, parameters=merged)
            except Exception as meta_err:  # noqa: BLE001 — 血缘记录失败不阻断任务
                logger.warning(f"[{task_id}] 降级/种子血缘写入历史失败: {meta_err}")
        return retry_result.result

    await run_task_with_state(
        task_id,
        record_id,
        _do_infer,
        history_db,
        task_queue,
        input_type="video",
        model_size=model_size,
    )


# ---------------------------------------------------------------------------
# 批量任务编排
# ---------------------------------------------------------------------------


def apply_oom_degradation(
    current_config: dict,
    batch_config: dict,
    error: str,
    attempt: int,
    app_config: dict,
) -> bool:
    """批量重试前的 OOM 分类与参数降级（成本治理 P0-2）。

    按 bad_case_retry 阶梯降级（blocks_to_swap↑ → resolution↓ → 种子轮换），
    调整结果同时写回当前文件配置与批量级配置——OOM 在批内通常是持续性
    显存约束，后续文件复用降级参数可避免逐文件重复 OOM 白烧 GPU 时间。

    Args:
        current_config: 当前文件的推理配置副本（就地更新）。
        batch_config: 批量级推理配置（就地更新，影响后续文件）。
        error: 失败错误信息。
        attempt: 即将进行的重试序号（1-based）。
        app_config: 应用全局配置（读取 runtime.retry）。

    Returns:
        是否发生了参数降级。
    """
    has_failure, failure_type, _reason = classify_failure(message=error)
    if not has_failure or failure_type != FailureType.OOM:
        return False
    adjusted = adjust_params_for_retry(current_config, failure_type, attempt, build_retry_config(app_config))
    merged = {k: v for k, v in adjusted.items() if k in current_config and v != current_config[k]}
    if not merged:
        return False
    current_config.update(merged)
    batch_config.update(merged)
    logger.warning(f"批量任务 OOM，参数已自动降级并应用于后续文件: {merged}")
    return True


# ---------------------------------------------------------------------------
# OOM 降级血缘（数据治理 P0-3）：降级事件写入历史 parameters，
# 使"低质量但成功"的输出在血缘中可解释（用户可识别该结果经历过自动降级）
# ---------------------------------------------------------------------------

# 降级阶梯可能触及的参数键，写入血缘时逐键对比 from → to
_DEGRADATION_TRACKED_KEYS = ("blocks_to_swap", "resolution", "max_resolution", "dit_model", "seed")


def _param_value(params: dict | None, key: str):
    """从参数字典取值：优先嵌套 config dataclass，回退顶层键。"""
    if not params:
        return None
    cfg = params.get("config")
    if cfg is not None and dataclasses.is_dataclass(cfg) and hasattr(cfg, key):
        return getattr(cfg, key)
    return params.get(key)


def build_degradation_metadata(
    original_params: dict | None,
    final_params: dict | None,
    retry_result,
) -> dict | None:
    """根据重试结果构造降级血缘元数据。

    Args:
        original_params: 首次推理使用的参数字典（{"config": ...} 或扁平 dict）。
        final_params: 重试结束后实际生效的参数字典。
        retry_result: bad_case_retry.RetryResult（或具有 degraded/attempts/
            failure_reason 属性的等价对象）。

    Returns:
        降级元数据 dict；未发生降级时返回 None。
    """
    if retry_result is None or not getattr(retry_result, "degraded", False):
        return None
    adjusted: dict[str, dict] = {}
    for key in _DEGRADATION_TRACKED_KEYS:
        old_value = _param_value(original_params, key)
        new_value = _param_value(final_params, key)
        if new_value != old_value:
            adjusted[key] = {"from": old_value, "to": new_value}
    return {
        "degraded": True,
        "attempts": int(getattr(retry_result, "attempts", 0) or 0),
        "adjusted": adjusted,
        "failure_reason": (getattr(retry_result, "failure_reason", "") or "")[:500],
        "note": "推理失败自动重试，参数已按降级阶梯调整",
    }


def merge_degradation_into_parameters(parameters_json: str, metadata: dict | None) -> str:
    """把降级元数据合并进历史记录的 parameters JSON 字符串。

    parameters 本就是 JSON 字符串列，合并策略：反序列化 → 写入 "degradation"
    键 → 重新序列化。原参数反序列化失败时把原文挂到 "raw" 键兜底，不丢数据。

    Args:
        parameters_json: 原始 parameters JSON 字符串（可能为空）。
        metadata: build_degradation_metadata 的返回值，None 时原样返回。

    Returns:
        合并后的 parameters JSON 字符串。
    """
    if not metadata:
        return parameters_json or ""
    try:
        data = json.loads(parameters_json) if parameters_json else {}
    except (ValueError, TypeError):
        data = {"raw": parameters_json}
    if not isinstance(data, dict):
        data = {"raw": parameters_json}
    data["degradation"] = metadata
    return json.dumps(data, ensure_ascii=False)


def merge_provenance_into_parameters(parameters_json: str, provenance: dict) -> str:
    """把运行期血缘字段（顶层键）合并进 parameters JSON（MLOps 报告 P1-1）。

    与 merge_degradation_into_parameters 同构：反序列化 → dict.update → 重序列化；
    非法 / 非 dict JSON 时把原文挂到 "raw" 键兜底，不丢数据。
    provenance 为空时原样返回。典型字段：seed_effective。

    Args:
        parameters_json: 原始 parameters JSON 字符串（可能为空）。
        provenance: 要合并进顶层的键值字典。

    Returns:
        合并后的 parameters JSON 字符串。
    """
    if not provenance:
        return parameters_json or ""
    try:
        data = json.loads(parameters_json) if parameters_json else {}
    except (ValueError, TypeError):
        data = {"raw": parameters_json}
    if not isinstance(data, dict):
        data = {"raw": parameters_json}
    data.update(provenance)
    return json.dumps(data, ensure_ascii=False)


def apply_ffmpeg_lineage(parameters_json: str, media_type: str) -> str:
    """视频任务把 ffmpeg 版本写入 parameters JSON（数据治理 P1-2 编码器血缘）。

    同一输出在多年后仍能回答「当时用什么编码器编码」。仅视频链路注入
    （图像链路不经过 ffmpeg）；版本探测失败或 JSON 异常时原样返回——
    血缘缺失可接受，绝不影响批量主流程。

    Args:
        parameters_json: 原始 parameters JSON 字符串（可能为空串）。
        media_type: 媒体类型（"image"/"video"）。

    Returns:
        注入 ffmpeg_version 后的 JSON 字符串（或原串）。
    """
    if media_type != "video":
        return parameters_json
    try:
        from app.integrated_app.video_processor import get_ffmpeg_version

        version = get_ffmpeg_version()
        if not version:
            return parameters_json
        data = json.loads(parameters_json) if parameters_json else {}
        if not isinstance(data, dict):
            data = {"raw": parameters_json}
        data["ffmpeg_version"] = version
        return json.dumps(data, ensure_ascii=False)
    except (ValueError, TypeError, ImportError) as e:
        logger.debug(f"ffmpeg 版本血缘注入跳过: {e}")
        return parameters_json


async def process_batch_background(
    batch_id: str,
    media_files: list,
    media_type: str,
    config: dict,
    use_model_size: str,
    history_db: HistoryDB,
    task_queue,
    app_config: dict,
    results_to_update: list | None = None,
    double_res: bool = False,
):
    """后台逐个处理批量任务（含自动重试 + 断点续跑）（服务层编排）。

    顺序处理媒体文件列表，每个文件失败后使用指数退避 + 抖动自动重试，
    重试次数和间隔从配置读取。处理过程中实时更新缓存和数据库状态。
    支持断点续跑：每个文件处理完成后保存 checkpoint，崩溃重启后可恢复。
    历史账目逐文件即时落库（P1-7），崩溃不丢已完成文件的记录。

    Args:
        batch_id: 批量任务 ID。
        media_files: 待处理文件路径列表。
        media_type: 媒体类型 "image"/"video"。
        config: 推理参数配置。
        use_model_size: 模型尺寸标识。
        history_db: 历史数据库实例。
        task_queue: 任务队列实例。
        app_config: 应用全局配置。
        results_to_update: 重试时传入的已有结果项列表（复用原结构），可选。
        double_res: 两倍模式开关（每个文件单独按短边×2 计算分辨率）。
    """
    task_state = await task_state_store.get(batch_id, history_db)
    if task_state is None:
        return

    # 心跳持久化器：在批量处理期间定期把进度写入 DB（刷新 updated_at + 断点续传），
    # 避免长视频被「卡死清理」因 DB 时间戳陈旧而误杀。
    db_persist = create_db_progress_persister(batch_id, history_db)

    # 初始化断点续跑管理器
    task_cfg = app_config.get("runtime", {}).get("task", {})
    checkpoint_dir = task_cfg.get("checkpoint_dir", "data/checkpoints")
    checkpoint_every = task_cfg.get("checkpoint_every", 1)
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    checkpoint_mgr = TaskCheckpoint(os.path.join(project_root, checkpoint_dir))

    # 加载已有 checkpoint，跳过已完成的文件
    completed_fingerprints = checkpoint_mgr.get_completed_fingerprints(batch_id)
    completed_files_list: list[dict] = []
    if completed_fingerprints:
        # 从 checkpoint 恢复已完成文件列表
        ckpt_data = checkpoint_mgr.load_checkpoint(batch_id)
        if ckpt_data:
            completed_files_list = ckpt_data.get("completed_files", [])
        logger.info(f"批量任务 {batch_id} 从断点恢复：已完成 {len(completed_fingerprints)} 个文件")

    cached = task_state_store.get_cached_or_create(
        batch_id,
        template={
            "task_id": batch_id,
            "type": "batch",
            "media_type": media_type,
            "total": len(media_files),
            "completed": 0,
            "failed": 0,
            "current_index": -1,
            "current_file": "",
            "results": [],
            "config": config,
            "use_model_size": use_model_size,
        },
    )

    results = cached["results"]
    completed = 0
    failed = 0
    engine = model_registry.get_engine()

    if engine is None:
        await task_state_store.update(
            batch_id,
            history_db,
            status="failed",
            error_message="引擎实例不可用",
        )
        return

    # 磁盘空间预检（成本治理 P0-1）：批量任务输出量大，启动前先确认剩余空间。
    # 空间不足时直接标记任务失败并返回（逐文件落库语义下不会有任何账目残留）
    try:
        ensure_disk_space(
            os.path.join(os.getcwd(), "outputs"),
            float((app_config.get("retention", {}) or {}).get("disk_min_free_gb", 5.0) or 0),
        )
    except DiskSpaceError as e:
        await task_state_store.update(batch_id, history_db, status="failed", error_message=e.message)
        return

    # 默认输出子目录名 (仅模板解析失败时回退时使用)
    output_subdir = "image" if media_type == "image" else "video"

    batch_cfg = app_config.get("runtime", {}).get("batch", {})
    max_retries = batch_cfg.get("max_retries", 2)
    retry_base = batch_cfg.get("retry_base_delay_seconds", 1.0)
    retry_max = batch_cfg.get("retry_max_delay_seconds", 30.0)

    for i, media_path in enumerate(media_files):
        # 断点续跑：跳过已完成的文件
        if media_path in completed_fingerprints:
            fp = completed_fingerprints[media_path]
            # 验证文件指纹（大小 + 修改时间）是否一致
            current_fp = _file_fingerprint(media_path)
            if fp.get("size", 0) == current_fp.get("size", 0) and fp.get("mtime", 0) == current_fp.get("mtime", 0):
                logger.debug(f"断点续跑：跳过已结束文件 {media_path}")
                completed += 1
                task_state_store.update_cached(batch_id, completed=completed)
                # 为跳过的文件创建结果项
                task_item = create_batch_item(media_path)
                task_item["status"] = "completed"
                task_item["output_path"] = fp.get("output_path", "")
                results.append(task_item)
                # P1-7：逐文件即时落库，崩溃/重启不再丢失已完成文件的账目
                skip_geometry = _probe_media_geometry(media_path, media_type)
                await history_db.add_record(
                    HistoryRecord(
                        task_type=media_type,
                        input_file=media_path,
                        model_size=use_model_size,
                        status="completed",
                        output_file=fp.get("output_path", ""),
                        input_width=skip_geometry[0] if skip_geometry else 0,
                        input_height=skip_geometry[1] if skip_geometry else 0,
                        input_duration_sec=skip_geometry[2] if skip_geometry else 0.0,
                    )
                )
                continue
            else:
                logger.warning(f"断点续跑：文件指纹不匹配，重新处理 {media_path}")

        if task_queue.is_cancelled(batch_id):
            for remaining in media_files[i:]:
                cancel_geometry = _probe_media_geometry(remaining, media_type)
                await history_db.add_record(
                    HistoryRecord(
                        task_type=media_type,
                        input_file=remaining,
                        model_size=use_model_size,
                        status="cancelled",
                        error_message="批量任务被取消",
                        input_width=cancel_geometry[0] if cancel_geometry else 0,
                        input_height=cancel_geometry[1] if cancel_geometry else 0,
                        input_duration_sec=cancel_geometry[2] if cancel_geometry else 0.0,
                    )
                )
            break

        if results_to_update is not None and i < len(results_to_update):
            task_item = results_to_update[i]
            task_item["status"] = "processing"
            task_item["retry_count"] = 0
        else:
            task_item = create_batch_item(media_path)
            task_item["status"] = "processing"
            results.append(task_item)
        current_filename = os.path.basename(media_path)
        task_state_store.update_cached(batch_id, current_index=i, current_file=current_filename)

        last_error = None
        # P0-3：本文件的 OOM 降级事件记录（写入该文件历史记录 parameters）
        degradation_events: list[dict] = []

        # 两倍模式：每个文件需要单独计算分辨率（因为不同文件分辨率可能不同）
        current_config = config.copy()  # 为当前文件创建配置副本
        if double_res and media_type == "image":
            try:
                from PIL import Image

                with Image.open(media_path) as im:
                    width, height = im.size
                    short_edge = min(width, height)
                    target_res = short_edge * 2
                    current_config["resolution"] = target_res
                    logger.info(
                        f"[double_res] 文件 {os.path.basename(media_path)}: 图片尺寸 {width}x{height} -> 短边 {short_edge} -> 分辨率 {target_res}"
                    )
            except Exception as e:
                logger.warning(f"[double_res] 无法读取图片尺寸 {media_path}, 保留原分辨率：{e}")

        for attempt in range(max_retries + 1):
            task_item["retry_count"] = attempt
            try:
                # 输出路径模板渲染 (user_preferences.output_path_template):
                # 占位符 {project_root}/{task_type}/{input_dir}/{input_name}/{ext}
                # -> 项目根目录/任务类型 (image/video)/输入目录/输入文件名 (不含扩展名)/扩展名 (含点号)
                # 默认模板 "{project_root}/outputs/{task_type}/restored/{input_name}{ext}" 输出到项目根目录的 outputs 子目录
                try:
                    template = app_config.get("user_preferences", {}).get(
                        "output_path_template", "{project_root}/outputs/{task_type}/restored/{input_name}{ext}"
                    )
                    project_root = os.getcwd()
                    task_type_for_template = media_type  # "image" or "video"
                    input_dir = os.path.dirname(media_path)
                    input_stem = os.path.splitext(os.path.basename(media_path))[0]
                    input_ext = os.path.splitext(media_path)[1]  # 含点号，如 ".png"
                    rendered = (
                        template.replace("{project_root}", project_root)
                        .replace("{task_type}", task_type_for_template)
                        .replace("{input_dir}", input_dir)
                        .replace("{input_name}", input_stem)
                        .replace("{ext}", input_ext)
                    )
                    # 渲染结果缺少文件名 (空模板/以分隔符结尾): 视为解析失败
                    # 注：必须在 normpath 之前检查，否则空串被归一化为 "." 会绕过校验
                    if not os.path.basename(rendered):
                        raise ValueError(f"模板渲染结果缺少文件名：{rendered!r}")
                    # 归一化分隔符 (模板用 / 而 Windows 用反斜杠): 保证输出路径风格统一
                    rendered = os.path.normpath(rendered)
                    output_dir = os.path.dirname(rendered)
                    output_name = os.path.basename(rendered)
                    # 模板未含目录 (如仅文件名) 时回退到输入文件所在目录，再回退当前工作目录
                    if not output_dir:
                        output_dir = input_dir or os.getcwd()
                    # 渲染结果为 "."/".." 等相对名：视为解析失败
                    if output_name in (".", ".."):
                        raise ValueError(f"模板渲染结果缺少有效文件名：{rendered!r}")
                except Exception as e:
                    # 模板解析失败：回退到原行为 (outputs/{image|video} + 默认随机命名), 不中断批量任务
                    logger.warning(f"输出路径模板解析失败，回退默认输出目录：{e}")
                    output_dir = os.path.join(os.getcwd(), "outputs", output_subdir)
                    output_name = None
                await asyncio.to_thread(os.makedirs, output_dir, exist_ok=True)

                if media_type == "image":
                    image_config = ImageInferenceConfig(
                        **{k: v for k, v in current_config.items() if k in ImageInferenceConfig.__dataclass_fields__}
                    )
                    result = await engine.infer_image(
                        image_path=media_path,
                        output_dir=output_dir,
                        config=image_config,
                        output_name=output_name,
                        # P3-1：批量场景水印绑定 batch_id（批内统一归属）
                        watermark_payload=batch_id,
                    )
                else:
                    # 批量任务的进度回调（同步函数 - 推理在工作线程同步执行）
                    # 注意：使用默认参数捕获 i，避免闭包延迟绑定问题
                    def progress_callback(current_frame: int, total_frames: int, progress: float, _i=i, **kwargs):
                        task_state_store.update_cached(
                            batch_id,
                            current_index=_i,
                            current_progress=round(progress, 1),
                        )
                        # 定期把进度写 DB，保证批处理内长视频期间 updated_at 保持新鲜
                        db_persist(progress)

                    engine.set_progress_callback(progress_callback)
                    # OOM 降级可能向 current_config 注入 blocks_to_swap；
                    # 仅在键存在时传入，避免用 0 覆盖引擎默认的 blockswap 配置
                    infer_kwargs = {
                        "resolution": current_config["resolution"],
                        "max_resolution": current_config["max_resolution"],
                        "cache_model": current_config["cache_model"],
                        "seed": current_config["seed"],
                    }
                    if "blocks_to_swap" in current_config:
                        infer_kwargs["blocks_to_swap"] = current_config["blocks_to_swap"]
                    if attempt > 0:
                        # 帧级断点续跑（成本治理 P2）：同文件重试复用上一轮已写盘的段帧
                        # （分辨率降档时引擎侧尺寸校验会自动放弃复用，整段重算）
                        infer_kwargs["resume_frames"] = True
                    result = await engine.infer_video(
                        video_path=media_path,
                        output_dir=output_dir,
                        output_name=output_name,
                        # P3-1：批量场景水印绑定 batch_id
                        watermark_payload=batch_id,
                        **infer_kwargs,
                    )

                if result.success:
                    task_item["status"] = "completed"
                    task_item["output_path"] = result.output_path
                    task_item["processing_time"] = result.processing_time
                    task_item["error"] = None
                    completed += 1
                    # 推理指标记账 + 输出体积统计（成本治理 P1-1）
                    output_size = 0
                    try:
                        if result.output_path and os.path.exists(result.output_path):
                            output_size = os.path.getsize(result.output_path)
                    except OSError:
                        output_size = 0
                    # MLOps P-2：输出侧轻量质量信号（视频产出由 helper 判扩展名后返回 None）
                    output_mean_luma = compute_output_mean_luma(result.output_path or "")
                    task_item["output_size_bytes"] = output_size
                    task_item["vram_peak_mb"] = float(
                        (getattr(result, "metadata", None) or {}).get("vram_peak_mb") or 0.0
                    )
                    metrics_collector.record_inference(
                        success=True,
                        duration=result.processing_time or 0.0,
                        model_size=use_model_size,
                        input_type=media_type,
                        output_mean_luma=output_mean_luma,
                    )
                    # P2-4：批量链路同样喂入显存泄漏检测器
                    batch_vram = float(task_item.get("vram_peak_mb") or 0.0)
                    if batch_vram > 0:
                        try:
                            vram_leak_detector.record(
                                peak_mb=batch_vram,
                                model_size=use_model_size,
                                input_type=media_type,
                                task_id=batch_id,
                            )
                        except Exception as leak_err:  # noqa: BLE001 — 检测失败不影响主流程
                            logger.warning(f"[{batch_id}] 显存泄漏检测失败: {leak_err}")
                    task_state_store.update_cached(batch_id, completed=completed)

                    # 断点续跑：保存 checkpoint
                    completed_files_list.append({**_file_fingerprint(media_path), "output_path": result.output_path})
                    remaining_files = media_files[i + 1 :]
                    if checkpoint_mgr.should_checkpoint(completed, checkpoint_every):
                        checkpoint_mgr.save_checkpoint(
                            batch_id,
                            total=len(media_files),
                            completed_files=completed_files_list,
                            remaining=remaining_files,
                            config=current_config,
                            media_type=media_type,
                            use_model_size=use_model_size,
                        )
                    break
                else:
                    last_error = result.error or "未知错误"
                    if attempt < max_retries and not is_ram_exhaustion(last_error):
                        task_item["status"] = "retrying"
                        logger.warning(
                            f"批量处理 {media_type} {i+1}/{len(media_files)} 第{attempt+1}次失败，重试中：{media_path}, {last_error}"
                        )
                        # OOM 坏案例降级（P0-2）：重试前分类失败并调整参数
                        if apply_oom_degradation(current_config, config, last_error, attempt + 1, app_config):
                            # P0-3：记录降级事件供血缘落库
                            degradation_events.append(
                                {
                                    "attempt": attempt + 1,
                                    "error": last_error[:300],
                                    "adjusted": {
                                        k: current_config.get(k)
                                        for k in _DEGRADATION_TRACKED_KEYS
                                        if k in current_config
                                    },
                                }
                            )
                        await exponential_backoff_with_jitter(attempt, base=retry_base, max_delay=retry_max)
                    else:
                        task_item["status"] = "failed"
                        task_item["error"] = last_error
                        failed += 1
                        metrics_collector.record_inference(
                            success=False,
                            duration=float(result.processing_time or 0.0),
                            model_size=use_model_size,
                            input_type=media_type,
                        )
                        task_state_store.update_cached(batch_id, failed=failed)
                        if is_ram_exhaustion(last_error):
                            # 评估 P2-2：RAM 守卫属资源耗尽，跳过重试并计入熔断
                            logger.error(f"批量处理遇系统内存耗尽，跳过该文件重试：{media_path}")
                            oom_breaker.record_failure(is_oom=True)

            except asyncio.CancelledError:
                task_item["status"] = "cancelled"
                task_item["error"] = "用户取消"
                raise
            except Exception as e:
                last_error = str(e)
                if attempt < max_retries and not is_ram_exhaustion(last_error):
                    task_item["status"] = "retrying"
                    logger.warning(
                        f"批量处理 {media_type} {i+1}/{len(media_files)} 第{attempt+1}次异常，重试中：{media_path}, {e}"
                    )
                    # OOM 坏案例降级（P0-2）：重试前分类异常并调整参数
                    if apply_oom_degradation(current_config, config, last_error, attempt + 1, app_config):
                        # P0-3：记录降级事件供血缘落库
                        degradation_events.append(
                            {
                                "attempt": attempt + 1,
                                "error": last_error[:300],
                                "adjusted": {
                                    k: current_config.get(k) for k in _DEGRADATION_TRACKED_KEYS if k in current_config
                                },
                            }
                        )
                    await exponential_backoff_with_jitter(attempt, base=retry_base, max_delay=retry_max)
                else:
                    task_item["status"] = "failed"
                    task_item["error"] = last_error
                    failed += 1
                    metrics_collector.record_inference(
                        success=False,
                        duration=0.0,
                        model_size=use_model_size,
                        input_type=media_type,
                    )
                    task_state_store.update_cached(batch_id, failed=failed)
                    if is_ram_exhaustion(last_error):
                        # 评估 P2-2：RAM 守卫属资源耗尽，跳过重试并计入熔断
                        logger.error(f"批量处理遇系统内存耗尽，跳过该文件重试：{media_path}")
                        oom_breaker.record_failure(is_oom=True)
                    logger.error(f"批量处理 {media_type} {i+1}/{len(media_files)} 最终失败：{media_path}, {e}")

        # P1-7：每个文件处理完成后立即落库（原实现攒到批末一次性插入，
        # 崩溃即丢失整批账目；逐文件插入在 WAL 单写者模型下开销可忽略）
        # P0-3：批量链路发生降级时，把事件写入该文件记录的 parameters（血缘可解释性）
        batch_parameters_json = ""
        # P1-1：源文件内容哈希（内容寻址血缘，线程内计算不阻塞事件循环）
        input_sha256 = await asyncio.to_thread(compute_file_sha256, media_path)
        if degradation_events:
            batch_parameters_json = json.dumps(
                {
                    "degradation": {
                        "degraded": True,
                        "events": degradation_events,
                        "note": "批量推理失败自动重试，参数已按降级阶梯调整",
                    }
                },
                ensure_ascii=False,
            )
        # 数据治理 P1-2：视频任务把 ffmpeg 版本写入 parameters（编码器血缘，
        # 进程级缓存；置于降级合并之后，避免被覆盖）
        batch_parameters_json = apply_ffmpeg_lineage(batch_parameters_json, media_type)
        # 输入分布台账（评估 P2-4a）：批量主路径每文件探测一次宽高/时长；失败留 0
        geometry = _probe_media_geometry(media_path, media_type)
        await history_db.add_record(
            HistoryRecord(
                task_type=media_type,
                input_file=media_path,
                model_size=use_model_size,
                status=task_item["status"],
                parameters=batch_parameters_json,
                output_file=task_item.get("output_path") or "",
                processing_time=float(task_item.get("processing_time") or 0.0),
                error_message=task_item.get("error") or "",
                output_size_bytes=int(task_item.get("output_size_bytes") or 0),
                vram_peak_mb=float(task_item.get("vram_peak_mb") or 0.0),
                input_sha256=input_sha256,
                input_width=geometry[0] if geometry else 0,
                input_height=geometry[1] if geometry else 0,
                input_duration_sec=geometry[2] if geometry else 0.0,
            )
        )

        progress = round(((i + 1) / len(media_files)) * 100, 1)
        await task_state_store.update(batch_id, history_db, progress=progress)

    final_status = "cancelled" if task_queue.is_cancelled(batch_id) else "completed"
    final_cached = task_state_store.get_cached(batch_id) or {}
    await task_state_store.update(
        batch_id,
        history_db,
        status=final_status,
        progress=100.0 if final_status == "completed" else final_cached.get("progress", 0),
    )

    # 批量任务完成后清理 checkpoint
    if final_status == "completed":
        checkpoint_mgr.remove_checkpoint(batch_id)
        logger.info(f"批量任务 {batch_id} checkpoint 已清理")

    logger.info(f"批量任务 {batch_id} 完成：{completed} 成功，{failed} 失败")
