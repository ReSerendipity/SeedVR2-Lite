#!/usr/bin/env python3
"""
SeedVR2 - 配置数据模型定义模块

所属项目：SeedVR2 (AI-powered video & image super-resolution toolkit)
核心功能：
    - 使用 Pydantic BaseModel 定义完整的应用配置层次结构
    - 自动类型转换与强制（ConfigDict 配置）
    - 字段范围校验（field_validator 装饰器）
    - 未知字段自动过滤（extra="ignore"）
    - 默认值管理与嵌套模型组合
    - 请求参数模型（图像/视频修复参数）定义

核心技术栈：
    - Pydantic 2.x 用于数据验证和模型定义
    - Field 用于字段默认值、范围约束和元数据
    - field_validator 用于自定义字段校验逻辑
    - ConfigDict 配置模型行为（如忽略未知字段）

配置模型层次结构：
    AppConfig (根)
    ├── ServerConfig          # HTTP 服务器配置
    ├── ModelConfig           # 模型加载配置
    │   └── ModelEntryConfig  # 单个模型条目配置
    ├── RestoreConfig         # 修复算法参数
    ├── GpuConfig             # GPU 后端配置
    ├── HistoryConfig         # 历史记录数据库配置
    ├── I18nConfig            # 国际化配置
    ├── LoggingConfig         # 日志配置
    ├── CacheConfig           # 文件缓存配置
    ├── InferenceConfig       # 推理优化配置
    ├── RuntimeConfig         # 运行时参数（替代硬编码）
    │   ├── RuntimeSseConfig       # SSE 推送配置
    │   ├── RuntimeBatchConfig     # 批量任务配置
    │   ├── RuntimeTaskConfig      # 任务队列配置
    │   ├── RuntimeUploadConfig    # 上传配置
    │   └── RuntimeSecurityConfig  # 安全配置
    └── user_preferences      # 用户偏好设置（前端管理）
"""

import os
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ServerConfig(BaseModel):
    """HTTP 服务器配置模型。

    定义 FastAPI/Uvicorn 服务器监听地址、端口、调试模式等参数。

    Attributes:
        host: 监听地址，默认仅本地访问（127.0.0.1）。
        port: 监听端口，默认 7870，必须在 1-65535 范围内。
        debug: 是否启用调试模式（热重载、详细错误）。
        auto_open_browser: 启动后是否自动打开浏览器访问应用。
        allowed_origins: CORS 允许的源列表，用于跨域请求控制。
    """

    model_config = ConfigDict(extra="ignore")
    host: str = "127.0.0.1"
    port: int = 7870
    debug: bool = False
    auto_open_browser: bool = True
    allowed_origins: list[str] = Field(default_factory=lambda: ["http://127.0.0.1:7870", "http://localhost:7870"])

    @field_validator("host")
    @classmethod
    def host_must_be_loopback(cls, v: str) -> str:
        """安全强制：host 只允许回环地址，禁止 0.0.0.0 公网暴露。"""
        allowed = {"127.0.0.1", "localhost", "::1"}
        if v not in allowed:
            raise ValueError(f"host must be loopback (127.0.0.1 / localhost / ::1), got: {v}")
        return v

    @field_validator("port")
    @classmethod
    def validate_port(cls, v: int) -> int:
        """验证端口号是否在合法范围内。

        Args:
            v: 待验证的端口号。

        Returns:
            int: 验证通过的端口号。

        Raises:
            ValueError: 端口号不在 1-65535 范围内时抛出。
        """
        if not (1 <= v <= 65535):
            raise ValueError(f"port 必须在 1-65535 范围内，当前值: {v}")
        return v


