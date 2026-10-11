#!/usr/bin/env python3
"""SeedVR2 预训练模型下载脚本。

从 HuggingFace 下载与 config.yaml 引用的文件名完全一致的权重文件，
并直接保存到 model/ 根目录（与 model_manager / engine 的
查找逻辑对齐，见 app/integrated_app/model_manager.py check_model_exists）。

命令行用法:
    # 下载 3B + VAE + 文本嵌入（默认行为，完整可运行最小集合）
    python scripts/download_model.py --size 3b

    # 下载 7B 或 7B-Sharp
    python scripts/download_model.py --size 7b
    python scripts/download_model.py --size 7b_sharp

    # 指定自定义保存目录
    python scripts/download_model.py --size 3b --save-dir D:/shared_models

    # 只下主权重，不下共享的 VAE / 嵌入
    python scripts/download_model.py --size 3b --no-vae

    # 精确只取便携包需要的最小可运行集合（FP8 主权重 + 共享组件），不拖下 FP16
    python scripts/download_model.py --files seedvr2_ema_3b_fp8_e4m3fn.safetensors ema_vae_fp16.safetensors pos_emb.pt neg_emb.pt

    # Comfy-Org 量化精度（int8_convrot / mxfp8 / nvfp4 从 ModelScope 的 Comfy-Org/SeedVR2 下载；
    # 注意体积：3B 三件约 6.7 GB，7B 档三件约 21 GB）
    python scripts/download_model.py --size 3b --precisions int8_convrot mxfp8 nvfp4 --no-vae

特性:
    - 幂等：已存在的文件自动跳过，不会重复下载
    - 断点续传：HF 由 huggingface_hub 内部机制保证；ModelScope 直下用 .part 续传
    - 双源路由：seedvr2_ema_* 走 HuggingFace（--repo），Comfy-Org 量化文件走 ModelScope，
      文件名与哈希以 config.yaml 为唯一事实来源，两套转换产物互不兼容、严禁混用
    - 镜像加速：--endpoint https://hf-mirror.com 或 HF_ENDPOINT 环境变量（P1-3）
    - 下载后校验：立即按 config.yaml 中的 sha256_* 期望哈希校验完整性，
      损坏文件当场暴露，而不是拖到推理加载时才失败（P1-3）
    - 文件名与 config.yaml 中 model.models.<size> 的引用完全一致，
      下载完成后无需任何改名/移动即可被应用识别
"""

import argparse
import hashlib
import importlib.util
import os
import sys
from pathlib import Path

# 各模型尺寸对应的权重文件名（与 config.yaml model.models.<size> 保持一致）
_MODEL_FILES: dict[str, list[str]] = {
    "3b": [
        "seedvr2_ema_3b_fp16.safetensors",
        "seedvr2_ema_3b_fp8_e4m3fn.safetensors",
    ],
    "7b": [
        "seedvr2_ema_7b_fp16.safetensors",
        "seedvr2_ema_7b_fp8_e4m3fn.safetensors",
    ],
    "7b_sharp": [
        "seedvr2_ema_7b_sharp_fp16.safetensors",
        "seedvr2_ema_7b_sharp_fp8_e4m3fn.safetensors",
    ],
}

# 所有尺寸共用的 VAE 与文本嵌入文件（config.yaml 每个尺寸都会引用）
_SHARED_FILES: list[str] = [
    "ema_vae_fp16.safetensors",
    "pos_emb.pt",
    "neg_emb.pt",
]

# 默认下载来源仓库（社区整理的 ComfyUI 版，文件名与上述清单完全一致）
# 可通过 --repo 覆盖为其他镜像仓库
_DEFAULT_REPO = "numz/SeedVR2_comfyUI"

