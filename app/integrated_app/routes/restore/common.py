#!/usr/bin/env python3
"""修复路由公共工具模块（HTTP 适配层）。

P0-2 分层治理后，本模块只保留 HTTP 路由侧的公共设施：
- 支持的媒体文件扩展名常量定义
- 文件大小限制常量
- 统一修复参数解析（表单到 Pydantic 模型）
- 模型自动加载（HTTP 503 语义）
- 两倍模式后端强制校验
- TaskStateStoreProxy 兼容类（保持原 dict-like 接口）

任务执行编排（状态机、OOM 降级重试、断点续跑、历史落账）已迁移到
services/restore_service.py —— 本模块对其做**再导出**以保持既有
`from ...routes.restore import common` 调用方兼容；新代码应直接
从 `app.integrated_app.services.restore_service` 导入。

API 路由前缀：/api/restore（由子模块注册）
所属项目：SeedVR2 (SeedVR2 视频/图像修复工具)
"""

import logging
import math
import os
from collections.abc import Sequence

from fastapi import Depends, Form, HTTPException

from app.integrated_app.config_models import UnifiedRestoreParams
from app.integrated_app.dependencies import get_config
from app.integrated_app.exceptions import TaskQueueFullError
from app.integrated_app.model_registry import model_registry
from app.integrated_app.security.magic_check import validate_upload_magic
from app.integrated_app.services.restore_service import (  # noqa: F401 — 再导出保持兼容
    build_retry_config,
    create_batch_item,
    create_db_progress_persister,
    ensure_disk_space,
    model_size_from_dit_model,
)
from app.integrated_app.services.task_state import task_state_store
from app.integrated_app.spec import precision_from_dit_model

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tiff", ".tif"}

VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".flv"}

ALLOWED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tiff"}

ALLOWED_VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".flv", ".wmv"}

# 上传大小默认上限；实际值从 config.yaml runtime.security.max_upload_*_mb 读取
# （见 upload.py / batch.py），此常量仅作配置缺失时的回退默认值。
MAX_IMAGE_SIZE = 50 * 1024 * 1024
MAX_VIDEO_SIZE = 500 * 1024 * 1024

MAX_RETRIES = 2

logger = logging.getLogger(__name__)


def queue_full_rejection(exc: TaskQueueFullError) -> HTTPException:
    """TaskQueueFullError → HTTP 503 + Retry-After（提交类路由统一转换，评估 P2-1）。

    Args:
        exc: 任务队列已满领域异常。

    Returns:
        HTTPException: 携带 Retry-After 头的 503 响应异常。
    """
    return HTTPException(
        status_code=503,
        detail=exc.message,
        headers={"Retry-After": "5"},
    )


async def ensure_model_loaded(model_manager, dit_model: str = "") -> None:
    """模型未就绪时自动加载（幂等），加载完成后再执行修复。

    `model_manager.load_model` 内部会短路：当前已加载同尺寸/精度模型时直接跳过，
    否则加载所请求尺寸与精度的模型，因此每次上传前调用是安全的。

    Args:
        model_manager: ModelManager 实例。
        dit_model: DiT 模型名（如 "3b_fp16"、"7b_sharp_int8_convrot"），
            同时决定要加载的模型尺寸与精度；无法解析精度时回退配置默认值。

    Raises:
        HTTPException: 模型自动加载失败时抛出 503。
    """
    # P1-2: 活动时间戳刷新，避免模型在队列排队期间被空闲卸载
    model_registry.touch_activity()
    try:
        await model_manager.load_model(
            model_size=model_size_from_dit_model(dit_model),
            precision=precision_from_dit_model(dit_model),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"自动加载模型失败: {e}")
        raise HTTPException(status_code=503, detail=f"模型自动加载失败: {e}") from e


def detect_media_type(file_ext: str) -> str | None:
    """根据文件扩展名判断媒体类型。

    Args:
        file_ext: 文件扩展名（含点号，大小写不敏感），如 ".png"、".MP4"。

    Returns:
        "image" 表示图片，"video" 表示视频；不支持的扩展名返回 None。
    """
    ext = file_ext.lower()
    if ext in ALLOWED_IMAGE_EXTENSIONS:
        return "image"
    if ext in ALLOWED_VIDEO_EXTENSIONS:
        return "video"
    return None


