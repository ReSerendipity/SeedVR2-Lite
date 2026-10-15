#!/usr/bin/env python3
"""崩溃预算门禁（形态包 X2 保留项 · 运维稳定性 P1#1）。

读取桌面壳崩溃记录（logs/crashes/，schema seedvr2-crash/1），统计窗口内崩溃
条数并对照阈值：超出即非零退出。

口径说明：崩溃数据只存在于用户/维护者本机（数据不出机器），CI 无法代跑本检查；
接线为发布前人工执行（docs/release-governance.md §3 步骤 6、docs/CODING_STANDARDS.md
§5.2 门禁表），阈值默认 7 天窗口 ≤ 5 次。

用法：
    python scripts/check_crash_budget.py                       # 默认 7 天窗口 / 上限 5
    python scripts/check_crash_budget.py --window-days 30 --max-crashes 10
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.integrated_app.utils.crash_telemetry import count_crashes  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    """统计窗口内崩溃并对照阈值；超限返回 1，达标返回 0。"""
    parser = argparse.ArgumentParser(description="崩溃预算检查（窗口计数 vs 上限）")
    parser.add_argument("--crashes-dir", default=None, help="崩溃记录目录（默认项目 logs/crashes）")
    parser.add_argument("--window-days", type=int, default=7, help="统计窗口（天），默认 7")
    parser.add_argument("--max-crashes", type=int, default=5, help="窗口内崩溃上限，默认 5")
    args = parser.parse_args(argv)

    crash_dir = Path(args.crashes_dir) if args.crashes_dir else None
    crashes = count_crashes(args.window_days, crash_dir)
    ok = crashes <= args.max_crashes
    print(
        json.dumps(
            {
                "crashes_dir": str(crash_dir if crash_dir is not None else ROOT / "logs" / "crashes"),
                "window_days": args.window_days,
                "max_crashes": args.max_crashes,
                "crashes": crashes,
                "ok": ok,
            },
            ensure_ascii=False,
        )
    )
    if not ok:
        print(f"[FAIL] 窗口内崩溃 {crashes} 次 > 上限 {args.max_crashes} 次，先复盘再发版")
        return 1
    print("[PASS] 崩溃预算达标")
    return 0


if __name__ == "__main__":
    sys.exit(main())
