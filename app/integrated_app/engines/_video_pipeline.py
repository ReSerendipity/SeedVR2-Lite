"""Video inference pipeline mixin for SeedVR2Engine.

Extracted from seedvr2_engine.py as part of structural refactoring
(phase 2A). Contains video inference implementation methods.
"""

import asyncio
import gc
import logging
import os
import random
import shutil
import time

import numpy as np
import torch
from einops import rearrange
from torchvision.transforms import Compose, Lambda, Normalize

from app.integrated_app.color_fix import apply_color_correction
from app.integrated_app.engine_interface import RestoreResult
from app.integrated_app.engines._image_pipeline import _resolve_unique_path
from app.integrated_app.engines._memory_utils import (
    MAX_SEED,
    TEMPORAL_ALIGN_MULTIPLE,
    TILE_ALIGNMENT_FACTOR,
    _check_memory,
    _cleanup_cuda_cache,
    _DivisibleCrop,
    _force_release_memory,
    _log_memory,
    _NaResize,
    _RearrangeTCHW2CTHW,
    _tensor_to_uint8_np,
    build_dit_load_signature,
)
from app.integrated_app.exceptions import InferenceCancelledError
from app.integrated_app.gpu_utils import oom_protect
from app.integrated_app.optimization.gpu.cache_manager import get_cache_manager
from app.integrated_app.optimization.gpu.memory_manager import clear_memory
from app.integrated_app.utils.output_names import build_output_name

logger = logging.getLogger(__name__)


