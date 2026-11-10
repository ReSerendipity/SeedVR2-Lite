#!/usr/bin/env python3
"""批量修复路由模块（HTTP 适配层）。

提供文件夹批量媒体修复功能。任务执行编排（逐文件顺序执行、指数退避重试、
OOM 分类降级、断点续跑）已迁移到 services/restore_service.py（P0-2 分层治理），
本模块只负责 HTTP 参数解析、校验与任务入队。

API 端点：
- POST /api/restore/batch: 创建批量修复任务
- GET /api/restore/batch/{batch_id}/progress: 查询批量任务进度
- POST /api/restore/batch/{batch_id}/cancel: 取消进行中的批量任务
- POST /api/restore/batch/{batch_id}/retry: 重试批量任务中失败的文件

所属项目：SeedVR2 (SeedVR2 视频/图像修复工具)
"""

import asyncio
import logging
import os
import re
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request

from app.integrated_app.config_models import (
    ImageRestoreParams,
    UnifiedRestoreParams,
    VideoRestoreParams,
)
from app.integrated_app.dependencies import (
    get_config,
    get_history_db,
    get_model_manager,
    get_task_queue,
)
from app.integrated_app.exceptions import TaskQueueFullError
from app.integrated_app.gpu_backend import gpu_manager
from app.integrated_app.history_db import HistoryDB
from app.integrated_app.model_manager import ModelManager
from app.integrated_app.model_registry import model_registry
from app.integrated_app.routes.restore import common
from app.integrated_app.services.restore_service import (
    model_size_from_dit_model,
    process_batch_background,
    vram_preflight_gate,
)
from app.integrated_app.spec import precision_from_dit_model
from app.integrated_app.task_queue import TaskQueue
from app.integrated_app.utils.response import respond_success

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/restore", tags=["修复"])

# P1-4：客户端幂等键合法格式（与 upload.py 一致）
_IDEMPOTENCY_KEY_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")


def _resolve_idempotency_key(request: Request, form_key: str | None) -> str | None:
    """解析并校验批量任务幂等键（P1-4）。格式非法抛 400。"""
    raw = (request.headers.get("Idempotency-Key") or form_key or "").strip()
    if not raw:
        return None
    if not _IDEMPOTENCY_KEY_RE.match(raw):
        raise HTTPException(
            status_code=400,
            detail="幂等键格式非法：仅允许字母/数字/下划线/点/连字符，长度 1-64",
        )
    return raw