class ModelEntryConfig(BaseModel):
    """单个模型条目配置模型。

    定义一个可加载模型的完整路径和资源需求信息，
    用于模型管理器根据可用显存自动选择合适的模型版本。

    Attributes:
        name: 模型显示名称。
        config_dir: 模型配置文件目录路径。
        checkpoint_fp16: FP16 精度检查点文件路径。
        checkpoint_fp8: FP8 精度检查点文件路径（显存需求更低）。
        checkpoint_int8_convrot: INT8 ConvRot 量化检查点（Comfy-Org 源，加载期反量化）。
        checkpoint_mxfp8: MXFP8 量化检查点（Comfy-Org 源，加载期反量化）。
        checkpoint_nvfp4: NVFP4 量化检查点（Comfy-Org 源，加载期反量化）。
        vae_checkpoint: VAE 模型检查点路径。
        pos_emb: 正面提示词嵌入路径。
        neg_emb: 负面提示词嵌入路径。
        min_vram_fp16_gb: 加载 FP16 版本所需最小显存（GB，含推理开销的最低运行门槛）。
        min_vram_fp8_gb: 加载 FP8 版本所需最小显存（GB）。
        min_vram_int8_convrot_gb: 加载 INT8 ConvRot 版本所需最小显存（GB）。
        min_vram_mxfp8_gb: 加载 MXFP8 版本所需最小显存（GB）。
        min_vram_nvfp4_gb: 加载 NVFP4 版本所需最小显存（GB）。
        baseline_vram_fp16_gb: FP16 权重显存基线（GB，加载预检用；0 表示未配置，
            gpu_utils 回退内置默认值）。
        baseline_vram_fp8_gb: FP8 权重显存基线（GB，0 表示未配置）。
        num_blocks: Transformer 块数量，用于 BlockSwap 策略。
        sha256_fp16: FP16 检查点期望 SHA256（空表示跳过完整性校验）。
        sha256_fp16_alt: FP16 检查点**等价命名**（Comfy-Org 转包版）的期望 SHA256。
            同一权重 numz 版（``seedvr2_ema_*``）与 Comfy-Org 版（``seedvr2_*``）
            字节不同 → 哈希不同，加载时命中任一即通过。
        sha256_fp8: FP8 检查点期望 SHA256。
        sha256_fp8_alt: FP8 检查点等价命名（Comfy-Org 版）的期望 SHA256。
        sha256_int8_convrot: INT8 ConvRot 检查点期望 SHA256。
        sha256_int8_convrot_alt: INT8 ConvRot 检查点等价命名的期望 SHA256。
        sha256_mxfp8: MXFP8 检查点期望 SHA256。
        sha256_mxfp8_alt: MXFP8 检查点等价命名的期望 SHA256。
        sha256_nvfp4: NVFP4 检查点期望 SHA256。
        sha256_nvfp4_alt: NVFP4 检查点等价命名的期望 SHA256。
        sha256_vae: VAE 检查点期望 SHA256。
        sha256_pos_emb: 正面嵌入期望 SHA256。
        sha256_neg_emb: 负面嵌入期望 SHA256。
    """

    model_config = ConfigDict(extra="ignore")
    name: str = ""
    config_dir: str = ""
    checkpoint_fp16: str = ""
    checkpoint_fp8: str = ""
    checkpoint_int8_convrot: str = ""
    checkpoint_mxfp8: str = ""
    checkpoint_nvfp4: str = ""
    vae_checkpoint: str = ""
    pos_emb: str = ""
    neg_emb: str = ""
    min_vram_fp16_gb: int = 16
    min_vram_fp8_gb: int = 8
    # 量化精度为加载期反量化，权重驻留≈fp16，门槛与同档 fp16 一致（默认取 3b 档值）
    min_vram_int8_convrot_gb: int = 16
    min_vram_mxfp8_gb: int = 16
    min_vram_nvfp4_gb: int = 16
    baseline_vram_fp16_gb: float = 0
    baseline_vram_fp8_gb: float = 0
    num_blocks: int = 36
    sha256_fp16: str = ""
    sha256_fp16_alt: str = ""
    sha256_fp8: str = ""
    sha256_fp8_alt: str = ""
    sha256_int8_convrot: str = ""
    sha256_int8_convrot_alt: str = ""
    sha256_mxfp8: str = ""
    sha256_mxfp8_alt: str = ""
    sha256_nvfp4: str = ""
    sha256_nvfp4_alt: str = ""
    sha256_vae: str = ""
    sha256_pos_emb: str = ""
    sha256_neg_emb: str = ""


class ModelConfig(BaseModel):
    """模型加载与管理配置模型。

    定义默认模型大小、精度、预训练目录、自动加载等参数，
    以及所有可用模型的配置字典。

    支持两种模型源模式（借鉴 Image_MultiModel）:
    - portable: 模型文件存储在项目内的 pretrained_dir 目录中（默认模式，完全自包含）
    - shared: 模型文件存储在外部共享目录 shared_models_root 中，
      多个项目（SeedVR2 / TTS / Image）可共用同一套模型文件，节省磁盘空间

    Attributes:
        default_size: 默认模型大小标识（如 "3b" 表示 30 亿参数）。
        default_precision: 默认推理精度，"fp16"/"fp8"/"mxfp8"/"int8_convrot"/"nvfp4"。
        pretrained_dir: 预训练模型文件根目录（portable 模式下使用）。
        model_source_mode: 模型源模式，"portable"（项目内自包含）或 "shared"（外部共享目录）。
        shared_models_root: shared 模式下的外部共享模型目录绝对路径，为空时回退到 portable 模式。
        auto_load: 应用启动时是否自动加载默认模型。
        device: 模型加载设备，"auto" 自动选择，或指定 "cuda:0"。
        idle_unload_minutes: 模型空闲自动卸载阈值（分钟），0-1440；0 表示禁用（P1-2）。
        models: 模型名称到 ModelEntryConfig 的映射字典。
    """

    model_config = ConfigDict(extra="ignore")
    default_size: str = "3b"
    default_precision: str = "fp16"
    pretrained_dir: str = "model"
    model_source_mode: Literal["shared", "portable"] = "portable"
    shared_models_root: str = ""
    auto_load: bool = True
    device: str = "auto"
    idle_unload_minutes: int = Field(15, ge=0, le=1440)
    models: dict[str, ModelEntryConfig] = Field(default_factory=dict)


class RestoreConfig(BaseModel):
    """视频/图像修复算法默认参数配置模型。

    定义修复任务的默认分辨率、缩放因子、时序一致性等参数，
    视频修复时统一从此读取而非前端表单控制。

    Attributes:
        default_resolution_h: 默认输出高度（像素）。
        default_resolution_w: 默认输出宽度（像素）。
        default_scale_factor: 默认放大倍数，1.0-4.0 范围内。
        temporal_consistency: 时序一致性权重，0-1 之间，值越高帧间越稳定但可能模糊。
        detail_enhancement: 细节增强模式，如 "cinematic"（电影质感）。
        seed: 默认随机种子，用于可复现结果。
        sp_size: 时空处理块大小。
    """

    model_config = ConfigDict(extra="ignore")
    default_resolution_h: int = 1080
    default_resolution_w: int = 1920
    default_scale_factor: float = 2.0
    temporal_consistency: float = 0.8
    detail_enhancement: str = "cinematic"
    seed: int = 42
    sp_size: int = 1

    @field_validator("default_scale_factor")
    @classmethod
    def validate_scale_factor(cls, v: float) -> float:
        """验证缩放因子是否在合法范围内。

        Args:
            v: 待验证的缩放因子。

        Returns:
            float: 验证通过的缩放因子。

        Raises:
            ValueError: 缩放因子不在 1.0-4.0 范围内时抛出。
        """
        if not (1.0 <= v <= 4.0):
            raise ValueError(f"default_scale_factor 必须在 1.0-4.0 范围内，当前值: {v}")
        return v


