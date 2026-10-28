"""VAE encode/decode pipeline mixin for SeedVR2Engine.

Extracted from seedvr2_engine.py as part of structural refactoring
(phase 2A). Contains VAE tiled encode and decode methods.
"""

import contextlib
import logging
import sys
import threading
import time
import traceback

import torch
from einops import rearrange

from app.integrated_app.engines._memory_utils import (
    DEFAULT_SCALING_FACTOR,
    _force_release_memory,
    get_free_vram_gb,
)
from app.integrated_app.gpu_utils import empty_gpu_cache

logger = logging.getLogger(__name__)

VAE_STALL_WARN_SECONDS = 20.0
"""VAE 解码超过该秒数即视为疑似卡死，看门狗开始打栈（不打断内核）"""

VAE_STALL_REPEAT_SECONDS = 20.0
"""疑似卡死后重复的告警间隔（秒）"""


class _TileProgressLogger:
    """给 tiled 编解码加每 tile 进度日志（不修改 model_lib 源码）。

    ``tiled_decode`` / ``tiled_encode`` 内部每个 tile 会调用一次
    ``self.slicing_decode`` / ``self.slicing_encode``。这里在**实例**上临时替换
    这两个方法做计数与计时，退出时删除实例属性还原类方法。
    ``model_lib/`` 是禁改目录，因此采用实例级钩子而不是直接改源码。
    """

    def __init__(self, vae, stage: str = "decode", total: int | None = None):
        self.vae = vae
        self.stage = stage
        self.total = total
        self.count = 0
        self.elapsed = 0.0
        self._patched: dict[str, object] = {}

    def __enter__(self) -> "_TileProgressLogger":
        for name in ("slicing_decode", "slicing_encode"):
            original = getattr(self.vae, name, None)
            if original is None or not callable(original):
                continue
            self._patched[name] = original
            setattr(self.vae, name, self._make_wrapper(name, original))
        self._t0 = time.monotonic()
        return self

    def __exit__(self, *exc_info) -> bool:
        self.elapsed = time.monotonic() - self._t0
        for name in self._patched:
            with contextlib.suppress(Exception):
                if name in getattr(self.vae, "__dict__", {}):
                    delattr(self.vae, name)
        self._patched.clear()
        if self.count:
            logger.info(
                f"[VAE tiled {self.stage}] 共 {self.count} 个 tile，"
                f"总耗时 {self.elapsed:.2f}s（均值 {self.elapsed / self.count:.2f}s/tile）"
            )
        return False

    def _make_wrapper(self, name: str, original):
        def wrapped(*args, **kwargs):
            tile_index = self.count + 1
            tile_t0 = time.monotonic()
            try:
                return original(*args, **kwargs)
            finally:
                cost = time.monotonic() - tile_t0
                self.count = tile_index
                total_hint = f"/{self.total}" if self.total else ""
                logger.info(
                    f"[VAE tiled {self.stage}] tile {tile_index}{total_hint} 用时 {cost:.2f}s，"
                    f"空闲显存 {get_free_vram_gb():.2f}GB"
                )

        return wrapped


class _VaeStallWatchdog:
    """VAE 解码看门狗：疑似卡死时把主线程栈与显存状态打到日志。

    显存超卖时 Windows WDDM 会把显存分页到系统内存，内核耗时膨胀 2~3 个数量级，
    表现为「无日志、无报错、永久卡住」，且 torch 不会抛 OOM。
    看门狗**不打断** CUDA 内核（从 Python 侧打断不安全），只负责让卡死可见、可定位。
    """

    def __init__(
        self,
        stage: str,
        warn_after: float = VAE_STALL_WARN_SECONDS,
        repeat: float = VAE_STALL_REPEAT_SECONDS,
    ):
        self.stage = stage
        self.warn_after = warn_after
        self.repeat = repeat
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._start = 0.0

    def __enter__(self) -> "_VaeStallWatchdog":
        self._start = time.monotonic()
        self._thread = threading.Thread(target=self._run, name="vae-stall-watchdog", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc_info) -> bool:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        logger.info(f"[VAE 看门狗] {self.stage} 实际耗时 {time.monotonic() - self._start:.1f}s")
        return False

    def _run(self) -> None:
        next_warn = self.warn_after
        while not self._stop.wait(0.5):
            if time.monotonic() - self._start < next_warn:
                continue
            with contextlib.suppress(Exception):
                self._dump()
            next_warn += self.repeat

    def _dump(self) -> None:
        elapsed = time.monotonic() - self._start
        free_gb = get_free_vram_gb()
        logger.warning(
            f"[VAE 看门狗] {self.stage} 已运行 {elapsed:.0f}s（阈值 {self.warn_after:.0f}s），"
            f"空闲显存 {free_gb:.2f}GB。这不是正常的计算耗时，绝大多数是"
            "显存超卖触发的 WDDM 分页（显存被换出到系统内存，内核慢 2~3 个数量级）。"
            "处理建议：调小 decode_tile_size / 开启 dit 卸载 / 降低输出分辨率。当前栈："
        )
        for _tid, frame in sys._current_frames().items():
            stack = "".join(traceback.format_stack(frame))
            if "vae" in stack or "attn_video_vae" in stack:
                logger.warning(f"[VAE 看门狗] 卡住位置:\n{stack}")
                break