# Comfy-Org 转包/量化权重来源（ModelScope，直连 resolve URL 下载）。
# config.yaml 的 fp16/fp8 用 numz 的 seedvr2_ema_*，三种量化精度用 Comfy-Org 的
# seedvr2_*_{int8_convrot,mxfp8,nvfp4}，两套文件名互不兼容，按前缀自动路由来源。
_COMFY_ORG_REPO = "Comfy-Org/SeedVR2"
_COMFY_ORG_SUBFOLDER = "diffusion_models"
_MODELSCOPE_BASE = "https://modelscope.cn/models"

# 精度标识 → config.yaml checkpoint 键（--precisions 的合法取值与文件名来源）
_PRECISION_CONFIG_KEYS: dict[str, str] = {
    "fp16": "checkpoint_fp16",
    "fp8": "checkpoint_fp8",
    "int8_convrot": "checkpoint_int8_convrot",
    "mxfp8": "checkpoint_mxfp8",
    "nvfp4": "checkpoint_nvfp4",
}
_ALL_PRECISIONS: list[str] = list(_PRECISION_CONFIG_KEYS)
# 默认仍只取 fp16/fp8（保持旧行为；量化包 3B 三件约 6.7 GB，需显式 --precisions 选择）
_DEFAULT_PRECISIONS: list[str] = ["fp16", "fp8"]


def _is_comfy_org(filename: str) -> bool:
    """按文件名判断是否来自 Comfy-Org 量化包（区别于 numz 的 seedvr2_ema_* 前缀）。"""
    return filename.startswith("seedvr2_") and not filename.startswith("seedvr2_ema_")


def _resolve_source(filename: str, default_repo: str) -> tuple[str, str | None]:
    """返回 (repo, subfolder)：Comfy-Org 文件走 ModelScope 子目录，其余走 --repo 指定的 HF 仓。"""
    if _is_comfy_org(filename):
        return _COMFY_ORG_REPO, _COMFY_ORG_SUBFOLDER
    return default_repo, None


def _http_download(url: str, target: Path, chunk: int = 1 << 20) -> None:
    """ModelScope 直连流式下载：.part 断点续传，完成后原子改名。

    进度用 rich.progress 展示（rich 为项目声明依赖）：从已有 .part 字节数续起；
    服务端不支持 Range 时回退整文件重下，进度条同样回零。
    """
    import requests
    from rich.progress import (
        BarColumn,
        DownloadColumn,
        Progress,
        SpinnerColumn,
        TransferSpeedColumn,
    )

    part = target.with_name(target.name + ".part")
    start = part.stat().st_size if part.exists() else 0
    headers = {"Range": f"bytes={start}-"} if start else {}
    with requests.get(url, headers=headers, stream=True, timeout=60) as r:
        r.raise_for_status()
        mode = "wb"
        if start and r.status_code == 206:
            mode = "ab"  # 服务端支持续传才追加；否则重头下载
        else:
            start = 0
        # Content-Length 在 Range 请求下是剩余字节数，进度总量要加回已续传部分
        remaining = int(r.headers.get("Content-Length", 0))
        total = start + remaining if remaining else 0
        with (
            open(part, mode) as f,
            Progress(
                SpinnerColumn(),
                "[progress.description]{task.description}",
                BarColumn(),
                DownloadColumn(),
                TransferSpeedColumn(),
                transient=True,
            ) as progress,
        ):
            task_id = progress.add_task(f"下载 {target.name}", total=total or None)
            if start:
                progress.advance(task_id, start)
            for blk in r.iter_content(chunk_size=chunk):
                if blk:
                    f.write(blk)
                    progress.advance(task_id, len(blk))
    os.replace(part, target)