class GpuVramTileTier(BaseModel):
    """VAE 分块推荐档位（P0-3：显存阈值配置化）。

    Attributes:
        min_available_gb: 适用本档位的最低可用显存（GB），档位按此值降序匹配。
        tile_size: 推荐 VAE 分块大小（像素）。
        tile_overlap: 推荐 VAE 分块重叠（像素）。
    """

    model_config = ConfigDict(extra="ignore")
    min_available_gb: float
    tile_size: int
    tile_overlap: int


def _default_tile_tiers() -> list[GpuVramTileTier]:
    """默认分块推荐档位（与 gpu_utils 回退值一致，仅作缺省）。"""
    return [
        GpuVramTileTier(min_available_gb=20, tile_size=1024, tile_overlap=512),
        GpuVramTileTier(min_available_gb=12, tile_size=768, tile_overlap=256),
        GpuVramTileTier(min_available_gb=8, tile_size=512, tile_overlap=128),
        GpuVramTileTier(min_available_gb=0, tile_size=256, tile_overlap=64),
    ]


class GpuConfig(BaseModel):
    """GPU 后端配置模型。

    Attributes:
        backend: GPU 后端类型，"auto" 自动检测（NVIDIA CUDA → AMD ROCm →
            Apple MPS），或指定 "cuda" / "rocm" / "mps"。
        vram_tile_tiers: VAE 分块推荐档位表（按 min_available_gb 降序），
            供 gpu_utils.recommend_params 消费；P0-3 起为单一事实来源。
    """

    model_config = ConfigDict(extra="ignore")
    backend: str = "auto"
    vram_tile_tiers: list[GpuVramTileTier] = Field(default_factory=_default_tile_tiers)


class HistoryConfig(BaseModel):
    """历史记录数据库配置模型。

    定义 SQLite 历史记录数据库路径和最大记录数限制。

    Attributes:
        db_path: SQLite 数据库文件路径，相对于项目根目录。
        max_records: 最大历史记录条数，1-100000 范围内，超出自动清理旧记录。
    """

    model_config = ConfigDict(extra="ignore")
    db_path: str = "data/history.db"
    max_records: int = 10000

    @field_validator("max_records")
    @classmethod
    def validate_max_records(cls, v: int) -> int:
        """验证最大记录数是否在合法范围内。

        Args:
            v: 待验证的最大记录数。

        Returns:
            int: 验证通过的最大记录数。

        Raises:
            ValueError: 记录数不在 1-100000 范围内时抛出。
        """
        if not (1 <= v <= 100000):
            raise ValueError(f"max_records 必须在 1-100000 范围内，当前值: {v}")
        return v


class I18nConfig(BaseModel):
    """国际化（i18n）配置模型。

    定义默认语言和可用语言列表，翻译文件位于 locales/ 目录。

    Attributes:
        default_locale: 默认语言代码，"zh"（中文）、"zh-TW"（繁体中文）、"en"（英文）、"ja"（日文）、"fr"（法文）。
        available_locales: 可用语言代码列表。
    """

    model_config = ConfigDict(extra="ignore")
    default_locale: str = "zh"
    available_locales: list[str] = Field(default_factory=lambda: ["zh", "zh-TW", "en", "ja", "fr"])


class LoggingConfig(BaseModel):
    """日志系统配置模型。

    定义日志级别、日志文件路径和滚动日志参数。

    Attributes:
        level: 日志级别，"DEBUG"、"INFO"、"WARNING"、"ERROR"、"CRITICAL"。
        file: 日志文件路径，相对于项目根目录。
        max_size_mb: 单个日志文件最大大小（MB），超出自动滚动。
        backup_count: 保留的历史日志文件数量。
    """

    model_config = ConfigDict(extra="ignore")
    level: str = "INFO"
    file: str = "logs/app.log"
    max_size_mb: int = 50
    backup_count: int = 3


class CacheConfig(BaseModel):
    """文件缓存配置模型。

    定义上传文件缓存的过期时间和最大大小限制。

    Attributes:
        ttl: 缓存文件存活时间（秒），默认 86400 秒（1天）。
        max_size_mb: 缓存目录最大大小（MB），超出自动清理最旧文件。
    """

    model_config = ConfigDict(extra="ignore")
    ttl: int = 86400
    max_size_mb: int = 500


