#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""Agent CLI 契约测试：JSON 输出 / 退出码 / stdout 隔离 / restore 参数校验。

只测不依赖 GPU、模型权重与网络运行的路径，CI（CPU、无权重）可跑。
"""

from __future__ import annotations

import json

import pytest

from scripts.agent_cli import (
    EXIT_MISSING_RESOURCE,
    EXIT_OK,
    CLIResult,
    _begin_json_mode,
    _emit,
    cmd_restore,
    main,
)


class TestResultContract:
    def test_success_json(self, capsys):
        _emit(CLIResult(True, EXIT_OK, "t", "ok", {"a": 1}), True)
        assert json.loads(capsys.readouterr().out)["data"]["a"] == 1

    def test_stdout_isolation(self, capsys):
        """回归：第三方 print 不得污染 JSON stdout。"""
        _begin_json_mode()
        try:
            print("[third-party] should not pollute stdout")
            _emit(CLIResult(True, EXIT_OK, "t", "ok", {"x": 2}), True)
        finally:
            import sys

            from scripts import agent_cli

            sys.stdout = agent_cli._ORIGINAL_STDOUT or sys.stdout
            agent_cli._ORIGINAL_STDOUT = None
        captured = capsys.readouterr()
        assert "[third-party]" not in captured.out
        assert json.loads(captured.out.strip())["data"]["x"] == 2
        assert "[third-party]" in captured.err


class TestCommands:
    def test_status_json(self, capsys):
        rc = main(["status", "--json"])
        out = capsys.readouterr().out
        assert rc == EXIT_OK
        assert json.loads(out)["success"] is True

    def test_json_flag_anywhere(self, capsys):
        rc = main(["--json", "status"])
        assert rc == EXIT_OK
        assert json.loads(capsys.readouterr().out)["command"] == "status"

    def test_restore_missing_input(self, tmp_path):
        import argparse

        args = argparse.Namespace(
            input=str(tmp_path / "missing.png"),
            output_dir=str(tmp_path / "out"),
            server_url="http://127.0.0.1:1",
            model_size="3b",
            seed=1,
            output_format="png",
            timeout=1,
        )
        result = cmd_restore(args)
        assert result.success is False
        assert result.exit_code == EXIT_MISSING_RESOURCE

    def test_unknown_command_exits_nonzero(self):
        with pytest.raises(SystemExit) as exc:
            main(["no-such-command"])
        assert exc.value.code != 0