@router.post("/batch")
async def batch_restore_from_folder(
    request: Request,
    folder_path: str = Form(...),
    task_type: str = Form("auto"),
    idempotency_key: str | None = Form(None),
    raw_params: UnifiedRestoreParams = Depends(common.parse_unified_params),
    config: dict = Depends(get_config),
    history_db: HistoryDB = Depends(get_history_db),
    task_queue: TaskQueue = Depends(get_task_queue),
    model_manager: ModelManager = Depends(get_model_manager),
):
    """批量处理文件夹中的媒体文件（后台异步，逐个顺序执行）。

    API 端点：POST /api/restore/batch

    请求参数（multipart/form-data）：
    - folder_path (required): 要处理的文件夹绝对路径
    - task_type (optional): "auto"/"image"/"video"，默认 "auto" 自动检测
    - 其他修复参数（dit_model, seed 等，详见 common.parse_unified_params）

    返回格式（JSON，统一包装 {success, data, error}）：
    {
        "success": true,
        "data": {
            "batch_id": str,      // 批量任务 ID
            "total": int,         // 待处理文件总数
            "media_type": "image"|"video",
            "status": "processing"
        }
    }

    错误响应：
    - 400: 参数错误（文件夹不存在、无可处理文件等）
    - 503: GPU 不可用，或模型自动加载失败
    - 507: 磁盘剩余空间不足

    Args:
        folder_path: 目标文件夹路径。
        task_type: 任务类型过滤。
        raw_params: 解析后的修复参数。
        config: 应用配置。
        history_db: 历史数据库实例。
        task_queue: 任务队列实例。
        model_manager: 模型管理器。

    Returns:
        包含 batch_id 的 JSON 响应。

    Raises:
        HTTPException: 校验失败或服务不可用时抛出。
    """
    # ============== 幂等键（P1-4） ==============
    # 先于 GPU 能力检查：同键重复提交返回既有任务，与 GPU 是否可用无关
    client_key = _resolve_idempotency_key(request, idempotency_key)
    if client_key is not None:
        existing_task = await history_db.get_task(client_key)
        if existing_task is not None:
            logger.info(f"幂等命中：batch_id={client_key} 已存在（status={existing_task.status}），未重复创建")
            return respond_success(
                {
                    "batch_id": client_key,
                    "record_id": existing_task.record_id,
                    "status": existing_task.status,
                    "duplicate": True,
                    "message": "幂等命中：同键批量任务已存在，未重复创建",
                }
            )

    # OOM 熔断检查（P2-12）：连续 OOM 达到阈值后拒绝新任务，避免队列白烧 GPU。
    # 先于 GPU 能力检查：熔断是全局安全状态，无 GPU 环境（CI）也应观测到该语义
    from app.integrated_app.services.restore_service import oom_breaker_remaining, quality_breaker_remaining

    _breaker_remaining = oom_breaker_remaining(config)
    if _breaker_remaining > 0:
        raise HTTPException(
            status_code=503,
            detail=(
                f"连续推理 OOM 触发熔断，请降低分辨率/切换更小模型后重试（约 {_breaker_remaining:.0f} 秒后自动恢复）"
            ),
            headers={"Retry-After": str(int(_breaker_remaining) + 1)},
        )

    # 输出质量劣化熔断检查（评估 A3 整改）：批量链路与单任务同语义
    _quality_remaining = quality_breaker_remaining(config)
    if _quality_remaining > 0:
        raise HTTPException(
            status_code=503,
            detail=(
                f"输出质量劣化触发熔断（疑似全黑输出），已自动卸载疑似异常权重；"
                f"约 {_quality_remaining:.0f} 秒后自动放行探测，若复现请检查模型权重完整性"
            ),
            headers={"Retry-After": str(int(_quality_remaining) + 1)},
        )

    if not gpu_manager.is_gpu_available:
        raise HTTPException(
            status_code=503,
            detail="SeedVR2 需要 GPU 推理（支持 NVIDIA CUDA / AMD ROCm / Apple Silicon MPS），当前未检测到可用 GPU。请安装对应 GPU 与 PyTorch 后端。",
        )

    # 自动加载模型：未加载（或尺寸不符）时先加载再修复
    await common.ensure_model_loaded(model_manager, raw_params.dit_model)

    # 磁盘空间预检（成本治理 P0-1）：空间不足直接 507（DiskSpaceError → 全局处理器），
    # 避免任务入队后静默失败
    common.ensure_disk_space(
        os.path.join(os.getcwd(), "outputs"),
        float((config.get("retention", {}) or {}).get("disk_min_free_gb", 5.0) or 0),
    )

    folder = Path(folder_path.strip())
    if not await asyncio.to_thread(folder.exists) or not await asyncio.to_thread(folder.is_dir):
        raise HTTPException(status_code=400, detail=f"文件夹不存在：{folder_path}")

    media_files = []
    for root, _dirs, files in await asyncio.to_thread(lambda: list(os.walk(folder))):
        for fname in sorted(files):
            ext = os.path.splitext(fname)[1].lower()
            detected = common.detect_media_type(ext)
            if detected:
                media_files.append((os.path.join(root, fname), detected))

    if task_type != "auto":
        media_files = [(p, t) for p, t in media_files if t == task_type]

    if not media_files:
        raise HTTPException(status_code=400, detail=f"文件夹中未找到可处理文件：{folder_path}")

    # P1-2：批量入口对齐上传分支的输入防线（大小 + 魔数），堵旁路校验缺口
    common.validate_local_media_files(media_files, config)

    actual_type = task_type if task_type != "auto" else media_files[0][1]

    dit_model = raw_params.dit_model
    use_model_size = model_size_from_dit_model(dit_model)

    # ============== 显存预检门禁（成本治理 P1-2） ==============
    # 以首个文件为代表、按**实际会执行的配置**估算（精度经 model_manager 回退链解析，
    # BlockSwap 取表单值与 config inference.blocks_to_swap 的较大者）：
    # 仅「最大降级仍放不下」才拒绝（InsufficientVramError → 全局处理器 503，消息含可操作
    # 降档建议），显存偏紧一律放行并把 warning 返回前端 + 写入批量任务状态缓存
    # （批量内 OOM 差异由运行期批级降级重试兜底）
    vram_preflight = vram_preflight_gate(
        config,
        use_model_size,
        precision_from_dit_model(dit_model) or (config.get("model", {}) or {}).get("default_precision", "fp16"),
        media_files[0][0],
        actual_type,
        blocks_to_swap=max(int(raw_params.blocks_to_swap or 0), common.config_blocks_to_swap(config)),
        model_manager=model_manager,
    )
    vram_warning = (vram_preflight or {}).get("warning", "")

    params: ImageRestoreParams | VideoRestoreParams
    if actual_type == "image":
        image_fields = {k: v for k, v in raw_params.model_dump().items() if k in ImageRestoreParams.model_fields}
        params = ImageRestoreParams(**image_fields)
        task_config = params.model_dump()
    else:
        params = VideoRestoreParams(
            seed=raw_params.seed,
            resolution=raw_params.resolution,
            max_resolution=raw_params.max_resolution,
            cache_model=raw_params.dit_cache_model,
        )
        task_config = {
            "resolution": params.resolution,
            "max_resolution": params.max_resolution,
            "cache_model": params.cache_model,
            "seed": params.seed,
        }

    batch_id = client_key or uuid.uuid4().hex[: config.get("runtime", {}).get("task", {}).get("id_length", 16)]

    batch_results = [common.create_batch_item(path) for path, _ in media_files]
    await common.create_task_state(batch_id, 0, history_db, task_type="batch")
    common.get_task_cache().update(
        batch_id,
        **{
            "type": "batch",
            "media_type": actual_type,
            "total": len(media_files),
            "completed": 0,
            "failed": 0,
            "current_index": -1,
            "current_file": "",
            "results": batch_results,
            "config": task_config,
            "use_model_size": use_model_size,
            "vram_warning": vram_warning,
        },
    )
    await common.update_task_state(batch_id, history_db, status="processing")

    engine = model_registry.get_engine()
    on_cancel = engine.request_cancel if engine else None
    paths_only = [p for p, _ in media_files]
    # 传递两倍模式配置
    double_res_flag = raw_params.double_res
    try:
        await task_queue.submit(
            batch_id,
            lambda: process_batch_background(
                batch_id,
                paths_only,
                actual_type,
                task_config,
                use_model_size,
                history_db,
                task_queue,
                config,
                double_res=double_res_flag,
            ),
            on_cancel=on_cancel,
        )
    except TaskQueueFullError as e:
        # 评估 P2-1：队列满快速拒绝。批量任务未入队，回写失败账目避免残留 processing 态
        await common.update_task_state(batch_id, history_db, status="failed", error_message=e.message)
        raise common.queue_full_rejection(e) from e

    return respond_success(
        {
            "batch_id": batch_id,
            "total": len(media_files),
            "media_type": actual_type,
            "vram_warning": vram_warning,
            "status": "processing",
        }
    )


