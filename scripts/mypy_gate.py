#!/usr/bin/env python3
"""可移植地定位解释器并执行 mypy —— ``.pre-commit-config.yaml`` 里 mypy 钩子的入口。

要修的故障
----------
钩子原先写死 ``entry: .venv/Scripts/python.exe -m mypy app/integrated_app``。那是
Windows venv 的布局，而且只有"当前目录恰好是主 checkout"时它才存在：linked worktree
（``git worktree add``）没有自己的 ``.venv``，POSIX 主机也没有 ``Scripts/`` 这一层。
于是提交被 ``Executable `.venv/Scripts/python.exe` not found`` 拦下 —— 一次从没跑过
类型检查的失败，既没检查出东西，也挡住了无关改动。

解释器顺序
----------
与 ``.githooks/pre-commit`` 的四级回退同序（venv 优先，再落到 PATH）：

1. ``.venv/Scripts/python.exe``  Windows venv
2. ``.venv/bin/python``          POSIX venv
3. ``python``                    PATH
4. ``python3``                   PATH

多一条守卫：按上述优先级取**第一个真的装了 mypy 的**解释器。因为"解释器在、mypy 不在"
与"解释器不在"对提交者表现为同一条 not found，逐个试过去才能让本地钩子真正跑到。

三种结局
--------
- 四级全都找不到解释器：本机没有 Python，钩子无从执行 → 打印原因后放行；类型门禁的
  权威执行点是 ``.github/workflows/ci.yml`` 的 typecheck job，不由本地钩子兜底。
  但别把它读成「推上去 CI 就会接手」：本仓全部工作流的 ``push`` 触发都限定在
  ``main`` / ``master``（或 ``v*`` 标签），**推功能分支不产生任何 check run**，
  typecheck 真正开始跑是在「开向 main 的 PR」或「推 main」那一刻。
- 找到了 Python 但没有一个装了 mypy：真缺依赖，退出码 1 并给出安装命令，不静默放行。
- mypy 真的跑了：退出码原样透传，类型错误照样拦。
"""

from __future__ import annotations

import contextlib
import shutil
import subprocess
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and hasattr(_stream, "reconfigure"):
        with contextlib.suppress(OSError, ValueError):
            _stream.reconfigure(encoding="utf-8", errors="replace")

# 顺序即语义，tests/test_mypy_gate.py 会拿 .githooks/pre-commit 里的字面量对拍，防止两处漂移。
INTERPRETER_CANDIDATES = (
    ".venv/Scripts/python.exe",
    ".venv/bin/python",
    "python",
    "python3",
)

SKIP_RC = 0
MISSING_DEP_RC = 1


def resolve_executables(candidates: tuple[str, ...] = INTERPRETER_CANDIDATES) -> list[str]:
    """按优先级返回本机实际存在的候选解释器（venv 相对当前目录，PATH 项走 which）。"""
    found = []
    for cand in candidates:
        if cand in ("python", "python3"):
            resolved = shutil.which(cand)
        else:
            resolved = cand if Path(cand).exists() else None
        if resolved and resolved not in found:
            found.append(resolved)
    return found


def has_module(executable: str, module: str) -> bool:
    """该解释器能否 ``-m <module>``。用于跳过装了 Python 但没装 mypy 的环境。"""
    try:
        return subprocess.run([executable, "-m", module, "--version"], capture_output=True).returncode == 0
    except OSError:
        return False


def run(module: str, args: list[str]) -> int:
    """定位解释器并执行 ``python -m <module> <args>``，返回退出码。"""
    executables = resolve_executables()
    if not executables:
        print(
            "[mypy-hook] 跳过：四级回退（"
            + " → ".join(INTERPRETER_CANDIDATES)
            + "）都没找到 Python，本地无从执行类型检查。CI 的 typecheck job 仍会把关。",
            file=sys.stderr,
        )
        return SKIP_RC

    for exe in executables:
        if has_module(exe, module):
            return subprocess.run([exe, "-m", module, *args]).returncode

    print(
        f"[mypy-hook] 失败：找到的 Python 都没装 {module}（试过：{', '.join(executables)}）。",
        file=sys.stderr,
    )
    print(
        f"            装进项目环境：{executables[0]} -m pip install {module}",
        file=sys.stderr,
    )
    print(
        f"            这不是误报：缺 {module} 与钩子写死路径时同样表现为 not found，两者都该让你知道。",
        file=sys.stderr,
    )
    return MISSING_DEP_RC


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print("用法: python scripts/mypy_gate.py <module> [args...]", file=sys.stderr)
        return 2
    return run(argv[0], argv[1:])


if __name__ == "__main__":
    sys.exit(main())