def _download_file(
    repo_id: str,
    filename: str,
    save_dir: Path,
    allow_missing: bool = False,
    subfolder: str | None = None,
) -> bool:
    """下载单个文件到 save_dir 根下，已存在则跳过。

    Args:
        repo_id: 仓库 ID（HuggingFace 或 ModelScope）。
        filename: 仓库内的文件名。
        save_dir: 保存目录（文件直接写入该目录根下）。
        allow_missing: 仓库中缺失该文件时是否允许静默跳过（用于 VAE/嵌入等
                      可能位于其他仓库的文件）。
        subfolder: 仓库内子目录。给出时视为 ModelScope 来源，走直连 HTTP 下载，
                  文件落地到 save_dir 根（config 与引擎按裸文件名查找）。

    Returns:
        bool: 下载或已存在返回 True；仓库缺失且 allow_missing 时返回 False。
    """
    target = save_dir / Path(filename).name
    if target.exists() and target.stat().st_size > 0:
        print(f"  [跳过] 已存在: {target.name} ({target.stat().st_size / 1024**3:.2f} GB)")
        return True

    try:
        if subfolder:
            url = f"{_MODELSCOPE_BASE}/{repo_id}/resolve/master/{subfolder}/{filename}"
            print(f"  [下载·ModelScope] {filename} ...")
            _http_download(url, target)
        else:
            from huggingface_hub import hf_hub_download

            print(f"  [下载·HuggingFace] {filename} ...")
            hf_hub_download(
                repo_id=repo_id,
                filename=filename,
                local_dir=str(save_dir),
                local_dir_use_symlinks=False,
            )
        print(f"  [完成] {filename}")
        return True
    except Exception as e:
        if allow_missing:
            print(f"  [跳过] 仓库中未找到 {filename}（不影响其他文件）: {e}")
            return False
        raise


