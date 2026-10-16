#!/usr/bin/env python3
"""tests/test_check_local.py — 本地体检里「钩子激活」一项的判定测试。

为什么值得单独测：这一项存在的唯一理由，是 `.githooks/` 的三层门禁依赖 `core.hooksPath`
这条**本机** git 配置，而它不随克隆传播（.githooks/README.md 自述）。新克隆上没人跑过
`install.sh` 时，pre-commit / pre-push / commit-msg 一起静默旁路，没有任何失败信号。
所以这个检查本身的判定必须可证：

1. **该红的地方必须红**：未设置、或设成了别的值，都要以退出码 1 阻断，并给出可复制的修复命令。
2. **该跳的地方不许误伤**：不在 git 仓库里 / git 根本不可用时是「无法判定」，不是「门禁缺失」，
   绝不能报 FAIL——把环境问题读成门禁缺失，会让人从此忽略这条检查。
3. **反漂移**：期望值必须与 `.githooks/install.sh` 真正写入的值逐字一致，否则检查会永远报红。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts import check_local

HOOKS_OPT = check_local.HOOKS_PATH_OPTION
# 生产代码里的实际 argv；查表键必须与它逐字一致，否则探针会误走「git 不可用」分支。
CONFIG_PROBE = ("config", "--get", HOOKS_OPT)


def _probe(table: dict[tuple[str, ...], tuple[int, str]]):
    """把 _git_probe 换成按 argv 查表的假探针；表里没有的调用视为 git 不可用。"""

    def fake(args: list[str]) -> subprocess.CompletedProcess[str] | None:
        key = tuple(args)
        if key not in table:
            return None
        code, out = table[key]
        return subprocess.CompletedProcess(args=list(args), returncode=code, stdout=out, stderr="")

    return fake


INSIDE = ("rev-parse", "--is-inside-work-tree")


def _config(value: str | None) -> tuple[int, str]:
    """未设置时 git config --get 返回退出码 1 且 stdout 为空（已对本机实测确认）。"""
    return (0, value) if value is not None else (1, "")


class TestHooksActivation:
    def test_passes_when_hooks_path_matches_the_installer(self, monkeypatch, capsys):
        monkeypatch.setattr(
            check_local,
            "_git_probe",
            _probe({INSIDE: (0, "true"), CONFIG_PROBE: (0, f"{check_local.EXPECTED_HOOKS_PATH}\n")}),
        )
        check_local.hooks_activation()  # 不得抛 SystemExit
        assert "OK" in capsys.readouterr().out

    def test_fails_and_shows_the_fix_when_unset(self, monkeypatch, capsys):
        monkeypatch.setattr(check_local, "_git_probe", _probe({INSIDE: (0, "true"), CONFIG_PROBE: _config(None)}))
        with pytest.raises(SystemExit) as exc:
            check_local.hooks_activation()
        assert exc.value.code == 1
        err = capsys.readouterr().out
        assert "FAIL" in err
        assert ".githooks/install.sh" in err, "必须给出可复制的修复动作，而不只说配置不对"

    def test_fails_when_hooks_path_points_somewhere_else(self, monkeypatch, capsys):
        monkeypatch.setattr(check_local, "_git_probe", _probe({INSIDE: (0, "true"), CONFIG_PROBE: (0, ".git-hooks\n")}))
        with pytest.raises(SystemExit) as exc:
            check_local.hooks_activation()
        assert exc.value.code == 1
        assert ".git-hooks" in capsys.readouterr().out, "报错要带上实际取到的值，便于定位是谁改的"

    def test_skips_outside_a_git_repo_instead_of_reporting_missing_hooks(self, monkeypatch, capsys):
        """误伤场景：仓库外跑体检时报 FAIL，会让人把「环境不对」当成「门禁缺失」并长期忽略这项。"""
        monkeypatch.setattr(check_local, "_git_probe", _probe({INSIDE: (128, "")}))
        check_local.hooks_activation()
        assert "跳过" in capsys.readouterr().out

    def test_skips_when_git_itself_is_unavailable(self, monkeypatch, capsys):
        monkeypatch.setattr(check_local, "_git_probe", _probe({}))
        check_local.hooks_activation()
        assert "跳过" in capsys.readouterr().out

    def test_expected_value_matches_what_the_installer_writes(self):
        """反漂移：期望值与 .githooks/install.sh 的字面量不一致时，这项会在新克隆上永远报红。"""
        installer = Path(check_local.__file__).resolve().parent.parent / ".githooks" / "install.sh"
        assert f"git config {HOOKS_OPT} {check_local.EXPECTED_HOOKS_PATH}" in installer.read_text(encoding="utf-8")
