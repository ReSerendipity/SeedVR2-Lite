#!/usr/bin/env python3
"""tests/test_post_edit_gate.py — 编辑后验证门禁的判定逻辑测试。

为什么值得单独测：这个脚本是「别的检查能不能被信任」的地基。它把一条自然语言约定
（改完代码立刻跑 ruff + 定向 pytest）落成钩子可调用的流程，如果映射或退出码判错，失效方式
是把「没验过」显示成「验过了」——比没有门禁更糟。所以这里锁四件事：

1. **反漂移**：解释器四级回退顺序必须与 ``scripts/mypy_gate.py`` 一致（后者已与 `.githooks/pre-commit`
   对拍）；三处各写各的就会出现「钩子能跑、git 钩子跑不动」。
2. **改动文件来源优先级**：入参 → ``POST_EDIT_FILES`` → git 工作区。顺序颠倒会让 pre-commit 传入的
   暂存清单被本机无关的未跟踪文件淹没。
3. **源码 → 定向用例映射**：精确命中优先、模糊命中兜底、映射不到必须显式列出（而不是静默少跑）。
4. **三种结局各归其位**：任一步失败即 1；本机根本没有 Python 才放行；有 Python 缺 ruff/pytest 是真失败。

真正的 ruff / pytest 由 monkeypatch 顶替，本测试不依赖本机装了什么、也不反复起子进程。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from scripts import mypy_gate, post_edit_gate


@pytest.fixture
def fake_repo(tmp_path, monkeypatch):
    """把仓库根钉在 tmp_path，避免读到真实仓库的 tests 索引与 logs 台账。"""
    (tmp_path / "tests").mkdir(parents=True)
    (tmp_path / "logs").mkdir(parents=True)
    monkeypatch.setattr(post_edit_gate, "repo_root", lambda: tmp_path)
    return tmp_path


def _make_source(root: Path, rel: str) -> str:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x = 1\n", encoding="utf-8")
    return rel


def _module_of(cmd: list[str]) -> str:
    """取 `python -m <module> ...` 里的模块名（不依赖 -X utf8 等前置参数的个数）。"""
    return cmd[cmd.index("-m") + 1]


class TestInterpreterProbe:
    def test_candidate_order_matches_mypy_gate(self):
        """与 mypy_gate 同序＝与 .githooks/pre-commit 同序（后者由 test_mypy_gate 对拍）。"""
        assert post_edit_gate.INTERPRETER_CANDIDATES == mypy_gate.INTERPRETER_CANDIDATES

    def test_probe_checks_all_modules_in_one_call(self):
        """探测口径：一次 `-c` 查齐 ruff + pytest，不是每个模块各跑一次 --version。"""
        assert "find_spec" in post_edit_gate._PROBE_SRC
        assert post_edit_gate.REQUIRED_MODULES == ("ruff", "pytest")

    def test_probe_returns_false_when_executable_is_missing(self, tmp_path):
        assert post_edit_gate._has_modules(str(tmp_path / "nope.exe"), ("ruff",)) is False

    def test_sys_executable_is_tried_before_the_fallback_chain(self, monkeypatch):
        monkeypatch.setattr(post_edit_gate.sys, "executable", "C:/fake/venv/python.exe")
        monkeypatch.setattr(post_edit_gate.Path, "exists", lambda self: False)
        monkeypatch.setattr(post_edit_gate.shutil, "which", lambda name: f"/usr/bin/{name}")
        probed: list[str] = []

        def fake_has_modules(exe, modules):
            probed.append(exe)
            return exe == "C:/fake/venv/python.exe"

        monkeypatch.setattr(post_edit_gate, "_has_modules", fake_has_modules)
        exe, order = post_edit_gate.resolve_interpreter()
        assert exe == "C:/fake/venv/python.exe"
        assert probed == [exe], "第一个可用解释器就该收敛，不该把整条回退链都探一遍"
        assert order[0] == exe and order[-2:] == ["/usr/bin/python", "/usr/bin/python3"]

    def test_same_interpreter_is_not_probed_twice(self, monkeypatch):
        venv = ".venv/Scripts/python.exe"
        monkeypatch.setattr(post_edit_gate.sys, "executable", venv)
        # 只有 Windows 布局的 venv 存在：sys.executable 与候选清单指向同一个文件，必须去重。
        monkeypatch.setattr(post_edit_gate.Path, "exists", lambda self: self.as_posix() == venv)
        monkeypatch.setattr(post_edit_gate.shutil, "which", lambda name: None)
        probed: list[str] = []
        monkeypatch.setattr(post_edit_gate, "_has_modules", lambda exe, modules: probed.append(exe) or False)

        exe, order = post_edit_gate.resolve_interpreter()
        assert exe is None
        assert order == [venv] and probed == [venv], "resolve() 去重失效会把同一个解释器探两遍"


class TestChangedFileSources:
    def test_args_win_over_env_and_git(self, monkeypatch):
        monkeypatch.setenv("POST_EDIT_FILES", "from_env.py")
        monkeypatch.setattr(post_edit_gate, "_git_lines", lambda root, *args: ["from_git.py"])
        files, source = post_edit_gate.resolve_changed(["a.py", "b.py"], Path("."))
        assert files == ["a.py", "b.py"]
        assert source == "args"

    def test_env_is_the_second_tier(self, monkeypatch):
        monkeypatch.setenv("POST_EDIT_FILES", "a.py;b.py,c.py")
        files, source = post_edit_gate.resolve_changed([], Path("."))
        assert source == "env"
        assert files == ["a.py", "b.py", "c.py"]

    def test_git_fallback_merges_staged_worktree_and_untracked(self, monkeypatch):
        monkeypatch.delenv("POST_EDIT_FILES", raising=False)

        def fake_git_lines(root, *args):
            table = {
                ("diff", "--cached", "--name-only", "--diff-filter=ACM"): ["staged.py"],
                ("diff", "--name-only"): ["worktree.py"],
                ("ls-files", "--others", "--exclude-standard"): ["untracked.py"],
            }
            return list(table.get(args, []))

        monkeypatch.setattr(post_edit_gate, "_git_lines", fake_git_lines)
        files, source = post_edit_gate.resolve_changed([], Path("."))
        assert source == "git"
        assert files == ["staged.py", "untracked.py", "worktree.py"], "来源必须去重并稳定排序"

    def test_backslash_and_dotslash_are_normalized(self, monkeypatch):
        monkeypatch.delenv("POST_EDIT_FILES", raising=False)
        files, _ = post_edit_gate.resolve_changed(["app\\server.py", "./x.py"], Path("."))
        assert files == ["app/server.py", "x.py"]


class TestPythonSourceFilter:
    def test_keeps_only_existing_python_files(self, fake_repo):
        _make_source(fake_repo, "app/live.py")
        (fake_repo / "README.md").write_text("x", encoding="utf-8")
        kept = post_edit_gate.python_sources(["app/live.py", "app/gone.py", "README.md", "a.js"], fake_repo)
        assert kept == ["app/live.py"]

    def test_vendor_and_artifact_dirs_are_skipped(self, fake_repo):
        rels = [
            ".venv/lib/site-packages/pkg/mod.py",
            "tests/node_modules/x/y.py",
            "build/gen.py",
            "dist/gen.py",
            "__pycache__/x.py",
            "logs/x.py",
            "_archive/x.py",
        ]
        for rel in rels:
            _make_source(fake_repo, rel)
        assert post_edit_gate.python_sources(rels, fake_repo) == []

    def test_model_lib_is_validated_like_any_other_source(self, fake_repo):
        """禁区只禁「自动改」，不禁「改完验证」：模型源码断链必须能被 ruff 抓到。"""
        rel = _make_source(fake_repo, "model_lib/dit/nadit.py")
        assert post_edit_gate.python_sources([rel], fake_repo) == [rel]


class TestTestMatching:
    index = [
        "tests/test_cache.py",
        "tests/test_cache_max_size.py",
        "tests/test_app_server.py",
        "tests/utils/test_helper.py",
    ]

    def test_exact_hit_wins_and_fuzzy_is_not_added(self):
        targets, unmatched = post_edit_gate.match_tests(["app/integrated_app/cache.py"], self.index)
        assert targets == ["tests/test_cache.py"]
        assert unmatched == []

    def test_fuzzy_hit_when_no_exact_match(self):
        targets, unmatched = post_edit_gate.match_tests(["app/integrated_app/server.py"], self.index)
        assert targets == ["tests/test_app_server.py"]
        assert unmatched == []

    def test_subdirectory_tests_are_reachable(self):
        targets, _ = post_edit_gate.match_tests(["app/utils/helper.py"], self.index)
        assert targets == ["tests/utils/test_helper.py"]

    def test_changed_test_file_maps_to_itself(self):
        targets, _ = post_edit_gate.match_tests(["tests/test_cache.py"], self.index)
        assert targets[0] == "tests/test_cache.py"

    def test_unmappable_source_is_reported_not_silently_dropped(self):
        targets, unmatched = post_edit_gate.match_tests(["model_lib/dit/nadit.py"], self.index)
        assert targets == []
        assert unmatched == ["model_lib/dit/nadit.py"]

    def test_duplicates_are_collapsed_preserving_order(self):
        targets, _ = post_edit_gate.match_tests(["app/cache.py", "app/integrated_app/cache.py"], self.index)
        assert targets == ["tests/test_cache.py"]


class TestTestIndexPruning:
    def test_artifact_dirs_are_pruned(self, fake_repo):
        for rel in (
            "tests/test_real.py",
            "tests/node_modules/pkg/test_vendor.py",
            "tests/perf/test_gpu.py",
            "tests/playwright-report/test_x.py",
            "tests/__pycache__/test_cached.py",
        ):
            _make_source(fake_repo, rel)
        assert post_edit_gate.index_test_files(fake_repo) == ["tests/test_real.py"]

    def test_missing_tests_dir_yields_empty_index(self, tmp_path):
        assert post_edit_gate.index_test_files(tmp_path) == []


class TestMainOutcomes:
    @pytest.fixture
    def wired(self, fake_repo, monkeypatch):
        """解释器 + 索引 + run_step 全部就位，只留判定逻辑被检。"""
        _make_source(fake_repo, "app/cache.py")
        _make_source(fake_repo, "model_lib/nadit.py")
        monkeypatch.setattr(post_edit_gate, "resolve_interpreter", lambda *a, **k: ("python", ["python"]))
        monkeypatch.setattr(post_edit_gate, "index_test_files", lambda root: ["tests/test_cache.py"])
        cmds: list[list[str]] = []
        monkeypatch.setattr(post_edit_gate, "run_step", lambda cmd, root: cmds.append(list(cmd)) or (0, 1.0))
        monkeypatch.delenv("POST_EDIT_GATE", raising=False)
        return cmds

    def test_pass_runs_ruff_then_targeted_pytest(self, wired, fake_repo, capsys):
        assert post_edit_gate.main(["app/cache.py"]) == 0
        out = capsys.readouterr().out
        assert "RESULT: PASS" in out
        assert _module_of(wired[0]) == "ruff" and "app/cache.py" in wired[0]
        assert _module_of(wired[1]) == "pytest" and "tests/test_cache.py" in wired[1]
        assert "--cov-fail-under=55" not in wired[1], "定向层不该背全量覆盖率门槛"

        record = json.loads((fake_repo / "logs" / "post-edit-gate.jsonl").read_text(encoding="utf-8").strip())
        assert record["rc"] == 0
        assert record["steps"]["pytest"]["scope"] == "targeted"

    def test_ruff_failure_fails_the_gate(self, wired, monkeypatch):
        monkeypatch.setattr(post_edit_gate, "run_step", lambda cmd, root: (1, 0.5))
        assert post_edit_gate.main(["app/cache.py"]) == 1

    def test_pytest_failure_fails_even_when_ruff_is_green(self, wired, monkeypatch):
        results = iter([(0, 0.2), (1, 3.0)])
        monkeypatch.setattr(post_edit_gate, "run_step", lambda cmd, root: next(results))
        assert post_edit_gate.main(["app/cache.py"]) == 1

    def test_unmapped_source_falls_back_to_the_coverage_gated_suite(self, wired, capsys):
        assert post_edit_gate.main(["model_lib/nadit.py"]) == 0
        out = capsys.readouterr().out
        assert "退回全量" in out or "未匹配到定向用例" in out
        full = wired[1]
        assert "tests/" in full
        assert "--cov=app/integrated_app" in full
        assert "--cov-fail-under=55" in full

    def test_full_flag_forces_the_suite_even_with_targeted_tests(self, wired, monkeypatch):
        assert post_edit_gate.main(["app/cache.py", "--full"]) == 0
        assert "--cov-fail-under=55" in wired[1]
        assert "tests/test_cache.py" not in wired[1]

    def test_basetemp_is_a_per_process_dir_outside_the_repo(self, wired):
        """两个坑同时锁住这个选择：仓库内 basetemp 会让用例里的 `.git` 残骸向上找到本仓
        （test_check_upstream_sync 实测因此恒红）；不指 --basetemp 则回到 Windows 默认的
        `pytest-of-<user>`，那里的 `pytest-current` 死链接会直接顶死 pytest（陷阱 #131/#132）。"""
        assert post_edit_gate.main(["app/cache.py"]) == 0
        flag = [arg for arg in wired[1] if str(arg).startswith("--basetemp=")][0]
        value = flag.split("=", 1)[1]
        assert Path(value).is_absolute(), "必须落在仓库外"
        assert Path(value).name == f"seedvr2-post-edit-gate-{os.getpid()}"

    def test_no_python_at_all_is_a_loud_skip(self, fake_repo, monkeypatch, capsys):
        _make_source(fake_repo, "app/cache.py")
        monkeypatch.setattr(post_edit_gate, "resolve_interpreter", lambda *a, **k: (None, []))
        assert post_edit_gate.main(["app/cache.py"]) == 0
        assert "跳过" in capsys.readouterr().err

    def test_python_without_tools_is_a_real_failure(self, fake_repo, monkeypatch, capsys):
        _make_source(fake_repo, "app/cache.py")
        monkeypatch.setattr(post_edit_gate, "resolve_interpreter", lambda *a, **k: (None, ["python", "python3"]))
        assert post_edit_gate.main(["app/cache.py"]) == 1
        assert "没装齐 ruff / pytest" in capsys.readouterr().err

    def test_no_python_changes_is_a_noop(self, fake_repo, monkeypatch, capsys):
        monkeypatch.delenv("POST_EDIT_FILES", raising=False)
        monkeypatch.setattr(post_edit_gate, "_git_lines", lambda root, *a: [])
        assert post_edit_gate.main(["README.md"]) == 0
        assert "未检测到改动的 Python 文件" in capsys.readouterr().out

    def test_off_mode_skips_loudly_and_records(self, fake_repo, monkeypatch, capsys):
        _make_source(fake_repo, "app/cache.py")
        monkeypatch.setenv("POST_EDIT_GATE", "off")
        assert post_edit_gate.main(["app/cache.py"]) == 0
        assert "POST_EDIT_GATE=off" in capsys.readouterr().err
        record = json.loads((fake_repo / "logs" / "post-edit-gate.jsonl").read_text(encoding="utf-8").strip())
        assert record["mode"] == "off"