def config_blocks_to_swap(config: dict | None) -> int:
    """读取引擎加载期生效的 BlockSwap 换出块数（`inference.blocks_to_swap`）。

    该值与表单的 `blocks_to_swap` 是两个来源：引擎在加载模型时按配置换块
    （`engines/seedvr2_engine.py`），显存预检若只看表单值会低估实际生效配置、
    高估显存需求。调用方按「两者取大」估算即可覆盖真实驻留。

    Args:
        config: 应用配置 dict。

    Returns:
        int: 换出块数，缺失/非法时返回 0（视为不启用）。
    """
    try:
        return int(((config or {}).get("inference", {}) or {}).get("blocks_to_swap", 0) or 0)
    except (TypeError, ValueError):
        return 0


def validate_local_media_files(media_files: Sequence[tuple[str, str | None]], config: dict) -> None:
    """folder 模式本地媒体文件安全校验（大小上限 + 魔数，数据治理 P1-2）。

    与 multipart 上传分支对齐同一套输入防线：扩展名白名单已在文件收集时
    保证，本函数补充大小上限与魔数校验，堵住"本地文件绕过上传校验"
    的旁路缺口（伪装扩展名/损坏文件不再静默进入推理管线）。

    Args:
        media_files: 待校验 (文件路径, 媒体类型 "image"/"video") 列表。
        config: 应用配置 dict（读取 runtime.security.max_upload_*_mb）。

    Raises:
        HTTPException: 任一文件大小超限、不可读或魔数校验失败时抛出 400。
    """
    security_cfg = (config.get("runtime", {}) or {}).get("security", {}) or {}
    max_image_size = int(security_cfg.get("max_upload_image_mb", 50) or 50) * 1024 * 1024
    max_video_size = int(security_cfg.get("max_upload_video_mb", 500) or 500) * 1024 * 1024

    for path, media_type in media_files:
        # 调用方可能只给出路径（类型待推断）：按扩展名补齐，仍无法识别则拒绝
        resolved_type = media_type or detect_media_type(os.path.splitext(path)[1].lower())
        if resolved_type not in ("image", "video"):
            raise HTTPException(status_code=400, detail=f"不支持的文件类型: {os.path.basename(path)}")

        try:
            size = os.path.getsize(path)
        except OSError as e:
            raise HTTPException(status_code=400, detail=f"无法读取文件大小: {path}: {e}") from e

        limit = max_image_size if resolved_type == "image" else max_video_size
        if size > limit:
            raise HTTPException(
                status_code=400,
                detail=f"文件大小超过限制（最大 {limit // (1024 * 1024)}MB）: {os.path.basename(path)}",
            )

        try:
            with open(path, "rb") as f:
                header = f.read(32)
        except OSError as e:
            raise HTTPException(status_code=400, detail=f"无法读取文件内容: {path}: {e}") from e

        file_ext = os.path.splitext(path)[1].lower()
        magic_ok, _dtype, magic_err = validate_upload_magic(header, file_ext)
        if not magic_ok:
            raise HTTPException(status_code=400, detail=f"{os.path.basename(path)}: {magic_err}")