class TorchCompileStage(BaseModel):
    """单个阶段（DiT 或 VAE）的 torch.compile 设置。

    两阶段独立建模是刻意的：torch.compile 对 DiT 与 VAE 的收益、代价与失败模式都不同
    （本机 12GB 实测：DiT 编译后稳态慢 47%、峰值显存翻倍；VAE 编译后稳态慢 50%；
    fullgraph 对含动态形状的 VAE 更易 graph break），全局单开关会让"只编一个"这种常见需求
    无法表达 —— numz 社区集成正是因此把编译做成独立节点。

    Attributes:
        enabled: 是否对本阶段启用 torch.compile。
        mode: 编译模式，"default" / "reduce-overhead" / "max-autotune"。
        backend: 编译后端，默认 "inductor"。
        fullgraph: 是否要求整图编译（True 时 graph break 会直接报错，便于发现问题）。
        dynamic: 是否按动态 shape 编译。
    """

    model_config = ConfigDict(extra="ignore")
    enabled: bool = False
    mode: str = "default"
    backend: str = "inductor"
    fullgraph: bool = False
    dynamic: bool = False


class TorchCompileSettings(BaseModel):
    """按阶段拆分的 torch.compile 总配置。

    Attributes:
        dit: DiT（去噪主干）阶段的编译设置。
        vae: VAE（编解码器）阶段的编译设置。
    """

    model_config = ConfigDict(extra="ignore")
    dit: TorchCompileStage = Field(default_factory=TorchCompileStage)
    vae: TorchCompileStage = Field(default_factory=TorchCompileStage)


class InferenceConfig(BaseModel):
    """推理优化配置模型。

    定义推理时的各种性能优化参数，字段名与默认值对齐
    seedvr2_engine._get_inference_config() 实际读取逻辑。

    Attributes:
        blocks_to_swap: BlockSwap 换出到 CPU 的 Transformer 块数量，0 表示不启用。
        blockswap_prefetch: 是否启用下一块预取流水（P2-2），传输与计算重叠；
            仅 blocks_to_swap > 0 时生效。修改后需重载模型。默认 False：
            2026-08-30 实测 A/B（3B@512/2048 稳态 on≈off，且多驻留一个块显存），
            收益场景为 7B 等大换出负载，验证后可开启。
        swap_io_components: 是否将输入/输出组件卸载到 CPU。
        offload_device: 卸载目标设备，"cpu" 或其他设备。
        attention_mode: 注意力计算模式，"sdpa"（PyTorch 内置缩放点积注意力）。
        inference_mode: 推理模式，"distilled"（蒸馏模式，速度更快）。
        resolution: 推理分辨率。
        max_resolution: 最大分辨率限制，0 表示不限制。
        batch_size: 批处理大小。
        uniform_batch_size: 是否强制所有批次使用相同大小。
        temporal_overlap: 时序片段重叠帧数。
        prepend_frames: 段首预填充帧数（用于保持时序连续性）。
        temporal_segment_size: 时序分段大小，0 表示不分段。
        temporal_segment_overlap: 时序分段重叠帧数。
        input_noise_scale: 输入噪声缩放因子，0 表示不加噪声。
        latent_noise_scale: 潜空间噪声缩放因子。
        restoration_guidance_scale: 修复引导强度。
        color_correction: 色彩校正方法，"lab" 使用 LAB 颜色空间匹配。
        seed: 随机种子，-1 表示随机。
        enable_debug: 是否启用调试输出。
        fp8_enabled: 是否启用 FP8 推理（兼容字段，引擎通过原始字典读取）。
        distilled_mode: 是否使用蒸馏模式（兼容字段）。
        memory_threshold: 内存使用率阈值 (0.5-0.99)，超过此值终止推理。
        memory_min_available_gb: 绝对可用内存下限 (GB)，低于此值同样终止推理。
        torch_compile: 分阶段编译设置，见 ``TorchCompileSettings``（dit / vae 各自 enabled/mode/
            backend/fullgraph/dynamic）。旧的顶层扁平写法仍被引擎接受并视为两阶段同时生效。
    """

    model_config = ConfigDict(extra="ignore")
    blocks_to_swap: int = 0
    blockswap_prefetch: bool = False
    swap_io_components: bool = False
    offload_device: str = "cpu"
    attention_mode: str = "sdpa"
    inference_mode: str = "distilled"
    resolution: int = 2048
    max_resolution: int = 0
    batch_size: int = 1
    uniform_batch_size: bool = True
    temporal_overlap: int = 0
    prepend_frames: int = 0
    temporal_segment_size: int = 0
    temporal_segment_overlap: int = 8
    input_noise_scale: float = 0.0
    latent_noise_scale: float = 0.0
    restoration_guidance_scale: float = 1.0
    color_correction: str = "lab"
    seed: int = -1
    enable_debug: bool = False
    fp8_enabled: bool = False
    distilled_mode: bool = False
    cache_model: bool = False
    force_reload_dit: bool = False
    torch_compile: TorchCompileSettings = Field(default_factory=TorchCompileSettings)
    """torch.compile 分阶段设置（dit / vae 各自独立开关），默认全部关闭。"""
    memory_threshold: float = Field(0.95, ge=0.5, le=0.99)
    """内存使用率阈值 (0.5-0.99)，超过此值终止推理，防止系统卡死"""
    memory_min_available_gb: float = Field(2.0, ge=0.5, le=64.0)
    """绝对可用内存下限 (GB)，低于此值同样终止推理"""
    max_output_side: int = Field(6144, ge=0)
    """输出长边服务端硬上限（像素），0 表示不限制。

    与 ``max_resolution`` 的区别：后者是**请求侧**的长边上限（0=不限，由客户端
    决定）；本字段是**服务端**兜底，请求里的 resolution / 两倍模式算出的分辨率
    一旦超过它就直接拒绝。用于挡住「客户端传 resolution=20000」这类必然 OOM
    且会占用 GPU 数小时的请求（对齐 VOSR2-Lite 的 inference.max_output_side）。
    """


