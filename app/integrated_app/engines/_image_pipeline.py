"""Image inference pipeline mixin for SeedVR2Engine.

Extracted from seedvr2_engine.py as part of structural refactoring
(phase 2A). Contains image inference and post-processing methods.
"""

import asyncio
import gc
import logging
import os
import random
import time
from pathlib import Path

import numpy as np
import torch
from einops import rearrange
from PIL import Image as PILImage

from app.integrated_app.color_fix import apply_color_correction
from app.integrated_app.engine_interface import RestoreResult
from app.integrated_app.engines._memory_utils import (
    MAX_SEED,
    ImageInferenceConfig,
    _check_memory,
    _cleanup_cuda_cache,
    _force_release_memory,
    _log_memory,
    _tensor_to_uint8_np,
    build_dit_load_signature,
    get_free_vram_gb,
    offload_model_to_cpu,
    restore_model_to_gpu,
)
from app.integrated_app.exceptions import InferenceCancelledError
from app.integrated_app.gpu_utils import oom_protect
from app.integrated_app.utils.output_names import build_output_name

logger = logging.getLogger(__name__)

DIT_KEEP_RESIDENT_FREE_GB = 10.0
"""阶段3 前是否保留 DiT 常驻显存的空闲显存阈值 (GB)。

低于该值时把 DiT 卸载到 CPU（保留实例），为 VAE 解码让出显存。
10GB 的取值依据：3B DiT 的 bf16 常驻约 6.3GB，若此时空闲仍 ≥10GB，
说明这是一张 ≥20GB 的卡，解码峰值（tile 1024 实测 5.45GB）不会触碰上限，
卸载反而白白多付两次 CPU↔GPU 拷贝。
"""


def _resolve_unique_path(output_dir: str, filename: str) -> str:
    """在 output_dir 下生成不冲突的输出路径，重名时尾部追加 _1/_2 序号。

    Args:
        output_dir: 输出目录。
        filename: 期望的文件名。

    Returns:
        不冲突的完整输出路径。
    """
    candidate = os.path.join(output_dir, filename)
    if not os.path.exists(candidate):
        return candidate
    stem, ext = os.path.splitext(filename)
    idx = 1
    while True:
        candidate = os.path.join(output_dir, f"{stem}_{idx}{ext}")
        if not os.path.exists(candidate):
            return candidate
        idx += 1


