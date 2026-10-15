"""本地崩溃遥测汇总（形态包 X2 保留项 · 运维稳定性 P1#1）。

读取桌面壳写入的 ``logs/crashes/crash-*.json``（schema ``seedvr2-crash/1``），
供 ``/metrics``（routes/system/prometheus_metrics.py）暴露，与
``scripts/check_crash_budget.py`` 发布前核验。

隐私边界：只读崩溃记录的结构化字段（时间/类别），不读取 logs/ 其它内容，
无任何远端上报——与产品「数据不出机器」定位一致。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

# schema 版本与 Rust 侧 crash_report.rs 的 CRASH_SCHEMA 一致
CRASH_SCHEMA = "seedvr2-crash/1"

#: 崩溃预算默认统计窗口（天）
DEFAULT_WINDOW_DAYS = 7

# dev 布局：ROOT/app/integrated_app/utils/本文件 → parents[3]=ROOT；
# 打包布局：ROOT(app)/integrated_app/utils/本文件 → parents[3]=ROOT(app)，同深。
DEFAULT_CRASH_DIR = Path(__file__).resolve().parents[3] / "logs" / "crashes"


def list_crash_records(crash_dir: Path | None = None) -> list[dict]:
    """读取全部崩溃记录；目录缺失返回空表，损坏/异 schema 条目跳过。"""
    directory = Path(crash_dir) if crash_dir is not None else DEFAULT_CRASH_DIR
    if not directory.is_dir():
        return []
    records: list[dict] = []
    for path in sorted(directory.glob("crash-*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("schema") == CRASH_SCHEMA:
            records.append(data)
    return records


def count_crashes(window_days: int | None = None, crash_dir: Path | None = None) -> int:
    """统计窗口内崩溃条数；``window_days`` 为 None 统计全部历史。

    Args:
        window_days: 统计窗口（天）；None 表示不限窗口。
        crash_dir: 崩溃记录目录；None 用默认 ``logs/crashes``。

    Returns:
        窗口内（schema 匹配且 timestamp_ms 可解析）的崩溃记录条数。
    """
    records = list_crash_records(crash_dir)
    if window_days is None:
        return len(records)
    cutoff_ms = (time.time() - window_days * 86400) * 1000
    return sum(1 for r in records if isinstance(r.get("timestamp_ms"), (int, float)) and r["timestamp_ms"] >= cutoff_ms)
