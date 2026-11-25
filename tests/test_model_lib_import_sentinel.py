#!/usr/bin/env python3
"""禁区 ``model_lib/`` 的最小导入哨兵（不依赖 GPU、不依赖模型权重）。

背景：``model_lib/`` 是禁区（公开口径见 ``docs/CODING_STANDARDS.md`` 第 5 节；上游
ByteDance 研究代码，ruff 命名规则与 mypy 均豁免，CI 不构建它），而 2026-09-18 仓库
评审核实 **tests/ 里没有任何用例 import
该包**——任何使该包 import 失败的改动（符号改名、跨包相对导入写错、漏文件、语法错误）都能
穿过全部现有门禁。这类断裂在项目账本里已经真实出现过并留过痕：

- GOTCHAS #134 / AGENTS.md v1.87：``dit/blocks/mmdit_window_block.py`` 的 RoPE 导入笔误；
- GOTCHAS #133 + KNOWN_ISSUES #97：7B 链缺 ``NaRotaryEmbedding3d`` 等符号，因无权重未修。

本地未分发引用：AGENTS.md、docs/agents/GOTCHAS.md、docs/project/KNOWN_ISSUES.md
（上面两条是维护者本地留痕账本的编号，仅供溯源；可执行口径以本文件与
``docs/CODING_STANDARDS.md`` 第 5 节为准）

本哨兵分三层，逐层变重、逐层变窄：

1. **语法层**（无依赖）：``model_lib/**/*.py`` 逐个 ``ast.parse``。
2. **静态导入层**（无依赖）：包内 ``from .x import y`` / ``from model_lib.x import y`` 的
   目标符号必须在磁盘上真实存在。不需要 torch、也不要求目标包能被成功 import，因此
   **连目前无法执行的 7B 树也在覆盖范围内**（上面那条 mmdit_window_block 笔误正落在这里）。
3. **运行时层**（需 torch，缺失即 skip）：引擎实际 import 的 3B 出货链模块必须真实 import 成功。

第 2 层的现存断裂走 :data:`KNOWN_BROKEN_INTRA_IMPORTS` 豁免表登记，且要求与实测结果
**集合精确相等**：出现新断裂 → 红；某条豁免被修好 → 同样红（要求删掉登记并同步账本），
从而豁免表不会变成永久盲区。禁区的实际编辑仍按 AGENTS.md 留痕文化执行，本哨兵只兜底
机械可查的部分。

所属项目：SeedVR2-Turbo (SeedVR2 视频/图像修复工具)
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_MODEL_LIB = _REPO_ROOT / "model_lib"

# 引擎运行时真实 import 的 model_lib 模块（3B 出货链）：
# app/integrated_app/engines/seedvr2_engine.py（dit_v2.nadit / video_vae_v3.modules.attn_video_vae）
# 与 _dit_pipeline.py（from model_lib.dit_v2 import na）。
RUNTIME_IMPORT_TARGETS: tuple[str, ...] = (
    "model_lib",
    "model_lib.common",
    "model_lib.dit_v2",
    "model_lib.dit_v2.nadit",
    "model_lib.video_vae_v3.modules.attn_video_vae",
)

# 7B 树（model_lib.dit.*）现存 import 断裂登记。
# 键 = (引用方模块, 目标模块, 缺失符号)。修好后必须从这里删除并更新 GOTCHAS / KNOWN_ISSUES。
# 注：GOTCHAS #134 记录的 `mmdit_window_block.py` RoPE 导入笔误已于 2026-09-18 修好
# （`from ...dit_v2.rope import RotaryEmbedding3d`），故此处不再登记；若那次修复被回退，
# 本哨兵会立刻把它作为「新增未登记断裂」报红。
KNOWN_BROKEN_INTRA_IMPORTS: frozenset[tuple[str, str, str]] = frozenset(
    {
        # dit/__init__.py 要 get_nablock，但 dit/nablocks/__init__.py 定义的是 get_na_block
        # （dit_v2 树里才是 get_nablock）。运行时被 nablocks 子模块内更早的 rope 报错掩盖（见下条），
        # 因此补上 NaRotaryEmbedding3d 后它会成为下一个报错——本行登记的是「修好 #97 后仍不可用」。
        ("model_lib.dit", "model_lib.dit.nablocks", "get_nablock"),
        # KNOWN_ISSUES #97 / GOTCHAS #134：本仓不存在该符号（dit/rope.py 只有 rotary_emb）。
        ("model_lib.dit.nablocks.mmsr_block", "model_lib.dit.rope", "NaRotaryEmbedding3d"),
        # dit/window.py 只有 window_partition / window_reverse，get_window_op 只在 dit_v2 有。
        ("model_lib.dit.nablocks.mmsr_block", "model_lib.dit.window", "get_window_op"),
    }
)

# 7B 遗留树的全部模块前缀（未随权重交付，见 KNOWN_ISSUES #97）。
_LEGACY_7B_PREFIXES = ("model_lib.dit", "model_lib.dit.")

# 目标模块本身在 model_lib 内解析不到（文件被删 / 相对层级写错）时的占位符号名。
_MODULE_NOT_FOUND = "<module not found>"


def _source_files() -> list[Path]:
    """禁区内的全部源文件（哨兵的作用域）。"""
    return sorted(_MODEL_LIB.rglob("*.py"))


def _module_key(path: Path) -> str:
    """``model_lib/dit_v2/rope.py`` → ``model_lib.dit_v2.rope``；``__init__.py`` → 包名。"""
    parts = list(path.relative_to(_REPO_ROOT).parts)
    if parts[-1] == "__init__.py":
        parts.pop()
    else:
        parts[-1] = parts[-1][: -len(".py")]
    return ".".join(parts)


def _module_scope_names(tree: ast.Module) -> tuple[set[str], list[ast.ImportFrom]]:
    """收集**模块作用域**可见的名字与通配导入节点。

    只下钻 if / try / with（可选依赖与条件定义的常见形态），不进函数体与类体——
    函数里的局部赋值不构成对外可见符号。
    """
    names: set[str] = set()
    stars: list[ast.ImportFrom] = []
    stack: list[ast.stmt] = list(tree.body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                names.update(sub.id for sub in ast.walk(target) if isinstance(sub, ast.Name))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*":
                    stars.append(node)
                    continue
                names.add(alias.asname or alias.name)
        elif isinstance(node, ast.If):
            stack += node.body + node.orelse
        elif isinstance(node, (ast.Try, ast.TryStar)):
            stack += node.body + node.orelse + node.finalbody
            for handler in node.handlers:
                stack += handler.body
        elif isinstance(node, ast.With):
            stack += node.body
    return names, stars


def _resolve_import_module(current_key: str, is_package: bool, node: ast.ImportFrom) -> str | None:
    """把 import 语句的源解析成点分模块路径（相对导入按 ``.`` 层级上溯）。"""
    if node.level == 0:
        return node.module
    parts = current_key.split(".") if is_package else current_key.split(".")[:-1]
    climb = node.level - 1
    if climb > len(parts):
        return None
    base = parts[: len(parts) - climb] if climb else parts
    return ".".join([*base, *([node.module] if node.module else [])])


class _ModuleIndex:
    """``model_lib`` 的静态（不执行）视图：模块 → 模块作用域可见符号。"""

    def __init__(self) -> None:
        self.modules: dict[str, ast.Module] = {}
        self.packages: set[str] = set()
        self.broken: list[str] = []
        for path in _source_files():
            key = _module_key(path)
            try:
                self.modules[key] = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError) as exc:  # 语法层用例负责精确报错
                self.broken.append(f"{path.relative_to(_REPO_ROOT)}: {type(exc).__name__}: {exc}")
                continue
            if path.name == "__init__.py":
                self.packages.add(key)
        self._names: dict[str, tuple[set[str], list[ast.ImportFrom]]] = {}

    def names(self, key: str, _seen: frozenset[str] = frozenset()) -> set[str]:
        """模块可见符号：自身定义 + 通配导入展开 + 子模块（``from . import na``）。"""
        if key not in self.modules:
            return set()
        if key in _seen:  # 循环通配导入
            return set()
        if key not in self._names:
            self._names[key] = _module_scope_names(self.modules[key])
        own, stars = self._names[key]
        out = set(own)
        for node in stars:
            target = _resolve_import_module(key, key in self.packages, node)
            if target:
                out |= self.names(target, _seen | {key})
        if key in self.packages:
            prefix = f"{key}."
            direct_children = {
                k[len(prefix) :] for k in self.modules if k.startswith(prefix) and "." not in k[len(prefix) :]
            }
            out |= direct_children
        return out


def _intra_import_violations(index: _ModuleIndex) -> set[tuple[str, str, str]]:
    """返回「引用方模块 → 目标模块 → 缺失符号」三元组集合（含目标模块缺失）。"""
    violations: set[tuple[str, str, str]] = set()
    for key, tree in index.modules.items():
        is_package = key in index.packages
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            target = _resolve_import_module(key, is_package, node)
            if not target or not target.startswith("model_lib"):
                continue  # 仓外依赖（torch / einops / 根级 common）不在本哨兵职责内
            if target not in index.modules:
                violations.add((key, target or "<unresolved>", _MODULE_NOT_FOUND))
                continue
            available = index.names(target)
            for alias in node.names:
                if alias.name != "*" and alias.name not in available:
                    violations.add((key, target, alias.name))
    return violations


def _is_legacy_7b(module_key: str) -> bool:
    """是否属于 7B 遗留树（`model_lib.dit` 及其子模块，不含 `model_lib.dit_v2`）。"""
    return module_key in _LEGACY_7B_PREFIXES or module_key.startswith(_LEGACY_7B_PREFIXES[1])


def _format(rows: set[tuple[str, str, str]]) -> str:
    return "\n".join(f"  - {importer} 需从 {target} 导入 {name}" for importer, target, name in sorted(rows))


class TestModelLibSyntaxSentinel:
    """第 1 层：禁区文件的语法完整性（无需 torch / GPU / 权重）。"""

    def test_sentinel_scope_is_not_empty(self):
        """作用域守卫：model_lib 被移动或清空时哨兵必须失效报警，而不是静默全绿。"""
        files = _source_files()
        assert files, f"哨兵作用域为空：未找到 {_MODEL_LIB} 下的任何 .py 文件"
        assert len(files) > 40, f"哨兵作用域异常缩小（仅 {len(files)} 个文件），请确认 model_lib 未被误删"

    def test_every_forbidden_zone_file_parses(self):
        """每个禁区源文件都必须能被 ast.parse（语法断裂 = 直接红）。"""
        failures: list[str] = []
        for path in _source_files():
            try:
                ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError) as exc:
                rel = path.relative_to(_REPO_ROOT)
                line = getattr(exc, "lineno", "?")
                detail = exc.msg if isinstance(exc, SyntaxError) else str(exc)
                failures.append(f"  - {rel}:{line}: {type(exc).__name__}: {detail}")
        assert not failures, "model_lib 存在语法层断裂（无法解析的源文件）：\n" + "\n".join(failures)


class TestModelLibStaticImportSentinel:
    """第 2 层：包内 import 符号的静态可解析性（覆盖目前无法执行的 7B 树）。"""

    def test_intra_package_import_names_match_exemption_ledger(self):
        """实测断裂集合必须与豁免登记**完全相等**：新增即红，修好不减登记同样红。"""
        index = _ModuleIndex()
        assert not index.broken, "model_lib 存在无法解析的文件，静态导入层结论不可信：\n  " + "\n  ".join(index.broken)
        found = _intra_import_violations(index)
        new_rows = found - KNOWN_BROKEN_INTRA_IMPORTS
        stale_rows = KNOWN_BROKEN_INTRA_IMPORTS - found
        report: list[str] = []
        if new_rows:
            report.append(
                "新增未登记的 import 断裂（禁区改动破坏了包内符号解析，"
                "修好引用，或按仓库留痕口径（docs/CODING_STANDARDS.md 第 5 节）记一笔后，"
                "再把它加进 KNOWN_BROKEN_INTRA_IMPORTS）：\n" + _format(new_rows)
            )
        if stale_rows:
            report.append(
                "豁免登记已失效（对应符号现在可解析，说明修复已落地）——"
                "请从 KNOWN_BROKEN_INTRA_IMPORTS 删除以下条目并同步更新 GOTCHAS / KNOWN_ISSUES：\n"
                + _format(stale_rows)
            )
        assert not report, "\n".join(report)

    def test_shipping_tree_has_zero_import_breakage(self):
        """3B 出货链（common / dit_v2 / video_vae_v3）不允许任何 import 断裂，也不允许登记。"""
        index = _ModuleIndex()
        found = {row for row in _intra_import_violations(index) if not _is_legacy_7b(row[0])}
        assert not found, "出货链 model_lib 树出现 import 断裂（此处不接受豁免）：\n" + _format(found)


class TestModelLibRuntimeImportSentinel:
    """第 3 层：引擎真实 import 路径的可执行性（需 torch，无 GPU / 无权重）。"""

    @pytest.mark.parametrize("module_name", RUNTIME_IMPORT_TARGETS)
    def test_engine_imported_module_imports_without_weights(self, module_name: str):
        pytest.importorskip("torch", reason="运行时层需要 torch；缺失时由语法/静态两层兜底")
        try:
            importlib.import_module(module_name)
        except Exception as exc:  # noqa: BLE001 - 哨兵职责是把任何导入期异常转成红
            pytest.fail(f"import {module_name} 失败（禁区运行时链断裂）：{type(exc).__name__}: {exc}")
