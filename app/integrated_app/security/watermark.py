# SPDX-FileCopyrightText: Copyright (c) 2024-2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""不可感知数字水印模块 (DCT 频域水印)

在推理输出图像/视频帧中嵌入不可感知的 DCT 频域水印，
即使所有 UI/代码标识被移除，仍可从输出内容中提取归属水印，
是唯一可举证的侵权溯源手段。

水印信息: "SeedVR2_ReSerendipity" + 生成时间戳
嵌入策略: 在图像的 DCT 中频系数中嵌入二进制水印序列，
          利用人类视觉对中频不敏感的特性实现不可感知性。

安全特性（实测边界见 scripts/experiment_watermark_transcode.py --attacks，2026-09-19）:
    - 不可感知: 图像档 (alpha=0.5) PSNR 57-69dB，最大像素改动 3/255、
      仅约 0.9% 像素被触碰；鲁棒档 (alpha=0.05, repeat=3) PSNR ≈ 37dB，
      最大改动 31-33/255（平面渐变区近看可察，只在产物要走有损编码时启用）
    - 抗再编码: 鲁棒档用三通道等幅嵌入（纯亮度扰动）+ 连续重复码，实测
      H.264 CRF14/18/23 转码后签名验证全部存活（16/16 帧，2026-09-06 实验）。
      **JPEG/WebP 属临界区**（2026-09-19 攻击矩阵）：真实照片与平滑内容实测
      q90/q95 存活，合成细密纹理与均匀噪声实测失效，q80 及以下未见过存活；
      所以有损图像产物是「尽力而为 + 落盘复验定真伪」，不是保证存活。
      图像档 (alpha=0.5) 则任何有损编码都活不下来（JPEG q95 即失效），
      只适用于无损保存产物——有损输出必须由调用方选鲁棒档
      （services/watermark_policy.select_image_embed_tier）。
      鲁棒档的冗余吃块数：生产载荷 = 品牌前缀 + 任务 ID + 摘要 ≈ 103 字符
      （824 bit），× repeat 3 需 ≥2472 个块（约 400x400 以上），小图会降档到
      repeat=1 并记 warning，存活率随之下滑（产出仍会落盘，由落盘复验按策略处置）
    - 不抗（实测多数情形失效）: 裁剪与旋转（载荷位序从 (0,0) 起算，位移即错位）、
      缩放再放回、JPEG q80 以下的强压缩；轻噪声 (σ=2) 鲁棒档可扛、图像档不可。
      本机制是「未再加工产物」的归属举证手段，不是对抗任意攻击的强鲁棒水印
    - 吃内容: 同一攻击下存活率随图像内容摆动（均匀白噪声图每个块都塞满中频能量，
      是 QIM 的最坏情形；照片/渐变/低频纹理更友好）。因此产物侧一律以落盘复验
      为准（watermark_policy.output_carries_watermark），不按内容类型猜测档位
    - 可证伪: 载荷带 HMAC-SHA256 签名，无密钥者无法伪造可通过验证的水印；
      未配置密钥时降级为弱检测（载荷补品牌前缀 + 一次性告警与审计事件），
      弱检测任何人可伪造，不具举证力

载荷与归属边界（决定这份水印能证明什么）:
    载荷格式:  "<SeedVR2_ReSerendipity>_<task_id>|<hmac-sha256>"   有密钥（生产默认）
               "<SeedVR2_ReSerendipity>_<task_id>|unsigned"          无密钥降级
               "SeedVR2_ReSerendipity_<时间戳>"                      调用方未给 payload
    提取后可用 strip_watermark_envelope() 剥回 task_id，经
    GET /api/system/history/resolve?watermark_payload= 反查任务与参数。

    能证明:  这份未再加工的产物，出自一个持有**该密钥**的 SeedVR2 实例。
    不能证明: 1) 内容是 AI 生成的（本工具是修复工具，输入本就是任意图）；
             2) 出自项目方——便携包/桌面安装包刻意不打包密钥（见
                scripts/portable_bundle_lib.ps1 的拒绝清单），终端用户首启会
                自动生成自己的密钥，因此**别人机器上的产物用你的密钥验不过**
                （已实测）。跨分发可归属需要在线签发，客户端持密钥做不到；
             3) 传播链上的副本——缩放/裁剪一次即结构性失效（无同步定位码）。
    品牌前缀让"出自 SeedVR2"在提取结果里可读，但它本身无密码学效力（任何人
    都能嵌同样的字符串）；有举证力的只有验签通过那一步，且仅对**你持密钥**的产物成立。