def parse_unified_params(
    task_type: str = Form("auto"),
    dit_model: str = Form("3b_fp16"),
    dit_device: str = Form("auto"),
    blocks_to_swap: int = Form(32),
    swap_io_components: bool = Form(True),
    dit_offload_device: str = Form("cpu"),
    dit_cache_model: bool = Form(True),
    force_reload_dit: bool = Form(False),
    attention_mode: str = Form("sdpa"),
    vae_model: str = Form("ema_vae_fp16"),
    vae_device: str = Form("auto"),
    encode_tiled: bool = Form(True),
    encode_tile_size: int = Form(1024),
    encode_tile_overlap: int = Form(512),
    decode_tiled: bool = Form(True),
    decode_tile_size: int = Form(1024),
    decode_tile_overlap: int = Form(512),
    tile_debug: str = Form("false"),
    vae_offload_device: str = Form("cpu"),
    vae_cache_model: bool = Form(True),
    seed: int = Form(1373201197),
    resolution: int = Form(2160),
    max_resolution: int = Form(0),
    batch_size: int = Form(5),
    uniform_batch_size: bool = Form(True),
    color_correction: str = Form("lab"),
    temporal_overlap: int = Form(2),
    prepend_frames: int = Form(0),
    input_noise_scale: float = Form(0.0),
    latent_noise_scale: float = Form(0.0),
    offload_device: str = Form("cpu"),
    enable_debug: bool = Form(False),
    double_res: bool = Form(False),
    output_format: str = Form(""),  # 输出格式："png"/"jpg"/"webp"/"bmp"/"tiff"，空串表示自动匹配输入
    config: dict = Depends(get_config),
) -> UnifiedRestoreParams:
    """解析统一修复表单参数，返回结构化 Pydantic 模型。

    作为 FastAPI Dependency 使用，从 multipart/form-data 中提取所有修复参数
    并构造 UnifiedRestoreParams 实例，供后续按任务类型（图像/视频）提取对应字段。

    Args:
        task_type: 任务类型，"auto"/"image"/"video"，默认 "auto"。
        dit_model: DiT 模型名称，默认 "3b_fp16"。
        dit_device: DiT 推理设备，默认 "auto"（自动选择 CUDA/ROCm/MPS）。
        blocks_to_swap: 交换到 CPU 的 transformer 块数量，默认 32。
        swap_io_components: 是否交换 I/O 组件，默认 True。
        dit_offload_device: DiT 卸载设备，默认 "cpu"。
        dit_cache_model: 是否缓存 DiT 模型，默认 True。
        force_reload_dit: 是否强制重载 DiT，默认 False。
        attention_mode: 注意力实现模式，默认 "sdpa"。
        vae_model: VAE 模型名称，默认 "ema_vae_fp16"。
        vae_device: VAE 推理设备，默认 "auto"（自动选择 CUDA/ROCm/MPS）。
        encode_tiled: 是否使用分块 VAE 编码，默认 True。
        encode_tile_size: VAE 编码分块大小，默认 1024。
        encode_tile_overlap: VAE 编码分块重叠，默认 512。
        decode_tiled: 是否使用分块 VAE 解码，默认 True。
        decode_tile_size: VAE 解码分块大小，默认 1024。
        decode_tile_overlap: VAE 解码分块重叠，默认 512。
        tile_debug: 分块调试模式，默认 "false"。
        vae_offload_device: VAE 卸载设备，默认 "cpu"。
        vae_cache_model: 是否缓存 VAE 模型，默认 True。
        seed: 随机种子，默认 1373201197。
        resolution: 输出分辨率，默认 2160。
        max_resolution: 最大分辨率限制，0 表示不限制，默认 0。
        batch_size: 批处理大小（需满足 4n+1，非法值自动修正），默认 5。
        uniform_batch_size: 是否统一批处理大小，默认 True。
        color_correction: 颜色校正模式，默认 "lab"。
        temporal_overlap: 视频帧时序重叠数，默认 2。
        prepend_frames: 前置参考帧数，默认 0。
        input_noise_scale: 输入噪声缩放，默认 0.0。
        latent_noise_scale: 隐空间噪声缩放，默认 0.0。
        offload_device: 通用卸载设备，默认 "cpu"。
        enable_debug: 是否启用调试模式，默认 False。
        double_res: 两倍模式（短边×2），默认 False。
        output_format: 输出格式，空串表示自动匹配输入。

    Returns:
        构造完成的 UnifiedRestoreParams 实例。
    """
    if batch_size < 1:
        batch_size = 1
    elif (batch_size - 1) % 4 != 0:
        batch_size = max(1, 4 * max(0, round((batch_size - 1) / 4)) + 1)
    _params = UnifiedRestoreParams(
        task_type=task_type,
        dit_model=dit_model,
        dit_device=dit_device,
        blocks_to_swap=blocks_to_swap,
        swap_io_components=swap_io_components,
        dit_offload_device=dit_offload_device,
        dit_cache_model=dit_cache_model,
        force_reload_dit=force_reload_dit,
        attention_mode=attention_mode,
        vae_model=vae_model,
        vae_device=vae_device,
        encode_tiled=encode_tiled,
        encode_tile_size=encode_tile_size,
        encode_tile_overlap=encode_tile_overlap,
        decode_tiled=decode_tiled,
        decode_tile_size=decode_tile_size,
        decode_tile_overlap=decode_tile_overlap,
        tile_debug=tile_debug,
        vae_offload_device=vae_offload_device,
        vae_cache_model=vae_cache_model,
        seed=seed,
        resolution=resolution,
        max_resolution=max_resolution,
        batch_size=batch_size,
        uniform_batch_size=uniform_batch_size,
        color_correction=color_correction,
        temporal_overlap=temporal_overlap,
        prepend_frames=prepend_frames,
        input_noise_scale=input_noise_scale,
        latent_noise_scale=latent_noise_scale,
        offload_device=offload_device,
        enable_debug=enable_debug,
        double_res=double_res,
        output_format=output_format,
    )
    # 记录本次请求实际收到的输出格式（排查"默认/自动"是否被悄悄改成其它格式）
    logger.info(
        f"[restore/params] output_format={output_format!r} double_res={double_res} resolution={resolution} task_type={task_type}"
    )
    enforce_max_output_side(_params, config)
    return _params


