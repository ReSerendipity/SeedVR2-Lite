#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""下载管理：环境包（Python 运行时 + torch）与模型权重的安装、进度、取消、卸载。

瘦包（几十 MB）不含运行时与权重，用户装完后必须在这里完成安装才能推理。
本模块是 `/api/downloads/*` 与「下载管理」页面的执行层。

硬约束：**禁止顶层 import torch**。本模块必须能在引导解释器（无 torch）上运行，
否则用户永远装不上环境——下载管理页是瘦包唯一的自救通道。torch 版本信息一律
从 runtime 元信息文件读，不启动子进程。

与 VOSR2-Lite 同名模块的差异（移植时勿照抄）：
- 本仓权重是 `model/` 下的**文件**（`seedvr2_3b_mxfp8.safetensors`），不是子目录，
  组件 ID 形如 `3b/mxfp8`；VOSR2 是 `model/<checkpoint>/` 目录。
- 模型下载走 `scripts/download_model.py::download_model(model_size, precisions, ...)`，
  由它按前缀路由来源（fp16/fp8→HF，Comfy-Org 量化→ModelScope）。
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable
from typing import Any

from app.integrated_app.version import get_app_version

# 打包态：本文件位于 <app_dir>/app/integrated_app/，上溯三级即 app_dir
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ---------------------------------------------------------------- 目录 ----
RUNTIME_DIRNAME = "runtime"
# 瘦包内置解释器目录名（无 torch）。环境装进去后目录名不变，只多出 torch。
BOOTSTRAP_DIRNAME = "bootstrap"
CATALOG_FILENAME = "downloads_catalog.json"
RUNTIME_META_NAME = "RUNTIME.json"

DEFAULT_RELEASE_BASE = "https://github.com/ReSerendipity/SeedVR2-Lite/releases/download"

ProgressCb = Callable[[str, float, str], None]  # (stage, ratio 0..1, message)


def _exe_name() -> str:
    return "python.exe" if os.name == "nt" else "python"


def _runtime_dir() -> str:
    """当前生效的运行时目录：优先 runtime/，其次 bootstrap/（两者都可能有 python.exe）。"""
    runtime = os.path.join(PROJECT_ROOT, RUNTIME_DIRNAME)
    if os.path.isfile(os.path.join(runtime, _exe_name())):
        return runtime
    bootstrap = os.path.join(PROJECT_ROOT, BOOTSTRAP_DIRNAME)
    if os.path.isfile(os.path.join(bootstrap, _exe_name())):
        return bootstrap
    return runtime


def _python_dir() -> str:
    """当前进程的解释器所在目录（环境就装进这里）。"""
    return os.path.dirname(os.path.abspath(sys.executable or "")) or _runtime_dir()


def _catalog_path() -> str:
    return os.path.join(PROJECT_ROOT, CATALOG_FILENAME)


def _dir_stats(path: str) -> tuple[int, int]:
    files = 0
    total = 0
    if not os.path.isdir(path):
        return 0, 0
    for root, _dirs, names in os.walk(path):
        for name in names:
            try:
                total += os.path.getsize(os.path.join(root, name))
                files += 1
            except OSError:
                continue
    return files, total


