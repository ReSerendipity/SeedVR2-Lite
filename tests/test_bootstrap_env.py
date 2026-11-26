#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""Issue #211 回归：启动期 sys.path 隔离不得裁掉解释器自身的标准库。

用例同时锁两个方向：
1. 当前解释器前缀（即使位于 AppData / Program Files 下）必须保留；
2. 其它 Python 安装路径仍必须被移除——修复不能把隔离能力一起放弃。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.bootstrap_env import (
    filter_sys_path,
    is_foreign_path,
    is_under,
    protected_prefixes,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _bootstrap_prefix(tmp_path: Path) -> str:
    """模拟 Tauri NSIS currentUser 的默认安装路径。"""
    prefix = tmp_path / "AppData" / "Local" / "Programs" / "SeedVR2" / "app" / "bootstrap"
    prefix.mkdir(parents=True, exist_ok=True)
    return str(prefix)


def test_own_prefix_inside_appdata_is_preserved(tmp_path: Path) -> None:
    """自身 bootstrap 位于 AppData 下时，Lib/DLLs/site-packages 全部保留。"""
    bootstrap = _bootstrap_prefix(tmp_path)
    paths = [
        str(Path(bootstrap) / "python312.zip"),
        str(Path(bootstrap) / "DLLs"),
        str(Path(bootstrap) / "Lib"),
        str(Path(bootstrap) / "Lib" / "site-packages"),
        bootstrap,
    ]
    filtered = filter_sys_path(paths, str(Path(bootstrap).parent.parent))
    assert filtered == paths


@pytest.mark.skipif(os.name != "nt", reason="Windows path semantics")
def test_foreign_python_installations_are_removed(tmp_path: Path) -> None:
    """其它 AppData / Program Files / ProgramData 下的 Python 仍被剥离。"""
    bootstrap = _bootstrap_prefix(tmp_path)
    foreign = [
        str(tmp_path / "AppData" / "Roaming" / "Python" / "Python312" / "site-packages"),
        "C:\\Program Files\\OtherPython\\Lib",
        "C:\\ProgramData\\Python\\Lib",
    ]
    keep = [str(Path(bootstrap) / "Lib"), bootstrap]
    filtered = filter_sys_path(foreign + keep, str(Path(bootstrap).parent.parent))
    assert filtered == keep


def test_empty_and_relative_entries_preserved() -> None:
    """空串（cwd）与相对路径条目不能删，删除会改变导入语义。"""
    assert filter_sys_path(["", ".", "..", "relative_dir"], None) == ["", ".", "..", "relative_dir"]
    assert is_foreign_path("", None) is False
    assert is_foreign_path("relative_dir", None) is False


@pytest.mark.skipif(os.name != "nt", reason="Windows path semantics")
def test_case_and_slash_normalization(tmp_path: Path) -> None:
    """大小写与正反斜杠混写仍能识别为外部路径。"""
    bootstrap = _bootstrap_prefix(tmp_path)
    mixed = str(tmp_path).replace("\\", "/") + "/APPDATA/ROAMING/Python/Python312/site-packages"
    assert is_foreign_path(mixed, protected_prefixes(str(Path(bootstrap).parent.parent))) is True


def test_project_root_under_appdata_is_preserved(tmp_path: Path) -> None:
    """项目根也在 AppData 下时必须保留，否则应用代码整体不可导入。"""
    project_root = tmp_path / "AppData" / "Local" / "Programs" / "SeedVR2" / "app"
    project_root.mkdir(parents=True, exist_ok=True)
    package = str(project_root / "app" / "integrated_app")
    assert is_foreign_path(package, protected_prefixes(str(project_root))) is False


def test_prefix_does_not_match_sibling_directory(tmp_path: Path) -> None:
    """前缀匹配按目录边界，不做裸字符串前缀。"""
    prefix = str(tmp_path / "AppData" / "SeedVR2").lower()
    assert is_under(str(tmp_path / "AppData" / "SeedVR2-Other"), prefix) is False
    assert is_under(str(tmp_path / "AppData" / "SeedVR2" / "Lib"), prefix) is True


@pytest.mark.skipif(os.name != "nt", reason="Windows drive-root semantics")
def test_drive_root_prefix_is_not_overbroad() -> None:
    """盘符根前缀不应把整块盘都豁免（防止 rstrip 后变成 "c:"）。"""
    assert is_under("C:\\Users", "c:\\") is True
    assert is_under("D:\\Other", "c:\\") is False


def test_protected_prefixes_dedup_and_include_project(tmp_path: Path) -> None:
    project = str(tmp_path / "SeedVR2")
    prefixes = protected_prefixes(project)
    assert len(prefixes) == len(set(prefixes))
    assert os.path.normcase(os.path.realpath(project)) in prefixes
    assert os.path.normcase(os.path.realpath(sys.prefix)) in prefixes


@pytest.mark.integration
def test_start_portable_help_runs() -> None:
    """真实入口冒烟：脚本能导入共享过滤器并正常解析 --help。"""
    script = REPO_ROOT / "scripts" / "start_portable.py"
    result = subprocess.run(
        [sys.executable, "-X", "utf8", str(script), "--help"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "SeedVR2 便携版启动器" in result.stdout
    assert "No module named 'locale'" not in result.stderr