def _load_checkpoint_files(config_path: Path, model_size: str, precisions: list[str]) -> list[str]:
    """从 config.yaml 按「尺寸 + 精度清单」解析要下载的权重文件名。

    文件名以 config.yaml ``model.models.<size>.checkpoint_<precision>`` 为唯一来源，
    避免与下载清单漂移；某精度键缺失则跳过。

    Args:
        config_path: config.yaml 路径。
        model_size: 模型尺寸键（"3b"/"7b"/"7b_sharp"）。
        precisions: 精度标识清单。

    Returns:
        list[str]: 去重后的文件名清单（按 precisions 顺序）。
    """
    import yaml

    if not config_path.exists():
        raise RuntimeError(f"未找到配置文件 {config_path}，无法解析 {model_size} 的权重清单")
    with open(config_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    entry = ((cfg.get("model") or {}).get("models") or {}).get(model_size)
    if not isinstance(entry, dict):
        raise ValueError(f"config.yaml 未找到模型条目: {model_size}")
    files: list[str] = []
    for prec in precisions:
        key = _PRECISION_CONFIG_KEYS.get(prec)
        if not key:
            raise ValueError(f"未知精度: {prec}，可选: {_ALL_PRECISIONS}")
        name = entry.get(key)
        if name and name not in files:
            files.append(str(name))
    return files


def download_model(
    model_size: str = "3b",
    save_dir: str = "model",
    repo_id: str = _DEFAULT_REPO,
    with_vae: bool = True,
    only_files: list[str] | None = None,
    verify_hashes: bool = True,
    precisions: list[str] | None = None,
) -> None:
    """下载指定尺寸/精度的 SeedVR2 模型权重到根目录（fp16/fp8 走 HF，量化走 ModelScope）。

    Args:
        model_size: 模型参数规模，可选 "3b" / "7b" / "7b_sharp"。默认为 "3b"。
        save_dir: 模型保存的根目录路径（权重文件直接写入该目录根下）。默认为 "model"。
        repo_id: HuggingFace 仓库 ID，默认为社区整理仓库 numz/SeedVR2_comfyUI。
                 仅用于 fp16/fp8 与共享组件；Comfy-Org 量化文件固定走 ModelScope。
        with_vae: 是否同时下载共享的 VAE 与文本嵌入文件。默认为 True。
        only_files: 精确指定要下载的文件名列表，给出时忽略 model_size 清单与 with_vae。
                    用于便携包只取单一精度（如只要 FP8）而不拖下整组权重。默认为 None。
                    注意：only_files 模式下**任何文件下载不到都会抛错终止**，不允许静默跳过，
                    因为调用方（便携包 CI）把「全部权重就在该仓库」当作前提。
        verify_hashes: 下载后按 config.yaml 的 sha256_* 期望哈希校验完整性。默认 True。
        precisions: 精度清单（如 ["fp16","nvfp4"]）。仅在未指定 only_files 时生效；
                    为 None 时保持旧行为只取 fp16/fp8。量化精度文件名从 config.yaml 解析。

    Returns:
        None

    Raises:
        ValueError: model_size / precisions 非法时抛出（only_files 未给定时）。
        RuntimeError: 文件缺失或 SHA256 校验失败时抛出。
    """
    config_path = Path(__file__).resolve().parent.parent / "config.yaml"
    if only_files:
        files = list(only_files)
    else:
        if model_size not in _MODEL_FILES:
            print(f"无效的模型大小: {model_size}，可选: {list(_MODEL_FILES.keys())}")
            return
        files = _load_checkpoint_files(config_path, model_size, precisions or _DEFAULT_PRECISIONS)
        if with_vae:
            files += _SHARED_FILES

    # huggingface_hub 仅在存在 HF 来源文件时才必须（Comfy-Org 量化走 ModelScope 直下）
    if any(not _is_comfy_org(f) for f in files) and importlib.util.find_spec("huggingface_hub") is None:
        print("请先安装 huggingface_hub: pip install huggingface_hub")
        return

    save_path = Path(save_dir)
    save_path.mkdir(parents=True, exist_ok=True)

    print(f"HuggingFace 源: {repo_id} ｜ Comfy-Org 量化源: {_COMFY_ORG_REPO}@ModelScope")
    print(f"保存目录: {save_path.resolve()}")
    print(f"共 {len(files)} 个文件（已存在会自动跳过）:")
    print()

    missing: list[str] = []
    sources: dict[str, tuple[str, str | None]] = {}
    for filename in files:
        # only_files 模式不允许静默跳过：便携包 CI 以「全部权重就在该仓库」为前提，
        # 任何文件缺失都必须失败并暴露给调用方，否则会产出"假成功"（此前就漏了 pos/neg）。
        allow = (filename in _SHARED_FILES) and not only_files
        repo, subfolder = _resolve_source(filename, repo_id)
        sources[filename] = (repo, subfolder)
        if not _download_file(repo, filename, save_path, allow_missing=allow, subfolder=subfolder):
            missing.append(filename)

    print()
    print("=" * 60)
    print("下载结束。请确认以下文件都已存在于保存目录根下：")
    ok = True
    for f in files:
        exists = (save_path / f).exists()
        mark = "✓" if exists else "✗"
        print(f"  {mark} {f}")
        ok = ok and exists
    print("=" * 60)

    if missing or not ok:
        raise RuntimeError(f"以下权重文件未能下载到（或下载后缺失）：{', '.join(missing or files)}")

    # 下载后立即按 config.yaml 的 sha256_* 期望哈希校验（成本治理 P1-3）
    if verify_hashes:
        verify_downloaded_hashes(save_path, files, config_path)

    # 下载产物写权重 sidecar（数据治理 P2-2：来源可追溯）——放在哈希校验之后，
    # 保证写入的 sidecar 对应的是已通过完整性校验的文件；幂等（哈希一致即跳过）
    write_download_sidecars(save_path, files, sources)
    # 双命名族并存警告（评估 P2-6）：ema_ 前缀族与裸名族互不兼容，并存时提示
    warn_on_dual_naming(save_path, files)


def warn_on_dual_naming(save_dir: Path, files: list[str]) -> None:
    """检测同一权重双命名族并存（评估 P2-6），提示裸名覆盖/混用风险。

    numz 族以 ``seedvr2_ema_`` 前缀命名、Comfy-Org 族为裸名（``seedvr2_*``）；
    两套文件名互不兼容、严禁混用。引擎按 config.yaml ``checkpoint_*`` 主字段
    加载不受影响，但人工拷贝/手动改名场景容易踩坑，此处显式警告。
    """
    for filename in files:
        alt: str | None = None
        if filename.startswith("seedvr2_ema_"):
            alt = "seedvr2_" + filename[len("seedvr2_ema_") :]
        elif filename.startswith("seedvr2_"):
            alt = "seedvr2_ema_" + filename[len("seedvr2_") :]
        if alt and (save_dir / filename).exists() and (save_dir / alt).exists():
            print(
                f"  [警告] 双命名族并存: {filename} 与 {alt}（互不兼容的权重，"
                f"引擎按 config.yaml checkpoint_* 主字段加载；请勿混用/手动改名）"
            )


def write_download_sidecars(save_dir: Path, files: list[str], sources: dict[str, tuple[str, str | None]]) -> None:
    """为下载产物写权重 sidecar（``<file>.meta.json``），记录来源仓库与内容哈希。

    幂等：sidecar 已存在且其哈希与文件当前哈希一致时跳过，避免重复计算 GB 级
    文件的 SHA256；文件缺失或大小为 0 时跳过。model/ 目录已被 .gitignore 覆盖，
    写入不会污染工作树。

    Args:
        save_dir: 权重保存目录。
        files: 本次下载/确认的文件名清单。
        sources: 文件名 → (repo, subfolder) 来源映射（_resolve_source 的输出）。
    """
    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from training.weight_sidecar import build_sidecar, read_sidecar, sidecar_path_for, write_sidecar

    for filename in files:
        target = save_dir / filename
        if not target.exists() or target.stat().st_size == 0:
            continue
        repo, subfolder = sources.get(filename, ("", None))
        existing = read_sidecar(str(target))
        if existing and existing.get("sha256") == _sha256_of(target):
            print(f"  [sidecar] 已存在且哈希一致，跳过: {sidecar_path_for(str(target))}")
            continue
        meta = build_sidecar(
            str(target),
            extra={"source_repo": repo, "source_subfolder": subfolder or ""},
        )
        written = write_sidecar(meta, str(target))
        print(f"  [sidecar] 已写入: {written}")


def _load_expected_hashes(config_path: Path) -> dict[str, str]:
    """从 config.yaml 收集「文件名 → 期望 sha256」映射。

    遍历 model.models 各条目，收集主权重（fp16/fp8）与共享组件
    （vae / pos_emb / neg_emb）的期望哈希。

    Args:
        config_path: config.yaml 路径。

    Returns:
        dict: 文件名到期望哈希的映射（缺失字段的文件不进入映射）。
    """
    import yaml

    if not config_path.exists():
        print(f"  [警告] 未找到 {config_path}，跳过 SHA256 校验")
        return {}

    with open(config_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    hash_pairs = tuple((key, f"sha256_{prec}") for prec, key in _PRECISION_CONFIG_KEYS.items()) + (
        ("vae_checkpoint", "sha256_vae"),
        ("pos_emb", "sha256_pos_emb"),
        ("neg_emb", "sha256_neg_emb"),
    )
    hashes: dict[str, str] = {}
    for entry in ((cfg.get("model") or {}).get("models") or {}).values():
        if not isinstance(entry, dict):
            continue
        for name_key, hash_key in hash_pairs:
            name = entry.get(name_key)
            expected = entry.get(hash_key)
            if name and expected:
                hashes[str(name)] = str(expected)
    return hashes


def _sha256_of(path: Path) -> str:
    """流式计算文件 SHA256（避免大权重整文件读入内存）。"""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_downloaded_hashes(save_dir: Path, files: list[str], config_path: Path) -> None:
    """对下载产物按 config.yaml 期望哈希逐一校验（成本治理 P1-3）。

    Args:
        save_dir: 下载目录。
        files: 本次下载清单内的文件名。
        config_path: config.yaml 路径（期望哈希来源）。

    Raises:
        RuntimeError: 任一文件的 SHA256 与期望值不符时抛出（损坏文件已被删除）。
    """
    expected_map = _load_expected_hashes(config_path)
    if not expected_map:
        return

    verified = skipped = 0
    print()
    print("开始 SHA256 完整性校验（期望哈希来自 config.yaml）:")
    for filename in files:
        expected = expected_map.get(filename)
        if not expected:
            skipped += 1
            continue
        target = save_dir / filename
        if not target.exists() or target.stat().st_size == 0:
            continue  # 缺失文件已由存在性检查负责报错
        actual = _sha256_of(target)
        if actual.lower() != expected.lower():
            # MLOps P2-5：校验失败立即删除残缺文件——防止重跑时旧坏文件被续传
            # 逻辑当作「已下载」跳过、或残留在 model/ 目录（加载侧 verify_model_files 为双保险）
            target.unlink(missing_ok=True)
            raise RuntimeError(
                f"SHA256 校验失败: {filename}\n"
                f"  期望: {expected}\n"
                f"  实际: {actual}\n"
                f"该文件可能在下载或镜像过程中损坏，已删除损坏文件，请重新下载。"
            )
        verified += 1
        print(f"  [校验] {filename}: SHA256 OK")
    print(f"SHA256 校验完成: {verified} 个通过, {skipped} 个无期望哈希（跳过）")


if __name__ == "__main__":
    # Windows 控制台/CI runner 的默认编码可能是 cp1252/GBK，本脚本输出含中文，
    # 不显式改 UTF-8 会在 print 处抛 UnicodeEncodeError（与是否下载成功无关）。
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="SeedVR2 模型下载工具")
    parser.add_argument(
        "--size",
        default="3b",
        choices=["3b", "7b", "7b_sharp"],
        help="模型大小 (默认 3b)",
    )
    parser.add_argument("--save-dir", default="model", help="保存目录 (默认 model)")
    parser.add_argument("--repo", default=_DEFAULT_REPO, help="HuggingFace 仓库 ID (默认 numz/SeedVR2_comfyUI)")
    parser.add_argument(
        "--endpoint",
        default=None,
        help="HuggingFace endpoint，如 https://hf-mirror.com（国内镜像加速，P1-3）。"
        "未指定时保留 HF_ENDPOINT 环境变量或官方默认值",
    )
    parser.add_argument(
        "--no-verify",
        action="store_true",
        help="跳过下载后的 SHA256 完整性校验（默认开启校验）",
    )
    parser.add_argument(
        "--no-vae",
        action="store_true",
        help="不下载共享的 VAE / 文本嵌入文件",
    )
    parser.add_argument(
        "--files",
        nargs="+",
        default=None,
        metavar="NAME",
        help="只下载列出的文件名（忽略 --size/--no-vae 的整组清单），" "用于便携打包只取单一精度权重",
    )
    parser.add_argument(
        "--precisions",
        nargs="+",
        default=None,
        metavar="PRECISION",
        choices=_ALL_PRECISIONS,
        help="精度清单（默认 fp16 fp8）。int8_convrot/mxfp8/nvfp4 从 ModelScope 的 "
        "Comfy-Org/SeedVR2 下载，文件名取自 config.yaml；注意量化包体积（3B 三件约 6.7 GB）",
    )
    args = parser.parse_args()
    # HF_ENDPOINT 必须在 huggingface_hub 导入前设置（库在 import 时读取该变量）
    if args.endpoint:
        os.environ["HF_ENDPOINT"] = args.endpoint
        print(f"使用 HuggingFace endpoint: {args.endpoint}")
    download_model(
        args.size,
        args.save_dir,
        args.repo,
        with_vae=not args.no_vae,
        only_files=args.files,
        verify_hashes=not args.no_verify,
        precisions=args.precisions,
    )