def _read_runtime_meta() -> dict | None:
    meta_path = os.path.join(_runtime_dir(), RUNTIME_META_NAME)
    if not os.path.isfile(meta_path):
        return None
    try:
        with open(meta_path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _current_mode() -> str:
    exe = os.path.abspath(sys.executable or "")
    if not exe:
        return "system"
    for name, label in ((RUNTIME_DIRNAME, "full"), (BOOTSTRAP_DIRNAME, "bootstrap")):
        base = os.path.abspath(os.path.join(PROJECT_ROOT, name))
        if exe.startswith(base + os.sep) or exe == base:
            return label
    return "system"


def _version_ge(actual: str | None, required: str | None) -> bool:
    if not actual or not required:
        return True

    def parts(text: str) -> tuple[int, ...]:
        out: list[int] = []
        for chunk in str(text).split(".")[:3]:
            digits = "".join(ch for ch in chunk if ch.isdigit())
            out.append(int(digits) if digits else 0)
        return tuple(out)

    return parts(actual) >= parts(required)


def load_catalog() -> dict:
    if os.path.isfile(_catalog_path()):
        try:
            with open(_catalog_path(), encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict):
                return data
        except (OSError, ValueError):
            pass
    return {}


def runtime_catalog() -> dict | None:
    entry = load_catalog().get("runtime")
    return entry if isinstance(entry, dict) else None


def _load_model_script():
    """按路径加载 scripts/download_model.py（不在包内，无法常规 import）。"""
    path = os.path.join(PROJECT_ROOT, "scripts", "download_model.py")
    if not os.path.isfile(path):
        raise FileNotFoundError(f"缺少模型下载脚本: {path}")
    spec = importlib.util.spec_from_file_location("seedvr2_model_downloader", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载模型下载脚本: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ------------------------------------------------------------ 模型清单 ----
def model_root(config: dict) -> str:
    model_cfg = config.get("model", {}) or {}
    rel = model_cfg.get("pretrained_dir", "model")
    return rel if os.path.isabs(rel) else os.path.join(PROJECT_ROOT, rel)


def scan_model_components(config: dict) -> list[dict]:
    """扫描 `model.models.<size>.checkpoint_<precision>` 登记的文件，返回就位情况。

    权重是本仓 `model/` 根下的文件（非子目录），故按文件存在性判定。
    """
    model_cfg = config.get("model", {}) or {}
    root = model_root(config)
    default_size = str(model_cfg.get("default_size", ""))
    default_precision = str(model_cfg.get("default_precision", ""))
    out: list[dict] = []
    for size, spec in (model_cfg.get("models", {}) or {}).items():
        if not isinstance(spec, dict):
            continue
        for key, filename in spec.items():
            if not str(key).startswith("checkpoint_") or not filename:
                continue
            precision = str(key)[len("checkpoint_") :]
            path = os.path.join(root, str(filename))
            present = os.path.isfile(path)
            out.append(
                {
                    "id": f"{size}/{precision}",
                    "display_name": f"SeedVR2 {str(size).upper()} · {precision.upper()}",
                    "size": str(size),
                    "precision": precision,
                    "file": str(filename),
                    "present": present,
                    "size_bytes": os.path.getsize(path) if present else 0,
                    "min_vram_gb": spec.get(f"min_vram_{precision}_gb", 0),
                    "is_default": str(size) == default_size and precision == default_precision,
                }
            )
    out.sort(key=lambda item: (not item["is_default"], item["id"]))
    return out


def build_runtime_status() -> dict:
    runtime_dir = _runtime_dir()
    exe = os.path.join(runtime_dir, _exe_name())
    meta = _read_runtime_meta() or {}
    present = os.path.isfile(exe)
    files, size = _dir_stats(runtime_dir) if os.path.isdir(runtime_dir) else (0, 0)
    app_version = get_app_version()
    min_app = meta.get("min_app_version")
    return {
        "present": present,
        # 引导态下 python.exe 本来就在，但 torch 还没装——「可用」要看 RUNTIME.json，
        # 它只在环境安装成功后才写入。两者分开报，页面才能提示「需安装环境」。
        "installed": bool(meta),
        "path": runtime_dir,
        "executable": exe if present else None,
        "python_version": meta.get("python"),
        "torch_version": meta.get("torch"),
        "cuda": meta.get("cuda"),
        "runtime_version": meta.get("runtime_version"),
        "built_at": meta.get("built_at"),
        "file_count": files,
        "size_bytes": size,
        "compatible": _version_ge(app_version, min_app) if present else True,
        "min_app_version": min_app,
    }


# ------------------------------------------------------------ HTTP 下载 ----
def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download_file(
    url: str,
    target: str,
    on_progress: ProgressCb | None = None,
    stage: str = "download",
    should_cancel: Callable[[], bool] | None = None,
) -> None:
    part = target + ".part"
    resume = os.path.getsize(part) if os.path.exists(part) else 0
    headers = {"User-Agent": "SeedVR2Lite-Desktop"}
    if resume > 0:
        headers["Range"] = f"bytes={resume}-"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=60) as response:
        if resume > 0 and response.status != 206:
            resume = 0
        total = int(response.headers.get("Content-Length") or 0) + resume
        written = resume
        with open(part, "ab" if resume > 0 else "wb") as handle:
            while True:
                if should_cancel and should_cancel():
                    raise InterruptedError("已取消")
                chunk = response.read(1024 * 256)
                if not chunk:
                    break
                handle.write(chunk)
                written += len(chunk)
                if on_progress and total > 0:
                    on_progress(stage, min(written / total, 1.0), "")
    os.replace(part, target)


# ------------------------------------------------------------ 环境包安装 ----
def install_runtime(on_progress: ProgressCb | None = None, should_cancel: Callable[[], bool] | None = None) -> dict:
    """下载环境包并把 torch 离线装进当前解释器（bootstrap/ 自身）。"""
    catalog = runtime_catalog()
    if not catalog:
        raise RuntimeError("未找到环境包清单 downloads_catalog.json，无法确定下载地址")
    volumes = [v for v in catalog.get("volumes", []) if isinstance(v, dict)]
    if not volumes:
        raise RuntimeError("环境包清单缺少 volumes")

    base = str(catalog.get("base_url") or f"{DEFAULT_RELEASE_BASE}/{catalog.get('tag', 'runtime-v1')}")
    staging_parent = os.path.join(PROJECT_ROOT, ".downloads")
    work = os.path.join(staging_parent, "runtime")
    os.makedirs(work, exist_ok=True)

    def report(stage: str, ratio: float, message: str) -> None:
        if on_progress:
            on_progress(stage, ratio, message)

    parts: list[str] = []
    total_vols = len(volumes)
    for index, vol in enumerate(volumes):
        name = str(vol.get("name"))
        url = f"{base.rstrip('/')}/{name}"
        dest = os.path.join(work, name)
        expected = str(vol.get("sha256") or "").lower()
        if os.path.exists(dest) and expected and _sha256(dest) == expected:
            parts.append(dest)
            report("download", (index + 1) / total_vols, f"{name} 已存在，跳过")
            continue
        report("download", index / total_vols, f"下载 {name}")
        _download_file(
            url,
            dest,
            # i/n 都用默认参数绑定循环变量（B023）：闭包晚绑定会在多分卷并发回调时
            # 引用最后一轮的 name/index，进度会串卷。
            on_progress=lambda _s, r, _m, i=index, n=name: report(  # type: ignore[misc]
                "download", (i + r) / total_vols, f"下载 {n}"
            ),
            should_cancel=should_cancel,
        )
        if expected:
            actual = _sha256(dest)
            if actual != expected:
                raise RuntimeError(f"{name} 校验失败（期望 {expected[:12]}…，实际 {actual[:12]}…）")
        parts.append(dest)

    report("verify", 0.0, "合并分卷")
    archive = os.path.join(work, "runtime.zip")
    if len(parts) == 1:
        archive = parts[0]
    else:
        with open(archive, "wb") as out:
            for part in parts:
                with open(part, "rb") as handle:
                    shutil.copyfileobj(handle, out, 1024 * 1024)

    report("extract", 0.0, "解压环境包")
    wheels = os.path.join(work, "wheels")
    if os.path.isdir(wheels):
        shutil.rmtree(wheels, ignore_errors=True)
    os.makedirs(wheels, exist_ok=True)
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(wheels)

    # 装进 bootstrap/ 自身，不另起一份解释器：目录名不变，壳靠
    # bootstrap/Lib/site-packages/torch 判定它已是完整运行时。
    report("swap", 0.5, "安装 torch（离线）")
    target = _python_dir()
    packages = [str(p) for p in (catalog.get("packages") or []) if p] or ["torch", "torchvision"]
    command = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--no-index",
        "--find-links",
        wheels,
        "--no-warn-script-location",
        *packages,
    ]
    env = dict(os.environ, PIP_CONFIG_FILE="NUL")
    result = subprocess.run(command, capture_output=True, text=True, cwd=PROJECT_ROOT, env=env)
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "")[-400:]
        raise RuntimeError(f"torch 离线安装失败（退出码 {result.returncode}）：{tail}")

    meta = {
        "runtime_version": str(catalog.get("version", "1")),
        "python": catalog.get("python"),
        "torch": catalog.get("torch"),
        "cuda": catalog.get("cuda"),
        "built_at": catalog.get("built_at"),
        "min_app_version": catalog.get("min_app_version"),
        "installed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "installed_by_app_version": get_app_version(),
    }
    with open(os.path.join(target, RUNTIME_META_NAME), "w", encoding="utf-8") as meta_handle:
        json.dump(meta, meta_handle, ensure_ascii=False, indent=2)

    shutil.rmtree(work, ignore_errors=True)
    report("done", 1.0, "环境安装完成，请重启应用")
    return meta