class RuntimeSseConfig(BaseModel):
    """SSE（Server-Sent Events）进度推送运行时参数配置模型。

    替代源码中硬编码的 SSE 超时和心跳间隔。

    Attributes:
        max_duration_seconds: 单个 SSE 连接最大持续时间（秒），10-86400 范围。
        heartbeat_interval_seconds: SSE 心跳发送间隔（秒），5-600 范围，防止代理超时断开。
        poll_interval_seconds: 事件轮询间隔（秒），0.1-10.0 范围，平衡实时性和 CPU 占用。
    """

    model_config = ConfigDict(extra="ignore")
    max_duration_seconds: int = Field(300, ge=10, le=86400)
    heartbeat_interval_seconds: int = Field(30, ge=5, le=600)
    poll_interval_seconds: float = Field(0.5, ge=0.1, le=10.0)


class RuntimeBatchConfig(BaseModel):
    """批量任务运行时参数配置模型。

    定义批量处理时的重试策略，使用指数退避算法。

    Attributes:
        max_retries: 单个任务最大重试次数，0-10 范围。
        retry_base_delay_seconds: 重试基础延迟（秒），0.1-60.0 范围，实际延迟为 base * 2^attempt。
        retry_max_delay_seconds: 重试最大延迟（秒），1.0-600.0 范围，防止指数退避无限增长。
    """

    model_config = ConfigDict(extra="ignore")
    max_retries: int = Field(2, ge=0, le=10)
    retry_base_delay_seconds: float = Field(1.0, ge=0.1, le=60.0)
    retry_max_delay_seconds: float = Field(30.0, ge=1.0, le=600.0)


class RuntimeTaskConfig(BaseModel):
    """任务队列运行时参数配置模型。

    定义任务队列容量、超时和 ID 长度等参数。

    Attributes:
        id_length: 任务 ID 字符串长度，8-32 字符范围。
        max_timeout_seconds: 单个任务最大执行时间（秒），60-86400 范围，防止卡死任务阻塞队列。
        queue_maxsize: 任务队列最大容量，1-10000 范围，超出时新任务提交会拒绝。
        auto_recover: 启动时是否自动恢复未完成任务并继续推理，默认关闭。
        checkpoint_dir: 断点续跑 checkpoint 文件存储目录，相对于项目根目录。
        checkpoint_every: 每处理多少个文件写一次 checkpoint，1 表示每个文件都写。
        stale_threshold_minutes: processing 任务超过该分钟数无更新视为卡死（0-1440，0=禁用）。
        progress_stall_timeout_minutes: 当前运行任务进度停滞超过该分钟数自动取消
            （0-1440，0=禁用；防止单文件推理挂死占住唯一 worker）。
        checkpoint_ttl_minutes: 失败/中断任务残留 checkpoint JSON 的最长保留分钟数，
            启动孤儿扫描时清理超期文件（0=禁用；数据治理 P2-1）。
    """

    model_config = ConfigDict(extra="ignore")
    id_length: int = Field(16, ge=8, le=32)
    max_timeout_seconds: int = Field(3600, ge=60, le=86400)
    queue_maxsize: int = Field(100, ge=1, le=10000)
    auto_recover: bool = Field(
        False,
        description="启动时是否自动恢复数据库中未完成的修复任务并重新推理",
    )
    checkpoint_dir: str = Field(
        "data/checkpoints",
        description="断点续跑 checkpoint 文件存储目录",
    )
    checkpoint_every: int = Field(
        1,
        ge=1,
        le=100,
        description="每处理多少个文件写一次 checkpoint",
    )
    stale_threshold_minutes: int = Field(
        30,
        ge=0,
        le=1440,
        description="processing 任务无更新超过该分钟数视为卡死并清理；0 表示禁用",
    )
    progress_stall_timeout_minutes: int = Field(
        30,
        ge=0,
        le=1440,
        description="运行中任务进度停滞超过该分钟数自动取消；0 表示禁用",
    )
    checkpoint_ttl_minutes: int = Field(
        1440,
        ge=0,
        le=43200,
        description="失败/中断任务残留 checkpoint JSON 的最长保留分钟数，启动孤儿扫描清理超期文件；0 表示禁用",
    )


class RuntimeUploadConfig(BaseModel):
    """文件上传运行时参数配置模型。

    定义大文件分片上传参数。

    Attributes:
        large_file_threshold_mb: 大文件阈值（MB），1-1024 范围，超过此大小使用分片上传。
        chunk_size_bytes: 分片大小（字节），1KB-1MB 范围。
    """

    model_config = ConfigDict(extra="ignore")
    large_file_threshold_mb: int = Field(10, ge=1, le=1024)
    chunk_size_bytes: int = Field(8192, ge=1024, le=1024 * 1024)


