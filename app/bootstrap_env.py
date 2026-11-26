#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""启动期 ``sys.path`` 隔离：只剥离「别人的 Python」，绝不裁掉当前解释器自身。

背景（Issue #211）：便携版可能被安装到包含 ``AppData`` / ``Program Files`` /
``ProgramData`` 的目录——Tauri NSIS 的 ``installMode=currentUser`` 默认就是
``%LOCALAPPDATA%\\SeedVR2``。旧实现用路径子串黑名单直接过滤 ``sys.path``，
会把解释器自身的 ``sys.prefix``（打包态的 ``bootstrap\\``）一并命中并删除，
导致 ``bootstrap\\Lib`` 与 ``site-packages`` 全部丢失；此后任何**惰性**导入的
标准库模块（第一个撞上的是 ``gettext`` 里的 ``import locale``）都会
``ModuleNotFoundError``。

正确语义：
1. 当前解释器自身的前缀（``sys.prefix`` / ``sys.base_prefix`` /
   ``sys.exec_prefix`` / 项目根）永远保留，即使它位于 AppData 下；
2. 只有**其它** Python 安装路径（AppData、Program Files、ProgramData 下且
   不属于当前解释器）才被移除；
3. 空字符串与相对路径保留——``""`` 在 ``sys.path`` 里表示当前工作目录，
   删除会改变导入语义。

本模块只依赖标准库，必须能在「无 torch、无第三方包」的引导解释器上导入。
"""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence

# 需要剥离的外部 Python 安装位置（全部小写 + 反斜杠，配合 normcase 使用）。
_FOREIGN_TOKENS = ("\\appdata\\", "\\program files\\", "\\programdata\\")


def _normalize(path: str) -> str:
    """把路径归一化为 ``realpath + normcase``，并去掉末尾分隔符。

    Args:
        path: 待归一化的路径（可为空串）。

    Returns:
        str: 归一化后的路径；无法解析时返回空串。
    """
    if not path:
        return ""
    try:
        absolute = os.path.abspath(path)
        real = os.path.realpath(absolute)
    except (OSError, ValueError):
        return ""
    normalized = os.path.normcase(real)
    # 盘符根（"c:\\"）与 UNC 共享根保留原样，避免 rstrip 后变成 "c:" 这种
    # 过宽的前缀，把整块盘都当成「解释器自身」。
    if len(normalized) > 3:
        normalized = normalized.rstrip("\\/")
    return normalized


def protected_prefixes(project_root: str | None = None) -> tuple[str, ...]:
    """返回必须保留的「当前解释器自身」路径前缀。

    Args:
        project_root: 项目根目录（打包态为 ``<安装根>\\app``）；可为 None。

    Returns:
        tuple[str, ...]: 去重后的归一化前缀，供 :func:`is_foreign_path` 豁免。
    """
    candidates = [
        getattr(sys, "prefix", ""),
        getattr(sys, "base_prefix", ""),
        getattr(sys, "exec_prefix", ""),
        getattr(sys, "base_exec_prefix", ""),
        project_root or "",
    ]
    prefixes: list[str] = []
    for candidate in candidates:
        normalized = _normalize(candidate)
        if normalized and normalized not in prefixes:
            prefixes.append(normalized)
    return tuple(prefixes)


def is_under(path: str, prefix: str) -> bool:
    """判断 ``path`` 是否位于 ``prefix`` 之下（含相等）。

    Args:
        path: 待判断路径。
        prefix: 已归一化的前缀。

    Returns:
        bool: 位于其下返回 True。
    """
    normalized = _normalize(path)
    if not normalized or not prefix:
        return False
    # 盘符根/UNC 根本身以分隔符结尾；普通前缀需要补一个分隔符，避免
    # "SeedVR2" 误匹配到兄弟目录 "SeedVR2-Other"。
    separator = "" if prefix.endswith(os.sep) else os.sep
    return normalized == prefix or normalized.startswith(prefix + separator)


def is_foreign_path(
    path: str,
    prefixes: Sequence[str] | None = None,
    project_root: str | None = None,
) -> bool:
    """判断一个 ``sys.path`` 条目是否属于应剥离的外部 Python 路径。

    Args:
        path: ``sys.path`` 中的单个条目。
        prefixes: 已归一化的保护前缀；为 None 时按 ``project_root`` 现算。
        project_root: 计算保护前缀时使用的项目根。

    Returns:
        bool: 外部路径返回 True；当前解释器自身、空串、相对路径返回 False。
    """
    # "" 表示当前工作目录，"." / ".." 等相对条目同样有语义，一律保留。
    if not path or not os.path.isabs(path):
        return False

    protected = tuple(prefixes) if prefixes is not None else protected_prefixes(project_root)
    if any(is_under(path, prefix) for prefix in protected):
        return False

    probe = _normalize(path) + "\\"
    if probe == "\\":
        return False
    return any(token in probe for token in _FOREIGN_TOKENS)


def filter_sys_path(paths: Sequence[str], project_root: str | None = None) -> list[str]:
    """过滤 ``sys.path``，移除其它 Python 的安装路径。

    Args:
        paths: 原始 ``sys.path``。
        project_root: 项目根目录，作为保护前缀之一。

    Returns:
        list[str]: 保留顺序的过滤结果。
    """
    protected = protected_prefixes(project_root)
    return [path for path in paths if not is_foreign_path(path, protected)]


__all__ = [
    "filter_sys_path",
    "is_foreign_path",
    "is_under",
    "protected_prefixes",
]