@router.get("/batch/{batch_id}/progress")
async def get_batch_progress(batch_id: str, history_db: HistoryDB = Depends(get_history_db)):
    """获取批量处理任务进度。

    API 端点：GET /api/restore/batch/{batch_id}/progress

    路径参数：
    - batch_id: 批量任务 ID

    返回格式（JSON）：
    {
        "success": true,
        "data": {
            "batch_id": str,
            "status": "pending"|"processing"|"completed"|"failed"|"cancelled",
            "progress": float,     // 0-100
            "total": int,
            "completed": int,
            "failed": int,
            "current_index": int,
            "results": [ ... ],    // 每个文件的详细状态
            "media_type": "image"|"video"
        }
    }

    错误响应：
    - 404: 批量任务不存在

    Args:
        batch_id: 批量任务 ID。
        history_db: 历史数据库实例。

    Returns:
        批量任务进度详情。

    Raises:
        HTTPException: 任务不存在时抛出。
    """
    task = await common.get_task_state(batch_id, history_db)
    if not task:
        raise HTTPException(status_code=404, detail="批量任务不存在")

    cached = common.get_task_cache().get(batch_id, {})
    assert cached is not None
    return respond_success(
        {
            "batch_id": batch_id,
            "status": task.get("status", "unknown"),
            "progress": task.get("progress", 0),
            "total": cached.get("total", 0),
            "completed": cached.get("completed", 0),
            "failed": cached.get("failed", 0),
            "current_index": cached.get("current_index", -1),
            "current_file": cached.get("current_file", ""),
            "results": cached.get("results", []),
            "media_type": cached.get("media_type", "image"),
        }
    )


