#!/usr/bin/env python3
"""应用版本号单一事实来源。

版本号以 ``pyproject.toml`` 的 ``[project].version`` 为准，运行时按以下顺序解析：

1. 项目根目录 ``pyproject.toml`` 直读（源码运行 / 便携包场景，始终是活的真相）；
2. 已安装发行包元数据（``importlib.metadata``，wheel 安装场景的回退）；
3. 兜底常量 ``unknown``（结构异常时仅降级展示，绝不抛错阻断启动）。

背景：2026-08-30 之前 ``/api/system/ping`` 与 FastAPI 实例版本为硬编码 ``"1.0.0"``，
与 pyproject 的 1.5.0 漂移 5 个 minor（发布管理体系评估 P0-2）。便携包 core 组件
需包含 ``pyproject.toml``（build_portable_bundle.ps1 的 CoreIncludeFiles）。
"""

from __future__ import annotations

import tomllib
from functools import lru_cache
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _dist_version
from pathlib import Path

_FALLBACK_VERSION = "unknown"
_DIST_NAME = "seedvr2-turbo"


@lru_cache(maxsize=1)
def get_app_version() -> str:
    """返回应用版本号（进程内缓存一次）。

    Returns:
        str: 形如 ``1.5.0`` 的版本串；全部来源解析失败时返回 ``unknown``。
    """
    project_root = Path(__file__).resolve().parents[2]
    candidate = project_root / "pyproject.toml"
    if candidate.is_file():
        try:
            data = tomllib.loads(candidate.read_text(encoding="utf-8"))
            value = str(data["project"]["version"]).strip()
            if value:
                return value
        except (OSError, tomllib.TOMLDecodeError, KeyError, TypeError, ValueError):
            pass
    try:
        return _dist_version(_DIST_NAME)
    except PackageNotFoundError:
        return _FALLBACK_VERSION