class RuntimeSecurityConfig(BaseModel):
    """安全运行时参数配置模型。

    定义路径白名单和速率限制等安全策略。

    Attributes:
        allowed_base_dirs: 允许文件系统访问的基础目录白名单，path_guard 使用此列表防止目录遍历。
        rate_limit_per_minute: 每分钟请求速率限制，1-10000 范围，防止 API 滥用。
        integrity_enforce: 启动完整性自检失败是否阻断启动（fail-fast）。
        integrity_recheck_interval_seconds: 运行时周期完整性重检间隔（秒），0 表示禁用。
        max_upload_image_mb: 单张图片上传大小上限（MB），1-2048 范围。
        max_upload_video_mb: 单个视频上传大小上限（MB），1-10240 范围。
        watermark_on_failure: 水印嵌入失败处置策略（评估报告 R2）：
            mark_metadata=侧车元数据标识（默认）/ block=阻断产出 / ignore=仅日志。
        mcp_enabled_tools: MCP 工具白名单：tools/list 与 tools/call 双侧按此过滤
            （缺省=全部可用，空列表=全部禁用）。
        mcp_max_calls_per_minute: MCP 工具调用频率预算（次/分钟，滑动 60s 窗口；0=不限频）。
    """

    model_config = ConfigDict(extra="ignore")
    allowed_base_dirs: list[str] = Field(default_factory=lambda: ["outputs/", "data/uploads/"])
    rate_limit_per_minute: int = Field(30, ge=1, le=10000)
    integrity_enforce: bool = False
    integrity_recheck_interval_seconds: int = Field(1800, ge=0, le=86400)
    max_upload_image_mb: int = Field(50, ge=1, le=2048, description="单张图片上传大小上限（MB）")
    max_upload_video_mb: int = Field(500, ge=1, le=10240, description="单个视频上传大小上限（MB）")
    watermark_on_failure: str = Field(
        "mark_metadata",
        pattern="^(mark_metadata|block|ignore)$",
        description="水印嵌入失败处置策略：mark_metadata（默认）/ block / ignore",
    )

    mcp_enabled_tools: list[str] = Field(
        default_factory=lambda: ["list_tools", "restore", "status", "history"],
        description="MCP 工具白名单：tools/list 与 tools/call 双侧按此过滤（缺省=全部可用，空列表=全部禁用）",
    )
    mcp_max_calls_per_minute: int = Field(
        30,
        ge=0,
        le=10000,
        description="MCP 工具调用频率预算（次/分钟，滑动 60s 窗口）；0 表示不限频",
    )


class RetentionConfig(BaseModel):
    """输出与上传产物保留策略配置模型。

    定义 outputs/ 推理输出与 data/uploads/ 用户上传的自动清理策略，
    以及任务启动前的磁盘空间预检阈值，防止文件只增不删写满磁盘
    （成本治理 P0-1；uploads 留存为数据治理 P0-1）。

    Attributes:
        outputs_max_age_days: 输出文件最大保留天数，超过自动删除；0 表示禁用年龄规则。
        outputs_max_files: 输出文件数量上限（保留最新 N 个）；0 表示不限制。
        outputs_cleanup_interval_seconds: outputs/uploads 周期清理共用间隔（秒），60-604800 范围。
        disk_min_free_gb: 任务启动前输出磁盘最低剩余空间（GB），低于此值拒绝新任务；0 表示禁用预检。
        uploads_max_age_days: 用户上传原始文件（data/uploads/ 下，restored/ 成品子树除外）
            最大保留天数，超过自动删除；0 表示禁用。原始上传比修复产物更隐私敏感，
            默认 7 天（短于 outputs 的 14 天）。
    """

    model_config = ConfigDict(extra="ignore")
    outputs_max_age_days: int = Field(14, ge=0, le=3650)
    outputs_max_files: int = Field(0, ge=0)
    outputs_cleanup_interval_seconds: int = Field(3600, ge=60, le=604800)
    disk_min_free_gb: float = Field(5.0, ge=0.0, le=1024.0)
    uploads_max_age_days: int = Field(7, ge=0, le=3650)


class RuntimeOomBreakerConfig(BaseModel):
    """OOM 连续失败熔断配置（P2-12）。

    Attributes:
        enabled: 是否启用熔断（False 时 oom_breaker_remaining 恒返回 0）。
        threshold: 连续 OOM 失败次数阈值（1-20），达到即打开熔断。
        cooldown_seconds: 熔断冷却期（30-7200 秒），期间新任务提交返回 503 + Retry-After。
    """

    model_config = ConfigDict(extra="ignore")
    enabled: bool = True
    threshold: int = Field(3, ge=1, le=20)
    cooldown_seconds: float = Field(600.0, ge=30.0, le=7200.0)


class RuntimeRetryConfig(BaseModel):
    """推理坏案例自动重试配置模型。

    定义 OOM 等推理失败后的自动重试与参数降级策略
    （blocks_to_swap 增加 → 分辨率下降 → 种子轮换）。

    Attributes:
        enabled: 是否启用坏案例自动重试。
        max_retries: 最大重试次数（不含首次），0-10 范围，0 表示禁用。
        base_delay_seconds: 重试基础退避延迟（秒）。
        max_delay_seconds: 重试退避上限（秒）。
    """

    model_config = ConfigDict(extra="ignore")
    enabled: bool = True
    max_retries: int = Field(2, ge=0, le=10)
    base_delay_seconds: float = Field(1.0, ge=0.0, le=60.0)
    max_delay_seconds: float = Field(30.0, ge=1.0, le=600.0)
    oom_breaker: RuntimeOomBreakerConfig = Field(
        default_factory=RuntimeOomBreakerConfig,
        description="OOM 连续失败熔断（P2-12）",
    )