class _VideoPipelineMixin:
    """Mixin: pipeline methods extracted from SeedVR2Engine."""

    @oom_protect
    async def infer_video(
        self,
        video_path: str,
        output_dir: str,
        output_name: str | None = None,
        watermark_payload: str | None = None,
        **kwargs,
    ) -> RestoreResult:
        """视频修复推理 - 在线程中运行以避免阻塞事件循环

        阶段1 (VAE编码): VAE在GPU, DiT在CPU
        阶段2 (DiT推理): DiT在GPU(BlockSwap动态交换), VAE在CPU
        阶段3 (VAE解码): VAE在GPU, DiT已清理
        阶段4 (后处理): 无模型
        """
        # REFACTOR [E4-1]: 每次推理开始前重置取消令牌
        self._reset_cancel_token()
        # VRAM 预检 (DiffBIR inspired)
        try:
            from app.integrated_app.optimization.gpu.vram_monitor import VRAMPeakMonitor

            self._vram_monitor = VRAMPeakMonitor(device=self.device, enabled=True)
        except Exception:
            self._vram_monitor = None
        return await asyncio.to_thread(
            self._infer_video_impl,
            video_path,
            output_dir,
            output_name=output_name,
            watermark_payload=watermark_payload,
            **kwargs,
        )

    @staticmethod
    def _segment_frames_complete(
        frames_dir: str, seg_start: int, seg_end: int, expected_hw: tuple[int, int] | None = None
    ) -> bool:
        """判断某段的输出帧是否已全部完整在盘（帧级断点续跑判定）。

        Args:
            frames_dir: 输出帧目录。
            seg_start: 段起始帧号（含）。
            seg_end: 段结束帧号（不含）。
            expected_hw: 期望帧尺寸 (h, w)。传入时校验帧尺寸与本次推理
                输出分辨率一致——OOM 降级阶梯可能降低分辨率，尺寸不一致
                的残留帧必须整段重算，否则 ffmpeg 合成会得到混尺寸帧序列。

        Returns:
            True 表示该段可跳过推理直接复用。
        """
        for idx in range(seg_start, seg_end):
            path = os.path.join(frames_dir, f"frame_{idx:06d}.png")
            try:
                if os.path.getsize(path) <= 0:
                    return False
            except OSError:
                return False
        if expected_hw is not None:
            import cv2

            img = cv2.imread(os.path.join(frames_dir, f"frame_{seg_start:06d}.png"))
            if img is None or (img.shape[0], img.shape[1]) != expected_hw:
                return False
        return True

    @staticmethod
    def _rebuild_tail_from_disk(frames_dir: str, seg_start: int, overlap_n: int):
        """从盘上重建上一段的处理后尾帧（帧级续跑的段间混合上下文）。

        盘上帧与原运行写入内容等价（均为段末未与下段混合的成品帧），
        以此恢复 prev_tail_out，保证续跑段的重叠混合与连续运行一致。

        Returns:
            uint8 HWC RGB 数组；任一帧缺失/不可读或 overlap_n<=0 时返回 None。
        """
        if overlap_n <= 0 or seg_start <= 0:
            return None
        import cv2
        import numpy as np

        frames = []
        for idx in range(max(0, seg_start - overlap_n), seg_start):
            img = cv2.imread(os.path.join(frames_dir, f"frame_{idx:06d}.png"))
            if img is None:
                return None
            frames.append(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
        return np.array(frames) if frames else None

    def _infer_video_impl(
        self,
        video_path: str,
        output_dir: str,
        output_name: str | None = None,
        watermark_payload: str | None = None,
        **kwargs,
    ) -> RestoreResult:
        """视频修复推理同步实现 - 分段流式处理，避免长视频全量加载导致 OOM

        与全量读取不同，本实现将长视频按段顺序读取 → 逐段推理 → 逐段写盘:
        - 任何时刻内存中最多驻留一段帧，杜绝整段视频全量加载的内存峰值
        - 每段按 VAE编码 → DiT采样 → VAE解码 → 后处理 四阶段流水线处理
        - 段间通过 overlap 余弦混合 + FeaturePropagation 保持时间一致性
        - 逐段写盘后由 ffmpeg 合成最终视频并合并音轨
        """
        start_time = time.time()

        if not self._loaded:
            return RestoreResult(success=False, error="模型未加载")

        _check_memory()

        # 开始 VRAM 监控 (DiffBIR inspired)
        if self._vram_monitor is not None:
            self._vram_monitor.start_inference()

        tensor_cache = None
        try:
            tensor_cache = get_cache_manager()
            tensor_cache.clear()
        except Exception as e:
            logger.debug(f"TensorCacheManager init skipped: {e}")

        # 临时帧目录引用：创建前为 None，供失败/取消路径统一回收。
        # 长视频帧文件可达数十 GB，任何退出路径泄漏都会写满磁盘
        frames_dir: str | None = None
        # 续跑意图在 except 中也要可判定（inf 可能在赋值前就抛错，如内存守卫），
        # 直接取调用方 kwargs，与 inf["resume_frames"] 同源
        resume_intent = bool(kwargs.get("resume_frames", False))

        try:
            os.makedirs(output_dir, exist_ok=True)
            _check_memory()
            _log_memory("视频推理初始")

            # REFACTOR [E4-1]: 阶段0 检查取消信号
            self._check_cancelled("video:init")

            # 从配置读取推理参数
            inf = self._get_inference_config(**kwargs)

            # 分辨率处理 (对齐图片 SideResize: 短边=resolution, 长边<=max_resolution)
            # request由前端/路由传入，缺失时回退 config.yaml restore 节
            res_h = kwargs.get("res_h", self.config.get("restore", {}).get("default_resolution_h", 0))
            res_w = kwargs.get("res_w", self.config.get("restore", {}).get("default_resolution_w", 0))
            req_resolution = int(kwargs.get("resolution", 0) or 0)
            req_max_resolution = int(kwargs.get("max_resolution", 0) or 0)
            # 若路由传入 inf["max_resolution"] 且无显式 request 则采纳
            if req_max_resolution <= 0:
                req_max_resolution = int(inf.get("max_resolution", 0) or 0)

            seed = inf["seed"]
            if seed == -1:
                seed = random.randint(0, MAX_SEED)
                # MLOps P1-1：随机抽签回写请求级 inf，实际种子就此物化（与图像链路对称）
                inf["seed"] = seed
                logger.info(f"随机种子: {seed}")

            sp_size = kwargs.get("sp_size", self.config.get("restore", {}).get("sp_size", 1))
            cfg_scale = inf["cfg_scale"]
            cfg_rescale = inf["cfg_rescale"]
            sample_steps = inf["sample_steps"]
            color_fix_method = inf["color_correction"]
            input_noise_scale = inf["input_noise_scale"]
            latent_noise_scale = inf["latent_noise_scale"]

            # 官方 VideoUpscaler 参数接入 (batch_size/uniform_batch_size/temporal_overlap/prepend_frames)
            # batch_size: 每批帧数, 必须满足 4n+1 (1,5,9,13,...); 优先作为段大小, 回退 temporal_segment_size
            batch_size = max(0, int(inf.get("batch_size", 0) or 0))
            seg_requested = batch_size if batch_size > 1 else max(0, int(inf.get("temporal_segment_size", 0) or 0))
            # temporal_overlap: 官方批间重叠帧数 (0-16), 优先; 回退 temporal_segment_overlap
            temporal_overlap = max(0, int(inf.get("temporal_overlap", 0) or 0))
            seg_overlap = (
                temporal_overlap if temporal_overlap > 0 else max(0, int(inf.get("temporal_segment_overlap", 8) or 0))
            )
            # uniform_batch_size: 末段补齐到 batch_size, 防止小末段时间伪影
            uniform_batch_size = bool(inf.get("uniform_batch_size", True))
            # prepend_frames: 视频开头反转预填充, 减少起始伪影 (自动移除)
            prepend_frames = max(0, int(inf.get("prepend_frames", 0) or 0))
            # 帧级断点续跑（成本治理 P2）：重试场景复用上一轮已完整写盘的段帧
            resume_frames = bool(inf.get("resume_frames", False))
            # cache_model: 是否缓存 DiT/VAE 模型跨任务复用 (12GB 默认关闭)
            cache_model = bool(inf.get("cache_model", False))

            # 获取视频信息
            video_info = self._ffmpeg.get_video_info(video_path)
            if not video_info:
                return RestoreResult(success=False, error="无法获取视频信息")
            if video_info.width <= 0 or video_info.height <= 0 or video_info.frame_count <= 0:
                return RestoreResult(success=False, error="视频信息无效(无法读取分辨率或帧数)")

            total_frames = video_info.frame_count
            fps = video_info.fps
            out_fps = kwargs.get("out_fps", fps)
            if not out_fps or out_fps <= 0:
                out_fps = 24.0
            src_w, src_h = video_info.width, video_info.height

            # 分辨率计算: 若前端/路由传了 resolution (短边目标) 则按 SideResize 计算，
            # 否则回退 res_h/res_w 或源分辨率
            if req_resolution > 0:
                current_short = min(src_h, src_w)
                scale = req_resolution / current_short
                if req_max_resolution > 0 and max(src_h, src_w) * scale > req_max_resolution:
                    scale = req_max_resolution / max(src_h, src_w)
                res_h = int(src_h * scale)
                res_w = int(src_w * scale)
            if res_h <= 0 or res_w <= 0:
                res_h, res_w = src_h, src_w
            res_h -= res_h % 2
            res_w -= res_w % 2
            if res_h <= 0 or res_w <= 0:
                res_h, res_w = src_h, src_w
            logger.info(f"开始视频修复: {video_path} -> {res_w}x{res_h}, seed={inf['seed']}")
            logger.info(f"视频帧数: {total_frames}, 帧率: {fps}, 源分辨率: {src_w}x{src_h}")
            # 内存估算使用源/目标分辨率的较大者 (原始帧按源分辨率驻留)
            eff_h, eff_w = max(src_h, res_h), max(src_w, res_w)

            # ==================== 内存保护: 全量加载风险评估 ====================
            # 整段视频全量加载的峰值内存估算 (float32), 用于决策与日志
            full_load_gb = total_frames * src_w * src_h * 3 * 4 / (1024**3)
            logger.info(f"内存保护: 若全量加载约需 {full_load_gb:.1f}GB RAM, " f"改用分段流式处理以避免 OOM")

            # ==================== 分段流式: 计算段大小与段边界 ====================
            segment_size = self._choose_segment_size(
                video_info=video_info,
                res_h=eff_h,
                res_w=eff_w,
                requested=seg_requested,
            )
            segment_overlap = seg_overlap

            # 内存保护: 校验单段内存不超过可用内存安全比例
            seg_mem_gb = self._estimate_segment_memory(segment_size, eff_h, eff_w)
            avail_ram_gb = self._available_ram_gb()
            safe_budget_gb = avail_ram_gb * 0.5
            if seg_mem_gb > safe_budget_gb:
                # 逐级缩小段大小 (每级减 4, 保持 VAE 时间对齐)
                while segment_size > 5 and seg_mem_gb > safe_budget_gb:
                    segment_size = max(1, segment_size - 4)
                    seg_mem_gb = self._estimate_segment_memory(segment_size, eff_h, eff_w)
                if seg_mem_gb > safe_budget_gb:
                    return RestoreResult(
                        success=False,
                        error=(
                            f"视频分辨率过高，单段处理需约 {seg_mem_gb:.1f}GB 内存，"
                            f"但可用内存仅 {avail_ram_gb:.1f}GB，无法安全处理"
                        ),
                    )
            logger.info(
                f"分段流式处理: 每段 {segment_size} 帧, 重叠 {segment_overlap} 帧, " f"单段估算内存 {seg_mem_gb:.1f}GB"
            )

            # 段边界计算 (RVRT/DiffVSR inspired)
            temporal_segments = None
            if total_frames > segment_size:
                try:
                    from app.integrated_app.optimization.inference.tile_blend import compute_temporal_segments

                    temporal_segments = compute_temporal_segments(
                        total_frames=total_frames,
                        segment_size=segment_size,
                        overlap=segment_overlap,
                    )
                    logger.info(f"长视频分段: {len(temporal_segments)} 段")
                except Exception as e:
                    logger.debug(f"Temporal segments calculation skipped: {e}")
            if temporal_segments is None or not temporal_segments:
                temporal_segments = [(0, total_frames)]

            # 构建变换 (所有段复用同一流水线)
            video_transform = self._build_video_transform(res_h, res_w)

            # 输出帧目录 (供 ffmpeg 合成)。frames_dir_override（评估 P2-6）为
            # 任务级隔离目录 _frames_<task_id>：段级续跑只在任务自有目录内判定
            # 帧完整性，杜绝共享 _frames 目录的跨任务帧污染
            frames_dir_override = str(inf.get("frames_dir_override") or "")
            frames_dir = frames_dir_override if frames_dir_override else os.path.join(output_dir, "_frames")
            os.makedirs(frames_dir, exist_ok=True)

            # VAE 常驻, 仅在编码/解码阶段切换到 GPU (DiT 采样阶段在 CPU)
            # P0-2: 请求级 tile 覆盖。_vae_encode/_vae_decode 只读
            # _vae_tiled_config，重试缩 tile 必须在这里落到该字典，
            # 否则视频侧永远使用加载期的全局配置
            vae_cfg = self.config.get("model", {}).get("vae", {}) or {}

            def _resolve_tile(name: str, default: int) -> int:
                value = kwargs.get(name)
                if value is None or value == "":
                    return int(default)
                return int(value)

            vae_tiled_config = {
                "encode_tiled": vae_cfg.get("encode_tiled", True),
                "encode_tile_size": _resolve_tile("encode_tile_size", vae_cfg.get("encode_tile_size", 1024)),
                "encode_tile_overlap": _resolve_tile("encode_tile_overlap", vae_cfg.get("encode_tile_overlap", 128)),
                "decode_tiled": vae_cfg.get("decode_tiled", True),
                "decode_tile_size": _resolve_tile("decode_tile_size", vae_cfg.get("decode_tile_size", 1024)),
                "decode_tile_overlap": _resolve_tile("decode_tile_overlap", vae_cfg.get("decode_tile_overlap", 128)),
                "tile_debug": vae_cfg.get("tile_debug", False),
                "offload_device": vae_cfg.get("offload_device", "cpu"),
                "cache_model": vae_cfg.get("cache_model", True),
                "auto_tile_size": vae_cfg.get("auto_tile_size", True),
            }
            if self.vae is None:
                self.vae = self._load_vae_model(
                    model_config=self._model_config,
                    checkpoint_path=self._vae_checkpoint_path,
                    device=self.device,
                    vae_tiled_config=vae_tiled_config,
                    torch_compile_args=inf.get("torch_compile") or None,
                )
            self._vae_tiled_config = vae_tiled_config

            # 后处理配置
            postprocess_cfg = self.config.get("postprocessing", {})
            enable_wavelet = postprocess_cfg.get("wavelet_reconstruction", False)
            sharpen_strength = postprocess_cfg.get("video_sharpen_strength", 0.0)

            # 跨段时间一致性: FeaturePropagation (Upscale-A-Video inspired)
            temporal_propagator = None
            temporal_propagation_enabled = self.config.get("inference", {}).get("temporal_propagation", True)
            # P0-1: 段间重叠权重类型（hann 默认，linear 为历史回退档）
            segment_overlap_weight = str(
                self.config.get("inference", {}).get("segment_overlap_weight", "hann") or "hann"
            )
            if temporal_propagation_enabled:
                try:
                    from app.integrated_app.optimization.inference.temporal_processing import FeaturePropagation

                    prop_weight = postprocess_cfg.get("temporal_propagation_weight", 0.2)
                    temporal_propagator = FeaturePropagation(propagation_weight=prop_weight)
                except Exception as e:
                    logger.debug(f"FeaturePropagation init skipped: {e}")

            # 输出文件名：默认沿用输入视频文件名（只换扩展名）；批量场景传入 output_name 保留原文件名
            if output_name is None:
                output_name = build_output_name(video_path, ".mp4")
            output_path = _resolve_unique_path(output_dir, output_name)

            # ==================== 分段流式主循环 ====================
            import cv2

            cap = None
            total_written = 0
            resumed_segments = 0  # 帧级续跑复用的段数（写入 metadata 供可观测）
            pending_read_pos = 0  # 跳段后下一处理段的顺序读取目标位置（含 prepend 偏移）
            prev_prop_frame = None  # FeaturePropagation 跨段传播 (torch 张量)
            prev_tail_out = None  # 上一段处理后尾帧 (uint8 HWC), 用于重叠混合
            raw_buf: list[torch.Tensor] = []  # 原始帧缓冲, 复用重叠帧避免回退读取
            # 水印失败处置（评估报告 R2）：任务级解析一次策略；失败帧聚合计数，
            # 审计/日志只报首帧避免逐帧刷屏
            watermark_policy = None
            watermark_failed_frames = 0
            watermark_last_error: str | None = None

            try:
                cap = cv2.VideoCapture(video_path)
                if not cap.isOpened():
                    if frames_dir:
                        shutil.rmtree(frames_dir, ignore_errors=True)
                    return RestoreResult(success=False, error="无法打开视频文件")

                for seg_idx, (seg_start, seg_end) in enumerate(temporal_segments):
                    self._check_cancelled(f"video:segment-{seg_idx}")
                    seg_len = seg_end - seg_start
                    logger.info(
                        f"处理段 {seg_idx + 1}/{len(temporal_segments)}: " f"帧 {seg_start}-{seg_end} ({seg_len}帧)"
                    )

                    # ---- 帧级断点续跑判定（成本治理 P2）----
                    # 该段帧已全部完整在盘（上一轮同任务重试前写盘）→ 跳过推理直接复用。
                    # 仅 resume_frames=True 时启用（编排层 OOM 重试路径），全新任务恒不跳，
                    # 防止把其他历史任务的残留帧误当成断点
                    if resume_frames and self._segment_frames_complete(
                        frames_dir, seg_start, seg_end, expected_hw=(res_h, res_w)
                    ):
                        logger.info(
                            f"断点续跑: 段 {seg_idx + 1}/{len(temporal_segments)} 帧 {seg_start}-{seg_end} "
                            f"已在盘, 跳过推理复用现有帧"
                        )
                        total_written += seg_len
                        resumed_segments += 1
                        # 顺序读取位置失配：记录目标位置（cap 从头打开未消费），
                        # 下一处理段前用 grab 精确推进（不解码像素，代价远低于推理）
                        pending_read_pos = seg_end + prepend_frames
                        # 段间混合尾帧延迟到下一处理段从盘上重建
                        prev_tail_out = None
                        if self._progress_callback is not None:
                            try:
                                current = min(seg_end, total_frames)
                                self._progress_callback(
                                    current_frame=current,
                                    total_frames=total_frames,
                                    progress=(current / total_frames * 100.0) if total_frames > 0 else 100.0,
                                )
                            except Exception as e:
                                logger.debug(f"Progress callback 调用失败: {e}")
                        continue

                    # 跳段后重新对齐顺序读取位置：grab 逐帧推进到目标（不做 retrieve/解码转换）
                    if pending_read_pos > 0:
                        target_pos = pending_read_pos
                        while cap.isOpened() and cap.get(cv2.CAP_PROP_POS_FRAMES) < target_pos:
                            if not cap.grab():
                                break
                        raw_buf = []
                        # 从盘上重建段间混合尾帧（与原运行写入的尾帧等价，均为段末未混合帧）
                        if seg_start > 0 and segment_overlap > 0:
                            prev_tail_out = self._rebuild_tail_from_disk(
                                frames_dir, seg_start, min(segment_overlap, seg_start)
                            )
                            if prev_tail_out is None:
                                logger.debug("断点续跑: 尾帧重建失败, 本段首重叠帧将不与上段混合（仅影响衔接质量）")
                        # FeaturePropagation 上下文不重建（仅影响续跑段首帧传播细节）
                        pending_read_pos = 0

                    # ---- 读取本段帧 (顺序读取, 重叠帧由 raw_buf 复用) ----
                    # prepend_frames: 首段前置反转帧作为时序上下文 (官方语义, 输出自动移除)
                    prepend_buf: list[torch.Tensor] = []
                    if seg_idx == 0 and prepend_frames > 0:
                        pre = []
                        for _ in range(prepend_frames):
                            ret, frame = cap.read()
                            if not ret:
                                break
                            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                            pre.append(torch.from_numpy(frame).permute(2, 0, 1).float() / 255.0)
                        if pre:
                            prepend_buf = list(reversed(pre))
                            raw_buf = pre + raw_buf
                            logger.info(f"prepend_frames: 首段前置 {len(prepend_buf)} 帧反转上下文 " f"(输出自动移除)")

                    while len(raw_buf) < seg_len:
                        ret, frame = cap.read()
                        if not ret:
                            break
                        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                        raw_buf.append(torch.from_numpy(frame).permute(2, 0, 1).float() / 255.0)
                    if len(raw_buf) < seg_len:
                        logger.warning(
                            f"段 {seg_idx} 实际读取 {len(raw_buf)} 帧, 少于预期 {seg_len} "
                            f"(视频提前结束或 ffprobe 帧数估算偏差)"
                        )
                        seg_len = len(raw_buf)
                    if seg_len == 0:
                        break

                    # uniform_batch_size: 末段补齐到 segment_size (官方: 统一批次防小末段时间伪影)
                    real_len = seg_len
                    target_len = seg_len
                    if uniform_batch_size and seg_idx == len(temporal_segments) - 1 and 0 < seg_len < segment_size:
                        target_len = segment_size
                        last_frame = raw_buf[-1].clone()
                        while len(raw_buf) < target_len:
                            raw_buf.append(last_frame.clone())
                        logger.info(f"uniform_batch_size: 末段 {real_len} 帧补齐到 {target_len} 帧")

                    video = torch.stack(prepend_buf + raw_buf[:target_len])  # T C H W
                    # 保留本段尾部重叠帧 (独立张量), 供下一段复用
                    keep = min(segment_overlap, target_len)
                    raw_buf = raw_buf[target_len - keep :]
                    _check_memory()
                    _log_memory(f"段{seg_idx}读取后")

                    # ---- 阶段1: VAE 编码 (VAE=GPU, DiT=CPU/驻留) ----
                    self._check_cancelled(f"video:segment-{seg_idx}:vae-encode")
                    self.vae.to(device=self.device)
                    cond_latent = video_transform(video.to(self.device))
                    ori_length = cond_latent.shape[1]
                    input_video = cond_latent.clone()
                    cond_latent = self._cut_videos(cond_latent, sp_size)
                    cond_latents = self._vae_encode([cond_latent])
                    del cond_latent, video
                    self.vae.to(device="cpu")
                    self.vae.zero_grad(set_to_none=True)
                    clear_memory(deep=False, force=True)

                    # ---- 阶段2: DiT 采样 (DiT=GPU/BlockSwap, VAE=CPU) ----
                    self._check_cancelled(f"video:segment-{seg_idx}:dit-sample")
                    model_cfg = self.config.get("model", {})
                    force_reload_dit = bool(inf.get("force_reload_dit", False))
                    # P1-2: 加载签名守卫——缓存的 DiT 与当前加载参数不一致时自动重载
                    if self.dit is not None and not force_reload_dit:
                        request_signature = build_dit_load_signature(
                            checkpoint_path=self._dit_checkpoint_path,
                            precision=self._dit_precision,
                            blocks_to_swap=inf.get("blocks_to_swap", model_cfg.get("blocks_to_swap", 0)),
                            swap_io_components=inf.get(
                                "swap_io_components", model_cfg.get("swap_io_components", False)
                            ),
                            offload_device=inf.get("offload_device", model_cfg.get("offload_device", "cpu")),
                            attention_mode=inf.get("attention_mode", model_cfg.get("attention_mode", "sdpa")),
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
                        logger.info(
                            "DiT 模型按需加载..." if not force_reload_dit else "DiT 模型强制重载（force_reload_dit）..."
                        )
                        # 内存保护: DiT 加载峰值约需 12GB RAM (6.32GB fp16 state_dict + 转换),
                        # 与图片管线一致, 加载前先释放 VAE 腾出内存, 加载后恢复
                        vae_was_loaded = self.vae is not None
                        if vae_was_loaded:
                            logger.info("DiT 加载前释放 VAE 腾出内存...")
                            self._destroy_vae()
                            gc.collect()
                            _force_release_memory()
                        try:
                            self.dit = self._load_dit_model(
                                model_size=self._dit_model_size,
                                model_config=self._model_config,
                                checkpoint_path=self._dit_checkpoint_path,
                                precision=self._dit_precision,
                                device=self.device,
                                blocks_to_swap=inf.get("blocks_to_swap", model_cfg.get("blocks_to_swap", 0)),
                                swap_io_components=inf.get(
                                    "swap_io_components", model_cfg.get("swap_io_components", False)
                                ),
                                offload_device=inf.get("offload_device", model_cfg.get("offload_device", "cpu")),
                                attention_mode=inf.get("attention_mode", model_cfg.get("attention_mode", "sdpa")),
                                torch_compile_args=inf.get("torch_compile") or None,
                            )
                        finally:
                            # 恢复 VAE (阶段3 解码需要), 无论 DiT 加载成败都恢复
                            if vae_was_loaded and self.vae is None:
                                try:
                                    logger.info("DiT 加载完成, 恢复 VAE...")
                                    self.vae = self._load_vae_model(
                                        model_config=self._model_config,
                                        checkpoint_path=self._vae_checkpoint_path,
                                        device=self.device,
                                        torch_compile_args=inf.get("torch_compile") or None,
                                    )
                                    self.vae.to(device="cpu")
                                except Exception as e:
                                    logger.warning(f"DiT 加载后恢复 VAE 失败: {e}")
                    text_embeds = self._get_text_embeds()
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
                    del cond_latents, text_embeds
                    clear_memory(deep=False, force=True)

                    # ---- 阶段3: VAE 解码 (VAE=GPU, DiT 保持驻留) ----
                    self._check_cancelled(f"video:segment-{seg_idx}:vae-decode")
                    self.vae.to(device=self.device)
                    decoded = self._vae_decode(samples)
                    del samples
                    self.vae.to(device="cpu")
                    clear_memory(deep=False, force=True)

                    # ---- 阶段4: 后处理 (无模型) ----
                    self._check_cancelled(f"video:segment-{seg_idx}:postprocess")
                    sample = decoded[0]
                    # C T H W -> T C H W
                    if sample.ndim == 3:
                        sample = rearrange(sample[:, None], "c t h w -> t c h w")
                    else:
                        sample = rearrange(sample, "c t h w -> t c h w")
                    # 截断到编码长度, 再移除 prepend 反转帧, 最后截断到本段真实帧数
                    if ori_length < sample.shape[0]:
                        sample = sample[:ori_length]
                    prepend_n = len(prepend_buf)
                    if prepend_n > 0 and sample.shape[0] > prepend_n:
                        sample = sample[prepend_n:]
                    if real_len < sample.shape[0]:
                        sample = sample[:real_len]
                    del decoded

                    input_frames = (
                        rearrange(input_video, "c t h w -> t c h w")
                        if input_video.ndim == 4
                        else rearrange(input_video[:, None], "c t h w -> t c h w")
                    )
                    # 输入帧与输出对齐: 跳过 prepend 帧
                    if prepend_n > 0 and input_frames.shape[0] > prepend_n:
                        input_frames = input_frames[prepend_n:]
                    input_frames_cpu = input_frames[: sample.shape[0]].cpu()
                    del input_video, input_frames

                    sample_np = _tensor_to_uint8_np(sample)
                    input_np = _tensor_to_uint8_np(input_frames_cpu)
                    del sample, input_frames_cpu

                    # 后处理增强 (段内使用, 视频可选)
                    from app.integrated_app.optimization.inference.post_processing import (
                        apply_sharpening,
                        wavelet_reconstruction,
                    )

                    restored_frames = []
                    for i in range(sample_np.shape[0]):
                        frame = sample_np[i].transpose(1, 2, 0)  # C H W -> H W C
                        ref = input_np[i].transpose(1, 2, 0)
                        if color_fix_method != "none":
                            frame = apply_color_correction(frame, ref, method=color_fix_method)
                        # 小波重建后处理 (视频可选, 默认关闭以节省时间)
                        if enable_wavelet:
                            try:
                                level = postprocess_cfg.get("wavelet_level", 2)
                                low_freq_weight = postprocess_cfg.get("low_freq_weight", 0.8)
                                frame = wavelet_reconstruction(frame, ref, level=level, low_freq_weight=low_freq_weight)
                            except Exception as e:
                                logger.debug(f"Video wavelet_reconstruction skipped: {e}")
                        # 视频锐化
                        if sharpen_strength > 0:
                            try:
                                frame = apply_sharpening(frame, strength=sharpen_strength, method="unsharp_mask")
                            except Exception as e:
                                logger.debug(f"Video sharpening skipped: {e}")
                        # 跨段时间一致性: 上一段末帧作为本段首帧的前帧
                        # FeaturePropagation 仅接受 torch 张量, 需临时转换 (numpy -> torch -> numpy)
                        if temporal_propagator is not None:
                            frame_t = torch.from_numpy(frame).float()
                            propagated = temporal_propagator.propagate(
                                current_frame=frame_t,
                                previous_frame=prev_prop_frame,
                            )
                            frame = propagated.clamp(0, 255).round().to(torch.uint8).numpy()
                            prev_prop_frame = propagated
                        restored_frames.append(frame)
                    del sample_np, input_np

                    # ---- 写盘 (含段间重叠混合) ----
                    # 水印强制启用（无关闭开关），失败处置策略按任务解析一次
                    if watermark_policy is None:
                        from app.integrated_app.services.watermark_policy import (
                            report_missing_watermark_key,
                            resolve_watermark_failure_policy,
                        )

                        watermark_policy = resolve_watermark_failure_policy(self.config)
                        report_missing_watermark_key()
                    overlap_n = min(segment_overlap, len(restored_frames))
                    # P0-1: 段间重叠权重抽成纯函数（默认 Hann+平台，config 可回退 linear），
                    # 替代历史整段线性斜坡 ((i+1)/(n+1))，减少段边界呼吸/重影
                    from app.integrated_app.optimization.inference.tile_blend import overlap_weights

                    seg_weights = overlap_weights(overlap_n, segment_overlap_weight)
                    for i, frame in enumerate(restored_frames):
                        if prev_tail_out is not None and i < len(seg_weights):
                            prev = prev_tail_out[i]
                            weight = seg_weights[i]
                            frame = (
                                frame.astype(np.float32) * weight + prev.astype(np.float32) * (1.0 - weight)
                            ).astype(np.uint8)
                        # 嵌入不可感知数字水印 (视频帧)
                        from app.integrated_app.security.watermark import _VIDEO_ALPHA, _VIDEO_REPEAT
                        from app.integrated_app.services.watermark_policy import embed_with_retry

                        # P3-1：逐帧水印绑定 task_id（视频按帧嵌入，payload 同源）
                        # 视频帧走鲁棒档（三通道等幅 + 步长 20 + 重复码 3）：
                        # 经 ffmpeg 有损编码后签名验证可存活（2026-09-06 实验）
                        frame, embedded, wm_error = embed_with_retry(
                            frame, payload=watermark_payload, alpha=_VIDEO_ALPHA, repeat=_VIDEO_REPEAT
                        )
                        if not embedded:
                            watermark_failed_frames += 1
                            watermark_last_error = wm_error
                            if watermark_failed_frames == 1:
                                from app.integrated_app.services.watermark_policy import (
                                    handle_watermark_failure,
                                )

                                handle_watermark_failure(
                                    policy=watermark_policy or "mark_metadata",
                                    error=wm_error or "unknown",
                                    payload=watermark_payload,
                                )
                        cv2.imwrite(
                            os.path.join(frames_dir, f"frame_{seg_start + i:06d}.png"),
                            cv2.cvtColor(frame, cv2.COLOR_RGB2BGR),
                        )
                        total_written += 1

                    # 保留本段处理后尾帧, 供下一段混合
                    if overlap_n > 0:
                        prev_tail_out = np.array(restored_frames[-overlap_n:])
                    del restored_frames
                    gc.collect()

                    # 段间内存检查
                    _check_memory()
                    _log_memory(f"段{seg_idx}处理完成")

                    # 进度上报 (供 SSE/任务状态更新)
                    if self._progress_callback is not None:
                        try:
                            current = min(seg_end, total_frames)
                            self._progress_callback(
                                current_frame=current,
                                total_frames=total_frames,
                                progress=(current / total_frames * 100.0) if total_frames > 0 else 100.0,
                            )
                        except Exception as e:
                            logger.debug(f"Progress callback 调用失败: {e}")

            finally:
                if cap is not None:
                    cap.release()
                # 统一释放 DiT/VAE, 归还内存 (成功/失败/取消/打开失败路径均执行)
                # cache_model=True 时保留模型供后续任务复用 (跨任务缓存)
                if not cache_model:
                    try:
                        self._destroy_dit()
                    except Exception as e:
                        logger.debug(f"清理 DiT 时出错: {e}")
                    try:
                        self._destroy_vae()
                    except Exception as e:
                        logger.debug(f"清理 VAE 时出错: {e}")
                    clear_memory(deep=False, force=True)
                else:
                    logger.info("cache_model=True: 保留 DiT/VAE 模型供跨任务复用")

            if total_written == 0:
                if frames_dir:
                    shutil.rmtree(frames_dir, ignore_errors=True)
                return RestoreResult(success=False, error="视频处理失败: 未能读取任何帧")

            # 帧文件连续性校验: 存在编号缺口时重排 (ffprobe 帧数估算偏差安全网)
            written_names = sorted(f for f in os.listdir(frames_dir) if f.startswith("frame_") and f.endswith(".png"))
            expected_names = [f"frame_{i:06d}.png" for i in range(len(written_names))]
            if written_names != expected_names:
                logger.warning(f"帧编号不连续({len(written_names)} 帧), 重新编号以便 ffmpeg 合成")
                for new_idx, name in enumerate(written_names):
                    os.rename(
                        os.path.join(frames_dir, name),
                        os.path.join(frames_dir, f"frame_{new_idx:06d}.png"),
                    )
                written_names = expected_names
            output_frames = len(written_names)

            # ==================== ffmpeg 合成视频 + 音轨 ====================
            self._check_cancelled("video:compose")
            # 数据治理 P2-5：生成参数 + AI 标识写入容器 comment（与图像元数据同源）
            try:
                from app.integrated_app.utils.output_metadata import generation_params_payload

                compose_comment = generation_params_payload(dict(inf))[:4000]
            except Exception:
                compose_comment = ""
            composed_ok = self._ffmpeg.compose_video(
                frames_dir=frames_dir,
                output_path=output_path,
                fps=float(out_fps),
                source_video=video_path,
                include_audio=True,
                comment=compose_comment or None,
            )
            if not composed_ok:
                logger.error(f"视频合成失败: {output_path}")
                if frames_dir:
                    shutil.rmtree(frames_dir, ignore_errors=True)
                # DX P2-8：报错给出可操作修复路径，而不是一句话让用户无从下手
                ffmpeg_hint = (
                    "ffmpeg 视频合成失败：请确认 FFmpeg 已安装（命令行执行 ffmpeg -version 应成功）。"
                    "未安装时从 https://www.gyan.dev/ffmpeg/builds/ 下载 release-full 并加入 PATH，"
                    "或将 ffmpeg.exe/ffprobe.exe 放到项目 app/ 目录（见 NOTICE 第 4 条）；详情见服务端日志。"
                )
                return RestoreResult(success=False, error=ffmpeg_hint)

            logger.info(f"视频修复完成: {output_path} ({output_frames} 帧)")

            # 合成后抽样验证（评估报告 R2 跟进）：显式检查编码是否吃掉水印。
            # 阈值 50%：鲁棒档方案实测存活率 100%（CRF14/18/23），低通过率
            # 只会出现在编码参数异常/容量降档等真实缺失场景
            mux_verify_passed: int | None = None
            mux_verify_sampled: int | None = None
            if not watermark_failed_frames and os.path.exists(output_path):
                mux_verify_passed = 0
                mux_verify_sampled = 0
                try:
                    from app.integrated_app.security.watermark import verify_watermark

                    cap = cv2.VideoCapture(output_path)
                    total_frames_out = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or output_frames
                    try:
                        for k in range(4):
                            pos = int(total_frames_out * (k + 0.5) / 4)
                            cap.set(cv2.CAP_PROP_POS_FRAMES, min(pos, total_frames_out - 1))
                            ok, frame = cap.read()
                            if not ok:
                                break
                            mux_verify_sampled += 1
                            if verify_watermark(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)):
                                mux_verify_passed += 1
                    finally:
                        cap.release()
                    if mux_verify_sampled:
                        logger.debug(f"合成后水印抽样验证: {mux_verify_passed}/{mux_verify_sampled} 帧携带可信水印")
                except Exception as e:
                    logger.debug(f"合成后水印抽样验证跳过: {e}")
                    mux_verify_passed = mux_verify_sampled = None

            # 水印缺失统一处置（评估报告 R2）：① 嵌入失败帧 > 0；② 合成后抽样验证通过率 < 50%
            # mark_metadata 写溯源侧车；block 删除产物并抛错；ignore 仅日志
            mux_lost = (
                mux_verify_passed is not None and mux_verify_sampled and mux_verify_passed * 2 < mux_verify_sampled
            )
            if watermark_failed_frames or mux_lost:
                from app.integrated_app.services.watermark_policy import handle_watermark_loss

                reason = (
                    f"{watermark_failed_frames} 帧水印嵌入失败（末次: {watermark_last_error}）"
                    if watermark_failed_frames
                    else f"合成后抽样验证仅 {mux_verify_passed}/{mux_verify_sampled} 帧携带可信水印"
                )
                handle_watermark_loss(
                    policy=watermark_policy or "mark_metadata",
                    output_path=output_path,
                    error=reason,
                    payload=watermark_payload,
                )

            # 清理临时帧目录, 释放磁盘空间 (长视频帧文件可达数十 GB)
            try:
                shutil.rmtree(frames_dir, ignore_errors=True)
            except Exception as e:
                logger.debug(f"临时帧目录清理跳过: {e}")

            # Tensor Cache: 清理缓存
            if tensor_cache is not None:
                tensor_cache.clear()
                cache_stats = tensor_cache.get_stats()
                logger.info(
                    f"Tensor Cache 统计: cached={cache_stats['total_cached']}, "
                    f"restored={cache_stats['total_restored']}, "
                    f"peak={cache_stats['peak_cache_mb']:.1f}MB"
                )

            # VRAM 监控: 结束并输出报告（峰值随 metadata 落库，P2-1）
            vram_peak_mb = 0.0
            # P3-2：分阶段耗时与 DiT 采样吞吐 it/s（口径见 docs/METRICS_SPEC.md）
            stage_durations_ms: dict[str, float] = {}
            dit_seconds = 0.0
            steps_per_second = 0.0
            if self._vram_monitor is not None:
                self._vram_monitor.end_inference()
                self._vram_monitor.log_report()
                try:
                    report = self._vram_monitor.get_report()
                    vram_peak_mb = float(report.get("global_peak_allocated_mb", 0.0))
                    for stage in report.get("stages", []) or []:
                        name = stage.get("stage")
                        if name:
                            stage_durations_ms[str(name)] = float(stage.get("duration_ms", 0.0) or 0.0)
                    dit_seconds = stage_durations_ms.get("dit_sample", 0.0) / 1000.0
                    if dit_seconds > 0:
                        steps_per_second = round(float(sample_steps) / dit_seconds, 3)
                except Exception as e:
                    logger.debug(f"VRAM 监控报告生成失败: {e}")
                self._vram_monitor = None

            _cleanup_cuda_cache(deep=True)

            processing_time = time.time() - start_time
            return RestoreResult(
                success=True,
                output_path=output_path,
                processing_time=processing_time,
                metadata={
                    "model_size": self.model_size,
                    "precision": self.precision,
                    "input_frames": total_frames,
                    "output_frames": output_frames,
                    "output_resolution": f"{res_w}x{res_h}",
                    "fps": out_fps,
                    "segment_size": segment_size,
                    "segment_overlap": segment_overlap,
                    "num_segments": len(temporal_segments),
                    "resumed_segments": resumed_segments,
                    "batch_size": batch_size,
                    "uniform_batch_size": uniform_batch_size,
                    "temporal_overlap": temporal_overlap,
                    "prepend_frames": prepend_frames,
                    "cache_model": cache_model,
                    "blockswap_active": self._blockswap_active,
                    "vram_peak_mb": vram_peak_mb,
                    "vae_decode_oom_fallback": (
                        self.get_vae_decode_oom_fallback()
                        if hasattr(self, "get_vae_decode_oom_fallback")
                        else None
                    ),
                    # MLOps P1-1：实际使用的种子（-1 已物化为抽签值），供服务层回写 history parameters
                    "seed_effective": seed,
                    "processing_fps": output_frames / processing_time if processing_time > 0 else 0,
                    "avg_frame_time_ms": (processing_time / output_frames * 1000) if output_frames > 0 else 0,
                    # P3-2：it/s 仅以 DiT 采样耗时为分母；processing_fps 为端到端口径，二者不可混用
                    "dit_seconds": round(dit_seconds, 3),
                    "steps_per_second": steps_per_second,
                    "stage_durations_ms": stage_durations_ms,
                    "cfg_scale": cfg_scale,
                    "sample_steps": sample_steps,
                    "inference_mode": inf["inference_mode"],
                },
            )

        except InferenceCancelledError as e:
            logger.warning(f"视频推理被取消: {e}")
            self._cleanup_after_error()
            if frames_dir:
                # 取消即放弃：即便 resume_frames 也清理（取消的任务不会进入崩溃恢复）
                shutil.rmtree(frames_dir, ignore_errors=True)
            return RestoreResult(
                success=False,
                error="推理已被取消",
                processing_time=time.time() - start_time,
                metadata={"cancelled": True, "stage": e.detail.get("stage", "")},
            )
        except Exception as e:
            logger.error(f"视频修复失败: {e}", exc_info=True)
            self._cleanup_after_error()
            if frames_dir and not resume_intent:
                # 评估 P2-6：resume_frames=True 表示调用方将重试/崩溃恢复续跑，
                # 失败后必须保留段帧，否则续跑退化为整体重跑。残留目录由
                # outputs 保留策略（年龄/水位清理）兜底回收
                shutil.rmtree(frames_dir, ignore_errors=True)
            return RestoreResult(success=False, error=str(e), processing_time=time.time() - start_time)

    # ------------------------------------------------------------------
    # 分段流式辅助
    # ------------------------------------------------------------------

    @staticmethod
    def _align_segment_size(n: int) -> int:
        """将段帧数对齐到 VAE 时间对齐倍数 (4n+1)

        帧数满足 (T-1) % 4 == 0 时 VAE 时间下采样无需填充，
        避免 _cut_videos 的额外填充浪费计算。
        """
        if n <= 1:
            return 1
        return ((n - 1) // TEMPORAL_ALIGN_MULTIPLE) * TEMPORAL_ALIGN_MULTIPLE + 1

    def _available_ram_gb(self) -> float:
        """获取当前可用系统内存 (GB)

        Returns:
            可用内存大小 (GB)；无 psutil 时保守返回 16.0
        """
        try:
            from app.integrated_app.engines._memory_utils import _get_system_memory

            mem = _get_system_memory()
            return mem.available / (1024**3)
        except Exception:
            return 16.0

    def _estimate_segment_memory(self, segment_frames: int, res_h: int, res_w: int) -> float:
        """估算单段处理的内存峰值 (GB)

        驻留项: 原始帧堆叠(float32) + 变换输入(float32) + 尾帧缓冲，
        按 2.2x 系数保守估算。

        Args:
            segment_frames: 段帧数
            res_h: 目标高度
            res_w: 目标宽度

        Returns:
            单段内存估算值 (GB)
        """
        per_frame = max(1, int(res_h) * int(res_w) * 3 * 4)
        return segment_frames * per_frame * 2.2 / (1024**3)

    def _choose_segment_size(self, video_info, res_h: int, res_w: int, requested: int) -> int:
        """选择安全的分段帧数

        优先使用配置的 requested 段大小，否则默认 25 帧；
        并按可用系统内存自动下调，保证单段驻留内存安全。
        返回帧数满足 VAE 时间对齐 (4n+1)。

        Args:
            video_info: 视频信息 (VideoInfo)
            res_h: 目标高度
            res_w: 目标宽度
            requested: 配置请求的段大小，<=1 时使用默认值

        Returns:
            对齐后的段帧数
        """
        if (res_h <= 0 or res_w <= 0) and video_info is not None:
            res_h, res_w = video_info.height, video_info.width
        target = 25
        if requested and requested > 1:
            target = requested
        per_frame = max(1, int(res_h) * int(res_w) * 3 * 4)
        avail_bytes = self._available_ram_gb() * (1024**3)
        budget = min(avail_bytes * 0.3, 4 * (1024**3))
        max_by_ram = max(1, int(budget / per_frame))
        seg = min(target, max_by_ram)
        return self._align_segment_size(max(1, seg))

    def _build_video_transform(self, res_h: int, res_w: int) -> Compose:
        """构建视频/图像预处理变换流水线

        创建与官方 ComfyUI 工作流一致的预处理变换序列，按顺序执行:
        1. _NaResize: 按短边缩放到目标分辨率（area 插值，保持长宽比）
        2. Clamp: 将像素值裁剪到 [0, 1] 范围
        3. _DivisibleCrop: 裁剪到 tile_size 整数倍，避免 VAE 分块边界问题
        4. Normalize: 标准化到 [-1, 1]（均值 0.5，标准差 0.5）
        5. _RearrangeTCHW2CTHW: 将 T C H W 重排为 C T H W（适配模型输入格式）

        Args:
            res_h: 目标高度
            res_w: 目标宽度

        Returns:
            Compose: torchvision Compose 变换对象
        """
        return Compose(
            [
                _NaResize(
                    resolution=(res_h * res_w) ** 0.5,
                    mode="area",
                    downsample_only=False,
                ),
                Lambda(lambda x: torch.clamp(x, 0.0, 1.0)),
                _DivisibleCrop((TILE_ALIGNMENT_FACTOR, TILE_ALIGNMENT_FACTOR)),
                Normalize(0.5, 0.5),
                _RearrangeTCHW2CTHW(),
            ]
        )

    @staticmethod
    def _cut_videos(videos: torch.Tensor, sp_size: int) -> torch.Tensor:
        """视频帧数对齐填充

        将视频帧数填充到 TEMPORAL_ALIGN_MULTIPLE * sp_size 的整数倍，
        确保 VAE 时间下采样时不会出错。使用最后一帧作为填充内容。

        Args:
            videos: 视频张量，形状 B C T H W
            sp_size: 空间分块大小（影响时间对齐粒度）

        Returns:
            torch.Tensor: 填充后的视频张量，帧数已对齐
        """
        t = videos.size(1)
        align_frames = TEMPORAL_ALIGN_MULTIPLE * sp_size
        if t == 1:
            return videos
        if t <= align_frames:
            padding = [videos[:, -1].unsqueeze(1)] * (align_frames - t + 1)
            padding = torch.cat(padding, dim=1)
            videos = torch.cat([videos, padding], dim=1)
            return videos
        if (t - 1) % align_frames == 0:
            return videos
        else:
            padding = [videos[:, -1].unsqueeze(1)] * (align_frames - ((t - 1) % align_frames))
            padding = torch.cat(padding, dim=1)
            videos = torch.cat([videos, padding], dim=1)
            assert (videos.size(1) - 1) % align_frames == 0
            return videos
