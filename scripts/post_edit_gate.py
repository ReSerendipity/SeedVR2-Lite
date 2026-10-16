#!/usr/bin/env python3
"""编辑后最小有界验证门禁 —— 把「每次代码编辑后立即验证」变成钩子能执行的确定流程。

要修的故障
----------
「编辑后验证」此前只是一句自然语言约定：本机没有任何机械执行点落在编辑动作上——
git 钩子只在 commit / push 触发，`ruff` / `pytest` 只在人工敲命令时才跑。审计结论
因此是「上千次编辑事件、零条验证命令」：约定存在，回路不存在。本脚本把同一条约定
收敛成可被钩子调用、可被会话留痕的最小流程。

分层口径
--------
- **commit 层（默认，十秒量级）**：`ruff check` 只查改过的文件；`pytest` 只跑对得上号的
  定向用例（先 `tests/test_<模块名>.py` 精确命中，再 `test_*<模块名>*.py` 模糊
  命中）。一个定向用例都对不上的时候，按约定退化为全量 `pytest tests/ -q --cov-fail-under=55`。
  （定向层的十秒不是用例本身：tests/conftest.py 会拉起 FastAPI + torch 导入链，这是本仓
  测试基设的固有成本，不是门禁可剪枝的部分。）
- **push 层（`--full`，分钟级）**：强制跑全量 + 覆盖率门槛，命令与 ci.yml 的
  「Pytest + coverage」步骤同款，把「本地绿、CI 红」的窗口压到 push 之前。

三种结局
--------
- 四级回退（``.venv/Scripts/python.exe`` → ``.venv/bin/python`` → ``python`` → ``python3``）
  一个解释器都没有：本机无从执行任何检查，打印原因后放行——权威门禁仍是 ci.yml。
- 有解释器但没装 ruff / pytest：真缺依赖，非零退出并给出安装命令，不静默放行
  （口径与 ``scripts/mypy_gate.py`` 一致：缺依赖与缺解释器必须能区分）。
- 检查真跑了：退出码原样判定，任一环节失败即非零，失败当场暴露而不是拖到 push / CI。

留痕
----
结果同时写入 ``logs/post-edit-gate.jsonl``（logs/ 已被 .gitignore 排除），让「这次编辑
到底验过没有」在会话结束后仍可机器核对；标准输出始终保留完整命令与结论，供会话上下文引用。
每条记录都带 ``branch`` / ``commit``（取不到记 null），使「已通过」能绑定到具体修订；
``gate-off`` / ``no-python-changes`` / ``no-interpreter`` / ``missing-tools`` 四种提前返回
同样落账（``note`` 字段标明是哪一种），否则事后只剩「有 PASS 记录」与「什么都没有」两态，
被跳过与压根没执行看不出来。``--quiet-ledger`` 仍然一个字都不写。
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import shutil
import subprocess  # nosec B404（仅以参数列表调用 git / ruff / pytest，无 shell=True）
import sys
import tempfile
import time
from collections.abc import Iterable, Sequence
from datetime import datetime
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and hasattr(_stream, "reconfigure"):
        with contextlib.suppress(OSError, ValueError):
            _stream.reconfigure(encoding="utf-8", errors="replace")

# 顺序即语义：与 .githooks/pre-commit 的四级回退、scripts/mypy_gate.py 保持同序，
# tests/test_post_edit_gate.py 会抓 .githooks 里的字面量对拍，防止三处漂移。
INTERPRETER_CANDIDATES = (
    ".venv/Scripts/python.exe",
    ".venv/bin/python",
    "python",
    "python3",
)

# 运行 / 构建产物与第三方落盘目录：里面的 .py 不是「本次编辑的成果」，不参与门禁。
SKIP_PARTS = frozenset(
    {
        ".venv",
        ".hypothesis",
        ".mypy_cache",
        ".pytest_cache",
        ".qoder",
        ".ruff_cache",
        ".torch_cache",
        "__pycache__",
        "_archive",
        "build",
        "dist",
        "logs",
        "node_modules",
        "outputs",
        "site-packages",
    }
)
# 定向用例索引额外排除：tests/perf 需 GPU / benchmark 环境，CI 亦以 --ignore=tests/perf 跳过。
TEST_SKIP_PARTS = SKIP_PARTS | frozenset({"perf", "playwright-report", "test-results", "wcag-reports"})

TESTS_DIR = "tests"
REQUIRED_MODULES = ("ruff", "pytest")
# 与 ci.yml「Pytest + coverage」步骤同款口径（push 层的调用面 `precheck.ps1` 是维护者本地脚本，未随仓库分发）。
FULL_SUITE_ARGS = ("tests/", "-q", "--ignore=tests/perf", "--cov=app/integrated_app", "--cov-fail-under=55")

RC_PASS = 0
RC_FAIL = 1
RC_SKIP = 0

BANNER = "=" * 60


def repo_root() -> Path:
    """脚本固定位于 <repo>/scripts/ 下，父父级即仓库根（与 check_*.py 系列同法）。"""
    return Path(__file__).resolve().parents[1]


def _git_lines(root: Path, *args: str) -> list[str]:
    """跑一条只读 git 命令并按行取回结果；git 不可用 / 非仓库时返回空列表。"""
    try:
        out = subprocess.run(  # nosec B603
            ["git", *args],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError:
        return []
    if out.returncode != 0:
        return []
    return [line.strip() for line in out.stdout.splitlines() if line.strip()]


def normalize(rel: str) -> str:
    """统一成相对仓库根的 POSIX 路径（pre-commit 传相对路径，Windows 手敲可能是反斜杠）。"""
    cleaned = rel.strip().replace("\\", "/").lstrip("./")
    return cleaned or rel


def resolve_changed(explicit: Sequence[str], root: Path) -> tuple[list[str], str]:
    """改动文件来源优先级：入参（pre-commit / 命令行）→ POST_EDIT_FILES → git 工作区改动。"""
    if explicit:
        return [normalize(f) for f in explicit], "args"

    env = os.environ.get("POST_EDIT_FILES", "").strip()
    if env:
        parts = [normalize(p) for p in re.split(r"[;,:\n]", env) if p.strip()]
        return parts, "env"

    files: set[str] = set()
    files.update(_git_lines(root, "diff", "--cached", "--name-only", "--diff-filter=ACM"))
    files.update(_git_lines(root, "diff", "--name-only"))
    files.update(_git_lines(root, "ls-files", "--others", "--exclude-standard"))
    return [normalize(f) for f in sorted(files)], "git"


def python_sources(files: Iterable[str], root: Path) -> list[str]:
    """留下本机真实存在的 .py（已删除的文件无从 lint / 测试，由 pytest 收集阶段兜底）。"""
    kept: set[str] = set()
    for rel in files:
        if not rel.endswith(".py"):
            continue
        parts = set(path_parts(rel))
        if parts & SKIP_PARTS:
            continue
        if not (root / rel).exists():
            continue
        kept.add(rel)
    return sorted(kept)


def path_parts(rel: str) -> list[str]:
    """路径的每一段（含目录名），用于与 SKIP_PARTS 求交。"""
    return [p for p in rel.split("/") if p]


def index_test_files(root: Path) -> list[str]:
    """仓库内可用测试文件的相对路径索引（排序保证跨机确定性）。

    用 `os.walk` 而非 `rglob`：前者能在目录层剪枝（node_modules / playwright-report 等），
    后者会把 Playwright 依赖树全走一遍——实测把整个门禁拖到十秒量级，而门禁的
    价值在于它能跟在每次编辑后面廉价跑一次。
    """
    tdir = root / TESTS_DIR
    if not tdir.is_dir():
        return []
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(tdir):
        dirnames[:] = sorted(d for d in dirnames if d not in TEST_SKIP_PARTS)
        for name in filenames:
            if name.startswith("test_") and name.endswith(".py"):
                found.append((Path(dirpath) / name).relative_to(root).as_posix())
    return sorted(found)


def match_tests(sources: Sequence[str], test_index: Sequence[str]) -> tuple[list[str], list[str]]:
    """源码 → 定向用例。返回 (去重后的用例清单, 对不上任何用例的源码)。

    命中优先级：改的就是测试文件本身 → 它自己；否则 `tests/test_<模块名>.py` 精确命中；
    再否则 `test_*<模块名>*.py` 模糊命中。刻意不做语义推断：门禁要的是可复现的机械映射，
    映射不到就退回全量，而不是猜一个可能相关的用例集让人误以为「已经验过了」。
    """
    index = set(test_index)
    matched: list[str] = []
    unmatched: list[str] = []

    for rel in sources:
        stem = Path(rel).name[:-3]  # 去掉 .py
        hits: list[str] = []
        if rel.startswith(f"{TESTS_DIR}/") and stem.startswith("test_") and rel in index:
            hits.append(rel)

        exact = f"{TESTS_DIR}/test_{stem}.py"
        if exact in index:
            hits.append(exact)
        else:
            hits.extend(p for p in test_index if stem and stem in Path(p).name and p not in hits)

        if hits:
            matched.extend(hits)
        else:
            unmatched.append(rel)

    seen: set[str] = set()
    ordered = [m for m in matched if not (m in seen or seen.add(m))]
    return ordered, unmatched


# 单次探测：只查模块存否（find_spec），不跑 `-m pytest --version`——后者会加载插件体系，
# 实测 1.3s vs 0.23s；门禁要跟在每次编辑后面跑，探测本身不能成为开销主体。
_PROBE_SRC = "import importlib.util, sys; sys.exit(0 if all(importlib.util.find_spec(m) for m in sys.argv[1:]) else 1)"


def _has_modules(executable: str, modules: Sequence[str]) -> bool:
    try:
        return (
            subprocess.run(  # nosec B603
                [executable, "-c", _PROBE_SRC, *modules],
                capture_output=True,
            ).returncode
            == 0
        )
    except OSError:
        return False


def resolve_interpreter(modules: Sequence[str] = REQUIRED_MODULES) -> tuple[str | None, list[str]]:
    """按四级回退取第一个装齐全部依赖的解释器；返回 (解释器或 None, 试过的清单)。"""
    order: list[str] = []
    seen_real: set[str] = set()

    def _push(resolved: str | None) -> None:
        if not resolved:
            return
        key = str(Path(resolved).resolve()) if Path(resolved).exists() else resolved
        if key in seen_real:
            return
        seen_real.add(key)
        order.append(resolved)

    # sys.executable 优先：它就是调用方（pre-commit / 钩子）选定的环境，venv 候选与之常常同一个。
    _push(sys.executable or None)
    for cand in INTERPRETER_CANDIDATES:
        if cand in ("python", "python3"):
            _push(shutil.which(cand))
        elif Path(cand).exists():
            _push(cand)

    for exe in order:
        if _has_modules(exe, modules):
            return exe, order
    return None, order


def _basetemp() -> str:
    r"""本轮 pytest 的临时目录：仓库外 + 进程独占。

    两个坑叠在一起才选的这个地方：
    - 放在仓库内（`logs/_pt/`）会把用例的 tmp_path 建在 git 工作树里：用例自己造一个残骸
      `.git/` 目录时，`git rev-parse` 会向上找到本仓的 `.git`，于是「缓存损坏」被误判成
      「网络不可用」——实测 `tests/test_check_upstream_sync.py` 在仓库内 basetemp 下恒红。
    - 不指 `--basetemp` 则回到 Windows 默认 `%TEMP%\pytest-of-<user>`：那里的 `pytest-current`
      是个符号链接，上次异常中断后会变成被锁定的死链接，直接把 pytest 顶失败（陷阱 #131/#132）。
      带 PID 的独立目录不经过那个链接，旧目录交给系统回收 %TEMP%。
    """
    return (Path(tempfile.gettempdir()) / f"seedvr2-post-edit-gate-{os.getpid()}").as_posix()


def run_step(cmd: Sequence[str], root: Path) -> tuple[int, float]:
    """执行一步并把输出原样透传到控制台（结果必须留在会话上下文里，不做静默捕获）。"""
    print(f"\n$ {' '.join(cmd)}", flush=True)
    start = time.perf_counter()
    rc = subprocess.run(cmd, cwd=root).returncode  # nosec B603
    return rc, time.perf_counter() - start


def _revision(root: Path) -> tuple[str | None, str | None]:
    """取当前分支与 HEAD 短哈希，用于把台账绑定到「哪一次提交验过」。

    logs/ 被 .gitignore 排除，本机这份 jsonl 是唯一留痕；没有修订号时「已通过」无法对应到某个
    commit。git 不可用 / 非仓库一律返回 (None, None) —— 留痕字段缺失绝不能反过来影响判定。
    """
    branch = _git_lines(root, "rev-parse", "--abbrev-ref", "HEAD")
    commit = _git_lines(root, "rev-parse", "--short", "HEAD")
    return (branch[0] if branch else None, commit[0] if commit else None)


def write_ledger(record: dict[str, object], root: Path) -> None:
    """best-effort 台账：写失败只告警，绝不能让留痕逻辑反过来把门禁搞红。"""
    path = root / "logs" / "post-edit-gate.jsonl"
    branch, commit = _revision(root)
    record.setdefault("branch", branch)
    record.setdefault("commit", commit)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    except OSError as exc:  # pragma: no cover - 只可能是磁盘/权限问题
        print(f"[post-edit-gate] 台账写入失败（不影响判定）：{exc}", file=sys.stderr)
    record["_ledger"] = str(path.relative_to(root).as_posix())


def note_ledger(
    root: Path, mode: str, rc: int, note: str, source: str | None = None, files: Sequence[str] = ()
) -> None:
    """给「没跑成 / 无需跑 / 被跳过」这些提前返回的路径也落一条台账。

    否则事后只剩两种状态：有一条 PASS 记录，或者什么都没有 ——「被跳过」和「压根没执行」看不出来。
    """
    record: dict[str, object] = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "mode": mode,
        "rc": rc,
        "note": note,
    }
    if source is not None:
        record["source"] = source
        record["sources"] = list(files)
    write_ledger(record, root)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="编辑后最小有界验证：ruff（改动文件）+ pytest（定向用例，缺失则全量 + 覆盖率门槛）"
    )
    parser.add_argument(
        "files", nargs="*", help="改动文件；由 pre-commit 自动传入，缺省时读 POST_EDIT_FILES 或 git 工作区"
    )
    parser.add_argument("--full", action="store_true", help="强制跑全量 pytest + 覆盖率门槛（push 层口径）")
    parser.add_argument("--quiet-ledger", action="store_true", help="不写 logs/post-edit-gate.jsonl")
    args = parser.parse_args(argv)

    root = repo_root()
    mode = os.environ.get("POST_EDIT_GATE", "fast").strip().lower()
    started = time.perf_counter()

    if mode == "off":
        print(
            f"{BANNER}\n[post-edit-gate] POST_EDIT_GATE=off —— 本次跳过编辑后验证。\n"
            f"这是紧急出口，等同 git commit --no-verify：全部门禁的权威执行点回到 ci.yml，"
            f"事后请按本地规范在 REVISION_LOG / GOTCHAS 留痕。\n{BANNER}",
            file=sys.stderr,
        )
        if not args.quiet_ledger:
            note_ledger(root, mode, RC_SKIP, "gate-off")
        return RC_SKIP

    # pre-commit / 命令行显式传入优先；没传再看 POST_EDIT_FILES，最后看 git 工作区改动。
    sources = python_sources(args.files, root)
    source_label = "args"
    if not sources:
        changed, source_label = resolve_changed(args.files, root)
        sources = python_sources(changed, root)

    print(f"{BANNER}\n编辑后验证 (post-edit-gate)  mode={mode}  来源={source_label}\n{BANNER}")
    if not sources:
        print("未检测到改动的 Python 文件 —— 无需 ruff / pytest，直接放行。")
        if not args.quiet_ledger:
            note_ledger(root, mode, RC_PASS, "no-python-changes", source_label, list(args.files))
        return RC_PASS

    print(f"改动的 Python 文件（{len(sources)}）：\n  " + "\n  ".join(sources))

    exe, tried = resolve_interpreter()
    if exe is None:
        if not tried:
            print("[post-edit-gate] 跳过：四级回退都没找到 Python，本机无从执行检查；CI 仍会把关。", file=sys.stderr)
            if not args.quiet_ledger:
                note_ledger(root, mode, RC_SKIP, "no-interpreter", source_label, sources)
            return RC_SKIP
        print(
            f"[post-edit-gate] 失败：找到的解释器都没装齐 ruff / pytest（试过：{', '.join(tried)}）。", file=sys.stderr
        )
        print(f"            装进项目环境：{tried[0]} -m pip install -r requirements-dev.txt", file=sys.stderr)
        if not args.quiet_ledger:
            note_ledger(root, mode, RC_FAIL, "missing-tools", source_label, sources)
        return RC_FAIL

    full = mode == "full" or args.full
    test_index = index_test_files(root)
    targets, unmatched = match_tests(sources, test_index)
    basetemp = _basetemp()

    rc_all = RC_PASS
    steps: dict[str, object] = {}

    ruff_rc, ruff_sec = run_step([exe, "-X", "utf8", "-m", "ruff", "check", *sources], root)
    steps["ruff"] = {"rc": ruff_rc, "seconds": round(ruff_sec, 2), "files": len(sources)}
    print(f"[1/2] ruff check: {'PASS' if ruff_rc == 0 else 'FAIL'}（{ruff_sec:.1f}s，{len(sources)} 个文件）")
    rc_all |= ruff_rc

    if full:
        why = "显式 --full / POST_EDIT_GATE=full" if not unmatched else "无定向用例，退回全量"
        print(f"\n[2/2] pytest 全量 + 覆盖率门槛（{why}）：目标覆盖率 fail_under=55")
        pytest_rc, pytest_sec = run_step(
            [exe, "-X", "utf8", "-m", "pytest", *FULL_SUITE_ARGS, f"--basetemp={basetemp}"], root
        )
        steps["pytest"] = {"rc": pytest_rc, "seconds": round(pytest_sec, 2), "scope": "full"}
    elif targets:
        print(f"\n[2/2] pytest 定向用例（{len(targets)} 个文件）：" + ", ".join(targets))
        if unmatched:
            print("     未匹配到定向用例（本层不覆盖，push 层 --full 兜底）：" + ", ".join(unmatched))
        pytest_rc, pytest_sec = run_step(
            [exe, "-X", "utf8", "-m", "pytest", "-q", *targets, f"--basetemp={basetemp}"], root
        )
        steps["pytest"] = {"rc": pytest_rc, "seconds": round(pytest_sec, 2), "scope": "targeted", "files": targets}
    else:
        # 铁律原文的退化分支：一个定向用例都对不上就跑全量，否则「跑过了」会变成假信号。
        why = f"未匹配到定向用例（{len(unmatched)} 个文件）"
        print(f"\n[2/2] pytest 全量 + 覆盖率门槛（{why}）：目标覆盖率 fail_under=55")
        pytest_rc, pytest_sec = run_step(
            [exe, "-X", "utf8", "-m", "pytest", *FULL_SUITE_ARGS, f"--basetemp={basetemp}"], root
        )
        steps["pytest"] = {"rc": pytest_rc, "seconds": round(pytest_sec, 2), "scope": "full-fallback"}
    print(f"[2/2] pytest: {'PASS' if pytest_rc == 0 else 'FAIL'}（{pytest_sec:.1f}s）")
    rc_all |= pytest_rc

    verdict = "PASS" if rc_all == RC_PASS else "FAIL"
    total = time.perf_counter() - started
    print(f"\n{BANNER}\nRESULT: {verdict} —— ruff + pytest（{steps['pytest']['scope']}）合计 {total:.1f}s")
    if verdict == "FAIL":
        print("失败必须当场修复或回滚，不得拖到 push / CI 阶段（编辑→验证闭环）。")
    else:
        print("本段输出即验证证据，请保留在会话上下文中。")
    print(f"{BANNER}")

    if not args.quiet_ledger:
        write_ledger(
            {
                "ts": datetime.now().isoformat(timespec="seconds"),
                "mode": mode,
                "full": full,
                "source": source_label,
                "sources": sources,
                "unmatched": unmatched,
                "total_seconds": round(total, 2),
                "rc": rc_all,
                "steps": steps,
            },
            root,
        )
    return rc_all


if __name__ == "__main__":
    sys.exit(main())