class _VAEPipelineMixin:
    """Mixin: pipeline methods extracted from SeedVR2Engine."""

    @torch.no_grad()
    def _vae_encode(self, samples: list[torch.Tensor]) -> list[torch.Tensor]:
        """VAE 编码: 像素空间 -> 潜空间，支持 tiled 编码

        与 ComfyUI/test_e2e.py 一致: 使用 vae.encode(x, tiled=True, tile_size=..., tile_overlap=...)
        集成 SCST 启发的自动 tile size 推荐和 NaN 检测回退。
        """
        from app.integrated_app.optimization.inference.vae_tiled_enhance import (
            detect_nan,
            get_optimal_tile_size,
        )

        vae_cfg = self._model_config["vae"]
        use_sample = vae_cfg.get("use_sample", True)
        scale = vae_cfg.get("scaling_factor", DEFAULT_SCALING_FACTOR)
        shift = vae_cfg.get("shifting_factor", 0.0)
        dtype = getattr(torch, vae_cfg.get("dtype", "bfloat16"))

        # tiled VAE 配置 (默认值对齐 ComfyUI HD 工作流: encode_tiled=True, tile_overlap=128)
        tiled_cfg = getattr(self, "_vae_tiled_config", {})
        encode_tiled = tiled_cfg.get("encode_tiled", True)
        tile_size = tiled_cfg.get("encode_tile_size", 1024)
        tile_overlap = tiled_cfg.get("encode_tile_overlap", 128)
        auto_tile_size = tiled_cfg.get("auto_tile_size", True)

        # 自动 tile size 推荐 (SCST inspired)
        if auto_tile_size and encode_tiled:
            try:
                # 根据输入尺寸和 GPU 显存计算最优 tile size
                if samples and len(samples) > 0:
                    sample = samples[0]
                    if sample.ndim >= 3:
                        h, w = sample.shape[-2], sample.shape[-1]
                        recommended_ts, recommended_overlap = get_optimal_tile_size(
                            h, w, is_decoder=False, device=self.device
                        )
                        # 如果配置的 tile_size 太大，或 overlap 配置不合理（>=50% tile_size），使用推荐值
                        bad_overlap = tile_size > 0 and tile_overlap >= tile_size // 2
                        if tile_size <= 0 or tile_size > recommended_ts * 1.5 or bad_overlap:
                            logger.info(
                                f"VAE 编码自动 tile size: 原配置({tile_size}/{tile_overlap}) "
                                f"-> 推荐({recommended_ts}/{recommended_overlap})"
                                f"{' (overlap 过大)' if bad_overlap else ''}"
                            )
                            tile_size = recommended_ts
                            tile_overlap = recommended_overlap
            except Exception as e:
                logger.debug(f"自动 tile size 推荐失败: {e}")

        if isinstance(scale, list):
            scale = torch.tensor(scale, device=self.device, dtype=dtype)
        if isinstance(shift, list):
            shift = torch.tensor(shift, device=self.device, dtype=dtype)

        latents = []
        oom_fallback_used = False
        for sample in samples:
            # sample: C T H W -> B C T H W
            batch = sample.unsqueeze(0).to(self.device, dtype)
            if hasattr(self.vae, "preprocess"):
                batch = self.vae.preprocess(batch)

            if encode_tiled:
                logger.info(f"VAE tiled 编码: tile_size={tile_size}, overlap={tile_overlap}")
                try:
                    enc_result = self.vae.encode(
                        batch,
                        tiled=True,
                        tile_size=(tile_size, tile_size),
                        tile_overlap=(tile_overlap, tile_overlap),
                    )
                except RuntimeError as e:
                    if "out of memory" in str(e).lower() and not oom_fallback_used:
                        logger.warning("VAE 编码 OOM，尝试更小的 tile size")
                        empty_gpu_cache()
                        tile_size = max(tile_size // 2, 256)
                        tile_overlap = max(tile_overlap // 2, 32)
                        enc_result = self.vae.encode(
                            batch,
                            tiled=True,
                            tile_size=(tile_size, tile_size),
                            tile_overlap=(tile_overlap, tile_overlap),
                        )
                        oom_fallback_used = True
                    else:
                        raise
            else:
                enc_result = self.vae.encode(batch)

            # 提取 latent
            if use_sample:
                latent = enc_result.latent
            else:
                latent = enc_result.posterior.mode().squeeze(2)

            latent = latent.unsqueeze(2) if latent.ndim == 4 else latent

            # NaN 检测
            if encode_tiled and detect_nan(latent, "vae_encode_latent"):
                logger.warning("VAE 编码检测到 NaN，回退到非 tiled 编码")
                empty_gpu_cache()
                enc_result = self.vae.encode(batch)
                if use_sample:
                    latent = enc_result.latent
                else:
                    latent = enc_result.posterior.mode().squeeze(2)
                latent = latent.unsqueeze(2) if latent.ndim == 4 else latent

            # channels-first -> channels-last + 缩放
            latent = rearrange(latent, "b c ... -> b ... c")
            latent = (latent - shift) * scale
            latents.append(latent.squeeze(0))  # 去掉 batch 维度

        return latents

    @torch.no_grad()
    def _vae_decode(self, latents: list[torch.Tensor]) -> list[torch.Tensor]:
        """VAE 解码: 潜空间 -> 像素空间，支持 tiled 解码

        与 ComfyUI/test_e2e.py 一致: 使用 vae.decode(x, tiled=True, tile_size=..., tile_overlap=...)
        集成 SCST 启发的自动 tile size 推荐、OOM 回退和 NaN 检测。
        """
        from app.integrated_app.optimization.inference.vae_tiled_enhance import (
            detect_nan,
            get_optimal_tile_size,
        )

        vae_cfg = self._model_config["vae"]
        scale = vae_cfg.get("scaling_factor", DEFAULT_SCALING_FACTOR)
        shift = vae_cfg.get("shifting_factor", 0.0)
        dtype = getattr(torch, vae_cfg.get("dtype", "bfloat16"))

        # tiled VAE 配置 (默认值对齐 ComfyUI 工作流: decode_tiled=True, decode_tile_size=1024)
        tiled_cfg = getattr(self, "_vae_tiled_config", {})
        decode_tiled = tiled_cfg.get("decode_tiled", True)
        tile_size = tiled_cfg.get("decode_tile_size", 1024)
        tile_overlap = tiled_cfg.get("decode_tile_overlap", 128)
        auto_tile_size = tiled_cfg.get("auto_tile_size", True)

        # 2026-09-10 清理：此前这里会安装 GroupNormAccumulator / TiledVAEHook 并把
        # gaussian_blend / groupnorm_accum 打进日志，但两者都是空转：
        #   - GroupNormAccumulator.accumulate_from_tile() 从未被 VAE 调用，
        #     apply_accumulated_stats() 因此是空操作；
        #   - TiledVAEHook 找的 vae._internal_tile_state 在 VAE 上不存在，
        #     _last_tile_outputs 恒为 None，Gaussian 混合分支永不执行。
        # tile 接缝由 VAE 自带的 raised-cosine 斜坡融合负责：见
        # model_lib/video_vae_v3/modules/attn_video_vae.py 的 tiled_decode
        # （ramp_cache = 0.5 - 0.5*cos(t*pi)，按 count 归一化叠加）。
        # 2026-10-28 复核：该融合确实生效（下方 decode 调用传 tiled=True 且
        # tile_overlap>0 时即走此路径），因此不存在"关掉混合"的开关，也不该
        # 再叠一层 Gaussian —— 那只会替换掉已验证的融合并加大上游同步成本。
        # 详见 docs/adr/0006。

        if isinstance(scale, list):
            scale = torch.tensor(scale, device=self.device, dtype=dtype)
        if isinstance(shift, list):
            shift = torch.tensor(shift, device=self.device, dtype=dtype)

        samples = []
        oom_fallback_used = False
        nan_fallback_used = False
        try:
            for latent in latents:
                # latent: ... C -> B ... C
                batch = latent.unsqueeze(0).to(self.device, dtype)
                batch = batch / scale + shift
                batch = rearrange(batch, "b ... c -> b c ...")
                batch = batch.squeeze(2)

                # 自动 tile size 推荐 (SCST inspired)
                # 重要: vae.decode 的 tile_size 参数为像素空间单位！VAE 内部自动 // 8 转换为潜空间
                current_tile_size = tile_size  # 像素空间
                current_tile_overlap = tile_overlap  # 像素空间
                if auto_tile_size and decode_tiled:
                    try:
                        if batch.ndim >= 4:
                            h_latent, w_latent = batch.shape[-2], batch.shape[-1]
                            # latent 空间尺寸 * 8 = 输出像素空间尺寸
                            h_pixel = h_latent * 8
                            w_pixel = w_latent * 8
                            # get_optimal_tile_size 直接返回像素空间推荐值
                            recommended_ts, recommended_overlap = get_optimal_tile_size(
                                h_pixel, w_pixel, is_decoder=True, device=self.device
                            )
                            # 如果配置的 tile_size 太大，或 overlap 配置不合理（>=50% tile_size），使用推荐值
                            bad_overlap = current_tile_size > 0 and current_tile_overlap >= current_tile_size // 2
                            if current_tile_size <= 0 or current_tile_size > recommended_ts * 1.5 or bad_overlap:
                                logger.info(
                                    f"VAE 解码自动 tile size (像素): 原配置({current_tile_size}/{current_tile_overlap})"
                                    f" -> 推荐({recommended_ts}/{recommended_overlap})"
                                    f"{' (overlap 过大)' if bad_overlap else ''}"
                                )
                                current_tile_size = recommended_ts
                                current_tile_overlap = recommended_overlap
                    except Exception as e:
                        logger.debug(f"自动 tile size 推荐失败: {e}")

                if decode_tiled:
                    logger.info(
                        f"VAE tiled 解码: tile_size={current_tile_size}, "
                        f"overlap={current_tile_overlap}, 空闲显存={get_free_vram_gb():.2f}GB"
                    )
                    try:
                        with _TileProgressLogger(self.vae, "decode"), _VaeStallWatchdog("VAE tiled 解码"):
                            dec_result = self.vae.decode(
                                batch,
                                tiled=True,
                                tile_size=(current_tile_size, current_tile_size),
                                tile_overlap=(current_tile_overlap, current_tile_overlap),
                            )
                    except RuntimeError as e:
                        if "out of memory" in str(e).lower() and not oom_fallback_used:
                            logger.warning("VAE 解码 OOM，尝试更小的 tile size")
                            empty_gpu_cache()
                            _force_release_memory()
                            # OOM 回退: 像素空间 tile size 减半，最小 256
                            current_tile_size = max(current_tile_size // 2, 256)
                            current_tile_overlap = max(current_tile_overlap // 2, 32)
                            dec_result = self.vae.decode(
                                batch,
                                tiled=True,
                                tile_size=(current_tile_size, current_tile_size),
                                tile_overlap=(current_tile_overlap, current_tile_overlap),
                            )
                            oom_fallback_used = True
                        elif "out of memory" in str(e).lower():
                            # 第二次 OOM，完全禁用 tiled
                            logger.warning("VAE 解码再次 OOM，回退到非 tiled 解码")
                            empty_gpu_cache()
                            _force_release_memory()
                            dec_result = self.vae.decode(batch)
                        else:
                            raise

                    sample = dec_result.sample

                    # NaN 检测
                    if detect_nan(sample, "vae_decode_sample") and not nan_fallback_used:
                        logger.warning("VAE 解码检测到 NaN，回退到非 tiled 解码")
                        empty_gpu_cache()
                        _force_release_memory()
                        dec_result = self.vae.decode(batch)
                        sample = dec_result.sample
                        nan_fallback_used = True
                else:
                    with _VaeStallWatchdog("VAE 非 tiled 解码"):
                        dec_result = self.vae.decode(batch)
                    sample = dec_result.sample

                if hasattr(self.vae, "postprocess"):
                    sample = self.vae.postprocess(sample)

                # 输出 NaN 最终检测
                if detect_nan(sample, "vae_decode_final"):
                    logger.error("VAE 解码最终输出仍包含 NaN，使用零填充")
                    sample = torch.nan_to_num(sample, nan=0.0, posinf=1.0, neginf=-1.0)

                samples.append(sample.squeeze(0))
        finally:
            # 2026-09-10: 原先在此卸载 TiledVAEHook 并应用 GroupNorm 累积统计，
            # 两者经查证均为空转（详见上方注释）已移除；保留 finally 用于收尾计数，
            # 异常路径下也能看到已完成的 latent 数。
            logger.info(f"[VAE 解码] 完成 {len(samples)}/{len(latents)} 个 latent")

        return samples

    # ------------------------------------------------------------------
    # 内部方法 - DiT 采样
    # ------------------------------------------------------------------