class RuntimeConfig(BaseModel):
    """运行时配置根模型。

    聚合所有运行时参数子配置，替代源码中散落的硬编码常量。
    通过 config.yaml 的 runtime 节统一配置。

    Attributes:
        vram_preflight_enabled: 任务提交前显存预检门禁开关（成本治理 P1-2）。
            开启时提交任务先估算所选配置的显存需求，超过可用预算直接拒绝
            （HTTP 503 + 降档建议）；关闭则回退到运行期 OOM 降级重试兜底。
        sse: SSE 进度推送配置。
        batch: 批量任务重试配置。
        retry: 推理坏案例自动重试配置。
        task: 任务队列配置。
        upload: 文件上传配置。
        security: 安全策略配置。
    """

    model_config = ConfigDict(extra="ignore")
    vram_preflight_enabled: bool = Field(True)
    sse: RuntimeSseConfig = Field(default_factory=RuntimeSseConfig)
    batch: RuntimeBatchConfig = Field(default_factory=RuntimeBatchConfig)
    retry: RuntimeRetryConfig = Field(default_factory=RuntimeRetryConfig)
    task: RuntimeTaskConfig = Field(default_factory=RuntimeTaskConfig)
    upload: RuntimeUploadConfig = Field(default_factory=RuntimeUploadConfig)
    security: RuntimeSecurityConfig = Field(default_factory=RuntimeSecurityConfig)


class ImageRestoreParams(BaseModel):
    """图像修复请求参数模型。

    定义图像修复 API 接受的完整参数集，包括 DiT 模型配置、VAE 配置、
    输出配置等，用于参数验证和默认值填充。

    Attributes:
        dit_model: DiT 模型版本标识，如 "3b_fp16"。
        dit_device: DiT 推理设备，如 "cuda:0"。
        blocks_to_swap: BlockSwap 换出块数，0-36 范围。
        swap_io_components: 是否卸载 I/O 组件到 CPU。
        dit_offload_device: DiT 卸载目标设备。
        dit_cache_model: 是否缓存 DiT 模型避免重复加载。
        attention_mode: 注意力计算模式。
        vae_model: VAE 模型版本标识，如 "ema_vae_fp16"。
        vae_device: VAE 推理设备。
        encode_tiled: VAE 编码是否启用分块处理（高分辨率必需）。
        encode_tile_size: VAE 编码瓦片大小，>=64。
        encode_tile_overlap: VAE 编码瓦片重叠像素，>=0。
        decode_tiled: VAE 解码是否启用分块处理。
        decode_tile_size: VAE 解码瓦片大小，>=64。
        decode_tile_overlap: VAE 解码瓦片重叠像素，>=0。
        tile_debug: 是否显示瓦片边界调试信息，"true"/"false"。
        vae_offload_device: VAE 卸载目标设备。
        vae_cache_model: 是否缓存 VAE 模型。
        seed: 随机种子。
        resolution: 输出分辨率（长边像素），>=1。
        max_resolution: 最大分辨率限制，0 表示不限制，>=0。
        batch_size: 批处理大小，>=1。
        uniform_batch_size: 是否强制统一批次大小。
        color_correction: 色彩校正方法。
        temporal_overlap: 时序重叠帧数（视频用），>=0。
        prepend_frames: 段首预填充帧数，>=0。
        input_noise_scale: 输入噪声缩放，>=0.0。
        latent_noise_scale: 潜空间噪声缩放，>=0.0。
        offload_device: 默认卸载设备。
        enable_debug: 是否启用调试输出。
    """

    model_config = ConfigDict(extra="ignore")

    dit_model: str = "3b_fp16"
    dit_device: str = "cuda:0"
    blocks_to_swap: int = Field(32, ge=0, le=36)
    swap_io_components: bool = True
    dit_offload_device: str = "cpu"
    dit_cache_model: bool = True
    force_reload_dit: bool = False
    attention_mode: str = "sdpa"

    vae_model: str = "ema_vae_fp16"
    vae_device: str = "cuda:0"
    encode_tiled: bool = True
    encode_tile_size: int = Field(1024, ge=64)
    encode_tile_overlap: int = Field(512, ge=0)
    decode_tiled: bool = True
    decode_tile_size: int = Field(1024, ge=64)
    decode_tile_overlap: int = Field(512, ge=0)
    tile_debug: str = "false"
    vae_offload_device: str = "cpu"
    vae_cache_model: bool = True

    seed: int = 1373201197
    resolution: int = Field(2160, ge=1)
    max_resolution: int = Field(0, ge=0)
    batch_size: int = Field(1, ge=1)
    uniform_batch_size: bool = True
    color_correction: str = "lab"
    temporal_overlap: int = Field(0, ge=0)
    prepend_frames: int = Field(0, ge=0)
    input_noise_scale: float = Field(0.0, ge=0.0)
    latent_noise_scale: float = Field(0.0, ge=0.0)
    offload_device: str = "cpu"
    enable_debug: bool = False

    # 输出配置
    output_format: str = "png"  # 输出格式："png", "jpg", "webp", "bmp", "tiff"


class UnifiedRestoreParams(ImageRestoreParams):
    """统一修复参数模型。

    继承自 ImageRestoreParams，增加 task_type 字段用于区分修复任务类型，
    作为 parse_unified_params 函数的返回值类型。

    Attributes:
        task_type: 任务类型，"auto" 自动检测、"video" 视频、"image" 图像。
        double_res: 两倍模式开关。True 时后端忽略客户端传入的分辨率，
            强制对图片文件按「真实短边 × 2」重新计算分辨率
            （视频文件暂不支持该模式，即使为 True 也不生效）。
    """

    task_type: str = "auto"
    double_res: bool = False