使用方式:
    from app.integrated_app.security.watermark import embed_watermark

    watermarked_np = embed_watermark(image_np)  # 在保存前调用
"""

import hashlib
import hmac
import logging
import os
import time
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

# 水印品牌标识
_WATERMARK_BRAND = "SeedVR2_ReSerendipity"

# 水印嵌入强度 (越小越不可感知，越大越鲁棒)
# 对于 QIM 量化: quant_step = 1.0 / alpha
# alpha=0.5 -> quant_step=2, 对 8bit 图像最大修改量 ~1, PSNR > 50dB
_WATERMARK_ALPHA = 0.5

# 视频帧路径的鲁棒档强度：quant_step=20，配合三通道等幅嵌入（纯亮度扰动）
# 与 repeat=3 重复码，实测 H.264 CRF14/18/23 转码后位误码率 ≈ 0
# （2026-09-06 转码实验，见 scripts/experiment_watermark_transcode.py）。
# 代价：PSNR ≈ 37.5dB（仍属视觉透明档），仅用于经有损编码的产物。
_VIDEO_ALPHA = 0.05
_VIDEO_REPEAT = 3

# ===== 密钥签名配置（v2）=====
# 载荷格式: "<brand>_<timestamp>|<hmac-sha256 hex>"
# 无密钥时嵌入旧格式未签名载荷（兼容旧版验证）；有密钥时嵌入签名载荷，
# 未持有密钥者无法伪造可通过验证的水印，溯源举证以签名验证为准。
_WATERMARK_KEY_ENV = "SEEDVR2_WATERMARK_KEY"
# 密钥位置策略（桌面端更新换载只保留 runtime/model/data/logs 四个顶层目录）：
#   优先 data/.watermark_key —— 随用户数据保留，应用更新不丢密钥；
#   回退项目根 .watermark_key —— 兼容旧部署，存在时自动迁入 data/（复制，不删旧文件）。
_WATERMARK_KEY_FILE_DATA = Path(__file__).resolve().parents[3] / "data" / ".watermark_key"
_WATERMARK_KEY_FILE_LEGACY = Path(__file__).resolve().parents[3] / ".watermark_key"
# K-1 密钥轮换：rotate_watermark_key.py 把旧密钥备份为 data/.watermark_key.old，
# 验证链同时尝试新旧密钥，轮换后历史产物仍可验明归属。
_WATERMARK_KEY_FILE_OLD = Path(__file__).resolve().parents[3] / "data" / ".watermark_key.old"
_HMAC_SEPARATOR = "|"
# 未签名降级载荷在分隔符后写的固定标记（不是 64 位摘要 → 永不可能通过验签）
_UNSIGNED_MARK = "unsigned"

# DCT 块大小
_BLOCK_SIZE = 8

# 水印嵌入的中频系数位置 (在 8x8 DCT 块中)
# 选择中频区域 (4-6 行/列) 作为嵌入位置，平衡不可感知性和鲁棒性
_EMBED_POSITIONS = [
    (4, 5),
    (5, 4),
    (5, 6),
    (6, 5),
    (4, 6),
    (6, 4),
    (5, 5),
    (6, 6),
]


def _text_to_bits(text: str) -> np.ndarray:
    """将文本转换为二进制位序列。

    Args:
        text: 待嵌入的文本字符串。

    Returns:
        np.ndarray: 二进制位数组 (0 和 1)。
    """
    data = text.encode("utf-8")
    bits = np.unpackbits(np.frombuffer(data, dtype=np.uint8))
    return bits


def _bits_to_text(bits: np.ndarray) -> str:
    """将二进制位序列转换回文本。

    Args:
        bits: 二进制位数组。

    Returns:
        str: 解码后的文本字符串。
    """
    # 确保长度是 8 的倍数
    length = (len(bits) // 8) * 8
    if length == 0:
        return ""
    packed = np.packbits(bits[:length])
    try:
        return packed.tobytes().decode("utf-8", errors="replace")
    except Exception:
        return ""


def _generate_watermark_payload() -> str:
    """生成包含品牌标识和时间戳的水印载荷。

    Returns:
        str: 格式为 "SeedVR2_ReSerendipity_YYYYMMDDHHMMSS" 的水印字符串。
    """
    timestamp = time.strftime("%Y%m%d%H%M%S")
    return f"{_WATERMARK_BRAND}_{timestamp}"


def _load_secret_key() -> bytes | None:
    """加载水印签名密钥（环境变量优先，其次 data/.watermark_key，回退项目根旧位置）。

    三者均未配置时首次运行自动生成密钥文件（等价 scripts/init_watermark_key.py），
    保证新部署开箱即有可证伪归属；生成失败（只读文件系统等）才降级为未签名水印。

    旧部署迁移：项目根 .watermark_key 存在而 data/ 无密钥时，自动复制到
    data/.watermark_key（保留旧文件兜底），保证升级后历史密钥仍可验证旧产物。
    """
    env_key = os.environ.get(_WATERMARK_KEY_ENV, "").strip()
    if env_key:
        return env_key.encode("utf-8")

    def _read_key_file(path: Path) -> bytes | None:
        try:
            if path.exists():
                key = path.read_text(encoding="utf-8").strip()
                if key:
                    return key.encode("utf-8")
        except OSError:
            pass
        return None

    # 1) 优先新位置 data/.watermark_key（更新换载保留目录，密钥不随版本丢失）
    key = _read_key_file(_WATERMARK_KEY_FILE_DATA)
    if key is not None:
        return key

    # 2) 回退旧位置（项目根），并顺手迁入 data/（复制，保留旧文件兜底）
    legacy = _read_key_file(_WATERMARK_KEY_FILE_LEGACY)
    if legacy is not None:
        try:
            _WATERMARK_KEY_FILE_DATA.parent.mkdir(parents=True, exist_ok=True)
            _WATERMARK_KEY_FILE_DATA.write_text(legacy.decode("utf-8") + "\n", encoding="utf-8")
            logger.debug(f"水印密钥已从旧位置迁移到 {_WATERMARK_KEY_FILE_DATA}")
        except Exception as e:  # noqa: BLE001 — 迁移失败继续用旧位置，不阻断
            logger.debug(f"水印密钥迁移失败（继续使用旧位置）: {e}")
        return legacy

    # 3) 首次运行自动生成（密钥文件已被 .gitignore 忽略，不会入库）
    try:
        import secrets as _secrets

        _WATERMARK_KEY_FILE_DATA.parent.mkdir(parents=True, exist_ok=True)
        _WATERMARK_KEY_FILE_DATA.write_text(_secrets.token_hex(32) + "\n", encoding="utf-8")
        # 信息类日志降到 debug：默认（INFO）级别下终端与 app.log 不出现水印字样。
        # 备份提示仍见 scripts/init_watermark_key.py 的输出与安全降级告警（缺密钥时 error）。
        logger.debug(f"已自动生成水印签名密钥: {_WATERMARK_KEY_FILE_DATA}（请离线备份）")
        return _WATERMARK_KEY_FILE_DATA.read_text(encoding="utf-8").strip().encode("utf-8")
    except Exception as e:
        logger.debug(f"水印密钥文件读写失败: {e}")
    return None


def _load_verify_keys() -> list[bytes]:
    """加载用于水印签名验证的全部密钥（K-1 多密钥验证链）。

    顺序：环境变量注入密钥（签发密钥）→ 当前 data/.watermark_key →
    旧位置项目根 .watermark_key → 轮换备份 data/.watermark_key.old →
    损坏修复备份 data/.watermark_key.invalid-*。
    轮换或修复后旧产物仍可用备份密钥验明归属；历史取证链不因密钥更换中断。
    """

    def _read(path: Path) -> bytes | None:
        try:
            if path.exists():
                key = path.read_text(encoding="utf-8").strip()
                if key:
                    return key.encode("utf-8")
        except OSError:
            pass
        return None

    keys: list[bytes] = []
    env_key = os.environ.get(_WATERMARK_KEY_ENV, "").strip()
    if env_key:
        keys.append(env_key.encode("utf-8"))
    paths = [
        _WATERMARK_KEY_FILE_DATA,
        _WATERMARK_KEY_FILE_LEGACY,
        _WATERMARK_KEY_FILE_OLD,
    ]
    paths.extend(sorted(_WATERMARK_KEY_FILE_DATA.parent.glob(f"{_WATERMARK_KEY_FILE_DATA.name}.invalid-*")))
    for path in paths:
        k = _read(path)
        if k is not None and k not in keys:
            keys.append(k)
    return keys


def _sign_payload(payload: str, key: bytes) -> str:
    """对水印载荷附加 HMAC-SHA256 签名。"""
    digest = hmac.new(key, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}{_HMAC_SEPARATOR}{digest}"


_key_missing_warned = False


def watermark_key_available() -> bool:
    """当前是否拿得到水印签名密钥（env / data/.watermark_key / 自动生成）。"""
    return _load_secret_key() is not None


def _report_missing_key() -> None:
    """密钥缺失只告警一次（嵌入按帧/按任务高频调用，避免刷屏）。

    缺失密钥意味着产物水印不可证伪（任何人都能伪造可通过弱检测的载荷）。
    这里刻意只打日志、不写审计通道：取证日志不该被 CLI/单测等非生产路径写入，
    生产侧由 :func:`watermark_policy.report_missing_watermark_key` 补记审计事件。
    """
    global _key_missing_warned
    if _key_missing_warned:
        return
    _key_missing_warned = True
    logger.error(
        "[SECURITY] 未配置水印签名密钥（env %s / data/.watermark_key 均缺失），"
        "产物水印降级为不可证伪的弱检测。请运行 scripts/init_watermark_key.py 生成密钥",
        _WATERMARK_KEY_ENV,
    )


def _verify_signature(signed_payload: str, key: bytes) -> str | None:
    """验证签名载荷，返回原始载荷；签名缺失或无效返回 None。

    解析规则：首个分隔符前的部分为载荷，其后固定 64 位 hex 为摘要；
    提取噪声不会影响解析（分隔符后的多余内容被忽略）。
    比较以字节进行——提取噪声可能产生非 ASCII 字符，
    hmac.compare_digest 对含非 ASCII 的 str 会抛 TypeError，此处必须免疫。
    """
    sep_pos = signed_payload.find(_HMAC_SEPARATOR)
    if sep_pos < 0:
        return None
    payload = signed_payload[:sep_pos]
    digest = signed_payload[sep_pos + 1 : sep_pos + 1 + 64]
    if len(digest) != 64:
        return None
    expected = hmac.new(key, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    if hmac.compare_digest(expected.encode("ascii"), digest.encode("utf-8", errors="replace")):
        return payload
    return None


def strip_watermark_envelope(payload: str) -> str:
    """把从产物里提取到的水印载荷还原成可反查任务的 ID。

    提取结果带着信封信息，直接拿去查任务必然落空：签名格式要剥掉分隔符之后的
    摘要（以及按字节对齐补进来的尾部噪声），未签名降级格式要剥掉品牌前缀。

    Args:
        payload: :func:`extract_watermark` 的返回值。

    Returns:
        str: 剥去签名段与品牌前缀后的载荷（即嵌入时传入的 task_id / batch_id）。
    """
    body = payload.split(_HMAC_SEPARATOR, 1)[0].strip()
    prefix = f"{_WATERMARK_BRAND}_"
    if body.startswith(prefix):
        body = body[len(prefix) :]
    return body


def _dct_1d(arr: np.ndarray) -> np.ndarray:
    """一维 DCT 变换 (Type-II)。

    使用矩阵乘法实现，避免依赖 scipy。
    """
    size = arr.shape[-1]
    n = np.arange(size)
    k = n.reshape(-1, 1)
    dct_matrix = np.cos(np.pi * (2 * n + 1) * k / (2 * size)) * np.sqrt(2.0 / size)
    dct_matrix[0] *= 1.0 / np.sqrt(2)
    return arr @ dct_matrix.T


def _idct_1d(arr: np.ndarray) -> np.ndarray:
    """一维逆 DCT 变换 (Type-II 的逆)。

    使用矩阵乘法实现。
    """
    size = arr.shape[-1]
    n = np.arange(size)
    k = n.reshape(-1, 1)
    idct_matrix = np.cos(np.pi * (2 * k + 1) * n / (2 * size)) * np.sqrt(2.0 / size)
    idct_matrix[:, 0] *= 1.0 / np.sqrt(2)
    return arr @ idct_matrix.T


def _dct_2d_block(block: np.ndarray) -> np.ndarray:
    """二维 DCT 变换 (行 DCT 后列 DCT)。

    Args:
        block: 2D 数组 (通常 8x8)。

    Returns:
        np.ndarray: DCT 变换后的系数矩阵。
    """
    return _dct_1d(_dct_1d(block.T).T)


def _idct_2d_block(block: np.ndarray) -> np.ndarray:
    """二维逆 DCT 变换。

    Args:
        block: DCT 系数矩阵。

    Returns:
        np.ndarray: 空间域图像块。
    """
    return _idct_1d(_idct_1d(block.T).T)


def embed_watermark(
    image_np: np.ndarray,
    *,
    payload: str | None = None,
    alpha: float = _WATERMARK_ALPHA,
    repeat: int = 1,
) -> np.ndarray:
    """在图像中嵌入不可感知 DCT 频域水印。

    嵌入流程:
        1. 生成水印载荷 (品牌标识 + 时间戳)，配置密钥时附加 HMAC 签名
        2. 将载荷转换为二进制位序列
        3. 对图像分 8x8 块做 DCT 变换，**三通道等幅嵌入**：每个通道在相同
           系数位置做相同的 QIM 系数修改——三通道等幅扰动 = 纯亮度扰动
           （ΔY=Δ、ΔCr=0），构造上免疫 H.264 4:2:0 色度下采样对单通道
           嵌入的破坏（2026-09-06 转码实验确认的结构性根因，见
           scripts/experiment_watermark_transcode.py）
        4. 连续重复码：载荷位序列按 ``repeat`` 倍连续铺入块序列
           （块 j 承载位 ``bits[j // repeat]``），提取端按多数投票恢复；
           容量不足时自动降档（repeat 收敛到 B // len(bits)）
        5. 做 IDCT 变换回空间域，裁剪到有效像素范围 [0, 255]

    Args:
        image_np: 输入图像 NumPy 数组 (H x W x C, uint8)。
        payload: 自定义水印载荷，None 时自动生成品牌标识+时间戳。
        alpha: 水印嵌入强度（quant_step = 1/alpha）。无损图像产物用默认 0.5
            （高保真）；要走有损编码的产物（视频帧、JPEG/WebP 图像）用 0.05
            —— 实测 H.264 CRF14/18/23 与 JPEG q85-q95、WebP q80/q100 后仍可验签。
            按格式选档见 ``watermark_policy.select_image_embed_tier``。
        repeat: 重复码次数。有损产物建议 3；无损图像 1（无需冗余）。

    Returns:
        np.ndarray: 嵌入水印后的图像 (与输入相同 shape 和 dtype)。

    Note:
        - 无损档 (alpha=0.5, repeat=1): PSNR 57-69dB，视觉不可感知，但**经
          JPEG q95 即不可验证**——只适用于 PNG/BMP/TIFF 等无损落盘
        - 鲁棒档 (alpha=0.05, repeat=3): PSNR ≈ 37dB（平面渐变区近看可察），
          换取有损编码后签名验证可存活；冗余要靠块数支撑——签名载荷约 776 bit
          × repeat 3 需 ≥2328 块（约 400x400 以上），小图会自动降档并记 warning，
          降到 1 后连 JPEG q95 都活不下来；图像小到装不下载荷时载荷被截断
          （同样记 warning），两种情形产物都无法验签
    """
    if image_np is None or image_np.size == 0:
        return image_np

    if payload is None:
        payload = _generate_watermark_payload()
    key = _load_secret_key()
    # 载荷一律带品牌前缀：签名只能证明"持该密钥的实例嵌入了它"，品牌串才是
    # "出自 SeedVR2"这件事在文件里的载体（分发安装各自生成密钥时尤其重要）。
    if _WATERMARK_BRAND not in payload:
        payload = f"{_WATERMARK_BRAND}_{payload}"
    if key is not None:
        if _HMAC_SEPARATOR not in payload:
            payload = _sign_payload(payload, key)
    else:
        _report_missing_key()
        if _HMAC_SEPARATOR not in payload:
            # 未签名也要留终止符：提取按字节补齐会带进尾部噪声，没有分隔符就
            # 切不出载荷边界（strip_watermark_envelope 依赖它还原 task_id）
            payload = f"{payload}{_HMAC_SEPARATOR}{_UNSIGNED_MARK}"

    bits = _text_to_bits(payload)
    if len(bits) == 0:
        return image_np

    # 转为 float 处理
    result = image_np.astype(np.float64).copy()
    h, w = result.shape[:2]
    if result.ndim == 2:
        result = result[:, :, np.newaxis]

    n_channels = result.shape[2]
    bit_idx = 0
    blocks_h = h // _BLOCK_SIZE
    blocks_w = w // _BLOCK_SIZE
    total_blocks = blocks_h * blocks_w
    # 容量不足时降档重复次数（至少 1 次完整嵌入）
    effective_repeat = max(1, min(int(repeat), total_blocks // len(bits)))
    if effective_repeat < repeat:
        logger.warning(
            "水印重复码从 repeat=%d 降到 %d（图像 %dx%d 只有 %d 块，装不下 %d bit 的 %d 倍冗余）"
            "——有损编码后的存活率随之下滑，产物可能验签失败",
            repeat,
            effective_repeat,
            w,
            h,
            total_blocks,
            len(bits),
            repeat,
        )
    repeat = effective_repeat
    n_marked = min(total_blocks, len(bits) * repeat)
    quant_step = 1.0 / alpha
    if len(bits) > total_blocks:
        logger.warning(
            "水印容量不足: 载荷 %d bit 需 %d 个 8x8 块，图像仅 %d 块（%dx%d），载荷将被截断且产物无法验签"
            "——小图请缩短 payload 或提高输出分辨率",
            len(bits),
            len(bits),
            total_blocks,
            w,
            h,
        )

    for bi in range(blocks_h):
        for bj in range(blocks_w):
            if bit_idx >= n_marked:
                break

            # 提取 8x8 块
            y0 = bi * _BLOCK_SIZE
            x0 = bj * _BLOCK_SIZE
            bit = bits[bit_idx // repeat]

            modified_blocks = []
            for c in range(n_channels):
                block = result[y0 : y0 + _BLOCK_SIZE, x0 : x0 + _BLOCK_SIZE, c].copy()
                dct_block = _dct_2d_block(block)

                # 在中频位置嵌入水印位 (QIM: Quantization Index Modulation)
                # 三通道做相同的系数修改 → 扰动集中于亮度分量
                for py, px in _EMBED_POSITIONS:
                    coeff = dct_block[py, px]
                    quantized = round(coeff / quant_step)
                    if quantized % 2 != bit:
                        quantized += 1
                    dct_block[py, px] = quantized * quant_step

                modified_blocks.append(_idct_2d_block(dct_block))

            for c in range(n_channels):
                result[y0 : y0 + _BLOCK_SIZE, x0 : x0 + _BLOCK_SIZE, c] = modified_blocks[c]

            bit_idx += 1

    # 裁剪到有效范围并恢复 dtype
    result = np.clip(result, 0, 255).astype(image_np.dtype)
    if image_np.ndim == 2:
        result = result[:, :, 0]

    logger.debug(f"水印已嵌入: payload='{payload[:30]}...', {bit_idx}/{n_marked} 块已标记 (repeat={repeat})")
    return result


def extract_watermark(
    image_np: np.ndarray,
    *,
    expected_length: int = 2048,
    alpha: float = _WATERMARK_ALPHA,
    repeat: int = 1,
) -> str:
    """从图像中提取 DCT 频域水印。

    用于验证输出图像是否包含 SeedVR2 归属水印。

    Args:
        image_np: 待检测的图像 NumPy 数组 (H x W x C, uint8)。
        expected_length: 期望提取的最大位数，默认 2048 bit（256 字符）——
            生产载荷是「品牌前缀 + 任务 ID + 64 位摘要」约 100 字符，
            取小了会连签名摘要都截断，反查与验签双双失败。
        alpha: 水印强度 (需与嵌入时一致)。
        repeat: 嵌入时的重复码次数 (需与嵌入时一致)；
            提取对每个位位置在 repeat 个连续块上做多数投票。

    Returns:
        str: 提取到的水印文本。如果包含 "SeedVR2" 则确认归属。
    """
    if image_np is None or image_np.size == 0:
        return ""

    data = image_np.astype(np.float64)
    if data.ndim == 2:
        data = data[:, :, np.newaxis]

    channel_data = data[:, :, 0]
    h, w = channel_data.shape
    blocks_h = h // _BLOCK_SIZE
    blocks_w = w // _BLOCK_SIZE
    total_blocks = blocks_h * blocks_w
    repeat = max(1, min(int(repeat), total_blocks))
    groups = total_blocks // repeat  # 每组 repeat 个连续块投票出一个位
    quant_step = 1.0 / alpha

    n_bits = min(expected_length, groups)
    bits: list[int] = []
    parity_flat: list[int] = []

    # 位序从图像左上角起算，读够 n_bits 组即可停：4K 图上全图扫描要百万级块，
    # 而验签只需载荷长度个块（落盘后逐产物复验依赖这一提前退出才够快）
    needed_blocks = n_bits * repeat
    for bi in range(blocks_h):
        for bj in range(blocks_w):
            if len(parity_flat) >= needed_blocks:
                break
            block = channel_data[bi * _BLOCK_SIZE : (bi + 1) * _BLOCK_SIZE, bj * _BLOCK_SIZE : (bj + 1) * _BLOCK_SIZE]
            dct_block = _dct_2d_block(block)

            votes = []
            for py, px in _EMBED_POSITIONS:
                coeff = dct_block[py, px]
                votes.append(round(coeff / quant_step) % 2)
            parity_flat.append(1 if sum(votes) > len(votes) // 2 else 0)

    # 连续重复码反交织：位 j = 组 j（块 j*repeat .. j*repeat+repeat-1）多数投票
    for j in range(n_bits):
        group = parity_flat[j * repeat : (j + 1) * repeat]
        bits.append(1 if sum(group) * 2 > len(group) else 0)

    return _bits_to_text(np.array(bits, dtype=np.uint8))


# 验证候选方案 (alpha, repeat)：按嵌入路径枚举。
# - (0.5, 1)：无损图像产物（PNG/BMP/TIFF）与历史产物
# - (0.05, 1..3)：视频帧与有损图像产物（鲁棒档，repeat 按容量可能降档）
# 实测依据见 scripts/experiment_watermark_transcode.py（2026-09-06 转码 / 2026-09-19 攻击矩阵）。
_VERIFY_SCHEMES: tuple[tuple[float, int], ...] = (
    (_WATERMARK_ALPHA, 1),
    (_VIDEO_ALPHA, 1),
    (_VIDEO_ALPHA, 2),
    (_VIDEO_ALPHA, 3),
)


def extract_watermark_best(image_np: np.ndarray, *, expected_length: int = 2048) -> str:
    """按 :data:`_VERIFY_SCHEMES` 逐档尝试，优先返回能通过验签（或含品牌）的载荷。

    取证场景必需：`verify_watermark()` 只回布尔，而拿到载荷才能去反查任务。
    直接调 `extract_watermark()` 走的是默认无损档参数，对鲁棒档产物
    （JPEG/WebP 图像、视频帧）只会读出乱码——即使水印明明验得过。

    Args:
        image_np: 待提取的图像。
        expected_length: 每档尝试的位数上限。

    Returns:
        str: 命中验签/品牌的载荷；都没命中时返回首个非空提取结果（可能是噪声，
            但至少让调用方看到"读到了什么"）。完全读不出返回空串。
    """
    keys = _load_verify_keys()
    fallback = ""
    for alpha, repeat in _VERIFY_SCHEMES:
        try:
            extracted = extract_watermark(image_np, expected_length=expected_length, alpha=alpha, repeat=repeat)
        except Exception as e:  # noqa: BLE001 — 单档异常不影响其余候选
            logger.debug(f"载荷提取失败 (alpha={alpha}, repeat={repeat}): {e}")
            continue
        if not extracted:
            continue
        if not fallback:
            fallback = extracted
        if keys:
            if any(_verify_signature(extracted, key) is not None for key in keys):
                return extracted
        elif _WATERMARK_BRAND in extracted:
            return extracted
    return fallback


def verify_watermark(image_np: np.ndarray, *, expected_length: int = 2048) -> bool:
    """验证图像是否包含可信的 SeedVR2 归属水印（v2 签名验证）。

    - 配置了密钥时（推荐）：严格验证 HMAC 签名，仅持有密钥嵌入的水印通过；
      旧版未签名水印将验证失败（无法证明真伪）。
    - 未配置密钥时：退化为弱检测（品牌字符串包含检查），仅作参考。
    - 依次尝试 :data:`_VERIFY_SCHEMES` 中的 (alpha, repeat) 组合，
      图像路径与视频路径（含容量降档）的产物均可验证，历史产物保持兼容。

    Args:
        image_np: 待验证的图像。
        expected_length: 提取位数上限（签名载荷较长，默认 2048 bit）。

    Returns:
        bool: True 表示检测到可信水印。
    """
    keys = _load_verify_keys()
    for alpha, repeat in _VERIFY_SCHEMES:
        try:
            extracted = extract_watermark(image_np, expected_length=expected_length, alpha=alpha, repeat=repeat)
            if not extracted:
                continue
            if keys:
                for key in keys:
                    if _verify_signature(extracted, key) is not None:
                        return True
            elif "SeedVR2" in extracted:
                return True
        except Exception as e:
            logger.debug(f"水印验证失败 (alpha={alpha}, repeat={repeat}): {e}")
    return False