def resolve_max_output_side(config: dict | None) -> int:
    """读取服务端输出长边硬上限（像素）；0 表示不限制，配置非法时退回默认 6144。"""
    raw = ((config or {}).get("inference", {}) or {}).get("max_output_side", 6144)
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        logger.warning("[restore/params] inference.max_output_side 配置非法(%r)，按默认 6144 处理", raw)
        return 6144


def enforce_max_output_side(params: UnifiedRestoreParams, config: dict | None) -> None:
    """服务端输出长边硬上限校验（就地修正 max_resolution，超限直接拒绝）。

    与 ``max_resolution`` 的分工：后者是请求侧的长边上限（0=不限，客户端决定），
    本函数读的是 ``inference.max_output_side`` —— 服务端兜底，挡住
    「客户端传 resolution=20000」这类必然 OOM 且会长时间占用 GPU 的请求。
    对齐 VOSR2-Lite 的同名配置项。

    Args:
        params: 已解析的请求参数（可能被就地钳位 max_resolution）。
        config: 应用配置字典。

    Raises:
        HTTPException: 请求分辨率超过服务端上限时返回 400，并给出可用上限。
    """
    cap = resolve_max_output_side(config)
    if cap <= 0:
        return

    if params.resolution > cap:
        raise HTTPException(
            status_code=400,
            detail=(
                f"输出分辨率 {params.resolution}px 超过服务端上限 {cap}px：请把分辨率调到 "
                f"{cap} 以内（config.yaml 的 inference.max_output_side，0 表示不限制）"
            ),
        )

    if params.max_resolution > cap:
        logger.warning(
            "[restore/params] max_resolution=%d 超过服务端上限 %d，已钳位到上限",
            params.max_resolution,
            cap,
        )
        params.max_resolution = cap


DOUBLE_RES_MAX_PIXELS = 8_000_000
"""两倍模式下输出像素上限（约 4K/8MP）。

「短边×2」意味着**像素数变成 4 倍**。一张 1672×941 的图会被放大到 3344×1882
（≈6.3MP），VAE 解码峰值实测 5.45GB（tile=1024），叠加 DiT 常驻 6.3GB 后
12GB 卡直接显存超卖 → WDDM 分页 → 表现为静默卡死（2026-09-10 实测）。
这里设上限并按比例回缩短边，超限时记录 warning 而不是静默放大。
"""