# -------------------------------------------------------------- 模型安装 ----
def install_model(
    component: str,
    config: dict,
    on_progress: ProgressCb | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict:
    """下载 `size/precision` 指定的权重（如 `3b/mxfp8`），含共享 VAE 与文本嵌入。"""
    module = _load_model_script()
    parts = str(component).split("/", 1)
    if len(parts) != 2:
        raise ValueError(f"组件 ID 形如 3b/mxfp8，收到：{component}")
    model_size, precision = parts[0].strip(), parts[1].strip()

    def report(stage: str, ratio: float, message: str) -> None:
        if on_progress:
            on_progress(stage, ratio, message)

    if should_cancel and should_cancel():
        raise InterruptedError("已取消")
    report("download", 0.0, f"下载 {model_size}/{precision}")
    module.download_model(
        model_size=model_size,
        save_dir=model_root(config),
        precisions=[precision],
        with_vae=True,
        verify_hashes=True,
    )
    report("done", 1.0, f"{model_size}/{precision} 下载完成")
    return {"component": component, "model_size": model_size, "precision": precision}


def uninstall_runtime() -> None:
    """卸载运行环境。

    瘦包态（bootstrap/）不能整目录删——正在用这个解释器跑后端，删了应用再也起不来。
    此时只卸 torch 家族与 RUNTIME.json，把 bootstrap 还原成「未装环境」。
    """
    target = _runtime_dir()
    if not os.path.isdir(target):
        return
    if os.path.basename(os.path.normpath(target)) == BOOTSTRAP_DIRNAME:
        site = os.path.join(target, "Lib", "site-packages")
        if os.path.isdir(site):
            for entry in os.listdir(site):
                lowered = entry.lower()
                if lowered.startswith(("torch", "functorch", "nvidia_")) or entry in (
                    "torch",
                    "torchvision",
                    "torchaudio",
                    "torchgen",
                    "functorch",
                ):
                    shutil.rmtree(os.path.join(site, entry), ignore_errors=True)
        meta = os.path.join(target, RUNTIME_META_NAME)
        if os.path.isfile(meta):
            os.remove(meta)
        return
    shutil.rmtree(target, ignore_errors=True)


def uninstall_model(component: str, config: dict) -> None:
    """删除指定权重文件。共享文件（VAE/pos_emb/neg_emb）保留，可能仍被其他精度引用。"""
    parts = str(component).split("/", 1)
    if len(parts) != 2:
        return
    model_size, precision = parts[0].strip(), parts[1].strip()
    model_cfg = config.get("model", {}) or {}
    spec = (model_cfg.get("models", {}) or {}).get(model_size)
    if not isinstance(spec, dict):
        return
    filename = spec.get(f"checkpoint_{precision}")
    if not filename:
        return
    path = os.path.join(model_root(config), str(filename))
    if os.path.isfile(path):
        os.remove(path)


# ------------------------------------------------------------ 任务管理 ----
class DownloadTask:
    def __init__(self, component: str, kind: str, target: str) -> None:
        self.component = component
        self.kind = kind
        self.target = target
        self.stage = "queued"
        self.ratio = 0.0
        self.message = ""
        self.error: str | None = None
        self.finished = False
        self.started_at = time.time()
        self._cancel = threading.Event()

    def to_dict(self) -> dict:
        return {
            "component": self.component,
            "kind": self.kind,
            "target": self.target,
            "stage": self.stage,
            "ratio": round(self.ratio, 4),
            "message": self.message,
            "error": self.error,
            "finished": self.finished,
            "elapsed_seconds": round(time.time() - self.started_at, 1),
        }

    def request_cancel(self) -> None:
        self._cancel.set()

    def _progress(self, stage: str, ratio: float, message: str) -> None:
        self.stage = stage
        self.ratio = ratio
        self.message = message

    def run(self, config: dict) -> None:
        try:
            if self.kind == "runtime":
                install_runtime(self._progress, self._cancel.is_set)
            else:
                install_model(self.target, config, self._progress, self._cancel.is_set)
            self.stage = "done"
            self.ratio = 1.0
        except InterruptedError as exc:
            self.stage = "cancelled"
            self.error = str(exc)
        except (urllib.error.URLError, OSError) as exc:
            self.stage = "error"
            self.error = f"网络错误: {exc}"
        except Exception as exc:  # noqa: BLE001 - 错误需回传给用户，不能让线程静默死
            self.stage = "error"
            self.error = str(exc)
        finally:
            self.finished = True


class DownloadManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tasks: dict[str, DownloadTask] = {}

    def start(self, kind: str, target: str, config: dict) -> DownloadTask:
        component = "runtime" if kind == "runtime" else f"model:{target}"
        with self._lock:
            existing = self._tasks.get(component)
            if existing and not existing.finished:
                return existing
            task = DownloadTask(component, kind, target)
            self._tasks[component] = task
        thread = threading.Thread(target=task.run, args=(config,), name=f"download-{component}", daemon=True)
        thread.start()
        return task

    def progress(self) -> list[dict]:
        with self._lock:
            return [task.to_dict() for task in self._tasks.values()]

    def cancel(self, component: str) -> bool:
        task = self._tasks.get(component)
        if not task or task.finished:
            return False
        task.request_cancel()
        return True


_MANAGER: DownloadManager | None = None
_MANAGER_LOCK = threading.Lock()


def get_download_manager() -> DownloadManager:
    global _MANAGER
    if _MANAGER is None:
        with _MANAGER_LOCK:
            if _MANAGER is None:
                _MANAGER = DownloadManager()
    return _MANAGER


def build_status(config: dict) -> dict:
    runtime = build_runtime_status()
    models = scan_model_components(config)
    return {
        "app_version": get_app_version(),
        "mode": _current_mode(),
        # 「能跑推理」= 环境装好了（RUNTIME.json）+ 至少一个权重就位；
        # 只看 python.exe 会把引导态误判为就绪。
        "ready": bool(runtime["installed"] and runtime["compatible"] and any(m["present"] for m in models)),
        "runtime": runtime,
        "models": models,
        "model_root": model_root(config),
    }


def catalog_view(config: dict) -> dict:
    runtime = runtime_catalog()
    models = []
    for item in scan_model_components(config):
        models.append(
            {
                "id": item["id"],
                "display_name": item["display_name"],
                "present": item["present"],
                "size_bytes": item["size_bytes"],
                "min_vram_gb": item["min_vram_gb"],
                "is_default": item["is_default"],
                "source": "huggingface / modelscope",
            }
        )
    return {"runtime": runtime, "models": models, "catalog_present": bool(load_catalog())}


def _unused(*args: Any) -> None:
    return None