class VideoRestoreParams(BaseModel):
    """视频修复请求参数模型。

    分辨率语义与图片一致 (SideResize): 短边=resolution, 长边<=max_resolution，
    0/缺失时回退 config.yaml restore 节 (default_resolution_h/w)。

    Attributes:
        seed: 随机种子，用于可复现的视频修复结果。
        resolution: 短边目标像素，0 表示不指定。
        max_resolution: 长边像素上限，0 表示不限制。
        cache_model: 是否缓存 DiT/VAE 模型跨任务复用。
        blocks_to_swap: BlockSwap 换出到 CPU 的 Transformer 块数量，0 表示不启用。
        batch_size: 批处理大小（视频分段帧数，需满足 4n+1）。
    """

    model_config = ConfigDict(extra="ignore")

    seed: int = 1373201197
    resolution: int = 0
    max_resolution: int = 0
    cache_model: bool = False
    force_reload_dit: bool = False
    blocks_to_swap: int = 0
    batch_size: int = 5

    # 输出配置
    output_format: str = "mp4"  # 视频输出格式："mp4", "avi", "mov", "mkv"


class AppConfig(BaseModel):
    """根应用配置模型。

    聚合所有配置子模型，对应 config.yaml 的根结构。
    所有配置项都有合理默认值，缺失字段自动填充默认。
    extra="ignore" 自动忽略 config.yaml 中未定义的字段，
    保证配置文件向前兼容。

    Attributes:
        server: HTTP 服务器配置。
        model: 模型加载配置。
        restore: 修复算法默认参数。
        gpu: GPU 后端配置。
        history: 历史记录数据库配置。
        i18n: 国际化配置。
        logging: 日志配置。
        cache: 文件缓存配置。
        inference: 推理优化配置。
        retention: 输出产物保留策略配置。
        runtime: 运行时参数（替代硬编码常量）。
        user_preferences: 用户偏好字典，前端 WebUI 通过 SettingsPersistence 独立管理，
                         此处仅保留字段防止 model_dump() 序列化时丢失。
    """

    model_config = ConfigDict(extra="ignore")
    server: ServerConfig = Field(default_factory=ServerConfig)
    model: ModelConfig = Field(default_factory=ModelConfig)
    restore: RestoreConfig = Field(default_factory=RestoreConfig)
    gpu: GpuConfig = Field(default_factory=GpuConfig)
    history: HistoryConfig = Field(default_factory=HistoryConfig)
    i18n: I18nConfig = Field(default_factory=I18nConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    cache: CacheConfig = Field(default_factory=CacheConfig)
    inference: InferenceConfig = Field(default_factory=InferenceConfig)
    retention: RetentionConfig = Field(default_factory=RetentionConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    user_preferences: dict[str, Any] = Field(default_factory=dict)


def get_pretrained_root(model_config: dict | ModelConfig, project_root: str | None = None) -> str:
    """根据模型源模式解析预训练模型根目录的绝对路径。

    支持两种模式（借鉴 Image_MultiModel 的 shared/portable 双模式）:
    - portable 模式（默认）: 返回 {project_root}/{pretrained_dir}，
      模型文件存储在项目内部的 model 目录中。
    - shared 模式: 当 shared_models_root 非空时，返回该外部共享目录路径，
      多个项目可共用同一套模型文件；shared_models_root 为空时回退到 portable 模式。

    Args:
        model_config: 模型配置字典或 ModelConfig 实例，包含 model_source_mode、
                      shared_models_root 和 pretrained_dir 字段。
        project_root: 项目根目录路径，为 None 时自动推断（基于本文件位置）。

    Returns:
        str: 预训练模型根目录的绝对路径。

    Example:
        >>> cfg = {"model_source_mode": "shared", "shared_models_root": "D:/shared_models"}
        >>> get_pretrained_root(cfg)
        'D:/shared_models'
        >>> cfg = {"model_source_mode": "portable", "pretrained_dir": "model"}
        >>> get_pretrained_root(cfg, "/project")
        '/project/model'
    """
    # 从 dict 或 ModelConfig 中提取字段
    if isinstance(model_config, dict):
        mode = model_config.get("model_source_mode", "portable")
        shared_root = model_config.get("shared_models_root", "")
        pretrained_dir = model_config.get("pretrained_dir", "model")
    else:
        mode = model_config.model_source_mode
        shared_root = model_config.shared_models_root
        pretrained_dir = model_config.pretrained_dir

    if mode == "shared" and shared_root:
        return os.path.abspath(shared_root)

    if project_root is None:
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(project_root, pretrained_dir)


def load_validated_config(config_path: str) -> AppConfig:
    """加载 YAML 配置文件并通过 AppConfig Pydantic 模型验证。

    便捷函数，用于需要直接从文件路径加载并验证配置的场景。

    Args:
        config_path: YAML 配置文件的绝对或相对路径。

    Returns:
        AppConfig: 验证后的配置模型实例。

    Raises:
        FileNotFoundError: 配置文件不存在时抛出。
        pydantic.ValidationError: 配置内容不符合模型定义时抛出。
        yaml.YAMLError: YAML 解析错误时抛出。
    """
    import yaml

    with open(config_path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    return AppConfig(**raw)
