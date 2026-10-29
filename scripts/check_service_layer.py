#!/usr/bin/env python
"""服务层边界门禁：app/integrated_app/services/ 不得依赖 Web 框架的请求/响应对象。

口径来源（项目自己的明文约束，不是外部规范）：
  - app/integrated_app/services/__init__.py 的「架构约束」一节
  - app/integrated_app/services/restore_service.py 模块头同类声明
被该目录依赖的方向是「路由层 → 服务层」，服务层反向 import fastapi/starlette 会把
HTTP 语义带进可复用逻辑，并使路由之外的调用方（CLI、定时任务、测试夹具）无法复用。

同目录相邻约束都有专门脚本承接（根目录结构 check_layout.py、本机绝对路径
check_no_hardcoded_paths.py、幻影引用 check_local_only_refs.py），本脚本补齐这一条。

用法：
    python scripts/check_service_layer.py                 # 检查默认目录
    python scripts/check_service_layer.py --root <dir>     # 检查指定目录（回归用例用）
退出码：0 = 通过；1 = 存在违例（逐条打印 文件:行号 与 import 内容）。
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TARGET = REPO_ROOT / "app" / "integrated_app" / "services"

# 被禁的顶层包：服务层要处理领域对象，不能处理 HTTP 对象
FORBIDDEN_TOP_LEVEL = {"fastapi", "starlette"}


def scan_file(path: Path) -> list[tuple[int, str]]:
    """返回该文件里所有对 Web 框架的 import（行号, 原文）。"""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError) as exc:  # pragma: no cover - 解析失败按违例处理
        return [(0, f"无法解析（{exc}）")]

    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in FORBIDDEN_TOP_LEVEL:
                    hits.append((node.lineno, f"import {alias.name}"))
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod.split(".")[0] in FORBIDDEN_TOP_LEVEL:
                hits.append((node.lineno, f"from {mod} import ..."))
    return hits


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="服务层不得依赖 Web 框架的请求/响应对象")
    parser.add_argument("--root", type=Path, default=DEFAULT_TARGET, help="要检查的目录（默认服务层）")
    args = parser.parse_args(argv)

    target: Path = args.root
    if not target.is_dir():
        print(f"[service-layer] 目录不存在，跳过检查: {target}", file=sys.stderr)
        return 0

    violations: list[str] = []
    files = sorted(p for p in target.rglob("*.py"))
    for path in files:
        for lineno, stmt in scan_file(path):
            rel = path.relative_to(REPO_ROOT).as_posix() if path.is_relative_to(REPO_ROOT) else path.as_posix()
            violations.append(f"{rel}:{lineno} -> {stmt}")

    if violations:
        print(
            f"[service-layer] 发现 {len(violations)} 处服务层对 Web 框架的依赖"
            "（口径见 app/integrated_app/services/__init__.py 架构约束）：",
        )
        for line in violations:
            print(f"  {line}")
        print(
            "\n修复方式：把请求/响应对象的拆装留在路由层，服务层只接收领域参数；"
            "确需 HTTP 语义时改为由路由层传入已解析的值。"
        )
        return 1

    print(f"[service-layer] 通过：{len(files)} 个服务层文件，未发现 Web 框架依赖")
    return 0


if __name__ == "__main__":
    sys.exit(main())
