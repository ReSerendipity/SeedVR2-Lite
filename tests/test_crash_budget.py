"""本地崩溃遥测与崩溃预算门禁测试（运维稳定性 P1#1 落地）。

覆盖：窗口/全量计数、损坏与异 schema 条目跳过、目录缺失、
check_crash_budget.py 的通过/超限退出码。全部走 tmp_path，不碰仓库 logs/。
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

from app.integrated_app.utils.crash_telemetry import (
    CRASH_SCHEMA,
    count_crashes,
    list_crash_records,
)

ROOT = Path(__file__).resolve().parent.parent


def _write_record(directory: Path, name: str, kind: str, ts_ms: int) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(
        json.dumps({"schema": CRASH_SCHEMA, "timestamp_ms": ts_ms, "kind": kind}, ensure_ascii=False),
        encoding="utf-8",
    )


class TestCrashTelemetry:
    """list/count 的窗口与容错口径。"""

    def test_counts_window_and_total(self, tmp_path):
        now_ms = int(time.time() * 1000)
        _write_record(tmp_path, "crash-1.json", "shell_panic", now_ms)
        _write_record(tmp_path, "crash-2.json", "backend_exit", now_ms - 1000)
        _write_record(tmp_path, "crash-3.json", "shell_panic", now_ms - 9 * 86400 * 1000)
        assert count_crashes(7, tmp_path) == 2
        assert count_crashes(None, tmp_path) == 3

    def test_skips_corrupted_and_wrong_schema(self, tmp_path):
        now_ms = int(time.time() * 1000)
        _write_record(tmp_path, "crash-ok.json", "shell_panic", now_ms)
        (tmp_path / "crash-bad.json").write_text("{not json", encoding="utf-8")
        (tmp_path / "crash-schema.json").write_text(
            json.dumps({"schema": "other/9", "timestamp_ms": now_ms}), encoding="utf-8"
        )
        assert count_crashes(None, tmp_path) == 1
        assert list_crash_records(tmp_path)[0]["kind"] == "shell_panic"

    def test_missing_dir_means_zero(self, tmp_path):
        assert count_crashes(7, tmp_path / "nope") == 0
        assert list_crash_records(tmp_path / "nope") == []


class TestCrashBudgetScript:
    """check_crash_budget.py 的退出码契约。"""

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "check_crash_budget.py"), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )

    def test_within_budget_passes(self, tmp_path):
        now_ms = int(time.time() * 1000)
        _write_record(tmp_path, "crash-a.json", "shell_panic", now_ms)
        proc = self._run("--crashes-dir", str(tmp_path), "--window-days", "7", "--max-crashes", "5")
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert '"ok": true' in proc.stdout

    def test_exceeding_budget_fails(self, tmp_path):
        now_ms = int(time.time() * 1000)
        for i in range(3):
            _write_record(tmp_path, f"crash-{i}.json", "shell_panic", now_ms - i)
        proc = self._run("--crashes-dir", str(tmp_path), "--window-days", "7", "--max-crashes", "2")
        assert proc.returncode == 1, proc.stdout + proc.stderr
        assert '"ok": false' in proc.stdout
