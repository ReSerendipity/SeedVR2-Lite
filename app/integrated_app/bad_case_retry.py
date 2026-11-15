#!/usr/bin/env python3
"""SeedVR2 坏案例自动重试机制（Bad Case Retry）。

参考 TTS_MultiModel/app/integrated_app/bad_case_retry.py 的设计模式，
适配 SeedVR2 的 GPU OOM / 网络抖动 / 瞬态失败场景：

核心策略（按 attempt 递进）：
  1. OOM 第一次重试：增大 blocks_to_swap（更多 transformer 块换出到 CPU）
  2. OOM 第二次重试：缩小 VAE tile（size/overlap 同步减半，先于降分辨率）
  3. OOM 第三次重试：降低 resolution（×0.75）+ 进一步增大 blocks_to_swap
  4. OOM 第四次重试：精度降级（dit_model fp16 → fp8，若可用）+ 降低 resolution
  5. 网络抖动/瞬态错误：指数退避 + 重新随机种子，参数不变
  6. 重试耗尽：接受当前结果（优雅降级），记录 warning

设计要点：
  - 纯 Python 实现，不依赖 torch（可在任意环境测试）
  - 异步实现（与 SeedVR2 引擎 infer_image/infer_video 的 async 签名匹配）
  - 降级策略幂等：重复调用不会产生副作用
  - 参数调整通过 dataclasses.replace 创建新对象，不修改原始参数

参考来源：
  - TTS_MultiModel: bad_case_retry.py（RetryConfig/RetryState/RetryResult 模式）
  - SeedVR2: utils/retry.py（exponential_backoff_with_jitter）
  - SeedVR2: _memory_utils.py（OOM 检测与显存保护逻辑）
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 枚举
# ---------------------------------------------------------------------------


class FailureType(Enum):
    """失败类型枚举。"""

    OOM = "oom"  # GPU 显存不足
    RAM = "ram"  # 系统内存耗尽（内存守卫触发，不可重试，评估 P2-2）
    NETWORK = "network"  # 网络抖动 / 瞬态 IO 错误
    CANCELLED = "cancelled"  # 任务被取消（不重试）
    UNKNOWN = "unknown"  # 未知异常


class RetryStrategy(Enum):
    """重试策略枚举。"""

    BLOCKSWAP_INCREASE = "blockswap_increase"
    TILE_DECREASE = "tile_decrease"
    RESOLUTION_DECREASE = "resolution_decrease"
    PRECISION_FALLBACK = "precision_fallback"
    SEED_CHANGE = "seed_change"
    BACKOFF_ONLY = "backoff_only"


# OOM 相关错误关键词（不区分大小写）
_OOM_KEYWORDS = (
    "out of memory",
    "cuda oom",
    "cudnn out of memory",
    "no available memory",
    "memory_error",
    "memoryerror",
    "alloc",
    "hip out of memory",
    "runtimeerror: cuda",
    "显存不足",  # 引擎侧中文错误消息（result.error / MemoryError 消息）
)

# 系统内存守卫错误关键词（_memory_utils 的 MemoryError 文案；评估 P2-2）。
# 判定顺序必须先于 OOM：守卫异常类型为 MemoryError，其异常类名 "memoryerror"
# 命中 OOM 关键词，若后判会被误分类为可重试的 GPU OOM
_RAM_GUARD_KEYWORDS = (
    "内存使用率",
    "必须立即终止模型",
    "可用内存",
    "insufficientramerror",
)

# 网络/瞬态错误关键词
_NETWORK_KEYWORDS = (
    "connection",
    "timeout",
    "timed out",
    "socket",
    "network",
    "temporary",
    "retry",
    "unreachable",
    "broken pipe",
    "connection reset",
    "eof occurred",
)

# 取消相关关键词
_CANCELLED_KEYWORDS = (
    "cancelled",
    "canceled",
    "inferencecancellederror",
    "keyboardinterrupt",
)


# ---------------------------------------------------------------------------
# 配置与状态
# ---------------------------------------------------------------------------


@dataclass
class RetryConfig:
    """重试机制配置参数。

    Attributes:
        max_retries: 最大重试次数（不含首次），默认 3。
            阶梯为 blockswap↑ → tile↓ → resolution↓ → precision↓，
            3 次重试可覆盖到 resolution 档（precision 档需 4 次，按需调大）。
        base_delay_seconds: 基础退避延迟（秒），用于指数退避。
        max_delay_seconds: 最大退避延迟上限（秒）。
        jitter_ratio: 抖动比例（0-1），防止雪崩。
        enable_degradation: 是否启用参数降级（OOM 时调整 blocks_to_swap/tile/resolution/precision）。
        blocks_to_swap_step: 每次重试增加的 blocks_to_swap 数量。
        blocks_to_swap_max: blocks_to_swap 上限（防止超过模型层数）。
        enable_tile_decrease: 是否在第二次重试起缩小 VAE tile（先于降分辨率）。
        tile_scale_factor: 每次 tile 降档的缩放因子（0-1）。
        tile_size_min: tile size 下限（像素），与 GPU 分档最小值 256 对齐。
        tile_overlap_min: tile overlap 下限（像素）。
        resolution_scale_factor: OOM 时 resolution 缩放因子（0-1）。
        resolution_min: resolution 下限（像素）。
        enable_precision_fallback: 是否在最后一次重试时尝试 fp16→fp8 降级。
        enable_seed_rotation: 是否在每次重试时轮换随机种子。
    """

    max_retries: int = 3
    base_delay_seconds: float = 1.0
    max_delay_seconds: float = 30.0
    jitter_ratio: float = 0.1
    enable_degradation: bool = True
    blocks_to_swap_step: int = 4
    blocks_to_swap_max: int = 36
    enable_tile_decrease: bool = True
    tile_scale_factor: float = 0.5
    tile_size_min: int = 256
    tile_overlap_min: int = 32
    resolution_scale_factor: float = 0.75
    resolution_min: int = 256
    enable_precision_fallback: bool = True
    enable_seed_rotation: bool = True


@dataclass
class RetryState:
    """重试过程状态追踪。"""

    attempt: int = 0
    failure_type: FailureType = FailureType.UNKNOWN
    failure_reason: str = ""
    last_params: dict[str, Any] = field(default_factory=dict)
    adjustments: list[RetryStrategy] = field(default_factory=list)
    degraded: bool = False


@dataclass
class RetryResult:
    """重试执行结果。

    Attributes:
        success: 是否最终成功（含优雅降级）。
        result: 原始推理结果对象（如 RestoreResult），失败时为 None。
        attempts: 实际尝试次数。
        final_params: 最终使用的参数字典。
        failure_reason: 失败原因描述。
        degraded: 是否发生了参数降级。
    """

    success: bool
    result: Any = None
    attempts: int = 0
    final_params: dict[str, Any] = field(default_factory=dict)
    failure_reason: str = ""
    degraded: bool = False


# ---------------------------------------------------------------------------
# 纯函数：失败分类与参数调整
# ---------------------------------------------------------------------------


def classify_failure(
    error: BaseException | None = None,
    message: str = "",
) -> tuple[bool, FailureType, str]:
    """分类失败类型。

    根据 异常对象 或 错误消息字符串 判断失败类型：
    - RAM: 系统内存守卫触发（内存使用率超阈值/可用内存不足，不可重试）
    - OOM: GPU 显存不足（含 "out of memory", "cuda", "alloc" 等关键词）
    - NETWORK: 网络/IO 瞬态错误（含 "connection", "timeout", "socket" 等）
    - CANCELLED: 任务被取消（不重试）
    - UNKNOWN: 其他未知错误

    Args:
        error: 捕获的异常对象，可选。
        message: 错误消息字符串，可选（error 为 None 时使用）。

    Returns:
        (has_failure, failure_type, reason) 三元组。
        has_failure 为 False 表示无需重试（如 CANCELLED）。
    """
    if error is not None:
        if isinstance(error, (asyncio.CancelledError, KeyboardInterrupt)):
            return False, FailureType.CANCELLED, str(error)
        msg = f"{type(error).__name__}: {error}".lower()
    else:
        msg = (message or "").lower()

    if not msg:
        return True, FailureType.UNKNOWN, "未知错误（空消息）"

    # 取消
    if any(kw in msg for kw in _CANCELLED_KEYWORDS):
        return False, FailureType.CANCELLED, msg

    # 系统内存守卫（先于 OOM：守卫异常类名 MemoryError 会命中 OOM 关键词）
    if any(kw in msg for kw in _RAM_GUARD_KEYWORDS):
        return True, FailureType.RAM, msg

    # OOM
    if any(kw in msg for kw in _OOM_KEYWORDS):
        return True, FailureType.OOM, msg

    # 网络/瞬态
    if any(kw in msg for kw in _NETWORK_KEYWORDS):
        return True, FailureType.NETWORK, msg

    return True, FailureType.UNKNOWN, msg


def _get_nested_config(params: dict[str, Any]) -> Any:
    """从参数字典中提取嵌套的推理配置对象（dataclass）。"""
    cfg = params.get("config")
    if cfg is not None and dataclasses.is_dataclass(cfg):
        return cfg
    return None


def _replace_nested_config(params: dict[str, Any], **field_overrides) -> dict[str, Any]:
    """用 dataclasses.replace 创建新的嵌套 config，返回新的 params 字典。"""
    new_params = dict(params)
    cfg = _get_nested_config(new_params)
    if cfg is not None:
        valid_fields = {f.name for f in dataclasses.fields(cfg)}
        filtered = {k: v for k, v in field_overrides.items() if k in valid_fields and v is not None}
        if filtered:
            new_params["config"] = dataclasses.replace(cfg, **filtered)
    else:
        # 没有嵌套 dataclass：直接在顶层字典写入
        for k, v in field_overrides.items():
            if v is not None:
                new_params[k] = v
    return new_params


def adjust_params_for_retry(
    params: dict[str, Any],
    failure_type: FailureType,
    attempt: int,
    config: RetryConfig | None = None,
) -> dict[str, Any]:
    """根据失败类型和重试次数调整推理参数。

    降级策略（仅 OOM 时启用，其他类型仅退避+换种子）：
      - attempt 1: blocks_to_swap += step
      - attempt 2: blocks_to_swap += step + VAE tile size/overlap ↓（先于降分辨率）
      - attempt 3: 上述 + resolution × factor
      - attempt 4+: 上述 + precision fp16→fp8

    Args:
        params: 当前推理参数字典。
        failure_type: 检测到的失败类型。
        attempt: 当前重试次数（1-based）。
        config: 重试配置。

    Returns:
        调整后的新参数字典（不修改原始）。
    """
    cfg = config or RetryConfig()
    new_params = dict(params)
    strategies: list[RetryStrategy] = []

    if not cfg.enable_degradation or failure_type != FailureType.OOM:
        # 非降级场景：仅换种子
        if cfg.enable_seed_rotation:
            new_params = _rotate_seed(new_params)
            strategies.append(RetryStrategy.SEED_CHANGE)
        _log_adjustment(attempt, new_params, strategies)
        return new_params

    # OOM 降级
    # 1. 增大 blocks_to_swap
    current_bts = _get_param(new_params, "blocks_to_swap", default=0)
    new_bts = min(current_bts + cfg.blocks_to_swap_step, cfg.blocks_to_swap_max)
    if new_bts != current_bts:
        new_params = _replace_nested_config(new_params, blocks_to_swap=new_bts)
        strategies.append(RetryStrategy.BLOCKSWAP_INCREASE)

    # 2. 第二次及以后：缩小 VAE tile（size 与 overlap 同步缩，先于降分辨率）
    #    必须先缩 tile 再降分辨率：tile 减半只增加分块数与少量耗时，不损失
    #    输出清晰度；分辨率降档会真实降低成品质量。overlap 同步压到
    #    new_tile//4（下限 tile_overlap_min），否则 _vae_pipeline 的
    #    auto_tile_size 会因 overlap >= tile//2 判定 bad_overlap 回填推荐值，
    #    把手工缩 tile 抵消掉。
    if attempt >= 2 and cfg.enable_tile_decrease:
        tile_changed = False
        for size_key, overlap_key in (
            ("encode_tile_size", "encode_tile_overlap"),
            ("decode_tile_size", "decode_tile_overlap"),
        ):
            current_tile = _get_param(new_params, size_key, default=0)
            if not isinstance(current_tile, (int, float)) or current_tile <= 0:
                continue
            new_tile = max(int(current_tile * cfg.tile_scale_factor), cfg.tile_size_min)
            if new_tile >= current_tile:
                continue
            current_overlap = _get_param(new_params, overlap_key, default=0) or 0
            new_overlap = (
                max(cfg.tile_overlap_min, min(int(current_overlap), new_tile // 4)) if current_overlap > 0 else 0
            )
            updated = _replace_nested_config(new_params, **{size_key: new_tile, overlap_key: new_overlap})
            if _get_param(updated, size_key, default=0) == new_tile:
                new_params = updated
                tile_changed = True
        if tile_changed:
            strategies.append(RetryStrategy.TILE_DECREASE)

    # 3. 第三次及以后：降低 resolution（tile 已缩过仍 OOM 才牺牲清晰度）
    if attempt >= 3:
        current_res = _get_param(new_params, "resolution", default=2160)
        new_res = max(int(current_res * cfg.resolution_scale_factor), cfg.resolution_min)
        if new_res < current_res:
            new_params = _replace_nested_config(new_params, resolution=new_res)
            strategies.append(RetryStrategy.RESOLUTION_DECREASE)

    # 4. 第四次及以后：精度降级
    #    ⚠️ 2026-09-10 修正（P1-5）：历史实现无条件按
    #    fp16 → fp8 → mxfp8 → int8_convrot → nvfp4 一路往下走，
    #    但 mxfp8/int8_convrot/nvfp4 都在**加载期**反量化为 bf16（见 quant_dequant.py），
    #    驻留显存与 fp16 同档 —— fp16→nvfp4 一点显存都不省，只白白降质。
    #    这里只跳到「驻留档位确实更小」的精度（当前实际上只有 fp8）。
    if attempt >= 4 and cfg.enable_precision_fallback:
        dit_model = _get_param(new_params, "dit_model", default="")
        if dit_model:
            from app.integrated_app.gpu_utils import precision_saves_vram
            from app.integrated_app.spec import model_size_from_dit_model, precision_from_dit_model

            size = model_size_from_dit_model(dit_model)
            prec = precision_from_dit_model(dit_model)
            fallback_chain = ["fp16", "fp8", "mxfp8", "int8_convrot", "nvfp4"]
            if prec and prec in fallback_chain:
                idx = fallback_chain.index(prec)
                next_prec = None
                for candidate in fallback_chain[idx + 1 :]:
                    if precision_saves_vram(prec, candidate):
                        next_prec = candidate
                        break
                if next_prec:
                    new_dit = f"{size}_{next_prec}"
                    new_params = _replace_nested_config(new_params, dit_model=new_dit)
                    strategies.append(RetryStrategy.PRECISION_FALLBACK)
                else:
                    logger.info(
                        f"OOM 降级跳过精度降级: {prec} 之后的候选精度均为加载期反量化格式，"
                        "驻留显存与 fp16 相同，降级不省显存只会降质"
                    )

    # 始终换种子
    if cfg.enable_seed_rotation:
        new_params = _rotate_seed(new_params)
        strategies.append(RetryStrategy.SEED_CHANGE)

    _log_adjustment(attempt, new_params, strategies)
    return new_params


def _get_param(params: dict[str, Any], key: str, default: Any = None) -> Any:
    """从参数字典获取值，优先嵌套 config dataclass，回退顶层。"""
    cfg = _get_nested_config(params)
    if cfg is not None and hasattr(cfg, key):
        return getattr(cfg, key)
    return params.get(key, default)


def _rotate_seed(params: dict[str, Any]) -> dict[str, Any]:
    """轮换随机种子。"""
    current_seed = _get_param(params, "seed", default=-1)
    if current_seed == -1 or current_seed is None:
        new_seed = random.randint(0, 0x7FFFFFFF)  # nosec B311
    else:
        new_seed = (int(current_seed) * 1103515245 + 12345 + 7919) & 0x7FFFFFFF
    return _replace_nested_config(params, seed=new_seed)


def _log_adjustment(attempt: int, params: dict[str, Any], strategies: list[RetryStrategy]) -> None:
    """记录参数调整日志。"""
    bts = _get_param(params, "blocks_to_swap", "N/A")
    res = _get_param(params, "resolution", "N/A")
    seed = _get_param(params, "seed", "N/A")
    dit = _get_param(params, "dit_model", "N/A")
    enc_tile = _get_param(params, "encode_tile_size", "N/A")
    dec_tile = _get_param(params, "decode_tile_size", "N/A")
    logger.info(
        "[BadCaseRetry] 第 %d 次重试参数调整: blocks_to_swap=%s, resolution=%s, "
        "encode_tile=%s, decode_tile=%s, dit_model=%s, seed=%s, 策略=%s",
        attempt,
        bts,
        res,
        enc_tile,
        dec_tile,
        dit,
        seed,
        [s.value for s in strategies],
    )


# ---------------------------------------------------------------------------
# 异步重试主函数
# ---------------------------------------------------------------------------


async def retry_with_bad_case_detection(
    generate_fn: Callable[..., Awaitable[Any]],
    params: dict[str, Any],
    config: RetryConfig | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> RetryResult:
    """带坏案例检测的异步重试生成函数。

    Args:
        generate_fn: 异步生成函数，接受 **params 并返回结果对象（需有 .success/.error 属性）。
        params: 生成参数字典。
        config: 重试配置。
        progress_callback: 进度回调 (attempt, max_attempts, reason) -> None。

    Returns:
        RetryResult 结果对象。
    """
    cfg = config or RetryConfig()
    state = RetryState()
    current_params = dict(params)

    for attempt in range(cfg.max_retries + 1):
        state.attempt = attempt + 1

        # 非首次尝试时退避
        if attempt > 0:
            delay = min(cfg.base_delay_seconds * (2 ** (attempt - 1)), cfg.max_delay_seconds)
            if cfg.jitter_ratio > 0:
                delay += random.uniform(0, cfg.jitter_ratio * delay)  # nosec B311
            await asyncio.sleep(delay)

            # 调整参数
            current_params = adjust_params_for_retry(current_params, state.failure_type, attempt, cfg)
            state.last_params = dict(current_params)
            state.degraded = state.degraded or state.failure_type == FailureType.OOM

            if progress_callback:
                progress_callback(attempt, cfg.max_retries, state.failure_reason)

        try:
            result = await generate_fn(**current_params)

            # 检查结果是否成功
            success = getattr(result, "success", None)
            if success is None:
                # 非标准结果对象，视为成功
                success = True

            if success:
                logger.info("[BadCaseRetry] 生成成功 (尝试 %d/%d)", attempt + 1, cfg.max_retries + 1)
                return RetryResult(
                    success=True,
                    result=result,
                    attempts=attempt + 1,
                    final_params=current_params,
                    degraded=state.degraded,
                )

            # 结果失败：检查 error 字段
            error_msg = getattr(result, "error", "") or "推理返回失败"
            has_failure, ftype, reason = classify_failure(message=error_msg)

            if not has_failure or ftype in (FailureType.CANCELLED, FailureType.RAM):
                # 取消 / 系统内存耗尽：不重试（RAM 重试只会重复白烧加载时间，
                # 且 OOM 降级阶梯的 blockswap 会进一步加剧系统内存压力，评估 P2-2）
                return RetryResult(
                    success=False,
                    result=result,
                    attempts=attempt + 1,
                    final_params=current_params,
                    failure_reason=(
                        f"任务被取消: {reason}" if ftype == FailureType.CANCELLED else f"系统内存耗尽，不重试: {reason}"
                    ),
                    degraded=state.degraded,
                )

            state.failure_type = ftype
            state.failure_reason = reason
            logger.warning("[BadCaseRetry] 尝试 %d/%d 结果失败: %s", attempt + 1, cfg.max_retries + 1, reason)

            if attempt == cfg.max_retries:
                # 重试耗尽：优雅降级，返回当前结果
                logger.warning("[BadCaseRetry] 重试耗尽 (%d 次)，接受当前结果", cfg.max_retries)
                return RetryResult(
                    success=True,  # 优雅降级
                    result=result,
                    attempts=attempt + 1,
                    final_params=current_params,
                    failure_reason=f"重试耗尽，接受低质量输出: {reason}",
                    degraded=state.degraded,
                )

        except asyncio.CancelledError:
            raise
        except Exception as e:
            has_failure, ftype, reason = classify_failure(error=e)
            state.failure_type = ftype
            state.failure_reason = reason

            if not has_failure or ftype in (FailureType.CANCELLED, FailureType.RAM):
                # 取消向上传播；系统内存耗尽直接返回失败（不重试，评估 P2-2）
                if ftype != FailureType.RAM:
                    raise
                return RetryResult(
                    success=False,
                    attempts=attempt + 1,
                    final_params=current_params,
                    failure_reason=f"系统内存耗尽，不重试: {reason}",
                    degraded=state.degraded,
                )

            logger.warning("[BadCaseRetry] 尝试 %d/%d 异常: %s", attempt + 1, cfg.max_retries + 1, reason)

            if attempt == cfg.max_retries:
                logger.error("[BadCaseRetry] 重试耗尽且所有尝试均异常")
                return RetryResult(
                    success=False,
                    attempts=attempt + 1,
                    final_params=current_params,
                    failure_reason=f"所有重试均失败: {reason}",
                    degraded=state.degraded,
                )

    # 理论上不会到达
    return RetryResult(success=False, attempts=state.attempt, failure_reason="未知错误")


__all__ = [
    "FailureType",
    "RetryStrategy",
    "RetryConfig",
    "RetryState",
    "RetryResult",
    "classify_failure",
    "adjust_params_for_retry",
    "retry_with_bad_case_detection",
]