def enforce_double_resolution_if_enabled(
    raw_params: UnifiedRestoreParams,
    detected_type: str | None,
    input_path: str | None,
    max_output_side: int = 0,
) -> None:
    """两倍模式后端强制校验 —— 就地修改 raw_params.resolution。

    规则（互斥保证）：
    1. 仅当 raw_params.double_res 为 True 且 detected_type == "image" 时生效；
    2. 使用 Pillow 重新读取 input_path 指向的真实图片宽高；
    3. 将 raw_params.resolution 覆写为 short_edge × 2，无论客户端表单传入什么；
    4. 若图片无法解析，记录 warning 并保持客户端传来的 resolution（fail-safe，不中断用户）；
    5. 视频文件 / folder_path 场景若传入了非图片类型 → 即使 double_res=True 也不覆写，仅记录 info 日志。

    Args:
        raw_params: parse_unified_params 返回的 UnifiedRestoreParams 实例，函数内就地修改其 resolution。
        detected_type: 媒体检测结果，"image"/"video"/None。
        input_path: 已保存到磁盘的上传文件路径（或本地文件夹模式首个媒体路径）。
    """
    if not raw_params.double_res:
        return

    if not input_path or not os.path.isfile(input_path):
        logger.warning("[double_res] 开关开启但输入路径无效，跳过强制覆写: %s", input_path)
        return

    if detected_type not in ("image", "video"):
        logger.info(
            "[double_res] 开关开启但当前文件类型 (%s) 不支持两倍检测，保留原分辨率 %d (path=%s)",
            detected_type,
            raw_params.resolution,
            input_path,
        )
        return

    width, height = _read_media_dimensions(detected_type, input_path, raw_params.resolution)
    if width is None or height is None:
        return

    if width <= 0 or height <= 0:
        logger.warning(
            "[double_res] 媒体宽高非法 (%dx%d)，保留原分辨率 %d (path=%s)",
            width,
            height,
            raw_params.resolution,
            input_path,
        )
        return

    short_edge = min(width, height)
    target_res = short_edge * 2

    # P1-7：像素上限保护。输出像素 = 输入像素 × (target_res / short_edge)²，
    # 「短边×2」即 4 倍像素；超限则按比例回缩 target_res（向下取整到 8 的倍数）。
    input_pixels = width * height
    if input_pixels > 0 and DOUBLE_RES_MAX_PIXELS > 0:
        projected_pixels = input_pixels * (target_res / short_edge) ** 2
        if projected_pixels > DOUBLE_RES_MAX_PIXELS:
            capped_res = int(short_edge * math.sqrt(DOUBLE_RES_MAX_PIXELS / input_pixels))
            capped_res = max(8, (capped_res // 8) * 8)
            if capped_res < target_res:
                logger.warning(
                    "[double_res] 短边×2 将产生 %.1fMP 输出，超过上限 %.1fMP；" "分辨率由 %d 回缩到 %d (输出约 %.1fMP)",
                    projected_pixels / 1e6,
                    DOUBLE_RES_MAX_PIXELS / 1e6,
                    target_res,
                    capped_res,
                    input_pixels * (capped_res / short_edge) ** 2 / 1e6,
                )
                target_res = capped_res

    # 服务端硬上限：两倍模式算出的分辨率同样受 inference.max_output_side 约束
    # （8MP 像素上限管的是显存峰值，本上限管的是单边尺寸，两者互补）
    if max_output_side > 0 and target_res > max_output_side:
        logger.warning(
            "[double_res] 目标分辨率 %d 超过服务端上限 %d，已钳位到上限",
            target_res,
            max_output_side,
        )
        target_res = max_output_side

    original_res = raw_params.resolution
    raw_params.resolution = target_res
    # 同步覆写 4 个 VAE tile 参数，与前端自动填值规则保持一致：
    #   tile_size = short_edge × 100%    (向下取整到整数，且不小于 64)
    #   tile_overlap = short_edge × 50%  (且必须 ≤ tile_size // 2，避免 VAE 分块重叠越界)
    safe_tile_size = max(64, int(short_edge))
    safe_tile_overlap = max(0, min(int(short_edge * 0.5), safe_tile_size // 2))
    original_encode_tile_size = raw_params.encode_tile_size
    original_encode_tile_overlap = raw_params.encode_tile_overlap
    original_decode_tile_size = raw_params.decode_tile_size
    original_decode_tile_overlap = raw_params.decode_tile_overlap
    raw_params.encode_tile_size = safe_tile_size
    raw_params.encode_tile_overlap = safe_tile_overlap
    raw_params.decode_tile_size = safe_tile_size
    raw_params.decode_tile_overlap = safe_tile_overlap

    logger.info(
        "[double_res] 已按「短边×2」强制设置分辨率 & tile 参数: "
        "图片尺寸 %dx%d -> 短边 %d -> 分辨率 %d (原 %d 丢弃), "
        "encode_tile %d/%d (原 %d/%d 丢弃), "
        "decode_tile %d/%d (原 %d/%d 丢弃)",
        width,
        height,
        short_edge,
        target_res,
        original_res,
        safe_tile_size,
        safe_tile_overlap,
        original_encode_tile_size,
        original_encode_tile_overlap,
        safe_tile_size,
        safe_tile_overlap,
        original_decode_tile_size,
        original_decode_tile_overlap,
    )


def _read_media_dimensions(
    detected_type: str,
    input_path: str,
    current_resolution: int,
) -> tuple[int | None, int | None]:
    """按媒体类型读取真实宽高（两倍模式专用）。

    - image: 用 Pillow 读取图片尺寸。
    - video: 用 FFmpegWrapper.ffprobe 读取视频分辨率。

    任何读取失败都返回 (None, None)，由调用方 fail-safe 保留原分辨率。

    Args:
        detected_type: 媒体类型，"image"/"video"。
        input_path: 已保存到磁盘的媒体文件路径。
        current_resolution: 当前分辨率（仅用于日志）。

    Returns:
        (width, height)，读取失败返回 (None, None)。
    """
    if detected_type == "image":
        try:
            # 延迟导入 — 只有开关打开时才触发，避免极少数环境缺失 Pillow
            from PIL import Image  # type: ignore
        except ImportError as e:
            logger.warning("[double_res] 开关开启但 Pillow 不可用，无法读取图片宽高: %s", e)
            return None, None

        try:
            with Image.open(input_path) as im:  # noqa: S311 — 魔数校验已在上游 validate_upload_magic 完成
                return im.size
        except Exception as e:  # noqa: BLE001 — 任何解码失败都 fail-safe
            logger.warning(
                "[double_res] 图片解析失败，保留原分辨率 %d: path=%s error=%s", current_resolution, input_path, e
            )
            return None, None

    if detected_type == "video":
        try:
            from app.integrated_app.video_processor import FFmpegWrapper
        except Exception as e:  # noqa: BLE001
            logger.warning("[double_res] 开关开启但无法初始化 FFmpeg，无法读取视频宽高: %s", e)
            return None, None

        try:
            info = FFmpegWrapper().get_video_info(input_path)
            if info is None or info.width <= 0 or info.height <= 0:
                logger.warning("[double_res] 视频解析失败，保留原分辨率 %d: path=%s", current_resolution, input_path)
                return None, None
            return info.width, info.height
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "[double_res] 视频解析失败，保留原分辨率 %d: path=%s error=%s", current_resolution, input_path, e
            )
            return None, None

    return None, None


async def create_task_state(task_id: str, record_id: int, history_db, task_type: str = "single") -> dict:
    """在数据库与内存缓存中创建任务初始状态。

    Args:
        task_id: 任务唯一标识。
        record_id: 历史记录数据库 ID。
        history_db: 历史记录数据库实例。
        task_type: 任务类型，"single"/"batch"，默认 "single"。

    Returns:
        创建的初始任务状态字典。
    """
    return await task_state_store.create(task_id, record_id, history_db, task_type=task_type)


async def get_task_state(task_id: str, history_db) -> dict | None:
    """获取任务状态；优先读内存缓存，缓存未命中则回源数据库。

    Args:
        task_id: 任务唯一标识。
        history_db: 历史记录数据库实例。

    Returns:
        任务状态字典；任务不存在返回 None。
    """
    return await task_state_store.get(task_id, history_db)


async def update_task_state(task_id: str, history_db, **kwargs) -> dict:
    """更新数据库任务状态并同步到内存缓存。

    Args:
        task_id: 任务唯一标识。
        history_db: 历史记录数据库实例。
        **kwargs: 要更新的状态字段（如 status, progress, error_message 等）。

    Returns:
        更新后的任务状态字典。
    """
    return await task_state_store.update(task_id, history_db, **kwargs)


def get_task_cache() -> "TaskStateStoreProxy":
    """返回任务状态存储代理（供批量任务/重试等操作使用）。

    返回的 TaskStateStoreProxy 实例包装了全局 task_state_store，
    提供与原 OrderedDict 类似的 dict-like 接口（__getitem__/get/__setitem__/__contains__/update），
    确保线程安全与单一数据源。

    注意：__getitem__/get 返回的是浅拷贝，顶层字段直接修改不会生效，
    需要修改时必须通过 update() 方法写回。嵌套 list/dict（如 results）仍为引用共享，
    修改其中元素会直接影响缓存。

    Returns:
        TaskStateStoreProxy 实例。
    """
    return TaskStateStoreProxy(task_state_store)


def get_cached_or_create(task_id: str, template: dict | None = None) -> dict:
    """从缓存获取任务状态，不存在则用 template 创建并写入缓存。

    Args:
        task_id: 任务唯一标识。
        template: 用于初始化的模板字典，可选；默认为空字典。

    Returns:
        任务状态字典（浅拷贝）。
    """
    return task_state_store.get_cached_or_create(task_id, template=template)


class TaskStateStoreProxy:
    """任务状态存储代理类 - 兼容原 OrderedDict 接口。

    包装 task_state_store，提供 dict-like 接口，使原有代码中
    `common.get_task_cache()[task_id]` 和 `common.get_task_cache().get(task_id)`
    等调用无需大规模修改即可迁移到新的 TaskStateStore。

    重要差异：
    - __getitem__/get 返回的是浅拷贝而非引用，调用方直接修改返回值不会影响缓存
    - 需要修改缓存内容时必须使用 update() 或重新 __setitem__

    Attributes:
        _store: 被包装的 TaskStateStore 实例。
    """

    def __init__(self, store):
        """初始化任务状态存储代理。

        Args:
            store: TaskStateStore 实例。
        """
        self._store = store

    def __getitem__(self, task_id: str) -> dict:
        """获取任务状态（浅拷贝）。不存在则抛出 KeyError。

        Args:
            task_id: 任务唯一标识。

        Returns:
            任务状态字典（浅拷贝）。

        Raises:
            KeyError: 任务不存在。
        """
        cached = self._store.get_cached(task_id)
        if cached is None:
            raise KeyError(task_id)
        return cached

    def __setitem__(self, task_id: str, value: dict) -> None:
        """设置任务状态（覆盖式写入缓存）。

        通过 update_cached 一次性写入所有字段，避免部分写入导致状态不一致。

        Args:
            task_id: 任务唯一标识。
            value: 完整任务状态字典。
        """
        self._store.update_cached(task_id, **value)

    def __contains__(self, task_id: str) -> bool:
        """检查任务是否存在于缓存中。

        Args:
            task_id: 任务唯一标识。

        Returns:
            存在返回 True，否则返回 False。
        """
        return self._store.get_cached(task_id) is not None

    def get(self, task_id: str, default: dict | None = None) -> dict | None:
        """获取任务状态（浅拷贝）。不存在则返回 default。

        Args:
            task_id: 任务唯一标识。
            default: 任务不存在时返回的默认值，默认 None。

        Returns:
            任务状态字典（浅拷贝）；不存在返回 default。
        """
        cached = self._store.get_cached(task_id)
        return cached if cached is not None else default

    def update(self, task_id: str, **kwargs) -> dict | None:
        """更新缓存中的任务字段（代理到 task_state_store.update_cached）。

        Args:
            task_id: 任务唯一标识。
            **kwargs: 要更新的字段键值对。

        Returns:
            更新后的完整任务状态字典；任务不存在返回 None。
        """
        return self._store.update_cached(task_id, **kwargs)