class _ImagePipelineMixin:
    """Mixin: pipeline methods extracted from SeedVR2Engine."""

    @oom_protect
    async def infer_image(
        self,
        image_path: str,
        output_dir: str,
        config: ImageInferenceConfig | None = None,
        output_name: str | None = None,
        watermark_payload: str | None = None,
        **kwargs,
    ) -> RestoreResult:
        """图像修复推理 - 在线程中运行以避免阻塞事件循环

        阶段1: 加载VAE → 编码 → 销毁VAE
        阶段2: 加载DiT → 采样 → 销毁DiT
        阶段3: 加载VAE → 解码 → 销毁VAE
        阶段4: 后处理 (无模型)

        任何时刻 RAM 中最多只有一个大模型，内存超过 90% 立即终止

        Args:
            image_path: 输入图像路径
            output_dir: 输出目录
            config: 图像推理配置 (为 None 时从 self.config 构建默认配置)
            output_name: 输出文件名 (含扩展名)；为 None 时使用默认「日期_时分秒_模型」命名
            **kwargs: 额外参数，会覆盖 config 中的同名字段 (兼容旧调用方)
        """
        if config is None:
            config = ImageInferenceConfig.from_config_dict(self.config, **kwargs)
        elif kwargs:
            # kwargs 覆盖 config 字段
            for k, v in kwargs.items():
                if hasattr(config, k):
                    object.__setattr__(config, k, v)

        # REFACTOR [E4-1]: 每次推理开始前重置取消令牌
        self._reset_cancel_token()
        # P2-1: 图像路径启用 VRAM 峰值监控（原实现仅视频路径有），峰值随结果落库
        try:
            from app.integrated_app.optimization.gpu.vram_monitor import VRAMPeakMonitor

            self._vram_monitor = VRAMPeakMonitor(device=self.device, enabled=True)
        except Exception:
            self._vram_monitor = None
        return await asyncio.to_thread(
            self._infer_image_impl,
            image_path,
            output_dir,
            config,
            output_name=output_name,
            watermark_payload=watermark_payload,
        )

    def _vram_stage(self, name: str):
        """获取 VRAM 阶段监控上下文（监控不可用时返回空上下文，P2-1）。

        Args:
            name: 阶段名（如 "vae_encode"/"dit_sample"/"vae_decode"）。

        Returns:
            上下文管理器：进入时记录阶段起点快照，退出时统计峰值。
        """
        import contextlib

        monitor = getattr(self, "_vram_monitor", None)
        if monitor is None:
            return contextlib.nullcontext()
        return monitor.stage(name)

    def _prepare_image_input(self, image_path: str, resolution: int, max_resolution: int = 0) -> tuple:
        """读取图像并预处理为模型输入

        分辨率语义与 ComfyUI `SideResize` 对齐:
        - `resolution` 为短边目标像素 (ComfyUI 的 `resolution` 参数)
        - `max_resolution` 为长边上限 (ComfyUI 的 `max_resolution` 参数, 0 表示不限制)
        - 输入短边 < resolution 时放大到 resolution; 长边超过 max_resolution 时再回缩

        Args:
            image_path: 输入图像路径
            resolution: 目标分辨率 (短边像素)
            max_resolution: 长边像素上限, 0 表示不限制

        Returns:
            (cond_latent, input_video, res_h, res_w, scale_factor)
        """

        orig_img = PILImage.open(image_path).convert("RGB")
        orig_w, orig_h = orig_img.size

        # 分辨率计算 (对齐 ComfyUI SideResize: 短边=resolution, 长边<=max_resolution)
        scale_factor = 2.0
        if resolution > 0:
            target_short = resolution
            current_short = min(orig_h, orig_w)
            scale_factor = target_short / current_short
            if max_resolution > 0:
                target_long = max(orig_h, orig_w) * scale_factor
                if target_long > max_resolution:
                    scale_factor = max_resolution / max(orig_h, orig_w)

        res_h = int(orig_h * scale_factor)
        res_w = int(orig_w * scale_factor)
        res_h = res_h - (res_h % 2)
        res_w = res_w - (res_w % 2)

        # 预处理图像 (与 ComfyUI 工作流一致: [0,1] 传入 transform)
        img_np = np.array(orig_img).astype(np.float32) / 255.0  # [0, 1]
        image = torch.from_numpy(img_np).permute(2, 0, 1)  # C H W
        image = image.unsqueeze(0)  # T C H W (T=1)
        del orig_img, img_np
        gc.collect()

        # 变换: NaResize + DivisibleCrop
        video_transform = self._build_video_transform(res_h, res_w)
        cond_latent = video_transform(image)  # C T H W
        input_video = cond_latent.clone()
        del image
        gc.collect()

        return cond_latent, input_video, res_h, res_w, scale_factor

    def _postprocess_output(
        self,
        decoded: list,
        input_video: torch.Tensor,
        color_fix_method: str,
        res_h: int,
        res_w: int,
        image_path: str,
        output_dir: str,
        scale_factor: float,
        inf: dict,
        cfg_scale: float,
        sample_steps: int,
        blockswap_was_active: bool,
        output_name: str | None = None,
        watermark_payload: str | None = None,
    ) -> RestoreResult:
        """后处理: 颜色校正、保存输出、创建 RestoreResult

        集成多种后处理增强:
        - 颜色校正 (LAB/Wavelet/AdaIN)
        - 小波重建锐化增强 (DiffBIR inspired)
        - Alpha 通道处理 (waifu2x inspired)
        - EXIF 元数据复制 (upscayl inspired)
        - 图像锐化增强 (Real-ESRGAN inspired)
        - 文本修复流水线 (Vivid-VR inspired, 可选)

        Args:
            decoded: VAE 解码结果
            input_video: 原始输入视频张量 (用于颜色校正参考)
            color_fix_method: 颜色校正方法
            res_h, res_w: 输出分辨率
            image_path: 输入图像路径
            output_dir: 输出目录
            output_name: 输出文件名 (含扩展名)，为 None 时使用默认命名
            scale_factor: 缩放因子
            inf: 推理配置字典 (含 inference_mode 等)
            cfg_scale: CFG 缩放
            sample_steps: 采样步数
            blockswap_was_active: BlockSwap 是否激活

        Returns:
            RestoreResult
        """

        from app.integrated_app.optimization.inference.post_processing import (
            TextRestorationConfig,
            TextRestorationPipeline,
            apply_sharpening,
            copy_exif_metadata,
            extract_alpha_from_image,
            merge_alpha_to_image,
            wavelet_reconstruction,
        )

        # 读取原始图像，处理 Alpha 通道
        original_alpha = None
        try:
            orig_img_pil = PILImage.open(image_path)
            orig_img_np = np.array(orig_img_pil)
            _, original_alpha = extract_alpha_from_image(orig_img_np)
            del orig_img_pil, orig_img_np
        except Exception as e:
            logger.debug(f"Alpha 通道提取失败: {e}")

        sample = decoded[0]  # [C, T, H, W] or [C, H, W]

        # 处理时间维度: C T H W -> C H W (单帧图像)
        if sample.ndim == 4:
            sample = rearrange(sample, "c t h w -> t c h w")  # T C H W
            sample = sample[0]  # C H W

        result_np = _tensor_to_uint8_np(sample)
        result_np = result_np.transpose(1, 2, 0)  # C H W -> H W C

        del sample, decoded
        gc.collect()

        ref_np = None
        if input_video is not None:
            ref = input_video
            if ref.ndim == 4:
                ref = rearrange(ref, "c t h w -> t c h w")[0]
            ref_np = _tensor_to_uint8_np(ref)
            ref_np = ref_np.transpose(1, 2, 0)

        # 颜色校正
        if color_fix_method != "none" and ref_np is not None:
            result_np = apply_color_correction(result_np, ref_np, method=color_fix_method)

        # 小波重建后处理 (DiffBIR inspired) - 提升锐度
        postprocess_cfg = self.config.get("postprocessing", {})
        enable_wavelet = postprocess_cfg.get("wavelet_reconstruction", True)
        if enable_wavelet and ref_np is not None:
            try:
                level = postprocess_cfg.get("wavelet_level", 3)
                low_freq_weight = postprocess_cfg.get("low_freq_weight", 0.8)
                result_np = wavelet_reconstruction(result_np, ref_np, level=level, low_freq_weight=low_freq_weight)
                logger.debug(f"小波重建应用: level={level}, low_freq_weight={low_freq_weight}")
            except Exception as e:
                logger.debug(f"wavelet_reconstruction skipped: {e}")

        # 锐化增强 (Real-ESRGAN inspired)
        sharpen_strength = postprocess_cfg.get("sharpen_strength", 0.0)
        if sharpen_strength > 0:
            try:
                result_np = apply_sharpening(result_np, strength=sharpen_strength, method="unsharp_mask")
                logger.debug(f"锐化增强应用: strength={sharpen_strength}")
            except Exception as e:
                logger.debug(f"sharpening skipped: {e}")

        # 文本修复流水线 (Vivid-VR inspired, 可选)
        enable_text_restoration = postprocess_cfg.get("text_restoration", False)
        if enable_text_restoration and ref_np is not None:
            try:
                text_config = TextRestorationConfig(
                    enabled=True,
                    ocr_languages=postprocess_cfg.get("ocr_languages", ["ch_sim", "en"]),
                    ocr_confidence_threshold=postprocess_cfg.get("ocr_confidence", 0.5),
                    text_enhance_method=postprocess_cfg.get("text_enhance_method", "sharpen"),
                )
                text_pipeline = TextRestorationPipeline(text_config)
                result_np = text_pipeline.process(result_np, ref_np)
                logger.info("文本修复流水线已应用")
            except Exception as e:
                logger.debug(f"text_restoration skipped: {e}")

        # 合并 Alpha 通道 (如果有)
        if original_alpha is not None:
            try:
                result_np = merge_alpha_to_image(result_np, original_alpha)
                logger.debug("Alpha 通道已合并")
            except Exception as e:
                logger.debug(f"Alpha 通道合并失败: {e}")

        del input_video, ref_np, original_alpha
        gc.collect()

        # 保存：默认沿用输入文件名（只换扩展名）；批量场景传入 output_name 按模板命名
        # 获取输出格式（从 inf 或默认自动匹配）
        requested_format = inf.get("output_format", "").lower().strip()

        # 🔍 DEBUG: 打印输出的实际值
        logger.info(
            f"🔧 [DEBUG] 输出格式调试：inf={inf.get('output_format', 'MISSING')}, requested_format='{requested_format}'"
        )

        # 如果用户选择"默认"（空字符串），则根据输入图片的扩展名自动匹配
        if not requested_format and image_path:
            input_ext = os.path.splitext(image_path)[1].lower()
            format_map_reverse = {
                ".png": "png",
                ".jpg": "jpg",
                ".jpeg": "jpg",
                ".webp": "webp",
                ".bmp": "bmp",
                ".tiff": "tiff",
                ".tif": "tiff",
            }
            requested_format = format_map_reverse.get(input_ext, "png")
            logger.info(f"🔧 [DEBUG] 自动匹配格式：输入={image_path}, 扩展名={input_ext} -> {requested_format}")

        # 验证格式合法性
        valid_formats = ("png", "jpg", "jpeg", "webp", "bmp", "tiff")
        if not requested_format or requested_format not in valid_formats:
            logger.warning(f"⚠️ [WARN] 无效的输出格式 '{requested_format}', 回退到 PNG")
            requested_format = "png"

        # 取证模式（SEEDVR2_WATERMARK_PROOF_MODE=1）：强制无损落盘。有损编码器会抹掉
        # 隐式标识（实测 JPEG q95 属临界存活），换 PNG 后产物侧变成确定性可验证。
        # 只影响本机显式开启的部署，默认关闭以保持交付格式习惯。
        if os.environ.get("SEEDVR2_WATERMARK_PROOF_MODE", "0") == "1" and requested_format in ("jpg", "jpeg", "webp"):
            logger.info(f"取证模式已启用：输出格式 {requested_format} → PNG（无损，标识确定性可验证）")
            requested_format = "png"

        logger.info(f"✅ [INFO] 最终使用输出格式：{requested_format.upper()}")

        format_map = {
            "png": ".png",
            "jpg": ".jpg",
            "jpeg": ".jpg",
            "webp": ".webp",
            "bmp": ".bmp",
            "tiff": ".tiff",
        }
        ext = format_map.get(requested_format, ".png")

        if output_name is None:
            output_name = build_output_name(image_path, ext)
        else:
            # 指定了输出格式时，强制用目标格式的扩展名，覆盖输入文件的原始扩展名
            # （批量模板带的是 {ext}=输入扩展名，直接保存会按原格式写出）
            stem, _nouse = os.path.splitext(output_name)
            output_name = stem + ext
        output_path = _resolve_unique_path(output_dir, output_name)

        # 根据格式保存图片
        save_kwargs = {}
        if requested_format in ("jpg", "jpeg"):
            save_kwargs["quality"] = 95
            save_kwargs["optimize"] = True
        elif requested_format == "webp":
            save_kwargs["quality"] = 90
            save_kwargs["lossless"] = False

        # 数据治理 P2-5：生成参数 + 源图 EXIF 合并进同一次编码保存
        # （避免 JPEG 二次有损压缩，也避免与 copy_exif 的相互覆盖）
        metadata_kwargs: dict = {}
        try:
            from app.integrated_app.utils.output_metadata import build_save_metadata_kwargs

            metadata_kwargs = build_save_metadata_kwargs(
                ext=ext,
                params=dict(inf),
                source_path=image_path,
                copy_source_exif=bool(postprocess_cfg.get("copy_exif", True)),
            )
            save_kwargs.update({k: v for k, v in metadata_kwargs.items() if k != "exif_merged"})
        except Exception as e:
            logger.debug(f"生成参数元数据构建失败（跳过嵌入）: {e}")

        # 嵌入不可感知数字水印 (归属溯源, DCT 频域)——强制启用，无关闭开关。
        # 失败处置策略化（评估报告 R2）：不再静默输出无水印文件
        # P3-1：payload 绑定 task_id，使输出图可反查到产生它的任务与参数
        from app.integrated_app.services.watermark_policy import (
            embed_with_retry,
            handle_watermark_loss,
            output_carries_watermark,
            report_missing_watermark_key,
            resolve_watermark_failure_policy,
            select_image_embed_tier,
        )

        watermark_policy = resolve_watermark_failure_policy(self.config)
        report_missing_watermark_key()
        # 自适应档位：有损编码格式连 JPEG q95 都会抹掉无损档水印（2026-09-19 实测），
        # 那类产物直接用鲁棒档（PSNR ≈ 37dB）换可举证性；PNG/BMP/TIFF 保持无损档画质。
        tier_alpha, tier_repeat = select_image_embed_tier(ext)
        result_np, watermark_embedded, wm_error = embed_with_retry(
            result_np, payload=watermark_payload, alpha=tier_alpha, repeat=tier_repeat
        )

        pil_img = PILImage.fromarray(result_np)
        # JPEG/WebP 不支持透明通道，需要转换为 RGB
        if requested_format in ("jpg", "jpeg") and pil_img.mode in ("RGBA", "LA", "P"):
            # 白色背景合成透明图
            background = PILImage.new("RGB", pil_img.size, (255, 255, 255))
            if pil_img.mode == "P":
                pil_img = pil_img.convert("RGBA")
            background.paste(pil_img, mask=pil_img.split()[-1] if pil_img.mode in ("RGBA", "LA") else None)
            pil_img = background
        elif requested_format in ("jpg", "jpeg") and pil_img.mode != "RGB":
            pil_img = pil_img.convert("RGB")

        pil_img.save(output_path, **save_kwargs)

        # 复制 EXIF 元数据 (upscayl inspired)——保存时已合并源图 EXIF 的情况下跳过
        # （copy_exif 的二次保存会用源图 EXIF 覆盖输出，抹掉生成参数元数据）
        enable_exif_copy = postprocess_cfg.get("copy_exif", True)
        if enable_exif_copy and not metadata_kwargs.get("exif_merged"):
            try:
                copy_exif_metadata(image_path, output_path)
            except Exception as e:
                logger.debug(f"EXIF 复制失败: {e}")

        # 水印缺失统一处置（评估报告 R2）：嵌入报错 ≠ 唯一失败方式——编码器/后处理
        # 同样能抹掉它，只有重读落盘产物验签才知道真伪；缺失绝不再静默。
        if not watermark_embedded:
            loss_reason = wm_error
        elif output_carries_watermark(output_path):
            logger.debug(
                "数字水印已嵌入并落盘验签通过 (payload=%s, alpha=%s, repeat=%s)",
                watermark_payload or "auto",
                tier_alpha,
                tier_repeat,
            )
            loss_reason = None
        else:
            loss_reason = f"{requested_format.upper()} 编码后水印验签未通过（alpha={tier_alpha}, repeat={tier_repeat}）"
        if loss_reason:
            handle_watermark_loss(
                policy=watermark_policy,
                output_path=output_path,
                error=loss_reason,
                payload=watermark_payload,
            )

        # 计算输出统计
        if result_np.shape[-1] >= 3:
            mean_val = result_np[..., :3].mean()
            std_val = result_np[..., :3].std()
        else:
            mean_val = result_np.mean()
            std_val = result_np.std()
        logger.info(f"输出: {result_np.shape[1]}x{result_np.shape[0]}, Mean={mean_val:.1f}, Std={std_val:.1f}")
        logger.info(f"保存: {output_path}")

        del result_np
        _cleanup_cuda_cache(deep=True)

        # P2-1: 结束 VRAM 监控并把全局峰值写入 metadata（由上传路由落库 history.vram_peak_mb）
        vram_peak_mb = 0.0
        # P3-2：分阶段耗时（毫秒）与 DiT 采样吞吐 it/s（口径见 docs/METRICS_SPEC.md）
        stage_durations_ms: dict[str, float] = {}
        dit_seconds = 0.0
        steps_per_second = 0.0
        monitor = getattr(self, "_vram_monitor", None)
        if monitor is not None:
            try:
                monitor.end_inference()
                monitor.log_report()
                report = monitor.get_report()
                vram_peak_mb = float(report.get("global_peak_allocated_mb", 0.0))
                for stage in report.get("stages", []) or []:
                    # 注意：vram_monitor 的阶段键名是 "stage"（非 "stage_name"）
                    name = stage.get("stage")
                    if name:
                        stage_durations_ms[str(name)] = float(stage.get("duration_ms", 0.0) or 0.0)
                dit_seconds = stage_durations_ms.get("dit_sample", 0.0) / 1000.0
                if dit_seconds > 0:
                    steps_per_second = round(float(sample_steps) / dit_seconds, 3)
            except Exception as e:
                logger.debug(f"VRAM 监控报告生成失败: {e}")
            finally:
                self._vram_monitor = None

        return RestoreResult(
            success=True,
            output_path=output_path,
            processing_time=0.0,  # 由调用方填充
            metadata={
                "model_size": self.model_size,
                "precision": self.precision,
                "output_resolution": f"{res_w}x{res_h}",
                "scale_factor": scale_factor,
                "inference_mode": inf["inference_mode"],
                "cfg_scale": cfg_scale,
                "sample_steps": sample_steps,
                "blockswap_active": blockswap_was_active,
                "output_format": requested_format,  # 实际使用的输出格式
                "mean": float(mean_val),
                "std": float(std_val),
                "vram_peak_mb": vram_peak_mb,
                "vae_decode_oom_fallback": (
                    self.get_vae_decode_oom_fallback()
                    if hasattr(self, "get_vae_decode_oom_fallback")
                    else None
                ),
                # MLOps P1-1：实际使用的种子（-1 已物化为抽签值），供服务层回写 history parameters
                "seed_effective": inf.get("seed"),
                # P3-2：统一速度口径 —— it/s 只以 DiT 采样阶段耗时为分母（不含 VAE/IO），
                # 与 processing_time（端到端墙钟）区分，避免跨口径比较
                "dit_seconds": round(dit_seconds, 3),
                "steps_per_second": steps_per_second,
                "stage_durations_ms": stage_durations_ms,
                "postprocessing": {
                    "wavelet": enable_wavelet,
                    "sharpen": sharpen_strength > 0,
                    "text_restoration": enable_text_restoration,
                },
            },
        )

    def _infer_image_impl(
        self,
        image_path: str,
        output_dir: str,
        cfg: ImageInferenceConfig,
        output_name: str | None = None,
        watermark_payload: str | None = None,
    ) -> RestoreResult:
        """图像修复推理同步实现 - 在线程中运行

        REFACTOR [B1-1] [P3-1]: 删除 copy.deepcopy(self.config) 配置快照
        - 原实现通过修改 self.config 全局状态传递请求级参数给 _load_dit_model/_load_vae_model，
          违反单一职责原则（引擎级配置不应被单个请求污染），且 deepcopy 大字典有性能开销
        - 改为显式参数化 _load_dit_model / _load_vae_model，参数直接从 cfg 读取
        - 删除 finally 中的 self.config = _config_snapshot 恢复逻辑
        """
        start_time = time.time()

        if not self._loaded:
            return RestoreResult(success=False, error="模型未加载")

        try:
            os.makedirs(output_dir, exist_ok=True)
            _check_memory()
            _log_memory("推理初始")

            # REFACTOR [E4-1]: 阶段0 检查取消信号
            self._check_cancelled("image:init")

            # 从 ImageInferenceConfig 读取推理参数
            inf = self._get_inference_config(
                seed=cfg.seed,
                resolution=cfg.resolution,
                max_resolution=cfg.max_resolution,
                batch_size=cfg.batch_size,
                uniform_batch_size=cfg.uniform_batch_size,
                color_correction=cfg.color_correction,
                temporal_overlap=cfg.temporal_overlap,
                prepend_frames=cfg.prepend_frames,
                input_noise_scale=cfg.input_noise_scale,
                latent_noise_scale=cfg.latent_noise_scale,
                attention_mode=cfg.attention_mode,
                enable_debug=cfg.enable_debug,
            )
            # REFACTOR [E5-1]: 添加 request-level 参数（与模型无关）
            inf["output_format"] = cfg.output_format  # 输出格式选择

            seed = inf["seed"]
            if seed == -1:
                seed = random.randint(0, MAX_SEED)
                # MLOps P1-1：随机抽签回写请求级 inf，实际种子就此物化——
                # 输出图元数据（dict(inf)）与 result.metadata.seed_effective 均取该值
                inf["seed"] = seed

            cfg_scale = inf["cfg_scale"]
            cfg_rescale = inf["cfg_rescale"]
            sample_steps = inf["sample_steps"]
            color_fix_method = inf["color_correction"]
            input_noise_scale = inf["input_noise_scale"]
            latent_noise_scale = inf["latent_noise_scale"]

            # 读取并预处理图像
            cond_latent, input_video, res_h, res_w, scale_factor = self._prepare_image_input(
                image_path, inf["resolution"], inf["max_resolution"]
            )
            logger.info(
                f"图像修复: {image_path}, -> {res_w}x{res_h}, "
                f"seed={seed}, mode={inf['inference_mode']}, cfg={cfg_scale}, steps={sample_steps}"
            )

            # REFACTOR [B1-1] [P3-1]: 构建请求级 VAE tiled 配置（从 cfg 读取）
            # 替代原 self.config["model"]["vae"][...] = ... 的配置污染
            vae_tiled_config = {
                "encode_tiled": cfg.encode_tiled,
                "encode_tile_size": cfg.encode_tile_size,
                "encode_tile_overlap": cfg.encode_tile_overlap,
                "decode_tiled": cfg.decode_tiled,
                "decode_tile_size": cfg.decode_tile_size,
                "decode_tile_overlap": cfg.decode_tile_overlap,
                "tile_debug": cfg.tile_debug,
                "offload_device": cfg.vae_offload_device,
                "cache_model": cfg.vae_cache_model,
            }

            logger.info(
                f"工作流参数: dit_model={cfg.dit_model}, dit_device={cfg.dit_device}, "
                f"blocks_to_swap={cfg.blocks_to_swap}, swap_io_components={cfg.swap_io_components}, "
                f"attention_mode={cfg.attention_mode}, "
                f"vae_model={cfg.vae_model}, vae_device={cfg.vae_device}, "
                f"encode_tiled={cfg.encode_tiled}, decode_tiled={cfg.decode_tiled}, "
                f"encode_tile_size={cfg.encode_tile_size}, decode_tile_size={cfg.decode_tile_size}, "
                f"tile_debug={cfg.tile_debug}, "
                f"resolution={cfg.resolution}, seed={cfg.seed}, color_correction={cfg.color_correction}"
            )

            # ==================== 阶段1: 加载VAE → 编码 → 销毁VAE ====================
            # REFACTOR [E4-1]: 阶段切换点检查取消信号
            self._check_cancelled("image:stage1-vae-encode")
            self._report_progress(current_frame=0, total_frames=4, progress=10.0, message="加载 VAE 模型...")
            logger.info("=" * 60)
            logger.info("阶段1: VAE 编码")
            logger.info("=" * 60)
            _check_memory()
            _log_memory("阶段1开始")

            # REFACTOR [B1-1] [P3-1]: 显式参数化 _load_vae_model，
            # 不再修改 self.config 全局状态
            # cache_model=True 时复用上一次任务缓存的 VAE，跳过加载
            if self.vae is None:
                self.vae = self._load_vae_model(
                    model_config=self._model_config,
                    checkpoint_path=self._vae_checkpoint_path,
                    device=self.device,
                    vae_tiled_config=vae_tiled_config,
                    torch_compile_args=inf.get("torch_compile") or None,
                )
                self.vae.to(device=self.device)
                _log_memory("VAE加载到GPU后")
                _check_memory()
            else:
                # 复用缓存 VAE 时必须确保其在 GPU 上：视频任务结束时会把 VAE 移回 CPU，
                # 若不迁移则编码会退化为 CPU 推理导致任务卡死
                self.vae.to(device=self.device)
                logger.info("cache_model=True: 复用已缓存的 VAE 模型（已迁移到 GPU）")

            # P0-2: 请求级 tile 配置在缓存复用时也必须落到引擎字典。
            # 此前 vae_tiled_config 只在“VAE 未加载”分支传给 _load_vae_model，
            # 跨任务复用缓存 VAE 时仍沿用旧配置，OOM 重试的 TILE_DECREASE 会失效
            self._vae_tiled_config = {**(getattr(self, "_vae_tiled_config", None) or {}), **vae_tiled_config}

            self._report_progress(current_frame=0, total_frames=4, progress=15.0, message="VAE 编码中...")
            with self._vram_stage("vae_encode"):
                cond_latents = self._vae_encode([cond_latent])
            del cond_latent
            gc.collect()

            self._report_progress(current_frame=0, total_frames=4, progress=25.0, message="VAE 编码完成，准备 DiT...")

            # 销毁 VAE 释放内存，为 DiT 腾出空间
            self._destroy_vae()
            _log_memory("VAE销毁后")
            _check_memory()

            # ==================== 阶段2: 加载DiT → 采样 → 销毁DiT ====================
            # REFACTOR [E4-1]: 阶段切换点检查取消信号
            self._check_cancelled("image:stage2-dit-sample")
            self._report_progress(current_frame=1, total_frames=4, progress=28.0, message="加载 DiT 模型...")
            logger.info("=" * 60)
            logger.info("阶段2: DiT 采样")
            logger.info("=" * 60)
            _check_memory()
            _log_memory("阶段2开始")

            # REFACTOR [B1-1] [P3-1]: 显式参数化 _load_dit_model，
            # 不再修改 self.config 全局状态
            # cache_model=True 时复用上一次任务缓存的 DiT，跳过加载；
            # force_reload_dit=True 时即使有缓存也按当前参数（如 blocks_to_swap）强制重载
            force_reload_dit = bool(getattr(cfg, "force_reload_dit", False))
            # P1-2: 加载签名守卫——缓存的 DiT 与当前请求的加载参数不一致时
            # 自动重载（典型场景：OOM 降级重试提高了 blocks_to_swap）
            if self.dit is not None and not force_reload_dit:
                request_signature = build_dit_load_signature(
                    checkpoint_path=self._dit_checkpoint_path,
                    precision=self._dit_precision,
                    blocks_to_swap=cfg.blocks_to_swap,
                    swap_io_components=cfg.swap_io_components,
                    offload_device=cfg.dit_offload_device,
                    attention_mode=cfg.attention_mode,
                    torch_compile_args=inf.get("torch_compile") or None,
                )
                if getattr(self, "_dit_load_signature", None) != request_signature:
                    logger.info("检测到 DiT 加载参数变化，重载缓存的 DiT 模型...")
                    self._destroy_dit()
                    gc.collect()
                    _force_release_memory()
                    force_reload_dit = True
            if self.dit is None or force_reload_dit:
                if force_reload_dit and self.dit is not None:
                    logger.info("force_reload_dit=True: 销毁缓存的 DiT 并按当前参数重载...")
                    self._destroy_dit()
                    gc.collect()
                    _force_release_memory()
                self.dit = self._load_dit_model(
                    model_size=self._dit_model_size,
                    model_config=self._model_config,
                    checkpoint_path=self._dit_checkpoint_path,
                    precision=self._dit_precision,
                    device=self.device,
                    blocks_to_swap=cfg.blocks_to_swap,
                    swap_io_components=cfg.swap_io_components,
                    offload_device=cfg.dit_offload_device,
                    attention_mode=cfg.attention_mode,
                    torch_compile_args=inf.get("torch_compile") or None,
                )
                self.dit.to(device=self.device)
                _log_memory("DiT加载后")
                _check_memory()
            else:
                logger.info("cache_model=True: 复用已缓存的 DiT 模型")
                # P0-1 安全网：上一任务可能在 VAE 解码前把 DiT 卸载到了 CPU，
                # 此处统一恢复（对 BlockSwap 模型会重建 blocks 设备分布）
                restore_model_to_gpu(self.dit, self.device, "DiT")

            self._report_progress(current_frame=1, total_frames=4, progress=35.0, message="DiT 采样中...")
            text_embeds = self._get_text_embeds()

            logger.info(f"开始 DiT 采样: cfg={cfg_scale}, steps={sample_steps}, blockswap={self._blockswap_active}")
            with self._vram_stage("dit_sample"):
                samples = self._generation_step(
                    cond_latents=cond_latents,
                    text_embeds=text_embeds,
                    cfg_scale=cfg_scale,
                    cfg_rescale=cfg_rescale,
                    sample_steps=sample_steps,
                    seed=seed,
                    input_noise_scale=input_noise_scale,
                    latent_noise_scale=latent_noise_scale,
                    restoration_guidance_scale=inf.get("restoration_guidance_scale", 0.0),
                )

            # 释放中间变量
            del cond_latents, text_embeds
            gc.collect()

            # 保存 blockswap 状态 (销毁 DiT 后会清除标志)
            blockswap_was_active = self._blockswap_active

            # P1-2: dit_cache_model=True 时保留 DiT 供后续任务复用
            # （跨任务省去 6.8-16.5GB 权重重载；由空闲超时卸载与签名守卫兜底）；
            # 否则销毁释放显存
            if not cfg.dit_cache_model:
                self._destroy_dit()
                _log_memory("DiT销毁后")
                _check_memory()
            else:
                # P0-1: dit_cache_model=True 时 DiT 会以 bf16 常驻（nvfp4 在加载期反量化，
                # 3B 约 6.3GB）。VAE 解码峰值另需数 GB，两者叠加会超出物理显存，
                # 触发 Windows WDDM 分页（表现为静默卡死，而非 OOM）。
                # 这里把 DiT 整体卸载到 CPU：保留实例供下一任务复用，只让出显存。
                free_gb = get_free_vram_gb(self.device)
                if free_gb >= DIT_KEEP_RESIDENT_FREE_GB:
                    logger.info(
                        f"DiT 保持常驻（空闲显存 {free_gb:.2f}GB ≥ {DIT_KEEP_RESIDENT_FREE_GB:.1f}GB），跳过卸载"
                    )
                else:
                    logger.info(
                        f"空闲显存仅 {free_gb:.2f}GB（< {DIT_KEEP_RESIDENT_FREE_GB:.1f}GB），"
                        "VAE 解码前将 DiT 卸载到 CPU 保留缓存"
                    )
                    if offload_model_to_cpu(
                        self.dit,
                        "DiT",
                        reason=f"free VRAM for VAE decode (free={free_gb:.2f}GB)",
                    ):
                        _log_memory("DiT卸载到CPU后")
                        _check_memory()

            # ==================== 阶段3: 加载VAE → 解码 → 销毁VAE ====================
            # REFACTOR [E4-1]: 阶段切换点检查取消信号
            self._check_cancelled("image:stage3-vae-decode")
            self._report_progress(current_frame=2, total_frames=4, progress=65.0, message="VAE 解码中...")
            logger.info("=" * 60)
            logger.info("阶段3: VAE 解码")
            logger.info("=" * 60)
            _check_memory()
            _log_memory("阶段3开始")

            # REFACTOR [B1-1] [P3-1]: 显式参数化 _load_vae_model（复用 vae_tiled_config）
            if self.vae is None:
                self.vae = self._load_vae_model(
                    model_config=self._model_config,
                    checkpoint_path=self._vae_checkpoint_path,
                    device=self.device,
                    vae_tiled_config=vae_tiled_config,
                    torch_compile_args=inf.get("torch_compile") or None,
                )
                self.vae.to(device=self.device)
                _log_memory("VAE重新加载到GPU后")
                _check_memory()
            else:
                # 防御性：复用缓存 VAE 时确保其在 GPU 上（同阶段1修复）
                self.vae.to(device=self.device)
                logger.info("cache_model=True: 复用已缓存的 VAE 模型（已迁移到 GPU）")

            with self._vram_stage("vae_decode"):
                decoded = self._vae_decode(samples)

            # 释放 samples
            del samples
            gc.collect()

            # cache_model=True 时保留 VAE 供后续任务复用；否则销毁释放显存
            if not cfg.vae_cache_model:
                self._destroy_vae()
                _log_memory("VAE最终销毁后")

            # ==================== 阶段4: 后处理 ====================
            # REFACTOR [E4-1]: 阶段切换点检查取消信号
            self._check_cancelled("image:stage4-postprocess")
            self._report_progress(current_frame=3, total_frames=4, progress=85.0, message="后处理中...")
            logger.info("=" * 60)
            logger.info("阶段4: 后处理")
            logger.info("=" * 60)
            result = self._postprocess_output(
                decoded=decoded,
                input_video=input_video,
                color_fix_method=color_fix_method,
                res_h=res_h,
                res_w=res_w,
                image_path=image_path,
                output_dir=output_dir,
                scale_factor=scale_factor,
                inf=inf,
                cfg_scale=cfg_scale,
                sample_steps=sample_steps,
                blockswap_was_active=blockswap_was_active,
                output_name=output_name,
                watermark_payload=watermark_payload,
            )
            result.processing_time = time.time() - start_time
            return result

        except InferenceCancelledError as e:
            logger.warning(f"图像推理被取消: {e}")
            self._cleanup_after_error()
            return RestoreResult(
                success=False,
                error="推理已被取消",
                processing_time=time.time() - start_time,
                metadata={"cancelled": True, "stage": e.detail.get("stage", "")},
            )

        except MemoryError as e:
            logger.error(f"内存不足，紧急终止推理: {e}")
            self._cleanup_after_error()
            return RestoreResult(success=False, error=str(e), processing_time=time.time() - start_time)

        except Exception as e:
            logger.error(f"图像修复失败: {e}", exc_info=True)
            self._cleanup_after_error()
            return RestoreResult(success=False, error=str(e), processing_time=time.time() - start_time)
        # REFACTOR [B1-1]: 删除 finally 中的 self.config = _config_snapshot
        # 显式参数化后不再修改 self.config，无需恢复

    async def infer_batch(self, input_dir: str, output_dir: str, **kwargs) -> list[RestoreResult]:
        """批量图像修复 - 从文件夹加载图片并逐张处理

        Args:
            input_dir: 输入图片文件夹路径
            output_dir: 输出目录
            **kwargs: 传递给 infer_image 的参数

        Returns:
            每张图片的修复结果列表
        """
        if not self._loaded:
            return [RestoreResult(success=False, error="模型未加载")]

        input_path = Path(input_dir)
        if not input_path.is_dir():
            return [RestoreResult(success=False, error=f"输入目录不存在: {input_dir}")]

        # 支持的图片格式
        image_extensions = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tiff", ".tif"}
        image_files = sorted([f for f in input_path.iterdir() if f.is_file() and f.suffix.lower() in image_extensions])

        if not image_files:
            return [RestoreResult(success=False, error=f"目录中未找到图片: {input_dir}")]

        logger.info(f"批量处理: 找到 {len(image_files)} 张图片")
        os.makedirs(output_dir, exist_ok=True)

        results = []
        for i, image_file in enumerate(image_files):
            logger.info(f"处理 [{i+1}/{len(image_files)}]: {image_file.name}")
            try:
                result = await self.infer_image(
                    image_path=str(image_file),
                    output_dir=output_dir,
                    **kwargs,
                )
                results.append(result)
                if result.success:
                    logger.info(f"完成 [{i+1}/{len(image_files)}]: {image_file.name} -> {result.output_path}")
                else:
                    logger.warning(f"失败 [{i+1}/{len(image_files)}]: {image_file.name} - {result.error}")
            except Exception as e:
                logger.error(f"异常 [{i+1}/{len(image_files)}]: {image_file.name} - {e}")
                results.append(RestoreResult(success=False, error=str(e)))

        success_count = sum(1 for r in results if r.success)
        logger.info(f"批量处理完成: {success_count}/{len(results)} 成功")
        return results