@router.post("/batch/{batch_id}/cancel")
async def cancel_batch(
    batch_id: str,
    history_db: HistoryDB = Depends(get_history_db),
    task_queue: TaskQueue = Depends(get_task_queue),
):
    """取消进行中的批量修复任务。

    API 端点：POST /api/restore/batch/{batch_id}/cancel

    路径参数：
    - batch_id: 批量任务 ID

    返回格式（JSON）：
    {
        "success": true,
        "data": {
            "batch_id": str,
            "status": "cancelled",
            "message": "批量任务已取消"
        }
    }

    错误响应：
    - 404: 批量任务不存在
    - 400: 任务状态不允许取消（已完成/失败/已取消）

    说明：批量任务的历史记录是**逐文件**落库的，这里不改动单条记录；
    后台循环在每次文件迭代前检查 ``task_queue.is_cancelled(batch_id)``，
    并在退出点把最终状态定为 ``cancelled``（见 services/restore_service.py）。

    Args:
        batch_id: 批量任务 ID。
        history_db: 历史数据库实例。
        task_queue: 任务队列实例。

    Returns:
        取消操作结果。

    Raises:
        HTTPException: 任务不存在或状态不合法时抛出。
    """
    task = await common.get_task_state(batch_id, history_db)
    if not task:
        raise HTTPException(status_code=404, detail="批量任务不存在")

    status = task.get("status", "unknown")
    if status not in ("pending", "processing"):
        raise HTTPException(status_code=400, detail=f"任务状态为 {status}，无法取消")

    task_queue.request_cancel(batch_id)
    await common.update_task_state(batch_id, history_db, status="cancelled", error_message="用户取消")
    return respond_success({"batch_id": batch_id, "status": "cancelled", "message": "批量任务已取消"})


@router.post("/batch/{batch_id}/retry")
async def retry_failed_batch(
    batch_id: str,
    history_db: HistoryDB = Depends(get_history_db),
    task_queue: TaskQueue = Depends(get_task_queue),
    config: dict = Depends(get_config),
):
    """重试批量任务中失败的文件。

    API 端点：POST /api/restore/batch/{batch_id}/retry

    路径参数：
    - batch_id: 批量任务 ID

    返回格式（JSON）：
    {
        "success": true,
        "data": {
            "message": str,
            "retry_count": int  // 本次重试的文件数
        }
    }

    错误响应：
    - 404: 批量任务不存在
    - 400: 任务未完成或详情丢失

    Args:
        batch_id: 批量任务 ID。
        history_db: 历史数据库实例。
        task_queue: 任务队列实例。
        config: 应用配置。

    Returns:
        重试操作结果。

    Raises:
        HTTPException: 任务不存在或状态不合法时抛出。
    """
    task = await common.get_task_state(batch_id, history_db)
    if not task:
        raise HTTPException(status_code=404, detail="批量任务不存在")

    cached = common.get_task_cache().get(batch_id)
    if not cached or "results" not in cached:
        raise HTTPException(status_code=400, detail="任务详情已丢失，无法重试")

    if cached["status"] != "completed":
        raise HTTPException(status_code=400, detail="任务尚未完成，无法重试")

    failed_items = [(i, r) for i, r in enumerate(cached["results"]) if r["status"] == "failed"]
    if not failed_items:
        return respond_success({"message": "没有失败的文件需要重试"})

    for _i, r in failed_items:
        r["status"] = "pending"
        r["error"] = None
        r["retry_count"] = 0

    common.get_task_cache().update(batch_id, status="processing", failed=0, current_index=-1)

    retry_files = [r["path"] for _, r in failed_items]
    retry_results = [r for _, r in failed_items]
    task_config = cached.get("config", {})
    use_model_size = cached.get("use_model_size", "3b")
    media_type = cached.get("media_type", "image")
    # 从缓存的配置中获取 double_res 设置（如果有）
    double_res_flag = config.get("user_preferences", {}).get("double_res", False)

    engine = model_registry.get_engine()
    on_cancel = engine.request_cancel if engine else None
    try:
        await task_queue.submit(
            batch_id,
            lambda: process_batch_background(
                batch_id,
                retry_files,
                media_type,
                task_config,
                use_model_size,
                history_db,
                task_queue,
                config,
                results_to_update=retry_results,
                double_res=double_res_flag,
            ),
            on_cancel=on_cancel,
        )
    except TaskQueueFullError as e:
        # 评估 P2-1：队列满快速拒绝。重试未入队，把已重置为 pending 的条目
        # 恢复为 failed，缓存状态回退 completed，保持与 DB 账目一致
        for _i, r in failed_items:
            r["status"] = "failed"
            r["error"] = e.message
        common.get_task_cache().update(batch_id, status="completed", failed=len(failed_items))
        raise common.queue_full_rejection(e) from e

    return respond_success(
        {
            "message": f"开始重试 {len(retry_files)} 个失败文件",
            "retry_count": len(retry_files),
        }
    )
